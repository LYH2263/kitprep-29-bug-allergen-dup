from sqlalchemy import inspect, select, text
from sqlalchemy.orm import Session

from app.database import Base, engine
from app.models.models import (
    BomLine, Dish, Ingredient, KitchenOrder, OrderLine,
    OrderPrepCommitment, PrepLedgerEntry, PrepRun,
)

def _dedupe_legacy_double_entry(conn) -> list[int]:
    """旧版本含敏行会双落主贴+专册。唯一索引建不上去，先按
    “账册归属与行内 is_allergen 快照自洽”优先保留专册那行，删除重复。
    返回被清理过的 prep_run_id，供重建旧单 JSON 快照。"""
    affected = [r[0] for r in conn.execute(text("""
        SELECT prep_run_id FROM prep_ledger_entries
        GROUP BY prep_run_id, ingredient_id
        HAVING COUNT(*) > 1
    """)).all()]
    if not affected:
        return []
    conn.execute(text("""
        DELETE FROM prep_ledger_entries
        WHERE id NOT IN (
            SELECT keep.id FROM prep_ledger_entries keep
            WHERE keep.id = (
                SELECT e.id FROM prep_ledger_entries e
                WHERE e.prep_run_id = keep.prep_run_id
                  AND e.ingredient_id = keep.ingredient_id
                ORDER BY CASE WHEN (e.book = 'allergen') = e.is_allergen
                              THEN 0 ELSE 1 END,
                         e.id
                LIMIT 1
            )
        )
    """))
    return affected

def _rebuild_run_snapshots(db: Session, run_ids: list[int]) -> None:
    """去重后按台账剩行重建受影响旧单的 result_json 分册部分：
    need/stock/shortage 等每个字保持落库时值，仅把错落在另一本的重复行抹掉。"""
    import json
    from app.services.bom_engine import (
        ALLERGEN_BOOK, MAIN_BOOK, NeedLine, result_to_dict,
    )
    for run_id in run_ids:
        run = db.get(PrepRun, run_id)
        if run is None:
            continue
        rows = db.scalars(
            select(PrepLedgerEntry)
            .where(PrepLedgerEntry.prep_run_id == run_id)
            .order_by(PrepLedgerEntry.ingredient_id)
        ).all()
        def _need(r: PrepLedgerEntry) -> NeedLine:
            return NeedLine(
                ingredient_id=r.ingredient_id, ingredient_code=r.ingredient_code,
                ingredient_name=r.ingredient_name, unit=r.unit,
                need_qty=r.need_qty, stock_qty=r.stock_qty, shortage=r.shortage,
                is_allergen=bool(r.is_allergen),
            )
        main = [_need(r) for r in rows if r.book == MAIN_BOOK]
        allergen = [_need(r) for r in rows if r.book == ALLERGEN_BOOK]
        data = json.loads(run.result_json or "{}")
        data.update(result_to_dict(main + allergen,
                                   {MAIN_BOOK: main, ALLERGEN_BOOK: allergen}))
        data["generated"] = True
        run.result_json = json.dumps(data, ensure_ascii=False)
    db.commit()

def ensure_schema() -> None:
    """建表 + 存量库补列/补约束（create_all 不会改旧表）。"""
    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    if "ingredients" not in tables:
        return
    columns = {c["name"] for c in inspector.get_columns("ingredients")}
    if "is_allergen" not in columns:
        # Postgres 的 boolean 不吃整数 0，按方言给 FALSE / 0
        default_lit = "FALSE" if engine.dialect.name == "postgresql" else "0"
        with engine.begin() as conn:
            conn.execute(text(
                f"ALTER TABLE ingredients ADD COLUMN is_allergen BOOLEAN NOT NULL DEFAULT {default_lit}"
            ))

    if "prep_ledger_entries" in tables:
        # 旧双落数据先去重，再上库级铁规：同一 run 同一原料只许一行
        with engine.begin() as conn:
            affected = _dedupe_legacy_double_entry(conn)
            conn.execute(text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_ledger_run_ingredient "
                "ON prep_ledger_entries (prep_run_id, ingredient_id)"
            ))
        if affected:
            from app.database import SessionLocal
            db = SessionLocal()
            try:
                _rebuild_run_snapshots(db, affected)
            finally:
                db.close()

def seed_if_empty(db: Session) -> None:
    if (db.scalar(text("SELECT count(*) FROM dishes")) or 0) > 0:
        return
    dishes = [("D-HS", "红烧肉套餐"), ("D-YC", "鱼香茄子"), ("D-JT", "鸡汤面")]
    dish_ids = {}
    for code, name in dishes:
        d = Dish(code=code, name=name, portion_unit="份")
        db.add(d); db.flush(); dish_ids[code] = d.id
    # (code, name, unit, stock, is_allergen)：生抽、食用油打含敏标记作演示
    ings = [
        ("I-PR", "五花肉", "kg", 8.0, False),
        ("I-EG", "茄子", "kg", 3.0, False),
        ("I-CK", "鸡肉", "kg", 5.0, True),
        ("I-RC", "大米", "kg", 20.0, False),
        ("I-ND", "面条", "kg", 4.0, False),
        ("I-SC", "生抽", "L", 2.0, True),
        ("I-OL", "食用油", "L", 1.5, True),
    ]
    ing_ids = {}
    for code, name, unit, stock, is_allergen in ings:
        i = Ingredient(code=code, name=name, unit=unit, stock_qty=stock, is_allergen=is_allergen)
        db.add(i); db.flush(); ing_ids[code] = i.id
    bom = [
        ("D-HS", "I-PR", 0.25), ("D-HS", "I-RC", 0.15), ("D-HS", "I-SC", 0.02), ("D-HS", "I-OL", 0.03),
        ("D-YC", "I-EG", 0.3), ("D-YC", "I-RC", 0.15), ("D-YC", "I-SC", 0.015), ("D-YC", "I-OL", 0.025),
        ("D-JT", "I-CK", 0.12), ("D-JT", "I-ND", 0.2), ("D-JT", "I-SC", 0.01),
    ]
    for dcode, icode, qty in bom:
        db.add(BomLine(dish_id=dish_ids[dcode], ingredient_id=ing_ids[icode], qty_per_portion=qty))
    order = KitchenOrder(code="KO-0901", outlet="城西门店", status="open")
    db.add(order); db.flush()
    for dcode, portions in [("D-HS", 40), ("D-YC", 30), ("D-JT", 50)]:
        db.add(OrderLine(order_id=order.id, dish_id=dish_ids[dcode], portions=portions))
    db.commit()
