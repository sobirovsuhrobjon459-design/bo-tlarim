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
from dataclasses import dataclass

import cv2
import numpy as np
from PIL import Image, ImageOps
from reportlab.lib.pagesizes import A4

PAGE_WIDTH, PAGE_HEIGHT = A4
SCALE = 3.0  # tekislangan rasmda 1 punkt = 3 piksel
CORNER_OFFSET = 32  # burchak nuqtalari markazi chetdan 32 punktda (qarang: _draw_corner_marks)
CORNER_RADIUS = 7
EDGE_MARKS = 10  # chap/o'ng chetdagi tekislash belgilari (qarang: SHEET_EDGE_MARKS)
EDGE_MARK_SIZE = 8
EDGE_SCORE = 0.8  # chet bo'lishi mumkin bo'lgan chiziq: belgilari va oralig'ining shuncha qismi joyida
MARKS_SCORE = 0.9  # to'rtburchak haqiqiy varaq: aniq joylari bo'yicha shuncha qismi mos

# Doiracha ichining qancha qismi qoraygan bo'lsa "bo'yalgan" hisoblanadi
FILLED_RATIO = 0.5
UNSURE_RATIO = 0.25

# Ism yoziladigan katakning chap qismi (birinchi sahifa, punktlarda, tepadan sanalganda):
# ism odatda shu yerda — telefonda kattaroq, o'qilishi oson ko'rinadi
NAME_BOX = (120, 80, 420, 100)


@dataclass(frozen=True)
class SheetPage:
    """
    Titul o'lchami (punktlarda). 6-versiya ixcham titul A4 ning 1/3, 1/2 qismi yoki to'liq A4
    bo'ladi (savollar soniga qarab) — burchak va chet belgilari shu titulning o'z chetida.
    """

    width: float = PAGE_WIDTH
    height: float = PAGE_HEIGHT
    edge_marks: int = EDGE_MARKS
    name_box: bool = True  # ism yoziladigan katak bor (ixcham titulda yo'q)
    name_area: tuple[float, float, float, float] | list[float] = NAME_BOX  # ism katagi (tepadan, punktlarda)


A4_PAGE = SheetPage()


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


def _edge_marks_score(gray: np.ndarray, points: np.ndarray, sheet: SheetPage = A4_PAGE) -> float:
    """
    3-versiya titulda burchaklar orasida chap va o'ng chetda 10 tadan kvadrat bor.
    To'rtta nuqta haqiqatan varaq burchaklari bo'lsa, shu kvadratlar aynan kutilgan
    joyda qora, ularning orasi esa oq qog'oz bo'ladi. Natija: mos kelgan nuqtalar ulushi (0..1).
    """

    page_corners = np.array([
        (CORNER_OFFSET, CORNER_OFFSET), (sheet.width - CORNER_OFFSET, CORNER_OFFSET),
        (sheet.width - CORNER_OFFSET, sheet.height - CORNER_OFFSET), (CORNER_OFFSET, sheet.height - CORNER_OFFSET),
    ], np.float32)
    matrix = cv2.getPerspectiveTransform(page_corners, points)
    step = (sheet.height - 2 * CORNER_OFFSET) / (sheet.edge_marks + 1)
    sides = (CORNER_OFFSET, sheet.width - CORNER_OFFSET)
    marks = [(x, CORNER_OFFSET + index * step) for index in range(1, sheet.edge_marks + 1) for x in sides]
    # Belgilar orasi butunlay oq bo'lishi kerak (doirachalar ustuni bunday emas — u yerda qatorlar zich)
    gaps = [(x, CORNER_OFFSET + (index + part) * step)
            for index in range(sheet.edge_marks + 1) for part in (0.3, 0.5, 0.7) for x in sides]
    # Tepadagi ikki burchak orasi ham bo'sh (sarlavha pastroqda)
    gaps += [(CORNER_OFFSET + 30 + index * (sheet.width - 2 * CORNER_OFFSET - 60) / 11, CORNER_OFFSET)
             for index in range(12)]
    projected = cv2.perspectiveTransform(np.array([marks + gaps + page_corners.tolist()], np.float32), matrix)[0]

    side_px = (np.linalg.norm(points[3] - points[0]) + np.linalg.norm(points[2] - points[1])) / 2
    radius = max(1, round(EDGE_MARK_SIZE / 2 * 0.6 * side_px / (sheet.height - 2 * CORNER_OFFSET)))
    height, width = gray.shape

    def window(x: float, y: float) -> np.ndarray:
        cx, cy = round(x), round(y)
        return gray[max(0, cy - radius): min(height, cy + radius + 1), max(0, cx - radius): min(width, cx + radius + 1)]

    # O'rtacha yorqinlik: belgi ichi to'liq qora (bo'sh doirachaning esa faqat chegarasi qora, o'rtasi oq)
    values = [float(patch.mean()) if (patch := window(x, y)).size else 255.0 for x, y in projected]
    mark_values = values[: len(marks)]
    gap_values = values[len(marks): len(marks) + len(gaps)]
    corner_values = values[len(marks) + len(gaps):]
    paper, dark = float(np.median(gap_values)), float(np.median(corner_values))
    if paper - dark < 30:
        return 0.0
    threshold = (paper + dark) / 2
    hits = sum(value < threshold for value in mark_values) + sum(value > threshold for value in gap_values)
    return hits / (len(mark_values) + len(gap_values))


