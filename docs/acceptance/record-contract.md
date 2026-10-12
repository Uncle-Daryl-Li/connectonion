# Shared Source Record Contract (F13FCAKE-16)

One record shape for every co rem source integration. Jira and OneNote map
into it, and `validate_record()` checks both against the same rules.
Implementation: `connectonion/useful_tools/adapter.py`.
Tests: `tests/unit/test_jira.py` (cases `16-1` … `16-24`); `adapter.py` line and branch coverage 100%.

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
| Jira | `issue_id`, `status`, `priority`, `labels`, `assignee`, `reporter`, `parent`, `duedate`, `resolutiondate`, `description_adf` (the raw ADF, kept so nothing in the original is lost) |
| OneNote | `section`, `notebook`, `url_is_api` (true when `url` is the Graph content URL, not a web link) |

People are stored by display name only. Account emails and tokens never enter
a record.

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

Every item is placed explicitly by the caller (F13FCAKE-11):

| Placement | Use for | Writer | Behaviour |
|-----------|---------|--------|-----------|
| `daily/YYYY/MM/DD.jsonl` | ongoing changes from a sync | `write_daily_jsonl` / `append_daily_jsonl` (F13FCAKE-18) | dated by the import day; appends, skipping a record whose `(source, account, id)` is already in that day's file; `append_daily_jsonl` also returns how many were new |
| `exports/<id>/` | a one-off import, e.g. a project's history | `write_export` (F13FCAKE-11 prototype) | written once, never appended; refuses an unsafe or existing id, any invalid or repeated record, and data that cannot be saved as JSON before writing anything; staged in a hidden `.<id>.partial` directory and renamed into place; if writing fails the staging directory is removed so the same id can be retried (a `.partial` left by a crash is reported with instructions to delete it) |

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

A page cites a record by file and id, for example
`docs/jira/daily/2026/10/07.jsonl#jira:example.atlassian.net:DEMO-42`.

## Not in this contract yet

| Item | Story |
|------|-------|
| Changelog under `extensions.changelog`, new version on update (`updated` in the dedupe key) | F13FCAKE-12 |
| Who owns the production `exports/` writer (prototype here); a `co jira sync --export <id>` option | confirm on #1938; CLI option with F13FCAKE-18 |
| OneNote page HTML → text, per-user `account` value | F13FCAKE-14 / F13FCAKE-15 |

## Verify

```
python -m pytest tests/unit/test_jira.py -q
```

Expected: all tests pass, including `16-1` … `16-24`.

Coverage check:

```
python -m pytest tests/unit/test_jira.py -q --cov=connectonion.useful_tools.adapter --cov-branch --cov-report=term-missing
```

Expected: `adapter.py` 100%.
