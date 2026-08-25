@echo off
cd /d %~dp0
if not exist .venv py -m venv .venv
call .venv\Scripts\activate
python -m pip install -r requirements.txt
if not exist .env (copy .env.example .env & notepad .env & exit /b)
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
