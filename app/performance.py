"""Monthly duty performance scoring and WhatsApp notices (v9.26).

Scores every employee's month from data BURAQ already records — scheduled duty,
worked duty, late minutes and incomplete days — and produces two kinds of
message:

* ``star``     — top performer of the month, a public-style congratulation.
* ``good``     — a strong month worth acknowledging, but not the top spot.
* ``coaching`` — a private, factual note for a weak month.

Two deliberate design decisions, both about not doing harm with automation:

1. **Nothing sends by itself.** This module only *computes* and *renders*.
   HR presses a button per employee on ``/performance-awards``.  Attendance
   data in this system has known failure modes — a GPS timeout, a selfie stuck
   in pending review, a forgotten check-out — and each one looks exactly like
   poor performance to a query.  A human confirms before anyone is told they
   had a bad month.

2. **The coaching notice never labels the person.**  It states the numbers, it
   asks whether something got in the way, and it invites a conversation.  It
   does not say "bad employee", it is never sent to anyone but the employee,
   and it never ranks them against a colleague.  Telling somebody by automated
   message that they are the worst worker in the office reliably produces
   resentment and turnover, not attendance.

Duty maths is not reimplemented here: ``employee_metrics`` calls the same
``_payroll_duty_metrics`` that payroll uses, so a score can never disagree with
a payslip.
"""
from __future__ import annotations

import logging
from datetime import datetime

from sqlalchemy.exc import IntegrityError

from app.database import get_db

logger = logging.getLogger(__name__)

# An employee needs a real month behind them before a score means anything.
# Without this a new joiner with two flawless days outranks everyone.
MIN_SCHEDULED_DAYS = 10

STAR_MIN_SCORE = 90.0      # don't crown a mediocre month
GOOD_MIN_SCORE = 82.0
COACHING_MAX_SCORE = 60.0  # below this, HR is offered a private note

# Average lateness per worked day at which the punctuality component hits zero.
LATE_ZERO_MINUTES = 30.0

WEIGHT_ATTENDANCE = 50.0
WEIGHT_PUNCTUALITY = 35.0
WEIGHT_COMPLETENESS = 15.0

NOTICE_TYPES = ("star", "good", "coaching")


def _round(value: float) -> float:
    return round(float(value), 1)


def score_from_metrics(metrics: dict) -> dict:
    """Turn duty metrics into a 0-100 score plus its three components.

    Paid leave is removed from the denominator throughout: approved leave is a
    right, not an absence, and must never cost an employee their score.
    """
    scheduled = float(metrics.get("scheduled") or 0)
    worked = float(metrics.get("worked") or 0)
    paid_leave = float(metrics.get("paid_leave") or 0)
    late_minutes = float(metrics.get("late_minutes") or 0)
    incomplete = len(metrics.get("incomplete_dates") or [])

    expected = max(scheduled - paid_leave, 0.0)
    if expected <= 0:
        return {"score": 0.0, "attendance": 0.0, "punctuality": 0.0,
                "completeness": 0.0, "expected_days": 0.0, "eligible": False}

    attendance_ratio = min(worked / expected, 1.0)

    if worked > 0:
        average_late = late_minutes / worked
        punctuality_ratio = max(0.0, 1.0 - (average_late / LATE_ZERO_MINUTES))
    else:
        punctuality_ratio = 0.0

    completeness_ratio = max(0.0, 1.0 - (incomplete / expected))

    attendance = WEIGHT_ATTENDANCE * attendance_ratio
    punctuality = WEIGHT_PUNCTUALITY * punctuality_ratio
    completeness = WEIGHT_COMPLETENESS * completeness_ratio

    return {
        "score": _round(attendance + punctuality + completeness),
        "attendance": _round(attendance),
        "punctuality": _round(punctuality),
        "completeness": _round(completeness),
        "expected_days": expected,
        "eligible": scheduled >= MIN_SCHEDULED_DAYS,
    }


def suggested_notice(score: float, rank_position: int, eligible: bool) -> str | None:
    """Which notice, if any, HR should be offered for this row."""
    if not eligible:
        return None
    if rank_position == 1 and score >= STAR_MIN_SCORE:
        return "star"
    if score >= GOOD_MIN_SCORE:
        return "good"
    if score <= COACHING_MAX_SCORE:
        return "coaching"
    return None


def month_label(period: str) -> str:
    try:
        return datetime.strptime(period + "-01", "%Y-%m-%d").strftime("%B %Y")
    except ValueError:
        return period


def employee_metrics(employee_id: int, period: str) -> dict:
    """Duty metrics for one employee/month, from payroll's own function.

    Imported lazily: ``app.main`` imports this module, so a module-level import
    would be circular. By call time main is fully loaded.
    """
    from app.main import _payroll_duty_metrics
    return _payroll_duty_metrics(employee_id, period)


