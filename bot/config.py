"""Botning sozlamalari shu yerdan o'qiladi (.env faylidan)."""

import os
from pathlib import Path

from dotenv import load_dotenv

# Loyihaning bosh papkasi (bot/ dan bitta yuqori papka)
BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
WEBAPP_URL = os.getenv("WEBAPP_URL", "")
