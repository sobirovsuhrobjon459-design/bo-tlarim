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
from datetime import datetime, timedelta
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


class _TelegramUnauthorized:
    """Soxta token bilan Telegram nima qaytarsa — shu (401), lekin tarmoqqa chiqmasdan."""

    status_code = 401
    text = '{"ok":false,"error_code":401,"description":"Unauthorized"}'

    def json(self) -> dict:
        return {"ok": False, "error_code": 401, "description": "Unauthorized"}


def _without_telegram(request):
    # Testlar Telegram'ga haqiqiy so'rov yubormaydi: tarmoq sekin bo'lsa, fondagi bot xabarlari
    # navbati (har xabar 5 × 15 s gacha urinadi) boshqa testlarni kutdirib, ularni yiqitardi
    def call(url, *args, **kwargs):
        if "api.telegram.org" in str(url):
            return _TelegramUnauthorized()
        return request(url, *args, **kwargs)

    return call


main.httpx.post = _without_telegram(main.httpx.post)
main.httpx.get = _without_telegram(main.httpx.get)


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


def test_ikki_admin_id():
    """ADMIN_ID=4242,5151 — ikkala admin ham panelga kiradi, begona kirmaydi."""
    import backend.database as database

    reset_database()
    original = database.ADMIN_IDS
    database.ADMIN_IDS = (4242, 5151)
    try:
        for tg_id in (4242, 5151):
            assert client.get("/api/admin/stats", headers=as_user(tg_id)).status_code == 200, tg_id
            assert client.get("/api/me", headers=as_user(tg_id)).json()["is_admin"] is True
        assert client.get("/api/admin/stats", headers=as_user(1111)).status_code == 403
        assert client.get("/api/me", headers=as_user(1111)).json()["is_admin"] is False
    finally:
        database.ADMIN_IDS = original


def test_admin_odamlar_muassasalar_va_kredit():
    """Admin odamni topadi, kartochkasini ko'radi, kredit beradi/ayiradi, xabar yozadi; muassasalar ro'yxati."""
    reset_database()
    register_teacher(555, "Ali", "Valiyev")
    client.get("/api/me", headers=as_user(901))  # oddiy foydalanuvchi
    admin = as_user(4242)

    stats = client.get("/api/admin/stats", headers=admin).json()
    assert stats["periods"]["new_users"]["today"] >= 2 and stats["pending"] == {"card_topups": 0, "withdrawals": 0}

    # Begona odam yangi bo'limlarga kira olmaydi
    for url in ("/api/admin/users", "/api/admin/schools", "/api/admin/payments"):
        assert client.get(url, headers=as_user(555)).status_code == 403, url

    found = client.get("/api/admin/users?q=valiy", headers=admin).json()
    assert [user["name"] for user in found] == ["Ali Valiyev"] and found[0]["is_teacher"], found
    teacher_user_id = found[0]["id"]
    assert client.get("/api/admin/users?q=555", headers=admin).json()[0]["id"] == teacher_user_id  # Telegram ID bo'yicha
    only_teachers = client.get("/api/admin/users?only=teachers", headers=admin).json()
    assert [user["id"] for user in only_teachers] == [teacher_user_id]

    before = client.get(f"/api/admin/users/{teacher_user_id}", headers=admin).json()["credits"]
    sent = []
    original_send, original_status = main._telegram_send_message, main._telegram_send_status
    main._telegram_send_message = lambda chat_id, text, markup=None, parse_mode=None: sent.append((chat_id, text)) or True
    main._telegram_send_status = lambda chat_id, text, markup=None, parse_mode=None: sent.append((chat_id, text)) or (200, "")
    try:
        url = f"/api/admin/users/{teacher_user_id}/credits"
        response = client.post(url, json={"delta": 1000, "reason": "Sovg'a"}, headers=admin)
        assert response.status_code == 200 and response.json()["credits"] == before + 1000, response.text
        assert sent and sent[-1][0] == 555 and "1 000 token" in sent[-1][1] and "Sovg'a" in sent[-1][1], sent
        # Ayirishda noldan pastga tushmaydi
        assert client.post(url, json={"delta": -10**6, "notify": False}, headers=admin).json()["credits"] == 0
        assert client.post(url, json={"delta": 0}, headers=admin).status_code == 422
        # Faqat admin; o'qituvchi bo'lmagan odamga kredit berilmaydi
        assert client.post(url, json={"delta": 5}, headers=as_user(555)).status_code == 403
        plain_id = client.get("/api/admin/users?q=901", headers=admin).json()[0]["id"]
        assert client.post(f"/api/admin/users/{plain_id}/credits", json={"delta": 5}, headers=admin).status_code == 404

        assert client.post(f"/api/admin/users/{plain_id}/message", json={"text": "Salom"}, headers=admin).status_code == 200
        assert sent[-1][0] == 901 and "Salom" in sent[-1][1]
    finally:
        main._telegram_send_message, main._telegram_send_status = original_send, original_status

    # Qo'lda berilgan kredit daromadga qo'shilmaydi
    assert client.get("/api/admin/stats", headers=admin).json()["credits_sold"] == 0

    # Muassasalar ro'yxati
    school = client.post("/api/schools", json={"name": "Ilm Maktabi", "region": "Toshkent shahri",
                                               "district": "Chilonzor tumani"}, headers=as_user(555))
    assert school.status_code == 200, school.text
    schools = client.get("/api/admin/schools", headers=admin).json()
    assert len(schools) == 1 and schools[0]["name"] == "Ilm Maktabi" and schools[0]["staff"] >= 1, schools
    assert client.get("/api/admin/schools?q=Chilonzor", headers=admin).json()[0]["owner_user_id"] == teacher_user_id
    assert client.get("/api/admin/schools?q=Samarqand", headers=admin).json() == []
    detail = client.get(f"/api/admin/users/{teacher_user_id}", headers=admin).json()
    assert detail["schools"][0]["role"] == "owner", detail
    assert client.get("/api/admin/payments", headers=admin).json() == []


def test_admin_elon_guruhga_va_tasdiq_soni_bilan():
    """Admin e'loni: faqat admin; guruh soni mos kelmasa ketmaydi; o'qituvchilar guruhiga faqat o'qituvchilar."""
    reset_database()
    register_teacher(555, "Ali", "Valiyev")
    register_teacher(556, "Vali", "Aliyev")
    client.get("/api/me", headers=as_user(901))
    admin = as_user(4242)

    assert client.get("/api/admin/broadcast", headers=as_user(555)).status_code == 403
    counts = {item["id"]: item["count"] for item in client.get("/api/admin/broadcast", headers=admin).json()["audiences"]}
    assert counts["teachers"] == 2 and counts["all"] >= 3 and counts["school_staff"] == 0, counts

    started, sent = [], []
    original_start, original_status = main._start_admin_broadcast, main._telegram_send_status
    main._start_admin_broadcast = lambda admin_tg, chat_ids, text, title: started.append((chat_ids, text))
    main._telegram_send_status = lambda chat_id, text, markup=None, parse_mode=None: sent.append((chat_id, text)) or (200, "")
    try:
        body = {"text": "Salom ustozlar", "audience": "teachers"}
        # Ekrandagi son eskirgan bo'lsa — yuborilmaydi
        assert client.post("/api/admin/broadcast", json={**body, "confirm_count": 5}, headers=admin).status_code == 409
        assert client.post("/api/admin/broadcast", json={**body, "audience": "hech", "confirm_count": 0},
                           headers=admin).status_code == 422
        assert client.post("/api/admin/broadcast", json={**body, "confirm_count": 2}, headers=as_user(555)).status_code == 403
        assert started == []

        response = client.post("/api/admin/broadcast", json={**body, "confirm_count": 2}, headers=admin)
        assert response.status_code == 200 and response.json()["recipients"] == 2, response.text
        assert sorted(started[0][0]) == [555, 556] and started[0][1] == "Salom ustozlar"
        # Birinchisi tugamaguncha ikkinchisi ketmaydi
        assert client.post("/api/admin/broadcast", json={**body, "confirm_count": 2}, headers=admin).status_code == 409

        # Haqiqiy yuborish: hammasiga yetadi, oxirida qulf ochiladi
        main._run_admin_broadcast(4242, [555, 556], "Salom", "O'qituvchilar")
        assert [chat for chat, _ in sent] == [555, 556, 4242], sent  # oxirida adminga hisobot
        assert "2 / 2" in sent[-1][1]
        assert main._admin_broadcast_running is False

        assert client.post("/api/admin/broadcast/test", json={"text": "Sinov"}, headers=admin).status_code == 200
        assert sent[-1] == (4242, "Sinov")
    finally:
        main._start_admin_broadcast, main._telegram_send_status = original_start, original_status
        main._admin_broadcast_running = False


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
    assert all("teachers" not in school and "students" not in school for school in listed), "son ko'rinib qoldi"

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
    assert "@aziza_teacher" in by_chat[702] and "Xususiy maktab" in by_chat[702]


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


def test_xodim_tugilgan_sana_saqlanadi_pasport_soralmaydi():
    """Tug'ilgan sana saqlanadi, faqat rahbariyat ko'radi, yuborilmagani o'chmaydi. Pasport so'ralmaydi va ko'rsatilmaydi."""
    reset_database()
    _set_name(701, "Maktab", "Admini")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Sana maktabi", "phone": "901112233"}, headers=as_user(701)).json()["school"]
    _set_name(702, "Ustoz", "Birinchi")
    _set_name(703, "Ustoz", "Ikkinchi")
    # Eski ilova pasport yuborsa ham (hatto noto'g'ri bo'lsa) — xato emas, saqlanmaydi
    joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"],
                                                    "birth_date": "1990-05-17", "passport": "12345"}, headers=as_user(702))
    assert joined.status_code == 200, joined.text
    me = joined.json()["school"]
    assert me["my_birth_date"] == "1990-05-17" and "my_passport" not in me, me
    from backend.database import SchoolMember

    with SessionLocal() as session:
        member = session.scalar(main.select(SchoolMember).where(SchoolMember.phone == "+998901234567"))
        assert member.passport is None
    client.post("/api/schools/join", json={"phone": "901234568", "code": current_code(school), "subjects": ["Fizika"]}, headers=as_user(703))

    # Oddiy o'qituvchi boshqaning sanasini ko'rmaydi; admin ko'radi; pasport hech kimga ko'rinmaydi
    others = {m["name"]: m for m in client.get("/api/schools/me", headers=as_user(703)).json()["school"]["members"]}
    assert others["Ustoz Birinchi"]["birth_date"] is None and "passport" not in others["Ustoz Birinchi"]
    seen = {m["name"]: m for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    assert seen["Ustoz Birinchi"]["birth_date"] == "1990-05-17" and "passport" not in seen["Ustoz Birinchi"]

    # Profilni faqat fan bilan saqlash sanani o'chirmaydi
    kept = client.put("/api/schools/me/profile", json={"subjects": ["Fizika"]}, headers=as_user(702)).json()["school"]
    assert kept["my_birth_date"] == "1990-05-17", kept
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
    # Excel'dan ommaviy yuklash zam direktorga emas — admin, direktor va kuratorga
    assert client.post(f"/api/classes/{created['id']}/students/import/preview", json={"text": "Ali Valiyev"},
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

    # Oddiy o'qituvchi ro'yxatni Excel'dan yuklay olmaydi (bittalab qo'shish — alohida testda)
    assert client.post(f"/api/classes/{cid}/students/import/preview", json={"text": "Ali Valiyev"},
                       headers=as_user(702)).status_code == 403
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

    # Kurator o'quvchi qo'shadi va Excel'dan yuklay oladi; boshqa o'qituvchi yuklay olmaydi
    detail = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(702)).json()
    assert detail["can_manage"] and detail["is_curator"]
    assert client.post(f"/api/classes/{cid}/students/import/preview", json={"text": "Begona"},
                       headers=as_user(703)).status_code == 403
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
    client.request("DELETE", f"/api/classes/{cid}/students/{ids['Sardor Raximov']}", json={"reason": "Boshqa maktabga o'tdi"}, headers=as_user(701))
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


def test_natija_rasmlari_albom_bolib_yuboriladi():
    """Har sahifa — alohida rasm: 11 sahifa → 10 talik albom + bitta rasm; izoh birinchisida."""
    import json as _json

    calls = []

    class Reply:
        status_code = 200

    original = main.httpx.post
    main.httpx.post = lambda url, data=None, files=None, timeout=None: calls.append((url.rsplit("/", 1)[-1], data, files)) or Reply()
    try:
        main._send_photo_album(1, "T", [b"png"] * 11, "Natijalar")
        main._send_photo_album(1, "T", [b"png"], "Bitta")
    finally:
        main.httpx.post = original
    assert [name for name, _, _ in calls] == ["sendMediaGroup", "sendPhoto", "sendPhoto"], calls
    media = _json.loads(calls[0][1]["media"])
    assert len(media) == 10 and len(calls[0][2]) == 10 and media[0]["caption"] == "Natijalar" and "caption" not in media[1]
    assert "caption" not in calls[1][1] and calls[2][1]["caption"] == "Bitta"


def test_boshlangich_test_rasch_siz_hisoblanadi():
    """Boshlang'ich test: ball yo'q — to'g'ri javoblar, foiz va 5 ballik baho; turni keyin o'zgartirsa — qayta hisoblanadi."""
    reset_database()
    register_teacher()
    key = [{"number": n, "question_type": "single", "option_count": 4, "correct": "A"} for n in range(1, 11)]
    created = create_test(key_questions=key, test_type="boshlangich")
    for index, solved in enumerate((9, 7, 4)):
        started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(7600 + index)).json()
        answers = {str(n): ("A" if n <= solved else "B") for n in range(1, 11)}
        client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": answers}, headers=as_user(7600 + index))

    url = f"/api/teacher/tests/{created['id']}"
    assert client.get(url, headers=as_user(555)).json()["test_type"] == "boshlangich"
    results = client.get(f"{url}/results", headers=as_user(555)).json()
    assert results["uses_rasch"] is False
    # Daraja ham, baho ham yo'q — faqat to'g'ri javoblar soni va foiz
    assert [(row["correct"], row["percent"], row["ball"], row["grade"]) for row in results["rows"]] ==         [(9, 90.0, None, None), (7, 70.0, None, None), (4, 40.0, None, None)]
    excel = client.get(f"{url}/excel", headers=as_user(555))
    assert excel.status_code == 200
    pdf = client.get(f"{url}/pdf", headers=as_user(555))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    certificates = client.get(f"{url}/certificates", headers=as_user(555))
    assert certificates.status_code == 200 and certificates.content.startswith(b"%PDF")

    # O'qituvchi hisoblash usulini o'zgartiradi — natijalar Rasch bo'yicha
    assert client.patch(url, json={"test_type": "rash"}, headers=as_user(555)).status_code == 200
    results = client.get(f"{url}/results", headers=as_user(555)).json()
    assert results["uses_rasch"] is True and all(row["ball"] is not None for row in results["rows"])
    assert client.patch(url, json={"test_type": "boshqa"}, headers=as_user(555)).status_code == 422


def test_admin_muassasani_ochiradi():
    """Platforma admini muassasani o'chiradi: nomi aynan yozilsa; ro'yxat, kod, havola va a'zolikdan yo'qoladi."""
    reset_database()
    cid = _school_with_class(701)["id"]
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(701))
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    _set_name(702, "Ustoz", "Ikkinchi")
    client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 02"},
                headers=as_user(702))
    invite = client.post("/api/schools/invites", json={}, headers=as_user(701)).json()
    admin = as_user(4242)
    _set_name(4242, "Bosh", "Admin")
    listed = client.get("/api/admin/schools", headers=admin).json()
    assert [item["id"] for item in listed] == [school["id"]]

    url = f"/api/admin/schools/{school['id']}/delete"
    assert client.post(url, json={"confirm": school["name"]}, headers=as_user(702)).status_code == 403  # admin emas
    assert client.post(url, json={"confirm": "boshqa nom"}, headers=admin).status_code == 422
    deleted = client.post(url, json={"confirm": "  " + school["name"].upper() + " "}, headers=admin)
    assert deleted.status_code == 200 and deleted.json()["members_removed"] == 2, deleted.text

    # Hamma joydan yo'qoladi
    assert client.get("/api/admin/schools", headers=admin).json() == []
    assert client.get("/api/schools/me", headers=as_user(701)).json()["school"] is None
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is None
    assert client.get(f"/api/classes/{cid}", headers=as_user(701)).status_code in (403, 404)
    _set_name(703, "Yangi", "Ustoz")
    assert client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 03"},
                       headers=as_user(703)).status_code == 404
    token = invite.get("token") or invite.get("link", "").rsplit("_", 1)[-1]
    if token:
        assert client.get(f"/api/schools/invites/{token}", headers=as_user(703)).status_code == 404
    assert client.post(url, json={"confirm": school["name"]}, headers=admin).status_code == 404  # ikkinchi marta
    # Ma'lumotlar bazada qoladi
    with SessionLocal() as session:
        from backend.database import PrivateSchool
        assert session.get(PrivateSchool, school["id"]).deleted_at is not None


def test_sinflar_raqam_tartibida():
    """Sinflar 1 dan 11 gacha tartibda: 2-A "10-1" dan oldin (oddiy saralashda keyin turardi)."""
    reset_database()
    _school_with_class(701)  # "10/2"
    for name in ("10-1 (Qizlar)", "2-A", "11-A", "1-A", "10-1 (Yigitlar)", "Iqtisod-1", "9-B"):
        assert client.post("/api/classes", json={"name": name}, headers=as_user(701)).status_code == 200
    names = [item["name"] for item in client.get("/api/classes", headers=as_user(701)).json()]
    assert names == ["1-A", "2-A", "9-B", "10-1 (Qizlar)", "10-1 (Yigitlar)", "10/2", "11-A", "Iqtisod-1"], names


def test_sinf_reytingi_test_va_sinov_natijalaridan():
    """
    O'quvchilar ro'yxatidagi reyting: sinfning barcha testlari (hamma fanlar) bo'yicha o'rtacha foiz,
    qatnashmagani hisobga olinmaydi, teng natija — teng o'rin. Hamma bir xil reytingni ko'radi.
    """
    reset_database()
    cid = _school_with_class()["id"]
    for name in ("Ali Valiyev", "Vali Aliyev", "Hasan Husanov", "Olim Karimov"):
        detail = client.post(f"/api/classes/{cid}/students", json={"full_name": name}, headers=as_user(701)).json()
    ids = {s["full_name"]: s["id"] for s in detail["students"]}
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    for tg_id in (702, 703):
        _set_name(tg_id, "Ustoz", f"N{tg_id}")
        client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": ["Fizika"]},
                    headers=as_user(tg_id))

    def rating(tg_id):
        view = client.get(f"/api/classes/{cid}", headers=as_user(tg_id))
        assert view.status_code == 200, view.text
        data = view.json()
        return data["rating_tests"], {s["full_name"]: s["rating"] for s in data["students"]}

    assert rating(701) == (0, {name: None for name in ids})

    # 702 (fan o'qituvchisi): Ali 90%, Vali 60%, Hasan 90%, Olim — kelmagan
    assert client.post(f"/api/classes/{cid}/tests", headers=as_user(702), json={
        "title": "Nazorat", "subject": "Fizika", "taken_on": "2026-10-01", "max_score": 30,
        "scores": {ids["Ali Valiyev"]: 27, ids["Vali Aliyev"]: 18, ids["Hasan Husanov"]: 27}}).status_code == 200
    # 701 (rahbariyat): Ali 50%, Vali 100%
    assert client.post(f"/api/classes/{cid}/tests", headers=as_user(701), json={
        "title": "Sinov", "subject": "Matematika", "taken_on": "2026-10-02", "max_score": 10,
        "scores": {ids["Ali Valiyev"]: 5, ids["Vali Aliyev"]: 10}}).status_code == 200

    # Rahbariyat — ikkala test: Hasan 90 (1 test), Vali 80, Ali 70
    tests, rows = rating(701)
    assert tests == 2
    assert rows["Hasan Husanov"] == {"rank": 1, "avg": 90.0, "tests": 1}
    assert rows["Vali Aliyev"] == {"rank": 2, "avg": 80.0, "tests": 2}
    assert rows["Ali Valiyev"] == {"rank": 3, "avg": 70.0, "tests": 2}
    assert rows["Olim Karimov"] is None

    # Reyting hamma uchun bir xil — fan o'qituvchisi ham, testi yo'q o'qituvchi ham
    assert rating(702) == (tests, rows)
    assert rating(703) == (tests, rows)

    # Teng natija — teng o'rin: Hasan ikkinchi testda 70% → o'rtachasi 80, Vali bilan teng
    assert client.post(f"/api/classes/{cid}/tests", headers=as_user(702), json={
        "title": "Qo'shimcha", "subject": "Fizika", "taken_on": "2026-10-03", "max_score": 10,
        "scores": {ids["Hasan Husanov"]: 7}}).status_code == 200
    tests, rows = rating(703)
    assert tests == 3
    # Teng natijadan keyin o'rin tashlab ketilmaydi: 1, 1, 2
    assert rows["Hasan Husanov"]["rank"] == rows["Vali Aliyev"]["rank"] == 1 and rows["Ali Valiyev"]["rank"] == 2

    # Profildagi natijalar hammaga ochiq — testi yo'q o'qituvchi ham ikkala testni ko'radi
    profile = client.get(f"/api/classes/{cid}/students/{ids['Ali Valiyev']}", headers=as_user(703)).json()
    assert sorted(item["title"] for item in profile["results"]) == ["Nazorat", "Sinov"], profile["results"]

    # Test markazidan o'chirilgan imtihon reytingga ham, profilga ham kirmaydi
    from backend.database import ClassTest, ClassTestScore

    center = create_test(tg_id=702)
    with SessionLocal() as session:
        teacher_user_id = session.scalar(main.select(main.User.id).where(main.User.tg_id == 702))
        scanned = ClassTest(class_id=cid, teacher_user_id=teacher_user_id, title="Skanerlangan", subject="Fizika", taken_on=main._dorm_today(), max_score=10,
                            source_test_id=center["id"])
        session.add(scanned)
        session.flush()
        session.add(ClassTestScore(test_id=scanned.id, student_id=ids["Ali Valiyev"], student_name="Ali Valiyev", score=10))
        session.commit()
    assert rating(703)[0] == 4 and rating(703)[1]["Ali Valiyev"]["rank"] == 1  # (90+50+100)/3 = 80 — Vali va Hasan bilan teng
    assert client.delete(f"/api/teacher/tests/{center['id']}", headers=as_user(702)).status_code == 200
    assert rating(703) == (tests, rows)
    profile = client.get(f"/api/classes/{cid}/students/{ids['Ali Valiyev']}", headers=as_user(703)).json()
    assert "Skanerlangan" not in [item["title"] for item in profile["results"]]


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
    # Boshlang'ich test rejimi: ball/maks va foiz, darajasiz (A+ … NC yo'q); diagramma rangi — foizdan
    assert rows[0]["percent"] == 90 and rows[0]["level"] == "good" and rows[0]["rank"] == 1 and rows[1]["rank"] == 1
    assert rows[2]["percent"] == 60 and rows[2]["level"] == "mid" and rows[2]["rank"] == 2  # 1, 1, 2 — o'rin tashlanmaydi
    assert "grade" not in rows[0] and test["max_score"] == 30
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
    # Rasm — PDF sahifasining o'zi (A4 nisbatida), sahifalar soni aytiladi
    from PIL import Image as _Image
    width, height = _Image.open(io.BytesIO(png.content)).size
    assert abs(height / width - 842 / 595) < 0.02 and png.headers["X-Page-Count"] == "1", (width, height)
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


