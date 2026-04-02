from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import product_actions, simple_manager_button
from app.bot.states import AutoReservationForm, OrderForm, ReservationForm
from app.config import get_settings
from app.db.models import OrderStatus, ReservationStatus
from app.services.faq import find_faq_answer, get_faq_by_intent
from app.services.link_moderation import is_suspicious_link
from app.services.llm import classify_message
from app.services.orders import (
    analyze_reservation_text,
    create_order,
    create_reservation,
    create_reservation_from_matches,
    is_customer_in_handoff,
    looks_like_reservation_text,
    set_customer_handoff,
    update_order_status,
    update_reservation_status,
)
from app.services.product_search import format_product_card, looks_like_product_question, search_products

router = Router()
settings = get_settings()

ORDER_ACK = (
    'Спасибо за заказ! Корректируем данные со складом. '
    f'В течение {settings.manager_response_minutes} минут '
    '(если это в рабочее время СЦ) вам напишет наш сотрудник.'
)


@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        'Здравствуйте! Я могу подсказать по наличию, цене, адресу, графику работы, а также принять заказ или бронь. '
        'Напишите вопрос обычным сообщением.'
    )


@router.callback_query(F.data == 'need_manager')
async def need_manager(callback: CallbackQuery):
    await set_customer_handoff(callback.from_user.id, True)
    await callback.message.answer('Передаю диалог менеджеру. Он ответит вам в ближайшее время.')
    await callback.answer()


