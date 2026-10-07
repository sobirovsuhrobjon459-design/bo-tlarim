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
import hmac
import io
import json
import logging
import random
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import delete, func, inspect, select, text
from sqlalchemy.orm import Session

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
    PLATFORM_COMMISSION_PERCENT,
    WELCOME_CREDITS,
    Attempt,
    Base,
    CreditPurchase,
    CreditPurchaseIn,
    FinishIn,
    KeyQuestion,
    NameUpdateIn,
    Payment,
    PriceUpdate,
    Question,
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
)
from bot.bot import run_bot

# ========== 1) Ilova sozlash ==========

Base.metadata.create_all(engine)


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
        },
        "test_attempts": {"offline_name": "VARCHAR(200)"},
        "key_questions": {"label": "VARCHAR(20)"},
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


_add_missing_columns()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server ishga tushganda botni va kunlik zaxira nusxa vazifasini ishga tushiradi."""
    bot_task = asyncio.create_task(run_bot())
    backup_task = asyncio.create_task(_daily_backup_loop())
    finish_task = asyncio.create_task(_finish_expired_tests_loop())
    yield
    bot_task.cancel()
    backup_task.cancel()
    finish_task.cancel()


app = FastAPI(title="Test Platformasi API", lifespan=lifespan)

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
        "exported_at": datetime.utcnow().isoformat(),
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

        test.results_sent_at = datetime.utcnow()
        db.commit()
        return True
    except Exception:
        logging.getLogger("backend").warning("Natijalar yuborilmadi (test %s)", test.code, exc_info=True)
        return False


async def _finish_expired_tests_loop() -> None:
    """
    Vaqti tugagan testlarni har 5 daqiqada tekshiradi: yakunlaydi va
    natijalarni o'qituvchiga yuboradi. Shunda o'qituvchi hech narsa
    bosmasdan, barcha o'quvchilar ishlab bo'lgach hisobotni oladi.
    """

    while True:
        await asyncio.sleep(5 * 60)

        session = SessionLocal()
        try:
            expired = session.scalars(
                select(Test).where(
                    Test.end_time.is_not(None),
                    Test.end_time <= datetime.utcnow(),
                    Test.results_sent_at.is_(None),
                )
            ).all()

            for test in expired:
                test.status = "closed"
                send_results_to_teacher(session, test)
            session.commit()
        except Exception:
            logging.getLogger("backend").warning("Vaqti tugagan testlarni tekshirib bo'lmadi", exc_info=True)
        finally:
            session.close()


async def _daily_backup_loop() -> None:
    """Har 24 soatda zaxira nusxani admin'ga yuboradi."""

    while True:
        await asyncio.sleep(24 * 60 * 60)

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


def _key_question_label(key_question: KeyQuestion) -> str:
    """Savolning ko'rinadigan nomi: odatda "12-savol", a/b qismli bo'lsa — "36a"."""
    return key_question.label or f"{key_question.number}-savol"


def build_test_stats(db: Session, test: Test) -> dict:
    """O'qituvchi dashboard'ida ko'rsatiladigan umumiy statistika."""

    results = best_attempts(db, test)
    percents = [attempt.percent for attempt, _ in results]

    def question_wrong_stats(ref: int, label: str) -> dict:
        base_query = (
            select(func.count())
            .select_from(StudentAnswer)
            .join(Attempt, Attempt.id == StudentAnswer.attempt_id)
            .where(StudentAnswer.question_ref == ref, Attempt.test_id == test.id, Attempt.status == "done")
        )
        total_answers = db.scalar(base_query)
        wrong_answers = db.scalar(base_query.where(StudentAnswer.is_correct.is_(False)))
        return {"text": label, "total": total_answers, "wrong": wrong_answers}

    if test.mode == "key":
        question_stats = [question_wrong_stats(kq.number, _key_question_label(kq)) for kq in test.key_questions]
    else:
        question_stats = [question_wrong_stats(q.id, q.text) for q in test.questions]

    return {
        "total": len(results),
        "avg": round(sum(percents) / len(percents), 1) if percents else 0,
        "max": max(percents, default=0),
        "min": min(percents, default=0),
        "avg_seconds": int(sum(a.seconds for a, _ in results) / len(results)) if results else 0,
        "questions": question_stats,
    }


