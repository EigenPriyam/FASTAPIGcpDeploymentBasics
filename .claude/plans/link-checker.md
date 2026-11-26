# Technical plan: link checker, checks history and SSRF guard

Implements `.claude/specs/link-checker.md` on branch `feature/link-checker`.

## Context
Links CRUD is merged to `main` and 15 tests pass. This step visits the links and records the
results. The spec was approved with all six recommended decisions.

## What already exists (reuse, don't rewrite)
- `db/models.py`: `Link` and `Check` already have every column needed. No schema change.
- `core/config.py`: `check_timeout_seconds` (10.0) and `max_concurrency` (10).
- `core/exceptions.py`: `NotFoundError`, and the 422 and 500 handlers.
- `repositories/links.py`: `get` and `list_all`.
- `routers/links.py`: `SessionDep` pattern to follow.
- `tests/conftest.py`: SQLite `engine`, `session` and `client` fixtures.
- One-line placeholder files to fill: `services/url_safety.py`, `services/checker.py`,
  `repositories/checks.py`, `schemas/check.py`, `routers/checks.py`, `tests/test_url_safety.py`,
  `tests/test_checker.py`.

## One change from the spec
The spec had the router coordinating the three phases (read, check, save). CLAUDE.md says
routers handle HTTP only, so that logic goes in a new `services/check_runner.py`:

    routers/checks.py -> services/check_runner.py -> services/checker.py  (network)
                                                  -> repositories/checks.py (database)

Behaviour is the same. The spec's file table gets this one extra row.

## How it was checked
A mocked probe with httpx 0.28.1 showed: a request to an IP URL keeps our `Host` header and
`sni_hostname`; `copy_with(host=ip)` handles IPv6 brackets and ports; a cookie jar with
`allowed_domains=[]` keeps nothing; `httpx.URL` accepts port 99999, so the guard must reject it.
A real TLS connection is only exercised in manual verification.

## Steps (one commit each)

### 0. Docs
Commit the spec (status set to Approved, extra file row added) and this plan.

### 1. SSRF guard: `services/url_safety.py` + `tests/test_url_safety.py`
- `class BlockedURLError(Exception)` and `class DNSError(Exception)`.
- `async def _lookup(host: str, port: int) -> list[str]`: `loop.getaddrinfo(..., type=SOCK_STREAM)`.
  This is the only function that touches DNS, so tests replace just this.
- `def is_public(address: str) -> bool`:
  - unwrap an embedded IPv4 first: `ipv4_mapped`, `sixtofour`, and the `64:ff9b::/96` prefix
  - return `ip.is_global and not ip.is_multicast`
  - (Python reports `224.0.0.1` and `64:ff9b::7f00:1` as global, hence both extra steps)
- `async def resolve_safe(url: httpx.URL) -> list[str]`:
  1. scheme in `{"http", "https"}`
  2. host not empty, no `userinfo`, port (if given) between 1 and 65535
  3. `_lookup(url.raw_host.decode("ascii"), port)`; `OSError`, `UnicodeError` or no results
     raise `DNSError`
  4. any address not public raises `BlockedURLError`
  5. return the addresses without duplicates, IPv4 first
- Test fixture in `conftest.py`, `fake_dns` (autouse): a dict of name -> addresses. IP literals
  return themselves, unknown names raise `socket.gaierror`. No test can do real DNS.
- Tests: one per rule 1-9, plus public allowed, mixed addresses refused, unresolvable name,
  bad port, IPv4-first ordering.

### 2. Checker: `services/checker.py` + `tests/test_checker.py`
- `@dataclass(frozen=True) class Outcome`: `status_code`, `latency_ms`, `error`, `status`
  (`"alive"`, `"dead"` or `"blocked"`).
- Constants: `MAX_REDIRECTS = 5`, `ALIVE_EXTRA = {401, 403, 429}`,
  `USER_AGENT = "LinkRotWatchdog/0.1"`.
- `def build_client(timeout: float) -> httpx.AsyncClient`: explicit timeout,
  `follow_redirects=False`, `trust_env=False`, `max_keepalive_connections=0` (so a connection
  opened for one host name is never reused for another on the same IP), cookie jar that keeps
  nothing, our `User-Agent`.
