from fastapi import FastAPI,Header,HTTPException,Query,Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from app.config import settings
from app.database import get_db,init_db
from app.whatsapp import handle,send_text
app=FastAPI(title=settings.app_name,version='4.0.0')
class TestMessage(BaseModel): phone:str; message:str='BURAQ Attendance test ✅'
def admin(k):
    if not settings.admin_api_key or k!=settings.admin_api_key: raise HTTPException(401,'Invalid admin API key')
@app.on_event('startup')
def startup():init_db()
@app.get('/')
def root():return {'name':settings.app_name,'version':'4.0.0','status':'running','docs':'/docs'}
@app.get('/health')
def health():return {'ok':True}
@app.get('/webhook/whatsapp',response_class=PlainTextResponse)
def verify(hub_mode:str|None=Query(None,alias='hub.mode'),hub_verify_token:str|None=Query(None,alias='hub.verify_token'),hub_challenge:str|None=Query(None,alias='hub.challenge')):
    if hub_mode=='subscribe' and hub_verify_token==settings.whatsapp_verify_token:return hub_challenge or ''
    raise HTTPException(403,'Webhook verification failed')
@app.post('/webhook/whatsapp')
async def webhook(request:Request):await handle(await request.json());return {'status':'ok'}
@app.post('/api/admin/test-message')
async def test_message(body:TestMessage,x_admin_key:str|None=Header(None,alias='X-Admin-Key')):admin(x_admin_key);return await send_text(body.phone,body.message)
@app.get('/api/admin/summary')
def summary(x_admin_key:str|None=Header(None,alias='X-Admin-Key')):
    admin(x_admin_key)
    with get_db() as c:
      return {'employees':c.execute('SELECT COUNT(*) c FROM employees').fetchone()['c'],'registered':c.execute("SELECT COUNT(*) c FROM employees WHERE registration_status='approved'").fetchone()['c'],'pending':c.execute("SELECT COUNT(*) c FROM pending_registrations WHERE status='pending'").fetchone()['c'],'attendance':c.execute('SELECT COUNT(*) c FROM attendance').fetchone()['c']}
@app.get('/api/admin/employees')
def employees(x_admin_key:str|None=Header(None,alias='X-Admin-Key')):
    admin(x_admin_key)
    with get_db() as c:return [dict(r) for r in c.execute('SELECT * FROM employees ORDER BY staff_id').fetchall()]
@app.get('/api/admin/attendance')
def attendance(x_admin_key:str|None=Header(None,alias='X-Admin-Key')):
    admin(x_admin_key)
    with get_db() as c:return [dict(r) for r in c.execute('SELECT a.*,e.staff_id,e.name FROM attendance a JOIN employees e ON e.id=a.employee_id ORDER BY work_date DESC').fetchall()]
