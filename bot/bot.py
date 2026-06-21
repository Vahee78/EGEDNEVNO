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
        msg = "Задания временно недоступны."
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


@router.callback_query(F.data.startswith("submit_"))
async def submit_answer(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id

    # Отправляем на "грязную работу" в core
    res = core.process_answer_submission(user_id, q_id)

    # Обработка ошибок валидации сессии
    if res["status"] == "error":
        if res["reason"] == "already_solved":
            return await callback.answer("Уже решено.")
        if res["reason"] == "session_conflict":
            return await callback.answer("Ошибка сессии.")
        if res["reason"] == "none_selected":
            return await callback.answer("Выбери хотя бы один вариант!", show_alert=True)

    # --- Слой отображения: Оформление вариантов ответов ---
    options_text = ""
    for i, opt in enumerate(res["options"]):
        is_selected = i in res["selected"]
        is_opt_correct = i in res["correct_indexes"]

        line = opt
        if is_selected:
            line = f"*{line}*"
        if is_opt_correct:
            line += " ✅"
        elif is_selected:
            line += " ❌"
        options_text += f"{i + 1}. {line}\n"

    # --- Слой отображения: Оформление основного текста сообщения ---

    if res["is_correct"]:
        res_text = f"✅ *Верно!*\n\n{options_text}"
        if res["new_score"] > res["old_score"]:
            res_text += f"\n🆙 Новый балл: {res['new_score']}!"

        # Добавляем поздравление со стриком, если он вырос
        if res["streak_increased"]:
            if streak_congrats := data_content.get_streak_congrats(res["current_streak"]):
                res_text = f"{streak_congrats}\n\n{res_text}"
    else:
        res_text = f"❌ *Ошибка.*\n\n{options_text}\nШтраф: -1 XP."
        if res["new_score"] < res["old_score"]:
            res_text += f"\n📉 Балл упал до {res['new_score']}."

    # Отправляем красиво оформленный ответ
    await callback.message.edit_text(
        res_text,
        reply_markup=kb.get_post_answer_kb(q_id, user_id=user_id),
        parse_mode="Markdown"
    )

    # --- Слой отображения: Оформление пуша о новой лиге ---
    old_league = res["old_league"]
    new_league = res["new_league"]
    if res["new_score"] > res["old_score"] and old_league["name"] != new_league["name"]:
        logger.info(f"Пользователь {user_id} перешел в лигу {new_league['name']}")
        promo_text = data_content.get_new_league_congrats(old_league, new_league)
        await callback.message.answer(promo_text, parse_mode="Markdown")
        pass

    await callback.answer()


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

