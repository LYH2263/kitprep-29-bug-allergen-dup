"""备料单生成：按含敏标记拆成主贴/专册两本账，同事务原子落库。

硬规矩：
- 两本账（主贴 main、敏料专册 allergen）+ 占用列 + 订单承诺，同一事务一起过或一起退；
- 占用列只记账（occupied_qty = need_qty），绝不扣 ingredients.stock_qty（结存保持生成前）；
- 每个订单只许一套已落库账：重复/抢点生成返回旧账，已落旧单不随标记改动改字；
- 提交前独立重查落库行，按当前含敏标记重核对拆法，对不上整次回退。
"""
from __future__ import annotations
import json
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.models import (
    BomLine, Ingredient, KitchenOrder, OrderLine,
    OrderPrepCommitment, PrepLedgerEntry, PrepRun,
)
from app.services.bom_engine import (
    ALLERGEN_BOOK, MAIN_BOOK, LedgerSplitError,
    explode_and_merge, result_to_dict, split_books, validate_books,
)

class PrepNotFound(LookupError):
    pass

def _load_inputs(db: Session, order_id: int):
    order = db.get(KitchenOrder, order_id)
    if not order:
        raise PrepNotFound("订单不存在")
    ols = [{"dish_id": l.dish_id, "portions": l.portions}
           for l in db.scalars(select(OrderLine).where(OrderLine.order_id == order_id)).all()]
    bom = [{"dish_id": b.dish_id, "ingredient_id": b.ingredient_id, "qty_per_portion": b.qty_per_portion}
           for b in db.scalars(select(BomLine)).all()]
    ings = {i.id: {"code": i.code, "name": i.name, "unit": i.unit,
                   "stock_qty": i.stock_qty, "is_allergen": i.is_allergen}
            for i in db.scalars(select(Ingredient)).all()}
    return order, ols, bom, ings

def _serialize(run: PrepRun) -> dict:
    data = json.loads(run.result_json)
    return {"id": run.id, "committed": run.status == "committed", **data}

def get_committed(db: Session, order_id: int) -> dict | None:
    """取该订单已落库的同一套账；没落过返回 None（只读，绝不触发生成）。"""
    commitment = db.scalar(
        select(OrderPrepCommitment).where(OrderPrepCommitment.order_id == order_id)
    )
    if not commitment:
        return None
    run = db.get(PrepRun, commitment.prep_run_id)
    if not run:
        return None
    data = _serialize(run)
    data["generated"] = True
    data["immutable"] = True
    return data

def preview_prep(db: Session, order_id: int) -> dict:
    """只读试算：按当前含敏标记拆两本给页面看，不写任何账、不占库存。"""
    order, ols, bom, ings = _load_inputs(db, order_id)
    lines = explode_and_merge(ols, bom, ings)
    result = result_to_dict(lines, split_books(lines))
    result["order"] = {"id": order.id, "code": order.code, "outlet": order.outlet}
    result["generated"] = False
    result["immutable"] = False
    result["id"] = None
    return result

def _verify_persisted(db: Session, run_id: int, expected_ids: set[int], ingredients: dict[int, dict]) -> None:
    """提交前独立重查：落库的主贴/专册/占用列必须与含敏标记拆法完全对得上。
    不依赖调用方传入的拆账结果，防止一本落漏、两本都记或含敏行混进主贴。"""
    rows = db.scalars(
        select(PrepLedgerEntry).where(PrepLedgerEntry.prep_run_id == run_id)
    ).all()
    seen: dict[int, PrepLedgerEntry] = {}
    for r in rows:
        if r.ingredient_id in seen:
            raise LedgerSplitError(f"原料 {r.ingredient_code} 在台账中重复落账")
        if r.book not in (MAIN_BOOK, ALLERGEN_BOOK):
            raise LedgerSplitError(f"原料 {r.ingredient_code} 落进了未知账册 {r.book}")
        seen[r.ingredient_id] = r
        flag_allergen = bool(ingredients[r.ingredient_id]["is_allergen"])
        if r.book == MAIN_BOOK and flag_allergen:
            raise LedgerSplitError(f"含敏原料 {r.ingredient_code} 的行混进了主贴")
        if r.book == ALLERGEN_BOOK and not flag_allergen:
            raise LedgerSplitError(f"非含敏原料 {r.ingredient_code} 混进了敏料专册")
        if r.is_allergen != flag_allergen:
            raise LedgerSplitError(f"原料 {r.ingredient_code} 含敏标记与专册归属不一致")
        # 占用列必须随两本账一起落下，且只记账不扣结存
        if r.occupied_qty != r.need_qty:
            raise LedgerSplitError(f"原料 {r.ingredient_code} 占用列与需料量对不上")
    # 完备性：每个需料行恰好落一本，禁止一本落下另一本空着/漏行，也禁止多出账外行
    missing = expected_ids - set(seen)
    extra = set(seen) - expected_ids
    if missing:
        raise LedgerSplitError(f"需料行未落账（两本必须一起落）: {sorted(missing)}")
    if extra:
        raise LedgerSplitError(f"台账出现本单之外的行: {sorted(extra)}")

