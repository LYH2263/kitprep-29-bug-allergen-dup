import pytest
import threading
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, apply_sqlite_write_serialization
from app.models.models import (
    BomLine, Dish, Ingredient, KitchenOrder, OrderLine,
    OrderPrepCommitment, PrepLedgerEntry, PrepRun,
)
from app.services import prep_service
from app.services.bom_engine import LedgerSplitError


@pytest.fixture()
def Session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()
    d = Dish(code="D1", name="菜一"); db.add(d); db.flush()
    i1 = Ingredient(code="I1", name="肉", unit="kg", stock_qty=10.0, is_allergen=False)
    i2 = Ingredient(code="I2", name="油", unit="L", stock_qty=100.0, is_allergen=False)
    db.add_all([i1, i2]); db.flush()
    db.add_all([
        BomLine(dish_id=d.id, ingredient_id=i1.id, qty_per_portion=0.2),
        BomLine(dish_id=d.id, ingredient_id=i2.id, qty_per_portion=0.1),
    ])
    o = KitchenOrder(code="KO1", outlet="门店", status="open"); db.add(o); db.flush()
    db.add(OrderLine(order_id=o.id, dish_id=d.id, portions=10))
    db.commit()
    yield SessionLocal
    db.close()
    engine.dispose()


def _count(db, model):
    return db.scalar(select(func.count()).select_from(model))


def test_generate_commits_both_books_and_occupied_but_never_stock(Session):
    db = Session()
    before = {i.code: i.stock_qty for i in db.scalars(select(Ingredient)).all()}
    res = prep_service.generate_prep(db, 1)
    assert res["generated"] is True and res["immutable"] is False
    # 两本账 + 占用列落库（无含敏：专册为空，主贴 2 行）
    rows = db.scalars(select(PrepLedgerEntry)).all()
    assert {r.book for r in rows} == {"main"}
    assert len(rows) == 2
    assert all(r.occupied_qty == r.need_qty for r in rows)
    assert _count(db, OrderPrepCommitment) == 1
    # 结存数字保持生成前，禁止当扣账
    after = {i.code: i.stock_qty for i in db.scalars(select(Ingredient)).all()}
    assert after == before
    db.close()


def test_allergen_goes_only_to_register_and_main_shortage_excludes_it(Session):
    db = Session()
    oil = db.scalar(select(Ingredient).where(Ingredient.code == "I2"))
    oil.is_allergen = True  # 油需求 1.0；打含敏
    meat = db.scalar(select(Ingredient).where(Ingredient.code == "I1"))
    meat.stock_qty = 1.0   # 肉需 2.0 → 主贴缺 1.0
    db.commit()

    res = prep_service.generate_prep(db, 1)
    assert [l["ingredient_code"] for l in res["prep_lines"]] == ["I1"]
    assert [l["ingredient_code"] for l in res["allergen_lines"]] == ["I2"]
    assert {l["ingredient_id"] for l in res["shortages"]} == {meat.id}

    main = db.scalars(select(PrepLedgerEntry).where(PrepLedgerEntry.book == "main")).all()
    reg = db.scalars(select(PrepLedgerEntry).where(PrepLedgerEntry.book == "allergen")).all()
    assert {r.ingredient_id for r in main} == {meat.id}
    assert {r.ingredient_id for r in reg} == {oil.id}
    assert all(r.occupied_qty == r.need_qty for r in main + reg)
    db.close()


def test_regenerate_returns_same_ledger_and_ignores_new_marker(Session):
    db = Session()
    first = prep_service.generate_prep(db, 1)
    first_id = first["id"]
    # 改标记：油改成含敏，再生成 —— 必须还是旧账，禁止改字
    db.scalar(select(Ingredient).where(Ingredient.code == "I2")).is_allergen = True
    db.commit()
    again = prep_service.generate_prep(db, 1)
    assert again["id"] == first_id
    assert again["immutable"] is True
    assert again["allergen_lines"] == []  # 旧单按旧标记，全部在主贴
    assert _count(db, PrepRun) == 1 and _count(db, OrderPrepCommitment) == 1
    db.close()


