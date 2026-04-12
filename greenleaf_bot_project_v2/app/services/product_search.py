from __future__ import annotations

import re
from dataclasses import dataclass

from rapidfuzz import fuzz
from sqlalchemy import select

from app.db.models import Product
from app.db.session import SessionLocal

STOPWORDS = {
    'есть', 'ли', 'у', 'вас', 'сколько', 'стоит', 'хочу', 'заказать', 'отложить', 'бронь',
    'можно', 'мне', 'пожалуйста', 'нужен', 'нужна', 'нужно', 'этот', 'эта', 'эти', 'товар',
    'в', 'на', 'и', 'или', 'для', 'с', 'по', 'как', 'какие', 'какой', 'цена', 'наличии',
    'нибудь', 'какойнибудь', 'чтонибудь'
}


@dataclass
class SearchResult:
    products: list[Product]
    query: str


def normalize(text: str) -> str:
    text = text.lower().replace('ё', 'е')
    text = re.sub(r'[^a-zа-я0-9\s]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def extract_candidate(text: str) -> str:
    value = normalize(text)
    tokens = [t for t in value.split() if t not in STOPWORDS]
    return ' '.join(tokens).strip() or value


def score_product(product: Product, query: str) -> int:
    haystack = ' '.join(
        part for part in [product.name, product.aliases or '', product.category or '', product.description or '', product.sku or ''] if part
    ).lower()
    query = query.lower()
    scores = [
        fuzz.partial_ratio(query, haystack),
        fuzz.token_set_ratio(query, haystack),
        fuzz.partial_ratio(query, (product.aliases or '').lower()),
        fuzz.partial_ratio(query, product.name.lower()),
    ]
    return int(max(scores))


async def search_products(text: str, limit: int = 5) -> SearchResult:
    query = extract_candidate(text)
    async with SessionLocal() as session:
        products = (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars().all()

    scored = sorted(((score_product(p, query), p) for p in products), key=lambda x: x[0], reverse=True)
    filtered = [product for score, product in scored if score >= 55][:limit]
    return SearchResult(products=filtered, query=query)


async def find_direct_product_match(text: str) -> Product | None:
    query = extract_candidate(text)
    if not query:
        return None
    query_norm = normalize(query)
    query_tokens = query_norm.split()
    if len(query_tokens) < 2 and len(query_norm) < 8:
        return None
    async with SessionLocal() as session:
        products = (await session.execute(select(Product).where(Product.is_active.is_(True)))).scalars().all()

    best_product = None
    best_score = 0
    strong_matches: list[Product] = []
    for product in products:
        candidates = [product.name, product.aliases or '', product.sku or '']
        for candidate in candidates:
            candidate_norm = normalize(candidate)
            if not candidate_norm:
                continue
            if query_norm == candidate_norm:
                return product
            if query_norm in candidate_norm:
                score = 95
            else:
                score = fuzz.token_set_ratio(query_norm, candidate_norm)
            if score > best_score:
                best_score = int(score)
                best_product = product
            if score >= 88 and all(existing.id != product.id for existing in strong_matches):
                strong_matches.append(product)
    if len(strong_matches) == 1 and best_score >= 88:
        return best_product
    return None


async def get_product_by_id(product_id: int) -> Product | None:
    async with SessionLocal() as session:
        return (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()


def looks_like_product_question(text: str) -> bool:
    t = normalize(text)
    patterns = [
        'есть ли', 'сколько стоит', 'какие есть', 'есть у вас', 'в наличии',
        'хочу заказать', 'отложите', 'бронь', 'забронировать', 'купить', 'товар',
    ]
    return any(p in t for p in patterns)


def format_product_card(product: Product) -> str:
    stock = 'в наличии' if product.quantity > 0 else 'нет в наличии'
    return (
        f"<b>{product.name}</b>\n"
        f"Артикул: {product.sku or '—'}\n"
        f"Партнёрская цена: {int(product.price_partner)} ₽\n"
        f"Обычная цена: {int(product.price_regular)} ₽\n"
        f"PV: {product.pv}\n"
        f"Остаток: {product.quantity} шт. ({stock})\n"
        f"Категория: {product.category or '—'}"
    )
