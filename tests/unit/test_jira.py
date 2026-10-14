"""Jira tool unit tests — F13FCAKE-9, F13FCAKE-10, F13FCAKE-18."""

import datetime
import httpx
import json
import yaml
import logging
from pathlib import Path
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

def test_valid_credentials_identify_authorized_site_without_persisting_token():
    """9-0: verify_connection() returns accountId + site_url; token absent from result and disk."""
    get, _ = _mock_get({"/rest/api/3/myself": (200, {"accountId": "abc123", "displayName": "Alice"})})

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().verify_connection()

    assert result["accountId"] == "abc123"
    assert result["site_url"] == JIRA_URL
    for v in result.values():
        assert JIRA_TOKEN not in str(v)

    for f in Path.home().rglob("*"):
        if f.is_file():
            assert JIRA_TOKEN not in f.read_text(encoding="utf-8", errors="ignore")


def test_missing_jira_url_raises_config_error(monkeypatch):
    """9-1: all three vars absent → JiraConfigError naming JIRA_URL with repair hint."""
    monkeypatch.delenv("JIRA_URL")
    monkeypatch.delenv("JIRA_EMAIL")
    monkeypatch.delenv("JIRA_API_TOKEN")
    with pytest.raises(JiraConfigError, match="JIRA_URL") as exc_info:
        Jira()
    assert "co env set JIRA_URL" in str(exc_info.value)


def test_missing_email_raises_config_error(monkeypatch):
    """9-2: JIRA_URL set, JIRA_EMAIL absent → JiraConfigError naming JIRA_EMAIL."""
    monkeypatch.delenv("JIRA_EMAIL")
    with pytest.raises(JiraConfigError, match="JIRA_EMAIL"):
        Jira()


def test_missing_token_raises_config_error(monkeypatch):
    """9-3: JIRA_URL + JIRA_EMAIL set, JIRA_API_TOKEN absent → JiraConfigError naming JIRA_API_TOKEN."""
    monkeypatch.delenv("JIRA_API_TOKEN")
    with pytest.raises(JiraConfigError, match="JIRA_API_TOKEN"):
        Jira()


def test_invalid_credentials_raise_auth_error_with_standard_message():
    """9-4: 401 → JiraAuthError; message contains 'Jira authentication failed'; no speculation."""
    get, _ = _mock_get({"/rest/api/3/myself": (401, {})})

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraAuthError) as exc_info:
            Jira().verify_connection()

    msg = str(exc_info.value)
    assert "Jira authentication failed" in msg
    assert "expired" not in msg
    assert "invalid token" not in msg
    assert "过期" not in msg


def test_401_message_does_not_speculate_about_cause():
    """9-5: same 401 path; no speculation words; repair hint present."""
    get, _ = _mock_get({"/rest/api/3/myself": (401, {})})

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraAuthError) as exc_info:
            Jira().verify_connection()

    msg = str(exc_info.value)
    assert "expired" not in msg
    assert "invalid token" not in msg
    assert "export JIRA_API_TOKEN" in msg


def test_no_records_written_when_auth_fails(caplog):
    """9-6: 401 via project_items() → no .jsonl/.yaml under HOME; token absent from all logs."""
    get, _ = _mock_get({"/rest/api/3/project/search": (401, {})})

    with caplog.at_level(logging.NOTSET):
        with patch("connectonion.useful_tools.jira.httpx.get", get):
            with pytest.raises(JiraAuthError):
                Jira().project_items()

    home = Path.home()
    assert list(home.rglob("*.jsonl")) == []
    assert list(home.rglob("*.yaml")) == []

    for record in caplog.records:
        assert JIRA_TOKEN not in record.getMessage()


def test_network_timeout_raises_recoverable_value_error_with_retry_hint():
    """9-7: httpx.TimeoutException → ValueError (not JiraAuthError/JiraPermissionError); message has retry hint."""
    def get(url, auth=None, params=None, timeout=None):
        raise httpx.TimeoutException('timed out', request=None)

    with patch('connectonion.useful_tools.jira.httpx.get', get):
        with pytest.raises(ValueError) as exc_info:
            Jira().project_items()

    assert not isinstance(exc_info.value, JiraAuthError)
    assert not isinstance(exc_info.value, JiraPermissionError)
    msg = str(exc_info.value)
    assert 'try again' in msg.lower() or 'retry' in msg.lower() or 'timed out' in msg.lower()


def test_no_mutating_requests_sent():
    """9-8: no PUT/PATCH/DELETE ever; no POST to mutation endpoints.

    POST is not blanket-banned: future read-only queries via POST (e.g. /search/jql)
    are allowed.  Only POSTs that mutate Jira data (/issue, /comment, /transition,
    /edit, /create) are forbidden.
    """
    _MUTATION_FRAGMENTS = ("/issue", "/comment", "/transition", "/edit", "/create")

    get, _ = _mock_get({
        '/rest/api/3/project/search': (200, {'values': [_P1], 'isLast': True}),
        '/rest/api/3/search/jql': (200, {'issues': [_I1]}),
    })

    post_urls: list[str] = []

    def _record_post(url, **kwargs):
        post_urls.append(url)
        resp = MagicMock(status_code=200)
        resp.json = MagicMock(return_value={})
        resp.raise_for_status = MagicMock()
        return resp

    with (
        patch('connectonion.useful_tools.jira.httpx.get', get),
        patch('connectonion.useful_tools.jira.httpx.post', _record_post),
        patch('connectonion.useful_tools.jira.httpx.put') as mock_put,
        patch('connectonion.useful_tools.jira.httpx.patch') as mock_patch,
        patch('connectonion.useful_tools.jira.httpx.delete') as mock_delete,
    ):
        j = Jira()
        j.project_items()
        j.issue_items('ALPHA', {'ALPHA-1'})

    mock_put.assert_not_called()
    mock_patch.assert_not_called()
    mock_delete.assert_not_called()
    for url in post_urls:
        for fragment in _MUTATION_FRAGMENTS:
            assert fragment not in url, f"POST to mutation endpoint detected: {url!r}"


def test_rate_limit_returns_recoverable_error_or_bounded_retry():
    """9-9: HTTP 429 → ValueError with rate-limit message; never returns []; HTTP calls bounded (≤ 5)."""
    call_count = [0]

    def get(url, auth=None, params=None, timeout=None):
        call_count[0] += 1
        resp = MagicMock(status_code=429)
        resp.json = MagicMock(return_value={})
        resp.raise_for_status = MagicMock()
        return resp

    with patch('connectonion.useful_tools.jira.httpx.get', get):
        with pytest.raises(ValueError) as exc_info:
            Jira().project_items()

    assert call_count[0] <= 5
    msg = str(exc_info.value)
    assert '429' in msg or 'rate limit' in msg.lower() or 'rate-limit' in msg.lower()

# ── STEP 4 TESTS (10-1, 10-5 through 10-15) ──

def test_project_items_return_type_is_list_of_dict():
    """10-1: project_items() returns list[dict]; each dict contains 'key' and 'name'."""
    get, _ = _mock_get(
        {"/rest/api/3/project/search": (200, {"values": [_P1, _P2], "isLast": True})}
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().project_items()
    assert isinstance(result, list)
    for item in result:
        assert isinstance(item, dict)
        assert "key" in item
        assert "name" in item


def test_project_items_valid_empty_result_returns_empty_list():
    """10-5: values=[] + isLast=true -> returns []; no exception raised."""
    get, _ = _mock_get(
        {"/rest/api/3/project/search": (200, {"values": [], "isLast": True})}
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().project_items()
    assert result == []


def test_issue_items_raises_before_network_when_no_issue_keys_selected():
    """10-6: empty selected_issue_keys -> ValueError before any HTTP request (count == 0)."""
    call_count = [0]

    def get(url, **kwargs):
        call_count[0] += 1
        raise AssertionError("HTTP should not be called for empty key set")

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError, match="non-empty"):
            Jira().issue_items("ALPHA", set())
    assert call_count[0] == 0


def test_issue_items_rejects_unknown_project_key_before_collection():
    """10-6b: project not in accessible_keys -> ValueError, 0 HTTP requests."""
    call_count = [0]

    def get(url, **kwargs):
        call_count[0] += 1
        raise AssertionError("HTTP should not be called when project not accessible")

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError):
            Jira().issue_items(
                "GAMMA", {"GAMMA-1"},
                accessible_keys={"ALPHA", "BETA"},
            )
    assert call_count[0] == 0


def test_issue_items_jql_query_is_scoped_server_side_not_post_filtered():
    """10-6c: JQL sent to Jira scopes server-side: 'project = ALPHA AND key IN (...)'."""
    received_params: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        received_params.append(dict(params or {}))
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"issues": [_I1, _I2]})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().issue_items("ALPHA", {"ALPHA-1", "ALPHA-2"})

    assert len(received_params) == 1
    jql = received_params[0].get("jql", "")
    assert "project = ALPHA" in jql
    assert "ALPHA-1" in jql
    assert "ALPHA-2" in jql
    assert result == [_I1, _I2]


def test_issue_items_sends_explicit_fields_param():
    """10-6c-fields: issue_items() sends fields=<comma-list> to search/jql; contains required fields."""
    received_params: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        received_params.append(dict(params or {}))
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"issues": [_I1]})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        Jira().issue_items("ALPHA", {"ALPHA-1"})

    assert len(received_params) == 1
    fields_param = received_params[0].get("fields", "")
    assert fields_param, "fields param must be present and non-empty"
    field_names = [f.strip() for f in fields_param.split(",")]
    for required in ("summary", "description", "status", "created", "updated"):
        assert required in field_names, f"required field {required!r} missing from fields param"


def test_issue_items_realistic_response_shape_with_fields_succeeds():
    """10-6c-shape: server returns realistic shape (expand/fields/id/key/self) -> key check passes."""
    realistic_issue = {
        "expand": "operations,versionedRepresentations,editmeta,changelog,renderedFields",
        "id": "10001",
        "key": "ALPHA-1",
        "self": "https://example.atlassian.net/rest/api/3/issue/10001",
        "fields": {
            "summary": "Realistic issue summary",
            "description": None,
            "status": {"name": "To Do", "id": "1"},
            "created": "2024-01-15T09:00:00.000+0000",
            "updated": "2024-01-16T12:00:00.000+0000",
        },
    }

    def get(url, auth=None, params=None, timeout=None):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"issues": [realistic_issue]})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().issue_items("ALPHA", {"ALPHA-1"})

    assert len(result) == 1
    assert result[0]["key"] == "ALPHA-1"
    assert result[0]["fields"]["summary"] == "Realistic issue summary"


def test_issue_items_rejects_key_from_wrong_project_before_network():
    """10-6d: BETA-1 passed to ALPHA project -> ValueError, 0 HTTP; message names the key."""
    call_count = [0]

    def get(url, **kwargs):
        call_count[0] += 1
        raise AssertionError("HTTP should not be called for mismatched project key")

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError) as exc_info:
            Jira().issue_items("ALPHA", {"BETA-1"})
    assert call_count[0] == 0
    assert "BETA-1" in str(exc_info.value)


def test_issue_items_rejects_malicious_key_to_prevent_jql_injection():
    """10-6e: keys with quotes/spaces/AND -> ValueError, 0 HTTP requests each."""
    malicious = [
        {"ALPHA-1' OR project = EVIL"},
        {"ALPHA 1"},
        {"ALPHA-1 AND project=EVIL"},
    ]
    for key_set in malicious:
        count = [0]

        def _track(url, **kwargs):
            count[0] += 1
            raise AssertionError(f"HTTP called for malicious key {key_set!r}")

        with patch("connectonion.useful_tools.jira.httpx.get", _track):
            with pytest.raises(ValueError):
                Jira().issue_items("ALPHA", key_set)
        assert count[0] == 0, f"HTTP was called for {key_set!r}"


def test_issue_items_raises_permission_error_on_403_with_safe_message():
    """10-7: HTTP 403 -> JiraPermissionError; message contains project key; no server body."""
    get, _ = _mock_get(
        {"/rest/api/3/search/jql": (403, {"errorMessages": ["Access denied"]})}
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraPermissionError) as exc_info:
            Jira().issue_items("ALPHA", {"ALPHA-1"})
    msg = str(exc_info.value)
    assert "ALPHA" in msg
    assert "Access denied" not in msg
    assert "errorMessages" not in msg


def test_issue_items_pagination_collects_all_pages_via_confirmed_cursor():
    """10-8: two-page nextPageToken cursor -> all issues combined; 2 HTTP requests."""
    call_seq = [0]

    def get(url, auth=None, params=None, timeout=None):
        call_seq[0] += 1
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        if call_seq[0] == 1:
            resp.json = MagicMock(return_value={
                "issues": [_I1, _I2],
                "nextPageToken": "page-2-token",
            })
        else:
            resp.json = MagicMock(return_value={"issues": [_I3]})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().issue_items("ALPHA", {"ALPHA-1", "ALPHA-2", "ALPHA-3"})

    assert result == [_I1, _I2, _I3]
    assert call_seq[0] == 2


def test_issue_items_raises_when_all_requested_keys_missing():
    """10-9: issues=[] when keys are requested -> ValueError naming missing keys."""
    get, _ = _mock_get(
        {"/rest/api/3/search/jql": (200, {"issues": []})}
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError, match="ALPHA-1"):
            Jira().issue_items("ALPHA", {"ALPHA-1"})


def test_write_source_yaml_creates_file_at_correct_path(tmp_path):
    """10-10: source.yaml created at correct path; parses as valid YAML."""
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1"})
    yaml_file = dest / "source.yaml"
    assert yaml_file.exists()
    content = yaml.safe_load(yaml_file.read_text(encoding="utf-8"))
    assert isinstance(content, dict)


def test_write_source_yaml_required_fields_present(tmp_path):
    """10-11: all required YAML fields; media_policy=metadata_only; no 'all' in scope.issues."""
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1", "ALPHA-2"})
    content = yaml.safe_load((dest / "source.yaml").read_text(encoding="utf-8"))
    assert content["driver"] == "jira"
    assert content["account"] == JIRA_URL
    assert content["retention"] == "90d"
    assert content["media_policy"] == "metadata_only"
    assert content["scope"]["projects"] == ["ALPHA"]
    assert sorted(content["scope"]["issues"]) == ["ALPHA-1", "ALPHA-2"]
    assert "all" not in content["scope"]["issues"]
    assert "validated_with" in content


def test_write_source_yaml_validated_with_fields(tmp_path):
    """10-12: validated_with: connectonion_version mocked to 9.9.9; command/path/timestamp present."""
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    with patch("connectonion.useful_tools.jira.importlib.metadata.version", return_value="9.9.9"):
        Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1"})
    vw = yaml.safe_load((dest / "source.yaml").read_text(encoding="utf-8"))["validated_with"]
    assert vw["connectonion_version"] == "9.9.9"
    assert vw["command"].startswith("co ")
    assert "path" in vw
    datetime.datetime.fromisoformat(vw["timestamp"])


def test_write_source_yaml_excludes_secrets_from_yaml_and_fixture_directory(tmp_path, monkeypatch):
    """10-13: JIRA_API_TOKEN and JIRA_EMAIL absent from all files written under tmp_path."""
    _TOKEN = "ultra-secret-token-xyzzy"
    _EMAIL = "secretuser@example.org"
    monkeypatch.setenv("JIRA_API_TOKEN", _TOKEN)
    monkeypatch.setenv("JIRA_EMAIL", _EMAIL)
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1"})
    for f in tmp_path.rglob("*"):
        if f.is_file():
            text = f.read_text(encoding="utf-8", errors="ignore")
            assert _TOKEN not in text, f"Token found in {f}"
            assert _EMAIL not in text, f"Email found in {f}"


def test_project_items_pagination_stable_keys_across_pages():
    """10-14: multi-page result; all returned project dicts have distinct 'key' values."""
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

    keys = [item["key"] for item in result]
    assert len(keys) == len(set(keys))


def test_write_source_yaml_rejects_issue_keys_not_belonging_to_project(tmp_path):
    """10-15: BETA-2 not in project ALPHA -> ValueError; no source.yaml written."""
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    with pytest.raises(ValueError):
        Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1", "BETA-2"})
    assert not (dest / "source.yaml").exists()


