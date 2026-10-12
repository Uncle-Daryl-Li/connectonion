import json
from pathlib import Path
import httpx
import pytest
import yaml
from connectonion.provider_credentials import ProviderCredentialError
from connectonion.useful_tools.onenote_source import OneNoteSource
from onenote_source_fixtures import graph, BASE, DATA, HTML

from connectonion.useful_tools.onenote_source import sync_onenote

def test_selected_import_preserves_content_media_and_partial_errors(graph):
    routes, calls = graph
    routes["/me/onenote/pages/page-malformed"] = ["invalid"]
    result = OneNoteSource().collect("nb-fixture", "section-fixture",
                                   ["page-good", "page-empty", "page-gone", "page-malformed", "page-outside"])
    assert {r["id"] for r in result.records} == {"page-good", "page-empty"}
    assert set(result.errors) == {"page-gone", "page-malformed", "page-outside"}
    record = result.records[0]
    assert record["source"] == "onenote" and record["url"].startswith("https://example.invalid/")
    assert record["created"] == "2026-10-01T01:00:00Z" and record["date"] == "2026-10-02T01:00:00Z"
    assert "Keep & cite" in record["text"] and "do_not_include" not in record["text"]
    assert record["media"][0]["mime_type"] == "image/png" and record["media"][0]["size"] == 42
    assert record["media"][1]["name"] == "fixture.txt"
    assert all(m["status"] == "metadata_only" and m["fetch_reference"] for m in record["media"])
    assert result.records[1]["text"] == "" and result.records[1]["url"].endswith("/pages/page-empty")
    assert not any("resources/" in url or "page-outside/content" in url for _, url, _ in calls)


@pytest.mark.parametrize("mode", ["daily", "export"])
def test_sync_explicit_layout_and_source_config(graph, tmp_path, mode):
    result = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"],
                          tmp_path, mode=mode, export_id="fixture-export" if mode == "export" else None)
    doc = yaml.safe_load((tmp_path / "source.yaml").read_text(encoding="utf-8"))
    assert doc["driver"] == "onenote" and doc["scope"]["pages"] == ["page-good"]
    assert doc["media_policy"] == "metadata_only" and doc["retention"] == "90d"
    assert doc["validated_with"]["connectonion_version"] and doc["validated_with"]["path"]
    target = Path(result["jsonl"])
    record = json.loads(target.read_text(encoding="utf-8").strip())
    assert record["ingestion"] == {"mode": mode, "path": target.relative_to(tmp_path).as_posix()}
    if mode == "export":
        manifest = json.loads((target.parent / "manifest.json").read_text(encoding="utf-8"))
        assert manifest["records"] == 1 and manifest["scope"] == doc["scope"]
        with pytest.raises(ValueError, match="exists"):
            sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"],
                         tmp_path, mode=mode, export_id="fixture-export")
    assert "fixture-secret-token" not in (tmp_path / "source.yaml").read_text(encoding="utf-8")


@pytest.mark.parametrize("options", [{"mode": "unknown"}, {"mode": "export", "export_id": "../escape"},
                                    {"mode": "export"}, {"mode": "daily", "export_id": "unexpected"}])
def test_invalid_storage_rejected_before_network_or_writes(graph, tmp_path, options):
    _, calls = graph
    with pytest.raises(ValueError):
        sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path, **options)
    assert not calls and not list(tmp_path.iterdir())


def test_repeat_daily_import_reports_dedup_instead_of_claiming_new_writes(graph, tmp_path):
    first = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    second = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    assert first["written"] == 1 and second["written"] == 0 and second["skipped"] == 1
    assert "changed pages" in second["note"]


def test_all_failed_import_leaves_no_files(graph, tmp_path):
    result = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-gone"], tmp_path)
    assert result["records"] == 0 and result["errors"] and not list(tmp_path.iterdir())


def test_account_or_scope_cannot_overwrite_an_existing_source_config(graph, tmp_path):
    sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    original = (tmp_path / "source.yaml").read_bytes()
    with pytest.raises(ValueError, match="another account or scope"):
        sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-empty"], tmp_path)
    assert (tmp_path / "source.yaml").read_bytes() == original


@pytest.mark.parametrize("status", [401, 403, 404, 429, 503])
def test_page_errors_are_safe_and_unauthorized_aborts(graph, monkeypatch, status):
    monkeypatch.setattr("connectonion.useful_tools.onenote_source.time.sleep", lambda seconds: None)
    from connectonion.useful_tools.outlook import Outlook
    monkeypatch.setattr(Outlook, "_refresh_via_backend", lambda self, rt: "fresh")
    routes, _ = graph
    routes["/me/onenote/pages/page-good/content"] = (status, "fixture-secret-token")
    if status == 401:
        with pytest.raises(ProviderCredentialError, match="co auth microsoft"):
            OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good"])
    else:
        result = OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good", "page-empty"])
        assert len(result.records) == 1 and "page-good" in result.errors
        assert "fixture-secret-token" not in json.dumps(result.errors)
        if status == 404:
            assert "co onenote source pages --help" in result.errors["page-good"]
            assert "co auth microsoft" not in result.errors["page-good"]


