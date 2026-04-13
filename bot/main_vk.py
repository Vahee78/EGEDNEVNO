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
from data_content import get_streak_congrats
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
                        q = session.get("task_data", {})

                        # Определяем тип задания
                        is_text_task = "answer_variants" in q and len(q["answer_variants"]) > 0
                        options_text = ""

                        old_score = user["score"]
                        old_league = content.get_league(old_score)
                        today_str = datetime.now().strftime("%Y-%m-%d")

                        if is_text_task:
                            # Логика для заданий с текстовым ответом (5, 6, 7)
                            user_ans = text.lower().strip().translate(str.maketrans('', '', '.,!?'))
                            if not user_ans:
                                send_msg(user_id, "Пожалуйста, напишите ответ текстом.")
                                continue

                            correct_variants = [v.lower().strip() for v in q["answer_variants"]]
                            is_correct = user_ans in correct_variants

                            correct_display = " / ".join(q["answer_variants"])

                            # Начисляем/отнимаем очки для текстового задания
                            if is_correct:
                                engine.add_user_xp(user, 1)
                                res_text = f"✅ Верно!\nОтвет: {q['answer_variants'][0]}\n" + (
                                    f"\n🆙 Новый балл: {user['score']}!" if user["score"] > old_score else "")
                                if user["last_solved_date"] != today_str:
                                    user["streak"] += 1
                                    user["last_solved_date"] = today_str
                                    if streak_congrats := get_streak_congrats(user["streak"]):
                                        res_text = streak_congrats + "\n\n" + res_text
                            else:
                                engine.remove_user_xp(user, 1)
                                user["streak"] = 0
                                res_text = f"❌ Ошибка.\nВаш ответ: {user_ans}\nПравильный ответ: {correct_display}\nШтраф: -1 XP." + (
                                    f"\n📉 Балл упал до {user['score']}." if user["score"] < old_score else "")

                        else:
                            # Логика для заданий с вариантами ответов (индексы)
                            user_digits = "".join(filter(lambda c: c.isdigit(), text))
                            if not user_digits:
                                send_msg(user_id, "Пожалуйста, введи цифры ответа (например: 13).")
                                continue

                            user_selected_indices = sorted(
                                list(set([int(d) - 1 for d in user_digits if 0 < int(d) <= len(q.get('options', []))])))
                            correct_indices = sorted(q.get("correct_indexes", []))

                            is_correct = user_selected_indices == correct_indices

                            # Формируем текст разбора для цифровых вариантов
                            options_text = "\n".join(
                                f"{'👉 ' if i in user_selected_indices else '— '}{i + 1}. {opt}{' ✅' if i in correct_indices else (' ❌' if i in user_selected_indices else '')}"
                                for i, opt in enumerate(q.get('options', []))
                            )

                            if is_correct:
                                engine.add_user_xp(user, 1)
                                res_text = f"✅ Верно!\n\n{options_text}\n" + (
                                    f"\n🆙 Новый балл: {user['score']}!" if user["score"] > old_score else "")
                                if user["last_solved_date"] != today_str:
                                    user["streak"] += 1
                                    user["last_solved_date"] = today_str
                                    if streak_congrats := get_streak_congrats(user["streak"]):
                                        res_text = streak_congrats + "\n\n" + res_text
                            else:
                                engine.remove_user_xp(user, 1)
                                user["streak"] = 0
                                res_text = f"❌ Ошибка.\n\n{options_text}\n\nШтраф: -1 XP." + (
                                    f"\n📉 Балл упал до {user['score']}." if user["score"] < old_score else "")

                        # Сохраняем обновленного пользователя
                        db.update_user_data(user_id, user, platform="vk")

                        # Проверка повышения лиги
                        new_league = content.get_league(user["score"])
                        if user["score"] > old_score and old_league["name"] != new_league["name"]:
                            send_msg(user_id, f"🎊 УРОВЕНЬ ПОВЫШЕН!\nЛига: {new_league['name']} {new_league['icon']}")

                        # Кнопка для разбора ИИ
                        post_kb = get_vk_keyboard(
                            [[{"text": "✨ Разбор от ИИ", "color": "primary"}, {"text": "🏠 Меню"}]])
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

                            is_text_task = "answer_variants" in q and len(q["answer_variants"]) > 0

                            if is_text_task:
                                q['instruction'].replace("\n", "\n\n")
                                msg_text = f"📝 Задание №{q['type']}\n\n{q['instruction']}\n\n⌨️ Напиши ответ сообщением:"
                            else:
                                opts = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q.get('options', []))])
                                msg_text = f"📝 Задание №{q['type']}\n\n{q['instruction']}\n\n{opts}\n\n👉 Введи цифры ответа:"

                            msg_text = reset_msg + msg_text
                            send_msg(user_id, msg_text, get_vk_keyboard([[{"text": "🏠 Меню", "color": "negative"}]]))
                        else:
                            send_msg(user_id, "Задания не найдены.",
                                     get_vk_keyboard([[{"text": "🏠 Меню", "color": "negative"}]]))

                    elif text_l == "✨ разбор от ИИ" and state == "after_solve":
                        q = session.get("task_data", {})
                        send_msg(user_id, "⏳ ИИ готовит разбор...")

                        is_text_task = "answer_variants" in q and len(q["answer_variants"]) > 0

                        if is_text_task:
                            prompt = f"Разбери задание ЕГЭ: {q.get('instruction', '')}\nПравильные ответы: {q.get('answer_variants', [])}\nОбъясни кратко, почему так."
                        else:
                            prompt = f"Разбери задание: {q.get('instruction', '')}\nВарианты: {q.get('options', [])}\nВерные ответы (индексы): {q.get('correct_indexes', [])}\nОбъясни кратко."

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

    # Хранилище: кому и в какой час мы уже отправили пуш
    # Формат: {"user_id_hour"} -> "123456_9", "123456_15"
    notified_today = set()

    while True:
        try:
            now_utc = datetime.now(timezone.utc)

            # Раз в час очищаем кэш отправленных
            if now_utc.minute == 0:
                notified_today.clear()

            users = db.get_all_users_for_notify()

            for u_id, pl, tz, last_date in users:
                if pl != 'vk':
                    continue

                    # 1. Считаем локальное время пользователя с защитой от None
                tz_offset = tz if tz is not None else 3  # по умолчанию МСК (UTC+3)
                l_time = now_utc + timedelta(hours=tz_offset)

                # 2. Правильная локальная дата для проверки last_date
                user_today_str = l_time.strftime("%Y-%m-%d")
                if last_date == user_today_str:
                    continue  # Сегодня уже решал

                # 3. Проверка попадания в час рассылки
                current_hour = l_time.hour
                current_minute = l_time.minute

                if any(current_hour == int(h) and 0 <= current_minute - (30 if h % 1 != 0 else 0) < 15 for h in
                       content.NOTIFICATION_HOURS):

                    # 4. Защита от дублей
                    notify_key = f"{u_id}_{current_hour}"
                    if notify_key in notified_today:
                        continue  # Уже отправляли в этот час

                    # 5. Отправка
                    notify_text = content.get_notification(l_time)
                    send_notification_msg(u_id, notify_text)
                    print(f"✅ Отправлено уведомление ВК пользователю {u_id} в {l_time.strftime('%H:%M')}")

                    # Запоминаем, что пуш отправлен
                    notified_today.add(notify_key)

                    time.sleep(0.5)  # Защита от лимитов ВК

        except Exception as e:
            print(f"⚠️ Ошибка в потоке уведомлений: {e}")

        # Проверяем раз в 5 минут (300 секунд), чтобы точно не пропустить окно
        time.sleep(300)


if __name__ == "__main__":
    db.init_db()

    notify_thread = threading.Thread(target=notification_thread_func, daemon=True)
    notify_thread.start()

    main_loop()