def test_jira_cli_sync_writes_selected_scope_to_source_yaml(tmp_path):
    """10-16: co jira sync verifies, collects selected issues, and writes source.yaml only."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app

    def get(url, auth=None, params=None, timeout=None):
        response = MagicMock(status_code=200)
        response.raise_for_status = MagicMock()
        if url.endswith("/myself"):
            response.json = MagicMock(return_value={
                "accountId": "abc123", "displayName": "Alice",
            })
        elif url.endswith("/search/jql"):
            response.json = MagicMock(return_value={"issues": [_I1]})
        elif url.endswith("/issue/ALPHA-1/changelog"):  # history is read by default since F13FCAKE-12
            response.json = MagicMock(return_value={"values": [], "isLast": True})
        else:
            raise AssertionError(f"Unexpected GET {url!r}")
        return response

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = CliRunner().invoke(
            app, ["jira", "sync", "ALPHA", "ALPHA-1", "--output", str(tmp_path), "--json"]
        )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["written"] == str(tmp_path / "source.yaml")
    assert payload["project"] == "ALPHA"
    assert payload["issues"] == 1
    assert Path(payload["jsonl"]).exists()
    source = yaml.safe_load((tmp_path / "source.yaml").read_text(encoding="utf-8"))
    assert source["scope"] == {"projects": ["ALPHA"], "issues": ["ALPHA-1"]}
# ── STEP 5 TESTS (18-1 through 18-10, 18-6b, 18-6c, 18-7b, 18-8b, 18-8c) ──

_ONENOTE_ACCOUNT = "https://graph.microsoft.com/v1.0"
_ON1 = {"id": "page-1", "lastModifiedDateTime": "2024-01-01T00:00:00Z", "title": "Page One"}
_ON2 = {"id": "page-2", "lastModifiedDateTime": "2024-01-02T00:00:00Z", "title": "Page Two"}
_JIRA_URL_2 = "https://other.atlassian.net"


def test_adapter_result_has_records_and_errors_fields():
    """18-1: AdapterResult has records: list[dict] and errors: dict[str, str]."""
    from connectonion.useful_tools.adapter import AdapterResult
    result = AdapterResult()
    assert isinstance(result.records, list)
    assert isinstance(result.errors, dict)
    result2 = AdapterResult(records=[{"id": "x"}], errors={"k": "v"})
    assert result2.records == [{"id": "x"}]
    assert result2.errors == {"k": "v"}


def test_jira_adapter_returns_contract_shape_with_provenance():
    """18-2: jira_issues_to_records produces AdapterResult with source/account/id/url and timestamps."""
    from connectonion.useful_tools.adapter import AdapterResult, jira_issues_to_records
    result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    assert isinstance(result, AdapterResult)
    assert len(result.records) == 2
    for record in result.records:
        assert record["source"] == "jira"
        assert record["account"] == JIRA_URL
        assert "id" in record
        assert "url" in record
        assert "updated" in record and "fetched" in record


def test_onenote_adapter_returns_same_contract_shape_with_provenance():
    """18-3: onenote_pages_to_records has identical provenance contract to jira_issues_to_records."""
    from connectonion.useful_tools.adapter import AdapterResult, onenote_pages_to_records
    result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    assert isinstance(result, AdapterResult)
    assert len(result.records) == 2
    for record in result.records:
        assert record["source"] == "onenote"
        assert record["account"] == _ONENOTE_ACCOUNT
        assert "id" in record
        assert "url" in record
        assert "updated" in record and "fetched" in record


def test_common_consumer_reads_jira_without_source_specific_branching():
    """18-4: consume_records() is source-agnostic; works with Jira AdapterResult."""
    from connectonion.useful_tools.adapter import consume_records, jira_issues_to_records
    result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    records = consume_records(result)
    assert isinstance(records, list)
    assert len(records) == 2
    assert all(r["source"] == "jira" for r in records)


def test_common_consumer_reads_onenote_without_source_specific_branching():
    """18-5: same consume_records() function accepts OneNote AdapterResult."""
    from connectonion.useful_tools.adapter import consume_records, onenote_pages_to_records
    result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    records = consume_records(result)
    assert isinstance(records, list)
    assert len(records) == 2
    assert all(r["source"] == "onenote" for r in records)


def test_jira_failure_does_not_corrupt_onenote_records():
    """18-6: Jira failure (errors dict non-empty) leaves OneNote records intact and readable."""
    from connectonion.useful_tools.adapter import AdapterResult, consume_records, onenote_pages_to_records
    jira_result = AdapterResult(records=[], errors={"ALPHA": "Permission denied"})
    onenote_result = onenote_pages_to_records([_ON1], _ONENOTE_ACCOUNT)
    onenote_records = consume_records(onenote_result)
    assert len(onenote_records) == 1
    assert onenote_records[0]["source"] == "onenote"
    assert "ALPHA" in jira_result.errors
    assert len(consume_records(jira_result)) == 0


def test_jira_onenote_same_remote_id_no_collision():
    """18-6b: id=ABC-123 from Jira and OneNote -> two distinct records differing by source field."""
    from connectonion.useful_tools.adapter import consume_records, jira_issues_to_records, onenote_pages_to_records
    _issue = {"id": "99999", "key": "ABC-123", "fields": {"summary": "Shared ID"}}
    _page = {"id": "ABC-123", "lastModifiedDateTime": "2024-01-01", "title": "Same ID"}
    jira_result = jira_issues_to_records([_issue], JIRA_URL)
    onenote_result = onenote_pages_to_records([_page], _ONENOTE_ACCOUNT)
    all_records = consume_records(jira_result) + consume_records(onenote_result)
    assert len(all_records) == 2
    sources = {r["source"] for r in all_records}
    assert sources == {"jira", "onenote"}
    assert sum(1 for r in all_records if r["external_id"] == "ABC-123") == 2
    assert len({r["id"] for r in all_records}) == 2


def test_two_jira_accounts_same_issue_key_no_collision():
    """18-6c: id=ABC-123 from two Jira instances (different account) -> distinct by account field."""
    from connectonion.useful_tools.adapter import consume_records, jira_issues_to_records
    _issue = {"id": "99999", "key": "ABC-123", "fields": {"summary": "Multi-site"}}
    result1 = jira_issues_to_records([_issue], JIRA_URL)
    result2 = jira_issues_to_records([_issue], _JIRA_URL_2)
    all_records = consume_records(result1) + consume_records(result2)
    assert len(all_records) == 2
    accounts = {r["account"] for r in all_records}
    assert accounts == {JIRA_URL, _JIRA_URL_2}
    assert all(r["external_id"] == "ABC-123" for r in all_records)
    assert len({r["id"] for r in all_records}) == 2


def test_onenote_failure_does_not_corrupt_jira_records():
    """18-7: OneNote failure leaves Jira records intact and readable."""
    from connectonion.useful_tools.adapter import AdapterResult, consume_records, jira_issues_to_records
    jira_result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    onenote_result = AdapterResult(records=[], errors={"notebook-1": "Token expired"})
    jira_records = consume_records(jira_result)
    assert len(jira_records) == 2
    assert all(r["source"] == "jira" for r in jira_records)
    assert "notebook-1" in onenote_result.errors
    assert len(consume_records(onenote_result)) == 0


def test_jira_safe_errors_do_not_leak_secrets_into_adapter_result():
    """18-7b: Jira 401/403 error messages in AdapterResult.errors contain no token or server body.

    NOTE: OneNote authentication and transport error mapping is owned by the
    OneNote integration and is not implemented or verified in this Jira-led
    shared-adapter change.
    """
    from connectonion.useful_tools.adapter import AdapterResult
    # Jira 401: JiraAuthError message must not contain JIRA_API_TOKEN or JIRA_EMAIL
    get, _ = _mock_get({"/rest/api/3/project/search": (401, {})})
    jira_result = AdapterResult()
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        try:
            Jira().project_items()
        except Exception as exc:
            jira_result.errors["jira"] = str(exc)
    assert JIRA_TOKEN not in jira_result.errors.get("jira", "")
    assert JIRA_EMAIL not in jira_result.errors.get("jira", "")
    # Jira 403: JiraPermissionError message must not contain raw server response body
    get403, _ = _mock_get({"/rest/api/3/search/jql": (403, {"errorMessages": ["raw server body"]})})
    jira_403 = AdapterResult()
    with patch("connectonion.useful_tools.jira.httpx.get", get403):
        try:
            Jira().issue_items("ALPHA", {"ALPHA-1"})
        except Exception as exc:
            jira_403.errors["jira"] = str(exc)
    assert "raw server body" not in jira_403.errors.get("jira", "")
    assert "errorMessages" not in jira_403.errors.get("jira", "")
    assert JIRA_TOKEN not in jira_403.errors.get("jira", "")


def test_end_to_end_jira_fixture_written_in_daily_jsonl_layout(tmp_path):
    """18-8: write_daily_jsonl places Jira records at daily/YYYY/MM/DD.jsonl; each line has provenance."""
    from connectonion.useful_tools.adapter import consume_records, write_daily_jsonl, jira_issues_to_records
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    jsonl_path = write_daily_jsonl(result.records, dest)
    today = datetime.date.today()
    expected = dest / "daily" / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    assert jsonl_path == expected
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        rec = json.loads(line)
        assert rec["source"] == "jira"
        assert rec["account"] == JIRA_URL
        assert "id" in rec
        assert "url" in rec
        assert "updated" in rec and "fetched" in rec
    assert consume_records(result) == result.records


def test_end_to_end_onenote_fixture_written_in_daily_jsonl_layout(tmp_path):
    """18-8b: write_daily_jsonl places OneNote records at daily/YYYY/MM/DD.jsonl."""
    from connectonion.useful_tools.adapter import consume_records, write_daily_jsonl, onenote_pages_to_records
    dest = tmp_path / ".co" / "rem" / "docs" / "onenote"
    result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    jsonl_path = write_daily_jsonl(result.records, dest)
    today = datetime.date.today()
    expected = dest / "daily" / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    assert jsonl_path == expected
    assert jsonl_path.exists()
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    for line in lines:
        rec = json.loads(line)
        assert rec["source"] == "onenote"
        assert rec["account"] == _ONENOTE_ACCOUNT
        assert "id" in rec
        assert "url" in rec
        assert "updated" in rec and "fetched" in rec
    assert consume_records(result) == result.records


def test_jira_issue_text_follows_blobs_derived_or_metadata_only_convention(tmp_path):
    """18-8c: media_policy=metadata_only; summary inline in JSONL; no blob files; no token in files."""
    from connectonion.useful_tools.adapter import write_daily_jsonl, jira_issues_to_records
    dest = tmp_path / ".co" / "rem" / "docs" / "jira"
    with patch("connectonion.useful_tools.jira.importlib.metadata.version", return_value="9.9.9"):
        Jira().write_source_yaml(dest, ["ALPHA"], {"ALPHA-1"})
    source_yaml = yaml.safe_load((dest / "source.yaml").read_text(encoding="utf-8"))
    assert source_yaml["media_policy"] == "metadata_only"
    issues = [{"id": "10001", "key": "ALPHA-1", "fields": {"summary": "Inline summary text", "created": "2024-01-01"}}]
    result = jira_issues_to_records(issues, JIRA_URL)
    jsonl_path = write_daily_jsonl(result.records, dest)
    line = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert line["title"] == "Inline summary text"
    assert "blob_hash" not in line
    assert "blob_ref" not in line
    assert JIRA_TOKEN not in jsonl_path.read_text(encoding="utf-8")
    written = [f for f in dest.rglob("*") if f.is_file()]
    assert not any("blob" in f.name or "attachment" in f.name for f in written)


def test_cli_help_exits_zero_and_contains_example_both_sources():
    """18-9: co jira --help and co onenote --help exit 0 with usage examples."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    result_jira = runner.invoke(app, ["jira", "--help"])
    assert result_jira.exit_code == 0, result_jira.output
    assert "example" in result_jira.output.lower() or "co jira" in result_jira.output.lower()
    result_onenote = runner.invoke(app, ["onenote", "--help"])
    assert result_onenote.exit_code == 0, result_onenote.output
    assert "example" in result_onenote.output.lower() or "onenote" in result_onenote.output.lower()


def test_cli_json_output_for_success_and_valid_empty_both_sources():
    """18-10: co jira projects --json and co onenote ls --json produce valid JSON."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    # co jira projects --json: non-empty
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.return_value = [_P1, _P2]
        result = runner.invoke(app, ["jira", "projects", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert "projects" in data
    assert len(data["projects"]) == 2
    # co jira projects --json: valid empty
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.return_value = []
        result = runner.invoke(app, ["jira", "projects", "--json"])
    assert result.exit_code == 0, result.output
    data_empty = json.loads(result.output)
    assert data_empty.get("projects") == []
    # co onenote ls --json: non-empty
    with patch("connectonion.cli.commands.onenote_commands._onenote") as mock_onenote:
        mock_onenote.return_value.notebook_items.return_value = [{"displayName": "NB1", "sections": []}]
        result = runner.invoke(app, ["onenote", "ls", "--json"])
    assert result.exit_code == 0, result.output
    data_on = json.loads(result.output)
    assert "notebooks" in data_on
    assert len(data_on["notebooks"]) == 1
    # co onenote ls --json: valid empty
    with patch("connectonion.cli.commands.onenote_commands._onenote") as mock_onenote:
        mock_onenote.return_value.notebook_items.return_value = []
        result = runner.invoke(app, ["onenote", "ls", "--json"])
    assert result.exit_code == 0, result.output
    data_on_empty = json.loads(result.output)
    assert data_on_empty.get("notebooks") == []

def test_onenote_ls_json_error_is_safe():
    """18-10b: co onenote ls --json wraps exceptions in a safe generic message; no raw content leaks."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.onenote_commands._onenote") as mock_onenote:
        mock_onenote.return_value.notebook_items.side_effect = ValueError(
            "raw server body: secret-token"
        )
        result = runner.invoke(app, ["onenote", "ls", "--json"])
    assert result.exit_code == 1
    payload = json.loads(result.output)
    assert payload["exit_code"] == 1
    assert "secret-token" not in result.output
    assert "raw server body" not in result.output
    assert "OneNote request failed" in payload["error"]

# ── STEP 6 TESTS (CLI-1 through CLI-8) ──

def test_jira_cli_exits_zero_on_success():
    """CLI-1: successful project_items() → exit 0; no credential in stdout."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.return_value = [_P1, _P2]
        result = runner.invoke(app, ["jira", "projects"])
    assert result.exit_code == 0, result.output
    assert JIRA_TOKEN not in result.stdout


def test_jira_cli_exits_nonzero_on_config_missing():
    """CLI-2: JiraConfigError → non-zero exit; no traceback; no token in output."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.side_effect = JiraConfigError(
            "JIRA_URL is not set. Run: co env set JIRA_URL https://your-domain.atlassian.net"
        )
        result = runner.invoke(app, ["jira", "projects"])
    assert result.exit_code != 0
    assert "Traceback" not in result.stderr
    assert JIRA_TOKEN not in result.stdout
    assert JIRA_TOKEN not in result.stderr


def test_jira_cli_exits_nonzero_on_401():
    """CLI-3: JiraAuthError → non-zero exit; 'authentication failed' on stderr; no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = JiraAuthError(
            "Jira authentication failed. Run: co env set JIRA_API_TOKEN <token>"
        )
        result = runner.invoke(app, ["jira", "projects"])
    assert result.exit_code != 0
    assert "Jira authentication failed" in result.stderr
    assert JIRA_TOKEN not in result.stderr


def test_jira_cli_exits_nonzero_on_403():
    """CLI-4: issue_items() 403 path via co jira issues -> non-zero exit;
    project key on stderr; no token; no raw server body."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.issue_items.side_effect = JiraPermissionError(
            "Permission denied for project ALPHA"
        )
        result = runner.invoke(app, ["jira", "issues", "ALPHA", "ALPHA-1"])
    assert result.exit_code != 0
    assert "ALPHA" in result.stderr
    assert JIRA_TOKEN not in result.stderr
    assert "errorMessages" not in result.stderr  # no raw server body


