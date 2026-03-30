import time
import threading
import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import json
import re
import requests
from datetime import datetime, timedelta, timezone

import config
import database as db
import data_content as content
import engine
from handlers.utils import get_random_task

# Инициализация ВК
vk_session = vk_api.VkApi(token=config.VK_TOKEN)
vk = vk_session.get_api()

try:
    longpoll = VkBotLongPoll(vk_session, config.VK_GROUP_ID)
except Exception as e:
    print(f"❌ Ошибка подключения LongPoll: {e}")

# Хранилище сессий
active_sessions = {}


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВК ---
def get_vk_keyboard(buttons_rows, inline=False):
    keyboard = {"one_time": False, "inline": inline, "buttons": []}
    for row in buttons_rows:
        vk_row = []
        for btn in row:
            # Превращаем callback_data в payload для ВК
            action = {"type": "text", "label": btn['text']}
            if 'payload' in btn:
                action = {"type": "callback", "label": btn['text'], "payload": json.dumps(btn['payload'])}
            vk_row.append({"action": action, "color": btn.get('color', 'primary')})
        keyboard["buttons"].append(vk_row)
    return json.dumps(keyboard, ensure_ascii=False)


def send_msg(user_id, text, keyboard=None, template=None):
    params = {
        "user_id": user_id,
        "message": text,
        "random_id": get_random_id(),
    }
    if keyboard:
        params["keyboard"] = keyboard
    if template:
        params["template"] = template

    try:
        vk.messages.send(**params)
    except Exception as e:
        print(f"❌ Ошибка отправки ВК: {e}")


