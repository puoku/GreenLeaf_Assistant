from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from aiogram import Bot
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select

from app.config import get_settings
from app.db.models import Customer, Order, OrderStatus, Product, Reservation, ReservationStatus, StockStatus
from app.db.session import SessionLocal
from app.services.product_search import search_products

settings = get_settings()
RESERVATION_LINE_RE = re.compile(
    r'^\s*(?:\d+\s*[\.)]\s*)?(?P<name>.+?)\s*(?:[-–—:]\s*)?(?P<qty>\d+)\s*(?:шт|штук|шт\.)\s*$',
    re.IGNORECASE,
)


@dataclass
class ParsedReservationItem:
    name: str
    quantity: int


@dataclass
class ReservationMatch:
    product_id: int
    requested_name: str
    product_name: str
    quantity: int


@dataclass
class ReservationAnalysis:
    matches: list[ReservationMatch]
    missing_items: list[str]


async def get_or_create_customer(user_id: int, username: str | None, full_name: str | None) -> Customer:
    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).where(Customer.telegram_user_id == user_id))).scalar_one_or_none()
        if customer is None:
            customer = Customer(telegram_user_id=user_id, username=username, full_name=full_name)
            session.add(customer)
            await session.commit()
            await session.refresh(customer)
            return customer
        customer.username = username
        customer.full_name = full_name
        await session.commit()
        await session.refresh(customer)
        return customer


async def create_order(
    user_id: int,
    username: str | None,
    full_name: str | None,
    items_text: str,
    delivery_type: str | None,
    address: str | None,
    comment: str | None,
    source_chat_id: int | None,
    source_thread_id: int | None,
    bot: Bot,
) -> Order:
    customer = await get_or_create_customer(user_id, username, full_name)
    async with SessionLocal() as session:
        order = Order(
            customer_id=customer.id,
            items_text=items_text,
            delivery_type=delivery_type,
            address=address,
            comment=comment,
            source_chat_id=source_chat_id,
            source_thread_id=source_thread_id,
            status=OrderStatus.new.value,
        )
        session.add(order)
        await session.commit()
        await session.refresh(order)

        return order


async def create_reservation(
    user_id: int,
    username: str | None,
    full_name: str | None,
    items_text: str,
    reserve_until: str | None,
    customer_name: str,
    customer_phone: str,
    source_chat_id: int | None,
    bot: Bot,
) -> Reservation:
    customer = await get_or_create_customer(user_id, username, full_name)
    customer.phone = customer_phone
    async with SessionLocal() as session:
        fresh_customer = (await session.execute(select(Customer).where(Customer.id == customer.id))).scalar_one()
        fresh_customer.phone = customer_phone
        reservation = Reservation(
            customer_id=customer.id,
            items_text=items_text,
            reserve_until=reserve_until,
            customer_name=customer_name,
            customer_phone=customer_phone,
            status=ReservationStatus.new.value,
        )
        session.add(reservation)
        await session.commit()
        await session.refresh(reservation)
        return reservation


def parse_reservation_items(text: str) -> list[ParsedReservationItem]:
    items: list[ParsedReservationItem] = []
    for raw_line in (text or '').splitlines():
        line = raw_line.strip()
        if not line:
            continue
        match = RESERVATION_LINE_RE.match(line)
        if not match:
            continue
        name = match.group('name').strip(' "\'«»')
        quantity = int(match.group('qty'))
        if not name or quantity <= 0:
            continue
        items.append(ParsedReservationItem(name=name, quantity=quantity))
    return items


def looks_like_reservation_text(text: str) -> bool:
    lowered = (text or '').lower()
    if any(word in lowered for word in {'бронь', 'забронировать', 'отложить'}):
        return True
    return len(parse_reservation_items(text)) > 0 and any(word in lowered for word in {'заказ', 'товар', 'нужно'})


def calc_stock_status(quantity: int) -> str:
    if quantity <= 0:
        return StockStatus.out_of_stock.value
    if quantity <= 5:
        return StockStatus.low.value
    return StockStatus.in_stock.value


