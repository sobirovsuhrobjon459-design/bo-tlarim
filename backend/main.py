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
import io
import random
import string
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .auth import (
    create_teacher_token,
    get_current_admin,
    get_current_teacher,
    get_current_user,
    get_db,
    hash_password,
    verify_password,
)
from .database import (
    ADMIN_CARD_NUMBER,
    ADMIN_ID,
    BOT_TOKEN,
    FREE_TEST_FEE,
    PLATFORM_COMMISSION_PERCENT,
    Attempt,
    Base,
    Credentials,
    FinishIn,
    KeyQuestion,
    Payment,
    PriceUpdate,
    Question,
    StudentAnswer,
    Teacher,
    Test,
    TestIn,
    TopUpRequest,
    TopUpRequestIn,
    User,
    engine,
)
from bot.bot import run_bot

# ========== 1) Ilova sozlash ==========

Base.metadata.create_all(engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Server ishga tushganda botni fon vazifasi sifatida ham ishga tushiradi."""
    bot_task = asyncio.create_task(run_bot())
    yield
    bot_task.cancel()


app = FastAPI(title="Test Platformasi API", lifespan=lifespan)

FRONTEND_DIR = Path(__file__).resolve().parent.parent / "frontend"


# ========== 2) Statistika va PDF yordamchilari ==========


def best_attempts(db: Session, test: Test) -> list[tuple[Attempt, User]]:
    """Har bir o'quvchining shu testdagi ENG YAXSHI (eng yuqori foiz) urinishini qaytaradi."""

    query = (
        select(Attempt, User)
        .join(User, User.id == Attempt.user_id)
        .where(Attempt.test_id == test.id, Attempt.status == "done")
        .order_by(Attempt.percent.desc(), Attempt.seconds)
    )

    best_by_user: dict[int, tuple[Attempt, User]] = {}
    for attempt, user in db.execute(query).all():
        best_by_user.setdefault(user.id, (attempt, user))

    return list(best_by_user.values())


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
        question_stats = [question_wrong_stats(kq.number, f"{kq.number}-savol") for kq in test.key_questions]
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
        Paragraph(f"O'qituvchi: {teacher.username} | Sana: {datetime.now():%d.%m.%Y}", styles["Normal"]),
        Paragraph(f"Qatnashchilar: {stats['total']} | O'rtacha natija: {stats['avg']}%", styles["Normal"]),
        Spacer(1, 14),
    ]

    table_data = [["#", "O'quvchi", "To'g'ri", "Noto'g'ri", "Natija"]]
    for index, (attempt, user) in enumerate(best_attempts(db, test), start=1):
        table_data.append([index, user.full_name, attempt.correct, attempt.wrong, f"{attempt.percent}%"])

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


def _question_count(test: Test) -> int:
    """Testning rejimiga qarab savollar sonini qaytaradi."""
    return len(test.key_questions) if test.mode == "key" else len(test.questions)


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


def _is_group_member(group_id: int, user_tg_id: int) -> bool:
    """Telegram'dan so'rab, foydalanuvchi shu guruhning a'zosi ekanini tekshiradi."""

    try:
        response = httpx.get(
            f"https://api.telegram.org/bot{BOT_TOKEN}/getChatMember",
            params={"chat_id": group_id, "user_id": user_tg_id},
            timeout=10,
        )
        data = response.json()
        if not data.get("ok"):
            return False
        return data["result"]["status"] in ("member", "administrator", "creator")
    except Exception:
        return False


def _check_enrollment(test: Test, user: User) -> None:
    """Yopiq test uchun guruh a'zoligini, va boshlanish/tugash vaqtini tekshiradi."""

    now = datetime.utcnow()

    if test.start_time and now < test.start_time:
        raise HTTPException(
            status_code=403, detail=f"Test hali boshlanmagan. Boshlanish vaqti: {test.start_time:%d.%m.%Y %H:%M}"
        )
    if test.end_time and now > test.end_time:
        raise HTTPException(status_code=403, detail="Test vaqti tugagan")

    if test.enrollment_type == "closed" and test.telegram_group_id:
        if not _is_group_member(test.telegram_group_id, user.tg_id):
            raise HTTPException(status_code=403, detail="Bu test yopiq — faqat tegishli guruh a'zolari kira oladi")


# ========== 3) Umumiy endpointlar ==========


@app.get("/api/ping")
def ping() -> dict:
    """Autentifikatsiyasiz oddiy tekshiruv — backend ishlayotganini ko'rsatadi."""
    return {"status": "ok", "message": "Backend ishlayapti ✅"}


@app.get("/api/me")
def get_me(user: User = Depends(get_current_user)) -> dict:
    """Mini App ochilganda chaqiriladi — Telegram orqali aniqlangan foydalanuvchini qaytaradi."""
    return {"id": user.id, "full_name": user.full_name, "is_admin": bool(ADMIN_ID) and user.tg_id == ADMIN_ID}