def _marked_edges(gray: np.ndarray, candidates: list[tuple[float, float, float]],
                  sheet: SheetPage = A4_PAGE) -> list[tuple]:
    """
    Varaqning chap/o'ng cheti bo'lishi mumkin bo'lgan dog'lar juftlari: orasida 10 ta
    chet belgisi teng oraliqda qatorlashib turadi. Kadrda qayerda bo'lishidan qat'i nazar
    topiladi — kadr burchagiga yaqinroq begona narsalar xalaqit bermaydi.
    Natija: [(ball, tepadagi_dog', pastdagi_dog'), ...].
    """

    if len(candidates) < 2 or len(candidates) > 400:
        return []
    points = np.array(candidates, np.float64)
    first, second = np.triu_indices(len(points), 1)
    swap = points[first, 1] > points[second, 1]
    top = np.where(swap, second, first)
    bottom = np.where(swap, first, second)
    length = np.hypot(*(points[bottom, :2] - points[top, :2]).T)
    radius = (points[top, 2] + points[bottom, 2]) / 2
    expected = radius * (sheet.height - 2 * CORNER_OFFSET) / CORNER_RADIUS / 1.2  # radius ~ 1,2 x yarim tomon
    spread = np.abs(points[top, 2] - points[bottom, 2]) / np.maximum(points[top, 2], points[bottom, 2])
    keep = (spread <= 0.45) & (length >= expected * 0.45) & (length <= expected * 2.2)
    top, bottom, radius = top[keep], bottom[keep], radius[keep]
    if not len(top):
        return []

    height, width = gray.shape
    edges = []
    # Belgi kichik: atrofidagi eng qora nuqta olinadi (ozgina siljigan bo'lsa ham topilsin)
    # Oyna ±0,8 radius: bukilgan qog'ozda belgi to'g'ri chiziqdan biroz chetga suriladi
    for kernel in sorted({max(3, 2 * round(r * 0.8) + 1) for r in radius}):
        darkest = cv2.erode(gray, np.ones((kernel, kernel), np.uint8))
        average = cv2.blur(gray, (kernel, kernel))
        group = np.array([max(3, 2 * round(r * 0.8) + 1) == kernel for r in radius])
        start, end = points[top[group], :2], points[bottom[group], :2]
        # Perspektiva: varaqning yaqinroq uchi kattaroq ko'rinadi — belgilar orasi ham shunga qarab
        # o'zgaradi. Uchlaridagi dog'lar o'lchamidan taxmin qilinadi (u -> t proyektiv moslik).
        near = np.sqrt(points[top[group], 2] / points[bottom[group], 2])[:, None]
        steps = sheet.edge_marks + 1

        def sample(image: np.ndarray, u: np.ndarray) -> np.ndarray:
            t = u[None, :] * near / ((1 - u[None, :]) + u[None, :] * near)
            xy = start[:, None, :] + (end - start)[:, None, :] * t[..., None]
            columns = np.clip(np.rint(xy[..., 0]).astype(int), 0, width - 1)
            rows = np.clip(np.rint(xy[..., 1]).astype(int), 0, height - 1)
            return image[rows, columns].astype(np.float32)

        # Belgi joyi atrofida bir necha nuqta (taxmin aniq emas); belgilar orasi — uch joyda oq bo'lishi shart
        jitter = np.linspace(-0.15, 0.15, 5)
        mark_u = (np.arange(1, steps)[:, None] + jitter[None, :]).ravel() / steps
        gap_u = (np.arange(steps)[:, None] + np.array([0.35, 0.5, 0.65])[None, :]).ravel() / steps
        marks = sample(darkest, mark_u).reshape(len(start), sheet.edge_marks, len(jitter)).min(axis=2)
        gaps = sample(average, gap_u)
        ends = np.minimum(sample(average, np.array([0.0])), sample(average, np.array([1.0])))[:, 0]
        paper = np.median(gaps, axis=1)
        threshold = (paper + ends) / 2
        score = ((marks < threshold[:, None]).sum(axis=1) + (gaps > threshold[:, None]).sum(axis=1)) / (
            sheet.edge_marks + gaps.shape[1]
        )
        score[paper - ends < 30] = 0
        for index in np.nonzero(score >= EDGE_SCORE)[0]:
            edges.append((float(score[index]), candidates[top[group][index]], candidates[bottom[group][index]]))
    # Haqiqiy chet — eng uzunlaridan (burchaklar varaqdagi hamma narsani o'rab turadi)
    # Uchlarida katta burchak kvadratlari (doirachadan ~1,5 baravar katta) bo'lgani oldin — bo'yalgan doirachalar
    # ustuni ham "chet"ga o'xshaydi, ular haqiqiy chetni ro'yxatdan siqib chiqarmasin
    edges.sort(key=lambda edge: (-round(edge[0], 1), -round(edge[1][2] + edge[2][2]),
                                 -np.hypot(edge[2][0] - edge[1][0], edge[2][1] - edge[1][1])))
    return edges[:60]


