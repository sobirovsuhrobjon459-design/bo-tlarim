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
import hmac
import io
import json
import logging
import os
import random
import re
import secrets
import threading
import time
from types import SimpleNamespace
from contextlib import asynccontextmanager
from xml.sax.saxutils import escape as xml_escape
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.graphics.shapes import Drawing, Line, Rect
from reportlab.graphics.shapes import String as ChartString
from reportlab.lib.styles import ParagraphStyle
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import HRFlowable, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy.exc import IntegrityError
from sqlalchemy import String, case, cast, delete, distinct, func, inspect, or_, select, text
from sqlalchemy.orm import Session, selectinload

from .rasch import grade_for, rasch_scores
from .regions import REGIONS, is_valid_location
from .student_import import names_from_rows, parse_student_file, rows_from_text
from .auth import (
    get_current_admin,
    get_current_teacher,
    get_current_user,
    get_db,
)
from .database import (
    ADMIN_CARD_NUMBER,
    ADMIN_ID,
    BOT_TOKEN,
    PAYME_MERCHANT_ID,
    PAYME_SECRET_KEY,
    CLICK_MERCHANT_ID,
    CLICK_SECRET_KEY,
    CLICK_SERVICE_ID,
    PLATFORM_COMMISSION_PERCENT,
    WELCOME_CREDITS,
    Attempt,
    Attendance,
    AttendanceIn,
    AttendanceMark,
    Base,
    ClassIn,
    ClassStudent,
    ClassTest,
    ClassTestIn,
    ClassTestScore,
    DiscountIn,
    FeeIn,
    PaymentCancelIn,
    PaymentIn,
    PromoCode,
    PromoCreateIn,
    PromoRedeemIn,
    PromoRedemption,
    TuitionPayment,
    CuratorIn,
    CreditPurchase,
    CardTopUpIn,
    CreditPurchaseIn,
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
    SchoolMember,
    SchoolClass,
    SchoolInvite,
    SchoolProfileIn,
    StudentImportIn,
    StudentIn,
    StudentsBulkIn,
    SessionLocal,
    StudentAnswer,
    StudentAnswersIn,
    StudentNamesIn,
    Teacher,
    TeacherRegisterIn,
    Test,
    TestIn,
    User,
    WithdrawalRequest,
    WithdrawalRequestIn,
    engine,
    utcnow,
)
from bot.bot import run_bot

# ========== 1) Ilova sozlash ==========

Base.metadata.create_all(engine)


# Ustun BIRINCHI marta qo'shilganda bir martalik to'ldirish: ilgari sinfni
# o'qituvchining o'zi yaratib boshqarardi — endi u o'sha sinfning kuratori.
COLUMN_BACKFILLS = {
    ("school_classes", "curator_user_id"): "UPDATE school_classes SET curator_user_id = teacher_user_id",
}


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
            "status": "VARCHAR(10) DEFAULT 'active'",
            "required_channel": "VARCHAR(100)",
            "results_sent_at": "TIMESTAMP",
            "deleted_at": "TIMESTAMP",
        },
        "test_attempts": {"offline_name": "VARCHAR(200)", "saved_answers": "TEXT"},
        "users": {"username": "VARCHAR(64)", "photo_url": "VARCHAR(500)"},
        "school_classes": {"curator_user_id": "INTEGER"},
        "private_schools": {"monthly_fee": "INTEGER DEFAULT 0"},
        "class_students": {"discount_percent": "INTEGER DEFAULT 0"},
        "promo_codes": {"per_teacher_limit": "INTEGER DEFAULT 3"},
        "school_members": {"position": "VARCHAR(20)", "phone": "VARCHAR(20)", "birth_date": "DATE", "passport": "VARCHAR(20)"},
        "attendances": {
            "lesson_start": "VARCHAR(5)",
            "lesson_end": "VARCHAR(5)",
            "deleted_at": "TIMESTAMP",
            "deleted_by": "INTEGER",
        },
        "key_questions": {"label": "VARCHAR(20)"},
        "payments": {
            "payme_transaction_id": "VARCHAR(50)",
            "performed_at": "TIMESTAMP",
            "cancelled_at": "TIMESTAMP",
            "click_trans_id": "VARCHAR(50)",
        },
        "credit_purchases": {
            "method": "VARCHAR(10) DEFAULT 'payme'",
            "receipt_file_id": "VARCHAR(200)",
            "click_trans_id": "VARCHAR(50)",
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server ishga tushganda botni va kunlik zaxira nusxa vazifasini ishga tushiradi."""
    _warn_if_storage_is_temporary()
    bot_task = asyncio.create_task(run_bot())
    backup_task = asyncio.create_task(_daily_backup_loop())
    finish_task = asyncio.create_task(_finish_expired_tests_loop())
    awake_task = asyncio.create_task(_keep_awake_loop())
    yield
    bot_task.cancel()
    backup_task.cancel()
    finish_task.cancel()
    awake_task.cancel()


app = FastAPI(title="Test Platformasi API", lifespan=lifespan)
# JS/CSS fayllar siqilib yuboriladi (~320 KB → ~70 KB) — ilova tezroq ochiladi
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.get("/api/health")
def health():
    """Server tirikligini tekshirish (uyg'oq tutish uchun ham)."""
    return {"ok": True}


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


# ========== 1.1) Zaxira nusxa (backup) ==========
#
# Ma'lumotlar PostgreSQL'da saqlanadi va deploy qilinganda yo'qolmaydi.
# Lekin bazaning o'zi bilan nimadir bo'lib qolsa (xizmat o'chirilsa, adashib
# o'chirib yuborilsa) deb, butun bazaning nusxasi JSON fayl ko'rinishida
# admin'ning Telegram chatiga yuboriladi: har kuni avtomatik va istalgan
# paytda admin panelidagi tugma orqali.


def export_database(db: Session) -> bytes:
    """Butun bazani bitta JSON faylga aylantiradi."""

    export: dict[str, list[dict]] = {
        "exported_at": utcnow().isoformat(),
        "tables": {},
    }

    for table in Base.metadata.sorted_tables:
        rows = db.execute(select(table)).mappings().all()
        export["tables"][table.name] = [dict(row) for row in rows]

    # default=str — sana/vaqt qiymatlarini matnga aylantiradi
    return json.dumps(export, ensure_ascii=False, indent=1, default=str).encode("utf-8")


def send_backup_to_admin(db: Session) -> bool:
    """Zaxira nusxani admin'ning Telegram chatiga yuboradi. Xato bo'lsa, ilovani to'xtatmaydi."""

    if not ADMIN_ID:
        return False

    try:
        backup_bytes = export_database(db)
        row_count = sum(len(rows) for rows in json.loads(backup_bytes)["tables"].values())

        _send_pdf_to_telegram(
            ADMIN_ID,
            f"backup_{datetime.now():%Y-%m-%d_%H-%M}.json",
            backup_bytes,
            caption=(
                "💾 Zaxira nusxa\n\n"
                f"Sana: {datetime.now():%d.%m.%Y %H:%M}\n"
                f"Jami yozuvlar: {row_count} ta\n\n"
                "Bu faylni saqlab qo'ying — baza bilan nimadir bo'lsa, hammasi shundan tiklanadi."
            ),
        )
        return True
    except Exception:
        logging.getLogger("backend").warning("Zaxira nusxa yuborilmadi", exc_info=True)
        return False


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
            test.status = "closed"
            send_results_to_teacher(session, test)
        session.commit()
    finally:
        session.close()


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
        "o'qituvchilar, kreditlar, to'lovlar va ballar yo'qoladi.\n\n"
        "Render → Environment bo'limida DATABASE_URL ga doimiy PostgreSQL manzilini qo'ying."
    )
    logging.getLogger("backend").error(message)
    if ADMIN_ID and BOT_TOKEN:
        try:
            httpx.post(f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
                       json={"chat_id": ADMIN_ID, "text": message}, timeout=10)
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


def _send_backup_once() -> None:
    session = SessionLocal()
    try:
        send_backup_to_admin(session)
    finally:
        session.close()


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
    """Savolning ko'rinadigan nomi: odatda "12-savol", a/b qismli bo'lsa — "36a"."""
    return key_question.label or f"{key_question.number}-savol"


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
        match = re.fullmatch(r"(\d+)([a-z]+)", label)
        items.append((key_question.number, f"{match.group(1)}({match.group(2)})" if match else label))
    return items


def _school_grade(percent: float) -> str:
    """Bepul testlar uchun oddiy 5 ballik baho: 86%+ — 5, 71%+ — 4, 56%+ — 3, qolgani — 2."""
    if percent >= 86:
        return "5"
    if percent >= 71:
        return "4"
    if percent >= 56:
        return "3"
    return "2"


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
    # Rasch (Milliy sertifikat uslubidagi ball) — faqat PULLIK testlarda.
    # Bepul testda — oddiy natija: to'g'ri javoblar, foiz va 5 ballik baho.
    uses_rasch = (test.price or 0) > 0

    correct_refs: dict[int, set[int]] = {attempt.id: set() for attempt, _ in results}
    if correct_refs:
        for attempt_id, question_ref in db.execute(
            select(StudentAnswer.attempt_id, StudentAnswer.question_ref).where(
                StudentAnswer.attempt_id.in_(list(correct_refs)), StudentAnswer.is_correct.is_(True)
            )
        ).all():
            correct_refs[attempt_id].add(question_ref)

    matrix = [[1 if ref in correct_refs[attempt.id] else 0 for ref, _ in items] for attempt, _ in results]
    scores = rasch_scores(matrix)["scores"] if uses_rasch else [None] * len(results)

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
            "grade": grade_for(score) if uses_rasch else _school_grade(attempt.percent),
        }
        for (attempt, name), score in zip(results, scores)
    ]

    # O'rin: pullikda — Rasch balli, bepulda — to'g'ri javoblar/foiz; teng natija — teng o'rin (1, 1, 3 ...)
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

    top_count = sum(1 for row in rows if (row["ball"] >= 65 if uses_rasch else row["grade"] == "5"))
    best = rows[0] if rows else None

    return {
        "rows": rows,
        "items": item_stats,
        "uses_rasch": uses_rasch,
        "summary": {
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

REPORT_BLUE = colors.HexColor("#1f4e79")
REPORT_TILE_BG = colors.HexColor("#f0f3f7")
REPORT_MUTED = colors.HexColor("#6b7280")
REPORT_GRID = colors.HexColor("#e5e7eb")
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
        HRFlowable(width="100%", thickness=1.4, color=REPORT_BLUE, spaceBefore=3, spaceAfter=10),
    ]


def _stat_tiles(summary: dict, styles: dict, width: float):
    if summary.get("uses_rasch"):
        tiles = [
            ("ISHTIROKCHILAR", f"{summary['participants']}", ""),
            ("O'RTACHA BALL", f"{summary['avg_ball']:.2f}", f"o'rtacha foiz {summary['avg_percent']}%"),
            ("ENG YUQORI BALL", f"{summary['max_ball']:.2f}", summary["max_name"]),
            ("A+ VA A", f"{summary['top_count']} ta", f"ishtirokchilarning {summary['top_share']}%"),
        ]
    else:
        tiles = [
            ("ISHTIROKCHILAR", f"{summary['participants']}", ""),
            ("O'RTACHA FOIZ", f"{summary['avg_percent']}%", ""),
            ("ENG YUQORI", f"{summary['max_percent']}%", summary["max_name"]),
            ("A'LO (5)", f"{summary['top_count']} ta", f"ishtirokchilarning {summary['top_share']}%"),
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


def _results_table(rows: list[dict], styles: dict, width: float, uses_rasch: bool = True) -> Table:
    header = ["O'rin", "F.I.Sh.", "To'g'ri", "Xato", "Foiz", "Ball", "Daraja"] if uses_rasch else \
        ["O'rin", "F.I.Sh.", "To'g'ri", "Xato", "Foiz", "Baho"]
    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in header]]
    for row in rows:
        data.append(
            [
                Paragraph(str(row["rank"]), styles["cell_bold"]),
                Paragraph(xml_escape(row["name"]), styles["cell"]),
                Paragraph(f"{row['correct']}/{row['total']}", styles["cell"]),
                Paragraph(str(row["wrong"]), styles["cell"]),
                Paragraph(f"{row['percent']}%", styles["cell"]),
                *([Paragraph(f"{row['ball']:.2f}", styles["cell_bold"])] if uses_rasch else []),
                Paragraph(xml_escape(row["grade"]), styles["cell_bold"]),
            ]
        )

    column_widths = [0.08, 0.38, 0.11, 0.08, 0.11, 0.12, 0.12] if uses_rasch else [0.09, 0.44, 0.12, 0.09, 0.12, 0.14]
    table = Table(data, colWidths=[width * part for part in column_widths], repeatRows=1)
    commands = [
        ("BACKGROUND", (0, 0), (-1, 0), REPORT_BLUE),
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


def generate_test_report_pdf(db: Session, test: Test, teacher: Teacher) -> bytes:
    """
    Test natijalari hisoboti (PDF): umumiy statistika, savollar bo'yicha
    to'g'ri javoblar ulushi, eng qiyin/oson savollar va o'rinlar jadvali.
    Pullik testda — Rasch balli (Milliy sertifikat uslubi), bepulda — oddiy natija.
    """

    report = build_rasch_report(db, test)
    styles = _report_styles()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=36, rightMargin=36, topMargin=34, bottomMargin=34,
                                 title=f"{test.title} — natijalar")
    width = document.width

    status = "yakunlangan" if test.status == "closed" else "davom etmoqda"
    elements = [
        Paragraph(xml_escape(test.title), styles["title"]),
        Paragraph(
            xml_escape(
                f"Fan: {test.subject}  ·  Test kodi: {test.code}  ·  O'qituvchi: {teacher.user.full_name}  ·  "
                f"Sana: {datetime.now():%d.%m.%Y}  ·  Test {status}"
            ),
            styles["meta"],
        ),
        *_section_heading("Umumiy statistika", styles),
        _stat_tiles(report["summary"], styles, width),
        *_section_heading("Savollar bo'yicha to'g'ri javoblar ulushi", styles),
        _share_chart(report["items"], width),
        Spacer(1, 4),
        _share_legend(width),
        Spacer(1, 16),
    ]

    answered_items = [item for item in report["items"]]
    hardest = sorted(answered_items, key=lambda item: (item["share"], item["label"]))[:8]
    easiest = sorted(answered_items, key=lambda item: (-item["share"], item["label"]))[:8]

    half = (width - 24) / 2
    lists = Table(
        [[_question_list("Eng qiyin savollar", hardest, styles["hard"], styles, half), "",
          _question_list("Eng oson savollar", easiest, styles["easy"], styles, half)]],
        colWidths=[half, 24, half],
    )
    lists.setStyle(TableStyle([("VALIGN", (0, 0), (-1, -1), "TOP"), ("LEFTPADDING", (0, 0), (-1, -1), 0),
                               ("RIGHTPADDING", (0, 0), (-1, -1), 0)]))
    elements.append(lists)

    elements.append(PageBreak())
    uses_rasch = report["uses_rasch"]
    elements += _section_heading(
        "Natijalar (Rasch balli bo'yicha o'rinlar)" if uses_rasch else "Natijalar (to'g'ri javoblar bo'yicha o'rinlar)", styles
    )
    if report["rows"]:
        elements.append(_results_table(report["rows"], styles, width, uses_rasch))
    else:
        elements.append(Paragraph("Hali hech kim testni yakunlamagan.", styles["cell"]))

    footnote = (
        "Ball Rasch modeli bo'yicha hisoblangan (Milliy sertifikat uslubida, 75 ballik shkala): qiyin savol ko'proq "
        "ball beradi, o'rtacha ball ~50. Darajalar: A+ 70+, A 65+, B+ 60+, B 55+, C+ 50+, C 46+."
        if uses_rasch
        else "Baho to'g'ri javoblar foizi bo'yicha: 86% va yuqori — 5, 71% — 4, 56% — 3, qolgani — 2."
    )
    elements += [Spacer(1, 10), Paragraph(xml_escape(footnote), styles["meta"])]

    document.build(elements)
    return buffer.getvalue()


