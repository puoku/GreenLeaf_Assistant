from __future__ import annotations

from datetime import datetime
from enum import Enum

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class StockStatus(str, Enum):
    in_stock = 'in_stock'
    low = 'low'
    out_of_stock = 'out_of_stock'


class OrderStatus(str, Enum):
    new = 'NEW'
    confirmed = 'CONFIRMED'
    in_progress = 'IN_PROGRESS'
    ready = 'READY'
    completed = 'COMPLETED'
    canceled = 'CANCELED'


class ReservationStatus(str, Enum):
    new = 'NEW'
    confirmed = 'CONFIRMED'
    canceled = 'CANCELED'
    expired = 'EXPIRED'


class Product(Base):
    __tablename__ = 'products'
    __table_args__ = (
        Index('ix_products_active_name', 'is_active', 'name'),
        Index('ix_products_active_sku', 'is_active', 'sku'),
        Index('ix_products_active_category', 'is_active', 'category'),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    sku: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    price_partner: Mapped[float] = mapped_column(Float)
    price_regular: Mapped[float] = mapped_column(Float)
    pv: Mapped[float] = mapped_column(Float, default=0)
    quantity: Mapped[int] = mapped_column(Integer, default=0)
    stock_status: Mapped[str] = mapped_column(String(32), default=StockStatus.in_stock.value)
    category: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    aliases: Mapped[str | None] = mapped_column(Text, nullable=True)
    photo_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class FAQItem(Base):
    __tablename__ = 'faq_items'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    intent: Mapped[str] = mapped_column(String(64), index=True)
    question_patterns: Mapped[str] = mapped_column(Text)
    answer_text: Mapped[str] = mapped_column(Text)
    buttons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Customer(Base):
    __tablename__ = 'customers'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(128), nullable=True)
    full_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    is_human_handoff: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    orders: Mapped[list[Order]] = relationship(back_populates='customer')
    reservations: Mapped[list[Reservation]] = relationship(back_populates='customer')


class Order(Base):
    __tablename__ = 'orders'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'))
    source_chat_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    source_thread_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    delivery_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    payment_method: Mapped[str | None] = mapped_column(String(64), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    items_text: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32), default=OrderStatus.new.value)
    manager_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer: Mapped[Customer] = relationship(back_populates='orders')


class Reservation(Base):
    __tablename__ = 'reservations'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey('customers.id'))
    items_text: Mapped[str] = mapped_column(Text)
    reserve_until: Mapped[str | None] = mapped_column(String(64), nullable=True)
    customer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    customer_phone: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default=ReservationStatus.new.value)
    manager_message_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    customer: Mapped[Customer] = relationship(back_populates='reservations')


class ImportLog(Base):
    __tablename__ = 'import_logs'

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    filename: Mapped[str] = mapped_column(String(255), default='unknown.csv')
    mode: Mapped[str] = mapped_column(String(32), default='upsert', index=True)
    status: Mapped[str] = mapped_column(String(32), default='ok', index=True)
    added_count: Mapped[int] = mapped_column(Integer, default=0)
    updated_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    errors_preview: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    finished_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    duration_ms: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, index=True)
