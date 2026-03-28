import os
import json
import random
import aiohttp
from datetime import datetime, timedelta

import config
import database as db
import engine
import data_content as content


def get_random_task():
    """Выбирает случайное задание из папки tasks/"""
    try:
        tasks_dir = "tasks"
        files = [f for f in os.listdir(tasks_dir) if f.startswith("task_") and f.endswith(".json")]
        if not files:
            return None

        chosen_file = random.choice(files)
        with open(os.path.join(tasks_dir, chosen_file), 'r', encoding='utf-8') as f:
            tasks_list = json.load(f)
            task = random.choice(tasks_list)
            task["type"] = chosen_file.split('_')[1].split('.')[0]
            if int(task["type"]) == 4:
                task[
                    "instruction"] = "Укажите варианты ответов, в которых верно выделена буква, обозначающая ударный гласный звук. Запишите номера ответов."
            return task
    except Exception as e:
        print(f"Ошибка загрузки задания: {e}")
        return None


def handle_streak_check(user_id: int) -> bool:
    """Централизованная проверка стрика с защитой от бесконечного списания."""
    user = db.get_user_data(user_id)
    was_reset = engine.check_and_reset_streak(user)
    if was_reset:
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        user['last_solved_date'] = yesterday_str
        db.update_user_data(user_id, user)
    return was_reset


def get_menu_text(user_id: int) -> str:
    """Генерирует текст главного меню пользователя"""
    was_reset = handle_streak_check(user_id)
    user = db.get_user_data(user_id)

    league = content.get_league(user["score"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    status = "✅ Решено" if user['last_solved_date'] == today_str else "❌ Не решено"
    max_xp = engine.get_max_xp(user["score"])
    streak_icon = engine.get_streak_icon(user["streak"])

    reset_msg = "\n\n⚠️ *Ваш стрик сгорел!* -5 XP" if was_reset else ""

    return (
        f"📊 *Ваша статистика:*\n"
        f"{league['icon']} Лига: {league['name']} ({league['desc']})\n"
        f"🎯 Цель: {user['target']} | 🏆 Балл: {user['score']}\n"
        f"{streak_icon} Стрик: {user['streak']} | 🛤 XP: {user['xp']}/{max_xp}\n"
        f"📅 Сегодня: {status}{reset_msg}"
    )


async def ask_gemini(prompt: str) -> str:
    """Отправляет запрос к ИИ Gemini"""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL_NAME}:generateContent?key={config.GEMINI_API_KEY}"
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"Gemini API Error {resp.status}: {error_text}")
                    return "⚠️ Ошибка API."
                data = await resp.json()
                candidates = data.get("candidates", [])
                if not candidates:
                    return "⚠️ ИИ не смог сгенерировать ответ."
                return candidates[0]["content"]["parts"][0]["text"]
    except Exception as e:
        print(f"Ошибка при вызове Gemini: {e}")
        return "⚠️ Не удалось связаться с ИИ."
