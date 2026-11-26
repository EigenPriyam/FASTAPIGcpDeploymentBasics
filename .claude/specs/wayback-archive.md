# Spec: Wayback archive lookup for dead links

Status: APPROVED (all recommended decisions). Branch: `feature/wayback-archive`. Roadmap step 4.

## 1. In one example
1. You check `https://example.org/old-page` and it returns 404, so it is `dead`.
2. The app asks the Internet Archive: "do you have a saved copy of this page?"
3. It answers with an address on `web.archive.org`.
4. The app saves that address in `links.archive_url`, so you can still read the page.

## 2. Scope
In:
- `services/archive.py`: ask the Wayback availability API.
- Use it after a `dead` result in both check endpoints.
- Save and return `archive_url`.
- Tests, with all HTTP mocked.

Out: asking the archive to save new copies, retries, Dockerfile, GCP.

No database change (`links.archive_url` already exists). No new libraries or settings.

## 3. The Wayback API (sampled on 2026-11-26)
`GET https://archive.org/wayback/available?url=<the link>`

Saved copy exists:
```json
{"url": "github.com", "archived_snapshots": {"closest": {"status": "200", "available": true,
 "url": "http://web.archive.org/web/20261126073010/https://github.com/",
 "timestamp": "20261126073010"}}}
```
No saved copy (also HTTP 200):
```json
{"url": "https://nope.invalid/zzz", "archived_snapshots": {}}
```

## 4. Rules
| check result | ask Wayback? | `archive_url` |
|---|---|---|
| `dead`, none saved yet | yes | set if a copy is found, else stays empty |
| `dead`, already saved | no | kept |
| `alive` | no | cleared |
| `blocked` | **never** | unchanged |

- `blocked` links are never sent: they may name internal machines, and that should not be
  disclosed to an outside service.
- A snapshot is used only if `available` is true, its `status` is 2xx, and its address is
  `http(s)` on `web.archive.org`. We store it as `https`.
- The `#fragment` part of a link is not sent.

## 5. When Wayback is down
Timeout, network error, non-200 reply (including 429 "too many requests") or unreadable JSON:
- log a warning with the link id,
- still save the check result and return 200,
- leave `archive_url` as it was. The next check of that dead link tries again.

The lookup can never turn a check into an error.

## 6. Design
- `services/archive.py`: `find_snapshot(client, link_id, url, timeout) -> str | None`. It never raises.
- Explicit timeout: `CHECK_TIMEOUT_SECONDS`. Redirects are not followed.
- At most 3 lookups at a time, to be polite to a free service.
- `services/check_runner.py` now works in four phases: read links, check them, look up dead
  ones, save one by one. No database transaction is open during network calls.
- `repositories/checks.py`: also reads the current `archive_url`, and saves the new value in the
  same transaction as the check.
- The lookup time is not part of `latency_ms`.

## 7. API change
No new endpoints. `CheckResult` (returned by both check endpoints) gains `archive_url`.
`GET /links` already shows it.

## 8. Tests
- `tests/test_archive.py`: copy found; no copy; timeout; network error; 429 and 500; bad JSON;
  unexpected shape; `available` false; non-2xx snapshot; address on another host; `http` stored
  as `https`; fragment not sent.
- `tests/test_checks_api.py`: one test per row of section 4; Wayback down still returns 200
  and saves the check; `/check-all` with several dead links.
- Existing tests with dead links get a Wayback mock, so no test reaches the real service.

## 9. Decisions to confirm (my recommendation first)
1. **When to ask.** *Automatically during a check* (recommended, matches CLAUDE.md) vs a
   separate endpoint.
2. **Link is alive again.** *Clear `archive_url`* (recommended) vs keep it.
3. **Already has a copy.** *Do not ask again* (recommended, fewer calls) vs refresh every time.
4. **Query strings** (the `?...` part). *Send them* (recommended: without them Wayback may
   return a different page) vs strip them to avoid sharing tokens.
5. **Show it in the check response.** *Yes* (recommended) vs only through `GET /links`.

## 10. Known limitations
- Dead links are shared with the Internet Archive, including any query string. A private
  host name that does not resolve counts as `dead`, so it is sent too.
- If Wayback is slow, `/check-all` takes longer.
- The closest copy may itself be an error page that was saved with a 2xx status.
- Having no copy and being unable to ask look the same: `archive_url` is empty.

## 11. Manual verification
1. `uv run pytest` is green.
2. Start the database and API, add a dead page that once existed, and check it.
3. `archive_url` opens a saved copy in the browser.
4. Add `https://nope.invalid/` and check it: `dead`, and `archive_url` stays empty.

## 12. Implementation order (small commits)
1. Archive client and its tests
2. Repository, runner and schema changes with API tests
3. Manual verification, then you open the PR
