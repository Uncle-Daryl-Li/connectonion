"""
Purpose: Shared record contract and common adapter for co rem source integrations (F13FCAKE-16, F13FCAKE-18).
LLM-Note:
  Dependencies: imports from [datetime, json, dataclasses, pathlib, useful_tools/jira_adf.adf_to_text] | imported by [tests/unit/test_jira.py, cli/commands/jira_commands.py] | no network calls
  Data flow: raw tool output (Jira issues / OneNote pages) → *_to_records() → make_record() envelope → validate_record() → AdapterResult (valid records + per-item errors) → write_daily_jsonl stores under daily/YYYY/MM/DD.jsonl
  State/Effects: write_daily_jsonl() / append_daily_jsonl() create directories and write files (append_daily_jsonl also returns how many records were new); all other functions are pure
  Integration: RECORD_FIELDS is the #1833 envelope every source writes; record_id() is the shared identity rule; make_record() / media_ref() build records; validate_record() is the one contract check for every source; AdapterResult is the shared result type; consume_records() is the in-memory consumer; consume_jsonl_records() reads from disk across multiple source directories; jira_issues_to_records() and onenote_pages_to_records() are the converter functions
  Errors: write_daily_jsonl() raises ValueError if the existing JSONL file contains a malformed line (refuses to append to a corrupted file) | consume_jsonl_records() never raises — malformed lines are recorded in AdapterResult.errors keyed by "file:lineno" | converters never raise on bad input — an invalid or malformed item is left out of records and reported in AdapterResult.errors keyed by "<source>:<item>"
  F13FCAKE-16 contract status:
    Envelope, identity, extensions, media references and validation implemented for both sources; documented in docs/acceptance/record-contract.md
    OneNote page HTML is not fetched here; readable text conversion is F13FCAKE-14
  F13FCAKE-11 Jira content status:
    Jira.issue_items() requests jira.ISSUE_FIELDS; description ADF → record["text"] via jira_adf.adf_to_text(),
    raw ADF kept under extensions.description_adf; due and resolution dates kept under extensions
  F13FCAKE-18 prototype status:
    Jira chain -- fully integrated: verify_connection -> issue_items -> write_source_yaml -> jira_issues_to_records -> write_daily_jsonl
    OneNote chain -- adapter function implemented and tested with synthetic fixtures; live Notes.Read integration is pending authorized account credentials and OneNote team implementation

Common adapter contract for co rem integrations.

Usage:
    from connectonion.useful_tools.adapter import (
        AdapterResult, consume_records, consume_jsonl_records, validate_record,
        write_daily_jsonl, jira_issues_to_records, onenote_pages_to_records,
    )
    result = jira_issues_to_records(issues, base_url)
    records = consume_records(result)          # only records that passed validate_record()
    rejected = result.errors                   # "<source>:<item>" -> reason
    jsonl_path = write_daily_jsonl(result.records, source_dir)
    # Read back from disk (source-agnostic):
    result = consume_jsonl_records(jira_dir, onenote_dir)
    all_records = result.records  # result.errors contains any malformed-line diagnostics
"""

import datetime
import json
from dataclasses import dataclass, field
from pathlib import Path

from .jira_adf import adf_to_text

# The shared envelope (#1833): every source writes exactly these top-level fields, plus optional "media".
RECORD_FIELDS = ("id", "source", "account", "external_id", "kind", "title", "text",
                 "url", "author", "at", "updated", "fetched", "extensions")
REQUIRED_FIELDS = ("id", "source", "account", "external_id", "kind", "fetched")
MEDIA_FIELDS = ("sha256", "name", "mime", "size", "status")
MEDIA_STATUSES = ("metadata_only", "stored", "unavailable")


@dataclass
class AdapterResult:
    """Common result type for all co rem source adapters (F13FCAKE-16, F13FCAKE-18).

    records: list of envelope dicts that passed validate_record().
    errors: mapping of label → error message for partial failures and rejected items.
    """

    records: list[dict] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)


# Shared identity rule: source + account + external id, e.g. "jira:test.atlassian.net:ALPHA-1".
def record_id(source: str, account: str, external_id: str) -> str:
    """Globally unique record id; the same external id in two sources or two accounts never collides."""
    account_key = account.split("://", 1)[-1].rstrip("/")
    return f"{source}:{account_key}:{external_id}"


def _now_utc() -> str:
    stamp = datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0)
    return stamp.isoformat().replace("+00:00", "Z")


# Build one envelope; anything source-specific belongs under extensions, never at the top level.
def make_record(source: str, account: str, external_id: str, kind: str, *,
                title: str = "", text: str = "", url: str = "", author: str = "",
                at: str = "", updated: str = "", extensions: dict | None = None,
                media: list[dict] | None = None, fetched: str | None = None) -> dict:
    """Return a record in the shared envelope; "media" is present only when there is media."""
    record = {
        "id": record_id(source, account, external_id),
        "source": source, "account": account, "external_id": external_id, "kind": kind,
        "title": title, "text": text, "url": url, "author": author,
        "at": at, "updated": updated, "fetched": fetched or _now_utc(),
        "extensions": extensions or {},
    }
    if media:
        record["media"] = media
    return record


