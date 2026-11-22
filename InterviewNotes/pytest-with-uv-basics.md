# pytest with uv: basics for API projects (revision notes)

## One-line summary
pytest runs small functions that call your code and `assert` the result is what you expect. In an API project it catches regressions, documents behaviour and lets you refactor safely.

## Setup with uv
```bash
uv add --dev pytest pytest-asyncio respx    # dev-only libraries (never pip install)
uv run pytest                               # run everything
```
- `--dev` puts them in the dev dependency group of `pyproject.toml`, not in production.
- `uv run` uses the project's virtual environment automatically.
- Add more tools the same way, e.g. `uv add --dev aiosqlite`.

Useful `pyproject.toml` settings:
```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"      # async tests run without extra decorators
testpaths = ["tests"]
```

## Commands to remember
| command | what it does |
|---|---|
| `uv run pytest` | run all tests |
| `uv run pytest -q` | quiet, short output |
| `uv run pytest -v` | one line per test |
| `uv run pytest tests/test_x.py` | one file |
| `uv run pytest tests/test_x.py::test_name` | one test |
| `uv run pytest -k "duplicate"` | tests whose name matches |
| `uv run pytest -x` | stop at the first failure |
| `uv run pytest --lf` | rerun only the last failures |
| `uv run pytest -s` | show `print` output |
| `uv run pytest --maxfail=3` | stop after 3 failures |

## Anatomy of a test
```python
def test_adds_numbers() -> None:
    result = add(2, 3)
    assert result == 5
```
- File names start with `test_`, function names start with `test_`. pytest finds them automatically.
- `assert` is the whole check. On failure pytest shows both sides of the comparison.
- Pattern: **arrange** (set up), **act** (call the code), **assert** (check).

## Testing async code
```python
async def test_fetch() -> None:
    data = await fetch_thing()
    assert data["ok"] is True
```
With `pytest-asyncio` and `asyncio_mode = "auto"`, just write `async def test_...`.

## Fixtures: reusable setup
```python
import pytest

@pytest.fixture
def sample_user() -> dict[str, str]:
    return {"name": "Asha", "email": "asha@example.com"}

def test_has_email(sample_user: dict[str, str]) -> None:
    assert "@" in sample_user["email"]
```
- A test asks for a fixture by naming it as a parameter.
- Fixtures can `yield` to run cleanup afterwards:
```python
@pytest.fixture
def temp_resource():
    r = open_resource()
    yield r          # test runs here
    r.close()        # cleanup always runs
```
- Put shared fixtures in `tests/conftest.py`. pytest loads it automatically, so no imports are needed.
- Each test gets a fresh fixture by default, so tests don't leak state into each other.

## Testing a FastAPI app without a server
```python
from httpx import ASGITransport, AsyncClient

async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
    r = await client.get("/health")
    assert r.status_code == 200
```
- Calls the app in memory: no uvicorn, no open port, fast.
- Assert on status code **and** body, especially error bodies.

## Replacing dependencies in tests
FastAPI lets a test swap what an endpoint depends on:
```python
app.dependency_overrides[get_session] = fake_session_provider
...
app.dependency_overrides.clear()    # always clean up
```
Typical swaps: real database to in-memory database, real clock to fixed time, real config to test config.

## Mocking outbound HTTP with respx
```python
import respx, httpx

@respx.mock
async def test_dead_link_is_recorded() -> None:
    respx.get("https://example.com/").mock(return_value=httpx.Response(404))
    result = await check_url("https://example.com/")
    assert result.status_code == 404
```
Simulate failures too:
```python
respx.get("https://slow.example/").mock(side_effect=httpx.ConnectTimeout("t"))
respx.get("https://down.example/").mock(side_effect=httpx.ConnectError("dns"))
```
- No real network ever happens, so tests are fast, deterministic and safe.
- Lets you test cases that are hard to trigger for real (timeouts, DNS errors, 500s).

## Parametrize: one test, many inputs
```python
@pytest.mark.parametrize("url", [
    "http://localhost/",
    "http://127.0.0.1/",
    "http://10.0.0.5/",
    "http://169.254.169.254/",
    "ftp://example.com/",
])
def test_unsafe_urls_rejected(url: str) -> None:
    assert is_safe_url(url) is False
```
Each input is reported as its own test. Ideal for rule lists like SSRF blocking, validation rules or status code mappings.

## Testing that errors are raised
```python
import pytest

def test_missing_link_raises() -> None:
    with pytest.raises(NotFoundError):
        find_link(999)
```

## Where pytest helps in an API project
| area | what to test |
|---|---|
| Validation | bad URLs, wrong types, too-long fields give 422 |
| Error shape | every failure returns the same JSON structure |
| CRUD | create, read, update, delete and the status code for each |
| Business rules | duplicates give 409, cascade deletes, ordering |
| Security rules | blocked addresses and schemes, one test per rule |
| External calls | success, timeout, DNS error, 5xx, all mocked |
| Concurrency limits | no more than N calls in flight at once |
| Graceful degradation | a downstream service failing does not crash the endpoint |
| Regressions | a bug is fixed, so write a test that would have caught it |

## What tests can and cannot prove
- Tests with a stand-in database prove your **logic** is right, quickly and reliably.
- They do not prove the real database, drivers, Docker or cloud setup work. Cover that with a manual run or a small integration suite.
- Passing tests show the cases you thought of are fine, not that the code is bug-free.

## Good habits
- One behaviour per test, with a name that reads like a sentence (`test_create_duplicate_returns_409`).
- Tests must be independent. Do not rely on order or leftover data.
- No real network, no real secrets, no shared files.
- Assert specific values (`== 409`), not vague ones (`!= 200`).
- Write the failing test first when fixing a bug. It proves the fix works.
- Run the suite before every commit.
- When a test fails, read the diff pytest shows before changing anything.

## Common problems
| symptom | usual cause |
|---|---|
| `coroutine was never awaited` | missing `await`, or async test without `pytest-asyncio` |
| `async def functions are not natively supported` | `asyncio_mode` not set or plugin missing |
| `fixture 'x' not found` | typo, or fixture is not in `conftest.py` or the same file |
| Test passes alone, fails in the suite | shared state leaking between tests |
| `ModuleNotFoundError` for your package | run with `uv run pytest` from the project root |
| Real network calls slipping in | forgot `@respx.mock` |
| Overrides leak into other tests | forgot `dependency_overrides.clear()` |

## Interview-ready answers
- **Why write tests?** They catch regressions, document behaviour and make refactoring safe.
- **What is a fixture?** Reusable setup (and teardown) that pytest injects into tests by parameter name.
- **How do you test an API without a server?** An ASGI test client calls the app in memory.
- **How do you test code that calls other services?** Mock the HTTP layer (e.g. respx) so tests never touch the network, and simulate failures.
- **Unit vs integration tests?** Unit tests check logic in isolation and run fast. Integration tests check real components together (e.g. against a real database) and are slower but closer to production.
- **What is parametrize for?** Running one test body over many inputs.
- **Why `uv run pytest`?** It runs in the project's locked environment, so results match everyone else's.

## Quick self-test
1. What do the `--dev` flag and `uv run` each do?
2. When would you use a fixture with `yield`?
3. Why mock outbound HTTP instead of calling real sites?
4. What does `parametrize` save you from writing?
5. Name one thing a test with a fake database cannot prove.
