"""Jira tool unit tests — F13FCAKE-9, F13FCAKE-10, F13FCAKE-18.

Step 1 (this file): tests 10-2, 10-3, 10-4, 10-4b, 10-4c — pagination safety.
Later steps will extend this file with the remaining 49 tests.
"""

import os
from unittest.mock import MagicMock, patch

import pytest

from connectonion.useful_tools.jira import (
    Jira,
    JiraAuthError,
    JiraConfigError,
    JiraPermissionError,
)

JIRA_URL = "https://test.atlassian.net"
JIRA_EMAIL = "test@example.com"
JIRA_TOKEN = "test-api-token-do-not-log"

@pytest.fixture(autouse=True)
def jira_env(monkeypatch):
    """Provide valid Jira env vars for every test; use monkeypatch for per-test overrides."""
    monkeypatch.setenv("JIRA_URL", JIRA_URL)
    monkeypatch.setenv("JIRA_EMAIL", JIRA_EMAIL)
    monkeypatch.setenv("JIRA_API_TOKEN", JIRA_TOKEN)

def _mock_get(routes: dict):
    """
    Return (get_fn, calls) where get_fn is a drop-in for httpx.get.

    routes: {url_suffix: (http_status, json_body)}
    url_suffix is matched against url after stripping query-string.
    calls: list of {"url": ..., "params": ...} appended on each invocation.
    """
    calls: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        for suffix, (status, body) in routes.items():
            if url.rstrip("/").endswith(suffix):
                resp = MagicMock(status_code=status)
                resp.json = MagicMock(return_value=body)
                resp.raise_for_status = MagicMock()
                return resp
        raise AssertionError(f"Unexpected GET {url!r} params={params!r}")

    return get, calls

_P1 = {"key": "ALPHA", "name": "Alpha Project"}
_P2 = {"key": "BETA", "name": "Beta Project"}
_P3 = {"key": "GAMMA", "name": "Gamma Project"}

_I1 = {"id": "10001", "key": "ALPHA-1", "fields": {"summary": "First issue"}}
_I2 = {"id": "10002", "key": "ALPHA-2", "fields": {"summary": "Second issue"}}
_I3 = {"id": "10003", "key": "ALPHA-3", "fields": {"summary": "Third issue"}}

def test_project_items_follows_pagination_two_pages():
    """10-2: two pages → combined list; exactly two HTTP requests."""
    call_seq = [0]

    def get(url, auth=None, params=None, timeout=None):
        call_seq[0] += 1
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        if call_seq[0] == 1:
            resp.json = MagicMock(return_value={"values": [_P1, _P2], "isLast": False})
        else:
            resp.json = MagicMock(return_value={"values": [_P3], "isLast": True})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().project_items()

    assert result == [_P1, _P2, _P3]
    assert call_seq[0] == 2

def test_project_items_second_page_startAt_equals_first_page_length():
    """10-3: second request startAt equals the number of items on page 1."""
    received: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        received.append(dict(params or {}))
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        if len(received) == 1:
            resp.json = MagicMock(return_value={"values": [_P1, _P2], "isLast": False})
        else:
            resp.json = MagicMock(return_value={"values": [_P3], "isLast": True})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        Jira().project_items()

    assert len(received) == 2
    assert received[1].get("startAt") == 2

def test_project_items_single_page_sends_exactly_one_request():
    """10-4: isLast=true on the only page → exactly one HTTP request."""
    get, calls = _mock_get(
        {"/rest/api/3/project/search": (200, {"values": [_P1], "isLast": True})}
    )

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().project_items()

    assert result == [_P1]
    assert len(calls) == 1

def test_project_items_stops_safely_on_non_progressing_page():
    """10-4b: values=[] + isLast=false → raises ValueError; HTTP calls bounded."""
    call_count = [0]

    def get(url, auth=None, params=None, timeout=None):
        call_count[0] += 1
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"values": [], "isLast": False})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError, match="empty page"):
            Jira().project_items()

    assert call_count[0] <= 5

def test_issue_items_stops_safely_on_repeated_next_page_token():
    """10-4c: non-progressing cursor → raises ValueError; HTTP calls bounded.

    Also verifies that the cursor is correctly echoed back to Jira on each
    subsequent request (params["nextPageToken"] == FIXED_TOKEN on call 2+).
    """
    FIXED_TOKEN = "cursor-that-never-advances"
    received_params: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        received_params.append(dict(params or {}))
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={
            "issues": [_I1],
            "nextPageToken": FIXED_TOKEN,
        })
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError, match="cursor did not advance"):
            Jira().issue_items("ALPHA", {"ALPHA-1"})

    assert len(received_params) <= 5

    assert received_params[1].get("nextPageToken") == FIXED_TOKEN
