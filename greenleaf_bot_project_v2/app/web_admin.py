from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from fastapi.templating import Jinja2Templates
from sqlalchemy import desc, select

from app.config import get_settings
from app.db.models import FAQItem, Order, Product, Reservation
from app.db.session import SessionLocal

router = APIRouter()
templates = Jinja2Templates(directory='app/templates')
security = HTTPBasic()
settings = get_settings()


def verify(credentials: HTTPBasicCredentials = Depends(security)) -> str:
    if credentials.username != settings.admin_username or credentials.password != settings.admin_password:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, headers={'WWW-Authenticate': 'Basic'})
    return credentials.username


@router.get('/', response_class=HTMLResponse)
async def dashboard(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        products_count = len((await session.execute(select(Product.id))).scalars().all())
        orders = (await session.execute(select(Order).order_by(desc(Order.created_at)).limit(10))).scalars().all()
        reservations = (await session.execute(select(Reservation).order_by(desc(Reservation.created_at)).limit(10))).scalars().all()
    return templates.TemplateResponse('dashboard.html', {'request': request, 'products_count': products_count, 'orders': orders, 'reservations': reservations, 'user': user})


@router.get('/products', response_class=HTMLResponse)
async def products_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        products = (await session.execute(select(Product).order_by(Product.id.desc()))).scalars().all()
    return templates.TemplateResponse('products.html', {'request': request, 'products': products, 'user': user})


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
    async with SessionLocal() as session:
        session.add(Product(
            name=name,
            sku=sku or None,
            price_partner=price_partner,
            price_regular=round(price_partner * settings.partner_price_multiplier, 2),
            quantity=quantity,
            pv=pv,
            category=category or None,
            aliases=aliases or None,
            description=description or None,
            stock_status='in_stock' if quantity > 5 else 'low' if quantity > 0 else 'out_of_stock',
        ))
        await session.commit()
    return RedirectResponse('/admin/products', status_code=303)


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
        orders = (await session.execute(select(Order).order_by(desc(Order.created_at)))).scalars().all()
    return templates.TemplateResponse('orders.html', {'request': request, 'orders': orders, 'user': user})


@router.get('/reservations', response_class=HTMLResponse)
async def reservations_page(request: Request, user: str = Depends(verify)):
    async with SessionLocal() as session:
        reservations = (await session.execute(select(Reservation).order_by(desc(Reservation.created_at)))).scalars().all()
    return templates.TemplateResponse('reservations.html', {'request': request, 'reservations': reservations, 'user': user})
