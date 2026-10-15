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
from types import SimpleNamespace
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
    CLICK_SERVICE_ID="111",
    CLICK_MERCHANT_ID="222",
    CLICK_SECRET_KEY="click_secret",
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
    main._promo_failures.clear()
    main._test_code_failures.clear()
    main._subscription_confirmed.clear()


def current_code(school: dict) -> str:
    """Maktabning HOZIRGI kodi — kod bir martalik, har qo'shilishdan keyin yangilanadi."""
    from backend.database import PrivateSchool, SessionLocal

    with SessionLocal() as session:
        return session.get(PrivateSchool, school["id"]).code


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
    """Payme/Click ulanmagan va o'qituvchi kartasi yo'q bo'lsa, pullik test yaratilmaydi (karta so'raladi)."""
    reset_database()
    register_teacher()

    original, original_click = main.PAYME_MERCHANT_ID, main.CLICK_SERVICE_ID
    main.PAYME_MERCHANT_ID = ""
    main.CLICK_SERVICE_ID = ""
    try:
        assert client.get("/api/me", headers=as_user(555)).json()["payment_providers"] == []
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
        assert response.status_code == 422 and "karta" in response.text, response.text
    finally:
        main.PAYME_MERCHANT_ID = original
        main.CLICK_SERVICE_ID = original_click

    # Faqat Click ulangan bo'lsa ham pullik test yaratiladi
    original = main.PAYME_MERCHANT_ID
    main.PAYME_MERCHANT_ID = ""
    try:
        assert client.get("/api/me", headers=as_user(555)).json()["payment_providers"] == ["click"]
        assert create_test(price=5000)["code"]
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


def test_pullik_test_narxi_5000_dan_100000_gacha():
    reset_database()
    register_teacher()
    client.put("/api/teacher/card", json={"card_number": "8600123412341234", "card_expiry": "12/30", "card_holder": "Test"},
               headers=as_user(555))
    for bad in (4999, 100_001):
        body = {"title": "Pullik", "subject": "Matematika", "mode": "key", "price": bad,
                "key_questions": [{"number": 1, "question_type": "single", "option_count": 4, "correct": "A"}]}
        response = client.post("/api/teacher/tests", json=body, headers=as_user(555))
        assert response.status_code == 422, response.text
    created = create_test(price=100_000)
    assert create_test(price=5000)["code"]
    for bad in (1000, 200_000):
        response = client.patch(f"/api/teacher/tests/{created['id']}/price", json={"price": bad}, headers=as_user(555))
        assert response.status_code == 422, response.text
    response = client.patch(f"/api/teacher/tests/{created['id']}/price", json={"price": 0}, headers=as_user(555))
    assert response.status_code == 200, response.text


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
    assert grade_for(70) == "A+" and grade_for(65.5) == "A" and grade_for(45.9) == "NC"

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

    # Har qanday test (pullik bo'lmasa ham) — Rasch balli va Milliy sertifikat darajalari
    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    rows = results["rows"]
    assert results["uses_rasch"] is True
    assert [row["rank"] for row in rows] == [1, 1, 3], rows
    assert rows[0]["correct"] == 1 and rows[2]["correct"] == 0, rows
    assert rows[0]["ball"] > rows[2]["ball"] and rows[0]["grade"] in ("A+", "A", "B+", "B", "C+", "C", "NC"), rows
    assert all(0 <= row["ball"] <= 100 for row in rows), rows
    assert results["summary"]["participants"] == 3 and results["summary"]["avg_ball"] is not None, results["summary"]
    pdf = client.get(f"/api/teacher/tests/{created['id']}/pdf", headers=as_user(555))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), pdf.status_code
    assert grade_for(30) == "NC" and grade_for(46) == "C"


def test_bolimlar_va_sertifikat():
    """Bo'limlar (Algebra/Geometriya) — har bo'lim bo'yicha foiz; sertifikat PDF."""
    assert main.parse_sections("Algebra: 1-2\nGeometriya: 3, 4") == [
        {"name": "Algebra", "numbers": [1, 2]}, {"name": "Geometriya", "numbers": [3, 4]}]
    for bad in ("Algebra 1-2", "Algebra: x", "Algebra: 5-2"):
        try:
            main.parse_sections(bad)
            raise AssertionError(bad)
        except ValueError:
            pass
    assert main.sections_text([{"name": "A", "numbers": [1, 2, 3, 7, 9, 10]}]) == "A: 1-3, 7, 9-10"

    reset_database()
    register_teacher()
    created = create_test()  # 4 savol: 1-B, 2-C, 3-A,C, 4-yozma
    assert client.put(f"/api/teacher/tests/{created['id']}/sections", json={"text": "Algebra 1-2"},
                      headers=as_user(555)).status_code == 422
    saved = client.put(f"/api/teacher/tests/{created['id']}/sections", json={"text": "Algebra: 1-2\nGeometriya: 3-4"},
                       headers=as_user(555)).json()
    assert saved["text"] == "Algebra: 1-2\nGeometriya: 3-4", saved
    sent, restore = _capture_result_notices()
    try:
        for tg_id, answers in ((901, {"1": "B", "2": "C"}), (902, {"1": "B", "3": "A,C"})):
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
            client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": answers}, headers=as_user(tg_id))
    finally:
        restore()

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert results["section_names"] == ["Algebra", "Geometriya"], results["section_names"]
    assert results["question_numbers"] == [1, 2, 3, 4], results["question_numbers"]
    by_name = {row["name"]: row["sections"] for row in results["rows"]}
    assert sorted(by_name.values(), key=lambda v: v["Algebra"]) == [{"Algebra": 50.0, "Geometriya": 50.0}, {"Algebra": 100.0, "Geometriya": 0.0}], by_name

    pdf = client.get(f"/api/teacher/tests/{created['id']}/pdf", headers=as_user(555))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    certs = client.get(f"/api/teacher/tests/{created['id']}/certificates", headers=as_user(555))
    assert certs.status_code == 200 and certs.content.startswith(b"%PDF")
    one = client.get(f"/api/teacher/tests/{created['id']}/certificates", params={"attempt_id": results["rows"][0]["attempt_id"]},
                     headers=as_user(555))
    assert one.status_code == 200

    # "Bo'limsiz" ataylab tanlansa — bo'limlar chiqmaydi
    client.put(f"/api/teacher/tests/{created['id']}/sections", json={"text": ""}, headers=as_user(555))
    cleared = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert cleared["section_names"] == [], cleared["section_names"]


def test_milliy_sertifikat_matematika_bolimlari_ozi_qollanadi():
    """45 savolli matematika testida bo'lim belgilanmasa — Algebra/Geometriya shablon bo'yicha, foizda."""
    reset_database()
    register_teacher()
    key = [{"number": n, "question_type": "single", "option_count": 4, "correct": "A"} for n in range(1, 46)]
    created = create_test(key_questions=key)
    answers = {str(n): "A" for n in range(1, 24)}  # faqat 1-23 (hammasi Algebra) to'g'ri
    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": answers}, headers=as_user(901))

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert results["section_names"] == ["Algebra", "Geometriya"], results["section_names"]
    row = results["rows"][0]
    assert row["sections"] == {"Algebra": round(23 / 31 * 100, 1), "Geometriya": 0.0}, row["sections"]

    # Pasporti yo'q fan yoki savollar soni boshqa bo'lsa — shablon qo'llanmaydi
    other = create_test(subject="Informatika", key_questions=key)
    assert client.get(f"/api/teacher/tests/{other['id']}/results", headers=as_user(555)).json()["section_names"] == []
    short = create_test(subject="Fizika", key_questions=key[:30])
    assert client.get(f"/api/teacher/tests/{short['id']}/results", headers=as_user(555)).json()["section_names"] == []


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
    wrong = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": "XXXXXX"}, headers=as_user(702))
    assert wrong.status_code == 404, wrong.text
    joined = client.post("/api/schools/join", json={"phone": "901234567", "code": f" {current_code(school).lower()} ", "subjects": ["Kimyo"]},
                         headers=as_user(702))
    assert joined.status_code == 200, joined.text
    members = joined.json()["school"]["members"]
    assert [member["name"] for member in members] == ["Aziza Karimova", "Bobur Aliyev"], members
    assert members[0]["role"] == "owner" and members[1]["subjects"] == ["Kimyo"], members

    # Fan majburiy: fansiz qo'shilib yoki profilni bo'shatib bo'lmaydi
    _set_name(705, "Fansiz", "Odam")
    assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school)}, headers=as_user(705)).status_code == 422
    assert client.put("/api/schools/me/profile", json={"phone": "901234567", "subjects": []}, headers=as_user(702)).status_code == 422

    # Profil (fanlar) yangilanadi
    updated = client.put("/api/schools/me/profile", json={"phone": "901234567", "subjects": ["Kimyo", "Biologiya"]}, headers=as_user(702))
    assert updated.json()["school"]["my_subjects"] == ["Kimyo", "Biologiya"]

    # Guruhlarga ulashish havolasi — bot orqali kod bilan
    # Kod va havola faqat adminga ko'rinadi
    assert joined.json()["school"]["code"] is None and joined.json()["school"]["invite_link"] is None
    owner_view = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    assert owner_view["invite_link"] == f"https://t.me/test_platform_bot?start=school_{current_code(school)}"

    # Kod bir martalik: ishlatilgan kod endi ishlamaydi, yangisi beriladi
    assert current_code(school) != school["code"]
    _set_name(706, "Kech", "Qolgan")
    stale = client.post("/api/schools/join", json={"phone": "901234567", "code": school["code"], "subjects": ["Fizika"]}, headers=as_user(706))
    assert stale.status_code == 404, stale.text
    assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]},
                       headers=as_user(706)).status_code == 200

    # Maktab saqlanib qoladi — qayta kirganda ham o'sha maktab
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"]["id"] == school["id"]


def test_maktabdan_faqat_kod_bilan_chiqiladi_va_tarix_saqlanadi():
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Bobur", "Aliyev")
    school = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Samarqand viloyati", "district": "Urgut tumani",
                                               "name": "Ilm maktabi"}, headers=as_user(701)).json()["school"]
    client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": current_code(school)}, headers=as_user(702))

    # O'qituvchi o'zi chiqa olmaydi (to'g'ri kod bilan ham) — faqat admin chiqaradi
    assert client.post("/api/schools/leave", json={"code": current_code(school)}, headers=as_user(702)).status_code == 403
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is not None
    # Admin noto'g'ri kod bilan chiqa olmaydi
    assert client.post("/api/schools/leave", json={"code": "AAAAAA"}, headers=as_user(701)).status_code == 403

    # Direktor o'rinbosar tanlamasdan chiqa olmaydi — maktab direktorsiz qolmasin
    assert client.post("/api/schools/leave", json={"code": current_code(school)}, headers=as_user(701)).status_code == 422
    successor_id = next(m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]
                        if m["name"] == "Bobur Aliyev")
    assert client.post("/api/schools/leave", json={"code": current_code(school), "successor_user_id": 999999},
                       headers=as_user(701)).status_code == 422
    assert client.post("/api/schools/leave", json={"code": current_code(school), "successor_user_id": successor_id},
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
    assert client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": current_code(school)}, headers=as_user(701)).status_code == 200


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
    wrong = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": current_code(second), "school_id": first["id"]}, headers=as_user(703))
    assert wrong.status_code == 403, wrong.text
    ok = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": current_code(first), "school_id": first["id"]}, headers=as_user(703))
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


def test_xodimlar_telefon_raqami():
    """Qo'shilishda telefon majburiy; raqamlarni faqat rahbariyat (va xodimning o'zi) ko'radi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Raqam maktabi", "phone": "+998 90 111 22 33"},
                         headers=as_user(701)).json()["school"]
    assert school["my_phone"] == "+998901112233" and school["can_see_phones"] is True, school

    _set_name(702, "Raqamsiz", "Ustoz")
    no_phone = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"]}, headers=as_user(702))
    assert no_phone.status_code == 422 and "Telefon" in no_phone.text, no_phone.text
    bad = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "12345"},
                      headers=as_user(702))
    assert bad.status_code == 422, bad.text
    joined = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 66"},
                         headers=as_user(702)).json()["school"]
    assert joined["my_phone"] == "+998934445566" and joined["can_see_phones"] is False, joined
    # Oddiy o'qituvchi boshqalarning raqamini ko'rmaydi, o'zinikini ko'radi
    phones = {m["name"]: m["phone"] for m in joined["members"]}
    assert phones == {"Maktab Admini": None, "Raqamsiz Ustoz": "+998934445566"}, phones
    # Admin hammasini ko'radi
    phones = {m["name"]: m["phone"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    assert phones == {"Maktab Admini": "+998901112233", "Raqamsiz Ustoz": "+998934445566"}, phones

    # Profilda raqam yangilanadi; raqami yo'q admin raqamsiz saqlay olmaydi
    updated = client.put("/api/schools/me/profile", json={"subjects": ["Fizika"], "phone": "+998 97 000 00 01"},
                         headers=as_user(702)).json()["school"]
    assert updated["my_phone"] == "+998970000001", updated
    _set_name(703, "Ikkinchi", "Admin")
    other = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                              "name": "Raqamsiz maktab"}, headers=as_user(703)).json()["school"]
    assert other["my_phone"] is None
    assert client.put("/api/schools/me/profile", json={"subjects": []}, headers=as_user(703)).status_code == 422


def test_xodim_tugilgan_sana_va_pasport():
    """Tug'ilgan sana va pasport saqlanadi; faqat rahbariyat ko'radi; yuborilmagan maydon o'chmaydi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Pasport maktabi", "phone": "901112233"}, headers=as_user(701)).json()["school"]
    _set_name(702, "Ustoz", "Birinchi")
    _set_name(703, "Ustoz", "Ikkinchi")
    bad = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"],
                                                 "passport": "12345"}, headers=as_user(702))
    assert bad.status_code == 422, bad.text
    joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"],
                                                    "birth_date": "1990-05-17", "passport": "aa 1234567"}, headers=as_user(702))
    assert joined.status_code == 200, joined.text
    me = joined.json()["school"]
    assert me["my_birth_date"] == "1990-05-17" and me["my_passport"] == "AA1234567", me
    client.post("/api/schools/join", json={"phone": "901234568", "code": current_code(school), "subjects": ["Fizika"]}, headers=as_user(703))

    # Oddiy o'qituvchi boshqaning sanasi/pasportini ko'rmaydi
    others = {m["name"]: m for m in client.get("/api/schools/me", headers=as_user(703)).json()["school"]["members"]}
    assert others["Ustoz Birinchi"]["passport"] is None and others["Ustoz Birinchi"]["birth_date"] is None
    # Admin ko'radi
    seen = {m["name"]: m for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    assert seen["Ustoz Birinchi"]["passport"] == "AA1234567" and seen["Ustoz Birinchi"]["birth_date"] == "1990-05-17"

    # Profilni faqat fan bilan saqlash pasportni o'chirmaydi; aniq bo'sh yuborilsa — o'chadi
    kept = client.put("/api/schools/me/profile", json={"subjects": ["Fizika"]}, headers=as_user(702)).json()["school"]
    assert kept["my_passport"] == "AA1234567", kept
    cleared = client.put("/api/schools/me/profile", json={"subjects": ["Fizika"], "passport": ""}, headers=as_user(702)).json()["school"]
    assert cleared["my_passport"] is None, cleared
    future = client.put("/api/schools/me/profile", json={"subjects": ["Fizika"], "birth_date": "2999-01-01"}, headers=as_user(702))
    assert future.status_code == 422


def test_xodimni_faqat_admin_chiqaradi():
    """Admin/direktor xodimni chiqaradi (yozuv qoladi, kuratorlik bo'shaydi); oddiy o'qituvchi chiqara olmaydi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Chiqarish maktabi"}, headers=as_user(701)).json()["school"]
    for tg_id in (702, 703):
        _set_name(tg_id, "Ustoz", f"Nomer{tg_id}")
        assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school),
                                                      "subjects": ["Fizika"]}, headers=as_user(tg_id)).status_code == 200
    ids = {m["name"]: m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    cls = client.post("/api/classes", json={"name": "7-A"}, headers=as_user(701)).json()
    client.put(f"/api/classes/{cls['id']}/curator", json={"user_id": ids["Ustoz Nomer702"]}, headers=as_user(701))

    assert client.delete(f"/api/schools/members/{ids['Ustoz Nomer702']}", headers=as_user(703)).status_code == 403
    assert client.delete(f"/api/schools/members/{ids['Maktab Admini']}", headers=as_user(701)).status_code == 422
    removed = client.delete(f"/api/schools/members/{ids['Ustoz Nomer702']}", headers=as_user(701))
    assert removed.status_code == 200, removed.text
    assert [m["name"] for m in removed.json()["school"]["members"]] == ["Maktab Admini", "Ustoz Nomer703"]
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is None
    assert client.get(f"/api/classes/{cls['id']}", headers=as_user(701)).json()["curator"] is None

    # Admin chiqib ketgan bo'lsa ham o'z maktabini ko'radi (kodsiz qaytish uchun)
    _set_name(704, "Yolgiz", "Admin")
    own = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                            "name": "Yolgiz maktab"}, headers=as_user(704)).json()["school"]
    assert client.post("/api/schools/leave", json={"code": own["code"]}, headers=as_user(704)).status_code == 200
    me = client.get("/api/schools/me", headers=as_user(704)).json()
    assert me["school"] is None and me["owned_school"]["id"] == own["id"], me


def test_lavozimlar_direktor_va_zam_direktor():
    """Admin lavozim beradi: direktor (bitta) va zam direktor. Ikkalasi sinf yaratadi va
    o'qituvchi taklif qiladi; sinfni faqat admin va direktor o'chiradi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Lavozim maktabi"}, headers=as_user(701)).json()["school"]
    for tg_id in (702, 703, 704):
        _set_name(tg_id, "Ustoz", f"Nomer{tg_id}")
        assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]},
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

    # Sinfni faqat admin va direktor ochadi; zam direktor o'qituvchi taklif qiladi, lekin sinf ocha/o'chira olmaydi
    assert me["can_create_classes"] is False, me
    assert client.post("/api/classes", json={"name": "1-A"}, headers=as_user(703)).status_code == 403
    created = client.post("/api/classes", json={"name": "1-A"}, headers=as_user(702)).json()
    assert created["is_director"] is True and created["can_delete"] is True, created
    as_deputy = client.get(f"/api/classes/{created['id']}", headers=as_user(703)).json()
    assert as_deputy["is_director"] is False and as_deputy["can_manage"] is False, as_deputy
    # O'quvchilarni zam direktor emas — admin, direktor va kurator qo'shadi
    assert client.post(f"/api/classes/{created['id']}/students", json={"full_name": "Ali Valiyev"},
                       headers=as_user(703)).status_code == 403
    assert client.post(f"/api/classes/{created['id']}/students", json={"full_name": "Ali Valiyev"},
                       headers=as_user(702)).status_code == 200
    assert client.put(f"/api/classes/{created['id']}/curator", json={"user_id": uid[704]}, headers=as_user(703)).status_code == 403
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


