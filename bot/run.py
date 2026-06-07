import logger
import database as db
import bot
from config import BOT_TOKEN

import asyncio
from aiogram import Bot, Dispatcher
from loguru import logger

tg_bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


async def main():
    logger.info("Проверка и инициализация базы данных...")
    db.init_db()

    dp.include_router(bot.router)

    logger.success("🚀 Бэкэнд собран. Запускаем polling...")
    await dp.start_polling(tg_bot)


if __name__ == "__main__":
    asyncio.run(main())
