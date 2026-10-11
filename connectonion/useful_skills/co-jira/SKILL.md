---
name: co-jira
description: Work with a Jira instance through `co jira` — verify the connection, list projects, and read selected issues. Use when the user mentions Jira, a Jira issue key like ALPHA-1, "my tickets", "Jira projects", or wants to check their Jira account.
---

# co jira

`co jira` acts against the Jira instance named by `JIRA_URL` as the user
identified by `JIRA_EMAIL` and `JIRA_API_TOKEN`. It is **read-only**: it never
creates, edits, comments on, or deletes any Jira resource.

**Read the output, not just the exit code.** Every failure exits 1 and ends
with one `Next:` line or an error message; run that command, it is the fix.

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
(Settings → Security → API tokens). The `--secret` flag encrypts the stored value
in the configuration file. Use the no-echo procedure below so the real token is not
typed into shell history.
**Never paste the real token into chat, log output, or Git.**

```bash
# No-echo procedure — Git Bash / Linux / macOS
read -rsp "Jira API token: " JIRA_TOKEN; echo
co env set JIRA_API_TOKEN "$JIRA_TOKEN" --secret
unset JIRA_TOKEN
```

## Which command

| You want to | Run |
|---|---|
| Confirm credentials and show the authorised account | `co jira verify` |
| List all projects the account can access | `co jira projects` |
| Same, as JSON for scripting | `co jira projects --json` |
| Read specific issues from a project | `co jira issues ALPHA ALPHA-1 ALPHA-2` |
| Same, as JSON | `co jira issues ALPHA ALPHA-1 ALPHA-2 --json` |
| Collect issues and write source.yaml for co rem | `co jira sync ALPHA ALPHA-1 ALPHA-2` |
| Same, to a custom directory | `co jira sync ALPHA ALPHA-1 --output ~/my/dir` |

## The 80 % commands

```bash
co jira verify                                   # confirm connection; shows site URL + display name
co jira projects                                 # table: KEY  Name
co jira projects --json                          # {"projects": [{"key": "ALPHA", "name": "..."}, ...]}
co jira issues ALPHA ALPHA-1 ALPHA-3             # issue summaries for the named keys
co jira issues ALPHA ALPHA-1 ALPHA-3 --json      # {"issues": [...]}
co jira sync ALPHA ALPHA-1 ALPHA-3               # verify + collect + write ~/.co/rem/docs/jira/source.yaml
co jira sync ALPHA ALPHA-1 --json                # {"written": "...", "project": "ALPHA", "issues": 1}
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
