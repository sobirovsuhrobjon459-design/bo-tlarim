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
    BotCommand,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    MenuButtonCommands,
    MenuButtonDefault,
    MenuButtonWebApp,
    Message,
    ReplyKeyboardMarkup,
    WebAppInfo,
)

from .config import BOT_TOKEN, WEBAPP_URL
from .guide import GUIDE_INTRO, GUIDE_ROLES

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

# Murojaat uchun adminlar — savol, to'lov yoki muammo bo'lsa shularga yoziladi.
# Almashtirish kerak bo'lsa, faqat shu ro'yxat o'zgartiriladi (Mini App'da ham shunday ro'yxat bor).
SUPPORT_CONTACTS = ("saidjanovic", "Ulugbeck_dev")
SUPPORT_TEXT = "💬 Murojaat uchun: " + ", ".join(f"@{name}" for name in SUPPORT_CONTACTS)

# Pastdagi tugmalar (reply-klaviatura). Ular bor ekan, xabar maydonining o'ng
# tomonida 2×2 kub belgisi chiqadi — u tugmalarni ochadi/yopadi. Mini App
# baribir chapdagi PLAY orqali ochiladi; bu tugmalar faqat bot buyruqlari.
GUIDE_BUTTON = "📘 Qo'llanma"
RESTART_BUTTON = "🔄 Qayta ishga tushirish"
SUPPORT_BUTTON = "💬 Admin bilan bog'lanish"
MAIN_KEYBOARD = ReplyKeyboardMarkup(
    keyboard=[
        [KeyboardButton(text=GUIDE_BUTTON), KeyboardButton(text=RESTART_BUTTON)],
        [KeyboardButton(text=SUPPORT_BUTTON)],
    ],
    resize_keyboard=True,
    is_persistent=False,
    input_field_placeholder="Tugmani tanlang yoki PLAY ni bosing",
)

def _money(amount: int) -> str:
    return f"{amount:,}".replace(",", " ")


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


def _find_invite(token: str):
    """Shaxsiy taklif (maktab nomi, kim taklif qilgan) — alohida ipda chaqiriladi."""

    from backend.database import PrivateSchool, SchoolInvite, SessionLocal, User, utcnow
    from sqlalchemy import select

    with SessionLocal() as session:
        invite = session.scalar(select(SchoolInvite).where(SchoolInvite.token == token))
        if not invite:
            return None, "Taklif havolasi topilmadi."
        if invite.used_at is not None:
            return None, "Bu taklif havolasi allaqachon ishlatilgan. Admindan yangisini so'rang."
        if invite.expires_at < utcnow():
            return None, "Taklif havolasining muddati tugagan. Admindan yangisini so'rang."
        school = session.get(PrivateSchool, invite.school_id)
        inviter = session.get(User, invite.created_by)
        return (school.name, school.region, school.district, inviter.full_name if inviter else ""), None


async def _handle_teacher_invite(message: Message, token: str) -> None:
    """
    Admin yuborgan shaxsiy havola bosildi (t.me/bot?start=inv_TOKEN):
    o'qituvchi KODSIZ qo'shiladi — tugma Mini App'ni shu taklif bilan ochadi.
    """

    found, error = await asyncio.to_thread(_find_invite, token)
    if not found:
        await message.answer(f"❗️ {error}\n\n{SUPPORT_TEXT}")
        return

    name, region, district, inviter = found
    text = (
        f"🏫 <b>{name}</b>\n📍 {region}, {district}\n\n"
        f"👤 {inviter} sizni shu maktabga o'qituvchi sifatida taklif qildi.\n"
        "Kod kerak emas — pastdagi tugmani bosing va ismingiz, fan yoki sinfingizni kiriting."
    )
    if WEBAPP_URL.startswith("https://"):
        separator = "&" if "?" in WEBAPP_URL else "?"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[[
                InlineKeyboardButton(
                    text="🏫 Maktabga qo'shilish",
                    web_app=WebAppInfo(url=f"{WEBAPP_URL}{separator}invite={token}"),
                )
            ]]
        )
        await message.answer(text, parse_mode="HTML", reply_markup=keyboard)
    else:
        await message.answer(text, parse_mode="HTML")