def _nearly_rectangular(points: np.ndarray) -> bool:
    """Har burchagi 90° ga yaqin (±35°) — suratga qiya olinganda ham varaq shunday ko'rinadi."""

    for index in range(4):
        before, here, after = points[index - 1], points[index], points[(index + 1) % 4]
        a, b = before - here, after - here
        cosine = float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
        if abs(cosine) > np.cos(np.radians(55)):
            return False
    return True


def _corners_from_edges(gray: np.ndarray, candidates: list[tuple[float, float, float]],
                        sheet: SheetPage = A4_PAGE) -> np.ndarray | None:
    """Ikkita belgili chetdan (chap va o'ng) varaq; chet belgilari hammasi joyida bo'lgani olinadi."""

    expected_ratio = (sheet.width - 2 * CORNER_OFFSET) / (sheet.height - 2 * CORNER_OFFSET)
    edges = _marked_edges(gray, candidates, sheet)
    shapes = []
    for (_, top_a, bottom_a), (_, top_b, bottom_b) in itertools.combinations(edges, 2):
        if {top_a, bottom_a} & {top_b, bottom_b}:
            continue
        if top_a[0] + bottom_a[0] > top_b[0] + bottom_b[0]:
            top_a, bottom_a, top_b, bottom_b = top_b, bottom_b, top_a, bottom_a
        combo = (top_a, top_b, bottom_b, bottom_a)
        points = np.array([(c[0], c[1]) for c in combo], dtype=np.float32)
        top = np.linalg.norm(points[1] - points[0])
        bottom = np.linalg.norm(points[2] - points[3])
        left = np.linalg.norm(points[3] - points[0])
        right = np.linalg.norm(points[2] - points[1])
        ratio = ((top + bottom) / 2) / ((left + right) / 2)
        radii = [c[2] for c in combo]
        if abs(ratio - expected_ratio) / expected_ratio > 0.3 or (max(radii) - min(radii)) / max(radii) > 0.45:
            continue
        # Haqiqiy to'rtburchak: bir chiziqda yotgan nuqtalar emas, qarama-qarshi tomonlari deyarli teng
        if cv2.contourArea(points) < 0.6 * (top + bottom) / 2 * (left + right) / 2:
            continue
        if max(top, bottom) > 1.5 * min(top, bottom) or max(left, right) > 1.5 * min(left, right):
            continue
        if not _nearly_rectangular(points):
            continue
        if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
            continue
        shapes.append((cv2.contourArea(points), points))
    # Kattasidan boshlab: haqiqiy burchaklar varaqdagi hamma narsani o'rab turadi
    best_score, best = 0.0, None
    for _, points in sorted(shapes, key=lambda shape: -shape[0]):
        score = _edge_marks_score(gray, points, sheet)
        if score > best_score:
            best_score, best = score, points
        if score >= 0.95:
            break
    return best if best_score >= MARKS_SCORE else None


