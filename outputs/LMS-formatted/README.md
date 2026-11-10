# Shelfwise — Library Management System

## Chosen stack

- **Backend: FastAPI** — validated REST endpoints and generated OpenAPI docs.
- **Database: SQLite + SQLAlchemy 2** — zero-configuration local setup, with portable ORM models for PostgreSQL later.
- **Frontend: Jinja2 + Bootstrap 5** — quick, responsive desk workflows without a separate frontend build.
- **Authentication: JWT + Argon2** — expiring signed tokens, HttpOnly browser cookie, and role checks on protected APIs.
- **Migrations and tests: Alembic + pytest** — repeatable schema setup and focused service-layer checks.

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

Open <http://127.0.0.1:8000>. Interactive API docs are at `/docs`. Run `pytest` from the project root.

## Project layout and formatting

- `app/main.py` contains HTTP routes and request/response handling.
- `app/models.py` defines the database tables and relationships.
- `app/services.py` contains circulation and fine rules.
- `app/security.py` handles password hashing, JWTs, and role checks.
- `app/templates/` contains the server-rendered pages; `app/static/` contains styles.
- `migrations/`, `scripts/`, and `tests/` hold schema changes, setup/seed tools, and tests.

Python files use Ruff's standard formatter with an 88-character line width. Run `ruff format app migrations scripts tests` after edits. `.editorconfig` keeps indentation consistent in editors that support it.

### Demo accounts

All accounts use Argon2-hashed passwords. Change them before using the system beyond a local demo.

| Role | Email | Password |
|---|---|---|
| Admin | admin@library.local | AdminPass123! |
| Librarian | librarian@library.local | Librarian123! |
| Student/member | student@library.local | StudentPass123! |

## Configuration

Copy `.env.example` to `.env` (the setup script does this) and set:

| Variable | Purpose |
|---|---|
| `SECRET_KEY` | JWT signing key; replace the example before use |
| `DATABASE_URL` | SQLAlchemy URL, default `sqlite:///./library.db` |
| `UPLOAD_DIR` | Private softcopy storage directory |
| `ACCESS_TOKEN_MINUTES` | JWT/browser session lifetime |
| `LOAN_DAYS` | Standard loan period |
| `FINE_RATE_PER_DAY`, `FINE_CAP` | Overdue fine policy |
| `MAX_UPLOAD_MB` | Softcopy upload ceiling |
| `COOKIE_SECURE` | Set `true` when serving the app over HTTPS; keep `false` for local HTTP development |

## Architecture and integrity

The database separates a **title** (catalogue record) from its individual **copies**. Availability is derived from copy rows; each copy carries its own status, barcode, optional QR/RFID tags, replacement value, and shelf location. Historical circulation belongs to the copy, so a catalogue title can be archived without erasing loans or incidents. The ER map is `User 1—0..1 Member; Title 1—many Copy; Shelf 1—many Copy; Copy 1—many Loan (at most one active); Member 1—many Loan/Reservation; Loan 1—many Fine; Copy 1—many LossDamageLog; Title 1—many Softcopy; Member 1—many Notification`.

Issue and return use one SQLAlchemy transaction. The service checks membership, live borrow count, copy status, and the reservation queue before changing the copy and loan together. A partial unique database index on active loans per copy backs up the service check. Fine calculation is a pure `calculate_fine(due_date, returned_on, rate, cap)` function: non-negative overdue days times the daily rate, rounded to cents and capped. Return records overdue and replacement fines, and creates incident history for loss/damage. Titles with active loans cannot be archived.

JWT roles are enforced on APIs and web workflows. Browser session cookies are HttpOnly and SameSite=Strict; state-changing requests check same-origin request provenance. Softcopies are stored under opaque generated names outside the static web directory; download routes check role and borrowing entitlement before serving. Files are size-limited, extension-checked, and checked for basic PDF/EPUB signatures. Notifications are in-app; admins trigger due/overdue generation at `POST /api/notifications/run` (schedule this endpoint from a trusted job runner if desired). Members can request payment confirmation; only staff can record receipt and mark a fine paid.

## Main workflows

- Dashboard: at-a-glance title/copy/member/issued/overdue totals and recent transactions.
- Books: catalogue lookup, add title/copy, barcode and shelf display, outdated review flag.
- Students: create members with borrowing limits.
- Issue/return: scan the barcode, select the member, return with condition, renew active loans.
- Fines: review and mark offline payments as paid.
- Reports: most-borrowed titles, overdue loans, review flags, and loss/damage history.
- Member login: view current loans and unpaid fines; reserve via API; members can renew eligible loans via API.

## API outline

`POST /api/auth/token`, `GET /api/dashboard`, `GET/POST/PATCH/DELETE /api/titles`, `POST /api/copies`, `GET /api/copies/{barcode}/location`, `POST /api/members`, circulation issue/return/renew endpoints, `POST /api/reservations`, `/api/me`, fine payment, softcopy upload/download, and notification generation. Request and response details are in `/docs`.

## Small deviations and follow-ups

- No email provider is configured; due and reservation alerts are persisted as in-app notifications.
- Fine payment is recorded as paid/waived status only; no payment gateway is in scope.
- The demo uses a single library and local disk. Multi-branch support and external object storage remain outside v1.
- Member creation is implemented; membership suspension and full profile editing are represented in the schema but the initial desk UI keeps the CRUD focused on registration.
- Schema creation runs through Alembic for setup; `create_all` is also retained for convenient test/application startup on a fresh local database.
