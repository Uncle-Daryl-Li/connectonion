# Shared Source Record Contract (F13FCAKE-16)

One record shape for every co rem source integration. Jira and OneNote map
into it, and `validate_record()` checks both against the same rules.
Implementation: `connectonion/useful_tools/adapter.py`.
Tests: `tests/unit/test_jira.py` (cases `16-1` … `16-24`, `11-1` … `11-30`, `12-1` … `12-38`) and
`tests/unit/test_jira_adf.py`; `adapter.py` line and branch coverage 100%.

---

## Envelope (#1833)

Every record has exactly these top-level fields, plus `media` when the item
has images or attachments. Nothing else is allowed at the top level.

| Field | Required | Meaning | Jira | OneNote |
|-------|----------|---------|------|---------|
| `id` | yes | Global identity, `source:account:external_id` | `jira:<site>:<key>` | `onenote:<account>:<page id>` |
| `source` | yes | Source name | `jira` | `onenote` |
| `account` | yes | Site or account the item was read from | site URL | an id naming the signed-in user (see Identity); the Graph base URL in fixtures is a placeholder only |
| `external_id` | yes | The source's own id | issue key | page id |
| `kind` | yes | Item type | issue type name, else `issue` | `page` |
| `title` | no | Title | `summary` | `title` |
| `text` | no | Readable content | description converted from ADF by `jira_adf.adf_to_text()` (light Markdown: headings, lists, code fences, quotes, tables; media as `[attachment: name]`) | empty (HTML → text is F13FCAKE-14) |
| `url` | no | Link back to the original item | browse URL | `oneNoteWebUrl`, else `contentUrl` |
| `author` | no | Who wrote it (display name) | reporter, else creator | — |
| `at` | no | Created time, as the source gives it | `created` | `createdDateTime` |
| `updated` | no | Last modified time | `updated` | `lastModifiedDateTime` |
| `fetched` | yes | When this import read it (UTC, `Z`) | import time | import time |
| `extensions` | yes (may be `{}`) | Source-only fields | see below | see below |
| `media` | only when present | Image / attachment references | `attachment` | — (not read in Sprint 1) |

All fields except `extensions` and `media` are strings. Every envelope field must
be present: optional ones may be empty (`""` or `{}`) but never missing —
`validate_record()` reports `missing field '<name>'` otherwise.

Jira content comes from `Jira.issue_items()`, which asks `/search/jql` for
`jira.ISSUE_FIELDS` explicitly (`fields=` on every page), so the record does not
depend on the server's default field set (F13FCAKE-11).

## Identity

`id = source + ":" + account (scheme and trailing "/" removed) + ":" + external_id`

- The same Jira key on two sites gives two ids.
- The same value as a Jira key and a OneNote page id gives two ids.
- `validate_record()` rejects a record whose `id` does not match this rule.
- OneNote: `account` must name the user (for example the signed-in user's
  object id), so the same page id read for two users gives two ids. A shared
  value such as the Graph base URL would make them collide; the OneNote import
  (F13FCAKE-14/15) supplies the per-user value.

## Extensions

Source-specific data is kept, not flattened, under `extensions`. Empty values
are left out.

| Source | Keys |
|--------|------|
| Jira | `issue_id`, `status`, `priority`, `labels`, `assignee`, `reporter`, `parent`, `duedate`, `resolutiondate`, `description_adf` (the raw ADF, kept so nothing in the original is lost), `changelog` (see below) |
| OneNote | `section`, `notebook`, `url_is_api` (true when `url` is the Graph content URL, not a web link) |

People are stored by display name only. Account emails and tokens never enter
a record.

### Jira change history (F13FCAKE-12)

`co jira sync` reads each issue's changelog (`/rest/api/3/issue/{key}/changelog`, all
pages) and stores it on that issue's own record, matched by issue key:

```json
"changelog": {
  "status": "ok",
  "entries": [
    {"id": "10", "at": "2026-10-05T10:00:00.000+1100", "author": "Diego Example",
     "changes": [{"field": "status", "from": "To Do", "to": "Done"}]}
  ]
}
```

| `status` | Meaning | Issue content |
|----------|---------|---------------|
| `ok` | history read, at least one entry (oldest first) | kept |
| `none` | history read, the issue was never edited | kept |
| `restricted` | Jira returned 403 for this issue's history; `reason` says so | kept |
| `unavailable` | anything else for this issue (rate limit, 5xx, network, missing outcome); `reason` says why | kept |

- "Never edited" (`none`) and "could not read" (`restricted` / `unavailable`) are never mixed up.
- A history problem on one issue never drops that issue or any other; the sync still exits 0.
- An authentication or configuration failure is not per issue: it stops the sync before
  `source.yaml` or any record is written.
- A change value longer than 1000 characters is cut and marked; malformed history entries are
  skipped and counted under `skipped`.
- `co jira sync --no-history` skips these reads; the records then have no `changelog` key.
- If history was skipped, restricted or unavailable, retrying with readable history appends
  the recovered evidence even when Jira's `updated` timestamp has not changed. An identical
  retry appends nothing; only a new `fetched` time does not create a snapshot.
- `--json` output reports `"history": {"ok": n, "none": n, "restricted": n, "unavailable": n}`
  (`null` with `--no-history`).

## Media (#1938)

```json
{"sha256": null, "name": "diagram.png", "mime": "image/png", "size": 2048, "status": "metadata_only"}
```

- `status` is one of `metadata_only`, `stored`, `unavailable`.
- Sprint 1 is metadata only: nothing is downloaded, `sha256` is `null`.
- A `stored` item must carry its `sha256` content hash (the blob reference).
- No blob, quota or eviction engine is part of this Story.

## Validation and rejected items

