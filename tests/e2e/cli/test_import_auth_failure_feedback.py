"""Sprint 1 acceptance: safe authentication feedback for both import sources.

Only HTTP transport is mocked. The CLI, credential checks and import paths run
normally against temporary files; no live account or provider is contacted.
"""

import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from connectonion.cli.main import app
from connectonion.useful_tools.adapter import make_record


JIRA_SITE = "https://sprint1-test.atlassian.net"
JIRA_TOKEN = "sprint1-fake-jira-token-never-disclose"
MICROSOFT_TOKEN = "sprint1-fake-microsoft-token-never-disclose"
PRIVATE_DETAIL = "sprint1-private-provider-response"
SECRETS = (JIRA_TOKEN, MICROSOFT_TOKEN, PRIVATE_DETAIL)


@pytest.fixture(params=["jira", "onenote"])
def rejected_import(request, monkeypatch, tmp_path):
    """Return a selected import whose real HTTP boundary always returns 401."""
    source = request.param
    calls = []
    if source == "jira":
        _reject_jira(monkeypatch, calls)
        args = ["jira", "sync", "ALPHA", "ALPHA-1", "--no-history"]
        identifiers = ("ALPHA", "ALPHA-1")
    else:
        _reject_onenote(monkeypatch, calls)
        args = ["onenote", "source", "sync", "nb-test", "section-test", "page-test"]
        identifiers = ("nb-test", "section-test", "page-test")
    output = tmp_path / source
    return {
        "source": source,
        "args": [*args, "--output", str(output)],
        "identifiers": identifiers,
        "output": output,
        "calls": calls,
    }


def _reject_jira(monkeypatch, calls):
    monkeypatch.setenv("JIRA_URL", JIRA_SITE)
    monkeypatch.setenv("JIRA_EMAIL", "sprint1-test@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", JIRA_TOKEN)

    def get(url, **kwargs):
        assert url.startswith(JIRA_SITE + "/rest/api/3/")
        assert kwargs["auth"] == ("sprint1-test@example.com", JIRA_TOKEN)
        calls.append(url)
        return httpx.Response(
            401, request=httpx.Request("GET", url),
            json={"errorMessages": [PRIVATE_DETAIL, JIRA_TOKEN]},
        )

    monkeypatch.setattr(httpx, "get", get)


