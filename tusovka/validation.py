import re
import unicodedata
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from flask import current_app

from .security import utcnow

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")

TITLE_MAX = 120
DESCRIPTION_MAX = 5000
COMMENT_MAX = 1000
CAPACITY_MIN, CAPACITY_MAX = 1, 10000
MAX_YEARS_AHEAD = 3


def clean_text(value, multiline=False):
    """Нормалізує текст: прибирає керуючі/невидимі символи, обрізає пробіли."""
    if not isinstance(value, str):
        return ""
    value = unicodedata.normalize("NFC", value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    if not multiline:
        value = value.replace("\n", " ").replace("\t", " ")
    value = _CONTROL.sub("", value)
    if multiline:
        value = re.sub(r"\n{3,}", "\n\n", value)
    return value.strip()


def app_tz():
    return ZoneInfo(current_app.config["APP_TIMEZONE"])


def parse_local_datetime(raw):
    """'YYYY-MM-DDTHH:MM' з <input type=datetime-local> у часовому поясі застосунку → UTC datetime."""
    if not isinstance(raw, str):
        return None
    try:
        naive = datetime.strptime(raw.strip(), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None
    return naive.replace(tzinfo=app_tz())


def to_local_input(dt_utc):
    return dt_utc.astimezone(app_tz()).strftime("%Y-%m-%dT%H:%M")


def validate_event_form(form, *, current_participants=0):
    errors = {}
    title = clean_text(form.get("title"))
    description = clean_text(form.get("description"), multiline=True)
    raw_dt = form.get("starts_at") or ""
    raw_capacity = (form.get("capacity") or "").strip()
    visibility = form.get("visibility")

    if not (3 <= len(title) <= TITLE_MAX):
        errors["title"] = f"Назва — від 3 до {TITLE_MAX} символів."
    if not (1 <= len(description) <= DESCRIPTION_MAX):
        errors["description"] = f"Опис — від 1 до {DESCRIPTION_MAX} символів."

    starts_at = parse_local_datetime(raw_dt)
    if starts_at is None:
        errors["starts_at"] = "Вкажіть дату й час події."
    else:
        now = utcnow()
        if starts_at <= now + timedelta(minutes=5):
            errors["starts_at"] = "Подія має починатися щонайменше через 5 хвилин."
        elif starts_at > now + timedelta(days=365 * MAX_YEARS_AHEAD):
            errors["starts_at"] = f"Не пізніше ніж за {MAX_YEARS_AHEAD} роки від сьогодні."

    capacity = None
    if not re.fullmatch(r"\d{1,6}", raw_capacity):
        errors["capacity"] = f"Кількість місць — ціле число від {CAPACITY_MIN} до {CAPACITY_MAX}."
    else:
        capacity = int(raw_capacity)
        if not (CAPACITY_MIN <= capacity <= CAPACITY_MAX):
            errors["capacity"] = f"Кількість місць — ціле число від {CAPACITY_MIN} до {CAPACITY_MAX}."
        elif capacity < current_participants:
            errors["capacity"] = (
                f"Уже записалося {current_participants} — не можна зробити менше місць."
            )

    if visibility not in ("public", "private"):
        errors["visibility"] = "Оберіть тип події."

    values = {
        "title": title[:TITLE_MAX + 50],
        "description": description[:DESCRIPTION_MAX + 50],
        "starts_at": raw_dt[:32] if isinstance(raw_dt, str) else "",
        "capacity": raw_capacity[:12],
        "visibility": visibility if visibility in ("public", "private") else "public",
    }
    cleaned = {
        "title": title,
        "description": description,
        "starts_at": starts_at,
        "capacity": capacity,
        "is_private": 1 if visibility == "private" else 0,
    }
    return values, cleaned, errors