async def _missing_channels(tg_id: int) -> list[str]:
    """Foydalanuvchi obuna bo'lmagan adminlar kanallari (@sukhrobmath)."""

    from backend.main import missing_default_channels

    return await asyncio.to_thread(missing_default_channels, tg_id)


async def _show_subscribe_gate(message: Message, missing: list[str], payload: str) -> None:
    """
    Obuna bo'lmaguncha botdan foydalanib bo'lmaydi: shu chatda PLAY tugmasi
    yashiriladi, kanal(lar)ga obuna bo'lish va tekshirish tugmalari chiqadi.
    """

    try:
        await bot.set_chat_menu_button(chat_id=message.chat.id, menu_button=MenuButtonCommands())
    except Exception:
        logger.warning("PLAY tugmasini yashirib bo'lmadi", exc_info=True)

    buttons = [
        [InlineKeyboardButton(text=f"📢 {channel} — obuna bo'lish", url=f"https://t.me/{channel.lstrip('@')}")]
        for channel in missing
        if channel.startswith("@")
    ]
    buttons.append([InlineKeyboardButton(text="✅ Obuna bo'ldim", callback_data=f"checksub:{payload}"[:64])])
    await message.answer(
        f"Assalomu alaykum, {message.from_user.first_name}! 👋\n\n"
        f"Botdan foydalanish uchun avval kanalimizga obuna bo'ling: <b>{', '.join(missing)}</b>\n\n"
        "Obuna bo'lgach, «✅ Obuna bo'ldim» tugmasini bosing.",
        parse_mode="HTML",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


async def _open_bot(message: Message, payload: str) -> None:
    """Obuna tasdiqlangan foydalanuvchi: PLAY tugmasi va salomlashish (yoki taklif)."""

    if payload.startswith("inv_"):
        token = payload[len("inv_"):]
        if token.isalnum() and 6 <= len(token) <= 40:
            await _handle_teacher_invite(message, token)
            return
    if payload.startswith("school_"):
        code = payload[len("school_"):].upper()
        if code.isalnum() and 4 <= len(code) <= 20:
            await _handle_school_invite(message, code)
            return

    welcome_text = (
        f"Assalomu alaykum, {message.chat.first_name or ''}! 👋\n\n"
        "🎓 <b>Test Platformasiga xush kelibsiz!</b>\n\n"
        "Bilimingizni sinang, natijangizni kuzating va rivojlaning.\n\n"
        "Boshlash uchun pastda, xabar yozish maydoni yonidagi <b>PLAY</b> tugmasini bosing.\n"
        "📘 Qo'llanma va ilova haqida — /yordam\n\n"
        f"{SUPPORT_TEXT}"
    )

    # Pastdagi tugmalar (Qo'llanma, Qayta ishga tushirish, Admin) — eski reply-keyboard
    # PLAY tugmasi qolib ketgan bo'lsa, shu bilan almashadi. Mini App — chapdagi PLAY'da.
    await message.answer(welcome_text, parse_mode="HTML", reply_markup=MAIN_KEYBOARD)


def _remember_user(tg_id: int, full_name: str, username: str | None) -> None:
    """
    /start bosgan odamni bazaga yozadi — test e'lonlari unga ham yetib borsin.
    Ilgari foydalanuvchi faqat Mini App'ni ochganda saqlanardi.
    """

    from sqlalchemy import select
    from sqlalchemy.exc import IntegrityError

    from backend.database import SessionLocal, User

    with SessionLocal() as session:
        if session.scalar(select(User.id).where(User.tg_id == tg_id)):
            return
        session.add(User(tg_id=tg_id, full_name=full_name, username=username))
        try:
            session.commit()
        except IntegrityError:
            session.rollback()  # shu payt Mini App ham yaratib qo'ygan


@dispatcher.message(CommandStart())
async def handle_start(message: Message, command: CommandObject) -> None:
    """Foydalanuvchi botga /start yuborganda ishlaydi (taklif havolasi bilan ham)."""

    payload = (command.args or "").strip()
    logger.info("Yangi /start: %s (tg_id=%s)", message.from_user.full_name, message.from_user.id)
    try:
        await asyncio.to_thread(_remember_user, message.from_user.id, message.from_user.full_name,
                                message.from_user.username)
    except Exception:
        logger.warning("Foydalanuvchini saqlab bo'lmadi", exc_info=True)  # /start baribir ishlasin

    missing = await _missing_channels(message.from_user.id)
    if missing:
        await _show_subscribe_gate(message, missing, payload)
        return

    await _restore_play_button(message.chat.id)
    await _open_bot(message, payload)


@dispatcher.message(Command("restart"))
async def handle_restart(message: Message) -> None:
    """
    /restart — botni "toza holatga" qaytaradi: PLAY tugmasi yo'qolgan, eski
    klaviatura qolib ketgan yoki bot javob bermayotgandek tuyulganda.
    Obuna qayta tekshiriladi, PLAY tiklanadi va bosh xabar (pastdagi tugmalar bilan) qayta chiqadi.
    """

    logger.info("/restart: %s (tg_id=%s)", message.from_user.full_name, message.from_user.id)

    missing = await _missing_channels(message.from_user.id)
    if missing:
        await _show_subscribe_gate(message, missing, "")
        return

    await _restore_play_button(message.chat.id)
    await message.answer("🔄 Bot qayta ishga tushirildi.")
    await _open_bot(message, "")


def _play_menu_button():
    """Mini App'ni ochadigan PLAY tugmasi (WEBAPP_URL noto'g'ri bo'lsa — umumiy sozlama)."""

    if WEBAPP_URL.startswith("https://"):
        return MenuButtonWebApp(text="PLAY", web_app=WebAppInfo(url=WEBAPP_URL))
    return MenuButtonDefault()


async def _restore_play_button(chat_id: int) -> None:
    """
    Obuna bo'lgan foydalanuvchiga PLAY tugmasini qaytaradi.

    MUHIM: bu yerda MenuButtonDefault() emas, aynan PLAY (MenuButtonWebApp)
    o'rnatiladi. Default umumiy sozlamaga tayanadi va Telegram ba'zan uni
    PLAY emas, oddiy "Menyu" (buyruqlar ro'yxati) qilib ko'rsatadi.
    """

    try:
        await bot.set_chat_menu_button(chat_id=chat_id, menu_button=_play_menu_button())
    except Exception:
        logger.warning("PLAY tugmasini qaytarib bo'lmadi", exc_info=True)


@dispatcher.callback_query(F.data.startswith("checksub:"))
async def handle_check_subscription(callback: CallbackQuery) -> None:
    """«✅ Obuna bo'ldim» — qayta tekshiradi; obuna bo'lsa botni ochadi."""

    from backend.main import _subscription_confirmed

    _subscription_confirmed.pop(callback.from_user.id, None)  # eski natija emas, hozirgisini so'raymiz
    missing = await _missing_channels(callback.from_user.id)
    if missing:
        await callback.answer(f"Hali obuna ko'rinmayapti: {', '.join(missing)}", show_alert=True)
        return

    await callback.answer("✅ Rahmat! Obuna tasdiqlandi")
    try:
        await callback.message.delete()
    except Exception:
        pass
    await _restore_play_button(callback.message.chat.id)
    await _open_bot(callback.message, callback.data.partition(":")[2])


@dispatcher.message(Command("yordam", "help"))
async def handle_help(message: Message) -> None:
    """
    /yordam — avval "Siz kimsiz?" so'raladi, qo'llanma tanlangan rol bo'yicha
    keladi (qarang: handle_guide_role). Ilgari hamma qism bir yo'la tashlanardi.

    Qo'llanma HIMOYALANGAN (protect_content): uni boshqaga uzatib, saqlab yoki
    nusxalab bo'lmaydi. Botning boshqa xabarlari (davomat, chek, natija) odatdagidek
    uzatiladi — himoya faqat shu xabarlarga qo'yiladi.
    """

    await message.answer(GUIDE_INTRO, parse_mode="HTML", reply_markup=_guide_roles_keyboard(), protect_content=True)


def _guide_roles_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"guide:{key}")] for key, (label, _) in GUIDE_ROLES.items()
        ]
    )