def test_moliyachi_va_komendant_lavozimlari():
    """
    Moliyachi — barcha sinflar to'lovi, narxi va moliya hisoboti (rahbariyat huquqlarisiz).
    Komendant — yotoqxonani boshqaradi. Ikkalasi ham sinf ocha olmaydi va lavozim tayinlay olmaydi.
    """
    from backend.database import User

    reset_database()
    cid = _school_with_class(701)["id"]
    sid = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali Valiyev"}, headers=as_user(701)).json()["students"][0]["id"]
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    for tg_id, first in ((705, "Moliya"), (706, "Komendant")):
        _set_name(tg_id, first, "Xodim")
        assert client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"],
                                                      "phone": f"93 444 55 {tg_id - 700:02d}"}, headers=as_user(tg_id)).status_code == 200
    with SessionLocal() as session:
        uid = {tg: session.scalar(main.select(User.id).where(User.tg_id == tg)) for tg in (705, 706)}

    # Lavozimni faqat admin tayinlaydi
    assert client.put(f"/api/schools/members/{uid[705]}/position", json={"position": "accountant"}, headers=as_user(706)).status_code == 403
    for tg, position in ((705, "accountant"), (706, "commandant")):
        assigned = client.put(f"/api/schools/members/{uid[tg]}/position", json={"position": position}, headers=as_user(701))
        assert assigned.status_code == 200, assigned.text
    members = {m["name"]: m for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    assert members["Moliya Xodim"]["position"] == "accountant" and members["Komendant Xodim"]["position"] == "commandant"

    # Moliyachi: narx belgilaydi, to'lov qabul qiladi, hisobotni ko'radi; rahbariyat huquqlari yo'q
    finance = client.get("/api/schools/me", headers=as_user(705)).json()["school"]
    assert finance["can_see_all_payments"] is True and finance["can_set_fee"] is True and finance["is_leader"] is False
    assert finance["dorm"] is False
    assert client.put("/api/schools/fee", json={"monthly_fee": 500_000}, headers=as_user(705)).status_code == 200
    paid = client.post(f"/api/classes/{cid}/students/{sid}/payments", json={"month": main._current_month(), "amount": 200_000},
                       headers=as_user(705))
    assert paid.status_code == 200, paid.text
    assert client.get("/api/schools/payments/summary", headers=as_user(705)).json()["totals"]["paid"] == 200_000
    assert client.get(f"/api/classes/{cid}/students/{sid}", headers=as_user(705)).json()["payments"] is not None
    assert client.post("/api/classes", json={"name": "5-B"}, headers=as_user(705)).status_code == 403
    assert client.get("/api/dorm", headers=as_user(705)).status_code == 403

    # Komendant: yotoqxona — xona ochadi, o'quvchini joylashtiradi; to'lovlarni ko'rmaydi
    dorm_school = client.get("/api/schools/me", headers=as_user(706)).json()["school"]
    assert dorm_school["dorm"] is True and dorm_school["can_see_all_payments"] is False and dorm_school["is_leader"] is False
    room = client.post("/api/dorm/rooms", json={"name": "101", "capacity": 4}, headers=as_user(706))
    assert room.status_code == 200, room.text
    assert client.get("/api/dorm", headers=as_user(706)).json()["can_manage"] is True
    assert any(item["id"] == sid for item in client.get("/api/dorm/candidates", headers=as_user(706)).json())
    assert client.get(f"/api/classes/{cid}/payments", headers=as_user(706)).status_code == 403
    assert client.get("/api/schools/payments/summary", headers=as_user(706)).status_code == 403
    assert client.post("/api/classes", json={"name": "5-B"}, headers=as_user(706)).status_code == 403

    # Noma'lum lavozim rad etiladi
    assert client.put(f"/api/schools/members/{uid[705]}/position", json={"position": "boss"}, headers=as_user(701)).status_code == 422


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
    # Ota-onalar guruhiga — maktab shabloni: salom, sana va vaqt, guruh, dars, o'qituvchi, davomat soni
    assert text.startswith("Assalomu alaykum, hurmatli ota-onalar.\n\n📆 Sana: "), text
    assert " · 11:00–11:45\n📋 Guruh: 10/2\n📚 Dars: Ingliz tili\n👤 Aziza Karimova\n\n" in text, text
    assert "Davomat: ✅ Bor: 0 · ❌ Yo'q: 1 · 🟡 Sababli: 0 · ⏰ Kechikdi: 0" in text, text

    # Tahrirda dars vaqti o'zgaradi
    edited = client.put(f"/api/attendance/{attendance['id']}", json={"lesson_start": "12:00", "lesson_end": "12:45"},
                        headers=as_user(701)).json()
    assert (edited["lesson_start"], edited["lesson_end"]) == ("12:00", "12:45") and edited["updated_at"], edited
    assert "❌ Yo'q:\n1. Фаррух Комилжонов" in text, text

    pdf = client.get(f"/api/attendance/{attendance['id']}/file", params={"format": "pdf"}, headers=as_user(701))
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    image = client.get(f"/api/attendance/{attendance['id']}/file", params={"format": "image"}, headers=as_user(701))
    assert image.status_code == 200 and image.content.startswith(b"\x89PNG")
    # Rasm — PDF sahifasining o'zi: o'lchami A4 nisbatida, sahifalar soni aytiladi
    from PIL import Image as _Image
    width, height = _Image.open(io.BytesIO(image.content)).size
    assert abs(height / width - 842 / 595) < 0.02 and image.headers["X-Page-Count"] == "1", (width, height)

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
    # Yozma javobni qo'lda kiritishda fanga mos klaviatura (Matematika — matematik)
    assert answer_detail["keyboard"] == "math", answer_detail.get("keyboard")

    # 6) Ikkinchi o'quvchi ham kiritilgach, test yakunlanadi
    client.post(
        f"/api/teacher/tests/{created['id']}/students/{students[1]['id']}/answers",
        json={"answers": {"1": "B"}},
        headers=as_user(555),
    )
    closed = client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555))
    assert closed.status_code == 200, closed.text


def test_javobi_kiritilmagan_oquvchi_bilan_baribir_yakunlash():
    """O'qituvchi "Baribir yakunlaysizmi?"ni tasdiqlasa — kiritilmaganlar bo'lsa ham test yakunlanadi."""
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    client.post(f"/api/teacher/tests/{created['id']}/students", json={"names": ["Ali Valiyev", "Vali Aliyev"]},
                headers=as_user(555))
    students = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["students"]
    client.post(f"/api/teacher/tests/{created['id']}/students/{students[0]['id']}/answers",
                json={"answers": {"1": "B"}}, headers=as_user(555))

    # Tasdiqsiz — avvalgidek to'xtatiladi
    assert client.post(f"/api/teacher/tests/{created['id']}/close", headers=as_user(555)).status_code == 400
    # "Baribir yakunlash" — yakunlanadi, kiritilmagan o'quvchi natijalarga kirmaydi
    closed = client.post(f"/api/teacher/tests/{created['id']}/close?force=true", headers=as_user(555))
    assert closed.status_code == 200, closed.text
    detail = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()
    assert detail["status"] == "closed", detail["status"]
    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert len(results["rows"]) == 1, results["rows"]


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
    from backend.subjects import part_count, subject_config

    config = subject_config(subject)
    key = [{"number": n, "question_type": "single", "option_count": config["options"].get(n, 4), "correct": "A"}
           for n in range(1, config["tests"] + 1)]
    for n in range(config["tests"] + 1, config["tests"] + config["written"] + 1):
        bands = part_count(config, n)
        if bands > 1:
            start = len(key) + 1
            key += [{"number": start + band, "label": f"{n}{chr(97 + band)}", "question_type": "text", "correct": str(band + 1)}
                    for band in range(bands)]
        else:
            key.append({"number": len(key) + 1, "label": str(n), "question_type": "text", "correct": "1"})
    return key


def test_ona_tili_milliy_sertifikat_tuzilishi():
    """Ona tili: 1–32 — A–D, 33–35 — A–F, 36–39 — bitta yozma javob, 40–44 — a va b."""
    from backend.subjects import SUBJECTS, frontend_config

    key = _subject_key("Ona tili va adabiyot")
    options = {item["label"] if "label" in item else str(item["number"]): item.get("option_count") for item in key}
    assert all(options[str(n)] == 4 for n in range(1, 33))
    assert all(options[str(n)] == 6 for n in (33, 34, 35))
    written = [item["label"] for item in key if item["question_type"] == "text"]
    assert written == ["36", "37", "38", "39"] + [f"{n}{part}" for n in range(40, 45) for part in "ab"]
    assert frontend_config()["Ona tili va adabiyot"]["fixed_parts"] == {"36": 1, "37": 1, "38": 1, "39": 1}
    assert "fixed_parts" not in frontend_config()["Matematika"] or not frontend_config()["Matematika"]["fixed_parts"]
    assert SUBJECTS["Ona tili va adabiyot"]["written"] == 9

    reset_database()
    register_teacher()
    created = create_test(subject="Ona tili va adabiyot", key_questions=key, duration=180)
    started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(7300))
    assert started.status_code == 200, started.text
    assert len(started.json()["questions"]) == 44


def test_ona_tili_essesi_oqituvchi_baholaydi_va_ballga_qoshiladi():
    """
    Esse (45): o'quvchi ilovada yozadi, o'qituvchi 0–24 ball qo'yadi (UZBMB: 12 mezon × 0–2).
    Bir xil javobli ikki o'quvchidan essesi yaxshirog'i ko'proq Rasch balli oladi.
    """
    reset_database()
    register_teacher()
    key = _subject_key("Ona tili va adabiyot")
    created = create_test(subject="Ona tili va adabiyot", key_questions=key, duration=180, essay=True)
    plain = create_test(subject="Ona tili va adabiyot", key_questions=key, duration=180)

    essay_text = "Vatan " * 120
    attempts = {}
    sent, restore = _capture_result_notices()
    try:
        for index in range(6):
            user = 7400 + index
            started = client.post(f"/api/tests/{created['code']}/start", json={}, headers=as_user(user)).json()
            assert started["essay"] == {"number": 45, "min_words": 100}
            attempt_id = started["attempt_id"]
            attempts[user] = attempt_id
            # 7400 va 7401 — javoblari bir xil (yarmi to'g'ri); qolganlari har xil
            solved = 20 if index < 2 else 5 + index * 6
            answers = {str(item["number"]): (item["correct"] if position < solved else "Z")
                       for position, item in enumerate(key)}
            if index == 0:
                # Qoralama ham esseni uzun saqlaydi (300 belgidan ko'p)
                saved = client.post(f"/api/attempts/{attempt_id}/answers", headers=as_user(user),
                                    json={"answers": {"essay": essay_text}})
                assert saved.status_code == 200
            else:
                answers["essay"] = "Qisqa esse" if index == 1 else ""
            finished = client.post(f"/api/attempts/{attempt_id}/finish", json={"answers": answers}, headers=as_user(user))
            assert finished.status_code == 200, finished.text
        plain_start = client.post(f"/api/tests/{plain['code']}/start", json={}, headers=as_user(7400)).json()
        assert plain_start["essay"] is None
        client.post(f"/api/attempts/{plain_start['attempt_id']}/finish", json={"answers": {"essay": "x"}},
                    headers=as_user(7400))
    finally:
        restore()

    teacher = as_user(555)
    detail_url = f"/api/teacher/tests/{created['id']}/students/{attempts[7400]}"
    detail = client.get(f"{detail_url}/detail", headers=teacher).json()
    assert detail["essay"]["text"] == essay_text.strip() and detail["essay"]["words"] == 120
    assert detail["essay"]["score"] is None and detail["essay"]["max"] == 24 and detail["essay"]["number"] == 45

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=teacher).json()
    assert results["essay_max"] == 24 and results["essay_pending"] == 6
    assert results["section_names"][-1] == "Esse"
    same = {row["attempt_id"]: row["ball"] for row in results["rows"]}
    assert same[attempts[7400]] == same[attempts[7401]]  # esse baholanmaguncha — teng

    # Baholash: 0–24 oralig'ida; 7400 — 20 ball, 7401 — 2 ball (100 so'zdan kam)
    assert client.put(f"{detail_url}/essay", json={"score": 25}, headers=teacher).status_code == 422
    assert client.put(f"{detail_url}/essay", json={"score": 20}, headers=teacher).json()["score"] == 20
    other_url = f"/api/teacher/tests/{created['id']}/students/{attempts[7401]}/essay"
    assert client.put(other_url, json={"score": 2}, headers=teacher).status_code == 200

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=teacher).json()
    rows = {row["attempt_id"]: row for row in results["rows"]}
    assert results["essay_pending"] == 4
    assert rows[attempts[7400]]["essay"] == 20 and rows[attempts[7400]]["sections"]["Esse"] == 83.3
    assert rows[attempts[7400]]["ball"] > rows[attempts[7401]]["ball"], (rows[attempts[7400]], rows[attempts[7401]])
    assert rows[attempts[7400]]["percent"] == rows[attempts[7401]]["percent"]  # foiz — faqat savollar

    # Bahoni olib tashlash mumkin
    assert client.put(other_url, json={"score": None}, headers=teacher).json()["score"] is None

    # Essesiz testda baholab bo'lmaydi; boshqa o'qituvchi baholay olmaydi
    plain_attempt = client.get(f"/api/teacher/tests/{plain['id']}/results", headers=teacher).json()["rows"][0]["attempt_id"]
    assert client.put(f"/api/teacher/tests/{plain['id']}/students/{plain_attempt}/essay",
                      json={"score": 3}, headers=teacher).status_code == 400
    register_teacher(556)
    assert client.put(f"{detail_url}/essay", json={"score": 5}, headers=as_user(556)).status_code in (403, 404)

    for url in (f"/api/teacher/tests/{created['id']}/pdf", f"/api/teacher/tests/{created['id']}/certificates"):
        pdf = client.get(url, headers=teacher)
        assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF"), url


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
                         "Ona tili va adabiyot": "text", "DTM testi": "text"}
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
        # Natijalar rasmi (PNG) — har fanda, DTM testida ham; har sahifa alohida
        image = client.get(f"/api/teacher/tests/{created['id']}/image", headers=as_user(555))
        assert image.status_code == 200 and image.content[1:4] == b"PNG", (subject, image.status_code)
        assert int(image.headers["X-Page-Count"]) >= 1
        assert client.get(f"/api/teacher/tests/{created['id']}/image?page=99", headers=as_user(555)).status_code == 404

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
        # Ikkala o'qituvchining xabarlari ham yetib kelguncha kutiladi (777 ga xabar 555 dan keyin ketishi mumkin)
        while _time.monotonic() < deadline and (
            sum(text.count("Natija #") for _, text, _ in sent) < 300 or not any(chat == 777 for chat, _, _ in sent)
        ):
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
    # Hali botda tasdiqlanmagan — kod tekshirilmaydi
    assert client.post(f"/api/app-login/{token}/verify", json={"code": "000000"}).status_code == 400
    assert client.get("/api/me", headers={"X-App-Session": "soxta"}).status_code == 401

    with SessionLocal() as session:
        assert main.confirm_app_login(session, "yoq-token", 4242, "Ilova Odam", "ilova")[0] is False
        ok, text = main.confirm_app_login(session, token, 4242, "Ilova Odam", "ilova")
        assert ok is True
        assert main.confirm_app_login(session, token, 9999, "Boshqa", None)[0] is False  # qayta tasdiqlab bo'lmaydi
        code = session.query(AppLogin).filter(AppLogin.token == token).one().code
        assert code in text and len(code) == 6 and code.isdigit()

    # Noto'g'ri kod — rad etiladi, urinish soni oshadi
    assert client.post(f"/api/app-login/{token}/verify", json={"code": "000000"}).status_code == 400

    granted = client.post(f"/api/app-login/{token}/verify", json={"code": code})
    assert granted.status_code == 200, granted.text
    assert granted.json()["session"]
    # Seans faqat bir marta beriladi — to'g'ri kod bilan ham ikkinchi marta bo'lmaydi
    assert client.post(f"/api/app-login/{token}/verify", json={"code": code}).status_code == 410

    headers = {"X-App-Session": granted.json()["session"]}
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
    assert client.post(f"/api/app-login/{old}/verify", json={"code": "123456"}).status_code == 410


def test_android_ilovaga_kirish_bir_necha_notogri_kod_qaytadan_boshlatadi():
    reset_database()
    token = client.post("/api/app-login").json()["token"]
    with SessionLocal() as session:
        _, text = main.confirm_app_login(session, token, 4242, "Ilova Odam", "ilova")
    code = text.split("<code>")[1].split("</code>")[0]

    for _ in range(main.APP_LOGIN_MAX_ATTEMPTS - 1):
        assert client.post(f"/api/app-login/{token}/verify", json={"code": "000000"}).status_code == 400
    # Oxirgi (beshinchi) noto'g'ri urinish — butunlay yopadi, to'g'ri kod bilan ham kirib bo'lmaydi
    assert client.post(f"/api/app-login/{token}/verify", json={"code": "000000"}).status_code == 410
    assert client.post(f"/api/app-login/{token}/verify", json={"code": code}).status_code == 410


def test_google_tekshiruvchisi_sinov_akkauntiga_kiradi():
    reset_database()
    wrong = client.post("/api/review-login", json={"username": "reviewer@chittak.uz", "password": "noto'g'ri"})
    assert wrong.status_code == 401, wrong.text

    original = main.REVIEW_PASSWORD_SHA256
    main.REVIEW_PASSWORD_SHA256 = hashlib.sha256(b"sinov-parol").hexdigest()
    try:
        granted = client.post("/api/review-login", json={"username": " Reviewer@Chittak.uz ", "password": "sinov-parol"})
        again = client.post("/api/review-login", json={"username": "reviewer@chittak.uz", "password": "sinov-parol"})
    finally:
        main.REVIEW_PASSWORD_SHA256 = original
    assert granted.status_code == 200 and again.status_code == 200, granted.text

    headers = {"X-App-Session": granted.json()["session"]}
    me = client.get("/api/me", headers=headers).json()
    assert me["tg_id"] == main.REVIEW_TG_ID, me
    # Ikkinchi kirish o'sha bitta demo akkauntga tushadi
    assert client.get("/api/me", headers={"X-App-Session": again.json()["session"]}).json()["id"] == me["id"]
    # Telegram'da yo'q akkauntni majburiy kanal ekrani to'smaydi
    assert client.get("/api/me/subscription", headers=headers).json() == {"missing": []}
    # Tekshiruvchi o'qituvchi bo'lib, test yarata oladi
    registered = client.post("/api/teacher/register", json={"first_name": "Demo", "last_name": "O'qituvchi"}, headers=headers)
    assert registered.status_code == 200, registered.text
    assert client.get("/api/teacher/stats", headers=headers).status_code == 200


def test_dars_jadvali_setka_oquv_reja_va_yuklama():
    """Smena setkasi, sinf o'quv rejasi, o'qituvchi yuklamasi va darsni setkaga qo'yish (to'qnashuvlar bilan)."""

    reset_database()
    _set_name(701, "Aziza", "Karimova")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Jadval maktabi", "subjects": []}, headers=as_user(701)).json()["school"]
    _set_name(702, "Bobur", "Aliyev")
    _set_name(703, "Dilnoza", "Rahimova")
    for tg_id, subject in ((702, "Matematika"), (703, "Ona tili")):
        joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": [subject]},
                             headers=as_user(tg_id))
        assert joined.status_code == 200, joined.text
    a = client.post("/api/classes", json={"name": "5-A"}, headers=as_user(701)).json()
    b = client.post("/api/classes", json={"name": "5-B"}, headers=as_user(701)).json()
    users = {member["name"]: member["user_id"] for member in
             client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]}
    bobur, dilnoza = users["Bobur Aliyev"], users["Dilnoza Rahimova"]

    # Smenani faqat rahbariyat tuzadi; darslar ustma-ust tushmaydi
    shift_body = {"name": "1-smena", "weekdays": [0, 1, 2, 3, 4, 5],
                  "periods": [{"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"}, {"start": "09:50", "end": "10:35"}]}
    assert client.post("/api/schools/shifts", json=shift_body, headers=as_user(702)).status_code == 403
    bad = {**shift_body, "periods": [{"start": "08:00", "end": "08:45"}, {"start": "08:40", "end": "09:20"}]}
    assert client.post("/api/schools/shifts", json=bad, headers=as_user(701)).status_code == 422
    plan = client.post("/api/schools/shifts", json=shift_body, headers=as_user(701)).json()
    shift_id = plan["shifts"][0]["id"]
    assert plan["shifts"][0]["periods"][1] == {"start": "08:55", "end": "09:40"}, plan
    for class_id in (a["id"], b["id"]):
        assert client.put(f"/api/classes/{class_id}/shift", json={"shift_id": shift_id}, headers=as_user(701)).status_code == 200

    # O'quv reja: 5-A da Matematika 5 soat (Bobur), Ona tili 4 soat (o'qituvchisiz)
    plan = client.put(f"/api/classes/{a['id']}/plan", json={"items": [
        {"subject": "Matematika", "hours": 5, "teacher_user_id": bobur},
        {"subject": "Ona tili", "hours": 4},
    ]}, headers=as_user(701)).json()
    row = next(item for item in plan["classes"] if item["id"] == a["id"])
    assert row["planned_hours"] == 9 and row["shift_id"] == shift_id, row
    math = next(item for item in plan["subjects"] if item["subject"] == "Matematika")
    assert math["teachers"] == ["Bobur Aliyev"] and math["needed_hours"] == 5 and math["unassigned_hours"] == 0, math
    mother = next(item for item in plan["subjects"] if item["subject"] == "Ona tili")
    assert mother["unassigned_hours"] == 4, mother
    dup = client.put(f"/api/classes/{a['id']}/plan", json={"items": [{"subject": "Fizika", "hours": 2}, {"subject": "fizika", "hours": 1}]},
                     headers=as_user(701))
    assert dup.status_code == 422, dup.text

    # Yuklama: Bobur 18 soat, faqat Du–Chor, 08:00–10:00
    plan = client.put(f"/api/schools/members/{bobur}/workload", json={"weekly_hours": 18, "work_days": [0, 1, 2],
                                                                      "work_start": "08:00", "work_end": "10:00"}, headers=as_user(701)).json()
    teacher = next(item for item in plan["teachers"] if item["user_id"] == bobur)
    assert (teacher["weekly_hours"], teacher["work_days"], teacher["assigned_hours"]) == (18, [0, 1, 2], 5), teacher

    def place(class_id, weekday, period, subject="Matematika", teacher_id=bobur):
        return client.post(f"/api/classes/{class_id}/schedule", json={
            "weekday": weekday, "period": period, "start_time": "00:00", "end_time": "00:01",
            "subject": subject, "teacher_user_id": teacher_id}, headers=as_user(701))

    # Vaqt setkadan olinadi (yuborilgan 00:00 emas)
    first = place(a["id"], 0, 2)
    assert first.status_code == 200, first.text
    slot = first.json()["slots"][0]
    assert (slot["start_time"], slot["end_time"], slot["period"]) == ("08:55", "09:40", 2), slot
    assert first.json()["plan"][0]["placed"] == 1, first.json()["plan"]
    # O'qituvchi bir vaqtda ikki sinfda bo'lmaydi; ish kuni va ish vaqtidan tashqarida ham
    assert place(b["id"], 0, 2).status_code == 409
    assert "ishlamaydi" in place(b["id"], 4, 1).json()["detail"]
    assert "ish vaqti" in place(b["id"], 0, 3).json()["detail"]
    assert place(a["id"], 6, 1).status_code == 422  # smenada yakshanba yo'q
    assert place(a["id"], 0, 4).status_code == 422  # 4-dars yo'q
    assert place(b["id"], 1, 1).status_code == 200

    # Setka o'zgarsa — darslar yangi vaqtga ko'chadi
    moved = {**shift_body, "periods": [{"start": "08:30", "end": "09:15"}, {"start": "09:20", "end": "09:59"}, {"start": "10:05", "end": "10:50"}]}
    assert client.put(f"/api/schools/shifts/{shift_id}", json=moved, headers=as_user(701)).status_code == 200
    slot = client.get(f"/api/classes/{a['id']}/schedule", headers=as_user(701)).json()["slots"][0]
    assert (slot["start_time"], slot["end_time"]) == ("09:20", "09:59"), slot
    # Darsi bor kun yoki dars setkadan olib tashlanmaydi; ishlatilayotgan smena o'chirilmaydi
    fewer = {**moved, "periods": moved["periods"][:1]}
    assert client.put(f"/api/schools/shifts/{shift_id}", json=fewer, headers=as_user(701)).status_code == 409
    assert client.delete(f"/api/schools/shifts/{shift_id}", headers=as_user(701)).status_code == 409
    # ...rozilik bilan (drop_outside) — setkaga sig'magan darslar olib tashlanadi, qolgani ko'chadi
    before = len(client.get(f"/api/classes/{a['id']}/schedule", headers=as_user(701)).json()["slots"])
    dropped = client.put(f"/api/schools/shifts/{shift_id}?drop_outside=true", json=fewer, headers=as_user(701))
    assert dropped.status_code == 200 and dropped.json()["removed_lessons"] >= 1, dropped.text
    after = client.get(f"/api/classes/{a['id']}/schedule", headers=as_user(701)).json()["slots"]
    assert len(after) < before and all(slot["period"] == 1 for slot in after), after

    # Oddiy o'qituvchi rejani ko'radi, lekin yuklamalarni ko'rmaydi va o'zgartira olmaydi
    seen = client.get("/api/schools/timetable", headers=as_user(703)).json()
    assert seen["can_manage"] is False and seen["teachers"] == [] and seen["classes"], seen
    assert client.put(f"/api/classes/{a['id']}/plan", json={"items": []}, headers=as_user(703)).status_code == 403
    assert client.put(f"/api/schools/members/{dilnoza}/workload", json={"weekly_hours": 10}, headers=as_user(703)).status_code == 403


