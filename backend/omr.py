"""
Javoblar varaqasini suratdan o'qish (OMR).

Varaq (generate_answer_sheet_pdf) burchaklarida 4 ta qora nuqta bor. Suratda
shu nuqtalar topiladi, varaq tekislanadi (perspektiva to'g'rilanadi) va har bir
doirachaning bo'yalganligi o'lchanadi. Doirachalarning aniq joyi PDF chizilgan
paytdagi koordinatalardan olinadi — shuning uchun varaq qanday joylashgan
bo'lsa ham (2, 3 yoki 4 ustun) skaner aynan o'sha nuqtalarni tekshiradi.

Natija faqat TAKLIF: o'qituvchi ekranda ko'rib, kerak bo'lsa tuzatadi va
o'zi saqlaydi. Ikkita doiracha bo'yalgan yoki shubhali savollar alohida
belgilanadi.
"""

from __future__ import annotations

import base64
import io
import itertools

import cv2
import numpy as np
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4

PAGE_WIDTH, PAGE_HEIGHT = A4
SCALE = 3.0  # tekislangan rasmda 1 punkt = 3 piksel
CORNER_OFFSET = 32  # burchak nuqtalari markazi chetdan 32 punktda (qarang: _draw_corner_marks)
CORNER_RADIUS = 7

# Doiracha ichining qancha qismi qoraygan bo'lsa "bo'yalgan" hisoblanadi
FILLED_RATIO = 0.5
UNSURE_RATIO = 0.25

# Ism yoziladigan katakning chap qismi (birinchi sahifa, punktlarda, tepadan sanalganda):
# ism odatda shu yerda — telefonda kattaroq, o'qilishi oson ko'rinadi
NAME_BOX = (120, 80, 420, 100)


class ScanError(Exception):
    """Suratdan varaqni o'qib bo'lmadi — sababi foydalanuvchiga ko'rsatiladi."""


def _load_image(data: bytes) -> np.ndarray:
    try:
        image = Image.open(io.BytesIO(data))
        image = ImageOps.exif_transpose(image).convert("RGB")  # telefon surati yonboshlab qolmasin
    except Exception as error:  # noqa: BLE001 — har qanday buzilgan fayl
        raise ScanError("Rasmni ochib bo'lmadi — JPG yoki PNG surat yuboring") from error
    longest = max(image.size)
    if longest > 2200:
        factor = 2200 / longest
        image = image.resize((round(image.width * factor), round(image.height * factor)), Image.LANCZOS)
    if min(image.size) < 500:
        raise ScanError("Surat juda kichik — varaqni yaqinroqdan, aniq qilib suratga oling")
    return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)