def test_sovga_kredit_faqat_bir_marta():
    """Sovg'a kredit Telegram akkauntiga bir marta beriladi: qayta ro'yxatdan o'tish (bot/kesh tozalansa ham) bermaydi."""
    reset_database()
    register_teacher(tg_id=777)
    assert credits_of(777) == int(main.WELCOME_CREDITS)

    # Kredit sarflandi deb faraz qilamiz — qayta ro'yxatdan o'tish urinishi balansni tiklamasligi kerak
    session = SessionLocal()
    try:
        from backend.database import Teacher, User as DbUser
        teacher = session.scalar(main.select(Teacher).join(DbUser, DbUser.id == Teacher.user_id).where(DbUser.tg_id == 777))
        teacher.credits = 3
        session.commit()
    finally:
        session.close()

    again = client.post("/api/teacher/register", json={"first_name": "Ali", "last_name": "Valiyev"}, headers=as_user(777))
    assert again.status_code == 400, again.text
    assert credits_of(777) == 3


def test_kredit_narx_pogonalari():
    """1-100: 1200, 101-150: 1000, 151-300: 650, 301-500: 500, 501+: 450 so'm (1 dona)."""
    price = main._calculate_credit_price
    assert [price(n) // n for n in (1, 100, 101, 150, 151, 300, 301, 500, 501, 8000)] ==         [1200, 1200, 1000, 1000, 650, 650, 500, 500, 450, 450]


def _click_form(action: int, ref: str, amount, prepare_id=None, click_trans_id="9001", error=0) -> dict:
    import hashlib

    form = {"click_trans_id": click_trans_id, "service_id": "111", "click_paydoc_id": "5", "merchant_trans_id": ref,
            "amount": str(amount), "action": str(action), "error": str(error), "error_note": "", "sign_time": "2026-10-09 12:00:00"}
    if prepare_id is not None:
        form["merchant_prepare_id"] = str(prepare_id)
    parts = [form["click_trans_id"], "111", "click_secret", ref] + ([str(prepare_id)] if prepare_id is not None else []) + \
        [form["amount"], form["action"], form["sign_time"]]
    form["sign_string"] = hashlib.md5("".join(parts).encode()).hexdigest()
    return form


def test_click_orqali_kredit_avtomatik_tushadi():
    """Click: Prepare + Complete; imzo, summa, takror to'lov va bekor qilish tekshiriladi."""
    reset_database()
    register_teacher(tg_id=777)
    start = credits_of(777)

    purchase = client.post("/api/teacher/credit-purchases", json={"credits": 100, "provider": "click"}, headers=as_user(777)).json()
    assert purchase["checkout_url"].startswith("https://my.click.uz/services/pay?"), purchase
    assert f"transaction_param=c{purchase['id']}" in purchase["checkout_url"] and "amount=120000" in purchase["checkout_url"]
    ref, amount = f"c{purchase['id']}", "120000.00"

    # Imzo noto'g'ri yoki summa xato — rad etiladi
    bad = _click_form(0, ref, amount); bad["sign_string"] = "0" * 32
    assert client.post("/api/click/prepare", data=bad).json()["error"] == -1
    assert client.post("/api/click/prepare", data=_click_form(0, ref, "1000.00")).json()["error"] == -2
    assert client.post("/api/click/prepare", data=_click_form(0, "c999999", amount)).json()["error"] == -5

    prepared = client.post("/api/click/prepare", data=_click_form(0, ref, amount)).json()
    assert prepared["error"] == 0 and prepared["merchant_prepare_id"] == purchase["id"], prepared
    assert credits_of(777) == start  # hali pul yechilmagan

    done = client.post("/api/click/complete", data=_click_form(1, ref, amount, prepare_id=purchase["id"])).json()
    assert done["error"] == 0 and done["merchant_confirm_id"] == purchase["id"], done
    assert credits_of(777) == start + 100
    again = client.post("/api/click/complete", data=_click_form(1, ref, amount, prepare_id=purchase["id"])).json()
    assert again["error"] == -4 and credits_of(777) == start + 100  # ikki marta yozilmaydi

    # Click tomonda to'lov o'tmasa — bekor, kredit yo'q
    second = client.post("/api/teacher/credit-purchases", json={"credits": 10, "provider": "click"}, headers=as_user(777)).json()
    ref2 = f"c{second['id']}"
    client.post("/api/click/prepare", data=_click_form(0, ref2, "12000.00", click_trans_id="9002"))
    failed = client.post("/api/click/complete", data=_click_form(1, ref2, "12000.00", prepare_id=second["id"], click_trans_id="9002", error=-5017)).json()
    assert failed["error"] == -9 and credits_of(777) == start + 100

    # Sozlanmagan provider — 503
    assert client.post("/api/teacher/credit-purchases", json={"credits": 5, "provider": "uzum"}, headers=as_user(777)).status_code == 422


def test_payme_sinov_rejimi_havolasi():
    """PAYME_TEST_MODE yoqilsa — to'lov sahifasi test.paycom.uz, aks holda checkout.paycom.uz."""
    original = main.PAYME_TEST_MODE
    try:
        main.PAYME_TEST_MODE = True
        assert main._build_payme_checkout_url("purchase_id", 1, 1000).startswith("https://test.paycom.uz/")
        main.PAYME_TEST_MODE = False
        assert main._build_payme_checkout_url("purchase_id", 1, 1000).startswith("https://checkout.paycom.uz/")
    finally:
        main.PAYME_TEST_MODE = original


def test_promo_kod():
    """Admin kod yaratadi; o'qituvchi bir marta 3000 oladi; noto'g'ri kodlar cheklanadi; boshlang'ich kod izi to'g'ri."""
    reset_database()
    register_teacher(tg_id=777)
    start = credits_of(777)
    _set_name(4242, "Bosh", "Admin")

    assert client.post("/api/admin/promo-codes", json={"code": "BAHOR2026"}, headers=as_user(777)).status_code == 403
    created = client.post("/api/admin/promo-codes", json={"code": "bahor 2026"}, headers=as_user(4242)).json()
    assert created["credits"] == 3000 and created["hint"] == "BA…26", created
    assert client.post("/api/admin/promo-codes", json={"code": "BAHOR2026"}, headers=as_user(4242)).status_code == 409

    # Bitta o'qituvchi 3 martagacha; 4-chisi rad etiladi
    for attempt in range(1, 4):
        ok = client.post("/api/teacher/promo", json={"code": " Bahor2026 "}, headers=as_user(777))
        assert ok.status_code == 200 and ok.json()["used"] == attempt and ok.json()["limit"] == 3, ok.text
    assert credits_of(777) == start + 9000
    fourth = client.post("/api/teacher/promo", json={"code": "BAHOR2026"}, headers=as_user(777))
    assert fourth.status_code == 409 and "3 marta" in fourth.text, fourth.text
    assert credits_of(777) == start + 9000

    # Faqat raqamli qisqa kod — miqdor bilan adashmasligi uchun rad etiladi
    assert client.post("/api/admin/promo-codes", json={"code": "2026"}, headers=as_user(4242)).status_code == 422
    assert client.post("/api/admin/promo-codes", json={"code": "2026123"}, headers=as_user(4242)).status_code == 200

    # Chegaralangan kod: 1 kishilik
    client.post("/api/admin/promo-codes", json={"code": "BIRTA", "credits": 500, "max_uses": 1, "per_teacher_limit": 1},
                headers=as_user(4242))
    register_teacher(tg_id=778)
    register_teacher(tg_id=779)
    assert client.post("/api/teacher/promo", json={"code": "BIRTA"}, headers=as_user(778)).status_code == 200
    assert client.post("/api/teacher/promo", json={"code": "BIRTA"}, headers=as_user(779)).status_code == 410

    # To'xtatilgan kod ishlamaydi
    client.post(f"/api/admin/promo-codes/{created['id']}/toggle", headers=as_user(4242))
    assert client.post("/api/teacher/promo", json={"code": "BAHOR2026"}, headers=as_user(779)).status_code == 404

    # Taxmin qilish: 5 ta noto'g'ridan keyin bloklanadi
    main._promo_failures.clear()
    main._test_code_failures.clear()
    for attempt in range(5):
        assert client.post("/api/teacher/promo", json={"code": f"XATO{attempt}"}, headers=as_user(779)).status_code == 404
    assert client.post("/api/teacher/promo", json={"code": "BIRTA"}, headers=as_user(779)).status_code == 429

    # Boshlang'ich kodning izi to'g'ri hisoblangan
    assert main._promo_hash("51464825709") == main.SEED_PROMO_CODES[0]["code_hash"]


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
        joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": [subject]}, headers=as_user(tg_id))
        assert joined.status_code == 200, joined.text

    # Direktor fansiz profilini saqlay oladi, o'qituvchi esa yo'q
    assert client.put("/api/schools/me/profile", json={"phone": "901234567", "subjects": []}, headers=as_user(701)).status_code == 200
    assert client.put("/api/schools/me/profile", json={"phone": "901234567", "subjects": []}, headers=as_user(703)).status_code == 422

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
    no_class = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "is_curator": True}, headers=as_user(702))
    assert no_class.status_code == 422, no_class.text
    joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "is_curator": True,
                                                    "curator_class_id": first["id"]}, headers=as_user(702))
    assert joined.status_code == 200, joined.text
    detail = client.get(f"/api/classes/{first['id']}", headers=as_user(702)).json()
    assert detail["is_curator"] and detail["curator"]["name"] == "Kurator Aliyeva", detail

    # Band sinfni boshqa kurator tanlay olmaydi; ro'yxatda u endi yo'q
    taken = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "is_curator": True,
                                                   "curator_class_id": first["id"]}, headers=as_user(703))
    assert taken.status_code == 409, taken.text
    lookup = client.post("/api/schools/lookup", json={"code": current_code(school)}, headers=as_user(703)).json()
    assert [c["name"] for c in lookup["open_classes"]] == ["5-B"]

    # Kurator profilini fansiz saqlay oladi
    assert client.put("/api/schools/me/profile", json={"phone": "901234567", "subjects": []}, headers=as_user(702)).status_code == 200

    # Oddiy o'qituvchi: fansiz qo'shilmaydi
    assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school)}, headers=as_user(704)).status_code == 422
    assert client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]},
                       headers=as_user(704)).status_code == 200


def test_direktor_oz_maktabiga_kodsiz_qaytadi():
    """Yolg'iz direktor chiqib ketgan bo'lsa, o'z maktabiga kodsiz, direktor sifatida qaytadi."""
    reset_database()
    _set_name(701, "Direktor", "Karimov")
    _set_name(702, "Begona", "Odam")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Qaytish maktabi"}, headers=as_user(701)).json()["school"]
    assert client.post("/api/schools/leave", json={"code": current_code(school)}, headers=as_user(701)).status_code == 200

    listed = client.get("/api/schools", params={"region": "Toshkent shahri"}, headers=as_user(701)).json()
    assert listed[0]["is_mine"] is True
    assert client.get("/api/schools", params={"region": "Toshkent shahri"}, headers=as_user(702)).json()[0]["is_mine"] is False

    # Begona odam kodsiz kira olmaydi
    assert client.post(f"/api/schools/{school['id']}/return", headers=as_user(702)).status_code == 403

    back = client.post(f"/api/schools/{school['id']}/return", headers=as_user(701))
    assert back.status_code == 200 and back.json()["school"]["my_role"] == "owner", back.text
    assert client.post(f"/api/schools/{school['id']}/return", headers=as_user(701)).status_code == 409

    # Kod bilan kirsa ham — direktor sifatida (fan so'ralmaydi)
    client.post("/api/schools/leave", json={"code": current_code(school)}, headers=as_user(701))
    again = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school)}, headers=as_user(701))
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
                           json={"phone": "901234567", "is_curator": True, "curator_class_id": classes["id"]}, headers=as_user(702))
    assert accepted.status_code == 200, accepted.text
    assert client.get(f"/api/classes/{classes['id']}", headers=as_user(702)).json()["is_curator"]

    # Bir martalik: boshqa odam shu havola bilan kira olmaydi
    again = client.post(f"/api/schools/invites/{invite['token']}/accept", json={"phone": "901234567", "subjects": ["Fizika"]}, headers=as_user(703))
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
    expired = client.post(f"/api/schools/invites/{second['token']}/accept", json={"phone": "901234567", "subjects": ["Fizika"]}, headers=as_user(703))
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


