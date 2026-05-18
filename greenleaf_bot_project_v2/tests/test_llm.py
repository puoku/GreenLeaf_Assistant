from app.services.llm import (
    _extract_json_object,
    _heuristic_classification,
    _normalize_payload,
)


def test_extract_json_object_clean():
    raw = '{"intent": "faq"}'
    assert _extract_json_object(raw) == '{"intent": "faq"}'


def test_extract_json_object_with_surrounding_noise():
    raw = 'Some prefix\n{"intent": "order"}\nsome suffix'
    assert _extract_json_object(raw) == '{"intent": "order"}'


def test_extract_json_object_returns_none_when_no_json():
    assert _extract_json_object('просто текст без скобок') is None
    assert _extract_json_object('') is None


def test_normalize_payload_keeps_valid_items():
    data = {
        'intent': 'reservation',
        'items': [{'name': 'паста', 'quantity': 2}, {'name': 'гель', 'quantity': 1}],
        'reserve_until': 'до завтра',
    }
    result = _normalize_payload(data)
    assert len(result['items']) == 2
    assert result['items'][0] == {'name': 'паста', 'quantity': 2}
    assert result['reserve_until'] == 'до завтра'


def test_normalize_payload_drops_items_without_quantity():
    data = {'items': [{'name': 'паста'}, {'name': 'гель', 'quantity': 1}]}
    result = _normalize_payload(data)
    assert len(result['items']) == 1
    assert result['items'][0]['name'] == 'гель'


def test_normalize_payload_drops_items_with_bad_quantity():
    data = {
        'items': [
            {'name': 'a', 'quantity': 0},
            {'name': 'b', 'quantity': -3},
            {'name': 'c', 'quantity': 'abc'},
            {'name': 'd', 'quantity': 5},
        ]
    }
    result = _normalize_payload(data)
    assert [item['name'] for item in result['items']] == ['d']


def test_normalize_payload_collapses_null_string_to_none():
    assert _normalize_payload({'reserve_until': 'null'})['reserve_until'] is None
    assert _normalize_payload({'reserve_until': 'None'})['reserve_until'] is None
    assert _normalize_payload({'reserve_until': '   '})['reserve_until'] is None


def test_heuristic_classification_detects_faq():
    result = _heuristic_classification('где вы находитесь')
    assert result['intent'] == 'faq'
    assert result['faq_intent'] == 'address'
    assert result['items'] == []


def test_heuristic_classification_detects_reservation_by_shtuk():
    result = _heuristic_classification('паста 2 шт')
    assert result['intent'] == 'reservation'


def test_heuristic_classification_falls_back_to_other():
    result = _heuristic_classification('абракадабра')
    assert result['intent'] == 'other'
    assert result['items'] == []
    assert result['reserve_until'] is None
