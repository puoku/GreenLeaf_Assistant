from sqlalchemy import select

from app.config import get_settings
from app.db.base import Base
from app.db.models import FAQItem, Product
from app.db.session import SessionLocal, engine


PRODUCTS = [
    {'name': 'YIBEILE Детская зубная паста с тройным экстрактом фруктов', 'sku': '4439', 'price_partner': 273, 'pv': 1, 'quantity': 19, 'category': 'Гигиена', 'aliases': 'детская паста,yibeile,зубная паста,фруктовая паста'},
    {'name': 'CARICH Fragrant Pearl - Освежитель воздуха "Ароматная жемчужина"', 'sku': '4042', 'price_partner': 424, 'pv': 2, 'quantity': 9, 'category': 'Дом', 'aliases': 'освежитель воздуха,carich,ароматная жемчужина'},
    {'name': 'MARVISIA Очки', 'sku': '4506', 'price_partner': 1925, 'pv': 7, 'quantity': 38, 'category': 'Аксессуары', 'aliases': 'очки,marvisia'},
    {'name': 'BULE POINT детская зубная паста', 'sku': '4236', 'price_partner': 154, 'pv': 0.5, 'quantity': 41, 'category': 'Гигиена', 'aliases': 'bule point,детская паста,зубная паста'},
    {'name': 'Калиши ополаскиватель для полости рта 300мл', 'sku': '5744', 'price_partner': 318, 'pv': 0.2, 'quantity': 10, 'category': 'Гигиена', 'aliases': 'калиши,kali shi,ополаскиватель,полость рта'},
    {'name': 'YIBEILE Детская антибактериальная зубная щетка (2 шт. в упаковке)', 'sku': '5634', 'price_partner': 149, 'pv': 0.2, 'quantity': 45, 'category': 'Гигиена', 'aliases': 'зубная щетка,щетка детская,yibeile'},
    {'name': 'YIBEILE Детская зубная паста со вкусом апельсина', 'sku': '5841', 'price_partner': 153, 'pv': 0.2, 'quantity': 24, 'category': 'Гигиена', 'aliases': 'апельсиновая паста,детская паста,yibeile'},
    {'name': 'Nilrich зеленый чай с жасмином', 'sku': '3919', 'price_partner': 439, 'pv': 0.2, 'quantity': 16, 'category': 'Напитки', 'aliases': 'nilrich,зеленый чай,жасмин'},
    {'name': 'Nilrich черный чай с розой', 'sku': '5106', 'price_partner': 439, 'pv': 0.2, 'quantity': 18, 'category': 'Напитки', 'aliases': 'nilrich,черный чай,роза'},
    {'name': 'iLiFE Увлажняющий гель с алоэ вера 180 г', 'sku': '5528', 'price_partner': 385, 'pv': 0.7, 'quantity': 25, 'category': 'Уход', 'aliases': 'aloe vera,алоэ вера,гель,ilife'},
    {'name': 'iLiFE Розовый гель алоэ вера, 180г', 'sku': '4458', 'price_partner': 385, 'pv': 0.7, 'quantity': 56, 'category': 'Уход', 'aliases': 'розовый гель,алоэ вера,ilife'},
    {'name': 'CARICH Shea butter moisturizing soap - Увлажняющее мыло с маслом ши', 'sku': '5284', 'price_partner': 144, 'pv': 0.2, 'quantity': 121, 'category': 'Уход', 'aliases': 'мыло,масло ши,carich,shea butter'},
    {'name': 'Carich гель для фиксации волос 80мл', 'sku': '3876', 'price_partner': 182, 'pv': 0.1, 'quantity': 17, 'category': 'Уход', 'aliases': 'гель для волос,фиксация волос,carich'},
    {'name': 'iLiFE Подтягивающие детские трусики 28 шт. (размер XL)', 'sku': '5509', 'price_partner': 1165, 'pv': 0.5, 'quantity': 7, 'category': 'Дети', 'aliases': 'pull ups,xl,трусики,pampers,подгузники,ilife'},
    {'name': 'iLiFE Подтягивающие детские трусики 32 шт. (размер L)', 'sku': '4115', 'price_partner': 1209, 'pv': 0.5, 'quantity': 8, 'category': 'Дети', 'aliases': 'pull ups,l,трусики,pampers,подгузники,ilife'},
    {'name': 'iLiFE Подтягивающие детские трусики 38 шт. (размер M)', 'sku': '4262', 'price_partner': 1275, 'pv': 0.5, 'quantity': 8, 'category': 'Дети', 'aliases': 'pull ups,m,трусики,pampers,подгузники,ilife'},
    {'name': 'SEALUXE Твердые духи (Роза)', 'sku': '4922', 'price_partner': 439, 'pv': 0.5, 'quantity': 33, 'category': 'Парфюм', 'aliases': 'духи,роза,sealuxe,твердые духи'},
    {'name': 'CARICH Освежающий антибактериальный спрей 30 мл', 'sku': '4746', 'price_partner': 319, 'pv': 0.2, 'quantity': 92, 'category': 'Гигиена', 'aliases': 'спрей антибактериальный,освежающий спрей,carich'},
    {'name': 'Ультратонкая зубная нить Kali Shi Fresh Clean из полимера', 'sku': '4842', 'price_partner': 87, 'pv': 0.2, 'quantity': 7, 'category': 'Гигиена', 'aliases': 'зубная нить,kali shi,нить для зубов,fresh clean'},
]


