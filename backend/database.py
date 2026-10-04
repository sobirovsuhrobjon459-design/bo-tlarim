"""
Ma'lumotlar bazasi fayli: .env sozlamalari, bazaga ulanish, barcha jadvallar
(modellar) va API so'rov sxemalari (Pydantic) shu bitta faylda.
"""

import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from sqlalchemy import BigInteger, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

# ========== 1) Sozlamalar (.env) ==========

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./backend/app.db")

# 1 bo'lsa, Telegram'siz (oddiy brauzerda) ham sinash mumkin bo'ladi. Productionda 0 bo'lsin.
DEV_MODE = os.getenv("DEV_MODE", "0") == "1"

# Shu Telegram ID'ga ega foydalanuvchi "ADMIN" bo'limini ko'radi va boshqara oladi.
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)

# Bepul test yaratganda o'qituvchi balansidan bir martalik yechiladigan summa (so'm).
FREE_TEST_FEE = int(os.getenv("FREE_TEST_FEE") or 2000)

# Pullik testda o'quvchi to'laganda, platforma ushlab qoladigan foiz (qolgani o'qituvchiga boradi).
PLATFORM_COMMISSION_PERCENT = int(os.getenv("PLATFORM_COMMISSION_PERCENT") or 30)

# O'qituvchilarga balans to'ldirish uchun ko'rsatiladigan karta raqami.
ADMIN_CARD_NUMBER = os.getenv("ADMIN_CARD_NUMBER", "")


# ========== 2) Bazaga ulanish ==========

# SQLite bitta threadda ishlashni talab qiladi, FastAPI esa turli threadlardan
# murojaat qilishi mumkin — shuning uchun buni o'chiramiz.
connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}

engine = create_engine(DATABASE_URL, connect_args=connect_args)
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
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


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

    # Bepul testlar uchun shu yerdan yechiladi, pullik testlardan tushgan ulush shu yerga qo'shiladi.
    balance: Mapped[int] = mapped_column(default=0)

    user: Mapped["User"] = relationship()
    tests: Mapped[list["Test"]] = relationship(back_populates="teacher")


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

    # Telegram guruhga bog'lash (ixtiyoriy)
    telegram_group_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    enrollment_type: Mapped[str] = mapped_column(String(10), default="open")  # "open" | "closed"
    start_time: Mapped[datetime | None] = mapped_column(default=None)
    end_time: Mapped[datetime | None] = mapped_column(default=None)

    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

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
    savol raqami, turi (single/multi) va to'g'ri javob(lar) saqlanadi.
    """

    __tablename__ = "key_questions"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    number: Mapped[int]
    question_type: Mapped[str] = mapped_column(String(10), default="single")  # "single" | "multi"
    option_count: Mapped[int] = mapped_column(default=4)  # variantlar soni (A, B, C... shu sonigacha)
    correct: Mapped[str] = mapped_column(String(50))  # "B" yoki ko'p tanlovda "A,C"

    test: Mapped["Test"] = relationship(back_populates="key_questions")


class Attempt(Base):
    """O'quvchining bitta testni ishlash urinishi (va natijasi)."""

    __tablename__ = "test_attempts"

    id: Mapped[int] = mapped_column(primary_key=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(default=None)
    status: Mapped[str] = mapped_column(String(20), default="in_progress")  # in_progress | done | expired
    correct: Mapped[int] = mapped_column(default=0)
    wrong: Mapped[int] = mapped_column(default=0)
    percent: Mapped[float] = mapped_column(default=0)
    seconds: Mapped[int] = mapped_column(default=0)


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
    chosen: Mapped[str | None] = mapped_column(String(50), default=None)
    is_correct: Mapped[bool] = mapped_column(default=False)


class Payment(Base):
    """To'lov yozuvi. Hozircha provider har doim 'mock'."""

    __tablename__ = "payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    test_id: Mapped[int] = mapped_column(ForeignKey("tests.id"), index=True)
    amount: Mapped[int] = mapped_column(default=0)
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | failed
    provider: Mapped[str] = mapped_column(String(20), default="mock")
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class TopUpRequest(Base):
    """
    O'qituvchi balansni to'ldirish uchun yuborgan so'rov.

    O'qituvchi admin ko'rsatgan kartaga pul tashlaydi, so'ngra shu yerda
    "shuncha pul tashladim" deb so'rov qoldiradi. Admin tasdiqlasa, balans ortadi.
    """

    __tablename__ = "topup_requests"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    amount: Mapped[int]
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | approved | rejected
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


# ========== 4) API so'rov sxemalari (Pydantic) ==========


class TeacherRegisterIn(BaseModel):
    """O'qituvchi sifatida ro'yxatdan o'tishda yuboriladi — parol kerak emas."""

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
    question_type: str = Field(pattern="^(single|multi)$")
    option_count: int = Field(ge=2, le=8)
    correct: str = Field(min_length=1, max_length=50)


class TestIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    subject: str = Field(min_length=2, max_length=100)
    topic: str = Field("", max_length=200)
    price: int = Field(0, ge=0, le=10_000_000)
    duration: int = Field(30, ge=1, le=300)
    mode: str = Field("full", pattern="^(full|key)$")

    # Telegram guruhga bog'lash (ixtiyoriy)
    telegram_group_id: int | None = None
    enrollment_type: str = Field("open", pattern="^(open|closed)$")
    start_time: datetime | None = None
    end_time: datetime | None = None

    questions: list[QuestionIn] = Field(default_factory=list, max_length=300)
    key_questions: list[KeyQuestionIn] = Field(default_factory=list, max_length=300)


class FinishIn(BaseModel):
    """O'quvchi testni yakunlaganda yuboradigan javoblar: {savol_id: "A"}."""

    answers: dict[str, str] = {}


class PriceUpdate(BaseModel):
    """O'qituvchi testni yaratgandan keyin narxini o'zgartirganda yuboriladi."""

    price: int = Field(ge=0, le=10_000_000)


class TopUpRequestIn(BaseModel):
    """O'qituvchi balansni to'ldirish so'rovi yuborganda ishlatiladi."""

    amount: int = Field(gt=0, le=50_000_000)
