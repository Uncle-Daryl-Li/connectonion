"""
Purpose: `co jira` — verify connection, list projects, read scoped issues (F13FCAKE-9/10/18)
LLM-Note:
  Dependencies: imports from [sys, json, rich, useful_tools/jira.Jira] | imported by [cli/main.py] | tested by [tests/unit/test_jira.py]
  Data flow: CLI flags -> _jira() factory -> Jira.verify_connection()/project_items()/issue_items() -> formatted output or JSON
  State/Effects: sync writes source.yaml under the output dir; Jira itself is always read-only; all auth/scope validation in tool layer
  Errors: JiraConfigError/JiraAuthError/JiraPermissionError/ValueError -> stderr + exit 1; never exposes token
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
) -> None:
    """Validate credentials, collect selected issues, and write source.yaml.

    Full workflow: verify → issue_items → write_source_yaml.
    Daily JSONL conversion and shared-record persistence are provided by the
    F13FCAKE-18 adapter layer, not this scoped-selection command.
    Writes to output_dir (default: ~/.co/rem/docs/jira).
    JSON output: {"written": source_yaml_path, "issues": count, "project": key}.
    """
    from ...useful_tools.jira import JiraConfigError, JiraAuthError, JiraPermissionError
    if output_dir is None:
        output_dir = Path.home() / ".co" / "rem" / "docs" / "jira"
    try:
        jira = _jira()
        jira.verify_connection()
        issues = jira.issue_items(project_key, set(issue_keys))
        jira.write_source_yaml(output_dir, [project_key], set(issue_keys))
    except (JiraConfigError, JiraAuthError, JiraPermissionError, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    source_yaml = str(output_dir / "source.yaml")
    if as_json:
        print(json.dumps({
            "written": source_yaml,
            "project": project_key,
            "issues": len(issues),
        }))
        return
    print(f"Wrote {source_yaml}")
    print(f"Project: {project_key}  Issues collected: {len(issues)}")
    print("Example: co rem sources")
