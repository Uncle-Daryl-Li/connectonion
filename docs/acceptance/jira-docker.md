# Jira Docker Verification (Step 7)

Exact commands and pass criteria for the Jira unit-test Docker run.
Required by the v9 acceptance checklist before Step 7 is considered complete.

---

## Build

```
docker build -f Dockerfile.jira-test -t connectonion-jira-test .
```

## Run

```
docker run --rm connectonion-jira-test
```

To make the absence of credentials explicit, pass empty values:

```
docker run --rm \
  -e JIRA_URL="" \
  -e JIRA_EMAIL="" \
  -e JIRA_API_TOKEN="" \
  connectonion-jira-test
```

---

## Pass Criteria

| # | Criterion | How verified |
|---|-----------|-------------|
| 1 | **Exit code 0** | `docker run` exits 0 |
| 2 | **All 57 tests pass** | Output ends with `57 passed` |
| 3 | **No real Jira network calls** | `conftest.py` `_no_network` autouse fixture raises on any non-loopback socket; tests mock all HTTP via `unittest.mock` |
| 4 | **No token in image layers** | `JIRA_API_TOKEN` is never set in `Dockerfile.jira-test`; layer audit (see below) returns no matches |
| 5 | **No token in test output** | Tests assert `JIRA_TOKEN not in result.stdout` and `JIRA_TOKEN not in result.stderr` (CLI-1 through CLI-10) |

---

## Token Layer Audit

Run after a successful build:

```
docker history --no-trunc connectonion-jira-test | grep -i "JIRA_API\|secret\|token"
```

**Pass**: grep exits 1 (no output). Any match is a failure.

---

## What the Container Does

1. Starts from `python:3.11-slim`
2. Copies `pyproject.toml`, `README.md`, `MANIFEST.in`, and `connectonion/`
3. Installs `connectonion[dev]` — includes pytest, pytest-mock, httpx, typer, rich
4. Copies `tests/` and `pytest.ini` (no `.env`, no `.co/`, no credential files)
5. Runs `pytest tests/unit/test_jira.py -v --tb=short -p no:cacheprovider`

The 57 tests cover:

| Step | Tests | Area |
|------|-------|------|
| 1–2  | 10-2 … 10-4c, 9-0 … 9-6 | Tool layer — connection, pagination |
| 3    | 9-7, 9-8, 9-9 | Tool layer — timeout, read-only, rate-limit |
| 4    | 10-1, 10-5 … 10-15 | Tool layer — scope, `write_source_yaml` |
| 5    | 18-1 … 18-10 | Adapter contract — `AdapterResult`, JSONL layout |
| 6    | CLI-1 … CLI-10 | CLI layer — `jira_commands.py` via `CliRunner` |

---

## Verification Evidence (2026-10-10)

### docker build — exit 0

```
docker build -f Dockerfile.jira-test -t connectonion-jira-test .
```

Build completed successfully (exit 0). Base image: `python:3.11-slim`.

### docker run — 57 passed

```
platform linux -- Python 3.11.17, pytest-9.1.1, pluggy-1.6.0 -- /usr/local/bin/python3.11
rootdir: /app
configfile: pytest.ini
collected 57 items

...
============================== 57 passed in 3.07s ==============================
```

Exit code: 0.

### Token layer audit — no matches

```
docker history --no-trunc connectonion-jira-test | grep -i "JIRA_API\|secret\|token"
```

Output: _(empty — grep exited 1)_. No token present in any image layer.
