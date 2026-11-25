"""
Ma'lumotlar bazasi fayli: .env sozlamalari, bazaga ulanish, barcha jadvallar
(modellar) va API so'rov sxemalari (Pydantic) shu bitta faylda.
"""

import hashlib
import os
import re
from typing import Literal
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import BigInteger, ForeignKey, String, Text, UniqueConstraint, case, create_engine, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, object_session, relationship, sessionmaker

# ========== 1) Sozlamalar (.env) ==========

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def utcnow() -> datetime:
    """
    Hozirgi UTC vaqti, mintaqa belgisisiz.

    datetime.utcnow() Python 3.12'dan boshlab eskirgan deb ogohlantiradi, lekin
    bazadagi ustunlar mintaqasiz (naive) saqlangani uchun mintaqani olib
    tashlaymiz — aks holda vaqtlarni solishtirganda xato chiqadi.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)


BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# MUHIM: Render'ning bepul tarifida fayl tizimi vaqtinchalik — har deploydan
# keyin (va xizmat uyqudan uyg'onganda) fayllar o'chib ketadi. Ya'ni SQLite
# bazasi bilan ishlasa, o'qituvchi akkaunti ham, testlar ham yo'qoladi.
# Shuning uchun productionda DATABASE_URL ga doimiy PostgreSQL manzili
# qo'yiladi; lokal kompyuterda esa SQLite yetarli.
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./backend/app.db")

# Render va Neon manzilni "postgres://..." ko'rinishida beradi. SQLAlchemy esa
# "postgresql+psycopg://..." kutadi (qaysi drayver ishlatilishini ochiq yozib
# qo'yamiz — shunda SQLAlchemy versiyasi o'zgarsa ham ishlayveradi).
for prefix in ("postgres://", "postgresql://"):
    if DATABASE_URL.startswith(prefix):
        DATABASE_URL = "postgresql+psycopg://" + DATABASE_URL[len(prefix) :]
        break

# 1 bo'lsa, Telegram'siz (oddiy brauzerda) ham sinash mumkin bo'ladi.
#
# MUHIM: bu rejimda "X-Dev-User" header'i Telegram imzosini butunlay almashtiradi,
# ya'ni istalgan odam istalgan foydalanuvchi (hatto admin) bo'lib kirishi mumkin.
# Shuning uchun u FAQAT lokal SQLite bazasi bilan ishlaganda yoqiladi — Render'da
# DEV_MODE=1 qolib ketsa ham, PostgreSQL bo'lgani uchun o'zi o'chadi.
DEV_MODE = os.getenv("DEV_MODE", "0") == "1" and DATABASE_URL.startswith("sqlite")

# Shu Telegram ID'larga ega foydalanuvchilar "ADMIN" bo'limini ko'radi va boshqara oladi.
# Bir nechta admin — vergul bilan: ADMIN_ID=111111,222222. Bildirishnomalar hammasiga boradi.
ADMIN_IDS: tuple[int, ...] = tuple(
    int(part) for part in (os.getenv("ADMIN_ID") or "").replace(";", ",").replace(" ", ",").split(",") if part.strip().isdigit()
)
ADMIN_ID = ADMIN_IDS[0] if ADMIN_IDS else 0  # birinchi (asosiy) admin


def is_admin_tg(tg_id: int | None) -> bool:
    return bool(tg_id) and tg_id in ADMIN_IDS

# Pullik testda o'quvchi to'laganda, platforma ushlab qoladigan foiz (qolgani o'qituvchiga boradi).
PLATFORM_COMMISSION_PERCENT = int(os.getenv("PLATFORM_COMMISSION_PERCENT") or 20)

# Yangi o'qituvchi ro'yxatdan o'tganda beriladigan sovg'a tokenlar.
# Har bir bepul test natijasi uchun 1 token sarflanadi.
WELCOME_CREDITS = int(os.getenv("WELCOME_CREDITS") or 8000)
# Referal: taklif qilingan odam o'qituvchi bo'lsa, havola egasiga beriladigan token
REFERRAL_CREDITS = int(os.getenv("REFERRAL_CREDITS") or 10)

# O'qituvchilarga pul balansini to'ldirish uchun ko'rsatiladigan karta raqami.
ADMIN_CARD_NUMBER = os.getenv("ADMIN_CARD_NUMBER", "")

# Payme Merchant API ma'lumotlari (token sotib olish uchun). Payme Business
# kabinetidan olinadi: https://business.payme.uz
PAYME_MERCHANT_ID = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY = os.getenv("PAYME_SECRET_KEY", "")
# Payme sinov rejimi: to'lov sahifasi test.paycom.uz (haqiqiy pul yechilmaydi).
# Kassani ishga tushirishdan oldin Payme sinovlarini o'tish uchun — 1 qo'ying,
# PAYME_SECRET_KEY o'rniga esa kassaning TEST kalitini. Sinovdan keyin olib tashlang.
PAYME_TEST_MODE = os.getenv("PAYME_TEST_MODE", "").strip().lower() in ("1", "true", "yes")

# Click (my.click.uz) — Uzcard, Humo, Visa, Mastercard va Click ilovasi.
# Uchalasi ham to'ldirilsa, "Click orqali to'lash" tugmasi paydo bo'ladi.
CLICK_SERVICE_ID = os.getenv("CLICK_SERVICE_ID", "")
CLICK_MERCHANT_ID = os.getenv("CLICK_MERCHANT_ID", "")
CLICK_SECRET_KEY = os.getenv("CLICK_SECRET_KEY", "")


# ========== 2) Bazaga ulanish ==========

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite bitta threadda ishlashni talab qiladi, FastAPI esa turli threadlardan
# murojaat qilishi mumkin — shuning uchun buni o'chiramiz.
connect_args = {"check_same_thread": False} if IS_SQLITE else {}

# pool_pre_ping: PostgreSQL ulanishi uzilib qolgan bo'lsa (masalan, xizmat
# uyquga ketib qaytgandan keyin), SQLAlchemy uni avtomatik qayta ulaydi.
# Ulanishlar havzasi: imtihon paytida yuzlab o'quvchi bir vaqtda so'rov yuboradi —
# standart 5 (+10) ulanishda so'rovlar navbatda turib qolardi. Neon'ning "-pooler"
# manzili ko'p ulanishni ko'taradi. SQLite'da (lokal sinov) havza sozlanmaydi.
pool_options = {} if IS_SQLITE else {"pool_size": 10, "max_overflow": 20, "pool_timeout": 60}
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=not IS_SQLITE, **pool_options)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    """Barcha jadval (model) klasslari shundan meros oladi."""


# ========== 3) Jadvallar (modellar) ==========


class User(Base):
    """Har bir Telegram foydalanuvchisi (o'quvchi ham, o'qituvchi ham) shu jadvalda saqlanadi."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    tg_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(200), default="")
    # Telegram username (@siz) — masalan, maktab asoschisiga yozish havolasi uchun.
    # Har kirishda yangilanadi: foydalanuvchi uni o'zgartirishi yoki o'chirishi mumkin.
    username: Mapped[str | None] = mapped_column(String(64), default=None)
    # Telegram profil rasmi (Mini App ochilganda avtomatik olinadi)
    photo_url: Mapped[str | None] = mapped_column(String(500), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Hisobni o'chirish (Akkaunt → Hisobni o'chirish): yozuv qoladi (to'lovlar, natijalar unga bog'langan),
    # lekin shaxsiy ma'lumot o'chadi va tg_id bo'shatiladi (-id) — odam qaytsa, yangi hisob ochiladi.
    # deleted_tg_hash — eski tg_id'ning izi: qayta ro'yxatdan o'tganda sovg'a token va referal qayta berilmasin.
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_tg_hash: Mapped[str | None] = mapped_column(String(64), default=None, index=True)


def tg_fingerprint(tg_id: int) -> str:
    """O'chirilgan hisobning Telegram ID izi — ID'ning o'zi saqlanmaydi, faqat solishtirish uchun."""
    return hashlib.sha256(f"chittak-deleted-account:{tg_id}".encode()).hexdigest()


def had_deleted_account(session, tg_id: int) -> bool:
    """Shu Telegram akkaunt ilgari Chittak hisobini o'chirganmi."""
    return session.scalar(select(User.id).where(User.deleted_tg_hash == tg_fingerprint(tg_id)).limit(1)) is not None


def welcome_credits_for(session, tg_id: int) -> int:
    """Yangi o'qituvchiga sovg'a token — hisobini o'chirib, qayta ochgan odamga qayta berilmaydi."""
    return 0 if had_deleted_account(session, tg_id) else WELCOME_CREDITS


class Teacher(Base):
    """
    Foydalanuvchi o'qituvchi sifatida ro'yxatdan o'tganda shu jadvalga yozuv qo'shiladi.

    Alohida login/parol yo'q — Telegram orqali kim ekani allaqachon aniq (tg_id).
    Ro'yxatdan o'tishda faqat ism-familiya so'raladi (Telegram ismi ishonchli
    bo'lmasligi mumkin, masalan taxallus yoki emoji bo'lishi mumkin).
    """

    __tablename__ = "teachers"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)

    # Pul balansi: pullik testlardan tushgan ulush shu yerga qo'shiladi (pul, so'mda).
    balance: Mapped[int] = mapped_column(default=0)

    # Token balansi: bepul testda har bir o'quvchi natijasi uchun 1 dona sarflanadi.
    credits: Mapped[int] = mapped_column(default=0)

    # Oylik obuna: shu vaqtgacha token umuman yechilmaydi (cheksiz tekshiruv).
    # O'qituvchi token yoki obunadan birini o'zi tanlab sotib oladi.
    subscription_until: Mapped[datetime | None] = mapped_column(default=None)

    # Pullik test: o'quvchi pulni shu kartaga TO'G'RIDAN-TO'G'RI o'tkazadi va chek
    # yuboradi, o'qituvchi tasdiqlaydi (platforma pulni ushlamaydi).
    card_number: Mapped[str | None] = mapped_column(String(30), default=None)
    card_holder: Mapped[str | None] = mapped_column(String(100), default=None)
    # Amal qilish muddati "MM/YY" — faqat o'qituvchiga ko'rinadi, o'quvchiga emas
    card_expiry: Mapped[str | None] = mapped_column(String(5), default=None)

    # Pullik testlardan platforma ushlab qoladigan foiz — shu muallif uchun alohida (admin belgilaydi).
    # Bo'sh — admin panelidagi umumiy foiz ishlatiladi.
    commission_percent: Mapped[int | None] = mapped_column(default=None)

    user: Mapped["User"] = relationship()
    tests: Mapped[list["Test"]] = relationship(back_populates="teacher")


# Token va pul balansi faqat shu funksiyalar orqali o'zgaradi.
#
# Nega "teacher.credits += n" emas: u holda server avval qiymatni o'qib, keyin
# bazaga TAYYOR raqamni yozadi. Ikki so'rov bir vaqtda kelsa (masalan Payme
# 1000 token yozayotganda o'quvchi testni tugatsa), biri ikkinchisining
# yozganini ustidan bosib, o'qituvchi sotib olgan tokeni yo'qolardi.
# Bu yerda hisob bazaning o'zida bajariladi: "credits = credits + n".


def _adjust_teacher(teacher: "Teacher", column: str, delta: int) -> None:
    session = object_session(teacher)
    current = getattr(Teacher, column)
    # Kamaytirilganda noldan pastga tushmaydi (max(0, ...) bilan bir xil)
    new_value = current + delta if delta >= 0 else case((current + delta > 0, current + delta), else_=0)
    session.execute(
        update(Teacher).where(Teacher.id == teacher.id).values({column: new_value}),
        execution_options={"synchronize_session": False},
    )
    session.refresh(teacher, [column])


def add_credits(teacher: "Teacher", delta: int) -> None:
    """O'qituvchi tokenini atomar o'zgartiradi (manfiy delta — yechish, 0 dan pastga tushmaydi)."""
    _adjust_teacher(teacher, "credits", delta)


