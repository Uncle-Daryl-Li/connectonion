---
name: co-onenote
description: Read the user's OneNote notebooks through `co onenote` — list notebooks and sections, browse recent pages, and read a page. Use when the user mentions OneNote, "my notes", "notebook", or a note title. This integration is read-only (Notes.Read scope); do not create, edit, or delete OneNote pages.
---

# co onenote

`co onenote` acts in the user's Microsoft OneNote as the user, via the
Microsoft Graph API. This integration is **read-only** (`Notes.Read` scope):
it lists notebooks and sections, browses pages, and reads page content. Do
not create, edit, or delete OneNote pages.

**Read the output, not just the exit code.** Every failure exits 1 and ends
with one `Next:` line; run that command, it is the fix.

## Before anything

Run `co onenote ls`. If it says the Microsoft account is not connected, tell
the user to run `co auth microsoft`. Never ask them to paste a token into the
chat.

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

## Judgment

- **This integration is read-only.** Do not create, edit, or delete any
  OneNote page. If the user asks for a write operation, explain that the
  current `co rem` OneNote integration uses `Notes.Read` scope and cannot
  write to OneNote.
- **An unknown section number exits 1** and prints the listing token. Re-run
  `co onenote ls` to refresh section numbers.
- **Pagination**: `pages` defaults to 20 results. Pass a specific section
  number to narrow the scope when needed.
