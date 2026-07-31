from datetime import datetime, time
from zoneinfo import ZoneInfo
import re

from app.config import settings
from app.database import get_db


def now_local():
    return datetime.now(ZoneInfo(settings.timezone))


def normalize_phone(value):
    return re.sub(r"\D", "", value or "")


def phones_match(first, second):
    a, b = normalize_phone(first), normalize_phone(second)
    return bool(a and b and (a == b or (len(a) >= 10 and len(b) >= 10 and a[-10:] == b[-10:])))


def state(phone):
    with get_db() as c:
        return c.execute("SELECT * FROM conversation_states WHERE phone=?", (normalize_phone(phone),)).fetchone()


def set_state(phone, value):
    with get_db() as c:
        c.execute(
            "INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP",
            (normalize_phone(phone), value),
        )


def clear_state(phone):
    with get_db() as c:
        c.execute("DELETE FROM conversation_states WHERE phone=?", (normalize_phone(phone),))


def employee_by_phone(phone):
    target = normalize_phone(phone)
    with get_db() as c:
        rows = c.execute("SELECT * FROM employees WHERE registration_status='approved'").fetchall()
    for employee in rows:
        if phones_match(employee["whatsapp_phone"], target) or phones_match(employee["phone"], target):
            return employee
    return None


def employee_by_staff_id(staff_id):
    with get_db() as c:
        return c.execute("SELECT * FROM employees WHERE LOWER(staff_id)=LOWER(?)", ((staff_id or "").strip(),)).fetchone()


def menu(name=None):
    greeting = f"স্বাগতম {name}" if name else "BURAQ Smart Attendance"
    return f"👋 {greeting}\n\n1️⃣ Register\n2️⃣ Check In\n3️⃣ Check Out\n4️⃣ My Attendance\n5️⃣ Help"


def registration_preview(employee):
    return (
        "👤 আপনার তথ্য পাওয়া গেছে\n\n"
        f"নাম: {employee['name']}\n"
        f"Staff ID: {employee['staff_id']}\n"
        f"Department: {employee['department'] or 'Not set'}\n"
        f"Shift: {(employee['shift'] or 'morning').title()}\n\n"
        "তথ্য সঠিক হলে YES লিখুন। বাতিল করতে CANCEL লিখুন।"
    )


def begin_registration(staff_id, phone):
    employee = employee_by_staff_id(staff_id)
    if not employee:
        set_state(phone, "awaiting_staff_id")
        return "❌ Staff ID পাওয়া যায়নি। আবার সঠিক Staff ID পাঠান।"
    set_state(phone, f"confirm_registration:{employee['id']}")
    return registration_preview(employee)


def confirm_registration(employee_id, phone):
    phone = normalize_phone(phone)
    with get_db() as c:
        employee = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            clear_state(phone)
            return "❌ Employee record পাওয়া যায়নি। আবার Register করুন।"
        if phones_match(employee["phone"], phone):
            c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (phone, employee["id"]))
            c.execute("UPDATE pending_registrations SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE employee_id=? AND status='pending'", (employee["id"],))
            clear_state(phone)
            return f"✅ Registration সফল হয়েছে\nনাম: {employee['name']}\nStaff ID: {employee['staff_id']}\n\nHi লিখে Attendance Menu খুলুন।"
        c.execute("UPDATE pending_registrations SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE employee_id=? AND status='pending'", (employee["id"],))
        c.execute("INSERT INTO pending_registrations(employee_id,whatsapp_phone) VALUES(?,?)", (employee["id"], phone))
        c.execute("UPDATE employees SET registration_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=?", (employee["id"],))
    clear_state(phone)
    return "⏳ WhatsApp নম্বর employee record-এর সঙ্গে মেলেনি। Admin approval-এর জন্য পাঠানো হয়েছে।"


def shift_times(shift):
    return (time(16), time(22)) if (shift or "").lower() == "evening" else (time(8), time(16))


def check_in(employee):
    current = now_local(); work_date = current.date().isoformat(); start, _ = shift_times(employee["shift"])
    late = max(0, int((current - datetime.combine(current.date(), start, tzinfo=current.tzinfo)).total_seconds() // 60))
    with get_db() as c:
        record = c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], work_date)).fetchone()
        if record and record["check_in"]:
            return f"ℹ️ আজ Check In করা হয়েছে: {datetime.fromisoformat(record['check_in']).strftime('%I:%M %p')}"
        if record:
            c.execute("UPDATE attendance SET check_in=?,late_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current.isoformat(timespec="seconds"), late, record["id"]))
        else:
            c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)", (employee["id"], work_date, current.isoformat(timespec="seconds"), late))
    return f"✅ Check In সফল\nসময়: {current.strftime('%I:%M %p')}" + (f"\n⏰ Late: {late} মিনিট" if late else "\n🟢 On time")


