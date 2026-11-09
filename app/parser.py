"""Turn a free-text leave request ("next Monday to Wednesday, family trip") into a LeaveDraft.

Two strategies:
1. LLM parser – used when LLM_API_KEY is set. Calls any OpenAI-compatible
   /chat/completions endpoint, asks for JSON, then validates it with Pydantic.
2. Rule-based parser – deterministic fallback for English and Turkish phrases.
   Used when no key is configured or when the LLM output is unusable,
   so the endpoint never depends on an external service to work.
"""

import json
import logging
import os
import re
from datetime import date, timedelta

import httpx
from pydantic import ValidationError

from app.models import LeaveType
from app.schemas import LeaveDraft

log = logging.getLogger(__name__)

WEEKDAYS = {
    "monday": 0,
    "pazartesi": 0,
    "tuesday": 1,
    "salı": 1,
    "sali": 1,
    "wednesday": 2,
    "çarşamba": 2,
    "carsamba": 2,
    "thursday": 3,
    "perşembe": 3,
    "persembe": 3,
    "friday": 4,
    "cuma": 4,
}
MONTHS = {
    "january": 1,
    "ocak": 1,
    "february": 2,
    "şubat": 2,
    "subat": 2,
    "march": 3,
    "mart": 3,
    "april": 4,
    "nisan": 4,
    "may": 5,
    "mayıs": 5,
    "mayis": 5,
    "june": 6,
    "haziran": 6,
    "july": 7,
    "temmuz": 7,
    "august": 8,
    "ağustos": 8,
    "agustos": 8,
    "september": 9,
    "eylül": 9,
    "eylul": 9,
    "october": 10,
    "ekim": 10,
    "november": 11,
    "kasım": 11,
    "kasim": 11,
    "december": 12,
    "aralık": 12,
    "aralik": 12,
}
SICK_WORDS = ("sick", "ill", "doctor", "hasta", "rapor", "doktor")
UNPAID_WORDS = ("unpaid", "ücretsiz", "ucretsiz")

SYSTEM_PROMPT = """You extract leave requests from employee messages.
Today is {today} ({weekday}). Return ONLY a JSON object with keys:
leave_type ("annual" | "sick" | "unpaid"), start_date (YYYY-MM-DD),
end_date (YYYY-MM-DD), reason (short string or null).
Resolve relative dates against today. Single-day requests have start_date == end_date."""


def parse(text: str, today: date | None = None) -> LeaveDraft:
    today = today or date.today()
    if os.getenv("LLM_API_KEY"):
        try:
            return _parse_with_llm(text, today)
        except (httpx.HTTPError, ValueError, KeyError, ValidationError) as exc:
            log.warning("LLM parse failed, falling back to rules: %s", exc)
    return parse_with_rules(text, today)


def _parse_with_llm(text: str, today: date) -> LeaveDraft:
    base_url = os.getenv("LLM_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {os.environ['LLM_API_KEY']}"},
        json={
            "model": os.getenv("LLM_MODEL", "gpt-4o-mini"),
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT.format(today=today, weekday=today.strftime("%A")),
                },
                {"role": "user", "content": text},
            ],
        },
        timeout=15,
    )
    response.raise_for_status()
    content = response.json()["choices"][0]["message"]["content"]
    data = json.loads(content)  # raises ValueError on malformed JSON
    draft = LeaveDraft(**data, source="llm")
    if draft.end_date < draft.start_date:
        raise ValueError("LLM returned end_date before start_date")
    return draft


def _next_weekday(today: date, weekday: int, next_week: bool) -> date:
    """'friday' -> the coming Friday (never today). 'next friday' -> Friday of next calendar week."""
    if next_week:
        monday_next_week = today - timedelta(days=today.weekday()) + timedelta(days=7)
        return monday_next_week + timedelta(days=weekday)
    days_ahead = (weekday - today.weekday()) % 7 or 7
    return today + timedelta(days=days_ahead)


def _find_dates(text: str, today: date) -> list[date]:
    """Return the dates mentioned in text, in the order they appear."""
    found: list[tuple[int, date]] = []
    lowered = text.lower()

    # 2026-12-14
    for m in re.finditer(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b", lowered):
        found.append((m.start(), date(int(m[1]), int(m[2]), int(m[3]))))
    # 14.12.2026 or 14/12/2026
    for m in re.finditer(r"\b(\d{1,2})[./](\d{1,2})[./](\d{4})\b", lowered):
        found.append((m.start(), date(int(m[3]), int(m[2]), int(m[1]))))
    # 14 December / 14 Aralık (year inferred: next occurrence)
    month_names = "|".join(sorted(MONTHS, key=len, reverse=True))
    for m in re.finditer(rf"\b(\d{{1,2}})\s+({month_names})\b", lowered):
        candidate = date(today.year, MONTHS[m[2]], int(m[1]))
        if candidate < today:
            candidate = candidate.replace(year=today.year + 1)
        found.append((m.start(), candidate))
    # weekdays, optionally preceded by "next" / "haftaya" / "gelecek"
    day_names = "|".join(sorted(WEEKDAYS, key=len, reverse=True))
    for m in re.finditer(rf"\b(next|haftaya|gelecek|önümüzdeki)?\s*({day_names})\b", lowered):
        found.append((m.start(2), _next_weekday(today, WEEKDAYS[m[2]], bool(m[1]))))
    # relative words
    for word, delta in (
        ("tomorrow", 1),
        ("yarın", 1),
        ("yarin", 1),
        ("today", 0),
        ("bugün", 0),
        ("bugun", 0),
    ):
        for m in re.finditer(rf"\b{word}\b", lowered):
            found.append((m.start(), today + timedelta(days=delta)))

    found.sort(key=lambda item: item[0])
    return [d for _, d in found]


def parse_with_rules(text: str, today: date) -> LeaveDraft:
    lowered = text.lower()
    dates = _find_dates(text, today)
    if not dates:
        raise ValueError("Could not find a date in the text")

    start = dates[0]
    end = dates[1] if len(dates) > 1 else start

    # "3 days" / "3 gün" extends a single start date by N working days
    duration = re.search(r"\b(\d{1,2})\s*(days|day|gün|gun)\b", lowered)
    if duration and len(dates) == 1:
        remaining = int(duration[1]) - 1
        end = start
        while remaining > 0:
            end += timedelta(days=1)
            if end.weekday() < 5:
                remaining -= 1

    # "next Monday to Wednesday": the bare weekday resolved to this week, so move it after start
    if end < start and (start - end).days < 7:
        end += timedelta(days=7)
    if end < start:
        raise ValueError("The end date is before the start date")

    if any(w in lowered for w in SICK_WORDS):
        leave_type = LeaveType.SICK
    elif any(w in lowered for w in UNPAID_WORDS):
        leave_type = LeaveType.UNPAID
    else:
        leave_type = LeaveType.ANNUAL

    reason_match = re.search(
        r"\b(?:because(?:\s+of)?|for|çünkü|sebebi|nedeni)[:\s]+(.+)$", text, re.IGNORECASE
    )
    reason = reason_match[1].strip().rstrip(".") if reason_match else None

    return LeaveDraft(
        leave_type=leave_type, start_date=start, end_date=end, reason=reason, source="rules"
    )
