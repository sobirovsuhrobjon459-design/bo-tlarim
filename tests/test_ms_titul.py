"""
15-versiya: Milliy sertifikat tituli (2 bet) — chizilishi va skanerlanishi.

Matematika, Fizika, Tarix (shu tuzilishdagi) oflayn testi (1–32 A–D, 33–35 A–F, 36–45 a/b) imtihondagidek
2 betlik titulda chiqadi: 1-betda ID, variant va 1–35; 2-betda yotqizilgan ID va 36–45 yozish kataklari.
"""

import base64
import io

from test_api import SessionLocal, as_user, client, create_test, main, register_teacher, reset_database

ZOOM = 2.0


def _ms_questions() -> list[dict]:
    questions = [{"number": n, "question_type": "single", "option_count": 6 if n >= 33 else 4,
                  "correct": "ABCDEF"[n % (6 if n >= 33 else 4)]} for n in range(1, 36)]
    number = 36
    for question in range(36, 46):
        for part in "ab":
            questions.append({"number": number, "label": f"{question}{part}", "question_type": "text",
                              "correct": str(question)})
            number += 1
    return questions


def _ms_test(**overrides) -> int:
    body = {"enrollment_type": "closed", "subject": "Matematika", "key_questions": _ms_questions()}
    body.update(overrides)
    return create_test(**body)["id"]


def _sheet(test_id: int, variants: list[str] | None = None, students: list | None = None) -> tuple[bytes, list[dict]]:
    from backend.database import Test

    with SessionLocal() as session:
        test = session.get(Test, test_id)
        bubbles: list[dict] = []
        pdf = main.generate_answer_sheet_pdf(test, bubbles=bubbles, variants=variants, students=students)
    return pdf, bubbles


def _photo(pdf: bytes, bubbles: list[dict], page: int, marks: dict, writing: dict | None = None) -> str:
    """Betning "surati": PDF beti rasmga aylanadi, doirachalar bo'yaladi, kataklarga yoziladi, qiyshaytiriladi."""

    import fitz
    from PIL import Image, ImageDraw

    rendered = fitz.open(stream=pdf, filetype="pdf")[page - 1].get_pixmap(matrix=fitz.Matrix(ZOOM, ZOOM))
    image = Image.frombytes("RGB", (rendered.width, rendered.height), rendered.samples)
    height = image.height / ZOOM
    draw = ImageDraw.Draw(image)
    for bubble in bubbles:
        if bubble["page"] != page or bubble["kind"] in ("mark", "code"):
            continue
        cx, cy = bubble["x"] * ZOOM, (height - bubble["y"]) * ZOOM
        if bubble["kind"] == "write":
            if bubble["label"] in (writing or {}):
                draw.text((cx - 20, cy - 10), writing[bubble["label"]], fill=(20, 30, 120), font_size=26)
            continue
        if bubble["letter"] in marks.get(bubble["ref"], "").split(","):
            r = bubble["r"] * ZOOM
            draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(30, 40, 110))
    background = Image.new("RGB", (image.width + 300, image.height + 300), (90, 70, 60))
    background.paste(image.rotate(3, expand=True, fillcolor=(90, 70, 60)), (120, 110))
    buffer = io.BytesIO()
    background.save(buffer, "JPEG", quality=85)
    return base64.b64encode(buffer.getvalue()).decode()


def _id_marks(number: int) -> dict:
    return {f"id{index}": digit for index, digit in enumerate(f"{number:04d}")}


