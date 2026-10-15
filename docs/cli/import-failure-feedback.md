# Jira and OneNote source import failure feedback

Applies to `co jira` and `co onenote source`. Integrations read remote content
and save local imports; these changes do not register a source with rem or connect
a frontend. Normal success shapes are unchanged.

## Output and exit codes

In JSON mode, handled failures print one JSON object on stdout. Human-readable
failures use stderr. Provider response bodies and tokens are not included.
Parser errors do not echo raw invalid argument values; business validation still
identifies the selected scope. JSON uses Unicode escapes so Windows legacy-codepage
pipes can recover the original values.

Every handled failure includes string `error`, numeric `exit_code`, string
`source`, object `scope`, string `error_code`, string `recovery_action`.
Selected import scopes retain their project/issue or notebook/section/page IDs.
Parser failures have a command-level scope because resource arguments have not
been validated; their recovery action names the recognized command's `--help`.
Account-level failures have an empty scope.

| Outcome | Jira | OneNote source |
| --- | --- | --- |
| Success / valid empty | Exit 0, ordinary result | Exit 0, ordinary result |
| Invalid command arguments | Exit 2, `invalid_input` | Exit 2, `invalid_input` |
| Authentication cannot recover | Exit 1, `reauth_required` | Exit 1, typed credential code, normally `reauth_required` |
| Fatal permission failure | Exit 1, `permission_denied` | Exit 1, `permission_denied` / `missing_scope` |
| Fatal network/rate/pagination/malformed metadata | Exit 1, `import_failed`, sanitized cause and retry guidance | Exit 1, provider code or `import_failed`, sanitized cause and OneNote recovery command |
| Local I/O failure | Exit 1, `storage_error` | Exit 1, `storage_error` |
| Some items rejected, others valid | Exit 1, `partial_import`, saved counts/paths plus `rejected` | Exit 2, `partial_import`, saved counts/paths plus `errors` |
| All selected items rejected | Exit 1, `processing_failed`, zero mapped/appended, null saved paths, no new files | Exit 2, `processing_failed`, zero records/written/skipped, null saved paths, no new files |

Fatal identity/listing protocol corruption is not a valid empty result. Jira
rejects malformed account fields, non-list issue arrays, non-object/keyless issue
rows and non-string cursors before writing. Valid issue objects with malformed
content fields are instead isolated by the mapper so other valid items survive.
Repeated Jira pagination cursors are never echoed to error output.

## Recovery and retained results

Jira token errors direct the user to create/set a temporary API token. OneNote
credential errors direct the user to `co auth microsoft`; an unavailable OneNote
listing directs the user to OneNote verification, not Outlook inbox.

For daily partial imports, keep the same `--output` and retry the selection.
For exports use a new `--export` ID (Jira) or `--export-id` with `--mode export`
(OneNote); existing exports are not overwritten. Counters distinguish collected,
valid, newly written and skipped records, and failed IDs are explicitly reported.

An unrecoverable OneNote authentication interruption after valid pages were
processed saves those pages, stops later reads and still exits 1. Re-login and
retry as above. An all-rejected batch creates no new files and does not claim a
saved path. Valid empty results remain normal success, not `processing_failed`.

Compatibility changes: failed human Jira imports now report retained counts on
stderr instead of printing success-looking stdout. Partial JSON keeps the old
counters and item diagnostics but adds the error envelope. Jira all-rejected
batches no longer create an empty JSONL/source declaration. OneNote page-level
failures keep exit 2; scripts should use `error_code` to distinguish them from
parser errors, rather than guessing from the exit code alone.

Synthetic volume checks are not production latency or memory guarantees.
