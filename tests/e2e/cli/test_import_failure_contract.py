"""Story acceptance beyond authentication: real CLI/source paths, synthetic HTTP."""

import json
import math
from time import perf_counter

import httpx
import pytest
from typer.testing import CliRunner

from connectonion.cli.main import app
from connectonion.useful_tools.adapter import make_record


SITE = "https://sprint1-test.atlassian.net"
GRAPH = "https://graph.microsoft.com/v1.0"
SECRET = "synthetic-private-token-never-print"


@pytest.fixture(params=["jira", "onenote"])
def service(request, monkeypatch):
    """All network boundaries are GET-only; no real credentials or sleeps."""
    source = request.param
    monkeypatch.setenv("JIRA_URL", SITE)
    monkeypatch.setenv("JIRA_EMAIL", "test@example.invalid")
    monkeypatch.setenv("JIRA_API_TOKEN", SECRET)
    monkeypatch.setenv("MICROSOFT_ACCESS_TOKEN", SECRET)
    monkeypatch.setenv("MICROSOFT_SCOPES", "Notes.Read User.Read")
    monkeypatch.delenv("MICROSOFT_REFRESH_TOKEN", raising=False)
    state = {"source": source, "failure": None, "count": 1, "calls": [], "waits": []}
    monkeypatch.setattr("connectonion.useful_tools.outlook.time.sleep", state["waits"].append)

    def respond(method, url, **kwargs):
        assert method == "GET", "Imports must never write to a provider"
        state["calls"].append((url, kwargs.get("params", {})))
        path = url.removeprefix(SITE + "/rest/api/3/") if source == "jira" else url.removeprefix(GRAPH)
        target = "search/jql" if source == "jira" else "/me/onenote/notebooks"
        if path.split("?")[0] == target and state["failure"]:
            return _failed_response(state["failure"], url, source)
        if path in state.get("overrides", {}):
            payload = state["overrides"][path]
        else:
            payload = _jira_response(path, kwargs.get("params", {}), state["count"]) if source == "jira" else _graph_response(path)
        if state.get("partial") is not None:
            if source == "jira" and path == "search/jql":
                for row in payload["issues"]:
                    if state["partial"] or row["key"] == "ALPHA-2":
                        row["fields"] = SECRET
            elif source == "onenote" and path.endswith("/content") and (state["partial"] or "/page-2/" in path):
                if state.get("partial_problem"):
                    return _failed_response(state["partial_problem"], url, source)
                return httpx.Response(200, request=httpx.Request(method, url), json={"invalid": SECRET})
        if isinstance(payload, str):
            return httpx.Response(200, request=httpx.Request(method, url), text=payload,
                                  headers={"Content-Type": "text/html"})
        return httpx.Response(200, request=httpx.Request(method, url), json=payload)

    monkeypatch.setattr(httpx, "get", lambda url, **kwargs: respond("GET", url, **kwargs))
    monkeypatch.setattr(httpx, "request", respond)
    return state


def _failed_response(failure, url, source):
    if failure == "network":
        raise httpx.ConnectError(SECRET, request=httpx.Request("GET", url))
    if failure in {"permission", "rate"}:
        return httpx.Response(403 if failure == "permission" else 429,
                              request=httpx.Request("GET", url), json={"error": SECRET},
                              headers={"Retry-After": "1"})
    if failure == "pagination":
        payload = ({"issues": [], "nextPageToken": SECRET} if source == "jira" else
                   {"value": [{"id": "nb-test"}], "@odata.nextLink": url})
    else:
        payload = [SECRET]  # Valid JSON, invalid metadata shape.
    return httpx.Response(200, request=httpx.Request("GET", url), json=payload)


def _jira_response(path, params, count):
    if path == "myself":
        return {"accountId": "fixture-account", "displayName": "Fixture"}
    assert path == "search/jql", path
    start = int(params.get("nextPageToken", "0"))
    end = min(start + 50, count)
    payload = {"issues": [{"id": str(n), "key": f"ALPHA-{n}",
                            "fields": {"summary": f"Synthetic issue {n}"}}
                           for n in range(start + 1, end + 1)]}
    if end < count:
        payload["nextPageToken"] = str(end)
    return payload


