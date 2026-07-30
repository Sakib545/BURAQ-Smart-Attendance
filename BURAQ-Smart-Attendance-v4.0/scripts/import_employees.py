import csv,sys
from app.database import get_db,init_db
from app.services import normalize_phone
if len(sys.argv)<2:raise SystemExit('Usage: python scripts/import_employees.py employees.csv')
init_db();count=0
with open(sys.argv[1],encoding='utf-8-sig',newline='') as f,get_db() as c:
 for r in csv.DictReader(f):
  if not r.get('staff_id') or not r.get('name'):continue
  shift=(r.get('shift') or 'morning').lower(); shift=shift if shift in {'morning','evening'} else 'morning'
  c.execute("INSERT INTO employees(staff_id,name,phone,department,shift) VALUES(?,?,?,?,?) ON CONFLICT(staff_id) DO UPDATE SET name=excluded.name,phone=excluded.phone,department=excluded.department,shift=excluded.shift",(r['staff_id'].strip(),r['name'].strip(),normalize_phone(r.get('phone','')),r.get('department','').strip(),shift));count+=1
print(f'Imported/updated {count} employees')
