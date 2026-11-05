"""
Tituldagi qo'lda yozilgan javoblarni AI (Claude) bilan o'qish.

Skaner yozma katakni (36a, 36b …) rasm qilib qirqadi. Bu modul bitta o'quvchining hamma kataklarini
bitta so'rovda Claude'ga yuboradi va har birida nima yozilganini (matn) va AI qanchalik ishonchli
o'qiganini qaytaradi. AI kalitni ko'rmaydi — yozilmagan to'g'ri javobni "ko'rib qolmasin".
Solishtirish va baho qo'yish serverning o'zida (main.py): AI aniq o'qigan va kalitga mos javob — ✓,
qolganini o'qituvchi tekshiradi.

Sozlash (Render → Environment):
  ANTHROPIC_API_KEY — Claude API kaliti (console.anthropic.com). Bo'lmasa, AI o'chiq: o'qituvchi
                      hamma yozma javobni o'zi baholaydi.
  AI_MODEL          — ixtiyoriy, standart: claude-opus-5-5
Yuboriladigan narsa — faqat katak rasmlari va savol raqamlari (o'quvchi ismi ketmaydi).
"""

from __future__ import annotations

import json
import logging
import os

log = logging.getLogger("backend.ai")

AI_MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")

SYSTEM_PROMPT = """You transcribe handwritten answers from scanned answer sheets of the Uzbek national \
certificate exam (Milliy sertifikat). Each image is one answer box, preceded by its label (for example "36a"). \
Students write short final answers: numbers, fractions, roots, expressions, intervals, or a few words \
in Uzbek (Latin script), Russian or English.

For every box, return exactly what the student wrote. Do not solve the task, correct the student, or guess \
the intended answer — a teacher compares your transcription with the answer key.
- Math: write plain text such as 2√3, -1/2, 3,5, x=7, π/4, (2;5], a^2. Keep a decimal comma or point as written.
- Ignore the printed box border and stray dots. If something is crossed out, transcribe only what remains.
- Empty box: text "".
- Set sure to false when any character is ambiguous (for example 1 or 7, 0 or 6, 5 or S) or part of the \
writing is illegible; set it to true only when you would bet on every character.
Return one entry per label, in the order given."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "answers": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "label": {"type": "string"},
                    "text": {"type": "string"},
                    "sure": {"type": "boolean"},
                },
                "required": ["label", "text", "sure"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["answers"],
    "additionalProperties": False,
}


class AIReadError(Exception):
    """AI javob bermadi yoki javobini ishlatib bo'lmaydi — kataklar o'qituvchiga qoladi."""


def ai_enabled() -> bool:
    return bool(os.getenv("ANTHROPIC_API_KEY"))


_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # faqat AI yoqilganda yuklanadi

        _client = anthropic.Anthropic(timeout=90.0, max_retries=3)
    return _client


def _image_block(data_url: str) -> dict:
    header, _, data = data_url.partition(",")
    media_type = header.removeprefix("data:").split(";")[0] or "image/jpeg"
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def read_boxes(subject: str, boxes: list[tuple[str, str]]) -> dict[str, tuple[str, bool]]:
    """
    boxes — [(katak nomi "36a", rasm data URL), ...] bitta o'quvchiniki.
    Natija — {"36a": (o'qilgan matn, aniqmi)}. Xato bo'lsa — AIReadError.
    """

    import anthropic

    if not boxes:
        return {}
    content: list[dict] = [{"type": "text", "text": f"Subject: {subject}. Answer boxes:"}]
    for label, image in boxes:
        content.append({"type": "text", "text": f"{label}:"})
        content.append(_image_block(image))
    content.append({"type": "text", "text": "Transcribe every box."})

    try:
        response = _get_client().beta.messages.create(
            model=AI_MODEL,
            max_tokens=4096,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": content}],
            # Oddiy o'qish ishi — chuqur fikrlash shart emas (tezroq va arzonroq)
            output_config={"effort": "low", "format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
            # Xavfsizlik filtri so'rovni rad etsa — Anthropic tavsiya qilgan boshqa model bilan qayta urinadi
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
        )
    except anthropic.APIConnectionError as error:
        raise AIReadError("AI bilan aloqa yo'q") from error
    except anthropic.RateLimitError as error:
        raise AIReadError("AI band (so'rovlar chegarasi)") from error
    except anthropic.APIStatusError as error:
        log.warning("AI xatosi %s: %s", error.status_code, error.message)
        raise AIReadError(f"AI xatosi ({error.status_code})") from error

    if response.stop_reason == "refusal":
        raise AIReadError("AI so'rovni rad etdi")
    if response.stop_reason == "max_tokens":
        raise AIReadError("AI javobi chala qoldi")
    text = next((block.text for block in response.content if block.type == "text"), "")
    try:
        answers = json.loads(text)["answers"]
    except (ValueError, KeyError, TypeError) as error:
        raise AIReadError("AI javobi tushunarsiz") from error
    usage = response.usage
    log.info("AI o'qidi: %d katak, model %s, tokenlar %s/%s", len(boxes), response.model,
             usage.input_tokens, usage.output_tokens)
    wanted = {label for label, _ in boxes}
    return {
        str(item["label"]).strip(): (str(item["text"]).strip()[:300], bool(item["sure"]))
        for item in answers
        if str(item.get("label", "")).strip() in wanted
    }
