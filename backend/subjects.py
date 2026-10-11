"""
Fanlar "pasporti" — Milliy sertifikat (UZBMB) test tuzilishi, BITTA joyda.

Manba: uzbmb.uz/page/fanlar — har bir fanning rasmiy test spetsifikatsiyasi
(Matematika, Fizika, Tarix — 2024; Kimyo, Biologiya, Geografiya — 2025;
Rus tili — 2024). Spetsifikatsiya o'zgarsa, faqat shu fayl yangilanadi —
server (bo'limlar, natijalar, PDF, sertifikat) ham, ilova (test yaratish
shabloni, klaviatura) ham shu yerdan o'qiydi.

Maydonlar:
  tests     — variantli savollar soni (1..tests)
  options   — boshqacha variantli savollar: {savol: variantlar soni}
              (33–35 — moslashtirish topshiriqlari, A–F)
  written   — yozma (ochiq) savollar soni, tests dan keyin raqamlanadi
  parts     — har bir yozma savol nechta bandli (2 — a va b)
  duration  — daqiqa
  keyboard  — yozma javob klaviaturasi: "math" (√, kasr, π) yoki "text" (oddiy harflar)
  sections  — bo'limlar: (nomi, "savollar") — None bo'lsa "qolgan barcha savollar"
  note      — test yaratishda ko'rinadigan izoh

Imtihonchi qo'lda baholaydigan "yozma ish" topshiriqlari (Kimyo, Biologiya
41–43; Rus tili 45 — insho) javoblar kaliti bilan avtomatik tekshirib
bo'lmaydi — ular ilovadagi testga kiritilmaydi (izohda aytiladi).
"""

MATCHING = {33: 6, 34: 6, 35: 6}  # moslashtirish topshiriqlari — A–F

SUBJECTS: dict[str, dict] = {
    "Matematika": {
        "tests": 35, "options": MATCHING, "written": 10, "parts": 2, "duration": 150, "keyboard": "math",
        "sections": [("Algebra", None), ("Geometriya", "24-30, 33-35, 41-44")],
        "note": "Matematika: 35 ta test (33–35 — A–F), 10 ta yozma (a, b). Bo'limlar: Algebra, Geometriya",
    },
    "Fizika": {
        "tests": 35, "options": MATCHING, "written": 10, "parts": 2, "duration": 150, "keyboard": "math",
        "sections": [
            ("Mexanika", "1-10, 36-38"),
            ("Molekulyar fizika", "11-15, 39-40"),
            ("Elektr va magnitizm", "16-25, 41-42"),
            ("Optika", "26-28, 43-44"),
            ("Atom va yadro fizikasi", "29-31, 45"),
            ("Mantiqiy va aralash", "32-35"),
        ],
        "note": "Fizika: 35 ta test (33–35 — moslashtirish, A–F), 10 ta yozma (a, b), 150 daqiqa",
    },
    "Kimyo": {
        "tests": 35, "options": MATCHING, "written": 5, "parts": 1, "duration": 100, "keyboard": "math",
        "sections": [
            ("Umumiy kimyo", "1-13, 36-37"),
            ("Anorganik kimyo", "14-19, 38"),
            ("Organik kimyo", "20-29, 39-40"),
            ("Kimyoviy tahlil", "30-32"),
            ("Aralash", "33-35"),
        ],
        "note": "Kimyo: 35 ta test (33–35 — moslashtirish), 5 ta qisqa javob, 100 daqiqa. "
                "41–43 (yozma ish) imtihonchi baholaydi — ilovada tekshirilmaydi",
    },
    "Biologiya": {
        "tests": 35, "options": MATCHING, "written": 5, "parts": 1, "duration": 100, "keyboard": "text",
        "sections": [
            ("Hujayra va genetika", "1-11"),
            ("O'simlik va hayvonlar", "12-19"),
            ("Odam organizmi", "20-23"),
            ("Evolyutsiya va ekologiya", "24-32"),
            ("Umumbiologik qonuniyatlar", "33-40"),
        ],
        "note": "Biologiya: 35 ta test (33–35 — moslashtirish), 5 ta qisqa javob, 100 daqiqa. "
                "41–43 (yozma ish) imtihonchi baholaydi — ilovada tekshirilmaydi",
    },
    "Tarix": {
        "tests": 35, "options": MATCHING, "written": 10, "parts": 2, "duration": 90, "keyboard": "text",
        "sections": [
            ("Siyosiy tarix", "1-20, 36-44"),
            ("Ijtimoiy-iqtisodiy tarix", "21-26, 45"),
            ("Madaniy tarix", "27-32"),
            ("Aralash", "33-35"),
        ],
        "note": "Tarix: 35 ta test (33–35 — moslashtirish), 10 ta yozma (a, b), 90 daqiqa",
    },
    "Geografiya": {
        "tests": 35, "options": MATCHING, "written": 10, "parts": 2, "duration": 150, "keyboard": "text",
        "sections": [
            ("Tabiiy geografiya", "1-10, 29-30, 36-40"),
            ("O'rta Osiyo va O'zbekiston", "11-15, 31-32"),
            ("Iqtisodiy geografiya", "16-21, 26-28, 41-44"),
            ("Jahon mintaqalari", "22-25, 45"),
            ("Aralash", "33-35"),
        ],
        "note": "Geografiya: 35 ta test (33–35 — moslashtirish), 10 ta yozma (a, b), 150 daqiqa",
    },
    "Rus tili": {
        "tests": 35, "options": MATCHING, "written": 9, "parts": 2, "duration": 180, "keyboard": "text",
        "sections": [
            ("Til (grammatika)", "1-12"),
            ("Adabiyot", "13-17"),
            ("O'qish savodxonligi", "18-32"),
            ("Aralash", "33-35"),
            ("Lingvistik tahlil", "36-44"),
        ],
        "note": "Rus tili: 35 ta test (33–35 — moslashtirish), 9 ta yozma (a, b). "
                "45 (insho) imtihonchi baholaydi — ilovada tekshirilmaydi",
    },
}