def test_jira_cli_exits_nonzero_on_timeout_and_429():
    """CLI-5: timeout ValueError → non-zero exit, retry hint on stderr, no token;
    429 ValueError → non-zero exit, rate-limit hint on stderr, no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    # timeout
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = ValueError(
            "Network timeout contacting Jira — please retry"
        )
        result_timeout = runner.invoke(app, ["jira", "projects"])
    assert result_timeout.exit_code != 0
    assert "retry" in result_timeout.stderr.lower()
    assert JIRA_TOKEN not in result_timeout.stderr
    # 429 rate limit
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = ValueError(
            "Rate limited (429) — wait and retry"
        )
        result_429 = runner.invoke(app, ["jira", "projects"])
    assert result_429.exit_code != 0
    assert "429" in result_429.stderr or "rate" in result_429.stderr.lower()
    assert JIRA_TOKEN not in result_429.stderr


def test_jira_cli_human_mode_error_to_stderr_not_stdout_no_traceback():
    """CLI-6: human mode error path — error to stderr only; stdout clean; no traceback; no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = JiraAuthError(
            "Jira authentication failed"
        )
        result = runner.invoke(app, ["jira", "projects"])
    assert result.exit_code != 0
    assert result.stdout == ""
    assert "Jira authentication failed" in result.stderr
    assert "Traceback" not in result.stderr
    assert JIRA_TOKEN not in result.stderr


def test_jira_cli_json_mode_produces_pure_json_on_success():
    """CLI-7: --json success → stdout is valid JSON with 'projects'; no traceback; no tip text."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.return_value = [_P1, _P2]
        result = runner.invoke(app, ["jira", "projects", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "projects" in data
    assert len(data["projects"]) == 2
    assert "Traceback" not in result.stdout


def test_jira_cli_json_mode_produces_machine_readable_error_on_failure():
    """CLI-8: --json failure → stdout is valid JSON with 'error'; no token; non-zero exit."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = JiraAuthError(
            "Jira authentication failed"
        )
        result = runner.invoke(app, ["jira", "projects", "--json"])
    assert result.exit_code != 0
    data = json.loads(result.stdout)
    assert "error" in data
    assert data.get("exit_code") == 1
    assert JIRA_TOKEN not in data.get("error", "")
    assert JIRA_TOKEN not in result.stdout

def test_jira_verify_json_returns_safe_account_info():
    """CLI-9 (verify --json): returns account/account_id/display_name; token absent."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc123",
            "displayName": "Alice",
            "site_url": JIRA_URL,
        }
        result = runner.invoke(app, ["jira", "verify", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["account"] == JIRA_URL
    assert data["account_id"] == "abc123"
    assert data["display_name"] == "Alice"
    assert JIRA_TOKEN not in result.stdout


def test_jira_issues_json_passes_project_and_keys_to_tool_layer():
    """CLI-10 (issues --json): tool layer called with correct project key and issue key set."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.issue_items.return_value = [_I1, _I2]
        result = runner.invoke(app, ["jira", "issues", "ALPHA", "ALPHA-1", "ALPHA-2", "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "issues" in data
    assert len(data["issues"]) == 2
    # verify the tool layer was called with project_key="ALPHA" and set of keys
    call_args = mock_jira.return_value.issue_items.call_args
    assert call_args[0][0] == "ALPHA"
    assert call_args[0][1] == {"ALPHA-1", "ALPHA-2"}


# ── FIX 1: https enforcement ──

def test_http_url_raises_config_error_with_https_hint(monkeypatch):
    """Fix-1: http:// JIRA_URL → JiraConfigError with https:// guidance and Repair hint."""
    monkeypatch.setenv("JIRA_URL", "http://test.atlassian.net")
    with pytest.raises(JiraConfigError) as exc_info:
        Jira()
    msg = str(exc_info.value)
    assert "https://" in msg
    assert "Repair:" in msg


def test_ftp_url_raises_config_error(monkeypatch):
    """Fix-1b: ftp:// JIRA_URL → JiraConfigError; not just http:// is blocked."""
    monkeypatch.setenv("JIRA_URL", "ftp://test.atlassian.net")
    with pytest.raises(JiraConfigError) as exc_info:
        Jira()
    msg = str(exc_info.value)
    assert "https://" in msg


def test_no_scheme_url_raises_config_error(monkeypatch):
    """Fix-1c: scheme-less URL (e.g. example.com) → JiraConfigError; urlparse required."""
    monkeypatch.setenv("JIRA_URL", "example.com")
    with pytest.raises(JiraConfigError) as exc_info:
        Jira()
    msg = str(exc_info.value)
    assert "https://" in msg


def test_url_with_embedded_credentials_raises_config_error_without_leaking_secret(monkeypatch):
    """Fix-1d: https://secret-token@host → JiraConfigError; message does not contain secret."""
    monkeypatch.setenv("JIRA_URL", "https://secret-token@example.atlassian.net")
    with pytest.raises(JiraConfigError) as exc_info:
        Jira()
    msg = str(exc_info.value)
    assert "secret-token" not in msg
    assert "Repair:" in msg


# ── FIX 2: Network errors, 5xx, JSON decode errors ──

def test_network_request_error_raises_recoverable_value_error():
    """Fix-2a: non-timeout RequestError (e.g. DNS failure) → ValueError; not JiraAuthError."""
    import httpx as _httpx

    def get(url, auth=None, params=None, timeout=None):
        raise _httpx.ConnectError("Name or service not known", request=None)

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError) as exc_info:
            Jira().project_items()

    assert not isinstance(exc_info.value, JiraAuthError)
    assert not isinstance(exc_info.value, JiraPermissionError)
    msg = str(exc_info.value)
    assert "reach" in msg.lower() or "network" in msg.lower()


def test_5xx_raises_recoverable_value_error_with_unavailable_hint():
    """Fix-2b: HTTP 503 → ValueError; message says 'unavailable'; no traceback from raise_for_status."""
    get, _ = _mock_get({"/rest/api/3/project/search": (503, {})})

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError) as exc_info:
            Jira().project_items()

    assert not isinstance(exc_info.value, JiraAuthError)
    assert not isinstance(exc_info.value, JiraPermissionError)
    msg = str(exc_info.value)
    assert "unavailable" in msg.lower() or "503" in msg


def test_json_decode_error_raises_value_error():
    """Fix-2c: 200 response with malformed JSON body → ValueError; not raw exception."""
    def get(url, auth=None, params=None, timeout=None):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(side_effect=ValueError("No JSON object could be decoded"))
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError) as exc_info:
            Jira().project_items()

    msg = str(exc_info.value)
    assert "JSON" in msg or "parsed" in msg.lower()


# ── FIX 3: --secret guidance ──

def test_missing_token_repair_hint_uses_temporary_environment(monkeypatch):
    """Fix-3a: JIRA_API_TOKEN missing → repair hint uses temporary env var procedure, not --secret."""
    monkeypatch.delenv("JIRA_API_TOKEN")
    with pytest.raises(JiraConfigError) as exc_info:
        Jira()
    msg = str(exc_info.value)
    assert "export JIRA_API_TOKEN" in msg
    assert "unset JIRA_API_TOKEN" in msg


def test_401_repair_hint_uses_temporary_environment():
    """Fix-3b: 401 → JiraAuthError repair hint uses temporary env var procedure, not --secret."""
    get, _ = _mock_get({"/rest/api/3/myself": (401, {})})

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraAuthError) as exc_info:
            Jira().verify_connection()

    msg = str(exc_info.value)
    assert "export JIRA_API_TOKEN" in msg
    assert "unset JIRA_API_TOKEN" in msg


def test_token_from_secret_store_is_never_used(monkeypatch):
    """Fix-3c: JIRA_API_TOKEN absent from os.environ → JiraConfigError even when setting()
    would return a saved value from the secret store.

    Regression lock for F13FCAKE-9: jira.py must read the token ONLY via
    os.environ.get(), never via setting().  This test patches setting() so it
    would return "saved-token" for JIRA_API_TOKEN, removes the env var, and
    asserts that Jira() still raises JiraConfigError — proving the secret store
    is bypassed for the token regardless of what setting() would return.
    """
    monkeypatch.delenv("JIRA_API_TOKEN")

    real_setting = __import__(
        "connectonion.environment", fromlist=["setting"]
    ).setting

    def _patched_setting(name: str):
        if name == "JIRA_API_TOKEN":
            return "saved-token-from-secret-store"
        return real_setting(name)

    with patch("connectonion.useful_tools.jira.setting", _patched_setting):
        with pytest.raises(JiraConfigError, match="JIRA_API_TOKEN"):
            Jira()


# ── FIX 4: co jira sync CLI workflow ──

def test_jira_cli_sync_creates_source_yaml(tmp_path):
    """Fix-4a: co jira sync PROJECT KEY... --output DIR → exit 0; source.yaml written."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc", "displayName": "Alice", "site_url": JIRA_URL,
        }
        mock_jira.return_value.issue_items.return_value = [_I1]
        mock_jira.return_value.write_source_yaml = MagicMock()
        result = runner.invoke(
            app, ["jira", "sync", "ALPHA", "ALPHA-1", "--output", str(tmp_path)]
        )
    assert result.exit_code == 0, result.output
    mock_jira.return_value.verify_connection.assert_called_once()
    mock_jira.return_value.issue_items.assert_called_once_with("ALPHA", {"ALPHA-1"})
    mock_jira.return_value.write_source_yaml.assert_called_once()


def test_jira_cli_sync_json_output(tmp_path):
    """Fix-4b: co jira sync --json → valid JSON with written/jsonl/project/issues fields."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc", "displayName": "Alice", "site_url": JIRA_URL,
        }
        mock_jira.return_value.issue_items.return_value = [_I1, _I2]
        mock_jira.return_value.write_source_yaml = MagicMock()
        result = runner.invoke(
            app, ["jira", "sync", "ALPHA", "ALPHA-1", "ALPHA-2",
                  "--output", str(tmp_path), "--json"]
        )
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert "written" in data
    assert "jsonl" in data
    assert data["project"] == "ALPHA"
    assert data["issues"] == 2
    assert JIRA_TOKEN not in result.stdout


def test_jira_cli_sync_end_to_end_writes_both_source_yaml_and_jsonl(tmp_path):
    """Fix-4d: sync without mocking Jira internals → source.yaml and JSONL actually written."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app

    call_seq = [0]

    def mock_get(url, auth=None, params=None, timeout=None):
        call_seq[0] += 1
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        if "myself" in url:
            resp.json = MagicMock(return_value={
                "accountId": "abc123",
                "displayName": "Alice",
            })
        else:
            resp.json = MagicMock(return_value={"issues": [_I1]})
        return resp

    runner = CliRunner()
    with patch("connectonion.useful_tools.jira.httpx.get", mock_get):
        with patch("connectonion.useful_tools.jira.importlib.metadata.version", return_value="9.9.9"):
            result = runner.invoke(
                app, ["jira", "sync", "ALPHA", "ALPHA-1", "--output", str(tmp_path)]
            )
    assert result.exit_code == 0, result.output
    assert (tmp_path / "source.yaml").exists()
    today = datetime.date.today()
    jsonl_path = (
        tmp_path / "daily" / str(today.year)
        / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    )
    assert jsonl_path.exists()
    rec = json.loads(jsonl_path.read_text(encoding="utf-8").strip())
    assert rec["source"] == "jira"
    assert rec["external_id"] == "ALPHA-1"
    assert rec["account"] == JIRA_URL


def test_jira_cli_sync_exits_nonzero_on_auth_error():
    """Fix-4c: sync with JiraAuthError → non-zero exit; message on stderr; no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.side_effect = JiraAuthError(
            "Jira authentication failed."
        )
        result = runner.invoke(app, ["jira", "sync", "ALPHA", "ALPHA-1"])
    assert result.exit_code != 0
    assert JIRA_TOKEN not in result.stderr
    assert JIRA_TOKEN not in result.stdout


# ── FIX 5: JSONL append + dedup ──

def test_write_daily_jsonl_second_write_same_day_appends_not_overwrites(tmp_path):
    """Fix-5a: two sequential writes on the same day → both batches present in the file."""
    from connectonion.useful_tools.adapter import write_daily_jsonl, jira_issues_to_records
    dest = tmp_path / "jira"
    result1 = jira_issues_to_records([_I1], JIRA_URL)
    write_daily_jsonl(result1.records, dest)
    result2 = jira_issues_to_records([_I2], JIRA_URL)
    write_daily_jsonl(result2.records, dest)

    today = datetime.date.today()
    jsonl_path = dest / "daily" / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    ids = {json.loads(l)["external_id"] for l in lines}
    assert ids == {"ALPHA-1", "ALPHA-2"}


def test_write_daily_jsonl_deduplicates_same_record_within_day(tmp_path):
    """Fix-5b: writing the same (source, account, id) twice → only one line in the file."""
    from connectonion.useful_tools.adapter import write_daily_jsonl, jira_issues_to_records
    dest = tmp_path / "jira"
    result = jira_issues_to_records([_I1], JIRA_URL)
    write_daily_jsonl(result.records, dest)
    write_daily_jsonl(result.records, dest)  # same records again

    today = datetime.date.today()
    jsonl_path = dest / "daily" / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_write_daily_jsonl_deduplicates_within_single_batch(tmp_path):
    """Fix-5c: two records with same (source, account, id) in one batch → only one written."""
    from connectonion.useful_tools.adapter import write_daily_jsonl
    dest = tmp_path / "jira"
    dup_records = [
        {"source": "jira", "account": JIRA_URL, "id": "ALPHA-1", "url": "u", "date": "2024"},
        {"source": "jira", "account": JIRA_URL, "id": "ALPHA-1", "url": "u", "date": "2024"},
    ]
    write_daily_jsonl(dup_records, dest)
    today = datetime.date.today()
    jsonl_path = dest / "daily" / str(today.year) / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1


def test_write_daily_jsonl_raises_on_corrupted_existing_file(tmp_path):
    """Fix-3: existing JSONL with corrupt line → ValueError; no new data appended."""
    from connectonion.useful_tools.adapter import write_daily_jsonl, jira_issues_to_records
    dest = tmp_path / "jira"
    today = datetime.date.today()
    daily_dir = dest / "daily" / str(today.year) / f"{today.month:02d}"
    daily_dir.mkdir(parents=True)
    jsonl_path = daily_dir / f"{today.day:02d}.jsonl"
    jsonl_path.write_text(
        '{"source": "jira", "id": "ALPHA-1"}\nNOT_JSON\n', encoding="utf-8"
    )
    result = jira_issues_to_records([_I2], JIRA_URL)
    with pytest.raises(ValueError) as exc_info:
        write_daily_jsonl(result.records, dest)
    assert "corrupted" in str(exc_info.value).lower()
    # no new data appended — file still contains the original 2 lines
    lines = jsonl_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2


# ── FIX 6: consume_jsonl_records disk reader ──

def test_consume_jsonl_records_reads_from_two_source_dirs(tmp_path):
    """Fix-6: consume_jsonl_records returns AdapterResult; reads Jira + OneNote from separate dirs."""
    from connectonion.useful_tools.adapter import (
        consume_jsonl_records, write_daily_jsonl,
        jira_issues_to_records, onenote_pages_to_records,
    )
    jira_dir = tmp_path / "jira"
    onenote_dir = tmp_path / "onenote"

    jira_result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    onenote_result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    write_daily_jsonl(jira_result.records, jira_dir)
    write_daily_jsonl(onenote_result.records, onenote_dir)

    result = consume_jsonl_records(jira_dir, onenote_dir)
    assert len(result.records) == 4
    sources = {r["source"] for r in result.records}
    assert sources == {"jira", "onenote"}
    assert result.errors == {}


def test_consume_jsonl_records_missing_dir_is_silently_skipped(tmp_path):
    """Fix-6b: a source_dir with no JSONL file for today → 0 records; no errors."""
    from connectonion.useful_tools.adapter import consume_jsonl_records
    nonexistent = tmp_path / "ghost"
    result = consume_jsonl_records(nonexistent)
    assert result.records == []
    assert result.errors == {}


