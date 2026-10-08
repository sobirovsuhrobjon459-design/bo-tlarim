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

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardRemove,
    WebAppInfo,
)

from .config import BOT_TOKEN, WEBAPP_URL

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("bot")

TOKEN_IS_SET = bool(BOT_TOKEN) and BOT_TOKEN != "your_bot_token_here"

if not TOKEN_IS_SET:
    # MUHIM: bu yerda ilovani to'xtatmaymiz. Bot backend bilan BITTA jarayonda
    # ishlaydi, shuning uchun bu fayl import paytida xato ko'tarsa, Mini App
    # ham ishga tushmay qolardi. Token yo'q bo'lsa, faqat bot ishlamaydi.
    logger.error(
        "BOT_TOKEN sozlanmagan. .env faylida BOT_TOKEN=... qiymatini BotFather'dan "
        "olingan haqiqiy tokenga almashtiring. Bot ishga tushmaydi, Mini App esa ishlaydi."
    )

if TOKEN_IS_SET and not WEBAPP_URL.startswith("https://"):
    logger.warning(
        "WEBAPP_URL noto'g'ri yoki sozlanmagan (%r). Telegram Mini App faqat "
        "https:// bilan boshlanadigan manzilni qabul qiladi, aks holda PLAY tugmasi ishlamaydi.",
        WEBAPP_URL,
    )

# Token yo'q bo'lsa Bot() obyekti yaratilmaydi — aiogram tokenni darhol tekshiradi
bot = Bot(token=BOT_TOKEN) if TOKEN_IS_SET else None
dispatcher = Dispatcher()


def _find_school(code: str):
    """Taklif havolasidagi kod bo'yicha maktab (nomi va joylashuvi) — alohida ipda chaqiriladi."""

    from backend.database import PrivateSchool, SessionLocal
    from sqlalchemy import select

    with SessionLocal() as session:
        school = session.scalar(select(PrivateSchool).where(PrivateSchool.code == code))
        return (school.name, school.region, school.district) if school else None


async def _handle_school_invite(message: Message, code: str) -> None:
    """
    Guruhda ulashilgan maktab taklifi bosildi (t.me/bot?start=school_KOD).
    Bot maktabni va nima qilishni tushuntiradi; tugma Mini App'ni kod bilan
    tayyor holda ochadi — o'qituvchi faqat ismi va fanlarini kiritadi.
    """

    school = await asyncio.to_thread(_find_school, code)
    if not school:
        await message.answer("❗️ Bu taklif havolasi eskirgan yoki noto'g'ri. Maktab admini'dan yangisini so'rang.")
        return

    name, region, district = school
    text = (
        f"🏫 <b>{name}</b>\n📍 {region}, {district}\n\n"
        "Sizni shu xususiy maktab o'qituvchilari qatoriga taklif qilishdi.\n"
        f"🔑 Maktab kodi: <code>{code}</code>\n\n"
        "Ro'yxatdan o'tish uchun pastdagi tugmani bosing — ismingiz va dars beradigan fanlaringizni kiriting.\n\n"
        "⚠️ Agar siz bu maktab o'qituvchisi bo'lmasangiz, qo'shilmang."
    )

    if WEBAPP_URL.startswith("https://"):
        separator = "&" if "?" in WEBAPP_URL else "?"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🏫 Maktabga qo'shilish",
                    web_app=WebAppInfo(url=f"{WEBAPP_URL}{separator}school_code={code}"),
                )
            ]]
        )
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML")


@dispatcher.message(CommandStart())
async def handle_start(message: Message, command: CommandObject) -> None:
    """Foydalanuvchi botga /start yuborganda ishlaydi (taklif havolasi bilan ham)."""

    payload = (command.args or "").strip()
    if payload.startswith("school_"):
        code = payload[len("school_"):].upper()
        if code.isalnum() and 4 <= len(code) <= 20:
            await _handle_school_invite(message, code)
            return

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


@dispatcher.callback_query(F.data.startswith("withdrawal_"))
async def handle_withdrawal_decision(callback: CallbackQuery) -> None:
    """Admin pul yechish xabaridagi ✅/❌ tugmasini bosganda ishlaydi."""

    # Importlar shu yerda, funksiya ichida — chunki backend hali to'liq ishga tushmagan
    # bosqichda (masalan standalone "python -m bot.bot" bilan) bu modulga ehtiyoj yo'q.
    from backend.database import ADMIN_ID, SessionLocal, Teacher, WithdrawalRequest

    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu tugma faqat admin uchun", show_alert=True)
        return

    # Tugma ma'lumoti kutilganidan boshqa ko'rinishda bo'lsa (eski xabar, qo'lda
    # yuborilgan so'rov) — xato ko'tarmasdan, tushunarli javob qaytaramiz.
    action, separator, request_id_str = callback.data.partition(":")
    if not separator or not request_id_str.isdigit():
        await callback.answer("Bu tugma eskirgan", show_alert=True)
        return

    request_id = int(request_id_str)

    session = SessionLocal()
    try:
        request = session.get(WithdrawalRequest, request_id)
        if not request or request.status != "pending":
            await callback.answer("So'rov allaqachon ko'rib chiqilgan", show_alert=True)
            return

        teacher = session.get(Teacher, request.teacher_id)
        if not teacher:
            await callback.answer("O'qituvchi topilmadi", show_alert=True)
            return

        teacher_name = teacher.user.full_name

        if action == "withdrawal_approve":
            if teacher.balance < request.amount:
                await callback.answer("O'qituvchi balansi yetarli emas", show_alert=True)
                return

            teacher.balance -= request.amount
            request.status = "approved"
            session.commit()
            await callback.message.edit_text(
                f"✅ To'landi: {teacher_name} — {request.amount} so'm\n"
                f"Karta: {request.card_number}\n"
                f"Qolgan balans: {teacher.balance} so'm"
            )
        else:
            request.status = "rejected"
            session.commit()
            await callback.message.edit_text(f"❌ Rad etildi: {teacher_name} — {request.amount} so'm")
    finally:
        session.close()

    await callback.answer()


