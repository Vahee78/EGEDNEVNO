from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command

import database as db
import data_content as content
import keyboards as kb
import engine
from data_content import get_streak_congrats
from handlers.utils import get_random_task, handle_streak_check, ask_gemini
from keyboards import get_after_explanation_kb

from loguru import logger

router = Router()

# Локальное хранилище сессий
active_sessions = {}


def format_task_text(q: dict) -> tuple[str, list]:
    """Адаптивное форматирование текста задания"""
    is_text_type = "answer_variants" in q and q["answer_variants"]

    # Если есть список опций (как в задании 16)
    if "options" in q and q["options"]:
        options_text = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
        option_numbers = [str(i + 1) for i in range(len(q['options']))]
        footer = "\n\n_Выбери правильные варианты (кнопками):_"
    else:
        # Для 5, 6, 7 заданий список опций обычно уже вшит в инструкцию или текст
        q['instruction'] = q['instruction'].replace("\n", "\n\n", 1)
        options_text = ""
        option_numbers = []
        footer = "⌨️ *Напиши ответ сообщением:* "

    text = f"📝 *Задание №{q['type']}*\n\n{q['instruction']}\n\n{options_text}{footer}"
    return text, option_numbers


async def start_new_task(user_id: int, message_or_call) -> None:
    """Универсальная функция запуска задания (для команды и для кнопки)"""
    trigger_type = "CALLBACK" if isinstance(message_or_call, CallbackQuery) else "COMMAND"
    logger.info(f"Запуск нового задания для пользователя {user_id} через {trigger_type}")

    was_reset = handle_streak_check(user_id)
    if was_reset:
        logger.warning(f"У пользователя {user_id} сгорел стрик! Потеряно XP: {was_reset}")
        msg_text = f"⚠️ *Ваш стрик сгорел за неактивность!* -{was_reset} XP"
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.message.answer(msg_text, parse_mode="Markdown")
        else:
            await message_or_call.answer(msg_text, parse_mode="Markdown")

    q = get_random_task()
    if not q:
        logger.error(f"Ошибка при получении задания из БД для {user_id} — база пуста или недоступна")
        msg = "Задания временно недоступны."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(msg)
        else:
            await message_or_call.answer(msg)
        return

    # Инициализируем сессию
    active_sessions[user_id] = {"task_data": q, "selected": [], "state": "solving"}
    logger.debug(f"Сессия создана для {user_id}. ID задания: {q['id']}, тип: {q['type']}")

    text, option_numbers = format_task_text(q)

    # Если это текстовое задание, клавиатура будет пустой или содержать только тех. кнопки
    is_text_type = "answer_variants" in q and q["answer_variants"]
    if is_text_type:
        reply_markup = None
    else:
        reply_markup = kb.get_question_kb(q["id"], option_numbers)

    if isinstance(message_or_call, CallbackQuery):
        logger.debug(f"Отправка задания {q['id']} пользователю {user_id} новым сообщением (после клика)")
        await message_or_call.answer()
        await message_or_call.message.answer(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        logger.debug(f"Отправка задания {q['id']} пользователю {user_id} ответом на команду")
        await message_or_call.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


# --- ХЕНДЛЕР: ОБРАБОТКА ТЕКСТОВОГО ОТВЕТА (5, 6, 7 задания) ---
@router.message(F.text, ~F.text.startswith("/"))
async def handle_text_answer(message: Message):
    user_id = message.from_user.id
    session = active_sessions.get(user_id)

    # Проверяем, что пользователь действительно решает текстовое задание
    if not session:
        logger.debug(f"Игнорируем текстовое сообщение от {user_id}: активная сессия отсутствует")
        return

    if session.get("state") != "solving":
        logger.debug(
            f"Игнорируем текстовое сообщение от {user_id}: стейт сессии не 'solving' (текущий: '{session.get('state')}')")
        return

    q = session["task_data"]
    if "answer_variants" not in q or not q["answer_variants"]:
        logger.debug(
            f"Игнорируем текстовое сообщение от {user_id}: текущее задание {q['id']} не предусматривает текстовый ввод")
        return

    # Обработка ввода
    user_ans = message.text.lower().strip().translate(str.maketrans('', '', '.,!?'))
    correct_variants = [v.lower().strip() for v in q["answer_variants"]]
    is_correct = user_ans in correct_variants

    logger.info(
        f"Пользователь {user_id} ответил текстом на задание {q['id']}. Ввод: '{user_ans}' | Ожидалось: {correct_variants} | Результат: {is_correct}")

    # Логика начисления очков
    user = db.get_user_data(user_id)
    today_str = datetime.now().strftime("%Y-%m-%d")
    old_score = user["score"]
    old_league = content.get_league(old_score)

    if is_correct:
        engine.add_user_xp(user, 1)
        res_text = f"✅ *Верно!*\nОтвет: `{q['answer_variants'][0]}`"
        if user["last_solved_date"] != today_str:
            user["streak"] += 1
            user["last_solved_date"] = today_str
            logger.info(f"Стрик пользователя {user_id} увеличен до {user['streak']} дней.")
            if streak_congrats := get_streak_congrats(user["streak"]):
                res_text = streak_congrats + "\n\n" + res_text
    else:
        engine.remove_user_xp(user, 1)
        correct_display = " / ".join(q["answer_variants"])
        res_text = f"❌ *Ошибка.*\n\nВаш ответ: `{user_ans}`\nПравильный: `{correct_display}`\n\nШтраф: -1 XP."

    db.update_user_data(user_id, user)
    new_league = content.get_league(user["score"])
    logger.debug(f"БД обновлена для {user_id}. Старый балл: {old_score} -> Новый балл: {user['score']}")

    # Меняем стейт, чтобы не спамить ответами
    session["state"] = "after_solve"

    await message.answer(res_text, reply_markup=kb.get_post_answer_kb(q["id"]), parse_mode="Markdown")

    if user["score"] > old_score and old_league["name"] != new_league["name"]:
        logger.info(f"Пользователь {user_id} перешел в новую лигу: {new_league['name']}!")
        promo = f"🎊 *УРОВЕНЬ ПОВЫШЕН!*\nЛига: {new_league['name']} {new_league['icon']}"
        await message.answer(promo, parse_mode="Markdown")


@router.message(Command("bot"))
async def cmd_bot(message: Message):
    logger.info(f"Команда /bot от {message.from_user.id}")
    await start_new_task(message.from_user.id, message)


@router.callback_query(F.data == "play")
async def send_question_callback(callback: CallbackQuery):
    logger.info(f"Клик на кнопку 'play' от {callback.from_user.id}")
    await start_new_task(callback.from_user.id, callback)


@router.callback_query(F.data.startswith("toggle_"))
async def toggle_option(callback: CallbackQuery):
    _, q_id, opt_idx = callback.data.split("_")
    opt_idx = int(opt_idx)
    user_id = callback.from_user.id

    session = active_sessions.get(user_id)
    if not session:
        logger.warning(f"Попытка переключения кнопок пользователем {user_id} без активной сессии")
        return await callback.answer("Задание завершено или устарело.")

    if session.get("state") != "solving" or str(session["task_data"]["id"]) != q_id:
        logger.warning(
            f"Устаревший клик toggle от {user_id}. Стейт: {session.get('state')}, ID в сессии: {session['task_data']['id']}, Получен: {q_id}")
        return await callback.answer("Задание завершено или устарело.")

    if opt_idx in session["selected"]:
        session["selected"].remove(opt_idx)
        logger.debug(f"Юзер {user_id} убрал вариант {opt_idx + 1}. Текущий выбор: {session['selected']}")
    else:
        session["selected"].append(opt_idx)
        logger.debug(f"Юзер {user_id} выбрал вариант {opt_idx + 1}. Текущий выбор: {session['selected']}")

    option_numbers = [str(i + 1) for i in range(len(session["task_data"]["options"]))]
    await callback.message.edit_reply_markup(
        reply_markup=kb.get_question_kb(int(q_id), option_numbers, session["selected"])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("submit_"))
async def submit_answer(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[1])
    user_id = callback.from_user.id
    session = active_sessions.get(user_id)

    if not session or session.get("state") != "solving":
        logger.warning(f"Попытка отправки ответа пользователем {user_id} для неактивной сессии")
        return await callback.answer("Уже решено.")

    if str(session["task_data"]["id"]) != str(q_id):
        logger.warning(
            f"Конфликт ID заданий при отправке у {user_id}. В сессии: {session['task_data']['id']}, Пришло: {q_id}")
        return await callback.answer("Ошибка сессии.")

    if not session["selected"]:
        logger.debug(f"Пользователь {user_id} нажал проверить без выбора вариантов")
        return await callback.answer("Выбери хотя бы один вариант!", show_alert=True)

    user = db.get_user_data(user_id)
    q = session["task_data"]

    selected_sorted = sorted(session["selected"])
    correct_sorted = sorted(q["correct_indexes"])
    is_correct = selected_sorted == correct_sorted

    logger.info(
        f"Пользователь {user_id} отправил ответ на задание {q_id}. Выбрано: {selected_sorted} | Верно: {correct_sorted} | Результат: {is_correct}")

    # Формируем наглядный разбор ответов
    options_text = ""
    for i, opt in enumerate(q['options']):
        is_selected = i in session["selected"]
        is_opt_correct = i in q["correct_indexes"]

        line = opt
        if is_selected: line = f"*{line}*"
        if is_opt_correct:
            line += " ✅"
        elif is_selected:
            line += " ❌"
        options_text += f"{i + 1}. {line}\n"

    today_str = datetime.now().strftime("%Y-%m-%d")
    old_score = user["score"]
    old_league = content.get_league(old_score)

    if is_correct:
        engine.add_user_xp(user, 1)
        res_text = f"✅ *Верно!*\n\n{options_text}" + (
            f"\n🆙 Новый балл: {user['score']}!" if user["score"] > old_score else "")
        if user["last_solved_date"] != today_str:
            user["streak"] += 1
            user["last_solved_date"] = today_str
            logger.info(f"Стрик пользователя {user_id} увеличен до {user['streak']} дней.")
            if streak_congrats := get_streak_congrats(user["streak"]):
                res_text = streak_congrats + "\n\n" + res_text
    else:
        engine.remove_user_xp(user, 1)
        res_text = f"❌ *Ошибка.*\n\n{options_text}\nШтраф: -1 XP." + (
            f"\n📉 Балл упал до {user['score']}." if user["score"] < old_score else "")

    # Меняем стейт сессии, чтобы предотвратить повторную отправку
    session["state"] = "after_solve"

    new_league = content.get_league(user["score"])
    db.update_user_data(user_id, user)
    logger.debug(f"БД обновлена для {user_id}. Старый балл: {old_score} -> Новый балл: {user['score']}")

    await callback.message.edit_text(res_text, reply_markup=kb.get_post_answer_kb(q_id), parse_mode="Markdown")

    if user["score"] > old_score and old_league["name"] != new_league["name"]:
        logger.info(f"Пользователь {user_id} перешел в новую лигу: {new_league['name']}!")
        promo_text = (f"🎊 *УРОВЕНЬ ПОВЫШЕН!*\nТы покинул лигу {old_league['name']} {old_league['icon']}.\n"
                      f"Твой новый дом — {new_league['name']} {new_league['icon']}.\n_{new_league['desc']}_")
        await callback.message.answer(promo_text, parse_mode="Markdown")


@router.callback_query(F.data.startswith("explain_"))
async def explain_gemini(callback: CallbackQuery):
    user_id = callback.from_user.id
    logger.info(f"Запрос ИИ-разбора от {callback.from_user.first_name} ({user_id})")

    q_id = callback.data.split("_")[1]
    session = active_sessions.get(user_id)

    if not session:
        logger.error(f"Не удалось подготовить разбор для {user_id}: сессия отсутствует в памяти")
        return await callback.answer("Данные задания утеряны.")

    if str(session["task_data"]["id"]) != q_id:
        logger.error(
            f"Несоответствие ID задания в сессии ({session['task_data']['id']}) и запросе ({q_id}) для {user_id}")
        return await callback.answer("Данные задания утеряны.")

    q = session["task_data"]
    await callback.answer("Готовлю разбор...")

    try:
        correct_labels = [q["options"][i] for i in q["correct_indexes"]] if q.get('options') else str(
            *q['answer_variants'])

        prompt = f"""Ты — крутой дружелюбный учитель русского, который очень ценит краткость! 🔥
        Твоя задача: подсказать правильный путь, используя понятные объяснения и капельку поддержки и юмора. 
        Никаких приветствий (Отличная задача!), сразу приступай к делу!

        ДАННЫЕ:
        Задание: {q['instruction']}
        Варианты: {q.get('options', q.get('text', 'см. задание'))}

        ИНСТРУКЦИЯ:
        1. Пройдись по всему заданию по порядку и объясни почему именно {correct_labels} - правильные ответы
        2. Никаких приветсвтий, вводных слов и итогов. Сразу приступай к объяснению, краткость - сестра таланта!
        3. Оформление: Только *жирный* шрифт (именно *так*, а не **так**) для выделение важных слов, 
        а также нумерация, если в задании несколько вариантов (например, 5)
        4. Стиль: Дружелюбный и бодрый. Объясняй кратко, но понятно.
        5. Пользователь ошибся и выбрал не {correct_labels}.
        Подбодри его в конце, например, скажи, что всё получится или пошути про что-то, связанное с текстом задания
        """

        logger.debug(f"Отправка запроса к Gemini API для пользователя {user_id}. Вопрос: {q['id']}")
        explanation = await ask_gemini(prompt)

        # Корректируем разметку
        explanation += "*" if explanation.count("*") % 2 else ""

        logger.info(f"Разбор от Gemini успешно получен для {user_id}. Длина текста: {len(explanation)}")

        if "⚠️" in explanation:
            reply_markup = get_after_explanation_kb(q_id)
            logger.warning(f"ИИ вернул предупреждение во время генерации разбора для {user_id}")
        else:
            reply_markup = get_after_explanation_kb()
            # Очищаем сессию, так как задание полностью разобрано
            active_sessions.pop(user_id, None)
            logger.debug(f"Сессия пользователя {user_id} удалена после успешного объяснения.")

        await callback.message.answer(f"✨ *Разбор от ИИ:*\n\n{explanation}", reply_markup=reply_markup,
                                      parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Исключение при генерации разбора ИИ для {user_id}: {e}", exc_info=True)
        reply_markup = get_after_explanation_kb(q_id)
        await callback.message.answer("⚠️ Ошибка разбора.", reply_markup=reply_markup, parse_mode="Markdown")