def ask_gemini_sync(prompt: str) -> str:
    """Синхронная версия запроса к Gemini (для ВК LongPoll)"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL_NAME}:generateContent?key={config.GEMINI_API_KEY}"
    try:
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15)
        if resp.status_code != 200: return "⚠️ Ошибка API Gemini."
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates: return "⚠️ ИИ не смог сгенерировать ответ."
        return candidates[0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Ошибка Gemini: {e}")
        return "⚠️ Не удалось связаться с ИИ."


def handle_streak_check_vk(user_id: int) -> int:
    """Проверка стрика (аналог из TG)"""
    user = db.get_user_data(user_id, platform="vk")
    was_reset = engine.check_streak(user)
    if was_reset:
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        user['last_solved_date'] = yesterday_str
        db.update_user_data(user_id, user, platform="vk")
    return was_reset


def get_menu_text_vk(user_id: int) -> str:
    """Формирование текста статистики (аналог из TG)"""
    was_reset = handle_streak_check_vk(user_id)
    user = db.get_user_data(user_id, platform="vk")

    league = content.get_league(user["score"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    status = "✅ Решено" if user['last_solved_date'] == today_str else "❌ Не решено"
    max_xp = engine.get_max_xp(user["score"])
    streak_icon = engine.get_streak_icon(user["streak"])

    reset_msg = f"\n\n⚠️ Ваш стрик сгорел! -{was_reset} XP" if was_reset else ""

    return (
        f"📊 Твоя статистика:\n\n"
        f"{league['icon']} Лига: {league['name']} ({league['desc']})\n"
        f"🎯 Цель: {user['target']} | 🏆 Балл: {user['score']}\n"
        f"{streak_icon} Стрик: {user['streak']} | 🛤 XP: {user['xp']}/{max_xp}\n"
        f"📅 Сегодня: {status}{reset_msg}"
    )


# --- КЛАВИАТУРЫ ---
main_menu_kb = get_vk_keyboard([
    [{"text": "📝 Решать", "color": "positive"}, {"text": "📊 Статистика", "color": "primary"}],
    [{"text": "⚙️ Настройки", "color": "secondary"}]
])

settings_kb = get_vk_keyboard([
    [{"text": "🎯 Изменить цель"}, {"text": "🌍 Часовой пояс"}],
    [{"text": "🏠 Меню", "color": "negative"}]
])


# --- ОСНОВНОЙ ЦИКЛ ---
def main_loop():
    print("🚀 Бот ВК запущен...\n")
    while True:
        try:
            for event in longpoll.listen():
                if event.type == VkBotEventType.MESSAGE_NEW:
                    msg = event.obj.message
                    user_id = msg['from_id']
                    text = msg['text'].strip()
                    text_l = text.lower()

                    # Инициализация пользователя и сессии
                    user = db.get_user_data(user_id, platform="vk")
                    if user_id not in active_sessions:
                        active_sessions[user_id] = {"state": "menu"}

                    session = active_sessions[user_id]
                    state = session.get("state")

                    # Глобальные команды
                    if text_l in ["🏠 меню", "начать", "старт", "/start"]:
                        active_sessions[user_id] = {"state": "menu"}
                        send_msg(user_id, f"Главное меню:", main_menu_kb)
                        continue

                    # --- ОБРАБОТКА СОСТОЯНИЙ ---

                    if state == "solving":
                        # Очищаем ввод: оставляем только цифры
                        user_digits = "".join(filter(lambda c: c.isdigit(), text))
                        if not user_digits:
                            send_msg(user_id, "Пожалуйста, введи цифры ответа (например: 13).")
                            continue

                        q = session.get("task_data", {})
                        # Индексы из ответа пользователя (уменьшаем на 1, т.к. в интерфейсе 1-5, а в базе 0-4)
                        user_selected_indices = sorted(
                            list(set([int(d) - 1 for d in user_digits if 0 < int(d) <= len(q['options'])])))
                        correct_indices = sorted(q.get("correct_indexes", []))

                        is_correct = user_selected_indices == correct_indices

                        # Формируем текст разбора
                        options_text = ""
                        for i, opt in enumerate(q['options']):
                            marker = ""
                            if i in correct_indices:
                                marker = " ✅"
                            elif i in user_selected_indices:
                                marker = " ❌"

                            style = "👉 " if i in user_selected_indices else "— "
                            options_text += f"{style}{i + 1}. {opt}{marker}\n"

                        old_score = user["score"]
                        old_league = content.get_league(old_score)
                        today_str = datetime.now().strftime("%Y-%m-%d")

                        if is_correct:
                            if user["last_solved_date"] != today_str:
                                user["streak"] += 1
                                user["last_solved_date"] = today_str
                            engine.add_user_xp(user, 1)
                            res_text = f"✅ Верно!\n\n{options_text}\n" + (
                                f"\n🆙 Новый балл: {user['score']}!" if user["score"] > old_score else "")
                        else:
                            engine.remove_user_xp(user, 1)
                            res_text = f"❌ Ошибка.\n\n{options_text}\n" + (
                                f"\n📉 Балл упал до {user['score']}." if user["score"] < old_score else "")

                        db.update_user_data(user_id, user, platform="vk")

                        # Проверка повышения лиги
                        new_league = content.get_league(user["score"])
                        if user["score"] > old_score and old_league["name"] != new_league["name"]:
                            send_msg(user_id, f"🎊 УРОВЕНЬ ПОВЫШЕН!\nЛига: {new_league['name']} {new_league['icon']}")

                        # Кнопка для разбора ИИ
                        post_kb = get_vk_keyboard(
                            [[{"text": "✨ Объяснить ошибку", "color": "primary"}, {"text": "🏠 Меню"}]])
                        send_msg(user_id, res_text, post_kb)
                        # Сохраняем сессию для объяснения, но меняем стейт
                        active_sessions[user_id]["state"] = "after_solve"
                        continue

                    elif state == "set_target":
                        match = re.search(r'\d+', text)
                        if match:
                            val = int(match.group())
                            if 0 <= val <= 100:
                                user["target"] = val
                                db.update_user_data(user_id, user, platform="vk")
                                send_msg(user_id, f"✅ Цель {val} установлена!", main_menu_kb)
                                active_sessions[user_id] = {"state": "menu"}
                            else:
                                send_msg(user_id, "Введите число от 0 до 100.")
                        else:
                            send_msg(user_id, "Пожалуйста, введите число.")
                        continue

                    elif state == "set_tz":
                        try:
                            val = int(re.search(r'-?\d+', text).group())
                            if -12 <= val <= 14:
                                user["timezone"] = val
                                db.update_user_data(user_id, user, platform="vk")
                                send_msg(user_id, f"✅ Часовой пояс UTC{val:+d} сохранен!", main_menu_kb)
                                active_sessions[user_id] = {"state": "menu"}
                            else:
                                send_msg(user_id, "Введите число от -12 до 14.")
                        except:
                            send_msg(user_id, "Введите число.")
                        continue

                    # --- ОБРАБОТКА КНОПОК МЕНЮ ---
                    if text_l == "📊 статистика":
                        send_msg(user_id, get_menu_text_vk(user_id), main_menu_kb)

                    elif text_l in ["📝 решать", "/bot", "bot", "решать"]:
                        was_reset = handle_streak_check_vk(user_id)
                        reset_msg = f"⚠️ Ваш стрик сгорел! -{was_reset} XP\n\n" if was_reset else ""
                        q = get_random_task()
                        if q:
                            active_sessions[user_id] = {"state": "solving", "task_data": q}
                            opts = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
                            msg_text = f"📝 Задание №{q['type']}\n\n{q['instruction']}\n\n{opts}\n\n👉 Введи цифры ответа:"
                            msg_text = reset_msg + msg_text

                            send_msg(user_id, msg_text, get_vk_keyboard([[{"text": "🏠 Меню", "color": "negative"}]]))
                        else:
                            send_msg(user_id, "Задания не найдены.",
                                     get_vk_keyboard([[{"text": "🏠 Меню", "color": "negative"}]]))

                    elif text_l == "✨ объяснить ошибку" and state == "after_solve":
                        q = session.get("task_data", {})
                        send_msg(user_id, "⏳ ИИ готовит разбор...")
                        prompt = f"Разбери задание: {q['instruction']}\nВарианты: {q['options']}\nОтветы: {q['correct_indexes']}\nОбъясни кратко."
                        explanation = ask_gemini_sync(prompt)
                        send_msg(user_id, f"✨ Разбор:\n\n{explanation}", main_menu_kb)
                        active_sessions[user_id] = {"state": "menu"}

                    elif text_l == "⚙️ настройки":
                        send_msg(user_id, "Настройки профиля:", settings_kb)

                    elif text_l == "🎯 изменить цель":
                        active_sessions[user_id] = {"state": "set_target"}
                        send_msg(user_id, "Введите ваш целевой балл (0-100):", get_vk_keyboard([[{"text": "🏠 Меню"}]]))

                    elif text_l == "🌍 часовой пояс":
                        active_sessions[user_id] = {"state": "set_tz"}
                        send_msg(user_id, "Введите смещение часового пояса (например, 3 для МСК):",
                                 get_vk_keyboard([[{"text": "🏠 Меню"}]]))

        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as e:
            print(f"[!] Ошибка сети/таймаут: {e}. Переподключение через 5 секунд...")
            time.sleep(5)

        except Exception as e:
            print(f"[!!!] Критическая ошибка: {e}")
            time.sleep(10)


# Инициализация сессии для уведомлений (отдельно от основного цикла)
vk_notify_session = vk_api.VkApi(token=config.VK_TOKEN)
vk_notify = vk_notify_session.get_api()


def send_notification_msg(user_id, text):
    """Отдельная функция отправки для потока уведомлений"""
    try:
        vk_notify.messages.send(
            user_id=user_id,
            message=text,
            random_id=get_random_id()
        )
    except Exception as e:
        print(f"❌ Ошибка отправки уведомления ВК ({user_id}): {e}")


def notification_thread_func():
    """Функция, которая будет крутиться в фоновом потоке"""
    print("🔔 Поток уведомлений ВК запущен...\n")
    while True:
        try:
            now_utc = datetime.now(timezone.utc)
            # Получаем всех пользователей, которым нужны уведомления
            # Убедись, что в базе данных у ВК пользователей стоит platform='vk'
            users = db.get_all_users_for_notify()
            today_str = datetime.now().strftime("%Y-%m-%d")

            for u_id, pl, tz, last_date in users:
                if pl != 'vk':
                    continue  # Игнорируем ТГ пользователей в этом потоке

                if last_date == today_str:
                    continue  # Пользователь сегодня уже решал

                # Вычисляем локальное время пользователя
                l_time = now_utc + timedelta(hours=(tz or 0))

                # Проверяем, совпадает ли текущий час с часами из конфига (например, 10, 14, 18)
                # content.NOTIFICATION_HOURS — это список [10, 14, 18] или аналогичный
                if any(l_time.hour == int(h) and abs(l_time.minute - (30 if h % 1 != 0 else 0)) < 5 for h in
                       content.NOTIFICATION_HOURS):
                    notify_text = content.get_notification(l_time)
                    send_notification_msg(u_id, notify_text)
                    print(f"Отправлено уведомление пользователю {u_id} в {l_time}")
                    time.sleep(0.5)  # Небольшая пауза, чтобы не спамить в API ВК

        except Exception as e:
            print(f"⚠️ Ошибка в потоке уведомлений: {e}")

        # Проверяем раз в 10 минут (600 секунд)
        time.sleep(600)


if __name__ == "__main__":
    db.init_db()

    notify_thread = threading.Thread(target=notification_thread_func, daemon=True)
    notify_thread.start()

    main_loop()
