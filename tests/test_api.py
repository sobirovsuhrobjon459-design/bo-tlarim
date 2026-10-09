"""
Backend testlari — qo'shimcha kutubxona talab qilmaydi.

Ishga tushirish (loyihaning bosh papkasidan):
    .venv/Scripts/python tests/test_api.py        # Windows
    .venv/bin/python tests/test_api.py            # Linux/Mac

Har bir test alohida, vaqtinchalik SQLite bazasida ishlaydi — haqiqiy
ma'lumotlarga tegmaydi. Bu testlar aynan ilgari yo'l qo'yilgan xatolarni
qo'riqlaydi: agar kimdir ularni qaytarib qo'ysa, test darhol yiqiladi.
"""

import base64
import io
import hashlib
import hmac
import json
import os
import sys
import tempfile
import traceback
import urllib.parse
from datetime import timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

# MUHIM: sozlamalar backend import qilinishidan OLDIN berilishi kerak —
# backend.database ularni import paytida .env dan o'qiydi.
TEST_BOT_TOKEN = "123456:TEST-TOKEN-FOR-AUTOMATED-CHECKS"
BOT_USER_ID = 123456  # tokenning boshidagi raqam — botning Telegram ID'si
DB_PATH = Path(tempfile.mkdtemp(prefix="matemttest-")) / "test.db"

os.environ.update(
    BOT_TOKEN=TEST_BOT_TOKEN,
    DATABASE_URL="sqlite:///" + DB_PATH.as_posix(),
    DEV_MODE="1",
    WELCOME_CREDITS="75",
    ADMIN_ID="4242",
    PAYME_MERCHANT_ID="test_merchant",
    PAYME_SECRET_KEY="test_secret_key",
    PLATFORM_COMMISSION_PERCENT="30",
    ADMIN_CARD_NUMBER="8600 1234 5678 9012",
)

from fastapi.testclient import TestClient  # noqa: E402

import backend.auth as auth  # noqa: E402
import backend.main as main  # noqa: E402
from backend.database import Attempt, Base, SessionLocal, Teacher, engine, utcnow  # noqa: E402

client = TestClient(main.app)


# ========== Yordamchi funksiyalar ==========


def reset_database() -> None:
    """Har bir test toza bazada boshlanadi."""
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    main._school_code_failures.clear()  # noto'g'ri kod urinishlari hisobi ham tozalanadi
    main._code_requests.clear()
    main._bot_username_cache = "test_platform_bot"  # Telegram'ga so'rov ketmasin
    main.DEFAULT_REQUIRED_CHANNELS = []  # adminlar kanali — alohida testda tekshiriladi
    main._subscription_confirmed.clear()


def as_user(tg_id: int) -> dict:
    """DEV_MODE'dagi soxta foydalanuvchi header'i."""
    return {"X-Dev-User": str(tg_id)}


def register_teacher(tg_id: int = 555, first: str = "Ali", last: str = "Valiyev") -> dict:
    response = client.post(
        "/api/teacher/register", json={"first_name": first, "last_name": last}, headers=as_user(tg_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


def finish_and_grade(attempt_id: int, json: dict | None = None, headers: dict | None = None) -> dict:
    """
    O'quvchi testni yakunlaydi. Natija o'quvchiga qaytarilmaydi (faqat
    o'qituvchiga boradi), shuning uchun tekshirish uchun uni bazadan o'qiymiz.
    """
    response = client.post(f"/api/attempts/{attempt_id}/finish", json=json, headers=headers)
    assert response.status_code == 200, response.text
    session = SessionLocal()
    try:
        attempt = session.get(Attempt, attempt_id)
        return {
            "correct": attempt.correct,
            "wrong": attempt.wrong,
            "total": attempt.correct + attempt.wrong,
            "percent": attempt.percent,
        }
    finally:
        session.close()


def create_test(tg_id: int = 555, **overrides) -> dict:
    body = {
        "title": "Sinov test",
        "subject": "Matematika",
        "mode": "key",
        "enrollment_type": "open",
        "price": 0,
        "duration": 30,
        "key_questions": [
            {"number": 1, "question_type": "single", "option_count": 4, "correct": "B"},
            {"number": 2, "question_type": "single", "option_count": 4, "correct": "C"},
            {"number": 3, "question_type": "multi", "option_count": 4, "correct": "A,C"},
            {"number": 4, "question_type": "text", "correct": r"2\sqrt{3}"},
        ],
    }
    body.update(overrides)

    response = client.post("/api/teacher/tests", json=body, headers=as_user(tg_id))
    assert response.status_code == 200, response.text
    return response.json()


def credits_of(tg_id: int = 555) -> int:
    return client.get("/api/teacher/balance", headers=as_user(tg_id)).json()["credits"]


def balance_of(tg_id: int = 555) -> int:
    return client.get("/api/teacher/balance", headers=as_user(tg_id)).json()["balance"]


def payme(method: str, params: dict, secret: str = "test_secret_key") -> dict:
    """Payme webhook'iga so'rov yuboradi (Basic Auth 'Paycom:{secret}' ko'rinishida)."""
    token = base64.b64encode(f"Paycom:{secret}".encode()).decode()
    response = client.post(
        "/api/payme/webhook",
        headers={"Authorization": f"Basic {token}"},
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
    )
    return response.json()


def signed_init_data(tg_id: int, token: str = TEST_BOT_TOKEN, auth_date: int | None = None) -> str:
    """Telegram'ning rasmiy algoritmi bo'yicha haqiqiy imzolangan initData yasaydi."""
    import time

    fields = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAABBBCCC",
        "user": json.dumps({"id": tg_id, "first_name": "Test", "last_name": "User"}),
    }

    check_string = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
    secret_key = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    fields["hash"] = hmac.new(secret_key, check_string.encode(), hashlib.sha256).hexdigest()

    return urllib.parse.urlencode(fields)


# ========== 1) Autentifikatsiya ==========


def test_initdata_imzosi_tekshiriladi():
    """Haqiqiy imzo o'tadi, soxta imzo 401 bilan to'xtatiladi."""
    reset_database()

    good = client.get("/api/me", headers={"X-Init-Data": signed_init_data(777)})
    assert good.status_code == 200, good.text
    assert good.json()["tg_id"] == 777

    # Boshqa token bilan imzolangan — ya'ni Telegramdan kelmagan
    forged = client.get("/api/me", headers={"X-Init-Data": signed_init_data(777, token="999:BOSHQA-TOKEN")})
    assert forged.status_code == 401, forged.text


def test_buzilgan_initdata_500_bermaydi():
    """Buzilgan ma'lumot kelsa, server yiqilmasdan tushunarli 401 qaytaradi."""
    reset_database()

    for broken in ("", "hash=abc", "auth_date=salom&hash=abc", "user=%7Bbuzilgan&hash=abc"):
        response = client.get("/api/me", headers={"X-Init-Data": broken})
        assert response.status_code == 401, f"{broken!r} -> {response.status_code}"


def test_eskirgan_initdata_qaytariladi():
    """Bir kundan oshgan initData qabul qilinmaydi."""
    reset_database()

    old = signed_init_data(777, auth_date=0)
    response = client.get("/api/me", headers={"X-Init-Data": old})
    assert response.status_code == 401
    assert "eskirgan" in response.json()["detail"]


def test_dev_mode_ochirilganda_header_ishlamaydi():
    """DEV_MODE=0 bo'lsa, X-Dev-User orqali kirishning yo'li yo'q."""
    reset_database()

    original = auth.DEV_MODE
    auth.DEV_MODE = False
    try:
        assert client.get("/api/me", headers=as_user(4242)).status_code == 401
        assert client.get("/api/admin/backup", headers=as_user(4242)).status_code == 401
    finally:
        auth.DEV_MODE = original


def test_dev_mode_productionda_ozi_ochadi():
    """PostgreSQL bazasi bilan ishlaganda DEV_MODE yoqilgan bo'lsa ham o'chadi."""
    import subprocess

    code = "import backend.database as d; print(d.DEV_MODE)"
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=str(BASE_DIR),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "DEV_MODE": "1",
            "DATABASE_URL": "postgresql://user:pass@localhost:5432/db",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "False", result.stdout


def test_admin_faqat_admin_id_bilan():
    """Admin bo'limi faqat ADMIN_ID'ga ega foydalanuvchiga ochiladi."""
    reset_database()

    assert client.get("/api/admin/stats", headers=as_user(4242)).status_code == 200
    assert client.get("/api/admin/stats", headers=as_user(1111)).status_code == 403


# ========== 2) Test yaratish va javoblar kaliti ==========


def test_yangi_oqituvchiga_sovga_kredit():
    reset_database()
    assert register_teacher()["credits"] == 75
    assert credits_of() == 75


def test_kalit_javoblari_normallashtiriladi():
    """Kichik harf va aralash tartibda kiritilgan kalit bir ko'rinishga keltiriladi."""
    reset_database()
    register_teacher()

    created = create_test(
        key_questions=[{"number": 1, "question_type": "multi", "option_count": 4, "correct": "c, a"}]
    )
    detail = client.get(f"/api/teacher/tests/{created['id']}/questions", headers=as_user(555)).json()
    assert detail["questions"][0]["question_type"] == "multi"

    session = SessionLocal()
    try:
        from backend.database import KeyQuestion

        stored = session.scalars(main.select(KeyQuestion)).all()
        assert stored[0].correct == "A,C", stored[0].correct
    finally:
        session.close()


def test_notogri_kalit_qabul_qilinmaydi():
    """Variantlar sonidan tashqaridagi harf yoki bitta javobli savolda ikki harf — xato."""
    reset_database()
    register_teacher()

    for bad in ("Z", "A,B"):
        response = client.post(
            "/api/teacher/tests",
            json={
                "title": "Sinov test",
                "subject": "Matematika",
                "mode": "key",
                "enrollment_type": "open",
                "price": 0,
                "key_questions": [
                    {"number": 1, "question_type": "single", "option_count": 4, "correct": bad}
                ],
            },
            headers=as_user(555),
        )
        assert response.status_code == 422, f"{bad!r} o'tib ketdi"


def test_pullik_test_tolov_tizimisiz_yaratilmaydi():
    """Payme sozlanmagan bo'lsa, pullik test yaratishga yo'l qo'yilmaydi."""
    reset_database()
    register_teacher()

    original = main.PAYME_MERCHANT_ID
    main.PAYME_MERCHANT_ID = ""
    try:
        response = client.post(
            "/api/teacher/tests",
            json={
                "title": "Pullik test",
                "subject": "Matematika",
                "mode": "key",
                "enrollment_type": "open",
                "price": 5000,
                "key_questions": [
                    {"number": 1, "question_type": "single", "option_count": 4, "correct": "A"}
                ],
            },
            headers=as_user(555),
        )
        assert response.status_code == 503, response.text
    finally:
        main.PAYME_MERCHANT_ID = original


def test_yopiq_testni_pullik_qilib_bolmaydi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")

    response = client.patch(
        f"/api/teacher/tests/{created['id']}/price", json={"price": 5000}, headers=as_user(555)
    )
    assert response.status_code == 422, response.text


# ========== 3) Javoblarni tekshirish ==========


def test_javoblar_togri_tekshiriladi():
    """Bitta javob, ko'p javob (tartibi muhim emas) va yozma javob (LaTeX)."""
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={
            "answers": {
                "1": "B",        # to'g'ri
                "2": "A",        # noto'g'ri (C bo'lishi kerak)
                "3": "c,a",      # to'g'ri — tartib va harf o'lchami muhim emas
                "4": "2 ildiz 3",  # to'g'ri — "2\sqrt{3}" bilan bir xil ma'noda
            }
        },
        headers=as_user(901),
    )

    assert result["total"] == 4, result
    assert result["correct"] == 3, result
    assert result["percent"] == 75.0, result


