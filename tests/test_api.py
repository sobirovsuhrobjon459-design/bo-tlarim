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


def as_user(tg_id: int) -> dict:
    """DEV_MODE'dagi soxta foydalanuvchi header'i."""
    return {"X-Dev-User": str(tg_id)}


def register_teacher(tg_id: int = 555, first: str = "Ali", last: str = "Valiyev") -> dict:
    response = client.post(
        "/api/teacher/register", json={"first_name": first, "last_name": last}, headers=as_user(tg_id)
    )
    assert response.status_code == 200, response.text
    return response.json()


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
    result = client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={
            "answers": {
                "1": "B",        # to'g'ri
                "2": "A",        # noto'g'ri (C bo'lishi kerak)
                "3": "c,a",      # to'g'ri — tartib va harf o'lchami muhim emas
                "4": "2 ildiz 3",  # to'g'ri — "2\sqrt{3}" bilan bir xil ma'noda
            }
        },
        headers=as_user(901),
    ).json()

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
        result = client.post(
            f"/api/attempts/{started['attempt_id']}/finish",
            json={"answers": {"1": written}},
            headers=as_user(1000 + index),
        ).json()
        assert result["correct"] == 1, f"{written!r} to'g'ri deb hisoblanmadi: {result}"


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
    result = client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={"answers": {"1": "A", "2": "5", "3": "9"}},
        headers=as_user(901),
    ).json()
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
    result = client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={"answers": {"1": "B", "2": "E", "3": "c,a", "4": "2 ildiz 3", "5": "(pi)/(5)"}},
        headers=as_user(901),
    ).json()

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
    result = client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={"answers": {"1": "5", "2": "9"}},
        headers=as_user(901),
    ).json()

    assert result["total"] == 1 and result["correct"] == 0, result

    row = client.get(f"/api/teacher/tests/{created['id']}/results", headers=as_user(555)).json()["rows"][0]
    assert row["written_total"] == 1 and row["written_correct"] == 0, row


# ========== 4) Bitta urinish qoidasi ==========


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
    result = client.post(
        f"/api/attempts/{started['attempt_id']}/finish",
        json={"answers": {"4": "2 ildiz 3"}},
        headers=as_user(901),
    ).json()

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


def test_majburiy_obuna_bot_kira_olmasa_oquvchi_otkazilmaydi():
    """
    Eng muhim tuzatish: ilgari bot kanalga kira olmasa, HAMMA o'tib ketardi —
    ya'ni "majburiy obuna" aslida hech kimni to'smasdi.
    """
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "member"}

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
        assert response.status_code == 409, f"o'quvchi o'tkazib yuborildi: {response.status_code}"
        assert "administrator" in response.json()["detail"]
    finally:
        main._ask_telegram_chat_member = original


def test_majburiy_obuna_vaqtinchalik_xatoda_toxtatmaydi():
    """Internet uzilsa, butun sinf test ishlay olmay qolmasligi kerak."""
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "member"}

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
        assert response.status_code == 200, response.text
    finally:
        main._ask_telegram_chat_member = original


def test_kanal_nomi_har_xil_yozilsa_ham_bir_xil_saqlanadi():
    reset_database()
    register_teacher()

    def ok(channel, tg_id):
        return {"status": "member"}

    original = main._ask_telegram_chat_member
    main._ask_telegram_chat_member = ok
    try:
        for written in ("@kanalim", "kanalim", "t.me/kanalim", "https://t.me/kanalim"):
            created = create_test(required_channel=written)
            info = client.get(f"/api/tests/{created['code']}", headers=as_user(901)).json()
            assert info["required_channel"] == "@kanalim", f"{written!r} -> {info['required_channel']}"
    finally:
        main._ask_telegram_chat_member = original


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
