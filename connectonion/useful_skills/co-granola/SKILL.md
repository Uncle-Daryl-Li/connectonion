---
name: co-granola
description: Import personal Granola meeting notes into co rem through `co granola` — verify the API key, list personal folders, list note IDs, and import notes (all or one) as co rem records. Use when the user mentions Granola, "meeting notes", "import my meetings", "import a specific note", or wants to add Granola as a co rem source.
---

# co granola

`co granola` imports notes from one user-selected personal Granola folder into
co rem. It is **read-only**: it never creates, edits, or deletes any Granola
resource. Every import writes `source.yaml` and daily JSONL records under
`~/.co/rem/docs/granola/` (or a custom `--output` path).

**Read the output, not just the exit code.** Every failure exits 1 and ends
with a `Next:` line; run that command — it is the fix.

## When execution is unavailable

If the shell refuses a `co granola` command, credentials are unavailable, or
the environment prohibits networked commands, do not try to bypass that limit
with Python, a background process, another shell, or repeated attempts. Give
the user the exact `co granola` command to run in their own shell, state that
it is read-only, explain the expected result, and say that their temporary
`GRANOLA_API_KEY` is required. Never search for, print, or persist a key.

## Required wording for common requests

Use these complete boundaries in the user-facing answer; do not rely on an
implicit command flag to convey them.

- When listing folders, give `co granola folders --json` and say: “Choose one
  personal folder ID from this output before importing.”
- When importing, say: “This is a manual, read-only import against Granola,”
  alongside the exact import command.
- When asked for a shared-space folder, say: “This MVP supports only one
  user-selected personal folder per import; shared-space folders are not
  supported.” Then direct the user to `co granola folders`.
- When asked for automatic sync, say: “This MVP is manual and read-only, with
  no scheduler, webhook, polling loop, or full-folder synchronization.” Do
  not run a command or investigate alternatives.

## Before anything

Set `GRANOLA_API_KEY` as a **temporary shell variable** — never on disk, never
in chat, never via `co env set`. Obtain the key from your Granola app settings.

```bash
# Bash / Git Bash — no-echo, not saved to disk
read -rsp "Granola API key: " GRANOLA_API_KEY; echo
export GRANOLA_API_KEY
co granola verify
unset GRANOLA_API_KEY
```

```zsh
# zsh — no-echo, not saved to disk
read -rs "GRANOLA_API_KEY?Granola API key: "; echo
export GRANOLA_API_KEY
co granola verify
unset GRANOLA_API_KEY
```

For the full import session, keep the variable set until `co granola import`
completes, then unset it. Never ask the user to paste the key into the chat.

## Which command

| You want to | Run |
|---|---|
| Confirm the API key works | `co granola verify` |
| List your personal folders | `co granola folders` |
| Same, as JSON | `co granola folders --json` |
| List note IDs from a personal folder | `co granola notes <folder-id>` |
| Same, as JSON (for scripting or --note-id lookup) | `co granola notes <folder-id> --json` |
| Import all notes from a folder into co rem | `co granola import <folder-id>` |
| Import with a custom profile label | `co granola import <folder-id> --profile work` |
| Import up to N notes | `co granola import <folder-id> --limit 50` |
| Import to a custom directory | `co granola import <folder-id> --output ~/my/dir` |
| Import exactly one note by ID | `co granola import <folder-id> --note-id <note-id>` |
| One-off export (snapshot) | `co granola import <folder-id> --export snapshot-1` |
| One-off export of one note | `co granola import <folder-id> --note-id <note-id> --export snap-1` |
| Any command as JSON | add `--json` |

## The 80 % commands

```bash
co granola verify                                         # confirm key; shows first note title
co granola folders                                        # table: ID  Name
co granola folders --json                                 # {"folders": [{"id": "...", "name": "...", "space_id": null}, ...]}
co granola notes fld-abc123                               # table: note_id  title  created_at
co granola notes fld-abc123 --json                        # {"notes": [{"id", "title", "created_at", "updated_at"}], "complete": true}
co granola import fld-abc123                              # validate → fetch → write source.yaml + daily JSONL
co granola import fld-abc123 --profile work               # label records as account "work"
co granola import fld-abc123 --limit 50 --json            # {"written": "...", "notes": 50, "mapped": 50, ...}
co granola import fld-abc123 --note-id not_abc123         # import exactly one note by ID
co granola import fld-abc123 --note-id not_abc123 --json  # {"notes": 1, "mapped": 1, "complete": true, ...}
co granola import fld-abc123 --export snap-1              # one-off export to exports/snap-1/
```

## Import workflow

### Full folder import (all notes)

Always follow these three steps in order. Do not skip to `import` without
completing `verify` and `folders` first.

**Step 1 — verify**

```bash
co granola verify
```

Exits 0 with the title of the first accessible note. Exits 1 with an
`error_code` and a `Next:` recovery command on any auth or config failure.

**Step 2 — choose a folder**

```bash
co granola folders
```

Lists only personal folders (those not in a shared space). Ask the user to
identify the folder they want to import from. Copy the folder ID exactly from
the first column — do not type it by hand.

