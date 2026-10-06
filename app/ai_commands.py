"""Manage duty and payroll by typing an instruction in plain Bangla or English.

    "রানা, সজীব, কুদ্দুস এদের কাল সকাল ১০টা থেকে ৪টা পর্যন্ত duty দাও"
    "Rana ke ei mashe 500 taka bonus dao, Eid er jonno"

The AI never changes anything. It only turns the sentence into a plan. The plan
is checked here, shown to the Admin as "what will change", and applied only
when the Admin presses Confirm — through the very same functions the Duty and
Payroll pages use, so every existing rule (permissions, locked payslips, audit
log, payroll change history) still applies.

Anything unclear — a name matching two people, an unknown name, a finalized
payslip — blocks the whole plan instead of being guessed.

Deliberately not possible from here: finalizing or paying payroll, deleting
employees, changing attendance. Those stay on their own pages.
"""
import json
import logging
import re
import time
from datetime import datetime, timedelta
from html import escape

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import text

from app import ai_insights as ai
from app.database import get_db
from app.time_format import format_time_12h

logger = logging.getLogger(__name__)
router = APIRouter()

DRAFT_TTL_SECONDS = 15 * 60
MAX_DUTY_DAYS = 62
MAX_ACTIONS = 10
MAX_AMOUNT = 10_000_000
WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]

# payroll field -> (label, payroll_records column, save_payroll argument, needs a reason)
PAYROLL_FIELDS = {
    "bonus": ("Bonus", "bonus", "bonus", True),
    "advance": ("Advance", "advance_amount", "advance", True),
    "fine": ("Fine", "fine_amount", "fine", True),
    "deduction": ("Other deduction", "deduction", "deduction", True),
    "overtime_hours": ("Overtime hours", "overtime_hours", "overtime_hours", False),
    "night_allowance": ("Night allowance", "night_allowance", "night_allowance", False),
    "friday_allowance": ("Friday allowance", "friday_allowance", "friday_allowance", False),
    "eid_duty_allowance": ("Eid duty allowance", "eid_duty_allowance", "eid_duty_allowance", False),
}
PERMISSION_FOR = {"duty_assign": "duty_manage", "duty_cancel": "duty_manage", "payroll_adjust": "payroll_manage",
                  "salary_set": "payroll_manage", "payroll_prepare": "payroll_manage"}

SYSTEM_PROMPT = """You turn an HR manager's instruction (Bengali, Banglish or English) into a JSON plan for an attendance and payroll system in Bangladesh. You never act; a person reviews the plan.

Reply with JSON only:
{"actions": [...], "unclear": ["<short note in the instruction's language>", ...]}

Action types:
{"type":"duty_assign","staff_ids":["..."],"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD","weekdays":null,"start_time":"HH:MM","end_time":"HH:MM","break_minutes":0,"note":""}
{"type":"duty_cancel","staff_ids":["..."],"start_date":"YYYY-MM-DD","end_date":"YYYY-MM-DD"}
{"type":"payroll_adjust","staff_ids":["..."],"month":"YYYY-MM","field":"bonus|advance|fine|deduction|overtime_hours|night_allowance|friday_allowance|eid_duty_allowance","op":"add|set","amount":0,"reason":""}
{"type":"salary_set","staff_ids":["..."],"fixed_salary":0}
{"type":"payroll_prepare","month":"YYYY-MM"}

Rules:
- Use only staff_ids from the employee list. Match names loosely (spelling, Bangla/English). "সবাই"/"all" means every listed employee.
- If a name matches more than one employee, or nobody, do NOT guess: leave that person out and add a note to "unclear".
- Dates: no date means today. Understand আজ/aj, কাল/kal (tomorrow), পরশু, weekday names, "এই সপ্তাহ", "আগামী সপ্তাহ", and ranges. One day: start_date = end_date.
- weekdays: null for every day in the range, or a list using 0=Monday ... 6=Sunday (Friday is 4) when only some days are meant.
- Times are 24-hour HH:MM. "সকাল ১০টা" = 10:00. A bare end hour that is earlier than a morning start is afternoon/evening: "১০টা থেকে ৪টা" = 10:00 to 16:00. রাত/night duty may end after midnight.
- payroll month: no month means the current month. "গত মাস" = previous month.
- op: "add" for giving/adding ("bonus দাও", "আরও ২ ঘণ্টা"), "set" when a total is stated ("bonus ৫০০ করো", "overtime মোট ১০ ঘণ্টা").
- reason: copy any reason given, in the user's words; else "".
- salary_set only when the monthly basic salary itself is changed.
- Finalizing or paying payroll, deleting anything else, changing attendance, leave, or anything not listed: return no action and explain in "unclear".
- Amounts are numbers without currency. Never invent names, dates or amounts."""


