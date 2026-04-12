from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def product_actions(product_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='🛒 Заказать', callback_data=f'order_start:{product_id}'),
                InlineKeyboardButton(text='📦 Отложить', callback_data=f'reserve_start:{product_id}'),
            ],
            [InlineKeyboardButton(text='👤 Позвать менеджера', callback_data='need_manager')],
        ]
    )


def simple_manager_button() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text='👤 Позвать менеджера', callback_data='need_manager')]]
    )


def customer_order_review_actions(order_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'client_order:confirm:{order_id}'),
                InlineKeyboardButton(text='❌ Отклонить', callback_data=f'client_order:cancel:{order_id}'),
            ],
            [InlineKeyboardButton(text='👤 Нужен менеджер', callback_data=f'client_order:manager:{order_id}')],
        ]
    )


def customer_reservation_review_actions(reservation_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text='✅ Подтвердить', callback_data=f'client_reservation:confirm:{reservation_id}'),
                InlineKeyboardButton(text='❌ Отклонить', callback_data=f'client_reservation:cancel:{reservation_id}'),
            ],
            [InlineKeyboardButton(text='👤 Нужен менеджер', callback_data=f'client_reservation:manager:{reservation_id}')],
        ]
    )