def test_yozma_javobning_turli_yozilishlari():
    """Bir xil ma'nodagi javoblar har xil yozilsa ham to'g'ri hisoblanadi."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[{"number": 1, "question_type": "text", "correct": r"\frac{\pi}{5}"}]
    )

    for index, written in enumerate([r"\frac{\pi}{5}", r"\dfrac{\pi}{5}", "(pi)/(5)"], start=1):
        started = client.post(
            f"/api/tests/{created['code']}/start", json={}, headers=as_user(1000 + index)
        ).json()
        result = finish_and_grade(
            started['attempt_id'],
            json={"answers": {"1": written}},
            headers=as_user(1000 + index),
        )
        assert result["correct"] == 1, f"{written!r} to'g'ri deb hisoblanmadi: {result}"


def test_oddiy_klaviaturada_yozilgan_javob_eski_latex_kalitga_mos_keladi():
    """
    Matematik klaviatura olib tashlandi: o'quvchi endi "2sqrt3", "pi/5" deb
    yozadi. Eski testlarning kaliti esa LaTeX'da saqlangan — ular baribir
    to'g'ri tekshirilishi shart.
    """
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "question_type": "text", "correct": r"2\sqrt{3}"},
            {"number": 2, "question_type": "text", "correct": r"\frac{\pi}{5}"},
            {"number": 3, "question_type": "text", "correct": r"\frac{1}{2}"},
            {"number": 4, "question_type": "text", "correct": r"2\pi"},
            {"number": 5, "question_type": "text", "correct": r"\sqrt{x+1}"},
            {"number": 6, "question_type": "text", "correct": "x^{2}"},
        ]
    )

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={
            "answers": {
                "1": "2sqrt(3)",
                "2": "pi/5",
                "3": "1/2",
                "4": "2*pi",
                "5": "sqrt(x+1)",
                "6": "x^2",
            }
        },
        headers=as_user(901),
    )

    assert result["correct"] == 6, result


def test_mathlive_klaviaturasi_yozuvlari_oddiy_kalitga_mos_keladi():
    """
    MathLive klaviaturasi javobni o'ziga xos LaTeX'da beradi: ichma-ich kasr,
    "\\frac12" (qavssiz), gradus "^{\\circ}", "\\exponentialE", bo'sh joy
    "\\placeholder{}". Kalit oddiy yozuvda bo'lsa ham to'g'ri tekshirilsin.
    """
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "question_type": "text", "correct": "sqrt3/2"},
            {"number": 2, "question_type": "text", "correct": "1/2"},
            {"number": 3, "question_type": "text", "correct": "30°"},
            {"number": 4, "question_type": "text", "correct": "e^x"},
            {"number": 5, "question_type": "text", "correct": "(1/2)/3"},
            {"number": 6, "question_type": "text", "correct": "5"},
        ]
    )

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(902)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={
            "answers": {
                "1": r"\frac{\sqrt{3}}{2}",
                "2": r"\frac12",
                "3": r"30^{\circ}",
                "4": r"\exponentialE^{x}",
                "5": r"\frac{\frac{1}{2}}{3}",
                "6": r"5\placeholder{}",
            }
        },
        headers=as_user(902),
    )

    assert result["correct"] == 6, result


def test_oddiy_yozuvda_manosi_farqli_javob_xato_hisoblanadi():
    """Yozilishni soddalashtirish ma'noni o'zgartirib yubormasligi kerak."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "question_type": "text", "correct": r"2\times 3"},
            {"number": 2, "question_type": "text", "correct": r"\sqrt{x+1}"},
            {"number": 3, "question_type": "text", "correct": "x^2"},
        ]
    )

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={
            "answers": {
                "1": "23",         # 2×3 emas
                "2": "sqrt(x)+1",  # √(x+1) emas
                "3": "x^23",       # x² emas
            }
        },
        headers=as_user(901),
    )

    assert result["correct"] == 0, result


def test_yangi_kalit_oddiy_yozuvda_ham_ishlaydi():
    """O'qituvchi ham endi kalitni oddiy yozadi; bir nechta variant "|" bilan."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[{"number": 1, "question_type": "text", "correct": "0,5|1/2"}]
    )

    for tg_id, written in ((901, "0.5"), (902, "1/2"), (903, "(1)/(2)")):
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
        result = finish_and_grade(
            started['attempt_id'],
            json={"answers": {"1": written}},
            headers=as_user(tg_id),
        )
        assert result["correct"] == 1, f"{written!r} qabul qilinmadi"


def test_yozma_savol_ab_qismlari_bitta_savol():
    """36a va 36b — bitta savol; ikkalasi ham to'g'ri bo'lsa hisoblanadi."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "question_type": "single", "option_count": 4, "correct": "A"},
            {"number": 2, "label": "2a", "question_type": "text", "correct": "5"},
            {"number": 3, "label": "2b", "question_type": "text", "correct": "7"},
        ]
    )

    # Jami 2 ta savol bo'lishi kerak (1 va 2), 3 ta emas
    info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
    assert info["count"] == 2, info

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    assert len(started["questions"]) == 2

    # 2a to'g'ri, 2b noto'g'ri -> 2-savol to'g'ri hisoblanmaydi
    result = finish_and_grade(
        started['attempt_id'],
        json={"answers": {"1": "A", "2": "5", "3": "9"}},
        headers=as_user(901),
    )
    assert result["total"] == 2 and result["correct"] == 1, result