def test_davomatni_ochirish_tarix_va_statistikadan_olinadi():
    """O'chirilgan davomat tarixda, statistikada ko'rinmaydi, lekin bazada qoladi."""
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    sid = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(701)).json()["students"][0]["id"]
    first = client.post(f"/api/classes/{cid}/attendance", json={"marks": {sid: "absent"}}, headers=as_user(701)).json()
    client.post(f"/api/classes/{cid}/attendance", json={"marks": {sid: "present"}}, headers=as_user(701))
    detail = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()
    assert len(detail["attendances"]) == 2 and detail["students"][0]["stats"]["rate"] == 50, detail

    # Boshqa (oddiy) o'qituvchi o'chira olmaydi
    _set_name(702, "Begona", "Ustoz")
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]}, headers=as_user(702))
    assert client.delete(f"/api/attendance/{first['id']}", headers=as_user(702)).status_code == 403

    assert client.delete(f"/api/attendance/{first['id']}", headers=as_user(701)).status_code == 200
    detail = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()
    assert len(detail["attendances"]) == 1 and detail["students"][0]["stats"]["rate"] == 100, detail
    assert client.get(f"/api/attendance/{first['id']}", headers=as_user(701)).status_code == 404
    student = client.get(f"/api/classes/{cid}/students/{sid}", headers=as_user(701)).json()
    assert len(student["history"]) == 1, student

    session = SessionLocal()
    try:
        from backend.database import Attendance
        assert session.get(Attendance, first["id"]).deleted_at is not None  # bazada qolgan
    finally:
        session.close()


def test_sinf_test_natijalari():
    """O'qituvchi natija kiritadi: foiz, baho, o'rin. Ko'radi — kiritgan o'qituvchi va rahbariyat."""
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    ids = {}
    for name in ("Ali Valiyev", "Vali Aliyev", "Hasan Husanov", "Olim Karimov"):
        detail = client.post(f"/api/classes/{cid}/students", json={"full_name": name}, headers=as_user(701)).json()
    ids = {s["full_name"]: s["id"] for s in detail["students"]}
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    for tg_id in (702, 703):
        _set_name(tg_id, "Ustoz", f"N{tg_id}")
        client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]},
                    headers=as_user(tg_id))

    body = {"title": "1-chorak nazorat", "subject": "Fizika", "taken_on": "2026-10-01", "max_score": 30,
            "scores": {ids["Ali Valiyev"]: 27, ids["Vali Aliyev"]: 18, ids["Hasan Husanov"]: 27}}
    assert client.post(f"/api/classes/{cid}/tests", json={**body, "scores": {ids["Ali Valiyev"]: 31}},
                       headers=as_user(702)).status_code == 422
    created = client.post(f"/api/classes/{cid}/tests", json=body, headers=as_user(702))
    assert created.status_code == 200, created.text
    test = created.json()
    rows = test["scores"]
    assert [r["name"] for r in rows] == ["Ali Valiyev", "Hasan Husanov", "Vali Aliyev", "Olim Karimov"], rows
    assert rows[0]["percent"] == 90 and rows[0]["grade"] == "A+" and rows[0]["rank"] == 1 and rows[1]["rank"] == 1
    assert rows[2]["percent"] == 60 and rows[2]["grade"] == "B+" and rows[2]["rank"] == 3
    assert rows[3]["score"] is None and "rank" not in rows[3]
    assert test["participants"] == 3 and test["average_percent"] == 80, test

    # Boshqa oddiy o'qituvchi ko'rmaydi; admin ko'radi
    assert client.get(f"/api/class-tests/{test['id']}", headers=as_user(703)).status_code == 403
    assert client.get(f"/api/classes/{cid}/tests", headers=as_user(703)).json() == []
    assert client.get(f"/api/class-tests/{test['id']}", headers=as_user(701)).status_code == 200
    assert len(client.get(f"/api/classes/{cid}/tests", headers=as_user(701)).json()) == 1

    # PDF va rasm; rahbariyat uchun sinflar bo'yicha ro'yxat
    pdf = client.get(f"/api/class-tests/{test['id']}/file", params={"format": "pdf"}, headers=as_user(702))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    png = client.get(f"/api/class-tests/{test['id']}/file", params={"format": "image"}, headers=as_user(701))
    assert png.status_code == 200 and png.content[1:4] == b"PNG"
    overview = client.get("/api/schools/test-results", headers=as_user(701)).json()
    assert overview[0]["tests"] == 1 and overview[0]["average_percent"] == 80, overview
    assert client.get("/api/schools/test-results", headers=as_user(703)).status_code == 403

    # Tahrirlash va o'chirish (bazada qoladi)
    edited = client.put(f"/api/class-tests/{test['id']}", json={**body, "scores": {ids["Olim Karimov"]: 30}},
                        headers=as_user(702)).json()
    assert edited["scores"][0]["name"] == "Olim Karimov" and edited["participants"] == 1, edited
    assert client.delete(f"/api/class-tests/{test['id']}", headers=as_user(703)).status_code == 403
    assert client.delete(f"/api/class-tests/{test['id']}", headers=as_user(702)).status_code == 200
    assert client.get(f"/api/classes/{cid}/tests", headers=as_user(701)).json() == []


def test_fan_oqituvchisi_faqat_ozi_olgan_davomatni_koradi():
    """Biologiya o'qituvchisi matematika o'qituvchisining davomatini ko'rmaydi; kurator va admin hammasini ko'radi."""
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(701))
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    for tg_id, subject in ((702, "Matematika"), (703, "Biologiya"), (704, "Kimyo")):
        _set_name(tg_id, "Ustoz", subject)
        client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": [subject]},
                    headers=as_user(tg_id))
    members = {m["name"]: m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    client.put(f"/api/classes/{cid}/curator", json={"user_id": members["Ustoz Kimyo"]}, headers=as_user(701))

    math = client.post(f"/api/classes/{cid}/attendance", json={"subject": "Matematika"}, headers=as_user(702)).json()
    bio = client.post(f"/api/classes/{cid}/attendance", json={"subject": "Biologiya"}, headers=as_user(703)).json()

    seen = lambda tg: {a["id"] for a in client.get(f"/api/classes/{cid}", headers=as_user(tg)).json()["attendances"]}
    assert seen(703) == {bio["id"]}, seen(703)
    assert seen(702) == {math["id"]}
    assert seen(704) == {math["id"], bio["id"]}  # kurator
    assert seen(701) == {math["id"], bio["id"]}  # admin
    assert client.get(f"/api/attendance/{math['id']}", headers=as_user(703)).status_code == 403
    assert client.get(f"/api/attendance/{math['id']}", headers=as_user(704)).status_code == 200

    # O'quvchi foizi va tarixi ham faqat o'z fani bo'yicha: matematikada yo'q, biologiyada bor
    sid = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"][0]["id"]
    client.put(f"/api/attendance/{math['id']}", json={"marks": {sid: "absent"}}, headers=as_user(702))
    bio_view = client.get(f"/api/classes/{cid}", headers=as_user(703)).json()["students"][0]["stats"]
    assert bio_view["total"] == 1 and bio_view["rate"] == 100, bio_view
    admin_view = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"][0]["stats"]
    assert admin_view["total"] == 2 and admin_view["rate"] == 50, admin_view
    profile = client.get(f"/api/classes/{cid}/students/{sid}", headers=as_user(703)).json()
    assert [h["subject"] for h in profile["history"]] == ["Biologiya"], profile["history"]

    # Ikkinchi biologiya o'qituvchisi (o'rinbosar) biologiya davomatini ko'radi, matematikanikini emas
    _set_name(705, "Ikkinchi", "Biolog")
    client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Biologiya"]},
                headers=as_user(705))
    assert seen(705) == {bio["id"]}


def test_oqish_tolovlari():
    """Oylik summa, chegirma, qisman to'lov, ortiqcha to'lovni rad etish, bekor qilish (o'chirilmaydi), ruxsatlar."""
    reset_database()
    school_class = _school_with_class()
    cid = school_class["id"]
    sid = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(701)).json()["students"][0]["id"]
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    _set_name(702, "Oddiy", "Ustoz")
    client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]}, headers=as_user(702))
    month = main._current_month()

    # Summa belgilanmaguncha to'lov qabul qilinmaydi; summani oddiy o'qituvchi belgilay olmaydi
    assert client.post(f"/api/classes/{cid}/students/{sid}/payments", json={"month": month, "amount": 1000},
                       headers=as_user(701)).status_code == 422
    assert client.put("/api/schools/fee", json={"monthly_fee": 1_000_000}, headers=as_user(702)).status_code == 403
    assert client.put("/api/schools/fee", json={"monthly_fee": 1_000_000}, headers=as_user(701)).json()["school"]["monthly_fee"] == 1_000_000

    # Oddiy o'qituvchi to'lovlarni ko'rmaydi
    assert client.get(f"/api/classes/{cid}/payments", headers=as_user(702)).status_code == 403

    # 30% chegirma → 700 000; qisman to'lov va qoldiq
    info = client.put(f"/api/classes/{cid}/students/{sid}/discount", json={"percent": 30}, headers=as_user(701)).json()
    assert info["due"] == 700_000, info
    first = client.post(f"/api/classes/{cid}/students/{sid}/payments", json={"month": month, "amount": 350_000, "method": "card"},
                        headers=as_user(701)).json()
    assert first["payment"]["receipt_no"] == 1 and first["months"][0]["remaining"] == 350_000, first
    too_much = client.post(f"/api/classes/{cid}/students/{sid}/payments", json={"month": month, "amount": 400_000}, headers=as_user(701))
    assert too_much.status_code == 422 and "350 000" in too_much.text, too_much.text

    status = client.get(f"/api/classes/{cid}/payments", headers=as_user(701)).json()
    assert status["students"][0]["status"] == "partial" and status["totals"]["debtors"] == 1, status
    reminder = client.get(f"/api/classes/{cid}/students/{sid}/reminder", headers=as_user(701)).json()["text"]
    assert "hurmatli ota-ona" in reminder and "350 000 so'm" in reminder, reminder
    debtors = client.get(f"/api/classes/{cid}/debtors", headers=as_user(701)).json()["text"]
    assert "Ali Valiyev — 350 000 so'm" in debtors, debtors

    second = client.post(f"/api/classes/{cid}/students/{sid}/payments", json={"month": month, "amount": 350_000},
                         headers=as_user(701)).json()
    assert second["payment"]["receipt_no"] == 2 and second["months"][0]["status"] == "paid", second
    receipt = client.get(f"/api/payments/{second['payment']['id']}/receipt", headers=as_user(701))
    assert receipt.status_code == 200 and receipt.content[1:4] == b"PNG"

    # Bekor qilish: to'lov tarixda qoladi, qarz qaytadi
    cancelled = client.post(f"/api/payments/{second['payment']['id']}/cancel", json={"reason": "Xato kiritildi"},
                            headers=as_user(701)).json()
    assert cancelled["months"][0]["remaining"] == 350_000 and len(cancelled["payments"]) == 2, cancelled
    assert cancelled["payments"][0]["cancelled"] and cancelled["payments"][0]["cancel_reason"] == "Xato kiritildi"
    summary = client.get("/api/schools/payments/summary", headers=as_user(701)).json()
    assert summary["totals"]["paid"] == 350_000 and summary["totals"]["remaining"] == 350_000, summary
    assert client.get("/api/schools/payments/summary", headers=as_user(702)).status_code == 403


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
        response = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": f"WRONG{attempt:03d}", "school_id": school["id"]},
                               headers=as_user(709))
        assert response.status_code == 403, response.text

    # 6-urinish — hatto to'g'ri kod bilan ham vaqtincha to'xtatiladi
    blocked = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"], "code": current_code(school), "school_id": school["id"]}, headers=as_user(709))
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
    assert purchase["amount"] == 50 * 1200, purchase
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

        # "Tekshirish": har bir kanal holati alohida
        statuses = client.get(f"/api/tests/{own['code']}/subscriptions", headers=as_user(901)).json()["channels"]
        assert statuses == [{"channel": "@sukhrobmath", "status": "no"}, {"channel": "@ustoz_kanali", "status": "no"}], statuses

        # Kanal qo'shilmagan testda ham adminlar kanaliga obuna shart
        blocked = client.post(f"/api/tests/{plain['code']}/start", json={}, headers=as_user(901))
        assert blocked.status_code == 451 and "@sukhrobmath" in blocked.json()["detail"], blocked.text

        subscribed.add("@sukhrobmath")
        assert client.post(f"/api/tests/{plain['code']}/start", json={}, headers=as_user(901)).status_code == 200
        # O'qituvchi kanaliga obuna bo'lmaguncha — to'xtatiladi, xabarda faqat yetishmagani
        blocked = client.post(f"/api/tests/{own['code']}/start", json={}, headers=as_user(901))
        assert blocked.status_code == 451 and "@ustoz_kanali" in blocked.json()["detail"], blocked.text
        assert "@sukhrobmath" not in blocked.json()["detail"], blocked.text
        statuses = client.get(f"/api/tests/{own['code']}/subscriptions", headers=as_user(901)).json()["channels"]
        assert [item["status"] for item in statuses] == ["yes", "no"], statuses
        # Bitta kanalni alohida tekshirish
        single = client.get(f"/api/tests/{own['code']}/subscriptions", params={"channel": "@ustoz_kanali"},
                            headers=as_user(901)).json()["channels"]
        assert single == [{"channel": "@ustoz_kanali", "status": "no"}], single
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

    # 1) O'qituvchi 50 ta kredit sotib oladi: 50 x 1200 = 60 000 so'm
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

    assert stats["credits_revenue"] == 60_000, stats
    assert stats["credits_sold"] == 50, stats
    assert stats["tests_turnover"] == 10_000, stats
    assert stats["tests_commission"] == 3_000, stats
    assert stats["revenue"] == 63_000, stats          # 60 000 + 3 000
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
    assert response.json()["amount"] == 50 * 1200, response.json()
    assert response.json()["status"] == "pending"

    # Chek admin'ga summa va o'qituvchi ismi bilan bordi
    assert len(telegram.receipts) == 1
    assert "60 000 so'm" in telegram.receipts[0] and "Ali Valiyev" in telegram.receipts[0], telegram.receipts

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
    assert client.get("/api/admin/stats", headers=as_user(4242)).json()["credits_revenue"] == 60_000


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

    reopened = client.post(f"/api/teacher/tests/{created['id']}/reopen", headers=as_user(555)).json()
    # Qayta ochilgan test — yangi kod; eski kod endi ishlamaydi
    assert reopened["code"] and reopened["code"] != created["code"], reopened
    assert client.get(f"/api/tests/{created['code']}", headers=as_user(901)).status_code == 404
    assert client.get(f"/api/tests/{reopened['code']}", headers=as_user(901)).status_code == 200

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


def test_kredit_bir_vaqtdagi_ozgarishda_yoqolmaydi():
    """Bir so'rov o'qituvchini o'qib turganda boshqasi kredit yozsa — ikkalasi ham saqlanadi."""
    from backend.database import add_credits, withdraw_balance

    reset_database()
    register_teacher(tg_id=555)
    start = credits_of(555)

    slow, fast = SessionLocal(), SessionLocal()
    try:
        # "Sekin" so'rov o'qituvchini oldinroq o'qib oldi (masalan o'quvchi testni tugatyapti)
        slow_teacher = slow.scalar(main.select(Teacher))
        assert slow_teacher.credits == start
        # Shu orada Payme 1000 kredit yozdi
        fast_teacher = fast.scalar(main.select(Teacher))
        add_credits(fast_teacher, 1000)
        fast.commit()
        # Eski "credits = credits - 1" bo'lganda bu yerda 1000 kredit yo'qolardi
        add_credits(slow_teacher, -1)
        slow.commit()
        assert slow_teacher.credits == start + 999
    finally:
        slow.close()
        fast.close()
    assert credits_of(555) == start + 999

    # Kamaytirish noldan pastga tushmaydi; yetmagan balansdan pul yechilmaydi
    with SessionLocal() as session:
        teacher = session.scalar(main.select(Teacher))
        add_credits(teacher, -10**9)
        assert teacher.credits == 0
        assert withdraw_balance(teacher, 1) is False and teacher.balance == 0
        add_credits(teacher, start)
        session.commit()