def check_out(employee):
    current = now_local(); work_date = current.date().isoformat(); _, end_time = shift_times(employee["shift"])
    early = max(0, int((datetime.combine(current.date(), end_time, tzinfo=current.tzinfo) - current).total_seconds() // 60))
    overtime = max(0, int((current - datetime.combine(current.date(), time(22), tzinfo=current.tzinfo)).total_seconds() // 60))
    with get_db() as c:
        record = c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], work_date)).fetchone()
        if not record or not record["check_in"]:
            return "❌ আগে Check In করতে হবে।"
        if record["check_out"]:
            return f"ℹ️ আজ Check Out করা হয়েছে: {datetime.fromisoformat(record['check_out']).strftime('%I:%M %p')}"
        worked = max(0, int((current - datetime.fromisoformat(record["check_in"])).total_seconds() // 60))
        c.execute("UPDATE attendance SET check_out=?,early_leave_minutes=?,overtime_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current.isoformat(timespec="seconds"), early, overtime, record["id"]))
    hours, minutes = divmod(worked, 60)
    message = f"✅ Check Out সফল\nসময়: {current.strftime('%I:%M %p')}\nকাজ করেছেন: {hours} ঘণ্টা {minutes} মিনিট"
    if early: message += f"\n⚠️ Early leave: {early} মিনিট"
    if overtime: message += f"\n⏱️ Overtime: {overtime} মিনিট"
    return message


def report(employee):
    with get_db() as c:
        rows = c.execute("SELECT * FROM attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 7", (employee["id"],)).fetchall()
    if not rows:
        return "ℹ️ কোনো attendance record নেই।"
    output = [f"📊 {employee['name']}-এর সর্বশেষ Attendance:"]
    for row in rows:
        output.append(f"{row['work_date']} | In {row['check_in'][11:16] if row['check_in'] else '-'} | Out {row['check_out'][11:16] if row['check_out'] else '-'} | Late {row['late_minutes']}m | OT {row['overtime_minutes']}m")
    return "\n".join(output)


def process(phone, text):
    command = " ".join((text or "").strip().lower().split())
    current_state = state(phone)
    if current_state:
        value = current_state["state"]
        if value == "awaiting_staff_id":
            if command in {"cancel", "বাতিল"}:
                clear_state(phone); return "Registration বাতিল হয়েছে। Hi লিখে Menu খুলুন।"
            return begin_registration(text, phone)
        if value.startswith("confirm_registration:"):
            if command in {"yes", "y", "confirm", "হ্যাঁ", "ha"}:
                return confirm_registration(int(value.split(":", 1)[1]), phone)
            if command in {"cancel", "no", "n", "না", "বাতিল"}:
                clear_state(phone); return "Registration বাতিল হয়েছে। আবার Register লিখুন।"
            return "তথ্য সঠিক হলে YES লিখুন, অথবা বাতিল করতে CANCEL লিখুন।"
    if command in {"hi", "hello", "menu", "start"}:
        employee = employee_by_phone(phone); return menu(employee["name"] if employee else None)
    if command in {"register", "1"}:
        if employee_by_phone(phone): return "✅ এই WhatsApp নম্বর ইতিমধ্যে registered।"
        set_state(phone, "awaiting_staff_id"); return "আপনার Staff ID পাঠান। উদাহরণ: BRQ001"
    if command in {"help", "5"}: return menu()
    employee = employee_by_phone(phone)
    if not employee: return "❌ আগে Register করুন। শুধু লিখুন: Register"
    if command in {"check in", "checkin", "check_in", "in", "2"}: return check_in(employee)
    if command in {"check out", "checkout", "check_out", "out", "3"}: return check_out(employee)
    if command in {"my attendance", "my_attendance", "attendance", "report", "4"}: return report(employee)
    return "বুঝতে পারিনি। Menu দেখতে লিখুন: Hi"


def log(direction, phone, typ, content, message_id=None):
    try:
        with get_db() as c:
            c.execute("INSERT INTO whatsapp_logs(direction,phone,message_type,content,message_id) VALUES(?,?,?,?,?)", (direction, normalize_phone(phone), typ, content, message_id))
        return True
    except Exception:
        return False
