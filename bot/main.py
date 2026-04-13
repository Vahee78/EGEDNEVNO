import asyncio
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher
from aiogram.types import BotCommand

import config
import database as db
import data_content as content

# Импортируем наши роутеры из новых файлов
from handlers import menu, play

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()


async def notification_loop():
    """Фоновая задача для рассылки уведомлений"""
    while True:
        now_utc = datetime.now(timezone.utc)
        users = db.get_all_users_for_notify()
        today_str = now_utc.now().strftime("%Y-%m-%d")

        for u_id, pl, tz, last_date in users:
            if pl != 'tg': continue
            if last_date == today_str: continue

            l_time = now_utc + timedelta(hours=tz)
            if any(l_time.hour == int(h) and 0 < l_time.minute - (30 if h % 1 != 0 else 0) < 15 for h in
                   content.NOTIFICATION_HOURS):
                try:
                    await bot.send_message(u_id, content.get_notification(l_time))
                    await asyncio.sleep(0.05)  # Защита от спам-блока Telegram
                except Exception as e:
                    print(f"Ошибка отправки уведомления {u_id}: {e}")

        await asyncio.sleep(600)


async def main():
    db.init_db()
    print("Бот запущен.")

    # Подключаем модули (роутеры) к главному диспетчеру
    dp.include_router(menu.router)
    dp.include_router(play.router)

    await bot.set_my_commands([
        BotCommand(command="start", description="🔄 Перезапустить"),
        BotCommand(command="menu", description="📋 Меню"),
        BotCommand(command="bot", description="📝 Решать")
    ])

    # Запускаем фоновую задачу уведомлений
    _ = asyncio.create_task(notification_loop())

    # Запускаем бота
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
