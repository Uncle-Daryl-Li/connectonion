"""
Purpose: `co granola` — verify key, list folders, list notes, import meeting notes as co rem records.
LLM-Note:
  Dependencies: imports from [sys, json, pathlib, rich, useful_tools/granola, useful_tools/adapter] | imported by [cli/main.py] | tested by [tests/unit/test_granola_commands.py]
  Data flow: CLI flags -> _granola() factory -> GranolaClient.verify()/list_folders()/list_notes()/get_note() -> raw dicts -> granola_notes_to_records() -> place_records() / write_source_yaml()
  State/Effects: import writes source.yaml and daily JSONL under the output dir; Granola itself is always read-only; all auth/key validation in tool layer
  Errors: GranolaConfigError/GranolaAuthError/GranolaPermissionError -> stderr + exit 1; GranolaIncompleteRead -> partial records retained, exit 1 with complete:false; never exposes API key
"""

import json
import sys
from pathlib import Path

from rich.console import Console

errors = Console(stderr=True)

_DEFAULT_PROFILE = "default"


# ---------------------------------------------------------------------------
# Private validation exceptions used only within this module.
# ---------------------------------------------------------------------------

class _FolderNotFound(ValueError):
    """folder_id does not appear in the list of accessible folders."""


class _NotPersonalFolder(ValueError):
    """folder_id is accessible but belongs to a shared space (space_id != None).

    NOTE: space_id=None as the personal-folder marker follows the Granola API
    field definition (space_id is null when the folder has no parent space).
    This assumption has not yet been confirmed with official Granola documentation
    or a direct client/vendor response; treat it as provisional until verified.
    """


class _NoteNotInSelectedFolder(ValueError):
    """note_id is not in the scoped, non-deleted list for the selected folder.

    Raised when the requested note_id does not appear in the list of active
    (deleted_at=null) notes for the selected personal folder.  No detail
    request is made and no files are written before this is raised.
    """


def _validate_personal_folder(folder_id: str, all_folders: list[dict]) -> None:
    """Raise _FolderNotFound or _NotPersonalFolder before any notes fetch or file writes.

    Must be called before list_notes() and before any output is written so that
    rejection leaves no side effects on disk.
    """
    all_ids = {f["id"] for f in all_folders}
    personal_ids = {f["id"] for f in all_folders if f.get("space_id") is None}
    if folder_id not in all_ids:
        raise _FolderNotFound(
            f"Folder {folder_id!r} was not found or is not accessible with this API key. "
            "Run 'co granola folders' to list your personal folder IDs."
        )
    if folder_id not in personal_ids:
        raise _NotPersonalFolder(
            f"Folder {folder_id!r} is in a shared space and is not a personal folder. "
            "Only personal folders (space_id: null) are supported. "
            "Run 'co granola folders' to list your personal folder IDs."
        )


def _granola():
    """Factory function patchable in unit tests."""
    from ...useful_tools.granola import GranolaClient
    return GranolaClient()


def _failure_details(exc: Exception) -> tuple[str, str]:
    """Classify typed failures; returns (error_code, recovery_action)."""
    from ...useful_tools.granola import (
        GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead,
    )

    if isinstance(exc, OSError):
        return "storage_error", "Check --output and directory permissions, then retry the Granola command."
    if isinstance(exc, _NoteNotInSelectedFolder):
        return "note_not_in_selected_folder", (
            "The note ID was not found in the selected folder active notes. "
            "Run 'co granola notes <folder-id> --json' to list available note IDs."
        )
    if isinstance(exc, _NotPersonalFolder):
        return "personal_folder_required", (
            "Run 'co granola folders' to list your personal folder IDs, then retry with a personal folder ID."
        )
    if isinstance(exc, _FolderNotFound):
        return "folder_not_found", (
            "Run 'co granola folders' to list your personal folder IDs, then retry."
        )
    if isinstance(exc, GranolaIncompleteRead):
        return "transient_error", (
            "Granola API returned a transient error; wait a moment and retry the command."
        )
    if isinstance(exc, GranolaAuthError):
        return "reauth_required", (
            "Set GRANOLA_API_KEY to your personal Granola API key as a temporary shell variable, "
            "then retry this command."
        )
    if isinstance(exc, GranolaPermissionError):
        return "permission_denied", "Check that GRANOLA_API_KEY can read this folder, then retry."
    if isinstance(exc, GranolaConfigError):
        return "invalid_configuration", (
            "Check that GRANOLA_API_KEY is set and valid; see co granola --help."
        )
    return "import_failed", (
        "Check the selected folder and co granola import --help, then retry."
    )