def test_yakunlash_ikki_marta_bosilsa_kredit_bir_marta_yechiladi():
    reset_database()
    register_teacher(tg_id=555)
    created = create_test(tg_id=555)
    before = credits_of(555)

    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
    finish_and_grade(started["attempt_id"], json={"answers": {"1": "B"}}, headers=as_user(901))
    again = client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {}}, headers=as_user(901))
    assert again.status_code == 400, again.text
    assert credits_of(555) == before - 1


def test_test_kodini_taxmin_qilish_cheklanadi():
    reset_database()
    register_teacher(tg_id=555)
    created = create_test(tg_id=555)

    assert client.get(f"/api/tests/{created['code']}", headers=as_user(901)).status_code == 200
    for _ in range(main.TEST_CODE_MAX_FAILURES):
        assert client.get("/api/tests/ZZZZ", headers=as_user(901)).status_code == 404
    # Endi to'g'ri kod ham vaqtincha ochilmaydi — boshqa foydalanuvchiga esa ta'sir qilmaydi
    blocked = client.get(f"/api/tests/{created['code']}", headers=as_user(901))
    assert blocked.status_code == 429 and "daqiqadan keyin" in blocked.text, blocked.text
    assert client.get(f"/api/tests/{created['code']}", headers=as_user(902)).status_code == 200


def test_sertifikatlar_oquvchilarga_yuboriladi():
    """Har bir Telegram o'quvchisiga o'z sertifikati; qo'lda qo'shilgan o'tkaziladi; o'qituvchiga hisobot."""
    reset_database()
    register_teacher(tg_id=555)
    created = create_test(tg_id=555)
    sent, restore = _capture_result_notices()
    try:
        for tg_id, answer in ((901, "B"), (902, "A")):
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
            client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": answer}}, headers=as_user(tg_id))
    finally:
        restore()
    # Telegram'siz (qog'ozda ishlagan) o'quvchi — unga yuborib bo'lmaydi
    with SessionLocal() as session:
        session.add(Attempt(test_id=created["id"], offline_name="Qog'ozda Ishlagan", status="done",
                            correct=1, wrong=3, percent=25.0, finished_at=utcnow()))
        session.commit()

    pdfs, messages = [], []

    class InstantThread:  # fon ipi o'rniga darhol bajariladi
        def __init__(self, target, args, daemon):
            self.target, self.args = target, args

        def start(self):
            self.target(*self.args)

    originals = (main._send_pdf_to_telegram, main.threading.Thread, main.httpx.post)
    main._send_pdf_to_telegram = lambda chat_id, name, data, caption="": pdfs.append((chat_id, data, caption))
    main.threading.Thread = InstantThread
    main.httpx.post = lambda url, json=None, **kw: messages.append(json)
    try:
        # Begona o'qituvchi yubora olmaydi
        register_teacher(tg_id=556)
        assert client.post(f"/api/teacher/tests/{created['id']}/certificates/send-students",
                           headers=as_user(556)).status_code == 404
        # Test hali ochiq — yuborilmaydi
        still_open = client.post(f"/api/teacher/tests/{created['id']}/certificates/send-students", headers=as_user(555))
        assert still_open.status_code == 409 and "yakunlang" in still_open.text, still_open.text
        assert not pdfs
        with SessionLocal() as session:
            from backend.database import Test as DbTest
            session.get(DbTest, created["id"]).status = "closed"
            session.commit()
        response = client.post(f"/api/teacher/tests/{created['id']}/certificates/send-students", headers=as_user(555))
    finally:
        main._send_pdf_to_telegram, main.threading.Thread, main.httpx.post = originals

    assert response.status_code == 200 and response.json() == {"queued": 2, "skipped": 1}, response.text
    assert sorted(chat_id for chat_id, _, _ in pdfs) == [901, 902], pdfs
    assert all(data.startswith(b"%PDF") and "Daraja:" in caption for _, data, caption in pdfs)
    assert messages and messages[-1]["chat_id"] == 555 and "2 ta" in messages[-1]["text"], messages
    assert not main._certificates_in_progress


def test_oquvchi_yakunlangan_testini_koradi():
    """Test yopilmaguncha natija yopiq; yopilgach — o'quvchi o'z javoblari, to'g'ri javob va balini ko'radi."""
    reset_database()
    register_teacher(tg_id=555)
    created = create_test(tg_id=555)  # 1-savolning to'g'ri javobi B
    sent, restore = _capture_result_notices()
    try:
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901)).json()
        client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "A"}}, headers=as_user(901))
    finally:
        restore()

    completed = client.get("/api/me/attempts", headers=as_user(901)).json()["completed"]
    assert completed[0]["attempt_id"] == started["attempt_id"] and completed[0]["can_review"] is False, completed
    url = f"/api/me/attempts/{started['attempt_id']}/review"
    early = client.get(url, headers=as_user(901))
    assert early.status_code == 403 and "yakunlangach" in early.text, early.text

    with SessionLocal() as session:
        from backend.database import Test as DbTest
        session.get(DbTest, created["id"]).status = "closed"
        session.commit()

    assert client.get("/api/me/attempts", headers=as_user(901)).json()["completed"][0]["can_review"] is True
    review = client.get(url, headers=as_user(901))
    assert review.status_code == 200, review.text
    body = review.json()
    first = body["questions"][0]
    assert first["chosen"] == "A" and first["correct"] == "B" and first["is_correct"] is False, first
    assert body["grade"] and body["rank"] == 1 and body["participants"] == 1, body
    # Boshqa o'quvchi birovning natijasini ko'ra olmaydi
    assert client.get(url, headers=as_user(902)).status_code == 404


def test_otgan_tugash_vaqti_bilan_test_yaratilmaydi():
    """Tugash vaqti o'tgan test o'zi yopilib, o'quvchilarga "Test yakunlangan" chiqardi — endi yaratilmaydi."""

    reset_database()
    register_teacher()
    now = utcnow()
    iso = lambda moment: moment.strftime("%Y-%m-%dT%H:%M:%S")  # noqa: E731
    body = {"title": "Vaqtli", "subject": "Matematika", "mode": "key", "enrollment_type": "open", "price": 0,
            "duration": 30, "key_questions": [{"number": 1, "question_type": "single", "option_count": 4, "correct": "B"}]}

    # 12:00 da boshlanib, soatda adashib 03:00 (allaqachon o'tgan) tanlangan
    past = client.post("/api/teacher/tests", headers=as_user(555), json={
        **body, "start_time": iso(now + timedelta(hours=1)), "end_time": iso(now - timedelta(hours=9))})
    assert past.status_code == 422 and "o'tib ketgan" in past.json()["detail"], past.text
    reversed_window = client.post("/api/teacher/tests", headers=as_user(555), json={
        **body, "start_time": iso(now + timedelta(hours=5)), "end_time": iso(now + timedelta(hours=2))})
    assert reversed_window.status_code == 422, reversed_window.text

    # To'g'ri oyna: hozir boshlanib 3 soat ochiq — o'quvchi kira oladi, fondagi tekshiruv yopmaydi
    created = client.post("/api/teacher/tests", headers=as_user(555), json={
        **body, "start_time": iso(now - timedelta(minutes=1)), "end_time": iso(now + timedelta(hours=3))}).json()
    main._finish_expired_tests_once()
    started = client.post(f"/api/tests/{created['code']}/start", headers=as_user(900))
    assert started.status_code == 200, started.text


def test_oqituvchi_testni_tahrirlaydi_va_vaqtini_chozadi():
    reset_database()
    register_teacher(tg_id=555)
    created = create_test(tg_id=555)
    url = f"/api/teacher/tests/{created['id']}"
    future = (utcnow() + timedelta(hours=2)).replace(microsecond=0).isoformat()

    # Hali hech kim boshlamagan — hammasi o'zgaradi
    edited = client.patch(url, json={"title": "  Yangi   nom ", "duration": 20, "end_time": future}, headers=as_user(555))
    assert edited.status_code == 200, edited.text
    assert edited.json()["title"] == "Yangi nom" and edited.json()["duration"] == 20
    assert client.patch(url, json={"duration": 99}, headers=as_user(556)).status_code in (403, 404)
    past = (utcnow() - timedelta(minutes=5)).isoformat()
    assert client.patch(url, json={"end_time": past}, headers=as_user(555)).status_code == 422

    # O'quvchi boshladi — davomiylik faqat uzayadi, boshlanish vaqti o'zgarmaydi
    client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(901))
    assert client.get(url, headers=as_user(555)).json()["has_attempts"] is True
    shorter = client.patch(url, json={"duration": 10}, headers=as_user(555))
    assert shorter.status_code == 409 and "uzaytirish" in shorter.text, shorter.text
    assert client.patch(url, json={"duration": 45}, headers=as_user(555)).json()["duration"] == 45
    assert client.patch(url, json={"start_time": future}, headers=as_user(555)).status_code == 409

    # Vaqti tugab yopilgan test cho'zilsa — qayta ochiladi
    with SessionLocal() as session:
        from backend.database import Test as DbTest
        row = session.get(DbTest, created["id"])
        row.status, row.results_sent_at = "closed", utcnow()
        session.commit()
    later = (utcnow() + timedelta(hours=3)).replace(microsecond=0).isoformat()
    reopened = client.patch(url, json={"end_time": later}, headers=as_user(555)).json()
    assert reopened["reopened"] is True and reopened["status"] == "active", reopened
    assert reopened["code"] != created["code"], "qayta ochilgan test yangi kod olmadi"


# ========== Pullik test: o'quvchi o'qituvchi kartasiga to'laydi (chek bilan) ==========


