from __future__ import annotations

import html

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

from app.bot.keyboards import (
    customer_order_review_actions,
    customer_reservation_review_actions,
    product_actions,
    simple_manager_button,
)
from app.bot.states import AutoReservationForm, OrderForm, ProductSelectionForm, ReservationForm
from app.config import get_settings
from app.db.models import OrderStatus, ReservationStatus
from app.services.faq import find_faq_answer, get_faq_by_intent
from app.services.llm import classify_message
from app.services.orders import (
    analyze_reservation_text,
    create_order,
    create_reservation,
    create_reservation_from_matches,
    is_customer_in_handoff,
    looks_like_reservation_text,
    render_customer_order_review,
    render_customer_reservation_review,
    set_customer_handoff,
    update_order_status,
    update_reservation_status,
)
from app.services.product_search import (
    find_direct_product_match,
    format_product_card,
    get_product_by_id,
    looks_like_product_question,
    search_products,
)

router = Router()
settings = get_settings()

ORDER_ACK = (
    'Спасибо за заказ! Корректируем данные со складом. '
    f'В течение {settings.manager_response_minutes} минут '
    '(если это в рабочее время СЦ) вам напишет наш сотрудник.'
)

ORDER_START_TRIGGERS = ['заказать', 'оформить заказ', 'хочу купить', 'сделать заказ', 'хочу заказ']
RESERVATION_START_TRIGGERS = ['отложить', 'бронь', 'забронировать']


async def can_use_manager_actions(callback: CallbackQuery) -> bool:
    manager_chat_id = settings.manager_chat_id
    if not manager_chat_id or not callback.message:
        return False
    if callback.message.chat.id != manager_chat_id:
        return False
    if callback.message.chat.type == ChatType.PRIVATE:
        return callback.from_user.id == manager_chat_id

    member = await callback.bot.get_chat_member(manager_chat_id, callback.from_user.id)
    return member.status in {'administrator', 'creator'}


async def send_customer_review(message: Message, text: str, reply_markup) -> None:
    if message.chat.type == ChatType.PRIVATE:
        await message.answer(text, reply_markup=reply_markup)
        return
    try:
        await message.bot.send_message(message.from_user.id, text, reply_markup=reply_markup)
        await message.answer('Я отправил подтверждение заявки вам в личные сообщения.')
    except (TelegramForbiddenError, TelegramBadRequest):
        await message.answer(
            'Не смог написать вам в личные сообщения. Откройте чат с ботом и нажмите /start, потом повторите заказ.'
        )


@router.message(CommandStart())
async def start_handler(message: Message):
    await message.answer(
        'Здравствуйте! Я могу подсказать по наличию, цене, адресу, графику работы, а также принять заказ или бронь. '
        'Напишите вопрос обычным сообщением.'
    )


@router.callback_query(F.data == 'need_manager')
async def need_manager(callback: CallbackQuery):
    if callback.message.chat.type == ChatType.PRIVATE:
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
    lowered = (message.text or '').lower()
    if any(trigger in lowered for trigger in ORDER_START_TRIGGERS):
        await message.answer('Напишите именно список товаров и количество. Например: спрей 2 шт, гель алоэ 1 шт.')
        return
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
    order = await create_order(
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
    await send_customer_review(message, render_customer_order_review(order), customer_order_review_actions(order.id))


@router.message(ReservationForm.waiting_items)
async def reservation_items(message: Message, state: FSMContext):
    lowered = (message.text or '').lower()
    if any(trigger in lowered for trigger in RESERVATION_START_TRIGGERS + ORDER_START_TRIGGERS):
        await message.answer('Напишите именно товары и количество для брони. Например: спрей 2 шт, гель 1 шт.')
        return
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
    reservation = await create_reservation(
        user_id=message.from_user.id,
        username=message.from_user.username,
        full_name=message.from_user.full_name,
        items_text=data.get('items_text', ''),
        reserve_until=message.text,
        customer_name=data.get('customer_name', ''),
        customer_phone=data.get('customer_phone', ''),
        source_chat_id=message.chat.id,
        bot=message.bot,
    )
    await state.clear()
    await message.answer('Спасибо! Бронь передана сотруднику. После сверки по складу вам напишут отдельно.')
    await send_customer_review(message, render_customer_reservation_review(reservation), customer_reservation_review_actions(reservation.id))


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
        source_chat_id=message.chat.id,
        bot=message.bot,
    )
    await state.clear()
    if reservation:
        await message.answer('Ваша заявка принята!')
        await send_customer_review(
            message,
            render_customer_reservation_review(reservation),
            customer_reservation_review_actions(reservation.id),
        )
        return
    await message.answer('Не удалось оформить бронь. Пожалуйста, уточните список товаров или напишите менеджеру.')