**Step 3 — import**

```bash
co granola import <folder-id>
```

The command validates that `<folder-id>` is a personal folder before fetching
any notes. If the ID belongs to a shared space or is not accessible, the
command exits 1 with `error_code: personal_folder_required` or
`error_code: folder_not_found` and no files are written. On success it writes:

- `~/.co/rem/docs/granola/source.yaml` — scope, profile, and completion status
- `~/.co/rem/docs/granola/daily/YYYY/MM/DD.jsonl` — one record per note

### Precise single-note import (`--note-id`)

Use this workflow when the user wants to import one specific meeting note.

**Step 2b — list note IDs**

```bash
co granola notes <folder-id> --json
```

Lists safe metadata (id, title, created_at, updated_at) for every active
(non-deleted) note in the folder. Deleted notes are filtered out automatically.
Copy the full note ID from the `id` field — it starts with `not_`.

**Step 3b — import one note**

```bash
co granola import <folder-id> --note-id <note-id>
```

Performs a scoped lookup of the folder's active notes, verifies the note ID is
present and not deleted, then fetches and imports that one note. `source.yaml`
records `note_id` and `limit: 1` for traceability.

Restrictions:
- `--note-id` and `--limit` are mutually exclusive — exit 1 with
  `error_code: invalid_configuration` if both are given.
- `--note-id` and `--export` are allowed together (one-note snapshot).
- If the note ID is absent from the active list (deleted, moved, or not yet
  processed), exits 1 with `error_code: note_not_in_selected_folder`; no
  files are written.

Imported fields: title, meeting time, Granola URL, folder/space metadata,
plain-text and Markdown summaries. Excluded: private notes, transcripts,
audio, attachments, attendee names, and attendee email addresses.

## Partial and incomplete reads

When the Granola API returns a transient error mid-fetch, `co granola import`
retains all notes collected before the failure, maps them to records, writes
`source.yaml` with `complete: false`, writes the valid records to the daily
file, and exits 1 with `error_code: partial_read`.

| Field in JSON output | Meaning |
|---|---|
| `"complete": true` | All notes were fetched and mapped |
| `"complete": false` | The read was interrupted; valid records retained |
| `"rejected": {...}` | Notes that could not be mapped; valid notes still written |

On a partial read, report `complete: false`, the note counts, and the recovery
command to the user. Retained records are safe to use; re-running the import
when the API recovers will append any missing notes.

Use `--export <id>` for point-in-time snapshots. Each export ID must be unique;
re-using an existing ID exits 1 before any network call. Partial exports also
record `complete: false` in their `manifest.json` scope.

## Error handling

| `error_code` | Cause | Recovery |
|---|---|---|
| `invalid_configuration` | `GRANOLA_API_KEY` is missing or empty, or `--note-id` and `--limit` given together | Set the key using the no-echo procedure above; remove `--limit` when using `--note-id` |
| `reauth_required` | Key rejected (401) | Re-obtain the key from Granola settings and retry |
| `permission_denied` | Key cannot read the selected folder (403) | Verify the key has access to this folder |
| `folder_not_found` | Folder ID is not accessible | Run `co granola folders` and choose an ID from the list |
| `personal_folder_required` | Folder belongs to a shared space | Run `co granola folders`; only personal folders are supported |
| `note_not_in_selected_folder` | `--note-id` value not found in the active notes for this folder | Run `co granola notes <folder-id> --json` to get valid note IDs |
| `transient_error` | 429 or 5xx after retries exhausted | Wait a moment and retry the command |
| `storage_error` | Cannot read or write local files | Check `--output` path and directory permissions |
| `partial_read` | Transient error mid-fetch; valid records retained | Retry later; partial records are already saved |
| `partial_import` | Some notes could not be mapped; valid records retained | Review rejected notes and retry |
| `processing_failed` | All fetched notes failed to map; no records written | Review rejected notes in the JSON output and retry |
| `import_failed` | Unexpected error (not covered by the codes above) | Check the selected folder and `co granola import --help`, then retry |

Error messages never contain the API key. Do not log, display, or relay the
value of `GRANOLA_API_KEY`.

## Scope and safety

- **Personal folders only.** `co granola import` rejects any folder that is
  not a personal folder before making any notes request or writing any file.
  Run `co granola folders` to see which folders are eligible. Personal folders
  are identified by `space_id: null` in the API response; this is a provisional
  marker that has not yet been confirmed with official Granola documentation.
- **One selected personal folder in this MVP.** This MVP supports only one
  user-selected personal folder per import; shared-space folders are rejected.
- **Read-only.** `co granola` cannot create, edit, or delete any Granola note,
  folder, or event. Say so if the user asks for a write operation.
- **No private content.** Private notes, audio, and attachments are never
  imported, persisted, or written to records; transcripts are not requested.
  Attendee names and emails are excluded from every record.
- **Key never persisted.** Do not pass `GRANOLA_API_KEY` to `co env set`; it
  must remain a temporary shell variable for the duration of the session only.
- **`--profile` is a non-secret label**, not the API key — safe to log and
  store. Use it to distinguish accounts when importing from multiple keys.
