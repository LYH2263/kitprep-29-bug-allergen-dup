import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
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
