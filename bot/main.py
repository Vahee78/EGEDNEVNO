import asyncio
import random
import aiohttp
import os
import json
from datetime import datetime, timedelta, timezone
from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery, BotCommand
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

import config
import database as db
import data_content as content
import keyboards as kb
import engine

bot = Bot(token=config.BOT_TOKEN)
dp = Dispatcher()

active_sessions = {}


def get_random_task():
    """Выбирает случайное задание из папки tasks/"""
    try:
        tasks_dir = "tasks"
        files = [f for f in os.listdir(tasks_dir) if f.startswith("task_") and f.endswith(".json")]
        if not files:
            return None

        chosen_file = random.choice(files)
        with open(os.path.join(tasks_dir, chosen_file), 'r', encoding='utf-8') as f:
            tasks_list = json.load(f)
            task = random.choice(tasks_list)
            task["type"] = chosen_file.split('_')[1].split('.')[0]
            if int(chosen_file.split('_')[1].split('.')[0]) == 4:
                task["instruction"] = "Укажите варианты ответов, в которых верно выделена буква, обозначающая ударный гласный звук. Запишите номера ответов."
            return task
    except Exception as e:
        print(f"Ошибка загрузки задания: {e}")
        return None


# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def handle_streak_check(user_id: int) -> bool:
    """Централизованная проверка стрика с защитой от бесконечного списания."""
    user = db.get_user_data(user_id)
    was_reset = engine.check_and_reset_streak(user)
    if was_reset:
        # Ставим вчерашнюю дату, чтобы списание происходило только 1 раз до следующей попытки
        yesterday_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        user['last_solved_date'] = yesterday_str
        db.update_user_data(user_id, user)
    return was_reset


def get_menu_text(user_id: int) -> str:
    was_reset = handle_streak_check(user_id)
    user = db.get_user_data(user_id)  # Перечитываем данные после возможного сброса стрика

    league = content.get_league(user["score"])
    today_str = datetime.now().strftime("%Y-%m-%d")
    status = "✅ Решено" if user['last_solved_date'] == today_str else "❌ Не решено"
    max_xp = engine.get_max_xp(user["score"])
    streak_icon = engine.get_streak_icon(user["streak"])

    reset_msg = "\n\n⚠️ *Ваш стрик сгорел!* -5 XP" if was_reset else ""

    return (
        f"📊 *Ваша статистика:*\n"
        f"{league['icon']} Лига: {league['name']} ({league['desc']})\n"
        f"🎯 Цель: {user['target']} | 🏆 Балл: {user['score']}\n"
        f"{streak_icon} Стрик: {user['streak']} | 🛤 XP: {user['xp']}/{max_xp}\n"
        f"📅 Сегодня: {status}{reset_msg}"
    )


async def ask_gemini(prompt: str) -> str:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{config.GEMINI_MODEL_NAME}:generateContent?key={config.GEMINI_API_KEY}"

    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}) as resp:
                # Если API вернул ошибку (403, 404, 500 и т.д.)
                if resp.status != 200:
                    error_text = await resp.text()
                    print(f"Gemini API Error {resp.status}: {error_text}")
                    return "⚠️ Ошибка API."

                data = await resp.json()

                # Безопасное извлечение текста через .get()
                candidates = data.get("candidates", [])
                if not candidates:
                    # Такое бывает, если сработал фильтр безопасности контента
                    return "⚠️ ИИ не смог сгенерировать ответ."

                return candidates[0]["content"]["parts"][0]["text"]

    except Exception as e:
        print(f"Ошибка при вызове Gemini: {e}")
        return "⚠️ Не удалось связаться с ИИ."


# --- ОБРАБОТЧИКИ КОМАНД И REPLY КНОПОК ---
@dp.message(CommandStart())
async def cmd_start(message: Message):
    user = db.get_user_data(message.from_user.id)
    user["username"] = message.from_user.username
    user["full_name"] = message.from_user.full_name
    user["last_seen_date"] = datetime.now().strftime("%Y-%m-%d")

    db.update_user_data(message.from_user.id, user)

    await message.answer(f"👋 Привет, {message.from_user.first_name}! Система ЕГЭДНЕВНО активирована!\n\n"
                         f"🤖 В связи с внедрением сервиса в ВК могут наблюдаться неисправности.",
                         reply_markup=kb.get_reply_kb())

    if user["timezone"] is None:
        await message.answer("Выберите свой часовой пояс (МСК = UTC+3):", reply_markup=kb.get_tz_kb())
    else:
        await message.answer(get_menu_text(message.from_user.id), reply_markup=kb.get_main_menu_kb(),
                             parse_mode="Markdown")


