"""
Ma'lumotlar bazasi fayli: .env sozlamalari, bazaga ulanish, barcha jadvallar
(modellar) va API so'rov sxemalari (Pydantic) shu bitta faylda.
"""

import os
import re
from typing import Literal
from datetime import date, datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import BigInteger, ForeignKey, String, Text, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

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

# Shu Telegram ID'ga ega foydalanuvchi "ADMIN" bo'limini ko'radi va boshqara oladi.
ADMIN_ID = int(os.getenv("ADMIN_ID") or 0)

# Pullik testda o'quvchi to'laganda, platforma ushlab qoladigan foiz (qolgani o'qituvchiga boradi).
PLATFORM_COMMISSION_PERCENT = int(os.getenv("PLATFORM_COMMISSION_PERCENT") or 30)

# Yangi o'qituvchi ro'yxatdan o'tganda beriladigan sovg'a kreditlar.
# Har bir bepul test natijasi uchun 1 kredit sarflanadi.
WELCOME_CREDITS = int(os.getenv("WELCOME_CREDITS") or 75)

# O'qituvchilarga pul balansini to'ldirish uchun ko'rsatiladigan karta raqami.
ADMIN_CARD_NUMBER = os.getenv("ADMIN_CARD_NUMBER", "")

# Payme Merchant API ma'lumotlari (kredit sotib olish uchun). Payme Business
# kabinetidan olinadi: https://business.payme.uz
PAYME_MERCHANT_ID = os.getenv("PAYME_MERCHANT_ID", "")
PAYME_SECRET_KEY = os.getenv("PAYME_SECRET_KEY", "")


# ========== 2) Bazaga ulanish ==========

IS_SQLITE = DATABASE_URL.startswith("sqlite")

# SQLite bitta threadda ishlashni talab qiladi, FastAPI esa turli threadlardan
# murojaat qilishi mumkin — shuning uchun buni o'chiramiz.
connect_args = {"check_same_thread": False} if IS_SQLITE else {}

# pool_pre_ping: PostgreSQL ulanishi uzilib qolgan bo'lsa (masalan, xizmat
# uyquga ketib qaytgandan keyin), SQLAlchemy uni avtomatik qayta ulaydi.
engine = create_engine(DATABASE_URL, connect_args=connect_args, pool_pre_ping=not IS_SQLITE)
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

    # Kredit balansi: bepul testda har bir o'quvchi natijasi uchun 1 dona sarflanadi.
    credits: Mapped[int] = mapped_column(default=0)

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

    # "active" — test jarayonda (o'quvchi qo'shish/javob kiritish mumkin)
    # "closed" — o'qituvchi yakunlagan, natijalar olingan
    status: Mapped[str] = mapped_column(String(10), default="active")

    # Natijalar o'qituvchiga yuborilgan vaqt (bo'sh bo'lsa — hali yuborilmagan).
    # Test vaqti tugagach, hisobot o'zi bir marta yuboriladi.
    results_sent_at: Mapped[datetime | None] = mapped_column(default=None)

    # Majburiy obuna: shu kanalga obuna bo'lmagan o'quvchi testni boshlay olmaydi.
    # "@kanal_nomi" ko'rinishida saqlanadi; bo'sh bo'lsa, tekshiruv yo'q.
    required_channel: Mapped[str | None] = mapped_column(String(100), default=None)

    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    # O'qituvchi "o'chirgan" test. Bazadan butunlay o'chirilmaydi: unga bog'liq
    # to'lovlar (pul hisobi) va o'quvchilar natijalari saqlanib qoladi, test esa
    # hamma joydan yashiriladi. Kerak bo'lsa admin uni qayta tiklay oladi.
    deleted_at: Mapped[datetime | None] = mapped_column(default=None)

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
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | cancelled
    provider: Mapped[str] = mapped_column(String(20), default="payme")
    payme_transaction_id: Mapped[str | None] = mapped_column(String(50), default=None, unique=True, index=True)
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
    O'qituvchining kredit sotib olish buyurtmasi — Payme orqali to'lanadi.

    Payme'ning Merchant API'si (webhook) shu yozuvni "pending" -> "paid"
    holatiga o'tkazadi va shunda teacher.credits oshiriladi.
    """

    __tablename__ = "credit_purchases"

    id: Mapped[int] = mapped_column(primary_key=True)
    teacher_id: Mapped[int] = mapped_column(ForeignKey("teachers.id"), index=True)
    credits: Mapped[int]
    amount: Mapped[int]  # so'm
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending | paid | cancelled
    payme_transaction_id: Mapped[str | None] = mapped_column(String(50), default=None, unique=True, index=True)
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
    # Pasport seriya va raqami (AA1234567) — ixtiyoriy, faqat rahbariyat ko'radi
    passport: Mapped[str | None] = mapped_column(String(20), default=None)
    subjects: Mapped[str] = mapped_column(Text, default="[]")  # JSON ro'yxat: ["Matematika", ...]
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
    teacher_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)  # kim yaratgan
    curator_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), index=True, default=None)
    name: Mapped[str] = mapped_column(String(100))
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
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    removed_at: Mapped[datetime | None] = mapped_column(default=None)

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


# ========== 4) API so'rov sxemalari (Pydantic) ==========


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
    correct: str = Field(min_length=1, max_length=300)

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
            raise ValueError("to'g'ri javob bo'sh")

        allowed = {chr(65 + index) for index in range(self.option_count)}
        if not set(letters) <= allowed:
            raise ValueError(f"to'g'ri javob faqat A–{chr(64 + self.option_count)} harflaridan bo'lishi kerak")
        if self.question_type == "single" and len(letters) > 1:
            raise ValueError("bitta javobli savolda faqat bitta harf bo'ladi")

        self.correct = ",".join(letters)
        return self


class TestIn(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    subject: str = Field(min_length=2, max_length=100)
    topic: str = Field("", max_length=200)
    price: int = Field(0, ge=0, le=10_000_000)
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


class FinishIn(BaseModel):
    """O'quvchi testni yakunlaganda yuboradigan javoblar: {savol_id: "A"}."""

    answers: dict[str, str] = {}


