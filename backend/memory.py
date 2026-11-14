"""
Server xotirasini nazorat qilish (Render bepul tarifi — 512 MB, oshsa server o'ldiriladi).

Muammo (2026-11-14): server ~13 soat ishlab, "Ran out of memory (used over 512MB)" bilan
qayta ishga tushdi. Python'ning o'zi bo'shatgan xotirani glibc operatsion tizimga
qaytarmay ushlab turadi, ayniqsa ko'p oqimda: FastAPI har sinxron so'rovni alohida
oqimda bajaradi va glibc har oqimga alohida xotira "arenasi" ochadi (yadro soni × 8
tagacha). Katta vaqtinchalik ma'lumot (skaner rasmi, PDF, zaxira nusxa) har arenada
bo'laklanib qoladi va jarayon xotirasi kundan kunga o'sib boradi.

Yechim:
- arenalar soni 2 ta bilan cheklanadi (MALLOC_ARENA_MAX=2 bilan bir xil, lekin kod ichidan);
- har 5 daqiqada bo'shagan xotira tizimga qaytariladi (malloc_trim);
- jarayon qancha xotira egallayotgani logga yoziladi — o'sish bo'lsa, Render loglarida ko'rinadi.

Faqat Linux'da (Render) ishlaydi; Windows/macOS'da hech narsa qilmaydi.
"""

from __future__ import annotations

import ctypes
import gc
import logging
import os
import sys

# glibc mallopt() parametri: M_ARENA_MAX
_M_ARENA_MAX = -8
# Shu chegaradan oshsa — ogohlantirish (Render bepul tarifi 512 MB'da o'ldiradi)
MEMORY_WARN_MB = 400

_libc = None


def _glibc():
    global _libc
    if _libc is None:
        _libc = False
        if sys.platform.startswith("linux"):
            try:
                _libc = ctypes.CDLL("libc.so.6")
            except OSError:
                pass
    return _libc or None


def limit_malloc_arenas(count: int = 2) -> None:
    """Keyin ochiladigan oqimlar xotira arenalarini ko'paytirmasin (server ishga tushganda bir marta)."""

    libc = _glibc()
    if libc:
        try:
            libc.mallopt(_M_ARENA_MAX, count)
        except Exception:  # noqa: BLE001 — musl va boshqa libc'larda bu funksiya bo'lmasligi mumkin
            logging.getLogger("backend").warning("mallopt(M_ARENA_MAX) ishlamadi", exc_info=True)


def rss_mb() -> float | None:
    """Jarayon hozir egallab turgan haqiqiy xotira (MB). Linux bo'lmasa — None."""

    try:
        with open("/proc/self/statm", encoding="ascii") as handle:
            resident_pages = int(handle.read().split()[1])
        return resident_pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, IndexError, AttributeError):
        return None


def storage_report(path) -> str:
    """Server ishga tushganda logga: vaqtinchalik fayllar diskidagi joy va /tmp xotirada (tmpfs) emasmi."""

    import shutil

    parts = []
    try:
        usage = shutil.disk_usage(path)
        parts.append(f"disk {path}: bo'sh {usage.free / 1024 ** 3:.1f} GB / jami {usage.total / 1024 ** 3:.1f} GB")
    except OSError:
        parts.append(f"disk {path}: o'lchab bo'lmadi")
    try:
        with open("/proc/mounts", encoding="utf-8") as handle:
            mounts = {fields[1]: fields[2] for fields in (line.split() for line in handle) if len(fields) > 2}
        parts.append(f"/tmp turi: {mounts.get('/tmp', 'alohida emas (asosiy disk)')}")
    except OSError:
        pass
    used = rss_mb()
    if used is not None:
        parts.append(f"xotira: {used:.0f} MB")
    return "; ".join(parts)


def release_memory() -> float | None:
    """Keraksiz obyektlarni tozalab, bo'shagan xotirani operatsion tizimga qaytaradi. Keyingi RSS (MB)."""

    gc.collect()
    libc = _glibc()
    if libc:
        try:
            libc.malloc_trim(0)
        except Exception:  # noqa: BLE001
            pass
    return rss_mb()