async def reserve_products_for_text(session, raw_text: str) -> list[str]:
    parsed_items = parse_reservation_items(raw_text)
    reserved_lines: list[str] = []
    for item in parsed_items:
        result = await search_products(item.name, limit=1)
        if not result.products:
            continue

        product = (await session.execute(select(Product).where(Product.id == result.products[0].id))).scalar_one_or_none()
        if not product or product.quantity <= 0:
            continue

        reserved_qty = min(item.quantity, product.quantity)
        if reserved_qty <= 0:
            continue

        product.quantity -= reserved_qty
        product.stock_status = calc_stock_status(product.quantity)
        reserved_lines.append(f'{product.name} - {reserved_qty} шт')
    return reserved_lines


async def release_products_for_text(session, raw_text: str) -> None:
    parsed_items = parse_reservation_items(raw_text)
    for item in parsed_items:
        result = await search_products(item.name, limit=1)
        if not result.products:
            continue

        product = (await session.execute(select(Product).where(Product.id == result.products[0].id))).scalar_one_or_none()
        if not product:
            continue

        product.quantity += item.quantity
        product.stock_status = calc_stock_status(product.quantity)


async def reserve_order_products(session, raw_text: str) -> list[str]:
    return await reserve_products_for_text(session, raw_text)


async def create_reservation_from_text(
    user_id: int,
    username: str | None,
    full_name: str | None,
    raw_text: str,
    bot: Bot,
) -> Reservation | None:
    analysis = await analyze_reservation_text(raw_text)
    if not analysis or not analysis.matches or analysis.missing_items:
        return None

    return await create_reservation_from_matches(
        user_id=user_id,
        username=username,
        full_name=full_name,
        raw_text=raw_text,
        matches=analysis.matches,
        source_chat_id=None,
        bot=bot,
    )


async def analyze_reservation_text(raw_text: str) -> ReservationAnalysis | None:
    parsed_items = parse_reservation_items(raw_text)
    if not parsed_items:
        return None

    matches: list[ReservationMatch] = []
    missing_items: list[str] = []
    async with SessionLocal() as session:
        for item in parsed_items:
            result = await search_products(item.name, limit=1)
            if not result.products:
                missing_items.append(f'{item.name} - {item.quantity} шт')
                continue

            product = (await session.execute(select(Product).where(Product.id == result.products[0].id))).scalar_one_or_none()
            if not product or product.quantity < item.quantity:
                missing_items.append(f'{item.name} - {item.quantity} шт')
                continue

            matches.append(
                ReservationMatch(
                    product_id=product.id,
                    requested_name=item.name,
                    product_name=product.name,
                    quantity=item.quantity,
                )
            )

    if not matches and not missing_items:
        return None

    return ReservationAnalysis(matches=matches, missing_items=missing_items)


async def create_reservation_from_matches(
    user_id: int,
    username: str | None,
    full_name: str | None,
    raw_text: str,
    matches: list[ReservationMatch] | list[dict],
    bot: Bot,
    source_chat_id: int | None = None,
) -> Reservation | None:
    if not matches:
        return None

    customer = await get_or_create_customer(user_id, username, full_name)
    async with SessionLocal() as session:
        fresh_customer = (await session.execute(select(Customer).where(Customer.id == customer.id))).scalar_one()
        validated_matches: list[ReservationMatch | dict] = []
        for item in matches:
            product_id = item.product_id if isinstance(item, ReservationMatch) else item['product_id']
            quantity = item.quantity if isinstance(item, ReservationMatch) else item['quantity']
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
            if product and product.quantity >= quantity:
                validated_matches.append(item)

        if not validated_matches:
            return None

        reservation = Reservation(
            customer_id=customer.id,
            items_text=raw_text.strip(),
            reserve_until='до подтверждения менеджером',
            customer_name=fresh_customer.full_name or fresh_customer.username or 'Не указано',
            customer_phone=fresh_customer.phone or 'Не указан',
            status=ReservationStatus.new.value,
        )
        session.add(reservation)
        await session.commit()
        await session.refresh(reservation)

        return reservation


