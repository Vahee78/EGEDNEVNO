import sqlite3
from loguru import logger

# Имя файла базы данных
DB_NAME = "users.db"


def init_db():
    """Инициализирует таблицу пользователей."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Создаем таблицу с составным первичным ключом (user_id + platform)
    # notifications_enabled хранит 1 (включено, по умолчанию) или 0 (выключено)
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
            PRIMARY KEY (user_id, platform)
        )
    ''')

    # Миграция базы данных: если таблица уже существовала, но в ней нет новой колонки notifications_enabled,
    # мы ее автоматически добавим, чтобы у тебя не упал старый файл БД.
    try:
        cursor.execute("ALTER TABLE users ADD COLUMN notifications_enabled INTEGER DEFAULT 1")
        logger.info("База данных обновлена: успешно добавлена колонка notifications_enabled")
    except sqlite3.OperationalError:
        # Эта ошибка возникает, если колонка уже существует в таблице. Просто игнорируем ее.
        pass

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
        "notifications_enabled": 1
    }


def update_user_data(user_id: int, data: dict, platform: str = "tg"):
    """
    Безопасно сохраняет или обновляет данные пользователя (UPSERT).
    Если пользователь существует, обновляются только переданные поля,
    а старые данные гарантированно не затираются.
    """
    logger.debug(f"Сохранение/обновление данных пользователя {user_id} ({platform})")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Извлекаем значения из словаря. Если ключа нет в словаре data,
    # мы передаем None, чтобы SQLite знала, что это поле обновлять не нужно.
    username = data.get("username")
    full_name = data.get("full_name")
    score = data.get("score")
    xp = data.get("xp")
    streak = data.get("streak")
    target = data.get("target")
    timezone = data.get("timezone")
    last_solved_date = data.get("last_solved_date")
    notifications_enabled = data.get("notifications_enabled")

    # В блоке VALUES мы вставляем дефолтные значения (score=40, target=80, notifications_enabled=1),
    # если запись создается впервые.
    # В блоке ON CONFLICT (user_id, platform) DO UPDATE SET мы обновляем колонки
    # только ЕСЛИ нам передали новое значение (используем COALESCE, чтобы не затереть старое значение на None).
    cursor.execute('''
        INSERT INTO users (
            user_id, platform, username, full_name, score, xp, 
            streak, target, timezone, last_solved_date, notifications_enabled
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, platform) DO UPDATE SET
            username = COALESCE(?, username),
            full_name = COALESCE(?, full_name),
            score = COALESCE(?, score),
            xp = COALESCE(?, xp),
            streak = COALESCE(?, streak),
            target = COALESCE(?, target),
            timezone = COALESCE(?, timezone),
            last_solved_date = COALESCE(?, last_solved_date),
            notifications_enabled = COALESCE(?, notifications_enabled)
    ''', (
        # Блок INSERT (VALUES)
        user_id, platform, username, full_name,
        score if score is not None else 40,
        xp if xp is not None else 0,
        streak if streak is not None else 0,
        target if target is not None else 80,
        timezone, last_solved_date,
        notifications_enabled if notifications_enabled is not None else 1,

        # Блок UPDATE (DO UPDATE SET) — связывается с COALESCE(?, column_name)
        username, full_name, score, xp, streak, target, timezone, last_solved_date, notifications_enabled
    ))

    conn.commit()
    conn.close()
    logger.debug(f"Данные пользователя {user_id} ({platform}) успешно сохранены/обновлены через UPSERT.")


def get_all_users_for_notify():
    """Возвращает список активных пользователей с включенными уведомлениями для рассылки."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Выбираем только тех, у кого заполнена таймзона И включены уведомления (notifications_enabled = 1)
    cursor.execute('''
        SELECT user_id, platform, timezone, last_solved_date 
        FROM users 
        WHERE timezone IS NOT NULL AND notifications_enabled = 1
    ''')
    rows = cursor.fetchall()
    conn.close()
    return rows
