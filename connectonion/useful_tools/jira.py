"""
Purpose: Jira Cloud integration — projects, issues, source.yaml for co rem (F13FCAKE-9/10/18).
LLM-Note:
  Dependencies: imports from [re, pathlib, datetime, importlib.metadata, httpx, yaml, connectonion.environment.setting] | imported by [useful_tools/__init__.py, cli/commands/jira_commands.py] | tested by [tests/unit/test_jira.py]
  Data flow: Jira methods → HTTP Basic Auth (JIRA_EMAIL:JIRA_API_TOKEN) httpx requests to JIRA_URL → raw Jira REST API v3 JSON → returned as plain dicts/lists
  State/Effects: reads JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN from environment | write_source_yaml() creates source.yaml under source_dir | no blobs or derived files (metadata_only, v1)
  Integration: exposes Jira with verify_connection(), project_items(), issue_items(), write_source_yaml() | CLI via cli/commands/jira_commands.py
  Errors: JiraConfigError (missing env var) | JiraAuthError (HTTP 401) | JiraPermissionError (HTTP 403) — all ValueError subclasses; never swallow; never put token in messages

Jira Cloud tool: list projects, collect issues, write source.yaml for co rem.

Usage:
    from connectonion import Jira
    jira = Jira()
    projects = jira.project_items()
    issues = jira.issue_items("ALPHA", {"ALPHA-1", "ALPHA-2"})
"""

import datetime
import importlib.metadata
import re
from pathlib import Path

import httpx
import yaml

from ..environment import setting

class JiraConfigError(ValueError):
    """Local configuration missing or invalid (JIRA_URL / JIRA_EMAIL / JIRA_API_TOKEN)."""

class JiraAuthError(ValueError):
    """Jira returned HTTP 401 — authentication failed."""

class JiraPermissionError(ValueError):
    """Jira returned HTTP 403 — permission denied for the requested resource."""

_KEY_PATTERN = re.compile(r'^[A-Z][A-Z0-9]*-\d+$')

_MAX_PAGES = 50

