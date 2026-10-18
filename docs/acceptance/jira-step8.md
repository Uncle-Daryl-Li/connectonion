# Step 8 — Jira and OneNote Skills (v9 acceptance checklist)

Write / update Jira and OneNote Skills in `connectonion/useful_skills/`;
run `co benchmark` and `co eval` before and after each change; save terminal
output as PR verification evidence.

---

## Files created

| File | Purpose |
|------|---------|
| `connectonion/useful_skills/co-jira/SKILL.md` | Jira skill — verify, projects, issues |
| `connectonion/useful_skills/co-onenote/SKILL.md` | OneNote skill — ls, pages, read (read-only; Notes.Read scope) |
| `docs/acceptance/benchmarks/jira.yaml` | Versioned repo copy of jira benchmark (11 cases) |
| `docs/acceptance/benchmarks/fixtures/` | Canonical offline eval fixtures (map.json + 4 txt/json files) |
| `docs/acceptance/benchmarks/onenote.yaml` | Versioned repo copy of onenote benchmark (7 cases) |
| `.co/benchmarks/jira.yaml` | Local working copy used by `co benchmark` / `co eval` |
| `.co/benchmarks/onenote.yaml` | Local working copy used by `co benchmark` / `co eval` |

---

## Benchmark repo copies

`co benchmark` and `co eval` read from the local `.co/benchmarks/` directory,
which is **not tracked by Git**. To make the benchmark definitions reviewable
and versioned alongside the rest of the PR, canonical copies are kept in:

```
docs/acceptance/benchmarks/jira.yaml
docs/acceptance/benchmarks/onenote.yaml
docs/acceptance/benchmarks/fixtures/        ← offline eval fixtures for jira
    map.json
    jira_verify.txt
    jira_projects.txt
    jira_projects_json.json
    jira_issues.txt
```

These are the **official copies for code review and version control**.

The `fixtures/` directory contains pre-recorded outputs for read-only Jira
commands. When `co eval run` executes with `live=False` (the default), the
benchmark runner swaps the real `bash`, `run_background`, and `task_output`
tools for fixture stubs that return these files instead of invoking real
subprocesses.  `bash` returns fixture content directly; `run_background`
creates a task ID that `task_output` resolves.  Commands not listed in
`map.json` fall through to the original tools.
This makes eval runs deterministic without requiring a live Jira account.

Before running `co eval run jira` on a new machine or CI environment:

1. Run `co benchmark check jira` and note the local benchmark directory shown
   in its output (e.g. `D:\9900\.co\benchmarks` — this path is
   environment-specific, do not hardcode it).
2. Copy **both** the YAML and the complete fixtures directory into that
   reported directory.

Example using Git Bash (substitute the exact path printed by `co benchmark check jira`):

```bash
# Replace this value with the exact directory printed by:
# co benchmark check jira
LOCAL_BM="./.co/benchmarks"

mkdir -p "$LOCAL_BM/fixtures"
cp docs/acceptance/benchmarks/jira.yaml "$LOCAL_BM/jira.yaml"
cp -r docs/acceptance/benchmarks/fixtures/. "$LOCAL_BM/fixtures/"
```

Keep the three locations in sync: any edit to the benchmark suite or fixtures
must be applied to **both** the `docs/acceptance/benchmarks/` copy (committed)
and the local `.co/benchmarks/` copy (used at runtime).

---

## Benchmark authoring

Benchmarks were written **before** the SKILL.md files were finalised,
following the `co benchmark` → `co eval` → edit skill loop.

### Jira benchmark cases (11)

| id | kind |
|----|------|
| `list-projects` | normal |
| `verify-connection` | normal |
| `fetch-named-issues` | normal |
| `missing-env-guidance` | normal |
| `json-output-for-projects` | normal |
| `create-issue-refused` | counterexample |
| `delete-issue-refused` | counterexample |
| `incremental-repeat-import` | normal |
| `full-repair-read` | normal |
| `incremental-without-history` | normal |
| `automatic-jira-scheduler-refused` | counterexample |

### OneNote benchmark cases (7)

| id | kind |
|----|------|
| `list-notebooks` | normal |
| `list-recent-pages` | normal |
| `read-page-by-number` | normal |
| `auth-not-configured` | normal |
| `create-page` | counterexample — integration is read-only; agent must refuse |
| `delete-page-refused` | counterexample |
| `edit-page-refused` | counterexample |

---

## `co benchmark check` — 2026-10-10

### Jira

```
co benchmark check jira
```

```
Valid: 11 cases (8 normal, 3 counterexample) — D:\9900\.co\benchmarks\jira.yaml
Structure is checked; whether the cases are really different decisions is for a person to read.
Next: write or edit .co/skills/<skill>/SKILL.md, then co eval run jira --agent agent.py --skill <skill> --runs 1
```

### OneNote

```
co benchmark check onenote
```

```
Valid: 7 cases (4 normal, 3 counterexample) — D:\9900\.co\benchmarks\onenote.yaml
Structure is checked; whether the cases are really different decisions is for a person to read.
Next: write or edit .co/skills/<skill>/SKILL.md, then co eval run onenote --agent agent.py --skill <skill> --runs 1
```

