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

## Frontend access (CORS and auth cookies)

The storefront in `../sukilo-app` calls this API from the browser with credentials. Set:

- `CORS_ORIGINS` to a JSON list of the exact storefront origins, for example `["http://localhost:3000","http://127.0.0.1:3000"]`. Never use `"*"`: the API sends `Access-Control-Allow-Credentials: true`.
- `FRONTEND_URL` to the main storefront origin. Token refresh and logout reject requests whose `Origin` is not in `FRONTEND_URL` or `CORS_ORIGINS`.

Login sets an HttpOnly refresh cookie (`sukilo_refresh`) and a CSRF cookie (`sukilo_csrf`), both with `Path=/api/v1/auth`. `POST /api/v1/auth/login` and `POST /api/v1/auth/token/refresh` also return the CSRF value as `csrf_token` in the JSON body, because a storefront on another port or host cannot read the API's cookies. The client sends it back as `X-CSRF-Token` on refresh and logout. The refresh token itself is never returned in the body.

`AUTH_COOKIE_SAMESITE=lax` works when the storefront and API share a site (for example `localhost:3000` and `localhost:8000`, or two subdomains of one domain). If they are on different sites, use `none` with `AUTH_COOKIE_SECURE=true` and HTTPS.

Development authentication starts without a real SMTP server or a secret manager. Production (`APP_ENV=production`) refuses to start until the JWT key, digest pepper, Redis, and `SMTP_PROVIDER_REFERENCE` in `.env.example` are filled in.

Set `SMTP_HOST` and `SMTP_FROM` to deliver OTP and security mail. In development, leaving those empty writes each message to `data/outbox` as an `.eml` file. The code is only inside that file. Logs record that a message was saved and never include the code or the address. SMS is not sent. `GET /api/v1/access/me` returns the signed-in account's active role codes; customers receive an empty list.

## Current gaps

- SMS is not delivered. Email is sent only when `SMTP_HOST` and `SMTP_FROM` are set; otherwise development saves the message under `data/outbox`.
- Data exports stay `PENDING`: nothing processes them yet. `POST /me/data-exports/{id}/download` returns an opaque reference, and there is no endpoint that serves the file.
- There is no durable audit writer and no CLI for `bootstrap_super_admin`, so the first administrator can only be created from code.
- `POST /api/v1/vectors/index`, `/vectors/search`, `/vectors/smoke` and `POST /api/v1/products` have no authentication.

## Customer preferences contract

`PUT /api/v1/me/preferences` creates the preferences on the first call: omit `expected_version` while `GET /me/preferences` still returns 404. After that, send the current `version`; a stale one returns 409 `CUSTOMER_VERSION_CONFLICT`.

## Checks

```powershell
uv run pytest -q
uv run alembic heads
```