# Test kodi qisqa va yozishga oson bo'lsin: 4 ta belgi.
# Chalkashadigan harf-raqamlar olib tashlangan (O va 0, I va 1, L va 1),
# shuning uchun o'quvchi kodni noto'g'ri yozib yubormaydi.
TEST_CODE_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TEST_CODE_LENGTH = 4

# Javoblar varaqasining o'lchamlari (punktlarda)
SHEET_MARGIN = 40
SHEET_BOTTOM_LIMIT = 56

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


def _answer_sheet_items(test: Test) -> list[tuple[str, str, int, str]]:
    """Javoblar varaqasi uchun: har bir savolning (nomi, turi, variantlar soni, to'g'ri javobi)."""

    if test.mode == "key":
        return [
            (kq.label or str(kq.number), kq.question_type, kq.option_count, kq.correct) for kq in test.key_questions
        ]
    return [(str(index), "single", 4, question.correct) for index, question in enumerate(test.questions, start=1)]


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


def _draw_corner_marks(sheet, width: float, height: float) -> None:
    """Burchaklardagi qora nuqtalar — varaqni rasmga olganda chetlarini aniqlash uchun."""
    for x, y in [(32, height - 32), (width - 32, height - 32), (32, 32), (width - 32, 32)]:
        sheet.circle(x, y, 7, stroke=0, fill=1)


def _draw_sheet_header(sheet, test: Test, width: float, height: float, with_answers: bool) -> float:
    """
    Varaq sarlavhasini chizadi va savollar boshlanadigan y koordinatasini qaytaradi.

    Sarlavha ixcham: savollarga ko'proq joy qolishi uchun (hammasi bitta
    varaqqa sig'ishi kerak) test ma'lumotlari bitta qatorga joylashtiriladi.
    """

    _draw_corner_marks(sheet, width, height)

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
        sheet.drawCentredString(width / 2, height - 88, "JAVOBLAR KALITI")
        return height - 104

    # O'quvchi o'z ismini yozadigan maydon
    name_y = height - 100
    sheet.setFont("Helvetica", 9)
    sheet.drawString(SHEET_MARGIN, name_y + 6, "Ism va familiya:")
    sheet.rect(SHEET_MARGIN + 80, name_y, width - SHEET_MARGIN * 2 - 80, 20)

    return name_y - 16


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


def _draw_sheet_column(sheet, column_cells, column_x, column_width, top_y, layout, with_answers) -> None:
    """Bitta ustundagi sarlavha va savol qatorlarini chizadi."""

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

        label, question_type, options, correct = cell["item"]
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

        y -= row_height


def generate_answer_sheet_pdf(test: Test, with_answers: bool = False) -> bytes:
    """
    "Javoblar varaqasi" — o'quvchilarga tarqatiladigan bo'sh blank.

    O'quvchi javoblarini shu varaqada belgilaydi, o'qituvchi esa ularni
    Mini App'dagi "O'quvchilar" bo'limiga kiritadi va bot avtomatik tekshiradi.

    Hamma savol iloji boricha bitta varaqqa joylashtiriladi (qarang: SHEET_LAYOUTS).

    with_answers=True bo'lsa, to'g'ri javoblar bo'yalgan "kalit" varaqasi
    chiqadi — o'qituvchi qo'lda tekshirishi uchun.
    """

    buffer = io.BytesIO()
    sheet = pdf_canvas.Canvas(buffer, pagesize=A4)
    width, height = A4

    # Har bir guruh: sarlavha katagi + savol kataklari
    cells: list[dict] = []
    for group in _group_similar_questions(_answer_sheet_items(test)):
        cells.append({"kind": "header", "group": group})
        cells.extend({"kind": "row", "item": item} for item in group)

    top_y = _draw_sheet_header(sheet, test, width, height, with_answers)
    available_height = top_y - SHEET_BOTTOM_LIMIT

    layout, columns = _choose_sheet_layout(cells, available_height)
    columns_per_page = layout[0]
    column_width = (width - SHEET_MARGIN * 2) / columns_per_page

    for index, column_cells in enumerate(columns):
        position = index % columns_per_page

        if index and position == 0:
            # Sig'magan ustunlar keyingi sahifaga o'tadi
            sheet.showPage()
            _draw_corner_marks(sheet, width, height)
            top_y = height - 70

        _draw_sheet_column(
            sheet,
            column_cells,
            SHEET_MARGIN + position * column_width,
            column_width,
            top_y,
            layout,
            with_answers,
        )

    if not with_answers:
        sheet.setFont("Helvetica", 8)
        sheet.drawCentredString(width / 2, 40, "To'g'ri javob doirachasini to'liq bo'yang.")

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


def _get_test_by_code(db: Session, code: str) -> Test:
    test = db.scalar(select(Test).where(Test.code == code.strip().upper(), Test.deleted_at.is_(None)))
    if not test:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


def _is_paid(db: Session, user: User, test: Test) -> bool:
    if test.price == 0:
        return True
    payment = db.scalar(
        select(Payment.id).where(Payment.user_id == user.id, Payment.test_id == test.id, Payment.status == "paid")
    )
    return payment is not None


# Kredit narxi qancha ko'p sotib olinsa, shuncha arzonlashadi (optom chegirma).
# Har bir qator: (eng kami, eng ko'pi yoki None=cheksiz, 1 dona kredit narxi so'mda)
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


def _teacher_share(amount: int) -> int:
    """O'quvchi to'lagan summadan o'qituvchiga tegadigan qismi (qolgani platformada qoladi)."""
    return round(amount * (100 - PLATFORM_COMMISSION_PERCENT) / 100)


def _calculate_credit_price(credit_count: int) -> int:
    """Kredit sonining jami narxini optom chegirma jadvali bo'yicha hisoblaydi."""
    for low, high, price_per_credit in CREDIT_PRICE_TIERS:
        if credit_count >= low and (high is None or credit_count <= high):
            return credit_count * price_per_credit
    return credit_count * CREDIT_PRICE_TIERS[0][2]


