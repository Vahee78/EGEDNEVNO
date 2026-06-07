import os
import random
import json
from loguru import logger
from datetime import datetime


def get_random_task() -> dict | None:
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
                task["instruction"] = "Укажите варианты ответов, в которых верно выделена буква, обозначающая ударный гласный звук. Запишите номера ответов."
            return task
    except Exception as e:
        logger.warning(f"Ошибка загрузки задания: {e}")
        return None


def get_task(task_id) -> dict | None:
    """Ищет задание по ID во всех файлах папки tasks/"""
    tasks_dir = "tasks"
    target_id = str(task_id).strip()

    # Сканируем папку с файлами задач
    for filename in os.listdir(tasks_dir):
        if filename.startswith("task_") and filename.endswith(".json"):
            try:
                with open(os.path.join(tasks_dir, filename), 'r', encoding='utf-8') as f:
                    tasks = json.load(f)

                # Ищем задачу с нужным ID
                for task in tasks:
                    if str(task.get("id")).strip() == target_id:
                        task["type"] = filename.split('_')[1].split('.')[0]
                        if task["type"] == "4":
                            task["instruction"] = (
                                "Укажите варианты ответов, в которых верно выделена буква, "
                                "обозначающая ударный гласный звук. Запишите номера ответов."
                            )
                        return task
            except Exception:
                continue

    return None


# Система Лиг
def get_league(score: int):
    if score < 60:
        return {"name": "Бронзовая лига", "desc": "Платное обучение", "icon": "🥉", "next": 60}
    elif score < 70:
        return {"name": "Серебряная лига", "desc": "Регионы (УрФУ, НГУ, КФУ)", "icon": "🥈", "next": 70}
    elif score < 80:
        return {"name": "Золотая лига", "desc": "Золотой стандарт (МИРЭА, МИСиС, ЛЭТИ)", "icon": "🥇", "next": 80}
    elif score < 90:
        return {"name": "Алмазная лига", "desc": "Топ-вузы (Бауманка, МИФИ, СПбПУ)", "icon": "💎", "next": 90}
    else:
        return {"name": "Платиновая лига", "desc": "Элита (МФТИ, ВШЭ, ИТМО)", "icon": "🛸", "next": 101}


def get_max_xp(score: int) -> int:
    """Возвращает количество заданий (XP), нужных для получения следующего балла."""
    if score < 50:
        return 1
    if score < 60:
        return 3
    if score < 70:
        return 6
    if score < 80:
        return 13
    if score < 90:
        return 26
    if score < 99:
        return 50
    if score == 99:
        return 100
    return 1


def add_user_xp(user: dict, amount: int):
    """Добавляет XP и повышает балл, если шкала заполнена."""
    if user["score"] >= 100: return
    user["xp"] += amount
    while True:
        max_xp = get_max_xp(user["score"])
        if user["xp"] >= max_xp:
            user["xp"] -= max_xp
            user["score"] += 1
            if user["score"] >= 100:
                user["score"] = 100
                user["xp"] = 0
                break
        else:
            break


def remove_user_xp(user: dict, amount: int):
    """Отнимает XP и понижает балл со штрафами."""
    user["xp"] -= amount
    while user["xp"] < 0:
        if user["score"] <= 0:
            user["score"] = 0
            user["xp"] = 0
            break
        user["score"] -= 1
        user["xp"] += get_max_xp(user["score"])


def check_streak(user: dict) -> int:
    """Проверяет, не сгорел ли стрик. Возвращает количество штрафных XP, если стрик сброшен."""
    if not user.get("last_solved_date"): return False

    today = datetime.now().date()
    try:
        last_solved = datetime.strptime(user["last_solved_date"], "%Y-%m-%d").date()
    except ValueError:
        return False

    diff = (today - last_solved).days
    if diff > 1:
        penalty = max(1, get_max_xp(user["score"]) // 3)
        user["streak"] = 0
        remove_user_xp(user, penalty)  # Штраф за пропуск
        return penalty
    return False


def get_streak_icon(streak: int) -> str:
    thresholds = [
        (365, "👑"),
        (100, "💯"),
        (30, "☀️"),
        (7, "☄️"),
        (3, "💥"),
        (1, "🔥"),
    ]

    if streak == 0:
        return "🌑"

    for threshold, icon in thresholds:
        if streak >= threshold:
            return icon

    return "🔥"
