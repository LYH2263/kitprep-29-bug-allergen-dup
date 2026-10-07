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
    from app.models.models import PrepRun
    import json as _json
    for run in db.scalars(select(PrepRun)).all():
        data = _json.loads(run.result_json)
        for line in data.get("prep_lines", []):
            if line.get("ingredient_id") == ingredient_id:
                line["is_allergen"] = body.is_allergen
        for line in data.get("allergen_lines", []) or []:
            if line.get("ingredient_id") == ingredient_id:
                line["is_allergen"] = body.is_allergen
        run.result_json = _json.dumps(data, ensure_ascii=False)
    db.commit()
    return {"id": ing.id, "code": ing.code, "name": ing.name, "unit": ing.unit,
            "stock_qty": ing.stock_qty, "is_allergen": ing.is_allergen}
