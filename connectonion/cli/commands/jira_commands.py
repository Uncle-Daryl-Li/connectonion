"""
Purpose: `co jira` — verify connection, list projects, read scoped issues and their history (F13FCAKE-9/10/12/18)
LLM-Note:
  Dependencies: imports from [sys, json, rich, useful_tools/jira.Jira] | imported by [cli/main.py] | tested by [tests/unit/test_jira.py]
  Data flow: CLI flags -> _jira() factory -> Jira.verify_connection()/project_items()/issue_items() -> formatted output or JSON
    sync: issue_items -> issue_changelogs (unless --no-history, F13FCAKE-12) -> jira_issues_to_records(changelogs=...) -> daily/ or exports/
  State/Effects: sync writes source.yaml and daily JSONL under the output dir; Jira itself is always read-only; all auth/scope validation in tool layer
  Errors: JiraConfigError/JiraAuthError/JiraPermissionError/ValueError -> stderr + exit 1; never exposes token | sync with items rejected by the shared record contract (F13FCAKE-16) still writes the valid records, lists every rejected item, and exits 1 | restricted or unavailable history is per item: the issue is still written, the reason is kept under extensions.changelog, exit stays 0
"""

import json
import sys
from pathlib import Path

from rich.console import Console

errors = Console(stderr=True)


def _jira():
    """Factory function — patchable in unit tests."""
    from ...useful_tools.jira import Jira
    return Jira()


def _handle_error(exc: Exception, as_json: bool) -> None:
    msg = str(exc).strip()
    if as_json:
        print(json.dumps({"error": msg, "exit_code": 1}))
    else:
        errors.print(msg, style="red", markup=False)
    sys.exit(1)


