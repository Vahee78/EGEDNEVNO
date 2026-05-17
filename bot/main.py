import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
import database as db
import data_content as content

from handlers import menu, play

import sys
from loguru import logger

# Настраиваем логирование
logger.remove()  # Удаляем стандартный блеклый вывод

# 1. Красивый вывод в консоль (stdout)
logger.add(
    sys.stdout,
    format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
    level="DEBUG"
)

# 2. Запись в файл с авто-очисткой
logger.add(
    "logs/bot.log",
    rotation="5 MB",      # Как только файл разрастется до 5 МБ — создастся новый
    retention="10 days",  # Логи старше 10 дней будут сами удаляться
    level="INFO",         # В файл пишем только важные вещи, без дебага
    encoding="utf-8"
)

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


async def notification_loop():
    """Фоновая задача для рассылки уведомлений"""
    while True:
        now_utc = datetime.now(timezone.utc)
        users = db.get_all_users_for_notify()
        today_str = now_utc.now().strftime("%Y-%m-%d")

        for u_id, pl, tz, last_date in users:
            if pl != 'tg' or last_date == today_str:
                continue  # пропускаем не тг юзеров и тех, кто сегодня решал

            l_time = now_utc + timedelta(hours=tz)
            if any(l_time.hour == int(h) and 0 < l_time.minute - (30 if h % 1 != 0 else 0) < 10 for h in
                   content.NOTIFICATION_HOURS):
                try:
                    await bot.send_message(u_id, content.get_notification(l_time))
                    await asyncio.sleep(0.05)  # Защита от спам-блока Telegram
                except Exception as e:
                    if "bot was blocked by the user" not in e:
                        logger.warning(f"Ошибка отправки уведомления {u_id}: {e}")

        await asyncio.sleep(600)


async def main():
    db.init_db()
    logger.info("Бот запущен")

    # Подключаем модули (роутеры) к главному диспетчеру
    dp.include_router(menu.router)
    dp.include_router(play.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="🔄 Перезапустить"),
        BotCommand(command="menu", description="📋 Меню"),
        BotCommand(command="bot", description="📝 Решать"),
        BotCommand(command="settings", description="⚙️ Настройки")
    ])

    # Запускаем фоновую задачу уведомлений
    _ = asyncio.create_task(notification_loop())

    # Запускаем бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
