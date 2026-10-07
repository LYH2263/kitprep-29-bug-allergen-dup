from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.database import get_db
from app.models.models import KitchenOrder
from app.services.bom_engine import LedgerSplitError
from app.services import prep_service
router = APIRouter(prefix="/prep", tags=["prep"])

@router.post("/run")
def run_prep(order_id: int = 1, db: Session = Depends(get_db)):
    """点「生成备料单」：按当前含敏标记拆主贴/专册，两本账同事务落库。
    已落过库的订单原样返回旧账（禁止改字）。拆法对不上整次失败、全部退回。"""
    if not db.get(KitchenOrder, order_id):
        raise HTTPException(404, "订单不存在")
    try:
        return prep_service.generate_prep(db, order_id)
    except prep_service.PrepNotFound:
        raise HTTPException(404, "订单不存在")
    except LedgerSplitError as e:
        # 注意：这是拆账失败，不是结存不够
        raise HTTPException(409, f"两本账拆法对不上，整次失败，主贴/专册/占用列已全部退回：{e}")

@router.get("/latest")
def latest(order_id: int = 1, db: Session = Depends(get_db)):
    """只读。已落库返回那一套两本账（不可改）；没落过返回按当前标记的试算，绝不隐式落库。"""
    if not db.get(KitchenOrder, order_id):
        raise HTTPException(404, "订单不存在")
    data = prep_service.get_committed(db, order_id)
    if data is not None:
        return data
    try:
        return prep_service.preview_prep(db, order_id)
    except prep_service.PrepNotFound:
        raise HTTPException(404, "订单不存在")

@router.get("/shortages")
def shortages(order_id: int = 1, db: Session = Depends(get_db)):
    """主缺料贴：只统计主贴缺料。含敏拆册不是结存不够，不进缺料贴。"""
    data = latest(order_id=order_id, db=db)
    generated = bool(data.get("generated"))
    return {"order_id": order_id,
            "generated": generated,
            # 主缺料贴只认真正落库的主贴账；试算不算账，贴为空
            "shortages": data.get("shortages", []) if generated else [],
            "stats": data.get("stats", {}) if generated else {}}