def test_yopiq_va_yozma_sanoq_jamiga_teng():
    """"Yopiq 3/3" + "Yozma 1/1" yig'indisi umumiy "4/4" ga teng bo'lishi kerak."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "question_type": "single", "option_count": 4, "correct": "B"},
            {"number": 2, "question_type": "single", "option_count": 6, "correct": "E"},
            {"number": 3, "question_type": "multi", "option_count": 4, "correct": "A,C"},
            # 4a va 4b — bitta yozma savol
            {"number": 4, "label": "4a", "question_type": "text", "correct": r"2\sqrt{3}"},
            {"number": 5, "label": "4b", "question_type": "text", "correct": r"\frac{\pi}{5}"},
        ]
    )

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={"answers": {"1": "B", "2": "E", "3": "c,a", "4": "2 ildiz 3", "5": "(pi)/(5)"}},
        headers=as_user(901),
    )

    assert result["total"] == 4 and result["correct"] == 4, result

    row = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()["rows"][0]
    assert row["closed_total"] == 3 and row["written_total"] == 1, row
    assert row["closed_total"] + row["written_total"] == result["total"], row
    assert row["closed_correct"] + row["written_correct"] == result["correct"], row

    detail = client.get(
        f"/api/teacher/tests/{created['id']}/students/{started['attempt_id']}/detail", headers=as_user(555)
    ).json()
    assert detail["closed_total"] + detail["written_total"] == detail["total"], detail


def test_yozma_savolning_bir_qismi_xato_bolsa_hisoblanmaydi():
    """4a to'g'ri, 4b xato -> yozma savol to'g'ri sanalmaydi."""
    reset_database()
    register_teacher()
    created = create_test(
        key_questions=[
            {"number": 1, "label": "1a", "question_type": "text", "correct": "5"},
            {"number": 2, "label": "1b", "question_type": "text", "correct": "7"},
        ]
    )

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    result = finish_and_grade(
        started['attempt_id'],
        json={"answers": {"1": "5", "2": "9"}},
        headers=as_user(901),
    )

    assert result["total"] == 1 and result["correct"] == 0, result

    row = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()["rows"][0]
    assert row["written_total"] == 1 and row["written_correct"] == 0, row


# ========== 4) Bitta urinish qoidasi ==========


def _capture_result_notices() -> tuple[list, callable]:
    """O'qituvchiga ketadigan natija xabarlarini Telegram o'rniga ro'yxatga yig'adi."""
    sent: list = []
    original = main._send_result_notice_in_background
    main._send_result_notice_in_background = lambda notice: sent.append(notice)
    return sent, lambda: setattr(main, "_send_result_notice_in_background", original)


def test_natija_oquvchiga_korsatilmaydi_oqituvchiga_boradi():
    """
    Bepul va pullik testda ham: o'quvchi natijasini ko'rmaydi (yakunlashda,
    tarixida, qayta kirganda, reytingda), o'qituvchiga esa ism-familiya va
    natija bot orqali boradi.
    """
    reset_database()
    register_teacher()
    client.post("/api/me/name", json={"first_name": "Vali", "last_name": "Karimov"}, headers=as_user(901))
    created = create_test()

    sent, restore = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        response = client.post(
            f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901)
        )
    finally:
        restore()

    assert response.status_code == 200, response.text
    body = response.json()
    for hidden in ("correct", "wrong", "percent", "total", "seconds"):
        assert hidden not in body, f"o'quvchiga natija ko'rinib qoldi: {body}"

    # O'qituvchiga bitta xabar: uning chatiga, ism-familiya va natija bilan
    assert len(sent) == 1, sent
    chat_id, text = sent[0]
    assert chat_id == 555, chat_id
    assert "Vali Karimov" in text and created["code"] in text and "%" in text, text

    # Testlar tarixida ham natija yo'q
    history = client.get("/api/me/attempts", headers=as_user(901)).json()["completed"]
    assert history and "percent" not in history[0] and "correct" not in history[0], history

    # Qayta kirganda foiz aytilmaydi
    again = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    assert again.status_code == 409 and "%" not in again.json()["detail"], again.text

    # Reytingni o'quvchi ko'ra olmaydi, o'qituvchi ko'radi
    assert client.get(f"/api/tests/{created['code']}/rating", headers=as_user(901)).status_code == 403
    rating = client.get(f"/api/tests/{created['code']}/rating", headers=as_user(555))
    assert rating.status_code == 200 and len(rating.json()) == 1, rating.text


def test_pullik_testda_ham_natija_oquvchiga_korsatilmaydi():
    reset_database()
    register_teacher()
    created = create_test(price=5000)

    # To'lovni bazaga to'g'ridan-to'g'ri yozamiz (Payme oqimi alohida testlarda)
    session = SessionLocal()
    try:
        from backend.database import Payment, User

        user = session.scalar(main.select(User).where(User.tg_id == 901))
        if not user:
            client.get("/api/me", headers=as_user(901))
            user = session.scalar(main.select(User).where(User.tg_id == 901))
        test = session.scalar(main.select(main.Test).where(main.Test.code == created["code"]))
        session.add(Payment(user_id=user.id, test_id=test.id, amount=5000, status="paid"))
        session.commit()
    finally:
        session.close()

    sent, restore = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
        assert started.status_code == 200, started.text
        body = client.post(
            f"/api/attempts/{started.json()['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901)
        ).json()
    finally:
        restore()

    assert "percent" not in body and "correct" not in body, body
    assert len(sent) == 1 and sent[0][0] == 555, sent


def test_vaqti_tugagan_urinish_natijasi_ham_oqituvchiga_boradi():
    """O'quvchi "Yakunlash"ni bosmasa ham, vaqti tugagach natija o'qituvchiga boradi."""
    reset_database()
    register_teacher()
    created = create_test()
    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()

    session = SessionLocal()
    try:
        attempt = session.get(Attempt, started["attempt_id"])
        attempt.started_at = utcnow() - timedelta(hours=5)
        session.commit()

        notices = main._finish_expired_attempts(session)
        session.commit()
    finally:
        session.close()

    assert len(notices) == 1 and notices[0][0] == 555, notices


def test_yopilgan_test_natijalari_ochirilmaguncha_saqlanadi():
    """Test yakunlangach ham qatnashchilar va javoblar o'qituvchi o'chirmaguncha turadi."""
    reset_database()
    register_teacher()
    created = create_test()

    sent, restore = _capture_result_notices()
    try:
        for tg_id in (901, 902):
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
            client.post(
                f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(tg_id)
            )
    finally:
        restore()

    closed = client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555))
    assert closed.status_code == 200, closed.text

    # Fon vazifasi ham hech narsani o'chirmasligi kerak
    main._finish_expired_tests_once()

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert len(results["rows"]) == 2, results
    detail = client.get(
        f"/api/teacher/tests/{created['id']}/students/{results['rows'][0]['attempt_id']}/detail", headers=as_user(555)
    )
    assert detail.status_code == 200 and detail.json()["questions"], detail.text

    # O'qituvchi o'chirgandagina yo'qoladi
    assert client.delete(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).status_code == 200
    assert client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).status_code == 404


def test_rasch_balli_orin_va_daraja():
    """Ko'proq (va qiyinroq) yechgan yuqori o'rinda; teng natija — teng o'rin; o'rtacha ~50."""
    from backend.rasch import grade_for, rasch_scores

    result = rasch_scores([[1, 1, 1, 0], [1, 1, 0, 0], [1, 0, 0, 0], [1, 1, 0, 0], [1, 1, 1, 1], [0, 0, 0, 0]])
    scores = result["scores"]
    assert scores[4] > scores[0] > scores[1] > scores[2] > scores[5], scores
    assert scores[1] == scores[3], "bir xil natija — bir xil ball"
    assert abs(sum(scores) / len(scores) - 50) < 0.5, scores
    assert grade_for(70) == "A+" and grade_for(65.5) == "A" and grade_for(45.9) == "—"

    # Bitta o'quvchi bo'lsa ham ma'noli ball (hammasi 50 bo'lib qolmaydi)
    assert rasch_scores([[1] * 10])["scores"][0] > rasch_scores([[1] * 5 + [0] * 5])["scores"][0]

    reset_database()
    register_teacher()
    created = create_test()  # 1-savolning to'g'ri javobi B (jami 4 savol)
    sent, restore = _capture_result_notices()
    try:
        for tg_id, answer in ((901, "B"), (902, "A"), (903, "B")):
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
            client.post(
                f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": answer}}, headers=as_user(tg_id)
            )
    finally:
        restore()

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    rows = results["rows"]
    assert [row["rank"] for row in rows] == [1, 1, 3], rows
    assert rows[0]["ball"] > rows[2]["ball"] and rows[2]["correct"] == 0 and rows[0]["correct"] == 1, rows
    assert all("grade" in row and "correct" in row for row in rows), rows
    assert results["summary"]["participants"] == 3, results["summary"]


def test_pdf_hisobot_kirill_ismlar_va_yopilgan_test():
    """PDF yopilgan (eski) test uchun ham olinadi, kirill ismlar bilan ham buzilmaydi."""
    reset_database()
    register_teacher()
    client.post("/api/me/name", json={"first_name": "Фаррух", "last_name": "Комилжонов"}, headers=as_user(901))
    created = create_test()

    sent, restore = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
    finally:
        restore()

    assert client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555)).status_code == 200

    pdf = client.get(f"/api/teacher/tests/{created['id']}/pdf", headers=as_user(555))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.text[:200]
    assert len(pdf.content) > 5000

    # Bo'sh test ham PDF beradi (xato emas)
    empty = create_test()
    assert client.get(f"/api/teacher/tests/{empty['id']}/pdf", headers=as_user(555)).status_code == 200


def test_ochirilgan_test_arxivda_qoladi_tolov_va_natijalar_saqlanadi():
    """
    Ilgari test o'chirilsa, uning to'lovlari va o'quvchilar natijalari bazadan
    butunlay yo'qolardi. Endi test faqat yashiriladi — hamma narsa saqlanadi.
    """
    reset_database()
    register_teacher()
    created = create_test()

    sent, restore_notices = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
    finally:
        restore_notices()

    session = SessionLocal()
    try:
        from backend.database import Payment, StudentAnswer, User

        user = session.scalar(main.select(User).where(User.tg_id == 901))
        session.add(Payment(user_id=user.id, test_id=created["id"], amount=5000, status="paid"))
        session.commit()
    finally:
        session.close()

    assert client.delete(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).status_code == 200

    # Hamma joydan yashirindi
    listed = client.get("/api/teacher/tests", headers=as_user(555)).json()
    assert all(test["id"] != created["id"] for test in listed), listed
    assert client.get(f"/api/tests/{created['code']}", headers=as_user(902)).status_code == 404
    assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(902)).status_code == 404
    assert client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).status_code == 404

    # ...lekin bazada hammasi turibdi
    session = SessionLocal()
    try:
        test = session.get(main.Test, created["id"])
        assert test is not None and test.deleted_at is not None
        assert session.scalar(main.select(main.func.count()).select_from(Payment)) == 1
        assert session.scalar(main.select(main.func.count()).select_from(Attempt)) == 1
        assert session.scalar(main.select(main.func.count()).select_from(StudentAnswer)) > 0
    finally:
        session.close()


def test_zaxira_nusxadan_toliq_tiklanadi():
    """Admin'ga keladigan zaxira nusxadan bo'sh bazaga hamma narsa qaytadi."""
    from backend.restore_backup import restore

    reset_database()
    register_teacher()
    client.post("/api/me/name", json={"first_name": "Фаррух", "last_name": "Комилжонов"}, headers=as_user(901))
    created = create_test()
    sent, restore_notices = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
    finally:
        restore_notices()

    session = SessionLocal()
    try:
        backup = json.loads(main.export_database(session))
    finally:
        session.close()
    before = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()

    # Bo'sh bo'lmagan bazaga yozishdan bosh tortadi
    try:
        restore(backup)
        raise AssertionError("to'la bazaga tiklashga ruxsat berildi")
    except RuntimeError:
        pass

    reset_database()
    restored = restore(backup)
    assert restored["users"] >= 2 and restored["tests"] == 1, restored

    after = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert after["rows"] == before["rows"], (before["rows"], after["rows"])
    assert after["rows"][0]["name"] == "Фаррух Комилжонов"

    # Tiklangandan keyin yangi yozuvlar ham qo'shila oladi (id to'qnashmaydi)
    assert create_test()["id"] != created["id"]


def test_oquvchini_ochirish_javoblarni_yoqotmaydi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    client.post(f"/api/teacher/tests/{created['id']}/students", json={"names": ["Ali Valiyev"]}, headers=as_user(555))
    detail = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()
    attempt_id = detail["students"][0]["attempt_id"] if "attempt_id" in detail["students"][0] else detail["students"][0]["id"]

    assert client.delete(f"/api/teacher/tests/{created['id']}/students/{attempt_id}", headers=as_user(555)).status_code == 200
    session = SessionLocal()
    try:
        attempt = session.get(Attempt, attempt_id)
        assert attempt is not None and attempt.status == "deleted"
    finally:
        session.close()
    detail = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()
    assert not detail["students"], detail["students"]


def _set_name(tg_id: int, first: str, last: str) -> None:
    client.post("/api/me/name", json={"first_name": first, "last_name": last}, headers=as_user(tg_id))


def test_xususiy_maktab_yaratish_va_kod_bilan_qoshilish():
    reset_database()
    regions = client.get("/api/regions", headers=as_user(701)).json()
    assert len(regions) == 14 and all(region["districts"] for region in regions), len(regions)
    region = next(item for item in regions if item["name"] == "Toshkent shahri")

    _set_name(701, "Aziza", "Karimova")
    created = client.post(
        "/api/schools",
        json={"region": region["name"], "district": "Chilonzor tumani", "name": "  Kelajak   maktabi ",
              "subjects": ["Matematika", "matematika", "Fizika", ""]},
        headers=as_user(701),
    )
    assert created.status_code == 200, created.text
    school = created.json()["school"]
    assert school["name"] == "Kelajak maktabi" and len(school["code"]) == 8, school
    # Maktabni ochgan — direktor: fan o'qituvchisi emas, fanlari yo'q
    assert school["my_role"] == "owner" and school["my_subjects"] == [], school

    # Ikkinchi maktab ochib bo'lmaydi
    again = client.post("/api/schools", json={"subjects": ["Matematika"], "region": region["name"], "district": "Chilonzor tumani",
                                              "name": "Yana biri"}, headers=as_user(701))
    assert again.status_code == 409, again.text

    # Boshqa o'qituvchi kod bilan qo'shiladi (kichik harf va bo'shliq bilan ham)
    _set_name(702, "Bobur", "Aliyev")
    wrong = client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": "XXXXXX"}, headers=as_user(702))
    assert wrong.status_code == 404, wrong.text
    joined = client.post("/api/schools/join", json={"code": f" {school['code'].lower()} ", "subjects": ["Kimyo"]},
                         headers=as_user(702))
    assert joined.status_code == 200, joined.text
    members = joined.json()["school"]["members"]
    assert [member["name"] for member in members] == ["Aziza Karimova", "Bobur Aliyev"], members
    assert members[0]["role"] == "owner" and members[1]["subjects"] == ["Kimyo"], members

    # Fan majburiy: fansiz qo'shilib yoki profilni bo'shatib bo'lmaydi
    _set_name(705, "Fansiz", "Odam")
    assert client.post("/api/schools/join", json={"code": school["code"]}, headers=as_user(705)).status_code == 422
    assert client.put("/api/schools/me/profile", json={"subjects": []}, headers=as_user(702)).status_code == 422

    # Profil (fanlar) yangilanadi
    updated = client.put("/api/schools/me/profile", json={"subjects": ["Kimyo", "Biologiya"]}, headers=as_user(702))
    assert updated.json()["school"]["my_subjects"] == ["Kimyo", "Biologiya"]

    # Guruhlarga ulashish havolasi — bot orqali kod bilan
    assert joined.json()["school"]["invite_link"] == f"https://t.me/test_platform_bot?start=school_{school['code']}"

    # Maktab saqlanib qoladi — qayta kirganda ham o'sha maktab
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"]["id"] == school["id"]


def test_maktabdan_faqat_kod_bilan_chiqiladi_va_tarix_saqlanadi():
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Bobur", "Aliyev")
    school = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Samarqand viloyati", "district": "Urgut tumani",
                                               "name": "Ilm maktabi"}, headers=as_user(701)).json()["school"]
    client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": school["code"]}, headers=as_user(702))

    # Kodsiz / noto'g'ri kod bilan chiqib bo'lmaydi
    assert client.post("/api/schools/leave", json={"code": "AAAAAA"}, headers=as_user(702)).status_code == 403
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is not None

    # Direktor o'rinbosar tanlamasdan chiqa olmaydi — maktab direktorsiz qolmasin
    assert client.post("/api/schools/leave", json={"code": school["code"]}, headers=as_user(701)).status_code == 422
    successor_id = next(m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]
                        if m["name"] == "Bobur Aliyev")
    assert client.post("/api/schools/leave", json={"code": school["code"], "successor_user_id": 999999},
                       headers=as_user(701)).status_code == 422
    assert client.post("/api/schools/leave", json={"code": school["code"], "successor_user_id": successor_id},
                       headers=as_user(701)).status_code == 200
    assert client.get("/api/schools/me", headers=as_user(701)).json()["school"] is None
    remaining = client.get("/api/schools/me", headers=as_user(702)).json()["school"]
    assert remaining["my_role"] == "owner" and len(remaining["members"]) == 1, remaining

    # Chiqqan o'qituvchining yozuvi o'chirilmagan
    session = SessionLocal()
    try:
        from backend.database import SchoolMember

        rows = session.scalars(main.select(SchoolMember)).all()
        assert len(rows) == 2 and any(row.left_at is not None for row in rows), rows
    finally:
        session.close()

    # Chiqqandan keyin yana qo'shila oladi
    assert client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": school["code"]}, headers=as_user(701)).status_code == 200


def test_tumandagi_maktablar_royxati_va_tanlangan_maktabga_faqat_uning_kodi_bilan():
    reset_database()
    main._school_code_failures.clear()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Bobur", "Aliyev")
    _set_name(703, "Sardor", "Raximov")
    place = {"region": "Samarqand viloyati", "district": "Urgut tumani"}

    first = client.post("/api/schools", json={"subjects": ["Matematika"], **place, "name": "Ilm maktabi"}, headers=as_user(701)).json()["school"]
    second = client.post("/api/schools", json={"subjects": ["Matematika"], **place, "name": "Bilim maktabi"}, headers=as_user(702)).json()["school"]
    assert len(first["code"]) == 8, first["code"]

    # Shu tumanda bir xil nomli maktab ikkinchi marta ochilmaydi (katta-kichik harf farqi bilan ham)
    _set_name(704, "Dilshod", "Toshev")
    duplicate = client.post("/api/schools", json={"subjects": ["Matematika"], **place, "name": "ilm MAKTABI"}, headers=as_user(704))
    assert duplicate.status_code == 409, duplicate.text

    listed = client.get("/api/schools", params=place, headers=as_user(703)).json()
    assert [school["name"] for school in listed] == ["Bilim maktabi", "Ilm maktabi"], listed
    assert all("code" not in school for school in listed), "kod ro'yxatda ko'rinib qoldi"
    assert listed[1]["teachers"] == 1

    # Boshqa tuman ro'yxatida ko'rinmaydi
    other = client.get("/api/schools", params={"region": "Samarqand viloyati", "district": "Jomboy tumani"},
                       headers=as_user(703)).json()
    assert other == []

    # "Ilm maktabi"ni tanlab, boshqa maktabning (to'g'ri) kodi bilan kira olmaydi
    wrong = client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": second["code"], "school_id": first["id"]}, headers=as_user(703))
    assert wrong.status_code == 403, wrong.text
    ok = client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": first["code"], "school_id": first["id"]}, headers=as_user(703))
    assert ok.status_code == 200 and ok.json()["school"]["name"] == "Ilm maktabi", ok.text


def test_kod_sorash_asoschiga_va_sorovchiga_bot_xabari():
    """
    Maktabga kirmoqchi bo'lgan o'qituvchi asoschining Telegram manzilini ko'radi
    va "Kod so'rash" bosadi: bot asoschiga kodni (va berish qoidasini), so'rovchiga
    esa nima qilishni yozadi. Kodning o'zi so'rovchiga HECH QACHON yuborilmaydi.
    """
    reset_database()
    main._code_requests.clear()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Bobur", "Aliyev")

    session = SessionLocal()
    try:
        from backend.database import User

        founder = session.scalar(main.select(User).where(User.tg_id == 701))
        founder.username = "aziza_teacher"
        session.commit()
    finally:
        session.close()

    school = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Kelajak"}, headers=as_user(701)).json()["school"]

    # Butun viloyat bo'yicha ro'yxat (tumansiz) — asoschi manzili bilan, kodsiz
    listed = client.get("/api/schools", params={"region": "Toshkent shahri"}, headers=as_user(702)).json()
    assert len(listed) == 1 and listed[0]["district"] == "Chilonzor tumani", listed
    assert listed[0]["founder"]["link"] == "https://t.me/aziza_teacher", listed[0]["founder"]
    assert "code" not in listed[0]

    sent: list[tuple[int, str]] = []
    original = main._send_bot_text
    main._send_bot_text = lambda chat_id, text: sent.append((chat_id, text)) or True
    try:
        response = client.post(f"/api/schools/{school['id']}/request-code", headers=as_user(702))
        assert response.status_code == 200 and response.json()["delivered"] is True, response.text

        # Ketma-ket qayta so'rash — asoschini bezor qilmaslik uchun to'xtatiladi
        again = client.post(f"/api/schools/{school['id']}/request-code", headers=as_user(702))
        assert again.status_code == 429, again.text

        # Asoschi o'z maktabiga kod so'ramaydi
        assert client.post(f"/api/schools/{school['id']}/request-code", headers=as_user(701)).status_code == 400
    finally:
        main._send_bot_text = original

    by_chat = dict(sent)
    assert set(by_chat) == {701, 702}, sent
    assert school["code"] in by_chat[701] and "Bobur Aliyev" in by_chat[701] and "BERMANG" in by_chat[701]
    assert school["code"] not in by_chat[702], "kod so'rovchiga yuborilib qoldi!"
    assert "@aziza_teacher" in by_chat[702] and "Maktabga kirish" in by_chat[702]


def _school_with_class(tg_id: int = 701) -> dict:
    _set_name(tg_id, "Aziza", "Karimova")
    client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                      "name": f"Maktab {tg_id}", "subjects": ["Matematika"]}, headers=as_user(tg_id))
    created = client.post("/api/classes", json={"name": "10/2"}, headers=as_user(tg_id))
    assert created.status_code == 200, created.text
    return created.json()


def test_lavozimlar_direktor_va_zam_direktor():
    """Admin lavozim beradi: direktor (bitta) va zam direktor. Ikkalasi sinf yaratadi va
    o'qituvchi taklif qiladi; sinfni faqat admin va direktor o'chiradi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Lavozim maktabi"}, headers=as_user(701)).json()["school"]
    for tg_id in (702, 703, 704):
        _set_name(tg_id, "Ustoz", f"Nomer{tg_id}")
        assert client.post("/api/schools/join", json={"code": school["code"], "subjects": ["Fizika"]},
                           headers=as_user(tg_id)).status_code == 200
    ids = {m["name"]: m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    uid = {tg: ids[f"Ustoz Nomer{tg}"] for tg in (702, 703, 704)}

    # Oddiy o'qituvchi sinf yarata olmaydi, lavozim ham bera olmaydi
    assert client.post("/api/classes", json={"name": "1-A"}, headers=as_user(702)).status_code == 403
    assert client.put(f"/api/schools/members/{uid[703]}/position", json={"position": "deputy"},
                      headers=as_user(702)).status_code == 403
    assert client.put(f"/api/schools/members/{uid[702]}/position", json={"position": "boss"},
                      headers=as_user(701)).status_code == 422

    # Admin: 702 — direktor, 703 — zam direktor
    for tg, position in ((702, "director"), (703, "deputy")):
        r = client.put(f"/api/schools/members/{uid[tg]}/position", json={"position": position}, headers=as_user(701))
        assert r.status_code == 200, r.text
    me = client.get("/api/schools/me", headers=as_user(703)).json()["school"]
    assert me["my_position"] == "deputy" and me["is_leader"] is True, me
    assert [m["position"] for m in me["members"]][:3] == [None, "director", "deputy"], me["members"]

    # Zam direktor: sinf yaratadi, taklif qiladi, lekin o'chira olmaydi
    created = client.post("/api/classes", json={"name": "1-A"}, headers=as_user(703)).json()
    assert created["is_director"] is True and created["can_delete"] is False, created
    assert client.post("/api/schools/invites", json={}, headers=as_user(703)).status_code == 200
    assert client.delete(f"/api/classes/{created['id']}", headers=as_user(703)).status_code == 403
    # Direktor o'chira oladi
    assert client.get(f"/api/classes/{created['id']}", headers=as_user(702)).json()["can_delete"] is True
    assert client.delete(f"/api/classes/{created['id']}", headers=as_user(702)).status_code == 200

    # Direktor bitta: 704 direktor bo'lsa, 702 oddiy o'qituvchiga qaytadi
    client.put(f"/api/schools/members/{uid[704]}/position", json={"position": "director"}, headers=as_user(701))
    positions = {m["user_id"]: m["position"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    assert positions[uid[704]] == "director" and positions[uid[702]] is None, positions
    assert client.post("/api/classes", json={"name": "2-A"}, headers=as_user(702)).status_code == 403

    # Lavozimni olib tashlash va admin lavozimiga tegib bo'lmasligi
    assert client.put(f"/api/schools/members/{uid[703]}/position", json={"position": None},
                      headers=as_user(701)).json()["school"]["members"]
    admin_id = ids["Maktab Admini"]
    assert client.put(f"/api/schools/members/{admin_id}/position", json={"position": "deputy"},
                      headers=as_user(701)).status_code == 422


def test_sinf_yaratish_va_oquvchilar():
    reset_database()
    # Maktabga a'zo bo'lmasa sinf ochib bo'lmaydi
    _set_name(709, "Begona", "Odam")
    assert client.post("/api/classes", json={"name": "5-A"}, headers=as_user(709)).status_code == 403

    school_class = _school_with_class()
    assert school_class["name"] == "10/2" and school_class["students"] == []
    assert client.post("/api/classes", json={"name": " 10/2 "}, headers=as_user(701)).status_code == 409

    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Islom  Karimov", "birth_date": "2010-05-14"},
                headers=as_user(701))
    detail = client.post(f"/api/classes/{cid}/students", json={"full_name": "Фаррух Комилжонов"}, headers=as_user(701)).json()
    assert [s["full_name"] for s in detail["students"]] == ["Islom Karimov", "Фаррух Комилжонов"], detail
    assert detail["students"][0]["birth_date"] == "2010-05-14" and detail["students"][1]["birth_date"] is None

    listed = client.get("/api/classes", headers=as_user(701)).json()
    assert listed[0]["students_count"] == 2, listed

    # Nomini o'zgartirish: o'quvchilar joyida qoladi, takror nom bo'lmaydi
    client.post("/api/classes", json={"name": "11-A"}, headers=as_user(701))
    renamed = client.put(f"/api/classes/{cid}", json={"name": "10/2 Iqtisod"}, headers=as_user(701))
    assert renamed.status_code == 200 and renamed.json()["name"] == "10/2 Iqtisod", renamed.text
    assert len(renamed.json()["students"]) == 2
    assert client.put(f"/api/classes/{cid}", json={"name": "11-a"}, headers=as_user(701)).status_code == 409

    # Boshqa o'qituvchi bu sinfni ko'ra olmaydi
    client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Chilonzor tumani",
                                      "name": "Boshqa maktab"}, headers=as_user(709))
    assert client.get(f"/api/classes/{cid}", headers=as_user(709)).status_code == 404


