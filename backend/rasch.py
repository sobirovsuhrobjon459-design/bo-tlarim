"""
Rasch modeli bo'yicha ball — Milliy sertifikat uslubida.

Oddiy foizdan farqi: qiyin savolni yechgan o'quvchi oson savolni yechgandan
ko'proq ball oladi. Savolning qiyinligi esa shu testni ishlaganlarning
natijalaridan o'zi aniqlanadi (ko'pchilik yecha olmagan savol — qiyin).

Hisoblash (JMLE — qo'shma maksimal ehtimollik):
  1. Hamma yechgan yoki hech kim yechmagan savollar va hammasini to'g'ri yoki
     hammasini xato qilgan o'quvchilar baholashdan chiqarib turiladi — ular
     haqida model hech narsa ayta olmaydi.
  2. O'quvchi qobiliyati (θ) va savol qiyinligi (b) navbatma-navbat
     aniqlashtiriladi. Rasch modelida θ faqat to'g'ri javoblar soniga bog'liq,
     shuning uchun bir xil natijali o'quvchilar bitta guruhda hisoblanadi —
     2500 kishilik testda ham bir zumda tugaydi.
  3. Chetki natijalar (0 yoki hammasi to'g'ri) uchun θ odatdagidek 0.3 ga
     surilgan ball bo'yicha baholanadi.
  4. θ ballga aylantiriladi (UZBMB, Milliy sertifikat): Z = (θ − μ) / σ,
     T = 50 + 10·Z. O'rtacha ball ~50 (A+ — 70 va yuqori). C (46) ga
     yetmagan natija — NC (sertifikatsiz).
     Qatnashchilar juda kam bo'lsa (< MIN_FOR_STANDARDIZE), z o'rniga θ
     ishlatiladi (savollar qiyinligi o'rtachasi = 0 ga nisbatan).
"""

from __future__ import annotations

import math

MAX_ITERATIONS = 300
TOLERANCE = 1e-5
MAX_STEP = 1.0
EXTREME_ADJUSTMENT = 0.3
MIN_FOR_STANDARDIZE = 5
MAX_SCORE = 100.0  # yuqori chegara (mock natijalari uslubida; A+ — 70 va yuqori)

# Milliy sertifikat darajalari (ball bo'yicha, yuqoridan pastga)
GRADES = [(70, "A+"), (65, "A"), (60, "B+"), (55, "B"), (50, "C+"), (46, "C")]


def grade_for(score: float) -> str:
    for threshold, grade in GRADES:
        if score >= threshold:
            return grade
    return "NC"  # C darajasiga yetmagan


def _sigmoid(value: float) -> float:
    if value >= 0:
        return 1.0 / (1.0 + math.exp(-value))
    exp_value = math.exp(value)
    return exp_value / (1.0 + exp_value)


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


def _estimate_theta(raw_score: float, difficulties: list[float], start: float = 0.0) -> float:
    """Berilgan qiyinliklarda shu ballga mos qobiliyat (Nyuton usuli)."""

    theta = start
    for _ in range(MAX_ITERATIONS):
        probabilities = [_sigmoid(theta - b) for b in difficulties]
        expected = sum(probabilities)
        variance = sum(p * (1 - p) for p in probabilities)
        if variance < 1e-12:
            break
        step = _clamp((raw_score - expected) / variance, MAX_STEP)
        theta += step
        if abs(step) < TOLERANCE:
            break
    return theta


