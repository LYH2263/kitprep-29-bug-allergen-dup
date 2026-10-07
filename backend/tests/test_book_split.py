from app.services.bom_engine import (
    ALLERGEN_BOOK, MAIN_BOOK, LedgerSplitError,
    explode_and_merge, result_to_dict, split_books, validate_books,
)
import pytest

ORDER_LINES = [{"dish_id": 1, "portions": 10}, {"dish_id": 2, "portions": 5}]
BOM = [
    {"dish_id": 1, "ingredient_id": 1, "qty_per_portion": 0.2},
    {"dish_id": 1, "ingredient_id": 2, "qty_per_portion": 0.1},
    {"dish_id": 2, "ingredient_id": 1, "qty_per_portion": 0.3},
    {"dish_id": 2, "ingredient_id": 3, "qty_per_portion": 0.4},
]

def _ings(flags: dict[int, bool] | None = None, stocks: dict[int, float] | None = None):
    flags = flags or {}
    stocks = stocks or {1: 1.0, 2: 5.0, 3: 0.5}
    return {
        iid: {"code": f"I{iid}", "name": f"料{iid}", "unit": "kg",
              "stock_qty": stocks.get(iid, 0.0), "is_allergen": flags.get(iid, False)}
        for iid in (1, 2, 3)
    }

def test_no_allergen_marker_all_go_main_book_empty_register():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings())
    result = result_to_dict(lines)
    assert {l["ingredient_id"] for l in result["prep_lines"]} == {1, 2, 3}
    assert result["allergen_lines"] == []  # 未打过含敏：专册为空
    books = split_books(lines)
    assert [l.ingredient_id for l in books[ALLERGEN_BOOK]] == []

def test_allergen_lines_only_in_register_never_in_main():
    # 原料 3 打含敏：需求 5*0.4=2.0，结存 0.5，缺 1.5
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={3: True}))
    result = result_to_dict(lines)
    main_ids = {l["ingredient_id"] for l in result["prep_lines"]}
    reg_ids = {l["ingredient_id"] for l in result["allergen_lines"]}
    assert main_ids == {1, 2}          # 主贴禁止再出现含敏行
    assert reg_ids == {3}              # 含敏行只进专册
    assert main_ids.isdisjoint(reg_ids)
    assert all(l["is_allergen"] for l in result["allergen_lines"])
    assert not any(l["is_allergen"] for l in result["prep_lines"])

def test_allergen_shortage_not_reported_as_stock_shortage():
    # 含敏料结存不够也不算“缺料”，缺料贴只取主贴
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={3: True}))
    result = result_to_dict(lines)
    assert {l["ingredient_id"] for l in result["shortages"]} == {1}  # 需3.5存1.0
    assert all(not l["is_allergen"] for l in result["shortages"])

def test_validate_rejects_double_entry_in_both_books():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={2: True}))
    bad = {MAIN_BOOK: list(lines), ALLERGEN_BOOK: [l for l in lines if l.ingredient_id == 2]}
    with pytest.raises(LedgerSplitError):
        validate_books(lines, bad)

def test_validate_rejects_dropped_line():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={2: True}))
    bad = {MAIN_BOOK: [l for l in lines if l.ingredient_id == 1],
           ALLERGEN_BOOK: [l for l in lines if l.ingredient_id == 2]}  # 原料3漏落
    with pytest.raises(LedgerSplitError):
        validate_books(lines, bad)

def test_split_books_is_disjoint_and_complete_for_mixed_flags():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={2: True, 3: True}))
    books = split_books(lines)
    main_ids = {l.ingredient_id for l in books[MAIN_BOOK]}
    reg_ids = {l.ingredient_id for l in books[ALLERGEN_BOOK]}
    assert main_ids == {1}
    assert reg_ids == {2, 3}
    assert main_ids.isdisjoint(reg_ids)
    assert main_ids | reg_ids == {l.ingredient_id for l in lines}
    validate_books(lines, books)  # 正规拆法必须通过

def test_validate_rejects_allergen_crammed_into_main():
    # 含敏行全塞进主贴、专册空着：也不能算过
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={2: True}))
    bad = {MAIN_BOOK: list(lines), ALLERGEN_BOOK: []}
    with pytest.raises(LedgerSplitError):
        validate_books(lines, bad)

def test_validate_rejects_extra_book_line():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings(flags={3: True}))
    extra = explode_and_merge(
        [{"dish_id": 1, "portions": 1}],
        [{"dish_id": 1, "ingredient_id": 2, "qty_per_portion": 9.0}],
        _ings(flags={3: True}),
    )
    bad = {MAIN_BOOK: [l for l in lines if not l.is_allergen] + extra,
           ALLERGEN_BOOK: [l for l in lines if l.is_allergen]}
    with pytest.raises(LedgerSplitError):
        validate_books(lines, bad)

def test_validate_rejects_missing_book_key():
    lines = explode_and_merge(ORDER_LINES, BOM, _ings())
    with pytest.raises(LedgerSplitError):
        validate_books(lines, {MAIN_BOOK: list(lines)})