def _find_corners(gray: np.ndarray, edge_marks: bool = False, sheet: SheetPage = A4_PAGE) -> np.ndarray:
    """
    Varaqning 4 ta burchak nuqtasi (chap-tepa, o'ng-tepa, o'ng-past, chap-past).

    Har bir burchakka eng yaqin bir nechta dog' olinadi va ulardan to'g'ri
    to'rtburchak (A4 nisbatida, o'lchami o'xshash dog'lar) hosil qiladigani tanlanadi —
    o'quvchi bo'yagan doirachalar yoki stol ustidagi dog'lar adashtirmasin.
    Titulda chet belgilari bo'lsa (edge_marks), ular joyida turgan to'rtlik olinadi:
    varaq atrofidagi qora narsalar (stol, qo'l, boshqa qog'oz) burchak bo'lib qolmaydi.
    """

    candidates = _corner_candidates(gray)
    if len(candidates) < 4:
        raise ScanError(_CORNERS_HINT)
    if edge_marks:
        found = _corners_from_edges(gray, candidates, sheet)
        if found is not None:
            return found
    height, width = gray.shape
    image_corners = [(0, 0), (width, 0), (width, height), (0, height)]
    nearest = [
        sorted(candidates, key=lambda c, corner=corner: (c[0] - corner[0]) ** 2 + (c[1] - corner[1]) ** 2)[:8]
        for corner in image_corners
    ]
    expected_ratio = (sheet.width - 2 * CORNER_OFFSET) / (sheet.height - 2 * CORNER_OFFSET)
    min_side = min(width, height) * 0.25

    # Shakli A4'ga yetarlicha mos kombinatsiyalar orasidan ENG KATTA maydonlisi olinadi:
    # haqiqiy burchak nuqtalari varaqdagi hamma narsani (bo'yalgan doirachalarni ham) o'rab turadi.
    shapes = []
    for combo in itertools.product(*nearest):
        if len({(round(c[0]), round(c[1])) for c in combo}) < 4:
            continue
        (x0, y0, _), (x1, y1, _), (x2, y2, _), (x3, y3, _) = combo
        top = np.hypot(x1 - x0, y1 - y0)
        bottom = np.hypot(x2 - x3, y2 - y3)
        left = np.hypot(x3 - x0, y3 - y0)
        right = np.hypot(x2 - x1, y2 - y1)
        if min(top, bottom, left, right) < min_side:
            continue
        ratio = ((top + bottom) / 2) / ((left + right) / 2)
        radii = [c[2] for c in combo]
        size_spread = (max(radii) - min(radii)) / max(radii)
        if abs(ratio - expected_ratio) / expected_ratio > 0.3 or size_spread > 0.45:
            continue
        points = np.array([(c[0], c[1]) for c in combo], dtype=np.float32)
        # Qavariq to'rtburchak bo'lishi shart (nuqtalar tartibi to'g'ri)
        if not cv2.isContourConvex(points.reshape(-1, 1, 2)):
            continue
        shapes.append((cv2.contourArea(points), points))
    if not shapes:
        raise ScanError(_CORNERS_HINT)
    shapes.sort(key=lambda shape: -shape[0])
    if edge_marks:
        best_score, best = 0.0, None
        for _, points in shapes[:300]:
            score = _edge_marks_score(gray, points, sheet)
            if score > best_score:
                best_score, best = score, points
        if best_score >= MARKS_SCORE:
            return best
        # Chet belgilari joyida emas — bu varaq burchaklari emas (noto'g'ri javob o'qigandan ko'ra xato yaxshi)
        raise ScanError(_CORNERS_HINT)
    return shapes[0][1]