def test_direktor_kurator_va_oqituvchi_huquqlari():
    """
    Direktor sinf yaratadi va kurator tayinlaydi; kurator o'quvchi qo'shadi;
    sinfi yo'q o'qituvchi faqat davomat oladi va boshqaning davomatini tahrirlay olmaydi.
    """
    reset_database()
    _set_name(701, "Direktor", "Karimov")
    _set_name(702, "Kurator", "Aliyeva")
    _set_name(703, "Fan", "Oqituvchisi")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Rollar maktabi"}, headers=as_user(701)).json()["school"]
    for tg_id, subject in ((702, "Matematika"), (703, "Fizika")):
        joined = client.post("/api/schools/join", json={"code": school["code"], "subjects": [subject]}, headers=as_user(tg_id))
        assert joined.status_code == 200, joined.text

    # Direktor fansiz profilini saqlay oladi, o'qituvchi esa yo'q
    assert client.put("/api/schools/me/profile", json={"subjects": []}, headers=as_user(701)).status_code == 200
    assert client.put("/api/schools/me/profile", json={"subjects": []}, headers=as_user(703)).status_code == 422

    # Sinfni faqat direktor yaratadi
    assert client.post("/api/classes", json={"name": "9-A"}, headers=as_user(702)).status_code == 403
    created = client.post("/api/classes", json={"name": "9-A"}, headers=as_user(701)).json()
    cid = created["id"]
    assert created["curator"] is None and created["is_director"] is True

    # Kurator tayinlanmaguncha o'qituvchi o'quvchi qo'sha olmaydi
    assert client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(702)).status_code == 403
    # Kuratorni faqat direktor tayinlaydi; maktabda yo'q odamni bo'lmaydi
    assert client.put(f"/api/classes/{cid}/curator", json={"user_id": 1}, headers=as_user(702)).status_code == 403
    users = {u["name"]: u for u in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    session = SessionLocal()
    try:
        from backend.database import User

        curator_id = session.scalar(main.select(User.id).where(User.tg_id == 702))
    finally:
        session.close()
    assert client.put(f"/api/classes/{cid}/curator", json={"user_id": 99999}, headers=as_user(701)).status_code == 404
    assigned = client.put(f"/api/classes/{cid}/curator", json={"user_id": curator_id}, headers=as_user(701)).json()
    assert assigned["curator"]["name"] == "Kurator Aliyeva", assigned

    # Kurator o'quvchi qo'shadi; boshqa o'qituvchi qo'sha olmaydi
    detail = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(702)).json()
    assert detail["can_manage"] and detail["is_curator"]
    assert client.post(f"/api/classes/{cid}/students", json={"full_name": "Begona"}, headers=as_user(703)).status_code == 403
    assert client.put(f"/api/classes/{cid}", json={"name": "9-B"}, headers=as_user(702)).status_code == 403

    # Sinfi yo'q o'qituvchi sinfni ko'radi va davomat oladi
    listed = client.get("/api/classes", headers=as_user(703)).json()
    assert listed[0]["name"] == "9-A" and not listed[0]["can_manage"], listed
    taken = client.post(f"/api/classes/{cid}/attendance", json={"subject": "Fizika"}, headers=as_user(703)).json()
    assert taken["counts"]["present"] == 1

    # Boshqaning davomatini tahrirlay olmaydi; olgan o'qituvchi va direktor — mumkin
    sid = detail["students"][0]["id"]
    assert client.put(f"/api/attendance/{taken['id']}", json={"marks": {sid: "absent"}}, headers=as_user(702)).status_code == 403
    assert client.put(f"/api/attendance/{taken['id']}", json={"marks": {sid: "absent"}}, headers=as_user(703)).status_code == 200
    assert client.put(f"/api/attendance/{taken['id']}", json={"marks": {sid: "late"}}, headers=as_user(701)).status_code == 200
    history = client.get(f"/api/classes/{cid}", headers=as_user(702)).json()["attendances"]
    assert history[0]["teacher_name"] == "Fan Oqituvchisi" and history[0]["can_edit"] is False, history

    # Kuratorni olib tashlash
    removed = client.put(f"/api/classes/{cid}/curator", json={"user_id": None}, headers=as_user(701)).json()
    assert removed["curator"] is None
    assert users  # a'zolar ro'yxati olindi


def test_qoshilishda_kuratormisiz_savoli():
    """Kurator o'z sinfini tanlab qo'shiladi (fan ixtiyoriy); oddiy o'qituvchi uchun fan majburiy."""
    reset_database()
    _set_name(701, "Direktor", "Karimov")
    _set_name(702, "Kurator", "Aliyeva")
    _set_name(703, "Ikkinchi", "Kurator")
    _set_name(704, "Fan", "Ustoz")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Kurator maktabi"}, headers=as_user(701)).json()["school"]
    first = client.post("/api/classes", json={"name": "5-A"}, headers=as_user(701)).json()
    client.post("/api/classes", json={"name": "5-B"}, headers=as_user(701))

    # Kod bo'yicha: maktab nomi va kuratorsiz sinflar
    lookup = client.post("/api/schools/lookup", json={"code": school["code"]}, headers=as_user(702)).json()
    assert lookup["school"]["name"] == "Kurator maktabi"
    assert [c["name"] for c in lookup["open_classes"]] == ["5-A", "5-B"], lookup
    assert client.post("/api/schools/lookup", json={"code": "NOTOGRI1"}, headers=as_user(702)).status_code == 404

    # Kurator: sinf tanlamasa — so'raladi; tanlasa — fansiz ham qo'shiladi va kurator bo'ladi
    no_class = client.post("/api/schools/join", json={"code": school["code"], "is_curator": True}, headers=as_user(702))
    assert no_class.status_code == 422, no_class.text
    joined = client.post("/api/schools/join", json={"code": school["code"], "is_curator": True,
                                                    "curator_class_id": first["id"]}, headers=as_user(702))
    assert joined.status_code == 200, joined.text
    detail = client.get(f"/api/classes/{first['id']}", headers=as_user(702)).json()
    assert detail["is_curator"] and detail["curator"]["name"] == "Kurator Aliyeva", detail

    # Band sinfni boshqa kurator tanlay olmaydi; ro'yxatda u endi yo'q
    taken = client.post("/api/schools/join", json={"code": school["code"], "is_curator": True,
                                                   "curator_class_id": first["id"]}, headers=as_user(703))
    assert taken.status_code == 409, taken.text
    lookup = client.post("/api/schools/lookup", json={"code": school["code"]}, headers=as_user(703)).json()
    assert [c["name"] for c in lookup["open_classes"]] == ["5-B"]

    # Kurator profilini fansiz saqlay oladi
    assert client.put("/api/schools/me/profile", json={"subjects": []}, headers=as_user(702)).status_code == 200

    # Oddiy o'qituvchi: fansiz qo'shilmaydi
    assert client.post("/api/schools/join", json={"code": school["code"]}, headers=as_user(704)).status_code == 422
    assert client.post("/api/schools/join", json={"code": school["code"], "subjects": ["Fizika"]},
                       headers=as_user(704)).status_code == 200


def test_direktor_oz_maktabiga_kodsiz_qaytadi():
    """Yolg'iz direktor chiqib ketgan bo'lsa, o'z maktabiga kodsiz, direktor sifatida qaytadi."""
    reset_database()
    _set_name(701, "Direktor", "Karimov")
    _set_name(702, "Begona", "Odam")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Qaytish maktabi"}, headers=as_user(701)).json()["school"]
    assert client.post("/api/schools/leave", json={"code": school["code"]}, headers=as_user(701)).status_code == 200

    listed = client.get("/api/schools", params={"region": "Toshkent shahri"}, headers=as_user(701)).json()
    assert listed[0]["is_mine"] is True
    assert client.get("/api/schools", params={"region": "Toshkent shahri"}, headers=as_user(702)).json()[0]["is_mine"] is False

    # Begona odam kodsiz kira olmaydi
    assert client.post(f"/api/schools/{school['id']}/return", headers=as_user(702)).status_code == 403

    back = client.post(f"/api/schools/{school['id']}/return", headers=as_user(701))
    assert back.status_code == 200 and back.json()["school"]["my_role"] == "owner", back.text
    assert client.post(f"/api/schools/{school['id']}/return", headers=as_user(701)).status_code == 409

    # Kod bilan kirsa ham — direktor sifatida (fan so'ralmaydi)
    client.post("/api/schools/leave", json={"code": school["code"]}, headers=as_user(701))
    again = client.post("/api/schools/join", json={"code": school["code"]}, headers=as_user(701))
    assert again.status_code == 200 and again.json()["school"]["my_role"] == "owner", again.text


