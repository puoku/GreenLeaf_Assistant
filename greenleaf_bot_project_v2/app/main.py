from __future__ import annotations

import asyncio
import contextlib
import logging

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI

from app.bot.handlers import router as bot_router
from app.config import get_settings
from app.db.init_db import init_db
from app.web_admin import router as admin_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

settings = get_settings()

bot = Bot(settings.bot_token, default=DefaultBotProperties(parse_mode='HTML'))
dp = Dispatcher()
dp.include_router(bot_router)


async def start_polling() -> None:
    try:
        logger.info("Starting bot polling...")
        await dp.start_polling(bot)
    except asyncio.CancelledError:
        logger.info("Polling task cancelled")
        raise
    except Exception:
        logger.exception("Polling crashed with an exception")
        raise
    finally:
        logger.info("Polling task finished")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    polling_task = asyncio.create_task(start_polling())

    try:
        yield
    finally:
        if not polling_task.done():
            polling_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await polling_task
        else:
            with contextlib.suppress(Exception):
                await polling_task

        await bot.session.close()


app = FastAPI(title='GreenLeaf Assistant', lifespan=lifespan)
app.include_router(admin_router, prefix='/admin', tags=['admin'])


@app.get('/health')
async def health() -> dict:
    return {'ok': True}


if __name__ == '__main__':
    uvicorn.run(app, host=settings.webapp_host, port=settings.webapp_port, reload=False)