@dp.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(get_menu_text(message.from_user.id), reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")


@dp.message(Command("bot"))
async def cmd_bot(message: Message):
    was_reset = handle_streak_check(message.from_user.id)
    if was_reset:
        await message.answer("⚠️ *Ваш стрик сгорел за неактивность!* -5 XP", parse_mode="Markdown")

    q = get_random_task()
    if not q:
        return await message.answer("Задания временно недоступны.")

    active_sessions[message.from_user.id] = {"task_data": q, "selected": []}

    # Формируем текст вариантов прямо в сообщении, а в клавиатуру отдаем цифры
    options_text = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
    text = f"📝 *Задание №{q['type']}*\n\n{q['instruction']}\n\n{options_text}\n\n_Выбери правильные варианты:_ "
    option_numbers = [str(i + 1) for i in range(len(q['options']))]

    await message.answer(text, reply_markup=kb.get_question_kb(q["id"], option_numbers), parse_mode="Markdown")


@dp.message(F.text == "🎯 Изменить цель")
async def change_target_msg(message: Message):
    await message.answer("Выберите желаемый балл на ЕГЭ:", reply_markup=kb.get_targets_kb())


@dp.message(F.text == "🕒 Сменить часовой пояс")
async def change_tz_msg(message: Message):
    await message.answer("Выберите часовой пояс:", reply_markup=kb.get_tz_kb())


# --- ОБРАБОТЧИКИ CALLBACK ---
@dp.callback_query(F.data == "menu")
async def show_menu(callback: CallbackQuery):
    await callback.message.edit_text(get_menu_text(callback.from_user.id), reply_markup=kb.get_main_menu_kb(),
                                     parse_mode="Markdown")


@dp.callback_query(F.data == "my_league")
async def show_league_info(callback: CallbackQuery):
    user = db.get_user_data(callback.from_user.id)
    league = content.get_league(user["score"])
    next_info = f"\n\n🔜 До следующей лиги осталось баллов: *{league['next'] - user['score']}*" if league[
                                                                                                      "next"] <= 100 else "\n\n🌟 Вы на вершине!"

    text = f"🏆 *Ваша лига: {league['name']}*\n\n{league['icon']} {league['desc']}\n\nТекущий балл: {user['score']}{next_info}"
    builder = InlineKeyboardBuilder().button(text="🔙 Назад", callback_data="menu")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@dp.callback_query(F.data == "play")
async def send_question(callback: CallbackQuery):
    was_reset = handle_streak_check(callback.from_user.id)
    if was_reset:
        await callback.message.answer("⚠️ *Ваш стрик сгорел за неактивность!* -5 XP", parse_mode="Markdown")

    q = get_random_task()
    if not q:
        return await callback.answer("Задания не найдены.")

    active_sessions[callback.from_user.id] = {"task_data": q, "selected": []}

    options_text = "\n".join([f"{i + 1}. {opt}" for i, opt in enumerate(q['options'])])
    text = f"📝 *Задание №{q['type']}*\n\n{q['instruction']}\n\n{options_text}\n\n_Выбери правильные варианты:_ "
    option_numbers = [str(i + 1) for i in range(len(q['options']))]

    await callback.message.edit_text(text, reply_markup=kb.get_question_kb(q["id"], option_numbers),
                                     parse_mode="Markdown")


@dp.callback_query(F.data.startswith("toggle_"))
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


@dp.callback_query(F.data.startswith("submit_"))
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
        if is_selected:
            line = f"*{line}*"  # Выделяем жирным то, что выбрал юзер

        if is_opt_correct:
            line += " ✅"  # Верный вариант
        elif is_selected:
            line += " ❌"  # Юзер выбрал неверно

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
        promo_text = (
            f"🎊 *УРОВЕНЬ ПОВЫШЕН!*\n"
            f"Ты покинул лигу {old_league['name']} {old_league['icon']}.\n"
            f"Твой новый дом — {new_league['name']} {new_league['icon']}.\n"
            f"_{new_league['desc']}_"
        )
        await callback.message.answer(promo_text, parse_mode="Markdown")


@dp.callback_query(F.data.startswith("explain_"))
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
        prompt = (
            f"Разбери задание ЕГЭ по русскому языку: {q['instruction']}\n"
            f"Текст/Варианты: {q['options']}\n"
            f"Правильные ответы: {correct_labels}\n"
            "Объясни очень кратко и по делу, без лишней воды и приветствий"
        )

        explanation = await ask_gemini(prompt)
        await callback.message.answer(f"✨ *Разбор от ИИ:*\n\n{explanation}", reply_markup=builder.as_markup(),
                                      parse_mode="Markdown")
        active_sessions.pop(callback.from_user.id, None)
    except Exception as e:
        print(e)
        await callback.message.answer("⚠️ Ошибка разбора.", reply_markup=builder.as_markup())


@dp.callback_query(F.data.startswith(("reg_tz_", "set_target_")))
async def settings_callbacks(callback: CallbackQuery):
    user = db.get_user_data(callback.from_user.id)
    if callback.data.startswith("reg_tz_"):
        user["timezone"] = int(callback.data.split("_")[2])
        msg = "Часовой пояс сохранен!"
    else:
        user["target"] = int(callback.data.split("_")[2])
        msg = f"Цель изменена на {user['target']}!"

    db.update_user_data(callback.from_user.id, user)
    await callback.answer(msg, show_alert=True)
    await callback.message.edit_text(get_menu_text(callback.from_user.id), reply_markup=kb.get_main_menu_kb(),
                                     parse_mode="Markdown")


async def notification_loop():
    while True:
        now_utc = datetime.now(timezone.utc)
        users = db.get_all_users_for_notify()
        today_str = datetime.now().strftime("%Y-%m-%d")
        for u_id, pl, tz, last_date in users:
            if pl != 'tg': continue
            if last_date == today_str: continue
            l_time = now_utc + timedelta(hours=tz)
            if any(l_time.hour == int(h) and abs(l_time.minute - (30 if h % 1 != 0 else 0)) < 5 for h in
                   content.NOTIFICATION_HOURS):
                try:
                    await bot.send_message(u_id, content.get_notification(l_time))
                    print(l_time)
                    await asyncio.sleep(0.05)
                except:
                    pass
        await asyncio.sleep(300)


async def main():
    db.init_db()
    print("Бот запущен.")
    await bot.set_my_commands([
        BotCommand(command="start", description="🔄 Перезапустить"),
        BotCommand(command="menu", description="📋 Меню"),
        BotCommand(command="bot", description="📝 Решать")
    ])
    _ = asyncio.create_task(notification_loop())
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
