# GreenLeaf Assistant

Telegram-бот для магазина.

Что умеет сейчас:
- отвечает на FAQ: адрес, график, доставка, оплата, гарантия, возврат, самовывоз, контакты
- ищет товары по каталогу
- показывает цену и остаток товара
- принимает заказ
- принимает бронь
- умеет принять бронь из обычного сообщения со списком товаров
- если части товаров нет, предлагает оформить бронь без них
- отправляет заказы и брони в чат менеджеров
- даёт менеджеру кнопки подтверждения и отмены
- удаляет подозрительные ссылки от не-админов
- открывает простую веб-админку

Проект находится в папке `greenleaf_bot_project_v2`.

## Запуск

```bash
cd greenleaf_bot_project_v2
cp .env.example .env
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m app.main
```

После запуска:
- бот работает в Telegram
- админка доступна на `http://localhost:8000/admin`
- проверка сервиса: `http://localhost:8000/health`

## Что заполнить в `.env`

Минимально:

```env
BOT_TOKEN=your_bot_token
BOT_USERNAME=GreenLeafBot
OPENAI_API_KEY=your_openrouter_api_key
OPENAI_MODEL=openai/gpt-4o-mini
MANAGER_CHAT_ID=-1000000000000
ADMIN_USERNAME=admin
ADMIN_PASSWORD=change-me
```