class CardPaymentTelegramStub:
    """O'qituvchiga ketadigan chek va foydalanuvchilarga xabarlarni ushlab qoladi."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.receipts: list[tuple[int, str]] = []  # (o'qituvchi chat_id, izoh)
        self.messages: list[tuple[int, str]] = []

    def __enter__(self):
        self._send, self._notify, self._providers = main._send_test_receipt_to_teacher, main._notify_user, main._enabled_providers

        def send(payment, test, student, image, mime, extension):
            if self.fail:
                raise RuntimeError("Telegram javob bermadi")
            self.receipts.append((test.teacher.user.tg_id, main._test_payment_caption(payment, test, student)))
            return f"file-{payment.id}"

        main._send_test_receipt_to_teacher = send
        main._notify_user = lambda chat_id, text: self.messages.append((chat_id, text))
        main._enabled_providers = lambda: []  # Payme/Click ulanmagan — faqat karta
        return self

    def __exit__(self, *exc):
        main._send_test_receipt_to_teacher, main._notify_user, main._enabled_providers = self._send, self._notify, self._providers


def _pay_card(code: str, tg_id: int, receipt: str = FAKE_RECEIPT):
    return client.post(f"/api/tests/{code}/pay-card", json={"receipt_base64": receipt}, headers=as_user(tg_id))


def test_pullik_test_oquvchi_oqituvchi_kartasiga_tolaydi():
    reset_database()
    register_teacher(tg_id=555)
    register_teacher(tg_id=556)
    credits_before = credits_of(555)

    with CardPaymentTelegramStub() as tg:
        # Karta yo'q — pullik test yaratilmaydi; noto'g'ri karta qabul qilinmaydi
        no_card = client.post("/api/teacher/tests", headers=as_user(555), json={
            "title": "Pullik", "subject": "Matematika", "mode": "key", "price": 15000,
            "key_questions": [{"number": 1, "question_type": "single", "option_count": 4, "correct": "B"}]})
        assert no_card.status_code == 422 and "karta" in no_card.text, no_card.text
        assert client.put("/api/teacher/card", json={"card_number": "8600 1234 5678", "card_expiry": "12/30", "card_holder": "Ali Valiyev"},
                          headers=as_user(555)).status_code == 422
        card = client.put("/api/teacher/card", json={"card_number": "8600-1234 5678 9012", "card_expiry": "12/30", "card_holder": "  Ali   Valiyev "},
                          headers=as_user(555))
        assert card.status_code == 200 and card.json() == {"card_number": "8600123456789012", "card_expiry": "12/30", "card_holder": "Ali Valiyev"}

        created = create_test(tg_id=555, price=15000)
        code = created["code"]

        # O'quvchi test kartasida o'qituvchi kartasini ko'radi; to'lamasdan boshlay olmaydi
        info = client.get(f"/api/tests/{code}", headers=as_user(901)).json()
        assert info["paid"] is False and info["card_payment_pending"] is False
        assert info["teacher_card"] == {"number": "8600 1234 5678 9012", "holder": "Ali Valiyev"}, info
        assert client.post(f"/api/tests/{code}/start", json={}, headers=as_user(901)).status_code == 402

        # Rasm bo'lmagan chek rad etiladi
        bad = _pay_card(code, 901, base64.b64encode(b"not an image" * 20).decode())
        assert bad.status_code == 422, bad.text

        # Chek yuboriladi — o'qituvchiga boradi, ikkinchisi kutish paytida qabul qilinmaydi
        sent = _pay_card(code, 901)
        assert sent.status_code == 200 and sent.json()["pending"] is True, sent.text
        payment_id = sent.json()["payment_id"]
        assert tg.receipts and tg.receipts[0][0] == 555 and "15 000" in tg.receipts[0][1], tg.receipts
        assert _pay_card(code, 901).status_code == 409
        assert client.get(f"/api/tests/{code}", headers=as_user(901)).json()["card_payment_pending"] is True
        assert client.post(f"/api/tests/{code}/start", json={}, headers=as_user(901)).status_code == 402

        # Payme/Click bu to'lovni to'lay olmaydi (ikki marta to'lanmasin)
        with SessionLocal() as session:
            assert main._payme_find_order_by_account({"account": {"payment_id": payment_id}}, session) is None
            assert main._click_find_order(session, f"t{payment_id}") is None

        # Boshqa o'qituvchi tasdiqlay olmaydi — na ilovada, na bot tugmasida
        assert client.post(f"/api/teacher/test-payments/{payment_id}/approve", headers=as_user(556)).status_code == 404
        with SessionLocal() as session:
            ok, _ = main.decide_test_payment(session, payment_id, approve=True, teacher_tg_id=556)
            assert ok is False
        assert client.get(f"/api/tests/{code}", headers=as_user(901)).json()["paid"] is False

        # O'z o'qituvchisi ro'yxatda ko'radi va tasdiqlaydi — o'quvchiga xabar, test ochiladi
        listing = client.get("/api/teacher/test-payments", headers=as_user(555)).json()
        assert [item["id"] for item in listing["pending"]] == [payment_id], listing
        approved = client.post(f"/api/teacher/test-payments/{payment_id}/approve", headers=as_user(555))
        assert approved.status_code == 200, approved.text
        assert tg.messages and tg.messages[-1][0] == 901 and code in tg.messages[-1][1], tg.messages
        assert client.post(f"/api/teacher/test-payments/{payment_id}/approve", headers=as_user(555)).status_code == 409
        assert client.get(f"/api/tests/{code}", headers=as_user(901)).json()["paid"] is True

        # Test ishlanadi: o'qituvchidan kredit yechilmaydi, platforma balansiga ham pul yozilmaydi
        started = client.post(f"/api/tests/{code}/start", json={}, headers=as_user(901))
        assert started.status_code == 200, started.text
        sent_notices, restore = _capture_result_notices()
        try:
            client.post(f"/api/attempts/{started.json()['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
        finally:
            restore()
        balance = client.get("/api/teacher/balance", headers=as_user(555)).json()
        assert balance["credits"] == credits_before and balance["balance"] == 0, balance

        # Rad etilsa — o'quvchiga xabar, test yopiq qoladi, qayta chek yuborish mumkin
        second = _pay_card(code, 902).json()["payment_id"]
        rejected = client.post(f"/api/teacher/test-payments/{second}/reject", headers=as_user(555))
        assert rejected.status_code == 200 and tg.messages[-1][0] == 902 and "tasdiqlanmadi" in tg.messages[-1][1]
        assert client.get(f"/api/tests/{code}", headers=as_user(902)).json()["paid"] is False
        assert _pay_card(code, 902).status_code == 200

    # Chek o'qituvchiga yetmasa — saqlanmaydi, o'quvchi qayta urina oladi
    with CardPaymentTelegramStub(fail=True):
        failed = _pay_card(code, 903)
        assert failed.status_code == 502, failed.text
    with CardPaymentTelegramStub():
        assert _pay_card(code, 903).status_code == 200


def _subject_key(subject: str) -> list[dict]:
    """Fan pasporti bo'yicha to'liq javoblar kaliti (a/b qismlari bilan)."""
    from backend.subjects import subject_config

    config = subject_config(subject)
    key = [{"number": n, "question_type": "single", "option_count": config["options"].get(n, 4), "correct": "A"}
           for n in range(1, config["tests"] + 1)]
    for n in range(config["tests"] + 1, config["tests"] + config["written"] + 1):
        bands = config.get("part_overrides", {}).get(n, config["parts"])
        if bands > 1:
            start = len(key) + 1
            key += [{"number": start + band, "label": f"{n}{chr(97 + band)}", "question_type": "text", "correct": str(band + 1)}
                    for band in range(bands)]
        else:
            key.append({"number": len(key) + 1, "label": str(n), "question_type": "text", "correct": "1"})
    return key


def test_yozma_ish_har_bandi_kalitga_solishtiriladi():
    """Kimyo 41–43: har bandning yakuniy javobi kalit bilan; ko'proq to'g'ri band — ko'proq ball."""
    reset_database()
    register_teacher()
    key = _subject_key("Kimyo")
    created = create_test(subject="Kimyo", key_questions=key)
    refs = {item["label"]: str(item["number"]) for item in key if item.get("label")}
    assert {"41a", "41b", "41c", "43c"} <= set(refs), sorted(refs)[-6:]

    sent, restore = _capture_result_notices()
    try:
        for tg_id, bands in ((7101, ["41a", "41b", "41c"]), (7102, ["41a"]), (7103, [])):
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(tg_id)).json()
            assert len(started["questions"]) == 43, len(started["questions"])  # 41 — bitta savol, 3 band
            q41 = next(q for q in started["questions"] if q["number"] == 41)
            assert [part["label"] for part in q41["parts"]] == ["a", "b", "c"], q41
            answers = {refs[band]: str(ord(band[-1]) - 96) for band in bands}  # 41a→1, 41b→2, 41c→3
            client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": answers}, headers=as_user(tg_id))
    finally:
        restore()

    rows = {row["name"]: row for row in client.get(f"/api/teacher/tests/{created['id']}/results",
                                                    headers=as_user(555)).json()["rows"]}
    full, one, none = rows["Dev 7101"], rows["Dev 7102"], rows["Dev 7103"]
    assert full["ball"] > one["ball"] > none["ball"], (full["ball"], one["ball"], none["ball"])
    assert full["correct"] == 1 and one["correct"] == 0  # foizda: savol butun to'g'ri bo'lsagina
    assert full["sections"]["Umumiy kimyo"] > one["sections"]["Umumiy kimyo"] > 0  # bo'limda — qisman


def test_har_fan_milliy_sertifikat_pasporti_boyicha_ishlaydi():
    """Har bir fan: bo'limlar shablondan, natija/PDF/sertifikat bo'limlar bilan, klaviatura fanga mos."""
    from backend.subjects import SUBJECTS

    reset_database()
    register_teacher()
    expected_keyboard = {"Matematika": "math", "Fizika": "math", "Kimyo": "math", "Biologiya": "text",
                         "Tarix": "text", "Geografiya": "text", "Rus tili": "text",
                         "Ona tili va adabiyot": "text"}
    assert set(SUBJECTS) == set(expected_keyboard)
    for index, (subject, config) in enumerate(SUBJECTS.items()):
        created = create_test(subject=subject, key_questions=_subject_key(subject), duration=config["duration"])
        sent, restore = _capture_result_notices()
        try:
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(7000 + index))
            assert started.status_code == 200, (subject, started.text)
            assert started.json()["keyboard"] == expected_keyboard[subject], subject
            assert len(started.json()["questions"]) == config["tests"] + config["written"], subject
            client.post(f"/api/attempts/{started.json()['attempt_id']}/finish", json={"answers": {"1": "A", "2": "A"}},
                        headers=as_user(7000 + index))
        finally:
            restore()

        results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
        assert results["section_names"] == [name for name, _ in config["sections"]], (subject, results["section_names"])
        row = results["rows"][0]
        assert set(row["sections"]) == set(results["section_names"]) and row["grade"], (subject, row)
        assert sum(1 for value in row["sections"].values() if value > 0) >= 1, (subject, row["sections"])

        for url in (f"/api/teacher/tests/{created['id']}/pdf", f"/api/teacher/tests/{created['id']}/certificates"):
            pdf = client.get(url, headers=as_user(555))
            assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), (subject, url)

        questions = client.get(f"/api/teacher/tests/{created['id']}/questions", headers=as_user(555)).json()
        assert questions["keyboard"] == expected_keyboard[subject], subject

    # Ko'p bo'limni qo'lda ham saqlash mumkin (Fizikada 6 ta)
    saved = client.put(f"/api/teacher/tests/{created['id']}/sections", headers=as_user(555),
                       json={"text": "\n".join(f"{name}: {n}-{n + 4}" for name, n in zip("ABCDEF", range(1, 31, 5)))})
    assert saved.status_code == 200 and len(saved.json()["sections"]) == 6, saved.text


def test_sahifada_fanlar_pasporti_bor():
    html = client.get("/").text
    config_at, app_at = html.find("window.SUBJECTS_CONFIG"), html.find('src="/js/app.js')
    assert 0 < config_at < app_at, (config_at, app_at)
    assert '"Fizika"' in html and '"keyboard": "text"' in html


def test_natija_xabarlari_navbat_bilan_yetkaziladi():
    """300 o'quvchi bir vaqtda tugatsa: xabarlar birlashtiriladi, hammasi yetadi, chatga ≥1 s oraliq."""
    import time as _time

    sent: list[tuple[int, str, float]] = []
    originals = (main._telegram_send_message, main.NOTICE_BATCH_WINDOW, main.NOTICE_CHAT_INTERVAL, main.BOT_TOKEN)
    main._telegram_send_message = lambda chat, text, markup=None: sent.append((chat, text, _time.monotonic())) or True
    main.NOTICE_BATCH_WINDOW, main.NOTICE_CHAT_INTERVAL = 0.2, 0.3
    try:
        for index in range(300):
            main._send_result_notice((555, f"Natija #{index:03d} " + "x" * 40))
        main._send_result_notice((777, "boshqa o'qituvchi"))
        deadline = _time.monotonic() + 20
        while _time.monotonic() < deadline and sum(text.count("Natija #") for _, text, _ in sent) < 300:
            _time.sleep(0.1)
    finally:
        main._telegram_send_message, main.NOTICE_BATCH_WINDOW, main.NOTICE_CHAT_INTERVAL, main.BOT_TOKEN = originals

    to_teacher = [item for item in sent if item[0] == 555]
    delivered = [line for _, text, _ in to_teacher for line in text.split("\n") if line.startswith("Natija #")]
    assert sorted(delivered) == sorted(f"Natija #{index:03d} " + "x" * 40 for index in range(300)), len(delivered)
    assert len(to_teacher) < 30, f"birlashtirilmadi: {len(to_teacher)} ta xabar"
    assert all(len(text) <= 4096 for _, text, _ in to_teacher)
    gaps = [b[2] - a[2] for a, b in zip(to_teacher, to_teacher[1:])]
    assert all(gap >= 0.29 for gap in gaps), gaps
    assert any(chat == 777 for chat, _, _ in sent)


def test_yangi_foydalanuvchi_bir_vaqtdagi_sorovlarda_xato_bermaydi():
    """Ilova birinchi ochilganda ikki so'rov bir vaqtda foydalanuvchi yaratsa — 500 emas."""
    from backend.database import User

    reset_database()
    with SessionLocal() as other:  # "boshqa so'rov" foydalanuvchini allaqachon yaratib qo'ydi
        other.add(User(tg_id=4321, full_name="Birinchi"))
        other.commit()

    session = SessionLocal()
    real_scalar = session.scalar
    calls = {"n": 0}

    def racing_scalar(statement, *args, **kwargs):
        calls["n"] += 1
        return None if calls["n"] == 1 else real_scalar(statement, *args, **kwargs)  # 1-SELECT hali ko'rmagan

    session.scalar = racing_scalar
    try:
        user = auth.get_current_user(x_init_data="", x_dev_user="4321", db=session, x_app_session="")
        assert user.tg_id == 4321 and user.full_name == "Birinchi"
    finally:
        session.close()


def test_haddan_tashqari_yuklamada_faqat_avtosaqlash_kutadi():
    """Server band bo'lsa: avtosaqlash darhol 503 oladi, qolgan so'rovlar (yakunlash ham) o'taveradi."""
    import asyncio

    reached = []

    async def inner(scope, receive, send):
        reached.append(scope["path"])
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b"ok"})

    shedder = main.AutosaveLoadShedding(inner, shed_at=2)

    async def request(method, path):
        statuses = []

        async def send(message):
            if message["type"] == "http.response.start":
                statuses.append(message["status"])

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        await shedder({"type": "http", "method": method, "path": path, "headers": []}, receive, send)
        return statuses[0]

    async def scenario():
        assert await request("POST", "/api/attempts/5/answers") == 200  # band emas — o'tadi
        shedder.in_flight = 2  # band
        results = {
            "saqlash": await request("POST", "/api/attempts/5/answers"),
            "yakunlash": await request("POST", "/api/attempts/5/finish"),
            "boshlash": await request("POST", "/api/tests/ABCD/start"),
            "ochish": await request("GET", "/api/me"),
        }
        return results

    results = asyncio.run(scenario())
    assert results == {"saqlash": 503, "yakunlash": 200, "boshlash": 200, "ochish": 200}, results
    assert "/api/attempts/5/finish" in reached and reached.count("/api/attempts/5/answers") == 1
    assert shedder.in_flight == 2  # hisoblagich to'g'ri qaytdi


def test_chek_eslatmalari_oqituvchi_etiboridan_chetda_qolmaydi():
    """Javobsiz chek: 5 daqiqadan keyin eslatma, 30 daqiqada takror, ko'pi bilan 6; o'quvchi ham eslata oladi."""
    from backend.database import Payment

    reset_database()
    register_teacher(tg_id=555)
    with CardPaymentTelegramStub():
        client.put("/api/teacher/card", json={"card_number": "8600123456789012", "card_expiry": "12/30", "card_holder": "Ali Valiyev"},
                   headers=as_user(555))
        code = create_test(tg_id=555, price=10000)["code"]
        payment_id = _pay_card(code, 901).json()["payment_id"]

        sent = []
        original_post = main.httpx.post
        main.httpx.post = lambda url, **kwargs: (sent.append((url, kwargs.get("json") or kwargs.get("data"))),
                                                 SimpleNamespace(status_code=200, json=lambda: {"ok": True}))[1]

        def age(minutes=0, reminded_hours_ago=None):
            with SessionLocal() as session:
                payment = session.get(Payment, payment_id)
                payment.created_at = utcnow() - timedelta(minutes=minutes)
                if reminded_hours_ago is not None:
                    payment.reminded_at = utcnow() - timedelta(hours=reminded_hours_ago)
                session.commit()

        def remind():
            sent.clear()
            with SessionLocal() as session:
                main._remind_pending_payments(session)
            return [body for url, body in sent if url.endswith("/sendMessage")]

        try:
            # O'quvchi juda erta eslatsa — kutishni so'raymiz
            early = client.post(f"/api/tests/{code}/pay-card/remind", headers=as_user(901))
            assert early.status_code == 429 and "daqiqadan keyin" in early.text, early.text
            assert remind() == []  # 5 daqiqa o'tmagan

            age(minutes=20)
            messages = remind()
            assert len(messages) == 1 and messages[0]["chat_id"] == 555, messages
            assert "1 ta o'quvchi" in messages[0]["text"] and "10 000" in messages[0]["text"], messages[0]["text"]
            assert remind() == []  # 30 daqiqa o'tmaguncha takrorlanmaydi

            # /api/me — o'qituvchi ilovani ochganda "N ta ruxsat kutmoqda" oynasi uchun
            assert client.get("/api/me", headers=as_user(555)).json()["pending_payments"] == 1
            assert client.get("/api/me", headers=as_user(901)).json()["pending_payments"] == 0

            for _ in range(5):
                age(minutes=300, reminded_hours_ago=1)
                assert len(remind()) == 1
            age(minutes=600, reminded_hours_ago=1)
            assert remind() == []  # 6 tadan ortiq emas

            # O'quvchi eslatadi: chek qayta yuboriladi (yaqinda eslatilgan bo'lsa — kutadi)
            age(minutes=60, reminded_hours_ago=1)
            resent = []
            original_photo = main._post_receipt_photo
            main._post_receipt_photo = lambda *args, **kwargs: resent.append(kwargs.get("caption_prefix")) or "file"
            try:
                assert client.post(f"/api/tests/{code}/pay-card/remind", headers=as_user(901)).status_code == 200
                assert resent and "eslatmoqda" in resent[0]
                assert client.post(f"/api/tests/{code}/pay-card/remind", headers=as_user(901)).status_code == 429
            finally:
                main._post_receipt_photo = original_photo

            # Ruxsat berilgach — eslatma to'xtaydi
            client.post(f"/api/teacher/test-payments/{payment_id}/approve", headers=as_user(555))
            age(minutes=600, reminded_hours_ago=5)
            assert remind() == []
        finally:
            main.httpx.post = original_post


def test_bot_xabarlari_oxirida_kutilayotgan_chek_eslatiladi():
    """O'qituvchiga natija xabari ketganda, ruxsat kutayotgan chek bo'lsa — xabar oxirida eslatma va tugma."""
    import time as _time

    reset_database()
    register_teacher(tg_id=555)
    with CardPaymentTelegramStub():
        client.put("/api/teacher/card", json={"card_number": "8600123456789012", "card_expiry": "12/30", "card_holder": "Ali Valiyev"},
                   headers=as_user(555))
        code = create_test(tg_id=555, price=10000)["code"]
        _pay_card(code, 901)

    sent = []
    originals = (main._telegram_send_message, main.NOTICE_BATCH_WINDOW, main.WEBAPP_URL)
    main._telegram_send_message = lambda chat, text, markup=None: sent.append((chat, text, markup)) or True
    main.NOTICE_BATCH_WINDOW, main.WEBAPP_URL = 0.1, "https://example.com"
    try:
        main._send_result_notice((555, "📝 Yangi natija: Vali"))
        main._send_result_notice((777, "boshqa o'qituvchi"))
        deadline = _time.monotonic() + 10
        while _time.monotonic() < deadline and len(sent) < 2:
            _time.sleep(0.05)
    finally:
        main._telegram_send_message, main.NOTICE_BATCH_WINDOW, main.WEBAPP_URL = originals

    teacher = next(item for item in sent if item[0] == 555)
    assert "1 ta o'quvchi to'lov qildi va ruxsat kutmoqda" in teacher[1], teacher[1]
    assert teacher[2] and "open=payments" in json.dumps(teacher[2]), teacher[2]
    other = next(item for item in sent if item[0] == 777)
    assert "ruxsat kutmoqda" not in other[1] and other[2] is None