_CORNERS_HINT = (
    "Varaq burchaklaridagi 4 ta qora nuqta topilmadi. Varaqni to'liq (4 burchagi ko'rinadigan), "
    "tekis va yorug' joyda, tepadan suratga oling."
)


def _warp(image: np.ndarray, corners: np.ndarray, sheet: SheetPage = A4_PAGE) -> np.ndarray:
    target = np.array(
        [
            (CORNER_OFFSET, CORNER_OFFSET),
            (sheet.width - CORNER_OFFSET, CORNER_OFFSET),
            (sheet.width - CORNER_OFFSET, sheet.height - CORNER_OFFSET),
            (CORNER_OFFSET, sheet.height - CORNER_OFFSET),
        ],
        dtype=np.float32,
    ) * SCALE
    matrix = cv2.getPerspectiveTransform(corners, target)
    size = (round(sheet.width * SCALE), round(sheet.height * SCALE))
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
               start: tuple[float, float] = (0.0, 0.0), reach: int = 5) -> tuple[float, float]:
    """
    Bitta savol qatori doirachalarini chop etilgan halqalari ustiga aniq joylashtiradi:
    qator butunligicha ±0,8 radius oralig'ida siljitib, halqalar eng qora bo'lgan joy olinadi.
    Qog'oz o'rtasidan bo'rtgan bo'lsa ham (chet belgilari buni ko'rmaydi) doiracha topiladi.
    """

    ring_map, _ = maps.get(radius)
    grid = np.arange(-reach, reach + 1) * radius * 0.16
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


def _mark_offsets(binary: np.ndarray, marks: list[dict],
                  sheet: SheetPage = A4_PAGE) -> dict[str, list[tuple[float, float, float]]]:
    """
    Chetdagi tekislash belgilarining haqiqiy joyi: kutilgan joy atrofida qora kvadrat
    qidiriladi. Natija: {"left": [(y, dx, dy), ...], "right": [...]} — punktlarda.
    """

    offsets: dict[str, list[tuple[float, float, float]]] = {"left": [], "right": []}
    for mark in marks:
        y = sheet.height - mark["y"]
        found = _locate_mark(binary, mark["x"], y, mark["r"], window=9)
        if found:
            offsets[mark["ref"]].append((y, *found))
    return offsets


