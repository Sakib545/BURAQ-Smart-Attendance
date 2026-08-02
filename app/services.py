from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo
from math import asin, cos, radians, sin, sqrt
import re
import json
import logging
import random
import time as time_module

from app.face_ai import FaceAIError, extract_embedding, best_match, best_impostor, gallery_score
from app.face_log import invalidate_gallery_cache, load_impostor_gallery, log_face_event
from app.duplicate_detector import DuplicateThresholds, detect_duplicate, make_fingerprint
from app.config import settings, OFFICE_LATITUDE, OFFICE_LONGITUDE, OFFICE_RADIUS_METERS
from app.database import get_db
from app.time_format import format_time_12h




LIVENESS_CHALLENGE_TTL_SECONDS = 300
POSE_LABELS = {
    "straight": "সোজা সামনে তাকান",
    "left": "মাথা সামান্য বাম দিকে ঘুরিয়ে তাকান",
    "right": "মাথা সামান্য ডান দিকে ঘুরিয়ে তাকান",
}

def new_liveness_challenge():
    pose = "any" if settings.simple_face_mode else random.choice(tuple(POSE_LABELS))
    issued_at = int(time_module.time())
    return pose, issued_at

def liveness_prompt(pose):
    if settings.simple_face_mode:
        return (
            "📸 Face Verification\n\n"
            "👉 ক্যামেরার দিকে স্বাভাবিকভাবে তাকিয়ে একটি নতুন selfie পাঠান।\n"
            "⏳ সময়: ৫ মিনিট\n"
            "⚠️ ছবিতে শুধু আপনার মুখ রাখুন।"
        )
    return (
        "🛡️ Live Selfie Challenge\n\n"
        f"👉 {POSE_LABELS.get(pose, 'সোজা সামনে তাকান')}\n"
        "📸 নির্দেশনাটি মেনে এখনই একটি নতুন selfie তুলে পাঠান।\n\n"
        "⏳ সময়: ২ মিনিট\n"
        "⚠️ পুরোনো/gallery ছবি ব্যবহার করবেন না।"
    )

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
        c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP", (normalize_phone(phone), value))


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

def employee_by_known_phone(phone):
    target = normalize_phone(phone)
    with get_db() as c:
        rows = c.execute("SELECT * FROM employees WHERE is_active").fetchall()
    for employee in rows:
        if phones_match(employee["whatsapp_phone"], target) or phones_match(employee["phone"], target):
            return employee
    return None

def activate_known_phone(phone):
    employee=employee_by_known_phone(phone)
    if not employee: return None
    normalized=normalize_phone(phone)
    with get_db() as c:
        c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?",(normalized,employee['id']))
    return employee_by_phone(normalized)


def employee_by_staff_id(staff_id):
    with get_db() as c:
        return c.execute("SELECT * FROM employees WHERE LOWER(staff_id)=LOWER(?)", ((staff_id or "").strip(),)).fetchone()


def face_sample_count(employee_id):
    with get_db() as c:
        return c.execute("SELECT COUNT(*) c FROM face_samples WHERE employee_id=?", (employee_id,)).fetchone()["c"]


def has_face(employee_id):
    return face_sample_count(employee_id) >= 3


def menu(name=None):
    greeting = f"স্বাগতম {name}" if name else "BURAQ Smart Attendance"
    return f"👋 {greeting}\n\n1️⃣ Register\n2️⃣ Check In\n3️⃣ Check Out\n4️⃣ My Attendance\n5️⃣ Help"


def registration_preview(employee):
    return ("👤 আপনার তথ্য পাওয়া গেছে\n\n" f"নাম: {employee['name']}\n" f"Staff ID: {employee['staff_id']}\n" f"Department: {employee['department'] or 'Not set'}\n" f"Shift: {(employee['shift'] or 'morning').title()}\n\n" "তথ্য সঠিক হলে YES লিখুন। বাতিল করতে CANCEL লিখুন।")


def begin_registration(staff_id, phone):
    employee = employee_by_staff_id(staff_id)
    if not employee:
        set_state(phone, "awaiting_staff_id")
        return "❌ Staff ID পাওয়া যায়নি। আবার সঠিক Staff ID পাঠান।"
    return confirm_registration(employee['id'], phone)

