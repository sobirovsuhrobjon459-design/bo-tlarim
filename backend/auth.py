"""
Autentifikatsiya fayli:

1) Telegram initData tekshiruvi (validate_init_data)
2) FastAPI Depends() orqali ishlatiladigan umumiy funksiyalar (get_db, get_current_user, ...)

MUHIM: o'qituvchi uchun alohida login/parol yo'q. Telegram orqali kim ekani
allaqachon aniq (tg_id) — "o'qituvchi" bo'lish shunchaki shu foydalanuvchiga
tegishli Teacher yozuvi borligini bildiradi (qarang: get_current_teacher).
"""

import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import ADMIN_ID, BOT_TOKEN, DEV_MODE, SessionLocal, Teacher, User

INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60  # 1 kun


class InitDataError(Exception):
    """initData yaroqsiz yoki soxta bo'lsa shu xato ko'tariladi."""


# ========== 1) Telegram initData tekshiruvi ==========


def validate_init_data(init_data: str) -> dict:
    """
    initData imzosini tekshiradi va ichidagi Telegram foydalanuvchisi ma'lumotini qaytaradi.

    Rasmiy algoritm: https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app
    """

    if not init_data:
        raise InitDataError("initData bo'sh")

    parsed_fields = dict(parse_qsl(init_data, keep_blank_values=True))

    received_hash = parsed_fields.pop("hash", None)
    if not received_hash:
        raise InitDataError("hash maydoni topilmadi")

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(parsed_fields.items()))

    secret_key = hmac.new(b"WebAppData", BOT_TOKEN.encode(), hashlib.sha256).digest()
    expected_hash = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    if not BOT_TOKEN:
        # Token sozlanmagan bo'lsa, yuqoridagi imzo bo'sh kalit bilan hisoblanadi —
        # ya'ni soxta initData ham o'tib ketishi mumkin. Shuning uchun to'smaymiz.
        raise InitDataError("server sozlanmagan (BOT_TOKEN yo'q)")

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("imzo noto'g'ri — bu so'rov Telegramdan kelmagan bo'lishi mumkin")

    try:
        auth_date = int(parsed_fields.get("auth_date", 0))
    except ValueError:
        raise InitDataError("auth_date noto'g'ri")

    if time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise InitDataError("initData eskirgan, Mini App'ni qaytadan oching")

    user_json = parsed_fields.get("user")
    if not user_json:
        raise InitDataError("foydalanuvchi ma'lumoti topilmadi")

    # Buzilgan JSON kelsa, 500 emas — tushunarli 401 qaytishi kerak
    try:
        telegram_user = json.loads(user_json)
    except ValueError:
        raise InitDataError("foydalanuvchi ma'lumoti o'qilmadi")

    if not isinstance(telegram_user, dict) or not isinstance(telegram_user.get("id"), int):
        raise InitDataError("foydalanuvchi ma'lumoti noto'g'ri")

    return telegram_user


# ========== 2) FastAPI Depends() funksiyalari ==========


def get_db():
    """Har bir so'rov uchun alohida ma'lumotlar bazasi session'i ochadi va oxirida yopadi."""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


def get_current_user(
    x_init_data: str = Header(default=""),
    x_dev_user: str = Header(default=""),
    db: Session = Depends(get_db),
) -> User:
    """
    So'rov header'idagi Telegram initData'ni tekshiradi va shu foydalanuvchini
    ma'lumotlar bazasidan topadi. Birinchi marta kelgan foydalanuvchi bo'lsa,
    avtomatik yangi yozuv yaratiladi.
    """

    telegram_user = None

    if x_init_data:
        try:
            telegram_user = validate_init_data(x_init_data)
        except InitDataError as error:
            raise HTTPException(status_code=401, detail=str(error))
    elif DEV_MODE and x_dev_user:
        # Faqat lokal sinov uchun: Telegram'siz, oddiy brauzerda tekshirish imkoni
        telegram_user = {"id": int(x_dev_user), "first_name": "Dev", "last_name": x_dev_user}
    else:
        raise HTTPException(status_code=401, detail="Autentifikatsiya ma'lumoti yo'q")

    user = db.scalar(select(User).where(User.tg_id == telegram_user["id"]))

    username = (telegram_user.get("username") or "").strip() or None

    if not user:
        full_name = f'{telegram_user.get("first_name", "")} {telegram_user.get("last_name", "")}'.strip()
        user = User(tg_id=telegram_user["id"], full_name=full_name, username=username)
        db.add(user)
        db.commit()
        db.refresh(user)
    elif x_init_data and user.username != username:
        user.username = username
        db.commit()

    return user


def get_current_teacher(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> Teacher:
    """Joriy foydalanuvchiga tegishli Teacher yozuvi borligini tekshiradi (login/parolsiz)."""

    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    if not teacher:
        raise HTTPException(status_code=403, detail="Siz hali o'qituvchi sifatida ro'yxatdan o'tmagansiz")

    return teacher


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Joriy foydalanuvchining Telegram ID'si ADMIN_ID bilan mosligini tekshiradi."""

    if not ADMIN_ID or user.tg_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Bu bo'lim faqat admin uchun")

    return user