def test_oqituvchini_shaxsiy_havola_bilan_kodsiz_qoshish():
    """Admin bir martalik havola yaratadi; o'qituvchi kodsiz qo'shiladi; havola ikkinchi marta ishlamaydi."""
    reset_database()
    _set_name(701, "Admin", "Karimov")
    _set_name(702, "Yangi", "Ustoz")
    _set_name(703, "Ikkinchi", "Odam")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Havola maktabi"}, headers=as_user(701)).json()["school"]
    classes = client.post("/api/classes", json={"name": "8-A"}, headers=as_user(701)).json()

    created = client.post("/api/schools/invites", headers=as_user(701))
    assert created.status_code == 200, created.text
    invite = created.json()
    assert invite["link"] == f"https://t.me/test_platform_bot?start=inv_{invite['token']}", invite
    assert school["code"] not in invite["link"], "havolada maktab kodi bo'lmasligi kerak"

    # Havola ma'lumoti: maktab, kim taklif qilgan, kuratorsiz sinflar
    info = client.get(f"/api/schools/invites/{invite['token']}", headers=as_user(702)).json()
    assert info["school"]["name"] == "Havola maktabi" and info["invited_by"] == "Admin Karimov"
    assert [c["name"] for c in info["open_classes"]] == ["8-A"]

    # Kodsiz qo'shilish — kurator sifatida
    accepted = client.post(f"/api/schools/invites/{invite['token']}/accept",
                           json={"is_curator": True, "curator_class_id": classes["id"]}, headers=as_user(702))
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/classes/{classes['id']}", headers=as_user(702)).json()["is_curator"]

    # Bir martalik: boshqa odam shu havola bilan kira olmaydi
    again = client.post(f"/api/schools/invites/{invite['token']}/accept", json={"subjects": ["Fizika"]}, headers=as_user(703))
    assert again.status_code == 410, again.text
    assert client.get(f"/api/schools/invites/{invite['token']}", headers=as_user(703)).status_code == 410

    # Faqat admin havola yaratadi; muddati o'tgan havola ishlamaydi
    assert client.post("/api/schools/invites", headers=as_user(702)).status_code == 403
    second = client.post("/api/schools/invites", headers=as_user(701)).json()
    session = SessionLocal()
    try:
        from backend.database import SchoolInvite

        row = session.scalar(main.select(SchoolInvite).where(SchoolInvite.token == second["token"]))
        row.expires_at = utcnow() - timedelta(minutes=1)
        session.commit()
    finally:
        session.close()
    expired = client.post(f"/api/schools/invites/{second['token']}/accept", json={"subjects": ["Fizika"]}, headers=as_user(703))
    assert expired.status_code == 410, expired.text
    assert client.get("/api/schools/invites/YOQTOKEN", headers=as_user(703)).status_code == 404


def test_davomat_olish_tahrirlash_va_vaqtlar():
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]

    # O'quvchisiz sinfda davomat olinmaydi
    assert client.post(f"/api/classes/{cid}/attendance", json={"subject": "Matematika"}, headers=as_user(701)).status_code == 400

    for name in ("Islom Karimov", "Ali Valiyev", "Dilnoza Aliyeva", "Sardor Raximov"):
        detail = client.post(f"/api/classes/{cid}/students", json={"full_name": name}, headers=as_user(701)).json()
    ids = {s["full_name"]: s["id"] for s in detail["students"]}

    # Faqat yo'q/sababli belgilanadi — qolgani avtomatik "bor"
    taken = client.post(
        f"/api/classes/{cid}/attendance",
        json={"subject": "Matematika", "marks": {ids["Ali Valiyev"]: "absent", ids["Dilnoza Aliyeva"]: "excused"}},
        headers=as_user(701),
    )
    assert taken.status_code == 200, taken.text
    attendance = taken.json()
    assert attendance["counts"] == {"present": 2, "absent": 1, "excused": 1, "late": 0}, attendance["counts"]
    assert attendance["updated_at"] is None

    # Noto'g'ri holat qabul qilinmaydi
    bad = client.post(f"/api/classes/{cid}/attendance", json={"marks": {ids["Ali Valiyev"]: "kasal"}}, headers=as_user(701))
    assert bad.status_code == 422

    # Tahrir: Ali yarim darsda keldi — "kechikdi". Olingan vaqt o'zgarmaydi, tahrir vaqti yoziladi
    edited = client.put(f"/api/attendance/{attendance['id']}", json={"marks": {ids["Ali Valiyev"]: "late"}},
                        headers=as_user(701)).json()
    assert edited["taken_at"] == attendance["taken_at"], (edited["taken_at"], attendance["taken_at"])
    assert edited["updated_at"] is not None
    assert edited["counts"]["late"] == 1 and edited["counts"]["absent"] == 0

    # O'quvchi sinfdan chiqarilsa ham eski davomatda turadi
    client.delete(f"/api/classes/{cid}/students/{ids['Sardor Raximov']}", headers=as_user(701))
    again = client.get(f"/api/attendance/{attendance['id']}", headers=as_user(701)).json()
    assert any(mark["name"] == "Sardor Raximov" for mark in again["marks"])

    # Tarix sinf sahifasida
    history = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["attendances"]
    assert len(history) == 1 and history[0]["subject"] == "Matematika"

    # O'quvchi profili: statistika va darslar tarixi (kechikkan ham qatnashgan hisoblanadi)
    profile = client.get(f"/api/classes/{cid}/students/{ids['Ali Valiyev']}", headers=as_user(701)).json()
    assert profile["stats"]["late"] == 1 and profile["stats"]["total"] == 1 and profile["stats"]["rate"] == 100, profile
    assert profile["history"][0]["status"] == "late" and profile["history"][0]["subject"] == "Matematika"
    dilnoza = client.get(f"/api/classes/{cid}/students/{ids['Dilnoza Aliyeva']}", headers=as_user(701)).json()
    assert dilnoza["stats"]["excused"] == 1 and dilnoza["stats"]["rate"] == 0, dilnoza["stats"]
    students = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"]
    assert all("stats" in student for student in students)

    # Begona o'qituvchi ko'ra olmaydi
    _set_name(709, "Begona", "Odam")
    assert client.get(f"/api/attendance/{attendance['id']}", headers=as_user(709)).status_code in (403, 404)


def test_davomat_matn_rasm_va_pdf():
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    detail = client.post(f"/api/classes/{cid}/students", json={"full_name": "Фаррух Комилжонов"}, headers=as_user(701)).json()
    sid = detail["students"][0]["id"]
    # Dars vaqti: tugash boshlanishdan keyin bo'lishi shart, format HH:MM
    for start, end in (("11:45", "11:00"), ("9:00", "09:45"), ("25:00", "25:45")):
        bad = client.post(f"/api/classes/{cid}/attendance", json={"lesson_start": start, "lesson_end": end},
                          headers=as_user(701))
        assert bad.status_code == 422, (start, end, bad.status_code)
    attendance = client.post(f"/api/classes/{cid}/attendance",
                             json={"subject": "Ingliz tili", "marks": {sid: "absent"},
                                   "lesson_start": "11:00", "lesson_end": "11:45"},
                             headers=as_user(701)).json()
    assert (attendance["lesson_start"], attendance["lesson_end"]) == ("11:00", "11:45"), attendance
    history = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["attendances"]
    assert history[0]["lesson_start"] == "11:00" and history[0]["lesson_end"] == "11:45", history[0]

    text = client.get(f"/api/attendance/{attendance['id']}/text", headers=as_user(701)).json()["text"]
    assert "10/2 — Ingliz tili darsi" in text and "Aziza Karimova" in text, text
    assert " · 11:00–11:45" in text, text

    # Tahrirda dars vaqti o'zgaradi
    edited = client.put(f"/api/attendance/{attendance['id']}", json={"lesson_start": "12:00", "lesson_end": "12:45"},
                        headers=as_user(701)).json()
    assert (edited["lesson_start"], edited["lesson_end"]) == ("12:00", "12:45") and edited["updated_at"], edited
    assert "❌ Yo'q:\n1. Фаррух Комилжонов" in text, text
    assert "Ota-onalar" not in text

    pdf = client.get(f"/api/attendance/{attendance['id']}/file", params={"format": "pdf"}, headers=as_user(701))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    image = client.get(f"/api/attendance/{attendance['id']}/file", params={"format": "image"}, headers=as_user(701))
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")

    # Bot orqali yuborish (Telegram o'rniga yozib olamiz)
    sent = []
    originals = (main._send_bot_text, main._send_pdf_to_telegram, main._send_photo_to_telegram)
    main._send_bot_text = lambda chat_id, body: sent.append(("text", chat_id)) or True
    main._send_pdf_to_telegram = lambda chat_id, name, data, caption="": sent.append(("pdf", chat_id))
    main._send_photo_to_telegram = lambda chat_id, name, data, caption="": sent.append(("image", chat_id))
    try:
        for fmt in ("text", "image", "pdf"):
            assert client.post(f"/api/attendance/{attendance['id']}/send", params={"format": fmt},
                               headers=as_user(701)).status_code == 200
    finally:
        main._send_bot_text, main._send_pdf_to_telegram, main._send_photo_to_telegram = originals
    assert sent == [("text", 701), ("image", 701), ("pdf", 701)], sent


def test_oquvchilarni_excel_va_matndan_import():
    """Tayyor ro'yxat Excel/CSV/matndan o'qiladi: sarlavha, raqam, sana tashlanadi; takror qo'shilmaydi."""
    from datetime import date as _date

    from openpyxl import Workbook

    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Karimov Islom"}, headers=as_user(701))

    # Excel: sarlavha + "№ | Familiya | Ism | Tug'ilgan sana" + bo'sh qator
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["№", "Familiya", "Ism", "Tug'ilgan sana"])
    sheet.append([1, "Karimov", "Islom", _date(2010, 5, 14)])
    sheet.append([2, "Комилжонов", "Фаррух", None])
    sheet.append([None, None, None, None])
    sheet.append([3, "Aliyeva", "Dilnoza", "14.05.2011"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    encoded = base64.b64encode(buffer.getvalue()).decode()

    preview = client.post(f"/api/classes/{cid}/students/import/preview",
                          json={"file_base64": encoded, "file_name": "10-2 sinf.xlsx"}, headers=as_user(701))
    assert preview.status_code == 200, preview.text
    data = preview.json()
    assert data["names"] == ["Комилжонов Фаррух", "Aliyeva Dilnoza"], data
    assert data["already_in_class"] == ["Karimov Islom"], data

    # Hali hech narsa saqlanmagan — faqat ko'rib chiqish
    assert len(client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"]) == 1

    added = client.post(f"/api/classes/{cid}/students/bulk", json={"names": data["names"] + ["Karimov Islom"]},
                        headers=as_user(701)).json()
    assert added["added"] == 2 and len(added["class"]["students"]) == 3, added

    # Excel'dan nusxalab joylangan matn (TAB) va raqamlangan ro'yxat
    pasted = "F.I.Sh\n1. Sardor Raximov\n2) Malika Yusupova\nToshev\tJasur\n\n"
    names = client.post(f"/api/classes/{cid}/students/import/preview", json={"text": pasted}, headers=as_user(701)).json()["names"]
    assert names == ["Sardor Raximov", "Malika Yusupova", "Toshev Jasur"], names

    # CSV (nuqta-vergul bilan, kirillcha)
    csv_bytes = "ФИО;Телефон\nНилуфар Каримова;+998 90 123 45 67\n".encode("utf-8-sig")
    names = client.post(f"/api/classes/{cid}/students/import/preview",
                        json={"file_base64": base64.b64encode(csv_bytes).decode(), "file_name": "royxat.csv"},
                        headers=as_user(701)).json()["names"]
    assert names == ["Нилуфар Каримова"], names

    # Eski .xls va bo'sh ro'yxat — tushunarli xato
    old = client.post(f"/api/classes/{cid}/students/import/preview",
                      json={"file_base64": base64.b64encode(b"xx").decode(), "file_name": "a.xls"}, headers=as_user(701))
    assert old.status_code == 422 and ".xlsx" in old.json()["detail"]
    empty = client.post(f"/api/classes/{cid}/students/import/preview", json={"text": "№\n1\n2\n"}, headers=as_user(701))
    assert empty.status_code == 422


def test_maktab_kodini_taxmin_qilib_topish_toxtatiladi():
    reset_database()
    main._school_code_failures.clear()
    _set_name(701, "Aziza", "Karimova")
    _set_name(709, "Begona", "Odam")
    school = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Maktab"}, headers=as_user(701)).json()["school"]

    for attempt in range(5):
        response = client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": f"WRONG{attempt:03d}", "school_id": school["id"]},
                               headers=as_user(709))
        assert response.status_code == 403, response.text

    # 6-urinish — hatto to'g'ri kod bilan ham vaqtincha to'xtatiladi
    blocked = client.post("/api/schools/join", json={"subjects": ["Matematika"], "code": school["code"], "school_id": school["id"]}, headers=as_user(709))
    assert blocked.status_code == 429, blocked.text
    main._school_code_failures.clear()


def test_maktab_notogri_joylashuv_va_ismsiz_ochilmaydi():
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    bad = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Urgut tumani",
                                            "name": "Xato"}, headers=as_user(701))
    assert bad.status_code == 422, bad.text

    nameless = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Chilonzor tumani",
                                                 "name": "Maktab"}, headers=as_user(709))
    # DEV rejimida yangi foydalanuvchining nomi "Dev 709" — ism bor; bo'sh ism bilan sinaymiz
    session = SessionLocal()
    try:
        from backend.database import User

        user = session.scalar(main.select(User).where(User.tg_id == 709))
        if nameless.status_code == 200:
            assert user.full_name.strip(), "ismsiz maktab ochildi"
    finally:
        session.close()


def test_testni_qayta_ishlab_bolmaydi():
    """Tugatilgan testni qayta boshlashga yo'l qo'yilmaydi (reyting soxtalashmasin)."""
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={"answers": {"1": "A"}},
        headers=as_user(901),
    )

    again = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    assert again.status_code == 409, again.text
    assert "allaqachon" in again.json()["detail"]

    # O'qituvchi faqat bitta natija ko'radi
    rows = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()["rows"]
    assert len(rows) == 1, rows