@dispatcher.callback_query(F.data.startswith("topup_"))
async def handle_card_topup_decision(callback: CallbackQuery) -> None:
    """Admin karta chekidagi "✅ Pul tushdi" / "❌ Rad etish" tugmasini bosganda ishlaydi."""

    from backend.database import ADMIN_ID, SessionLocal

    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Bu tugma faqat admin uchun", show_alert=True)
        return

    action, separator, purchase_id_str = callback.data.partition(":")
    if not separator or not purchase_id_str.isdigit():
        await callback.answer("Bu tugma eskirgan", show_alert=True)
        return

    # Tasdiqlash mantig'i backend'da (admin paneli ham aynan shuni ishlatadi).
    # Bot backend bilan bitta jarayonda ishlaydi, shuning uchun bu import
    # tayyor modulni oladi, qaytadan yuklamaydi.
    from backend.main import decide_card_topup

    def decide() -> tuple[bool, str]:
        session = SessionLocal()
        try:
            return decide_card_topup(session, int(purchase_id_str), approve=action == "topup_approve")
        finally:
            session.close()

    # Baza va Telegram'ga so'rov — sinxron ishlar, botni to'xtatib qo'ymasligi
    # uchun alohida ipda bajariladi.
    ok, message = await asyncio.to_thread(decide)

    if not ok:
        await callback.answer(message, show_alert=True)
        return

    # Chek rasmi ostidagi matn natija bilan almashtiriladi, tugmalar olib tashlanadi —
    # shunda qaysi so'rov ko'rib chiqilgani chatda darhol ko'rinadi.
    try:
        await callback.message.edit_caption(caption=message)
    except Exception:
        logger.warning("Chek xabarini yangilab bo'lmadi", exc_info=True)

    await callback.answer("Bajarildi")


async def _setup_menu_button() -> None:
    """Chat ichidagi doimiy PLAY tugmasini Mini App'ga ulaydi — Mini App'ni ochishning yagona yo'li shu."""

    if not WEBAPP_URL.startswith("https://"):
        return

    try:
        await bot.set_chat_menu_button(
            menu_button=MenuButtonWebApp(text="PLAY", web_app=WebAppInfo(url=WEBAPP_URL))
        )
    except Exception:
        # Internet uzilgan yoki Telegram javob bermagan bo'lishi mumkin. Bu
        # botning ishlashiga to'sqinlik qilmasligi kerak — tugma avvalgi
        # deploydan saqlanib qolgan bo'ladi, keyingi ishga tushishda yangilanadi.
        logger.warning("PLAY tugmasini sozlab bo'lmadi", exc_info=True)


# Aloqa uzilib qolsa, bot qayta ulanishga urinadi. Har xatodan keyin kutish
# vaqti oshib boradi, 5 daqiqadan oshmaydi — shunda Telegram API'ni ham
# bezovta qilmaymiz, bot ham o'zi tiklanadi (qo'lda qayta deploy shart emas).
RETRY_DELAYS_SECONDS = [5, 15, 60, 300]


async def run_bot() -> None:
    """Botni ishga tushiradi. backend/main.py ham, standalone ishga tushirish ham shuni chaqiradi."""

    if not TOKEN_IS_SET:
        logger.error("Bot ishga tushmadi: BOT_TOKEN sozlanmagan")
        return

    attempt = 0
    try:
        while True:
            try:
                logger.info("Bot ishga tushmoqda...")
                await _setup_menu_button()
                attempt = 0  # muvaffaqiyatli ulandi — keyingi xatoda yana 5 sekunddan boshlanadi
                await dispatcher.start_polling(bot)
                return  # start_polling o'z-o'zidan tugasa (to'xtatilsa), qayta urinmaymiz
            except TelegramUnauthorizedError:
                # Token noto'g'ri — qayta urinishdan foyda yo'q, faqat vaqt va
                # so'rov limiti sarflanadi. Mini App esa ishlashda davom etadi.
                logger.error("Bot ishga tushmadi: BOT_TOKEN noto'g'ri (BotFather'dan tekshiring)")
                return
            except Exception:
                delay = RETRY_DELAYS_SECONDS[min(attempt, len(RETRY_DELAYS_SECONDS) - 1)]
                attempt += 1
                logger.warning("Bot xato bilan to'xtadi, %s sekunddan keyin qayta urinadi", delay, exc_info=True)
                await asyncio.sleep(delay)
    except asyncio.CancelledError:
        logger.info("Bot to'xtatildi")
        raise
    finally:
        # Ulanish bir marta, bot butunlay to'xtaganda yopiladi
        try:
            await bot.session.close()
        except Exception:
            pass


if __name__ == "__main__":
    asyncio.run(run_bot())
