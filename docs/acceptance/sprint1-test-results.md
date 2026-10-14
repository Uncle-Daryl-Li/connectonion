# Sprint 1 import failure feedback: consolidated test results

## Scope and overall result

Baseline: `0a0c1269`; fixes and tests committed as `356eb88b` on `F13FCAKE-17`.
These are recorded completed
runs; consolidating the documents does not rerun tests. Automated checks use
synthetic HTTP, isolated credentials/homes and temporary storage. Manual live
checks were performed by the tester through CLI commands and saved-result
inspection. No test account,
email, site address, resource ID, notebook name or raw private content is included.

**Final related regression: 424 passed, exit 0, no warnings or skipped tests.**
This consists of 405 import/provider cases and 19 CLI-help cases: 114 added test
instances and 310 existing cases. It is not a full-repository test pass.

Representative manual checks are complete for both sources: connection,
selected import, repeated import, recoverable failure, preservation of saved
results and corrected retry. The live results below record those checks.
Other live failure scenarios remain subject to the verification boundaries.

## Automated coverage and outcomes

| Category | Checks and observed outcome | Result |
| --- | --- | --- |
| Discovery / import | Selected content maps to the shared record contract and is saved with provenance | Pass |
| Repetition / exports | Daily retries do not duplicate IDs; existing exports are not overwritten; retry guidance names a new export ID where required | Pass |
| Authentication | Initial failure identifies source/scope, returns a stable code/recovery and nonzero exit without secrets | Pass |
| Interrupted authentication | Previously processed OneNote pages are saved; later requests stop; failed/pending pages are identified; restored access can retry safely | Pass |
| Permission | Fatal and page-level failures give safe, actionable permission/read-access guidance | Pass |
| Pagination | Multiple pages collected; loops, invalid cursors and foreign continuation links fail explicitly; opaque Jira cursors are not echoed | Pass |
| Rate limiting | Bounded retries/recoverable failures; reasonable Retry-After honoured; excessive content waits rejected; feedback tells users to wait | Pass |
| Network | Safe recoverable errors without private transport details; human page feedback names the network action | Pass |
| Invalid input / scope | Missing arguments, invalid values, unknown commands/options and wrong scopes return defined errors before unintended requests/writes | Pass |
| Valid empty | Empty discovery and valid empty content remain distinguishable from failed processing | Pass |
| Malformed metadata | Invalid identity/issue/cursor shapes produce safe errors instead of uncaught exceptions; malformed protocol envelopes reject the batch | Pass |
| Mixed / all-item failures | Valid items survive malformed content; all-rejected batches have zero valid counts, null saved paths and no new files | Pass |
| Storage / preservation | Corrupt existing storage is not replaced; representative failed operations retain saved bytes and file inventory | Pass |
| Privacy / output | Synthetic secrets absent from checked output/logs/config; stable error fields and output-channel separation verified | Pass |
| Unicode | Chinese/emoji JSON values survive legacy-codepage decoding; saved content remains UTF-8 | Pass |
| Volume | Both sources persist 100/1000 unique items correctly; repeated imports write zero new items; 1001 selected OneNote pages rejected before remote access | Pass |
| Related features / UX | Shared consumers, namespaced IDs and cross-source isolation work; retained counts and recovery actions are visible | Pass |
| CLI compatibility | Existing CLI-help cases remain valid after import-parser changes | Pass |

### Added tests

| File | Added instances / purpose |
| --- | --- |
| `tests/e2e/cli/test_import_auth_failure_feedback.py` | 22: cross-source first-request authentication plus OneNote interrupted import and restored-access retry |
| `tests/unit/test_onenote_source.py` | 1: unrefreshable content authentication failure; an existing positive refresh test was also strengthened |
| `tests/unit/test_onenote_source_commands.py` | 4: multilingual success/error JSON through legacy Windows decoding |
| `tests/e2e/cli/test_import_failure_contract.py` | 87: 28 fatal-category checks, 20 parser checks, 8 mixed/all malformed checks, 14 Jira metadata-shape checks, 12 OneNote page-failure checks, 4 volume checks and 1 selection-bound check |