def _corner_candidates(gray: np.ndarray) -> list[tuple[float, float, float]]:
    """To'liq bo'yalgan, dumaloq qora dog'lar: (x, y, radius)."""

    height, width = gray.shape
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)
    block = max(31, (min(height, width) // 10) | 1)
    binary = cv2.adaptiveThreshold(blurred, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, block, 12)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    min_radius = min(height, width) * 0.0035
    max_radius = min(height, width) * 0.04
    candidates = []
    for contour in contours:
        (x, y), radius = cv2.minEnclosingCircle(contour)
        if not min_radius <= radius <= max_radius:
            continue
        area = cv2.contourArea(contour)
        if area < 0.6 * np.pi * radius * radius:  # dumaloq emas (harf, chiziq)
            continue
        mask = np.zeros_like(binary)
        cv2.drawContours(mask, [contour], -1, 255, -1)
        if cv2.mean(binary, mask=mask)[0] < 0.8 * 255:  # ichi bo'sh doiracha
            continue
        candidates.append((x, y, radius))
    return candidates


def _find_corners(gray: np.ndarray) -> np.ndarray:
    """
    Varaqning 4 ta burchak nuqtasi (chap-tepa, o'ng-tepa, o'ng-past, chap-past).

    Har bir burchakka eng yaqin bir nechta dog' olinadi va ulardan to'g'ri
    to'rtburchak (A4 nisbatida, o'lchami o'xshash dog'lar) hosil qiladigani tanlanadi —
    o'quvchi bo'yagan doirachalar yoki stol ustidagi dog'lar adashtirmasin.
    """

    candidates = _corner_candidates(gray)
    if len(candidates) < 4:
        raise ScanError(_CORNERS_HINT)
    height, width = gray.shape
    image_corners = [(0, 0), (width, 0), (width, height), (0, height)]
    nearest = [
        sorted(candidates, key=lambda c, corner=corner: (c[0] - corner[0]) ** 2 + (c[1] - corner[1]) ** 2)[:8]
        for corner in image_corners
    ]
    expected_ratio = (PAGE_WIDTH - 2 * CORNER_OFFSET) / (PAGE_HEIGHT - 2 * CORNER_OFFSET)

    # Shakli A4'ga yetarlicha mos kombinatsiyalar orasidan ENG KATTA maydonlisi olinadi:
    # haqiqiy burchak nuqtalari varaqdagi hamma narsani (bo'yalgan doirachalarni ham) o'rab turadi.
    best, best_area = None, 0.0
    for combo in itertools.product(*nearest):
        if len({(round(c[0]), round(c[1])) for c in combo}) < 4:
            continue
        points = np.array([(c[0], c[1]) for c in combo], dtype=np.float32)
        top = np.linalg.norm(points[1] - points[0])
        bottom = np.linalg.norm(points[2] - points[3])
        left = np.linalg.norm(points[3] - points[0])
        right = np.linalg.norm(points[2] - points[1])
        if min(top, bottom, left, right) < min(width, height) * 0.25:
            continue
        ratio = ((top + bottom) / 2) / ((left + right) / 2)
        radii = [c[2] for c in combo]
        size_spread = (max(radii) - min(radii)) / max(radii)
        # Qavariq to'rtburchak bo'lishi shart (nuqtalar tartibi to'g'ri)
        if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
            continue
        if abs(ratio - expected_ratio) / expected_ratio > 0.3 or size_spread > 0.45:
            continue
        area = cv2.contourArea(points)
        if area > best_area:
            best, best_area = points, area
    if best is None:
        raise ScanError(_CORNERS_HINT)
    return best


_CORNERS_HINT = (
    "Varaq burchaklaridagi 4 ta qora nuqta topilmadi. Varaqni to'liq (4 burchagi ko'rinadigan), "
    "tekis va yorug' joyda, tepadan suratga oling."
)


def _warp(image: np.ndarray, corners: np.ndarray) -> np.ndarray:
    target = np.array(
        [
            (CORNER_OFFSET, CORNER_OFFSET),
            (PAGE_WIDTH - CORNER_OFFSET, CORNER_OFFSET),
            (PAGE_WIDTH - CORNER_OFFSET, PAGE_HEIGHT - CORNER_OFFSET),
            (CORNER_OFFSET, PAGE_HEIGHT - CORNER_OFFSET),
        ],
        dtype=np.float32,
    ) * SCALE
    matrix = cv2.getPerspectiveTransform(corners, target)
    size = (round(PAGE_WIDTH * SCALE), round(PAGE_HEIGHT * SCALE))
    return cv2.warpPerspective(image, matrix, size, flags=cv2.INTER_LINEAR, borderValue=(255, 255, 255))


def _fill_ratio(binary: np.ndarray, x: float, y: float, radius: float) -> float:
    """
    Doiracha ichining qoraygan ulushi. Chizilgan chegarasi hisobga olinmaydi (ichki 55%).
    Qog'oz biroz egilgan bo'lsa ham topilishi uchun yaqin atrof (radiusning 25% igacha) ham tekshiriladi.
    """

    inner = max(2, round(radius * SCALE * 0.55))
    best = 0.0
    shift = radius * 0.25
    for dx, dy in itertools.product((-shift, -shift / 2, 0, shift / 2, shift), repeat=2):
        cx, cy = round((x + dx) * SCALE), round((y + dy) * SCALE)
        patch = binary[cy - inner: cy + inner + 1, cx - inner: cx + inner + 1]
        if patch.size == 0:
            continue
        yy, xx = np.ogrid[-inner: inner + 1, -inner: inner + 1]
        disk = (xx * xx + yy * yy) <= inner * inner
        disk = disk[: patch.shape[0], : patch.shape[1]]
        best = max(best, float((patch[disk] > 0).mean()))
    return best


def scan_answer_sheet(data: bytes, bubbles: list[dict], page: int = 1) -> dict:
    """
    Surat + varaqdagi doirachalar ro'yxati (generate_answer_sheet_pdf'dan) → o'qilgan javoblar.

    Qaytaradi:
      answers  — {savol_kaliti: "A"} (ko'p javobli savolda "A,C"), faqat aniq o'qilganlari;
      questions — har savol: label, tanlangan harflar, holati (ok / empty / multiple / unsure);
      name_image — ism yozilgan katak (PNG, data URL) — o'qituvchi ismni ko'rib yozishi uchun.
    """

    image = _load_image(data)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    corners = _find_corners(gray)
    warped = _warp(image, corners)
    warped_gray = cv2.cvtColor(warped, cv2.COLOR_BGR2GRAY)
    warped_gray = cv2.GaussianBlur(warped_gray, (3, 3), 0)
    binary = cv2.adaptiveThreshold(
        warped_gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, round(40 * SCALE) | 1, 18
    )

    questions: dict = {}
    id_rows: dict[str, dict[str, float]] = {}
    for bubble in bubbles:
        if bubble["page"] != page:
            continue
        # PDF koordinatasi pastdan, rasm esa tepadan sanaladi
        ratio = _fill_ratio(binary, bubble["x"], PAGE_HEIGHT - bubble["y"], bubble["r"])
        if bubble["kind"] == "id":
            id_rows.setdefault(bubble["ref"], {})[bubble["letter"]] = ratio
            continue
        entry = questions.setdefault(bubble["ref"], {
            "ref": bubble["ref"], "label": bubble["label"], "kind": bubble["kind"], "options": [], "ratios": {},
        })
        entry["options"].append(bubble["letter"])
        entry["ratios"][bubble["letter"]] = ratio

    answers: dict[str, str] = {}
    result = []
    for entry in questions.values():
        ratios = entry.pop("ratios")
        filled = [letter for letter in entry["options"] if ratios[letter] >= FILLED_RATIO]
        unsure = [letter for letter in entry["options"] if UNSURE_RATIO <= ratios[letter] < FILLED_RATIO]
        if entry["kind"] == "multi":
            status = "unsure" if unsure else ("ok" if filled else "empty")
        elif len(filled) > 1:
            status = "multiple"  # bir nechta doiracha bo'yalgan — o'qituvchi hal qiladi
        elif filled:
            status = "unsure" if unsure else "ok"
        else:
            status = "unsure" if unsure else "empty"
        chosen = ",".join(filled) if (entry["kind"] == "multi" or len(filled) == 1) else ""
        if chosen:
            answers[str(entry["ref"])] = chosen
        result.append({**entry, "chosen": chosen, "status": status})

    name_image = None
    if page == 1:
        x1, y1, x2, y2 = (round(value * SCALE) for value in NAME_BOX)
        crop = warped[max(0, y1 - 6): y2 + 6, max(0, x1 - 6): x2 + 6]
        ok, png = cv2.imencode(".png", crop)
        if ok:
            name_image = "data:image/png;base64," + base64.b64encode(png.tobytes()).decode()

    # O'quvchi raqami: har qatorda (yuzlik, o'nlik, birlik) aniq bitta doiracha bo'lishi kerak
    sheet_number, number_status = None, "none"
    if id_rows:
        digits = []
        for ref in sorted(id_rows):
            filled = [digit for digit, ratio in id_rows[ref].items() if ratio >= FILLED_RATIO]
            digits.append(filled[0] if len(filled) == 1 else None)
        if all(digit is None for digit in digits):
            number_status = "empty"
        elif any(digit is None for digit in digits) or int("".join(digits)) == 0:
            number_status = "unclear"
        else:
            sheet_number, number_status = int("".join(digits)), "ok"

    return {"answers": answers, "questions": result, "name_image": name_image,
            "sheet_number": sheet_number, "sheet_number_status": number_status}
