import sqlite3

# Имя файла базы данных
DB_NAME = "users.db"


def init_db():
    """Инициализирует таблицу пользователей с поддержкой TG и VK."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    # Создаем таблицу с составным первичным ключом (user_id + platform)
    # Это позволяет избежать конфликтов, если ID в ТГ и ВК совпадут
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
            last_seen_date TEXT,
            PRIMARY KEY (user_id, platform)
        )
    ''')

    conn.commit()
    conn.close()


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
        "last_seen_date": None
    }


def update_user_data(user_id, data, platform="tg"):
    """Обновляет или вставляет данные пользователя (INSERT OR REPLACE)."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()

    cursor.execute('''
        INSERT OR REPLACE INTO users 
        (user_id, platform, username, full_name, score, xp, streak, target, timezone, last_solved_date, last_seen_date)
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
        data.get("last_seen_date")
    ))
    conn.commit()
    conn.close()


def get_all_users_for_notify():
    """Возвращает список всех пользователей с часовыми поясами для рассылки."""
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    # Теперь возвращаем и платформу, чтобы знать куда отправлять пуш
    cursor.execute('SELECT user_id, platform, timezone, last_solved_date FROM users WHERE timezone IS NOT NULL')
    rows = cursor.fetchall()
    conn.close()
    return rows