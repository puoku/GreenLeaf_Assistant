from __future__ import annotations

import csv
import io
import re

from aiogram import Bot
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select
from sqlalchemy.orm import selectinload

from app.config import get_settings
from app.db.models import FAQItem, Order, OrderStatus, Product, Reservation, ReservationStatus, StockStatus
from app.db.session import SessionLocal
from app.services.orders import update_order_status, update_reservation_status

router = APIRouter()
templates = Jinja2Templates(directory='app/templates')
security = HTTPBasic()
settings = get_settings()


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if credentials.username != settings.admin_username or credentials.password != settings.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={'WWW-Authenticate': 'Basic'})
    return credentials.username


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


def _extract_pv(title: str) -> float | None:
    match = re.search(r'\bpv\s*[:=]?\s*([0-9]+(?:[.,][0-9]+)?)\b', title.lower())
    if not match:
        return None
    try:
        return float(match.group(1).replace(',', '.'))
    except ValueError:
        return None


def _clean_product_name(raw_name: str) -> str:
    # Remove embedded "PV 1.5", "PV=1.5", "PV:1.5" fragments from product titles.
    name = re.sub(r'\s*\bpv\s*[:=]?\s*[0-9]+(?:[.,][0-9]+)?\b\s*', ' ', raw_name, flags=re.IGNORECASE)
    name = re.sub(r'\s{2,}', ' ', name)
    return name.strip(' -–—')


def display_status(status_value: str) -> str:
    mapping = {
        'CONFIRMED': 'ПОДТВЕРЖДЕНО',
        'CANCELED': 'ОТКЛОНЕНО',
        'NEW': 'НОВОЕ',
        'IN_PROGRESS': 'В РАБОТЕ',
        'READY': 'ГОТОВО',
        'COMPLETED': 'ЗАВЕРШЕНО',
    }
    return mapping.get(status_value, status_value)


def status_class(status_value: str) -> str:
    if status_value == 'CONFIRMED':
        return 'status-ok'
    if status_value == 'CANCELED':
        return 'status-bad'
    return 'status-neutral'


async def notify_customer(customer_id: int, text: str) -> None:
    bot = Bot(settings.bot_token)
    try:
        await bot.send_message(customer_id, text)
    finally:
        await bot.session.close()


@router.get('/', response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        products_count = len((await session.execute(select(Product.id))).scalars().all())
        orders = (
            await session.execute(
                select(Order).options(selectinload(Order.customer)).order_by(desc(Order.created_at)).limit(10)
            )
        ).scalars().all()
        reservations = (
            await session.execute(
                select(Reservation).options(selectinload(Reservation.customer)).order_by(desc(Reservation.created_at)).limit(10)
            )
        ).scalars().all()
    return templates.TemplateResponse(
        'dashboard.html',
        {
            'request': request,
            'products_count': products_count,
            'orders': orders,
            'reservations': reservations,
            'user': user,
            'display_status': display_status,
            'status_class': status_class,
        },
    )


@router.get('/products', response_class=HTMLResponse)
async def products_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.id.desc()))).scalars().all()
    message = request.query_params.get('message', '')
    return templates.TemplateResponse('products.html', {'request': request, 'products': products, 'user': user, 'message': message})


@router.post('/products/create')
async def create_product(
    request: Request,
    user: str = Depends(verify),
    name: str = Form(...),
    sku: str = Form(''),
    price_partner: float = Form(...),
    quantity: int = Form(...),
    pv: float = Form(0),
    category: str = Form(''),
    aliases: str = Form(''),
    description: str = Form(''),
):
    extracted_pv = _extract_pv(name)
    clean_name = _clean_product_name(name)
    effective_pv = pv if pv not in (None, 0) else (extracted_pv if extracted_pv is not None else 0)

    async with SessionLocal() as session:
        session.add(Product(
            name=clean_name,
            sku=sku or None,
            price_partner=price_partner,
            price_regular=round(price_partner * settings.partner_price_multiplier, 2),
            quantity=quantity,
            pv=effective_pv,
            category=category or None,
            aliases=aliases or None,
            description=description or None,
            stock_status=calc_stock_status(quantity),
        ))
        await session.commit()
    return RedirectResponse('/admin/products', status_code=303)


