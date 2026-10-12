---
name: co-onenote
description: Read the user's OneNote notebooks through `co onenote`, or import explicitly selected pages with `co onenote source`. Use when the user mentions OneNote, "my notes", "notebook", or a note title. This workflow performs reads only; do not create, edit, or delete OneNote pages.
---

# co onenote

`co onenote` acts in the user's Microsoft OneNote as the user, via the
Microsoft Graph API. This workflow is **read-only**:
it lists notebooks and sections, browses pages, and reads page content. Do
not create, edit, or delete OneNote pages.

**Read the output, not just the exit code.** Source commands use exit 1 for
fatal failures and exit 2 for partial page failures with valid pages retained.
Follow the recovery guidance and report partial outcomes explicitly.

## Before anything

For source import, run `co onenote source verify --json` first. Source commands
accept `Notes.Read` and compatible broader existing grants. The older browsing
commands below currently use the general OneNote tool, which requires
`Notes.ReadWrite`; a Notes.Read-only account should use the source commands.
If authentication is missing, tell the user to run `co auth microsoft`. Never
ask them to paste a token into the chat. For older browsing, start with
`co onenote ls` only when its required permission is already available.

## Row numbers

`co onenote ls` assigns row numbers to sections. `co onenote pages [section]`
assigns row numbers to pages. Numbers are valid for 15 minutes; after that,
re-run `ls` or `pages` to refresh them. A number from a different session will
not resolve — pass the literal section or page ID instead, or re-list.

## Which command

| You want to | Run |
|---|---|
| List notebooks and numbered sections | `co onenote ls` |
| List recent pages (all notebooks) | `co onenote pages` |
| List pages in one section by number | `co onenote pages 2` |
| Read a page by row number | `co onenote read 1` |
| Read a page by exact title | `co onenote read "Meeting notes"` |

## The 80 % commands

```bash
co onenote ls                             # notebooks → numbered sections
co onenote pages                          # recent pages across all notebooks
co onenote pages 2                        # pages in section 2
co onenote read 1                         # read page 1 from last pages listing
co onenote read "Sprint retro"            # read by exact title
```

## Import explicitly selected notes as a source

For source collection, use the separate read-only commands:

```bash
co onenote source verify --json
co onenote source notebooks --json
co onenote source sections NOTEBOOK_ID --json
co onenote source pages NOTEBOOK_ID SECTION_ID --limit 100 --json
co onenote source sync NOTEBOOK_ID SECTION_ID PAGE_ID --mode daily --json
co onenote source sync NOTEBOOK_ID SECTION_ID PAGE_ID --mode export --export-id snapshot-1 --json
```

Use stable IDs from discovery, not the row numbers from `ls` or `pages`.
Ask the user to choose the scope before importing; never import all notebooks
by default. A truncated listing has more results: increase the bound before
claiming completeness. Empty scope is valid. Exit 2 means some pages failed;
report the errors and the retained/written/skipped counts separately.
Media stays metadata-only. Writing `source.yaml` does not register a rem
runtime source: this preview supports external imports only. On an auth failure,
use `co auth microsoft`; a missing broker Notes.Read grant depends on #1886.
Never promise least-privilege consent merely because an existing broader grant
can read. Use `--output PATH` for isolated checks and keep real notes private.

## Read-only judgment

- **This integration is read-only.** Do not create, edit, or delete any
  OneNote page. If the user asks for a write operation, explain that the
  current `co rem` OneNote integration only performs reads. `Notes.Read` is
  sufficient; an existing broader grant does not authorize this workflow to
  write. `co onenote create` may exist elsewhere in ConnectOnion,
  but it is outside this integration's scope and must not be used for co rem
  source collection.
- **An unknown section number exits 1** and prints the listing token. Re-run
  `co onenote ls` to refresh section numbers.
- **Pagination**: `pages` defaults to 20 results. Pass a specific section
  number to narrow the scope when needed.
