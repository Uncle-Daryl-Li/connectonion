"""
Purpose: `co jira` — list Jira projects and issues from the terminal (F13FCAKE-9/10/18)
LLM-Note:
  Dependencies: imports from [sys, json, rich, useful_tools/jira.Jira] | imported by [cli/main.py] | tested by [tests/unit/test_jira.py]
  Data flow: CLI flags → _jira() factory → Jira.project_items() → formatted output or JSON
  State/Effects: read-only; no files written; all auth via tool layer
  Errors: JiraConfigError/JiraAuthError/JiraPermissionError/ValueError → stderr + exit 1; never exposes token
"""

import json
import sys

from rich.console import Console

errors = Console(stderr=True)


def _jira():
    """Factory function — patchable in unit tests."""
    from ...useful_tools.jira import Jira
    return Jira()


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
        msg = str(exc).strip()
        if as_json:
            print(json.dumps({"error": msg, "exit_code": 1}))
        else:
            errors.print(msg, style="red", markup=False)
        sys.exit(1)
    if as_json:
        print(json.dumps({"projects": projects}))
        return
    if not projects:
        print("No Jira projects found.")
        print("Example: co env set JIRA_URL https://your-domain.atlassian.net")
        return
    for project in projects:
        print(f"{project.get('key', ''):<12}  {project.get('name', '')}")
