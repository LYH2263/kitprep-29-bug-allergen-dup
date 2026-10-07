import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base, get_db
from app.main import app
from app.models.models import (
    BomLine, Dish, Ingredient, KitchenOrder, OrderLine,
    OrderPrepCommitment, PrepLedgerEntry,
)


@pytest.fixture()
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine, autoflush=False)
    db = SessionLocal()
    d = Dish(code="D1", name="菜一"); db.add(d); db.flush()
    i1 = Ingredient(code="I1", name="肉", unit="kg", stock_qty=1.0, is_allergen=False)
    i2 = Ingredient(code="I2", name="油", unit="L", stock_qty=9.0, is_allergen=False)
    db.add_all([i1, i2]); db.flush()
    db.add_all([
        BomLine(dish_id=d.id, ingredient_id=i1.id, qty_per_portion=0.2),
        BomLine(dish_id=d.id, ingredient_id=i2.id, qty_per_portion=0.1),
    ])
    o = KitchenOrder(code="KO1", outlet="门店", status="open"); db.add(o); db.flush()
    db.add(OrderLine(order_id=o.id, dish_id=d.id, portions=10))
    db.commit()
    db.close()

    def override_get_db():
        s = SessionLocal()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()
    engine.dispose()


def test_inventory_persists_allergen_flag_and_shows_occupied(client):
    rows = client.get("/api/inventory").json()
    oil = next(r for r in rows if r["code"] == "I2")
    assert oil["is_allergen"] is False and oil["occupied_qty"] == 0.0

    res = client.patch(f"/api/inventory/{oil['id']}", json={"is_allergen": True})
    assert res.status_code == 200 and res.json()["is_allergen"] is True
    assert client.get("/api/inventory").json()[1]["is_allergen"] is True


def test_run_splits_books_keeps_stock_and_is_idempotent(client):
    stock_before = {r["code"]: r["stock_qty"] for r in client.get("/api/inventory").json()}
    # 油打含敏
    rows = client.get("/api/inventory").json()
    oil_id = next(r for r in rows if r["code"] == "I2")["id"]
    client.patch(f"/api/inventory/{oil_id}", json={"is_allergen": True})

    r1 = client.post("/api/prep/run?order_id=1")
    assert r1.status_code == 200, r1.text
    first = r1.json()
    assert [l["ingredient_code"] for l in first["prep_lines"]] == ["I1"]
    assert [l["ingredient_code"] for l in first["allergen_lines"]] == ["I2"]
    # 含敏油（需1.0 存9.0）不进主缺料贴；主贴肉需2.0存1.0缺1.0
    assert {l["ingredient_code"] for l in first["shortages"]} == {"I1"}

    # 结存保持生成前；占用列出现且只记账
    inv = {r["code"]: r for r in client.get("/api/inventory").json()}
    assert {c: r["stock_qty"] for c, r in inv.items()} == stock_before
    assert inv["I1"]["occupied_qty"] == 2.0 and inv["I2"]["occupied_qty"] == 1.0

    # 抢点/再点：同一套账，id 不变、immutable
    r2 = client.post("/api/prep/run?order_id=1")
    assert r2.status_code == 200
    second = r2.json()
    assert second["id"] == first["id"] and second["immutable"] is True
    assert [l["ingredient_code"] for l in second["allergen_lines"]] == ["I2"]


def test_change_marker_after_commit_does_not_rewrite_old_run(client):
    first = client.post("/api/prep/run?order_id=1").json()
    assert first["allergen_lines"] == []  # 全普通
    rows = client.get("/api/inventory").json()
    oil_id = next(r for r in rows if r["code"] == "I2")["id"]
    client.patch(f"/api/inventory/{oil_id}", json={"is_allergen": True})
    # 旧单不变；但新试算(latest 只读)按新标记拆，提示未落库的那一套长什么样
    again = client.post("/api/prep/run?order_id=1").json()
    assert again["id"] == first["id"] and again["allergen_lines"] == []


def test_latest_before_generation_is_preview_and_shortages_empty(client):
    latest = client.get("/api/prep/latest?order_id=1").json()
    assert latest["generated"] is False and latest["id"] is None
    sh = client.get("/api/prep/shortages?order_id=1").json()
    assert sh["generated"] is False and sh["shortages"] == []


def test_split_conflict_returns_409_not_shortage_and_persists_nothing(client, monkeypatch):
    from app.services import prep_service
    from app.services.bom_engine import LedgerSplitError

    def boom(*a, **k):
        raise LedgerSplitError("模拟拆法对不上")
    monkeypatch.setattr(prep_service, "_verify_persisted", boom)

    res = client.post("/api/prep/run?order_id=1")
    assert res.status_code == 409
    assert "退回" in res.json()["detail"] and "结存" not in res.json()["detail"]

    db = next(app.dependency_overrides[get_db]())
    assert db.query(PrepLedgerEntry).count() == 0
    assert db.query(OrderPrepCommitment).count() == 0
    db.close()