def _handle_error(exc: Exception, as_json: bool, *, scope: dict | None = None) -> None:
    msg = ("Could not read or write local Granola source files. "
           "Check --output and directory permissions.") if isinstance(exc, OSError) else str(exc).strip()
    code, recovery = _failure_details(exc)
    result = {"error": msg, "exit_code": 1, "source": "granola", "scope": scope or {},
              "error_code": code, "recovery_action": recovery}
    if as_json:
        print(json.dumps(result))
    else:
        errors.print(f"Granola command failed (scope: {json.dumps(result['scope'])}).", markup=False)
        errors.print(msg, style="red", markup=False)
        errors.print(f"Next: {recovery}", markup=False)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Note stub helpers (for handle_granola_notes).
# ---------------------------------------------------------------------------

def _truncate(s: str, max_len: int) -> str:
    """Truncate to max_len chars with ASCII ... suffix if needed."""
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "..."


def _fmt_date(iso: str) -> str:
    """Format ISO 8601 timestamp to YYYY-MM-DD HH:MM UTC."""
    if not iso:
        return ""
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        dt = dt.astimezone(timezone.utc)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return iso[:16] + " UTC" if len(iso) >= 16 else iso


def _note_stub_json(stub: dict) -> dict:
    """Return the safe stub fields for JSON output (full title, ISO 8601 timestamps)."""
    return {
        "id": str(stub.get("id") or ""),
        "title": str(stub.get("title") or ""),
        "created_at": str(stub.get("created_at") or ""),
        "updated_at": str(stub.get("updated_at") or ""),
    }


def _print_notes_table(stubs: list[dict]) -> None:
    """Print a human-readable table: full ID, title (max 28 chars), formatted date."""
    for stub in stubs:
        note_id = str(stub.get("id") or "")
        title = _truncate(str(stub.get("title") or ""), 28)
        created = _fmt_date(str(stub.get("created_at") or ""))
        print(f"{note_id:<40}  {title:<28}  {created}")


# ---------------------------------------------------------------------------
# Public command handlers
# ---------------------------------------------------------------------------

# Added to every successful verify response (JSON + terminal) per Canvas spec:
# "success JSON must state verified_read: true AND that this only validates a
# minimal read request, not all folders/spaces/notes."
_VERIFY_SCOPE_NOTE = (
    "This verifies only a minimal /v1/notes?page_size=1 read. "
    "It does not confirm access to all folders, spaces, or notes."
)


