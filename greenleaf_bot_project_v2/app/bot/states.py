from aiogram.fsm.state import State, StatesGroup


class OrderForm(StatesGroup):
    waiting_items = State()
    waiting_delivery_type = State()
    waiting_address = State()
    waiting_comment = State()


class ReservationForm(StatesGroup):
    waiting_items = State()
    waiting_name = State()
    waiting_phone = State()
    waiting_until = State()


class AutoReservationForm(StatesGroup):
    waiting_confirmation = State()
