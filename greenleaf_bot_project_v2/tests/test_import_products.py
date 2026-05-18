from app.services.import_products import (
    _parse_int,
    _validate_ranges,
    normalize_category,
)


def test_parse_int_handles_spaces_and_narrow_nbsp():
    assert _parse_int('1 200') == 1200
    assert _parse_int('99') == 99


def test_parse_int_returns_none_for_invalid():
    assert _parse_int('абв') is None
    assert _parse_int('1.5') is None


def test_parse_int_default_for_empty():
    assert _parse_int('') is None
    assert _parse_int('', default=0) == 0


def test_normalize_category_supports_multiple_separators():
    assert normalize_category('Уход; Гигиена, Дом / Аксессуары | Прочее') == 'Уход; Гигиена; Дом; Аксессуары; Прочее'


def test_normalize_category_returns_none_for_empty():
    assert normalize_category('') is None
    assert normalize_category('   ') is None


def test_validate_ranges_ok():
    assert _validate_ranges(100.0, 1.5, 50) == []


def test_validate_ranges_rejects_negative_price():
    errors = _validate_ranges(-5, 1.0, 10)
    assert any('цена' in e for e in errors)


def test_validate_ranges_rejects_huge_quantity():
    errors = _validate_ranges(100, 1, 999_999_999)
    assert any('количество' in e for e in errors)
