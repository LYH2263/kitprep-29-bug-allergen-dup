from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import Ingredient, PrepLedgerEntry
router = APIRouter(prefix="/inventory", tags=["inventory"])

class AllergenPatch(BaseModel):
    is_allergen: bool

def _occupied_map(db: Session) -> dict[int, float]:
    """占用列：各原料在已落库台账（主贴+专册）中的占用合计。占用不动结存。"""
    rows = db.execute(
        select(PrepLedgerEntry.ingredient_id, func.coalesce(func.sum(PrepLedgerEntry.occupied_qty), 0.0))
        .group_by(PrepLedgerEntry.ingredient_id)
    ).all()
    return {iid: float(qty) for iid, qty in rows}

@router.get("")
def list_inventory(db: Session = Depends(get_db)):
    occupied = _occupied_map(db)
    return [{"id": r.id, "code": r.code, "name": r.name, "unit": r.unit,
             "stock_qty": r.stock_qty, "is_allergen": r.is_allergen,
             "occupied_qty": round(occupied.get(r.id, 0.0), 3)}
            for r in db.scalars(select(Ingredient).order_by(Ingredient.id)).all()]

@router.patch("/{ingredient_id}")
def patch_allergen(ingredient_id: int, body: AllergenPatch, db: Session = Depends(get_db)):
    """库存页保存含敏标记。改标记只影响之后新生成的单；已落库的旧单禁止改字。"""
    ing = db.get(Ingredient, ingredient_id)
    if not ing:
        raise HTTPException(404, "原料不存在")
    ing.is_allergen = body.is_allergen
    # 已落下的旧单（PrepRun.result_json 与 prep_ledger_entries）按当时标记钉死，
    # 绝不回写改字；新标记只影响之后新生成的单。
    db.commit()
    return {"id": ing.id, "code": ing.code, "name": ing.name, "unit": ing.unit,
            "stock_qty": ing.stock_qty, "is_allergen": ing.is_allergen}
