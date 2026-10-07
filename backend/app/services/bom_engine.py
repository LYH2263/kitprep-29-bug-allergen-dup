"""Central kitchen BOM explode: order lines × BOM qty, merge ingredients, shortage = need - stock.

含敏原料(is_allergen=True)只进敏料专册(allergen)，其余进主贴(main)。
两本账互斥且并集完备：同一原料行禁止同时进两本，也禁止哪本都不进。
"""
from __future__ import annotations
from dataclasses import asdict, dataclass

MAIN_BOOK = "main"
ALLERGEN_BOOK = "allergen"

class LedgerSplitError(RuntimeError):
    """主贴/专册拆法对不上（含敏行进主贴、普通行进专册、重复入账或漏账）。"""

@dataclass
class NeedLine:
    ingredient_id: int
    ingredient_code: str
    ingredient_name: str
    unit: str
    need_qty: float
    stock_qty: float
    shortage: float
    is_allergen: bool = False

def explode_and_merge(
    order_lines: list[dict],
    bom_lines: list[dict],
    ingredients: dict[int, dict],
) -> list[NeedLine]:
    """order_lines: dish_id, portions; bom_lines: dish_id, ingredient_id, qty_per_portion."""
    need: dict[int, float] = {}
    for ol in order_lines:
        for bl in bom_lines:
            if bl["dish_id"] != ol["dish_id"]:
                continue
            need[bl["ingredient_id"]] = need.get(bl["ingredient_id"], 0.0) + ol["portions"] * bl["qty_per_portion"]
    lines: list[NeedLine] = []
    for iid, qty in sorted(need.items()):
        ing = ingredients[iid]
        stock = float(ing.get("stock_qty", 0))
        shortage = max(0.0, qty - stock)
        lines.append(NeedLine(
            ingredient_id=iid,
            ingredient_code=ing["code"],
            ingredient_name=ing["name"],
            unit=ing.get("unit", ""),
            need_qty=round(qty, 3),
            stock_qty=round(stock, 3),
            shortage=round(shortage, 3),
            is_allergen=bool(ing.get("is_allergen", False)),
        ))
    return lines

def split_books(lines: list[NeedLine]) -> dict[str, list[NeedLine]]:
    """互斥拆账：含敏行只进专册，其余只进主贴。同一行严禁两本都出现。"""
    books: dict[str, list[NeedLine]] = {MAIN_BOOK: [], ALLERGEN_BOOK: []}
    for l in lines:
        books[ALLERGEN_BOOK if l.is_allergen else MAIN_BOOK].append(l)
    return books

def validate_books(lines: list[NeedLine], books: dict[str, list[NeedLine]]) -> None:
    """两本账互斥且并集完备、归属正确。任何对不上都抛 LedgerSplitError：
    - 含敏行混进主贴 / 非含敏行混进专册；
    - 同一原料行两本都记；
    - 需料行哪本都没落（一本落下另一本空/漏行）；
    - 台账多出本单之外的行。
    """
    main = books.get(MAIN_BOOK)
    allergen = books.get(ALLERGEN_BOOK)
    if main is None or allergen is None:
        raise LedgerSplitError("两本账缺册：主贴或敏料专册缺失")

    main_ids = [l.ingredient_id for l in main]
    reg_ids = [l.ingredient_id for l in allergen]
    expected_ids = {l.ingredient_id for l in lines}

    overlap = set(main_ids) & set(reg_ids)
    if overlap:
        codes = [l.ingredient_code for l in main + allergen if l.ingredient_id in overlap]
        raise LedgerSplitError(f"含敏行同时记进两本账: {sorted(set(codes))}")
    if len(main_ids) != len(set(main_ids)):
        raise LedgerSplitError("主贴存在同一原料重复落账")
    if len(reg_ids) != len(set(reg_ids)):
        raise LedgerSplitError("敏料专册存在同一原料重复落账")

    for l in main:
        if l.is_allergen:
            raise LedgerSplitError(f"含敏原料 {l.ingredient_code} 的行混进了主贴")
    for l in allergen:
        if not l.is_allergen:
            raise LedgerSplitError(f"非含敏原料 {l.ingredient_code} 混进了敏料专册")

    landed = set(main_ids) | set(reg_ids)
    missing = expected_ids - landed
    if missing:
        raise LedgerSplitError(f"需料行未落账（两本必须一起落）: {sorted(missing)}")
    extra = landed - expected_ids
    if extra:
        raise LedgerSplitError(f"台账出现本单之外的行: {sorted(extra)}")

def _book_stats(book_lines: list[NeedLine]) -> dict:
    return {
        "ingredient_count": len(book_lines),
        "shortage_count": sum(1 for l in book_lines if l.shortage > 0),
        "total_shortage_qty": round(sum(l.shortage for l in book_lines), 3),
        "total_need_qty": round(sum(l.need_qty for l in book_lines), 3),
    }

def result_to_dict(lines: list[NeedLine], books: dict[str, list[NeedLine]] | None = None) -> dict:
    """主贴 prep_lines 只含非含敏行；专册 allergen_lines 只含含敏行（可为空）。
    shortages（缺料便利贴）只取主贴——含敏拆册不是结存不够，不得混报。"""
    if books is None:
        books = split_books(lines)
    main_lines = books[MAIN_BOOK]
    allergen_lines = books[ALLERGEN_BOOK]
    return {
        "prep_lines": [asdict(l) for l in main_lines],
        "allergen_lines": [asdict(l) for l in allergen_lines],
        "shortages": [asdict(l) for l in main_lines if l.shortage > 0],
        "stats": _book_stats(main_lines),
        "allergen_stats": _book_stats(allergen_lines),
    }
