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
        # Doira yoki kvadrat (yangi titulda burchaklar kvadrat); harf va chiziqlar o'tmaydi
        if area < 0.5 * np.pi * radius * radius:
            continue
        # Ichi to'liq qorami (bo'sh doiracha emas) — faqat dog' atrofidagi kichik hudud tekshiriladi
        left, top, box_width, box_height = cv2.boundingRect(contour)
        mask = np.zeros((box_height, box_width), np.uint8)
        cv2.drawContours(mask, [contour], -1, 255, -1, offset=(-left, -top))
        if cv2.mean(binary[top: top + box_height, left: left + box_width], mask=mask)[0] < 0.8 * 255:
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


def _fill_ratio(binary: np.ndarray, x: float, y: float, radius: float, square: bool = False) -> float:
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
        disk = (xx * xx + yy * yy) <= inner * inner if not square else np.ones((2 * inner + 1, 2 * inner + 1), bool)
        disk = disk[: patch.shape[0], : patch.shape[1]]
        best = max(best, float((patch[disk] > 0).mean()))
    return best


def _disk(radius_px: int, inner: float, outer: float) -> tuple[np.ndarray, np.ndarray]:
    """Doiracha atrofidagi kvadrat ichida: halqa (chegara) va ichki doira niqoblari."""

    yy, xx = np.ogrid[-radius_px: radius_px + 1, -radius_px: radius_px + 1]
    distance = np.sqrt(xx * xx + yy * yy) / max(1, radius_px / 1.4)
    return (distance >= inner) & (distance <= outer), distance <= 0.55


class _ResponseMaps:
    """
    Har bir nuqta uchun: atrofidagi halqa (chop etilgan chegara) va ichki doira qanchalik
    qora — bir marta hisoblanadi (har doiracha o'lchami uchun), keyin faqat o'qiladi. Tez.
    """

    def __init__(self, binary: np.ndarray) -> None:
        self.binary = (binary > 0).astype(np.float32)
        self.cache: dict[float, tuple[np.ndarray, np.ndarray]] = {}

    def get(self, radius: float) -> tuple[np.ndarray, np.ndarray]:
        key = round(radius, 2)
        if key not in self.cache:
            ring, inner = _disk(round(radius * SCALE * 1.4), 0.82, 1.18)
            maps = []
            for mask in (ring, inner):
                kernel = mask.astype(np.float32) / mask.sum()
                maps.append(cv2.filter2D(self.binary, -1, kernel, borderType=cv2.BORDER_CONSTANT))
            self.cache[key] = (maps[0], maps[1])
        return self.cache[key]


def _sample(image: np.ndarray, xs: np.ndarray, ys: np.ndarray) -> np.ndarray:
    """Xaritadan qiymatlar (punktlardagi koordinatalar); chetdan chiqqanlari — 0."""

    columns = np.rint(xs * SCALE).astype(int)
    rows = np.rint(ys * SCALE).astype(int)
    inside = (columns >= 0) & (columns < image.shape[1]) & (rows >= 0) & (rows < image.shape[0])
    values = np.zeros(columns.shape, np.float32)
    values[inside] = image[rows[inside], columns[inside]]
    return values


def _align_row(maps: "_ResponseMaps", centers: list[tuple[float, float]], radius: float,
               start: tuple[float, float] = (0.0, 0.0)) -> tuple[float, float]:
    """
    Bitta savol qatori doirachalarini chop etilgan halqalari ustiga aniq joylashtiradi:
    qator butunligicha ±0,8 radius oralig'ida siljitib, halqalar eng qora bo'lgan joy olinadi.
    Qog'oz o'rtasidan bo'rtgan bo'lsa ham (chet belgilari buni ko'rmaydi) doiracha topiladi.
    """

    ring_map, _ = maps.get(radius)
    grid = np.arange(-5, 6) * radius * 0.16
    dx, dy = np.meshgrid(start[0] + grid, start[1] + grid)
    dx, dy = dx.ravel(), dy.ravel()
    points = np.array(centers, np.float32)
    scores = _sample(ring_map, points[:, 0][None, :] + dx[:, None], points[:, 1][None, :] + dy[:, None]).sum(axis=1)
    best = int(np.argmax(scores))
    return float(dx[best]), float(dy[best])


def _inner_fill(maps: "_ResponseMaps", x: float, y: float, radius: float) -> float:
    """Halqaga moslangan doiracha ichining qoraygan ulushi (chegarasi hisobga olinmaydi)."""

    _, inner_map = maps.get(radius)
    offsets = np.array([-0.1, 0, 0.1]) * radius
    dx, dy = np.meshgrid(offsets, offsets)
    return float(_sample(inner_map, x + dx.ravel(), y + dy.ravel()).max())


def _mark_offsets(binary: np.ndarray, marks: list[dict]) -> dict[str, list[tuple[float, float, float]]]:
    """
    Chetdagi tekislash belgilarining haqiqiy joyi: kutilgan joy atrofida qora kvadrat
    qidiriladi. Natija: {"left": [(y, dx, dy), ...], "right": [...]} — punktlarda.
    """

    offsets: dict[str, list[tuple[float, float, float]]] = {"left": [], "right": []}
    window = 9  # ±9 pt
    for mark in marks:
        x, y = mark["x"], PAGE_HEIGHT - mark["y"]
        x0, y0 = round((x - window) * SCALE), round((y - window) * SCALE)
        patch = binary[max(0, y0): round((y + window) * SCALE), max(0, x0): round((x + window) * SCALE)]
        if patch.size == 0:
            continue
        count, labels, stats, centroids = cv2.connectedComponentsWithStats(patch, connectivity=8)
        expected_area = (2 * mark["r"] * SCALE) ** 2
        best = None
        for label in range(1, count):
            area = stats[label, cv2.CC_STAT_AREA]
            if not 0.4 * expected_area <= area <= 2.2 * expected_area:
                continue
            cx = centroids[label][0] / SCALE + max(0, x0) / SCALE
            cy = centroids[label][1] / SCALE + max(0, y0) / SCALE
            distance = (cx - x) ** 2 + (cy - y) ** 2
            if best is None or distance < best[0]:
                best = (distance, cx - x, cy - y)
        if best:
            offsets[mark["ref"]].append((y, best[1], best[2]))
    return offsets