def _graph_response(path):
    if path == "/me":
        return {"id": "fixture-account"}
    if path == "/me/onenote/notebooks":
        return {"value": [{"id": "nb-test", "displayName": "Synthetic notebook"}]}
    if path.endswith("/sections"):
        return {"value": [{"id": "section-test", "displayName": "Synthetic section"}]}
    if path.endswith("/sectionGroups"):
        return {"value": []}
    if path.endswith("/content"):
        return "<html><body>中文 synthetic import</body></html>"
    page_id = path.rsplit("/", 1)[-1]
    assert path.startswith("/me/onenote/pages/"), path
    return {"id": page_id, "title": page_id, "parentNotebook": {"id": "nb-test"},
            "parentSection": {"id": "section-test"}}


def _args(source, output, count=1):
    selection = (["jira", "sync", "ALPHA", *[f"ALPHA-{n}" for n in range(1, count + 1)], "--no-history"]
                 if source == "jira" else
                 ["onenote", "source", "sync", "nb-test", "section-test",
                  *[f"page-{n}" for n in range(1, count + 1)]])
    return [*selection, "--output", str(output)]


def _seed(output, source):
    target = output / "daily" / "2026" / "10" / "01.jsonl"
    target.parent.mkdir(parents=True)
    row = make_record(source, "https://fixture.invalid", "retained", "note", text="Retain me")
    target.write_text(json.dumps(row) + "\n", encoding="utf-8")
    (output / "source.yaml").write_text("driver: " + source + "\n", encoding="utf-8")
    return _files(output)


def _files(output):
    return {str(path.relative_to(output)): path.read_bytes() for path in output.rglob("*") if path.is_file()}


def _safe(result, caplog):
    assert SECRET not in result.stdout + result.stderr + caplog.text
    assert "Traceback" not in result.stdout + result.stderr