def _build_payme_checkout_url(account_field: str, order_id: int, amount: int) -> str:
    """
    Payme'ning hosted to'lov sahifasiga olib boruvchi havolani yaratadi (amount — so'mda).

    account_field — Payme buyurtmani qaysi nom bilan qaytarishi:
      "purchase_id" — o'qituvchining kredit xaridi (CreditPurchase)
      "payment_id"  — o'quvchining pullik testga to'lovi (Payment)
    """

    # Payme summani tiyinda kutadi
    params = f"m={PAYME_MERCHANT_ID};ac.{account_field}={order_id};a={amount * 100}"
    encoded = base64.b64encode(params.encode()).decode()
    return f"https://checkout.paycom.uz/{encoded}"


def _notify_admin_about_withdrawal(request: WithdrawalRequest, teacher: Teacher) -> None:
    """Admin'ga Telegram orqali, tugmali xabar yuboradi — shu yerdan to'g'ridan-to'g'ri tasdiqlash/rad etish mumkin."""

    if not ADMIN_ID:
        return

    try:
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": ADMIN_ID,
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


def _send_pdf_to_telegram(chat_id: int, file_name: str, pdf_bytes: bytes, caption: str = "") -> None:
    """Tayyor PDF'ni bot orqali foydalanuvchining shaxsiy chatiga yuboradi."""

    response = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
        data={"chat_id": chat_id, "caption": caption} if caption else {"chat_id": chat_id},
        files={"document": (file_name, pdf_bytes, "application/pdf")},
        timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="PDF yuborilmadi (botga /start bosganmisiz?)")


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

    if not DEFAULT_REQUIRED_CHANNELS or not BOT_TOKEN or BOT_TOKEN == "your_bot_token_here":
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
            status_code=403, detail="Bu yopiq test — javoblarni o'qituvchining o'zi kiritadi"
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


def _strip_simple_parens(text: str) -> str:
    """Ortiqcha qavslarni olib tashlaydi (ichma-ich bo'lsa ham)."""
    for _ in range(10):
        new_text = _SIMPLE_PARENS_PATTERN.sub(r"\1", text)
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

    return _IMPLICIT_MULTIPLY_PATTERN.sub("", text)


def _grade_answers(db: Session, attempt: Attempt, test: Test, answers: dict[str, str]) -> int:
    """
    Javoblarni tekshirib, har biri uchun StudentAnswer yozuvini yaratadi.
    Savollarning umumiy sonini qaytaradi.

    Ikki joyda ishlatiladi: o'quvchi testni o'zi yakunlaganda, va yopiq testda
    o'qituvchi o'quvchi javoblarini o'zi kiritganda.
    """

    if test.mode == "key":
        for key_question in test.key_questions:
            student_answer = answers.get(str(key_question.number)) or ""

            if key_question.question_type == "multi":
                raw_answer = student_answer.upper().replace(" ", "")
                chosen_letters = {letter for letter in raw_answer.split(",") if letter}
                # Kalit ham xuddi shunday tozalanadi — bazada eski, normallashtirilmagan
                # yozuv ("a, c") qolgan bo'lsa ham to'g'ri solishtiriladi.
                correct_letters = {
                    part.strip().upper() for part in key_question.correct.split(",") if part.strip()
                }
                is_correct = bool(chosen_letters) and chosen_letters == correct_letters
                chosen_value = ",".join(sorted(chosen_letters)) if chosen_letters else None
            elif key_question.question_type == "text":
                # Yozma javob: yozilish farqlari hisobga olinmaydi (qarang: _normalize_text_answer),
                # o'qituvchi kiritgan variantlardan biriga mos kelsa yetarli.
                normalized_answer = _normalize_text_answer(student_answer)
                accepted_variants = {
                    _normalize_text_answer(variant) for variant in _split_accepted_variants(key_question.correct)
                }
                is_correct = bool(normalized_answer) and normalized_answer in accepted_variants
                chosen_value = student_answer.strip()[:300] or None
            else:
                raw_answer = student_answer.upper().replace(" ", "")
                is_correct = raw_answer == key_question.correct
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
    bepul testda o'qituvchidan 1 kredit yechadi. Savollar sonini qaytaradi.

    Uch joyda ishlatiladi — o'quvchi "YAKUNLASH" ni bosganda, vaqti tugab
    qolgan urinish fon vazifasida avtomatik tekshirilganda, va o'quvchi
    allaqachon tugab ketgan urinish bilan qaytib kelganda. Hammasi bitta
    funksiyada bo'lgani uchun kredit hisobi hech qachon chalkashmaydi.
    """

    # Qayta yakunlanmasligi uchun: eski javoblar o'rniga yangilari yoziladi
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))

    total_questions = _grade_answers(db, attempt, test, answers)
    _calculate_attempt_score(db, attempt, test)

    attempt.finished_at = utcnow()
    attempt.status = "done"
    attempt.seconds = int(min((attempt.finished_at - attempt.started_at).total_seconds(), test.duration * 60))
    attempt.saved_answers = None  # natija StudentAnswer'da — qoralamani saqlashning hojati yo'q

    # Bepul testda har bir yakunlangan natija o'qituvchidan 1 kredit oladi.
    # max(0, ...) — kredit test BOSHLANGANDA tekshiriladi, lekin oradagi vaqtda
    # o'qituvchi uni boshqa joyda sarflab yuborishi mumkin; shunda balans
    # manfiyga tushib, o'qituvchi keyin sotib olgan kreditini yo'qotardi.
    if test.price == 0:
        test.teacher.credits = max(0, test.teacher.credits - 1)

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


def _send_result_notice(notice: tuple[int, str] | None) -> None:
    """Natija xabarini o'qituvchiga yuboradi. Yuborilmasa ham natija bazada saqlanib qoladi."""

    if not notice or not BOT_TOKEN:
        return

    chat_id, text = notice
    try:
        httpx.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=10,
        )
    except Exception:
        logging.getLogger("backend").warning("Natija o'qituvchiga yuborilmadi", exc_info=True)


def _send_result_notice_in_background(notice: tuple[int, str] | None) -> None:
    """
    So'rov ichidan chaqiriladi: xabar alohida ipda ketadi, o'quvchi esa
    Telegram javobini (10 soniyagacha) kutmasdan darhol javob oladi.
    """

    if notice:
        threading.Thread(target=_send_result_notice, args=(notice,), daemon=True).start()


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

    is_teacher = db.scalar(select(Teacher.id).where(Teacher.user_id == user.id)) is not None

    return {
        "id": user.id,
        "full_name": user.full_name,
        "tg_id": user.tg_id,
        "is_admin": bool(ADMIN_ID) and user.tg_id == ADMIN_ID,
        "is_teacher": is_teacher,
        "default_channels": DEFAULT_REQUIRED_CHANNELS,
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
                    "code": test.code,
                    "title": test.title,
                    "subject": test.subject,
                    "finished_at": attempt.finished_at,
                }
            )
    return {"in_progress": in_progress, "completed": completed}
# ========== 4) O'qituvchi ro'yxatdan o'tishi ==========
# Noto'g'ri promo kod urinishlari (taxmin qilib topishdan himoya): 15 daqiqada 5 ta
PROMO_MAX_FAILURES = 5
PROMO_WINDOW_SECONDS = 15 * 60
_promo_failures: dict[int, list[float]] = {}


@app.post("/api/teacher/promo")
def redeem_promo(body: PromoRedeemIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchi promo kod kiritadi — kredit oladi. Har bir kod bitta o'qituvchiga bir marta."""

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
    teacher.credits += promo.credits
    db.commit()
    _promo_failures.pop(teacher.id, None)
    logging.getLogger("backend").info("Promo kod ishlatildi: promo=%s teacher=%s +%s kredit", promo.id, teacher.id, promo.credits)
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

    # Yangi o'qituvchi darhol ishlay boshlashi uchun sovg'a kreditlar beriladi
    db.add(Teacher(user_id=user.id, credits=WELCOME_CREDITS))
    db.commit()

    return {"ok": True, "full_name": user.full_name, "credits": WELCOME_CREDITS}


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
    }


