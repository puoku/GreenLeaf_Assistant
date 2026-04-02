from __future__ import annotations

import json

from openai import AsyncOpenAI

from app.config import get_settings

settings = get_settings()

PROMPT = '''
Ты классификатор запросов магазина. Верни JSON с полями:
intent: faq | product_search | order | reservation | manager | other
faq_intent: address | schedule | delivery | payment | guarantee | returns | pickup | contacts | null
product_query: строка или null
reply_hint: короткая подсказка на русском
Если пользователь прислал список товаров с количеством по строкам, это reservation, даже если в тексте есть слово "заказ".
Никакого текста вне JSON.
'''


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
        return json.loads(raw)
    except Exception as e:
        print('OPENROUTER ERROR:', repr(e))
        return None