def add_balance(teacher: "Teacher", delta: int) -> None:
    """O'qituvchi pul balansini atomar o'zgartiradi (manfiy delta — yechish, 0 dan pastga tushmaydi)."""
    _adjust_teacher(teacher, "balance", delta)


def withdraw_balance(teacher: "Teacher", amount: int) -> bool:
    """Balansdan pul yechadi — faqat yetarli bo'lsa. Yetmasa, hech narsa o'zgarmaydi va False qaytadi."""

    session = object_session(teacher)
    result = session.execute(
        update(Teacher)
        .where(Teacher.id == teacher.id, Teacher.balance >= amount)
        .values(balance=Teacher.balance - amount),
        execution_options={"synchronize_session": False},
    )
    session.refresh(teacher, ["balance"])
    return result.rowcount == 1


class Test(Base):
    """O'qituvchi yaratgan test (bir nechta savoldan iborat)."""

    __tablename__ = "tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100))
    topic: Mapped[str] = mapped_column(String(200), default="")
    price: Mapped[int] = mapped_column(default=0)
    duration: Mapped[int] = mapped_column(default=30)  # daqiqalarda

    # "full"  — savol matni va variantlar to'liq botda kiritiladi
    # "key"   — faqat javoblar kaliti kiritiladi (savollar tashqarida, masalan kanalda)
    mode: Mapped[str] = mapped_column(String(10), default="full")
    # Test turi: "rash" — Rasch modeli (ball, A+ … NC daraja); "boshlangich" — to'g'ri javoblar, foiz va 5 ballik baho
    test_type: Mapped[str] = mapped_column(String(15), default="rash")

    # Telegram guruhga bog'lash (ixtiyoriy)
    telegram_group_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    enrollment_type: Mapped[str] = mapped_column(String(10), default="open")  # "open" | "closed"
    start_time: Mapped[datetime | None] = mapped_column(default=None)
    end_time: Mapped[datetime | None] = mapped_column(default=None)

    # "active" — test jarayonda (o'quvchi qo'shish/javob kiritish mumkin)
    # "closed" — o'qituvchi yakunlagan, natijalar olingan
    status: Mapped[str] = mapped_column(String(10), default="active")

    # Natijalar o'qituvchiga yuborilgan vaqt (bo'sh bo'lsa — hali yuborilmagan).
    # Test vaqti tugagach, hisobot o'zi bir marta yuboriladi.
    results_sent_at: Mapped[datetime | None] = mapped_column(default=None)
    # O'qituvchi testni bot foydalanuvchilarining barchasiga e'lon qilgan vaqt (bir marta)
    broadcast_at: Mapped[datetime | None] = mapped_column(default=None)

    # Majburiy obuna: shu kanalga obuna bo'lmagan o'quvchi testni boshlay olmaydi.
    # "@kanal_nomi" ko'rinishida saqlanadi; bo'sh bo'lsa, tekshiruv yo'q.
    required_channel: Mapped[str | None] = mapped_column(String(100), default=None)

    # Javoblar varaqasi ko'rinishi: 2 — "O'quvchi raqami" doirachalari bilan (skaner
    # o'quvchini o'zi taniydi); 3 — kvadrat burchaklar, chetlarda tekislash belgilari
    # va pastda imtihon kodi (boshqa imtihon tituli bilan adashmaydi); 4 — o'quvchi ID
    # vertikal 3 ustunda (0–9); 5 — ID 4 xonali (maktab o'quvchilari ID'si 1001, 1002 ...).
    # Eski testlar o'z versiyasida qoladi — oldin chop etilgan varaqlar ham to'g'ri skanerlanishi uchun.
    # 6 — ixcham; 7 — bloklari kvadratli; 8–10 — EvalBee uslubi; 11 — doirachalar o'lchami va oralig'i doim bir xil;
    # 12 — yozma savollar uchun pastda yozish kataklari (36a, 36b …), 33–35 (A–F) qatorlari ustunga sig'adi
    sheet_version: Mapped[int] = mapped_column(default=14)  # 13 — DTM tituli rasmiy ko'rinishda; 14 — sertifikat bo'limi bilan
    # Esse (Ona tili 45): maksimal bali (24). Bo'sh — testda esse yo'q
    essay_max: Mapped[int | None] = mapped_column(default=None)
    # Esseni kim baholaydi: False — o'qituvchi, True — AI (o'quvchi topshirgach ballni o'zi qo'yadi,
    # o'qituvchi istalgan vaqtda o'zgartira oladi)
    essay_ai: Mapped[bool] = mapped_column(default=False)
    # Esse mavzusi: AI baholasa — majburiy (AI mavzuga moslikni tekshiradi), o'quvchiga test ichida ko'rinadi
    essay_topic: Mapped[str | None] = mapped_column(Text, default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # O'qituvchi "o'chirgan" test. Bazadan butunlay o'chirilmaydi: unga bog'liq
    # to'lovlar (pul hisobi) va o'quvchilar natijalari saqlanib qoladi, test esa
    # hamma joydan yashiriladi. Kerak bo'lsa admin uni qayta tiklay oladi.
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    # Avtomatik tozalash (backend/main.py, "Testlarni avtomatik yopish va o'chirish"): har qanday test
    # yaratilganidan (qayta ochilganidan) 10 kun o'tib o'zi yopiladi, ma'lumotlari ZIP bo'lib
    # o'qituvchiga yuboriladi va test o'chiriladi.
    closed_at: Mapped[datetime | None] = mapped_column(default=None)  # oxirgi marta yopilgan vaqt
    reopened_at: Mapped[datetime | None] = mapped_column(default=None)  # oxirgi marta qayta ochilgan vaqt
    delete_warned_at: Mapped[datetime | None] = mapped_column(default=None)  # "3 kundan keyin o'chadi" xabari
    archive_tried_at: Mapped[datetime | None] = mapped_column(default=None)  # ZIP yuborishga oxirgi urinish
    auto_deleted_at: Mapped[datetime | None] = mapped_column(default=None)  # ZIP yetkazilib, test o'chirilgan
    # Bo'limlar (masalan Algebra / Geometriya) — JSON: [{"name": "Algebra", "numbers": [1, 2, ...]}]
    sections: Mapped[str] = mapped_column(Text, default="")

    teacher: Mapped["Teacher"] = relationship(back_populates="tests")
    questions: Mapped[list["Question"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", order_by="Question.id"
    )
    key_questions: Mapped[list["KeyQuestion"]] = relationship(
        back_populates="test", cascade="all, delete-orphan", order_by="KeyQuestion.number"
    )


class Question(Base):
    """Testning bitta savoli — 'full' rejimida ishlatiladi (4 ta variant: A, B, C, D)."""

    __tablename__ = "questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    text: Mapped[str] = mapped_column(Text)
    a: Mapped[str] = mapped_column(Text)
    b: Mapped[str] = mapped_column(Text)
    c: Mapped[str] = mapped_column(Text)
    d: Mapped[str] = mapped_column(Text)
    correct: Mapped[str] = mapped_column(String(1))  # "A" | "B" | "C" | "D"

    test: Mapped["Test"] = relationship(back_populates="questions")


class KeyQuestion(Base):
    """
    Faqat javoblar kaliti — 'key' rejimida ishlatiladi.

    Savol matni saqlanmaydi (chunki haqiqiy savollar tashqarida, masalan
    o'qituvchining Telegram kanalida joylashtirilgan bo'ladi). Bu yerda faqat:
    savol raqami, turi va to'g'ri javob(lar) saqlanadi.

    question_type:
      "single" — bitta to'g'ri javob (A/B/C/D...), correct="B"
      "multi"  — bir nechta to'g'ri javob, correct="A,C"
      "text"   — yozma javob (masalan Milliy sertifikatdagi kabi), o'quvchi
                 matn kiritadi; correct bir nechta qabul qilinadigan variantni
                 "|" bilan ajratib saqlaydi, masalan "15|fifteen|o'n besh"
    """

    __tablename__ = "key_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    number: Mapped[int]

    # Ko'rinadigan nom. Odatda bo'sh (u holda "12-savol" deb ko'rsatiladi), lekin
    # Milliy sertifikatdagi kabi yozma savol a/b qismlarga bo'linsa — "36a", "36b".
    label: Mapped[str | None] = mapped_column(String(20), default=None)
    question_type: Mapped[str] = mapped_column(String(10), default="single")  # "single" | "multi" | "text"
    option_count: Mapped[int] = mapped_column(default=4)  # "text" turida ishlatilmaydi
    correct: Mapped[str] = mapped_column(String(300))  # "B" | "A,C" | "15|fifteen"

    test: Mapped["Test"] = relationship(back_populates="key_questions")


class TestVariant(Base):
    """
    Testning qo'shimcha varianti (B, C, D): savollar tuzilishi asosiy kalit bilan bir xil,
    faqat to'g'ri javoblar boshqacha. answers — JSON: {"1": "C", "2": "A,D", "36": "15"}.
    """

    __tablename__ = "test_variants"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    name: Mapped[str] = mapped_column(String(2))
    answers: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)


class Attempt(Base):
    """
    O'quvchining bitta testni ishlash urinishi (va natijasi).

    Ochiq testda o'quvchining o'zi ishlaydi — user_id to'ldiriladi.
    Yopiq testda esa o'qituvchi o'quvchini o'zi qo'shadi va javoblarini kiritadi —
    bunda o'quvchining Telegram akkaunti bo'lmasligi mumkin, shuning uchun
    user_id bo'sh qoladi va uning o'rniga offline_name ishlatiladi.
    """

    __tablename__ = "test_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    offline_name: Mapped[str | None] = mapped_column(String(200), default=None)
    # Javoblar varaqasidagi o'quvchi raqami (1–999) — o'qituvchi qo'shgan o'quvchilarga beriladi
    sheet_number: Mapped[int | None] = mapped_column(default=None)
    # Qaysi variant kaliti bilan tekshiriladi (B, C, D); bo'sh — asosiy (A)
    variant: Mapped[str | None] = mapped_column(String(2), default=None)
    # Maktab sinfidagi o'quvchi (offline imtihon sinfga biriktirilganda) — natija sinfga ham yoziladi
    class_student_id: Mapped[int | None] = mapped_column(default=None, index=True)
    # DTM testi: o'qituvchi shu natijaga kiritgan sertifikatlar (JSON): {"3": {"kind": "ms", "value": "57.90"},
    # "0": {"kind": "cert"}, "1": {"kind": "none"}} — blok raqami bo'yicha; o'quvchi profilidagisidan ustun
    dtm_certificates: Mapped[str | None] = mapped_column(Text, default=None)
    started_at: Mapped[datetime] = mapped_column(default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    # pending  — o'qituvchi yopiq testga qo'shgan, javoblari hali kiritilmagan
    # in_progress — o'quvchi testni ishlayapti
    # done     — yakunlangan va tekshirilgan
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # pending | in_progress | done
    correct: Mapped[int] = mapped_column(default=0)
    wrong: Mapped[int] = mapped_column(default=0)
    percent: Mapped[float] = mapped_column(default=0)
    seconds: Mapped[int] = mapped_column(default=0)

    # O'quvchi test ishlayotganda belgilab borgan javoblari, JSON ko'rinishida:
    # {"1": "B", "36a": "2\\sqrt{3}"}. Telegram Mini App juda oson yopiladi
    # (pastga surish, qo'ng'iroq kelishi), shuning uchun javoblar yakunlashni
    # kutmasdan serverda saqlanib boradi — aks holda o'quvchi qaytib kelganda
    # hammasini boshidan yozishi kerak bo'lardi.
    #
    # Yakunlanganda tozalanadi: natija StudentAnswer jadvalida qoladi.
    saved_answers: Mapped[str | None] = mapped_column(Text, default=None)

    # Esse: o'quvchi ilovada yozgan matn (onlayn testda) va o'qituvchi qo'ygan ball (0–24).
    # Ball bo'sh — hali baholanmagan
    essay_text: Mapped[str | None] = mapped_column(Text, default=None)
    essay_score: Mapped[int | None] = mapped_column(default=None)
    # AI taklifi (o'qituvchi so'raganda): ball va izoh. Ballni baribir o'qituvchi qo'yadi
    essay_ai_score: Mapped[int | None] = mapped_column(default=None)
    essay_ai_comment: Mapped[str | None] = mapped_column(Text, default=None)


class StudentAnswer(Base):
    """
    O'quvchining har bir savolga bergan aniq javobi.

    question_ref ma'nosi testning rejimiga bog'liq:
    - 'full' rejimida — Question.id
    - 'key' rejimida — KeyQuestion.number (savol tartib raqami)
    Ikkala rejim ham qo'llab-quvvatlanishi uchun bu oddiy raqam, chet kalit (FK) emas.
    """

    __tablename__ = "student_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("test_attempts.id"), index=True)
    question_ref: Mapped[int] = mapped_column(index=True)
    chosen: Mapped[str | None] = mapped_column(String(300), default=None)
    is_correct: Mapped[bool] = mapped_column(default=False)


class SheetImage(Base):
    """
    Skanerlangan titulning rasmi (tekislangan, oq-qora JPEG) — o'quvchi natijasiga biriktiriladi:
    o'qituvchi va o'quvchi profilini ko'radiganlar keyin asl varaqni ochib ko'ra oladi.
    Ko'p sahifali titulda har sahifa alohida yozuv. Qayta skanerlansa — yangisi bilan almashadi.
    """

    __tablename__ = "sheet_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("test_attempts.id"), index=True)
    page: Mapped[int] = mapped_column(default=1)
    image: Mapped[str] = mapped_column(Text)  # data:image/jpeg;base64,…
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class EssayImage(Base):
    """
    Qog'ozga qo'lda yozilgan esse rasmi (o'quvchi test ichida yuklaydi): har sahifa alohida yozuv.
    AI matnga aylantiradi, o'quvchi uni tekshirib tuzatadi; o'qituvchi asl rasmni matn bilan solishtira oladi.
    """

    __tablename__ = "essay_images"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("test_attempts.id"), index=True)
    page: Mapped[int] = mapped_column(default=1)
    image: Mapped[str] = mapped_column(Text)  # data:image/jpeg;base64,…
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PupilAccount(Base):
    """
    Maktab o'quvchisining ilovaga kirishi: o'quvchi maktabini topadi, login va parol bilan kiradi.
    Login — o'quvchining ID'si (bo'lmasa — maktab ichida takrorlanmaydigan 6 xonali raqam).
    Boshlang'ich parolni maktab beradi (kurator ro'yxatni chop etadi) va u ochiq saqlanadi — o'quvchi
    birinchi kirishda o'z parolini o'rnatadi, shundan keyin faqat uning PBKDF2 izi qoladi.
    Bir akkaunt — bitta o'quvchi (asl sinfidagi yozuvi); o'quvchi va ota-onasi shu login bilan kiradi.
    """

    __tablename__ = "pupil_accounts"
    __table_args__ = (UniqueConstraint("school_id", "login"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), unique=True, index=True)
    login: Mapped[str] = mapped_column(String(20))
    initial_password: Mapped[str | None] = mapped_column(String(20), default=None)  # maktab bergan, almashtirilguncha
    password_hash: Mapped[str | None] = mapped_column(String(200), default=None)  # o'quvchi o'rnatgan parol izi
    must_change: Mapped[bool] = mapped_column(default=True)
    failures: Mapped[int] = mapped_column(default=0)
    locked_until: Mapped[datetime | None] = mapped_column(default=None)
    last_login_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class PupilSession(Base):
    """O'quvchi seansi. Tokenning o'zi saqlanmaydi — faqat SHA-256 izi (AppSession kabi)."""

    __tablename__ = "pupil_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("pupil_accounts.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)