### `co benchmark list` (after both skills written)

```
co benchmark list
```

```
jira      11 cases, valid    D:\9900\.co\benchmarks\jira.yaml
onenote    7 cases, valid    D:\9900\.co\benchmarks\onenote.yaml
Next: co benchmark check jira
```

---

## `co eval run` — status

`co benchmark check` validates YAML structure only. `co eval run` invokes a
real agent against each case and scores every expectation.

**Status: passed — 11/11 cases, 18/18 expectations, 0 failures (2026-10-18).**

### What has been done

- Offline Jira eval fixtures are implemented and committed:
  `docs/acceptance/benchmarks/fixtures/` (see section above).
- The benchmark runner (`connectonion/benchmark/runner.py`) automatically
  activates these fixtures when `live=False` (the default for `co eval run`).
  Pre-recorded outputs are returned for `co jira verify`, `co jira projects`,
  `co jira projects --json`, and `co jira issues ALPHA ALPHA-1 ALPHA-2` without
  making live network calls, making eval results deterministic.
- `co benchmark check jira` passes clean with 11 cases.

### Verification record — 2026-10-18

```
co eval run jira \
  --agent D:/9900/project/connectonion/examples/rem-skill-evals/agent.py \
  --skill co-jira --invoke explicit --runs 1
```

| Field | Value |
|-------|-------|
| Date | 2026-10-18 |
| Agent model | gemini-3.8-flash |
| Cases | 11 / 11 |
| Attempts | 11 / 11 |
| Expectations | 18 / 18 |
| Failed | 0 |
| Stopped | 0 |
| Invalid | 0 |
| Runner errors | 0 |
| Report | `.co/eval-runs/jira/20261018T000559463476Z-b885/report.json` |

**Scope note:** this is an offline fixture-based eval. The benchmark runner
substitutes pre-recorded outputs for `bash`, `run_background`, and
`task_output` calls so no live Jira account is required. The eval verifies
that the agent selects the correct `co jira` sub-commands and refuses unsafe
operations (create, delete, automatic scheduling) as specified by the skill.
It does **not** verify live Jira connectivity or API token handling.

---

## Skill content summary

### co-jira SKILL.md

- Triggers: user mentions Jira, issue keys (e.g. `ALPHA-1`), "my tickets"
- Setup: `co jira verify` — diagnoses missing `JIRA_URL`/`JIRA_EMAIL`/`JIRA_API_TOKEN`
- Commands: `verify`, `projects [--json]`, `issues PROJECT KEY… [--json]`
- Safety: read-only; JQL injection blocked; key-prefix mismatch blocked before any HTTP
- Error table: `JiraConfigError`, `JiraAuthError`, `JiraPermissionError`, timeout, 429

### co-onenote SKILL.md

- Triggers: user mentions OneNote, notebooks, "my notes", page titles
- Setup: `co auth microsoft` — account must be connected first
- **Scope: read-only (`Notes.Read`).** Do not create, edit, or delete pages.
- Commands: `ls`, `pages [section]`, `read <num|title>`
- Row numbers: valid 15 min; must re-list after expiry
- If user requests any write operation: explain that this integration is read-only

---

## F13FCAKE-18 prototype scope and limitations

### Jira chain — fully integrated

The full Jira pipeline is implemented and covered by synthetic unit tests:

```
verify_connection() → issue_items() → write_source_yaml()
    → jira_issues_to_records() → write_daily_jsonl()
```

The end-to-end test `test_adapter_f13fcake18_full_pipeline_both_chains_end_to_end_synthetic`
(test id `18-11`) exercises both chains using synthetic fixtures only (no live network calls).
This is synthetic evidence, **not** a live demo.

### OneNote chain — synthetic fixtures only

The OneNote adapter function `onenote_pages_to_records()` is implemented and covered by
synthetic unit tests.  The full pipeline using live `Notes.Read` scope is **not yet implemented**:

- The OneNote team's integration code is pending.
- An authorized Microsoft account with `Notes.Read` scope is required for a live demo.
- All current OneNote tests (`18-3`, `18-5`, `18-6`, `18-7`, `18-8b`, `18-11`, etc.) use
  synthetic fixture dicts, not responses from the Graph API.

### F13FCAKE-9 live Jira verification — pending

`co jira verify`, `co jira projects`, and a scoped `co jira sync` against a real authorized
read-only Jira account have **not yet been executed**.  Running them and saving sanitized
terminal output as evidence is required before this story can be closed.

The token handling code is complete (temporary shell variable, never persisted) and all
unit-level security assertions pass, but live account verification needs an authorized
account.

### Resilience: one-side failure does not corrupt the other

The `consume_jsonl_records()` reader is source-agnostic.  A corrupted or missing JSONL from
one source (e.g. Jira) does not prevent valid records from another source (e.g. OneNote) from
being read.  Malformed lines are reported in `AdapterResult.errors`; valid lines from all
other sources are still returned in `AdapterResult.records`.

Test `18-12` (`test_adapter_f13fcake18_jira_jsonl_corrupted_onenote_still_readable`) verifies
this property explicitly.
