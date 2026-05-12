from __future__ import annotations

import re
from dataclasses import dataclass
from time import monotonic

from rapidfuzz import fuzz
from sqlalchemy import or_, select

from app.db.models import Product
from app.db.session import SessionLocal

STOPWORDS = {
    'есть', 'ли', 'у', 'вас', 'сколько', 'стоит', 'хочу', 'заказать', 'отложить', 'бронь',
    'можно', 'мне', 'пожалуйста', 'нужен', 'нужна', 'нужно', 'этот', 'эта', 'эти', 'товар',
    'в', 'на', 'и', 'или', 'для', 'с', 'по', 'как', 'какие', 'какой', 'цена', 'наличии',
    'нибудь', 'какойнибудь', 'чтонибудь'
}

SEARCH_TTL_SECONDS = 30
DIRECT_MATCH_TTL_SECONDS = 30
SEARCH_CANDIDATE_LIMIT = 120


@dataclass
class SearchResult:
    products: list[Product]
    query: str


@dataclass
class _CacheEntry:
    expires_at: float
    product_ids: list[int]


_search_cache: dict[tuple[str, int], _CacheEntry] = {}
_direct_cache: dict[str, _CacheEntry] = {}


def invalidate_search_cache() -> None:
    _search_cache.clear()
    _direct_cache.clear()


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


async def _fetch_products_by_ids(product_ids: list[int]) -> list[Product]:
    if not product_ids:
        return []
    async with SessionLocal() as session:
        products = (
            await session.execute(
                select(Product).where(Product.id.in_(product_ids), Product.is_active.is_(True))
            )
        ).scalars().all()
    by_id = {product.id: product for product in products}
    return [by_id[item_id] for item_id in product_ids if item_id in by_id]


def _cache_get(cache: dict, key):
    item = cache.get(key)
    if not item:
        return None
    if item.expires_at <= monotonic():
        cache.pop(key, None)
        return None
    return item.product_ids


def _cache_set(cache: dict, key, ids: list[int], ttl_seconds: int) -> None:
    cache[key] = _CacheEntry(expires_at=monotonic() + ttl_seconds, product_ids=ids)


async def _fetch_candidates(query: str, limit: int = SEARCH_CANDIDATE_LIMIT) -> list[Product]:
    tokens = [token for token in query.split() if token][:4]
    like_conditions = [
        Product.name.ilike(f'%{query}%'),
        Product.sku.ilike(f'%{query}%'),
        Product.aliases.ilike(f'%{query}%'),
        Product.category.ilike(f'%{query}%'),
        Product.description.ilike(f'%{query}%'),
    ]
    for token in tokens:
        like_conditions.append(Product.name.ilike(f'%{token}%'))
        like_conditions.append(Product.sku.ilike(f'%{token}%'))
        like_conditions.append(Product.aliases.ilike(f'%{token}%'))
        like_conditions.append(Product.description.ilike(f'%{token}%'))

    async with SessionLocal() as session:
        candidates = (
            await session.execute(
                select(Product)
                .where(Product.is_active.is_(True), or_(*like_conditions))
                .limit(limit)
            )
        ).scalars().all()
    return candidates


async def search_products(text: str, limit: int = 5) -> SearchResult:
    query = extract_candidate(text)
    if not query:
        return SearchResult(products=[], query=query)

    cache_key = (query, limit)
    cached_ids = _cache_get(_search_cache, cache_key)
    if cached_ids is not None:
        return SearchResult(products=await _fetch_products_by_ids(cached_ids), query=query)

    products = await _fetch_candidates(query)

    scored = sorted(((score_product(p, query), p) for p in products), key=lambda x: x[0], reverse=True)
    filtered = [product for score, product in scored if score >= 55][:limit]
    _cache_set(_search_cache, cache_key, [product.id for product in filtered], SEARCH_TTL_SECONDS)
    return SearchResult(products=filtered, query=query)


async def find_direct_product_match(text: str) -> Product | None:
    query = extract_candidate(text)
    if not query:
        return None
    query_norm = normalize(query)
    query_tokens = query_norm.split()
    if len(query_tokens) < 2 and len(query_norm) < 8:
        return None

    cached_ids = _cache_get(_direct_cache, query_norm)
    if cached_ids is not None:
        products = await _fetch_products_by_ids(cached_ids)
    else:
        products = await _fetch_candidates(query_norm, limit=SEARCH_CANDIDATE_LIMIT)

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
        _cache_set(_direct_cache, query_norm, [best_product.id], DIRECT_MATCH_TTL_SECONDS)
        return best_product
    _cache_set(_direct_cache, query_norm, [], DIRECT_MATCH_TTL_SECONDS)
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