def test_yakunlangan_urinish_ikki_marta_yakunlanmaydi():
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    first = client.post(
        f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901)
    )
    second = client.post(
        f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901)
    )

    assert first.status_code == 200
    assert second.status_code == 400, second.text


def test_boshqa_oquvchining_urinishini_yakunlab_bolmaydi():
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    response = client.post(
        f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(902)
    )
    assert response.status_code == 404, response.text


# ========== 5) Javoblar yo'qolmasligi ==========


def test_javoblar_saqlanadi_va_tiklanadi():
    """Mini App yopilib qayta ochilsa, o'quvchi javoblari joyida bo'ladi."""
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    assert started["answers"] == {}

    saved = client.post(
        f"/api/attempts/{started['attempt_id']}/answers",
        json={"answers": {"1": "B", "3": "A,C"}},
        headers=as_user(901),
    )
    assert saved.status_code == 200, saved.text

    # Ilova qayta ochildi
    resumed = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    assert resumed["attempt_id"] == started["attempt_id"], "yangi urinish ochilib ketdi"
    assert resumed["answers"] == {"1": "B", "3": "A,C"}, resumed["answers"]


def test_yakunlashda_saqlangan_javoblar_hisobga_olinadi():
    """Oxirgi so'rov yetib kelmasa ham, avval saqlangan javoblar yo'qolmaydi."""
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    client.post(
        f"/api/attempts/{started['attempt_id']}/answers",
        json={"answers": {"1": "B", "2": "C"}},
        headers=as_user(901),
    )

    # Yakunlashda faqat oxirgi savolning javobi yuborildi
    result = finish_and_grade(
        started['attempt_id'],
        json={"answers": {"4": "2 ildiz 3"}},
        headers=as_user(901),
    )

    # 1, 2 (saqlangan) va 4 (yuborilgan) — uchtasi to'g'ri
    assert result["correct"] == 3, result


def test_vaqti_tugagan_urinish_saqlangan_javoblar_boyicha_tekshiriladi():
    """O'quvchi yakunlashni bosmasa ham, mehnati yo'qolmaydi."""
    reset_database()
    register_teacher()
    created = create_test(duration=30)

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    client.post(
        f"/api/attempts/{started['attempt_id']}/answers",
        json={"answers": {"1": "B", "2": "C"}},
        headers=as_user(901),
    )

    # Urinish boshlanish vaqtini orqaga suramiz — ya'ni vaqti tugagan
    session = SessionLocal()
    try:
        attempt = session.get(Attempt, started["attempt_id"])
        attempt.started_at = utcnow() - timedelta(minutes=31)
        session.commit()
    finally:
        session.close()

    assert main._finish_expired_tests_once() is None  # fon vazifasi bir qadam

    rows = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()["rows"]
    assert len(rows) == 1 and rows[0]["correct"] == 2, rows


# ========== 6) Kredit hisobi ==========


def test_kredit_manfiyga_tushmaydi():
    """Boshqa joyda kredit sarflansa ham, balans hech qachon manfiy bo'lmaydi."""
    reset_database()
    register_teacher()

    session = SessionLocal()
    try:
        teacher = session.scalars(main.select(Teacher)).one()
        teacher.credits = 1
        session.commit()
    finally:
        session.close()

    open_test = create_test()
    closed_test = create_test(enrollment_type="closed")

    # O'quvchi testni boshladi (kredit hali yechilmagan)
    started = client.post(f"/api/tests/{open_test['code']}/start", json={}, headers=as_user(901)).json()
    assert credits_of() == 1

    # O'qituvchi oradagi vaqtda yopiq testga javob kiritib, kreditni sarfladi
    client.post(
        f"/api/teacher/tests/{closed_test['id']}/students",
        json={"names": ["Hasan Hasanov"]},
        headers=as_user(555),
    )
    student_id = client.get(f"/api/teacher/tests/{closed_test['id']}", headers=as_user(555)).json()[
        "students"
    ][0]["id"]
    client.post(
        f"/api/teacher/tests/{closed_test['id']}/students/{student_id}/answers",
        json={"answers": {"1": "B"}},
        headers=as_user(555),
    )
    assert credits_of() == 0

    # Endi o'quvchi yakunladi — kredit 0'da qolishi kerak, -1 emas
    client.post(
        f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901)
    )
    assert credits_of() == 0, f"kredit manfiyga tushdi: {credits_of()}"


def test_kredit_tugaganda_test_boshlanmaydi():
    reset_database()
    register_teacher()

    session = SessionLocal()
    try:
        teacher = session.scalars(main.select(Teacher)).one()
        teacher.credits = 0
        session.commit()
    finally:
        session.close()

    created = create_test()
    response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    assert response.status_code == 402, response.text


# ========== 7) Payme to'lovlari ==========


def test_payme_bosh_kalit_bilan_otmaydi():
    """PAYME_SECRET_KEY sozlanmagan bo'lsa, hech kim webhook'dan o'tolmaydi."""
    reset_database()

    original = main.PAYME_SECRET_KEY
    main.PAYME_SECRET_KEY = ""
    try:
        # Bo'sh kalit bilan "to'landi" deb yuborish — ilgari bu o'tib ketardi,
        # chunki hmac.compare_digest("", "") -> True
        answer = payme("CheckTransaction", {"id": "soxta"}, secret="")
        assert answer["error"]["code"] == -32504, answer
    finally:
        main.PAYME_SECRET_KEY = original


def test_payme_notogri_kalit_bilan_otmaydi():
    reset_database()
    answer = payme("CheckTransaction", {"id": "soxta"}, secret="boshqa_kalit")
    assert answer["error"]["code"] == -32504, answer


def test_kredit_xaridi_tolovdan_keyin_yoziladi():
    """Kredit faqat Payme "PerformTransaction" yuborgandan keyin qo'shiladi."""
    reset_database()
    register_teacher()
    before = credits_of()

    purchase = client.post(
        "/api/teacher/credit-purchases", json={"credits": 50}, headers=as_user(555)
    ).json()
    assert purchase["amount"] == 50 * 800, purchase
    assert credits_of() == before, "to'lovdan oldin kredit qo'shilib ketdi"

    account = {"purchase_id": str(purchase["id"])}
    amount = purchase["amount"] * 100

    assert payme("CheckPerformTransaction", {"account": account, "amount": amount})["result"]["allow"]
    payme("CreateTransaction", {"id": "tx-1", "time": 1, "account": account, "amount": amount})
    assert credits_of() == before, "CreateTransaction kreditni qo'shib yubordi"

    payme("PerformTransaction", {"id": "tx-1"})
    assert credits_of() == before + 50, credits_of()

    # Bekor qilinsa — orqaga olinadi
    payme("CancelTransaction", {"id": "tx-1", "reason": 5})
    assert credits_of() == before, credits_of()


def test_testga_tolov_balansga_faqat_tolovdan_keyin_tushadi():
    """Mock to'lov yo'q: o'qituvchi balansi haqiqiy to'lovdan keyin oshadi."""
    reset_database()
    register_teacher()
    created = create_test(price=10_000)

    assert balance_of() == 0
    assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).status_code == 402

    payment = client.post(f"/api/tests/{created['code']}/pay", json={}, headers=as_user(901)).json()
    assert payment["paid"] is False and payment["checkout_url"].startswith("https://checkout.paycom.uz/")
    assert balance_of() == 0, "to'lovdan oldin balans oshib ketdi"

    # Hali to'lanmagan — test ochilmaydi
    assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).status_code == 402

    account = {"payment_id": str(payment["payment_id"])}
    amount = payment["amount"] * 100
    payme("CreateTransaction", {"id": "tx-test", "time": 1, "account": account, "amount": amount})
    payme("PerformTransaction", {"id": "tx-test"})

    # 30% platformaga, 70% o'qituvchiga
    assert balance_of() == 7000, balance_of()
    assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).status_code == 200


def test_payme_notogri_summa_qabul_qilinmaydi():
    reset_database()
    register_teacher()
    purchase = client.post(
        "/api/teacher/credit-purchases", json={"credits": 10}, headers=as_user(555)
    ).json()

    answer = payme(
        "CheckPerformTransaction",
        {"account": {"purchase_id": str(purchase["id"])}, "amount": 1},
    )
    assert answer["error"]["code"] == -31001, answer


def test_payme_buzilgan_sorov_500_bermaydi():
    reset_database()
    token = base64.b64encode(b"Paycom:test_secret_key").decode()
    response = client.post(
        "/api/payme/webhook", headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
        content=b"{buzilgan",
    )
    assert response.status_code == 200 and "error" in response.json(), response.text


def test_majburiy_obuna_bot_kanalni_kormasa_test_yaratilmaydi():
    """O'qituvchi xatoni DARHOL biladi, testni tarqatib yuborgandan keyin emas."""
    reset_database()
    register_teacher()

    def bot_cannot_see(channel, tg_id):
        raise main.ChannelUnavailable("Bad Request: chat not found")

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = bot_cannot_see
    try:
        response = client.post(
            "/api/teacher/tests",
            json={
                "title": "Obunali test",
                "subject": "Matematika",
                "mode": "key",
                "enrollment_type": "open",
                "price": 0,
                "required_channel": "@yoqkanal",
                "key_questions": [
                    {"number": 1, "question_type": "single", "option_count": 4, "correct": "A"}
                ],
            },
            headers=as_user(555),
        )
        assert response.status_code == 422, response.text
        assert "administrator qilib qo'shing" in response.json()["detail"], response.text
    finally:
        main._ask_telegram_chat_member = original


def test_majburiy_obuna_obunasizni_toxtatadi():
    """Obuna bo'lmagan o'quvchi 451 oladi, obuna bo'lgani esa testni boshlaydi."""
    reset_database()
    register_teacher()

    answers = {"status": "left"}

    def fake_member(channel, tg_id):
        if tg_id == BOT_USER_ID:
            return {"status": "administrator"}
        return dict(answers)

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = fake_member
    try:
        created = create_test(required_channel="@mening_kanalim")

        # Kanal "@" bilan saqlanadi
        info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
        assert info["required_channel"] == "@mening_kanalim", info

        # Obuna emas -> to'xtatiladi
        blocked = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
        assert blocked.status_code == 451, blocked.text

        # Obuna bo'ldi -> o'tadi
        answers["status"] = "member"
        assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).status_code == 200
    finally:
        main._ask_telegram_chat_member = original


def test_adminlar_kanali_har_bir_testda_majburiy():
    """@sukhrobmath (adminlar kanali) har bir testda majburiy; o'qituvchi kanali qo'shilsa — ikkalasi ham."""
    reset_database()
    register_teacher()
    main.DEFAULT_REQUIRED_CHANNELS = ["@sukhrobmath"]
    subscribed = set()

    def fake_member(channel, tg_id):
        if tg_id == BOT_USER_ID:
            return {"status": "administrator"}
        return {"status": "member" if channel in subscribed else "left"}

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = fake_member
    try:
        plain = create_test()
        own = create_test(required_channel="@ustoz_kanali")
        same = create_test(required_channel="@SukhrobMath")  # takrorlanmaydi

        assert client.get(f"/api/tests/{plain['code']}", headers=as_user(901)).json()["required_channels"] == ["@sukhrobmath"]
        assert client.get(f"/api/tests/{own['code']}", headers=as_user(901)).json()["required_channels"] == ["@sukhrobmath", "@ustoz_kanali"]
        assert client.get(f"/api/tests/{same['code']}", headers=as_user(901)).json()["required_channels"] == ["@sukhrobmath"]
        assert client.get("/api/me", headers=as_user(901)).json()["default_channels"] == ["@sukhrobmath"]

        # Kanal qo'shilmagan testda ham adminlar kanaliga obuna shart
        blocked = client.post(f"/api/tests/{plain['code']}/start", json={}, headers=as_user(901))
        assert blocked.status_code == 451 and "@sukhrobmath" in blocked.json()["detail"], blocked.text

        subscribed.add("@sukhrobmath")
        assert client.post(f"/api/tests/{plain['code']}/start", json={}, headers=as_user(901)).status_code == 200
        # O'qituvchi kanaliga obuna bo'lmaguncha — to'xtatiladi, xabarda faqat yetishmagani
        blocked = client.post(f"/api/tests/{own['code']}/start", json={}, headers=as_user(901))
        assert blocked.status_code == 451 and "@ustoz_kanali" in blocked.json()["detail"], blocked.text
        assert "@sukhrobmath" not in blocked.json()["detail"], blocked.text
        subscribed.add("@ustoz_kanali")
        assert client.post(f"/api/tests/{own['code']}/start", json={}, headers=as_user(901)).status_code == 200
    finally:
        main._ask_telegram_chat_member = original
        main.DEFAULT_REQUIRED_CHANNELS = []


