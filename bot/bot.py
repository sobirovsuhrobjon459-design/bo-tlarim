"""
Telegram bot.

Vazifasi: foydalanuvchi /start bosganda salomlashish va Mini App'ni ochish
uchun bitta PLAY tugmasi (chat menyusida, xabar yozish maydoni yonida)
ko'rsatish, tushunarsiz xabarlarga ham javob berish.

MUHIM: bu fayl backend bilan BIRGA, bitta jarayon ichida ishga tushadi
(backend/main.py shuni qiladi) — chunki Render'ning bepul tarifida alohida
"Background Worker" pullik. Shuning uchun standalone ishga tushirish kerak
bo'lsa ham ".config" nisbiy import orqali, paket sifatida ishga tushiriladi:
    python -m bot.bot
"""

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.filters import Command, CommandStart
from aiogram.types import MenuButtonWebApp, Message, ReplyKeyboardRemove, WebAppInfo

from .config import BOT_TOKEN, WEBAPP_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")

if not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
    raise SystemExit(
        "BOT_TOKEN sozlanmagan. .env faylida BOT_TOKEN=... qiymatini "
        "BotFather'dan olingan haqiqiy tokenga almashtiring."
    )

if not WEBAPP_URL.startswith("https://"):
    logger.warning(
        "WEBAPP_URL noto'g'ri yoki sozlanmagan (%r). Telegram Mini App faqat "
        "https:// bilan boshlanadigan manzilni qabul qiladi, aks holda PLAY tugmasi ishlamaydi.",
        WEBAPP_URL,
    )

bot = Bot(token=BOT_TOKEN)
dispatcher = Dispatcher()


@dispatcher.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Foydalanuvchi botga /start yuborganda ishlaydi."""

    welcome_text = (
        f"Assalomu alaykum, {message.from_user.first_name}! 👋\n\n"
        "🎓 <b>Test Platformasiga xush kelibsiz!</b>\n\n"
        "Bilimingizni sinang, natijangizni kuzating va rivojlaning.\n\n"
        "Boshlash uchun pastda, xabar yozish maydoni yonidagi <b>PLAY</b> tugmasini bosing."
    )

    # Eski reply-keyboard PLAY tugmasi qolib ketgan bo'lsa, uni tozalaydi —
    # endi Mini App faqat yagona, chat menyusidagi PLAY tugmasi orqali ochiladi.
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=ReplyKeyboardRemove())
    logger.info("Yangi /start: %s (tg_id=%s)", message.from_user.full_name, message.from_user.id)


@dispatcher.message(Command("help"))
async def handle_help(message: Message) -> None:
    """/help — bot nima qila olishi haqida qisqacha ma'lumot beradi."""

    await message.answer(
        "ℹ️ Bu bot Test Platformasi Mini App'ini ochadi.\n\n"
        "Pastdagi PLAY tugmasini bosib Mini App'ni oching — u yerda test ishlashingiz "
        "yoki o'qituvchi sifatida test yaratishingiz mumkin."
    )


@dispatcher.message()
async def handle_unknown(message: Message) -> None:
    """Yuqoridagi buyruqlarga mos kelmagan har qanday xabarga javob beradi."""

    await message.answer(
        "Buyruqni tushunmadim 🤔\nBoshlash uchun /start yuboring yoki pastdagi "
        "PLAY tugmasini bosing."
    )


async def _setup_menu_button() -> None:
    """Chat ichidagi doimiy PLAY tugmasini Mini App'ga ulaydi — Mini App'ni ochishning yagona yo'li shu."""
    if WEBAPP_URL.startswith("https://"):
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="PLAY", web_app=WebAppInfo(url=WEBAPP_URL))
        )


async def run_bot() -> None:
    """Botni ishga tushiradi. backend/main.py ham, standalone ishga tushirish ham shuni chaqiradi."""
    logger.info("Bot ishga tushmoqda...")
    await _setup_menu_button()
    try:
        await dispatcher.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(run_bot())
