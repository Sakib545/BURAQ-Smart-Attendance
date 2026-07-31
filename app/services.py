from datetime import datetime, time
from zoneinfo import ZoneInfo
from math import asin, cos, radians, sin, sqrt
import re
import json
import random
import time as time_module

from app.face_ai import FaceAIError, extract_embedding, best_match
from app.duplicate_detector import DuplicateThresholds, detect_duplicate, make_fingerprint
from app.config import settings, OFFICE_LATITUDE, OFFICE_LONGITUDE, OFFICE_RADIUS_METERS
from app.database import get_db




LIVENESS_CHALLENGE_TTL_SECONDS = 120
POSE_LABELS = {
    "straight": "সোজা সামনে তাকান",
    "left": "মাথা সামান্য বাম দিকে ঘুরিয়ে তাকান",
    "right": "মাথা সামান্য ডান দিকে ঘুরিয়ে তাকান",
}

def new_liveness_challenge():
    pose = random.choice(tuple(POSE_LABELS))
    issued_at = int(time_module.time())
    return pose, issued_at

def liveness_prompt(pose):
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
    set_state(phone, f"confirm_registration:{employee['id']}")
    return registration_preview(employee)


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


def save_face_reference(employee, media_id, image_bytes):
    try:
        embedding, quality, diagnostics = extract_embedding(image_bytes)
    except FaceAIError as exc:
        return f"❌ Face Registration হয়নি।\n{exc}"
    with get_db() as c:
        rows = c.execute("SELECT embedding FROM face_samples WHERE employee_id=?", (employee["id"],)).fetchall()
        if rows:
            score = best_match(embedding, [json.loads(r["embedding"]) for r in rows])
            if score < 0.42:
                return "❌ আগের selfie-এর সঙ্গে এই মুখ মিলছে না। একই employee নিজের selfie দিন।"
            if score > 0.985:
                return "⚠️ একই বা প্রায় একই selfie আবার পাঠানো হয়েছে। ফোন/মুখের angle সামান্য বদলে নতুন live selfie দিন।"

        # Stop one person's face being registered under another employee account.
        other_rows = c.execute("SELECT employee_id,embedding FROM face_samples WHERE employee_id<>?", (employee["id"],)).fetchall()
        if other_rows:
            duplicate_score = best_match(embedding, [json.loads(r["embedding"]) for r in other_rows])
            if duplicate_score >= 0.62:
                return "🚫 এই মুখটি অন্য employee profile-এ আগে থেকেই নিবন্ধিত। HR/Admin-এর সঙ্গে যোগাযোগ করুন।"

        c.execute("INSERT INTO face_samples(employee_id,media_id,embedding,quality) VALUES(?,?,?,?)", (employee["id"], media_id, json.dumps(embedding), quality))
        count = c.execute("SELECT COUNT(*) c FROM face_samples WHERE employee_id=?", (employee["id"],)).fetchone()["c"]
        existing = c.execute("SELECT id FROM face_profiles WHERE employee_id=?", (employee["id"],)).fetchone()
        if existing:
            c.execute("UPDATE face_profiles SET reference_media_id=?,updated_at=CURRENT_TIMESTAMP WHERE employee_id=?", (media_id, employee["id"]))
        else:
            c.execute("INSERT INTO face_profiles(employee_id,reference_media_id) VALUES(?,?)", (employee["id"], media_id))
    if count < 3:
        set_state(employee["whatsapp_phone"] or employee["phone"], "awaiting_face_registration")
        return f"✅ Selfie {count}/3 গ্রহণ করা হয়েছে।\n🔎 Face quality: {quality:.1f}%\n📐 Face area: {diagnostics['face_ratio']:.1f}%\n\nআরও {3-count}টি আলাদা selfie পাঠান—একবার সোজা, একবার সামান্য বাম, একবার সামান্য ডান দিকে তাকিয়ে।"
    clear_state(employee["whatsapp_phone"] or employee["phone"])
    return f"✅ Face Registration সম্পন্ন হয়েছে।\n\n👤 {employee['name']}\n🆔 {employee['staff_id']}\n🔐 ৩টি Face AI sample সংরক্ষিত\n🔎 শেষ selfie quality: {quality:.1f}%\n\nএখন Attendance Menu ব্যবহার করুন।"


def shift_times(shift):
    return (time(16), time(22)) if (shift or "").lower() == "evening" else (time(8), time(16))


def check_in(employee):
    current = now_local(); work_date = current.date().isoformat(); start, _ = shift_times(employee["shift"])
    late = max(0, int((current - datetime.combine(current.date(), start, tzinfo=current.tzinfo)).total_seconds() // 60))
    with get_db() as c:
        record = c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], work_date)).fetchone()
        if record and record["check_in"]: return f"ℹ️ আজ Check In করা হয়েছে: {datetime.fromisoformat(record['check_in']).strftime('%I:%M %p')}"
        if record: c.execute("UPDATE attendance SET check_in=?,late_minutes=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (current.isoformat(timespec="seconds"), late, record["id"]))
        else: c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)", (employee["id"], work_date, current.isoformat(timespec="seconds"), late))
    return f"✅ Check In সফল\nসময়: {current.strftime('%I:%M %p')}" + (f"\n⏰ Late: {late} মিনিট" if late else "\n🟢 On time")