def generate_prep(db: Session, order_id: int) -> dict:
    # 锁订单行：订单页与备料台抢点同单生成时在此排队，只许有一套两本账
    order = db.scalar(
        select(KitchenOrder).where(KitchenOrder.id == order_id).with_for_update()
    )
    if not order:
        raise PrepNotFound("订单不存在")

    existing = get_committed(db, order_id)
    if existing is not None:
        # 已按旧标记落下的单禁止改字：原样返回旧账（immutable: True）
        existing["immutable"] = True
        return existing

    _, ols, bom, ings = _load_inputs(db, order_id)
    stock_before = {iid: float(v["stock_qty"]) for iid, v in ings.items()}

    lines = explode_and_merge(ols, bom, ings)
    books = split_books(lines)
    validate_books(lines, books)  # 两本都记/漏记/错记在这里即整次失败
    result = result_to_dict(lines, books)
    result["order"] = {"id": order.id, "code": order.code, "outlet": order.outlet}
    result["generated"] = True
    result["immutable"] = False

    try:
        run = PrepRun(order_id=order_id, status="committed",
                      created_at=datetime.utcnow(),
                      result_json=json.dumps(result, ensure_ascii=False))
        db.add(run)
        db.flush()

        for book, book_lines in ((MAIN_BOOK, books[MAIN_BOOK]),
                                 (ALLERGEN_BOOK, books[ALLERGEN_BOOK])):
            for l in book_lines:
                db.add(PrepLedgerEntry(
                    prep_run_id=run.id, order_id=order_id, book=book,
                    ingredient_id=l.ingredient_id, ingredient_code=l.ingredient_code,
                    ingredient_name=l.ingredient_name, unit=l.unit,
                    need_qty=l.need_qty, stock_qty=l.stock_qty, shortage=l.shortage,
                    occupied_qty=l.need_qty, is_allergen=l.is_allergen,
                ))
        # 两本账作为一个整体处理（专册无含敏时允许为空列表，但事务不允许只落一半）
        db.flush()
        run_id = run.id
        _verify_persisted(db, run_id, {l.ingredient_id for l in lines}, ings)

        # 结存保护：生成前后 stock_qty 必须逐字一致，禁止当扣账
        db.flush()
        for iid, before in stock_before.items():
            after = db.get(Ingredient, iid).stock_qty
            if float(after) != before:
                raise LedgerSplitError(f"原料 {iid} 结存在生成中被改动，禁止占用即扣账")

        db.add(OrderPrepCommitment(order_id=order_id, prep_run_id=run.id,
                                   created_at=datetime.utcnow()))
        db.commit()
    except Exception:
        # 专册、主贴、占用列、承诺全部退回；
        # 若因抢点（行锁在 SQLite 等方言无效，靠唯一承诺约束兜底）发现对方已落库，
        # 则返回对方那一套账，保证两边只许同一套两本账。
        db.rollback()
        existing = get_committed(db, order_id)
        if existing is not None:
            return existing
        raise

    return _serialize(db.get(PrepRun, run.id)) | {"generated": True, "immutable": False}
