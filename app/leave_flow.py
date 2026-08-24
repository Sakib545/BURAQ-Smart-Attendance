"""Guided WhatsApp leave requests and HR decision messages (v9.26)."""
from __future__ import annotations

import json
import re
from datetime import date, timedelta

from app.database import get_db
from app.services import clear_state, employee_by_phone, now_local, set_state, state

STATE_PREFIX = "leave:"
LEAVE_TYPES = ("Casual", "Sick", "Annual", "Unpaid")
MAX_LEAVE_DAYS = 60
MAX_PAST_DAYS = 30

_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")
_TYPE_ALIASES = {
    "1": "Casual", "casual": "Casual", "নৈমিত্তিক": "Casual",
    "2": "Sick", "sick": "Sick", "অসুস্থ": "Sick", "অসুস্থতা": "Sick",
    "3": "Annual", "annual": "Annual", "বার্ষিক": "Annual",
    "4": "Unpaid", "unpaid": "Unpaid", "বেতনবিহীন": "Unpaid", "বেতন ছাড়া": "Unpaid",
}


def _normalize(value: str) -> str:
    return " ".join(str(value or "").translate(_DIGITS).strip().lower().split())


def parse_leave_date(value: str, today: date | None = None) -> date:
    """Accept ISO, DD/MM/YYYY, Bengali digits, today and tomorrow."""
    today = today or now_local().date()
    text = _normalize(value)
    if text in {"আজ", "today"}:
        return today
    if text in {"আগামীকাল", "আগামিকাল", "tomorrow"}:
        return today + timedelta(days=1)

    iso = re.fullmatch(r"(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})", text)
    if iso:
        return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
    local = re.fullmatch(r"(\d{1,2})[-/.](\d{1,2})[-/.](\d{4})", text)
    if local:
        return date(int(local.group(3)), int(local.group(2)), int(local.group(1)))
    raise ValueError("তারিখ বুঝতে পারিনি")


def _load(phone: str) -> dict | None:
    row = state(phone)
    raw = str(row["state"] or "") if row else ""
    if not raw.startswith(STATE_PREFIX):
        return None
    try:
        data = json.loads(raw[len(STATE_PREFIX):])
        return data if isinstance(data, dict) else None
    except (TypeError, ValueError, json.JSONDecodeError):
        clear_state(phone)
        return None


def _save(phone: str, data: dict) -> None:
    set_state(phone, STATE_PREFIX + json.dumps(data, ensure_ascii=False, separators=(",", ":")))


def _type_from_text(value: str) -> str | None:
    return _TYPE_ALIASES.get(_normalize(value))


def _date_error(start: date, end: date | None = None, today: date | None = None) -> str | None:
    today = today or now_local().date()
    if start < today - timedelta(days=MAX_PAST_DAYS):
        return f"❌ ৩০ দিনের বেশি পুরোনো তারিখে আবেদন করা যাবে না।"
    if end is not None:
        if end < start:
            return "❌ শেষ তারিখ শুরুর তারিখের আগে হতে পারে না।"
        days = (end - start).days + 1
        if days > MAX_LEAVE_DAYS:
            return "❌ একবারে সর্বোচ্চ ৬০ দিনের ছুটির আবেদন করা যাবে।"
    return None


def _overlap(employee_id: int, start: date, end: date) -> bool:
    with get_db() as c:
        row = c.execute(
            "SELECT 1 FROM leave_requests WHERE employee_id=? "
            "AND status IN ('pending','approved') AND NOT(end_date<? OR start_date>?) LIMIT 1",
            (employee_id, start.isoformat(), end.isoformat()),
        ).fetchone()
    return bool(row)


def leave_status(employee_id: int) -> str:
    with get_db() as c:
        rows = c.execute(
            "SELECT leave_type,start_date,end_date,status,created_at FROM leave_requests "
            "WHERE employee_id=? ORDER BY id DESC LIMIT 10", (employee_id,),
        ).fetchall()
    if not rows:
        return "📋 আপনার কোনো ছুটির আবেদন নেই।"
    labels = {"pending": "⏳ Pending", "approved": "✅ Approved", "rejected": "❌ Rejected"}
    lines = ["📋 আপনার সাম্প্রতিক ছুটির আবেদন", ""]
    for row in rows:
        lines.append(
            f"{labels.get(str(row['status']), str(row['status']).title())} · "
            f"{row['leave_type']} · {row['start_date']} → {row['end_date']}"
        )
    return "\n".join(lines)


def decision_message(request_row, status: str) -> str:
    approved = status == "approved"
    title = "✅ ছুটির আবেদন অনুমোদিত" if approved else "❌ ছুটির আবেদন বাতিল"
    result = "অনুমোদন করেছেন" if approved else "অনুমোদন করেননি"
    return (
        f"{title}\n\nHR আপনার {request_row['leave_type']} leave আবেদন {result}।\n"
        f"তারিখ: {request_row['start_date']} → {request_row['end_date']}\n\n"
        "নিজের আবেদনের অবস্থা দেখতে লিখুন: my leave"
    )