def test_consume_jsonl_records_malformed_lines_reported_in_errors(tmp_path):
    """Fix-6c: malformed JSON line → reported in result.errors; valid lines still returned."""
    from connectonion.useful_tools.adapter import consume_jsonl_records
    dest = tmp_path / "jira"
    today = datetime.date.today()
    daily_dir = dest / "daily" / str(today.year) / f"{today.month:02d}"
    daily_dir.mkdir(parents=True)
    jsonl_path = daily_dir / f"{today.day:02d}.jsonl"
    jsonl_path.write_text(
        '{"source": "jira", "id": "ALPHA-1"}\nNOT_VALID_JSON\n',
        encoding="utf-8",
    )
    result = consume_jsonl_records(dest)
    assert len(result.records) == 1
    assert result.records[0]["id"] == "ALPHA-1"
    assert len(result.errors) == 1
    error_key = list(result.errors.keys())[0]
    assert str(jsonl_path) in error_key


# ── CLI JSON error-contract tests ──

def test_jira_cli_projects_json_with_503_produces_valid_json_no_traceback():
    """CLI-JSON-1: projects --json with 503 error → stdout is valid JSON; no traceback; no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.project_items.side_effect = ValueError(
            "Jira is temporarily unavailable (HTTP 503). Try again in a few minutes."
        )
        result = runner.invoke(app, ["jira", "projects", "--json"])
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert "error" in payload
    assert "503" in payload["error"] or "unavailable" in payload["error"].lower()
    assert "Traceback" not in result.stdout
    assert JIRA_TOKEN not in result.stdout


def test_jira_cli_sync_json_with_network_error_produces_valid_json_no_traceback():
    """CLI-JSON-2: sync --json with network error → stdout is valid JSON; no traceback; no token."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.side_effect = ValueError(
            "Could not reach Jira — check your network connection and try again."
        )
        result = runner.invoke(app, ["jira", "sync", "ALPHA", "ALPHA-1", "--json"])
    assert result.exit_code != 0
    payload = json.loads(result.stdout)
    assert "error" in payload
    assert "reach" in payload["error"].lower() or "network" in payload["error"].lower()
    assert "Traceback" not in result.stdout
    assert JIRA_TOKEN not in result.stdout


# ── F13FCAKE-10: missing-key verification ──

def test_issue_items_raises_when_requested_key_not_returned():
    """10-17: requesting ALPHA-999 which Jira does not return -> ValueError naming missing key."""
    get, _ = _mock_get(
        {"/rest/api/3/search/jql": (200, {"issues": [_I1]})}  # returns ALPHA-1 only
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(ValueError) as exc_info:
            Jira().issue_items("ALPHA", {"ALPHA-1", "ALPHA-999"})
    msg = str(exc_info.value)
    assert "ALPHA-999" in msg   # missing key named
    assert "ALPHA-1" not in msg  # returned key not mentioned


def test_jira_cli_sync_missing_issue_key_exits_nonzero_no_files_written(tmp_path):
    """10-18: sync with non-existent ALPHA-999 -> non-zero exit; no source.yaml; no JSONL."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app

    def mock_get(url, auth=None, params=None, timeout=None):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        if "myself" in url:
            resp.json = MagicMock(return_value={"accountId": "abc123", "displayName": "Alice"})
        else:
            resp.json = MagicMock(return_value={"issues": [_I1]})  # ALPHA-999 not returned
        return resp

    runner = CliRunner()
    with patch("connectonion.useful_tools.jira.httpx.get", mock_get):
        result = runner.invoke(
            app, ["jira", "sync", "ALPHA", "ALPHA-1", "ALPHA-999", "--output", str(tmp_path)]
        )
    assert result.exit_code != 0
    assert not (tmp_path / "source.yaml").exists()
    today = datetime.date.today()
    jsonl_path = (
        tmp_path / "daily" / str(today.year)
        / f"{today.month:02d}" / f"{today.day:02d}.jsonl"
    )
    assert not jsonl_path.exists()


# ── F13FCAKE-18: end-to-end synthetic pipeline (both chains) ──

def test_adapter_f13fcake18_full_pipeline_both_chains_end_to_end_synthetic(tmp_path):
    """18-11: end-to-end synthetic proof of both adapter chains (F13FCAKE-18).

    Chain A: Jira raw issue  -> jira_issues_to_records()   -> write_daily_jsonl() -> jira_dir
    Chain B: OneNote raw page -> onenote_pages_to_records() -> write_daily_jsonl() -> onenote_dir
    Merge:   consume_jsonl_records(jira_dir, onenote_dir)  -> unified record list

    NOTE: Chain B tests synthetic fixtures only.  Live Notes.Read integration is
    pending the OneNote team's implementation and authorized account credentials.
    """
    from connectonion.useful_tools.adapter import (
        jira_issues_to_records,
        onenote_pages_to_records,
        write_daily_jsonl,
        consume_jsonl_records,
    )

    jira_dir = tmp_path / "jira"
    onenote_dir = tmp_path / "onenote"

    # Chain A: Jira
    jira_result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    assert jira_result.errors == {}
    write_daily_jsonl(jira_result.records, jira_dir)

    # Chain B: OneNote (synthetic fixture — live integration pending)
    on_result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    assert on_result.errors == {}
    write_daily_jsonl(on_result.records, onenote_dir)

    # Merge
    merged = consume_jsonl_records(jira_dir, onenote_dir)
    assert merged.errors == {}
    assert len(merged.records) == 4
    sources = {r["source"] for r in merged.records}
    assert sources == {"jira", "onenote"}

    jira_records = [r for r in merged.records if r["source"] == "jira"]
    on_records = [r for r in merged.records if r["source"] == "onenote"]
    assert len(jira_records) == 2
    assert len(on_records) == 2

    # Each record carries full provenance
    for r in merged.records:
        assert r["account"]
        assert r["id"]
        assert r["url"] is not None
        assert r["fetched"]


def test_adapter_f13fcake18_jira_jsonl_corrupted_onenote_still_readable(tmp_path):
    """18-12: Jira JSONL has a corrupted line; OneNote JSONL is intact.

    consume_jsonl_records() must:
    - return all valid Jira records (from non-corrupted lines)
    - return all OneNote records unaffected
    - record the corrupted Jira line in AdapterResult.errors (keyed by file:lineno)
    - NOT raise — one-side corruption must not break the other side

    This directly proves the "one side fails, other side still works" resilience
    property of the shared consumer (F13FCAKE-18).
    """
    from connectonion.useful_tools.adapter import (
        onenote_pages_to_records,
        write_daily_jsonl,
        consume_jsonl_records,
    )

    jira_dir = tmp_path / "jira"
    onenote_dir = tmp_path / "onenote"

    # Write a Jira JSONL manually: 1 valid record + 1 corrupted line
    today = datetime.date.today()
    jira_daily = jira_dir / "daily" / str(today.year) / f"{today.month:02d}"
    jira_daily.mkdir(parents=True)
    jira_jsonl = jira_daily / f"{today.day:02d}.jsonl"
    valid_jira_line = json.dumps({
        "source": "jira", "account": JIRA_URL, "id": "ALPHA-1",
        "url": f"{JIRA_URL}/browse/ALPHA-1", "date": "2024-01-01", "summary": "Valid issue",
    })
    jira_jsonl.write_text(valid_jira_line + "\nNOT_VALID_JSON\n", encoding="utf-8")

    # Write valid OneNote records via the normal path
    on_result = onenote_pages_to_records([_ON1, _ON2], _ONENOTE_ACCOUNT)
    write_daily_jsonl(on_result.records, onenote_dir)

    # consume_jsonl_records must not raise
    merged = consume_jsonl_records(jira_dir, onenote_dir)

    # OneNote records are intact
    on_records = [r for r in merged.records if r["source"] == "onenote"]
    assert len(on_records) == 2, "OneNote records must survive a Jira-side corruption"

    # Valid Jira record is still returned (only the bad line is skipped)
    jira_records = [r for r in merged.records if r["source"] == "jira"]
    assert len(jira_records) == 1
    assert jira_records[0]["id"] == "ALPHA-1"

    # The corrupted Jira line is reported in errors, keyed by file:lineno
    assert len(merged.errors) == 1, "Exactly one error expected for the corrupted Jira line"
    error_key = next(iter(merged.errors))
    assert str(jira_jsonl) in error_key
    assert "2" in error_key  # line 2 is the corrupted line
    assert "JSON" in merged.errors[error_key]


# ── F13FCAKE-16: shared record contract (envelope, identity, extensions, validation, media) ──

_I_FULL = {
    "id": "10042",
    "key": "ALPHA-42",
    "fields": {
        "summary": "Decide record format",
        "issuetype": {"name": "Story"},
        "status": {"name": "In Progress"},
        "priority": {"name": "High"},
        "labels": ["records", "sprint-1"],
        "assignee": {"displayName": "Alice Example", "emailAddress": "alice@example.com"},
        "reporter": {"displayName": "Bob Example", "emailAddress": "bob@example.com"},
        "parent": {"key": "ALPHA-7"},
        "created": "2026-10-01T09:00:00.000+1000",
        "updated": "2026-10-02T10:30:00.000+1000",
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "Use JSON Lines."}]}]},
        "attachment": [{"filename": "diagram.png", "mimeType": "image/png", "size": 2048,
                        "content": "https://test.atlassian.net/secure/attachment/1/diagram.png"}],
    },
}
_ON_FULL = {
    "id": "page-9",
    "title": "Architecture notes",
    "createdDateTime": "2026-09-30T01:00:00Z",
    "lastModifiedDateTime": "2026-10-02T02:00:00Z",
    "links": {"oneNoteWebUrl": {"href": "https://onenote.example/page-9"}},
    "parentSection": {"displayName": "Design"},
    "parentNotebook": {"displayName": "CAKE"},
}
_FETCHED = "2026-10-12T00:00:00Z"


def test_jira_record_has_full_shared_envelope():
    """16-1: a Jira issue maps to every envelope field with the documented meaning."""
    from connectonion.useful_tools.adapter import RECORD_FIELDS, jira_issues_to_records
    result = jira_issues_to_records([_I_FULL], JIRA_URL, fetched=_FETCHED)
    assert result.errors == {}
    record = result.records[0]
    assert set(RECORD_FIELDS) <= set(record)
    assert record["id"] == "jira:test.atlassian.net:ALPHA-42"
    assert record["external_id"] == "ALPHA-42"
    assert record["kind"] == "Story"
    assert record["title"] == "Decide record format"
    assert record["url"] == f"{JIRA_URL}/browse/ALPHA-42"
    assert record["author"] == "Bob Example"
    assert record["at"] == "2026-10-01T09:00:00.000+1000"
    assert record["updated"] == "2026-10-02T10:30:00.000+1000"
    assert record["fetched"] == _FETCHED


def test_onenote_record_has_same_shared_envelope():
    """16-2: a OneNote page maps to the same envelope as a Jira issue."""
    from connectonion.useful_tools.adapter import RECORD_FIELDS, onenote_pages_to_records
    result = onenote_pages_to_records([_ON_FULL], _ONENOTE_ACCOUNT, fetched=_FETCHED)
    assert result.errors == {}
    record = result.records[0]
    assert set(RECORD_FIELDS) <= set(record)
    assert record["id"] == "onenote:graph.microsoft.com/v1.0:page-9"
    assert record["external_id"] == "page-9"
    assert record["kind"] == "page"
    assert record["title"] == "Architecture notes"
    assert record["url"] == "https://onenote.example/page-9"
    assert record["at"] == "2026-09-30T01:00:00Z"
    assert record["updated"] == "2026-10-02T02:00:00Z"
    assert record["extensions"] == {"section": "Design", "notebook": "CAKE"}


def test_identity_includes_source_account_and_external_id():
    """16-3: same external id across sources or Jira sites yields distinct record ids."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, onenote_pages_to_records, record_id
    issue = {"key": "ABC-1", "fields": {"summary": "x"}}
    page = {"id": "ABC-1", "title": "x"}
    ids = {
        jira_issues_to_records([issue], JIRA_URL).records[0]["id"],
        jira_issues_to_records([issue], _JIRA_URL_2).records[0]["id"],
        onenote_pages_to_records([page], _ONENOTE_ACCOUNT).records[0]["id"],
    }
    assert len(ids) == 3
    assert record_id("jira", "https://test.atlassian.net/", "ABC-1") == "jira:test.atlassian.net:ABC-1"


def test_source_specific_fields_live_under_extensions_only():
    """16-4: status, priority, labels, people, parent and raw ADF stay under extensions."""
    from connectonion.useful_tools.adapter import RECORD_FIELDS, jira_issues_to_records
    record = jira_issues_to_records([_I_FULL], JIRA_URL, fetched=_FETCHED).records[0]
    assert set(record) <= set(RECORD_FIELDS) | {"media"}
    ext = record["extensions"]
    assert ext["issue_id"] == "10042"
    assert ext["status"] == "In Progress"
    assert ext["priority"] == "High"
    assert ext["labels"] == ["records", "sprint-1"]
    assert ext["assignee"] == "Alice Example"
    assert ext["reporter"] == "Bob Example"
    assert ext["parent"] == "ALPHA-7"
    assert ext["description_adf"]["type"] == "doc"
    assert record["text"] == "Use JSON Lines."  # ADF → readable text (F13FCAKE-11); raw ADF kept above


def test_missing_optional_fields_still_produce_valid_records():
    """16-5: an issue with only a key is valid; empty extras are left out of extensions."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, validate_record
    result = jira_issues_to_records([{"key": "ALPHA-5"}], JIRA_URL)
    assert result.errors == {}
    record = result.records[0]
    assert validate_record(record) == []
    assert record["kind"] == "issue"
    assert record["extensions"] == {}
    assert "media" not in record


def test_validate_record_reports_each_contract_violation():
    """16-6: validate_record names missing fields, wrong types, bad id and top-level extras."""
    from connectonion.useful_tools.adapter import make_record, validate_record
    good = make_record("jira", JIRA_URL, "ALPHA-1", "Task", fetched=_FETCHED)
    assert validate_record(good) == []
    bad = dict(good, external_id="", title=7, extensions=[], status="Done")
    bad["id"] = "ALPHA-1"
    problems = " | ".join(validate_record(bad))
    assert "missing required field 'external_id'" in problems
    assert "field 'title' must be a string" in problems
    assert "field 'extensions' must be an object" in problems
    assert "field 'id' must be" in problems
    assert "unknown top-level field 'status'" in problems
    assert validate_record("not a record") == ["record is not an object"]


def test_malformed_items_are_isolated_with_mapping_error():
    """16-7: bad items go to errors with a clear reason; valid items in the same batch survive."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, onenote_pages_to_records
    result = jira_issues_to_records(
        [_I1, "not-an-issue", {"key": "ALPHA-9", "fields": "broken"}, {"fields": {"summary": "no key"}}],
        JIRA_URL,
    )
    assert [r["external_id"] for r in result.records] == ["ALPHA-1"]
    assert result.errors["jira:item-1"] == "mapping error: item is not an object"
    assert result.errors["jira:ALPHA-9"] == "mapping error: 'fields' is not an object"
    assert "missing required field 'external_id'" in result.errors["jira:item-3"]
    pages = onenote_pages_to_records([_ON1, None, {"title": "no id"}], _ONENOTE_ACCOUNT)
    assert [r["external_id"] for r in pages.records] == ["page-1"]
    assert set(pages.errors) == {"onenote:item-1", "onenote:item-2"}


def test_media_uses_hash_reference_or_metadata_only():
    """16-8: attachments become metadata-only media refs; a stored blob must carry its sha256."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, make_record, media_ref, validate_record
    record = jira_issues_to_records([_I_FULL], JIRA_URL, fetched=_FETCHED).records[0]
    assert record["media"] == [{"sha256": None, "name": "diagram.png", "mime": "image/png",
                                "size": 2048, "status": "metadata_only"}]
    stored = media_ref("a.png", "image/png", 10, sha256="ab" * 32, status="stored")
    assert validate_record(make_record("jira", JIRA_URL, "A-1", "Task", media=[stored])) == []
    unhashed = media_ref("a.png", status="stored")
    problems = validate_record(make_record("jira", JIRA_URL, "A-1", "Task", media=[unhashed]))
    assert any("no sha256" in p for p in problems)


def test_records_never_contain_emails_or_tokens():
    """16-9: people are kept by display name only; account emails and tokens never enter a record."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    text = json.dumps(jira_issues_to_records([_I_FULL], JIRA_URL).records)
    assert "alice@example.com" not in text
    assert "bob@example.com" not in text
    assert JIRA_TOKEN not in text


def test_onenote_api_url_fallback_is_flagged():
    """16-10: without a web link the Graph contentUrl is used and flagged as an API URL."""
    from connectonion.useful_tools.adapter import onenote_pages_to_records
    page = {"id": "page-3", "title": "t", "contentUrl": "https://graph.microsoft.com/v1.0/me/onenote/pages/page-3/content"}
    record = onenote_pages_to_records([page], _ONENOTE_ACCOUNT).records[0]
    assert record["url"].endswith("/page-3/content")
    assert record["extensions"]["url_is_api"] is True


def test_contract_records_round_trip_through_daily_layout(tmp_path):
    """16-11: records from both sources keep the full envelope after write_daily_jsonl + consume_jsonl_records."""
    from connectonion.useful_tools.adapter import (
        consume_jsonl_records, jira_issues_to_records, onenote_pages_to_records,
        validate_record, write_daily_jsonl,
    )
    jira_dir = tmp_path / ".co" / "rem" / "docs" / "jira"
    onenote_dir = tmp_path / ".co" / "rem" / "docs" / "onenote"
    write_daily_jsonl(jira_issues_to_records([_I_FULL], JIRA_URL).records, jira_dir)
    write_daily_jsonl(onenote_pages_to_records([_ON_FULL], _ONENOTE_ACCOUNT).records, onenote_dir)
    merged = consume_jsonl_records(jira_dir, onenote_dir)
    assert merged.errors == {}
    assert len(merged.records) == 2
    assert all(validate_record(r) == [] for r in merged.records)


def test_validate_record_rejects_malformed_media():
    """16-12: media must be a list of exact refs with a known status."""
    from connectonion.useful_tools.adapter import make_record, media_ref, validate_record
    base = make_record("jira", JIRA_URL, "A-1", "Task", fetched=_FETCHED)
    assert validate_record(dict(base, media="diagram.png")) == ["field 'media' must be a list"]
    missing_key = {"name": "a.png", "mime": "image/png", "size": 1, "status": "metadata_only"}
    extra_key = dict(media_ref("a.png"), bytes="...")
    problems = validate_record(dict(base, media=[missing_key, extra_key, "a.png"]))
    assert problems == [f"media[{i}] must have exactly: sha256, name, mime, size, status" for i in range(3)]
    unknown = media_ref("a.png", status="downloaded")
    assert validate_record(dict(base, media=[unknown])) == ["media[0] has unknown status 'downloaded'"]


def test_jira_mapping_handles_plain_text_and_odd_field_shapes():
    """16-13: plain-string description becomes text; creator stands in for reporter; odd shapes are ignored."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, validate_record
    issue = {"key": "ALPHA-50", "fields": {
        "summary": "Plain text issue",
        "description": "Already plain text.",
        "creator": {"displayName": "Gabe Example"},
        "status": "Done",                 # not an object
        "labels": "single-label",         # not a list
        "assignee": ["not", "a", "person"],
        "parent": "ALPHA-1",              # not an object
        "attachment": ["not-an-object", {"filename": "a.txt", "mimeType": "text/plain", "size": 3}],
    }}
    result = jira_issues_to_records([issue], JIRA_URL, fetched=_FETCHED)
    assert result.errors == {}
    record = result.records[0]
    assert validate_record(record) == []
    assert record["text"] == "Already plain text."
    assert record["author"] == "Gabe Example"
    assert record["extensions"] == {}
    assert [m["name"] for m in record["media"]] == ["a.txt"]


def test_onenote_mapping_without_any_link_or_parents():
    """16-14: no web link and no content URL → empty url, no url_is_api flag; odd links shape is ignored."""
    from connectonion.useful_tools.adapter import onenote_pages_to_records
    page = {"id": "page-7", "title": "Loose page", "links": "not-an-object",
            "parentSection": "Design", "parentNotebook": None}
    record = onenote_pages_to_records([page], _ONENOTE_ACCOUNT, fetched=_FETCHED).records[0]
    assert record["url"] == ""
    assert record["extensions"] == {}


def test_fetched_defaults_to_one_utc_timestamp_per_batch():
    """16-15: without an explicit fetched value every record in a batch shares one UTC 'Z' timestamp."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    records = jira_issues_to_records([_I1, _I2, _I3], JIRA_URL).records
    stamps = {r["fetched"] for r in records}
    assert len(stamps) == 1
    stamp = stamps.pop()
    assert stamp.endswith("Z")
    parsed = datetime.datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    assert parsed.tzinfo is not None


def test_daily_layout_skips_blank_lines_and_reads_a_given_date(tmp_path):
    """16-16: blank lines in a daily file are ignored by writer and reader; an explicit date reads that day's file."""
    from connectonion.useful_tools.adapter import consume_jsonl_records, jira_issues_to_records, write_daily_jsonl
    dest = tmp_path / "jira"
    first = write_daily_jsonl(jira_issues_to_records([_I1], JIRA_URL).records, dest)
    first.write_text(first.read_text(encoding="utf-8") + "\n   \n", encoding="utf-8")
    write_daily_jsonl(jira_issues_to_records([_I2], JIRA_URL).records, dest)
    today = consume_jsonl_records(dest, date=datetime.date.today())
    assert today.errors == {}
    assert [r["external_id"] for r in today.records] == ["ALPHA-1", "ALPHA-2"]
    assert consume_jsonl_records(dest, date=datetime.date(2000, 1, 1)).records == []


def test_bad_items_with_the_same_key_are_all_reported():
    """16-17: two bad items sharing a key both stay in errors; the second label carries its position."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    bad = {"key": "ALPHA-9", "fields": "broken"}
    result = jira_issues_to_records([bad, _I1, dict(bad)], JIRA_URL)
    assert [r["external_id"] for r in result.records] == ["ALPHA-1"]
    assert set(result.errors) == {"jira:ALPHA-9", "jira:ALPHA-9#2"}
    assert all(v == "mapping error: 'fields' is not an object" for v in result.errors.values())


def test_validate_record_requires_every_envelope_field_even_optional_ones():
    """16-18: dropping an optional envelope field (text, extensions, …) is a contract violation, not a valid record."""
    from connectonion.useful_tools.adapter import RECORD_FIELDS, make_record, validate_record
    good = make_record("jira", JIRA_URL, "ALPHA-1", "Task", fetched=_FETCHED)
    stripped = {k: v for k, v in good.items() if k not in ("text", "extensions")}
    assert validate_record(stripped) == ["missing field 'text'", "missing field 'extensions'"]
    for name in RECORD_FIELDS:
        partial = {k: v for k, v in good.items() if k != name}
        assert f"missing field '{name}'" in validate_record(partial)
    assert validate_record(dict(good, text="", extensions={})) == []


def _sync_with(issues, extra_args):
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc", "displayName": "Alice", "site_url": JIRA_URL,
        }
        mock_jira.return_value.issue_items.return_value = issues
        mock_jira.return_value.write_source_yaml = MagicMock()
        return CliRunner().invoke(app, ["jira", "sync", "ALPHA", "ALPHA-1", "ALPHA-9", *extra_args])


def test_jira_cli_sync_json_reports_rejected_items_and_exits_nonzero(tmp_path):
    """16-19: a rejected item is listed in JSON "rejected", only valid records are mapped and appended, exit code is 1."""
    result = _sync_with([_I1, {"key": "ALPHA-9", "fields": "broken"}], ["--output", str(tmp_path), "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["issues"] == 2
    assert data["mapped"] == 1
    assert data["appended"] == 1
    assert data["rejected"] == {"jira:ALPHA-9": "mapping error: 'fields' is not an object"}
    lines = Path(data["jsonl"]).read_text(encoding="utf-8").strip().splitlines()
    assert [json.loads(line)["external_id"] for line in lines] == ["ALPHA-1"]
    assert JIRA_TOKEN not in result.stdout


def test_jira_cli_sync_text_mode_lists_rejected_items_on_stderr(tmp_path):
    """16-20: a failed human import reports retained counts and rejected items on stderr only, exits 1."""
    result = _sync_with([_I1, {"key": "ALPHA-9", "fields": "broken"}], ["--output", str(tmp_path)])
    assert result.exit_code == 1
    assert result.stdout == ""
    assert "Records mapped: 1  Records appended: 1" in result.stderr
    assert "1 item(s) were rejected" in result.stderr
    assert "jira:ALPHA-9" in result.stderr


def test_jira_cli_sync_with_no_rejections_still_exits_zero(tmp_path):
    """16-21: the clean path is unchanged: rejected is empty and exit code is 0."""
    result = _sync_with([_I1], ["--output", str(tmp_path), "--json"])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["rejected"] == {}
    assert data["mapped"] == 1
    assert data["appended"] == 1


def test_onenote_records_from_two_users_do_not_collide_when_account_is_per_user():
    """16-22: the same page id read for two users gets two ids once account names the user, not the Graph URL."""
    from connectonion.useful_tools.adapter import onenote_pages_to_records
    page = {"id": "page-1", "title": "Same page id"}
    alice = onenote_pages_to_records([page], "onenote-user:alice-object-id").records[0]
    bob = onenote_pages_to_records([page], "onenote-user:bob-object-id").records[0]
    assert alice["id"] != bob["id"]
    assert alice["external_id"] == bob["external_id"] == "page-1"
    shared = onenote_pages_to_records([page], _ONENOTE_ACCOUNT).records[0]
    assert shared["id"] == "onenote:graph.microsoft.com/v1.0:page-1"  # fixture placeholder: not per user


def test_jira_cli_sync_same_day_resync_reports_zero_appended(tmp_path):
    """16-23: a second sync on the same day maps the item again but appends nothing; the summary says so."""
    first = json.loads(_sync_with([_I1], ["--output", str(tmp_path), "--json"]).stdout)
    second_run = _sync_with([_I1], ["--output", str(tmp_path), "--json"])
    second = json.loads(second_run.stdout)
    assert (first["mapped"], first["appended"]) == (1, 1)
    assert (second["mapped"], second["appended"]) == (1, 0)
    assert second_run.exit_code == 0
    assert len(Path(second["jsonl"]).read_text(encoding="utf-8").strip().splitlines()) == 1
    text_run = _sync_with([_I1], ["--output", str(tmp_path)])
    assert "Records mapped: 1  Records appended: 0" in text_run.stdout


def test_append_daily_jsonl_counts_only_new_records(tmp_path):
    """16-24: append_daily_jsonl returns the same path as write_daily_jsonl plus the number really appended."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records, write_daily_jsonl
    records = jira_issues_to_records([_I1, _I2], JIRA_URL).records
    path, appended = append_daily_jsonl(records, tmp_path)
    assert appended == 2
    assert append_daily_jsonl(records, tmp_path) == (path, 0)
    assert write_daily_jsonl(records, tmp_path) == path


# ── F13FCAKE-11: Jira work-item content import ──

def test_issue_items_requests_the_full_content_field_set():
    """11-1: every /search/jql page asks for ISSUE_FIELDS, so content does not depend on server defaults."""
    from connectonion.useful_tools.jira import ISSUE_FIELDS
    received_params: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        received_params.append(dict(params or {}))
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        page = {"issues": [_I1]} if len(received_params) == 1 else {"issues": [_I2]}
        if len(received_params) == 1:
            page["nextPageToken"] = "page-2"
        resp.json = MagicMock(return_value=page)
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", get):
        issues = Jira().issue_items("ALPHA", {"ALPHA-1", "ALPHA-2"})

    assert [i["key"] for i in issues] == ["ALPHA-1", "ALPHA-2"]
    assert len(received_params) == 2
    for params in received_params:
        assert params["fields"].split(",") == list(ISSUE_FIELDS)
    for needed in ("summary", "description", "issuetype", "status", "priority", "labels",
                   "assignee", "reporter", "created", "updated", "attachment"):
        assert needed in ISSUE_FIELDS


_I_SEARCH = {
    "id": "10077",
    "key": "ALPHA-77",
    "fields": {
        "summary": "Pagination stops after the first page",
        "issuetype": {"name": "Bug"},
        "status": {"name": "Done"},
        "priority": {"name": "Highest"},
        "labels": ["bug", "pagination"],
        "assignee": {"displayName": "Ben Example"},
        "reporter": {"displayName": "Diego Example"},
        "created": "2026-10-05T09:00:00.000+1100",
        "updated": "2026-10-07T16:20:00.000+1100",
        "duedate": "2026-10-09",
        "resolutiondate": "2026-10-07T16:20:00.000+1100",
        "parent": {"key": "ALPHA-1"},
        "description": {"type": "doc", "version": 1, "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Steps"}]},
            {"type": "orderedList", "content": [
                {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Select 60 issues"}]}]},
                {"type": "listItem", "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Run the import"}]}]}]},
        ]},
        "attachment": [],
    },
}


