"""
Zaxira nusxadan (admin'ga Telegram orqali keladigan backup_*.json) bazani tiklash.

Ishlatish (loyihaning bosh papkasidan):

    # 1) DATABASE_URL — tiklanadigan (yangi, BO'SH) baza manzili
    # 2) Fayl — botdan kelgan zaxira nusxa
    python -m backend.restore_backup backup_2026-10-08_12-00.json

Xavfsizlik: baza bo'sh bo'lmasa, skript hech narsa yozmaydi (mavjud
ma'lumotlarni ustidan yozib, aralashtirib yubormaslik uchun). Ataylab
ustiga yozish kerak bo'lsa: --force (avval eski bazaning nusxasini oling!).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, func, insert, select, text

from .database import IS_SQLITE, Base, SessionLocal, engine


def _convert(value, column):
    """JSON'dagi matnni ustun turiga qaytaradi (sana/vaqt, mantiqiy qiymat)."""

    if value is None:
        return None
    column_type = column.type
    if isinstance(column_type, DateTime) and isinstance(value, str):
        return datetime.fromisoformat(value)
    if isinstance(column_type, Date) and isinstance(value, str):
        return date.fromisoformat(value[:10])
    if isinstance(column_type, Boolean) and isinstance(value, str):
        return value.lower() in ("1", "true", "t", "yes")
    return value


def restore(backup: dict, force: bool = False) -> dict[str, int]:
    """Zaxira nusxadagi barcha jadvallarni bazaga yozadi. Har jadvalga yozilgan qatorlar sonini qaytaradi."""

    Base.metadata.create_all(engine)
    tables = backup.get("tables") or {}

    with SessionLocal() as session:
        existing = {
            table.name: session.scalar(select(func.count()).select_from(table))
            for table in Base.metadata.sorted_tables
        }
        if any(existing.values()) and not force:
            busy = ", ".join(f"{name}={count}" for name, count in existing.items() if count)
            raise RuntimeError(
                f"Baza bo'sh emas ({busy}). Ma'lumotlar aralashib ketmasligi uchun tiklash to'xtatildi. "
                "Bo'sh bazaga tiklang yoki --force bilan ishga tushiring."
            )

        restored: dict[str, int] = {}
        # Bog'liqlik tartibida (avval users, keyin teachers, tests ...) — tashqi kalitlar buzilmasin
        if force:
            for table in reversed(Base.metadata.sorted_tables):
                session.execute(table.delete())

        for table in Base.metadata.sorted_tables:
            rows = tables.get(table.name) or []
            columns = {column.name: column for column in table.columns}
            prepared = [
                {name: _convert(value, columns[name]) for name, value in row.items() if name in columns}
                for row in rows
            ]
            if prepared:
                session.execute(insert(table), prepared)
            restored[table.name] = len(prepared)

        # PostgreSQL: id hisoblagichlari tiklangan eng katta id'dan davom etsin,
        # aks holda keyingi yangi yozuv "duplicate key" xatosi bilan yiqiladi.
        if not IS_SQLITE:
            for table in Base.metadata.sorted_tables:
                if "id" in table.columns and restored.get(table.name):
                    session.execute(
                        text(
                            f"SELECT setval(pg_get_serial_sequence('{table.name}', 'id'), "
                            f"(SELECT MAX(id) FROM {table.name}))"
                        )
                    )

        session.commit()
    return restored


def main() -> int:
    parser = argparse.ArgumentParser(description="Zaxira nusxadan bazani tiklash")
    parser.add_argument("file", help="botdan kelgan backup_*.json fayli")
    parser.add_argument("--force", action="store_true", help="bo'sh bo'lmagan bazani tozalab, ustiga yozish")
    args = parser.parse_args()

    with open(args.file, encoding="utf-8") as handle:
        backup = json.load(handle)

    print(f"Zaxira sanasi: {backup.get('exported_at', '?')}")
    try:
        restored = restore(backup, force=args.force)
    except RuntimeError as error:
        print(f"TO'XTATILDI: {error}")
        return 1

    for name, count in restored.items():
        print(f"  {name}: {count} ta")
    print(f"Tayyor — jami {sum(restored.values())} ta yozuv tiklandi.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
