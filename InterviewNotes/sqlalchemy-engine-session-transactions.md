# SQLAlchemy: Engine, Sessionmaker, Session, Transactions (revision notes)

## One-line summary
- **Engine:** the app-wide gateway to the database. It owns the connection pool.
- **Sessionmaker (factory):** creates sessions on demand.
- **Session:** one unit of work: add, query, change, then `commit` or `rollback`.
- **Transaction:** all-or-nothing group of changes inside a session.

## Analogy: a phone company and a bank
| concept | analogy | job |
|---|---|---|
| Engine | the phone exchange to the bank | knows the address, manages real connections |
| Sessionmaker | a factory making handsets | builds new sessions |
| Session | one phone call | one conversation with the database |
| Transaction | one request you make during the call | applied fully or not at all |

## Picture
```
DATABASE_URL
     |
     v
  Engine ------------- pool of connections to the DB (one per app)
     |
     v
Sessionmaker --------- factory (one per app)
     |
     v   (one per request)
  Session ------------- add / get / execute / commit / rollback
```

## Engine
```python
engine = create_async_engine("postgresql+asyncpg://user:pw@localhost:5432/linkrot")
```
- The URL format: `dialect+driver://user:password@host:port/database`.
- Holds a **connection pool**. Opening a connection is slow, so connections are kept open and reused.
- **Lazy:** creating the engine does not connect. The first real query does. Importing your app will not fail just because the DB is down.
- Create **one per application**, not one per request (e.g. `@lru_cache` on a getter).
- Cloud SQL via unix socket changes only the URL, not the code.

## Sessionmaker
```python
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)
async with SessionLocal() as session:
    ...
```
- Calling the sessionmaker gives a new session bound to the engine.
- Also one per application.
- `expire_on_commit=False`: by default, after `commit()` all loaded objects are marked stale and reloaded on next access. In async code that implicit reload fails. Turning it off keeps values readable after commit.

## Session
Typical operations:
```python
session.add(obj)                 # stage an INSERT (nothing saved yet)
await session.get(Model, 5)      # fetch by primary key
await session.scalars(select(Model))   # run a query
await session.delete(obj)        # stage a DELETE
await session.commit()           # save everything permanently
await session.rollback()         # undo everything since the last commit
await session.refresh(obj)       # reload an object's columns from the DB
```
- **One request = one session.** Concurrent requests get separate sessions, so they never share state.
- A session can run **many queries**. It is not one query.
- A session does not hold a DB connection for its whole life. It borrows one from the pool when needed and returns it at commit or close.
- Always close it. `async with` guarantees this even on errors.

## Transactions
A transaction is a group of changes that succeed or fail together.
```
BEGIN
  INSERT order
  INSERT payment      <- fails
ROLLBACK              -> the order insert is undone too
```
- `commit()` makes the group permanent.
- `rollback()` discards it.
- After an error (e.g. `IntegrityError`) the session must be rolled back before reuse.
- Rule of thumb: one request does one commit. A session can contain several transactions over its life.

## Session per request in FastAPI
```python
async def get_session() -> AsyncIterator[AsyncSession]:
    async with SessionLocal() as session:
        yield session           # lent to the endpoint
    # closed here after the response
```
```python
SessionDep = Annotated[AsyncSession, Depends(get_session)]

@router.get("/links")
async def list_links(session: SessionDep): ...
```
- `Depends(get_session)` tells FastAPI to build the session before the endpoint runs.
- Code after `yield` runs after the response, which is where cleanup happens.
- Endpoints do not create sessions, so tests can swap in another one via `dependency_overrides`.

## Creating tables
```python
async with engine.begin() as conn:
    await conn.run_sync(Base.metadata.create_all)
```
- `Base.metadata` is the registry of all model classes.
- `create_all` issues `CREATE TABLE IF NOT EXISTS`. It never alters an existing table.
- `run_sync` is needed because `create_all` is a synchronous function.
- Production alternative: **Alembic migrations**, which track and apply schema changes.

## Handling constraint errors
```python
try:
    await session.commit()
except IntegrityError:
    await session.rollback()
    raise DuplicateError(...)
```
The database enforces rules (`UNIQUE`, foreign keys) and the code translates the failure into a proper API error.

## Common mistakes
- Forgetting `await` on a session call: nothing runs, a coroutine is returned.
- Creating an engine per request: exhausts connections.
- Not rolling back after a failed commit: the session stays unusable.
- Sharing one session across concurrent tasks: sessions are not safe to share.
- Using a sync driver inside `async def`: blocks the event loop.
- Relying on server defaults (e.g. `created_at`) without `refresh()`: attribute is not loaded after insert.

## Interview-ready answers
- **Engine vs session?** The engine is the long-lived connection manager (one per app). A session is a short-lived unit of work (one per request).
- **Is a session a connection?** No. It borrows pooled connections as needed.
- **Why a connection pool?** Opening connections is expensive. Reusing them saves time and limits load on the DB.
- **Why one session per request?** Isolation between requests and a clear point to commit, roll back and clean up.
- **What does a transaction guarantee?** All-or-nothing changes (atomicity).
- **Why `expire_on_commit=False` in async?** Expired attributes trigger implicit lazy loads, which are not allowed outside an awaited call.
- **`create_all` vs Alembic?** `create_all` only creates missing tables. Alembic versions and applies schema changes safely.

## Quick self-test
1. How many engines should an app have? How many sessions?
2. What happens to uncommitted changes if you call `rollback()`?
3. Why is `await session.commit()` the line where a duplicate error shows up, not `session.add()`?
4. What does `yield` do inside a FastAPI dependency?
5. Why must the engine be lazy?