def test_milliy_sertifikat_tituli_ikki_betli():
    """Yangi oflayn Matematika testi — 15-versiya, titul 2 bet: 1-betda 35 savol, 2-betda 20 ta yozish katagi."""

    import fitz

    from backend.database import Test

    reset_database()
    register_teacher()
    test_id = _ms_test()
    with SessionLocal() as session:
        assert session.get(Test, test_id).sheet_version == 15

    layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
    assert layout["page_count"] == 2
    assert layout["sheet_page"]["height"] == main.A4[1] and layout["sheet_page"]["name_box"] is True
    by_page = {page: [b for b in layout["bubbles"] if b["page"] == page] for page in (1, 2)}
    questions = {}
    for bubble in by_page[1]:
        if bubble["kind"] == "single":
            questions.setdefault(bubble["ref"], []).append(bubble["letter"])
    assert len(questions) == 35 and questions[1] == list("ABCD") and questions[35] == list("ABCDEF")
    for page in (1, 2):  # ID ikkala betda: 4 xona × 10 raqam; burchak/chet belgilari va kod — har betda
        ids = [b for b in by_page[page] if b["kind"] == "id"]
        assert len(ids) == 40 and {b["ref"] for b in ids} == {"id0", "id1", "id2", "id3"}
        assert sum(b["kind"] == "mark" for b in by_page[page]) == 20 and any(b["kind"] == "code" for b in by_page[page])
    written = [b for b in by_page[2] if b["kind"] == "write"]
    assert [b["label"] for b in written] == [f"{n}{p}" for n in range(36, 46) for p in "ab"]
    assert not any(b["kind"] == "write" for b in by_page[1])
    assert all(b["w"] > 150 and b["h"] > 30 for b in written)  # yozishga yetarli katta
    # 2-betdagi ID — yotqizilgan: har xona bitta gorizontal qator (1-betda — tik ustun)
    for page, along in ((1, "y"), (2, "x")):
        row = [b for b in by_page[page] if b["ref"] == "id0"]
        assert len({round(b["x" if along == "y" else "y"]) for b in row}) == 1

    pdf = client.get(f"/api/teacher/tests/{test_id}/answer-sheet", headers=as_user(555))
    pages = fitz.open(stream=pdf.content, filetype="pdf")
    assert len(pages) == 2
    first, second = pages[0].get_text(), pages[1].get_text()
    assert "JAVOBLAR VARAQASI" in first and "MILLIY SERTIFIKAT" in first and "Sinov test" in first
    assert "DALOLATNOMASI" in first and "36–45: YOZMA JAVOBLAR" in second and "YOZILMASIN" in second

    # Variantli test: 1-betda variant doirachalari (ko'pi bilan 4 ta)
    _, bubbles = _sheet(test_id, variants=["A", "B", "C"])
    assert [b["letter"] for b in bubbles if b["kind"] == "variant"] == ["A", "B", "C"]
    assert all(b["page"] == 1 for b in bubbles if b["kind"] == "variant")

    # Ismli titul: har o'quvchiga 2 bet, ID ikkala betda bo'yalgan
    pdf, _ = _sheet(test_id, students=[(17, "Aliyev Vali"), (18, "Karimova Zeb")])
    named = fitz.open(stream=pdf, filetype="pdf")
    assert len(named) == 4 and "Aliyev Vali" in named[1].get_text() and "Karimova Zeb" in named[2].get_text()


def test_milliy_sertifikat_tituli_skanerlanadi():
    """1-bet: ID, 1–35 javoblar; 2-bet: o'sha ID va yozma kataklar rasmi (yozilgani — bo'sh emas)."""

    from backend.omr import written_is_blank

    reset_database()
    register_teacher()
    test_id = _ms_test()
    pdf, bubbles = _sheet(test_id)
    base = f"/api/teacher/tests/{test_id}"

    marks = {**_id_marks(1234), 1: "B", 17: "D", 32: "A", 33: "E", 35: "F"}
    first = client.post(f"{base}/scan", json={"image_base64": _photo(pdf, bubbles, 1, marks), "page": 1},
                        headers=as_user(555))
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["answers"] == {"1": "B", "17": "D", "32": "A", "33": "E", "35": "F"}, body["answers"]
    assert body["sheet_number"] == 1234 and body["page_count"] == 2 and body["written"] == []
    assert body["name_image"].startswith("data:image/png")

    photo = _photo(pdf, bubbles, 2, _id_marks(1234), writing={"36a": "2√3", "41b": "-1/2"})
    second = client.post(f"{base}/scan", json={"image_base64": photo, "page": 2}, headers=as_user(555))
    assert second.status_code == 200, second.text
    body = second.json()
    assert body["sheet_number"] == 1234 and body["answers"] == {}
    images = {item["label"]: item["image"] for item in body["written"]}
    assert len(images) == 20
    assert not written_is_blank(images["36a"]) and not written_is_blank(images["41b"])
    # Bo'sh katak — katakchalari bo'lsa ham bo'sh deb o'qiladi (AI'ga yuborilmaydi)
    assert written_is_blank(images["36b"]) and written_is_blank(images["45a"])