def _reject_onenote(monkeypatch, calls):
    monkeypatch.setenv("MICROSOFT_ACCESS_TOKEN", MICROSOFT_TOKEN)
    monkeypatch.setenv("MICROSOFT_SCOPES", "Notes.Read User.Read")
    monkeypatch.delenv("MICROSOFT_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("MICROSOFT_TOKEN_EXPIRES_AT", raising=False)

    def request(method, url, **kwargs):
        assert method == "GET"
        assert url.startswith("https://graph.microsoft.com/v1.0/")
        assert kwargs["headers"]["Authorization"] == f"Bearer {MICROSOFT_TOKEN}"
        calls.append(url)
        return httpx.Response(
            401, request=httpx.Request(method, url),
            json={"error": {"message": PRIVATE_DETAIL + " " + MICROSOFT_TOKEN}},
        )

    monkeypatch.setattr(httpx, "request", request)


def _invoke(case, *, as_json):
    args = [*case["args"], *(["--json"] if as_json else [])]
    result = CliRunner().invoke(app, args)
    assert case["calls"], "The simulated authentication failure was not reached"
    assert result.exit_code == 1, result.output
    return result


def _json_failure(case):
    result = _invoke(case, as_json=True)
    data = json.loads(result.stdout)
    assert isinstance(data, dict)
    assert isinstance(data.get("error"), str) and data["error"].strip()
    assert data.get("exit_code") == result.exit_code
    return result, data


def _assert_no_secrets(result, caplog):
    for text in (result.stdout, result.stderr, caplog.text):
        for secret in SECRETS:
            assert secret not in text
        assert "Traceback (most recent call last)" not in text


def test_auth_failure_json_identifies_source_and_selected_scope(rejected_import):
    """AC: scripts can identify the affected source and requested import scope."""
    _, data = _json_failure(rejected_import)
    assert data.get("source") == rejected_import["source"], (
        "JSON authentication errors must identify the affected import source"
    )
    scope = data.get("scope")
    assert isinstance(scope, dict), "JSON authentication errors must include a scope object"
    scope_values = json.dumps(scope)
    for identifier in rejected_import["identifiers"]:
        assert json.dumps(identifier) in scope_values, f"Missing selected identifier: {identifier}"


def test_auth_failure_json_has_stable_code_and_recovery_action(rejected_import):
    """AC: error classification and recovery are structured, not parsed from prose."""
    _, first = _json_failure(rejected_import)
    _, second = _json_failure(rejected_import)
    code = first.get("error_code")
    assert isinstance(code, str) and code.strip(), "A machine-readable error_code is required"
    assert second.get("error_code") == code
    action = first.get("recovery_action")
    assert isinstance(action, str) and action.strip(), "An actionable recovery_action is required"
    assert second.get("recovery_action") == action
    if rejected_import["source"] == "jira":
        assert "JIRA_API_TOKEN" in action
        assert "https://id.atlassian.com/manage-profile/security/api-tokens" in action
    else:
        assert "co auth microsoft" in action


def test_auth_failure_json_is_safe_and_separate_from_human_output(rejected_import, caplog):
    """AC: one parseable JSON object, documented non-zero exit and no secret leakage."""
    result, _ = _json_failure(rejected_import)
    assert not result.stderr, "JSON mode must keep recovery information in the JSON object"
    _assert_no_secrets(result, caplog)


def test_auth_failure_human_feedback_names_scope_and_recovery(rejected_import, caplog):
    """AC: a person sees the failed scope and a recovery action on stderr."""
    result = _invoke(rejected_import, as_json=False)
    assert result.stdout == "", "Human-readable authentication errors belong on stderr"
    _assert_no_secrets(result, caplog)
    message = result.stderr
    assert rejected_import["source"] in message.lower()
    for identifier in rejected_import["identifiers"]:
        assert identifier in message, f"Human feedback omits affected scope: {identifier}"
    if rejected_import["source"] == "jira":
        assert "JIRA_API_TOKEN" in message
    else:
        assert "co auth microsoft" in message


def test_auth_failure_preserves_existing_import_files(rejected_import, caplog):
    """AC: a failed retry leaves prior records/config intact and creates no new results."""
    output = rejected_import["output"]
    daily = output / "daily" / "2026" / "10" / "12.jsonl"
    daily.parent.mkdir(parents=True)
    source = rejected_import["source"]
    record = make_record(source, "https://existing.example", "existing-item", "note",
                         title="Previously imported", text="Keep this valid result")
    daily.write_text(json.dumps(record) + "\n", encoding="utf-8")
    (output / "source.yaml").write_text(f"driver: {source}\nread_only: true\n", encoding="utf-8")
    before = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    result, data = _json_failure(rejected_import)
    after = {path.relative_to(output): path.read_bytes() for path in output.rglob("*") if path.is_file()}
    assert after == before, "Authentication failure must not modify existing import files"
    assert not any(data.get(name) for name in ("written", "appended", "jsonl", "source_yaml"))
    _assert_no_secrets(result, caplog)
    for content in after.values():
        for secret in SECRETS:
            assert secret.encode() not in content


def _onenote_page(page_id):
    return {"id": page_id, "title": page_id,
            "parentNotebook": {"id": "nb-test"}, "parentSection": {"id": "section-test"}}


@pytest.fixture(params=["metadata", "content"])
def interrupted_onenote(request, monkeypatch):
    """Import one page, then lose authentication reading the next page."""
    calls = []
    _reject_onenote(monkeypatch, calls)
    routes = {
        "/me": {"id": "test-account"},
        "/me/onenote/notebooks": {"value": [{"id": "nb-test", "displayName": "Test"}]},
        "/me/onenote/notebooks/nb-test/sections": {"value": [{"id": "section-test"}]},
        "/me/onenote/notebooks/nb-test/sectionGroups": {"value": []},
    }
    for page_id in ("page-good", "page-auth", "page-later"):
        routes[f"/me/onenote/pages/{page_id}"] = _onenote_page(page_id)
        routes[f"/me/onenote/pages/{page_id}/content"] = f"<html><body>{page_id}</body></html>"
    failed_path = "/me/onenote/pages/page-auth" + ("/content" if request.param == "content" else "")
    normal_response = routes[failed_path]
    routes[failed_path] = (401, {"error": {"message": PRIVATE_DETAIL + " " + MICROSOFT_TOKEN}})
    _serve_onenote_routes(monkeypatch, routes, calls)
    return {"routes": routes, "calls": calls, "failed_path": failed_path, "normal_response": normal_response}


def _serve_onenote_routes(monkeypatch, routes, calls):
    def request(method, url, **kwargs):
        assert method == "GET"
        assert kwargs["headers"]["Authorization"] == f"Bearer {MICROSOFT_TOKEN}"
        calls.append(url)
        path = url.removeprefix("https://graph.microsoft.com/v1.0")
        body = routes[path]
        status, body = body if isinstance(body, tuple) else (200, body)
        payload = ({"text": body, "headers": {"Content-Type": "text/html"}}
                   if isinstance(body, str) else {"json": body})
        return httpx.Response(status, request=httpx.Request(method, url), **payload)

    monkeypatch.setattr(httpx, "request", request)


def _interrupted_args(output, mode, export_id="interrupted"):
    args = ["onenote", "source", "sync", "nb-test", "section-test",
            "page-good", "page-auth", "page-later", "--output", str(output), "--mode", mode]
    return [*args, "--export-id", export_id] if mode == "export" else args


@pytest.mark.parametrize("mode", ["daily", "export"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_onenote_mid_import_auth_failure_keeps_new_records(interrupted_onenote, tmp_path, mode, as_json, caplog):
    """AC: authentication loss must not discard this batch's already processed page."""
    args = _interrupted_args(tmp_path, mode)
    result = CliRunner().invoke(app, [*args, *(["--json"] if as_json else [])])
    assert result.exit_code == 1, result.output
    files = list(tmp_path.rglob("*.jsonl"))
    assert len(files) == 1, "The successfully processed page must be saved before reporting auth failure"
    records = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    assert [record["external_id"] for record in records] == ["page-good"]
    assert not any("/pages/page-later" in url for url in interrupted_onenote["calls"])
    _assert_no_secrets(result, caplog)
    if as_json:
        _assert_saved_auth_failure(result, files[0])
    else:
        assert result.stdout == ""
        assert "Valid records retained: 1" in result.stderr
        assert "Not imported: page-auth, page-later" in result.stderr
        assert "co auth microsoft" in result.stderr
        retry_hint = "new --export-id" if mode == "export" else "same --output"
        assert retry_hint in result.stderr


def _assert_saved_auth_failure(result, saved_file):
    data = json.loads(result.stdout)
    assert not result.stderr
    assert data["source"] == "onenote" and data["exit_code"] == 1
    assert data["error_code"] in {"reauth_required", "incomplete_record"}
    assert data["recovery_action"] == "co auth microsoft"
    assert data["written"] == data["records"] == 1
    assert Path(data["jsonl"]) == saved_file
    assert set(data["errors"]) == {"page-auth", "page-later"}
    for reason in data["errors"].values():
        assert "co auth microsoft" in reason
    assert set(data["scope"]["pages"]) == {"page-good", "page-auth", "page-later"}
    assert "retry the same notebook/section/page selection" in data["note"]
    retry_hint = "new --export-id" if data["mode"] == "export" else "same --output"
    assert retry_hint in data["note"]


@pytest.mark.parametrize("mode", ["daily", "export"])
def test_onenote_restored_auth_retries_without_losing_partial_records(interrupted_onenote, tmp_path, mode, caplog):
    """AC: restoring provider access and retrying retains the first result without duplicates."""
    first = CliRunner().invoke(app, [*_interrupted_args(tmp_path, mode), "--json"])
    assert first.exit_code == 1, first.output
    first_data = json.loads(first.stdout)
    assert first_data.get("written") == 1, "A recoverable auth failure must report retained new records"
    original = Path(first_data["jsonl"])
    original_bytes = original.read_bytes()
    case = interrupted_onenote
    case["routes"][case["failed_path"]] = case["normal_response"]
    retry = CliRunner().invoke(app, [*_interrupted_args(tmp_path, mode, "recovered"), "--json"])
    assert retry.exit_code == 0, retry.output
    data = json.loads(retry.stdout)
    assert not data["errors"] and "error" not in data
    records = [json.loads(line) for line in Path(data["jsonl"]).read_text(encoding="utf-8").splitlines()]
    assert len(records) == 3 and {r["external_id"] for r in records} == {"page-good", "page-auth", "page-later"}
    assert data["written"] == (2 if mode == "daily" else 3)
    assert data["skipped"] == (1 if mode == "daily" else 0)
    assert original.read_bytes().startswith(original_bytes)
    if mode == "export":
        assert original.read_bytes() == original_bytes
    _assert_no_secrets(first, caplog)
    _assert_no_secrets(retry, caplog)
