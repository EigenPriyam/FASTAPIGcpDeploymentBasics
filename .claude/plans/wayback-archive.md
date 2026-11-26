# Technical plan: Wayback archive lookup

Implements `.claude/specs/wayback-archive.md` on branch `feature/wayback-archive`.
The spec was approved with all five recommended decisions.

## Context
The link checker is merged (PR #3) and 89 tests pass. This step finds a saved copy of dead
links and stores it in `links.archive_url`.

## A bug to fix first (from step 3)
The link-checker spec says full URLs are not logged. They are: the `httpx` library logs every
request at INFO, for example:

    logger=httpx msg=HTTP Request: GET https://93.184.216.34/p?token=SECRET123 "HTTP/1.1 200 OK"

Confirmed with a mocked run. This step would also log every link inside the Wayback request.
Fix: in `core/logging.py`, set the `httpx` and `httpcore` loggers to `WARNING`. A test runs a
check and a lookup with a token in the URL and asserts the token never appears in the logs.

## What already exists (reuse)
- `links.archive_url` column and `LinkRead.archive_url`. No schema change.
- `check_runner.py`: read, check, save phases.
- `repositories/checks.py`: `get_target`, `list_targets`, `save`.
- `core/config.py`: `check_timeout_seconds`.
- One-line placeholders to fill: `services/archive.py`, `tests/test_archive.py`.

## Steps (one commit each)

### 0. Log fix and docs
- `core/logging.py` change plus `tests/test_logging.py`.
- Commit separately from the spec (status set to Approved) and this plan.

### 1. Archive client: `services/archive.py` + `tests/test_archive.py`
- `API_URL = "https://archive.org/wayback/available"`, `MAX_LOOKUPS = 3`.
- `async find_snapshot(client, link_id, url, timeout) -> str | None`
  - send `params={"url": <url without #fragment>}`, inside `asyncio.timeout(timeout)`
  - require HTTP 200, then read `archived_snapshots.closest`
  - never raises (except cancellation). On any failure log
    `archive lookup failed id=... error=<exception type>`. Never log the exception text,
    because it can contain the URL.
- `_valid_snapshot(closest) -> str | None`: `available` is true, `status` starts with `2`,
  address is `http(s)`, host is exactly `web.archive.org`, no username. Return the address
  string with `http://` replaced by `https://` (the rest is left untouched).
- `async find_snapshots(targets: Sequence[tuple[int, str]], timeout) -> dict[int, str]`:
  one `AsyncClient` (explicit timeout, no redirects), `Semaphore(MAX_LOOKUPS)`, `gather`.
  The result holds only the links where a copy was found. Opens no client if there are no
  targets.
- The spec's signature gains `link_id` and `timeout`, for logging and testing.

### 2. Repository, runner, schema, API tests
- `repositories/checks.py`
  - `Target(NamedTuple)`: `id`, `url`, `archive_url`. Returned by `get_target`, `list_targets`.
  - `save(..., new_archive_url: str | None = None, clear_archive_url: bool = False)`:
    the `UPDATE` touches `archive_url` only when setting or clearing it, so an older value read
    before the network phase can never overwrite a newer one. Return the check and the stored
    `archive_url` (`RETURNING`).
- `services/check_runner.py`
  - after `check_many`: look up targets that are `dead` and have no `archive_url`
  - save: `alive` clears, a found copy is set, anything else leaves it alone
  - used by both `check_link` and `check_all`
- `schemas/check.py`: `CheckResult.archive_url: str | None`.
- `tests/test_checks_api.py`
  - add a Wayback mock to the existing tests that produce dead links
  - new: copy saved and shown in `GET /links`; saved copy not asked again; cleared when alive;
    blocked never asked (also when it already has a copy); Wayback down still returns 200 and
    saves the check; `/check-all` with several dead links

### 3. Verification (you run Docker)
1. `uv run pytest`
2. I run one real lookup from the command line, without the database.
3. `docker compose up -d db`, then `uv run uvicorn gcpbasicdeployment.main:app --reload`.
4. Spec section 11 in `/docs`. Check that the log shows no link URLs.
5. I push when you say so, and you open the PR.

## Gotchas
- A lookup that is not mocked is swallowed by the "never raises" rule, so API tests must assert
  that the mock was called, and every test with a dead link needs `@respx.mock`.
- `respx` matches the Wayback URL with its query: mock `API_URL` with `params={"url": ...}`.
- The lookup happens outside any database transaction and is not counted in `latency_ms`.
- Uvicorn's access log still shows our own API paths. Those contain link ids, not link URLs.