@router.post('/products/{product_id}/update')
async def update_product(
    product_id: int,
    request: Request,
    user: str = Depends(verify),
    quantity: int = Form(...),
):
    async with SessionLocal() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail='Product not found')
        product.quantity = quantity
        product.stock_status = calc_stock_status(quantity)
        await session.commit()
    return RedirectResponse('/admin/products', status_code=303)


@router.post('/products/{product_id}/delete')
async def delete_product(
    product_id: int,
    request: Request,
    user: str = Depends(verify),
):
    async with SessionLocal() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if product:
            await session.delete(product)
            await session.commit()
    return RedirectResponse('/admin/products', status_code=303)


@router.post('/products/import')
async def import_products_csv(
    request: Request,
    user: str = Depends(verify),
    csv_file: UploadFile = File(...),
):
    if not csv_file.filename or not csv_file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail='Требуется CSV файл')

    content = await csv_file.read()
    text = content.decode('utf-8-sig')

    imported = 0
    updated = 0
    skipped = 0
    errors = []

    async with SessionLocal() as session:
        reader = csv.DictReader(io.StringIO(text), delimiter=';')
        existing_products = (await session.execute(select(Product))).scalars().all()
        existing_by_sku = {item.sku.strip().lower(): item for item in existing_products if item.sku}
        existing_by_name = {item.name.strip().lower(): item for item in existing_products if item.name}

        for idx, row in enumerate(reader, start=2):
            try:
                raw_name = _first_non_empty(row, 'name', 'Название', 'название', 'Title')
                name = _clean_product_name(raw_name)
                sku = _first_non_empty(row, 'sku', 'SKU', 'Артикул', 'артикул')
                price_str = _first_non_empty(row, 'price_partner', 'Цена', 'цена', 'Price')
                qty_str = _first_non_empty(row, 'quantity', 'Количество', 'количество', 'остаток', 'Quantity')
                pv_str = _first_non_empty(row, 'pv', 'PV')
                category = _first_non_empty(row, 'category', 'Категория', 'категория', 'Category')
                aliases = _first_non_empty(row, 'aliases', 'Синонимы', 'синонимы')
                description = _first_non_empty(row, 'description', 'Описание', 'описание', 'Description')

                if not name:
                    errors.append(f'Строка {idx}: пропущено название')
                    continue

                normalized_name = name.lower()
                normalized_sku = sku.lower()

                try:
                    price_partner = float(price_str.replace(',', '.').replace(' ', '').replace('\u202f', ''))
                except ValueError:
                    errors.append(f'Строка {idx}: неверная цена "{price_str}"')
                    continue

                if qty_str:
                    try:
                        quantity = int(qty_str.replace(' ', '').replace('\u202f', ''))
                    except ValueError:
                        errors.append(f'Строка {idx}: неверное количество "{qty_str}"')
                        continue
                else:
                    quantity = 0

                try:
                    extracted_from_title = _extract_pv(raw_name)
                    pv = float(pv_str.replace(',', '.')) if pv_str else (extracted_from_title if extracted_from_title is not None else 0.0)
                except ValueError:
                    pv = 0.0

                price_regular = round(price_partner * settings.partner_price_multiplier, 2)

                existing_product = None
                if normalized_sku:
                    existing_product = existing_by_sku.get(normalized_sku)
                if existing_product is None:
                    existing_product = existing_by_name.get(normalized_name)

                if existing_product is None:
                    product = Product(
                        name=name,
                        sku=sku or None,
                        price_partner=price_partner,
                        price_regular=price_regular,
                        quantity=quantity,
                        pv=pv,
                        category=category or None,
                        aliases=aliases or None,
                        description=description or None,
                        stock_status=calc_stock_status(quantity),
                    )
                    session.add(product)
                    imported += 1
                    if normalized_sku:
                        existing_by_sku[normalized_sku] = product
                    existing_by_name[normalized_name] = product
                else:
                    changed = False

                    if existing_product.name != name:
                        existing_product.name = name
                        changed = True
                    if (existing_product.sku or '') != (sku or ''):
                        existing_product.sku = sku or None
                        changed = True
                    if existing_product.price_partner != price_partner:
                        existing_product.price_partner = price_partner
                        changed = True
                    if existing_product.price_regular != price_regular:
                        existing_product.price_regular = price_regular
                        changed = True
                    if existing_product.quantity != quantity:
                        existing_product.quantity = quantity
                        changed = True
                    if float(existing_product.pv or 0) != float(pv or 0):
                        existing_product.pv = pv
                        changed = True
                    if (existing_product.category or '') != (category or ''):
                        existing_product.category = category or None
                        changed = True
                    if (existing_product.aliases or '') != (aliases or ''):
                        existing_product.aliases = aliases or None
                        changed = True
                    if (existing_product.description or '') != (description or ''):
                        existing_product.description = description or None
                        changed = True

                    new_stock_status = calc_stock_status(quantity)
                    if existing_product.stock_status != new_stock_status:
                        existing_product.stock_status = new_stock_status
                        changed = True

                    if changed:
                        updated += 1
                    else:
                        skipped += 1

            except Exception as e:
                errors.append(f'Строка {idx}: {str(e)}')

        await session.commit()

    error_msg = ''
    if errors:
        error_msg = f' Ошибки: {"; ".join(errors[:5])}' + ('...' if len(errors) > 5 else '')

    return RedirectResponse(
        f'/admin/products?message=Импортировано {imported} товаров. Обновлено {updated}. Пропущено без изменений: {skipped}.{error_msg}',
        status_code=303,
    )


