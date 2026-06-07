from loguru import logger
from aiogram import Router, F
from aiogram.types import Message

import data_content
import core
import keyboards as kb

router = Router()


@router.message(F.text == "/menu")
async def cmd_menu(message: Message):
    logger.debug(f"Обработка команды /menu от пользователя {message.from_user.id}")
    menu_data = core.get_menu_data(message.from_user.id)
    text = data_content.render_menu_text(menu_data)
    await message.answer(text, reply_markup=kb.get_main_menu_kb(), parse_mode="Markdown")