def apply_ai_command_migrations(engine, sqlite: bool) -> None:
    pk = "INTEGER PRIMARY KEY AUTOINCREMENT" if sqlite else "BIGSERIAL PRIMARY KEY"
    big = "INTEGER" if sqlite else "BIGINT"
    with engine.begin() as conn:
        conn.execute(text(
            f"""CREATE TABLE IF NOT EXISTS ai_command_drafts(
                id {pk}, created_by TEXT NOT NULL, command TEXT NOT NULL, plan TEXT NOT NULL,
                created_at {big} NOT NULL, applied_at {big}, result TEXT)"""))


# ----------------------------------------------------------------- validation

def _actor(request: Request) -> str:
    return "admin" if request.session.get("admin") else f"hr:{request.session.get('hr_id')}"


def _date(value) -> str:
    return datetime.strptime(str(value), "%Y-%m-%d").strftime("%Y-%m-%d")


def _clock(value) -> str:
    return datetime.strptime(str(value).strip(), "%H:%M").strftime("%H:%M")


def _amount(value) -> float:
    number = round(float(value), 2)
    if not 0 <= number <= MAX_AMOUNT:
        raise ValueError("amount out of range")
    return number


def _dates(start: str, end: str, weekdays) -> list[str]:
    first, last = datetime.strptime(start, "%Y-%m-%d").date(), datetime.strptime(end, "%Y-%m-%d").date()
    if last < first:
        raise ValueError("end before start")
    days, current = [], first
    while current <= last:
        if weekdays is None or current.weekday() in weekdays:
            days.append(current.isoformat())
        current += timedelta(days=1)
        if len(days) > MAX_DUTY_DAYS or (current - first).days > 370:
            raise ValueError("too many days")
    return days


def clean_plan(raw, employees: dict[str, dict]) -> tuple[list[dict], list[str]]:
    """Keep only well-formed actions about real, active employees."""
    problems: list[str] = []
    actions: list[dict] = []
    if not isinstance(raw, dict) or not isinstance(raw.get("actions"), list):
        raise ai.AIUnavailable("The AI gave an answer that could not be read. Try again.")
    for note in (raw.get("unclear") or [])[:8]:
        if str(note).strip():
            problems.append(str(note).strip()[:300])
    for item in raw["actions"][:MAX_ACTIONS]:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type") or "")
        try:
            action: dict = {"type": kind}
            if kind != "payroll_prepare":
                ids = []
                for staff_id in item.get("staff_ids") or []:
                    key = str(staff_id).strip().upper()
                    if key not in employees:
                        problems.append(f"Staff ID {str(staff_id)[:40]} is not an active employee.")
                    elif key not in ids:
                        ids.append(key)
                if not ids:
                    problems.append("One instruction did not name any employee the system knows.")
                    continue
                action["staff_ids"] = ids
            if kind in ("duty_assign", "duty_cancel"):
                weekdays = item.get("weekdays")
                if weekdays is not None:
                    weekdays = sorted({int(d) for d in weekdays if 0 <= int(d) <= 6}) or None
                action.update(start_date=_date(item.get("start_date")), end_date=_date(item.get("end_date")), weekdays=weekdays)
                action["dates"] = _dates(action["start_date"], action["end_date"], weekdays)
                if not action["dates"]:
                    problems.append("No day in that date range matches the weekdays given.")
                    continue
                if kind == "duty_assign":
                    action.update(start_time=_clock(item.get("start_time")), end_time=_clock(item.get("end_time")),
                                  break_minutes=max(0, int(item.get("break_minutes") or 0)),
                                  note=str(item.get("note") or "").strip()[:200])
                    if action["start_time"] == action["end_time"]:
                        raise ValueError("zero-length duty")
            elif kind == "payroll_adjust":
                field = str(item.get("field") or "")
                if field not in PAYROLL_FIELDS:
                    raise ValueError("unknown payroll field")
                action.update(month=datetime.strptime(str(item.get("month")), "%Y-%m").strftime("%Y-%m"), field=field,
                              op="set" if item.get("op") == "set" else "add", amount=_amount(item.get("amount")),
                              reason=str(item.get("reason") or "").strip()[:300])
            elif kind == "salary_set":
                action["fixed_salary"] = _amount(item.get("fixed_salary"))
            elif kind == "payroll_prepare":
                action["month"] = datetime.strptime(str(item.get("month")), "%Y-%m").strftime("%Y-%m")
            else:
                problems.append("One part of the instruction is something this cannot do.")
                continue
            actions.append(action)
        except (TypeError, ValueError):
            problems.append("One part of the instruction had a date, time or amount that could not be understood.")
    return actions, problems


