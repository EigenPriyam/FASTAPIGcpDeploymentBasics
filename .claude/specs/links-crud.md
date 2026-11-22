# Spec: Postgres layer + Links CRUD

Status: DRAFT, awaiting review. Branch: `feature/links-crud`. Roadmap step 2.

## 1. Goal
Persist links in PostgreSQL and expose `POST/GET/DELETE /links` with validation and the
consistent JSON error shape. Local Postgres runs via Docker Compose. The same code runs
on Cloud SQL later, with only `DATABASE_URL` changing.

## 2. Scope
In:
- `docker-compose.yml` with a Postgres service only (the app container comes in roadmap step 6).
- Async SQLAlchemy engine, session dependency, ORM models for `links` and `checks`.
- Links CRUD: create, list, get, delete.
- Tests for all of it.

Out (later roadmap steps):
- Checker, SSRF guard, `POST /links/{id}/check`, `/check-all`.
- Wayback lookup, app Dockerfile, GCP deploy.
- `GET /links/{id}/checks` (needs the checks repository and the checker).

The `checks` table is created now, so the schema is complete and delete-cascade is testable.
No checks endpoints yet.

## 3. Data model
`links`
| column | type | notes |
|---|---|---|
| id | integer PK, autoincrement | |
| url | text, NOT NULL, UNIQUE | stored normalised (see 5) |
| label | text, NULL | optional, max 200 chars |
| created_at | timestamptz, NOT NULL | server default `now()` |
| last_status | text, NULL | e.g. "alive"/"dead"; unset until a check runs |
| archive_url | text, NULL | filled by the Wayback step |

`checks`
| column | type | notes |
|---|---|---|
| id | integer PK | |
| link_id | integer FK -> links.id, `ON DELETE CASCADE`, indexed | |
| status_code | integer, NULL | NULL when the request failed |
| latency_ms | integer, NULL | |
| error | text, NULL | error type, e.g. "timeout", "dns", "ssl" |
| checked_at | timestamptz, NOT NULL, default `now()` | |

## 4. API
| method & path | success | errors |
|---|---|---|
| `POST /links` body `{url, label?}` | 201 + LinkRead | 422 invalid body/URL, 409 duplicate URL |
| `GET /links` | 200 + list[LinkRead], newest first | none |
| `GET /links/{id}` | 200 + LinkRead | 404, 422 if id is not an int |
| `DELETE /links/{id}` | 204, no body | 404 |

Error shape (already implemented): `{"error": {"code", "message", "details"}}`.
`LinkRead`: `id, url, label, created_at, last_status, archive_url`.

Pagination is deliberately omitted (YAGNI for a capstone). It's noted as a known limitation.

## 5. Validation rules
- `url` must be a valid `http` or `https` URL (Pydantic `AnyHttpUrl`), max 2048 chars.
- Stored as given by Pydantic's string form. Pydantic normalises things like an empty path to `/`.
  So `http://a.com` and `http://a.com/` collide as duplicates. Beyond that there is no custom normalisation
  (no stripping of trailing slashes, query params or case on the path).
- `label`: optional, max 200 chars.
- SSRF checking is NOT done at registration. It is enforced by the checker at request time (step 3),
  as CLAUDE.md specifies. Registering `http://localhost` is accepted but never fetched.

## 6. Design

Layering (per CLAUDE.md): router -> repository -> DB. No SQL in routers.

Files:
- `docker-compose.yml`: service `db` (`postgres:16`), named volume `pgdata`, `pg_isready`
  healthcheck, port 5432 published to localhost, credentials from `.env` via `${VAR}`.
- `db/session.py`: `create_async_engine(settings.database_url)`, `async_sessionmaker`,
  `get_session()` dependency (one session per request, closed afterwards).
- `db/models.py` (new): `Base`, `Link`, `Check` ORM models.
- `schemas/link.py`: `LinkCreate`, `LinkRead` (`from_attributes=True`).
- `repositories/links.py`: `create`, `list_all`, `get`, `delete`.
  - `create` commits and catches `IntegrityError` on the unique url, then raises `DuplicateError` (409).
  - `get`/`delete` raise `NotFoundError` (404) on a miss.
- `routers/links.py`: thin endpoints that depend on `get_session`.
- `main.py`: include the links router. Create tables at startup via a lifespan hook (see 7).
- `.env.example`: add `POSTGRES_USER/PASSWORD/DB` for compose.

Logging: `logger.info` on create/delete with the link id. No `print`. Never log the DB password.

## 7. Decisions to confirm (my recommendation first)
1. **Schema creation:** `Base.metadata.create_all` at startup (simple) vs Alembic migrations.
   Recommend `create_all` now. It's enough for a capstone, and Alembic is mentioned as the production upgrade.
   Caveat: `create_all` never alters existing tables, so schema changes later need a manual reset
   (`docker compose down -v`).
2. **Test database:** SQLite in memory via `aiosqlite` (fast, no Docker needed, overrides `get_session`)
   vs real Postgres test DB. Recommend aiosqlite.
   Caveat: it can't catch Postgres-specific behaviour. The unique constraint and cascade behave the same,
   but SQLite needs `PRAGMA foreign_keys=ON` for the cascade test. Postgres-only issues are covered
   by the manual local run (section 9).
3. **Primary key type:** integer id (matches CLAUDE.md `/links/{id}` examples) vs UUID. Recommend integer.
4. **`last_status` type:** free text vs Postgres enum. Recommend text for now. The checker step will define the values.

## 8. Tests (`tests/test_links_api.py`, no real network)
- create returns 201 and the body has an id
- create with a duplicate URL returns 409 with error code `duplicate`
- create with an invalid URL returns 422
- create with a non-http scheme (`ftp://x`) returns 422
- create with a too-long label returns 422
- list returns newest first, and an empty list when there are none
- get existing returns 200
- get missing returns 404 with error code `not_found`
- get with a non-integer id returns 422
- delete returns 204 and a following GET returns 404
- delete missing returns 404
- delete cascades to the link's checks (insert a check row directly, then delete the link)

Needs: `uv add --dev aiosqlite` (dev dependency; `uv add` only, per CLAUDE.md).
`requirements.txt` sync: CLAUDE.md says to keep it in sync, but the file doesn't exist in the repo.
Open question: create it, or amend CLAUDE.md?

## 9. Manual verification (definition of done)
1. `docker compose up -d db`, then `docker compose ps` shows healthy.
2. `uv run uvicorn gcpbasicdeployment.main:app --reload` starts and creates the tables
   (check with `\dt` in psql).
3. Via `/docs`: create, then duplicate (409), then list, get, delete, then get (404).
4. `uv run pytest` is green.

## 10. Risks / notes
- A DB connection failure at startup would crash the app. Acceptable for now, and the compose healthcheck covers local use.
  A DB-aware `/health` is a possible later addition.
- Unhandled DB errors fall through to the existing 500 handler (generic message, no stack trace to the client).
- Cloud SQL: Cloud Run uses the unix-socket URL from `.env.example`. No code change is needed.
  Cloud Run also needs the Cloud SQL connection attached at deploy time (step 7).

## 11. Implementation order (small commits)
1. compose + `.env.example`
2. models + session + lifespan table creation
3. schemas + repository + router + wiring
4. tests (`aiosqlite` dev dep)
5. manual verification, then PR
