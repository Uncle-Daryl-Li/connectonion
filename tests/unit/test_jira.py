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
    assert "co env set JIRA_API_TOKEN" in msg


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


def test_issue_items_empty_result_returns_empty_list():
    """10-9: issues=[] + no nextPageToken -> returns []; no exception."""
    get, _ = _mock_get(
        {"/rest/api/3/search/jql": (200, {"issues": []})}
    )
    with patch("connectonion.useful_tools.jira.httpx.get", get):
        result = Jira().issue_items("ALPHA", {"ALPHA-1"})
    assert result == []


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
