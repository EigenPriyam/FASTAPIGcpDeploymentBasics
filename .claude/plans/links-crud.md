# Technical plan: Postgres layer + Links CRUD

Implements `.claude/specs/links-crud.md` on branch `feature/links-crud`.

## Context
Skeleton (config, logging, error handlers, `/health`) is merged to `main`. This step adds
persistence and `POST/GET/DELETE /links`. Spec decisions are taken at my recommended
defaults (the user hasn't overridden them): `create_all` at startup, aiosqlite for tests,
integer PK, `last_status` as text, and a new `requirements.txt` holding direct packages only.

## What already exists (reuse, don't rewrite)
- `core/config.py`: `get_settings()` (lru_cached) gives `database_url`.
- `core/exceptions.py`: `NotFoundError` (404), `DuplicateError` (409), `register_exception_handlers`.
  The 422 shape is also handled there.
- `main.py`: `create_app()`, and `app = create_app()` at import.
- `tests/test_health.py` uses `TestClient(app)` without a context manager. Lifespan does NOT run there,
  so adding table creation to lifespan won't break it or need a DB.
- pytest config already has `asyncio_mode = "auto"`.
- Stub files to fill: `db/session.py`, `repositories/links.py`, `routers/links.py`, `schemas/link.py`,
  `tests/conftest.py`, `tests/test_links_api.py`. New files: `db/models.py`, `docker-compose.yml`.

## Steps (one commit each)

### 1. Compose + env
- `docker-compose.yml`: service `db`, image `postgres:16`, `restart: unless-stopped`.
  - Environment: `POSTGRES_USER/PASSWORD/DB: ${POSTGRES_USER}` etc. (compose reads `.env` automatically).
  - Ports: `5432:5432`. Volume: `pgdata:/var/lib/postgresql/data`.
  - Healthcheck: `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}`, interval 5s, retries 10.
- `.env.example`: add `POSTGRES_USER=user`, `POSTGRES_PASSWORD=password`, `POSTGRES_DB=linkrot`,
  matching the existing `DATABASE_URL`.
- Manual follow-up for the user: add the same three vars to their local git-ignored `.env`.
  I won't edit `.env` without asking.

### 2. Models, session, lifespan
- `db/models.py`: SQLAlchemy 2.0 typed style (`DeclarativeBase`, `Mapped`, `mapped_column`).
  - `Link`: `id` PK, `url` `Text` unique not null, `label` `String(200)` nullable,
    `created_at` `DateTime(timezone=True)` with `server_default=func.now()`, `last_status` `Text` nullable,
    `archive_url` `Text` nullable.
  - `Check`: `id`, `link_id` `ForeignKey("links.id", ondelete="CASCADE")` with `index=True`,
    `status_code`, `latency_ms`, `error`, `checked_at` (server default now).
  - No ORM `relationship` for now. DB-level cascade is enough and the checker step can add it.
- `db/session.py`:
  - `get_engine()` (lru_cached): `create_async_engine(get_settings().database_url)`. This is lazy, so
    importing it never connects.
  - `get_sessionmaker()`: `async_sessionmaker(engine, expire_on_commit=False)`.
  - `async def get_session() -> AsyncIterator[AsyncSession]`: the FastAPI dependency.
  - `async def create_tables()`: `engine.begin()` then `run_sync(Base.metadata.create_all)`.
- `main.py`: add an `@asynccontextmanager` lifespan that calls `create_tables()` and logs, and pass
  `lifespan=` to `FastAPI(...)`. Also `include_router(links.router)`.

### 3. Schemas, repository, router
- `schemas/link.py`:
  - `LinkCreate`: `url: Annotated[AnyHttpUrl, UrlConstraints(max_length=2048)]`,
    `label: str | None = Field(default=None, max_length=200)`.
    Pydantic restricts schemes to http(s) and normalises e.g. a bare host to `http://a.com/`.
  - `LinkRead`: `id, url: str, label, created_at, last_status, archive_url`, with `from_attributes=True`.
- `repositories/links.py` (the only SQL):
  - `create(session, url: str, label)`: `session.add`, `commit` inside `try`. On `IntegrityError`, `rollback`
    and raise `DuplicateError("A link with this URL already exists")`. Then `refresh` to load
    `created_at`, and log `link created id=...`.
  - `list_all(session)`: `select(Link).order_by(Link.created_at.desc(), Link.id.desc())`.
  - `get(session, link_id)`: `session.get`, else raise `NotFoundError`.
  - `delete(session, link_id)`: reuse `get`, then `session.delete` and `commit`, and log.
- `routers/links.py` (`APIRouter(prefix="/links", tags=["links"])`):
  - `POST ""` with 201 and `response_model=LinkRead`; passes `str(payload.url)`.
  - `GET ""` returns `list[LinkRead]`.
  - `GET "/{link_id}"` with `link_id: int`, so a non-int path value gives 422 automatically.
  - `DELETE "/{link_id}"` with 204 and `response_class=Response`.
  - Inject `session: Annotated[AsyncSession, Depends(get_session)]`. The router never imports SQL.

### 4. Tests
- `uv add --dev aiosqlite`.
- `tests/conftest.py`:
  - Engine fixture: `create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)`, so a single in-memory
    connection is shared. Register an event listener on `engine.sync_engine` for `connect` that runs
    `PRAGMA foreign_keys=ON`. Then `create_all`.
  - `client` fixture: override `get_session` via `app.dependency_overrides`, yield
    `httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://test")`, then clear the overrides.
    ASGITransport doesn't run lifespan, so the real Postgres is never touched.
  - A `session` fixture for direct row insertion in the cascade test.
- `tests/test_links_api.py`: the 12 cases from spec section 8. Dedupe check: the duplicate test posts
  the same URL twice. The cascade test creates a link via the API, inserts a `Check` row via the
  session, deletes via the API, then asserts the checks count is 0.
- Keep `test_health.py` unchanged. It must still pass.

### 5. Housekeeping
- Create `requirements.txt` with direct runtime packages only (CLAUDE.md rule), versions pinned from
  `pyproject.toml`. Done by hand, since `uv export` would include transitive dependencies.
- Fix the CLAUDE.md Commands line: `uvicorn gcpbasicdeployment.main:app --reload`.
  Mark the package name as no longer TBD.

## Risks and gotchas
- `created_at` is a server default, so `refresh` after commit is required. Otherwise the response model
  fails to read it.
- SQLite drops timezone info, so tests must not assert tz-aware datetimes.
- Without the `PRAGMA`, SQLite silently ignores `ON DELETE CASCADE`, and the cascade test would fail
  for the wrong reason.
- `create_all` never alters existing tables. If the schema changes later, run `docker compose down -v`.
- Unique violation is mapped to 409 in the repository. Other integrity errors would be mislabelled, but
  `links` has only that one constraint.

## Verification
1. `uv run pytest`: all new tests plus the two health tests pass.
2. `docker compose up -d db`, then `docker compose ps` shows `healthy`.
3. `uv run uvicorn gcpbasicdeployment.main:app --reload` starts. Then
   `docker compose exec db psql -U user linkrot -c "\dt"` lists `links` and `checks`.
4. In `/docs` or curl: POST (201), repeat POST (409), invalid URL (422), GET list, GET by id, GET missing
   (404), DELETE (204), DELETE again (404). Every error body matches the `{"error": {...}}` shape.
5. `git diff` review before committing. PR into `main`, with no direct commits to `main`.