def handle_granola_verify(*, as_json: bool = False) -> None:
    """Verify the Granola API key with a minimal read.

    JSON output: {"verified_read": true, "note": "<first note title or sentinel>",
                  "verify_scope": "<scope caveat>"}.
    Terminal output also prints the scope caveat so the user knows the limits of
    this check.  The API key is never included in any output.
    """
    from ...useful_tools.granola import (
        GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead,
    )
    try:
        result = _granola().verify()
    except (GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    if as_json:
        result["verify_scope"] = _VERIFY_SCOPE_NOTE
        print(json.dumps(result))
        return
    print(f"Verified: {result.get('note', '(no notes yet)')}")
    print(f"Note: {_VERIFY_SCOPE_NOTE}")
    print("Next: co granola folders")


def handle_granola_folders(*, as_json: bool = False) -> None:
    """List personal Granola folders (space_id: null) accessible with this API key.

    The Granola API returns all accessible folders; this command filters to personal
    folders only (space_id is null), matching the single-personal-folder import scope.

    JSON output: {"folders": [{"id": ..., "name": ..., "space_id": null}, ...]}.
    Empty result: {"folders": []} valid when no personal folders exist.
    Errors: exit 1; JSON includes error, exit_code, source, scope, error_code, recovery_action.
    """
    from ...useful_tools.granola import (
        GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead,
    )
    try:
        all_folders = _granola().list_folders()
    except (GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead, ValueError) as exc:
        _handle_error(exc, as_json)
        return
    # Only expose personal folders (not in a shared space).
    folders = [f for f in all_folders if f.get("space_id") is None]
    if as_json:
        print(json.dumps({"folders": folders}))
        return
    if not folders:
        print("No personal folders found.")
        print("Next: co granola verify")
        return
    for folder in folders:
        name = folder.get("name") or folder.get("title", "")
        print(f"{folder.get('id', ''):<24}  {name}")
    print("Next: co granola import <folder-id>")


def handle_granola_notes(
    folder_id: str,
    *,
    limit: int = 100,
    as_json: bool = False,
) -> None:
    """List safe note metadata (id, title, created_at, updated_at) from a personal folder.

    Step 1: validate personal folder (space_id=None) via list_folders().
    Step 2: list_notes(folder_id, limit) to get lightweight stubs.
    Step 3: filter out deleted notes (deleted_at not null/empty).
    Step 4: display as table or JSON.

    On GranolaIncompleteRead from list_folders(): standard error JSON (no notes array), exit 1.
    On GranolaIncompleteRead from list_notes(): partial notes JSON with complete:false, exit 1,
      no files written.

    JSON success output: {"notes": [...], "complete": true}.
    JSON partial output: {"notes": [...], "complete": false, "error_code": "transient_error",
      "recovery_action": "..."}.
    """
    from ...useful_tools.granola import (
        GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead,
    )

    scope: dict = {"folder_id": folder_id, "limit": limit}

    try:
        client = _granola()
        # list_folders() GranolaIncompleteRead -> caught by outer except -> standard error JSON.
        _validate_personal_folder(folder_id, client.list_folders())

        complete = True
        failure_reason: str = ""
        try:
            stubs = client.list_notes(folder_id=folder_id, limit=limit)
        except GranolaIncompleteRead as exc:
            # Partial stubs: flatten all partial pages received before the failure.
            stubs = [note for page in exc.partial_pages for note in page]
            complete = False
            failure_reason = exc.reason

    except (GranolaConfigError, GranolaAuthError, GranolaPermissionError,
            GranolaIncompleteRead, _FolderNotFound, _NotPersonalFolder, ValueError) as exc:
        _handle_error(exc, as_json, scope=scope)
        return

    # Filter out deleted stubs (deleted_at not null/empty).
    active = [s for s in stubs if not s.get("deleted_at")]

    if not complete:
        recovery = f"Retry co granola notes {folder_id} when the API recovers."
        if as_json:
            print(json.dumps({
                "notes": [_note_stub_json(s) for s in active],
                "complete": False,
                "error_code": "transient_error",
                "recovery_action": recovery,
            }))
        else:
            _print_notes_table(active)
            errors.print(
                f"Granola notes (scope: {json.dumps(scope)}): partial result: {failure_reason}",
                markup=False,
            )
            errors.print(f"Next: {recovery}", markup=False)
        sys.exit(1)

    if as_json:
        print(json.dumps({"notes": [_note_stub_json(s) for s in active], "complete": True}))
        return

    if not active:
        print("No notes found in this folder.")
        print(f"Next: co granola import {folder_id}")
        return

    _print_notes_table(active)
    print(f"Next: co granola import {folder_id} --note-id <note-id>")


def handle_granola_import(
    folder_id: str,
    *,
    profile: str = _DEFAULT_PROFILE,
    limit: int | None = None,
    output_dir: Path | None = None,
    as_json: bool = False,
    export_id: str | None = None,
    note_id: str | None = None,
) -> None:
    """Validate personal folder, list notes, map to records, write source.yaml and daily records.

    Full workflow: list_folders -> validate personal folder (space_id=None) ->
    list_notes(folder_id, limit) -> granola_notes_to_records ->
    write_source_yaml -> append_daily_jsonl (ongoing) or write_export (one-off with --export).
    Folder validation runs before any notes fetch or file writes; rejection has no side effects.

    When --note-id is given:
      - --limit is rejected with exit 1 before any network call (mutually exclusive).
      - A scoped lookup (list_notes up to MAX_SELECTED) is performed.
      - Deleted stubs (deleted_at not null) are excluded from the scoped list.
      - If note_id is not in the active scoped list, exits 1 with note_not_in_selected_folder.
      - Otherwise calls get_note(note_id) for full detail, then maps one record.
      - source.yaml records note_id and limit: 1 for traceability.

    On GranolaIncompleteRead: partial notes already fetched are mapped and retained;
    source.yaml IS written with complete:false so the source metadata is always present;
    the result carries complete:false and exits 1.  When --export is given and the read
    is incomplete, the export manifest also records complete:false and the failure reason.

    JSON output: {"written": source_yaml_path, "placement": "daily"|"export", "jsonl": path,
    "folder_id": ..., "profile": ..., "notes": collected, "mapped": valid_records,
    "appended": newly_added, "complete": true|false, "rejected": {"granola:<id>": reason}}.
    """
    from ...useful_tools.granola import (
        GranolaConfigError, GranolaAuthError, GranolaPermissionError, GranolaIncompleteRead,
        MAX_SELECTED,
    )
    from ...useful_tools.adapter import (
        granola_notes_to_records, append_daily_jsonl, write_export, export_target,
    )

    if output_dir is None:
        output_dir = Path.home() / ".co" / "rem" / "docs" / "granola"

    scope: dict = {"folder_id": folder_id, "profile": profile, "limit": limit}
    if note_id is not None:
        scope["note_id"] = note_id

    # --note-id and --limit are mutually exclusive; reject before any network call.
    if note_id is not None and limit is not None:
        msg = "--note-id and --limit cannot be used together."
        recovery = "Remove --limit when using --note-id, then retry."
        result = {
            "error": msg, "exit_code": 1, "source": "granola",
            "scope": scope, "error_code": "invalid_configuration",
            "recovery_action": recovery,
        }
        if as_json:
            print(json.dumps(result))
        else:
            errors.print(msg, markup=False)
            errors.print(f"Next: {recovery}", markup=False)
        sys.exit(1)

    try:
        if export_id is not None:
            export_target(output_dir, export_id)
        client = _granola()
        # Validate before any notes fetch or file writes so rejection has no side effects.
        _validate_personal_folder(folder_id, client.list_folders())
        complete = True
        failure_reason: str = ""

        if note_id is not None:
            # Precise single-note import: scoped lookup then targeted detail fetch.
            scoped_incomplete = False
            scoped_failure_reason = ""
            try:
                scoped_stubs = client.list_notes(folder_id=folder_id, limit=MAX_SELECTED)
            except GranolaIncompleteRead as exc:
                scoped_stubs = [n for page in exc.partial_pages for n in page]
                scoped_incomplete = True
                scoped_failure_reason = exc.reason

            active_ids = {
                str(s.get("id") or "")
                for s in scoped_stubs
                if not s.get("deleted_at")
            }
            if note_id not in active_ids:
                if scoped_incomplete:
                    # Note absent from partial pages: it may be on an un-fetched page.
                    # Report transient error rather than "not found" to avoid a false rejection.
                    raise GranolaIncompleteRead(
                        reason=(
                            f"Scoped lookup for folder {folder_id!r} was incomplete "
                            f"({scoped_failure_reason}); cannot confirm whether note "
                            f"{note_id!r} exists. Retry when the API recovers."
                        ),
                        partial_pages=[],
                    )
                raise _NoteNotInSelectedFolder(
                    f"Note {note_id!r} was not found in the active notes for folder {folder_id!r}. "
                    "It may have been deleted, moved, or not yet processed. "
                    "Run 'co granola notes <folder-id> --json' to list available note IDs."
                )
            notes = client.hydrate_notes([{"id": note_id}])
            if scoped_incomplete:
                # Scoped lookup was partial but note was found; retain it with complete=False.
                complete = False
                failure_reason = scoped_failure_reason
        else:
            try:
                notes = client.hydrate_notes(client.list_notes(folder_id=folder_id, limit=limit))
            except GranolaIncompleteRead as exc:
                notes = client.hydrate_notes([note for page in exc.partial_pages for note in page])
                complete = False
                failure_reason = exc.reason

        adapter_result = granola_notes_to_records(notes, profile, selected_folder_id=folder_id)

        # Always write source.yaml so source metadata is present alongside daily records.
        # complete=False is recorded so callers know the read was partial.
        # Compute the limit that source.yaml and the export manifest must both record.
        # write_source_yaml() maps None → MAX_SELECTED internally, so we mirror that here
        # so the manifest scope is consistent with source.yaml for every import path.
        effective_limit = 1 if note_id is not None else (limit if limit is not None else MAX_SELECTED)
        scope["limit"] = effective_limit
        client.write_source_yaml(
            output_dir, folder_id, profile,
            limit=effective_limit, complete=complete, note_id=note_id,
        )
        source_yaml = str(output_dir / "source.yaml")

        # For exports, enrich scope so the manifest records completion status.
        export_scope = scope if complete else {**scope, "complete": False, "failure_reason": failure_reason}
        jsonl_path, appended = _place_records(
            adapter_result.records, output_dir, export_id, export_scope,
        )

    except (GranolaConfigError, GranolaAuthError, GranolaPermissionError,
            GranolaIncompleteRead, _NoteNotInSelectedFolder, ValueError, OSError) as exc:
        _handle_error(exc, as_json, scope=scope)
        return

    rejected = adapter_result.errors
    mapped = len(adapter_result.records)
    collected = len(notes)

    if rejected or not complete:
        _report_partial(
            rejected, as_json, scope, source_yaml, jsonl_path,
            collected, mapped, appended, complete, failure_reason, export_id,
        )
        return

    if as_json:
        print(json.dumps({
            "written": source_yaml,
            "placement": "export" if export_id else "daily",
            "jsonl": str(jsonl_path),
            "folder_id": folder_id,
            "profile": profile,
            "notes": collected,
            "mapped": mapped,
            "appended": appended,
            "complete": True,
            "rejected": rejected,
        }))
    else:
        if source_yaml:
            print(f"Wrote {source_yaml}")
        print(f"Wrote {jsonl_path}")
        print(f"Folder: {folder_id}  Profile: {profile}  "
              f"Notes collected: {collected}  Records mapped: {mapped}  Records appended: {appended}")
        print("Next: co rem sources")


def _place_records(
    records: list[dict],
    output_dir: Path,
    export_id: str | None,
    scope: dict,
) -> tuple[Path, int]:
    from ...useful_tools.adapter import append_daily_jsonl, write_export
    if export_id is None:
        return append_daily_jsonl(records, output_dir)
    return write_export(records, output_dir, export_id, scope) / "records.jsonl", len(records)


def _report_partial(
    rejected: dict,
    as_json: bool,
    scope: dict,
    source_yaml: str | None,
    jsonl_path: Path | None,
    collected: int,
    mapped: int,
    appended: int,
    complete: bool,
    failure_reason: str,
    export_id: str | None,
) -> None:
    if not complete:
        message = (
            f"Granola read was incomplete: {failure_reason} "
            f"Valid records retained: {mapped}." if mapped else
            f"Granola read was incomplete: {failure_reason} No valid records were processed."
        )
    elif mapped:
        message = "Some selected Granola notes could not be imported; valid results are retained."
    else:
        message = "No valid Granola records were processed; no new import files were written."

    recovery = ("Review the rejected notes and retry; keep --output for daily imports or use a new --export ID."
                if rejected else "Retry later; partial records have been retained.")
    report = {
        "error": message,
        "exit_code": 1,
        "source": "granola",
        "scope": scope,
        "error_code": "partial_read" if not complete else ("partial_import" if mapped else "processing_failed"),
        "recovery_action": recovery,
        "written": source_yaml,
        "placement": "export" if export_id else "daily",
        "jsonl": str(jsonl_path) if jsonl_path else None,
        "folder_id": scope.get("folder_id", ""),
        "profile": scope.get("profile", ""),
        "notes": collected,
        "mapped": mapped,
        "appended": appended,
        "complete": complete,
        "rejected": rejected,
    }
    if as_json:
        print(json.dumps(report))
    else:
        errors.print(f"Granola import (scope: {json.dumps(scope)}).", markup=False)
        errors.print(message, markup=False)
        if jsonl_path and mapped:
            errors.print(
                f"Valid records retained: {mapped}; newly appended: {appended}. Saved to: {jsonl_path}",
                markup=False,
            )
        if rejected:
            errors.print(f"{len(rejected)} note(s) were rejected and not written:", markup=False)
            for label, reason in rejected.items():
                errors.print(f"  Rejected {label}: {reason}", markup=False)
        errors.print(f"Next: {recovery}", markup=False)
    sys.exit(1)
