# TODO: project name

> **First session:** the TODOs below are still open. Ask the owner what is unclear, fill them
> in, delete this note and open a PR. Keep this file under 60 lines. Tools, MCPs and box rules
> are in the global box CLAUDE.md, don't repeat them here.

## Purpose
TODO: what this service does, for whom, and the production URL.

## Stack
Python 3.13, FastAPI, uvicorn, pytest, ruff. The demo has no database. TODO: add what the project
needs (SQLAlchemy 2.0 async + PostgreSQL + Alembic, Celery + Redis, ...) and list it here.

## Commands
- Setup: `python -m venv .venv && . .venv/bin/activate && pip install -r requirements-dev.txt`
- Dev server: `uvicorn app.main:app --reload --host 0.0.0.0 --port 8080`
- Lint: `ruff check .` (format: `ruff format .`)
- Tests: `pytest -q`
- Dependencies: pin exact versions in `requirements.txt` (runtime) and `requirements-dev.txt`
  (everything incl. test tools), regenerate with `pip freeze`.

## Layout
- `app/main.py`: FastAPI app and the contract endpoints. Add routers under `app/` as it grows
  (`app/routers/`, `app/services/`, `app/schemas/`).
- `tests/`: pytest, one file per module, `TestClient` for endpoints.
- `Dockerfile`: stages `base`, `test` (CI runs ruff + pytest), `runtime`.

## Demo content
`app/` and `tests/` are a placeholder. Replace them with the real service and delete this section.

## CI and deploy contract
`.github/workflows/ci.yml` is a thin caller of the shared pipeline in `seitenschwung/template`,
managed by the owner. You can't change workflow files, so build the project to fit it:
- The Dockerfile's `test` stage (`FROM ... AS test`) runs in CI before the image build. Keep
  lint and tests there, and it must not be the last stage.
- The image runs hardened: read-only root filesystem, `/tmp` as the only writable path, no
  capabilities, non-root user. Keep `PYTHONDONTWRITEBYTECODE=1`; write only to `/tmp`.
- It must listen on port **8080**, answer `GET /healthz` with 200 and serve `GET /version.txt`
  with the build argument `APP_VERSION` (the commit SHA).
- If `/` returns HTML, CI also crawls the site (axe WCAG AA, Lighthouse at least 90).
- `docker-compose.yml` keeps the CrowdSec labels (`crowdsec.enable=true`,
  `crowdsec.labels.type=http`) and the json-file log rotation on every service that serves traffic.
  The CrowdSec agent on the server reads the container logs through them. Don't remove them; the
  type must match a parser there, so ask the owner before changing it.
- `docker-compose.yml` is what Coolify runs. Keep `image: ${IMAGE}`. `main` is production: every
  merge deploys after green CI. Work on branches, open PRs.

## Conventions
- Type hints everywhere. Business logic in service functions, routers stay thin.
- Success responses: `{"data": ..., "meta": {...}}`. Errors: `{"error": {"code": ..., "message": ...}}`.
  List endpoints paginate (`limit`, `offset`) and return the total in `meta`.
- Conventional Commits (`feat:`, `fix:`, `chore:`).
- TODO: auth (e.g. JWT), language of messages, anything the owner insists on.
