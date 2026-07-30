#!/bin/bash
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
if [ ! -f .env ]; then cp .env.example .env; open -e .env; echo "Add Meta credentials to .env, then run again."; exit 0; fi
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