async def get_order(order_id: int) -> Order | None:
    async with SessionLocal() as session:
        return (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()


async def update_order_status(order_id: int, status: str) -> tuple[Order | None, Customer | None]:
    async with SessionLocal() as session:
        order = (await session.execute(select(Order).where(Order.id == order_id))).scalar_one_or_none()
        if not order:
            return None, None
        customer = (await session.execute(select(Customer).where(Customer.id == order.customer_id))).scalar_one_or_none()
        previous_status = order.status
        if status == OrderStatus.confirmed.value and previous_status not in {
            OrderStatus.confirmed.value,
            OrderStatus.ready.value,
            OrderStatus.completed.value,
        }:
            await reserve_order_products(session, order.items_text)
        if status == OrderStatus.canceled.value and previous_status in {
            OrderStatus.confirmed.value,
            OrderStatus.in_progress.value,
            OrderStatus.ready.value,
            OrderStatus.completed.value,
        }:
            await release_products_for_text(session, order.items_text)
        order.status = status
        await session.commit()
        return order, customer


async def update_reservation_status(reservation_id: int, status: str) -> tuple[Reservation | None, Customer | None]:
    async with SessionLocal() as session:
        reservation = (await session.execute(select(Reservation).where(Reservation.id == reservation_id))).scalar_one_or_none()
        if not reservation:
            return None, None
        customer = (await session.execute(select(Customer).where(Customer.id == reservation.customer_id))).scalar_one_or_none()
        previous_status = reservation.status
        if status == ReservationStatus.confirmed.value and previous_status != ReservationStatus.confirmed.value:
            reserved_lines = await reserve_products_for_text(session, reservation.items_text)
            if not reserved_lines:
                return None, customer
        reservation.status = status
        if status == ReservationStatus.canceled.value and previous_status == ReservationStatus.confirmed.value:
            await release_products_for_text(session, reservation.items_text)
        await session.commit()
        return reservation, customer


async def set_customer_handoff(telegram_user_id: int, value: bool) -> None:
    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).where(Customer.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if customer:
            customer.is_human_handoff = value
            await session.commit()


async def is_customer_in_handoff(telegram_user_id: int) -> bool:
    async with SessionLocal() as session:
        customer = (await session.execute(select(Customer).where(Customer.telegram_user_id == telegram_user_id))).scalar_one_or_none()
        if not customer or not customer.is_human_handoff:
            return False
        expires_at = customer.updated_at + timedelta(minutes=settings.human_handoff_minutes)
        if expires_at <= datetime.utcnow():
            customer.is_human_handoff = False
            await session.commit()
            return False
        return True


def render_order(order_id: int, customer: Customer, order: Order) -> str:
    return (
        f'<b>Новый заказ #{order_id}</b>\n'
        f'Клиент: {customer.full_name or "—"} (@{customer.username or "—"})\n'
        f'Telegram ID: <code>{customer.telegram_user_id}</code>\n'
        f'Товары: {order.items_text}\n'
        f'Получение: {order.delivery_type or "не указано"}\n'
        f'Адрес: {order.address or "—"}\n'
        f'Комментарий: {order.comment or "—"}\n'
        f'Статус: {order.status}'
    )


def render_reservation(reservation_id: int, customer: Customer, reservation: Reservation) -> str:
    return (
        f'<b>Новая бронь #{reservation_id}</b>\n'
        f'Клиент: {reservation.customer_name}\n'
        f'Телефон: {reservation.customer_phone}\n'
        f'Telegram: @{customer.username or "—"} / <code>{customer.telegram_user_id}</code>\n'
        f'Товары: {reservation.items_text}\n'
        f'Срок брони: {reservation.reserve_until or "24 часа"}\n'
        f'Статус: {reservation.status}'
    )


def render_customer_order_review(order: Order) -> str:
    return (
        f'<b>Проверьте заказ #{order.id}</b>\n'
        f'Товары: {order.items_text}\n'
        f'Получение: {order.delivery_type or "не указано"}\n'
        f'Адрес: {order.address or "—"}\n'
        f'Комментарий: {order.comment or "—"}\n\n'
        'Если всё верно, нажмите "Подтвердить". '
        'Это не подтверждение наличия товара, а только проверка, что бот правильно собрал вашу заявку.'
    )


def render_customer_reservation_review(reservation: Reservation) -> str:
    return (
        f'<b>Проверьте бронь #{reservation.id}</b>\n'
        f'Товары: {reservation.items_text}\n'
        f'Имя: {reservation.customer_name or "—"}\n'
        f'Телефон: {reservation.customer_phone or "—"}\n'
        f'Срок брони: {reservation.reserve_until or "24 часа"}\n\n'
        'Если всё верно, нажмите "Подтвердить". '
        'Это не резервирует товар автоматически, а только подтверждает, что заявка собрана верно.'
    )
