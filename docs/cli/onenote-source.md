# Read-only OneNote source connection

## Baseline and authentication

- Implementation branch: `F13FCAKE-13`.
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

Discovery and selection are added by F13FCAKE-15; page import is added by F13FCAKE-14. This branch exposes `co onenote source verify` only.