@dispatcher.callback_query(F.data.startswith("guide:"))
async def handle_guide_role(callback: CallbackQuery) -> None:
    """Rol tanlanganda — faqat shu rolga tegishli qo'llanma; oxirida boshqa rolni tanlash tugmasi."""

    role = GUIDE_ROLES.get(callback.data.partition(":")[2])
    if not role:
        await callback.answer("Bu tugma eskirgan — /yordam ni qayta yuboring", show_alert=True)
        return

    await callback.answer()
    _, parts = role
    back = InlineKeyboardMarkup(
        inline_keyboard=[[InlineKeyboardButton(text="🔄 Boshqa bo'limni ko'rish", callback_data="guide_menu")]]
    )
    for index, part in enumerate(parts):
        is_last = index == len(parts) - 1
        await callback.message.answer(
            part, parse_mode="HTML", protect_content=True, reply_markup=back if is_last else None
        )


@dispatcher.callback_query(F.data == "guide_menu")
async def handle_guide_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    await callback.message.answer(GUIDE_INTRO, parse_mode="HTML", reply_markup=_guide_roles_keyboard(), protect_content=True)


@dispatcher.message(F.text == GUIDE_BUTTON)
async def handle_guide_button(message: Message) -> None:
    await handle_help(message)


@dispatcher.message(Command("pul"))
async def handle_money(message: Message) -> None:
    """/pul — o'qituvchi balansi; Click/Payme orqali tushgan pulni shu yerdan yechib oladi."""

    from backend.database import SessionLocal
    from backend.main import bot_withdrawal_overview

    def overview() -> tuple[str, int]:
        with SessionLocal() as session:
            return bot_withdrawal_overview(session, message.from_user.id)

    text, amount = await asyncio.to_thread(overview)
    markup = None
    if amount > 0:
        markup = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text=f"💸 {_money(amount)} so'mni yechish", callback_data="wd_request")
        ]])
    await message.answer(text, reply_markup=markup)


