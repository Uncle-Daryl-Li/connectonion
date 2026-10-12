# OneNote source collection for co rem

`co onenote source` provides authentication verification, selected page import
and scope selection. It reads remote
OneNote data and writes local records. It never creates, edits or deletes
OneNote pages and never downloads images or attachments.

## Baseline and authentication

- Story branches: `F13FCAKE-13` (connection), `F13FCAKE-15` (scope), `F13FCAKE-14` (import). Review/merge dependencies: 13 → 15 → 14. The original combined branch is preserved as historical evidence.
- Starting revision: `d69aecda` (`origin/develop` at implementation time).
- Tested source version: ConnectOnion `1.9.2b10`, Python `3.13.5`, Windows.
- This baseline is newer than the task's original `1.9.0a39` example; no 1.8.x
  baseline or separate team-managed Azure application is used.
- Use the existing `co auth microsoft` flow and selected Microsoft credential
  record, including its token refresh. `Notes.Read` is the least privilege scope.
  Existing `Notes.Read.All`, `Notes.ReadWrite` or `Notes.ReadWrite.All` grants
  also permit reads; this source never requests an upgrade to write access.
- Account verification also uses `/me` and the `User.Read` grant included in
  the client's Microsoft flow. The stable account ID is saved, not its token.

```console
co auth microsoft
co onenote source verify --json
```