def _employees(db) -> dict[str, dict]:
    return {str(r["staff_id"]).upper(): dict(r) for r in db.execute(
        "SELECT id,staff_id,name,fixed_salary,default_overtime_rate FROM employees WHERE is_active ORDER BY staff_id").fetchall()}


def _payroll_row(db, employee_id: int, month: str):
    return db.execute("SELECT * FROM payroll_records WHERE employee_id=? AND salary_month=?", (employee_id, month)).fetchone()


def describe(actions: list[dict], command: str, can) -> tuple[list[dict], list[str]]:
    """Work out exactly what each action would change, and what blocks it."""
    rows: list[dict] = []
    blockers: list[str] = []
    with get_db() as c:
        employees = _employees(c)
        for index, action in enumerate(actions):
            kind = action["type"]
            if not can(PERMISSION_FOR[kind]):
                blockers.append(f"You do not have permission for: {kind.replace('_', ' ')}.")
                continue
            people = [employees[s] for s in action.get("staff_ids", []) if s in employees]
            if kind != "payroll_prepare" and len(people) != len(action.get("staff_ids", [])):
                blockers.append("An employee in this plan is no longer active.")
                continue
            if kind == "duty_assign":
                span = action["dates"][0] if len(action["dates"]) == 1 else f"{action['dates'][0]} → {action['dates'][-1]} ({len(action['dates'])} days)"
                when = f"{format_time_12h(action['start_time'])} – {format_time_12h(action['end_time'])}"
                if action["end_time"] < action["start_time"]:
                    when += " (ends next day)"
                for person in people:
                    marks = ",".join("?" * len(action["dates"]))
                    existing = c.execute(
                        f"SELECT COUNT(*) n FROM custom_duties WHERE employee_id=? AND duty_date IN ({marks})",
                        (person["id"], *action["dates"])).fetchone()["n"]
                    rows.append({"action": index, "who": person, "what": "Assign duty", "detail": f"{span} • {when}",
                                 "warn": f"Replaces the duty already set on {existing} of these days" if existing else ""})
            elif kind == "duty_cancel":
                for person in people:
                    marks = ",".join("?" * len(action["dates"]))
                    existing = c.execute(
                        f"SELECT COUNT(*) n FROM custom_duties WHERE employee_id=? AND duty_date IN ({marks})",
                        (person["id"], *action["dates"])).fetchone()["n"]
                    span = action["dates"][0] if len(action["dates"]) == 1 else f"{action['dates'][0]} → {action['dates'][-1]}"
                    rows.append({"action": index, "who": person, "what": "Cancel duty", "detail": f"{span} • {existing} duty day(s) will be removed",
                                 "warn": "" if existing else "No duty is set on these dates — nothing to remove"})
            elif kind == "payroll_adjust":
                label, column, _, needs_reason = PAYROLL_FIELDS[action["field"]]
                unit = "h" if action["field"] == "overtime_hours" else "৳"
                for person in people:
                    record = _payroll_row(c, person["id"], action["month"])
                    if record and record["payment_status"] in ("finalized", "paid"):
                        blockers.append(f"{person['name']}'s {action['month']} payroll is {record['payment_status']} and locked. Reopen it on the Payroll page first.")
                        continue
                    if not record and float(person["fixed_salary"] or 0) <= 0:
                        blockers.append(f"{person['name']} has no basic salary set, so a {action['month']} payslip cannot be created.")
                        continue
                    old = float(record[column] or 0) if record else 0.0
                    new = action["amount"] if action["op"] == "set" else round(old + action["amount"], 2)
                    show = (lambda v: f"{v:g}h") if unit == "h" else (lambda v: f"৳{v:,.2f}")
                    rows.append({"action": index, "who": person, "what": f"{label} • {action['month']}",
                                 "detail": f"{show(old)} → {show(new)}",
                                 "warn": "" if record else "No payslip yet for this month — a draft will be created"})
                if needs_reason and not (action["reason"] or command.strip()):
                    blockers.append(f"{label} needs a reason.")
            elif kind == "salary_set":
                for person in people:
                    rows.append({"action": index, "who": person, "what": "Monthly basic salary",
                                 "detail": f"৳{float(person['fixed_salary'] or 0):,.2f} → ৳{action['fixed_salary']:,.2f}",
                                 "warn": "Applies to payslips prepared from now on"})
            elif kind == "payroll_prepare":
                missing = c.execute(
                    "SELECT COUNT(*) n FROM employees e WHERE e.is_active AND COALESCE(e.fixed_salary,0)>0 AND NOT EXISTS "
                    "(SELECT 1 FROM payroll_records p WHERE p.employee_id=e.id AND p.salary_month=?)", (action["month"],)).fetchone()["n"]
                rows.append({"action": index, "who": None, "what": f"Prepare payroll • {action['month']}",
                             "detail": f"Draft payslips will be created for {missing} employee(s) who have none yet",
                             "warn": "" if missing else "Everyone already has a payslip for this month"})
    return rows, blockers