@dispatcher.callback_query(F.data == "wd_request")
async def handle_money_request(callback: CallbackQuery) -> None:
    from backend.database import SessionLocal
    from backend.main import bot_request_withdrawal

    def request() -> tuple[bool, str]:
        with SessionLocal() as session:
            return bot_request_withdrawal(session, callback.from_user.id)

    ok, text = await asyncio.to_thread(request)
    if not ok:
        await callback.answer(text, show_alert=True)
        return
    try:
        await callback.message.edit_text(text)
    except Exception:
        await callback.message.answer(text)
    await callback.answer()


@dispatcher.message(F.text == RESTART_BUTTON)
async def handle_restart_button(message: Message) -> None:
    await handle_restart(message)


@dispatcher.message(F.text == SUPPORT_BUTTON)
async def handle_support_button(message: Message) -> None:
    await message.answer(
        f"{SUPPORT_TEXT}\n\nSavolingizni shu adminlardan biriga yozing — tez orada javob berishadi.",
        reply_markup=MAIN_KEYBOARD,
    )


@dispatcher.message()
async def handle_unknown(message: Message) -> None:
    """Yuqoridagi buyruqlarga mos kelmagan har qanday xabarga javob beradi."""

    await message.answer(
        "Buyruqni tushunmadim 🤔\nBoshlash uchun /start yuboring yoki pastdagi "
        "PLAY tugmasini bosing. Qo'llanma — /yordam\n\n"
        f"Savolingiz bo'lsa — adminlarga yozing.\n{SUPPORT_TEXT}"
    )


