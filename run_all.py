"""
Единый лаунчер — запускает FastAPI + Бот Кассира в одном процессе.
Запуск: venv\Scripts\python run_all.py
"""
import asyncio
import logging
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

import uvicorn
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode

from src.config import CASHIER_BOT_TOKEN

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger(__name__)


async def run_fastapi():
    """FastAPI webhook сервер на порту 8000."""
    config = uvicorn.Config(
        "src.main:app",
        host="0.0.0.0",
        port=8000,
        log_level="info",
        reload=False,
    )
    server = uvicorn.Server(config)
    await server.serve()


async def run_cashier_bot():
    """Бот кассира — Telegram polling."""
    from cashier_bot.main import router as cashier_router
    bot = Bot(token=CASHIER_BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(cashier_router)
    logger.info("✅ Cashier bot started")
    await bot.delete_webhook(drop_pending_updates=True)
    await dp.start_polling(bot)


async def main():
    logger.info("🚀 Запуск всех сервисов...")
    await asyncio.gather(
        run_fastapi(),
        run_cashier_bot(),
    )


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Все сервисы остановлены.")