def test_dars_jadvali_avtomatik_tuziladi():
    """Reja + setka + o'qituvchi ish vaqti bo'yicha jadval o'zi tuziladi: to'qnashuvsiz, teshiksiz, rejadagi soat aniq."""

    reset_database()
    _set_name(711, "Aziza", "Karimova")
    school = client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani",
                                               "name": "Avto maktab", "subjects": []}, headers=as_user(711)).json()["school"]
    for tg_id, first, subject in ((712, "Bobur", "Matematika"), (713, "Dilnoza", "Ona tili"), (714, "Jamshid", "Ingliz tili")):
        _set_name(tg_id, first, "Aliyev")
        joined = client.post("/api/schools/join", json={"phone": "901234567", "code": current_code(school), "subjects": [subject]},
                             headers=as_user(tg_id))
        assert joined.status_code == 200, joined.text
    users = {member["name"]: member["user_id"] for member in
             client.get("/api/schools/me", headers=as_user(711)).json()["school"]["members"]}
    bobur, dilnoza, jamshid = users["Bobur Aliyev"], users["Dilnoza Aliyev"], users["Jamshid Aliyev"]
    a = client.post("/api/classes", json={"name": "5-A"}, headers=as_user(711)).json()
    b = client.post("/api/classes", json={"name": "5-B"}, headers=as_user(711)).json()
    c = client.post("/api/classes", json={"name": "5-C"}, headers=as_user(711)).json()  # smenasiz

    shift = client.post("/api/schools/shifts", json={"name": "1-smena", "weekdays": [0, 1, 2, 3, 4], "periods": [
        {"start": "08:00", "end": "08:45"}, {"start": "08:55", "end": "09:40"},
        {"start": "09:50", "end": "10:35"}, {"start": "10:45", "end": "11:30"}]}, headers=as_user(711)).json()["shifts"][0]
    for school_class in (a, b):
        client.put(f"/api/classes/{school_class['id']}/shift", json={"shift_id": shift["id"]}, headers=as_user(711))
    plan_items = [{"subject": "Matematika", "hours": 5, "teacher_user_id": bobur},
                  {"subject": "Ona tili", "hours": 4, "teacher_user_id": dilnoza},
                  {"subject": "Ingliz tili", "hours": 3, "teacher_user_id": jamshid}]
    for school_class in (a, b, c):
        assert client.put(f"/api/classes/{school_class['id']}/plan", json={"items": plan_items},
                          headers=as_user(711)).status_code == 200
    # Bobur faqat Du–Pa, 08:00–10:40 (har kuni birinchi 3 dars) ishlaydi
    client.put(f"/api/schools/members/{bobur}/workload", json={"work_days": [0, 1, 2, 3], "work_start": "08:00", "work_end": "10:40"},
               headers=as_user(711))
    # Qo'lda qo'yilgan dars "to'ldirish" rejimida joyida qoladi
    manual = client.post(f"/api/classes/{a['id']}/schedule", json={"weekday": 0, "period": 1, "start_time": "00:00",
                                                                    "end_time": "00:01", "subject": "Matematika",
                                                                    "teacher_user_id": bobur}, headers=as_user(711))
    assert manual.status_code == 200, manual.text

    url = "/api/schools/timetable/auto"
    assert client.post(url, json={}, headers=as_user(713)).status_code == 403  # oddiy o'qituvchi tuza olmaydi

    preview = client.post(url, json={"mode": "fill"}, headers=as_user(711)).json()
    assert preview["applied"] is False and preview["unplaced"] == [] and preview["gaps"] == 0, preview
    assert [item["class_name"] for item in preview["skipped"]] == ["5-C"] and "smena" in preview["skipped"][0]["reason"]
    assert len(preview["lessons"]) == 11 + 12  # 5-A da 1 tasi qo'lda qo'yilgan
    assert len(client.get(f"/api/classes/{b['id']}/schedule", headers=as_user(711)).json()["slots"]) == 0  # ko'rish — yozmaydi

    applied = client.post(url, json={"mode": "fill", "apply": True}, headers=as_user(711)).json()
    assert applied["lessons"] == preview["lessons"]  # natija har safar bir xil

    def check(class_ids):
        teacher_times = set()
        for class_id in class_ids:
            slots = client.get(f"/api/classes/{class_id}/schedule", headers=as_user(711)).json()["slots"]
            assert len({(slot["weekday"], slot["period"]) for slot in slots}) == len(slots) == 12  # sinf to'qnashuvi yo'q
            counts = {}
            for slot in slots:
                counts[slot["subject"]] = counts.get(slot["subject"], 0) + 1
                key = (slot["teacher_user_id"], slot["weekday"], slot["start_time"])
                assert key not in teacher_times, slot  # o'qituvchi bir vaqtda ikki sinfda emas
                teacher_times.add(key)
                if slot["teacher_user_id"] == bobur:
                    assert slot["weekday"] in (0, 1, 2, 3) and slot["end_time"] <= "10:40", slot
            assert counts == {"Matematika": 5, "Ona tili": 4, "Ingliz tili": 3}, counts
            by_day = {}
            for slot in slots:
                by_day.setdefault(slot["weekday"], []).append(slot["period"])
            for periods in by_day.values():  # teshik yo'q: darslar ketma-ket
                assert sorted(periods) == list(range(min(periods), min(periods) + len(periods))), by_day
        return teacher_times

    check([a["id"], b["id"]])
    slots_a = client.get(f"/api/classes/{a['id']}/schedule", headers=as_user(711)).json()["slots"]
    assert any(slot["id"] == manual.json()["slots"][0]["id"] for slot in slots_a)  # qo'lda qo'yilgani qoldi

    # Hammasi joylashgan — yana "to'ldirish" hech narsa qo'shmaydi; "qaytadan" esa yangidan tuzadi
    again = client.post(url, json={"mode": "fill"}, headers=as_user(711)).json()
    assert again["lessons"] == [] and again["unplaced"] == []
    rebuilt = client.post(url, json={"mode": "replace", "class_ids": [a["id"]], "apply": True}, headers=as_user(711)).json()
    assert rebuilt["removed"] == 12 and len(rebuilt["lessons"]) == 12 and rebuilt["unplaced"] == [], rebuilt
    check([a["id"], b["id"]])

    # Imkonsiz holat — sababi bilan aytiladi: Bobur 1 kun ishlasa, 10 soat sig'maydi
    client.put(f"/api/schools/members/{bobur}/workload", json={"work_days": [0], "work_start": "08:00", "work_end": "10:40"},
               headers=as_user(711))
    tight = client.post(url, json={"mode": "replace"}, headers=as_user(711)).json()
    assert sum(item["hours"] for item in tight["unplaced"]) == 10 - 3, tight["unplaced"]
    assert all("Bobur" in item["reason"] for item in tight["unplaced"]), tight["unplaced"]

    # Har kunga alohida ish vaqti: Bobur Du 08:00–09:40 (1–2-dars), Se 09:50–11:30 (3–4-dars), Ch kun bo'yi
    saved = client.put(f"/api/schools/members/{bobur}/workload", json={
        "work_days": [0, 1, 2], "day_hours": {"0": {"start": "08:00", "end": "09:40"}, "1": {"start": "09:50", "end": "11:30"}},
    }, headers=as_user(711))
    assert saved.status_code == 200, saved.text
    teacher = next(item for item in saved.json()["teachers"] if item["user_id"] == bobur)
    assert teacher["day_hours"] == {"0": {"start": "08:00", "end": "09:40"}, "1": {"start": "09:50", "end": "11:30"}}, teacher
    bad = client.put(f"/api/schools/members/{bobur}/workload", json={
        "work_days": [0], "day_hours": {"3": {"start": "08:00", "end": "09:00"}}}, headers=as_user(711))
    assert bad.status_code == 422  # ish kuni bo'lmagan kunga vaqt
    per_day = client.post(url, json={"mode": "replace", "apply": True}, headers=as_user(711)).json()
    for lesson in per_day["lessons"]:
        if lesson["teacher_user_id"] == bobur:
            assert lesson["weekday"] in (0, 1, 2), lesson
            assert lesson["weekday"] != 0 or lesson["period"] in (1, 2), lesson
            assert lesson["weekday"] != 1 or lesson["period"] in (3, 4), lesson
    # Qo'lda qo'yishda ham kunning o'z vaqti tekshiriladi
    clash = client.post(f"/api/classes/{c['id']}/schedule", json={"weekday": 1, "start_time": "08:00", "end_time": "08:45",
                                                                    "subject": "Matematika", "teacher_user_id": bobur},
                        headers=as_user(711))
    assert clash.status_code == 409 and "seshanba kungi ish vaqti 09:50–11:30" in clash.json()["detail"], clash.text

    # Har o'qituvchiga bot orqali o'z jadvali ketadi (faqat rahbariyat yuboradi)
    assert client.post("/api/schools/timetable/notify", headers=as_user(713)).status_code == 403
    sent, restore = _capture_result_notices()
    try:
        result = client.post("/api/schools/timetable/notify", headers=as_user(711)).json()
    finally:
        restore()
    assert result["sent"] == 3 and result["teachers"] == ["Bobur Aliyev", "Dilnoza Aliyev", "Jamshid Aliyev"], result
    to_bobur = [text for chat, text in sent if chat == 712]
    assert len(to_bobur) == 1 and "Bobur Aliyev" in to_bobur[0] and "Jami: haftasiga 8 soat" in to_bobur[0], to_bobur
    assert "5-A · Matematika" in to_bobur[0] and "Ona tili" not in to_bobur[0]


def test_maxfiylik_siyosati_sahifasi():
    """Ochiq havola: kirishsiz ochiladi."""

    for path in ("/maxfiylik", "/privacy"):
        page = client.get(path)
        assert page.status_code == 200 and "Maxfiylik siyosati" in page.text, path


def test_akkauntni_ochirish_sahifasi():
    """Google Play talabi: ilova nomi, o'chirish qadamlari va saqlanadigan ma'lumotlar — kirishsiz ochiladi."""

    for path in ("/hisobni-ochirish", "/delete-account"):
        page = client.get(path)
        assert page.status_code == 200, path
        assert "Chittak" in page.text and "Akkauntimni o'chiring" in page.text and "saqlanib qoladi" in page.text, path


def _sheet_photo(test_id: int, marks: dict[int, str], skew: bool = True, code_of: int | None = None) -> str:
    """
    Varaq "surati": chop etiladigan PDF'ning o'zi rasmga aylantiriladi (ixcham titulda — A4 dagi
    birinchi titul kesib olinadi), belgilangan doirachalar bo'yaladi, so'ng qiyshaytiriladi.
    """

    import fitz
    from PIL import Image, ImageDraw

    from backend.database import Test

    with SessionLocal() as session:
        bubbles: list[dict] = []
        sheet_test = session.get(Test, test_id)
        variants = main._variant_names(session, sheet_test) or None
        pdf = main.generate_answer_sheet_pdf(sheet_test, bubbles=bubbles, variants=variants)
        geometry = main._sheet_geometry(sheet_test, variants)
        session.expunge(sheet_test)
    zoom = 2.0
    rendered = fitz.open(stream=pdf, filetype="pdf")[0].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    page = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.samples)
    height = geometry["height"] if geometry else page.height / zoom
    width = geometry["width"] if geometry else page.width / zoom
    left = (page.width / zoom - width) / 2  # tor titul A4 ning o'rtasida chop etiladi
    page = page.crop((round(left * zoom), 0, round((left + width) * zoom), round(height * zoom)))
    draw = ImageDraw.Draw(page)
    for bubble in bubbles:
        if bubble["page"] != 1:
            continue
        cx, cy, r = bubble["x"] * zoom, (height - bubble["y"]) * zoom, bubble["r"] * zoom
        if bubble["kind"] == "code":
            if code_of is not None:
                # Boshqa imtihon kodi: katak qayta bo'yaladi
                fill = "black" if code_of >> int(bubble["letter"]) & 1 else "white"
                draw.rectangle([cx - r - 1, cy - r - 1, cx + r + 1, cy + r + 1], fill=fill)
            continue
        if bubble["kind"] == "mark":
            continue
        if bubble["letter"] in marks.get(bubble["ref"], "").split(","):
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=2, fill=(40, 40, 40))
    if skew:
        background = Image.new("RGB", (page.width + 300, page.height + 300), (90, 70, 60))
        background.paste(page.rotate(4, expand=True, fillcolor=(90, 70, 60)), (120, 110))
        page = background
    buffer = io.BytesIO()
    page.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def test_ixcham_titul_savollar_soniga_moslashadi():
    """6–7-versiya: ism va yozma savolsiz ixcham titul; o'lchami savollar soniga qarab (bo'sh titul — bitta)."""

    import fitz

    from backend.database import Test

    reset_database()
    register_teacher()

    def closed_test(count: int, version: int = 7) -> int:
        questions = [{"number": n, "question_type": "single", "option_count": 4, "correct": "ABCD"[n % 4]}
                     for n in range(1, count + 1)]
        test_id = create_test(enrollment_type="closed", key_questions=questions)["id"]
        with SessionLocal() as session:
            session.get(Test, test_id).sheet_version = version  # oldin chop etilgan ixcham titul
            session.commit()
        return test_id

    sizes = {}
    for count in (25, 70, 150, 300):
        test_id = closed_test(count)
        layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
        sizes[count] = (round(layout["sheet_page"]["height"]), layout["page_count"])
        questions = {bubble["ref"] for bubble in layout["bubbles"] if bubble["kind"] == "single"}
        assert len(questions) == count
        sheet = client.get(f"/api/teacher/tests/{test_id}/answer-sheet", headers=as_user(555))
        pages = fitz.open(stream=sheet.content, filetype="pdf")
        if count == 25:
            # Bo'sh titul — bitta (o'qituvchi kerakli sonda chop etadi)
            assert len(pages) == 1 and pages[0].get_text().count("Sinov test") == 1
    assert sizes == {25: (281, 1), 70: (421, 1), 150: (842, 1), 300: (842, 2)}, sizes

    # 7-versiya: har 5 savollik blok 4 ta kvadrat bilan o'ralgan, har doiracha o'z blokini biladi
    anchors = {bubble["letter"] for bubble in layout["bubbles"] if bubble["kind"] == "anchor"}
    blocks = [bubble["block"] for bubble in layout["bubbles"] if bubble["kind"] in ("single", "id")]
    assert anchors and all(len(block) == 4 and set(block) <= anchors for block in blocks)

    # Yozma savolning doirachasi yo'q — faqat yozish katagi (12-versiya); kodi va burchaklari joyida
    test_id = create_test(enrollment_type="closed")["id"]
    layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
    assert {bubble["kind"] for bubble in layout["bubbles"] if bubble["ref"] == 4} == {"write"}

    # 25 savol: ID va javoblar o'qiladi
    test_id = closed_test(25)
    photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "1", "id3": "3", 1: "B", 13: "D", 25: "A"})
    scanned = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": photo}, headers=as_user(555))
    assert scanned.status_code == 200, scanned.text
    body = scanned.json()
    assert body["answers"] == {"1": "B", "13": "D", "25": "A"} and body["sheet_number"] == 13, body["answers"]

    # Oldin chop etilgan 6-versiya (bloksiz ixcham) titul avvalgidek o'qiladi
    with SessionLocal() as session:
        session.get(Test, test_id).sheet_version = 6
        session.commit()
    photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "0", "id3": "2", 4: "C"})
    scanned = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": photo}, headers=as_user(555)).json()
    assert scanned["answers"] == {"4": "C"} and scanned["sheet_number"] == 2, scanned

    # Oldin chop etilgan 5-versiya (A4) titul avvalgidek o'qiladi
    with SessionLocal() as session:
        session.get(Test, test_id).sheet_version = 5
        session.commit()
    layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
    assert layout["sheet_page"] is None and layout["old_bubbles"]
    scanned = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": _sheet_photo(test_id, {2: "C"})},
                          headers=as_user(555)).json()
    assert scanned["answers"] == {"2": "C"} and scanned["name_image"].startswith("data:image/png"), scanned["answers"]


def test_evalbee_uslubidagi_titul():
    """8–9-versiya: to'liq A4 da bitta titul, tepada ism/imtihon/sana qutisi, har 5 savol kvadratlar orasida.
    9-versiya kattaroq va pastida bo'yash namunasi bor; 8-versiya (oldin chop etilgan) ham o'qiladi."""

    import fitz

    from backend.database import Test

    reset_database()
    register_teacher()
    questions = [{"number": n, "question_type": "single", "option_count": 4, "correct": "ABCD"[n % 4]}
                 for n in range(1, 91)]
    # 9-versiya — moslashuvchan (90 savol: 4 ustun × 5 qator, kvadratlar faqat ishlatilgan bloklarda);
    # 8-versiya — to'liq A4, hamma kesishmada kvadrat
    for version, anchor_count, height in ((9, 30, 668), (8, 45, 842)):
        test_id = create_test(enrollment_type="closed", key_questions=questions)["id"]
        with SessionLocal() as session:
            assert session.get(Test, test_id).sheet_version == 12  # yangi test — eng yangi titul
            session.get(Test, test_id).sheet_version = version
            session.commit()
        base = f"/api/teacher/tests/{test_id}"
        layout = client.get(f"{base}/scan/layout", headers=as_user(555)).json()
        assert layout["page_count"] == 1 and round(layout["sheet_page"]["height"]) == height, layout["sheet_page"]
        anchors = {bubble["letter"] for bubble in layout["bubbles"] if bubble["kind"] == "anchor"}
        blocks = [bubble["block"] for bubble in layout["bubbles"] if bubble["kind"] in ("single", "id")]
        assert len(anchors) == anchor_count and all(len(block) == 4 and set(block) <= anchors for block in blocks)

        blank = fitz.open(stream=client.get(f"{base}/answer-sheet", headers=as_user(555)).content, filetype="pdf")
        text = blank[0].get_text()
        assert len(blank) == 1 and text.count("Sinov test") == 1 and "Ism-familiya" in text and "Sana" in text
        assert ("NOTO'G'RI" in text) == (version == 9)  # bo'yash namunasi — 9-versiyada

        marks = {"id0": "0", "id1": "0", "id2": "4", "id3": "2", 1: "B", 31: "C", 77: "D", 90: "A"}
        scanned = client.post(f"{base}/scan", json={"image_base64": _sheet_photo(test_id, marks)}, headers=as_user(555))
        assert scanned.status_code == 200, scanned.text
        body = scanned.json()
        assert body["answers"] == {"1": "B", "31": "C", "77": "D", "90": "A"} and body["sheet_number"] == 42, body
        assert body["name_image"].startswith("data:image/png")


def test_yotiq_titulli_natijasiz_test_tik_titulga_otadi():
    """6–7-versiya (yotiq ixcham titul): natija kiritilmagan bo'lsa — 8-versiya; natijasi borga tegilmaydi."""

    from backend.database import Test

    reset_database()
    register_teacher()
    empty = create_test(enrollment_type="closed")["id"]
    scanned = create_test(enrollment_type="closed")["id"]
    client.post(f"/api/teacher/tests/{scanned}/students", json={"names": ["Bola"]}, headers=as_user(555))
    with SessionLocal() as session:
        session.get(Test, empty).sheet_version = 7
        session.get(Test, scanned).sheet_version = 7
        session.commit()
    saved = client.post(f"/api/teacher/tests/{scanned}/scan/save", json={"sheet_number": 1, "answers": {"1": "B"}},
                        headers=as_user(555))
    assert saved.status_code == 200, saved.text

    main._upgrade_sheet_versions()
    with SessionLocal() as session:
        assert session.get(Test, empty).sheet_version == 9
        assert session.get(Test, scanned).sheet_version == 7


def test_skanerlash_yakunlansa_natijalar_botga_yuboriladi():
    """«Yakunlash»: o'qituvchi chatiga reyting (ism, ID, ball) va PDF hisobot ketadi."""

    reset_database()
    register_teacher()
    test_id = create_test(enrollment_type="closed")["id"]
    base = f"/api/teacher/tests/{test_id}"
    client.post(f"{base}/students", json={"names": ["Birinchi Bola", "Ikkinchi Bola", "Uchinchi Bola"]},
                headers=as_user(555))
    client.post(f"{base}/scan/save", json={"sheet_number": 2, "answers": {"1": "B", "2": "C"}}, headers=as_user(555))
    client.post(f"{base}/scan/save", json={"sheet_number": 1, "answers": {"1": "B"}}, headers=as_user(555))

    messages, pdfs = [], []
    originals = (main._telegram_send_message, main._send_pdf_to_telegram)
    main._telegram_send_message = lambda chat, text, markup=None: messages.append((chat, text)) or True
    main._send_pdf_to_telegram = lambda chat, name, data, caption="", *args, **kwargs: pdfs.append((chat, name, data[:4]))
    try:
        finished = client.post(f"{base}/scan/finish", headers=as_user(555))
    finally:
        main._telegram_send_message, main._send_pdf_to_telegram = originals
    assert finished.status_code == 200 and finished.json() == {"ok": True, "checked": 2, "total": 3}, finished.text
    text = "\n".join(text for _, text in messages)
    assert all(chat == 555 for chat, _ in messages) and "Tekshirildi: 2 / 3" in text, text
    assert "1. Ikkinchi Bola · ID 0002 — 2/4 · 50.0%" in text and "2. Birinchi Bola · ID 0001 — 1/4 · 25.0%" in text, text
    assert "Hali tekshirilmagan (1): Uchinchi Bola" in text
    assert pdfs and pdfs[0][0] == 555 and pdfs[0][2] == b"%PDF"


def test_titul_savollar_soniga_moslashadi():
    """
    11-versiya (uzluksiz): titul har doim tik va A4 ni to'ldiradi (bir tekis kattalashadi — savol kam bo'lsa doirachalar
    yirikroq); har titulda doirachalar orasi gorizontal va vertikal bo'yicha teng; ko'p savolda — ko'proq ustun.
    """

    reset_database()
    register_teacher()
    sizes = {}
    for count in (10, 45, 120):
        questions = [{"number": n, "question_type": "single", "option_count": 4, "correct": "ABCD"[n % 4]}
                     for n in range(1, count + 1)]
        test_id = create_test(enrollment_type="closed", key_questions=questions)["id"]
        layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
        page = layout["sheet_page"]
        radius = next(bubble["r"] for bubble in layout["bubbles"] if bubble["kind"] == "single")
        first_row = [bubble for bubble in layout["bubbles"] if bubble["kind"] == "single" and bubble["ref"] == 1]
        second_row = [bubble for bubble in layout["bubbles"] if bubble["kind"] == "single" and bubble["ref"] == 2]
        spacing = (round(first_row[1]["x"] - first_row[0]["x"], 2), round(first_row[0]["y"] - second_row[0]["y"], 2))
        anchors = sum(bubble["kind"] == "anchor" for bubble in layout["bubbles"])
        sizes[count] = (round(page["width"]), round(page["height"]), round(radius, 1), anchors, spacing)
        assert page["height"] >= page["width"], page  # tik

        # ID ning hamma raqamlari (0–9 oraliqsiz ustunda) — 5–9 qatorlari ham to'g'ri o'qilsin
        number = {10: "5679", 45: "8051", 120: "0394"}[count]
        marks = {**{f"id{index}": digit for index, digit in enumerate(number)}, 1: "A", count: "C"}
        scanned = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": _sheet_photo(test_id, marks)},
                              headers=as_user(555))
        assert scanned.status_code == 200, (count, scanned.text)
        assert scanned.json()["answers"] == {"1": "A", str(count): "C"} and scanned.json()["sheet_number"] == int(number), (
            count, scanned.json()["sheet_number"])
    assert all(width <= 595 and height <= 842 for width, height, *_ in sizes.values()), sizes  # bitta A4
    # A4 ni to'ldiradi: eni yoki bo'yi (ostida Chittak belgisiga joy qoldirib) to'liq
    assert all(max(width / 595, height / (842 - main.SHEET_BRAND_SPACE)) >= 0.95 for width, height, *_ in sizes.values()), sizes
    assert sizes[10][1] <= 842 - main.SHEET_BRAND_SPACE, sizes  # kam savolda ostida Chittak belgisiga joy qoladi
    assert all(spacing[0] == spacing[1] for *_, spacing in sizes.values()), sizes  # gorizontal = vertikal
    assert sizes[10][2] >= sizes[45][2] >= sizes[120][2], sizes  # savol kam — doirachalar yirikroq
    assert sizes[10][3] < sizes[45][3] < sizes[120][3], sizes  # kvadratlar faqat ishlatilgan bloklarda


def test_qisman_korinmagan_varaq_rad_etiladi():
    """Galereyadan surat: titulning bir qismi kadrdan chiqib ketgan — javob xato o'qilmasin, qayta so'ralsin."""

    import cv2
    import numpy as np

    reset_database()
    register_teacher()
    questions = [{"number": n, "question_type": "single", "option_count": 4, "correct": "ABCD"[n % 4]}
                 for n in range(1, 61)]
    test_id = create_test(enrollment_type="closed", key_questions=questions)["id"]
    photo = _sheet_photo(test_id, {"id0": "0", "id1": "0", "id2": "1", "id3": "3", 1: "B"})
    image = cv2.imdecode(np.frombuffer(base64.b64decode(photo), np.uint8), cv2.IMREAD_COLOR)
    height, width = image.shape[:2]
    url = f"/api/teacher/tests/{test_id}/scan"
    covered = image.copy()
    covered[int(height * 0.75):, int(width * 0.7):] = (90, 70, 60)  # pastki o'ng burchak (qo'l, stol) yopilgan
    for name, part in (("o'ng cheti kesilgan", image[:, : int(width * 0.8)]), ("pasti kesilgan", image[: int(height * 0.8)]),
                       ("burchagi yopilgan", covered)):
        cut = base64.b64encode(cv2.imencode(".jpg", part)[1].tobytes()).decode()
        refused = client.post(url, json={"image_base64": cut}, headers=as_user(555))
        # Burchaklar topilmaydi yoki imtihon kodi o'qilmaydi — har ikkalasida javob qaytmaydi
        assert refused.status_code == 422 and "answers" not in refused.json(), (name, refused.text)
    # Qiyshiq olingan, lekin to'liq kadrdagi surat — to'g'ri o'qiladi
    turned = cv2.warpAffine(image, cv2.getRotationMatrix2D((width / 2, height / 2), 10, 1.0), (width, height),
                            borderValue=(90, 70, 60))
    tilted = client.post(url, json={"image_base64": base64.b64encode(cv2.imencode(".jpg", turned)[1].tobytes()).decode()},
                         headers=as_user(555))
    assert tilted.status_code == 200 and tilted.json()["answers"] == {"1": "B"}, tilted.text
    whole = client.post(url, json={"image_base64": photo}, headers=as_user(555))
    assert whole.status_code == 200 and whole.json()["answers"] == {"1": "B"}, whole.text


def test_javoblar_kaliti_royxat_korinishida():
    """O'qituvchi kaliti titul emas — "1 — B" ro'yxati (yozma javob matn bo'lib)."""

    import re

    import fitz

    reset_database()
    register_teacher()
    test_id = create_test(enrollment_type="closed")["id"]
    key = client.get(f"/api/teacher/tests/{test_id}/answer-sheet", params={"answers": "true"}, headers=as_user(555))
    assert key.status_code == 200
    text = fitz.open(stream=key.content, filetype="pdf")[0].get_text()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    assert "JAVOBLAR KALITI" in lines[0]
    answers = [line for line in lines if re.match(r"^\d+ — ", line)]
    assert ["1 — B", "2 — C", "3 — A, C", "4 — 2sqrt(3)"] == answers, lines
    # Chittak brendi: sarlavhada nom va hujjat turi, pastda sahifa raqami
    assert "Chittak" in lines and "Javoblar kaliti" in lines and "1 / 1" in lines, lines


