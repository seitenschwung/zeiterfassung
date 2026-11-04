# Zeiterfassung

## Purpose
Single-user work-time tracker (Stempeluhr, manual edits, Soll/Ist, overtime balance). German UI.
No login: reachable only inside the tailnet. No public deploy, nothing configured for Coolify.

## Stack
Python 3.13, FastAPI, uvicorn, SQLite (stdlib `sqlite3`), pytest, ruff. Plain HTML/CSS/JS in
`app/static`, no build step, no CDNs (CSP is `default-src 'self'`). `tzdata` is pinned because the
slim image may lack system zoneinfo.

## Commands
- Setup: `python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
- Dev: `ZEIT_DB_PATH=/tmp/zeit-dev.db uvicorn app.main:app --reload --host 0.0.0.0 --port 8080`
- Lint: `ruff check .` / `ruff format .`. Tests: `pytest -q`
- Dependencies: pin exact versions in `requirements.txt` and `requirements-dev.txt`.

## Layout
- `app/calc.py`: pure maths (Settings, SH holidays, DST-safe day splitting, `build_days`). Test first here.
- `app/services.py`: all rules and DB access; raises `AppError(status, code, German message)`.
- `app/api.py`: thin routes under `/api/v1`; `app/main.py`: app, error envelope, CSP, `/healthz`, `/version.txt`.
- `app/db.py`: schema, `write_tx` (BEGIN IMMEDIATE); `app/schemas.py`: request models.
- `tests/`: `conftest.py` gives `client` (temp DB) and `clock` (fake now, Berlin time).

## Rules / decisions
- Times are stored as epoch seconds (UTC); `end_ts IS NULL` = running (one at most, unique index).
  Input without offset = Europe/Berlin; spring-gap times rejected, ambiguous autumn hour = first occurrence,
  explicit offset (`+01:00`) picks the second. UI sends the original offset for unchanged fields.
- Entries over midnight are split per local day (DST days are 23/25 h). Overlap -> 409, end<=start -> 422,
  future start/end -> 422. Breaks = gaps (self-stamped), nothing is deducted.
- Soll = weekly_hours / number of workdays, 0 on weekends and Schleswig-Holstein holidays (own code, no lib).
- Urlaub/Krank (whole or half day, workdays only) credit that share of the day's Soll. Vacation quota per year.
- Balance = start balance + sum of diffs from `start_date` (default: first entry) through today; future days
  show Soll but do not count.
- Settings (weekly hours, workdays, vacation days, start date/balance) live in the `settings` table.
- Success: `{"data","meta"}`; errors: `{"error":{"code","message"}}`; `/entries` paginates (`limit`, `offset`, `total`).

## Deploy contract
Port 8080, `GET /healthz`, `GET /version.txt` (`APP_VERSION`), non-root, read-only root FS. DB path via
`ZEIT_DB_PATH` (image default `/data/zeit.db`, mount a volume at `/data`). `docker-compose.yml` is untouched
and has no volume yet: add one before any real deploy. Keep the CrowdSec labels in it.
Conventional Commits; branches + PRs only.