def _locate_mark(binary: np.ndarray, x: float, y: float, half: float, window: float) -> tuple[float, float] | None:
    """Kutilgan joy (x, y) atrofidagi (±window pt) qora kvadrat: haqiqiy joyi farqi (dx, dy) yoki None."""

    x0, y0 = round((x - window) * SCALE), round((y - window) * SCALE)
    patch = binary[max(0, y0): round((y + window) * SCALE), max(0, x0): round((x + window) * SCALE)]
    if patch.size == 0:
        return None
    count, labels, stats, centroids = cv2.connectedComponentsWithStats(patch, connectivity=8)
    expected_area = (2 * half * SCALE) ** 2
    areas = stats[1:, cv2.CC_STAT_AREA]
    centers = centroids[1:] / SCALE + np.array([max(0, x0), max(0, y0)]) / SCALE
    fits = (areas >= 0.4 * expected_area) & (areas <= 2.2 * expected_area)
    if not fits.any():
        return None
    distance = np.where(fits, ((centers - (x, y)) ** 2).sum(axis=1), np.inf)
    cx, cy = centers[int(distance.argmin())]
    return float(cx - x), float(cy - y)


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


class PreparedSheet:
    """
    Tekislangan varaq (burchaklar topilgan, perspektiva to'g'rilangan). Bir suratni boshqa
    doirachalar joylashuvi bilan qayta o'qishda (masalan, eski 3 xonali ID) shu qism
    qaytadan hisoblanmaydi — eng og'ir ish shu yerda.
    """

    def __init__(self, data: bytes, bubbles: list[dict], page: int = 1, sheet: SheetPage = A4_PAGE) -> None:
        image = _load_image(data)
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        page_bubbles = [bubble for bubble in bubbles if bubble["page"] == page]
        self.edge_marks = any(bubble["kind"] == "mark" for bubble in page_bubbles)
        self.sheet = sheet
        corners = _find_corners(gray, edge_marks=self.edge_marks, sheet=sheet)
        self.warped = _warp(image, corners, sheet)
        warped_gray = cv2.cvtColor(self.warped, cv2.COLOR_BGR2GRAY)
        warped_gray = cv2.GaussianBlur(warped_gray, (3, 3), 0)
        self.binary = cv2.adaptiveThreshold(
            warped_gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY_INV, round(40 * SCALE) | 1, 18
        )
        self.maps = _ResponseMaps(self.binary)


