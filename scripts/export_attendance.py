from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from datetime import datetime
import csv
from app.database import get_db,init_db
init_db();Path('exports').mkdir(exist_ok=True);out=Path('exports')/f"attendance_{datetime.now():%Y%m%d_%H%M%S}.csv"
with get_db() as c:rows=c.execute('SELECT e.staff_id,e.name,e.department,e.shift,a.* FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY work_date').fetchall()
headers=['staff_id','name','department','shift','work_date','check_in','check_out','late_minutes','early_leave_minutes','overtime_minutes','status']
with out.open('w',encoding='utf-8-sig',newline='') as f:
 w=csv.DictWriter(f,fieldnames=headers);w.writeheader();[w.writerow({k:r[k] for k in headers}) for r in rows]
print(out)