class Jira:
    """Read-only Jira Cloud integration: project listing, issue collection, source.yaml."""

    def __init__(self):
        url = (setting("JIRA_URL") or "").rstrip("/")
        email = (setting("JIRA_EMAIL") or "")
        token = (setting("JIRA_API_TOKEN") or "")
        if not url:
            raise JiraConfigError(
                "JIRA_URL is not set.\n"
                "Repair: co env set JIRA_URL https://your-domain.atlassian.net"
            )
        if not email:
            raise JiraConfigError(
                "JIRA_EMAIL is not set.\n"
                "Repair: co env set JIRA_EMAIL your@email.com"
            )
        if not token:
            raise JiraConfigError(
                "JIRA_API_TOKEN is not set.\n"
                "Repair: co env set JIRA_API_TOKEN <your-api-token>"
            )
        self._base = url
        self._auth = (email, token)

    def _get(self, path: str, **params) -> dict:
        """Authenticated GET to /rest/api/3/<path>; raises typed errors for 401/403/429."""
        url = f"{self._base}/rest/api/3/{path.lstrip('/')}"
        try:
            response = httpx.get(url, auth=self._auth, params=params, timeout=30)
        except httpx.TimeoutException as exc:
            raise ValueError(
                f"Request to Jira timed out. "
                "Check your network connection and try again."
            ) from exc
        if response.status_code == 401:
            raise JiraAuthError(
                "Jira authentication failed. "
                "Repair: co env set JIRA_API_TOKEN <your-api-token>"
            )
        if response.status_code == 403:
            raise JiraPermissionError(
                "Jira returned 403. "
                "Check that your API token has read access."
            )
        if response.status_code == 429:
            raise ValueError(
                "Jira rate limit exceeded (HTTP 429). "
                "Wait a moment and try again."
            )
        response.raise_for_status()
        return response.json()

    def verify_connection(self) -> dict:
        """GET /rest/api/3/myself — confirms credentials; returns accountId, displayName, site_url.

        Token does not appear in the returned dict, logs, or any file written to disk.
        emailAddress is included only when Jira returns it (privacy settings may omit it).
        """
        data = self._get("myself")
        result: dict = {
            "accountId": data["accountId"],
            "displayName": data["displayName"],
            "site_url": self._base,
        }
        if "emailAddress" in data:
            result["emailAddress"] = data["emailAddress"]
        return result

    def project_items(self) -> list[dict]:
        """GET /rest/api/3/project/search — all accessible projects, offset-based pagination.

        Pagination: startAt / isLast / values (confirmed in Step 0).
        Empty values with isLast=false → raises ValueError (non-progressing page guard).
        """
        results: list[dict] = []
        start_at = 0
        for _ in range(_MAX_PAGES):
            page = self._get("project/search", startAt=start_at, maxResults=50)
            values: list[dict] = page.get("values", [])
            is_last: bool = page.get("isLast", True)
            if not values and not is_last:
                raise ValueError(
                    f"Jira project pagination returned an empty page with isLast=false "
                    f"(startAt={start_at}). Aborting to prevent an infinite loop."
                )
            results.extend(values)
            if is_last:
                return results
            start_at += len(values)
        raise ValueError(
            f"Jira project pagination exceeded {_MAX_PAGES} pages without reaching isLast=true. "
            "Aborting to prevent an infinite loop."
        )

    def issue_items(
        self,
        project_key: str,
        selected_issue_keys: set[str],
        *,
        max_results: int = 50,
        accessible_keys: set[str] | None = None,
    ) -> list[dict]:
        """GET /rest/api/3/search/jql — selected issues, cursor-based pagination.

        Pagination: nextPageToken (absent = last page); startAt is silently ignored by server.
        JQL is server-scoped: project = project_key AND key IN (selected_issue_keys).

        Raises ValueError before any HTTP request when:
        - selected_issue_keys is empty
        - any key's prefix does not match project_key
        - any key contains JQL metacharacters (single quote, space, JQL keywords, etc.)
        - accessible_keys provided and project_key not in it

        Repeated nextPageToken → raises ValueError (non-progressing cursor guard).
        HTTP 403 → raises JiraPermissionError containing the project key, never the server body.
        """
        if not selected_issue_keys:
            raise ValueError(
                "issue_items() requires a non-empty set of issue keys. "
                "Pass at least one key, e.g. issue_items('ALPHA', {'ALPHA-1'})."
            )
        for key in selected_issue_keys:
            if not _KEY_PATTERN.match(key):
                raise ValueError(
                    f"Invalid Jira issue key {key!r}. "
                    "Keys must match [A-Z][A-Z0-9]*-<digits> (e.g. ALPHA-1). "
                    "No spaces, quotes, or JQL metacharacters are allowed."
                )
            if not key.startswith(project_key + "-"):
                raise ValueError(
                    f"Issue key {key!r} does not belong to project {project_key!r}. "
                    "All keys in selected_issue_keys must be prefixed with the project key."
                )
        if accessible_keys is not None and project_key not in accessible_keys:
            raise ValueError(
                f"Project {project_key!r} is not in accessible_keys {accessible_keys!r}. "
                "No HTTP request was made."
            )

        keys_clause = ", ".join(sorted(selected_issue_keys))
        jql = f"project = {project_key} AND key IN ({keys_clause})"

        results: list[dict] = []
        next_token: str | None = None
        seen_tokens: set[str] = set()

        for _ in range(_MAX_PAGES):
            params: dict = {"jql": jql, "maxResults": max_results}
            if next_token is not None:
                params["nextPageToken"] = next_token
            try:
                page = self._get("search/jql", **params)
            except JiraPermissionError:
                raise JiraPermissionError(
                    f"Jira returned 403 for project {project_key!r}. "
                    "Check that your API token has read access to this project."
                ) from None
            results.extend(page.get("issues", []))
            next_token = page.get("nextPageToken")
            if next_token is None:
                return results
            if next_token in seen_tokens:
                raise ValueError(
                    f"Jira issue pagination cursor did not advance "
                    f"(repeated nextPageToken={next_token!r}). "
                    "Aborting to prevent an infinite loop."
                )
            seen_tokens.add(next_token)

        raise ValueError(
            f"Jira issue pagination exceeded {_MAX_PAGES} pages without exhausting nextPageToken. "
            "Aborting to prevent an infinite loop."
        )

    def write_source_yaml(
        self,
        source_dir: Path,
        project_keys: list[str],
        selected_issue_keys: set[str],
    ) -> None:
        """Write source_dir/source.yaml with scope, account, retention, and validated_with.

        Raises ValueError if selected_issue_keys is empty or any key does not belong to
        a listed project (prefix check). No file is written on validation failure.
        Token and email never appear in the written file.
        """
        if not selected_issue_keys:
            raise ValueError("selected_issue_keys must not be empty.")
        for key in selected_issue_keys:
            prefix = key.split("-")[0] if "-" in key else ""
            if prefix not in project_keys:
                raise ValueError(
                    f"Issue key {key!r} does not belong to any listed project {project_keys!r}. "
                    "No file was written."
                )
        source_dir = Path(source_dir)
        source_dir.mkdir(parents=True, exist_ok=True)
        version = importlib.metadata.version("connectonion")
        doc = {
            "driver": "jira",
            "account": self._base,
            "scope": {
                "projects": list(project_keys),
                "issues": sorted(selected_issue_keys),
            },
            "retention": "90d",
            "media_policy": "metadata_only",
            "validated_with": {
                "connectonion_version": version,
                "command": "co rem sources",
                "path": str(source_dir / "source.yaml"),
                "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            },
        }
        target = source_dir / "source.yaml"
        with open(target, "w", encoding="utf-8") as fh:
            yaml.safe_dump(doc, fh, allow_unicode=True, sort_keys=False)
