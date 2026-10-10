"""
Purpose: Common adapter contract for co rem source integrations (F13FCAKE-18).
LLM-Note:
  Dependencies: imports from [datetime, json, dataclasses, pathlib] | imported by [tests/unit/test_jira.py, cli/commands/jira_commands.py] | no network calls
  Data flow: raw tool output (Jira issues / OneNote pages) → adapter functions → AdapterResult with provenance records → write_daily_jsonl stores under daily/YYYY/MM/DD.jsonl
  State/Effects: write_daily_jsonl() creates directories and writes files; all other functions are pure
  Integration: AdapterResult is the shared record type; consume_records() is the source-agnostic consumer; jira_issues_to_records() and onenote_pages_to_records() are the converter functions
  Errors: none raised; malformed input produces empty/partial records

Common adapter contract for co rem integrations.

Usage:
    from connectonion.useful_tools.adapter import (
        AdapterResult, consume_records, write_daily_jsonl,
        jira_issues_to_records, onenote_pages_to_records,
    )
    result = jira_issues_to_records(issues, base_url)
    records = consume_records(result)
    jsonl_path = write_daily_jsonl(result.records, source_dir)
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
    """Write records as JSONL under source_dir/daily/YYYY/MM/DD.jsonl.

    Creates all intermediate directories.  Returns the path written.
    """
    source_dir = Path(source_dir)
    today = datetime.date.today()
    daily_dir = source_dir / "daily" / str(today.year) / f"{today.month:02d}"
    daily_dir.mkdir(parents=True, exist_ok=True)
    target = daily_dir / f"{today.day:02d}.jsonl"
    with open(target, "w", encoding="utf-8") as fh:
        for record in records:
            json.dump(record, fh, ensure_ascii=False)
            fh.write("\n")
    return target


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