def test_preview_does_not_persist(Session):
    db = Session()
    preview = prep_service.preview_prep(db, 1)
    assert preview["generated"] is False and preview["id"] is None
    assert _count(db, PrepRun) == 0 and _count(db, PrepLedgerEntry) == 0
    db.close()


def test_split_failure_rolls_back_everything(Session, monkeypatch):
    db = Session()
    def boom(*a, **k):
        raise LedgerSplitError("模拟主贴/专册拆法对不上")
    monkeypatch.setattr(prep_service, "_verify_persisted", boom)
    with pytest.raises(LedgerSplitError):
        prep_service.generate_prep(db, 1)
    # 专册、主贴、占用列、承诺、运行记录全部退回
    assert _count(db, PrepLedgerEntry) == 0
    assert _count(db, PrepRun) == 0
    assert _count(db, OrderPrepCommitment) == 0
    # 结存也没被动
    assert db.scalar(select(Ingredient).where(Ingredient.code == "I1")).stock_qty == 10.0
    db.close()


def test_second_session_race_gets_same_ledger_set(Session):
    # 两个会话抢点：先落一套，后到者必须拿到同一套，禁止第二套账
    db1 = Session()
    first = prep_service.generate_prep(db1, 1)
    db1.commit()
    db2 = Session()
    second = prep_service.generate_prep(db2, 1)
    assert second["id"] == first["id"] and second["immutable"] is True
    db1.close(); db2.close()


def test_get_committed_is_none_before_generation(Session):
    db = Session()
    assert prep_service.get_committed(db, 1) is None
    db.close()


def _seed_file_engine(eng):
    """在文件库（多连接）上铺一份与 Session fixture 同构的基础数据。"""
    from sqlalchemy import text
    SL = sessionmaker(bind=eng, autoflush=False)
    db = SL()
    db.add(Dish(code="D1", name="菜一")); db.flush()
    db.add_all([
        Ingredient(code="I1", name="肉", unit="kg", stock_qty=10.0, is_allergen=False),
        Ingredient(code="I2", name="油", unit="L", stock_qty=100.0, is_allergen=False),
    ]); db.flush()
    db.add_all([
        BomLine(dish_id=1, ingredient_id=1, qty_per_portion=0.2),
        BomLine(dish_id=1, ingredient_id=2, qty_per_portion=0.1),
    ])
    db.add(KitchenOrder(code="KO1", outlet="门店", status="open")); db.flush()
    db.add(OrderLine(order_id=1, dish_id=1, portions=10))
    db.commit(); db.close()
    return SL


def test_two_entrypoints_clicking_together_commit_one_ledger_set(tmp_path):
    # 真·双连接抢点：文件库 + 两条连接 + 两个线程同时生成（内存 StaticPool 只有一条连接，复现不了）
    db_file = tmp_path / "race.db"
    eng = create_engine(
        f"sqlite:///{db_file}",
        connect_args={"check_same_thread": False, "timeout": 15},
    )
    apply_sqlite_write_serialization(eng)
    Base.metadata.create_all(eng)
    SL = _seed_file_engine(eng)

    results: dict[str, object] = {}
    errors: dict[str, object] = {}
    barrier = threading.Barrier(2)

    def worker(side: str):
        db = SL()
        try:
            barrier.wait()
            results[side] = prep_service.generate_prep(db, order_id=1)
        except Exception as e:  # 抢点双方谁都不许报错
            errors[side] = e
        finally:
            db.close()

    t1 = threading.Thread(target=worker, args=("orders_page",))
    t2 = threading.Thread(target=worker, args=("prep_desk",))
    t1.start(); t2.start(); t1.join(30); t2.join(30)

    assert not errors, errors
    assert results["orders_page"]["id"] == results["prep_desk"]["id"]  # 同一套两本账
    check = SL()
    assert _count(check, PrepRun) == 1
    assert _count(check, OrderPrepCommitment) == 1
    rows = check.scalars(select(PrepLedgerEntry)).all()
    assert len(rows) == 2                       # 没有任何一行被两本都记
    assert {r.book for r in rows} == {"main"}  # 全普通：专册为空，全走主贴
    assert {r.ingredient_id for r in rows} == {1, 2}
    check.close(); eng.dispose()