# ---------------------------------------------------------------------- apply

def _apply_action(request: Request, action: dict, command: str) -> int:
    """Run one action through the same functions the normal pages use."""
    from app import main
    done = 0
    with get_db() as c:
        employees = _employees(c)
    people = [employees[s] for s in action.get("staff_ids", []) if s in employees]
    kind = action["type"]
    if kind == "duty_assign":
        for person in people:
            for day in action["dates"]:
                main.save_custom_duty(request, employee_id=person["id"], duty_date=day, start_time=action["start_time"],
                                      end_time=action["end_time"], office_name="BURAQ Office",
                                      note=action["note"] or "AI command", break_minutes=action["break_minutes"])
                done += 1
    elif kind == "duty_cancel":
        for person in people:
            with get_db() as c:
                marks = ",".join("?" * len(action["dates"]))
                ids = [r["id"] for r in c.execute(
                    f"SELECT id FROM custom_duties WHERE employee_id=? AND duty_date IN ({marks})",
                    (person["id"], *action["dates"])).fetchall()]
            for duty_id in ids:
                main.delete_custom_duty(request, duty_id)
                done += 1
    elif kind == "payroll_adjust":
        _, column, argument, _ = PAYROLL_FIELDS[action["field"]]
        for person in people:
            with get_db() as c:
                record = _payroll_row(c, person["id"], action["month"])
            values = {
                "fixed_salary": float((record["fixed_salary"] if record else person["fixed_salary"]) or 0),
                "overtime_hours": float(record["overtime_hours"] or 0) if record else 0.0,
                "overtime_rate": float((record["overtime_rate"] if record else person["default_overtime_rate"]) or 0),
                "night_allowance": float(record["night_allowance"] or 0) if record else 0.0,
                "friday_allowance": float(record["friday_allowance"] or 0) if record else 0.0,
                "eid_duty_allowance": float(record["eid_duty_allowance"] or 0) if record else 0.0,
                "bonus": float(record["bonus"] or 0) if record else 0.0,
                "advance": float(record["advance_amount"] or 0) if record else 0.0,
                "fine": float(record["fine_amount"] or 0) if record else 0.0,
                "deduction": float(record["deduction"] or 0) if record else 0.0,
            }
            old = values[argument]
            values[argument] = action["amount"] if action["op"] == "set" else round(old + action["amount"], 2)
            reason = action["reason"] or (str(record["adjustment_reason"] or "") if record else "") or f"AI command: {command}"[:300]
            response = main.save_payroll(
                request, employee_id=person["id"], salary_month=action["month"], overtime_mode="manual",
                adjustment_reason=reason, note=str(record["note"] or "") if record else "",
                return_month=action["month"], profile_employee_id=0, **values)
            if "error" in str(response.headers.get("location", "")):
                raise HTTPException(400, f"Payroll for {person['name']} was not accepted")
            done += 1
    elif kind == "salary_set":
        for person in people:
            main.salary_master(request, person["id"], fixed_salary=action["fixed_salary"],
                               overtime_rate=float(person["default_overtime_rate"] or 0), return_month="")
            done += 1
    elif kind == "payroll_prepare":
        main.payroll_bulk_prepare(request, month=action["month"])
        done += 1
    return done


