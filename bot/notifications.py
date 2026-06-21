import asyncio
from datetime import datetime, timedelta, timezone
from aiogram.exceptions import TelegramForbiddenError

import database as db
import data_content as content

from loguru import logger


async def loop(bot):
    """Фоновая задача для рассылки уведомлений"""
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            users = db.get_all_users_for_notify()
            today_str = now_utc.strftime("%Y-%m-%d")

            for u_id, pl, tz, last_date in users:
                if pl != 'tg' or last_date == today_str:
                    continue  # Пропускаем не тг юзеров и тех, кто сегодня решал

                l_time = now_utc + timedelta(hours=tz)
                # Проверяем, пора ли отправлять
                if any(l_time.hour == int(h) and 0 < l_time.minute - (30 if h % 1 != 0 else 0) < 10 for h in content.NOTIFICATION_HOURS):
                    try:
                        await bot.send_message(u_id, content.get_notification(l_time))
                        logger.info(f"Уведомление отправлено пользователю {u_id}")
                        await asyncio.sleep(0.05)  # Защита от спам-блока Telegram
                    except TelegramForbiddenError:
                        logger.warning(f"Пользователь {u_id} заблокировал бота. Отключаем рассылку.")
                        user = db.get_user_data(u_id, platform="tg")
                        user["notifications_enabled"] = 0
                        db.update_user_data(u_id, user)

                    except Exception as e:
                        logger.error(f"Ошибка отправки уведомления {u_id}: {e}")

        except Exception as loop_error:
            logger.error(f"Ошибка в цикле уведомлений: {loop_error}")

        await asyncio.sleep(600)  # Проверка раз в 10 минут