- `async def _send(client, url, addresses) -> httpx.Response`: for each address build a GET to
  `url.copy_with(host=address)` with `Host: url.netloc` and
  `extensions={"sni_hostname": <ascii host>}`, send with `stream=True`. On `httpx.ConnectError`
  try the next address; raise the last error. SSL errors are not retried.
- `async def _follow(client, url: str) -> int`: loop of guard -> send -> close. On
  301/302/303/307/308 with a `Location`, set `current = current.join(location)` (join against the
  **original** host name URL, not the IP one) and repeat. More than 5 redirects raises
  `TooManyRedirects`. Otherwise return the status code.
- `async def check_url(client, url, timeout) -> Outcome`: wraps `_follow` in
  `asyncio.timeout(timeout)` and maps exceptions. It never raises (except cancellation).

  | exception | `error` |
  |---|---|
  | `BlockedURLError` | `blocked_url` |
  | `DNSError` | `dns` |
  | `TimeoutError`, `httpx.TimeoutException` | `timeout` |
  | `TooManyRedirects` | `too_many_redirects` |
  | `httpx.RequestError` with `ssl.SSLError` in its cause chain | `ssl` |
  | other `httpx.RequestError` | `connection` |
  | anything else (`logger.exception`) | `unknown` |

  An unparseable `Location` (`httpx.InvalidURL`) is `blocked_url`.
- `async def check_many(urls, timeout, max_concurrency) -> list[Outcome]`: one client, one
  `Semaphore`, `asyncio.gather`. The timeout starts after the semaphore slot is taken.
  Results come back in the same order as `urls`.
- Tests: as listed in spec section 10. The concurrency test uses a slow async `respx` side
  effect that records the highest number running at once. The TLS test raises a
  `ConnectError` whose cause is `ssl.SSLCertVerificationError`.

### 3. Schemas and repository
- `schemas/check.py`: `CheckRead` (`from_attributes=True`), `CheckResult`, `CheckAllResult`.
- `repositories/checks.py` (the only new SQL):
  - `get_target(session, link_id) -> tuple[int, str]`: 404 if missing, then `commit` to end
    the read transaction.
  - `list_targets(session) -> list[tuple[int, str]]`: ordered by id, then `commit`.
  - `save(session, link_id, outcome) -> Check`: `UPDATE links SET last_status`; if no row
    matched, `rollback` and raise `NotFoundError`. Otherwise insert the check, `commit`,
    `refresh`.
  - `list_for_link(session, link_id)`: 404 if the link is missing; order by
    `checked_at desc, id desc`.
- Covered by the API tests in step 4.

### 4. Runner, router, wiring: `tests/test_checks_api.py`
- `services/check_runner.py`:
  - `check_link(session, link_id) -> CheckResult`
  - `check_all(session) -> CheckAllResult`: read targets, `check_many`, save one by one, skip
    links deleted meanwhile, count outcomes.
  - Log `link checked id=... status=... error=... latency_ms=...`. No URLs.
- `routers/checks.py` (no prefix): the three endpoints from spec section 8, thin.
- `main.py`: `include_router(checks.router)`.
- Tests: success, 404, 422, history order, `last_status` visible in `GET /links`, mixed
  `/check-all`, empty `/check-all`, link deleted mid-check is skipped.

### 5. Verification (you run the Docker commands)
1. `uv run pytest`
2. `docker compose up -d db`, then `docker compose ps` shows `healthy`.
3. `uv run uvicorn gcpbasicdeployment.main:app --reload`
4. In `http://127.0.0.1:8000/docs`, follow spec section 13. Use a real `https` page so TLS with
   a pinned IP is proven, and one that redirects (`http://github.com`).
5. I review the full diff, push the branch if you say so, and you open the PR on GitHub.

## Gotchas
- A database session is not safe to share between concurrent tasks: no database calls inside
  `gather`.
- `respx` mocks must use the IP URL (`https://93.184.216.34/...`), because that is what is sent.
- `asyncio.timeout` turns cancellation into `TimeoutError`. Do not catch `BaseException`.
- IPv4-first plus address fallback avoids false `dead` results on machines without IPv6. If the
  first address hangs instead of refusing, the whole check times out.
- SQLite timestamps have one-second precision, so history order tests rely on the `id` tiebreak.
- `.env` is read by the app at run time. I will not open or edit it.
