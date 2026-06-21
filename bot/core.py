from datetime import datetime, timedelta, date
from loguru import logger
from random import choice

import database as db
import engine


active_sessions = {}


def prepare_new_task(user_id: int, task_type: str = "def") -> dict:
    """
    Чистое ядро: управляет сессией и тянет задание из БД.
    Возвращает словарь с заданием и флагами для бота.
    """
    # 1. Тянем задание из базы данных
    if task_type == "fav":
        fav_ids = db.get_user_favourites(user_id)
        q = engine.get_task(choice(fav_ids)) if fav_ids else None
    else:
        q = engine.get_random_task()

    if not q:
        logger.error(f"Ошибка при получении задания из БД для {user_id}")
        return {"status": "error_empty"}

    # 2. Инициализируем локальную сессию в памяти ядра
    active_sessions[user_id] = {"task_data": q, "selected": [], "state": "solving"}
    logger.debug(f"Сессия создана в ядре для {user_id}. ID задания: {q['id']}")

    # 3. Проверяем, находится ли задание в избранном
    is_fav = db.is_favourite(user_id, q['id'])

    # Отдаем боту чистые данные для формирования интерфейса
    return {
        "status": "success",
        "task_data": q,
        "is_favourite": is_fav
    }


def process_answer_submission(user_id: int, q_id: int) -> dict:
    """
    Выполняет всю грязную работу: валидация сессии, расчет правильности,
    изменение очков/стриков в БД. Возвращает только чистые данные.
    """
    session = active_sessions.get(user_id)

    # Валидация состояния сессии
    if not session or session.get("state") != "solving":
        return {"status": "error", "reason": "already_solved"}

    if str(session["task_data"]["id"]) != str(q_id):
        return {"status": "error", "reason": "session_conflict"}

    if not session["selected"]:
        return {"status": "error", "reason": "none_selected"}

    user = db.get_user_data(user_id)
    q = session["task_data"]

    # Проверка правильности
    is_correct = sorted(session["selected"]) == sorted(q["correct_indexes"])

    # Замораживаем стейт сессии от повторных кликов
    session["state"] = "after_solve"

    # Фиксируем старые значения для сравнения изменений
    old_score = user["score"]
    today_str = datetime.now().strftime("%Y-%m-%d")
    streak_increased = False

    # Геймификация
    if is_correct:
        engine.add_user_xp(user, 1)
        if user["last_solved_date"] != today_str:
            user["streak"] += 1
            user["last_solved_date"] = today_str
            streak_increased = True
    else:
        engine.remove_user_xp(user, 1)

    db.update_user_data(user_id, user)

    # Работа с лигами
    old_league = engine.get_league(old_score)
    new_league = engine.get_league(user["score"])

    # Возвращаем сухие данные для bot.py
    return {
        "status": "success",
        "is_correct": is_correct,
        "selected": session["selected"],
        "correct_indexes": q["correct_indexes"],
        "options": q["options"],
        "old_score": old_score,
        "new_score": user["score"],
        "current_streak": user["streak"],
        "streak_increased": streak_increased,
        "old_league": old_league,
        "new_league": new_league
    }


def handle_streak_check(user_id: int) -> int:
    """Централизованная проверка стрика с защитой от бесконечного списания."""
    user = db.get_user_data(user_id)
    penalty = engine.check_streak(user)  #
    if penalty:
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        user['last_solved_date'] = yesterday_str
        db.update_user_data(user_id, user)
    return penalty


def get_menu_data(user_id: int):
    user = db.get_user_data(user_id)

    league = engine.get_league(user["score"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    is_solved_today = user['last_solved_date'] == today_str

    max_xp = engine.get_max_xp(user["score"])
    streak_icon = engine.get_streak_icon(user["streak"])

    days_left = (date(2027, 6, 1) - date.today()).days
    return {
        "username": user.get("username"),
        "league_icon": league['icon'],
        "league_name": league['name'],
        "league_desc": league['desc'],
        "target": user['target'],
        "score": user['score'],
        "streak_icon": streak_icon,
        "streak": user['streak'],
        "xp": user['xp'],
        "max_xp": max_xp,
        "is_solved_today": is_solved_today,
        "days_left": days_left
    }
