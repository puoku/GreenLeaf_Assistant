from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from datetime import datetime
from time import perf_counter
from typing import Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.db.models import ImportLog, Product, StockStatus

settings = get_settings()

ImportMode = Literal['preview', 'insert-only', 'upsert']
ErrorKind = Literal['schema', 'validation', 'conflict', 'system']

MAX_PRICE = 1_000_000
MAX_PV = 10_000
MAX_QUANTITY = 1_000_000
MAX_ERROR_PREVIEW = 20


@dataclass
class ImportErrorItem:
    line: int
    kind: ErrorKind
    message: str


@dataclass
class ImportResult:
    mode: ImportMode
    filename: str
    added_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    error_count: int = 0
    errors: list[ImportErrorItem] | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: int = 0
    status: str = 'ok'

    def to_summary_message(self) -> str:
        base = (
            f'Режим: {self.mode}. Добавлено {self.added_count}, '
            f'обновлено {self.updated_count}, пропущено {self.skipped_count}, '
            f'ошибок {self.error_count}.'
        )
        if not self.errors:
            return base
        preview = '; '.join(f'строка {item.line}: {item.message}' for item in self.errors[:5])
        suffix = '...' if len(self.errors) > 5 else ''
        return f'{base} Ошибки: {preview}{suffix}'


def calc_stock_status(quantity: int) -> str:
    if quantity <= 0:
        return StockStatus.out_of_stock.value
    if quantity <= 5:
        return StockStatus.low.value
    return StockStatus.in_stock.value