def regular_price(price_partner: float) -> float:
    multiplier = get_settings().partner_price_multiplier
    return round(price_partner * multiplier, 2)


FAQS = [
    {
        'intent': 'address',
        'question_patterns': 'где вы находитесь;адрес;где находится сц;как добраться;сервисный центр',
        'answer_text': 'Мы находимся по адресу: Город Новосибирск, Советский район, Проспект Строителей 13.',
    },
    {
        'intent': 'schedule',
        'question_patterns': 'график;режим работы;работаете ли вы сегодня;со скольки;до скольки;когда открыты',
        'answer_text': 'График работы: вт-суб с 13:00 до 20:00. Воскресенье и понедельник — выходной.',
    },
    {
        'intent': 'delivery',
        'question_patterns': 'доставка;как доставляете;можно доставку',
        'answer_text': 'Доставка выполняется через OZON, WILDBERRIES и Яндекс Доставку. После принятия заказа детали доставки согласует менеджер.',
    },
    {
        'intent': 'payment',
        'question_patterns': 'оплата;как оплатить;способы оплаты',
        'answer_text': 'Оплатой занимается менеджер после сборки заказа и расчёта стоимости доставки.',
    },
    {
        'intent': 'guarantee',
        'question_patterns': 'гарантия;есть гарантия',
        'answer_text': 'На этот ассортимент отдельная гарантия не предусмотрена.',
    },
    {
        'intent': 'returns',
        'question_patterns': 'возврат;можно вернуть',
        'answer_text': 'Возврат не предусмотрен.',
    },
    {
        'intent': 'pickup',
        'question_patterns': 'самовывоз;можно забрать;заберу сам',
        'answer_text': 'Самовывоз есть. Можно заранее заказать товары, поставить их в бронь и затем приехать забрать.',
    },
    {
        'intent': 'contacts',
        'question_patterns': 'контакты;телефон;номер;как связаться',
        'answer_text': 'Контакт для связи: +79130615296, Жазгул.',
    },
]


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with SessionLocal() as session:
        products_count = len((await session.execute(select(Product.id))).scalars().all())
        if products_count == 0:
            for item in PRODUCTS:
                session.add(Product(**item, price_regular=regular_price(item['price_partner'])))

        faqs_count = len((await session.execute(select(FAQItem.id))).scalars().all())
        if faqs_count == 0:
            for item in FAQS:
                session.add(FAQItem(**item))

        await session.commit()
