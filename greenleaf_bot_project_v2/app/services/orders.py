from __future__ import annotations

import re
from dataclasses import dataclass

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

        if settings.manager_chat_id:
            text = render_order(order_id=order.id, customer=customer, order=order)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'order:confirm:{order.id}'),
                    InlineKeyboardButton(text='🛠 В работу', callback_data=f'order:progress:{order.id}'),
                ],
                [
                    InlineKeyboardButton(text='❌ Отменить', callback_data=f'order:cancel:{order.id}'),
                    InlineKeyboardButton(text='💬 Нужен менеджер', callback_data=f'order:handoff:{order.id}'),
                ]
            ])
            send_kwargs = {
                'chat_id': settings.manager_chat_id,
                'text': text,
                'reply_markup': kb,
            }
            if settings.manager_topic_id:
                send_kwargs['message_thread_id'] = settings.manager_topic_id

            msg = await bot.send_message(**send_kwargs)
            order.manager_message_id = msg.message_id
            await session.commit()
        return order


async def create_reservation(
    user_id: int,
    username: str | None,
    full_name: str | None,
    items_text: str,
    reserve_until: str | None,
    customer_name: str,
    customer_phone: str,
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
        if settings.manager_chat_id:
            text = render_reservation(reservation.id, fresh_customer, reservation)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text='✅ Подтвердить бронь', callback_data=f'reservation:confirm:{reservation.id}'),
                    InlineKeyboardButton(text='❌ Отменить бронь', callback_data=f'reservation:cancel:{reservation.id}'),
                ]
            ])
            send_kwargs = {
                'chat_id': settings.manager_chat_id,
                'text': text,
                'reply_markup': kb,
            }
            if settings.manager_topic_id:
                send_kwargs['message_thread_id'] = settings.manager_topic_id

            msg = await bot.send_message(**send_kwargs)
            reservation.manager_message_id = msg.message_id
            await session.commit()
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
) -> Reservation | None:
    if not matches:
        return None

    customer = await get_or_create_customer(user_id, username, full_name)
    async with SessionLocal() as session:
        fresh_customer = (await session.execute(select(Customer).where(Customer.id == customer.id))).scalar_one()
        reserved_lines: list[str] = []

        for item in matches:
            product_id = item.product_id if isinstance(item, ReservationMatch) else item['product_id']
            quantity = item.quantity if isinstance(item, ReservationMatch) else item['quantity']
            product = (await session.execute(select(Product).where(Product.id == product_id))).scalar_one_or_none()
            if not product or product.quantity < quantity:
                continue

            product.quantity -= quantity
            product.stock_status = calc_stock_status(product.quantity)
            reserved_lines.append(f'{product.name} - {quantity} шт')

        if not reserved_lines:
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

        if settings.manager_chat_id:
            text = render_reservation(reservation.id, fresh_customer, reservation)
            text += '\n\nПодтверждено ботом:\n' + '\n'.join(reserved_lines)
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [
                    InlineKeyboardButton(text='✅ Подтвердить бронь', callback_data=f'reservation:confirm:{reservation.id}'),
                    InlineKeyboardButton(text='❌ Отменить бронь', callback_data=f'reservation:cancel:{reservation.id}'),
                ]
            ])
            send_kwargs = {
                'chat_id': settings.manager_chat_id,
                'text': text,
                'reply_markup': kb,
            }
            if settings.manager_topic_id:
                send_kwargs['message_thread_id'] = settings.manager_topic_id

            msg = await bot.send_message(**send_kwargs)
            reservation.manager_message_id = msg.message_id
            await session.commit()

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
        order.status = status
        await session.commit()
        return order, customer


async def update_reservation_status(reservation_id: int, status: str) -> tuple[Reservation | None, Customer | None]:
    async with SessionLocal() as session:
        reservation = (await session.execute(select(Reservation).where(Reservation.id == reservation_id))).scalar_one_or_none()
        if not reservation:
            return None, None
        customer = (await session.execute(select(Customer).where(Customer.id == reservation.customer_id))).scalar_one_or_none()
        reservation.status = status
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
        return bool(customer and customer.is_human_handoff)


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