def test_tarix_atamasi_tutuq_belgisi_har_xil_bolsa_ham_togri():
    from backend.main import _normalize_text_answer as norm

    # O'quvchi telefonda "'" yozadi, kalitda "‘" yoki "ʻ" bo'lishi mumkin
    assert norm("Farg'ona") == norm("Farg‘ona") == norm("Fargʻona") == norm("Farg`ona")
    assert norm("Qo‘qon xonligi") == norm("qo'qon xonligi")
    assert norm("Farg'ona") != norm("Fargona")


def test_click_payme_pul_balansga_tushadi_va_botdan_yechiladi():
    reset_database()
    register_teacher()
    client.put("/api/teacher/card", json={"card_number": "8600123412341234", "card_expiry": "12/30", "card_holder": "Ali"}, headers=as_user(555))
    created = create_test(price=10_000)

    # Click/Payme ulangan — o'quvchiga o'qituvchi kartasi ko'rsatilmaydi, chek qabul qilinmaydi
    info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
    assert info["teacher_card"] is None and info["payment_providers"], info
    assert _pay_card(created["code"], 901).status_code == 409

    notices, admin_calls = [], []
    original_notice, original_admin = main._send_result_notice, main._notify_admin_about_withdrawal
    main._send_result_notice = notices.append
    main._notify_admin_about_withdrawal = lambda request, teacher: admin_calls.append(request.amount)
    try:
        payment = client.post(f"/api/tests/{created['code']}/pay", json={}, headers=as_user(901)).json()
        payme("CreateTransaction", {"id": "tx-w", "time": 1, "account": {"payment_id": str(payment["payment_id"])},
                                    "amount": payment["amount"] * 100})
        payme("PerformTransaction", {"id": "tx-w"})
        assert balance_of() == 7000
        assert notices and notices[0][0] == 555 and "+7 000 so'm" in notices[0][1] and "/pul" in notices[0][1], notices

        with SessionLocal() as session:
            text, amount = main.bot_withdrawal_overview(session, 555)
            assert amount == 7000 and "7 000" in text and "8600 1234 1234 1234" in text, text
            assert main.bot_withdrawal_overview(session, 901)[1] == 0  # o'qituvchi emas
        with SessionLocal() as session:
            ok, text = main.bot_request_withdrawal(session, 555)
            assert ok and admin_calls == [7000], (text, admin_calls)
        with SessionLocal() as session:
            # Ikkinchi marta bosilsa — yangi so'rov yaratilmaydi
            assert main.bot_request_withdrawal(session, 555)[0] is False
            assert main.bot_withdrawal_overview(session, 555)[1] == 0
            from sqlalchemy import func, select
            from backend.database import WithdrawalRequest
            assert session.scalar(select(func.count()).select_from(WithdrawalRequest)) == 1
    finally:
        main._send_result_notice, main._notify_admin_about_withdrawal = original_notice, original_admin


def test_vaqti_tugagan_test_qayta_ochilsa_ishlaydi():
    reset_database()
    register_teacher()
    created = create_test()
    with SessionLocal() as session:
        from backend.database import Test as DbTest
        row = session.get(DbTest, created["id"])
        row.status, row.end_time = "closed", utcnow() - timedelta(hours=1)
        session.commit()

    reopened = client.post(f"/api/teacher/tests/{created['id']}/reopen", headers=as_user(555)).json()
    info = client.get(f"/api/tests/{reopened['code']}", headers=as_user(901)).json()
    assert info["end_time"] is None, info
    started = client.post(f"/api/tests/{reopened['code']}/start", json={}, headers=as_user(901))
    assert started.status_code == 200, started.text


def test_karta_muddati_kiritiladi_oquvchiga_korinmaydi():
    reset_database()
    register_teacher()
    url, base = "/api/teacher/card", {"card_number": "8600123412341234", "card_holder": "Ali Valiyev"}
    for bad in ("13/30", "1/3", "01/20", ""):
        assert client.put(url, json={**base, "card_expiry": bad}, headers=as_user(555)).status_code == 422, bad
    assert client.put(url, json=base, headers=as_user(555)).status_code == 422  # muddat majburiy
    saved = client.put(url, json={**base, "card_expiry": "0830"}, headers=as_user(555)).json()
    assert saved["card_expiry"] == "08/30", saved
    assert client.get(url, headers=as_user(555)).json()["card_expiry"] == "08/30"

    original = main._enabled_providers
    main._enabled_providers = lambda: []
    try:
        created = create_test(price=10_000)
        info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
    finally:
        main._enabled_providers = original
    assert info["teacher_card"] == {"number": "8600 1234 1234 1234", "holder": "Ali Valiyev"}, info
    assert "08/30" not in json.dumps(info)


def test_sozli_javob_chiziqcha_va_rim_raqami_bilan_ham_togri():
    ok = main._text_answer_is_correct
    # O'qituvchi "21 asr" — o'quvchi har xil yozadi
    for student in ("21-asr", "21asr", "21 - asr", "XXI asr", "xxi-asr", "21 Asr."):
        assert ok(student, "21 asr"), student
    assert ok("21 asr", "XXI asr") and ok("XIX asr", "19-asr")
    assert ok("Bolqon urushi", "Bolqon-urushi") and ok("Amir Temur", "amir  temur")
    assert ok("Farg‘ona", "Farg'ona") and ok("Qo'qon xonligi", "qo‘qon xonligi")
    assert ok("Bolqon urushi", "Rus-turk urushi|Bolqon urushi")
    assert not ok("20-asr", "21 asr") and not ok("", "21 asr") and not ok("Bolqon", "Bolqon urushi")
    # Matematik javoblar qat'iy qoladi: chiziqcha — minus
    assert not ok("x1", "x-1") and not ok("3", "-3") and ok("x - 1", "x-1")
    assert ok("2sqrt(3)", r"2\sqrt{3}") and not ok("23", "2-3")
    # So'zli kalitda ham raqam oldidagi minus saqlanadi
    assert not ok("3 metr", "-3 metr") and not ok("-5 gradus", "5 gradus")
    assert ok("−3 metr", "-3 metr") and ok("- 3 metr", "-3 metr") and ok("21 - asr", "21 asr")


def test_test_hammaga_elon_qilinadi():
    reset_database()
    register_teacher()
    client.put("/api/teacher/card", json={"card_number": "8600123412341234", "card_expiry": "12/30",
                                          "card_holder": "Ali"}, headers=as_user(555))
    for tg_id in (901, 902):
        client.get("/api/me", headers=as_user(tg_id))  # bot foydalanuvchilari
    created = create_test(price=15_000, title="Tarix <mock>")

    started, sent = [], []
    original_start, original_send, original_status = main._start_broadcast, main._telegram_send_message, main._telegram_send_status
    main._start_broadcast = started.append
    main._telegram_send_message = lambda chat_id, text, markup=None, parse_mode=None: sent.append((chat_id, text, markup)) or True
    main._telegram_send_status = lambda chat_id, text, markup=None, parse_mode=None: sent.append((chat_id, text, markup)) or (200, "")
    try:
        url = f"/api/teacher/tests/{created['id']}/broadcast"
        assert client.post(url, json={}, headers=as_user(901)).status_code in (403, 404)  # boshqa odam
        response = client.post(url, json={}, headers=as_user(555))
        assert response.status_code == 200 and response.json()["recipients"] == 2, response.text
        assert started == [created["id"]]
        assert client.post(url, json={}, headers=as_user(555)).status_code == 409  # faqat bir marta
        assert client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["broadcast_at"]

        main._run_broadcast(created["id"])
        students = [item for item in sent if item[0] in (901, 902)]
        assert sorted(item[0] for item in students) == [901, 902], sent
        text, markup = students[0][1], students[0][2]
        assert "Tarix &lt;mock&gt;" in text and "Pullik — 15 000 so'm" in text and created["code"] in text, text
        assert 'href="tg://user?id=555"' in text and "Ali Valiyev" in text, text
        assert sent[-1][0] == 555 and "2 ta" in sent[-1][1], sent[-1]  # o'qituvchiga hisobot

        closed = create_test(enrollment_type="closed")
        assert client.post(f"/api/teacher/tests/{closed['id']}/broadcast", json={}, headers=as_user(555)).status_code == 409

        # Telegram profil havolasini rad etsa — havolasiz ko'rinishda baribir yetkaziladi;
        # "chat not found" esa faqat o'sha odamga tegishli, ko'rinish o'zgarmaydi
        client.get("/api/me", headers=as_user(903))
        attempts = []

        def picky(chat_id, text, markup=None, parse_mode=None):
            attempts.append((chat_id, "tg://user" in text, markup is not None))
            if chat_id == 901:
                return 400, '{"description":"Bad Request: chat not found"}'
            if "tg://user" in text:
                return 400, '{"description":"Bad Request: unsupported start tag"}'
            return 200, ""

        main._telegram_send_status = picky
        sent.clear()
        main._run_broadcast(created["id"])
        assert attempts == [(901, True, True), (902, True, True), (902, False, True), (903, False, True)], attempts
        assert "2 ta" in sent[-1][1], sent
    finally:
        main._start_broadcast, main._telegram_send_message, main._telegram_send_status = original_start, original_send, original_status


def test_kredit_uchun_ikki_karta_korsatiladi():
    reset_database()
    register_teacher()
    original = main.ADMIN_CARD_NUMBER
    main.ADMIN_CARD_NUMBER = "8600 1234 5678 9012; 9860 1111 2222 3333"
    try:
        cards = client.get("/api/teacher/balance", headers=as_user(555)).json()["admin_cards"]
        assert cards == ["8600 1234 5678 9012", "9860 1111 2222 3333"], cards
        main.ADMIN_CARD_NUMBER = "9860,3501,4880,5707"  # vergul bilan yozilsa ham — bitta karta
        cards = client.get("/api/teacher/balance", headers=as_user(555)).json()["admin_cards"]
        assert cards == ["9860 3501 4880 5707"], cards
    finally:
        main.ADMIN_CARD_NUMBER = original
    assert client.get("/api/teacher/balance", headers=as_user(555)).json()["admin_cards"] == ["8600 1234 5678 9012"]


def test_yozma_javob_notogrisi_hech_qachon_togri_bolib_ketmaydi():
    """Pullik test: noto'g'ri javob to'g'ri deb olinmasin (va to'g'risi yo'qolmasin)."""
    ok = main._text_answer_is_correct
    wrong = [  # (o'quvchi, kalit) — HAR BIRI xato bo'lishi shart
        ("3", "-3"), ("-3", "3"), ("x1", "x-1"), ("x+1", "x-1"), ("23", "2(3)"), ("2-3", "2(-3)"),
        ("23", "2*3"), ("2", "2^2"), ("x2", "x^2"), ("1/2", "2"), ("12", "1/2"), ("35", "3,5"), ("0,5", "5"),
        ("2√3", "3√2"), ("√3/2", "√(3/2)"), ("π/5", "5π"), ("-1", "1"), ("1", "-1"), ("10", "1"), ("1.5", "15"),
        ("35 mln", "3,5 mln"), ("12 qism", "1/2 qism"), ("12 asr", "1 2 asr"), ("3 metr", "-3 metr"),
        ("5 gradus", "-5 gradus"), ("20-asr", "21-asr"), ("XX asr", "21 asr"), ("XXII asr", "21 asr"),
        ("Bolqon", "Bolqon urushi"), ("Temir", "Temur"), ("Buxoro", "Xiva"), ("1990", "1991"),
        ("1991-yil", "1990-yil"), ("Amir Temur", "Temur Malik"), ("", "5"), ("   ", "Temur"), ("A", "5"),
        # Tasodifiy sinov (fuzz) topgan holatlar
        ("-3^2", "(-3)^2"), ("π/2√2", "π/(2√2)"), ("1/2π", "1/2*pi"), ("π/-2x", "π/-(2x)"), ("2^2x", "2^(2x)"),
        ("3,5-5 qism", "3,5, -5 qism"), ("0,5-3 asr", "0,5 -3 asr"),
    ]
    for student, correct in wrong:
        assert not ok(student, correct), f"XATO TO'G'RI BO'LIB KETDI: {student!r} ~ {correct!r}"

    right = [  # (o'quvchi, kalit) — har biri to'g'ri bo'lishi kerak
        ("-3", "-3"), ("x - 1", "x-1"), ("2sqrt(3)", r"2\sqrt{3}"), ("0.5", "0,5"), ("pi/5", r"\frac{\pi}{5}"),
        ("2*pi", "2π"), ("x^2", "x²"), ("√(3)", "√3"), ("12 000", "12000"), ("ln(2)", "ln2"), ("sin(x)", "sinx"),
        ("21-asr", "21 asr"), ("XXI asr", "21 asr"), ("xix-asr", "19 asr"), ("21 Asr.", "21 asr"),
        ("Bolqon-urushi", "Bolqon urushi"), ("«Temur tuzuklari»", "Temur tuzuklari"), ("3.5 mln", "3,5 mln"),
        ("Farg‘ona", "Farg'ona"), ("fargʻona", "FARG'ONA"), ("−3 metr", "-3 metr"), ("1991-yil", "1991 yil"),
        ("Mavarounnahr", "Movarounnahr|Mavarounnahr"), ("1/2", "0,5|1/2"),
    ]
    for student, correct in right:
        assert ok(student, correct), f"to'g'ri javob o'tmadi: {student!r} ~ {correct!r}"


def test_oferta_sahifasi_ochiq_va_narxlar_koddan():
    response = client.get("/oferta")  # Telegram'siz, avtorizatsiyasiz
    assert response.status_code == 200 and "text/html" in response.headers["content-type"]
    text = response.text
    assert "Ommaviy oferta" in text and "1 200 so'm" in text and "450 so'm" in text, text[:300]
    assert "5 000" in text and "100 000" in text and f"{main.PLATFORM_COMMISSION_PERCENT}%" in text
    original = main.LEGAL_NAME
    main.LEGAL_NAME = "Sinov <YaTT>"
    try:
        assert "Sinov &lt;YaTT&gt;" in client.get("/oferta").text
    finally:
        main.LEGAL_NAME = original


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


def test_start_bosgan_foydalanuvchi_saqlanadi():
    from bot.bot import _remember_user

    reset_database()
    _remember_user(777, "Botdagi Odam", "odam")
    _remember_user(777, "Botdagi Odam", "odam")  # ikkinchi /start — takror yozilmaydi
    me = client.get("/api/me", headers=as_user(777))
    assert me.status_code == 200, me.text
    from backend.database import User

    with SessionLocal() as session:
        assert session.query(User).filter(User.tg_id == 777).count() == 1


