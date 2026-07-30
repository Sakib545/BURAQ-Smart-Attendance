from datetime import datetime,time
from zoneinfo import ZoneInfo
import re
from app.config import settings
from app.database import get_db

def now_local(): return datetime.now(ZoneInfo(settings.timezone))
def normalize_phone(v): return re.sub(r"\D","",v or "")
def state(phone):
    with get_db() as c: return c.execute("SELECT * FROM conversation_states WHERE phone=?",(normalize_phone(phone),)).fetchone()
def set_state(phone,s):
    with get_db() as c: c.execute("INSERT INTO conversation_states(phone,state) VALUES(?,?) ON CONFLICT(phone) DO UPDATE SET state=excluded.state,updated_at=CURRENT_TIMESTAMP",(normalize_phone(phone),s))
def clear_state(phone):
    with get_db() as c: c.execute("DELETE FROM conversation_states WHERE phone=?",(normalize_phone(phone),))
def employee_by_phone(phone):
    with get_db() as c: return c.execute("SELECT * FROM employees WHERE whatsapp_phone=? AND registration_status='approved'",(normalize_phone(phone),)).fetchone()
def menu(name=None): return f"👋 {'স্বাগতম '+name if name else 'Welcome to BURAQ Attendance'}\n\n1️⃣ Register\n2️⃣ Check In\n3️⃣ Check Out\n4️⃣ My Attendance\n5️⃣ Help"
def register(staff_id,phone):
    phone=normalize_phone(phone)
    with get_db() as c:
        e=c.execute("SELECT * FROM employees WHERE staff_id=? COLLATE NOCASE",(staff_id.strip(),)).fetchone()
        if not e: return "❌ Staff ID পাওয়া যায়নি। আবার পাঠান।",False
        if normalize_phone(e['phone'])==phone:
            c.execute("UPDATE employees SET whatsapp_phone=?,registration_status='approved',updated_at=CURRENT_TIMESTAMP WHERE id=?",(phone,e['id']))
            clear_state(phone); return f"✅ Registration সফল\nনাম: {e['name']}\nStaff ID: {e['staff_id']}",True
        c.execute("INSERT INTO pending_registrations(employee_id,whatsapp_phone) VALUES(?,?)",(e['id'],phone))
        c.execute("UPDATE employees SET registration_status='pending' WHERE id=?",(e['id'],))
    clear_state(phone); return "⏳ নম্বর মেলেনি। Admin approval-এর জন্য পাঠানো হয়েছে।",True
def shift_times(shift): return (time(16),time(22)) if (shift or '').lower()=='evening' else (time(8),time(16))
def check_in(e):
    n=now_local(); d=n.date().isoformat(); start,_=shift_times(e['shift']); late=max(0,int((n-datetime.combine(n.date(),start,tzinfo=n.tzinfo)).total_seconds()//60))
    with get_db() as c:
        r=c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?",(e['id'],d)).fetchone()
        if r and r['check_in']: return f"ℹ️ আজ Check In করা হয়েছে: {r['check_in']}"
        if r: c.execute("UPDATE attendance SET check_in=?,late_minutes=? WHERE id=?",(n.isoformat(timespec='seconds'),late,r['id']))
        else: c.execute("INSERT INTO attendance(employee_id,work_date,check_in,late_minutes) VALUES(?,?,?,?)",(e['id'],d,n.isoformat(timespec='seconds'),late))
    return f"✅ Check In সফল\nসময়: {n.strftime('%I:%M %p')}"+(f"\nLate: {late} মিনিট" if late else '')
def check_out(e):
    n=now_local(); d=n.date().isoformat(); _,end=shift_times(e['shift']); early=max(0,int((datetime.combine(n.date(),end,tzinfo=n.tzinfo)-n).total_seconds()//60)); ot=max(0,int((n-datetime.combine(n.date(),time(22),tzinfo=n.tzinfo)).total_seconds()//60))
    with get_db() as c:
        r=c.execute("SELECT * FROM attendance WHERE employee_id=? AND work_date=?",(e['id'],d)).fetchone()
        if not r or not r['check_in']: return "❌ আগে Check In করতে হবে।"
        if r['check_out']: return f"ℹ️ আজ Check Out করা হয়েছে: {r['check_out']}"
        c.execute("UPDATE attendance SET check_out=?,early_leave_minutes=?,overtime_minutes=? WHERE id=?",(n.isoformat(timespec='seconds'),early,ot,r['id']))
    return f"✅ Check Out সফল\nসময়: {n.strftime('%I:%M %p')}"+(f"\nOvertime: {ot} মিনিট" if ot else '')
def report(e):
    with get_db() as c: rows=c.execute("SELECT * FROM attendance WHERE employee_id=? ORDER BY work_date DESC LIMIT 7",(e['id'],)).fetchall()
    if not rows:return "ℹ️ কোনো attendance record নেই।"
    out=[f"📊 {e['name']}-এর Attendance:"]
    for r in rows: out.append(f"{r['work_date']} | In {r['check_in'][11:16] if r['check_in'] else '-'} | Out {r['check_out'][11:16] if r['check_out'] else '-'} | Late {r['late_minutes']}m | OT {r['overtime_minutes']}m")
    return "\n".join(out)
def process(phone,text):
    cmd=' '.join((text or '').strip().lower().split()); s=state(phone)
    if s and s['state']=='awaiting_staff_id':
        msg,done=register(text,phone)
        if not done:set_state(phone,'awaiting_staff_id')
        return msg
    if cmd in {'hi','hello','menu','start'}:
        e=employee_by_phone(phone); return menu(e['name'] if e else None)
    if cmd in {'register','1'}:
        if employee_by_phone(phone): return '✅ এই নম্বর ইতিমধ্যে registered।'
        set_state(phone,'awaiting_staff_id'); return 'আপনার Staff ID পাঠান। উদাহরণ: BRQ001'
    if cmd in {'help','5'}: return menu()
    e=employee_by_phone(phone)
    if not e:return '❌ আগে Register করুন। শুধু লিখুন: Register'
    if cmd in {'check in','checkin','in','2'}:return check_in(e)
    if cmd in {'check out','checkout','out','3'}:return check_out(e)
    if cmd in {'my attendance','attendance','report','4'}:return report(e)
    return 'বুঝতে পারিনি। Menu দেখতে লিখুন: Hi'
def log(direction,phone,typ,content,message_id=None):
    with get_db() as c:c.execute("INSERT INTO whatsapp_logs(direction,phone,message_type,content,message_id) VALUES(?,?,?,?,?)",(direction,normalize_phone(phone),typ,content,message_id))
