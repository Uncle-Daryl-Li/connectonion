# Read-only OneNote source connection

## Baseline and authentication

- Implementation branch: `F13FCAKE-15`, depending on `F13FCAKE-13`.
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

## Verify this Story

```console
python -m pytest tests/unit/test_onenote_source_auth.py tests/unit/test_onenote.py tests/unit/test_provider_credentials.py -q
```

This branch adds notebook/section discovery, bounded page selection and scope configuration on top of F13FCAKE-13 verification. Page content import and configuration persistence are added by F13FCAKE-14.

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


## Scope declaration

`onenote_scope.source_config()` builds the account-bound `source.yaml` declaration with driver, scope, retention, metadata-only policy and validation provenance. It does not write records or register a runtime source. Persistence and explicit daily/export import arrive in F13FCAKE-14.

```console
python -m pytest tests/unit/test_onenote_source_auth.py tests/unit/test_onenote_source_scope.py -q
```
