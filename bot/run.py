import database as db
import bot
import notifications
from config import BOT_TOKEN
import my_logger

import asyncio
from aiogram import Bot, Dispatcher
from loguru import logger

tg_bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


async def main():
    my_logger.init_logger()

    logger.info("Проверка и инициализация базы данных...")
    db.init_db()

    dp.include_router(bot.router)

    logger.info("Регистрация фоновой задачи уведомлений...")
    _ = asyncio.create_task(notifications.loop(tg_bot))

    logger.success("🚀 Бот запущен")
    await dp.start_polling(tg_bot)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.warning("Бот был остановлен вручную.")