def _interpolate(points: list[tuple[float, float, float]], y: float) -> tuple[float, float]:
    """Shu balandlikdagi siljish: eng yaqin ikki belgi orasida chiziqli."""

    if not points:
        return 0.0, 0.0
    points = sorted(points)
    if y <= points[0][0]:
        return points[0][1], points[0][2]
    if y >= points[-1][0]:
        return points[-1][1], points[-1][2]
    for (y1, dx1, dy1), (y2, dx2, dy2) in zip(points, points[1:]):
        if y1 <= y <= y2:
            t = (y - y1) / (y2 - y1) if y2 > y1 else 0
            return dx1 + (dx2 - dx1) * t, dy1 + (dy2 - dy1) * t
    return 0.0, 0.0


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

    # 3-versiya titul: chet belgilari bo'yicha mahalliy tuzatish (egilgan qog'oz)
    page_bubbles = [bubble for bubble in bubbles if bubble["page"] == page]
    offsets = _mark_offsets(binary, [bubble for bubble in page_bubbles if bubble["kind"] == "mark"])
    left_x, right_x = CORNER_OFFSET, PAGE_WIDTH - CORNER_OFFSET

    def corrected(x: float, y: float) -> tuple[float, float]:
        ldx, ldy = _interpolate(offsets["left"], y)
        rdx, rdy = _interpolate(offsets["right"], y)
        t = min(1.0, max(0.0, (x - left_x) / (right_x - left_x)))
        return x + ldx + (rdx - ldx) * t, y + ldy + (rdy - ldy) * t

    code_bits: dict[int, float] = {}
    questions: dict = {}
    id_rows: dict[str, dict[str, float]] = {}
    variant_ratios: dict[str, float] = {}
    # Har savol qatori (va raqam/variant qatori) chop etilgan halqalari bo'yicha tekislanadi
    rows: dict = {}
    for bubble in page_bubbles:
        if bubble["kind"] not in ("mark", "code"):
            rows.setdefault(bubble["ref"], []).append(bubble)
    # Qatorlar ustun bo'yicha tepadan pastga: har biri eng yaqin tekislangan qatorning
    # siljishidan boshlab qidiriladi — qog'oz silliq egiladi, kuchli bo'rtish ham "kuzatiladi"
    maps = _ResponseMaps(binary)
    row_shift: dict = {}
    aligned: list[tuple[float, float, tuple[float, float]]] = []
    for ref, row in sorted(rows.items(), key=lambda item: (round(item[1][0]["x"] / 40), -item[1][0]["y"])):
        centers = [corrected(bubble["x"], PAGE_HEIGHT - bubble["y"]) for bubble in row]
        x0, y0 = centers[0]
        near = [item for item in aligned if abs(item[0] - x0) < 40 and abs(item[1] - y0) < 60]
        start = min(near, key=lambda item: abs(item[1] - y0))[2] if near else (0.0, 0.0)
        row_shift[ref] = _align_row(maps, centers, row[0]["r"], start)
        aligned.append((x0, y0, row_shift[ref]))

    for bubble in page_bubbles:
        if bubble["kind"] == "mark":
            continue
        # PDF koordinatasi pastdan, rasm esa tepadan sanaladi
        x, y = corrected(bubble["x"], PAGE_HEIGHT - bubble["y"])
        if bubble["kind"] == "code":
            code_bits[int(bubble["letter"])] = _fill_ratio(binary, x, y, bubble["r"], square=True)
            continue
        dx, dy = row_shift[bubble["ref"]]
        ratio = _inner_fill(maps, x + dx, y + dy, bubble["r"])
        if bubble["kind"] == "id":
            id_rows.setdefault(bubble["ref"], {})[bubble["letter"]] = ratio
            continue
        if bubble["kind"] == "variant":
            variant_ratios[bubble["letter"]] = ratio
            continue
        entry = questions.setdefault(bubble["ref"], {
            "ref": bubble["ref"], "label": bubble["label"], "kind": bubble["kind"], "options": [], "ratios": {},
        })
        entry["options"].append(bubble["letter"])
        entry["ratios"][bubble["letter"]] = ratio

    answers: dict[str, str] = {}
    result = []
    for entry in questions.values():
        ratios = entry["ratios"]
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

    # Variant: aniq bitta doiracha bo'yalgan bo'lsa
    marked_variants = [name for name, ratio in variant_ratios.items() if ratio >= FILLED_RATIO]
    variant = marked_variants[0] if len(marked_variants) == 1 else None

    # Imtihon kodi (3-versiya): har katak aniq bo'yalgan yoki aniq bo'sh bo'lsa o'qiladi
    sheet_code = None
    if code_bits and all(ratio >= FILLED_RATIO or ratio < UNSURE_RATIO for ratio in code_bits.values()):
        sheet_code = sum(1 << bit for bit, ratio in code_bits.items() if ratio >= FILLED_RATIO)

    return {"answers": answers, "questions": result, "name_image": name_image,
            "sheet_number": sheet_number, "sheet_number_status": number_status, "variant": variant,
            "sheet_code": sheet_code}