@router.get('/faqs', response_class=HTMLResponse)
async def faqs_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        faqs = (await session.execute(select(FAQItem).order_by(FAQItem.id.desc()))).scalars().all()
    return templates.TemplateResponse('faqs.html', {'request': request, 'faqs': faqs, 'user': user})


@router.post('/faqs/create')
async def create_faq(
    request: Request,
    user: str = Depends(verify),
    intent: str = Form(...),
    question_patterns: str = Form(...),
    answer_text: str = Form(...),
):
    async with SessionLocal() as session:
        session.add(FAQItem(intent=intent, question_patterns=question_patterns, answer_text=answer_text))
        await session.commit()
    return RedirectResponse('/admin/faqs', status_code=303)


@router.get('/orders', response_class=HTMLResponse)
async def orders_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        orders = (await session.execute(select(Order).options(selectinload(Order.customer)).order_by(desc(Order.created_at)))).scalars().all()
    return templates.TemplateResponse(
        'orders.html',
        {
            'request': request,
            'orders': orders,
            'user': user,
            'display_status': display_status,
            'status_class': status_class,
            'OrderStatus': OrderStatus,
        },
    )


@router.post('/orders/{order_id}/status')
async def update_order_from_admin(
    order_id: int,
    request: Request,
    user: str = Depends(verify),
    status_value: str = Form(...),
):
    order, customer = await update_order_status(order_id, status_value)
    if order and customer:
        text_map = {
            OrderStatus.confirmed.value: f'Ваш заказ #{order.id} подтверждён менеджером.',
            OrderStatus.in_progress.value: f'Ваш заказ #{order.id} взят в работу менеджером.',
            OrderStatus.canceled.value: f'Ваш заказ #{order.id} отменён. Для уточнения деталей с вами свяжется менеджер.',
        }
        text = text_map.get(status_value)
        if text:
            await notify_customer(customer.telegram_user_id, text)
    return RedirectResponse('/admin/orders', status_code=303)


@router.get('/reservations', response_class=HTMLResponse)
async def reservations_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        reservations = (
            await session.execute(select(Reservation).options(selectinload(Reservation.customer)).order_by(desc(Reservation.created_at)))
        ).scalars().all()
    return templates.TemplateResponse(
        'reservations.html',
        {
            'request': request,
            'reservations': reservations,
            'user': user,
            'display_status': display_status,
            'status_class': status_class,
            'ReservationStatus': ReservationStatus,
        },
    )


@router.post('/reservations/{reservation_id}/status')
async def update_reservation_from_admin(
    reservation_id: int,
    request: Request,
    user: str = Depends(verify),
    status_value: str = Form(...),
):
    reservation, customer = await update_reservation_status(reservation_id, status_value)
    if reservation and customer:
        text_map = {
            ReservationStatus.confirmed.value: f'Ваша бронь #{reservation.id} подтверждена менеджером.',
            ReservationStatus.canceled.value: f'Бронь #{reservation.id} отклонена. Для уточнения деталей вам напишет менеджер.',
        }
        text = text_map.get(status_value)
        if text:
            await notify_customer(customer.telegram_user_id, text)
    return RedirectResponse('/admin/reservations', status_code=303)
