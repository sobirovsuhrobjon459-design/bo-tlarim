"""
Tituldagi qo'lda yozilgan javoblarni AI bilan o'qish (Google Gemini yoki Claude).

Skaner yozma katakni (36a, 36b …) rasm qilib qirqadi. Bu modul bitta o'quvchining hamma kataklarini
bitta so'rovda AI'ga yuboradi va har birida nima yozilganini (matn) va AI qanchalik ishonchli
o'qiganini qaytaradi. AI kalitni ko'rmaydi — yozilmagan to'g'ri javobni "ko'rib qolmasin".
Solishtirish va baho qo'yish serverning o'zida (main.py): AI aniq o'qigan va kalitga mos javob — ✓,
qolganini o'qituvchi tekshiradi.

Sozlash (Render → Environment) — qaysi kalit bo'lsa, o'sha ishlatiladi (ikkalasi bo'lsa — Gemini):
  GEMINI_API_KEY    — Google AI Studio kaliti (aistudio.google.com). Bepul tarifda kunlik/daqiqalik
                      cheklov bor; bepul tarifda Google yuborilgan rasmlardan o'z mahsulotlari uchun
                      foydalanadi (shartlari: aistudio.google.com/docs/terms).
  GEMINI_MODEL      — ixtiyoriy, standart: gemini-3.6-flash
  ANTHROPIC_API_KEY — Claude API kaliti (console.anthropic.com), pullik.
  AI_MODEL          — ixtiyoriy, standart: claude-opus-5-5
  AI_PROVIDER       — ixtiyoriy: "gemini" yoki "anthropic" (ikkala kalit bo'lsa, qaysi biri)
Kalit bo'lmasa — AI o'chiq: o'qituvchi hamma yozma javobni o'zi baholaydi.
Yuboriladigan narsa — faqat katak rasmlari va savol raqamlari (o'quvchi ismi, sinfi, ID'si ketmaydi).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("backend.ai")

AI_MODEL = os.getenv("AI_MODEL", "claude-opus-5-5")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.6-flash")
# Asosiy model band yoki cheklovi tugaganda — zaxira (Flash-Lite: yengilroq, odatda bo'sh); "" — zaxirasiz
GEMINI_FALLBACK_MODEL = os.getenv("GEMINI_FALLBACK_MODEL", "gemini-3.5-flash-lite")
GEMINI_URL = (os.getenv("GEMINI_BASE_URL", "https://generativelanguage.googleapis.com").rstrip("/")
              + "/v1beta/models/{model}:generateContent")
GEMINI_GAP = float(os.getenv("GEMINI_GAP", "7"))  # so'rovlar orasi (s): bepul tarif ~10 so'rov/daqiqa

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
Return one entry per label, in the order given, as JSON: {"answers": [{"label": "...", "text": "...", "sure": true}]}."""

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

# Oxirgi xato — o'qituvchi "Yozma javoblar" bo'limida sababini ko'radi (kalit/cheklov muammosi)
last_error: dict | None = None


class AIReadError(Exception):
    """AI javob bermadi yoki javobini ishlatib bo'lmaydi — kataklar o'qituvchiga qoladi."""


def ai_provider() -> str | None:
    """Ishlatiladigan AI: "gemini", "anthropic" yoki None (kalit yo'q — AI o'chiq)."""

    keys = {"gemini": bool(os.getenv("GEMINI_API_KEY")), "anthropic": bool(os.getenv("ANTHROPIC_API_KEY"))}
    chosen = os.getenv("AI_PROVIDER", "").strip().lower()
    if keys.get(chosen):
        return chosen
    return "gemini" if keys["gemini"] else "anthropic" if keys["anthropic"] else None


def ai_enabled() -> bool:
    return ai_provider() is not None