def duty_report(employee):
    today=now_local().date(); days=[]
    with get_db() as c:
        rows=c.execute("SELECT * FROM duty_schedules WHERE employee_id=? AND is_active ORDER BY weekday",(employee['id'],)).fetchall()
        custom=c.execute("SELECT * FROM custom_duties WHERE employee_id=? AND duty_date>=? AND duty_date<=? AND is_active ORDER BY duty_date",(employee['id'],today.isoformat(),(today+timedelta(days=6)).isoformat())).fetchall()
    by_day={int(r['weekday']):r for r in rows}
    by_date={r['duty_date']:r for r in custom}
    for offset in range(7):
        day=today+timedelta(days=offset); duty=by_date.get(day.isoformat()) or by_day.get(day.weekday())
        if duty: days.append(f"{'⭐' if day.isoformat() in by_date else '📅'} {day.strftime('%a, %d %b')}: {format_time_12h(duty['start_time'])} - {format_time_12h(duty['end_time'])} ({duty['office_name'] or 'BURAQ Office'})")
    return "🗓️ আপনার Duty Schedule\n\n"+("\n".join(days) if days else "আগামী ৭ দিনে কোনো duty schedule নেই।")


def confirm_registration(employee_id, phone):
    phone = normalize_phone(phone)
    with get_db() as c:
        employee = c.execute("SELECT * FROM employees WHERE id=?", (employee_id,)).fetchone()
        if not employee:
            clear_state(phone); return "❌ Employee record পাওয়া যায়নি। আবার Register করুন।"
        if phones_match(employee["phone"], phone):
            c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?", (phone, employee["id"]))
            c.execute("UPDATE pending_registrations SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE employee_id=? AND status='pending'", (employee["id"],))
            c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP", (phone, "awaiting_face_registration"))
            return f"✅ Registration সফল হয়েছে\nনাম: {employee['name']}\nStaff ID: {employee['staff_id']}\n\n📸 এখন সামনে তাকিয়ে ৩টি পরিষ্কার selfie পাঠান। প্রথম selfie এখন পাঠান।"
        c.execute("UPDATE pending_registrations SET status='cancelled',updated_at=CURRENT_TIMESTAMP WHERE employee_id=? AND status='pending'", (employee["id"],))
        c.execute("INSERT INTO pending_registrations(employee_id,whatsapp_phone) VALUES(?,?)", (employee["id"], phone))
        c.execute("UPDATE employees SET registration_status='pending',updated_at=CURRENT_TIMESTAMP WHERE id=?", (employee["id"],))
    set_state(phone, "waiting_for_approval")
    return "⏳ WhatsApp নম্বর employee record-এর সঙ্গে মেলেনি। Admin approval-এর জন্য পাঠানো হয়েছে। Approve হলে পরের ধাপ নিজে থেকেই আসবে।"


def adapt_gallery(employee_id: int, embedding, quality: float, score: float, margin: float,
                  diagnostics: dict, media_id: str | None) -> None:
    """Let a confidently verified selfie join the gallery.

    A face profile built from three registration selfies slowly stops matching
    the person: beards, glasses, weight, ageing. Rather than making everyone
    re-register every few months, a verification that clears a much higher bar
    than normal acceptance is stored as an extra reference.

    The bar is deliberately strict — a wrong sample here poisons the profile
    permanently. Enrolment samples are never evicted.
    """
    if not settings.face_adapt_enabled:
        return
    if score < settings.face_adapt_min_score or quality < settings.face_adapt_min_quality:
        return
    if margin < settings.face_adapt_min_margin:
        return
    if diagnostics.get("liveness_verdict") != "live":
        return
    # Near-identical to something already stored adds nothing but bloat.
    if score > 0.97:
        return

    try:
        with get_db() as c:
            rows = c.execute("SELECT id,quality,source FROM face_samples WHERE employee_id=? ORDER BY id",
                             (employee_id,)).fetchall()
            c.execute("INSERT INTO face_samples(employee_id,media_id,embedding,quality,source) VALUES(?,?,?,?,?)",
                      (employee_id, media_id, json.dumps(embedding), quality, "auto"))

            auto = [r for r in rows if (r["source"] or "enroll") == "auto"]
            over = len(rows) + 1 - settings.face_gallery_max
            if over > 0 and auto:
                weakest = sorted(auto, key=lambda r: float(r["quality"] or 0))[:over]
                for row in weakest:
                    c.execute("DELETE FROM face_samples WHERE id=?", (row["id"],))
        invalidate_gallery_cache()
        log_face_event(employee_id=employee_id, stage="adapt", action="gallery_sample",
                       decision="accepted", reason="auto_enrolled", match_score=score,
                       quality=quality, diagnostics=diagnostics)
    except Exception:
        # Never let profile maintenance break a check-in.
        logging.getLogger(__name__).warning("gallery adaptation failed", exc_info=True)


