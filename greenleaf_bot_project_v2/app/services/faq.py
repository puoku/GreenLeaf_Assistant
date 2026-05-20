from __future__ import annotations

from rapidfuzz import fuzz
from sqlalchemy import select

from app.db.models import FAQItem
from app.db.session import SessionLocal


# Префиксы, которые сами по себе матчатся как FAQ (greeting / gratitude),
# но если после них идёт реальный вопрос — их нужно снять, чтобы поиск
# нашёл настоящий интент. Сортированы по длине убыванию, чтобы более длинные
# варианты ловились до коротких ("здравствуйте" до "здравствуй").
_PREFIX_TO_STRIP = (
    'здравствуйте',
    'добрый вечер',
    'добрый день',
    'доброе утро',
    'здравствуй',
    'благодарю',
    'спасибо',
    'здарова',
    'привет',
    'hello',
    'hi',
)


def _strip_polite_prefix(text_norm: str) -> str:
    for prefix in _PREFIX_TO_STRIP:
        if text_norm.startswith(prefix):
            rest = text_norm[len(prefix):].lstrip(' ,.!?')
            if len(rest) >= 3:
                return rest
            break
    return text_norm


async def find_faq_answer(text: str) -> tuple[FAQItem | None, int]:
    text_norm = text.lower().replace('ё', 'е').strip()
    if len(text_norm) < 3:
        return None, 0
    text_norm = _strip_polite_prefix(text_norm)
    async with SessionLocal() as session:
        items = (await session.execute(select(FAQItem).where(FAQItem.is_active.is_(True)))).scalars().all()

    best_item = None
    best_score = 0
    for item in items:
        patterns = [p.strip() for p in item.question_patterns.split(';') if p.strip()]
        score = max((fuzz.partial_ratio(text_norm, p.lower()) for p in patterns), default=0)
        if score > best_score:
            best_score = score
            best_item = item
    if best_score >= 75:
        return best_item, best_score
    return None, best_score


async def get_faq_by_intent(intent: str) -> FAQItem | None:
    async with SessionLocal() as session:
        return (await session.execute(select(FAQItem).where(FAQItem.intent == intent, FAQItem.is_active.is_(True)))).scalar_one_or_none()
