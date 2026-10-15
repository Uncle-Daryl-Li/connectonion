"""Read-only OneNote source CLI; imports collection code only when invoked."""

import json
from pathlib import Path
from typing import List, Optional

import typer

from .import_feedback import ImportFeedbackCommand, ImportFeedbackGroup


def _source():
    from ...useful_tools.onenote_source import OneNoteSource
    return OneNoteSource()


def _failure(message: str, scope: dict | None, code: str, recovery: str) -> dict:
    """Keep legacy error text while exposing source, scope and recovery to scripts."""
    return {"error": message, "exit_code": 1, "source": "onenote", "scope": scope or {},
            "error_code": code, "recovery_action": recovery}


def _run(operation, as_json: bool, family: str | None = None, *, scope: dict | None = None) -> None:
    from ...provider_credentials import ProviderCredentialError
    import yaml
    try:
        result = operation(_source())
    except (OSError, yaml.YAMLError):
        result = _failure("Could not read or write the local source files. Check --output and directory permissions.",
                          scope, "storage_error", "Check --output and directory permissions, then retry the source command.")
    except ProviderCredentialError as error:
        result = _failure(str(error), scope, error.code, error.next_command)
        result.update(getattr(error, "saved_results", {}))
    except ValueError as error:
        message = str(error)
        result = _failure(message, scope, "import_failed",
                          message or "Check co onenote source --help, then retry the selected command.")
    if result.get("errors") and "error" not in result:
        message = ("Some selected OneNote pages could not be imported; valid results are retained." if result["records"] else
                   "No valid OneNote records were processed; no new import files were written.")
        result.update(_failure(message,
                               scope, "partial_import" if result["records"] else "processing_failed",
                               "Relist with co onenote source pages, then retry failed pages; "
                               "keep the same --output for daily imports, or use a new --export-id for exports."))
        result["exit_code"] = 2
    if as_json:
        # ASCII JSON escapes preserve Unicode values even when a Windows
        # native pipe decodes stdout using a legacy code page instead of UTF-8.
        print(json.dumps(result))
    elif "error" in result:
        message = result["error"].split("\nNext:", 1)[0]
        typer.echo(f"OneNote command failed (scope: {json.dumps(result['scope'])}).\n"
                   f"{message}\nNext: {result['recovery_action']}", err=True)
        if result.get("jsonl"):
            typer.echo(f"Valid records retained: {result['records']}; newly written: {result['written']}; "
                       f"skipped: {result['skipped']}.\nSaved to: {result['jsonl']}", err=True)
        if result.get("errors"):
            typer.echo("Not imported: " + ", ".join(result["errors"]), err=True)
            for page_id, diagnostic in result["errors"].items():
                typer.echo(f"{page_id}: {diagnostic.replace(chr(10) + 'Next:', chr(10) + 'Recovery:')}", err=True)
            typer.echo(result.get("note", ""), err=True)
    elif family:
        for row in result[family]:
            print(f"{row['id']}  {' '.join(str(row.get('displayName') or row.get('title') or '(untitled)').split())}")
        if not result[family]:
            print(f"No {family} in this scope.")
        if result.get("truncated"):
            print(f"Listing limited to {result['limit']} pages; more pages remain. Increase --limit (maximum 1000).")
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        if result["exit_code"] == 2:
            from .command_tips import mark_next_step_named
            mark_next_step_named()
        raise typer.Exit(result["exit_code"])


def register_onenote_source(parent, factory) -> None:
    source_app = factory(help="Read-only OneNote collection: choose notebook, section and explicit page IDs.",
                         cls=ImportFeedbackGroup,
                         no_args_is_help=True)
    parent.add_typer(source_app, name="source")

    @source_app.command("verify", cls=ImportFeedbackCommand)
    def verify(as_json: bool = typer.Option(False, "--json")):
        """Verify existing co auth microsoft credentials and OneNote read access."""
        _run(lambda source: source.verify_connection(), as_json)

    @source_app.command("notebooks", cls=ImportFeedbackCommand)
    def notebooks(as_json: bool = typer.Option(False, "--json")):
        """List all accessible notebooks, following pagination."""
        _run(lambda source: {"notebooks": source.notebooks()}, as_json, "notebooks")

    @source_app.command("sections", cls=ImportFeedbackCommand)
    def sections(notebook: str = typer.Argument(..., help="Notebook ID or unique exact name"),
                 as_json: bool = typer.Option(False, "--json")):
        """List all sections of the selected notebook, following pagination."""
        _run(lambda source: {"sections": source.sections(notebook)}, as_json, "sections",
             scope={"notebook": notebook})

    @source_app.command("pages", cls=ImportFeedbackCommand)
    def pages(notebook: str = typer.Argument(..., help="Notebook ID or unique exact name"),
              section: str = typer.Argument(..., help="Section ID or unique exact name within that notebook"),
              limit: int = typer.Option(100, "--limit", min=1, max=1000),
              as_json: bool = typer.Option(False, "--json")):
        """List a bounded page set and report whether later pages remain."""
        _run(lambda source: source.pages(notebook, section, limit=limit), as_json, "pages",
             scope={"notebook": notebook, "section": section})

    @source_app.command("sync", cls=ImportFeedbackCommand)
    def sync(notebook: str = typer.Argument(..., help="Selected notebook ID or exact name"),
             section: str = typer.Argument(..., help="Selected section ID or exact name"),
             page_ids: List[str] = typer.Argument(..., help="Explicit page IDs from source pages; never imports all pages"),
             output: Optional[Path] = typer.Option(None, "--output", help="Default: ~/.co/rem/docs/onenote"),
             mode: str = typer.Option("daily", "--mode", help="daily or export"),
             export_id: Optional[str] = typer.Option(None, "--export-id", help="Required for one-off export"),
             as_json: bool = typer.Option(False, "--json")):
        """Save chosen pages as daily records or a one-off export. Remote reads only.

        Exit 0: all selected pages imported. Exit 2: partial/all page failures;
        valid records are saved. Exit 1: authentication, scope or storage failure.
        This writes an external import, not automatic co rem source registration.
        """
        from ...useful_tools.onenote_source import sync_onenote
        directory = output or Path.home() / ".co" / "rem" / "docs" / "onenote"
        _run(lambda source: sync_onenote(source, notebook, section, page_ids, directory,
                                        mode=mode, export_id=export_id), as_json,
             scope={"notebook": notebook, "section": section, "pages": sorted(set(page_ids))})