def generate_test_report_pdf(db: Session, test: Test, teacher: Teacher) -> bytes:
    """Berilgan test bo'yicha to'liq PDF hisobotni bayt ko'rinishida qaytaradi."""

    stats = build_test_stats(db, test)
    styles = getSampleStyleSheet()
    buffer = io.BytesIO()

    elements = [
        Paragraph(f"Test hisoboti: {test.title}", styles["Title"]),
        Paragraph(f"Fan: {test.subject} | Mavzu: {test.topic or '-'}", styles["Normal"]),
        Paragraph(f"O'qituvchi: {teacher.user.full_name} | Sana: {datetime.now():%d.%m.%Y}", styles["Normal"]),
        Paragraph(f"Qatnashchilar: {stats['total']} | O'rtacha natija: {stats['avg']}%", styles["Normal"]),
        Spacer(1, 14),
    ]

    total_questions = _question_count(test)

    table_data = [["#", "O'quvchi", "Natija", "To'g'ri", "Noto'g'ri", "Foiz"]]
    for index, (attempt, name) in enumerate(best_attempts(db, test), start=1):
        table_data.append(
            [
                index,
                name,
                f"{attempt.correct}/{total_questions}",
                attempt.correct,
                attempt.wrong,
                f"{attempt.percent}%",
            ]
        )

    table = Table(table_data, repeatRows=1)
    table.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#4f46e5")),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
            ]
        )
    )
    elements.append(table)

    SimpleDocTemplate(buffer, pagesize=A4).build(elements)
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

    for _ in range(5):
        new_text = _FRACTION_PATTERN.sub(r"(\1)/(\2)", text)
        if new_text == text:
            break
        text = new_text

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
    if not test or test.teacher_id != teacher.id:
        raise HTTPException(status_code=404, detail="Test topilmadi")
    return test


def _get_test_by_code(db: Session, code: str) -> Test:
    test = db.scalar(select(Test).where(Test.code == code.strip().upper()))
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
    (1, 49, 1000),
    (50, 199, 800),
    (200, 499, 650),
    (500, None, 500),
]

CREDIT_PRICE_TIERS_DISPLAY = [
    {"from": low, "to": high, "price_per_credit": price} for low, high, price in CREDIT_PRICE_TIERS
]


def _calculate_credit_price(credit_count: int) -> int:
    """Kredit sonining jami narxini optom chegirma jadvali bo'yicha hisoblaydi."""
    for low, high, price_per_credit in CREDIT_PRICE_TIERS:
        if credit_count >= low and (high is None or credit_count <= high):
            return credit_count * price_per_credit
    return credit_count * CREDIT_PRICE_TIERS[0][2]


def _build_payme_checkout_url(purchase_id: int, amount: int) -> str:
    """Payme'ning hosted to'lov sahifasiga olib boruvchi havolani yaratadi (amount — so'mda)."""
    params = f"m={PAYME_MERCHANT_ID};ac.purchase_id={purchase_id};a={amount * 100}"
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
    for prefix in ("https://t.me/", "http://t.me/", "t.me/", "@"):
        if channel.startswith(prefix):
            channel = channel[len(prefix) :]
            break

    channel = channel.strip("/")
    return f"@{channel}" if channel else None


def _check_channel_subscription(test: Test, user: User) -> None:
    """
    Majburiy obuna yoqilgan bo'lsa, o'quvchi kanalga obuna ekanini tekshiradi.

    Agar bot o'sha kanalda administrator bo'lmasa, Telegram javob bera olmaydi —
    bunday holatda o'quvchini to'smaymiz (aks holda o'qituvchining sozlamasidagi
    xato tufayli hech kim test ishlay olmay qoladi), faqat logga yozib qo'yamiz.
    """

    if not test.required_channel:
        return

    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
            params={"chat_id": test.required_channel, "user_id": user.tg_id},
            timeout=10,
        )
        data = response.json()
    except Exception:
        logging.getLogger("backend").warning("Kanal obunasini tekshirib bo'lmadi", exc_info=True)
        return

    if not data.get("ok"):
        logging.getLogger("backend").warning(
            "Kanal obunasi tekshirilmadi (%s): %s — bot kanalda admin emas bo'lishi mumkin",
            test.required_channel,
            data.get("description"),
        )
        return

    status = data.get("result", {}).get("status")
    is_subscribed = status in ("creator", "administrator", "member") or (
        status == "restricted" and data["result"].get("is_member")
    )

    if not is_subscribed:
        raise HTTPException(
            status_code=451,  # frontend shu kodni ko'rib, "Obuna bo'lish" tugmasini chiqaradi
            detail=f"Testni boshlash uchun avval {test.required_channel} kanaliga obuna bo'ling",
        )