def scan_answer_sheet(data: bytes, bubbles: list[dict], page: int = 1,
                      prepared: PreparedSheet | None = None, sheet: SheetPage = A4_PAGE) -> dict:
    """
    Surat + varaqdagi doirachalar ro'yxati (generate_answer_sheet_pdf'dan) → o'qilgan javoblar.

    prepared — shu surat uchun avval tayyorlangan PreparedSheet (qayta o'qishda tezroq).
    Faqat burchak/chet belgilari bir xil joylashgan varaq uchun beriladi.

    Qaytaradi:
      answers  — {savol_kaliti: "A"} (ko'p javobli savolda "A,C"), faqat aniq o'qilganlari;
      questions — har savol: label, tanlangan harflar, holati (ok / empty / multiple / unsure);
      name_image — ism yozilgan katak (PNG, data URL) — o'qituvchi ismni ko'rib yozishi uchun.
    """

    page_bubbles = [bubble for bubble in bubbles if bubble["page"] == page]
    if (prepared is None or prepared.sheet != sheet
            or prepared.edge_marks != any(bubble["kind"] == "mark" for bubble in page_bubbles)):
        prepared = PreparedSheet(data, bubbles, page, sheet)
    warped, binary = prepared.warped, prepared.binary

    # 3-versiya titul: chet belgilari bo'yicha mahalliy tuzatish (egilgan qog'oz)
    offsets = _mark_offsets(binary, [bubble for bubble in page_bubbles if bubble["kind"] == "mark"], sheet)
    left_x, right_x = CORNER_OFFSET, sheet.width - CORNER_OFFSET

    def corrected(x: float, y: float) -> tuple[float, float]:
        ldx, ldy = _interpolate(offsets["left"], y)
        rdx, rdy = _interpolate(offsets["right"], y)
        t = min(1.0, max(0.0, (x - left_x) / (right_x - left_x)))
        return x + ldx + (rdx - ldx) * t, y + ldy + (rdy - ldy) * t

    # 7-versiya: har 5 savollik blok burchagidagi kvadratlar — doiracha joyi o'z blokining
    # 4 kvadrati orasida (bilinear) aniqlanadi; topilmagan kvadrat o'rniga chet belgilari bo'yicha joy
    anchor_shift: dict[str, tuple[float, float, float, float]] = {}
    for anchor in page_bubbles:
        if anchor["kind"] != "anchor":
            continue
        x, y = anchor["x"], sheet.height - anchor["y"]
        cx, cy = corrected(x, y)
        found = _locate_mark(binary, cx, cy, anchor["r"], window=5) or (0.0, 0.0)
        anchor_shift[anchor["letter"]] = (x, y, cx + found[0] - x, cy + found[1] - y)

    def placed(bubble: dict) -> tuple[float, float]:
        x, y = bubble["x"], sheet.height - bubble["y"]
        corners = bubble.get("block")
        if not corners or any(corner not in anchor_shift for corner in corners):
            return corrected(x, y)
        top_left, top_right, bottom_right, bottom_left = (anchor_shift[corner] for corner in corners)
        u = (x - top_left[0]) / (top_right[0] - top_left[0])
        v = (y - top_left[1]) / (bottom_left[1] - top_left[1])
        weights = ((1 - u) * (1 - v), u * (1 - v), u * v, (1 - u) * v)
        shifts = (top_left, top_right, bottom_right, bottom_left)
        return (x + sum(w * shift[2] for w, shift in zip(weights, shifts)),
                y + sum(w * shift[3] for w, shift in zip(weights, shifts)))

    code_bits: dict[int, float] = {}
    questions: dict = {}
    id_rows: dict[str, dict[str, float]] = {}
    variant_ratios: dict[str, float] = {}
    # Har savol qatori (va raqam/variant qatori) chop etilgan halqalari bo'yicha tekislanadi
    rows: dict = {}
    for bubble in page_bubbles:
        if bubble["kind"] not in ("mark", "code", "anchor"):
            rows.setdefault(bubble["ref"], []).append(bubble)
    # Qatorlar ustun bo'yicha tepadan pastga: har biri eng yaqin tekislangan qatorning
    # siljishidan boshlab qidiriladi — qog'oz silliq egiladi, kuchli bo'rtish ham "kuzatiladi"
    maps = prepared.maps
    row_shift: dict = {}
    aligned: list[tuple[float, float, tuple[float, float]]] = []
    for ref, row in sorted(rows.items(), key=lambda item: (round(item[1][0]["x"] / 40), -item[1][0]["y"])):
        centers = [placed(bubble) for bubble in row]
        x0, y0 = centers[0]
        # Eng yaqin tekislangan qatordan boshlanadi; u uzoq bo'lsa — kengroq qidiriladi
        nearest = min(aligned, key=lambda item: (item[0] - x0) ** 2 + (item[1] - y0) ** 2, default=None)
        start = nearest[2] if nearest else (0.0, 0.0)
        far = nearest is None or abs(nearest[0] - x0) >= 40 or abs(nearest[1] - y0) >= 60
        row_shift[ref] = _align_row(maps, centers, row[0]["r"], start, reach=8 if far else 5)
        aligned.append((x0, y0, row_shift[ref]))

    for bubble in page_bubbles:
        if bubble["kind"] in ("mark", "anchor"):
            continue
        # PDF koordinatasi pastdan, rasm esa tepadan sanaladi
        x, y = placed(bubble)
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
    if page == 1 and sheet.name_box:
        x1, y1, x2, y2 = (round(value * SCALE) for value in sheet.name_area)
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
