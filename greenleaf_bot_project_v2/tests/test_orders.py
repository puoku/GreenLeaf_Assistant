from app.services.orders import (
    calc_stock_status,
    looks_like_reservation_text,
    parse_reservation_items,
)


def test_parse_with_numbered_prefix():
    items = parse_reservation_items('1. паста — 2 шт')
    assert len(items) == 1
    assert items[0].name == 'паста'
    assert items[0].quantity == 2


def test_parse_with_bracket_prefix():
    items = parse_reservation_items('1) гель алоэ — 3 шт')
    assert len(items) == 1
    assert items[0].quantity == 3


def test_parse_accepts_shtuk_and_dot_variant():
    items = parse_reservation_items('паста 2 штук\nгель 1 шт.')
    assert len(items) == 2
    assert [i.quantity for i in items] == [2, 1]


def test_parse_skips_invalid_lines_between_valid():
    text = 'паста — 2 шт\nкакая-то заметка\nгель алоэ — 1 шт'
    items = parse_reservation_items(text)
    assert len(items) == 2
    assert items[0].name == 'паста'
    assert items[1].name == 'гель алоэ'


def test_looks_like_reservation_by_keyword():
    assert looks_like_reservation_text('хочу забронировать') is True
    assert looks_like_reservation_text('давайте бронь') is True
    assert looks_like_reservation_text('хочу отложить пасту') is True


def test_looks_like_reservation_ignores_plain_questions():
    assert looks_like_reservation_text('какой график работы') is False
    assert looks_like_reservation_text('адрес магазина') is False


def test_calc_stock_status_branches():
    assert calc_stock_status(0) == 'out_of_stock'
    assert calc_stock_status(-5) == 'out_of_stock'
    assert calc_stock_status(1) == 'low'
    assert calc_stock_status(5) == 'low'
    assert calc_stock_status(6) == 'in_stock'
    assert calc_stock_status(100) == 'in_stock'