def _check_enrollment(test: Test) -> None:
    """O'quvchi testni boshlay olishini tekshiradi: yopiq emasligini va vaqt oynasini."""

    if test.enrollment_type == "closed":
        raise HTTPException(
            status_code=403, detail="Bu yopiq test — javoblarni o'qituvchining o'zi kiritadi"
        )
    if test.status == "closed":
        raise HTTPException(status_code=403, detail="Test yakunlangan")

    now = datetime.utcnow()

    if test.start_time and now < test.start_time:
        raise HTTPException(
            status_code=403, detail=f"Test hali boshlanmagan. Boshlanish vaqti: {test.start_time:%d.%m.%Y %H:%M}"
        )
    if test.end_time and now > test.end_time:
        raise HTTPException(status_code=403, detail="Test vaqti tugagan")


# Yozma javoblar MathLive orqali kiritiladi va LaTeX ko'rinishida saqlanadi
# ("2\sqrt{3}", "\frac{\pi}{5}"). Bir xil javob turlicha yozilishi mumkin,
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
    ("\\cdot", "\\times"),
    ("$", ""),
]

# 2-qadam: oddiy matn ko'rinishidagi yozilishlar ("sqrt", "pi", "^2")
TEXT_ANSWER_REPLACEMENTS = [
    (",", "."),
    ("sqrt", "√"),
    ("ildiz", "√"),
    ("pi", "π"),
    # Daraja belgilari ko'paytirishdan OLDIN almashtiriladi, aks holda
    # "x**2" dagi yulduzchalar avval "×" ga aylanib ketadi.
    ("**2", "²"),
    ("**3", "³"),
    ("^2", "²"),
    ("^3", "³"),
    ("*", "×"),
]

_FRACTION_PATTERN = re.compile(r"\\frac\{([^{}]*)\}\{([^{}]*)\}")


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


def _normalize_text_answer(value: str) -> str:
    """
    Yozma javobni solishtirishga tayyorlaydi.

    Probel, katta-kichik harf, LaTeX buyruqlari va yozilish farqlari yo'qoladi:
    "2\\sqrt{3}", "2 sqrt 3" va "2 ildiz 3" — hammasi bir xil natija beradi.
    """

    text = "".join((value or "").split()).lower()

    for source, target in LATEX_REPLACEMENTS:
        text = text.replace(source, target)

    # Kasrlar: \frac{a}{b} -> (a)/(b). Ichma-ich kasrlar uchun bir necha marta.
    for _ in range(5):
        new_text = _FRACTION_PATTERN.sub(r"(\1)/(\2)", text)
        if new_text == text:
            break
        text = new_text

    text = text.replace("{", "").replace("}", "")

    for source, target in TEXT_ANSWER_REPLACEMENTS:
        text = text.replace(source, target)

    # Qolgan LaTeX teskari chiziqlari: "\π" -> "π", "\times" -> "times"
    return text.replace("\\", "")


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
                correct_letters = set(key_question.correct.split(","))
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
        if not test:
            continue

        if attempt.status == "in_progress":
            in_progress.append({"code": test.code, "title": test.title, "subject": test.subject})
        elif attempt.status == "done":
            completed.append(
                {
                    "code": test.code,
                    "title": test.title,
                    "subject": test.subject,
                    "percent": attempt.percent,
                    "correct": attempt.correct,
                    "wrong": attempt.wrong,
                }
            )

    return {"in_progress": in_progress, "completed": completed}


# ========== 4) O'qituvchi ro'yxatdan o'tishi ==========


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

    test_ids = [test.id for test in teacher.tests]
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
    """Kredit sotib olish buyurtmasini yaratadi va Payme to'lov havolasini qaytaradi."""

    if not PAYME_MERCHANT_ID:
        raise HTTPException(status_code=503, detail="Payme hali sozlanmagan, admin bilan bog'laning")

    amount = _calculate_credit_price(body.credits)
    purchase = CreditPurchase(teacher_id=teacher.id, credits=body.credits, amount=amount)
    db.add(purchase)
    db.commit()

    return {
        "id": purchase.id,
        "credits": purchase.credits,
        "amount": purchase.amount,
        "checkout_url": _build_payme_checkout_url(purchase.id, amount),
    }


