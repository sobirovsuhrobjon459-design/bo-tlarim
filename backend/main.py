"""
Backend (FastAPI) — barcha API endpointlari shu bitta faylda.

Bo'limlar:
1) Ilova sozlash (app, baza jadvallarini yaratish, botni ham shu jarayon ichida ishga tushirish)
2) Statistika va PDF yordamchi funksiyalari
3) Umumiy endpointlar (ping, me)
4) O'qituvchi autentifikatsiyasi (register, login)
5) O'qituvchi endpointlari (testlar, statistika, PDF)
6) O'quvchi endpointlari (qidirish, to'lov, test ishlash, natija, reyting)

MUHIM: Telegram bot ham aynan shu jarayon ichida, fon vazifasi (background task)
sifatida ishga tushadi (pastda "lifespan" qismida) — alohida deploy qilish shart
emas. Sabab: Render'ning bepul tarifida alohida "Background Worker" pullik,
lekin "Web Service" bepul — shuning uchun ikkalasini bitta xizmatga birlashtiramiz.
"""

import asyncio
import base64
import binascii
import contextvars
import gzip
import hashlib
import hmac
import io
import json
import logging
import queue
import math
import os
import re
import secrets
import tempfile
import threading
import time
import zipfile
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from html import escape as html_escape
from xml.sax.saxutils import escape as xml_escape
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import MutableHeaders
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.graphics.shapes import String as ChartString
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import CondPageBreak, HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy import String, case, cast, delete, distinct, event, func, inspect, or_, select, text, update
from sqlalchemy.orm import Session, defer, object_session, selectinload

from . import keymath
from .memory import MEMORY_WARN_MB, limit_malloc_arenas, release_memory, rss_mb, storage_report
from .rasch import grade_for, rasch_scores
from .timetable import ClassInput, Period, TeacherInput, build_timetable

# Ko'p oqimda xotira bo'laklanib, 512 MB'dan oshib ketmasin (qarang: backend/memory.py)
limit_malloc_arenas()
from .subjects import band_label, default_sections, essay_max, frontend_config, score_weights, subject_keyboard
from .regions import REGIONS, is_valid_location
from .student_import import names_from_rows, parse_student_file, rows_from_text
from .auth import (
    InitDataError,
    app_session_hash,
    validate_init_data,
    ensure_school_teacher,
    get_current_admin,
    get_current_teacher,
    get_current_user,
    get_db,
)
from .database import (
    ADMIN_CARD_NUMBER,
    ADMIN_ID,
    ADMIN_IDS,
    is_admin_tg,
    BOT_TOKEN,
    PAYME_MERCHANT_ID,
    PAYME_SECRET_KEY,
    PAYME_TEST_MODE,
    CLICK_MERCHANT_ID,
    CLICK_SECRET_KEY,
    CLICK_SERVICE_ID,
    PLATFORM_COMMISSION_PERCENT,
    REFERRAL_CREDITS,
    WELCOME_CREDITS,
    had_deleted_account,
    tg_fingerprint,
    welcome_credits_for,
    APP_SESSION_IDLE_LIMIT,
    AppLogin,
    AppLoginVerifyIn,
    AccountDeleteIn,
    AppSession,
    AppSetting,
    Referral,
    Attempt,
    Attendance,
    AttendanceIn,
    AttendanceMark,
    Base,
    ClassFeeIn,
    ClassIn,
    ClassStudent,
    ClassTest,
    ClassTestIn,
    ClassTestScore,
    DiscountIn,
    DormBuilding,
    DormBuildingIn,
    DormCheck,
    DormCheckIn,
    DormCheckMark,
    DormPlaceIn,
    DormResident,
    DormRoom,
    DormRoomBulkIn,
    DormRoomIn,
    FeeChange,
    FeeIn,
    PaymentCancelIn,
    PaymentIn,
    PaymentSettingsIn,
    PromoCode,
    PromoCreateIn,
    PromoRedeemIn,
    ReviewLoginIn,
    PromoRedemption,
    SectionsIn,
    TuitionPayment,
    CuratorIn,
    CreditPurchase,
    CardTopUpIn,
    CreditPurchaseIn,
    DtmCertificatesIn,
    EssayScoreIn,
    AdminSchoolDeleteIn,
    GroupMembersIn,
    StudentRemoveIn,
    UnassignedAssignIn,
    FinishIn,
    IS_SQLITE,
    InviteAcceptIn,
    PositionIn,
    KeyQuestion,
    NameUpdateIn,
    Payment,
    PriceUpdate,
    PrivateSchool,
    Question,
    SchoolCodeIn,
    SchoolCreateIn,
    LessonSlot,
    LessonSlotIn,
    ScheduleShift,
    ClassSubjectPlan,
    ShiftIn,
    ClassShiftIn,
    ClassPlanIn,
    WorkloadIn,
    AutoTimetableIn,
    SchoolAnnouncement,
    StudentStatus,
    StudentStatusIn,
    AnnouncementIn,
    SchoolMember,
    SchoolClass,
    SchoolInvite,
    SchoolProfileIn,
    StudentImportIn,
    StudentContactIn,
    StudentIn,
    StudentsBulkIn,
    SessionLocal,
    StudentAnswer,
    SheetResultIn,
    SheetScanIn,
    SheetScanSaveIn,
    StudentAnswersIn,
    StudentNamesIn,
    TestClassesIn,
    ClassCodeRangeIn,
    TestVariant,
    VariantKeyIn,
    Teacher,
    TeacherRegisterIn,
    Test,
    TestIn,
    TestEditIn,
    SUBSCRIPTION_PLAN_DAYS,
    SubscriptionSettingsIn,
    TeacherCardIn,
    TestCardPaymentIn,
    User,
    WithdrawalRequest,
    WithdrawalRequestIn,
    SheetImage,
    EssayImage,
    PupilAccount,
    PupilLoginIn,
    PupilPasswordIn,
    PupilSession,
    WrittenAnswer,
    WrittenRestIn,
    WrittenVerdictIn,
    add_balance,
    add_credits,
    engine,
    utcnow,
    withdraw_balance,
)
from bot.bot import SUPPORT_CONTACTS, run_bot
from bot.config import WEBAPP_URL

# ========== 1) Ilova sozlash ==========

Base.metadata.create_all(engine)


# Ustun BIRINCHI marta qo'shilganda bir martalik to'ldirish: ilgari sinfni
# o'qituvchining o'zi yaratib boshqarardi — endi u o'sha sinfning kuratori.
COLUMN_BACKFILLS = {
    ("school_classes", "curator_user_id"): "UPDATE school_classes SET curator_user_id = teacher_user_id",
}


# Olib tashlangan bo'limlarning jadvallari — bazadan butunlay o'chiriladi (ma'lumotlari bilan).
# «Video darslar» bo'limi kerak emas: video_lessons jadvali ham o'chiriladi.
REMOVED_TABLES = ("video_lessons",)


def _drop_removed_tables() -> None:
    with engine.begin() as connection:
        existing = set(inspect(connection).get_table_names())
        for name in REMOVED_TABLES:
            if name in existing:
                connection.execute(text(f"DROP TABLE {name}"))
                logging.getLogger("backend").warning("Olib tashlangan jadval o'chirildi: %s", name)


def _add_missing_columns() -> None:
    """
    Mavjud bazaga keyinchalik qo'shilgan ustunlarni qo'shadi.

    SQLAlchemy'ning create_all() funksiyasi faqat YANGI jadval yarata oladi —
    mavjud jadvalga yangi ustun qo'shmaydi. Shuning uchun yangi ustunlarni shu
    yerda qo'lda tekshiramiz (aks holda "no such column" / "column does not
    exist" xatosi chiqadi). SQLite'da ham, PostgreSQL'da ham bir xil ishlaydi.
    """

    new_columns = {
        "tests": {
            "sections": "TEXT DEFAULT ''",
            "status": "VARCHAR(10) DEFAULT 'active'",
            "required_channel": "VARCHAR(100)",
            "results_sent_at": "TIMESTAMP",
            "broadcast_at": "TIMESTAMP",
            "deleted_at": "TIMESTAMP",
            # Eski testlar — eski varaq ko'rinishi (raqamsiz), chop etilganlari to'g'ri skanerlansin
            "sheet_version": "INTEGER DEFAULT 1",
            "essay_max": "INTEGER",
            "essay_ai": "BOOLEAN DEFAULT FALSE",
            "essay_topic": "TEXT",
            "test_type": "VARCHAR(15) DEFAULT 'rash'",
            "closed_at": "TIMESTAMP",
            "reopened_at": "TIMESTAMP",
            "delete_warned_at": "TIMESTAMP",
            "archive_tried_at": "TIMESTAMP",
            "auto_deleted_at": "TIMESTAMP",
        },
        "test_attempts": {
            "offline_name": "VARCHAR(200)", "saved_answers": "TEXT", "sheet_number": "INTEGER", "variant": "VARCHAR(2)",
            "class_student_id": "INTEGER", "essay_text": "TEXT", "essay_score": "INTEGER", "dtm_certificates": "TEXT",
            "essay_ai_score": "INTEGER", "essay_ai_comment": "TEXT",
        },
        "users": {"username": "VARCHAR(64)", "photo_url": "VARCHAR(500)", "deleted_at": "TIMESTAMP",
                  "deleted_tg_hash": "VARCHAR(64)"},
        "teachers": {"card_number": "VARCHAR(30)", "card_holder": "VARCHAR(100)", "card_expiry": "VARCHAR(5)",
                     "subscription_until": "TIMESTAMP", "commission_percent": "INTEGER"},
        "school_classes": {
            "curator_user_id": "INTEGER", "code_start": "INTEGER", "code_end": "INTEGER", "shift_id": "INTEGER",
            "monthly_fee": "INTEGER", "subject": "VARCHAR(100)", "assistant_user_id": "INTEGER",
        },
        "private_schools": {"monthly_fee": "INTEGER DEFAULT 0", "kind": "VARCHAR(10) DEFAULT 'school'",
                            "deleted_at": "TIMESTAMP", "delete_requested_at": "TIMESTAMP", "verified_at": "TIMESTAMP"},
        "class_students": {"discount_percent": "INTEGER DEFAULT 0", "student_code": "INTEGER", "added_by_user_id": "INTEGER",
                           "source_student_id": "INTEGER", "removed_reason": "TEXT", "removed_by_user_id": "INTEGER",
                           "basket_closed_at": "TIMESTAMP", "reassigned_to_id": "INTEGER",
                           "address": "VARCHAR(300)", "phone": "VARCHAR(20)",
                           "region": "VARCHAR(100)", "district": "VARCHAR(100)", "mahalla": "VARCHAR(150)",
                           "grade": "INTEGER"},
        "class_tests": {"source_test_id": "INTEGER"},
        "promo_codes": {"per_teacher_limit": "INTEGER DEFAULT 3"},
        "school_members": {
            "position": "VARCHAR(20)", "phone": "VARCHAR(20)", "birth_date": "DATE", "passport": "VARCHAR(20)",
            "weekly_hours": "INTEGER", "work_days": "TEXT", "work_start": "VARCHAR(5)", "work_end": "VARCHAR(5)",
            "work_hours": "TEXT",
        },
        "lesson_slots": {"period": "INTEGER"},
        "dorm_rooms": {"building_id": "INTEGER"},
        "attendances": {
            "lesson_start": "VARCHAR(5)",
            "lesson_end": "VARCHAR(5)",
            "deleted_at": "TIMESTAMP",
            "deleted_by": "INTEGER",
        },
        "key_questions": {"label": "VARCHAR(20)"},
        "app_logins": {"code": "VARCHAR(6) DEFAULT ''", "attempts": "INTEGER DEFAULT 0"},
        "app_sessions": {"last_seen_at": "TIMESTAMP", "user_agent": "VARCHAR(300) DEFAULT ''"},
        "payments": {
            "payme_transaction_id": "VARCHAR(50)",
            "payme_created_at": "TIMESTAMP",
            "payme_time": "BIGINT",
            "payme_cancel_reason": "INTEGER",
            "performed_at": "TIMESTAMP",
            "cancelled_at": "TIMESTAMP",
            "click_trans_id": "VARCHAR(50)",
            "receipt_file_id": "VARCHAR(200)",
            "receipt_message_id": "BIGINT",
            "reminded_at": "TIMESTAMP",
            "reminder_count": "INTEGER DEFAULT 0",
            "teacher_share": "INTEGER",
        },
        "credit_purchases": {
            "kind": "VARCHAR(15) DEFAULT 'tokens'",
            "months": "INTEGER DEFAULT 0",
            "method": "VARCHAR(10) DEFAULT 'payme'",
            "receipt_file_id": "VARCHAR(200)",
            "click_trans_id": "VARCHAR(50)",
            "payme_created_at": "TIMESTAMP",
            "payme_time": "BIGINT",
            "payme_cancel_reason": "INTEGER",
            "test_id": "INTEGER",
            "school_id": "INTEGER",
        },
        # Yozma javoblarni AI o'qishi (jadval oldingi yangilanishda AI ustunlarisiz yaratilgan)
        "written_answers": {
            "ai_text": "VARCHAR(300)", "ai_sure": "BOOLEAN", "ai_status": "VARCHAR(10)",
            "auto_verdict": "BOOLEAN DEFAULT FALSE",
        },
    }

    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    logger = logging.getLogger("backend")

    with engine.begin() as connection:
        for table_name, columns in new_columns.items():
            if table_name not in existing_tables:
                continue  # jadval hali yo'q — create_all uni allaqachon to'g'ri yaratadi

            existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
            for column_name, column_type in columns.items():
                if column_name not in existing_columns:
                    connection.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}"))
                    logger.info("Bazaga yangi ustun qo'shildi: %s.%s", table_name, column_name)
                    backfill = COLUMN_BACKFILLS.get((table_name, column_name))
                    if backfill:
                        connection.execute(text(backfill))


_add_missing_columns()
_drop_removed_tables()


def _upgrade_sheet_versions() -> None:
    """
    4-versiya (3 xonali ID) testlar 5-versiyaga (4 xonali ID) o'tkaziladi: savollar joyi
    bir xil, faqat ID blokiga 4-ustun qo'shiladi. Oldin chop etilgan 3 xonali varaqlar
    ham o'qiladi (skaner ularni qayta urinib taniydi — qarang: scan_sheet).
    """
    with engine.begin() as connection:
        if "tests" in inspect(engine).get_table_names():
            connection.execute(text("UPDATE tests SET sheet_version = 5 WHERE sheet_version = 4"))
            # 6–8-versiya — hali birorta natija kiritilmagan bo'lsa, eng yangi (9: kattaroq, bo'yash namunasi
            # bilan) titulga o'tadi. Natijasi bor testga tegilmaydi — uning varaqlari tarqatilgan
            connection.execute(text(
                "UPDATE tests SET sheet_version = 9 WHERE sheet_version IN (6, 7, 8) AND NOT EXISTS ("
                "SELECT 1 FROM test_attempts WHERE test_attempts.test_id = tests.id AND test_attempts.status = 'done')"
            ))


_upgrade_sheet_versions()


def _clear_unassigned_student_codes() -> None:
    """ID oralig'i belgilanmagan sinflarda ID bo'lmaydi (ilgari avtomatik berilganlari olinadi)."""
    with engine.begin() as connection:
        tables = inspect(engine).get_table_names()
        if "class_students" in tables and "school_classes" in tables:
            connection.execute(text(
                "UPDATE class_students SET student_code = NULL WHERE student_code IS NOT NULL AND class_id IN "
                "(SELECT id FROM school_classes WHERE code_start IS NULL)"
            ))


_clear_unassigned_student_codes()


# ---------- Promo kodlar ----------

def _drop_promo_unique_constraint() -> None:
    """
    Avval bitta o'qituvchi kodni faqat BIR MARTA ishlata olardi (bazada unique
    cheklov). Endi 3 martagacha — eski cheklov olib tashlanadi. Yozuvlar saqlanadi.
    """

    logger = logging.getLogger("backend")
    if not IS_SQLITE:
        with engine.begin() as connection:
            connection.execute(text("ALTER TABLE promo_redemptions DROP CONSTRAINT IF EXISTS uq_promo_teacher"))
        return

    # SQLite cheklovni o'chira olmaydi — jadval yangidan quriladi, yozuvlar ko'chiriladi.
    # Har qadam alohida: yarim qolgan holatdan (masalan _old jadval qolgan bo'lsa) ham tiklanadi.
    with engine.begin() as connection:
        tables = {name: sql or "" for name, sql in connection.execute(
            text("SELECT name, sql FROM sqlite_master WHERE type='table' AND name LIKE 'promo_redemptions%'")
        )}
        if "uq_promo_teacher" in tables.get("promo_redemptions", "") and "promo_redemptions_old" not in tables:
            connection.execute(text("ALTER TABLE promo_redemptions RENAME TO promo_redemptions_old"))
            tables["promo_redemptions_old"] = tables.pop("promo_redemptions")
        if "promo_redemptions_old" not in tables:
            return
        connection.execute(text("DROP INDEX IF EXISTS ix_promo_redemptions_promo_id"))
        connection.execute(text("DROP INDEX IF EXISTS ix_promo_redemptions_teacher_id"))
    PromoRedemption.__table__.create(engine, checkfirst=True)
    with engine.begin() as connection:
        connection.execute(text(
            # id emas, mazmuni bo'yicha solishtiriladi — yangi id bilan ko'chiriladi (hech biri yo'qolmaydi)
            "INSERT INTO promo_redemptions (promo_id, teacher_id, credits, created_at) "
            "SELECT o.promo_id, o.teacher_id, o.credits, o.created_at FROM promo_redemptions_old o "
            "WHERE NOT EXISTS (SELECT 1 FROM promo_redemptions n WHERE n.promo_id = o.promo_id "
            "AND n.teacher_id = o.teacher_id AND n.created_at = o.created_at)"
        ))
        connection.execute(text("DROP TABLE promo_redemptions_old"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_promo_redemptions_promo_id ON promo_redemptions (promo_id)"))
        connection.execute(text("CREATE INDEX IF NOT EXISTS ix_promo_redemptions_teacher_id ON promo_redemptions (teacher_id)"))
    logger.info("promo_redemptions: bir martalik cheklov olib tashlandi (SQLite)")


try:
    _drop_promo_unique_constraint()
except Exception:
    logging.getLogger("backend").warning("Promo cheklovini yangilab bo'lmadi", exc_info=True)


def _promo_hash(code: str) -> str:
    """Kod katta-kichik harf va bo'shliqlarsiz solishtiriladi; faqat izi saqlanadi."""
    import hashlib

    return hashlib.sha256(re.sub(r"\s", "", code).upper().encode()).hexdigest()


def _promo_hint(code: str) -> str:
    code = re.sub(r"\s", "", code).upper()
    return f"{code[:2]}…{code[-2:]}" if len(code) > 4 else "…"


# Boshlang'ich promo kod(lar) — kodning o'zi emas, faqat izi (repo ochiq bo'lsa ham bilinmaydi)
SEED_PROMO_CODES = [
    {"code_hash": "22a75c624bbc4380b4791cf1e145c50bcb8bc638639aaae11d3a69f98a46a299", "hint": "51…09", "credits": 3000,
     "per_teacher_limit": 3},
]


def _seed_promo_codes() -> None:
    with SessionLocal() as session:
        for seed in SEED_PROMO_CODES:
            if not session.scalar(select(PromoCode.id).where(PromoCode.code_hash == seed["code_hash"])):
                session.add(PromoCode(**seed))
        session.commit()


try:
    _seed_promo_codes()
except Exception:  # baza hali tayyor bo'lmasa — ilova baribir ishga tushsin
    logging.getLogger("backend").warning("Promo kodlarni yozib bo'lmadi", exc_info=True)


# ========== 1.2) To'lov kalitlari (admin panelidan) ==========
#
# Payme va Click kalitlari ikki joydan kelishi mumkin: .env (server muhiti) va
# baza (admin panelidagi "To'lov tizimlari" bo'limi). Bazadagisi ustun turadi —
# shunda kalitni almashtirish uchun serverga kirish va qayta deploy shart emas.
#
# Kalitlar shu modulning global o'zgaruvchilariga yoziladi, shuning uchun ularni
# ishlatadigan hamma joy (checkout havolasi, webhook imzosi, "qaysi usul yoqilgan")
# o'zgarishsiz qolaveradi. Server bitta jarayonda ishlaydi — saqlangan kalit
# darhol kuchga kiradi.

PAYMENT_SETTING_KEYS = (
    "payme_merchant_id",
    "payme_secret_key",
    "payme_test_mode",
    "click_service_id",
    "click_merchant_id",
    "click_secret_key",
    "payme_mxik",
    "payme_package_code",
    "payme_vat_percent",
)

# Payme fiskal cheki uchun standart qiymatlar (admin panelidan o'zgartiriladi):
# MXIK — "Dasturiy ta'minot / platforma xizmati"; QQS — 0 (YaTT QQS to'lovchisi emas)
PAYME_MXIK = os.getenv("PAYME_MXIK", "10305008003000000")
PAYME_PACKAGE_CODE = os.getenv("PAYME_PACKAGE_CODE", "1546532")
PAYME_VAT_PERCENT = os.getenv("PAYME_VAT_PERCENT", "0")

# .env'dagi dastlabki qiymatlar: bazada kalit bo'lmasa yoki o'chirilsa, shular qoladi.
# Pastdagi funksiya global o'zgaruvchilarni qayta yozgani uchun, asl qiymatlar shu
# yerda alohida saqlanadi.
_ENV_PAYMENT_DEFAULTS = {
    "payme_merchant_id": PAYME_MERCHANT_ID,
    "payme_secret_key": PAYME_SECRET_KEY,
    "payme_test_mode": PAYME_TEST_MODE,
    "click_service_id": CLICK_SERVICE_ID,
    "click_merchant_id": CLICK_MERCHANT_ID,
    "click_secret_key": CLICK_SECRET_KEY,
    "payme_mxik": PAYME_MXIK,
    "payme_package_code": PAYME_PACKAGE_CODE,
    "payme_vat_percent": PAYME_VAT_PERCENT,
}


def _apply_payment_settings(db: Session) -> None:
    """Bazadagi kalitlarni kuchga kiritadi (bo'sh bo'lsa — .env'dagisi qoladi)."""

    global PAYME_MERCHANT_ID, PAYME_SECRET_KEY, PAYME_TEST_MODE
    global CLICK_SERVICE_ID, CLICK_MERCHANT_ID, CLICK_SECRET_KEY
    global PAYME_MXIK, PAYME_PACKAGE_CODE, PAYME_VAT_PERCENT

    saved = {row.key: (row.value or "").strip() for row in db.scalars(select(AppSetting)).all()}

    def value_of(key: str) -> str:
        return saved.get(key) or _ENV_PAYMENT_DEFAULTS[key]

    PAYME_MERCHANT_ID = value_of("payme_merchant_id")
    PAYME_SECRET_KEY = value_of("payme_secret_key")
    CLICK_SERVICE_ID = value_of("click_service_id")
    CLICK_MERCHANT_ID = value_of("click_merchant_id")
    CLICK_SECRET_KEY = value_of("click_secret_key")
    PAYME_MXIK = value_of("payme_mxik")
    PAYME_PACKAGE_CODE = value_of("payme_package_code")
    PAYME_VAT_PERCENT = value_of("payme_vat_percent")

    test_mode = saved.get("payme_test_mode")
    PAYME_TEST_MODE = test_mode == "1" if test_mode else _ENV_PAYMENT_DEFAULTS["payme_test_mode"]


try:
    with SessionLocal() as _settings_session:
        _apply_payment_settings(_settings_session)
except Exception:  # jadval hali yo'q bo'lsa — .env'dagi kalitlar bilan ishlayveradi
    logging.getLogger("backend").warning("To'lov kalitlarini bazadan o'qib bo'lmadi", exc_info=True)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server ishga tushganda botni va kunlik zaxira nusxa vazifasini ishga tushiradi."""
    _warn_if_storage_is_temporary()
    try:
        logging.getLogger("backend").info("Server: %s", storage_report(_temp_dir()))
    except Exception:
        logging.getLogger("backend").warning("Disk hajmini o'lchab bo'lmadi", exc_info=True)
    bot_task = asyncio.create_task(run_bot())
    backup_task = asyncio.create_task(_daily_backup_loop())
    finish_task = asyncio.create_task(_finish_expired_tests_loop())
    awake_task = asyncio.create_task(_keep_awake_loop())
    yield
    bot_task.cancel()
    backup_task.cancel()
    finish_task.cancel()
    awake_task.cancel()


# API xaritasi (/docs, /redoc, /openapi.json) faqat lokal sinovda ochiq — productionda
# begona odam barcha yo'llarni parametrlari bilan ko'rib chiqmasin
from .database import DEV_MODE  # noqa: E402

_API_DOCS = {} if DEV_MODE else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="Chittak API", lifespan=lifespan, **_API_DOCS)
# JS/CSS fayllar siqilib yuboriladi (~320 KB → ~70 KB) — ilova tezroq ochiladi
app.add_middleware(GZipMiddleware, minimum_size=1000)


class AutosaveLoadShedding:
    """
    Haddan tashqari yuklamada server qotib qolmasligi uchun.

    Yuklama sinovi (0.1 CPU, 200 o'quvchi): protsessor ulgurmay, so'rovlar navbatda
    baza ulanishini 60 s kutib xato bilan tugar, server umuman javob bermay qolardi.
    Endi bir vaqtda ishlanayotgan so'rovlar AUTOSAVE_SHED_AT dan oshsa, eng kam
    muhim so'rov — oraliq avtosaqlash (/api/attempts/{id}/answers) — darhol 503
    oladi. Bu xavfsiz: javoblar telefon xotirasida turadi, keyingi saqlashda yoki
    yakunlashda (finish hamma javobni yuboradi) serverga yetadi. Ilovani ochish,
    testni boshlash, yakunlash — o'taveradi.
    """

    def __init__(self, asgi_app, shed_at: int = 40):
        self.app = asgi_app
        self.shed_at = shed_at
        self.in_flight = 0

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path", "")
        if (self.in_flight >= self.shed_at and scope.get("method") == "POST"
                and path.startswith("/api/attempts/") and path.endswith("/answers")):
            await JSONResponse({"detail": "Server band — javoblar keyinroq saqlanadi"}, status_code=503,
                               headers={"Retry-After": "10"})(scope, receive, send)
            return
        self.in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            self.in_flight -= 1


class BodySizeLimit:
    """
    Juda katta so'rovni o'qimasdan rad etadi. Usiz istalgan (kirmagan ham) odam yuzlab MB'lik
    so'rov yuborib, 512 MB xotirali serverni yiqita olardi — hajm tekshiruvi (pydantic
    max_length) so'rov to'liq o'qilgandan KEYIN ishlaydi. Eng katta qonuniy so'rov —
    titul surati (12 MB base64).
    """

    def __init__(self, asgi_app, limit: int):
        self.app = asgi_app
        self.limit = limit

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        if declared.isdigit() and int(declared) > self.limit:
            await JSONResponse({"detail": "So'rov juda katta"}, status_code=413)(scope, receive, send)
            return

        received = 0

        async def limited_receive():
            # Content-Length'siz (bo'lak-bo'lak) kelgan so'rov ham chegaradan oshsa — uziladi
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    return {"type": "http.disconnect"}
            return message

        await self.app(scope, limited_receive, send)


MAX_REQUEST_BYTES = 16 * 1024 * 1024
app.add_middleware(BodySizeLimit, limit=MAX_REQUEST_BYTES)

AUTOSAVE_SHED_AT = 40  # anyio ip havzasi (40) to'lganda — avtosaqlashlar kutmaydi
app.add_middleware(AutosaveLoadShedding, shed_at=AUTOSAVE_SHED_AT)


# Ta'lim muassasalari uch turda: xususiy maktab, o'quv markazi (kurslar) va to'garak.
# Ilova qaysi bo'limda ekanini X-Inst-Kind sarlavhasida yuboradi; "maktabim" degan
# hamma joy (_active_membership) shu turdagi a'zolikni oladi. Sarlavha yo'q — maktab.
INSTITUTION_KINDS = ("school", "course", "club")
_request_inst_kind: contextvars.ContextVar[str] = contextvars.ContextVar("inst_kind", default="school")


class InstitutionKindMiddleware:
    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            kind = dict(scope.get("headers") or []).get(b"x-inst-kind", b"school").decode("latin-1")
            token = _request_inst_kind.set(kind if kind in INSTITUTION_KINDS else "school")
            try:
                await self.app(scope, receive, send)
            finally:
                _request_inst_kind.reset(token)
            return
        await self.app(scope, receive, send)


app.add_middleware(InstitutionKindMiddleware)


class StaticCacheHeaders:
    """
    Statik fayllar uchun brauzer keshi.

    index.html ichidagi js/css havolalari versiyalangan ("?v=abc123", qarang:
    _asset_version): fayl o'zgarsa, havola ham o'zgaradi. Shuning uchun ular
    bir yilga saqlanadi va ilova qayta ochilganda serverdan umuman so'ralmaydi.
    Rasmlar — bir hafta. sw.js hech qachon keshlanmaydi (yangisi darhol kelsin).
    Javobda Cache-Control allaqachon bo'lsa (masalan, index.html), tegilmaydi.

    Yangilanish paytida (Render eski va yangi serverni bir-ikki daqiqa birga ishlatadi) yangi
    versiya havolasi eski serverga tushib, ESKI fayl qaytishi mumkin — u bir yilga "o'zgarmas" deb
    saqlanib, ilova qayta ochilganda ham eski menyu chiqardi. Shuning uchun so'ralgan versiya shu
    serverdagisiga mos kelmasa — keshlanmaydi; javobda serverdagi versiya (X-Asset-Version) aytiladi,
    sw.js ham faqat mos versiyani saqlaydi.
    """

    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "") if scope["type"] == "http" else ""
        if not path or path.startswith("/api/") or scope.get("method") not in ("GET", "HEAD"):
            await self.app(scope, receive, send)
            return

        query = scope.get("query_string", b"").decode("latin-1")
        served_version = None
        if path == "/sw.js":
            policy = b"no-cache"
        elif "v=" in query:
            policy = b"public, max-age=31536000, immutable"
            if path in VERSIONED_ASSETS:
                served_version = _asset_version()
                requested = dict(item.split("=", 1) for item in query.split("&") if "=" in item).get("v")
                if requested != served_version:
                    policy = b"no-store"
        elif path.startswith("/img/"):
            policy = b"public, max-age=604800"
        else:
            policy = None

        async def send_with_cache(message):
            if (policy and message["type"] == "http.response.start" and message.get("status") == 200
                    and not any(name.lower() == b"cache-control" for name, _ in message.get("headers", []))):
                headers = [*message.get("headers", []), (b"cache-control", policy)]
                if served_version:
                    headers.append((b"x-asset-version", served_version.encode()))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


app.add_middleware(StaticCacheHeaders)


# Har javobga qo'shiladigan xavfsizlik sarlavhalari:
#   nosniff         — brauzer fayl turini o'zi "taxmin qilib", matnni skript sifatida ishga tushirmasin;
#   referrer-policy — boshqa saytga o'tilganda to'liq manzil (kodlar, ID'lar) emas, faqat domen ketsin;
#   frame-ancestors — saytni faqat o'zi va Telegram (web.telegram.org — Mini App shu yerda iframe'da
#                     ochiladi) o'z sahifasiga joylay oladi: begona sayt Chittak'ni yashirin oynaga
#                     qo'yib, foydalanuvchini aldab tugma bostira olmaydi (clickjacking).
#   HSTS            — faqat HTTPS orqali kelgan so'rovga: brauzer keyingi safar ham faqat HTTPS bilan ochadi.
SECURITY_HEADERS = (
    ("x-content-type-options", "nosniff"),
    ("referrer-policy", "strict-origin-when-cross-origin"),
    ("content-security-policy", "frame-ancestors 'self' https://web.telegram.org https://*.telegram.org"),
)
HSTS_HEADER = "max-age=31536000"


class SecurityHeaders:
    def __init__(self, asgi_app):
        self.app = asgi_app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        forwarded = dict(scope.get("headers") or []).get(b"x-forwarded-proto", b"").decode("latin-1")
        https = (forwarded.split(",")[0].strip() or scope.get("scheme", "")) == "https"

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS:
                    headers.setdefault(name, value)
                if https:
                    headers.setdefault("strict-transport-security", HSTS_HEADER)
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(SecurityHeaders)


_STARTED_AT = time.monotonic()


# HEAD ham: tashqi kuzatuv xizmatlari (UptimeRobot) saytni HEAD so'rovi bilan tekshiradi
@app.api_route("/api/health", methods=["GET", "HEAD"])
def health():
    """
    Server tirikligini tekshirish (uyg'oq tutish uchun ham). Nosozlikni tashqaridan kuzatish uchun:
    qaysi versiya ishlayapti (Render beradigan commit), necha daqiqadan beri (qayta ishga tushganmi)
    va qancha xotira egallagan (Render bepul tarifi 512 MB'dan oshsa serverni o'ldiradi).
    """
    used = rss_mb()
    return {
        "ok": True,
        "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7] or None,
        "uptime_min": round((time.monotonic() - _STARTED_AT) / 60),
        "memory_mb": round(used) if used is not None else None,
    }


# Render'ning bepul tarifi 15 daqiqa so'rov kelmasa serverni uxlatib qo'yadi:
# keyingi kirish 30-50 soniya cho'ziladi va bot ham javob bermaydi. Server o'z
# manzilini har 10 daqiqada so'rab, uyg'oq turadi (RENDER_EXTERNAL_URL'ni
# Render o'zi beradi; lokalda bu ishlamaydi).
KEEP_AWAKE_SECONDS = 600


async def _keep_awake_loop() -> None:
    base_url = os.getenv("RENDER_EXTERNAL_URL", "").rstrip("/")
    if not base_url:
        return
    async with httpx.AsyncClient(timeout=20) as client:
        while True:
            await asyncio.sleep(KEEP_AWAKE_SECONDS)
            try:
                await client.get(f"{base_url}/api/health")
            except Exception:
                logging.getLogger("backend").warning("Uyg'oq tutish so'rovi o'tmadi", exc_info=True)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"

# Vaqtinchalik fayllar (zaxira nusxa, test arxivi ZIP) — loyiha papkasi ichida, ya'ni aniq diskda.
# Ba'zi serverlarda /tmp operativ xotirada turadi va u yerga yozilgan fayl 512 MB hisobiga kirardi.
# Render'da bu disk vaqtinchalik (qayta ishga tushganda tozalanadi) — fayllar bir necha daqiqa yashaydi.
TEMP_DIR = Path(__file__).resolve().parent.parent / ".tmp"


def _temp_dir() -> Path:
    TEMP_DIR.mkdir(exist_ok=True)
    return TEMP_DIR


# ========== 1.1) Zaxira nusxa (backup) ==========
#
# Ma'lumotlar PostgreSQL'da saqlanadi va deploy qilinganda yo'qolmaydi.
# Lekin bazaning o'zi bilan nimadir bo'lib qolsa (xizmat o'chirilsa, adashib
# o'chirib yuborilsa) deb, butun bazaning nusxasi JSON fayl ko'rinishida
# admin'ning Telegram chatiga yuboriladi: har kuni avtomatik va istalgan
# paytda admin panelidagi tugma orqali.


def write_database_export(db: Session, out) -> int:
    """
    Butun bazani JSON qilib `out` (ikkilik fayl) ga yozadi va yozuvlar sonini qaytaradi.

    Fayl tuzilishi: {"exported_at": ..., "tables": {jadval: [qator, ...]}} — restore_backup shuni o'qiydi.
    Qatorlar bazadan bo'lib-bo'lib o'qiladi va darhol faylga yoziladi: bazada skaner rasmlari
    (base64) bor, butun bazani xotirada yig'ish server xotirasini (512 MB) bir necha barobar
    to'ldirib, uni o'ldirishi mumkin edi.
    """

    def write(chunk: str) -> None:
        out.write(chunk.encode("utf-8"))

    row_count = 0
    write('{"exported_at": ' + json.dumps(utcnow().isoformat()) + ', "tables": {')
    for table_index, table in enumerate(Base.metadata.sorted_tables):
        write(("," if table_index else "") + "\n" + json.dumps(table.name) + ": [")
        # Skaner rasmlari nusxaga tushmaydi — ular deyarli siqilmaydi va nusxani Telegram
        # chegarasidan oshirib yuboradi. Titul rasmlari umuman olinmaydi; yozma javoblarning
        # bahosi va AI o'qigan matni qoladi, faqat katak rasmi bo'sh ("") bo'ladi.
        if table.name == SheetImage.__tablename__:
            write("]")
            continue
        blank = {"image": ""} if table.name == WrittenAnswer.__tablename__ else {}
        columns = [column for column in table.columns if column.name not in blank]
        rows = db.execute(select(*columns).execution_options(yield_per=200)).mappings()
        first = True
        for row in rows:
            row = {**row, **blank}
            # To'lov kalitlari nusxaga tushmaydi: bu fayl Telegram chatiga yuboriladi,
            # maxfiy kalit esa u yerda yotmasligi kerak. Ular HAMMASI chiqariladi (faqat
            # maxfiylari emas): tiklangan bazada Merchant ID bo'lib, kalit bo'lmasa, Payme
            # yoqilgandek ko'rinardi, lekin to'lov tasdig'i qabul qilinmay pul osilib qolardi.
            # Baza tiklanganda kalitlar admin panelidan qaytadan kiritiladi.
            if table.name == AppSetting.__tablename__ and row["key"] in PAYMENT_SETTING_KEYS:
                continue
            # default=str — sana/vaqt qiymatlarini matnga aylantiradi
            write(("" if first else ",") + "\n " + json.dumps(dict(row), ensure_ascii=False, default=str))
            first = False
            row_count += 1
        write("]")
    write("\n}}\n")
    return row_count


def export_database(db: Session) -> bytes:
    """Butun bazani bitta JSON faylga aylantiradi."""

    buffer = io.BytesIO()
    write_database_export(db, buffer)
    return buffer.getvalue()


# Telegram bot 50 MB'dan katta fayl yubora olmaydi — kattaroq nusxa shu o'lchamdagi qismlarga bo'linadi
BACKUP_PART_BYTES = 45 * 1024 * 1024
BACKUP_SEND_ATTEMPTS = 3
BACKUP_RETRY_SECONDS = 5


def _copy_backup_part(source, target: Path) -> None:
    """Nusxaning navbatdagi qismini alohida faylga ko'chiradi (xotiraga butun qism yuklanmaydi)."""

    remaining = BACKUP_PART_BYTES
    with target.open("wb") as out:
        while remaining and (chunk := source.read(min(remaining, 1024 * 1024))):
            out.write(chunk)
            remaining -= len(chunk)


def _send_backup_part(admin_id: int, file_name: str, path: Path, caption: str) -> bool:
    """Bitta qism: tarmoq uzilsa qayta urinadi — bitta qism yetmasa, butun nusxa yaroqsiz bo'lardi."""

    for attempt in range(1, BACKUP_SEND_ATTEMPTS + 1):
        try:
            _send_pdf_to_telegram(admin_id, file_name, path, caption=caption, mime="application/gzip")
            return True
        except Exception:
            logging.getLogger("backend").warning("Zaxira nusxa %s ga yuborilmadi (%s-urinish)", admin_id, attempt, exc_info=True)
            if attempt < BACKUP_SEND_ATTEMPTS:
                time.sleep(BACKUP_RETRY_SECONDS)
    return False


def send_backup_to_admin(db: Session) -> bool:
    """
    Zaxira nusxani adminlarning Telegram chatiga yuboradi. Xato bo'lsa, ilovani to'xtatmaydi.

    Esse fotosuratlari deyarli siqilmaydi (skaner rasmlari nusxaga tushmaydi), shuning uchun nusxa Telegram chegarasidan
    oshib ketishi mumkin: unda fayl qismlarga bo'linadi (backup_….json.gz.part1of2, part2of2).
    Tiklash: python -m backend.restore_backup <istalgan qism> — qolganlarini yonidan o'zi topadi.
    """

    if not ADMIN_ID:
        return False

    try:
        with tempfile.TemporaryDirectory(dir=_temp_dir()) as folder:
            packed_path = Path(folder) / "backup.json.gz"
            # Nusxa diskka, siqilgan holda yoziladi (xotirada butun baza yig'ilmaydi)
            with packed_path.open("wb") as handle, gzip.GzipFile(fileobj=handle, mode="wb") as packed:
                row_count = write_database_export(db, packed)

            now = datetime.now()
            base_name = f"backup_{now:%Y-%m-%d_%H-%M}.json.gz"
            total = max(1, math.ceil(packed_path.stat().st_size / BACKUP_PART_BYTES))
            receivers = list(ADMIN_IDS)  # biror qism yetmagan admin keyingilarini olmaydi
            with packed_path.open("rb") as source:
                for number in range(1, total + 1):
                    part_path = Path(folder) / f"part{number}"
                    _copy_backup_part(source, part_path)
                    file_name = base_name if total == 1 else f"{base_name}.part{number}of{total}"
                    title = f"💾 Zaxira nusxa ({number}/{total}-qism)" if total > 1 else "💾 Zaxira nusxa"
                    advice = (
                        "Hamma qismlarni bir papkada saqlang — bittasi yo'qolsa, baza tiklanmaydi."
                        if total > 1 else
                        "Bu faylni saqlab qo'ying — baza bilan nimadir bo'lsa, hammasi shundan tiklanadi."
                    )
                    caption = f"{title}\n\nSana: {now:%d.%m.%Y %H:%M}\nJami yozuvlar: {row_count} ta\n\n{advice}"
                    receivers = [admin_id for admin_id in receivers
                                 if _send_backup_part(admin_id, file_name, part_path, caption)]
                    part_path.unlink()
                    if not receivers:
                        _warn_admins_backup_failed(
                            f"{number}/{total}-qism hech bir adminga yetib bormadi" if total > 1
                            else "fayl hech bir adminga yetib bormadi"
                        )
                        return False
        return True
    except Exception:
        logging.getLogger("backend").warning("Zaxira nusxa yuborilmadi", exc_info=True)
        _warn_admins_backup_failed("nusxani tayyorlashda xato (batafsil — server logida)")
        return False


def _warn_admins_backup_failed(reason: str) -> None:
    """Zaxira nusxa olinmasa — jim qolmaydi: adminlarga oddiy xabar boradi (fayl emas, shuning uchun yetadi)."""

    if not BOT_TOKEN:
        return
    for admin_id in ADMIN_IDS:
        try:
            httpx.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                json={"chat_id": admin_id, "text": f"⚠️ Zaxira nusxa OLINMADI: {reason}.\n\n"
                                                   "Baza hozir zaxirasiz qolmoqda — tekshiring."},
                timeout=10,
            )
        except Exception:
            logging.getLogger("backend").warning("Zaxira xatosi haqida adminga xabar yuborilmadi", exc_info=True)


NEWLINE = chr(10)


def send_results_to_teacher(db: Session, test: Test) -> bool:
    """
    Test natijalarini PDF qilib o'qituvchining Telegram chatiga yuboradi.

    Test vaqti tugaganda avtomatik, yoki o'qituvchi testni yakunlaganda chaqiriladi.
    Bir marta yuborilgandan keyin qayta yuborilmaydi (results_sent_at).
    """

    if test.results_sent_at:
        return False

    teacher = test.teacher
    results = best_attempts(db, test)
    total_questions = _question_count(test)

    try:
        rows = [
            f"{index}. {name} — {attempt.correct}/{total_questions} ({attempt.percent}%)"
            for index, (attempt, name) in enumerate(results[:20], start=1)
        ]
        caption = NEWLINE.join(
            [
                f"📊 Test yakunlandi: {test.title}",
                f"🔑 Kod: {test.code}",
                f"👥 Qatnashchilar: {len(results)} ta",
                "",
                *rows,
            ]
        )
        if len(results) > 20:
            caption += NEWLINE + "...to'liq ro'yxat PDF faylda"

        _send_pdf_to_telegram(
            teacher.user.tg_id,
            f"{test.code}_natijalar.pdf",
            generate_test_report_pdf(db, test, teacher),
            caption=caption,
        )

        test.results_sent_at = utcnow()
        db.commit()
        return True
    except Exception:
        logging.getLogger("backend").warning("Natijalar yuborilmadi (test %s)", test.code, exc_info=True)
        return False


def _finish_expired_attempts(db: Session) -> list:
    """
    Vaqti tugab, yakunlanmay qolgan urinishlarni tekshirib qo'yadi.

    O'quvchi javoblarini belgilab borgan, lekin "YAKUNLASH" ni bosmagan bo'lishi
    mumkin (ilovani yopib qo'ygan, interneti uzilgan). Javoblari serverda
    saqlanib turadi — shuning uchun mehnati yo'qolmasligi uchun shu javoblar
    bo'yicha natija o'zi hisoblanadi.
    """

    notices = []
    attempts = db.scalars(select(Attempt).where(Attempt.status == "in_progress")).all()

    for attempt in attempts:
        test = db.get(Test, attempt.test_id)
        if not test:
            continue
        if (utcnow() - attempt.started_at).total_seconds() < test.duration * 60:
            continue
        # O'quvchi shu payt o'zi yakunlayotgan bo'lishi mumkin — qulflab, qayta tekshiramiz
        db.refresh(attempt, with_for_update=True)
        if attempt.status != "in_progress":
            continue

        total_questions = _grade_attempt_from_saved_answers(db, attempt, test)
        notices.append(_result_notice(db, attempt, test, total_questions))

    return notices


async def _finish_expired_tests_loop() -> None:
    """
    Har 5 daqiqada: vaqti tugagan urinishlarni tekshiradi, vaqti tugagan
    testlarni yakunlaydi va natijalarni o'qituvchiga yuboradi. Shunda o'qituvchi
    hech narsa bosmasdan, barcha o'quvchilar ishlab bo'lgach hisobotni oladi.
    """

    while True:
        await asyncio.sleep(5 * 60)

        try:
            # MUHIM: bu ish alohida ipda bajariladi. Telegram bot ham AYNAN shu
            # event loop ichida ishlaydi, PDF yasash va Telegram'ga fayl yuborish
            # esa sinxron amallar — to'g'ridan-to'g'ri chaqirilsa, bot o'nlab
            # sekund davomida hech kimga javob bermay qolardi.
            await asyncio.to_thread(_finish_expired_tests_once)
        except Exception:
            logging.getLogger("backend").warning("Vaqti tugagan testlarni tekshirib bo'lmadi", exc_info=True)
        try:
            await asyncio.to_thread(_delete_scheduled_schools_once)
        except Exception:
            logging.getLogger("backend").warning("O'chirishga qo'yilgan maktablarni tekshirib bo'lmadi", exc_info=True)
        try:
            await asyncio.to_thread(_auto_cleanup_tests_once)
        except Exception:
            logging.getLogger("backend").warning("Testlarni avtomatik yopish/o'chirish bajarilmadi", exc_info=True)
        await asyncio.to_thread(_release_memory_and_log)


def _release_memory_and_log() -> None:
    """Bo'shagan xotirani tizimga qaytaradi va qancha egallanganini logga yozadi (Render'da 512 MB chegara)."""

    try:
        used = release_memory()
    except Exception:
        logging.getLogger("backend").warning("Xotirani bo'shatib bo'lmadi", exc_info=True)
        return
    if used is None:
        return
    logger = logging.getLogger("backend")
    if used >= MEMORY_WARN_MB:
        logger.warning("Xotira: %.0f MB — 512 MB chegarasiga yaqin", used)
    else:
        logger.info("Xotira: %.0f MB", used)


def _finish_expired_tests_once() -> None:
    """Yuqoridagi tsiklning bitta qadami (alohida ipda ishlaydi)."""

    session = SessionLocal()
    try:
        notices = _finish_expired_attempts(session)
        session.commit()
        # Natija bazaga yozilgandan KEYIN xabar yuboriladi
        for notice in notices:
            _send_result_notice(notice)

        expired = session.scalars(
            select(Test).where(
                Test.end_time.is_not(None),
                Test.end_time <= utcnow(),
                Test.results_sent_at.is_(None),
                Test.deleted_at.is_(None),
            )
        ).all()

        for test in expired:
            if test.status != "closed":
                _mark_test_closed(test)
            send_results_to_teacher(session, test)
        session.commit()

        if BOT_TOKEN:
            _remind_pending_payments(session)
    finally:
        session.close()


# ========== 1.2) Testlarni avtomatik yopish va o'chirish ==========
#
# Baza (bepul tarifda 1 GB) skanerlangan titul rasmlari bilan to'lib qolmasligi uchun:
# - har qanday test (yopilgan-yopilmaganidan qat'i nazar) yaratilganidan (qayta ochilganidan)
#   10 kun o'tib o'zi yopiladi va o'sha zahoti o'chiriladi: avval barcha ma'lumotlari — natijalar
#   (Excel), hisobot (PDF) va titul rasmlari — bitta ZIP bo'lib o'qituvchining Telegram'iga
#   yuboriladi; 3 kun oldin ogohlantiriladi (ogohlantirishdan kamida 3 kun o'tmay o'chmaydi).
#   Tugash vaqti keyinroq qo'yilgan bo'lsa — o'sha vaqtda. ZIP yetib bormasa (bot bloklangan,
#   Telegram xatosi) — hech narsa o'chirilmaydi, ertasi kuni qayta uriniladi.
# Bazadan faqat og'ir qism — titul va yozma javob rasmlari — butunlay o'chadi. Test yozuvi,
# ballar va to'lovlar qoladi (sinf jurnali, o'quvchi profili, to'lov hisobi buzilmasin):
# test xuddi o'qituvchi o'zi o'chirgandek ro'yxatdan yashiriladi.

TEST_LIFETIME = timedelta(days=10)  # yaratilganidan shuncha vaqtda yopiladi va o'chiriladi
TEST_DELETE_WARNING = timedelta(days=3)
TEST_ARCHIVE_RETRY = timedelta(days=1)
# Qoida shu kuni ishga tushdi: oldingi testlar uchun muddat shu kundan sanaladi —
# eski testlar yangilanish chiqqan kuni birdaniga yopilib, o'chib ketmasin
TEST_CLEANUP_SINCE = datetime(2026, 11, 15)
# Telegram bot 50 MB'dan katta fayl yubora olmaydi — ZIP shundan oshsa, qismlarga bo'linadi
TEST_ARCHIVE_PART_LIMIT = 45 * 1024 * 1024
TEST_ARCHIVES_PER_RUN = 3  # bir tekshiruvda ko'pi bilan nechta test arxivlanadi (server band bo'lib qolmasin)


def _mark_test_closed(test: Test) -> None:
    test.status = "closed"
    test.closed_at = utcnow()
    # delete_warned_at qoladi: o'chirish muddati yopilgan kundan emas, yaratilgan kundan sanaladi
    test.archive_tried_at = None


def _mark_test_reopened(test: Test) -> None:
    test.status = "active"
    test.reopened_at = utcnow()
    test.closed_at = test.delete_warned_at = test.archive_tried_at = None


def _test_lifetime_end(test: Test) -> datetime:
    """Yaratilgan (qayta ochilgan) kundan 10 kun; tugash vaqti keyinroq qo'yilgan bo'lsa — o'sha vaqt."""

    due = max(test.created_at, test.reopened_at or test.created_at, TEST_CLEANUP_SINCE) + TEST_LIFETIME
    return max(due, test.end_time) if test.end_time else due


def _test_auto_close_at(test: Test) -> datetime | None:
    """Ochiq test qachon o'zi yopiladi."""

    if test.status == "closed" or test.deleted_at:
        return None
    return _test_lifetime_end(test)


def _test_auto_delete_at(test: Test) -> datetime | None:
    """Test qachon o'chiriladi (ochiq bo'lsa — o'sha kuni yopilib; ogohlantirishdan keyin kamida 3 kun o'tadi)."""

    if test.deleted_at:
        return None
    due = _test_lifetime_end(test)
    if test.delete_warned_at:
        due = max(due, test.delete_warned_at + TEST_DELETE_WARNING)
    return due


def _local_date(moment: datetime) -> str:
    return (moment + TASHKENT_OFFSET).strftime("%d.%m.%Y")


def _safe_file_name(name: str, limit: int = 80) -> str:
    cleaned = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', " ", name or "")
    return " ".join(cleaned.split())[:limit].strip() or "fayl"


def _build_test_archive(db: Session, test: Test, folder: Path) -> tuple[list[Path], int]:
    """
    Test arxivi: natijalar (Excel), hisobot (PDF) va Titullar/ papkasida har o'quvchining titul rasmi.
    Diskka yoziladi va 45 MB'dan oshsa, qismlarga bo'linadi. (qismlar, titul rasmlari soni)
    """

    has_results = bool(db.scalar(select(Attempt.id).where(Attempt.test_id == test.id, Attempt.status == "done").limit(1)))
    reports = []
    for file_name, build in (
        (f"{test.code}_natijalar.xlsx", lambda: generate_test_report_xlsx(db, test)),
        (f"{test.code}_hisobot.pdf", lambda: generate_test_report_pdf(db, test, test.teacher)),
    ):
        try:
            reports.append((file_name, build()))
        except Exception:
            # Natijasi bor testning hisobotini yasab bo'lmasa — arxivlanmaydi (hech narsa o'chmaydi)
            if has_results:
                raise
            logging.getLogger("backend").warning("Arxiv: %s yasalmadi (test %s)", file_name, test.id, exc_info=True)

    parts: list[Path] = []
    archive = None
    size = 0

    def next_part():
        nonlocal archive, size
        if archive:
            archive.close()
        parts.append(folder / f"part{len(parts) + 1}.zip")
        archive = zipfile.ZipFile(parts[-1], "w", zipfile.ZIP_DEFLATED)
        size = 0

    next_part()
    try:
        for file_name, data in reports:
            archive.writestr(file_name, data)
            size += len(data)

        # Titullar — bittadan o'qiladi (hammasi birdaniga xotiraga yuklanmaydi)
        sheets = db.execute(
            select(SheetImage.id, SheetImage.page, Attempt.id, Attempt.offline_name, User.full_name)
            .join(Attempt, Attempt.id == SheetImage.attempt_id)
            .outerjoin(User, User.id == Attempt.user_id)
            .where(Attempt.test_id == test.id)
            .order_by(Attempt.offline_name, Attempt.id, SheetImage.page)
        ).all()
        used: set[str] = set()
        count = 0
        for sheet_id, page, attempt_id, offline_name, full_name in sheets:
            image = db.scalar(select(SheetImage.image).where(SheetImage.id == sheet_id)) or ""
            header, _, encoded = image.partition(",")
            try:
                data = base64.b64decode(encoded)
            except (binascii.Error, ValueError):
                continue
            if not data:
                continue
            extension = "png" if "image/png" in header else "jpg"
            stem = _safe_file_name(offline_name or full_name or f"O'quvchi {attempt_id}") + (f"_{page}" if page > 1 else "")
            name, copy = stem, 2
            while name.lower() in used:
                name, copy = f"{stem} ({copy})", copy + 1
            used.add(name.lower())
            if size and size + len(data) > TEST_ARCHIVE_PART_LIMIT:
                next_part()
            archive.writestr(f"Titullar/{name}.{extension}", data, compress_type=zipfile.ZIP_STORED)
            size += len(data)
            count += 1
    finally:
        archive.close()
    return parts, count


def _archive_and_delete_test(db: Session, test: Test) -> None:
    """ZIP'ni o'qituvchiga yuboradi; yetkazilgandagina rasmlar o'chiriladi va test ro'yxatdan yashiriladi."""

    chat_id = test.teacher.user.tg_id
    with tempfile.TemporaryDirectory(dir=_temp_dir()) as folder:
        parts, sheet_count = _build_test_archive(db, test, Path(folder))
        base_name = f"{_safe_file_name(test.title, 40)}_{test.code}"
        for index, path in enumerate(parts, start=1):
            if index == 1:
                caption = NEWLINE.join([
                    f"🗂 «{test.title}» ({test.code}) testi yaratilganiga {TEST_LIFETIME.days} kun bo'lgani uchun avtomatik o'chirildi.",
                    "",
                    "Barcha ma'lumotlari shu faylda:",
                    "• natijalar — Excel",
                    "• hisobot — PDF",
                    *([f"• titullar — {sheet_count} ta rasm"] if sheet_count else []),
                    "",
                    "Faylni saqlab qo'ying — test endi ro'yxatingizda ko'rinmaydi."
                    + (f" Fayl {len(parts)} qismga bo'lingan, hammasini saqlang." if len(parts) > 1 else ""),
                ])
            else:
                caption = f"🗂 «{test.title}» ({test.code}) — {index}/{len(parts)}-qism"
            file_name = f"{base_name}_{index}-qism.zip" if len(parts) > 1 else f"{base_name}.zip"
            _send_pdf_to_telegram(chat_id, file_name, path, caption=caption, mime="application/zip")

    # Yetkazildi — og'ir qism (rasmlar) bazadan o'chadi, ballar va to'lovlar qoladi
    attempt_ids = select(Attempt.id).where(Attempt.test_id == test.id)
    db.execute(delete(SheetImage).where(SheetImage.attempt_id.in_(attempt_ids)))
    db.execute(delete(EssayImage).where(EssayImage.attempt_id.in_(attempt_ids)))
    db.execute(update(WrittenAnswer).where(WrittenAnswer.attempt_id.in_(attempt_ids)).values(image=""))
    test.deleted_at = test.auto_deleted_at = utcnow()
    db.commit()
    logging.getLogger("backend").info("Test avtomatik o'chirildi: id=%s kod=%s, %s qism", test.id, test.code, len(parts))


def _auto_cleanup_tests_once() -> None:
    """Har 5 daqiqada (alohida ipda): muddati kelgan testlarni yopadi, ogohlantiradi, arxivlab o'chiradi."""

    now = utcnow()
    logger = logging.getLogger("backend")
    with SessionLocal() as db:
        # 1) Yaratilganiga 10 kun bo'lgan ochiq testlar yopiladi (o'chirish — pastda, 3-qadamda)
        for test in db.scalars(
            select(Test).where(Test.status != "closed", Test.deleted_at.is_(None),
                               Test.created_at <= now - TEST_LIFETIME)
        ).all():
            due = _test_auto_close_at(test)
            if not due or due > now:
                continue
            _mark_test_closed(test)
            db.commit()
            delete_at = _test_auto_delete_at(test)
            # Odatda shu zahoti o'chiriladi va ZIP xabari keladi; ogohlantirish kech ketgan bo'lsa — kun aytiladi
            if BOT_TOKEN and delete_at and delete_at > now:
                _notify_teacher(test.teacher, NEWLINE.join([
                    f"🔒 «{test.title}» ({test.code}) testi yaratilganiga {TEST_LIFETIME.days} kun bo'lgani uchun avtomatik yopildi.",
                    "",
                    f"{_local_date(delete_at)} kuni test o'chiriladi — natijalar (Excel, PDF) va "
                    "titullar Telegram'ingizga ZIP fayl bo'lib keladi.",
                    "Test hali kerak bo'lsa — uni ochib «Qayta ochish»ni bosing.",
                ]))

        # 2) O'chirilishiga 3 kun qolgan testlar (ochiq yoki yopiq) — o'qituvchiga bitta xabar
        warn_by_teacher: dict[int, list[Test]] = {}
        for test in db.scalars(
            select(Test).where(Test.deleted_at.is_(None), Test.delete_warned_at.is_(None))
        ).all():
            due = _test_auto_delete_at(test)
            if due and due - TEST_DELETE_WARNING <= now:
                warn_by_teacher.setdefault(test.teacher_id, []).append(test)
        for tests in warn_by_teacher.values():
            for test in tests:
                test.delete_warned_at = now
            db.commit()
            if not BOT_TOKEN:
                continue
            lines = [f"• «{test.title}» ({test.code}) — {_local_date(_test_auto_delete_at(test))}" for test in tests[:30]]
            if len(tests) > 30:
                lines.append(f"…va yana {len(tests) - 30} ta")
            _notify_teacher(tests[0].teacher, NEWLINE.join([
                "⏳ Quyidagi testlar 3 kundan keyin avtomatik yopilib, o'chiriladi:",
                *lines,
                "",
                "O'sha kuni har birining natijalari (Excel, PDF) va titullari Telegram'ingizga ZIP fayl bo'lib keladi.",
                "Biror test hali kerak bo'lsa — uni ochib «Qayta ochish»ni bosing.",
            ]))

        # 3) Muddati kelganlari — ZIP yuboriladi va o'chiriladi (yetkazib bo'lmasa — ertaga qayta)
        if not BOT_TOKEN:
            return
        archived = 0
        for test in db.scalars(
            select(Test).where(Test.status == "closed", Test.deleted_at.is_(None), Test.delete_warned_at.is_not(None))
            .order_by(Test.closed_at)
        ).all():
            if archived >= TEST_ARCHIVES_PER_RUN:
                break
            due = _test_auto_delete_at(test)
            if not due or due > now or (test.archive_tried_at and now - test.archive_tried_at < TEST_ARCHIVE_RETRY):
                continue
            test.archive_tried_at = now
            db.commit()
            archived += 1
            try:
                _archive_and_delete_test(db, test)
            except Exception:
                db.rollback()
                logger.warning("Test arxivi yuborilmadi — ertaga qayta uriniladi (test %s)", test.id, exc_info=True)


BACKUP_INTERVAL_SECONDS = 6 * 60 * 60  # kuniga 4 marta
FIRST_BACKUP_DELAY_SECONDS = 10 * 60   # server ishga tushgandan 10 daqiqa keyin


def _warn_if_storage_is_temporary() -> None:
    """
    Render'da SQLite bilan ishlash — eng katta xavf: har deploy va uyqudan
    uyg'onishda butun baza (pullar, ballar) o'chib ketadi. Bunday holatda
    admin'ga darhol, baland ovozda xabar beramiz.
    """

    if not (IS_SQLITE and os.getenv("RENDER")):
        return

    message = (
        "🚨 DIQQAT: server vaqtinchalik SQLite bazasida ishlayapti!\n\n"
        "Render'da bu baza har deploy va uyqudan uyg'onishda O'CHIB KETADI — "
        "o'qituvchilar, tokenlar, to'lovlar va ballar yo'qoladi.\n\n"
        "Render → Environment bo'limida DATABASE_URL ga doimiy PostgreSQL manzilini qo'ying."
    )
    logging.getLogger("backend").error(message)
    for admin_id in ADMIN_IDS if BOT_TOKEN else ():
        try:
            httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                       json={"chat_id": admin_id, "text": message}, timeout=10)
        except Exception:
            logging.getLogger("backend").warning("Ogohlantirish yuborilmadi", exc_info=True)


async def _daily_backup_loop() -> None:
    """
    Zaxira nusxani admin'ga yuboradi: server ishga tushgach (har deploydan
    keyin) 10 daqiqada bir marta, keyin har 6 soatda. Bu botda pul va
    o'quvchilar ballari aylanadi — eng ko'pi bilan bir necha soatlik
    ma'lumot xavf ostida qolsin.
    """

    delay = FIRST_BACKUP_DELAY_SECONDS
    while True:
        await asyncio.sleep(delay)
        delay = BACKUP_INTERVAL_SECONDS

        try:
            # Zaxira nusxa ham alohida ipda — butun bazani JSON qilib Telegram'ga
            # yuborish uzoq davom etadi va bot kutib qolmasligi kerak.
            await asyncio.to_thread(_send_backup_once)
        except Exception:
            logging.getLogger("backend").warning("Zaxira nusxa vazifasi bajarilmadi", exc_info=True)


_backup_lock = threading.Lock()  # bir vaqtda bitta nusxa (tugma va jadval ustma-ust tushmasin)


def _send_backup_once() -> None:
    if not _backup_lock.acquire(blocking=False):
        return
    session = SessionLocal()
    try:
        send_backup_to_admin(session)
    finally:
        session.close()
        _backup_lock.release()
        _release_memory_and_log()  # nusxa katta bo'lishi mumkin — bo'shagan xotira darhol qaytsin


# ========== 2) Statistika va PDF yordamchilari ==========


def best_attempts(db: Session, test: Test) -> list[tuple[Attempt, str]]:
    """
    Har bir qatnashchining shu testdagi ENG YAXSHI urinishini (urinish, ism) ko'rinishida qaytaradi.

    Qatnashchi Telegram foydalanuvchisi bo'lishi ham (ochiq test), o'qituvchi
    qo'lda kiritgan o'quvchi bo'lishi ham mumkin (yopiq test).
    """

    query = (
        select(Attempt, User)
        .outerjoin(User, User.id == Attempt.user_id)
        .where(Attempt.test_id == test.id, Attempt.status == "done")
        .order_by(Attempt.percent.desc(), Attempt.seconds)
    )

    best_by_participant: dict[str, tuple[Attempt, str]] = {}
    for attempt, user in db.execute(query).all():
        if user:
            key, name = f"user:{user.id}", user.full_name
        else:
            name = attempt.offline_name or "Noma'lum"
            key = f"offline:{name.lower()}"
        best_by_participant.setdefault(key, (attempt, name))

    return list(best_by_participant.values())


def _all_participants(db: Session, test: Test) -> list[tuple[Attempt, str]]:
    """
    Test sahifasidagi "O'quvchilar" ro'yxati: natijasi borlar ham,
    o'qituvchi qo'shgan-u javoblari hali kiritilmaganlar ham.
    """

    query = (
        select(Attempt, User)
        .outerjoin(User, User.id == Attempt.user_id)
        .where(Attempt.test_id == test.id, Attempt.status.in_(("done", "pending")))
        .order_by(Attempt.id)
    )

    participants = []
    for attempt, user in db.execute(query).all():
        participants.append((attempt, user.full_name if user else (attempt.offline_name or "Noma'lum")))
    return participants


def _ensure_sheet_numbers(db: Session, test: Test) -> None:
    """
    O'qituvchi qo'shgan o'quvchilarga varaq raqami (ID) — hali yo'q bo'lsa, qo'shilish tartibida, ro'yxatdagi
    OXIRGI raqamdan keyingisi: 1, 2, 3 …; imtihonga sinf biriktirilgan bo'lsa (1001–1057) — 1058.
    Maktab o'quvchilarining ID'lari band — skaner varaqni ID bo'yicha taniydi, boshqa o'quvchiga tushmasin.
    """

    offline = db.scalars(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done")))
        .order_by(Attempt.id)
    ).all()
    missing = [attempt for attempt in offline if not attempt.sheet_number]
    if not missing:
        return
    taken = {attempt.sheet_number for attempt in offline if attempt.sheet_number}
    reserved: set[int] = set()
    class_student_ids = {attempt.class_student_id for attempt in offline if attempt.class_student_id}
    if class_student_ids:
        schools = (select(SchoolClass.school_id).join(ClassStudent, ClassStudent.class_id == SchoolClass.id)
                   .where(ClassStudent.id.in_(class_student_ids)))
        reserved = set(db.scalars(
            select(ClassStudent.student_code).join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
            .where(SchoolClass.school_id.in_(schools), ClassStudent.student_code.is_not(None),
                   ClassStudent.removed_at.is_(None))
        ).all())
    next_number = max(taken, default=0) + 1
    for attempt in missing:
        while next_number in taken or next_number in reserved:
            next_number += 1
        if next_number > 9999:  # raqamlar tugagan — bo'shab qolgan eng kichigi
            next_number = next((number for number in range(1, 10000) if number not in taken and number not in reserved),
                               None)
            if next_number is None:
                break
        attempt.sheet_number = next_number
        taken.add(next_number)
    db.commit()


def _get_offline_attempt(db: Session, test: Test, attempt_id: int) -> Attempt:
    """O'qituvchi qo'shgan o'quvchi yozuvini oladi (o'quvchi o'zi ishlaganini emas)."""

    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id or attempt.user_id is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    return attempt


def _group_key_questions(test: Test) -> list[list[KeyQuestion]]:
    """
    "36a" va "36b" kabi qismlarni bitta savol sifatida guruhlaydi.

    O'quvchi uchun bu bitta savol (36-savol, ikkita javob maydoni bilan),
    shuning uchun savollar soni ham shunga qarab sanaladi: 35 + 10 = 45.
    """

    groups: list[list[KeyQuestion]] = []
    current_base = None

    for key_question in test.key_questions:
        base = re.sub(r"[a-z]+$", "", key_question.label) if key_question.label else str(key_question.number)
        if base != current_base or not groups:
            groups.append([])
            current_base = base
        groups[-1].append(key_question)

    return groups


def _scored_groups(test: Test) -> list[tuple[str, list[int]]]:
    """
    Testning baholanadigan birliklari: (turi, savol havolalari) ko'rinishida.

    Turi — "closed" (variantli) yoki "written" (yozma).

    MUHIM: a/b qismli yozma savol BITTA birlik hisoblanadi va faqat ikkala
    qismi to'g'ri bo'lgandagina to'g'ri sanaladi — xuddi umumiy natija
    hisoblanganidek (qarang: _calculate_attempt_score). Shuning uchun
    "Yopiq 3/3 + Yozma 1/1" yig'indisi umumiy "4/4" ga teng chiqadi.
    """

    if test.mode != "key":
        return [("closed", [question.id]) for question in test.questions]

    groups = []
    for group in _group_key_questions(test):
        kind = "written" if group[0].question_type == "text" else "closed"
        groups.append((kind, [key_question.number for key_question in group]))

    return groups


def score_splits(db: Session, test: Test, attempts: list[Attempt]) -> dict[int, dict]:
    """
    Bir nechta urinishni yopiq va yozma savollar bo'yicha ajratib sanaydi.

    Hammasi BITTA so'rovda olinadi — har bir o'quvchi uchun alohida
    so'rov yuborilsa, ro'yxat sekin ochilardi.
    """

    groups = _scored_groups(test)
    totals = {"closed": 0, "written": 0}
    for kind, _ in groups:
        totals[kind] += 1

    splits = {
        attempt.id: {
            "closed_correct": 0,
            "closed_total": totals["closed"],
            "written_correct": 0,
            "written_total": totals["written"],
        }
        for attempt in attempts
    }

    if not splits:
        return splits

    correct_refs: dict[int, set[int]] = {attempt_id: set() for attempt_id in splits}
    rows = db.execute(
        select(StudentAnswer.attempt_id, StudentAnswer.question_ref)
        .where(StudentAnswer.attempt_id.in_(list(splits)), StudentAnswer.is_correct.is_(True))
    ).all()

    for attempt_id, question_ref in rows:
        correct_refs[attempt_id].add(question_ref)

    for attempt_id, answered in correct_refs.items():
        for kind, refs in groups:
            if refs and all(ref in answered for ref in refs):
                splits[attempt_id][f"{kind}_correct"] += 1

    return splits


def _key_question_label(key_question: KeyQuestion) -> str:
    """Savolning ko'rinadigan nomi: odatda "12-savol", a/b qismli bo'lsa — "36a", yozma ish bandi — "43.1"."""
    return band_label(key_question.test.subject, key_question.label) or f"{key_question.number}-savol"


def build_test_stats(db: Session, test: Test) -> dict:
    """
    O'qituvchi ko'radigan umumiy statistika.

    Savollar bo'yicha xatolar BITTA so'rovda yig'iladi — ilgari har bir savol
    uchun alohida so'rov ketardi va 55 savolli testda sahifa sekin ochilardi.
    """

    results = best_attempts(db, test)
    percents = [attempt.percent for attempt, _ in results]

    counts = {
        ref: (total, wrong or 0)
        for ref, total, wrong in db.execute(
            select(
                StudentAnswer.question_ref,
                func.count(),
                func.sum(case((StudentAnswer.is_correct.is_(False), 1), else_=0)),
            )
            .join(Attempt, Attempt.id == StudentAnswer.attempt_id)
            .where(Attempt.test_id == test.id, Attempt.status == "done")
            .group_by(StudentAnswer.question_ref)
        ).all()
    }

    if test.mode == "key":
        labelled = [(kq.number, _key_question_label(kq)) for kq in test.key_questions]
    else:
        labelled = [(question.id, question.text) for question in test.questions]

    question_stats = [
        {"text": label, "total": counts.get(ref, (0, 0))[0], "wrong": counts.get(ref, (0, 0))[1]}
        for ref, label in labelled
    ]

    return {
        "total": len(results),
        "avg": round(sum(percents) / len(percents), 1) if percents else 0,
        "max": max(percents, default=0),
        "min": min(percents, default=0),
        "avg_seconds": int(sum(a.seconds for a, _ in results) / len(results)) if results else 0,
        "questions": question_stats,
    }


# Esse (Ona tili 45): 0–24 ball bilan baholanadi (12 mezon × 0–2) va natijada 0–75 ga o'giriladi
# (ball / 24 × 75). Umumiy natija = (1–44 savollar bo'yicha Rasch balli + esse 0–75) / 2.
# Esse hali baholanmagan bo'lsa — umumiy ball o'rnida faqat test qismi (Rasch) turadi.
# So'zlar soniga qat'iy talab yo'q (2024-yildan) — qisqa esseni baholovchi o'zi past baholaydi.
ESSAY_SCALE = 75  # natijadagi esse shkalasi: ball / essay_max × 75 (essay_max=75 bilan yaratilganlarda — o'zi)
ESSAY_KEY = "essay"  # o'quvchi javoblaridagi kalit
ESSAY_MAX_CHARS = 10000


def _essay_ball(test: Test, attempt: Attempt) -> float | None:
    """Esse bali natija uchun 0–75 shkalada (0–24 ball / 24 × 75); baholanmagan — None."""
    if not test.essay_max or attempt.essay_score is None:
        return None
    return round(min(attempt.essay_score, test.essay_max) / test.essay_max * ESSAY_SCALE, 2)


CERTIFICATE_PERCENT_BASE = 65  # Milliy sertifikat foizi shu ballga nisbatan (A darajasi chegarasi)


def certificate_percent(ball: float | None) -> float | None:
    """Milliy sertifikatdagi foiz ko'rsatkichi: 61.11 ball → 94.02 %. 65 va undan yuqori — 100 %."""
    if ball is None:
        return None
    return round(min(100.0, ball / CERTIFICATE_PERCENT_BASE * 100), 2)


# ---------- DTM: sertifikati bor o'quvchilar ----------
#
# DTM'da sertifikati bor fanning bloki ishlanmaydi — o'rniga sertifikat bo'yicha ball qo'yiladi:
#   • majburiy fan (10 savol × 1,1) — har qanday sertifikat bilan 10 dan 10 (11 ball);
#   • asosiy fan (30 savol × 3,1 / 2,1) — foiz ko'rsatkichi bo'yicha: Milliy sertifikat — ball / 65 × 100
#     (ko'pi bilan 100 %), chet tili — C1 va B2 — 100 %, B1 — 75 %. Masalan, 57.90 ball → 89,08 % → 2-blokda 56,12.
# Sertifikat faqat shu test uchun: o'quvchi titulda belgilaydi yoki o'qituvchi natijada kiritadi.
# Belgilanmagan fan — javoblar bo'yicha hisoblanadi (o'quvchi profilida sertifikat saqlanmaydi:
# u o'quvchi ishlashni xohlagan testda ham o'zi qo'llanib qolardi).

DTM_LANG_PERCENT = {"C1": 100.0, "B2": 100.0, "B1": 75.0}
MS_BALL_MAX = 75.0
DTM_MANDATORY_WEIGHT = 1.1  # majburiy fan savolining bali


def _subject_key(name: str | None) -> str:
    text = (name or "").lower().replace("(majburiy)", "")
    for mark in "ʻʼ’‘`´":
        text = text.replace(mark, "'")
    return " ".join(text.split())


def _subject_matches(certificate_subject: str, block_name: str) -> bool:
    """Sertifikat fani bloknikiga mosmi: "Ona tili va adabiyot" ↔ "Ona tili", "Tarix" ↔ "O'zbekiston tarixi"."""
    a, b = _subject_key(certificate_subject), _subject_key(block_name)
    if not a or not b:
        return False
    if a == b:
        return True
    return min(len(a), len(b)) >= 4 and (a in b or b in a)


def _clean_certificate_value(kind: str, value: str | None) -> str | None:
    """Ball "57.9" → "57.90" (0–75), daraja "b2" → "B2"; yaroqsiz bo'lsa — None."""
    raw = (value or "").strip()
    if kind == "ms":
        try:
            ball = float(raw.replace(",", "."))
        except ValueError:
            return None
        return f"{ball:.2f}" if 0 <= ball <= MS_BALL_MAX else None
    if kind == "lang":
        level = raw.upper()
        return level if level in DTM_LANG_PERCENT else None
    return None


def _certificate_block_percent(kind: str, value: str | None, mandatory: bool) -> float | None:
    """Sertifikat blokka necha foiz beradi: majburiy fanda — doim 100, asosiy fanda — turi va qiymatiga qarab."""
    if mandatory:
        return 100.0
    if kind == "ms":
        try:
            return certificate_percent(float(value))
        except (TypeError, ValueError):
            return None
    if kind == "lang":
        return DTM_LANG_PERCENT.get((value or "").upper())
    return None


def _certificate_label(kind: str, value: str | None) -> str:
    if kind == "ms":
        return f"Milliy sertifikat: {value} ball"
    if kind == "lang":
        return f"Chet tili sertifikati: {value}"
    return "Sertifikat"


def _dtm_certificates_for(db: Session, attempts: list[Attempt], section_names: list[str],
                          mandatory: list[bool]) -> dict[int, dict[int, dict]]:
    """
    Har urinish uchun amaldagi sertifikatlar: {urinish: {blok: {kind, value, percent, label, source}}}.
    Faqat shu testniki: titulda belgilangani yoki o'qituvchi natijaga kiritgani.
    """

    result: dict[int, dict[int, dict]] = {}
    for attempt in attempts:
        blocks: dict[int, dict] = {}
        try:
            overrides = json.loads(attempt.dtm_certificates or "{}")
        except ValueError:
            overrides = {}
        for key, entry in overrides.items():
            index = int(key) if str(key).isdigit() else -1
            if not 0 <= index < len(section_names) or not isinstance(entry, dict):
                continue
            if entry.get("kind") == "none":
                blocks.pop(index, None)
                continue
            if entry.get("kind") == "claimed":
                # Titulda belgilangan: majburiy fanda — 10 dan 10; asosiy fanda — titulda bo'yalgan ball / daraja;
                # bo'yalmagan bo'lsa — bali noma'lum (o'qituvchi kiritadi), blok javoblar bo'yicha qoladi
                sheet_kind, sheet_value = entry.get("sheet_kind"), entry.get("sheet_value")
                sheet_percent = (_certificate_block_percent(sheet_kind, sheet_value, False)
                                 if sheet_kind in ("ms", "lang") and sheet_value else None)
                if mandatory[index]:
                    blocks[index] = {"kind": "claimed", "value": None, "percent": 100.0,
                                     "label": (f"Sertifikat (titulda: {_certificate_label(sheet_kind, sheet_value)})"
                                               if sheet_percent is not None else "Sertifikat (titulda «Ha»)"),
                                     "source": "titul"}
                elif sheet_percent is not None:
                    blocks[index] = {"kind": sheet_kind, "value": sheet_value, "percent": sheet_percent,
                                     "label": f"{_certificate_label(sheet_kind, sheet_value)} (titulda)",
                                     "source": "titul"}
                else:
                    blocks[index] = {"kind": "claimed", "value": None, "percent": None,
                                     "label": "Titulda «Ha», lekin sertifikat bali kiritilmagan", "source": "titul"}
                continue
            percent = _certificate_block_percent(entry.get("kind"), entry.get("value"), mandatory[index])
            if percent is not None:
                blocks[index] = {"kind": entry.get("kind"), "value": entry.get("value"), "percent": percent,
                                 "label": _certificate_label(entry.get("kind"), entry.get("value")), "source": "test"}
        if blocks:
            result[attempt.id] = blocks
    return result


def _essay_number(test: Test) -> int:
    """Esse raqami — oxirgi savoldan keyingisi (Ona tili: 44 ta savol → 45)."""
    numbers = [_item_number(label) or 0 for _, label in _rasch_items(test)]
    return max(numbers, default=0) + 1


def _rasch_items(test: Test) -> list[tuple[int, str]]:
    """
    Rasch modelidagi savollar: (javob havolasi, qisqa nomi).

    a/b qismli yozma savolning har bir qismi ALOHIDA savol hisoblanadi —
    Milliy sertifikatdagidek (36(a), 36(b)). Foiz esa avvalgidek savollar
    guruhi bo'yicha qoladi (qarang: _calculate_attempt_score).
    """

    if test.mode != "key":
        return [(question.id, str(index)) for index, question in enumerate(test.questions, start=1)]

    items = []
    for key_question in test.key_questions:
        label = key_question.label or str(key_question.number)
        shown = band_label(test.subject, label)
        if shown != label:  # yozma ish bandi — imtihondagidek "43.1"
            items.append((key_question.number, shown))
            continue
        match = re.fullmatch(r"(\d+)([a-z]+)", label)
        items.append((key_question.number, f"{match.group(1)}({match.group(2)})" if match else label))
    return items


MAX_SECTIONS = 8  # Fizikada 6 ta bo'lim bor


def parse_sections(text: str) -> list[dict]:
    """
    "Algebra: 1-20, 31-35" qatorlarini [{"name", "numbers"}] ga aylantiradi.
    Xato bo'lsa — tushunarli ValueError.
    """

    sections = []
    for line_number, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        if ":" not in line:
            raise ValueError(f"{line_number}-qator: «Nomi: 1-20, 31-35» ko'rinishida yozing")
        name, ranges = (part.strip() for part in line.split(":", 1))
        if not name or len(name) > 30:
            raise ValueError(f"{line_number}-qator: bo'lim nomi 1-30 belgi bo'lsin")
        numbers: list[int] = []
        for chunk in re.split(r"[,\s]+", ranges):
            if not chunk:
                continue
            match = re.fullmatch(r"(\d{1,3})(?:-(\d{1,3}))?", chunk)
            if not match:
                raise ValueError(f"{line_number}-qator: «{chunk}» noto'g'ri (masalan 1-20 yoki 31)")
            low = int(match.group(1))
            high = int(match.group(2) or low)
            if high < low:
                raise ValueError(f"{line_number}-qator: «{chunk}» — boshi oxiridan katta")
            numbers += range(low, high + 1)
        if not numbers:
            raise ValueError(f"{line_number}-qator: savol raqamlarini yozing")
        sections.append({"name": name, "numbers": sorted(set(numbers))})
    if len(sections) > MAX_SECTIONS:
        raise ValueError(f"Ko'pi bilan {MAX_SECTIONS} ta bo'lim")
    return sections


def _default_sections(test: Test) -> list[dict]:
    """
    O'qituvchi bo'limlarni belgilamagan bo'lsa — fanning Milliy sertifikat
    bo'limlari (backend/subjects.py), test tuzilishi shablonga mos kelsa.
    """
    return default_sections(test.subject, _question_count(test))


def _test_sections(test: Test) -> list[dict]:
    # "" — hali belgilanmagan (shablon ishlaydi), "[]" — o'qituvchi ataylab "Bo'limsiz" tanlagan
    if not test.sections:
        return _default_sections(test)
    try:
        return json.loads(test.sections)
    except ValueError:
        return []


def sections_text(sections: list[dict]) -> str:
    """Saqlangan bo'limlarni qayta tahrirlash uchun matnga aylantiradi (1-20 kabi qisqartirib)."""
    lines = []
    for section in sections:
        numbers, parts, start = section["numbers"], [], None
        for index, number in enumerate(numbers):
            if start is None:
                start = number
            if index + 1 == len(numbers) or numbers[index + 1] != number + 1:
                parts.append(str(start) if start == number else f"{start}-{number}")
                start = None
        lines.append(f"{section['name']}: {', '.join(parts)}")
    return "\n".join(lines)


def _item_number(label: str) -> int | None:
    """Savol nomidan raqami: "12" → 12, "36(a)" → 36."""
    match = re.match(r"(\d+)", label)
    return int(match.group(1)) if match else None


def build_rasch_report(db: Session, test: Test) -> dict:
    """
    Test natijalari Rasch modeli bo'yicha: har bir qatnashchining balli,
    darajasi va o'rni, umumiy statistika va savollar bo'yicha to'g'ri javoblar
    ulushi. O'qituvchining natijalar sahifasi ham, PDF hisobot ham shundan oladi.

    Rasch balli qatnashchilarga nisbatan hisoblanadi: yangi o'quvchi qo'shilsa,
    savollar qiyinligi aniqlashadi va ballar biroz o'zgarishi mumkin.
    """

    results = best_attempts(db, test)
    items = _rasch_items(test)
    total_questions = _question_count(test)
    # Rash test — Rasch modeli (Milliy sertifikat uslubidagi ball va A+ … NC daraja);
    # oddiy test (ichki nomi "boshlangich") — faqat to'g'ri javoblar soni va foiz, bahosiz (o'qituvchi test turini tanlaydi)
    uses_rasch = test.test_type != "boshlangich"

    correct_refs: dict[int, set[int]] = {attempt.id: set() for attempt, _ in results}
    if correct_refs:
        for attempt_id, question_ref in db.execute(
            select(StudentAnswer.attempt_id, StudentAnswer.question_ref).where(
                StudentAnswer.attempt_id.in_(list(correct_refs)), StudentAnswer.is_correct.is_(True)
            )
        ).all():
            correct_refs[attempt_id].add(question_ref)

    matrix = [[1 if ref in correct_refs[attempt.id] else 0 for ref, _ in items] for attempt, _ in results]
    # Rasch — faqat savollar (1–44) bo'yicha. Esse Rasch'ga kirmaydi: umumiy ball = (Rasch + esse) / 2
    rasch = rasch_scores(matrix) if uses_rasch else None
    test_scores = rasch["scores"] if uses_rasch else [None] * len(results)
    essay_balls = [_essay_ball(test, attempt) for attempt, _ in results]
    scores = [
        round((test_score + essay) / 2, 2) if test_score is not None and essay is not None else test_score
        for test_score, essay in zip(test_scores, essay_balls)
    ]

    # Bo'limlar (Algebra / Geometriya ...): har savol qaysi bo'limda
    sections = _test_sections(test)
    section_names = [section["name"] for section in sections]
    item_section: list[str | None] = []
    for _, label in items:
        number = _item_number(label)
        item_section.append(next((section["name"] for section in sections if number in section["numbers"]), None))
    if sections and any(name is None for name in item_section):
        section_names.append("Boshqa")
        item_section = [name or "Boshqa" for name in item_section]
    if test.essay_max:
        # Esseli testda natija Milliy sertifikatdagidek: test bali, yozma ish (esse) bali, umumiy ball,
        # foiz ko'rsatkichi va daraja. Bo'limlar bo'yicha foizlar chiqarilmaydi
        section_names = []

    def split_sections(row_matrix: list[int]) -> dict:
        """Har bo'lim bo'yicha to'g'ri yechilgan savollar foizi (Algebra 60%, Geometriya 35% ...)."""
        result = {}
        for name in section_names:
            in_section = [solved for solved, sec in zip(row_matrix, item_section) if sec == name]
            result[name] = round(sum(in_section) / len(in_section) * 100, 1) if in_section else 0.0
        return result

    # DTM testi: har to'g'ri javob o'z fanining koeffitsienti bilan (1,1 / 3,1 / 2,1) — jami 189 ball
    weights = score_weights(test.subject, total_questions)
    item_weights = [weights.get(_item_number(label), 0.0) for _, label in items] if weights else None
    dtm_max = round(sum(weights.values()), 1) if weights else None

    block_columns = [[index for index, sec in enumerate(item_section) if sec == name] for name in section_names]
    block_max = [sum(item_weights[index] for index in columns) for columns in block_columns] if item_weights else []
    block_mandatory = [bool(columns) and all(item_weights[index] <= DTM_MANDATORY_WEIGHT + 1e-9 for index in columns)
                       for columns in block_columns] if item_weights else []
    certificates = (_dtm_certificates_for(db, [attempt for attempt, _ in results], section_names, block_mandatory)
                    if item_weights and section_names else {})

    def section_scores(row_matrix: list[int], attempt: Attempt) -> dict:
        """
        Har fan bo'yicha DTM bali va to'g'ri javoblar: {"Fizika": {"ball": 58.8, "correct": 28, "total": 30}}.
        Sertifikatli blokda — sertifikat bo'yicha ball ("correct" — unga teng javoblar soni, masalan 29.54).
        """
        result = {}
        attempt_certificates = certificates.get(attempt.id, {})
        for position, (name, columns) in enumerate(zip(section_names, block_columns)):
            certificate = attempt_certificates.get(position)
            missing = bool(certificate and certificate["percent"] is None)
            if missing:
                certificate = None
            if certificate:
                share = certificate["percent"] / 100
                result[name] = {"ball": round(block_max[position] * share, 2),
                                "correct": round(len(columns) * share, 2), "total": len(columns)}
            else:
                result[name] = {
                    "ball": round(sum(item_weights[index] for index in columns if row_matrix[index]), 1),
                    "correct": sum(row_matrix[index] for index in columns),
                    "total": len(columns),
                }
            result[name].update({"max": round(block_max[position], 2), "mandatory": block_mandatory[position],
                                 "certificate": certificate, "missing": missing})
        return result

    def dtm_ball(row_matrix: list[int], scores: dict) -> float | None:
        if not item_weights:
            return None
        if scores:  # bloklar bo'yicha (sertifikatli bloklar ham)
            return round(sum(score["ball"] for score in scores.values()), 2)
        return round(sum(w for solved, w in zip(row_matrix, item_weights) if solved), 1)

    dtm_scores = {attempt.id: section_scores(row_matrix, attempt) if item_weights else {}
                  for (attempt, _), row_matrix in zip(results, matrix)}

    rows = [
        {
            "attempt_id": attempt.id,
            "name": name,
            "correct": attempt.correct,
            "wrong": attempt.wrong,
            "total": total_questions,
            "percent": attempt.percent,
            "seconds": attempt.seconds,
            "ball": score,
            # Oddiy testda daraja ham, baho ham yo'q — to'g'ri javoblar soni va foiz yetarli
            "grade": grade_for(score) if uses_rasch else None,
            "sections": split_sections(row_matrix),
            # Milliy sertifikatdagi "Umumiy ballga nisbatan foiz ko'rsatkichi": ball / 65 × 100 (ko'pi bilan 100)
            "cert_percent": certificate_percent(score) if uses_rasch else None,
            "essay": attempt.essay_score,
            # Esseli testda: test qismi (Rasch, 1–44) va esse bali (0–75) alohida; "ball" — ularning o'rtachasi
            "test_ball": test_score if test.essay_max else None,
            "essay_ball": essay_ball,
            "essay_pending": bool(test.essay_max) and attempt.essay_score is None,
            "dtm_ball": dtm_ball(row_matrix, dtm_scores[attempt.id]),
            "section_scores": dtm_scores[attempt.id],
            # Rasmiy natijadagidek: "* Chet tili sertifikati: 93.00" — sertifikat bilan hisoblangan bloklar
            "dtm_certificates": [
                {"section": name, "label": score["certificate"]["label"], "ball": score["ball"],
                 "source": score["certificate"]["source"]}
                for name, score in dtm_scores[attempt.id].items() if score.get("certificate")
            ],
            "variant": attempt.variant,
        }
        for (attempt, name), score, test_score, essay_ball, row_matrix
        in zip(results, scores, test_scores, essay_balls, matrix)
    ]

    # O'rin: DTM testida — DTM bali, aks holda Rasch balli; teng natija — teng o'rin (1, 1, 3 ...)
    if weights:
        sort_key = lambda row: row["dtm_ball"]  # noqa: E731
        rows.sort(key=lambda row: (-row["dtm_ball"], -row["ball"], row["name"].lower()))
    else:
        sort_key = (lambda row: row["ball"]) if uses_rasch else (lambda row: (row["correct"], row["percent"]))
        rows.sort(key=lambda row: (-row["ball"] if uses_rasch else -row["correct"], -row["percent"], row["name"].lower()))
    for index, row in enumerate(rows):
        same_as_previous = index and sort_key(rows[index - 1]) == sort_key(row)
        row["rank"] = rows[index - 1]["rank"] if same_as_previous else index + 1

    participants = len(rows)
    item_stats = []
    for column, (ref, label) in enumerate(items):
        solved = sum(matrix_row[column] for matrix_row in matrix)
        item_stats.append(
            {
                "label": label,
                "correct": solved,
                "share": round(solved / participants * 100, 1) if participants else 0.0,
            }
        )

    top_count = sum(1 for row in rows if row["ball"] >= 65) if uses_rasch else 0
    best = rows[0] if rows else None

    return {
        "rows": rows,
        "items": item_stats,
        "uses_rasch": uses_rasch,
        "section_names": section_names,
        "essay_max": test.essay_max,
        "essay_pending": sum(1 for row in rows if row["essay_pending"]),
        "dtm_max": dtm_max,
        "summary": {
            "dtm_max": dtm_max,
            "max_dtm_ball": best["dtm_ball"] if best and weights else None,
            "avg_dtm_ball": round(sum(row["dtm_ball"] for row in rows) / participants, 1) if participants and weights else None,
            "participants": participants,
            "uses_rasch": uses_rasch,
            "avg_ball": round(sum(row["ball"] for row in rows) / participants, 2) if participants and uses_rasch else None,
            "max_ball": best["ball"] if best else None,
            "max_percent": max((row["percent"] for row in rows), default=0),
            "max_name": best["name"] if best else "",
            "top_count": top_count,
            "top_share": round(top_count / participants * 100, 1) if participants else 0,
            "avg_percent": round(sum(row["percent"] for row in rows) / participants, 1) if participants else 0,
        },
    }


# ---------- PDF hisobot (Milliy sertifikat natijalari uslubida) ----------

FONTS_DIR = Path(__file__).resolve().parent / "fonts"

# Kirill va o'zbek harflari (Комилжонов, oʻ, gʻ) uchun shrift. Standart
# Helvetica ularni chiza olmaydi — ilgari bunday ismlar PDF'da yo'qolib qolardi.
REPORT_FONT, REPORT_FONT_BOLD, REPORT_FONT_ITALIC = "Helvetica", "Helvetica-Bold", "Helvetica-Oblique"
try:
    pdfmetrics.registerFont(TTFont("ReportSerif", str(FONTS_DIR / "DejaVuSerif.ttf")))
    pdfmetrics.registerFont(TTFont("ReportSerif-Bold", str(FONTS_DIR / "DejaVuSerif-Bold.ttf")))
    pdfmetrics.registerFont(TTFont("ReportSerif-Italic", str(FONTS_DIR / "DejaVuSerif-Italic.ttf")))
    REPORT_FONT, REPORT_FONT_BOLD, REPORT_FONT_ITALIC = "ReportSerif", "ReportSerif-Bold", "ReportSerif-Italic"
except Exception:
    logging.getLogger("backend").warning("PDF shrifti topilmadi — Helvetica ishlatiladi", exc_info=True)

# Hisobot ranglari — Chittak brendi (nomi tarixiy: REPORT_BLUE endi to'q siyoh rang)
REPORT_BLUE = colors.HexColor("#0B1F2A")
REPORT_GREEN = colors.HexColor("#00C853")
REPORT_TABLE_HEAD = colors.HexColor("#00A844")
REPORT_TILE_BG = colors.HexColor("#F3FAF5")
REPORT_MUTED = colors.HexColor("#4F6670")
REPORT_GRID = colors.HexColor("#DDEFE2")
REPORT_SLOGAN = "Ilg'or ta'lim, tezkor natija"
SHARE_BANDS = [
    (70, colors.HexColor("#1e7b34"), "70% va yuqori"),
    (40, colors.HexColor("#2e75b6"), "40–70%"),
    (15, colors.HexColor("#ed7d31"), "15–40%"),
    (0, colors.HexColor("#c00000"), "15% dan past"),
]


def _share_color(share: float):
    for threshold, color, _ in SHARE_BANDS:
        if share >= threshold:
            return color
    return SHARE_BANDS[-1][1]


def _report_styles() -> dict:
    base = {"fontName": REPORT_FONT, "textColor": colors.HexColor("#111827"), "leading": 13, "fontSize": 9.5}
    return {
        "title": ParagraphStyle("title", **{**base, "fontName": REPORT_FONT_BOLD, "fontSize": 17, "leading": 21,
                                            "textColor": REPORT_BLUE}),
        "meta": ParagraphStyle("meta", **{**base, "fontSize": 9, "textColor": REPORT_MUTED}),
        "section": ParagraphStyle("section", **{**base, "fontName": REPORT_FONT_BOLD, "fontSize": 14, "leading": 18,
                                                "textColor": REPORT_BLUE}),
        "tile_label": ParagraphStyle("tile_label", **{**base, "fontSize": 7.5, "leading": 10, "textColor": REPORT_MUTED}),
        "tile_value": ParagraphStyle("tile_value", **{**base, "fontName": REPORT_FONT_BOLD, "fontSize": 19, "leading": 23}),
        "tile_note": ParagraphStyle("tile_note", **{**base, "fontName": REPORT_FONT_ITALIC, "fontSize": 7.5,
                                                    "leading": 10, "textColor": REPORT_MUTED}),
        "cell": ParagraphStyle("cell", **base),
        "cell_bold": ParagraphStyle("cell_bold", **{**base, "fontName": REPORT_FONT_BOLD}),
        "cell_muted": ParagraphStyle("cell_muted", **{**base, "fontSize": 8, "textColor": REPORT_MUTED,
                                                      "alignment": 2}),
        "hard": ParagraphStyle("hard", **{**base, "fontName": REPORT_FONT_BOLD, "fontSize": 11,
                                          "textColor": colors.HexColor("#c00000")}),
        "easy": ParagraphStyle("easy", **{**base, "fontName": REPORT_FONT_BOLD, "fontSize": 11,
                                          "textColor": colors.HexColor("#1e7b34")}),
    }


def _section_heading(text: str, styles: dict) -> list:
    return [
        Spacer(1, 10),
        Paragraph(xml_escape(text), styles["section"]),
        HRFlowable(width="100%", thickness=1.4, color=REPORT_GREEN, spaceBefore=3, spaceAfter=10),
    ]


def _stat_tiles(summary: dict, styles: dict, width: float):
    if summary.get("dtm_max"):
        dtm = lambda value: f"{value or 0:g}".replace(".", ",")  # noqa: E731
        tiles = [
            ("ISHTIROKCHILAR", f"{summary['participants']}", ""),
            ("O'RTACHA DTM BALI", dtm(summary.get("avg_dtm_ball")), f"{summary['dtm_max']:g} balldan"),
            ("ENG YUQORI", dtm(summary.get("max_dtm_ball")), summary["max_name"]),
            ("O'RTACHA FOIZ", f"{summary['avg_percent']}%", "to'g'ri javoblar"),
        ]
    elif summary.get("uses_rasch"):
        tiles = [
            ("ISHTIROKCHILAR", f"{summary['participants']}", ""),
            ("O'RTACHA BALL", f"{summary['avg_ball'] or 0:.2f}", f"o'rtacha foiz {summary['avg_percent']}%"),
            ("ENG YUQORI BALL", f"{summary['max_ball'] or 0:.2f}", summary["max_name"]),
            ("A+ VA A", f"{summary['top_count']} ta", f"ishtirokchilarning {summary['top_share']}%"),
        ]
    else:
        tiles = [
            ("ISHTIROKCHILAR", f"{summary['participants']}", ""),
            ("O'RTACHA FOIZ", f"{summary['avg_percent']}%", ""),
            ("ENG YUQORI", f"{summary['max_percent']}%", summary["max_name"]),
        ]

    gap = 8
    tile_width = (width - gap * 3) / 4
    cells, column_widths = [], []
    for index, (label, value, note) in enumerate(tiles):
        cells.append(
            [
                Paragraph(xml_escape(label), styles["tile_label"]),
                Paragraph(xml_escape(value), styles["tile_value"]),
                Paragraph(xml_escape(note) or "&nbsp;", styles["tile_note"]),
            ]
        )
        column_widths.append(tile_width)
        if index < len(tiles) - 1:
            cells.append("")
            column_widths.append(gap)

    table = Table([cells], colWidths=column_widths)
    commands = [("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 8),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]
    for column in range(0, len(cells), 2):
        commands += [
            ("BACKGROUND", (column, 0), (column, 0), REPORT_TILE_BG),
            ("LINEBEFORE", (column, 0), (column, 0), 3.5, REPORT_BLUE),
            ("LEFTPADDING", (column, 0), (column, 0), 10),
        ]
    table.setStyle(TableStyle(commands))
    return table


def _chart_text(x: float, y: float, text: str, size: float, color=None, anchor: str = "start") -> ChartString:
    """Diagrammadagi yozuv (ReportLab'ning String'i shriftni faqat xossa orqali qabul qiladi)."""
    label = ChartString(x, y, text)
    label.fontName = REPORT_FONT
    label.fontSize = size
    label.fillColor = color or REPORT_MUTED
    label.textAnchor = anchor
    return label


def _share_chart(items: list[dict], width: float) -> Drawing:
    """Savollar bo'yicha to'g'ri javoblar ulushi — rangli ustunlar."""

    height = 210
    left, bottom, top = 34, 34, 8
    plot_width = width - left - 4
    plot_height = height - bottom - top
    drawing = Drawing(width, height)

    for percent in (0, 25, 50, 75, 100):
        y = bottom + plot_height * percent / 100
        drawing.add(Line(left, y, left + plot_width, y, strokeColor=REPORT_GRID, strokeWidth=0.6))
        drawing.add(_chart_text(left - 5, y - 3, f"{percent}%", 7, anchor="end"))

    count = max(1, len(items))
    slot = plot_width / count
    bar_width = slot * 0.72
    label_size = 6 if count <= 60 else 4.5

    for index, item in enumerate(items):
        x = left + index * slot + (slot - bar_width) / 2
        bar_height = plot_height * item["share"] / 100
        drawing.add(Rect(x, bottom, bar_width, max(bar_height, 0.6), fillColor=_share_color(item["share"]),
                         strokeColor=None))

        number, _, part = item["label"].partition("(")
        center = x + bar_width / 2
        drawing.add(_chart_text(center, bottom - 10, number, label_size, anchor="middle"))
        if part:
            drawing.add(_chart_text(center, bottom - 18, f"({part}", label_size, anchor="middle"))

    return drawing


def _share_legend(width: float) -> Drawing:
    drawing = Drawing(width, 14)
    x = 0
    for _, color, label in SHARE_BANDS:
        drawing.add(Rect(x, 2, 10, 10, fillColor=color, strokeColor=None))
        drawing.add(_chart_text(x + 14, 4, label, 8))
        x += 14 + stringWidth(label, REPORT_FONT, 8) + 22
    return drawing


def _question_list(title: str, items: list[dict], title_style, styles: dict, width: float) -> list:
    rows = [
        [
            Paragraph(xml_escape(f"{item['label']}-savol"), styles["cell"]),
            Paragraph(f"{item['share']:.0f}%", styles["cell_bold"]),
            Paragraph(xml_escape(f"{item['correct']} ta ishtirokchi to'g'ri yechgan"), styles["cell_muted"]),
        ]
        for item in items
    ] or [[Paragraph("—", styles["cell"]), "", ""]]

    table = Table(rows, colWidths=[width * 0.27, width * 0.16, width * 0.57])
    table.setStyle(TableStyle([("TOPPADDING", (0, 0), (-1, -1), 2), ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
                               ("LEFTPADDING", (0, 0), (-1, -1), 0), ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    return [Paragraph(xml_escape(title), title_style),
            HRFlowable(width="100%", thickness=0.5, color=REPORT_GRID, spaceBefore=3, spaceAfter=5), table]


DTM_MAX_BALL = 189.0


def _dtm_section_titles(section_names: list[str]) -> list[str]:
    """DTM ustun sarlavhalari: "1-majburiy fan\nOna tili", "1-asosiy fan\nFizika" (5 ta bo'lim tartibida)."""
    titles = []
    for index, name in enumerate(section_names):
        subject = name.replace(" (majburiy)", "")
        if index < 3:
            titles.append(f"{index + 1}-majburiy fan\n{subject}")
        elif index < 5:
            kind = f"{index - 2}-asosiy fan"
            titles.append(kind if subject == kind else f"{kind}\n{subject}")
        else:
            titles.append(subject)
    return titles


def _dtm_cell(row: dict, name: str) -> str:
    score = (row.get("section_scores") or {}).get(name)
    if not score:
        return "—"
    detail = "(sertifikat)" if score.get("certificate") else f"({score['correct']}/{score['total']})"
    return f"{score['ball']:g}".replace(".", ",") + f"<br/>{detail}"


def _is_dtm(rows: list[dict]) -> bool:
    return bool(rows) and rows[0].get("dtm_ball") is not None


def _is_essay(rows: list[dict]) -> bool:
    """Esseli (Ona tili, Milliy sertifikat formati) test: natijada test va yozma ish bali alohida."""
    return bool(rows) and rows[0].get("test_ball") is not None


def _percent_text(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f}%"


def _essay_cells(row: dict) -> list[str]:
    """Esseli test qatori: test bali (Rasch, 1–44) va yozma ish bali (0–75; baholanmagan — "kutilmoqda")."""
    test_ball = f"{row['test_ball']:.2f}" if row.get("test_ball") is not None else "—"
    essay_ball = "kutilmoqda" if row.get("essay_ball") is None else f"{row['essay_ball']:g}"
    return [test_ball, essay_ball]


def _total_column(rows: list[dict], uses_rasch: bool) -> tuple:
    """Jadvalning "jami" ustuni: DTM testida — DTM bali, aks holda Rasch balli yoki foiz."""
    if _is_dtm(rows):
        return "DTM bali", lambda row: f"{row['dtm_ball']:g}".replace(".", ",")
    if uses_rasch:
        return "Ball", lambda row: f"{row['ball']:.2f}"
    return "Foiz", lambda row: f"{row['percent']}%"


def _results_table(rows: list[dict], styles: dict, width: float, uses_rasch: bool = True,
                   section_names: list[str] | None = None) -> Table:
    if section_names:
        return _section_results_table(rows, styles, width, uses_rasch, section_names)
    total_title, total_of = _total_column(rows, uses_rasch)
    header = ["O'rin", "F.I.Sh.", "To'g'ri", "Xato", "Foiz", total_title, "Daraja"] if uses_rasch else \
        ["O'rin", "F.I.Sh.", "To'g'ri", "Xato", "Foiz"]
    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in header]]
    for row in rows:
        data.append(
            [
                Paragraph(str(row["rank"]), styles["cell_bold"]),
                Paragraph(xml_escape(row["name"]), styles["cell"]),
                Paragraph(f"{row['correct']}/{row['total']}", styles["cell"]),
                Paragraph(str(row["wrong"]), styles["cell"]),
                Paragraph(f"{row['percent']}%", styles["cell"]),
                *([Paragraph(total_of(row), styles["cell_bold"]),
                   Paragraph(xml_escape(row["grade"]), styles["cell_bold"])] if uses_rasch else []),
            ]
        )

    column_widths = [0.08, 0.38, 0.11, 0.08, 0.11, 0.12, 0.12] if uses_rasch else [0.10, 0.52, 0.14, 0.10, 0.14]
    table = Table(data, colWidths=[width * part for part in column_widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD),
        ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for index in range(1, len(data)):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-1, index), REPORT_TILE_BG))
    table.setStyle(TableStyle(commands))
    return table


GRADE_FILL = {"A+": "#1e7b34", "A": "#2e8b47", "B+": "#2e75b6", "B": "#3d85c6", "C+": "#b8860b", "C": "#c99a2e",
              "5": "#1e7b34", "4": "#2e75b6", "3": "#b8860b", "2": "#c00000"}


def _section_results_table(rows: list[dict], styles: dict, width: float, uses_rasch: bool, section_names: list[str]) -> Table:
    """Milliy sertifikat natijalari uslubida: №, F.I.Sh., bo'limlar, Ball/Foiz, Baho."""

    total_title, total_of = _total_column(rows, uses_rasch)
    dtm = _is_dtm(rows)
    header = ["№", "F.I.Sh.", *(_dtm_section_titles(section_names) if dtm else section_names), total_title,
              *(["Baho"] if uses_rasch else [])]
    centered = ParagraphStyle("cell_center", parent=styles["cell"], alignment=1)
    data = [[Paragraph(f"<font color='white'><b>{xml_escape(text).replace(chr(10), '<br/>')}</b></font>",
                       centered if index != 1 else styles["cell"])
             for index, text in enumerate(header)]]
    for row in rows:
        values = [_dtm_cell(row, name) if dtm else f"{row['sections'].get(name, 0):g}%" for name in section_names]
        total = total_of(row)
        data.append([
            Paragraph(str(row["rank"]), centered),
            Paragraph(xml_escape(row["name"]), styles["cell"]),
            *[Paragraph(value, centered) for value in values],
            Paragraph(f"<b>{total}</b>", centered),
            *([Paragraph(f"<font color='white'><b>{xml_escape(row['grade'])}</b></font>", centered)] if uses_rasch else []),
        ])

    tail = 2 if uses_rasch else 1
    name_share = 0.40 if len(section_names) <= 2 else 0.30
    other = (1 - 0.07 - name_share) / (len(section_names) + tail)
    widths = [0.07, name_share, *([other] * (len(section_names) + tail))]
    table = Table(data, colWidths=[width * share for share in widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD),
        ("GRID", (0, 0), (-1, -1), 0.4, REPORT_GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (2, 0), (-1, -1), "CENTER"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]
    for index, row in enumerate(rows, start=1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-2 if uses_rasch else -1, index), REPORT_TILE_BG))
        if not uses_rasch:
            continue
        fill = GRADE_FILL.get(row["grade"])
        commands.append(("BACKGROUND", (-1, index), (-1, index), colors.HexColor(fill) if fill else REPORT_MUTED))
    table.setStyle(TableStyle(commands))
    return table


RASCH_GRADE_ORDER = ["A+", "A", "B+", "B", "C+", "C", "NC"]


REPORT_HEADER_HEIGHT = 64  # tepadagi sarlavha (logo + yashil chiziq) egallagan joy (pt)


class _BrandCanvas(pdf_canvas.Canvas):
    """
    Chittak hisobot sahifasi (oq, ixcham — printerda kam bo'yoq): tepada chapda logo va "Chittak",
    o'ngda tashkilot va hisobot turi, ostida yashil chiziq; pastda shior va "1 / 3".
    Jami sahifalar soni oxirida ma'lum bo'ladi.
    """

    org_text = ""   # o'ng tepada, 1-qator (maktab yoki test nomi)
    kind_text = ""  # o'ng tepada, 2-qator (masalan "Davomat hisoboti")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages = []

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def _draw_header(self) -> None:
        width, height = A4
        left, right = 40, width - 40
        top = height - 30
        self.saveState()
        try:
            self.drawImage(str(FRONTEND_DIR / "img" / "chittak-icon.png"), left, top - 26, 26, 26, mask="auto")
        except Exception:
            pass  # logo fayli bo'lmasa ham hisobot chiqaveradi
        self.setFillColor(REPORT_BLUE)
        self.setFont(REPORT_FONT_BOLD, 14)
        self.drawString(left + 33, top - 18, "Chittak")
        self.setFillColor(REPORT_MUTED)
        self.setFont(REPORT_FONT, 8)
        if self.org_text:
            org = self.org_text
            while stringWidth(org, REPORT_FONT, 8) > 320 and len(org) > 4:
                org = org[:-2].rstrip() + "…"
            self.drawRightString(right, top - 11, org)
        if self.kind_text:
            self.drawRightString(right, top - 22, self.kind_text)
        self.setStrokeColor(REPORT_GREEN)
        self.setLineWidth(1.6)
        self.line(left, top - 34, right, top - 34)
        self.restoreState()

    def _draw_footer(self, total: int) -> None:
        left, right = 40, A4[0] - 40
        self.saveState()
        self.setFillColor(REPORT_MUTED)
        self.setFont(REPORT_FONT, 7.5)
        self.drawString(left, 22, f"Chittak — {REPORT_SLOGAN}")
        self.drawRightString(right, 22, f"{self._pageNumber} / {total}")
        self.restoreState()

    def save(self):
        # Oddiy canvas bilan chizilganda (kalit, ID ro'yxati) oxirgi sahifa showPage'siz qoladi — u ham kirsin
        if self._code:
            self.showPage()
        total = len(self._saved_pages)
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_header()
            self._draw_footer(total)
            super().showPage()
        super().save()


def _brand_canvas(org_text: str, kind_text: str) -> type:
    return type("BrandCanvas", (_BrandCanvas,), {"org_text": org_text or "", "kind_text": kind_text or ""})


def _brand_document(buffer, title: str, side_margin: int = 40) -> SimpleDocTemplate:
    """Brend sahifasi uchun hujjat: tepada tasmaga, pastda imzoga joy qoldiriladi."""
    return SimpleDocTemplate(buffer, pagesize=A4, leftMargin=side_margin, rightMargin=side_margin,
                             topMargin=REPORT_HEADER_HEIGHT + 14, bottomMargin=48, title=title)


def _results_grid(rows: list[dict], styles: dict, width: float, uses_rasch: bool, section_names: list[str]) -> Table:
    """Mock natijalari uslubidagi jadval: №, Ism Familiya, bo'limlar (yoki To'g'ri/Foiz), Ball, rangli Baho."""

    centered = ParagraphStyle("grid_center", parent=styles["cell"], alignment=1)
    essay = _is_essay(rows)
    if essay:
        middle = ["Test bali", "Yozma ish\n(esse)"]
    elif section_names:
        middle = list(section_names)
    else:
        middle = ["To'g'ri"]
    total_title, total_of = _total_column(rows, uses_rasch)
    dtm = _is_dtm(rows) and bool(section_names)
    show_grade = uses_rasch and not dtm  # oddiy testda baho ustuni yo'q
    # Milliy sertifikat uslubidagi (Rasch) natijada foiz ko'rsatkichi: ball / 65 × 100 (65 dan yuqori — 100 %)
    cert = uses_rasch and not _is_dtm(rows)
    if essay:  # Milliy sertifikatdagidek: test, yozma ish, umumiy ball, foiz, daraja
        header = ["№", "Ism Familiya", *middle, "Umumiy\nball", "Foiz", "Daraja"]
    else:
        header = ["№", "Ism Familiya", *(_dtm_section_titles(middle) if dtm else middle),
                  "Jami ball" if dtm else total_title, *(["Foiz"] if cert else []), *(["Baho"] if show_grade else [])]
    # Ko'p bo'limda (Fizika — 6 ta) ustunlar tor: sarlavha kichikroq shriftda,
    # aks holda "Mexanika" kabi so'zlar o'rtasidan bo'linib ketardi
    many = len(middle) > 4
    head_center = ParagraphStyle("grid_head", parent=centered, fontSize=7, leading=8.5) if many else centered
    data = [[Paragraph(f"<font color='white'><b>{xml_escape(text).replace(chr(10), '<br/>')}</b></font>",
                       styles["cell"] if index == 1 else head_center)
             for index, text in enumerate(header)]]
    for row in rows:
        if essay:
            middle_values = _essay_cells(row)
        elif dtm:
            middle_values = [_dtm_cell(row, name) for name in section_names]
        elif section_names:
            middle_values = [f"{row['sections'].get(name, 0):g}%" for name in section_names]
        else:
            middle_values = [f"{row['correct']}/{row['total']}"]
        total = total_of(row)
        data.append([
            Paragraph(str(row["rank"]), centered),
            Paragraph(xml_escape(row["name"]), styles["cell"]),
            *[Paragraph(value, centered) for value in middle_values],
            Paragraph(f"<b>{total}</b>", centered),
            *([Paragraph(_percent_text(row.get("cert_percent")), centered)] if cert else []),
            *([Paragraph(f"<font color='white'><b>{xml_escape(row['grade'])}</b></font>", centered)] if show_grade else []),
        ])

    # Bo'limlar ko'p bo'lsa (Fizikada 6 ta) ustunlar torayadi — ism ustuniga kamida 24% qoladi
    tail = (2 if show_grade else 1) + (1 if cert else 0)
    other = min(0.13, (1 - 0.07 - 0.24) / (len(middle) + tail))
    name_share = 1 - 0.07 - other * (len(middle) + tail)
    widths = [0.07, name_share, *([other] * (len(middle) + tail))]
    table = Table(data, colWidths=[width * share for share in widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD),
        ("GRID", (0, 0), (-1, -1), 0.4, REPORT_GRID),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if many:
        commands += [("LEFTPADDING", (2, 0), (-1, -1), 2), ("RIGHTPADDING", (2, 0), (-1, -1), 2)]
    for index, row in enumerate(rows, start=1):
        if index % 2 == 0:
            commands.append(("BACKGROUND", (0, index), (-2 if show_grade else -1, index), REPORT_TILE_BG))
        if not show_grade:
            continue
        fill = GRADE_FILL.get(row["grade"])
        commands.append(("BACKGROUND", (-1, index), (-1, index), colors.HexColor(fill) if fill else REPORT_MUTED))
    table.setStyle(TableStyle(commands))
    return table


def _report_banner(test: Test, width: float, uses_rasch: bool, dtm_max: float | None = None) -> list:
    """Yuqoridagi kanal qatori va ko'k sarlavha bloki ("… NATIJALARI")."""

    elements = []
    if test.required_channel:
        line = ParagraphStyle("banner_line", fontName=REPORT_FONT_BOLD, fontSize=9.5, textColor=colors.HexColor("#c00000"))
        elements += [Paragraph(xml_escape(f"Telegram kanalimiz: {test.required_channel}"), line), Spacer(1, 6)]
    title_style = ParagraphStyle("banner_title", fontName=REPORT_FONT_BOLD, fontSize=17, leading=21,
                                 textColor=colors.white, alignment=1)
    subtitle_style = ParagraphStyle("banner_sub", fontName=REPORT_FONT_ITALIC, fontSize=8.5, leading=11,
                                    textColor=colors.HexColor("#B7CBBF"), alignment=1)
    if dtm_max:
        subtitle = f"DTM bali bo'yicha ({dtm_max:g} ball) · barcha ishtirokchilar"
    else:
        subtitle = ("Milliy sertifikat formatida · barcha ishtirokchilar" if uses_rasch
                    else "To'g'ri javoblar bo'yicha · barcha ishtirokchilar")
    banner = Table(
        [[Paragraph(xml_escape(f"{test.title.upper()} NATIJALARI"), title_style)],
         [Paragraph(xml_escape(f"{test.subject} · {datetime.now():%d.%m.%Y} · {subtitle}"), subtitle_style)]],
        colWidths=[width],
    )
    banner.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), REPORT_BLUE),
                                ("TOPPADDING", (0, 0), (-1, 0), 12), ("BOTTOMPADDING", (0, -1), (-1, -1), 10)]))
    return elements + [banner, Spacer(1, 14)]


def generate_test_report_pdf(db: Session, test: Test, teacher: Teacher, overview_only: bool = False) -> bytes:
    """
    Test natijalari (PDF) — mock natijalari uslubida: sarlavha bloki, umumiy
    natijalar jadvali, so'ng har bir daraja alohida (A+ olganlar, A olganlar …
    NC), oxirida statistika. Pullikda — Rasch balli, bepulda — foiz va baho.
    overview_only — faqat sarlavha va umumiy natijalar jadvali (natijalar rasmi uchun).
    """

    report = build_rasch_report(db, test)
    styles = _report_styles()
    uses_rasch = report["uses_rasch"]
    section_names = report["section_names"]
    rows = report["rows"]
    buffer = io.BytesIO()
    document = _brand_document(buffer, f"{test.title} — natijalar", side_margin=36)
    width = document.width
    big_heading = ParagraphStyle("big_heading", fontName=REPORT_FONT_BOLD, fontSize=20, leading=24, textColor=REPORT_BLUE)

    elements = _report_banner(test, width, uses_rasch, report.get("dtm_max"))
    elements += [Paragraph("Umumiy natijalar", big_heading), *_section_heading("Ishtirokchilar natijalari", styles)]
    if rows:
        elements.append(_results_grid(rows, styles, width, uses_rasch, section_names))
    else:
        elements.append(Paragraph("Hali hech kim testni yakunlamagan.", styles["cell"]))
    if overview_only:
        document.build(elements, canvasmaker=_brand_canvas(test.title, "Test natijalari"))
        return buffer.getvalue()

    # Har bir daraja alohida: A+ olganlar, A olganlar ... NC (DTM testida — yo'q: natija DTM bali)
    order = RASCH_GRADE_ORDER if uses_rasch and not report.get("dtm_max") else []
    for grade in order:
        group = [row for row in rows if row["grade"] == grade]
        if not group:
            continue
        if grade == "NC":
            title = f"NC — C darajasiga yetmaganlar ({len(group)} ta)"
        elif uses_rasch:
            title = f"{grade} olganlar ({len(group)} ta)"
        else:
            title = f"«{grade}» baho olganlar ({len(group)} ta)"
        # Guruhlar ketma-ket; joy kam qolsa (sarlavha + bir necha qator sig'masa) — yangi sahifa
        elements += [Spacer(1, 12), CondPageBreak(130)]
        elements += _section_heading(title, styles)
        elements.append(_results_grid(group, styles, width, uses_rasch, section_names))

    # Statistika — oxirida
    elements.append(PageBreak())
    elements += _section_heading("Umumiy statistika", styles)
    elements += [_stat_tiles(report["summary"], styles, width)]
    elements += _section_heading("Savollar bo'yicha to'g'ri javoblar ulushi", styles)
    elements += [_share_chart(report["items"], width), Spacer(1, 4), _share_legend(width), Spacer(1, 16)]
    hardest = sorted(report["items"], key=lambda item: (item["share"], item["label"]))[:8]
    easiest = sorted(report["items"], key=lambda item: (-item["share"], item["label"]))[:8]
    half = (width - 24) / 2
    lists = Table(
        [[_question_list("Eng qiyin savollar", hardest, styles["hard"], styles, half), "",
          _question_list("Eng oson savollar", easiest, styles["easy"], styles, half)]],
        colWidths=[half, 24, half],
    )
    lists.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(lists)
    footnote = (
        "Ball Rasch modeli bo'yicha hisoblangan (Milliy sertifikat uslubida): qiyin savol ko'proq ball beradi, "
        "o'rtacha ball ~50. Darajalar: A+ 70+, A 65+, B+ 60+, B 55+, C+ 50+, C 46+; 46 dan past — NC."
        if uses_rasch
        else "Natija to'g'ri javoblar soni va foizi bo'yicha."
    )
    elements += [Spacer(1, 10), Paragraph(xml_escape(f"{footnote} O'qituvchi: {teacher.user.full_name}."), styles["meta"])]

    document.build(elements, canvasmaker=_brand_canvas(test.title, "Test natijalari"))
    return buffer.getvalue()


# Test kodi qisqa va yozishga oson bo'lsin: 4 ta belgi.
# Chalkashadigan harf-raqamlar olib tashlangan (O va 0, I va 1, L va 1),
# shuning uchun o'quvchi kodni noto'g'ri yozib yubormaydi.
TEST_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TEST_CODE_LENGTH = 4

# Javoblar varaqasining o'lchamlari (punktlarda)
SHEET_MARGIN = 40
SHEET_BOTTOM_LIMIT = 56
# "O'quvchi raqami" doirachalari: radius, orasi, qatorlar orasi
SHEET_ID_RADIUS = 5.0
SHEET_ID_GAP = 16
SHEET_ID_ROW = 13

# Hamma savol iloji boricha BITTA varaqqa sig'ishi kerak. Shuning uchun bir
# nechta joylashuv tayyor turadi: savollar ko'paygan sari ustunlar ko'payadi va
# qatorlar ixchamlashadi. Birinchi mos keladigani tanlanadi.
# (ustunlar soni, qator balandligi, doiracha radiusi, doirachalar orasi, shrift)
SHEET_LAYOUTS = [
    (2, 24, 6.5, 26, 9),
    (2, 20, 6.0, 24, 9),
    (3, 20, 5.5, 20, 8),
    (3, 17, 5.0, 18, 8),
    (4, 16, 4.5, 15, 7),
    (4, 13, 4.0, 14, 7),
]


def _answer_sheet_items(test: Test, key_override: dict | None = None) -> list[tuple[str, str, int, str, int]]:
    """
    Javoblar varaqasi uchun: har bir savolning (nomi, turi, variantlar soni, to'g'ri javobi,
    javob kaliti — skanerlangan javob qaysi savolga yozilishi uchun).
    """

    if test.mode == "key":
        return [
            (band_label(test.subject, kq.label) or str(kq.number), kq.question_type, kq.option_count,
             (key_override or {}).get(kq.number, kq.correct), kq.number)
            for kq in test.key_questions
        ]
    return [
        (str(index), "single", 4, question.correct, question.id) for index, question in enumerate(test.questions, start=1)
    ]


def _group_similar_questions(items) -> list[list]:
    """
    Ketma-ket kelgan bir xil turdagi savollarni guruhlaydi.

    Masalan 1-32 to'rt variantli, 33-35 olti variantli, 36-45 yozma bo'lsa —
    uchta guruh chiqadi va har biri varaqada alohida bo'lim bo'lib ko'rinadi.
    """

    groups: list[list] = []
    for item in items:
        kind_and_count = (item[1] if item[1] == "text" else "letters", item[2])
        if groups and (groups[-1][0][1] if groups[-1][0][1] == "text" else "letters", groups[-1][0][2]) == kind_and_count:
            groups[-1].append(item)
        else:
            groups.append([item])
    return groups


def _group_label(band) -> str:
    """Bo'lim sarlavhasi, masalan "33-35 savollar (6 variantli)"."""

    first, last = band[0][0], band[-1][0]
    span = f"{first}-{last} savollar" if first != last else f"{first}-savol"

    kind, option_count = band[0][1], band[0][2]
    if kind == "text":
        return f"{span} (yozma)"
    if option_count != 4:
        return f"{span} ({option_count} variantli)"
    return span


# PDF'da ishlatiladigan standart shrift (Helvetica) matematik belgilarni
# chiza olmaydi, shuning uchun LaTeX javobni oddiy harflar bilan yozamiz:
# "2\sqrt{3}" -> "2sqrt(3)", "\frac{\pi}{5}" -> "(pi)/(5)"
_PLAIN_REPLACEMENTS = [
    ("\\left", ""),
    ("\\right", ""),
    ("\\placeholder{}", ""),
    ("\\exponentialE", "e"),
    ("\\imaginaryI", "i"),
    ("^{\\circ}", "°"),
    ("^\\circ", "°"),
    ("\\degree", "°"),
    ("\\sqrt", "sqrt"),
    ("\\pi", "pi"),
    ("\\times", "*"),
    ("\\cdot", "*"),
    ("\\div", ":"),
    ("\\infty", "inf"),
    ("\\pm", "+-"),
    ("√", "sqrt"),
    ("π", "pi"),
    ("×", "*"),
    ("÷", ":"),
    ("²", "^2"),
    ("³", "^3"),
    ("∞", "inf"),
    ("±", "+-"),
]


def _latex_to_plain(value: str) -> str:
    """LaTeX javobni PDF'ga yozish uchun oddiy matnga aylantiradi."""

    text = (value or "").strip()

    text = _expand_fractions(text)

    # Daraja va indeks: "x^{2}" -> "x^2"
    text = re.sub(r"([\^_])\{([^{}]*)\}", r"\1\2", text)

    for source, target in _PLAIN_REPLACEMENTS:
        text = text.replace(source, target)

    text = text.replace("{", "(").replace("}", ")").replace("\\", "")

    # Shrift chiza olmaydigan belgilar qolsa, ular "?" bo'lib ko'rinmasligi uchun olib tashlanadi
    return "".join(char for char in text if ord(char) < 256)


# 3-versiya titul: chetdagi tekislash belgilari va imtihon kodi (punktlarda)
SHEET_CORNER = 32           # burchak belgisi markazi chetdan
SHEET_CORNER_SIZE = 14      # burchak kvadrati
SHEET_EDGE_MARKS = 10       # chap va o'ng chetdagi belgilar soni (burchaklar orasida)
SHEET_EDGE_SIZE = 8
SHEET_CODE_BITS = 12        # pastki chetdagi imtihon kodi kataklari
SHEET_CODE_SIZE = 9


SHEET_SHARED_CODE = 0b101101011010  # hamma testda bir xil (2906)


def sheet_code(test: Test) -> int:
    """
    Titulning pastki chetidagi kod — HAMMA testda bir xil. Test o'chib ketsa, xuddi shunday
    yangi test yaratib, tarqatilgan varaqlarni unga skanerlash mumkin. Kod endi varaq qaysi
    imtihonniki ekanini tekshirmaydi — faqat varaq kadrda to'liq ko'ringanini bildiradi
    (kataklar o'qilmasa, surat qayta olinadi). Oldin chop etilgan (test raqamiga bog'liq kodli)
    titullar ham shu sababli istalgan testga o'qiladi.
    """
    return SHEET_SHARED_CODE


def _draw_sheet_marks(sheet, test: Test, width: float, height: float, bubbles: list | None, page: int,
                      edge_marks: int = SHEET_EDGE_MARKS) -> None:
    """
    3-versiya: burchaklarda kvadratlar, chap/o'ng chetda tekislash belgilari va pastda
    imtihon kodi. Skaner varaqni burchaklar bo'yicha tekislab, har qatorni eng yaqin
    chet belgisiga qarab aniqlashtiradi — egilgan yoki qiyshaygan qog'ozda ham adashmaydi.
    """

    half = SHEET_CORNER_SIZE / 2
    for x, y in [(SHEET_CORNER, height - SHEET_CORNER), (width - SHEET_CORNER, height - SHEET_CORNER),
                 (SHEET_CORNER, SHEET_CORNER), (width - SHEET_CORNER, SHEET_CORNER)]:
        sheet.rect(x - half, y - half, SHEET_CORNER_SIZE, SHEET_CORNER_SIZE, stroke=0, fill=1)

    step = (height - 2 * SHEET_CORNER) / (edge_marks + 1)
    edge_half = SHEET_EDGE_SIZE / 2
    for index in range(1, edge_marks + 1):
        y = SHEET_CORNER + index * step
        for side, x in (("left", SHEET_CORNER), ("right", width - SHEET_CORNER)):
            sheet.rect(x - edge_half, y - edge_half, SHEET_EDGE_SIZE, SHEET_EDGE_SIZE, stroke=0, fill=1)
            if bubbles is not None:
                bubbles.append({"page": page, "kind": "mark", "ref": side, "x": x, "y": y, "r": edge_half})

    code = sheet_code(test)
    first_x, last_x = SHEET_CORNER + 48, width - SHEET_CORNER - 48
    gap = (last_x - first_x) / (SHEET_CODE_BITS - 1)
    code_half = SHEET_CODE_SIZE / 2
    for bit in range(SHEET_CODE_BITS):
        x = first_x + bit * gap
        if code >> bit & 1:
            sheet.rect(x - code_half, SHEET_CORNER - code_half, SHEET_CODE_SIZE, SHEET_CODE_SIZE, stroke=0, fill=1)
        if bubbles is not None:
            bubbles.append({"page": page, "kind": "code", "ref": "code", "letter": str(bit),
                            "x": x, "y": SHEET_CORNER, "r": code_half})


def _draw_corner_marks(sheet, width: float, height: float, test: Test | None = None,
                       bubbles: list | None = None, page: int = 1) -> None:
    if test is not None and (test.sheet_version or 1) >= 3:
        _draw_sheet_marks(sheet, test, width, height, bubbles, page)
        return
    """Burchaklardagi qora nuqtalar — varaqni rasmga olganda chetlarini aniqlash uchun."""
    for x, y in [(32, height - 32), (width - 32, height - 32), (32, 32), (width - 32, 32)]:
        sheet.circle(x, y, 7, stroke=0, fill=1)


def _draw_sheet_header(sheet, test: Test, width: float, height: float, with_answers: bool,
                       student: tuple | None = None, bubbles: list | None = None,
                       variants: list[str] | None = None, key_variant: str | None = None) -> float:
    """
    Varaq sarlavhasini chizadi va savollar boshlanadigan y koordinatasini qaytaradi.

    Sarlavha ixcham: savollarga ko'proq joy qolishi uchun (hammasi bitta
    varaqqa sig'ishi kerak) test ma'lumotlari bitta qatorga joylashtiriladi.

    Yangi varaqlarda (sheet_version 2) ism ostida "O'quvchi raqami" doirachalari bor:
    skaner o'quvchini shu raqam bo'yicha o'zi taniydi. student = (raqam, ism) berilsa —
    ism yozib, raqam bo'yab qo'yilgan shaxsiy varaq chiqadi.
    """

    _draw_corner_marks(sheet, width, height, test, bubbles, 1)

    sheet.setFont("Helvetica-Bold", 15)
    sheet.drawCentredString(width / 2, height - 56, test.title)

    sheet.setFont("Helvetica", 9)
    sheet.drawCentredString(
        width / 2,
        height - 70,
        f"Test kodi: {test.code}   |   Fan: {test.subject}   |   Sana: {datetime.now():%d/%m/%Y}",
    )

    if with_answers:
        # Kalit varaqasi faqat o'qituvchi uchun — ism yozadigan katak kerak emas.
        sheet.setFont("Helvetica-Bold", 11)
        sheet.drawCentredString(width / 2, height - 88, f"JAVOBLAR KALITI — {key_variant} VARIANT" if key_variant else "JAVOBLAR KALITI")
        return height - 104

    # O'quvchi o'z ismini yozadigan maydon
    name_y = height - 100
    sheet.setFont("Helvetica", 9)
    sheet.drawString(SHEET_MARGIN, name_y + 6, "Ism va familiya:")
    sheet.rect(SHEET_MARGIN + 80, name_y, width - SHEET_MARGIN * 2 - 80, 20)
    if student:
        sheet.setFont(REPORT_FONT_BOLD, 11)
        sheet.drawString(SHEET_MARGIN + 86, name_y + 6, student[1][:60])

    if (test.sheet_version or 1) < 2:
        return name_y - 16

    id_width = 4 if test.sheet_version >= 5 else 3
    number_digits = f"{student[0]:0{id_width}d}" if student else None
    if test.sheet_version >= 4:
        id_bottom, hint_x, hint_top = _draw_vertical_id(sheet, name_y, number_digits, bubbles, id_width)
    else:
        id_bottom, hint_x, hint_top = _draw_horizontal_id(sheet, name_y, number_digits, bubbles)

    # Variant (A/B/C/D) — o'ng chetda; ismli varaqda bo'yab qo'yiladi
    if variants:
        student_variant = student[2] if student and len(student) > 2 else None
        variant_x = width - SHEET_MARGIN - (len(variants) - 1) * SHEET_ID_GAP - SHEET_ID_RADIUS
        sheet.setFont("Helvetica-Bold", 8)
        sheet.drawString(variant_x - SHEET_ID_RADIUS, name_y - 13, "Variant:")
        for index, name in enumerate(variants):
            x = variant_x + index * SHEET_ID_GAP
            sheet.setFont("Helvetica-Bold", 7)
            sheet.drawCentredString(x, name_y - 50, name)
            sheet.circle(x, name_y - 37, SHEET_ID_RADIUS + 1, stroke=1, fill=1 if student_variant == name else 0)
            if bubbles is not None:
                bubbles.append({"page": 1, "ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                                "x": x, "y": name_y - 37, "r": SHEET_ID_RADIUS + 1})

    if student:
        sheet.setFont("Helvetica-Bold", 12)
        sheet.drawString(hint_x, hint_top - 14, f"ID {number_digits}")
    else:
        sheet.setFont("Helvetica", 7)
        along = "har ustunda" if test.sheet_version >= 4 else "har qatorda"
        sheet.drawString(hint_x, hint_top, "ID raqamingizni o'qituvchidan oling,")
        sheet.drawString(hint_x, hint_top - 9, f"katakchalarga yozing va {along} bittadan bo'yang.")
        sheet.drawString(hint_x, hint_top - 18, "Masalan 1023 -> 1, 0, 2, 3" if id_width == 4 else "Masalan 7 -> 0, 0, 7;  23 -> 0, 2, 3")
    return id_bottom


SHEET_ID_COLUMN_GAP = 18
SHEET_ID_DIGIT_STEP = 12.5


def _draw_vertical_id(sheet, name_y: float, digits: str | None, bubbles: list | None,
                      columns: int = 3) -> tuple[float, float, float]:
    """
    O'quvchi ID (4–5-versiya): 3 yoki 4 ustun, har ustunda yuqoridan pastga 0–9.
    Tepada raqam yoziladigan katakchalar. (pastki chegara, izoh x, izoh y)
    """

    radius = SHEET_ID_RADIUS
    first_x = SHEET_MARGIN + 30
    sheet.setFont("Helvetica-Bold", 8)
    sheet.drawString(SHEET_MARGIN, name_y - 14, "O'quvchi ID:")
    for column in range(columns):
        x = first_x + column * SHEET_ID_COLUMN_GAP
        sheet.rect(x - 7, name_y - 36, 14, 14, stroke=1, fill=0)
        if digits:
            sheet.setFont("Helvetica-Bold", 10)
            sheet.drawCentredString(x, name_y - 32.5, digits[column])
    for digit in range(10):
        center_y = name_y - 48 - digit * SHEET_ID_DIGIT_STEP
        sheet.setFont("Helvetica-Bold", 7)
        sheet.drawCentredString(first_x - 16, center_y - 2.5, str(digit))
        for column in range(columns):
            x = first_x + column * SHEET_ID_COLUMN_GAP
            filled = digits is not None and int(digits[column]) == digit
            sheet.circle(x, center_y, radius, stroke=1, fill=1 if filled else 0)
            if bubbles is not None:
                bubbles.append({"page": 1, "ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                                "x": x, "y": center_y, "r": radius})
    bottom = name_y - 48 - 9 * SHEET_ID_DIGIT_STEP - radius - 12
    return bottom, first_x + (columns - 1) * SHEET_ID_COLUMN_GAP + 24, name_y - 50


def _draw_horizontal_id(sheet, name_y: float, number_digits: str | None, bubbles: list | None) -> tuple[float, float, float]:
    """O'quvchi raqami (2–3-versiya): 3 qator (yuzlik, o'nlik, birlik), har qatorda 0–9."""

    first_x = SHEET_MARGIN + 80 + SHEET_ID_RADIUS
    sheet.setFont("Helvetica-Bold", 7)
    for digit in range(10):
        sheet.drawCentredString(first_x + digit * SHEET_ID_GAP, name_y - 13, str(digit))
    sheet.setFont("Helvetica", 8)
    sheet.drawString(SHEET_MARGIN, name_y - 13, "O'quvchi raqami:")
    for row, place in enumerate(("yuzlik", "o'nlik", "birlik")):
        center_y = name_y - 24 - row * SHEET_ID_ROW
        sheet.setFont("Helvetica", 7)
        sheet.drawRightString(SHEET_MARGIN + 72, center_y - 2.5, place)
        for digit in range(10):
            filled = number_digits is not None and int(number_digits[row]) == digit
            x = first_x + digit * SHEET_ID_GAP
            sheet.circle(x, center_y, SHEET_ID_RADIUS, stroke=1, fill=1 if filled else 0)
            if bubbles is not None:
                bubbles.append({"page": 1, "ref": f"id{row}", "label": place, "kind": "id", "letter": str(digit),
                                "x": x, "y": center_y, "r": SHEET_ID_RADIUS})
    return name_y - 24 - 2 * SHEET_ID_ROW - 16, first_x + 10 * SHEET_ID_GAP + 6, name_y - 26


def _pack_sheet_cells(cells: list[dict], row_height: float, available_height: float) -> list[list[dict]]:
    """
    Kataklarni ustunlarga joylaydi: ustun to'lgach, keyingisiga o'tiladi.

    Har bir guruh sarlavhasi (masalan "36a-45b savollar (yozma)") ham bitta
    katak hisoblanadi, lekin u savol qatoridan balandroq joy oladi.
    """

    header_height = row_height + 16
    columns: list[list[dict]] = []
    current: list[dict] = []
    used = 0.0

    for cell in cells:
        cell_height = header_height if cell["kind"] == "header" else row_height

        if current and used + cell_height > available_height:
            columns.append(current)
            current, used = [], 0.0

        current.append(cell)
        used += cell_height

    if current:
        columns.append(current)
    return columns


def _choose_sheet_layout(cells: list[dict], available_height: float):
    """Hamma savol bitta sahifaga sig'adigan eng keng (eng o'qilishi oson) joylashuvni tanlaydi."""

    for layout in SHEET_LAYOUTS:
        columns = _pack_sheet_cells(cells, layout[1], available_height)
        if len(columns) <= layout[0]:
            return layout, columns

    # Hech biriga sig'masa — eng zichini olamiz, ortig'i keyingi sahifaga o'tadi
    densest = SHEET_LAYOUTS[-1]
    return densest, _pack_sheet_cells(cells, densest[1], available_height)


def _draw_sheet_column(sheet, column_cells, column_x, column_width, top_y, layout, with_answers,
                       bubbles: list | None = None, page: int = 1) -> None:
    """
    Bitta ustundagi sarlavha va savol qatorlarini chizadi.

    bubbles berilsa — har bir doirachaning joyi (sahifa, punktlarda) shu ro'yxatga
    yoziladi: skaner varaq rasmidan aynan shu nuqtalarni tekshiradi.
    """

    _, row_height, bubble_radius, bubble_gap, font_size = layout
    label_width = max(24.0, font_size * 3)
    bubble_x = column_x + label_width + bubble_radius

    y = top_y
    for cell in column_cells:
        if cell["kind"] == "header":
            group = cell["group"]
            sheet.setFont("Helvetica-Bold", font_size + 1)
            sheet.drawString(column_x, y - row_height + 4, _group_label(group))

            if group[0][1] != "text":
                sheet.setFont("Helvetica-Bold", font_size - 1)
                for option_index in range(group[0][2]):
                    sheet.drawCentredString(bubble_x + option_index * bubble_gap, y - row_height - 10, chr(65 + option_index))

            y -= row_height + 16
            continue

        label, question_type, options, correct, ref = cell["item"]
        center_y = y - row_height / 2

        sheet.setFont("Helvetica", font_size)
        sheet.drawRightString(column_x + label_width - 8, center_y - 3, f"{label}.")

        if question_type == "text":
            # Yozma javob uchun doiracha emas, yoziladigan katak
            box_width = column_width - label_width - 12
            sheet.rect(column_x + label_width, center_y - row_height / 2 + 2, box_width, row_height - 4)

            if with_answers:
                sheet.setFont("Helvetica-Bold", font_size - 1)
                answer_text = " / ".join(_latex_to_plain(variant) for variant in _split_accepted_variants(correct))
                sheet.drawString(column_x + label_width + 4, center_y - 3, answer_text[: int(box_width / 4)])
        else:
            correct_letters = set(correct.split(","))
            for option_index in range(options):
                letter = chr(65 + option_index)
                filled = with_answers and letter in correct_letters
                sheet.circle(
                    bubble_x + option_index * bubble_gap,
                    center_y,
                    bubble_radius,
                    stroke=1,
                    fill=1 if filled else 0,
                )
                if bubbles is not None:
                    bubbles.append({
                        "page": page, "ref": ref, "label": label, "kind": question_type, "letter": letter,
                        "x": bubble_x + option_index * bubble_gap, "y": center_y, "r": bubble_radius,
                    })

        y -= row_height


def _draw_answer_sheet(sheet, test: Test, with_answers: bool, bubbles: list | None, student: tuple | None,
                       variants: list[str] | None = None, key_override: dict | None = None,
                       key_variant: str | None = None) -> None:
    """Bitta to'liq varaq (bir yoki bir necha sahifa) — hamma savollar bilan."""

    width, height = A4

    # Har bir guruh: sarlavha katagi + savol kataklari
    cells: list[dict] = []
    for group in _group_similar_questions(_answer_sheet_items(test, key_override)):
        cells.append({"kind": "header", "group": group})
        cells.extend({"kind": "row", "item": item} for item in group)

    top_y = _draw_sheet_header(sheet, test, width, height, with_answers, student, bubbles, variants, key_variant)
    available_height = top_y - SHEET_BOTTOM_LIMIT

    layout, columns = _choose_sheet_layout(cells, available_height)
    columns_per_page = layout[0]
    column_width = (width - SHEET_MARGIN * 2) / columns_per_page

    page = 1
    for index, column_cells in enumerate(columns):
        position = index % columns_per_page

        if index and position == 0:
            # Sig'magan ustunlar keyingi sahifaga o'tadi
            sheet.showPage()
            page += 1
            _draw_corner_marks(sheet, width, height, test, bubbles, page)
            top_y = height - 70

        _draw_sheet_column(
            sheet,
            column_cells,
            SHEET_MARGIN + position * column_width,
            column_width,
            top_y,
            layout,
            with_answers,
            bubbles,
            page,
        )

    if not with_answers:
        sheet.setFont("Helvetica", 8)
        sheet.drawCentredString(width / 2, 44, "To'g'ri javob doirachasini to'liq bo'yang.")


# 6-versiya — ixcham titul: ism yoziladigan maydon va yozma savollar yo'q, faqat doirachalar
# (ID, variant, savollar). O'lchami savollar soniga qarab A4 ning 1/3, 1/2 qismi yoki to'liq A4 —
# bitta A4 ga 3 ta yoki 2 ta titul sig'adi (qog'oz tejaladi). Burchak va chet belgilari har
# titulning o'z chetida, shuning uchun qirqilgan titul ham, qirqilmagani ham o'qiladi.
COMPACT_HEIGHTS = (A4[1] / 3, A4[1] / 2, A4[1])
COMPACT_LEFT = 48
COMPACT_TOP = 58      # sarlavha ostidan (titul tepasidan, punktlarda)
COMPACT_BOTTOM = 46   # pastki imtihon kodi kataklari ustida
COMPACT_ROW = 15
COMPACT_RADIUS = 5.0
COMPACT_GAP = 15
COMPACT_DIGIT_STEP = 12.5
COMPACT_ID_GAP = 16
COMPACT_EDGE_STEP = (A4[1] - 2 * SHEET_CORNER) / (SHEET_EDGE_MARKS + 1)  # A4 dagidek oraliq


def _compact_plan(test: Test, variants: list[str] | None = None, key_override: dict | None = None) -> dict:
    """Ixcham titul: o'lchami, chet belgilari soni va savollarning sahifa/ustunlarga bo'linishi."""

    items = [item for item in _answer_sheet_items(test, key_override) if item[1] != "text"]
    width = A4[0]
    options = max((item[2] for item in items), default=4)
    column_width = 26 + options * COMPACT_GAP + 6
    id_right = COMPACT_LEFT + 18 + 3 * COMPACT_ID_GAP + COMPACT_RADIUS  # 4 ta ID ustuni
    questions_x = id_right + (30 if variants else 0) + 14
    right = width - COMPACT_LEFT

    def capacity(height: float, first: bool) -> tuple[int, int]:
        rows = int((height - COMPACT_TOP - COMPACT_BOTTOM - 12) // COMPACT_ROW)
        columns = int((right - (questions_x if first else COMPACT_LEFT)) // column_width)
        return rows, columns

    for height in COMPACT_HEIGHTS:
        rows, columns = capacity(height, True)
        if rows * columns >= len(items):
            break
    pages = []
    rest = items
    while rest or not pages:
        rows, columns = capacity(height, not pages)
        chunk, rest = rest[: rows * columns], rest[rows * columns:]
        # Ustunlar teng to'ldiriladi (25 ta savol 4 ustunda: 7, 7, 7, 4)
        per_column = max(1, math.ceil(len(chunk) / columns)) if not rest and not pages else rows
        pages.append([chunk[index: index + per_column] for index in range(0, len(chunk), per_column)])
    return {
        "width": width,
        "height": height,
        "edge_marks": max(2, round((height - 2 * SHEET_CORNER) / COMPACT_EDGE_STEP) - 1),
        "pages": pages,
        "column_width": column_width,
        "questions_x": questions_x,
        "id_right": id_right,
    }


def _sheet_geometry(test: Test, variants: list[str] | None = None) -> dict | None:
    """Skaner uchun titul o'lchami (ixcham titulda); oddiy A4 titulda — None."""

    if (test.sheet_version or 1) < 6:
        return None
    if test.sheet_version >= 8:
        # EvalBee uslubi: ism yoziladigan qator bor (natija ekranida rasmi ko'rinadi); 9-versiya o'lchami moslashuvchan
        plan = _evalbee_plan(test, variants)
        return {"width": plan["width"], "height": plan["height"], "edge_marks": plan["edge_marks"], "name_box": True,
                "name_area": list(plan["style"]["name_area"])}
    plan = (_grid_plan if test.sheet_version >= 7 else _compact_plan)(test, variants)
    return {"width": plan["width"], "height": plan["height"], "edge_marks": plan["edge_marks"], "name_box": False}


def _draw_compact_card(sheet, test: Test, plan: dict, page: int, with_answers: bool, student: tuple | None,
                       bubbles: list | None, variants: list[str] | None, key_variant: str | None) -> None:
    """Ixcham titulning bitta sahifasi — (0, 0) titulning chap-past burchagi."""

    width, height = plan["width"], plan["height"]

    def top(distance: float) -> float:
        """Titul tepasidan sanalgan masofa → PDF koordinatasi (pastdan)."""
        return height - distance

    def note(item: dict) -> None:
        if bubbles is not None:
            bubbles.append({"page": page, **item})

    _draw_sheet_marks(sheet, test, width, height, bubbles, page, plan["edge_marks"])

    # Sarlavha — bitta qator
    sheet.setFont(REPORT_FONT_BOLD, 9)
    sheet.drawString(COMPACT_LEFT, top(47), test.title[:48] + (f" — {page}-sahifa" if page > 1 else ""))
    sheet.setFont(REPORT_FONT, 8)
    if with_answers:
        info = f"JAVOBLAR KALITI{f' — {key_variant} variant' if key_variant else ''}  ·  Kod {test.code}"
    elif student:
        info = f"{student[1][:40]}  ·  Kod {test.code}"
    else:
        info = f"Kod {test.code}  ·  {test.subject}  ·  doirachani to'liq bo'yang"
    sheet.drawRightString(width - COMPACT_LEFT, top(47), info)

    rows_top = COMPACT_TOP + 12  # birinchi qator tepasi
    if page == 1:
        # O'quvchi ID: 4 ustun, har ustunda 0–9; tepada raqam yoziladigan katakchalar
        digits = f"{student[0]:04d}" if student else None
        first_x = COMPACT_LEFT + 18
        sheet.setFont("Helvetica-Bold", 8)
        sheet.drawString(COMPACT_LEFT, top(COMPACT_TOP + 7), "ID")
        for column in range(4):
            x = first_x + column * COMPACT_ID_GAP
            sheet.rect(x - 6, top(COMPACT_TOP + 10), 12, 12, stroke=1, fill=0)
            if digits:
                sheet.setFont("Helvetica-Bold", 9)
                sheet.drawCentredString(x, top(COMPACT_TOP + 7), digits[column])
        for digit in range(10):
            center = top(rows_top + 6.5 + digit * COMPACT_DIGIT_STEP)
            sheet.setFont("Helvetica-Bold", 7)
            sheet.drawCentredString(first_x - 13, center - 2.5, str(digit))
            for column in range(4):
                x = first_x + column * COMPACT_ID_GAP
                filled = digits is not None and int(digits[column]) == digit
                sheet.circle(x, center, COMPACT_RADIUS, stroke=1, fill=1 if filled else 0)
                note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                      "x": x, "y": center, "r": COMPACT_RADIUS})
        if variants:
            # Variant — ID yonida ustun bo'lib (harfi doiracha o'ng tomonida)
            student_variant = student[2] if student and len(student) > 2 else None
            x = plan["id_right"] + 14
            sheet.setFont("Helvetica-Bold", 7)
            sheet.drawCentredString(x + 3, top(COMPACT_TOP + 7), "Var.")
            for index, name in enumerate(variants):
                center = top(rows_top + 6.5 + index * COMPACT_DIGIT_STEP)
                sheet.circle(x, center, COMPACT_RADIUS, stroke=1, fill=1 if student_variant == name else 0)
                sheet.drawString(x + COMPACT_RADIUS + 2, center - 2.5, name)
                note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                      "x": x, "y": center, "r": COMPACT_RADIUS})

    start_x = plan["questions_x"] if page == 1 else COMPACT_LEFT
    for index, column in enumerate(plan["pages"][page - 1]):
        column_x = start_x + index * plan["column_width"]
        bubble_x = column_x + 26 + COMPACT_RADIUS
        sheet.setFont("Helvetica-Bold", 7)
        for option in range(max(item[2] for item in column)):
            sheet.drawCentredString(bubble_x + option * COMPACT_GAP, top(COMPACT_TOP + 7), chr(65 + option))
        for row, (label, question_type, options, correct, ref) in enumerate(column):
            center = top(rows_top + (row + 0.5) * COMPACT_ROW)
            sheet.setFont("Helvetica", 8)
            sheet.drawRightString(column_x + 20, center - 3, f"{label}.")
            correct_letters = set((correct or "").split(","))
            for option in range(options):
                letter = chr(65 + option)
                x = bubble_x + option * COMPACT_GAP
                filled = with_answers and letter in correct_letters
                sheet.circle(x, center, COMPACT_RADIUS, stroke=1, fill=1 if filled else 0)
                note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                      "x": x, "y": center, "r": COMPACT_RADIUS})


# 7-versiya — bloklarga bo'lingan titul (EvalBee uslubida): har 5 savollik blok to'rt burchagidan
# kichik qora kvadratlar (anchor) bilan o'ralgan. Skaner har doirachani o'z blokidagi 4 kvadrat
# bo'yicha joylashtiradi — qog'oz egilgan, bukilgan yoki qiyshiq tutilganda ham doiracha joyi aniq.
# Ism maydoni yo'q (o'quvchi ID bilan taniladi). 50 tagacha savol — A4 ning 1/3 qismi (bitta A4 ga 3 ta),
# 100 tagacha — yarmi (2 ta).
GRID_LEFT = 44            # birinchi blok chizig'i (chet belgilaridan ichkarida)
GRID_TOP = 58             # birinchi qator kvadratlari (sarlavha ostida)
GRID_BOTTOM = 46          # pastki imtihon kodi ustida
GRID_ROW = 13             # savol qatorlari orasi
GRID_BLOCK_ROWS = 5       # blokdagi savollar
GRID_HEADER = 9           # blok tepasidagi A B C D qatori
GRID_BLOCK = GRID_HEADER + GRID_BLOCK_ROWS * GRID_ROW + 5  # blok balandligi (kvadratlar orasi) — 79
GRID_RADIUS = 4.5
GRID_GAP = 13             # doirachalar orasi (markazdan markazga)
GRID_ANCHOR = 7           # blok kvadrati tomoni


def _grid_plan(test: Test, variants: list[str] | None = None, key_override: dict | None = None) -> dict:
    """Bloklarga bo'lingan titul: o'lchami, ustunlar va har savolning bloki (ustun, blok qatori, qator)."""

    items = [item for item in _answer_sheet_items(test, key_override) if item[1] != "text"]
    width = A4[0]
    options = max((item[2] for item in items), default=4)
    column_width = 26 + options * GRID_GAP
    id_width = 32 + 4 * GRID_GAP + (24 if variants else 0)  # ID: raqam yorlig'i + 4 ustun (+ variant)
    right = width - GRID_LEFT

    def capacity(height: float, first: bool) -> tuple[int, int]:
        block_rows = int((height - GRID_TOP - GRID_BOTTOM) // GRID_BLOCK)
        start = GRID_LEFT + (id_width if first else 0)
        return block_rows, int((right - start) // column_width)

    for height in COMPACT_HEIGHTS:
        block_rows, columns = capacity(height, True)
        if block_rows * columns * GRID_BLOCK_ROWS >= len(items):
            break
    pages = []
    rest = items
    while rest or not pages:
        block_rows, columns = capacity(height, not pages)
        size = block_rows * columns * GRID_BLOCK_ROWS
        chunk, rest = rest[:size], rest[size:]
        # Ustun bo'ylab pastga: 1-ustunda 1–5, 6–10 ...; to'lgach keyingi ustun
        placed = []
        for index, item in enumerate(chunk):
            column, within = divmod(index, block_rows * GRID_BLOCK_ROWS)
            placed.append((item, column, within // GRID_BLOCK_ROWS, within % GRID_BLOCK_ROWS))
        pages.append(placed)
    return {
        "width": width,
        "height": height,
        "edge_marks": max(2, round((height - 2 * SHEET_CORNER) / COMPACT_EDGE_STEP) - 1),
        "pages": pages,
        "column_width": column_width,
        "id_width": id_width,
        "grid": True,
    }


def _draw_grid_card(sheet, test: Test, plan: dict, page: int, with_answers: bool, student: tuple | None,
                    bubbles: list | None, variants: list[str] | None, key_variant: str | None) -> None:
    """Bloklarga bo'lingan titulning bitta sahifasi — (0, 0) titulning chap-past burchagi."""

    width, height = plan["width"], plan["height"]
    column_width = plan["column_width"]
    questions_x = GRID_LEFT + (plan["id_width"] if page == 1 else 0)

    def top(distance: float) -> float:
        """Titul tepasidan sanalgan masofa → PDF koordinatasi (pastdan)."""
        return height - distance

    def note(item: dict) -> None:
        if bubbles is not None:
            bubbles.append({"page": page, **item})

    # Blok kvadratlari: chiziqlar kesishmasi (x — ustun chegarasi, y — blok qatori chegarasi)
    anchors: dict[tuple[float, int], str] = {}

    def anchor(x: float, row: int) -> str:
        key = (round(x, 2), row)
        if key not in anchors:
            anchors[key] = f"{len(anchors)}"
            y = top(GRID_TOP + row * GRID_BLOCK)
            sheet.rect(x - GRID_ANCHOR / 2, y - GRID_ANCHOR / 2, GRID_ANCHOR, GRID_ANCHOR, stroke=0, fill=1)
            note({"kind": "anchor", "ref": "anchor", "letter": anchors[key], "x": x, "y": y, "r": GRID_ANCHOR / 2})
        return anchors[key]

    def block(left: float, right_x: float, row: int) -> list[str]:
        """Blokning 4 kvadrati: chap-tepa, o'ng-tepa, o'ng-past, chap-past."""
        return [anchor(left, row), anchor(right_x, row), anchor(right_x, row + 1), anchor(left, row + 1)]

    def row_center(block_row: int, line: int) -> float:
        return top(GRID_TOP + block_row * GRID_BLOCK + GRID_HEADER + (line + 0.5) * GRID_ROW)

    _draw_sheet_marks(sheet, test, width, height, bubbles, page, plan["edge_marks"])

    # Sarlavha — bitta qator
    sheet.setFont(REPORT_FONT_BOLD, 9)
    sheet.drawString(GRID_LEFT, top(47), test.title[:48] + (f" — {page}-sahifa" if page > 1 else ""))
    sheet.setFont(REPORT_FONT, 8)
    if with_answers:
        info = f"JAVOBLAR KALITI{f' — {key_variant} variant' if key_variant else ''}  ·  Kod {test.code}"
    elif student:
        info = f"{student[1][:40]}  ·  Kod {test.code}"
    else:
        info = f"Kod {test.code}  ·  {test.subject}  ·  doirachani to'liq bo'yang"
    sheet.drawRightString(width - GRID_LEFT, top(47), info)

    if page == 1:
        # O'quvchi ID: 4 ustun; 0–4 birinchi blokda, 5–9 ikkinchisida (savol qatorlari bilan bir tekisda)
        digits = f"{student[0]:04d}" if student else None
        first_x = GRID_LEFT + 24 + GRID_RADIUS
        id_right = GRID_LEFT + plan["id_width"]
        for block_row in (0, 1):
            corners = block(GRID_LEFT, id_right, block_row)
            sheet.setFont("Helvetica-Bold", 7)
            if block_row == 0:
                sheet.drawString(GRID_LEFT + 7, top(GRID_TOP + 7.5), "ID")
                for column in range(4):
                    x = first_x + column * GRID_GAP
                    sheet.rect(x - 5, top(GRID_TOP + 8.5), 10, 8, stroke=1, fill=0)
                    if digits:
                        sheet.drawCentredString(x, top(GRID_TOP + 7), digits[column])
            for line in range(GRID_BLOCK_ROWS):
                digit = block_row * GRID_BLOCK_ROWS + line
                center = row_center(block_row, line)
                sheet.setFont("Helvetica-Bold", 7)
                sheet.drawCentredString(GRID_LEFT + 14, center - 2.5, str(digit))
                for column in range(4):
                    x = first_x + column * GRID_GAP
                    filled = digits is not None and int(digits[column]) == digit
                    sheet.circle(x, center, GRID_RADIUS, stroke=1, fill=1 if filled else 0)
                    note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                          "x": x, "y": center, "r": GRID_RADIUS, "block": corners})
            if variants and block_row == 0:
                # Variant — ID yonida ustun (harfi doiracha o'ng tomonida)
                student_variant = student[2] if student and len(student) > 2 else None
                x = first_x + 4 * GRID_GAP + 2
                sheet.setFont("Helvetica-Bold", 6)
                sheet.drawCentredString(x + 3, top(GRID_TOP + 7), "Var.")
                for line, name in enumerate(variants[:GRID_BLOCK_ROWS]):
                    center = row_center(0, line)
                    sheet.circle(x, center, GRID_RADIUS, stroke=1, fill=1 if student_variant == name else 0)
                    sheet.setFont("Helvetica-Bold", 7)
                    sheet.drawString(x + GRID_RADIUS + 1.5, center - 2.5, name)
                    note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                          "x": x, "y": center, "r": GRID_RADIUS, "block": corners})

    headers_drawn = set()
    for (label, question_type, options, correct, ref), column, block_row, line in plan["pages"][page - 1]:
        left = questions_x + column * column_width
        corners = block(left, left + column_width, block_row)
        bubble_x = left + 22 + GRID_RADIUS
        if (column, block_row) not in headers_drawn:
            headers_drawn.add((column, block_row))
            sheet.setFont("Helvetica-Bold", 6.5)
            for option in range(options):
                sheet.drawCentredString(bubble_x + option * GRID_GAP, top(GRID_TOP + block_row * GRID_BLOCK + 7.5),
                                        chr(65 + option))
        center = row_center(block_row, line)
        sheet.setFont("Helvetica", 7.5)
        sheet.drawRightString(left + 18, center - 2.7, f"{label}")
        correct_letters = set((correct or "").split(","))
        for option in range(options):
            letter = chr(65 + option)
            x = bubble_x + option * GRID_GAP
            filled = with_answers and letter in correct_letters
            sheet.circle(x, center, GRID_RADIUS, stroke=1, fill=1 if filled else 0)
            note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                  "x": x, "y": center, "r": GRID_RADIUS, "block": corners})


# 8–9-versiya — EvalBee titulidek: bitta varaqda bitta titul. Tepada "Ism-familiya / Imtihon / Sana"
# qutisi, chap ustunda ID, javoblar 5 savollik bloklarda; blok burchaklarida qora kvadratlar —
# skaner har doirachani o'z blokining 4 kvadrati bo'yicha joylashtiradi.
# 8-versiya: to'liq A4, 4 ustun, o'lchamlari qat'iy (oldin chop etilgan varaqlar uchun O'ZGARMAYDI).
# 9-versiya: moslashuvchan — savol kam bo'lsa titul kichik va tik, doirachalar kattaroq; ko'p bo'lsa
# kattaroq, doirachalar kichikroq (120 tagacha). Kvadratlar faqat savol bor bloklar atrofida;
# pastida qanday bo'yash / bo'yamaslik namunasi.
SHEET8_STYLE = {"edge_marks": 9, "row": 13, "header": 9, "radius": 5.5, "gap": 19, "label": 24,
                "number_font": 8.5, "letter_font": 7, "digit_font": 8, "box": (42, 70, 98), "box_font": 10,
                "sana_width": 120, "name_area": (118, 46, 540, 70), "instructions": False, "sparse": False,
                "columns": 4, "column_width": (A4[0] - 2 * SHEET_CORNER) / 4,
                "step": (A4[1] - 2 * SHEET_CORNER) / 10, "width": A4[0], "height": A4[1]}
SHEET9 = {"column": 120, "step": 78, "row": 13, "header": 9, "radius": 5.5, "gap": 19, "label": 24}
SHEET_LOGO = FRONTEND_DIR / "img" / "chittak-logo-wide.png"  # qush + "Chittak" yozuvi, shaffof fon
SHEET_LOGO_RATIO = 600 / 149
SHEET_LOGO_HEIGHT = 22.0  # ism qatoridagi logo balandligi (pt)
SHEET_NAME_MIN_ROOM = 220  # ism-familiyaga kamida shuncha joy qolsagina ism qatorida logo bo'ladi
SHEET9_BASE = SHEET9
DTM_TITLE_BAND = 20  # DTM titulida blok sarlavhasi shuncha balandroq — fan nomi (ikki qator) A B C D ustida sig'adi
SHEET9_MAX_SCALE = 1.3   # savol kam bo'lsa doirachalar shuncha kattalashadi (varaqning to'liq balandligida)
SHEET9_FILL_SCALE = 2.0  # undan ham katta — varaqni to'ldirguncha, faqat titul ostidagi Chittak belgisiga joy qolsa
SHEET_BRAND_SPACE = 58   # titul ostidagi Chittak belgisi uchun joy (_draw_sheet_brand: kartochkadan 28 pt past, chetdan 30)
SHEET9_MIN_SCALE = 0.7


def _adaptive_style(cells: int, reserved: int = 2, title_band: float = 0.0) -> dict:
    """9-versiya: cells ta blok (ID bilan) sig'adigan eng katta masshtab; tengida — kamroq katak, tikroq titul.
    reserved — 1-ustunda ID (va variant) uchun bloklar: qatorlar soni undan kam bo'lmaydi.
    title_band — blok sarlavhasiga qo'shimcha balandlik (DTM titulida fan nomi uchun)."""

    SHEET9_BASE = {**SHEET9, "header": SHEET9["header"] + title_band, "step": SHEET9["step"] + title_band}
    best = None
    for columns in range(2, 6):
        for rows in range(max(2, reserved), 12):
            if columns * rows < cells:
                continue
            by_width = (A4[0] - 2 * SHEET_CORNER) / (columns * SHEET9_BASE["column"])
            by_height = (A4[1] - 2 * SHEET_CORNER) / ((rows + 2) * SHEET9_BASE["step"])
            scale = min(by_width, by_height, SHEET9_MAX_SCALE)
            if scale >= SHEET9_MAX_SCALE:
                # Savol kam — titul A4 ni to'ldiradi (yonlarda va pastda bo'sh joy qolmasin), lekin ostidagi
                # Chittak belgisiga joy qoladi. Har titul baribir alohida varaqda chop etiladi (qog'oz ko'paymaydi)
                by_brand = (A4[1] - SHEET_BRAND_SPACE - 2 * SHEET_CORNER) / ((rows + 2) * SHEET9_BASE["step"])
                scale = max(SHEET9_MAX_SCALE, min(by_width, by_brand, SHEET9_FILL_SCALE))
            width = 2 * SHEET_CORNER + columns * SHEET9_BASE["column"] * scale
            height = 2 * SHEET_CORNER + (rows + 2) * SHEET9_BASE["step"] * scale
            key = (round(scale, 2), -columns * rows, -abs(height / width - 1.4))
            if best is None or key > best[0]:
                best = (key, columns, rows, scale, width, height)
    _, columns, rows, scale, width, height = best
    if scale < SHEET9_MIN_SCALE:  # juda ko'p savol — bir necha sahifa (masshtab kichraymaydi)
        columns, rows, scale = 4, 7, 1.0
        width = 2 * SHEET_CORNER + columns * SHEET9_BASE["column"]
        height = 2 * SHEET_CORNER + (rows + 2) * SHEET9_BASE["step"]
    step = SHEET9_BASE["step"] * scale
    column_width, gap = SHEET9_BASE["column"] * scale, SHEET9_BASE["gap"] * scale
    if scale >= SHEET9_MIN_SCALE and width < A4[0] - 1:
        # Tor titul (2–3 ustun) varaq kengligigacha cho'ziladi — yonlarda bo'sh joy qolmaydi;
        # doirachalar orasi o'zgarmaydi (zich, qatorni adashtirmaydi), qo'shimcha joy ustunlar orasiga ketadi
        column_width = (A4[0] - 2 * SHEET_CORNER) / columns
        width = A4[0]
    font = min(scale, 1.2)
    box_top, box_bottom = SHEET_CORNER + 12, SHEET_CORNER + step - max(8.0, 0.12 * step)
    box_middle = (box_top + box_bottom) / 2
    box_font = 10 * font
    # Ism-familiya qatorining o'ng tomonidagi Chittak logosi (qush + yozuv), qator balandligiga moslab
    # Logo hamma titulda bir xil kattalikda (DTM titulidagidek) — titul kattalashsa ham o'smaydi
    logo_height = max(8.0, min(SHEET_LOGO_HEIGHT, (box_middle - box_top) - 8))
    logo_width = logo_height * SHEET_LOGO_RATIO
    # Tor titulda (savollar kam — 2 ustun) ism uchun joy kam: logo ism qatoriga qo'yilmaydi
    # (titul ostidagi Chittak belgisi baribir bor) — uzun ism logoning ustiga chiqmasin
    name_left, name_right = SHEET_CORNER + 16 + 7.2 * box_font, width - SHEET_CORNER - 16
    show_logo = (name_right - name_left) - logo_width - 10 >= SHEET_NAME_MIN_ROOM
    return {
        "edge_marks": rows + 1, "step": step, "row": SHEET9_BASE["row"] * scale,
        "header": SHEET9_BASE["header"] * scale, "radius": SHEET9_BASE["radius"] * scale,
        "gap": gap, "label": SHEET9_BASE["label"] * scale,
        "number_font": 8.5 * scale, "letter_font": 7 * scale, "digit_font": 8 * scale,
        "box": (box_top, box_middle, box_bottom), "box_font": box_font,
        "sana_width": min(120 * font, (width - 2 * SHEET_CORNER - 32) * 0.3),
        "name_area": (name_left, box_top + 2, name_right - (logo_width + 10 if show_logo else 0), box_middle),
        "logo": (logo_width, logo_height) if show_logo else None,
        "instructions": True, "sparse": True, "columns": columns, "title_band": title_band * scale,
        "column_width": column_width, "width": width, "height": height, "scale": scale,
    }


# DTM titulida bloklar (5 savoldan) qayerda turadi: (ustun, blok qatori). 1-ustun tepasida — ID.
DTM_SHEET_BLOCKS = [(1, 0), (1, 1), (2, 0), (2, 1), (3, 0), (3, 1),  # majburiy fanlar: 1–10, 11–20, 21–30
                    (0, 2), (0, 3), (0, 4), (1, 2), (1, 3), (1, 4),  # 1-asosiy fan: 31–45, 46–60
                    (2, 2), (2, 3), (2, 4), (3, 2), (3, 3), (3, 4)]  # 2-asosiy fan: 61–75, 76–90


def _dtm_sheet_plan(test: Test, items: list, style: dict, reserved: int) -> dict | None:
    """
    DTM testi titulida savollar fanlar bo'yicha guruhlanadi (namunadagidek): tepada uchta majburiy fan
    yonma-yon, pastda ikkita asosiy fan (har biri ikki ustunda). Skaner to'ri o'zgarmaydi — 4 ustun ×
    5 blok qatori, faqat bloklar boshqa tartibda. Variantli testda (ID ostida variant) — oddiy tartib.
    """

    if reserved != 2 or not score_weights(test.subject, len(items)) or len(items) != 90:
        return None
    if style["columns"] != 4 or style["edge_marks"] - 1 != 5 or not style.get("title_band"):
        return None
    slots = [(item, *DTM_SHEET_BLOCKS[index // 5], index % 5) for index, item in enumerate(items)]
    names = [section["name"] for section in _test_sections(test)]
    if len(names) != 5:
        names = ["Ona tili", "Matematika", "O'zbekiston tarixi", "1-asosiy fan", "2-asosiy fan"]
    parts = [title.split("\n") + [""] for title in _dtm_section_titles(names)]
    # (ustun, blok qatori, nechta ustun kengligida, 1-qator (kichik), 2-qator (katta)); asosiy fan — bir qatorda
    titles = [(1, 0, 1, parts[0][0], parts[0][1]), (2, 0, 1, parts[1][0], parts[1][1]), (3, 0, 1, parts[2][0], parts[2][1]),
              (0, 2, 2, "", f"{parts[3][0]}: {parts[3][1]} (31–60)" if parts[3][1] else f"{parts[3][0]} (31–60)"),
              (2, 2, 2, "", f"{parts[4][0]}: {parts[4][1]} (61–90)" if parts[4][1] else f"{parts[4][0]} (61–90)")]
    return {"slots": slots, "titles": titles}


# 11-versiya — "uzluksiz" titul (EvalBee'ning Continuous ko'rinishi): savollar ustun bo'ylab bir xil qator
# oralig'ida uzluksiz ketadi, har 5 savolda "A B C D" qatori; doiracha o'lchami va oraliqlari savollar sonidan
# qat'i nazar bir xil. Kvadratlar savolga emas, to'rga bog'langan: har 5 qatorda, ustunlar chegarasida —
# skaner ilgarigidek har doirachani atrofidagi 4 kvadrat bo'yicha joylaydi.
SHEET11_SCALE = 1.1                   # yozuvlar (raqam, harf) o'lchami — eng kichik (120 savolli) titulda
SHEET11_PITCH = 15.0                  # doirachalar orasi — gorizontal ham, vertikal ham bir xil; eng kichigi
SHEET11_MAX_PITCH = 30.0              # savol kam bo'lsa titul A4 ni to'ldirguncha kattalashadi, lekin 2 baravardan oshmaydi
SHEET11_RADIUS_RATIO = 5.5 / 15.0     # doiracha radiusi / oraliq (diametri oraliqning ~73% i, namunadagidek)
SHEET11_COLUMN_PITCHES = 8            # ustun kengligi — 8 oraliq (namunadagidek, ustunlar bir-biriga yaqin)
SHEET11_MAX_BLOCKS = 8                # 5 qatorli oraliqlar soni (A4 bo'yiga sig'adigani)
SHEET11_MIN_RATIO = 1.2               # titul tik bo'lsin: skaner varaqni shakli bo'yicha ham tekshiradi
SHEET11_GROUP = 5                     # har 5 savolda "A B C D" qatori


def _continuous_fill(items: list, columns: int, rows: int, prefix: int, titles: dict[int, str]) -> list[list]:
    """
    Savollarni ustunlarga qatorma-qator joylaydi. 1-sahifaning 1-ustunida tepada ID uchun prefix qator band.
    "A B C D" qatori: ustun boshida, fan sarlavhasidan keyin, variantlar soni o'zgarganda va har 5 savolda
    (ustun boshidagi harflardan keyin hali 3 ta savol bo'lmagan bo'lsa — qo'yilmaydi, EvalBee'dagidek).
    Natija — sahifalar: [(tur, ustun, qator, ma'lumot)], tur: "title" | "letters" | "question".
    """

    pages, page = [], []
    column, row = 0, prefix
    since_label, last_options = None, None
    for index, item in enumerate(items):
        options, title = item[2], titles.get(index)
        while True:
            label = (row == 0 or title is not None or options != last_options or since_label is None
                     or (index % SHEET11_GROUP == 0 and since_label >= 3))
            need = (1 if title else 0) + (1 if label else 0) + 1
            # Yangi guruh ustun oxirida yolg'iz savol bilan qolmasin ("A B C D" ostida bitta savol) — keyingi ustunga
            orphan = label and row > 0 and index < len(items) - 1 and rows - row - need < 1
            if row + need <= rows and not orphan:
                break
            column, row = column + 1, 0
            if column == columns:
                pages.append(page)
                page, column = [], 0
        if title is not None:
            page.append(("title", column, row, title))
            row += 1
        if label:
            page.append(("letters", column, row, options))
            row += 1
            since_label = 0
        page.append(("question", column, row, item))
        row += 1
        since_label += 1
        last_options = options
    pages.append(page)
    return pages


def _continuous_pitch(columns: int, blocks: int) -> float:
    """
    Titulni A4 ga to'ldiradigan oraliq (zoom): enida ustunlar, bo'yida qatorlar sig'adigan eng kattasi;
    ostida Chittak belgisiga joy qoladi (sig'masa — titul butun A4 bo'yida, belgisiz).
    Kvadrat belgilar va imtihon kodi kattalashmaydi — skaner ularning o'lchamiga tayanadi.
    """

    by_width = (A4[0] - 2 * SHEET_CORNER) / (columns * SHEET11_COLUMN_PITCHES)
    rows = (blocks + 2) * SHEET11_GROUP
    pitch = min(SHEET11_MAX_PITCH, by_width, (A4[1] - SHEET_BRAND_SPACE - 2 * SHEET_CORNER) / rows)
    if pitch < SHEET11_PITCH:
        pitch = min(by_width, (A4[1] - 2 * SHEET_CORNER) / rows)
    return pitch


def _continuous_style(columns: int, blocks: int, pitch: float = SHEET11_PITCH) -> dict:
    """11-versiya titulining o'lchamlari: hammasi oraliqqa (pitch) mutanosib — titul bir tekis kattalashadi."""

    zoom = pitch / SHEET11_PITCH
    k = SHEET11_SCALE * zoom
    step = SHEET11_GROUP * pitch
    radius = SHEET11_RADIUS_RATIO * pitch
    column_width = SHEET11_COLUMN_PITCHES * pitch
    width = 2 * SHEET_CORNER + columns * column_width
    height = 2 * SHEET_CORNER + (blocks + 2) * step
    # Quti hamma titulda bir xil: "Ism-familiya … [Chittak]" / "Imtihon · Kod … | Sana"; yozuvlar titul
    # kattalashganda ham me'yorida (ism va imtihon nomiga joy qolsin)
    font = min(k, 1.25)
    # Quti birinchi kvadratlar qatoridan (ID kataklaridan) yuqorida tugaydi
    box_top, box_bottom = SHEET_CORNER + 4, SHEET_CORNER + step - radius - 8
    box_middle = (box_top + box_bottom) / 2
    box_font = 10 * font
    logo_height = max(8.0, min(SHEET_LOGO_HEIGHT, (box_middle - box_top) - 8))
    logo_width = logo_height * SHEET_LOGO_RATIO
    name_left, name_right = SHEET_CORNER + 16 + 7.2 * box_font, width - SHEET_CORNER - 16
    show_logo = (name_right - name_left) - logo_width - 10 >= 100  # ismga kamida 100 pt qolsa
    return {
        "edge_marks": blocks + 1, "step": step, "row": pitch, "header": 0.0,
        "radius": radius, "gap": pitch, "label": SHEET9["label"] * k,
        "number_font": 8.5 * k, "letter_font": 7 * k, "digit_font": 8 * k,
        "box": (box_top, box_middle, box_bottom), "box_font": box_font,
        "sana_width": min(120 * font, (width - 2 * SHEET_CORNER - 32) * 0.3),
        "name_area": (name_left, box_top + 2, name_right - (logo_width + 10 if show_logo else 0), box_middle),
        "logo": (logo_width, logo_height) if show_logo else None,
        "instructions": True, "sparse": True, "columns": columns, "title_band": 0.0,
        "column_width": column_width, "width": width, "height": height,
        "scale": k, "bubble_scale": k,
    }


# Yozma javoblar bandi (Milliy sertifikat 36–45): doirachalardan keyin, titulning butun enida —
# har ustunda bitta katak, har 5 qatorli oraliqda (kvadratlar orasida) 2 qator katak
SHEET12_LINES = 2
OFFLINE_MAX_OPTIONS = 6  # oflayn testda bir savolning variantlari: titul ustuniga 6 ta doiracha (A–F) sig'adi


def _continuous_plan(test: Test, items: list, variants: list[str] | None, written: list | None = None) -> dict:
    """
    11-versiya: savollar bitta sahifaga sig'adigan, titul tik bo'ladigan ustun/qator soni orasidan
    A4 ga eng katta (doirachalar eng yirik) chiqadigani; teng bo'lsa — kamroq ustunlisi.

    written — yozma savollar (36a, 36b …): titul pastida yozish kataklari bandi. Hammasi bitta
    sahifaga sig'magan bo'lsa — band chizilmaydi (javoblar oldingidek qo'lda kiritiladi).
    """

    prefix = 11 + (2 if variants else 0) + 1  # ID (yozish kataklari + 0–9), variant, bo'sh qator
    titles: dict[int, str] = {}  # fan sarlavhalari (hozircha ishlatilmaydi: DTM tituli eski ko'rinishda)

    def search(boxes: int):
        best = None
        for columns in (2, 3, 4):
            # Yozma kataklar: har qatorda ustunlar soncha; har oraliqda SHEET12_LINES qator
            band = math.ceil(math.ceil(boxes / columns) / SHEET12_LINES) if boxes else 0
            for blocks in range(2, SHEET11_MAX_BLOCKS + 1 - band):
                # Band bo'lsa — doirachalar oxirgi kvadratlar chizig'igacha bormaydi (kataklarga tegmasin)
                rows = blocks * SHEET11_GROUP + (0 if band else 1)
                if rows < prefix + 2:
                    continue
                pitch = _continuous_pitch(columns, blocks + band)
                if pitch < SHEET11_PITCH - 0.01:
                    continue
                style = _continuous_style(columns, blocks + band, pitch)
                if style["height"] / style["width"] < SHEET11_MIN_RATIO:
                    continue
                pages = _continuous_fill(items, columns, rows, prefix, titles)
                if len(pages) != 1:
                    continue
                key = (round(pitch, 2), -columns)
                if best is None or key > best[0]:
                    best = (key, style, pages, blocks, columns)
                break  # shu ustunlar sonida keyingilari balandroq — oraliq kichikroq
        return best

    best = search(len(written)) if written else None
    if best is None:
        written = []
        best = search(0)
    if best:
        _, style, pages, blocks, columns = best
    else:  # juda ko'p savol — 4 ustunli eng baland titul, bir necha sahifa
        style = _continuous_style(4, SHEET11_MAX_BLOCKS, _continuous_pitch(4, SHEET11_MAX_BLOCKS))
        pages = _continuous_fill(items, 4, SHEET11_MAX_BLOCKS * SHEET11_GROUP + 1, prefix, titles)
        blocks, columns = SHEET11_MAX_BLOCKS, 4
    return {"continuous": True, "width": style["width"], "height": style["height"], "edge_marks": style["edge_marks"],
            "pages": pages, "column_width": style["column_width"], "style": style, "reserved": 0, "prefix": prefix,
            # Yozma kataklar (1-sahifada): (savol, ustun, qator) — qatorma-qator, chapdan o'ngga
            "written": [(item, index % columns, index // columns) for index, item in enumerate(written or [])],
            "bubble_blocks": blocks}


def _evalbee_plan(test: Test, variants: list[str] | None = None, key_override: dict | None = None) -> dict:
    """8–9-versiya: blok qatorlari × ustunlar; 1-ustunning tepasida ID (va variant). 11-versiya — uzluksiz."""

    all_items = _answer_sheet_items(test, key_override)
    items = [item for item in all_items if item[1] != "text"]
    # DTM tituli (fanlar bo'yicha guruhlangan) o'zgarmaydi — 10-versiyadagidek chiziladi
    dtm_test = len(items) == 90 and bool(score_weights(test.subject, len(items)))
    if dtm_test and (test.sheet_version or 1) >= 13:
        # 13-versiya: DTM tituli rasmiy "Javoblar varaqasi" ko'rinishida (oldin chop etilganlari — o'z joylashuvida)
        plan = _dtm13_plan(test, items, variants)
        if plan:
            return plan
    if (test.sheet_version or 1) >= 15 and not dtm_test:
        # 15-versiya: Milliy sertifikat tuzilishidagi oflayn test — imtihondagidek 2 betlik titul
        plan = _ms15_plan(test, all_items, variants)
        if plan:
            return plan
    if (test.sheet_version or 1) >= 11 and not dtm_test:
        # Yozma savollar faqat oflayn testda titulga chiziladi (onlayn testda o'quvchi ilovada yozadi).
        # Faqat 12-versiyadan: oldin chop etilgan titullar o'z joylashuvida qoladi
        closed = getattr(test, "enrollment_type", None) == "closed"
        written = [item for item in all_items if item[1] == "text"] if closed and (test.sheet_version or 1) >= 12 else []
        plan = _continuous_plan(test, items, variants, written)
        plan["wide_rows"] = (test.sheet_version or 1) >= 12
        return plan
    reserved = 3 if variants else 2  # 1-ustunda ID (2 blok) va variant (1 blok)
    if (test.sheet_version or 1) >= 9:
        dtm_titles = reserved == 2 and len(items) == 90 and bool(score_weights(test.subject, len(items)))
        style = _adaptive_style(reserved + math.ceil(len(items) / 5), reserved, DTM_TITLE_BAND if dtm_titles else 0.0)
        # 10-versiya: ID raqamlari 0–9 oraliqsiz, tekis ketma-ket (9-versiyada 4 va 5 orasida bo'sh joy bor)
        style["id_tight"] = test.sheet_version >= 10
    else:
        style = SHEET8_STYLE
    block_rows = style["edge_marks"] - 1
    dtm = _dtm_sheet_plan(test, items, style, reserved)
    if dtm:
        return {"width": style["width"], "height": style["height"], "edge_marks": style["edge_marks"],
                "pages": [dtm["slots"]], "column_width": style["column_width"], "style": style, "reserved": reserved,
                "titles": dtm["titles"]}
    pages, rest = [], items
    while rest or not pages:
        slots = [(column, block_row) for column in range(style["columns"]) for block_row in range(block_rows)
                 if pages or column or block_row >= reserved]
        chunk, rest = rest[: len(slots) * 5], rest[len(slots) * 5:]
        pages.append([(item, *slots[index // 5], index % 5) for index, item in enumerate(chunk)])
    return {"width": style["width"], "height": style["height"], "edge_marks": style["edge_marks"], "pages": pages,
            "column_width": style["column_width"], "style": style, "reserved": reserved}


# ---------- 13-versiya: DTM tituli — rasmiy "Javoblar varaqasi" ko'rinishida ----------
#
# A4 ga to'liq: tepada sarlavha, ko'rsatmalar va F.I.O., bo'yash namunasi; uchta ustun (Majburiy fanlar 1–30,
# 1-asosiy fan 31–60, 2-asosiy fan 61–90), o'ngda variant va o'quvchi raqami, ism yoziladigan quti; pastda
# nazoratchilar dalolatnomasi. Ranglari rasmiy varaqdagidek (qizil chiziqlar, pushti ustunlar).
# Skaner belgilari (burchak kvadratlari, chap/o'ng chet belgilari, imtihon kodi) — boshqa titullardagidek qora:
# skaner varaqni ular bo'yicha tekislaydi. Pushti fon kulrangda qog'ozdek och — "bo'yalgan" bo'lib qolmaydi,
# qizil halqalar esa qora ko'rinadi (skaner har qatorni shu halqalar bo'yicha aniqlashtiradi).

DTM13_RED = colors.HexColor("#E0281E")
DTM13_PINK = colors.HexColor("#FAD2D2")
DTM13_BANNER = colors.HexColor("#F3A3A3")
DTM13_LETTER = colors.HexColor("#F2A5A5")  # doiracha ichidagi harf/raqam: ko'rinadi, lekin skaner uchun "qog'oz"
DTM13_PEN = colors.HexColor("#1F2A44")     # bo'yash namunasi (ruchka rangi)
DTM13_FRAME = 46.0                          # ramka chetdan (burchak va chet belgilari ramkadan tashqarida)
DTM13_ROW = 13.7                            # savollar qatori oralig'i
DTM13_RADIUS = 4.9
DTM13_PITCH = 13.6                          # A B C D doirachalari orasi
DTM13_COLUMN = 92.0                         # savollar ustuni kengligi
DTM13_COLUMN_GAP = 10.0
DTM13_TITLE_BOTTOM = 80.0                   # tepadan: sarlavha bandining pastki chizig'i
DTM13_INFO_BOTTOM = 168.0                   # ko'rsatmalar bandining pastki chizig'i (chap tomonda)
DTM13_HEAD_BOTTOM = 204.0                   # bo'yash namunasi bandining pastki chizig'i
DTM13_COLUMNS_TOP = 212.0
DTM13_COLUMN_HEADER = 32.0
DTM13_BANNER_HEIGHT = 24.0
DTM13_ACT_HEIGHT = 92.0
DTM13_SPLIT = 330.0                         # ko'rsatmalar | F.I.O. chegarasi (chapdan)

try:
    pdfmetrics.registerFont(TTFont("SheetSans", str(FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("SheetSans-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
    SHEET_SANS, SHEET_SANS_BOLD = "SheetSans", "SheetSans-Bold"
except Exception:
    SHEET_SANS, SHEET_SANS_BOLD = "Helvetica", "Helvetica-Bold"


def _dtm13_layout(certificates: bool = False) -> dict:
    """
    Titul qismlarining joyi (tepadan sanalgan, punktlarda) — chizish va skaner bir xil o'lchamni ishlatadi.
    certificates (14-versiya): o'ngdagi ism qutisi o'rnida "SERTIFIKAT" bo'limi, ism — tepadagi F.I.O. qutisiga.
    """

    width, height = A4
    frame_left, frame_right = DTM13_FRAME, width - DTM13_FRAME
    frame_bottom = height - DTM13_FRAME
    banner_top = frame_bottom - DTM13_ACT_HEIGHT - DTM13_BANNER_HEIGHT
    columns_bottom = banner_top - 10
    first_row = DTM13_COLUMNS_TOP + DTM13_COLUMN_HEADER + 6 + DTM13_ROW / 2  # 1-qator markazi
    column_x = [frame_left + 10 + index * (DTM13_COLUMN + DTM13_COLUMN_GAP) for index in range(3)]
    right_left = column_x[2] + DTM13_COLUMN + 12  # o'ng qism: variant, o'quvchi raqami, ism
    right_right = frame_right - 10
    side_top = DTM13_COLUMNS_TOP
    side_header = 34.0
    id_first = side_top + side_header + 18 + 6 + DTM13_ROW / 2  # o'quvchi raqami: yozish kataklari ostida
    side_bottom = id_first + 9.5 * DTM13_ROW + 6
    if certificates:
        cert_box = (side_bottom + 8, columns_bottom)
        name_box, lines = None, []
        # Bo'sh titulda ism tepadagi "Abituriyent F.I.O." qutisiga yoziladi (ismli titulda — bosilgan)
        name_area = (DTM13_SPLIT + 8, DTM13_TITLE_BOTTOM + 8, frame_right - 8, DTM13_HEAD_BOTTOM - 28)
    else:
        cert_box = None
        name_top = side_bottom + 10
        lines = [name_top + 58 + index * 34 for index in range(3)]  # familiya, ism, otasining ismi chiziqlari
        name_box = (right_left - 4, name_top, right_right + 4, columns_bottom)
        name_area = (right_left, name_top + 34, right_right, lines[1] + 4)
    return {
        "width": width, "height": height, "frame": (frame_left, DTM13_FRAME, frame_right, frame_bottom),
        "banner_top": banner_top, "columns_bottom": columns_bottom, "first_row": first_row,
        "column_x": column_x, "right": (right_left, right_right), "side": (side_top, side_bottom, side_header),
        "id_first": id_first, "name_box": name_box, "cert_box": cert_box, "name_lines": lines,
        # Skaner o'qituvchiga ko'rsatadigan ism rasmi: ism yoziladigan joy
        "name_area": name_area,
    }


LANGUAGE_SUBJECT_WORDS = ("ingliz", "nemis", "fransuz", "chet til", "english", "german", "french", "deutsch")


def _is_language_subject(name: str) -> bool:
    """Chet tili fani — sertifikati CEFR darajasida (C1/B2/B1), Milliy sertifikat bali emas."""

    text = (name or "").lower().replace("’", "'").replace("ʻ", "'")
    return any(word in text for word in LANGUAGE_SUBJECT_WORDS)


def _dtm13_plan(test: Test, items: list, variants: list[str] | None) -> dict | None:
    """DTM testi (90 savol, har biri ko'pi bilan 4 variantli) uchun 13-versiya titul rejasi; mos kelmasa — None."""

    if len(items) != 90 or any(item[2] > 4 for item in items) or (variants and len(variants) > 4):
        return None
    certificates = (test.sheet_version or 1) >= 14
    layout = _dtm13_layout(certificates)
    first_row, rows_room = layout["first_row"], layout["columns_bottom"] - 6 - (layout["first_row"] - DTM13_ROW / 2)
    if rows_room < 30 * DTM13_ROW:  # o'lchamlar o'zgarsa — titul sig'masligi testda darhol bilinadi
        raise RuntimeError("DTM titulida savollar ustuniga joy yetmadi")
    slots = [(item, index // 30, index % 30) for index, item in enumerate(items)]
    names = [section["name"] for section in _test_sections(test)]
    real_sections = len(names) == 5
    if not real_sections:
        names = ["Ona tili", "Matematika", "O'zbekiston tarixi", "1-asosiy fan", "2-asosiy fan"]
    ranges = ["1–10", "11–20", "21–30", "31–60", "61–90"]
    # 14-versiya: "SERTIFIKAT" bo'limi. Asosiy fan — o'quvchi Milliy sertifikat balini (4 raqam: 61.11), chet tili
    # bo'lsa — darajasini (C1/B2/B1) bo'yaydi; bo'yalgan bo'lsa — sertifikatdan foydalanadi. Majburiylar ichida shu fan
    # bo'lsa (Matematika 11–20 va 31–60) — ikkala blok ham shu bilan. Qolgan majburiy fanlar — "Ha / Yo'q".
    # Kalit — bloklar raqami ("1-3"), skaner shuni qaytaradi.
    cert_section = None
    if certificates and real_sections:
        main_groups = []
        for main_index in (3, 4):
            group = [index for index in range(3) if _subject_matches(names[index], names[main_index])] + [main_index]
            blocks = ", ".join(ranges[index] for index in group)
            main_groups.append({"label": f"{names[main_index]} ({blocks})", "name": names[main_index],
                                "blocks": f"({blocks})", "key": "-".join(str(index) for index in group),
                                "language": _is_language_subject(names[main_index])})
        covered = {int(part) for group in main_groups for part in group["key"].split("-")}
        mandatory_rows = [(f"{names[index].replace(' (majburiy)', '')} ({ranges[index]})", str(index))
                          for index in range(3) if index not in covered]
        cert_section = {"mandatory": mandatory_rows, "main": main_groups}
    return {
        "dtm13": True, "cert_section": cert_section, "width": layout["width"], "height": layout["height"], "edge_marks": SHEET_EDGE_MARKS,
        "pages": [slots], "column_width": DTM13_COLUMN, "reserved": 0, "layout": layout,
        "subjects": [name.replace(" (majburiy)", "") for name in names],
        "style": {"name_area": layout["name_area"]},
        "first_row": first_row,
    }


DTM14_CERT_ROW = 12.6        # sertifikat bo'limidagi qatorlar oralig'i (majburiy fanlar)
DTM14_CERT_RADIUS = 4.5
DTM14_DIGIT_ROW = 12.4       # ball raqamlari: 0–9 tepadan pastga (o'quvchi raqamidagidek)
DTM14_DIGIT_PITCH = 14.0     # ball ustunlari oralig'i


def _draw_dtm14_certificates(sheet, plan: dict, layout: dict, top, note, bubble) -> None:
    """
    "SERTIFIKAT" bo'limi (o'ngda, o'quvchi raqami ostida):
      • majburiy fan — "Ha / Yo'q" (Ha — 10 dan 10);
      • asosiy fan — Milliy sertifikat bali: 4 ustun × 0–9, tepadan pastga (masalan 6, 1, 1, 1 → 61.11);
      • chet tili — CEFR darajasi (C1 / B2 / B1), ustunda.
    Ikkala asosiy fan yonma-yon (chapda 1-asosiy, o'ngda 2-asosiy).
    Bo'yalmagan bo'lsa — sertifikatdan foydalanmaydi (javoblari hisoblanadi).
    """

    right_left, right_right = layout["right"]
    cert_top, cert_bottom = layout["cert_box"]
    box_left, box_right = right_left - 4, right_right + 4
    pitch, radius = DTM14_CERT_ROW, DTM14_CERT_RADIUS
    sheet.setStrokeColor(DTM13_RED)
    sheet.setLineWidth(0.9)
    sheet.rect(box_left, top(cert_bottom), box_right - box_left, cert_bottom - cert_top, stroke=1, fill=0)
    sheet.line(box_left, top(cert_top + 22), box_right, top(cert_top + 22))
    _dtm13_text(sheet, box_left + 5, top(cert_top + 10), "SERTIFIKAT / СЕРТИФИКАТ", 7.4, SHEET_SANS_BOLD, align="left")
    _dtm13_text(sheet, box_left + 5, top(cert_top + 19), "Faqat sertifikati borlar to'ldiradi", 5.8, align="left",
                room=box_right - box_left - 10)

    def ring(x: float, center: float, text: str, kind: str, ref: str, letter: str, label: str) -> None:
        sheet.setStrokeColor(DTM13_RED)
        sheet.setLineWidth(0.8)
        sheet.setFillColor(colors.white)
        sheet.circle(x, top(center), radius, stroke=1, fill=1)
        if text:
            sheet.setFillColor(DTM13_LETTER)
            sheet.setFont(SHEET_SANS, 5.2)
            sheet.drawCentredString(x, top(center) - 1.9, text)
        note({"ref": ref, "label": label, "kind": kind, "letter": letter, "x": x, "y": top(center), "r": radius})

    y = cert_top + 22
    section = plan["cert_section"]
    if section["mandatory"]:
        yes_x, no_x = box_right - 30, box_right - 11
        _dtm13_text(sheet, box_left + 5, top(y + 9), "Majburiy fan — sertifikatingiz bormi?", 6.0, SHEET_SANS_BOLD,
                    align="left", room=yes_x - box_left - 16)
        _dtm13_text(sheet, yes_x, top(y + 9), "Ha", 6.0, SHEET_SANS_BOLD)
        _dtm13_text(sheet, no_x, top(y + 9), "Yo'q", 6.0, SHEET_SANS_BOLD)
        y += 11
        for label, key in section["mandatory"]:
            center = y + pitch / 2
            _dtm13_text(sheet, box_left + 5, top(center) - 2.2, label, 6.2, align="left", color=colors.black,
                        room=yes_x - box_left - 16)
            ring(yes_x, center, "", "cert", f"cert{key}", "yes", label)
            ring(no_x, center, "", "cert", f"cert{key}", "no", label)
            y += pitch
        y += 3
        sheet.setStrokeColor(DTM13_RED)
        sheet.line(box_left, top(y), box_right, top(y))

    digit_row = DTM14_DIGIT_ROW
    if y + 12 + 39 + 10 * digit_row + 10 <= cert_bottom:  # 3 ta majburiy qator bo'lsa — sarlavhasiz
        _dtm13_text(sheet, (box_left + box_right) / 2, top(y + 9), "Asosiy fan — sertifikat bali yoki darajasi", 6.0,
                    SHEET_SANS_BOLD, room=box_right - box_left - 10)
        y += 12
    halves = [(box_left, (box_left + box_right) / 2), ((box_left + box_right) / 2, box_right)]
    bottoms = []
    for (half_left, half_right), group in zip(halves, section["main"]):
        middle = (half_left + half_right) / 2
        room = half_right - half_left - 8
        label, key = group["label"], group["key"]
        _dtm13_text(sheet, middle, top(y + 8), group["name"], 6.4, SHEET_SANS_BOLD, color=colors.black, room=room)
        _dtm13_text(sheet, middle, top(y + 16), group["blocks"], 5.8, color=colors.black, room=room)
        if group["language"]:
            # Chet tili: CEFR darajasi — ustunda C1, B2, B1
            _dtm13_text(sheet, middle, top(y + 29), "CEFR darajasi", 5.8, room=room)
            center = y + 33 + digit_row / 2 + 4
            for level in DTM_LANG_PERCENT:
                ring(middle, center, level, "certlevel", f"certlevel{key}", level, label)
                center += digit_row + 4
            bottoms.append(center - digit_row / 2)
            continue
        # Ball yoziladigan kataklar [ ][ ] , [ ][ ] va ularning ostida 4 ustun × 0–9
        columns = [middle + (index - 1.5) * DTM14_DIGIT_PITCH + (3 if index >= 2 else -3) for index in range(4)]
        cells_top = y + 21
        sheet.setStrokeColor(DTM13_RED)
        sheet.setLineWidth(0.6)
        sheet.setFillColor(colors.white)
        for x in columns:
            sheet.rect(x - 5.5, top(cells_top + 12), 11, 12, stroke=1, fill=1)
        _dtm13_text(sheet, middle, top(cells_top + 11), ",", 10, SHEET_SANS_BOLD)
        first = cells_top + 12 + 4 + digit_row / 2
        for index in (0, 2):  # pushti ustunlar — o'quvchi raqamidagidek
            sheet.setFillColor(DTM13_PINK)
            sheet.rect(columns[index] - radius - 1.5, top(first + 9.5 * digit_row), 2 * radius + 3, 10 * digit_row,
                       stroke=0, fill=1)
        for index, x in enumerate(columns):
            for digit in range(10):
                ring(x, first + digit * digit_row, str(digit), "certball", f"certball{key}#{index}", str(digit), label)
        bottoms.append(first + 9.5 * digit_row)
    if bottoms:
        sheet.setStrokeColor(DTM13_RED)
        sheet.setLineWidth(0.6)
        sheet.line(halves[0][1], top(y - 2), halves[0][1], top(max(bottoms) + 2))
        y = max(bottoms) + 2
    if y > cert_bottom + 0.5:  # o'lchamlar o'zgarsa — darhol bilinsin
        raise RuntimeError("DTM titulida sertifikat bo'limi sig'madi")
    if y + 9 < cert_bottom:
        _dtm13_text(sheet, (box_left + box_right) / 2, top(cert_bottom - 4.5),
                    "Bo'yalmagan fan — javoblari bo'yicha hisoblanadi", 5.6, room=box_right - box_left - 10)


def _dtm13_text(sheet, x: float, y: float, text: str, size: float, font: str | None = None,
                align: str = "center", color=None, room: float | None = None) -> None:
    """Matn: joyiga sig'masa shrifti kichrayadi (60% gacha), keyin "…" bilan qisqaradi."""

    font = font or SHEET_SANS
    if room:
        start = size
        while size > start * 0.6 and sheet.stringWidth(text, font, size) > room:
            size -= 0.25
        while len(text) > 4 and sheet.stringWidth(text, font, size) > room:
            text = text[:-2].rstrip() + "…"
    sheet.setFillColor(color or DTM13_RED)
    sheet.setFont(font, size)
    if align == "center":
        sheet.drawCentredString(x, y, text)
    elif align == "right":
        sheet.drawRightString(x, y, text)
    else:
        sheet.drawString(x, y, text)


def _draw_dtm13_card(sheet, test: Test, plan: dict, page: int, with_answers: bool, student: tuple | None,
                     bubbles: list | None, variants: list[str] | None, key_variant: str | None) -> None:
    """13-versiya DTM tituli (A4, (0, 0) — chap-past burchak)."""

    layout = plan["layout"]
    width, height = layout["width"], layout["height"]
    frame_left, frame_top, frame_right, frame_bottom = layout["frame"]

    def top(distance: float) -> float:
        return height - distance

    def note(item: dict) -> None:
        if bubbles is not None:
            bubbles.append({"page": page, **item})

    def bubble(x: float, center_top: float, text: str, filled: bool, letter_size: float = 5.6) -> None:
        y = top(center_top)
        sheet.setStrokeColor(DTM13_RED)
        sheet.setLineWidth(0.8)
        if filled:
            sheet.setFillColor(colors.black)
            sheet.circle(x, y, DTM13_RADIUS, stroke=1, fill=1)
            return
        sheet.setFillColor(colors.white)
        sheet.circle(x, y, DTM13_RADIUS, stroke=1, fill=1)
        sheet.setFillColor(DTM13_LETTER)
        sheet.setFont(SHEET_SANS, letter_size)
        sheet.drawCentredString(x, y - letter_size * 0.36, text)

    # Skaner belgilari: burchaklar, chap/o'ng chet belgilari va pastdagi imtihon kodi (qora)
    sheet.setFillColor(colors.black)
    _draw_sheet_marks(sheet, test, width, height, bubbles, page, plan["edge_marks"])

    sheet.saveState()
    sheet.setStrokeColor(DTM13_RED)
    sheet.setLineWidth(0.9)
    sheet.rect(frame_left, top(frame_bottom), frame_right - frame_left, frame_bottom - frame_top, stroke=1, fill=0)

    # 1) Sarlavha: logo va "JAVOBLAR VARAQASI"
    sheet.line(frame_left, top(DTM13_TITLE_BOTTOM), frame_right, top(DTM13_TITLE_BOTTOM))
    logo_height = 22.0
    try:
        sheet.drawImage(str(SHEET_LOGO), frame_left + 10, top(frame_top + (DTM13_TITLE_BOTTOM - frame_top + logo_height) / 2),
                        logo_height * SHEET_LOGO_RATIO, logo_height, mask="auto")
    except Exception:
        logging.getLogger("backend").warning("DTM titul logosi chizilmadi", exc_info=True)
    _dtm13_text(sheet, (frame_left + frame_right) / 2 + 40, top(DTM13_TITLE_BOTTOM - 11), "JAVOBLAR VARAQASI", 16,
                REPORT_FONT_BOLD)

    # 2) Ko'rsatmalar (chapda) va F.I.O. (o'ngda)
    split = DTM13_SPLIT
    sheet.setStrokeColor(DTM13_RED)
    sheet.line(frame_left, top(DTM13_HEAD_BOTTOM), frame_right, top(DTM13_HEAD_BOTTOM))
    sheet.line(split, top(DTM13_TITLE_BOTTOM), split, top(DTM13_HEAD_BOTTOM))
    sheet.line(frame_left, top(DTM13_INFO_BOTTOM), split, top(DTM13_INFO_BOTTOM))
    middle = (frame_left + split) / 2
    rules = [
        (DTM13_TITLE_BOTTOM + 13, "ABITURIYENT DIQQATIGA!", 7.6, "center"),
        (DTM13_TITLE_BOTTOM + 25, "1. SHARIKLI HAVORANG PASTALI RUCHKADAN FOYDALANING.", 6.3, "left"),
        (DTM13_TITLE_BOTTOM + 36, "2. HAR BIR TEST TOPSHIRIG'IDAGI JAVOBLARDAN FAQAT BITTASINI BELGILANG.", 6.3, "left"),
        (DTM13_TITLE_BOTTOM + 52, "ВНИМАНИЮ АБИТУРИЕНТА!", 7.6, "center"),
        (DTM13_TITLE_BOTTOM + 64, "1. ИСПОЛЬЗУЙТЕ СИНЮЮ ШАРИКОВУЮ РУЧКУ.", 6.3, "left"),
        (DTM13_TITLE_BOTTOM + 75, "2. К КАЖДОМУ ТЕСТОВОМУ ЗАДАНИЮ ОТМЕТЬТЕ ТОЛЬКО ОДИН ОТВЕТ.", 6.3, "left"),
    ]
    for distance, text, size, align in rules:
        x = middle if align == "center" else frame_left + 8
        _dtm13_text(sheet, x, top(distance), text, size, align=align, room=split - frame_left - 14)

    # Bo'yash namunasi: to'g'ri — to'liq bo'yalgan; noto'g'ri — nuqta, chala, chiziqli, X, halqa, belgi
    _dtm13_text(sheet, middle, top(DTM13_INFO_BOTTOM + 11), "DOIRACHANI BO'YASH NAMUNASI / ОБРАЗЕЦ ЗАКРАШИВАНИЯ КРУЖОЧКОВ",
                6.3, room=split - frame_left - 14)
    _dtm13_text(sheet, frame_left + 62, top(DTM13_INFO_BOTTOM + 21), "To'g'ri / Правильно", 5.6)
    _dtm13_text(sheet, frame_left + 190, top(DTM13_INFO_BOTTOM + 21), "Noto'g'ri / Неправильно", 5.6)
    sample_y, sample_r = top(DTM13_INFO_BOTTOM + 29), 4.2
    sheet.setStrokeColor(DTM13_PEN)
    sheet.setFillColor(DTM13_PEN)
    sheet.setLineWidth(0.8)
    sheet.circle(frame_left + 62, sample_y, sample_r, stroke=1, fill=1)
    for index in range(6):
        x = frame_left + 150 + index * 17
        sheet.setFillColor(colors.white)
        sheet.circle(x, sample_y, sample_r, stroke=1, fill=1)
        sheet.setFillColor(DTM13_PEN)
        if index == 0:
            sheet.circle(x, sample_y, 1.0, stroke=0, fill=1)
        elif index == 1:
            sheet.wedge(x - sample_r, sample_y - sample_r, x + sample_r, sample_y + sample_r, 90, 230, stroke=0, fill=1)
        elif index == 2:
            for shift in (-2.4, 0, 2.4):
                sheet.line(x + shift - 1.6, sample_y - 2.6, x + shift + 1.6, sample_y + 2.6)
        elif index == 3:
            sheet.line(x - 2.6, sample_y - 2.6, x + 2.6, sample_y + 2.6)
            sheet.line(x - 2.6, sample_y + 2.6, x + 2.6, sample_y - 2.6)
        elif index == 4:
            sheet.circle(x, sample_y, sample_r * 0.55, stroke=1, fill=0)
        else:
            path = sheet.beginPath()
            path.moveTo(x - 2.4, sample_y)
            path.lineTo(x - 0.6, sample_y - 2.2)
            path.lineTo(x + 2.8, sample_y + 2.6)
            sheet.drawPath(path, stroke=1, fill=0)

    # F.I.O.: ismli titulda — o'quvchi ismi, bo'shida — yozuv; pastida imtihon nomi va kodi
    name_center = (split + frame_right) / 2
    if student:
        _dtm13_text(sheet, name_center, top(DTM13_TITLE_BOTTOM + 52), student[1][:60], 15, REPORT_FONT_BOLD,
                    color=colors.black, room=frame_right - split - 20)
    elif not layout["name_box"]:
        _dtm13_text(sheet, name_center, top(DTM13_TITLE_BOTTOM + 14), "Abituriyent F. I. O. / Ф. И. О. абитуриента", 7,
                    room=frame_right - split - 20)
        for distance in (DTM13_TITLE_BOTTOM + 40, DTM13_TITLE_BOTTOM + 64):
            sheet.setStrokeColor(DTM13_RED)
            sheet.line(split + 14, top(distance), frame_right - 14, top(distance))
    else:
        _dtm13_text(sheet, name_center, top(DTM13_TITLE_BOTTOM + 52), "Abituriyent F. I. O.", 19,
                    color=colors.HexColor("#5F5F5F"), room=frame_right - split - 20)
    _dtm13_text(sheet, name_center, top(DTM13_HEAD_BOTTOM - 20), test.title, 7.5, REPORT_FONT_BOLD,
                color=colors.black, room=frame_right - split - 20)
    _dtm13_text(sheet, name_center, top(DTM13_HEAD_BOTTOM - 9), f"Kod: {test.code}   ·   Sana: ____________", 6.5,
                color=REPORT_MUTED)

    # 3) Savollar ustunlari: sarlavha, pushti chiziqlar, raqamlar, A B C D
    titles = _dtm_section_titles(plan["subjects"])
    headers = [("", "Majburiy fanlar")]
    for title in titles[3:5]:
        parts = title.split("\n")
        headers.append((parts[0], parts[1]) if len(parts) > 1 else ("", parts[0]))
    columns_bottom = layout["columns_bottom"]
    row_top = DTM13_COLUMNS_TOP + DTM13_COLUMN_HEADER + 6
    for column, x in enumerate(layout["column_x"]):
        option_x = [x + 26 + option * DTM13_PITCH for option in range(4)]
        band_left = option_x[3] + DTM13_RADIUS + 5
        # Pushti chiziqlar: A va C doirachalari ostida, o'ng chetda keng band (rasmiy varaqdagidek)
        sheet.setFillColor(DTM13_PINK)
        for option in (0, 2):
            sheet.rect(option_x[option] - DTM13_RADIUS - 1.6, top(columns_bottom), 2 * DTM13_RADIUS + 3.2,
                       columns_bottom - DTM13_COLUMNS_TOP - DTM13_COLUMN_HEADER, stroke=0, fill=1)
        sheet.rect(band_left, top(columns_bottom), x + DTM13_COLUMN - band_left - 4,
                   columns_bottom - DTM13_COLUMNS_TOP - DTM13_COLUMN_HEADER, stroke=0, fill=1)
        sheet.setStrokeColor(DTM13_RED)
        sheet.setLineWidth(0.9)
        sheet.rect(x, top(columns_bottom), DTM13_COLUMN, columns_bottom - DTM13_COLUMNS_TOP, stroke=1, fill=0)
        sheet.line(x, top(DTM13_COLUMNS_TOP + DTM13_COLUMN_HEADER), x + DTM13_COLUMN,
                   top(DTM13_COLUMNS_TOP + DTM13_COLUMN_HEADER))
        sheet.line(x + 19, top(DTM13_COLUMNS_TOP + DTM13_COLUMN_HEADER), x + 19, top(columns_bottom))
        caption, name = headers[column]
        if caption:
            _dtm13_text(sheet, x + DTM13_COLUMN / 2, top(DTM13_COLUMNS_TOP + 12), caption, 7, room=DTM13_COLUMN - 8)
            _dtm13_text(sheet, x + DTM13_COLUMN / 2, top(DTM13_COLUMNS_TOP + 25), name, 10, REPORT_FONT,
                        color=colors.black, room=DTM13_COLUMN - 8)
        else:
            _dtm13_text(sheet, x + DTM13_COLUMN / 2, top(DTM13_COLUMNS_TOP + 20), name, 10.5, REPORT_FONT,
                        color=colors.black, room=DTM13_COLUMN - 8)
        if column == 0:
            # Majburiy fanlar: har 10 savol — bitta fan; nomi o'ng bandda tik yoziladi, guruhlar orasida chiziq
            sheet.saveState()
            sheet.setDash(1.5, 1.5)
            sheet.setLineWidth(0.5)
            for group in (1, 2):
                line_top = row_top + group * 10 * DTM13_ROW
                sheet.line(x + 2, top(line_top), band_left - 2, top(line_top))
            sheet.restoreState()
            for group, subject in enumerate(plan["subjects"][:3]):
                center = row_top + (group * 10 + 5) * DTM13_ROW
                sheet.saveState()
                sheet.translate((band_left + x + DTM13_COLUMN - 4) / 2 - 2, top(center))
                sheet.rotate(-90)
                _dtm13_text(sheet, 0, 0, subject, 6, room=10 * DTM13_ROW - 8)
                sheet.restoreState()

    for (label, question_type, options, correct, ref), column, row in plan["pages"][page - 1]:
        x = layout["column_x"][column]
        center = row_top + (row + 0.5) * DTM13_ROW
        _dtm13_text(sheet, x + 16, top(center) - 2.9, str(label), 7.8, align="right")
        correct_letters = set((correct or "").split(","))
        for option in range(options):
            letter = chr(65 + option)
            option_x = x + 26 + option * DTM13_PITCH
            bubble(option_x, center, letter, with_answers and letter in correct_letters)
            note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                  "x": option_x, "y": top(center), "r": DTM13_RADIUS})

    # 4) O'ng tomon: variant (rasmiy varaqdagi "Chet tili" o'rnida) va o'quvchi raqami ("Kitob raqami" o'rnida)
    right_left, right_right = layout["right"]
    side_top, side_bottom, side_header = layout["side"]
    id_width = 4 * 15 + 16
    shown_variants = (variants or [])[:4]
    if shown_variants:
        variant_box = (right_left, right_left + 18 + 15 * len(shown_variants))
        id_box = (right_right - id_width, right_right)
    else:
        variant_box = None
        id_box = ((right_left + right_right - id_width) / 2, (right_left + right_right + id_width) / 2)
    student_variant = student[2] if student and len(student) > 2 else None
    if variant_box:
        left, right = variant_box
        sheet.setStrokeColor(DTM13_RED)
        sheet.rect(left, top(side_bottom), right - left, side_bottom - side_top, stroke=1, fill=0)
        sheet.line(left, top(side_top + side_header), right, top(side_top + side_header))
        _dtm13_text(sheet, (left + right) / 2, top(side_top + 14), "Variant", 8, REPORT_FONT, room=right - left - 4)
        _dtm13_text(sheet, (left + right) / 2, top(side_top + 26), "Вариант", 7, REPORT_FONT, room=right - left - 4)
        for index, name in enumerate(shown_variants):
            x = left + 16 + index * 15
            if index % 2 == 0:
                sheet.setFillColor(DTM13_PINK)
                sheet.rect(x - 6, top(side_bottom - 3), 12, side_bottom - side_top - side_header - 6, stroke=0, fill=1)
            center = side_top + side_header + 14
            bubble(x, center, name, student_variant == name, 5.4)
            note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                  "x": x, "y": top(center), "r": DTM13_RADIUS})
            sheet.saveState()
            sheet.translate(x - 2.0, top(center + 12))
            sheet.rotate(-90)
            _dtm13_text(sheet, 0, 0, f"{name}-variant / Вариант {name}", 5.6, align="left",
                        room=side_bottom - center - 20)
            sheet.restoreState()

    left, right = id_box
    sheet.setStrokeColor(DTM13_RED)
    sheet.setLineWidth(0.9)
    sheet.rect(left, top(side_bottom), right - left, side_bottom - side_top, stroke=1, fill=0)
    sheet.line(left, top(side_top + side_header), right, top(side_top + side_header))
    _dtm13_text(sheet, (left + right) / 2, top(side_top + 14), "O'quvchi raqami", 8, REPORT_FONT, room=right - left - 4)
    _dtm13_text(sheet, (left + right) / 2, top(side_top + 26), "Номер ученика", 7, REPORT_FONT, room=right - left - 4)
    digits = f"{student[0]:04d}" if student else None
    first_x = left + 8 + 7.5
    cell_top = side_top + side_header + 4
    for column in range(4):
        x = first_x + column * 15
        if column % 2 == 0:
            sheet.setFillColor(DTM13_PINK)
            sheet.rect(x - 6.5, top(side_bottom - 3), 13, side_bottom - cell_top - 21, stroke=0, fill=1)
        sheet.setStrokeColor(DTM13_RED)
        sheet.setFillColor(colors.white)
        sheet.rect(x - 6.5, top(cell_top + 16), 13, 16, stroke=1, fill=1)
        if digits:
            _dtm13_text(sheet, x, top(cell_top + 12), digits[column], 9, REPORT_FONT_BOLD, color=colors.black)
        for row, digit in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 0]):  # rasmiy varaqdagidek: 1 … 9, 0
            center = layout["id_first"] + row * DTM13_ROW
            bubble(x, center, str(digit), bool(digits) and int(digits[column]) == digit, 5.6)
            note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                  "x": x, "y": top(center), "r": DTM13_RADIUS})

    # 4.5) 14-versiya: "SERTIFIKAT" — faqat sertifikati borlar to'ldiradi
    if plan.get("cert_section"):
        _draw_dtm14_certificates(sheet, plan, layout, top, note, bubble)

    # 5) Ism-familiya qutisi (13-versiya; o'quvchi to'ldiradi) — skaner shu joyni rasm qilib o'qituvchiga ko'rsatadi
    if layout["name_box"]:
        box_left, box_top, box_right, box_bottom = layout["name_box"]
        sheet.setStrokeColor(DTM13_RED)
        sheet.rect(box_left, top(box_bottom), box_right - box_left, box_bottom - box_top, stroke=1, fill=0)
        sheet.line(box_left, top(box_top + 34), box_right, top(box_top + 34))
        _dtm13_text(sheet, (box_left + box_right) / 2, top(box_top + 14), "Abituriyent tomonidan to'ldiriladi!", 8,
                    room=box_right - box_left - 8)
        _dtm13_text(sheet, (box_left + box_right) / 2, top(box_top + 26), "Заполняется абитуриентом!", 8,
                    room=box_right - box_left - 8)
        for distance, caption in zip(layout["name_lines"], ["(Familiyasi / Фамилия)", "(Ismi / Имя)",
                                                            "(Otasining ismi / Отчество)"]):
            sheet.setStrokeColor(DTM13_RED)
            sheet.line(box_left + 8, top(distance), box_right - 8, top(distance))
            _dtm13_text(sheet, (box_left + box_right) / 2, top(distance + 9), caption, 6.3)
        sign = layout["name_lines"][-1] + 30
        if sign + 10 < box_bottom:
            sheet.saveState()
            sheet.setDash(1, 1.5)
            sheet.line(box_left + 40, top(sign), box_right - 40, top(sign))
            sheet.restoreState()
            _dtm13_text(sheet, (box_left + box_right) / 2, top(sign + 9), "Imzosi / Подпись", 6.3)

    # 6) Dalolatnoma: nazoratchilar to'ldiradi (rasmiy varaqdagidek)
    banner_top = layout["banner_top"]
    sheet.setFillColor(DTM13_BANNER)
    sheet.rect(frame_left, top(banner_top + DTM13_BANNER_HEIGHT), frame_right - frame_left, DTM13_BANNER_HEIGHT,
               stroke=0, fill=1)
    sheet.setStrokeColor(DTM13_RED)
    sheet.line(frame_left, top(banner_top), frame_right, top(banner_top))
    sheet.line(frame_left, top(banner_top + DTM13_BANNER_HEIGHT), frame_right, top(banner_top + DTM13_BANNER_HEIGHT))
    _dtm13_text(sheet, width / 2, top(banner_top + 10),
                "Diqqat! Quyidagi dalolatnoma guruh nazoratchilari tomonidan rasmiylashtiriladi!", 7.2,
                SHEET_SANS_BOLD, color=colors.white)
    _dtm13_text(sheet, width / 2, top(banner_top + 20),
                "Внимание! Нижеследующий акт оформляется наблюдателями группы!", 7.2, SHEET_SANS_BOLD,
                color=colors.white)
    act = banner_top + DTM13_BANNER_HEIGHT
    _dtm13_text(sheet, width / 2, top(act + 14),
                "TEST SINOVIDAN CHETLASHTIRISH DALOLATNOMASI  /  АКТ ОБ ОТСТРАНЕНИИ ОТ ТЕСТОВЫХ ИСПЫТАНИЙ", 7,
                room=frame_right - frame_left - 20)
    sheet.setStrokeColor(DTM13_RED)
    sheet.setLineWidth(0.5)
    sheet.line(frame_left + 12, top(act + 32), frame_right - 12, top(act + 32))
    _dtm13_text(sheet, width / 2, top(act + 39), "(Chetlashtirish vaqti va sababi / Время и причина отстранения)", 5.8)
    for index, distance in enumerate((act + 58, act + 80)):
        _dtm13_text(sheet, frame_left + 10, top(distance), f"{index + 1})", 8, align="left")
        sheet.line(frame_left + 22, top(distance), frame_right - 110, top(distance))
        _dtm13_text(sheet, (frame_left + frame_right - 88) / 2, top(distance + 7),
                    "Guruh nazoratchisining familiyasi, ismi, otasining ismi / Фамилия, имя, отчество наблюдателя группы",
                    5.2, room=frame_right - frame_left - 140)
        sheet.saveState()
        sheet.setDash(1, 1.5)
        sheet.line(frame_right - 96, top(distance), frame_right - 12, top(distance))
        sheet.restoreState()
        _dtm13_text(sheet, frame_right - 54, top(distance + 7), "Imzosi / Подпись", 5.6)
    sheet.restoreState()
    sheet.setFillColor(colors.black)
    sheet.setStrokeColor(colors.black)


def _draw_fill_examples(sheet, top, width: float, y_line: float, style: dict) -> None:
    """Titul pastida: qanday bo'yash kerak (to'liq qora doiracha) va qanday bo'yamaslik kerak."""

    radius, band = style["radius"], style["step"]
    font = min(style.get("scale", 1), 1.2)
    left, right = SHEET_CORNER + 16, width - SHEET_CORNER - 16
    first, second = top(y_line + 0.3 * band), top(y_line + 0.62 * band)
    sheet.setFont(REPORT_FONT_BOLD, 9.5 * font)
    sheet.drawString(left, first - 3.3 * font, "TO'G'RI:")
    sheet.drawString(left, second - 3.3 * font, "NOTO'G'RI:")
    start = left + sheet.stringWidth("NOTO'G'RI:", REPORT_FONT_BOLD, 9.5 * font) + 14 + radius
    sheet.setLineWidth(1)
    # To'g'ri — to'liq qora
    sheet.circle(start, first, radius, stroke=1, fill=1)
    sheet.setFont(REPORT_FONT, 8.5 * font)
    hint = "doirachani to'liq, qora ruchka yoki qalam bilan bo'yang. Har savolga — bitta javob."
    for shorter in ("to'liq bo'yang, har savolga bitta javob", "to'liq bo'yang"):
        # Yozuv titul chetidan (o'ng chet belgilari chizig'idan) chiqib ketmasin — skanerga xalaqit beradi
        if sheet.stringWidth(hint, REPORT_FONT, 8.5 * font) > right - start - radius - 8:
            hint = shorter
    sheet.drawString(start + radius + 8, first - 3 * font, hint)
    # Noto'g'ri — belgi, X, yarim, nuqta, ikkita
    examples = ["belgi", "X", "yarim", "nuqta", "ikkita"]
    spacing = min(62.0, (right - start - 2 * radius) / (len(examples) - 0.4))
    while spacing < 38 and len(examples) > 3:  # tor titulda yozuvlar bir-biriga tegmasin
        examples.remove("nuqta" if "nuqta" in examples else "belgi")
        spacing = min(62.0, (right - start - 2 * radius) / (len(examples) - 0.4))
    sheet.setFont(REPORT_FONT, 7.5 * font)
    for index, caption in enumerate(examples):
        x = start + index * spacing
        if caption == "ikkita":
            for dx in (-radius * 1.3, radius * 1.3):
                sheet.circle(x + dx, second, radius, stroke=1, fill=1)
        else:
            sheet.circle(x, second, radius, stroke=1, fill=0)
        if caption == "belgi":
            path = sheet.beginPath()
            path.moveTo(x - radius * 0.55, second)
            path.lineTo(x - radius * 0.1, second - radius * 0.5)
            path.lineTo(x + radius * 0.65, second + radius * 0.6)
            sheet.drawPath(path, stroke=1, fill=0)
        elif caption == "X":
            for sign in (1, -1):
                sheet.line(x - radius * 0.55, second - sign * radius * 0.55, x + radius * 0.55, second + sign * radius * 0.55)
        elif caption == "yarim":
            sheet.wedge(x - radius, second - radius, x + radius, second + radius, 90, 180, stroke=0, fill=1)
        elif caption == "nuqta":
            sheet.circle(x, second, radius * 0.28, stroke=0, fill=1)
        sheet.drawCentredString(x, second - radius - 8 * font, caption)


def _draw_evalbee_card(sheet, test: Test, plan: dict, page: int, with_answers: bool, student: tuple | None,
                       bubbles: list | None, variants: list[str] | None, key_variant: str | None) -> None:
    """EvalBee uslubidagi titulning bitta sahifasi ((0, 0) — titulning chap-past burchagi)."""

    style = plan["style"]
    width, height = plan["width"], plan["height"]
    column_width = plan["column_width"]
    radius, gap, label_width, header = style["radius"], style["gap"], style["label"], style["header"]
    content = label_width + 3 * gap + 2 * radius
    pad = (column_width - content) / 2
    label_right = pad + label_width - 6 * style.get("bubble_scale", style.get("scale", 1))  # savol raqamining o'ng cheti

    def top(distance: float) -> float:
        return height - distance

    def note(item: dict) -> None:
        if bubbles is not None:
            bubbles.append({"page": page, **item})

    def line_y(block_row: int) -> float:
        """Blok qatorining tepa chizig'i (PDF koordinatasi): chet belgilari bilan bir qatorda."""
        return top(SHEET_CORNER + (block_row + 1) * style["step"])

    def line_x(column: int) -> float:
        return SHEET_CORNER + column * column_width

    continuous = bool(plan.get("continuous"))
    blocks_count = style["edge_marks"] - 1

    def cell(column: int, row: int) -> tuple[int, int]:
        """11-versiya: qator qaysi 5 qatorli oraliqda (kvadratlar to'rining katagi)."""
        return column, min(row // SHEET11_GROUP, blocks_count - 1)

    # Shu sahifada ishlatilgan bloklar: kvadratlar faqat ularning burchaklarida (9-versiya)
    if continuous:
        used = {cell(column, row) for _, column, row, _ in plan["pages"][page - 1]}
        if page == 1:
            used |= {cell(0, row) for row in range(plan["prefix"] - 1)}
            if plan.get("written"):
                # Yozma kataklar bandi: har katak o'z oralig'ining 4 kvadrati orasida (skaner shu bo'yicha qirqadi)
                bands = math.ceil((max(line for _, _, line in plan["written"]) + 1) / SHEET12_LINES)
                used |= {(column, plan["bubble_blocks"] + band)
                         for column in range(style["columns"]) for band in range(bands)}
    else:
        used = {(column, block_row) for _, column, block_row, _ in plan["pages"][page - 1]}
        if page == 1:
            used |= {(0, block_row) for block_row in range(plan["reserved"] if variants else 2)}
    corners_used = {(column + dx, block_row + dy) for column, block_row in used for dx in (0, 1) for dy in (0, 1)}

    # Chet belgilari (chap/o'ng chiziqlar) + burchaklar + imtihon kodi
    _draw_sheet_marks(sheet, test, width, height, bubbles, page, style["edge_marks"])
    anchor_ids: dict[tuple[int, int], str] = {}
    for column in range(style["columns"] + 1):
        for row in range(style["edge_marks"]):
            if style["sparse"] and (column, row) not in corners_used:
                continue
            x, y = line_x(column), line_y(row)
            if 0 < column < style["columns"]:  # chetdagilari — chet belgilarining o'zi
                sheet.rect(x - SHEET_EDGE_SIZE / 2, y - SHEET_EDGE_SIZE / 2, SHEET_EDGE_SIZE, SHEET_EDGE_SIZE,
                           stroke=0, fill=1)
            anchor_ids[(column, row)] = str(len(anchor_ids))
            note({"kind": "anchor", "ref": "anchor", "letter": anchor_ids[(column, row)],
                  "x": x, "y": y, "r": SHEET_EDGE_SIZE / 2})

    def block(column: int, block_row: int) -> list[str]:
        return [anchor_ids[(column, block_row)], anchor_ids[(column + 1, block_row)],
                anchor_ids[(column + 1, block_row + 1)], anchor_ids[(column, block_row + 1)]]

    def row_center(block_row: int, line: int) -> float:
        return line_y(block_row) - header - (line + 0.5) * style["row"]

    def option_x(column: int, option: int) -> float:
        return line_x(column) + pad + label_width + radius + option * gap

    # Tepadagi quti: Ism-familiya | Imtihon | Sana (EvalBee'dagi NAME / EXAM / DATE)
    box_top, box_middle, box_bottom = style["box"]
    font = style["box_font"]
    sana = style["sana_width"]
    box_left, box_right = SHEET_CORNER + 16, width - SHEET_CORNER - 16
    sheet.setStrokeColor(colors.HexColor("#555555"))
    sheet.rect(box_left, top(box_bottom), box_right - box_left, box_bottom - box_top, stroke=1, fill=0)
    sheet.line(box_left, top(box_middle), box_right, top(box_middle))
    sheet.line(box_right - sana, top(box_bottom), box_right - sana, top(box_middle))
    sheet.setStrokeColor(colors.black)
    # Yozuvlar qatorlar (gorizontal chiziqlar) o'rtasida
    name_y = top(box_middle - (box_middle - box_top) / 2 + font * 0.35)
    exam_y = top(box_bottom - (box_bottom - box_middle) / 2 + font * 0.35)
    sheet.setFont(REPORT_FONT, font)
    sheet.drawString(box_left + 6, name_y, "Ism-familiya :")
    sheet.drawString(box_left + 6, exam_y, "Imtihon :")
    sheet.drawString(box_right - sana + 6, exam_y, "Sana :")
    if style.get("logo"):
        logo_width, logo_height = style["logo"]
        row_height = box_middle - box_top
        try:
            sheet.drawImage(str(SHEET_LOGO), box_right - logo_width - 6, top(box_middle) + (row_height - logo_height) / 2,
                            logo_width, logo_height, mask="auto")
        except Exception:
            logging.getLogger("backend").warning("Titul logosi chizilmadi", exc_info=True)
    sheet.setFont(REPORT_FONT_BOLD, font)
    if student:
        # Ism o'z joyiga sig'adi: avval shrift kichrayadi (70% gacha), baribir uzun bo'lsa — "…" bilan
        name, name_x = student[1][:60], box_left + 8 * font
        limit = (box_right - style["logo"][0] - 12 if style.get("logo") else box_right - 6) - name_x
        size = font
        while size > font * 0.7 and sheet.stringWidth(name, REPORT_FONT_BOLD, size) > limit:
            size -= 0.25
        while len(name) > 3 and sheet.stringWidth(name, REPORT_FONT_BOLD, size) > limit:
            name = name[:-2].rstrip() + "…"
        sheet.setFont(REPORT_FONT_BOLD, size)
        sheet.drawString(name_x, name_y, name)
        sheet.setFont(REPORT_FONT_BOLD, font)
    title = test.title + (f" — {page}-sahifa" if page > 1 else "")
    code = f"  ·  Kod {test.code}" if style["instructions"] else ""
    exam_left = box_left + 5.6 * font
    # Tor titulda avval shrift kichrayadi (70% gacha), keyin nom qisqartiriladi — imtihon kodi doim ko'rinsin
    room, size = box_right - sana - exam_left - 6, font
    while size > font * 0.7 and sheet.stringWidth(title + code, REPORT_FONT_BOLD, size) > room:
        size -= 0.25
    while len(title) > 4 and sheet.stringWidth(title + code, REPORT_FONT_BOLD, size) > room:
        title = title[:-2].rstrip() + "…" if not title.endswith("…") else title[:-2] + "…"
    sheet.setFont(REPORT_FONT_BOLD, size)
    sheet.drawString(exam_left, exam_y, title + code)
    sheet.setFont(REPORT_FONT_BOLD, font)
    bottom_line = SHEET_CORNER + style["edge_marks"] * style["step"]  # oxirgi kvadratlar qatori (tepadan)
    corner_note = f"JAVOBLAR KALITI{f' — {key_variant}' if key_variant else ''} · " if with_answers else ""
    if style["instructions"]:
        _draw_fill_examples(sheet, top, width, bottom_line, style)
    else:
        sheet.setFillColor(REPORT_MUTED)
        sheet.setFont(REPORT_FONT, 7)
        # Pastda, oxirgi kvadratlar qatori bilan imtihon kodi orasidagi bo'sh joyda (kvadratlarga tegmasin)
        sheet.drawCentredString(width / 2, top(bottom_line + 30), f"{corner_note}Kod {test.code} · doirachani to'liq bo'yang")
        sheet.setFillColor(colors.black)

    if continuous:
        _draw_continuous_body(sheet, plan, page, student, variants, with_answers, note, block, cell, line_x, line_y,
                              option_x, label_right, column_width)
        return

    if page == 1:
        # O'quvchi ID (Roll No): 4 ustun; 0–4 birinchi blokda, 5–9 ikkinchisida; tepada yozish kataklari
        digits = f"{student[0]:04d}" if student else None
        for block_row in (0, 1):
            corners = block(0, block_row)
            if block_row == 0:
                # Yozish kataklari "0" qatoridagi doirachalardan ajralib turadi (tegib qolmasin)
                bubble_top = line_y(0) - header - 0.5 * style["row"] + radius
                box_bottom = bubble_top + 2.5
                box_height = min(header - 3, 13 * min(style.get("scale", 1), 1.2))
                text_y = box_bottom + (box_height - style["letter_font"] * 0.7) / 2
                sheet.setFont("Helvetica-Bold", style["letter_font"])
                sheet.drawRightString(line_x(0) + label_right, text_y, "ID")
                for column in range(4):
                    x = option_x(0, column)
                    sheet.rect(x - radius - 0.5, box_bottom, 2 * radius + 1, box_height, stroke=1, fill=0)
                    if digits:
                        sheet.drawCentredString(x, text_y, digits[column])
            for line in range(5):
                digit = block_row * 5 + line
                center = row_center(block_row, line)
                if style.get("id_tight"):
                    # Raqamlar ustma-ust, oraliqsiz; doiracha qaysi blok ichiga tushsa — o'sha blok kvadratlari
                    center = line_y(0) - header - (digit + 0.5) * style["row"]
                    corners = block(0, 0 if line_y(0) - center < style["step"] else 1)
                sheet.setFont("Helvetica-Bold", style["digit_font"])
                sheet.drawRightString(line_x(0) + label_right, center - style["digit_font"] * 0.35, str(digit))
                for column in range(4):
                    x = option_x(0, column)
                    filled = digits is not None and int(digits[column]) == digit
                    sheet.circle(x, center, radius, stroke=1, fill=1 if filled else 0)
                    note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                          "x": x, "y": center, "r": radius, "block": corners})
        if variants:
            # Variant — 1-ustunning 3-blokida, bitta qatorda (A B C D)
            student_variant = student[2] if student and len(student) > 2 else None
            corners = block(0, 2)
            sheet.setFont("Helvetica-Bold", style["letter_font"])
            center = row_center(2, 0)
            sheet.drawRightString(line_x(0) + label_right, center - 2.5, "Var.")
            for index, name in enumerate(variants[:4]):
                x = option_x(0, index)
                sheet.drawCentredString(x, line_y(2) - header + 1.5, name)
                sheet.circle(x, center, radius, stroke=1, fill=1 if student_variant == name else 0)
                note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                      "x": x, "y": center, "r": radius, "block": corners})

    if page == 1 and plan.get("titles"):
        # Sarlavha: chiziqdagi kvadratlar (±yarim o'lcham) ostida, A B C D harflari ustida; kvadratlar faqat
        # chiziqda turadi — ostidagi joy ochiq, asosiy fan sarlavhasi ikki ustun kengligida yoziladi
        letters_top = header - 1.5 - style["letter_font"] * 0.8  # chiziqdan harflarning yuqori chetigacha
        for column, block_row, span, caption, text in plan["titles"]:
            # Majburiy fan — o'z bloki o'rtasida; asosiy fan (ikki ustunli) — chapga tekislangan
            center = line_x(column) + span * column_width / 2
            left = line_x(column) + SHEET_EDGE_SIZE / 2 + 5
            room = span * column_width - SHEET_EDGE_SIZE - 10
            y = line_y(block_row) - SHEET_EDGE_SIZE / 2 - 1.5
            if caption:
                sheet.setFont(REPORT_FONT, 8.5)
                y -= 6.2
                sheet.drawCentredString(center, y, caption)
            size = 10.0
            while size > 7 and sheet.stringWidth(text, REPORT_FONT_BOLD, size) > room:
                size -= 0.25
            while len(text) > 4 and sheet.stringWidth(text, REPORT_FONT_BOLD, size) > room:
                text = text[:-2].rstrip() + "…"
            y -= size * (1.0 if caption else 0.85)
            y = max(y, line_y(block_row) - letters_top + 2)  # harflarga tegmasin
            sheet.setFont(REPORT_FONT_BOLD, size)
            if span > 1:
                sheet.drawString(left, y, text)
            else:
                sheet.drawCentredString(center, y, text)

    headers_drawn = set()
    for (label, question_type, options, correct, ref), column, block_row, line in plan["pages"][page - 1]:
        corners = block(column, block_row)
        if (column, block_row) not in headers_drawn:
            headers_drawn.add((column, block_row))
            sheet.setFont("Helvetica-Bold", style["letter_font"])
            for option in range(options):
                sheet.drawCentredString(option_x(column, option), line_y(block_row) - header + 1.5, chr(65 + option))
        center = row_center(block_row, line)
        sheet.setFont("Helvetica", style["number_font"])
        sheet.drawRightString(line_x(column) + label_right, center - style["number_font"] * 0.35, f"{label}")
        correct_letters = set((correct or "").split(","))
        for option in range(options):
            letter = chr(65 + option)
            x = option_x(column, option)
            filled = with_answers and letter in correct_letters
            sheet.circle(x, center, radius, stroke=1, fill=1 if filled else 0)
            note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                  "x": x, "y": center, "r": radius, "block": corners})


def _draw_continuous_body(sheet, plan: dict, page: int, student: tuple | None, variants: list[str] | None,
                          with_answers: bool, note, block, cell, line_x, line_y, option_x, label_right: float,
                          column_width: float) -> None:
    """11-versiya: ID, variant, fan sarlavhalari, "A B C D" qatorlari va savollar — bir xil qator oralig'ida."""

    style = plan["style"]
    radius, pitch = style["radius"], style["row"]

    def row_y(row: int) -> float:
        return line_y(0) - row * pitch

    def text_y(row: int, size: float) -> float:
        return row_y(row) - size * 0.35

    def wide_x(column: int, option: int, options: int) -> float:
        """
        5–6 variantli qator (33–35, A–F) ustunga sig'ishi uchun o'ng chetdan joylanadi (kvadratlarga tegmaydi).
        12-versiyadan; oldingi titullar o'z joylashuvida (chop etilganlari to'g'ri skanerlansin).
        """
        if options <= 4 or not plan.get("wide_rows"):
            return option_x(column, option)
        right = line_x(column) + column_width - 0.65 * pitch - radius
        return right - (options - 1 - option) * style["gap"]

    def number_right(column: int, options: int) -> float:
        """Savol raqamining o'ng cheti: keng qatorda — birinchi doirachadan oldin."""
        if options <= 4 or not plan.get("wide_rows"):
            return line_x(column) + label_right
        return wide_x(column, 0, options) - radius - 0.25 * pitch

    if page == 1:
        # O'quvchi ID: 0-qatorda yozish kataklari, ostida 0–9 (4 xona)
        digits = f"{student[0]:04d}" if student else None
        sheet.setFont("Helvetica-Bold", style["letter_font"])
        sheet.drawRightString(line_x(0) + label_right, text_y(0, style["letter_font"]), "ID")
        box_low = row_y(0) - radius + 2.5  # "0" qatoridagi doirachalarga tegmasin
        for column in range(4):
            x = option_x(0, column)
            sheet.rect(x - radius - 0.5, box_low, 2 * radius + 1, 2 * radius - 1, stroke=1, fill=0)
            if digits:
                sheet.drawCentredString(x, box_low + radius - 0.5 - style["letter_font"] * 0.35, digits[column])
        sheet.setFont("Helvetica-Bold", style["digit_font"])
        for digit in range(10):
            row = 1 + digit
            corners = block(*cell(0, row))
            sheet.drawRightString(line_x(0) + label_right, text_y(row, style["digit_font"]), str(digit))
            for column in range(4):
                x = option_x(0, column)
                filled = digits is not None and int(digits[column]) == digit
                sheet.circle(x, row_y(row), radius, stroke=1, fill=1 if filled else 0)
                note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                      "x": x, "y": row_y(row), "r": radius, "block": corners})
        if variants:
            # Variant: harflar (11-qator) va doirachalar (12-qator)
            student_variant = student[2] if student and len(student) > 2 else None
            sheet.setFont("Helvetica-Bold", style["letter_font"])
            for index, name in enumerate(variants[:4]):
                sheet.drawCentredString(option_x(0, index), text_y(11, style["letter_font"]), name)
            corners = block(*cell(0, 12))
            sheet.drawRightString(line_x(0) + label_right, text_y(12, style["letter_font"]), "Var.")
            for index, name in enumerate(variants[:4]):
                x = option_x(0, index)
                sheet.circle(x, row_y(12), radius, stroke=1, fill=1 if student_variant == name else 0)
                note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                      "x": x, "y": row_y(12), "r": radius, "block": corners})

    for kind, column, row, payload in plan["pages"][page - 1]:
        if kind == "title":
            # Fan nomi (DTM): ustun kengligiga sig'adigan qilib
            text, size = payload, 8.5 * style["scale"]
            room = column_width - SHEET_EDGE_SIZE - 10
            while size > 6 and sheet.stringWidth(text, REPORT_FONT_BOLD, size) > room:
                size -= 0.25
            while len(text) > 4 and sheet.stringWidth(text, REPORT_FONT_BOLD, size) > room:
                text = text[:-2].rstrip() + "…"
            sheet.setFont(REPORT_FONT_BOLD, size)
            sheet.drawCentredString(line_x(column) + column_width / 2, text_y(row, size), text)
        elif kind == "letters":
            sheet.setFont("Helvetica-Bold", style["letter_font"])
            for option in range(payload):
                sheet.drawCentredString(wide_x(column, option, payload), text_y(row, style["letter_font"]), chr(65 + option))
        else:
            label, question_type, options, correct, ref = payload
            corners = block(*cell(column, row))
            sheet.setFont("Helvetica", style["number_font"])
            sheet.drawRightString(number_right(column, options), text_y(row, style["number_font"]), f"{label}")
            correct_letters = set((correct or "").split(","))
            for option in range(options):
                letter = chr(65 + option)
                x = wide_x(column, option, options)
                filled = with_answers and letter in correct_letters
                sheet.circle(x, row_y(row), radius, stroke=1, fill=1 if filled else 0)
                note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                      "x": x, "y": row_y(row), "r": radius, "block": corners})

    if page == 1 and plan.get("written"):
        _draw_written_band(sheet, plan, note, block, line_x, line_y)


def _draw_written_band(sheet, plan: dict, note, block, line_x, line_y) -> None:
    """
    Yozma javob kataklari (36a, 36b …): chapda savol nomi, o'ngida yozish uchun to'rtburchak.
    Skaner javobni o'qimaydi — katakni rasm qilib qirqib oladi, o'qituvchi rasmga qarab baholaydi.
    """

    style = plan["style"]
    pitch = style["row"]
    line_height = style["step"] / SHEET12_LINES
    margin = 0.45 * pitch  # kvadratlar chizig'idan va qo'shni qatordan oraliq
    height = line_height - 2 * margin
    font_size = style["number_font"]
    slot = max(stringWidth(item[0], "Helvetica-Bold", font_size) for item, _, _ in plan["written"])
    sheet.setLineWidth(0.9)
    sheet.setFont("Helvetica-Bold", font_size)
    for (label, _, _, _, ref), column, line in plan["written"]:
        block_row = plan["bubble_blocks"] + line // SHEET12_LINES
        top = line_y(block_row) - (line % SHEET12_LINES) * line_height - margin
        label_left = line_x(column) + 0.45 * pitch
        left = label_left + slot + 0.3 * pitch
        right = line_x(column + 1) - 0.5 * pitch
        sheet.drawString(label_left, top - height / 2 - font_size * 0.35, label)
        sheet.rect(left, top - height, right - left, height, stroke=1, fill=0)
        note({"ref": ref, "label": label, "kind": "write", "letter": "",
              "x": (left + right) / 2, "y": top - height / 2, "w": right - left, "h": height, "r": 0,
              "block": block(column, block_row)})


def _draw_sheet_brand(sheet, card_bottom: float) -> None:
    """
    Titul ostida (kartochkadan tashqarida) Chittak belgisi: logo, nom va shior. Skaner faqat burchak va
    chet belgilari orasini o'qiydi; belgi pastki belgilardan ~60 pt pastda, kichik va o'qilmaydigan joyda.
    Kartochka butun sahifani egallasa (joy yo'q) — chizilmaydi.
    """

    y = card_bottom - 28
    if y < 30:
        return
    name_width = stringWidth("Chittak", REPORT_FONT_BOLD, 10)
    slogan = f"·  {REPORT_SLOGAN}"
    total = 16 + 6 + name_width + 6 + stringWidth(slogan, REPORT_FONT, 8)
    x = (A4[0] - total) / 2
    sheet.saveState()
    try:
        sheet.drawImage(str(FRONTEND_DIR / "img" / "chittak-icon.png"), x, y - 4, 16, 16, mask="auto")
    except Exception:
        pass
    sheet.setFillColor(REPORT_BLUE)
    sheet.setFont(REPORT_FONT_BOLD, 10)
    sheet.drawString(x + 22, y, "Chittak")
    sheet.setFillColor(REPORT_MUTED)
    sheet.setFont(REPORT_FONT, 8)
    sheet.drawString(x + 28 + name_width, y + 1, slogan)
    sheet.restoreState()


# ---------- 15-versiya: Milliy sertifikat tituli (2 bet) — imtihondagi "Javoblar varaqasi" ko'rinishida ----------
#
# Milliy sertifikat tuzilishidagi oflayn test (hozircha Matematika, Fizika, Tarix — MS15_READY_SUBJECTS): 1–32 (A–D),
# 33–35 (A–F), 36–45 — yozma (a, b). Boshqa testlar (Geografiya, Kimyo, Biologiya, Ona tili, DTM, erkin) — o'z titulida.
#   1-bet: fan, o'quvchi ID (4 xona), variant, test ma'lumoti; 1–35 doirachalar; diqqat, bo'yash namunasi, F.I.O.;
#          dalolatnoma (imtihondagidek).
#   2-bet: o'quvchi ID — 1-betdagi blokning o'zi, chapga 90° burilgan; 36–45 yozish kataklari (ichi och katakchali).
#          ID ikkala betda: skaner 2-bet qaysi o'quvchiniki ekanini biladi (betlar aralashsa — ogohlantiradi).
# Chiziq va halqalar — to'q sariq (qora-oqda kulrang: skaner halqalarni ko'radi), doiracha ichidagi harf/raqam —
# och (skaner uchun "qog'oz"), katakchalar — undan ham och (yozuvga xalaqit bermaydi, katak "bo'sh" deb o'qiladi).
# Skaner belgilari (burchak va chet kvadratlari, pastki kod) — boshqa titullardagidek qora va o'sha joyda.

MS15_INK = colors.HexColor("#E2691D")
MS15_LETTER = colors.HexColor("#F6B48A")
MS15_FAINT = colors.HexColor("#F8D9C4")
MS15_BANNER = colors.HexColor("#F6B98E")
MS15_MARGIN = 48.0          # ramka chap/o'ng chetdan va pastdan (chet belgilari ramkadan tashqarida)
MS15_TOP = 46.0             # ramka tepadan
MS15_RADIUS = 6.2           # A–D doirachasi
MS15_WIDE_RADIUS = 5.8      # A–F doirachasi (ustunga sig'ishi uchun biroz kichik)
MS15_ROW = 29.0             # 1–35 qatorlari oralig'i
MS15_ID_RADIUS = 5.6
MS15_ID_STEP = 13.6         # ID raqamlari (1 … 9, 0) oralig'i
MS15_ID_BLOCK = (120.0, 186.0)  # ID bloki (eni, bo'yi) — 2-betda shu blok yotqizilgan
MS15_SUBJECTS = ("Matematika", "Fizika", "Kimyo", "Biologiya", "Tarix", "Geografiya")
MS15_WRITTEN = [f"{number}{part}" for number in range(36, 46) for part in "ab"]
# Titul tayyor (imtihondagi 2-beti tasdiqlangan) fanlar; qolganlari — o'z namunasi kelguncha eski titulda
MS15_NATIVE_SUBJECT = "ona tili va adabiyot"
MS15_SCIENCE_SUBJECTS = {"kimyo", "biologiya"}  # 36–40 — qisqa javob, 41–43 — yozma ish (har biriga bet)
MS15_READY_SUBJECTS = {"matematika", "fizika", "tarix", "geografiya", MS15_NATIVE_SUBJECT, *MS15_SCIENCE_SUBJECTS}
MS15_WORK_BANDS = 7          # yozma ishdagi bandlar soni — ko'pi bilan
MS15_SCIENCE_NOTE = ("Talabgorlar diqqatiga! O‘zgartirilgan javoblar hisobga olinmaydi. Sharikli ko‘k ruchkadan "
                     "foydalaning. Bosh harflarda yozing. Har bir belgi alohida katakchaga yoziladi.")
MS15_SCIENCE_SAMPLE = ["0", "1", "2", "3", ".", ":", "‘", "", "C", "a", "₃", "(", "P", "O", "₄", ")", "₂", "",
                       "A", "B", "C", "D", "E", "F", "I", "J"]
# Ona tili: 36–39 — bitta javob, 40–44 — a va b; 45 — esse (3–4-bet)
MS15_NATIVE_WRITTEN = ["36", "37", "38", "39"] + [f"{number}{part}" for number in range(40, 45) for part in "ab"]
MS15_NATIVE_NOTE = ("36–44-savollar javobini quyidagi namuna asosida bosh harflar bilan yozing. Sh, Ch, Ng "
                    "tovushlaridagi har bir belgi alohida katakchada, O‘, G‘ tovushlari esa bitta katakchada yoziladi.")
MS15_NATIVE_SAMPLE = [["A", "B", "", "C", "h", "A", "N", "g", "", "E", "’", "L", "O", "N"],
                      ["–", "-", "«", "»", ":", ";", "?", ".", ",", "", "S", "h", "O‘", "G‘"]]
MS15_NATIVE_CELLS = 14       # 36–44: har qatorda katakchalar soni (imtihondagidek)
MS15_ESSAY_LINES = 32        # esse betidagi chiziqlar
MS15_ESSAY_RED = colors.HexColor("#F08A8A")   # har 10 qator — rangli (qatorlarni sanash oson)
MS15_ESSAY_LINE = colors.HexColor("#B9B9B9")
MS15_CELL_SUBJECTS = {"tarix", "geografiya"}  # 2-betda javob katakchalarga harfma-harf yoziladigan fanlar
MS15_GEO_NOTE = ("Javobni namunadagidek bosh harflar bilan yozing: har bir belgi — harf, raqam, tutuq belgisi va "
                 "chiziqcha — alohida katakchaga yoziladi. O‘zgartirilgan javoblar hisobga olinmaydi.")
MS15_GEO_SAMPLE = ["S", "H", "C", "H", "O", "‘", "G", "‘", "1", "2", "’", "N", "G", "-"]
MS15_CELL_DIVIDER = colors.HexColor("#F2AE80")  # katakchalar orasidagi chiziq: ko'rinadi, skaner uchun "qog'oz"
MS15_CELL_NOTE = ("Eslatma: Sh, Ch, Ng tovushlaridagi har bir belgi alohida katakchada, O‘, G‘ tovushlari esa bitta "
                  "katakchada yoziladi. Javoblar varaqasini to‘ldirish uchun namuna: Bo‘shang, Nuh II, Tamg‘a, 1929-yil")
MS15_CELL_SAMPLE = ["B", "O‘", "S", "H", "A", "N", "G", "", "N", "U", "H", "", "II", "", "T", "A", "M", "G‘", "A", "",
                    "1", "9", "2", "9", "-", "Y", "I", "L"]


def _ms15_layout() -> dict:
    """Bandlar chegaralari (PDF koordinatasi — pastdan). Chizish va skaner (ism joyi) bir xil o'lchamni ishlatadi."""

    width, height = A4
    left, right = MS15_MARGIN, width - MS15_MARGIN
    header = height - MS15_TOP - 40          # sarlavha bandining pastki chizig'i
    info = header - 200                      # fan | ID | variant | test bandining pastki chizig'i
    grid_top = info - 10
    grid = grid_top - (7 * MS15_ROW + 14)    # 1–35
    notes_top = grid - 10
    notes = notes_top - 132                  # diqqat | F.I.O.
    id_band = header - (MS15_ID_BLOCK[0] + 12)  # 2-bet: yotqizilgan ID bloki bandi
    warn = id_band - 8 - 40                  # 2-bet: "Talabgor diqqatiga" bandi
    middle = (left + right) / 2
    return {
        "width": width, "height": height, "left": left, "right": right, "top": height - MS15_TOP,
        "bottom": MS15_MARGIN, "header": header, "info": info, "grid_top": grid_top, "grid": grid,
        "notes_top": notes_top, "notes": notes, "middle": middle, "id_band": id_band, "warn": warn,
        # Skaner o'qituvchiga ko'rsatadigan ism rasmi (tepadan sanalgan): 1-betdagi F.I.O. qismi
        "name_area": (middle + 8, height - (notes_top - 24), right - 8, height - (notes + 4)),
    }


def _ms15_plan(test: Test, all_items: list, variants: list[str] | None) -> dict | None:
    """Milliy sertifikat tuzilishidagi oflayn test uchun 2 betlik titul rejasi; mos kelmasa — None."""

    if getattr(test, "enrollment_type", None) != "closed" or (variants and len(variants) > 4):
        return None
    subject = " ".join((test.subject or "").split()).lower()
    if subject not in MS15_READY_SUBJECTS:
        return None
    closed = [item for item in all_items if item[1] != "text"]
    written = [item for item in all_items if item[1] == "text"]
    expected = [(str(number), 6 if number >= 33 else 4) for number in range(1, 36)]
    if [(item[0], item[2]) for item in closed] != expected or any(item[1] != "single" for item in closed):
        return None
    native = subject == MS15_NATIVE_SUBJECT
    science = subject in MS15_SCIENCE_SUBJECTS
    if science:
        # Kimyo, Biologiya: 36–40 — bitta javob; 41–43 — yozma ish bandlari (41.1, 41.2 … — 1–7 ta, o'qituvchi
        # [−]/[+] bilan o'zgartiradi). Har yozma ish — alohida bet (band javoblari + yechim uchun katakli qog'oz)
        short, works = written[:5], written[5:]
        if [item[0] for item in short] != [str(number) for number in range(36, 41)]:
            return None
        groups = []
        for number in (41, 42, 43):
            group = [item for item in works if item[0].split(".")[0] == str(number)]
            if not 1 <= len(group) <= MS15_WORK_BANDS or [item[0] for item in group] != [
                    f"{number}.{band}" for band in range(1, len(group) + 1)]:
                return None
            groups.append(group)
        if sum(len(group) for group in groups) != len(works):
            return None
        pages = [closed, short, *groups]
    elif [item[0] for item in written] != (MS15_NATIVE_WRITTEN if native else MS15_WRITTEN):
        return None
    else:
        pages = [closed, written]
    if native and getattr(test, "essay_max", None):
        pages += [["essay"], ["essay"]]  # 3–4-bet: esse (skaner betni butunligicha rasm qiladi)
    layout = _ms15_layout()
    return {"ms15": True, "width": layout["width"], "height": layout["height"], "edge_marks": SHEET_EDGE_MARKS,
            "pages": pages, "column_width": 0.0, "reserved": 0, "layout": layout,
            "style": {"name_area": layout["name_area"]},
            # 2-bet ko'rinishi: Ona tili — 36–44 katakchalari; Kimyo/Biologiya — 36–40 qatorlari (3–5-bet — yozma ish);
            # Tarix — harfma-harf qatorlar; qolganlari — erkin katak
            "native": native, "science": science, "cells": subject in MS15_CELL_SUBJECTS, "geo": subject == "geografiya"}


def _draw_ms15_card(sheet, test: Test, plan: dict, page: int, with_answers: bool, student: tuple | None,
                    bubbles: list | None, variants: list[str] | None, key_variant: str | None) -> None:
    """15-versiya Milliy sertifikat titulining bitta beti (A4, (0, 0) — chap-past burchak)."""

    layout = plan["layout"]
    width, height = layout["width"], layout["height"]
    left, right, bottom = layout["left"], layout["right"], layout["bottom"]
    digits = f"{student[0]:04d}" if student else None
    student_variant = student[2] if student and len(student) > 2 else None

    def note(item: dict) -> None:
        if bubbles is not None:
            bubbles.append({"page": page, **item})

    def text(x: float, y: float, value: str, size: float, font: str | None = None, align: str = "center",
             color=None, room: float | None = None) -> None:
        _dtm13_text(sheet, x, y, value, size, font or SHEET_SANS_BOLD, align, color or MS15_INK, room)

    def frame(x: float, y: float, w: float, h: float, radius: float = 0, line: float = 0.9) -> None:
        sheet.setStrokeColor(MS15_INK)
        sheet.setLineWidth(line)
        if radius:
            sheet.roundRect(x, y, w, h, radius, stroke=1, fill=0)
        else:
            sheet.rect(x, y, w, h, stroke=1, fill=0)

    def rule(x1: float, y1: float, x2: float, y2: float) -> None:
        sheet.setStrokeColor(MS15_INK)
        sheet.setLineWidth(0.9)
        sheet.line(x1, y1, x2, y2)

    def bubble(x: float, y: float, label: str, filled: bool = False, radius: float = MS15_RADIUS,
               size: float = 6.4) -> None:
        sheet.setStrokeColor(MS15_INK)
        sheet.setLineWidth(0.8)
        sheet.setFillColor(colors.black if filled else colors.white)
        sheet.circle(x, y, radius, stroke=1, fill=1)
        if label and not filled:
            sheet.setFillColor(MS15_LETTER)
            sheet.setFont(SHEET_SANS_BOLD, size)
            sheet.drawCentredString(x, y - size * 0.36, label)

    def id_block(origin, flat: bool) -> None:
        """
        O'quvchi ID bloki: yozuv, 4 ta yozish katagi, ostida har xonaga 1 … 9, 0. flat (2-bet) — chapga 90°
        burilgan: yozuv va kataklar chapda, raqamlar o'ngga qarab; 1-xona — eng pastki qator.
        """

        block_width, block_height = MS15_ID_BLOCK
        x0, y0 = origin

        def place(u: float, v: float) -> tuple[float, float]:  # blok ichidagi nuqta → varaqdagi joyi
            return (x0 - v, y0 + u) if flat else (x0 + u, y0 + v)

        sheet.saveState()
        sheet.translate(x0, y0)
        if flat:
            sheet.rotate(90)
        text(block_width / 2, block_height - 12, "O‘quvchi ID raqami", 7.4)
        for column in range(4):
            u = block_width / 2 + (column - 1.5) * 22
            sheet.setStrokeColor(MS15_INK)
            sheet.setLineWidth(0.9)
            sheet.setFillColor(colors.white)
            sheet.rect(u - 9, block_height - 40, 18, 18, stroke=1, fill=1)
            if digits:
                text(u, block_height - 35, digits[column], 11, REPORT_FONT_BOLD, color=colors.black)
            for row, digit in enumerate([1, 2, 3, 4, 5, 6, 7, 8, 9, 0]):  # imtihon varag'idagidek: 1 … 9, 0
                v = block_height - 54 - row * MS15_ID_STEP
                bubble(u, v, str(digit), bool(digits) and int(digits[column]) == digit, MS15_ID_RADIUS, 6.2)
                x, y = place(u, v)
                note({"ref": f"id{column}", "label": "ID", "kind": "id", "letter": str(digit),
                      "x": x, "y": y, "r": MS15_ID_RADIUS})
        sheet.restoreState()

    def name_lines(x1: float, x2: float, first: float, gap: float, captions: list[str]) -> None:
        if student:
            text((x1 + x2) / 2, first + 6, student[1][:60], 12, REPORT_FONT_BOLD, color=colors.black,
                 room=x2 - x1 - 24)
        for index, caption in enumerate(captions):
            y = first - index * gap
            rule(x1 + 12, y, x2 - 12, y)
            text((x1 + x2) / 2, y - 10, caption, 7.5)

    # Skaner belgilari: burchaklar, chap/o'ng chet belgilari va pastdagi kod (qora)
    sheet.setFillColor(colors.black)
    _draw_sheet_marks(sheet, test, width, height, bubbles, page, plan["edge_marks"])

    # Sarlavha: bet raqami, "JAVOBLAR VARAQASI", logo
    header = layout["header"]
    frame(left, header, right - left, 40)
    text(left + 12, header + 11, str(page), 22, REPORT_FONT_BOLD, align="left")
    text(width / 2, header + 22, "JAVOBLAR VARAQASI", 14, REPORT_FONT_BOLD)
    if plan.get("science"):
        subtitles = {1: "MILLIY SERTIFIKAT", 2: "36–40: QISQA JAVOBLAR",
                     **{index + 3: f"{number}: YOZMA ISH" for index, number in enumerate((41, 42, 43))}}
    else:
        subtitles = {1: "MILLIY SERTIFIKAT",
                     2: "36–44: YOZMA JAVOBLAR" if plan.get("native") else "36–45: YOZMA JAVOBLAR",
                     3: "45: ESSE", 4: "45: ESSE (DAVOMI)"}
    text(width / 2, header + 9, subtitles.get(page, ""), 7.2)
    try:
        logo_height = 22.0
        sheet.drawImage(str(SHEET_LOGO), right - 8 - logo_height * SHEET_LOGO_RATIO, header + 9,
                        logo_height * SHEET_LOGO_RATIO, logo_height, mask="auto")
    except Exception:
        logging.getLogger("backend").warning("Milliy sertifikat titul logosi chizilmadi", exc_info=True)

    if page == 1:
        # 1) Fan | O'quvchi ID | Variant | Test ma'lumoti
        info = layout["info"]
        frame(left, info, right - left, header - info)
        fan_right = left + 118
        id_right = fan_right + MS15_ID_BLOCK[0]
        shown_variants = (variants or [])[:4]
        test_left = id_right + 62 if shown_variants else id_right
        for x in {fan_right, id_right, test_left}:
            rule(x, info, x, header)
        text((left + fan_right) / 2, header - 16, "Test topshiriladigan fan", 7.4)
        for index, name in enumerate(MS15_SUBJECTS):  # imtihondagidek; skaner o'qimaydi (fan testda ma'lum)
            y = header - 40 - index * 20
            bubble(left + 16, y, "")
            text(left + 28, y - 3, name, 8.5, SHEET_SANS, "left", colors.HexColor("#333333"))
        id_block((fan_right, header - MS15_ID_BLOCK[1] - 4), flat=False)
        if shown_variants:
            middle = (id_right + test_left) / 2
            text(middle, header - 16, "Variant", 7.4)
            for index, name in enumerate(shown_variants):
                y = header - 44 - index * 22
                bubble(middle, y, name, student_variant == name)
                note({"ref": "variant", "label": "Variant", "kind": "variant", "letter": name,
                      "x": middle, "y": y, "r": MS15_RADIUS})
        middle = (test_left + right) / 2
        room = right - test_left - 20
        text(middle, header - 16, "TEST MA'LUMOTI", 7.4)
        text(middle, header - 52, test.title, 9, REPORT_FONT_BOLD, color=colors.black, room=room)
        text(middle, header - 68, f"Kod: {test.code}", 7.5, SHEET_SANS, color=REPORT_MUTED, room=room)
        rule(test_left + 12, header - 140, right - 12, header - 140)
        text(middle, header - 150, "(Sana)", 7.5)

        # 2) 1–35: 5 ustun × 7 qator; oxirgi ustun kengroq (33–35 — A–F)
        grid_top, grid = layout["grid_top"], layout["grid"]
        frame(left, grid, right - left, grid_top - grid)
        weights = [1, 1, 1, 1, 1.32]
        unit = (right - left) / sum(weights)
        column_left = [left + sum(weights[:index]) * unit + 5 for index in range(5)]
        for index, x in enumerate(column_left):
            frame(x, grid + 7, weights[index] * unit - 10, grid_top - grid - 14, line=0.7)
            rule(x + 20, grid + 7, x + 20, grid_top - 7)
        for index, (label, question_type, options, correct, ref) in enumerate(plan["pages"][0]):
            x = column_left[index // 7]
            y = grid_top - 7 - MS15_ROW / 2 - (index % 7) * MS15_ROW
            text(x + 10, y - 3.2, str(label), 9, REPORT_FONT_BOLD)
            wide = options > 4
            pitch, radius = (13.6, MS15_WIDE_RADIUS) if wide else (14.6, MS15_RADIUS)
            correct_letters = set((correct or "").split(","))
            for option in range(options):
                letter = chr(65 + option)
                option_x = x + 31 + option * pitch
                bubble(option_x, y, letter, with_answers and letter in correct_letters, radius, 6.0 if wide else 6.4)
                note({"ref": ref, "label": label, "kind": question_type, "letter": letter,
                      "x": option_x, "y": y, "r": radius})

        # 3) Diqqat va bo'yash namunasi | F.I.O.
        notes_top, notes, middle = layout["notes_top"], layout["notes"], layout["middle"]
        frame(left, notes, right - left, notes_top - notes)
        rule(middle, notes, middle, notes_top)
        center = (left + middle) / 2
        text(center, notes_top - 18, "TALABGOR DIQQATIGA!", 8.4)
        text(center, notes_top - 36, "SHARIKLI HAVORANG PASTALI RUCHKADAN", 7)
        text(center, notes_top - 46, "FOYDALANING", 7)
        text(center, notes_top - 60, "O‘ZGARTIRILGAN JAVOBLAR HISOBGA OLINMAYDI", 7, room=middle - left - 12)
        sample_x, sample_y = left + 14, notes + 36
        text(sample_x, sample_y, "DOIRAGA BO‘YASH NAMUNASI", 7.6, align="left")
        text(sample_x, sample_y - 18, "To‘g‘ri:", 7.2, align="left")
        text(sample_x + 62, sample_y - 18, "Noto‘g‘ri:", 7.2, align="left")
        sheet.setStrokeColor(DTM13_PEN)
        sheet.setFillColor(DTM13_PEN)
        sheet.circle(sample_x + 42, sample_y - 15.5, 5.4, stroke=0, fill=1)
        for index in range(4):
            x, y = sample_x + 116 + index * 16, sample_y - 15.5
            bubble(x, y, "", radius=5.4)
            sheet.setStrokeColor(DTM13_PEN)
            sheet.setFillColor(DTM13_PEN)
            sheet.setLineWidth(1.1)
            if index == 0:
                sheet.circle(x, y, 1.6, stroke=0, fill=1)
            elif index == 1:
                sheet.line(x - 3.4, y - 3.4, x + 3.4, y + 3.4)
                sheet.line(x - 3.4, y + 3.4, x + 3.4, y - 3.4)
            elif index == 2:
                sheet.line(x - 3.2, y, x - 0.8, y - 3)
                sheet.line(x - 0.8, y - 3, x + 3.6, y + 3.6)
            else:
                sheet.wedge(x - 5.4, y - 5.4, x + 5.4, y + 5.4, 90, 180, stroke=0, fill=1)
        text((middle + right) / 2, notes_top - 18, "TALABGOR TOMONIDAN TO‘LDIRILISHI SHART", 7.6,
             room=right - middle - 12)
        name_lines(middle, right, notes_top - 52, 32, ["(Familiya)", "(Ismi)", "(Otasining ismi)"])

        # 4) Dalolatnoma — imtihondagidek (nazoratchi, ya'ni o'qituvchi to'ldiradi)
        act_top = notes - 10
        frame(left, bottom, right - left, act_top - bottom)
        sheet.setFillColor(MS15_BANNER)
        sheet.rect(left, act_top - 16, right - left, 16, stroke=0, fill=1)
        text(width / 2, act_top - 11.5, "Diqqat! Quyidagi dalolatnoma guruh nazoratchilari tomonidan rasmiylashtiriladi",
             7.6, SHEET_SANS, color=colors.white)
        text(width / 2, act_top - 32, "TEST SINOVIDAN CHETLASHTIRISH DALOLATNOMASI", 9, REPORT_FONT_BOLD)
        for index, caption in enumerate(["(Chetlashtirish vaqti va sababi)", "Guruh nazoratchisi F.I.O. / imzo"]):
            y = act_top - 56 - index * 26
            rule(left + 12, y, right - 12, y)
            text(width / 2, y - 9, caption, 6.8, SHEET_SANS)
        return

    if page >= 3 and plan.get("science"):  # Kimyo, Biologiya: 41, 42, 43 — yozma ish
        _draw_ms15_work(sheet, plan, layout, page, student, text, frame, rule, note)
        return
    if page >= 3:  # Ona tili: esse (3-bet) va uning davomi (4-bet)
        _draw_ms15_essay(sheet, layout, page, student, text, frame, rule, note)
        return

    # ===== 2-bet =====
    # 1) Yotqizilgan ID bloki (chapda) | F.I.O.
    id_band = layout["id_band"]
    frame(left, id_band, right - left, header - id_band)
    split = left + MS15_ID_BLOCK[1] + 12
    rule(split, id_band, split, header)
    id_block((left + 6 + MS15_ID_BLOCK[1], id_band + 6), flat=True)
    middle = (split + right) / 2
    text(middle, header - 16, "TALABGOR TOMONIDAN TO‘LDIRILADI", 7.4)
    text(middle, header - 30, "(ID raqami 1-betdagi bilan bir xil bo‘lsin)", 6.8, SHEET_SANS)
    name_lines(split, right, header - 66, 40, ["(Familiya)", "(Ismi)"])

    if plan.get("native"):
        _draw_ms15_native(sheet, plan, layout, text, frame, rule, note)
        return
    if plan.get("science"):
        _draw_ms15_short(sheet, plan, layout, text, frame, rule, note)
        return
    if plan.get("cells"):
        _draw_ms15_cells(sheet, plan, layout, text, frame, rule, note)
        return

    # 2) "Talabgor diqqatiga" | "Diqqat, yozilmasin" (imtihondagidek — tekshiruvchi ustuni)
    warn, side = layout["warn"], 66
    frame(left, warn, right - left, 40)
    rule(right - side, warn, right - side, warn + 40)
    text((left + right - side) / 2, warn + 23, "TALABGOR DIQQATIGA", 11, REPORT_FONT_BOLD)
    text((left + right - side) / 2, warn + 9, "O‘ZGARTIRILGAN JAVOBLAR HISOBGA OLINMAYDI", 8, SHEET_SANS)
    text(right - side / 2, warn + 23, "DIQQAT", 8)
    text(right - side / 2, warn + 11, "YOZILMASIN", 6.6, SHEET_SANS)

    # 3) 36–45: a) va b) yozish kataklari — skaner rasm qilib qirqadi (AI yoki o'qituvchi o'qiydi)
    written_top = warn - 8
    frame(left, bottom, right - left, written_top - bottom)
    rule(right - side, bottom, right - side, written_top)
    rule(left + 24, bottom, left + 24, written_top)
    row = (written_top - bottom) / 10
    box_width = (right - side - left - 24 - 2 * 22 - 14) / 2
    box_height = row - 12
    for index, (label, _, _, _, ref) in enumerate(plan["pages"][1]):
        line, part = divmod(index, 2)
        row_bottom = written_top - (line + 1) * row
        if part == 0:
            if line:
                sheet.saveState()
                sheet.setDash(2, 2)
                rule(left, row_bottom + row, right, row_bottom + row)
                sheet.restoreState()
            text(left + 12, row_bottom + row / 2 - 3.5, label[:-1], 10, REPORT_FONT_BOLD)
            for mark in range(2):  # tekshiruvchi ustunidagi chiziqchalar
                x = right - side + 8 + mark * 27
                rule(x, row_bottom + row / 2 - 4, x + 20, row_bottom + row / 2 - 4)
        label_x = left + 30 + part * (box_width + 26)
        text(label_x, row_bottom + row / 2 - 3.5, f"{label[-1]})", 9.5, REPORT_FONT_BOLD, align="left")
        box_left, box_bottom = label_x + 14, row_bottom + 6
        # Ichi daftardagidek kvadrat katakchalar (~5 mm) — juda och
        cells = max(1, round(box_height / 14))
        size = box_height / cells
        sheet.saveState()
        path = sheet.beginPath()
        path.roundRect(box_left, box_bottom, box_width, box_height, 6)
        sheet.clipPath(path, stroke=0, fill=0)
        sheet.setStrokeColor(MS15_FAINT)
        sheet.setLineWidth(0.5)
        for step in range(1, cells):
            sheet.line(box_left, box_bottom + step * size, box_left + box_width, box_bottom + step * size)
        columns = int(box_width / size)
        offset = (box_width - columns * size) / 2
        for step in range(columns + 1):
            x = box_left + offset + step * size
            sheet.line(x, box_bottom, x, box_bottom + box_height)
        sheet.restoreState()
        frame(box_left, box_bottom, box_width, box_height, radius=6)
        note({"ref": ref, "label": label, "kind": "write", "letter": "",
              "x": box_left + box_width / 2, "y": box_bottom + box_height / 2, "w": box_width, "h": box_height,
              "r": 0})


def _draw_ms15_cells(sheet, plan: dict, layout: dict, text, frame, rule, note) -> None:
    """
    Tarix 2-beti (ID bandidan pastda): eslatma va to'ldirish namunasi, so'ng 36–45 — har savolda a) va b)
    ostma-ost, har biri bir qator katakchalar (bitta katakka bitta belgi). Skaner har qatorni rasm qilib qirqadi.
    Katakchalar orasidagi chiziq och — bo'sh qator "bo'sh" deb o'qiladi; qator ramkasi to'q (skaner uchun chegara).
    """

    left, right, bottom = layout["left"], layout["right"], layout["bottom"]
    # Geografiya: namunasi boshqa va o'ngda "Diqqat! Yozilmasin" ustuni (tekshiruvchi uchun, imtihondagidek)
    geo = plan.get("geo")
    note_text, sample = (MS15_GEO_NOTE, MS15_GEO_SAMPLE) if geo else (MS15_CELL_NOTE, MS15_CELL_SAMPLE)
    side = 70 if geo else 0

    # 1) Eslatma va namuna
    note_top = layout["id_band"] - 8
    note_bottom = note_top - 58
    frame(left, note_bottom, right - left, note_top - note_bottom)
    words, lines, current = note_text.split(), [], ""
    for word in words:  # ikki qatorga bo'linadi (ramka eniga qarab)
        trial = f"{current} {word}".strip()
        if current and stringWidth(trial, SHEET_SANS, 7.6) > right - left - 16:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)
    for index, line in enumerate(lines[:2]):
        text(left + 8, note_top - 12 - index * 10, line, 7.6, SHEET_SANS, "left", colors.HexColor("#333333"))
    cell = 18.0 if geo else 14.0
    sample_left = (left + right - len(sample) * cell) / 2
    sample_bottom = note_bottom + 7
    for index, letter in enumerate(sample):
        x = sample_left + index * cell
        if letter:
            frame(x, sample_bottom, cell, 17, line=0.6)
            text(x + cell / 2, sample_bottom + 5, letter, 8, REPORT_FONT, color=colors.black)

    # 2) 36–45: chapda savol raqami, o'ngda a) va b) qatorlari
    written_top = note_bottom - 8
    frame(left, bottom, right - left, written_top - bottom)
    number_width = 28
    rule(left + number_width, bottom, left + number_width, written_top)
    block = (written_top - bottom) / 10
    strip_height = (block - 12) / 2
    label_width = 18
    strip_left = left + number_width + 6
    cells_left = strip_left + label_width
    cells_right = right - side - 6
    count = max(1, round((cells_right - cells_left) / 15.5))
    size = (cells_right - cells_left) / count
    if side:
        rule(right - side, bottom, right - side, written_top)
    for index, (label, _, _, _, ref) in enumerate(plan["pages"][1]):
        line, part = divmod(index, 2)
        block_top = written_top - line * block
        if part == 0:
            if line:
                rule(left, block_top, right, block_top)
            text(left + number_width / 2, block_top - block / 2 - 3.5, label[:-1], 10, REPORT_FONT_BOLD)
            if side:  # "Diqqat! Yozilmasin" va tekshiruvchi chiziqchalari
                middle = right - side / 2
                text(middle, block_top - 14, "DIQQAT!", 7, REPORT_FONT_BOLD)
                text(middle, block_top - 23, "YOZILMASIN", 6.4, REPORT_FONT_BOLD)
                for mark in range(2):
                    x = right - side + 8 + mark * 29
                    rule(x, block_top - block + 10, x + 22, block_top - block + 10)
        strip_bottom = block_top - 4 - (part + 1) * strip_height - part * 4
        # Katakchalar orasidagi chiziqlar (och), keyin ramkalar (to'q)
        sheet.setStrokeColor(MS15_CELL_DIVIDER)
        sheet.setLineWidth(0.6)
        for step in range(1, count):
            x = cells_left + step * size
            sheet.line(x, strip_bottom, x, strip_bottom + strip_height)
        frame(strip_left, strip_bottom, label_width, strip_height, line=0.7)
        frame(cells_left, strip_bottom, cells_right - cells_left, strip_height, line=0.7)
        text(strip_left + label_width / 2, strip_bottom + strip_height / 2 - 3, f"{label[-1]})", 8, REPORT_FONT,
             color=colors.HexColor("#333333"))
        note({"ref": ref, "label": label, "kind": "write", "letter": "",
              "x": (cells_left + cells_right) / 2, "y": strip_bottom + strip_height / 2,
              "w": cells_right - cells_left, "h": strip_height, "r": 0})


def _draw_ms15_native(sheet, plan: dict, layout: dict, text, frame, rule, note) -> None:
    """
    Ona tili 2-beti (ID bandidan pastda): eslatma va namuna katakchalari; 36–39 — bitta qator, 40–44 — a) va b)
    qatorlari, har qatorda 14 ta alohida katakcha; o'ngda "Diqqat! Yozilmasin" ustuni (imtihondagidek).
    Katakchalar chizig'i och — bo'sh qator skanerda "bo'sh" deb o'qiladi.
    """

    left, right, bottom = layout["left"], layout["right"], layout["bottom"]
    side_left, side_right = right - 72, right - 4
    dark = colors.HexColor("#333333")

    # 1) Eslatma, namuna va "Diqqat! Yozilmasin" belgisi
    note_top = layout["id_band"] - 8
    note_bottom = note_top - 70
    frame(left, note_bottom, right - left, note_top - note_bottom)
    words, lines, current = MS15_NATIVE_NOTE.split(), [], ""
    for word in words:
        trial = f"{current} {word}".strip()
        if current and stringWidth(trial, SHEET_SANS, 7.6) > right - left - 16:
            lines.append(current)
            current = word
        else:
            current = trial
    lines.append(current)
    for index, line in enumerate(lines[:2]):
        text(left + 8, note_top - 12 - index * 10, line, 7.6, SHEET_SANS, "left", dark)
    cell = 15.0
    for row, letters in enumerate(MS15_NATIVE_SAMPLE):
        y = note_top - 44 - row * 19
        for index, letter in enumerate(letters):
            x = left + 70 + index * (cell + 3)
            if letter:
                frame(x, y, cell, cell, line=0.6)
                text(x + cell / 2, y + 4.5, letter, 8, REPORT_FONT, color=colors.black)
    sheet.setFillColor(MS15_INK)
    sheet.roundRect(side_left, note_bottom + 14, side_right - side_left, 30, 3, stroke=0, fill=1)
    text((side_left + side_right) / 2, note_bottom + 32, "DIQQAT!", 7.4, color=colors.white)
    text((side_left + side_right) / 2, note_bottom + 21, "YOZILMASIN", 6.8, SHEET_SANS, color=colors.white)

    # 2) 36–44
    items = plan["pages"][1]
    groups: list[list] = []
    for item in items:
        number = item[0].rstrip("ab")
        if groups and groups[-1][0][0].rstrip("ab") == number:
            groups[-1].append(item)
        else:
            groups.append([item])
    written_top = note_bottom - 8
    strip, inner = 21.0, 4.0
    used = len(items) * strip + sum(len(group) - 1 for group in groups) * inner + 16
    gap = (written_top - bottom - used) / max(1, len(groups) - 1)
    # "Yozilmasin" ustuni — och sariq fon (tekshiruvchi uchun)
    sheet.setFillColor(colors.HexColor("#FFF3D6"))
    sheet.rect(side_left, bottom, side_right - side_left, written_top - bottom, stroke=0, fill=1)
    frame(side_left, bottom, side_right - side_left, written_top - bottom)
    cells_left = left + 56
    pitch = cell_size = strip
    pitch += 4
    cells_right = cells_left + MS15_NATIVE_CELLS * pitch - 4
    y = written_top - 8
    for group in groups:
        for index, (label, _, _, _, ref) in enumerate(group):
            strip_bottom = y - strip
            number, part = label.rstrip("ab"), label[len(label.rstrip("ab")):]
            caption = (f"{number}." + (f" {part})" if part else "")) if index == 0 else f"{part})"
            text(cells_left - 8, strip_bottom + 6.5, caption, 9.5, REPORT_FONT_BOLD, "right", colors.black)
            sheet.setStrokeColor(MS15_CELL_DIVIDER)
            sheet.setLineWidth(1.3)
            sheet.setFillColor(colors.white)
            for cell_index in range(MS15_NATIVE_CELLS):
                sheet.rect(cells_left + cell_index * pitch, strip_bottom, cell_size, cell_size, stroke=1, fill=0)
            # Tekshiruvchi chiziqchasi
            sheet.saveState()
            sheet.setDash(2, 1.5)
            sheet.setStrokeColor(colors.HexColor("#9A9A9A"))
            sheet.setLineWidth(0.6)
            sheet.line(side_left + 12, strip_bottom + 4, side_right - 12, strip_bottom + 4)
            sheet.restoreState()
            note({"ref": ref, "label": label, "kind": "write", "letter": "",
                  "x": (cells_left + cells_right) / 2, "y": strip_bottom + strip / 2,
                  "w": cells_right - cells_left, "h": strip, "r": 0})
            y = strip_bottom - inner
        y += inner - gap


def _ms15_cell_strip(sheet, frame, x1: float, x2: float, bottom: float, height: float, cell: float = 15.5) -> None:
    """Bir qator katakcha (bitta katakka bitta belgi): oraliq chiziqlari och (skaner uchun "qog'oz"), ramka to'q."""

    count = max(1, round((x2 - x1) / cell))
    size = (x2 - x1) / count
    sheet.setStrokeColor(MS15_CELL_DIVIDER)
    sheet.setLineWidth(0.6)
    for step in range(1, count):
        sheet.line(x1 + step * size, bottom, x1 + step * size, bottom + height)
    frame(x1, bottom, x2 - x1, height, radius=3, line=0.8)


def _ms15_wrap(value: str, width: float, size: float) -> list[str]:
    lines, current = [], ""
    for word in value.split():
        trial = f"{current} {word}".strip()
        if current and stringWidth(trial, SHEET_SANS, size) > width:
            lines.append(current)
            current = word
        else:
            current = trial
    return lines + [current]


def _draw_ms15_short(sheet, plan: dict, layout: dict, text, frame, rule, note) -> None:
    """Kimyo/Biologiya 2-beti (ID bandidan pastda): eslatma va namuna; 36–40 — har biri bir qator katakcha."""

    left, right, bottom = layout["left"], layout["right"], layout["bottom"]
    dark = colors.HexColor("#333333")
    note_top = layout["id_band"] - 8
    note_bottom = note_top - 64
    frame(left, note_bottom, right - left, note_top - note_bottom)
    for index, line in enumerate(_ms15_wrap(MS15_SCIENCE_NOTE, right - left - 16, 7.6)[:2]):
        text(left + 8, note_top - 12 - index * 10, line, 7.6, SHEET_SANS, "left", dark)
    text(left + 8, note_bottom + 14, "Namuna:", 8, REPORT_FONT_BOLD, "left", dark)
    cell = 15.0
    sample_left = left + 60
    for index, letter in enumerate(MS15_SCIENCE_SAMPLE):
        x = sample_left + index * cell
        if letter:
            frame(x, note_bottom + 8, cell, 20, line=0.6)
            text(x + cell / 2, note_bottom + 14, letter, 9, REPORT_FONT, color=colors.black)

    written_top = note_bottom - 8
    frame(left, bottom, right - left, written_top - bottom)
    items = plan["pages"][1]
    block = (written_top - bottom) / len(items)
    height = min(40.0, block - 24)
    for index, (label, _, _, _, ref) in enumerate(items):
        block_top = written_top - index * block
        if index:
            sheet.saveState()
            sheet.setDash(2, 2)
            rule(left, block_top, right, block_top)
            sheet.restoreState()
        strip_bottom = block_top - block / 2 - height / 2
        text(left + 22, strip_bottom + height / 2 - 5, label, 15, REPORT_FONT_BOLD)
        cells_left, cells_right = left + 44, right - 10
        _ms15_cell_strip(sheet, frame, cells_left, cells_right, strip_bottom, height, cell=17)
        note({"ref": ref, "label": label, "kind": "write", "letter": "",
              "x": (cells_left + cells_right) / 2, "y": strip_bottom + height / 2,
              "w": cells_right - cells_left, "h": height, "r": 0})


def _draw_ms15_work(sheet, plan: dict, layout: dict, page: int, student: tuple | None, text, frame, rule, note) -> None:
    """
    Kimyo/Biologiya yozma ishi (41, 42, 43 — har biri alohida bet): tepada har bandning yakuniy javobi uchun
    katakchali qator (41.1, 41.2 … — skaner o'qiydi, kalit bilan solishtiriladi), pastida yechim uchun katakli
    qog'oz (butunligicha rasm — o'qituvchi ko'radi).
    """

    left, right, bottom, header = layout["left"], layout["right"], layout["bottom"], layout["header"]
    items = plan["pages"][page - 1]
    number = items[0][0].split(".")[0]
    dark = colors.HexColor("#333333")

    name_bottom = header - 8 - 26
    frame(left, name_bottom, right - left, 26)
    text(left + 10, name_bottom + 9, "Familiya, ismi:", 8, align="left")
    rule(left + 92, name_bottom + 7, right - 12, name_bottom + 7)
    if student:
        text(left + 100, name_bottom + 10, student[1][:60], 11, REPORT_FONT_BOLD, "left", colors.black)

    # 1) Band javoblari
    step, height = 26.0, 20.0
    answers_top = name_bottom - 8
    answers_bottom = answers_top - 24 - len(items) * step
    frame(left, answers_bottom, right - left, answers_top - answers_bottom)
    text(left + 10, answers_top - 15, f"{number}", 15, REPORT_FONT_BOLD, "left")
    text(left + 40, answers_top - 14, "Har bandning YAKUNIY javobi — o'z qatoriga (bitta katakka bitta belgi)", 7.6,
         SHEET_SANS, "left", dark)
    for index, (label, _, _, _, ref) in enumerate(items):
        strip_bottom = answers_top - 24 - (index + 1) * step + (step - height) / 2
        text(left + 36, strip_bottom + 6, label, 9.5, REPORT_FONT_BOLD, "right", colors.black)
        cells_left, cells_right = left + 44, right - 10
        _ms15_cell_strip(sheet, frame, cells_left, cells_right, strip_bottom, height)
        note({"ref": ref, "label": label, "kind": "write", "letter": "",
              "x": (cells_left + cells_right) / 2, "y": strip_bottom + height / 2,
              "w": cells_right - cells_left, "h": height, "r": 0})

    # 2) Yechim — katakli qog'oz (5 mm)
    work_top = answers_bottom - 8
    frame(left, bottom, right - left, work_top - bottom)
    text(left + 10, work_top - 13, "Yechim", 8, REPORT_FONT_BOLD, "left")
    grid_left, grid_right, grid_top, grid_bottom = left + 8, right - 8, work_top - 20, bottom + 8
    size = 72 / 25.4 * 5
    sheet.setLineWidth(0.5)
    index = 0
    y = grid_top
    while y >= grid_bottom - 0.1:
        sheet.setStrokeColor(MS15_FAINT if index % 2 else colors.HexColor("#F3C29E"))
        sheet.line(grid_left, y, grid_right, y)
        y -= size
        index += 1
    index, x = 0, grid_left
    while x <= grid_right + 0.1:
        sheet.setStrokeColor(MS15_FAINT if index % 2 else colors.HexColor("#F3C29E"))
        sheet.line(x, grid_bottom, x, grid_top)
        x += size
        index += 1
    note({"ref": "solution", "label": f"{number} — yechim", "kind": "page_image", "letter": str(page),
          "x": (left + right) / 2, "y": (work_top + bottom) / 2, "w": right - left, "h": work_top - bottom, "r": 0})


def _draw_ms15_essay(sheet, layout: dict, page: int, student: tuple | None, text, frame, rule, note) -> None:
    """
    Ona tili essesi: 3-bet ("ESSE") va 4-bet (davomi) — chiziqli. Har 10 qator rangli (sanash oson).
    Skaner chiziqli qismni butunligicha rasm qiladi: u o'quvchining qo'lda yozgan essesi bo'lib saqlanadi.
    """

    left, right, bottom, header = layout["left"], layout["right"], layout["bottom"], layout["header"]
    # Kimniki — tepada ism (bet aralashsa ham o'qituvchi ko'radi)
    name_bottom = header - 8 - 26
    frame(left, name_bottom, right - left, 26)
    text(left + 10, name_bottom + 9, "Familiya, ismi:", 8, align="left")
    rule(left + 92, name_bottom + 7, right - 12, name_bottom + 7)
    if student:
        text(left + 100, name_bottom + 10, student[1][:60], 11, REPORT_FONT_BOLD, "left", colors.black)

    essay_top = name_bottom - 8
    frame(left, bottom, right - left, essay_top - bottom)
    first = essay_top - 14
    if page == 3:
        sheet.setFillColor(colors.HexColor("#FCE3D0"))
        sheet.rect(left + 6, essay_top - 20, right - left - 12, 14, stroke=0, fill=1)
        text((left + right) / 2, essay_top - 16, "ESSE", 9, REPORT_FONT_BOLD)
        first = essay_top - 34
    else:
        path = sheet.beginPath()  # ▶ — davomi
        path.moveTo(left + 10, essay_top - 6)
        path.lineTo(left + 10, essay_top - 16)
        path.lineTo(left + 19, essay_top - 11)
        path.close()
        sheet.setFillColor(colors.HexColor("#3D5DB8"))
        sheet.drawPath(path, stroke=0, fill=1)
        first = essay_top - 20
    step = (first - bottom - 10) / (MS15_ESSAY_LINES - 1)
    for index in range(MS15_ESSAY_LINES):
        y = first - index * step
        number = (page - 3) * MS15_ESSAY_LINES + index  # ikkala bet bo'ylab qator raqami
        sheet.setStrokeColor(MS15_ESSAY_RED if (number // 10) % 2 else MS15_ESSAY_LINE)
        sheet.setLineWidth(0.6)
        sheet.line(left + 10, y, right - 10, y)
    note({"ref": "essay", "label": f"Esse — {page - 2}-bet", "kind": "page_image", "letter": str(page - 2),
          "x": (left + right) / 2, "y": (essay_top + bottom) / 2, "w": right - left, "h": essay_top - bottom, "r": 0})


def _draw_compact_sheets(sheet, test: Test, with_answers: bool, bubbles: list | None, students: list | None,
                         variants: list[str] | None, key_override: dict | None, key_variant: str | None) -> None:
    """Ixcham titullar: bitta A4 ga sig'ganicha (ostma-ost, orasida qirqish chizig'i)."""

    version = test.sheet_version or 1
    plan = (_evalbee_plan if version >= 8 else _grid_plan if version >= 7 else _compact_plan)(
        test, variants, key_override)
    draw_card = _draw_evalbee_card if version >= 8 else _draw_grid_card if version >= 7 else _draw_compact_card
    if plan.get("dtm13"):
        draw_card = _draw_dtm13_card
    if plan.get("ms15"):
        draw_card = _draw_ms15_card
    page_count = len(plan["pages"])
    per_sheet = int(A4[1] // (plan["height"] - 1)) if page_count == 1 and version < 8 else 1
    left = (A4[0] - plan["width"]) / 2  # titul A4 dan tor bo'lsa — o'rtada
    # Bo'sh titul — bitta (o'qituvchi kerakli sonda chop etadi); ismli — har o'quvchiga bittadan
    cards = list(students) if students else [None]
    slot = 0
    for index, student in enumerate(cards):
        for page in range(1, page_count + 1):
            if slot == per_sheet:
                sheet.showPage()
                slot = 0
            offset = A4[1] - (slot + 1) * plan["height"]
            sheet.saveState()
            sheet.translate(left, offset)
            draw_card(sheet, test, plan, page, with_answers, student,
                      bubbles if index == 0 else None, variants, key_variant)
            sheet.restoreState()
            if per_sheet == 1:
                _draw_sheet_brand(sheet, offset)
            if slot:
                # Qirqish chizig'i — titullar orasida
                sheet.saveState()
                sheet.setDash(4, 4)
                sheet.setStrokeColor(colors.grey)
                sheet.line(12, offset + plan["height"], A4[0] - 12, offset + plan["height"])
                sheet.restoreState()
            slot += 1


def generate_answer_sheet_pdf(test: Test, with_answers: bool = False, bubbles: list | None = None,
                              students: list[tuple] | None = None, variants: list[str] | None = None,
                              key_override: dict | None = None, key_variant: str | None = None) -> bytes:
    """
    "Javoblar varaqasi" — o'quvchilarga tarqatiladigan bo'sh blank.

    O'quvchi javoblarini shu varaqada belgilaydi, o'qituvchi esa ularni
    skanerlaydi yoki Mini App'dagi "O'quvchilar" bo'limiga kiritadi va bot avtomatik tekshiradi.

    Hamma savol iloji boricha bitta varaqqa joylashtiriladi (qarang: SHEET_LAYOUTS).

    with_answers=True bo'lsa, to'g'ri javoblar bo'yalgan "kalit" varaqasi
    chiqadi — o'qituvchi qo'lda tekshirishi uchun.

    students = [(raqam, ism), ...] berilsa — har o'quvchiga ismi yozilgan va raqami
    bo'yalgan alohida varaq (skaner ularni o'zi taniydi).
    """

    if with_answers:
        # O'qituvchi kaliti — titul emas, ro'yxat: 1 — B, 2 — C ... (tagma-tag o'qish qulay)
        return generate_answer_key_pdf(test, key_override, key_variant)
    buffer = io.BytesIO()
    sheet = pdf_canvas.Canvas(buffer, pagesize=A4)
    if (test.sheet_version or 1) >= 6:
        _draw_compact_sheets(sheet, test, with_answers, bubbles, students, variants, key_override, key_variant)
        sheet.save()
        return buffer.getvalue()
    for index, student in enumerate(students or [None]):
        if index:
            sheet.showPage()
        _draw_answer_sheet(sheet, test, with_answers, bubbles if index == 0 else None, student,
                           variants, key_override, key_variant)
    sheet.save()
    return buffer.getvalue()


# ---------- Javoblar kaliti ("kartochkalar" dizayni; PDF va rasm bir xil — rasm PDF sahifasining o'zi) ----------

try:
    pdfmetrics.registerFont(TTFont("KeySans", str(FONTS_DIR / "DejaVuSans.ttf")))
    pdfmetrics.registerFont(TTFont("KeySans-Bold", str(FONTS_DIR / "DejaVuSans-Bold.ttf")))
    KEY_SANS, KEY_SANS_BOLD = "KeySans", "KeySans-Bold"
except Exception:
    logging.getLogger("backend").warning("Kalit shrifti topilmadi — Helvetica ishlatiladi", exc_info=True)
    KEY_SANS, KEY_SANS_BOLD = "Helvetica", "Helvetica-Bold"

KEY_MARGIN = 40
KEY_BOTTOM = 52                       # pastda imzo qatoriga joy
KEY_TILE_ROW = 7                      # yopiq test: qatorda nechta kartochka
KEY_TILE_H, KEY_TILE_GAP = 34, 7
KEY_GRID = colors.HexColor("#DCE5E2")
KEY_TILE = colors.HexColor("#F2F6F5")
KEY_LIGHT = colors.HexColor("#C9D6DA")


def _key_kind(test: Test) -> str:
    if test.test_type == "dtm":
        return "DTM testi"
    return "Oddiy test" if test.test_type == "boshlangich" else "Milliy sertifikat"


def _answer_key_data(test: Test, key_override: dict | None) -> dict:
    """Kalit uchun: yopiq savollar (raqam, javob), izohlar va yozma savollar (raqam, [qismlar javoblari])."""

    items = _answer_sheet_items(test, key_override)
    closed = [(label, (correct or "").replace(",", ", ") or "—") for label, kind, _, correct, _ in items if kind != "text"]
    notes = []
    for band in _group_similar_questions([item for item in items if item[1] != "text"]):
        span = f"{band[0][0]}–{band[-1][0]}" if band[0][0] != band[-1][0] else band[0][0]
        if any(item[1] == "multi" for item in band):
            notes.append(f"{span}: bir nechta to'g'ri javob")
        elif band[0][2] > 4:
            notes.append(f"{span}: moslashtirish (A–{chr(64 + min(band[0][2], 26))})")
        else:
            notes.append(f"{span}: bitta to'g'ri javob")
    written: list[tuple[str, list[list[str]]]] = []
    for label, kind, _, correct, _ in items:
        if kind != "text":
            continue
        base = re.sub(r"[a-z]+$", "", label) or label
        answers = [variant for variant in _split_accepted_variants(correct or "") if variant.strip()]
        if written and written[-1][0] == base and re.search(r"[a-z]$", label):
            written[-1][1].append(answers)
        else:
            written.append((base, [answers]))
    return {"closed": closed, "notes": notes, "written": written,
            "parts": max([len(parts) for _, parts in written] or [1])}


KEY_DTM_COLUMN = 15  # DTM kalitida bitta ustunda ko'pi bilan (asosiy fan 30 ta — 15 tadan 2 ustun)


def _dtm_key_columns(test: Test, closed: list[tuple[str, str]]) -> list[tuple[str, list[list[tuple[str, str]]]]] | None:
    """
    DTM kaliti fanlar bo'yicha: [(fan nomi, [ustun, ...]), ...] — har ustunda ko'pi bilan 15 ta savol.
    Bo'limlar savollarni to'liq va bir martadan qamrab olmasa yoki ustunlar 7 tadan oshsa — None (oddiy tartib).
    """

    if test.test_type != "dtm":
        return None
    by_label = dict(closed)
    sections = _test_sections(test)
    columns, used = [], set()
    for index, section in enumerate(sections):
        labels = [str(number) for number in section.get("numbers", []) if str(number) in by_label]
        if not labels or used & set(labels):
            return None
        used |= set(labels)
        name = str(section.get("name", "")).replace(" (majburiy)", "").strip()
        if index >= 3 and not re.fullmatch(r"\d+-asosiy fan", name):
            name = f"{index - 2}-asosiy fan: {name}"
        items = [(label, by_label[label]) for label in labels]
        columns.append((name, [items[start: start + KEY_DTM_COLUMN] for start in range(0, len(items), KEY_DTM_COLUMN)]))
    if len(used) != len(closed) or sum(len(chunks) for _, chunks in columns) > KEY_TILE_ROW:
        return None
    return columns


def _key_answer_box(variants: list[str], size: float, max_width: float):
    """Yozma javob formulasi (bir nechta qabul qilinadigan javob — " yoki " bilan), katakka sig'adigan o'lchamda."""

    def build(current: float):
        boxes = []
        for index, variant in enumerate(variants or ["—"]):
            if index:
                boxes.append(keymath.Text(" yoki ", KEY_SANS, current * 0.7, REPORT_MUTED))
            boxes.append(keymath.math_box(variant, current, (REPORT_FONT, REPORT_FONT_ITALIC), REPORT_BLUE,
                                          plain=_latex_to_plain))
        return keymath.Row(boxes)

    return keymath.fit(build, size, max_width)


class _KeyCanvas(_BrandCanvas):
    """Kalit sahifasi: sarlavhani kalitning o'zi chizadi; pastda shior va "Kod · sana · 1 / 2"."""

    footer_right = ""

    def _draw_header(self) -> None:
        pass

    def _draw_footer(self, total: int) -> None:
        self.saveState()
        self.setFillColor(REPORT_MUTED)
        self.setFont(KEY_SANS, 8)
        self.drawString(KEY_MARGIN, 26, f"Chittak — {REPORT_SLOGAN}")
        page = f" · {self._pageNumber} / {total}" if total > 1 else ""
        self.drawRightString(A4[0] - KEY_MARGIN, 26, self.footer_right + page)
        self.restoreState()


def generate_answer_key_pdf(test: Test, key_override: dict | None = None, key_variant: str | None = None) -> bytes:
    """
    O'qituvchi uchun javoblar kaliti — "kartochkalar" dizayni: tepada to'q sarlavha (Chittak, o'qituvchi,
    test turi va sana), ma'lumot qatori (test, fan, kod, savollar soni), yopiq savollar — har biri alohida
    kartochkada (raqam va javob), yozma savollar — ikki ustunda, formulalar kitobdagidek (kasr, ildiz).
    Sig'masa — keyingi sahifaga o'tadi. Rasm varianti shu PDF sahifalarining o'zi (test_answer_key_pngs).
    """

    width, height = A4
    left, right = KEY_MARGIN, width - KEY_MARGIN
    data = _answer_key_data(test, key_override)
    teacher = test.teacher.user.full_name if test.teacher and test.teacher.user else ""
    date = _local_date(test.created_at or utcnow())
    variant_text = f" — {key_variant} variant" if key_variant else ""

    buffer = io.BytesIO()
    canvas_class = type("KeyCanvas", (_KeyCanvas,), {"footer_right": f"Kod {test.code} · {date}"})
    sheet = canvas_class(buffer, pagesize=A4)
    sheet.setTitle(f"{test.title} — javoblar kaliti{variant_text}")

    def clip(text: str, font: str, size: float, max_width: float) -> str:
        while stringWidth(text, font, size) > max_width and len(text) > 2:
            text = text[:-2].rstrip() + "…"
        return text

    def banner() -> float:
        top = height - 34
        sheet.setFillColor(REPORT_BLUE)
        sheet.roundRect(left, top - 56, right - left, 56, 10, stroke=0, fill=1)
        try:
            sheet.drawImage(str(FRONTEND_DIR / "img" / "chittak-icon.png"), left + 14, top - 45, 34, 34, mask="auto")
        except Exception:
            pass
        sheet.setFillColor(colors.white)
        sheet.setFont(KEY_SANS_BOLD, 15)
        sheet.drawString(left + 58, top - 25, "Chittak")
        sheet.setFillColor(KEY_LIGHT)
        sheet.setFont(KEY_SANS, 8.5)
        sheet.drawString(left + 58, top - 40, clip(teacher, KEY_SANS, 8.5, 190))
        sheet.setFillColor(REPORT_GREEN)
        sheet.setFont(KEY_SANS_BOLD, 17)
        sheet.drawRightString(right - 16, top - 26, "JAVOBLAR KALITI")
        sheet.setFillColor(KEY_LIGHT)
        sheet.setFont(KEY_SANS, 8.5)
        sheet.drawRightString(right - 16, top - 41, f"{_key_kind(test)}{variant_text} · {date}")
        return top - 56

    def chips(y: float) -> float:
        values = [("TEST", test.title), ("FAN", test.subject), ("KOD", test.code + (f" · {key_variant}" if key_variant else "")),
                  ("SAVOLLAR", f"{_question_count(test)} ta")]
        chip_w = (right - left - 3 * 8) / 4
        for index, (name, value) in enumerate(values):
            x = left + index * (chip_w + 8)
            sheet.setFillColor(KEY_TILE)
            sheet.roundRect(x, y - 30, chip_w, 30, 6, stroke=0, fill=1)
            sheet.setFillColor(REPORT_MUTED)
            sheet.setFont(KEY_SANS, 7)
            sheet.drawString(x + 9, y - 11, name)
            sheet.setFillColor(REPORT_BLUE)
            sheet.setFont(KEY_SANS_BOLD, 10.5)
            sheet.drawString(x + 9, y - 23.5, clip(value or "—", KEY_SANS_BOLD, 10.5, chip_w - 16))
        return y - 30

    def heading(y: float, name: str, note: str) -> None:
        sheet.setFillColor(REPORT_BLUE)
        sheet.setFont(KEY_SANS_BOLD, 11)
        sheet.drawString(left, y, name)
        sheet.setFillColor(REPORT_MUTED)
        sheet.setFont(KEY_SANS, 9)
        offset = stringWidth(name, KEY_SANS_BOLD, 11) + 8
        sheet.drawString(left + offset, y, clip(note, KEY_SANS, 9, right - left - offset))

    def new_page() -> float:
        sheet.showPage()
        return banner() - 26

    y = chips(banner() - 12) - 26

    closed = data["closed"]
    tile_w = (right - left - (KEY_TILE_ROW - 1) * KEY_TILE_GAP) / KEY_TILE_ROW
    label_w = 22 if max([len(label) for label, _ in closed] or [1]) <= 2 else 28

    def tile(x: float, tile_y: float, tile_h: float, label: str, answer: str) -> None:
        sheet.setStrokeColor(KEY_GRID)
        sheet.setLineWidth(0.8)
        sheet.setFillColor(colors.white)
        sheet.roundRect(x, tile_y, tile_w, tile_h, 6, stroke=1, fill=1)
        sheet.setFillColor(KEY_TILE)
        sheet.roundRect(x, tile_y, label_w, tile_h, 6, stroke=0, fill=1)
        sheet.rect(x + label_w - 6, tile_y, 6, tile_h, stroke=0, fill=1)
        sheet.setFillColor(REPORT_MUTED)
        sheet.setFont(KEY_SANS_BOLD, 8.5 if len(label) <= 3 else 7)
        sheet.drawCentredString(x + label_w / 2, tile_y + tile_h / 2 - 3, label)
        size = 15 if tile_h >= KEY_TILE_H else 13.5
        while size > 7 and stringWidth(answer, KEY_SANS_BOLD, size) > tile_w - label_w - 6:
            size -= 0.5
        sheet.setFillColor(REPORT_TABLE_HEAD)
        sheet.setFont(KEY_SANS_BOLD, size)
        sheet.drawCentredString(x + label_w + (tile_w - label_w) / 2, tile_y + tile_h / 2 - size * 0.35, answer)

    dtm_columns = _dtm_key_columns(test, closed)
    if dtm_columns:
        # DTM: har fan o'z ustunida — majburiy fanlar 10 tadan, asosiy fanlar 15 tadan 2 ustun
        heading(y, "Yopiq testlar", f"{closed[0][0]}–{closed[-1][0]}-savollar · har to'g'ri javob o'z fani bali bilan")
        y -= 12
        step_x = tile_w + KEY_TILE_GAP
        for group, first, last in (("Majburiy fanlar", 0, 3), ("Asosiy fanlar", 3, len(dtm_columns))):
            columns_before = sum(len(chunks) for _, chunks in dtm_columns[:first])
            count = sum(len(chunks) for _, chunks in dtm_columns[first:last])
            if not count:
                continue
            x0, x1 = left + columns_before * step_x, left + (columns_before + count) * step_x - KEY_TILE_GAP
            sheet.setFillColor(REPORT_BLUE)
            sheet.setFont(KEY_SANS_BOLD, 9)
            sheet.drawCentredString((x0 + x1) / 2, y - 8, group.upper())
            sheet.setStrokeColor(REPORT_GREEN)
            sheet.setLineWidth(1.2)
            sheet.line(x0, y - 13, x1, y - 13)
        y -= 17
        column, tile_h, step_y = 0, 30, 35
        rows = max(len(chunk) for _, chunks in dtm_columns for chunk in chunks)
        for name, chunks in dtm_columns:
            x0 = left + column * step_x
            span_w = len(chunks) * step_x - KEY_TILE_GAP
            # Fan nomi ustun(lar) tepasida; sig'masa — ikki qatorga
            sheet.setFillColor(REPORT_MUTED)
            words, lines = name.split(), [""]
            for word in words:
                trial = f"{lines[-1]} {word}".strip()
                if lines[-1] and stringWidth(trial, KEY_SANS_BOLD, 8) > span_w - 4 and len(lines) < 2:
                    lines.append(word)
                else:
                    lines[-1] = trial
            for index, line in enumerate(lines):
                font_size = 8
                while font_size > 6 and stringWidth(line, KEY_SANS_BOLD, font_size) > span_w - 2:
                    font_size -= 0.5
                sheet.setFont(KEY_SANS_BOLD, font_size)
                sheet.drawCentredString(x0 + span_w / 2, y - 9 - index * 9.5 + (4.75 if len(lines) > 1 else 0), line)
            for chunk in chunks:
                x = left + column * step_x
                for row, (label, answer) in enumerate(chunk):
                    tile(x, y - 22 - tile_h - row * step_y, tile_h, label, answer)
                column += 1
        y -= 22 + rows * step_y + 16
    elif closed:
        span = f"{closed[0][0]}–{closed[-1][0]}-savollar" if len(closed) > 1 else f"{closed[0][0]}-savol"
        heading(y, "Yopiq testlar", " · ".join([span, *data["notes"]]))
        y -= 10
        step = KEY_TILE_H + KEY_TILE_GAP
        remaining = closed
        while remaining:
            if y - KEY_TILE_H < KEY_BOTTOM:
                y = new_page()
                heading(y, "Yopiq testlar", "(davomi)")
                y -= 10
            # Shu sahifaga sig'adigan bo'lak; ichida tartib ustun bo'ylab pastga (1–5, 6–10 ...), namunadagidek
            rows = min(int((y - KEY_BOTTOM + KEY_TILE_GAP) // step), -(-len(remaining) // KEY_TILE_ROW))
            chunk, remaining = remaining[: rows * KEY_TILE_ROW], remaining[rows * KEY_TILE_ROW:]
            rows = -(-len(chunk) // KEY_TILE_ROW)
            for index, (label, answer) in enumerate(chunk):
                col, row = divmod(index, rows)
                tile(left + col * (tile_w + KEY_TILE_GAP), y - KEY_TILE_H - row * step, KEY_TILE_H, label, answer)
            y -= rows * step
        y -= 21

    written, parts = data["written"], data["parts"]
    if written:
        if y - 50 < KEY_BOTTOM:
            y = new_page()
        span = f"{written[0][0]}–{written[-1][0]}-savollar" if len(written) > 1 else f"{written[0][0]}-savol"
        heading(y, "Ochiq (yozma) testlar", span)
        y -= 10
        columns = 2 if parts <= 2 else 1  # uch qismli (Kimyo 41.1–41.3) — keng, bitta ustun
        gap = 8
        card_w = (right - left - (columns - 1) * gap) / columns
        part_w = (card_w - 36) / parts
        for start in range(0, len(written), columns):
            row = written[start: start + columns]
            boxes = [[_key_answer_box(answers, 11, part_w - 14 - (16 if len(answer_parts) > 1 else 0))
                      for answers in answer_parts] for _, answer_parts in row]
            row_h = max(34, *(box.asc + box.desc + 14 for card in boxes for box in card))
            if y - row_h < KEY_BOTTOM:
                y = new_page()
                heading(y, "Ochiq (yozma) testlar", "(davomi)")
                y -= 10
            card_y = y - row_h
            for col, ((number, answer_parts), card) in enumerate(zip(row, boxes)):
                x = left + col * (card_w + gap)
                sheet.setStrokeColor(KEY_GRID)
                sheet.setLineWidth(0.8)
                sheet.setFillColor(colors.white)
                sheet.roundRect(x, card_y, card_w, row_h, 7, stroke=1, fill=1)
                sheet.setFillColor(REPORT_BLUE)
                sheet.circle(x + 17, card_y + row_h / 2, 11, stroke=0, fill=1)
                sheet.setFillColor(colors.white)
                sheet.setFont(KEY_SANS_BOLD, 9.5 if len(number) <= 2 else 7.5)
                sheet.drawCentredString(x + 17, card_y + row_h / 2 - 3.4, number)
                middle = card_y + row_h / 2
                for part, box in enumerate(card):
                    px = x + 34 + part * part_w
                    label_w = 0
                    if len(answer_parts) > 1:
                        # "a)", "b)" — javob bilan bir qatorda, kartochkaning o'rtasida
                        label_w = 16
                        sheet.setFillColor(REPORT_MUTED)
                        sheet.setFont(KEY_SANS_BOLD, 9)
                        sheet.drawString(px + 6, middle - 3.2, f"{chr(97 + part)})")
                    if part:
                        sheet.setStrokeColor(KEY_GRID)
                        sheet.line(px, card_y + 6, px, card_y + row_h - 6)
                    inner = part_w - 6 - label_w
                    box.draw(sheet, px + 6 + label_w + (inner - box.w) / 2, middle - 1 - (box.asc - box.desc) / 2)
            y = card_y - 6

    if not closed and not written:
        sheet.setFillColor(REPORT_MUTED)
        sheet.setFont(KEY_SANS, 11)
        sheet.drawString(left, y, "Bu testda hali javoblar kaliti kiritilmagan.")
    sheet.save()
    return buffer.getvalue()


def test_answer_key_pngs(test: Test, key_override: dict | None = None, key_variant: str | None = None) -> list[bytes]:
    """Kalit rasmlari — kalit PDF'ining sahifalari (ko'rinishi PDF bilan aynan bir xil)."""
    return pdf_pages_to_png(generate_answer_key_pdf(test, key_override, key_variant))


def generate_student_id_list_pdf(test: Test, students: list[tuple]) -> bytes:
    """
    "Ismli varaqlar" — o'quvchilar ro'yxati: ism-familiya va ID (variant bo'lsa — u ham).
    To'liq titul emas: o'quvchi bo'sh titulga ismini va shu ID'ni o'zi yozib, bo'yaydi.
    students = [(ID, ism, variant), ...]
    """

    buffer = io.BytesIO()
    sheet = _brand_canvas(test.title, "O'quvchilar ID ro'yxati")(buffer, pagesize=A4)
    width, height = A4
    margin, row_height = 50, 22
    with_variant = len({variant for _, _, variant in students}) > 1
    digits = 4 if (test.sheet_version or 1) >= 5 else 3  # titulda bo'yaladigan xonalar soni (masalan 0007)
    number_x, name_x, id_x, variant_x = margin, margin + 34, width - margin - (150 if with_variant else 80), width - margin - 70

    def header(first: bool) -> float:
        y = height - REPORT_HEADER_HEIGHT - 26
        if first:
            sheet.setFont(REPORT_FONT_BOLD, 15)
            sheet.drawString(margin, y, test.title[:70])
            y -= 18
            sheet.setFont(REPORT_FONT, 10)
            sheet.setFillColor(REPORT_MUTED)
            sheet.drawString(margin, y, f"Test kodi: {test.code}  ·  {test.subject}  ·  {len(students)} ta o'quvchi")
            sheet.setFillColor(colors.black)
            y -= 26
        sheet.setFont(REPORT_FONT_BOLD, 11)
        sheet.drawString(number_x, y, "№")
        sheet.drawString(name_x, y, "Ism-familiya")
        sheet.drawString(id_x, y, "ID")
        if with_variant:
            sheet.drawString(variant_x, y, "Variant")
        sheet.setStrokeColor(REPORT_GRID)
        sheet.line(margin, y - 7, width - margin, y - 7)
        return y - row_height

    y = header(True)
    for index, (number, name, variant) in enumerate(students, start=1):
        if y < 60:  # pastda Chittak imzosi turadi
            sheet.showPage()
            y = header(False)
        sheet.setFont(REPORT_FONT, 11)
        sheet.drawString(number_x, y, str(index))
        sheet.drawString(name_x, y, name[:60])
        sheet.setFont(REPORT_FONT_BOLD, 12)
        sheet.drawString(id_x, y, str(number).zfill(digits))
        if with_variant:
            sheet.drawString(variant_x, y, variant)
        sheet.line(margin, y - 7, width - margin, y - 7)
        y -= row_height
    sheet.save()
    return buffer.getvalue()


def _question_count(test: Test) -> int:
    """
    Savollar soni. "key" rejimida a/b qismlar bitta savol deb sanaladi,
    shuning uchun 35 ta test + 10 ta yozma = 45 bo'ladi.
    """
    return len(_group_key_questions(test)) if test.mode == "key" else len(test.questions)


def _get_own_test(db: Session, teacher: Teacher, test_id: int) -> Test:
    """Test shu o'qituvchiga tegishli ekanini tekshiradi, bo'lmasa 404 qaytaradi."""
    test = db.get(Test, test_id)
    if not test or test.teacher_id != teacher.id or test.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


# Test kodini taxmin qilib topishdan himoya. Kod qisqa (4 belgi) — cheklovsiz
# bo'lsa, begona odam kodlarni ketma-ket sinab, boshqalarning testlarini topib,
# savollarini ko'rishi va o'qituvchining tokenini sarflashi mumkin edi.
# O'quvchi kodni adashib yozishi tabiiy, shuning uchun chegara kengroq.
# Muvaffaqiyatli kirish hisobni tozalamaydi — aks holda ma'lum bitta kodni
# oralab kiritib, cheklovni chetlab o'tish mumkin bo'lardi.
TEST_CODE_MAX_FAILURES = 20
TEST_CODE_WINDOW_SECONDS = 15 * 60
_test_code_failures: dict[int, list[float]] = {}


def _get_test_by_code(db: Session, code: str, user: User) -> Test:
    now = time.monotonic()
    recent = [moment for moment in _test_code_failures.get(user.id, []) if now - moment < TEST_CODE_WINDOW_SECONDS]
    _test_code_failures[user.id] = recent
    if len(recent) >= TEST_CODE_MAX_FAILURES:
        minutes = int((TEST_CODE_WINDOW_SECONDS - (now - recent[0])) // 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Test kodi juda ko'p marta noto'g'ri kiritildi. {minutes} daqiqadan keyin qayta urinib ko'ring.",
        )

    test = db.scalar(select(Test).where(Test.code == code.strip().upper(), Test.deleted_at.is_(None)))
    if not test:
        recent.append(now)
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


def _is_paid(db: Session, user: User, test: Test) -> bool:
    if test.price == 0:
        return True
    payment = db.scalar(
        select(Payment.id).where(Payment.user_id == user.id, Payment.test_id == test.id, Payment.status == "paid")
    )
    return payment is not None


# Token narxi qancha ko'p sotib olinsa, shuncha arzonlashadi (optom chegirma).
# Har bir qator: (eng kami, eng ko'pi yoki None=cheksiz, 1 dona token narxi so'mda)
CREDIT_PRICE_TIERS = [
    (1, 100, 1200),
    (101, 150, 1000),
    (151, 300, 650),
    (301, 500, 500),
    (501, None, 450),
]

CREDIT_PRICE_TIERS_DISPLAY = [
    {"from": low, "to": high, "price_per_credit": price} for low, high, price in CREDIT_PRICE_TIERS
]


# ---------- Pullik test: platforma va muallif ulushi ----------
# Umumiy foizni admin «💰 Narxlar» bo'limida belgilaydi (AppSetting "test_commission_percent");
# belgilanmagan bo'lsa — .env'dagi PLATFORM_COMMISSION_PERCENT. Muayyan muallifga alohida foiz
# ham qo'yish mumkin (Teacher.commission_percent) — u umumiy foizdan ustun turadi.

COMMISSION_PERCENT_KEY = "test_commission_percent"


def _clamp_percent(value) -> int:
    return max(0, min(100, int(value)))


def _global_commission_percent(db: Session) -> int:
    """Platformaning umumiy foizi (pullik testlardan)."""
    row = db.get(AppSetting, COMMISSION_PERCENT_KEY)
    try:
        if row and str(row.value).strip() != "":
            return _clamp_percent(row.value)
    except ValueError:
        pass  # buzilgan qiymat — .env'dagi foiz
    return _clamp_percent(PLATFORM_COMMISSION_PERCENT)


def _commission_percent(db: Session, teacher: Teacher | None) -> int:
    """Shu muallifning testlaridan platforma oladigan foiz: alohida belgilangan bo'lsa — o'shasi."""
    if teacher is not None and teacher.commission_percent is not None:
        return _clamp_percent(teacher.commission_percent)
    return _global_commission_percent(db)


def _teacher_share(amount: int, percent: int | None = None) -> int:
    """O'quvchi to'lagan summadan muallifga tegadigan qismi (qolgani — platforma foizi; None — .env foizi)."""
    if percent is None:
        percent = _clamp_percent(PLATFORM_COMMISSION_PERCENT)
    return round(amount * (100 - percent) / 100)


def _payment_teacher_share(payment: "Payment") -> int:
    """
    To'langan to'lovdan muallifga yozilgan ulush. Saqlangan bo'lsa — aynan o'shasi; bu o'zgarishdan
    oldingi to'lovlarda saqlanmagan — ular .env'dagi foiz bilan yozilgan edi, shuning uchun o'sha bilan.
    """
    if payment.teacher_share is not None:
        return payment.teacher_share
    return _teacher_share(payment.amount, _clamp_percent(PLATFORM_COMMISSION_PERCENT))


def _tests_commission_total(db: Session, *where) -> int:
    """Pullik testlardan platformada qolgan jami summa (to'langan to'lovlar bo'yicha)."""
    paid = (Payment.status == "paid", Payment.provider != "card", *where)
    stored = db.scalar(
        select(func.sum(Payment.amount - Payment.teacher_share)).where(*paid, Payment.teacher_share.is_not(None))
    ) or 0
    legacy = db.scalar(select(func.sum(Payment.amount)).where(*paid, Payment.teacher_share.is_(None))) or 0
    return stored + legacy - _teacher_share(legacy, _clamp_percent(PLATFORM_COMMISSION_PERCENT))


def _calculate_credit_price(credit_count: int) -> int:
    """Token sonining jami narxini optom chegirma jadvali bo'yicha hisoblaydi."""
    for low, high, price_per_credit in CREDIT_PRICE_TIERS:
        if credit_count >= low and (high is None or credit_count <= high):
            return credit_count * price_per_credit
    return credit_count * CREDIT_PRICE_TIERS[0][2]


# ---------- Oylik obuna ----------
# O'qituvchi YOKI token sotib oladi (1 tekshiruv = 1 token), YOKI oylik obuna —
# obuna muddatida token umuman yechilmaydi. Obuna narxlarini admin panelda admin
# belgilaydi (AppSetting: "sub_price_1", "sub_price_3", "sub_price_12");
# narx 0 bo'lsa — shu muddat sotilmaydi.


def _subscription_price_key(months: int) -> str:
    return f"sub_price_{months}"


def _subscription_prices(db: Session) -> dict[int, int]:
    """Har bir obuna muddatining narxi: {1: 99000, 3: 0, 12: 900000} (0 — sotilmaydi)."""

    keys = {_subscription_price_key(months): months for months in SUBSCRIPTION_PLAN_DAYS}
    prices = {months: 0 for months in SUBSCRIPTION_PLAN_DAYS}
    for row in db.scalars(select(AppSetting).where(AppSetting.key.in_(keys))).all():
        try:
            prices[keys[row.key]] = max(0, int(row.value or 0))
        except ValueError:
            pass  # buzilgan qiymat — shu muddat sotilmaydi
    return prices


def _subscription_plans(db: Session) -> list[dict]:
    """O'qituvchiga ko'rsatiladigan obuna turlari — faqat narxi belgilanganlari."""

    return [
        {"months": months, "days": SUBSCRIPTION_PLAN_DAYS[months], "price": price}
        for months, price in _subscription_prices(db).items()
        if price > 0
    ]


def _subscription_active(teacher: Teacher) -> bool:
    return bool(teacher.subscription_until and teacher.subscription_until > utcnow())


def _charges_tokens(test: Test, teacher: Teacher) -> bool:
    """Bu natija uchun token yechiladimi: bepul testda — ha, faqat oylik obunasi faol bo'lmasa."""
    return test.price == 0 and not _subscription_active(teacher)


def _purchase_label(purchase: CreditPurchase) -> str:
    """Xarid nomi: "50 ta token", "3 oylik obuna" yoki "«AB12» testi e'loni"."""
    if purchase.kind == "broadcast":
        return f"«{purchase.test.code}» testi e'loni" if purchase.test else "Test e'loni"
    if purchase.kind == "verify":
        return f"«{purchase.school.name}» — ✓ tasdiq belgisi" if purchase.school else "✓ Tasdiq belgisi"
    if purchase.kind == "subscription":
        return f"{purchase.months} oylik obuna"
    return f"{purchase.credits} ta token"


def _new_purchase(db: Session, teacher: Teacher, choice, method: str) -> CreditPurchase:
    """Token yoki obuna xaridini yaratadi; narxni server o'zi hisoblaydi (frontendga ishonilmaydi)."""

    if choice.months:
        price = _subscription_prices(db).get(choice.months, 0)
        if price <= 0:
            raise HTTPException(status_code=409, detail="Bu obuna hozircha sotilmaydi")
        return CreditPurchase(teacher_id=teacher.id, kind="subscription", months=choice.months, credits=0,
                              amount=price, method=method)
    return CreditPurchase(teacher_id=teacher.id, kind="tokens", credits=choice.credits,
                          amount=_calculate_credit_price(choice.credits), method=method)


def _grant_purchase(purchase: CreditPurchase) -> None:
    """To'langan xaridni kuchga kiritadi: token qo'shiladi yoki obuna muddati uzayadi."""

    teacher = purchase.teacher
    if purchase.kind == "broadcast":
        _grant_broadcast(purchase)
        return
    if purchase.kind == "verify":
        school = object_session(purchase).get(PrivateSchool, purchase.school_id, with_for_update=True)
        if school and school.verified_at is None:
            school.verified_at = utcnow()
        return
    if purchase.kind != "subscription":
        add_credits(teacher, purchase.credits)
        return
    # Qatorni qulflaymiz: bir vaqtda ikki to'lov tushsa, biri ikkinchisining kunlarini bosib ketmasin
    object_session(teacher).refresh(teacher, with_for_update=True)
    # Obuna hali tugamagan bo'lsa — qolgan kunlariga qo'shiladi, tugagan bo'lsa — bugundan boshlanadi
    start = max(utcnow(), teacher.subscription_until or utcnow())
    teacher.subscription_until = start + timedelta(days=SUBSCRIPTION_PLAN_DAYS[purchase.months])


def _revoke_purchase(purchase: CreditPurchase) -> None:
    """Bekor qilingan (pul qaytarilgan) xarid: berilgan token yoki obuna kunlari olib qo'yiladi."""

    teacher = purchase.teacher
    if purchase.kind == "broadcast":
        # Yuborilgan e'lonni qaytarib bo'lmaydi — pul qaytarilgani faqat log'da qoladi
        logging.getLogger("backend").warning("Pullik e'lon to'lovi bekor qilindi: purchase=%s", purchase.id)
        return
    if purchase.kind == "verify":
        # Pul qaytarildi — sotib olingan belgi olib tashlanadi
        school = object_session(purchase).get(PrivateSchool, purchase.school_id) if purchase.school_id else None
        if school:
            school.verified_at = None
        return
    if purchase.kind != "subscription":
        add_credits(teacher, -purchase.credits)
        return
    object_session(teacher).refresh(teacher, with_for_update=True)
    if teacher.subscription_until:
        teacher.subscription_until -= timedelta(days=SUBSCRIPTION_PLAN_DAYS[purchase.months])


def _purchase_granted_text(purchase: CreditPurchase, teacher: Teacher) -> str:
    """Xarid kuchga kirgach o'qituvchiga boradigan xabar."""

    if purchase.kind == "broadcast":
        return f"📢 {_purchase_label(purchase)} to'landi — bot foydalanuvchilariga yuborilmoqda."
    if purchase.kind == "verify":
        return f"✅ {_purchase_label(purchase)} faollashdi — muassasa nomi yonida ko'k ✓ belgisi chiqadi."
    if purchase.kind == "subscription":
        until = (teacher.subscription_until + TASHKENT_OFFSET).strftime("%d.%m.%Y")
        return f"⭐ {purchase.months} oylik obuna faollashdi — {until} gacha tekshiruvlar cheksiz, token yechilmaydi."
    return f"Hisobingizga {purchase.credits} ta token qo'shildi.\nJoriy token: {teacher.credits} ta"


# ---------- Pullik test e'loni ----------
# O'qituvchi testini bot foydalanuvchilarining barchasiga e'lon qiladi. Narxni admin panelda admin
# belgilaydi (AppSetting "broadcast_price"); 0 — e'lon bepul (ilgarigidek). Narx bo'lsa — faqat Payme
# orqali: to'lov o'tgach (Payme PerformTransaction) e'lon o'zi yuboriladi.

BROADCAST_PRICE_KEY = "broadcast_price"
# ✓ Tasdiq belgisi narxi (muassasa rahbariyati Payme orqali sotib oladi); 0 — sotilmaydi (faqat admin beradi)
VERIFY_PRICE_KEY = "verify_price"


def _setting_price(db: Session, key: str) -> int:
    """Admin panelda belgilangan narx (so'm); yo'q yoki buzilgan — 0."""
    row = db.get(AppSetting, key)
    try:
        return max(0, int(row.value or 0)) if row else 0
    except ValueError:
        return 0


def _broadcast_price(db: Session) -> int:
    """Bitta test e'loni narxi (so'm); 0 — bepul."""
    return _setting_price(db, BROADCAST_PRICE_KEY)


def _verify_price(db: Session) -> int:
    """✓ Tasdiq belgisi narxi (so'm); 0 — sotilmaydi."""
    return _setting_price(db, VERIFY_PRICE_KEY)


def _broadcast_blocker(test: Test | None) -> str | None:
    """Testni hozir e'lon qilib bo'lmasa — sababi (aks holda None)."""
    if not test or test.deleted_at:
        return "Test topilmadi"
    if test.broadcast_at:
        return "Bu test allaqachon e'lon qilingan"
    if test.status != "active" or (test.end_time and test.end_time <= utcnow()):
        return "Test yakunlangan — e'lon qilib bo'lmaydi"
    if test.enrollment_type == "closed":
        return "Offline testga o'quvchilar o'zi kira olmaydi — e'lon qilib bo'lmaydi"
    return None


def _grant_broadcast(purchase: CreditPurchase) -> None:
    """To'langan e'lon: test e'lon qilingan deb belgilanadi, yuborish — commit'dan keyin fonda."""

    session = object_session(purchase)
    test = session.get(Test, purchase.test_id, with_for_update=True) if purchase.test_id else None
    if not test or test.broadcast_at:
        # Ikki marta to'lansa (yoki test o'chgan bo'lsa) — ikkinchi marta yuborilmaydi
        logging.getLogger("backend").warning("Pullik e'lon yuborilmadi (test yo'q yoki e'lon qilingan): purchase=%s",
                                             purchase.id)
        return
    test.broadcast_at = utcnow()
    session.info.setdefault("broadcast_tests", set()).add(test.id)


@event.listens_for(Session, "after_commit")
def _start_paid_broadcasts(session: Session) -> None:
    for test_id in sorted(session.info.pop("broadcast_tests", None) or ()):
        _start_broadcast(test_id)


@event.listens_for(Session, "after_rollback")
def _forget_paid_broadcasts(session: Session) -> None:
    session.info.pop("broadcast_tests", None)


def _build_payme_checkout_url(account_field: str, order_id: int | str, amount: int) -> str:
    """
    Payme'ning hosted to'lov sahifasiga olib boruvchi havolani yaratadi (amount — so'mda).

    account_field — Payme buyurtmani qaysi nom bilan qaytarishi:
      "purchase_id" — o'qituvchining token xaridi (CreditPurchase)
      "payment_id"  — o'quvchining pullik testga to'lovi (Payment)
    """

    # Payme summani tiyinda kutadi
    params = f"m={PAYME_MERCHANT_ID};ac.{account_field}={order_id};a={amount * 100}"
    encoded = base64.b64encode(params.encode()).decode()
    # Sinov rejimida — Payme'ning test to'lov sahifasi (test.paycom.uz — bu sandbox, to'lov sahifasi emas)
    base = "https://checkout.test.paycom.uz" if PAYME_TEST_MODE else "https://checkout.paycom.uz"
    return f"{base}/{encoded}"


def _notify_admin_about_withdrawal(request: WithdrawalRequest, teacher: Teacher) -> None:
    """Admin'ga Telegram orqali, tugmali xabar yuboradi — shu yerdan to'g'ridan-to'g'ri tasdiqlash/rad etish mumkin."""

    for admin_id in ADMIN_IDS:
        _send_withdrawal_notice(admin_id, request, teacher)


def _send_withdrawal_notice(admin_id: int, request: WithdrawalRequest, teacher: Teacher) -> None:
    try:
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": admin_id,
                "text": (
                    f"💸 Pul yechish so'rovi\n\n"
                    f"O'qituvchi: {teacher.user.full_name}\n"
                    f"Summa: {request.amount} so'm\n"
                    f"Karta: {request.card_number}"
                ),
                "reply_markup": {
                    "inline_keyboard": [
                        [
                            {"text": "✅ To'landi", "callback_data": f"withdrawal_approve:{request.id}"},
                            {"text": "❌ Rad etish", "callback_data": f"withdrawal_reject:{request.id}"},
                        ]
                    ]
                },
            },
            timeout=10,
        )
    except Exception:
        # Bildirishnoma yuborilmasa ham so'rov bazada saqlanib qoladi —
        # admin uni Mini App'dagi admin panelidan ham ko'rib, tasdiqlashi mumkin.
        logging.getLogger("backend").warning("Admin'ga pul yechish bildirishnomasi yuborilmadi", exc_info=True)


def _send_pdf_to_telegram(chat_id: int, file_name: str, pdf_bytes: "bytes | Path", caption: str = "",
                          mime: str = "application/pdf") -> None:
    """
    Tayyor faylni (PDF, Excel, ZIP) bot orqali foydalanuvchining shaxsiy chatiga yuboradi.
    Katta fayl (masalan test arxivi) diskdagi yo'li (Path) bilan beriladi — xotiraga to'liq yuklanmaydi.
    """

    for _ in range(3):
        on_disk = isinstance(pdf_bytes, Path)
        payload = pdf_bytes.open("rb") if on_disk else pdf_bytes
        try:
            response = httpx.post(
                f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
                data={"chat_id": chat_id, "caption": caption} if caption else {"chat_id": chat_id},
                files={"document": (file_name, payload, mime)},
                timeout=300 if on_disk else 30,
            )
        finally:
            if on_disk:
                payload.close()
        if response.status_code != 429:  # 429 — juda tez: Telegram aytgancha kutib qayta urinamiz
            break
        try:
            retry_after = int(response.json().get("parameters", {}).get("retry_after", 3))
        except ValueError:
            retry_after = 3
        time.sleep(min(retry_after, 30) + 0.5)
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Fayl yuborilmadi (botga /start bosganmisiz?)")


def _answer_sheet_caption(test: Test) -> str:
    """Javoblar varaqasi bilan birga yuboriladigan izoh."""

    return (
        "📄 Javoblar Varaqasi\n\n"
        f"📝 Test: {test.title}\n"
        f"🔑 Kod: {test.code}\n"
        f"📚 Fan: {test.subject}\n\n"
        "Bu varaqani o'quvchilarga tarqating. Ular javoblarini belgilab beradi, "
        "siz esa javoblarni botdagi \"O'quvchilar\" bo'limiga kiritasiz."
    )


def _normalize_channel(raw_channel: str | None) -> str | None:
    """O'qituvchi kanal nomini turlicha yozishi mumkin — hammasini "@nom" ko'rinishiga keltiradi."""

    if not raw_channel:
        return None

    channel = raw_channel.strip()

    # Username'siz yopiq guruh/kanal — raqamli ID bilan ("-1001234567890")
    if re.fullmatch(r"-?\d{5,20}", channel):
        return channel

    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if channel.startswith(prefix):
            channel = channel[len(prefix) :]
            break

    channel = channel.strip("/")
    return f"@{channel}" if channel else None


# Platforma adminlarining kanallari — HAR BIR testda majburiy (o'qituvchi
# o'z kanalini qo'shsa, unga ham obuna shart). Bot bu kanallarda admin.
DEFAULT_REQUIRED_CHANNELS = [
    channel
    for channel in (_normalize_channel(item) for item in os.getenv("DEFAULT_REQUIRED_CHANNELS", "@sukhrobmath").split(","))
    if channel
]


def _required_channels(test: Test) -> list[str]:
    """Testda obuna shart bo'lgan barcha kanallar: avval adminlarniki, keyin o'qituvchiniki."""

    # Oflayn test sinfda, titul bilan o'tadi — o'quvchi ilovaga kirmaydi, obuna so'ralmaydi
    if test.enrollment_type == "closed":
        return []
    channels = list(DEFAULT_REQUIRED_CHANNELS)
    if test.required_channel and test.required_channel.lower() not in {item.lower() for item in channels}:
        channels.append(test.required_channel)
    return channels


class ChannelUnavailable(Exception):
    """Botning kanalga ruxsati yo'q — ya'ni obunani umuman tekshirib bo'lmaydi."""


def _ask_telegram_chat_member(channel: str, tg_id: int) -> dict:
    """
    Telegram'dan "bu odam shu kanalda bormi?" deb so'raydi.

    Ikki xil muvaffaqiyatsizlikni ajratadi, chunki ularga munosabat ham har xil:

    - ChannelUnavailable — bot kanalda admin emas yoki kanal nomi xato.
      Buni O'QITUVCHI tuzatishi kerak.
    - TimeoutError/boshqa xato — internet uzilgan, Telegram javob bermadi.
      Bu o'tkinchi, hech kimning aybi yo'q.
    """

    response = httpx.get(
        f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
        params={"chat_id": channel, "user_id": tg_id},
        timeout=10,
    )
    data = response.json()

    if not data.get("ok"):
        raise ChannelUnavailable(data.get("description") or "noma'lum sabab")

    return data.get("result") or {}


def _ensure_bot_can_check_channel(channel: str) -> None:
    """
    Test yaratilayotganda: bot shu kanalni tekshira oladimi?

    Telegram botga faqat O'ZI a'zo (yoki admin) bo'lgan kanal haqida ma'lumot
    beradi. Shuning uchun botning o'zini so'raymiz — javob kelsa, demak bot
    kanalni ko'ryapti va o'quvchilarni ham tekshira oladi.
    """

    try:
        bot_id = int(BOT_TOKEN.split(":")[0])
    except (ValueError, IndexError):
        return  # token noto'g'ri — bu boshqa joyda allaqachon aytilgan

    try:
        bot_member = _ask_telegram_chat_member(channel, bot_id)
    except ChannelUnavailable as error:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Bot {channel} kanali/guruhini ko'ra olmayapti ({error}). "
                "Avval botni shu kanal yoki guruhga administrator qilib qo'shing, keyin testni saqlang. "
                "Aks holda obuna tekshiruvi ishlamaydi."
            ),
        )
    except Exception:
        # Tekshirib bo'lmasa testni saqlamaymiz: aks holda obuna tekshiruvi
        # ishlamaydigan test o'quvchilarga tarqab ketishi mumkin edi.
        logging.getLogger("backend").warning("Kanalni tekshirib bo'lmadi (vaqtinchalik xato)", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Telegram hozir javob bermadi — kanalni tekshirib bo'lmadi. Bir oz kutib, qayta saqlang.",
        )

    # Bot oddiy a'zo bo'lsa, kanalda o'quvchilarni tekshira olmaydi —
    # Telegram obunachilar ro'yxatini faqat administratorga ochadi.
    if bot_member.get("status") not in ("administrator", "creator"):
        raise HTTPException(
            status_code=422,
            detail=(
                f"Bot {channel} da administrator emas. Botni shu kanal yoki guruhga "
                "administrator qilib qo'shing, keyin testni saqlang."
            ),
        )


def _is_subscribed(channel: str, user: User) -> bool:
    """
    O'quvchi shu kanalga obunami? Tekshirib bo'lmasa — o'tkazib yubormaymiz:

    - 424 — bot kanalda admin emas (buni o'qituvchi yoki admin tuzatadi);
    - 503 — Telegram javob bermadi (bir oz kutib qayta urinadi).
    """

    try:
        member = _ask_telegram_chat_member(channel, user.tg_id)
    except ChannelUnavailable as error:
        # O'quvchi kanalda umuman bo'lmasa, Telegram ba'zan "user not found"
        # deb javob beradi — bu sozlama xatosi emas, shunchaki obuna yo'q.
        reason = str(error).lower()
        if any(marker in reason for marker in ("user not found", "participant_id_invalid", "member not found")):
            return False

        logging.getLogger("backend").warning("Kanal obunasini tekshirib bo'lmadi (%s): %s", channel, error)
        raise HTTPException(
            status_code=424,
            detail=(
                f"Bu testda {channel} ga obuna shart, lekin bot "
                "obunani tekshira olmayapti. O'qituvchiga ayting: botni kanal yoki "
                "guruhga administrator qilib qo'shish kerak."
            ),
        )
    except Exception:
        logging.getLogger("backend").warning("Kanal obunasi tekshirilmadi (vaqtinchalik xato)", exc_info=True)
        raise HTTPException(
            status_code=503,
            detail="Obunani tekshirib bo'lmadi — Telegram javob bermadi. Bir oz kutib, qayta urinib ko'ring.",
        )

    status = member.get("status")
    # "restricted" — kanalda, lekin cheklangan. U ham obunachi hisoblanadi,
    # faqat is_member rost bo'lsa. "left" va "kicked" — obuna emas.
    return status in ("creator", "administrator", "member") or (status == "restricted" and bool(member.get("is_member")))


# Adminlar kanaliga obuna tasdiqlangan foydalanuvchilar — har ochilishda
# Telegram'ga qayta so'ramaslik uchun 10 daqiqa eslab qolinadi.
SUBSCRIPTION_CACHE_SECONDS = 600
_subscription_confirmed: dict[int, float] = {}


def missing_default_channels(tg_id: int) -> list[str]:
    """
    Foydalanuvchi obuna bo'lmagan adminlar kanallari (bot va Mini App
    kirishida tekshiriladi). Tekshirib bo'lmasa (bot kanalda admin emas,
    Telegram javob bermadi) — foydalanuvchini to'smaymiz: testlar baribir
    alohida, qat'iy tekshiriladi.
    """

    # Sinov akkaunti (Google tekshiruvchisi) Telegram'da yo'q — obuna ekrani uni to'sib qo'ymasin
    if tg_id == REVIEW_TG_ID or not DEFAULT_REQUIRED_CHANNELS or not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        return []
    confirmed_at = _subscription_confirmed.get(tg_id)
    if confirmed_at and time.monotonic() - confirmed_at < SUBSCRIPTION_CACHE_SECONDS:
        return []

    probe = SimpleNamespace(tg_id=tg_id)
    missing = []
    for channel in DEFAULT_REQUIRED_CHANNELS:
        try:
            if not _is_subscribed(channel, probe):
                missing.append(channel)
        except HTTPException:
            logging.getLogger("backend").warning("Kirishda %s obunasini tekshirib bo'lmadi", channel)
    if not missing:
        _subscription_confirmed[tg_id] = time.monotonic()
    return missing


@app.get("/api/me/subscription")
def my_subscription(user: User = Depends(get_current_user)):
    """Mini App ochilganda: adminlar kanaliga obunami? Bo'lmasa — obuna ekrani chiqadi."""
    return {"missing": missing_default_channels(user.tg_id)}


def _check_channel_subscription(test: Test, user: User) -> None:
    """
    O'quvchi testdagi BARCHA majburiy kanallarga (adminlar kanali + o'qituvchi
    kanali) obuna ekanini tekshiradi. Bittasiga obuna bo'lmasa ham test
    boshlanmaydi: 451 — qaysi kanallarga obuna bo'lish kerakligi yoziladi.
    """

    missing = [channel for channel in _required_channels(test) if not _is_subscribed(channel, user)]
    if missing:
        raise HTTPException(
            status_code=451,  # frontend shu kodni ko'rib, "Obuna bo'lish" tugmalarini chiqaradi
            detail=f"Testni boshlash uchun avval obuna bo'ling: {', '.join(missing)}",
        )


def _check_enrollment(test: Test) -> None:
    """O'quvchi testni boshlay olishini tekshiradi: yopiq emasligini va vaqt oynasini."""

    if test.enrollment_type == "closed":
        raise HTTPException(
            status_code=403, detail="Bu offline test — ichki imtihon, javoblar titulda belgilanadi"
        )
    if test.status == "closed":
        raise HTTPException(status_code=403, detail="Test yakunlangan")

    now = utcnow()

    if test.start_time and now < test.start_time:
        raise HTTPException(
            status_code=403, detail=f"Test hali boshlanmagan. Boshlanish vaqti: {test.start_time:%d.%m.%Y %H:%M}"
        )
    if test.end_time and now > test.end_time:
        raise HTTPException(status_code=403, detail="Test vaqti tugagan")


# Yozma javoblar MathLive klaviaturasi orqali LaTeX'da ("2\sqrt{3}",
# "\frac{\pi}{5}"), yoki oddiy matnda ("2sqrt3", "pi/5") keladi. Bir xil
# javob turlicha yozilishi mumkin,
# shuning uchun solishtirishdan oldin hammasini bitta ko'rinishga keltiramiz.

# 1-qadam: LaTeX'ning bir xil ma'noli yozilishlarini birlashtirish
LATEX_REPLACEMENTS = [
    ("\\left", ""),
    ("\\right", ""),
    ("\\!", ""),
    ("\\,", ""),
    ("\\;", ""),
    ("\\dfrac", "\\frac"),
    ("\\tfrac", "\\frac"),
    # Ko'paytirish belgisi har doim "×" bo'ladi — o'quvchi "*" yozadi,
    # eski LaTeX kalitda esa "\cdot" yoki "\times" turishi mumkin. Ilgari
    # "\times" teskari chiziq olib tashlangach "times" so'ziga aylanib qolardi.
    ("\\cdot", "×"),
    ("\\times", "×"),
    ("\\div", "/"),
    ("$", ""),
    # MathLive klaviaturasi qo'yadigan maxsus buyruqlar. Matn bu yerga
    # kelguncha kichik harfga o'tkazilgan: "\exponentialE" -> "\exponentiale".
    ("\\placeholder{}", ""),
    ("\\exponentiale", "e"),
    ("\\imaginaryi", "i"),
    ("\\operatorname", ""),
    ("\\mathrm", ""),
    # Gradus: "30^{\circ}", "30^\circ", "30\degree" -> "30°"
    ("^{\\circ}", "°"),
    ("^\\circ", "°"),
    ("\\degree", "°"),
    ("\\circ", "°"),
]

# 2-qadam: oddiy matn ko'rinishidagi yozilishlar. O'quvchi javobni telefonning
# oddiy klaviaturasida yozadi: "2sqrt3", "pi/5", "x^2", "2*pi".
TEXT_ANSWER_REPLACEMENTS = [
    (",", "."),
    ("sqrt", "√"),
    ("ildiz", "√"),
    ("pi", "π"),
    # "**" — daraja (Python uslubi). Ko'paytirishdan OLDIN almashtiriladi,
    # aks holda yulduzchalar avval "×" ga aylanib ketadi.
    ("**", "^"),
    ("*", "×"),
    # Telefon klaviaturalaridagi o'xshash belgilar
    ("·", "×"),
    ("÷", "/"),
    ("−", "-"),
    ("–", "-"),
    # Tutuq belgisi (atamalar: "Farg'ona", "O'zbekiston") — telefonlarda har xil
    ("‘", "'"),
    ("’", "'"),
    ("ʻ", "'"),
    ("ʼ", "'"),
    ("`", "'"),
    ("´", "'"),
]


def _read_latex_argument(text: str, start: int) -> tuple[str, int]:
    r"""
    LaTeX buyrug'ining bitta argumentini o'qiydi: "{...}" (ichida boshqa
    qavslar bo'lsa ham), bitta belgi ("\frac12" dagi "1") yoki bitta buyruq
    ("\frac\pi2" dagi "\pi"). (argument, keyingi_pozitsiya) qaytaradi.
    """

    if start >= len(text):
        return "", start

    if text[start] == "{":
        depth = 0
        for index in range(start, len(text)):
            if text[index] == "{":
                depth += 1
            elif text[index] == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : index], index + 1
        return text[start + 1 :], len(text)  # qavs yopilmagan

    if text[start] == "\\":
        end = start + 1
        while end < len(text) and text[end].isalpha():
            end += 1
        end = max(end, start + 2)
        return text[start:end], end

    return text[start], start + 1


def _expand_fractions(text: str) -> str:
    r"""
    \frac{a}{b} -> (a)/(b). Qavslar sanab o'qiladi, shuning uchun ichida
    ildiz yoki boshqa kasr bo'lsa ham ishlaydi: "\frac{\sqrt{3}}{2}" -> "(\sqrt{3})/(2)".
    """

    for _ in range(50):
        position = text.find("\\frac")
        if position < 0:
            break
        numerator, after_numerator = _read_latex_argument(text, position + len("\\frac"))
        denominator, after_denominator = _read_latex_argument(text, after_numerator)
        text = f"{text[:position]}({numerator})/({denominator}){text[after_denominator:]}"
    return text


# Qavs ichida faqat bitta "bo'lak" bo'lsa (son, harf, π, ildiz) — qavs
# ortiqcha: "(3)" -> "3", "√(3)" -> "√3", "(π)/(5)" -> "π/5".
# Amal bor bo'lsa ("(x+1)") qavs saqlanadi, aks holda ma'no o'zgaradi.
_SIMPLE_PARENS_PATTERN = re.compile(r"\((-?[0-9a-zπ.√²³]+)\)")

# "^2" -> "²", lekin "^23" ni "²3" qilib yubormaslik uchun keyingi belgi raqam bo'lmasin
_SQUARE_PATTERN = re.compile(r"\^2(?![0-9.])")
_CUBE_PATTERN = re.compile(r"\^3(?![0-9.])")

# Yashirin ko'paytirish: "2×π" va "2π" — bir xil. Lekin "2×3" ni "23" qilib
# bo'lmaydi, shuning uchun "×" faqat harf, π, ildiz yoki qavs oldida olib tashlanadi.
_IMPLICIT_MULTIPLY_PATTERN = re.compile(r"×(?=[a-zπ√(])")


def _split_accepted_variants(correct: str) -> list[str]:
    """
    Qabul qilinadigan javoblarni ajratadi. Ular "|" bilan saqlanadi, lekin
    LaTeX'da "|" modul belgisi ham bo'lishi mumkin ("\\left|x\\right|") —
    shuning uchun ularni vaqtincha yashirib turamiz.
    """

    protected_text = correct.replace("\\left|", "\x00L").replace("\\right|", "\x00R").replace("\\|", "\x00N")
    parts = protected_text.split("|")

    return [
        part.replace("\x00L", "\\left|").replace("\x00R", "\\right|").replace("\x00N", "\\|")
        for part in parts
        if part.strip()
    ]


_FUNCTION_BEFORE_PATTERN = re.compile(r"(arcsin|arccos|arcctg|arctg|sin|cos|ctg|tg|tan|cot|log|lg|ln|exp)$")
# Bitta "bo'lak": son, harf, π yoki ildiz ostidagi son/harf (daraja belgisi bilan ham)
_PAREN_ATOM_PATTERN = re.compile(r"-?(\d+(\.\d+)?|[a-zπ]|√(\d+(\.\d+)?|[a-zπ]))[²³]?")


def _current_term(before: str) -> str:
    """
    Qavsdan oldingi joriy ko'paytuvchilar zanjiri (oxirgi +, ( , = yoki AYIRISH minusidan keyingi qism).
    Ishora minusi ("/-", "×-", boshidagi "-") zanjirni uzmaydi: "π/-(2x)" da "/" hali shu zanjirda.
    """
    for index in range(len(before) - 1, -1, -1):
        char = before[index]
        if char in "+(=":
            return before[index + 1:]
        if char == "-" and index > 0 and re.match(r"[0-9a-zπ)²³.!]", before[index - 1]):
            return before[index + 1:]
    return before


def _drop_parens(match: re.Match) -> str:
    """
    Ortiqcha qavsni olib tashlaydi — faqat ifodaning QIYMATI o'zgarmasa:
      "(3)" -> "3", "√(3)" -> "√3", "(π)/(5)" -> "π/5", "sin(x)" -> "sinx", "2(π)" -> "2π".
    Saqlanadi: "2(3)" (=6, "23" emas), "2(-3)", "(-3)^2" (=9, "-3²" = -9 emas),
    "π/(2√2)" ("π/2√2" emas), "2^(2x)", "1/2(π)".
    """
    before, inner, after = match.string[:match.start()], match.group(1), match.string[match.end():]
    atom = bool(_PAREN_ATOM_PATTERN.fullmatch(inner))
    if after[:1] in ("^", "²", "³") and not (atom and inner[0] != "-"):
        return match.group(0)
    if _FUNCTION_BEFORE_PATTERN.search(before):
        return inner
    last = before[-1:] if before else ""
    if last in ("", "+", "-", "(", "=", "×"):
        return inner if "/" not in _current_term(before) or atom else match.group(0)
    if last in ("/", "√", "^"):
        return inner if atom else match.group(0)
    if re.fullmatch(r"[0-9a-zπ)²³.!]", last):
        # Yashirin ko'paytirish: "2(π)" = "2π"; lekin son ("2(3)") va bo'lishdan keyin ("1/2(π)") — yo'q
        if inner[0] in "-0123456789." or "/" in _current_term(before):
            return match.group(0)
        return inner
    return match.group(0)


def _drop_multiply_sign(match: re.Match) -> str:
    """"2×π" -> "2π", lekin bo'lishdan keyin emas: "1/2×π" (= π/2) "1/2π" bilan bir xil emas."""
    return match.group(0) if "/" in _current_term(match.string[:match.start()]) else ""


def _strip_simple_parens(text: str) -> str:
    """Ortiqcha qavslarni olib tashlaydi (ichma-ich bo'lsa ham)."""
    for _ in range(10):
        new_text = _SIMPLE_PARENS_PATTERN.sub(_drop_parens, text)
        if new_text == text:
            break
        text = new_text
    return text


def _normalize_text_answer(value: str) -> str:
    """
    Yozma javobni solishtirishga tayyorlaydi.

    Bir xil javobning turli yozilishlari bitta ko'rinishga keladi. Bu ikki
    tomonlama ishlaydi: o'quvchi oddiy klaviaturada yozadi ("2sqrt(3)",
    "pi/5", "1/2"), eski testlarning kaliti esa LaTeX'da saqlangan bo'lishi
    mumkin ("2\\sqrt{3}", "\\frac{\\pi}{5}", "\\frac{1}{2}") — ikkalasi ham
    bir xil natija beradi.
    """

    text = "".join((value or "").split()).lower()

    for source, target in LATEX_REPLACEMENTS:
        text = text.replace(source, target)

    # Kasrlar: \frac{a}{b} -> (a)/(b)
    text = _expand_fractions(text)

    # Qolgan jingalak qavslar oddiy qavsga aylanadi: "\sqrt{x+1}" -> "√(x+1)".
    # Ilgari ular shunchaki o'chirilardi va "√x+1" qolib, ma'no buzilardi.
    text = text.replace("{", "(").replace("}", ")")

    for source, target in TEXT_ANSWER_REPLACEMENTS:
        text = text.replace(source, target)

    # Qolgan LaTeX teskari chiziqlari: "\π" -> "π", "\sin" -> "sin"
    text = text.replace("\\", "")

    text = _strip_simple_parens(text)

    text = _SQUARE_PATTERN.sub("²", text)
    text = _CUBE_PATTERN.sub("³", text)

    return _IMPLICIT_MULTIPLY_PATTERN.sub(_drop_multiply_sign, text)


# So'z bilan yoziladigan javoblar (Tarix atamalari, nomlar): "21-asr", "21 asr", "XXI asr" —
# bittasi. Matematik javoblarga tegmaydi: ularda "x-1" va "x1" har xil.
_MATH_WORDS_PATTERN = re.compile(
    r"arcsin|arccos|arcctg|arctg|sqrt|ildiz|frac|left|right|cdot|times|degree|circ|sin|cos|ctg|tg|tan|cot|log|lg|ln|lim|exp|pi"
)
_WORD_PATTERN = re.compile(r"[^\W\d_]{3,}")
_ROMAN_CENTURY_PATTERN = re.compile(r"\b([ivxlc]+)\s*-?\s*(?=asr)")
_VALID_ROMAN_PATTERN = re.compile(r"c{0,3}(xc|xl|l?x{0,3})(ix|iv|v?i{0,3})")
_ROMAN_VALUES = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100}
_APOSTROPHES = str.maketrans({"‘": "'", "’": "'", "ʻ": "'", "ʼ": "'", "`": "'", "´": "'"})


def _roman_to_int(roman: str) -> int:
    total = 0
    for index, char in enumerate(roman):
        value = _ROMAN_VALUES[char]
        following = _ROMAN_VALUES[roman[index + 1]] if index + 1 < len(roman) else 0
        total += -value if value < following else value
    return total


def _is_word_answer(correct: str) -> bool:
    """Kalit so'z bilan yozilganmi (kamida 3 harfli so'z, matematik belgilar emas)."""
    return bool(_WORD_PATTERN.search(_MATH_WORDS_PATTERN.sub("", correct.lower())))


def _roman_century(match: re.Match) -> str:
    roman = match.group(1)
    if not _VALID_ROMAN_PATTERN.fullmatch(roman):
        return match.group(0)
    return f"{_roman_to_int(roman)} "


def _normalize_word_answer(value: str) -> str:
    """
    So'zli javob ("21-asr", "Amir Temur"): katta-kichik harf, bo'sh joy, so'z oldidagi
    chiziqcha, qo'shtirnoq va tinish belgilari hisobga olinmaydi. Sonlarga tegilmaydi:
    "3,5" ≠ "35", "1/2" ≠ "12", "1 2" ≠ "12", "-3" ≠ "3".
    """
    text = (value or "").lower().translate(_APOSTROPHES)
    text = _ROMAN_CENTURY_PATTERN.sub(_roman_century, text)  # XXI asr -> 21 asr
    text = re.sub(r"[−–—]", "-", text)
    text = re.sub(r"(?<=\d),(?=\d)", ".", text)  # o'nli kasr: 3,5 = 3.5
    # Chiziqcha faqat so'z oldida olib tashlanadi ("21-asr", "Rus-turk"); raqam yoki bitta harf oldida — minus
    text = re.sub(r"(?<=[^\W_])\s*-\s*(?=[^\W\d_]{2,})", "", text)
    # Tinish belgilari va qo'shtirnoq — bo'shliqqa (sonlar ichidagi "." bundan mustasno)
    text = re.sub(r"(?<!\d)\.|\.(?!\d)|[,;:!?\"«»“”„]", " ", text)
    # Bo'shliqlar olib tashlanadi, faqat ikki son orasidagisi bitta bo'shliq bo'lib qoladi ("1 2" ≠ "12")
    # (son va keyingi son yoki manfiy son orasida ham: "3,5, -5" — ikki son, "3,5-5" — ayirma)
    text = re.sub(r"(?<=\d)\s+(?=-?\d)", "\x00", text)
    text = re.sub(r"\s+", "", text).replace("\x00", " ")
    return text.replace("_", "")


def _text_answer_is_correct(student_answer: str, correct: str) -> bool:
    """
    Har bir qabul qilinadigan variant o'z qoidasi bilan solishtiriladi:
    so'zli kalit ("21 asr") — so'z qoidasi bilan, matematik kalit ("2√3", "-3") — matematik qoida bilan.
    """
    if not (student_answer or "").strip():
        return False
    math_answer, word_answer = _normalize_text_answer(student_answer), _normalize_word_answer(student_answer)
    for variant in _split_accepted_variants(correct):
        if _is_word_answer(variant):
            if word_answer and _normalize_word_answer(variant) == word_answer:
                return True
        elif math_answer and _normalize_text_answer(variant) == math_answer:
            return True
    return False


def _written_verdicts(db: Session, attempt_id: int) -> dict[int, bool]:
    """Tituldagi yozma javoblarga o'qituvchi qo'ygan baholar: {savol raqami: to'g'ri/xato} (baholanganlari)."""

    return {
        ref: verdict
        for ref, verdict in db.execute(
            select(WrittenAnswer.question_ref, WrittenAnswer.verdict).where(
                WrittenAnswer.attempt_id == attempt_id, WrittenAnswer.verdict.is_not(None)
            )
        ).all()
    }


def _grade_answers(db: Session, attempt: Attempt, test: Test, answers: dict[str, str]) -> int:
    """
    Javoblarni tekshirib, har biri uchun StudentAnswer yozuvini yaratadi.
    Savollarning umumiy sonini qaytaradi.

    Ikki joyda ishlatiladi: o'quvchi testni o'zi yakunlaganda, va yopiq testda
    o'qituvchi o'quvchi javoblarini o'zi kiritganda.
    """

    if test.mode == "key":
        overrides = _key_override(db, test, attempt.variant)
        # Titulda qo'lda yozilgan javoblar: o'qituvchi rasmga qarab qo'ygan baho (✓/✗) kalitdan ustun
        verdicts = (
            _written_verdicts(db, attempt.id)
            if attempt.id and any(kq.question_type == "text" for kq in test.key_questions) else {}
        )
        for key_question in test.key_questions:
            student_answer = answers.get(str(key_question.number)) or ""
            key_correct = overrides.get(key_question.number, key_question.correct)

            if key_question.question_type == "multi":
                raw_answer = student_answer.upper().replace(" ", "")
                chosen_letters = {letter for letter in raw_answer.split(",") if letter}
                # Kalit ham xuddi shunday tozalanadi — bazada eski, normallashtirilmagan
                # yozuv ("a, c") qolgan bo'lsa ham to'g'ri solishtiriladi.
                correct_letters = {
                    part.strip().upper() for part in key_correct.split(",") if part.strip()
                }
                is_correct = bool(chosen_letters) and chosen_letters == correct_letters
                chosen_value = ",".join(sorted(chosen_letters)) if chosen_letters else None
            elif key_question.question_type == "text":
                # Yozma javob: yozilish farqlari hisobga olinmaydi (qarang: _normalize_text_answer),
                # o'qituvchi kiritgan variantlardan biriga mos kelsa yetarli.
                verdict = verdicts.get(key_question.number)
                is_correct = verdict if verdict is not None else _text_answer_is_correct(student_answer, key_correct)
                chosen_value = student_answer.strip()[:300] or None
            else:
                raw_answer = student_answer.upper().replace(" ", "")
                # Kalit kiritilmagan savol — hech kimga to'g'ri emas (bo'sh javob bo'sh kalitga "mos" kelmasin)
                is_correct = bool(key_correct) and raw_answer == key_correct
                chosen_value = raw_answer or None

            db.add(
                StudentAnswer(
                    attempt_id=attempt.id,
                    question_ref=key_question.number,
                    chosen=chosen_value,
                    is_correct=is_correct,
                )
            )
        return len(_group_key_questions(test))

    for question in test.questions:
        chosen = (answers.get(str(question.id)) or "").upper()
        chosen = chosen if chosen in "ABCD" and len(chosen) == 1 else None
        is_correct = chosen == question.correct
        db.add(StudentAnswer(attempt_id=attempt.id, question_ref=question.id, chosen=chosen, is_correct=is_correct))

    return len(test.questions)


def _calculate_attempt_score(db: Session, attempt: Attempt, test: Test) -> None:
    """
    Saqlangan javoblardan attempt.correct/wrong/percent'ni hisoblaydi.

    Yozma savol a va b qismlardan iborat bo'lsa, u BITTA savol deb sanaladi va
    ikkala qismi ham to'g'ri bo'lgandagina to'g'ri hisoblanadi. Shuning uchun
    natija "31/45" ko'rinishida chiqadi, "31/55" emas.
    """

    answers = {
        answer.question_ref: answer
        for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
    }

    if test.mode == "key":
        groups = _group_key_questions(test)
        total = len(groups)
        correct_count = sum(
            1
            for group in groups
            if group and all(answers.get(kq.number) is not None and answers[kq.number].is_correct for kq in group)
        )
    else:
        total = len(answers)
        correct_count = sum(1 for answer in answers.values() if answer.is_correct)

    attempt.correct = correct_count
    attempt.wrong = total - correct_count
    attempt.percent = round(correct_count / total * 100, 1) if total else 0


def _finalize_attempt(db: Session, attempt: Attempt, test: Test, answers: dict[str, str]) -> int:
    """
    Urinishni yakunlaydi: javoblarni tekshiradi, natijani hisoblaydi va
    bepul testda o'qituvchidan 1 token yechadi. Savollar sonini qaytaradi.

    Uch joyda ishlatiladi — o'quvchi "YAKUNLASH" ni bosganda, vaqti tugab
    qolgan urinish fon vazifasida avtomatik tekshirilganda, va o'quvchi
    allaqachon tugab ketgan urinish bilan qaytib kelganda. Hammasi bitta
    funksiyada bo'lgani uchun token hisobi hech qachon chalkashmaydi.
    """

    # Qayta yakunlanmasligi uchun: eski javoblar o'rniga yangilari yoziladi
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))

    total_questions = _grade_answers(db, attempt, test, answers)
    _calculate_attempt_score(db, attempt, test)
    if test.essay_max:
        attempt.essay_text = (answers.get(ESSAY_KEY) or "").strip()[:ESSAY_MAX_CHARS] or None
        if test.essay_ai and attempt.essay_text:
            # Bazaga yozilgach (commit) fonda AI baholaydi — qarang: _submit_essay_ai
            db.info.setdefault("essay_ai", set()).add(attempt.id)

    attempt.finished_at = utcnow()
    attempt.status = "done"
    attempt.seconds = int(min((attempt.finished_at - attempt.started_at).total_seconds(), test.duration * 60))
    attempt.saved_answers = None  # natija StudentAnswer'da — qoralamani saqlashning hojati yo'q

    # Bepul testda har bir yakunlangan natija o'qituvchidan 1 token oladi
    # (oylik obunasi faol bo'lsa — olinmaydi).
    # max(0, ...) — token test BOSHLANGANDA tekshiriladi, lekin oradagi vaqtda
    # o'qituvchi uni boshqa joyda sarflab yuborishi mumkin; shunda balans
    # manfiyga tushib, o'qituvchi keyin sotib olgan tokenini yo'qotardi.
    if _charges_tokens(test, test.teacher):
        add_credits(test.teacher, -1)

    return total_questions


def _result_notice(db: Session, attempt: Attempt, test: Test, total_questions: int) -> tuple[int, str] | None:
    """
    O'quvchi testni yakunlaganda o'qituvchiga boradigan xabar: (chat_id, matn).

    Natija o'quvchining o'ziga ko'rsatilmaydi (pullik va bepul testda ham) —
    uni faqat o'qituvchi ko'radi: shu xabarda va Mini App'dagi natijalarda.
    Xabar sessiya yopilmasidan oldin tayyorlanadi, yuborish esa keyinroq
    (_send_result_notice) — o'quvchi Telegram javobini kutib qolmasin.
    """

    teacher_user = test.teacher.user if test.teacher else None
    if not teacher_user:
        return None

    student = db.get(User, attempt.user_id) if attempt.user_id else None
    name = (student.full_name if student else attempt.offline_name) or "Noma'lum o'quvchi"

    text = (
        "📝 Yangi natija\n\n"
        f"👤 O'quvchi: {name}\n"
        f"📚 Test: {test.title} (kod: {test.code})\n"
        f"✅ To'g'ri javoblar: {attempt.correct} / {total_questions}\n"
        f"📊 Natija: {attempt.percent}%"
    )
    return teacher_user.tg_id, text


# ---------- O'qituvchiga natija xabarlari: navbat orqali ----------
#
# Telegram bitta chatga sekundiga ~1 xabardan ko'pini qabul qilmaydi (429).
# Ilgari har natija alohida ipda darhol yuborilardi — imtihon oxirida 300 o'quvchi
# bir vaqtda tugatsa, xabarlarning ko'pi rad etilib, jimgina yo'qolardi.
# Endi: bitta yuboruvchi ip, bir lahzada kelganlar bitta xabarga birlashtiriladi
# (4096 belgi chegarasida), har chatga ≥1.1 s oraliq, 429 kelsa — kutib qayta urinadi.
# (Natijalarning o'zi bazada; bu faqat xabar.)

NOTICE_BATCH_WINDOW = 1.0   # bir lahzada kelganlarni yig'ish (soniya)
NOTICE_CHAT_INTERVAL = 1.1  # bitta chatga xabarlar orasidagi eng kam vaqt
NOTICE_MAX_CHARS = 3800     # Telegram chegarasi 4096
_notice_queue: "queue.Queue[tuple[int, str]]" = queue.Queue()
_notice_worker_lock = threading.Lock()
_notice_worker: threading.Thread | None = None


def _telegram_send_message(chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None) -> bool:
    """sendMessage: 429 bo'lsa — Telegram aytgan vaqtcha kutib qayta urinadi. Yetkazilsa True."""

    return _telegram_send_status(chat_id, text, reply_markup, parse_mode)[0] == 200


def _telegram_send_status(
    chat_id: int, text: str, reply_markup: dict | None = None, parse_mode: str | None = None
) -> tuple[int, str]:
    """sendMessage: Telegram javobining kodi va izohi (200 — yetkazildi, 0 — ulanib bo'lmadi)."""

    logger = logging.getLogger("backend")
    body = {"chat_id": chat_id, "text": text}
    if reply_markup:
        body["reply_markup"] = reply_markup
    if parse_mode:
        body["parse_mode"] = parse_mode
    for _ in range(5):
        try:
            response = httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=body, timeout=15)
        except Exception:
            logger.warning("Telegram'ga ulanib bo'lmadi, qayta urinamiz", exc_info=True)
            time.sleep(2)
            continue
        if response.status_code == 429:
            try:
                retry_after = int(response.json().get("parameters", {}).get("retry_after", 3))
            except ValueError:
                retry_after = 3
            time.sleep(min(retry_after, 60) + 0.5)
            continue
        if response.status_code != 200:
            # 400/403 — chat yo'q yoki botni bloklagan: qayta urinishdan foyda yo'q
            logger.warning("Xabar yetmadi (%s): %s", response.status_code, response.text[:200])
        return response.status_code, response.text[:200]
    return 0, ""


def _notice_chunks(texts: list[str]) -> list[str]:
    """Bir nechta natija xabarini Telegram chegarasidagi bo'laklarga birlashtiradi."""
    chunks, current = [], ""
    for text in texts:
        text = text[:NOTICE_MAX_CHARS]
        candidate = f"{current}\n\n➖➖➖\n\n{text}" if current else text
        if len(candidate) > NOTICE_MAX_CHARS:
            chunks.append(current)
            current = text
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def _notice_worker_loop() -> None:
    last_sent: dict[int, float] = {}
    while True:
        batch = [_notice_queue.get()]
        time.sleep(NOTICE_BATCH_WINDOW)
        while True:
            try:
                batch.append(_notice_queue.get_nowait())
            except queue.Empty:
                break
        by_chat: dict[int, list[str]] = {}
        for chat_id, text in batch:
            by_chat.setdefault(chat_id, []).append(text)
        for chat_id, texts in by_chat.items():
            chunks = _notice_chunks(texts)
            footer = _pending_payments_footer(chat_id)
            for index, chunk in enumerate(chunks):
                wait = NOTICE_CHAT_INTERVAL - (time.monotonic() - last_sent.get(chat_id, 0.0))
                if wait > 0:
                    time.sleep(wait)
                markup = None
                if footer and index == len(chunks) - 1:  # oxirgi xabarga — chek eslatmasi
                    chunk, markup = chunk + footer, _payments_button()
                if not _telegram_send_message(chat_id, chunk, markup):
                    logging.getLogger("backend").warning("Natija xabari o'qituvchiga yetmadi (chat %s)", chat_id)
                last_sent[chat_id] = time.monotonic()


def _pending_count_for_chat(db: Session, chat_id: int) -> int:
    """Shu Telegram chatidagi o'qituvchining testlariga kelgan, ruxsat kutayotgan cheklar soni."""
    return db.scalar(
        select(func.count()).select_from(Payment)
        .join(Test, Test.id == Payment.test_id)
        .join(Teacher, Teacher.id == Test.teacher_id)
        .join(User, User.id == Teacher.user_id)
        .where(User.tg_id == chat_id, Payment.provider == "card", Payment.status == "pending")
    ) or 0


def _pending_payments_footer(chat_id: int) -> str:
    """Bot xabari oxiriga: "⏳ N ta o'quvchi ruxsat kutmoqda" — chek eslatmasi doim chat pastida tursin."""
    try:
        with SessionLocal() as session:
            count = _pending_count_for_chat(session, chat_id)
    except Exception:
        return ""
    if not count:
        return ""
    return (f"\n\n━━━━━━━━━━\n⏳ {count} ta o'quvchi to'lov qildi va ruxsat kutmoqda! "
            "Chatda qadalgan chekni oching yoki pastdagi tugmani bosing.")


def _send_result_notice(notice: tuple[int, str] | None) -> None:
    """Natija xabarini navbatga qo'yadi (darhol qaytadi). Yuborilmasa ham natija bazada saqlanib qoladi."""

    global _notice_worker
    if not notice or not BOT_TOKEN:
        return
    with _notice_worker_lock:
        if _notice_worker is None or not _notice_worker.is_alive():
            _notice_worker = threading.Thread(target=_notice_worker_loop, name="result-notices", daemon=True)
            _notice_worker.start()
    _notice_queue.put(notice)


def _send_result_notice_in_background(notice: tuple[int, str] | None) -> None:
    """So'rov ichidan chaqiriladi — navbatga qo'yadi, o'quvchi Telegram'ni kutmaydi."""

    _send_result_notice(notice)


def _grade_attempt_from_saved_answers(db: Session, attempt: Attempt, test: Test) -> int:
    """Vaqti tugagan urinishni, o'quvchi belgilab qo'ygan javoblari bo'yicha yakunlaydi."""

    return _finalize_attempt(db, attempt, test, _read_saved_answers(attempt))


def _read_saved_answers(attempt: Attempt) -> dict[str, str]:
    """Urinishdagi qoralama javoblarni o'qiydi. Yozuv buzilgan bo'lsa, bo'sh qaytaradi."""

    if not attempt.saved_answers:
        return {}
    try:
        saved = json.loads(attempt.saved_answers)
    except (ValueError, TypeError):
        return {}

    return {str(key): str(value) for key, value in saved.items()} if isinstance(saved, dict) else {}


# ========== 3) Umumiy endpointlar ==========


@app.get("/api/ping")
def ping() -> dict:
    """Autentifikatsiyasiz oddiy tekshiruv — backend ishlayotganini ko'rsatadi."""
    return {"status": "ok", "message": " ishlayapti ✅"}


@app.get("/api/me")
def get_me(user: User = Depends(get_current_user), db: Session = Depends(get_db)) -> dict:
    """Mini App ochilganda chaqiriladi — Telegram orqali aniqlangan foydalanuvchini qaytaradi."""

    # Maktabga qo'shilgan o'qituvchi Test markazida ham o'qituvchi (yozuv avtomatik ochiladi)
    is_teacher = ensure_school_teacher(db, user) is not None
    if is_teacher:
        reward_referral(db, user)  # ro'yxatdan o'tganda obuna bo'lmagan bo'lsa — keyinroq beriladi

    return {
        # O'qituvchiga ilova ochilishi bilan "N ta o'quvchi ruxsat kutmoqda" oynasi
        "pending_payments": _pending_count_for_chat(db, user.tg_id) if is_teacher else 0,
        "id": user.id,
        "full_name": user.full_name,
        "tg_id": user.tg_id,
        "photo_url": user.photo_url,
        "is_admin": is_admin_tg(user.tg_id),
        "is_teacher": is_teacher,
        "default_channels": DEFAULT_REQUIRED_CHANNELS,
        # Pullik test va onlayn to'lov uchun ulangan tizimlar (bo'sh — pullik test yaratib bo'lmaydi)
        "payment_providers": _enabled_providers(),
        # Pullik testda (Click/Payme orqali) har to'lovning necha foizi muallifga tushadi
        "author_percent": 100 - _commission_percent(db, db.scalar(select(Teacher).where(Teacher.user_id == user.id)))
        if is_teacher else None,
    }


@app.post("/api/me/name")
def update_my_name(body: NameUpdateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Foydalanuvchi o'z ism-familiyasini tahrirlaydi (Telegram ismi ishonchli bo'lmasligi mumkin)."""

    user.full_name = f"{body.first_name.strip()} {body.last_name.strip()}".strip()
    db.commit()
    return {"full_name": user.full_name}


@app.get("/api/me/attempts")
def my_attempts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Joriy foydalanuvchining jarayondagi va yakunlangan testlari — o'quvchi dashboard'i uchun."""

    attempts = db.scalars(select(Attempt).where(Attempt.user_id == user.id).order_by(Attempt.id.desc())).all()

    in_progress = []
    completed = []

    for attempt in attempts:
        test = db.get(Test, attempt.test_id)
        if not test or test.deleted_at is not None:
            continue

        if attempt.status == "in_progress":
            in_progress.append({"code": test.code, "title": test.title, "subject": test.subject})
        elif attempt.status == "done":
            # Natija (ball, foiz) o'quvchiga ko'rsatilmaydi — faqat o'qituvchiga
            completed.append(
                {
                    "attempt_id": attempt.id,
                    "code": test.code,
                    "title": test.title,
                    "subject": test.subject,
                    "finished_at": attempt.finished_at,
                    # Natija faqat test yakunlangach ochiladi (qarang: my_attempt_review)
                    "can_review": test.status == "closed",
                }
            )
    return {"in_progress": in_progress, "completed": completed}


@app.get("/api/home")
def home_summary(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Bosh menyu uchun: bugungi sana va "Mening maktabim" — a'zo bo'lgan muassasalari (maktab,
    o'quv markazi, to'garak): nomi, lavozimi, sinflar soni va kuratorlik sinflari.
    """

    local_now = utcnow() + TASHKENT_OFFSET
    places = []
    for member in _all_memberships(db, user):
        school = member.school
        if not school or school.deleted_at is not None:
            continue
        classes = _sorted_classes(db.scalars(
            select(SchoolClass).where(SchoolClass.school_id == school.id, SchoolClass.deleted_at.is_(None))
        ).all())
        places.append({
            "school_id": school.id, "kind": school.kind or "school", "name": school.name,
            "role": member.role, "position": member.position, "classes": len(classes),
            "curated": [item.name for item in classes if item.curator_user_id == user.id],
            "verified": school.verified_at is not None,
        })
    return {
        "today": local_now.date(),
        "weekday_label": WEEKDAYS[local_now.weekday()],
        "places": places,
    }


@app.get("/api/me/attempts/{attempt_id}/review")
def my_attempt_review(attempt_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    O'quvchi o'zi topshirgan testni ko'radi: natijasi (ball, daraja, o'rin) va
    har bir savolda nimani belgilagani, to'g'ri javob nima edi.

    Faqat test YAKUNLANGACH ochiladi — aks holda o'quvchi to'g'ri javoblarni
    hali test ishlayotgan boshqalarga tarqatishi mumkin edi.
    """

    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.user_id != user.id or attempt.status != "done":
        raise HTTPException(status_code=404, detail="Test topilmadi")
    test = db.get(Test, attempt.test_id)
    if not test or test.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    if test.status != "closed":
        raise HTTPException(status_code=403, detail="Natija test yakunlangach ochiladi — biroz kuting")

    review = _attempt_review(db, test, attempt)
    report = build_rasch_report(db, test)
    row = next((row for row in report["rows"] if row["attempt_id"] == attempt.id), None)
    return {
        **review,
        "title": test.title,
        "subject": test.subject,
        "ball": row["ball"] if row else None,
        "dtm_ball": row["dtm_ball"] if row else None,
        "dtm_max": report["dtm_max"],
        "grade": row["grade"] if row else None,
        "rank": row["rank"] if row else None,
        "sections": row["sections"] if row else {},
        # Esseli test: Milliy sertifikatdagidek — test bali, yozma ish bali, foiz ko'rsatkichi
        "test_ball": row["test_ball"] if row else None,
        "essay_ball": row["essay_ball"] if row else None,
        "essay_pending": bool(row and row["essay_pending"]),
        "cert_percent": row["cert_percent"] if row else None,
        "participants": report["summary"]["participants"],
    }
# ========== 4) O'qituvchi ro'yxatdan o'tishi ==========
# Noto'g'ri promo kod urinishlari (taxmin qilib topishdan himoya): 15 daqiqada 5 ta
PROMO_MAX_FAILURES = 5
PROMO_WINDOW_SECONDS = 15 * 60
_promo_failures: dict[int, list[float]] = {}


@app.post("/api/teacher/promo")
def redeem_promo(body: PromoRedeemIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchi promo kod kiritadi — token oladi. Har bir kod bitta o'qituvchiga bir marta."""

    now = time.monotonic()
    recent = [moment for moment in _promo_failures.get(teacher.id, []) if now - moment < PROMO_WINDOW_SECONDS]
    _promo_failures[teacher.id] = recent
    if len(recent) >= PROMO_MAX_FAILURES:
        minutes = int((PROMO_WINDOW_SECONDS - (now - recent[0])) // 60) + 1
        raise HTTPException(status_code=429, detail=f"Kod juda ko'p marta noto'g'ri kiritildi. {minutes} daqiqadan keyin urinib ko'ring.")

    promo = db.scalar(select(PromoCode).where(PromoCode.code_hash == _promo_hash(body.code)))
    if not promo or not promo.active or (promo.expires_on and promo.expires_on < date.today()):
        _promo_failures.setdefault(teacher.id, []).append(now)
        raise HTTPException(status_code=404, detail="Bunday promo kod yo'q yoki muddati tugagan")
    # Bir vaqtda bir necha marta bosilsa ham chegaradan oshmasin — o'qituvchi yozuvi qulflanadi
    db.scalar(select(Teacher.id).where(Teacher.id == teacher.id).with_for_update())
    limit = promo.per_teacher_limit or 1
    used_by_me = db.scalar(
        select(func.count()).select_from(PromoRedemption)
        .where(PromoRedemption.promo_id == promo.id, PromoRedemption.teacher_id == teacher.id)
    ) or 0
    if used_by_me >= limit:
        raise HTTPException(status_code=409, detail=f"Bu promo kodni {limit} marta ishlatib bo'lgansiz")
    if promo.max_uses is not None:
        used = db.scalar(select(func.count()).select_from(PromoRedemption).where(PromoRedemption.promo_id == promo.id)) or 0
        if used >= promo.max_uses:
            raise HTTPException(status_code=410, detail="Bu promo kodning soni tugagan")

    db.add(PromoRedemption(promo_id=promo.id, teacher_id=teacher.id, credits=promo.credits))
    add_credits(teacher, promo.credits)
    db.commit()
    _promo_failures.pop(teacher.id, None)
    logging.getLogger("backend").info("Promo kod ishlatildi: promo=%s teacher=%s +%s token", promo.id, teacher.id, promo.credits)
    return {"credits_added": promo.credits, "credits": teacher.credits, "used": used_by_me + 1, "limit": limit}


@app.post("/api/teacher/register")
def register_teacher(
    body: TeacherRegisterIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Joriy Telegram foydalanuvchisini o'qituvchi sifatida ro'yxatdan o'tkazadi.
    Login/parol yo'q — faqat ism-familiya so'raladi va shu User'ning
    full_name maydoniga yoziladi (Telegram ismi ishonchli bo'lmasligi mumkin).
    """
    already_registered = db.scalar(select(Teacher.id).where(Teacher.user_id == user.id))
    if already_registered:
        raise HTTPException(status_code=400, detail="Siz allaqachon o'qituvchi sifatida ro'yxatdan o'tgansiz")

    user.full_name = f"{body.first_name.strip()} {body.last_name.strip()}".strip()

    # Yangi o'qituvchi darhol ishlay boshlashi uchun sovg'a tokenlar beriladi
    # (hisobini o'chirib, qayta ochgan odamga — yo'q)
    credits = welcome_credits_for(db, user.tg_id)
    db.add(Teacher(user_id=user.id, credits=credits))
    db.commit()
    reward_referral(db, user)

    return {"ok": True, "full_name": user.full_name, "credits": credits}


# ---------- Referal ----------
# O'qituvchi o'z havolasini tarqatadi. Havola orqali botga BIRINCHI marta kirgan
# odam shu o'qituvchiga bog'lanadi. U @sukhrobmath kanaliga obuna bo'lib,
# o'qituvchi sifatida ro'yxatdan o'tsa — havola egasiga REFERRAL_CREDITS token.


def referral_link(teacher: Teacher) -> str | None:
    username = _bot_username()
    return f"https://t.me/{username}?start=ref_{teacher.id}" if username else None


def remember_bot_user(db: Session, tg_id: int, full_name: str, username: str | None, payload: str = "") -> None:
    """
    /start bosgan odamni bazaga yozadi (test e'lonlari unga ham yetsin). Yangi odam
    referal havolasi bilan kelgan bo'lsa — havola egasiga bog'lanadi. Avval botga
    yoki ilovaga kirgan odam referal hisoblanmaydi.
    """

    if db.scalar(select(User.id).where(User.tg_id == tg_id)):
        return
    user = User(tg_id=tg_id, full_name=full_name, username=username)
    db.add(user)
    try:
        db.flush()
        code = payload[len("ref_"):] if payload.startswith("ref_") else ""
        referrer = db.get(Teacher, int(code)) if code.isdigit() and len(code) <= 9 else None
        # Hisobini o'chirib, qayta kelgan odam — yangi odam emas (taklif qilganga token qayta berilmasin)
        if referrer and referrer.user_id != user.id and not had_deleted_account(db, tg_id):
            db.add(Referral(referrer_teacher_id=referrer.id, referred_user_id=user.id))
        db.commit()
    except IntegrityError:
        db.rollback()  # shu payt Mini App ham yaratib qo'ygan — u holda yangi odam emas


def _subscribed_to_default_channels(tg_id: int) -> bool:
    """Adminlar kanaliga obunami — QAT'IY: tekshirib bo'lmasa ham "yo'q" (token keyinroq beriladi)."""

    if not DEFAULT_REQUIRED_CHANNELS or not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
        return True
    probe = SimpleNamespace(tg_id=tg_id)
    try:
        return all(_is_subscribed(channel, probe) for channel in DEFAULT_REQUIRED_CHANNELS)
    except HTTPException:
        logging.getLogger("backend").warning("Referal: %s obunasini tekshirib bo'lmadi", tg_id)
        return False


def reward_referral(db: Session, user: User) -> None:
    """
    Taklif qilingan odam o'qituvchi bo'lgan va kanalga obuna bo'lsa — havola egasiga
    token (bir marta). Shartlar hali bajarilmagan bo'lsa, keyingi kirishda yana tekshiriladi.
    """

    try:
        referral = db.scalar(select(Referral).where(Referral.referred_user_id == user.id, Referral.rewarded_at.is_(None)))
        if not referral or not db.scalar(select(Teacher.id).where(Teacher.user_id == user.id)):
            return
        if not _subscribed_to_default_channels(user.tg_id):
            return
        # Ikki so'rov bir vaqtda kelsa ham token faqat bir marta beriladi
        claimed = db.execute(
            update(Referral)
            .where(Referral.id == referral.id, Referral.rewarded_at.is_(None))
            .values(rewarded_at=utcnow(), credits=REFERRAL_CREDITS),
            execution_options={"synchronize_session": False},
        ).rowcount
        if claimed != 1:
            db.rollback()
            return
        referrer = db.get(Teacher, referral.referrer_teacher_id)
        add_credits(referrer, REFERRAL_CREDITS)
        db.commit()
        referrer_chat, name = referrer.user.tg_id, user.full_name or "Yangi o'qituvchi"
    except Exception:
        db.rollback()
        logging.getLogger("backend").warning("Referal tokeni berilmadi", exc_info=True)
        return
    threading.Thread(
        target=_send_bot_text,
        args=(referrer_chat, f"🎉 Siz taklif qilgan {name} o'qituvchi sifatida qo'shildi — sizga +{REFERRAL_CREDITS} token!"),
        daemon=True,
    ).start()


# ---------- Android ilovaga va saytga bot orqali kirish ----------
# 1) Ilova/sayt POST /api/app-login — bir martalik token va bot havolasini oladi.
# 2) Foydalanuvchi botda (start=login_TOKEN) "Tasdiqlash"ni bosadi — bot unga
#    bir martalik 6 xonali kod beradi (qarang: confirm_app_login).
# 3) Foydalanuvchi shu kodni ilova/saytga qo'lda kiritadi — POST .../verify
#    kodni tekshirib, to'g'ri bo'lsa seans beradi.
APP_LOGIN_TTL = timedelta(minutes=10)
APP_LOGIN_MAX_ATTEMPTS = 5


@app.post("/api/app-login")
def start_app_login(db: Session = Depends(get_db)):
    username = _bot_username()
    if not username:
        raise HTTPException(status_code=503, detail="Bot hozir javob bermayapti — birozdan keyin urinib ko'ring")
    token = secrets.token_urlsafe(16)
    db.add(AppLogin(token=token))
    db.commit()
    return {"token": token, "link": f"https://t.me/{username}?start=login_{token}",
            "expires_in": int(APP_LOGIN_TTL.total_seconds())}


def _app_login_expired(login: AppLogin) -> bool:
    return utcnow() - login.created_at > APP_LOGIN_TTL


@app.post("/api/app-login/{token}/verify")
def verify_app_login(token: str, body: AppLoginVerifyIn, db: Session = Depends(get_db),
                     user_agent: str = Header(default="")):
    """Botdan kelgan kodni tekshiradi — to'g'ri bo'lsa, shu yerda (bir marta) seans beradi."""

    login = db.scalar(select(AppLogin).where(AppLogin.token == token).with_for_update())
    if not login or login.consumed_at or _app_login_expired(login):
        raise HTTPException(status_code=410, detail="Kirish havolasi eskirdi — qaytadan urinib ko'ring")
    if not login.confirmed_at:
        raise HTTPException(status_code=400, detail="Avval botda «✅ Tasdiqlash»ni bosing")

    code = re.sub(r"\s", "", body.code)
    if not hmac.compare_digest(code, login.code):
        login.attempts += 1
        if login.attempts >= APP_LOGIN_MAX_ATTEMPTS:
            login.consumed_at = utcnow()  # juda ko'p noto'g'ri urinish — qayta boshlash kerak
            db.commit()
            raise HTTPException(status_code=410, detail="Kod bir necha marta noto'g'ri kiritildi — qaytadan boshlang")
        db.commit()
        raise HTTPException(status_code=400, detail="Kod noto'g'ri — qaytadan tekshirib ko'ring")

    login.consumed_at = utcnow()
    session_token = secrets.token_urlsafe(32)
    db.add(AppSession(token_hash=app_session_hash(session_token), user_id=login.user_id,
                      last_seen_at=utcnow(), user_agent=user_agent[:300]))
    db.commit()
    return {"status": "ok", "session": session_token}


# ---------- Google Play tekshiruvchisi uchun sinov akkaunti ----------
# Tekshiruvchida Telegram yo'q — u login/parol bilan alohida demo akkauntga kiradi.
# Kodda faqat parolning izi (sha256) saqlanadi. Demo akkauntning Telegram ID'si
# manfiy: haqiqiy foydalanuvchi bilan hech qachon to'qnashmaydi.
REVIEW_USERNAME = "reviewer@chittak.uz"
REVIEW_PASSWORD_SHA256 = "30ebfaa67f914651135f61dddb1d8c5165738d9205b87957a272a5fbaa68cc55"
REVIEW_TG_ID = -100


@app.post("/api/review-login")
def review_login(body: ReviewLoginIn, db: Session = Depends(get_db), user_agent: str = Header(default="")):
    import hashlib

    password_hash = hashlib.sha256(body.password.encode()).hexdigest()
    if not (hmac.compare_digest(body.username.strip().lower(), REVIEW_USERNAME)
            and hmac.compare_digest(password_hash, REVIEW_PASSWORD_SHA256)):
        raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")
    remember_bot_user(db, REVIEW_TG_ID, "Demo o'qituvchi", None)
    user = db.scalar(select(User).where(User.tg_id == REVIEW_TG_ID))
    session_token = secrets.token_urlsafe(32)
    db.add(AppSession(token_hash=app_session_hash(session_token), user_id=user.id,
                      last_seen_at=utcnow(), user_agent=user_agent[:300]))
    db.commit()
    return {"status": "ok", "session": session_token}


@app.post("/api/app-logout")
def app_logout(x_app_session: str = Header(default=""), db: Session = Depends(get_db)):
    if x_app_session:
        db.execute(
            update(AppSession)
            .where(AppSession.token_hash == app_session_hash(x_app_session), AppSession.revoked_at.is_(None))
            .values(revoked_at=utcnow()),
            execution_options={"synchronize_session": False},
        )
        db.commit()
    return {"ok": True}


# ---------- Akkaunt: profil, token balansi va obuna, faol qurilmalar ----------


def _device_label(user_agent: str) -> str:
    """User-Agent'dan odam o'qiydigan nom: "Chittak ilovasi · Android", "Chrome · Windows"."""

    agent = user_agent or ""
    if "iPhone" in agent:
        system = "iPhone"
    elif "iPad" in agent:
        system = "iPad"
    elif "Android" in agent:
        system = "Android"
    elif "Windows" in agent:
        system = "Windows"
    elif "Mac OS" in agent or "Macintosh" in agent:
        system = "Mac"
    elif "Linux" in agent:
        system = "Linux"
    else:
        system = ""

    if "TestWayApp" in agent:
        app = "Chittak ilovasi"
    elif "YaBrowser" in agent:
        app = "Yandex Browser"
    elif "Edg/" in agent:
        app = "Edge"
    elif "OPR/" in agent or "Opera" in agent:
        app = "Opera"
    elif "Firefox/" in agent:
        app = "Firefox"
    elif "Chrome/" in agent or "CriOS/" in agent:
        app = "Chrome"
    elif "Safari/" in agent:
        app = "Safari"
    else:
        app = "Brauzer" if agent else "Noma'lum qurilma"
    return f"{app} · {system}" if system else app


def _active_app_sessions(db: Session, user: User) -> list[AppSession]:
    """Foydalanuvchining amaldagi seanslari (bekor qilinmagan va 90 kundan beri ishlatilmagan emas)."""

    cutoff = utcnow() - APP_SESSION_IDLE_LIMIT
    sessions = db.scalars(
        select(AppSession).where(AppSession.user_id == user.id, AppSession.revoked_at.is_(None))
    ).all()
    active = [item for item in sessions if (item.last_seen_at or item.created_at) > cutoff]
    active.sort(key=lambda item: item.last_seen_at or item.created_at, reverse=True)
    return active


@app.get("/api/me/account")
def my_account(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_app_session: str = Header(default=""),
):
    """Akkaunt oynasi: profil, (o'qituvchi bo'lsa) token va obuna, faol qurilmalar."""

    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    current_hash = app_session_hash(x_app_session) if x_app_session else ""
    return {
        "full_name": user.full_name,
        "username": user.username,
        "tg_id": user.tg_id,
        "photo_url": user.photo_url,
        "is_teacher": teacher is not None,
        "credits": teacher.credits if teacher else None,
        "subscription_until": teacher.subscription_until if teacher else None,
        "subscription_active": _subscription_active(teacher) if teacher else False,
        # Telegram ichida (Mini App) — seans yo'q, Telegram akkaunti bilan kirilgan
        "via_telegram": not x_app_session,
        "idle_days": APP_SESSION_IDLE_LIMIT.days,
        "sessions": [
            {
                "id": item.id,
                "device": _device_label(item.user_agent),
                "created_at": item.created_at,
                "last_seen_at": item.last_seen_at or item.created_at,
                "is_current": bool(current_hash) and hmac.compare_digest(item.token_hash, current_hash),
            }
            for item in _active_app_sessions(db, user)
        ],
    }


@app.post("/api/me/sessions/{session_id}/revoke")
def revoke_my_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Bitta qurilmadan chiqarish (faqat o'z seansini)."""

    session = db.get(AppSession, session_id)
    if not session or session.user_id != user.id or session.revoked_at is not None:
        raise HTTPException(status_code=404, detail="Qurilma topilmadi")
    session.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


@app.post("/api/me/sessions/revoke-others")
def revoke_other_sessions(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    x_app_session: str = Header(default=""),
):
    """Shu qurilmadan boshqa hamma qurilmalardan chiqarish (Telegram ichidan — barcha ilova/sayt seanslari)."""

    current_hash = app_session_hash(x_app_session) if x_app_session else ""
    now = utcnow()
    revoked = 0
    for session in _active_app_sessions(db, user):
        if session.token_hash != current_hash:
            session.revoked_at = now
            revoked += 1
    db.commit()
    return {"ok": True, "revoked": revoked}


# ---------- Hisobni o'chirish ----------
# Google Play talabi ham: hisob ochiladigan ilovada uni o'chirish imkoni bo'lishi shart.
# Foydalanuvchi yozuvini bazadan butunlay o'chirib bo'lmaydi — unga to'lovlar (pul hisobi,
# fiskal chek, Payme solishtiruvi), boshqa o'qituvchilar testlaridagi natijalar, sinf va
# maktab yozuvlari bog'langan. Shuning uchun hisob ANONIMLASHTIRILADI: ism, username, rasm,
# karta, telefon/pasport, kirish seanslari o'chadi, testlar ro'yxatdan olinadi, tokenlar va
# obuna bekor bo'ladi, Telegram ID bo'shatiladi — odam qaytib kirsa, unga yangi bo'sh hisob ochiladi.

ACCOUNT_DELETE_WORD = "O'CHIRISH"
DELETED_USER_NAME = "O'chirilgan foydalanuvchi"


def _all_memberships(db: Session, user: User) -> list[SchoolMember]:
    """Foydalanuvchining barcha turdagi muassasalardagi amaldagi a'zoliklari."""

    return db.scalars(
        select(SchoolMember)
        .join(PrivateSchool, PrivateSchool.id == SchoolMember.school_id)
        .where(SchoolMember.user_id == user.id, SchoolMember.left_at.is_(None), PrivateSchool.deleted_at.is_(None))
    ).all()


def _account_delete_blockers(db: Session, user: User, teacher: Teacher | None) -> list[str]:
    """Hisobni hozir o'chirib bo'lmaydigan sabablar (pul yoki boshqalar ishi bog'liq bo'lsa)."""

    blockers = []
    for member in _all_memberships(db, user):
        if member.role == "owner":
            school = member.school
            blockers.append(
                f"Siz «{school.name}» egasisiz — o'chirilishi kutilmoqda, u o'chgach hisobni o'chira olasiz."
                if school.delete_requested_at else
                f"Siz «{school.name}» egasisiz. Avval uni o'chiring yoki egalikni boshqa xodimga topshirib, chiqing."
            )
    if teacher and teacher.balance > 0:
        blockers.append(f"Balansingizda {teacher.balance:,} so'm bor — avval uni yechib oling.".replace(",", " "))
    if teacher and db.scalar(
        select(WithdrawalRequest.id).where(WithdrawalRequest.teacher_id == teacher.id, WithdrawalRequest.status == "pending")
    ):
        blockers.append("Pul yechish so'rovingiz ko'rib chiqilmoqda — u yakunlangach hisobni o'chira olasiz.")
    return blockers


@app.get("/api/me/delete-info")
def account_delete_info(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hisobni o'chirish oynasi: nima yo'qoladi va o'chirishga nima xalaqit beradi."""

    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    tests = db.scalar(
        select(func.count()).select_from(Test).where(Test.teacher_id == teacher.id, Test.deleted_at.is_(None))
    ) if teacher else 0
    return {
        "word": ACCOUNT_DELETE_WORD,
        "blockers": _account_delete_blockers(db, user, teacher),
        "is_teacher": teacher is not None,
        "tests": tests,
        "credits": teacher.credits if teacher else 0,
        "subscription_active": _subscription_active(teacher) if teacher else False,
        "schools": [member.school.name for member in _all_memberships(db, user)],
    }


@app.post("/api/me/delete")
def delete_my_account(body: AccountDeleteIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Hisobni o'chirish (qaytarib bo'lmaydi) — foydalanuvchi tasdiq so'zini yozgandan keyin."""

    typed = body.confirm.strip().upper()
    for apostrophe in ("ʻ", "ʼ", "’", "‘", "`"):
        typed = typed.replace(apostrophe, "'")
    if typed != ACCOUNT_DELETE_WORD:
        raise HTTPException(status_code=422, detail=f"Tasdiqlash uchun «{ACCOUNT_DELETE_WORD}» so'zini yozing")

    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))
    blockers = _account_delete_blockers(db, user, teacher)
    if blockers:
        raise HTTPException(status_code=409, detail=blockers[0])

    now = utcnow()
    old_tg_id = user.tg_id
    if teacher:
        for test in db.scalars(select(Test).where(Test.teacher_id == teacher.id, Test.deleted_at.is_(None))).all():
            test.deleted_at = now
            if test.status != "closed":
                _mark_test_closed(test)
        teacher.credits = 0
        teacher.subscription_until = None
        teacher.card_number = teacher.card_holder = teacher.card_expiry = None
    for member in db.scalars(select(SchoolMember).where(SchoolMember.user_id == user.id)).all():
        if member.left_at is None:
            member.left_at = now
        member.phone = member.passport = None
        member.birth_date = None
    db.execute(
        update(AppSession).where(AppSession.user_id == user.id, AppSession.revoked_at.is_(None)).values(revoked_at=now),
        execution_options={"synchronize_session": False},
    )
    user.deleted_tg_hash = tg_fingerprint(old_tg_id)
    user.tg_id = -user.id  # Telegram ID bo'shaydi: qaytib kirsa — yangi hisob
    user.full_name = DELETED_USER_NAME
    user.username = user.photo_url = None
    user.deleted_at = now
    db.commit()

    _subscription_confirmed.pop(old_tg_id, None)
    logging.getLogger("backend").info("Hisob o'chirildi: user=%s", user.id)
    if BOT_TOKEN:
        threading.Thread(
            target=_send_bot_text,
            args=(old_tg_id, "🗑 Chittak hisobingiz o'chirildi.\n\nIsmingiz, testlaringiz, tokenlaringiz va kirish "
                             "seanslaringiz o'chirildi. Xohlasangiz, istalgan payt qaytadan ro'yxatdan o'tishingiz mumkin."),
            daemon=True,
        ).start()
    return {"ok": True}


def confirm_app_login(db: Session, token: str, tg_id: int, full_name: str, username: str | None) -> tuple[bool, str]:
    """
    Bot: foydalanuvchi "Tasdiqlash"ni bosdi — kirish shu Telegram akkauntiga bog'lanadi
    va bir martalik 6 xonali kod yaratiladi (foydalanuvchi uni saytga/ilovaga kiritadi).
    """

    login = db.scalar(select(AppLogin).where(AppLogin.token == token))
    if not login or login.confirmed_at or _app_login_expired(login):
        return False, "❗️ Bu kirish so'rovi eskirgan. Saytda yoki ilovada «Kirish»ni qaytadan bosing."
    remember_bot_user(db, tg_id, full_name, username)
    user = db.scalar(select(User).where(User.tg_id == tg_id))
    code = f"{secrets.randbelow(1_000_000):06d}"
    login.user_id, login.confirmed_at, login.code = user.id, utcnow(), code
    db.commit()
    return True, (
        "✅ <b>Tasdiqlandi!</b>\n\n"
        f"Tasdiqlash kodingiz: <code>{code}</code>\n\n"
        "«📋 Kodni nusxalash»ni bosing, so'ng ilovada «Qo'yish»ni bosing — kirish shu bilan yakunlanadi.\n\n"
        "⚠️ Bu kodni hech kimga bermang. Chittak xodimlari kodni hech qachon so'ramaydi — "
        "kodni bilgan odam hisobingizga kiradi."
    )


def _referral_view(db: Session, teacher: Teacher) -> dict:
    invited, joined, earned = db.execute(
        select(func.count(Referral.id), func.count(Referral.rewarded_at), func.coalesce(func.sum(Referral.credits), 0))
        .where(Referral.referrer_teacher_id == teacher.id)
    ).one()
    return {"link": referral_link(teacher), "invited": invited, "joined": joined, "earned": earned,
            "reward": REFERRAL_CREDITS}


# ========== 5) O'qituvchi endpointlari ==========


@app.get("/api/teacher/stats")
def teacher_overview(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Dashboard'dagi umumiy statistika kartalari uchun."""

    test_ids = [test.id for test in teacher.tests if test.deleted_at is None]
    attempts = (
        db.scalars(select(Attempt).where(Attempt.test_id.in_(test_ids), Attempt.status == "done")).all()
        if test_ids
        else []
    )

    return {
        "tests": len(test_ids),
        "students": len({attempt.user_id or f"offline:{attempt.offline_name}" for attempt in attempts}),
        "avg": round(sum(a.percent for a in attempts) / len(attempts), 1) if attempts else 0,
        "max": max((a.percent for a in attempts), default=0),
        "balance": teacher.balance,
        "credits": teacher.credits,
        "subscription_until": teacher.subscription_until,
        "subscription_active": _subscription_active(teacher),
        "referral": _referral_view(db, teacher),
        # Pullik test: karta kiritilganmi va nechta chek tasdiq kutyapti
        "card": _teacher_card_view(teacher),
        "pending_payments": db.scalar(
            select(func.count()).select_from(Payment).join(Test, Test.id == Payment.test_id)
            .where(Test.teacher_id == teacher.id, Payment.provider == "card", Payment.status == "pending")
        ) or 0,
    }


@app.get("/api/teacher/balance")
def teacher_balance(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining joriy pul va token balansi, obunasi hamda sotib olish uchun kerakli ma'lumotlar."""

    return {
        "balance": teacher.balance,
        "credits": teacher.credits,
        "subscription_until": teacher.subscription_until,
        "subscription_active": _subscription_active(teacher),
        "subscription_plans": _subscription_plans(db),
        "admin_card": ADMIN_CARD_NUMBER,
        "admin_cards": _admin_cards(),
        "credit_price_tiers": CREDIT_PRICE_TIERS_DISPLAY,
        # Qaysi to'lov usullari ishlayapti — frontend faqat shularni ko'rsatadi
        "payme_enabled": bool(PAYME_MERCHANT_ID),
        "click_enabled": _click_enabled(),
        "card_enabled": _card_topup_enabled(),
    }


@app.post("/api/teacher/withdrawal-requests")
def create_withdrawal_request(
    body: WithdrawalRequestIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi pullik testlardan tushgan pulni yechib olish uchun so'rov qoldiradi."""

    if body.amount > teacher.balance:
        raise HTTPException(
            status_code=402,
            detail=f"Balansingizda yetarli mablag' yo'q (mavjud: {teacher.balance} so'm)",
        )

    request = WithdrawalRequest(teacher_id=teacher.id, amount=body.amount, card_number=body.card_number.strip())
    db.add(request)
    db.commit()
    _notify_admin_about_withdrawal(request, teacher)
    return {"id": request.id, "status": request.status}


def _teacher_by_tg_id(db: Session, tg_id: int, lock: bool = False) -> Teacher | None:
    query = select(Teacher).join(User, User.id == Teacher.user_id).where(User.tg_id == tg_id)
    return db.scalar(query.with_for_update() if lock else query)


def _pending_withdrawal(db: Session, teacher: Teacher) -> WithdrawalRequest | None:
    return db.scalar(
        select(WithdrawalRequest).where(WithdrawalRequest.teacher_id == teacher.id, WithdrawalRequest.status == "pending")
    )


def bot_withdrawal_overview(db: Session, tg_id: int) -> tuple[str, int]:
    """
    Botdagi /pul: o'qituvchi balansi va kartasi. (matn, yechish mumkin bo'lgan summa) —
    summa 0 bo'lsa, "Yechish" tugmasi chiqmaydi.
    """

    teacher = _teacher_by_tg_id(db, tg_id)
    if not teacher:
        return "Bu bo'lim faqat o'qituvchilar uchun.", 0
    text = f"💰 Balansingiz: {_format_sum(teacher.balance)} so'm"
    pending = _pending_withdrawal(db, teacher)
    if pending:
        return text + f"\n\n⏳ {_format_sum(pending.amount)} so'm yechish so'rovingiz admin tomonidan ko'rib chiqilmoqda.", 0
    if teacher.balance <= 0:
        return text + "\n\nO'quvchilar pullik testingizga Click/Payme orqali to'lasa, pul shu yerga tushadi.", 0
    if not teacher.card_number:
        return text + "\n\nPulni yechish uchun ilovada karta raqamingizni kiriting: PLAY → Pullik test → Karta.", 0
    return text + f"\n💳 Karta: {_format_card(teacher.card_number)}\n\nPul shu kartaga o'tkaziladi.", teacher.balance


def bot_request_withdrawal(db: Session, tg_id: int) -> tuple[bool, str]:
    """Botdagi "💸 Yechish" tugmasi: butun balansni o'qituvchi kartasiga yechish so'rovi."""

    teacher = _teacher_by_tg_id(db, tg_id, lock=True)  # ikki marta bosilsa — ikki so'rov bo'lmasin
    if not teacher:
        return False, "Bu bo'lim faqat o'qituvchilar uchun"
    if _pending_withdrawal(db, teacher):
        return False, "So'rovingiz allaqachon ko'rib chiqilmoqda"
    if teacher.balance <= 0:
        return False, "Balansda pul yo'q"
    if not teacher.card_number:
        return False, "Avval ilovada karta raqamingizni kiriting"
    request = WithdrawalRequest(teacher_id=teacher.id, amount=teacher.balance, card_number=_format_card(teacher.card_number))
    db.add(request)
    db.commit()
    _notify_admin_about_withdrawal(request, teacher)
    return True, (f"✅ {_format_sum(request.amount)} so'm yechish so'rovi yuborildi.\n"
                  f"💳 {request.card_number}\n\nAdmin pulni o'tkazgach, botda xabar keladi.")


@app.get("/api/teacher/withdrawal-requests")
def list_own_withdrawal_requests(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining o'zi yuborgan pul yechish so'rovlari tarixi."""

    requests = db.scalars(
        select(WithdrawalRequest)
        .where(WithdrawalRequest.teacher_id == teacher.id)
        .order_by(WithdrawalRequest.id.desc())
    ).all()
    return [{"id": r.id, "amount": r.amount, "status": r.status} for r in requests]


@app.get("/api/teacher/credit-price")
def preview_credit_price(credits: int, teacher: Teacher = Depends(get_current_teacher)):
    """O'qituvchi qancha token sotib olmoqchi ekanini yozganda, narxini jonli ko'rsatish uchun."""

    if credits < 1:
        raise HTTPException(status_code=422, detail="Token soni kamida 1 bo'lishi kerak")
    return {"credits": credits, "amount": _calculate_credit_price(credits)}


@app.post("/api/teacher/credit-purchases")
def create_credit_purchase(
    body: CreditPurchaseIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """Token yoki obuna buyurtmasini yaratadi va tanlangan tizimning (Payme/Click) to'lov havolasini qaytaradi."""

    _require_provider(body.provider)
    purchase = _new_purchase(db, teacher, body, method=body.provider)
    db.add(purchase)
    db.commit()

    return {
        "id": purchase.id,
        "kind": purchase.kind,
        "months": purchase.months,
        "label": _purchase_label(purchase),
        "credits": purchase.credits,
        "amount": purchase.amount,
        "checkout_url": _checkout_url(body.provider, purchase, purchase.amount),
    }


@app.get("/api/teacher/credit-purchases")
def list_own_credit_purchases(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    O'qituvchining o'zi yaratgan token sotib olish buyurtmalari tarixi.

    Payme/Click buyurtmasi "To'lash" bosilgan zahoti yaratiladi — mijoz to'lov sahifasini ochib,
    to'lamay chiqib ketsa, u "pending" (keyin muddati o'tib "cancelled") bo'lib qoladi va tarixda
    "Tekshirilmoqda"/"Rad etildi" bo'lib turardi. Shuning uchun onlayn buyurtma faqat pul haqiqatan
    o'tgan bo'lsa ko'rinadi. Karta orqali yuborilgan chek esa har doim ko'rinadi — uni admin tekshiradi.
    """

    purchases = db.scalars(
        select(CreditPurchase).where(CreditPurchase.teacher_id == teacher.id).order_by(CreditPurchase.id.desc())
    ).all()
    return [
        {"id": p.id, "kind": p.kind, "months": p.months, "credits": p.credits, "amount": p.amount,
         "status": p.status, "method": p.method, "label": _purchase_label(p)}
        for p in purchases
        if p.method == "card" or p.status == "paid" or p.performed_at is not None
    ]


# ========== 5.1) Karta orqali token sotib olish ==========
#
# O'qituvchi admin kartasiga o'zi pul o'tkazadi va chek rasmini yuboradi.
# Bot chekni admin'ga ✅/❌ tugmalari bilan yuboradi; admin tasdiqlasa,
# o'qituvchiga token avtomatik yoziladi va unga xabar boradi.
#
# Nega to'liq avtomatik emas: Uzcard/Humo shaxsiy kartaga tushgan pulni
# bot tekshira oladigan ochiq API bermaydi. Chekni soxtalashtirish oson,
# shuning uchun oxirgi qarorni pul tushganini ko'ra oladigan odam — admin
# qiladi. Unga faqat bitta tugmani bosish qoladi.

# Rasm turlarini ularning birinchi baytlaridan aniqlaymiz (fayl nomiga ishonmaymiz)
RECEIPT_SIGNATURES = {
    b"\xff\xd8\xff": ("image/jpeg", "jpg"),
    b"\x89PNG\r\n\x1a\n": ("image/png", "png"),
    b"RIFF": ("image/webp", "webp"),
}
RECEIPT_MAX_BYTES = 5 * 1024 * 1024


def _admin_cards() -> list[str]:
    """
    ADMIN_CARD_NUMBER'dagi kartalar. Qanday yozilgani muhim emas ("9860,3501,4880,5707",
    "8600 1234 5678 9012; 9860 ..."): raqamlar 16 tadan bo'linib, "9860 3501 4880 5707" bo'ladi.
    """
    digits = re.sub(r"\D", "", ADMIN_CARD_NUMBER)
    if not digits or len(digits) % 16:
        return [ADMIN_CARD_NUMBER.strip()] if ADMIN_CARD_NUMBER.strip() else []
    return [_format_card(digits[index:index + 16]) for index in range(0, len(digits), 16)]


def _card_topup_enabled() -> bool:
    """Karta orqali to'lov faqat karta raqami VA chekni oladigan admin bo'lsa ishlaydi."""
    return bool(ADMIN_CARD_NUMBER and ADMIN_ID and BOT_TOKEN)


def _decode_receipt(receipt_base64: str) -> tuple[bytes, str, str]:
    """Chek rasmini base64'dan o'qiydi va rasm ekanini tekshiradi."""

    # "data:image/jpeg;base64,..." ko'rinishida kelishi ham mumkin
    if receipt_base64.startswith("data:"):
        receipt_base64 = receipt_base64.split(",", 1)[-1]

    try:
        data = base64.b64decode(receipt_base64, validate=True)
    except (ValueError, binascii.Error):
        raise HTTPException(status_code=422, detail="Chek rasmi o'qilmadi, qaytadan tanlang")

    if len(data) > RECEIPT_MAX_BYTES:
        raise HTTPException(status_code=422, detail="Chek rasmi juda katta (5 MB dan oshmasin)")

    for signature, (mime, extension) in RECEIPT_SIGNATURES.items():
        if data.startswith(signature):
            if signature == b"RIFF" and data[8:12] != b"WEBP":
                continue
            return data, mime, extension

    raise HTTPException(status_code=422, detail="Chek rasm bo'lishi kerak (JPG, PNG yoki WEBP)")


def _format_sum(amount: int) -> str:
    """150000 -> "150 000" (o'qish oson bo'lishi uchun)."""
    return f"{amount:,}".replace(",", " ")


def _card_topup_caption(purchase: CreditPurchase, teacher: Teacher) -> str:
    return (
        "💳 Karta orqali to'lov\n\n"
        f"O'qituvchi: {teacher.user.full_name}\n"
        f"Telegram ID: {teacher.user.tg_id}\n"
        f"Xarid: {_purchase_label(purchase)}\n"
        f"Summa: {_format_sum(purchase.amount)} so'm\n\n"
        "Kartangizga shu summa tushganini tekshirib, tasdiqlang."
    )


def _send_receipt_to_admin(purchase: CreditPurchase, teacher: Teacher, image: bytes, mime: str, extension: str) -> str:
    """
    Chekni har bir admin'ning Telegram chatiga ✅/❌ tugmalari bilan yuboradi (qaysi biri
    birinchi bossa — o'sha hal qiladi; ikkinchi bosish "allaqachon ko'rib chiqilgan" bo'ladi).
    Telegram'dagi file_id'ni qaytaradi (rasm keyin shu orqali olinadi).
    """

    file_id = None
    last_error: Exception | None = None
    for admin_id in ADMIN_IDS:
        try:
            sent = _send_receipt_photo(admin_id, purchase, teacher, image, mime, extension)
            file_id = file_id or sent
        except Exception as error:  # biriga yetmasa ham qolganlariga yuboriladi
            last_error = error
            logging.getLogger("backend").warning("Chek %s ga yuborilmadi", admin_id, exc_info=True)
    if not file_id:
        raise last_error or RuntimeError("Admin sozlanmagan")
    return file_id


def _send_receipt_photo(admin_id: int, purchase: CreditPurchase, teacher: Teacher, image: bytes, mime: str, extension: str) -> str:
    response = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": admin_id,
            "caption": _card_topup_caption(purchase, teacher),
            "reply_markup": json.dumps(
                {
                    "inline_keyboard": [
                        [
                            {"text": "✅ Pul tushdi", "callback_data": f"topup_approve:{purchase.id}"},
                            {"text": "❌ Rad etish", "callback_data": f"topup_reject:{purchase.id}"},
                        ]
                    ]
                }
            ),
        },
        files={"photo": (f"chek_{purchase.id}.{extension}", image, mime)},
        timeout=30,
    )
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(data.get("description") or "Telegram rasmni qabul qilmadi")

    # Telegram bir rasmni bir nechta o'lchamda saqlaydi — eng kattasini olamiz
    return data["result"]["photo"][-1]["file_id"]


def _notify_teacher(teacher: Teacher, text: str) -> None:
    """O'qituvchiga bot orqali xabar yuboradi. Yuborilmasa ham jarayon to'xtamaydi."""

    try:
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": teacher.user.tg_id, "text": text},
            timeout=10,
        )
    except Exception:
        logging.getLogger("backend").warning("O'qituvchiga xabar yuborilmadi", exc_info=True)


def decide_card_topup(db: Session, purchase_id: int, approve: bool) -> tuple[bool, str]:
    """
    Karta orqali to'lovni tasdiqlaydi yoki rad etadi.

    Ikki joydan chaqiriladi: bot (admin chekdagi tugmani bosganda) va admin
    paneli. Mantiq bitta joyda bo'lgani uchun ikkala yo'l ham bir xil ishlaydi.

    Faqat "pending" holatdagi so'rov o'zgaradi — shuning uchun tugma ikki
    marta bosilsa ham token ikki marta yozilmaydi.

    (muvaffaqiyatli_mi, admin'ga ko'rsatiladigan matn) qaytaradi.
    """

    purchase = db.scalar(
        select(CreditPurchase).where(CreditPurchase.id == purchase_id).with_for_update()
    )
    if not purchase or purchase.method != "card":
        return False, "So'rov topilmadi"
    if purchase.status != "pending":
        return False, "So'rov allaqachon ko'rib chiqilgan"

    teacher = purchase.teacher
    amount_text = _format_sum(purchase.amount)

    if approve:
        purchase.status = "paid"
        purchase.performed_at = utcnow()
        _grant_purchase(purchase)
        db.commit()

        _notify_teacher(teacher, f"✅ To'lovingiz tasdiqlandi!\n\n{_purchase_granted_text(purchase, teacher)}")
        return True, f"✅ Tasdiqlandi: {teacher.user.full_name} — {_purchase_label(purchase)} ({amount_text} so'm)"

    purchase.status = "cancelled"
    purchase.cancelled_at = utcnow()
    db.commit()

    _notify_teacher(
        teacher,
        f"❌ {amount_text} so'mlik to'lovingiz tasdiqlanmadi.\n\n"
        "Chek noto'g'ri yoki kartaga pul tushmagan bo'lishi mumkin. "
        "Savol bo'lsa, admin bilan bog'laning.",
    )
    return True, f"❌ Rad etildi: {teacher.user.full_name} — {_purchase_label(purchase)} ({amount_text} so'm)"


@app.post("/api/teacher/card-topups")
def create_card_topup(
    body: CardTopUpIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi admin kartasiga pul o'tkazgach, chek rasmini yuboradi."""

    if not _card_topup_enabled():
        raise HTTPException(status_code=503, detail="Karta orqali to'lov hali sozlanmagan, admin bilan bog'laning")

    # Bir vaqtda faqat bitta kutilayotgan so'rov — aks holda bir xil chek
    # bilan ketma-ket so'rov yuborib, admin'ni chalg'itish mumkin edi.
    waiting = db.scalar(
        select(CreditPurchase.id).where(
            CreditPurchase.teacher_id == teacher.id,
            CreditPurchase.method == "card",
            CreditPurchase.status == "pending",
        )
    )
    if waiting:
        raise HTTPException(
            status_code=409,
            detail="Oldingi to'lovingiz hali tekshirilmoqda. Admin tasdiqlagach, yangisini yuborishingiz mumkin.",
        )

    image, mime, extension = _decode_receipt(body.receipt_base64)
    purchase = _new_purchase(db, teacher, body, method="card")
    db.add(purchase)
    db.commit()

    try:
        purchase.receipt_file_id = _send_receipt_to_admin(purchase, teacher, image, mime, extension)
        db.commit()
    except Exception:
        # Admin chekni ko'rmasa, so'rov hech qachon tasdiqlanmaydi — shuning
        # uchun uni saqlab qo'ymaymiz va o'qituvchiga qayta urinishni aytamiz.
        logging.getLogger("backend").warning("Chek admin'ga yuborilmadi", exc_info=True)
        purchase.status = "failed"  # yozuv iz sifatida qoladi
        db.commit()
        raise HTTPException(status_code=502, detail="Chek yuborilmadi, bir ozdan keyin qayta urinib ko'ring")

    return {"id": purchase.id, "kind": purchase.kind, "months": purchase.months, "credits": purchase.credits,
            "amount": purchase.amount, "status": purchase.status}


def _participant_key_expression():
    """
    Qatnashchini ajratuvchi kalit (SQL ichida).

    Telegram foydalanuvchisi bo'lsa — "user:12", o'qituvchi qo'lda qo'shgan
    o'quvchi bo'lsa — "offline:hasan hasanov". Prefiks shuning uchun kerakki,
    ism bilan raqam adashib ketmasin. best_attempts() bilan bir xil mantiq.
    """

    return case(
        (Attempt.user_id.is_not(None), "user:" + cast(Attempt.user_id, String)),
        else_="offline:" + func.lower(func.coalesce(Attempt.offline_name, "")),
    )


@app.get("/api/teacher/tests")
def list_tests(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    O'qituvchining barcha testlari ro'yxati (eng yangisi birinchi), qatnashchilar soni bilan.

    Qatnashchilar soni va savollar BITTA so'rovda olinadi. Ilgari har bir test
    uchun alohida so'rov ketardi (qatnashchilar uchun bitta, savollar uchun
    yana bitta) — 50 ta testi bor o'qituvchida bu 100 dan ortiq so'rov degani
    edi va eng ko'p ochiladigan ekran sekin ochilardi.
    """

    tests = db.scalars(
        select(Test)
        .where(Test.teacher_id == teacher.id, Test.deleted_at.is_(None))
        .options(selectinload(Test.key_questions), selectinload(Test.questions))
        .order_by(Test.id.desc())
    ).all()

    if not tests:
        return []

    participant_counts = dict(
        db.execute(
            select(Attempt.test_id, func.count(distinct(_participant_key_expression())))
            .where(Attempt.test_id.in_([test.id for test in tests]), Attempt.status == "done")
            .group_by(Attempt.test_id)
        ).all()
    )

    return [
        {
            "id": test.id,
            "code": test.code,
            "title": test.title,
            "subject": test.subject,
            "mode": test.mode,
            "enrollment_type": test.enrollment_type,
            "status": test.status,
            "created_at": test.created_at,
            "count": _question_count(test),
            "price": test.price,
            "students": participant_counts.get(test.id, 0),
        }
        for test in tests
    ]


@app.post("/api/teacher/tests")
def create_test(body: TestIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yangi test (va uning savollarini) yaratadi, tasodifiy noyob kod beradi."""

    if body.mode == "key" and not body.key_questions:
        raise HTTPException(status_code=422, detail="Kamida bitta savol kiriting")
    if body.mode == "full" and not body.questions:
        raise HTTPException(status_code=422, detail="Kamida bitta savol kiriting")
    if body.price > 0 and body.enrollment_type == "closed":
        raise HTTPException(status_code=422, detail="Pullik testlar faqat online test sifatida yaratiladi")
    if body.enrollment_type == "closed" and any(
        kq.question_type != "text" and kq.option_count > OFFLINE_MAX_OPTIONS for kq in body.key_questions
    ):
        # Titulda bir qatorga ko'pi bilan 6 ta doiracha (A–F) sig'adi
        raise HTTPException(status_code=422, detail=f"Offline testda variantlar ko'pi bilan {OFFLINE_MAX_OPTIONS} ta (A–F)")
    if body.price > 0 and not _can_sell_tests(teacher):
        raise HTTPException(status_code=422, detail="Pullik test uchun avval karta raqamingizni kiriting")

    # Tugash vaqti o'tib ketgan test darhol o'zi yopilib, o'quvchilarga "Test yakunlangan" chiqardi
    # (masalan soatda 15:00 o'rniga 03:00 tanlansa) — yaratishda ham tahrirlashdagidek tekshiriladi
    start_time, end_time = _as_utc_naive(body.start_time), _as_utc_naive(body.end_time)
    if end_time is not None and end_time <= utcnow():
        raise HTTPException(
            status_code=422,
            detail=f"Tugash vaqti o'tib ketgan ({_local_time(end_time)}) — kelajakdagi vaqtni tanlang",
        )
    if start_time and end_time and end_time <= start_time:
        raise HTTPException(
            status_code=422,
            detail=f"Tugash vaqti ({_local_time(end_time)}) boshlanish vaqtidan ({_local_time(start_time)}) keyin bo'lishi kerak",
        )

    # Majburiy obuna so'ralgan bo'lsa, bot kanalni haqiqatan tekshira olishini
    # SHU YERDA aniqlaymiz. Aks holda o'qituvchi testni tarqatib yuborgandan
    # keyin, o'quvchilar kira olmay qolganda bilardi.
    # Oflayn (sinfda, titul bilan) testda kanal yo'q — o'quvchi ilovaga kirmaydi.
    required_channel = None if body.enrollment_type == "closed" else _normalize_channel(body.required_channel)
    if required_channel:
        _ensure_bot_can_check_channel(required_channel)

    # Esseli testda mavzu majburiy (o'qituvchi ham, AI ham tekshirganda): o'quvchi uni esse maydoni ustida ko'radi,
    # AI esa mavzuga moslikni ham baholaydi. Oflayn esse qog'ozda — AI o'qiy olmaydi
    essay_on = bool(body.essay and essay_max(body.subject))
    essay_by_ai = essay_on and body.essay_ai and body.enrollment_type != "closed"
    essay_topic = (body.essay_topic or "").strip()
    if essay_on and not essay_topic:
        raise HTTPException(status_code=422, detail="Esse mavzusini kiriting — o'quvchilar esseni shu mavzuda yozadi")

    code = _new_test_code(db)

    test = Test(
        teacher_id=teacher.id,
        code=code,
        title=body.title,
        subject=body.subject,
        topic=body.topic,
        price=body.price,
        duration=body.duration,
        mode=body.mode,
        telegram_group_id=body.telegram_group_id,
        required_channel=required_channel,
        enrollment_type=body.enrollment_type,
        start_time=start_time,
        end_time=end_time,
        essay_max=essay_max(body.subject) if body.essay else None,
        essay_ai=essay_by_ai,
        essay_topic=essay_topic if essay_on else None,
        test_type=body.test_type,
        questions=[Question(**question.model_dump()) for question in body.questions] if body.mode == "full" else [],
        key_questions=(
            [KeyQuestion(**key_question.model_dump()) for key_question in body.key_questions]
            if body.mode == "key"
            else []
        ),
    )
    db.add(test)
    db.commit()

    return {"id": test.id, "code": test.code}


# E'lon: Telegram bir botdan sekundiga ~30 xabarga ruxsat beradi — 20 tasidan oshirmaymiz
BROADCAST_INTERVAL = 0.05


def _broadcast_text(test: Test, teacher_user: User, profile_link: bool = True) -> str:
    """Bot foydalanuvchilariga boradigan e'lon: test, pullik/bepul va kim ochgani (profil havolasi)."""

    name = html_escape(teacher_user.full_name or "O'qituvchi")
    profile = f'<a href="tg://user?id={teacher_user.tg_id}">{name}</a>' if profile_link else f"<b>{name}</b>"
    if teacher_user.username:
        profile += f" (@{html_escape(teacher_user.username)})"
    price = f"💰 Pullik — {_format_sum(test.price)} so'm" if test.price > 0 else "🆓 Bepul"
    lines = [
        "📢 <b>Yangi test!</b>",
        "",
        f"📘 <b>{html_escape(test.title)}</b>",
        f"📚 {html_escape(test.subject)} · {_question_count(test)} ta savol · {test.duration} daqiqa",
        price,
    ]
    if test.start_time:
        lines.append(f"🕒 Boshlanishi: {_local_time(test.start_time)}")
    if test.end_time:
        lines.append(f"⏳ Tugashi: {_local_time(test.end_time)}")
    lines += [f"🔑 Test kodi: <code>{test.code}</code>", "", f"👤 Testni ochgan: {profile}"]
    return "\n".join(lines)


def _broadcast_button(code: str) -> dict | None:
    if not WEBAPP_URL.startswith("https://"):
        return None
    separator = "&" if "?" in WEBAPP_URL else "?"
    return {"inline_keyboard": [[{"text": "▶️ Testni ochish", "web_app": {"url": f"{WEBAPP_URL}{separator}test_code={code}"}}]]}


def _run_broadcast(test_id: int) -> None:
    """Fonda: e'lonni botning barcha foydalanuvchilariga yuboradi, oxirida o'qituvchiga hisobot."""

    logger = logging.getLogger("backend")
    with SessionLocal() as session:
        test = session.get(Test, test_id)
        teacher_user = test.teacher.user
        markup, code = _broadcast_button(test.code), test.code
        # Telegram xabarni ko'rinishi uchun rad etsa (400 — profil havolasi yoki tugma),
        # keyingi soddaroq ko'rinishga o'tamiz: e'lon umuman ketmay qolmasin
        variants = [
            (_broadcast_text(test, teacher_user), markup),
            (_broadcast_text(test, teacher_user, profile_link=False), markup),
            (_broadcast_text(test, teacher_user, profile_link=False), None),
        ]
        teacher_tg_id = teacher_user.tg_id
        chat_ids = session.scalars(
            select(User.tg_id).where(User.tg_id != teacher_tg_id, User.tg_id > 0).order_by(User.id)  # o'chirilganlarsiz
        ).all()

    delivered, variant = 0, 0
    for chat_id in chat_ids:
        while True:
            text, buttons = variants[variant]
            status, error = _telegram_send_status(chat_id, text, buttons, parse_mode="HTML")
            # "chat not found" kabi xato faqat shu odamga tegishli — ko'rinishni o'zgartirmaymiz
            if status != 400 or "chat not found" in error.lower() or variant == len(variants) - 1:
                break
            variant += 1
        if status == 200:
            delivered += 1
        time.sleep(BROADCAST_INTERVAL)
    logger.info("E'lon yuborildi: test=%s, %s/%s", test_id, delivered, len(chat_ids))
    _telegram_send_message(
        teacher_tg_id, f"📢 «{code}» testi haqidagi e'lon {delivered} ta foydalanuvchiga yetkazildi."
    )


def _start_broadcast(test_id: int) -> None:
    threading.Thread(target=_run_broadcast, args=(test_id,), name=f"broadcast-{test_id}", daemon=True).start()


@app.post("/api/teacher/tests/{test_id}/broadcast")
def broadcast_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Testni bot foydalanuvchilarining barchasiga e'lon qiladi — har bir test uchun bir marta."""

    test = db.scalar(select(Test).where(Test.id == test_id, Test.teacher_id == teacher.id).with_for_update())
    blocker = _broadcast_blocker(test)
    if blocker:
        raise HTTPException(status_code=404 if blocker == "Test topilmadi" else 409, detail=blocker)
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Bot ulanmagan")

    # Pullik e'lon: Payme buyurtmasi yaratiladi, e'lon to'lov o'tgach o'zi yuboriladi (_grant_broadcast)
    price = _broadcast_price(db)
    if price > 0:
        if "payme" not in _enabled_providers():
            raise HTTPException(status_code=503, detail="Onlayn to'lov (Payme) hali sozlanmagan, admin bilan bog'laning")
        purchase = CreditPurchase(teacher_id=teacher.id, kind="broadcast", test_id=test.id, credits=0,
                                  amount=price, method="payme")
        db.add(purchase)
        db.commit()
        return {"ok": True, "payment_required": True, "amount": price,
                "checkout_url": _checkout_url("payme", purchase, price)}

    test.broadcast_at = utcnow()
    db.commit()
    _start_broadcast(test.id)
    return {"ok": True, "recipients": db.scalar(select(func.count()).select_from(User)) - 1}


@app.get("/api/teacher/tests/{test_id}")
def test_detail(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Test yaratilgandan keyin ochiladigan asosiy sahifa uchun ma'lumot:
    testning o'zi va unga qo'shilgan o'quvchilar ro'yxati.

    Yopiq testda o'quvchini o'qituvchi qo'shadi — shunda avval faqat ismi
    saqlanadi ("javoblari kiritilmagan"), javoblari keyin kiritiladi.
    """

    test = _get_own_test(db, teacher, test_id)
    _ensure_sheet_numbers(db, test)

    students = []
    participants = _all_participants(db, test)
    for attempt, name in participants:
        students.append(
            {
                "id": attempt.id,
                "name": name,
                "has_answers": attempt.status == "done",
                "correct": attempt.correct,
                "wrong": attempt.wrong,
                "percent": attempt.percent,
                "editable": attempt.user_id is None,  # faqat o'qituvchi qo'shgan o'quvchi tahrirlanadi
                "number": attempt.sheet_number,
                "variant": attempt.variant,
            }
        )

    filled = sum(1 for student in students if student["has_answers"])

    return {
        "id": test.id,
        "code": test.code,
        "title": test.title,
        "subject": test.subject,
        "topic": test.topic,
        "mode": test.mode,
        "test_type": test.test_type or "rash",
        "price": test.price,
        "enrollment_type": test.enrollment_type,
        "required_channel": test.required_channel,
        "required_channels": _required_channels(test),
        "status": test.status,
        "created_at": test.created_at,
        "count": _question_count(test),
        "duration": test.duration,
        "start_time": test.start_time,
        "end_time": test.end_time,
        "has_attempts": _test_has_attempts(db, test),
        "broadcast_at": test.broadcast_at,
        "broadcast_price": _broadcast_price(db),
        "sheet_version": test.sheet_version or 1,
        "variants": _variant_names(db, test),
        "students": students,
        "filled": filled,
        "missing": len(students) - filled,
        # Titulda qo'lda yozilgan javoblar: {total, unchecked} (yozma savolli oflayn testda)
        "written": _written_stats(db, test),
        # Javoblar kaliti kiritilmagan savollar soni — test sahifasida eslatma ("Javoblarni kiriting")
        "key_missing": _key_missing(test),
        # Avtomatik tozalash: ochiq test qachon o'zi yopiladi, yopilgani qachon o'chiriladi
        "auto_close_at": _test_auto_close_at(test),
        "auto_delete_at": _test_auto_delete_at(test),
    }


def _key_missing(test: Test) -> int:
    """Kaliti hali kiritilmagan savollar soni (Rash testni kalitsiz saqlash mumkin)."""

    return sum(1 for kq in test.key_questions if not (kq.correct or "").strip()) if test.mode == "key" else 0


def _test_has_attempts(db: Session, test: Test) -> bool:
    """O'quvchilardan kimdir testni boshlagan yoki tugatganmi (o'qituvchi qo'shgan ismlar hisobga olinmaydi)."""
    return bool(db.scalar(
        select(Attempt.id).where(Attempt.test_id == test.id, Attempt.user_id.is_not(None)).limit(1)
    ))


def _as_utc_naive(moment: datetime | None) -> datetime | None:
    """Bazada vaqt UTC'da, mintaqasiz saqlanadi."""
    if moment is not None and moment.tzinfo is not None:
        moment = moment.astimezone(timezone.utc).replace(tzinfo=None)
    return moment


def _new_test_code(db: Session) -> str:
    while True:
        code = "".join(secrets.choice(TEST_CODE_ALPHABET) for _ in range(TEST_CODE_LENGTH))
        if not db.scalar(select(Test.id).where(Test.code == code)):
            return code


@app.patch("/api/teacher/tests/{test_id}")
def edit_test(test_id: int, body: TestEditIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Testni tahrirlash: nomi, davomiyligi, boshlanish va tugash vaqti.

    O'quvchilar ishlay boshlagan bo'lsa — ularga zarar yetmasligi uchun:
    davomiylik faqat UZAYTIRILADI, boshlanish vaqti o'zgarmaydi.
    Tugash vaqti o'tib, test o'zi yopilgan bo'lsa — vaqt cho'zilganda qayta ochiladi.
    """

    test = _get_own_test(db, teacher, test_id)
    fields = body.model_fields_set
    started = _test_has_attempts(db, test)
    now = utcnow()

    start_time = _as_utc_naive(body.start_time) if "start_time" in fields else test.start_time
    end_time = _as_utc_naive(body.end_time) if "end_time" in fields else test.end_time

    if "duration" in fields and body.duration is not None and started and body.duration < test.duration:
        raise HTTPException(
            status_code=409,
            detail=f"O'quvchilar testni ishlay boshlagan — davomiylikni faqat uzaytirish mumkin (hozir {test.duration} daqiqa)",
        )
    if "start_time" in fields and started and start_time != test.start_time:
        raise HTTPException(status_code=409, detail="O'quvchilar testni boshlagan — boshlanish vaqtini o'zgartirib bo'lmaydi")
    if "end_time" in fields and end_time is not None and end_time != test.end_time and end_time <= now:
        raise HTTPException(status_code=422, detail="Tugash vaqti kelajakda bo'lishi kerak")
    if start_time and end_time and end_time <= start_time:
        raise HTTPException(status_code=422, detail="Tugash vaqti boshlanish vaqtidan keyin bo'lishi kerak")

    if "title" in fields and body.title:
        test.title = " ".join(body.title.split())
    if "duration" in fields and body.duration is not None:
        test.duration = body.duration
    if "test_type" in fields and body.test_type:
        test.test_type = body.test_type
    test.start_time, test.end_time = start_time, end_time

    # Vaqti tugab o'zi yopilgan test cho'zilsa — qayta ochiladi (o'qituvchi aynan shuni xohlaydi)
    reopened = False
    if "end_time" in fields and test.status == "closed" and (end_time is None or end_time > now):
        _mark_test_reopened(test)
        test.results_sent_at = None
        test.code = _new_test_code(db)  # qayta ochilgan test — yangi kod bilan
        reopened = True

    db.commit()
    logging.getLogger("backend").info("Test tahrirlandi: id=%s maydonlar=%s", test.id, sorted(fields))
    return {
        "title": test.title,
        "duration": test.duration,
        "start_time": test.start_time,
        "end_time": test.end_time,
        "status": test.status,
        "reopened": reopened,
        "code": test.code,
    }


@app.delete("/api/teacher/tests/{test_id}")
def delete_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Testni o'qituvchining ro'yxatidan o'chiradi.

    MUHIM: bazadan BUTUNLAY o'chirilmaydi. Ilgari test bilan birga uning
    to'lovlari (Payment) ham o'chib ketardi — pul hisobi buzilardi — va
    o'quvchilar natijalari qaytarib bo'lmas darajada yo'qolardi. Endi test
    faqat yashiriladi: o'quvchilar uni ochib/boshlay olmaydi, o'qituvchi
    ro'yxatida ko'rinmaydi, lekin hamma ma'lumot zaxira nusxada ham, bazada
    ham turadi va admin uni qayta tiklay oladi.
    """

    test = _get_own_test(db, teacher, test_id)
    test.deleted_at = utcnow()
    if test.status != "closed":
        _mark_test_closed(test)
    db.commit()

    logging.getLogger("backend").info("Test arxivga o'tkazildi: id=%s kod=%s o'qituvchi=%s", test.id, test.code, teacher.id)
    return {"ok": True}


@app.post("/api/teacher/tests/{test_id}/students")
def add_students(
    test_id: int, body: StudentNamesIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """Yopiq testga o'quvchilarni ism bo'yicha qo'shadi (javoblari keyin kiritiladi)."""

    test = _get_own_test(db, teacher, test_id)
    if test.enrollment_type != "closed":
        raise HTTPException(status_code=400, detail="O'quvchi qo'shish faqat offline testlarda")
    if test.status == "closed":
        raise HTTPException(status_code=400, detail="Test yakunlangan")

    added = 0
    for raw_name in body.names:
        name = raw_name.strip()
        if len(name) < 2:
            continue
        db.add(Attempt(test_id=test.id, offline_name=name[:200], status="pending"))
        added += 1

    if not added:
        raise HTTPException(status_code=422, detail="Hech bo'lmaganda bitta ism kiriting")

    db.commit()
    _ensure_sheet_numbers(db, test)
    return {"added": added}


@app.post("/api/teacher/tests/{test_id}/students/import")
def read_test_students_file(
    test_id: int, body: StudentImportIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """
    Offline testga o'quvchilar ro'yxatini Excel (.xlsx) yoki CSV fayldan o'qiydi — hali SAQLAMAYDI:
    ismlar formaga tushadi, o'qituvchi ko'rib-tuzatib, keyin qo'shadi.
    """

    test = _get_own_test(db, teacher, test_id)
    if test.enrollment_type != "closed":
        raise HTTPException(status_code=400, detail="O'quvchi qo'shish faqat offline testlarda")
    try:
        if not body.file_base64:
            raise ValueError("Fayl yuborilmadi")
        try:
            data = base64.b64decode(body.file_base64.split(",")[-1], validate=True)
        except (binascii.Error, ValueError):
            raise ValueError("Fayl buzilgan — qayta tanlab ko'ring")
        names = parse_student_file(body.file_name or "", data)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if not names:
        raise HTTPException(status_code=422, detail="Fayldan birorta ham ism topilmadi")

    existing = {
        (name or "").lower()
        for name in db.scalars(
            select(Attempt.offline_name).where(
                Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done"))
            )
        ).all()
    }
    return {
        "names": [name for name in names if name.lower() not in existing],
        "already_in_test": [name for name in names if name.lower() in existing],
    }


@app.delete("/api/teacher/tests/{test_id}/students/{attempt_id}")
def delete_student(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi o'zi qo'shgan o'quvchini ro'yxatdan o'chiradi."""

    test = _get_own_test(db, teacher, test_id)
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id or attempt.user_id is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

    # Butunlay o'chirilmaydi — "deleted" holati ro'yxat va natijalardan
    # chiqaradi, lekin javoblar bazada qoladi (adashib o'chirilsa tiklanadi).
    attempt.status = "deleted"
    db.commit()
    return {"ok": True}


@app.get("/api/teacher/tests/{test_id}/students/{attempt_id}/detail")
def student_answer_detail(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """
    O'quvchining har bir savol bo'yicha javobi: nimani belgilagan, to'g'risi nima edi.

    Bu yerda o'quvchi o'zi ishlagan urinish ham, o'qituvchi kiritgan javoblar
    ham ko'rinadi — shuning uchun qaysi savolda xato qilgani aniq bo'ladi.
    """

    test = _get_own_test(db, teacher, test_id)

    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id or attempt.status not in ("done", "pending"):
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

    return {
        **_attempt_review(db, test, attempt),
        "edit": _teacher_answer_sheet(db, test, attempt),
        # Yozma javobni qo'lda kiritish klaviaturasi: "math" (√, kasr) yoki "text" (oddiy harflar)
        "keyboard": subject_keyboard(test.subject),
    }


def _teacher_answer_sheet(db: Session, test: Test, attempt: Attempt) -> dict:
    """
    O'qituvchining "batafsil" ekrani uchun: har savol — o'quvchi belgilagani, kalit (o'z varianti bo'yicha),
    variantlar soni; yozma savolda — skanerlangan katak rasmi, bahosi va AI o'qigani. Shuningdek Rasch bali.
    O'qituvchi qo'shgan (oflayn) o'quvchining javoblarini shu ekranda o'zgartirsa bo'ladi.
    """

    answers = {
        answer.question_ref: answer
        for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
    }
    written = {
        row.question_ref: row
        for row in db.scalars(select(WrittenAnswer).where(WrittenAnswer.attempt_id == attempt.id)).all()
    }
    items = []
    if test.mode == "key":
        overrides = _key_override(db, test, attempt.variant)
        for kq in test.key_questions:
            answer = answers.get(kq.number)
            item = {
                "ref": kq.number, "label": band_label(test.subject, kq.label) or str(kq.number), "type": kq.question_type,
                "options": kq.option_count, "chosen": (answer.chosen if answer else "") or "",
                "key": overrides.get(kq.number, kq.correct), "is_correct": bool(answer and answer.is_correct),
            }
            row = written.get(kq.number) if kq.question_type == "text" else None
            if row:
                item.update({
                    "image": row.image, "verdict": row.verdict, "blank": row.blank, "ai_text": row.ai_text,
                    "ai_sure": row.ai_sure, "ai_status": row.ai_status, "auto": bool(row.auto_verdict),
                })
            items.append(item)
    else:
        for index, question in enumerate(test.questions, start=1):
            answer = answers.get(question.id)
            items.append({
                "ref": question.id, "label": str(index), "type": "single", "options": 4,
                "chosen": (answer.chosen if answer else "") or "", "key": question.correct,
                "is_correct": bool(answer and answer.is_correct),
            })

    row = None
    uses_rasch = False
    if attempt.status == "done":
        report = build_rasch_report(db, test)
        uses_rasch = bool(report.get("uses_rasch"))
        row = next((item for item in report["rows"] if item["attempt_id"] == attempt.id), None)
    return {
        "editable": attempt.user_id is None,  # o'qituvchi qo'shgan o'quvchi — javoblari o'zgartiriladi
        "pending": attempt.status != "done",
        "number": attempt.sheet_number,
        "variant": attempt.variant or "A",
        "variants": _variant_names(db, test),
        "items": items,
        "uses_rasch": uses_rasch,
        "ball": row["ball"] if row else None,
        "grade": row["grade"] if row else None,
        # Esseli testda: test qismi (Rasch) va esse kutilyaptimi — umumiy ball shunga qarab tushuniladi
        "test_ball": row["test_ball"] if row else None,
        "essay_ball": row["essay_ball"] if row else None,
        "cert_percent": row["cert_percent"] if row else None,
        "essay_pending": bool(row and row["essay_pending"]),
        "dtm": _dtm_attempt_view(db, attempt, row) if row and row.get("dtm_ball") is not None else None,
    }


def _dtm_attempt_view(db: Session, attempt: Attempt, row: dict) -> dict | None:
    """
    DTM natijasining bloklari: ball va sertifikat (qayerdan — titul yoki o'qituvchi kiritgani).
    """

    scores = row.get("section_scores") or {}
    if not scores:
        return None
    names = list(scores)
    mandatory = [score["mandatory"] for score in scores.values()]
    try:
        overrides = json.loads(attempt.dtm_certificates or "{}")
    except ValueError:
        overrides = {}
    return {
        "dtm_ball": row["dtm_ball"],
        "levels": list(DTM_LANG_PERCENT),
        "blocks": [
            {"index": index, "name": name, "mandatory": score["mandatory"], "total": score["total"], "max": score["max"],
             "ball": score["ball"], "correct": score["correct"], "certificate": score.get("certificate"),
             "missing": bool(score.get("missing")), "override": overrides.get(str(index))}
            for index, (name, score) in enumerate(scores.items())
        ],
    }


@app.put("/api/teacher/tests/{test_id}/students/{attempt_id}/certificates")
def set_dtm_certificates(
    test_id: int, attempt_id: int, body: DtmCertificatesIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """
    DTM natijasiga sertifikatlar: majburiy fanda — "bor" (10 dan 10), asosiy fanda — Milliy sertifikat bali
    yoki chet tili darajasi. "auto" / "none" — sertifikat yo'q (blok javoblar bo'yicha hisoblanadi).
    """

    test = _get_own_test(db, teacher, test_id)
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    if attempt.status != "done":
        raise HTTPException(status_code=400, detail="Avval o'quvchining javoblarini kiriting")
    row = next((item for item in build_rasch_report(db, test)["rows"] if item["attempt_id"] == attempt.id), None)
    if not row or row.get("dtm_ball") is None or not row.get("section_scores"):
        raise HTTPException(status_code=400, detail="Sertifikat faqat DTM testida (bo'limlari bilan) hisoblanadi")
    blocks = list(row["section_scores"].values())
    try:
        overrides = json.loads(attempt.dtm_certificates or "{}")
    except ValueError:
        overrides = {}
    for block in body.blocks:
        if block.index >= len(blocks):
            raise HTTPException(status_code=422, detail="Bunday blok yo'q")
        key = str(block.index)
        if block.kind in ("auto", "none"):
            overrides.pop(key, None)
        elif block.kind == "claimed":
            # Titulda bo'yalgan ball / daraja saqlanib qoladi
            previous = overrides.get(key) if isinstance(overrides.get(key), dict) else {}
            overrides[key] = {**{name: previous[name] for name in ("sheet_kind", "sheet_value") if name in previous},
                              "kind": "claimed"}
        elif blocks[block.index]["mandatory"]:
            overrides[key] = {"kind": "cert"}  # majburiy fanda turi va bali ahamiyatsiz — 10 dan 10
        elif block.kind == "cert":
            raise HTTPException(status_code=422, detail="Asosiy fan uchun sertifikat turi va qiymatini kiriting")
        else:
            value = _clean_certificate_value(block.kind, block.value)
            if value is None:
                raise HTTPException(status_code=422, detail=(
                    f"Milliy sertifikat bali 0 dan {MS_BALL_MAX:g} gacha bo'lsin" if block.kind == "ms"
                    else "Chet tili darajasi: " + ", ".join(DTM_LANG_PERCENT)))
            overrides[key] = {"kind": block.kind, "value": value}
    attempt.dtm_certificates = json.dumps(overrides, ensure_ascii=False) if overrides else None
    db.commit()
    return _teacher_answer_sheet(db, test, attempt)["dtm"]


def _store_sheet_certificates(attempt: Attempt, marks: dict[str, str | None]) -> None:
    """
    DTM titulidagi sertifikat bo'limi: majburiy fanda "Ha" — 10 dan 10; asosiy fanda bo'yalgan ball ("ms:61.11")
    yoki daraja ("lang:B2") — sertifikat qo'llanadi.
    "Yo'q", bo'yalmagan yoki aniq o'qilmagan — o'quvchining javoblari.
    """

    try:
        overrides = json.loads(attempt.dtm_certificates or "{}")
    except ValueError:
        overrides = {}
    for key, value in marks.items():
        entry: dict = {"kind": "none"}
        if value == "yes":
            entry = {"kind": "claimed"}
        elif isinstance(value, str) and ":" in value:
            kind, raw = value.split(":", 1)
            clean = _clean_certificate_value(kind, raw) if kind in ("ms", "lang") else None
            if clean:
                entry = {"kind": "claimed", "sheet_kind": kind, "sheet_value": clean}
        # Kalit — fan bloklari: "1-3" (Matematika majburiyda ham, asosiy fanda ham) yoki bittasi "4"
        for part in str(key).split("-"):
            if part.isdigit() and int(part) <= 20:
                overrides[str(int(part))] = dict(entry)
    attempt.dtm_certificates = json.dumps(overrides, ensure_ascii=False) if overrides else None


def _attempt_review(db: Session, test: Test, attempt: Attempt) -> dict:
    """Urinishning har bir savol bo'yicha tahlili — o'qituvchi ham, (test tugagach) o'quvchi ham ko'radi."""

    student = db.get(User, attempt.user_id) if attempt.user_id else None
    answers = {
        answer.question_ref: answer
        for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
    }

    questions = []
    if test.mode == "key":
        overrides = _key_override(db, test, attempt.variant)
        for group in _group_key_questions(test):
            for key_question in group:
                answer = answers.get(key_question.number)
                key_correct = overrides.get(key_question.number, key_question.correct)
                questions.append(
                    {
                        "label": _key_question_label(key_question),
                        "kind": key_question.question_type,
                        "chosen": (answer.chosen if answer else None) or "—",
                        "correct": " / ".join(_split_accepted_variants(key_correct)),
                        "is_correct": bool(answer and answer.is_correct),
                    }
                )
    else:
        for index, question in enumerate(test.questions, start=1):
            answer = answers.get(question.id)
            questions.append(
                {
                    "label": f"{index}-savol",
                    "kind": "single",
                    "chosen": (answer.chosen if answer else None) or "—",
                    "correct": question.correct,
                    "is_correct": bool(answer and answer.is_correct),
                }
            )

    # Yopiq (variantli) va yozma savollar alohida sanaladi:
    # "Yopiq 15/35", "Yozma 5/10" ko'rinishida ko'rsatish uchun.
    #
    # Sanoq natijalar jadvali bilan BIR XIL usulda olinadi (score_splits) —
    # ya'ni a/b qismli yozma savol bitta savol deb hisoblanadi. Ilgari bu yerda
    # qismlar alohida sanalardi va "Yopiq 35/35 + Yozma 20/20" yig'indisi
    # umumiy "45" dan katta chiqib, o'qituvchini chalg'itardi.
    splits = score_splits(db, test, [attempt])[attempt.id]

    return {
        "name": student.full_name if student else (attempt.offline_name or "Noma'lum"),
        "correct": attempt.correct,
        "wrong": attempt.wrong,
        "total": _question_count(test),
        "percent": attempt.percent,
        "seconds": attempt.seconds,
        **splits,
        "questions": questions,
        "essay": _essay_review(test, attempt),
        # Milliy sertifikat titulidan skanerlangan yozma ish yechimi betlari (Kimyo/Biologiya)
        "solution_photos": db.scalar(select(func.count()).select_from(SheetImage)
                                     .where(SheetImage.attempt_id == attempt.id)) or 0,
    }


def _essay_review(test: Test, attempt: Attempt) -> dict | None:
    """Esse: raqami, maksimal bali, qo'yilgan ball (None — baholanmagan) va o'quvchi yozgan matn."""
    if not test.essay_max:
        return None
    from .ai_reader import ai_enabled

    text_value = attempt.essay_text or ""
    return {
        "number": _essay_number(test),
        "max": test.essay_max,
        "score": attempt.essay_score,
        "text": text_value,
        "words": len(text_value.split()),
        "photos": len(_essay_photo_pages(object_session(attempt), attempt.id)),  # qog'ozdagi esse rasmlari
        # AI taklifi: o'qituvchi "AI bilan baholash" ni bosganda to'ladi
        "topic": test.essay_topic,
        "ai_enabled": ai_enabled(),
        "ai_auto": bool(test.essay_ai),  # AI baholaydigan test: topshirilgach ballni AI o'zi qo'yadi
        "ai_score": attempt.essay_ai_score,
        "ai_comment": attempt.essay_ai_comment,
    }


def _ai_grade_essay_now(db: Session, test: Test, attempt: Attempt, *, overwrite: bool) -> tuple[int, str]:
    """
    Esseni AI baholaydi: AI bahosi va izohi saqlanadi va ball (essay_score) qo'yiladi.
    overwrite=False (fonda) — o'qituvchi shu orada ball qo'ygan bo'lsa, unga tegilmaydi.
    So'zlar soniga qat'iy chegara yo'q — qisqa esseni AI o'zi past baholaydi. AI xatosi — AIReadError.
    """

    from .ai_reader import grade_essay

    text_value = (attempt.essay_text or "").strip()
    score, comment = grade_essay(test.subject, text_value, test.essay_max, test.essay_topic or "")
    db.refresh(attempt)  # AI o'qiyotganda o'qituvchi ball qo'ygan bo'lishi mumkin — bazadagisi olinadi
    attempt.essay_ai_score, attempt.essay_ai_comment = score, comment
    if overwrite or attempt.essay_score is None:
        attempt.essay_score = score
    db.commit()
    return score, comment


@event.listens_for(Session, "after_commit")
def _submit_essay_ai(session: Session) -> None:
    """Topshirilgan esselar bazaga yozilgach — fonda AI navbatiga (testda "AI orqali tekshirish" yoqilgan bo'lsa)."""

    from .ai_reader import ai_enabled

    attempt_ids = session.info.pop("essay_ai", None)
    if attempt_ids and ai_enabled():
        for attempt_id in sorted(attempt_ids):
            _ai_pool.submit(_ai_grade_essay, attempt_id)


@event.listens_for(Session, "after_rollback")
def _forget_essay_ai(session: Session) -> None:
    session.info.pop("essay_ai", None)


def _ai_grade_essay(attempt_id: int) -> None:
    """Fonda: bitta o'quvchining essesini AI baholaydi (xato bo'lsa — o'qituvchi tugma bilan qayta so'raydi)."""

    from .ai_reader import AIReadError

    logger = logging.getLogger("backend.ai")
    try:
        with SessionLocal() as db:
            attempt = db.get(Attempt, attempt_id)
            if not attempt or attempt.status != "done" or attempt.essay_ai_score is not None:
                return
            test = db.get(Test, attempt.test_id)
            if not test or not test.essay_max or not test.essay_ai or not (attempt.essay_text or "").strip():
                return
            try:
                _ai_grade_essay_now(db, test, attempt, overwrite=False)
            except AIReadError as error:
                logger.warning("AI esseni baholay olmadi (o'quvchi %s): %s", attempt_id, error)
    except Exception:  # noqa: BLE001 — fondagi ish: xato bo'lsa esseni o'qituvchi baholaydi
        logger.exception("AI esse baholashda kutilmagan xato (o'quvchi %s)", attempt_id)


@app.post("/api/teacher/tests/{test_id}/students/{attempt_id}/essay/ai")
def essay_ai_grade(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """
    Esseni AI bilan qayta baholash (AI baholaydigan testda): masalan fondagi baholash xato bergan bo'lsa.
    AI bahosi ballga yoziladi — o'qituvchi uni keyin o'zgartira oladi (PUT .../essay).
    """

    from .ai_reader import AIReadError, ai_enabled

    test = _get_own_test(db, teacher, test_id)
    if not test.essay_max:
        raise HTTPException(status_code=400, detail="Bu testda esse yo'q")
    if not test.essay_ai:
        raise HTTPException(status_code=400, detail="Bu testda esseni o'qituvchi baholaydi")
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    text_value = (attempt.essay_text or "").strip()
    if not text_value:
        raise HTTPException(status_code=400, detail="Esse ilovada yozilmagan — AI faqat ilovada yozilgan esseni baholaydi")
    if not ai_enabled():
        raise HTTPException(status_code=400, detail="AI ulanmagan — administrator API kalitini sozlashi kerak")

    try:
        score, comment = _ai_grade_essay_now(db, test, attempt, overwrite=True)
    except AIReadError as error:
        raise HTTPException(status_code=502, detail=f"AI baholay olmadi: {error}") from error
    return {"ok": True, "score": score, "comment": comment}


@app.put("/api/teacher/tests/{test_id}/students/{attempt_id}/essay")
def set_essay_score(
    test_id: int, attempt_id: int, body: EssayScoreIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """O'qituvchi esseni baholaydi (0–24). Ball Rasch balliga darhol qo'shiladi."""

    test = _get_own_test(db, teacher, test_id)
    if not test.essay_max:
        raise HTTPException(status_code=400, detail="Bu testda esse yo'q")
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    if body.score is not None and body.score > test.essay_max:
        raise HTTPException(status_code=422, detail=f"Esse bali 0–{test.essay_max} oralig'ida bo'ladi")
    attempt.essay_score = body.score
    db.commit()
    return {"ok": True, "score": attempt.essay_score}


@app.get("/api/teacher/tests/{test_id}/students/{attempt_id}/answers")
def get_student_answers(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'quvchining oldin kiritilgan javoblari (tahrirlash uchun): {savol_ref: "A"}."""

    test = _get_own_test(db, teacher, test_id)
    attempt = _get_offline_attempt(db, test, attempt_id)
    answers = db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()

    return {
        "name": attempt.offline_name,
        "variant": attempt.variant,
        "has_answers": attempt.status == "done",
        "answers": {str(answer.question_ref): answer.chosen or "" for answer in answers},
    }


@app.post("/api/teacher/tests/{test_id}/students/{attempt_id}/answers")
def save_student_answers(
    test_id: int,
    attempt_id: int,
    body: StudentAnswersIn,
    teacher: Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    O'qituvchi o'quvchining javoblarini kiritadi — bot ularni avtomatik tekshiradi.

    Bepul testda har bir YANGI natija uchun o'qituvchidan 1 token yechiladi
    (javoblarni keyin tahrirlash qo'shimcha token talab qilmaydi).
    """

    test = _get_own_test(db, teacher, test_id)
    attempt = _get_offline_attempt(db, test, attempt_id)
    _check_variant(db, test, body.variant)
    if body.variant:
        attempt.variant = body.variant
        db.flush()
    return _store_offline_answers(db, test, teacher, attempt, body.answers)


def _same_saved_answers(db: Session, attempt: Attempt, answers: dict[str, str]) -> bool:
    """Shu javoblar bu o'quvchiga allaqachon saqlanganmi (qayta yuborilgan so'rovni tanish uchun)."""

    def normal(value: str | None) -> str:
        return ",".join(sorted(part for part in (value or "").upper().replace(" ", "").split(",") if part))

    stored = {
        str(ref): normal(chosen)
        for ref, chosen in db.execute(
            select(StudentAnswer.question_ref, StudentAnswer.chosen).where(StudentAnswer.attempt_id == attempt.id)
        ).all()
        if normal(chosen)
    }
    return stored == {str(ref): normal(value) for ref, value in answers.items() if normal(value)}


def _saved_result(test: Test, attempt: Attempt) -> dict:
    """Saqlangan natija — _store_offline_answers javobi bilan bir xil ko'rinishda."""
    return {"name": attempt.offline_name, "correct": attempt.correct, "wrong": attempt.wrong,
            "total": _question_count(test), "percent": attempt.percent}


def _store_offline_answers(db: Session, test: Test, teacher: Teacher, attempt: Attempt, answers: dict[str, str]) -> dict:
    """O'qituvchi kiritgan (yoki skanerlangan) javoblarni tekshirib saqlaydi."""

    db.refresh(attempt, with_for_update=True)  # ikki marta yuborilsa ham token bir marta yechilsin

    # Har bir YANGI o'quvchi natijasi o'qituvchidan 1 token oladi (bepul testlarda).
    # Javoblarni keyin tahrirlash qo'shimcha token talab qilmaydi.
    was_filled = attempt.status == "done"
    if _charges_tokens(test, teacher) and not was_filled and teacher.credits <= 0:
        raise HTTPException(status_code=402, detail="Tokeningiz tugagan — token yoki oylik obuna sotib oling")

    # Qayta kiritilsa, eski javoblar o'rniga yangilari yoziladi.
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))

    total_questions = _grade_answers(db, attempt, test, answers)
    _calculate_attempt_score(db, attempt, test)
    attempt.status = "done"
    attempt.finished_at = utcnow()

    if _charges_tokens(test, teacher) and not was_filled:
        add_credits(teacher, -1)

    db.commit()
    try:
        _sync_class_result(db, test, teacher, attempt)
    except Exception:  # natija imtihonda saqlangan — sinfga yozilmasa ham yo'qolmaydi
        db.rollback()
        logging.getLogger("backend").warning("Sinf natijasiga yozib bo'lmadi", exc_info=True)

    return {
        "name": attempt.offline_name,
        "correct": attempt.correct,
        "wrong": attempt.wrong,
        "total": total_questions,
        "percent": attempt.percent,
    }


# ---------- Offline imtihon ↔ maktab sinflari ----------
# Imtihonga maktab sinflari biriktiriladi: sinf o'quvchilari o'z 4 xonali ID'si bilan
# imtihon ro'yxatiga tushadi (ismli varaqlar ham shu ID bilan). Skanerlangan natija
# o'quvchining sinfidagi "Natijalar" bo'limiga yoziladi (har sinf uchun bitta test).


def _student_attempt(db: Session, test: Test, student: ClassStudent) -> Attempt:
    """Maktab o'quvchisi uchun imtihondagi yozuv (bo'lmasa — yaratiladi, ID'si bilan)."""

    attempt = db.scalar(
        select(Attempt).where(
            Attempt.test_id == test.id, Attempt.class_student_id == student.id, Attempt.status.in_(("pending", "done"))
        )
    )
    if attempt:
        return attempt
    # O'chirilgan (sinfdan olib tashlangan) yozuv raqamni band qilmaydi — qayta biriktirilsa ID o'sha qoladi
    number_taken = student.student_code and db.scalar(
        select(Attempt.id).where(Attempt.test_id == test.id, Attempt.sheet_number == student.student_code,
                                 Attempt.status.in_(("pending", "done", "in_progress")))
    )
    attempt = Attempt(
        test_id=test.id, offline_name=student.full_name[:200], status="pending", class_student_id=student.id,
        sheet_number=None if number_taken else student.student_code,
    )
    db.add(attempt)
    db.flush()
    return attempt


def _sync_class_result(db: Session, test: Test, teacher: Teacher, attempt: Attempt) -> None:
    """Skanerlangan (yoki kiritilgan) natija o'quvchining sinf natijalariga yoziladi."""

    if not attempt.class_student_id or attempt.status != "done":
        return
    student = db.get(ClassStudent, attempt.class_student_id)
    school_class = db.get(SchoolClass, student.class_id) if student else None
    if not school_class or school_class.deleted_at is not None:
        return
    class_test = db.scalar(
        select(ClassTest).where(
            ClassTest.source_test_id == test.id, ClassTest.class_id == school_class.id, ClassTest.deleted_at.is_(None)
        )
    )
    if not class_test:
        class_test = ClassTest(
            class_id=school_class.id, teacher_user_id=teacher.user_id, title=test.title[:200],
            subject=test.subject or "", taken_on=(utcnow() + TASHKENT_OFFSET).date(),
            max_score=float(_question_count(test)), source_test_id=test.id,
        )
        db.add(class_test)
        db.flush()
        _save_scores(db, class_test, school_class, {})  # hali skanerlanmaganlar — "kelmadi"
        db.flush()
        db.refresh(class_test)
    score = next((item for item in class_test.scores if item.student_id == student.id), None)
    if score is None:
        score = ClassTestScore(test_id=class_test.id, student_id=student.id, student_name=student.full_name)
        db.add(score)
    score.score = float(attempt.correct)
    class_test.updated_at = utcnow()
    db.commit()


@app.get("/api/teacher/tests/{test_id}/classes")
def test_school_classes(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    test = _paper_test(db, teacher, test_id)
    members = _all_memberships(db, teacher.user)
    if not members:
        return {"available": False, "classes": []}
    attached = set(
        db.scalars(
            select(ClassStudent.class_id)
            .join(Attempt, Attempt.class_student_id == ClassStudent.id)
            .where(Attempt.test_id == test.id, Attempt.status.in_(("pending", "done")))
        ).all()
    )
    # Maktab, o'quv markazi va to'garakdagi guruhlar birga; bir nechta muassasa bo'lsa — nomi bilan
    schools = {member.school_id: member.school for member in members}
    classes = sorted(
        db.scalars(select(SchoolClass).where(SchoolClass.school_id.in_(list(schools)), SchoolClass.deleted_at.is_(None))).all(),
        key=lambda item: (item.school_id, _class_name_key(item.name)),
    )
    return {
        "available": True,
        "classes": [
            {"id": item.id, "name": item.name, "students": len(_active_students(item)), "attached": item.id in attached,
             "school": schools[item.school_id].name if len(schools) > 1 else None}
            for item in classes
        ],
    }


@app.post("/api/teacher/tests/{test_id}/classes")
def attach_school_classes(
    test_id: int, body: TestClassesIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    test = _paper_test(db, teacher, test_id)
    school_ids = {member.school_id for member in _all_memberships(db, teacher.user)}
    if not school_ids:
        raise HTTPException(status_code=403, detail="Siz hech qaysi ta'lim muassasasiga a'zo emassiz")
    added = 0
    for class_id in body.class_ids:
        school_class = db.get(SchoolClass, class_id)
        if not school_class or school_class.school_id not in school_ids or school_class.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Sinf topilmadi")
        _assign_student_codes(db, school_class.school_id)
        for student in _active_students(school_class):
            before = db.scalar(select(func.count()).select_from(Attempt).where(Attempt.test_id == test.id))
            _student_attempt(db, test, student)
            added += db.scalar(select(func.count()).select_from(Attempt).where(Attempt.test_id == test.id)) - before
    db.commit()
    _ensure_sheet_numbers(db, test)
    return {"added": added}


# ---------- Variantlar (A/B/C/D) ----------
# Ko'chirishga qarshi: bitta testning bir nechta varianti — savollar soni va turi
# bir xil, faqat to'g'ri javoblar boshqacha (savollar aralashtirilgan). A — asosiy
# kalit (KeyQuestion), B/C/D — TestVariant'da. Har o'quvchi o'z varianti kaliti
# bilan tekshiriladi (Attempt.variant; bo'sh — A).

VARIANT_LETTERS = ("A", "B", "C", "D")


def _test_variants(db: Session, test: Test) -> dict[str, dict[int, str]]:
    """Qo'shimcha variantlar kalitlari: {"B": {savol_raqami: to'g'ri_javob}}."""

    result = {}
    for variant in db.scalars(select(TestVariant).where(TestVariant.test_id == test.id).order_by(TestVariant.name)).all():
        result[variant.name] = {int(number): correct for number, correct in json.loads(variant.answers or "{}").items()}
    return result


def _variant_names(db: Session, test: Test) -> list[str]:
    """Variantlar ro'yxati (["A", "B", ...]); qo'shimcha variant bo'lmasa — bo'sh."""

    extra = sorted(_test_variants(db, test))
    return ["A", *extra] if extra else []


def _key_override(db: Session, test: Test, variant: str | None) -> dict[int, str]:
    if not variant or variant == "A" or test.mode != "key":
        return {}
    return _test_variants(db, test).get(variant, {})


def _check_variant(db: Session, test: Test, variant: str | None) -> None:
    if variant and variant != "A" and variant not in _test_variants(db, test):
        raise HTTPException(status_code=422, detail=f"Bu testda {variant} variant yo'q")


def _regrade_variant(db: Session, test: Test, variant: str) -> int:
    """Variant kaliti o'zgardi — shu variantdagi natijalar saqlangan javoblardan qayta tekshiriladi."""

    # A — asosiy kalit: varianti yozilmagan (bo'sh) natijalar ham shu kalit bilan tekshirilgan
    same_variant = or_(Attempt.variant.is_(None), Attempt.variant == "A") if variant == "A" else Attempt.variant == variant
    attempts = db.scalars(
        select(Attempt).where(Attempt.test_id == test.id, Attempt.status == "done", same_variant)
    ).all()
    for attempt in attempts:
        answers = {
            str(answer.question_ref): answer.chosen or ""
            for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
        }
        db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))
        _grade_answers(db, attempt, test, answers)
        _calculate_attempt_score(db, attempt, test)
    return len(attempts)


@app.get("/api/teacher/tests/{test_id}/variants")
def list_variants(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    test = _get_own_test(db, teacher, test_id)
    if test.mode != "key":
        return {"available": False, "variants": []}
    used = dict(
        db.execute(
            select(Attempt.variant, func.count())
            .where(Attempt.test_id == test.id, Attempt.status.in_(("pending", "done")), Attempt.variant.is_not(None))
            .group_by(Attempt.variant)
        ).all()
    )
    variants = [{"name": "A", "main": True, "students": used.get("A", 0)}]
    variants += [
        {"name": name, "main": False, "answers": {str(number): correct for number, correct in key.items()},
         "students": used.get(name, 0)}
        for name, key in _test_variants(db, test).items()
    ]
    return {"available": True, "variants": variants}


@app.put("/api/teacher/tests/{test_id}/variants/{name}")
def save_variant(
    test_id: int, name: str, body: VariantKeyIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    test = _get_own_test(db, teacher, test_id)
    if test.mode != "key":
        raise HTTPException(status_code=400, detail="Variantlar faqat javoblar kaliti bilan yaratilgan testlarda")
    if name not in VARIANT_LETTERS[1:]:
        raise HTTPException(status_code=422, detail="Variant nomi B, C yoki D bo'ladi")

    key: dict[str, str] = {}
    for kq in test.key_questions:
        value = (body.answers.get(str(kq.number)) or "").strip()
        label = _key_question_label(kq)
        if not value:
            raise HTTPException(status_code=422, detail=f"{label}: to'g'ri javobni belgilang")
        if kq.question_type == "text":
            key[str(kq.number)] = value[:300]
            continue
        letters = sorted({part.strip().upper() for part in value.split(",") if part.strip()})
        allowed = {chr(65 + index) for index in range(kq.option_count)}
        if not letters or not set(letters) <= allowed or (kq.question_type == "single" and len(letters) != 1):
            raise HTTPException(status_code=422, detail=f"{label}: javob noto'g'ri")
        key[str(kq.number)] = ",".join(letters)

    variant = db.scalar(select(TestVariant).where(TestVariant.test_id == test.id, TestVariant.name == name))
    if not variant:
        variant = TestVariant(test_id=test.id, name=name)
        db.add(variant)
    variant.answers = json.dumps(key, ensure_ascii=False)
    variant.updated_at = utcnow()
    db.flush()
    regraded = _regrade_variant(db, test, name)
    db.commit()
    return {"ok": True, "regraded": regraded}


@app.put("/api/teacher/tests/{test_id}/key")
def save_main_key(
    test_id: int, body: VariantKeyIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """Asosiy kalitni (A) tahrirlash: savollar soni va turi o'zgarmaydi, faqat to'g'ri javoblar.
    Tayyor natijalar yangi kalit bilan qayta tekshiriladi (o'quvchi javoblari saqlanib qoladi)."""

    test = _get_own_test(db, teacher, test_id)
    if test.mode != "key":
        raise HTTPException(status_code=400, detail="Kalitni faqat javoblar kaliti bilan yaratilgan testda tahrirlash mumkin")

    key: dict[int, str] = {}
    for kq in test.key_questions:
        if str(kq.number) not in body.answers:
            key[kq.number] = kq.correct  # yuborilmagan savol kaliti o'zgarmaydi (faqat bo'sh qiymat tozalaydi)
            continue
        value = (body.answers.get(str(kq.number)) or "").strip()
        label = _key_question_label(kq)
        if kq.question_type == "text":
            # Bir nechta to'g'ri javob "|" bilan: "0,5|1/2". Bo'sh — kalit hali kiritilmagan (keyin kiritiladi)
            value = "|".join(part.strip() for part in value.split("|") if part.strip())
            key[kq.number] = value[:300]
            continue
        letters = sorted({part.strip().upper() for part in value.split(",") if part.strip()})
        allowed = {chr(65 + index) for index in range(kq.option_count)}
        if not letters:
            key[kq.number] = ""  # kalit hali kiritilmagan
            continue
        if not set(letters) <= allowed or (kq.question_type == "single" and len(letters) != 1):
            raise HTTPException(status_code=422, detail=f"{label}: javob noto'g'ri")
        key[kq.number] = ",".join(letters)

    changed = 0
    for kq in test.key_questions:
        if kq.correct != key[kq.number]:
            kq.correct = key[kq.number]
            changed += 1
    if not changed:
        return {"ok": True, "changed": 0, "regraded": 0}
    db.flush()
    regraded = _regrade_variant(db, test, "A")
    db.commit()
    return {"ok": True, "changed": changed, "regraded": regraded}


@app.delete("/api/teacher/tests/{test_id}/variants/{name}")
def delete_variant(test_id: int, name: str, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    test = _get_own_test(db, teacher, test_id)
    variant = db.scalar(select(TestVariant).where(TestVariant.test_id == test.id, TestVariant.name == name))
    if not variant:
        raise HTTPException(status_code=404, detail="Variant topilmadi")
    in_use = db.scalar(
        select(func.count()).select_from(Attempt).where(
            Attempt.test_id == test.id, Attempt.variant == name, Attempt.status.in_(("pending", "done"))
        )
    )
    if in_use:
        raise HTTPException(status_code=409, detail=f"{name} variantda {in_use} ta o'quvchi bor — o'chirib bo'lmaydi")
    db.delete(variant)
    db.commit()
    return {"ok": True}


# ---------- Javoblar varaqasini skanerlash ----------
# O'qituvchi o'quvchi bo'yagan varaqni suratga oladi → skaner javoblarni o'qiydi
# (backend/omr.py) → o'qituvchi ekranda tekshirib, ismni yozib saqlaydi.
# Saqlash oddiy "javoblarni kiritish" bilan bir xil: token, baholash, natijalar.


def _paper_test(db: Session, teacher: Teacher, test_id: int) -> Test:
    # Titul faqat oflayn (yopiq) testda — onlayn testda o'quvchi javobini ilovada belgilaydi
    test = _get_own_test(db, teacher, test_id)
    if test.enrollment_type != "closed":
        raise HTTPException(status_code=400, detail="Varaq skanerlash faqat offline testlarda")
    if test.status == "closed":
        raise HTTPException(status_code=400, detail="Test yakunlangan — avval qayta oching")
    return test


@app.post("/api/teacher/tests/{test_id}/scan")
def scan_sheet(
    test_id: int, body: SheetScanIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    from .omr import A4_PAGE, PreparedSheet, ScanError, SheetPage, scan_answer_sheet  # og'ir kutubxona (OpenCV) — faqat kerak bo'lganda

    test = _paper_test(db, teacher, test_id)
    variants, bubbles, page_count = _sheet_layout(test, db, body.page)

    try:
        image = base64.b64decode(body.image_base64.split(",")[-1], validate=True)
    except ValueError:
        raise HTTPException(status_code=422, detail="Rasm buzilgan — qaytadan suratga oling")
    try:
        geometry = _sheet_geometry(test, variants)
        shape = SheetPage(**geometry) if geometry else A4_PAGE  # ixcham titul — o'z o'lchami bilan
        prepared = PreparedSheet(image, bubbles, page=body.page, sheet=shape)
        result = scan_answer_sheet(image, bubbles, page=body.page, prepared=prepared, sheet=shape)
        if test.sheet_version == 5 and body.page == 1 and result["sheet_number_status"] != "ok":
            # Oldin chop etilgan 3 xonali ID'li titul bo'lishi mumkin — shu ko'rinishda qayta o'qiladi
            old_bubbles = _old_id_bubbles(test, variants)
            # Burchak va chet belgilari ikkala titulda bir xil — tekislangan varaq qayta ishlatiladi
            old_result = scan_answer_sheet(image, old_bubbles, page=1, prepared=prepared)
            if old_result["sheet_number_status"] == "ok":
                result = old_result
    except ScanError as error:
        raise HTTPException(status_code=422, detail=str(error))
    if result["sheet_code"] is None and any(b["kind"] == "code" and b["page"] == body.page for b in bubbles):
        # Imtihon kodi kataklari bor, lekin o'qilmadi — varaq to'liq yoki to'g'ri ko'rinmagan (masalan bir qismi
        # kadrdan chiqib ketgan). Bunday suratdan javob o'qilsa, ular xato bo'lishi mumkin — qayta suratga olinadi
        raise HTTPException(
            status_code=422,
            detail="Varaq to'liq ko'rinmadi — titulning 4 ta burchagi kadrda bo'lsin, tekis va yorug' joyda qayta suratga oling",
        )
    return _scan_response(db, teacher, test, result, body.page, variants, page_count)


def _sheet_layout(test: Test, db: Session, page: int = 1) -> tuple[list[str], list[dict], int]:
    """Titulning doirachalari (varaqdagi joyi bilan), variantlar va sahifalar soni."""

    bubbles: list[dict] = []
    variants = _variant_names(db, test)
    generate_answer_sheet_pdf(test, bubbles=bubbles, variants=variants or None)
    if not bubbles:
        raise HTTPException(status_code=400, detail="Bu testda belgilanadigan (variantli) savol yo'q")
    page_count = max(bubble["page"] for bubble in bubbles)
    if page > page_count:
        raise HTTPException(status_code=422, detail=f"Varaq {page_count} sahifali")
    return variants, bubbles, page_count


def _old_id_bubbles(test: Test, variants: list[str]) -> list[dict]:
    """Oldin chop etilgan 3 xonali ID'li (4-versiya) titulning doirachalari — 5-versiya test uchun."""

    old_bubbles: list[dict] = []
    generate_answer_sheet_pdf(SimpleNamespace(
        mode=test.mode, key_questions=test.key_questions, questions=test.questions, title=test.title,
        code=test.code, subject=test.subject, id=test.id, sheet_version=4,
    ), bubbles=old_bubbles, variants=variants or None)
    return old_bubbles


def _school_students_query(members):
    """O'qituvchi muassasalaridagi (faol sinflardagi) o'quvchilar — 4 xonali ID'si bilan."""
    members = members if isinstance(members, list) else [members]
    return (
        select(ClassStudent)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id.in_([member.school_id for member in members]), SchoolClass.deleted_at.is_(None),
               ClassStudent.removed_at.is_(None), ClassStudent.student_code.is_not(None),
               ClassStudent.source_student_id.is_(None))
    )


def _attempt_by_sheet_number(db: Session, teacher: Teacher, test: Test, number: int) -> Attempt | None:
    """Varaqdagi ID bo'yicha o'quvchi: imtihon ro'yxatidan, bo'lmasa maktab o'quvchisi (ro'yxatga qo'shiladi)."""

    found = db.scalar(
        select(Attempt).where(
            Attempt.test_id == test.id,
            Attempt.user_id.is_(None),
            Attempt.status.in_(("pending", "done")),
            Attempt.sheet_number == number,
        )
    )
    if found:
        return found
    members = _all_memberships(db, teacher.user)
    student = members and db.scalars(_school_students_query(members).where(ClassStudent.student_code == number)).first()
    if not student:
        return None
    found = _student_attempt(db, test, student)  # maktab o'quvchisi — imtihon ro'yxatiga qo'shiladi
    db.commit()
    return found


def _scan_response(db: Session, teacher: Teacher, test: Test, result: dict, page: int,
                   variants: list[str], page_count: int) -> dict:
    """O'qilgan varaq (serverda yoki telefonda) → imtihon kodi tekshiruvi, ID bo'yicha o'quvchi, ro'yxat."""

    # Varaqdagi raqam bo'yicha o'quvchi topiladi (ro'yxatda bo'lsa)
    _ensure_sheet_numbers(db, test)
    match = None
    found = _attempt_by_sheet_number(db, teacher, test, result["sheet_number"]) if result.get("sheet_number") else None
    if found:
        match = {"id": found.id, "name": found.offline_name, "status": found.status, "variant": found.variant}

    pending = [
        {"id": attempt.id, "name": attempt.offline_name}
        for attempt in db.scalars(
            select(Attempt)
            .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status == "pending")
            .order_by(Attempt.offline_name)
        ).all()
    ]
    return {**result, "match": match, "variants": variants, "page": page, "page_count": page_count,
            "pending_students": pending}


@app.get("/api/teacher/tests/{test_id}/scan/layout")
def scan_layout(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Ilovadagi skaner varaqni telefonning o'zida o'qiydi (surat serverga yuborilmaydi — tez).
    Buning uchun titul tuzilishi: har doiracha, chet belgisi va kod katagining varaqdagi joyi.
    """

    test = _paper_test(db, teacher, test_id)
    variants, bubbles, page_count = _sheet_layout(test, db)
    _ensure_sheet_numbers(db, test)
    attempts = db.scalars(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done")))
        .order_by(Attempt.offline_name)
    ).all()
    members = _all_memberships(db, teacher.user)
    school = db.scalars(_school_students_query(members)).all() if members else []
    return {
        "bubbles": bubbles,
        "sheet_version": test.sheet_version or 1,
        "variants": variants,
        # Telefon varaqni internetsiz tekshiradi: ID → o'quvchi shu ro'yxatlardan topiladi
        "students": [
            {"id": attempt.id, "name": attempt.offline_name, "number": attempt.sheet_number,
             "status": attempt.status, "variant": attempt.variant}
            for attempt in attempts
        ],
        "school_students": [{"code": student.student_code, "name": student.full_name} for student in school],
        # 5-versiya testda oldin chop etilgan 3 xonali ID'li titul ham o'qilsin
        "old_bubbles": _old_id_bubbles(test, variants) if test.sheet_version == 5 else None,
        # Ixcham titul (6-versiya): o'lchami va chet belgilari soni; A4 titulda — None
        "sheet_page": _sheet_geometry(test, variants),
        "page_count": page_count,
        "sheet_code": sheet_code(test),
        # Natija ekranida doirachalarni ranglash uchun (yashil — to'g'ri, qizil — xato, sariq — to'g'ri javob)
        "keys": {name: _sheet_key(db, test, name) for name in (variants or ["A"])},
    }


def _sheet_key(db: Session, test: Test, variant: str) -> dict[str, str]:
    """Varaqdagi belgilanadigan savollarning to'g'ri javobi: {savol_kaliti: "B"} (ko'p javobli — "A,C")."""

    key = {}
    for _, kind, _, correct, ref in _answer_sheet_items(test, _key_override(db, test, variant)):
        if kind == "text" or not correct:
            continue
        letters = sorted({part.strip().upper() for part in correct.split(",") if part.strip()})
        key[str(ref)] = ",".join(letters) if kind == "multi" else correct.strip().upper()
    return key


@app.post("/api/teacher/tests/{test_id}/scan/result")
def scan_result(
    test_id: int, body: SheetResultIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """Telefonda o'qilgan varaq natijasi — o'quvchi ID bo'yicha topiladi (javob /scan bilan bir xil)."""

    test = _paper_test(db, teacher, test_id)
    variants, _, page_count = _sheet_layout(test, db, body.page)
    return _scan_response(db, teacher, test, body.model_dump(exclude={"page"}), body.page, variants, page_count)


@app.post("/api/teacher/tests/{test_id}/scan/finish")
def finish_scanning(
    test_id: int,
    teacher: Teacher = Depends(get_current_teacher),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Varaqlarni skanerlash tugadi: natijalar ro'yxati (reyting — ism, ID, ball) va PDF hisobot
    bot orqali o'qituvchining Telegram chatiga yuboriladi.
    """

    test = _get_own_test(db, teacher, test_id)
    if test.enrollment_type != "closed":
        raise HTTPException(status_code=400, detail="Varaq skanerlash faqat offline testlarda")
    attempts = db.scalars(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done")))
        .order_by(Attempt.offline_name)
    ).all()
    done = sorted((attempt for attempt in attempts if attempt.status == "done"),
                  key=lambda attempt: (-attempt.percent, attempt.offline_name or ""))
    total = _question_count(test)
    lines = [
        f"📋 {test.title} (kod {test.code}) — natijalar",
        f"✅ Tekshirildi: {len(done)} / {len(attempts)} ta o'quvchi",
        "",
    ]
    for place, attempt in enumerate(done, start=1):
        name = attempt.offline_name or "Noma'lum"
        number = f" · ID {attempt.sheet_number:04d}" if attempt.sheet_number else ""
        lines.append(f"{place}. {name}{number} — {attempt.correct}/{total} · {round(attempt.percent, 1)}%")
    waiting = [attempt.offline_name or "Noma'lum" for attempt in attempts if attempt.status == "pending"]
    if waiting:
        lines += ["", f"⏳ Hali tekshirilmagan ({len(waiting)}): " + ", ".join(waiting[:60])
                  + (" …" if len(waiting) > 60 else "")]

    # Telegram xabari 4096 belgigacha — qatorlar bo'yicha bo'laklanadi
    chunks, current = [], ""
    for line in lines:
        if current and len(current) + len(line) + 1 > 3900:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}" if current else line
    chunks.append(current)
    delivered = all([_telegram_send_message(user.tg_id, chunk) for chunk in chunks])
    if done:
        _send_pdf_to_telegram(user.tg_id, f"{test.code}.pdf", generate_test_report_pdf(db, test, teacher))
    return {"ok": delivered, "checked": len(done), "total": len(attempts)}


@app.post("/api/teacher/tests/{test_id}/scan/save")
def save_scanned_sheet(
    test_id: int, body: SheetScanSaveIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    test = _paper_test(db, teacher, test_id)
    _check_variant(db, test, body.variant)
    # Varaqdagi ID bo'yicha o'quvchi (telefondagi ro'yxat eskirgan bo'lsa ham — masalan o'quvchi keyin qo'shilgan):
    # ism yozilgan bo'lsa ham, shu ID li o'quvchi bor ekan, natija unga yoziladi — ikkinchi nusxa yaratilmaydi
    by_number = None
    if not body.attempt_id and body.sheet_number:
        _ensure_sheet_numbers(db, test)
        by_number = _attempt_by_sheet_number(db, teacher, test, body.sheet_number)
    if body.attempt_id:
        attempt = _get_offline_attempt(db, test, body.attempt_id)
    elif by_number:
        attempt = by_number
        if body.name.strip() and attempt.status == "done":
            # Telefonda "ro'yxatda yo'q" deb ism yozilgan, lekin bu ID ning natijasi allaqachon bor
            if _same_saved_answers(db, attempt, body.answers):
                return _saved_result(test, attempt)
            raise HTTPException(
                status_code=409,
                detail=f"ID {body.sheet_number} — «{attempt.offline_name}» natijasi allaqachon bor; "
                       "ro'yxatdan tahrirlang yoki «Boshqa o'quvchi» ni tanlang",
            )
    elif body.sheet_number and not body.name.strip():
        raise HTTPException(status_code=422, detail=f"ID {body.sheet_number} ro'yxatda yo'q — ismini yozing")
    else:
        name = " ".join(body.name.split())
        if len(name) < 2:
            raise HTTPException(status_code=422, detail="O'quvchining ism-familiyasini yozing")
        same_name = db.scalars(
            select(Attempt).where(
                Attempt.test_id == test.id,
                Attempt.user_id.is_(None),
                Attempt.status.in_(("pending", "done")),
                func.lower(Attempt.offline_name) == name.lower(),
            )
        ).first()
        if same_name and same_name.status == "done":
            if _same_saved_answers(db, same_name, body.answers):
                # Navbatdan qayta yuborilgan (birinchi so'rov saqlangan, javobi kelmay qolgan) — xato emas
                return _saved_result(test, same_name)
            raise HTTPException(
                status_code=409,
                detail=f"«{name}» natijasi allaqachon kiritilgan — boshqa ism yozing yoki ro'yxatdan tahrirlang",
            )
        attempt = same_name or Attempt(test_id=test.id, offline_name=name[:200], status="pending")
        db.add(attempt)
        db.flush()
        if not attempt.sheet_number:
            number_taken = body.sheet_number and db.scalar(
                select(Attempt.id).where(Attempt.test_id == test.id, Attempt.sheet_number == body.sheet_number)
            )
            if body.sheet_number and not number_taken:
                attempt.sheet_number = body.sheet_number
                db.flush()  # saqlashda yozuv bazadan qayta o'qiladi — raqam yo'qolmasin
    if body.variant:
        attempt.variant = body.variant
        db.flush()
    _store_written_images(db, test, attempt, body.written)
    _store_page_images(db, test, attempt, body.page_images)
    if body.certificates is not None:
        _store_sheet_certificates(attempt, body.certificates)
        db.flush()  # saqlashda yozuv bazadan qayta o'qiladi — tanlov yo'qolmasin
    result = _store_offline_answers(db, test, teacher, attempt, body.answers)
    if body.written:
        _queue_ai_reading(db, [attempt.id])  # AI yoqilgan bo'lsa — kataklarni fonda o'qiydi
    return result


# ---------- Yozma javoblar (titulda qo'lda yozilgan) ----------
# Skaner yozma katakni o'qimaydi — rasm qilib saqlaydi. O'qituvchi savolma-savol (36a: hamma
# o'quvchilar) rasmga qarab ✓ yoki ✗ qo'yadi; baho natija va Rasch'ga o'tadi. Bo'sh katak o'zi ✗.
# AI ulangan bo'lsa (ANTHROPIC_API_KEY), kataklarni Claude o'qiydi: aniq o'qilgan va kalitga mos
# javob — o'zi ✓; qolganlari (shubhali, mos emas) o'qituvchiga AI o'qigan matn bilan ko'rsatiladi.

WRITTEN_IMAGE_LIMIT = 300_000  # bitta katak rasmi (base64 matni) — ko'pi bilan
_ai_pool = ThreadPoolExecutor(max_workers=2, thread_name_prefix="ai-read")
_ai_running: set[int] = set()  # hozir AI o'qiyotgan o'quvchilar (bir varaq ikki marta yuborilmasin)
_ai_lock = threading.Lock()


def _apply_ai_verdict(row: WrittenAnswer, key: str) -> bool:
    """
    AI aniq o'qigan javobga baho: kalitga mos — ✓, mos emas (yoki AI bo'sh deb o'qidi) — ✗.
    AI "aniq emas" degan bo'lsa yoki o'qituvchi allaqachon baholagan bo'lsa — tegmaydi.
    """

    if row.verdict is not None or row.ai_status != "done" or not row.ai_sure or not (key or "").strip():
        return False  # kalit hali kiritilmagan bo'lsa ham — kalit kiritilgach baholanadi
    row.verdict = bool(row.ai_text) and _text_answer_is_correct(row.ai_text, key)
    row.auto_verdict, row.checked_at = True, utcnow()
    return True


def _apply_saved_ai_verdicts(db: Session, attempt_ids: list[int]) -> int:
    """
    AI oldin o'qigan, lekin baholanmay qolgan javoblar (oldingi qoida: faqat ✓ qo'yilardi) — qayta
    so'rovsiz, saqlangan o'qishi bo'yicha baholanadi. Baholanganlar soni.
    """

    rows = db.scalars(
        select(WrittenAnswer).where(
            WrittenAnswer.attempt_id.in_(attempt_ids), WrittenAnswer.verdict.is_(None),
            WrittenAnswer.ai_status == "done", WrittenAnswer.ai_sure.is_(True),
        ).options(defer(WrittenAnswer.image))  # rasm kerak emas — xotiraga yuklanmaydi
    ).all()
    touched: set[int] = set()
    for row in rows:
        attempt = db.get(Attempt, row.attempt_id)
        test = db.get(Test, attempt.test_id)
        kq = next((item for item in test.key_questions if item.number == row.question_ref), None)
        key = _key_override(db, test, attempt.variant).get(row.question_ref, kq.correct if kq else "")
        if _apply_ai_verdict(row, key):
            touched.add(row.attempt_id)
    db.commit()
    for attempt_id in touched:
        attempt = db.get(Attempt, attempt_id)
        test = db.get(Test, attempt.test_id)
        _regrade_attempt(db, test, test.teacher, attempt)
    return len(rows)


def _queue_ai_reading(db: Session, attempt_ids: list[int]) -> int:
    """O'quvchilarning hali baholanmagan, bo'sh bo'lmagan kataklari AI navbatiga. Navbatga tushganlar soni."""

    from .ai_reader import ai_enabled

    if not ai_enabled() or not attempt_ids:
        return 0
    rows = db.scalars(
        select(WrittenAnswer).where(
            WrittenAnswer.attempt_id.in_(attempt_ids), WrittenAnswer.blank.is_(False),
            WrittenAnswer.verdict.is_(None),
            or_(WrittenAnswer.ai_status.is_(None), WrittenAnswer.ai_status.in_(("queued", "error"))),
        ).options(defer(WrittenAnswer.image))  # rasm kerak emas — xotiraga yuklanmaydi
    ).all()
    for row in rows:
        row.ai_status = "queued"
    db.commit()
    for attempt_id in sorted({row.attempt_id for row in rows}):
        _ai_pool.submit(_ai_read_attempt, attempt_id)
    return len(rows)


def _ai_read_attempt(attempt_id: int) -> None:
    """Fonda: bitta o'quvchining navbatdagi kataklarini AI o'qiydi, aniq va kalitga mos javobga ✓ qo'yadi."""

    from .ai_reader import AIReadError, read_boxes

    logger = logging.getLogger("backend.ai")
    with _ai_lock:
        if attempt_id in _ai_running:
            return
        _ai_running.add(attempt_id)
    try:
        with SessionLocal() as db:
            attempt = db.get(Attempt, attempt_id)
            if not attempt or attempt.status != "done":
                return
            test = db.get(Test, attempt.test_id)
            rows = db.scalars(
                select(WrittenAnswer).where(WrittenAnswer.attempt_id == attempt_id, WrittenAnswer.ai_status == "queued")
            ).all()
            if not rows:
                return
            labels = {kq.number: _key_question_label(kq) for kq in test.key_questions}
            try:
                read = read_boxes(test.subject, [(labels.get(row.question_ref, str(row.question_ref)), row.image)
                                                 for row in rows])
            except AIReadError as error:
                logger.warning("AI o'qiy olmadi (o'quvchi %s): %s", attempt_id, error)
                for row in rows:
                    row.ai_status = "error"
                db.commit()
                return
            overrides = _key_override(db, test, attempt.variant)
            keys = {kq.number: overrides.get(kq.number, kq.correct) for kq in test.key_questions}
            db.expire_all()  # AI o'qiyotganda o'qituvchi baho qo'ygan bo'lishi mumkin — bazadagisi olinadi
            changed = False
            for row in rows:
                found = read.get(labels.get(row.question_ref, str(row.question_ref)))
                if found is None:
                    row.ai_status = "error"
                    continue
                row.ai_text, row.ai_sure = found
                row.ai_status = "done"
                changed |= _apply_ai_verdict(row, keys.get(row.question_ref, ""))
            db.commit()
            if changed:
                _regrade_attempt(db, test, test.teacher, db.get(Attempt, attempt_id))
    except Exception:  # noqa: BLE001 — fondagi ish: xato bo'lsa kataklar o'qituvchiga qoladi
        logger.exception("AI o'qishda kutilmagan xato (o'quvchi %s)", attempt_id)
    finally:
        with _ai_lock:
            _ai_running.discard(attempt_id)


PAGE_IMAGE_LIMIT = 2_500_000  # titulning bitta beti rasmi (base64 matni) — ko'pi bilan


def _store_page_images(db: Session, test: Test, attempt: Attempt, images: list[dict]) -> None:
    """
    Milliy sertifikat titulidagi butun bet rasmlari (skanerlangan): Ona tili essesi — o'quvchining "qo'lda yozilgan
    esse" rasmi (o'qituvchi ko'rib baholaydi); Kimyo/Biologiya yozma ish yechimi — titul beti rasmi. Qayta
    skanerlansa — shu bet yangisi bilan almashadi.
    """

    for item in images or []:
        image, page = item.get("image"), item.get("page")
        if not isinstance(image, str) or not image.startswith("data:image/") or len(image) > PAGE_IMAGE_LIMIT:
            continue
        if not isinstance(page, int) or not 1 <= page <= 10:
            continue
        if item.get("ref") == "essay" and test.essay_max:
            db.execute(delete(EssayImage).where(EssayImage.attempt_id == attempt.id, EssayImage.page == page))
            db.add(EssayImage(attempt_id=attempt.id, page=page, image=image))
        elif item.get("ref") == "solution":
            db.execute(delete(SheetImage).where(SheetImage.attempt_id == attempt.id, SheetImage.page == page))
            db.add(SheetImage(attempt_id=attempt.id, page=page, image=image))
    db.flush()


def _store_written_images(db: Session, test: Test, attempt: Attempt, images: dict[str, str]) -> None:
    """Skanerlangan varaqning yozma kataklari: yangi rasm — baho qaytadan qo'yiladi (bo'sh katak — o'zi ✗)."""

    if not images or test.mode != "key":
        return
    from .omr import written_is_blank  # og'ir kutubxona (OpenCV) — faqat kerak bo'lganda

    text_refs = {kq.number for kq in test.key_questions if kq.question_type == "text"}
    existing = {
        row.question_ref: row
        for row in db.scalars(select(WrittenAnswer).where(WrittenAnswer.attempt_id == attempt.id)).all()
    }
    for key, image in images.items():
        try:
            ref = int(key)
        except ValueError:
            continue
        if ref not in text_refs or not isinstance(image, str) or not image.startswith("data:image/"):
            continue
        if len(image) > WRITTEN_IMAGE_LIMIT:
            continue
        row = existing.get(ref)
        if row is None:
            row = WrittenAnswer(attempt_id=attempt.id, question_ref=ref)
            db.add(row)
        row.image = image
        row.blank = written_is_blank(image)
        row.verdict = False if row.blank else None
        row.checked_at = None
        row.created_at = utcnow()
        # Yangi rasm — AI ham qaytadan o'qiydi
        row.ai_text, row.ai_sure, row.ai_status, row.auto_verdict = None, None, None, False
    db.flush()


def _written_rows(db: Session, test: Test, ref: int | None = None, with_images: bool = False):
    """
    Testdagi (natijasi bor o'quvchilarning) yozma katak yozuvlari: (WrittenAnswer, Attempt).
    ref — faqat shu savol. Katak rasmlari (base64) faqat with_images=True bo'lsa o'qiladi:
    butun test bo'yicha yuzlab rasmni sanash uchun xotiraga yuklash server xotirasini to'ldirardi.
    """

    query = (
        select(WrittenAnswer, Attempt)
        .join(Attempt, Attempt.id == WrittenAnswer.attempt_id)
        .where(Attempt.test_id == test.id, Attempt.status == "done")
    )
    if ref is not None:
        query = query.where(WrittenAnswer.question_ref == ref)
    if not with_images:
        query = query.options(defer(WrittenAnswer.image))
    return db.execute(query).all()


def _written_stats(db: Session, test: Test) -> dict | None:
    """Test sahifasi uchun: nechta yozma katak saqlangan va nechtasi hali baholanmagan."""

    if test.enrollment_type != "closed" or test.mode != "key":
        return None
    if not any(kq.question_type == "text" for kq in test.key_questions):
        return None
    total, unchecked = db.execute(
        select(func.count(), func.count(case((WrittenAnswer.verdict.is_(None), 1))))
        .select_from(WrittenAnswer)
        .join(Attempt, Attempt.id == WrittenAnswer.attempt_id)
        .where(Attempt.test_id == test.id, Attempt.status == "done")
    ).one()
    return {"total": total, "unchecked": unchecked}


def _text_questions(test: Test) -> list[KeyQuestion]:
    return [kq for kq in test.key_questions if kq.question_type == "text"]


@app.get("/api/teacher/tests/{test_id}/written")
def written_overview(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yozma savollar ro'yxati: har birida nechta rasm, nechtasi baholangan, nechtasi to'g'ri."""

    from . import ai_reader

    test = _get_own_test(db, teacher, test_id)
    counts: dict[int, dict[str, int]] = {}
    queued = unread = auto = 0
    for row, _ in _written_rows(db, test):
        item = counts.setdefault(row.question_ref, {"total": 0, "checked": 0, "correct": 0})
        item["total"] += 1
        item["checked"] += row.verdict is not None
        item["correct"] += row.verdict is True
        queued += row.ai_status == "queued"
        # AI o'qimagan (yoki xato bergan) va AI aniq o'qib, lekin baholanmay qolgan javoblar
        unread += row.verdict is None and not row.blank and (
            row.ai_status in (None, "error") or (row.ai_status == "done" and bool(row.ai_sure)))
        auto += bool(row.auto_verdict)
    return {
        "title": test.title,
        "questions": [
            {"ref": kq.number, "label": _key_question_label(kq), "key": kq.correct,
             **counts.get(kq.number, {"total": 0, "checked": 0, "correct": 0})}
            for kq in _text_questions(test)
        ],
        # AI: ulanganmi (qaysi), nechta katak navbatda (o'qilmoqda), nechtasi hali o'qilmagan,
        # nechtasiga o'zi ✓ qo'ydi, oxirgi xato (o'qilmagan katak bo'lsa — sababini o'qituvchi ko'radi)
        "ai": {"enabled": ai_reader.ai_enabled(), "provider": ai_reader.ai_provider(),
               "model": ai_reader.GEMINI_MODEL if ai_reader.ai_provider() == "gemini" else ai_reader.AI_MODEL,
               "queued": queued, "unread": unread, "auto": auto,
               "error": (ai_reader.last_error or {}).get("message") if unread else None},
    }


@app.post("/api/teacher/tests/{test_id}/written/ai")
def written_ai_read(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Hali baholanmagan yozma javoblarni AI bilan o'qish (masalan AI ulanishidan oldin skanerlanganlar)."""

    from .ai_reader import ai_enabled

    test = _get_own_test(db, teacher, test_id)
    if not ai_enabled():
        raise HTTPException(status_code=400, detail="AI ulanmagan — administrator API kalitini sozlashi kerak")
    attempt_ids = sorted({attempt.id for _, attempt in _written_rows(db, test)})
    graded = _apply_saved_ai_verdicts(db, attempt_ids)  # AI allaqachon o'qiganlari — qayta so'rovsiz
    return {"ok": True, "queued": _queue_ai_reading(db, attempt_ids), "graded": graded}


@app.get("/api/teacher/tests/{test_id}/written/{ref}")
def written_question(
    test_id: int, ref: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """Bitta yozma savol (masalan 36a): hamma o'quvchilarning katak rasmi, bahosi va kalit."""

    test = _get_own_test(db, teacher, test_id)
    questions = _text_questions(test)
    index = next((position for position, kq in enumerate(questions) if kq.number == ref), None)
    if index is None:
        raise HTTPException(status_code=404, detail="Yozma savol topilmadi")
    question = questions[index]
    variants = _test_variants(db, test)
    rows = list(_written_rows(db, test, ref=ref, with_images=True))
    rows.sort(key=lambda pair: _uz_sort_key(pair[1].offline_name or ""))
    return {
        "ref": ref,
        "label": _key_question_label(question),
        "key": question.correct,
        "prev": questions[index - 1].number if index > 0 else None,
        "next": questions[index + 1].number if index + 1 < len(questions) else None,
        "items": [
            {
                "attempt_id": attempt.id,
                "name": attempt.offline_name or "Noma'lum",
                "number": attempt.sheet_number,
                # Boshqa variantdagi o'quvchi — o'z variantining kaliti bilan
                "variant": attempt.variant if attempt.variant and attempt.variant != "A" else None,
                "key": variants.get(attempt.variant, {}).get(ref, question.correct)
                if attempt.variant and attempt.variant != "A" else question.correct,
                "image": row.image,
                "verdict": row.verdict,
                "blank": row.blank,
                # AI o'qigani: matn, aniqmi, holati; auto — bahoni AI qo'ygan
                "ai_text": row.ai_text,
                "ai_sure": row.ai_sure,
                "ai_status": row.ai_status,
                "auto": bool(row.auto_verdict),
            }
            for row, attempt in rows
        ],
    }


def _regrade_attempt(db: Session, test: Test, teacher: Teacher, attempt: Attempt) -> None:
    """Saqlangan javoblardan qayta baholash (yozma javob bahosi o'zgardi) va sinf natijasini yangilash."""

    answers = {
        str(answer.question_ref): answer.chosen or ""
        for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
    }
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))
    _grade_answers(db, attempt, test, answers)
    _calculate_attempt_score(db, attempt, test)
    db.commit()
    try:
        _sync_class_result(db, test, teacher, attempt)
    except Exception:  # natija imtihonda saqlangan — sinfga yozilmasa ham yo'qolmaydi
        db.rollback()
        logging.getLogger("backend").warning("Sinf natijasiga yozib bo'lmadi", exc_info=True)


@app.post("/api/teacher/tests/{test_id}/written/{ref}/rest")
def set_written_rest(
    test_id: int, ref: int, body: WrittenRestIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """Shu savolda hali baholanmagan kataklarning hammasiga bir xil baho (odatda: qolganlari ✗)."""

    test = _get_own_test(db, teacher, test_id)
    pending = [(row, attempt) for row, attempt in _written_rows(db, test, ref=ref) if row.verdict is None]
    for row, _ in pending:
        row.verdict = body.verdict
        row.auto_verdict = False
        row.checked_at = utcnow()
    db.flush()
    for _, attempt in pending:
        _regrade_attempt(db, test, teacher, attempt)
    return {"ok": True, "updated": len(pending)}


@app.post("/api/teacher/tests/{test_id}/written/{ref}/{attempt_id}")
def set_written_verdict(
    test_id: int, ref: int, attempt_id: int, body: WrittenVerdictIn,
    teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db),
):
    """O'qituvchi bitta katakka baho qo'yadi (✓ / ✗ / bekor) — o'quvchi natijasi darhol qayta hisoblanadi."""

    test = _get_own_test(db, teacher, test_id)
    row = db.scalar(
        select(WrittenAnswer)
        .join(Attempt, Attempt.id == WrittenAnswer.attempt_id)
        .where(WrittenAnswer.attempt_id == attempt_id, WrittenAnswer.question_ref == ref,
               Attempt.test_id == test.id, Attempt.status == "done")
    )
    if not row:
        raise HTTPException(status_code=404, detail="Yozma javob topilmadi")
    row.verdict = body.verdict
    row.auto_verdict = False  # endi o'qituvchi bahosi
    row.checked_at = utcnow() if body.verdict is not None else None
    attempt = db.get(Attempt, attempt_id)
    _regrade_attempt(db, test, teacher, attempt)
    return {"ok": True, "verdict": row.verdict, "correct": attempt.correct, "percent": attempt.percent}


@app.post("/api/teacher/tests/{test_id}/close")
def close_test(
    test_id: int,
    force: bool = False,
    teacher: Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Testni yakunlaydi: natijalar qotadi va test "Yopilgan testlar" ro'yxatiga o'tadi.
    Javoblari kiritilmagan o'quvchilar bo'lsa — o'qituvchi "Baribir yakunlash"ni
    tasdiqlagandagina (force=true) yakunlanadi; ular natijalarga kirmaydi.
    """

    test = _get_own_test(db, teacher, test_id)

    missing = db.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.test_id == test.id, Attempt.status == "pending")
    )
    if missing and not force:
        raise HTTPException(
            status_code=400,
            detail=f"Yakunlash uchun barcha o'quvchilar javobini kiriting — {missing} ta o'quvchi qolgan",
        )
    if _key_missing(test):
        raise HTTPException(
            status_code=400,
            detail=f"Javoblar kaliti to'liq emas — {_key_missing(test)} ta savolning to'g'ri javobi kiritilmagan. "
                   "Avval «Kalit → Kalitni tuzatish» da kiriting",
        )
    written = _written_stats(db, test)
    if written and written["unchecked"]:
        raise HTTPException(
            status_code=400,
            detail=f"Yozma javoblar baholanmagan — {written['unchecked']} ta. "
                   "Avval «Yozma javoblar» bo'limida ✓ yoki ✗ qo'ying",
        )

    _mark_test_closed(test)
    db.commit()

    # Natijalar hisoboti o'qituvchining Telegram chatiga ham yuboriladi
    sent = send_results_to_teacher(db, test)

    return {"ok": True, "report_sent": sent}


@app.post("/api/teacher/tests/{test_id}/reopen")
def reopen_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yakunlangan testni qayta ochadi (masalan, yana o'quvchi qo'shish kerak bo'lsa)."""

    test = _get_own_test(db, teacher, test_id)
    _mark_test_reopened(test)  # avtomatik yopish/o'chirish muddati ham shu kundan qayta sanaladi
    test.code = _new_test_code(db)  # qayta ochilgan test — yangi kod bilan
    # Tugash vaqti o'tib ketgan bo'lsa — test baribir "vaqti tugagan" bo'lib qolmasin
    if test.end_time and test.end_time <= utcnow():
        test.end_time = None

    # Hisobot "allaqachon yuborilgan" belgisi ham tozalanadi — aks holda test
    # qayta yakunlanganda, yangi o'quvchilar natijasi bilan hisobot kelmay qolardi.
    test.results_sent_at = None

    db.commit()
    return {"ok": True, "code": test.code}


@app.get("/api/teacher/tests/{test_id}/results")
def test_results(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Shu testni ishlagan o'quvchilarning natijalar jadvali: o'rin, ism, to'g'ri,
    xato, foiz, Rasch balli va darajasi (o'rin bo'yicha tartiblangan).
    """

    test = _get_own_test(db, teacher, test_id)
    report = build_rasch_report(db, test)
    attempts = [db.get(Attempt, row["attempt_id"]) for row in report["rows"]]
    splits = score_splits(db, test, attempts)

    return {
        "id": test.id,
        "title": test.title,
        "code": test.code,
        "status": test.status,
        "price": test.price,
        "mode": test.mode,
        "enrollment_type": test.enrollment_type,
        "total_questions": _question_count(test),
        "summary": report["summary"],
        "uses_rasch": report["uses_rasch"],
        "section_names": report["section_names"],
        "essay_max": report["essay_max"],
        "essay_pending": report["essay_pending"],
        "sections_text": sections_text(_test_sections(test)),
        "sections": _test_sections(test),
        # Bo'limlarni tugmachalar bilan belgilash uchun savol raqamlari (a/b qismlar — bitta raqam)
        "question_numbers": sorted({number for number in (_item_number(label) for _, label in _rasch_items(test)) if number}),
        "rows": [{**row, **splits[row["attempt_id"]]} for row in report["rows"]],
        "pending": [
            {"name": name}
            for name in db.scalars(
                select(Attempt.offline_name)
                .where(Attempt.test_id == test.id, Attempt.status == "pending", Attempt.user_id.is_(None))
                .order_by(Attempt.offline_name)
            ).all()
        ],
    }


@app.get("/api/teacher/tests/{test_id}/stats")
def test_stats(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Shu testning umumiy statistikasi va savollar bo'yicha xatolar foizi."""
    return build_test_stats(db, _get_own_test(db, teacher, test_id))


@app.get("/api/teacher/tests/{test_id}/questions")
def teacher_test_questions(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yopiq testda o'qituvchi javoblarni kiritishi uchun savollar tuzilishi."""

    test = _get_own_test(db, teacher, test_id)

    if test.mode == "key":
        questions = [
            {"ref": str(kq.number), "label": _key_question_label(kq), "question_type": kq.question_type,
             "option_count": kq.option_count, "correct": kq.correct}
            for kq in test.key_questions
        ]
    else:
        questions = [
            {"ref": str(q.id), "label": q.text, "question_type": "single", "option_count": 4}
            for q in test.questions
        ]

    return {"mode": test.mode, "keyboard": subject_keyboard(test.subject), "questions": questions,
            "variants": _variant_names(db, test)}


@app.patch("/api/teacher/tests/{test_id}/price")
def update_test_price(
    test_id: int, body: PriceUpdate, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi testni yaratgandan keyin ham narxini o'zgartirishi uchun. Avvalgi to'lovlarga ta'sir qilmaydi."""

    test = _get_own_test(db, teacher, test_id)

    # Yaratishdagi qoidalar bu yerda ham amal qiladi — aks holda bepul yopiq
    # testni keyin pullik qilib, to'lov tizimini chetlab o'tish mumkin bo'lardi.
    if body.price > 0 and test.enrollment_type == "closed":
        raise HTTPException(status_code=422, detail="Offline testni pullik qilib bo'lmaydi")
    if body.price > 0 and not _can_sell_tests(teacher):
        raise HTTPException(status_code=422, detail="Pullik test uchun avval karta raqamingizni kiriting")

    test.price = body.price
    db.commit()
    return {"id": test.id, "price": test.price}


CERTIFICATE_BG = colors.HexColor("#f7eedb")
CERTIFICATE_PATTERN = colors.HexColor("#ecdcb8")
CERTIFICATE_INK = colors.HexColor("#1f2937")
CERTIFICATE_MUTED = colors.HexColor("#6b5d45")
CERTIFICATE_ACCENT = colors.HexColor("#1f4e79")


def _certificate_number(test: Test, row: dict) -> str:
    """Platformaning o'z raqami (MT-kod-urinish). Davlat sertifikati raqamiga o'xshatilmaydi."""
    return f"MT-{test.code}-{row['attempt_id']:05d}"


def _draw_certificate_background(pdf, width: float, height: float) -> None:
    """Och-sariq fon, burchaklarda yengil olti burchakli naqsh va ingichka ramka."""

    pdf.setFillColor(CERTIFICATE_BG)
    pdf.rect(0, 0, width, height, fill=1, stroke=0)

    pdf.setStrokeColor(CERTIFICATE_PATTERN)
    pdf.setLineWidth(1.2)
    size = 26
    for corner_x, corner_y in ((0, 0), (width, 0), (0, height), (width, height)):
        sign_x = 1 if corner_x == 0 else -1
        sign_y = 1 if corner_y == 0 else -1
        for ring in range(1, 5):
            for step in range(ring + 1):
                cx = corner_x + sign_x * (ring * size * 1.5 - step * size * 0.8)
                cy = corner_y + sign_y * (step * size * 1.6)
                points = [(cx + size * 0.55 * math.cos(math.radians(60 * k)), cy + size * 0.55 * math.sin(math.radians(60 * k)))
                          for k in range(7)]
                path = pdf.beginPath()
                path.moveTo(*points[0])
                for point in points[1:]:
                    path.lineTo(*point)
                pdf.drawPath(path, stroke=1, fill=0)

    pdf.setStrokeColor(colors.HexColor("#c9b07a"))
    pdf.setLineWidth(1)
    pdf.rect(18, 18, width - 36, height - 36)


def _draw_certificate_page(pdf, test: Test, teacher: Teacher, row: dict, section_names: list[str], issued: str) -> None:
    """
    Bitta o'quvchining sertifikati (A4, tik). Tuzilishi Milliy sertifikatga
    o'xshash (ma'lumotlar, ball, foiz, daraja, bo'limlar), lekin bu — MOCK test
    sertifikati: davlat gerbi, agentlik nomi, rasmiy raqam yoki imzo yo'q va
    rasmiy hujjat emasligi aniq yozib qo'yiladi.
    """

    width, height = A4
    left, right = 62, width - 62
    _draw_certificate_background(pdf, width, height)

    # Sarlavha: platforma belgisi va nomi
    logo_path = FRONTEND_DIR / "img" / "chittak-icon.png"
    if logo_path.exists():
        pdf.drawImage(str(logo_path), width / 2 - 30, height - 112, 60, 60, mask="auto")
    else:
        pdf.setFillColor(CERTIFICATE_ACCENT)
        pdf.circle(width / 2, height - 82, 30, fill=1, stroke=0)
    pdf.setFillColor(CERTIFICATE_INK)
    pdf.setFont(REPORT_FONT_BOLD, 12)
    pdf.drawCentredString(width / 2, height - 136, "CHITTAK")
    pdf.setFont(REPORT_FONT, 9.5)
    pdf.setFillColor(CERTIFICATE_MUTED)
    pdf.drawCentredString(width / 2, height - 150, "Mock (sinov) imtihon natijalari")
    pdf.setStrokeColor(CERTIFICATE_INK)
    pdf.setLineWidth(1.6)
    pdf.line(left, height - 162, right, height - 162)

    pdf.setFillColor(CERTIFICATE_INK)
    pdf.setFont(REPORT_FONT_BOLD, 13.5)
    pdf.drawCentredString(width / 2, height - 190, "FANNI BILISH DARAJASI")
    pdf.drawCentredString(width / 2, height - 208, "TO'G'RISIDA MOCK SERTIFIKAT")

    def field(y: float, label: str, value: str, bold: bool = True, size: float = 10.5) -> None:
        pdf.setFillColor(CERTIFICATE_MUTED)
        pdf.setFont(REPORT_FONT, 10)
        pdf.drawString(left, y, label)
        pdf.setFillColor(CERTIFICATE_INK)
        pdf.setFont(REPORT_FONT_BOLD if bold else REPORT_FONT, size)
        pdf.drawString(left + 215, y, value)

    def divider(y: float) -> None:
        pdf.setStrokeColor(colors.HexColor("#d8c79f"))
        pdf.setLineWidth(0.8)
        pdf.line(left, y, right, y)

    field(height - 240, "Sertifikat raqami:", _certificate_number(test, row))
    divider(height - 254)

    # O'quvchi
    field(height - 280, "Ism-familiyasi:", row["name"][:40].upper(), size=12)
    field(height - 302, "Test:", f"«{test.title[:40]}»", bold=False)
    field(height - 324, "Test kodi:", test.code, bold=False)
    divider(height - 340)

    # Natija
    ball = f"{row['ball']:.2f}" if row.get("ball") is not None else "—"  # Milliy sertifikatdagidek 2 xona
    field(height - 368, "Fan:", test.subject[:40])
    if row.get("dtm_ball") is not None:
        field(height - 392, "DTM bali:", f"{row['dtm_ball']:g} / {DTM_MAX_BALL:g}".replace(".", ","), size=12)
    elif row.get("ball") is not None:
        field(height - 392, "Umumiy to'plagan bali:", ball, size=12)
    else:  # oddiy test — Rasch balli yo'q
        field(height - 392, "To'g'ri javoblar:", f"{row['correct']} / {row['total']}", size=12)
    essay = row.get("test_ball") is not None  # esseli test: test va yozma ish natijasi alohida (pastda)
    if row.get("dtm_ball") is None and row.get("cert_percent") is not None:
        # Milliy sertifikatdagidek: "Umumiy ballga nisbatan foiz ko'rsatkichi" — ball / 65 × 100
        field(height - 416, "Foiz ko'rsatkichi:", _percent_text(row.get("cert_percent")).replace("%", " %"), size=12)
    else:
        field(height - 416, "To'g'ri javoblar foizi:", f"{row['percent']:g} %  ({row['correct']}/{row['total']})", size=12)
    if row.get("dtm_ball") is None and row.get("grade"):  # DTM va oddiy testda daraja yo'q
        field(height - 440, "Sertifikat darajasi:", row["grade"], size=13)
    field(height - 464, "Qatnashchilar orasidagi o'rni:", str(row["rank"]))

    # Bo'limlar
    y = height - 502
    if essay:
        test_value, essay_value = _essay_cells(row)
        for label, value in (("Test sinovi natijasi:", test_value), ("Yozma ish natijasi:", essay_value)):
            pdf.setFillColor(CERTIFICATE_INK)
            pdf.setFont(REPORT_FONT, 10.5)
            pdf.drawString(left + 14, y, label)
            pdf.drawRightString(right, y, value)
            pdf.setStrokeColor(colors.HexColor("#e3d5b3"))
            pdf.setLineWidth(0.5)
            pdf.line(left + 14, y - 5, right, y - 5)
            y -= 20
    elif section_names:
        pdf.setFillColor(CERTIFICATE_INK)
        pdf.setFont(REPORT_FONT_BOLD, 10.5)
        pdf.drawString(left, y, "Bo'limlar bo'yicha test sinovi natijalari:")
        pdf.setFont(REPORT_FONT, 10.5)
        for name in section_names:
            y -= 20
            pdf.setFillColor(CERTIFICATE_INK)
            pdf.drawString(left + 14, y, name)
            pdf.drawRightString(right, y, f"{row['sections'].get(name, 0):g} %")
            pdf.setStrokeColor(colors.HexColor("#e3d5b3"))
            pdf.setLineWidth(0.5)
            pdf.line(left + 14, y - 5, right, y - 5)

    # Pastki qism: sana, o'qituvchi va ogohlantirish
    pdf.setFillColor(CERTIFICATE_INK)
    pdf.setFont(REPORT_FONT, 10)
    pdf.drawString(left, 150, f"Berilgan sanasi: {issued}")
    pdf.drawRightString(right, 150, f"O'qituvchi: {teacher.user.full_name[:40]}")
    divider(132)
    pdf.setFillColor(CERTIFICATE_MUTED)
    pdf.setFont(REPORT_FONT_ITALIC, 8.5)
    pdf.drawCentredString(width / 2, 112, "Ushbu hujjat o'quv (mock) test natijasini ko'rsatadi va RASMIY DAVLAT SERTIFIKATI HISOBLANMAYDI.")
    if row.get("dtm_ball") is not None:
        pdf.drawCentredString(width / 2, 100, "DTM bali: majburiy fanlar — 1,1, 1-mutaxassislik — 3,1, 2-mutaxassislik — 2,1 ball.")
    else:
        pdf.drawCentredString(width / 2, 100, "Ball Rasch modeli bo'yicha shu testning qatnashchilariga nisbatan hisoblangan.")


def _certificate_rows(db: Session, test: Test, attempt_id: int | None = None) -> tuple[list[dict], list[str]]:
    report = build_rasch_report(db, test)
    rows = [row for row in report["rows"] if attempt_id is None or row["attempt_id"] == attempt_id]
    if not rows:
        raise HTTPException(status_code=404, detail="Natija topilmadi")
    return rows, report["section_names"]


def _render_certificates(test: Test, teacher: Teacher, rows: list[dict], section_names: list[str]) -> bytes:
    buffer = io.BytesIO()
    pdf = pdf_canvas.Canvas(buffer, pagesize=A4)
    pdf.setTitle(f"{test.title} — sertifikatlar")
    issued = (utcnow() + TASHKENT_OFFSET).strftime("%d.%m.%Y")
    for row in rows:
        _draw_certificate_page(pdf, test, teacher, row, section_names, issued)
        pdf.showPage()
    pdf.save()
    return buffer.getvalue()


def generate_certificates_pdf(db: Session, test: Test, teacher: Teacher, attempt_id: int | None = None) -> bytes:
    """Har bir o'quvchiga bir sahifali MOCK sertifikat (A4, tik). attempt_id berilsa — faqat bittasi."""

    rows, section_names = _certificate_rows(db, test, attempt_id)
    return _render_certificates(test, teacher, rows, section_names)


# Hozir yuborilayotgan testlar — tugma ikki marta bosilsa, o'quvchilarga ikki marta bormasin
_certificates_in_progress: set[int] = set()
_certificates_lock = threading.Lock()


def _certificate_caption(test: Test, row: dict) -> str:
    # DTM testida natija — DTM bali; Rasch balli va darajasi (Milliy sertifikat uslubi) ko'rsatilmaydi
    if row.get("dtm_ball") is not None:
        score = (f"🎯 DTM bali: {row['dtm_ball']:g} / {DTM_MAX_BALL:g}".replace(".", ",") + "\n")
    else:
        score = f"📊 Ball: {row['ball']:.1f}\n🏅 Daraja: {row['grade']}\n"
        if row.get("cert_percent") is not None:  # Milliy sertifikatdagidek: ball / 65 × 100
            score += f"📈 Foiz ko'rsatkichi: {_percent_text(row['cert_percent'])}\n"
    return (
        f"🎓 «{test.title}» testi natijangiz\n\n"
        + score
        + f"✅ To'g'ri javoblar: {row['correct']}/{row['total']} ({row['percent']:g}%)\n"
        f"🏆 O'rin: {row['rank']}"
    )


def _deliver_student_certificates(test_id: int, teacher_chat_id: int, test, teacher, section_names: list[str],
                                  jobs: list[tuple[int, dict]]) -> None:
    """
    Fonda: har bir o'quvchiga o'z sertifikati, oxirida o'qituvchiga qisqa hisobot.
    PDF ham shu yerda (fonda) yasaladi — minglab o'quvchida so'rov ichida
    yasalsa, o'qituvchi yarim daqiqa kutib qolardi va so'rov uzilishi mumkin edi.
    test/teacher — bazaga bog'lanmagan oddiy nusxalar (sessiya allaqachon yopilgan).
    """

    sent, failed = 0, []
    try:
        for chat_id, row in jobs:
            try:
                pdf_bytes = _render_certificates(test, teacher, [row], section_names)
                _send_pdf_to_telegram(chat_id, "sertifikat.pdf", pdf_bytes, _certificate_caption(test, row))
                sent += 1
            except Exception:
                failed.append(row["name"])
            time.sleep(0.05)  # Telegram cheklovi: sekundiga ~30 xabar
        text = f"🎓 Sertifikatlar yuborildi: {sent} ta o'quvchiga."
        if failed:
            text += (f"\n\n⚠️ {len(failed)} ta o'quvchiga yetmadi (botni bloklagan yoki /start bosmagan):\n"
                     + "\n".join(f"• {name}" for name in failed[:30]))
        try:
            httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                       json={"chat_id": teacher_chat_id, "text": text}, timeout=10)
        except Exception:
            logging.getLogger("backend").warning("Sertifikat hisoboti o'qituvchiga yuborilmadi", exc_info=True)
    finally:
        with _certificates_lock:
            _certificates_in_progress.discard(test_id)


@app.post("/api/teacher/tests/{test_id}/certificates/send-students")
def send_certificates_to_students(test_id: int, teacher: Teacher = Depends(get_current_teacher),
                                  user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Testni ishlagan har bir o'quvchining bot chatiga uning o'z sertifikati
    (PDF: ball, foiz, daraja va bo'limlar) yuboriladi. O'qituvchi qo'lda qo'shgan
    (Telegram'siz) o'quvchilarga yuborib bo'lmaydi — ular o'tkazib yuboriladi.
    Faqat yakunlangan testdan. Yuborish fonda bajariladi, oxirida o'qituvchiga hisobot keladi.
    """

    test = _get_own_test(db, teacher, test_id)
    # Rasch balli qatnashchilarga nisbatan — test ochiq turganda yangi o'quvchi
    # qo'shilsa, ballar o'zgaradi va yuborilgan sertifikat noto'g'ri bo'lib qoladi.
    if test.status != "closed":
        raise HTTPException(
            status_code=409,
            detail="Avval testni yakunlang (🏁 Yakunlash) — sertifikatlar faqat yakunlangan testdan yuboriladi",
        )
    rows, section_names = _certificate_rows(db, test)

    attempt_users = dict(db.execute(
        select(Attempt.id, User.tg_id).join(User, User.id == Attempt.user_id)
        .where(Attempt.id.in_([row["attempt_id"] for row in rows]))
    ).all())
    targets = [row for row in rows if row["attempt_id"] in attempt_users]
    if not targets:
        raise HTTPException(status_code=409, detail="Telegram orqali ishlagan o'quvchi yo'q — yuboradigan hech kim topilmadi")

    with _certificates_lock:
        if test.id in _certificates_in_progress:
            raise HTTPException(status_code=409, detail="Sertifikatlar hozir yuborilmoqda — biroz kuting")
        _certificates_in_progress.add(test.id)

    jobs = [(attempt_users[row["attempt_id"]], row) for row in targets]
    # Fon ipi bazaga murojaat qilmasin — kerakli maydonlar oddiy nusxaga olinadi
    test_copy = SimpleNamespace(title=test.title, code=test.code, subject=test.subject)
    teacher_copy = SimpleNamespace(user=SimpleNamespace(full_name=teacher.user.full_name))
    threading.Thread(
        target=_deliver_student_certificates,
        args=(test.id, user.tg_id, test_copy, teacher_copy, section_names, jobs),
        daemon=True,
    ).start()
    logging.getLogger("backend").info("Sertifikatlar o'quvchilarga yuborilmoqda: test=%s, %s ta", test.id, len(jobs))
    return {"queued": len(jobs), "skipped": len(rows) - len(targets)}


@app.put("/api/teacher/tests/{test_id}/sections")
def set_test_sections(test_id: int, body: SectionsIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchi savollarni bo'limlarga ajratadi (Algebra: 1-20 ...). Bo'sh matn — bo'limsiz."""
    test = _get_own_test(db, teacher, test_id)
    try:
        sections = parse_sections(body.text)
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))
    test.sections = json.dumps(sections, ensure_ascii=False)  # bo'sh bo'lsa "[]" — shablon qo'llanmaydi
    db.commit()
    return {"sections": sections, "text": sections_text(sections)}


@app.get("/api/teacher/tests/{test_id}/certificates")
def download_certificates(test_id: int, attempt_id: int | None = None, teacher: Teacher = Depends(get_current_teacher),
                          db: Session = Depends(get_db)):
    test = _get_own_test(db, teacher, test_id)
    return StreamingResponse(io.BytesIO(generate_certificates_pdf(db, test, teacher, attempt_id)), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="sertifikat_{test.code}.pdf"'})


@app.post("/api/teacher/tests/{test_id}/certificates/send")
def send_certificates(test_id: int, attempt_id: int | None = None, teacher: Teacher = Depends(get_current_teacher),
                      user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    test = _get_own_test(db, teacher, test_id)
    _send_pdf_to_telegram(user.tg_id, f"sertifikat_{test.code}.pdf", generate_certificates_pdf(db, test, teacher, attempt_id),
                          f"🎓 «{test.title}» — sertifikatlar")
    return {"ok": True}


@app.get("/api/teacher/tests/{test_id}/pdf")
def download_pdf(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """PDF hisobotni to'g'ridan-to'g'ri brauzerga yuklab beradi (lokal sinov uchun)."""

    test = _get_own_test(db, teacher, test_id)
    pdf_bytes = generate_test_report_pdf(db, test, teacher)

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{test.code}.pdf"'},
    )


def pdf_pages_to_png(pdf_bytes: bytes, scale: float = 2.0) -> list[bytes]:
    """PDF'ning har bir sahifasi — alohida rasm (PNG)."""

    import pypdfium2 as pdfium

    document = pdfium.PdfDocument(pdf_bytes)
    images = []
    try:
        for index in range(len(document)):
            buffer = io.BytesIO()
            document[index].render(scale=scale).to_pil().convert("RGB").save(buffer, format="PNG", optimize=True)
            images.append(buffer.getvalue())
    finally:
        document.close()
    return images


def test_results_pngs(db: Session, test: Test, teacher: Teacher) -> list[bytes]:
    """Natijalar rasmlari — PDF hisobotdagi "Umumiy natijalar" sahifalarining o'zi, har sahifa alohida."""
    return pdf_pages_to_png(generate_test_report_pdf(db, test, teacher, overview_only=True))


@app.get("/api/teacher/tests/{test_id}/image")
def download_results_image(test_id: int, page: int = 1, teacher: Teacher = Depends(get_current_teacher),
                           db: Session = Depends(get_db)):
    """Natijalar rasmining bitta sahifasi (PNG) — brauzerda yuklab olish uchun. X-Page-Count — jami sahifalar."""
    test = _get_own_test(db, teacher, test_id)
    images = test_results_pngs(db, test, teacher)
    if not 1 <= page <= len(images):
        raise HTTPException(status_code=404, detail="Bunday sahifa yo'q")
    return StreamingResponse(io.BytesIO(images[page - 1]), media_type="image/png", headers={
        "Content-Disposition": f'attachment; filename="{test.code}_{page}.png"',
        "X-Page-Count": str(len(images)), "Access-Control-Expose-Headers": "X-Page-Count",
    })


TELEGRAM_ALBUM_SIZE = 10  # bitta albomda ko'pi bilan 10 ta rasm


def _send_photo_album(chat_id: int, file_prefix: str, images: list[bytes], caption: str = "") -> None:
    """Bir nechta rasm — albom bo'lib (10 tadan); izoh birinchi rasmda. Bitta rasm — oddiy rasm."""
    if len(images) == 1:
        _send_photo_to_telegram(chat_id, f"{file_prefix}.png", images[0], caption)
        return
    for start in range(0, len(images), TELEGRAM_ALBUM_SIZE):
        chunk = images[start:start + TELEGRAM_ALBUM_SIZE]
        if len(chunk) == 1:  # albom kamida 2 ta rasmdan bo'ladi
            _send_photo_to_telegram(chat_id, f"{file_prefix}_{start + 1}.png", chunk[0])
            continue
        media = [{"type": "photo", "media": f"attach://photo{index}",
                  **({"caption": caption} if caption and start == 0 and index == 0 else {})}
                 for index in range(len(chunk))]
        response = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMediaGroup",
            data={"chat_id": chat_id, "media": json.dumps(media, ensure_ascii=False)},
            files={f"photo{index}": (f"{file_prefix}_{start + index + 1}.png", image, "image/png")
                   for index, image in enumerate(chunk)},
            timeout=60,
        )
        if response.status_code != 200:
            raise HTTPException(status_code=502, detail="Rasmlar yuborilmadi (botga /start bosganmisiz?)")


@app.post("/api/teacher/tests/{test_id}/image/send")
def send_results_image(test_id: int, teacher: Teacher = Depends(get_current_teacher), user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Natijalar rasmlarini (har sahifa — alohida rasm) bot orqali o'qituvchining chatiga yuboradi."""
    test = _get_own_test(db, teacher, test_id)
    images = test_results_pngs(db, test, teacher)
    _send_photo_album(user.tg_id, f"{test.code}_natijalar", images, f"📊 «{test.title}» — natijalar")
    return {"ok": True, "pages": len(images)}


@app.post("/api/teacher/tests/{test_id}/pdf/send")
def send_pdf_to_telegram(
    test_id: int,
    teacher: Teacher = Depends(get_current_teacher),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF hisobotni Telegram bot orqali o'qituvchining shaxsiy chatiga yuboradi."""

    test = _get_own_test(db, teacher, test_id)
    _send_pdf_to_telegram(user.tg_id, f"{test.code}.pdf", generate_test_report_pdf(db, test, teacher))
    return {"ok": True}


# ---------- Excel hisobot ----------
# Uch varaq: natijalar (reyting), har o'quvchining javoblari (yashil — to'g'ri,
# qizil — xato) va savollar tahlili (har variantni nechta o'quvchi tanlagan).


def generate_test_report_xlsx(db: Session, test: Test) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.utils import get_column_letter

    report = build_rasch_report(db, test)
    rows = report["rows"]
    sections = report["section_names"]
    numbers = {
        attempt_id: number
        for attempt_id, number in db.execute(
            select(Attempt.id, Attempt.sheet_number).where(Attempt.test_id == test.id, Attempt.sheet_number.is_not(None))
        ).all()
    }
    reviews = {row["attempt_id"]: _attempt_review(db, test, db.get(Attempt, row["attempt_id"])) for row in rows}

    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F4E79")
    green = PatternFill("solid", fgColor="C6EFCE")
    red = PatternFill("solid", fgColor="FFC7CE")
    grey = PatternFill("solid", fgColor="EDEDED")
    center = Alignment(horizontal="center", vertical="center")

    def header(sheet, titles: list[str], widths: list[int]) -> None:
        sheet.append(titles)
        for column, width in enumerate(widths, start=1):
            cell = sheet.cell(row=1, column=column)
            cell.font, cell.fill, cell.alignment = header_font, header_fill, center
            sheet.column_dimensions[get_column_letter(column)].width = width
        sheet.freeze_panes = "C2"

    workbook = Workbook()

    # 1) Natijalar
    results = workbook.active
    results.title = "Natijalar"
    dtm = _is_dtm(rows)
    rasch = report["uses_rasch"]
    essay = _is_essay(rows)  # esseli test: test bali va yozma ish bali alohida ustunda
    cert = rasch and not dtm  # Milliy sertifikat foizi (ball / 65 × 100) — har bir Rasch testida
    header(results, ["O'rin", "Raqam", "Ism-familiya", "To'g'ri", "Xato", "Jami", "Foiz %", *(["DTM bali"] if dtm else []),
                     *(["Test bali", "Yozma ish (esse)"] if essay else []),
                     *(["Ball", "Daraja"] if rasch else []), *(["Sertifikat foizi %"] if cert else []), *sections],
           [7, 8, 32, 9, 8, 7, 9, *([10] if dtm else []), *([10, 12] if essay else []), *([9, 9] if rasch else []),
            *([12] if cert else []), *[14] * len(sections)])
    for row in rows:
        results.append([
            row["rank"], numbers.get(row["attempt_id"]), row["name"], row["correct"], row["wrong"], row["total"],
            row["percent"], *([row["dtm_ball"]] if dtm else []), *([row["test_ball"], row["essay_ball"]] if essay else []),
            *([row["ball"], row["grade"]] if rasch else []), *([row["cert_percent"]] if cert else []),
            *[row["sections"].get(name) for name in sections],
        ])

    # 2) Javoblar: birinchi qator — to'g'ri javoblar kaliti
    first_review = next(iter(reviews.values()), None)
    labels = [question["label"] for question in first_review["questions"]] if first_review else []
    answers = workbook.create_sheet("Javoblar")
    header(answers, ["O'rin", "Ism-familiya", *labels], [7, 32, *[9] * len(labels)])
    if first_review:
        answers.append(["", "To'g'ri javob", *[question["correct"] for question in first_review["questions"]]])
        for cell in answers[2]:
            cell.font = Font(bold=True)
    for row in rows:
        questions = reviews[row["attempt_id"]]["questions"]
        answers.append([row["rank"], row["name"], *[question["chosen"] for question in questions]])
        for column, question in enumerate(questions, start=3):
            cell = answers.cell(row=answers.max_row, column=column)
            cell.alignment = center
            cell.fill = grey if question["chosen"] == "—" else green if question["is_correct"] else red

    # 3) Savollar tahlili: qaysi savolni ko'pchilik xato qildi, qaysi variant "tuzoq" bo'ldi
    letters = "ABCDEF"
    analysis = workbook.create_sheet("Savollar tahlili")
    header(analysis, ["Savol", "To'g'ri javob", "To'g'ri yechdi", "Ulush %", *letters, "Bo'sh"],
           [9, 14, 14, 9, *[7] * len(letters), 8])
    total = len(rows)
    for index, label in enumerate(labels):
        picked = [reviews[row["attempt_id"]]["questions"][index] for row in rows]
        solved = sum(1 for question in picked if question["is_correct"])
        counts = {letter: sum(1 for question in picked if letter in question["chosen"].split(",")) for letter in letters}
        empty = sum(1 for question in picked if question["chosen"] == "—")
        analysis.append([label, first_review["questions"][index]["correct"], solved,
                         round(solved / total * 100, 1) if total else 0, *[counts[letter] or "" for letter in letters], empty])
        share_cell = analysis.cell(row=analysis.max_row, column=4)
        share_cell.fill = green if share_cell.value >= 70 else red if share_cell.value < 40 else PatternFill()

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


@app.get("/api/teacher/tests/{test_id}/excel")
def download_excel(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    test = _get_own_test(db, teacher, test_id)
    return StreamingResponse(
        io.BytesIO(generate_test_report_xlsx(db, test)),
        media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{test.code}_natijalar.xlsx"'},
    )


@app.post("/api/teacher/tests/{test_id}/excel/send")
def send_excel_to_telegram(
    test_id: int,
    teacher: Teacher = Depends(get_current_teacher),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    test = _get_own_test(db, teacher, test_id)
    _send_pdf_to_telegram(
        user.tg_id, f"{test.code}_natijalar.xlsx", generate_test_report_xlsx(db, test),
        caption=f"📊 {test.title} ({test.code}) — natijalar, javoblar va savollar tahlili", mime=XLSX_MIME,
    )
    return {"ok": True}


def _sheet_variant_options(db: Session, test: Test, answers: bool, variant: str | None) -> dict:
    """Varaq uchun variant sozlamalari: blankda — variant doirachalari, kalitda — tanlangan variant javoblari."""

    variants = _variant_names(db, test)
    if answers:
        if variant and variant != "A":
            _check_variant(db, test, variant)
            return {"key_override": _key_override(db, test, variant), "key_variant": variant}
        return {"key_variant": "A"} if variants else {}
    return {"variants": variants or None}


def _parse_sheet_numbers(numbers: str | None) -> set[int] | None:
    """"1,2,5" → {1, 2, 5}. Bo'sh — hammasi (None)."""

    if not numbers:
        return None
    try:
        parsed = {int(item) for item in numbers.split(",") if item.strip()}
    except ValueError:
        raise HTTPException(status_code=422, detail="O'quvchilar ro'yxati noto'g'ri")
    if not parsed:
        raise HTTPException(status_code=422, detail="Birorta ham o'quvchi tanlanmadi")
    return parsed


def _named_sheet_students(db: Session, test: Test, named: bool, numbers: set[int] | None = None) -> list[tuple] | None:
    """Ismli varaqlar uchun: o'qituvchi qo'shgan o'quvchilar (raqam, ism) — raqam tartibida.
    numbers berilsa — faqat shu raqamdagilar (masalan, bugun darsda borlar)."""

    if not named:
        return None
    if (test.sheet_version or 1) < 2:
        raise HTTPException(status_code=400, detail="Bu eski test — ismli varaqlar yangi testlarda bor")
    _ensure_sheet_numbers(db, test)
    attempts = db.scalars(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done")),
               Attempt.sheet_number.is_not(None))
        .order_by(Attempt.sheet_number)
    ).all()
    # Variantlar bo'lsa — yonma-yon o'tirganlar har xil variant oladi (raqam bo'yicha navbatma-navbat)
    variants = _variant_names(db, test)
    if variants:
        for attempt in attempts:
            if not attempt.variant and attempt.status == "pending":
                attempt.variant = variants[(attempt.sheet_number - 1) % len(variants)]
        db.commit()
    students = [(attempt.sheet_number, attempt.offline_name or "", attempt.variant or "A") for attempt in attempts
                if numbers is None or attempt.sheet_number in numbers]
    if not students:
        raise HTTPException(status_code=400, detail="Avval o'quvchilarni qo'shing — har biriga ismli varaq chiqadi"
                            if numbers is None else "Tanlangan o'quvchilar topilmadi")
    return students


def _personal_sheets_pdf(db: Session, test: Test, numbers: set[int] | None = None) -> tuple[bytes, int]:
    """Har o'quvchiga alohida titul (ismi yozilgan, ID'si bo'yalgan) — hammasi bitta PDF'da."""
    students = _named_sheet_students(db, test, True, numbers)
    return generate_answer_sheet_pdf(test, students=students, variants=_variant_names(db, test) or None), len(students)


@app.get("/api/teacher/tests/{test_id}/sheet-students")
def sheet_students(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Ismli titul kimlarga chiqarilishini tanlash uchun: o'quvchilar va bugungi davomatdagi holati.
    present: True — bugun darsda (bor yoki kechikdi), False — yo'q/sababli, None — bugun davomat olinmagan.
    """

    test = _get_own_test(db, teacher, test_id)
    if (test.sheet_version or 1) < 2:
        raise HTTPException(status_code=400, detail="Bu eski test — ismli varaqlar yangi testlarda bor")
    _ensure_sheet_numbers(db, test)
    attempts = db.scalars(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id.is_(None), Attempt.status.in_(("pending", "done")),
               Attempt.sheet_number.is_not(None))
        .order_by(Attempt.sheet_number)
    ).all()

    # Bugungi (Toshkent vaqti) davomat: har o'quvchining eng oxirgi belgisi
    student_ids = [attempt.class_student_id for attempt in attempts if attempt.class_student_id]
    local_now = utcnow() + TASHKENT_OFFSET
    day_start = datetime(local_now.year, local_now.month, local_now.day) - TASHKENT_OFFSET
    today: dict[int, str] = {}
    if student_ids:
        for student_id, status in db.execute(
            select(AttendanceMark.student_id, AttendanceMark.status)
            .join(Attendance, Attendance.id == AttendanceMark.attendance_id)
            .where(AttendanceMark.student_id.in_(student_ids), Attendance.deleted_at.is_(None),
                   Attendance.taken_at >= day_start)
            .order_by(Attendance.taken_at, Attendance.id)
        ).all():
            today[student_id] = status
    rows = []
    for attempt in attempts:
        status = today.get(attempt.class_student_id) if attempt.class_student_id else None
        rows.append({"number": attempt.sheet_number, "name": attempt.offline_name or "",
                     "done": attempt.status == "done",
                     "present": None if status is None else status in ("present", "late")})
    return {"students": rows, "has_attendance": bool(today)}


@app.get("/api/teacher/tests/{test_id}/answer-sheet")
def download_answer_sheet(
    test_id: int,
    answers: bool = False,
    named: bool = False,
    personal: bool = False,
    variant: str | None = None,
    numbers: str | None = None,
    teacher: Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """
    Javoblar varaqasini brauzerga yuklab beradi (answers=true bo'lsa — to'g'ri javobli kalit).
    personal=true — har o'quvchiga ismli titul (ID bo'yalgan), named=true — o'quvchilar ro'yxati (ism va ID).
    """

    test = _get_own_test(db, teacher, test_id)
    if personal and not answers:
        pdf_bytes, _ = _personal_sheets_pdf(db, test, _parse_sheet_numbers(numbers))
        return StreamingResponse(io.BytesIO(pdf_bytes), media_type="application/pdf",
                                 headers={"Content-Disposition": f'attachment; filename="{test.code}_ismli_titullar.pdf"'})
    students = _named_sheet_students(db, test, named and not answers)
    if students:
        pdf_bytes = generate_student_id_list_pdf(test, students)  # faqat ism va ID — to'liq titul emas
    else:
        pdf_bytes = generate_answer_sheet_pdf(test, with_answers=answers, **_sheet_variant_options(db, test, answers, variant))
    file_name = f"{test.code}_{'kalit' if answers else 'ismli_varaqlar' if named else 'javoblar_varaqasi'}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.post("/api/teacher/tests/{test_id}/answer-sheet/send")
def send_answer_sheet_to_telegram(
    test_id: int,
    answers: bool = False,
    named: bool = False,
    personal: bool = False,
    variant: str | None = None,
    numbers: str | None = None,
    teacher: Teacher = Depends(get_current_teacher),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Javoblar varaqasini (yoki to'g'ri javobli kalitni) o'qituvchining Telegram chatiga yuboradi."""

    test = _get_own_test(db, teacher, test_id)

    if personal and not answers:
        pdf_bytes, count = _personal_sheets_pdf(db, test, _parse_sheet_numbers(numbers))
        _send_pdf_to_telegram(
            user.tg_id, f"{test.code}_ismli_titullar.pdf", pdf_bytes,
            caption=(f"🧾 Ismli titullar — {count} ta\n📝 {test.title} ({test.code})\n\n"
                     "Har titulda o'quvchining ismi yozilgan va ID raqami bo'yalgan — chop etib, egasiga tarqating."),
        )
        return {"ok": True}

    if answers:
        caption = (
            f"🔑 Javoblar Kaliti{f' — {variant} variant' if variant else ''}\n\n"
            f"📝 Test: {test.title}\n"
            f"🔑 Kod: {test.code}\n"
            f"📚 Fan: {test.subject}\n\n"
            "Diqqat: bu varaqda to'g'ri javoblar belgilangan — o'quvchilarga tarqatmang!"
        )
    else:
        caption = _answer_sheet_caption(test)
    students = _named_sheet_students(db, test, named and not answers)
    if students:
        caption = (f"👥 O'quvchilar ro'yxati — {len(students)} ta (ism va ID)\n📝 {test.title} ({test.code})\n\n"
                   "Har o'quvchi titulga ismini va o'z ID'sini yozib, bo'yaydi.")
        pdf_bytes = generate_student_id_list_pdf(test, students)
    else:
        pdf_bytes = generate_answer_sheet_pdf(test, with_answers=answers, **_sheet_variant_options(db, test, answers, variant))

    _send_pdf_to_telegram(
        user.tg_id,
        f"{test.code}_{'kalit' if answers else 'ismli_varaqlar' if students else 'javoblar_varaqasi'}.pdf",
        pdf_bytes,
        caption=caption,
    )
    return {"ok": True}


def _answer_key_images(db: Session, test: Test, variant: str | None) -> list[bytes]:
    options = _sheet_variant_options(db, test, True, variant)
    return test_answer_key_pngs(test, options.get("key_override"), options.get("key_variant"))


@app.get("/api/teacher/tests/{test_id}/answer-key/image")
def download_answer_key_image(test_id: int, page: int = 1, variant: str | None = None,
                              teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Kalit rasmining bitta sahifasi (PNG) — PDF kalitning o'zi. X-Page-Count — jami sahifalar."""

    test = _get_own_test(db, teacher, test_id)
    images = _answer_key_images(db, test, variant)
    if not 1 <= page <= len(images):
        raise HTTPException(status_code=404, detail="Bunday sahifa yo'q")
    return StreamingResponse(io.BytesIO(images[page - 1]), media_type="image/png", headers={
        "Content-Disposition": f'attachment; filename="{test.code}_kalit_{page}.png"',
        "X-Page-Count": str(len(images)), "Access-Control-Expose-Headers": "X-Page-Count",
    })


@app.post("/api/teacher/tests/{test_id}/answer-key/image/send")
def send_answer_key_image(test_id: int, variant: str | None = None, teacher: Teacher = Depends(get_current_teacher),
                          user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kalit rasmlarini (har sahifa — alohida rasm) bot orqali o'qituvchining chatiga yuboradi."""

    test = _get_own_test(db, teacher, test_id)
    images = _answer_key_images(db, test, variant)
    suffix = f" — {variant} variant" if variant else ""
    _send_photo_album(user.tg_id, f"{test.code}_kalit", images,
                      f"🔑 «{test.title}» ({test.code}) — javoblar kaliti{suffix}\n\n"
                      "Diqqat: to'g'ri javoblar — o'quvchilarga tarqatmang!")
    return {"ok": True, "pages": len(images)}


# ========== 6) O'quvchi endpointlari ==========


@app.get("/api/tests/{code}/subscriptions")
def test_subscriptions(
    code: str, channel: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    O'quvchi testning majburiy kanallariga obunami? channel berilsa — faqat
    o'sha bittasi tekshiriladi (kanallar alohida-alohida tekshiriladi).
    status: "yes" — obuna, "no" — obuna emas, "unknown" — tekshirib bo'lmadi.
    """

    test = _get_test_by_code(db, code, user)
    channels = _required_channels(test)
    if channel:
        channels = [item for item in channels if item.lower() == channel.strip().lower()]
    result = []
    for channel in channels:
        try:
            status = "yes" if _is_subscribed(channel, user) else "no"
        except HTTPException:
            status = "unknown"
        result.append({"channel": channel, "status": status})
    return {"channels": result}


@app.get("/api/tests/{code}")
def get_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi test kodini kiritganda, testning umumiy ma'lumotini qaytaradi."""

    test = _get_test_by_code(db, code, user)
    pending = _pending_card_payment(db, user, test) if test.price > 0 else None

    return {
        "code": test.code,
        "title": test.title,
        "subject": test.subject,
        "count": _question_count(test),
        "duration": test.duration,
        "price": test.price,
        "paid": _is_paid(db, user, test),
        # Karta orqali to'lov: o'qituvchi kartasi va o'quvchining tekshirilayotgan cheki
        # Click/Payme ulangan bo'lsa pul platformaga tushadi — karta ko'rsatilmaydi
        "teacher_card": _teacher_card_view(test.teacher) if test.price > 0 and not _enabled_providers() else None,
        "card_payment_pending": pending is not None,
        "card_payment_since": pending.created_at if pending else None,  # "eslatish" tugmasi uchun
        "enrollment_type": test.enrollment_type,
        "required_channel": test.required_channel,
        "required_channels": _required_channels(test),
        "payment_providers": _enabled_providers(),
        "start_time": test.start_time,
        "end_time": test.end_time,
    }


@app.post("/api/tests/{code}/pay")
def pay_for_test(code: str, provider: str = "payme", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Pullik testga to'lov boshlaydi: Payme to'lov sahifasiga havola qaytaradi.

    To'lov "pending" holatda yaratiladi. O'quvchi haqiqatan pul to'lagandan
    keyin Payme webhook'i (qarang: 7-bo'lim) uni "paid" ga o'tkazadi va
    shundagina test ochiladi, o'qituvchi balansiga ulush qo'shiladi.
    """

    test = _get_test_by_code(db, code, user)
    if _is_paid(db, user, test):
        return {"paid": True}

    if test.price <= 0:
        return {"paid": True}  # bepul test — to'lovning hojati yo'q

    _require_provider(provider)

    # Boshlangan, lekin tugatilmagan to'lov bo'lsa — yangisini yaratmaymiz,
    # o'sha havolani qaytaramiz (aks holda har bosishda yangi buyurtma to'planardi).
    payment = db.scalar(
        select(Payment)
        .where(Payment.user_id == user.id, Payment.test_id == test.id, Payment.status == "pending")
        .order_by(Payment.id.desc())
    )

    # To'lov tizimi almashtirildi, lekin eskisida tranzaksiya allaqachon boshlangan — o'sha buyurtma
    # o'z tizimida qoladi, yangi tizimga alohida buyurtma ochiladi (bitta buyurtmani ikki tizim to'lamasin)
    started_elsewhere = payment is not None and payment.provider != provider and bool(
        payment.payme_transaction_id or payment.click_trans_id)
    if not payment or payment.amount != test.price or started_elsewhere:
        payment = Payment(user_id=user.id, test_id=test.id, amount=test.price, status="pending", provider=provider)
        db.add(payment)
        db.commit()
    elif payment.provider != provider:
        payment.provider = provider
        db.commit()

    return {
        "paid": False,
        "payment_id": payment.id,
        "amount": payment.amount,
        "checkout_url": _checkout_url(provider, payment, payment.amount),
    }


# ========== 6.1) Pullik test: o'quvchi o'qituvchining kartasiga to'laydi ==========
#
# Platforma pulni ushlamaydi va token ishlatilmaydi: o'quvchi pulni o'qituvchining
# kartasiga o'zi o'tkazadi va chek rasmini yuboradi. Chek botda o'qituvchiga
# ✅/❌ tugmalari bilan boradi; o'qituvchi kartasiga pul tushganini ko'rib
# tasdiqlaydi — shundagina o'quvchiga test ochiladi va unga xabar boradi.
# Har bir qadam faqat "pending" holatdagi to'lovni o'zgartiradi (qulf bilan),
# shuning uchun tugma ikki marta bosilsa ham hech narsa ikki marta bo'lmaydi.


def _format_card(number: str) -> str:
    return " ".join(number[index:index + 4] for index in range(0, len(number), 4))


def _can_sell_tests(teacher: Teacher) -> bool:
    """Pullik test: karta raqami kiritilgan (yoki Payme/Click ulangan) bo'lsa."""
    return bool(teacher.card_number and BOT_TOKEN) or bool(_enabled_providers())


def _teacher_card_view(teacher: Teacher | None) -> dict | None:
    if not teacher or not teacher.card_number:
        return None
    return {"number": _format_card(teacher.card_number), "holder": teacher.card_holder or ""}


def _pending_card_payment(db: Session, user: User, test: Test) -> Payment | None:
    return db.scalar(
        select(Payment).where(
            Payment.user_id == user.id,
            Payment.test_id == test.id,
            Payment.provider == "card",
            Payment.status == "pending",
        )
    )


@app.get("/api/teacher/card")
def get_teacher_card(teacher: Teacher = Depends(get_current_teacher)):
    return {"card_number": teacher.card_number or "", "card_holder": teacher.card_holder or "",
            "card_expiry": teacher.card_expiry or ""}


@app.put("/api/teacher/card")
def set_teacher_card(body: TeacherCardIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'quvchilar pullik test uchun pulni shu kartaga o'tkazadi."""
    teacher.card_number = body.card_number
    teacher.card_holder = body.card_holder
    teacher.card_expiry = body.card_expiry
    db.commit()
    logging.getLogger("backend").info("O'qituvchi kartasi yangilandi: teacher=%s", teacher.id)
    return {"card_number": teacher.card_number, "card_holder": teacher.card_holder, "card_expiry": teacher.card_expiry}


def _test_payment_caption(payment: Payment, test: Test, student: User) -> str:
    return (
        "💳 Test uchun to'lov cheki\n\n"
        f"O'quvchi: {student.full_name}"
        + (f" (@{student.username})" if student.username else "")
        + f"\nTest: «{test.title}» (kod: {test.code})\n"
        f"Summa: {_format_sum(payment.amount)} so'm\n\n"
        "Kartangizga shu summa tushganini tekshiring. "
        "\"Ruxsat berish\" bosilsa, shu o'quvchi testga kira oladi."
    )


def _post_receipt_photo(payment: Payment, test: Test, student: User, caption_prefix: str = "",
                        image: bytes | None = None, mime: str = "", extension: str = "") -> str:
    """
    Chekni o'qituvchi chatiga ✅/❌ tugmalari bilan yuboradi va xabarni QADAYDI
    (chat tepasida turadi — e'tibordan chetda qolmasin). Rasm — birinchi marta
    fayl, qayta yuborishda (eslatma) Telegram'dagi file_id. file_id qaytaradi.
    """

    chat_id = test.teacher.user.tg_id
    data = {
        "chat_id": chat_id,
        "caption": (caption_prefix + _test_payment_caption(payment, test, student))[:1024],
        "reply_markup": json.dumps({
            "inline_keyboard": [[
                {"text": "✅ Ruxsat berish", "callback_data": f"tpay_approve:{payment.id}"},
                {"text": "❌ Ruxsat yo'q", "callback_data": f"tpay_reject:{payment.id}"},
            ]]
        }),
    }
    files = None
    if image is not None:
        files = {"photo": (f"chek_{payment.id}.{extension}", image, mime)}
    else:
        data["photo"] = payment.receipt_file_id
    response = httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto", data=data, files=files, timeout=30)
    result = response.json()
    if not result.get("ok"):
        raise RuntimeError(result.get("description") or "Telegram rasmni qabul qilmadi")

    # Eski qadalgan nusxa olinadi, yangisi qadaladi (qadab bo'lmasa ham jarayon to'xtamaydi)
    _telegram_pin(chat_id, payment.receipt_message_id, pin=False)
    payment.receipt_message_id = result["result"]["message_id"]
    _telegram_pin(chat_id, payment.receipt_message_id, pin=True)
    return result["result"]["photo"][-1]["file_id"]


def _telegram_pin(chat_id: int, message_id: int | None, pin: bool) -> None:
    if not message_id:
        return
    try:
        httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/{'pinChatMessage' if pin else 'unpinChatMessage'}",
                   json={"chat_id": chat_id, "message_id": message_id, "disable_notification": False}, timeout=10)
    except Exception:
        logging.getLogger("backend").warning("Xabarni qadab/qadovdan olib bo'lmadi", exc_info=True)


def _send_test_receipt_to_teacher(payment: Payment, test: Test, student: User, image: bytes, mime: str, extension: str) -> str:
    """Chekni o'qituvchining Telegram chatiga ✅/❌ tugmalari bilan yuboradi (qadaladi); file_id qaytaradi."""
    return _post_receipt_photo(payment, test, student, image=image, mime=mime, extension=extension)


def _payments_button() -> dict | None:
    """Eslatmadagi tugma — ilovani to'g'ridan-to'g'ri cheklar sahifasida ochadi."""
    if not WEBAPP_URL.startswith("https://"):
        return None
    separator = "&" if "?" in WEBAPP_URL else "?"
    return {"inline_keyboard": [[{"text": "🧾 Cheklarni ko'rish", "web_app": {"url": f"{WEBAPP_URL}{separator}open=payments"}}]]}


# Eslatma: chek 5 daqiqa javobsiz qolsa, keyin har 30 daqiqada, ko'pi bilan 6 marta —
# o'quvchi pul to'lab, testga kira olmay qolmasin
REMINDER_FIRST_AFTER = timedelta(minutes=5)
REMINDER_REPEAT_AFTER = timedelta(minutes=30)
REMINDER_MAX = 6


def _remind_pending_payments(db: Session) -> int:
    """Fonda: javobsiz qolgan cheklar haqida o'qituvchiga bitta jamlangan eslatma. Yuborilganlar soni."""

    now = utcnow()
    rows = db.execute(
        select(Payment, Test, User)
        .join(Test, Test.id == Payment.test_id)
        .join(User, User.id == Payment.user_id)
        .where(
            Payment.provider == "card",
            Payment.status == "pending",
            Payment.created_at <= now - REMINDER_FIRST_AFTER,
            Payment.reminder_count < REMINDER_MAX,
            or_(Payment.reminded_at.is_(None), Payment.reminded_at <= now - REMINDER_REPEAT_AFTER),
        )
        .order_by(Payment.id)
    ).all()

    by_teacher: dict[int, list] = {}
    for payment, test, student in rows:
        if test.teacher and test.deleted_at is None:
            by_teacher.setdefault(test.teacher.user.tg_id, []).append((payment, test, student))

    sent = 0
    for chat_id, items in by_teacher.items():
        lines = [f"⏳ {len(items)} ta o'quvchi to'lov cheki bilan ruxsat kutmoqda:", ""]
        for payment, test, student in items[:15]:
            minutes = int((now - payment.created_at).total_seconds() // 60)
            waited = f"{minutes // 60} soat {minutes % 60} daq" if minutes >= 60 else f"{minutes} daq"
            lines.append(f"• {student.full_name} — «{test.title}», {_format_sum(payment.amount)} so'm ({waited} oldin)")
        if len(items) > 15:
            lines.append(f"… va yana {len(items) - 15} ta")
        lines += ["", "Kartangizga pul tushganini tekshirib, chekdagi ✅ yoki ❌ ni bosing."]
        body = {"chat_id": chat_id, "text": "\n".join(lines)}
        button = _payments_button()
        if button:
            body["reply_markup"] = button
        try:
            response = httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json=body, timeout=15)
            delivered = response.status_code == 200
        except Exception:
            delivered = False
        if not delivered:
            logging.getLogger("backend").warning("Chek eslatmasi o'qituvchiga yetmadi (chat %s)", chat_id)
            continue
        for payment, _, _ in items:
            payment.reminded_at = now
            payment.reminder_count = (payment.reminder_count or 0) + 1
        sent += 1
    db.commit()
    return sent


def _notify_user(chat_id: int, text: str) -> None:
    """Bot orqali xabar. Yetmasa ham jarayon to'xtamaydi (masalan, botni bloklagan)."""
    try:
        httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage", json={"chat_id": chat_id, "text": text}, timeout=10)
    except Exception:
        logging.getLogger("backend").warning("Foydalanuvchiga xabar yuborilmadi", exc_info=True)


@app.post("/api/tests/{code}/pay-card")
def pay_test_by_card(code: str, body: TestCardPaymentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi o'qituvchi kartasiga pul o'tkazgach, chek rasmini yuboradi."""

    test = _get_test_by_code(db, code, user)
    if test.price <= 0:
        raise HTTPException(status_code=400, detail="Bu test bepul — to'lov kerak emas")
    if _is_paid(db, user, test):
        return {"paid": True}
    if test.enrollment_type == "closed" or test.status == "closed" or (test.end_time and utcnow() > test.end_time):
        raise HTTPException(status_code=403, detail="Bu testga to'lov qabul qilinmaydi (yopiq yoki tugagan)")
    if _enabled_providers():
        raise HTTPException(status_code=409, detail="Bu testga Click yoki Payme orqali to'lang")
    teacher = test.teacher
    if not teacher or not teacher.card_number or not BOT_TOKEN:
        raise HTTPException(status_code=409, detail="O'qituvchi karta raqamini kiritmagan — u bilan bog'laning")

    # Bir vaqtda bitta tekshirilayotgan chek — bir xil chekni qayta-qayta yuborib bo'lmasin
    db.scalar(select(User.id).where(User.id == user.id).with_for_update())
    if _pending_card_payment(db, user, test):
        raise HTTPException(status_code=409, detail="Chekingiz o'qituvchiga yuborilgan — u tasdiqlashini kuting")

    image, mime, extension = _decode_receipt(body.receipt_base64)
    payment = Payment(user_id=user.id, test_id=test.id, amount=test.price, status="pending", provider="card")
    db.add(payment)
    db.commit()

    try:
        payment.receipt_file_id = _send_test_receipt_to_teacher(payment, test, user, image, mime, extension)
        db.commit()
    except Exception:
        # O'qituvchi chekni ko'rmasa, hech qachon tasdiqlanmaydi — so'rov iz sifatida qoladi
        logging.getLogger("backend").warning("Test cheki o'qituvchiga yuborilmadi", exc_info=True)
        payment.status = "failed"
        db.commit()
        raise HTTPException(
            status_code=502,
            detail="Chek o'qituvchiga yetmadi (u botni ishga tushirmagan bo'lishi mumkin). Biroz keyin qayta urinib ko'ring",
        )

    logging.getLogger("backend").info("Test cheki yuborildi: payment=%s test=%s user=%s", payment.id, test.id, user.id)
    return {"paid": False, "pending": True, "payment_id": payment.id}


STUDENT_REMIND_AFTER = timedelta(minutes=10)
STUDENT_REMIND_EVERY = timedelta(minutes=30)


@app.post("/api/tests/{code}/pay-card/remind")
def remind_teacher_about_payment(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    O'quvchi chekini o'qituvchiga QAYTA yuboradi (chat oxiriga, qadalgan holda) —
    o'qituvchi uzoq javob bermasa. Chek yuborilgach 10 daqiqadan keyin, 30 daqiqada bir marta.
    """

    test = _get_test_by_code(db, code, user)
    payment = _pending_card_payment(db, user, test)
    if not payment or not payment.receipt_file_id:
        raise HTTPException(status_code=404, detail="Tekshirilayotgan chek topilmadi")
    now = utcnow()
    if now - payment.created_at < STUDENT_REMIND_AFTER:
        minutes = int((STUDENT_REMIND_AFTER - (now - payment.created_at)).total_seconds() // 60) + 1
        raise HTTPException(status_code=429, detail=f"O'qituvchi chekni hali ko'rmagan bo'lishi mumkin — {minutes} daqiqadan keyin eslating")
    if payment.reminded_at and now - payment.reminded_at < STUDENT_REMIND_EVERY:
        raise HTTPException(status_code=429, detail="Yaqinda eslatildi — biroz kuting")
    try:
        _post_receipt_photo(payment, test, user, caption_prefix="🔔 O'quvchi eslatmoqda!\n\n")
    except Exception:
        logging.getLogger("backend").warning("Eslatma chek o'qituvchiga yuborilmadi", exc_info=True)
        raise HTTPException(status_code=502, detail="Eslatma yetmadi, biroz keyin qayta urinib ko'ring")
    payment.reminded_at = now
    payment.reminder_count = (payment.reminder_count or 0) + 1
    db.commit()
    return {"ok": True}


def decide_test_payment(db: Session, payment_id: int, approve: bool, teacher_tg_id: int) -> tuple[bool, str]:
    """
    O'qituvchi o'quvchining chekini tasdiqlaydi yoki rad etadi (bot tugmasi
    yoki ilova). Faqat o'sha testning o'qituvchisi va faqat "pending" to'lov.
    (muvaffaqiyatli_mi, o'qituvchiga ko'rsatiladigan matn) qaytaradi.
    """

    payment = db.scalar(select(Payment).where(Payment.id == payment_id).with_for_update())
    if not payment or payment.provider != "card":
        return False, "To'lov topilmadi"
    test = db.get(Test, payment.test_id)
    if not test or not test.teacher or test.teacher.user.tg_id != teacher_tg_id:
        return False, "Bu to'lov sizning testingizga tegishli emas"
    if payment.status != "pending":
        return False, "Bu to'lov allaqachon ko'rib chiqilgan"

    student = db.get(User, payment.user_id)
    amount_text = _format_sum(payment.amount)
    _telegram_pin(test.teacher.user.tg_id, payment.receipt_message_id, pin=False)
    if approve:
        payment.status = "paid"
        payment.performed_at = utcnow()
        db.commit()
        _notify_user(student.tg_id, f"✅ «{test.title}» testi uchun to'lovingiz tasdiqlandi!\n\n"
                                    f"Endi testni boshlashingiz mumkin. Test kodi: {test.code}")
        logging.getLogger("backend").info("Test to'lovi tasdiqlandi: payment=%s", payment.id)
        return True, f"✅ Ruxsat berildi: {student.full_name} — «{test.title}», {amount_text} so'm. U testga kira oladi."

    payment.status = "cancelled"
    payment.cancelled_at = utcnow()
    db.commit()
    _notify_user(student.tg_id, f"❌ «{test.title}» testi uchun {amount_text} so'mlik to'lovingiz tasdiqlanmadi.\n\n"
                                "Chek noto'g'ri yoki kartaga pul tushmagan bo'lishi mumkin. "
                                "Savol bo'lsa, o'qituvchingizga yozing. Qayta chek yuborishingiz mumkin.")
    logging.getLogger("backend").info("Test to'lovi rad etildi: payment=%s", payment.id)
    return True, f"❌ Ruxsat berilmadi: {student.full_name} — «{test.title}», {amount_text} so'm"


@app.get("/api/teacher/test-payments")
def list_test_payments(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchi testlariga karta orqali qilingan to'lovlar: tasdiq kutayotganlar va oxirgi 50 tasi."""

    rows = db.execute(
        select(Payment, Test, User)
        .join(Test, Test.id == Payment.test_id)
        .join(User, User.id == Payment.user_id)
        .where(Test.teacher_id == teacher.id, Payment.provider == "card", Payment.status.in_(("pending", "paid", "cancelled")))
        .order_by((Payment.status == "pending").desc(), Payment.id.desc())
        .limit(200)
    ).all()
    items = [
        {
            "id": payment.id,
            "status": payment.status,
            "amount": payment.amount,
            "created_at": payment.created_at,
            "test_title": test.title,
            "test_code": test.code,
            "student": student.full_name,
            "username": student.username,
            "photo_url": student.photo_url,
            "has_receipt": bool(payment.receipt_file_id),
        }
        for payment, test, student in rows
    ]
    pending = [item for item in items if item["status"] == "pending"]
    return {"pending": pending, "recent": [item for item in items if item["status"] != "pending"][:50]}


def _own_test_payment(db: Session, teacher: Teacher, payment_id: int) -> Payment:
    payment = db.get(Payment, payment_id)
    test = db.get(Test, payment.test_id) if payment else None
    if not payment or payment.provider != "card" or not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="To'lov topilmadi")
    return payment


@app.get("/api/teacher/test-payments/{payment_id}/receipt")
def test_payment_receipt(payment_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Chek rasmi — Telegram'dan file_id orqali olinadi (serverda saqlanmaydi)."""

    payment = _own_test_payment(db, teacher, payment_id)
    if not payment.receipt_file_id:
        raise HTTPException(status_code=404, detail="Chek topilmadi")
    try:
        file_info = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
                              params={"file_id": payment.receipt_file_id}, timeout=15).json()
        image = httpx.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info['result']['file_path']}", timeout=30)
        image.raise_for_status()
    except Exception:
        logging.getLogger("backend").warning("Test cheki Telegram'dan olinmadi", exc_info=True)
        raise HTTPException(status_code=502, detail="Chek rasmini olib bo'lmadi")
    return StreamingResponse(io.BytesIO(image.content), media_type=image.headers.get("content-type", "image/jpeg"),
                             headers={"Cache-Control": "private, no-store"})


@app.post("/api/teacher/test-payments/{payment_id}/approve")
def approve_test_payment(payment_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    _own_test_payment(db, teacher, payment_id)
    ok, message = decide_test_payment(db, payment_id, approve=True, teacher_tg_id=teacher.user.tg_id)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/teacher/test-payments/{payment_id}/reject")
def reject_test_payment(payment_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    _own_test_payment(db, teacher, payment_id)
    ok, message = decide_test_payment(db, payment_id, approve=False, teacher_tg_id=teacher.user.tg_id)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/tests/{code}/start")
def start_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Testni boshlaydi yoki tugamagan urinishni davom ettiradi.

    Har bir o'quvchiga BITTA urinish beriladi. Ilgari tugatilgan urinish
    e'tiborga olinmasdi va har bosishda yangisi ochilardi — o'quvchi javoblarni
    yodlab, 100% olguncha qayta-qayta ishlab, reytingni ma'nosiz qilib qo'yardi
    (va har urinish o'qituvchining tokenini yeyardi).

    To'g'ri javoblar hech qachon yuborilmaydi — aks holda o'quvchi ularni
    brauzerning so'rovlar oynasidan ko'rib olishi mumkin.
    """

    test = _get_test_by_code(db, code, user)
    if not _is_paid(db, user, test):
        raise HTTPException(status_code=402, detail="Avval to'lov qiling")
    _check_enrollment(test)

    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id == user.id)
        .order_by(Attempt.id.desc())
    )

    # Testni allaqachon ishlagan o'quvchiga obuna ekrani emas, aniq xabar chiqsin
    if attempt and attempt.status == "done":
        raise HTTPException(
            status_code=409,
            detail="Siz bu testni allaqachon ishlab bo'lgansiz. Natijangiz o'qituvchingizga yuborilgan.",
        )

    _check_channel_subscription(test, user)

    # Kalitsiz saqlangan onlayn test: o'quvchi boshlasa, natijasi noto'g'ri (0) chiqardi — kalit to'liq kiritilguncha kutadi
    if not (attempt and attempt.status == "in_progress") and _key_missing(test):
        raise HTTPException(
            status_code=409,
            detail="Test hali tayyor emas — o'qituvchi javoblar kalitini to'liq kiritmagan. Birozdan keyin urinib ko'ring.",
        )

    # Vaqti tugab qolgan urinish: o'quvchi belgilab qo'ygan javoblar bo'yicha
    # darhol tekshirib, natijasini qotiramiz — mehnati yo'qolmasligi uchun.
    if attempt and attempt.status == "in_progress":
        if (utcnow() - attempt.started_at).total_seconds() >= test.duration * 60:
            # Fon vazifasi shu payt uni yakunlayotgan bo'lishi mumkin — qulflab, qayta tekshiramiz
            db.refresh(attempt, with_for_update=True)
            if attempt.status == "in_progress":
                total_questions = _grade_attempt_from_saved_answers(db, attempt, test)
                notice = _result_notice(db, attempt, test, total_questions)
                db.commit()
                _send_result_notice_in_background(notice)

    # Natija (foiz) o'quvchiga aytilmaydi — uni faqat o'qituvchi ko'radi
    if attempt and attempt.status == "done":
        raise HTTPException(
            status_code=409,
            detail="Siz bu testni allaqachon ishlab bo'lgansiz. Natijangiz o'qituvchingizga yuborilgan.",
        )

    if not attempt:
        # Bepul testda har bir natija o'qituvchidan 1 token oladi —
        # tokeni bo'lmasa, yangi urinish boshlanmaydi.
        if _charges_tokens(test, test.teacher) and test.teacher.credits <= 0:
            raise HTTPException(
                status_code=402,
                detail="O'qituvchining tokeni tugagan, hozircha bu testni boshlab bo'lmaydi",
            )

        attempt = Attempt(test_id=test.id, user_id=user.id)
        db.add(attempt)
        db.commit()

    remaining_seconds = max(0, int(test.duration * 60 - (utcnow() - attempt.started_at).total_seconds()))

    if test.mode == "key":
        # Savol matni yuborilmaydi — faqat raqami, turi va nechta variant borligi.
        # a/b qismli yozma savol o'quvchiga bitta savol bo'lib ko'rinadi.
        questions_payload = []
        for index, group in enumerate(_group_key_questions(test), start=1):
            first = group[0]
            questions_payload.append(
                {
                    "number": index,
                    "question_type": first.question_type,
                    "option_count": first.option_count,
                    "parts": [
                        # "a", "b" — yoki yozma ish bandida imtihondagidek "1", "2" ("43.1" -> "1")
                        {"ref": kq.number, "label": re.sub(r"^\d+\.?", "", band_label(test.subject, kq.label) or "")}
                        for kq in group
                    ],
                }
            )
    else:
        # To'g'ri javob (correct) yuborilmaydi — aks holda o'quvchi brauzer orqali ko'rib olishi mumkin.
        questions_payload = [
            {"id": q.id, "text": q.text, "a": q.a, "b": q.b, "c": q.c, "d": q.d} for q in test.questions
        ]

    return {
        "attempt_id": attempt.id,
        "mode": test.mode,
        "keyboard": subject_keyboard(test.subject),  # "math" (√, kasr) yoki "text" (oddiy harflar)
        "duration": test.duration,
        "remaining": remaining_seconds,
        "questions": questions_payload,
        # Ilgari belgilangan javoblar — Mini App yopilib qayta ochilsa,
        # o'quvchi to'xtagan joyidan davom etadi.
        "answers": _read_saved_answers(attempt),
        # Esse: o'quvchi ilovada yozadi (javoblarda "essay" kaliti), o'qituvchi yoki AI baholaydi
        # photos — qog'ozdagi esse rasmlari yuklangan sahifalar ([1, 2]); qayta ochilganda ham ko'rinadi
        # ai — esse rasmi matnga aylantiriladimi: faqat "AI orqali tekshirish" testida (AI matndan baholaydi);
        # o'qituvchi tekshiradigan testda rasm o'zgarmay o'qituvchiga boradi
        "essay": {"number": _essay_number(test), "topic": test.essay_topic, "photos": _essay_photo_pages(db, attempt.id),
                  "ai": bool(test.essay_ai) and _ai_on()} if test.essay_max else None,
    }


def _get_own_attempt(db: Session, user: User, attempt_id: int) -> Attempt:
    """Urinish aynan shu o'quvchiga tegishli ekanini tekshiradi."""

    # Qulflanadi: "YAKUNLASH" ikki marta bosilsa, ikkinchi so'rov birinchisi
    # tugashini kutadi va urinishni allaqachon yakunlangan holda ko'radi —
    # o'qituvchidan token ikki marta yechilmaydi.
    attempt = db.get(Attempt, attempt_id, with_for_update=True)
    if not attempt or attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    return attempt


@app.post("/api/attempts/{attempt_id}/answers")
def save_attempt_progress(
    attempt_id: int, body: FinishIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    O'quvchi belgilagan javoblarni yakunlashni kutmasdan saqlab qo'yadi.

    Telegram Mini App juda oson yopiladi (pastga surish, qo'ng'iroq kelishi) —
    ilgari bunda o'quvchining 45 ta savolga bergan javobi butunlay yo'qolardi,
    chunki javoblar faqat yakunlashda yuborilardi. Endi har savoldan keyin shu
    yerga yuboriladi va qaytib kelganda joyidan davom etadi.
    """

    attempt = _get_own_attempt(db, user, attempt_id)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="Test allaqachon yakunlangan")

    # Javoblar o'lchami cheklanadi — buzilgan yoki ataylab kattalashtirilgan
    # so'rov bazani to'ldirib qo'ymasligi uchun.
    cleaned = {
        str(ref)[:20]: str(value)[:ESSAY_MAX_CHARS if ref == ESSAY_KEY else 300]
        for ref, value in list(body.answers.items())[:300]
    }
    attempt.saved_answers = json.dumps(cleaned, ensure_ascii=False)
    db.commit()

    return {"saved": len(cleaned)}


# ---------- Qog'ozga yozilgan esse rasmi ----------
# Ayrim o'quvchilar esseni qo'lda varaqqa yozadi: test ichida rasmini yuklaydi (har sahifa alohida).
# AI rasmni matnga aylantiradi (xatolari bilan — imlo ham baholanadi), o'quvchi matnni rasm bilan
# solishtirib tuzatadi va odatdagidek topshiradi. O'qituvchi esse matni yonida asl rasmni ko'radi.

ESSAY_PHOTO_PAGES = 4  # bitta essega ko'pi bilan shuncha sahifa
ESSAY_PHOTO_LIMIT = 2_500_000  # bitta sahifa rasmi (base64 matni) — ko'pi bilan (~1.8 MB JPEG)


class EssayPhotoIn(BaseModel):
    image: str = Field(min_length=30, max_length=ESSAY_PHOTO_LIMIT)
    page: int = Field(ge=1, le=ESSAY_PHOTO_PAGES)


def _ai_on() -> bool:
    from .ai_reader import ai_enabled

    return ai_enabled()


def _essay_photo_pages(db: Session, attempt_id: int) -> list[int]:
    return list(db.scalars(select(EssayImage.page).where(EssayImage.attempt_id == attempt_id)
                           .order_by(EssayImage.page)).all())


def _own_essay_attempt(db: Session, user: User, attempt_id: int) -> tuple[Attempt, Test]:
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="Test allaqachon yakunlangan")
    test = db.get(Test, attempt.test_id)
    if not test or not test.essay_max:
        raise HTTPException(status_code=400, detail="Bu testda esse yo'q")
    return attempt, test


@app.post("/api/attempts/{attempt_id}/essay/photo")
def upload_essay_photo(
    attempt_id: int, body: EssayPhotoIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    O'quvchi qog'ozga yozgan essesining bitta sahifasi rasmini yuklaydi. Rasm saqlanadi (o'qituvchi ko'radi).
    "AI orqali tekshirish" testida (va AI ulangan bo'lsa) sahifa matnga aylantiriladi va qaytariladi — o'quvchi
    tekshirib, maydonga qo'shadi; AI esseni shu matndan baholaydi. Esseni o'qituvchi tekshiradigan testda rasm
    AI'ga yuborilmaydi — o'qituvchi qo'lyozmaning o'zini o'qib baholaydi.
    """

    attempt, test = _own_essay_attempt(db, user, attempt_id)
    if not re.match(r"^data:image/(jpeg|png|webp);base64,", body.image):
        raise HTTPException(status_code=422, detail="Rasm formati noto'g'ri (JPEG yoki PNG yuklang)")
    db.execute(delete(EssayImage).where(EssayImage.attempt_id == attempt.id, EssayImage.page == body.page))
    db.add(EssayImage(attempt_id=attempt.id, page=body.page, image=body.image))
    db.commit()  # AI kutilayotganda urinish qulflanib turmasin — rasm avval saqlanadi

    from .ai_reader import AIReadError, ai_enabled, transcribe_essay

    use_ai = bool(test.essay_ai) and ai_enabled()
    text, error = "", None
    if use_ai:
        try:
            text = transcribe_essay(test.subject, body.image)
        except AIReadError as failure:
            error = str(failure)
            logging.getLogger("backend.ai").warning("Esse rasmini o'qib bo'lmadi (urinish %s): %s", attempt.id, failure)
    return {"page": body.page, "pages": _essay_photo_pages(db, attempt.id), "text": text, "ai": use_ai,
            "error": error}


@app.delete("/api/attempts/{attempt_id}/essay/photo/{page}")
def delete_essay_photo(attempt_id: int, page: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    attempt, _ = _own_essay_attempt(db, user, attempt_id)
    db.execute(delete(EssayImage).where(EssayImage.attempt_id == attempt.id, EssayImage.page == page))
    db.commit()
    return {"pages": _essay_photo_pages(db, attempt.id)}


@app.get("/api/teacher/tests/{test_id}/students/{attempt_id}/essay/photos")
def student_essay_photos(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi: o'quvchi qog'ozga yozib yuklagan esse rasmlari (matnni asl qo'lyozma bilan solishtirish uchun)."""

    test = _get_own_test(db, teacher, test_id)
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    pages = db.scalars(select(EssayImage).where(EssayImage.attempt_id == attempt.id).order_by(EssayImage.page)).all()
    if not pages:
        raise HTTPException(status_code=404, detail="Esse rasmi yuklanmagan")
    return {"pages": [{"page": item.page, "image": item.image} for item in pages]}


@app.get("/api/teacher/tests/{test_id}/students/{attempt_id}/sheet/photos")
def student_sheet_photos(
    test_id: int, attempt_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi: Milliy sertifikat titulidan skanerlangan yozma ish yechimi betlari (Kimyo/Biologiya 41–43)."""

    test = _get_own_test(db, teacher, test_id)
    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    pages = db.scalars(select(SheetImage).where(SheetImage.attempt_id == attempt.id).order_by(SheetImage.page)).all()
    if not pages:
        raise HTTPException(status_code=404, detail="Yechim rasmi yo'q")
    return {"pages": [{"page": item.page, "image": item.image} for item in pages]}


@app.post("/api/attempts/{attempt_id}/finish")
def finish_attempt(
    attempt_id: int, body: FinishIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    O'quvchi javoblarini qabul qiladi va avtomatik tekshiradi.

    Natija o'quvchiga QAYTARILMAYDI (pullik va bepul testda ham) — u bot
    orqali o'qituvchiga yuboriladi va o'qituvchining natijalar bo'limida
    test o'chirilmaguncha saqlanib turadi.
    """

    attempt = _get_own_attempt(db, user, attempt_id)
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="Test allaqachon yakunlangan")

    test = db.get(Test, attempt.test_id)
    if not test:
        raise HTTPException(status_code=404, detail="Test topilmadi")

    # Saqlangan qoralama ham hisobga olinadi: aloqa uzilib, oxirgi so'rov
    # yetib kelmagan bo'lsa ham, avval belgilangan javoblar yo'qolmaydi.
    answers = {**_read_saved_answers(attempt), **body.answers}
    total_questions = _finalize_attempt(db, attempt, test, answers)
    notice = _result_notice(db, attempt, test, total_questions)
    db.commit()

    _send_result_notice_in_background(notice)

    return {"ok": True, "name": user.full_name, "test_title": test.title}


@app.get("/api/tests/{code}/rating")
def test_rating(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Shu testning top-50 reytingi (eng yaxshi natijalar).

    Faqat testning o'qituvchisi (va admin) ko'ra oladi: natijalar o'quvchilarga
    ko'rsatilmaydi — boshqalarning foizlari ham.
    """

    test = _get_test_by_code(db, code, user)
    is_owner = test.teacher is not None and test.teacher.user_id == user.id
    is_admin = is_admin_tg(user.tg_id)
    if not (is_owner or is_admin):
        raise HTTPException(status_code=403, detail="Natijalarni faqat o'qituvchi ko'radi")

    return [{"name": name, "percent": attempt.percent} for attempt, name in best_attempts(db, test)[:50]]


# ========== 6.5) Xususiy maktablar ==========
#
# O'qituvchi viloyat va tumanni tanlab maktab yaratadi (u maktab egasi bo'ladi)
# yoki maktab kodi bilan mavjud maktabga qo'shiladi. O'qituvchi bir vaqtda faqat
# bitta maktabda bo'ladi va u yerda doimiy qoladi — chiqish uchun ham maktab
# kodini kiritish kerak (tasodifan yoki begona odam chiqarib yubormasligi uchun).

# 8 belgi (31 xil belgidan) — ~850 milliard xil kod: taxmin qilib topib bo'lmaydi.
# Avval ochilgan 6 belgili kodlar ham ishlayveradi.
SCHOOL_CODE_LENGTH = 8

# Kodni taxmin qilib topishdan himoya: bir foydalanuvchi 15 daqiqada ko'pi
# bilan 5 marta noto'g'ri kod kirita oladi, keyin vaqtincha to'xtatiladi.
SCHOOL_CODE_MAX_FAILURES = 5
SCHOOL_CODE_WINDOW_SECONDS = 15 * 60
_school_code_failures: dict[int, list[float]] = {}


def _check_code_attempts(user: User) -> None:
    now = time.monotonic()
    recent = [moment for moment in _school_code_failures.get(user.id, []) if now - moment < SCHOOL_CODE_WINDOW_SECONDS]
    _school_code_failures[user.id] = recent
    if len(recent) >= SCHOOL_CODE_MAX_FAILURES:
        minutes = int((SCHOOL_CODE_WINDOW_SECONDS - (now - recent[0])) // 60) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Kod juda ko'p marta noto'g'ri kiritildi. {minutes} daqiqadan keyin qayta urinib ko'ring.",
        )


def _record_code_failure(user: User) -> None:
    _school_code_failures.setdefault(user.id, []).append(time.monotonic())


def _active_membership(db: Session, user: User, kind: str | None = None) -> SchoolMember | None:
    """Foydalanuvchining shu turdagi muassasadagi a'zoligi (tur berilmasa — so'rov sarlavhasidagi bo'lim)."""
    return db.scalar(
        select(SchoolMember)
        .join(PrivateSchool, PrivateSchool.id == SchoolMember.school_id)
        .where(
            SchoolMember.user_id == user.id,
            SchoolMember.left_at.is_(None),
            PrivateSchool.kind == (kind or _request_inst_kind.get()),
            PrivateSchool.deleted_at.is_(None),
        )
        .order_by(SchoolMember.id.desc())
    )


def _all_memberships(db: Session, user: User) -> list[SchoolMember]:
    """Barcha muassasalardagi faol a'zoliklar (maktab, o'quv markazi, to'garak) — test markazi uchun."""
    return db.scalars(
        select(SchoolMember)
        .where(SchoolMember.user_id == user.id, SchoolMember.left_at.is_(None))
        .order_by(SchoolMember.id)
    ).all()


def _member_in_school(db: Session, user: User, school_id: int) -> SchoolMember | None:
    return db.scalar(
        select(SchoolMember).where(
            SchoolMember.school_id == school_id, SchoolMember.user_id == user.id, SchoolMember.left_at.is_(None)
        )
    )


# Xabarlarda muassasa nomi turiga qarab: (nomi, qaratqich "...ga", bo'lim nomi)
KIND_WORDS = {
    "school": {"noun": "maktab", "to": "maktabga", "section": "Xususiy maktab", "group": "sinf"},
    "course": {"noun": "o'quv markazi", "to": "o'quv markaziga", "section": "O'quv kurslari", "group": "guruh"},
    "club": {"noun": "to'garak markazi", "to": "to'garak markaziga", "section": "To'garaklar", "group": "guruh"},
}


def _kind_words(kind: str | None = None) -> dict:
    return KIND_WORDS.get(kind or _request_inst_kind.get(), KIND_WORDS["school"])


def _new_school_code(db: Session) -> str:
    while True:
        # secrets — maktab kodi maxfiy kalit vazifasini bajaradi, uni oldindan hisoblab bo'lmasin
        code = "".join(secrets.choice(TEST_CODE_ALPHABET) for _ in range(SCHOOL_CODE_LENGTH))
        if not db.scalar(select(PrivateSchool.id).where(PrivateSchool.code == code)):
            return code


def _read_subjects(member: SchoolMember) -> list[str]:
    try:
        subjects = json.loads(member.subjects or "[]")
    except (TypeError, ValueError):
        return []
    return [str(subject) for subject in subjects] if isinstance(subjects, list) else []


def _require_profile_name(user: User) -> None:
    if len((user.full_name or "").strip()) < 3:
        raise HTTPException(status_code=422, detail="Avval ism-familiyangizni kiriting")


def _require_subjects(subjects: list[str]) -> None:
    """O'qituvchi profilida qaysi fandan dars berishi majburiy."""
    if not subjects:
        raise HTTPException(status_code=422, detail="Qaysi fandan dars berishingizni tanlang")


_bot_username_cache: str | None = None
_bot_username_checked_at = -1e9


def _bot_username() -> str | None:
    """Botning @username'i (taklif havolasi uchun). Bir marta so'raladi va eslab qolinadi."""

    global _bot_username_cache, _bot_username_checked_at
    if _bot_username_cache or not BOT_TOKEN:
        return _bot_username_cache
    # Muvaffaqiyatsiz bo'lsa, har sahifada qayta so'rab (10 s gacha) kuttirmaslik uchun
    if time.monotonic() - _bot_username_checked_at < 10 * 60:
        return None
    _bot_username_checked_at = time.monotonic()
    try:
        data = httpx.get(f"https://api.telegram.org/bot{BOT_TOKEN}/getMe", timeout=10).json()
        _bot_username_cache = (data.get("result") or {}).get("username") or None
    except Exception:
        logging.getLogger("backend").warning("Bot username'ini olib bo'lmadi", exc_info=True)
    return _bot_username_cache


def school_invite_link(code: str) -> str | None:
    """
    Guruhlarga ulashiladigan havola: bosilganda bot maktab nomi va kodni yozib,
    "Maktabga qo'shilish" tugmasini beradi (kod Mini App'da tayyor turadi).
    """
    username = _bot_username()
    return f"https://t.me/{username}?start=school_{code}" if username else None


def _school_view(db: Session, member: SchoolMember, user: User) -> dict:
    school = member.school
    members = [item for item in school.members if item.left_at is None]
    leader = _is_director(member)
    # Kim qaysi sinfning kuratori — xodim ma'lumotida ko'rsatish uchun
    curator_of: dict[int, list[str]] = {}
    for class_name, curator_id in db.execute(
        select(SchoolClass.name, SchoolClass.curator_user_id).where(
            SchoolClass.school_id == school.id, SchoolClass.deleted_at.is_(None), SchoolClass.curator_user_id.is_not(None)
        )
    ):
        curator_of.setdefault(curator_id, []).append(class_name)
    # Avval egasi, keyin qo'shilish tartibida
    # Avval admin, keyin direktor, zam direktor, so'ng qo'shilish tartibida
    rank = {"director": 1, "deputy": 2, "accountant": 3, "commandant": 4}
    members.sort(key=lambda item: (0 if item.role == "owner" else rank.get(item.position, 5), item.joined_at, item.id))

    return {
        "id": school.id,
        "name": school.name,
        "kind": school.kind or "school",
        "region": school.region,
        "district": school.district,
        # Kod faqat adminda: o'qituvchilarni maktabga faqat admin qo'shadi
        "code": school.code if member.role == "owner" else None,
        # ✓ Tasdiq belgisi: rahbariyat sotib olishi mumkin bo'lsa — narxi (aks holda 0: bo'sh belgi ko'rinmaydi)
        "verified": school.verified_at is not None,
        "verify_price": (_verify_price(db) if leader and school.verified_at is None
                         and "payme" in _enabled_providers() else 0),
        "created_at": school.created_at,
        # Admin o'chirishga qo'ygan bo'lsa — qachon o'chishi (hamma xodimga ogohlantirish ko'rinadi)
        "delete_at": _school_delete_at(school),
        "my_role": member.role,
        "my_position": member.position,
        "my_phone": member.phone,
        "my_birth_date": member.birth_date,
        # Xodimlarning telefon raqamlarini faqat rahbariyat ko'radi
        "can_see_phones": _is_director(member),
        "is_leader": _is_director(member),
        "can_create_classes": _can_delete_classes(member),
        "my_subjects": _read_subjects(member),
        "invite_link": school_invite_link(school.code) if member.role == "owner" else None,
        # Oylik to'lov — summani admin/direktor belgilaydi; ko'radi rahbariyat va kuratorlar (o'z sinfi)
        "monthly_fee": school.monthly_fee if _sees_all_finance(member) or db.scalar(
            select(SchoolClass.id).where(SchoolClass.school_id == school.id, SchoolClass.deleted_at.is_(None),
                                         SchoolClass.curator_user_id == user.id).limit(1)) else None,
        "can_see_all_payments": _sees_all_finance(member),
        # Yotoqxona: rahbariyat va tarbiyachilar ochadi (to'garakda yo'q)
        "dorm": (school.kind or "school") != "club" and (
            _can_manage_dorm(member)
            or bool(db.scalar(select(DormRoom.id).where(DormRoom.school_id == school.id, DormRoom.deleted_at.is_(None),
                                                       DormRoom.warden_user_id == user.id)))
        ),
        "can_set_fee": _can_set_fees(member),
        "can_remove_members": _can_delete_classes(member),
        "members": [
            {
                "user_id": item.user_id,
                "name": item.user.full_name if item.user else "Noma'lum",
                # Boshqa xodimlarning ma'lumotlari — faqat rahbariyatga
                "subjects": _read_subjects(item) if (leader or item.user_id == user.id) else [],
                "role": item.role,
                "position": item.position,
                # Shaxsiy ma'lumotlar — faqat rahbariyatga (va xodimning o'ziga)
                "phone": item.phone if (leader or item.user_id == user.id) else None,
                "birth_date": item.birth_date if (leader or item.user_id == user.id) else None,
                "photo_url": item.user.photo_url if item.user else None,
                "username": item.user.username if item.user else None,
                "curator_of": curator_of.get(item.user_id, []) if (leader or item.user_id == user.id) else [],
                "joined_at": item.joined_at,
                "is_me": item.user_id == user.id,
            }
            for item in members
        ],
    }


@app.get("/api/regions")
def list_regions():
    """O'zbekiston viloyatlari va ularning tumanlari/shaharlari."""
    return [{"name": name, "districts": districts} for name, districts in REGIONS.items()]


def _founder_contact(db: Session, school: PrivateSchool) -> dict:
    """Maktab asoschisi va unga Telegram'da yozish havolasi (kod so'rash uchun)."""

    founder = db.get(User, school.owner_user_id)
    if not founder:
        return {"name": "", "username": None, "link": None}
    link = f"https://t.me/{founder.username}" if founder.username else f"tg://user?id={founder.tg_id}"
    return {"name": founder.full_name, "username": founder.username, "link": link}


@app.get("/api/schools")
def list_schools(
    region: str,
    district: str | None = None,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Viloyatdagi (yoki tumandagi) ochilgan maktablar — o'qituvchi o'z maktabini
    topib, asoschidan kod so'rab qo'shilishi uchun. Kod bu yerda BERILMAYDI.
    """

    if region not in REGIONS or (district and not is_valid_location(region, district)):
        raise HTTPException(status_code=422, detail="Viloyat yoki tuman noto'g'ri tanlangan")

    query = select(PrivateSchool).where(PrivateSchool.region == region, PrivateSchool.kind == _request_inst_kind.get(),
                                        PrivateSchool.deleted_at.is_(None))
    if district:
        query = query.where(PrivateSchool.district == district)
    # Admin tasdiqlagan (haqiqiy) muassasalar — tepada, o'qituvchi o'zinikini oson topsin
    schools = db.scalars(query.order_by(PrivateSchool.verified_at.is_(None), PrivateSchool.district,
                                        PrivateSchool.name)).all()

    return [
        {
            "id": school.id,
            "name": school.name,
            "region": school.region,
            "district": school.district,
            "verified": school.verified_at is not None,
            # O'qituvchi/o'quvchi soni berilmaydi — ro'yxatda faqat nomi ko'rinadi
            "founder": _founder_contact(db, school),
            "is_mine": school.owner_user_id == user.id,
        }
        for school in schools
    ]


# Bir foydalanuvchi bitta maktabdan 10 daqiqada bir martadan ko'p kod so'ray
# olmaydi — asoschini xabarlar bilan bezor qilmaslik uchun.
CODE_REQUEST_INTERVAL_SECONDS = 10 * 60
_code_requests: dict[tuple[int, int], float] = {}


def _send_bot_text(chat_id: int, text: str) -> bool:
    if not BOT_TOKEN:
        return False
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text, "disable_web_page_preview": True},
            timeout=10,
        )
        return bool(response.json().get("ok"))
    except Exception:
        logging.getLogger("backend").warning("Bot xabari yuborilmadi", exc_info=True)
        return False


def _rejoin_as_director(db: Session, user: User, school: PrivateSchool) -> SchoolMember:
    """
    Maktab admini (egasi) a'zolikdan chiqib qolgan bo'lsa, kod so'ramasdan
    o'z maktabiga DIREKTOR sifatida qaytadi. Avvalgi fanlari tiklanadi.
    """

    previous = db.scalar(
        select(SchoolMember)
        .where(SchoolMember.school_id == school.id, SchoolMember.user_id == user.id)
        .order_by(SchoolMember.id.desc())
    )
    member = SchoolMember(
        school_id=school.id, user_id=user.id, role="owner", subjects=previous.subjects if previous else "[]"
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@app.post("/api/schools/{school_id}/return")
def return_to_own_school(school_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin o'z maktabiga kodsiz qaytadi."""

    school = db.get(PrivateSchool, school_id)
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Maktab topilmadi")
    if school.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Bu maktab sizniki emas — kirish uchun maktab kodi kerak")
    if _active_membership(db, user, school.kind):
        raise HTTPException(status_code=409, detail=f"Siz allaqachon {_kind_words(school.kind)['to']} a'zosiz")

    member = _rejoin_as_director(db, user, school)
    return {"school": _school_view(db, member, user)}


@app.post("/api/schools/{school_id}/request-code")
def request_school_code(school_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Maktab kodini asoschidan so'rash. Bot ikkala tomonga ham tushuntiradi:
      • asoschiga — kim so'rayapti, kodni (faqat o'z o'qituvchisiga) berish;
      • so'rovchiga — kodni olgach nima qilish kerak.
    Kodning o'zi so'rovchiga hech qachon to'g'ridan-to'g'ri yuborilmaydi.
    """

    _require_profile_name(user)
    school = db.get(PrivateSchool, school_id)
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Maktab topilmadi")
    if school.owner_user_id == user.id:
        raise HTTPException(status_code=400, detail="Bu sizning maktabingiz — kod kerak emas, «Maktabimga kirish»ni bosing")

    key = (user.id, school.id)
    now = time.monotonic()
    if now - _code_requests.get(key, -CODE_REQUEST_INTERVAL_SECONDS) < CODE_REQUEST_INTERVAL_SECONDS:
        raise HTTPException(
            status_code=429,
            detail="So'rov allaqachon yuborilgan. Admin javobini kuting (10 daqiqadan keyin qayta so'rash mumkin).",
        )

    founder = db.get(User, school.owner_user_id)
    contact = _founder_contact(db, school)
    # Oddiy matnda tg:// havola bosilmaydi — username bo'lmasa, Telegram ID yoziladi
    requester_link = f"@{user.username}" if user.username else f"Telegram ID: {user.tg_id}"

    words = _kind_words(school.kind)
    founder_text = (
        f"🔑 Kirish kodi so'raldi\n\n"
        f"👤 {user.full_name} ({requester_link})\n"
        f"«{school.name}» ({words['noun']}) ga o'qituvchi sifatida qo'shilmoqchi.\n\n"
        f"✅ Agar u haqiqatan sizning o'qituvchingiz bo'lsa — unga Telegram orqali kirish kodini yuboring:\n"
        f"{school.code}\n\n"
        f"⛔️ Tanimasangiz yoki boshqa maktabdan bo'lsa — kodni BERMANG. Kod bilan har kim "
        f"maktabingizga kira oladi."
    )
    delivered = _send_bot_text(founder.tg_id, founder_text) if founder else False

    founder_ref = f"@{contact['username']}" if contact["username"] else contact["name"]
    requester_text = (
        f"📨 Kod so'rovingiz yuborildi\n\n"
        f"«{school.name}» admini — {contact['name']} ({founder_ref}).\n"
        f"U sizga kirish kodini Telegram orqali yuboradi. Tezroq bo'lishi uchun unga o'zingiz ham yozishingiz mumkin.\n\n"
        f"Kodni olgach: Ta'lim muassasalari → {words['section']} → «{school.name}» ni tanlang → kodni kiriting.\n\n"
        f"Kodni hech kimga bermang — u faqat shu yerning o'qituvchilari uchun."
    )
    if delivered:
        _send_bot_text(user.tg_id, requester_text)
        _code_requests[key] = now

    return {"delivered": delivered, "founder": contact}


@app.get("/api/schools/me")
def my_school(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchi a'zo bo'lgan maktab (bo'lmasa — null)."""

    member = _active_membership(db, user)
    if member:
        return {"school": _school_view(db, member, user)}

    # A'zo emas, lekin o'zi ochgan maktab bor — kodsiz qaytish uchun
    owned = db.scalar(
        select(PrivateSchool)
        .where(PrivateSchool.owner_user_id == user.id, PrivateSchool.kind == _request_inst_kind.get(),
               PrivateSchool.deleted_at.is_(None))
        .order_by(PrivateSchool.id.desc())
    )
    return {
        "school": None,
        "owned_school": (
            {"id": owned.id, "name": owned.name, "region": owned.region, "district": owned.district} if owned else None
        ),
    }


@app.post("/api/schools")
def create_school(body: SchoolCreateIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Yangi xususiy maktab ochadi; yaratgan o'qituvchi uning egasi bo'ladi."""

    _require_profile_name(user)
    kind = _request_inst_kind.get()
    words = _kind_words(kind)
    if _active_membership(db, user, kind):
        raise HTTPException(status_code=409, detail=f"Siz allaqachon {words['to']} a'zosiz. Avval undan chiqing.")
    if not is_valid_location(body.region, body.district):
        raise HTTPException(status_code=422, detail="Viloyat yoki tuman noto'g'ri tanlangan")

    # Bitta tumanda bir xil nomli ikki maktab o'qituvchilarni chalg'itadi
    same_name = db.scalar(
        select(PrivateSchool.id).where(
            PrivateSchool.region == body.region,
            PrivateSchool.district == body.district,
            PrivateSchool.kind == kind,
            PrivateSchool.deleted_at.is_(None),
            func.lower(PrivateSchool.name) == body.name.lower(),
        )
    )
    if same_name:
        raise HTTPException(
            status_code=409,
            detail=f"«{body.name}» bu tumanda allaqachon ochilgan. Uni ro'yxatdan tanlab, kod bilan kiring.",
        )

    school = PrivateSchool(
        kind=kind,
        name=body.name,
        region=body.region,
        district=body.district,
        code=_new_school_code(db),
        owner_user_id=user.id,
    )
    db.add(school)
    db.flush()

    # Maktabni ochgan — DIREKTOR. U fan o'qituvchisi emas, fanlari bo'lmaydi.
    member = SchoolMember(
        school_id=school.id,
        user_id=user.id,
        role="owner",
        subjects="[]",
        phone=body.phone,
        birth_date=body.birth_date,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    logging.getLogger("backend").info("Muassasa ochildi (%s): id=%s kod=%s", kind, school.id, school.code)
    return {"school": _school_view(db, member, user)}


def _school_by_code(db: Session, user: User, code: str, school_id: int | None) -> PrivateSchool:
    """Kod to'g'ri bo'lsa maktabni qaytaradi; noto'g'ri urinishlar hisoblanadi (taxmin qilishdan himoya)."""

    _check_code_attempts(user)
    if school_id:
        school = db.get(PrivateSchool, school_id)
        if not school or school.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Maktab topilmadi")
        if not hmac.compare_digest(code, school.code):
            _record_code_failure(user)
            raise HTTPException(status_code=403, detail="Maktab kodi noto'g'ri. Kodni maktabingiz adminidan oling.")
    else:
        school = db.scalar(select(PrivateSchool).where(PrivateSchool.code == code, PrivateSchool.deleted_at.is_(None)))
        if not school:
            _record_code_failure(user)
            raise HTTPException(status_code=404, detail="Bunday kodli maktab topilmadi. Kodni tekshirib qayta kiriting.")
    _school_code_failures.pop(user.id, None)
    return school


def _open_classes(db: Session, school_id: int) -> list[SchoolClass]:
    """Kuratori hali tayinlanmagan sinflar — qo'shilayotgan kurator o'zinikini tanlaydi."""
    return _sorted_classes(db.scalars(
        select(SchoolClass)
        .where(SchoolClass.school_id == school_id, SchoolClass.deleted_at.is_(None), SchoolClass.curator_user_id.is_(None),
               SchoolClass.subject.is_(None))  # fan guruhining o'qituvchisini admin tayinlaydi
    ).all())


@app.post("/api/schools/lookup")
def lookup_school(body: SchoolCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kod bo'yicha maktab nomi va kuratorsiz sinflari (qo'shilish formasida "Kuratormisiz? — Ha" uchun)."""

    school = _school_by_code(db, user, body.code, body.school_id)
    return {
        "school": {"id": school.id, "name": school.name, "kind": school.kind or "school",
                   "region": school.region, "district": school.district},
        "open_classes": [{"id": item.id, "name": item.name} for item in _open_classes(db, school.id)],
    }


def _join_as_teacher(db: Session, user: User, school: PrivateSchool, body) -> SchoolMember:
    """
    O'qituvchini maktabga qo'shadi. Kuratormi? Ha — kuratori yo'q sinflardan
    o'zinikini tanlaydi (fan ixtiyoriy). Yo'q — dars beradigan fani majburiy.
    Maktab egasi (admin) qaytayotgan bo'lsa — admin sifatida qaytadi.
    """

    if school.owner_user_id == user.id:
        return _rejoin_as_director(db, user, school)
    if not body.phone:
        raise HTTPException(status_code=422, detail="Telefon raqamingizni kiriting — bu majburiy")
    if not body.is_curator:
        _require_subjects(body.subjects)

    curator_class = None
    if body.is_curator and body.curator_class_id:
        curator_class = db.get(SchoolClass, body.curator_class_id)
        if not curator_class or curator_class.school_id != school.id or curator_class.deleted_at is not None:
            raise HTTPException(status_code=404, detail="Sinf topilmadi")
        if curator_class.curator_user_id is not None:
            raise HTTPException(status_code=409, detail=f"«{curator_class.name}» sinfining kuratori allaqachon bor")
    elif body.is_curator and _open_classes(db, school.id):
        raise HTTPException(status_code=422, detail="Qaysi sinfning kuratori ekaningizni tanlang")

    member = SchoolMember(
        school_id=school.id,
        user_id=user.id,
        role="teacher",
        subjects=json.dumps(body.subjects, ensure_ascii=False),
        phone=body.phone,
        birth_date=body.birth_date,
    )
    db.add(member)
    if curator_class:
        curator_class.curator_user_id = user.id
    db.flush()
    return member


@app.post("/api/schools/join")
def join_school(body: SchoolCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Maktab kodi bilan maktabga qo'shiladi. Kod BIR MARTALIK: bitta o'qituvchi
    qo'shilgach, maktabga yangi kod beriladi (eski kod endi ishlamaydi).
    Yangi kod admin va o'qituvchilarning Profilida ko'rinadi.
    """

    _require_profile_name(user)
    school = _school_by_code(db, user, body.code, body.school_id)
    if _active_membership(db, user, school.kind):
        words = _kind_words(school.kind)
        raise HTTPException(status_code=409, detail=f"Siz allaqachon {words['to']} a'zosiz. Avval undan chiqing.")
    member = _join_as_teacher(db, user, school, body)
    if member.role != "owner":
        school.code = _new_school_code(db)
        logging.getLogger("backend").info("Maktab kodi ishlatildi va yangilandi: id=%s", school.id)
    db.commit()
    db.refresh(member)
    return {"school": _school_view(db, member, user)}


# ---------- Shaxsiy taklif havolasi (kodsiz qo'shilish) ----------

INVITE_LIFETIME = timedelta(hours=24)


def _invite_link(token: str) -> str | None:
    username = _bot_username()
    return f"https://t.me/{username}?start=inv_{token}" if username else None


def _valid_invite(db: Session, token: str) -> SchoolInvite:
    invite = db.scalar(select(SchoolInvite).where(SchoolInvite.token == token))
    if not invite:
        raise HTTPException(status_code=404, detail="Taklif havolasi topilmadi")
    if invite.used_at is not None:
        raise HTTPException(status_code=410, detail="Bu taklif havolasi allaqachon ishlatilgan. Admindan yangisini so'rang.")
    if invite.expires_at < utcnow():
        raise HTTPException(status_code=410, detail="Taklif havolasining muddati tugagan. Admindan yangisini so'rang.")
    school = db.get(PrivateSchool, invite.school_id)
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Taklif havolasi topilmadi")
    return invite


@app.post("/api/schools/invites")
def create_invite(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin o'qituvchiga yuborish uchun bir martalik, 24 soatlik havola yaratadi."""

    member = _require_school_member(db, user)
    _require_director(member)

    token = secrets.token_urlsafe(12).replace("-", "").replace("_", "")[:16]
    invite = SchoolInvite(
        school_id=member.school_id, token=token, created_by=user.id, expires_at=utcnow() + INVITE_LIFETIME
    )
    db.add(invite)
    db.commit()
    return {"token": token, "link": _invite_link(token), "expires_at": invite.expires_at,
            "school_name": member.school.name}


@app.get("/api/schools/invites/{token}")
def invite_info(token: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Havola kimni, qaysi maktabga taklif qilyapti — qo'shilish formasi uchun."""

    invite = _valid_invite(db, token)
    school = db.get(PrivateSchool, invite.school_id)
    inviter = db.get(User, invite.created_by)
    return {
        "school": {"id": school.id, "name": school.name, "kind": school.kind or "school",
                   "region": school.region, "district": school.district},
        "invited_by": inviter.full_name if inviter else "",
        "open_classes": [{"id": item.id, "name": item.name} for item in _open_classes(db, school.id)],
    }


@app.post("/api/schools/invites/{token}/accept")
def accept_invite(
    token: str, body: InviteAcceptIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Shaxsiy havola orqali — kodsiz qo'shilish. Havola shu bilan yopiladi."""

    _require_profile_name(user)
    invite = db.scalar(select(SchoolInvite).where(SchoolInvite.token == token).with_for_update())
    if not invite:
        raise HTTPException(status_code=404, detail="Taklif havolasi topilmadi")
    invite = _valid_invite(db, token)
    school = db.get(PrivateSchool, invite.school_id)
    if _active_membership(db, user, school.kind):
        words = _kind_words(school.kind)
        raise HTTPException(status_code=409, detail=f"Siz allaqachon {words['to']} a'zosiz. Avval undan chiqing.")

    member = _join_as_teacher(db, user, school, body)
    invite.used_by = user.id
    invite.used_at = utcnow()
    db.commit()
    db.refresh(member)
    return {"school": _school_view(db, member, user)}


@app.put("/api/schools/members/{user_id}/position")
def set_member_position(
    user_id: int, body: PositionIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Admin (maktabni ochgan) o'qituvchiga lavozim beradi: direktor yoki zam
    direktor. Direktor bitta bo'ladi — yangisi tayinlansa, avvalgisi oddiy
    o'qituvchiga qaytadi. Zam direktor bir nechta bo'lishi mumkin.
    """

    member = _require_school_member(db, user)
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Lavozimni faqat maktab admini tayinlaydi")
    target = db.scalar(
        select(SchoolMember).where(
            SchoolMember.school_id == member.school_id,
            SchoolMember.user_id == user_id,
            SchoolMember.left_at.is_(None),
        )
    )
    if not target:
        raise HTTPException(status_code=404, detail="Bu o'qituvchi maktabingizda yo'q")
    if target.role == "owner":
        raise HTTPException(status_code=422, detail="Admin lavozimi o'zgartirilmaydi")

    if body.position == "director":
        for other in db.scalars(
            select(SchoolMember).where(
                SchoolMember.school_id == member.school_id,
                SchoolMember.position == "director",
                SchoolMember.left_at.is_(None),
                SchoolMember.id != target.id,
            )
        ):
            other.position = None
    target.position = body.position
    db.commit()
    db.refresh(member)
    return {"school": _school_view(db, member, user)}


@app.delete("/api/schools/members/{user_id}")
def remove_member(user_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Admin (yoki direktor) xodimni maktabdan chiqaradi. Yozuv o'chirilmaydi —
    left_at belgilanadi; davomat tarixi o'z joyida qoladi. Kurator bo'lgan
    sinflari kuratorsiz qoladi (yangisini tayinlash kerak).
    """

    member = _require_school_member(db, user)
    if not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Xodimni faqat maktab admini yoki direktor chiqaradi")
    target = db.scalar(
        select(SchoolMember).where(
            SchoolMember.school_id == member.school_id,
            SchoolMember.user_id == user_id,
            SchoolMember.left_at.is_(None),
        )
    )
    if not target:
        raise HTTPException(status_code=404, detail="Bu xodim maktabingizda yo'q")
    if target.role == "owner":
        raise HTTPException(status_code=422, detail="Maktab adminini chiqarib bo'lmaydi")
    if target.id == member.id:
        raise HTTPException(status_code=422, detail="O'zingizni chiqarib bo'lmaydi")

    target.left_at = utcnow()
    target.position = None
    for school_class in db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id,
                                  or_(SchoolClass.curator_user_id == user_id, SchoolClass.assistant_user_id == user_id))
    ):
        if school_class.curator_user_id == user_id:
            school_class.curator_user_id = None
        if school_class.assistant_user_id == user_id:
            school_class.assistant_user_id = None
    db.commit()
    db.refresh(member)
    logging.getLogger("backend").info("Xodim maktabdan chiqarildi: school=%s user=%s", member.school_id, user_id)
    return {"school": _school_view(db, member, user)}


@app.put("/api/schools/me/profile")
def update_school_profile(body: SchoolProfileIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchi maktabdagi profilini (fanlarini) yangilaydi."""

    member = _active_membership(db, user)
    if not member:
        raise HTTPException(status_code=404, detail="Siz hech qaysi maktabga a'zo emassiz")
    is_curator = db.scalar(
        select(SchoolClass.id).where(SchoolClass.curator_user_id == user.id, SchoolClass.deleted_at.is_(None))
    )
    if member.role != "owner" and not is_curator:
        _require_subjects(body.subjects)  # admin va kurator uchun fan ixtiyoriy
    if body.phone:
        member.phone = body.phone
    elif not member.phone:
        raise HTTPException(status_code=422, detail="Telefon raqamingizni kiriting — bu majburiy")
    # Faqat yuborilgan maydonlar o'zgaradi (yuborilmagani o'chib ketmasin)
    if "birth_date" in body.model_fields_set:
        member.birth_date = body.birth_date

    member.subjects = json.dumps(body.subjects, ensure_ascii=False)
    db.commit()
    return {"school": _school_view(db, member, user)}


# ---------- Maktab admini o'z maktabini o'chiradi ----------
# Admin "Maktabni o'chirish" → tasdiq → maktab kodi. Maktab darhol emas, 3 kundan keyin o'chadi:
# shu orada admin bekor qila oladi, xodimlar esa bot orqali ogohlantiriladi. O'chirish platforma
# adminidagi kabi "yumshoq": ro'yxatlardan yo'qoladi, xodimlar chiqariladi, ma'lumotlar bazada qoladi.
SCHOOL_DELETE_DELAY = timedelta(days=3)


def _soft_delete_school(school: PrivateSchool) -> int:
    """Muassasani o'chirilgan deb belgilaydi va barcha xodimlarni chiqaradi. Chiqarilganlar sonini qaytaradi."""

    now = utcnow()
    school.deleted_at = now
    school.delete_requested_at = None
    removed = 0
    for member in school.members:
        if member.left_at is None:
            member.left_at = now
            member.position = None
            removed += 1
    return removed


def _school_delete_at(school: PrivateSchool) -> datetime | None:
    return school.delete_requested_at + SCHOOL_DELETE_DELAY if school.delete_requested_at else None


def _notify_school_staff(school: PrivateSchool, text: str, skip_user_id: int | None = None) -> None:
    """Muassasaning barcha faol xodimlariga bot xabari (fonda — so'rov kutib qolmasin)."""

    chats = [member.user.tg_id for member in school.members
             if member.left_at is None and member.user_id != skip_user_id and member.user and member.user.tg_id > 0]

    def send() -> None:
        for chat_id in chats:
            _send_bot_text(chat_id, text)

    if chats:
        threading.Thread(target=send, name="school-delete-notice", daemon=True).start()


def _owner_membership(db: Session, user: User) -> SchoolMember:
    member = _active_membership(db, user)
    if not member:
        raise HTTPException(status_code=404, detail="Siz hech qaysi maktabga a'zo emassiz")
    if member.role != "owner":
        raise HTTPException(status_code=403, detail="Maktabni faqat uning admini o'chira oladi")
    return member


@app.post("/api/schools/me/delete")
def request_school_delete(body: SchoolCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin maktabni o'chirishga qo'yadi (maktab kodi bilan) — 3 kundan keyin o'chadi."""

    member = _owner_membership(db, user)
    school = member.school
    _check_code_attempts(user)
    if not hmac.compare_digest(body.code.strip().upper(), school.code.upper()):
        _record_code_failure(user)
        raise HTTPException(status_code=403, detail="Maktab kodi noto'g'ri")
    _school_code_failures.pop(user.id, None)

    if not school.delete_requested_at:
        school.delete_requested_at = utcnow()
        db.commit()
        words = _kind_words(school.kind)
        _notify_school_staff(
            school,
            f"⚠️ «{school.name}» {words['noun']}i admin tomonidan o'chirishga qo'yildi. "
            f"U {_local_time(_school_delete_at(school))} da o'chiriladi — shundan keyin unga kirib bo'lmaydi.",
            skip_user_id=user.id,
        )
    return {"ok": True, "delete_at": _school_delete_at(school), "school": _school_view(db, member, user)}


@app.post("/api/schools/me/delete/cancel")
def cancel_school_delete(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin 3 kun ichida o'chirishni bekor qiladi."""

    member = _owner_membership(db, user)
    school = member.school
    if school.delete_requested_at:
        school.delete_requested_at = None
        db.commit()
        words = _kind_words(school.kind)
        _notify_school_staff(school, f"✅ «{school.name}» {words['noun']}ini o'chirish bekor qilindi — hammasi avvalgidek.",
                             skip_user_id=user.id)
    return {"ok": True, "school": _school_view(db, member, user)}


def _delete_scheduled_schools_once() -> int:
    """Fon tsikli: o'chirishga qo'yilganiga 3 kun bo'lgan muassasalarni o'chiradi."""

    with SessionLocal() as db:
        due = db.scalars(
            select(PrivateSchool).where(
                PrivateSchool.deleted_at.is_(None),
                PrivateSchool.delete_requested_at.is_not(None),
                PrivateSchool.delete_requested_at <= utcnow() - SCHOOL_DELETE_DELAY,
            )
        ).all()
        for school in due:
            words = _kind_words(school.kind)
            text = f"🗑 «{school.name}» {words['noun']}i o'chirildi."
            chats = [member.user.tg_id for member in school.members
                     if member.left_at is None and member.user and member.user.tg_id > 0]
            removed = _soft_delete_school(school)
            db.commit()
            logging.getLogger("backend").warning("Muassasa admin so'rovi bilan o'chirildi: id=%s nomi=%s (%s ta xodim)",
                                                 school.id, school.name, removed)
            for chat_id in chats:
                _send_bot_text(chat_id, text)
        return len(due)


@app.post("/api/schools/leave")
def leave_school(body: SchoolCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Maktabdan chiqish — faqat maktab kodi to'g'ri kiritilsa.

    A'zolik yozuvi o'chirilmaydi (left_at belgilanadi). Admin chiqsa,
    adminlikni o'zi tanlagan o'qituvchiga topshiradi; hech kim qolmasa,
    maktab ma'lumotlari baribir saqlanib turadi.
    """

    member = _active_membership(db, user)
    if not member:
        raise HTTPException(status_code=404, detail="Siz hech qaysi maktabga a'zo emassiz")
    if member.role != "owner":
        raise HTTPException(
            status_code=403, detail="Maktabdan chiqish — faqat admin orqali. Adminga murojaat qiling."
        )

    school = member.school
    _check_code_attempts(user)
    if not hmac.compare_digest(body.code, school.code):
        _record_code_failure(user)
        raise HTTPException(status_code=403, detail="Maktab kodi noto'g'ri. Chiqish uchun maktab kodini kiriting.")

    # Admin chiqayotganda o'rniga kimni admin qilishni O'ZI tanlaydi —
    # maktab adminsiz qolmasin. Boshqa o'qituvchi bo'lmasa, tanlash shart emas.
    successor = None
    if member.role == "owner":
        others = db.scalars(
            select(SchoolMember).where(
                SchoolMember.school_id == school.id, SchoolMember.left_at.is_(None), SchoolMember.id != member.id
            )
        ).all()
        if others:
            successor = next((item for item in others if item.user_id == body.successor_user_id), None)
            if not successor:
                raise HTTPException(
                    status_code=422, detail="Chiqishdan oldin adminlikni maktabdagi o'qituvchilardan biriga topshiring"
                )

    member.left_at = utcnow()
    if member.role == "owner":
        member.role = "teacher"
        if successor:
            successor.role = "owner"
            successor.position = None  # endi u admin
            school.owner_user_id = successor.user_id

    db.commit()
    return {"ok": True}


# ========== 6.6) Sinflar va davomat ==========
#
# O'qituvchi o'z sinf/guruhlarini yaratadi, o'quvchilarni qo'shadi va har
# darsda davomat oladi. Davomat serverda saqlanadi (birinchi olingan vaqt
# o'zgarmaydi, tahrir vaqti alohida) va matn, rasm yoki PDF ko'rinishida
# ota-onalar guruhiga yuborish uchun tayyorlanadi. Hech narsa butunlay
# o'chirilmaydi: sinf va o'quvchi faqat belgilanadi, davomat tarixi qoladi.

ATTENDANCE_LABELS = {
    "present": ("Bor", "✅"),
    "absent": ("Yo'q", "❌"),
    "excused": ("Sababli", "🟡"),
    "late": ("Kechikdi", "⏰"),
}
TASHKENT_OFFSET = timedelta(hours=5)


def _local_time(moment: datetime | None) -> str:
    """UTC vaqtni Toshkent vaqtida "08.10.2026 09:05" ko'rinishida yozadi."""
    return (moment + TASHKENT_OFFSET).strftime("%d.%m.%Y %H:%M") if moment else ""


def _require_school_member(db: Session, user: User) -> SchoolMember:
    member = _active_membership(db, user)
    if not member:
        raise HTTPException(status_code=403, detail="Avval maktabga a'zo bo'ling")
    return member


def _school_class(db: Session, user: User, class_id: int) -> tuple[SchoolClass, SchoolMember]:
    """Foydalanuvchi maktabidagi sinf (maktabning har bir o'qituvchisi ko'ra oladi)."""

    school_class = db.get(SchoolClass, class_id)
    # Sinf o'z muassasasi bo'yicha tekshiriladi: o'qituvchi maktabda ham, kursda ham bo'lsa — ikkalasida ishlaydi
    member = _member_in_school(db, user, school_class.school_id) if school_class else None
    if not school_class or school_class.deleted_at is not None or not member:
        raise HTTPException(status_code=404, detail="Sinf topilmadi")
    return school_class, member


POSITION_LABELS = {"director": "Direktor", "deputy": "Zam direktor"}  # rahbariyat
# Rahbariyatga kirmaydigan lavozimlar — faqat o'z sohasi: moliyachi — to'lovlar, komendant — yotoqxona
STAFF_POSITION_LABELS = {"accountant": "Moliyachi", "commandant": "Komendant"}
ALL_POSITION_LABELS = {**POSITION_LABELS, **STAFF_POSITION_LABELS}


def _sees_all_finance(member: SchoolMember) -> bool:
    """Barcha sinflarning to'lovlari, qarzlari va narxlari: rahbariyat va moliyachi."""
    return _is_director(member) or member.position == "accountant"


def _can_set_fees(member: SchoolMember) -> bool:
    """Oylik to'lov summasini belgilaydi: admin, direktor va moliyachi."""
    return _can_delete_classes(member) or member.position == "accountant"


def _can_manage_dorm(member: SchoolMember) -> bool:
    """Yotoqxonani boshqaradi (binolar, xonalar, joylashtirish, tarbiyachilar): admin, direktor va komendant."""
    return _can_delete_classes(member) or member.position == "commandant"


def _is_director(member: SchoolMember) -> bool:
    """Maktab rahbariyati: admin (maktabni ochgan), direktor va zam direktor.
    Sinf yaratadi, kurator tayinlaydi, o'qituvchi taklif qiladi, davomatni tahrirlaydi."""
    return member.role == "owner" or member.position in POSITION_LABELS


def _can_delete_classes(member: SchoolMember) -> bool:
    """Sinflarni ochish, sozlash (nom, kurator) va o'chirish — faqat admin va direktor."""
    return member.role == "owner" or member.position == "director"


def _require_head(member: SchoolMember) -> None:
    if not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Sinflarni faqat maktab admini yoki direktor ochadi va sozlaydi")


def _is_class_teacher(school_class: SchoolClass, user_id: int | None) -> bool:
    """Sinf kuratori yoki fan guruhining asosiy / yordamchi o'qituvchisi — huquqlari bir xil."""
    return user_id is not None and user_id in (school_class.curator_user_id, school_class.assistant_user_id)


def _can_manage_class(school_class: SchoolClass, member: SchoolMember) -> bool:
    """O'quvchilarni qo'shish/tahrirlash — admin, direktor yoki o'sha sinfning kuratori (guruh o'qituvchilari)."""
    return _can_delete_classes(member) or _is_class_teacher(school_class, member.user_id)


def _can_see_payments(school_class: SchoolClass, member: SchoolMember) -> bool:
    """Moliyaviy ma'lumotlar (to'lov, qarz, narx): rahbariyat va moliyachi — hamma sinf, kurator — faqat o'z sinfi."""
    return _sees_all_finance(member) or school_class.curator_user_id == member.user_id


def _require_director(member: SchoolMember) -> None:
    if not _is_director(member):
        raise HTTPException(status_code=403, detail="Buni faqat maktab admini, direktor yoki zam direktor qila oladi")


def _managed_class(db: Session, user: User, class_id: int) -> SchoolClass:
    school_class, member = _school_class(db, user, class_id)
    if not _can_manage_class(school_class, member):
        raise HTTPException(status_code=403, detail="O'quvchilarni faqat admin, direktor yoki sinf kuratori qo'sha oladi")
    return school_class


def _director_class(db: Session, user: User, class_id: int) -> SchoolClass:
    school_class, member = _school_class(db, user, class_id)
    if not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Sinfni faqat maktab admini yoki direktor o'chira oladi")
    return school_class


def _sees_all_attendance(school_class: SchoolClass, member: SchoolMember | None) -> bool:
    """Sinfning barcha davomatlarini — rahbariyat va sinf kuratori ko'radi; fan o'qituvchisi faqat o'zinikini."""
    return bool(member and (_is_director(member) or _is_class_teacher(school_class, member.user_id)))


def _attendance_filter(school_class: SchoolClass, member: SchoolMember | None):
    """
    Fan o'qituvchisi qaysi davomatlarni ko'radi: o'zi olganlarini va O'Z FANI
    bo'yicha olinganlarini (masalan, o'rniga dars o'tgan boshqa biologiya
    o'qituvchisiniki). Boshqa fanniki ko'rinmaydi. Rahbariyat va kurator — hammasini
    (shunda None qaytadi).
    """

    if _sees_all_attendance(school_class, member):
        return None
    if not member:
        return Attendance.id == -1
    subjects = _read_subjects(member)
    conditions = [Attendance.teacher_user_id == member.user_id]
    if subjects:
        conditions.append(Attendance.subject.in_(subjects))
    return or_(*conditions)


def _can_see_attendance(attendance: Attendance, school_class: SchoolClass, member: SchoolMember | None) -> bool:
    if _sees_all_attendance(school_class, member):
        return True
    return bool(member and (attendance.teacher_user_id == member.user_id or attendance.subject in _read_subjects(member)))


def _school_attendance(db: Session, user: User, attendance_id: int) -> tuple[Attendance, SchoolMember]:
    attendance = db.get(Attendance, attendance_id)
    if not attendance or attendance.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Davomat topilmadi")
    school_class, member = _school_class(db, user, attendance.class_id)
    if not _can_see_attendance(attendance, school_class, member):
        raise HTTPException(status_code=403, detail="Bu davomat boshqa fan bo'yicha olingan")
    return attendance, member


STUDENT_CODE_FIRST, STUDENT_CODE_LAST = 1001, 9999


def _exam_guest_numbers(db: Session, school_id: int) -> set[int]:
    """
    Maktab sinflari biriktirilgan, hali yakunlanmagan imtihonlarga test sahifasidan (sinfsiz) qo'shilgan
    o'quvchilarning raqamlari: sinfga yangi qo'shilgan o'quvchi ularni olmaydi — titul boshqasiga tushmasin.
    """

    school_tests = (
        select(Attempt.test_id)
        .join(ClassStudent, ClassStudent.id == Attempt.class_student_id)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == school_id)
    )
    return set(db.scalars(
        select(Attempt.sheet_number)
        .join(Test, Test.id == Attempt.test_id)
        .where(Attempt.test_id.in_(school_tests), Test.status != "closed", Attempt.class_student_id.is_(None),
               Attempt.user_id.is_(None), Attempt.sheet_number.is_not(None), Attempt.status.in_(("pending", "done")))
    ).all())


def _assign_student_codes(db: Session, school_id: int) -> None:
    """
    ID oralig'i belgilangan sinflarda ID'siz (yangi qo'shilgan) o'quvchilarga sinfdagi OXIRGI ID'dan
    keyingisini beradi: 1001–1056 berilgan bo'lsa, yangi o'quvchi — 1057. Oraliq tugagan bo'lsa, u o'zi
    kengayadi (boshqa sinf oralig'iga kirmasa); kirsa — oraliqda bo'shab qolgan raqam beriladi, u ham
    bo'lmasa — ID'siz qoladi (admin oraliqni o'zgartiradi).
    """

    # Bir vaqtda ikki o'qituvchi o'quvchi qo'shsa ham ID takrorlanmasin
    db.execute(select(PrivateSchool.id).where(PrivateSchool.id == school_id).with_for_update())
    rows = db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == school_id)
        .order_by(ClassStudent.id)
    ).all()
    changed = False
    # Sinfdan chiqarilgan (yoki sinfi o'chirilgan) o'quvchining ID'si bo'shaydi — keyingi yangi
    # o'quvchiga beriladi (1031, 1032 o'chdi → yangisi 1032). Natija va davomatlari o'quvchiga
    # bog'langan, ID'ga emas — yo'qolmaydi. Hali skanerlanmagan imtihon varag'idagi raqam ham
    # olinadi: shu ID'li yangi o'quvchining varag'i unga tushib qolmasin.
    for student, school_class in rows:
        if student.student_code and (student.removed_at or school_class.deleted_at):
            for attempt in db.scalars(
                select(Attempt).where(Attempt.class_student_id == student.id, Attempt.status == "pending",
                                      Attempt.sheet_number == student.student_code)
            ).all():
                attempt.sheet_number = None
            student.student_code = None
            changed = True
    taken = {student.student_code for student, _ in rows if student.student_code}
    # Har sinfdagi eng katta ID — yangi o'quvchi undan keyingisini oladi (ro'yxat oxiriga qo'shilgandek)
    last_code: dict[int, int] = {}
    for student, school_class in rows:
        if student.student_code and not student.removed_at and student.source_student_id is None:
            last_code[school_class.id] = max(last_code.get(school_class.id, 0), student.student_code)
    ranges: dict[int, tuple[int, int]] | None = None  # sinflar oraliqlari — oraliq kengaytirilganda kerak
    guests: set[int] | None = None  # imtihonga sinfsiz qo'shilgan o'quvchilar raqami — kerak bo'lganda o'qiladi
    for student, school_class in rows:
        # O'chirilgan sinf o'quvchisiga ID berilmaydi — aks holda har chaqiruvda yuqorida olinib, bu yerda
        # qayta berilardi (har sinf ochilishida bazaga o'nlab yozish va sekinlik)
        if (student.student_code or student.removed_at or school_class.deleted_at or school_class.code_start is None
                or student.source_student_id is not None):
            continue
        if guests is None:
            guests = _exam_guest_numbers(db, school_id)
        code = max(last_code.get(school_class.id, 0), school_class.code_start - 1) + 1
        while code in guests:  # imtihon ro'yxatiga qo'shilgan mehmon raqami (1058) — keyingisi (1059)
            code += 1
        if code > school_class.code_end:
            if ranges is None:
                ranges = {
                    class_id: (start, end) for class_id, start, end in db.execute(
                        select(SchoolClass.id, SchoolClass.code_start, SchoolClass.code_end).where(
                            SchoolClass.school_id == school_id, SchoolClass.deleted_at.is_(None),
                            SchoolClass.code_start.is_not(None))
                    ).all()
                }
            free = code <= STUDENT_CODE_LAST and code not in taken and not any(
                start <= code <= end for class_id, (start, end) in ranges.items() if class_id != school_class.id)
            if free:
                school_class.code_end = code  # oraliq o'zi kengayadi: 1001–1056 → 1001–1057
                ranges[school_class.id] = (school_class.code_start, code)
            else:
                code = None
        elif code in taken:
            code = None
        if code is None:
            # Keyingi raqam boshqa sinfniki — oraliqda bo'shab qolgan raqam (sinfdan chiqarilgan o'quvchiniki)
            code = next((value for value in range(school_class.code_start, school_class.code_end + 1)
                         if value not in taken and value not in guests), None)
        if code is None:
            continue
        student.student_code = code
        taken.add(code)
        last_code[school_class.id] = max(last_code.get(school_class.id, 0), code)
        changed = True
    # Fan guruhidagi o'quvchi — asl o'quvchining ID'si bilan (guruh imtihonida titul shu raqam bilan o'qiladi)
    by_id = {student.id: (student, school_class) for student, school_class in rows}
    for student, school_class in rows:
        if student.source_student_id is None or student.removed_at or school_class.deleted_at:
            continue
        source, home = by_id.get(student.source_student_id, (None, None))
        code = source.student_code if source and not source.removed_at and not home.deleted_at else None
        if student.student_code != code:
            student.student_code = code
            changed = True
    if changed:
        db.commit()


def _active_students(school_class: SchoolClass) -> list[ClassStudent]:
    """
    Sinf ro'yxati — jurnaldagidek o'zbek alifbosi tartibida (ism "Familiya Ism" ko'rinishida yoziladi,
    demak familiya bo'yicha; Sh, Ch, O‘, G‘ — Z dan keyin, qarang: _uz_sort_key). Davomat, to'lov, natija
    va PDF/rasm hisobotlari ham shu tartibda chiqadi.
    """
    active = [student for student in school_class.students if student.removed_at is None]
    return sorted(active, key=lambda student: (_uz_sort_key(student.full_name), student.id))


def _attendance_counts(attendance: Attendance) -> dict[str, int]:
    counts = {status: 0 for status in ATTENDANCE_LABELS}
    for mark in attendance.marks:
        counts[mark.status] = counts.get(mark.status, 0) + 1
    return counts


def _attendance_view(attendance: Attendance) -> dict:
    return {
        "id": attendance.id,
        "class_id": attendance.class_id,
        "subject": attendance.subject,
        "lesson_start": attendance.lesson_start,
        "lesson_end": attendance.lesson_end,
        "taken_at": attendance.taken_at,
        "updated_at": attendance.updated_at,
        "counts": _attendance_counts(attendance),
        "marks": [
            {"student_id": mark.student_id, "name": mark.student_name, "status": mark.status}
            for mark in _sorted_marks(attendance)
        ],
    }


def _sorted_marks(attendance: Attendance) -> list[AttendanceMark]:
    """Davomat belgilari — sinf ro'yxatidagidek alifbo tartibida (eski davomatlar ham)."""
    return sorted(attendance.marks, key=lambda mark: (_uz_sort_key(mark.student_name), mark.id))


def _student_attendance_stats(db: Session, student_ids: list[int], scope=None) -> dict[int, dict]:
    """Har bir o'quvchi bo'yicha: necha darsda bor/yo'q/sababli/kechikkan — bitta so'rovda.
    scope — fan o'qituvchisi uchun filtr: foiz faqat uning fani bo'yicha hisoblanadi."""

    stats = {sid: {status: 0 for status in ATTENDANCE_LABELS} for sid in student_ids}
    if not student_ids:
        return stats
    query = (
        select(AttendanceMark.student_id, AttendanceMark.status, func.count())
        .join(Attendance, Attendance.id == AttendanceMark.attendance_id)
        .where(AttendanceMark.student_id.in_(student_ids), Attendance.deleted_at.is_(None))
        .group_by(AttendanceMark.student_id, AttendanceMark.status)
    )
    if scope is not None:
        query = query.where(scope)
    rows = db.execute(query).all()
    for student_id, status, count in rows:
        stats[student_id][status] = count
    for item in stats.values():
        item["total"] = sum(item[status] for status in ATTENDANCE_LABELS)
        # Kechikkan ham darsda bo'lgan hisoblanadi
        attended = item["present"] + item["late"]
        item["rate"] = round(attended / item["total"] * 100) if item["total"] else None
    return stats


def _class_rating(
    db: Session, school_class: SchoolClass, member: SchoolMember | None, student_ids: list[int]
) -> tuple[dict[int, dict], int]:
    """
    Sinf reytingi: sinfning BARCHA testlari va skanerlangan sinovlari (ClassTest, hamma fanlar)
    bo'yicha har o'quvchining o'rtacha foizi — kim ochsa ham bir xil. Qatnashmagan testi hisobga
    olinmaydi; teng natija — teng o'rin (1, 1, 3 ...). Test markazidan o'chirilgan imtihon kirmaydi.
    O'quvchi profilidagi natijalar ham hammaga ochiq — reyting nimadan chiqqani ko'rinib turadi.
    Qaytaradi: ({o'quvchi: {"rank", "avg", "tests"}}, testlar soni).
    """

    if member is None or not student_ids:
        return {}, 0
    test_ids = db.scalars(
        select(ClassTest.id).where(ClassTest.class_id == school_class.id, ClassTest.max_score > 0, *_live_class_tests())
    ).all()
    if not test_ids:
        return {}, 0

    percents: dict[int, list[float]] = {}
    for student_id, score, max_score in db.execute(
        select(ClassTestScore.student_id, ClassTestScore.score, ClassTest.max_score)
        .join(ClassTest, ClassTest.id == ClassTestScore.test_id)
        .where(
            ClassTestScore.test_id.in_(test_ids),
            ClassTestScore.student_id.in_(student_ids),
            ClassTestScore.score.is_not(None),
        )
    ).all():
        percents.setdefault(student_id, []).append(min(score / max_score * 100, 100.0))

    averages = sorted(
        ((student_id, round(sum(values) / len(values), 1), len(values)) for student_id, values in percents.items()),
        key=lambda item: -item[1],
    )
    rating: dict[int, dict] = {}
    for index, (student_id, average, count) in enumerate(averages):
        # Teng natija — teng o'rin, keyingi o'rin tashlab ketilmaydi: 1, 2, 2, 3
        same_as_previous = index and averages[index - 1][1] == average
        previous_rank = rating[averages[index - 1][0]]["rank"] if index else 0
        rank = previous_rank if same_as_previous else previous_rank + 1
        rating[student_id] = {"rank": rank, "avg": average, "tests": count}
    return rating, len(test_ids)


def _class_name_key(name: str) -> list:
    """
    Sinf nomining tabiiy tartibi: raqamlar son bo'yicha — 1-A, 2-A, …, 9-B, 10-1, 11-A
    (oddiy saralashda "10" "2" dan oldin turardi). Raqamsiz nomlar ("Iqtisod-1") — raqamlilardan keyin.
    """
    return [(0, int(part)) if part.isdigit() else (1, part.lower()) for part in re.split(r"(\d+)", name.strip()) if part]


def _sorted_classes(classes) -> list:
    return sorted(classes, key=lambda item: _class_name_key(item.name))


def _class_view(db: Session, school_class: SchoolClass, with_details: bool = False, member: SchoolMember | None = None) -> dict:
    students = _active_students(school_class)
    curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
    assistant = db.get(User, school_class.assistant_user_id) if school_class.assistant_user_id else None
    view = {
        "id": school_class.id,
        "name": school_class.name,
        # Fan guruhi (masalan "Ingliz tili") — o'quvchilar maktab ro'yxatidan qo'shiladi, to'lov yo'q
        "subject": school_class.subject,
        "is_group": bool(school_class.subject),
        "students_count": len(students),
        "created_at": school_class.created_at,
        "curator": {"user_id": curator.id, "name": curator.full_name} if curator else None,
        "is_curator": bool(member and school_class.curator_user_id == member.user_id),
        "assistant": {"user_id": assistant.id, "name": assistant.full_name} if assistant else None,
        "is_assistant": bool(member and school_class.assistant_user_id and school_class.assistant_user_id == member.user_id),
        "can_manage": bool(member and _can_manage_class(school_class, member)),
        # ⚙️ sozlamalar (nom, kurator, o'chirish) — admin va direktor
        "is_director": bool(member and _can_delete_classes(member)),
        "code_start": school_class.code_start,
        "code_end": school_class.code_end,
        "can_delete": bool(member and _can_delete_classes(member)),
        # Guruhning o'z narxi (bo'sh — muassasaning umumiy summasi) — faqat to'lovni ko'ra oladiganlarga
        "monthly_fee": school_class.monthly_fee if member and _can_see_payments(school_class, member) else None,
    }
    if with_details:
        # ID oralig'i bor sinfda ID'siz o'quvchi bo'lsa (avval qo'shilganlar) — ID beriladi.
        # Oraliqsiz sinfda ID baribir berilmaydi — butun maktabni har safar aylanib chiqish shart emas
        if school_class.code_start is not None and any(not student.student_code for student in students):
            _assign_student_codes(db, school_class.school_id)
        stats = _student_attendance_stats(db, [student.id for student in students], _attendance_filter(school_class, member))
        # Guruhdagi o'quvchining holati (uyda, kasal) va sinfi — asl o'quvchidan
        owners = {student.id: student.source_student_id or student.id for student in students}
        away_by_owner = _active_status_codes(db, list(set(owners.values())))
        away = {student_id: away_by_owner.get(owner) for student_id, owner in owners.items()}
        home_classes = dict(
            db.execute(
                select(ClassStudent.id, SchoolClass.name)
                .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
                .where(ClassStudent.id.in_([owner for student_id, owner in owners.items() if owner != student_id] or [-1]))
            ).all()
        ) if school_class.subject else {}
        rating, rating_tests = _class_rating(db, school_class, member, [student.id for student in students])
        view["rating_tests"] = rating_tests
        view["students"] = [
            {
                "id": student.id,
                "code": student.student_code,
                "full_name": student.full_name,
                "birth_date": student.birth_date,
                "stats": stats[student.id],
                "away": away.get(student.id),  # "home" | "leave" | "sick" | None (maktabda)
                "from_class": home_classes.get(student.source_student_id),  # guruhda — o'quvchining sinfi
                "rating": rating.get(student.id),  # {"rank", "avg", "tests"} | None — testda qatnashmagan
            }
            for student in students
        ]
        query = select(Attendance).where(Attendance.class_id == school_class.id, Attendance.deleted_at.is_(None))
        scope = _attendance_filter(school_class, member)
        if scope is not None:
            # Fan o'qituvchisi faqat o'zi olgan va o'z fani bo'yicha davomatlarni ko'radi
            query = query.where(scope)
        recent = db.scalars(query.order_by(Attendance.taken_at.desc()).limit(50)).all()
        takers = {
            user.id: user.full_name
            for user in db.scalars(select(User).where(User.id.in_({item.teacher_user_id for item in recent}))).all()
        } if recent else {}
        view["attendances"] = [
            {
                "id": item.id,
                "subject": item.subject,
                "lesson_start": item.lesson_start,
                "lesson_end": item.lesson_end,
                "taken_at": item.taken_at,
                "updated_at": item.updated_at,
                "counts": _attendance_counts(item),
                "teacher_name": takers.get(item.teacher_user_id, ""),
                "can_edit": bool(member and (item.teacher_user_id == member.user_id or _is_director(member))),
            }
            for item in recent
        ]
    return view


@app.get("/api/classes")
def my_classes(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchining shu maktabdagi sinflari."""

    member = _require_school_member(db, user)
    classes = _sorted_classes(db.scalars(
        select(SchoolClass)
        .where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))
    ).all())
    views = [_class_view(db, item, member=member) for item in classes]
    views.sort(key=lambda view: (not view["is_curator"], _class_name_key(view["name"])))
    return views


@app.post("/api/classes")
def create_class(body: ClassIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _require_school_member(db, user)
    _require_head(member)

    duplicate = db.scalar(
        select(SchoolClass.id).where(
            SchoolClass.school_id == member.school_id,
            SchoolClass.deleted_at.is_(None),
            func.lower(SchoolClass.name) == body.name.lower(),
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail=f"«{body.name}» nomli sinf yoki guruh maktabda allaqachon bor")

    school_class = SchoolClass(school_id=member.school_id, teacher_user_id=user.id, name=body.name, subject=body.subject)
    db.add(school_class)
    db.commit()
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=member)


@app.get("/api/classes/{class_id}")
def class_detail(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    school_class, member = _school_class(db, user, class_id)
    return _class_view(db, school_class, with_details=True, member=member)


@app.put("/api/classes/{class_id}/curator")
def assign_curator(class_id: int, body: CuratorIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin yoki direktor sinfga kurator (sinf rahbari) tayinlaydi yoki olib tashlaydi."""

    school_class, member = _school_class(db, user, class_id)
    _require_head(member)

    if body.user_id is not None:
        teacher = db.scalar(
            select(SchoolMember).where(
                SchoolMember.school_id == member.school_id,
                SchoolMember.user_id == body.user_id,
                SchoolMember.left_at.is_(None),
            )
        )
        if not teacher:
            raise HTTPException(status_code=404, detail="Bu o'qituvchi maktabingizda yo'q")

    school_class.curator_user_id = body.user_id
    if body.user_id is not None and school_class.assistant_user_id == body.user_id:
        school_class.assistant_user_id = None
    db.commit()
    return _class_view(db, school_class, with_details=True, member=member)


@app.put("/api/classes/{class_id}/assistant")
def assign_assistant(class_id: int, body: CuratorIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Admin yoki direktor fan guruhiga yordamchi o'qituvchi tayinlaydi yoki olib tashlaydi."""

    school_class, member = _school_class(db, user, class_id)
    _require_head(member)
    if not school_class.subject:
        raise HTTPException(status_code=400, detail="Yordamchi o'qituvchi faqat fan guruhiga tayinlanadi")
    if body.user_id is not None:
        if not _active_school_member(db, member.school_id, body.user_id):
            raise HTTPException(status_code=404, detail="Bu o'qituvchi maktabingizda yo'q")
        if body.user_id == school_class.curator_user_id:
            raise HTTPException(status_code=422, detail="U — guruhning asosiy o'qituvchisi; yordamchi boshqa odam bo'lsin")
    school_class.assistant_user_id = body.user_id
    db.commit()
    return _class_view(db, school_class, with_details=True, member=member)


@app.put("/api/classes/{class_id}")
def rename_class(class_id: int, body: ClassIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf nomini o'zgartiradi (o'quvchilar va davomat tarixi o'z joyida qoladi). Faqat admin."""

    school_class, member = _school_class(db, user, class_id)
    _require_head(member)
    duplicate = db.scalar(
        select(SchoolClass.id).where(
            SchoolClass.school_id == school_class.school_id,
            SchoolClass.deleted_at.is_(None),
            SchoolClass.id != school_class.id,
            func.lower(SchoolClass.name) == body.name.lower(),
        )
    )
    if duplicate:
        raise HTTPException(status_code=409, detail=f"«{body.name}» nomli sinf maktabda allaqachon bor")
    school_class.name = body.name
    db.commit()
    return _class_view(db, school_class, with_details=True, member=member)


@app.delete("/api/classes/{class_id}")
def delete_class(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf ro'yxatdan olinadi (faqat admin); o'quvchilar va davomat tarixi bazada qoladi."""
    school_class = _director_class(db, user, class_id)
    school_class.deleted_at = utcnow()
    if not school_class.subject:
        for copy in _group_copies(db, [student.id for student in school_class.students]):
            copy.removed_at = school_class.deleted_at  # o'chirilgan sinf o'quvchilari fan guruhlaridan ham chiqadi
    db.commit()
    return {"ok": True}


@app.delete("/api/classes/{class_id}/student-codes")
def clear_class_code_range(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Sinfning ID oralig'i va o'quvchilarning ID'lari olib tashlanadi (admin yoki direktor).
    Natija va davomatlar o'quvchiga bog'langan — yo'qolmaydi. Hali skanerlanmagan titullardagi raqam ham
    bekor bo'ladi: keyin shu raqamlar boshqa sinfga berilsa, varaq begona o'quvchiga tushib qolmasin.
    """

    school_class = _director_class(db, user, class_id)
    students = _active_students(school_class)
    codes = {student.student_code for student in students if student.student_code}
    if codes:
        for attempt in db.scalars(
            select(Attempt).where(Attempt.class_student_id.in_([student.id for student in students]),
                                  Attempt.status == "pending", Attempt.sheet_number.in_(codes))
        ).all():
            attempt.sheet_number = None
    for student in students:
        student.student_code = None
    school_class.code_start = school_class.code_end = None
    db.commit()
    _assign_student_codes(db, school_class.school_id)  # fan guruhlaridagi yozuvlari ham ID'siz qoladi
    logging.getLogger("backend").info("ID oralig'i olib tashlandi: class=%s user=%s (%s ta ID)", class_id, user.id, len(codes))
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=_active_membership(db, user))


@app.put("/api/classes/{class_id}/student-codes")
def set_class_code_range(
    class_id: int, body: ClassCodeRangeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Maktab admini (direktor) sinf o'quvchilari uchun ID oralig'ini belgilaydi, masalan 1001–1038.
    O'quvchilar ro'yxatdagi tartibda shu oraliqdan ID oladi; boshqa sinf oralig'i bilan kesishmaydi.
    """

    school_class = _director_class(db, user, class_id)
    if body.end < body.start:
        raise HTTPException(status_code=422, detail="Oraliq oxiri boshidan kichik bo'lmasin")
    students = _active_students(school_class)
    size = body.end - body.start + 1
    if size < len(students):
        raise HTTPException(
            status_code=422,
            detail=f"Sinfda {len(students)} ta o'quvchi bor — oraliqda kamida {len(students)} ta raqam bo'lsin "
                   f"({body.start}–{body.start + len(students) - 1})",
        )
    others = db.scalars(
        select(SchoolClass).where(
            SchoolClass.school_id == school_class.school_id, SchoolClass.id != school_class.id,
            SchoolClass.deleted_at.is_(None), SchoolClass.code_start.is_not(None),
        )
    ).all()
    for other in others:
        if body.start <= other.code_end and other.code_start <= body.end:
            raise HTTPException(
                status_code=409,
                detail=f"Bu oraliq «{other.name}» sinfi oralig'i ({other.code_start}–{other.code_end}) bilan kesishadi",
            )

    school_class.code_start, school_class.code_end = body.start, body.end
    for student in school_class.students:  # chiqarilganlar ham oraliqdan tashqarida qolmasin
        student.student_code = None
    db.flush()
    for offset, student in enumerate(students):
        student.student_code = body.start + offset
        # Offline imtihon ro'yxatlaridagi (hali skanerlanmagan) raqam ham yangilanadi
        for attempt in db.scalars(
            select(Attempt).where(Attempt.class_student_id == student.id, Attempt.status == "pending")
        ).all():
            clash = db.scalar(
                select(Attempt.id).where(
                    Attempt.test_id == attempt.test_id, Attempt.id != attempt.id, Attempt.sheet_number == student.student_code
                )
            )
            if not clash:
                attempt.sheet_number = student.student_code
    db.commit()
    _assign_student_codes(db, school_class.school_id)  # fan guruhlaridagi yozuvlari ham yangi ID bilan
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=_active_membership(db, user))


def _member_role_label(member: SchoolMember) -> str:
    """Xabarlar uchun: "Fizika o'qituvchisi", "Direktor", "Maktab admini"."""
    if member.role == "owner":
        return "Maktab admini"
    if member.position in ALL_POSITION_LABELS:
        return ALL_POSITION_LABELS[member.position]
    try:
        subjects = json.loads(member.subjects or "[]")
    except ValueError:
        subjects = []
    return f"{subjects[0]} o'qituvchisi" if subjects else "O'qituvchi"


def _send_bot_text_in_background(chat_id: int, text: str) -> None:
    """Bot xabari alohida oqimda — so'rov Telegram javobini kutib qolmasin."""
    if not BOT_TOKEN:
        return
    threading.Thread(target=_notify_user, args=(chat_id, text), daemon=True).start()


def _add_class_student(db: Session, school_class: SchoolClass, member: SchoolMember, user: User, body: StudentIn) -> ClassStudent:
    """
    O'quvchini bittalab qo'shish — maktabning istalgan o'qituvchisi, istalgan sinfga.
    Sinfda shu ism bo'lsa — 409 (o'qituvchi tasdiqlasa, force bilan qayta yuboradi).
    Kim qo'shgani saqlanadi; kuratordan boshqa odam qo'shsa — kuratorga bot orqali xabar boradi.
    """

    _reject_new_student_in_group(school_class)
    key = _search_key(body.full_name)
    if not body.force and any(_search_key(student.full_name) == key for student in _active_students(school_class)):
        raise HTTPException(status_code=409, detail=f"«{body.full_name}» bu sinfda allaqachon bor. Baribir qo'shilsinmi?")
    student = ClassStudent(class_id=school_class.id, full_name=body.full_name, birth_date=body.birth_date,
                           added_by_user_id=user.id)
    db.add(student)
    db.commit()
    _assign_student_codes(db, school_class.school_id)
    db.refresh(student)
    logging.getLogger("backend").info("O'quvchi qo'shildi: class=%s student=%s user=%s", school_class.id, student.id, user.id)

    curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
    if curator and curator.id != user.id and curator.tg_id:
        school = db.get(PrivateSchool, school_class.school_id)
        unit = "guruhingizga" if school and school.kind == "center" else "sinfingizga"
        _send_bot_text_in_background(
            curator.tg_id,
            f"👤 {_member_role_label(member)} {user.full_name} «{school_class.name}» {unit} "
            f"{student.full_name}ni qo'shdi.\n\nXato bo'lsa, o'quvchi profilidan o'chiring.",
        )
    return student


def _reject_new_student_in_group(school_class: SchoolClass) -> None:
    if school_class.subject:
        raise HTTPException(status_code=422, detail="Guruhga o'quvchi maktab ro'yxatidan qo'shiladi («Maktabdan qo'shish»)")


def _group_copies(db: Session, source_ids: list[int]) -> list[ClassStudent]:
    """Fan guruhlaridagi (faol) yozuvlari — asl o'quvchilar bo'yicha."""
    if not source_ids:
        return []
    return db.scalars(
        select(ClassStudent).where(ClassStudent.source_student_id.in_(source_ids), ClassStudent.removed_at.is_(None))
    ).all()


def _group_class(db: Session, user: User, class_id: int) -> tuple[SchoolClass, SchoolMember]:
    """Fan guruhi — o'quvchi qo'shish/chiqarish: admin, direktor yoki guruh o'qituvchisi."""
    school_class, member = _school_class(db, user, class_id)
    if not school_class.subject:
        raise HTTPException(status_code=400, detail="Bu oddiy sinf — o'quvchi maktab ro'yxatidan faqat fan guruhiga qo'shiladi")
    if not _can_manage_class(school_class, member):
        raise HTTPException(status_code=403, detail="Guruhga o'quvchini admin, direktor yoki guruh o'qituvchisi qo'shadi")
    return school_class, member


@app.get("/api/classes/{class_id}/candidates")
def group_candidates(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Fan guruhiga qo'shish uchun maktab o'quvchilari (sinfi bilan), shu guruhdagilarsiz. Shu fanning
    boshqa guruhidagilar — o'sha guruh nomi bilan (qo'shilsa, u yerdan bu guruhga o'tadi).
    """

    school_class, _member = _group_class(db, user, class_id)
    rows = db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == school_class.school_id, SchoolClass.deleted_at.is_(None),
               SchoolClass.subject.is_(None), ClassStudent.removed_at.is_(None), ClassStudent.source_student_id.is_(None))
    ).all()
    same_subject = db.execute(
        select(ClassStudent.source_student_id, SchoolClass.id, SchoolClass.name)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == school_class.school_id, SchoolClass.deleted_at.is_(None),
               func.lower(SchoolClass.subject) == school_class.subject.lower(),
               ClassStudent.removed_at.is_(None), ClassStudent.source_student_id.is_not(None))
    ).all()
    here = {source for source, group_id, _ in same_subject if group_id == school_class.id}
    elsewhere = {source: name for source, group_id, name in same_subject if group_id != school_class.id}
    rows = sorted(rows, key=lambda row: (_class_name_key(row[1].name), _uz_sort_key(row[0].full_name), row[0].id))
    return [
        {"id": student.id, "name": student.full_name, "class_name": home.name, "code": student.student_code,
         "group": elsewhere.get(student.id)}
        for student, home in rows
        if student.id not in here
    ]


@app.post("/api/classes/{class_id}/members")
def add_group_members(class_id: int, body: GroupMembersIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """
    Fan guruhiga maktab o'quvchilarini qo'shish (yotoqxonadagi joylashtirish kabi). O'quvchi o'z sinfida
    qoladi; guruhda unga bog'langan yozuv paydo bo'ladi (ismi va ID'si sinfidagidan). Bir fanda — bitta
    guruh: shu fanning boshqa guruhida bo'lsa, o'sha guruhdan bu guruhga o'tadi.
    """

    school_class, member = _group_class(db, user, class_id)
    now = utcnow()
    current = {student.source_student_id for student in _active_students(school_class)}
    added = moved = 0
    for student_id in dict.fromkeys(body.student_ids):
        source = db.get(ClassStudent, student_id)
        home = db.get(SchoolClass, source.class_id) if source else None
        if (not source or source.removed_at is not None or source.source_student_id is not None or not home
                or home.school_id != school_class.school_id or home.deleted_at is not None or home.subject):
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        if source.id in current:
            continue
        for copy, _group in db.execute(
            select(ClassStudent, SchoolClass)
            .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
            .where(ClassStudent.source_student_id == source.id, ClassStudent.removed_at.is_(None),
                   SchoolClass.id != school_class.id, SchoolClass.deleted_at.is_(None),
                   func.lower(SchoolClass.subject) == school_class.subject.lower())
        ).all():
            copy.removed_at = now
            moved += 1
        db.add(ClassStudent(class_id=school_class.id, full_name=source.full_name, birth_date=source.birth_date,
                            student_code=source.student_code, added_by_user_id=user.id, source_student_id=source.id))
        current.add(source.id)
        added += 1
    db.commit()
    _assign_student_codes(db, school_class.school_id)  # boshqa guruhdan o'tganlarning eski yozuvi — ID'siz
    db.refresh(school_class)
    return {"added": added, "moved": moved, "class": _class_view(db, school_class, with_details=True, member=member)}


@app.post("/api/classes/{class_id}/students")
def add_student(class_id: int, body: StudentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Bittalab qo'shish — har bir o'qituvchi. Excel'dan yuklash, tahrirlash, o'chirish — rahbariyat va kurator."""
    school_class, member = _school_class(db, user, class_id)
    _add_class_student(db, school_class, member, user, body)
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=member)


@app.post("/api/classes/{class_id}/students/quick")
def add_student_from_attendance(class_id: int, body: StudentIn, user: User = Depends(get_current_user),
                                db: Session = Depends(get_db)):
    """Davomat paytida ro'yxatda yo'q o'quvchini shu joyda qo'shish (qoidalari — _add_class_student)."""

    school_class, member = _school_class(db, user, class_id)
    student = _add_class_student(db, school_class, member, user, body)
    return {"id": student.id, "full_name": student.full_name, "code": student.student_code}


@app.post("/api/classes/{class_id}/students/import/preview")
def preview_student_import(
    class_id: int, body: StudentImportIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Excel/CSV fayl yoki joylangan matndan ismlarni ajratadi — hali SAQLAMAYDI.
    O'qituvchi ro'yxatni ko'rib, tasdiqlagandan keyingina qo'shiladi.
    """

    school_class = _managed_class(db, user, class_id)
    _reject_new_student_in_group(school_class)
    try:
        if body.file_base64:
            try:
                data = base64.b64decode(body.file_base64.split(",")[-1], validate=True)
            except (binascii.Error, ValueError):
                raise ValueError("Fayl buzilgan — qayta tanlab ko'ring")
            names = parse_student_file(body.file_name or "", data)
        elif body.text:
            names = names_from_rows(rows_from_text(body.text))
        else:
            raise ValueError("Fayl yoki ro'yxat yuborilmadi")
    except ValueError as error:
        raise HTTPException(status_code=422, detail=str(error))

    if not names:
        raise HTTPException(status_code=422, detail="Ro'yxatdan birorta ham ism topilmadi")

    existing = {student.full_name.lower() for student in _active_students(school_class)}
    return {
        "names": [name for name in names if name.lower() not in existing],
        "already_in_class": [name for name in names if name.lower() in existing],
    }


@app.post("/api/classes/{class_id}/students/bulk")
def add_students_bulk(
    class_id: int, body: StudentsBulkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Ko'rib chiqilgan ro'yxatni bir yo'la qo'shadi; sinfda bor ismlar takrorlanmaydi."""

    school_class = _managed_class(db, user, class_id)
    _reject_new_student_in_group(school_class)
    existing = {student.full_name.lower() for student in _active_students(school_class)}

    added = 0
    for raw_name in body.names:
        name = " ".join(str(raw_name).split())[:200]
        if len(name) < 2 or name.lower() in existing:
            continue
        existing.add(name.lower())
        db.add(ClassStudent(class_id=school_class.id, full_name=name))
        added += 1

    db.commit()
    _assign_student_codes(db, school_class.school_id)
    db.refresh(school_class)
    return {"added": added, "class": _class_view(db, school_class, with_details=True, member=_active_membership(db, user))}


@app.put("/api/classes/{class_id}/students/{student_id}")
def edit_student(
    class_id: int, student_id: int, body: StudentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Ism-familiyani maktabning istalgan o'qituvchisi tahrirlaydi (istalgan sinfda) — xatoni ko'rgan odam
    o'zi tuzatadi. Tug'ilgan sanani faqat rahbariyat va sinf kuratori o'zgartiradi. Kuratordan boshqa
    odam ismni o'zgartirsa — kuratorga bot orqali xabar boradi (eski va yangi ismi bilan).
    """

    school_class, member = _school_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    if student.source_student_id is not None:
        raise HTTPException(status_code=422, detail="Ism-familiya o'quvchining o'z sinfida tahrirlanadi")
    old_name = student.full_name
    student.full_name = body.full_name
    if _can_manage_class(school_class, member):
        student.birth_date = body.birth_date
    for copy in _group_copies(db, [student.id]):
        copy.full_name, copy.birth_date = student.full_name, student.birth_date
    db.commit()
    if old_name != student.full_name:
        logging.getLogger("backend").info("O'quvchi ismi o'zgardi: class=%s student=%s user=%s", class_id, student.id, user.id)
        curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
        if curator and curator.id != user.id and curator.tg_id:
            school = db.get(PrivateSchool, school_class.school_id)
            unit = "guruhingizdagi" if school and school.kind == "center" else "sinfingizdagi"
            _send_bot_text_in_background(
                curator.tg_id,
                f"✏️ {_member_role_label(member)} {user.full_name} «{school_class.name}» {unit} o'quvchi ismini "
                f"o'zgartirdi:\n{old_name} → {student.full_name}",
            )
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=member)


@app.put("/api/classes/{class_id}/students/{student_id}/contact")
def edit_student_contact(
    class_id: int, student_id: int, body: StudentContactIn, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """O'quvchining yashash joyi (viloyat, tuman, MFY, ko'cha) va telefoni — rahbariyat yoki kurator yozadi."""

    school_class, member = _school_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    if student.source_student_id is not None:
        raise HTTPException(status_code=422, detail="Ma'lumotlar o'quvchining o'z sinfida tahrirlanadi")
    if not _can_manage_class(school_class, member):
        raise HTTPException(status_code=403, detail="Manzil va telefonni rahbariyat yoki sinf kuratori kiritadi")
    student.region, student.district, student.mahalla = body.region, body.district, body.mahalla
    student.address, student.phone = body.address, body.phone
    student.grade = body.grade
    db.commit()
    return {"region": student.region, "district": student.district, "mahalla": student.mahalla,
            "address": student.address, "phone": student.phone, "grade": student.grade}


def _short_place(name: str | None, suffix: str) -> str:
    """"Farg'ona viloyati" → "Farg'ona", "Uchko'prik tumani" → "Uchko'prik" (shaharlar o'z nomida qoladi)."""

    name = (name or "").strip()
    return name[: -len(suffix)].strip() if name.endswith(suffix) else name


def _local_phone(phone: str | None) -> str:
    """"+998901234567" → "901234567" (ro'yxatlarda odatdagidek 9 xonali)."""

    digits = re.sub(r"\D", "", phone or "")
    return digits[3:] if len(digits) == 12 and digits.startswith("998") else digits


def generate_class_students_xlsx(db: Session, school_class: SchoolClass) -> bytes:
    """
    Sinf o'quvchilarining umumiy ma'lumotlari (Excel): №, F.I.Sh., tug'ilgan kun, viloyat, tuman, MFY,
    ko'cha/uy, sinf, telefon — maktablarda topshiriladigan ro'yxat ko'rinishida (alifbo tartibida).
    Fan guruhida ma'lumotlar o'quvchining asl (sinfdagi) yozuvidan olinadi.
    """

    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side

    students = _active_students(school_class)
    source_ids = [student.source_student_id for student in students if student.source_student_id]
    sources = {row.id: row for row in db.scalars(select(ClassStudent).where(ClassStudent.id.in_(source_ids))).all()} if source_ids else {}

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = (re.sub(r"[\\/*?:\[\]]", "-", school_class.name).strip() or "Sinf")[:31]

    font = Font(name="Times New Roman", size=11)
    head_font = Font(name="Times New Roman", size=11, bold=True)
    head_fill = PatternFill("solid", fgColor="C6E0B4")
    thin = Side(style="thin", color="000000")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)
    center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left = Alignment(horizontal="left", vertical="center", wrap_text=True)

    header = ["№", school_class.name, "Tug'ilgan kun", "Viloyat", "Tuman", "MFY", "Ko'cha, Uy", "Sinf", "Tel. raqam"]
    sheet.append(header)
    for column, _ in enumerate(header, start=1):
        cell = sheet.cell(row=1, column=column)
        cell.font, cell.fill, cell.border = head_font, head_fill, border
        cell.alignment = left if column == 2 else center

    for index, student in enumerate(students, start=1):
        info = sources.get(student.source_student_id) or student
        birth = info.birth_date or student.birth_date
        sheet.append([
            index,
            student.full_name,
            birth.strftime("%d.%m.%Y") if birth else "",
            _short_place(info.region, "viloyati"),
            _short_place(info.district, "tumani"),
            info.mahalla or "",
            info.address or "",
            info.grade if info.grade else "",
            _local_phone(info.phone),
        ])
        row = index + 1
        for column in range(1, len(header) + 1):
            cell = sheet.cell(row=row, column=column)
            cell.font, cell.border = font, border
            cell.alignment = left if column == 2 else center
            if column == 9:
                cell.number_format = "@"  # telefon — matn (Excel boshidagi nolni yoki ko'rinishini buzmasin)

    for column, width in zip("ABCDEFGHI", (5, 32, 13, 12, 14, 22, 24, 7, 13)):
        sheet.column_dimensions[column].width = width
    sheet.freeze_panes = "C2"
    sheet.print_options.gridLines = False
    sheet.page_setup.orientation = "landscape"
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0
    sheet.sheet_properties.pageSetUpPr.fitToPage = True

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _class_students_file(db: Session, user: User, class_id: int) -> tuple[str, bytes]:
    school_class, member = _school_class(db, user, class_id)
    if not _can_manage_class(school_class, member):
        raise HTTPException(status_code=403, detail="O'quvchilar ma'lumotlarini rahbariyat yoki sinf kuratori yuklab oladi")
    file_name = re.sub(r"[\\/:*?\"<>|]", "-", f"{school_class.name} (umumiy ma'lumotlar)").strip()
    return file_name, generate_class_students_xlsx(db, school_class)


@app.get("/api/classes/{class_id}/students/file")
def class_students_file(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf o'quvchilarining umumiy ma'lumotlari — Excel (brauzerda yuklab olinadi)."""
    file_name, data = _class_students_file(db, user, class_id)
    ascii_name = file_name.encode("ascii", "ignore").decode().replace("'", "") or "oquvchilar"
    return StreamingResponse(
        io.BytesIO(data), media_type=XLSX_MIME,
        headers={"Content-Disposition": f'attachment; filename="{ascii_name}.xlsx"'},
    )


@app.post("/api/classes/{class_id}/students/file/send")
def send_class_students_file(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf o'quvchilari ro'yxati (Excel) — bot orqali foydalanuvchining o'z chatiga."""
    file_name, data = _class_students_file(db, user, class_id)
    _send_pdf_to_telegram(user.tg_id, f"{file_name}.xlsx", data, caption="📋 O'quvchilarning umumiy ma'lumotlari", mime=XLSX_MIME)
    return {"ok": True}


# O'quvchi holati: maktabda / uyda / ruxsat berilgan (javob) / kasal
STUDENT_STATUSES = {
    "at_school": ("Maktabda", "🏫"),
    "home": ("Uyda", "🏠"),
    "leave": ("Ruxsat berilgan", "📝"),
    "sick": ("Kasal", "🤒"),
}
# Holat sinf davomati va kechki yo'qlamaga oldindan qanday tushadi
STATUS_TO_ATTENDANCE = {"sick": "excused", "leave": "excused", "home": "excused"}
STATUS_TO_DORM = {"sick": "sick", "leave": "excused", "home": "excused"}


def _status_view(db: Session, row: StudentStatus | None) -> dict:
    """Holat ko'rinishi. until o'tib ketgan bo'lsa — yana "maktabda" (expired belgisi bilan)."""

    if row is None:
        return {"status": "at_school", "label": "Maktabda", "icon": "🏫", "note": "", "until": None,
                "set_at": None, "set_by": "", "expired": False}
    expired = row.until is not None and row.until < _dorm_today()
    status = "at_school" if expired else row.status
    label, icon = STUDENT_STATUSES[status]
    setter = db.get(User, row.set_by)
    return {"status": status, "label": label, "icon": icon, "note": "" if expired else row.note,
            "until": None if expired else row.until, "set_at": row.created_at,
            "set_by": setter.full_name if setter else "", "expired": expired}


def _current_statuses(db: Session, student_ids: list[int]) -> dict[int, StudentStatus]:
    """Har o'quvchining eng oxirgi holat yozuvi (muddati o'tganini _status_view hal qiladi)."""

    if not student_ids:
        return {}
    latest = (
        select(func.max(StudentStatus.id))
        .where(StudentStatus.student_id.in_(student_ids))
        .group_by(StudentStatus.student_id)
    )
    rows = db.scalars(select(StudentStatus).where(StudentStatus.id.in_(latest))).all()
    return {row.student_id: row for row in rows}


def _active_status_codes(db: Session, student_ids: list[int]) -> dict[int, str]:
    """Hozir maktabda bo'lmaganlar: {o'quvchi_id: "home"|"leave"|"sick"} (muddati o'tganlar kirmaydi)."""

    today = _dorm_today()
    return {
        student_id: row.status
        for student_id, row in _current_statuses(db, student_ids).items()
        if row.status != "at_school" and (row.until is None or row.until >= today)
    }


def _student_dorm_room(db: Session, student_id: int) -> DormRoom | None:
    return db.scalar(
        select(DormRoom)
        .join(DormResident, DormResident.room_id == DormRoom.id)
        .where(DormResident.student_id == student_id, DormResident.left_at.is_(None), DormRoom.deleted_at.is_(None))
    )


def _can_set_student_status(db: Session, member: SchoolMember, school_class: SchoolClass, student: ClassStudent) -> bool:
    """Holatni o'zgartiradi: rahbariyat, sinf kuratori yoki o'quvchi yashaydigan xonaning tarbiyachisi."""

    if _is_director(member) or _is_class_teacher(school_class, member.user_id):
        return True
    room = _student_dorm_room(db, student.id)
    return bool(room and (room.warden_user_id == member.user_id or member.position == "commandant"))


def _live_class_tests():
    """Sinf testi o'chirilmagan va (skanerlangan bo'lsa) Test markazidagi asl imtihoni ham o'chirilmagan."""
    return (
        ClassTest.deleted_at.is_(None),
        or_(
            ClassTest.source_test_id.is_(None),
            ClassTest.source_test_id.not_in(select(Test.id).where(Test.deleted_at.is_not(None))),
        ),
    )


def _student_results(db: Session, student: ClassStudent) -> list[dict]:
    """
    O'quvchining barcha natijalari: sinf testlari (o'qituvchi kiritgan yoki skanerlangan) va Test
    markazidagi natijalar. Profilni ochgan har kim hammasini ko'radi — reyting shulardan chiqadi.
    """

    results = []
    class_tests = db.execute(
        select(ClassTestScore, ClassTest)
        .join(ClassTest, ClassTest.id == ClassTestScore.test_id)
        .where(ClassTestScore.student_id == student.id, *_live_class_tests())
    ).all()
    attempts = db.execute(
        select(Attempt, Test)
        .join(Test, Test.id == Attempt.test_id)
        .where(Attempt.class_student_id == student.id, Attempt.status == "done", Test.deleted_at.is_(None))
    ).all()

    linked = set()
    for score, test in class_tests:
        if test.source_test_id:
            linked.add(test.source_test_id)
        if score.score is None:
            continue
        results.append({
            "title": test.title, "subject": test.subject, "date": test.taken_on,
            "score": score.score, "max_score": test.max_score,
            "percent": round(score.score / test.max_score * 100, 1) if test.max_score else None,
            "source": "class",
        })

    for attempt, test in attempts:
        if test.id in linked:
            continue
        finished = attempt.finished_at or attempt.started_at
        results.append({
            "title": test.title, "subject": test.subject, "date": (finished + TASHKENT_OFFSET).date(),
            "score": attempt.correct, "max_score": attempt.correct + attempt.wrong,
            "percent": attempt.percent, "source": "center",
        })
    results.sort(key=lambda item: item["date"], reverse=True)
    return results[:30]


def _student_dorm_view(db: Session, student: ClassStudent) -> dict | None:
    room = _student_dorm_room(db, student.id)
    if not room:
        return None
    building = db.get(DormBuilding, room.building_id) if room.building_id else None
    warden = db.get(User, room.warden_user_id) if room.warden_user_id else None
    last = db.execute(
        select(DormCheck.check_date, DormCheckMark.status)
        .join(DormCheckMark, DormCheckMark.check_id == DormCheck.id)
        .where(DormCheckMark.student_id == student.id, DormCheck.room_id == room.id)
        .order_by(DormCheck.check_date.desc())
        .limit(1)
    ).first()
    return {
        "room_id": room.id, "room": room.name, "floor": room.floor,
        "building": building.name if building else None,
        "warden": warden.full_name if warden else None,
        "last_check": {"date": last[0], "status": last[1], "label": DORM_STATUSES.get(last[1], last[1])} if last else None,
    }


def _student_payment_summary(db: Session, school_class: SchoolClass, student: ClassStudent) -> dict:
    school = db.get(PrivateSchool, school_class.school_id)
    paid = _paid_sums(db, [student.id])
    rows = [{"month": month, **_month_status(_student_due(school, student, month), paid.get((student.id, month), 0))}
            for month in _student_months(student)]
    current = rows[0] if rows else None
    return {
        "total_debt": sum(row["remaining"] for row in rows),
        "debt_months": sum(1 for row in rows if row["remaining"] > 0),
        "current": {**current, "label": _month_label(current["month"])} if current else None,
        "monthly_fee": _class_fee(school, school_class),
        "discount": student.discount_percent or 0,
    }


def _status_history(db: Session, student_id: int) -> list[dict]:
    rows = db.scalars(select(StudentStatus).where(StudentStatus.student_id == student_id)
                      .order_by(StudentStatus.id.desc()).limit(20)).all()
    result = []
    for row in rows:
        setter = db.get(User, row.set_by)
        label, icon = STUDENT_STATUSES[row.status]
        result.append({"status": row.status, "label": label, "icon": icon, "note": row.note, "until": row.until,
                       "set_at": row.created_at, "set_by": setter.full_name if setter else ""})
    return result


@app.get("/api/classes/{class_id}/students/{student_id}")
def student_profile(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi haqida: ma'lumotlari, davomat statistikasi va har darsdagi holati."""

    school_class, member = _school_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    # Guruhdagi o'quvchining holati va yotoqxonasi — asl (sinfdagi) yozuvidan
    owner = db.get(ClassStudent, student.source_student_id) if student.source_student_id else student
    owner = owner or student

    scope = _attendance_filter(school_class, member)
    history_query = (
        select(Attendance.id, Attendance.taken_at, Attendance.subject, AttendanceMark.status)
        .join(AttendanceMark, AttendanceMark.attendance_id == Attendance.id)
        .where(AttendanceMark.student_id == student.id, Attendance.deleted_at.is_(None))
        .order_by(Attendance.taken_at.desc())
        .limit(100)
    )
    if scope is not None:
        history_query = history_query.where(scope)
    history = db.execute(history_query).all()

    if not student.student_code:
        _assign_student_codes(db, school_class.school_id)
    curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
    added_by = db.get(User, student.added_by_user_id) if student.added_by_user_id else None
    return {
        "id": student.id,
        "code": student.student_code,
        "class_id": school_class.id,
        "class_name": school_class.name,
        "full_name": student.full_name,
        "birth_date": owner.birth_date,
        # Yashash joyi va telefon — asl (sinfdagi) yozuvdan
        "region": owner.region,
        "district": owner.district,
        "mahalla": owner.mahalla,
        "address": owner.address,
        "phone": owner.phone,
        "grade": owner.grade,
        "can_edit_contact": student.source_student_id is None and _can_manage_class(school_class, member),
        "created_at": student.created_at,
        "added_by": added_by.full_name if added_by else None,
        "removed": student.removed_at is not None,
        "stats": _student_attendance_stats(db, [student.id], scope)[student.id],
        "history": [
            {"attendance_id": aid, "taken_at": taken_at, "subject": subject, "status": status}
            for aid, taken_at, subject, status in history
        ],
        # O'quvchi markazi: holat, sinf, yotoqxona, natijalar, to'lov
        "status": _status_view(db, _current_statuses(db, [owner.id]).get(owner.id)),
        "status_history": _status_history(db, owner.id),
        "can_set_status": _can_set_student_status(db, member, school_class, student),
        "can_manage": _can_manage_class(school_class, member),
        "can_edit_name": student.source_student_id is None,  # maktabning har bir o'qituvchisi (o'z sinfidagi yozuvda)
        "curator": curator.full_name if curator else None,
        "dorm": _student_dorm_view(db, owner),
        "results": _student_results(db, student),
        "payments": (
            _student_payment_summary(db, school_class, student)
            if _can_see_payments(school_class, member) and not school_class.subject else None
        ),
    }


@app.put("/api/classes/{class_id}/students/{student_id}/status")
def set_student_status(class_id: int, student_id: int, body: StudentStatusIn, user: User = Depends(get_current_user),
                       db: Session = Depends(get_db)):
    """Holatni yozadi (yangi qator — tarix saqlanadi). Rahbariyat, kurator yoki xona tarbiyachisi."""

    school_class, member = _school_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    if not _can_set_student_status(db, member, school_class, student):
        raise HTTPException(status_code=403, detail="Holatni rahbariyat, sinf kuratori yoki tarbiyachi o'zgartiradi")
    if body.until is not None and body.until < _dorm_today():
        raise HTTPException(status_code=422, detail="Sana o'tib ketgan — bugungi yoki keyingi kunni tanlang")
    row = StudentStatus(school_id=school_class.school_id, student_id=student.source_student_id or student.id,
                        status=body.status, note=body.note, until=body.until, set_by=user.id)
    db.add(row)
    db.commit()
    return _status_view(db, row)


@app.delete("/api/classes/{class_id}/students/{student_id}")
def remove_student(class_id: int, student_id: int, body: StudentRemoveIn | None = None,
                   user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    O'quvchi sinfdan chiqariladi; eski davomatlarda u ko'rinib turadi. Oddiy sinfdan chiqarishda sabab
    majburiy — o'quvchi "Biriktirilmaganlar" ro'yxatiga tushadi (u yerdan qayta biriktiriladi yoki o'chiriladi).
    Fan guruhidan chiqarilsa — o'z sinfida qoladi, sabab shart emas.
    """
    school_class = _managed_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    reason = body.reason if body else ""
    if not school_class.subject:
        if len(reason) < 3:
            raise HTTPException(status_code=422, detail="Chiqarish sababini yozing (masalan: boshqa maktabga o'tdi)")
        student.removed_reason = reason
        student.removed_by_user_id = user.id
    student.removed_at = utcnow()
    if student.source_student_id is None:
        for copy in _group_copies(db, [student.id]):  # sinfdan chiqqan o'quvchi fan guruhlaridan ham chiqadi
            copy.removed_at = student.removed_at
    db.commit()
    _assign_student_codes(db, school_class.school_id)  # ID'si bo'shaydi
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=_active_membership(db, user))


@app.post("/api/classes/{class_id}/attendance")
def take_attendance(class_id: int, body: AttendanceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Davomatni saqlaydi. Ro'yxatda belgilanmagan o'quvchi "bor" hisoblanadi
    (davomat oynasida hamma oldindan "bor" turadi).
    """

    school_class, member = _school_class(db, user, class_id)
    students = _active_students(school_class)
    if not students:
        raise HTTPException(status_code=400, detail="Sinfda hali o'quvchi yo'q — avval o'quvchilarni qo'shing")

    attendance = Attendance(
        class_id=school_class.id,
        teacher_user_id=user.id,
        subject=body.subject,
        lesson_start=body.lesson_start,
        lesson_end=body.lesson_end,
    )
    db.add(attendance)
    db.flush()
    for student in students:
        db.add(
            AttendanceMark(
                attendance_id=attendance.id,
                student_id=student.id,
                student_name=student.full_name,
                status=body.marks.get(student.id, "present"),
            )
        )
    db.commit()
    db.refresh(attendance)
    return _attendance_view(attendance)


@app.get("/api/attendance/{attendance_id}")
def attendance_detail(attendance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    attendance, member = _school_attendance(db, user, attendance_id)
    view = _attendance_view(attendance)
    taker = db.get(User, attendance.teacher_user_id)
    view["teacher_name"] = taker.full_name if taker else ""
    view["can_edit"] = attendance.teacher_user_id == user.id or _is_director(member)
    return view


@app.put("/api/attendance/{attendance_id}")
def edit_attendance(
    attendance_id: int, body: AttendanceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Davomatni tahrirlaydi: birinchi olingan vaqt saqlanadi, tahrir vaqti alohida yoziladi.
    Tahrirlashi mumkin: davomatni olgan o'qituvchi yoki admin."""

    attendance, member = _school_attendance(db, user, attendance_id)
    if attendance.teacher_user_id != user.id and not _is_director(member):
        raise HTTPException(status_code=403, detail="Bu davomatni boshqa o'qituvchi olgan — uni faqat o'zi yoki admin tahrirlaydi")
    changed = False
    for mark in attendance.marks:
        new_status = body.marks.get(mark.student_id)
        if new_status and new_status != mark.status:
            mark.status = new_status
            changed = True
    # Tahrirlashda qo'shilgan yangi o'quvchi (davomat oynasida "+ O'quvchi qo'shish") — belgisi yoziladi
    marked = {mark.student_id for mark in attendance.marks}
    school_class = db.get(SchoolClass, attendance.class_id)
    for student in _active_students(school_class):
        if student.id not in marked and student.id in body.marks:
            db.add(AttendanceMark(attendance_id=attendance.id, student_id=student.id, student_name=student.full_name,
                                  status=body.marks[student.id]))
            changed = True
    if body.subject and body.subject != attendance.subject:
        attendance.subject = body.subject
        changed = True
    if body.lesson_start and body.lesson_end and (body.lesson_start, body.lesson_end) != (
        attendance.lesson_start,
        attendance.lesson_end,
    ):
        attendance.lesson_start, attendance.lesson_end = body.lesson_start, body.lesson_end
        changed = True

    if changed:
        attendance.updated_at = utcnow()
    db.commit()
    db.refresh(attendance)
    return _attendance_view(attendance)


def _attendance_when(attendance: Attendance) -> str:
    """Sana, keyin dars vaqti: "09.10.2026 · 11:00–11:45". Vaqt kiritilmagan
    eski davomatlarda — olingan vaqt."""

    taken = _local_time(attendance.taken_at)
    if attendance.lesson_start and attendance.lesson_end:
        return f"{taken[:10]} · {attendance.lesson_start}–{attendance.lesson_end}"
    return taken.replace(" ", " · ", 1)


@app.delete("/api/attendance/{attendance_id}")
def delete_attendance(attendance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Davomatni o'chiradi — uni olgan o'qituvchi yoki rahbariyat. Yozuv bazada
    qoladi (deleted_at), tarix va statistikadan olib tashlanadi.
    """

    attendance, member = _school_attendance(db, user, attendance_id)
    if attendance.teacher_user_id != user.id and not _is_director(member):
        raise HTTPException(status_code=403, detail="Bu davomatni boshqa o'qituvchi olgan — uni faqat o'zi yoki rahbariyat o'chiradi")
    attendance.deleted_at = utcnow()
    attendance.deleted_by = user.id
    db.commit()
    logging.getLogger("backend").info("Davomat o'chirildi: id=%s user=%s", attendance.id, user.id)
    return {"ok": True}


# ---------- Sinf test natijalari (o'qituvchi kiritadi) ----------
#
# Hisoblash — oddiy test rejimida: to'plangan ball / maksimal ball (21/25) va foiz (84%).
# Milliy sertifikat darajalari (A+ … NC) ishlatilmaydi.


def _score_level(percent: float) -> str:
    """Diagramma rangi: 71%+ — yashil, 56–70% — sariq, qolgani — qizil (5/4, 3, 2 baholar chegarasi)."""
    return "good" if percent >= 71 else "mid" if percent >= 56 else "low"


def _can_see_class_test(test: ClassTest, member: SchoolMember) -> bool:
    """Ko'radi: testni kiritgan o'qituvchi, admin, direktor va zam direktor."""
    return test.teacher_user_id == member.user_id or _is_director(member)


def _class_test_view(db: Session, test: ClassTest, member: SchoolMember, with_scores: bool = True) -> dict:
    rows = []
    for item in test.scores:
        percent = round(item.score / test.max_score * 100, 1) if item.score is not None else None
        rows.append({
            "student_id": item.student_id,
            "name": item.student_name,
            "score": item.score,
            "percent": percent,
            "level": _score_level(percent) if percent is not None else None,
        })
    # Eng ko'p yechganlar tepada; qatnashmaganlar — oxirida
    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["name"].lower()))
    # Teng ball — teng o'rin, keyingi o'rin tashlab ketilmaydi: 1, 1, 2, 3 (sinf reytingidagi kabi)
    rank = 0
    for index, row in enumerate(rows):
        if row["score"] is None:
            continue
        if index == 0 or row["score"] != rows[index - 1]["score"]:
            rank += 1
        row["rank"] = rank

    taken = [row for row in rows if row["score"] is not None]
    teacher = db.get(User, test.teacher_user_id)
    view = {
        "id": test.id,
        "class_id": test.class_id,
        "title": test.title,
        "subject": test.subject,
        "taken_on": test.taken_on,
        "max_score": test.max_score,
        "teacher_name": teacher.full_name if teacher else "",
        "participants": len(taken),
        "total": len(rows),
        "average_percent": round(sum(row["percent"] for row in taken) / len(taken), 1) if taken else None,
        "best": taken[0]["name"] if taken else None,
        "can_edit": test.teacher_user_id == member.user_id or _is_director(member),
        "updated_at": test.updated_at,
    }
    if with_scores:
        view["scores"] = rows
    return view


def _visible_class_test(db: Session, user: User, test_id: int) -> tuple[ClassTest, SchoolMember]:
    test = db.get(ClassTest, test_id)
    if not test or test.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    _, member = _school_class(db, user, test.class_id)
    if not _can_see_class_test(test, member):
        raise HTTPException(status_code=403, detail="Bu test natijalarini faqat uni kiritgan o'qituvchi va rahbariyat ko'radi")
    return test, member


def _save_scores(db: Session, test: ClassTest, school_class: SchoolClass, scores: dict[int, float | None]) -> None:
    """Sinfning har bir o'quvchisi uchun ball (yo'q bo'lsa — qatnashmagan)."""
    existing = {item.student_id: item for item in test.scores}
    for student in _active_students(school_class):
        value = scores.get(student.id)
        if student.id in existing:
            existing[student.id].score = value
        else:
            db.add(ClassTestScore(test_id=test.id, student_id=student.id, student_name=student.full_name, score=value))


@app.get("/api/classes/{class_id}/tests")
def list_class_tests(class_id: int, manual: bool = False, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """
    Sinf test natijalari. manual=true — faqat qo'lda kiritilganlar (sinfning "Sinov" bo'limi uchun:
    skanerlangan imtihon natijasi imtihonning o'z kartochkasida ko'rinadi, ikki marta chiqmasin).
    """
    school_class, member = _school_class(db, user, class_id)
    query = select(ClassTest).where(ClassTest.class_id == school_class.id, ClassTest.deleted_at.is_(None))
    if manual:
        query = query.where(ClassTest.source_test_id.is_(None))
    tests = db.scalars(query.order_by(ClassTest.taken_on.desc(), ClassTest.id.desc())).all()
    return [_class_test_view(db, test, member, with_scores=False) for test in tests if _can_see_class_test(test, member)]


@app.get("/api/classes/{class_id}/center-tests")
def class_center_tests(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Test markazidan shu sinfga biriktirilgan imtihonlar: nechta o'quvchi
    skanerlangan, kimning testi va sinf natijalaridagi yozuvi (bo'lsa).
    """

    school_class, member = _school_class(db, user, class_id)
    student_ids = [student.id for student in school_class.students]
    if not student_ids:
        return []
    counts = db.execute(
        select(Attempt.test_id, Attempt.status, func.count())
        .where(Attempt.class_student_id.in_(student_ids), Attempt.status.in_(("pending", "done")))
        .group_by(Attempt.test_id, Attempt.status)
    ).all()
    by_test: dict[int, dict[str, int]] = {}
    for test_id, status, count in counts:
        by_test.setdefault(test_id, {})[status] = count
    if not by_test:
        return []
    tests = db.scalars(
        select(Test).where(Test.id.in_(list(by_test)), Test.deleted_at.is_(None)).order_by(Test.id.desc())
    ).all()
    class_tests = {
        item.source_test_id: item
        for item in db.scalars(
            select(ClassTest).where(
                ClassTest.class_id == school_class.id, ClassTest.source_test_id.in_(list(by_test)), ClassTest.deleted_at.is_(None)
            )
        )
    }
    result = []
    for test in tests:
        owner = db.get(Teacher, test.teacher_id)
        done = by_test[test.id].get("done", 0)
        result.append({
            "id": test.id,
            "title": test.title,
            "code": test.code,
            "subject": test.subject,
            "status": test.status,
            "created_at": test.created_at,
            "teacher": owner.user.full_name if owner and owner.user else "",
            "is_mine": bool(owner and owner.user_id == user.id),
            # Sinfdan olib tashlash — test egasi yoki admin/direktor
            "can_remove": bool(owner and owner.user_id == user.id) or _can_delete_classes(member),
            "done": done,
            "total": done + by_test[test.id].get("pending", 0),
            **_center_test_results(db, class_tests.get(test.id), member),
        })
    return result


def _center_test_results(db: Session, class_test: ClassTest | None, member: SchoolMember) -> dict:
    """Imtihonning sinfdagi natijalari (skanerlangandan keyin): o'rtacha foiz — faqat natijani ko'ra oladiganga."""
    visible = bool(class_test) and _can_see_class_test(class_test, member)
    summary = _class_test_view(db, class_test, member, with_scores=False) if visible else None
    return {
        "class_test_id": class_test.id if class_test else None,
        "results_visible": visible,
        "average_percent": summary["average_percent"] if summary else None,
        "participants": summary["participants"] if summary else 0,
    }


@app.delete("/api/classes/{class_id}/center-tests/{test_id}")
def detach_center_test(class_id: int, test_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Imtihonni sinfdan olib tashlash (bekor qilish): sinf o'quvchilari imtihon
    ro'yxatidan chiqadi (skanerlanganlari ham), "Sinov natijalari"dagi yozuvi
    o'chadi. Imtihonning o'zi Test markazida qoladi (boshqa sinflar o'zgarmaydi).
    Butunlay o'chirilmaydi — holati "deleted" bo'ladi. Test egasi yoki admin/direktor.
    """

    school_class, member = _school_class(db, user, class_id)
    test = db.get(Test, test_id)
    owner = db.get(Teacher, test.teacher_id) if test else None
    if not test or test.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Imtihon topilmadi")
    if not (owner and owner.user_id == user.id) and not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Imtihonni testni yaratgan o'qituvchi yoki admin/direktor olib tashlaydi")

    student_ids = [student.id for student in school_class.students]
    attempts = db.scalars(
        select(Attempt).where(
            Attempt.test_id == test.id, Attempt.class_student_id.in_(student_ids or [-1]),
            Attempt.status.in_(("pending", "done")),
        )
    ).all()
    if not attempts:
        raise HTTPException(status_code=404, detail="Bu imtihon sinfga biriktirilmagan")
    for attempt in attempts:
        attempt.status = "deleted"
        attempt.sheet_number = None  # ID raqam bo'shaydi — qayta biriktirilsa o'quvchi o'z ID'si bilan qaytadi
    for class_test in db.scalars(
        select(ClassTest).where(
            ClassTest.class_id == school_class.id, ClassTest.source_test_id == test.id, ClassTest.deleted_at.is_(None)
        )
    ).all():
        class_test.deleted_at = utcnow()
    db.commit()
    logging.getLogger("backend").info("Imtihon sinfdan olib tashlandi: test=%s class=%s user=%s (%s ta o'quvchi)",
                                      test.id, school_class.id, user.id, len(attempts))
    return {"removed": len(attempts)}


@app.post("/api/classes/{class_id}/tests")
def create_class_test(class_id: int, body: ClassTestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchi sinfda o'tkazgan test natijalarini kiritadi."""

    school_class, member = _school_class(db, user, class_id)
    if not _active_students(school_class):
        raise HTTPException(status_code=400, detail="Sinfda hali o'quvchi yo'q — avval o'quvchilarni qo'shing")
    if not any(score is not None for score in body.scores.values()):
        raise HTTPException(status_code=422, detail="Kamida bitta o'quvchining balini kiriting")

    test = ClassTest(
        class_id=school_class.id,
        teacher_user_id=user.id,
        title=body.title,
        subject=body.subject,
        taken_on=body.taken_on,
        max_score=body.max_score,
    )
    db.add(test)
    db.flush()
    _save_scores(db, test, school_class, body.scores)
    db.commit()
    db.refresh(test)
    return _class_test_view(db, test, member)


@app.get("/api/class-tests/{test_id}")
def class_test_detail(test_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    test, member = _visible_class_test(db, user, test_id)
    return _class_test_view(db, test, member)


@app.put("/api/class-tests/{test_id}")
def edit_class_test(test_id: int, body: ClassTestIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    test, member = _visible_class_test(db, user, test_id)
    if not any(score is not None for score in body.scores.values()):
        raise HTTPException(status_code=422, detail="Kamida bitta o'quvchining balini kiriting")
    test.title, test.subject, test.taken_on, test.max_score = body.title, body.subject, body.taken_on, body.max_score
    _save_scores(db, test, db.get(SchoolClass, test.class_id), body.scores)
    test.updated_at = utcnow()
    db.commit()
    db.refresh(test)
    return _class_test_view(db, test, member)


# Diagramma ranglari (to'q, och): 71%+ — yashil, 56–70% — sariq, qolgani — qizil
SCORE_LEVEL_COLORS = {"good": ("#0e9f6e", "#34d399"), "mid": ("#c48a0a", "#f2c94c"), "low": ("#d9483b", "#f59e8b")}


def _class_test_context(db: Session, test: ClassTest, member: SchoolMember) -> dict:
    view = _class_test_view(db, test, member)
    school_class = db.get(SchoolClass, test.class_id)
    school = db.get(PrivateSchool, school_class.school_id)
    view.update({"class_name": school_class.name, "school_name": school.name if school else "",
                 "date": test.taken_on.strftime("%d.%m.%Y")})
    return view


def _fmt_number(value: float) -> str:
    return f"{value:g}"


def class_test_pdf(context: dict) -> bytes:
    """Test natijalari — PDF (chop etish uchun)."""

    styles = _report_styles()
    buffer = io.BytesIO()
    document = _brand_document(buffer, f"{context['title']} — {context['class_name']}")
    width = document.width
    meta = (f"{context['school_name']}  ·  {context['class_name']}  ·  {context['subject'] or 'Fan'}  ·  "
            f"{context['date']}  ·  Maks. {_fmt_number(context['max_score'])} ball")
    summary = (f"O'qituvchi: {context['teacher_name']}   ·   Qatnashdi: {context['participants']} / {context['total']}   ·   "
               f"O'rtacha: {_fmt_number(context['average_percent'] or 0)}%")

    header = ["№", "F.I.Sh.", "Natija", "%", "Diagramma"]
    bar_width = width * 0.24 - 12

    def bar(row: dict) -> Drawing:
        """Foiz chizig'i: och fon ustida natija rangidagi to'ldirilgan qism."""
        drawing = Drawing(bar_width, 10)
        drawing.add(Rect(0, 0, bar_width, 10, rx=5, ry=5, fillColor=colors.HexColor("#DDEFE2"), strokeColor=None))
        if row["score"] is not None:
            filled = max(10.0, bar_width * min(100.0, max(0.0, float(row["percent"]))) / 100)
            color = colors.HexColor(SCORE_LEVEL_COLORS[row["level"]][0])
            drawing.add(Rect(0, 0, filled, 10, rx=5, ry=5, fillColor=color, strokeColor=None))
        return drawing

    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in header]]
    for row in context["scores"]:
        absent = row["score"] is None
        data.append([
            Paragraph(str(row.get("rank", "—")), styles["cell"]),
            Paragraph(xml_escape(row["name"]), styles["cell"]),
            Paragraph("—" if absent else f"<b>{_fmt_number(row['score'])}/{_fmt_number(context['max_score'])}</b>", styles["cell"]),
            Paragraph("kelmadi" if absent else f"{_fmt_number(row['percent'])}%", styles["cell"]),
            bar(row),
        ])
    table = Table(data, colWidths=[width * 0.07, width * 0.43, width * 0.13, width * 0.13, width * 0.24], repeatRows=1)
    commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD), ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    for index in range(2, len(data), 2):
        commands.append(("BACKGROUND", (0, index), (-1, index), REPORT_TILE_BG))
    table.setStyle(TableStyle(commands))

    document.build([
        Paragraph(xml_escape(f"{context['title']} — natijalar"), styles["title"]),
        Paragraph(xml_escape(meta), styles["meta"]),
        Spacer(1, 4),
        Paragraph(xml_escape(summary), styles["cell_bold"]),
        Spacer(1, 10),
        table,
    ], canvasmaker=_brand_canvas(context["school_name"], f"{context['class_name']} · test natijalari"))
    return buffer.getvalue()


def class_test_images(context: dict) -> list[bytes]:
    """Sinf testi natijalari rasmi — PDF ko'rinishining o'zi, har sahifasi alohida rasm (PNG)."""
    return pdf_pages_to_png(class_test_pdf(context))


@app.get("/api/class-tests/{test_id}/file")
def class_test_file(test_id: int, format: str = "pdf", page: int = 1, user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    test, member = _visible_class_test(db, user, test_id)
    context = _class_test_context(db, test, member)
    if format == "image":
        # Rasm — PDF'ning page-sahifasi (X-Page-Count — jami sahifalar)
        images = class_test_images(context)
        if not 1 <= page <= len(images):
            raise HTTPException(status_code=404, detail="Bunday sahifa yo'q")
        return StreamingResponse(io.BytesIO(images[page - 1]), media_type="image/png", headers={
            "Content-Disposition": f'attachment; filename="{test.id}_{page}.png"',
            "X-Page-Count": str(len(images)), "Access-Control-Expose-Headers": "X-Page-Count",
        })
    return StreamingResponse(io.BytesIO(class_test_pdf(context)), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{test.id}.pdf"'})


@app.post("/api/class-tests/{test_id}/send")
def send_class_test(test_id: int, format: str = "pdf", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Natijalarni bot orqali foydalanuvchining o'z chatiga yuboradi (u yerdan uzatish yoki chop etish)."""
    test, member = _visible_class_test(db, user, test_id)
    context = _class_test_context(db, test, member)
    caption = (f"📝 {context['title']} — {context['class_name']}\n📅 {context['date']} · {context['subject'] or 'Fan'}\n"
               f"📊 O'rtacha: {_fmt_number(context['average_percent'] or 0)}%")
    file_name = f"natija_{context['class_name']}".replace("/", "-")
    if format == "image":
        # PDF bilan bir xil; bir necha sahifa bo'lsa — albom (har sahifa alohida rasm)
        _send_photo_album(user.tg_id, file_name, class_test_images(context), caption)
    else:
        _send_pdf_to_telegram(user.tg_id, f"{file_name}.pdf", class_test_pdf(context), caption)
    return {"ok": True}


@app.get("/api/schools/test-results")
def school_test_results(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rahbariyat uchun: har sinfda nechta test natijasi bor (sinfni tanlab, hammasini ko'radi)."""
    member = _require_school_member(db, user)
    if not _is_director(member):
        raise HTTPException(status_code=403, detail="Barcha natijalarni faqat rahbariyat ko'radi")
    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))
    ).all())
    rows = []
    for school_class in classes:
        tests = db.scalars(
            select(ClassTest).where(ClassTest.class_id == school_class.id, ClassTest.deleted_at.is_(None))
            .order_by(ClassTest.taken_on.desc(), ClassTest.id.desc())
        ).all()
        averages = [view["average_percent"] for view in (_class_test_view(db, test, member, with_scores=False) for test in tests)
                    if view["average_percent"] is not None]
        rows.append({
            "id": school_class.id,
            "name": school_class.name,
            "tests": len(tests),
            "last_date": tests[0].taken_on if tests else None,
            "average_percent": round(sum(averages) / len(averages), 1) if averages else None,
        })
    rows.sort(key=lambda row: (row["tests"] == 0, _class_name_key(row["name"])))
    return rows


@app.delete("/api/class-tests/{test_id}")
def delete_class_test(test_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Test natijalari ro'yxatdan olinadi — bazada qoladi (tiklash mumkin)."""
    test, _ = _visible_class_test(db, user, test_id)
    test.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


# ---------- O'qish to'lovlari ----------
#
# Oylik summa — maktab bo'yicha bitta (admin/direktor belgilaydi), o'quvchiga
# foizda chegirma berilishi mumkin. To'lovni admin, direktor va o'sha sinf
# kuratori kiritadi. Pul bilan bog'liq hech narsa o'chirilmaydi: xato to'lov
# sababi bilan bekor qilinadi va tarixda qoladi.

MONTH_NAMES = ["Yanvar", "Fevral", "Mart", "Aprel", "May", "Iyun",
               "Iyul", "Avgust", "Sentabr", "Oktabr", "Noyabr", "Dekabr"]
PAYMENT_METHODS = {"cash": "Naqd", "card": "Karta", "transfer": "O'tkazma"}


def _month_label(month: str) -> str:
    year, number = month.split("-")
    return f"{MONTH_NAMES[int(number) - 1]} {year}"


def _current_month() -> str:
    return (utcnow() + TASHKENT_OFFSET).strftime("%Y-%m")


def _shift_month(month: str, delta: int) -> str:
    year, number = map(int, month.split("-"))
    index = year * 12 + number - 1 + delta
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _student_months(student: ClassStudent, limit: int = 24) -> list[str]:
    """O'quvchi qo'shilgan oydan joriy oygacha (eng yangisi birinchi)."""
    start = (student.created_at + TASHKENT_OFFSET).strftime("%Y-%m")
    month, months = _current_month(), []
    while month >= start and len(months) < limit:
        months.append(month)
        month = _shift_month(month, -1)
    return months


FEE_HISTORY_START = "0000-00"  # narx tarixi yozila boshlangungacha amalda bo'lgan narx
_NO_FEE_HISTORY = object()


def _fee_changes(db: Session, school_id: int) -> list[FeeChange]:
    """Muassasaning narx tarixi (so'rov davomida eslab qolinadi — har o'quvchi/oy uchun qayta so'ralmasin)."""
    cache = db.info.setdefault("fee_changes", {})
    if school_id not in cache:
        cache[school_id] = db.scalars(
            select(FeeChange).where(FeeChange.school_id == school_id).order_by(FeeChange.from_month, FeeChange.id)
        ).all()
    return cache[school_id]


def _fee_at(changes: list[FeeChange], class_id: int | None, month: str):
    """Shu oyda amalda bo'lgan yozuv qiymati; tarix yo'q bo'lsa — _NO_FEE_HISTORY."""
    value = _NO_FEE_HISTORY
    for change in changes:
        if change.class_id == class_id and change.from_month <= month:
            value = change.monthly_fee
    return value


def _class_fee(school: PrivateSchool, school_class: SchoolClass, month: str | None = None) -> int:
    """
    Guruhning shu oydagi narxi: guruhning o'z narxi bo'lsa — shu, aks holda
    muassasaning umumiy summasi. Oy berilmasa — joriy oy. Narx o'zgargan
    bo'lsa, o'tgan oylar O'SHA paytdagi narx bilan hisoblanadi.
    """

    if school_class.subject:
        return 0  # fan guruhi — to'lov o'quvchining sinfi orqali
    month = month or _current_month()
    db = object_session(school)
    changes = _fee_changes(db, school.id) if db else []
    own = _fee_at(changes, school_class.id, month)
    if own is _NO_FEE_HISTORY:
        own = school_class.monthly_fee
    if own is not None:
        return own
    general = _fee_at(changes, None, month)
    return (school.monthly_fee if general is _NO_FEE_HISTORY else general) or 0


def _student_due(school: PrivateSchool, student: ClassStudent, month: str | None = None) -> int:
    """Chegirma hisobga olingan, shu oydagi narx bo'yicha oylik summa."""
    return round(_class_fee(school, student.school_class, month) * (100 - (student.discount_percent or 0)) / 100)


def _record_fee_change(db: Session, school: PrivateSchool, school_class: SchoolClass | None, fee: int | None,
                       from_month: str | None, user: User) -> str:
    """
    Narx o'zgarishini tarixga yozadi. Birinchi o'zgarishda avvalgi narx ham
    "avvaldan" deb yoziladi — shunda o'tgan oylar eski narxda qoladi.
    """

    current = _current_month()
    from_month = from_month or current
    if from_month > _shift_month(current, 3):
        raise HTTPException(status_code=422, detail="Narxni ko'pi bilan 3 oy oldindan belgilash mumkin")

    class_id = school_class.id if school_class else None
    changes = [item for item in _fee_changes(db, school.id) if item.class_id == class_id]
    if not changes:
        previous = school_class.monthly_fee if school_class else school.monthly_fee
        charged = _class_fee(school, school_class, current) if school_class else (school.monthly_fee or 0)
        if charged <= 0 and from_month == current:
            # Avval umuman narx bo'lmagan — birinchi narx boshidan amal qiladi (o'tgan oylar ham to'lanadi)
            from_month = FEE_HISTORY_START
        elif from_month != FEE_HISTORY_START:
            db.add(FeeChange(school_id=school.id, class_id=class_id, monthly_fee=previous, from_month=FEE_HISTORY_START))
    same_month = next((item for item in changes if item.from_month == from_month), None)
    if same_month:
        same_month.monthly_fee, same_month.created_by, same_month.created_at = fee, user.id, utcnow()
    else:
        db.add(FeeChange(school_id=school.id, class_id=class_id, monthly_fee=fee, from_month=from_month, created_by=user.id))
    db.flush()
    db.info.get("fee_changes", {}).pop(school.id, None)

    # Saqlangan "hozirgi narx" — joriy oydagi qiymat (ro'yxat va sozlamalarda ko'rinadi)
    now_value = _fee_at(_fee_changes(db, school.id), class_id, current)
    if school_class:
        school_class.monthly_fee = None if now_value is _NO_FEE_HISTORY else now_value
    else:
        school.monthly_fee = 0 if now_value is _NO_FEE_HISTORY or now_value is None else now_value
    return from_month


def _fee_history(db: Session, school: PrivateSchool, school_class: SchoolClass | None) -> list[dict]:
    """Narx tarixi (eng yangisi birinchi) — sozlamalar oynasida ko'rsatish uchun."""
    class_id = school_class.id if school_class else None
    rows = [item for item in _fee_changes(db, school.id) if item.class_id == class_id]
    return [
        {
            "from_month": item.from_month,
            "label": "Avvaldan" if item.from_month == FEE_HISTORY_START else f"{_month_label(item.from_month)} dan",
            "monthly_fee": item.monthly_fee,
        }
        for item in reversed(rows)
    ]


def _paid_sums(db: Session, student_ids: list[int], months: list[str] | None = None) -> dict[tuple[int, str], int]:
    if not student_ids:
        return {}
    query = (
        select(TuitionPayment.student_id, TuitionPayment.month, func.sum(TuitionPayment.amount))
        .where(TuitionPayment.student_id.in_(student_ids), TuitionPayment.cancelled_at.is_(None))
        .group_by(TuitionPayment.student_id, TuitionPayment.month)
    )
    if months:
        query = query.where(TuitionPayment.month.in_(months))
    return {(sid, month): int(total or 0) for sid, month, total in db.execute(query)}


def _month_status(due: int, paid: int) -> dict:
    remaining = max(due - paid, 0)
    if due == 0:
        status = "free"
    elif paid >= due:
        status = "paid"
    elif paid > 0:
        status = "partial"
    else:
        status = "unpaid"
    return {"due": due, "paid": paid, "remaining": remaining, "status": status}


def _payments_class(db: Session, user: User, class_id: int) -> tuple[SchoolClass, SchoolMember, PrivateSchool]:
    """To'lovlar bilan ishlash — rahbariyat yoki shu sinfning kuratori."""
    school_class, member = _school_class(db, user, class_id)
    if not _can_see_payments(school_class, member):
        raise HTTPException(status_code=403, detail="To'lovlarni rahbariyat yoki sinf kuratori ko'radi")
    return school_class, member, db.get(PrivateSchool, school_class.school_id)


def _payments_student(db: Session, user: User, class_id: int, student_id: int):
    school_class, member, school = _payments_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    return school_class, member, school, student


def _payment_view(db: Session, payment: TuitionPayment) -> dict:
    taker = db.get(User, payment.created_by)
    return {
        "id": payment.id,
        "receipt_no": payment.receipt_no,
        "month": payment.month,
        "month_label": _month_label(payment.month),
        "amount": payment.amount,
        "method": payment.method,
        "method_label": PAYMENT_METHODS.get(payment.method, payment.method),
        "paid_on": payment.paid_on,
        "note": payment.note,
        "created_by": taker.full_name if taker else "",
        "created_at": payment.created_at,
        "cancelled": payment.cancelled_at is not None,
        "cancel_reason": payment.cancel_reason,
    }


@app.put("/api/schools/fee")
def set_monthly_fee(body: FeeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Oylik o'qish to'lovini admin yoki direktor belgilaydi (barcha o'quvchilar uchun)."""
    member = _require_school_member(db, user)
    if not _can_set_fees(member):
        raise HTTPException(status_code=403, detail="Oylik to'lovni admin, direktor yoki moliyachi belgilaydi")
    from_month = _record_fee_change(db, member.school, None, body.monthly_fee, body.from_month, user)
    db.commit()
    logging.getLogger("backend").info("Oylik to'lov o'zgardi: school=%s fee=%s from=%s user=%s",
                                      member.school_id, body.monthly_fee, from_month, user.id)
    return {"school": _school_view(db, member, user), "history": _fee_history(db, member.school, None)}


@app.get("/api/schools/fee-history")
def fee_history(class_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Umumiy summa yoki guruh narxining tarixi (admin va direktor)."""
    if class_id:
        school_class, member = _school_class(db, user, class_id)
    else:
        school_class, member = None, _require_school_member(db, user)
    if not _can_set_fees(member):
        raise HTTPException(status_code=403, detail="Narx tarixini admin, direktor yoki moliyachi ko'radi")
    return {"history": _fee_history(db, member.school, school_class)}


@app.put("/api/classes/{class_id}/fee")
def set_class_fee(class_id: int, body: ClassFeeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Guruhning o'z oylik narxi (admin yoki direktor). Bo'sh — muassasaning umumiy summasiga qaytadi."""
    school_class, member = _school_class(db, user, class_id)
    if not _can_set_fees(member):
        raise HTTPException(status_code=403, detail="Narxni admin, direktor yoki moliyachi belgilaydi")
    from_month = _record_fee_change(db, member.school, school_class, body.monthly_fee, body.from_month, user)
    db.commit()
    logging.getLogger("backend").info("Guruh narxi o'zgardi: class=%s fee=%s from=%s user=%s",
                                      class_id, body.monthly_fee, from_month, user.id)
    return {"id": school_class.id, "monthly_fee": school_class.monthly_fee,
            "effective_fee": _class_fee(member.school, school_class),
            "history": _fee_history(db, member.school, school_class)}


@app.get("/api/classes/{class_id}/payments")
def class_payments(class_id: int, month: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf o'quvchilarining shu oydagi to'lov holati (qarzdorlar tepada)."""
    school_class, _, school = _payments_class(db, user, class_id)
    month = month if month and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) else _current_month()
    students = _active_students(school_class)
    paid = _paid_sums(db, [student.id for student in students], [month])
    order = {"unpaid": 0, "partial": 1, "paid": 2, "free": 3}
    rows = []
    for student in students:
        info = _month_status(_student_due(school, student, month), paid.get((student.id, month), 0))
        rows.append({"id": student.id, "name": student.full_name, "discount": student.discount_percent or 0, **info})
    rows.sort(key=lambda row: (order[row["status"]], row["name"].lower()))
    return {
        "month": month,
        "month_label": _month_label(month),
        "monthly_fee": _class_fee(school, school_class, month),
        "class_name": school_class.name,
        "students": rows,
        "totals": {
            "due": sum(row["due"] for row in rows),
            "paid": sum(min(row["paid"], row["due"]) for row in rows),
            "remaining": sum(row["remaining"] for row in rows),
            "debtors": sum(1 for row in rows if row["status"] in ("unpaid", "partial")),
        },
    }


@app.get("/api/classes/{class_id}/students/{student_id}/payments")
def student_payments(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchining oylar bo'yicha to'lovlari, umumiy qarzi va to'lovlar tarixi."""
    school_class, _, school, student = _payments_student(db, user, class_id, student_id)
    months = _student_months(student)
    due = _student_due(school, student)
    paid = _paid_sums(db, [student.id])
    # Har oy o'sha oydagi narx bilan: narx oshgani to'langan eski oylarni qarzga aylantirmaydi
    month_rows = [
        {"month": month, "label": _month_label(month),
         **_month_status(_student_due(school, student, month), paid.get((student.id, month), 0))}
        for month in months
    ]
    payments = db.scalars(
        select(TuitionPayment).where(TuitionPayment.student_id == student.id).order_by(TuitionPayment.id.desc())
    ).all()
    return {
        "student": {"id": student.id, "name": student.full_name, "class_name": school_class.name},
        "monthly_fee": _class_fee(school, school_class),
        "discount": student.discount_percent or 0,
        "due": due,
        "months": month_rows,
        "total_debt": sum(row["remaining"] for row in month_rows),
        "payments": [_payment_view(db, payment) for payment in payments],
    }


@app.put("/api/classes/{class_id}/students/{student_id}/discount")
def set_discount(class_id: int, student_id: int, body: DiscountIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _, _, _, student = _payments_student(db, user, class_id, student_id)
    student.discount_percent = body.percent
    db.commit()
    logging.getLogger("backend").info("Chegirma: student=%s percent=%s user=%s", student.id, body.percent, user.id)
    return student_payments(class_id, student_id, user, db)


@app.post("/api/classes/{class_id}/students/{student_id}/payments")
def add_payment(class_id: int, student_id: int, body: PaymentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Pul qabul qilindi — chek raqami bilan yoziladi. Oy uchun qolgan summadan oshmaydi."""
    school_class, _, school, student = _payments_student(db, user, class_id, student_id)
    if student.removed_at is not None:
        raise HTTPException(status_code=422, detail="Bu o'quvchi sinfdan chiqarilgan")
    if body.month > _shift_month(_current_month(), 3):
        raise HTTPException(status_code=422, detail="Faqat 3 oygacha oldindan to'lov qabul qilinadi")
    if _class_fee(school, school_class, body.month) <= 0:
        raise HTTPException(status_code=422, detail="Avval oylik to'lov summasini belgilang (admin yoki direktor)")

    due = _student_due(school, student, body.month)
    already = _paid_sums(db, [student.id], [body.month]).get((student.id, body.month), 0)
    remaining = max(due - already, 0)
    if remaining == 0:
        raise HTTPException(status_code=409, detail=f"{_month_label(body.month)} oyi uchun to'lov to'liq qilingan")
    if body.amount > remaining:
        raise HTTPException(status_code=422, detail=f"{_month_label(body.month)} uchun qolgan summa: {remaining:,} so'm".replace(",", " "))

    last_no = db.scalar(select(func.max(TuitionPayment.receipt_no)).where(TuitionPayment.school_id == school.id)) or 0
    payment = TuitionPayment(
        school_id=school.id,
        class_id=school_class.id,
        student_id=student.id,
        student_name=student.full_name,
        month=body.month,
        amount=body.amount,
        method=body.method,
        paid_on=body.paid_on,
        note=body.note,
        receipt_no=last_no + 1,
        created_by=user.id,
    )
    db.add(payment)
    db.commit()
    db.refresh(payment)
    logging.getLogger("backend").info("To'lov qabul qilindi: id=%s student=%s %s so'm user=%s", payment.id, student.id, body.amount, user.id)
    return {"payment": _payment_view(db, payment), **student_payments(class_id, student_id, user, db)}


def _school_payment(db: Session, user: User, payment_id: int) -> tuple[TuitionPayment, SchoolMember]:
    payment = db.get(TuitionPayment, payment_id)
    if not payment:
        raise HTTPException(status_code=404, detail="To'lov topilmadi")
    _, member, _ = _payments_class(db, user, payment.class_id)
    return payment, member


@app.post("/api/payments/{payment_id}/cancel")
def cancel_payment(payment_id: int, body: PaymentCancelIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Xato kiritilgan to'lovni bekor qilish — o'chirilmaydi, sababi bilan tarixda qoladi."""
    payment, _ = _school_payment(db, user, payment_id)
    if payment.cancelled_at is not None:
        raise HTTPException(status_code=409, detail="Bu to'lov allaqachon bekor qilingan")
    payment.cancelled_at = utcnow()
    payment.cancelled_by = user.id
    payment.cancel_reason = " ".join(body.reason.split())
    db.commit()
    logging.getLogger("backend").warning("To'lov bekor qilindi: id=%s user=%s sabab=%s", payment.id, user.id, payment.cancel_reason)
    return student_payments(payment.class_id, payment.student_id, user, db)


def _receipt_context(db: Session, payment: TuitionPayment) -> dict:
    school = db.get(PrivateSchool, payment.school_id)
    school_class = db.get(SchoolClass, payment.class_id)
    student = db.get(ClassStudent, payment.student_id)
    due = _student_due(school, student, payment.month)
    paid = _paid_sums(db, [student.id], [payment.month]).get((student.id, payment.month), 0)
    taker = db.get(User, payment.created_by)
    return {
        "school": school.name,
        "class_name": school_class.name,
        "student": payment.student_name,
        "receipt_no": payment.receipt_no,
        "month": _month_label(payment.month),
        "amount": payment.amount,
        "method": PAYMENT_METHODS.get(payment.method, payment.method),
        "paid_on": payment.paid_on.strftime("%d.%m.%Y"),
        "taker": taker.full_name if taker else "",
        "remaining": max(due - paid, 0),
        "cancelled": payment.cancelled_at is not None,
    }


def _money(value: int) -> str:
    return f"{value:,}".replace(",", " ") + " so'm"


def receipt_image(context: dict) -> bytes:
    """To'lov cheki (PNG) — ota-onaga yuborish uchun."""

    from PIL import Image, ImageDraw, ImageFont

    def font(name: str, size: int):
        try:
            return ImageFont.truetype(str(FONTS_DIR / name), size)
        except OSError:
            return ImageFont.load_default()

    title_font, text_font, bold_font, big_font = (font("DejaVuSerif-Bold.ttf", 42), font("DejaVuSerif.ttf", 30),
                                                  font("DejaVuSerif-Bold.ttf", 30), font("DejaVuSerif-Bold.ttf", 56))
    width, height = 900, 860
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 14], fill="#1e7b34")
    draw.text((50, 44), "TO'LOV CHEKI", font=title_font, fill="#1e7b34")
    draw.text((width - 50, 52), f"№ {context['receipt_no']}", font=bold_font, fill="#374151", anchor="ra")
    draw.text((50, 104), context["school"], font=text_font, fill="#374151")
    draw.line([50, 160, width - 50, 160], fill="#e5e7eb", width=3)

    rows = [
        ("O'quvchi", context["student"]),
        ("Sinf", context["class_name"]),
        ("Oy", context["month"]),
        ("To'lov usuli", context["method"]),
        ("Sana", context["paid_on"]),
        ("Qabul qildi", context["taker"]),
    ]
    y = 190
    for label, value in rows:
        draw.text((50, y), label, font=text_font, fill="#6b7280")
        draw.text((width - 50, y), str(value)[:34], font=bold_font, fill="#111827", anchor="ra")
        y += 58
    draw.line([50, y + 6, width - 50, y + 6], fill="#e5e7eb", width=3)
    draw.text((50, y + 40), "To'landi:", font=bold_font, fill="#111827")
    draw.text((width - 50, y + 26), _money(context["amount"]), font=big_font, fill="#1e7b34", anchor="ra")
    note = "✓ Shu oy uchun to'liq to'landi" if context["remaining"] == 0 else f"Shu oy uchun qoldi: {_money(context['remaining'])}"
    draw.text((50, y + 120), note, font=text_font, fill="#1e7b34" if context["remaining"] == 0 else "#b45309")
    if context["cancelled"]:
        draw.text((width // 2, height // 2), "BEKOR QILINGAN", font=big_font, fill="#dc2626", anchor="mm")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@app.get("/api/payments/{payment_id}/receipt")
def payment_receipt(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    payment, _ = _school_payment(db, user, payment_id)
    return StreamingResponse(io.BytesIO(receipt_image(_receipt_context(db, payment))), media_type="image/png",
                             headers={"Content-Disposition": f'attachment; filename="chek_{payment.receipt_no}.png"'})


@app.post("/api/payments/{payment_id}/send")
def send_receipt(payment_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Chekni bot orqali foydalanuvchining o'z chatiga yuboradi — u yerdan ota-onaga uzatiladi."""
    payment, _ = _school_payment(db, user, payment_id)
    context = _receipt_context(db, payment)
    caption = f"🧾 Chek № {context['receipt_no']} — {context['student']}, {context['month']}: {_money(context['amount'])}"
    _send_photo_to_telegram(user.tg_id, f"chek_{payment.receipt_no}.png", receipt_image(context), caption)
    return {"ok": True}


@app.get("/api/classes/{class_id}/students/{student_id}/reminder")
def payment_reminder(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ota-onaga hurmat bilan yozilgan to'lov eslatmasi (matn) — qarzi bo'lgan oylar bilan."""
    school_class, _, school, student = _payments_student(db, user, class_id, student_id)
    info = student_payments(class_id, student_id, user, db)
    debts = [row for row in info["months"] if row["remaining"] > 0]
    if not debts:
        raise HTTPException(status_code=409, detail="Bu o'quvchining qarzi yo'q")
    lines = [
        "Assalomu alaykum, hurmatli ota-ona!",
        "",
        f"«{school.name}» {school_class.name} sinf o'quvchisi {student.full_name}ning o'qish to'lovi bo'yicha eslatma:",
        "",
    ]
    lines += [f"• {row['label']}: {_money(row['remaining'])}" for row in reversed(debts)]
    lines += [
        "",
        f"Jami: {_money(info['total_debt'])}",
        "",
        "To'lovni imkon qadar tezroq amalga oshirishingizni hurmat bilan so'raymiz.",
        "Savollaringiz bo'lsa, maktab ma'muriyatiga murojaat qiling.",
        "",
        f"Hurmat bilan, «{school.name}» ma'muriyati",
    ]
    return {"text": "\n".join(lines)}


@app.get("/api/classes/{class_id}/debtors")
def class_debtors_text(class_id: int, month: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf qarzdorlari ro'yxati (matn) — rahbariyatga yoki guruhga yuborish uchun."""
    info = class_payments(class_id, month, user, db)
    debtors = [row for row in info["students"] if row["status"] in ("unpaid", "partial")]
    lines = [f"💰 {info['class_name']} — {info['month_label']} oyi to'lovlari", ""]
    if not debtors:
        lines.append("✅ Hamma o'quvchi to'lagan.")
    else:
        lines.append(f"Qarzdorlar ({len(debtors)} ta):")
        lines += [f"{index}. {row['name']} — {_money(row['remaining'])}" for index, row in enumerate(debtors, start=1)]
        lines += ["", f"Jami qarz: {_money(info['totals']['remaining'])}"]
    return {"text": "\n".join(lines)}


@app.get("/api/schools/payments/summary")
def payments_summary(month: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Oylik hisobot (rahbariyat): har sinf bo'yicha kutilgan, tushgan va qarz."""
    member = _require_school_member(db, user)
    if not _sees_all_finance(member):
        raise HTTPException(status_code=403, detail="Umumiy hisobotni rahbariyat va moliyachi ko'radi")
    month = month if month and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) else _current_month()
    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))
    ).all())
    rows = []
    for school_class in classes:
        if school_class.subject:
            continue  # fan guruhi — to'lov sinf orqali
        info = class_payments(school_class.id, month, user, db)
        rows.append({"id": school_class.id, "name": school_class.name, "students": len(info["students"]),
                     "monthly_fee": info["monthly_fee"], "own_fee": school_class.monthly_fee is not None, **info["totals"]})
    return {
        "month": month,
        "month_label": _month_label(month),
        "monthly_fee": member.school.monthly_fee,
        "classes": rows,
        "totals": {key: sum(row[key] for row in rows) for key in ("due", "paid", "remaining", "debtors")},
    }


SHORT_MONTH_NAMES = ["Yan", "Fev", "Mar", "Apr", "May", "Iyun", "Iyul", "Avg", "Sen", "Okt", "Noy", "Dek"]


_SEARCH_INVISIBLE = dict.fromkeys(map(ord, "​‌‍⁠﻿"))
_SEARCH_APOSTROPHES = str.maketrans({ch: "" for ch in "'`ʻʼ‘’´"})


_UZ_ALPHABET = ["a", "b", "d", "e", "f", "g", "h", "i", "j", "k", "l", "m", "n", "o", "p", "q", "r", "s", "t", "u", "v",
                "x", "y", "z", "o‘", "g‘", "sh", "ch"]
_UZ_LETTER_RANK = {letter: index + 1 for index, letter in enumerate(_UZ_ALPHABET)}
_UZ_APOSTROPHES = "'`ʻʼ‘’´"


def _uz_sort_key(text: str) -> tuple:
    """
    O'zbek alifbosi tartibi (maktab jurnalidagidek): A, B, D … X, Y, Z, O‘, G‘, Sh, Ch.
    Katta-kichik harf va apostrof turi farq qilmaydi (Jo`rayev = Jo‘rayev); tutuq belgisi (Ma’ruf)
    tartibga ta'sir qilmaydi. Alifboda yo'q harflar (kirill, c, w) — oxirida, o'z tartibida.
    """

    value = " ".join((text or "").translate(_SEARCH_INVISIBLE).lower().split())
    key, index = [], 0
    while index < len(value):
        char = value[index]
        following = value[index + 1] if index + 1 < len(value) else ""
        if char in "og" and following and following in _UZ_APOSTROPHES:
            key.append(_UZ_LETTER_RANK[char + "‘"])
            index += 2
        elif char + following in ("sh", "ch"):
            key.append(_UZ_LETTER_RANK[char + following])
            index += 2
        elif char in _UZ_APOSTROPHES:
            index += 1
        else:
            key.append(0 if char == " " else _UZ_LETTER_RANK.get(char, 100 + ord(char)))
            index += 1
    return tuple(key)


def _search_key(text: str) -> str:
    """Qidiruv uchun: kichik harf, ko'rinmas belgilar va har xil apostroflar olib tashlanadi (Noʻmonov = nomonov)."""

    return " ".join(text.translate(_SEARCH_INVISIBLE).translate(_SEARCH_APOSTROPHES).lower().split())


@app.get("/api/schools/students/search")
def search_school_students(q: str = "", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Butun muassasa bo'yicha o'quvchi qidirish: ism-familiya bo'lagi yoki ID (student_code).
    Har bir so'z alohida tekshiriladi ("ali val" → "Valiyev Ali"). Ko'pi bilan 30 ta natija.
    """

    member = _require_school_member(db, user)
    words = _search_key(q).split()
    if not words or (len(words) == 1 and len(words[0]) < 2 and not words[0].isdigit()):
        return {"students": []}

    rows = db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, ClassStudent.class_id == SchoolClass.id)
        .where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None),
               ClassStudent.removed_at.is_(None), ClassStudent.source_student_id.is_(None))
    ).all()

    code = q.strip() if q.strip().isdigit() else None
    found = []
    for student, school_class in rows:
        if code and student.student_code is not None and str(student.student_code) == code:
            rank = 0
        else:
            name = _search_key(student.full_name)
            if not all(word in name for word in words):
                continue
            rank = 1 if name.startswith(words[0]) or f" {words[0]}" in name else 2
        found.append((rank, _search_key(student.full_name), student, school_class))

    found.sort(key=lambda item: (item[0], item[1]))
    return {
        "students": [
            {"id": student.id, "full_name": student.full_name.translate(_SEARCH_INVISIBLE).strip(),
             "code": student.student_code, "class_id": school_class.id, "class_name": school_class.name}
            for _, _, student, school_class in found[:30]
        ]
    }


@app.get("/api/schools/overview")
def school_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Maktab bosh sahifasi uchun qisqa holat: raqamlar, bugun davomat olinmagan
    sinflar, qarzdorlar va oylar bo'yicha test natijalari. Har kim faqat o'ziga
    ruxsat etilganini oladi: rahbariyat — butun maktab, kurator — o'z sinfi,
    fan o'qituvchisi — o'zi olgan davomat va o'zi kiritgan testlar.
    """

    member = _require_school_member(db, user)
    school = member.school
    leader = _is_director(member)
    head = _can_delete_classes(member)

    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == school.id, SchoolClass.deleted_at.is_(None))
    ).all())
    curated = [item for item in classes if _is_class_teacher(item, user.id)]
    # Fan guruhlari — o'quvchilari sinflarda allaqachon sanalgan; to'lov va "davomat olinmagan" ham sinf bo'yicha
    real_classes = [item for item in classes if not item.subject]
    real_curated = [item for item in curated if not item.subject]
    students_total = sum(len(_active_students(item)) for item in real_classes)

    # Bugun (Toshkent vaqti) — kun boshi UTC'da
    local_now = utcnow() + TASHKENT_OFFSET
    day_start = datetime(local_now.year, local_now.month, local_now.day) - TASHKENT_OFFSET

    # Davomat: rahbariyat — butun maktab, kurator — o'z sinflari, qolganlar — o'zi olgani
    attendance_query = select(Attendance).where(Attendance.deleted_at.is_(None), Attendance.taken_at >= day_start)
    if leader:
        attendance_query = attendance_query.where(Attendance.class_id.in_([item.id for item in classes] or [-1]))
    else:
        own = or_(Attendance.teacher_user_id == user.id, Attendance.class_id.in_([item.id for item in curated] or [-1]))
        attendance_query = attendance_query.where(own)
    today_attendance = db.scalars(attendance_query).all()
    marks_total = marks_present = 0
    for attendance in today_attendance:
        counts = _attendance_counts(attendance)
        marks_total += sum(counts.values())
        marks_present += counts.get("present", 0) + counts.get("late", 0)

    # Bugun birorta ham davomat olinmagan sinflar (o'quvchisi bor sinflargina)
    watched = real_classes if leader else real_curated
    taken_ids = {attendance.class_id for attendance in today_attendance}
    missing_attendance = [
        {"id": item.id, "name": item.name}
        for item in watched
        if item.id not in taken_ids and _active_students(item)
    ]

    # Qarzdorlar (joriy oy): rahbariyat — butun maktab, kurator — o'z sinflari
    payment_classes = real_classes if _sees_all_finance(member) else real_curated
    debtors = debt_sum = None
    if payment_classes and any(_class_fee(school, item) > 0 for item in payment_classes):
        month = _current_month()
        debtors = debt_sum = 0
        for item in payment_classes:
            totals = class_payments(item.id, month, user, db)["totals"]
            debtors += totals["debtors"]
            debt_sum += totals["remaining"]

    # Test natijalari oylar bo'yicha (oxirgi 6 oy): rahbariyat — hammasi, qolganlar — o'zi kiritganlari
    first_month = _shift_month(_current_month(), -5)
    first_day = date(int(first_month[:4]), int(first_month[5:]), 1)
    test_query = select(ClassTest).where(
        ClassTest.deleted_at.is_(None),
        ClassTest.class_id.in_([item.id for item in classes] or [-1]),
        ClassTest.taken_on >= first_day,
    )
    if not leader:
        test_query = test_query.where(ClassTest.teacher_user_id == user.id)
    by_month: dict[str, list[float]] = {}
    for test in db.scalars(test_query).all():
        percents = [score.score / test.max_score * 100 for score in test.scores if score.score is not None and test.max_score]
        if percents:
            by_month.setdefault(test.taken_on.strftime("%Y-%m"), []).append(sum(percents) / len(percents))
    chart = []
    for offset in range(-5, 1):
        month = _shift_month(_current_month(), offset)
        values = by_month.get(month, [])
        chart.append({
            "month": month,
            "label": SHORT_MONTH_NAMES[int(month[5:]) - 1],
            "average": round(sum(values) / len(values), 1) if values else None,
            "tests": len(values),
        })

    # Bugun tug'ilgan kunlar: o'quvchilar va xodimlar (tabriklash uchun)
    today_md = (local_now.month, local_now.day)
    birthdays = [
        {"name": student.full_name, "class_name": item.name, "kind": "student"}
        for item in real_classes
        for student in _active_students(item)
        if student.birth_date and (student.birth_date.month, student.birth_date.day) == today_md
    ]
    birthdays += [
        {"name": staff.user.full_name if staff.user else "", "class_name": None, "kind": "staff"}
        for staff in school.members
        if staff.left_at is None and staff.birth_date and (staff.birth_date.month, staff.birth_date.day) == today_md
    ]

    latest_announcements = db.scalars(
        select(SchoolAnnouncement)
        .where(SchoolAnnouncement.school_id == school.id, SchoolAnnouncement.deleted_at.is_(None))
        .order_by(SchoolAnnouncement.id.desc())
        .limit(2)
    ).all()
    today_lessons = _today_lessons(db, member, user, classes, local_now, today_attendance)

    return {
        "today": local_now.date(),
        "weekday_label": WEEKDAYS[local_now.weekday()],
        "today_lessons": today_lessons,
        "birthdays": birthdays,
        "announcements": [_announcement_view(db, item, member, user) for item in latest_announcements],
        "month_label": _month_label(_current_month()),
        "stats": {
            "students": students_total,
            "classes": len(real_classes),
            "my_classes": len(curated),
            "lessons_today": len(today_lessons),
            "attendance_percent": round(marks_present / marks_total * 100) if marks_total else None,
            "debtors": debtors,
            "debt_sum": debt_sum,
        },
        "missing_attendance": missing_attendance,
        "classes_without_curator": sum(1 for item in real_classes if item.curator_user_id is None) if head else 0,
        "needs_fee": head and bool(real_classes) and all(_class_fee(school, item) <= 0 for item in real_classes),
        "chart": chart,
        "can": {
            "payments": bool(payment_classes),
            "create_class": head,
            "invite": member.role == "owner",
            "results": leader,
            "announce": leader or bool(curated),
            "schedule": leader or bool(curated),
        },
        "payment_classes": [{"id": item.id, "name": item.name} for item in payment_classes],
        # Biriktirilmaganlar (sinfdan sabab bilan chiqarilganlar) — rahbariyatga
        "unassigned": len(_unassigned_rows(db, school.id)) if leader else None,
    }


# ---------- Biriktirilmaganlar: sinfdan chiqarilgan o'quvchilar ----------
# Oddiy sinfdan sabab bilan chiqarilgan o'quvchi shu ro'yxatga tushadi. Rahbariyat uni qaytadan sinfga
# biriktiradi (eski sinfiga — tarixi bilan tiklanadi; boshqasiga — u yerda yangi yozuv) yoki o'chiradi.


def _unassigned_rows(db: Session, school_id: int) -> list[tuple[ClassStudent, SchoolClass]]:
    return db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == school_id, SchoolClass.subject.is_(None), ClassStudent.source_student_id.is_(None),
               ClassStudent.removed_at.is_not(None), ClassStudent.removed_reason.is_not(None),
               ClassStudent.basket_closed_at.is_(None))
        .order_by(ClassStudent.removed_at.desc(), ClassStudent.id.desc())
    ).all()


def _unassigned_member(db: Session, user: User) -> SchoolMember:
    member = _require_school_member(db, user)
    if not _is_director(member):
        raise HTTPException(status_code=403, detail="Biriktirilmaganlar ro'yxatini rahbariyat boshqaradi")
    return member


def _unassigned_student(db: Session, member: SchoolMember, student_id: int) -> tuple[ClassStudent, SchoolClass]:
    for student, school_class in _unassigned_rows(db, member.school_id):
        if student.id == student_id:
            return student, school_class
    raise HTTPException(status_code=404, detail="O'quvchi biriktirilmaganlar ro'yxatida yo'q")


@app.get("/api/schools/unassigned")
def unassigned_students(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinfdan chiqarilgan, hali hech qayerga biriktirilmagan o'quvchilar — sababi va kim chiqargani bilan."""

    member = _unassigned_member(db, user)
    rows = _unassigned_rows(db, member.school_id)
    removers = {
        item.id: item.full_name
        for item in db.scalars(select(User).where(User.id.in_({student.removed_by_user_id for student, _ in rows} or {-1})))
    }
    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None),
                                  SchoolClass.subject.is_(None))
    ).all())
    return {
        "students": [
            {
                "id": student.id, "full_name": student.full_name, "birth_date": student.birth_date,
                "class_id": school_class.id, "class_name": school_class.name,
                "class_deleted": school_class.deleted_at is not None,
                "removed_at": student.removed_at, "removed_by": removers.get(student.removed_by_user_id, ""),
                "reason": student.removed_reason,
            }
            for student, school_class in rows
        ],
        "classes": [{"id": item.id, "name": item.name} for item in classes],
    }


@app.post("/api/schools/unassigned/{student_id}/assign")
def assign_unassigned(student_id: int, body: UnassignedAssignIn, user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """
    Sinfga qayta biriktirish. Eski sinfiga qaytsa — o'sha yozuv tiklanadi (davomat, natija, ID tarixi bilan);
    boshqa sinfga — u yerda yangi yozuv ochiladi, eski sinfdagi tarixi o'sha sinfda qoladi.
    """

    member = _unassigned_member(db, user)
    student, old_class = _unassigned_student(db, member, student_id)
    target = db.get(SchoolClass, body.class_id)
    if (not target or target.school_id != member.school_id or target.deleted_at is not None or target.subject):
        raise HTTPException(status_code=404, detail="Sinf topilmadi")
    now = utcnow()
    if target.id == old_class.id:
        student.removed_at = None
        student.removed_reason = None
        student.removed_by_user_id = None
        restored = student
    else:
        restored = ClassStudent(class_id=target.id, full_name=student.full_name, birth_date=student.birth_date,
                                discount_percent=student.discount_percent, added_by_user_id=user.id)
        db.add(restored)
        db.flush()
        student.basket_closed_at = now
        student.reassigned_to_id = restored.id
    db.commit()
    _assign_student_codes(db, member.school_id)
    logging.getLogger("backend").info("Biriktirilmagan o'quvchi sinfga biriktirildi: student=%s class=%s user=%s",
                                      student.id, target.id, user.id)
    return {"ok": True, "class_id": target.id, "class_name": target.name, "student_id": restored.id,
            "restored": restored.id == student.id}


@app.post("/api/schools/unassigned/{student_id}/delete")
def delete_unassigned(student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Biriktirilmaganlardan o'chirish — ro'yxatdan butunlay olinadi; eski davomat va natijalari tarixda qoladi."""

    member = _unassigned_member(db, user)
    student, _old_class = _unassigned_student(db, member, student_id)
    student.basket_closed_at = utcnow()
    db.commit()
    logging.getLogger("backend").info("Biriktirilmagan o'quvchi o'chirildi: student=%s user=%s", student.id, user.id)
    return {"ok": True}


# ---------- Dars jadvali ----------
#
# Jadvalni har kim ko'radi; tuzadi — rahbariyat (admin, direktor, zam direktor)
# yoki o'sha sinfning kuratori. Bir sinfda bir vaqtda ikki dars, bir
# o'qituvchida bir vaqtda ikki sinf bo'lmaydi.

WEEKDAYS = ["Dushanba", "Seshanba", "Chorshanba", "Payshanba", "Juma", "Shanba", "Yakshanba"]


def _can_edit_schedule(school_class: SchoolClass, member: SchoolMember) -> bool:
    return _is_director(member) or _is_class_teacher(school_class, member.user_id)


def _slot_view(db: Session, slot: LessonSlot, class_name: str | None = None) -> dict:
    teacher = db.get(User, slot.teacher_user_id) if slot.teacher_user_id else None
    if class_name is None:
        school_class = db.get(SchoolClass, slot.class_id)
        class_name = school_class.name if school_class else ""
    return {
        "id": slot.id,
        "class_id": slot.class_id,
        "class_name": class_name,
        "weekday": slot.weekday,
        "weekday_label": WEEKDAYS[slot.weekday],
        "start_time": slot.start_time,
        "end_time": slot.end_time,
        "subject": slot.subject,
        "teacher_user_id": slot.teacher_user_id,
        "teacher_name": teacher.full_name if teacher else "",
        "period": slot.period,
    }


def _active_slots(db: Session, **filters):
    query = select(LessonSlot).where(LessonSlot.deleted_at.is_(None))
    for name, value in filters.items():
        query = query.where(getattr(LessonSlot, name) == value)
    return db.scalars(query.order_by(LessonSlot.weekday, LessonSlot.start_time)).all()


def _check_slot_conflicts(db: Session, school_class: SchoolClass, body: LessonSlotIn, skip_id: int | None = None) -> None:
    """Sinfda yoki o'qituvchida shu vaqtda boshqa dars bo'lsa — 409."""

    def overlaps(slot: LessonSlot) -> bool:
        return slot.id != skip_id and slot.start_time < body.end_time and body.start_time < slot.end_time

    for slot in _active_slots(db, class_id=school_class.id, weekday=body.weekday):
        if overlaps(slot):
            raise HTTPException(
                status_code=409,
                detail=f"{WEEKDAYS[body.weekday]} {slot.start_time}–{slot.end_time} da bu sinfda «{slot.subject or 'dars'}» bor",
            )
    if body.teacher_user_id:
        for slot in _active_slots(db, teacher_user_id=body.teacher_user_id, weekday=body.weekday, school_id=school_class.school_id):
            if overlaps(slot):
                other = db.get(SchoolClass, slot.class_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"Bu o'qituvchining {WEEKDAYS[body.weekday]} {slot.start_time}–{slot.end_time} da "
                    f"{other.name if other else 'boshqa'} sinfida darsi bor",
                )


def _check_slot_teacher(db: Session, school_id: int, teacher_user_id: int | None) -> None:
    if teacher_user_id is None:
        return
    active = db.scalar(
        select(SchoolMember.id).where(
            SchoolMember.school_id == school_id, SchoolMember.user_id == teacher_user_id, SchoolMember.left_at.is_(None)
        )
    )
    if not active:
        raise HTTPException(status_code=422, detail="Bu o'qituvchi maktab xodimi emas")


@app.get("/api/classes/{class_id}/schedule")
def class_schedule(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    school_class, member = _school_class(db, user, class_id)
    slots = _active_slots(db, class_id=school_class.id)
    shift = _class_shift(db, school_class)
    return {
        "class_id": school_class.id,
        "class_name": school_class.name,
        "can_edit": _can_edit_schedule(school_class, member),
        "slots": [_slot_view(db, slot, school_class.name) for slot in slots],
        "shift": _shift_view(shift) if shift else None,
        "plan": _plan_rows(db, school_class, slots),
    }


@app.post("/api/classes/{class_id}/schedule")
def add_lesson_slot(class_id: int, body: LessonSlotIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    school_class, member = _school_class(db, user, class_id)
    if not _can_edit_schedule(school_class, member):
        raise HTTPException(status_code=403, detail="Jadvalni rahbariyat yoki sinf kuratori tuzadi")
    _apply_shift_period(db, school_class, body)
    _check_slot_teacher(db, school_class.school_id, body.teacher_user_id)
    _check_teacher_hours(db, school_class.school_id, body)
    _check_slot_conflicts(db, school_class, body)
    slot = LessonSlot(
        school_id=school_class.school_id,
        class_id=school_class.id,
        weekday=body.weekday,
        start_time=body.start_time,
        end_time=body.end_time,
        subject=body.subject,
        teacher_user_id=body.teacher_user_id,
        period=body.period,
        created_by=user.id,
    )
    db.add(slot)
    db.commit()
    return class_schedule(class_id, user, db)


def _editable_slot(db: Session, user: User, slot_id: int) -> tuple[LessonSlot, SchoolClass]:
    slot = db.get(LessonSlot, slot_id)
    if not slot or slot.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Dars topilmadi")
    school_class, member = _school_class(db, user, slot.class_id)
    if not _can_edit_schedule(school_class, member):
        raise HTTPException(status_code=403, detail="Jadvalni rahbariyat yoki sinf kuratori tuzadi")
    return slot, school_class


@app.put("/api/schedule/{slot_id}")
def update_lesson_slot(slot_id: int, body: LessonSlotIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    slot, school_class = _editable_slot(db, user, slot_id)
    _apply_shift_period(db, school_class, body)
    _check_slot_teacher(db, school_class.school_id, body.teacher_user_id)
    _check_teacher_hours(db, school_class.school_id, body)
    _check_slot_conflicts(db, school_class, body, skip_id=slot.id)
    slot.weekday, slot.start_time, slot.end_time = body.weekday, body.start_time, body.end_time
    slot.subject, slot.teacher_user_id, slot.period = body.subject, body.teacher_user_id, body.period
    db.commit()
    return class_schedule(school_class.id, user, db)


@app.delete("/api/schedule/{slot_id}")
def delete_lesson_slot(slot_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    slot, school_class = _editable_slot(db, user, slot_id)
    slot.deleted_at = utcnow()
    db.commit()
    return class_schedule(school_class.id, user, db)


@app.get("/api/schools/my-schedule")
def my_week_schedule(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchining haftalik jadvali — o'zi o'tadigan darslar, kunlar bo'yicha."""
    member = _require_school_member(db, user)
    slots = _active_slots(db, school_id=member.school_id, teacher_user_id=user.id)
    names = {item.id: item.name for item in db.scalars(select(SchoolClass).where(SchoolClass.school_id == member.school_id))}
    return {"slots": [_slot_view(db, slot, names.get(slot.class_id, "")) for slot in slots]}


# ---------- Dars jadvali: smenalar (qo'ng'iroq setkasi), o'quv reja, o'qituvchi yuklamasi ----------
# Rahbariyat smenalarni (1-dars 08:00–08:45, ...), har sinfning o'quv rejasini (fan — haftasiga necha soat —
# kim o'tadi) va o'qituvchilarning yuklamasini (haftalik soat, ish kunlari, ish vaqti) kiritadi. Darslar
# sinf jadvalidagi setka kataklariga qo'yiladi: vaqt setkadan olinadi, to'qnashuv va ish vaqti tekshiriladi.


def _json_list(value: str | None) -> list:
    try:
        parsed = json.loads(value or "[]")
    except (TypeError, ValueError):
        return []
    return parsed if isinstance(parsed, list) else []


def _shift_view(shift: ScheduleShift) -> dict:
    return {"id": shift.id, "name": shift.name, "weekdays": _json_list(shift.weekdays), "periods": _json_list(shift.periods)}


def _active_shifts(db: Session, school_id: int) -> list[ScheduleShift]:
    return db.scalars(
        select(ScheduleShift)
        .where(ScheduleShift.school_id == school_id, ScheduleShift.deleted_at.is_(None))
        .order_by(ScheduleShift.id)
    ).all()


def _school_shift(db: Session, member: SchoolMember, shift_id: int) -> ScheduleShift:
    shift = db.get(ScheduleShift, shift_id)
    if not shift or shift.deleted_at is not None or shift.school_id != member.school_id:
        raise HTTPException(status_code=404, detail="Smena topilmadi")
    return shift


def _class_shift(db: Session, school_class: SchoolClass) -> ScheduleShift | None:
    shift = db.get(ScheduleShift, school_class.shift_id) if school_class.shift_id else None
    return shift if shift and shift.deleted_at is None and shift.school_id == school_class.school_id else None


def _active_plans(db: Session, **filters) -> list[ClassSubjectPlan]:
    query = select(ClassSubjectPlan).where(ClassSubjectPlan.deleted_at.is_(None))
    for name, value in filters.items():
        query = query.where(getattr(ClassSubjectPlan, name) == value)
    return db.scalars(query.order_by(ClassSubjectPlan.id)).all()


def _plan_rows(db: Session, school_class: SchoolClass, slots: list[LessonSlot]) -> list[dict]:
    """Sinf o'quv rejasi: har fandan haftalik soat, kim o'tadi va jadvalga nechtasi qo'yilgan."""

    placed: dict[str, int] = {}
    for slot in slots:
        key = (slot.subject or "").lower()
        placed[key] = placed.get(key, 0) + 1
    rows = []
    for plan in _active_plans(db, class_id=school_class.id):
        teacher = db.get(User, plan.teacher_user_id) if plan.teacher_user_id else None
        rows.append({
            "subject": plan.subject,
            "hours": plan.hours,
            "teacher_user_id": plan.teacher_user_id,
            "teacher_name": teacher.full_name if teacher else "",
            "placed": placed.get(plan.subject.lower(), 0),
        })
    return rows


def _active_school_member(db: Session, school_id: int, user_id: int) -> SchoolMember | None:
    return db.scalar(
        select(SchoolMember).where(
            SchoolMember.school_id == school_id, SchoolMember.user_id == user_id, SchoolMember.left_at.is_(None)
        )
    )


def _day_hours(member: SchoolMember) -> dict[int, tuple[str, str]]:
    """Har kunning o'z ish vaqti: {0: ("08:00", "12:00"), ...}."""

    try:
        raw = json.loads(member.work_hours) if member.work_hours else {}
    except ValueError:
        return {}
    return {int(day): (hours[0], hours[1]) for day, hours in raw.items() if isinstance(hours, list) and len(hours) == 2}


def _work_window(member: SchoolMember, weekday: int) -> tuple[str | None, str | None]:
    """Shu kundagi ish vaqti: kunning o'zi, bo'lmasa umumiy vaqt, u ham bo'lmasa — cheklanmagan."""

    return _day_hours(member).get(weekday, (member.work_start, member.work_end))


def _workload_view(member: SchoolMember) -> dict:
    return {
        "weekly_hours": member.weekly_hours,
        "work_days": _json_list(member.work_days) if member.work_days else [],
        "work_start": member.work_start,
        "work_end": member.work_end,
        "day_hours": {str(day): {"start": start, "end": end} for day, (start, end) in sorted(_day_hours(member).items())},
    }


def _apply_shift_period(db: Session, school_class: SchoolClass, body: LessonSlotIn) -> None:
    """Setkadagi dars raqami berilgan bo'lsa — vaqt sinf smenasidan olinadi."""

    if body.period is None:
        return
    shift = _class_shift(db, school_class)
    if not shift:
        raise HTTPException(status_code=422, detail="Sinfga smena (qo'ng'iroq setkasi) tanlanmagan")
    periods = _json_list(shift.periods)
    if body.period > len(periods):
        raise HTTPException(status_code=422, detail=f"«{shift.name}» da {body.period}-dars yo'q")
    if body.weekday not in _json_list(shift.weekdays):
        raise HTTPException(status_code=422, detail=f"«{shift.name}» {WEEKDAYS[body.weekday].lower()} kuni ishlamaydi")
    body.start_time, body.end_time = periods[body.period - 1]["start"], periods[body.period - 1]["end"]


def _check_teacher_hours(db: Session, school_id: int, body: LessonSlotIn) -> None:
    """O'qituvchining ish kunidan yoki ish vaqtidan tashqariga dars qo'yilmaydi."""

    if not body.teacher_user_id:
        return
    member = _active_school_member(db, school_id, body.teacher_user_id)
    if not member:
        return
    name = member.user.full_name if member.user else "O'qituvchi"
    days = _json_list(member.work_days) if member.work_days else []
    if days and body.weekday not in days:
        listed = ", ".join(WEEKDAYS[day] for day in days if 0 <= day < 7)
        raise HTTPException(
            status_code=409, detail=f"{name} {WEEKDAYS[body.weekday].lower()} kuni ishlamaydi (ish kunlari: {listed})"
        )
    start, end = _work_window(member, body.weekday)
    if start and end and (body.start_time < start or body.end_time > end):
        raise HTTPException(
            status_code=409,
            detail=f"{name}ning {WEEKDAYS[body.weekday].lower()} kungi ish vaqti {start}–{end}, "
            f"bu dars esa {body.start_time}–{body.end_time}",
        )


def _retime_slots(db: Session, school_id: int, slots: list[LessonSlot], shift: ScheduleShift) -> None:
    """Setkadagi darslar smenaning (yangi) vaqtiga ko'chiriladi. Dars yoki kun setkada bo'lmasa,
    yoki o'qituvchida boshqa dars bilan ustma-ust tushsa — hech narsa o'zgarmaydi (409)."""

    periods, weekdays = _json_list(shift.periods), _json_list(shift.weekdays)
    changed = []
    for slot in slots:
        if slot.period is None:
            continue
        if slot.period > len(periods) or slot.weekday not in weekdays:
            raise HTTPException(
                status_code=409,
                detail=f"{WEEKDAYS[slot.weekday]} {slot.period}-darsda «{slot.subject or 'dars'}» bor — «{shift.name}» setkasida "
                "bu kun yoki dars yo'q. Avval o'sha darslarni jadvaldan olib tashlang.",
            )
        slot.start_time, slot.end_time = periods[slot.period - 1]["start"], periods[slot.period - 1]["end"]
        changed.append(slot)
    db.flush()
    for slot in changed:
        if not slot.teacher_user_id:
            continue
        for other in _active_slots(db, school_id=school_id, teacher_user_id=slot.teacher_user_id, weekday=slot.weekday):
            if other.id != slot.id and other.start_time < slot.end_time and slot.start_time < other.end_time:
                db.rollback()
                teacher = db.get(User, slot.teacher_user_id)
                teacher_name = teacher.full_name if teacher else "O'qituvchi"
                raise HTTPException(
                    status_code=409,
                    detail=f"Yangi vaqtda {teacher_name}ning {WEEKDAYS[slot.weekday].lower()} "
                    f"kuni ikki darsi ustma-ust tushadi ({slot.start_time}–{slot.end_time})",
                )


@app.get("/api/schools/timetable")
def school_timetable(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Jadval rejasi: smenalar, sinflarning o'quv rejasi, o'qituvchilar yuklamasi va fanlar bo'yicha jamlanma."""

    member = _require_school_member(db, user)
    school_id = member.school_id
    leader = _is_director(member)
    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == school_id, SchoolClass.deleted_at.is_(None))
    ).all())
    class_ids = {item.id for item in classes}
    members = db.scalars(
        select(SchoolMember).where(SchoolMember.school_id == school_id, SchoolMember.left_at.is_(None)).order_by(SchoolMember.id)
    ).all()
    names = {item.user_id: (item.user.full_name if item.user else "Noma'lum") for item in members}
    slots = [slot for slot in _active_slots(db, school_id=school_id) if slot.class_id in class_ids]
    plans = [plan for plan in _active_plans(db, school_id=school_id) if plan.class_id in class_ids]
    shift_ids = {shift.id for shift in _active_shifts(db, school_id)}

    class_rows = []
    for school_class in classes:
        own_slots = [slot for slot in slots if slot.class_id == school_class.id]
        rows = _plan_rows(db, school_class, own_slots)
        class_rows.append({
            "id": school_class.id,
            "name": school_class.name,
            "shift_id": school_class.shift_id if school_class.shift_id in shift_ids else None,
            "can_edit": _can_edit_schedule(school_class, member),
            "plan": rows,
            "planned_hours": sum(row["hours"] for row in rows),
            "placed_hours": len(own_slots),
        })

    teachers, subjects = [], []
    if leader:
        assigned: dict[int, int] = {}
        for plan in plans:
            if plan.teacher_user_id:
                assigned[plan.teacher_user_id] = assigned.get(plan.teacher_user_id, 0) + plan.hours
        placed: dict[int, int] = {}
        for slot in slots:
            if slot.teacher_user_id:
                placed[slot.teacher_user_id] = placed.get(slot.teacher_user_id, 0) + 1
        teachers = [
            {
                "user_id": item.user_id,
                "name": names[item.user_id],
                "subjects": _read_subjects(item),
                **_workload_view(item),
                "assigned_hours": assigned.get(item.user_id, 0),
                "placed_hours": placed.get(item.user_id, 0),
            }
            for item in members
        ]
        # Fanlar bo'yicha: nechta o'qituvchi o'tadi, rejada haftasiga necha soat, qanchasiga o'qituvchi biriktirilmagan
        summary: dict[str, dict] = {}

        def entry(name: str) -> dict:
            return summary.setdefault(name.lower(), {"subject": name, "teachers": [], "needed_hours": 0, "unassigned_hours": 0})

        for item in teachers:
            for subject in item["subjects"]:
                entry(subject)["teachers"].append(item["name"])
        for plan in plans:
            row = entry(plan.subject)
            row["needed_hours"] += plan.hours
            if not plan.teacher_user_id:
                row["unassigned_hours"] += plan.hours
        subjects = sorted(summary.values(), key=lambda row: row["subject"].lower())

    return {
        "can_manage": leader,
        "shifts": [_shift_view(shift) for shift in _active_shifts(db, school_id)],
        "classes": class_rows,
        "teachers": teachers,
        "subjects": subjects,
    }


@app.post("/api/schools/shifts")
def create_shift(body: ShiftIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _require_school_member(db, user)
    _require_director(member)
    db.add(ScheduleShift(
        school_id=member.school_id,
        name=body.name,
        weekdays=json.dumps(body.weekdays),
        periods=json.dumps([period.model_dump() for period in body.periods]),
    ))
    db.commit()
    return school_timetable(user, db)


@app.put("/api/schools/shifts/{shift_id}")
def update_shift(shift_id: int, body: ShiftIn, drop_outside: bool = False, user: User = Depends(get_current_user),
                 db: Session = Depends(get_db)):
    """Setka o'zgarsa — shu smenadagi sinflarning darslari yangi vaqtga o'zi ko'chadi.
    drop_outside=true — yangi setkada yo'q kun yoki darsga tushgan darslar jadvaldan olib tashlanadi."""

    member = _require_school_member(db, user)
    _require_director(member)
    shift = _school_shift(db, member, shift_id)
    shift.name = body.name
    shift.weekdays = json.dumps(body.weekdays)
    shift.periods = json.dumps([period.model_dump() for period in body.periods])
    class_ids = db.scalars(
        select(SchoolClass.id).where(SchoolClass.shift_id == shift.id, SchoolClass.deleted_at.is_(None))
    ).all()
    slots = [slot for class_id in class_ids for slot in _active_slots(db, class_id=class_id)]
    removed = 0
    if drop_outside:
        periods, weekdays = _json_list(shift.periods), _json_list(shift.weekdays)
        now = utcnow()
        for slot in slots:
            if slot.period is not None and (slot.period > len(periods) or slot.weekday not in weekdays):
                slot.deleted_at = now
                removed += 1
        slots = [slot for slot in slots if slot.deleted_at is None]
    _retime_slots(db, member.school_id, slots, shift)
    db.commit()
    return {**school_timetable(user, db), "removed_lessons": removed}


@app.delete("/api/schools/shifts/{shift_id}")
def delete_shift(shift_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _require_school_member(db, user)
    _require_director(member)
    shift = _school_shift(db, member, shift_id)
    used = db.scalars(
        select(SchoolClass.name).where(SchoolClass.shift_id == shift.id, SchoolClass.deleted_at.is_(None))
    ).all()
    if used:
        raise HTTPException(
            status_code=409, detail=f"Bu smenada sinflar bor ({', '.join(used)}) — avval ularni boshqa smenaga o'tkazing"
        )
    shift.deleted_at = utcnow()
    db.commit()
    return school_timetable(user, db)


@app.put("/api/classes/{class_id}/shift")
def set_class_shift(class_id: int, body: ClassShiftIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf smenasi. Setkadagi darslari bo'lsa — yangi smenaning shu raqamli darsiga ko'chadi."""

    school_class, member = _school_class(db, user, class_id)
    _require_director(member)
    slots = _active_slots(db, class_id=school_class.id)
    if body.shift_id is None:
        if any(slot.period is not None for slot in slots):
            raise HTTPException(status_code=409, detail="Sinf jadvalida setkadagi darslar bor — smenani olib tashlab bo'lmaydi")
        school_class.shift_id = None
    else:
        shift = _school_shift(db, member, body.shift_id)
        school_class.shift_id = shift.id
        _retime_slots(db, member.school_id, slots, shift)
    db.commit()
    return school_timetable(user, db)


@app.put("/api/classes/{class_id}/plan")
def set_class_plan(class_id: int, body: ClassPlanIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf o'quv rejasi: fan — haftasiga necha soat — kim o'tadi. Ro'yxatda yo'q fanlar rejadan chiqariladi
    (jadvaldagi darslari o'z joyida qoladi)."""

    school_class, member = _school_class(db, user, class_id)
    _require_director(member)
    for item in body.items:
        _check_slot_teacher(db, school_class.school_id, item.teacher_user_id)
    existing = {plan.subject.lower(): plan for plan in _active_plans(db, class_id=school_class.id)}
    for item in body.items:
        plan = existing.pop(item.subject.lower(), None)
        if plan:
            plan.subject, plan.hours, plan.teacher_user_id = item.subject, item.hours, item.teacher_user_id
        else:
            db.add(ClassSubjectPlan(
                school_id=school_class.school_id,
                class_id=school_class.id,
                subject=item.subject,
                hours=item.hours,
                teacher_user_id=item.teacher_user_id,
            ))
    for plan in existing.values():
        plan.deleted_at = utcnow()
    db.commit()
    return school_timetable(user, db)


@app.post("/api/schools/timetable/auto")
def auto_timetable(body: AutoTimetableIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Jadvalni o'quv reja, smena setkasi va o'qituvchilar ish vaqti bo'yicha avtomatik tuzadi.
    apply=False — natija faqat ko'rsatiladi; apply=True — saqlanadi (natija har safar bir xil chiqadi).
    """

    member = _require_school_member(db, user)
    _require_director(member)
    school_id = member.school_id
    classes = _sorted_classes(db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == school_id, SchoolClass.deleted_at.is_(None))
    ).all())
    wanted = set(body.class_ids) if body.class_ids else None
    plans = _active_plans(db, school_id=school_id)
    plan_by_class: dict[int, list[ClassSubjectPlan]] = {}
    for plan in plans:
        plan_by_class.setdefault(plan.class_id, []).append(plan)

    targets, skipped = [], []
    for school_class in classes:
        if wanted is not None and school_class.id not in wanted:
            continue
        shift = _class_shift(db, school_class)
        if not plan_by_class.get(school_class.id):
            skipped.append({"class_id": school_class.id, "class_name": school_class.name, "reason": "o'quv reja kiritilmagan"})
        elif not shift:
            skipped.append({"class_id": school_class.id, "class_name": school_class.name, "reason": "smena (qo'ng'iroq setkasi) tanlanmagan"})
        else:
            targets.append((school_class, shift))
    target_ids = {school_class.id for school_class, _ in targets}

    all_slots = _active_slots(db, school_id=school_id)
    removed = [slot for slot in all_slots if body.mode == "replace" and slot.class_id in target_ids]
    removed_ids = {slot.id for slot in removed}

    inputs = []
    for school_class, shift in targets:
        kept = [
            {"weekday": slot.weekday, "period": slot.period, "start": slot.start_time, "end": slot.end_time,
             "subject": slot.subject, "teacher_user_id": slot.teacher_user_id}
            for slot in all_slots if slot.class_id == school_class.id and slot.id not in removed_ids
        ]
        inputs.append(ClassInput(
            id=school_class.id,
            name=school_class.name,
            weekdays=_json_list(shift.weekdays),
            periods=[Period(item["start"], item["end"]) for item in _json_list(shift.periods)],
            plan=[{"subject": plan.subject, "hours": plan.hours, "teacher_user_id": plan.teacher_user_id}
                  for plan in plan_by_class[school_class.id]],
            kept=kept,
        ))
    outside_busy = [
        {"teacher_user_id": slot.teacher_user_id, "weekday": slot.weekday, "start": slot.start_time, "end": slot.end_time}
        for slot in all_slots if slot.class_id not in target_ids and slot.teacher_user_id
    ]
    members = db.scalars(select(SchoolMember).where(SchoolMember.school_id == school_id, SchoolMember.left_at.is_(None))).all()
    teachers = {
        item.user_id: TeacherInput(
            name=item.user.full_name if item.user else "O'qituvchi",
            work_days=set(_json_list(item.work_days)) if item.work_days else None,
            work_start=item.work_start,
            work_end=item.work_end,
            day_hours=_day_hours(item),
        )
        for item in members
    }
    result = build_timetable(inputs, teachers, outside_busy, seed=school_id)

    names = {item.user_id: teachers[item.user_id].name for item in members}
    class_names = {school_class.id: school_class.name for school_class, _ in targets}
    per_class = []
    for school_class, _ in targets:
        planned = sum(plan.hours for plan in plan_by_class[school_class.id])
        new = sum(1 for lesson in result["lessons"] if lesson["class_id"] == school_class.id)
        kept = sum(1 for slot in all_slots if slot.class_id == school_class.id and slot.id not in removed_ids)
        missing = sum(item["hours"] for item in result["unplaced"] if item["class_id"] == school_class.id)
        per_class.append({"class_id": school_class.id, "class_name": school_class.name, "planned": planned,
                          "kept": kept, "new": new, "missing": missing})

    if body.apply:
        now = utcnow()
        for slot in removed:
            slot.deleted_at = now
        for lesson in result["lessons"]:
            db.add(LessonSlot(
                school_id=school_id,
                class_id=lesson["class_id"],
                weekday=lesson["weekday"],
                start_time=lesson["start_time"],
                end_time=lesson["end_time"],
                subject=lesson["subject"],
                teacher_user_id=lesson["teacher_user_id"],
                period=lesson["period"],
                created_by=user.id,
            ))
        db.commit()

    return {
        "applied": body.apply,
        "mode": body.mode,
        "classes": per_class,
        "skipped": skipped,
        "removed": len(removed),
        "lessons": [
            {**lesson, "class_name": class_names[lesson["class_id"]], "weekday_label": WEEKDAYS[lesson["weekday"]],
             "teacher_name": names.get(lesson["teacher_user_id"], "") if lesson["teacher_user_id"] else ""}
            for lesson in result["lessons"]
        ],
        "unplaced": result["unplaced"],
        "gaps": result["gaps"],
    }


def _teacher_week_text(school_name: str, teacher_name: str, slots: list[LessonSlot], class_names: dict[int, str]) -> str:
    """O'qituvchiga botda yuboriladigan haftalik jadval (oddiy matn)."""

    lines = [f"📅 {teacher_name}, sizning haftalik dars jadvalingiz", f"🏫 {school_name}", ""]
    by_day: dict[int, list[LessonSlot]] = {}
    for slot in slots:
        by_day.setdefault(slot.weekday, []).append(slot)
    for day in sorted(by_day):
        lines.append(f"{WEEKDAYS[day]}:")
        for slot in sorted(by_day[day], key=lambda item: item.start_time):
            number = f"{slot.period}-dars, " if slot.period else ""
            lines.append(f"  • {number}{slot.start_time}–{slot.end_time} — {class_names.get(slot.class_id, '')} · {slot.subject or 'dars'}")
        lines.append("")
    lines.append(f"Jami: haftasiga {len(slots)} soat. Jadval ilovada: Maktab → Dars jadvali → Mening.")
    return "\n".join(lines)


@app.post("/api/schools/timetable/notify")
def notify_timetable(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Har bir o'qituvchiga bot orqali o'zining haftalik jadvalini yuboradi (faqat rahbariyat)."""

    member = _require_school_member(db, user)
    _require_director(member)
    school = db.get(PrivateSchool, member.school_id)
    classes = db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))
    ).all()
    class_names = {item.id: item.name for item in classes}
    slots = [slot for slot in _active_slots(db, school_id=member.school_id) if slot.class_id in class_names and slot.teacher_user_id]
    by_teacher: dict[int, list[LessonSlot]] = {}
    for slot in slots:
        by_teacher.setdefault(slot.teacher_user_id, []).append(slot)
    sent = []
    for teacher_id, own in by_teacher.items():
        target = _active_school_member(db, member.school_id, teacher_id)
        if not target or not target.user or not target.user.tg_id:
            continue
        _send_result_notice_in_background((target.user.tg_id, _teacher_week_text(school.name, target.user.full_name, own, class_names)))
        sent.append(target.user.full_name)
    return {"sent": len(sent), "teachers": sorted(sent)}


@app.put("/api/schools/members/{user_id}/workload")
def set_member_workload(user_id: int, body: WorkloadIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'qituvchi yuklamasi: haftalik soat me'yori, ish kunlari va ish vaqti."""

    member = _require_school_member(db, user)
    _require_director(member)
    target = _active_school_member(db, member.school_id, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="Bu o'qituvchi maktabingizda yo'q")
    target.weekly_hours = body.weekly_hours
    target.work_days = json.dumps(body.work_days) if body.work_days else None
    target.work_start, target.work_end = body.work_start, body.work_end
    target.work_hours = (
        json.dumps({str(day): [hours.start, hours.end] for day, hours in sorted(body.day_hours.items())})
        if body.day_hours else None
    )
    db.commit()
    return school_timetable(user, db)


def _today_lessons(db: Session, member: SchoolMember, user: User, classes: list[SchoolClass], local_now: datetime,
                   today_attendance: list[Attendance]) -> list[dict]:
    """Bugungi darslar: rahbariyat — butun maktab, qolganlar — o'zi o'tadigan va kuratorlik sinfi darslari."""

    names = {item.id: item.name for item in classes}
    slots = [slot for slot in _active_slots(db, school_id=member.school_id, weekday=local_now.weekday()) if slot.class_id in names]
    if not _is_director(member):
        curated = {item.id for item in classes if _is_class_teacher(item, user.id)}
        slots = [slot for slot in slots if slot.teacher_user_id == user.id or slot.class_id in curated]
    now_text = local_now.strftime("%H:%M")
    rows = []
    for slot in slots:
        taken = any(
            attendance.class_id == slot.class_id
            and (attendance.lesson_start == slot.start_time or (slot.subject and attendance.subject == slot.subject))
            for attendance in today_attendance
        )
        state = "done" if slot.end_time <= now_text else "now" if slot.start_time <= now_text else "next"
        rows.append({**_slot_view(db, slot, names[slot.class_id]), "state": state, "attendance_taken": taken,
                     "mine": slot.teacher_user_id == user.id})
    rows.sort(key=lambda row: (row["start_time"], row["class_name"]))
    return rows


# ---------- E'lonlar ----------

def _announcement_view(db: Session, item: SchoolAnnouncement, member: SchoolMember, user: User) -> dict:
    author = db.get(User, item.author_user_id)
    school_class = db.get(SchoolClass, item.class_id) if item.class_id else None
    return {
        "id": item.id,
        "title": item.title,
        "body": item.body,
        "class_id": item.class_id,
        "class_name": school_class.name if school_class else None,
        "author": author.full_name if author else "",
        "created_at": item.created_at,
        "staff_notified": item.staff_notified,
        "can_delete": item.author_user_id == user.id or _is_director(member),
    }


def _announcement_text(db: Session, item: SchoolAnnouncement, for_parents: bool = False) -> str:
    school = db.get(PrivateSchool, item.school_id)
    school_class = db.get(SchoolClass, item.class_id) if item.class_id else None
    author = db.get(User, item.author_user_id)
    header = f"📢 «{school.name}»" + (f" · {school_class.name} sinfi" if school_class else "")
    lines = [header, "", item.title]
    if item.body:
        lines += ["", item.body]
    signature = f"«{school.name}» ma'muriyati" if for_parents else (author.full_name if author else "")
    lines += ["", f"Hurmat bilan, {signature}" if for_parents else f"— {signature}"]
    if for_parents:
        lines.insert(1, "Hurmatli ota-onalar!")
    return "\n".join(lines)


@app.get("/api/schools/announcements")
def list_announcements(limit: int = 30, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _require_school_member(db, user)
    items = db.scalars(
        select(SchoolAnnouncement)
        .where(SchoolAnnouncement.school_id == member.school_id, SchoolAnnouncement.deleted_at.is_(None))
        .order_by(SchoolAnnouncement.id.desc())
        .limit(max(1, min(limit, 100)))
    ).all()
    curated = db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None),
                                  SchoolClass.curator_user_id == user.id)
    ).all()
    return {
        "items": [_announcement_view(db, item, member, user) for item in items],
        # Butun maktabga — rahbariyat; sinfga — rahbariyat yoki o'sha sinf kuratori
        "can_post_school": _is_director(member),
        "post_classes": (
            [{"id": item.id, "name": item.name} for item in _sorted_classes(db.scalars(
                select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))))]
            if _is_director(member)
            else [{"id": item.id, "name": item.name} for item in curated]
        ),
    }


@app.post("/api/schools/announcements")
def create_announcement(body: AnnouncementIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _require_school_member(db, user)
    school_class = None
    if body.class_id is None:
        if not _is_director(member):
            raise HTTPException(status_code=403, detail="Butun maktabga e'lonni rahbariyat beradi")
    else:
        school_class, member = _school_class(db, user, body.class_id)
        if not (_is_director(member) or school_class.curator_user_id == user.id):
            raise HTTPException(status_code=403, detail="Sinfga e'lonni rahbariyat yoki sinf kuratori beradi")

    item = SchoolAnnouncement(
        school_id=member.school_id, class_id=body.class_id, author_user_id=user.id, title=body.title, body=body.body
    )
    db.add(item)
    db.flush()

    if body.notify_staff:
        # Butun maktab — barcha xodimlarga; sinf — rahbariyat, kurator va o'sha sinfda dars o'tadiganlarga
        members = [item_member for item_member in member.school.members if item_member.left_at is None]
        if school_class:
            teachers = {slot.teacher_user_id for slot in _active_slots(db, class_id=school_class.id)}
            members = [m for m in members if _is_director(m) or m.user_id == school_class.curator_user_id or m.user_id in teachers]
        text_message = _announcement_text(db, item)
        sent = 0
        for target in members:
            if target.user_id == user.id or not target.user or not target.user.tg_id:
                continue
            _send_result_notice((target.user.tg_id, text_message))
            sent += 1
        item.staff_notified = sent

    db.commit()
    db.refresh(item)
    logging.getLogger("backend").info("E'lon: id=%s school=%s class=%s staff=%s", item.id, item.school_id, item.class_id, item.staff_notified)
    return _announcement_view(db, item, member, user)


def _visible_announcement(db: Session, user: User, announcement_id: int) -> tuple[SchoolAnnouncement, SchoolMember]:
    member = _require_school_member(db, user)
    item = db.get(SchoolAnnouncement, announcement_id)
    if not item or item.deleted_at is not None or item.school_id != member.school_id:
        raise HTTPException(status_code=404, detail="E'lon topilmadi")
    return item, member


@app.get("/api/announcements/{announcement_id}/text")
def announcement_text(announcement_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Ota-onalar guruhiga yuborish uchun matn."""
    item, _ = _visible_announcement(db, user, announcement_id)
    return {"text": _announcement_text(db, item, for_parents=True)}


@app.delete("/api/announcements/{announcement_id}")
def delete_announcement(announcement_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    item, member = _visible_announcement(db, user, announcement_id)
    if not (item.author_user_id == user.id or _is_director(member)):
        raise HTTPException(status_code=403, detail="E'lonni muallifi yoki rahbariyat o'chiradi")
    item.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


# ---------- Maktab o'quvchisining kabineti: login, parol, davomat, natijalar, reyting, jadval ----------
# O'quvchi (yoki ota-onasi) ilovada maktabini topib, maktab bergan login-parol bilan kiradi va faqat O'Z
# ma'lumotlarini ko'radi. Telegram shart emas — seans X-Pupil-Session sarlavhasida keladi.

PUPIL_LOCK_FAILURES = 5  # shuncha xato paroldan keyin akkaunt vaqtincha yopiladi
PUPIL_LOCK_MINUTES = 15
PUPIL_IP_LIMIT = 30  # bitta manzildan 15 daqiqada shuncha xato urinish (turli loginlarni terib chiqishga qarshi)
PUPIL_HASH_ITERATIONS = 60_000
_pupil_ip_failures: dict[str, list[float]] = {}


def _new_pupil_password() -> str:
    """Maktab beradigan boshlang'ich parol — 6 xonali raqam (chop etiladi, bola oson kiritadi)."""
    return f"{secrets.randbelow(10**6):06d}"


def _hash_pupil_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), PUPIL_HASH_ITERATIONS).hex()
    return f"pbkdf2${PUPIL_HASH_ITERATIONS}${salt}${digest}"


def _pupil_password_ok(account: PupilAccount, password: str) -> bool:
    if account.initial_password:
        return hmac.compare_digest(password.strip(), account.initial_password)
    try:
        _, iterations, salt, digest = (account.password_hash or "").split("$")
        candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(iterations)).hex()
    except ValueError:
        return False
    return hmac.compare_digest(candidate, digest)


def _pupil_token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _pupil_class(db: Session, student: ClassStudent | None) -> SchoolClass | None:
    """O'quvchining asl sinfi — u hali ro'yxatda bo'lsa (chiqarilgan, sinfi o'chirilgan bo'lsa — None)."""
    if not student or student.removed_at is not None or student.source_student_id is not None:
        return None
    school_class = db.get(SchoolClass, student.class_id)
    if not school_class or school_class.deleted_at is not None or school_class.subject:
        return None
    return school_class


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for", "")
    return forwarded.split(",")[0].strip() or (request.client.host if request.client else "")


def get_current_pupil(x_pupil_session: str = Header(default=""), db: Session = Depends(get_db)) -> SimpleNamespace:
    session = db.scalar(
        select(PupilSession).where(PupilSession.token_hash == _pupil_token_hash(x_pupil_session),
                                   PupilSession.revoked_at.is_(None))
    ) if x_pupil_session else None
    account = db.get(PupilAccount, session.account_id) if session else None
    student = db.get(ClassStudent, account.student_id) if account else None
    school_class = _pupil_class(db, student)
    if not school_class:
        raise HTTPException(status_code=401, detail="Seans tugagan — maktabingizga qaytadan kiring")
    if not session.last_seen_at or session.last_seen_at < utcnow() - timedelta(hours=1):
        session.last_seen_at = utcnow()
        db.commit()
    return SimpleNamespace(account=account, student=student, school_class=school_class, session=session)


def _ensure_pupil_accounts(db: Session, school_class: SchoolClass) -> list[tuple[ClassStudent, PupilAccount]]:
    """Sinf o'quvchilarining kirish akkauntlari (yo'qlariga — login va boshlang'ich parol beriladi)."""

    _assign_student_codes(db, school_class.school_id)
    # Ro'yxatdan chiqqan o'quvchilarning akkauntlari o'chadi — login bo'shaydi, eski seanslar ishlamaydi
    stale = db.scalars(
        select(PupilAccount)
        .join(ClassStudent, ClassStudent.id == PupilAccount.student_id)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(PupilAccount.school_id == school_class.school_id,
               or_(ClassStudent.removed_at.is_not(None), SchoolClass.deleted_at.is_not(None)))
    ).all()
    for account in stale:
        db.execute(delete(PupilSession).where(PupilSession.account_id == account.id))
        db.delete(account)
    db.flush()

    students = _active_students(school_class)
    existing = {
        account.student_id: account
        for account in db.scalars(select(PupilAccount).where(PupilAccount.student_id.in_([s.id for s in students] or [-1])))
    }
    taken = set(db.scalars(select(PupilAccount.login).where(PupilAccount.school_id == school_class.school_id)).all())
    rows = []
    for student in students:
        account = existing.get(student.id)
        if account is None:
            login = str(student.student_code) if student.student_code and str(student.student_code) not in taken else None
            while login is None or login in taken:
                login = str(100000 + secrets.randbelow(900000))  # ID'siz o'quvchi — 6 xonali (4 xonali ID'lar bilan to'qnashmaydi)
            account = PupilAccount(school_id=school_class.school_id, student_id=student.id, login=login,
                                   initial_password=_new_pupil_password(), must_change=True)
            db.add(account)
            taken.add(login)
        rows.append((student, account))
    db.commit()
    return rows


def _pupil_login_view(student: ClassStudent, account: PupilAccount) -> dict:
    return {
        "id": student.id,
        "name": student.full_name,
        "code": student.student_code,
        "login": account.login,
        "password": account.initial_password,  # o'quvchi o'z parolini qo'ygan bo'lsa — None
        "changed": not account.must_change,
        "last_login_at": account.last_login_at,
    }


def _pupil_logins_class(db: Session, user: User, class_id: int) -> SchoolClass:
    school_class = _managed_class(db, user, class_id)
    if school_class.subject:
        raise HTTPException(status_code=400, detail="Fan guruhida login berilmaydi — o'quvchi uni o'z sinfidan oladi")
    return school_class


@app.get("/api/classes/{class_id}/pupil-logins")
def class_pupil_logins(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Sinf o'quvchilarining ilovaga kirish ma'lumotlari (kurator va rahbariyat) — chop etib tarqatish uchun."""
    school_class = _pupil_logins_class(db, user, class_id)
    school = db.get(PrivateSchool, school_class.school_id)
    return {
        "school_name": school.name if school else "",
        "class_name": school_class.name,
        "students": [_pupil_login_view(student, account) for student, account in _ensure_pupil_accounts(db, school_class)],
    }


@app.post("/api/classes/{class_id}/students/{student_id}/pupil-password")
def reset_pupil_password(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Yangi boshlang'ich parol (o'quvchi unutgan bo'lsa yoki parol begonaga ma'lum bo'lsa) — eski seanslar tugaydi."""
    school_class = _pupil_logins_class(db, user, class_id)
    row = next(((student, account) for student, account in _ensure_pupil_accounts(db, school_class) if student.id == student_id), None)
    if not row:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    student, account = row
    account.initial_password = _new_pupil_password()
    account.password_hash = None
    account.must_change = True
    account.failures = 0
    account.locked_until = None
    db.execute(update(PupilSession).where(PupilSession.account_id == account.id, PupilSession.revoked_at.is_(None))
               .values(revoked_at=utcnow()))
    db.commit()
    return _pupil_login_view(student, account)


def pupil_logins_pdf(school_name: str, class_name: str, rows: list[dict]) -> bytes:
    """Kirish ma'lumotlari — chop etib, qirqib har o'quvchiga beriladi (yoki ota-onalar guruhiga)."""

    styles = _report_styles()
    buffer = io.BytesIO()
    document = _brand_document(buffer, f"{class_name} — ilovaga kirish")
    width = document.width
    steps = (f"1) Chittak ilovasini oching (chittak.uz yoki Telegram) → «Maktab o'quvchisi».  "
             f"2) Maktabingizni toping: «{school_name}».  3) Login va parolni kiriting — birinchi kirishda "
             "o'z parolingizni o'rnatasiz.")
    header = ["№", "F.I.Sh.", "Login", "Parol"]
    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in header]]
    for index, row in enumerate(rows, start=1):
        data.append([
            Paragraph(str(index), styles["cell"]),
            Paragraph(xml_escape(row["name"]), styles["cell"]),
            Paragraph(f"<b>{xml_escape(row['login'])}</b>", styles["cell"]),
            Paragraph(f"<b>{xml_escape(row['password'])}</b>" if row["password"] else "o'zi o'rnatgan", styles["cell"]),
        ])
    table = Table(data, colWidths=[width * 0.08, width * 0.52, width * 0.18, width * 0.22], repeatRows=1)
    commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD), ("LINEBELOW", (0, 0), (-1, -1), 0.6, REPORT_GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]
    table.setStyle(TableStyle(commands))
    document.build([
        Paragraph(xml_escape(f"{class_name} — ilovaga kirish ma'lumotlari"), styles["title"]),
        Paragraph(xml_escape(school_name), styles["meta"]),
        Spacer(1, 4),
        Paragraph(xml_escape(steps), styles["cell"]),
        Spacer(1, 10),
        table,
    ], canvasmaker=_brand_canvas(school_name, f"{class_name} · kirish ma'lumotlari"))
    return buffer.getvalue()


def _pupil_logins_file(db: Session, user: User, class_id: int) -> tuple[str, bytes]:
    data = class_pupil_logins(class_id, user, db)
    file_name = f"kirish_{data['class_name']}".replace("/", "-")
    return file_name, pupil_logins_pdf(data["school_name"], data["class_name"], data["students"])


@app.get("/api/classes/{class_id}/pupil-logins/file")
def class_pupil_logins_file(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    file_name, pdf = _pupil_logins_file(db, user, class_id)
    return StreamingResponse(io.BytesIO(pdf), media_type="application/pdf",
                             headers={"Content-Disposition": f'attachment; filename="{file_name}.pdf"'.encode("ascii", "ignore").decode()})


@app.post("/api/classes/{class_id}/pupil-logins/file/send")
def send_class_pupil_logins(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kirish ma'lumotlari PDF'i — bot orqali foydalanuvchining o'z chatiga (u yerdan chop etiladi)."""
    file_name, pdf = _pupil_logins_file(db, user, class_id)
    _send_pdf_to_telegram(user.tg_id, f"{file_name}.pdf", pdf, "🔑 O'quvchilarning ilovaga kirish ma'lumotlari")
    return {"ok": True}


@app.get("/api/pupil/schools")
def pupil_school_search(q: str = "", db: Session = Depends(get_db)):
    """O'quvchi o'z maktabini topadi: nomi, tumani yoki viloyati bo'yicha (kamida 2 harf)."""
    words = [word for word in q.lower().split() if word][:4]
    if len(q.strip()) < 2 or not words:
        return []
    query = select(PrivateSchool).where(PrivateSchool.deleted_at.is_(None))
    for word in words:
        like = f"%{word}%"
        query = query.where(or_(func.lower(PrivateSchool.name).like(like), func.lower(PrivateSchool.district).like(like),
                                func.lower(PrivateSchool.region).like(like)))
    return [
        {"id": school.id, "name": school.name, "region": school.region, "district": school.district, "kind": school.kind}
        for school in db.scalars(query.order_by(PrivateSchool.name).limit(20)).all()
    ]


@app.post("/api/pupil/login")
def pupil_login(body: PupilLoginIn, request: Request, db: Session = Depends(get_db)):
    ip, now = _client_ip(request), time.monotonic()
    recent = [moment for moment in _pupil_ip_failures.get(ip, []) if now - moment < PUPIL_LOCK_MINUTES * 60]
    _pupil_ip_failures[ip] = recent
    if len(recent) >= PUPIL_IP_LIMIT:
        raise HTTPException(status_code=429, detail="Juda ko'p urinish — 15 daqiqadan keyin qayta urinib ko'ring")

    account = db.scalar(select(PupilAccount).where(PupilAccount.school_id == body.school_id,
                                                   PupilAccount.login == body.login.strip()))
    if account and account.locked_until and account.locked_until > utcnow():
        minutes = max(1, int((account.locked_until - utcnow()).total_seconds() // 60) + 1)
        raise HTTPException(status_code=429, detail=f"Parol {PUPIL_LOCK_FAILURES} marta xato kiritildi — {minutes} daqiqadan "
                                                    "keyin urinib ko'ring yoki kuratoringizdan yangi parol so'rang")
    if not account or not _pupil_password_ok(account, body.password):
        _pupil_ip_failures.setdefault(ip, []).append(now)
        if account:
            account.failures += 1
            if account.failures >= PUPIL_LOCK_FAILURES:
                account.failures = 0
                account.locked_until = utcnow() + timedelta(minutes=PUPIL_LOCK_MINUTES)
            db.commit()
        raise HTTPException(status_code=401, detail="Login yoki parol noto'g'ri")
    student = db.get(ClassStudent, account.student_id)
    school_class = _pupil_class(db, student)
    if not school_class:
        raise HTTPException(status_code=403, detail="Siz maktab ro'yxatida yo'qsiz — kuratoringizga murojaat qiling")

    token = secrets.token_urlsafe(32)
    db.add(PupilSession(token_hash=_pupil_token_hash(token), account_id=account.id, last_seen_at=utcnow()))
    account.failures = 0
    account.locked_until = None
    account.last_login_at = utcnow()
    db.commit()
    school = db.get(PrivateSchool, school_class.school_id)
    return {"token": token, "must_change": account.must_change, "name": student.full_name,
            "class_name": school_class.name, "school_name": school.name if school else ""}


@app.post("/api/pupil/password")
def pupil_set_password(body: PupilPasswordIn, pupil=Depends(get_current_pupil), db: Session = Depends(get_db)):
    account = pupil.account
    if not _pupil_password_ok(account, body.old_password):
        raise HTTPException(status_code=400, detail="Joriy parol noto'g'ri")
    if body.new_password.strip() != body.new_password or len(set(body.new_password)) < 2:
        raise HTTPException(status_code=422, detail="Parol bo'sh joysiz va bir xil belgilardan iborat bo'lmasin")
    if account.initial_password and body.new_password == account.initial_password:
        raise HTTPException(status_code=422, detail="Yangi parol maktab bergan paroldan boshqacha bo'lsin")
    account.password_hash = _hash_pupil_password(body.new_password)
    account.initial_password = None
    account.must_change = False
    # Boshqa qurilmalardagi seanslar tugaydi (parol begonaga ma'lum bo'lgan bo'lishi mumkin)
    db.execute(update(PupilSession).where(PupilSession.account_id == account.id, PupilSession.id != pupil.session.id,
                                          PupilSession.revoked_at.is_(None)).values(revoked_at=utcnow()))
    db.commit()
    return {"ok": True}


@app.post("/api/pupil/logout")
def pupil_logout(pupil=Depends(get_current_pupil), db: Session = Depends(get_db)):
    pupil.session.revoked_at = utcnow()
    db.commit()
    return {"ok": True}


@app.get("/api/pupil/me")
def pupil_home(pupil=Depends(get_current_pupil), db: Session = Depends(get_db)):
    """O'quvchining o'z kabineti: davomat, natijalar, sinfdagi o'rni, dars jadvali."""

    student, school_class = pupil.student, pupil.school_class
    school = db.get(PrivateSchool, school_class.school_id)
    curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
    # Fan guruhlaridagi darslar (ingliz tili …) guruhdagi nusxa yozuvida — ular ham hisobga olinadi
    copies = _group_copies(db, [student.id])
    ids = [student.id, *(copy.id for copy in copies)]

    stats = {status: 0 for status in ATTENDANCE_LABELS}
    for item in _student_attendance_stats(db, ids).values():
        for status in ATTENDANCE_LABELS:
            stats[status] += item[status]
    stats["total"] = sum(stats[status] for status in ATTENDANCE_LABELS)
    attended = stats["present"] + stats["late"]
    stats["rate"] = round(attended / stats["total"] * 100) if stats["total"] else None
    history = db.execute(
        select(Attendance.taken_at, Attendance.subject, AttendanceMark.status)
        .join(AttendanceMark, AttendanceMark.attendance_id == Attendance.id)
        .where(AttendanceMark.student_id.in_(ids), Attendance.deleted_at.is_(None))
        .order_by(Attendance.taken_at.desc())
        .limit(60)
    ).all()

    results = []
    for record in [student, *copies]:
        results += _student_results(db, record)
    results.sort(key=lambda item: item["date"], reverse=True)

    # Reyting: faqat o'z o'rni va sinf o'rtachasi (boshqalarning ballari ko'rsatilmaydi)
    classmates = [item.id for item in _active_students(school_class)]
    rating, test_count = _class_rating(db, school_class, SimpleNamespace(), classmates)
    mine = rating.get(student.id)
    averages = [item["avg"] for item in rating.values()]

    group_classes = {copy.class_id: db.get(SchoolClass, copy.class_id) for copy in copies}
    slots = [_slot_view(db, slot, school_class.name) for slot in _active_slots(db, class_id=school_class.id)]
    for class_id, group in group_classes.items():
        if group and group.deleted_at is None:
            slots += [_slot_view(db, slot, group.name) for slot in _active_slots(db, class_id=class_id)]
    slots.sort(key=lambda slot: (slot["weekday"], slot["start_time"]))

    return {
        "name": student.full_name,
        "code": student.student_code,
        "login": pupil.account.login,
        "must_change": pupil.account.must_change,
        "class_name": school_class.name,
        "school_name": school.name if school else "",
        "curator": curator.full_name if curator else None,
        # Profil — xodimlar ko'radigan o'quvchi sahifasidagidek (faqat o'qish uchun): ma'lumotlar, holat,
        # yotoqxona va to'lov (ota-ona qarzni ko'radi)
        "birth_date": student.birth_date,
        "region": student.region,
        "district": student.district,
        "mahalla": student.mahalla,
        "address": student.address,
        "phone": student.phone,
        "grade": student.grade,
        "status": _status_view(db, _current_statuses(db, [student.id]).get(student.id)),
        "dorm": _student_dorm_view(db, student),
        "payments": _student_payment_summary(db, school_class, student) if not school_class.subject else None,
        "attendance": {
            "stats": stats,
            "history": [{"taken_at": taken_at, "subject": subject, "status": status} for taken_at, subject, status in history],
        },
        "results": results[:30],
        "rating": {
            "rank": mine["rank"] if mine else None,
            "avg": mine["avg"] if mine else None,
            "tests": mine["tests"] if mine else 0,
            "ranked": len(rating),
            "class_size": len(classmates),
            "class_avg": round(sum(averages) / len(averages), 1) if averages else None,
            "test_count": test_count,
        },
        "schedule": slots,
    }


# ---------- Yotoqxona: xonalar, joylashtirish, kechki yo'qlama ----------
# Maktab va o'quv markazida (to'garakda yo'q). Rahbariyat (admin, direktor) va komendant — hamma xona: ochadi,
# o'quvchilarni joylashtiradi, tarbiyachi tayinlaydi. Tarbiyachi — faqat o'z xonalari: ko'radi va yo'qlama oladi.

DORM_STATUSES = {"present": "Bor", "absent": "Yo'q", "sick": "Kasal", "excused": "Ruxsat bilan"}


def _dorm_member(db: Session, user: User) -> SchoolMember:
    member = _require_school_member(db, user)
    if (member.school.kind or "school") == "club":
        raise HTTPException(status_code=404, detail="Yotoqxona bo'limi to'garaklarda yo'q")
    return member


def _dorm_rooms_of(db: Session, member: SchoolMember) -> list[DormRoom]:
    """Ko'rinadigan xonalar: rahbariyatga — hammasi, tarbiyachiga — o'ziniki."""
    query = select(DormRoom).where(DormRoom.school_id == member.school_id, DormRoom.deleted_at.is_(None))
    if not _can_manage_dorm(member):
        query = query.where(DormRoom.warden_user_id == member.user_id)
    return db.scalars(query.order_by(DormRoom.floor, DormRoom.name)).all()


def _dorm_room(db: Session, member: SchoolMember, room_id: int, manage: bool = False) -> DormRoom:
    room = db.get(DormRoom, room_id)
    if not room or room.deleted_at is not None or room.school_id != member.school_id:
        raise HTTPException(status_code=404, detail="Xona topilmadi")
    leader = _can_manage_dorm(member)
    if manage and not leader:
        raise HTTPException(status_code=403, detail="Xonalarni admin, direktor yoki komendant boshqaradi")
    if not leader and room.warden_user_id != member.user_id:
        raise HTTPException(status_code=403, detail="Bu xona sizga biriktirilmagan")
    return room


def _dorm_residents(db: Session, room_ids: list[int]) -> dict[int, list[tuple[DormResident, ClassStudent, SchoolClass]]]:
    """Xonalardagi hozirgi o'quvchilar (sinfdan chiqarilgan yoki sinfi o'chirilganlar — yo'q)."""
    result: dict[int, list] = {room_id: [] for room_id in room_ids}
    if not room_ids:
        return result
    rows = db.execute(
        select(DormResident, ClassStudent, SchoolClass)
        .join(ClassStudent, ClassStudent.id == DormResident.student_id)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(DormResident.room_id.in_(room_ids), DormResident.left_at.is_(None),
               ClassStudent.removed_at.is_(None), SchoolClass.deleted_at.is_(None))
        .order_by(ClassStudent.full_name)
    ).all()
    for resident, student, school_class in rows:
        result[resident.room_id].append((resident, student, school_class))
    return result


def _dorm_today() -> date:
    return (utcnow() + TASHKENT_OFFSET).date()


def _dorm_check_counts(check: DormCheck | None) -> dict | None:
    if not check:
        return None
    counts = {status: 0 for status in DORM_STATUSES}
    for mark in check.marks:
        counts[mark.status] = counts.get(mark.status, 0) + 1
    return counts


def _dorm_room_view(room: DormRoom, residents: list, warden: User | None, today_check: DormCheck | None) -> dict:
    return {
        "id": room.id,
        "name": room.name,
        "building_id": room.building_id,
        "floor": room.floor,
        "capacity": room.capacity,
        "occupied": len(residents),
        "free": max(room.capacity - len(residents), 0),
        "warden": {"user_id": warden.id, "name": warden.full_name} if warden else None,
        "residents": [
            {"id": student.id, "name": student.full_name, "class_id": school_class.id, "class_name": school_class.name}
            for _, student, school_class in residents
        ],
        "today": _dorm_check_counts(today_check),
    }


@app.get("/api/dorm")
def dorm_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Yotoqxona: xonalar (qavat bo'yicha), bandlik va bugungi kechki yo'qlama holati."""

    member = _dorm_member(db, user)
    leader = _can_manage_dorm(member)
    rooms = _dorm_rooms_of(db, member)
    if not leader and not rooms:
        raise HTTPException(status_code=403, detail="Yotoqxona bo'limi rahbariyat va tarbiyachilar uchun")
    residents = _dorm_residents(db, [room.id for room in rooms])
    wardens = {
        item.id: item
        for item in db.scalars(select(User).where(User.id.in_({room.warden_user_id for room in rooms if room.warden_user_id})))
    } if rooms else {}
    today = _dorm_today()
    checks = {
        check.room_id: check
        for check in db.scalars(
            select(DormCheck).where(DormCheck.room_id.in_([room.id for room in rooms] or [-1]), DormCheck.check_date == today)
        )
    }
    views = [_dorm_room_view(room, residents[room.id], wardens.get(room.warden_user_id), checks.get(room.id)) for room in rooms]
    buildings = _dorm_buildings_of(db, member, rooms)
    building_views = [
        {"id": building.id if building else None, "name": building.name if building else DORM_NO_BUILDING,
         **_dorm_totals([view for view in views if view["building_id"] == (building.id if building else None)])}
        for building in buildings
    ]
    return {
        "can_manage": leader,
        "date": today,
        "buildings": building_views,
        "rooms": views,
        "totals": _dorm_totals(views),
    }


DORM_NO_BUILDING = "Binoga biriktirilmagan"


def _dorm_totals(views: list[dict]) -> dict:
    """Xonalar bo'yicha jami: o'rinlar, bandlik, bugungi yo'qlama (har holat bo'yicha o'quvchilar soni)."""
    status_total = lambda status: sum((view["today"] or {}).get(status, 0) for view in views)  # noqa: E731
    return {
        "rooms": len(views),
        "capacity": sum(view["capacity"] for view in views),
        "occupied": sum(view["occupied"] for view in views),
        "free": sum(view["free"] for view in views),
        "checked_rooms": sum(1 for view in views if view["today"] is not None),
        "rooms_with_residents": sum(1 for view in views if view["occupied"]),
        "not_checked_students": sum(view["occupied"] for view in views if view["today"] is None),
        **{status: status_total(status) for status in ("present", "absent", "sick", "excused")},
    }


def _dorm_buildings_of(db: Session, member: SchoolMember, rooms: list[DormRoom]) -> list[DormBuilding | None]:
    """
    Ko'rinadigan binolar: rahbariyatga — hammasi (bo'sh binolar ham), tarbiyachiga — o'z xonalari bor binolar.
    None — binoga biriktirilmagan xonalar (binolar qo'shilishidan oldin ochilganlar) guruhi.
    """
    query = select(DormBuilding).where(DormBuilding.school_id == member.school_id, DormBuilding.deleted_at.is_(None))
    if not _can_manage_dorm(member):
        query = query.where(DormBuilding.id.in_({room.building_id for room in rooms if room.building_id} or {-1}))
    result: list[DormBuilding | None] = list(db.scalars(query.order_by(DormBuilding.name)).all())
    if any(room.building_id is None for room in rooms):
        result.append(None)
    return result


def _dorm_building(db: Session, member: SchoolMember, building_id: int | None) -> int | None:
    if building_id is None:
        return None
    building = db.get(DormBuilding, building_id)
    if not building or building.deleted_at is not None or building.school_id != member.school_id:
        raise HTTPException(status_code=404, detail="Bino topilmadi")
    return building.id


@app.post("/api/dorm/buildings")
def create_dorm_building(body: DormBuildingIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="Binolarni admin, direktor yoki komendant qo'shadi")
    same = db.scalar(select(DormBuilding.id).where(DormBuilding.school_id == member.school_id, DormBuilding.deleted_at.is_(None),
                                                   func.lower(DormBuilding.name) == body.name.lower()))
    if same:
        raise HTTPException(status_code=409, detail=f"«{body.name}» binosi allaqachon bor")
    first = not db.scalar(select(DormBuilding.id).where(DormBuilding.school_id == member.school_id,
                                                        DormBuilding.deleted_at.is_(None)))
    building = DormBuilding(school_id=member.school_id, name=body.name)
    db.add(building)
    db.flush()
    moved = 0
    if first:
        # Birinchi bino: binolar qo'shilishidan oldin ochilgan xonalar shu binoga o'tadi
        # ("Binoga biriktirilmagan" guruhi qolmaydi; keyingi binolarni rahbariyat o'zi to'ldiradi)
        for room in db.scalars(select(DormRoom).where(DormRoom.school_id == member.school_id, DormRoom.deleted_at.is_(None),
                                                      DormRoom.building_id.is_(None))):
            room.building_id = building.id
            moved += 1
    db.commit()
    return {"id": building.id, "name": building.name, "moved_rooms": moved}


@app.put("/api/dorm/buildings/{building_id}")
def rename_dorm_building(building_id: int, body: DormBuildingIn, user: User = Depends(get_current_user),
                        db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="Binolarni admin, direktor yoki komendant boshqaradi")
    building = db.get(DormBuilding, _dorm_building(db, member, building_id))
    same = db.scalar(select(DormBuilding.id).where(DormBuilding.school_id == member.school_id, DormBuilding.deleted_at.is_(None),
                                                   DormBuilding.id != building.id, func.lower(DormBuilding.name) == body.name.lower()))
    if same:
        raise HTTPException(status_code=409, detail=f"«{body.name}» binosi allaqachon bor")
    building.name = body.name
    db.commit()
    return {"id": building.id, "name": building.name}


@app.delete("/api/dorm/buildings/{building_id}")
def delete_dorm_building(building_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="Binolarni admin, direktor yoki komendant boshqaradi")
    building = db.get(DormBuilding, _dorm_building(db, member, building_id))
    if db.scalar(select(DormRoom.id).where(DormRoom.building_id == building.id, DormRoom.deleted_at.is_(None))):
        raise HTTPException(status_code=409, detail="Binoda xonalar bor — avval ularni o'chiring yoki boshqa binoga o'tkazing")
    building.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


def _dorm_warden(db: Session, member: SchoolMember, warden_user_id: int | None) -> int | None:
    if warden_user_id is None:
        return None
    staff = db.scalar(select(SchoolMember.id).where(SchoolMember.school_id == member.school_id,
                                                     SchoolMember.user_id == warden_user_id, SchoolMember.left_at.is_(None)))
    if not staff:
        raise HTTPException(status_code=404, detail="Bu xodim muassasangizda yo'q")
    return warden_user_id


def _dorm_same_room(db: Session, member: SchoolMember, name: str, building_id: int | None, exclude: int | None = None):
    """Shu binoda shu nomli xona bormi (har binoda o'z 201-xonasi bo'lishi mumkin)."""
    query = select(DormRoom.id).where(DormRoom.school_id == member.school_id, DormRoom.deleted_at.is_(None),
                                      func.lower(DormRoom.name) == name.lower(),
                                      DormRoom.building_id.is_(None) if building_id is None else DormRoom.building_id == building_id)
    if exclude:
        query = query.where(DormRoom.id != exclude)
    return db.scalar(query)


@app.post("/api/dorm/rooms")
def create_dorm_room(body: DormRoomIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="Xonalarni admin, direktor yoki komendant ochadi")
    building_id = _dorm_building(db, member, body.building_id)
    if _dorm_same_room(db, member, body.name, building_id):
        raise HTTPException(status_code=409, detail=f"«{body.name}» xonasi bu binoda allaqachon bor")
    room = DormRoom(school_id=member.school_id, name=body.name, floor=body.floor, capacity=body.capacity,
                    warden_user_id=_dorm_warden(db, member, body.warden_user_id), building_id=building_id)
    db.add(room)
    db.commit()
    return {"id": room.id}


@app.post("/api/dorm/rooms/bulk")
def create_dorm_rooms_bulk(body: DormRoomBulkIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Raqamlar oralig'idagi xonalarni birdan ochadi (101–110). Binoda allaqachon
    bor raqamlar o'tkazib yuboriladi. Qavat berilmasa — raqamdan: 101 → 1, 1203 → 12;
    100 dan kichik raqamlarda qavat bo'sh qoladi.
    """

    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="Xonalarni admin, direktor yoki komendant ochadi")
    building_id = _dorm_building(db, member, body.building_id)
    warden = _dorm_warden(db, member, body.warden_user_id)

    created, skipped = [], []
    for number in range(body.start, body.end + 1):
        name = str(number)
        if _dorm_same_room(db, member, name, building_id):
            skipped.append(name)
            continue
        floor = body.floor if body.floor is not None else (number // 100 if number >= 100 else None)
        if floor is not None and floor > 100:
            floor = None
        db.add(DormRoom(school_id=member.school_id, name=name, floor=floor, capacity=body.capacity,
                        warden_user_id=warden, building_id=building_id))
        created.append(name)
    db.commit()
    return {"created": created, "skipped": skipped}


@app.put("/api/dorm/rooms/{room_id}")
def update_dorm_room(room_id: int, body: DormRoomIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id, manage=True)
    occupied = len(_dorm_residents(db, [room.id])[room.id])
    if body.capacity < occupied:
        raise HTTPException(status_code=422, detail=f"Xonada {occupied} ta o'quvchi bor — o'rinlar soni undan kam bo'lmasin")
    building_id = _dorm_building(db, member, body.building_id)
    if _dorm_same_room(db, member, body.name, building_id, exclude=room.id):
        raise HTTPException(status_code=409, detail=f"«{body.name}» xonasi bu binoda allaqachon bor")
    room.name, room.floor, room.capacity, room.building_id = body.name, body.floor, body.capacity, building_id
    room.warden_user_id = _dorm_warden(db, member, body.warden_user_id)
    db.commit()
    return {"ok": True}


@app.delete("/api/dorm/rooms/{room_id}")
def delete_dorm_room(room_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id, manage=True)
    if _dorm_residents(db, [room.id])[room.id]:
        raise HTTPException(status_code=409, detail="Xonada o'quvchilar bor — avval ularni chiqaring")
    room.deleted_at = utcnow()
    db.commit()
    return {"ok": True}


@app.get("/api/dorm/rooms/{room_id}")
def dorm_room_detail(room_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Xona: o'quvchilar va oxirgi yo'qlamalar (14 kun)."""

    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id)
    residents = _dorm_residents(db, [room.id])[room.id]
    checks = db.scalars(
        select(DormCheck).where(DormCheck.room_id == room.id).order_by(DormCheck.check_date.desc()).limit(14)
    ).all()
    today = _dorm_today()
    view = _dorm_room_view(room, residents, db.get(User, room.warden_user_id) if room.warden_user_id else None,
                           next((check for check in checks if check.check_date == today), None))
    takers = {item.id: item.full_name for item in db.scalars(select(User).where(User.id.in_({c.taken_by for c in checks})))} \
        if checks else {}
    building = db.get(DormBuilding, room.building_id) if room.building_id else None
    return {
        **view,
        "building_name": building.name if building else DORM_NO_BUILDING,
        "can_manage": _can_manage_dorm(member),
        "date": today,
        "checks": [
            {"date": check.check_date, "counts": _dorm_check_counts(check), "taken_by": takers.get(check.taken_by, ""),
             "absent": [mark.student_name for mark in check.marks if mark.status == "absent"]}
            for check in checks
        ],
    }


@app.get("/api/dorm/candidates")
def dorm_candidates(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Joylashtirish uchun: hali yotoqxonada joyi yo'q o'quvchilar (sinfi bilan)."""

    member = _dorm_member(db, user)
    if not _can_manage_dorm(member):
        raise HTTPException(status_code=403, detail="O'quvchilarni admin, direktor yoki komendant joylashtiradi")
    placed = select(DormResident.student_id).where(DormResident.school_id == member.school_id, DormResident.left_at.is_(None))
    rows = db.execute(
        select(ClassStudent, SchoolClass)
        .join(SchoolClass, SchoolClass.id == ClassStudent.class_id)
        .where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None),
               ClassStudent.removed_at.is_(None), ClassStudent.id.not_in(placed), ClassStudent.source_student_id.is_(None))
    ).all()
    rows = sorted(rows, key=lambda row: (_class_name_key(row[1].name), row[0].full_name.lower()))
    return [{"id": student.id, "name": student.full_name, "class_name": school_class.name} for student, school_class in rows]


@app.post("/api/dorm/rooms/{room_id}/residents")
def place_dorm_residents(room_id: int, body: DormPlaceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchilarni xonaga joylashtirish. Boshqa xonada bo'lsa — shu xonaga ko'chiriladi."""

    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id, manage=True)
    current = {student.id for _, student, _ in _dorm_residents(db, [room.id])[room.id]}
    new_ids = [student_id for student_id in dict.fromkeys(body.student_ids) if student_id not in current]
    if len(current) + len(new_ids) > room.capacity:
        raise HTTPException(status_code=422, detail=f"«{room.name}» xonasida {room.capacity - len(current)} ta bo'sh o'rin bor")
    for student_id in new_ids:
        student = db.get(ClassStudent, student_id)
        school_class = db.get(SchoolClass, student.class_id) if student else None
        if not student or student.removed_at is not None or not school_class or school_class.school_id != member.school_id \
                or school_class.deleted_at is not None:
            raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
        for old in db.scalars(select(DormResident).where(DormResident.student_id == student.id, DormResident.left_at.is_(None))):
            old.left_at = utcnow()  # boshqa xonadan ko'chirildi
        db.add(DormResident(school_id=member.school_id, room_id=room.id, student_id=student.id))
    db.commit()
    return {"placed": len(new_ids)}


@app.delete("/api/dorm/rooms/{room_id}/residents/{student_id}")
def remove_dorm_resident(room_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id, manage=True)
    resident = db.scalar(select(DormResident).where(DormResident.room_id == room.id, DormResident.student_id == student_id,
                                                    DormResident.left_at.is_(None)))
    if not resident:
        raise HTTPException(status_code=404, detail="Bu o'quvchi xonada yo'q")
    resident.left_at = utcnow()
    db.commit()
    return {"ok": True}


@app.get("/api/dorm/rooms/{room_id}/check")
def dorm_check(room_id: int, check_date: date | None = None, user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Shu kungi yo'qlama (olinmagan bo'lsa — hamma "Bor" bilan tayyor ro'yxat)."""

    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id)
    day = check_date or _dorm_today()
    check = db.scalar(select(DormCheck).where(DormCheck.room_id == room.id, DormCheck.check_date == day))
    marks = {mark.student_id: mark.status for mark in check.marks} if check else {}
    residents = _dorm_residents(db, [room.id])[room.id]
    # Yo'qlama olinmagan o'quvchi uchun — holatidan (kasal → "Kasal", uyda/ruxsatda → "Ruxsat bilan")
    away = _active_status_codes(db, [student.id for _, student, _ in residents]) if day == _dorm_today() else {}
    rows = [{"id": student.id, "name": student.full_name, "class_name": school_class.name,
             "status": marks.get(student.id, STATUS_TO_DORM.get(away.get(student.id), "present")),
             "away": away.get(student.id)} for _, student, school_class in residents]
    if check:  # yo'qlamadan keyin xonadan chiqqanlar ham tarixda ko'rinadi
        known = {row["id"] for row in rows}
        rows += [{"id": mark.student_id, "name": mark.student_name, "class_name": "", "status": mark.status}
                 for mark in check.marks if mark.student_id not in known]
    return {"room": {"id": room.id, "name": room.name}, "date": day, "taken": check is not None,
            "taken_at": check.taken_at if check else None, "rows": rows}


@app.put("/api/dorm/rooms/{room_id}/check")
def save_dorm_check(room_id: int, body: DormCheckIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kechki yo'qlamani saqlash (tarbiyachi yoki rahbariyat). Shu kun uchun qayta saqlansa — yangilanadi."""

    member = _dorm_member(db, user)
    room = _dorm_room(db, member, room_id)
    day = body.check_date or _dorm_today()
    if day > _dorm_today():
        raise HTTPException(status_code=422, detail="Kelajakdagi kun uchun yo'qlama olinmaydi")
    residents = {student.id: student for _, student, _ in _dorm_residents(db, [room.id])[room.id]}
    if not residents:
        raise HTTPException(status_code=400, detail="Xonada o'quvchi yo'q")
    check = db.scalar(select(DormCheck).where(DormCheck.room_id == room.id, DormCheck.check_date == day))
    if check:
        check.marks.clear()
        check.updated_at = utcnow()
        check.taken_by = user.id
    else:
        check = DormCheck(school_id=member.school_id, room_id=room.id, check_date=day, taken_by=user.id)
        db.add(check)
    for student_id, student in residents.items():
        check.marks.append(DormCheckMark(student_id=student_id, student_name=student.full_name,
                                         status=body.marks.get(student_id, "present")))
    db.commit()
    return {"date": day, "counts": _dorm_check_counts(check)}


# ---------- Yotoqxona hisoboti: matn, rasm, PDF ----------

DORM_REPORT_LABELS = {"present": ("Bor", "✅"), "absent": ("Yo'q", "❌"), "sick": ("Kasal", "🤒"),
                      "excused": ("Ruxsat bilan", "🟡")}
DORM_REPORT_COLORS = {"present": "#1e7b34", "absent": "#c00000", "sick": "#8e44ad", "excused": "#b8860b"}


def _dorm_room_title(room: DormRoom) -> str:
    """"201" → "201-xona"; "Qizlar 12" o'zgarmaydi (ilovadagi bilan bir xil)."""
    return f"{room.name}-xona" if re.fullmatch(r"\d+[a-zA-Z]?", room.name) else room.name


def _dorm_report_context(db: Session, user: User, check_date: date | None) -> dict:
    """Bir kunlik yotoqxona holati: umumiy ko'rsatkichlar, xonalar va yotoqxonada bo'lmaganlar."""

    member = _dorm_member(db, user)
    rooms = _dorm_rooms_of(db, member)
    if not _can_delete_classes(member) and not rooms:
        raise HTTPException(status_code=403, detail="Yotoqxona bo'limi rahbariyat va tarbiyachilar uchun")
    day = check_date or _dorm_today()
    residents = _dorm_residents(db, [room.id for room in rooms])
    checks = {
        check.room_id: check
        for check in db.scalars(
            select(DormCheck).where(DormCheck.room_id.in_([room.id for room in rooms] or [-1]), DormCheck.check_date == day)
        )
    }
    wardens = {item.id: item.full_name for item in db.scalars(
        select(User).where(User.id.in_({room.warden_user_id for room in rooms if room.warden_user_id} or {-1})))}
    building_names = {item.id: item.name for item in db.scalars(
        select(DormBuilding).where(DormBuilding.id.in_({room.building_id for room in rooms if room.building_id} or {-1})))}
    class_of = {student.id: school_class.name for room_id in residents for _, student, school_class in residents[room_id]}
    room_rows, missing = [], {status: [] for status in ("absent", "sick", "excused")}
    for room in rooms:
        check = checks.get(room.id)
        counts = _dorm_check_counts(check)
        title = _dorm_room_title(room)
        building = building_names.get(room.building_id, DORM_NO_BUILDING) if building_names or room.building_id else ""
        label = f"{building}, {title}" if building else title  # ro'yxatlarda: qaysi binodagi xona
        room_rows.append({"title": title, "label": label, "floor": room.floor, "warden": wardens.get(room.warden_user_id, ""),
                          "building": building, "occupied": len(residents[room.id]), "capacity": room.capacity,
                          "free": max(room.capacity - len(residents[room.id]), 0), "counts": counts})
        for mark in (check.marks if check else []):
            if mark.status in missing:
                missing[mark.status].append({"name": mark.student_name, "room": label, "class_name": class_of.get(mark.student_id, "")})
    # Binolar tartibida (nomi bo'yicha), bino ichida — qavat va xona nomi bo'yicha
    room_rows.sort(key=lambda row: (row["building"] == DORM_NO_BUILDING, row["building"], row["floor"] is None,
                                    row["floor"] or 0, row["title"]))
    totals = {status: sum((row["counts"] or {}).get(status, 0) for row in room_rows) for status in DORM_REPORT_LABELS}
    return {
        "school_name": member.school.name,
        "date": day.strftime("%d.%m.%Y"),
        "rooms": room_rows,
        "missing": missing,
        "totals": totals,
        "occupied": sum(row["occupied"] for row in room_rows),
        "capacity": sum(row["capacity"] for row in room_rows),
        "checked": sum(1 for row in room_rows if row["counts"] is not None),
        "free": sum(row["free"] for row in room_rows),
        "not_checked_students": sum(row["occupied"] for row in room_rows if row["counts"] is None),
        "has_buildings": any(row["building"] for row in room_rows),
        "with_residents": sum(1 for row in room_rows if row["occupied"]),
        "not_checked": [row["label"] for row in room_rows if row["occupied"] and row["counts"] is None],
    }


def dorm_report_caption(context: dict) -> str:
    totals = context["totals"]
    return "\n".join([
        f"🏠 Yotoqxona — {context['date']}",
        f"🏫 {context['school_name']}",
        f"🛏 Band: {context['occupied']} / {context['capacity']} o'rin · Yo'qlama: {context['checked']} / {context['with_residents']} xona",
        " · ".join(f"{icon} {label}: {totals.get(status, 0)}" for status, (label, icon) in DORM_REPORT_LABELS.items()),
    ])


def dorm_report_stats(context: dict) -> list[tuple[str, str, int]]:
    """Alohida statistika: (belgi, nomi, soni) — jami o'quvchi, bor, yo'q, kasal, ruxsat bilan, yo'qlama olinmagan."""
    totals = context["totals"]
    rows = [("👥", "Jami o'quvchi", context["occupied"])]
    rows += [(icon, label, totals.get(status, 0)) for status, (label, icon) in DORM_REPORT_LABELS.items()]
    if context["not_checked_students"]:
        rows.append(("⏳", "Yo'qlama olinmagan", context["not_checked_students"]))
    rows.append(("🛏", "Bo'sh o'rin", context["free"]))
    return rows


def dorm_free_places(context: dict) -> list[tuple[str, list[dict]]]:
    """Bo'sh o'rinli xonalar binolar bo'yicha: [(bino, [xona, ...]), ...]."""
    groups: dict[str, list[dict]] = {}
    for row in context["rooms"]:
        if row["free"]:
            groups.setdefault(row["building"], []).append(row)
    return list(groups.items())


def dorm_report_text(context: dict) -> str:
    """Matn — statistika, yotoqxonada BO'LMAGANLAR (yo'q, kasal, ruxsat bilan), bo'sh o'rinlar va yo'qlama olinmaganlar."""

    lines = [f"🏠 Yotoqxona — {context['date']}", f"🏫 {context['school_name']}", "", "📊 Statistika:"]
    lines += [f"{icon} {label}: {count}" for icon, label, count in dorm_report_stats(context)]
    for status in ("absent", "sick", "excused"):
        people = context["missing"][status]
        if not people:
            continue
        label, icon = DORM_REPORT_LABELS[status]
        lines += ["", f"{icon} {label}:"]
        for index, item in enumerate(people, start=1):
            class_note = f" ({item['class_name']})" if item["class_name"] else ""
            lines.append(f"{index}. {item['name']} — {item['room']}{class_note}")
    if not any(context["missing"].values()) and context["checked"]:
        lines += ["", "✅ Yo'qlama olingan xonalarda hamma joyida."]
    if context["not_checked"]:
        lines += ["", f"⏳ Yo'qlama olinmagan xonalar: {', '.join(context['not_checked'])}"]
    free = dorm_free_places(context)
    if free:
        lines += ["", f"🛏 Bo'sh o'rinlar ({context['free']} ta):"]
        for building, rooms in free:
            room_list = ", ".join(f"{row['title']} — {row['free']} ta" for row in rooms)
            lines.append(f"{building}: {room_list}" if building else room_list)
    return "\n".join(lines)


def dorm_report_image(context: dict) -> bytes:
    """Rasm (PNG): alohida statistika, xonalar jadvali (binolar bo'yicha, bo'sh o'rinlar bilan) va bo'lmaganlar."""

    from PIL import Image, ImageDraw, ImageFont

    def font(name: str, size: int):
        try:
            return ImageFont.truetype(str(FONTS_DIR / name), size)
        except OSError:
            return ImageFont.load_default()

    title_font, text_font, bold_font = font("DejaVuSerif-Bold.ttf", 46), font("DejaVuSerif.ttf", 28), font("DejaVuSerif-Bold.ttf", 28)
    big_font, small_font = font("DejaVuSans-Bold.ttf", 44), font("DejaVuSans.ttf", 22)
    width, row_height = 1080, 58
    stats = dorm_report_stats(context)
    stat_rows = math.ceil(len(stats) / 4)
    buildings = [row["building"] for row in context["rooms"]]
    building_rows = len({name for name in buildings if name}) if context["has_buildings"] else 0
    people = [(status, item) for status in ("absent", "sick", "excused") for item in context["missing"][status]]
    height = (190 + stat_rows * 130 + 30 + row_height * (len(context["rooms"]) + building_rows + 1)
              + (90 + row_height * (len(people) + 1) if people else 0) + 60)
    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.text((50, 40), f"Yotoqxona — {context['date']}", font=title_font, fill="#0B1F2A")
    draw.text((50, 108), context["school_name"], font=text_font, fill="#4F6670")

    # Alohida statistika: plitkalar (soni katta, nomi ostida)
    stat_colors = {"Jami o'quvchi": "#0B1F2A", "Yo'qlama olinmagan": "#6b7280", "Bo'sh o'rin": "#2e75b6",
                   **{label: DORM_REPORT_COLORS[status] for status, (label, _) in DORM_REPORT_LABELS.items()}}
    tile_width, gap, y = (width - 80 - 3 * 16) / 4, 16, 170
    for index, (_, label, count) in enumerate(stats):
        x = 40 + (index % 4) * (tile_width + gap)
        top = y + (index // 4) * 130
        draw.rounded_rectangle([x, top, x + tile_width, top + 112], radius=18, fill="#F3FAF5")
        draw.text((x + tile_width / 2, top + 40), str(count), font=big_font, fill=stat_colors.get(label, "#0B1F2A"), anchor="mm")
        draw.text((x + tile_width / 2, top + 86), label, font=small_font, fill="#4F6670", anchor="mm")

    # Xonalar jadvali: bino sarlavhasi, xona | band | bo'sh | bor | yo'q | kasal | ruxsat
    columns = [(60, "Xona"), (330, "Band"), (450, "Bo'sh"), (570, "Bor"), (680, "Yo'q"), (790, "Kasal"), (910, "Ruxsat")]
    y = 170 + stat_rows * 130 + 30
    draw.rectangle([40, y, width - 40, y + row_height], fill="#00A844")
    for column_x, title in columns:
        draw.text((column_x, y + 13), title, font=bold_font, fill="#ffffff")
    current_building = None
    for index, row in enumerate(context["rooms"], start=1):
        if context["has_buildings"] and row["building"] != current_building:
            current_building = row["building"]
            y += row_height
            draw.rectangle([40, y, width - 40, y + row_height], fill="#DDF3E4")
            draw.text((60, y + 13), current_building, font=bold_font, fill="#0B5E2B")
        y += row_height
        if index % 2 == 0:
            draw.rectangle([40, y, width - 40, y + row_height], fill="#F7FBF8")
        draw.text((60, y + 13), row["title"][:16], font=text_font, fill="#111827")
        draw.text((330, y + 13), f"{row['occupied']}/{row['capacity']}", font=text_font, fill="#111827")
        draw.text((450, y + 13), str(row["free"]), font=bold_font, fill="#2e75b6" if row["free"] else "#9ca3af")
        counts = row["counts"]
        if counts is None:
            draw.text((570, y + 13), "yo'qlama olinmagan" if row["occupied"] else "bo'sh xona", font=text_font, fill="#9ca3af")
            continue
        for (column_x, _), status in zip(columns[3:], ("present", "absent", "sick", "excused")):
            draw.text((column_x, y + 13), str(counts.get(status, 0)), font=bold_font, fill=DORM_REPORT_COLORS[status])

    if people:
        y += row_height + 40
        draw.text((50, y), "Yotoqxonada bo'lmaganlar", font=bold_font, fill="#0B1F2A")
        y += 50
        draw.rectangle([40, y, width - 40, y + row_height], fill="#00A844")
        for column_x, title in ((60, "F.I.Sh."), (470, "Xona"), (830, "Holat")):
            draw.text((column_x, y + 13), title, font=bold_font, fill="#ffffff")
        for index, (status, item) in enumerate(people, start=1):
            y += row_height
            if index % 2 == 0:
                draw.rectangle([40, y, width - 40, y + row_height], fill="#F3FAF5")
            draw.text((60, y + 13), item["name"][:24], font=text_font, fill="#111827")
            draw.text((470, y + 17), item["room"][:30], font=small_font, fill="#111827")
            draw.text((830, y + 13), DORM_REPORT_LABELS[status][0], font=bold_font, fill=DORM_REPORT_COLORS[status])

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return _brand_png(buffer.getvalue(), context["school_name"], f"Yotoqxona · {context['date']}")


def dorm_report_pdf(context: dict) -> bytes:
    """PDF: alohida statistika, xonalar (binolar bo'yicha, tarbiyachi va bo'sh o'rinlar bilan) va bo'lmaganlar."""

    styles = _report_styles()
    buffer = io.BytesIO()
    document = _brand_document(buffer, f"Yotoqxona — {context['date']}")
    width = document.width

    def head(texts):
        return [Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in texts]

    def table_style(rows: int, spans: list[int] = (), groups: list[int] = ()) -> TableStyle:
        commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD), ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
                    ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
        commands += [("BACKGROUND", (0, row), (-1, row), REPORT_TILE_BG) for row in range(2, rows, 2) if row not in groups]
        commands += [("SPAN", (4, row), (7, row)) for row in spans]  # "yo'qlama olinmagan" — 4 ustun kengligida
        commands += [cmd for row in groups for cmd in (("SPAN", (0, row), (-1, row)),
                                                       ("BACKGROUND", (0, row), (-1, row), colors.HexColor("#DDF3E4")))]
        return TableStyle(commands)

    # Alohida statistika — bitta qatorli jadval: nomi tepada, soni ostida
    stats = dorm_report_stats(context)
    stat_table = Table(
        [[Paragraph(f"<font color='#4F6670'>{xml_escape(label)}</font>", styles["cell"]) for _, label, _ in stats],
         [Paragraph(f"<b>{count}</b>", styles["cell_bold"]) for _, _, count in stats]],
        colWidths=[width / len(stats)] * len(stats))
    stat_table.setStyle(TableStyle([("BACKGROUND", (0, 0), (-1, -1), REPORT_TILE_BG), ("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                    ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                                    ("LINEABOVE", (0, 0), (-1, 0), 1.2, REPORT_TABLE_HEAD)]))

    rooms = [head(("Xona", "Tarbiyachi", "Band", "Bo'sh", "Bor", "Yo'q", "Kasal", "Ruxsat"))]
    spans, groups, current_building = [], [], None
    for row in context["rooms"]:
        if context["has_buildings"] and row["building"] != current_building:
            current_building = row["building"]
            groups.append(len(rooms))
            rooms.append([Paragraph(f"<b>{xml_escape(current_building)}</b>", styles["cell_bold"]), "", "", "", "", "", "", ""])
        counts = row["counts"]
        cells = [Paragraph(xml_escape(row["title"]), styles["cell_bold"]), Paragraph(xml_escape(row["warden"] or "—"), styles["cell"]),
                 Paragraph(f"{row['occupied']}/{row['capacity']}", styles["cell"]),
                 Paragraph(f"<font color='{'#2e75b6' if row['free'] else '#9ca3af'}'><b>{row['free']}</b></font>", styles["cell"])]
        if counts is None:
            note = "yo'qlama olinmagan" if row["occupied"] else "bo'sh"
            cells += [Paragraph(f"<font color='#9ca3af'>{note}</font>", styles["cell"]), "", "", ""]
            spans.append(len(rooms))
        else:
            cells += [Paragraph(f"<font color='{DORM_REPORT_COLORS[status]}'><b>{counts.get(status, 0)}</b></font>", styles["cell"])
                      for status in ("present", "absent", "sick", "excused")]
        rooms.append(cells)
    room_table = Table(rooms, colWidths=[width * share for share in (0.15, 0.29, 0.09, 0.09, 0.09, 0.09, 0.1, 0.1)],
                       repeatRows=1)
    room_table.setStyle(table_style(len(rooms), spans, groups))

    elements = [
        Paragraph(xml_escape(f"Yotoqxona — {context['date']}"), styles["title"]),
        Paragraph(xml_escape(f"{context['school_name']}  ·  Band: {context['occupied']} / {context['capacity']} o'rin  ·  "
                             f"Yo'qlama: {context['checked']} / {context['with_residents']} xona"), styles["meta"]),
        Spacer(1, 10),
        stat_table,
        Spacer(1, 14),
        room_table,
    ]
    people = [(status, item) for status in ("absent", "sick", "excused") for item in context["missing"][status]]
    if people:
        rows = [head(("№", "F.I.Sh.", "Xona", "Sinf", "Holat"))]
        for index, (status, item) in enumerate(people, start=1):
            rows.append([Paragraph(str(index), styles["cell"]), Paragraph(xml_escape(item["name"]), styles["cell"]),
                         Paragraph(xml_escape(item["room"]), styles["cell"]), Paragraph(xml_escape(item["class_name"]), styles["cell"]),
                         Paragraph(f"<font color='{DORM_REPORT_COLORS[status]}'><b>{DORM_REPORT_LABELS[status][0]}</b></font>",
                                   styles["cell"])])
        people_table = Table(rows, colWidths=[width * share for share in (0.07, 0.38, 0.17, 0.18, 0.2)], repeatRows=1)
        people_table.setStyle(table_style(len(rows)))
        elements += [Spacer(1, 16), Paragraph("Yotoqxonada bo'lmaganlar", styles["cell_bold"]), Spacer(1, 6), people_table]
    document.build(elements, canvasmaker=_brand_canvas(context["school_name"], "Yotoqxona hisoboti"))
    return buffer.getvalue()


@app.get("/api/dorm/report/text")
def dorm_report_text_view(check_date: date | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Matn — Mini App uni to'g'ridan-to'g'ri guruhga ulashadi."""
    return {"text": dorm_report_text(_dorm_report_context(db, user, check_date))}


@app.get("/api/dorm/report/file")
def dorm_report_file(format: str = "pdf", check_date: date | None = None, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Yotoqxona hisobotini PDF yoki rasm qilib yuklab beradi (oddiy brauzer uchun)."""
    context = _dorm_report_context(db, user, check_date)
    if format == "image":
        return StreamingResponse(io.BytesIO(dorm_report_image(context)), media_type="image/png",
                                 headers={"Content-Disposition": 'attachment; filename="yotoqxona.png"'})
    return StreamingResponse(io.BytesIO(dorm_report_pdf(context)), media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="yotoqxona.pdf"'})


@app.post("/api/dorm/report/send")
def send_dorm_report(format: str = "pdf", check_date: date | None = None, user: User = Depends(get_current_user),
                     db: Session = Depends(get_db)):
    """Hisobotni bot orqali foydalanuvchining o'z chatiga yuboradi — u yerdan rahbariyat guruhiga uzatiladi."""
    context = _dorm_report_context(db, user, check_date)
    if format == "text":
        if not _send_bot_text(user.tg_id, dorm_report_text(context)):
            raise HTTPException(status_code=502, detail="Xabar yuborilmadi (botga /start bosganmisiz?)")
    elif format == "image":
        _send_photo_to_telegram(user.tg_id, "yotoqxona.png", dorm_report_image(context), dorm_report_caption(context))
    else:
        _send_pdf_to_telegram(user.tg_id, f"yotoqxona_{context['date']}.pdf", dorm_report_pdf(context),
                              dorm_report_caption(context))
    return {"ok": True}


def _attendance_context(db: Session, attendance: Attendance) -> dict:
    school_class = db.get(SchoolClass, attendance.class_id)
    school = db.get(PrivateSchool, school_class.school_id)
    teacher = db.get(User, attendance.teacher_user_id)
    return {
        "class_name": school_class.name,
        "school_name": school.name if school else "",
        "teacher_name": teacher.full_name if teacher else "",
        "subject": attendance.subject or "",
        # O'qituvchi profilidagi fan bo'yicha: "Matematika darsi"
        "lesson": f"{attendance.subject} darsi" if attendance.subject else "Dars",
        "taken_at": _local_time(attendance.taken_at),
        "updated_at": _local_time(attendance.updated_at),
        # "09.10.2026 · 11:00–11:45" — sana, keyin dars vaqti
        "when": _attendance_when(attendance),
        "counts": _attendance_counts(attendance),
        "marks": [(mark.student_name, mark.status) for mark in _sorted_marks(attendance)],
    }


def attendance_caption(context: dict) -> str:
    """Rasm/PDF ostidagi qisqa izoh: guruh, o'qituvchi, sana-soat."""

    lines = [
        f"📋 {context['class_name']} — {context['lesson']}",
        f"👤 {context['teacher_name']}",
        f"🕘 {context['when']}",
    ]
    if context["updated_at"]:
        lines.append(f"✏️ Tahrirlangan: {context['updated_at']}")
    return "\n".join(lines)


def attendance_text(context: dict) -> str:
    """
    Matn ko'rinishi — ota-onalar guruhiga (maktab shabloni): salom, sana va dars vaqti, guruh, fan,
    o'qituvchi, davomat soni va faqat darsda BO'LMAGANLAR (yo'q, sababli, kechikkan).
    To'liq ro'yxat (borlar bilan) rasm va PDF'da bo'ladi.
    """

    counts = context["counts"]
    lines = [
        "Assalomu alaykum, hurmatli ota-onalar.",
        "",
        f"📆 Sana: {context['when']}",
        f"📋 Guruh: {context['class_name']}",
    ]
    if context["subject"]:
        lines.append(f"📚 Dars: {context['subject']}")
    lines += [
        f"👤 {context['teacher_name']}",
        "",
        "Davomat: " + " · ".join(
            f"{ATTENDANCE_LABELS[s][1]} {ATTENDANCE_LABELS[s][0]}: {counts.get(s, 0)}" for s in ATTENDANCE_LABELS
        ),
    ]

    missing_any = False
    for status in ("absent", "excused", "late"):
        names = [name for name, mark_status in context["marks"] if mark_status == status]
        if not names:
            continue
        missing_any = True
        label, icon = ATTENDANCE_LABELS[status]
        lines += ["", f"{icon} {label}:"]
        lines += [f"{index}. {name}" for index, name in enumerate(names, start=1)]

    if not missing_any:
        lines += ["", "✅ Hamma o'quvchi darsda."]
    return "\n".join(lines)


ATTENDANCE_COLORS = {"present": "#1e7b34", "absent": "#c00000", "excused": "#b8860b", "late": "#2e75b6"}


def attendance_pdf(context: dict) -> bytes:
    """Printerga va ota-onalarga — PDF ko'rinishi."""

    styles = _report_styles()
    buffer = io.BytesIO()
    document = _brand_document(buffer, f"Davomat — {context['class_name']}")
    width = document.width

    meta = f"🏫 {context['school_name']}  ·  {context['lesson']}  ·  O'qituvchi: {context['teacher_name']}"
    times = f"Dars: {context['when']}" + (f"  ·  Tahrirlangan: {context['updated_at']}" if context["updated_at"] else "")
    counts = context["counts"]
    summary = "   ".join(f"{ATTENDANCE_LABELS[s][0]}: {counts.get(s, 0)}" for s in ATTENDANCE_LABELS)

    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in ("№", "F.I.Sh.", "Holat")]]
    for index, (name, status) in enumerate(context["marks"], start=1):
        label = ATTENDANCE_LABELS.get(status, ("?", ""))[0]
        color = ATTENDANCE_COLORS.get(status, "#111827")
        data.append([
            Paragraph(str(index), styles["cell"]),
            Paragraph(xml_escape(name), styles["cell"]),
            Paragraph(f"<font color='{color}'><b>{xml_escape(label)}</b></font>", styles["cell"]),
        ])
    table = Table(data, colWidths=[width * 0.08, width * 0.64, width * 0.28], repeatRows=1)
    commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_TABLE_HEAD), ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
    for row in range(2, len(data), 2):
        commands.append(("BACKGROUND", (0, row), (-1, row), REPORT_TILE_BG))
    table.setStyle(TableStyle(commands))

    document.build([
        Paragraph(xml_escape(f"{context['class_name']} — {context['lesson']} davomati"), styles["title"]),
        Paragraph(xml_escape(meta.replace("🏫 ", "")), styles["meta"]),
        Paragraph(xml_escape(times), styles["meta"]),
        Spacer(1, 8),
        Paragraph(xml_escape(summary), styles["cell_bold"]),
        Spacer(1, 10),
        table,
    ], canvasmaker=_brand_canvas(context["school_name"], "Davomat hisoboti"))
    return buffer.getvalue()


def _brand_png(image_bytes: bytes, org_text: str, kind_text: str) -> bytes:
    """
    Ota-onalarga yuboriladigan rasmga Chittak ramkasi: tepada logo, "Chittak", o'ngda maktab va
    hisobot turi, yashil chiziq; pastda shior. Fon rangi ichki rasmnikidek (chok ko'rinmaydi).
    """

    from PIL import Image, ImageDraw, ImageFont

    inner = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    width = inner.width
    scale = width / 1080
    header, footer = round(118 * scale), round(76 * scale)
    background = inner.getpixel((min(4, width - 1), min(inner.height - 1, 40)))
    canvas = Image.new("RGB", (width, header + inner.height + footer), background)
    canvas.paste(inner, (0, header))
    draw = ImageDraw.Draw(canvas)

    def font(name: str, size: float):
        try:
            return ImageFont.truetype(str(FONTS_DIR / name), round(size * scale))
        except OSError:
            return ImageFont.load_default()

    side = round(40 * scale)
    logo_size = round(62 * scale)
    try:
        logo = Image.open(FRONTEND_DIR / "img" / "chittak-icon.png").convert("RGBA").resize((logo_size, logo_size), Image.LANCZOS)
        canvas.paste(logo, (side, round(26 * scale)), logo)
    except OSError:
        pass  # logo bo'lmasa ham rasm chiqaveradi
    draw.text((side + logo_size + round(16 * scale), round(57 * scale)), "Chittak", font=font("DejaVuSans-Bold.ttf", 36),
              fill="#0B1F2A", anchor="lm")
    right = width - side
    org = org_text or ""
    org_font = font("DejaVuSans-Bold.ttf", 22)
    while org and draw.textlength(org, font=org_font) > width * 0.5:
        org = org[:-2].rstrip() + "…"
    draw.text((right, round(44 * scale)), org, font=org_font, fill="#0B1F2A", anchor="rm")
    draw.text((right, round(74 * scale)), kind_text, font=font("DejaVuSans.ttf", 19), fill="#4F6670", anchor="rm")
    line_y = round(104 * scale)
    draw.rectangle([side, line_y, right, line_y + max(2, round(4 * scale))], fill="#00C853")
    draw.text((side, header + inner.height + footer // 2), f"Chittak — {REPORT_SLOGAN}", font=font("DejaVuSans.ttf", 19),
              fill="#4F6670", anchor="lm")

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def attendance_images(context: dict) -> list[bytes]:
    """Davomat rasmi — PDF ko'rinishining o'zi, har sahifasi alohida rasm (PNG)."""
    return pdf_pages_to_png(attendance_pdf(context))


def _send_photo_to_telegram(chat_id: int, file_name: str, image_bytes: bytes, caption: str = "") -> None:
    response = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={"chat_id": chat_id, "caption": caption} if caption else {"chat_id": chat_id},
        files={"photo": (file_name, image_bytes, "image/png")},
        timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="Rasm yuborilmadi (botga /start bosganmisiz?)")


@app.get("/api/attendance/{attendance_id}/file")
def attendance_file(
    attendance_id: int, format: str = "pdf", page: int = 1, user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Davomatni PDF yoki rasm qilib yuklab beradi (oddiy brauzer uchun). Rasm — PDF'ning page-sahifasi."""

    context = _attendance_context(db, _school_attendance(db, user, attendance_id)[0])
    if format == "image":
        images = attendance_images(context)
        if not 1 <= page <= len(images):
            raise HTTPException(status_code=404, detail="Bunday sahifa yo'q")
        return StreamingResponse(io.BytesIO(images[page - 1]), media_type="image/png", headers={
            "Content-Disposition": f'attachment; filename="davomat_{page}.png"',
            "X-Page-Count": str(len(images)), "Access-Control-Expose-Headers": "X-Page-Count",
        })
    return StreamingResponse(io.BytesIO(attendance_pdf(context)), media_type="application/pdf",
                             headers={"Content-Disposition": 'attachment; filename="davomat.pdf"'})


@app.post("/api/attendance/{attendance_id}/send")
def send_attendance(
    attendance_id: int, format: str = "pdf", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Davomatni bot orqali o'qituvchining o'z chatiga yuboradi — u yerdan
    ota-onalar guruhiga "Uzatish" (forward) qilinadi yoki printerga chiqariladi.
    """

    context = _attendance_context(db, _school_attendance(db, user, attendance_id)[0])
    caption = attendance_caption(context)

    if format == "text":
        if not _send_bot_text(user.tg_id, attendance_text(context)):
            raise HTTPException(status_code=502, detail="Xabar yuborilmadi (botga /start bosganmisiz?)")
    elif format == "image":
        # PDF bilan bir xil; bir necha sahifa bo'lsa — albom (har sahifa alohida rasm)
        _send_photo_album(user.tg_id, "davomat", attendance_images(context), caption)
    else:
        _send_pdf_to_telegram(user.tg_id, f"davomat_{context['class_name']}.pdf".replace("/", "-"),
                              attendance_pdf(context), caption)
    return {"ok": True}


@app.get("/api/attendance/{attendance_id}/text")
def attendance_text_view(attendance_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Matn ko'rinishi — Mini App uni to'g'ridan-to'g'ri guruhga ulashadi."""
    return {"text": attendance_text(_attendance_context(db, _school_attendance(db, user, attendance_id)[0]))}


# ========== 6.9) To'lov tizimlari: umumiy ==========

def _click_enabled() -> bool:
    return bool(CLICK_SERVICE_ID and CLICK_MERCHANT_ID and CLICK_SECRET_KEY)


def _enabled_providers() -> list[str]:
    providers = []
    if PAYME_MERCHANT_ID:
        providers.append("payme")
    if _click_enabled():
        providers.append("click")
    return providers


def _require_provider(provider: str) -> None:
    if provider not in _enabled_providers():
        raise HTTPException(status_code=503, detail="Bu to'lov usuli hali sozlanmagan, admin bilan bog'laning")


def _order_provider(order) -> str:
    """Buyurtma qaysi to'lov yo'li uchun yaratilgan: "payme", "click" yoki "card" (chek bilan)."""
    return order.method if isinstance(order, CreditPurchase) else order.provider


def _test_already_paid(db: Session, order) -> bool:
    """
    Shu o'quvchi shu testga BOSHQA buyurtma bilan allaqachon to'laganmi.

    O'quvchi Payme sahifasini ochib, keyin Click'ni tanlashi (yoki aksincha) mumkin — har biri
    o'z buyurtmasi bilan. Ikkinchisi ham oxirigacha bosilsa, pul ikki marta yechilardi;
    shuning uchun to'lov yakunlanishidan oldin shu tekshiriladi va ikkinchisi rad etiladi.
    """
    if isinstance(order, CreditPurchase) and order.kind == "verify":
        # Muassasa o'chgan yoki allaqachon tasdiqlangan — pul yechilmaydi
        school = db.get(PrivateSchool, order.school_id) if order.school_id else None
        return not school or school.deleted_at is not None or school.verified_at is not None
    if isinstance(order, CreditPurchase) and order.kind == "broadcast":
        # Pullik e'lon: test allaqachon e'lon qilingan, yakunlangan yoki o'chgan bo'lsa — pul yechilmaydi
        return _broadcast_blocker(db.get(Test, order.test_id) if order.test_id else None) is not None
    if not isinstance(order, Payment):
        return False  # token/obunani ikki marta sotib olish — ikki alohida xarid, bu normal
    return db.scalar(
        select(Payment.id).where(Payment.user_id == order.user_id, Payment.test_id == order.test_id,
                                 Payment.status == "paid", Payment.id != order.id).limit(1)
    ) is not None


def _click_order_ref(order) -> str:
    """Click'ga yuboriladigan buyurtma raqami: c12 — token xaridi, t34 — test to'lovi."""
    return f"{'c' if isinstance(order, CreditPurchase) else 't'}{order.id}"


def _build_click_checkout_url(order, amount: int) -> str:
    from urllib.parse import urlencode

    query = urlencode({
        "service_id": CLICK_SERVICE_ID,
        "merchant_id": CLICK_MERCHANT_ID,
        "amount": f"{amount:.2f}" if isinstance(amount, float) else str(amount),
        "transaction_param": _click_order_ref(order),
    })
    return f"https://my.click.uz/services/pay?{query}"


def _checkout_url(provider: str, order, amount: int) -> str:
    if provider == "click":
        return _build_click_checkout_url(order, amount)
    # Payme'da bitta account parametri — order_id: "c12" (token/obuna xaridi) yoki "t34" (testga to'lov)
    return _build_payme_checkout_url("order_id", _click_order_ref(order), amount)


# ========== 6.10) Click webhook (SHOP API: Prepare + Complete) ==========
#
# Click to'lovni ikki bosqichda tasdiqlaydi: Prepare (buyurtma bormi, summa
# to'g'rimi) va Complete (pul yechildi). Har so'rov MD5 imzo bilan keladi —
# imzo noto'g'ri bo'lsa, hech narsa o'zgarmaydi. Token/balans faqat Complete
# muvaffaqiyatli bo'lgandagina yoziladi (Payme bilan bir xil mantiq).

CLICK_ERRORS = {
    -1: "SIGN CHECK FAILED",
    -2: "Incorrect parameter amount",
    -3: "Action not found",
    -4: "Already paid",
    -5: "Order does not exist",
    -6: "Transaction does not exist",
    -8: "Error in request from click",
    -9: "Transaction cancelled",
}


def _click_response(form: dict, error: int, **extra) -> JSONResponse:
    body = {
        "click_trans_id": form.get("click_trans_id"),
        "merchant_trans_id": form.get("merchant_trans_id"),
        "error": error,
        "error_note": CLICK_ERRORS.get(error, "Success"),
        **extra,
    }
    return JSONResponse(body)


def _click_sign_ok(form: dict, with_prepare_id: bool) -> bool:
    import hashlib

    parts = [form.get("click_trans_id", ""), form.get("service_id", ""), CLICK_SECRET_KEY, form.get("merchant_trans_id", "")]
    if with_prepare_id:
        parts.append(form.get("merchant_prepare_id", ""))
    parts += [form.get("amount", ""), form.get("action", ""), form.get("sign_time", "")]
    expected = hashlib.md5("".join(str(part) for part in parts).encode()).hexdigest()
    return hmac.compare_digest(expected, str(form.get("sign_string", "")))


def _click_find_order(db: Session, ref: str):
    if not ref or ref[0] not in "ct" or not ref[1:].isdigit():
        return None
    model = CreditPurchase if ref[0] == "c" else Payment
    order = db.get(model, int(ref[1:]), with_for_update=True)  # takroriy so'rov ikki marta yozmasin
    # Click faqat Click uchun yaratilgan buyurtmani to'laydi: Payme yoki karta (chek) buyurtmasini
    # ham to'lay olsa, bitta buyurtma ikki yo'l bilan ikki marta to'lanishi mumkin edi
    if order is not None and _order_provider(order) != "click":
        return None
    return order


def _click_amount_ok(form: dict, order) -> bool:
    try:
        return abs(float(form.get("amount", "0")) - order.amount) < 0.01
    except ValueError:
        return False


@app.post("/api/click/prepare")
async def click_prepare(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    if not _click_enabled():
        return _click_response(form, -8)
    if not _click_sign_ok(form, with_prepare_id=False):
        return _click_response(form, -1)
    if str(form.get("action")) != "0":
        return _click_response(form, -3)
    order = _click_find_order(db, form.get("merchant_trans_id", ""))
    if not order:
        return _click_response(form, -5)
    if order.status == "paid":
        return _click_response(form, -4)
    if order.status == "cancelled":
        return _click_response(form, -9)
    if not _click_amount_ok(form, order):
        return _click_response(form, -2)
    if _test_already_paid(db, order):
        return _click_response(form, -4)
    order.click_trans_id = str(form.get("click_trans_id"))
    db.commit()
    return _click_response(form, 0, merchant_prepare_id=order.id)


@app.post("/api/click/complete")
async def click_complete(request: Request, db: Session = Depends(get_db)):
    form = dict(await request.form())
    if not _click_enabled():
        return _click_response(form, -8)
    if not _click_sign_ok(form, with_prepare_id=True):
        return _click_response(form, -1)
    if str(form.get("action")) != "1":
        return _click_response(form, -3)
    order = _click_find_order(db, form.get("merchant_trans_id", ""))
    if not order or str(order.id) != str(form.get("merchant_prepare_id")):
        return _click_response(form, -6)
    if order.click_trans_id and order.click_trans_id != str(form.get("click_trans_id")):
        return _click_response(form, -6)
    if order.status == "paid":
        return _click_response(form, -4, merchant_confirm_id=order.id)
    if not _click_amount_ok(form, order):
        return _click_response(form, -2)

    # Click tomonda to'lov o'tmagan (error < 0) — buyurtma bekor qilinadi, hech narsa yozilmaydi
    try:
        click_error = int(form.get("error", "0"))
    except ValueError:
        click_error = 0
    if click_error < 0:
        if order.status != "cancelled":
            order.status = "cancelled"
            order.cancelled_at = utcnow()
            db.commit()
        return _click_response(form, -9)
    if order.status == "cancelled":
        return _click_response(form, -9)

    if _test_already_paid(db, order):
        return _click_response(form, -4)  # boshqa yo'l bilan to'lab bo'lingan — Click pulni qaytaradi

    order.status = "paid"
    order.performed_at = utcnow()
    order.click_trans_id = str(form.get("click_trans_id"))
    _payme_apply_paid(order)
    db.commit()
    logging.getLogger("backend").info("Click to'lovi o'tdi: %s, %s so'm", _click_order_ref(order), order.amount)
    return _click_response(form, 0, merchant_confirm_id=order.id)


# ========== 7) Payme webhook (Merchant API) ==========
#
# Payme to'lov qilingan/bekor qilinganda shu bitta manzilga JSON-RPC so'rov
# yuboradi. Hujjat: https://developer.help.paycom.uz/
# Har bir metod alohida funksiyada — shunday o'qish osonroq.


# Payme xato matnini uch tilda kutadi: {"ru": ..., "uz": ..., "en": ...}
PAYME_ERROR_TEXTS = {
    "Buyurtma topilmadi": ("Заказ не найден", "Order not found"),
    "Buyurtma allaqachon ko'rib chiqilgan": ("Заказ уже обработан", "Order already processed"),
    "Summa mos kelmadi": ("Неверная сумма", "Incorrect amount"),
    "Boshqa tranzaksiya allaqachon mavjud": ("Для заказа уже есть другая транзакция", "Another transaction already exists"),
    "Buyurtma allaqachon to'langan": ("Заказ уже оплачен", "Order already paid"),
    "Buyurtma bekor qilingan": ("Заказ отменён", "Order cancelled"),
    "Tranzaksiya topilmadi": ("Транзакция не найдена", "Transaction not found"),
    "Tranzaksiya bekor qilingan": ("Транзакция отменена", "Transaction cancelled"),
    "JSON o'qilmadi": ("Ошибка разбора JSON", "JSON parse error"),
    "So'rov noto'g'ri": ("Неверный запрос", "Invalid request"),
    "Ruxsat yo'q": ("Недостаточно привилегий", "Insufficient privileges"),
    "Metod topilmadi": ("Метод не найден", "Method not found"),
    "Buyurtma boshqa tranzaksiya bilan band": ("Заказ занят другой транзакцией", "Order is busy with another transaction"),
    "Tranzaksiyani bajarib bo'lmaydi": ("Невозможно выполнить операцию", "Unable to perform operation"),
    "Tranzaksiya muddati o'tgan": ("Время транзакции истекло", "Transaction timed out"),
    "Tizim xatosi": ("Системная ошибка", "System error"),
}


def _payme_error(request_id, code: int, message: str) -> JSONResponse:
    ru, en = PAYME_ERROR_TEXTS.get(message, (message, message))
    error = {"code": code, "message": {"ru": ru, "uz": message, "en": en}}
    # -31050…-31099 — buyurtma (account) bilan bog'liq xatolar: qaysi maydon ekanini ham aytamiz
    if -31099 <= code <= -31050:
        error["data"] = "order_id"
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": error})


def _payme_result(request_id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _payme_check_auth(request: Request) -> bool:
    """Payme har bir so'rovda 'Paycom:{secret_key}' ko'rinishida Basic Auth yuboradi."""

    # MUHIM: kalit sozlanmagan bo'lsa, hech kimni o'tkazmaymiz. Aks holda
    # hmac.compare_digest("", "") -> True bo'lib, istalgan odam soxta "to'lov
    # bo'ldi" xabarini yuborib, o'ziga tekinga token yozdirib olardi.
    if not PAYME_SECRET_KEY:
        logging.getLogger("backend").warning("Payme webhook so'rovi keldi, lekin PAYME_SECRET_KEY sozlanmagan")
        return False

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(auth_header[6:]).decode()
        _, _, secret = decoded.partition(":")
        return hmac.compare_digest(secret, PAYME_SECRET_KEY)
    except Exception:
        return False


# Payme tranzaksiyasi 12 soat ichida bajarilmasa — bekor qilinadi (sabab 4)
PAYME_TRANSACTION_TIMEOUT = timedelta(hours=12)


def _payme_state(order) -> int:
    """1 — yaratilgan, 2 — bajarilgan, -1 — bajarilmasdan bekor, -2 — bajarilgandan keyin bekor."""
    if order.status == "paid":
        return 2
    if order.status == "cancelled":
        return -2 if order.performed_at else -1
    return 1


def _payme_ms(moment: datetime | None) -> int:
    """Bazadagi UTC vaqt -> Payme kutadigan millisekund (0 — yo'q)."""
    return int(moment.replace(tzinfo=timezone.utc).timestamp() * 1000) if moment else 0


def _payme_created(order) -> datetime:
    return order.payme_created_at or order.created_at


def _payme_timed_out(order) -> bool:
    return utcnow() - _payme_created(order) > PAYME_TRANSACTION_TIMEOUT


def _payme_cancel(order, reason: int | None) -> None:
    """Tranzaksiyani bekor qiladi (bajarilgan bo'lsa — berilgan token/balans qaytarib olinadi)."""
    if order.status == "paid":
        _payme_revert_paid(order)
    order.status = "cancelled"
    order.cancelled_at = utcnow()
    order.payme_cancel_reason = reason


# Payme ikki xil buyurtmani to'laydi va ikkalasi ham bir xil maydonlarga ega
# (status, amount, payme_transaction_id, created_at, performed_at, cancelled_at),
# shuning uchun quyidagi funksiyalar ikkalasi bilan ham bir xil ishlaydi:
#
#   CreditPurchase — o'qituvchining token xaridi,     account: {"purchase_id": ...}
#   Payment        — o'quvchining pullik testga to'lovi, account: {"payment_id": ...}
PAYME_ORDER_MODELS = {"purchase_id": CreditPurchase, "payment_id": Payment}


def _payme_find_order_by_account(params: dict, db: Session):
    """So'rovdagi "account" bo'yicha buyurtmani topadi (token xaridi yoki testga to'lov)."""

    account = params.get("account") or {}

    # Asosiy: order_id ("c12" — token/obuna xaridi, "t34" — testga to'lov).
    # Eski havolalardagi purchase_id / payment_id ham qabul qilinadi.
    candidates = []
    order_ref = str(account.get("order_id") or "").strip().lower()
    if order_ref:
        model = {"c": CreditPurchase, "t": Payment}.get(order_ref[:1])
        if not model:
            return None
        candidates.append((model, order_ref[1:]))
    candidates += [(model, account.get(field)) for field, model in PAYME_ORDER_MODELS.items()]

    for model, raw_id in candidates:
        if raw_id in (None, ""):
            continue
        try:
            order = db.get(model, int(raw_id), with_for_update=True)
        except (TypeError, ValueError):
            return None

        # Karta orqali qilingan xaridni Payme to'lay olmaydi — u admin
        # tasdig'ini kutadi. Aks holda bitta buyurtma ikki yo'l bilan (chek +
        # Payme) ikki marta token olishi mumkin edi.
        if isinstance(order, CreditPurchase) and order.method != "payme":
            return None
        # Testga to'lovda ham — faqat Payme uchun yaratilgan buyurtma (Click yoki chek bilan
        # to'lanadigan buyurtmani Payme to'lasa, pul ikki marta yechilishi mumkin edi)
        if isinstance(order, Payment) and order.provider != "payme":
            return None
        # O'chirilgan (arxivlangan) testga yangi to'lov qabul qilinmaydi
        if isinstance(order, Payment):
            test = db.get(Test, order.test_id)
            if not test or test.deleted_at is not None:
                return None
        return order

    return None


def _payme_find_order_by_transaction(params: dict, db: Session):
    """So'rovdagi Payme tranzaksiya ID'si bo'yicha buyurtmani topadi."""

    payme_id = params.get("id")
    if not payme_id:
        return None

    for model in PAYME_ORDER_MODELS.values():
        # Qulflanadi: Payme bir so'rovni qayta yuborsa ham token ikki marta yozilmaydi
        order = db.scalar(select(model).where(model.payme_transaction_id == payme_id).with_for_update())
        if order:
            return order

    return None


def _payme_account_of(order) -> dict:
    """Buyurtmani Payme tushunadigan "account" ko'rinishida qaytaradi."""

    return {"order_id": _click_order_ref(order)}


def _payme_apply_paid(order) -> None:
    """
    To'lov haqiqatan o'tganda bajariladi.

    Token/obuna xaridida — o'qituvchiga token yoziladi yoki obunasi uzayadi.
    Testga to'lovda — o'qituvchi balansiga ulushi qo'shiladi (qolgani platformada).
    """

    if isinstance(order, CreditPurchase):
        _grant_purchase(order)
        return

    db = object_session(order)
    percent = _commission_percent(db, order.test.teacher)
    share = _teacher_share(order.amount, percent)
    # Ulush shu paytdagi foiz bilan saqlanadi — admin foizni keyin o'zgartirsa ham bu to'lov o'zgarmaydi
    order.teacher_share = share
    add_balance(order.test.teacher, share)
    # O'qituvchiga botda: pul balansga tushdi — /pul bilan yechib oladi
    teacher_user = order.test.teacher.user if order.test.teacher else None
    if teacher_user:
        _send_result_notice((teacher_user.tg_id, (
            f"💰 To'lov tushdi: «{order.test.title}» — {_format_sum(order.amount)} so'm\n"
            f"Balansingizga: +{_format_sum(share)} so'm (platforma ulushi {percent}%)\n"
            "Pulni yechib olish: /pul"
        )))


def _payme_revert_paid(order) -> None:
    """To'lov bekor qilinganda (pul qaytarilganda) yozilgani orqaga olinadi."""

    if isinstance(order, CreditPurchase):
        _revoke_purchase(order)
        return

    add_balance(order.test.teacher, -_payment_teacher_share(order))


def _payme_receipt_detail(order) -> dict:
    """
    Payme fiskal cheki uchun ma'lumot: bitta qator — xizmat nomi, narxi (tiyinda),
    MXIK kodi, o'lchov birligi kodi va QQS foizi (admin panelida o'zgartiriladi).
    """

    if isinstance(order, CreditPurchase):
        title = f"Chittak platformasi: {_purchase_label(order)}"
    else:
        title = f"Chittak platformasi: «{order.test.title}» testi"
    return {
        "receipt_type": 0,
        "items": [{
            "title": title[:128],
            "price": order.amount * 100,
            "count": 1,
            "code": PAYME_MXIK,
            "package_code": PAYME_PACKAGE_CODE,
            "vat_percent": int(PAYME_VAT_PERCENT or 0),
        }],
    }


def _payme_check_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_account(params, db)
    if not order:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")
    if order.status != "pending":
        return _payme_error(request_id, -31051, "Buyurtma allaqachon ko'rib chiqilgan")
    if order.payme_transaction_id:
        return _payme_error(request_id, -31099, "Buyurtma boshqa tranzaksiya bilan band")
    if params.get("amount") != order.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")
    if _test_already_paid(db, order):
        return _payme_error(request_id, -31051, "Buyurtma allaqachon to'langan")

    # Fiskal chek ma'lumoti (MXIK, o'lchov birligi, QQS) — Payme chekni shundan chiqaradi
    return _payme_result(request_id, {"allow": True, "detail": _payme_receipt_detail(order)})


def _payme_create_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    order = _payme_find_order_by_account(params, db)

    if not order:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")

    # Shu tranzaksiya qayta yuborildi — holatiga qarab javob
    if payme_id and order.payme_transaction_id == payme_id:
        if order.status != "pending":
            return _payme_error(request_id, -31008, "Tranzaksiyani bajarib bo'lmaydi")
        if _payme_timed_out(order):
            _payme_cancel(order, 4)
            db.commit()
            return _payme_error(request_id, -31008, "Tranzaksiya muddati o'tgan")
        return _payme_result(request_id, {"create_time": _payme_ms(_payme_created(order)),
                                          "transaction": _click_order_ref(order), "state": 1})

    if order.payme_transaction_id:
        return _payme_error(request_id, -31099, "Buyurtma boshqa tranzaksiya bilan band")
    if order.status != "pending":
        return _payme_error(request_id, -31051, "Buyurtma allaqachon ko'rib chiqilgan")
    if params.get("amount") != order.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")
    if _test_already_paid(db, order):
        return _payme_error(request_id, -31051, "Buyurtma allaqachon to'langan")

    order.payme_transaction_id = payme_id
    order.payme_created_at = utcnow()
    order.payme_time = params.get("time") if isinstance(params.get("time"), int) else None
    db.commit()
    return _payme_result(request_id, {"create_time": _payme_ms(order.payme_created_at),
                                      "transaction": _click_order_ref(order), "state": 1})


def _payme_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")
    if order.status == "cancelled":
        return _payme_error(request_id, -31008, "Tranzaksiya bekor qilingan")

    if order.status != "paid":
        if _payme_timed_out(order):
            _payme_cancel(order, 4)
            db.commit()
            return _payme_error(request_id, -31008, "Tranzaksiya muddati o'tgan")
        if _test_already_paid(db, order):
            # Shu orada boshqa yo'l (Click) bilan to'lab bo'lindi — Payme pulni yechmaydi
            return _payme_error(request_id, -31008, "Buyurtma allaqachon to'langan")
        order.status = "paid"
        order.performed_at = utcnow()
        _payme_apply_paid(order)
        db.commit()

    return _payme_result(request_id, {"transaction": _click_order_ref(order),
                                      "perform_time": _payme_ms(order.performed_at), "state": 2})


def _payme_cancel_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    if order.status != "cancelled":
        reason = params.get("reason")
        _payme_cancel(order, reason if isinstance(reason, int) else None)
        db.commit()

    return _payme_result(request_id, {"transaction": _click_order_ref(order),
                                      "cancel_time": _payme_ms(order.cancelled_at), "state": _payme_state(order)})


def _payme_transaction_info(order) -> dict:
    """Payme kutayotgan, tranzaksiya holatini tasvirlovchi javob."""

    return {
        "create_time": _payme_ms(_payme_created(order)),
        "perform_time": _payme_ms(order.performed_at),
        "cancel_time": _payme_ms(order.cancelled_at),
        "transaction": _click_order_ref(order),
        "state": _payme_state(order),
        "reason": order.payme_cancel_reason if order.status == "cancelled" else None,
    }


def _payme_check_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    return _payme_result(request_id, _payme_transaction_info(order))


def _payme_get_statement(request_id, params: dict, db: Session) -> JSONResponse:
    # Davr millisekundda solishtiriladi: sanaga aylantirilmaydi — juda katta "to"
    # (masalan 10^16) Python sanasi chegarasidan chiqib, server yiqilardi
    from_ms, to_ms = params.get("from"), params.get("to")
    if not isinstance(from_ms, int) or not isinstance(to_ms, int) or isinstance(from_ms, bool) or isinstance(to_ms, bool):
        return _payme_error(request_id, -32600, "So'rov noto'g'ri")

    orders = []
    for model in PAYME_ORDER_MODELS.values():
        candidates = db.scalars(select(model).where(model.payme_transaction_id.is_not(None))).all()
        orders.extend(order for order in candidates if from_ms <= _payme_ms(_payme_created(order)) <= to_ms)

    orders.sort(key=_payme_created)

    transactions = [
        {
            "id": order.payme_transaction_id,
            "time": order.payme_time or _payme_ms(_payme_created(order)),
            "amount": order.amount * 100,
            "account": _payme_account_of(order),
            **_payme_transaction_info(order),
        }
        for order in orders
    ]
    return _payme_result(request_id, {"transactions": transactions})


def _payme_change_password(request_id, params: dict, db: Session) -> JSONResponse:
    """Payme kassa kalitini almashtiradi: yangi kalit saqlanadi va darhol kuchga kiradi."""

    password = params.get("password")
    if not isinstance(password, str) or not password.strip() or len(password) > 200:
        return _payme_error(request_id, -32600, "So'rov noto'g'ri")
    row = db.get(AppSetting, "payme_secret_key")
    if row:
        row.value, row.updated_at = password.strip(), utcnow()
    else:
        db.add(AppSetting(key="payme_secret_key", value=password.strip()))
    db.commit()
    _apply_payment_settings(db)
    logging.getLogger("backend").warning("Payme kaliti ChangePassword orqali almashtirildi")
    return _payme_result(request_id, {"success": True})


PAYME_METHOD_HANDLERS = {
    "CheckPerformTransaction": _payme_check_perform_transaction,
    "CreateTransaction": _payme_create_transaction,
    "PerformTransaction": _payme_perform_transaction,
    "CancelTransaction": _payme_cancel_transaction,
    "CheckTransaction": _payme_check_transaction,
    "GetStatement": _payme_get_statement,
    "ChangePassword": _payme_change_password,
}


@app.post("/api/payme/webhook")
async def payme_webhook(request: Request, db: Session = Depends(get_db)):
    """Payme Merchant API — token va testga to'lov hodisalari shu yerga keladi."""

    try:
        body = await request.json()
    except Exception:
        return _payme_error(None, -32700, "JSON o'qilmadi")
    if not isinstance(body, dict):
        return _payme_error(None, -32600, "So'rov noto'g'ri")

    request_id = body.get("id")
    method = body.get("method")
    params = body.get("params", {})

    if not _payme_check_auth(request):
        return _payme_error(request_id, -32504, "Ruxsat yo'q")

    handler = PAYME_METHOD_HANDLERS.get(method)
    if not handler:
        return _payme_error(request_id, -32601, "Metod topilmadi")
    if not isinstance(params, dict):
        return _payme_error(request_id, -32600, "So'rov noto'g'ri")

    try:
        return handler(request_id, params, db)
    except Exception:
        # Kutilmagan xato — Payme'ga 500 emas, spetsifikatsiyadagi "tizim xatosi" (-32400)
        db.rollback()
        logging.getLogger("backend").exception("Payme %s so'rovida xato", method)
        return _payme_error(request_id, -32400, "Tizim xatosi")


# ========== 8) Admin endpointlari ==========


@app.get("/api/admin/stats")
def admin_overview(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """
    Butun platforma bo'yicha umumiy statistika — faqat ADMIN_ID'ga ega foydalanuvchi ko'ra oladi.

    Platformaning daromadi IKKI manbadan keladi va ularni aralashtirmaslik kerak:

    1) Token sotuvi — o'qituvchilar token sotib oladi. Bu pul to'liq
       platformaga tegishli (100%).
    2) Pullik testlar — o'quvchi to'laydi, uning faqat komissiya qismi
       (admin «💰 Narxlar» bo'limida belgilaydi) platformada qoladi, qolgani o'qituvchiniki.

    Ilgari bu yerda faqat testga to'lovlarning TO'LIQ summasi ko'rsatilardi:
    ya'ni asosiy daromad (token sotuvi) umuman hisoblanmas, test to'lovi esa
    o'qituvchining ulushi bilan birga, oshirib ko'rsatilardi.
    """

    credits_revenue = (
        db.scalar(select(func.sum(CreditPurchase.amount)).where(CreditPurchase.status == "paid")) or 0
    )
    # Kartaga to'g'ridan-to'g'ri to'lovlar (provider="card") platforma orqali o'tmaydi — hisobga kirmaydi
    tests_turnover = db.scalar(
        select(func.sum(Payment.amount)).where(Payment.status == "paid", Payment.provider != "card")
    ) or 0
    tests_commission = _tests_commission_total(db)

    # O'qituvchilar balansidagi pul — bu platformaning QARZI: ular istalgan
    # paytda yechib olishni so'rashi mumkin, shuning uchun "sof" daromad emas.
    teachers_owed = db.scalar(select(func.sum(Teacher.balance))) or 0

    # Davr bo'yicha o'sish: bugun (UTC sutka), oxirgi 7 va 30 kun
    now = utcnow()
    since = {
        "today": now.replace(hour=0, minute=0, second=0, microsecond=0),
        "week": now - timedelta(days=7),
        "month": now - timedelta(days=30),
    }

    def _count_since(model, column, extra=()):
        return {
            key: db.scalar(select(func.count()).select_from(model).where(column >= moment, *extra)) or 0
            for key, moment in since.items()
        }

    def _credit_revenue_since():
        return {
            key: db.scalar(
                select(func.sum(CreditPurchase.amount)).where(
                    CreditPurchase.status == "paid",
                    func.coalesce(CreditPurchase.performed_at, CreditPurchase.created_at) >= moment,
                )
            ) or 0
            for key, moment in since.items()
        }

    periods = {
        "new_users": _count_since(User, User.created_at),
        "new_tests": _count_since(Test, Test.created_at),
        "new_schools": _count_since(PrivateSchool, PrivateSchool.created_at),
        "attempts": _count_since(Attempt, Attempt.finished_at, (Attempt.status == "done",)),
        "credits_revenue": _credit_revenue_since(),
    }

    pending = {
        "card_topups": db.scalar(
            select(func.count()).select_from(CreditPurchase).where(
                CreditPurchase.method == "card", CreditPurchase.status == "pending"
            )
        ) or 0,
        "withdrawals": db.scalar(
            select(func.count()).select_from(WithdrawalRequest).where(WithdrawalRequest.status == "pending")
        ) or 0,
    }

    return {
        "periods": periods,
        "pending": pending,
        "schools": db.scalar(select(func.count()).select_from(PrivateSchool).where(PrivateSchool.deleted_at.is_(None))),
        "users": db.scalar(select(func.count()).select_from(User)),
        "teachers": db.scalar(select(func.count()).select_from(Teacher)),
        "tests": db.scalar(select(func.count()).select_from(Test)),
        "attempts": db.scalar(select(func.count()).select_from(Attempt).where(Attempt.status == "done")),
        # Daromad tafsiloti
        "credits_revenue": credits_revenue,
        "credits_sold": db.scalar(
            select(func.sum(CreditPurchase.credits)).where(CreditPurchase.status == "paid")
        ) or 0,
        "tests_turnover": tests_turnover,
        "tests_commission": tests_commission,
        "revenue": credits_revenue + tests_commission,
        "teachers_owed": teachers_owed,
        "commission_percent": _global_commission_percent(db),
        "ai": _admin_ai_status(db, since),
    }


def _admin_ai_status(db: Session, since: dict) -> dict:
    """
    Admin uchun AI holati: ulanganmi, qaysi AI va model, oxirgi xato, esselar soni.
    Kalit qiymatlari hech qachon qaytarilmaydi — faqat bor/yo'qligi.
    """

    from . import ai_reader

    provider = ai_reader.ai_provider()
    essay_done = (Attempt.status == "done", Attempt.essay_ai_score.is_not(None))
    graded = {
        key: db.scalar(
            select(func.count()).select_from(Attempt).where(*essay_done, Attempt.finished_at >= moment)
        ) or 0
        for key, moment in since.items()
    }
    # AI tekshirishi kerak, lekin hali ball qo'yilmagan esselar (navbatda yoki xato bergan)
    waiting = db.scalar(
        select(func.count()).select_from(Attempt).join(Test, Test.id == Attempt.test_id).where(
            Attempt.status == "done",
            Test.essay_ai.is_(True),
            Attempt.essay_text.is_not(None),
            Attempt.essay_score.is_(None),
            Attempt.essay_ai_score.is_(None),
        )
    ) or 0
    return {
        "enabled": provider is not None,
        "provider": provider,
        "model": (ai_reader.GEMINI_MODEL if provider == "gemini" else ai_reader.AI_MODEL) if provider else None,
        "keys": {"gemini": bool(os.getenv("GEMINI_API_KEY")), "anthropic": bool(os.getenv("ANTHROPIC_API_KEY"))},
        "essay_tests": db.scalar(select(func.count()).select_from(Test).where(Test.essay_ai.is_(True))) or 0,
        "essays_graded": graded,
        "essays_graded_total": db.scalar(select(func.count()).select_from(Attempt).where(*essay_done)) or 0,
        "essays_waiting": waiting,
        "last_error": ai_reader.last_error,
    }


def _promo_view(db: Session, promo: PromoCode) -> dict:
    used = db.scalar(select(func.count()).select_from(PromoRedemption).where(PromoRedemption.promo_id == promo.id)) or 0
    return {
        "id": promo.id,
        "hint": promo.hint,
        "credits": promo.credits,
        "max_uses": promo.max_uses,
        "per_teacher_limit": promo.per_teacher_limit,
        "used": used,
        "expires_on": promo.expires_on,
        "active": promo.active,
        "created_at": promo.created_at,
    }


@app.get("/api/admin/promo-codes")
def admin_promo_codes(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    promos = db.scalars(select(PromoCode).order_by(PromoCode.id.desc())).all()
    return [_promo_view(db, promo) for promo in promos]


@app.post("/api/admin/promo-codes")
def admin_create_promo(body: PromoCreateIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Admin yangi promo kod yaratadi (kodning o'zi saqlanmaydi — faqat izi)."""
    code_hash = _promo_hash(body.code)
    if db.scalar(select(PromoCode.id).where(PromoCode.code_hash == code_hash)):
        raise HTTPException(status_code=409, detail="Bunday promo kod allaqachon bor")
    promo = PromoCode(code_hash=code_hash, hint=_promo_hint(body.code), credits=body.credits,
                      max_uses=body.max_uses, per_teacher_limit=body.per_teacher_limit,
                      expires_on=body.expires_on, created_by=admin.id)
    db.add(promo)
    db.commit()
    db.refresh(promo)
    return _promo_view(db, promo)


@app.post("/api/admin/promo-codes/{promo_id}/toggle")
def admin_toggle_promo(promo_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Promo kodni to'xtatish yoki qayta yoqish (o'chirilmaydi — kim ishlatgani tarixda qoladi)."""
    promo = db.get(PromoCode, promo_id)
    if not promo:
        raise HTTPException(status_code=404, detail="Promo kod topilmadi")
    promo.active = not promo.active
    db.commit()
    return _promo_view(db, promo)


@app.get("/api/admin/teachers")
def admin_list_teachers(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """
    Barcha o'qituvchilar ro'yxati: kim qancha token sotib olgan, qancha
    test yaratgan, balansida qancha pul bor.

    Ko'p o'qituvchili platformada eng faol (va eng ko'p to'lov qiladigan)
    o'qituvchilarni ko'rish uchun kerak.
    """

    spent = (
        select(
            CreditPurchase.teacher_id.label("teacher_id"),
            func.sum(CreditPurchase.amount).label("paid_total"),
        )
        .where(CreditPurchase.status == "paid")
        .group_by(CreditPurchase.teacher_id)
        .subquery()
    )
    test_counts = (
        select(Test.teacher_id.label("teacher_id"), func.count().label("test_count"))
        .group_by(Test.teacher_id)
        .subquery()
    )

    query = (
        select(Teacher, User.full_name, spent.c.paid_total, test_counts.c.test_count)
        .join(User, User.id == Teacher.user_id)
        .outerjoin(spent, spent.c.teacher_id == Teacher.id)
        .outerjoin(test_counts, test_counts.c.teacher_id == Teacher.id)
        .order_by(func.coalesce(spent.c.paid_total, 0).desc(), Teacher.id)
    )

    return [
        {
            "id": teacher.id,
            "user_id": teacher.user_id,
            "name": full_name,
            "credits": teacher.credits,
            "balance": teacher.balance,
            "paid_total": paid_total or 0,
            "tests": test_count or 0,
        }
        for teacher, full_name, paid_total, test_count in db.execute(query).all()
    ]


@app.post("/api/admin/backup")
def admin_send_backup(admin: User = Depends(get_current_admin)):
    """
    Zaxira nusxani fonda tayyorlab adminlarning Telegram chatiga yuboradi: katta nusxa bir necha
    daqiqa oladi, so'rov shuni kutib qolmaydi. Yuborilmasa — bot adminlarga o'zi xabar beradi.
    """

    if not ADMIN_IDS:
        raise HTTPException(status_code=502, detail="Admin sozlanmagan (ADMIN_ID)")
    threading.Thread(target=_send_backup_once, name="backup", daemon=True).start()
    return {"ok": True}


@app.get("/api/admin/backup")
def admin_download_backup(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Zaxira nusxani to'g'ridan-to'g'ri yuklab beradi (brauzerda sinash uchun)."""

    return StreamingResponse(
        io.BytesIO(export_database(db)),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="backup_{datetime.now():%Y-%m-%d}.json"'},
    )


# ---------- To'lov tizimlari (Payme / Click) sozlamalari ----------
# Kalitlar bazada saqlanadi va darhol kuchga kiradi (qarang: _apply_payment_settings).
# Maxfiy kalitning o'zi hech qachon qaytarilmaydi — faqat "qo'yilgan / qo'yilmagan".


def _payment_settings_view(db: Session) -> dict:
    saved = {row.key: (row.value or "").strip() for row in db.scalars(select(AppSetting)).all()}
    base = WEBAPP_URL.rstrip("/")

    def source(key: str) -> str:
        """Qiymat qayerdan kelgani: admin panelidan, .env'dan yoki hali yo'q."""
        if saved.get(key):
            return "db"
        return "env" if _ENV_PAYMENT_DEFAULTS[key] else "none"

    providers = _enabled_providers()
    return {
        "payme": {
            "merchant_id": PAYME_MERCHANT_ID,
            "merchant_source": source("payme_merchant_id"),
            "secret_set": bool(PAYME_SECRET_KEY),
            "secret_source": source("payme_secret_key"),
            "test_mode": PAYME_TEST_MODE,
            "enabled": "payme" in providers,
            # Fiskal chek
            "mxik": PAYME_MXIK,
            "package_code": PAYME_PACKAGE_CODE,
            "vat_percent": int(PAYME_VAT_PERCENT or 0),
        },
        "click": {
            "service_id": CLICK_SERVICE_ID,
            "merchant_id": CLICK_MERCHANT_ID,
            "secret_set": bool(CLICK_SECRET_KEY),
            "enabled": "click" in providers,
        },
        # Payme/Click kabinetiga kiritiladigan manzillar
        "webhooks": {
            "payme": f"{base}/api/payme/webhook",
            "click_prepare": f"{base}/api/click/prepare",
            "click_complete": f"{base}/api/click/complete",
        },
    }


@app.get("/api/admin/payment-settings")
def admin_payment_settings(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """To'lov tizimlarining holati: qaysi kalit qo'yilgan, qaysi usul yoqilgan."""

    return _payment_settings_view(db)


@app.post("/api/admin/payment-settings")
def admin_save_payment_settings(body: PaymentSettingsIn, admin: User = Depends(get_current_admin),
                                db: Session = Depends(get_db)):
    """
    Kalitlarni saqlaydi va darhol kuchga kiritadi (serverni qayta ishga tushirish shart emas).

    Bo'sh qoldirilgan maydon — "o'zgarishsiz qoldir". Kalitni butunlay olib
    tashlash uchun uning nomi `clear` ro'yxatida yuboriladi (shunda .env'dagisi qoladi).
    """

    updates: dict[str, str] = {}
    for key in ("payme_merchant_id", "payme_secret_key", "click_service_id", "click_merchant_id", "click_secret_key"):
        value = (getattr(body, key) or "").strip()
        if value:
            updates[key] = value
    if body.payme_test_mode is not None:
        updates["payme_test_mode"] = "1" if body.payme_test_mode else "0"
    # Fiskal chek: MXIK — 17 ta raqam, o'lchov birligi kodi — faqat raqam
    mxik = (body.payme_mxik or "").strip()
    if mxik:
        if not re.fullmatch(r"\d{17}", mxik):
            raise HTTPException(status_code=422, detail="MXIK kodi 17 ta raqamdan iborat bo'lishi kerak")
        updates["payme_mxik"] = mxik
    package_code = (body.payme_package_code or "").strip()
    if package_code:
        if not re.fullmatch(r"\d{1,20}", package_code):
            raise HTTPException(status_code=422, detail="O'lchov birligi kodi faqat raqamlardan iborat bo'ladi")
        updates["payme_package_code"] = package_code
    if body.payme_vat_percent is not None:
        updates["payme_vat_percent"] = str(body.payme_vat_percent)
    for key in body.clear:
        if key in PAYMENT_SETTING_KEYS:
            updates[key] = ""

    for key, value in updates.items():
        row = db.get(AppSetting, key)
        if row:
            row.value, row.updated_at, row.updated_by = value, utcnow(), admin.id
        else:
            db.add(AppSetting(key=key, value=value, updated_by=admin.id))
    db.commit()

    _apply_payment_settings(db)
    # Log'da faqat kalitlarning NOMI — qiymati hech qayerga yozilmaydi
    logging.getLogger("backend").info(
        "To'lov sozlamalari yangilandi: %s (admin=%s)", ", ".join(sorted(updates)) or "o'zgarishsiz", admin.id
    )
    return _payment_settings_view(db)


# ---------- Oylik obuna narxlari ----------


def _subscription_settings_view(db: Session) -> dict:
    return {
        "plans": [
            {"months": months, "days": SUBSCRIPTION_PLAN_DAYS[months], "price": price}
            for months, price in _subscription_prices(db).items()
        ],
        # Obunasi hozir faol o'qituvchilar soni — admin narxni o'zgartirishdan oldin ko'rsin
        "active": db.scalar(select(func.count()).select_from(Teacher).where(Teacher.subscription_until > utcnow())) or 0,
    }


@app.get("/api/admin/subscription-settings")
def admin_subscription_settings(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Har bir obuna muddatining narxi (0 — bu muddat o'qituvchilarga ko'rsatilmaydi)."""

    return _subscription_settings_view(db)


@app.post("/api/admin/subscription-settings")
def admin_save_subscription_settings(body: SubscriptionSettingsIn, admin: User = Depends(get_current_admin),
                                     db: Session = Depends(get_db)):
    """Obuna narxlarini saqlaydi — darhol kuchga kiradi. Allaqachon sotilgan obunalarga ta'sir qilmaydi."""

    for months, price in body.prices.items():
        key = _subscription_price_key(months)
        row = db.get(AppSetting, key)
        if row:
            row.value, row.updated_at, row.updated_by = str(price), utcnow(), admin.id
        else:
            db.add(AppSetting(key=key, value=str(price), updated_by=admin.id))
    db.commit()
    logging.getLogger("backend").info("Obuna narxlari yangilandi: %s (admin=%s)", body.prices, admin.id)
    return _subscription_settings_view(db)


# ---------- Pullik test e'loni narxi ----------


class BroadcastPriceIn(BaseModel):
    """Admin panelidan: bitta test e'loni narxi (so'm). 0 — e'lon bepul."""

    price: int = Field(ge=0, le=100_000_000)


def _broadcast_settings_view(db: Session) -> dict:
    paid = (CreditPurchase.kind == "broadcast", CreditPurchase.status == "paid")
    recent = db.scalars(
        select(CreditPurchase).where(*paid).order_by(CreditPurchase.performed_at.desc().nulls_last(),
                                                     CreditPurchase.id.desc()).limit(20)
    ).all()
    return {
        "price": _broadcast_price(db),
        "payme_enabled": "payme" in _enabled_providers(),
        "paid_count": db.scalar(select(func.count()).select_from(CreditPurchase).where(*paid)) or 0,
        "revenue": db.scalar(select(func.sum(CreditPurchase.amount)).where(*paid)) or 0,
        "recent": [
            {"id": p.id, "label": _purchase_label(p), "amount": p.amount,
             "teacher": p.teacher.user.full_name if p.teacher and p.teacher.user else "—",
             "user_id": p.teacher.user_id if p.teacher else None,
             "paid_at": p.performed_at or p.created_at}
            for p in recent
        ],
    }


class VerifyPriceIn(BaseModel):
    """Admin panelidan: ✓ tasdiq belgisi narxi (so'm). 0 — sotilmaydi (belgini faqat admin qo'yadi)."""

    price: int = Field(ge=0, le=100_000_000)


def _verify_settings_view(db: Session) -> dict:
    paid = (CreditPurchase.kind == "verify", CreditPurchase.status == "paid")
    return {
        "price": _verify_price(db),
        "paid_count": db.scalar(select(func.count()).select_from(CreditPurchase).where(*paid)) or 0,
        "revenue": db.scalar(select(func.sum(CreditPurchase.amount)).where(*paid)) or 0,
        "verified_count": db.scalar(select(func.count()).select_from(PrivateSchool).where(
            PrivateSchool.verified_at.is_not(None), PrivateSchool.deleted_at.is_(None))) or 0,
    }


@app.get("/api/admin/verify-price")
def admin_verify_price(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    return _verify_settings_view(db)


@app.post("/api/admin/verify-price")
def admin_save_verify_price(body: VerifyPriceIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """✓ Tasdiq belgisi narxini saqlaydi — darhol kuchga kiradi."""

    row = db.get(AppSetting, VERIFY_PRICE_KEY)
    if row:
        row.value, row.updated_at, row.updated_by = str(body.price), utcnow(), admin.id
    else:
        db.add(AppSetting(key=VERIFY_PRICE_KEY, value=str(body.price), updated_by=admin.id))
    db.commit()
    logging.getLogger("backend").info("Tasdiq belgisi narxi yangilandi: %s so'm (admin=%s)", body.price, admin.id)
    return _verify_settings_view(db)


# ---------- Pullik test foizlari (platforma / muallif) ----------


class CommissionIn(BaseModel):
    """Admin panelidan: pullik testlardan platformaning umumiy foizi (qolgani — muallifga)."""

    percent: int = Field(ge=0, le=100)


class TeacherCommissionIn(BaseModel):
    """Muayyan muallif uchun alohida platforma foizi; None — umumiy foizga qaytaradi."""

    teacher_id: int
    percent: int | None = Field(default=None, ge=0, le=100)


def _commission_settings_view(db: Session, query: str = "") -> dict:
    """Umumiy foiz, alohida foizli mualliflar, pullik testdan daromad qilgan mualliflar va qidiruv natijasi."""

    paid = (Payment.status == "paid", Payment.provider != "card")
    turnover_rows = db.execute(
        select(Test.teacher_id, func.sum(Payment.amount), func.count(Payment.id))
        .join(Test, Test.id == Payment.test_id).where(*paid).group_by(Test.teacher_id)
    ).all()
    turnover = {teacher_id: (int(total or 0), int(count or 0)) for teacher_id, total, count in turnover_rows}
    commission = {
        teacher_id: _tests_commission_total(db, Payment.test_id.in_(select(Test.id).where(Test.teacher_id == teacher_id)))
        for teacher_id in turnover
    }

    shown_ids = set(turnover) | set(
        db.scalars(select(Teacher.id).where(Teacher.commission_percent.is_not(None))).all()
    )
    found: list[int] = []
    query = (query or "").strip()
    if len(query) >= 2:
        like = f"%{query.lower()}%"
        found = db.scalars(
            select(Teacher.id).join(User, User.id == Teacher.user_id)
            .where(User.deleted_at.is_(None),
                   or_(func.lower(User.full_name).like(like), func.lower(func.coalesce(User.username, "")).like(like)))
            .limit(20)
        ).all()
        shown_ids |= set(found)

    teachers = db.scalars(
        select(Teacher).where(Teacher.id.in_(shown_ids)).options(selectinload(Teacher.user))
    ).all() if shown_ids else []
    global_percent = _global_commission_percent(db)

    def row(teacher: Teacher) -> dict:
        total, count = turnover.get(teacher.id, (0, 0))
        percent = _commission_percent(db, teacher)
        return {
            "teacher_id": teacher.id,
            "user_id": teacher.user_id,
            "name": (teacher.user.full_name if teacher.user else "") or f"#{teacher.id}",
            "username": teacher.user.username if teacher.user else None,
            "custom_percent": teacher.commission_percent,
            "platform_percent": percent,
            "author_percent": 100 - percent,
            "paid_count": count,
            "turnover": total,
            "platform_income": commission.get(teacher.id, 0),
            "author_income": total - commission.get(teacher.id, 0),
            "balance": teacher.balance,
        }

    rows = sorted((row(t) for t in teachers), key=lambda r: (-r["turnover"], r["name"].lower()))
    total_turnover = sum(total for total, _ in turnover.values())
    total_commission = sum(commission.values())
    return {
        "percent": global_percent,
        "author_percent": 100 - global_percent,
        "env_percent": _clamp_percent(PLATFORM_COMMISSION_PERCENT),
        "turnover": total_turnover,
        "platform_income": total_commission,
        "author_income": total_turnover - total_commission,
        "teachers_owed": db.scalar(select(func.sum(Teacher.balance))) or 0,
        "authors": [r for r in rows if r["turnover"] or r["custom_percent"] is not None],
        "found": [r for r in rows if r["teacher_id"] in set(found)],
    }


@app.get("/api/admin/commission")
def admin_commission(q: str = "", admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Pullik testlar: platforma va muallif foizlari, kim qancha ishlagan; q — muallifni ism bo'yicha qidirish."""

    return _commission_settings_view(db, q)


@app.post("/api/admin/commission")
def admin_save_commission(body: CommissionIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Umumiy foizni saqlaydi — faqat YANGI to'lovlarga ta'sir qiladi (o'tganlarining ulushi saqlangan)."""

    row = db.get(AppSetting, COMMISSION_PERCENT_KEY)
    if row:
        row.value, row.updated_at, row.updated_by = str(body.percent), utcnow(), admin.id
    else:
        db.add(AppSetting(key=COMMISSION_PERCENT_KEY, value=str(body.percent), updated_by=admin.id))
    db.commit()
    logging.getLogger("backend").info("Pullik test foizi: platforma %s%% (admin=%s)", body.percent, admin.id)
    return _commission_settings_view(db)


@app.post("/api/admin/commission/teacher")
def admin_save_teacher_commission(body: TeacherCommissionIn, admin: User = Depends(get_current_admin),
                                  db: Session = Depends(get_db)):
    """Muallifga alohida foiz qo'yadi yoki olib tashlaydi (percent = null — umumiy foiz). Muallifga botda xabar boradi."""

    teacher = db.get(Teacher, body.teacher_id)
    if not teacher:
        raise HTTPException(status_code=404, detail="Muallif topilmadi")
    before = _commission_percent(db, teacher)
    teacher.commission_percent = body.percent
    db.commit()
    after = _commission_percent(db, teacher)
    logging.getLogger("backend").info(
        "Muallif foizi: teacher=%s platforma %s%% -> %s%% (alohida=%s, admin=%s)",
        teacher.id, before, after, body.percent, admin.id,
    )
    if after != before and teacher.user:
        _send_result_notice((teacher.user.tg_id, (
            f"💼 Pullik testlaringiz bo'yicha ulushingiz o'zgardi: endi har to'lovning {100 - after}% sizga, "
            f"{after}% platformaga.\nBu faqat yangi to'lovlarga taalluqli."
        )))
    return _commission_settings_view(db)


@app.get("/api/admin/broadcast-price")
def admin_broadcast_price(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Test e'loni narxi va pullik e'lonlar statistikasi."""

    return _broadcast_settings_view(db)


@app.post("/api/admin/broadcast-price")
def admin_save_broadcast_price(body: BroadcastPriceIn, admin: User = Depends(get_current_admin),
                               db: Session = Depends(get_db)):
    """E'lon narxini saqlaydi — darhol kuchga kiradi (allaqachon yaratilgan to'lov havolalari eski narxda qoladi)."""

    row = db.get(AppSetting, BROADCAST_PRICE_KEY)
    if row:
        row.value, row.updated_at, row.updated_by = str(body.price), utcnow(), admin.id
    else:
        db.add(AppSetting(key=BROADCAST_PRICE_KEY, value=str(body.price), updated_by=admin.id))
    db.commit()
    logging.getLogger("backend").info("E'lon narxi yangilandi: %s so'm (admin=%s)", body.price, admin.id)
    return _broadcast_settings_view(db)


@app.get("/api/admin/top-students")
def admin_top_students(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Kamida bitta testni tugatgan o'quvchilarning, barcha testlar bo'yicha o'rtacha natijasiga ko'ra reytingi."""

    query = (
        select(
            User.full_name,
            func.avg(Attempt.percent).label("avg_percent"),
            func.count(Attempt.id).label("attempts_count"),
        )
        .join(Attempt, Attempt.user_id == User.id)
        .where(Attempt.status == "done")
        .group_by(User.id)
        .order_by(func.avg(Attempt.percent).desc())
        .limit(20)
    )

    return [
        {"name": name, "avg_percent": round(avg_percent, 1), "attempts": attempts_count}
        for name, avg_percent, attempts_count in db.execute(query).all()
    ]


@app.get("/api/admin/withdrawal-requests")
def admin_list_withdrawal_requests(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Barcha kutilayotgan ('pending') pul yechish so'rovlari."""

    query = (
        select(WithdrawalRequest, Teacher)
        .join(Teacher, Teacher.id == WithdrawalRequest.teacher_id)
        .where(WithdrawalRequest.status == "pending")
        .order_by(WithdrawalRequest.id)
    )

    return [
        {
            "id": request.id,
            "teacher": teacher.user.full_name,
            "amount": request.amount,
            "card_number": request.card_number,
        }
        for request, teacher in db.execute(query).all()
    ]


@app.post("/api/admin/withdrawal-requests/{request_id}/approve")
def admin_approve_withdrawal(request_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Pul o'tkazilgach tasdiqlanadi — o'qituvchi balansidan shuncha summa yechiladi."""

    # Qulflanadi: admin panel va bot tugmasi bir vaqtda bosilsa ham pul ikki marta yechilmaydi
    request = db.get(WithdrawalRequest, request_id, with_for_update=True)
    if not request or request.status != "pending":
        raise HTTPException(status_code=404, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    teacher = db.get(Teacher, request.teacher_id)
    if not withdraw_balance(teacher, request.amount):
        raise HTTPException(status_code=402, detail="O'qituvchi balansi yetarli emas, so'rovni rad eting")

    request.status = "approved"
    db.commit()
    return {"ok": True, "balance": teacher.balance}


@app.post("/api/admin/withdrawal-requests/{request_id}/reject")
def admin_reject_withdrawal(request_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """So'rovni rad etadi, balansga tegmaydi."""

    request = db.get(WithdrawalRequest, request_id, with_for_update=True)
    if not request or request.status != "pending":
        raise HTTPException(status_code=404, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    request.status = "rejected"
    db.commit()
    return {"ok": True}


@app.get("/api/admin/card-topups")
def admin_list_card_topups(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """
    Tasdiq kutayotgan karta to'lovlari.

    Asosan admin chekni Telegram'da ko'rib, o'sha yerda tugmani bosadi. Bu
    ro'yxat — zaxira yo'l: xabar o'tkazib yuborilgan yoki o'chirilgan bo'lsa.
    """

    purchases = db.scalars(
        select(CreditPurchase)
        .where(CreditPurchase.method == "card", CreditPurchase.status == "pending")
        .order_by(CreditPurchase.id)
    ).all()

    return [
        {
            "id": purchase.id,
            "teacher": purchase.teacher.user.full_name,
            "kind": purchase.kind,
            "months": purchase.months,
            "label": _purchase_label(purchase),
            "credits": purchase.credits,
            "amount": purchase.amount,
            "created_at": purchase.created_at,
            "has_receipt": bool(purchase.receipt_file_id),
        }
        for purchase in purchases
    ]


@app.get("/api/admin/card-topups/{purchase_id}/receipt")
def admin_card_topup_receipt(
    purchase_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
):
    """
    Chek rasmini ko'rsatadi. Rasm serverda saqlanmaydi — Telegram'dan
    file_id orqali olinadi (Render'da fayllar har deployda o'chadi).
    """

    purchase = db.get(CreditPurchase, purchase_id)
    if not purchase or not purchase.receipt_file_id:
        raise HTTPException(status_code=404, detail="Chek topilmadi")

    try:
        file_info = httpx.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getFile",
            params={"file_id": purchase.receipt_file_id},
            timeout=15,
        ).json()
        file_path = file_info["result"]["file_path"]
        image = httpx.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30)
        image.raise_for_status()
    except Exception:
        logging.getLogger("backend").warning("Chek rasmi Telegram'dan olinmadi", exc_info=True)
        raise HTTPException(status_code=502, detail="Chek rasmini olib bo'lmadi")

    return StreamingResponse(
        io.BytesIO(image.content),
        media_type=image.headers.get("content-type", "image/jpeg"),
        # Rasm maxfiy — brauzer yoki oraliq serverlar uni saqlab qolmasin
        headers={"Cache-Control": "private, no-store"},
    )


@app.post("/api/admin/card-topups/{purchase_id}/approve")
def admin_approve_card_topup(purchase_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Kartaga pul tushgani tasdiqlanadi — o'qituvchiga token yoziladi."""

    ok, message = decide_card_topup(db, purchase_id, approve=True)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/admin/card-topups/{purchase_id}/reject")
def admin_reject_card_topup(purchase_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """To'lov rad etiladi — token yozilmaydi, o'qituvchiga xabar boradi."""

    ok, message = decide_card_topup(db, purchase_id, approve=False)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


class AdminCreditsIn(BaseModel):
    """Admin o'qituvchiga token qo'shadi (musbat) yoki ayiradi (manfiy). Sabab xabarda ko'rsatiladi."""

    delta: int = Field(ge=-1_000_000, le=1_000_000)
    reason: str = Field(default="", max_length=300)
    notify: bool = True


class AdminMessageIn(BaseModel):
    """Admin bot orqali bitta foydalanuvchiga xabar yuboradi."""

    text: str = Field(min_length=1, max_length=3500)


def _admin_user_row(user: User, teacher: Teacher | None, school_name: str | None) -> dict:
    return {
        "id": user.id,
        "tg_id": user.tg_id,
        "name": user.full_name or "—",
        "username": user.username,
        "created_at": user.created_at,
        "is_teacher": teacher is not None,
        "credits": teacher.credits if teacher else None,
        "subscription_until": teacher.subscription_until if teacher else None,
        "balance": teacher.balance if teacher else None,
        "school": school_name,
    }


@app.get("/api/admin/users")
def admin_search_users(
    q: str = "",
    only: str = "all",
    admin: User = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """
    Foydalanuvchilarni qidirish: ism, @username, Telegram ID yoki ichki ID bo'yicha.
    only=teachers — faqat o'qituvchilar. Eng yangi ro'yxatdan o'tganlar birinchi, ko'pi bilan 50 ta.
    """

    active_school = (
        select(SchoolMember.user_id.label("user_id"), func.min(PrivateSchool.name).label("school_name"))
        .join(PrivateSchool, PrivateSchool.id == SchoolMember.school_id)
        .where(SchoolMember.left_at.is_(None))
        .group_by(SchoolMember.user_id)
        .subquery()
    )
    query = (
        select(User, Teacher, active_school.c.school_name)
        .outerjoin(Teacher, Teacher.user_id == User.id)
        .outerjoin(active_school, active_school.c.user_id == User.id)
    )
    if only == "teachers":
        query = query.where(Teacher.id.is_not(None))

    text_query = q.strip().lstrip("@")
    if text_query:
        conditions = [
            User.full_name.ilike(f"%{text_query}%"),
            User.username.ilike(f"%{text_query}%"),
        ]
        if text_query.isdigit():
            conditions += [User.tg_id == int(text_query), User.id == int(text_query)]
        query = query.where(or_(*conditions))

    rows = db.execute(query.order_by(User.id.desc()).limit(50)).all()
    return [_admin_user_row(user, teacher, school_name) for user, teacher, school_name in rows]


@app.get("/api/admin/users/{user_id}")
def admin_user_detail(user_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Bitta foydalanuvchi haqida hammasi: o'qituvchilik, muassasalari, testlari, to'lovlari, natijalari."""

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user.id))

    memberships = db.execute(
        select(SchoolMember, PrivateSchool)
        .join(PrivateSchool, PrivateSchool.id == SchoolMember.school_id)
        .where(SchoolMember.user_id == user.id)
        .order_by(SchoolMember.id.desc())
    ).all()

    attempts_done, avg_percent = db.execute(
        select(func.count(Attempt.id), func.avg(Attempt.percent)).where(
            Attempt.user_id == user.id, Attempt.status == "done"
        )
    ).one()

    data = {
        **_admin_user_row(user, teacher, None),
        "attempts": attempts_done or 0,
        "avg_percent": round(avg_percent, 1) if avg_percent is not None else None,
        "schools": [
            {
                "id": school.id,
                "name": school.name,
                "kind": school.kind,
                "region": school.region,
                "district": school.district,
                "role": "owner" if member.role == "owner" else (member.position or "teacher"),
                "joined_at": member.joined_at,
                "left_at": member.left_at,
            }
            for member, school in memberships
        ],
        "tests": 0,
        "paid_total": 0,
        "purchases": [],
    }
    if teacher:
        data["tests"] = db.scalar(select(func.count()).select_from(Test).where(Test.teacher_id == teacher.id)) or 0
        data["paid_total"] = db.scalar(
            select(func.sum(CreditPurchase.amount)).where(
                CreditPurchase.teacher_id == teacher.id, CreditPurchase.status == "paid"
            )
        ) or 0
        purchases = db.scalars(
            select(CreditPurchase)
            .where(CreditPurchase.teacher_id == teacher.id)
            .order_by(CreditPurchase.id.desc())
            .limit(10)
        ).all()
        data["purchases"] = [
            {
                "id": purchase.id,
                "kind": purchase.kind,
                "months": purchase.months,
                "label": _purchase_label(purchase),
                "credits": purchase.credits,
                "amount": purchase.amount,
                "status": purchase.status,
                "method": purchase.method,
                "created_at": purchase.created_at,
            }
            for purchase in purchases
        ]
    return data


@app.post("/api/admin/users/{user_id}/credits")
def admin_adjust_credits(
    user_id: int, body: AdminCreditsIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
):
    """
    O'qituvchiga qo'lda token qo'shish yoki ayirish (masalan, sovg'a yoki xato
    to'lovni tuzatish). Sotuv hisobiga kirmaydi — daromad statistikasi o'zgarmaydi.
    """

    if body.delta == 0:
        raise HTTPException(status_code=422, detail="Token soni 0 bo'lmasin")
    teacher = db.scalar(select(Teacher).where(Teacher.user_id == user_id))
    if not teacher:
        raise HTTPException(status_code=404, detail="Bu foydalanuvchi o'qituvchi emas — token faqat o'qituvchiga beriladi")

    add_credits(teacher, body.delta)
    db.commit()
    logging.getLogger("backend").info(
        "Admin %s token o'zgartirdi: user=%s delta=%s sabab=%r", admin.id, user_id, body.delta, body.reason
    )

    notified = False
    if body.notify:
        reason = f"\nSabab: {body.reason.strip()}" if body.reason.strip() else ""
        amount = f"{abs(body.delta):,}".replace(",", " ")
        total = f"{teacher.credits:,}".replace(",", " ")
        if body.delta > 0:
            text = f"🎁 Hisobingizga {amount} token qo'shildi.{reason}\nJami: {total} token"
        else:
            text = f"ℹ️ Hisobingizdan {amount} token yechildi.{reason}\nQoldi: {total} token"
        notified = _telegram_send_message(teacher.user.tg_id, text)
    return {"ok": True, "credits": teacher.credits, "notified": notified}


@app.post("/api/admin/users/{user_id}/message")
def admin_message_user(
    user_id: int, body: AdminMessageIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
):
    """Bot nomidan bitta foydalanuvchiga xabar. Botni bloklagan bo'lsa — xato qaytadi."""

    user = db.get(User, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="Foydalanuvchi topilmadi")
    status, _ = _telegram_send_status(user.tg_id, f"📩 Chittak ma'muriyatidan:\n\n{body.text.strip()}")
    if status != 200:
        raise HTTPException(
            status_code=502, detail="Xabar yetmadi — foydalanuvchi botni ishga tushirmagan yoki bloklagan"
        )
    return {"ok": True}


@app.get("/api/admin/schools")
def admin_list_schools(q: str = "", admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Barcha ta'lim muassasalari: turi, joyi, egasi, xodimlar, guruhlar va o'quvchilar soni."""

    staff = (
        select(SchoolMember.school_id.label("school_id"), func.count().label("staff"))
        .where(SchoolMember.left_at.is_(None))
        .group_by(SchoolMember.school_id)
        .subquery()
    )
    classes = (
        select(SchoolClass.school_id.label("school_id"), func.count().label("classes"))
        .where(SchoolClass.deleted_at.is_(None))
        .group_by(SchoolClass.school_id)
        .subquery()
    )
    students = (
        select(SchoolClass.school_id.label("school_id"), func.count(ClassStudent.id).label("students"))
        .join(ClassStudent, ClassStudent.class_id == SchoolClass.id)
        .where(SchoolClass.deleted_at.is_(None), ClassStudent.removed_at.is_(None))
        .group_by(SchoolClass.school_id)
        .subquery()
    )
    query = (
        select(PrivateSchool, User, staff.c.staff, classes.c.classes, students.c.students)
        .join(User, User.id == PrivateSchool.owner_user_id)
        .outerjoin(staff, staff.c.school_id == PrivateSchool.id)
        .outerjoin(classes, classes.c.school_id == PrivateSchool.id)
        .outerjoin(students, students.c.school_id == PrivateSchool.id)
        .where(PrivateSchool.deleted_at.is_(None))
    )
    text_query = q.strip()
    if text_query:
        query = query.where(
            or_(
                PrivateSchool.name.ilike(f"%{text_query}%"),
                PrivateSchool.region.ilike(f"%{text_query}%"),
                PrivateSchool.district.ilike(f"%{text_query}%"),
            )
        )
    rows = db.execute(query.order_by(PrivateSchool.id.desc()).limit(200)).all()
    return [
        {
            "id": school.id,
            "name": school.name,
            "kind": school.kind,
            "region": school.region,
            "district": school.district,
            "owner": owner.full_name,
            "owner_user_id": owner.id,
            "created_at": school.created_at,
            "verified": school.verified_at is not None,
            "staff": staff_count or 0,
            "classes": class_count or 0,
            "students": student_count or 0,
        }
        for school, owner, staff_count, class_count, student_count in rows
    ]


@app.post("/api/schools/{school_id}/verify-purchase")
def buy_school_verification(school_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Muassasa rahbariyati ✓ tasdiq belgisini Payme orqali sotib oladi: belgi to'lov o'tishi bilan qo'yiladi."""

    member = db.scalar(select(SchoolMember).where(SchoolMember.school_id == school_id, SchoolMember.user_id == user.id,
                                                  SchoolMember.left_at.is_(None)))
    school = member.school if member else None
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Muassasa topilmadi")
    if not _is_director(member):
        raise HTTPException(status_code=403, detail="Tasdiq belgisini muassasa rahbariyati sotib oladi")
    if school.verified_at is not None:
        raise HTTPException(status_code=409, detail="Muassasa allaqachon tasdiqlangan")
    price = _verify_price(db)
    if price <= 0:
        raise HTTPException(status_code=409, detail="Tasdiq belgisi hozircha sotilmaydi")
    if "payme" not in _enabled_providers():
        raise HTTPException(status_code=503, detail="Onlayn to'lov (Payme) hali sozlanmagan, admin bilan bog'laning")
    teacher = ensure_school_teacher(db, user)
    if not teacher:
        raise HTTPException(status_code=403, detail="Tasdiq belgisini muassasa rahbariyati sotib oladi")
    purchase = CreditPurchase(teacher_id=teacher.id, kind="verify", school_id=school.id, credits=0,
                              amount=price, method="payme")
    db.add(purchase)
    db.commit()
    return {"ok": True, "amount": price, "checkout_url": _checkout_url("payme", purchase, price)}


class AdminSchoolVerifyIn(BaseModel):
    verified: bool


@app.post("/api/admin/schools/{school_id}/verify")
def admin_verify_school(school_id: int, body: AdminSchoolVerifyIn, admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """Muassasaga ✓ (haqiqiy) belgisini qo'yadi yoki olib tashlaydi — qidiruvda tasdiqlanganlar tepada turadi."""

    school = db.get(PrivateSchool, school_id)
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Muassasa topilmadi")
    school.verified_at = (school.verified_at or utcnow()) if body.verified else None
    db.commit()
    logging.getLogger("backend").info("Muassasa tasdig'i: id=%s verified=%s admin=%s", school.id, body.verified, admin.id)
    return {"ok": True, "verified": school.verified_at is not None}


@app.post("/api/admin/schools/{school_id}/delete")
def admin_delete_school(school_id: int, body: AdminSchoolDeleteIn, admin: User = Depends(get_current_admin),
                        db: Session = Depends(get_db)):
    """
    Platforma admini muassasani o'chiradi: ro'yxatlardan yo'qoladi, kod va havola bilan qo'shilib
    bo'lmaydi, barcha xodimlar undan chiqariladi (Test markazidagi testlari o'zida qoladi).
    Ma'lumotlar bazada qoladi — tiklash mumkin. Tasdiq uchun muassasa nomi aynan yoziladi.
    """

    school = db.get(PrivateSchool, school_id)
    if not school or school.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Muassasa topilmadi")
    if " ".join(body.confirm.split()).lower() != " ".join(school.name.split()).lower():
        raise HTTPException(status_code=422, detail="Tasdiqlash uchun muassasa nomini aynan yozing")
    members = _soft_delete_school(school)
    db.commit()
    logging.getLogger("backend").warning("Muassasa o'chirildi: id=%s nomi=%s admin=%s (%s ta xodim chiqarildi)",
                                         school.id, school.name, admin.id, members)
    return {"ok": True, "members_removed": members}


@app.get("/api/admin/payments")
def admin_payment_history(
    status: str = "all", admin: User = Depends(get_current_admin), db: Session = Depends(get_db)
):
    """Token xaridlari tarixi (oxirgi 100 ta): Payme, Click va karta orqali, har qanday holatda."""

    query = select(CreditPurchase).options(selectinload(CreditPurchase.teacher).selectinload(Teacher.user))
    if status != "all":
        query = query.where(CreditPurchase.status == status)
    purchases = db.scalars(query.order_by(CreditPurchase.id.desc()).limit(100)).all()
    return [
        {
            "id": purchase.id,
            "teacher": purchase.teacher.user.full_name,
            "user_id": purchase.teacher.user_id,
            "kind": purchase.kind,
            "months": purchase.months,
            "label": _purchase_label(purchase),
            "credits": purchase.credits,
            "amount": purchase.amount,
            "status": purchase.status,
            "method": purchase.method,
            "created_at": purchase.created_at,
        }
        for purchase in purchases
    ]


ADMIN_BROADCAST_AUDIENCES = {
    "all": "Barcha foydalanuvchilar",
    "teachers": "O'qituvchilar",
    "school_staff": "Muassasa xodimlari",
    "school_leaders": "Muassasa rahbarlari",
}
_admin_broadcast_lock = threading.Lock()
_admin_broadcast_running = False


class AdminBroadcastIn(BaseModel):
    """
    Hammaga e'lon. confirm_count — admin ekranda ko'rgan qabul qiluvchilar soni:
    u hozirgi son bilan mos kelmasa, yuborilmaydi (adashib boshqa guruhga ketmasin).
    """

    text: str = Field(min_length=1, max_length=3500)
    audience: str
    confirm_count: int = Field(ge=0)


def _admin_audience_chat_ids(db: Session, audience: str) -> list[int]:
    if audience == "all":
        query = select(User.tg_id)
    elif audience == "teachers":
        query = select(User.tg_id).join(Teacher, Teacher.user_id == User.id)
    elif audience in ("school_staff", "school_leaders"):
        query = (
            select(User.tg_id)
            .join(SchoolMember, SchoolMember.user_id == User.id)
            .where(SchoolMember.left_at.is_(None))
        )
        if audience == "school_leaders":
            query = query.where(or_(SchoolMember.role == "owner", SchoolMember.position.in_(("director", "deputy"))))
    else:
        raise HTTPException(status_code=422, detail="Bunday guruh yo'q")
    # O'chirilgan hisoblar (tg_id manfiy) — xabar yuborilmaydi
    return list(dict.fromkeys(db.scalars(query.where(User.tg_id > 0).order_by(User.id)).all()))


def _run_admin_broadcast(admin_tg_id: int, chat_ids: list[int], text: str, title: str) -> None:
    """Fonda: xabarni ro'yxatdagilarga birma-bir yuboradi, oxirida adminga hisobot."""

    global _admin_broadcast_running
    delivered = 0
    try:
        for chat_id in chat_ids:
            if _telegram_send_status(chat_id, text)[0] == 200:
                delivered += 1
            time.sleep(BROADCAST_INTERVAL)
        logging.getLogger("backend").info("Admin e'loni (%s): %s/%s", title, delivered, len(chat_ids))
        _telegram_send_message(
            admin_tg_id,
            f"📢 E'lon yuborildi — «{title}»: {delivered} / {len(chat_ids)} kishiga yetdi."
            + (f"\n{len(chat_ids) - delivered} kishi botni bloklagan yoki ishga tushirmagan." if delivered < len(chat_ids) else ""),
        )
    finally:
        with _admin_broadcast_lock:
            _admin_broadcast_running = False


def _start_admin_broadcast(admin_tg_id: int, chat_ids: list[int], text: str, title: str) -> None:
    threading.Thread(
        target=_run_admin_broadcast, args=(admin_tg_id, chat_ids, text, title), name="admin-broadcast", daemon=True
    ).start()


@app.get("/api/admin/broadcast")
def admin_broadcast_audiences(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Har bir guruhda necha kishi bor — admin kimga yuborayotganini aniq ko'rsin."""

    return {
        "running": _admin_broadcast_running,
        "audiences": [
            {"id": key, "label": label, "count": len(_admin_audience_chat_ids(db, key))}
            for key, label in ADMIN_BROADCAST_AUDIENCES.items()
        ],
    }


@app.post("/api/admin/broadcast/test")
def admin_broadcast_test(body: AdminMessageIn, admin: User = Depends(get_current_admin)):
    """E'lonni avval faqat adminning o'ziga yuborib ko'rish — qanday ko'rinishini tekshirish uchun."""

    if _telegram_send_status(admin.tg_id, body.text.strip())[0] != 200:
        raise HTTPException(status_code=502, detail="Xabar yetmadi — botga /start bosganmisiz?")
    return {"ok": True}


@app.post("/api/admin/broadcast")
def admin_broadcast(body: AdminBroadcastIn, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Tanlangan guruhga e'lon. Fonda yuboriladi; bir vaqtda faqat bitta e'lon ketadi."""

    global _admin_broadcast_running
    if body.audience not in ADMIN_BROADCAST_AUDIENCES:
        raise HTTPException(status_code=422, detail="Bunday guruh yo'q")
    if not BOT_TOKEN:
        raise HTTPException(status_code=503, detail="Bot ulanmagan")
    chat_ids = _admin_audience_chat_ids(db, body.audience)
    if not chat_ids:
        raise HTTPException(status_code=409, detail="Bu guruhda hech kim yo'q")
    if len(chat_ids) != body.confirm_count:
        raise HTTPException(
            status_code=409, detail=f"Qabul qiluvchilar soni o'zgardi (endi {len(chat_ids)} ta) — sahifani yangilab, qayta tasdiqlang"
        )
    with _admin_broadcast_lock:
        if _admin_broadcast_running:
            raise HTTPException(status_code=409, detail="Oldingi e'lon hali yuborilmoqda — tugashini kuting")
        _admin_broadcast_running = True
    title = ADMIN_BROADCAST_AUDIENCES[body.audience]
    try:
        _start_admin_broadcast(admin.tg_id, chat_ids, body.text.strip(), title)
    except Exception:
        with _admin_broadcast_lock:
            _admin_broadcast_running = False
        raise
    return {"ok": True, "recipients": len(chat_ids), "seconds": math.ceil(len(chat_ids) * (BROADCAST_INTERVAL + 0.05))}


# Barcha boshqa manzillarni frontend papkasidan beradi (index.html, css, js)
# index.html'dagi versiyali fayllar (havolasiga "?v=..." qo'shiladi; qarang: StaticCacheHeaders)
VERSIONED_ASSETS = ("/js/app.js", "/js/icons.js", "/css/style.css", "/css/theme-pastel.css", "/css/theme-chittak.css",
                    "/css/theme-web.css")


def _asset_version() -> str:
    """
    Fayllar oxirgi marta o'zgargan vaqtdan qisqa "versiya" yasaydi.

    Telegram Mini App eski js/css fayllarni keshlab qoladi, shuning uchun
    index.html ichidagi havolalarga shu versiya qo'shiladi:
    "/js/app.js?v=abc123". Fayl o'zgarsa, havola ham o'zgaradi va
    Telegram yangisini yuklashga majbur bo'ladi.
    """

    latest = 0.0
    for asset in VERSIONED_ASSETS:
        path = FRONTEND_DIR / asset.lstrip("/")
        if path.exists():
            latest = max(latest, path.stat().st_mtime)
    return format(int(latest), "x")


# ========== Ommaviy oferta (Payme/Click arizasi uchun ochiq sahifa) ==========

# Ijrochi (YaTT) rekvizitlari — Render → Environment'da beriladi, kodda saqlanmaydi
LEGAL_NAME = os.getenv("LEGAL_NAME", "Sobirov Suxrobjon Saidjon o'g'li").strip()
LEGAL_PHONE = os.getenv("LEGAL_PHONE", "+998 90 537 07 38").strip()
LEGAL_EMAIL = os.getenv("LEGAL_EMAIL", "").strip()
LEGAL_ADDRESS = os.getenv("LEGAL_ADDRESS", "").strip()
LEGAL_BOT = os.getenv("LEGAL_BOT", "@chittakapp").strip()
# Murojaat uchun — tirik odamlar (bot yozilgan xabarni hech kimga yetkazmaydi, faqat avtomatik javob beradi)
LEGAL_SUPPORT = ", ".join(f"@{name}" for name in SUPPORT_CONTACTS)


WITHDRAWAL_DAYS = 3  # o'qituvchi /pul bilan so'rov yuborgach, pul shuncha ish kunida kartasiga o'tkaziladi
OFERTA_EDITED = "15.11.2026"


def _oferta_html(db: Session) -> str:
    """
    Ommaviy oferta. Bo'limlar saytning HAQIQIY sozlamalariga qarab chiqadi: obuna — admin panelda
    narxi belgilangan bo'lsa, Click — ulangan bo'lsa, karta orqali xarid — karta kiritilgan bo'lsa,
    pullik testda esa ulangan to'lov tizimiga qarab ikki tartibdan biri. Shunda oferta sotilayotgan
    narsadan hech qachon farq qilmaydi.
    """
    from .database import TEST_PRICE_MAX, TEST_PRICE_MIN

    def value(text: str) -> str:
        return html_escape(text) if text else "<span class='blank'>__________</span>"

    tiers = "".join(
        f"<tr><td>{low}{'–' + str(high) if high else '+'} ta</td><td>{_format_sum(price)} so'm</td></tr>"
        for low, high, price in CREDIT_PRICE_TIERS
    )
    commission = _global_commission_percent(db)
    share = 100 - commission
    service = html_escape(LEGAL_BOT) if LEGAL_BOT else "Telegram bot"
    providers = [{"payme": "Payme", "click": "Click"}[name] for name in _enabled_providers()]
    providers_text = " yoki ".join(providers)
    plans = _subscription_plans(db)
    card_topup = _card_topup_enabled()

    sections: list[tuple[str, str]] = []

    sections.append(("Taraflar", f"""
<p><b>Ijrochi:</b> yakka tartibdagi tadbirkor {value(LEGAL_NAME)}.</p>
<p><b>Foydalanuvchi:</b> Chittak xizmatidan ({service} Telegram boti va Mini App, Android ilova yoki chittak.uz sayti
orqali) foydalanayotgan har qanday shaxs — o'qituvchi, ta'lim muassasasi (xususiy maktab, o'quv markazi, to'garak) va uning
xodimlari, o'quvchi yoki uning ota-onasi.</p>"""))

    sections.append(("Xizmatlar", """
<ul>
  <li><b>O'qituvchi uchun:</b> onlayn va titul (javob varaqasi) testlarini yaratish, o'quvchilarga tarqatish, natijalarni
    avtomatik tekshirish (shu jumladan titulni telefon kamerasi bilan skanerlash), Rasch modeli bo'yicha ball, hisobot (PDF)
    va sertifikatlar.</li>
  <li><b>Ta'lim muassasasi uchun (maktab CRM):</b> sinf va guruhlar, o'quvchilar ro'yxati, davomat, dars jadvali, o'qish
    to'lovlari hisobi, e'lonlar va yotoqxona. Bu bo'lim hozirda bepul; pullik bo'lsa, narxi ushbu ofertaga
    oldindan kiritiladi.</li>
  <li><b>O'quvchi uchun:</b> test ishlash, natija va savollar tahlilini ko'rish, maktab o'quvchisi kabineti.</li>
  <li>Xizmat internet mavjud bo'lganda Telegram Mini App, Android ilova va chittak.uz sayti orqali ko'rsatiladi.</li>
</ul>"""))

    sections.append(("Tokenlar va narxlar", f"""
<p>Bepul testda har bir o'quvchi natijasi uchun o'qituvchi hisobidan 1 token yechiladi (oylik obunasi faol bo'lsa —
yechilmaydi). Token narxi (1 dona uchun):</p>
<table>{tiers}</table>
<p>Narx sotib olinadigan tokenlar soniga qarab yuqoridagi jadval bo'yicha hisoblanadi va to'lovdan oldin ko'rsatiladi.
Ro'yxatdan o'tganda yoki do'stni taklif qilganda beriladigan sovg'a tokenlar pulga almashtirilmaydi va ular uchun pul
qaytarilmaydi.</p>"""))

    if plans:
        rows = "".join(
            f"<tr><td>{plan['months']} oy ({plan['days']} kun)</td><td>{_format_sum(plan['price'])} so'm</td></tr>"
            for plan in plans
        )
        sections.append(("Oylik obuna", f"""
<table>{rows}</table>
<ul>
  <li>Obuna davrida bepul testlar natijalari uchun token yechilmaydi — tekshiruvlar soni cheklanmaydi.</li>
  <li>Obuna to'lov tasdiqlangan paytdan boshlanadi. Faol obuna ustiga yana obuna olinsa, yangi muddat qolgan kunlarga qo'shiladi.</li>
  <li>Obuna avtomatik uzaytirilmaydi va kartadan o'z-o'zidan pul yechilmaydi.</li>
</ul>"""))

    if providers:
        paid_tests = f"""
<p>O'qituvchi o'z testiga {_format_sum(TEST_PRICE_MIN)} — {_format_sum(TEST_PRICE_MAX)} so'm oralig'ida narx belgilashi mumkin.
O'quvchi testga {providers_text} orqali to'laydi; to'lov tasdiqlangach test unga avtomatik ochiladi.</p>
<ul>
  <li>To'lov summasining {share}% o'qituvchi balansiga yoziladi, {commission}% — platforma xizmat haqi
    (ayrim mualliflar bilan alohida kelishilgan foiz bo'lsa, o'shasi qo'llanadi va bu haqda muallifga
    oldindan xabar beriladi).</li>
  <li>O'qituvchi balansdagi mablag'ni yechib olish uchun botda /pul buyrug'i orqali so'rov yuboradi. Ijrochi so'rovni
    {WITHDRAWAL_DAYS} ish kuni ichida bajarib, pulni o'qituvchi ko'rsatgan kartaga o'tkazadi.</li>
  <li>O'quvchiga pul qaytarilsa (masalan, to'lov tizimi orqali bekor qilinsa), o'qituvchiga yozilgan ulush balansdan qaytarib olinadi.</li>
</ul>"""
    else:
        paid_tests = f"""
<p>O'qituvchi o'z testiga {_format_sum(TEST_PRICE_MIN)} — {_format_sum(TEST_PRICE_MAX)} so'm oralig'ida narx belgilashi mumkin.</p>
<ul>
  <li>O'quvchi pulni to'g'ridan-to'g'ri o'qituvchi ko'rsatgan kartaga o'tkazadi va chek rasmini ilovada yuboradi.
    O'qituvchi pul tushganini tekshirib tasdiqlagach, test o'quvchiga ochiladi.</li>
  <li>Bu to'lov o'quvchi va o'qituvchi o'rtasida amalga oshiriladi: Ijrochi pulni qabul qilmaydi, ushlab turmaydi va
    undan xizmat haqi olmaydi. To'lov, testni ochish va pulni qaytarish bo'yicha nizolar o'quvchi va o'qituvchi
    o'rtasida hal qilinadi.</li>
</ul>"""
    sections.append(("Pullik testlar", paid_tests))

    payment_items = []
    if providers:
        payment_items.append(f"<li>Token, obuna va pullik testlar uchun to'lovlar {providers_text} to'lov tizimi orqali,"
                             " O'zbekiston so'mida qabul qilinadi. Karta ma'lumotlari faqat to'lov tizimi sahifasida"
                             " kiritiladi; Ijrochi ularni ko'rmaydi va saqlamaydi.</li>")
        payment_items.append("<li>To'lov tasdiqlangach, token, obuna yoki testga ruxsat avtomatik beriladi.</li>")
    if card_topup:
        also = " ham" if providers else ""
        payment_items.append(f"<li>Token va obunani Ijrochi kartasiga pul o'tkazib, chek rasmini ilovada yuborish orqali{also}"
                             " sotib olish mumkin. Ijrochi pul tushganini tekshirib tasdiqlagach, token yoki obuna beriladi;"
                             " chek tasdiqlanmasa, pul to'lovchiga qaytariladi.</li>")
    if not payment_items:
        payment_items.append("<li>Mavjud to'lov usullari ilovada, to'lovdan oldin ko'rsatiladi.</li>")
    sections.append(("To'lov tartibi", f"<ul>{''.join(payment_items)}</ul>"))

    sections.append(("Ta'lim muassasalaridagi o'qish to'lovlari", """
<p>Ta'lim muassasasi o'quvchilardan (ota-onalardan) oladigan o'qish to'lovlari Chittak orqali o'tmaydi. Chittak ularni
faqat hisobga yozish, chek va qarzdorlik ro'yxatini chiqarish vositasi hisoblanadi. To'lov summasi, chegirmalar, qarzdorlik
va pulni qaytarish masalalari ta'lim muassasasi bilan o'quvchi (ota-ona) o'rtasida hal qilinadi; Ijrochi bu to'lovlar
bo'yicha tomon emas va javobgar emas.</p>"""))

    refund_items = [
        "<li>Sotib olingan, lekin ishlatilmagan tokenlar uchun to'lovdan keyin 14 kun ichida murojaat qilinsa, ishlatilmagan"
        " qism uchun pul qaytariladi.</li>",
    ]
    if plans:
        refund_items.append("<li>Oylik obuna uchun to'langan pul qaytarilmaydi — shu jumladan hisob o'chirilganda ham"
                            " (obunaning qolgan kunlari bekor bo'ladi).</li>")
    refund_items.append("<li>Hisob o'chirilsa, undagi tokenlar va obuna bekor bo'ladi va ular uchun pul qaytarilmaydi"
                        " (o'chirishdan oldin ilova buni alohida ogohlantiradi).</li>")
    refund_items += [
        "<li>Texnik nosozlik sababli xizmat ko'rsatilmagan bo'lsa (masalan, to'lov o'tdi, lekin token yozilmadi yoki test"
        " ochilmadi), pul to'liq qaytariladi.</li>",
        "<li>Pul to'lov qilingan kartaga, to'lov tizimi qoidalariga ko'ra 3–10 ish kuni ichida qaytariladi.</li>",
    ]
    sections.append(("Pulni qaytarish", f"<ul>{''.join(refund_items)}</ul>"))

    sections.append(("Shaxsiy ma'lumotlar", """
<p>Ijrochi faqat xizmat uchun zarur ma'lumotlarni saqlaydi va ularni sotmaydi. Qaysi ma'lumotlar saqlanishi, kimga
berilishi va qanday o'chirilishi <a href="/maxfiylik">Maxfiylik siyosati</a>da yozilgan. Hisobni ilovaning o'zida
o'chirish mumkin: <a href="/delete-account">batafsil</a>.</p>"""))

    sections.append(("Javobgarlik", """
<p>Ijrochi Telegram, internet provayderi, xosting yoki to'lov tizimlari ishidagi uzilishlar uchun javobgar emas. Test
savollari va to'g'ri javoblar mazmuni uchun testni yaratgan o'qituvchi, ta'lim muassasasi kiritgan ma'lumotlar (o'quvchilar,
baholar, davomat, to'lovlar) uchun esa o'sha muassasa javobgar.</p>"""))

    sections.append(("Ofertaning o'zgarishi va nizolar", """
<p>Ijrochi ushbu ofertani o'zgartirishi mumkin; yangi tahrir shu sahifada e'lon qilingan paytdan kuchga kiradi. Oldin
to'langan xizmatlar to'lov paytidagi shartlar asosida ko'rsatiladi. Nizolar muzokara yo'li bilan, kelishuvga erishilmasa —
O'zbekiston Respublikasi qonunchiligiga muvofiq hal qilinadi.</p>"""))

    sections.append(("Ijrochi rekvizitlari va aloqa", f"""
<table class="req">
  <tr><td>YaTT</td><td>{value(LEGAL_NAME)}</td></tr>
  {f"<tr><td>Manzil</td><td>{html_escape(LEGAL_ADDRESS)}</td></tr>" if LEGAL_ADDRESS else ""}
  <tr><td>Telefon</td><td>{value(LEGAL_PHONE)}</td></tr>
  {f"<tr><td>E-mail</td><td>{html_escape(LEGAL_EMAIL)}</td></tr>" if LEGAL_EMAIL else ""}
  <tr><td>Murojaat</td><td>{html_escape(LEGAL_SUPPORT)}</td></tr>
  <tr><td>Xizmat</td><td>{service}, chittak.uz</td></tr>
</table>"""))

    body = "\n".join(f"<h2>{number}. {title}</h2>{content}" for number, (title, content) in enumerate(sections, 1))
    return f"""<!doctype html>
<html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ommaviy oferta — Chittak</title>
<style>
  :root {{ color-scheme: light; --ink: #151b3d; --muted: #5d6385; --line: #e3e5f0; --bg: #f6f6fb }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif }}
  main {{ max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; background: #fff; min-height: 100vh; box-sizing: border-box }}
  h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 6px }}
  h2 {{ font-size: 18px; margin: 28px 0 8px }}
  p, li {{ margin: 0 0 8px }}
  .muted {{ color: var(--muted); font-size: 14px }}
  table {{ border-collapse: collapse; width: 100%; max-width: 420px; margin: 8px 0; table-layout: fixed }}
  td {{ border: 1px solid var(--line); padding: 7px 10px; font-variant-numeric: tabular-nums; overflow-wrap: anywhere }}
  .req td:first-child {{ color: var(--muted); width: 40% }}
  .blank {{ color: #c0392b }}
</style></head><body><main>
<h1>Ommaviy oferta — Chittak ta'lim platformasi xizmatlari</h1>
<p class="muted">Ushbu hujjat O'zbekiston Respublikasi Fuqarolik kodeksining 367–370-moddalariga muvofiq ommaviy oferta hisoblanadi.
Xizmatdan foydalanish yoki to'lov qilish — oferta shartlarini to'liq qabul qilish (aksept) demakdir.
Oxirgi tahrir: {OFERTA_EDITED}.</p>
{body}
</main></body></html>"""


@app.get("/oferta", include_in_schema=False)
def oferta_page(db: Session = Depends(get_db)):
    """Ommaviy oferta — Telegram'siz ochiladi (Payme/Click arizasiga havola)."""
    return HTMLResponse(_oferta_html(db))


def _privacy_html() -> str:
    contacts = "".join(
        f"<li>{label}: {html_escape(text)}</li>"
        for label, text in (("Telegram", LEGAL_SUPPORT), ("Telefon", LEGAL_PHONE), ("E-mail", LEGAL_EMAIL))
        if text
    ) or "<li>Telegram bot ichidagi «Murojaat uchun» bo'limi orqali</li>"
    owner = f"<p>Xizmat egasi: {html_escape(LEGAL_NAME)}.</p>" if LEGAL_NAME else ""
    return f"""<!doctype html>
<html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Maxfiylik siyosati — Chittak</title>
<style>
  :root {{ color-scheme: light; --ink: #0b1f2a; --muted: #4f6670; --bg: #f3faf5 }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif }}
  main {{ max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; background: #fff; min-height: 100vh; box-sizing: border-box }}
  h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 6px }}
  h2 {{ font-size: 18px; margin: 28px 0 8px }}
  p, li {{ margin: 0 0 8px }}
  .muted {{ color: var(--muted); font-size: 14px }}
</style></head><body><main>
<h1>Maxfiylik siyosati</h1>
<p class="muted">Chittak — onlayn test va ta'lim muassasalari platformasi (Telegram bot, Mini App, Android ilova va web sayt).</p>
{owner}

<h2>1. Qanday ma'lumotlar saqlanadi</h2>
<ul>
  <li>Telegram akkaunti: ID, ism, username va profil rasmi havolasi.</li>
  <li>Test natijalari, javoblar va xizmat ichida kiritilgan ma'lumotlar (o'quvchilar ro'yxati, davomat, to'lovlar).</li>
  <li>Foydalanuvchi o'zi kiritgan profil ma'lumotlari (masalan, telefon raqami).</li>
</ul>

<h2>2. Ma'lumotlar nima uchun ishlatiladi</h2>
<ul>
  <li>Akkauntga kirish va uni Telegram, ilova hamda web saytda bir xil saqlash uchun.</li>
  <li>Testlarni o'tkazish, natijalarni hisoblash va o'qituvchiga ko'rsatish uchun.</li>
  <li>Natijalar, hisobotlar va bildirishnomalarni Telegram bot orqali yuborish uchun.</li>
</ul>

<h2>3. Ma'lumotlar kimga beriladi</h2>
<p>Ma'lumotlar sotilmaydi va reklama uchun ishlatilmaydi. Ular faqat xizmatni ko'rsatish uchun zarur hollarda beriladi:
to'lov tizimlari (Payme, Click) to'lovni amalga oshirish uchun, hamda qonunchilikda nazarda tutilgan hollarda.</p>
<p>Xizmat ishlashi uchun quyidagi texnik xizmatlardan foydalaniladi:</p>
<ul>
  <li>Server va ma'lumotlar bazasi — Render (Yevropa Ittifoqi, Germaniya); sayt trafigi Cloudflare orqali o'tadi.</li>
  <li>Xabar va fayllar (natija, hisobot, chek) Telegram orqali yetkaziladi.</li>
  <li>O'qituvchi tituldagi qo'lda yozilgan javoblarni avtomatik o'qishni yoqsa, faqat javob kataklarining rasmlari
    sun'iy intellekt xizmatiga (Google Gemini yoki Anthropic Claude) yuboriladi. O'quvchining ismi, sinfi va ID raqami yuborilmaydi.</li>
</ul>

<h2>4. Saqlash va himoya</h2>
<p>Ma'lumotlar himoyalangan serverda saqlanadi. Kirish seanslarining o'zi emas, faqat ularning shifrlangan izi saqlanadi.
Brauzerda faqat kirish seansi tokeni saqlanadi; reklama yoki kuzatuv cookie'lari ishlatilmaydi.</p>
<p>Testlar yaratilganidan 10 kun o'tib avtomatik yopiladi va o'chiriladi — undan oldin
natijalar arxivi o'qituvchiga Telegram orqali yuboriladi.</p>

<h2>5. O'quvchilar va voyaga yetmaganlar</h2>
<p>O'quvchilar ma'lumotlarini (ism, sinf, natijalar, davomat) ta'lim muassasasi yoki o'qituvchi kiritadi va ular faqat
o'quv jarayoni uchun ishlatiladi. Voyaga yetmagan o'quvchining ma'lumotlarini ko'rish, tuzatish yoki o'chirish uchun
ota-onasi (qonuniy vakili) o'quvchining muassasasiga yoki quyidagi aloqa orqali bizga murojaat qilishi mumkin.</p>

<h2>6. Ma'lumotlarni o'chirish</h2>
<p>Hisobingizni istalgan payt o'zingiz o'chirishingiz mumkin: ilovada «Akkaunt» → «Hisobni o'chirish».
Batafsil: <a href="/delete-account">akkauntni o'chirish tartibi</a>. Ilovaga kira olmasangiz — quyidagi aloqa orqali murojaat qiling.
To'lovlar bo'yicha yozuvlar qonunchilik talab qilgan muddat davomida saqlanishi mumkin.</p>

<h2>7. Aloqa</h2>
<ul>{contacts}</ul>
</main></body></html>"""


@app.get("/maxfiylik", include_in_schema=False)
@app.get("/privacy", include_in_schema=False)
def privacy_page():
    """Maxfiylik siyosati — kirishsiz ochiladigan ommaviy sahifa."""
    return HTMLResponse(_privacy_html())


def _account_deletion_html() -> str:
    contacts = "".join(
        f"<li>{label}: <b>{html_escape(text)}</b></li>"
        for label, text in (("Telegram", LEGAL_SUPPORT), ("Telefon", LEGAL_PHONE), ("E-mail", LEGAL_EMAIL))
        if text
    )
    return f"""<!doctype html>
<html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Akkauntni o'chirish — Chittak</title>
<style>
  :root {{ color-scheme: light; --ink: #0b1f2a; --muted: #4f6670; --bg: #f3faf5; --line: #ddefe2 }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif }}
  main {{ max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; background: #fff; min-height: 100vh; box-sizing: border-box }}
  h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 6px }}
  h2 {{ font-size: 18px; margin: 28px 0 8px }}
  p, li {{ margin: 0 0 8px }}
  .muted {{ color: var(--muted); font-size: 14px }}
  ol.steps {{ padding-left: 22px }}
  .note {{ padding: 12px 14px; border: 1px solid var(--line); border-radius: 12px; background: var(--bg) }}
</style></head><body><main>
<h1>Chittak — akkaunt va ma'lumotlarni o'chirish</h1>
<p class="muted">Chittak ilovasi (Android, Telegram Mini App va chittak.uz sayti) foydalanuvchilari uchun.
Ishlab chiquvchi: {html_escape(LEGAL_NAME)}.</p>

<h2>Akkauntni o'zingiz o'chirishingiz mumkin</h2>
<ol class="steps">
  <li>Chittak'ni oching (ilova, Telegram Mini App yoki chittak.uz) va hisobingizga kiring.</li>
  <li>«Akkaunt» bo'limini oching va pastdagi <b>«Hisobni o'chirish»</b> tugmasini bosing.</li>
  <li>Tasdiqlash so'zini yozing — hisob shu zahoti o'chiriladi.</li>
</ol>

<h2>Ilovaga kira olmasangiz — so'rov yuboring</h2>
<ol class="steps">
  <li>Quyidagi aloqa usullaridan biri orqali bizga yozing:
    <ul>{contacts}</ul>
  </li>
  <li>Xabarda <b>«Akkauntimni o'chiring»</b> deb yozing va Chittak'dagi ismingizni yoki Telegram username'ingizni ko'rsating.</li>
  <li>Akkaunt sizniki ekanini tekshirish uchun xabarni o'sha Telegram akkauntdan yuboring (yoki biz so'ragan tasdiqni bering).</li>
  <li>So'rov <b>30 kun ichida</b> bajariladi va bu haqda sizga javob yozamiz.</li>
</ol>

<h2>Qaysi ma'lumotlar o'chiriladi</h2>
<ul>
  <li>Profil: ism, Telegram username, profil rasmi havolasi, telefon raqami.</li>
  <li>Yaratgan testlaringiz, tokenlaringiz va obunangiz.</li>
  <li>Topshirgan testlaringiz natijalari ismingizdan ajratiladi (ism o'chgach, ular kimniki ekanini bilib bo'lmaydi).</li>
  <li>Kirish seanslari (ilova va saytdan avtomatik chiqasiz).</li>
</ul>

<h2>Qaysi ma'lumotlar saqlanib qoladi</h2>
<ul>
  <li>To'lovlar va pul yechish bo'yicha yozuvlar (summa, sana, to'lov tizimi) — O'zbekiston Respublikasi qonunchiligi
    talab qilgan muddat davomida saqlanadi, so'ngra o'chiriladi.</li>
</ul>

<p class="note">Akkauntni o'chirmasdan faqat ayrim ma'lumotlarni (masalan, telefon raqamini yoki ma'lum test natijalarini)
o'chirishni ham xuddi shu aloqa orqali so'rashingiz mumkin.</p>

<p class="muted">Batafsil: <a href="/maxfiylik">Maxfiylik siyosati</a>.</p>
</main></body></html>"""


def _founder_html() -> str:
    # Google odam haqidagi ma'lumotni shu sahifadan va undagi Person tuzilgan ma'lumotidan oladi.
    # Tug'ilgan sana va yashash joyi ataylab yozilmaydi — ommaviy sahifada shaxsiy ma'lumot bo'lmasin.
    person = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Sobirov Suxrobjon Saidjon o'g'li",
        "givenName": "Suxrobjon",
        "familyName": "Sobirov",
        "alternateName": ["Suxrobjon Sobirov", "Sobirov Suxrobjon", "Sukhrobjon Sobirov", "Сухробжон Собиров", "Собиров Сухробжон"],
        "nationality": {"@type": "Country", "name": "O'zbekiston"},
        "jobTitle": "Chittak.uz platformasi asoschisi",
        "url": "https://www.chittak.uz/asoschi",
        "worksFor": {"@type": "Organization", "name": "Chittak", "url": "https://www.chittak.uz/"},
    }
    return f"""<!doctype html>
<html lang="uz"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sobirov Suxrobjon Saidjon o'g'li — Chittak.uz asoschisi</title>
<meta name="description" content="Sobirov Suxrobjon (Suxrobjon Sobirov, Собиров Сухробжон) Saidjon o'g'li — Chittak.uz ta'lim platformasi asoschisi.">
<link rel="canonical" href="https://www.chittak.uz/asoschi">
<meta property="og:type" content="profile">
<meta property="og:title" content="Sobirov Suxrobjon — Chittak.uz asoschisi">
<meta property="og:url" content="https://www.chittak.uz/asoschi">
<script type="application/ld+json">{json.dumps(person, ensure_ascii=False)}</script>
<style>
  :root {{ color-scheme: light; --ink: #0b1f2a; --muted: #4f6670; --bg: #f3faf5; --line: #ddefe2 }}
  body {{ margin: 0; background: var(--bg); color: var(--ink); font: 16px/1.6 -apple-system, "Segoe UI", Roboto, sans-serif }}
  main {{ max-width: 760px; margin: 0 auto; padding: 28px 18px 60px; background: #fff; min-height: 100vh; box-sizing: border-box }}
  h1 {{ font-size: 26px; line-height: 1.25; margin: 0 0 6px }}
  h2 {{ font-size: 18px; margin: 28px 0 8px }}
  p, li {{ margin: 0 0 8px }}
  .muted {{ color: var(--muted); font-size: 14px }}
  dl {{ display: grid; grid-template-columns: max-content 1fr; gap: 6px 16px; margin: 0 }}
  dt {{ color: var(--muted) }}
  dd {{ margin: 0; font-weight: 600 }}
</style></head><body><main>
<h1>Sobirov Suxrobjon Saidjon o'g'li</h1>
<p class="muted"><a href="/">Chittak.uz</a> platformasi asoschisi</p>
<p class="muted">Boshqa yozilishlari: Suxrobjon Sobirov, Sukhrobjon Sobirov, Собиров Сухробжон, Сухробжон Собиров</p>

<p>Sobirov Suxrobjon Saidjon o'g'li — maktablar, o'quv markazlari va repetitorlar uchun yaratilgan
Chittak.uz ta'lim platformasining asoschisi. Platformada onlayn testlar, titul (javob varaqasi) skaneri,
Rasch modeli bo'yicha natijalar tahlili va maktab CRM bitta joyda jamlangan.</p>

<h2>Ma'lumot</h2>
<dl>
  <dt>Ism-familiyasi</dt><dd>Sobirov Suxrobjon Saidjon o'g'li</dd>
  <dt>Faoliyati</dt><dd>Chittak.uz platformasi asoschisi</dd>
</dl>

<h2>Chittak.uz haqida</h2>
<p>Chittak — ilg'or ta'lim, tezkor natija. Batafsil: <a href="https://www.chittak.uz/">www.chittak.uz</a></p>
</main></body></html>"""


@app.get("/asoschi", include_in_schema=False)
def founder_page():
    """Platforma asoschisi haqida ommaviy sahifa (Google qidiruvi uchun)."""
    return HTMLResponse(_founder_html())


@app.get("/hisobni-ochirish", include_in_schema=False)
@app.get("/delete-account", include_in_schema=False)
def account_deletion_page():
    """Akkauntni o'chirish tartibi — Google Play talab qiladigan ommaviy sahifa."""
    return HTMLResponse(_account_deletion_html())


@app.get("/", include_in_schema=False)
def serve_index(request: Request):
    """Bosh sahifa: har safar yangi tekshiriladi va fayl havolalari versiyalanadi."""

    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    # Havola ulashilganda chiqadigan rasm (og:image) — Telegram to'liq manzil talab qiladi.
    # Render HTTPS'ni proksi orqali beradi, shuning uchun sxema X-Forwarded-Proto'dan olinadi.
    scheme = request.headers.get("x-forwarded-proto", request.url.scheme).split(",")[0].strip()
    origin = f"{scheme}://{request.url.netloc}"
    html = html.replace('content="/img/og-image.jpg"', f'content="{origin}/img/og-image.jpg"')
    version = _asset_version()
    for asset in VERSIONED_ASSETS:
        html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')

    # Fanlar pasporti (backend/subjects.py) — ilova test shablonini va klaviaturani shundan oladi
    subjects_json = json.dumps(frontend_config(), ensure_ascii=False).replace("</", "<\\/")
    html = html.replace(
        f'<script src="/js/app.js?v={version}">',
        f'<script>window.SUBJECTS_CONFIG = {subjects_json};</script>\n  <script src="/js/app.js?v={version}">',
    )

    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
