from datetime import datetime
from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart, Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

import database as db
import data_content as content
import keyboards as kb
from handlers.utils import get_menu_text

# Создаем роутер для этого файла
router = Router()


@router.message(CommandStart())
async def cmd_start(message: Message):
    user = db.get_user_data(message.from_user.id)
    user["username"] = message.from_user.username
    user["full_name"] = message.from_user.full_name
    db.update_user_data(message.from_user.id, user)

    await message.answer(
        f"👋 Привет, {message.from_user.first_name}! Система ЕГЭДНЕВНО активирована!",
        reply_markup=kb.get_reply_kb()
    )

    if user["timezone"] is None:
        await message.answer("Выберите свой часовой пояс (МСК = UTC+3):", reply_markup=kb.get_tz_kb())
    else:
        await message.answer(get_menu_text(message.from_user.id), reply_markup=kb.get_main_menu_kb(),
                             parse_mode="Markdown")


@router.message(Command("menu"))
async def cmd_menu(message: Message):
    await message.answer(get_menu_text(message.from_user.id), reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")


@router.message(F.text == "🎯 Изменить цель")
async def change_target_msg(message: Message):
    await message.answer("Выберите желаемый балл на ЕГЭ:", reply_markup=kb.get_targets_kb())


@router.message(F.text == "🕒 Сменить часовой пояс")
async def change_tz_msg(message: Message):
    await message.answer("Выберите часовой пояс:", reply_markup=kb.get_tz_kb())


@router.callback_query(F.data == "menu")
async def show_menu(callback: CallbackQuery):
    await callback.message.edit_text(get_menu_text(callback.from_user.id), reply_markup=kb.get_main_menu_kb(),
                                     parse_mode="Markdown")


@router.callback_query(F.data == "my_league")
async def show_league_info(callback: CallbackQuery):
    user = db.get_user_data(callback.from_user.id)
    league = content.get_league(user["score"])
    next_info = f"\n\n🔜 До следующей лиги осталось баллов: *{league['next'] - user['score']}*" if league[
                                                                                                      "next"] <= 100 else "\n\n🌟 Вы на вершине!"

    text = f"🏆 *Ваша лига: {league['name']}*\n\n{league['icon']} {league['desc']}\n\nТекущий балл: {user['score']}{next_info}"
    builder = InlineKeyboardBuilder().button(text="🔙 Назад", callback_data="menu")
    await callback.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="Markdown")


@router.callback_query(F.data.startswith(("reg_tz_", "set_target_")))
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