def test_kirishda_adminlar_kanaliga_obuna_tekshiriladi():
    """Mini App/bot kirishida: obuna bo'lmasa — kanal ro'yxati; tekshirib bo'lmasa — to'smaydi."""
    reset_database()
    main.DEFAULT_REQUIRED_CHANNELS = ["@sukhrobmath"]
    original_member, original_token = main._ask_telegram_chat_member, main.BOT_TOKEN
    main.BOT_TOKEN = "123:test"
    state = {"status": "left"}

    def fake_member(channel, tg_id):
        if state["status"] == "error":
            raise main.ChannelUnavailable("Bad Request: member list is inaccessible")
        return {"status": state["status"]}

    main._ask_telegram_chat_member = fake_member
    try:
        assert client.get("/api/me/subscription", headers=as_user(901)).json() == {"missing": ["@sukhrobmath"]}
        state["status"] = "member"
        assert client.get("/api/me/subscription", headers=as_user(901)).json() == {"missing": []}
        # Tasdiqlangan obuna 10 daqiqa eslab qolinadi
        state["status"] = "left"
        assert client.get("/api/me/subscription", headers=as_user(901)).json() == {"missing": []}
        # Bot kanalni tekshira olmasa — kirish to'silmaydi
        state["status"] = "error"
        assert client.get("/api/me/subscription", headers=as_user(902)).json() == {"missing": []}
    finally:
        main._ask_telegram_chat_member, main.BOT_TOKEN = original_member, original_token
        main.DEFAULT_REQUIRED_CHANNELS = []


def test_majburiy_obuna_bot_kira_olmasa_oquvchi_otkazilmaydi():
    """
    Eng muhim tuzatish: ilgari bot kanalga kira olmasa, HAMMA o'tib ketardi —
    ya'ni "majburiy obuna" aslida hech kimni to'smasdi.
    """
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "administrator" if tg_id == BOT_USER_ID else "member"}

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = ok
    try:
        created = create_test(required_channel="@kanal")
    finally:
        main._ask_telegram_chat_member = original

    # Endi bot kanaldan chiqarib yuborilgan
    def cannot(channel, tg_id):
        raise main.ChannelUnavailable("Bad Request: member list is inaccessible")

    main._ask_telegram_chat_member = cannot
    try:
        response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
        assert response.status_code == 424, f"o'quvchi o'tkazib yuborildi: {response.status_code}"
        assert "administrator" in response.json()["detail"]
    finally:
        main._ask_telegram_chat_member = original


def test_majburiy_obuna_tekshirib_bolmasa_ham_otkazilmaydi():
    """Obuna tasdiqlanmaguncha test boshlanmaydi — Telegram javob bermasa ham (503, qayta urinadi)."""
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "administrator" if tg_id == BOT_USER_ID else "member"}

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = ok
    try:
        created = create_test(required_channel="@kanal")
    finally:
        main._ask_telegram_chat_member = original

    def timeout(channel, tg_id):
        raise TimeoutError("Telegram javob bermadi")

    main._ask_telegram_chat_member = timeout
    try:
        response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
        assert response.status_code == 503, response.text
    finally:
        main._ask_telegram_chat_member = original


def test_kanal_nomi_har_xil_yozilsa_ham_bir_xil_saqlanadi():
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "administrator" if tg_id == BOT_USER_ID else "member"}

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = ok
    try:
        for written in ("@kanalim", "kanalim", "t.me/kanalim", "https://t.me/kanalim"):
            created = create_test(required_channel=written)
            info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
            assert info["required_channel"] == "@kanalim", f"{written!r} -> {info['required_channel']}"
    finally:
        main._ask_telegram_chat_member = original


def _fake_telegram(statuses: dict, error: Exception | None = None):
    """
    _ask_telegram_chat_member o'rniga: statuses[tg_id] holatini qaytaradi
    (bot uchun ham). error berilsa, botdan boshqa hamma uchun shuni ko'taradi.
    """
    original = main._ask_telegram_chat_member

    def fake(channel, tg_id):
        if error is not None and tg_id != BOT_USER_ID:
            raise error
        return {"status": statuses.get(tg_id, "left")}

    main._ask_telegram_chat_member = fake
    return lambda: setattr(main, "_ask_telegram_chat_member", original)


def test_kanal_qoshilmagan_test_tekshiruvsiz_boshlanadi():
    reset_database()
    register_teacher()
    created = create_test()

    restore = _fake_telegram({}, error=AssertionError("kanalsiz testda Telegram so'ralmasligi kerak"))
    try:
        response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    finally:
        restore()
    assert response.status_code == 200, response.text


def test_bot_admin_bolmasa_test_yaratilmaydi():
    """Bot kanal/guruhda oddiy a'zo bo'lsa — obunani tekshira olmaydi, test saqlanmaydi."""
    reset_database()
    register_teacher()

    restore = _fake_telegram({BOT_USER_ID: "member"})
    try:
        response = client.post(
            "/api/teacher/tests",
            json={
                "title": "Obunali test",
                "subject": "Matematika",
                "mode": "key",
                "required_channel": "@kanal",
                "key_questions": [{"number": 1, "question_type": "single", "option_count": 4, "correct": "A"}],
            },
            headers=as_user(555),
        )
    finally:
        restore()
    assert response.status_code == 422 and "administrator" in response.json()["detail"], response.text


def test_kanalni_tekshirib_bolmasa_test_yaratilmaydi():
    reset_database()
    register_teacher()

    original = main._ask_telegram_chat_member

    def timeout(channel, tg_id):
        raise TimeoutError("Telegram javob bermadi")

    main._ask_telegram_chat_member = timeout
    try:
        response = client.post(
            "/api/teacher/tests",
            json={
                "title": "Obunali test",
                "subject": "Matematika",
                "mode": "key",
                "required_channel": "@kanal",
                "key_questions": [{"number": 1, "question_type": "single", "option_count": 4, "correct": "A"}],
            },
            headers=as_user(555),
        )
    finally:
        main._ask_telegram_chat_member = original
    assert response.status_code == 503, response.text


def test_guruh_raqamli_id_bilan_ham_ishlaydi():
    reset_database()
    register_teacher()

    restore = _fake_telegram({BOT_USER_ID: "administrator", 901: "member"})
    try:
        created = create_test(required_channel="-1001234567890")
        info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
        assert info["required_channel"] == "-1001234567890", info
        assert client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).status_code == 200
    finally:
        restore()


def test_kanalda_umuman_yoq_oquvchi_obuna_bolishga_yuboriladi():
    """Telegram "user not found" desa — bu sozlama xatosi emas, o'quvchi obuna emas (451)."""
    reset_database()
    register_teacher()

    restore = _fake_telegram({BOT_USER_ID: "administrator"})
    try:
        created = create_test(required_channel="@kanal")
    finally:
        restore()

    restore = _fake_telegram({}, error=main.ChannelUnavailable("Bad Request: user not found"))
    try:
        response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    finally:
        restore()
    assert response.status_code == 451, response.text


def test_ishlab_bolgan_oquvchiga_obuna_emas_allaqachon_xabari_chiqadi():
    """Ilgari 409 obuna xatosi deb ko'rsatilib, "Obuna tekshirilmadi" ekrani chiqardi."""
    reset_database()
    register_teacher()

    restore = _fake_telegram({BOT_USER_ID: "administrator", 901: "member"})
    try:
        created = create_test(required_channel="@kanal")
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        finish_and_grade(started["attempt_id"], json={"answers": {"1": "B"}}, headers=as_user(901))
    finally:
        restore()

    # Endi bot kanalni ko'rmasa ham — o'quvchiga "allaqachon ishlagansiz" deyiladi
    restore = _fake_telegram({}, error=main.ChannelUnavailable("chat not found"))
    try:
        again = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    finally:
        restore()
    assert again.status_code == 409 and "allaqachon" in again.json()["detail"], again.text


def test_testlar_royxatidagi_qatnashchilar_soni_togri():
    """
    Ro'yxatdagi son natijalar jadvalidagi son bilan bir xil bo'lishi kerak.

    Sanoq tezlik uchun SQL'ga ko'chirilgan, shuning uchun u eski mantiq bilan
    bir xil ishlashini tekshiramiz: bir xil ismli o'quvchilar (harf o'lchami
    boshqa bo'lsa ham) bitta deb sanaladi.
    """
    reset_database()
    register_teacher()

    # Ochiq test: ikki o'quvchi o'zi ishlaydi
    open_test = create_test()
    for tg_id in (901, 902):
        started = client.post(
            f"/api/tests/{open_test['code']}/start", json={}, headers=as_user(tg_id)
        ).json()
        client.post(
            f"/api/attempts/{started['attempt_id']}/finish",
            json={"answers": {"1": "B"}},
            headers=as_user(tg_id),
        )

    # Yopiq test: uch ism, lekin ikkitasi aslida bitta odam (harf o'lchami farqli)
    closed_test = create_test(enrollment_type="closed")
    client.post(
        f"/api/teacher/tests/{closed_test['id']}/students",
        json={"names": ["Hasan Hasanov", "hasan hasanov", "Zuhra Karimova"]},
        headers=as_user(555),
    )
    for student in client.get(f"/api/teacher/tests/{closed_test['id']}", headers=as_user(555)).json()[
        "students"
    ]:
        client.post(
            f"/api/teacher/tests/{closed_test['id']}/students/{student['id']}/answers",
            json={"answers": {"1": "B"}},
            headers=as_user(555),
        )

    listed = {test["id"]: test["students"] for test in client.get("/api/teacher/tests", headers=as_user(555)).json()}

    assert listed[open_test["id"]] == 2, listed
    assert listed[closed_test["id"]] == 2, f"bir xil ism ikki marta sanaldi: {listed}"

    # Natijalar jadvalidagi son bilan ham mos kelishi shart
    for test_id in (open_test["id"], closed_test["id"]):
        rows = client.get(f"/api/teacher/tests/{test_id}/results", headers=as_user(555)).json()["rows"]
        assert len(rows) == listed[test_id], f"test {test_id}: ro'yxatda {listed[test_id]}, jadvalda {len(rows)}"


def test_admin_daromadni_togri_ajratadi():
    """
    Platforma daromadi = kredit sotuvi (100%) + testlardan komissiya (30%).
    O'qituvchi balansidagi pul daromad EMAS — u qarz.
    """
    reset_database()
    register_teacher()

    # 1) O'qituvchi 50 ta kredit sotib oladi: 50 x 800 = 40 000 so'm
    purchase = client.post(
        "/api/teacher/credit-purchases", json={"credits": 50}, headers=as_user(555)
    ).json()
    payme(
        "CreateTransaction",
        {"id": "tx-kredit", "time": 1, "account": {"purchase_id": str(purchase["id"])},
         "amount": purchase["amount"] * 100},
    )
    payme("PerformTransaction", {"id": "tx-kredit"})

    # 2) O'quvchi 10 000 so'mlik testga to'laydi: 3 000 platformaga, 7 000 o'qituvchiga
    created = create_test(price=10_000)
    payment = client.post(f"/api/tests/{created['code']}/pay", json={}, headers=as_user(901)).json()
    payme(
        "CreateTransaction",
        {"id": "tx-test", "time": 1, "account": {"payment_id": str(payment["payment_id"])},
         "amount": payment["amount"] * 100},
    )
    payme("PerformTransaction", {"id": "tx-test"})

    stats = client.get("/api/admin/stats", headers=as_user(4242)).json()

    assert stats["credits_revenue"] == 40_000, stats
    assert stats["credits_sold"] == 50, stats
    assert stats["tests_turnover"] == 10_000, stats
    assert stats["tests_commission"] == 3_000, stats
    assert stats["revenue"] == 43_000, stats          # 40 000 + 3 000
    assert stats["teachers_owed"] == 7_000, stats     # o'qituvchiniki, daromad emas


def test_admin_oqituvchilar_royxatini_koradi():
    """Har bir o'qituvchi qancha to'lov qilgani ko'rinadi — eng ko'pi birinchi."""
    reset_database()
    register_teacher(555, "Ali", "Valiyev")
    register_teacher(666, "Vali", "Aliyev")

    # Faqat ikkinchi o'qituvchi kredit sotib oladi
    purchase = client.post(
        "/api/teacher/credit-purchases", json={"credits": 200}, headers=as_user(666)
    ).json()
    payme(
        "CreateTransaction",
        {"id": "tx-1", "time": 1, "account": {"purchase_id": str(purchase["id"])},
         "amount": purchase["amount"] * 100},
    )
    payme("PerformTransaction", {"id": "tx-1"})
    create_test(555)

    rows = client.get("/api/admin/teachers", headers=as_user(4242)).json()

    assert len(rows) == 2, rows
    assert rows[0]["name"] == "Vali Aliyev", rows      # ko'p to'lagani birinchi
    assert rows[0]["paid_total"] == 200 * 650, rows    # optom narx
    assert rows[0]["credits"] == 75 + 200, rows
    assert rows[1]["name"] == "Ali Valiyev" and rows[1]["paid_total"] == 0, rows
    assert rows[1]["tests"] == 1, rows

    # Oddiy o'qituvchi bu ro'yxatni ko'ra olmaydi
    assert client.get("/api/admin/teachers", headers=as_user(555)).status_code == 403


# ========== 7.1) Karta orqali kredit sotib olish ==========


# Chek o'rnida: PNG imzosi bilan boshlanadigan kichik "rasm"
FAKE_RECEIPT = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 300).decode()


class TelegramStub:
    """Telegram'ga ketadigan so'rovlarni ushlab qoladi — haqiqiy xabar yuborilmaydi."""

    def __init__(self, fail_receipt: bool = False):
        self.fail_receipt = fail_receipt
        self.receipts: list[str] = []      # admin'ga ketgan cheklar ostidagi matn
        self.teacher_messages: list[str] = []

    def __enter__(self):
        self._send, self._notify = main._send_receipt_to_admin, main._notify_teacher

        def send_receipt(purchase, teacher, image, mime, extension):
            if self.fail_receipt:
                raise RuntimeError("Telegram javob bermadi")
            self.receipts.append(main._card_topup_caption(purchase, teacher))
            return f"file-{purchase.id}"

        main._send_receipt_to_admin = send_receipt
        main._notify_teacher = lambda teacher, text: self.teacher_messages.append(text)
        return self

    def __exit__(self, *exc):
        main._send_receipt_to_admin, main._notify_teacher = self._send, self._notify


