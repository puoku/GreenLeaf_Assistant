from __future__ import annotations

from urllib.parse import quote_plus

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
from app.services.import_products import clean_product_name, extract_pv, run_product_import
from app.services.orders import update_order_status, update_reservation_status
from app.services.product_search import invalidate_search_cache

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
    extracted_pv = extract_pv(name)
    clean_name = clean_product_name(name)
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
    invalidate_search_cache()
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
    invalidate_search_cache()
    return RedirectResponse('/admin/products', status_code=303)


@router.get('/products/{product_id}/edit', response_class=HTMLResponse)
async def product_edit_page(product_id: int, request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail='Product not found')
    return templates.TemplateResponse('product_edit.html', {'request': request, 'product': product, 'user': user})


@router.post('/products/{product_id}/edit')
async def product_edit_submit(
    product_id: int,
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
    async with SessionLocal() as session:
        product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
        if not product:
            raise HTTPException(status_code=404, detail='Product not found')
        product.name = name.strip()
        product.sku = sku.strip() or None
        product.price_partner = price_partner
        product.price_regular = round(price_partner * settings.partner_price_multiplier, 2)
        product.quantity = quantity
        product.pv = pv
        product.category = category.strip() or None
        product.aliases = aliases.strip() or None
        product.description = description.strip() or None
        product.stock_status = calc_stock_status(quantity)
        await session.commit()
    invalidate_search_cache()
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
    invalidate_search_cache()
    return RedirectResponse('/admin/products', status_code=303)


@router.post('/products/import')
async def import_products_csv(
    request: Request,
    user: str = Depends(verify),
    csv_file: UploadFile = File(...),
    mode: str = Form('upsert'),
):
    if not csv_file.filename or not csv_file.filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail='Требуется CSV файл')

    content = await csv_file.read()
    async with SessionLocal() as session:
        result = await run_product_import(session, csv_content=content, filename=csv_file.filename, mode=mode)
    if mode != 'preview':
        invalidate_search_cache()
    message = result.to_summary_message()
    return RedirectResponse(f'/admin/products?message={quote_plus(message)}', status_code=303)


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


@router.get('/faqs/{faq_id}/edit', response_class=HTMLResponse)
async def faq_edit_page(faq_id: int, request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        faq = (await session.execute(select(FAQItem).where(FAQItem.id == faq_id))).scalar_one_or_none()
        if not faq:
            raise HTTPException(status_code=404, detail='FAQ not found')
    return templates.TemplateResponse('faq_edit.html', {'request': request, 'faq': faq, 'user': user})


@router.post('/faqs/{faq_id}/edit')
async def faq_edit_submit(
    faq_id: int,
    request: Request,
    user: str = Depends(verify),
    intent: str = Form(...),
    question_patterns: str = Form(...),
    answer_text: str = Form(...),
):
    async with SessionLocal() as session:
        faq = (await session.execute(select(FAQItem).where(FAQItem.id == faq_id))).scalar_one_or_none()
        if not faq:
            raise HTTPException(status_code=404, detail='FAQ not found')
        faq.intent = intent.strip()
        faq.question_patterns = question_patterns.strip()
        faq.answer_text = answer_text.strip()
        await session.commit()
    return RedirectResponse('/admin/faqs', status_code=303)


@router.post('/faqs/{faq_id}/delete')
async def faq_delete(faq_id: int, request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        faq = (await session.execute(select(FAQItem).where(FAQItem.id == faq_id))).scalar_one_or_none()
        if faq:
            await session.delete(faq)
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


@router.get('/products/create')
@router.get('/products/import')
@router.get('/products/{product_id}/update')
@router.get('/products/{product_id}/delete')
async def _products_post_redirect(request: Request, user: str = Depends(verify)):
    return RedirectResponse('/admin/products', status_code=303)


@router.get('/faqs/create')
@router.get('/faqs/{faq_id}/delete')
async def _faqs_post_redirect(request: Request, user: str = Depends(verify)):
    return RedirectResponse('/admin/faqs', status_code=303)


@router.get('/orders/{order_id}/status')
async def _orders_post_redirect(request: Request, user: str = Depends(verify)):
    return RedirectResponse('/admin/orders', status_code=303)


@router.get('/reservations/{reservation_id}/status')
async def _reservations_post_redirect(request: Request, user: str = Depends(verify)):
    return RedirectResponse('/admin/reservations', status_code=303)
