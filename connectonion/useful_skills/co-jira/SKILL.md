---
name: co-jira
description: Work with a Jira instance through `co jira` — verify the connection, list projects, read selected issues, and run incremental checkpoint-backed syncs for co rem. Use when the user mentions Jira, a Jira issue key like ALPHA-1, "my tickets", "Jira projects", "keep Jira up to date", or wants to check their Jira account.
---

# co jira

`co jira` acts against the Jira instance named by `JIRA_URL` as the user
identified by `JIRA_EMAIL` and `JIRA_API_TOKEN`. It is **read-only**: it never
creates, edits, comments on, or deletes any Jira resource.

**Read the output, not just the exit code.** Every failure exits 1 and ends
with one `Next:` line or an error message; run that command, it is the fix.

## Answering simple requests — reply immediately

This section divides requests into two categories based on whether the agent
should execute the command or simply explain it.

### Observation requests — run one foreground command, show the output

For the four request types below, run **exactly one** corresponding foreground
`co jira …` command and show or accurately summarise its actual output.
If the command fails, report only the safe error text and the `Next:` guidance
it prints; do **not** log, display, or relay the value of `JIRA_API_TOKEN`.
Do **not** start a background task, poll repeatedly, run Git commands, inspect
the repository, retry automatically, or perform unrelated diagnostics.

**Verify / check Jira connection** → run `co jira verify`
Confirms credentials; prints the authorised account and site URL on success, or
a `Next:` repair line on failure.

**List all projects** → run `co jira projects`
Prints a table of every project the account can access.

**JSON list of projects** → run `co jira projects --json`
Return the command's raw JSON output unchanged, with no prose before or after it.

**Fetch named issues** (all keys must share the project prefix) → run, e.g.:
```bash
co jira issues ALPHA ALPHA-1 ALPHA-2
```
Reads and displays the named issues.

### Sync requests — reply with command and explanation; do not execute unless asked

For the request types below, **reply immediately** with the command and the
explanation shown here.  Do **not** run the command, start a background task,
poll, run Git commands, or inspect the repository unless the user explicitly
asks you to execute the import now.  Do **not** claim the command has already
succeeded unless its actual output is visible to you.

**Incremental repeat sync (user-triggered, checkpoint-backed)**
```bash
co jira sync-inc --project ALPHA
```
This is a user-triggered import; it uses a source-scoped checkpoint and does
not schedule or poll automatically.  Run it whenever the user wants to bring
their knowledge store up to date.

**Full repair read** (rereads all issues; incremental cursor unchanged)
```bash
co jira sync-inc --project ALPHA --full
```
Fetches every matching issue and writes any new records, but does **not**
advance the incremental cursor — the next `sync-inc` run resumes from exactly
the same checkpoint position as before this run.

**Incremental sync omitting changelog for this run only**
```bash
co jira sync-inc --project ALPHA --no-history
```
Omits changelog data for **this invocation only**.  The flag does not persist
as configuration; the next run without it collects changelog data normally and
the incremental checkpoint advances as usual.

## Before anything

Run `co jira verify`. If it reports a missing variable, tell the user to run
the `co env set` command it prints. Never ask them to paste the token into the
chat.

| Missing variable | Repair command |
|---|---|
| `JIRA_URL` | `co env set JIRA_URL https://your-domain.atlassian.net` |
| `JIRA_EMAIL` | `co env set JIRA_EMAIL you@example.com` |
| `JIRA_API_TOKEN` | Use the no-echo procedure below |

Tokens are created at https://id.atlassian.com/manage-profile/security/api-tokens
(Settings → Security → API tokens). Set the token as a **temporary shell variable**
so it is never saved to disk. Unset it immediately after use.
**Never paste the real token into chat, log output, or Git.**

```bash
# Bash / Git Bash — no-echo, not saved to disk
read -rsp "Jira API token: " JIRA_API_TOKEN; echo
export JIRA_API_TOKEN
co jira verify   # or any other co jira command
unset JIRA_API_TOKEN
```

```zsh
# zsh — no-echo, not saved to disk
read -rs "JIRA_API_TOKEN?Jira API token: "; echo
export JIRA_API_TOKEN
co jira verify   # or any other co jira command
unset JIRA_API_TOKEN
```

## Which command

| You want to | Run |
|---|---|
| Confirm credentials and show the authorised account | `co jira verify` |
| List all projects the account can access | `co jira projects` |
| Same, as JSON for scripting | `co jira projects --json` |
| Read specific issues from a project | `co jira issues ALPHA ALPHA-1 ALPHA-2` |
| Same, as JSON | `co jira issues ALPHA ALPHA-1 ALPHA-2 --json` |
| Collect issues and write source.yaml for co rem (one-off) | `co jira sync ALPHA ALPHA-1 ALPHA-2` |
| Same, to a custom directory | `co jira sync ALPHA ALPHA-1 --output ~/my/dir` |
| Incremental repeat sync (ongoing, checkpoint-backed) | `co jira sync-inc --project ALPHA` |
| Same, restricted to specific issues | `co jira sync-inc --project ALPHA --issues ALPHA-1 --issues ALPHA-2` |
| Force a full reread of all matching issues (repair read; cursor unchanged) | `co jira sync-inc --project ALPHA --full` |
| Incremental sync, machine-readable output | `co jira sync-inc --project ALPHA --json` |
| Incremental sync, omit changelog payload | `co jira sync-inc --project ALPHA --no-history` |

## The 80 % commands