def test_telefon_internetsiz_tekshirgan_varaq_id_boyicha_saqlanadi():
    """Titul tuzilishida o'quvchilar ro'yxati (ID → ism) bor; navbatdan faqat ID va javoblar keladi."""

    reset_database()
    register_teacher()
    test_id = create_test(enrollment_type="closed")["id"]
    base = f"/api/teacher/tests/{test_id}"
    client.post(f"{base}/students", json={"names": ["Birinchi Bola", "Ikkinchi Bola"]}, headers=as_user(555))

    layout = client.get(f"{base}/scan/layout", headers=as_user(555)).json()
    assert [(s["number"], s["name"], s["status"]) for s in layout["students"]] == [
        (1, "Birinchi Bola", "pending"), (2, "Ikkinchi Bola", "pending")], layout["students"]
    assert layout["variants"] == [] and layout["school_students"] == []

    # Telefonda o'qilgan: ID 2, 1-savol to'g'ri, 2-savol xato — ism yuborilmaydi
    saved = client.post(f"{base}/scan/save", json={"sheet_number": 2, "answers": {"1": "B", "2": "A"}},
                        headers=as_user(555))
    assert saved.status_code == 200, saved.text
    assert saved.json()["name"] == "Ikkinchi Bola" and saved.json()["correct"] == 1, saved.json()
    layout = client.get(f"{base}/scan/layout", headers=as_user(555)).json()
    assert {s["name"]: s["status"] for s in layout["students"]} == {"Birinchi Bola": "pending", "Ikkinchi Bola": "done"}

    # Ro'yxatda yo'q ID — saqlanmaydi, sababi aytiladi (navbatda qoladi, o'qituvchi ism yozadi)
    unknown = client.post(f"{base}/scan/save", json={"sheet_number": 77, "answers": {"1": "B"}}, headers=as_user(555))
    assert unknown.status_code == 422 and "77" in unknown.json()["detail"]


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
    # Yangi test — EvalBee uslubidagi titul (8-versiya): to'liq A4, ism qatori bor, eski ID tuzilishi kerak emas
    # 3 savol — titul varaqni to'ldiradi, lekin to'liq A4 dan past (ostida Chittak belgisi)
    assert layout["old_bubbles"] is None and layout["sheet_page"]["height"] <= 842 - main.SHEET_BRAND_SPACE, layout["sheet_page"]
    assert layout["sheet_page"]["name_box"] is True and len(layout["sheet_page"]["name_area"]) == 4
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

    # Kod hamma testda bir xil; oldin chop etilgan (boshqa kodli) titul ham qabul qilinadi
    assert layout["sheet_code"] == main.SHEET_SHARED_CODE
    other = client.post(f"{base}/scan/result", json={**result, "sheet_code": (layout["sheet_code"] + 1) % 4096},
                        headers=as_user(555))
    assert other.status_code == 200, other.text


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
    assert body["page_count"] == 1 and body["name_image"].startswith("data:image/png")  # ism qatori rasmi
    assert [s["name"] for s in body["pending_students"]] == ["Ro'yxatdagi Bola"]

    # Ikki doiracha bo'yalgan oddiy savol — tanlanmaydi, o'qituvchiga belgilanadi
    double = client.post(url, json={"image_base64": _sheet_photo(test_id, {1: "A,B"})}, headers=as_user(555)).json()
    first = next(q for q in double["questions"] if q["ref"] == 1)
    assert first["status"] == "multiple" and first["chosen"] == "" and "1" not in double["answers"], first

    # Eski (test raqamiga bog'liq kodli) titul ham o'qiladi — test o'chsa, xuddi shunday yangi testga skanerlanadi
    old_code = (test_id * 2654435761) % 4096 or 1
    other = client.post(url, json={"image_base64": _sheet_photo(test_id, {1: "B"}, code_of=old_code)}, headers=as_user(555))
    assert other.status_code == 200 and other.json()["answers"].get("1") == "B", other.text

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
    assert detail["sheet_version"] == 12

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


def test_asosiy_kalitni_tahrirlash_natijalarni_qayta_hisoblaydi():
    reset_database()
    register_teacher()
    created = create_test(enrollment_type="closed")
    base = f"/api/teacher/tests/{created['id']}"
    headers = as_user(555)
    client.post(f"{base}/students", json={"names": ["Birinchi"]}, headers=headers)
    student = client.get(base, headers=headers).json()["students"][0]
    saved = client.post(f"{base}/students/{student['id']}/answers",
                        json={"answers": {"1": "B", "2": "D", "3": "A,C", "4": "5"}}, headers=headers).json()
    assert saved["correct"] == 2, saved

    questions = client.get(f"{base}/questions", headers=headers).json()["questions"]
    assert [q["correct"] for q in questions] == ["B", "C", "A,C", r"2\sqrt{3}"]

    # Qisman yuborilsa — yuborilmagan savollar kaliti o'zgarmaydi (kalitni bo'laklab kiritish mumkin)
    assert client.put(f"{base}/key", json={"answers": {"1": "B"}}, headers=headers).json()["changed"] == 0
    assert client.put(f"{base}/key", json={"answers": {"1": "B,C", "2": "D", "3": "A,C", "4": "5"}},
                      headers=headers).status_code == 422  # bitta javobli savolga ikki harf
    assert client.put(f"{base}/key", json={"answers": {"1": "B", "2": "D", "3": "A,C", "4": "5"}},
                      headers=as_user(777)).status_code in (403, 404)

    result = client.put(f"{base}/key", json={"answers": {"1": "b", "2": "D", "3": "c, a", "4": " 5 | besh "}},
                        headers=headers).json()
    assert result == {"ok": True, "changed": 2, "regraded": 1}, result
    review = client.get(f"{base}/students/{student['id']}/detail", headers=headers).json()
    assert review["correct"] == 4, review
    questions = client.get(f"{base}/questions", headers=headers).json()["questions"]
    assert [q["correct"] for q in questions] == ["B", "D", "A,C", "5|besh"]

    unchanged = client.put(f"{base}/key", json={"answers": {"1": "B", "2": "D", "3": "A,C", "4": "5|besh"}},
                           headers=headers).json()
    assert unchanged["changed"] == 0


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
    client.request("DELETE", f"/api/classes/{cid}/students/{students[2]['id']}", json={"reason": "Boshqa maktabga o'tdi"}, headers=as_user(701))
    view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Yangi Bola"}, headers=as_user(701)).json()
    assert [(s["full_name"], s["code"]) for s in view["students"]][-1] == ("Yangi Bola", 1032), view["students"]

    # O'rtadagi (1030) chiqarilsa ham — bo'sh joy keyingi yangisiga
    client.request("DELETE", f"/api/classes/{cid}/students/{students[0]['id']}", json={"reason": "Boshqa maktabga o'tdi"}, headers=as_user(701))
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
    # Ro'yxat alifbo tartibida — yangi o'quvchi oxirida bo'lishi shart emas
    assert next(s for s in after["students"] if s["full_name"] == "Yangi Bola")["code"] == 1004

    # ID oralig'ini olib tashlash: faqat admin/direktor; ID'lar bo'shaydi va qaytib berilmaydi
    assert client.delete(url, headers=as_user(703)).status_code in (403, 404)
    cleared = client.delete(url, headers=as_user(701))
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["code_start"] is None and all(s["code"] is None for s in cleared.json()["students"])
    reopened = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()
    assert all(s["code"] is None for s in reopened["students"]), reopened["students"]
    # Qayta belgilash mumkin
    again = client.put(url, json={"start": 1001, "end": 1038}, headers=as_user(701)).json()
    assert [s["code"] for s in again["students"]] == [1001, 1002, 1003, 1004], again["students"]

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
    assert detail["sheet_version"] == 12

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


def _as_kind(tg_id: int, kind: str) -> dict:
    """Ilova "O'quv kurslari"/"To'garaklar" bo'limida — so'rovlar shu turdagi muassasaga."""
    return {**as_user(tg_id), "X-Inst-Kind": kind}


def test_kurs_va_togarak_maktabdan_alohida_muassasa():
    """Bir o'qituvchi maktabda ham, o'quv markazida ham ishlaydi; har bo'lim o'z muassasasini ko'radi."""
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    place = {"region": "Toshkent shahri", "district": "Chilonzor tumani"}
    school = client.post("/api/schools", json={**place, "name": "Ilm maktabi"}, headers=as_user(701)).json()["school"]
    assert school["kind"] == "school"
    # Xuddi shu nom bilan o'quv markazi — boshqa turda takror emas
    course = client.post("/api/schools", json={**place, "name": "Ilm maktabi"}, headers=_as_kind(701, "course"))
    assert course.status_code == 200, course.text
    course = course.json()["school"]
    assert course["kind"] == "course" and course["id"] != school["id"]
    # Ikkinchi o'quv markazi ochilmaydi (har turdan bittaga a'zo)
    again = client.post("/api/schools", json={**place, "name": "Boshqa markaz"}, headers=_as_kind(701, "course"))
    assert again.status_code == 409 and "o'quv markazi" in again.text, again.text

    assert client.get("/api/schools/me", headers=as_user(701)).json()["school"]["id"] == school["id"]
    assert client.get("/api/schools/me", headers=_as_kind(701, "course")).json()["school"]["id"] == course["id"]
    assert client.get("/api/schools/me", headers=_as_kind(701, "club")).json()["school"] is None
    # Ro'yxatda faqat shu turdagilar
    listed = client.get("/api/schools", params=place, headers=_as_kind(701, "course")).json()
    assert [item["id"] for item in listed] == [course["id"]], listed

    # Guruhlar har muassasada alohida
    school_class = client.post("/api/classes", json={"name": "10/2"}, headers=as_user(701)).json()
    group = client.post("/api/classes", json={"name": "Ingliz tili B1"}, headers=_as_kind(701, "course")).json()
    assert [item["id"] for item in client.get("/api/classes", headers=as_user(701)).json()] == [school_class["id"]]
    assert [item["id"] for item in client.get("/api/classes", headers=_as_kind(701, "course")).json()] == [group["id"]]
    # Guruh sahifasi qaysi bo'limdan ochilsa ham ishlaydi (guruh o'z muassasasi bo'yicha tekshiriladi)
    assert client.get(f"/api/classes/{group['id']}", headers=as_user(701)).status_code == 200

    # Kod bilan qo'shilish — kod qaysi turga tegishli bo'lsa, o'sha turga
    _set_name(702, "Bobur", "Aliyev")
    joined = client.post("/api/schools/join", json={"code": current_code(course), "subjects": ["Ingliz tili"], "phone": "901112233"},
                         headers=as_user(702))
    assert joined.status_code == 200 and joined.json()["school"]["kind"] == "course", joined.text
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is None

    # Test markazi: o'qituvchining hamma muassasalaridagi guruhlar, muassasa nomi bilan
    register_teacher(701, "Aziza", "Karimova")
    client.post(f"/api/classes/{group['id']}/students", json={"full_name": "Jasur Umarov"}, headers=as_user(701))
    test = create_test(701, enrollment_type="closed")
    base = f"/api/teacher/tests/{test['id']}"
    classes = client.get(f"{base}/classes", headers=as_user(701)).json()
    assert {item["id"] for item in classes["classes"]} == {school_class["id"], group["id"]}, classes
    assert {item["school"] for item in classes["classes"]} == {"Ilm maktabi"} and len(classes["classes"]) == 2, classes
    attached = client.post(f"{base}/classes", json={"class_ids": [group["id"]]}, headers=as_user(701))
    assert attached.status_code == 200 and attached.json()["added"] == 1, attached.text


def test_guruhning_oz_oylik_narxi():
    """Kursda har guruhning o'z narxi; belgilanmagan guruh muassasaning umumiy summasini oladi."""
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani", "name": "Ilm markazi"},
                headers=_as_kind(701, "course"))
    english = client.post("/api/classes", json={"name": "Ingliz tili"}, headers=_as_kind(701, "course")).json()
    maths = client.post("/api/classes", json={"name": "Matematika"}, headers=_as_kind(701, "course")).json()
    client.post(f"/api/classes/{english['id']}/students", json={"full_name": "Ali Valiyev"}, headers=_as_kind(701, "course"))
    sid = client.get(f"/api/classes/{english['id']}", headers=as_user(701)).json()["students"][0]["id"]
    month = client.get(f"/api/classes/{english['id']}/payments", headers=as_user(701)).json()["month"]

    # Narx yo'q — to'lov qabul qilinmaydi
    blocked = client.post(f"/api/classes/{english['id']}/students/{sid}/payments", json={"month": month, "amount": 1000},
                          headers=as_user(701))
    assert blocked.status_code == 422, blocked.text

    fee = client.put(f"/api/classes/{english['id']}/fee", json={"monthly_fee": 300_000}, headers=as_user(701))
    assert fee.status_code == 200 and fee.json()["effective_fee"] == 300_000, fee.text
    status = client.get(f"/api/classes/{english['id']}/payments", headers=as_user(701)).json()
    assert status["monthly_fee"] == 300_000 and status["students"][0]["due"] == 300_000, status
    paid = client.post(f"/api/classes/{english['id']}/students/{sid}/payments", json={"month": month, "amount": 300_000},
                       headers=as_user(701))
    assert paid.status_code == 200, paid.text

    # Umumiy summa faqat o'z narxi yo'q guruhlarga ta'sir qiladi
    client.put("/api/schools/fee", json={"monthly_fee": 500_000}, headers=_as_kind(701, "course"))
    summary = client.get("/api/schools/payments/summary", headers=_as_kind(701, "course")).json()
    fees = {row["id"]: (row["monthly_fee"], row["own_fee"]) for row in summary["classes"]}
    assert fees == {english["id"]: (300_000, True), maths["id"]: (500_000, False)}, fees

    # Narxni o'chirish — umumiy summaga qaytadi
    reset = client.put(f"/api/classes/{english['id']}/fee", json={"monthly_fee": None}, headers=as_user(701)).json()
    assert reset["monthly_fee"] is None and reset["effective_fee"] == 500_000, reset

    # Faqat admin/direktor belgilaydi
    _set_name(702, "Bobur", "Aliyev")
    course = client.get("/api/schools/me", headers=_as_kind(701, "course")).json()["school"]
    client.post("/api/schools/join", json={"code": current_code(course), "subjects": ["Ingliz tili"], "phone": "901112233"},
                headers=as_user(702))
    denied = client.put(f"/api/classes/{english['id']}/fee", json={"monthly_fee": 1}, headers=as_user(702))
    assert denied.status_code == 403, denied.text


def test_narx_oshsa_tolangan_eski_oylar_qarzga_aylanmaydi():
    """Har oy o'sha oydagi narx bilan hisoblanadi; narxni qaysi oydan amal qilishini tanlash mumkin."""
    from backend.database import ClassStudent, SessionLocal
    from backend.main import _current_month, _shift_month

    reset_database()
    _set_name(701, "Aziza", "Karimova")
    kind = _as_kind(701, "course")
    client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani", "name": "Ilm markazi"},
                headers=kind)
    group = client.post("/api/classes", json={"name": "Ingliz tili"}, headers=kind).json()
    client.post(f"/api/classes/{group['id']}/students", json={"full_name": "Ali Valiyev"}, headers=kind)
    sid = client.get(f"/api/classes/{group['id']}", headers=kind).json()["students"][0]["id"]
    now = _current_month()
    two_ago, one_ago = _shift_month(now, -2), _shift_month(now, -1)
    with SessionLocal() as session:  # o'quvchi 2 oy oldin (o'sha oyning boshida) qo'shilgan — oyning kunidan qat'i nazar
        session.get(ClassStudent, sid).created_at = datetime.strptime(f"{two_ago}-03", "%Y-%m-%d")
        session.commit()
    client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 300_000}, headers=kind)
    for month in (two_ago, one_ago):
        paid = client.post(f"/api/classes/{group['id']}/students/{sid}/payments", json={"month": month, "amount": 300_000},
                           headers=kind)
        assert paid.status_code == 200, paid.text

    # Narx oshdi (joriy oydan) — o'tgan oylar to'liq to'langanicha qoladi
    raised = client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 400_000}, headers=kind).json()
    assert raised["effective_fee"] == 400_000
    assert [row["monthly_fee"] for row in raised["history"]] == [400_000, 300_000], raised["history"]
    info = client.get(f"/api/classes/{group['id']}/students/{sid}/payments", headers=kind).json()
    by_month = {row["month"]: row for row in info["months"]}
    assert by_month[two_ago]["status"] == "paid" and by_month[one_ago]["status"] == "paid", by_month
    assert by_month[now]["due"] == 400_000 and info["total_debt"] == 400_000, info
    past = client.get(f"/api/classes/{group['id']}/payments", params={"month": one_ago}, headers=kind).json()
    assert past["monthly_fee"] == 300_000 and past["totals"]["debtors"] == 0, past

    # Umumiy summa ham tarix bilan: guruhning o'z narxi bo'lmasa, eski oylar eski summada
    client.put("/api/schools/fee", json={"monthly_fee": 200_000}, headers=kind)
    other = client.post("/api/classes", json={"name": "Matematika"}, headers=kind).json()
    client.put("/api/schools/fee", json={"monthly_fee": 250_000, "from_month": _shift_month(now, 1)}, headers=kind)
    assert client.get(f"/api/classes/{other['id']}/payments", headers=kind).json()["monthly_fee"] == 200_000
    assert client.get(f"/api/classes/{other['id']}/payments", params={"month": _shift_month(now, 1)},
                      headers=kind).json()["monthly_fee"] == 250_000

    # Ilgari noto'g'ri bo'lib qolgan tarixni tuzatish: narxni o'tgan oydan qo'yish mumkin
    fixed = client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 350_000, "from_month": one_ago}, headers=kind).json()
    assert [row["from_month"] for row in fixed["history"]][:2] == [now, one_ago], fixed["history"]
    too_far = client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 1, "from_month": _shift_month(now, 5)},
                         headers=kind)
    assert too_far.status_code == 422, too_far.text
    assert client.get("/api/schools/fee-history", params={"class_id": group["id"]}, headers=kind).json()["history"][0]["monthly_fee"] == 400_000


def test_tarixsiz_oshirilgan_narxni_tuzatish():
    """Tuzatishdan oldin oshirilgan narx (tarixsiz): eski narx "avvaldan", yangisi oshgan oydan — eski qarzlar yo'qoladi."""
    from backend.database import ClassStudent, SchoolClass, SessionLocal
    from backend.main import _current_month, _shift_month

    reset_database()
    _set_name(701, "Aziza", "Karimova")
    kind = _as_kind(701, "course")
    client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani", "name": "Ilm markazi"},
                headers=kind)
    group = client.post("/api/classes", json={"name": "Ingliz tili"}, headers=kind).json()
    client.post(f"/api/classes/{group['id']}/students", json={"full_name": "Ali Valiyev"}, headers=kind)
    sid = client.get(f"/api/classes/{group['id']}", headers=kind).json()["students"][0]["id"]
    now = _current_month()
    one_ago = _shift_month(now, -1)
    with SessionLocal() as session:
        # O'tgan oyning boshida qo'shilgan (oyning qaysi kuni ishga tushirilishidan qat'i nazar — 2 oy)
        session.get(ClassStudent, sid).created_at = datetime.strptime(f"{one_ago}-03", "%Y-%m-%d")
        session.get(SchoolClass, group["id"]).monthly_fee = 300_000  # eski tizimda — tarixsiz
        session.commit()
    client.post(f"/api/classes/{group['id']}/students/{sid}/payments", json={"month": one_ago, "amount": 300_000}, headers=kind)
    with SessionLocal() as session:
        session.get(SchoolClass, group["id"]).monthly_fee = 400_000  # eski tizimda oshirilgan — tarix yo'q
        session.commit()
    broken = client.get(f"/api/classes/{group['id']}/students/{sid}/payments", headers=kind).json()
    assert {row["month"]: row["remaining"] for row in broken["months"]}[one_ago] == 100_000  # xato qarz

    client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 300_000, "from_month": "0000-00"}, headers=kind)
    fixed = client.put(f"/api/classes/{group['id']}/fee", json={"monthly_fee": 400_000, "from_month": now}, headers=kind).json()
    assert [(row["from_month"], row["monthly_fee"]) for row in fixed["history"]] == [(now, 400_000), ("0000-00", 300_000)]
    info = client.get(f"/api/classes/{group['id']}/students/{sid}/payments", headers=kind).json()
    months = {row["month"]: row for row in info["months"]}
    assert months[one_ago]["status"] == "paid" and months[now]["due"] == 400_000 and info["total_debt"] == 400_000, info