# ---------------------------------------------------------------------- pages

def _plan_table(rows: list[dict]) -> str:
    body = "".join(
        "<tr><td>" + (f"<b>{escape(r['who']['name'])}</b><div class='sub'>{escape(r['who']['staff_id'])}</div>" if r["who"] else "<b>All employees</b>")
        + f"</td><td>{escape(r['what'])}</td><td><b>{escape(r['detail'])}</b>"
        + (f"<div class='sub' style='color:#b45309'>{escape(r['warn'])}</div>" if r["warn"] else "") + "</td></tr>"
        for r in rows
    )
    return ("<table><thead><tr><th>Employee</th><th>Change</th><th>What will happen</th></tr></thead>"
            f"<tbody>{body}</tbody></table>") if rows else ""


def _render(request: Request, command: str, rows=None, blockers=None, draft_id: int = 0, done: str = "", error: str = ""):
    from app.main import layout
    parts = []
    if error:
        parts.append(f"<div class='notice' style='background:#fee2e2;color:#991b1b'>{escape(error)}</div>")
    if done:
        parts.append(f"<div class='notice'>{escape(done)}</div>")
    if blockers:
        items = "".join(f"<li>{escape(b)}</li>" for b in blockers)
        parts.append("<div class='notice' style='background:#fef3c7;color:#92400e'><b>Nothing can be applied yet.</b> "
                     f"Fix these and send the instruction again:<ul style='padding-left:20px;margin-top:6px'>{items}</ul></div>")
    if rows:
        confirm = ""
        if draft_id and not blockers:
            confirm = (f"<form method='post' action='/ai/command/{draft_id}/apply' class='actions' style='margin-top:16px'>"
                       "<button class='btn'>Confirm and apply</button><a class='btn secondary' href='/ai/command'>Cancel</a></form>"
                       "<div class='sub'>Nothing has changed yet. This plan expires in 15 minutes.</div>")
        parts.append(f"<div class='card' style='overflow:auto'><div class='eyebrow'>Your instruction</div><h3>{escape(command)}</h3>"
                     f"{_plan_table(rows)}{confirm}</div><div class='section-gap'></div>")
    configured = bool(ai.api_key())
    disabled = "" if configured else " disabled"
    if not configured:
        parts.insert(0, "<div class='notice' style='background:#fef3c7;color:#92400e'>Gemini is not connected. Add <b>GEMINI_API_KEY</b> in Railway Variables.</div>")
    body = f"""<div class='hero'><div><div class='eyebrow'>AI command</div><h2>Manage by typing</h2>
    <div class='sub'>Type what you want in Bangla or English. You will see exactly what changes before anything is saved.</div></div>
    <a class='btn secondary' href='/ai'>AI insights</a></div>
    {''.join(parts)}
    <div class='card'><h3>New instruction</h3>
    <form method='post' action='/ai/command'><input name='command' maxlength='500' required{disabled}
    placeholder='যেমন: রানা, সজীব, কুদ্দুস এদের কাল সকাল ১০টা থেকে ৪টা পর্যন্ত duty দাও'>
    <button class='btn'{disabled}>Show plan</button></form>
    <div class='sub' style='margin-top:10px'>Can do: assign or cancel duty • bonus, advance, fine, deduction, overtime hours and allowances •
    change basic salary • prepare a month's payroll.<br>Cannot do: finalize or pay payroll, change attendance or leave, delete employees.</div>
    <div class='sub' style='margin-top:6px'>Employee names and your instruction are sent to Gemini to understand it.</div></div>"""
    return layout("AI command", body, request, "ai")


