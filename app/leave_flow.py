"""Employee-side leave request flow over WhatsApp (v9.25).

The HR side already exists: ``/hr-operations`` lists ``leave_requests`` and
``POST /leave/{id}/approve`` writes ``status='leave'`` attendance rows that
payroll reads as paid leave.  What was missing was a way for the *employee* to
file the request, so HR had to type every one by hand.

This module adds only the conversation.  It writes exactly one row into the
existing ``leave_requests`` table with ``status='pending'`` and
``requested_by='employee:whatsapp'``, so approval, payroll and audit behaviour
stay byte-for-byte identical to an HR-entered request.

State machine (stored in the existing ``conversation_states`` table):

    leave_type
        -> leave_start:<type>
            -> leave_end:<type>:<start>
                -> leave_reason:<type>:<start>:<end>
                    -> leave_confirm:<type>:<start>:<end>:<reason_b64>
                        -> INSERT, clear_state

``CANCEL`` aborts at any step because ``services.process`` checks it before
dispatching on state.
"""
from __future__ import annotations

import base64
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app.database import get_db

logger = logging.getLogger(__name__)

# Must match the HR dropdown in /hr-operations so both sources produce the same
# leave_type values. 'Unpaid' is deliberately included: payroll treats it as an
# unpaid unit rather than silently paying for it.
LEAVE_TYPES = ("Casual", "Sick", "Annual", "Unpaid")

MAX_PAST_DAYS = 30        # retroactive sick leave is normal; older needs HR
MAX_FUTURE_DAYS = 365
MAX_DURATION_DAYS = 60
MAX_REASON_CHARS = 500

_BENGALI_DIGITS = str.maketrans("০১২৩৪৫৬৭৮৯", "0123456789")

_TYPE_ALIASES = {
    "1": "Casual", "casual": "Casual", "নৈমিত্তিক": "Casual", "ক্যাজুয়াল": "Casual",
    "2": "Sick", "sick": "Sick", "অসুস্থ": "Sick", "অসুস্থতা": "Sick",
    "3": "Annual", "annual": "Annual", "বার্ষিক": "Annual",
    "4": "Unpaid", "unpaid": "Unpaid", "বিনা বেতনে": "Unpaid", "অবৈতনিক": "Unpaid",
}

_TODAY_WORDS = {"আজ", "আজকে", "today"}
_TOMORROW_WORDS = {"কাল", "আগামীকাল", "কালকে", "tomorrow"}


def today_local():
    return datetime.now(ZoneInfo(settings.timezone)).date()


def _encode_reason(reason: str) -> str:
    return base64.urlsafe_b64encode(reason.encode("utf-8")).decode("ascii")


def _decode_reason(blob: str) -> str:
    try:
        return base64.urlsafe_b64decode(blob.encode("ascii")).decode("utf-8")
    except Exception:
        return ""


def parse_leave_type(text: str) -> str | None:
    key = " ".join((text or "").strip().lower().split())
    return _TYPE_ALIASES.get(key)


def parse_leave_date(text: str, base_date=None):
    """Accept ISO, DD/MM/YYYY, DD-MM-YYYY and Bengali relative words.

    Bengali digits are normalised first so ``২৫/০৮/২০২৬`` works exactly like
    ``25/08/2026``. Returns ``None`` when nothing parses.
    """
    raw = " ".join((text or "").strip().lower().split()).translate(_BENGALI_DIGITS)
    if not raw:
        return None
    base = base_date or today_local()
    if raw in _TODAY_WORDS:
        return base
    if raw in _TOMORROW_WORDS:
        return base + timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%Y"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def _overlapping(employee_id: int, start, end):
    """Any pending/approved request covering part of the same range."""
    with get_db() as c:
        return c.execute(
            "SELECT leave_type,start_date,end_date,status FROM leave_requests "
            "WHERE employee_id=? AND status IN ('pending','approved') "
            "AND start_date<=? AND end_date>=? ORDER BY id DESC LIMIT 1",
            (employee_id, end.isoformat(), start.isoformat()),
        ).fetchone()