# ========== 4) O'qituvchi autentifikatsiyasi ==========


@app.post("/api/auth/register")
def register_teacher(data: Credentials, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Joriy Telegram foydalanuvchisini o'qituvchi sifatida ro'yxatdan o'tkazadi."""

    already_taken = db.scalar(
        select(Teacher).where((Teacher.username == data.username) | (Teacher.user_id == user.id))
    )
    if already_taken:
        raise HTTPException(status_code=400, detail="Bu login band yoki siz allaqachon ro'yxatdan o'tgansiz")

    teacher = Teacher(user_id=user.id, username=data.username, password_hash=hash_password(data.password))
    db.add(teacher)
    db.commit()

    return {"token": create_teacher_token(user.id)}


@app.post("/api/auth/login")
def login_teacher(data: Credentials, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Avval ro'yxatdan o'tgan o'qituvchini login/parol orqali kirgizadi."""

    teacher = db.scalar(select(Teacher).where(Teacher.username == data.username))
    if not teacher or teacher.user_id != user.id or not verify_password(data.password, teacher.password_hash):
        raise HTTPException(status_code=401, detail="Login yoki parol xato")

    return {"token": create_teacher_token(user.id)}


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
        "students": len({attempt.user_id for attempt in attempts}),
        "avg": round(sum(a.percent for a in attempts) / len(attempts), 1) if attempts else 0,
        "max": max((a.percent for a in attempts), default=0),
        "balance": teacher.balance,
    }


@app.get("/api/teacher/balance")
def teacher_balance(teacher: Teacher = Depends(get_current_teacher)):
    """O'qituvchining joriy balansi va balans to'ldirish uchun kerakli ma'lumotlar."""

    return {
        "balance": teacher.balance,
        "free_test_fee": FREE_TEST_FEE,
        "admin_card": ADMIN_CARD_NUMBER,
    }


@app.post("/api/teacher/topup-requests")
def create_topup_request(
    body: TopUpRequestIn, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)
):
    """O'qituvchi 'shuncha pul tashladim' deb so'rov qoldiradi — admin tasdiqlashi kutiladi."""

    request = TopUpRequest(teacher_id=teacher.id, amount=body.amount)
    db.add(request)
    db.commit()
    return {"id": request.id, "status": request.status}


