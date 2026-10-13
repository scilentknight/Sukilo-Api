# Sukilo API

FastAPI backend for Sukilo. Python 3.12 and [uv](https://docs.astral.sh/uv/) are required. Do not use pip.

## Run locally

From this directory, with PostgreSQL already running and `DATABASE_URL` set in `.env`:

```powershell
uv sync
uv run alembic upgrade head
uv run uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Open [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health). Interactive docs are at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs). API routes are under `/api/v1`.

Copy `.env.example` to `.env` only when that file does not exist yet. Keep secrets in `.env`. The app reads `DATABASE_URL` from there, including a URL of the form `postgresql+psycopg://USER:PASSWORD@localhost:5432/DATABASE`.

## What must be running

- PostgreSQL 18, reachable at the host in `DATABASE_URL`. `alembic upgrade head` creates the product tables and the `iam` schema. The Phase 5 migration also creates the `sukilo_migrator`, `sukilo_runtime`, `sukilo_iam_jobs`, and `sukilo_readonly_ops` roles, so the migration user needs permission to create roles.
- Redis, only when `REDIS_URL` is set. Login and OTP rate limits use it. Leave `REDIS_URL` empty in development to use the in-memory limiter instead.
- Qdrant, only when `ENABLE_VECTOR_STACK=true`. Image search loads DINOv2 and fails startup if Qdrant is down. Set `ENABLE_VECTOR_STACK=false` to run identity, access, and customer APIs without the vector stack.

Development authentication starts without a real SMTP server or a secret manager. Production (`APP_ENV=production`) refuses to start until the JWT key, digest pepper, Redis, and SMTP settings in `.env.example` are filled in.

## Checks

```powershell
uv run pytest -q
uv run alembic heads
```
