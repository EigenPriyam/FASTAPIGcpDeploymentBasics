# Spec: Link checker, checks history and SSRF guard

Status: APPROVED (all recommended decisions). Branch: `feature/link-checker`. Roadmap step 3.

## 1. Goal
Check registered links over HTTP, save every result in `checks`, update `links.last_status`,
and never let the checker be used to reach private or internal addresses.

## 2. Scope
In:
- `services/url_safety.py`: the SSRF guard, also applied to every redirect.
- `services/checker.py`: async checker with a concurrency cap and timeouts.
- `repositories/checks.py`: save and list check results.
- `POST /links/{id}/check`, `POST /check-all`, `GET /links/{id}/checks`.
- Tests for all of it.

Out: Wayback lookup (`archive_url` stays empty), app Dockerfile, GCP.

No database schema change. The `checks` table already exists, so no volume reset is needed.
No new libraries.

## 3. Words used here
- **async / await:** while one request waits for a slow website, the program works on others.
- **Semaphore:** a counter of free slots. With 10 slots, the 11th check waits for one to free up.
- **SSRF** (server-side request forgery): someone registers a URL such as
  `http://169.254.169.254/` so that *our server* fetches internal data on their behalf.
- **DNS resolution:** turning a host name (`example.com`) into IP addresses.
- **Redirect:** a response saying "go to this other URL instead" (301, 302, 303, 307, 308).

## 4. How one check works
1. Start a timer.
2. **Guard:** validate the URL and resolve its host (section 5). If refused, stop.
3. Send a `GET` and read only the status line and headers, never the body.
4. If it is a redirect, build the next URL and go back to step 2. At most 5 redirects.
5. Classify the result (section 6) and return it. The checker itself never touches the database.

## 5. SSRF rules (`services/url_safety.py`)
A URL is refused when:

| # | rule | example |
|---|---|---|
| 1 | scheme is not `http` or `https` | `ftp://x`, `file:///etc/passwd` |
| 2 | there is no host | `http:///path` |
| 3 | it contains a username or password | `http://user:pw@example.com` |
| 4 | loopback | `localhost`, `127.0.0.1`, `::1` |
| 5 | private network | `10.x`, `172.16-31.x`, `192.168.x`, `fd00::/8` |
| 6 | link-local (includes the cloud metadata server) | `169.254.169.254`, `fe80::1` |
| 7 | any other non-public range | `0.0.0.0`, `100.64.x` |
| 8 | multicast | `224.0.0.1`, `ff02::1` |
| 9 | a private IPv4 address wrapped in IPv6 | `::ffff:127.0.0.1`, `64:ff9b::7f00:1` |

- The rules apply to the **resolved IP addresses**, not the text of the URL. A public-looking
  name that points to `10.0.0.5` is refused.
- If a name resolves to several addresses and **any one** is not public, the URL is refused.
- **Redirects:** httpx's automatic redirect following is turned off. We follow them ourselves
  and run all the rules on every hop, so a public URL cannot bounce us to a private one.
- **DNS rebinding:** a hostile DNS server can answer "public" when we validate and "private"
  a moment later when httpx connects. To close this, we connect to the exact IP we validated,
  while still sending the original host name (`Host` header and TLS certificate check).
  See decision 1.
- System proxy environment variables are ignored (`trust_env=False`), so a proxy cannot
  re-resolve the name behind our back.

Registration (`POST /links`) is unchanged: `http://localhost` can still be saved. It is
refused when checked.

## 6. Results

| situation | `status_code` | `error` | `last_status` |
|---|---|---|---|
| final response is 2xx | the code | null | `alive` |
| final response is 401, 403 or 429 | the code | null | `alive` |
| final response is anything else (404, 410, 500...) | the code | null | `dead` |
| took too long | null | `timeout` | `dead` |
| host name does not resolve | null | `dns` | `dead` |
| bad or expired certificate, TLS failure | null | `ssl` | `dead` |
| connection refused or reset, other network error | null | `connection` | `dead` |
| more than 5 redirects (including loops) | null | `too_many_redirects` | `dead` |
| refused by the SSRF guard | null | `blocked_url` | `blocked` |
| anything unexpected (logged with traceback) | null | `unknown` | `dead` |

- A failed target is a **successful check**: the API returns 200 and records the failure.
  It never returns 500 because a website is broken.
- A redirect with no `Location` header counts as a final response (so `dead`).
- `latency_ms` is the total time including redirects. It is null for `blocked_url`.
- `error` never contains internal details such as which IP a name resolved to.

## 7. Concurrency and timeouts
- One `httpx.AsyncClient` and one `asyncio.Semaphore(MAX_CONCURRENCY)` (default 10) per API
  request. The cap is therefore per request, not for the whole server (see section 12).
- `CHECK_TIMEOUT_SECONDS` (default 10) is used twice: as the httpx timeout, and as a limit on
  the **whole** check (DNS plus all redirects). The httpx timeout alone limits each network step,
  so a slow server could otherwise take much longer.