def validate_range(employee_id: int, start, end) -> str | None:
    """Return a Bengali error message, or None when the range is acceptable."""
    base = today_local()
    if end < start:
        return "❌ শেষ তারিখ শুরুর তারিখের আগে হতে পারে না। আবার শেষ তারিখ পাঠান।"
    if start < base - timedelta(days=MAX_PAST_DAYS):
        return (f"❌ {MAX_PAST_DAYS} দিনের বেশি পুরোনো তারিখে আবেদন করা যায় না। "
                "এমন ক্ষেত্রে সরাসরি HR-এর সঙ্গে যোগাযোগ করুন।")
    if start > base + timedelta(days=MAX_FUTURE_DAYS):
        return "❌ এক বছরের বেশি ভবিষ্যতের তারিখে আবেদন করা যায় না।"
    if (end - start).days + 1 > MAX_DURATION_DAYS:
        return f"❌ একবারে সর্বোচ্চ {MAX_DURATION_DAYS} দিনের ছুটির আবেদন করা যায়।"
    clash = _overlapping(employee_id, start, end)
    if clash:
        status = "অনুমোদিত" if str(clash["status"]) == "approved" else "অপেক্ষমাণ"
        return (f"❌ এই তারিখগুলোর সঙ্গে আপনার একটি {status} ছুটি মিলে যাচ্ছে "
                f"({clash['start_date']} → {clash['end_date']})। অন্য তারিখ দিন।")
    return None


def type_prompt() -> str:
    return ("🗓️ ছুটির আবেদন\n\nকোন ধরনের ছুটি চান? নম্বর বা নাম লিখুন:\n"
            "1️⃣ Casual (নৈমিত্তিক)\n2️⃣ Sick (অসুস্থতা)\n"
            "3️⃣ Annual (বার্ষিক)\n4️⃣ Unpaid (বিনা বেতনে)\n\n"
            "বাতিল করতে লিখুন: CANCEL")


def _date_prompt(label: str) -> str:
    return (f"📅 ছুটির {label} তারিখ পাঠান।\n\n"
            "উদাহরণ: 2026-08-25 অথবা 25/08/2026\n"
            "চাইলে লিখতে পারেন: আজ / আগামীকাল")


def start_leave_request(phone: str, employee) -> str:
    """Entry point for the `leave` command."""
    from app.services import set_state
    if not employee:
        return "❌ আগে Register করুন। শুধু লিখুন: Register"
    set_state(phone, "leave_type")
    return type_prompt()