@router.message(ProductSelectionForm.waiting_choice)
async def product_selection_choice(message: Message, state: FSMContext):
    text = (message.text or '').strip()
    data = await state.get_data()
    options = data.get('product_options', [])
    if not options:
        await state.clear()
        await message.answer('Список вариантов устарел. Напишите запрос ещё раз.')
        return

    selected_product = None
    if text.isdigit():
        index = int(text) - 1
        if 0 <= index < len(options):
            selected_product = await get_product_by_id(options[index]['id'])
        else:
            await message.answer(f'Введите номер от 1 до {len(options)}.')
            return
    else:
        lowered = text.lower()
        for option in options:
            if lowered in option['name'].lower() or option['name'].lower() in lowered:
                selected_product = await get_product_by_id(option['id'])
                break
        if not selected_product:
            direct_product = await find_direct_product_match(text)
            if direct_product:
                await state.clear()
                await message.answer(format_product_card(direct_product), reply_markup=product_actions(direct_product.id))
                return

            if looks_like_product_question(text):
                await state.clear()
                result = await search_products(text)
                if len(result.products) == 1:
                    product = result.products[0]
                    await message.answer(format_product_card(product), reply_markup=product_actions(product.id))
                    return
                if 2 <= len(result.products) <= 5:
                    await state.set_state(ProductSelectionForm.waiting_choice)
                    await state.update_data(
                        product_options=[{'id': product.id, 'name': product.name} for product in result.products]
                    )
                    lines = ['Нашёл несколько вариантов:']
                    for idx, product in enumerate(result.products, start=1):
                        lines.append(f'{idx}. {html.escape(product.name)} — {int(product.price_partner)} ₽, остаток {product.quantity} шт.')
                    lines.append('\nНапишите номер варианта, точное название или нажмите кнопку ниже, чтобы подключить менеджера.')
                    await message.answer('\n'.join(lines), reply_markup=simple_manager_button())
                    return
            await message.answer('Напишите номер варианта или более точное название товара.')
            return

    await state.clear()
    if not selected_product:
        await message.answer('Не удалось найти выбранный товар. Попробуйте ещё раз.')
        return
    await message.answer(format_product_card(selected_product), reply_markup=product_actions(selected_product.id))


@router.callback_query(F.data.startswith('order:'))
async def order_actions(callback: CallbackQuery):
    if not await can_use_manager_actions(callback):
        await callback.answer('Эта кнопка доступна только менеджеру.', show_alert=True)
        return
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
    if action == 'handoff' and (order.source_chat_id is None or order.source_chat_id > 0):
        await set_customer_handoff(customer.telegram_user_id, True)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.answer('Готово')


@router.callback_query(F.data.startswith('reservation:'))
async def reservation_actions(callback: CallbackQuery):
    if not await can_use_manager_actions(callback):
        await callback.answer('Эта кнопка доступна только менеджеру.', show_alert=True)
        return
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


@router.callback_query(F.data.startswith('client_order:'))
async def client_order_actions(callback: CallbackQuery):
    _, action, order_id_str = callback.data.split(':')
    order_id = int(order_id_str)

    if action == 'confirm':
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer('Спасибо! Заявка сохранена. Менеджер проверит наличие товара в админке.')
        await callback.answer('Заявка подтверждена')
        return

    if action == 'cancel':
        await update_order_status(order_id, OrderStatus.canceled.value)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer('Хорошо, эту заявку не учитываем. Пришлите новый заказ, если нужно.')
        await callback.answer('Заявка отклонена')
        return

    await set_customer_handoff(callback.from_user.id, True)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Передаю заявку менеджеру. Он проверит её в админке и свяжется с вами.')
    await callback.answer('Менеджер подключён')


