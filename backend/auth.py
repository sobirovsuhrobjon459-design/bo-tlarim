"""
Autentifikatsiya fayli:

1) Telegram initData tekshiruvi (validate_init_data)
2) O'qituvchi parol/token funksiyalari (hash_password, create_teacher_token, ...)
3) FastAPI Depends() orqali ishlatiladigan umumiy funksiyalar (get_db, get_current_user, ...)
"""

import hashlib
import hmac
import json
import os
import time
from urllib.parse import parse_qsl

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .database import ADMIN_ID, BOT_TOKEN, DEV_MODE, SECRET_KEY, SessionLocal, Teacher, User

INIT_DATA_MAX_AGE_SECONDS = 24 * 60 * 60  # 1 kun
TEACHER_TOKEN_LIFETIME_SECONDS = 7 * 24 * 60 * 60  # 7 kun
PASSWORD_HASH_ITERATIONS = 200_000


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

    if not hmac.compare_digest(expected_hash, received_hash):
        raise InitDataError("imzo noto'g'ri — bu so'rov Telegramdan kelmagan bo'lishi mumkin")

    auth_date = int(parsed_fields.get("auth_date", 0))
    if time.time() - auth_date > INIT_DATA_MAX_AGE_SECONDS:
        raise InitDataError("initData eskirgan, Mini App'ni qaytadan oching")

    user_json = parsed_fields.get("user")
    if not user_json:
        raise InitDataError("foydalanuvchi ma'lumoti topilmadi")

    return json.loads(user_json)


# ========== 2) O'qituvchi parol va token funksiyalari ==========


def hash_password(password: str) -> str:
    """Parolni "tuz (salt) + hash" ko'rinishida qaytaradi, bazada shu saqlanadi."""
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored_hash: str) -> bool:
    """Kiritilgan parolni bazadagi hash bilan solishtiradi."""
    salt_hex, digest_hex = stored_hash.split("$")
    salt = bytes.fromhex(salt_hex)
    expected_digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, PASSWORD_HASH_ITERATIONS).hex()
    return hmac.compare_digest(expected_digest, digest_hex)


def create_teacher_token(user_id: int) -> str:
    """O'qituvchi login qilgandan keyin frontendga beriladigan, 7 kun amal qiladigan token."""
    expires_at = int(time.time()) + TEACHER_TOKEN_LIFETIME_SECONDS
    payload = f"{user_id}:{expires_at}"
    signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}:{signature}"


def verify_teacher_token(token: str, user_id: int) -> bool:
    """Frontend yuborgan tokenni tekshiradi: imzosi to'g'rimi, muddati o'tmaganmi, shu userga tegishlimi."""
    try:
        user_id_str, expires_str, signature = token.split(":")
        payload = f"{user_id_str}:{expires_str}"
        expected_signature = hmac.new(SECRET_KEY.encode(), payload.encode(), hashlib.sha256).hexdigest()

        if not hmac.compare_digest(expected_signature, signature):
            return False

        return int(user_id_str) == user_id and int(expires_str) > time.time()
    except (ValueError, AttributeError):
        return False


# ========== 3) FastAPI Depends() funksiyalari ==========


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

    if not user:
        full_name = f'{telegram_user.get("first_name", "")} {telegram_user.get("last_name", "")}'.strip()
        user = User(tg_id=telegram_user["id"], full_name=full_name)
        db.add(user)
        db.commit()
        db.refresh(user)

    return user


def get_current_teacher(
    x_teacher_token: str = Header(default=""),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Teacher:
    """Joriy foydalanuvchining o'qituvchi sifatida kirganini tekshiradi."""

    if not verify_teacher_token(x_teacher_token, user.id):
        raise HTTPException(status_code=401, detail="Qayta kiring")

    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    if not teacher:
        raise HTTPException(status_code=403, detail="Siz o'qituvchi sifatida ro'yxatdan o'tmagansiz")

    return teacher


def get_current_admin(user: User = Depends(get_current_user)) -> User:
    """Joriy foydalanuvchining Telegram ID'si ADMIN_ID bilan mosligini tekshiradi."""

    if not ADMIN_ID or user.tg_id != ADMIN_ID:
        raise HTTPException(status_code=403, detail="Bu bo'lim faqat admin uchun")

    return user
