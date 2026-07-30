import httpx
from app.config import settings
from app.services import log,process
async def send_text(to,text):
    log('outgoing',to,'text',text)
    if not settings.whatsapp_access_token or not settings.whatsapp_phone_number_id:return {'sent':False,'reason':'credentials missing'}
    url=f"https://graph.facebook.com/{settings.meta_api_version}/{settings.whatsapp_phone_number_id}/messages"
    headers={'Authorization':f'Bearer {settings.whatsapp_access_token}','Content-Type':'application/json'}
    payload={'messaging_product':'whatsapp','to':to,'type':'text','text':{'preview_url':False,'body':text}}
    async with httpx.AsyncClient(timeout=30) as client:
        r=await client.post(url,headers=headers,json=payload); r.raise_for_status(); return r.json()
async def handle(payload):
    for entry in payload.get('entry',[]):
      for change in entry.get('changes',[]):
       for m in change.get('value',{}).get('messages',[]):
        phone=m.get('from',''); typ=m.get('type','unknown'); mid=m.get('id')
        if typ=='text':
            text=m.get('text',{}).get('body',''); log('incoming',phone,'text',text,mid); await send_text(phone,process(phone,text))
        elif typ=='location': await send_text(phone,'📍 Location পেয়েছি। GPS verification পরবর্তী version-এ সক্রিয় হবে।')
        elif typ=='image': await send_text(phone,'📸 ছবি পেয়েছি। Face AI verification পরবর্তী version-এ সক্রিয় হবে।')
        else: await send_text(phone,'এই message type এখনো supported নয়।')