def test_jira_record_text_is_readable_and_raw_adf_is_retained():
    """11-12: ADF description becomes readable text in record["text"]; the raw ADF stays in extensions."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    record = jira_issues_to_records([_I_SEARCH], JIRA_URL, fetched=_FETCHED).records[0]
    assert record["text"] == "## Steps\n\n1. Select 60 issues\n2. Run the import"
    assert record["extensions"]["description_adf"] == _I_SEARCH["fields"]["description"]
    plain = {"key": "ALPHA-78", "fields": {"summary": "s", "description": "  Plain text body.  "}}
    plain_record = jira_issues_to_records([plain], JIRA_URL).records[0]
    assert plain_record["text"] == "Plain text body."
    assert "description_adf" not in plain_record["extensions"]


def test_jira_record_carries_every_requested_content_field():
    """11-13: summary, type, status, priority, people, labels, timestamps, dates and URL all reach the record."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, validate_record
    record = jira_issues_to_records([_I_SEARCH], JIRA_URL, fetched=_FETCHED).records[0]
    assert validate_record(record) == []
    assert (record["title"], record["kind"], record["author"]) == (
        "Pagination stops after the first page", "Bug", "Diego Example")
    assert (record["at"], record["updated"]) == ("2026-10-05T09:00:00.000+1100", "2026-10-07T16:20:00.000+1100")
    assert record["url"] == f"{JIRA_URL}/browse/ALPHA-77"
    assert record["extensions"] == {
        "issue_id": "10077", "status": "Done", "priority": "Highest", "labels": ["bug", "pagination"],
        "assignee": "Ben Example", "reporter": "Diego Example", "parent": "ALPHA-1",
        "duedate": "2026-10-09", "resolutiondate": "2026-10-07T16:20:00.000+1100",
        "description_adf": _I_SEARCH["fields"]["description"],
    }


def test_missing_or_unreadable_description_does_not_block_the_record():
    """11-14: no description, an empty ADF doc or an unreadable description still gives a valid record with text ""."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    issues = [
        {"key": "ALPHA-80", "fields": {"summary": "no description"}},
        {"key": "ALPHA-81", "fields": {"summary": "empty doc", "description": {"type": "doc", "content": []}}},
        {"key": "ALPHA-82", "fields": {"summary": "odd description", "description": ["not", "adf"]}},
    ]
    result = jira_issues_to_records(issues, JIRA_URL)
    assert result.errors == {}
    assert [r["text"] for r in result.records] == ["", "", ""]
    assert [r["title"] for r in result.records] == ["no description", "empty doc", "odd description"]


def _paged_search(pages: list[list[dict]]):
    """httpx.get stand-in: /search/jql returns the given pages in order, chained by nextPageToken."""
    calls: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        calls.append(dict(params or {}))
        index = len(calls) - 1
        body = {"issues": pages[index]}
        if index + 1 < len(pages):
            body["nextPageToken"] = f"page-{index + 2}"
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=body)
        return resp

    return get, calls


def _issues(first: int, last: int) -> list[dict]:
    return [{"id": str(10000 + n), "key": f"ALPHA-{n}", "fields": {"summary": f"Issue {n}"}}
            for n in range(first, last + 1)]


def test_duplicate_items_in_one_batch_are_reported_not_stored_twice():
    """11-15: the same issue twice in one batch gives one record; the repeat is reported, keeping the first copy."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    result = jira_issues_to_records([_I1, _I2, dict(_I1)], JIRA_URL)
    assert [r["external_id"] for r in result.records] == ["ALPHA-1", "ALPHA-2"]
    assert result.errors == {"jira:ALPHA-1": "mapping error: duplicate of an earlier item in this batch "
                                             "(jira:test.atlassian.net:ALPHA-1); the first copy was kept"}


