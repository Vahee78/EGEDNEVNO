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


def toggle_option(user_id: int, q_id: str, opt_idx: int):
    session = active_sessions.get(user_id)
    if not session:
        q = engine.get_task(q_id)
        session = active_sessions[user_id] = {"task_data": q, "selected": [], "state": "solving"}
        logger.info(f"Попытка переключения кнопок пользователем {user_id} без активной сессии.\n"
                    f"Сессия создана. ID задания: {q_id}")

    if session.get("state") != "solving" or str(session["task_data"]["id"]) != q_id:
        logger.warning(
            f"Устаревший клик toggle от {user_id}. Стейт: {session.get('state')}, ID в сессии: {session['task_data']['id']}, Получен: {q_id}")
        return {"status": "error"}

    if opt_idx in session["selected"]:
        session["selected"].remove(opt_idx)
        logger.debug(f"Юзер {user_id} убрал вариант {opt_idx + 1}. Текущий выбор: {session['selected']}")
    else:
        session["selected"].append(opt_idx)
        logger.debug(f"Юзер {user_id} выбрал вариант {opt_idx + 1}. Текущий выбор: {session['selected']}")

    option_numbers = [str(i + 1) for i in range(len(session["task_data"]["options"]))]

    return {
        "status": "success",
        "option_numbers": option_numbers,
        "selected": session["selected"]
    }


def process_text_answer(user_id: int, user_ans: str):

    session = active_sessions.get(user_id)

    # Проверяем, что пользователь действительно решает текстовое задание
    if not session:
        logger.debug(f"Игнорируем текстовое сообщение от {user_id}: активная сессия отсутствует")
        return {"status": "error"}

    if session.get("state") != "solving":
        logger.debug(
            f"Игнорируем текстовое сообщение от {user_id}: стейт сессии не 'solving' (текущий: '{session.get('state')}')")
        return {"status": "error"}

    q = session["task_data"]
    if "answer_variants" not in q or not q["answer_variants"]:
        logger.debug(
            f"Игнорируем текстовое сообщение от {user_id}: текущее задание {q['id']} не предусматривает текстовый ввод")
        return {"status": "error"}

    # Проверка правильности
    correct_variants = [v.lower().strip() for v in q["answer_variants"]]
    is_correct = user_ans in correct_variants

    logger.info(
        f"Пользователь {user_id} ответил текстом на задание {q['id']}. Ввод: '{user_ans}' | "
        f"Ожидалось: {correct_variants} | Результат: {is_correct}")

    user = db.get_user_data(user_id)

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
            logger.info(f"Стрик пользователя {user_id} увеличен до {user['streak']} дней.")
    else:
        engine.remove_user_xp(user, 1)

    # Работа с БД
    db.log_user_answer(user_id, q["id"], is_correct)

    db.update_user_data(user_id, user)
    logger.debug(f"БД обновлена для {user_id}. Старый балл: {old_score} -> Новый балл: {user['score']}")

    # Работа с лигами
    old_league = engine.get_league(old_score)
    new_league = engine.get_league(user["score"])

    # Меняем стейт, чтобы не спамить ответами
    session["state"] = "after_solve"

    return {
        "status": "success",
        "is_correct": is_correct,
        "selected": session["selected"],
        "answer_variants": q["answer_variants"],
        "id": q["id"],
        "old_score": old_score,
        "new_score": user["score"],
        "current_streak": user["streak"],
        "streak_increased": streak_increased,
        "old_league": old_league,
        "new_league": new_league
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
            logger.info(f"Стрик пользователя {user_id} увеличен до {user['streak']} дней.")
    else:
        engine.remove_user_xp(user, 1)

    # Работа с БД
    db.log_user_answer(user_id, q["id"], is_correct)

    db.update_user_data(user_id, user)
    logger.debug(f"БД обновлена для {user_id}. Старый балл: {old_score} -> Новый балл: {user['score']}")

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


def update_user_names(user_id, username, full_name):
    user = db.get_user_data(user_id)
    user["username"] = username
    user["full_name"] = full_name
    db.update_user_data(user_id, user)
    return user
