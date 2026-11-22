# Threads, Memory, Sync and Async (high-level revision notes)

## One-line summary
- **Sync:** do one thing, wait for it, then do the next. To serve many requests at once you need more threads, and threads cost memory.
- **Async:** when a task has to wait, it pauses and lets another task run. One thread can serve thousands of waiting requests cheaply, but you must never block that thread.

## The core idea: most of a web request is waiting
Example: `GET /links`
| step | time |
|---|---|
| parse request | ~0.1 ms |
| send query to Postgres and **wait** | ~5 ms |
| build JSON | ~0.1 ms |

The CPU sits idle during the wait. Sync and async are two strategies for what to do with that idle time.

## Key terms
- **Process:** a running program (e.g. the uvicorn server). Has its own memory.
- **Thread:** a worker inside a process that executes code. Threads in one process share memory.
- **Memory per thread:** each thread reserves its own stack, roughly 1 MB or more.
- **Event loop:** the scheduler in an async program. It runs one task until that task hits `await`, then switches to another.
- **Coroutine:** what an `async def` function returns when called. It does nothing until awaited.
- **I/O-bound:** work that mostly waits (database, network, disk). Async helps here.
- **CPU-bound:** work that mostly calculates (image resizing, number crunching). Async does not help.

## Restaurant analogy
| design | picture | result |
|---|---|---|
| Sync, 1 thread | one waiter stands at the kitchen until food is ready | everyone else waits |
| Sync, many threads | hire 40 waiters, each waits at the kitchen | works, but costs memory; the 41st customer queues |
| Async, 1 thread | one waiter hands the order over and moves to the next table, returns when the bell rings | thousands of customers, tiny cost |

## Comparison
| | Sync + threads | Async |
|---|---|---|
| Concurrency by | one thread per request | one thread switching at each `await` |
| Memory per waiting request | ~1 MB+ | a few KB |
| Typical limit | ~40 threads (FastAPI default pool for `def` endpoints) | thousands of tasks |
| Main risk | running out of threads | one blocking call freezes everything |

## In code
```python
rows = db.query("SELECT ...")          # sync: this thread waits, does nothing else
rows = await db.query("SELECT ...")    # async: pause here, let others run
```
- `async def` only marks a function as pausable.
- The pausing happens at `await`. A function with no `await` inside never pauses.

## Important points
- Async is **not faster for a single request**. A 5 ms query takes 5 ms either way. It helps when **many requests overlap**.
- **Never block inside `async def`.** `time.sleep(5)` or a sync DB call freezes every other request, including `/health`. These bugs appear only under load.
- **Plain `def` endpoints in FastAPI** run in a thread pool. That's safe but limited to ~40 at once.
- **Calling an `async def` without `await`** returns an unstarted coroutine ("coroutine was never awaited") and does nothing.
- **CPU-heavy work** needs separate processes or worker threads, not plain async.
- **Pick one style per app.** Mixing sync and async libraries is where the bugs come from.

## When to use what
**Async:** database queries, calling other APIs (many URL checks at once), network I/O.
**Sync/threads:** a library with no async version, quick simple work, less complexity wanted.

## Why LinkRot Watchdog is async
1. The DB driver (`asyncpg`) and SQLAlchemy asyncio are async.
2. The checker must check many URLs at once with `httpx.AsyncClient`.
3. Concurrency is capped with `asyncio.Semaphore` (`MAX_CONCURRENCY=10`) so we don't hit thousands of sites simultaneously.
4. One style across the whole app.

## Interview-ready answers
- **"Why async here?"** The workload is I/O-bound: database calls and outbound HTTP. Async lets one thread overlap many waits with far less memory than a thread per request.
- **"Does async make it faster?"** Not per request. It improves throughput when many requests wait at the same time.
- **"What goes wrong with async?"** Blocking the event loop. One synchronous slow call stalls all requests.
- **"What is a semaphore for?"** It limits how many tasks do something at once (e.g. 10 concurrent URL checks).
- **"When not async?"** CPU-bound work, or when a needed library has no async support.

## Quick self-test
1. What does `await` actually do?
2. Why is `time.sleep(5)` inside an `async def` endpoint harmful?
3. Why does a thread cost more memory than an async task?
4. Would async speed up resizing 1000 images? Why or why not?