`jira_issues_to_records()` and `onenote_pages_to_records()` never raise on bad
input. Each item is mapped and checked; a valid record goes to
`AdapterResult.records`, anything else goes to `AdapterResult.errors`:

```
"jira:item-1":   "mapping error: item is not an object"
"jira:ALPHA-9":  "mapping error: 'fields' is not an object"
"jira:item-3":   "mapping error: missing required field 'external_id'; ..."
"jira:ALPHA-9#4": "mapping error: ..."   (a second bad item with the same key: "#<position>")
"jira:ALPHA-50":  "mapping error: duplicate of an earlier item in this batch (jira:<site>:ALPHA-50); the first copy was kept"
```

Every rejected item gets its own entry; valid items in the same batch are unaffected.
This includes failures nobody anticipated: if mapping one item raises (for example
an ADF date node far outside the calendar), that item is reported as
`content could not be mapped (<error type>: …)` and the batch continues.
The same item twice in one batch (for example a server repeating an issue on the
next page) produces one record; the repeat is reported, so pagination can neither
duplicate nor silently drop items (F13FCAKE-11).

`co jira sync` never hides them: it still writes the valid records, prints
`Records mapped: N  Records appended: M`, lists each rejected item on stderr
(or under `"rejected"` next to `"mapped"` and `"appended"` in `--json` output)
and exits with code 1. A clean run has `"rejected": {}` and exits 0.
`mapped` counts records that passed the contract; `appended` counts the ones
actually added to today's file, so a same-day re-sync shows `appended: 0`.

## Layout (#1938)

Records are placed under `~/.co/rem/docs/<source>/`:

```
~/.co/rem/docs/<source>/
  source.yaml                       scope, account, retention, media_policy
  daily/YYYY/MM/DD.jsonl            ongoing imports, one record per line
  exports/<id>/manifest.json        one-off import: scope and counts
  exports/<id>/records.jsonl        one-off import: records
```

Every item is placed explicitly by the caller (F13FCAKE-11). `co jira sync` reports
which one it used as `"placement": "daily"` or `"export"` in `--json` output, and refuses an
unusable `--export` id before contacting Jira or writing any file:

| Placement | Use for | Writer | Behaviour |
|-----------|---------|--------|-----------|
| `daily/YYYY/MM/DD.jsonl` | ongoing changes from a sync — `co jira sync PROJECT KEY…` | `write_daily_jsonl` / `append_daily_jsonl` (F13FCAKE-18) | dated by the import day; deduplicates `(source, account, id, updated, content hash)`, ignoring `fetched`; an edit or newly read history appends a snapshot even with the same `updated`. Existing lines and earlier days' files are never rewritten (F13FCAKE-12); `append_daily_jsonl` also returns how many were new |
| `exports/<id>/` | a one-off import, e.g. a project's history — `co jira sync PROJECT KEY… --export <id>` | `write_export` (F13FCAKE-11 prototype) | written once, never appended; refuses an unsafe or existing id, any invalid or repeated record, and data that cannot be saved as JSON before writing anything; staged in a hidden `.<id>.partial` directory and renamed into place; if writing fails the staging directory is removed so the same id can be retried (a `.partial` left by a crash is reported with instructions to delete it) |

`exports/<id>/manifest.json` example:

```json
{
  "export_id": "ALPHA-history-2026-10-12",
  "created": "2026-10-12T05:00:00Z",
  "scope": {"projects": ["ALPHA"], "issues": "all"},
  "sources": ["jira"],
  "accounts": ["https://example.atlassian.net"],
  "counts": {"records": 3, "by_kind": {"Bug": 1, "Story": 2}},
  "files": ["records.jsonl"],
  "contract": "#1833 shared record envelope — docs/acceptance/record-contract.md"
}
```

An export id may contain letters, digits, `.`, `_` and `-` (max 64). An empty
export (0 records) is a valid outcome. These are prototype writers for the
client's layout; the general storage, blob, quota and eviction engines stay
with the client.

### References (F13FCAKE-12)

A page cites a record by file, id and snapshot version, for example
`docs/jira/daily/2026/10/07.jsonl#jira:example.atlassian.net:DEMO-42?version=<sha256>`.

- `record_reference(file, record, notebook_root)` builds it: the file path relative to
  the notebook root, always with `/`, then `#`, the record id and `?version=<sha256>`.
  The SHA-256 covers canonical JSON of all fields except `fetched`, including history.
  Object key order has no effect. A file outside the notebook or a record without an id
  raises `ValueError`.
- `resolve_reference(reference, notebook_root)` returns the matching snapshot. Appending
  another version or recovering history cannot move an existing citation. A missing file,
  unknown id/version, malformed version, reference without `#` or path outside the
  notebook gives `None`; damaged JSON lines are skipped. A missing version never falls
  back to the latest line.
- Legacy `file#id` references remain readable and return the first matching line. They
  never specified a version, so any intended later snapshot is ambiguous; regenerate
  such citations with `record_reference()` and the intended record to pin it precisely.
- Daily files and `exports/<id>/records.jsonl` are cited the same way.

## Not in this contract yet

| Item | Story |
|------|-------|
| Who owns the production `exports/` writer (prototype here) | confirm on #1938 |
| OneNote page HTML → text, per-user `account` value | F13FCAKE-14 / F13FCAKE-15 |

## Verify

```
python -m pytest tests/unit/test_jira.py -q
```

Expected: all tests pass, including `16-1` … `16-24`, `11-1` … `11-30` and `12-1` … `12-38`.

Coverage check:

```
python -m pytest tests/unit/test_jira.py -q --cov=connectonion.useful_tools.adapter --cov-branch --cov-report=term-missing
```

Expected: `adapter.py` 100%.
