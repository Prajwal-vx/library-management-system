#!/usr/bin/env sh
set -eu
python -m venv .venv
. .venv/bin/activate
python -m pip install -e '.[dev]'
[ -f .env ] || cp .env.example .env
python -c 'from pathlib import Path; import secrets; p=Path(".env"); s=p.read_text(); s=s.replace("replace-this-with-a-long-random-secret", secrets.token_urlsafe(48)); p.write_text(s)'
alembic upgrade head
python scripts/seed.py
echo 'Ready. Run: .venv/bin/uvicorn app.main:app --reload'