def test_sinf_ichida_test_markazi_imtihonlari():
    """Sinfga biriktirilgan Test markazi imtihonlari sinf sahifasida: skanerlash holati, egasi."""
    reset_database()
    school_class = _school_with_class(701)
    register_teacher(701, "Aziza", "Karimova")
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova"]}, headers=as_user(701))
    assert client.get(f"/api/classes/{cid}/center-tests", headers=as_user(701)).json() == []

    test = create_test(701, enrollment_type="closed")
    attached = client.post(f"/api/teacher/tests/{test['id']}/classes", json={"class_ids": [cid]}, headers=as_user(701))
    assert attached.status_code == 200 and attached.json()["added"] == 2, attached.text
    listed = client.get(f"/api/classes/{cid}/center-tests", headers=as_user(701)).json()
    assert len(listed) == 1 and listed[0]["id"] == test["id"], listed
    assert listed[0]["is_mine"] is True and listed[0]["total"] == 2 and listed[0]["done"] == 0, listed

    assert listed[0]["can_remove"] is True, listed

    # Boshqa maktab o'qituvchisi bu sinfni ko'rmaydi va olib tashlay olmaydi
    _set_name(799, "Begona", "Ustoz")
    assert client.get(f"/api/classes/{cid}/center-tests", headers=as_user(799)).status_code == 404
    assert client.delete(f"/api/classes/{cid}/center-tests/{test['id']}", headers=as_user(799)).status_code == 404

    # Imtihonni sinfdan olib tashlash: sinf ro'yxatidan va imtihon ro'yxatidan chiqadi, test qoladi
    removed = client.delete(f"/api/classes/{cid}/center-tests/{test['id']}", headers=as_user(701))
    assert removed.status_code == 200 and removed.json()["removed"] == 2, removed.text
    assert client.get(f"/api/classes/{cid}/center-tests", headers=as_user(701)).json() == []
    detail = client.get(f"/api/teacher/tests/{test['id']}", headers=as_user(701))
    assert detail.status_code == 200 and detail.json()["students"] == [], detail.text
    again = client.delete(f"/api/classes/{cid}/center-tests/{test['id']}", headers=as_user(701))
    assert again.status_code == 404, again.text
    # Qayta biriktirish mumkin
    reattached = client.post(f"/api/teacher/tests/{test['id']}/classes", json={"class_ids": [cid]}, headers=as_user(701))
    assert reattached.json()["added"] == 2, reattached.text

    # Natija kiritilgach: imtihon kartochkasida o'rtacha foiz; "qo'lda kiritilganlar" ro'yxatida takrorlanmaydi
    attempt_id = client.get(f"/api/teacher/tests/{test['id']}", headers=as_user(701)).json()["students"][0]["id"]
    saved = client.post(f"/api/teacher/tests/{test['id']}/students/{attempt_id}/answers",
                        json={"answers": {"1": "B", "2": "C"}}, headers=as_user(701))
    assert saved.status_code == 200, saved.text
    card = client.get(f"/api/classes/{cid}/center-tests", headers=as_user(701)).json()[0]
    assert card["class_test_id"] and card["results_visible"] is True, card
    assert card["average_percent"] == 50 and card["participants"] == 1, card  # 4 savoldan 2 tasi
    assert client.get(f"/api/classes/{cid}/tests", params={"manual": "true"}, headers=as_user(701)).json() == []
    # Rahbariyatning "Test natijalari" sahifasi — hammasi (skanerlanganlari ham)
    assert len(client.get(f"/api/classes/{cid}/tests", headers=as_user(701)).json()) == 1
    assert client.get(f"/api/class-tests/{card['class_test_id']}", headers=as_user(701)).status_code == 200
    manual = client.post(f"/api/classes/{cid}/tests", headers=as_user(701), json={
        "title": "Qo'lda", "subject": "Matematika", "taken_on": "2026-11-01", "max_score": 10,
        "scores": {str(client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"][0]["id"]): 7}})
    assert manual.status_code == 200, manual.text
    manual_titles = [item["title"] for item in client.get(f"/api/classes/{cid}/tests", params={"manual": "true"},
                                                          headers=as_user(701)).json()]
    assert manual_titles == ["Qo'lda"], manual_titles


def test_dtm_testi_fan_shabloni():
    """DTM testi: 90 ta savol, majburiy 3 fan (10 tadan) va 2 mutaxassislik fani (30 tadan), yozmasiz."""
    from backend.subjects import default_sections, frontend_config

    config = frontend_config()["DTM testi"]
    assert config["tests"] == 90 and config["written"] == 0 and config["duration"] == 180, config
    sections = default_sections("DTM testi", 90)
    assert [len(section["numbers"]) for section in sections] == [10, 10, 10, 30, 30], sections
    assert sections[3]["numbers"][0] == 31 and sections[4]["numbers"][-1] == 90
    assert default_sections("DTM testi", 45) == []  # boshqa tuzilishdagi testga shablon qo'llanmaydi


def test_dtm_testi_natijasi_dtm_bali_bilan():
    """DTM testida har to'g'ri javob o'z fanining koeffitsienti bilan: 1–30 — 1,1; 31–60 — 3,1; 61–90 — 2,1 (jami 189)."""
    reset_database()
    register_teacher()
    key = [{"number": n, "question_type": "single", "option_count": 4, "correct": "A"} for n in range(1, 91)]
    # Test turi — "DTM test" (ilovada alohida tur: fan — "DTM testi", 90 savol)
    created = create_test(enrollment_type="closed", subject="DTM testi", duration=180, key_questions=key, test_type="dtm")
    assert client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["test_type"] == "dtm"
    client.post(f"/api/teacher/tests/{created['id']}/students", json={"names": ["Hasan Hasanov", "Zuhra Karimova"]},
                headers=as_user(555))
    students = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()["students"]
    by_name = {student["name"]: student["id"] for student in students}

    # Hasan: majburiy fanlarning hammasi (30×1,1 = 33) + 1-mutaxassislikdan 10 ta (10×3,1 = 31) = 64
    hasan = {str(n): "A" for n in range(1, 31)} | {str(n): "A" for n in range(31, 41)} | {str(n): "B" for n in range(41, 91)}
    # Zuhra: faqat 2-mutaxassislik (30×2,1 = 63) — to'g'ri javoblari ko'p (30 ta), lekin DTM bali kamroq
    zuhra = {str(n): "B" for n in range(1, 61)} | {str(n): "A" for n in range(61, 91)}
    for name, answers in (("Hasan Hasanov", hasan), ("Zuhra Karimova", zuhra)):
        saved = client.post(f"/api/teacher/tests/{created['id']}/students/{by_name[name]}/answers",
                            json={"answers": answers}, headers=as_user(555))
        assert saved.status_code == 200, saved.text

    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    rows = {row["name"]: row for row in results["rows"]}
    assert rows["Hasan Hasanov"]["dtm_ball"] == 64.0 and rows["Zuhra Karimova"]["dtm_ball"] == 63.0, rows
    # O'rin DTM bali bo'yicha
    assert [row["name"] for row in results["rows"]] == ["Hasan Hasanov", "Zuhra Karimova"]
    assert results["summary"]["dtm_max"] == 189.0 and results["summary"]["max_dtm_ball"] == 64.0, results["summary"]
    assert len(results["section_names"]) == 5, results["section_names"]
    # Har fan bo'yicha: ball va to'g'ri/jami ("Ona tili: 11 (10/10)", "1-asosiy fan: 31 (10/30)")
    hasan_scores = rows["Hasan Hasanov"]["section_scores"]
    assert hasan_scores["Ona tili (majburiy)"] == {"ball": 11.0, "correct": 10, "total": 10}, hasan_scores
    assert hasan_scores["1-asosiy fan"] == {"ball": 31.0, "correct": 10, "total": 30}, hasan_scores
    assert hasan_scores["2-asosiy fan"]["ball"] == 0, hasan_scores

    # Asosiy fanlar nomi bilan (o'qituvchi formada tanlaydi) — natijada ustun shu nom bilan
    named = client.put(f"/api/teacher/tests/{created['id']}/sections", headers=as_user(555), json={"text": "\n".join([
        "Ona tili (majburiy): 1-10", "Matematika (majburiy): 11-20", "O'zbekiston tarixi (majburiy): 21-30",
        "Matematika: 31-60", "Fizika: 61-90"])})
    assert named.status_code == 200, named.text
    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert results["section_names"][3:] == ["Matematika", "Fizika"], results["section_names"]
    zuhra_row = next(row for row in results["rows"] if row["name"] == "Zuhra Karimova")
    assert zuhra_row["section_scores"]["Fizika"] == {"ball": 63.0, "correct": 30, "total": 30}, zuhra_row

    # Javobi kiritilmagan o'quvchi — "Kiritilmagan" ro'yxatida
    client.post(f"/api/teacher/tests/{created['id']}/students", json={"names": ["Otabek Qodirov"]}, headers=as_user(555))
    results = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()
    assert results["pending"] == [{"name": "Otabek Qodirov"}], results["pending"]

    # PDF va Excel hisobotlari DTM bali bilan yaratiladi
    for path in ("pdf", "excel"):
        response = client.get(f"/api/teacher/tests/{created['id']}/{path}", headers=as_user(555))
        assert response.status_code == 200 and len(response.content) > 1000, (path, response.status_code)

    # Oddiy test — DTM bali yo'q
    plain = create_test(enrollment_type="closed")
    client.post(f"/api/teacher/tests/{plain['id']}/students", json={"names": ["Ali Valiyev"]}, headers=as_user(555))
    sid = client.get(f"/api/teacher/tests/{plain['id']}", headers=as_user(555)).json()["students"][0]["id"]
    client.post(f"/api/teacher/tests/{plain['id']}/students/{sid}/answers", json={"answers": {"1": "B"}}, headers=as_user(555))
    plain_results = client.get(f"/api/teacher/tests/{plain['id']}/results", headers=as_user(555)).json()
    assert plain_results["rows"][0]["dtm_ball"] is None and plain_results["summary"]["dtm_max"] is None


def test_dtm_ismli_titul_skanerlanadi():
    """
    Sinfga biriktirilgan DTM imtihoni: har o'quvchiga ismli titul (ID bo'yalgan, bitta PDF), bloklar
    fanlar bo'yicha (namunadagidek). Skaner chop etilgan ismli titulni o'qiydi: o'quvchi ID'dan
    taniladi, javoblar to'g'ri savollarga tushadi (31–45 — 1-ustun pastida, 76–90 — 4-ustun pastida).
    """

    import fitz
    from PIL import Image, ImageDraw

    from backend.database import Test

    reset_database()
    school_class = _school_with_class(701)
    register_teacher(701, "Aziza", "Karimova")
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova"]}, headers=as_user(701))
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1001, "end": 1038}, headers=as_user(701))
    key = [{"number": n, "question_type": "single", "option_count": 4, "correct": "ABCD"[n % 4]} for n in range(1, 91)]
    created = create_test(701, enrollment_type="closed", subject="DTM testi", duration=180, key_questions=key)
    base = f"/api/teacher/tests/{created['id']}"
    client.put(f"{base}/sections", headers=as_user(701), json={"text": "\n".join([
        "Ona tili (majburiy): 1-10", "Matematika (majburiy): 11-20", "O'zbekiston tarixi (majburiy): 21-30",
        "Matematika: 31-60", "Fizika: 61-90"])})
    assert client.post(f"{base}/classes", json={"class_ids": [cid]}, headers=as_user(701)).json()["added"] == 2

    # Ismli titullar: har o'quvchiga bitta sahifa
    personal = client.get(f"{base}/answer-sheet?personal=true", headers=as_user(701))
    assert personal.status_code == 200 and personal.content[:4] == b"%PDF", personal.text[:200]
    document = fitz.open(stream=personal.content, filetype="pdf")
    assert len(document) == 2
    texts = [page.get_text() for page in document]
    assert "Ali Valiyev" in texts[0] and "Zuhra Karimova" in texts[1], texts
    for title in ("1-majburiy fan", "O'zbekiston tarixi", "1-asosiy fan: Matematika (31–60)", "2-asosiy fan: Fizika (61–90)"):
        assert title in texts[0], title

    # Bloklar namunadagidek: 31 — 1-ustunda (ID ostida), 76 — 4-ustunda; 1 — 2-ustun tepasida
    with SessionLocal() as session:
        sheet_test = session.get(Test, created["id"])
        bubbles: list[dict] = []
        main.generate_answer_sheet_pdf(sheet_test, bubbles=bubbles)
        geometry = main._sheet_geometry(sheet_test)
        session.expunge(sheet_test)
    first_x = {bubble["label"]: bubble["x"] for bubble in bubbles if bubble.get("letter") == "A" and bubble["kind"] != "id"}
    id_x = min(bubble["x"] for bubble in bubbles if bubble["kind"] == "id")
    assert abs(first_x["31"] - id_x) < 1 and first_x["1"] > first_x["31"] and first_x["76"] > first_x["61"], first_x

    # Zuhraning titulini "suratga olamiz": javoblarni bo'yaymiz (ID titulda allaqachon bo'yalgan)
    zuhra_page = next(index for index, text in enumerate(texts) if "Zuhra Karimova" in text)
    zoom = 2.0
    rendered = document[zuhra_page].get_pixmap(matrix=fitz.Matrix(zoom, zoom))
    page = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.samples)
    width, height = geometry["width"], geometry["height"]
    left = (page.width / zoom - width) / 2
    page = page.crop((round(left * zoom), 0, round((left + width) * zoom), round(height * zoom)))
    draw = ImageDraw.Draw(page)
    answers = {str(n): "ABCD"[n % 4] if n <= 60 else "A" for n in range(1, 91)}  # 61–90: hammasi A
    for bubble in bubbles:
        if bubble["page"] != 1 or bubble["kind"] in ("code", "mark", "anchor", "id"):
            continue
        if answers.get(bubble["label"]) == bubble["letter"]:
            cx, cy, r = bubble["x"] * zoom, (height - bubble["y"]) * zoom, bubble["r"] * zoom
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], outline="black", width=2, fill=(40, 40, 40))
    background = Image.new("RGB", (page.width + 300, page.height + 300), (90, 70, 60))
    background.paste(page.rotate(4, expand=True, fillcolor=(90, 70, 60)), (120, 110))
    buffer = io.BytesIO()
    background.save(buffer, "JPEG", quality=85)
    photo = base64.b64encode(buffer.getvalue()).decode()

    scanned = client.post(f"{base}/scan", json={"image_base64": photo}, headers=as_user(701))
    assert scanned.status_code == 200, scanned.text
    result = scanned.json()
    zuhra = next(student for student in client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"]
                 if student["full_name"] == "Zuhra Karimova")
    assert result["sheet_number"] == zuhra["code"] and result["match"]["name"] == "Zuhra Karimova", result
    read = result["answers"]
    assert all(read.get(str(n)) == answers[str(n)] for n in range(1, 91)), {
        n: (read.get(str(n)), answers[str(n)]) for n in range(1, 91) if read.get(str(n)) != answers[str(n)]}


def test_yotoqxona_xonalar_joylashtirish_va_kechki_yoqlama():
    """Rahbariyat xona ochadi va o'quvchilarni joylashtiradi; tarbiyachi faqat o'z xonasida yo'qlama oladi."""
    reset_database()
    school_class = _school_with_class(701)  # 701 — maktab admini
    cid = school_class["id"]
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova", "Jasur Umarov"]},
                headers=as_user(701))
    students = {s["full_name"]: s["id"] for s in client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"]}

    # Tarbiyachi va oddiy o'qituvchi
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    assert school["dorm"] is True
    for tg_id, first in ((702, "Tarbiyachi"), (703, "Oddiy")):
        _set_name(tg_id, first, "Ustoz")
        client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "901112233"},
                    headers=as_user(tg_id))
    assert client.get("/api/schools/me", headers=as_user(703)).json()["school"]["dorm"] is False
    assert client.get("/api/dorm", headers=as_user(703)).status_code == 403
    assert client.post("/api/dorm/rooms", json={"name": "201"}, headers=as_user(703)).status_code == 403

    room = client.post("/api/dorm/rooms", json={"name": "201", "floor": 2, "capacity": 2, "warden_user_id": None},
                       headers=as_user(701))
    assert room.status_code == 200, room.text
    room_id = room.json()["id"]
    other = client.post("/api/dorm/rooms", json={"name": "202", "floor": 2, "capacity": 4}, headers=as_user(701)).json()["id"]
    assert client.post("/api/dorm/rooms", json={"name": "201"}, headers=as_user(701)).status_code == 409

    # Joylashtirish: sig'imdan oshmaydi; boshqa xonaga ko'chirish
    assert len(client.get("/api/dorm/candidates", headers=as_user(701)).json()) == 3
    too_many = client.post(f"/api/dorm/rooms/{room_id}/residents", json={"student_ids": list(students.values())},
                           headers=as_user(701))
    assert too_many.status_code == 422 and "2 ta bo'sh o'rin" in too_many.text, too_many.text
    placed = client.post(f"/api/dorm/rooms/{room_id}/residents",
                         json={"student_ids": [students["Ali Valiyev"], students["Zuhra Karimova"]]}, headers=as_user(701))
    assert placed.json()["placed"] == 2
    assert [c["name"] for c in client.get("/api/dorm/candidates", headers=as_user(701)).json()] == ["Jasur Umarov"]
    client.post(f"/api/dorm/rooms/{other}/residents", json={"student_ids": [students["Zuhra Karimova"]]}, headers=as_user(701))
    overview = client.get("/api/dorm", headers=as_user(701)).json()
    occupied = {item["name"]: item["occupied"] for item in overview["rooms"]}
    assert occupied == {"201": 1, "202": 1}, occupied
    assert overview["totals"]["capacity"] == 6 and overview["totals"]["free"] == 4, overview["totals"]

    # Tarbiyachi tayinlanadi — faqat o'z xonasini ko'radi, joylashtira olmaydi
    tarbiyachi = next(m["user_id"] for m in client.get("/api/schools/me", headers=as_user(701)).json()["school"]["members"]
                      if m["name"].startswith("Tarbiyachi"))
    assert client.put(f"/api/dorm/rooms/{room_id}", json={"name": "201", "floor": 2, "capacity": 2, "warden_user_id": tarbiyachi},
                      headers=as_user(701)).status_code == 200
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"]["dorm"] is True
    warden_view = client.get("/api/dorm", headers=as_user(702)).json()
    assert [item["name"] for item in warden_view["rooms"]] == ["201"] and warden_view["can_manage"] is False
    assert client.get(f"/api/dorm/rooms/{other}", headers=as_user(702)).status_code == 403
    assert client.post(f"/api/dorm/rooms/{room_id}/residents", json={"student_ids": [students["Jasur Umarov"]]},
                       headers=as_user(702)).status_code == 403

    # Kechki yo'qlama: tarbiyachi oladi, qayta saqlansa — yangilanadi (bir kun — bitta)
    blank = client.get(f"/api/dorm/rooms/{room_id}/check", headers=as_user(702)).json()
    assert blank["taken"] is False and [row["status"] for row in blank["rows"]] == ["present"]
    saved = client.put(f"/api/dorm/rooms/{room_id}/check", json={"marks": {str(students["Ali Valiyev"]): "absent"}},
                       headers=as_user(702))
    assert saved.status_code == 200 and saved.json()["counts"]["absent"] == 1, saved.text
    resaved = client.put(f"/api/dorm/rooms/{room_id}/check", json={"marks": {str(students["Ali Valiyev"]): "excused"}},
                         headers=as_user(702)).json()
    assert resaved["counts"] == {"present": 0, "absent": 0, "sick": 0, "excused": 1}, resaved
    sick = client.put(f"/api/dorm/rooms/{room_id}/check", json={"marks": {str(students["Ali Valiyev"]): "sick"}},
                      headers=as_user(702)).json()
    assert sick["counts"]["sick"] == 1, sick
    assert client.get("/api/dorm", headers=as_user(701)).json()["totals"]["sick"] == 1
    client.put(f"/api/dorm/rooms/{room_id}/check", json={"marks": {str(students["Ali Valiyev"]): "excused"}},
               headers=as_user(702))
    detail = client.get(f"/api/dorm/rooms/{room_id}", headers=as_user(701)).json()
    assert len(detail["checks"]) == 1 and detail["today"]["excused"] == 1, detail
    assert client.get("/api/dorm", headers=as_user(701)).json()["totals"]["checked_rooms"] == 1

    # Hisobot: matn (faqat yo'qlar), rasm va PDF; tarbiyachi ham oladi, oddiy o'qituvchi — yo'q
    text = client.get("/api/dorm/report/text", headers=as_user(701)).json()["text"]
    assert "Yotoqxona" in text and "Ruxsat bilan" in text and "Ali Valiyev — 201-xona" in text, text
    assert "Yo'qlama olinmagan xonalar: 202-xona" in text, text
    image = client.get("/api/dorm/report/file", params={"format": "image"}, headers=as_user(702))
    assert image.status_code == 200 and image.content[:4] == b"\x89PNG", image.status_code
    pdf = client.get("/api/dorm/report/file", params={"format": "pdf"}, headers=as_user(701))
    assert pdf.status_code == 200 and pdf.content[:4] == b"%PDF"
    assert client.get("/api/dorm/report/text", headers=as_user(703)).status_code == 403

    # Xonani o'chirish: avval o'quvchilarni chiqarish kerak
    assert client.delete(f"/api/dorm/rooms/{room_id}", headers=as_user(701)).status_code == 409
    assert client.delete(f"/api/dorm/rooms/{room_id}/residents/{students['Ali Valiyev']}", headers=as_user(701)).status_code == 200
    assert client.delete(f"/api/dorm/rooms/{room_id}", headers=as_user(701)).status_code == 200

    # To'garakda yotoqxona yo'q
    _set_name(710, "Murabbiy", "Aliyev")
    client.post("/api/schools", json={"region": "Toshkent shahri", "district": "Chilonzor tumani", "name": "Sport"},
                headers=_as_kind(710, "club"))
    assert client.get("/api/schools/me", headers=_as_kind(710, "club")).json()["school"]["dorm"] is False
    assert client.get("/api/dorm", headers=_as_kind(710, "club")).status_code == 404


def test_yotoqxona_binolar_va_hisobot_statistikasi():
    """Binolar (nomi ixtiyoriy): har binoda o'z 201-xonasi; hisobotda statistika va xonalar bo'yicha bo'sh o'rinlar."""
    reset_database()
    school_class = _school_with_class(701)
    h = as_user(701)
    client.post(f"/api/classes/{school_class['id']}/students/bulk",
                json={"names": ["Ali Valiyev", "Zuhra Karimova", "Jasur Umarov"]}, headers=h)
    ids = [s["id"] for s in client.get(f"/api/classes/{school_class['id']}", headers=h).json()["students"]]

    a = client.post("/api/dorm/buildings", json={"name": "A bino"}, headers=h).json()
    b = client.post("/api/dorm/buildings", json={"name": "  Qizlar   binosi "}, headers=h).json()
    assert b["name"] == "Qizlar binosi"
    assert client.post("/api/dorm/buildings", json={"name": "a BINO"}, headers=h).status_code == 409
    renamed = client.put(f"/api/dorm/buildings/{b['id']}", json={"name": "B bino"}, headers=h).json()
    assert renamed["name"] == "B bino"

    room_a = client.post("/api/dorm/rooms", json={"name": "201", "capacity": 2, "building_id": a["id"]}, headers=h)
    room_b = client.post("/api/dorm/rooms", json={"name": "201", "capacity": 4, "building_id": b["id"]}, headers=h)
    assert room_a.status_code == 200 and room_b.status_code == 200, (room_a.text, room_b.text)  # har binoda o'z 201-xonasi
    assert client.post("/api/dorm/rooms", json={"name": "201", "building_id": a["id"]}, headers=h).status_code == 409
    room_a, room_b = room_a.json()["id"], room_b.json()["id"]
    client.post(f"/api/dorm/rooms/{room_a}/residents", json={"student_ids": ids[:2]}, headers=h)
    client.post(f"/api/dorm/rooms/{room_b}/residents", json={"student_ids": ids[2:]}, headers=h)
    client.put(f"/api/dorm/rooms/{room_a}/check", json={"marks": {str(ids[0]): "sick"}}, headers=h)

    overview = client.get("/api/dorm", headers=h).json()
    buildings = {item["name"]: item for item in overview["buildings"]}
    assert set(buildings) == {"A bino", "B bino"}, buildings
    assert (buildings["A bino"]["occupied"], buildings["A bino"]["free"], buildings["A bino"]["sick"]) == (2, 0, 1)
    assert (buildings["B bino"]["free"], buildings["B bino"]["not_checked_students"]) == (3, 1)

    # Bino ichida xona bo'lsa — o'chirilmaydi
    assert client.delete(f"/api/dorm/buildings/{a['id']}", headers=h).status_code == 409

    text = client.get("/api/dorm/report/text", headers=h).json()["text"]
    for line in ("Jami o'quvchi: 3", "Bor: 1", "Kasal: 1", "Yo'qlama olinmagan: 1", "Bo'sh o'rin: 3",
                 "Ali Valiyev — A bino, 201-xona", "B bino: 201-xona — 3 ta"):
        assert line in text, (line, text)
    for fmt, magic in (("image", b"\x89PNG"), ("pdf", b"%PDF")):
        response = client.get("/api/dorm/report/file", params={"format": fmt}, headers=h)
        assert response.status_code == 200 and response.content[:4] == magic, fmt

    # Binosiz xona (masalan, eski) — alohida guruhda, keyin binoga o'tkaziladi
    old = client.post("/api/dorm/rooms", json={"name": "Eski 5", "capacity": 2}, headers=h).json()["id"]
    groups = [item["name"] for item in client.get("/api/dorm", headers=h).json()["buildings"]]
    assert groups[-1] == "Binoga biriktirilmagan", groups
    client.put(f"/api/dorm/rooms/{old}", json={"name": "Eski 5", "capacity": 2, "building_id": b["id"]}, headers=h)
    groups = [item["name"] for item in client.get("/api/dorm", headers=h).json()["buildings"]]
    assert groups == ["A bino", "B bino"], groups


def test_yotoqxona_birinchi_bino_eski_xonalarni_oladi():
    """Binolardan oldin ochilgan xonalar birinchi bino yaratilganda unga o'tadi — "Binoga biriktirilmagan" qolmaydi."""
    reset_database()
    _school_with_class(701)
    h = as_user(701)
    for name in ("101", "102"):
        client.post("/api/dorm/rooms", json={"name": name, "capacity": 4}, headers=h)
    assert [b["name"] for b in client.get("/api/dorm", headers=h).json()["buildings"]] == ["Binoga biriktirilmagan"]
    first = client.post("/api/dorm/buildings", json={"name": "A bino"}, headers=h).json()
    assert first["moved_rooms"] == 2, first
    second = client.post("/api/dorm/buildings", json={"name": "B bino"}, headers=h).json()
    assert second["moved_rooms"] == 0, second
    buildings = {b["name"]: b["rooms"] for b in client.get("/api/dorm", headers=h).json()["buildings"]}
    assert buildings == {"A bino": 2, "B bino": 0}, buildings


def test_yotoqxona_bir_nechta_xonani_birdan_ochish():
    """101–110 bitta so'rovda: qavat raqamdan, bor raqamlar o'tkaziladi, faqat rahbariyat, 100 tadan ortiq emas."""
    reset_database()
    _school_with_class(701)
    h = as_user(701)
    a = client.post("/api/dorm/buildings", json={"name": "A bino"}, headers=h).json()
    b = client.post("/api/dorm/buildings", json={"name": "B bino"}, headers=h).json()
    client.post("/api/dorm/rooms", json={"name": "103", "capacity": 2, "building_id": a["id"]}, headers=h)

    result = client.post("/api/dorm/rooms/bulk", json={"start": 101, "end": 110, "capacity": 6, "building_id": a["id"]},
                         headers=h)
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["skipped"] == ["103"] and len(body["created"]) == 9, body
    rooms = {r["name"]: r for r in client.get("/api/dorm", headers=h).json()["rooms"] if r["building_id"] == a["id"]}
    assert len(rooms) == 10 and rooms["110"]["floor"] == 1 and rooms["105"]["capacity"] == 6, rooms["110"]
    assert rooms["103"]["capacity"] == 2  # oldingi xona o'zgarmadi

    # Boshqa binoda o'sha raqamlar bemalol; qavat qo'lda berilsa — o'sha
    other = client.post("/api/dorm/rooms/bulk", json={"start": 1203, "end": 1204, "floor": 7, "building_id": b["id"]},
                        headers=h).json()
    assert other["created"] == ["1203", "1204"], other
    floors = {r["name"]: r["floor"] for r in client.get("/api/dorm", headers=h).json()["rooms"] if r["building_id"] == b["id"]}
    assert floors == {"1203": 7, "1204": 7}, floors
    auto = client.post("/api/dorm/rooms/bulk", json={"start": 1205, "end": 1205, "building_id": b["id"]}, headers=h).json()
    assert auto["created"] == ["1205"]
    assert {r["name"]: r["floor"] for r in client.get("/api/dorm", headers=h).json()["rooms"]}["1205"] == 12

    assert client.post("/api/dorm/rooms/bulk", json={"start": 5, "end": 2}, headers=h).status_code == 422
    assert client.post("/api/dorm/rooms/bulk", json={"start": 1, "end": 101}, headers=h).status_code == 422
    assert client.post("/api/dorm/rooms/bulk", json={"start": 1, "end": 2}, headers=as_user(703)).status_code == 403


def test_oquvchini_qidirish_butun_maktab_boyicha():
    """Ism bo'lagi (apostrof va ko'rinmas belgilarga sezgir emas), bir necha so'z, ID; boshqa maktab va chiqarilganlar chiqmaydi."""
    from backend.database import ClassStudent

    reset_database()
    first = _school_with_class(701)
    h = as_user(701)
    second = client.post("/api/classes", json={"name": "9-A"}, headers=h).json()
    with SessionLocal() as session:
        session.add_all([
            ClassStudent(class_id=first["id"], full_name="\u200bNo\u02bbmonov Asqatbek", student_code=1001),
            ClassStudent(class_id=first["id"], full_name="Valiyev Ali"),
            ClassStudent(class_id=second["id"], full_name="Alimova Zuhra"),
            ClassStudent(class_id=second["id"], full_name="Aliyev Chiqqan", removed_at=utcnow()),
        ])
        session.commit()
    other = _school_with_class(702)
    with SessionLocal() as session:
        session.add(ClassStudent(class_id=other["id"], full_name="Aliyev Begona"))
        session.commit()

    def search(q, headers=h):
        response = client.get("/api/schools/students/search", params={"q": q}, headers=headers)
        assert response.status_code == 200, response.text
        return [(s["full_name"], s["class_name"]) for s in response.json()["students"]]

    assert search("nomonov") == [("No\u02bbmonov Asqatbek", "10/2")]
    assert search("No'monov asq") == [("No\u02bbmonov Asqatbek", "10/2")]
    assert search("1001") == [("No\u02bbmonov Asqatbek", "10/2")]
    assert search("ali") == [("Alimova Zuhra", "9-A"), ("Valiyev Ali", "10/2")]  # chiqarilgan va boshqa maktab yo'q
    assert search("ali val") == [("Valiyev Ali", "10/2")]
    assert search("a") == []
    assert search("Begona", as_user(702)) == [("Aliyev Begona", "10/2")]
    assert client.get("/api/schools/students/search", params={"q": "ali"}, headers=as_user(799)).status_code == 403


