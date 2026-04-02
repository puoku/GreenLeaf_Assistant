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
