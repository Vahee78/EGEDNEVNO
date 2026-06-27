from loguru import logger
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command, CommandStart

from database import get_user_data, update_user_data
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


@router.callback_query(F.data.startswith("toggle_"))
async def cb_toggle_option(callback: CallbackQuery):
    _, q_id, opt_idx = callback.data.split("_")
    opt_idx = int(opt_idx)
    user_id = callback.from_user.id
    res = core.toggle_option(user_id, q_id, opt_idx)

    if res["status"] != "error":
        await callback.message.edit_reply_markup(
            reply_markup=kb.get_question_kb(int(q_id), res["option_numbers"], res["selected"])
        )
        await callback.answer()
    else:
        await callback.answer("Задание завершено или устарело.")


# --- ХЕНДЛЕР: ОБРАБОТКА ТЕКСТОВОГО ОТВЕТА (5, 6, 7 задания) ---
@router.message(F.text, ~F.text.startswith("/"))
async def handle_text_answer(message: Message):
    user_id = message.from_user.id
    user_ans = message.text.lower().strip().translate(str.maketrans('', '', '.,!?'))
    res = core.process_text_answer(user_id, user_ans)

    if res["status"] == "error":
        return

    # --- Слой отображения: Оформление основного текста сообщения ---

    correct_ans = " / ".join(res["answer_variants"])
    if res["is_correct"]:
        res_text = f"✅ *Верно!*\nОтвет: `{correct_ans}`"
        if res["new_score"] > res["old_score"]:
            res_text += f"\n🆙 Новый балл: {res['new_score']}!"

        # Добавляем поздравление со стриком, если он вырос
        if res["streak_increased"]:
            if streak_congrats := data_content.get_streak_congrats(res["current_streak"]):
                res_text = f"{streak_congrats}\n\n{res_text}"
    else:
        res_text = f"❌ *Ошибка.*\n\nВаш ответ: `{user_ans}`\nПравильный: `{correct_ans}`\n\nШтраф: -1 XP."
        if res["new_score"] < res["old_score"]:
            res_text += f"\n📉 Балл упал до {res['new_score']}."


    await message.answer(res_text, reply_markup=kb.get_post_answer_kb(res["id"], user_id=user_id), parse_mode="Markdown")

    # --- Оформление пуша о новой лиге ---
    old_league = res["old_league"]
    new_league = res["new_league"]
    if res["new_score"] > res["old_score"] and old_league["name"] != new_league["name"]:
        logger.info(f"Пользователь {user_id} перешел в лигу {new_league['name']}")
        promo_text = data_content.get_new_league_congrats(old_league, new_league)
        await message.answer(promo_text, parse_mode="Markdown")


# --- ОБРАБОТКА ОТВЕТА (с вариантами) ---
@router.callback_query(F.data.startswith("submit_"))
async def cb_submit_answer(callback: CallbackQuery):
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

    await callback.answer()


@router.message(CommandStart())
async def cmd_start(message: Message):
    user = core.update_user_names(message.from_user.id, message.from_user.username, message.from_user.full_name)

    await message.answer(f"👋 Привет, {message.from_user.first_name}! Бот активирован!")

    if user["timezone"] is None:
        await message.answer("Выберите свой часовой пояс (МСК = UTC+3):", reply_markup=kb.get_tz_kb())
    else:
        menu_data = core.get_menu_data(message.from_user.id)
        text = data_content.render_menu_text(menu_data)
        await message.answer(text, reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    logger.debug(f"Команда /menu от {message.from_user.id}")
    menu_data = core.get_menu_data(message.from_user.id)
    text = data_content.render_menu_text(menu_data)
    await message.answer(text, reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")


@router.message(Command("bot"))
async def cmd_bot(message: Message):
    logger.debug(f"Команда /bot от {message.from_user.id}")
    await start_new_task(message.from_user.id, message)


@router.callback_query(F.data == "menu")
async def cb_menu(callback: CallbackQuery):
    logger.debug(f"Callback 'menu' от {callback.from_user.id}")
    menu_data = core.get_menu_data(callback.from_user.id)
    text = data_content.render_menu_text(menu_data)
    await callback.message.answer(text, reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(F.data.startswith("play_"))
async def cb_send_question(callback: CallbackQuery):
    logger.info(f"Callback 'play' от {callback.from_user.id}")
    type_of_task = callback.data.split("_")[1]
    await start_new_task(callback.from_user.id, callback, type_of_task)


# ==========================================
# НАСТРОЙКИ
# ==========================================


@router.message(Command("settings"))
async def cmd_settings(message: Message):
    await message.answer("⚙️ Настройки:", reply_markup=kb.get_settings_kb(), parse_mode="Markdown")


@router.callback_query(F.data == "change_target")
async def show_menu(callback: CallbackQuery):
    await callback.message.edit_text("Выберите желаемый балл на ЕГЭ:", reply_markup=kb.get_targets_kb())


@router.callback_query(F.data == "change_tz")
async def show_menu(callback: CallbackQuery):
    await callback.message.edit_text("Выберите часовой пояс:", reply_markup=kb.get_tz_kb())


@router.callback_query(F.data.startswith(("reg_tz_", "set_target_")))
async def settings_callbacks(callback: CallbackQuery):
    user = get_user_data(callback.from_user.id)
    if callback.data.startswith("reg_tz_"):
        user["timezone"] = int(callback.data.split("_")[2])
        msg = "Часовой пояс сохранен!"
    else:
        user["target"] = int(callback.data.split("_")[2])
        msg = f"Цель изменена на {user['target']}!"

    update_user_data(callback.from_user.id, user)
    await callback.answer(msg, show_alert=True)
    await callback.message.edit_text("⚙️ Настройки:", reply_markup=kb.get_settings_kb(), parse_mode="Markdown")