def read_boxes(subject: str, boxes: list[tuple[str, str]]) -> dict[str, tuple[str, bool]]:
    """
    boxes — [(katak nomi "36a", rasm data URL), ...] bitta o'quvchiniki.
    Natija — {"36a": (o'qilgan matn, aniqmi)}. Xato bo'lsa — AIReadError.
    """

    global last_error
    if not boxes:
        return {}
    provider = ai_provider()
    try:
        if provider == "gemini":
            answers = _read_with_gemini(subject, boxes)
        elif provider == "anthropic":
            answers = _read_with_claude(subject, boxes)
        else:
            raise AIReadError("AI kaliti sozlanmagan")
    except AIReadError as error:
        last_error = {"message": str(error), "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
        raise
    last_error = None
    wanted = {label for label, _ in boxes}
    return {
        str(item["label"]).strip(): (str(item.get("text", "")).strip()[:300], bool(item.get("sure")))
        for item in answers
        if isinstance(item, dict) and str(item.get("label", "")).strip() in wanted
    }


def _parse_answers(text: str) -> list:
    """AI javobidagi JSON: {"answers": [...]} (ba'zan ```json … ``` ichida keladi)."""

    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        answers = json.loads(text)["answers"]
    except (ValueError, KeyError, TypeError) as error:
        raise AIReadError("AI javobi tushunarsiz") from error
    if not isinstance(answers, list):
        raise AIReadError("AI javobi tushunarsiz")
    return answers


def _split_data_url(data_url: str) -> tuple[str, str]:
    header, _, data = data_url.partition(",")
    return header.removeprefix("data:").split(";")[0] or "image/jpeg", data


# ---------- Google Gemini (REST, httpx orqali) ----------

_gemini_lock = threading.Lock()
_gemini_last = 0.0


def _gemini_schema(schema):
    """Gemini sxemasi: additionalProperties olib tashlanadi (eski modellar uni qabul qilmasligi mumkin)."""

    if isinstance(schema, dict):
        return {key: _gemini_schema(value) for key, value in schema.items() if key != "additionalProperties"}
    return schema


def _read_with_gemini(subject: str, boxes: list[tuple[str, str]]) -> list:
    parts: list[dict] = [{"text": f"Subject: {subject}. Answer boxes:"}]
    for label, image in boxes:
        media_type, data = _split_data_url(image)
        parts.append({"text": f"{label}:"})
        parts.append({"inlineData": {"mimeType": media_type, "data": data}})
    parts.append({"text": "Transcribe every box."})
    body = {
        "systemInstruction": {"parts": [{"text": SYSTEM_PROMPT}]},
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseJsonSchema": _gemini_schema(OUTPUT_SCHEMA),
            "maxOutputTokens": 8192,
        },
    }
    headers = {"x-goog-api-key": os.getenv("GEMINI_API_KEY", ""), "Content-Type": "application/json"}
    # Asosiy model band (503), topilmadi (404) yoki uning cheklovi tugadi — zaxira model bilan urinib ko'riladi
    models = [GEMINI_MODEL] + ([GEMINI_FALLBACK_MODEL] if GEMINI_FALLBACK_MODEL not in ("", GEMINI_MODEL) else [])
    quota_over = False
    for model in models:
        response, quota_over = _gemini_call(GEMINI_URL.format(model=model), body, headers)
        if response.status_code in (404, 429, 500, 502, 503, 504) and model != models[-1]:
            log.warning("Gemini %s ishlamadi (%s) — zaxira model %s", model, response.status_code, models[-1])
            continue
        break
    if response.status_code != 200:
        if quota_over:
            raise AIReadError("Gemini bepul cheklovi tugadi — keyinroq (yoki ertaga) «AI bilan o'qish» ni bosing")
        message = _gemini_error(response)
        log.warning("Gemini xatosi %s: %s", response.status_code, message)
        raise AIReadError(f"Gemini xatosi ({response.status_code}): {message}")

    data = response.json()
    if data.get("promptFeedback", {}).get("blockReason"):
        raise AIReadError(f"Gemini so'rovni rad etdi ({data['promptFeedback']['blockReason']})")
    candidates = data.get("candidates") or []
    if not candidates:
        raise AIReadError("Gemini javob bermadi")
    finish = candidates[0].get("finishReason", "STOP")
    if finish not in ("STOP", "FINISH_REASON_UNSPECIFIED"):
        raise AIReadError(f"Gemini javobi to'liq emas ({finish})")
    text = "".join(part.get("text", "") for part in candidates[0].get("content", {}).get("parts", [])
                   if not part.get("thought"))
    usage = data.get("usageMetadata", {})
    log.info("Gemini o'qidi: %d katak, model %s, tokenlar %s/%s", len(boxes), GEMINI_MODEL,
             usage.get("promptTokenCount"), usage.get("candidatesTokenCount"))
    return _parse_answers(text)