def monthly_ranking(period: str) -> list[dict]:
    """Every active, approved employee scored and ranked for `period`.

    Ineligible employees (too few scheduled days) are still returned so HR can
    see them, but they are sorted last and never receive a notice.
    """
    with get_db() as c:
        employees = c.execute(
            "SELECT id,staff_id,name,department,whatsapp_phone,phone FROM employees "
            "WHERE is_active AND registration_status='approved' ORDER BY name"
        ).fetchall()
        sent = c.execute(
            "SELECT employee_id,notice_type FROM performance_notices WHERE period=?",
            (period,)).fetchall()

    already = {(int(r["employee_id"]), str(r["notice_type"])) for r in sent}
    rows = []
    for employee in employees:
        try:
            metrics = employee_metrics(employee["id"], period)
        except Exception:
            logger.exception("Performance metrics failed employee=%s period=%s",
                             employee["id"], period)
            continue
        scored = score_from_metrics(metrics)
        rows.append({
            "employee_id": int(employee["id"]),
            "staff_id": employee["staff_id"],
            "name": employee["name"],
            "department": employee["department"],
            "phone": employee["whatsapp_phone"] or employee["phone"] or "",
            "scheduled": float(metrics.get("scheduled") or 0),
            "worked": float(metrics.get("worked") or 0),
            "paid_leave": float(metrics.get("paid_leave") or 0),
            "absent": float(metrics.get("absent") or 0),
            "late_minutes": int(metrics.get("late_minutes") or 0),
            "incomplete": len(metrics.get("incomplete_dates") or []),
            **scored,
        })

    # Eligible first, then by score; ineligible rows keep rank 0.
    rows.sort(key=lambda r: (not r["eligible"], -r["score"], r["name"]))
    position = 0
    for row in rows:
        if row["eligible"]:
            position += 1
            row["rank_position"] = position
        else:
            row["rank_position"] = 0
        row["suggested"] = suggested_notice(row["score"], row["rank_position"], row["eligible"])
        row["already_sent"] = sorted(t for (e, t) in already if e == row["employee_id"])
    return rows


def build_message(row: dict, notice_type: str, period: str) -> str:
    """The exact text an employee will receive. HR sees this before sending."""
    label = month_label(period)
    name = row["name"]
    worked = int(row["worked"]) if float(row["worked"]).is_integer() else row["worked"]
    expected = int(row["expected_days"]) if float(row["expected_days"]).is_integer() else row["expected_days"]

    if notice_type == "star":
        return (f"🏆 অভিনন্দন {name}!\n\n"
                f"{label} মাসে আপনি BURAQ-এর সেরা কর্মী নির্বাচিত হয়েছেন।\n\n"
                f"উপস্থিতি: {worked}/{expected} দিন\n"
                f"মোট দেরি: {row['late_minutes']} মিনিট\n"
                f"স্কোর: {row['score']}/100\n\n"
                "আপনার নিয়মানুবর্তিতা ও পরিশ্রমের জন্য ধন্যবাদ। 🌟")

    if notice_type == "good":
        return (f"👏 ধন্যবাদ {name}!\n\n"
                f"{label} মাসে আপনার উপস্থিতি ছিল প্রশংসনীয়।\n\n"
                f"উপস্থিতি: {worked}/{expected} দিন\n"
                f"মোট দেরি: {row['late_minutes']} মিনিট\n"
                f"স্কোর: {row['score']}/100\n\n"
                "এভাবেই চালিয়ে যান। 💪")

    # Coaching: numbers and an open question, never a verdict on the person.
    lines = [f"📋 {name}, {label} মাসের উপস্থিতির হিসাব:", ""]
    lines.append(f"উপস্থিত: {worked}/{expected} দিন")
    if row["absent"]:
        absent = int(row["absent"]) if float(row["absent"]).is_integer() else row["absent"]
        lines.append(f"অনুপস্থিত: {absent} দিন")
    if row["late_minutes"]:
        lines.append(f"মোট দেরি: {row['late_minutes']} মিনিট")
    if row["incomplete"]:
        lines.append(f"Check-out হয়নি: {row['incomplete']} দিন")
    lines += [
        "",
        "হিসাবটি ঠিক আছে কি না একবার দেখে নিন। কোনো দিনের তথ্য ভুল থাকলে",
        "WhatsApp-এ HR-কে জানান বা Attendance Correction-এর আবেদন করুন।",
        "",
        "কোনো সমস্যার কারণে অসুবিধা হয়ে থাকলে HR-এর সঙ্গে কথা বলুন —",
        "আমরা সমাধানে সাহায্য করতে চাই।",
    ]
    return "\n".join(lines)


def record_notice(employee_id: int, period: str, notice_type: str, row: dict,
                  message: str, sent_by: str) -> bool:
    """Atomically reserve/log a notice; False means it already exists."""
    try:
        with get_db() as c:
            c.execute(
                "INSERT INTO performance_notices(employee_id,period,notice_type,score,rank_position,message,sent_by) "
                "VALUES(?,?,?,?,?,?,?)",
                (employee_id, period, notice_type, float(row.get("score") or 0),
                 int(row.get("rank_position") or 0), message, sent_by))
        return True
    except IntegrityError:
        # The database UNIQUE constraint is the concurrency-safe duplicate
        # guard, including two HR users pressing send at the same moment.
        return False
