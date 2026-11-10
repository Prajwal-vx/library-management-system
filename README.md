# Shelfwise Library Management System

A server-rendered library desk built with FastAPI, Jinja2, SQLAlchemy, and SQLite. The application uses a modular route structure, service-layer circulation rules, and environment-backed settings.

## Run locally

Windows PowerShell:

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\setup.ps1
.\.venv\Scripts\uvicorn.exe app.main:app --reload
```

macOS/Linux:

```sh
sh scripts/setup.sh
. .venv/bin/activate
uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000>. Interactive API documentation is available at `/docs` and `/redoc`.

## Project layout

- `app/main.py` creates the application, middleware, and shared error handlers.
- `app/routes/web.py` serves browser pages and form actions.
- `app/routes/api.py` exposes versionable JSON endpoints under `/api`.
- `app/routes/common.py` contains shared page authentication and template setup.
- `app/schemas/api.py` validates API request bodies.
- `app/services.py` owns circulation and fine rules independently of HTTP and templates.
- `app/models.py`, `app/database.py`, and `app/config.py` define persistence, sessions, and environment settings.
- `app/templates/` contains Jinja pages; `app/static/` contains the stylesheet, menu behavior, and favicon.
- `migrations/`, `scripts/`, and `tests/` hold schema changes, setup helpers, and service checks.

The `/api` prefix is kept as the stable API boundary. New endpoint groups can be added as separate `APIRouter` modules and included in `app/main.py`. Business state is persisted through SQLAlchemy transactions; browser authentication uses signed, revocable `HttpOnly; Secure; SameSite=Lax` cookies. Serve the app over HTTPS so browsers send the session cookie. The token endpoint sets the cookie and does not return the token in its JSON body. API clients may authenticate with bearer tokens. Circulation policy belongs in `app/services.py`, so UI handlers remain adapters rather than a second source of policy.

## Configuration

Copy `.env.example` to `.env`; setup scripts do this automatically. Keep secrets and environment-specific values out of source control.

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | Signing key for access tokens; set a unique random value |
| `DATABASE_URL` | SQLAlchemy database URL |
| `UPLOAD_DIR` | Private softcopy storage location |
| `ACCESS_TOKEN_MINUTES` | Session lifetime |
| `LOAN_DAYS` | Standard loan period |
| `FINE_RATE_PER_DAY`, `FINE_CAP` | Fine policy |
| `MAX_UPLOAD_MB` | Upload size limit |

Never commit `.env`, database files, or uploaded books.

## Initial accounts

The seed script creates no accounts with default passwords. Set unique `SEED_ADMIN_PASSWORD`, `SEED_LIBRARIAN_PASSWORD`, and `SEED_MEMBER_PASSWORD` values before seeding accounts. Leave them unset to seed catalogue data without changing account credentials.

## Workflows and API

The desk supports catalogue search, copy management, membership, issue/return/renew, fines, reports, and member accounts. Core JSON resources include `/api/auth/token`, `/api/dashboard`, `/api/titles`, `/api/copies`, `/api/members`, `/api/circulation`, `/api/reservations`, `/api/me`, `/api/fines`, `/api/softcopies`, and `/api/notifications`.

Request and response schemas are documented by FastAPI in `/docs`. SQLite is the local default; SQLAlchemy allows a PostgreSQL URL to be supplied for a later multi-user deployment.