@app.get("/api/teacher/topup-requests")
def list_own_topup_requests(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining o'zi yuborgan balans to'ldirish so'rovlari tarixi."""

    requests = db.scalars(
        select(TopUpRequest).where(TopUpRequest.teacher_id == teacher.id).order_by(TopUpRequest.id.desc())
    ).all()
    return [{"id": r.id, "amount": r.amount, "status": r.status} for r in requests]


@app.get("/api/teacher/tests")
def list_tests(teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """O'qituvchining barcha testlari ro'yxati (eng yangisi birinchi), qatnashchilar soni bilan."""

    result = []
    for test in reversed(teacher.tests):
        participant_count = db.scalar(
            select(func.count(func.distinct(Attempt.user_id))).where(
                Attempt.test_id == test.id, Attempt.status == "done"
            )
        )
        result.append(
            {
                "id": test.id,
                "code": test.code,
                "title": test.title,
                "subject": test.subject,
                "mode": test.mode,
                "count": _question_count(test),
                "price": test.price,
                "students": participant_count,
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

    # Bepul testlar uchun bir martalik to'lov o'qituvchi balansidan yechiladi
    # (chunki tekshirish xizmati — bizning serverimiz — bepul emas).
    if body.price == 0 and teacher.balance < FREE_TEST_FEE:
        raise HTTPException(
            status_code=402,
            detail=f"Balansingizda yetarli mablag' yo'q (kerak: {FREE_TEST_FEE} so'm). Avval balansni to'ldiring.",
        )

    prefix = "".join(ch for ch in body.subject.upper() if ch in string.ascii_uppercase)[:4] or "TEST"

    while True:
        code = f"{prefix}-{''.join(random.choices(string.ascii_uppercase + string.digits, k=4))}"
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
    if body.price == 0:
        teacher.balance -= FREE_TEST_FEE

    db.add(test)
    db.commit()

    return {"id": test.id, "code": test.code}


@app.get("/api/teacher/tests/{test_id}/results")
def test_results(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Shu testni ishlagan o'quvchilarning natijalar jadvali."""

    test = _get_own_test(db, teacher, test_id)
    return {
        "id": test.id,
        "title": test.title,
        "price": test.price,
        "rows": [
            {
                "name": user.full_name,
                "correct": attempt.correct,
                "wrong": attempt.wrong,
                "percent": attempt.percent,
                "seconds": attempt.seconds,
            }
            for attempt, user in best_attempts(db, test)
        ],
    }


@app.get("/api/teacher/tests/{test_id}/stats")
def test_stats(test_id: int, teacher: Teacher = Depends(get_current_teacher), db: Session = Depends(get_db)):
    """Shu testning umumiy statistikasi va savollar bo'yicha xatolar foizi."""
    return build_test_stats(db, _get_own_test(db, teacher, test_id))


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
    pdf_bytes = generate_test_report_pdf(db, test, teacher)

    response = httpx.post(
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendDocument",
        data={"chat_id": user.tg_id},
        files={"document": (f"{test.code}.pdf", pdf_bytes, "application/pdf")},
        timeout=30,
    )
    if response.status_code != 200:
        raise HTTPException(status_code=502, detail="PDF yuborilmadi (botga /start bosganmisiz?)")

    return {"ok": True}


# ========== 6) O'quvchi endpointlari ==========


@app.get("/api/tests/{code}")
def get_test(code: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """O'quvchi test kodini kiritganda, testning umumiy ma'lumotini qaytaradi."""

    test = _get_test_by_code(db, code)

    is_member = None
    if test.enrollment_type == "closed" and test.telegram_group_id:
        is_member = _is_group_member(test.telegram_group_id, user.tg_id)

    return {
        "code": test.code,
        "title": test.title,
        "subject": test.subject,
        "count": _question_count(test),
        "duration": test.duration,
        "price": test.price,
        "paid": _is_paid(db, user, test),
        "enrollment_type": test.enrollment_type,
        "start_time": test.start_time,
        "end_time": test.end_time,
        "is_member": is_member,
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
    _check_enrollment(test, user)

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
        questions_payload = [
            {"number": kq.number, "question_type": kq.question_type, "option_count": kq.option_count}
            for kq in test.key_questions
        ]
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
    correct_count = 0

    if test.mode == "key":
        total_questions = len(test.key_questions)
        for key_question in test.key_questions:
            raw_answer = (body.answers.get(str(key_question.number)) or "").upper().replace(" ", "")

            if key_question.question_type == "multi":
                chosen_letters = {letter for letter in raw_answer.split(",") if letter}
                correct_letters = set(key_question.correct.split(","))
                is_correct = bool(chosen_letters) and chosen_letters == correct_letters
                chosen_value = ",".join(sorted(chosen_letters)) if chosen_letters else None
            else:
                is_correct = raw_answer == key_question.correct
                chosen_value = raw_answer or None

            correct_count += is_correct
            db.add(
                StudentAnswer(
                    attempt_id=attempt.id,
                    question_ref=key_question.number,
                    chosen=chosen_value,
                    is_correct=is_correct,
                )
            )
    else:
        total_questions = len(test.questions)
        for question in test.questions:
            chosen = (body.answers.get(str(question.id)) or "").upper()
            chosen = chosen if chosen in "ABCD" and len(chosen) == 1 else None
            is_correct = chosen == question.correct
            correct_count += is_correct
            db.add(StudentAnswer(attempt_id=attempt.id, question_ref=question.id, chosen=chosen, is_correct=is_correct))
    attempt.correct = correct_count
    attempt.wrong = total_questions - correct_count
    attempt.percent = round(correct_count / total_questions * 100, 1)
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
    return [{"name": u.full_name, "percent": a.percent} for a, u in best_attempts(db, test)[:50]]


# ========== 7) Admin endpointlari ==========


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


@app.get("/api/admin/topup-requests")
def admin_list_topup_requests(admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """Barcha kutilayotgan ('pending') balans to'ldirish so'rovlari."""

    query = (
        select(TopUpRequest, Teacher)
        .join(Teacher, Teacher.id == TopUpRequest.teacher_id)
        .where(TopUpRequest.status == "pending")
        .order_by(TopUpRequest.id)
    )

    return [
        {"id": request.id, "teacher": teacher.username, "amount": request.amount}
        for request, teacher in db.execute(query).all()
    ]


@app.post("/api/admin/topup-requests/{request_id}/approve")
def admin_approve_topup(request_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """So'rovni tasdiqlaydi va o'qituvchi balansini oshiradi."""

    request = db.get(TopUpRequest, request_id)
    if not request or request.status != "pending":
        raise HTTPException(status_code=404, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    teacher = db.get(Teacher, request.teacher_id)
    teacher.balance += request.amount
    request.status = "approved"
    db.commit()
    return {"ok": True, "balance": teacher.balance}


@app.post("/api/admin/topup-requests/{request_id}/reject")
def admin_reject_topup(request_id: int, admin: User = Depends(get_current_admin), db: Session = Depends(get_db)):
    """So'rovni rad etadi, balansga tegmaydi."""

    request = db.get(TopUpRequest, request_id)
    if not request or request.status != "pending":
        raise HTTPException(status_code=404, detail="So'rov topilmadi yoki allaqachon ko'rib chiqilgan")

    request.status = "rejected"
    db.commit()
    return {"ok": True}


# Barcha boshqa manzillarni frontend papkasidan beradi (index.html, css, js)
app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