def _first_non_empty(row: dict[str, str | None], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if value is None:
            continue
        normalized = str(value).strip()
        if normalized:
            return normalized
    return ''


def extract_pv(title: str) -> float | None:
    match = re.search(r'\bpv\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\b', title.lower())
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', '.'))
    except ValueError:
        return None


def clean_product_name(raw_name: str) -> str:
    name = re.sub(r'\s*\bpv\s*[:=]?\s*[0-9]+(?:[.,][0-9]+)?\b\s*', ' ', raw_name, flags=re.IGNORECASE)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip(' -–—')


def normalize_category(raw_category: str) -> str | None:
    if not raw_category:
        return None
    parts = re.split(r'[;,/|]+', raw_category)
    cleaned: list[str] = []
    seen: set[str] = set()
    for part in parts:
        value = re.sub(r'\s{2,}', ' ', part.strip())
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return '; '.join(cleaned) if cleaned else None


def _parse_float(value: str, *, default: float | None = None) -> float | None:
    if not value:
        return default
    try:
        return float(value.replace(',', '.').replace(' ', '').replace('\u202f', ''))
    except ValueError:
        return None


def _parse_int(value: str, *, default: int | None = None) -> int | None:
    if not value:
        return default
    try:
        return int(value.replace(' ', '').replace('\u202f', ''))
    except ValueError:
        return None


def _validate_ranges(price_partner: float, pv: float, quantity: int) -> list[str]:
    errors: list[str] = []
    if price_partner <= 0 or price_partner > MAX_PRICE:
        errors.append(f'цена должна быть в диапазоне (0, {MAX_PRICE}]')
    if pv < 0 or pv > MAX_PV:
        errors.append(f'PV должен быть в диапазоне [0, {MAX_PV}]')
    if quantity < 0 or quantity > MAX_QUANTITY:
        errors.append(f'количество должно быть в диапазоне [0, {MAX_QUANTITY}]')
    return errors


def _parse_mode(value: str) -> ImportMode:
    if value in {'preview', 'insert-only', 'upsert'}:
        return value
    return 'upsert'


async def run_product_import(
    session: AsyncSession,
    *,
    csv_content: bytes,
    filename: str,
    mode: str,
) -> ImportResult:
    parsed_mode = _parse_mode(mode)
    result = ImportResult(mode=parsed_mode, filename=filename, errors=[], started_at=datetime.utcnow())
    started = perf_counter()

    try:
        text = csv_content.decode('utf-8-sig')
    except UnicodeDecodeError:
        result.errors.append(ImportErrorItem(line=1, kind='schema', message='CSV должен быть в UTF-8'))
        result.error_count = 1
        result.status = 'failed'
        result.finished_at = datetime.utcnow()
        result.duration_ms = int((perf_counter() - started) * 1000)
        await _persist_import_log(session, result)
        return result

    reader = csv.DictReader(io.StringIO(text), delimiter=';')
    if not reader.fieldnames:
        result.errors.append(ImportErrorItem(line=1, kind='schema', message='не найдены заголовки CSV'))
        result.error_count = 1
        result.status = 'failed'
        result.finished_at = datetime.utcnow()
        result.duration_ms = int((perf_counter() - started) * 1000)
        await _persist_import_log(session, result)
        return result
    header_set = {item.strip() for item in reader.fieldnames if item and item.strip()}
    name_headers = {'name', 'Название', 'название', 'Title'}
    price_headers = {'price_partner', 'Цена', 'цена', 'Price'}
    if not (header_set & name_headers):
        result.errors.append(ImportErrorItem(line=1, kind='schema', message='не найдена колонка названия'))
    if not (header_set & price_headers):
        result.errors.append(ImportErrorItem(line=1, kind='schema', message='не найдена колонка цены'))
    if result.errors:
        result.error_count = len(result.errors)
        result.status = 'failed'
        result.finished_at = datetime.utcnow()
        result.duration_ms = int((perf_counter() - started) * 1000)
        await _persist_import_log(session, result)
        return result

    existing_products = (await session.execute(select(Product))).scalars().all()
    existing_by_sku = {item.sku.strip().lower(): item for item in existing_products if item.sku}
    existing_by_name = {item.name.strip().lower(): item for item in existing_products if item.name}

    for idx, row in enumerate(reader, start=2):
        try:
            raw_name = _first_non_empty(row, 'name', 'Название', 'название', 'Title')
            name = clean_product_name(raw_name)
            if not name:
                result.errors.append(ImportErrorItem(line=idx, kind='validation', message='пропущено название'))
                continue

            sku = _first_non_empty(row, 'sku', 'SKU', 'Артикул', 'артикул')
            price_str = _first_non_empty(row, 'price_partner', 'Цена', 'цена', 'Price')
            qty_str = _first_non_empty(row, 'quantity', 'Количество', 'количество', 'остаток', 'Quantity')
            pv_str = _first_non_empty(row, 'pv', 'PV')
            category_raw = _first_non_empty(row, 'category', 'Категория', 'категория', 'Category')
            aliases = _first_non_empty(row, 'aliases', 'Синонимы', 'синонимы')
            description = _first_non_empty(row, 'description', 'Описание', 'описание', 'Description')

            price_partner = _parse_float(price_str)
            if price_partner is None:
                result.errors.append(ImportErrorItem(line=idx, kind='validation', message=f'неверная цена "{price_str}"'))
                continue

            quantity = _parse_int(qty_str, default=0)
            if quantity is None:
                result.errors.append(
                    ImportErrorItem(line=idx, kind='validation', message=f'неверное количество "{qty_str}"')
                )
                continue

            extracted_from_title = extract_pv(raw_name)
            pv = _parse_float(pv_str, default=extracted_from_title if extracted_from_title is not None else 0.0)
            if pv is None:
                result.errors.append(ImportErrorItem(line=idx, kind='validation', message=f'неверный PV "{pv_str}"'))
                continue

            range_errors = _validate_ranges(price_partner, pv, quantity)
            if range_errors:
                for error in range_errors:
                    result.errors.append(ImportErrorItem(line=idx, kind='validation', message=error))
                continue

            category = normalize_category(category_raw)
            price_regular = round(price_partner * settings.partner_price_multiplier, 2)
            normalized_name = name.lower()
            normalized_sku = sku.lower()

            existing = existing_by_sku.get(normalized_sku) if normalized_sku else None
            if existing is None:
                existing = existing_by_name.get(normalized_name)

            if parsed_mode == 'insert-only' and existing is not None:
                result.skipped_count += 1
                continue

            if existing is None:
                result.added_count += 1
                if parsed_mode != 'preview':
                    product = Product(
                        name=name,
                        sku=sku or None,
                        price_partner=price_partner,
                        price_regular=price_regular,
                        quantity=quantity,
                        pv=pv,
                        category=category,
                        aliases=aliases or None,
                        description=description or None,
                        stock_status=calc_stock_status(quantity),
                    )
                    session.add(product)
                    if normalized_sku:
                        existing_by_sku[normalized_sku] = product
                    existing_by_name[normalized_name] = product
                continue

            if parsed_mode == 'preview':
                changed = _product_changed(
                    existing,
                    name=name,
                    sku=sku or None,
                    price_partner=price_partner,
                    price_regular=price_regular,
                    quantity=quantity,
                    pv=pv,
                    category=category,
                    aliases=aliases or None,
                    description=description or None,
                )
                if changed:
                    result.updated_count += 1
                else:
                    result.skipped_count += 1
                continue

            if parsed_mode == 'upsert':
                changed = _apply_product_update(
                    existing,
                    name=name,
                    sku=sku or None,
                    price_partner=price_partner,
                    price_regular=price_regular,
                    quantity=quantity,
                    pv=pv,
                    category=category,
                    aliases=aliases or None,
                    description=description or None,
                )
                if changed:
                    result.updated_count += 1
                else:
                    result.skipped_count += 1
                continue

            result.skipped_count += 1
        except Exception as exc:
            result.errors.append(ImportErrorItem(line=idx, kind='system', message=str(exc)))

    result.error_count = len(result.errors)
    result.status = 'ok' if result.error_count == 0 else 'partial'
    result.finished_at = datetime.utcnow()
    result.duration_ms = int((perf_counter() - started) * 1000)

    if parsed_mode != 'preview':
        await session.commit()

    await _persist_import_log(session, result)
    return result


def _product_changed(
    product: Product,
    *,
    name: str,
    sku: str | None,
    price_partner: float,
    price_regular: float,
    quantity: int,
    pv: float,
    category: str | None,
    aliases: str | None,
    description: str | None,
) -> bool:
    if product.name != name:
        return True
    if (product.sku or '') != (sku or ''):
        return True
    if float(product.price_partner) != float(price_partner):
        return True
    if float(product.price_regular) != float(price_regular):
        return True
    if int(product.quantity) != int(quantity):
        return True
    if float(product.pv or 0) != float(pv or 0):
        return True
    if (product.category or '') != (category or ''):
        return True
    if (product.aliases or '') != (aliases or ''):
        return True
    if (product.description or '') != (description or ''):
        return True
    if product.stock_status != calc_stock_status(quantity):
        return True
    return False


def _apply_product_update(
    product: Product,
    *,
    name: str,
    sku: str | None,
    price_partner: float,
    price_regular: float,
    quantity: int,
    pv: float,
    category: str | None,
    aliases: str | None,
    description: str | None,
) -> bool:
    changed = False

    if product.name != name:
        product.name = name
        changed = True
    if (product.sku or '') != (sku or ''):
        product.sku = sku
        changed = True
    if float(product.price_partner) != float(price_partner):
        product.price_partner = price_partner
        changed = True
    if float(product.price_regular) != float(price_regular):
        product.price_regular = price_regular
        changed = True
    if int(product.quantity) != int(quantity):
        product.quantity = quantity
        changed = True
    if float(product.pv or 0) != float(pv or 0):
        product.pv = pv
        changed = True
    if (product.category or '') != (category or ''):
        product.category = category
        changed = True
    if (product.aliases or '') != (aliases or ''):
        product.aliases = aliases
        changed = True
    if (product.description or '') != (description or ''):
        product.description = description
        changed = True

    stock_status = calc_stock_status(quantity)
    if product.stock_status != stock_status:
        product.stock_status = stock_status
        changed = True

    return changed


async def _persist_import_log(session: AsyncSession, result: ImportResult) -> None:
    errors_preview = ''
    if result.errors:
        serialized = [f'line {item.line} [{item.kind}] {item.message}' for item in result.errors[:MAX_ERROR_PREVIEW]]
        errors_preview = '\n'.join(serialized)

    log = ImportLog(
        filename=result.filename,
        mode=result.mode,
        status=result.status,
        added_count=result.added_count,
        updated_count=result.updated_count,
        skipped_count=result.skipped_count,
        error_count=result.error_count,
        errors_preview=errors_preview or None,
        started_at=result.started_at or datetime.utcnow(),
        finished_at=result.finished_at or datetime.utcnow(),
        duration_ms=result.duration_ms,
    )
    session.add(log)
    await session.commit()