def test_sixty_issues_over_two_pages_map_to_sixty_unique_records(tmp_path):
    """11-16: issue_items + mapping + daily write over 2 pages (50 + 10): nothing duplicated, nothing missing."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records
    wanted = {f"ALPHA-{n}" for n in range(1, 61)}
    get, calls = _paged_search([_issues(1, 50), _issues(51, 60)])
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        issues = Jira().issue_items("ALPHA", wanted)
    assert len(calls) == 2
    result = jira_issues_to_records(issues, JIRA_URL)
    assert result.errors == {}
    assert {r["external_id"] for r in result.records} == wanted
    assert len({r["id"] for r in result.records}) == 60
    _, appended = append_daily_jsonl(result.records, tmp_path)
    assert appended == 60


def test_overlapping_pages_do_not_create_duplicate_records(tmp_path):
    """11-17: a server that repeats an issue on the next page yields 60 records and reports the repeat."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records
    wanted = {f"ALPHA-{n}" for n in range(1, 61)}
    get, _ = _paged_search([_issues(1, 50), _issues(50, 60)])  # ALPHA-50 appears on both pages
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        issues = Jira().issue_items("ALPHA", wanted)
    assert len(issues) == 61
    result = jira_issues_to_records(issues, JIRA_URL)
    assert len(result.records) == 60
    assert {r["external_id"] for r in result.records} == wanted
    assert list(result.errors) == ["jira:ALPHA-50"]
    assert "the first copy was kept" in result.errors["jira:ALPHA-50"]
    _, appended = append_daily_jsonl(result.records, tmp_path)
    assert appended == 60


def test_sync_reports_a_repeated_issue_instead_of_hiding_it(tmp_path):
    """11-18: co jira sync lists the repeated issue under "rejected", appends each issue once and exits 1."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc", "displayName": "Alice", "site_url": JIRA_URL,
        }
        mock_jira.return_value.issue_items.return_value = [_I1, _I2, dict(_I2)]
        mock_jira.return_value.write_source_yaml = MagicMock()
        result = CliRunner().invoke(app, ["jira", "sync", "ALPHA", "ALPHA-1", "ALPHA-2",
                                          "--output", str(tmp_path), "--json"])
    assert result.exit_code == 1
    data = json.loads(result.stdout)
    assert (data["issues"], data["mapped"], data["appended"]) == (3, 2, 2)
    assert list(data["rejected"]) == ["jira:ALPHA-2"]


def test_one_item_that_cannot_be_mapped_does_not_abort_the_batch():
    """11-24: an unexpected failure while mapping one issue is reported for that issue; the rest still map."""
    from connectonion.useful_tools import adapter
    original = adapter._jira_record

    def flaky(issue, base_url, fetched):
        if issue.get("key") == "ALPHA-2":
            raise OverflowError("timestamp out of range")
        return original(issue, base_url, fetched)

    with patch.object(adapter, "_jira_record", flaky):
        result = adapter.jira_issues_to_records([_I1, _I2, _I3], JIRA_URL)
    assert [r["external_id"] for r in result.records] == ["ALPHA-1", "ALPHA-3"]
    assert result.errors == {"jira:ALPHA-2": "mapping error: content could not be mapped "
                                             "(OverflowError: timestamp out of range)"}


def test_out_of_range_adf_date_keeps_the_issue_and_the_batch():
    """11-25: a date node far outside the calendar is dropped from the text; both issues are still imported."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    bad_date = {"key": "ALPHA-2", "fields": {"summary": "odd date", "description": {"type": "doc", "content": [
        {"type": "paragraph", "content": [{"type": "text", "text": "due "},
                                          {"type": "date", "attrs": {"timestamp": "9" * 30}}]}]}}}
    result = jira_issues_to_records([_I1, bad_date], JIRA_URL)
    assert result.errors == {}
    assert [r["external_id"] for r in result.records] == ["ALPHA-1", "ALPHA-2"]
    assert result.records[1]["text"] == "due"


def test_failed_export_leaves_nothing_behind_and_can_be_retried(tmp_path):
    """11-26: unserialisable data is refused before any write; a failure while writing removes the staging folder."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, write_export
    good = jira_issues_to_records([_I1], JIRA_URL).records
    weird = [dict(good[0], extensions={"not_json": {1, 2}})]
    with pytest.raises(ValueError, match="cannot be saved as JSON.*Nothing was written"):
        write_export(weird, tmp_path, "retry-me", _SCOPE)
    with pytest.raises(ValueError, match="cannot be saved as JSON"):
        write_export(good, tmp_path, "retry-me", {"when": {1, 2}})
    assert not (tmp_path / "exports").exists()
    with patch("pathlib.Path.rename", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            write_export(good, tmp_path, "retry-me", _SCOPE)
    assert list((tmp_path / "exports").iterdir()) == []
    export_dir = write_export(good, tmp_path, "retry-me", _SCOPE)
    assert (export_dir / "manifest.json").exists()


def _sync_cli(args: list[str], issues: list[dict]):
    """Run co jira sync with a mocked Jira; returns (result, mock_jira)."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    with patch("connectonion.cli.commands.jira_commands._jira") as mock_jira:
        mock_jira.return_value.verify_connection.return_value = {
            "accountId": "abc", "displayName": "Alice", "site_url": JIRA_URL,
        }
        mock_jira.return_value.issue_items.return_value = issues
        mock_jira.return_value.write_source_yaml = MagicMock()
        result = CliRunner().invoke(app, ["jira", "sync", *args])
    return result, mock_jira


def test_sync_export_writes_a_one_off_snapshot_instead_of_daily(tmp_path):
    """11-27: co jira sync --export <id> places the records in exports/<id>/ with a manifest; daily/ is untouched."""
    result, _ = _sync_cli(["ALPHA", "ALPHA-1", "ALPHA-2", "--output", str(tmp_path),
                           "--export", "ALPHA-history", "--json"], [_I1, _I2])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    export_dir = tmp_path / "exports" / "ALPHA-history"
    assert data["placement"] == "export"
    assert data["jsonl"] == str(export_dir / "records.jsonl")
    assert (data["mapped"], data["appended"]) == (2, 2)
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["scope"] == {"projects": ["ALPHA"], "issues": ["ALPHA-1", "ALPHA-2"]}
    assert manifest["counts"]["records"] == 2
    assert not (tmp_path / "daily").exists()