def test_oquvchi_holati_va_profil_markazi():
    """Holat (maktabda/uyda/ruxsat/kasal): kim o'rnatadi, muddat o'tishi, sinf ro'yxati va profil bo'limlari."""
    from datetime import timedelta

    from backend.database import ClassStudent, StudentStatus

    reset_database()
    created = _school_with_class(701)
    cid = created["id"]
    h = as_user(701)
    school = client.get("/api/schools/me", headers=h).json()["school"]
    _set_name(702, "Oddiy", "Ustoz")
    joined = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 66"},
                         headers=as_user(702))
    assert joined.status_code == 200, joined.text
    with SessionLocal() as session:
        student = ClassStudent(class_id=cid, full_name="Valiyev Ali")
        session.add(student)
        session.commit()
        sid = student.id
    url = f"/api/classes/{cid}/students/{sid}"

    profile = client.get(url, headers=h).json()
    assert profile["status"]["status"] == "at_school" and profile["can_set_status"] is True, profile["status"]
    assert profile["payments"] is not None and profile["dorm"] is None and profile["results"] == []

    tomorrow = (main._dorm_today() + timedelta(days=1)).isoformat()
    yesterday = (main._dorm_today() - timedelta(days=1)).isoformat()
    # Oddiy o'qituvchi holatni o'zgartira olmaydi, to'lovni ko'rmaydi
    assert client.put(f"{url}/status", json={"status": "sick"}, headers=as_user(702)).status_code == 403
    teacher_view = client.get(url, headers=as_user(702)).json()
    assert teacher_view["can_set_status"] is False and teacher_view["payments"] is None
    # O'tgan sana bo'lmaydi
    assert client.put(f"{url}/status", json={"status": "sick", "until": yesterday}, headers=h).status_code == 422

    saved = client.put(f"{url}/status", json={"status": "sick", "until": tomorrow, "note": "  Shamollagan  "}, headers=h)
    assert saved.status_code == 200, saved.text
    assert saved.json()["status"] == "sick" and saved.json()["note"] == "Shamollagan"
    profile = client.get(url, headers=h).json()
    assert profile["status"]["label"] == "Kasal" and profile["status"]["until"] == tomorrow
    assert [row["status"] for row in profile["status_history"]] == ["sick"]
    students = client.get(f"/api/classes/{cid}", headers=h).json()["students"]
    assert students[0]["away"] == "sick", students

    # Muddat o'tsa — yana maktabda
    with SessionLocal() as session:
        row = session.scalar(main.select(StudentStatus).where(StudentStatus.student_id == sid))
        row.until = main._dorm_today() - timedelta(days=1)
        session.commit()
    profile = client.get(url, headers=h).json()
    assert profile["status"]["status"] == "at_school" and profile["status"]["expired"] is True
    assert client.get(f"/api/classes/{cid}", headers=h).json()["students"][0]["away"] is None

    # Maktabga qaytdi — tarix saqlanadi
    client.put(f"{url}/status", json={"status": "home"}, headers=h)
    client.put(f"{url}/status", json={"status": "at_school", "until": tomorrow}, headers=h)
    profile = client.get(url, headers=h).json()
    assert profile["status"]["status"] == "at_school" and profile["status"]["until"] is None
    assert [row["status"] for row in profile["status_history"]] == ["at_school", "home", "sick"]
    assert client.put(f"{url}/status", json={"status": "bad"}, headers=h).status_code == 422


def test_moliyaviy_malumotlar_faqat_rahbariyat_va_kuratorga():
    """To'lov, qarz va narx: rahbariyat (admin, direktor, zam direktor) — hammasi, kurator — o'z sinfi, o'qituvchi — hech narsa."""
    from backend.database import ClassStudent, User

    reset_database()
    first = _school_with_class(701)
    h = as_user(701)
    second = client.post("/api/classes", json={"name": "9-A"}, headers=h).json()
    school = client.get("/api/schools/me", headers=h).json()["school"]
    for tg, name in ((702, "Kurator"), (703, "Zam"), (704, "Oddiy")):
        _set_name(tg, name, "Ustoz")
        joined = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": f"93 444 55 0{tg % 10}"},
                             headers=as_user(tg))
        assert joined.status_code == 200, joined.text
    with SessionLocal() as session:
        uid = {tg: session.scalar(main.select(User.id).where(User.tg_id == tg)) for tg in (702, 703, 704)}
        student = ClassStudent(class_id=first["id"], full_name="Valiyev Ali")
        other = ClassStudent(class_id=second["id"], full_name="Karimov Bek")
        session.add_all([student, other])
        session.commit()
        sid, oid = student.id, other.id
    assert client.put(f"/api/classes/{first['id']}/curator", json={"user_id": uid[702]}, headers=h).status_code == 200
    assert client.put(f"/api/schools/members/{uid[703]}/position", json={"position": "deputy"}, headers=h).status_code == 200
    assert client.put("/api/schools/fee", json={"monthly_fee": 500000}, headers=h).status_code == 200

    def status(path, tg):
        return client.get(path, headers=as_user(tg)).status_code

    first_pay, second_pay = f"/api/classes/{first['id']}/payments", f"/api/classes/{second['id']}/payments"
    # Kurator — faqat o'z sinfi
    assert status(first_pay, 702) == 200 and status(second_pay, 702) == 403
    assert status(f"/api/classes/{first['id']}/students/{sid}/payments", 702) == 200
    assert status(f"/api/classes/{second['id']}/students/{oid}/payments", 702) == 403
    assert status("/api/schools/payments/summary", 702) == 403
    # Zam direktor — hamma sinf va umumiy hisobot, lekin narxni belgilamaydi
    assert status(first_pay, 703) == 200 and status(second_pay, 703) == 200
    assert status("/api/schools/payments/summary", 703) == 200
    assert client.put("/api/schools/fee", json={"monthly_fee": 1}, headers=as_user(703)).status_code == 403
    # Oddiy o'qituvchi — hech narsa
    assert status(first_pay, 704) == 403 and status("/api/schools/payments/summary", 704) == 403
    assert status(f"/api/classes/{first['id']}/students/{sid}/payments", 704) == 403

    # Summa ham faqat ko'ra oladiganlarga keladi
    assert client.get("/api/schools/me", headers=as_user(704)).json()["school"]["monthly_fee"] is None
    assert client.get("/api/schools/me", headers=as_user(702)).json()["school"]["monthly_fee"] == 500000
    me_deputy = client.get("/api/schools/me", headers=as_user(703)).json()["school"]
    assert me_deputy["monthly_fee"] == 500000 and me_deputy["can_see_all_payments"] is True
    assert client.get(f"/api/classes/{first['id']}/students/{sid}", headers=as_user(704)).json()["payments"] is None
    assert client.get(f"/api/classes/{first['id']}/students/{sid}", headers=as_user(702)).json()["payments"]["total_debt"] > 0

    # Bosh sahifadagi qarzdorlar: o'qituvchida yo'q, kuratorda o'z sinfi, zam direktorda hamma
    def overview(tg):
        return client.get("/api/schools/overview", headers=as_user(tg)).json()

    assert overview(704)["stats"]["debtors"] is None and overview(704)["can"]["payments"] is False
    assert overview(702)["stats"]["debtors"] == 1
    assert overview(703)["stats"]["debtors"] == 2


def test_ochgan_test_varaqlari_xuddi_shunday_yangi_testga_skanerlanadi():
    """Titul tarqatildi, test adashib o'chdi — xuddi shunday yangi test yaratiladi va eski varaqlar unga o'qiladi."""
    reset_database()
    register_teacher()
    old_id = create_test(enrollment_type="closed")["id"]
    photo = _sheet_photo(old_id, {1: "B", 2: "A"})  # o'quvchi eski testning varag'ini belgiladi
    assert client.delete(f"/api/teacher/tests/{old_id}", headers=as_user(555)).status_code == 200

    new_id = create_test(enrollment_type="closed")["id"]
    assert new_id != old_id
    scanned = client.post(f"/api/teacher/tests/{new_id}/scan", json={"image_base64": photo}, headers=as_user(555))
    assert scanned.status_code == 200, scanned.text
    assert scanned.json()["answers"] == {"1": "B", "2": "A"}, scanned.json()["questions"]


def test_har_bir_oqituvchi_oquvchini_bittalab_qoshadi():
    """
    Istalgan o'qituvchi istalgan sinfga o'quvchini bittalab qo'shadi: kim qo'shgani saqlanadi,
    kuratorga xabar boradi, takroriy ismga ogohlantirish (tasdiqlansa — qo'shiladi).
    Excel'dan yuklash, tahrirlash va o'chirish — rahbariyat va kuratorda.
    """
    from backend.database import User

    reset_database()
    cid = _school_with_class(701)["id"]
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    for tg_id, first in ((702, "Kurator"), (704, "Fizik")):
        _set_name(tg_id, first, "Ustoz")
        client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": f"93 444 55 {tg_id - 700:02d}"},
                    headers=as_user(tg_id))
    with SessionLocal() as session:
        curator_id = session.scalar(main.select(User.id).where(User.tg_id == 702))
    assert client.put(f"/api/classes/{cid}/curator", json={"user_id": curator_id}, headers=as_user(701)).status_code == 200

    sent = []
    original = main._send_bot_text_in_background
    main._send_bot_text_in_background = lambda chat_id, text: sent.append((chat_id, text))
    try:
        teacher = as_user(704)
        view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Ali  Valiyev"}, headers=teacher)
        assert view.status_code == 200, view.text
        assert view.json()["can_manage"] is False
        student_id = view.json()["students"][0]["id"]
        # Kuratorga xabar: kim (fani bilan), qaysi sinfga, kimni
        assert len(sent) == 1 and sent[0][0] == 702, sent
        assert "Fizika o'qituvchisi Fizik Ustoz" in sent[0][1] and "Ali Valiyev" in sent[0][1] and "«10/2»" in sent[0][1], sent

        # Takroriy ism — ogohlantirish (409); tasdiqlansa — qo'shiladi
        duplicate = client.post(f"/api/classes/{cid}/students", json={"full_name": "ali valiyev"}, headers=teacher)
        assert duplicate.status_code == 409 and "Baribir" in duplicate.json()["detail"], duplicate.text
        forced = client.post(f"/api/classes/{cid}/students", json={"full_name": "ali valiyev", "force": True}, headers=teacher)
        assert forced.status_code == 200 and len(forced.json()["students"]) == 2

        # Kuratorning o'zi qo'shsa — xabar yo'q
        sent.clear()
        assert client.post(f"/api/classes/{cid}/students", json={"full_name": "Vali Aliyev"}, headers=as_user(702)).status_code == 200
        assert sent == []
    finally:
        main._send_bot_text_in_background = original

    # Profilda — kim qo'shgani
    profile = client.get(f"/api/classes/{cid}/students/{student_id}", headers=as_user(701)).json()
    assert profile["added_by"] == "Fizik Ustoz", profile["added_by"]

    # Tahrirlash, o'chirish va Excel'dan yuklash — oddiy o'qituvchiga yopiq; kurator qila oladi
    url = f"/api/classes/{cid}/students/{student_id}"
    assert client.put(url, json={"full_name": "Boshqa Ism"}, headers=as_user(704)).status_code == 403
    assert client.delete(url, headers=as_user(704)).status_code == 403
    assert client.post(f"/api/classes/{cid}/students/import/preview", json={"text": "X Y"}, headers=as_user(704)).status_code == 403
    # Kurator chiqaradi — sababi bilan (o'quvchi "Biriktirilmaganlar"ga tushadi)
    assert client.request("DELETE", url, json={"reason": "Boshqa maktabga o'tdi"}, headers=as_user(702)).status_code == 200


def test_davomatda_royxatda_yoq_oquvchini_qoshish():
    """Davomat paytida istalgan o'qituvchi ro'yxatda yo'q o'quvchini qo'shadi; tahrirlashda ham belgisi yoziladi."""
    from backend.database import ClassStudent

    reset_database()
    cid = _school_with_class(701)["id"]
    h = as_user(701)
    school = client.get("/api/schools/me", headers=h).json()["school"]
    _set_name(704, "Oddiy", "Ustoz")
    client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 04"},
                headers=as_user(704))
    with SessionLocal() as session:
        session.add(ClassStudent(class_id=cid, full_name="Valiyev Ali"))
        session.commit()
    teacher = as_user(704)

    # Oddiy o'qituvchi Excel'dan yuklay olmaydi, davomatdan esa bittalab qo'shadi
    assert client.post(f"/api/classes/{cid}/students/import/preview", json={"text": "Yangi Bola"}, headers=teacher).status_code == 403
    added = client.post(f"/api/classes/{cid}/students/quick", json={"full_name": "  Yangi   Bola "}, headers=teacher)
    assert added.status_code == 200, added.text
    new = added.json()
    assert new["full_name"] == "Yangi Bola" and "code" in new
    # Takror — rad etiladi (katta-kichik harf va apostrof farqi hisobga olinmaydi)
    assert client.post(f"/api/classes/{cid}/students/quick", json={"full_name": "yangi bola"}, headers=teacher).status_code == 409
    # Boshqa maktab o'qituvchisi qo'sha olmaydi
    _school_with_class(799)
    assert client.post(f"/api/classes/{cid}/students/quick", json={"full_name": "Begona"}, headers=as_user(799)).status_code in (403, 404)

    saved = client.post(f"/api/classes/{cid}/attendance", json={"subject": "Fizika", "marks": {str(new["id"]): "late"},
                                                               "lesson_start": "08:00", "lesson_end": "08:45"}, headers=teacher)
    assert saved.status_code == 200, saved.text
    marks = {mark["name"]: mark["status"] for mark in saved.json()["marks"]}
    assert marks == {"Valiyev Ali": "present", "Yangi Bola": "late"}, marks

    # Tahrirlash paytida qo'shilgan o'quvchi ham davomatga yoziladi
    later = client.post(f"/api/classes/{cid}/students/quick", json={"full_name": "Kech Keldi"}, headers=teacher).json()
    edited = client.put(f"/api/attendance/{saved.json()['id']}", json={"subject": "Fizika", "marks": {str(later["id"]): "absent"}},
                        headers=teacher)
    assert edited.status_code == 200, edited.text
    assert {mark["name"]: mark["status"] for mark in edited.json()["marks"]}["Kech Keldi"] == "absent"


def test_ismli_titul_faqat_bugun_darsda_borlarga():
    """Ismli titul: bugungi davomat bo'yicha kim bor-yo'qligi ko'rinadi, tanlanganlargagina chiqariladi."""
    reset_database()
    cid = _school_with_class(701)["id"]
    register_teacher(701, "Aziza", "Karimova")
    h = as_user(701)
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova", "Bek Umarov"]}, headers=h)
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1001, "end": 1038}, headers=h)
    test = create_test(701, enrollment_type="closed")
    base = f"/api/teacher/tests/{test['id']}"
    client.post(f"{base}/classes", json={"class_ids": [cid]}, headers=h)

    # Davomat hali olinmagan — hammasi "noma'lum"
    before = client.get(f"{base}/sheet-students", headers=h).json()
    assert before["has_attendance"] is False and {s["present"] for s in before["students"]} == {None}, before

    students = {s["full_name"]: s["id"] for s in client.get(f"/api/classes/{cid}", headers=h).json()["students"]}
    marks = {str(students["Zuhra Karimova"]): "absent", str(students["Bek Umarov"]): "late"}
    assert client.post(f"/api/classes/{cid}/attendance", json={"subject": "Matematika", "marks": marks,
                                                               "lesson_start": "08:00", "lesson_end": "08:45"},
                       headers=h).status_code == 200
    data = client.get(f"{base}/sheet-students", headers=h).json()
    assert data["has_attendance"] is True
    present = {s["name"]: s["present"] for s in data["students"]}
    assert present == {"Ali Valiyev": True, "Zuhra Karimova": False, "Bek Umarov": True}, present

    numbers = ",".join(str(s["number"]) for s in data["students"] if s["present"])
    import fitz
    picked = client.get(f"{base}/answer-sheet", params={"personal": "true", "numbers": numbers}, headers=h)
    everyone = client.get(f"{base}/answer-sheet", params={"personal": "true"}, headers=h)
    assert picked.status_code == 200 and everyone.status_code == 200, picked.text

    def names_in(pdf):
        text = "".join(page.get_text() for page in fitz.open(stream=pdf, filetype="pdf"))
        return {name for name in present if name in text}

    assert names_in(picked.content) == {"Ali Valiyev", "Bek Umarov"}
    assert names_in(everyone.content) == set(present)
    assert client.get(f"{base}/answer-sheet", params={"personal": "true", "numbers": "9999"}, headers=h).status_code == 400
    assert client.get(f"{base}/answer-sheet", params={"personal": "true", "numbers": "a,b"}, headers=h).status_code == 422


def test_sinf_ochilganda_bazaga_yozilmaydi():
    """O'chirilgan sinf o'quvchilarining ID'si har safar olinib-qaytarilmasin (sinf ochilishi sekinlashardi)."""
    from sqlalchemy import event

    from backend.database import engine

    reset_database()
    h = as_user(701)
    cid = _school_with_class(701)["id"]
    # Oraliq to'lgan: ikkinchi o'quvchi ID'siz qoladi — sinf har ochilganda ID berish tekshiriladi
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1001, "end": 1001}, headers=h)
    client.post(f"/api/classes/{cid}/students/bulk", json={"names": ["Ali Valiyev", "Zuhra Karimova"]}, headers=h)
    gone = client.post("/api/classes", json={"name": "Yopilgan"}, headers=h).json()["id"]
    client.put(f"/api/classes/{gone}/student-codes", json={"start": 1101, "end": 1110}, headers=h)
    client.post(f"/api/classes/{gone}/students/bulk", json={"names": ["Eski Bola", "Eski Qiz"]}, headers=h)
    assert client.delete(f"/api/classes/{gone}", headers=h).status_code == 200
    client.get(f"/api/classes/{cid}", headers=h)  # birinchi ochilish — kerakli tozalash bo'lishi mumkin

    writes = []

    def count(conn, cursor, statement, *args):
        if statement.lstrip().upper().startswith(("UPDATE", "INSERT", "DELETE")):
            writes.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        for _ in range(3):
            view = client.get(f"/api/classes/{cid}", headers=h).json()
    finally:
        event.remove(engine, "before_cursor_execute", count)
    assert writes == [], writes
    assert sorted((s["code"] or 0) for s in view["students"]) == [0, 1001]


def test_maktabga_qoshilgan_oqituvchi_avtomatik_test_markazida_royxatdan_otadi():
    """Havola/kod orqali maktabga qo'shilgan odam sinfda sinov yaratishi uchun alohida ro'yxatdan o'tmaydi."""
    reset_database()
    _school_with_class(701)
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    _set_name(704, "Yangi", "Ustoz")
    _set_name(705, "Begona", "Odam")

    # Maktabga qo'shilmagan odam — o'qituvchi emas
    assert client.get("/api/me", headers=as_user(705)).json()["is_teacher"] is False
    assert client.get("/api/teacher/tests", headers=as_user(705)).status_code == 403

    joined = client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 04"},
                         headers=as_user(704))
    assert joined.status_code == 200, joined.text
    me = client.get("/api/me", headers=as_user(704)).json()
    assert me["is_teacher"] is True, me
    # Test yarata oladi (sinovni sinfga biriktirib)
    assert create_test(704, enrollment_type="closed")["id"]
    # Maktab egasi ham — ro'yxatdan o'tmagan bo'lsa ham o'qituvchi
    assert client.get("/api/me", headers=as_user(701)).json()["is_teacher"] is True


def test_bosh_menyu_mening_maktabim():
    """/api/home: bugungi sana va "Mening maktabim" — a'zo bo'lgan muassasalari, lavozimi va kuratorlik sinfi."""
    from backend.database import User

    reset_database()
    # Hech qayerga a'zo emas — ro'yxat bo'sh
    empty = client.get("/api/home", headers=as_user(950)).json()
    assert empty["places"] == [] and empty["today"] and empty["weekday_label"], empty

    cid = _school_with_class(701)["id"]
    with SessionLocal() as session:
        owner_id = session.scalar(main.select(User.id).where(User.tg_id == 701))
    assert client.put(f"/api/classes/{cid}/curator", json={"user_id": owner_id}, headers=as_user(701)).status_code == 200
    place = client.get("/api/home", headers=as_user(701)).json()["places"][0]
    assert (place["kind"], place["name"], place["role"], place["classes"], place["curated"]) == ("school", "Maktab 701", "owner", 1, ["10/2"])

    # O'chirilgan muassasa "Mening maktabim"da ko'rinmaydi
    school = client.get("/api/schools/me", headers=as_user(701)).json()["school"]
    deleted = client.post(f"/api/admin/schools/{school['id']}/delete", json={"confirm": "Maktab 701"}, headers=as_user(4242))
    assert deleted.status_code == 200, deleted.text
    assert client.get("/api/home", headers=as_user(701)).json()["places"] == []


def test_sinf_royxati_familiya_boyicha_alifbo_tartibida():
    """Sinf ro'yxati va davomat — familiya bo'yicha o'zbek alifbosi tartibida (apostrof turi, katta-kichik harf farq qilmaydi)."""
    reset_database()
    cid = _school_with_class(701)["id"]
    names = ("Shavkatov Husanjon", "Tohirov Husanboy", "Jo`rayev Ziyodjon", "G‘aniyev Bobur", "abdurahmonov Xudoyberdi",
             "Chorshanbiyev Ali", "Bahromov Asadxo'ja", "O‘ktamov Sardor", "Zokirov Jasur", "Sobirova Gulsevar",
             "Abdufattoyev Rashidbek", "Ma’rufov Aziz")
    for name in names:
        added = client.post(f"/api/classes/{cid}/students", json={"full_name": name}, headers=as_user(701))
        assert added.status_code == 200, added.text
    # O'zbek alifbosi: ... Y, Z, O‘, G‘, Sh, Ch (jurnaldagidek); tutuq belgisi (Ma’rufov) hisobga olinmaydi
    expected = ["Abdufattoyev Rashidbek", "abdurahmonov Xudoyberdi", "Bahromov Asadxo'ja", "Jo`rayev Ziyodjon",
                "Ma’rufov Aziz", "Sobirova Gulsevar", "Tohirov Husanboy", "Zokirov Jasur", "O‘ktamov Sardor",
                "G‘aniyev Bobur", "Shavkatov Husanjon", "Chorshanbiyev Ali"]
    students = client.get(f"/api/classes/{cid}", headers=as_user(701)).json()["students"]
    assert [student["full_name"] for student in students] == expected, students

    taken = client.post(f"/api/classes/{cid}/attendance", json={"subject": "Matematika"}, headers=as_user(701))
    assert taken.status_code == 200, taken.text
    assert [mark["name"] for mark in taken.json()["marks"]] == expected