def test_tarix_tituli_katakchali():
    """Tarix: 2-betda javob harfma-harf katakchalarga (a va b ostma-ost); bo'sh qator — bo'sh, yozilgani — yo'q."""

    import fitz

    from backend.omr import written_is_blank

    reset_database()
    register_teacher()
    test_id = _ms_test(subject="Tarix")
    pdf, bubbles = _sheet(test_id)
    second = fitz.open(stream=pdf, filetype="pdf")[1].get_text()
    assert "Eslatma" in second and "Bo‘shang" in second and "YOZILMASIN" not in second
    written = [b for b in bubbles if b["kind"] == "write"]
    assert len(written) == 20 and all(b["page"] == 2 and b["w"] > 400 for b in written)
    a, b = (next(item for item in written if item["label"] == label) for label in ("36a", "36b"))
    assert a["x"] == b["x"] and a["y"] > b["y"]  # b) — a) ning ostida

    photo = _photo(pdf, bubbles, 2, _id_marks(77), writing={"36a": "AMIR TEMUR", "40b": "1 9 2 9"})
    body = client.post(f"/api/teacher/tests/{test_id}/scan", json={"image_base64": photo, "page": 2},
                       headers=as_user(555)).json()
    assert body["sheet_number"] == 77
    images = {item["label"]: item["image"] for item in body["written"]}
    assert not written_is_blank(images["36a"]) and not written_is_blank(images["40b"])
    assert written_is_blank(images["36b"]) and written_is_blank(images["45b"])

    # Geografiya — Tarixdagidek katakchali, o'z namunasi va "Diqqat! Yozilmasin" ustuni bilan
    geo_pdf, geo_bubbles = _sheet(_ms_test(subject="Geografiya"))
    geo_text = fitz.open(stream=geo_pdf, filetype="pdf")[1].get_text()
    assert "YOZILMASIN" in geo_text and "tutuq belgisi" in geo_text and "Bo‘shang" not in geo_text
    geo_written = [b for b in geo_bubbles if b["kind"] == "write"]
    assert len(geo_written) == 20 and all(b["page"] == 2 and 330 < b["w"] < 420 for b in geo_written)

    # Matematika — katakchasiz (erkin katak)
    _, bubbles = _sheet(_ms_test())
    assert all(b["w"] < 300 for b in bubbles if b["kind"] == "write")


def test_ona_tili_tituli_esse_bilan():
    """Ona tili: 2-betda 36–39 (bitta qator) va 40–44 (a, b) katakchalari; 3-bet — esse (bitta bet), rasmi saqlanadi."""

    import fitz

    from backend.database import EssayImage
    from backend.omr import written_is_blank
    from test_api import _subject_key

    reset_database()
    register_teacher()
    test_id = _ms_test(subject="Ona tili va adabiyot", key_questions=_subject_key("Ona tili va adabiyot"), essay=True,
                       essay_topic="Kitob — bilim manbai")
    pdf, bubbles = _sheet(test_id)
    pages = fitz.open(stream=pdf, filetype="pdf")
    assert len(pages) == 3
    assert "bosh harflar bilan" in pages[1].get_text() and "YOZILMASIN" in pages[1].get_text()
    assert "ESSE" in pages[2].get_text() and "Familiya, ismi" in pages[2].get_text()
    written = [b for b in bubbles if b["kind"] == "write"]
    assert [b["label"] for b in written] == main.MS15_NATIVE_WRITTEN and all(b["page"] == 2 for b in written)
    essay = [b for b in bubbles if b["kind"] == "page_image"]
    assert [(b["page"], b["ref"], b["letter"]) for b in essay] == [(3, "essay", "1")]
    for page in (2, 3):  # har betda skaner belgilari bor
        assert sum(b["kind"] == "mark" and b["page"] == page for b in bubbles) == 20

    base = f"/api/teacher/tests/{test_id}"
    photo = _photo(pdf, bubbles, 2, _id_marks(55), writing={"36": "OLMOS", "40b": "KITOB"})
    body = client.post(f"{base}/scan", json={"image_base64": photo, "page": 2}, headers=as_user(555)).json()
    images = {item["label"]: item["image"] for item in body["written"]}
    assert body["sheet_number"] == 55 and len(images) == 14
    assert not written_is_blank(images["36"]) and not written_is_blank(images["40b"]) and written_is_blank(images["37"])

    photo = _photo(pdf, bubbles, 3, {})
    body = client.post(f"{base}/scan", json={"image_base64": photo, "page": 3}, headers=as_user(555)).json()
    assert body["written"] == [] and [(item["ref"], item["page"]) for item in body["page_images"]] == [("essay", 1)]
    essay_image = body["page_images"][0]["image"]
    assert essay_image.startswith("data:image/jpeg") and len(essay_image) < main.PAGE_IMAGE_LIMIT

    saved = client.post(f"{base}/scan/save", headers=as_user(555), json={
        "name": "Aliyev Vali", "answers": {"1": "A"},
        "page_images": [{"ref": "essay", "page": 1, "image": essay_image}, {"ref": "bad", "page": 2, "image": "x"}],
    })
    assert saved.status_code == 200, saved.text
    with SessionLocal() as session:
        stored = session.query(EssayImage).all()
        assert [(row.page, row.image == essay_image) for row in stored] == [(1, True)]

    # Essesiz Ona tili testi — 2 bet
    plain = _ms_test(subject="Ona tili va adabiyot", key_questions=_subject_key("Ona tili va adabiyot"))
    _, bubbles = _sheet(plain)
    assert max(b["page"] for b in bubbles) == 2