@dispatcher.callback_query(F.data.startswith("withdrawal_"))
async def handle_withdrawal_decision(callback: CallbackQuery) -> None:
    """Admin pul yechish xabaridagi ✅/❌ tugmasini bosganda ishlaydi."""

    # Importlar shu yerda, funksiya ichida — chunki backend hali to'liq ishga tushmagan
    # bosqichda (masalan standalone "python -m bot.bot" bilan) bu modulga ehtiyoj yo'q.
    from backend.database import ADMIN_ID, SessionLocal, Teacher, WithdrawalRequest, withdraw_balance

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
        request = session.get(WithdrawalRequest, request_id, with_for_update=True)
        if not request or request.status != "pending":
            await callback.answer("So'rov allaqachon ko'rib chiqilgan", show_alert=True)
            return

        teacher = session.get(Teacher, request.teacher_id)
        if not teacher:
            await callback.answer("O'qituvchi topilmadi", show_alert=True)
            return

        teacher_name = teacher.user.full_name

        if action == "withdrawal_approve":
            if not withdraw_balance(teacher, request.amount):
                await callback.answer("O'qituvchi balansi yetarli emas", show_alert=True)
                return

            request.status = "approved"
            session.commit()
            try:
                await callback.bot.send_message(
                    teacher.user.tg_id,
                    f"✅ {_money(request.amount)} so'm kartangizga o'tkazildi.\n💳 {request.card_number}",
                )
            except Exception:
                logger.warning("O'qituvchiga pul yechilgani haqida xabar yuborilmadi", exc_info=True)
            await callback.message.edit_text(
                f"✅ To'landi: {teacher_name} — {request.amount} so'm\n"
                f"Karta: {request.card_number}\n"
                f"Qolgan balans: {teacher.balance} so'm"
            )
        else:
            request.status = "rejected"
            session.commit()
            try:
                await callback.bot.send_message(
                    teacher.user.tg_id,
                    f"❌ {_money(request.amount)} so'm yechish so'rovi rad etildi. Savol bo'lsa — adminga yozing.\n{SUPPORT_TEXT}",
                )
            except Exception:
                logger.warning("O'qituvchiga rad etilgani haqida xabar yuborilmadi", exc_info=True)
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


@dispatcher.callback_query(F.data.startswith("tpay_"))
async def handle_test_payment_decision(callback: CallbackQuery) -> None:
    """
    O'qituvchi o'quvchining test chekidagi "✅ Pul tushdi" / "❌ Rad etish" tugmasini
    bosganda. Kim bosganini backend tekshiradi: faqat o'sha testning o'qituvchisi.
    """

    from backend.database import SessionLocal

    action, separator, payment_id_str = callback.data.partition(":")
    if not separator or not payment_id_str.isdigit():
        await callback.answer("Bu tugma eskirgan", show_alert=True)
        return

    from backend.main import decide_test_payment

    def decide() -> tuple[bool, str]:
        session = SessionLocal()
        try:
            return decide_test_payment(
                session, int(payment_id_str), approve=action == "tpay_approve", teacher_tg_id=callback.from_user.id
            )
        finally:
            session.close()

    ok, message = await asyncio.to_thread(decide)
    if not ok:
        await callback.answer(message, show_alert=True)
        return

    try:
        await callback.message.edit_caption(caption=message)
    except Exception:
        logger.warning("Test cheki xabarini yangilab bo'lmadi", exc_info=True)

    await callback.answer("Bajarildi")


async def _setup_menu_button() -> None:
    """Chat ichidagi doimiy PLAY tugmasini Mini App'ga ulaydi — Mini App'ni ochishning yagona yo'li shu."""

    if not WEBAPP_URL.startswith("https://"):
        return

    try:
        await bot.set_chat_menu_button(menu_button=_play_menu_button())
    except Exception:
        # Internet uzilgan yoki Telegram javob bermagan bo'lishi mumkin. Bu
        # botning ishlashiga to'sqinlik qilmasligi kerak — tugma avvalgi
        # deploydan saqlanib qolgan bo'ladi, keyingi ishga tushishda yangilanadi.
        logger.warning("PLAY tugmasini sozlab bo'lmadi", exc_info=True)

    # "/" bosilganda chiqadigan buyruqlar ro'yxati
    try:
        from backend.main import _enabled_providers

        commands = [
            BotCommand(command="start", description="Botni ishga tushirish"),
            BotCommand(command="yordam", description="Qo'llanma va ilova haqida"),
            BotCommand(command="restart", description="Botni qayta ishga tushirish"),
        ]
        # Pul yechish faqat Click/Payme ulanganda ma'noli (pul platformaga tushadi)
        if _enabled_providers():
            commands.append(BotCommand(command="pul", description="O'qituvchi: balans va pulni yechish"))
        await bot.set_my_commands(commands)
    except Exception:
        logger.warning("Buyruqlar ro'yxatini sozlab bo'lmadi", exc_info=True)


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