class WrittenAnswer(Base):
    """
    Tituldagi yozma javob katagi (36a, 36b …): skaner qirqib olgan rasm va o'qituvchi bahosi.

    O'quvchi javobni qog'ozga qo'lda yozadi — skaner uni o'qimaydi, faqat katakni rasm qilib saqlaydi.
    O'qituvchi "Yozma javoblar" bo'limida rasmga qarab ✓ (to'g'ri) yoki ✗ (xato) qo'yadi; baho
    StudentAnswer.is_correct'ga o'tadi (Rasch ham shundan hisoblaydi). Bo'sh katak o'zi ✗ bo'ladi.
    question_ref — KeyQuestion.number.
    """

    __tablename__ = "written_answers"

    id: Mapped[int] = mapped_column(primary_key=True)
    attempt_id: Mapped[int] = mapped_column(ForeignKey("test_attempts.id"), index=True)
    question_ref: Mapped[int]
    image: Mapped[str] = mapped_column(Text)  # data:image/jpeg;base64,…
    # True — to'g'ri, False — xato, None — hali tekshirilmagan
    verdict: Mapped[bool | None] = mapped_column(default=None)
    blank: Mapped[bool] = mapped_column(default=False)  # katak bo'sh (skaner aniqladi) — o'zi ✗
    checked_at: Mapped[datetime | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # AI (Claude) o'qigan matn va ishonchi. ai_status: None — o'qilmagan, "queued" — navbatda,
    # "done" — o'qildi, "error" — o'qib bo'lmadi (o'qituvchi o'zi baholaydi)
    ai_text: Mapped[str | None] = mapped_column(String(300), default=None)
    ai_sure: Mapped[bool | None] = mapped_column(default=None)
    ai_status: Mapped[str | None] = mapped_column(String(10), default=None)
    # Bahoni AI qo'ydi (aniq o'qilgan va kalitga mos — ✓); o'qituvchi o'zgartirsa — False
    auto_verdict: Mapped[bool] = mapped_column(default=False)


class Payment(Base):
    """
    O'quvchining pullik testga to'lovi — Payme orqali amalga oshiriladi.

    Payme'ning webhook'i shu yozuvni "pending" -> "paid" holatiga o'tkazadi va
    shundagina o'quvchi testni boshlay oladi (qarang: _is_paid). To'lovning
    o'qituvchiga tegishli ulushi ham aynan shu paytda balansga qo'shiladi —
    ya'ni haqiqiy pul kelmaguncha hech kimning balansi oshmaydi.
    """

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    amount: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | cancelled | failed
    # payme | click | card (o'qituvchi kartasiga to'g'ridan-to'g'ri, chek bilan)
    provider: Mapped[str] = mapped_column(String(20), default="payme")
    # Karta to'lovi cheki — Telegram'dagi file_id (rasm serverda saqlanmaydi)
    receipt_file_id: Mapped[str | None] = mapped_column(String(200), default=None)
    # O'qituvchi chatidagi chek xabari (qadash/qadovdan olish uchun) va eslatmalar hisobi
    receipt_message_id: Mapped[int | None] = mapped_column(default=None)
    reminded_at: Mapped[datetime | None] = mapped_column(default=None)
    reminder_count: Mapped[int] = mapped_column(default=0)
    payme_transaction_id: Mapped[str | None] = mapped_column(String(50), default=None, unique=True, index=True)
    # Payme tranzaksiyasi: bizda yaratilgan vaqt, Payme yuborgan "time" (ms) va bekor qilish sababi
    payme_created_at: Mapped[datetime | None] = mapped_column(default=None)
    payme_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    payme_cancel_reason: Mapped[int | None] = mapped_column(default=None)
    click_trans_id: Mapped[str | None] = mapped_column(String(50), default=None, index=True)
    # To'lov o'tgan paytda muallif balansiga yozilgan ulush (so'm). Foiz keyin o'zgarsa ham
    # bekor qilishda aynan shu summa qaytarib olinadi. Bo'sh — eski to'lov (foiz saqlanmagan).
    teacher_share: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    performed_at: Mapped[datetime | None] = mapped_column(default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)

    test: Mapped["Test"] = relationship()


class WithdrawalRequest(Base):
    """
    O'qituvchining pul balansini yechib olish so'rovi.

    Pul balansi — pullik testlardan tushgan daromad. O'qituvchi shu puldan
    qancha yechmoqchi ekanini va qaysi kartaga o'tkazilishini yuboradi, admin
    tasdiqlagach (haqiqiy pulni qo'lda o'tkazgach), balansdan shuncha yechiladi.
    """

    __tablename__ = "withdrawal_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    amount: Mapped[int]
    card_number: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class CreditPurchase(Base):
    """
    O'qituvchining xaridi: token yoki oylik obuna (Payme, Click yoki karta orqali).

    To'lov tasdiqlanganda (Payme/Click webhook yoki admin chekni ko'rib) yozuv
    "pending" -> "paid" bo'ladi va xarid kuchga kiradi: tokenda — teacher.credits
    oshadi, obunada — teacher.subscription_until uzayadi (qarang: _grant_purchase).
    """

    __tablename__ = "credit_purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    # "tokens" — token paketi (credits ta); "subscription" — months oylik obuna (credits = 0);
    # "broadcast" — test_id testini bot foydalanuvchilariga pullik e'lon qilish (credits = 0)
    kind: Mapped[str] = mapped_column(String(15), default="tokens")
    test_id: Mapped[int | None] = mapped_column(ForeignKey("tests.id"), default=None, index=True)
    # "verify" — school_id muassasasiga ✓ (tasdiqlangan) belgisini sotib olish (credits = 0)
    school_id: Mapped[int | None] = mapped_column(ForeignKey("private_schools.id"), default=None, index=True)
    months: Mapped[int] = mapped_column(default=0)
    credits: Mapped[int]
    amount: Mapped[int]  # so'm
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | cancelled
    payme_transaction_id: Mapped[str | None] = mapped_column(String(50), default=None, unique=True, index=True)
    # Payme tranzaksiyasi: bizda yaratilgan vaqt, Payme yuborgan "time" (ms) va bekor qilish sababi
    payme_created_at: Mapped[datetime | None] = mapped_column(default=None)
    payme_time: Mapped[int | None] = mapped_column(BigInteger, default=None)
    payme_cancel_reason: Mapped[int | None] = mapped_column(default=None)
    click_trans_id: Mapped[str | None] = mapped_column(String(50), default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    performed_at: Mapped[datetime | None] = mapped_column(default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)

    # To'lov usuli:
    #   "payme" — Payme sahifasi orqali, webhook avtomatik tasdiqlaydi
    #   "card"  — o'qituvchi admin kartasiga o'zi pul o'tkazadi va chek rasmini
    #             yuboradi; admin chekni ko'rib, bir bosishda tasdiqlaydi
    method: Mapped[str] = mapped_column(String(10), default="payme")

    # Karta orqali to'lovda chek rasmi. Rasmning o'zi serverda saqlanmaydi
    # (Render'da fayllar vaqtinchalik) — u admin chatiga yuboriladi va
    # Telegram'ning file_id'si saqlanadi, kerak bo'lsa shu orqali qayta olinadi.
    receipt_file_id: Mapped[str | None] = mapped_column(String(200), default=None)

    teacher: Mapped["Teacher"] = relationship()
    test: Mapped["Test | None"] = relationship()
    school: Mapped["PrivateSchool | None"] = relationship()


class PrivateSchool(Base):
    """
    Xususiy maktab. Uni birinchi o'qituvchi (egasi) viloyat va tumanni tanlab
    yaratadi; qolgan o'qituvchilar maktabning maxsus kodi bilan qo'shiladi.
    """

    __tablename__ = "private_schools"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    region: Mapped[str] = mapped_column(String(100))
    district: Mapped[str] = mapped_column(String(100))
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    owner_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Oylik o'qish to'lovi (so'm) — barcha o'quvchilar uchun bitta summa
    monthly_fee: Mapped[int] = mapped_column(default=0)
    # Muassasa turi: school (xususiy maktab) | course (o'quv markazi) | club (to'garak).
    # Hammasi bir xil tizimda ishlaydi; o'qituvchi har turdan bittasiga a'zo bo'la oladi
    kind: Mapped[str] = mapped_column(String(10), default="school", index=True)
    # Platforma admini o'chirgan: ro'yxatlarda ko'rinmaydi, kod/havola bilan qo'shilib bo'lmaydi.
    # Ma'lumotlar (sinflar, o'quvchilar, to'lovlar, davomat) bazada qoladi — tiklash mumkin
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    # Maktab admini o'chirishni so'radi (kod bilan tasdiqlab): 3 kundan keyin o'chadi, shu orada bekor qilsa bo'ladi
    delete_requested_at: Mapped[datetime | None] = mapped_column(default=None)
    # Platforma admini haqiqiy muassasa ekanini tasdiqlagan (✓ belgi): qidiruvda tepada va belgi bilan ko'rinadi
    verified_at: Mapped[datetime | None] = mapped_column(default=None)

    members: Mapped[list["SchoolMember"]] = relationship(back_populates="school", order_by="SchoolMember.id")


class SchoolMember(Base):
    """
    O'qituvchining maktabdagi a'zoligi va profili (fanlari).

    O'qituvchi bir vaqtda faqat BITTA maktabda bo'ladi va u yerda doimiy
    qoladi; chiqish uchun maktab kodini kiritishi kerak. Chiqqanda yozuv
    o'chirilmaydi — left_at belgilanadi (tarix saqlanadi).
    """

    __tablename__ = "school_members"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    role: Mapped[str] = mapped_column(String(20), default="teacher")  # owner | teacher
    # Lavozim — admin tayinlaydi: director (direktor) | deputy (zam direktor) | None
    position: Mapped[str | None] = mapped_column(String(20), default=None)
    # Xodimning telefon raqami (+998XXXXXXXXX) — faqat rahbariyat ko'radi
    phone: Mapped[str | None] = mapped_column(String(20), default=None)
    birth_date: Mapped[date | None] = mapped_column(default=None)
    # Pasport seriya va raqami — endi so'ralmaydi va ko'rsatilmaydi (avval kiritilgani bazada qoladi)
    passport: Mapped[str | None] = mapped_column(String(20), default=None)
    subjects: Mapped[str] = mapped_column(Text, default="[]")  # JSON ro'yxat: ["Matematika", ...]
    # Dars jadvali uchun: haftalik soat me'yori (stavka), ish kunlari (JSON [0..6]) va kun ichidagi ish vaqti.
    # Bo'sh — cheklanmagan (istalgan kun/vaqtga dars qo'yiladi)
    weekly_hours: Mapped[int | None] = mapped_column(default=None)
    work_days: Mapped[str | None] = mapped_column(Text, default=None)
    work_start: Mapped[str | None] = mapped_column(String(5), default=None)
    work_end: Mapped[str | None] = mapped_column(String(5), default=None)
    # Har kunning o'z ish vaqti (JSON {"0": ["08:00", "12:00"], ...}); kunda yo'q bo'lsa — work_start/work_end
    work_hours: Mapped[str | None] = mapped_column(Text, default=None)
    joined_at: Mapped[datetime] = mapped_column(default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(default=None)

    school: Mapped["PrivateSchool"] = relationship(back_populates="members")
    user: Mapped["User"] = relationship()


class SchoolInvite(Base):
    """
    O'qituvchiga shaxsiy taklif havolasi — kodsiz qo'shilish uchun.

    Admin "O'qituvchi qo'shish"ni bosib, havolani o'qituvchiga yuboradi.
    Havola BIR MARTALIK va muddatli: tarqalib ketsa ham faqat bir kishi
    ishlata oladi.
    """

    __tablename__ = "school_invites"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    token: Mapped[str] = mapped_column(String(40), unique=True, index=True)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    expires_at: Mapped[datetime]
    used_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    used_at: Mapped[datetime | None] = mapped_column(default=None)


class SchoolClass(Base):
    """
    Maktab sinfi yoki guruhi (masalan "10/2", "Iqtisod guruhi").

    Sinfni DIREKTOR yaratadi va unga KURATOR (sinf rahbari) tayinlaydi.
    Kurator o'quvchilarni qo'shadi va sinfni boshqaradi; boshqa o'qituvchilar
    bu sinfda faqat davomat oladi. O'chirilganda bazada qoladi.
    """

    __tablename__ = "school_classes"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    # O'quvchi ID oralig'i — maktab admini belgilaydi (masalan 1001–1038). O'quvchilar shu
    # oraliqdan ID oladi; oraliq belgilanmagan sinf o'quvchilarida ID bo'lmaydi.
    code_start: Mapped[int | None] = mapped_column(default=None)
    code_end: Mapped[int | None] = mapped_column(default=None)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # kim yaratgan
    curator_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    # Fan guruhida — yordamchi o'qituvchi (asosiy o'qituvchi — curator_user_id); huquqlari asosiynikidek
    assistant_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    name: Mapped[str] = mapped_column(String(100))
    # Fan guruhi (masalan ingliz tili darajalari): fan nomi. Bo'sh — oddiy sinf. Guruh sinfdagidek ishlaydi
    # (davomat, test natijalari, jadval), lekin o'quvchilari maktab ro'yxatidan olinadi va o'z sinfida qoladi
    subject: Mapped[str | None] = mapped_column(String(100), default=None)
    # Qo'ng'iroq setkasi (smena) — darslar shu setka kataklariga qo'yiladi
    shift_id: Mapped[int | None] = mapped_column(default=None)
    # Guruhning o'z oylik narxi (kurs va to'garaklarda har guruh har xil); bo'sh — muassasaning umumiy summasi
    monthly_fee: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    students: Mapped[list["ClassStudent"]] = relationship(back_populates="school_class", order_by="ClassStudent.id")


class ClassStudent(Base):
    """Sinfdagi o'quvchi. Chiqarilganda o'chirilmaydi — removed_at belgilanadi (davomat tarixi saqlanadi)."""

    __tablename__ = "class_students"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    full_name: Mapped[str] = mapped_column(String(200))
    birth_date: Mapped[date | None] = mapped_column(default=None)
    # O'quvchi ID — 4 xonali, sinfning ID oralig'idan (admin belgilaydi), maktab ichida
    # takrorlanmaydi. Titulga shu raqam bo'yaladi.
    student_code: Mapped[int | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # Kim qo'shgan (o'quvchini maktabning istalgan o'qituvchisi bittalab qo'sha oladi). Eski yozuvlarda bo'sh
    added_by_user_id: Mapped[int | None] = mapped_column(default=None)
    removed_at: Mapped[datetime | None] = mapped_column(default=None)
    # O'qish to'lovidan chegirma (foiz): 30 — oylik summaning 70% i to'lanadi
    discount_percent: Mapped[int] = mapped_column(default=0)
    # Fan guruhidagi o'quvchi — asl (sinfdagi) o'quvchiga havola: ismi, ID'si va holati o'shandan olinadi
    source_student_id: Mapped[int | None] = mapped_column(default=None, index=True)
    # Sinfdan chiqarilganda: sababi (majburiy) va kim chiqargani. Shunday o'quvchi "Biriktirilmaganlar"
    # ro'yxatiga tushadi; u yerdan qayta biriktirilsa yoki o'chirilsa — basket_closed_at belgilanadi
    removed_reason: Mapped[str | None] = mapped_column(Text, default=None)
    removed_by_user_id: Mapped[int | None] = mapped_column(default=None)
    basket_closed_at: Mapped[datetime | None] = mapped_column(default=None)
    # Boshqa sinfga biriktirilganda — o'sha sinfdagi yangi yozuvi
    reassigned_to_id: Mapped[int | None] = mapped_column(default=None)
    # O'quvchi ma'lumotlari: yashash manzili va telefon raqami (rahbariyat yoki kurator kiritadi)
    address: Mapped[str | None] = mapped_column(String(300), default=None)
    phone: Mapped[str | None] = mapped_column(String(20), default=None)

    school_class: Mapped["SchoolClass"] = relationship(back_populates="students")


class Attendance(Base):
    """
    Bitta dars davomati. taken_at — birinchi olingan vaqt (hech qachon
    o'zgarmaydi), updated_at — oxirgi tahrir vaqti (tahrirlanmagan bo'lsa bo'sh).
    """

    __tablename__ = "attendances"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    subject: Mapped[str] = mapped_column(String(100), default="")
    # Dars vaqti — o'qituvchi kiritadi: "11:00" dan "11:45" gacha (Toshkent vaqti)
    lesson_start: Mapped[str | None] = mapped_column(String(5), default=None)
    lesson_end: Mapped[str | None] = mapped_column(String(5), default=None)
    taken_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    # O'chirilgan davomat bazada qoladi (tiklash mumkin) — faqat ro'yxatlardan yashiriladi
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)

    marks: Mapped[list["AttendanceMark"]] = relationship(back_populates="attendance", order_by="AttendanceMark.id")


class AttendanceMark(Base):
    """
    O'quvchining shu darsdagi holati: present (bor), absent (yo'q),
    excused (sababli), late (kechikdi). Ism nusxasi ham saqlanadi — o'quvchi
    keyin o'zgartirilsa yoki chiqarilsa ham eski davomat to'g'ri ko'rinadi.
    """

    __tablename__ = "attendance_marks"

    id: Mapped[int] = mapped_column(primary_key=True)
    attendance_id: Mapped[int] = mapped_column(ForeignKey("attendances.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    student_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="present")

    attendance: Mapped["Attendance"] = relationship(back_populates="marks")


class ClassTest(Base):
    """
    Sinfda o'tkazilgan test natijalari — o'qituvchi kiritadi (o'quvchilar botda
    ishlamaydi). Ko'radi: kiritgan o'qituvchi va rahbariyat. O'chirilganda
    bazada qoladi (deleted_at).
    """

    __tablename__ = "class_tests"

    id: Mapped[int] = mapped_column(primary_key=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    subject: Mapped[str] = mapped_column(String(100), default="")
    taken_on: Mapped[date] = mapped_column()
    max_score: Mapped[float] = mapped_column()
    # Skaner orqali tekshirilgan offline imtihondan avtomatik yaratilgan bo'lsa — o'sha test
    source_test_id: Mapped[int | None] = mapped_column(default=None, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

    scores: Mapped[list["ClassTestScore"]] = relationship(back_populates="test", order_by="ClassTestScore.id")


class ClassTestScore(Base):
    """O'quvchining test bali. score bo'sh — testda qatnashmagan. Ism nusxasi saqlanadi."""

    __tablename__ = "class_test_scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("class_tests.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    student_name: Mapped[str] = mapped_column(String(200))
    score: Mapped[float | None] = mapped_column(default=None)

    test: Mapped["ClassTest"] = relationship(back_populates="scores")


class PromoCode(Base):
    """
    Promo kod — o'qituvchi kiritsa, token oladi. Kodning o'zi saqlanmaydi,
    faqat SHA-256 izi (code_hash): baza yoki kod ochilib qolsa ham promo kod
    bilinmaydi. hint — adminga ko'rsatish uchun ("51…09").
    """

    __tablename__ = "promo_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    hint: Mapped[str] = mapped_column(String(20))
    credits: Mapped[int] = mapped_column()
    max_uses: Mapped[int | None] = mapped_column(default=None)  # jami (hamma uchun); bo'sh — cheksiz
    # Bitta o'qituvchi shu kodni necha marta ishlata oladi
    per_teacher_limit: Mapped[int] = mapped_column(default=3)
    expires_on: Mapped[date | None] = mapped_column(default=None)
    active: Mapped[bool] = mapped_column(default=True)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AppLogin(Base):
    """
    Android ilovaga (APK) bot orqali kirish: ilova bir martalik token oladi,
    foydalanuvchi botda "Tasdiqlash"ni bosadi, ilova esa shundan keyin seans oladi.
    """

    __tablename__ = "app_logins"

    id: Mapped[int] = mapped_column(primary_key=True)
    token: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    confirmed_at: Mapped[datetime | None] = mapped_column(default=None)
    consumed_at: Mapped[datetime | None] = mapped_column(default=None)
    # Botda "Tasdiqlash" bosilganda beriladigan 6 xonali kod — foydalanuvchi
    # shuni saytga/ilovaga qo'lda kiritadi (qarang: confirm_app_login)
    code: Mapped[str] = mapped_column(String(6), default="")
    attempts: Mapped[int] = mapped_column(default=0)


class AppSession(Base):
    """
    Android ilova seansi. Tokenning o'zi saqlanmaydi — faqat SHA-256 izi:
    baza ochilib qolsa ham seansni o'g'irlab bo'lmaydi. Chiqilganda — revoked_at.
    """

    __tablename__ = "app_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(default=None)
    # "Faol qurilmalar" uchun: oxirgi faollik (90 kun ishlatilmasa — seans o'zi tugaydi) va qurilma (User-Agent)
    last_seen_at: Mapped[datetime | None] = mapped_column(default=None)
    user_agent: Mapped[str] = mapped_column(String(300), default="")


# Shuncha vaqt ishlatilmagan ilova/sayt seansi o'zi tugaydi — qurilmadan avtomatik chiqiladi
APP_SESSION_IDLE_LIMIT = timedelta(days=90)


class Referral(Base):
    """
    Referal: o'qituvchi o'z havolasi (t.me/bot?start=ref_ID) bilan yangi odam olib keldi.
    Taklif qilingan odam @sukhrobmath kanaliga obuna bo'lib, o'qituvchi sifatida
    ro'yxatdan o'tganda havola egasiga REFERRAL_CREDITS token beriladi (bir marta).
    HECH QACHON o'chirilmaydi — kim kimni olib kelgani va berilgan token tarixi.
    """

    __tablename__ = "referrals"

    id: Mapped[int] = mapped_column(primary_key=True)
    referrer_teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    # Bitta odamni faqat bitta o'qituvchi "olib kelgan" bo'ladi
    referred_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    rewarded_at: Mapped[datetime | None] = mapped_column(default=None)
    credits: Mapped[int] = mapped_column(default=0)


class PromoRedemption(Base):
    """Kim qaysi kodni qachon ishlatgani (bitta o'qituvchi — kodning per_teacher_limit marta)."""

    __tablename__ = "promo_redemptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    promo_id: Mapped[int] = mapped_column(ForeignKey("promo_codes.id"), index=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    credits: Mapped[int] = mapped_column()
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FeeChange(Base):
    """
    Oylik narx tarixi: qaysi oydan qancha. Har oyning qarzi O'SHA oydagi narx
    bilan hisoblanadi — narx oshsa, avval to'liq to'langan oylar qarzga aylanmaydi.

    class_id bo'sh — muassasaning umumiy summasi; to'ldirilgan — guruhning o'z
    narxi (monthly_fee bo'sh bo'lsa — o'sha oydan umumiy summaga qaytgan).
    """

    __tablename__ = "fee_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("school_classes.id"), index=True, default=None)
    monthly_fee: Mapped[int | None] = mapped_column(default=None)
    from_month: Mapped[str] = mapped_column(String(7))  # "2026-10"; "0000-00" — tarix boshlanishidan oldingi narx
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class TuitionPayment(Base):
    """
    O'qish to'lovi (pul qabul qilindi). HECH QACHON o'chirilmaydi: xato
    kiritilgan bo'lsa — sababi bilan bekor qilinadi (cancelled_at).
    """

    __tablename__ = "tuition_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    student_name: Mapped[str] = mapped_column(String(200))
    month: Mapped[str] = mapped_column(String(7), index=True)  # "2026-10"
    amount: Mapped[int] = mapped_column()
    method: Mapped[str] = mapped_column(String(10), default="cash")  # cash | card | transfer
    paid_on: Mapped[date] = mapped_column()
    note: Mapped[str] = mapped_column(String(300), default="")
    receipt_no: Mapped[int] = mapped_column()  # maktab ichida tartib raqami
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    cancelled_at: Mapped[datetime | None] = mapped_column(default=None)
    cancelled_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"), default=None)
    cancel_reason: Mapped[str] = mapped_column(String(300), default="")


class LessonSlot(Base):
    """
    Dars jadvalidagi bitta dars: qaysi sinf, haftaning qaysi kuni, soat nechadan
    nechagacha, qaysi fan va (ixtiyoriy) qaysi o'qituvchi. Jadvalni rahbariyat
    yoki sinf kuratori tuzadi. O'chirilganda bazada qoladi (deleted_at).
    """

    __tablename__ = "lesson_slots"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    weekday: Mapped[int] = mapped_column()  # 0 — dushanba ... 6 — yakshanba
    start_time: Mapped[str] = mapped_column(String(5))  # "08:30"
    end_time: Mapped[str] = mapped_column(String(5))
    subject: Mapped[str] = mapped_column(String(100), default="")
    teacher_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    # Setkadagi dars raqami (1-dars, 2-dars...); bo'sh — setkasiz, qo'lda kiritilgan vaqt
    period: Mapped[int | None] = mapped_column(default=None)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class ScheduleShift(Base):
    """
    Qo'ng'iroq setkasi (smena): ish kunlari va darslar vaqti (1-dars 08:00–08:45, ...).
    Tanaffus — ketma-ket darslar orasidagi bo'sh vaqt. Sinf o'z smenasini tanlaydi.
    """

    __tablename__ = "schedule_shifts"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    weekdays: Mapped[str] = mapped_column(Text, default="[0, 1, 2, 3, 4, 5]")  # JSON: 0 — dushanba
    periods: Mapped[str] = mapped_column(Text, default="[]")  # JSON: [{"start": "08:00", "end": "08:45"}, ...]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class ClassSubjectPlan(Base):
    """O'quv reja: sinfda fan haftasiga necha soat o'tiladi va uni kim o'tadi."""

    __tablename__ = "class_subject_plans"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    class_id: Mapped[int] = mapped_column(ForeignKey("school_classes.id"), index=True)
    subject: Mapped[str] = mapped_column(String(100))
    hours: Mapped[int] = mapped_column(default=1)
    teacher_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class DormBuilding(Base):
    """Yotoqxona binosi (A bino, B bino, "Qizlar binosi" ...) — nomini rahbariyat xohlagancha qo'yadi."""

    __tablename__ = "dorm_buildings"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    name: Mapped[str] = mapped_column(String(60))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class DormRoom(Base):
    """
    Yotoqxona xonasi (maktab yoki o'quv markazi). Rahbariyat (admin, direktor)
    xonalarni ochadi va o'quvchilarni joylashtiradi; xonaga tarbiyachi (mas'ul
    xodim) tayinlanadi — u o'z xonalarida kechki yo'qlama oladi.
    O'chirilganda bazada qoladi (deleted_at).
    """

    __tablename__ = "dorm_rooms"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    name: Mapped[str] = mapped_column(String(40))  # "201", "3-xona", "Qizlar 12"
    # Qaysi binoda (bo'sh — binoga biriktirilmagan, binolar qo'shilishidan oldin ochilgan xona)
    building_id: Mapped[int | None] = mapped_column(ForeignKey("dorm_buildings.id"), index=True, default=None)
    floor: Mapped[int | None] = mapped_column(default=None)
    capacity: Mapped[int] = mapped_column(default=4)
    warden_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class DormResident(Base):
    """O'quvchining xonadagi joyi. Chiqarilganda o'chirilmaydi — left_at (yo'qlama tarixi saqlanadi)."""

    __tablename__ = "dorm_residents"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("dorm_rooms.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    placed_at: Mapped[datetime] = mapped_column(default=utcnow)
    left_at: Mapped[datetime | None] = mapped_column(default=None)


class DormCheck(Base):
    """Xonaning bir kunlik kechki yo'qlamasi (bir xona — bir kun — bitta yozuv, qayta saqlansa yangilanadi)."""

    __tablename__ = "dorm_checks"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    room_id: Mapped[int] = mapped_column(ForeignKey("dorm_rooms.id"), index=True)
    check_date: Mapped[date] = mapped_column(index=True)
    taken_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    taken_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_at: Mapped[datetime | None] = mapped_column(default=None)

    marks: Mapped[list["DormCheckMark"]] = relationship(order_by="DormCheckMark.id", cascade="all, delete-orphan")


class DormCheckMark(Base):
    """Yo'qlamadagi bitta o'quvchi: present (bor) | absent (yo'q) | sick (kasal) | excused (ruxsat bilan ketgan)."""

    __tablename__ = "dorm_check_marks"

    id: Mapped[int] = mapped_column(primary_key=True)
    check_id: Mapped[int] = mapped_column(ForeignKey("dorm_checks.id"), index=True)
    student_id: Mapped[int] = mapped_column(index=True)
    student_name: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(10), default="present")


class StudentCertificate(Base):
    """
    O'quvchining sertifikati — Milliy sertifikat (ball) yoki chet tili (CEFR: C1, B2, B1). DTM testlarida
    shu fan bloki ishlanmaydi: o'rniga sertifikat bo'yicha ball qo'yiladi (majburiy fanda — 10 dan 10,
    asosiy fanda — foiz ko'rsatkichi bo'yicha). Sinfdagi (asl) o'quvchiga bog'lanadi; muddati o'tgach qo'llanmaydi.

    ESKIRGAN: o'quvchi profilidagi sertifikat bo'limi olib tashlandi — endi hech qayerda o'qilmaydi va yozilmaydi
    (o'quvchi ishlashni xohlagan testda ham o'zi qo'llanib qolardi). Sertifikat faqat shu test uchun: titulda
    yoki o'qituvchi natijaga kiritadi. Jadval eski zaxira nusxalar tiklanishi uchun qoldirilgan.
    """

    __tablename__ = "student_certificates"

    id: Mapped[int] = mapped_column(primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    subject: Mapped[str] = mapped_column(String(100))
    kind: Mapped[str] = mapped_column(String(10))  # "ms" — Milliy sertifikat, "lang" — chet tili
    value: Mapped[str] = mapped_column(String(20))  # ball ("57.90") yoki daraja ("B2")
    valid_until: Mapped[date | None] = mapped_column(default=None)
    added_by_user_id: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class StudentStatus(Base):
    """
    O'quvchining hozirgi holati: maktabda, uyda, ruxsat berilgan (javob), kasal.
    Har o'zgarish yangi qator — tarix saqlanadi; hozirgisi — eng oxirgi qator.
    until — qachongacha (shu kun ham kiradi); o'tib ketsa, o'quvchi yana "maktabda" hisoblanadi.
    """

    __tablename__ = "student_statuses"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    student_id: Mapped[int] = mapped_column(ForeignKey("class_students.id"), index=True)
    status: Mapped[str] = mapped_column(String(12), default="at_school")
    note: Mapped[str] = mapped_column(String(300), default="")
    until: Mapped[date | None] = mapped_column(default=None)
    set_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class SchoolAnnouncement(Base):
    """
    Maktab yoki sinf e'loni. Xodimlarga bot orqali yuborilishi mumkin; ota-onalarga
    esa matn sifatida sinf guruhiga ulashiladi (ota-onalarning Telegram'i tizimda yo'q).
    O'chirilganda bazada qoladi (deleted_at).
    """

    __tablename__ = "school_announcements"

    id: Mapped[int] = mapped_column(primary_key=True)
    school_id: Mapped[int] = mapped_column(ForeignKey("private_schools.id"), index=True)
    class_id: Mapped[int | None] = mapped_column(ForeignKey("school_classes.id"), index=True, default=None)  # bo'sh — butun maktab
    author_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    staff_notified: Mapped[int] = mapped_column(default=0)  # bot orqali nechta xodimga yuborildi
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)


class AppSetting(Base):
    """
    Admin panelidan kiritiladigan sozlamalar (kalit-qiymat): Payme va Click kalitlari.

    Nega baza? Kalitlar .env'da bo'lsa, ularni o'zgartirish uchun serverga kirib
    qayta deploy qilish kerak. Bu yerda esa admin ularni ilovaning o'zidan kiritadi.
    Bazadagi qiymat .env'dagisidan ustun turadi; bo'sh bo'lsa — .env ishlatiladi.

    MUHIM: maxfiy qiymatlar zaxira nusxaga TUSHMAYDI (qarang: export_database) —
    aks holda ular JSON fayl bo'lib Telegram chatiga chiqib ketardi.
    """

    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(50), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(default=utcnow)
    updated_by: Mapped[int | None] = mapped_column(default=None)


# ========== 4) API so'rov sxemalari (Pydantic) ==========


class LessonSlotIn(BaseModel):
    """Jadvaldagi dars: kun (0 — dushanba), vaqt oralig'i, fan va o'qituvchi."""

    weekday: int = Field(ge=0, le=6)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    subject: str = Field(default="", max_length=100)
    teacher_user_id: int | None = None
    # Setkadagi dars raqami — berilsa, vaqt sinf smenasidan olinadi
    period: int | None = Field(default=None, ge=1, le=12)

    @model_validator(mode="after")
    def check_times(self) -> "LessonSlotIn":
        self.subject = " ".join(self.subject.split())
        if self.end_time <= self.start_time:
            raise ValueError("Dars tugash vaqti boshlanishidan keyin bo'lishi kerak")
        return self


WEEKDAY_NAMES = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


def _clean_weekdays(days: list[int]) -> list[int]:
    if any(day < 0 or day > 6 for day in days):
        raise ValueError("Hafta kuni noto'g'ri")
    return sorted(set(days))


class PeriodIn(BaseModel):
    """Setkadagi bitta dars vaqti."""

    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class ShiftIn(BaseModel):
    """Smena: nomi, ish kunlari va darslar vaqti (ketma-ket, ustma-ust tushmaydi)."""

    name: str = Field(min_length=1, max_length=60)
    weekdays: list[int] = Field(min_length=1, max_length=7)
    periods: list[PeriodIn] = Field(min_length=1, max_length=12)

    @model_validator(mode="after")
    def check(self) -> "ShiftIn":
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Smena nomini yozing")
        self.weekdays = _clean_weekdays(self.weekdays)
        previous_end = ""
        for number, period in enumerate(self.periods, start=1):
            if period.end <= period.start:
                raise ValueError(f"{number}-dars tugash vaqti boshlanishidan keyin bo'lishi kerak")
            if previous_end and period.start < previous_end:
                raise ValueError(f"{number}-dars oldingi dars tugashidan oldin boshlanyapti")
            previous_end = period.end
        return self


class ClassShiftIn(BaseModel):
    """Sinf smenasi (bo'sh — setkasiz)."""

    shift_id: int | None = None


class SubjectPlanItem(BaseModel):
    subject: str = Field(min_length=1, max_length=100)
    hours: int = Field(ge=1, le=20)
    teacher_user_id: int | None = None


class ClassPlanIn(BaseModel):
    """Sinfning butun o'quv rejasi (yuborilgan ro'yxat eskisining o'rniga yoziladi)."""

    items: list[SubjectPlanItem] = Field(max_length=40)

    @model_validator(mode="after")
    def check(self) -> "ClassPlanIn":
        seen = set()
        for item in self.items:
            item.subject = " ".join(item.subject.split())
            key = item.subject.lower()
            if not key:
                raise ValueError("Fan nomini yozing")
            if key in seen:
                raise ValueError(f"«{item.subject}» rejada ikki marta yozilgan")
            seen.add(key)
        return self


class DayHoursIn(BaseModel):
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")


class WorkloadIn(BaseModel):
    """
    O'qituvchi yuklamasi: haftalik soat me'yori, ish kunlari va ish vaqti (bo'sh — cheklanmagan).
    day_hours — har kunning o'z vaqti ({0: {"start": "08:00", "end": "12:00"}, ...}); kunda bo'lmasa —
    umumiy work_start/work_end, u ham bo'lmasa — kun bo'yi.
    """

    weekly_hours: int | None = Field(default=None, ge=0, le=60)
    work_days: list[int] = Field(default_factory=list, max_length=7)
    work_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    work_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    day_hours: dict[int, DayHoursIn] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check(self) -> "WorkloadIn":
        self.work_days = _clean_weekdays(self.work_days)
        if bool(self.work_start) != bool(self.work_end):
            raise ValueError("Ish vaqtining boshlanishi va tugashini birga kiriting")
        if self.work_start and self.work_end <= self.work_start:
            raise ValueError("Ish tugash vaqti boshlanishidan keyin bo'lishi kerak")
        _clean_weekdays(list(self.day_hours))
        for day, hours in self.day_hours.items():
            if hours.end <= hours.start:
                raise ValueError(f"{WEEKDAY_NAMES[day]}: ish tugash vaqti boshlanishidan keyin bo'lishi kerak")
            if self.work_days and day not in self.work_days:
                raise ValueError(f"{WEEKDAY_NAMES[day]} ish kunlari ro'yxatida yo'q")
        return self


class AutoTimetableIn(BaseModel):
    """
    Jadvalni avtomatik tuzish: fill — mavjud darslar qoladi, faqat rejadagi qolgan soatlar qo'yiladi;
    replace — tanlangan sinflar jadvali qaytadan tuziladi. apply=False — faqat ko'rsatadi (bazaga yozmaydi).
    """

    mode: str = Field(default="fill", pattern="^(fill|replace)$")
    class_ids: list[int] | None = Field(default=None, max_length=300)  # bo'sh — smenasi va rejasi bor hamma sinflar
    apply: bool = False


class DormBuildingIn(BaseModel):
    name: str = Field(min_length=1, max_length=60)

    @model_validator(mode="after")
    def _clean(self):
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Bino nomini kiriting")
        return self


class DormRoomBulkIn(BaseModel):
    """
    Bir nechta xonani birdan ochish: raqamlar oralig'i (101–110), hammasiga bir xil
    o'rinlar soni, bino va tarbiyachi. floor bo'sh bo'lsa — raqamdan (101 → 1-qavat).
    """

    start: int = Field(ge=1, le=99999)
    end: int = Field(ge=1, le=99999)
    floor: int | None = Field(default=None, ge=-5, le=100)
    capacity: int = Field(default=4, ge=1, le=50)
    warden_user_id: int | None = None
    building_id: int | None = None

    @model_validator(mode="after")
    def _check_range(self):
        if self.end < self.start:
            raise ValueError("Oxirgi raqam birinchisidan kichik bo'lmasin")
        if self.end - self.start + 1 > 100:
            raise ValueError("Bir martada ko'pi bilan 100 ta xona ochiladi")
        return self


class DormRoomIn(BaseModel):
    """Yotoqxona xonasi: nomi/raqami, qavati, necha o'rinli, tarbiyachisi (bo'sh — tayinlanmagan)."""

    name: str = Field(min_length=1, max_length=40)
    floor: int | None = Field(default=None, ge=-5, le=100)
    capacity: int = Field(default=4, ge=1, le=50)
    warden_user_id: int | None = None
    building_id: int | None = None

    @model_validator(mode="after")
    def _clean(self):
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Xona nomini kiriting")
        return self


class DormPlaceIn(BaseModel):
    student_ids: list[int] = Field(min_length=1, max_length=60)


class DormCheckIn(BaseModel):
    """Kechki yo'qlama: {o'quvchi_id: holat}; sana bo'sh — bugun (Toshkent vaqti)."""

    marks: dict[int, Literal["present", "absent", "sick", "excused"]]
    check_date: date | None = None


class StudentStatusIn(BaseModel):
    """O'quvchi holati: maktabda / uyda / ruxsat berilgan / kasal; izoh va qachongacha (ixtiyoriy)."""

    status: Literal["at_school", "home", "leave", "sick"]
    note: str = Field(default="", max_length=300)
    until: date | None = None

    @model_validator(mode="after")
    def _clean(self):
        self.note = " ".join(self.note.split())
        if self.status == "at_school":
            self.until = None
        return self


class AnnouncementIn(BaseModel):
    """E'lon: sarlavha, matn, kimga (bo'sh class_id — butun maktab), xodimlarga botda yuborilsinmi."""

    title: str = Field(min_length=2, max_length=200)
    body: str = Field(default="", max_length=3000)
    class_id: int | None = None
    notify_staff: bool = False

    @model_validator(mode="after")
    def normalize(self) -> "AnnouncementIn":
        self.title = " ".join(self.title.split())
        self.body = self.body.strip()
        if len(self.title) < 2:
            raise ValueError("E'lon sarlavhasini yozing")
        return self


class TeacherRegisterIn(BaseModel):
    """O'qituvchi sifatida ro'yxatdan o'tishda yuboriladi — parol kerak emas."""

    first_name: str = Field(min_length=2, max_length=100)
    last_name: str = Field(min_length=2, max_length=100)


class NameUpdateIn(BaseModel):
    """Foydalanuvchi (o'quvchi yoki o'qituvchi) o'z ism-familiyasini tahrirlaganda yuboriladi."""

    first_name: str = Field(min_length=2, max_length=100)
    last_name: str = Field(min_length=2, max_length=100)


class QuestionIn(BaseModel):
    """'full' rejimidagi bitta savol: to'liq matn va 4 variant."""

    text: str = Field(min_length=1, max_length=1000)
    a: str = Field(min_length=1, max_length=500)
    b: str = Field(min_length=1, max_length=500)
    c: str = Field(min_length=1, max_length=500)
    d: str = Field(min_length=1, max_length=500)
    correct: str = Field(pattern="^[ABCD]$")


class KeyQuestionIn(BaseModel):
    """'key' rejimidagi bitta savol: faqat raqami, turi, variantlar soni va to'g'ri javob."""

    number: int = Field(ge=1, le=300)
    label: str | None = Field(None, max_length=20)  # masalan "36a"; bo'sh bo'lsa "36-savol"
    question_type: str = Field(pattern="^(single|multi|text)$")
    option_count: int = Field(4, ge=2, le=8)  # "text" turida ishlatilmaydi
    # Bo'sh bo'lishi mumkin: Rash testni javoblar kalitisiz saqlab, kalitni keyin kiritish mumkin
    # (bo'sh kalitli savol hech kimga to'g'ri sanalmaydi; test sahifasida eslatma turadi)
    correct: str = Field("", max_length=300)

    @model_validator(mode="after")
    def normalize_correct(self) -> "KeyQuestionIn":
        """
        Variantli savollarning to'g'ri javobini bitta ko'rinishga keltiradi.

        O'quvchining javobi tekshirishdan oldin katta harfga o'tkaziladi va
        saralanadi, shuning uchun KALIT ham xuddi shunday saqlanishi kerak —
        aks holda "a,c" deb kiritilgan kalitga hech qanday javob mos kelmaydi.
        Yozma ("text") javobga tegmaymiz: u LaTeX va alohida solishtiriladi.
        """

        if self.question_type == "text":
            return self

        letters = sorted({part.strip().upper() for part in self.correct.split(",") if part.strip()})
        if not letters:
            self.correct = ""  # kalit hali kiritilmagan
            return self

        allowed = {chr(65 + index) for index in range(self.option_count)}
        if not set(letters) <= allowed:
            raise ValueError(f"to'g'ri javob faqat A–{chr(64 + self.option_count)} harflaridan bo'lishi kerak")
        if self.question_type == "single" and len(letters) > 1:
            raise ValueError("bitta javobli savolda faqat bitta harf bo'ladi")

        self.correct = ",".join(letters)
        return self


# Pullik test narxi (so'm): 0 — bepul, aks holda shu oraliqda
TEST_PRICE_MIN = 5_000
TEST_PRICE_MAX = 100_000


def _check_test_price(price: int) -> None:
    if price and not TEST_PRICE_MIN <= price <= TEST_PRICE_MAX:
        raise ValueError(f"Narx {TEST_PRICE_MIN:,} — {TEST_PRICE_MAX:,} so'm oralig'ida bo'lishi kerak".replace(",", " "))


class TestIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    subject: str = Field(min_length=2, max_length=100)
    topic: str = Field("", max_length=200)
    price: int = Field(0, ge=0, le=TEST_PRICE_MAX)
    duration: int = Field(30, ge=1, le=300)
    mode: str = Field("full", pattern="^(full|key)$")

    # Telegram guruhga bog'lash (ixtiyoriy)
    telegram_group_id: int | None = None
    required_channel: str | None = Field(None, max_length=100)
    enrollment_type: str = Field("open", pattern="^(open|closed)$")
    start_time: datetime | None = None
    end_time: datetime | None = None

    questions: list[QuestionIn] = Field(default_factory=list, max_length=300)
    key_questions: list[KeyQuestionIn] = Field(default_factory=list, max_length=300)
    # Esse qo'shilsinmi (faqat esseli fanda — Ona tili)
    essay: bool = False
    # Esseni AI baholasinmi (faqat essay=True bo'lsa); AI uchun esse mavzusi majburiy
    essay_ai: bool = False
    # Mavzu — vaziyat matni bilan birga (Milliy sertifikatda bir necha jumla): 300 belgi kamlik qilardi
    essay_topic: str | None = Field(None, max_length=2000)
    # dtm — Oliygohga kirish (DTM) testi: 90 savol, natija DTM bali bilan (fan — "DTM testi")
    test_type: Literal["rash", "boshlangich", "dtm"] = "rash"

    @model_validator(mode="after")
    def check_price(self) -> "TestIn":
        _check_test_price(self.price)
        return self


class AdminSchoolDeleteIn(BaseModel):
    """Muassasani o'chirishni tasdiqlash: nomi aynan yoziladi (tasodifan o'chib ketmasin)."""

    confirm: str = Field(min_length=1, max_length=200)


class DtmBlockCertificateIn(BaseModel):
    """DTM natijasidagi bitta blok: auto — profildagisi, none — sertifikat hisoblanmasin, cert — majburiy fanda,
    ms — Milliy sertifikat (value — ball), lang — chet tili (value — C1 / B2 / B1)."""

    index: int = Field(ge=0, le=20)
    kind: str = Field(pattern="^(auto|none|cert|ms|lang|claimed)$")  # claimed — titulda "Ha" bo'yalgan
    value: str | None = Field(None, max_length=20)


class DtmCertificatesIn(BaseModel):
    blocks: list[DtmBlockCertificateIn] = Field(default_factory=list, max_length=20)


class EssayScoreIn(BaseModel):
    """O'qituvchi qo'ygan esse bali; null — baholanmagan holatga qaytarish."""

    score: int | None = Field(None, ge=0, le=100)


class FinishIn(BaseModel):
    """O'quvchi testni yakunlaganda yuboradigan javoblar: {savol_id: "A"}."""

    answers: dict[str, str] = {}


class TestEditIn(BaseModel):
    """
    O'qituvchi testni yaratgandan keyin tahrirlaydi: nomi va vaqtlari.
    Faqat yuborilgan maydonlar o'zgaradi (start_time/end_time ga null — cheklovni olib tashlash).
    """

    title: str | None = Field(None, min_length=2, max_length=200)
    duration: int | None = Field(None, ge=1, le=300)
    start_time: datetime | None = None
    end_time: datetime | None = None
    # Hisoblash usuli: Rasch yoki oddiy (natijalar darhol qayta hisoblanadi)
    test_type: Literal["rash", "boshlangich"] | None = None


class TeacherCardIn(BaseModel):
    """O'qituvchining karta raqami — pullik test pullari shu kartaga o'tkaziladi."""

    card_number: str = Field(min_length=16, max_length=30)
    card_holder: str = Field(min_length=3, max_length=100)
    card_expiry: str = Field(min_length=4, max_length=7)  # "MM/YY"

    @model_validator(mode="after")
    def normalize(self) -> "TeacherCardIn":
        digits = re.sub(r"[\s-]", "", self.card_number)
        if not re.fullmatch(r"\d{16}", digits):
            raise ValueError("Karta raqami 16 ta raqamdan iborat bo'lishi kerak")
        self.card_number = digits
        self.card_holder = " ".join(self.card_holder.split())

        expiry = re.sub(r"\D", "", self.card_expiry)
        if len(expiry) != 4 or not 1 <= int(expiry[:2]) <= 12:
            raise ValueError("Amal qilish muddatini OO/YY ko'rinishida kiriting (masalan 08/28)")
        month, year = int(expiry[:2]), 2000 + int(expiry[2:])
        now = utcnow()
        if (year, month) < (now.year, now.month):
            raise ValueError("Kartaning amal qilish muddati tugagan")
        self.card_expiry = f"{expiry[:2]}/{expiry[2:]}"
        return self


class TestCardPaymentIn(BaseModel):
    """O'quvchi o'qituvchi kartasiga pul o'tkazgach, chek rasmini yuboradi."""

    receipt_base64: str = Field(min_length=100, max_length=7_000_000)


class PriceUpdate(BaseModel):
    """O'qituvchi testni yaratgandan keyin narxini o'zgartirganda yuboriladi."""

    price: int = Field(ge=0, le=TEST_PRICE_MAX)

    @model_validator(mode="after")
    def check_price(self) -> "PriceUpdate":
        _check_test_price(self.price)
        return self


class WithdrawalRequestIn(BaseModel):
    """O'qituvchi pul yechish so'rovi yuborganda ishlatiladi."""

    amount: int = Field(gt=0, le=50_000_000)
    card_number: str = Field(min_length=8, max_length=50)


# Oylik obuna muddatlari: necha oy -> necha kun. Narxini admin panelda admin belgilaydi.
SUBSCRIPTION_PLAN_DAYS = {1: 30, 3: 90, 12: 365}


class _PurchaseChoice(BaseModel):
    """Xarid: YOKI token (credits ta), YOKI oylik obuna (months oy) — ikkisidan aynan bittasi."""

    credits: int | None = Field(default=None, ge=1, le=100_000)
    months: int | None = None

    @model_validator(mode="after")
    def one_of(self):
        if (self.credits is None) == (self.months is None):
            raise ValueError("Token sonini yoki obuna muddatini tanlang")
        if self.months is not None and self.months not in SUBSCRIPTION_PLAN_DAYS:
            raise ValueError("Bunday obuna muddati yo'q")
        return self


class CreditPurchaseIn(_PurchaseChoice):
    """O'qituvchi token yoki obuna sotib olmoqchi bo'lganda (Payme/Click) yuboriladi."""

    provider: Literal["payme", "click"] = "payme"


class CardTopUpIn(_PurchaseChoice):
    """
    Karta orqali token yoki obuna sotib olish: o'qituvchi admin kartasiga pul
    o'tkazgach, chek rasmini yuboradi.

    Rasm base64 matn ko'rinishida keladi (oddiy JSON so'rovda) — fayl yuklash
    uchun qo'shimcha kutubxona kerak bo'lmasligi uchun. Frontend rasmni
    yuborishdan oldin kichraytiradi, shuning uchun ~7 MB chegara yetarli.
    """

    receipt_base64: str = Field(min_length=100, max_length=7_000_000)


class SubscriptionSettingsIn(BaseModel):
    """Admin panelidan: har bir obuna muddatining narxi (so'm). 0 — bu muddat sotilmaydi."""

    prices: dict[int, int] = Field(default_factory=dict)

    @model_validator(mode="after")
    def valid_plans(self):
        for months, price in self.prices.items():
            if months not in SUBSCRIPTION_PLAN_DAYS:
                raise ValueError("Bunday obuna muddati yo'q")
            if not 0 <= price <= 100_000_000:
                raise ValueError("Narx noto'g'ri")
        return self


class PaymentSettingsIn(BaseModel):
    """
    Admin panelidan kiritiladigan Payme/Click kalitlari.

    Maxfiy kalitlar (secret_key) qaytarib ko'rsatilmaydi, shuning uchun
    bo'sh qoldirilgan maydon "o'zgarishsiz qoldir" degani. Kalitni butunlay
    o'chirish uchun alohida `clear` ro'yxati yuboriladi.
    """

    payme_merchant_id: str | None = Field(default=None, max_length=100)
    payme_secret_key: str | None = Field(default=None, max_length=200)
    payme_test_mode: bool | None = None
    click_service_id: str | None = Field(default=None, max_length=100)
    click_merchant_id: str | None = Field(default=None, max_length=100)
    click_secret_key: str | None = Field(default=None, max_length=200)
    # Payme fiskal cheki: xizmatning MXIK (IKPU) kodi, o'lchov birligi (qadoq) kodi va QQS foizi
    payme_mxik: str | None = Field(default=None, max_length=20)
    payme_package_code: str | None = Field(default=None, max_length=20)
    payme_vat_percent: int | None = Field(default=None, ge=0, le=100)
    clear: list[str] = Field(default_factory=list)


class StudentNamesIn(BaseModel):
    """
    Yopiq testda o'qituvchi o'quvchilarni ro'yxatga qo'shganda yuboriladi.

    Javoblar keyin, har bir o'quvchi uchun alohida kiritiladi (StudentAnswersIn).
    """

    names: list[str] = Field(min_length=1, max_length=300)


class StudentAnswersIn(BaseModel):
    """O'qituvchi ro'yxatdagi o'quvchining javoblarini kiritganda yuboriladi: {savol_ref: "A"}."""

    answers: dict[str, str] = {}
    variant: str | None = Field(default=None, pattern="^[A-D]$")


class ClassCodeRangeIn(BaseModel):
    """Sinf o'quvchilari ID oralig'i: 1001 dan 1038 gacha."""

    start: int = Field(ge=1000, le=9999)
    end: int = Field(ge=1000, le=9999)


class TestClassesIn(BaseModel):
    """Offline imtihonga biriktiriladigan maktab sinflari."""

    class_ids: list[int] = Field(min_length=1, max_length=50)


class VariantKeyIn(BaseModel):
    """Variant kaliti: {savol_raqami: to'g'ri javob}."""

    answers: dict[str, str] = {}


class SheetScanIn(BaseModel):
    """Javoblar varaqasining surati (base64) — skaner javoblarni o'qib beradi."""

    image_base64: str = Field(max_length=12_000_000)
    page: int = Field(default=1, ge=1, le=10)


class SheetResultIn(BaseModel):
    """Ilovada (telefonning o'zida) o'qilgan varaq — /scan natijasi bilan bir xil ko'rinishda."""

    page: int = Field(default=1, ge=1, le=10)
    answers: dict[str, str] = Field(default_factory=dict)
    questions: list[dict] = Field(default_factory=list, max_length=500)
    name_image: str | None = Field(default=None, max_length=2_000_000)
    sheet_number: int | None = Field(default=None, ge=0, le=9999)
    sheet_number_status: str = Field(default="none", max_length=20)
    variant: str | None = Field(default=None, max_length=5)
    sheet_code: int | None = None


class SheetScanSaveIn(BaseModel):
    """
    Skanerlangan (o'qituvchi tekshirib tuzatgan) javoblarni saqlash: ro'yxatdagi
    o'quvchi (attempt_id) yoki yangi ism.
    """

    name: str = Field(default="", max_length=200)
    attempt_id: int | None = None
    sheet_number: int | None = Field(default=None, ge=1, le=9999)
    variant: str | None = Field(default=None, pattern="^[A-D]$")
    answers: dict[str, str] = {}
    # Yozma javob kataklari rasmi: {savol_kaliti: "data:image/jpeg;base64,…"}
    written: dict[str, str] = {}
    # DTM titulidagi sertifikat bo'limi (14-versiya): {bloklar: "yes" | "no" | "both" | "ms:61.11" | "lang:B2" |
    # "unclear" | None}. "yes", ball yoki daraja — sertifikat qo'llanadi (profilda bo'lsa — profildagisi ustun);
    # qolgani — o'quvchining javoblari
    certificates: dict[str, str | None] | None = None


class WrittenVerdictIn(BaseModel):
    """Yozma javobga o'qituvchi bahosi: True — to'g'ri, False — xato, None — bahoni olib tashlash."""

    verdict: bool | None = None


class WrittenRestIn(BaseModel):
    """Shu savolda hali baholanmaganlarning hammasiga bir xil baho (odatda ✗)."""

    verdict: bool = False


def _clean_phone(value: str | None) -> str | None:
    """Telefon raqamni +998XXXXXXXXX ko'rinishiga keltiradi (bo'sh bo'lsa — None)."""

    if not value or not value.strip():
        return None
    digits = re.sub(r"\D", "", value)
    if len(digits) == 9:
        digits = "998" + digits
    if len(digits) != 12 or not digits.startswith("998"):
        raise ValueError("Telefon raqam noto'g'ri. Masalan: +998 90 123 45 67")
    return "+" + digits


def _check_birth_date(value: date | None) -> date | None:
    if value and not (date(1930, 1, 1) <= value <= date.today()):
        raise ValueError("Tug'ilgan sana noto'g'ri")
    return value


def _clean_subjects(subjects: list[str]) -> list[str]:
    """Fanlar ro'yxatini tozalaydi: bo'sh va takrorlanganlari olib tashlanadi."""
    cleaned: list[str] = []
    for subject in subjects:
        value = " ".join(str(subject).split())[:60]
        if value and value.lower() not in {item.lower() for item in cleaned}:
            cleaned.append(value)
    return cleaned


class SchoolCreateIn(BaseModel):
    """Xususiy maktab yaratish: joylashuvi, nomi va yaratuvchining fanlari."""

    region: str = Field(min_length=2, max_length=100)
    district: str = Field(min_length=2, max_length=100)
    name: str = Field(min_length=2, max_length=200)
    subjects: list[str] = Field(default_factory=list, max_length=20)
    phone: str | None = Field(None, max_length=30)
    birth_date: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "SchoolCreateIn":
        self.name = " ".join(self.name.split())
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan sana o'chib ketardi
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


class AccountDeleteIn(BaseModel):
    """Hisobni o'chirish: foydalanuvchi tasdiq so'zini o'zi yozadi (adashib bosilmasin)."""

    confirm: str = Field(default="", max_length=30)


class SchoolCodeIn(BaseModel):
    """Maktabga kirish yoki undan chiqish uchun maktab kodi."""

    code: str = Field(min_length=4, max_length=20)
    subjects: list[str] = Field(default_factory=list, max_length=20)
    # Tuman ro'yxatidan tanlangan maktab (bo'lmasa — maktab kod bo'yicha topiladi)
    school_id: int | None = None
    # Qo'shilayotgan o'qituvchi kuratormi (sinf rahbari) va qaysi sinfniki
    is_curator: bool = False
    curator_class_id: int | None = None
    # Direktor chiqayotganda: direktorlik kimga o'tadi
    successor_user_id: int | None = None
    # Qo'shilayotgan xodimning telefon raqami (qo'shilishda majburiy)
    phone: str | None = Field(None, max_length=30)
    birth_date: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "SchoolCodeIn":
        self.code = self.code.strip().upper().replace(" ", "")
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan sana o'chib ketardi
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


class SchoolProfileIn(BaseModel):
    """Xodimning maktabdagi profili: fanlari va telefon raqami."""

    subjects: list[str] = Field(default_factory=list, max_length=20)
    phone: str | None = Field(None, max_length=30)
    birth_date: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "SchoolProfileIn":
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan sana o'chib ketardi
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


ATTENDANCE_STATUSES = ("present", "absent", "excused", "late")


class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    # Fan guruhi ochilganda — fan nomi (masalan "Ingliz tili"); oddiy sinfda bo'sh
    subject: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def normalize(self) -> "ClassIn":
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Sinf nomini kiriting")
        self.subject = " ".join((self.subject or "").split()) or None
        return self


class StudentRemoveIn(BaseModel):
    """Sinfdan chiqarish sababi (oddiy sinfda majburiy; fan guruhida — ixtiyoriy)."""

    reason: str = Field(default="", max_length=500)

    @model_validator(mode="after")
    def normalize(self) -> "StudentRemoveIn":
        self.reason = " ".join(self.reason.split())
        return self


class UnassignedAssignIn(BaseModel):
    """Biriktirilmagan o'quvchini qaysi sinfga biriktirish."""

    class_id: int


class GroupMembersIn(BaseModel):
    """Fan guruhiga maktab ro'yxatidan qo'shiladigan o'quvchilar (sinfdagi yozuvlari)."""

    student_ids: list[int] = Field(min_length=1, max_length=500)


class StudentIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    birth_date: date | None = None
    # Sinfda shu ism allaqachon bo'lsa ham qo'shilsin (o'qituvchi ogohlantirishni tasdiqlagan)
    force: bool = False

    @model_validator(mode="after")
    def normalize(self) -> "StudentIn":
        self.full_name = " ".join(self.full_name.split())
        if len(self.full_name) < 2:
            raise ValueError("O'quvchining ism-familiyasini kiriting")
        return self


class StudentContactIn(BaseModel):
    """O'quvchining yashash manzili va telefon raqami (bo'sh qoldirilsa — o'chiriladi)."""

    address: str | None = Field(None, max_length=300)
    phone: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> "StudentContactIn":
        self.address = " ".join((self.address or "").split()) or None
        self.phone = _clean_phone(self.phone)
        return self


class ReviewLoginIn(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=100)


class AppLoginVerifyIn(BaseModel):
    """Botdan kelgan 6 xonali kodni kiritib, saytda/ilovada kirishni yakunlaydi."""

    code: str = Field(min_length=1, max_length=6)


class PromoRedeemIn(BaseModel):
    code: str = Field(min_length=3, max_length=40)


class PromoCreateIn(BaseModel):
    """Admin promo kod yaratadi: kod, beriladigan token, ixtiyoriy chegara va muddat."""

    code: str = Field(min_length=4, max_length=40)
    credits: int = Field(default=3000, ge=1, le=1_000_000)
    max_uses: int | None = Field(default=None, ge=1, le=1_000_000)
    per_teacher_limit: int = Field(default=3, ge=1, le=100)
    expires_on: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "PromoCreateIn":
        self.code = re.sub(r"\s", "", self.code).upper()
        if not re.fullmatch(r"[A-Z0-9_-]{4,40}", self.code):
            raise ValueError("Kod faqat harf, raqam, - va _ dan iborat bo'lsin (4-40 belgi)")
        # Kod token miqdori maydoniga yoziladi: faqat raqamli kod miqdor bilan adashmasligi kerak
        if self.code.isdigit() and len(self.code) < 7:
            raise ValueError("Faqat raqamdan iborat kod kamida 7 xonali bo'lsin (yoki harf qo'shing)")
        return self


class SectionsIn(BaseModel):
    """Bo'limlar matni: har qatorda "Nomi: 1-20, 31-35" (bo'sh — bo'limsiz)."""

    text: str = Field(default="", max_length=2000)


class FeeIn(BaseModel):
    monthly_fee: int = Field(ge=0, le=100_000_000)
    # Qaysi oydan amal qiladi ("2026-11"); "0000-00" — avvaldan (barcha oylar); bo'sh — joriy oydan
    from_month: str | None = Field(default=None, pattern=r"^(\d{4}-(0[1-9]|1[0-2])|0000-00)$")


class ClassFeeIn(BaseModel):
    # None — guruh muassasaning umumiy summasiga qaytadi
    monthly_fee: int | None = Field(default=None, ge=0, le=100_000_000)
    from_month: str | None = Field(default=None, pattern=r"^(\d{4}-(0[1-9]|1[0-2])|0000-00)$")


class DiscountIn(BaseModel):
    percent: int = Field(ge=0, le=100)


class PaymentIn(BaseModel):
    """To'lov: qaysi oy uchun, qancha, qanday (naqd/karta/o'tkazma)."""

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    amount: int = Field(gt=0, le=1_000_000_000)
    method: Literal["cash", "card", "transfer"] = "cash"
    paid_on: date = Field(default_factory=date.today)
    note: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def normalize(self) -> "PaymentIn":
        self.note = " ".join(self.note.split())
        if self.paid_on > date.today():
            raise ValueError("To'lov sanasi kelajakda bo'lishi mumkin emas")
        return self


class PaymentCancelIn(BaseModel):
    reason: str = Field(min_length=3, max_length=300)


class ClassTestIn(BaseModel):
    """Sinf testi natijalari: nomi, fani, sanasi, maksimal ball va har o'quvchining bali (bo'sh — qatnashmadi)."""

    title: str = Field(min_length=2, max_length=200)
    subject: str = Field(default="", max_length=100)
    taken_on: date = Field(default_factory=date.today)
    max_score: float = Field(gt=0, le=1000)
    scores: dict[int, float | None] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_scores(self) -> "ClassTestIn":
        self.title = " ".join(self.title.split())
        self.subject = " ".join(self.subject.split())
        if self.taken_on > date.today():
            raise ValueError("Test sanasi kelajakda bo'lishi mumkin emas")
        for score in self.scores.values():
            if score is not None and not (0 <= score <= self.max_score):
                raise ValueError(f"Ball 0 dan {self.max_score:g} gacha bo'lishi kerak")
        return self


class PupilLoginIn(BaseModel):
    """Maktab o'quvchisining kirishi: maktab, login va parol."""

    school_id: int
    login: str = Field(min_length=1, max_length=20)
    password: str = Field(min_length=1, max_length=100)


class PupilPasswordIn(BaseModel):
    """O'quvchi o'z parolini o'rnatadi (birinchi kirishda — majburiy)."""

    old_password: str = Field(max_length=100)
    new_password: str = Field(min_length=6, max_length=100)


class AttendanceIn(BaseModel):
    """Davomat: fan va har bir o'quvchining holati {o'quvchi_id: "present"|"absent"|"excused"|"late"}."""

    subject: str = Field(default="", max_length=100)
    marks: dict[int, str] = Field(default_factory=dict)
    # Dars qachon boshlanib, qachon tugagani — "HH:MM"
    lesson_start: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    lesson_end: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def validate_marks(self) -> "AttendanceIn":
        self.subject = " ".join(self.subject.split())
        if self.lesson_start and self.lesson_end and self.lesson_end <= self.lesson_start:
            raise ValueError("Dars tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak")
        for status in self.marks.values():
            if status not in ATTENDANCE_STATUSES:
                raise ValueError(f"Noto'g'ri holat: {status}")
        return self


class StudentImportIn(BaseModel):
    """O'quvchilar ro'yxatini o'qish: Excel/CSV fayl (base64) yoki joylangan matn."""

    file_base64: str | None = Field(default=None, max_length=4_000_000)  # ~3 MB fayl
    file_name: str | None = Field(default=None, max_length=200)
    text: str | None = Field(default=None, max_length=200_000)


class StudentsBulkIn(BaseModel):
    """Ko'rib chiqilgan ro'yxatni bir yo'la qo'shish."""

    names: list[str] = Field(min_length=1, max_length=500)


class PositionIn(BaseModel):
    """Xodim lavozimi: direktor, zam direktor, moliyachi, komendant yoki oddiy o'qituvchi (bo'sh)."""

    position: Literal["director", "deputy", "accountant", "commandant"] | None = None


class CuratorIn(BaseModel):
    """Sinfga kurator tayinlash (bo'sh — kuratorni olib tashlash)."""

    user_id: int | None = None


class InviteAcceptIn(BaseModel):
    """Shaxsiy havola orqali qo'shilish: kuratormi (qaysi sinf) yoki fanlari."""

    subjects: list[str] = Field(default_factory=list, max_length=20)
    is_curator: bool = False
    curator_class_id: int | None = None
    phone: str | None = Field(None, max_length=30)
    birth_date: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "InviteAcceptIn":
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan sana o'chib ketardi
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self