def handle_leave_message(phone: str, text: str) -> str | None:
    """Handle a leave command/state, or return None for the attendance router."""
    command = _normalize(text)
    employee = employee_by_phone(phone)

    if command in {"my leave", "my_leave", "আমার ছুটি", "ছুটির অবস্থা"}:
        if not employee:
            return "❌ আগে Register করুন। শুধু লিখুন: Register"
        return leave_status(int(employee["id"]))

    current = _load(phone)
    if command in {"leave", "ছুটি"} and current is None:
        if not employee:
            return "❌ আগে Register করুন। শুধু লিখুন: Register"
        existing = state(phone)
        if existing and not str(existing["state"] or "").startswith(STATE_PREFIX):
            return "আগের প্রক্রিয়াটি আগে শেষ করুন অথবা CANCEL লিখে বাতিল করুন।"
        _save(phone, {"stage": "type", "employee_id": int(employee["id"])})
        return (
            "🏖️ ছুটির আবেদন শুরু হয়েছে।\n\n"
            "ধরন লিখুন:\n1. Casual\n2. Sick\n3. Annual\n4. Unpaid\n\n"
            "বাতিল করতে লিখুন: CANCEL"
        )

    if current is None:
        return None
    if not employee or int(current.get("employee_id") or 0) != int(employee["id"]):
        clear_state(phone)
        return "❌ Employee verification পাওয়া যায়নি। আবার leave লিখুন।"

    stage = current.get("stage")
    if stage == "type":
        leave_type = _type_from_text(command)
        if not leave_type:
            return "সঠিক ধরন লিখুন: 1 Casual, 2 Sick, 3 Annual অথবা 4 Unpaid।"
        current.update(stage="start", leave_type=leave_type)
        _save(phone, current)
        return "📅 ছুটির শুরুর তারিখ লিখুন। উদাহরণ: 2026-08-25, 25/08/2026, আজ বা আগামীকাল"

    if stage == "start":
        try:
            start = parse_leave_date(text)
        except ValueError:
            return "❌ তারিখ বুঝতে পারিনি। লিখুন: 2026-08-25, 25/08/2026, আজ বা আগামীকাল"
        error = _date_error(start)
        if error:
            return error
        current.update(stage="end", start_date=start.isoformat())
        _save(phone, current)
        return "📅 ছুটির শেষ তারিখ লিখুন। এক দিনের হলে একই তারিখ আবার লিখুন।"

    if stage == "end":
        try:
            start = date.fromisoformat(current["start_date"])
            end = parse_leave_date(text)
        except (KeyError, ValueError):
            return "❌ তারিখ বুঝতে পারিনি। শেষ তারিখ আবার লিখুন।"
        error = _date_error(start, end)
        if error:
            return error
        if _overlap(int(employee["id"]), start, end):
            clear_state(phone)
            return "❌ এই তারিখের সঙ্গে আগের Pending/Approved ছুটির আবেদন মিলে গেছে। নতুন আবেদন করতে আবার leave লিখুন।"
        current.update(stage="reason", end_date=end.isoformat())
        _save(phone, current)
        return "📝 ছুটির কারণ সংক্ষেপে লিখুন।"

    if stage == "reason":
        reason = str(text or "").strip()
        if len(reason) < 2:
            return "ছুটির কারণ লিখুন।"
        if len(reason) > 500:
            return "কারণটি ৫০০ অক্ষরের মধ্যে লিখুন।"
        current.update(stage="confirm", reason=reason)
        _save(phone, current)
        return (
            "📋 আবেদনটি যাচাই করুন\n\n"
            f"ধরন: {current['leave_type']}\n"
            f"তারিখ: {current['start_date']} → {current['end_date']}\n"
            f"কারণ: {reason}\n\n"
            "জমা দিতে YES লিখুন। পরিবর্তন করতে CANCEL লিখে আবার শুরু করুন।"
        )

    if stage == "confirm":
        if command not in {"yes", "y", "হ্যাঁ", "হ্যা", "ha"}:
            return "জমা দিতে YES লিখুন, অথবা বাতিল করতে CANCEL লিখুন।"
        start = date.fromisoformat(current["start_date"])
        end = date.fromisoformat(current["end_date"])
        error = _date_error(start, end)
        if error:
            clear_state(phone)
            return error + " আবার leave লিখে শুরু করুন।"
        if _overlap(int(employee["id"]), start, end):
            clear_state(phone)
            return "❌ একই তারিখে আরেকটি Pending/Approved আবেদন আছে।"
        with get_db() as c:
            c.execute(
                "INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,reason,requested_by) "
                "VALUES(?,?,?,?,?,?)",
                (int(employee["id"]), current["leave_type"], start.isoformat(), end.isoformat(),
                 current["reason"], "employee:whatsapp"),
            )
        clear_state(phone)
        return (
            "✅ ছুটির আবেদন HR-এর কাছে পাঠানো হয়েছে।\n"
            f"{current['leave_type']} · {start.isoformat()} → {end.isoformat()}\n\n"
            "অবস্থা দেখতে লিখুন: my leave"
        )

    clear_state(phone)
    return "প্রক্রিয়াটি আবার শুরু করতে লিখুন: leave"
