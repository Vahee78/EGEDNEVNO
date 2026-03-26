from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton


def get_reply_kb():
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text="🎯 Изменить цель"), KeyboardButton(text="🕒 Сменить часовой пояс")]
        ],
        resize_keyboard=True
    )


def get_main_menu_kb():
    builder = InlineKeyboardBuilder()
    builder.button(text="📝 Решать задания", callback_data="play")
    builder.button(text="🏆 Моя лига", callback_data="my_league")
    builder.adjust(1)
    return builder.as_markup()


def get_question_kb(q_id: int, options: list, selected_indices: list = None):
    if selected_indices is None:
        selected_indices = []

    builder = InlineKeyboardBuilder()
    for idx, opt in enumerate(options):
        # Если индекс выбран, добавляем галочку
        prefix = "✅ " if idx in selected_indices else ""
        builder.button(text=f"{prefix}{opt}", callback_data=f"toggle_{q_id}_{idx}")

    # Кнопка подтверждения появляется всегда под вариантами
    builder.button(text="📤 Отправить ответ", callback_data=f"submit_{q_id}")
    builder.adjust(1)
    return builder.as_markup()


def get_post_answer_kb(q_id: int):
    builder = InlineKeyboardBuilder()
    builder.button(text="✨ Умный разбор от ИИ", callback_data=f"explain_{q_id}")
    builder.button(text="➡️ Следующее задание", callback_data="play")
    builder.button(text="🏠 В меню", callback_data="menu")
    builder.adjust(1)
    return builder.as_markup()


def get_targets_kb():
    builder = InlineKeyboardBuilder()
    for t in [70, 80, 90, 100]:
        builder.button(text=f"{t} баллов", callback_data=f"set_target_{t}")
    builder.adjust(2)
    return builder.as_markup()


def get_tz_kb():
    builder = InlineKeyboardBuilder()
    for tz in range(-2, 13):
        label = f"UTC{'+' if tz >= 0 else ''}{tz}"
        builder.button(text=label, callback_data=f"reg_tz_{tz}")
    builder.adjust(3)
    return builder.as_markup()
