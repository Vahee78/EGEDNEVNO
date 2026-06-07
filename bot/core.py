from datetime import datetime, timedelta, date

import database as db
import engine


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
