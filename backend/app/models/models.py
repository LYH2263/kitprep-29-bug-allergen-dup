from datetime import datetime
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class Dish(Base):
    __tablename__ = "dishes"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    portion_unit: Mapped[str] = mapped_column(String(16), default="份")

class Ingredient(Base):
    __tablename__ = "ingredients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(16), default="kg")
    stock_qty: Mapped[float] = mapped_column(Float, default=0.0)
    # 含敏标记：True 的原料只进敏料专册，禁止出现在主贴
    is_allergen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

class BomLine(Base):
    __tablename__ = "bom_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    qty_per_portion: Mapped[float] = mapped_column(Float)

class KitchenOrder(Base):
    __tablename__ = "kitchen_orders"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(32), unique=True)
    outlet: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="open")

class OrderLine(Base):
    __tablename__ = "order_lines"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("kitchen_orders.id"))
    dish_id: Mapped[int] = mapped_column(ForeignKey("dishes.id"))
    portions: Mapped[int] = mapped_column(Integer)

class PrepRun(Base):
    __tablename__ = "prep_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("kitchen_orders.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    # draft=仅试算未正式落库；committed=两本账已一起落库，不可改字
    status: Mapped[str] = mapped_column(String(16), default="committed", nullable=False)
    result_json: Mapped[str] = mapped_column(Text, default="{}")

class OrderPrepCommitment(Base):
    """每个订单只许有一套已落库的两本账（主贴 + 专册）。
    订单页与备料台抢点生成时，靠这一行 + 订单行锁保证拿到同一套账。"""
    __tablename__ = "order_prep_commitments"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("kitchen_orders.id"), unique=True, nullable=False)
    prep_run_id: Mapped[int] = mapped_column(ForeignKey("prep_runs.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

class PrepLedgerEntry(Base):
    """备料台账行：主贴(book=main)与敏料专册(book=allergen)分本落库，
    occupied_qty 是占用列（记账不扣结存）；stock_after 留生成时结存快照，
    供提交前核对“占用列拆法与两本账一致”。"""
    __tablename__ = "prep_ledger_entries"
    __table_args__ = (
        UniqueConstraint("prep_run_id", "book", "ingredient_id", name="uq_ledger_run_book_ingredient"),
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    prep_run_id: Mapped[int] = mapped_column(ForeignKey("prep_runs.id"), nullable=False)
    order_id: Mapped[int] = mapped_column(ForeignKey("kitchen_orders.id"), nullable=False)
    book: Mapped[str] = mapped_column(String(16), nullable=False)  # main | allergen
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"), nullable=False)
    ingredient_code: Mapped[str] = mapped_column(String(32))
    ingredient_name: Mapped[str] = mapped_column(String(128))
    unit: Mapped[str] = mapped_column(String(16), default="")
    need_qty: Mapped[float] = mapped_column(Float, default=0.0)
    stock_qty: Mapped[float] = mapped_column(Float, default=0.0)
    shortage: Mapped[float] = mapped_column(Float, default=0.0)
    occupied_qty: Mapped[float] = mapped_column(Float, default=0.0)
    is_allergen: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