def test_fan_guruhlari_ingliz_tili_darajalari():
    """
    Fan guruhi (masalan ingliz tili darajalari): admin ochadi, guruh o'qituvchisi o'quvchini maktab ro'yxatidan
    qo'shadi (yotoqxonadagidek). O'quvchi o'z sinfida qoladi; bir fanda — bitta guruh. Guruhda davomat sinfdagidek;
    ism, ID va holat asl o'quvchidan; to'lov, qidiruv va o'quvchilar sonida ikki marta chiqmaydi.
    """
    from backend.database import User

    reset_database()
    owner = as_user(701)
    cid = _school_with_class(701)["id"]  # 10/2
    other = client.post("/api/classes", json={"name": "9-A"}, headers=owner).json()["id"]
    school = client.get("/api/schools/me", headers=owner).json()["school"]
    _set_name(704, "Ingliz", "Ustoz")
    client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Ingliz tili"], "phone": "93 444 55 04"},
                headers=as_user(704))
    with SessionLocal() as session:
        teacher_id = session.scalar(main.select(User.id).where(User.tg_id == 704))
    ids = {}
    for class_id, names in ((cid, ("Valiyev Ali", "Karimova Zuhra")), (other, ("Sobirov Bek",))):
        for name in names:
            view = client.post(f"/api/classes/{class_id}/students", json={"full_name": name}, headers=owner).json()
            ids[name] = next(s["id"] for s in view["students"] if s["full_name"] == name)

    # Guruhni faqat admin/direktor ochadi
    body = {"name": "Ingliz tili — A1", "subject": "Ingliz tili"}
    assert client.post("/api/classes", json=body, headers=as_user(704)).status_code == 403
    a1 = client.post("/api/classes", json=body, headers=owner).json()
    assert a1["is_group"] is True and a1["subject"] == "Ingliz tili", a1
    b1 = client.post("/api/classes", json={"name": "Ingliz tili — B1", "subject": "Ingliz tili"}, headers=owner).json()
    for group in (a1, b1):
        assert client.put(f"/api/classes/{group['id']}/curator", json={"user_id": teacher_id}, headers=owner).status_code == 200

    # Guruh o'qituvchisi: nomzodlar — maktabning hamma o'quvchisi (sinfi bilan); oddiy sinfga bunday qo'shib bo'lmaydi
    teacher = as_user(704)
    candidates = client.get(f"/api/classes/{a1['id']}/candidates", headers=teacher).json()
    assert sorted((c["name"], c["class_name"], c["group"]) for c in candidates) == [
        ("Karimova Zuhra", "10/2", None), ("Sobirov Bek", "9-A", None), ("Valiyev Ali", "10/2", None)], candidates
    assert client.get(f"/api/classes/{cid}/candidates", headers=owner).status_code == 400
    added = client.post(f"/api/classes/{a1['id']}/members", json={"student_ids": [ids["Valiyev Ali"], ids["Sobirov Bek"]]},
                        headers=teacher)
    assert added.status_code == 200, added.text
    assert added.json()["added"] == 2 and added.json()["moved"] == 0
    members = {s["full_name"]: s for s in added.json()["class"]["students"]}
    assert {name: s["from_class"] for name, s in members.items()} == {"Sobirov Bek": "9-A", "Valiyev Ali": "10/2"}
    # Ism bilan yangi o'quvchi guruhga qo'shilmaydi; boshqa o'qituvchi guruhni boshqara olmaydi
    assert client.post(f"/api/classes/{a1['id']}/students", json={"full_name": "Yangi Bola"}, headers=teacher).status_code == 422
    assert client.post(f"/api/classes/{a1['id']}/members", json={"student_ids": [ids["Karimova Zuhra"]]},
                       headers=as_user(999)).status_code in (403, 404)

    # Bir fanda — bitta guruh: B1 ga qo'shilsa, A1 dan chiqadi
    moved = client.post(f"/api/classes/{b1['id']}/members", json={"student_ids": [ids["Valiyev Ali"]]}, headers=teacher).json()
    assert moved["added"] == 1 and moved["moved"] == 1, moved
    a1_view = client.get(f"/api/classes/{a1['id']}", headers=teacher).json()
    assert [s["full_name"] for s in a1_view["students"]] == ["Sobirov Bek"]
    candidates = {c["name"]: c["group"] for c in client.get(f"/api/classes/{a1['id']}/candidates", headers=teacher).json()}
    assert candidates == {"Karimova Zuhra": None, "Valiyev Ali": "Ingliz tili — B1"}, candidates

    # Davomat — sinfdagidek
    taken = client.post(f"/api/classes/{b1['id']}/attendance", json={"subject": "Ingliz tili"}, headers=teacher)
    assert taken.status_code == 200, taken.text
    assert [mark["name"] for mark in taken.json()["marks"]] == ["Valiyev Ali"]

    # Ism va ID — asl o'quvchidan; guruhdagi yozuv o'zi tahrirlanmaydi
    copy_id = client.get(f"/api/classes/{b1['id']}", headers=teacher).json()["students"][0]["id"]
    assert client.put(f"/api/classes/{b1['id']}/students/{copy_id}", json={"full_name": "Boshqa Ism"}, headers=owner).status_code == 422
    assert client.put(f"/api/classes/{cid}/students/{ids['Valiyev Ali']}", json={"full_name": "Valiyev Alisher"},
                      headers=owner).status_code == 200
    assert client.put(f"/api/classes/{cid}/student-codes", json={"start": 1001, "end": 1030}, headers=owner).status_code == 200
    source = next(s for s in client.get(f"/api/classes/{cid}", headers=owner).json()["students"] if s["id"] == ids["Valiyev Ali"])
    copy = client.get(f"/api/classes/{b1['id']}", headers=teacher).json()["students"][0]
    assert (copy["full_name"], copy["code"]) == ("Valiyev Alisher", source["code"]) and source["code"], (copy, source)

    # Holat guruhda belgilansa ham asl o'quvchiga yoziladi — sinfda ham ko'rinadi
    assert client.put(f"/api/classes/{b1['id']}/students/{copy_id}/status", json={"status": "sick"}, headers=teacher).status_code == 200
    assert next(s for s in client.get(f"/api/classes/{cid}", headers=owner).json()["students"]
                if s["id"] == ids["Valiyev Ali"])["away"] == "sick"

    # Qidiruv, o'quvchilar soni, to'lov — guruh nusxasisiz
    found = client.get("/api/schools/students/search", params={"q": "valiyev"}, headers=owner).json()["students"]
    assert [s["class_name"] for s in found] == ["10/2"], found
    assert client.put("/api/schools/fee", json={"monthly_fee": 300000}, headers=owner).status_code == 200
    overview = client.get("/api/schools/overview", headers=owner).json()
    assert overview["stats"]["students"] == 3 and overview["stats"]["classes"] == 2, overview["stats"]
    assert {item["name"] for item in overview["payment_classes"]} == {"10/2", "9-A"}
    summary = client.get("/api/schools/payments/summary", headers=owner).json()
    assert {row["name"] for row in summary["classes"]} == {"10/2", "9-A"}
    assert client.get(f"/api/classes/{b1['id']}/payments", headers=owner).json()["monthly_fee"] == 0

    # Sinfdan chiqarilsa — guruhdan ham
    assert client.request("DELETE", f"/api/classes/{cid}/students/{ids['Valiyev Ali']}", json={"reason": "Boshqa maktabga o'tdi"}, headers=owner).status_code == 200
    assert client.get(f"/api/classes/{b1['id']}", headers=teacher).json()["students"] == []


    # Yordamchi o'qituvchi: faqat fan guruhiga, admin tayinlaydi; huquqlari asosiy o'qituvchinikidek
    _set_name(705, "Yordamchi", "Ustoz")
    client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Ingliz tili"], "phone": "93 444 55 05"},
                headers=as_user(705))
    with SessionLocal() as session:
        helper_id = session.scalar(main.select(User.id).where(User.tg_id == 705))
    helper = as_user(705)
    assert client.post(f"/api/classes/{a1['id']}/members", json={"student_ids": [ids["Karimova Zuhra"]]}, headers=helper).status_code == 403
    assert client.put(f"/api/classes/{cid}/assistant", json={"user_id": helper_id}, headers=owner).status_code == 400
    assert client.put(f"/api/classes/{a1['id']}/assistant", json={"user_id": helper_id}, headers=teacher).status_code == 403
    assert client.put(f"/api/classes/{a1['id']}/assistant", json={"user_id": teacher_id}, headers=owner).status_code == 422
    assigned = client.put(f"/api/classes/{a1['id']}/assistant", json={"user_id": helper_id}, headers=owner).json()
    assert assigned["assistant"]["name"] == "Yordamchi Ustoz" and assigned["curator"]["user_id"] == teacher_id, assigned
    assert client.get(f"/api/classes/{a1['id']}", headers=helper).json()["is_assistant"] is True
    joined = client.post(f"/api/classes/{a1['id']}/members", json={"student_ids": [ids["Karimova Zuhra"]]}, headers=helper)
    assert joined.status_code == 200 and joined.json()["added"] == 1, joined.text
    assert client.post(f"/api/classes/{a1['id']}/attendance", json={"subject": "Ingliz tili"}, headers=helper).status_code == 200
    # Asosiy o'qituvchi qilib tayinlansa — yordamchilikdan olinadi
    promoted = client.put(f"/api/classes/{a1['id']}/curator", json={"user_id": helper_id}, headers=owner).json()
    assert promoted["curator"]["user_id"] == helper_id and promoted["assistant"] is None, promoted


def test_versiyali_fayl_faqat_mos_versiyada_keshlanadi():
    """
    Yangilanish paytida yangi versiya havolasi eski serverga tushib, eski fayl bir yilga keshlanib qolmasin:
    so'ralgan versiya serverdagisiga mos kelsagina — "immutable"; aks holda — no-store. Javobda serverning
    o'z versiyasi (X-Asset-Version) — sw.js mos kelmaganini keshga yozmaydi.
    """
    version = main._asset_version()
    html = client.get("/").text
    assert f'/js/app.js?v={version}"' in html and f'/css/style.css?v={version}"' in html

    good = client.get(f"/js/app.js?v={version}")
    assert good.status_code == 200
    assert good.headers["cache-control"] == "public, max-age=31536000, immutable"
    assert good.headers["x-asset-version"] == version

    stale = client.get("/css/style.css?v=0000")
    assert stale.status_code == 200 and stale.headers["cache-control"] == "no-store", stale.headers
    assert stale.headers["x-asset-version"] == version
    # Versiyasiz fayl (sw.js) — versiya aytilmaydi, avvalgidek har safar tekshiriladi
    sw = client.get("/sw.js")
    assert "x-asset-version" not in sw.headers and sw.headers["cache-control"] == "no-cache"


def test_biriktirilmaganlar_savati():
    """
    Sinfdan chiqarishda sabab majburiy; o'quvchi "Biriktirilmaganlar"ga tushadi. Rahbariyat uni boshqa sinfga
    biriktiradi (yangi yozuv), eski sinfiga qaytaradi (tarixi bilan tiklanadi) yoki o'chiradi.
    """
    reset_database()
    owner = as_user(701)
    cid = _school_with_class(701)["id"]  # 10/2
    other = client.post("/api/classes", json={"name": "9-A"}, headers=owner).json()["id"]
    school = client.get("/api/schools/me", headers=owner).json()["school"]
    _set_name(704, "Oddiy", "Ustoz")
    client.post("/api/schools/join", json={"code": current_code(school), "subjects": ["Fizika"], "phone": "93 444 55 04"},
                headers=as_user(704))
    view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Valiyev Ali"}, headers=owner).json()
    view = client.post(f"/api/classes/{cid}/students", json={"full_name": "Karimova Zuhra"}, headers=owner).json()
    ids = {s["full_name"]: s["id"] for s in view["students"]}

    def remove(class_id, student_id, reason=None):
        body = {"reason": reason} if reason is not None else None
        return client.request("DELETE", f"/api/classes/{class_id}/students/{student_id}", json=body, headers=owner)

    # Sababsiz (yoki juda qisqa sabab bilan) chiqarib bo'lmaydi
    assert remove(cid, ids["Valiyev Ali"]).status_code == 422
    assert remove(cid, ids["Valiyev Ali"], "ab").status_code == 422
    assert remove(cid, ids["Valiyev Ali"], "Boshqa maktabga o'tdi").status_code == 200

    # Ro'yxat — rahbariyatga; sababi va kim chiqargani bilan
    assert client.get("/api/schools/unassigned", headers=as_user(704)).status_code == 403
    basket = client.get("/api/schools/unassigned", headers=owner).json()
    assert [(s["full_name"], s["class_name"], s["reason"], s["removed_by"]) for s in basket["students"]] == [
        ("Valiyev Ali", "10/2", "Boshqa maktabga o'tdi", "Aziza Karimova")], basket
    assert sorted(item["name"] for item in basket["classes"]) == ["10/2", "9-A"]
    assert client.get("/api/schools/overview", headers=owner).json()["unassigned"] == 1

    # Boshqa sinfga — u yerda yangi yozuv; ro'yxatdan chiqadi
    moved = client.post(f"/api/schools/unassigned/{ids['Valiyev Ali']}/assign", json={"class_id": other}, headers=owner).json()
    assert moved["class_name"] == "9-A" and moved["restored"] is False and moved["student_id"] != ids["Valiyev Ali"], moved
    assert [s["full_name"] for s in client.get(f"/api/classes/{other}", headers=owner).json()["students"]] == ["Valiyev Ali"]
    assert [s["full_name"] for s in client.get(f"/api/classes/{cid}", headers=owner).json()["students"]] == ["Karimova Zuhra"]
    assert client.get("/api/schools/unassigned", headers=owner).json()["students"] == []

    # Eski sinfiga qaytarilsa — o'sha yozuv tiklanadi (tarixi bilan)
    assert remove(cid, ids["Karimova Zuhra"], "Vaqtincha ketdi").status_code == 200
    back = client.post(f"/api/schools/unassigned/{ids['Karimova Zuhra']}/assign", json={"class_id": cid}, headers=owner).json()
    assert back["restored"] is True and back["student_id"] == ids["Karimova Zuhra"], back
    assert [s["id"] for s in client.get(f"/api/classes/{cid}", headers=owner).json()["students"]] == [ids["Karimova Zuhra"]]

    # O'chirish — ro'yxatdan butunlay olinadi
    assert remove(other, moved["student_id"], "O'qishni tashladi").status_code == 200
    assert client.post(f"/api/schools/unassigned/{moved['student_id']}/delete", headers=as_user(704)).status_code == 403
    assert client.post(f"/api/schools/unassigned/{moved['student_id']}/delete", headers=owner).status_code == 200
    assert client.get("/api/schools/unassigned", headers=owner).json()["students"] == []
    assert client.post(f"/api/schools/unassigned/{moved['student_id']}/delete", headers=owner).status_code == 404

    # Fan guruhidan chiqarish — sababsiz, ro'yxatga tushmaydi (o'quvchi o'z sinfida qoladi)
    group = client.post("/api/classes", json={"name": "Ingliz tili — A1", "subject": "Ingliz tili"}, headers=owner).json()
    added = client.post(f"/api/classes/{group['id']}/members", json={"student_ids": [ids["Karimova Zuhra"]]}, headers=owner).json()
    copy_id = added["class"]["students"][0]["id"]
    assert client.delete(f"/api/classes/{group['id']}/students/{copy_id}", headers=owner).status_code == 200
    assert client.get("/api/schools/unassigned", headers=owner).json()["students"] == []


def test_video_darslar_rahbariyat_qoshadi_hamma_korad():
    """Video dars: muassasa rahbariyati YouTube havolasi bilan qo'shadi, ilovadagi hamma ko'radi, admin o'chira oladi."""
    from backend.database import youtube_video_id

    assert youtube_video_id("https://youtu.be/dQw4w9WgXcQ?si=abc") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=10s") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://m.youtube.com/watch?feature=share&v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert youtube_video_id("https://example.com/video") is None

    reset_database()
    _set_name(701, "Aziza", "Karimova")
    _set_name(801, "Oddiy", "Oquvchi")
    school = client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri",
                                               "district": "Chilonzor tumani", "name": "Ilm maktabi"}, headers=as_user(701))
    assert school.status_code == 200, school.text

    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Kasrlar", "subject": "Matematika", "grade": 5,
            "description": "Kasrlarni qo'shish"}
    # Maktabga a'zo bo'lmagan foydalanuvchi qo'sha olmaydi; noto'g'ri havola — 422
    assert client.post("/api/videos", json=body, headers=as_user(801)).status_code == 403
    assert client.post("/api/videos", json={**body, "url": "https://example.com/x"}, headers=as_user(701)).status_code == 422

    created = client.post("/api/videos", json=body, headers=as_user(701))
    assert created.status_code == 200, created.text
    video = created.json()
    assert video["youtube_id"] == "dQw4w9WgXcQ" and video["school_name"] == "Ilm maktabi"
    assert client.post("/api/videos", json=body, headers=as_user(701)).status_code == 409  # ikki marta emas
    client.post("/api/videos", json={**body, "url": "https://youtu.be/aaaaaaaaaaa", "title": "Tarix 1",
                                     "subject": "Tarix", "grade": None, "description": ""}, headers=as_user(701))

    # Hozircha faqat maktab o'quvchilari ko'radi (login-parol bilan, /api/pupil/videos) — oddiy foydalanuvchi emas
    assert client.get("/api/videos", headers=as_user(801)).status_code == 403
    cid = client.post("/api/classes", json={"name": "5-A"}, headers=as_user(701)).json()["id"]
    client.post(f"/api/classes/{cid}/students", json={"full_name": "Aliyev Vali"}, headers=as_user(701))
    access = client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(701)).json()["students"][0]
    main._pupil_ip_failures.clear()
    token = client.post("/api/pupil/login", json={"school_id": school.json()["school"]["id"], "login": access["login"],
                                                  "password": access["password"]}).json()["token"]
    pupil = {"X-Pupil-Session": token}
    assert client.get("/api/pupil/videos").status_code == 401

    # Fan va sinf bo'yicha saralanadi (sinfsiz video — hamma sinflarga)
    seen = client.get("/api/pupil/videos", headers=pupil).json()
    assert {item["name"] for item in seen["subjects"]} == {"Matematika", "Tarix"}
    assert len(seen["videos"]) == 2 and not seen["can_add"] and not seen["videos"][0]["can_manage"]
    assert [item["title"] for item in client.get("/api/pupil/videos?subject=Matematika", headers=pupil).json()["videos"]] == ["Kasrlar"]
    assert len(client.get("/api/pupil/videos?grade=7", headers=pupil).json()["videos"]) == 1  # faqat sinfsiz "Tarix 1"
    assert [item["title"] for item in client.get("/api/pupil/videos?q=qo'shish", headers=pupil).json()["videos"]] == ["Kasrlar"]
    assert client.post(f"/api/videos/{video['id']}/view").status_code == 200

    # Boshqa odam o'zgartira olmaydi; rahbariyat tahrirlaydi; platforma admini o'chira oladi
    assert client.delete(f"/api/videos/{video['id']}", headers=as_user(801)).status_code == 403
    edited = client.put(f"/api/videos/{video['id']}", json={**body, "title": "Kasrlar (1-dars)"}, headers=as_user(701))
    assert edited.status_code == 200 and edited.json()["title"] == "Kasrlar (1-dars)"
    assert client.delete(f"/api/videos/{video['id']}", headers=as_user(4242)).status_code == 200
    left = client.get("/api/videos", headers=as_user(701)).json()
    assert [item["title"] for item in left["videos"]] == ["Tarix 1"] and left["can_add"]
    assert left["videos"][0]["views"] == 0 and client.get("/api/videos", headers=as_user(4242)).status_code == 200


def test_maktab_oquvchisi_login_parol_bilan_oz_kabinetiga_kiradi():
    """O'quvchi maktabini topadi, maktab bergan login-parol bilan kiradi, o'z parolini o'rnatadi va faqat o'zinikini ko'radi."""
    reset_database()
    main._pupil_ip_failures.clear()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Boshqa", "Odam")
    client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri", "district": "Chilonzor tumani",
                                      "name": "Ilm maktabi"}, headers=as_user(701))
    cid = client.post("/api/classes", json={"name": "7-A"}, headers=as_user(701)).json()["id"]
    client.put(f"/api/classes/{cid}/student-codes", json={"start": 1030, "end": 1040}, headers=as_user(701))
    for name in ("Aliyev Vali", "Karimova Zuhra"):
        client.post(f"/api/classes/{cid}/students", json={"full_name": name}, headers=as_user(701))

    # Kurator/rahbariyat ro'yxatni oladi; begona odam — yo'q
    assert client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(702)).status_code in (403, 404)
    logins = client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(701)).json()
    vali = next(item for item in logins["students"] if item["name"] == "Aliyev Vali")
    assert vali["login"] == str(vali["code"]) and len(vali["password"]) == 6 and not vali["changed"]
    again = client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(701)).json()
    assert next(item for item in again["students"] if item["id"] == vali["id"])["password"] == vali["password"]  # o'zgarmaydi
    assert client.get(f"/api/classes/{cid}/pupil-logins/file", headers=as_user(701)).content[:4] == b"%PDF"

    # Maktabni topish (Telegram shart emas)
    found = client.get("/api/pupil/schools", params={"q": "ilm"}).json()
    school_id = found[0]["id"]
    assert found[0]["name"] == "Ilm maktabi" and client.get("/api/pupil/schools", params={"q": "x"}).json() == []

    login = {"school_id": school_id, "login": vali["login"], "password": vali["password"]}
    assert client.post("/api/pupil/login", json={**login, "password": "000000" if vali["password"] != "000000" else "111111"}).status_code == 401
    session = client.post("/api/pupil/login", json=login).json()
    assert session["must_change"] and session["name"] == "Aliyev Vali" and session["class_name"] == "7-A"
    pupil = {"X-Pupil-Session": session["token"]}

    me = client.get("/api/pupil/me", headers=pupil).json()
    assert me["name"] == "Aliyev Vali" and me["must_change"] and me["rating"]["class_size"] == 2
    assert client.get("/api/pupil/me").status_code == 401

    # O'z parolini o'rnatadi — maktab bergani endi ishlamaydi
    assert client.post("/api/pupil/password", json={"old_password": "xato", "new_password": "yangi123"}, headers=pupil).status_code == 400
    assert client.post("/api/pupil/password", json={"old_password": vali["password"], "new_password": "yangi123"},
                       headers=pupil).status_code == 200
    assert client.post("/api/pupil/login", json=login).status_code == 401
    assert client.post("/api/pupil/login", json={**login, "password": "yangi123"}).status_code == 200
    listed = client.get(f"/api/classes/{cid}/pupil-logins", headers=as_user(701)).json()["students"]
    assert next(item for item in listed if item["id"] == vali["id"]) ["password"] is None

    # 5 marta xato — vaqtincha yopiladi (to'g'ri parol bilan ham)
    for _ in range(5):
        client.post("/api/pupil/login", json={**login, "password": "noto'g'ri"})
    assert client.post("/api/pupil/login", json={**login, "password": "yangi123"}).status_code == 429

    # Kurator yangi parol beradi: qulf ochiladi, eski seanslar tugaydi
    reset = client.post(f"/api/classes/{cid}/students/{vali['id']}/pupil-password", headers=as_user(701)).json()
    assert reset["password"] and not reset["changed"]
    assert client.get("/api/pupil/me", headers=pupil).status_code == 401
    fresh = client.post("/api/pupil/login", json={**login, "password": reset["password"]})
    assert fresh.status_code == 200, fresh.text

    # Sinfdan chiqarilgan o'quvchi kira olmaydi
    client.request("DELETE", f"/api/classes/{cid}/students/{vali['id']}", json={"reason": "Boshqa maktabga o'tdi"},
                   headers=as_user(701))
    assert client.get("/api/pupil/me", headers={"X-Pupil-Session": fresh.json()["token"]}).status_code == 401
    assert client.post("/api/pupil/login", json={**login, "password": reset["password"]}).status_code == 403


def test_tolov_kalitlari_admin_panelidan_kiritiladi():
    """
    Payme/Click kalitlari admin panelidan kiritiladi va darhol kuchga kiradi.

    Eng muhimi: maxfiy kalit javobda ham, zaxira nusxada ham ko'rinmasligi kerak —
    aks holda u Telegramga yuboriladigan JSON faylga tushib ketardi.
    """
    import json as _json

    import backend.main as main
    from backend.database import AppSetting

    reset_database()
    admin = as_user(4242)

    # Kalitni kiritish
    saved = client.post(
        "/api/admin/payment-settings",
        headers=admin,
        json={"payme_merchant_id": "65abc123", "payme_secret_key": "SIR-KALIT", "payme_test_mode": True},
    )
    assert saved.status_code == 200, saved.text
    state = saved.json()
    assert state["payme"]["merchant_id"] == "65abc123"
    assert state["payme"]["enabled"] and state["payme"]["test_mode"]
    assert state["payme"]["merchant_source"] == "db"
    # Maxfiy kalitning o'zi qaytarilmaydi — faqat "qo'yilgan" degan belgi
    assert state["payme"]["secret_set"] is True
    assert "SIR-KALIT" not in _json.dumps(state)
    assert main.PAYME_MERCHANT_ID == "65abc123" and main.PAYME_SECRET_KEY == "SIR-KALIT"
    # To'lov havolasi yangi kalit bilan, sinov rejimida yasaladi
    assert main._build_payme_checkout_url("payment_id", 7, 5000).startswith("https://test.paycom.uz/")

    # Bo'sh yuborilgan maxfiy kalit — "o'zgarishsiz qoldir" degani
    again = client.post("/api/admin/payment-settings", headers=admin, json={"payme_merchant_id": "65abc123"}).json()
    assert again["payme"]["secret_set"] is True
    assert main.PAYME_SECRET_KEY == "SIR-KALIT"

    # Zaxira nusxaga maxfiy kalit TUSHMAYDI
    with SessionLocal() as session:
        backup = main.export_database(session).decode("utf-8")
    assert "SIR-KALIT" not in backup
    saved_settings = _json.loads(backup)["tables"].get(AppSetting.__tablename__, [])
    assert not any(row["key"] in main.PAYMENT_SETTING_KEYS for row in saved_settings)

    # O'chirilsa — bazadagi qiymat bo'shaydi (.env'dagisi qoladi)
    cleared = client.post(
        "/api/admin/payment-settings",
        headers=admin,
        json={"clear": ["payme_merchant_id", "payme_secret_key", "payme_test_mode"]},
    )
    assert cleared.status_code == 200, cleared.text
    with SessionLocal() as session:
        assert session.get(AppSetting, "payme_secret_key").value == ""

    # Begona odam na ko'ra oladi, na o'zgartira oladi
    assert client.get("/api/admin/payment-settings", headers=as_user(1111)).status_code == 403
    assert client.post("/api/admin/payment-settings", headers=as_user(1111), json={}).status_code == 403


def test_video_davomiyligi_va_bosh_sahifadagi_karta():
    """
    Dars davomiyligi: pleyer bir marta xabar beradi, keyin hammaga ko'rinadi va
    qayta yozilmaydi. Bosh sahifadagi "Video darslar" kartasi esa faqat
    muassasaga a'zo bo'lganlarga (darslar soni bilan) qaytariladi.
    """
    reset_database()
    _set_name(701, "Aziza", "Karimova")
    _set_name(801, "Oddiy", "Oquvchi")
    client.post("/api/schools", json={"subjects": ["Matematika"], "region": "Toshkent shahri",
                                      "district": "Chilonzor tumani", "name": "Ilm maktabi"}, headers=as_user(701))
    video = client.post("/api/videos", json={"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Kasrlar",
                                             "subject": "Matematika", "grade": 5, "description": ""},
                        headers=as_user(701)).json()
    assert video["duration"] is None

    # Pleyer davomiylikni yuboradi (kirish talab qilinmaydi — Telegram'siz o'quvchi ham ko'radi)
    assert client.post(f"/api/videos/{video['id']}/duration?seconds=760").status_code == 200
    # Ikkinchi marta yozilmaydi — birinchi o'lchov qoladi
    client.post(f"/api/videos/{video['id']}/duration?seconds=99")
    listed = client.get("/api/videos", headers=as_user(701)).json()["videos"][0]
    assert listed["duration"] == 760

    # Aqlga sig'maydigan qiymat rad etiladi
    assert client.post(f"/api/videos/{video['id']}/duration?seconds=0").status_code == 400
    assert client.post(f"/api/videos/{video['id']}/duration?seconds=999999").status_code == 400

    # Bosh sahifa: maktab a'zosiga darslar soni, begonaga — nol
    assert client.get("/api/home", headers=as_user(701)).json()["videos"] == 1
    assert client.get("/api/home", headers=as_user(801)).json()["videos"] == 0

    # Mustahkamlash testining kodi: kichik harf va bo'shliq tozalanadi, noto'g'risi rad etiladi
    body = {"url": "https://youtu.be/dQw4w9WgXcQ", "title": "Kasrlar", "subject": "Matematika",
            "grade": 5, "description": "", "test_code": " gy68 "}
    edited = client.put(f"/api/videos/{video['id']}", json=body, headers=as_user(701))
    assert edited.status_code == 200, edited.text
    assert edited.json()["test_code"] == "GY68"
    assert client.put(f"/api/videos/{video['id']}", json={**body, "test_code": "a!"},
                      headers=as_user(701)).status_code == 422
    # Kod bo'sh qoldirilsa — karta ko'rsatilmaydi
    assert client.put(f"/api/videos/{video['id']}", json={**body, "test_code": ""},
                      headers=as_user(701)).json()["test_code"] == ""


# ========== Oylik obuna (token o'rniga cheksiz tekshiruv) ==========