@pytest.mark.parametrize("failure", ["permission", "rate", "network", "pagination", "malformed", "storage", "scope"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_failure_contract_and_existing_results(service, monkeypatch, tmp_path, failure, as_json, caplog):
    """Every representative fatal failure names source/scope/recovery and preserves files."""
    source = service["source"]
    before = _seed(tmp_path, source)
    if failure == "storage":
        target = ("connectonion.useful_tools.jira.Jira.write_source_yaml" if source == "jira"
                  else "connectonion.useful_tools.onenote_storage.write_import")
        def refuse_write(*args, **kwargs):
            raise OSError(SECRET)
        monkeypatch.setattr(target, refuse_write)
    elif failure != "scope":
        service["failure"] = failure
    args = _args(source, tmp_path)
    if failure == "scope":
        args[3 if source == "jira" else 4] = "OTHER-1" if source == "jira" else "missing-section"
    result = CliRunner().invoke(app, [*args, *(["--json"] if as_json else [])])
    assert result.exit_code == 1, result.output
    _safe(result, caplog)
    assert _files(tmp_path) == before
    if as_json:
        data = json.loads(result.stdout)
        assert not result.stderr
        assert data["source"] == source and data["exit_code"] == 1
        assert isinstance(data["scope"], dict) and data["scope"]
        assert (data["scope"].get("projects") == ["ALPHA"] if source == "jira" else
                data["scope"].get("notebook") == "nb-test")
        assert data["error"] and data["error_code"] and data["recovery_action"]
        assert "outlook" not in data["recovery_action"].lower()
        waits_before_retry = len(service["waits"])
        assert waits_before_retry <= 3
        repeated = CliRunner().invoke(app, [*args, "--json"])
        assert len(service["waits"]) - waits_before_retry <= 3
        assert repeated.exit_code == 1 and not repeated.stderr
        again = json.loads(repeated.stdout)
        assert {key: data[key] for key in ("source", "scope", "error_code", "recovery_action")} == {
            key: again[key] for key in ("source", "scope", "error_code", "recovery_action")}
        _safe(repeated, caplog)
        assert _files(tmp_path) == before
    else:
        assert not result.stdout
        assert source in result.stderr.lower() and "scope" in result.stderr.lower()
        assert "Next:" in result.stderr
        assert "outlook" not in result.stderr.lower()
    if failure == "rate":
        assert len(service["waits"]) <= 3 * (2 if as_json else 1)


@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
@pytest.mark.parametrize("case", ["missing", "unknown-option", "bad-value", "unknown-command", "group-option"])
def test_invalid_arguments_are_safe_and_machine_readable(service, tmp_path, case, as_json, caplog):
    """Parser failures cannot leak raw argument values or require parsing prose in JSON mode."""
    source = service["source"]
    if case == "missing":
        args = ["jira", "sync", "ALPHA"] if source == "jira" else ["onenote", "source", "sync", "nb-test"]
    elif case == "unknown-option":
        args = [*_args(source, tmp_path), "--" + SECRET]
    elif case in {"unknown-command", "group-option"}:
        args = ["jira"] if source == "jira" else ["onenote", "source"]
        args.append(SECRET if case == "unknown-command" else "--" + SECRET)
    else:
        args = (["jira", "sync", "ALPHA", "ALPHA-1", "--history=" + SECRET] if source == "jira" else
                ["onenote", "source", "pages", "nb-test", "section-test", "--limit", SECRET])
    result = CliRunner().invoke(app, [*args, *(["--json"] if as_json else [])])
    assert result.exit_code == 2, result.output
    _safe(result, caplog)
    assert not service["calls"] and not _files(tmp_path)
    if as_json:
        data = json.loads(result.stdout)
        assert not result.stderr
        assert data["source"] == source and data["exit_code"] == 2
        assert data["error_code"] == "invalid_input" and data["scope"]["command"]
        assert data["recovery_action"].endswith("--help")
    else:
        assert not result.stdout and source in result.stderr.lower()
        assert "--help" in result.stderr


@pytest.mark.parametrize("count", [100, 1000])
def test_batch_volume_and_repeat_are_lossless(service, tmp_path, count, record_property):
    """Synthetic volume acceptance, not a live throughput or memory SLA."""
    source = service["source"]
    service["count"] = count
    args = [*_args(source, tmp_path, count), "--json"]
    started = perf_counter()
    result = CliRunner().invoke(app, args)
    elapsed = perf_counter() - started
    assert result.exit_code == 0, result.output
    data = json.loads(result.stdout)
    rows = [json.loads(line) for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len({row["id"] for row in rows}) == count
    assert {row["external_id"] for row in rows} == {
        (f"ALPHA-{n}" if source == "jira" else f"page-{n}") for n in range(1, count + 1)}
    assert data["appended" if source == "jira" else "written"] == count
    assert SECRET.encode() not in b"".join(_files(tmp_path).values())
    if source == "jira":
        assert sum(url.endswith("search/jql") for url, _ in service["calls"]) == math.ceil(count / 50)
    else:
        assert sum(url.endswith("/content") for url, _ in service["calls"]) == count
    repeat = CliRunner().invoke(app, args)
    assert repeat.exit_code == 0, repeat.output
    repeated = json.loads(repeat.stdout)
    assert repeated["appended" if source == "jira" else "written"] == 0
    assert sum(len(path.read_text(encoding="utf-8").splitlines()) for path in tmp_path.rglob("*.jsonl")) == count
    record_property("source", source)
    record_property("items", count)
    record_property("synthetic_first_import_seconds", round(elapsed, 4))


def test_onenote_selection_over_bound_rejected_before_requests(monkeypatch, tmp_path):
    """The 1000-page selection limit must fail before remote reads or local writes."""
    from connectonion.cli.commands import onenote_source_commands
    from connectonion.useful_tools.onenote_source import OneNoteSource
    source = OneNoteSource.__new__(OneNoteSource)
    monkeypatch.setattr(onenote_source_commands, "_source", lambda: source)
    monkeypatch.setattr(source, "scope", lambda *args: pytest.fail("Oversized selection must not read Graph"))
    result = CliRunner().invoke(app, [*_args("onenote", tmp_path, 1001), "--json"])
    assert result.exit_code == 1, result.output
    data = json.loads(result.stdout)
    assert data["source"] == "onenote" and "1000" in data["error"]
    assert data["error_code"] and data["recovery_action"] and not _files(tmp_path)


@pytest.mark.parametrize("all_failed", [False, True], ids=["mixed", "all-failed"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_malformed_items_have_failure_envelope_without_losing_valid_results(service, tmp_path, all_failed, as_json, caplog):
    """Partial and total processing failures have structured feedback, accurate counts and safe channels."""
    source = service["source"]
    service.update(count=2, partial=all_failed)
    result = CliRunner().invoke(app, [*_args(source, tmp_path, 2), *(["--json"] if as_json else [])])
    expected_exit = 1 if source == "jira" else 2
    assert result.exit_code == expected_exit, result.output
    _safe(result, caplog)
    rows = [json.loads(line) for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == (0 if all_failed else 1)
    if rows:
        assert rows[0]["external_id"] == ("ALPHA-1" if source == "jira" else "page-1")
    if as_json:
        data = json.loads(result.stdout)
        assert not result.stderr
        assert data["source"] == source and data["scope"]
        assert data["exit_code"] == expected_exit
        assert data["error_code"] == ("processing_failed" if all_failed else "partial_import")
        assert data["error"] and data["recovery_action"]
        assert data["mapped" if source == "jira" else "records"] == len(rows)
        if all_failed:
            assert "no" in data["error"].lower() and data["jsonl"] is None
            assert not _files(tmp_path), "A fully rejected batch must not create success-looking files"
    else:
        assert not result.stdout
        assert source in result.stderr.lower() and "scope" in result.stderr.lower()
        assert "Next:" in result.stderr
        if all_failed:
            assert "no valid" in result.stderr.lower() and "Saved to:" not in result.stderr


@pytest.mark.parametrize("service", ["jira"], indirect=True)
@pytest.mark.parametrize("path,payload", [
    ("myself", {}),
    ("myself", {"accountId": [SECRET], "displayName": "Fixture"}),
    ("myself", {"accountId": "fixture-account", "displayName": {"private": SECRET}}),
    ("search/jql", {"issues": None}),
    ("search/jql", {"issues": {"private": SECRET}}),
    ("search/jql", {"issues": [SECRET]}),
    ("search/jql", {"issues": [], "nextPageToken": {"private": SECRET}}),
])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_jira_malformed_metadata_shapes_are_safe(service, tmp_path, path, payload, as_json, caplog):
    """Missing/wrong metadata fields cannot escape the documented CLI error contract."""
    service["overrides"] = {path: payload}
    before = _seed(tmp_path, "jira")
    result = CliRunner().invoke(app, [*_args("jira", tmp_path), *(["--json"] if as_json else [])])
    assert result.exit_code == 1, result.output
    _safe(result, caplog)
    assert _files(tmp_path) == before
    if as_json:
        data = json.loads(result.stdout)
        assert not result.stderr
        assert data["source"] == "jira" and data["scope"]["issues"] == ["ALPHA-1"]
        assert data["error_code"] == "import_failed" and data["recovery_action"]
    else:
        assert not result.stdout and "Jira" in result.stderr and "Next:" in result.stderr


@pytest.mark.parametrize("service", ["onenote"], indirect=True)
@pytest.mark.parametrize("problem", ["permission", "rate", "network"])
@pytest.mark.parametrize("all_failed", [False, True], ids=["mixed", "all-failed"])
@pytest.mark.parametrize("as_json", [True, False], ids=["json", "human"])
def test_onenote_page_failures_preserve_results_and_report_recovery(service, tmp_path, problem, all_failed, as_json, caplog):
    """Per-page permission, exhausted rate-limit and network failures obey the same CLI contract."""
    service.update(count=2, partial=all_failed, partial_problem=problem)
    result = CliRunner().invoke(app, [*_args("onenote", tmp_path, 2), *(["--json"] if as_json else [])])
    assert result.exit_code == 2, result.output
    _safe(result, caplog)
    rows = [json.loads(line) for path in tmp_path.rglob("*.jsonl")
            for line in path.read_text(encoding="utf-8").splitlines()]
    assert [row["external_id"] for row in rows] == ([] if all_failed else ["page-1"])
    if as_json:
        data = json.loads(result.stdout)
        assert not result.stderr
        assert data["source"] == "onenote" and data["scope"]["pages"] == ["page-1", "page-2"]
        assert data["exit_code"] == 2 and data["records"] == len(rows)
        assert data["error_code"] == ("processing_failed" if all_failed else "partial_import")
        assert data["recovery_action"] and "page-2" in data["errors"]
        diagnostic = " ".join(data["errors"].values())
    else:
        assert not result.stdout and "OneNote" in result.stderr and "Next:" in result.stderr
        diagnostic = result.stderr
    expected_hint = {"permission": "permission", "rate": "wait before retrying", "network": "network connection"}
    assert expected_hint[problem] in diagnostic.lower()
    if all_failed:
        assert not _files(tmp_path)