# Media reference (#1938): content hash when the bytes are stored, metadata only otherwise.
def media_ref(name: str, mime: str = "", size: int | None = None,
              sha256: str | None = None, status: str = "metadata_only") -> dict:
    """Describe an image or attachment without embedding or downloading it."""
    return {"sha256": sha256, "name": name, "mime": mime, "size": size, "status": status}


# The one contract check every source mapping is validated against; empty list means valid.
def validate_record(record) -> list[str]:
    """Return every contract violation in one record, worded for a mapping-error report."""
    if not isinstance(record, dict):
        return ["record is not an object"]
    # Every envelope field must be present; optional ones may be empty ("" or {}), never absent.
    problems = [f"missing field '{name}'" for name in RECORD_FIELDS if name not in record]
    problems += [f"missing required field '{name}'" for name in REQUIRED_FIELDS
                 if name in record and not record[name]]
    problems += [f"field '{name}' must be a string" for name in RECORD_FIELDS
                 if name != "extensions" and name in record and not isinstance(record[name], str)]
    if "extensions" in record and not isinstance(record["extensions"], dict):
        problems.append("field 'extensions' must be an object")
    expected = record_id(str(record.get("source", "")), str(record.get("account", "")),
                         str(record.get("external_id", "")))
    if record.get("id") and record["id"] != expected:
        problems.append(f"field 'id' must be '{expected}' (source:account:external_id)")
    problems += _media_problems(record.get("media", []))
    problems += [f"unknown top-level field '{name}'; put source-specific data under extensions"
                 for name in sorted(set(record) - set(RECORD_FIELDS) - {"media"})]
    return problems


def _media_problems(media) -> list[str]:
    if not isinstance(media, list):
        return ["field 'media' must be a list"]
    problems = []
    for index, item in enumerate(media):
        if not isinstance(item, dict) or set(item) != set(MEDIA_FIELDS):
            problems.append(f"media[{index}] must have exactly: {', '.join(MEDIA_FIELDS)}")
        elif item["status"] not in MEDIA_STATUSES:
            problems.append(f"media[{index}] has unknown status {item['status']!r}")
        elif item["status"] == "stored" and not item["sha256"]:
            problems.append(f"media[{index}] is stored but has no sha256 content hash")
    return problems


# Map every item, keep only valid records; malformed or invalid items are reported, not raised.
def _collect(items: list, source: str, shape_problems, build) -> AdapterResult:
    result = AdapterResult()
    for index, item in enumerate(items):
        problems = shape_problems(item)
        record = None if problems else build(item)
        problems = problems or validate_record(record)
        if problems:
            result.errors[_error_label(result.errors, source, item, index)] = "mapping error: " + "; ".join(problems)
        else:
            result.records.append(record)
    return result


# Error labels stay unique: a second bad item with the same key gets "#<position>" instead of overwriting.
def _error_label(errors: dict, source: str, item, index: int) -> str:
    label = f"{source}:{_item_label(item, index)}"
    return f"{label}#{index}" if label in errors else label


def _item_label(item, index: int) -> str:
    if isinstance(item, dict) and (item.get("key") or item.get("id")):
        return str(item.get("key") or item.get("id"))
    return f"item-{index}"


def consume_records(result: AdapterResult) -> list[dict]:
    """Source-agnostic consumer: returns the records from an AdapterResult.

    No source-specific branching — works identically for Jira, OneNote, or
    any other adapter that produces an AdapterResult.
    """
    return result.records


def write_daily_jsonl(records: list[dict], source_dir: Path) -> Path:
    """Append records as JSONL under source_dir/daily/YYYY/MM/DD.jsonl and return that path.

    Same behaviour as append_daily_jsonl(); use that when the caller needs to know how
    many records were actually appended (CLI summaries).
    """
    return append_daily_jsonl(records, source_dir)[0]


