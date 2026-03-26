import vk_api
from vk_api.bot_longpoll import VkBotLongPoll, VkBotEventType
from vk_api.utils import get_random_id
import json
import re
import requests
from datetime import datetime, timedelta

import config
import database as db
import data_content as content
import engine
from main import get_random_task

# Инициализация ВК
vk_session = vk_api.VkApi(token=config.VK_TOKEN)
vk = vk_session.get_api()

try:
    longpoll = VkBotLongPoll(vk_session, config.VK_GROUP_ID)
except Exception as e:
    print(f"❌ Ошибка подключения LongPoll: {e}")

# Хранилище сессий (аналог FSM и active_sessions из TG)
active_sessions = {}


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ВК ---
def get_vk_keyboard(buttons_rows, inline=False):
    keyboard = {
        "one_time": False,
        "inline": inline,
        "buttons": []
    }
    for row in buttons_rows:
        vk_row = []
        for btn in row:
            # Превращаем callback_data в payload для ВК
            action = {"type": "text", "label": btn['text']}
            if 'payload' in btn:
                action = {"type": "callback", "label": btn['text'], "payload": json.dumps(btn['payload'])}

            vk_row.append({
                "action": action,
                "color": btn.get('color', 'primary')
            })
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
        resp = requests.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=10)
        if resp.status_code != 200: return "⚠️ Ошибка API."
        data = resp.json()
        candidates = data.get("candidates", [])
        if not candidates: return "⚠️ ИИ не смог сгенерировать ответ."
        return candidates[0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Ошибка Gemini: {e}")
        return "⚠️ Не удалось связаться с ИИ."


def handle_streak_check_vk(user_id: int) -> bool:
    """Проверка стрика (аналог из TG)"""
    user = db.get_user_data(user_id, platform="vk")
    was_reset = engine.check_and_reset_streak(user)
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

    reset_msg = "\n\n⚠️ Ваш стрик сгорел! -5 XP" if was_reset else ""

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
    print("🚀 Бот ВК запущен...")

    for event in longpoll.listen():
        if event.type == VkBotEventType.MESSAGE_NEW:
            msg = event.obj.message
            user_id = msg['from_id']
            text = msg['text'].strip()
            text_l = text.lower()

            user = db.get_user_data(user_id, platform="vk")
            session = active_sessions.get(user_id, {"state": "menu"})

            # Логика команд "Назад" / "Меню"
            if text_l in ["🏠 меню", "начать", "старт", "/start"]:
                active_sessions[user_id] = {"state": "menu"}
                send_msg(user_id, "Главное меню:", main_menu_kb)
                continue

            # --- СОСТОЯНИЯ (Аналог FSM) ---
            state = session.get("state")

            if state == "solving":
                # Обработка ответа на задачу
                q = session.get("task_data")
                # Упрощенная логика ответа для ВК (текстом)
                user_answer = "".join(filter(str.isdigit, text))
                if not user_answer:
                    send_msg(user_id, "Пожалуйста, введи цифры ответа (например: 13).")
                    continue

                correct_indices = sorted([i + 1 for i in q["correct_indexes"]])
                correct_str = "".join(map(str, correct_indices))
                user_sorted = "".join(sorted(list(set(user_answer))))

                old_score = user["score"]
                old_league = content.get_league(old_score)
                today_str = datetime.now().strftime("%Y-%m-%d")

                if user_sorted == correct_str:
                    if user["last_solved_date"] != today_str:
                        user["streak"] += 1
                        user["last_solved_date"] = today_str
                    engine.add_user_xp(user, 1)
                    res_text = f"✅ Верно!\n\nXP: {user['xp']} | Балл: {user['score']}"
                else:
                    engine.remove_user_xp(user, 1)
                    user["streak"] = 0
                    res_text = f"❌ Ошибка. Правильный ответ: {correct_str}\nШтраф: -1 XP."

                db.update_user_data(user_id, user, platform="vk")

                # Проверка повышения лиги
                new_league = content.get_league(user["score"])
                if user["score"] > old_score and old_league["name"] != new_league["name"]:
                    send_msg(user_id, f"🎊 УРОВЕНЬ ПОВЫШЕН!\nЛига: {new_league['name']} {new_league['icon']}")

                # Кнопка для разбора ИИ
                post_kb = get_vk_keyboard([[{"text": "✨ Объяснить ошибку", "color": "primary"}, {"text": "🏠 Меню"}]])
                send_msg(user_id, res_text, post_kb)
                # Сохраняем сессию для объяснения, но меняем стейт
                active_sessions[user_id]["state"] = "after_solve"
                continue

            elif state == "ai":
                send_msg(user_id, "⏳ ИИ анализирует ваш вопрос...")
                ans = ask_gemini_sync(text)
                send_msg(user_id, f"🤖 Ответ ИИ:\n\n{ans}", get_vk_keyboard([[{"text": "🏠 Меню"}]]))
                continue

            elif state == "set_target":
                try:
                    val = int(re.search(r'\d+', text).group())
                    if 0 <= val <= 100:
                        user["target"] = val
                        db.update_user_data(user_id, user, platform="vk")
                        send_msg(user_id, f"✅ Цель {val} установлена!", main_menu_kb)
                        active_sessions[user_id] = {"state": "menu"}
                    else:
                        send_msg(user_id, "Введите число от 0 до 100.")
                except:
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

            elif text_l == "📝 решать":
                handle_streak_check_vk(user_id)
                q = get_random_task()
                if q:
                    active_sessions[user_id] = {"state": "solving", "task_data": q}
                    opts = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
                    msg_text = f"📝 Задание №{q['type']}\n\n{q['instruction']}\n\n{opts}\n\n👉 Введи цифры ответа:"
                    send_msg(user_id, msg_text, get_vk_keyboard([[{"text": "🏠 Меню", "color": "negative"}]]))

            elif text_l == "✨ объяснить ошибку" and session.get("state") == "after_solve":
                q = session.get("task_data")
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


if __name__ == "__main__":
    db.init_db()
    main_loop()