def _set_subscription_prices(prices: dict) -> dict:
    response = client.post("/api/admin/subscription-settings", json={"prices": prices}, headers=as_user(4242))
    assert response.status_code == 200, response.text
    return response.json()


def _set_teacher(tg_id: int = 555, **values) -> None:
    from backend.database import User

    session = SessionLocal()
    try:
        teacher = session.scalars(
            main.select(Teacher).join(User, User.id == Teacher.user_id).where(User.tg_id == tg_id)
        ).one()
        for name, value in values.items():
            setattr(teacher, name, value)
        session.commit()
    finally:
        session.close()


def _subscription_days_left(tg_id: int = 555) -> float:
    from datetime import datetime

    until = client.get("/api/teacher/balance", headers=as_user(tg_id)).json()["subscription_until"]
    if not until:
        return 0
    return (datetime.fromisoformat(until) - main.utcnow()).total_seconds() / 86400


def test_oylik_obuna_narxlarini_admin_belgilaydi():
    """Narxni admin belgilaydi; narxi 0 bo'lgan muddat o'qituvchiga ko'rsatilmaydi va sotilmaydi."""
    reset_database()
    register_teacher()

    # Faqat admin
    assert client.post("/api/admin/subscription-settings", json={"prices": {"1": 1}},
                       headers=as_user(555)).status_code == 403
    # Bunday muddat yo'q / manfiy narx — rad etiladi
    assert client.post("/api/admin/subscription-settings", json={"prices": {"2": 5000}},
                       headers=as_user(4242)).status_code == 422
    assert client.post("/api/admin/subscription-settings", json={"prices": {"1": -5}},
                       headers=as_user(4242)).status_code == 422

    saved = _set_subscription_prices({"1": 99_000, "3": 0, "12": 900_000})
    assert {plan["months"]: plan["price"] for plan in saved["plans"]} == {1: 99_000, 3: 0, 12: 900_000}

    info = client.get("/api/teacher/balance", headers=as_user(555)).json()
    assert [(plan["months"], plan["price"]) for plan in info["subscription_plans"]] == [(1, 99_000), (12, 900_000)]
    assert info["subscription_active"] is False and info["subscription_until"] is None

    # Sotilmaydigan muddat, ikkalasi birga yoki hech biri — rad etiladi
    assert client.post("/api/teacher/credit-purchases", json={"months": 3}, headers=as_user(555)).status_code == 409
    assert client.post("/api/teacher/credit-purchases", json={"months": 1, "credits": 10},
                       headers=as_user(555)).status_code == 422
    assert client.post("/api/teacher/credit-purchases", json={}, headers=as_user(555)).status_code == 422

    # Narxlar zaxira nusxada saqlanadi (to'lov kalitlaridan farqli)
    with SessionLocal() as session:
        backup = main.export_database(session).decode("utf-8")
    assert "sub_price_1" in backup


def test_oylik_obuna_payme_orqali_va_token_yechilmaydi():
    """Obuna Payme to'lovidan keyin faollashadi; amal qilgan muddatda token yechilmaydi."""
    from datetime import timedelta

    reset_database()
    register_teacher()
    _set_subscription_prices({"1": 99_000, "12": 900_000})
    _set_teacher(credits=3)

    purchase = client.post("/api/teacher/credit-purchases", json={"months": 1}, headers=as_user(555)).json()
    assert purchase["kind"] == "subscription" and purchase["amount"] == 99_000, purchase
    account = {"purchase_id": str(purchase["id"])}
    amount = purchase["amount"] * 100
    assert payme("CheckPerformTransaction", {"account": account, "amount": amount})["result"]["allow"]
    payme("CreateTransaction", {"id": "tx-obuna", "time": 1, "account": account, "amount": amount})
    assert _subscription_days_left() == 0, "to'lovdan oldin obuna faollashib ketdi"

    payme("PerformTransaction", {"id": "tx-obuna"})
    assert 29.9 < _subscription_days_left() <= 30, _subscription_days_left()
    assert credits_of() == 3, "obuna tokenlarga tegmasligi kerak"

    # Obuna faol: o'quvchi natijasi ham, o'qituvchi kiritgan javob ham token olmaydi
    open_test = create_test()
    closed_test = create_test(enrollment_type="closed")
    started = client.post(f"/api/tests/{open_test['code']}/start", json={}, headers=as_user(901)).json()
    client.post(f"/api/attempts/{started['attempt_id']}/finish", json={"answers": {"1": "B"}}, headers=as_user(901))
    client.post(f"/api/teacher/tests/{closed_test['id']}/students", json={"names": ["Hasan Hasanov"]},
                headers=as_user(555))
    student_id = client.get(f"/api/teacher/tests/{closed_test['id']}", headers=as_user(555)).json()[
        "students"][0]["id"]
    answered = client.post(f"/api/teacher/tests/{closed_test['id']}/students/{student_id}/answers",
                           json={"answers": {"1": "B"}}, headers=as_user(555))
    assert answered.status_code == 200, answered.text
    assert credits_of() == 3, f"obuna faol bo'lsa ham token yechildi: {credits_of()}"

    # Token 0 bo'lsa ham obuna bilan yangi o'quvchi testni boshlay oladi
    _set_teacher(credits=0)
    assert client.post(f"/api/tests/{open_test['code']}/start", json={}, headers=as_user(902)).status_code == 200

    # Obuna tugamay turib yana 12 oy olinsa — qolgan kunlarga QO'SHILADI
    second = client.post("/api/teacher/credit-purchases", json={"months": 12}, headers=as_user(555)).json()
    account = {"purchase_id": str(second["id"])}
    payme("CreateTransaction", {"id": "tx-yillik", "time": 2, "account": account, "amount": second["amount"] * 100})
    payme("PerformTransaction", {"id": "tx-yillik"})
    assert 394.9 < _subscription_days_left() <= 395, _subscription_days_left()

    # Pul qaytarilsa — o'sha kunlar olib qo'yiladi
    payme("CancelTransaction", {"id": "tx-yillik", "reason": 5})
    assert 29.9 < _subscription_days_left() <= 30, _subscription_days_left()

    # Obuna tugadi — yana tokendan yechiladi, token yo'q bo'lsa test boshlanmaydi
    _set_teacher(subscription_until=main.utcnow() - timedelta(minutes=1))
    assert client.post(f"/api/tests/{open_test['code']}/start", json={}, headers=as_user(903)).status_code == 402


def test_oylik_obuna_karta_orqali_admin_tasdiqlaydi():
    reset_database()
    register_teacher()
    _set_subscription_prices({"3": 270_000})

    with TelegramStub() as telegram:
        response = client.post("/api/teacher/card-topups",
                               json={"months": 3, "receipt_base64": FAKE_RECEIPT}, headers=as_user(555))
        assert response.status_code == 200, response.text
        assert response.json()["amount"] == 270_000 and response.json()["kind"] == "subscription"
        # Admin chekda nima sotib olinganini ko'radi
        assert "3 oylik obuna" in telegram.receipts[0] and "270 000 so'm" in telegram.receipts[0], telegram.receipts
        assert _subscription_days_left() == 0

        purchase_id = response.json()["id"]
        listed = client.get("/api/admin/card-topups", headers=as_user(4242)).json()
        assert listed[0]["label"] == "3 oylik obuna", listed
        approved = client.post(f"/api/admin/card-topups/{purchase_id}/approve", headers=as_user(4242))

    assert approved.status_code == 200, approved.text
    assert 89.9 < _subscription_days_left() <= 90, _subscription_days_left()
    assert any("3 oylik obuna faollashdi" in text for text in telegram.teacher_messages), telegram.teacher_messages
    history = client.get("/api/teacher/credit-purchases", headers=as_user(555)).json()
    assert history[0]["kind"] == "subscription" and history[0]["months"] == 3 and history[0]["status"] == "paid"


def test_oflayn_testda_kanalga_majburiy_obuna_yoq():
    """Oflayn test sinfda, titul bilan o'tadi — kanal yozilmaydi va obuna so'ralmaydi."""
    reset_database()
    register_teacher()

    # Kanal yuborilsa ham (eski ilova) — oflayn testga yozilmaydi, bot kanalni tekshirmaydi
    created = create_test(enrollment_type="closed", required_channel="@mening_kanalim")
    detail = client.get(f"/api/teacher/tests/{created['id']}", headers=as_user(555)).json()
    assert detail["required_channels"] == [], detail["required_channels"]
    with SessionLocal() as session:
        assert session.get(main.Test, created["id"]).required_channel is None

    # Onlayn testda adminlar kanali avvalgidek majburiy
    online = create_test()
    online_detail = client.get(f"/api/teacher/tests/{online['id']}", headers=as_user(555)).json()
    assert online_detail["required_channels"] == list(main.DEFAULT_REQUIRED_CHANNELS)


def test_maktab_admini_maktabni_3_kundan_keyin_ochiradi():
    """Admin: tasdiq → maktab kodi → 3 kundan keyin o'chadi; shu orada bekor qila oladi."""
    from datetime import timedelta

    from backend.database import PrivateSchool

    reset_database()
    _set_name(701, "Aziza", "Karimova")
    _set_name(702, "Bobur", "Aliyev")
    place = {"region": "Samarqand viloyati", "district": "Urgut tumani"}
    school = client.post("/api/schools", json={"subjects": ["Matematika"], **place, "name": "Ilm maktabi"},
                         headers=as_user(701)).json()["school"]
    joined = client.post("/api/schools/join", json={"phone": "901234567", "subjects": ["Matematika"],
                                                    "code": current_code(school), "school_id": school["id"]},
                         headers=as_user(702))
    assert joined.status_code == 200, joined.text

    sent: list[tuple[int, str]] = []
    original = main._send_bot_text
    main._send_bot_text = lambda chat_id, text: sent.append((chat_id, text)) or True
    try:
        # O'qituvchi o'chira olmaydi, noto'g'ri kod bilan ham bo'lmaydi
        assert client.post("/api/schools/me/delete", json={"code": current_code(school)}, headers=as_user(702)).status_code == 403
        assert client.post("/api/schools/me/delete", json={"code": "XXXXXXXX"}, headers=as_user(701)).status_code == 403

        scheduled = client.post("/api/schools/me/delete", json={"code": current_code(school).lower()}, headers=as_user(701))
        assert scheduled.status_code == 200, scheduled.text
        assert scheduled.json()["school"]["delete_at"], scheduled.json()
        # O'qituvchi ham ogohlantirishni ko'radi; maktab hali ishlaydi
        teacher_view = client.get("/api/schools/me", headers=as_user(702)).json()["school"]
        assert teacher_view["delete_at"] == scheduled.json()["school"]["delete_at"]

        # Bekor qilish
        cancelled = client.post("/api/schools/me/delete/cancel", headers=as_user(701)).json()
        assert cancelled["school"]["delete_at"] is None
        assert main._delete_scheduled_schools_once() == 0

        # Qayta qo'yiladi; 3 kun o'tmaguncha o'chmaydi
        client.post("/api/schools/me/delete", json={"code": current_code(school)}, headers=as_user(701))
        assert main._delete_scheduled_schools_once() == 0
        with SessionLocal() as session:
            row = session.get(PrivateSchool, school["id"])
            row.delete_requested_at = main.utcnow() - main.SCHOOL_DELETE_DELAY - timedelta(minutes=1)
            session.commit()
        assert main._delete_scheduled_schools_once() == 1

        with SessionLocal() as session:
            assert session.get(PrivateSchool, school["id"]).deleted_at is not None
        assert client.get("/api/schools/me", headers=as_user(701)).json()["school"] is None
        assert client.get("/api/schools/me", headers=as_user(702)).json()["school"] is None
        assert any("o'chirildi" in text for _, text in sent), sent
    finally:
        main._send_bot_text = original


def _app_session_for(tg_id: int, user_agent: str) -> str:
    """Bot orqali kirish (ilova/sayt) — berilgan qurilmadan seans ochadi."""
    from backend.database import AppLogin

    token = client.post("/api/app-login").json()["token"]
    with SessionLocal() as session:
        main.confirm_app_login(session, token, tg_id, "Ilova Odam", "ilova")
        code = session.query(AppLogin).filter(AppLogin.token == token).one().code
    granted = client.post(f"/api/app-login/{token}/verify", json={"code": code}, headers={"User-Agent": user_agent})
    assert granted.status_code == 200, granted.text
    return granted.json()["session"]


def test_akkaunt_faol_qurilmalar_va_90_kunlik_muddat():
    """Akkaunt: qurilmalar ro'yxati, bittasidan / boshqa hammasidan chiqish, 90 kun faol bo'lmasa — avtomatik chiqish."""
    from datetime import timedelta

    from backend.database import AppSession

    reset_database()
    phone = _app_session_for(4242, "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 Chrome/120 Mobile TestWayApp/1.0")
    laptop = _app_session_for(4242, "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36")
    tablet = _app_session_for(4242, "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) Safari/604.1")

    account = client.get("/api/me/account", headers={"X-App-Session": phone}).json()
    assert account["via_telegram"] is False and account["idle_days"] == 90
    devices = {item["device"]: item for item in account["sessions"]}
    assert set(devices) == {"Chittak ilovasi · Android", "Chrome · Windows", "Safari · iPad"}, devices
    assert devices["Chittak ilovasi · Android"]["is_current"] is True
    assert devices["Chrome · Windows"]["is_current"] is False

    # O'qituvchi bo'lsa — token va obuna ham ko'rinadi
    assert account["is_teacher"] is False and account["credits"] is None
    client.post("/api/teacher/register", json={"first_name": "Ilova", "last_name": "Odam"}, headers={"X-App-Session": phone})
    account = client.get("/api/me/account", headers={"X-App-Session": phone}).json()
    assert account["is_teacher"] is True and account["credits"] == main.WELCOME_CREDITS, account

    # Begona odam boshqaning qurilmasini chiqara olmaydi
    other = _app_session_for(5151, "Firefox/120")
    tablet_id = devices["Safari · iPad"]["id"]
    assert client.post(f"/api/me/sessions/{tablet_id}/revoke", headers={"X-App-Session": other}).status_code == 404
    # Bitta qurilmadan chiqarish
    assert client.post(f"/api/me/sessions/{tablet_id}/revoke", headers={"X-App-Session": phone}).status_code == 200
    assert client.get("/api/me", headers={"X-App-Session": tablet}).status_code == 401

    # Boshqa hammasidan chiqish — shu qurilma qoladi
    result = client.post("/api/me/sessions/revoke-others", headers={"X-App-Session": phone}).json()
    assert result["revoked"] == 1, result
    assert client.get("/api/me", headers={"X-App-Session": laptop}).status_code == 401
    assert client.get("/api/me", headers={"X-App-Session": phone}).status_code == 200

    # 90 kundan ko'p ishlatilmagan qurilma — avtomatik chiqiladi va ro'yxatda ko'rinmaydi
    with SessionLocal() as session:
        row = session.scalar(main.select(AppSession).where(AppSession.token_hash == main.app_session_hash(phone)))
        row.last_seen_at = main.utcnow() - timedelta(days=91)
        session.commit()
    expired = client.get("/api/me", headers={"X-App-Session": phone})
    assert expired.status_code == 401 and "90" in expired.json()["detail"], expired.text

    # Telegram ichidan (seanssiz) — "Telegram" qatori, ilova seanslari ro'yxati bo'sh
    via_telegram = client.get("/api/me/account", headers=as_user(4242)).json()
    assert via_telegram["via_telegram"] is True and via_telegram["sessions"] == [], via_telegram


def test_har_qanday_test_kalitsiz_saqlanadi_onlayn_kalitsiz_boshlanmaydi():
    """Kalitni keyin kiritish mumkin (hamma turda); onlayn testni o'quvchi kalit to'liq bo'lgachgina boshlaydi."""
    reset_database()
    register_teacher()
    empty_key = [
        {"number": 1, "question_type": "single", "option_count": 4, "correct": ""},
        {"number": 2, "question_type": "single", "option_count": 4, "correct": "B"},
        {"number": 3, "question_type": "text", "correct": ""},
    ]

    # Oddiy (boshlang'ich) oflayn test — kalitsiz saqlanadi, eslatma chiqadi
    offline = create_test(enrollment_type="closed", test_type="boshlangich", key_questions=empty_key)
    detail = client.get(f"/api/teacher/tests/{offline['id']}", headers=as_user(555)).json()
    assert detail["key_missing"] == 2, detail["key_missing"]

    # Onlayn oddiy test — kalitsiz saqlanadi, lekin o'quvchi boshlay olmaydi
    online = create_test(test_type="boshlangich", key_questions=empty_key)
    blocked = client.post(f"/api/tests/{online['code']}/start", headers=as_user(900))
    assert blocked.status_code == 409 and "kalit" in blocked.json()["detail"], blocked.text

    # Kalit to'liq kiritilgach — boshlanadi
    saved = client.put(f"/api/teacher/tests/{online['id']}/key", json={"answers": {"1": "A", "3": "5"}}, headers=as_user(555))
    assert saved.status_code == 200, saved.text
    started = client.post(f"/api/tests/{online['code']}/start", headers=as_user(900))
    assert started.status_code == 200, started.text


def test_payme_order_id_va_fiskal_chek():
    """Payme: bitta account parametri (order_id), CheckPerformTransaction'da fiskal chek (MXIK, QQS)."""
    import base64 as _b64

    reset_database()
    register_teacher()
    before = credits_of()
    purchase = client.post("/api/teacher/credit-purchases", json={"credits": 50, "provider": "payme"},
                           headers=as_user(555)).json()
    # To'lov havolasida ac.order_id=c<raqam>
    encoded = purchase["checkout_url"].rsplit("/", 1)[1]
    assert f"ac.order_id=c{purchase['id']}" in _b64.b64decode(encoded).decode(), _b64.b64decode(encoded)

    account = {"order_id": f"c{purchase['id']}"}
    amount = purchase["amount"] * 100
    checked = payme("CheckPerformTransaction", {"account": account, "amount": amount})["result"]
    assert checked["allow"] is True
    item = checked["detail"]["items"][0]
    assert checked["detail"]["receipt_type"] == 0
    assert item["code"] == "10305008003000000" and item["package_code"] == "1546532", item
    assert item["vat_percent"] == 0 and item["count"] == 1 and item["price"] == amount, item
    assert "50 ta token" in item["title"], item

    payme("CreateTransaction", {"id": "tx-order", "time": 1, "account": account, "amount": amount})
    payme("PerformTransaction", {"id": "tx-order"})
    assert credits_of() == before + 50
    # Noto'g'ri order_id — buyurtma topilmadi
    wrong = payme("CheckPerformTransaction", {"account": {"order_id": "x1"}, "amount": amount})
    assert wrong["error"]["code"] == -31050, wrong

    # Admin MXIK, o'lchov birligi va QQS'ni o'zgartira oladi; MXIK 17 raqam bo'lishi shart
    admin = as_user(4242)
    bad = client.post("/api/admin/payment-settings", headers=admin, json={"payme_mxik": "123"})
    assert bad.status_code == 422, bad.text
    saved = client.post("/api/admin/payment-settings", headers=admin,
                        json={"payme_mxik": "10305008003000001", "payme_package_code": "999", "payme_vat_percent": 12}).json()
    assert saved["payme"]["mxik"] == "10305008003000001" and saved["payme"]["vat_percent"] == 12, saved["payme"]
    other = client.post("/api/teacher/credit-purchases", json={"credits": 10, "provider": "payme"}, headers=as_user(555)).json()
    detail = payme("CheckPerformTransaction", {"account": {"order_id": f"c{other['id']}"}, "amount": other["amount"] * 100})
    item = detail["result"]["detail"]["items"][0]
    assert item["code"] == "10305008003000001" and item["package_code"] == "999" and item["vat_percent"] == 12, item
    client.post("/api/admin/payment-settings", headers=admin,
                json={"clear": ["payme_mxik", "payme_package_code", "payme_vat_percent"]})


def test_payme_xato_xabari_uch_tilda():
    """Payme xato matnini ru/uz/en ko'rinishida kutadi; buyurtma xatosida — maydon nomi (data)."""
    reset_database()
    wrong = payme("CheckPerformTransaction", {"account": {"order_id": "c999"}, "amount": 100})
    assert wrong["error"]["code"] == -31050
    assert wrong["error"]["message"] == {"ru": "Заказ не найден", "uz": "Buyurtma topilmadi", "en": "Order not found"}
    assert wrong["error"]["data"] == "order_id"
    missing = payme("CheckTransaction", {"id": "yoq"})
    assert missing["error"]["code"] == -31003 and "data" not in missing["error"], missing


def test_payme_sandbox_holatlari():
    """Payme sandbox: band buyurtma, qayta CreateTransaction, bekor qilish sababi, -2 holat, 12 soatlik muddat."""
    from datetime import timedelta

    from backend.database import CreditPurchase

    reset_database()
    register_teacher()

    def new_order(credits=10):
        purchase = client.post("/api/teacher/credit-purchases", json={"credits": credits, "provider": "payme"},
                               headers=as_user(555)).json()
        return {"order_id": f"c{purchase['id']}"}, purchase["amount"] * 100, purchase["id"]

    account, amount, purchase_id = new_order()
    created = payme("CreateTransaction", {"id": "tx-a", "time": 1700000000000, "account": account, "amount": amount})["result"]
    assert created["state"] == 1 and created["transaction"] == f"c{purchase_id}", created

    # Band buyurtma: tekshiruv ham, boshqa tranzaksiya ham — -31050…-31099
    busy = payme("CheckPerformTransaction", {"account": account, "amount": amount})["error"]
    assert -31099 <= busy["code"] <= -31050, busy
    other = payme("CreateTransaction", {"id": "tx-b", "time": 1, "account": account, "amount": amount})["error"]
    assert -31099 <= other["code"] <= -31050, other
    # Shu tranzaksiya qayta kelsa — o'sha create_time
    again = payme("CreateTransaction", {"id": "tx-a", "time": 1700000000000, "account": account, "amount": amount})["result"]
    assert again["create_time"] == created["create_time"], (again, created)

    # Bajarilgandan keyin bekor — holat -2, sabab saqlanadi
    payme("PerformTransaction", {"id": "tx-a"})
    cancelled = payme("CancelTransaction", {"id": "tx-a", "reason": 5})["result"]
    assert cancelled["state"] == -2, cancelled
    info = payme("CheckTransaction", {"id": "tx-a"})["result"]
    assert info["state"] == -2 and info["reason"] == 5 and info["create_time"] == created["create_time"], info
    # Bloklangan (bekor qilingan) buyurtmaga yangi tranzaksiya — -31050…-31099
    blocked = payme("CreateTransaction", {"id": "tx-c", "time": 1, "account": account, "amount": amount})["error"]
    assert -31099 <= blocked["code"] <= -31050, blocked
    # Bekor qilingan tranzaksiyani qayta yaratib bo'lmaydi — -31008
    assert payme("CreateTransaction", {"id": "tx-a", "time": 1, "account": account, "amount": amount})["error"]["code"] == -31008

    # Bajarilmasdan bekor — -1
    account2, amount2, _ = new_order(12)
    payme("CreateTransaction", {"id": "tx-d", "time": 1, "account": account2, "amount": amount2})
    assert payme("CancelTransaction", {"id": "tx-d", "reason": 3})["result"]["state"] == -1

    # 12 soatdan eski tranzaksiya bajarilmaydi — sabab 4 bilan bekor bo'ladi
    account3, amount3, purchase3 = new_order(14)
    payme("CreateTransaction", {"id": "tx-e", "time": 1, "account": account3, "amount": amount3})
    with SessionLocal() as session:
        row = session.get(CreditPurchase, purchase3)
        row.payme_created_at = main.utcnow() - timedelta(hours=13)
        session.commit()
    late = payme("PerformTransaction", {"id": "tx-e"})["error"]
    assert late["code"] == -31008, late
    late_info = payme("CheckTransaction", {"id": "tx-e"})["result"]
    assert late_info["state"] == -1 and late_info["reason"] == 4, late_info

    # GetStatement — tranzaksiyalar Payme yuborgan "time" bilan
    statement = payme("GetStatement", {"from": 0, "to": 4102444800000})["result"]["transactions"]
    first = next(item for item in statement if item["id"] == "tx-a")
    assert first["time"] == 1700000000000 and first["account"] == account, first


def test_payme_getstatement_katta_davr_va_changepassword():
    """GetStatement juda katta "to" bilan yiqilmaydi; ChangePassword yangi kalitni darhol kuchga kiritadi."""
    import base64 as _b64

    reset_database()
    register_teacher()
    purchase = client.post("/api/teacher/credit-purchases", json={"credits": 10, "provider": "payme"},
                           headers=as_user(555)).json()
    payme("CreateTransaction", {"id": "tx-s", "time": 1, "account": {"order_id": f"c{purchase['id']}"},
                                "amount": purchase["amount"] * 100})
    statement = payme("GetStatement", {"from": 1, "to": 10000000000000000})
    assert [item["id"] for item in statement["result"]["transactions"]] == ["tx-s"], statement
    assert payme("GetStatement", {"from": "x", "to": 1})["error"]["code"] == -32600

    original = main.PAYME_SECRET_KEY
    try:
        changed = payme("ChangePassword", {"password": "yangi-kalit"})
        assert changed["result"] == {"success": True}, changed
        assert main.PAYME_SECRET_KEY == "yangi-kalit"
        # Eski kalit endi o'tmaydi, yangisi o'tadi
        assert payme("CheckTransaction", {"id": "tx-s"})["error"]["code"] == -32504
        token = _b64.b64encode(b"Paycom:yangi-kalit").decode()
        response = client.post("/api/payme/webhook", headers={"Authorization": f"Basic {token}"},
                               json={"jsonrpc": "2.0", "id": 1, "method": "CheckTransaction", "params": {"id": "tx-s"}})
        assert response.json()["result"]["state"] == 1, response.json()
    finally:
        client.post("/api/admin/payment-settings", headers=as_user(4242), json={"clear": ["payme_secret_key"]})
        main.PAYME_SECRET_KEY = original