The existing Jira human-rejection test retains its count/item assertions and now
checks stderr with empty stdout, matching the updated failure-output contract.

### Recorded test stages

| Stage | Recorded outcome |
| --- | --- |
| Initial Jira ADF baseline | 11 passed; cache-directory permission warning did not fail tests |
| Initial Jira baseline | 175 passed |
| Initial OneNote source baseline | 53 passed |
| First cross-source authentication acceptance | 6 failed, 4 passed; missing structured/human context reproduced |
| Authentication feedback regression | 301 passed |
| Interrupted-import/recovery reproduction | 12 failed; valid new records and recovery gaps reproduced |
| Extended authentication/import regression | 314 passed |
| Unicode-output reproduction | 4 failed; later related regression 318 passed |
| Initial final-round contract checks | 16 failed, 25 passed; mixed/all malformed extension also reproduced 8 failures |
| Metadata-shape / zero-valid review extension | 18 failed, 4 passed; subsequent 67-case stage passed |
| Specific page-recovery review extension | 10 failed, 2 passed; safe diagnostic/action fixes applied |
| Final combined related regression | 424 passed, exit 0, no warnings/skips |

One intermediate stability test summed retry waits across two commands against
one command's limit. It was corrected to assert each command's bound separately.
Temporary-directory/cache setup failures were environment issues, not product
defects. No product assertions were disabled, skipped or marked xfail.

## Manual live Jira results

| Check | Observed outcome | Result |
| --- | --- | --- |
| Verify access | Read access verified, exit 0 | Pass |
| List accessible projects | Projects returned, exit 0 | Pass |
| Read the selected issue | Requested content returned, exit 0 | Pass |
| First daily import | One issue mapped and appended, history available, no rejected items, exit 0 | Pass |
| Repeat import | Zero newly appended records, exit 0 | Pass |
| Temporary invalid-token failure | Source/scope, reauthentication code and recovery provided, exit 1 | Pass |
| Compare saved files after failure | File paths and hashes unchanged | Pass |
| Restore valid access and retry | Success, zero duplicate writes, exit 0 | Pass |

Demonstrated: success → repeat → authentication failure → saved results unchanged
→ restored-token retry. The test restored an existing valid token; it did not
demonstrate expired-token reissuance. No remote issue was created or edited.

## Manual live OneNote results

| Check | Observed outcome | Result |
| --- | --- | --- |
| Supported authorization and verification | Authorization completed; read-only verification, exit 0 | Pass |
| Initially empty notebook discovery | Valid empty JSON with exit 0, no parser error | Pass |
| Notebook discovery after test data preparation | Notebook found, JSON parsed, exit 0 | Pass |
| Multilingual section discovery | Original JSON pipe failed; after fix English/Chinese names parsed correctly, exit 0 | Pass after fix |
| Selected page listing | Page listed, JSON parsed, exit 0; an untitled page was not treated as failure | Pass |
| First daily import | One record written, no errors, exit 0 | Pass |
| Repeat import | Zero writes and one skip, exit 0 | Pass |
| Inspect saved content | Exactly one record with complete title/body; table display abbreviation was not data loss | Pass |
| Invalid section and corrected retry | Failure exit 1 with source/scope and recovery; saved file paths/hashes unchanged; corrected retry exit 0 without duplicate writes | Pass |

Demonstrated: success → repeat → invalid selected scope → saved results unchanged
→ corrected-scope retry. This was scope recovery, not a real 401/403. Read access
does not establish minimum OAuth consent. Imports remain external-import-only;
same-day changed pages are skipped rather than incrementally refreshed.

## Defect and retest register

All 14 groups below are **fixed locally and retested**, with team review pending.
Owners are proposed component roles, not assigned people; the team must fill in
actual names before the demo. Severity is provisional: High concerns discarded
valid new results; Medium concerns failure/output/recovery requirements; Low is
misdirected recovery guidance. Synthetic privacy failures do not establish that
real credentials were disclosed.