@router.callback_query(F.data.startswith('order_start:'))
async def order_start(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split(':', 1)[1]
    await state.set_state(OrderForm.waiting_items)
    await state.update_data(prefill_product_id=product_id)
    await callback.message.answer('Напишите список товаров и количество. Например: паста YIBEILE — 2 шт, зубная нить — 1 шт.')
    await callback.answer()


@router.callback_query(F.data.startswith('reserve_start:'))
async def reserve_start(callback: CallbackQuery, state: FSMContext):
    product_id = callback.data.split(':', 1)[1]
    await state.set_state(ReservationForm.waiting_items)
    await state.update_data(prefill_product_id=product_id)
    await callback.message.answer('Напишите, какие товары нужно поставить в бронь и в каком количестве.')
    await callback.answer()


@router.message(OrderForm.waiting_items)
async def order_items(message: Message, state: FSMContext):
    await state.update_data(items_text=message.text)
    await state.set_state(OrderForm.waiting_delivery_type)
    await message.answer('Как хотите получить заказ? Напишите: самовывоз или доставка.')


@router.message(OrderForm.waiting_delivery_type)
async def order_delivery(message: Message, state: FSMContext):
    await state.update_data(delivery_type=message.text)
    if 'достав' in message.text.lower():
        await state.set_state(OrderForm.waiting_address)
        await message.answer('Напишите адрес доставки.')
    else:
        await state.set_state(OrderForm.waiting_comment)
        await message.answer('Добавьте комментарий к заказу или напишите "нет".')


@router.message(OrderForm.waiting_address)
async def order_address(message: Message, state: FSMContext):
    await state.update_data(address=message.text)
    await state.set_state(OrderForm.waiting_comment)
    await message.answer('Добавьте комментарий к заказу или напишите "нет".')


@router.message(OrderForm.waiting_comment)
async def order_comment(message: Message, state: FSMContext):
    data = await state.get_data()
    await create_order(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        items_text=data.get('items_text', ''),
        delivery_type=data.get('delivery_type'),
        address=data.get('address'),
        comment=None if message.text.lower() == 'нет' else message.text,
        source_chat_id=message.chat.id,
        source_thread_id=message.message_thread_id,
        bot=message.bot,
    )
    await state.clear()
    await message.answer(ORDER_ACK)


@router.message(ReservationForm.waiting_items)
async def reservation_items(message: Message, state: FSMContext):
    await state.update_data(items_text=message.text)
    await state.set_state(ReservationForm.waiting_name)
    await message.answer('Напишите имя для брони.')


@router.message(ReservationForm.waiting_name)
async def reservation_name(message: Message, state: FSMContext):
    await state.update_data(customer_name=message.text)
    await state.set_state(ReservationForm.waiting_phone)
    await message.answer('Напишите телефон для брони.')


@router.message(ReservationForm.waiting_phone)
async def reservation_phone(message: Message, state: FSMContext):
    await state.update_data(customer_phone=message.text)
    await state.set_state(ReservationForm.waiting_until)
    await message.answer('На какой срок поставить бронь? Например: до завтра 18:00. Если не важно, напишите "24 часа".')


@router.message(ReservationForm.waiting_until)
async def reservation_until(message: Message, state: FSMContext):
    data = await state.get_data()
    await create_reservation(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        items_text=data.get('items_text', ''),
        reserve_until=message.text,
        customer_name=data.get('customer_name', ''),
        customer_phone=data.get('customer_phone', ''),
        bot=message.bot,
    )
    await state.clear()
    await message.answer('Спасибо! Бронь передана сотруднику. После сверки по складу вам напишут отдельно.')


@router.message(AutoReservationForm.waiting_confirmation)
async def auto_reservation_confirmation(message: Message, state: FSMContext):
    answer = (message.text or '').strip().lower()
    if answer not in {'да', 'нет', 'yes', 'no'}:
        await message.answer('Ответьте, пожалуйста: да или нет.')
        return

    if answer in {'нет', 'no'}:
        await state.clear()
        await message.answer('Хорошо, заявку не оформляю.')
        return

    data = await state.get_data()
    reservation = await create_reservation_from_matches(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        raw_text=data.get('pending_reservation_raw_text', ''),
        matches=data.get('pending_reservation_matches', []),
        bot=message.bot,
    )
    await state.clear()
    if reservation:
        await message.answer('Ваша заявка принята!')
        return
    await message.answer('Не удалось оформить бронь. Пожалуйста, уточните список товаров или напишите менеджеру.')


@router.callback_query(F.data.startswith('order:'))
async def order_actions(callback: CallbackQuery):
    _, action, order_id_str = callback.data.split(':')
    order_id = int(order_id_str)
    mapping = {
        'confirm': OrderStatus.confirmed.value,
        'progress': OrderStatus.in_progress.value,
        'cancel': OrderStatus.canceled.value,
        'handoff': OrderStatus.in_progress.value,
    }
    order, customer = await update_order_status(order_id, mapping[action])
    if not order or not customer:
        await callback.answer('Заказ не найден', show_alert=True)
        return

    text_map = {
        'confirm': f'Ваш заказ #{order.id} подтверждён менеджером.',
        'progress': f'Ваш заказ #{order.id} взят в работу менеджером.',
        'cancel': f'Ваш заказ #{order.id} отменён. Для уточнения деталей с вами свяжется менеджер.',
        'handoff': 'Менеджер подключился к вашему заказу и ответит вам в ближайшее время.',
    }
    await callback.bot.send_message(customer.telegram_user_id, text_map[action])
    if action == 'handoff':
        await set_customer_handoff(customer.telegram_user_id, True)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer('Готово')


@router.callback_query(F.data.startswith('reservation:'))
async def reservation_actions(callback: CallbackQuery):
    _, action, reservation_id_str = callback.data.split(':')
    reservation_id = int(reservation_id_str)
    status = ReservationStatus.confirmed.value if action == 'confirm' else ReservationStatus.canceled.value
    reservation, customer = await update_reservation_status(reservation_id, status)
    if not reservation or not customer:
        await callback.answer('Бронь не найдена', show_alert=True)
        return
    text = (
        f'Ваша бронь #{reservation.id} подтверждена. '
        if action == 'confirm'
        else f'Бронь #{reservation.id} отменена. Для уточнения деталей вам напишет менеджер.'
    )
    await callback.bot.send_message(customer.telegram_user_id, text)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer('Готово')


@router.message(F.text)
async def universal_text_handler(message: Message, state: FSMContext):
    if await state.get_state():
        return

    if message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}:
        member = await message.bot.get_chat_member(message.chat.id, message.from_user.id)
        is_admin = member.status in {'administrator', 'creator'}
        if not is_admin and is_suspicious_link(message.text or '', settings.bot_username):
            try:
                await message.delete()
                await message.answer('Подозрительная ссылка удалена. Если это ошибка — напишите менеджеру.')
            except Exception:
                pass
            return

    if await is_customer_in_handoff(message.from_user.id):
        return

    text = (message.text or '').strip()
    if not text:
        return

    lowered = text.lower()
    if any(trigger in lowered for trigger in ['менеджер', 'оператор', 'живой человек', 'жалоба', 'не работает', 'хочу вернуть']):
        await set_customer_handoff(message.from_user.id, True)
        await message.answer('Передаю диалог менеджеру. Он ответит вам в ближайшее время.')
        return

    faq_item, _ = await find_faq_answer(text)
    if faq_item:
        await message.answer(faq_item.answer_text, reply_markup=simple_manager_button())
        return

    reservation_by_format = looks_like_reservation_text(text)

    ai_result = await classify_message(text)
    if ai_result and ai_result.get('faq_intent'):
        faq_item = await get_faq_by_intent(ai_result['faq_intent'])
        if faq_item:
            await message.answer(faq_item.answer_text, reply_markup=simple_manager_button())
            return

    if reservation_by_format or (ai_result and ai_result.get('intent') == 'reservation'):
        analysis = await analyze_reservation_text(text)
        if analysis and analysis.matches and not analysis.missing_items:
            reservation = await create_reservation_from_matches(
                user_id=message.from_user.id,
                username=message.from_user.username,
                full_name=message.from_user.full_name,
                raw_text=text,
                matches=[
                    {
                        'product_id': item.product_id,
                        'requested_name': item.requested_name,
                        'product_name': item.product_name,
                        'quantity': item.quantity,
                    }
                    for item in analysis.matches
                ],
                bot=message.bot,
            )
            if reservation:
                await message.answer('Ваша заявка принята!')
                return

        if analysis and analysis.matches and analysis.missing_items:
            await state.set_state(AutoReservationForm.waiting_confirmation)
            await state.update_data(
                pending_reservation_raw_text=text,
                pending_reservation_matches=[
                    {
                        'product_id': item.product_id,
                        'requested_name': item.requested_name,
                        'product_name': item.product_name,
                        'quantity': item.quantity,
                    }
                    for item in analysis.matches
                ],
            )
            missing_lines = '\n'.join(f'- {item}' for item in analysis.missing_items)
            await message.answer(
                'Такие товары отсутствуют или их недостаточно:\n'
                f'{missing_lines}\n\n'
                'Вы согласны оформить бронь без них? Ответьте: да или нет.'
            )
            return

        if analysis and analysis.missing_items and not analysis.matches:
            missing_lines = '\n'.join(f'- {item}' for item in analysis.missing_items)
            await message.answer(
                'Сейчас такие товары отсутствуют или их недостаточно:\n'
                f'{missing_lines}\n\n'
                'Если хотите, я могу передать ваш запрос менеджеру.'
            )
            await set_customer_handoff(message.from_user.id, True)
            return

    if looks_like_product_question(text) or (ai_result and ai_result.get('intent') == 'product_search'):
        query = ai_result.get('product_query') if ai_result and ai_result.get('product_query') else text
        result = await search_products(query)
        if len(result.products) == 1:
            product = result.products[0]
            await message.answer(format_product_card(product), reply_markup=product_actions(product.id))
            return
        if 2 <= len(result.products) <= 5:
            lines = ['Нашёл несколько вариантов:']
            for idx, product in enumerate(result.products, start=1):
                lines.append(f'{idx}. {html.escape(product.name)} — {int(product.price_partner)} ₽, остаток {product.quantity} шт.')
            lines.append('\nНапишите точнее название или нажмите кнопку ниже, чтобы подключить менеджера.')
            await message.answer('\n'.join(lines), reply_markup=simple_manager_button())
            return
        await message.answer('Пока не нашёл точного совпадения. Менеджер ответит вам в ближайшее время.')
        await set_customer_handoff(message.from_user.id, True)
        return

    if any(k in lowered for k in ['заказать', 'оформить заказ', 'хочу купить']):
        await state.set_state(OrderForm.waiting_items)
        await message.answer('Напишите список товаров и количество. Например: паста YIBEILE — 2 шт, гель алоэ — 1 шт.')
        return

    if any(k in lowered for k in ['отложить', 'бронь', 'забронировать']):
        await state.set_state(ReservationForm.waiting_items)
        await message.answer('Напишите товары и количество, которые нужно поставить в бронь.')
        return

    await message.answer('Менеджер ответит вам в ближайшее время.')
    await set_customer_handoff(message.from_user.id, True)
