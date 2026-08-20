"""Configurable global shift rules (v9.24).

The rules live in the existing ``system_settings`` table, so an Admin/HR change
made from the Duty dashboard stays active for every following month until it is
changed again.  Nothing here creates or drops a table: the settings table is
part of the base schema and each value is a plain key/value row.

Precedence for any attendance calculation is deliberately narrow-to-wide:

    employee custom duty (one date)
        -> employee weekly duty (weekday)
            -> these global shift rules

Overtime is intentionally absent from this module: from v9.24 overtime is
manual-only and is entered by HR/Admin in Payroll.
"""
from __future__ import annotations

from datetime import datetime, time

from app.config import settings
from app.database import get_db

FIRST_START_KEY = "shift_first_start"
FIRST_END_KEY = "shift_first_end"
SECOND_START_KEY = "shift_second_start"
SECOND_END_KEY = "shift_second_end"
CUTOFF_KEY = "shift_second_cutoff"
GRACE_KEY = "shift_late_grace_minutes"

TIME_KEYS = (FIRST_START_KEY, FIRST_END_KEY, SECOND_START_KEY, SECOND_END_KEY, CUTOFF_KEY)
SETTING_KEYS = TIME_KEYS + (GRACE_KEY,)

MAX_GRACE_MINUTES = 240

# Default First Shift 08:30 AM - 04:00 PM, Second Shift 04:00 PM - 10:00 PM,
# automatic Second Shift detection from 04:00 PM, no late grace.
DEFAULTS: dict[str, str] = {
    FIRST_START_KEY: "08:30",
    FIRST_END_KEY: "16:00",
    SECOND_START_KEY: "16:00",
    SECOND_END_KEY: "22:00",
    CUTOFF_KEY: "16:00",
    GRACE_KEY: "0",
}


def normalize_time(value, fallback: str) -> str:
    """Return a valid ``HH:MM`` string, falling back to a known-good default."""
    text = str(value or "").strip()
    for pattern in ("%H:%M", "%H:%M:%S"):
        try:
            return datetime.strptime(text, pattern).strftime("%H:%M")
        except ValueError:
            continue
    return fallback


def normalize_grace(value, fallback: str = "0") -> int:
    try:
        minutes = int(float(str(value).strip()))
    except (TypeError, ValueError):
        minutes = int(float(fallback or 0))
    return max(0, min(MAX_GRACE_MINUTES, minutes))


def _environment_defaults() -> dict[str, str]:
    """Environment variables stay usable as the first-run default."""
    values = dict(DEFAULTS)
    values[CUTOFF_KEY] = normalize_time(settings.second_shift_from, DEFAULTS[CUTOFF_KEY])
    return values


def _stored_values() -> dict[str, str]:
    """Read every rule in one query; a missing table simply means defaults."""
    stored: dict[str, str] = {}
    try:
        with get_db() as c:
            placeholders = ",".join("?" for _ in SETTING_KEYS)
            rows = c.execute(
                f"SELECT key,value FROM system_settings WHERE key IN ({placeholders})",
                tuple(SETTING_KEYS),
            ).fetchall()
        for row in rows:
            if row["value"] is not None:
                stored[str(row["key"])] = str(row["value"])
    except Exception:
        return {}
    return stored


def get_shift_rules() -> dict:
    """Current global rules, always complete and always valid."""
    fallbacks = _environment_defaults()
    values = dict(fallbacks)
    values.update({k: v for k, v in _stored_values().items() if str(v).strip()})
    rules = {key: normalize_time(values.get(key), fallbacks[key]) for key in TIME_KEYS}
    rules[GRACE_KEY] = normalize_grace(values.get(GRACE_KEY), DEFAULTS[GRACE_KEY])
    return rules


def save_shift_rules(first_start: str, first_end: str, second_start: str,
                     second_end: str, second_cutoff: str, late_grace_minutes) -> dict:
    """Persist the rules. Invalid input keeps the previous value, never blank."""
    current = get_shift_rules()
    values = {
        FIRST_START_KEY: normalize_time(first_start, current[FIRST_START_KEY]),
        FIRST_END_KEY: normalize_time(first_end, current[FIRST_END_KEY]),
        SECOND_START_KEY: normalize_time(second_start, current[SECOND_START_KEY]),
        SECOND_END_KEY: normalize_time(second_end, current[SECOND_END_KEY]),
        CUTOFF_KEY: normalize_time(second_cutoff, current[CUTOFF_KEY]),
        GRACE_KEY: str(normalize_grace(late_grace_minutes, str(current[GRACE_KEY]))),
    }
    if values[FIRST_START_KEY] == values[FIRST_END_KEY]:
        raise ValueError("First Shift start and end cannot be the same time")
    if values[SECOND_START_KEY] == values[SECOND_END_KEY]:
        raise ValueError("Second Shift start and end cannot be the same time")
    with get_db() as c:
        for key, value in values.items():
            c.execute(
                "INSERT INTO system_settings(key,value,updated_at) VALUES(?,?,CURRENT_TIMESTAMP) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=CURRENT_TIMESTAMP",
                (key, value),
            )
    return get_shift_rules()


def _as_time(value: str, fallback: str) -> time:
    return time.fromisoformat(normalize_time(value, fallback))


def shift_window(shift, rules: dict | None = None) -> tuple[time, time]:
    """Global fallback duty window for an employee without an assigned duty."""
    rules = rules or get_shift_rules()
    if str(shift or "").lower() in {"second", "evening", "night"}:
        return (_as_time(rules[SECOND_START_KEY], DEFAULTS[SECOND_START_KEY]),
                _as_time(rules[SECOND_END_KEY], DEFAULTS[SECOND_END_KEY]))
    return (_as_time(rules[FIRST_START_KEY], DEFAULTS[FIRST_START_KEY]),
            _as_time(rules[FIRST_END_KEY], DEFAULTS[FIRST_END_KEY]))


def second_shift_cutoff(rules: dict | None = None) -> time:
    rules = rules or get_shift_rules()
    return _as_time(rules[CUTOFF_KEY], DEFAULTS[CUTOFF_KEY])


def late_grace_minutes(rules: dict | None = None) -> int:
    rules = rules or get_shift_rules()
    return normalize_grace(rules.get(GRACE_KEY), DEFAULTS[GRACE_KEY])


def apply_late_grace(raw_late_minutes: int, rules: dict | None = None) -> int:
    """Late minutes are recorded only after the configured grace period."""
    late = max(0, int(raw_late_minutes or 0))
    grace = late_grace_minutes(rules)
    return max(0, late - grace) if late > grace else 0