@app.get("/api/teacher/credit-purchases")
def list_own_credit_purchases(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining o'zi yaratgan kredit sotib olish buyurtmalari tarixi."""

    purchases = db.scalars(
        select(CreditPurchase).where(CreditPurchase.teacher_id == teacher.id).order_by(CreditPurchase.id.desc())
    ).all()
    return [{"id": p.id, "credits": p.credits, "amount": p.amount, "status": p.status} for p in purchases]


@app.get("/api/teacher/tests")
def list_tests(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining barcha testlari ro'yxati (eng yangisi birinchi), qatnashchilar soni bilan."""

    result = []
    for test in reversed(teacher.tests):
        result.append(
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
                "students": len(best_attempts(db, test)),
            }
        )

    return result


@app.post("/api/teacher/tests")
def create_test(body: TestIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Yangi test (va uning savollarini) yaratadi, tasodifiy noyob kod beradi."""

    if body.mode == "key" and not body.key_questions:
        raise HTTPException(status_code=422, detail="Kamida bitta savol kiriting")
    if body.mode == "full" and not body.questions:
        raise HTTPException(status_code=422, detail="Kamida bitta savol kiriting")
    if body.price > 0 and body.enrollment_type == "closed":
        raise HTTPException(status_code=422, detail="Pullik testlar faqat ochiq test sifatida yaratiladi")

    # Har bir yaratilgan test o'qituvchidan 1 kredit oladi
    if teacher.credits <= 0:
        raise HTTPException(
            status_code=402,
            detail="Kreditingiz tugagan — yangi test yaratish uchun kredit sotib oling",
        )

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
        required_channel=_normalize_channel(body.required_channel),
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
    teacher.credits -= 1

    db.add(test)
    db.commit()

    return {"id": test.id, "code": test.code, "credits_left": teacher.credits}


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
        "status": test.status,
        "created_at": test.created_at,
        "count": _question_count(test),
        "students": students,
        "filled": filled,
        "missing": len(students) - filled,
    }


@app.delete("/api/teacher/tests/{test_id}")
def delete_test(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Testni butunlay o'chiradi: savollari, o'quvchilari va javoblari bilan birga."""

    test = _get_own_test(db, teacher, test_id)

    attempt_ids = db.scalars(select(Attempt.id).where(Attempt.test_id == test.id)).all()
    if attempt_ids:
        db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id.in_(attempt_ids)))
    db.execute(delete(Attempt).where(Attempt.test_id == test.id))
    db.execute(delete(Payment).where(Payment.test_id == test.id))

    db.delete(test)  # savollar cascade orqali o'chadi
    db.commit()
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

    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))
    db.delete(attempt)
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
                    "chosen": (answer.chosen if answer else None) or "—",
                    "correct": question.correct,
                    "is_correct": bool(answer and answer.is_correct),
                }
            )

    return {
        "name": student.full_name if student else (attempt.offline_name or "Noma'lum"),
        "correct": attempt.correct,
        "wrong": attempt.wrong,
        "total": _question_count(test),
        "percent": attempt.percent,
        "seconds": attempt.seconds,
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

    # Kredit test yaratilganda olingan — o'quvchi javoblari uchun qo'shimcha olinmaydi.
    # Qayta kiritilsa, eski javoblar o'rniga yangilari yoziladi.
    db.execute(delete(StudentAnswer).where(StudentAnswer.attempt_id == attempt.id))

    total_questions = _grade_answers(db, attempt, test, body.answers)
    _calculate_attempt_score(db, attempt, test)
    attempt.status = "done"
    attempt.finished_at = datetime.utcnow()

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
    db.commit()
    return {"ok": True}


@app.get("/api/teacher/tests/{test_id}/results")
def test_results(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Shu testni ishlagan o'quvchilarning natijalar jadvali."""

    test = _get_own_test(db, teacher, test_id)
    return {
        "id": test.id,
        "title": test.title,
        "price": test.price,
        "mode": test.mode,
        "enrollment_type": test.enrollment_type,
        "rows": [
            {
                "name": name,
                "correct": attempt.correct,
                "wrong": attempt.wrong,
                "percent": attempt.percent,
                "seconds": attempt.seconds,
            }
            for attempt, name in best_attempts(db, test)
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
        "start_time": test.start_time,
        "end_time": test.end_time,
    }


@app.post("/api/tests/{code}/pay")
def pay_for_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """
    HOZIRCHA MOCK: to'lov darhol "paid" deb belgilanadi.

    Haqiqiy Click/Payme ulanganda: bu yerda to'lov "pending" holatda yaratiladi va
    foydalanuvchi to'lov sahifasiga yo'naltiriladi; to'lov tizimining webhook'i kelganda
    shu yozuv "paid"ga o'zgartiriladi.
    """

    test = _get_test_by_code(db, code)
    if _is_paid(db, user, test):
        return {"ok": True}

    db.add(Payment(user_id=user.id, test_id=test.id, amount=test.price, status="paid", provider="mock"))

    # To'lovning bir qismi darhol o'qituvchi balansiga qo'shiladi, qolgani platformada qoladi.
    teacher_share = round(test.price * (100 - PLATFORM_COMMISSION_PERCENT) / 100)
    test.teacher.balance += teacher_share

    db.commit()
    return {"ok": True}


@app.post("/api/tests/{code}/start")
def start_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Testni boshlaydi (yoki tugamagan urinishni davom ettiradi). To'g'ri javoblar yuborilmaydi."""

    test = _get_test_by_code(db, code)
    if not _is_paid(db, user, test):
        raise HTTPException(status_code=402, detail="Avval to'lov qiling")
    _check_enrollment(test)
    _check_channel_subscription(test, user)

    attempt = db.scalar(
        select(Attempt)
        .where(Attempt.test_id == test.id, Attempt.user_id == user.id, Attempt.status == "in_progress")
        .order_by(Attempt.id.desc())
    )

    if attempt and (datetime.utcnow() - attempt.started_at).total_seconds() >= test.duration * 60:
        attempt.status = "expired"
        attempt = None

    if not attempt:
        attempt = Attempt(test_id=test.id, user_id=user.id)
        db.add(attempt)
        db.commit()

    remaining_seconds = int(test.duration * 60 - (datetime.utcnow() - attempt.started_at).total_seconds())

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
    }


@app.post("/api/attempts/{attempt_id}/finish")
def finish_attempt(
    attempt_id: int, body: FinishIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """O'quvchi javoblarini qabul qiladi, avtomatik tekshiradi va natijani qaytaradi."""

    attempt = db.get(Attempt, attempt_id)
    if not attempt or attempt.user_id != user.id:
        raise HTTPException(status_code=404, detail="Urinish topilmadi")
    if attempt.status != "in_progress":
        raise HTTPException(status_code=400, detail="Test allaqachon yakunlangan")

    test = db.get(Test, attempt.test_id)
    total_questions = _grade_answers(db, attempt, test, body.answers)

    _calculate_attempt_score(db, attempt, test)
    attempt.finished_at = datetime.utcnow()
    attempt.status = "done"
    attempt.seconds = int(min((attempt.finished_at - attempt.started_at).total_seconds(), test.duration * 60))

    db.commit()

    return {
        "correct": attempt.correct,
        "wrong": attempt.wrong,
        "total": total_questions,
        "percent": attempt.percent,
        "seconds": attempt.seconds,
        "name": user.full_name,
    }


@app.get("/api/tests/{code}/rating")
def test_rating(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Shu testning top-50 reytingi (eng yaxshi natijalar)."""

    test = _get_test_by_code(db, code)
    return [{"name": name, "percent": attempt.percent} for attempt, name in best_attempts(db, test)[:50]]


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


def _payme_check_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    purchase_id = params.get("account", {}).get("purchase_id")
    amount = params.get("amount")

    purchase = db.get(CreditPurchase, int(purchase_id)) if purchase_id else None
    if not purchase:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")
    if purchase.status != "pending":
        return _payme_error(request_id, -31051, "Buyurtma allaqachon ko'rib chiqilgan")
    if amount != purchase.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")

    return _payme_result(request_id, {"allow": True})


def _payme_create_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    purchase_id = params.get("account", {}).get("purchase_id")
    amount = params.get("amount")

    purchase = db.get(CreditPurchase, int(purchase_id)) if purchase_id else None
    if not purchase:
        return _payme_error(request_id, -31050, "Buyurtma topilmadi")

    if purchase.payme_transaction_id and purchase.payme_transaction_id != payme_id:
        return _payme_error(request_id, -31008, "Boshqa tranzaksiya allaqachon mavjud")
    if purchase.status == "paid":
        return _payme_error(request_id, -31008, "Buyurtma allaqachon to'langan")
    if amount != purchase.amount * 100:
        return _payme_error(request_id, -31001, "Summa mos kelmadi")

    if not purchase.payme_transaction_id:
        purchase.payme_transaction_id = payme_id
        db.commit()

    create_time = int(purchase.created_at.timestamp() * 1000)
    return _payme_result(request_id, {"create_time": create_time, "transaction": str(purchase.id), "state": 1})


def _payme_perform_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.payme_transaction_id == payme_id))
    if not purchase:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    if purchase.status == "cancelled":
        return _payme_error(request_id, -31008, "Tranzaksiya bekor qilingan")

    if purchase.status != "paid":
        purchase.status = "paid"
        purchase.performed_at = datetime.utcnow()
        purchase.teacher.credits += purchase.credits
        db.commit()

    perform_time = int(purchase.performed_at.timestamp() * 1000)
    return _payme_result(request_id, {"transaction": str(purchase.id), "perform_time": perform_time, "state": 2})


def _payme_cancel_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.payme_transaction_id == payme_id))
    if not purchase:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    was_paid = purchase.status == "paid"

    if purchase.status != "cancelled":
        if was_paid:
            purchase.teacher.credits -= purchase.credits
        purchase.status = "cancelled"
        purchase.cancelled_at = datetime.utcnow()
        db.commit()

    cancel_time = int(purchase.cancelled_at.timestamp() * 1000)
    state = -2 if was_paid else -1
    return _payme_result(request_id, {"transaction": str(purchase.id), "cancel_time": cancel_time, "state": state})


def _payme_check_transaction(request_id, params: dict, db: Session) -> JSONResponse:
    payme_id = params.get("id")
    purchase = db.scalar(select(CreditPurchase).where(CreditPurchase.payme_transaction_id == payme_id))
    if not purchase:
        return _payme_error(request_id, -31003, "Tranzaksiya topilmadi")

    return _payme_result(
        request_id,
        {
            "create_time": int(purchase.created_at.timestamp() * 1000),
            "perform_time": int(purchase.performed_at.timestamp() * 1000) if purchase.performed_at else 0,
            "cancel_time": int(purchase.cancelled_at.timestamp() * 1000) if purchase.cancelled_at else 0,
            "transaction": str(purchase.id),
            "state": _payme_state(purchase.status),
            "reason": None,
        },
    )


def _payme_get_statement(request_id, params: dict, db: Session) -> JSONResponse:
    from_dt = datetime.utcfromtimestamp(params.get("from", 0) / 1000)
    to_dt = datetime.utcfromtimestamp(params.get("to", 0) / 1000)

    purchases = db.scalars(
        select(CreditPurchase)
        .where(CreditPurchase.created_at >= from_dt, CreditPurchase.created_at <= to_dt)
        .where(CreditPurchase.payme_transaction_id.is_not(None))
    ).all()

    transactions = [
        {
            "id": purchase.payme_transaction_id,
            "time": int(purchase.created_at.timestamp() * 1000),
            "amount": purchase.amount * 100,
            "account": {"purchase_id": str(purchase.id)},
            "create_time": int(purchase.created_at.timestamp() * 1000),
            "perform_time": int(purchase.performed_at.timestamp() * 1000) if purchase.performed_at else 0,
            "cancel_time": int(purchase.cancelled_at.timestamp() * 1000) if purchase.cancelled_at else 0,
            "transaction": str(purchase.id),
            "state": _payme_state(purchase.status),
            "reason": None,
        }
        for purchase in purchases
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
    """Payme Merchant API — kredit uchun to'lov hodisalari shu yerga keladi."""

    body = await request.json()
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
    """Butun platforma bo'yicha umumiy statistika — faqat ADMIN_ID'ga ega foydalanuvchi ko'ra oladi."""

    total_revenue = db.scalar(select(func.sum(Payment.amount)).where(Payment.status == "paid")) or 0

    return {
        "users": db.scalar(select(func.count()).select_from(User)),
        "teachers": db.scalar(select(func.count()).select_from(Teacher)),
        "tests": db.scalar(select(func.count()).select_from(Test)),
        "attempts": db.scalar(select(func.count()).select_from(Attempt).where(Attempt.status == "done")),
        "revenue": total_revenue,
    }


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


# Barcha boshqa manzillarni frontend papkasidan beradi (index.html, css, js)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