def rasch_scores(matrix: list[list[int]]) -> dict:
    """
    matrix[n][i] — n-o'quvchi i-savolni to'g'ri yechgan (1) yoki yo'q (0).

    Qaytaradi:
      "thetas"  — har bir o'quvchining qobiliyati (logit),
      "scores"  — har bir o'quvchining balli (0..100, ikki xona aniqlikda),
      "difficulties" — har bir savolning qiyinligi (logit; chetki savollar
                       uchun taxminiy qiymat).
    """

    persons = len(matrix)
    items = len(matrix[0]) if persons else 0
    if not persons or not items:
        return {"thetas": [0.0] * persons, "scores": [50.0] * persons, "difficulties": [0.0] * items}

    # 1) Chetki savol va o'quvchilarni bosqichma-bosqich chiqarib tashlash
    kept_items = list(range(items))
    kept_persons = list(range(persons))
    while True:
        item_totals = {i: sum(matrix[n][i] for n in kept_persons) for i in kept_items}
        new_items = [i for i in kept_items if 0 < item_totals[i] < len(kept_persons)]

        person_totals = {n: sum(matrix[n][i] for i in new_items) for n in kept_persons}
        new_persons = [n for n in kept_persons if 0 < person_totals[n] < len(new_items)]

        if new_items == kept_items and new_persons == kept_persons:
            break
        kept_items, kept_persons = new_items, new_persons
        if not kept_items or not kept_persons:
            break

    # Ma'lumot juda kam (masalan, bitta o'quvchi) — qiyinlikni aniqlab bo'lmaydi.
    # Shunda hamma savol bir xil qiyin deb olinadi va ball to'g'ri javoblar
    # soniga qarab chiqadi (aks holda hammasi 50 bo'lib qolardi).
    estimable = len(kept_items) >= 2 and bool(kept_persons)
    if not estimable:
        kept_items = list(range(items))

    item_count = len(kept_items)
    difficulties = {i: 0.0 for i in kept_items}

    # 2) JMLE — faqat baholanadigan qism bo'yicha, o'quvchilar ball guruhlarida
    if estimable:
        score_counts: dict[int, int] = {}
        for n in kept_persons:
            raw = sum(matrix[n][i] for i in kept_items)
            score_counts[raw] = score_counts.get(raw, 0) + 1
        item_correct = {i: sum(matrix[n][i] for n in kept_persons) for i in kept_items}

        thetas_by_score = {r: math.log(r / (item_count - r)) for r in score_counts}

        for _ in range(MAX_ITERATIONS):
            largest_change = 0.0

            for r in score_counts:
                probabilities = [_sigmoid(thetas_by_score[r] - difficulties[i]) for i in kept_items]
                variance = sum(p * (1 - p) for p in probabilities)
                if variance < 1e-12:
                    continue
                step = _clamp((r - sum(probabilities)) / variance, MAX_STEP)
                thetas_by_score[r] += step
                largest_change = max(largest_change, abs(step))

            for i in kept_items:
                expected = 0.0
                variance = 0.0
                for r, count in score_counts.items():
                    p = _sigmoid(thetas_by_score[r] - difficulties[i])
                    expected += count * p
                    variance += count * p * (1 - p)
                if variance < 1e-12:
                    continue
                step = _clamp((expected - item_correct[i]) / variance, MAX_STEP)
                difficulties[i] += step
                largest_change = max(largest_change, abs(step))

            # Savollar qiyinligi o'rtachasi 0 — shkala shunga bog'lanadi
            mean_difficulty = sum(difficulties.values()) / item_count
            for i in kept_items:
                difficulties[i] -= mean_difficulty

            if largest_change < TOLERANCE:
                break

    kept_difficulties = [difficulties[i] for i in kept_items]

    # 3) Har bir o'quvchining qobiliyati (chetkilari ham)
    thetas: list[float] = []
    theta_cache: dict[float, float] = {}
    for n in range(persons):
        if item_count == 0:
            thetas.append(0.0)
            continue
        raw = float(sum(matrix[n][i] for i in kept_items))
        raw = min(max(raw, EXTREME_ADJUSTMENT), item_count - EXTREME_ADJUSTMENT)
        if raw not in theta_cache:
            start = math.log(raw / (item_count - raw))
            theta_cache[raw] = _estimate_theta(raw, kept_difficulties, start)
        thetas.append(theta_cache[raw])

    # Hamma yechgan / hech kim yechmagan savollar — jadval uchun taxminiy qiyinlik
    low = min(kept_difficulties, default=0.0) - 1.0
    high = max(kept_difficulties, default=0.0) + 1.0
    all_difficulties = []
    for i in range(items):
        if i in difficulties:
            all_difficulties.append(difficulties[i])
        else:
            solved = sum(row[i] for row in matrix)
            all_difficulties.append(low if solved == persons else high)

    # 4) Ball: 50 + 10·z
    if persons >= MIN_FOR_STANDARDIZE and item_count:
        mean_theta = sum(thetas) / persons
        deviation = math.sqrt(sum((t - mean_theta) ** 2 for t in thetas) / persons)
    else:
        mean_theta, deviation = 0.0, 0.0

    scores = []
    for theta in thetas:
        z = (theta - mean_theta) / deviation if deviation > 1e-9 else theta
        scores.append(round(min(MAX_SCORE, max(0.0, 50 + 10 * z)), 2))

    return {"thetas": thetas, "scores": scores, "difficulties": all_difficulties}