ENROLL_POSES = ["straight", "left", "right"]
ENROLL_POSE_PROMPT = {
    "straight": "সোজা সামনে তাকিয়ে",
    "left": "মাথা সামান্য বাম দিকে ঘুরিয়ে",
    "right": "মাথা সামান্য ডান দিকে ঘুরিয়ে",
}


def save_face_reference(employee, media_id, image_bytes):
    started = time_module.perf_counter()
    target = settings.face_enroll_samples

    with get_db() as c:
        rows = c.execute("SELECT embedding FROM face_samples WHERE employee_id=? ORDER BY id", (employee["id"],)).fetchall()
    existing = [json.loads(r["embedding"]) for r in rows]
    index = len(existing)

    # Ask for a different angle each time. Without this every sample tends to be
    # the same straight-on shot, and the gallery has no tolerance for head turn.
    wanted_pose = "any" if settings.simple_face_mode else (ENROLL_POSES[index] if index < len(ENROLL_POSES) else "any")

    def record(decision, reason, **extra):
        log_face_event(employee_id=employee["id"], stage="enroll", action="sample_%d" % (index + 1),
                       decision=decision, reason=reason,
                       elapsed_ms=(time_module.perf_counter() - started) * 1000, **extra)

    try:
        embedding, quality, diagnostics = extract_embedding(image_bytes, required_pose=wanted_pose)
    except FaceAIError as exc:
        record("rejected", str(exc).splitlines()[0][:200])
        return "\u274c Face Registration হয়নি।\n%s" % exc

    if not settings.simple_face_mode and diagnostics.get("liveness_verdict") == "spoof":
        record("rejected", "liveness", diagnostics=diagnostics)
        return "\U0001f6ab ছবিটি স্ক্রিন বা ছাপানো ছবি বলে মনে হচ্ছে। সরাসরি ক্যামেরার সামনে থেকে live selfie দিন।"

    enroll_quality_min = min(settings.face_enroll_quality_min, 35.0) if settings.simple_face_mode else settings.face_enroll_quality_min
    if quality < enroll_quality_min:
        record("rejected", "quality", quality=quality, diagnostics=diagnostics)
        return ("\u274c এই selfie-এর মান কম (%.0f%%, দরকার %.0f%%)।\n"
                "Registration-এর ছবি সারাজীবন ব্যবহার হবে, তাই ভালো আলোতে ক্যামেরার কাছ থেকে আবার তুলুন।"
                % (quality, enroll_quality_min))

    with get_db() as c:
        if existing:
            score = gallery_score(embedding, existing)
            if score < (0.38 if settings.simple_face_mode else 0.42):
                record("rejected", "identity_mismatch", match_score=score, quality=quality, diagnostics=diagnostics)
                return "\u274c আগের selfie-এর সঙ্গে এই মুখ মিলছে না। একই employee নিজের selfie দিন।"
            if not settings.simple_face_mode and best_match(embedding, existing) > 0.985:
                record("rejected", "identical_sample", match_score=score, quality=quality, diagnostics=diagnostics)
                return "\u26a0\ufe0f একই বা প্রায় একই selfie আবার পাঠানো হয়েছে। ফোন/মুখের angle সামান্য বদলে নতুন live selfie দিন।"

        # Stop one person's face being registered under another employee account.
        other_rows = c.execute("SELECT employee_id,embedding FROM face_samples WHERE employee_id<>?", (employee["id"],)).fetchall()
        if other_rows:
            duplicate_score = best_match(embedding, [json.loads(r["embedding"]) for r in other_rows])
            if duplicate_score >= 0.62:
                record("rejected", "already_registered_elsewhere", impostor_score=duplicate_score,
                       quality=quality, diagnostics=diagnostics)
                return "🚫 এই মুখটি অন্য employee profile-এ আগে থেকেই নিবন্ধিত। HR/Admin-এর সঙ্গে যোগাযোগ করুন।"

        c.execute("INSERT INTO face_samples(employee_id,media_id,embedding,quality) VALUES(?,?,?,?)", (employee["id"], media_id, json.dumps(embedding), quality))
        count = c.execute("SELECT COUNT(*) c FROM face_samples WHERE employee_id=?", (employee["id"],)).fetchone()["c"]
        existing = c.execute("SELECT id FROM face_profiles WHERE employee_id=?", (employee["id"],)).fetchone()
        if existing:
            c.execute("UPDATE face_profiles SET reference_media_id=?,updated_at=CURRENT_TIMESTAMP WHERE employee_id=?", (media_id, employee["id"]))
        else:
            c.execute("INSERT INTO face_profiles(employee_id,reference_media_id) VALUES(?,?)", (employee["id"], media_id))
    invalidate_gallery_cache()
    record("accepted", "sample_stored", quality=quality, diagnostics=diagnostics)
    if count < target:
        set_state(employee["whatsapp_phone"] or employee["phone"], "awaiting_face_registration")
        next_pose = "any" if settings.simple_face_mode else (ENROLL_POSES[count] if count < len(ENROLL_POSES) else "any")
        guidance = ENROLL_POSE_PROMPT.get(next_pose, "স্বাভাবিকভাবে")
        return ("\u2705 Selfie %d/%d গ্রহণ করা হয়েছে।\n\U0001f50e Face quality: %.1f%%\n"
                "\U0001f4d0 Face area: %.1f%%\n\nএবার %s পরের selfie তুলুন।"
                % (count, target, quality, diagnostics["face_ratio"], guidance))
    clear_state(employee["whatsapp_phone"] or employee["phone"])
    return ("\u2705 Face Registration সম্পন্ন হয়েছে।\n\n\U0001f464 %s\n\U0001f194 %s\n"
            "\U0001f510 %d টি Face AI sample সংরক্ষিত\n\U0001f50e শেষ selfie quality: %.1f%%\n\n"
            "এখন Attendance Menu ব্যবহার করুন।"
            % (employee["name"], employee["staff_id"], count, quality))


