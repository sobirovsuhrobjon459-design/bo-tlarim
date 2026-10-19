"""
Dars jadvalini avtomatik tuzish (bazaga tegmaydi — faqat hisob-kitob).

Kirish: sinflar (smena setkasi + o'quv reja), o'qituvchilarning ish kunlari/vaqti va
jadvalda qoladigan (o'zgarmaydigan) darslar. Chiqish: qo'yiladigan darslar va
joylasha olmagan soatlar (sababi bilan).

Qat'iy qoidalar (hech qachon buzilmaydi):
  - dars faqat sinf smenasidagi kun va dars raqamiga qo'yiladi;
  - sinfda bir vaqtda bitta dars;
  - o'qituvchi bir vaqtda bitta darsda (boshqa smenadagi darslari vaqt bo'yicha solishtiriladi);
  - o'qituvchining ish kunlari va ish vaqti.
Yaxshi jadval uchun (iloji boricha):
  - bir fan bir kunga ikki marta tushmaydi (soati kunlardan ko'p bo'lsa — teng taqsimlanadi);
  - sinf kunida "teshik" qolmaydi — darslar 1-darsdan ketma-ket;
  - sinf va o'qituvchining kunlik yuki teng.

Usul: eng qiyin joylashadigan darslardan boshlab ochko'z (greedy) joylashtirish, bir necha
marta turli tartibda (urug' bilan, natija har safar bir xil chiqadi) — eng yaxshisi olinadi.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

RESTART_BUDGET = 40_000  # jami urinishlar ~ shu songa / darslar soniga (katta maktabda ham bir necha soniya)


@dataclass
class Period:
    start: str
    end: str


@dataclass
class ClassInput:
    id: int
    name: str
    weekdays: list[int]
    periods: list[Period]
    plan: list[dict]  # [{"subject": str, "hours": int, "teacher_user_id": int | None}]
    kept: list[dict] = field(default_factory=list)  # qoladigan darslari: {"weekday", "period", "start", "end", "subject", "teacher_user_id"}


@dataclass
class TeacherInput:
    name: str
    work_days: set[int] | None = None  # None — cheklanmagan
    work_start: str | None = None
    work_end: str | None = None


def _overlaps(a_start: str, a_end: str, b_start: str, b_end: str) -> bool:
    return a_start < b_end and b_start < a_end


def build_timetable(
    classes: list[ClassInput],
    teachers: dict[int, TeacherInput],
    outside_busy: list[dict] | None = None,
    seed: int = 0,
) -> dict:
    """
    outside_busy — tanlanmagan sinflardagi darslar (o'qituvchi bandligi uchun):
    [{"teacher_user_id", "weekday", "start", "end"}].
    Qaytaradi: {"lessons": [...], "unplaced": [...], "gaps": int}
    """

    # O'qituvchi bandligi: kun -> [(start, end)]
    base_busy: dict[int, dict[int, list[tuple[str, str]]]] = {}

    def add_busy(store, teacher_id, weekday, start, end):
        store.setdefault(teacher_id, {}).setdefault(weekday, []).append((start, end))

    for item in outside_busy or []:
        if item.get("teacher_user_id"):
            add_busy(base_busy, item["teacher_user_id"], item["weekday"], item["start"], item["end"])
    for school_class in classes:
        for item in school_class.kept:
            if item.get("teacher_user_id"):
                add_busy(base_busy, item["teacher_user_id"], item["weekday"], item["start"], item["end"])

    # Joylashtiriladigan birliklar: (sinf, fan, o'qituvchi) × qolgan soat
    units: list[tuple[int, str, int | None]] = []
    class_by_id = {item.id: item for item in classes}
    for school_class in classes:
        placed: dict[str, int] = {}
        for item in school_class.kept:
            key = (item.get("subject") or "").lower()
            placed[key] = placed.get(key, 0) + 1
        for row in school_class.plan:
            missing = row["hours"] - placed.get(row["subject"].lower(), 0)
            units += [(school_class.id, row["subject"], row.get("teacher_user_id"))] * max(missing, 0)

    def teacher_allows(teacher_id, weekday, period: Period) -> bool:
        if not teacher_id or teacher_id not in teachers:
            return True
        teacher = teachers[teacher_id]
        if teacher.work_days and weekday not in teacher.work_days:
            return False
        if teacher.work_start and teacher.work_end and (period.start < teacher.work_start or period.end > teacher.work_end):
            return False
        return True

    # Har birlik uchun boshlang'ich imkoniyatlar soni — eng qiyinlari birinchi joylashadi
    def static_options(unit) -> int:
        class_id, _, teacher_id = unit
        school_class = class_by_id[class_id]
        return sum(
            1
            for day in school_class.weekdays
            for period in school_class.periods
            if teacher_allows(teacher_id, day, period)
        )

    tightness = {unit: static_options(unit) for unit in set(units)}
    teacher_total: dict[int, int] = {}
    for _, _, teacher_id in units:
        if teacher_id:
            teacher_total[teacher_id] = teacher_total.get(teacher_id, 0) + 1
    plan_hours = {(item.id, row["subject"]): row["hours"] for item in classes for row in item.plan}

    def kept_cells(school_class: ClassInput) -> set[tuple[int, int]]:
        """Qoladigan darslar egallagan kataklar; setkasiz (qo'lda vaqt kiritilgan) dars — vaqti to'g'ri kelgan katak."""
        cells = set()
        for item in school_class.kept:
            if item.get("period"):
                cells.add((item["weekday"], item["period"]))
                continue
            for index, period in enumerate(school_class.periods, start=1):
                if _overlaps(period.start, period.end, item["start"], item["end"]):
                    cells.add((item["weekday"], index))
        return cells

    fixed_cells = {item.id: kept_cells(item) for item in classes}

    def attempt(rng: random.Random) -> tuple[list[dict], list[tuple], int]:
        class_cells: dict[int, dict[tuple[int, int], bool]] = {
            item.id: {cell: True for cell in fixed_cells[item.id]} for item in classes
        }
        busy = {teacher: {day: list(spans) for day, spans in days.items()} for teacher, days in base_busy.items()}
        subject_day: dict[tuple[int, str, int], int] = {}
        class_day_load: dict[tuple[int, int], int] = {}
        teacher_day_load: dict[tuple[int, int], int] = {}
        for school_class in classes:
            for item in school_class.kept:
                key = (school_class.id, (item.get("subject") or "").lower(), item["weekday"])
                subject_day[key] = subject_day.get(key, 0) + 1
                class_day_load[(school_class.id, item["weekday"])] = class_day_load.get((school_class.id, item["weekday"]), 0) + 1

        order = list(units)
        rng.shuffle(order)
        order.sort(key=lambda unit: (tightness[unit], -teacher_total.get(unit[2] or 0, 0)))

        lessons, failed, penalty = [], [], 0
        for class_id, subject, teacher_id in order:
            school_class = class_by_id[class_id]
            hours = plan_hours[(class_id, subject)]
            per_day_limit = -(-hours // max(len(school_class.weekdays), 1))  # ceil
            best, best_score = None, None
            for day in school_class.weekdays:
                cells = class_cells[class_id]
                same_subject = subject_day.get((class_id, subject.lower(), day), 0)
                first_free = next((index for index in range(1, len(school_class.periods) + 1) if (day, index) not in cells), None)
                if first_free is None:
                    continue
                for index, period in enumerate(school_class.periods, start=1):
                    if (day, index) in cells or not teacher_allows(teacher_id, day, period):
                        continue
                    if teacher_id and any(_overlaps(period.start, period.end, s, e) for s, e in busy.get(teacher_id, {}).get(day, [])):
                        continue
                    score = (
                        (1000 if same_subject >= per_day_limit else 0)
                        + 120 * same_subject
                        + 90 * (index - first_free)  # teshik qoldirmaslik: birinchi bo'sh darsga yaqin
                        + 6 * class_day_load.get((class_id, day), 0)
                        + 3 * teacher_day_load.get((teacher_id or 0, day), 0)
                        + rng.random()
                    )
                    if best_score is None or score < best_score:
                        best, best_score = (day, index, period), score
            if best is None:
                failed.append((class_id, subject, teacher_id))
                continue
            day, index, period = best
            class_cells[class_id][(day, index)] = True
            if teacher_id:
                busy.setdefault(teacher_id, {}).setdefault(day, []).append((period.start, period.end))
                teacher_day_load[(teacher_id, day)] = teacher_day_load.get((teacher_id, day), 0) + 1
            subject_day[(class_id, subject.lower(), day)] = subject_day.get((class_id, subject.lower(), day), 0) + 1
            class_day_load[(class_id, day)] = class_day_load.get((class_id, day), 0) + 1
            penalty += best_score
            lessons.append({
                "class_id": class_id, "weekday": day, "period": index, "start_time": period.start,
                "end_time": period.end, "subject": subject, "teacher_user_id": teacher_id,
            })

        # Teshiklar: sinf kunida ikki dars orasidagi bo'sh dars
        gaps = 0
        for class_id, cells in class_cells.items():
            for day in class_by_id[class_id].weekdays:
                taken = sorted(index for (cell_day, index) in cells if cell_day == day)
                if taken:
                    gaps += (taken[-1] - taken[0] + 1) - len(taken)
        return lessons, failed, gaps * 500 + penalty, gaps

    rounds = max(4, min(60, RESTART_BUDGET // max(len(units), 1)))
    rng = random.Random(seed)
    best = None
    for _ in range(rounds):
        lessons, failed, score, gaps = attempt(random.Random(rng.random()))
        key = (len(failed), score)
        if best is None or key < best[0]:
            best = (key, lessons, failed, gaps)
        if not units:
            break
    _, lessons, failed, gaps = best

    # O'qituvchiga jami kerak soat va ish vaqtiga sig'adigan turli dars vaqtlari (barcha smenalar bo'yicha)
    teacher_demand: dict[int, int] = {}
    for school_class in classes:
        for row in school_class.plan:
            if row.get("teacher_user_id"):
                teacher_demand[row["teacher_user_id"]] = teacher_demand.get(row["teacher_user_id"], 0) + row["hours"]
    teacher_room: dict[int, int] = {}
    for teacher_id in teacher_demand:
        times = {(day, period.start, period.end) for item in classes for day in item.weekdays for period in item.periods
                 if teacher_allows(teacher_id, day, period)}
        teacher_room[teacher_id] = len(times)

    # Joylashmaganlarni guruhlab, sababini aytamiz
    missing: dict[tuple[int, str, int | None], int] = {}
    for unit in failed:
        missing[unit] = missing.get(unit, 0) + 1
    unplaced = []
    for (class_id, subject, teacher_id), count in missing.items():
        school_class = class_by_id[class_id]
        capacity = len(school_class.weekdays) * len(school_class.periods)
        needed = sum(row["hours"] for row in school_class.plan)
        teacher = teachers.get(teacher_id) if teacher_id else None
        demand, room = (teacher_demand.get(teacher_id, 0), teacher_room.get(teacher_id, 0)) if teacher_id else (0, 0)
        if needed > capacity:
            reason = f"rejada haftasiga {needed} soat, setkada esa {capacity} ta dars bor"
        elif teacher and tightness[(class_id, subject, teacher_id)] == 0:
            reason = f"{teacher.name}ning ish kunlari/vaqti bu sinf smenasiga to'g'ri kelmaydi"
        elif teacher and demand > room:
            reason = f"{teacher.name}ga rejada haftasiga {demand} soat, ish kunlari va vaqtiga esa {room} ta dars sig'adi"
        elif teacher:
            reason = f"{teacher.name}ning bo'sh vaqti boshqa sinflardagi darslar bilan band"
        else:
            reason = "sinf setkasida mos bo'sh joy qolmadi"
        unplaced.append({"class_id": class_id, "class_name": school_class.name, "subject": subject,
                         "teacher_user_id": teacher_id, "hours": count, "reason": reason})
    lessons.sort(key=lambda item: (item["class_id"], item["weekday"], item["period"]))
    return {"lessons": lessons, "unplaced": unplaced, "gaps": gaps}
