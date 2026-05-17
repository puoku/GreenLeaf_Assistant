from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass
from time import monotonic

from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI, RateLimitError

from app.config import get_settings

settings = get_settings()
logger = logging.getLogger(__name__)
_client: AsyncOpenAI | None = None


@dataclass
class _CircuitState:
    failures: int = 0
    open_until: float = 0.0


_circuit_state = _CircuitState()

PROMPT = '''
Ты классификатор и экстрактор данных для бота магазина. Верни только валидный JSON.
Используй строго такую схему:
{
  "intent": "faq | product_search | order | reservation | manager | other",
  "faq_intent": "address | schedule | delivery | payment | guarantee | returns | pickup | contacts | null",
  "product_query": "строка или null",
  "items": [{"name": "название товара", "quantity": число}],
  "reserve_until": "строка с указанием срока, например 'до среды', или null",
  "reply_hint": "короткая подсказка на русском до 12 слов"
}

Правила:
- Если пользователь упоминает товары с количеством (даже без слова "шт"), занеси их в items. "Пара" = 2, "тройка" = 3, "несколько" = 3.
- Если товар назван без количества — quantity = 1.
- Если упоминает срок брони ("до завтра", "до пятницы", "24 часа"), занеси в reserve_until ровно тот текст, что был в сообщении.
- Если items извлечены — это intent=reservation, даже если в тексте есть "заказ" или "купить".
- Если items пусто — оставь пустой массив [].
- Никакого текста вне JSON. Все ключи и строковые значения только в двойных кавычках.
'''


def _extract_json_object(raw: str) -> str | None:
    start = raw.find('{')
    end = raw.rfind('}')
    if start == -1 or end == -1 or end <= start:
        return None
    return raw[start:end + 1]


def _normalize_payload(data: dict) -> dict:
    source = data.get('source', 'llm')
    raw_items = data.get('items') or []
    items: list[dict] = []
    if isinstance(raw_items, list):
        for entry in raw_items:
            if not isinstance(entry, dict):
                continue
            name = entry.get('name')
            quantity = entry.get('quantity')
            if not name or quantity is None:
                continue
            try:
                qty = int(quantity)
            except (TypeError, ValueError):
                continue
            if qty <= 0:
                continue
            items.append({'name': str(name).strip(), 'quantity': qty})
    return {
        'intent': data.get('intent', 'other'),
        'faq_intent': data.get('faq_intent'),
        'product_query': data.get('product_query'),
        'items': items,
        'reserve_until': data.get('reserve_until'),
        'reply_hint': data.get('reply_hint', ''),
        'source': source,
    }


def _heuristic_classification(text: str) -> dict:
    lowered = (text or '').lower()
    base = {'items': [], 'reserve_until': None, 'source': 'heuristic'}
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
            return {**base, 'intent': 'faq', 'faq_intent': faq_intent, 'product_query': None, 'reply_hint': 'Ответить по FAQ.'}

    if any(word in lowered for word in ['менеджер', 'оператор', 'человек']):
        return {**base, 'intent': 'manager', 'faq_intent': None, 'product_query': None, 'reply_hint': 'Передать диалог менеджеру.'}

    if re.search(r'\b\d+\s*(шт|штук|шт\.)\b', lowered):
        return {**base, 'intent': 'reservation', 'faq_intent': None, 'product_query': text.strip() or None, 'reply_hint': 'Уточнить и передать бронь менеджеру.'}

    if any(word in lowered for word in ['заказ', 'заказать', 'купить', 'оформить заказ']):
        return {**base, 'intent': 'order', 'faq_intent': None, 'product_query': None, 'reply_hint': 'Уточнить товар и количество.'}

    if any(word in lowered for word in ['есть', 'сколько стоит', 'цена', 'в наличии', 'товар']):
        return {**base, 'intent': 'product_search', 'faq_intent': None, 'product_query': text.strip() or None, 'reply_hint': 'Поискать товар в каталоге.'}

    return {**base, 'intent': 'other', 'faq_intent': None, 'product_query': None, 'reply_hint': 'Уточнить запрос.'}


def _is_circuit_open(now: float) -> bool:
    return _circuit_state.open_until > now


def _register_failure(now: float) -> None:
    _circuit_state.failures += 1
    if _circuit_state.failures >= settings.llm_circuit_fail_threshold:
        _circuit_state.open_until = now + settings.llm_circuit_open_seconds


def _register_success() -> None:
    _circuit_state.failures = 0
    _circuit_state.open_until = 0.0


def _is_retryable_error(error: Exception) -> bool:
    if isinstance(error, (APIConnectionError, APITimeoutError, RateLimitError)):
        return True
    if isinstance(error, APIStatusError):
        return error.status_code in {429, 500, 502, 503, 504}
    return False


async def _call_llm(text: str) -> dict:
    global _client
    if _client is None:
        _client = AsyncOpenAI(
            base_url='https://openrouter.ai/api/v1',
            api_key=settings.openai_api_key,
            timeout=settings.llm_timeout_seconds,
            max_retries=0,
        )
    response = await _client.chat.completions.create(
        model=settings.openai_model,
        messages=[
            {'role': 'system', 'content': PROMPT},
            {'role': 'user', 'content': text},
        ],
        temperature=0,
        max_tokens=180,
    )
    if not response.choices:
        raise ValueError('Empty choices from LLM')
    content = response.choices[0].message.content
    if not content:
        raise ValueError('Empty message content from LLM')
    raw = content.strip()
    try:
        payload = _normalize_payload(json.loads(raw))
        payload['source'] = 'llm'
        return payload
    except json.JSONDecodeError:
        extracted = _extract_json_object(raw)
        if not extracted:
            raise
        payload = _normalize_payload(json.loads(extracted))
        payload['source'] = 'llm'
        return payload


async def classify_message(text: str) -> dict:
    if not settings.openai_api_key:
        logger.info('llm.disabled_no_api_key')
        return _heuristic_classification(text)

    now = monotonic()
    if _is_circuit_open(now):
        logger.warning('llm.circuit_open', extra={'open_until': _circuit_state.open_until})
        fallback = _heuristic_classification(text)
        fallback['source'] = 'circuit_fallback'
        return fallback

    max_attempts = max(1, settings.llm_retries + 1)
    for attempt in range(1, max_attempts + 1):
        try:
            payload = await _call_llm(text)
            _register_success()
            logger.info('llm.classify_success', extra={'attempt': attempt, 'source': payload.get('source', 'llm')})
            return payload
        except Exception as exc:
            retryable = _is_retryable_error(exc)
            logger.warning(
                'llm.classify_error',
                extra={
                    'attempt': attempt,
                    'retryable': retryable,
                    'error_type': exc.__class__.__name__,
                },
            )
            if not retryable or attempt >= max_attempts:
                _register_failure(monotonic())
                fallback = _heuristic_classification(text)
                fallback['source'] = 'heuristic_fallback'
                logger.info('llm.fallback_used', extra={'attempt': attempt})
                return fallback
            sleep_for = settings.llm_retry_base_delay_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(sleep_for)

    fallback = _heuristic_classification(text)
    fallback['source'] = 'heuristic_fallback'
    return fallback