def send_card_topup(credits: int = 50, tg_id: int = 555, receipt: str = FAKE_RECEIPT):
    return client.post(
        "/api/teacher/card-topups",
        json={"credits": credits, "receipt_base64": receipt},
        headers=as_user(tg_id),
    )


def test_karta_orqali_chek_adminga_boradi_kredit_hali_yozilmaydi():
    reset_database()
    register_teacher()
    before = credits_of()

    with TelegramStub() as telegram:
        response = send_card_topup(credits=50)

    assert response.status_code == 200, response.text
    assert response.json()["amount"] == 50 * 800, response.json()
    assert response.json()["status"] == "pending"

    # Chek admin'ga summa va o'qituvchi ismi bilan bordi
    assert len(telegram.receipts) == 1
    assert "40 000 so'm" in telegram.receipts[0] and "Ali Valiyev" in telegram.receipts[0], telegram.receipts

    # Admin tasdiqlamaguncha kredit yozilmaydi
    assert credits_of() == before

    history = client.get("/api/teacher/credit-purchases", headers=as_user(555)).json()
    assert history[0]["method"] == "card" and history[0]["status"] == "pending", history


def test_admin_tasdiqlasa_kredit_yoziladi_va_oqituvchiga_xabar_boradi():
    reset_database()
    register_teacher()
    before = credits_of()

    with TelegramStub() as telegram:
        purchase_id = send_card_topup(credits=50).json()["id"]

        pending = client.get("/api/admin/card-topups", headers=as_user(4242)).json()
        assert [item["id"] for item in pending] == [purchase_id], pending
        assert pending[0]["has_receipt"] is True

        approved = client.post(f"/api/admin/card-topups/{purchase_id}/approve", headers=as_user(4242))

    assert approved.status_code == 200, approved.text
    assert credits_of() == before + 50, credits_of()
    assert any("tasdiqlandi" in text for text in telegram.teacher_messages), telegram.teacher_messages

    # Kutilayotganlar ro'yxatidan chiqdi, daromadga qo'shildi
    assert client.get("/api/admin/card-topups", headers=as_user(4242)).json() == []
    assert client.get("/api/admin/stats", headers=as_user(4242)).json()["credits_revenue"] == 40_000


def test_tugma_ikki_marta_bosilsa_kredit_ikki_marta_yozilmaydi():
    reset_database()
    register_teacher()
    before = credits_of()

    with TelegramStub():
        purchase_id = send_card_topup(credits=50).json()["id"]

        # Bot tugmasi ham, admin paneli ham bitta funksiyani ishlatadi
        session = SessionLocal()
        try:
            first = main.decide_card_topup(session, purchase_id, approve=True)
            second = main.decide_card_topup(session, purchase_id, approve=True)
            reject_after = main.decide_card_topup(session, purchase_id, approve=False)
        finally:
            session.close()

        again = client.post(f"/api/admin/card-topups/{purchase_id}/approve", headers=as_user(4242))

    assert first[0] is True
    assert second == (False, "So'rov allaqachon ko'rib chiqilgan"), second
    assert reject_after[0] is False
    assert again.status_code == 409
    assert credits_of() == before + 50, f"kredit ikki marta yozildi: {credits_of()}"


def test_admin_rad_etsa_kredit_yozilmaydi():
    reset_database()
    register_teacher()
    before = credits_of()

    with TelegramStub() as telegram:
        purchase_id = send_card_topup(credits=50).json()["id"]
        rejected = client.post(f"/api/admin/card-topups/{purchase_id}/reject", headers=as_user(4242))

    assert rejected.status_code == 200, rejected.text
    assert credits_of() == before
    assert any("tasdiqlanmadi" in text for text in telegram.teacher_messages), telegram.teacher_messages
    assert client.get("/api/admin/stats", headers=as_user(4242)).json()["credits_revenue"] == 0

    # Rad etilgandan keyin yangi chek yuborish mumkin
    with TelegramStub():
        assert send_card_topup(credits=10).status_code == 200


def test_bir_vaqtda_faqat_bitta_kutilayotgan_chek():
    reset_database()
    register_teacher()

    with TelegramStub() as telegram:
        assert send_card_topup().status_code == 200
        second = send_card_topup()

    assert second.status_code == 409, second.text
    assert len(telegram.receipts) == 1, "ikkinchi chek ham admin'ga ketib qoldi"


def test_rasm_bolmagan_fayl_qabul_qilinmaydi():
    reset_database()
    register_teacher()

    not_image = base64.b64encode(b"%PDF-1.4 bu rasm emas" + b" " * 300).decode()
    with TelegramStub() as telegram:
        response = send_card_topup(receipt=not_image)

    assert response.status_code == 422, response.text
    assert telegram.receipts == []
    assert client.get("/api/teacher/credit-purchases", headers=as_user(555)).json() == []


def test_chek_adminga_yetib_bormasa_sorov_kutilmaydi_lekin_izi_qoladi():
    """
    Admin ko'rmagan so'rov hech qachon tasdiqlanmaydi — u "kutilmoqda" bo'lib
    qolmasligi kerak. Lekin yozuv o'chirilmaydi: pul bilan bog'liq har bir
    harakatning izi saqlanadi ("failed" holatida).
    """
    reset_database()
    register_teacher()

    with TelegramStub(fail_receipt=True):
        response = send_card_topup()

    assert response.status_code == 502, response.text
    purchases = client.get("/api/teacher/credit-purchases", headers=as_user(555)).json()
    assert [purchase["status"] for purchase in purchases] == ["failed"], purchases
    assert client.get("/api/teacher/balance", headers=as_user(555)).json()["credits"] == 75  # kredit yozilmadi

    # Qayta urinish to'sib qo'yilmagan
    with TelegramStub():
        assert send_card_topup().status_code == 200


def test_karta_sozlanmagan_bolsa_usul_ochiq():
    reset_database()
    register_teacher()

    original = main.ADMIN_CARD_NUMBER
    main.ADMIN_CARD_NUMBER = ""
    try:
        assert client.get("/api/teacher/balance", headers=as_user(555)).json()["card_enabled"] is False
        with TelegramStub():
            assert send_card_topup().status_code == 503
    finally:
        main.ADMIN_CARD_NUMBER = original

    assert client.get("/api/teacher/balance", headers=as_user(555)).json()["card_enabled"] is True


def test_karta_sorovini_payme_tolay_olmaydi():
    """Aks holda bitta buyurtma chek + Payme orqali ikki marta kredit olardi."""
    reset_database()
    register_teacher()

    with TelegramStub():
        purchase = send_card_topup(credits=50).json()

    answer = payme(
        "CheckPerformTransaction",
        {"account": {"purchase_id": str(purchase["id"])}, "amount": purchase["amount"] * 100},
    )
    assert answer["error"]["code"] == -31050, answer


def test_oddiy_oqituvchi_tolovni_tasdiqlay_olmaydi():
    reset_database()
    register_teacher(555)
    register_teacher(666, "Vali", "Aliyev")

    with TelegramStub():
        purchase_id = send_card_topup(tg_id=555).json()["id"]

    # O'zi ham, boshqa o'qituvchi ham tasdiqlay olmaydi
    for tg_id in (555, 666):
        assert client.post(f"/api/admin/card-topups/{purchase_id}/approve", headers=as_user(tg_id)).status_code == 403
    assert client.get("/api/admin/card-topups", headers=as_user(555)).status_code == 403


# ========== 8) O'qituvchi oqimi ==========


def test_testni_qayta_ochganda_hisobot_belgisi_tozalanadi():
    """Qayta ochib yakunlaganda, yangi natijalar bilan hisobot yana yuboriladi."""
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")

    session = SessionLocal()
    try:
        from backend.database import Test

        test = session.scalars(main.select(Test)).one()
        test.results_sent_at = utcnow()
        session.commit()
    finally:
        session.close()

    client.post(f"/api/teacher/tests/{created['id']}/reopen", headers=as_user(555))

    session = SessionLocal()
    try:
        from backend.database import Test

        test = session.scalars(main.select(Test)).one()
        assert test.results_sent_at is None, "belgi tozalanmadi — hisobot qayta yuborilmaydi"
    finally:
        session.close()


def test_yopiq_test_oqimi_toliq():
    """O'qituvchi o'quvchi qo'shadi, javoblarini kiritadi, tahrirlaydi va testni yakunlaydi."""
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")

    # 1) O'quvchilar qo'shiladi
    added = client.post(
        f"/api/teacher/tests/{created['id']}/students",
        json={"names": ["Hasan Hasanov", "Zuhra Karimova"]},
        headers=as_user(555),
    )
    assert added.status_code == 200 and added.json()["added"] == 2, added.text

    detail = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()
    assert detail["missing"] == 2 and detail["filled"] == 0, detail

    # 2) Javoblari kiritilmagan o'quvchi bo'lsa, test yakunlanmaydi
    blocked = client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555))
    assert blocked.status_code == 400, blocked.text

    # 3) Javoblar kiritiladi
    students = detail["students"]
    before_credits = credits_of()

    saved = client.post(
        f"/api/teacher/tests/{created['id']}/students/{students[0]['id']}/answers",
        json={"answers": {"1": "B", "2": "C", "3": "A,C", "4": r"2\sqrt{3}"}},
        headers=as_user(555),
    ).json()
    assert saved["correct"] == 4 and saved["total"] == 4, saved
    assert credits_of() == before_credits - 1, "yangi natija uchun 1 kredit yechilishi kerak"

    # 4) Tahrirlash qo'shimcha kredit olmaydi
    client.post(
        f"/api/teacher/tests/{created['id']}/students/{students[0]['id']}/answers",
        json={"answers": {"1": "A", "2": "C", "3": "A,C", "4": r"2\sqrt{3}"}},
        headers=as_user(555),
    )
    assert credits_of() == before_credits - 1, "tahrirlash uchun kredit yechilib ketdi"

    # 5) Batafsil ko'rinishdagi sanoq umumiy sanoqqa mos keladi
    answer_detail = client.get(
        f"/api/teacher/tests/{created['id']}/students/{students[0]['id']}/detail", headers=as_user(555)
    ).json()
    assert answer_detail["correct"] == 3, answer_detail
    assert answer_detail["closed_total"] + answer_detail["written_total"] == answer_detail["total"]
    assert len(answer_detail["questions"]) == 4

    # 6) Ikkinchi o'quvchi ham kiritilgach, test yakunlanadi
    client.post(
        f"/api/teacher/tests/{created['id']}/students/{students[1]['id']}/answers",
        json={"answers": {"1": "B"}},
        headers=as_user(555),
    )
    closed = client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555))
    assert closed.status_code == 200, closed.text


def test_oquvchini_royxatdan_ochirish():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    client.post(
        f"/api/teacher/tests/{created['id']}/students", json={"names": ["Hasan Hasanov"]}, headers=as_user(555)
    )

    student_id = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["students"][0][
        "id"
    ]
    assert (
        client.delete(
            f"/api/teacher/tests/{created['id']}/students/{student_id}", headers=as_user(555)
        ).status_code
        == 200
    )
    assert client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["students"] == []


def test_boshqa_oqituvchining_testiga_tegib_bolmaydi():
    reset_database()
    register_teacher(555)
    register_teacher(666, "Vali", "Aliyev")
    created = create_test(555)

    assert client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(666)).status_code == 404
    assert client.delete(f"/api/teacher/tests/{created['id']}", headers=as_user(666)).status_code == 404


def test_yopiq_testda_oquvchi_ozi_ishlay_olmaydi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")

    response = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    assert response.status_code == 403, response.text


def test_togri_javoblar_oquvchiga_yuborilmaydi():
    """Savollar ro'yxatida to'g'ri javob bo'lmasligi kerak."""
    reset_database()
    register_teacher()
    created = create_test()

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    payload = json.dumps(started)

    assert "correct" not in payload, payload
    for question in started["questions"]:
        assert "correct" not in question


def test_pdf_va_javoblar_varaqasi_yasaladi():
    """PDF hisobot va javoblar varaqasi o'zbekcha belgilar bilan ham yasaladi."""
    reset_database()
    register_teacher(555, "Gʻulom", "Oʻktamov")
    created = create_test(title="Oʻzbek tili — 1-bosqich", subject="Oʻzbek tili")

    for url in (
        f"/api/teacher/tests/{created['id']}/pdf",
        f"/api/teacher/tests/{created['id']}/answer-sheet",
        f"/api/teacher/tests/{created['id']}/answer-sheet?answers=true",
    ):
        response = client.get(url, headers=as_user(555))
        assert response.status_code == 200, f"{url} -> {response.status_code}"
        assert response.content.startswith(b"%PDF"), url
        assert len(response.content) > 1000, url


# ========== Ishga tushirgich ==========


def main_runner() -> int:
    tests = [value for name, value in sorted(globals().items()) if name.startswith("test_")]

    passed, failed = 0, []
    for test in tests:
        name = test.__name__.removeprefix("test_").replace("_", " ")
        try:
            test()
            passed += 1
            print(f"  OK   {name}")
        except Exception as error:
            failed.append((name, error))
            print(f"  XATO {name}")

    print(f"\n{passed}/{len(tests)} test o'tdi")

    for name, error in failed:
        print(f"\n===== {name} =====")
        traceback.print_exception(type(error), error, error.__traceback__)

    return 1 if failed else 0


if __name__ == "__main__":
    print(f"Baza: {DB_PATH}\n")
    sys.exit(main_runner())
