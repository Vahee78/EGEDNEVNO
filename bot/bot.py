from loguru import logger
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery

import data_content
import core
import keyboards as kb

router = Router()


async def start_new_task(user_id: int, message_or_call, task_type: str = "def") -> None:
    """Переходник бота: принимает триггер aiogram и выплевывает визуал юзеру"""
    trigger_type = "CALLBACK" if isinstance(message_or_call, CallbackQuery) else "COMMAND"
    logger.info(f"Запуск задания для {user_id} через {trigger_type}")

    # 1. Запрашиваем данные у независимого ядра
    core_result = core.prepare_new_task(user_id, task_type)

    # 2. Определяем, как отвечать (на сообщение или на колбэк)
    target = message_or_call.message if isinstance(message_or_call, CallbackQuery) else message_or_call

    # Обработка ошибки
    if core_result["status"] == "error_empty":
        msg = data_content.get_error_no_tasks_text()
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(msg)
        await target.answer(msg)
        return

    # 3. Собираем текст в модуле контента, передавая туда сухие данные из ядра
    q = core_result["task_data"]
    text, option_numbers = data_content.format_task_text(q, core_result["is_favourite"])

    # 4. Собираем клавиатуру на фронтенде бота
    is_text_type = "answer_variants" in q and q["answer_variants"]
    reply_markup = None if is_text_type else kb.get_question_kb(q["id"], option_numbers)

    # 5. Отправляем готовый визуал в Телеграм
    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.answer()

    await target.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.message(F.text == "/menu")
async def cmd_menu(message: Message):
    logger.debug(f"Команда /menu от {message.from_user.id}")
    menu_data = core.get_menu_data(message.from_user.id)
    text = data_content.render_menu_text(menu_data)
    await message.answer(text, reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")


@router.message(F.text == "/bot")
async def cmd_bot(message: Message):
    logger.debug(f"Команда /bot от {message.from_user.id}")
    await start_new_task(message.from_user.id, message)


@router.callback_query(F.data.startswith("play_"))
async def send_question_callback(callback: CallbackQuery):
    logger.info(f"Клик на кнопку 'play' от {callback.from_user.id}")
    type_of_task = callback.data.split("_")[1]
    await start_new_task(callback.from_user.id, callback, type_of_task)