def test_biologiya_kimyo_tituli_yozma_ish_betlari():
    """Biologiya/Kimyo: 2-bet — 36–40; 41, 42, 43 — har biri bet: band javoblari qatori + yechim rasmi."""

    import fitz

    from backend.database import Attempt
    from backend.omr import written_is_blank
    from test_api import _subject_key

    reset_database()
    register_teacher()
    test_id = _ms_test(subject="Biologiya", key_questions=_subject_key("Biologiya"))
    pdf, bubbles = _sheet(test_id)
    pages = fitz.open(stream=pdf, filetype="pdf")
    assert len(pages) == 5
    assert "Namuna" in pages[1].get_text() and "41: YOZMA ISH" in pages[2].get_text()
    labels = {page: [b["label"] for b in bubbles if b["kind"] == "write" and b["page"] == page] for page in range(1, 6)}
    assert labels == {1: [], 2: ["36", "37", "38", "39", "40"], 3: ["41.1", "41.2", "41.3", "41.4"],
                      4: ["42.1", "42.2", "42.3", "42.4", "42.5"], 5: ["43.1", "43.2", "43.3"]}
    solutions = [(b["page"], b["ref"], b["letter"]) for b in bubbles if b["kind"] == "page_image"]
    assert solutions == [(3, "solution", "3"), (4, "solution", "4"), (5, "solution", "5")]

    base = f"/api/teacher/tests/{test_id}"
    photo = _photo(pdf, bubbles, 3, {}, writing={"41.2": "FOTOSINTEZ"})
    body = client.post(f"{base}/scan", json={"image_base64": photo, "page": 3}, headers=as_user(555)).json()
    images = {item["label"]: item["image"] for item in body["written"]}
    assert len(images) == 4 and not written_is_blank(images["41.2"]) and written_is_blank(images["41.1"])
    assert [(item["ref"], item["page"]) for item in body["page_images"]] == [("solution", 3)]

    saved = client.post(f"{base}/scan/save", headers=as_user(555), json={
        "name": "Karimov Anvar", "answers": {"1": "A"},
        "page_images": [{"ref": "solution", "page": 3, "image": body["page_images"][0]["image"]}],
    })
    assert saved.status_code == 200, saved.text
    with SessionLocal() as session:
        attempt_id = session.query(Attempt).filter_by(offline_name="Karimov Anvar").one().id
    detail = client.get(f"{base}/students/{attempt_id}/detail", headers=as_user(555)).json()
    assert detail["solution_photos"] == 1
    photos = client.get(f"{base}/students/{attempt_id}/sheet/photos", headers=as_user(555)).json()
    assert [page["page"] for page in photos["pages"]] == [3]

    # Kimyo ham shunday (bandlar soni boshqa: 3, 3, 3)
    _, bubbles = _sheet(_ms_test(subject="Kimyo", key_questions=_subject_key("Kimyo")))
    assert max(b["page"] for b in bubbles) == 5
    assert [b["label"] for b in bubbles if b["kind"] == "write" and b["page"] == 3] == ["41.1", "41.2", "41.3"]


