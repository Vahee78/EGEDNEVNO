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


def update_user_data(user_id, data, platform="tg"):
    """Обновляет или вставляет данные пользователя (INSERT OR REPLACE)."""
    logger.debug(f"Сохранение/обновление данных пользователя {user_id} ({platform})")
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, platform, username, full_name, score, xp, streak, target, timezone, last_solved_date, notifications_enabled)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ''', (
        user_id,
        platform,
        data.get("username"),
        data.get("full_name"),
        data.get("score", 40),
        data.get("xp", 0),
        data.get("streak", 0),
        data.get("target", 80),
        data.get("timezone"),
        data.get("last_solved_date"),
        data.get("notifications_enabled", 1)  # Записываем значение флага (по умолчанию 1)
    ))
    conn.commit()
    conn.close()


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


def disable_notifications(user_id: int, platform: str = "tg") -> bool:
    """
    Мягко отключает рассылку уведомлений для пользователя в БД.
    Возвращает True в случае успеха и False в случае ошибки.
    """
    logger.info(f"Отключение уведомлений для {user_id} ({platform}) в базе данных...")
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()

        cursor.execute('''
            UPDATE users 
            SET notifications_enabled = 0 
            WHERE user_id = ? AND platform = ?
        ''', (user_id, platform))

        rows_affected = cursor.rowcount
        conn.commit()
        conn.close()

        if rows_affected > 0:
            logger.info(f"Уведомления для пользователя {user_id} успешно переведены в режим OFF.")
            return True
        else:
            logger.warning(f"Пользователь {user_id} ({platform}) не был найден в БД для изменения статуса уведомлений.")
            return False

    except Exception as e:
        logger.error(f"Не удалось выключить уведомления для {user_id} в БД из-за ошибки: {e}")
        return False
