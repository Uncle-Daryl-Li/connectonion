"""
Purpose: Common adapter contract for co rem source integrations (F13FCAKE-18).
LLM-Note:
  Dependencies: imports from [datetime, json, dataclasses, pathlib] | imported by [tests/unit/test_jira.py, cli/commands/jira_commands.py] | no network calls
  Data flow: raw tool output (Jira issues / OneNote pages) → adapter functions → AdapterResult with provenance records → write_daily_jsonl stores under daily/YYYY/MM/DD.jsonl
  State/Effects: write_daily_jsonl() creates directories and writes files; all other functions are pure
  Integration: AdapterResult is the shared record type; consume_records() is the in-memory consumer; consume_jsonl_records() reads from disk across multiple source directories; jira_issues_to_records() and onenote_pages_to_records() are the converter functions
  Errors: write_daily_jsonl() raises ValueError if the existing JSONL file contains a malformed line (refuses to append to a corrupted file) | consume_jsonl_records() never raises — malformed lines are recorded in AdapterResult.errors keyed by "file:lineno"; all other functions are pure and produce empty/partial records on malformed input
  F13FCAKE-18 prototype status:
    Jira chain -- fully integrated: verify_connection -> issue_items -> write_source_yaml -> jira_issues_to_records -> write_daily_jsonl
    OneNote chain -- adapter function implemented and tested with synthetic fixtures; live Notes.Read integration is pending authorized account credentials and OneNote team implementation

Common adapter contract for co rem integrations.

Usage:
    from connectonion.useful_tools.adapter import (
        AdapterResult, consume_records, consume_jsonl_records,
        write_daily_jsonl, jira_issues_to_records, onenote_pages_to_records,
    )
    result = jira_issues_to_records(issues, base_url)
    records = consume_records(result)
    jsonl_path = write_daily_jsonl(result.records, source_dir)
    # Read back from disk (source-agnostic):
    result = consume_jsonl_records(jira_dir, onenote_dir)
    all_records = result.records  # result.errors contains any malformed-line diagnostics
"""

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class AdapterResult:
    """Common result type for all co rem source adapters (F13FCAKE-18).

    records: list of provenance dicts with source/account/id/url/date.
    errors: mapping of label → error message for partial failures.
    """

    records: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


def consume_records(result: AdapterResult) -> list[dict]:
    """Source-agnostic consumer: returns the records from an AdapterResult.

    No source-specific branching — works identically for Jira, OneNote, or
    any other adapter that produces an AdapterResult.
    """
    return result.records


def write_daily_jsonl(records: list[dict], source_dir: Path) -> Path:
    """Append records as JSONL under source_dir/daily/YYYY/MM/DD.jsonl.

    Deduplicates by (source, account, id): records whose composite key already
    exists in the file are skipped so that a second write on the same day
    does not overwrite or duplicate entries from the first write.  Duplicate
    keys within the same records list are also suppressed.
    Creates all intermediate directories.  Returns the path written.
    Raises ValueError if the existing file contains a malformed JSON line —
    refuses to append to a corrupted file to avoid data inconsistency.
    """
    source_dir = Path(source_dir)
    today = datetime.date.today()
    daily_dir = source_dir / "daily" / str(today.year) / f"{today.month:02d}"
    daily_dir.mkdir(parents=True, exist_ok=True)
    target = daily_dir / f"{today.day:02d}.jsonl"

    existing_keys: set[tuple] = set()
    if target.exists():
        with open(target, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if line:
                    try:
                        rec = json.loads(line)
                        existing_keys.add((
                            rec.get("source", ""),
                            rec.get("account", ""),
                            rec.get("id", ""),
                        ))
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"Existing JSONL file {target} is corrupted at line {lineno}: {exc}. "
                            "Refusing to append to avoid data inconsistency."
                        ) from exc

    seen_keys: set[tuple] = set(existing_keys)
    new_records: list[dict] = []
    for r in records:
        key = (r.get("source", ""), r.get("account", ""), r.get("id", ""))
        if key not in seen_keys:
            seen_keys.add(key)
            new_records.append(r)

    with open(target, "a", encoding="utf-8") as fh:
        for record in new_records:
            json.dump(record, fh, ensure_ascii=False)
            fh.write("\n")
    return target


def consume_jsonl_records(
    *source_dirs: Path,
    date: datetime.date | None = None,
) -> AdapterResult:
    """Read daily JSONL records from one or more source directories.

    Source-agnostic: works for any directory written by write_daily_jsonl().
    date defaults to today.  Missing or empty files are silently skipped.
    Malformed JSON lines are recorded in result.errors keyed by
    "<file_path>:<line_number>" so callers can detect and report data corruption
    without losing the valid records from other sources.
    """
    if date is None:
        date = datetime.date.today()
    records: list[dict] = []
    errors: dict[str, str] = {}
    for source_dir in source_dirs:
        source_dir = Path(source_dir)
        jsonl_path = (
            source_dir / "daily" / str(date.year) / f"{date.month:02d}" / f"{date.day:02d}.jsonl"
        )
        if not jsonl_path.exists():
            continue
        with open(jsonl_path, "r", encoding="utf-8") as fh:
            for lineno, line in enumerate(fh, 1):
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError as exc:
                        errors[f"{jsonl_path}:{lineno}"] = f"JSON decode error: {exc}"
    return AdapterResult(records=records, errors=errors)


def jira_issues_to_records(issues: list[dict], base_url: str) -> AdapterResult:
    """Convert raw Jira issue dicts to provenance records (F13FCAKE-18).

    Each record contains: source ("jira"), account (site URL), id (issue key),
    url (browse URL), date (created or updated timestamp from fields),
    summary (inline issue text — metadata_only convention, no blob hash references).
    Token and email are never included.
    """
    records = []
    for issue in issues:
        key = issue.get("key", "")
        fields = issue.get("fields") or {}
        record: dict = {
            "source": "jira",
            "account": base_url,
            "id": key,
            "url": f"{base_url}/browse/{key}",
            "date": fields.get("created") or fields.get("updated", ""),
            "summary": fields.get("summary", ""),
        }
        description = fields.get("description")
        if description:
            record["description"] = description
        records.append(record)
    return AdapterResult(records=records)


def onenote_pages_to_records(pages: list[dict], account: str) -> AdapterResult:
    """Convert raw OneNote page dicts to provenance records (F13FCAKE-18).

    Each record contains: source ("onenote"), account (Graph account URL),
    id (page id), url (oneNoteWebUrl or contentUrl), date (lastModifiedDateTime).
    """
    records = []
    for page in pages:
        page_id = page.get("id", "")
        web_url = ((page.get("links") or {}).get("oneNoteWebUrl") or {}).get("href", "")
        if not web_url:
            web_url = page.get("contentUrl", "")
        records.append({
            "source": "onenote",
            "account": account,
            "id": page_id,
            "url": web_url,
            "date": page.get("lastModifiedDateTime", ""),
        })
    return AdapterResult(records=records)