def test_referal_yangi_oqituvchi_uchun_kredit():
    from backend.database import Referral, User
    from bot.bot import _remember_user

    reset_database()
    register_teacher()  # 555 — havola egasi
    with SessionLocal() as session:
        referrer = session.query(Teacher).join(User, User.id == Teacher.user_id).filter(User.tg_id == 555).one()
        referrer_id, start_credits = referrer.id, referrer.credits

    def credits() -> int:
        with SessionLocal() as session:
            return session.get(Teacher, referrer_id).credits

    stats = client.get("/api/teacher/stats", headers=as_user(555)).json()["referral"]
    assert stats["link"] == f"https://t.me/test_platform_bot?start=ref_{referrer_id}", stats
    assert (stats["invited"], stats["joined"], stats["earned"], stats["reward"]) == (0, 0, 0, 10), stats

    client.get("/api/me", headers=as_user(700))  # avval ilovaga kirgan — referal emas
    _remember_user(700, "Eski Odam", None, f"ref_{referrer_id}")
    _remember_user(701, "Noto'g'ri", None, "ref_abc")
    _remember_user(702, "Yo'q o'qituvchi", None, "ref_99999")
    _remember_user(800, "Yangi Odam", None, f"ref_{referrer_id}")
    _remember_user(801, "Obunasiz", None, f"ref_{referrer_id}")
    _remember_user(800, "Yangi Odam", None, "ref_1")  # qayta /start — o'zgarmaydi
    with SessionLocal() as session:
        assert sorted(r.referred_user_id for r in session.query(Referral)) == sorted(
            u.id for u in session.query(User).filter(User.tg_id.in_([800, 801]))
        )

    # O'quvchi bo'lib qolsa — kredit yo'q
    client.get("/api/me", headers=as_user(800))
    assert credits() == start_credits

    original = main._subscribed_to_default_channels
    main._subscribed_to_default_channels = lambda tg_id: tg_id != 801
    try:
        register_teacher(800, "Yangi", "Odam")
        assert credits() == start_credits + 10
        client.get("/api/me", headers=as_user(800))  # takror — ikkinchi marta berilmaydi
        assert credits() == start_credits + 10

        register_teacher(801, "Obunasiz", "Odam")  # kanalga obuna emas — hali berilmaydi
        assert credits() == start_credits + 10
        main._subscribed_to_default_channels = lambda tg_id: True
        client.get("/api/me", headers=as_user(801))  # obuna bo'lgach, keyingi kirishda
        assert credits() == start_credits + 20
    finally:
        main._subscribed_to_default_channels = original

    stats = client.get("/api/teacher/stats", headers=as_user(555)).json()["referral"]
    assert (stats["invited"], stats["joined"], stats["earned"]) == (2, 2, 20), stats


def test_android_ilovaga_bot_orqali_kirish():
    from datetime import timedelta

    from backend.database import AppLogin

    reset_database()
    started = client.post("/api/app-login").json()
    token = started["token"]
    assert started["link"] == f"https://t.me/test_platform_bot?start=login_{token}", started
    assert client.get(f"/api/app-login/{token}").json() == {"status": "pending"}
    assert client.get("/api/me", headers={"X-App-Session": "soxta"}).status_code == 401

    with SessionLocal() as session:
        assert main.confirm_app_login(session, "yoq-token", 4242, "Ilova Odam", "ilova")[0] is False
        assert main.confirm_app_login(session, token, 4242, "Ilova Odam", "ilova")[0] is True
        assert main.confirm_app_login(session, token, 9999, "Boshqa", None)[0] is False  # qayta tasdiqlab bo'lmaydi

    granted = client.get(f"/api/app-login/{token}").json()
    assert granted["status"] == "ok" and granted["session"], granted
    assert client.get(f"/api/app-login/{token}").status_code == 410  # seans faqat bir marta

    headers = {"X-App-Session": granted["session"]}
    me = client.get("/api/me", headers=headers)
    assert me.status_code == 200 and me.json()["tg_id"] == 4242, me.text
    assert client.post("/api/teacher/register", json={"first_name": "Ilova", "last_name": "Odam"}, headers=headers).status_code == 200

    assert client.post("/api/app-logout", headers=headers).status_code == 200
    assert client.get("/api/me", headers=headers).status_code == 401

    # 10 daqiqadan eski so'rov — eskirgan
    old = client.post("/api/app-login").json()["token"]
    with SessionLocal() as session:
        login = session.query(AppLogin).filter(AppLogin.token == old).one()
        login.created_at = login.created_at - timedelta(minutes=11)
        session.commit()
        assert main.confirm_app_login(session, old, 4242, "Ilova Odam", None)[0] is False
    assert client.get(f"/api/app-login/{old}").status_code == 410


def _sheet_photo(test_id: int, marks: dict[int, str], skew: bool = True, code_of: int | None = None) -> str:
    """Varaq "surati": doirachalar joyi PDF'dan olinadi, belgilanganlari bo'yaladi, so'ng qiyshaytiriladi."""

    from PIL import Image, ImageDraw

    from backend.database import Test
    from backend.omr import PAGE_HEIGHT, PAGE_WIDTH

    with SessionLocal() as session:
        bubbles: list[dict] = []
        sheet_test = session.get(Test, test_id)
        main.generate_answer_sheet_pdf(sheet_test, bubbles=bubbles, variants=main._variant_names(session, sheet_test) or None)
        session.expunge(sheet_test)
    zoom = 2.0
    page = Image.new("RGB", (round(PAGE_WIDTH * zoom), round(PAGE_HEIGHT * zoom)), "white")
    draw = ImageDraw.Draw(page)
    code = main.sheet_code(sheet_test) if code_of is None else code_of
    for x, y in [(32, 32), (PAGE_WIDTH - 32, 32), (32, PAGE_HEIGHT - 32), (PAGE_WIDTH - 32, PAGE_HEIGHT - 32)]:
        box = [(x - 7) * zoom, (y - 7) * zoom, (x + 7) * zoom, (y + 7) * zoom]
        if sheet_test.sheet_version >= 3:
            draw.rectangle(box, fill="black")
        else:
            draw.ellipse(box, fill="black")
    for bubble in bubbles:
        cx, cy, r = bubble["x"] * zoom, (PAGE_HEIGHT - bubble["y"]) * zoom, bubble["r"] * zoom
        if bubble["kind"] in ("mark", "code"):
            if bubble["kind"] == "mark" or code >> int(bubble["letter"]) & 1:
                draw.rectangle([cx - r, cy - r, cx + r, cy + r], fill="black")
            continue
        filled = bubble["letter"] in marks.get(bubble["ref"], "").split(",")
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=2, fill=(40, 40, 40) if filled else None)
    if skew:
        background = Image.new("RGB", (page.width + 300, page.height + 300), (90, 70, 60))
        background.paste(page.rotate(4, expand=True, fillcolor=(90, 70, 60)), (120, 110))
        page = background
    buffer = io.BytesIO()
    page.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def test_ilovada_oqilgan_varaq_natijasi_qabul_qilinadi():
    """Ilova varaqni telefonda o'qiydi: titul tuzilishini oladi, serverga faqat javoblarni yuboradi."""

    reset_database()
    register_teacher()
    test_id = create_test(enrollment_type="closed")["id"]
    base = f"/api/teacher/tests/{test_id}"
    client.post(f"{base}/students", json={"names": ["Birinchi Bola", "Ikkinchi Bola"]}, headers=as_user(555))

    layout = client.get(f"{base}/scan/layout", headers=as_user(555)).json()
    kinds = {bubble["kind"] for bubble in layout["bubbles"]}
    assert {"mark", "code", "id", "single"} <= kinds, kinds
    assert layout["page_count"] == 1 and isinstance(layout["sheet_code"], int)
    assert layout["old_bubbles"], "5-versiya testda eski 3 xonali ID'li titul tuzilishi ham beriladi"
    # Natija ekranida ranglash uchun kalit (yozma savol yo'q — u titulda bo'yalmaydi)
    assert layout["keys"] == {"A": {"1": "B", "2": "C", "3": "A,C"}}, layout["keys"]

    result = {"answers": {"1": "B"}, "questions": [{"ref": 1, "label": "1", "kind": "single", "options": ["A", "B"],
                                                    "chosen": "B", "status": "ok"}],
              "sheet_number": 2, "sheet_number_status": "ok", "variant": None, "sheet_code": layout["sheet_code"]}
    scanned = client.post(f"{base}/scan/result", json=result, headers=as_user(555))
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert body["match"]["name"] == "Ikkinchi Bola" and body["answers"] == {"1": "B"}, body
    assert body["page_count"] == 1 and [s["name"] for s in body["pending_students"]] == ["Birinchi Bola", "Ikkinchi Bola"]
    saved = client.post(f"{base}/scan/save", json={"attempt_id": body["match"]["id"], "answers": body["answers"]},
                        headers=as_user(555))
    assert saved.status_code == 200 and saved.json()["correct"] == 1, saved.text

    # Boshqa imtihonning titul kodi — rad etiladi
    other = client.post(f"{base}/scan/result", json={**result, "sheet_code": (layout["sheet_code"] + 1) % 4096},
                        headers=as_user(555))
    assert other.status_code == 422, other.text


def test_varaq_atrofidagi_qora_narsalar_skanerni_adashtirmaydi():
    """Stol ustida varaqdan tashqarida qora dog'lar bor — burchak deb olinmaydi (chet belgilari tekshiriladi)."""

    from PIL import Image, ImageDraw

    reset_database()
    register_teacher()
    test_id = create_test(enrollment_type="closed")["id"]
    sheet = Image.open(io.BytesIO(base64.b64decode(_sheet_photo(test_id, {1: "C", 2: "B"}, skew=False))))
    photo = Image.new("RGB", (sheet.width + 500, sheet.height + 500), (120, 120, 120))
    photo.paste(sheet, (250, 250))
    draw = ImageDraw.Draw(photo)
    # Kadr burchaklariga yaqin, burchak kvadratiga o'xshash dog'lar — varaq burchaklaridan ham chetroqda
    for x in (40, 110, photo.width - 140, photo.width - 70):
        for y in (40, 120, photo.height - 150, photo.height - 70):
            draw.rectangle([x, y, x + 28, y + 28], fill="black")
    buffer = io.BytesIO()
    photo.save(buffer, "JPEG", quality=85)

    scanned = client.post(f"/api/teacher/tests/{test_id}/scan",
                          json={"image_base64": base64.b64encode(buffer.getvalue()).decode()}, headers=as_user(555))
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["answers"] == {"1": "C", "2": "B"}, scanned.json()["questions"]


def test_javoblar_varaqasi_skanerlanadi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    test_id = created["id"]
    url = f"/api/teacher/tests/{test_id}/scan"
    client.post(f"/api/teacher/tests/{test_id}/students", json={"names": ["Ro'yxatdagi Bola"]}, headers=as_user(555))

    # 1-savol B (to'g'ri), 2-savol A (xato), 3-savol A,C (to'g'ri, ko'p javobli)
    scanned = client.post(url, json={"image_base64": _sheet_photo(test_id, {1: "B", 2: "A", 3: "A,C"})}, headers=as_user(555))
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert body["answers"] == {"1": "B", "2": "A", "3": "A,C"}, body["questions"]
    assert all(q["status"] == "ok" for q in body["questions"]), body["questions"]
    assert body["page_count"] == 1 and body["name_image"].startswith("data:image/png;base64,")
    assert [s["name"] for s in body["pending_students"]] == ["Ro'yxatdagi Bola"]

    # Ikki doiracha bo'yalgan oddiy savol — tanlanmaydi, o'qituvchiga belgilanadi
    double = client.post(url, json={"image_base64": _sheet_photo(test_id, {1: "A,B"})}, headers=as_user(555)).json()
    first = next(q for q in double["questions"] if q["ref"] == 1)
    assert first["status"] == "multiple" and first["chosen"] == "" and "1" not in double["answers"], first

    # Boshqa imtihonning tituli (kodi boshqacha) — qabul qilinmaydi
    other = client.post(url, json={"image_base64": _sheet_photo(test_id, {1: "B"}, code_of=main.sheet_code(SimpleNamespace(id=test_id + 1)))},
                        headers=as_user(555))
    assert other.status_code == 422 and "boshqa imtihon" in other.json()["detail"], other.text

    blank = io.BytesIO()
    from PIL import Image
    Image.new("RGB", (900, 1200), "white").save(blank, "PNG")
    bad = client.post(url, json={"image_base64": base64.b64encode(blank.getvalue()).decode()}, headers=as_user(555))
    assert bad.status_code == 422 and "qora nuqta" in bad.json()["detail"], bad.text

    credits_before = credits_of()
    saved = client.post(f"{url}/save", json={"name": "Skaner  Bola", "answers": body["answers"]}, headers=as_user(555))
    assert saved.status_code == 200, saved.text
    assert saved.json()["correct"] == 2 and saved.json()["name"] == "Skaner Bola", saved.json()
    assert credits_of() == credits_before - 1
    again = client.post(f"{url}/save", json={"name": "skaner bola", "answers": {}}, headers=as_user(555))
    assert again.status_code == 409, again.text  # bir xil ism ikki marta kiritilmaydi

    pending_id = body["pending_students"][0]["id"]
    saved = client.post(f"{url}/save", json={"attempt_id": pending_id, "answers": {"1": "B"}}, headers=as_user(555))
    assert saved.status_code == 200 and saved.json()["name"] == "Ro'yxatdagi Bola", saved.text

    detail = client.get(f"/api/teacher/tests/{test_id}", headers=as_user(555)).json()
    assert detail["filled"] == 2 and detail["missing"] == 0, detail

    # Onlayn (ochiq) testda titul yo'q — skanerlash ham yo'q
    opened = create_test(enrollment_type="open")
    open_scan = client.post(f"/api/teacher/tests/{opened['id']}/scan",
                            json={"image_base64": _sheet_photo(opened["id"], {1: "B"})}, headers=as_user(555))
    assert open_scan.status_code == 400, open_scan.text


def test_varaqdagi_oquvchi_raqami_boyicha_taniladi():
    from backend.database import Test

    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    test_id = created["id"]
    client.post(f"/api/teacher/tests/{test_id}/students", json={"names": ["Birinchi Bola", "Ikkinchi Bola"]},
                headers=as_user(555))
    detail = client.get(f"/api/teacher/tests/{test_id}", headers=as_user(555)).json()
    assert [(s["number"], s["name"]) for s in detail["students"]] == [(1, "Birinchi Bola"), (2, "Ikkinchi Bola")]
    assert detail["sheet_version"] == 5

    url = f"/api/teacher/tests/{test_id}/scan"
    photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "0", "id3": "2", 1: "B", 2: "C"})
    scanned = client.post(url, json={"image_base64": photo}, headers=as_user(555)).json()
    assert scanned["sheet_number"] == 2 and scanned["sheet_number_status"] == "ok", scanned
    assert scanned["match"]["name"] == "Ikkinchi Bola" and scanned["match"]["status"] == "pending", scanned["match"]
    saved = client.post(f"{url}/save", json={"attempt_id": scanned["match"]["id"], "answers": scanned["answers"]},
                        headers=as_user(555))
    assert saved.json()["correct"] == 2, saved.text
    again = client.post(url, json={"image_base64": photo}, headers=as_user(555)).json()
    assert again["match"]["status"] == "done"  # qayta skanerlansa — "natijasi bor" deb ogohlantiriladi

    # Ro'yxatda yo'q raqam — yangi o'quvchi shu raqam bilan saqlanadi
    unknown = client.post(url, json={"image_base64": _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "1", "id3": "7"})},
                          headers=as_user(555)).json()
    assert unknown["sheet_number"] == 17 and unknown["match"] is None, unknown
    client.post(f"{url}/save", json={"name": "Yangi Bola", "sheet_number": 17, "answers": {}}, headers=as_user(555))
    detail = client.get(f"/api/teacher/tests/{test_id}", headers=as_user(555)).json()
    assert (17, "Yangi Bola") in [(s["number"], s["name"]) for s in detail["students"]], detail["students"]

    named = client.get(f"/api/teacher/tests/{test_id}/answer-sheet", params={"named": "true"}, headers=as_user(555))
    # To'liq titul emas — bitta sahifada ism va ID ro'yxati
    assert named.status_code == 200 and named.content.startswith(b"%PDF") and named.content.count(b"/Type /Page\n") == 1

    # Eski test (raqamsiz varaq) ham skanerlanadi, ismli varaq esa yo'q
    old = create_test(enrollment_type="closed")
    with SessionLocal() as session:
        session.get(Test, old["id"]).sheet_version = 1
        session.commit()
    old_scan = client.post(f"/api/teacher/tests/{old['id']}/scan", json={"image_base64": _sheet_photo(old["id"], {1: "B"})},
                           headers=as_user(555)).json()
    assert old_scan["answers"] == {"1": "B"} and old_scan["sheet_number"] is None, old_scan
    empty_named = client.get(f"/api/teacher/tests/{old['id']}/answer-sheet", params={"named": "true"}, headers=as_user(555))
    assert empty_named.status_code == 400