GEMINI_BUSY_WAITS = (5, 15, 30)  # model band (5xx) bo'lsa — shuncha soniya kutib qayta urinish


def _gemini_call(url: str, body: dict, headers: dict):
    """
    Bitta model bilan so'rov: band (5xx) bo'lsa kutib qayta urinadi, qisqa cheklovda (429) — aytilgan vaqt kutadi.
    Natija: (javob, cheklov tugadimi).
    """

    import httpx

    global _gemini_last
    busy = limited = 0
    while True:
        # Bepul tarif cheklovi: so'rovlar orasida tanaffus (ikki fon oqimi ham shu navbatdan o'tadi)
        with _gemini_lock:
            wait = _gemini_last + GEMINI_GAP - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            _gemini_last = time.monotonic()
            try:
                response = httpx.post(url, json=body, headers=headers, timeout=90)
            except httpx.HTTPError as error:
                raise AIReadError("Gemini bilan aloqa yo'q") from error
        status = response.status_code
        if status == 429:
            delay = _retry_delay(response)
            if delay > 70 or limited >= 3:
                return response, True  # kunlik (yoki uzoq) cheklov — shu model bilan kutish befoyda
            log.info("Gemini cheklovi (429) — %.0f s kutiladi", delay)
            limited += 1
            time.sleep(delay)
            continue
        if status in (500, 502, 503, 504) and busy < len(GEMINI_BUSY_WAITS):
            log.info("Gemini band (%s) — %s s kutiladi", status, GEMINI_BUSY_WAITS[busy])
            time.sleep(GEMINI_BUSY_WAITS[busy])
            busy += 1
            continue
        if status == 400 and "responseJsonSchema" in response.text and "responseJsonSchema" in body["generationConfig"]:
            # Bu model sxemani qabul qilmadi — JSON ko'rinishi so'rovning o'zida yozilgan, sxemasiz qayta urinamiz
            body["generationConfig"].pop("responseJsonSchema")
            continue
        return response, False


def _retry_delay(response) -> float:
    """429 javobidagi "retryDelay" ("17s") — qancha kutish kerak; yo'q bo'lsa 20 s."""

    try:
        for detail in response.json().get("error", {}).get("details", []):
            delay = detail.get("retryDelay")
            if delay:
                return float(str(delay).rstrip("s"))
    except (ValueError, AttributeError):
        return 20.0
    return 20.0


def _gemini_error(response) -> str:
    try:
        return str(response.json().get("error", {}).get("message", ""))[:200] or response.text[:200]
    except ValueError:
        return response.text[:200]


# ---------- Claude (Anthropic SDK) ----------

_client = None


def _get_client():
    global _client
    if _client is None:
        import anthropic  # faqat Claude ishlatilganda yuklanadi

        _client = anthropic.Anthropic(timeout=90.0, max_retries=3)
    return _client


def _read_with_claude(subject: str, boxes: list[tuple[str, str]]) -> list:
    import anthropic

    content: list[dict] = [{"type": "text", "text": f"Subject: {subject}. Answer boxes:"}]
    for label, image in boxes:
        media_type, data = _split_data_url(image)
        content.append({"type": "text", "text": f"{label}:"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}})
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
    usage = response.usage
    log.info("Claude o'qidi: %d katak, model %s, tokenlar %s/%s", len(boxes), response.model,
             usage.input_tokens, usage.output_tokens)
    return _parse_answers(next((block.text for block in response.content if block.type == "text"), ""))