def subject_config(subject: str | None) -> dict | None:
    """Fan nomi bo'yicha pasport (katta-kichik harf va ortiqcha bo'shliqlarsiz)."""
    key = " ".join((subject or "").split()).lower()
    for name, config in SUBJECTS.items():
        if name.lower() == key:
            return config
    return None


def subject_keyboard(subject: str | None) -> str:
    config = subject_config(subject)
    return config["keyboard"] if config else "math"


def _numbers(ranges: str) -> list[int]:
    result: list[int] = []
    for chunk in ranges.replace(" ", "").split(","):
        if not chunk:
            continue
        low, _, high = chunk.partition("-")
        result += range(int(low), int(high or low) + 1)
    return result


def default_sections(subject: str | None, question_count: int) -> list[dict]:
    """
    Fanning Milliy sertifikat bo'limlari — faqat test tuzilishi shablonga mos
    kelsa (savollar soni bir xil). Aks holda bo'limsiz.
    """
    config = subject_config(subject)
    if not config or question_count != config["tests"] + config["written"]:
        return []
    all_numbers = range(1, question_count + 1)
    explicit = {name: _numbers(ranges) for name, ranges in config["sections"] if ranges}
    taken = {number for numbers in explicit.values() for number in numbers}
    sections = []
    for name, ranges in config["sections"]:
        numbers = explicit[name] if ranges else [number for number in all_numbers if number not in taken]
        sections.append({"name": name, "numbers": sorted(set(numbers))})
    return sections


def frontend_config() -> dict:
    """Ilovaga beriladigan ko'rinish (JSON): savol raqamlari kalitlari matn bo'ladi."""
    return {
        name: {
            **{key: value for key, value in config.items() if key != "sections"},
            "options": {str(number): count for number, count in config["options"].items()},
            "sections": [{"name": name_, "ranges": ranges} for name_, ranges in config["sections"]],
        }
        for name, config in SUBJECTS.items()
    }