def test_excel_hisobot():
    from openpyxl import load_workbook

    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    test_id = created["id"]
    client.post(f"/api/teacher/tests/{test_id}/students", json={"names": ["Ali", "Vali"]}, headers=as_user(555))
    students = client.get(f"/api/teacher/tests/{test_id}", headers=as_user(555)).json()["students"]
    answers = {"Ali": {"1": "B", "2": "C", "3": "A,C", "4": r"2\sqrt{3}"}, "Vali": {"1": "A", "3": "A"}}
    for student in students:
        client.post(f"/api/teacher/tests/{test_id}/students/{student['id']}/answers",
                    json={"answers": answers[student["name"]]}, headers=as_user(555))

    response = client.get(f"/api/teacher/tests/{test_id}/excel", headers=as_user(555))
    assert response.status_code == 200, response.text
    book = load_workbook(io.BytesIO(response.content))
    assert book.sheetnames == ["Natijalar", "Javoblar", "Savollar tahlili"]

    results = list(book["Natijalar"].iter_rows(min_row=2, values_only=True))
    assert [(row[0], row[1], row[2], row[3]) for row in results] == [(1, 1, "Ali", 4), (2, 2, "Vali", 0)], results

    answer_rows = list(book["Javoblar"].iter_rows(min_row=2, values_only=True))
    assert answer_rows[0][1] == "To'g'ri javob" and answer_rows[0][2:5] == ("B", "C", "A,C"), answer_rows[0]
    vali = next(row for row in answer_rows if row[1] == "Vali")
    assert vali[2:5] == ("A", "—", "A"), vali
    vali_row = next(row for row in book["Javoblar"].iter_rows(min_row=3) if row[1].value == "Vali")
    assert vali_row[2].fill.fgColor.rgb.endswith("FFC7CE")  # xato — qizil

    analysis = list(book["Savollar tahlili"].iter_rows(min_row=2, values_only=True))
    first = analysis[0]
    assert first[0] == "1-savol" and first[1] == "B" and first[2] == 1 and first[3] == 50.0, first
    assert first[4] == 1 and first[5] == 1, first  # A ni 1 kishi, B ni 1 kishi tanlagan


def test_variantlar_alohida_kalit_bilan_tekshiriladi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    test_id = created["id"]
    base = f"/api/teacher/tests/{test_id}"
    headers = as_user(555)

    bad = client.put(f"{base}/variants/B", json={"answers": {"1": "C"}}, headers=headers)
    assert bad.status_code == 422, bad.text  # hamma savol uchun javob kerak
    assert client.put(f"{base}/variants/E", json={"answers": {}}, headers=headers).status_code == 422
    key_b = {"1": "C", "2": "A", "3": "b, d", "4": "5"}
    assert client.put(f"{base}/variants/B", json={"answers": key_b}, headers=headers).status_code == 200
    listed = client.get(f"{base}/variants", headers=headers).json()
    assert [v["name"] for v in listed["variants"]] == ["A", "B"] and listed["variants"][1]["answers"]["3"] == "B,D"

    client.post(f"{base}/students", json={"names": ["Birinchi", "Ikkinchi"]}, headers=headers)
    named = client.get(f"{base}/answer-sheet", params={"named": "true"}, headers=headers)
    assert named.status_code == 200
    detail = client.get(base, headers=headers).json()
    assert detail["variants"] == ["A", "B"]
    assert [(s["number"], s["variant"]) for s in detail["students"]] == [(1, "A"), (2, "B")], detail["students"]

    # 2-o'quvchi (B variant) varaqi: B kaliti bo'yicha hammasi to'g'ri
    photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "0", "id3": "2", "variant": "B", 1: "C", 2: "A", 3: "B,D"})
    scanned = client.post(f"{base}/scan", json={"image_base64": photo}, headers=headers).json()
    assert scanned["variant"] == "B" and scanned["match"]["variant"] == "B", scanned
    saved = client.post(f"{base}/scan/save", json={"attempt_id": scanned["match"]["id"], "variant": "B",
                                                  "answers": scanned["answers"]}, headers=headers).json()
    assert saved["correct"] == 3, saved  # A kaliti bilan 0 bo'lardi

    # B kaliti o'zgardi — natija qayta hisoblanadi
    key_b["1"] = "D"
    assert client.put(f"{base}/variants/B", json={"answers": key_b}, headers=headers).json()["regraded"] == 1
    review = client.get(f"{base}/students/{scanned['match']['id']}/detail", headers=headers).json()
    assert review["correct"] == 2 and review["questions"][0]["correct"] == "D", review

    sheet_key = client.get(f"{base}/answer-sheet", params={"answers": "true", "variant": "B"}, headers=headers)
    assert sheet_key.status_code == 200 and sheet_key.content.startswith(b"%PDF")
    assert client.delete(f"{base}/variants/B", headers=headers).status_code == 409  # o'quvchilar bor
    assert client.post(f"{base}/scan/save", json={"name": "Boshqa", "variant": "C", "answers": {}},
                       headers=headers).status_code == 422


def test_offline_testga_excel_dan_oquvchilar():
    from openpyxl import Workbook

    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    base = f"/api/teacher/tests/{created['id']}"
    client.post(f"{base}/students", json={"names": ["Karimova Laylo"]}, headers=as_user(555))

    book = Workbook()
    sheet = book.active
    sheet.append(["№", "F.I.Sh.", "Sinf"])
    sheet.append([1, "Aliyev Vali", "9-A"])
    sheet.append([2, "karimova laylo", "9-A"])
    sheet.append([3, "Oʻrinboyeva Gʻulnora", "9-A"])
    buffer = io.BytesIO()
    book.save(buffer)
    payload = {"file_base64": base64.b64encode(buffer.getvalue()).decode(), "file_name": "royxat.xlsx"}

    read = client.post(f"{base}/students/import", json=payload, headers=as_user(555))
    assert read.status_code == 200, read.text
    assert read.json() == {"names": ["Aliyev Vali", "Oʻrinboyeva Gʻulnora"], "already_in_test": ["karimova laylo"]}, read.json()
    # Faqat o'qiydi — hali qo'shmaydi
    assert len(client.get(base, headers=as_user(555)).json()["students"]) == 1

    bad = client.post(f"{base}/students/import", json={"file_base64": "eA==", "file_name": "eski.xls"}, headers=as_user(555))
    assert bad.status_code == 422, bad.text
    opened = create_test(enrollment_type="open")
    assert client.post(f"/api/teacher/tests/{opened['id']}/students/import", json=payload, headers=as_user(555)).status_code == 400


def test_chiqarilgan_oquvchi_idsi_yangisiga_beriladi():
    reset_database()
    cid = _school_with_class()["id"]  # 701 — maktab admini
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova", "Islom Karimov"]},
                headers=as_user(701))
    students = client.put(f"/api/classes/{cid}/student-codes", json={"start": 1030, "end": 1040},
                          headers=as_user(701)).json()["students"]
    assert [s["code"] for s in students] == [1030, 1031, 1032]

    # Oxirgi (1032) chiqarildi — yangi o'quvchi 1033 emas, 1032 oladi
    client.delete(f"/api/classes/{cid}/students/{students[2]['id']}", headers=as_user(701))
    view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Yangi Bola"}, headers=as_user(701)).json()
    assert [(s["full_name"], s["code"]) for s in view["students"]][-1] == ("Yangi Bola", 1032), view["students"]

    # O'rtadagi (1030) chiqarilsa ham — bo'sh joy keyingi yangisiga
    client.delete(f"/api/classes/{cid}/students/{students[0]['id']}", headers=as_user(701))
    view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Yana Bola"}, headers=as_user(701)).json()
    assert sorted(s["code"] for s in view["students"]) == [1030, 1031, 1032], view["students"]


def test_sinf_id_oraligini_admin_belgilaydi():
    from backend.database import ClassStudent

    reset_database()
    school_class = _school_with_class()  # 10/2, 701 — maktab admini
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova", "Islom Karimov"]},
                headers=as_user(701))
    view = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()
    assert [s["code"] for s in view["students"]] == [None, None, None]  # oraliq yo'q — ID yo'q
    assert view["code_start"] is None

    url = f"/api/classes/{cid}/student-codes"
    assert client.put(url, json={"start": 1001, "end": 1002}, headers=as_user(701)).status_code == 422  # 3 ta o'quvchi
    assert client.put(url, json={"start": 1005, "end": 1001}, headers=as_user(701)).status_code == 422
    ranged = client.put(url, json={"start": 1001, "end": 1038}, headers=as_user(701))
    assert ranged.status_code == 200, ranged.text
    assert [s["code"] for s in ranged.json()["students"]] == [1001, 1002, 1003], ranged.json()["students"]
    assert (ranged.json()["code_start"], ranged.json()["code_end"]) == (1001, 1038)

    # Yangi o'quvchi — oraliqdagi keyingi bo'sh raqam
    after = client.post(f"/api/classes/{cid}/students", json={"full_name": "Yangi Bola"}, headers=as_user(701)).json()
    assert after["students"][-1]["code"] == 1004

    # Boshqa sinf: kesishgan oraliq rad etiladi
    other = client.post("/api/classes", json={"name": "Iqtisod-1"}, headers=as_user(701)).json()
    client.post(f"/api/classes/{other['id']}/students", json={"full_name": "Jasur Umarov"}, headers=as_user(701))
    clash = client.put(f"/api/classes/{other['id']}/student-codes", json={"start": 1030, "end": 1060}, headers=as_user(701))
    assert clash.status_code == 409 and "10/2" in clash.json()["detail"], clash.text
    ok = client.put(f"/api/classes/{other['id']}/student-codes", json={"start": 1101, "end": 1142}, headers=as_user(701))
    assert ok.json()["students"][0]["code"] == 1101

    # Oraliqni o'zgartirish — o'quvchilar yangi oraliqdan qayta raqamlanadi
    moved = client.put(url, json={"start": 2001, "end": 2050}, headers=as_user(701)).json()
    assert [s["code"] for s in moved["students"]] == [2001, 2002, 2003, 2004]

    # Faqat admin/direktor belgilaydi
    _set_name(709, "Oddiy", "Oqituvchi")
    assert client.put(url, json={"start": 3001, "end": 3010}, headers=as_user(709)).status_code in (403, 404)

    # Oraliq belgilanmagan sinfdagi eski avtomatik ID'lar tozalanadi
    third = client.post("/api/classes", json={"name": "Huquq"}, headers=as_user(701)).json()
    with SessionLocal() as session:
        session.add(ClassStudent(class_id=third["id"], full_name="Eski", student_code=1001))
        session.commit()
    main._clear_unassigned_student_codes()
    assert client.get(f"/api/classes/{third['id']}", headers=as_user(701)).json()["students"][0]["code"] is None


def test_skaner_natijasi_sinf_natijalariga_yoziladi():
    reset_database()
    school_class = _school_with_class(701)  # 10/2
    register_teacher(701, "Aziza", "Karimova")
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova"]}, headers=as_user(701))
    other = client.post("/api/classes", json={"name": "11-A"}, headers=as_user(701)).json()
    client.post(f"/api/classes/{other['id']}/students", json={"full_name": "Jasur Umarov"}, headers=as_user(701))
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1001, "end": 1038}, headers=as_user(701))
    client.put(f"/api/classes/{other['id']}/student-codes", json={"start": 1101, "end": 1110}, headers=as_user(701))  # Jasur 1101

    test = create_test(701, enrollment_type="closed")
    base = f"/api/teacher/tests/{test['id']}"
    listed = client.get(f"{base}/classes", headers=as_user(701)).json()
    assert listed["available"] and [(c["name"], c["students"], c["attached"]) for c in listed["classes"]] == [
        ("10/2", 2, False), ("11-A", 1, False)], listed
    attached = client.post(f"{base}/classes", json={"class_ids": [cid]}, headers=as_user(701))
    assert attached.json() == {"added": 2}, attached.text
    assert client.post(f"{base}/classes", json={"class_ids": [cid]}, headers=as_user(701)).json() == {"added": 0}
    detail = client.get(base, headers=as_user(701)).json()
    assert [(s["number"], s["name"]) for s in detail["students"]] == [(1001, "Ali Valiyev"), (1002, "Zuhra Karimova")]
    assert detail["sheet_version"] == 5

    # Zuhra (1002) varag'i: 1-savol to'g'ri, 2-savol to'g'ri
    scanned = client.post(f"{base}/scan", json={"image_base64": _sheet_photo(test["id"], {
        "id0": "1", "id1": "0", "id2": "0", "id3": "2", 1: "B", 2: "C"})}, headers=as_user(701)).json()
    assert scanned["sheet_number"] == 1002 and scanned["match"]["name"] == "Zuhra Karimova", scanned
    client.post(f"{base}/scan/save", json={"attempt_id": scanned["match"]["id"], "answers": scanned["answers"]},
                headers=as_user(701))

    class_tests = client.get(f"/api/classes/{cid}/tests", headers=as_user(701)).json()
    assert len(class_tests) == 1 and class_tests[0]["title"] == "Sinov test" and class_tests[0]["max_score"] == 4, class_tests
    scores = {row["name"]: row["score"] for row in client.get(f"/api/class-tests/{class_tests[0]['id']}",
                                                               headers=as_user(701)).json()["scores"]}
    assert scores == {"Zuhra Karimova": 2, "Ali Valiyev": None}, scores

    # Biriktirilmagan sinf o'quvchisi (Jasur, 1101) ham ID bo'yicha topiladi va o'z sinfiga yoziladi
    jasur = client.post(f"{base}/scan", json={"image_base64": _sheet_photo(test["id"], {
        "id0": "1", "id1": "1", "id2": "0", "id3": "1", 1: "B"})}, headers=as_user(701)).json()
    assert jasur["match"]["name"] == "Jasur Umarov", jasur
    client.post(f"{base}/scan/save", json={"attempt_id": jasur["match"]["id"], "answers": jasur["answers"]},
                headers=as_user(701))
    other_tests = client.get(f"/api/classes/{other['id']}/tests", headers=as_user(701)).json()
    assert len(other_tests) == 1
    assert client.get(f"/api/class-tests/{other_tests[0]['id']}", headers=as_user(701)).json()["scores"][0]["score"] == 1

    # Qayta skanerlab tuzatilsa — sinfdagi ball ham yangilanadi (yangi test yaratilmaydi)
    client.post(f"{base}/scan/save", json={"attempt_id": scanned["match"]["id"], "answers": {"1": "B"}}, headers=as_user(701))
    class_tests = client.get(f"/api/classes/{cid}/tests", headers=as_user(701)).json()
    assert len(class_tests) == 1
    scores = {row["name"]: row["score"] for row in client.get(f"/api/class-tests/{class_tests[0]['id']}",
                                                               headers=as_user(701)).json()["scores"]}
    assert scores["Zuhra Karimova"] == 1, scores

    named = client.get(f"{base}/answer-sheet", params={"named": "true"}, headers=as_user(701))
    assert named.status_code == 200 and named.content.startswith(b"%PDF")


def test_3_xonali_eski_titul_yangi_testda_ham_oqiladi():
    from backend.database import Test

    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    test_id = created["id"]
    client.post(f"/api/teacher/tests/{test_id}/students", json={"names": ["Birinchi", "Ikkinchi"]}, headers=as_user(555))
    with SessionLocal() as session:
        session.get(Test, test_id).sheet_version = 4
        session.commit()
    old_photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "2", 1: "B"})  # 3 xonali titul chop etilgan

    main._upgrade_sheet_versions()
    with SessionLocal() as session:
        assert session.get(Test, test_id).sheet_version == 5

    scanned = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": old_photo}, headers=as_user(555)).json()
    assert scanned["sheet_number"] == 2 and scanned["match"]["name"] == "Ikkinchi", scanned
    assert scanned["answers"] == {"1": "B"}