def handle_jira_verify(*, as_json: bool = False) -> None:
    """Verify Jira credentials and report the authorised account.

    JSON output: {"account": site_url, "account_id": ..., "display_name": ...}.
    Token and email are never included in any output.
    """
    from ...useful_tools.jira import JiraConfigError, JiraAuthError, JiraPermissionError
    try:
        info = _jira().verify_connection()
    except (JiraConfigError, JiraAuthError, JiraPermissionError, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    if as_json:
        print(json.dumps({
            "account": info.get("site_url", ""),
            "account_id": info.get("accountId", ""),
            "display_name": info.get("displayName", ""),
        }))
        return
    print(f"Connected to: {info.get('site_url', '')} ({info.get('displayName', '')})")
    print("Example:  co jira projects")
def handle_jira_projects(*, as_json: bool = False) -> None:
    """List all accessible Jira projects.

    JSON output: {"projects": [{"key": ..., "name": ...}, ...]}.
    Empty result: {"projects": []}.
    Errors: exit 1; JSON mode returns {"error": ..., "exit_code": 1}; no token in output.
    """
    from ...useful_tools.jira import JiraConfigError, JiraAuthError, JiraPermissionError
    try:
        projects = _jira().project_items()
    except (JiraConfigError, JiraAuthError, JiraPermissionError, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    if as_json:
        print(json.dumps({"projects": projects}))
        return
    if not projects:
        print("No Jira projects found.")
        print("Example: co env set JIRA_URL https://your-domain.atlassian.net")
        return
    for project in projects:
        print(f"{project.get('key', ''):<12}  {project.get('name', '')}")


def handle_jira_issues(
    project_key: str,
    issue_keys: list[str],
    *,
    as_json: bool = False,
) -> None:
    """Fetch Jira issues scoped to the given project and issue keys.

    Scope validation (empty set, key prefix, JQL injection) is enforced by the tool layer.
    JSON output: {"issues": [{"key": ..., "fields": {...}}, ...]}.
    """
    from ...useful_tools.jira import JiraConfigError, JiraAuthError, JiraPermissionError
    try:
        issues = _jira().issue_items(project_key, set(issue_keys))
    except (JiraConfigError, JiraAuthError, JiraPermissionError, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    if as_json:
        print(json.dumps({"issues": issues}))
        return
    if not issues:
        print(f"No issues found in {project_key}.")
        return
    for issue in issues:
        key = issue.get("key", "")
        summary = (issue.get("fields") or {}).get("summary", "")
        print(f"{key:<16}  {summary}")


def handle_jira_sync(
    project_key: str,
    issue_keys: list[str],
    *,
    output_dir: Path | None = None,
    as_json: bool = False,
    export_id: str | None = None,
    history: bool = True,
) -> None:
    """Validate credentials, collect selected issues and their history, write source.yaml and the records.

    Full workflow: verify → issue_items → issue_changelogs → write_source_yaml → jira_issues_to_records → placement.
    History (F13FCAKE-12): one extra read per issue, attached as extensions.changelog; history=False
    (--no-history) skips those reads and the records carry no changelog.
    Placement (F13FCAKE-11): ongoing sync → append_daily_jsonl (daily/YYYY/MM/DD.jsonl);
    export_id given → write_export (exports/<export_id>/ records.jsonl + manifest.json), a one-off import.
    An unusable export id is refused before any request or file write.
    Writes to output_dir (default: ~/.co/rem/docs/jira).
    JSON output: {"written": source_yaml_path, "placement": "daily" | "export", "jsonl": records file path,
    "project": key, "issues": collected,
    "mapped": records that passed the contract, "appended": records newly added to today's file
    (already-present records are skipped; for an export, every mapped record),
    "history": {"ok": n, "none": n, "restricted": n, "unavailable": n} or null with --no-history,
    "rejected": {"jira:<key>": reason}}.
    Rejected items never disappear silently: valid records are still written, the rejected ones are
    listed (stderr, or "rejected" in JSON), and the command exits 1.
    """
    from ...useful_tools.jira import JiraConfigError, JiraAuthError, JiraPermissionError
    from ...useful_tools.adapter import export_target, jira_issues_to_records
    if output_dir is None:
        output_dir = Path.home() / ".co" / "rem" / "docs" / "jira"
    try:
        if export_id is not None:
            export_target(output_dir, export_id)
        jira = _jira()
        info, issues, changelogs = _read_jira(jira, project_key, issue_keys, history)
        jira.write_source_yaml(output_dir, [project_key], set(issue_keys))
        adapter_result = jira_issues_to_records(issues, info["site_url"], changelogs=changelogs)
        jsonl_path, appended = _place_records(adapter_result.records, output_dir, export_id,
                                              {"projects": [project_key], "issues": sorted(set(issue_keys))})
    except (JiraConfigError, JiraAuthError, JiraPermissionError, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    source_yaml = str(output_dir / "source.yaml")
    rejected = adapter_result.errors
    counts = _history_counts(adapter_result.records) if history else None
    if as_json:
        print(json.dumps({
            "written": source_yaml,
            "placement": "export" if export_id else "daily",
            "jsonl": str(jsonl_path),
            "project": project_key,
            "issues": len(issues),
            "mapped": len(adapter_result.records),
            "appended": appended,
            "history": counts,
            "rejected": rejected,
        }))
    else:
        print(f"Wrote {source_yaml}")
        print(f"Wrote {jsonl_path}")
        print(f"Project: {project_key}  Issues collected: {len(issues)}  "
              f"Records mapped: {len(adapter_result.records)}  Records appended: {appended}")
        _print_history(counts)
        print("Example: co rem sources")
    _exit_if_rejected(rejected, as_json)


# Every Jira read happens before any file is written, so an auth failure while reading history writes nothing.
def _read_jira(jira, project_key: str, issue_keys: list[str], history: bool) -> tuple[dict, list, dict | None]:
    info = jira.verify_connection()
    issues = jira.issue_items(project_key, set(issue_keys))
    if not history:
        return info, issues, None
    keys = {issue["key"] for issue in issues if isinstance(issue, dict) and isinstance(issue.get("key"), str)}
    return info, issues, jira.issue_changelogs(keys)


# How many mapped records ended up with each history status (F13FCAKE-12).
def _history_counts(records: list[dict]) -> dict:
    from ...useful_tools.adapter import CHANGELOG_STATUSES
    statuses = [record["extensions"].get("changelog", {}).get("status") for record in records]
    return {status: statuses.count(status) for status in CHANGELOG_STATUSES}


def _print_history(counts: dict | None) -> None:
    if counts is None:
        print("History: not requested (--no-history)")
        return
    print("History: " + "  ".join(f"{status} {n}" for status, n in counts.items()))
    if counts["restricted"] or counts["unavailable"]:
        print("Issues whose history could not be read were still written; "
              "extensions.changelog.reason says why.")


# Ongoing sync appends to today's daily file; --export writes a one-off snapshot under exports/<id>/.
def _place_records(records: list[dict], output_dir: Path, export_id: str | None, scope: dict) -> tuple[Path, int]:
    from ...useful_tools.adapter import append_daily_jsonl, write_export
    if export_id is None:
        return append_daily_jsonl(records, output_dir)
    return write_export(records, output_dir, export_id, scope) / "records.jsonl", len(records)


# Mapping errors (F13FCAKE-16) must reach the user: list each rejected item, then exit 1.
def _exit_if_rejected(rejected: dict, as_json: bool) -> None:
    if not rejected:
        return
    if not as_json:
        errors.print(f"{len(rejected)} item(s) were rejected and not written:", style="red", markup=False)
        for label, reason in rejected.items():
            errors.print(f"  {label}: {reason}", markup=False)
    sys.exit(1)