def shift_times(shift):
    return (time(16), time(22)) if (shift or "").lower() == "evening" else (time(8), time(16))

def duty_window(employee, duty_date):
    date_text=duty_date.isoformat()
    with get_db() as c:
        duty=c.execute("SELECT start_time,end_time FROM custom_duties WHERE employee_id=? AND duty_date=? AND is_active",(employee['id'],date_text)).fetchone()
        if not duty: duty=c.execute("SELECT start_time,end_time FROM duty_schedules WHERE employee_id=? AND weekday=? AND is_active",(employee['id'],duty_date.weekday())).fetchone()
    if duty:
        start=time.fromisoformat(duty['start_time']); end=time.fromisoformat(duty['end_time'])
    else: start,end=shift_times(employee['shift'])
    start_dt=datetime.combine(duty_date,start,tzinfo=now_local().tzinfo); end_dt=datetime.combine(duty_date,end,tzinfo=now_local().tzinfo)
    if end_dt<=start_dt: end_dt+=timedelta(days=1)
    return start_dt,end_dt


def check_in(employee):
    current = now_local(); work_date = current.date().isoformat(); start_dt,_=duty_window(employee,current.date())
    late = max(0, int((current-start_dt).total_seconds() // 60))
    with get_db() as c:
        record = c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], work_date)).fetchone()
        if record and record["check_in"]: return f"ℹ️ আজ Check In করা হয়েছে: {datetime.fromisoformat(record['check_in']).strftime('%I:%M %p')}"
        if record: c.execute("UPDATE attendance SET check_in=?,late_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current.isoformat(timespec="seconds"), late, record["id"]))
        else: c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)", (employee["id"], work_date, current.isoformat(timespec="seconds"), late))
    return f"✅ Check In সফল\nসময়: {current.strftime('%I:%M %p')}" + (f"\n⏰ Late: {late} মিনিট" if late else "\n🟢 On time")