- Time spent waiting for a semaphore slot does not count against a link's timeout.

**Database rule:** a database session must not be shared by tasks running at the same time.
So `/check-all` works in three phases:
1. Read all links, then end the transaction.
2. Check them concurrently (network only, no database).
3. Save the results one at a time.

No database transaction is held open while waiting on websites.

## 8. API
| method & path | success | errors |
|---|---|---|
| `POST /links/{id}/check` | 200 + CheckResult | 404, 422 |
| `POST /check-all` | 200 + CheckAllResult | none expected |
| `GET /links/{id}/checks` | 200 + list[CheckRead], newest first | 404, 422 |

- `CheckRead`: `id, link_id, status_code, latency_ms, error, checked_at`.
- `CheckResult`: `{link_id, last_status, check: CheckRead}`.
- `CheckAllResult`: `{total, alive, dead, blocked, results: list[CheckResult]}`.
- With no links, `/check-all` returns zero counts and an empty list.
- If a link is deleted while it is being checked, `/check-all` skips it (it is not counted),
  and the single-link endpoint returns 404.
- Errors use the existing shape `{"error": {"code", "message", "details"}}`.

## 9. Design and files
Layering: router -> `check_runner` service -> `checker` (network) and repository (database).

| file | content |
|---|---|
| `services/url_safety.py` | resolve the host, apply the rules, return the safe IP addresses |
| `services/checker.py` | `check_url`, `check_many`; returns plain result objects |
| `services/check_runner.py` | read links, check them, save the results |
| `repositories/checks.py` | `save` (insert check and set `last_status` in one commit), `list_for_link` |
| `schemas/check.py` | `CheckRead`, `CheckResult`, `CheckAllResult` |
| `routers/checks.py` | the three endpoints |
| `main.py` | include the new router |

Logging: link id, outcome, error type and latency. Full URLs are not logged, because query
strings can contain tokens. No `print`.

## 10. Tests (no real network)
HTTP is mocked with `respx`. DNS is network too, so tests replace the resolver with a fake one.

- `tests/test_url_safety.py`: at least one test per rule in section 5, plus: a public address is
  allowed, a name with one public and one private address is refused, and an unresolvable name
  reports `dns`.
- `tests/test_checker.py`:
  - each row of the table in section 6
  - redirect to a public URL is followed, including a relative `Location`
  - **redirect to a private address is blocked and the private URL is never requested**
  - redirect to a non-http scheme is blocked
  - the request goes to the validated IP with the original `Host` and TLS name
  - never more than `MAX_CONCURRENCY` checks run at once
  - the whole-check time limit applies
- `tests/test_checks_api.py` (new): the three endpoints, 404 and 422 cases, history order,
  `last_status` updates, `/check-all` with a mix of alive, dead and blocked links.

## 11. Decisions to confirm (my recommendation first)
1. **DNS rebinding.** *Connect to the validated IP* (recommended) vs *validate, then let httpx
   resolve again*. CLAUDE.md calls SSRF protection critical, and the second option can be
   bypassed. The cost is about 20 more lines, tests that mock IP addresses, and no connection
   reuse between requests.
2. **401, 403, 429.** *Count as alive* (recommended): the page exists but refuses a bot or is
   rate limiting. Counting them dead would later fetch pointless Wayback copies.
3. **Request method.** *GET, headers only* (recommended) vs HEAD. Many servers answer HEAD wrongly.
4. **Blocked links.** *Save a check with `blocked_url` and status `blocked`* (recommended) vs
   return a 4xx error and save nothing. The first keeps history and lets `/check-all` carry on.
5. **Ports.** *Allow any port on a public address* (recommended) vs only 80 and 443.
6. **`/check-all`.** *Wait and return all results* (recommended) vs run in the background.
   Background jobs are unreliable on Cloud Run's default settings and add a lot of complexity.

## 12. Known limitations
- `/check-all` takes longer as links grow. Worst case is about
  `links / MAX_CONCURRENCY * CHECK_TIMEOUT_SECONDS` (100 links: about 100 s).
- Several `/check-all` calls at once each get their own concurrency cap.
- The API has no authentication. Anyone with the public URL can make the server send requests
  to public sites. This needs a decision before step 7 (deploy).
- No retries: one temporary failure marks a link `dead` until the next check.
- Cookies are not supported by design, so a site that needs them to redirect may look `dead`.
- Check history grows forever and `GET /links/{id}/checks` is not paginated.

## 13. Manual verification (definition of done)
1. `uv run pytest` is green.
2. `docker compose up -d db`, then start the API with uvicorn.
3. In `/docs`: add a working page, a 404 page, a name that does not exist, and
   `http://localhost:8000/health`.
4. `POST /check-all` reports them as alive, dead, dead (`dns`) and blocked.
5. `GET /links/{id}/checks` shows the history, and `GET /links` shows `last_status`.

## 14. Implementation order (small commits)
1. URL safety guard and its tests
2. Checker service and its tests
3. Check schemas and repository
4. Router, wiring and API tests
5. Manual verification, then you open the PR on GitHub