@pytest.mark.parametrize("retry_after, expected_wait", [
    ("3", 3), ("Thu, 01 Jan 1970 00:16:45 GMT", 5), ("invalid", 1),
])
def test_content_429_then_success_honors_retry_after(graph, monkeypatch, retry_after, expected_wait):
    waits = []
    monkeypatch.setattr("connectonion.useful_tools.onenote_source.time.sleep", waits.append)
    monkeypatch.setattr("connectonion.useful_tools.onenote_source.time.time", lambda: 1000)
    routes, calls = graph
    responses = iter([httpx.Response(429, headers={"Retry-After": retry_after}), HTML])
    routes["/me/onenote/pages/page-good/content"] = lambda: next(responses)
    result = OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good"])
    assert len(result.records) == 1 and not result.errors
    assert waits == [expected_wait]
    assert len([url for _, url, _ in calls if url.endswith("/page-good/content")]) == 2


@pytest.mark.parametrize("status", [429, 503, 504])
def test_content_retry_exhaustion_keeps_valid_pages_and_reports_recovery(graph, monkeypatch, status):
    waits = []
    monkeypatch.setattr("connectonion.useful_tools.onenote_source.time.sleep", waits.append)
    routes, calls = graph
    routes["/me/onenote/pages/page-good/content"] = (status, "private provider body")
    result = OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good", "page-empty"])
    assert len(result.records) == 1 and "page-good" in result.errors
    assert waits == [1, 2]
    assert len([url for _, url, _ in calls if url.endswith("/page-good/content")]) == 3
    error = result.errors["page-good"]
    assert f"HTTP {status}" in error and "retries exhausted" in error
    assert "Wait" in error and "co onenote source sync" in error
    assert "pages --help" not in error and "private provider body" not in error


@pytest.mark.parametrize("retry_after", ["86400", "1e300"])
def test_content_long_retry_after_reports_failure_without_sleeping_or_retrying_early(graph, monkeypatch, retry_after):
    waits = []
    monkeypatch.setattr("connectonion.useful_tools.onenote_source.time.sleep", waits.append)
    routes, calls = graph
    routes["/me/onenote/pages/page-good/content"] = httpx.Response(429, headers={"Retry-After": retry_after})
    result = OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good", "page-empty"])
    assert len(result.records) == 1 and not waits
    assert len([url for _, url, _ in calls if url.endswith("/page-good/content")]) == 1
    assert "automatic wait limit" in result.errors["page-good"]
    assert "Wait" in result.errors["page-good"] and "co onenote source sync" in result.errors["page-good"]


def test_content_refresh_can_recover_without_discarding_page(graph, monkeypatch):
    from connectonion.useful_tools.outlook import Outlook
    refreshed = []
    monkeypatch.setattr(Outlook, "_refresh_via_backend", lambda self, rt: refreshed.append(rt) or "fresh")
    routes, _ = graph
    responses = iter([(401, "private"), HTML])
    routes["/me/onenote/pages/page-good/content"] = lambda: next(responses)
    result = OneNoteSource().collect("nb-fixture", "section-fixture", ["page-good"])
    assert len(result.records) == 1 and not result.errors and len(refreshed) == 1


def test_cli_help_and_json_partial_import(graph, tmp_path):
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    runner = CliRunner()
    for command in ([], ["sync"], ["pages"], ["verify"]):
        result = runner.invoke(app, ["onenote", "source", *command, "--help"])
        assert result.exit_code == 0, result.output
    result = runner.invoke(app, ["onenote", "source", "sync", "nb-fixture", "section-fixture",
                                 "page-good", "page-gone", "--output", str(tmp_path), "--json"])
    assert result.exit_code == 2, result.output
    report = json.loads(result.stdout)
    assert report["written"] == 1 and "page-gone" in report["errors"]
    assert Path(report["jsonl"]).is_file()
    assert "fixture-secret-token" not in result.output
    assert not result.stderr


@pytest.mark.parametrize("invalid", ["[]", "null", "42", '"scalar"', "{broken"])
def test_corrupted_daily_storage_returns_json_error_and_never_appends(graph, tmp_path, invalid):
    from typer.testing import CliRunner
    from connectonion.cli.main import app
    initial = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    target = Path(initial["jsonl"])
    target.write_text(invalid + "\n", encoding="utf-8")
    result = CliRunner().invoke(app, ["onenote", "source", "sync", "nb-fixture", "section-fixture",
                                    "page-good", "--output", str(tmp_path), "--json"])
    assert result.exit_code == 1, result.output
    assert "corrupted at line 1" in json.loads(result.stdout)["error"]
    assert target.read_text(encoding="utf-8") == invalid + "\n"


def test_changed_same_day_page_is_explicitly_skipped(graph, tmp_path):
    routes, _ = graph
    initial = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    routes["/me/onenote/pages/page-good/content"] = "<html><body>Changed fixture</body></html>"
    repeated = sync_onenote(OneNoteSource(), "nb-fixture", "section-fixture", ["page-good"], tmp_path)
    assert repeated["written"] == 0 and repeated["skipped"] == 1
    assert "incremental refresh is not implemented" in repeated["note"]
    assert "Changed fixture" not in Path(initial["jsonl"]).read_text(encoding="utf-8")