def check_out(employee):
    current=now_local(); today=current.date(); yesterday=today-timedelta(days=1)
    with get_db() as c:
        today_record=c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?",(employee['id'],today.isoformat())).fetchone()
        previous_record=c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?",(employee['id'],yesterday.isoformat())).fetchone()
    record=today_record if today_record and today_record['check_in'] and not today_record['check_out'] else None
    duty_date=today
    if not record and previous_record and previous_record['check_in'] and not previous_record['check_out']:
        prev_start,prev_end=duty_window(employee,yesterday)
        if prev_end.date()>yesterday and current<=prev_end+timedelta(hours=12): record=previous_record; duty_date=yesterday
    if not record: return "❌ আগে Check In করতে হবে।"
    _,end_dt=duty_window(employee,duty_date)
    early=max(0,int((end_dt-current).total_seconds()//60)); overtime=max(0,int((current-end_dt).total_seconds()//60))
    with get_db() as c:
        if record["check_out"]: return f"ℹ️ আজ Check Out করা হয়েছে: {datetime.fromisoformat(record['check_out']).strftime('%I:%M %p')}"
        worked = max(0, int((current - datetime.fromisoformat(record["check_in"])).total_seconds() // 60))
        c.execute("UPDATE attendance SET check_out=?,early_leave_minutes=?,overtime_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current.isoformat(timespec="seconds"), early, overtime, record["id"]))
    hours, minutes = divmod(worked, 60)
    message = f"✅ Check Out সফল\nসময়: {current.strftime('%I:%M %p')}\nকাজ করেছেন: {hours} ঘণ্টা {minutes} মিনিট"
    if early: message += f"\n⚠️ Early leave: {early} মিনিট"
    if overtime: message += f"\n⏱️ Overtime: {overtime} মিনিট"
    return message


def report(employee):
    with get_db() as c: rows = c.execute("SELECT * FROM attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 7", (employee["id"],)).fetchall()
    if not rows: return "ℹ️ কোনো attendance record নেই।"
    output = [f"📊 {employee['name']}-এর সর্বশেষ Attendance:"]
    for row in rows: output.append(f"{row['work_date']} | In {row['check_in'][11:16] if row['check_in'] else '-'} | Out {row['check_out'][11:16] if row['check_out'] else '-'} | Late {row['late_minutes']}m | OT {row['overtime_minutes']}m")
    return "\n".join(output)


def distance_meters(lat1, lon1, lat2, lon2):
    r = 6371000.0
    p1, p2 = radians(lat1), radians(lat2)
    dp, dl = radians(lat2-lat1), radians(lon2-lon1)
    a = sin(dp/2)**2 + cos(p1)*cos(p2)*sin(dl/2)**2
    return 2*r*asin(sqrt(a))


def verify_location(latitude, longitude):
    if not OFFICE_LATITUDE or not OFFICE_LONGITUDE:
        return True, None
    d = distance_meters(float(OFFICE_LATITUDE), float(OFFICE_LONGITUDE), float(latitude), float(longitude))
    return d <= OFFICE_RADIUS_METERS, round(d, 1)


def begin_attendance_action(phone, action):
    employee = employee_by_phone(phone)
    if not employee: return "❌ আগে Register করুন। শুধু লিখুন: Register"
    if not has_face(employee["id"]):
        set_state(phone, "awaiting_face_registration")
        return "📸 Attendance দেওয়ার আগে Face Registration সম্পন্ন করুন। এখন ৩টি পরিষ্কার selfie পাঠান। প্রথম selfie এখন পাঠান।"
    set_state(phone, f"{action}_location")
    return "__REQUEST_LOCATION__"


def receive_location(phone, latitude, longitude):
    current = state(phone)
    if not current or current["state"] not in {"checkin_location", "checkout_location"}:
        return "ℹ️ এই মুহূর্তে Location প্রয়োজন নেই। Menu থেকে Check In বা Check Out নির্বাচন করুন।"
    ok, distance = verify_location(latitude, longitude)
    if not ok:
        return f"❌ আপনি অনুমোদিত অফিস এলাকার বাইরে আছেন।\nদূরত্ব: {distance:.0f} মিটার\nঅনুমোদিত: {OFFICE_RADIUS_METERS:.0f} মিটার\n\nআবার সঠিক Location পাঠান।"
    action = "checkin" if current["state"].startswith("checkin") else "checkout"
    dist_value = "" if distance is None else str(distance)
    pose, issued_at = new_liveness_challenge()
    set_state(phone, f"{action}_selfie:{latitude}:{longitude}:{dist_value}:{pose}:{issued_at}")
    return "✅ Location গ্রহণ করা হয়েছে।\n\n" + liveness_prompt(pose)


def receive_image(phone, media_id, image_bytes=None):
    employee = employee_by_phone(phone)
    current = state(phone)
    if image_bytes is None:
        return "❌ WhatsApp থেকে ছবিটি download করা যায়নি। আবার selfie পাঠান।"
    if current and current["state"] == "awaiting_face_registration":
        if not employee: return "❌ Registration approval পাওয়া যায়নি।"
        return save_face_reference(employee, media_id, image_bytes)
    if not employee: return "❌ আগে Register করুন।"
    if not current or not current["state"].startswith(("checkin_selfie:", "checkout_selfie:")):
        return "ℹ️ এই মুহূর্তে selfie প্রয়োজন নেই। Menu থেকে Check In বা Check Out নির্বাচন করুন।"
    parts = current["state"].split(":")
    challenge_pose = parts[4] if len(parts) > 4 else "straight"
    issued_at = int(parts[5]) if len(parts) > 5 and parts[5].isdigit() else 0
    age_seconds = int(time_module.time()) - issued_at if issued_at else LIVENESS_CHALLENGE_TTL_SECONDS + 1
    if age_seconds > LIVENESS_CHALLENGE_TTL_SECONDS:
        pose, new_issued_at = new_liveness_challenge()
        parts[4:] = [pose, str(new_issued_at)]
        set_state(phone, ":".join(parts))
        return "⌛ আগের selfie challenge-এর সময় শেষ হয়েছে।\n\n" + liveness_prompt(pose)
    started = time_module.perf_counter()

    def record(decision, reason, score=0.0, impostor=0.0, impostor_id=None, quality_value=0.0, detail=None):
        log_face_event(employee_id=employee["id"], stage="verify", action=parts[0],
                       decision=decision, reason=reason, match_score=score,
                       impostor_score=impostor, impostor_employee_id=impostor_id,
                       quality=quality_value, diagnostics=detail or {},
                       elapsed_ms=(time_module.perf_counter() - started) * 1000)

    try:
        required_pose = None if settings.simple_face_mode else challenge_pose
        candidate, quality, diagnostics = extract_embedding(image_bytes, required_pose=required_pose)
    except FaceAIError as exc:
        record("rejected", str(exc).splitlines()[0][:200])
        return "\u274c Live Face Verification Failed\n%s\n\n\u23f3 Challenge শেষ হওয়ার আগে আবার চেষ্টা করুন।" % exc

    with get_db() as c:
        rows = c.execute("SELECT embedding FROM face_samples WHERE employee_id=? ORDER BY id", (employee["id"],)).fetchall()
    own_samples = [json.loads(r["embedding"]) for r in rows]
    score = gallery_score(candidate, own_samples)
    impostor_score, impostor_id = best_impostor(candidate, load_impostor_gallery(employee["id"]))
    margin = score - impostor_score
    threshold = min(settings.face_match_threshold, 0.44) if settings.simple_face_mode else settings.face_match_threshold

    # Only a confident spoof reading blocks attendance. The passive signals are
    # heuristics; until they have been tuned against logged data, a borderline
    # reading is recorded for review rather than used to turn someone away.
    if not settings.simple_face_mode and diagnostics.get("liveness_verdict") == "spoof":
        record("rejected", "liveness", score, impostor_score, impostor_id, quality, diagnostics)
        return ("\U0001f6ab Live Face Verification Failed\n\n"
                "ছবিটি স্ক্রিন বা ছাপানো ছবি থেকে তোলা বলে মনে হচ্ছে।\n"
                "সরাসরি ক্যামেরার সামনে দাঁড়িয়ে নতুন selfie দিন।")

    quality_min = min(settings.face_quality_min, 30.0) if settings.simple_face_mode else settings.face_quality_min
    if quality < quality_min:
        record("rejected", "quality", score, impostor_score, impostor_id, quality, diagnostics)
        return "\u274c Selfie quality কম (%.0f%%)। ভালো আলোতে ক্যামেরার কাছে এসে নতুন selfie দিন।" % quality

    if score < threshold:
        record("rejected", "below_threshold", score, impostor_score, impostor_id, quality, diagnostics)
        return ("\U0001f6ab Face Verification Failed\n\nনিবন্ধিত মুখের সঙ্গে মিল পাওয়া যায়নি।\n"
                "Match score: %.1f%%\n\nশুধু নিজের বর্তমান selfie পাঠান।" % (score * 100))

    # Beating the threshold is not enough when someone else in the workforce
    # matches almost as well — siblings and lookalikes fail exactly here.
    if not settings.simple_face_mode and impostor_id is not None and margin < settings.face_margin_min:
        record("rejected", "margin", score, impostor_score, impostor_id, quality, diagnostics)
        return ("\U0001f6ab Face Verification Failed\n\nএই মুখ একাধিক employee profile-এর সঙ্গে "
                "প্রায় সমানভাবে মিলছে, তাই নিশ্চিতভাবে শনাক্ত করা যায়নি।\n"
                "ভালো আলোতে সোজা তাকিয়ে আবার চেষ্টা করুন, না হলে HR-এর সঙ্গে যোগাযোগ করুন।")

    record("accepted", "verified" if diagnostics.get("liveness_verdict") == "live" else "verified_liveness_review",
           score, impostor_score, impostor_id, quality, diagnostics)
    adapt_gallery(employee["id"], candidate, quality, score, margin, diagnostics, media_id)

    action = "check_in" if parts[0] == "checkin_selfie" else "check_out"
    try:
        fingerprint = make_fingerprint(image_bytes, candidate, diagnostics)
        limits = DuplicateThresholds(settings.duplicate_accept_below, settings.duplicate_reject_at,
            settings.duplicate_hash_weight, settings.duplicate_face_weight,
            settings.duplicate_pose_weight, settings.duplicate_landmark_weight,
            settings.duplicate_corroboration_gate)
        with get_db() as c:
            # Exact hashes are checked globally to stop one employee reusing another
            # employee's saved image. Near-duplicate scoring is limited to the same
            # employee, otherwise normal faces of two people can create false alerts.
            exact = c.execute("""SELECT id,phash,ahash,dhash,embedding,pose,yaw,landmarks FROM attendance_fingerprints
                WHERE phash=? OR ahash=? OR dhash=? ORDER BY id DESC LIMIT 100""",(fingerprint["phash"],fingerprint["ahash"],fingerprint["dhash"])).fetchall()
            recent = c.execute("""SELECT id,phash,ahash,dhash,embedding,pose,yaw,landmarks FROM attendance_fingerprints
                WHERE employee_id=? ORDER BY id DESC LIMIT 120""", (employee["id"],)).fetchall()
            seen=set(); prior=[]
            for row in [*exact,*recent]:
                if row['id'] not in seen:
                    seen.add(row['id']); prior.append(row)
        duplicate = detect_duplicate(fingerprint, prior, limits)
        # With verified GPS plus a successful identity match, only near-exact image
        # reuse should block attendance in simple mode.
        if settings.simple_face_mode and duplicate.hash_score < 0.985:
            duplicate = type(duplicate)(duplicate.score, "accept", duplicate.matched_fingerprint_id,
                duplicate.hash_score, duplicate.face_score, duplicate.pose_score, duplicate.landmark_score)
        review_status = "pending" if duplicate.decision == "pending" else "none"
        with get_db() as c:
            c.execute("""INSERT INTO attendance_fingerprints(employee_id,action,media_id,phash,ahash,dhash,embedding,pose,yaw,landmarks,duplicate_score,hash_score,face_score,pose_score,landmark_score,matched_fingerprint_id,decision,review_status)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (employee["id"], action, media_id, fingerprint["phash"], fingerprint["ahash"], fingerprint["dhash"], json.dumps(fingerprint["embedding"]), fingerprint["pose"], fingerprint["yaw"], json.dumps(fingerprint["landmarks"]), duplicate.score, duplicate.hash_score, duplicate.face_score, duplicate.pose_score, duplicate.landmark_score, duplicate.matched_fingerprint_id, duplicate.decision, review_status))
        if duplicate.decision == "reject":
            return f"🚫 Duplicate Selfie Rejected\nDuplicate score: {duplicate.score*100:.1f}%\nনতুন live selfie তুলে আবার পাঠান।"
        if duplicate.decision == "pending":
            clear_state(phone)
            return f"⏳ Selfie Admin Review-এ পাঠানো হয়েছে।\nDuplicate score: {duplicate.score*100:.1f}%\nAdmin সিদ্ধান্ত দেওয়ার পর আবার চেষ্টা করুন।"
    except Exception:
        # GPS and identity have already passed. In Simple Face Mode an optional
        # duplicate-analysis/database issue must not block valid attendance.
        logging.getLogger(__name__).exception("duplicate selfie analysis failed")
        if not settings.simple_face_mode:
            return "⚠️ Selfie verification সাময়িকভাবে সম্পন্ন হয়নি। Challenge শেষ হওয়ার আগে আবার চেষ্টা করুন।"
    lat, lon = float(parts[1]), float(parts[2]); dist = float(parts[3]) if len(parts) > 3 and parts[3] else None
    result = check_in(employee) if action == "check_in" else check_out(employee)
    with get_db() as c:
        # Python bool works for PostgreSQL BOOLEAN and is stored as 1 by SQLite.
        # Passing integer 1 made psycopg reject an otherwise valid selfie after
        # attendance had already been recorded.
        c.execute("INSERT INTO attendance_evidence(employee_id,action,latitude,longitude,distance_meters,image_media_id,verified) VALUES(?,?,?,?,?,?,?)", (employee["id"], action, lat, lon, dist, media_id, True))
    clear_state(phone)
    verification = "Simple Face Verified" if settings.simple_face_mode else f"Live Challenge Verified: {POSE_LABELS.get(challenge_pose, challenge_pose)}"
    return result + f"\n✅ Location Verified\n😊 Face Verified: {score*100:.1f}%\n🛡️ {verification}"


def process(phone, text):
    command = " ".join((text or "").strip().lower().split())
    current_state = state(phone)
    if command in {"cancel", "বাতিল"}:
        clear_state(phone); return "বর্তমান প্রক্রিয়া বাতিল হয়েছে। Menu খুলতে লিখুন: Menu"
    if current_state:
        value = current_state["state"]
        if value == "awaiting_staff_id": return begin_registration(text, phone)
        if value.startswith("confirm_registration:"):
            if command in {"yes", "y", "confirm", "হ্যাঁ", "ha"}: return confirm_registration(int(value.split(":",1)[1]), phone)
            if command in {"no", "n", "না"}: clear_state(phone); return "Registration বাতিল হয়েছে। আবার Register লিখুন।"
            return "তথ্য সঠিক হলে YES লিখুন, অথবা বাতিল করতে CANCEL লিখুন।"
        if value == "waiting_for_approval": return "⏳ আপনার registration এখনো Admin approval-এর অপেক্ষায় আছে।"
        if value == "awaiting_face_registration": return "📸 এখন ৩টি পরিষ্কার selfie পাঠান। প্রথম selfie এখন পাঠান।"
        if value.endswith("_location"): return "📍 নিচের Send Location বাটন ব্যবহার করে বর্তমান Location পাঠান।"
        if value.startswith(("checkin_selfie:", "checkout_selfie:")):
            parts = value.split(":")
            pose = parts[4] if len(parts) > 4 else "straight"
            return liveness_prompt(pose)
    if command in {"hi","hello","menu","start"}:
        employee=employee_by_phone(phone); return menu(employee["name"] if employee else None)
    if command in {"register","1"}:
        existing=employee_by_phone(phone)
        if existing:
            if not has_face(existing["id"]): set_state(phone,"awaiting_face_registration"); return "✅ Registration approved। এখন Face Registration-এর জন্য একটি selfie পাঠান।"
            return "✅ এই WhatsApp নম্বর ইতিমধ্যে registered।"
        set_state(phone,"awaiting_staff_id"); return "আপনার Staff ID পাঠান। উদাহরণ: B520202"
    if command in {"help","5"}: return menu()
    if command in {"check in","checkin","check_in","in","2"}: return begin_attendance_action(phone,"checkin")
    if command in {"check out","checkout","check_out","out","3"}: return begin_attendance_action(phone,"checkout")
    employee=employee_by_phone(phone)
    if not employee: return "❌ আগে Register করুন। শুধু লিখুন: Register"
    if command in {"my attendance","my_attendance","attendance","report","4"}: return report(employee)
    if command in {"my duty","my_duty","duty","5"}: return duty_report(employee)
    return "বুঝতে পারিনি। Menu দেখতে লিখুন: Menu"


def log(direction, phone, typ, content, message_id=None):
    try:
        with get_db() as c: c.execute("INSERT INTO whatsapp_logs(direction,phone,message_type,content,message_id) VALUES(?,?,?,?,?)", (direction,normalize_phone(phone),typ,content,message_id))
        return True
    except Exception: return False