| ID | Problem and fix | Severity | Proposed owner (assignment pending) | Status | Retest |
| --- | --- | --- | --- | --- | --- |
| AUTH-D01 | Missing JSON source/scope → selected context added | Medium | Jira / OneNote CLI | Fixed locally; review pending | Pass; Jira live feedback also checked |
| AUTH-D02 | Missing stable code/recovery fields → structured fields added | Medium | Jira / OneNote CLI | Fixed locally; review pending | Pass; Jira live feedback also checked |
| AUTH-D03 | Human errors omitted source/selection → source/scope on stderr | Medium | Jira / OneNote CLI | Fixed locally; review pending | Pass |
| AUTH-D04 | OneNote mid-batch auth failure discarded processed pages → valid pages saved before fatal report | High | OneNote source | Fixed locally; review pending | Pass for metadata/content and daily/export |
| AUTH-D05 | Content 401 without refresh credentials misclassified → explicit reauthentication, no invalid broker call | Medium | OneNote source | Fixed locally; review pending | Pass |
| AUTH-D06 | Retained export retry lacked a new-ID instruction → explicit daily/export recovery hints | Medium | OneNote source / CLI | Fixed locally; review pending | Pass |
| OUTPUT-D01 | Unicode output broke Windows JSON pipes → ASCII JSON escapes preserve parsed values | Medium | OneNote CLI | Fixed locally; review pending | Automated and live retest pass |
| FEEDBACK-D01 | Jira pagination error exposed opaque cursor → cursor omitted from feedback | Medium | Jira source | Fixed locally; review pending | Safe JSON/human and file-preservation checks pass |
| FEEDBACK-D02 | Malformed Jira response shapes escaped safe handlers → metadata validation | Medium | Jira source | Fixed locally; review pending | Top-level and 14 shape cases pass |
| FEEDBACK-D03 | Jira I/O errors lacked safe structured feedback → storage error and recovery | Medium | Jira CLI | Fixed locally; review pending | JSON/human failure and preservation checks pass |
| FEEDBACK-D04 | OneNote unavailability suggested Outlook → OneNote recovery command | Low | OneNote source | Fixed locally; review pending | Rate failure and stable repeated recovery pass |
| FEEDBACK-D05 | Parser errors lacked JSON and echoed invalid values → safe parser error object/help | Medium | Import CLI | Fixed locally; review pending | All 20 parser variants pass |
| FEEDBACK-D06 | Partial/all failure output misrepresented results → structured reports, stderr, zero-valid/no-files distinction | Medium | Jira / OneNote CLI | Fixed locally; review pending | Eight malformed mixed/all variants pass |
| FEEDBACK-D07 | OneNote human page failures lacked reasons/actions → per-page diagnostics and permission/wait/network guidance | Medium | OneNote source / CLI | Fixed locally; review pending | All 12 page-failure variants pass |

## Known limitation from the extra CLI check

The broader CLI-discovery selection returned **500 passed, 1 failed, 1 warning**.
The failed test expected ANSI terminal colors; this Windows environment did not
emit them. A read-only in-memory comparison with the original CLI entry and
OneNote registration produced identical original/current output; the comparison
passed. The warning concerns an existing deprecated class-scoped fixture style.
The color test was not changed or suppressed, and is not counted as passed.

## Verification boundaries and rerun

- Real permission denial, throttling, outages, deliberately forced pagination
  and newly processed live mixed-batch failure were not induced; automated
  coverage must not be described as live API verification.
- Volume correctness is checked with mocked transport, not production latency,
  memory or internet throughput. No timing guarantee is claimed.
- No frontend import, automatic rem registration or complete security audit was
  performed.
- Team matrix approval, individual owner assignment, MR/merge and
  formal demo remain pending; use the [review summary](sprint1-review-summary.md).

To rerun the recorded related selection from the project directory:

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest tests/e2e/cli/test_import_failure_contract.py tests/e2e/cli/test_import_auth_failure_feedback.py tests/unit/test_onenote_source_commands.py tests/unit/test_jira.py tests/unit/test_jira_adf.py tests/unit/test_onenote_source_auth.py tests/unit/test_onenote_source_scope.py tests/unit/test_onenote_source.py tests/unit/test_onenote.py tests/unit/test_provider_credentials.py tests/e2e/cli/test_cli_help.py -q -p no:cacheprovider
$LASTEXITCODE
```