@router.get("/ai/command", response_class=HTMLResponse)
def command_page(request: Request, done: str = "", error: str = ""):
    from app.main import require_permission
    require_permission(request, "ai_manage")
    return _render(request, "", done=done[:300], error=error[:300])


@router.post("/ai/command", response_class=HTMLResponse)
def command_plan(request: Request, command: str = Form(...)):
    from app.main import require_permission, has_permission, audit
    require_permission(request, "ai_manage")
    command = " ".join(command.split())[:500]
    if not command:
        return _render(request, "", error="Type an instruction first.")
    with get_db() as c:
        employees = _employees(c)
    today = ai._now()
    prompt = json.dumps({
        "today": today.strftime("%Y-%m-%d"), "weekday": today.strftime("%A"), "current_month": today.strftime("%Y-%m"),
        "employees": [{"staff_id": e["staff_id"], "name": e["name"]} for e in employees.values()],
        "instruction": command,
    }, ensure_ascii=False)
    try:
        actions, problems = clean_plan(ai.ask_gemini(SYSTEM_PROMPT, prompt), employees)
    except ai.AIUnavailable as exc:
        return _render(request, command, error=str(exc))
    rows, blockers = describe(actions, command, lambda permission: has_permission(request, permission))
    blockers = problems + blockers
    if not rows and not blockers:
        blockers = ["The instruction did not contain anything that can be done here."]
    draft_id = 0
    if rows and not blockers:
        with get_db() as c:
            c.execute("INSERT INTO ai_command_drafts(created_by,command,plan,created_at) VALUES(?,?,?,?)",
                      (_actor(request), command, json.dumps(actions), int(time.time())))
            draft_id = c.execute("SELECT MAX(id) m FROM ai_command_drafts WHERE created_by=?", (_actor(request),)).fetchone()["m"]
    audit(request, "ai_command_plan", "ai", str(draft_id or ""), command[:200])
    return _render(request, command, rows=rows, blockers=blockers, draft_id=draft_id)


@router.post("/ai/command/{draft_id}/apply", response_class=HTMLResponse)
def command_apply(request: Request, draft_id: int):
    from app.main import require_permission, has_permission, audit
    require_permission(request, "ai_manage")
    now = int(time.time())
    with get_db() as c:
        draft = c.execute("SELECT * FROM ai_command_drafts WHERE id=?", (draft_id,)).fetchone()
        if not draft or draft["created_by"] != _actor(request):
            raise HTTPException(404, "Plan not found")
        if draft["applied_at"]:
            return _render(request, "", error="This plan was already applied.")
        if now - int(draft["created_at"]) > DRAFT_TTL_SECONDS:
            return _render(request, "", error="This plan expired. Send the instruction again.")
        # Claim it first so a double click cannot apply the same plan twice.
        claimed = c.execute("UPDATE ai_command_drafts SET applied_at=? WHERE id=? AND applied_at IS NULL", (now, draft_id))
        if getattr(claimed.result, "rowcount", 1) == 0:
            return _render(request, "", error="This plan was already applied.")
    actions, command = json.loads(draft["plan"]), draft["command"]
    # Things may have changed since the preview (a payslip finalized, someone deactivated).
    rows, blockers = describe(actions, command, lambda permission: has_permission(request, permission))
    if blockers:
        with get_db() as c:
            c.execute("UPDATE ai_command_drafts SET result=? WHERE id=?", ("blocked", draft_id))
        return _render(request, command, rows=rows, blockers=blockers)
    applied, failures = 0, []
    for action in actions:
        try:
            applied += _apply_action(request, action, command)
        except HTTPException as exc:
            failures.append(str(exc.detail))
        except Exception:
            logger.exception("AI command action failed draft=%s", draft_id)
            failures.append(f"{action['type'].replace('_', ' ')} failed")
    summary = f"{applied} change(s) applied" + (f"; {len(failures)} failed" if failures else "")
    with get_db() as c:
        c.execute("UPDATE ai_command_drafts SET result=? WHERE id=?", (summary[:300], draft_id))
    audit(request, "ai_command_apply", "ai", str(draft_id), f"{summary} — {command[:150]}")
    if failures:
        return _render(request, command, rows=rows, error=f"{summary}. Not done: " + "; ".join(failures)[:400])
    return _render(request, "", done=f"Done — {summary}: “{command}”")
