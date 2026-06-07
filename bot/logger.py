import sys
from loguru import logger


def init_logger():
    # Настраиваем логирование
    logger.remove()  # Удаляем стандартный блеклый вывод

    # 1. Красивый вывод в консоль (stdout)
    logger.add(
        sys.stdout,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{message}</cyan>",
        level="DEBUG"
    )

    # 2. Запись в файл с авто-очисткой
    logger.add(
        "logs/new_bot.log",
        rotation="5 MB",  # Как только файл разрастется до 5 МБ — создастся новый
        retention="10 days",  # Логи старше 10 дней будут сами удаляться
        level="INFO",  # В файл пишем только важные вещи, без дебага
        encoding="utf-8"
    )

    logger.success("🚀 Логгер успешно инициализирован с ротацией файлов!")