def test_shtrix_kod_betlar_aralash_skanerlanadi():
    """Har betda shtrix-kod: skaner betni o'zi aniqlaydi, ismli titulda ID koddan olinadi, boshqa test rad etiladi."""

    from backend.barcode import decode, modules, parse_sheet_value, sheet_value
    from test_api import _subject_key

    assert parse_sheet_value(decode(modules(sheet_value(1101, 3, 123456)))) == {"number": 1101, "page": 3, "test": 23456}
    assert decode("0" * 95) is None

    reset_database()
    register_teacher()
    test_id = _ms_test(subject="Biologiya", key_questions=_subject_key("Biologiya"))
    pdf, bubbles = _sheet(test_id, students=[(1101, "Aliyev Vali")])
    assert sorted(b["page"] for b in bubbles if b["kind"] == "barcode") == [1, 2, 3, 4, 5]
    layout = client.get(f"/api/teacher/tests/{test_id}/scan/layout", headers=as_user(555)).json()
    assert layout["test_code"] == test_id % 100000

    base = f"/api/teacher/tests/{test_id}"
    # 4-betning surati "1-bet" deb yuborildi — skaner shtrix-koddan 4-bet ekanini va ID'ni (bo'yalmagan) biladi
    photo = _photo(pdf, bubbles, 4, {}, writing={"42.2": "MITOZ"})
    body = client.post(f"{base}/scan", json={"image_base64": photo, "page": 1}, headers=as_user(555)).json()
    assert body["page"] == 4 and body["sheet_number"] == 1101 and body["barcode"]["page"] == 4
    assert [item["label"] for item in body["written"]] == ["42.1", "42.2", "42.3", "42.4", "42.5"]

    # Boshqa testning varag'i — rad etiladi
    other = _ms_test(subject="Biologiya", key_questions=_subject_key("Biologiya"))
    wrong = client.post(f"/api/teacher/tests/{other}/scan", json={"image_base64": photo, "page": 1}, headers=as_user(555))
    assert wrong.status_code == 422 and "boshqa test" in wrong.json()["detail"]


def test_shtrix_kod_telefondagi_skanerda_oqiladi():
    """Telefondagi skanerning readSheetBarcode funksiyasi (app.js) — V8'da, titulning chizilgan rasmi ustida."""

    import re
    from pathlib import Path

    import fitz
    import numpy as np
    from py_mini_racer import MiniRacer

    reset_database()
    register_teacher()
    test_id = _ms_test()
    pdf, bubbles = _sheet(test_id, students=[(4321, "Valiyev Ali")])
    source = Path(main.__file__).resolve().parent.parent.joinpath("frontend", "js", "app.js").read_text(encoding="utf-8")
    function = re.search(r"^function readSheetBarcode\(sheet, bubbles\) \{.*?^\}$", source, re.S | re.M).group(0)
    context = MiniRacer()
    context.eval(f"const SHEET_PAGE = {{ height: {main.A4[1]} }}; const READ_SCALE = 2;\n{function}")
    for page in (1, 2):
        pix = fitz.open(stream=pdf, filetype="pdf")[page - 1].get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csGRAY)
        gray = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)[:240]  # faqat tepa (sarlavha)
        sheet = {"W": int(gray.shape[1]), "H": int(gray.shape[0]), "warped": gray.ravel().tolist()}
        page_bubbles = [b for b in bubbles if b["page"] == page]
        found = context.call("readSheetBarcode", sheet, page_bubbles)
        assert found == {"number": 4321, "page": page, "test": test_id % 100000}, found


def test_mos_kelmaydigan_test_eski_titulda():
    """Tuzilishi boshqa (yoki onlayn) test 15-versiyada ham avvalgi bitta betlik titulda chiqadi."""

    reset_database()
    register_teacher()
    cases = {
        "boshqa tuzilish": _ms_test(key_questions=_ms_questions()[:30]),
        "onlayn": _ms_test(enrollment_type="open"),
        # Tuzilishi mos, lekin titul namunasi hali yo'q fan
        "Rus tili": _ms_test(subject="Rus tili"),
        # 33–35 to'rt variantli — imtihon formati emas
        "33-35 A-D": _ms_test(key_questions=[{**q, "option_count": 4, "correct": "A"} if q["number"] in (33, 34, 35) else q
                                             for q in _ms_questions()]),
    }
    for name, test_id in cases.items():
        _, bubbles = _sheet(test_id)
        assert max(b["page"] for b in bubbles) == 1, name
        assert not any(b["kind"] == "write" and b["page"] == 2 for b in bubbles), name

    # 5 tadan ko'p variant — 1-betga sig'maydi: eski titul
    test_id = _ms_test()
    _, bubbles = _sheet(test_id, variants=["A", "B", "C", "D", "E"])
    assert max(b["page"] for b in bubbles) == 1
