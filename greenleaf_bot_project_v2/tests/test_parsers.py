from app.services.import_products import (
    _parse_float,
    clean_product_name,
    extract_pv,
    normalize_category,
)
from app.services.orders import parse_reservation_items
from app.services.product_search import normalize


def test_parse_reservation_items_single():
    items = parse_reservation_items('паста — 2 шт')
    assert len(items) == 1
    assert items[0].name == 'паста'
    assert items[0].quantity == 2


def test_parse_reservation_items_multiple_lines():
    items = parse_reservation_items('паста — 2 шт\nгель алоэ — 1 шт')
    assert len(items) == 2
    assert items[1].quantity == 1


def test_parse_reservation_items_ignores_plain_text():
    assert parse_reservation_items('') == []
    assert parse_reservation_items('просто привет') == []


def test_clean_product_name_strips_pv_marker():
    assert clean_product_name('CARICH мыло PV: 2.5') == 'CARICH мыло'
    assert clean_product_name('товар без значения') == 'товар без значения'


def test_extract_pv_returns_float():
    assert extract_pv('товар PV 1.5') == 1.5
    assert extract_pv('pv=0.2') == 0.2
    assert extract_pv('товар без значения') is None


def test_parse_float_handles_spaces_and_comma():
    assert _parse_float('1 200,50') == 1200.5
    assert _parse_float('99') == 99.0
    assert _parse_float('') is None


def test_normalize_category_deduplicates():
    assert normalize_category('Уход; Гигиена, Уход') == 'Уход; Гигиена'
    assert normalize_category('') is None


def test_normalize_text_basic():
    assert normalize('Привет, МИР!') == 'привет мир'
    assert normalize('Тётя') == 'тетя'