# Same append as write_daily_jsonl, but also reports how many records were new (not deduplicated away).
def append_daily_jsonl(records: list[dict], source_dir: Path) -> tuple[Path, int]:
    """Append records as JSONL under source_dir/daily/YYYY/MM/DD.jsonl.

    Deduplicates by (source, account, id): records whose composite key already
    exists in the file are skipped so that a second write on the same day
    does not overwrite or duplicate entries from the first write.  Duplicate
    keys within the same records list are also suppressed.
    Creates all intermediate directories.  Returns (path written, number of records appended).
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
    return target, len(new_records)


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


def jira_issues_to_records(issues: list[dict], base_url: str, *, fetched: str | None = None) -> AdapterResult:
    """Convert raw Jira issue dicts to shared-contract records (F13FCAKE-11, F13FCAKE-16, F13FCAKE-18).

    Envelope: id = "jira:<site>:<key>", external_id = issue key, kind = issue type,
    title = summary, text = description as readable text (ADF converted by jira_adf.adf_to_text),
    url = browse URL, author = reporter, at = created, updated = updated.
    Jira-only fields (status, priority, labels, people, parent, due/resolution dates, raw ADF) go under extensions;
    attachments become metadata-only media references. Token and email are never included.
    An item that is not an object or fails validate_record() is reported in errors, not raised.
    """
    stamp = fetched or _now_utc()
    return _collect(issues, "jira", _jira_shape_problems, lambda issue: _jira_record(issue, base_url, stamp))


def _jira_shape_problems(issue) -> list[str]:
    if not isinstance(issue, dict):
        return ["item is not an object"]
    if not isinstance(issue.get("fields", {}), dict):
        return ["'fields' is not an object"]
    return []


# One Jira issue → one envelope; the description becomes readable text, its raw ADF stays in extensions.
def _jira_record(issue: dict, base_url: str, fetched: str) -> dict:
    fields = issue.get("fields") or {}
    key = str(issue.get("key") or "")
    return make_record(
        "jira", base_url, key, _name(fields.get("issuetype")) or "issue",
        title=str(fields.get("summary") or ""),
        text=adf_to_text(fields.get("description")),
        url=f"{base_url}/browse/{key}" if key else "",
        author=_display_name(fields.get("reporter") or fields.get("creator")),
        at=str(fields.get("created") or ""), updated=str(fields.get("updated") or ""),
        extensions=_jira_extensions(issue, fields),
        media=[_jira_media(item) for item in _list(fields.get("attachment")) if isinstance(item, dict)],
        fetched=fetched,
    )


# Jira-only fields kept under extensions; empty values are left out.
def _jira_extensions(issue: dict, fields: dict) -> dict:
    parent = fields.get("parent")
    description = fields.get("description")
    values = {
        "issue_id": str(issue.get("id") or ""),
        "status": _name(fields.get("status")),
        "priority": _name(fields.get("priority")),
        "labels": [str(label) for label in _list(fields.get("labels"))],
        "assignee": _display_name(fields.get("assignee")),
        "reporter": _display_name(fields.get("reporter")),
        "parent": str(parent.get("key") or "") if isinstance(parent, dict) else "",
        "duedate": str(fields.get("duedate") or ""),
        "resolutiondate": str(fields.get("resolutiondate") or ""),
        "description_adf": description if isinstance(description, dict) else None,
    }
    return {name: value for name, value in values.items() if value not in ("", None, [])}


def _jira_media(attachment: dict) -> dict:
    return media_ref(str(attachment.get("filename") or ""), str(attachment.get("mimeType") or ""),
                     attachment.get("size"))


def _name(value) -> str:
    return str(value.get("name") or "") if isinstance(value, dict) else ""


def _display_name(person) -> str:
    """Display name only; account emails never enter a record."""
    return str(person.get("displayName") or "") if isinstance(person, dict) else ""


def _list(value) -> list:
    return value if isinstance(value, list) else []


def onenote_pages_to_records(pages: list[dict], account: str, *, fetched: str | None = None) -> AdapterResult:
    """Convert raw OneNote page dicts to shared-contract records (F13FCAKE-16, F13FCAKE-18).

    Envelope: id = "onenote:<account>:<page id>", external_id = page id, kind = "page",
    title, url (oneNoteWebUrl, or contentUrl flagged extensions.url_is_api),
    at = createdDateTime, updated = lastModifiedDateTime; section and notebook go under extensions.
    Page HTML is not fetched here (F13FCAKE-14). Invalid items are reported in errors, not raised.
    """
    stamp = fetched or _now_utc()
    return _collect(pages, "onenote", _onenote_shape_problems, lambda page: _onenote_record(page, account, stamp))


def _onenote_shape_problems(page) -> list[str]:
    return [] if isinstance(page, dict) else ["item is not an object"]


# One OneNote page → one envelope; the web link is preferred over the Graph content URL.
def _onenote_record(page: dict, account: str, fetched: str) -> dict:
    links = page.get("links") if isinstance(page.get("links"), dict) else {}
    web = links.get("oneNoteWebUrl")
    web_url = str(web.get("href") or "") if isinstance(web, dict) else ""
    content_url = str(page.get("contentUrl") or "")
    values = {
        "section": _parent_name(page.get("parentSection")),
        "notebook": _parent_name(page.get("parentNotebook")),
        "url_is_api": True if not web_url and content_url else None,
    }
    return make_record(
        "onenote", account, str(page.get("id") or ""), "page",
        title=str(page.get("title") or ""), url=web_url or content_url,
        at=str(page.get("createdDateTime") or ""), updated=str(page.get("lastModifiedDateTime") or ""),
        extensions={name: value for name, value in values.items() if value not in ("", None)},
        fetched=fetched,
    )


def _parent_name(parent) -> str:
    return str(parent.get("displayName") or "") if isinstance(parent, dict) else ""
