import sqlite3
from loguru import logger

# Имя файла базы данных
DB_NAME = "users.db"


def init_db():
    """Инициализирует все таблицы базы данных."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # 1. Создаем таблицу пользователей с составным первичным ключом (user_id + platform)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER,
            platform TEXT DEFAULT 'tg', 
            username TEXT,
            full_name TEXT,
            score INTEGER DEFAULT 40,
            xp INTEGER DEFAULT 0,
            streak INTEGER DEFAULT 0,
            target INTEGER DEFAULT 80,
            timezone INTEGER,
            last_solved_date TEXT,
            notifications_enabled INTEGER DEFAULT 1,
            streak_freezes INTEGER DEFAULT 0, -- Хранит количество купленных заморозок
            PRIMARY KEY (user_id, platform)
        )
    ''')

    # Миграция: Добавляем streak_freezes, если таблицы уже были созданы ранее
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN streak_freezes INTEGER DEFAULT 0")
        logger.info("База данных обновлена: успешно добавлена колонка streak_freezes")
    except sqlite3.OperationalError:
        # Эта ошибка возникает, если колонка уже существует в таблице. Просто игнорируем ее.
        pass

    # 2. Создаем таблицу истории ответов пользователя (для статистики и работы над ошибками)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS user_answers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            platform TEXT DEFAULT 'tg',
            question_id INTEGER,
            is_correct INTEGER, -- 1 (верно), 0 (неверно)
            answered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(user_id, platform) REFERENCES users(user_id, platform)
        )
    ''')

    # 3. Создаем таблицу избранных заданий
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS favourites (
            user_id INTEGER,
            platform TEXT DEFAULT 'tg',
            question_id INTEGER,
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, platform, question_id),
            FOREIGN KEY(user_id, platform) REFERENCES users(user_id, platform)
        )
    ''')

    conn.commit()
    conn.close()
    logger.info("Инициализация базы данных SQLite успешно завершена.")


def get_user_data(user_id, platform="tg"):
    """Получает данные пользователя по ID и платформе."""
    conn = sqlite3.connect(DB_NAME)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Поиск идет по двум полям сразу
    cursor.execute('SELECT * FROM users WHERE user_id = ? AND platform = ?', (user_id, platform))
    row = cursor.fetchone()
    conn.close()

    if row:
        return dict(row)

    logger.info(f"Пользователь {user_id} ({platform}) не найден. Создаем новый профиль.")
    # Если записи нет — возвращаем шаблон для нового игрока
    return {
        "user_id": user_id,
        "platform": platform,
        "username": None,
        "full_name": None,
        "score": 40,
        "xp": 0,
        "streak": 0,
        "target": 80,
        "timezone": None,
        "last_solved_date": None,
        "notifications_enabled": 1,
        "streak_freezes": 0  # Дефолтное значение для нового профиля
    }


def update_user_data(user_id: int, data: dict, platform: str = "tg"):
    """
    Безопасно сохраняет или обновляет данные пользователя (UPSERT).
    Если пользователь существует, обновляются только переданные поля.
    """
    logger.debug(f"Сохранение/обновление данных пользователя {user_id} ({platform})")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    username = data.get("username")
    full_name = data.get("full_name")
    score = data.get("score")
    xp = data.get("xp")
    streak = data.get("streak")
    target = data.get("target")
    timezone = data.get("timezone")
    last_solved_date = data.get("last_solved_date")
    notifications_enabled = data.get("notifications_enabled")
    streak_freezes = data.get("streak_freezes")

    # В блоке VALUES мы вставляем дефолтные значения (score=40, target=80, notifications_enabled=1),
    # если запись создается впервые.
    # В блоке ON CONFLICT(user_id, platform) DO UPDATE SET мы обновляем колонки
    # только ЕСЛИ нам передали новое значение (используем COALESCE, чтобы не затереть старое значение на None).
    cursor.execute('''
        INSERT INTO users (
            user_id, platform, username, full_name, score, xp, 
            streak, target, timezone, last_solved_date, notifications_enabled, streak_freezes
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, platform) DO UPDATE SET
            username = COALESCE(?, username),
            full_name = COALESCE(?, full_name),
            score = COALESCE(?, score),
            xp = COALESCE(?, xp),
            streak = COALESCE(?, streak),
            target = COALESCE(?, target),
            timezone = COALESCE(?, timezone),
            last_solved_date = COALESCE(?, last_solved_date),
            notifications_enabled = COALESCE(?, notifications_enabled),
            streak_freezes = COALESCE(?, streak_freezes)
    ''', (
        # Блок INSERT (VALUES)
        user_id, platform, username, full_name,
        score if score is not None else 40,
        xp if xp is not None else 0,
        streak if streak is not None else 0,
        target if target is not None else 80,
        timezone, last_solved_date,
        notifications_enabled if notifications_enabled is not None else 1,
        streak_freezes if streak_freezes is not None else 0,

        # Блок UPDATE (DO UPDATE SET) — связывается с COALESCE(?, column_name)
        username, full_name, score, xp, streak, target, timezone, last_solved_date, notifications_enabled,
        streak_freezes
    ))

    conn.commit()
    conn.close()
    logger.debug(f"Данные пользователя {user_id} ({platform}) успешно сохранены/обновлены через UPSERT.")


def get_all_users_for_notify():
    """Возвращает список активных пользователей с включенными уведомлениями для рассылки."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT user_id, platform, timezone, last_solved_date 
        FROM users 
        WHERE timezone IS NOT NULL AND notifications_enabled = 1
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows


# ==========================================
# ИСТОРИЯ ОТВЕТОВ И РАБОТА НАД ОШИБКАМИ
# ==========================================

def log_user_answer(user_id: int, question_id: int, is_correct: bool, platform: str = "tg"):
    """Записывает попытку ответа пользователя на задание в историю."""
    logger.debug(f"Логирование попытки: {user_id} ({platform}) | Вопрос: {question_id} | Верно: {is_correct}")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        INSERT INTO user_answers (user_id, platform, question_id, is_correct)
        VALUES (?, ?, ?, ?)
    ''', (user_id, platform, question_id, 1 if is_correct else 0))
    conn.commit()
    conn.close()


def get_unresolved_mistakes(user_id: int, platform: str = "tg") -> list:
    """
    Возвращает список ID заданий, в которых последняя попытка пользователя была неверной.
    Сюда попадают нерешенные «ошибки» пользователя.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT question_id 
        FROM user_answers ua
        WHERE user_id = ? AND platform = ?
          AND answered_at = (
              SELECT MAX(answered_at) 
              FROM user_answers 
              WHERE user_id = ua.user_id 
                AND platform = ua.platform 
                AND question_id = ua.question_id
          )
          AND is_correct = 0
    ''', (user_id, platform))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]


def check_mistake_history(user_id: int, question_id: int, platform: str = "tg") -> dict:
    """
    Проверяет историю решений по конкретной задаче.
    Возвращает словарь, по которому можно понять, исправил ли юзер старую ошибку.
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT is_correct FROM user_answers 
        WHERE user_id = ? AND platform = ? AND question_id = ?
        ORDER BY answered_at ASC
    ''', (user_id, platform, question_id))
    attempts = cursor.fetchall()
    conn.close()

    if not attempts:
        return {"solved": False, "had_mistake": False, "corrected": False, "attempts_count": 0}

    had_mistake = any(attempt[0] == 0 for attempt in attempts)
    currently_correct = attempts[-1][0] == 1

    return {
        "solved": True,
        "had_mistake": had_mistake,
        "corrected": had_mistake and currently_correct,  # исправил, если раньше лажал, а сейчас решил верно
        "attempts_count": len(attempts)
    }


# ==========================================
# РАБОТА С ИЗБРАННЫМ (FAVOURITES)
# ==========================================

def toggle_favourite(user_id: int, question_id: int, platform: str = "tg") -> bool:
    """
    Переключает статус избранного.
    Если вопроса не было в избранном — добавляет его (вернет True).
    Если был — удаляет его (вернет False).
    """
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        SELECT 1 FROM favourites 
        WHERE user_id = ? AND platform = ? AND question_id = ?
    ''', (user_id, platform, question_id))
    exists = cursor.fetchone()

    if exists:
        cursor.execute('''
            DELETE FROM favourites 
            WHERE user_id = ? AND platform = ? AND question_id = ?
        ''', (user_id, platform, question_id))
        is_added = False
        logger.debug(f"Удалено из избранного: {user_id} -> {question_id}")
    else:
        cursor.execute('''
            INSERT INTO favourites (user_id, platform, question_id) 
            VALUES (?, ?, ?)
        ''', (user_id, platform, question_id))
        is_added = True
        logger.debug(f"Добавлено в избранное: {user_id} -> {question_id}")

    conn.commit()
    conn.close()
    return is_added


def is_favourite(user_id: int, question_id: int, platform: str = "tg") -> bool:
    """Проверяет, добавлено ли задание в избранное."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT 1 FROM favourites 
        WHERE user_id = ? AND platform = ? AND question_id = ?
    ''', (user_id, platform, question_id))
    row = cursor.fetchone()
    conn.close()
    return row is not None


def get_user_favourites(user_id: int, platform: str = "tg") -> list:
    """Возвращает список ID всех избранных вопросов пользователя."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        SELECT question_id FROM favourites 
        WHERE user_id = ? AND platform = ?
        ORDER BY added_at DESC
    ''', (user_id, platform))
    rows = cursor.fetchall()
    conn.close()
    return [row[0] for row in rows]