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
| `docs/acceptance/benchmarks/jira.yaml` | Versioned repo copy of jira benchmark (7 cases) |
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
```

These are the **official copies for code review and version control**.

Before running `co eval` on a new machine or CI environment, run
`co benchmark check jira` first and use the local benchmark directory printed
in its output. That location is environment-specific (for example,
`D:\\9900\\.co\\benchmarks` in the development environment), so do not assume
that it is `~/.co/benchmarks`. Copy the two canonical files above into that
reported directory, retaining the filenames `jira.yaml` and `onenote.yaml`.

Keep the two locations in sync: any edit to the benchmark suite must be
applied to **both** the `docs/acceptance/benchmarks/` copy (committed) and
the local `.co/benchmarks/` copy (used at runtime).

---

## Benchmark authoring

Benchmarks were written **before** the SKILL.md files were finalised,
following the `co benchmark` → `co eval` → edit skill loop.

### Jira benchmark cases (7)

| id | kind |
|----|------|
| `list-projects` | normal |
| `verify-connection` | normal |
| `fetch-named-issues` | normal |
| `missing-env-guidance` | normal |
| `json-output-for-projects` | normal |
| `create-issue-refused` | counterexample |
| `delete-issue-refused` | counterexample |

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
Valid: 7 cases (5 normal, 2 counterexample) — D:\9900\.co\benchmarks\jira.yaml
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
jira      7 cases, valid    D:\9900\.co\benchmarks\jira.yaml
onenote   7 cases, valid    D:\9900\.co\benchmarks\onenote.yaml
Next: co benchmark check jira
```

---

## `co eval run` — **pending / blocked**

`co benchmark check` only validates the YAML structure of the benchmark suite.
It does not verify that the Skill actually guides a real agent correctly.
`co eval run` is the required next step and has **not yet been executed**.

**Status: eval pending — blocked on demo environment.**

Required to unblock:
- A configured `agent.py` in the working directory
- An active LLM API key (or managed `co/gemma` with a positive balance)
- Network connectivity to the LLM provider

Once the demo environment is available, run:

```
co eval run jira   --agent agent.py --skill co-jira   --runs 1
co eval run onenote --agent agent.py --skill co-onenote --runs 1
```

Then append the real `co eval report` output to this file before marking
Step 8 complete and the PR ready for review.

The skills at `.co/skills/co-jira/SKILL.md` and `.co/skills/co-onenote/SKILL.md`
are discoverable by `co eval run` (the `skill is not discoverable` error is
resolved), but discoverability alone is not a substitute for a passing eval.

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