class PriceUpdate(BaseModel):
    """O'qituvchi testni yaratgandan keyin narxini o'zgartirganda yuboriladi."""

    price: int = Field(ge=0, le=10_000_000)


class WithdrawalRequestIn(BaseModel):
    """O'qituvchi pul yechish so'rovi yuborganda ishlatiladi."""

    amount: int = Field(gt=0, le=50_000_000)
    card_number: str = Field(min_length=8, max_length=50)


class CreditPurchaseIn(BaseModel):
    """O'qituvchi kredit sotib olmoqchi bo'lganda yuboriladi."""

    credits: int = Field(ge=1, le=100_000)


class CardTopUpIn(BaseModel):
    """
    Karta orqali kredit sotib olish: o'qituvchi admin kartasiga pul o'tkazgach,
    chek rasmini yuboradi.

    Rasm base64 matn ko'rinishida keladi (oddiy JSON so'rovda) — fayl yuklash
    uchun qo'shimcha kutubxona kerak bo'lmasligi uchun. Frontend rasmni
    yuborishdan oldin kichraytiradi, shuning uchun ~7 MB chegara yetarli.
    """

    credits: int = Field(ge=1, le=100_000)
    receipt_base64: str = Field(min_length=100, max_length=7_000_000)


class StudentNamesIn(BaseModel):
    """
    Yopiq testda o'qituvchi o'quvchilarni ro'yxatga qo'shganda yuboriladi.

    Javoblar keyin, har bir o'quvchi uchun alohida kiritiladi (StudentAnswersIn).
    """

    names: list[str] = Field(min_length=1, max_length=300)


class StudentAnswersIn(BaseModel):
    """O'qituvchi ro'yxatdagi o'quvchining javoblarini kiritganda yuboriladi: {savol_ref: "A"}."""

    answers: dict[str, str] = {}


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


def _clean_passport(value: str | None) -> str | None:
    """Pasport/ID-karta seriya va raqami: AA1234567 (bo'sh bo'lsa — None)."""

    if not value or not value.strip():
        return None
    passport = re.sub(r"\s", "", value).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{7}", passport):
        raise ValueError("Pasport seriya va raqami noto'g'ri. Masalan: AA1234567")
    return passport


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
    passport: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> "SchoolCreateIn":
        self.name = " ".join(self.name.split())
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan pasport/sana o'chib ketardi
        if self.passport is not None:
            self.passport = _clean_passport(self.passport)
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


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
    passport: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> "SchoolCodeIn":
        self.code = self.code.strip().upper().replace(" ", "")
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan pasport/sana o'chib ketardi
        if self.passport is not None:
            self.passport = _clean_passport(self.passport)
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


class SchoolProfileIn(BaseModel):
    """Xodimning maktabdagi profili: fanlari va telefon raqami."""

    subjects: list[str] = Field(default_factory=list, max_length=20)
    phone: str | None = Field(None, max_length=30)
    birth_date: date | None = None
    passport: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> "SchoolProfileIn":
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan pasport/sana o'chib ketardi
        if self.passport is not None:
            self.passport = _clean_passport(self.passport)
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self


ATTENDANCE_STATUSES = ("present", "absent", "excused", "late")


class ClassIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def normalize(self) -> "ClassIn":
        self.name = " ".join(self.name.split())
        if not self.name:
            raise ValueError("Sinf nomini kiriting")
        return self


class StudentIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=200)
    birth_date: date | None = None

    @model_validator(mode="after")
    def normalize(self) -> "StudentIn":
        self.full_name = " ".join(self.full_name.split())
        if len(self.full_name) < 2:
            raise ValueError("O'quvchining ism-familiyasini kiriting")
        return self


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
    """O'qituvchiga lavozim: direktor, zam direktor yoki oddiy o'qituvchi (bo'sh)."""

    position: Literal["director", "deputy"] | None = None


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
    passport: str | None = Field(None, max_length=30)

    @model_validator(mode="after")
    def normalize(self) -> "InviteAcceptIn":
        self.subjects = _clean_subjects(self.subjects)
        self.phone = _clean_phone(self.phone)
        # Faqat kelgan maydonga tegamiz — aks holda u "yuborilgan" deb belgilanib,
        # profil saqlanganda yuborilmagan pasport/sana o'chib ketardi
        if self.passport is not None:
            self.passport = _clean_passport(self.passport)
        if self.birth_date is not None:
            self.birth_date = _check_birth_date(self.birth_date)
        return self