def test_legacy_double_entry_db_is_deduped_when_schema_ensured(tmp_path, monkeypatch):
    # 存量库：旧版本已把含敏行双落（主贴+专册各一行），启动 ensure_schema 必须：
    # 去重 → 上库级唯一索引 → 旧单快照按剩行重建（数字钉死，只抹重复行）
    from sqlalchemy import text
    from app.services import seed
    db_file = tmp_path / "legacy.db"
    eng = create_engine(f"sqlite:///{db_file}")
    with eng.begin() as c:
        # 手工建旧 schema：只有 (run,book,ingredient) 唯一约束，没有 (run,ingredient)
        c.execute(text(
            "CREATE TABLE ingredients (id INTEGER PRIMARY KEY, code TEXT, name TEXT, "
            "unit TEXT, stock_qty FLOAT, is_allergen BOOLEAN NOT NULL DEFAULT 0)"))
        c.execute(text(
            "CREATE TABLE prep_runs (id INTEGER PRIMARY KEY, order_id INTEGER, "
            "created_at DATETIME, status TEXT, result_json TEXT)"))
        c.execute(text(
            "CREATE TABLE prep_ledger_entries ("
            "id INTEGER PRIMARY KEY, prep_run_id INTEGER, order_id INTEGER, book TEXT, "
            "ingredient_id INTEGER, ingredient_code TEXT, ingredient_name TEXT, unit TEXT, "
            "need_qty FLOAT, stock_qty FLOAT, shortage FLOAT, occupied_qty FLOAT, "
            "is_allergen BOOLEAN NOT NULL, UNIQUE(prep_run_id, book, ingredient_id))"))
        c.execute(text(
            "INSERT INTO ingredients VALUES (1,'I1','肉','kg',10.0,0)"))
        c.execute(text(
            "INSERT INTO prep_runs VALUES (1,1,'2026-10-01 00:00:00','committed','{}')"))
        # 旧 bug：油(2) 双落两本
        c.execute(text(
            "INSERT INTO prep_ledger_entries VALUES "
            "(1,1,1,'main',1,'I1','肉','kg',2.0,1.0,1.0,2.0,0),"
            "(2,1,1,'main',2,'I2','油','L',1.0,9.0,0.0,1.0,1),"
            "(3,1,1,'allergen',2,'I2','油','L',1.0,9.0,0.0,1.0,1)"))

    monkeypatch.setattr(seed, "engine", eng)
    monkeypatch.setattr("app.database.SessionLocal", sessionmaker(bind=eng, autoflush=False))
    seed.ensure_schema()

    with eng.begin() as c:
        rows = c.execute(text(
            "SELECT book, ingredient_id FROM prep_ledger_entries ORDER BY id")).all()
        assert rows == [("main", 1), ("allergen", 2)]  # 含敏行只留专册一侧
        idx = c.execute(text(
            "SELECT COUNT(*) FROM sqlite_master WHERE type='index' "
            "AND name='uq_ledger_run_ingredient'")).scalar()
        assert idx == 1
        import json
        data = json.loads(c.execute(text(
            "SELECT result_json FROM prep_runs WHERE id=1")).scalar())
    assert [l["ingredient_code"] for l in data["prep_lines"]] == ["I1"]
    assert [l["ingredient_code"] for l in data["allergen_lines"]] == ["I2"]
    # 数字钉死：留下的行仍是落库时的量
    oil = data["allergen_lines"][0]
    assert (oil["need_qty"], oil["stock_qty"], oil["shortage"]) == (1.0, 9.0, 0.0)
    # 唯一索引真的拦得住再次双落
    from sqlalchemy.exc import IntegrityError
    with pytest.raises(IntegrityError):
        with eng.begin() as c:
            c.execute(text(
                "INSERT INTO prep_ledger_entries "
                "(prep_run_id, order_id, book, ingredient_id, ingredient_code, ingredient_name, "
                "unit, need_qty, stock_qty, shortage, occupied_qty, is_allergen) "
                "VALUES (1,1,'main',2,'I2','油','L',1.0,9.0,0.0,1.0,1)"))
    eng.dispose()