@router.callback_query(F.data.startswith('client_reservation:'))
async def client_reservation_actions(callback: CallbackQuery):
    _, action, reservation_id_str = callback.data.split(':')
    reservation_id = int(reservation_id_str)

    if action == 'confirm':
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer('Спасибо! Бронь сохранена. Менеджер проверит наличие товара в админке.')
        await callback.answer('Бронь подтверждена')
        return

    if action == 'cancel':
        await update_reservation_status(reservation_id, ReservationStatus.canceled.value)
        await callback.message.edit_reply_markup(reply_markup=None)
        await callback.message.answer('Хорошо, эту бронь не учитываем. Пришлите новую заявку, если нужно.')
        await callback.answer('Бронь отклонена')
        return

    await set_customer_handoff(callback.from_user.id, True)
    await callback.message.edit_reply_markup(reply_markup=None)
    await callback.message.answer('Передаю бронь менеджеру. Он проверит её в админке и свяжется с вами.')
    await callback.answer('Менеджер подключён')


@router.message(F.text)
async def universal_text_handler(message: Message, state: FSMContext):
    if await state.get_state():
        return

    is_group_chat = message.chat.type in {ChatType.GROUP, ChatType.SUPERGROUP}

    text = (message.text or '').strip()
    if not text:
        return

    lowered = text.lower()
    handoff_active = await is_customer_in_handoff(message.from_user.id) if not is_group_chat else False

    if any(k in lowered for k in ORDER_START_TRIGGERS):
        await state.set_state(OrderForm.waiting_items)
        await message.answer('Напишите список товаров и количество. Например: паста YIBEILE — 2 шт, гель алоэ — 1 шт.')
        return

    if any(k in lowered for k in RESERVATION_START_TRIGGERS):
        await state.set_state(ReservationForm.waiting_items)
        await message.answer('Напишите товары и количество, которые нужно поставить в бронь.')
        return

    if any(trigger in lowered for trigger in ['менеджер', 'оператор', 'живой человек', 'жалоба', 'не работает', 'хочу вернуть']):
        if not is_group_chat:
            await set_customer_handoff(message.from_user.id, True)
        await message.answer('Передаю диалог менеджеру. Он ответит вам в ближайшее время.')
        return

    direct_product = await find_direct_product_match(text)
    if direct_product:
        await message.answer(format_product_card(direct_product), reply_markup=product_actions(direct_product.id))
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

    if ai_result and ai_result.get('intent') == 'order':
        await state.set_state(OrderForm.waiting_items)
        await message.answer('Напишите список товаров и количество. Например: паста YIBEILE — 2 шт, гель алоэ — 1 шт.')
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
                source_chat_id=message.chat.id,
                bot=message.bot,
            )
            if reservation:
                await message.answer('Ваша заявка принята!')
                await message.answer(
                    render_customer_reservation_review(reservation),
                    reply_markup=customer_reservation_review_actions(reservation.id),
                )
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
            if not is_group_chat:
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
            await state.set_state(ProductSelectionForm.waiting_choice)
            await state.update_data(
                product_options=[{'id': product.id, 'name': product.name} for product in result.products]
            )
            lines = ['Нашёл несколько вариантов:']
            for idx, product in enumerate(result.products, start=1):
                lines.append(f'{idx}. {html.escape(product.name)} — {int(product.price_partner)} ₽, остаток {product.quantity} шт.')
            lines.append('\nНапишите номер варианта, точное название или нажмите кнопку ниже, чтобы подключить менеджера.')
            await message.answer('\n'.join(lines), reply_markup=simple_manager_button())
            return
        await message.answer('Пока не нашёл точного совпадения. Уточните название товара или позовите менеджера кнопкой ниже.', reply_markup=simple_manager_button())
        return

    if handoff_active:
        await message.answer('Менеджер уже подключён к вашему диалогу. Если вопрос срочный, напишите подробнее или дождитесь ответа.')
        return

    await message.answer('Не до конца понял запрос. Могу помочь с FAQ, поиском товара, заказом или бронью. Если нужно, позовите менеджера кнопкой ниже.', reply_markup=simple_manager_button())