```bash
co jira verify                                   # confirm connection; shows site URL + display name
co jira projects                                 # table: KEY  Name
co jira projects --json                          # {"projects": [{"key": "ALPHA", "name": "..."}, ...]}
co jira issues ALPHA ALPHA-1 ALPHA-3             # issue summaries for the named keys
co jira issues ALPHA ALPHA-1 ALPHA-3 --json      # {"issues": [...]}
co jira sync ALPHA ALPHA-1 ALPHA-3               # verify + collect + write ~/.co/rem/docs/jira/source.yaml
co jira sync ALPHA ALPHA-1 --json                # {"written": "...", "project": "ALPHA", "issues": 1}

# Incremental (checkpoint-backed) sync — ongoing imports
co jira sync-inc --project ALPHA                 # fetch only issues updated since last run
co jira sync-inc --project ALPHA --full          # force full re-import (does NOT advance cursor)
co jira sync-inc --project ALPHA --issues ALPHA-1 --issues ALPHA-2  # restrict to specific keys
co jira sync-inc --project ALPHA --json          # {"mode": "incremental", "net_new_records": 3, ...}
co jira sync-inc --project ALPHA --no-history    # omit changelog payload (lightweight)
```

## Error handling

| Error | What it means | What to do |
|---|---|---|
| `JiraConfigError` | A required env var is missing | Run the `co env set` command in the message |
| `Jira authentication failed` | The token or email is wrong (401) | Re-generate a token, then use the no-echo procedure above |
| `JiraPermissionError` (403) | Account lacks access to that project | Check project permissions in Jira; ask the project admin |
| `Could not reach Jira` | DNS / TLS / connection failure | Check VPN, network, and that `JIRA_URL` is correct |
| Network timeout | Jira took too long | Retry; if persistent, check VPN / network |
| Rate-limit (429) | Too many requests | Wait and retry; reduce request frequency |
| `temporarily unavailable` (5xx) | Jira server error | Wait a few minutes and retry |

Error messages never contain the token. Do not log, display, or relay the
value of `JIRA_API_TOKEN`.

## Scope and safety

- **Issue keys must match the project.** `co jira issues ALPHA BETA-1` is
  rejected before any HTTP request — all keys must start with `ALPHA-`.
- **JQL injection is blocked.** Keys containing quotes, spaces, or JQL
  keywords are rejected before any HTTP request.
- **Empty selections are rejected.** `co jira issues ALPHA` (no keys) exits 1
  with a clear message.
- `co jira` cannot delete, create, comment on, or transition any issue. Say
  so if the user asks for a write operation.

## Incremental sync (`co jira sync-inc`)

`co jira sync-inc` runs a cursor-based incremental import that is safe to call
repeatedly (user-triggered; do not schedule automatically). It is designed for
the **ongoing** use case; `co jira sync` is for **one-off** imports.

### How it works

1. On the **first run** (no checkpoint), it fetches all matching issues.
2. On **subsequent runs**, it fetches only issues whose `updated` timestamp is
   >= the last checkpoint cursor minus a 2-minute overlap window. This
   eliminates duplicates while tolerating sub-minute Jira clock skew.
3. New records are deduplicated against the existing daily JSONL and the
   overlap hash set before writing.
4. The checkpoint (cursor + scope hash + overlap hashes) is saved only after
   the records are durably flushed to disk (fsync + atomic rename).
5. **Crash recovery**: if the process is interrupted between writing the staging
   file and flushing the daily JSONL, the next run automatically recovers and
   resumes without re-fetching from Jira.

### When to use `--full`

- After changing the scope (adding or removing issue keys).
- After a suspected checkpoint corruption (`checkpoint_corrupt` error).
- Whenever you want a clean slate without deleting checkpoint files manually.

`--full` is a **repair read**: it fetches all matching issues and writes any
new records, but it does **not** advance the incremental cursor.  The next
`sync-inc` run (without `--full`) will still start from the same checkpoint
position as before the full run.  `checkpoint_result` is reported as `"skipped"`
to reflect that no cursor update occurred.

### `--no-history`

`--no-history` omits the Jira changelog payload from each record.  Use it for
large datasets or privacy-sensitive projects where per-field change history is
not needed.  **This flag is user-supplied per invocation and has no persistent
state; it is not a scheduler directive.**  Omitting it on the next run
re-enables changelog collection without requiring any other change.
Records written with `--no-history` have the changelog field permanently absent
in the JSONL — re-running without the flag on a subsequent sync appends **new**
records that include the changelog, but does not modify or back-fill records
already written.  On a successful run the incremental checkpoint advances
normally, identical to a standard `sync-inc` run.

### Exit codes for `sync-inc`

| Code | Meaning | What to do |
|---|---|---|
| 0 | Success (or up-to-date) | Nothing |
| 1 | Partial failure: mapping errors; valid records written, cursor unchanged | Check `rejections` in JSON, fix issues, retry |
| 2 | Hard failure: auth, lock, persistence, or recovery error | Follow the `Next:` line printed to stderr |

### JSON output fields (`--json`)

| Field | Type | Description |
|---|---|---|
| `mode` | string | `"full"` or `"incremental"` |
| `scope` | object | `{type, project[, keys]}` |
| `issues_read` | int | Raw issues fetched from Jira |
| `issues_mapped` | int | Issues successfully converted to records |
| `net_new_records` | int | Records actually appended (after dedup) |
| `placement` | string | Absolute path to the daily JSONL file |
| `checkpoint_result` | string | `advanced`, `unchanged`, `skipped` (--full), `error`, `recovered` |
| `rejections` | object | `{issue_key: reason}` for any failed mappings |
| `error_code` | string\|null | Short token; `null` on clean success |
| `exit_code` | int | 0, 1, or 2 (see table above) |

On error (exit_code ≥ 1) the object also includes `error`, `source`,
`recovery_action`, and the fields above.
