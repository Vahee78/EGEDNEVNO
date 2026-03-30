from datetime import datetime
import data_content as content


def get_max_xp(score: int) -> int:
    """Возвращает количество заданий (XP), нужных для получения следующего балла."""
    if score < 50:
        return 1
    elif score < 60:
        return 3
    elif score < 70:
        return 6
    elif score < 80:
        return 13
    elif score < 90:
        return 26
    elif score < 99:
        return 50
    elif score == 99:
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
    if streak == 0: return "🌑"
    if streak < 3: return "🔥"
    if streak < 7: return "💥"
    if streak < 30: return "☄️"
    return "☀️"