def test_sync_without_export_reports_daily_placement(tmp_path):
    """11-28: the ongoing path is unchanged and says so: placement "daily", records in today's daily file."""
    result, _ = _sync_cli(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--json"], [_I1])
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["placement"] == "daily"
    assert "/daily/" in data["jsonl"].replace("\\", "/")
    assert not (tmp_path / "exports").exists()


def test_sync_export_with_unusable_id_fails_before_any_request(tmp_path):
    """11-29: a bad or already-used export id exits 1 before calling Jira or writing source.yaml."""
    bad, mock_jira = _sync_cli(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--export", "../escape"], [_I1])
    assert bad.exit_code == 1
    assert "Invalid export id" in bad.stderr
    mock_jira.assert_not_called()
    first, _ = _sync_cli(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--export", "once"], [_I1])
    assert first.exit_code == 0, first.output
    again, mock_jira = _sync_cli(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--export", "once", "--json"], [_I1])
    assert again.exit_code == 1
    assert "already exists" in json.loads(again.stdout)["error"]
    mock_jira.assert_not_called()


def test_jira_help_is_defined_once_with_the_temporary_token_guidance():
    """11-30: co jira is registered once; its help gives the temporary-token rule and sync documents --export."""
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    import connectonion.cli.main as cli_main
    source = Path(cli_main.__file__).read_text(encoding="utf-8")
    assert source.count("jira_app = _typer_app(") == 1
    top = CliRunner().invoke(app, ["jira", "--help"])
    assert "temporary" in top.stdout
    assert "Requires JIRA_URL, JIRA_EMAIL, and JIRA_API_TOKEN from co env set" not in top.stdout
    sync_help = CliRunner().invoke(app, ["jira", "sync", "--help"])
    assert "--export" in sync_help.stdout


_SCOPE = {"projects": ["ALPHA"], "issues": "all", "reason": "first import of project history"}


def test_write_export_writes_records_and_manifest(tmp_path):
    """11-19: a one-off import lands in exports/<id>/ as records.jsonl plus a manifest of scope and counts."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, validate_record, write_export
    records = jira_issues_to_records([_I_SEARCH, _I1, _I2], JIRA_URL, fetched=_FETCHED).records
    export_dir = write_export(records, tmp_path / "jira", "ALPHA-history-2026-10-12", _SCOPE)
    assert export_dir == tmp_path / "jira" / "exports" / "ALPHA-history-2026-10-12"
    assert sorted(f.name for f in export_dir.iterdir()) == ["manifest.json", "records.jsonl"]
    lines = (export_dir / "records.jsonl").read_text(encoding="utf-8").strip().splitlines()
    stored = [json.loads(line) for line in lines]
    assert [r["external_id"] for r in stored] == ["ALPHA-77", "ALPHA-1", "ALPHA-2"]
    assert all(validate_record(r) == [] for r in stored)
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["export_id"] == "ALPHA-history-2026-10-12"
    assert manifest["scope"] == _SCOPE
    assert manifest["sources"] == ["jira"]
    assert manifest["accounts"] == [JIRA_URL]
    assert manifest["counts"] == {"records": 3, "by_kind": {"Bug": 1, "issue": 2}}
    assert manifest["files"] == ["records.jsonl"]
    assert manifest["created"].endswith("Z")
    assert not (tmp_path / "jira" / "daily").exists()  # an export never touches daily/


def test_write_export_refuses_invalid_or_duplicate_records_and_writes_nothing(tmp_path):
    """11-20: one invalid record or a repeated id stops the whole export before any file is written."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, write_export
    good = jira_issues_to_records([_I1, _I2], JIRA_URL).records
    broken = dict(good[1])
    del broken["text"]
    with pytest.raises(ValueError, match=r"missing field 'text'.*Nothing was written"):
        write_export([good[0], broken], tmp_path, "bad-record", _SCOPE)
    with pytest.raises(ValueError, match="duplicate id in this export"):
        write_export([good[0], good[0]], tmp_path, "dup-record", _SCOPE)
    with pytest.raises(ValueError, match="record 0: record is not an object"):
        write_export(["not a record"], tmp_path, "not-a-record", _SCOPE)
    assert not (tmp_path / "exports").exists()


def test_write_export_reports_at_most_five_problems(tmp_path):
    """11-21: a large bad batch names the first five problems and counts the rest."""
    from connectonion.useful_tools.adapter import write_export
    with pytest.raises(ValueError, match=r"; … 2 more\. Nothing was written"):
        write_export(["a", "b", "c", "d", "e", "f", "g"], tmp_path, "many", _SCOPE)


def test_write_export_refuses_unsafe_ids_and_existing_exports(tmp_path):
    """11-22: export ids cannot escape exports/, and an existing export is never overwritten or appended to."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, write_export
    records = jira_issues_to_records([_I1], JIRA_URL).records
    for bad_id in ("", "../outside", "a/b", ".hidden", "with space", "x" * 65, None):
        with pytest.raises(ValueError, match="Invalid export id"):
            write_export(records, tmp_path, bad_id, _SCOPE)
    first = write_export(records, tmp_path, "once", _SCOPE)
    before = (first / "records.jsonl").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="already exists"):
        write_export(jira_issues_to_records([_I2], JIRA_URL).records, tmp_path, "once", _SCOPE)
    assert (first / "records.jsonl").read_text(encoding="utf-8") == before
    (tmp_path / "exports" / ".stale.partial").mkdir()
    with pytest.raises(ValueError, match="unfinished earlier attempt.*Delete that folder and retry"):
        write_export(records, tmp_path, "stale", _SCOPE)


def test_empty_export_is_a_valid_outcome(tmp_path):
    """11-23: an export with no records is written with a manifest that says 0, not treated as a failure."""
    from connectonion.useful_tools.adapter import write_export
    export_dir = write_export([], tmp_path, "empty-scope", {"projects": ["ALPHA"], "issues": []})
    assert (export_dir / "records.jsonl").read_text(encoding="utf-8") == ""
    manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["counts"] == {"records": 0, "by_kind": {}}
    assert manifest["sources"] == [] and manifest["accounts"] == []


# ── F13FCAKE-12: Jira history and provenance ──

def _history(entry_id: str, when: str, field: str, old: str, new: str, who: str = "Diego Example") -> dict:
    """One raw Jira changelog history entry, as /issue/{key}/changelog returns it."""
    return {"id": entry_id, "author": {"displayName": who, "emailAddress": "diego@example.com"},
            "created": when, "items": [{"field": field, "fieldtype": "jira",
                                        "fromString": old, "toString": new}]}


def _changelog_pages(pages: list[list[dict]], status: int = 200):
    """httpx.get stand-in for /issue/{key}/changelog: offset pages, isLast on the final one."""
    calls: list[dict] = []

    def get(url, auth=None, params=None, timeout=None):
        calls.append({"url": url, "params": dict(params or {})})
        index = len(calls) - 1
        resp = MagicMock(status_code=status)
        resp.raise_for_status = MagicMock()
        values = pages[index] if index < len(pages) else []
        resp.json = MagicMock(return_value={"startAt": sum(len(p) for p in pages[:index]),
                                            "values": values, "isLast": index + 1 >= len(pages)})
        return resp

    return get, calls


def test_issue_changelog_reads_every_page_in_order():
    """12-1: issue_changelog() follows startAt/isLast across pages and returns all histories, oldest first."""
    page1 = [_history("1", "2026-10-05T10:00:00.000+1100", "status", "To Do", "In Progress")]
    page2 = [_history("2", "2026-10-06T10:00:00.000+1100", "priority", "High", "Highest")]
    get, calls = _changelog_pages([page1, page2])
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        histories = Jira().issue_changelog("ALPHA-1")
    assert [h["id"] for h in histories] == ["1", "2"]
    assert all(c["url"].endswith("/rest/api/3/issue/ALPHA-1/changelog") for c in calls)
    assert [c["params"]["startAt"] for c in calls] == [0, 1]


def test_issue_changelog_never_edited_issue_returns_empty_list():
    """12-2: an issue with no history gives [] (a valid empty result, not an error)."""
    get, calls = _changelog_pages([[]])
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        assert Jira().issue_changelog("ALPHA-2") == []
    assert len(calls) == 1


def test_issue_changelog_403_names_the_issue_and_hides_the_server_body():
    """12-3: no permission for the history → JiraPermissionError naming the key, no server body, no token."""
    get, _ = _mock_get({"/rest/api/3/issue/ALPHA-3/changelog": (403, {"errorMessages": ["raw server body"]})})
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraPermissionError) as excinfo:
            Jira().issue_changelog("ALPHA-3")
    message = str(excinfo.value)
    assert "ALPHA-3" in message
    assert "raw server body" not in message
    assert JIRA_TOKEN not in message


def test_issue_changelog_rejects_bad_keys_and_stuck_pages():
    """12-4: a malformed key makes no request; an empty page that is not the last aborts instead of looping."""
    get, calls = _changelog_pages([[]])
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        for bad in ("alpha-1", "ALPHA-1 OR 1=1", "", None):
            with pytest.raises(ValueError, match="Invalid Jira issue key"):
                Jira().issue_changelog(bad)
    assert calls == []

    def stuck(url, auth=None, params=None, timeout=None):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"values": [], "isLast": False})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", stuck):
        with pytest.raises(ValueError, match="empty page with isLast=false"):
            Jira().issue_changelog("ALPHA-1")


def test_issue_changelog_stops_after_the_page_limit():
    """12-5: a server that never reports isLast stops after the page limit instead of looping."""
    def endless(url, auth=None, params=None, timeout=None):
        resp = MagicMock(status_code=200)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"values": [_history("x", "2026-10-05T10:00:00Z", "status", "a", "b")],
                                            "isLast": False})
        return resp

    with patch("connectonion.useful_tools.jira.httpx.get", endless):
        with pytest.raises(ValueError, match="exceeded 50 pages"):
            Jira().issue_changelog("ALPHA-1")


def _changelog_by_key(responses: dict):
    """httpx.get stand-in routing /issue/{key}/changelog to (status, values) per key; one page each."""
    calls: list[str] = []

    def get(url, auth=None, params=None, timeout=None):
        key = url.rstrip("/").split("/issue/")[1].split("/")[0]
        calls.append(key)
        status, values = responses[key]
        resp = MagicMock(status_code=status)
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"values": values, "isLast": True})
        return resp

    return get, calls


def test_issue_changelogs_classifies_each_issue_independently():
    """12-6: ok / none / restricted / unavailable per issue; a 403 or 500 on one issue does not stop the others."""
    from connectonion.useful_tools.jira import CHANGELOG_STATUSES
    edited = [_history("1", "2026-10-05T10:00:00.000+1100", "status", "To Do", "Done")]
    get, calls = _changelog_by_key({
        "ALPHA-1": (200, edited),
        "ALPHA-2": (200, []),
        "ALPHA-3": (403, {"errorMessages": ["raw server body"]}),
        "ALPHA-4": (500, []),
    })
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        outcomes = Jira().issue_changelogs({"ALPHA-4", "ALPHA-2", "ALPHA-1", "ALPHA-3"})
    assert list(outcomes) == ["ALPHA-1", "ALPHA-2", "ALPHA-3", "ALPHA-4"]
    assert calls == ["ALPHA-1", "ALPHA-2", "ALPHA-3", "ALPHA-4"]
    assert {k: v["status"] for k, v in outcomes.items()} == {
        "ALPHA-1": "ok", "ALPHA-2": "none", "ALPHA-3": "restricted", "ALPHA-4": "unavailable"}
    assert set(v["status"] for v in outcomes.values()) == set(CHANGELOG_STATUSES)
    assert outcomes["ALPHA-1"]["histories"] == edited
    assert outcomes["ALPHA-2"] == {"status": "none", "histories": [], "reason": ""}
    assert "ALPHA-3" in outcomes["ALPHA-3"]["reason"]
    assert "HTTP 500" in outcomes["ALPHA-4"]["reason"]


def test_no_history_is_distinguishable_from_unreadable_history():
    """12-7: "never edited" (none) and "could not read" (restricted/unavailable) are different outcomes."""
    get, _ = _changelog_by_key({"ALPHA-1": (200, []), "ALPHA-2": (403, {}), "ALPHA-3": (429, [])})
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        outcomes = Jira().issue_changelogs(["ALPHA-1", "ALPHA-2", "ALPHA-3"])
    assert [outcomes[k]["status"] for k in ("ALPHA-1", "ALPHA-2", "ALPHA-3")] == ["none", "restricted", "unavailable"]
    assert outcomes["ALPHA-1"]["reason"] == ""
    assert outcomes["ALPHA-2"]["reason"] and outcomes["ALPHA-3"]["reason"]
    assert "rate limit" in outcomes["ALPHA-3"]["reason"]


def test_issue_changelogs_reasons_never_leak_secrets_or_server_bodies():
    """12-8: restricted / unavailable reasons contain the key and guidance, never the token, email or body."""
    get, _ = _changelog_by_key({"ALPHA-1": (403, {"errorMessages": ["raw server body"]}),
                                "ALPHA-2": (503, {"message": "raw outage body"})})
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        outcomes = Jira().issue_changelogs(["ALPHA-1", "ALPHA-2"])
    text = json.dumps(outcomes)
    for secret in (JIRA_TOKEN, JIRA_EMAIL, "raw server body", "raw outage body"):
        assert secret not in text


def test_authentication_failure_stops_the_whole_history_run():
    """12-9: a 401 is not per item — issue_changelogs raises JiraAuthError instead of marking every issue."""
    get, calls = _changelog_by_key({"ALPHA-1": (401, []), "ALPHA-2": (200, [])})
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        with pytest.raises(JiraAuthError):
            Jira().issue_changelogs(["ALPHA-1", "ALPHA-2"])
    assert calls == ["ALPHA-1"]


def test_bad_key_in_a_history_batch_is_unavailable_without_a_request():
    """12-10: an invalid key is reported as unavailable for that key only; no request is made for it."""
    get, calls = _changelog_by_key({"ALPHA-1": (200, [])})
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        outcomes = Jira().issue_changelogs(["ALPHA-1", "not a key"])
    assert outcomes["ALPHA-1"]["status"] == "none"
    assert outcomes["not a key"]["status"] == "unavailable"
    assert "Invalid Jira issue key" in outcomes["not a key"]["reason"]
    assert calls == ["ALPHA-1"]


def test_changelog_entries_are_readable_and_oldest_first():
    """12-11: raw histories become {id, at, author, changes[field, from, to]}, sorted oldest first, no emails or ids."""
    from connectonion.useful_tools.adapter import jira_changelog_entries
    later = _history("2", "2026-10-06T10:00:00.000+1100", "priority", "High", "Highest", who="Ben Example")
    earlier = _history("1", "2026-10-05T10:00:00.000+1100", "status", "To Do", "In Progress")
    earlier["items"][0].update({"from": "10000", "to": "3"})
    entries, skipped = jira_changelog_entries([later, earlier])
    assert skipped == 0
    assert entries == [
        {"id": "1", "at": "2026-10-05T10:00:00.000+1100", "author": "Diego Example",
         "changes": [{"field": "status", "from": "To Do", "to": "In Progress"}]},
        {"id": "2", "at": "2026-10-06T10:00:00.000+1100", "author": "Ben Example",
         "changes": [{"field": "priority", "from": "High", "to": "Highest"}]},
    ]
    assert "diego@example.com" not in json.dumps(entries)


def test_changelog_entry_keeps_every_field_changed_in_one_edit():
    """12-12: one history with several items keeps them all; null values (field set or cleared) become ""."""
    from connectonion.useful_tools.adapter import jira_changelog_entries
    history = {"id": "7", "created": "2026-10-07T09:00:00Z", "author": {"displayName": "Elena Example"},
               "items": [{"field": "assignee", "fromString": None, "toString": "Ben Example"},
                         {"field": "labels", "fromString": "draft", "toString": None}]}
    entries, _ = jira_changelog_entries([history])
    assert entries[0]["changes"] == [{"field": "assignee", "from": "", "to": "Ben Example"},
                                     {"field": "labels", "from": "draft", "to": ""}]


def test_malformed_histories_are_skipped_and_counted():
    """12-13: non-objects, histories without items and items without a field are skipped and counted, not raised."""
    from connectonion.useful_tools.adapter import jira_changelog_entries
    good = _history("1", "2026-10-05T10:00:00Z", "status", "To Do", "Done")
    broken = ["not a history", {"id": "x", "items": "nope"}, {"id": "y", "items": [{"fromString": "a"}, "z"]}]
    entries, skipped = jira_changelog_entries([good, *broken])
    assert [e["id"] for e in entries] == ["1"]
    assert skipped == 3
    assert jira_changelog_entries("not a list") == ([], 0)
    no_author = {"id": "2", "items": [{"field": "status", "toString": "Done"}]}
    assert jira_changelog_entries([no_author])[0][0]["author"] == ""


def test_long_change_values_are_cut_and_marked():
    """12-14: a huge value (e.g. a description edit) is cut to CHANGE_VALUE_CHARS with a visible marker."""
    from connectonion.useful_tools.adapter import CHANGE_VALUE_CHARS, jira_changelog_entries
    big = "x" * (CHANGE_VALUE_CHARS + 250)
    entries, _ = jira_changelog_entries([_history("1", "2026-10-05T10:00:00Z", "description", "short", big)])
    change = entries[0]["changes"][0]
    assert change["from"] == "short"
    assert change["to"] == "x" * CHANGE_VALUE_CHARS + " … [250 more characters]"


def test_changelog_extension_keeps_none_and_unreadable_distinct():
    """12-15: ok / none / restricted / unavailable survive into the record value; unknown outcomes become unavailable."""
    from connectonion.useful_tools.adapter import CHANGELOG_STATUSES, changelog_extension
    from connectonion.useful_tools.jira import CHANGELOG_STATUSES as TOOL_STATUSES
    assert CHANGELOG_STATUSES == TOOL_STATUSES
    edited = [_history("1", "2026-10-05T10:00:00Z", "status", "To Do", "Done"), "broken"]
    ok = changelog_extension({"status": "ok", "histories": edited, "reason": ""})
    assert ok["status"] == "ok" and len(ok["entries"]) == 1 and ok["skipped"] == 1 and "reason" not in ok
    assert changelog_extension({"status": "none", "histories": [], "reason": ""}) == {"status": "none", "entries": []}
    restricted = changelog_extension({"status": "restricted", "histories": [], "reason": "Jira returned 403 for ALPHA-3"})
    assert restricted == {"status": "restricted", "entries": [], "reason": "Jira returned 403 for ALPHA-3"}
    for unknown in (None, {}, {"status": "maybe"}, "ok"):
        assert changelog_extension(unknown)["status"] == "unavailable"


def _outcome(status: str, histories=None, reason: str = "") -> dict:
    return {"status": status, "histories": histories or [], "reason": reason}


def test_each_record_gets_the_history_of_its_own_issue():
    """12-16: histories are matched by issue key, so ALPHA-1's changes never appear on ALPHA-2's record."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    changelogs = {
        "ALPHA-2": _outcome("ok", [_history("20", "2026-10-06T10:00:00Z", "priority", "Low", "High")]),
        "ALPHA-1": _outcome("ok", [_history("10", "2026-10-05T10:00:00Z", "status", "To Do", "Done")]),
    }
    records = jira_issues_to_records([_I1, _I2], JIRA_URL, changelogs=changelogs).records
    by_key = {r["external_id"]: r["extensions"]["changelog"] for r in records}
    assert [e["id"] for e in by_key["ALPHA-1"]["entries"]] == ["10"]
    assert by_key["ALPHA-1"]["entries"][0]["changes"] == [{"field": "status", "from": "To Do", "to": "Done"}]
    assert [e["id"] for e in by_key["ALPHA-2"]["entries"]] == ["20"]


def test_restricted_or_unavailable_history_keeps_the_issue_content():
    """12-17: a 403 or outage on the history still yields a full, valid record; only the changelog says why."""
    from connectonion.useful_tools.adapter import jira_issues_to_records, validate_record
    changelogs = {"ALPHA-77": _outcome("restricted", reason="Jira returned 403 for the change history of ALPHA-77."),
                  "ALPHA-1": _outcome("unavailable", reason="Jira is temporarily unavailable (HTTP 503).")}
    result = jira_issues_to_records([_I_SEARCH, _I1], JIRA_URL, changelogs=changelogs)
    assert result.errors == {}
    restricted, unavailable = result.records
    assert restricted["title"] == "Pagination stops after the first page"
    assert restricted["text"].startswith("## Steps")
    assert restricted["extensions"]["status"] == "Done"
    assert restricted["extensions"]["changelog"] == {
        "status": "restricted", "entries": [], "reason": "Jira returned 403 for the change history of ALPHA-77."}
    assert unavailable["extensions"]["changelog"]["status"] == "unavailable"
    assert all(validate_record(r) == [] for r in result.records)


def test_never_edited_and_unreadable_history_look_different_on_the_record():
    """12-18: "none" (never edited) is stored differently from "unavailable" (not read), including a missing key."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    records = jira_issues_to_records([_I1, _I2], JIRA_URL, changelogs={"ALPHA-1": _outcome("none")}).records
    never_edited, not_collected = (r["extensions"]["changelog"] for r in records)
    assert never_edited == {"status": "none", "entries": []}
    assert not_collected["status"] == "unavailable"
    assert not_collected["reason"]


def test_history_not_requested_adds_no_changelog():
    """12-19: without changelogs (history not requested) records look exactly as in F13FCAKE-11."""
    from connectonion.useful_tools.adapter import jira_issues_to_records
    with_none = jira_issues_to_records([_I_SEARCH], JIRA_URL, fetched=_FETCHED).records[0]
    assert "changelog" not in with_none["extensions"]
    odd = jira_issues_to_records([_I1], JIRA_URL, changelogs=["not", "a", "dict"]).records[0]
    assert odd["extensions"]["changelog"]["status"] == "unavailable"


def test_records_with_history_survive_the_daily_round_trip(tmp_path):
    """12-20: a record carrying its changelog is written to daily/ and read back unchanged and still valid."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, consume_jsonl_records, jira_issues_to_records, validate_record,
    )
    changelogs = {"ALPHA-1": _outcome("ok", [_history("10", "2026-10-05T10:00:00Z", "status", "To Do", "Done")])}
    records = jira_issues_to_records([_I1], JIRA_URL, changelogs=changelogs).records
    append_daily_jsonl(records, tmp_path)
    read_back = consume_jsonl_records(tmp_path).records
    assert read_back == records
    assert validate_record(read_back[0]) == []
    assert read_back[0]["extensions"]["changelog"]["entries"][0]["author"] == "Diego Example"


def _version(updated: str, summary: str) -> dict:
    return {"id": "10001", "key": "ALPHA-1", "fields": {"summary": summary, "updated": updated}}


_DRAFT = _version("2026-10-12T09:00:00.000+0000", "Draft")
_FINAL = _version("2026-10-12T15:30:00.000+0000", "Final")


def test_changed_item_is_appended_as_a_new_version_and_old_lines_stay(tmp_path):
    """12-21: the same issue with a new "updated" is appended below; the same version is skipped; line 1 is untouched."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records
    day = datetime.date(2026, 10, 12)
    draft = jira_issues_to_records([_DRAFT], JIRA_URL).records
    final = jira_issues_to_records([_FINAL], JIRA_URL).records
    path, _ = append_daily_jsonl(draft, tmp_path, date=day, keep_versions=True)
    original = path.read_text(encoding="utf-8")
    assert append_daily_jsonl(final, tmp_path, date=day, keep_versions=True) == (path, 1)
    assert append_daily_jsonl(final + draft, tmp_path, date=day, keep_versions=True) == (path, 0)
    stored = path.read_text(encoding="utf-8")
    assert stored.startswith(original)
    assert [json.loads(line)["title"] for line in stored.splitlines()] == ["Draft", "Final"]
    assert {json.loads(line)["id"] for line in stored.splitlines()} == {"jira:test.atlassian.net:ALPHA-1"}


def test_a_new_day_never_rewrites_the_previous_day(tmp_path):
    """12-22: a change synced the next day goes into that day's file; the previous day's file is byte-for-byte unchanged."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records
    draft = jira_issues_to_records([_DRAFT], JIRA_URL).records
    final = jira_issues_to_records([_FINAL], JIRA_URL).records
    yesterday, _ = append_daily_jsonl(draft, tmp_path, date=datetime.date(2026, 10, 11), keep_versions=True)
    before = yesterday.read_bytes()
    today, appended = append_daily_jsonl(final, tmp_path, date=datetime.date(2026, 10, 12), keep_versions=True)
    assert appended == 1
    assert yesterday.read_bytes() == before
    assert today.relative_to(tmp_path).as_posix() == "daily/2026/10/12.jsonl"


def test_record_reference_is_notebook_relative_file_and_id(tmp_path):
    """12-23: a reference carries the notebook-relative path, id and snapshot version; outside paths/no id raise."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records, record_reference
    notebook = tmp_path / "notebook"
    record = jira_issues_to_records([_I1], JIRA_URL).records[0]
    path, _ = append_daily_jsonl([record], notebook / "docs" / "jira", date=datetime.date(2026, 10, 12))
    reference = record_reference(path, record, notebook)
    assert reference.partition("?version=")[0] == (
        "docs/jira/daily/2026/10/12.jsonl#jira:test.atlassian.net:ALPHA-1")
    assert "?version=" in reference
    with pytest.raises(ValueError, match="outside the notebook"):
        record_reference(tmp_path / "elsewhere.jsonl", record, notebook)
    with pytest.raises(ValueError, match="no id"):
        record_reference(path, {"title": "no id"}, notebook)


def test_resolve_reference_returns_the_cited_version_or_none(tmp_path):
    """12-24: the cited snapshot wins; unknown id, missing file, no "#" or a path escaping the notebook give None."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, jira_issues_to_records, record_reference, resolve_reference,
    )
    notebook = tmp_path / "notebook"
    jira_dir = notebook / "docs" / "jira"
    day = datetime.date(2026, 10, 12)
    draft = jira_issues_to_records([_DRAFT], JIRA_URL).records
    final = jira_issues_to_records([_FINAL], JIRA_URL).records
    path, _ = append_daily_jsonl(draft, jira_dir, date=day, keep_versions=True)
    append_daily_jsonl(final, jira_dir, date=day, keep_versions=True)
    with open(path, "a", encoding="utf-8") as fh:
        fh.write("{damaged line\n")
    reference = record_reference(path, draft[0], notebook)
    assert resolve_reference(reference, notebook) == draft[0]
    assert resolve_reference(record_reference(path, final[0], notebook), notebook) == final[0]
    (tmp_path / "outside.jsonl").write_text(json.dumps(draft[0]) + "\n", encoding="utf-8")
    for broken in (reference.replace("ALPHA-1", "ALPHA-404"), reference.replace("12.jsonl", "13.jsonl"),
                   reference.split("#")[0], "../outside.jsonl#" + draft[0]["id"], ""):
        assert resolve_reference(broken, notebook) is None


def test_references_also_point_into_one_off_exports(tmp_path):
    """12-25: a record in exports/<id>/records.jsonl is cited and resolved the same way as a daily record."""
    from connectonion.useful_tools.adapter import (
        jira_issues_to_records, record_reference, resolve_reference, write_export,
    )
    notebook = tmp_path / "notebook"
    records = jira_issues_to_records([_I1, _I2], JIRA_URL, fetched=_FETCHED).records
    export_dir = write_export(records, notebook / "docs" / "jira", "alpha-history", _SCOPE)
    reference = record_reference(export_dir / "records.jsonl", records[1], notebook)
    assert reference.partition("?version=")[0] == "docs/jira/exports/alpha-history/records.jsonl#jira:test.atlassian.net:ALPHA-2"
    assert resolve_reference(reference, notebook) == records[1]


def _sync_site(args: list[str], issues: list[dict], histories: dict):
    """Run co jira sync against the real Jira tool; only HTTP is faked: /myself, one search page, each issue's history.

    histories: {issue key: (http status, raw histories)}. Returns (result, calls).
    """
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    routes = {"/rest/api/3/myself": (200, {"accountId": "abc", "displayName": "Alice"}),
              "/rest/api/3/search/jql": (200, {"issues": issues})}
    for key, (status, values) in histories.items():
        routes[f"/rest/api/3/issue/{key}/changelog"] = (status, {"values": values, "isLast": True})
    get, calls = _mock_get(routes)
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = CliRunner().invoke(app, ["jira", "sync", *args])
    return result, calls


_EDITED = [_history("10", "2026-10-05T10:00:00.000+1100", "status", "To Do", "Done")]


def test_sync_attaches_each_issue_history_end_to_end(tmp_path):
    """12-26: co jira sync puts ok / none / restricted history on the right records; a 403 keeps the issue; exit 0."""
    from connectonion.useful_tools.adapter import consume_jsonl_records, validate_record
    result, calls = _sync_site(
        ["ALPHA", "ALPHA-1", "ALPHA-2", "ALPHA-3", "--output", str(tmp_path), "--json"], [_I1, _I2, _I3],
        {"ALPHA-1": (200, _EDITED), "ALPHA-2": (200, []), "ALPHA-3": (403, [])})
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["history"] == {"ok": 1, "none": 1, "restricted": 1, "unavailable": 0}
    assert data["rejected"] == {}
    by_key = {r["external_id"]: r for r in consume_jsonl_records(tmp_path).records}
    assert by_key["ALPHA-1"]["extensions"]["changelog"]["entries"][0]["changes"] == [
        {"field": "status", "from": "To Do", "to": "Done"}]
    assert by_key["ALPHA-2"]["extensions"]["changelog"] == {"status": "none", "entries": []}
    assert by_key["ALPHA-3"]["extensions"]["changelog"]["status"] == "restricted"
    assert by_key["ALPHA-3"]["title"] == "Third issue"
    assert all(validate_record(r) == [] for r in by_key.values())
    assert sum(c["url"].endswith("/changelog") for c in calls) == 3
    assert JIRA_TOKEN not in result.output and "diego@example.com" not in json.dumps(list(by_key.values()))


def test_resync_after_an_edit_keeps_both_versions_and_the_reference_trail(tmp_path):
    """12-27: a re-sync after an edit appends a version; the old line and its citation remain unchanged."""
    from connectonion.useful_tools.adapter import record_reference, resolve_reference
    jira_dir = tmp_path / "docs" / "jira"
    args = ["ALPHA", "ALPHA-1", "--output", str(jira_dir), "--json"]
    first, _ = _sync_site(args, [_DRAFT], {"ALPHA-1": (200, [])})
    path = Path(json.loads(first.stdout)["jsonl"])
    draft_line = path.read_text(encoding="utf-8")
    reference = record_reference(path, json.loads(draft_line), tmp_path)
    second, _ = _sync_site(args, [_FINAL], {"ALPHA-1": (200, _EDITED)})
    assert json.loads(second.stdout)["appended"] == 1
    assert path.read_text(encoding="utf-8").startswith(draft_line)
    draft, final = (json.loads(line) for line in path.read_text(encoding="utf-8").splitlines())
    assert (draft["title"], draft["extensions"]["changelog"]["status"]) == ("Draft", "none")
    assert (final["title"], final["extensions"]["changelog"]["status"]) == ("Final", "ok")
    assert reference.startswith("docs/jira/daily/") and "#jira:test.atlassian.net:ALPHA-1?version=" in reference
    assert resolve_reference(reference, tmp_path) == draft
    assert resolve_reference(record_reference(path, final, tmp_path), tmp_path) == final


def test_no_history_skips_the_history_reads(tmp_path):
    """12-28: --no-history makes no changelog request; records carry no changelog; the summary says so."""
    result, calls = _sync_site(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--json", "--no-history"], [_I1], {})
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    assert data["history"] is None
    record = json.loads(Path(data["jsonl"]).read_text(encoding="utf-8"))
    assert "changelog" not in record["extensions"]
    assert not any(c["url"].endswith("/changelog") for c in calls)
    text, _ = _sync_site(["ALPHA", "ALPHA-1", "--output", str(tmp_path), "--no-history"], [_I1], {})
    assert "History: not requested (--no-history)" in text.stdout


def test_authentication_failure_while_reading_history_writes_nothing(tmp_path):
    """12-29: a 401 on the history stops the whole sync before source.yaml or any record is written; exit 1."""
    result, _ = _sync_site(["ALPHA", "ALPHA-1", "ALPHA-2", "--output", str(tmp_path), "--json"], [_I1, _I2],
                           {"ALPHA-1": (401, []), "ALPHA-2": (200, [])})
    assert result.exit_code == 1
    assert json.loads(result.stdout)["exit_code"] == 1
    assert JIRA_TOKEN not in result.output
    assert list(tmp_path.iterdir()) == []


def test_text_summary_counts_history_and_explains_unreadable_ones(tmp_path):
    """12-30: the text summary lists each history status and says unreadable history did not drop the issue."""
    result, _ = _sync_site(["ALPHA", "ALPHA-1", "ALPHA-2", "--output", str(tmp_path)], [_I1, _I2],
                           {"ALPHA-1": (200, _EDITED), "ALPHA-2": (500, [])})
    assert result.exit_code == 0, result.output
    assert "History: ok 1  none 0  restricted 0  unavailable 1" in result.stdout
    assert "were still written" in result.stdout
    clean, _ = _sync_site(["ALPHA", "ALPHA-1", "--output", str(tmp_path / "clean")], [_I1], {"ALPHA-1": (200, [])})
    assert "History: ok 0  none 1  restricted 0  unavailable 0" in clean.stdout
    assert "were still written" not in clean.stdout


def test_reference_keeps_its_snapshot_after_same_day_update(tmp_path):
    """12-31: citing a draft before an edit keeps pointing to that draft after the final version is appended."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, jira_issues_to_records, record_reference, resolve_reference,
    )
    notebook = tmp_path / "notebook"
    source_dir = notebook / "docs" / "jira"
    day = datetime.date(2026, 10, 12)
    draft = jira_issues_to_records([_DRAFT], JIRA_URL, fetched=_FETCHED).records[0]
    final = jira_issues_to_records([_FINAL], JIRA_URL, fetched=_FETCHED).records[0]
    path, _ = append_daily_jsonl([draft], source_dir, date=day, keep_versions=True)
    reference = record_reference(path, draft, notebook)
    assert resolve_reference(reference, notebook) == draft
    append_daily_jsonl([final], source_dir, date=day, keep_versions=True)
    assert resolve_reference(reference, notebook) == draft
    assert record_reference(path, final, notebook) != reference
    assert resolve_reference(record_reference(path, final, notebook), notebook) == final


@pytest.mark.parametrize("first_status", [None, "restricted", "unavailable", "none"])
def test_recovered_history_is_appended_without_an_issue_edit(tmp_path, first_status):
    """12-32: retry after --no-history or an unreadable history saves new evidence even with the same updated time."""
    from connectonion.useful_tools.adapter import record_reference, resolve_reference
    source_dir = tmp_path / "docs" / "jira"
    args = ["ALPHA", "ALPHA-1", "--output", str(source_dir), "--json"]
    statuses = {"restricted": 403, "unavailable": 500, "none": 200}
    first_args = args + ["--no-history"] if first_status is None else args
    first_histories = {} if first_status is None else {"ALPHA-1": (statuses[first_status], [])}
    first, _ = _sync_site(first_args, [_DRAFT], first_histories)
    assert first.exit_code == 0, first.output
    path = Path(json.loads(first.stdout)["jsonl"])
    original = path.read_bytes()
    old_record = json.loads(original)
    reference = record_reference(path, old_record, tmp_path)

    second, _ = _sync_site(args, [_DRAFT], {"ALPHA-1": (200, _EDITED)})
    assert second.exit_code == 0, second.output
    assert json.loads(second.stdout)["appended"] == 1
    assert path.read_bytes().startswith(original)
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 2
    assert records[0]["id"] == records[1]["id"]
    assert records[0]["updated"] == records[1]["updated"]
    assert records[1]["extensions"]["changelog"]["status"] == "ok"
    assert records[1]["extensions"]["changelog"]["entries"]
    assert resolve_reference(reference, tmp_path) == old_record
    third, _ = _sync_site(args, [_DRAFT], {"ALPHA-1": (200, _EDITED)})
    assert third.exit_code == 0, third.output
    assert json.loads(third.stdout)["appended"] == 0


def test_fetch_time_alone_does_not_append_or_change_the_citation(tmp_path):
    """12-33: an identical re-read with a later fetched time deduplicates and still cites the stored snapshot."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, jira_issues_to_records, record_reference, resolve_reference,
    )
    first = jira_issues_to_records([_DRAFT], JIRA_URL, fetched="2026-10-12T10:00:00Z").records[0]
    reread = jira_issues_to_records([_DRAFT], JIRA_URL, fetched="2026-10-12T11:00:00Z").records[0]
    path, _ = append_daily_jsonl([first], tmp_path, date=datetime.date(2026, 10, 12), keep_versions=True)
    assert append_daily_jsonl([reread], tmp_path, date=datetime.date(2026, 10, 12), keep_versions=True) == (path, 0)
    assert record_reference(path, first, tmp_path) == record_reference(path, reread, tmp_path)
    assert resolve_reference(record_reference(path, reread, tmp_path), tmp_path) == first


def test_legacy_reference_keeps_the_first_stored_record(tmp_path):
    """12-34: the old file#id format still reads, but later appends cannot move it to a new version."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records, resolve_reference
    draft = jira_issues_to_records([_DRAFT], JIRA_URL, fetched=_FETCHED).records[0]
    final = jira_issues_to_records([_FINAL], JIRA_URL, fetched=_FETCHED).records[0]
    day = datetime.date(2026, 10, 12)
    path, _ = append_daily_jsonl([draft], tmp_path, date=day, keep_versions=True)
    reference = f"{path.relative_to(tmp_path).as_posix()}#{draft['id']}"
    assert resolve_reference(reference, tmp_path) == draft
    append_daily_jsonl([final], tmp_path, date=day, keep_versions=True)
    assert resolve_reference(reference, tmp_path) == draft


@pytest.mark.parametrize("version", ["", "short", "g" * 64, "0" * 64])
def test_missing_or_invalid_snapshot_never_falls_back_to_another_record(tmp_path, version):
    """12-35: a damaged or absent version must return None even when the file contains the requested id."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, jira_issues_to_records, record_reference, resolve_reference,
    )
    record = jira_issues_to_records([_DRAFT], JIRA_URL, fetched=_FETCHED).records[0]
    path, _ = append_daily_jsonl([record], tmp_path, date=datetime.date(2026, 10, 12), keep_versions=True)
    prefix = record_reference(path, record, tmp_path).rpartition("?version=")[0]
    assert resolve_reference(f"{prefix}?version={version}", tmp_path) is None