def check_out(employee):
    current = now_local(); work_date = current.date().isoformat(); _, end_time = shift_times(employee["shift"])
    early = max(0, int((datetime.combine(current.date(), end_time, tzinfo=current.tzinfo) - current).total_seconds() // 60))
    overtime = max(0, int((current - datetime.combine(current.date(), time(22), tzinfo=current.tzinfo)).total_seconds() // 60))
    with get_db() as c:
        record = c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?", (employee["id"], work_date)).fetchone()
        if not record or not record["check_in"]: return "❌ আগে Check In করতে হবে।"
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
    try:
        candidate, quality, diagnostics = extract_embedding(image_bytes, required_pose=challenge_pose)
    except FaceAIError as exc:
        return f"❌ Live Face Verification Failed\n{exc}\n\n⏳ Challenge শেষ হওয়ার আগে আবার চেষ্টা করুন।"
    with get_db() as c:
        rows = c.execute("SELECT embedding FROM face_samples WHERE employee_id=? ORDER BY id", (employee["id"],)).fetchall()
    score = best_match(candidate, [json.loads(r["embedding"]) for r in rows])
    threshold = 0.46
    if score < threshold:
        return f"🚫 Face Verification Failed\n\nনিবন্ধিত মুখের সঙ্গে মিল পাওয়া যায়নি।\nMatch score: {score*100:.1f}%\n\nশুধু নিজের বর্তমান selfie পাঠান।"
    action = "check_in" if parts[0] == "checkin_selfie" else "check_out"
    fingerprint = make_fingerprint(image_bytes, candidate, diagnostics)
    limits = DuplicateThresholds(settings.duplicate_accept_below, settings.duplicate_reject_at,
        settings.duplicate_hash_weight, settings.duplicate_face_weight,
        settings.duplicate_pose_weight, settings.duplicate_landmark_weight)
    with get_db() as c:
        prior = c.execute("SELECT * FROM attendance_fingerprints ORDER BY id DESC LIMIT 1000").fetchall()
    duplicate = detect_duplicate(fingerprint, prior, limits)
    review_status = "pending" if duplicate.decision == "pending" else "none"
    with get_db() as c:
        c.execute("""INSERT INTO attendance_fingerprints(employee_id,action,media_id,phash,ahash,dhash,embedding,pose,yaw,landmarks,duplicate_score,hash_score,face_score,pose_score,landmark_score,matched_fingerprint_id,decision,review_status)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (employee["id"], action, media_id, fingerprint["phash"], fingerprint["ahash"], fingerprint["dhash"], json.dumps(fingerprint["embedding"]), fingerprint["pose"], fingerprint["yaw"], json.dumps(fingerprint["landmarks"]), duplicate.score, duplicate.hash_score, duplicate.face_score, duplicate.pose_score, duplicate.landmark_score, duplicate.matched_fingerprint_id, duplicate.decision, review_status))
    if duplicate.decision == "reject":
        return f"🚫 Duplicate Selfie Rejected\nDuplicate score: {duplicate.score*100:.1f}%\nনতুন live selfie তুলে আবার পাঠান।"
    if duplicate.decision == "pending":
        clear_state(phone)
        return f"⏳ Selfie Admin Review-এ পাঠানো হয়েছে।\nDuplicate score: {duplicate.score*100:.1f}%\nAdmin সিদ্ধান্ত দেওয়ার পর আবার চেষ্টা করুন।"
    lat, lon = float(parts[1]), float(parts[2]); dist = float(parts[3]) if len(parts) > 3 and parts[3] else None
    result = check_in(employee) if action == "check_in" else check_out(employee)
    with get_db() as c:
        c.execute("INSERT INTO attendance_evidence(employee_id,action,latitude,longitude,distance_meters,image_media_id,verified) VALUES(?,?,?,?,?,?,?)", (employee["id"], action, lat, lon, dist, media_id, 1))
    clear_state(phone)
    return result + f"\n✅ Location Verified\n😊 Face Verified: {score*100:.1f}%\n🛡️ Live Challenge Verified: {POSE_LABELS.get(challenge_pose, challenge_pose)}"


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
    return "বুঝতে পারিনি। Menu দেখতে লিখুন: Menu"


def log(direction, phone, typ, content, message_id=None):
    try:
        with get_db() as c: c.execute("INSERT INTO whatsapp_logs(direction,phone,message_type,content,message_id) VALUES(?,?,?,?,?)", (direction,normalize_phone(phone),typ,content,message_id))
        return True
    except Exception: return False