@app.get("/api/teacher/balance")
def teacher_balance(teacher: Teacher = Depends(get_current_teacher)):
    """O'qituvchining joriy pul va kredit balansi, hamda to'ldirish uchun kerakli ma'lumotlar."""

    return {
        "balance": teacher.balance,
        "credits": teacher.credits,
        "admin_card": ADMIN_CARD_NUMBER,
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
    """O'qituvchi qancha kredit sotib olmoqchi ekanini yozganda, narxini jonli ko'rsatish uchun."""

    if credits < 1:
        raise HTTPException(status_code=422, detail="Kredit soni kamida 1 bo'lishi kerak")
    return {"credits": credits, "amount": _calculate_credit_price(credits)}


@app.post("/api/teacher/credit-purchases")
def create_credit_purchase(
    body: CreditPurchaseIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """Kredit sotib olish buyurtmasini yaratadi va tanlangan tizimning (Payme/Click) to'lov havolasini qaytaradi."""

    _require_provider(body.provider)
    amount = _calculate_credit_price(body.credits)
    purchase = CreditPurchase(teacher_id=teacher.id, credits=body.credits, amount=amount, method=body.provider)
    db.add(purchase)
    db.commit()

    return {
        "id": purchase.id,
        "credits": purchase.credits,
        "amount": purchase.amount,
        "checkout_url": _checkout_url(body.provider, purchase, amount),
    }


@app.get("/api/teacher/credit-purchases")
def list_own_credit_purchases(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining o'zi yaratgan kredit sotib olish buyurtmalari tarixi."""

    purchases = db.scalars(
        select(CreditPurchase).where(CreditPurchase.teacher_id == teacher.id).order_by(CreditPurchase.id.desc())
    ).all()
    return [
        {"id": p.id, "credits": p.credits, "amount": p.amount, "status": p.status, "method": p.method}
        for p in purchases
    ]


# ========== 5.1) Karta orqali kredit sotib olish ==========
#
# O'qituvchi admin kartasiga o'zi pul o'tkazadi va chek rasmini yuboradi.
# Bot chekni admin'ga ✅/❌ tugmalari bilan yuboradi; admin tasdiqlasa,
# o'qituvchiga kredit avtomatik yoziladi va unga xabar boradi.
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
        "💳 Karta orqali kredit so'rovi\n\n"
        f"O'qituvchi: {teacher.user.full_name}\n"
        f"Telegram ID: {teacher.user.tg_id}\n"
        f"Kredit: {purchase.credits} ta\n"
        f"Summa: {_format_sum(purchase.amount)} so'm\n\n"
        "Kartangizga shu summa tushganini tekshirib, tasdiqlang."
    )


def _send_receipt_to_admin(purchase: CreditPurchase, teacher: Teacher, image: bytes, mime: str, extension: str) -> str:
    """
    Chekni admin'ning Telegram chatiga ✅/❌ tugmalari bilan yuboradi.
    Telegram'dagi file_id'ni qaytaradi (rasm keyin shu orqali olinadi).
    """

    response = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendPhoto",
        data={
            "chat_id": ADMIN_ID,
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
    marta bosilsa ham kredit ikki marta yozilmaydi.

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
        teacher.credits += purchase.credits
        db.commit()

        _notify_teacher(
            teacher,
            f"✅ To'lovingiz tasdiqlandi!\n\n"
            f"Hisobingizga {purchase.credits} ta kredit qo'shildi.\n"
            f"Joriy kredit: {teacher.credits} ta",
        )
        return True, (
            f"✅ Tasdiqlandi: {teacher.user.full_name} — {purchase.credits} kredit ({amount_text} so'm)\n"
            f"Uning krediti endi: {teacher.credits} ta"
        )

    purchase.status = "cancelled"
    purchase.cancelled_at = utcnow()
    db.commit()

    _notify_teacher(
        teacher,
        f"❌ {amount_text} so'mlik to'lovingiz tasdiqlanmadi.\n\n"
        "Chek noto'g'ri yoki kartaga pul tushmagan bo'lishi mumkin. "
        "Savol bo'lsa, admin bilan bog'laning.",
    )
    return True, f"❌ Rad etildi: {teacher.user.full_name} — {purchase.credits} kredit ({amount_text} so'm)"


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
    amount = _calculate_credit_price(body.credits)

    purchase = CreditPurchase(teacher_id=teacher.id, credits=body.credits, amount=amount, method="card")
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

    return {"id": purchase.id, "credits": purchase.credits, "amount": purchase.amount, "status": purchase.status}


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
        raise HTTPException(status_code=422, detail="Pullik testlar faqat ochiq test sifatida yaratiladi")
    if body.price > 0 and not PAYME_MERCHANT_ID:
        raise HTTPException(
            status_code=503,
            detail="To'lov tizimi hali sozlanmagan — hozircha faqat bepul test yaratish mumkin",
        )

    # Majburiy obuna so'ralgan bo'lsa, bot kanalni haqiqatan tekshira olishini
    # SHU YERDA aniqlaymiz. Aks holda o'qituvchi testni tarqatib yuborgandan
    # keyin, o'quvchilar kira olmay qolganda bilardi.
    required_channel = _normalize_channel(body.required_channel)
    if required_channel:
        _ensure_bot_can_check_channel(required_channel)

    while True:
        code = "".join(random.choices(TEST_CODE_ALPHABET, k=TEST_CODE_LENGTH))
        if not db.scalar(select(Test.id).where(Test.code == code)):
            break

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
        start_time=body.start_time,
        end_time=body.end_time,
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


@app.get("/api/teacher/tests/{test_id}")
def test_detail(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """
    Test yaratilgandan keyin ochiladigan asosiy sahifa uchun ma'lumot:
    testning o'zi va unga qo'shilgan o'quvchilar ro'yxati.

    Yopiq testda o'quvchini o'qituvchi qo'shadi — shunda avval faqat ismi
    saqlanadi ("javoblari kiritilmagan"), javoblari keyin kiritiladi.
    """

    test = _get_own_test(db, teacher, test_id)

    students = []
    for attempt, name in _all_participants(db, test):
        students.append(
            {
                "id": attempt.id,
                "name": name,
                "has_answers": attempt.status == "done",
                "correct": attempt.correct,
                "wrong": attempt.wrong,
                "percent": attempt.percent,
                "editable": attempt.user_id is None,  # faqat o'qituvchi qo'shgan o'quvchi tahrirlanadi
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
        "price": test.price,
        "enrollment_type": test.enrollment_type,
        "required_channel": test.required_channel,
        "required_channels": _required_channels(test),
        "status": test.status,
        "created_at": test.created_at,
        "count": _question_count(test),
        "students": students,
        "filled": filled,
        "missing": len(students) - filled,
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
    test.status = "closed"
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
        raise HTTPException(status_code=400, detail="O'quvchi qo'shish faqat yopiq testlarda")
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
    return {"added": added}


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
    if not attempt or attempt.test_id != test.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

    student = db.get(User, attempt.user_id) if attempt.user_id else None
    answers = {
        answer.question_ref: answer
        for answer in db.scalars(select(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id)).all()
    }

    questions = []
    if test.mode == "key":
        for group in _group_key_questions(test):
            for key_question in group:
                answer = answers.get(key_question.number)
                questions.append(
                    {
                        "label": _key_question_label(key_question),
                        "kind": key_question.question_type,
                        "chosen": (answer.chosen if answer else None) or "—",
                        "correct": " / ".join(_split_accepted_variants(key_question.correct)),
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
    }


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

    Bepul testda har bir YANGI natija uchun o'qituvchidan 1 kredit yechiladi
    (javoblarni keyin tahrirlash qo'shimcha kredit talab qilmaydi).
    """

    test = _get_own_test(db, teacher, test_id)
    attempt = _get_offline_attempt(db, test, attempt_id)

    # Har bir YANGI o'quvchi natijasi o'qituvchidan 1 kredit oladi (bepul testlarda).
    # Javoblarni keyin tahrirlash qo'shimcha kredit talab qilmaydi.
    was_filled = attempt.status == "done"
    if test.price == 0 and not was_filled and teacher.credits <= 0:
        raise HTTPException(status_code=402, detail="Krediti tugagan — avval kredit sotib oling")

    # Qayta kiritilsa, eski javoblar o'rniga yangilari yoziladi.
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))

    total_questions = _grade_answers(db, attempt, test, body.answers)
    _calculate_attempt_score(db, attempt, test)
    attempt.status = "done"
    attempt.finished_at = utcnow()

    if test.price == 0 and not was_filled:
        teacher.credits = max(0, teacher.credits - 1)

    db.commit()

    return {
        "name": attempt.offline_name,
        "correct": attempt.correct,
        "wrong": attempt.wrong,
        "total": total_questions,
        "percent": attempt.percent,
    }


@app.post("/api/teacher/tests/{test_id}/close")
def close_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Testni yakunlaydi: natijalar qotadi va test "Yopilgan testlar" ro'yxatiga o'tadi."""

    test = _get_own_test(db, teacher, test_id)

    missing = db.scalar(
        select(func.count()).select_from(Attempt).where(Attempt.test_id == test.id, Attempt.status == "pending")
    )
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Yakunlash uchun barcha o'quvchilar javobini kiriting — {missing} ta o'quvchi qolgan",
        )

    test.status = "closed"
    db.commit()

    # Natijalar hisoboti o'qituvchining Telegram chatiga ham yuboriladi
    sent = send_results_to_teacher(db, test)

    return {"ok": True, "report_sent": sent}


@app.post("/api/teacher/tests/{test_id}/reopen")
def reopen_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yakunlangan testni qayta ochadi (masalan, yana o'quvchi qo'shish kerak bo'lsa)."""

    test = _get_own_test(db, teacher, test_id)
    test.status = "active"

    # Hisobot "allaqachon yuborilgan" belgisi ham tozalanadi — aks holda test
    # qayta yakunlanganda, yangi o'quvchilar natijasi bilan hisobot kelmay qolardi.
    test.results_sent_at = None

    db.commit()
    return {"ok": True}


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
        "rows": [{**row, **splits[row["attempt_id"]]} for row in report["rows"]],
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
             "option_count": kq.option_count}
            for kq in test.key_questions
        ]
    else:
        questions = [
            {"ref": str(q.id), "label": q.text, "question_type": "single", "option_count": 4}
            for q in test.questions
        ]

    return {"mode": test.mode, "questions": questions}


@app.patch("/api/teacher/tests/{test_id}/price")
def update_test_price(
    test_id: int, body: PriceUpdate, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi testni yaratgandan keyin ham narxini o'zgartirishi uchun. Avvalgi to'lovlarga ta'sir qilmaydi."""

    test = _get_own_test(db, teacher, test_id)

    # Yaratishdagi qoidalar bu yerda ham amal qiladi — aks holda bepul yopiq
    # testni keyin pullik qilib, to'lov tizimini chetlab o'tish mumkin bo'lardi.
    if body.price > 0 and test.enrollment_type == "closed":
        raise HTTPException(status_code=422, detail="Yopiq testni pullik qilib bo'lmaydi")
    if body.price > 0 and not PAYME_MERCHANT_ID:
        raise HTTPException(status_code=503, detail="To'lov tizimi hali sozlanmagan")

    test.price = body.price
    db.commit()
    return {"id": test.id, "price": test.price}


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


@app.get("/api/teacher/tests/{test_id}/answer-sheet")
def download_answer_sheet(
    test_id: int,
    answers: bool = False,
    teacher: Teacher = Depends(get_current_teacher),
    db: Session = Depends(get_db),
):
    """Javoblar varaqasini brauzerga yuklab beradi (answers=true bo'lsa — to'g'ri javobli kalit)."""

    test = _get_own_test(db, teacher, test_id)
    pdf_bytes = generate_answer_sheet_pdf(test, with_answers=answers)
    file_name = f"{test.code}_{'kalit' if answers else 'javoblar_varaqasi'}.pdf"

    return StreamingResponse(
        io.BytesIO(pdf_bytes),
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


@app.post("/api/teacher/tests/{test_id}/answer-sheet/send")
def send_answer_sheet_to_telegram(
    test_id: int,
    answers: bool = False,
    teacher: Teacher = Depends(get_current_teacher),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Javoblar varaqasini (yoki to'g'ri javobli kalitni) o'qituvchining Telegram chatiga yuboradi."""

    test = _get_own_test(db, teacher, test_id)

    if answers:
        caption = (
            "🔑 Javoblar Kaliti\n\n"
            f"📝 Test: {test.title}\n"
            f"🔑 Kod: {test.code}\n"
            f"📚 Fan: {test.subject}\n\n"
            "Diqqat: bu varaqda to'g'ri javoblar belgilangan — o'quvchilarga tarqatmang!"
        )
    else:
        caption = _answer_sheet_caption(test)

    _send_pdf_to_telegram(
        user.tg_id,
        f"{test.code}_{'kalit' if answers else 'javoblar_varaqasi'}.pdf",
        generate_answer_sheet_pdf(test, with_answers=answers),
        caption=caption,
    )
    return {"ok": True}


# ========== 6) O'quvchi endpointlari ==========


@app.get("/api/tests/{code}/subscriptions")
def test_subscriptions(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    "Tekshirish" tugmasi: o'quvchi testning har bir majburiy kanaliga obunami?
    status: "yes" — obuna, "no" — obuna emas, "unknown" — tekshirib bo'lmadi.
    """

    test = _get_test_by_code(db, code)
    result = []
    for channel in _required_channels(test):
        try:
            status = "yes" if _is_subscribed(channel, user) else "no"
        except HTTPException:
            status = "unknown"
        result.append({"channel": channel, "status": status})
    return {"channels": result}


@app.get("/api/tests/{code}")
def get_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi test kodini kiritganda, testning umumiy ma'lumotini qaytaradi."""

    test = _get_test_by_code(db, code)

    return {
        "code": test.code,
        "title": test.title,
        "subject": test.subject,
        "count": _question_count(test),
        "duration": test.duration,
        "price": test.price,
        "paid": _is_paid(db, user, test),
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

    test = _get_test_by_code(db, code)
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

    if not payment or payment.amount != test.price:
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


@app.post("/api/tests/{code}/start")
def start_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    Testni boshlaydi yoki tugamagan urinishni davom ettiradi.

    Har bir o'quvchiga BITTA urinish beriladi. Ilgari tugatilgan urinish
    e'tiborga olinmasdi va har bosishda yangisi ochilardi — o'quvchi javoblarni
    yodlab, 100% olguncha qayta-qayta ishlab, reytingni ma'nosiz qilib qo'yardi
    (va har urinish o'qituvchining kreditini yeyardi).

    To'g'ri javoblar hech qachon yuborilmaydi — aks holda o'quvchi ularni
    brauzerning so'rovlar oynasidan ko'rib olishi mumkin.
    """

    test = _get_test_by_code(db, code)
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

    # Vaqti tugab qolgan urinish: o'quvchi belgilab qo'ygan javoblar bo'yicha
    # darhol tekshirib, natijasini qotiramiz — mehnati yo'qolmasligi uchun.
    if attempt and attempt.status == "in_progress":
        if (utcnow() - attempt.started_at).total_seconds() >= test.duration * 60:
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
        # Bepul testda har bir natija o'qituvchidan 1 kredit oladi —
        # krediti bo'lmasa, yangi urinish boshlanmaydi.
        if test.price == 0 and test.teacher.credits <= 0:
            raise HTTPException(
                status_code=402,
                detail="O'qituvchining krediti tugagan, hozircha bu testni boshlab bo'lmaydi",
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
                        {"ref": kq.number, "label": re.sub(r"^\d+", "", kq.label or "")} for kq in group
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
        "duration": test.duration,
        "remaining": remaining_seconds,
        "questions": questions_payload,
        # Ilgari belgilangan javoblar — Mini App yopilib qayta ochilsa,
        # o'quvchi to'xtagan joyidan davom etadi.
        "answers": _read_saved_answers(attempt),
    }


def _get_own_attempt(db: Session, user: User, attempt_id: int) -> Attempt:
    """Urinish aynan shu o'quvchiga tegishli ekanini tekshiradi."""

    attempt = db.get(Attempt, attempt_id)
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
    cleaned = {str(ref)[:20]: str(value)[:300] for ref, value in list(body.answers.items())[:300]}
    attempt.saved_answers = json.dumps(cleaned, ensure_ascii=False)
    db.commit()

    return {"saved": len(cleaned)}


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

    test = _get_test_by_code(db, code)
    is_owner = test.teacher is not None and test.teacher.user_id == user.id
    is_admin = bool(ADMIN_ID) and user.tg_id == ADMIN_ID
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


def _active_membership(db: Session, user: User) -> SchoolMember | None:
    return db.scalar(
        select(SchoolMember)
        .where(SchoolMember.user_id == user.id, SchoolMember.left_at.is_(None))
        .order_by(SchoolMember.id.desc())
    )


def _new_school_code(db: Session) -> str:
    while True:
        code = "".join(random.choice(TEST_CODE_ALPHABET) for _ in range(SCHOOL_CODE_LENGTH))
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
    rank = {"director": 1, "deputy": 2}
    members.sort(key=lambda item: (0 if item.role == "owner" else rank.get(item.position, 3), item.joined_at, item.id))

    return {
        "id": school.id,
        "name": school.name,
        "region": school.region,
        "district": school.district,
        # Kod faqat adminda: o'qituvchilarni maktabga faqat admin qo'shadi
        "code": school.code if member.role == "owner" else None,
        "created_at": school.created_at,
        "my_role": member.role,
        "my_position": member.position,
        "my_phone": member.phone,
        "my_birth_date": member.birth_date,
        "my_passport": member.passport,
        # Xodimlarning telefon raqamlarini faqat rahbariyat ko'radi
        "can_see_phones": _is_director(member),
        "is_leader": _is_director(member),
        "can_create_classes": _can_delete_classes(member),
        "my_subjects": _read_subjects(member),
        "invite_link": school_invite_link(school.code) if member.role == "owner" else None,
        # Oylik to'lov — summani admin/direktor belgilaydi; to'lovlarni ular va kuratorlar kiritadi
        "monthly_fee": school.monthly_fee,
        "can_set_fee": _can_delete_classes(member),
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
                "passport": item.passport if (leader or item.user_id == user.id) else None,
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

    query = select(PrivateSchool).where(PrivateSchool.region == region)
    if district:
        query = query.where(PrivateSchool.district == district)
    schools = db.scalars(query.order_by(PrivateSchool.district, PrivateSchool.name)).all()

    return [
        {
            "id": school.id,
            "name": school.name,
            "region": school.region,
            "district": school.district,
            "teachers": sum(1 for member in school.members if member.left_at is None),
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
    if not school:
        raise HTTPException(status_code=404, detail="Maktab topilmadi")
    if school.owner_user_id != user.id:
        raise HTTPException(status_code=403, detail="Bu maktab sizniki emas — kirish uchun maktab kodi kerak")
    if _active_membership(db, user):
        raise HTTPException(status_code=409, detail="Siz allaqachon maktabga a'zosiz")

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
    if not school:
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

    founder_text = (
        f"🔑 Maktab kodi so'raldi\n\n"
        f"👤 {user.full_name} ({requester_link})\n"
        f"«{school.name}» maktabingizga o'qituvchi sifatida qo'shilmoqchi.\n\n"
        f"✅ Agar u haqiqatan maktabingiz o'qituvchisi bo'lsa — unga Telegram orqali maktab kodini yuboring:\n"
        f"{school.code}\n\n"
        f"⛔️ Tanimasangiz yoki boshqa maktabdan bo'lsa — kodni BERMANG. Kod bilan har kim "
        f"maktabingizga kira oladi."
    )
    delivered = _send_bot_text(founder.tg_id, founder_text) if founder else False

    founder_ref = f"@{contact['username']}" if contact["username"] else contact["name"]
    requester_text = (
        f"📨 Kod so'rovingiz yuborildi\n\n"
        f"«{school.name}» maktabi admini — {contact['name']} ({founder_ref}).\n"
        f"U sizga maktab kodini Telegram orqali yuboradi. Tezroq bo'lishi uchun unga o'zingiz ham yozishingiz mumkin.\n\n"
        f"Kodni olgach: Ta'lim muassasalari → Xususiy maktab → Maktabga kirish → maktabingizni tanlang → kodni kiriting.\n\n"
        f"Kodni hech kimga bermang — u faqat maktabingiz o'qituvchilari uchun."
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
    owned = db.scalar(select(PrivateSchool).where(PrivateSchool.owner_user_id == user.id).order_by(PrivateSchool.id.desc()))
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
    if _active_membership(db, user):
        raise HTTPException(status_code=409, detail="Siz allaqachon maktabga a'zosiz. Avval undan chiqing.")
    if not is_valid_location(body.region, body.district):
        raise HTTPException(status_code=422, detail="Viloyat yoki tuman noto'g'ri tanlangan")

    # Bitta tumanda bir xil nomli ikki maktab o'qituvchilarni chalg'itadi
    same_name = db.scalar(
        select(PrivateSchool.id).where(
            PrivateSchool.region == body.region,
            PrivateSchool.district == body.district,
            func.lower(PrivateSchool.name) == body.name.lower(),
        )
    )
    if same_name:
        raise HTTPException(
            status_code=409,
            detail=f"«{body.name}» maktabi bu tumanda allaqachon ochilgan. Uni ro'yxatdan tanlab, kod bilan kiring.",
        )

    school = PrivateSchool(
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
        passport=body.passport,
    )
    db.add(member)
    db.commit()
    db.refresh(member)

    logging.getLogger("backend").info("Xususiy maktab ochildi: id=%s kod=%s", school.id, school.code)
    return {"school": _school_view(db, member, user)}


def _school_by_code(db: Session, user: User, code: str, school_id: int | None) -> PrivateSchool:
    """Kod to'g'ri bo'lsa maktabni qaytaradi; noto'g'ri urinishlar hisoblanadi (taxmin qilishdan himoya)."""

    _check_code_attempts(user)
    if school_id:
        school = db.get(PrivateSchool, school_id)
        if not school:
            raise HTTPException(status_code=404, detail="Maktab topilmadi")
        if not hmac.compare_digest(code, school.code):
            _record_code_failure(user)
            raise HTTPException(status_code=403, detail="Maktab kodi noto'g'ri. Kodni maktabingiz adminidan oling.")
    else:
        school = db.scalar(select(PrivateSchool).where(PrivateSchool.code == code))
        if not school:
            _record_code_failure(user)
            raise HTTPException(status_code=404, detail="Bunday kodli maktab topilmadi. Kodni tekshirib qayta kiriting.")
    _school_code_failures.pop(user.id, None)
    return school


def _open_classes(db: Session, school_id: int) -> list[SchoolClass]:
    """Kuratori hali tayinlanmagan sinflar — qo'shilayotgan kurator o'zinikini tanlaydi."""
    return db.scalars(
        select(SchoolClass)
        .where(SchoolClass.school_id == school_id, SchoolClass.deleted_at.is_(None), SchoolClass.curator_user_id.is_(None))
        .order_by(SchoolClass.name)
    ).all()


@app.post("/api/schools/lookup")
def lookup_school(body: SchoolCodeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Kod bo'yicha maktab nomi va kuratorsiz sinflari (qo'shilish formasida "Kuratormisiz? — Ha" uchun)."""

    school = _school_by_code(db, user, body.code, body.school_id)
    return {
        "school": {"id": school.id, "name": school.name},
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
        passport=body.passport,
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
    if _active_membership(db, user):
        raise HTTPException(status_code=409, detail="Siz allaqachon maktabga a'zosiz. Avval undan chiqing.")

    school = _school_by_code(db, user, body.code, body.school_id)
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
        "school": {"id": school.id, "name": school.name, "region": school.region, "district": school.district},
        "invited_by": inviter.full_name if inviter else "",
        "open_classes": [{"id": item.id, "name": item.name} for item in _open_classes(db, school.id)],
    }


@app.post("/api/schools/invites/{token}/accept")
def accept_invite(
    token: str, body: InviteAcceptIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Shaxsiy havola orqali — kodsiz qo'shilish. Havola shu bilan yopiladi."""

    _require_profile_name(user)
    if _active_membership(db, user):
        raise HTTPException(status_code=409, detail="Siz allaqachon maktabga a'zosiz. Avval undan chiqing.")

    invite = db.scalar(select(SchoolInvite).where(SchoolInvite.token == token).with_for_update())
    if not invite:
        raise HTTPException(status_code=404, detail="Taklif havolasi topilmadi")
    invite = _valid_invite(db, token)
    school = db.get(PrivateSchool, invite.school_id)

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
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.curator_user_id == user_id)
    ):
        school_class.curator_user_id = None
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
    if "passport" in body.model_fields_set:
        member.passport = body.passport

    member.subjects = json.dumps(body.subjects, ensure_ascii=False)
    db.commit()
    return {"school": _school_view(db, member, user)}


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

    member = _require_school_member(db, user)
    school_class = db.get(SchoolClass, class_id)
    if not school_class or school_class.deleted_at is not None or school_class.school_id != member.school_id:
        raise HTTPException(status_code=404, detail="Sinf topilmadi")
    return school_class, member


POSITION_LABELS = {"director": "Direktor", "deputy": "Zam direktor"}


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


def _can_manage_class(school_class: SchoolClass, member: SchoolMember) -> bool:
    """O'quvchilarni qo'shish/tahrirlash — admin, direktor yoki o'sha sinfning kuratori."""
    return _can_delete_classes(member) or school_class.curator_user_id == member.user_id


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
    return bool(member and (_is_director(member) or school_class.curator_user_id == member.user_id))


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


def _active_students(school_class: SchoolClass) -> list[ClassStudent]:
    return [student for student in school_class.students if student.removed_at is None]


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
            for mark in attendance.marks
        ],
    }


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


def _class_view(db: Session, school_class: SchoolClass, with_details: bool = False, member: SchoolMember | None = None) -> dict:
    students = _active_students(school_class)
    curator = db.get(User, school_class.curator_user_id) if school_class.curator_user_id else None
    view = {
        "id": school_class.id,
        "name": school_class.name,
        "students_count": len(students),
        "created_at": school_class.created_at,
        "curator": {"user_id": curator.id, "name": curator.full_name} if curator else None,
        "is_curator": bool(member and school_class.curator_user_id == member.user_id),
        "can_manage": bool(member and _can_manage_class(school_class, member)),
        # ⚙️ sozlamalar (nom, kurator, o'chirish) — admin va direktor
        "is_director": bool(member and _can_delete_classes(member)),
        "can_delete": bool(member and _can_delete_classes(member)),
    }
    if with_details:
        stats = _student_attendance_stats(db, [student.id for student in students], _attendance_filter(school_class, member))
        view["students"] = [
            {
                "id": student.id,
                "full_name": student.full_name,
                "birth_date": student.birth_date,
                "stats": stats[student.id],
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
    classes = db.scalars(
        select(SchoolClass)
        .where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None))
        .order_by(SchoolClass.name)
    ).all()
    views = [_class_view(db, item, member=member) for item in classes]
    views.sort(key=lambda view: (not view["is_curator"], view["name"].lower()))
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
        raise HTTPException(status_code=409, detail=f"«{body.name}» nomli sinf maktabda allaqachon bor")

    school_class = SchoolClass(school_id=member.school_id, teacher_user_id=user.id, name=body.name)
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
    db.commit()
    return {"ok": True}


@app.post("/api/classes/{class_id}/students")
def add_student(class_id: int, body: StudentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    school_class = _managed_class(db, user, class_id)
    db.add(ClassStudent(class_id=school_class.id, full_name=body.full_name, birth_date=body.birth_date))
    db.commit()
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=_active_membership(db, user))


@app.post("/api/classes/{class_id}/students/import/preview")
def preview_student_import(
    class_id: int, body: StudentImportIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """
    Excel/CSV fayl yoki joylangan matndan ismlarni ajratadi — hali SAQLAMAYDI.
    O'qituvchi ro'yxatni ko'rib, tasdiqlagandan keyingina qo'shiladi.
    """

    school_class = _managed_class(db, user, class_id)
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
    db.refresh(school_class)
    return {"added": added, "class": _class_view(db, school_class, with_details=True, member=_active_membership(db, user))}


@app.put("/api/classes/{class_id}/students/{student_id}")
def edit_student(
    class_id: int, student_id: int, body: StudentIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    school_class = _managed_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    student.full_name = body.full_name
    student.birth_date = body.birth_date
    db.commit()
    db.refresh(school_class)
    return _class_view(db, school_class, with_details=True, member=_active_membership(db, user))


@app.get("/api/classes/{class_id}/students/{student_id}")
def student_profile(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi haqida: ma'lumotlari, davomat statistikasi va har darsdagi holati."""

    school_class, member = _school_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")

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

    return {
        "id": student.id,
        "class_id": school_class.id,
        "class_name": school_class.name,
        "full_name": student.full_name,
        "birth_date": student.birth_date,
        "created_at": student.created_at,
        "removed": student.removed_at is not None,
        "stats": _student_attendance_stats(db, [student.id], scope)[student.id],
        "history": [
            {"attendance_id": aid, "taken_at": taken_at, "subject": subject, "status": status}
            for aid, taken_at, subject, status in history
        ],
    }


@app.delete("/api/classes/{class_id}/students/{student_id}")
def remove_student(class_id: int, student_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi sinfdan chiqariladi; eski davomatlarda u ko'rinib turadi."""
    school_class = _managed_class(db, user, class_id)
    student = db.get(ClassStudent, student_id)
    if not student or student.class_id != school_class.id or student.removed_at is not None:
        raise HTTPException(status_code=404, detail="O'quvchi topilmadi")
    student.removed_at = utcnow()
    db.commit()
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


def _grade(percent: float) -> int:
    """Foizdan baho: 86%+ — 5, 71%+ — 4, 56%+ — 3, qolgani — 2."""
    if percent >= 86:
        return 5
    if percent >= 71:
        return 4
    if percent >= 56:
        return 3
    return 2


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
            "grade": _grade(percent) if percent is not None else None,
        })
    # Eng ko'p yechganlar tepada; qatnashmaganlar — oxirida
    rows.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["name"].lower()))
    rank = 0
    for index, row in enumerate(rows):
        if row["score"] is None:
            continue
        if index == 0 or row["score"] != rows[index - 1]["score"]:
            rank = index + 1
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
def list_class_tests(class_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    school_class, member = _school_class(db, user, class_id)
    tests = db.scalars(
        select(ClassTest)
        .where(ClassTest.class_id == school_class.id, ClassTest.deleted_at.is_(None))
        .order_by(ClassTest.taken_on.desc(), ClassTest.id.desc())
    ).all()
    return [_class_test_view(db, test, member, with_scores=False) for test in tests if _can_see_class_test(test, member)]


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


GRADE_COLORS = {5: "#16a34a", 4: "#2563eb", 3: "#d97706", 2: "#dc2626"}


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
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=36,
                                 title=f"{context['title']} — {context['class_name']}")
    width = document.width
    meta = (f"{context['school_name']}  ·  {context['class_name']}  ·  {context['subject'] or 'Fan'}  ·  "
            f"{context['date']}  ·  Maks. {_fmt_number(context['max_score'])} ball")
    summary = (f"O'qituvchi: {context['teacher_name']}   ·   Qatnashdi: {context['participants']} / {context['total']}   ·   "
               f"O'rtacha: {_fmt_number(context['average_percent'] or 0)}%")

    header = ["№", "F.I.Sh.", "Ball", "%", "Baho"]
    data = [[Paragraph(f"<font color='white'>{xml_escape(text)}</font>", styles["cell_bold"]) for text in header]]
    for row in context["scores"]:
        absent = row["score"] is None
        grade = row["grade"]
        data.append([
            Paragraph(str(row.get("rank", "—")), styles["cell"]),
            Paragraph(xml_escape(row["name"]), styles["cell"]),
            Paragraph("—" if absent else _fmt_number(row["score"]), styles["cell"]),
            Paragraph("—" if absent else f"{_fmt_number(row['percent'])}%", styles["cell"]),
            Paragraph("kelmadi" if absent else f"<font color='{GRADE_COLORS[grade]}'><b>{grade}</b></font>", styles["cell"]),
        ])
    table = Table(data, colWidths=[width * 0.08, width * 0.52, width * 0.12, width * 0.14, width * 0.14], repeatRows=1)
    commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_BLUE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
                ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5)]
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
        Spacer(1, 8),
        Paragraph("Baho: 86% va yuqori — 5, 71% — 4, 56% — 3, qolgani — 2.", styles["meta"]),
    ])
    return buffer.getvalue()


def class_test_image(context: dict) -> bytes:
    """Test natijalari — rasm (PNG), guruhga yuborish uchun."""

    from PIL import Image, ImageDraw, ImageFont

    def font(name: str, size: int):
        try:
            return ImageFont.truetype(str(FONTS_DIR / name), size)
        except OSError:
            return ImageFont.load_default()

    title_font, text_font, bold_font = font("DejaVuSerif-Bold.ttf", 44), font("DejaVuSerif.ttf", 28), font("DejaVuSerif-Bold.ttf", 28)
    width, row_height, top = 1080, 58, 300
    rows = context["scores"]
    height = top + row_height * (len(rows) + 1) + 80

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 12], fill="#1f4e79")
    draw.text((50, 36), f"{context['title']}"[:40], font=title_font, fill="#1f4e79")
    draw.text((50, 104), f"{context['school_name']}  ·  {context['class_name']}"[:60], font=text_font, fill="#374151")
    draw.text((50, 146), f"{context['subject'] or 'Fan'}  ·  {context['date']}  ·  maks. {_fmt_number(context['max_score'])} ball",
              font=text_font, fill="#374151")
    draw.text((50, 188), f"O'qituvchi: {context['teacher_name']}"[:60], font=text_font, fill="#6b7280")
    draw.text((50, 236), f"Qatnashdi: {context['participants']} / {context['total']}    O'rtacha: {_fmt_number(context['average_percent'] or 0)}%",
              font=bold_font, fill="#111827")

    columns = [(60, "№"), (130, "F.I.Sh."), (700, "Ball"), (820, "%"), (950, "Baho")]
    y = top
    draw.rectangle([40, y, width - 40, y + row_height], fill="#1f4e79")
    for x, label in columns:
        draw.text((x, y + 13), label, font=bold_font, fill="#ffffff")
    for index, row in enumerate(rows, start=1):
        y += row_height
        if index % 2 == 0:
            draw.rectangle([40, y, width - 40, y + row_height], fill="#f0f3f7")
        absent = row["score"] is None
        color = "#9ca3af" if absent else "#111827"
        draw.text((60, y + 13), str(row.get("rank", "—")), font=text_font, fill=color)
        draw.text((130, y + 13), row["name"][:32], font=text_font, fill=color)
        draw.text((700, y + 13), "—" if absent else _fmt_number(row["score"]), font=text_font, fill=color)
        draw.text((820, y + 13), "—" if absent else f"{_fmt_number(row['percent'])}%", font=text_font, fill=color)
        if absent:
            draw.text((950, y + 13), "kelmadi", font=text_font, fill="#9ca3af")
        else:
            draw.rounded_rectangle([950, y + 9, 994, y + row_height - 9], radius=8, fill=GRADE_COLORS[row["grade"]])
            draw.text((972, y + row_height // 2), str(row["grade"]), font=bold_font, fill="#ffffff", anchor="mm")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


@app.get("/api/class-tests/{test_id}/file")
def class_test_file(test_id: int, format: str = "pdf", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    test, member = _visible_class_test(db, user, test_id)
    context = _class_test_context(db, test, member)
    name = f"natija_{context['class_name']}".replace("/", "-")
    if format == "image":
        return StreamingResponse(io.BytesIO(class_test_image(context)), media_type="image/png",
                                 headers={"Content-Disposition": f'attachment; filename="{test.id}.png"'})
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
        _send_photo_to_telegram(user.tg_id, f"{file_name}.png", class_test_image(context), caption)
    else:
        _send_pdf_to_telegram(user.tg_id, f"{file_name}.pdf", class_test_pdf(context), caption)
    return {"ok": True}


@app.get("/api/schools/test-results")
def school_test_results(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Rahbariyat uchun: har sinfda nechta test natijasi bor (sinfni tanlab, hammasini ko'radi)."""
    member = _require_school_member(db, user)
    if not _is_director(member):
        raise HTTPException(status_code=403, detail="Barcha natijalarni faqat rahbariyat ko'radi")
    classes = db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None)).order_by(SchoolClass.name)
    ).all()
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
    rows.sort(key=lambda row: (row["tests"] == 0, row["name"].lower()))
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


def _student_due(school: PrivateSchool, student: ClassStudent) -> int:
    """Chegirma hisobga olingan oylik summa."""
    return round(school.monthly_fee * (100 - (student.discount_percent or 0)) / 100)


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
    """To'lovlar bilan ishlash — admin, direktor yoki shu sinfning kuratori."""
    school_class, member = _school_class(db, user, class_id)
    if not _can_manage_class(school_class, member):
        raise HTTPException(status_code=403, detail="To'lovlarni admin, direktor yoki sinf kuratori ko'radi")
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
    if not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Oylik to'lovni faqat admin yoki direktor belgilaydi")
    member.school.monthly_fee = body.monthly_fee
    db.commit()
    logging.getLogger("backend").info("Oylik to'lov o'zgardi: school=%s fee=%s user=%s", member.school_id, body.monthly_fee, user.id)
    return {"school": _school_view(db, member, user)}


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
        info = _month_status(_student_due(school, student), paid.get((student.id, month), 0))
        rows.append({"id": student.id, "name": student.full_name, "discount": student.discount_percent or 0, **info})
    rows.sort(key=lambda row: (order[row["status"]], row["name"].lower()))
    return {
        "month": month,
        "month_label": _month_label(month),
        "monthly_fee": school.monthly_fee,
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
    month_rows = [{"month": month, "label": _month_label(month), **_month_status(due, paid.get((student.id, month), 0))} for month in months]
    payments = db.scalars(
        select(TuitionPayment).where(TuitionPayment.student_id == student.id).order_by(TuitionPayment.id.desc())
    ).all()
    return {
        "student": {"id": student.id, "name": student.full_name, "class_name": school_class.name},
        "monthly_fee": school.monthly_fee,
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
    if school.monthly_fee <= 0:
        raise HTTPException(status_code=422, detail="Avval maktabning oylik to'lov summasini belgilang (admin yoki direktor)")
    if body.month > _shift_month(_current_month(), 3):
        raise HTTPException(status_code=422, detail="Faqat 3 oygacha oldindan to'lov qabul qilinadi")

    due = _student_due(school, student)
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
    due = _student_due(school, student)
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
    """Oylik hisobot (admin va direktor): har sinf bo'yicha kutilgan, tushgan va qarz."""
    member = _require_school_member(db, user)
    if not _can_delete_classes(member):
        raise HTTPException(status_code=403, detail="Umumiy hisobotni admin yoki direktor ko'radi")
    month = month if month and re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", month) else _current_month()
    classes = db.scalars(
        select(SchoolClass).where(SchoolClass.school_id == member.school_id, SchoolClass.deleted_at.is_(None)).order_by(SchoolClass.name)
    ).all()
    rows = []
    for school_class in classes:
        info = class_payments(school_class.id, month, user, db)
        rows.append({"id": school_class.id, "name": school_class.name, "students": len(info["students"]), **info["totals"]})
    return {
        "month": month,
        "month_label": _month_label(month),
        "monthly_fee": member.school.monthly_fee,
        "classes": rows,
        "totals": {key: sum(row[key] for row in rows) for key in ("due", "paid", "remaining", "debtors")},
    }


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
        "marks": [(mark.student_name, mark.status) for mark in attendance.marks],
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
    Matn ko'rinishi — faqat darsda BO'LMAGANLAR (yo'q, sababli, kechikkan).
    To'liq ro'yxat (borlar bilan) rasm va PDF'da bo'ladi.
    """

    counts = context["counts"]
    lines = [
        attendance_caption(context),
        "",
        " · ".join(f"{ATTENDANCE_LABELS[s][1]} {ATTENDANCE_LABELS[s][0]}: {counts.get(s, 0)}" for s in ATTENDANCE_LABELS),
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
    document = SimpleDocTemplate(buffer, pagesize=A4, leftMargin=40, rightMargin=40, topMargin=36, bottomMargin=36,
                                 title=f"Davomat — {context['class_name']}")
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
    commands = [("BACKGROUND", (0, 0), (-1, 0), REPORT_BLUE), ("LINEBELOW", (0, 0), (-1, -1), 0.4, REPORT_GRID),
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
    ])
    return buffer.getvalue()


def attendance_image(context: dict) -> bytes:
    """Ota-onalar guruhiga — rasm (PNG) ko'rinishi."""

    from PIL import Image, ImageDraw, ImageFont

    def font(name: str, size: int):
        try:
            return ImageFont.truetype(str(FONTS_DIR / name), size)
        except OSError:
            return ImageFont.load_default()

    title_font, text_font, bold_font = font("DejaVuSerif-Bold.ttf", 46), font("DejaVuSerif.ttf", 30), font("DejaVuSerif-Bold.ttf", 30)
    width, row_height, top = 1080, 62, 300
    marks = context["marks"]
    height = top + row_height * (len(marks) + 1) + 60

    image = Image.new("RGB", (width, height), "#ffffff")
    draw = ImageDraw.Draw(image)
    draw.rectangle([0, 0, width, 12], fill="#1f4e79")
    draw.text((50, 40), f"{context['class_name']} — {context['lesson']}", font=title_font, fill="#1f4e79")
    draw.text((50, 110), f"{context['school_name']}  ·  davomat", font=text_font, fill="#374151")
    draw.text((50, 152), f"O'qituvchi: {context['teacher_name']}", font=text_font, fill="#374151")
    times = f"Dars: {context['when']}" + (f"   Tahrir: {context['updated_at']}" if context["updated_at"] else "")
    draw.text((50, 194), times, font=text_font, fill="#6b7280")

    counts = context["counts"]
    x = 50
    for status in ATTENDANCE_LABELS:
        text = f"{ATTENDANCE_LABELS[status][0]}: {counts.get(status, 0)}"
        draw.text((x, 240), text, font=bold_font, fill=ATTENDANCE_COLORS[status])
        x += draw.textlength(text, font=bold_font) + 40

    y = top
    draw.rectangle([40, y, width - 40, y + row_height], fill="#1f4e79")
    draw.text((60, y + 14), "№", font=bold_font, fill="#ffffff")
    draw.text((140, y + 14), "F.I.Sh.", font=bold_font, fill="#ffffff")
    draw.text((width - 300, y + 14), "Holat", font=bold_font, fill="#ffffff")
    for index, (name, status) in enumerate(marks, start=1):
        y += row_height
        if index % 2 == 0:
            draw.rectangle([40, y, width - 40, y + row_height], fill="#f0f3f7")
        draw.text((60, y + 14), str(index), font=text_font, fill="#111827")
        draw.text((140, y + 14), name[:38], font=text_font, fill="#111827")
        draw.text((width - 300, y + 14), ATTENDANCE_LABELS.get(status, ("?", ""))[0], font=bold_font,
                  fill=ATTENDANCE_COLORS.get(status, "#111827"))

    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


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
    attendance_id: int, format: str = "pdf", user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Davomatni PDF yoki rasm qilib yuklab beradi (oddiy brauzer uchun)."""

    context = _attendance_context(db, _school_attendance(db, user, attendance_id)[0])
    if format == "image":
        return StreamingResponse(io.BytesIO(attendance_image(context)), media_type="image/png",
                                 headers={"Content-Disposition": 'attachment; filename="davomat.png"'})
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
        _send_photo_to_telegram(user.tg_id, "davomat.png", attendance_image(context), caption)
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


def _click_order_ref(order) -> str:
    """Click'ga yuboriladigan buyurtma raqami: c12 — kredit xaridi, t34 — test to'lovi."""
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
    field = "purchase_id" if isinstance(order, CreditPurchase) else "payment_id"
    return _build_payme_checkout_url(field, order.id, amount)


# ========== 6.10) Click webhook (SHOP API: Prepare + Complete) ==========
#
# Click to'lovni ikki bosqichda tasdiqlaydi: Prepare (buyurtma bormi, summa
# to'g'rimi) va Complete (pul yechildi). Har so'rov MD5 imzo bilan keladi —
# imzo noto'g'ri bo'lsa, hech narsa o'zgarmaydi. Kredit/balans faqat Complete
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
    return db.get(model, int(ref[1:]))


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


def _payme_error(request_id, code: int, message: str) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


def _payme_result(request_id, result: dict) -> JSONResponse:
    return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


def _payme_check_auth(request: Request) -> bool:
    """Payme har bir so'rovda 'Paycom:{secret_key}' ko'rinishida Basic Auth yuboradi."""

    # MUHIM: kalit sozlanmagan bo'lsa, hech kimni o'tkazmaymiz. Aks holda
    # hmac.compare_digest("", "") -> True bo'lib, istalgan odam soxta "to'lov
    # bo'ldi" xabarini yuborib, o'ziga tekinga kredit yozdirib olardi.
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


def _payme_state(status: str) -> int:
    return {"pending": 1, "paid": 2, "cancelled": -1}.get(status, 1)


# Payme ikki xil buyurtmani to'laydi va ikkalasi ham bir xil maydonlarga ega
# (status, amount, payme_transaction_id, created_at, performed_at, cancelled_at),
# shuning uchun quyidagi funksiyalar ikkalasi bilan ham bir xil ishlaydi:
#
#   CreditPurchase — o'qituvchining kredit xaridi,     account: {"purchase_id": ...}
#   Payment        — o'quvchining pullik testga to'lovi, account: {"payment_id": ...}
PAYME_ORDER_MODELS = {"purchase_id": CreditPurchase, "payment_id": Payment}


def _payme_find_order_by_account(params: dict, db: Session):
    """So'rovdagi "account" bo'yicha buyurtmani topadi (kredit xaridi yoki testga to'lov)."""

    account = params.get("account") or {}

    for field, model in PAYME_ORDER_MODELS.items():
        raw_id = account.get(field)
        if raw_id in (None, ""):
            continue
        try:
            order = db.get(model, int(raw_id))
        except (TypeError, ValueError):
            return None

        # Karta orqali qilingan xaridni Payme to'lay olmaydi — u admin
        # tasdig'ini kutadi. Aks holda bitta buyurtma ikki yo'l bilan (chek +
        # Payme) ikki marta kredit olishi mumkin edi.
        if isinstance(order, CreditPurchase) and order.method != "payme":
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
        order = db.scalar(select(model).where(model.payme_transaction_id == payme_id))
        if order:
            return order

    return None


def _payme_account_of(order) -> dict:
    """Buyurtmani Payme tushunadigan "account" ko'rinishida qaytaradi."""

    field = "purchase_id" if isinstance(order, CreditPurchase) else "payment_id"
    return {field: str(order.id)}


def _payme_apply_paid(order) -> None:
    """
    To'lov haqiqatan o'tganda bajariladi.

    Kredit xaridida — o'qituvchiga kredit yoziladi.
    Testga to'lovda — o'qituvchi balansiga ulushi qo'shiladi (qolgani platformada).
    """

    if isinstance(order, CreditPurchase):
        order.teacher.credits += order.credits
        return

    order.test.teacher.balance += _teacher_share(order.amount)


def _payme_revert_paid(order) -> None:
    """To'lov bekor qilinganda (pul qaytarilganda) yozilgani orqaga olinadi."""

    if isinstance(order, CreditPurchase):
        order.teacher.credits = max(0, order.teacher.credits - order.credits)
        return

    order.test.teacher.balance = max(0, order.test.teacher.balance - _teacher_share(order.amount))


def _payme_check_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_account(params, db)
    if not order:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")
    if order.status != "pending":
        return _payme_error(request_id, -31051, "Buyurtma allaqachon ko'rib chiqilgan")
    if params.get("amount") != order.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")

    return _payme_result(request_id, {"allow": True})


def _payme_create_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    order = _payme_find_order_by_account(params, db)

    if not order:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")
    if order.payme_transaction_id and order.payme_transaction_id != payme_id:
        return _payme_error(request_id, -31008, "Boshqa tranzaksiya allaqachon mavjud")
    if order.status == "paid":
        return _payme_error(request_id, -31008, "Buyurtma allaqachon to'langan")
    if order.status == "cancelled":
        return _payme_error(request_id, -31008, "Buyurtma bekor qilingan")
    if params.get("amount") != order.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")

    if not order.payme_transaction_id:
        order.payme_transaction_id = payme_id
        db.commit()

    create_time = int(order.created_at.timestamp() * 1000)
    return _payme_result(request_id, {"create_time": create_time, "transaction": str(order.id), "state": 1})


def _payme_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")
    if order.status == "cancelled":
        return _payme_error(request_id, -31008, "Tranzaksiya bekor qilingan")

    if order.status != "paid":
        order.status = "paid"
        order.performed_at = utcnow()
        _payme_apply_paid(order)
        db.commit()

    perform_time = int(order.performed_at.timestamp() * 1000)
    return _payme_result(request_id, {"transaction": str(order.id), "perform_time": perform_time, "state": 2})


def _payme_cancel_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    was_paid = order.status == "paid"

    if order.status != "cancelled":
        if was_paid:
            _payme_revert_paid(order)
        order.status = "cancelled"
        order.cancelled_at = utcnow()
        db.commit()

    cancel_time = int(order.cancelled_at.timestamp() * 1000)
    state = -2 if was_paid else -1
    return _payme_result(request_id, {"transaction": str(order.id), "cancel_time": cancel_time, "state": state})


def _payme_transaction_info(order) -> dict:
    """Payme kutayotgan, tranzaksiya holatini tasvirlovchi javob."""

    return {
        "create_time": int(order.created_at.timestamp() * 1000),
        "perform_time": int(order.performed_at.timestamp() * 1000) if order.performed_at else 0,
        "cancel_time": int(order.cancelled_at.timestamp() * 1000) if order.cancelled_at else 0,
        "transaction": str(order.id),
        "state": _payme_state(order.status),
        "reason": None,
    }


def _payme_check_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    order = _payme_find_order_by_transaction(params, db)
    if not order:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    return _payme_result(request_id, _payme_transaction_info(order))


def _payme_get_statement(request_id, params: dict, db: Session) -> JSONResponse:
    from_dt = datetime.fromtimestamp(params.get("from", 0) / 1000, tz=timezone.utc).replace(tzinfo=None)
    to_dt = datetime.fromtimestamp(params.get("to", 0) / 1000, tz=timezone.utc).replace(tzinfo=None)

    orders = []
    for model in PAYME_ORDER_MODELS.values():
        orders.extend(
            db.scalars(
                select(model)
                .where(model.created_at >= from_dt, model.created_at <= to_dt)
                .where(model.payme_transaction_id.is_not(None))
            ).all()
        )

    orders.sort(key=lambda order: order.created_at)

    transactions = [
        {
            "id": order.payme_transaction_id,
            "time": int(order.created_at.timestamp() * 1000),
            "amount": order.amount * 100,
            "account": _payme_account_of(order),
            **_payme_transaction_info(order),
        }
        for order in orders
    ]
    return _payme_result(request_id, {"transactions": transactions})


PAYME_METHOD_HANDLERS = {
    "CheckPerformTransaction": _payme_check_perform_transaction,
    "CreateTransaction": _payme_create_transaction,
    "PerformTransaction": _payme_perform_transaction,
    "CancelTransaction": _payme_cancel_transaction,
    "CheckTransaction": _payme_check_transaction,
    "GetStatement": _payme_get_statement,
}


@app.post("/api/payme/webhook")
async def payme_webhook(request: Request, db: Session = Depends(get_db)):
    """Payme Merchant API — kredit va testga to'lov hodisalari shu yerga keladi."""

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

    return handler(request_id, params, db)


# ========== 8) Admin endpointlari ==========


@app.get("/api/admin/stats")
def admin_overview(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """
    Butun platforma bo'yicha umumiy statistika — faqat ADMIN_ID'ga ega foydalanuvchi ko'ra oladi.

    Platformaning daromadi IKKI manbadan keladi va ularni aralashtirmaslik kerak:

    1) Kredit sotuvi — o'qituvchilar kredit sotib oladi. Bu pul to'liq
       platformaga tegishli (100%).
    2) Pullik testlar — o'quvchi to'laydi, uning faqat komissiya qismi
       ({PLATFORM_COMMISSION_PERCENT}%) platformada qoladi, qolgani o'qituvchiniki.

    Ilgari bu yerda faqat testga to'lovlarning TO'LIQ summasi ko'rsatilardi:
    ya'ni asosiy daromad (kredit sotuvi) umuman hisoblanmas, test to'lovi esa
    o'qituvchining ulushi bilan birga, oshirib ko'rsatilardi.
    """

    credits_revenue = (
        db.scalar(select(func.sum(CreditPurchase.amount)).where(CreditPurchase.status == "paid")) or 0
    )
    tests_turnover = db.scalar(select(func.sum(Payment.amount)).where(Payment.status == "paid")) or 0
    tests_commission = tests_turnover - _teacher_share(tests_turnover)

    # O'qituvchilar balansidagi pul — bu platformaning QARZI: ular istalgan
    # paytda yechib olishni so'rashi mumkin, shuning uchun "sof" daromad emas.
    teachers_owed = db.scalar(select(func.sum(Teacher.balance))) or 0

    return {
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
        "commission_percent": PLATFORM_COMMISSION_PERCENT,
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
    Barcha o'qituvchilar ro'yxati: kim qancha kredit sotib olgan, qancha
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
            "name": full_name,
            "credits": teacher.credits,
            "balance": teacher.balance,
            "paid_total": paid_total or 0,
            "tests": test_count or 0,
        }
        for teacher, full_name, paid_total, test_count in db.execute(query).all()
    ]


@app.post("/api/admin/backup")
def admin_send_backup(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Butun bazaning zaxira nusxasini JSON fayl ko'rinishida admin'ning Telegram chatiga yuboradi."""

    if not send_backup_to_admin(db):
        raise HTTPException(status_code=502, detail="Zaxira nusxa yuborilmadi (botga /start bosganmisiz?)")
    return {"ok": True}


@app.get("/api/admin/backup")
def admin_download_backup(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Zaxira nusxani to'g'ridan-to'g'ri yuklab beradi (brauzerda sinash uchun)."""

    return StreamingResponse(
        io.BytesIO(export_database(db)),
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="backup_{datetime.now():%Y-%m-%d}.json"'},
    )


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

    request = db.get(WithdrawalRequest, request_id)
    if not request or request.status != "pending":
        raise HTTPException(status_code=404, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    teacher = db.get(Teacher, request.teacher_id)
    if teacher.balance < request.amount:
        raise HTTPException(status_code=402, detail="O'qituvchi balansi yetarli emas, so'rovni rad eting")

    teacher.balance -= request.amount
    request.status = "approved"
    db.commit()
    return {"ok": True, "balance": teacher.balance}


@app.post("/api/admin/withdrawal-requests/{request_id}/reject")
def admin_reject_withdrawal(request_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """So'rovni rad etadi, balansga tegmaydi."""

    request = db.get(WithdrawalRequest, request_id)
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
    """Kartaga pul tushgani tasdiqlanadi — o'qituvchiga kredit yoziladi."""

    ok, message = decide_card_topup(db, purchase_id, approve=True)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


@app.post("/api/admin/card-topups/{purchase_id}/reject")
def admin_reject_card_topup(purchase_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """To'lov rad etiladi — kredit yozilmaydi, o'qituvchiga xabar boradi."""

    ok, message = decide_card_topup(db, purchase_id, approve=False)
    if not ok:
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "message": message}


# Barcha boshqa manzillarni frontend papkasidan beradi (index.html, css, js)
def _asset_version() -> str:
    """
    Fayllar oxirgi marta o'zgargan vaqtdan qisqa "versiya" yasaydi.

    Telegram Mini App eski js/css fayllarni keshlab qoladi, shuning uchun
    index.html ichidagi havolalarga shu versiya qo'shiladi:
    "/js/app.js?v=abc123". Fayl o'zgarsa, havola ham o'zgaradi va
    Telegram yangisini yuklashga majbur bo'ladi.
    """

    latest = 0.0
    for path in (FRONTEND_DIR / "js" / "app.js", FRONTEND_DIR / "css" / "style.css"):
        if path.exists():
            latest = max(latest, path.stat().st_mtime)
    return format(int(latest), "x")


@app.get("/", include_in_schema=False)
def serve_index():
    """Bosh sahifa: har safar yangi tekshiriladi va fayl havolalari versiyalanadi."""

    html = (FRONTEND_DIR / "index.html").read_text(encoding="utf-8")
    version = _asset_version()
    for asset in ("/js/app.js", "/css/style.css"):
        html = html.replace(f'"{asset}"', f'"{asset}?v={version}"')

    return HTMLResponse(
        html,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate", "Pragma": "no-cache"},
    )


app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