def test_record_key_order_does_not_create_a_new_snapshot(tmp_path):
    """12-36: reordered JSON objects represent the same content, so re-reading them does not duplicate evidence."""
    from connectonion.useful_tools.adapter import append_daily_jsonl, jira_issues_to_records, record_reference
    record = jira_issues_to_records([_DRAFT], JIRA_URL, changelogs={"ALPHA-1": _outcome("ok", _EDITED)},
                                   fetched=_FETCHED).records[0]
    reordered = dict(reversed(list(record.items())))
    reordered["extensions"] = dict(reversed(list(record["extensions"].items())))
    day = datetime.date(2026, 10, 12)
    path, _ = append_daily_jsonl([record], tmp_path, date=day, keep_versions=True)
    assert append_daily_jsonl([reordered], tmp_path, date=day, keep_versions=True) == (path, 0)
    assert record_reference(path, record, tmp_path) == record_reference(path, reordered, tmp_path)


def test_more_history_at_the_same_updated_time_keeps_both_snapshots(tmp_path):
    """12-37: history can gain entries without an issue edit; old citations and old lines stay intact."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, jira_issues_to_records, record_reference, resolve_reference,
    )
    histories = _EDITED + [_history("11", "2026-10-06T10:00:00Z", "priority", "Low", "High")]
    partial = jira_issues_to_records([_DRAFT], JIRA_URL, changelogs={"ALPHA-1": _outcome("ok", _EDITED)},
                                 fetched=_FETCHED).records[0]
    complete = jira_issues_to_records([_DRAFT], JIRA_URL, changelogs={"ALPHA-1": _outcome("ok", histories)},
                                  fetched=_FETCHED).records[0]
    day = datetime.date(2026, 10, 12)
    path, count = append_daily_jsonl([partial, complete, partial, complete], tmp_path, date=day, keep_versions=True)
    assert count == 2
    assert append_daily_jsonl([partial, complete], tmp_path, date=day, keep_versions=True) == (path, 0)
    assert resolve_reference(record_reference(path, partial, tmp_path), tmp_path) == partial
    assert resolve_reference(record_reference(path, complete, tmp_path), tmp_path) == complete


def test_shared_writer_keeps_onenote_same_day_rule_and_its_citation(tmp_path):
    """12-38: by default a changed same-day OneNote page is skipped (its import rule); versions are opt-in only."""
    from connectonion.useful_tools.adapter import (
        append_daily_jsonl, media_ref, onenote_pages_to_records, record_reference, resolve_reference,
    )
    page = dict(_ON1, scope={"sections": ["design"]}, media_fetch_references={"diagram": "attachment-1"})
    first = onenote_pages_to_records([page], "fictional-account", fetched=_FETCHED).records[0]
    enriched = onenote_pages_to_records(
        [dict(page, media=[media_ref("diagram.png", "image/png", 2048)])], "fictional-account", fetched=_FETCHED,
    ).records[0]
    day = datetime.date(2026, 10, 12)
    path, _ = append_daily_jsonl([first], tmp_path, date=day)
    reference = record_reference(path, first, tmp_path)
    assert append_daily_jsonl([enriched], tmp_path, date=day) == (path, 0)
    assert resolve_reference(reference, tmp_path) == first
    assert first["extensions"]["scope"] == {"sections": ["design"]}
    assert first["extensions"]["media_fetch_references"] == {"diagram": "attachment-1"}
    assert append_daily_jsonl([enriched], tmp_path, date=day, keep_versions=True) == (path, 1)
    assert resolve_reference(reference, tmp_path) == first
    assert resolve_reference(record_reference(path, enriched, tmp_path), tmp_path) == enriched
