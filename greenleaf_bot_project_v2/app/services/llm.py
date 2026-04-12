from __future__ import annotations

import json
import re

from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

PROMPT = '''
Ты классификатор запросов магазина. Верни только валидный JSON-объект.
Используй строго такую схему:
{
  "intent": "faq | product_search | order | reservation | manager | other",
  "faq_intent": "address | schedule | delivery | payment | guarantee | returns | pickup | contacts | null",
  "product_query": "строка или null",
  "reply_hint": "короткая подсказка на русском до 12 слов"
}
Если пользователь прислал список товаров с количеством по строкам, это reservation, даже если в тексте есть слово "заказ".
Никакого текста вне JSON. Никаких пояснений. Все ключи и строковые значения только в двойных кавычках.
'''


def _extract_json_object(raw: str) -> str | None:
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start:end + 1]


def _normalize_payload(data: dict) -> dict:
    return {
        'intent': data.get('intent', 'other'),
        'faq_intent': data.get('faq_intent'),
        'product_query': data.get('product_query'),
        'reply_hint': data.get('reply_hint', ''),
    }


def _heuristic_classification(text: str) -> dict:
    lowered = (text or '').lower()
    faq_map = {
        'address': ['где вы', 'адрес', 'где находитесь'],
        'schedule': ['график', 'когда работаете', 'часы работы', 'режим работы'],
        'delivery': ['доставка', 'доставк'],
        'payment': ['оплата', 'оплатить'],
        'guarantee': ['гарантия'],
        'returns': ['возврат', 'вернуть'],
        'pickup': ['самовывоз'],
        'contacts': ['контакт', 'телефон', 'номер', 'связаться'],
    }
    for faq_intent, patterns in faq_map.items():
        if any(pattern in lowered for pattern in patterns):
            return {
                'intent': 'faq',
                'faq_intent': faq_intent,
                'product_query': None,
                'reply_hint': 'Ответить по FAQ.',
            }

    if any(word in lowered for word in ['менеджер', 'оператор', 'человек']):
        return {
            'intent': 'manager',
            'faq_intent': None,
            'product_query': None,
            'reply_hint': 'Передать диалог менеджеру.',
        }

    if re.search(r'\b\d+\s*(шт|штук|шт\.)\b', lowered):
        return {
            'intent': 'reservation',
            'faq_intent': None,
            'product_query': text.strip() or None,
            'reply_hint': 'Уточнить и передать бронь менеджеру.',
        }

    if any(word in lowered for word in ['заказ', 'заказать', 'купить', 'оформить заказ']):
        return {
            'intent': 'order',
            'faq_intent': None,
            'product_query': None,
            'reply_hint': 'Уточнить товар и количество.',
        }

    if any(word in lowered for word in ['есть', 'сколько стоит', 'цена', 'в наличии', 'товар']):
        return {
            'intent': 'product_search',
            'faq_intent': None,
            'product_query': text.strip() or None,
            'reply_hint': 'Поискать товар в каталоге.',
        }

    return {
        'intent': 'other',
        'faq_intent': None,
        'product_query': None,
        'reply_hint': 'Уточнить запрос.',
    }


async def classify_message(text: str) -> dict | None:
    if not settings.openai_api_key:
        print('OPENROUTER: ключ не задан')
        return None
    client = AsyncOpenAI(
        base_url='https://openrouter.ai/api/v1',
        api_key=settings.openai_api_key,
    )
    try:
        response = await client.chat.completions.create(
            model=settings.openai_model,
            messages=[
                {'role': 'system', 'content': PROMPT},
                {'role': 'user', 'content': text},
            ],
            temperature=0,
            max_tokens=180,
        )
        raw = response.choices[0].message.content.strip()
        print('OPENROUTER RAW:', raw)
        try:
            return _normalize_payload(json.loads(raw))
        except json.JSONDecodeError:
            extracted = _extract_json_object(raw)
            if extracted:
                return _normalize_payload(json.loads(extracted))
            raise
    except Exception as e:
        print('OPENROUTER ERROR:', repr(e))
        fallback = _heuristic_classification(text)
        print('OPENROUTER FALLBACK:', fallback)
        return fallback
