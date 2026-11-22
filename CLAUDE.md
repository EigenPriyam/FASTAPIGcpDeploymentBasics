# LinkRot Watchdog

## What this project is
Links in READMEs, docs and bookmarks die silently. LinkRot Watchdog is a REST API where you
register URLs. It checks them, records whether each is alive or dead, and for dead ones finds an
archived copy on the Wayback Machine.

Capstone (M0.8) of the Level 0 roadmap: a **no-AI** REST service. AI tools are allowed to build it,
but the product itself has no LLM dependency.

## How it works (plain language)
1. A user adds a link: `POST /links`.
2. A check is triggered: `POST /links/{id}/check`, or `POST /check-all` for every link.
3. The service requests each URL (async, many at once, concurrency capped) and stores the result
   (status code, latency, error type, time) as a row in `checks`.
4. If the link is dead, it asks the Wayback Machine for the closest archived snapshot and saves that URL.
5. The user reads the results: `GET /links`, `GET /links/{id}/checks`.

## Architecture
```
Client (curl / Swagger /docs)
        |
   FastAPI app  (routers -> services -> repositories)
     |-- routers/        HTTP only: parse, validate (Pydantic), return responses
     |-- services/       business logic: checker (httpx async), archive client, URL safety
     |-- repositories/   all SQL / DB access
     |-- schemas/        Pydantic request & response models
     |-- core/           config (env vars), logging, exception handlers
        |                     |
   PostgreSQL            External HTTP
   links, checks         - target URLs (the links being checked)
                         - Wayback Machine availability API
```
Packaged with Docker (`Dockerfile` + `docker-compose.yml` for app + Postgres locally).

## Deployment (Google Cloud Platform)
Planned setup (confirm before changing):
- **Cloud Run** runs the container and gives the public URL. It listens on the `PORT` env var, so the app must read `PORT`.
- **Cloud SQL for PostgreSQL** is the production database. Cloud Run connects to it through the Cloud SQL connection (unix socket), not a public IP.
- **Artifact Registry** stores the Docker image.
- **Secret Manager** holds DB credentials in production. Cloud Run injects them as env vars.
- Local uses docker-compose Postgres; prod uses Cloud SQL. The only difference should be env vars (`DATABASE_URL`).
- Deploy flow: build image -> push to Artifact Registry -> `gcloud run deploy`.
- Watch cost: use the smallest Cloud SQL tier, set Cloud Run max instances low, and delete resources after the interview demo.

Data model: `links` (id, url unique, label, created_at, last_status, archive_url) has a one-to-many
relationship with `checks` (id, link_id, status_code, latency_ms, error, checked_at).

## Stack
Python 3.12, uv, FastAPI, Pydantic, PostgreSQL, httpx (async), pytest + respx, Docker, Git, GCP (Cloud Run, Cloud SQL, Artifact Registry, Secret Manager).

## Commands
- Install / rebuild env: `uv sync`
- Add a library: `uv add <pkg>` (never `pip install`)
- Run API locally: `uv run uvicorn gcpbasicdeployment.main:app --reload`
- Tests: `uv run pytest`
- Run with DB: `docker compose up --build`
- Deploy: `gcloud run deploy <service> --image <artifact-registry-image> --region <region>` (project, region and service names TBD)

## Rules
- **Libraries:** only via `uv add`, so they land in `pyproject.toml` and `uv.lock`. Keep `requirements.txt` in sync with direct packages only.
- **Layering:** routers never touch the DB directly; SQL lives in repositories only.
- **Logging:** use the `logging` module with structured messages. No `print`.
- **Config and secrets:** read from environment variables. Never hardcode credentials. `.env` is git-ignored; commit `.env.example`.
- **Errors:** every failure path returns a consistent JSON error shape. Never leak stack traces to clients.
  Cover: validation (422), not found (404), duplicate URL (409), target timeout / DNS / SSL error (recorded as a failed check, not a 500), Wayback API down (degrade gracefully), unexpected errors (500 with generic message).
- **SSRF protection (critical):** the checker must refuse URLs that resolve to private, loopback or link-local addresses (e.g. localhost, 10.x, 169.254.x) and non-http(s) schemes. Each rule has a test.
- **Concurrency:** use `httpx.AsyncClient` with an `asyncio.Semaphore`. Set an explicit timeout on every outbound call.
- **GCP:** never run `gcloud` commands that create, change or delete cloud resources without asking first (they cost money and are outward-facing). Never commit service-account keys; use Secret Manager.
- **Typing:** type hints everywhere.
- **Tests:** mock all outbound HTTP with `respx`. No real network in tests.

## Git and process
- Branch -> PR -> merge. Never commit to `main` directly once the remote exists.
- Never commit `.env`, `.venv`, or secrets.
- For any non-trivial feature: write the spec, review the plan, then implement. Review every diff before accepting it.
- Keep a short `docs/ai-failure.md`: one case where the AI was wrong (what it proposed, why it was wrong, how it was caught, the lesson). Required for the capstone.

## Roadmap (build in this order)
1. Skeleton: package layout, `/health`, config, logging.
2. Postgres + `links` CRUD with validation and error handling.
3. Checker service (async) + `checks` history + SSRF guard.
4. Wayback archive lookup for dead links.
5. Tests across all of the above.
6. Dockerfile + compose, README.
7. Deploy to GCP (Cloud Run + Cloud SQL), verify via `/docs` and curl.
