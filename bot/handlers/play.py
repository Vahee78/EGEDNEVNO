from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
import data_content as content
import keyboards as kb
import engine
from handlers.utils import get_random_task, handle_streak_check, ask_gemini

router = Router()

# Локальное хранилище сессий для этого модуля
active_sessions = {}


def format_task_text(q: dict) -> tuple[str, list]:
    """Вспомогательная функция, убирающая дублирование кода при отправке задания"""
    options_text = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
    text = f"📝 *Задание №{q['type']}*\n\n{q['instruction']}\n\n{options_text}\n\n_Выбери правильные варианты:_ "
    option_numbers = [str(i + 1) for i in range(len(q['options']))]
    return text, option_numbers


async def start_new_task(user_id: int, message_or_call) -> None:
    """Универсальная функция запуска задания (для команды и для кнопки)"""
    was_reset = handle_streak_check(user_id)
    if was_reset:
        await message_or_call.answer(f"⚠️ *Ваш стрик сгорел за неактивность!* -{was_reset} XP", parse_mode="Markdown")

    q = get_random_task()
    if not q:
        # aiogram позволяет ответить по-разному в зависимости от того, Message это или CallbackQuery
        msg = "Задания временно недоступны."
        if isinstance(message_or_call, CallbackQuery):
            await message_or_call.answer(msg)
        else:
            await message_or_call.answer(msg)
        return

    active_sessions[user_id] = {"task_data": q, "selected": []}
    text, option_numbers = format_task_text(q)
    reply_markup = kb.get_question_kb(q["id"], option_numbers)

    if isinstance(message_or_call, CallbackQuery):
        await message_or_call.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    else:
        await message_or_call.answer(text, reply_markup=reply_markup, parse_mode="Markdown")


@router.message(Command("bot"))
async def cmd_bot(message: Message):
    await start_new_task(message.from_user.id, message)


@router.callback_query(F.data == "play")
async def send_question_callback(callback: CallbackQuery):
    await start_new_task(callback.from_user.id, callback)


@router.callback_query(F.data.startswith("toggle_"))
async def toggle_option(callback: CallbackQuery):
    _, q_id, opt_idx = callback.data.split("_")
    opt_idx = int(opt_idx)

    session = active_sessions.get(callback.from_user.id)
    if not session or str(session["task_data"]["id"]) != str(q_id):
        return await callback.answer("Задание устарело.")

    if opt_idx in session["selected"]:
        session["selected"].remove(opt_idx)
    else:
        session["selected"].append(opt_idx)

    option_numbers = [str(i + 1) for i in range(len(session["task_data"]["options"]))]
    await callback.message.edit_reply_markup(
        reply_markup=kb.get_question_kb(q_id, option_numbers, session["selected"])
    )
    await callback.answer()


@router.callback_query(F.data.startswith("submit_"))
async def submit_answer(callback: CallbackQuery):
    q_id = int(callback.data.split("_")[1])
    session = active_sessions.get(callback.from_user.id)

    if not session or str(session["task_data"]["id"]) != str(q_id):
        return await callback.answer("Ошибка сессии.")

    if not session["selected"]:
        return await callback.answer("Выбери хотя бы один вариант!", show_alert=True)

    db_data = db.get_user_data(callback.from_user.id)
    q = session["task_data"]
    is_correct = sorted(session["selected"]) == sorted(q["correct_indexes"])

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
    old_score = db_data["score"]
    old_league = content.get_league(old_score)

    if is_correct:
        if db_data["last_solved_date"] != today_str:
            db_data["streak"] += 1
            db_data["last_solved_date"] = today_str
        engine.add_user_xp(db_data, 1)
        res_text = f"✅ *Верно!*\n\n{options_text}" + (
            f"\n🆙 Новый балл: {db_data['score']}!" if db_data["score"] > old_score else "")
    else:
        engine.remove_user_xp(db_data, 1)
        db_data["streak"] = 0
        res_text = f"❌ *Ошибка.*\n\n{options_text}\nШтраф: -1 XP." + (
            f"\n📉 Балл упал до {db_data['score']}." if db_data["score"] < old_score else "")

    new_league = content.get_league(db_data["score"])
    db.update_user_data(callback.from_user.id, db_data)

    await callback.message.edit_text(res_text, reply_markup=kb.get_post_answer_kb(q_id), parse_mode="Markdown")

    if db_data["score"] > old_score and old_league["name"] != new_league["name"]:
        promo_text = (f"🎊 *УРОВЕНЬ ПОВЫШЕН!*\nТы покинул лигу {old_league['name']} {old_league['icon']}.\n"
                      f"Твой новый дом — {new_league['name']} {new_league['icon']}.\n_{new_league['desc']}_")
        await callback.message.answer(promo_text, parse_mode="Markdown")


@router.callback_query(F.data.startswith("explain_"))
async def explain_gemini(callback: CallbackQuery):
    q_id = callback.data.split("_")[1]
    session = active_sessions.get(callback.from_user.id)

    if not session or str(session["task_data"]["id"]) != str(q_id):
        return await callback.answer("Данные задания утеряны.")

    q = session["task_data"]
    await callback.answer("Готовлю разбор...")

    builder = InlineKeyboardBuilder()
    builder.button(text="➡️ Решать еще", callback_data="play")
    builder.button(text="🏠 Меню", callback_data="menu")
    builder.adjust(1)

    try:
        correct_labels = [q["options"][i] for i in q["correct_indexes"]] if q["options"] else "смотрите текст задания"
        prompt = (f"Разбери задание ЕГЭ по русскому: {q['instruction']}\nВарианты: {q['options']}\n"
                  f"Правильные ответы: {correct_labels}\nОбъясни очень кратко и по делу.")

        explanation = await ask_gemini(prompt)
        await callback.message.answer(f"✨ *Разбор от ИИ:*\n\n{explanation}", reply_markup=builder.as_markup(),
                                      parse_mode="Markdown")
        active_sessions.pop(callback.from_user.id, None)
    except Exception as e:
        print(e)
        await callback.message.answer("⚠️ Ошибка разбора.", reply_markup=builder.as_markup())
