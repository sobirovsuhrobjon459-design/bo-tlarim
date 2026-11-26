"""
EAN-13 shtrix-kod — Milliy sertifikat titulining har betida (sarlavhada).

Kodda: "2" (ichki kod) + o'quvchi ID (4 xona; bo'sh titulda 0000) + bet raqami (2 xona) + test raqami (oxirgi
5 xona) + nazorat raqami. Skaner kodni titulning ma'lum joyidan to'g'ridan-to'g'ri o'qiydi (modul eni ma'lum) —
bet qaysi o'quvchiniki, nechanchi bet va qaysi testniki ekani shundan bilinadi: betlar aralashib ketsa ham
har biri o'z egasiga boradi. Telefondagi skanerda xuddi shu jadvallar (frontend/js/app.js: readSheetBarcode).
"""

from __future__ import annotations

MODULE = 1.2        # bitta modul (eng ingichka chiziq) eni, pt
BAR_HEIGHT = 22.0   # chiziqlar bo'yi, pt
MODULES = 95        # EAN-13: 3 + 6×7 + 5 + 6×7 + 3

L_CODES = ["0001101", "0011001", "0010011", "0111101", "0100011", "0110001", "0101111", "0111011", "0110111", "0001011"]
R_CODES = ["".join("1" if bit == "0" else "0" for bit in code) for code in L_CODES]
G_CODES = [code[::-1] for code in R_CODES]
PARITY = ["LLLLLL", "LLGLGG", "LLGGLG", "LLGGGL", "LGLLGG", "LGGLLG", "LGGGLL", "LGLGLG", "LGLGGL", "LGGLGL"]


def check_digit(digits: str) -> int:
    """12 raqamning nazorat raqami (EAN-13)."""
    total = sum(int(digit) * (3 if index % 2 else 1) for index, digit in enumerate(digits))
    return (10 - total % 10) % 10


def modules(digits: str) -> str:
    """12 raqam → 95 modul ("1" — qora chiziq, "0" — oq), nazorat raqami qo'shiladi."""
    full = digits + str(check_digit(digits))
    parity = PARITY[int(full[0])]
    left = "".join((L_CODES if kind == "L" else G_CODES)[int(digit)] for kind, digit in zip(parity, full[1:7]))
    right = "".join(R_CODES[int(digit)] for digit in full[7:13])
    return "101" + left + "01010" + right + "101"


def decode(bits: str) -> str | None:
    """95 modul → 13 raqam (nazorat raqami to'g'ri bo'lsa), aks holda None."""
    if len(bits) != MODULES or bits[:3] != "101" or bits[45:50] != "01010" or bits[92:] != "101":
        return None
    digits, parity = [], ""
    for index in range(6):
        chunk = bits[3 + index * 7: 10 + index * 7]
        if chunk in L_CODES:
            digits.append(L_CODES.index(chunk))
            parity += "L"
        elif chunk in G_CODES:
            digits.append(G_CODES.index(chunk))
            parity += "G"
        else:
            return None
    if parity not in PARITY:
        return None
    for index in range(6):
        chunk = bits[50 + index * 7: 57 + index * 7]
        if chunk not in R_CODES:
            return None
        digits.append(R_CODES.index(chunk))
    value = str(PARITY.index(parity)) + "".join(map(str, digits))
    return value if check_digit(value[:12]) == int(value[12]) else None


def sheet_value(number: int | None, page: int, test_id: int) -> str:
    """Titul beti kodi (12 raqam, nazoratsiz)."""
    return f"2{(number or 0) % 10000:04d}{page % 100:02d}{test_id % 100000:05d}"


def parse_sheet_value(value: str | None) -> dict | None:
    """13 raqamli kod → {"number": ID (0 — bo'sh titul), "page", "test"}; titul kodi bo'lmasa — None."""
    if not value or len(value) != 13 or value[0] != "2":
        return None
    page = int(value[5:7])
    if not page:
        return None
    return {"number": int(value[1:5]), "page": page, "test": int(value[7:12])}
