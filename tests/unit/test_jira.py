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
    """18-2: jira_issues_to_records produces AdapterResult with source/account/id/url/date."""
    from connectonion.useful_tools.adapter import AdapterResult, jira_issues_to_records
    result = jira_issues_to_records([_I1, _I2], JIRA_URL)
    assert isinstance(result, AdapterResult)
    assert len(result.records) == 2
    for record in result.records:
        assert record["source"] == "jira"
        assert record["account"] == JIRA_URL
        assert "id" in record
        assert "url" in record
        assert "date" in record


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
        assert "date" in record


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
    assert sum(1 for r in all_records if r["id"] == "ABC-123") == 2


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
    assert all(r["id"] == "ABC-123" for r in all_records)


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
        assert "date" in rec
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
        assert "date" in rec
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
    assert "summary" in line
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
    assert rec["id"] == "ALPHA-1"
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
    ids = {json.loads(l)["id"] for l in lines}
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
        assert r["date"] is not None


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