If the client's consent flow still omits OneNote read permission, reconnecting
cannot fix the broker. Record the external dependency on
[#1886](https://github.com/openonion/connectonion/issues/1886) and wait for the
client's supported flow. Do not invent another credential flow or Azure app.
Offline fixture success does not establish live consent support.

## Choose the exact scope

```console
co onenote source notebooks --json
co onenote source sections NOTEBOOK_ID --json
co onenote source pages NOTEBOOK_ID SECTION_ID --limit 100 --json
```

Notebook and section IDs are preferred; unique exact names also work. Duplicate
names, unknown IDs and inaccessible scopes are rejected. Discovery follows all
Graph pages and includes nested section groups. Empty notebooks and sections
are valid results. Each page listing is bounded (1–1000); `truncated: true`
explicitly means more pages remain. Increase `--limit` to inspect a larger set;
the source never silently claims this bounded listing is a complete notebook.

Copy only the desired page IDs into the import command. Before reading each
page's HTML, collection verifies its current parent notebook and section. A
moved page or one outside the scope is skipped with an error; its body is not
fetched. Only explicit IDs are collected. Pagination links must stay on the
same Microsoft Graph collection; redirects, foreign hosts and loops are refused.

## Daily import and one-off export

```console
co onenote source sync NOTEBOOK_ID SECTION_ID PAGE_ID_1 PAGE_ID_2 --mode daily --json
co onenote source sync NOTEBOOK_ID SECTION_ID PAGE_ID_1 --mode export --export-id sprint1-demo --json
```

Default storage is under `~/.co/rem/docs/onenote/`:

```text
source.yaml
daily/YYYY/MM/DD.jsonl
exports/sprint1-demo/manifest.json
exports/sprint1-demo/records.jsonl
```

Use `--output PATH` for an isolated test directory. A source directory belongs
to one account and one exact selection; use a different directory when changing
accounts, notebooks, sections or page selections. Existing export IDs cannot be
overwritten. Every record has an `extensions.ingestion` field identifying its mode and
relative JSONL path.

`source.yaml` contains driver, stable account, notebook/section/page scope,
`retention: 90d`, `media_policy: metadata_only`, the version, exact import command,
config path and validation timestamp. Retention is a declaration for the
downstream workflow; this collector does not run a deletion scheduler.

The daily writer deduplicates `(source, account, id)` within a day. Output reports
`records` (collected), `written` and `skipped`; skipped IDs include pages changed
since an earlier same-day import. Incremental refresh belongs to F13FCAKE-24 and
is not claimed here. Use a new one-off export ID for a fresh independent snapshot.

## Record and media contract

Records use the shared F13FCAKE-16 envelope and F13FCAKE-18 `AdapterResult`,
validated by the same `adapter.validate_record()` as Jira. `id` is the global
`source:account:external_id` identity; `external_id` retains the original page ID.
Readable HTML-derived content is `text`, creation time is `at`, modification
time is `updated`, and collection time is `fetched`. Scope and import
classification live under `extensions.scope` and `extensions.ingestion`.
See [shared record contract](../acceptance/record-contract.md). Optional missing
metadata does not discard readable content. An absent title becomes `(untitled)`;
an absent source link falls back to the page's Graph metadata endpoint. An empty
page is a valid empty record. Scripts/styles are excluded from text.

Each `media` entry contains exactly `sha256` (null), `name`, `mime`, `size`,
and `status: metadata_only`. The corresponding source descriptors, including
kind and HTTPS `fetch_reference`, live in `extensions.media_fetch_references`
in the same order as `media`. Unknown optional values are
empty or null. References are not dereferenced; query strings and embedded data
URLs are not saved. No media download, OCR, transcription, blob store or inferred
media processing is implemented.

Daily output can be read by the existing source-agnostic
`consume_jsonl_records(jira_dir, onenote_dir)` consumer. Export consumers read
`manifest.json` plus `records.jsonl`; the daily consumer does not scan exports.

## Errors and exit codes

| Exit | Meaning |
| --- | --- |
| 0 | Successful verification/listing/import, including valid empty listings |
| 1 | Fatal authentication, scope, local storage or malformed listing failure |
| 2 | One or more selected pages failed; valid pages were retained |

`--json` produces a single JSON object on stdout. Partial failures are keyed by
page ID. Missing, removed, inaccessible or malformed pages do not abort other
valid pages. Authentication expiration that cannot be refreshed aborts with
`co auth microsoft` recovery guidance. No provider response bodies or tokens are
printed. If every selected page fails, no import files are created. Corrupted
existing JSONL is preserved and no records are appended.

Page HTML reads retry HTTP 429, 503 and 504 at most twice (three transient
attempts, plus at most one authentication refresh request). The collector honors
`Retry-After` seconds or HTTP-date; absent or invalid values use 1s then 2s
backoff. Exhausted retries report the page failure and advise waiting before
retrying the import; other valid pages are retained. Redirects remain disabled
and media resources are never fetched.
If the server asks to wait more than 30s, the page is reported unavailable with
wait/retry guidance instead of blocking the whole import or retrying early.

## Current rem approval boundary

The following help was inspected on the actual selected preview:

```console
co rem --help
co rem sources --help
```

This baseline's runtime registry lists gmail, outlook, codex, claude-code and
whatsapp. `co rem sources add onenote` is not supported. Its existing workflow
stores source choices with `sources add`, then `rem start` approves sources and
scheduling; a configuration file does not bypass that approval.

This collector explicitly marks `registration: external_import_only`. It
produces the external record handoff for F13FCAKE-18; it does not claim automatic
registration, scheduling, investigation, or runtime consumption by `co rem sync`.
Keep the [source/listener boundary (#1833)](https://github.com/openonion/connectonion/issues/1833)
and the [storage layout discussion (#1938)](https://github.com/openonion/connectonion/issues/1938)
as upstream integration references. Runtime integration must follow the client
workflow when that support is available.

## Reproduce verification

All fixtures under `tests/fixtures/onenote/` are synthetic and contain no account
data. Mock Graph tests enforce GET-only requests and cover scope enforcement,
pagination (including nested groups and unsafe links), empty pages, HTML/media,
401 refresh and safe failures, partial imports, storage, JSON CLI output and
same-day deduplication. The Microsoft credential and existing Jira/OneNote suites
are regression targets too.

```console
python -m pytest tests/unit/test_onenote_source_auth.py tests/unit/test_onenote_source_scope.py tests/unit/test_onenote_source.py tests/unit/test_onenote.py tests/unit/test_jira.py tests/unit/test_provider_credentials.py -q
```

For Windows sandbox execution, create `.pytest-local` and add
`--basetemp=.pytest-local/RUN_ID` with a new RUN_ID to keep temporary test homes
inside the workspace. In this environment use `.venv/Scripts/python.exe` instead
of the system Python; CLI checks used
`.venv/Scripts/python.exe -m connectonion.cli.main ...` with the editable source.

For an authorized live check, verify the account, list the chosen scope and
import only explicit page IDs to an isolated `--output` directory. Inspect the
record provenance, readable text, media descriptors, source configuration and
export manifest locally. Keep raw notes and credentials out of version control.
Record the tested commit, version, sanitized commands and process exit codes.
Test a known empty page and a permitted unavailable-page scenario separately;
retain valid records when a selected page fails. Broader granted permissions do
not establish that a Notes.Read-only consent profile works. Enterprise tenant
approval and automatic rem runtime registration remain separate limitations.

API references: [list pages and permissions](https://learn.microsoft.com/en-us/graph/api/onenote-list-pages?view=graph-rest-1.0),
[page metadata and HTML](https://learn.microsoft.com/en-us/graph/api/page-get?view=graph-rest-1.0),
[section groups](https://learn.microsoft.com/en-us/graph/api/resources/sectiongroup?view=graph-rest-1.0).