def handle_leave_state(phone: str, state_value: str, text: str, employee) -> str:
    """Advance the leave conversation one step. Never raises to the webhook."""
    from app.services import clear_state, set_state

    if not employee:
        clear_state(phone)
        return "❌ আগে Register করুন। শুধু লিখুন: Register"

    parts = state_value.split(":")
    step = parts[0]

    if step == "leave_type":
        leave_type = parse_leave_type(text)
        if not leave_type:
            return "❌ বুঝতে পারিনি।\n\n" + type_prompt()
        set_state(phone, f"leave_start:{leave_type}")
        return _date_prompt("শুরুর")

    if step == "leave_start":
        leave_type = parts[1]
        start = parse_leave_date(text)
        if not start:
            return "❌ তারিখ বুঝতে পারিনি।\n\n" + _date_prompt("শুরুর")
        error = validate_range(employee["id"], start, start)
        if error:
            return error
        set_state(phone, f"leave_end:{leave_type}:{start.isoformat()}")
        return (f"✅ শুরু: {start.isoformat()}\n\n" + _date_prompt("শেষ") +
                "\n\nএক দিনের ছুটি হলে একই তারিখ আবার পাঠান।")

    if step == "leave_end":
        leave_type, start_iso = parts[1], parts[2]
        start = datetime.fromisoformat(start_iso).date()
        end = parse_leave_date(text)
        if not end:
            return "❌ তারিখ বুঝতে পারিনি।\n\n" + _date_prompt("শেষ")
        error = validate_range(employee["id"], start, end)
        if error:
            return error
        set_state(phone, f"leave_reason:{leave_type}:{start_iso}:{end.isoformat()}")
        days = (end - start).days + 1
        return (f"✅ {start_iso} → {end.isoformat()} ({days} দিন)\n\n"
                "📝 ছুটির কারণ সংক্ষেপে লিখুন।")

    if step == "leave_reason":
        leave_type, start_iso, end_iso = parts[1], parts[2], parts[3]
        reason = " ".join((text or "").strip().split())[:MAX_REASON_CHARS]
        if len(reason) < 3:
            return "❌ কারণটি অন্তত ৩ অক্ষরের হতে হবে। আবার লিখুন।"
        set_state(phone, f"leave_confirm:{leave_type}:{start_iso}:{end_iso}:{_encode_reason(reason)}")
        start = datetime.fromisoformat(start_iso).date()
        end = datetime.fromisoformat(end_iso).date()
        days = (end - start).days + 1
        return (f"📋 আবেদন যাচাই করুন:\n\n"
                f"ধরন: {leave_type}\n"
                f"তারিখ: {start_iso} → {end_iso} ({days} দিন)\n"
                f"কারণ: {reason}\n\n"
                "সব ঠিক থাকলে লিখুন: YES\nবাতিল করতে লিখুন: CANCEL")

    if step == "leave_confirm":
        command = " ".join((text or "").strip().lower().split())
        if command not in {"yes", "y", "confirm", "হ্যাঁ", "ha", "ok"}:
            return "সব ঠিক থাকলে YES লিখুন, অথবা বাতিল করতে CANCEL লিখুন।"
        leave_type, start_iso, end_iso = parts[1], parts[2], parts[3]
        reason = _decode_reason(parts[4] if len(parts) > 4 else "")
        start = datetime.fromisoformat(start_iso).date()
        end = datetime.fromisoformat(end_iso).date()
        # Re-validate at submit time: HR may have approved something else while
        # the employee was typing.
        error = validate_range(employee["id"], start, end)
        if error:
            clear_state(phone)
            return error + "\n\nআবার শুরু করতে লিখুন: Leave"
        try:
            with get_db() as c:
                c.execute(
                    "INSERT INTO leave_requests(employee_id,leave_type,start_date,end_date,reason,requested_by) "
                    "VALUES(?,?,?,?,?,?)",
                    (employee["id"], leave_type, start_iso, end_iso, reason, "employee:whatsapp"),
                )
        except Exception:
            logger.exception("Leave request insert failed employee_id=%s", employee["id"])
            clear_state(phone)
            return "⚠️ আবেদন সংরক্ষণ করা যায়নি। একটু পরে আবার চেষ্টা করুন।"
        clear_state(phone)
        days = (end - start).days + 1
        return (f"✅ ছুটির আবেদন জমা হয়েছে।\n\n"
                f"ধরন: {leave_type}\n"
                f"তারিখ: {start_iso} → {end_iso} ({days} দিন)\n"
                f"অবস্থা: ⏳ HR অনুমোদনের অপেক্ষায়\n\n"
                "সিদ্ধান্ত হলে আপনাকে WhatsApp-এ জানানো হবে।\n"
                "অবস্থা দেখতে লিখুন: My Leave")

    # Unknown leave_* state: fail safe rather than trapping the employee.
    clear_state(phone)
    return "প্রক্রিয়াটি বাতিল হয়েছে। আবার শুরু করতে লিখুন: Leave"


def leave_report(employee) -> str:
    """Recent requests and their status, for the `my leave` command."""
    with get_db() as c:
        rows = c.execute(
            "SELECT leave_type,start_date,end_date,status FROM leave_requests "
            "WHERE employee_id=? ORDER BY id DESC LIMIT 5",
            (employee["id"],)).fetchall()
    if not rows:
        return "ℹ️ আপনার কোনো ছুটির আবেদন নেই।\n\nনতুন আবেদন করতে লিখুন: Leave"
    icons = {"pending": "⏳", "approved": "✅", "rejected": "❌"}
    output = [f"🗓️ {employee['name']}-এর সাম্প্রতিক ছুটির আবেদন:"]
    for row in rows:
        status = str(row["status"])
        output.append(f"{icons.get(status,'•')} {row['leave_type']} | "
                      f"{row['start_date']} → {row['end_date']} | {status}")
    return "\n".join(output)


def decision_message(row, status: str) -> str:
    """Text pushed to the employee when HR approves or rejects."""
    if status == "approved":
        return (f"✅ আপনার ছুটির আবেদন অনুমোদিত হয়েছে।\n\n"
                f"ধরন: {row['leave_type']}\n"
                f"তারিখ: {row['start_date']} → {row['end_date']}\n\n"
                "এই দিনগুলোতে Check In করার প্রয়োজন নেই।")
    return (f"❌ আপনার ছুটির আবেদন অনুমোদিত হয়নি।\n\n"
            f"ধরন: {row['leave_type']}\n"
            f"তারিখ: {row['start_date']} → {row['end_date']}\n\n"
            "বিস্তারিত জানতে HR-এর সঙ্গে যোগাযোগ করুন।")
