from app.db.models import Product
from app.services.product_search import (
    extract_candidate,
    looks_like_product_question,
    normalize,
    score_product,
)


def test_normalize_replaces_yo_with_e():
    assert normalize('Тётя') == 'тетя'
    assert normalize('ёлка') == 'елка'


def test_normalize_lowercases_and_strips_special_chars():
    assert normalize('Hello, WORLD!') == 'hello world'
    assert normalize('123-456') == '123 456'
    assert normalize('   несколько   пробелов   ') == 'несколько пробелов'


def test_extract_candidate_removes_stopwords():
    assert extract_candidate('есть ли у вас зубная паста?') == 'зубная паста'
    assert extract_candidate('сколько стоит гель') == 'гель'


def test_extract_candidate_falls_back_when_all_stopwords():
    # 'товар' и 'нужно' стоп-слова, после фильтрации остаётся пусто
    # функция должна вернуть нормализованную строку как есть
    result = extract_candidate('товар нужно')
    assert result == 'товар нужно'


def test_score_product_high_for_exact_name_match():
    product = Product(name='гель алоэ', aliases=None, price_partner=100, price_regular=200)
    assert score_product(product, 'гель алоэ') >= 90


def test_score_product_uses_aliases():
    product = Product(name='SEALUXE distance', aliases='спрей от комаров, антимоскит', price_partner=100, price_regular=200)
    assert score_product(product, 'спрей от комаров') >= 90


def test_looks_like_product_question_positive():
    assert looks_like_product_question('есть ли у вас гель') is True
    assert looks_like_product_question('сколько стоит паста') is True
    assert looks_like_product_question('хочу заказать мыло') is True


def test_looks_like_product_question_negative():
    assert looks_like_product_question('какой график работы') is False
    assert looks_like_product_question('адрес магазина') is False
