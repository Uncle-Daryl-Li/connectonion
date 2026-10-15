# Sprint 1 import failure feedback: team review summary

## Story and outcome

Users should understand why a Jira or OneNote import failed, know how to recover,
and keep valid results without exposing sensitive information.

Local implementation and related automated testing are complete: **449 tests
passed under both Typer 0.20.0 and 0.27.3** (the same selection, run separately).
Representative manual live checks passed for both sources: connection, selected
import, repeat import, saved-result preservation and failure/recovery.
This is ready for team review, not a merged release or final Story sign-off.
The [test results](sprint1-test-results.md) contain the coverage, live outcomes,
defect/retest register and known limitations. Test account details are omitted.

## Work completed

- Ran the developers' existing tests and added 139 Story-focused test instances,
  including 87 failure-contract and 25 parser-compatibility checks. Existing tests are regression evidence, not
  newly authored tests.
- Added consistent failure fields: source, scope, error code, recovery action and
  exit code. Human errors use stderr; JSON errors remain machine-readable.
- Preserved valid OneNote pages when authentication fails later in an import,
  with clear daily/export retry instructions.
- Distinguished partial success, all-item failure and valid empty results;
  all-rejected batches do not create success-looking import files.
- Fixed unsafe or misleading feedback for pagination, malformed metadata,
  storage failures, invalid arguments and OneNote page-level failures.
- Fixed Chinese/emoji JSON output through legacy Windows pipes.
- Fixed Typer-version compatibility in all three usage-error handlers without
  changing the project's dependency versions; parser/help regressions run under
  both legacy and vendored Click runtimes.
- Checked both sources with 100 and 1000 synthetic items, plus duplicate imports
  and the OneNote selection bound.
- Consolidated 15 locally fixed and retested defect groups into the results
  register, including severity and proposed component ownership.

## Strategy and acceptance matrix for confirmation

Use isolated mocked tests for controlled failures and dedicated live scopes for
representative import/recovery flows. Check output, exit codes and saved records,
not only whether a command finishes. Never manufacture live throttling or expose
credentials to demonstrate an error.

AC numbers follow the order of the Story's acceptance criteria. Verified means
the cited checks passed, not that team approval or the formal demo is complete.

| AC | Story acceptance criterion | Corresponding checks / evidence | Status |
| --- | --- | --- | --- |
| AC1 | Authentication, permission, pagination, rate-limit, network, empty and malformed-content failures have defined outcomes. | Automated checks for all listed categories; defined error/empty/partial outcomes in the [CLI contract](../cli/import-failure-feedback.md) | Verified |
| AC2 | Messages identify the affected source or scope and a recovery action without exposing secrets. | JSON/human source, scope and recovery assertions; synthetic-secret checks; representative manual failure feedback | Verified |
| AC3 | A failed item or source does not erase or misrepresent valid processed results. | Mixed/all-failed batches, interrupted OneNote imports, shared-consumer/cross-source isolation and accurate counts; manual saved-file comparisons and corrected retries | Verified |
| AC4 | Functional, non-functional, performance or volume, related-feature and UX or recovery checks are in the test matrix. | Functional imports; privacy/output non-functional checks; 100/1000-item synthetic volume; shared consumers/isolation; recovery flows, documented in [test results](sprint1-test-results.md#automated-coverage-and-outcomes) | Coverage documented; team matrix review pending |
| AC5 | Each defect records severity, owner, status and retest result before the demo. | [Defect and retest register](sprint1-test-results.md#defect-and-retest-register) lists 15 fixed/retested groups with severity and proposed component owners | Actual owner assignment and team confirmation pending |
| AC6 | The demo contains at least one successful flow and one recoverable failure. | [Manual Jira](sprint1-test-results.md#manual-live-jira-results) and [OneNote](sprint1-test-results.md#manual-live-onenote-results) success/failure/recovery sequences completed | Representative flows verified; formal demo pending |
| AC7 | Invalid input, authentication/permission failure, rate limiting and unrecoverable processing failure return documented non-zero exit codes. | Parser, fatal-category and mixed/all-item processing tests; [documented exit codes](../cli/import-failure-feedback.md#output-and-exit-codes): parser 2, fatal 1, item failures Jira 1 / OneNote 2 | Verified |
| AC8 | Valid empty results remain distinguishable and do not masquerade as failures. | Automated valid-empty listing/content checks; manual empty OneNote discovery returned valid JSON and exit 0 | Verified |
| AC9 | Human-readable errors are separated from machine-readable output; JSON mode returns a stable error object containing source/scope, error code and recovery action without secrets. | stdout/stderr separation, repeated stable JSON fields, scope/code/recovery and synthetic-secret assertions; live JSON parsing, including multilingual output | Verified |
| AC10 | Automated tests cover pagination, authentication/permission failure, empty result, rate limiting, malformed content and exit-code/output behaviour. | [Automated coverage and added tests](sprint1-test-results.md#automated-coverage-and-outcomes); final related regression: 449 passed per Typer runtime | Verified |

## Decisions to confirm with the team

- [x] Manually verify representative Jira and OneNote connection, import,
  repeat-import, failure, saved-result preservation and recovery flows; outcomes
  are recorded in the test results.
- [ ] Review this matrix and agree that the automated/live split is sufficient
  for Sprint 1; identify any additional required checks.
- [ ] Review the user-facing changes, especially failure JSON fields, human
  stderr output, all-failed behaviour and recovery instructions.
- [ ] Replace proposed component-owner roles in the defect register with actual
  responsible team members; confirm severity, status and retest outcomes.
- [x] Commit the fixes and tests on the Story branch; fix commit: `356eb88b`.
- [ ] Review the Story branch/MR and merge the accepted changes.
- [ ] Present a successful import and a recoverable failure in the formal demo.

Story key / branch: `F13FCAKE-17` (pushed to origin).
Team reviewer(s): pending. Review outcome: pending.
Fix commit: `356eb88b`. MR / merge: pending. Formal demo outcome: pending.

Representative manual testing is complete; team approval remains pending.
Additional manual scenarios can be agreed during team review.
Real 403/429/outage tests, production latency/memory
benchmarks, frontend import and automatic rem registration were not performed.
An additional CLI color-display check has a reproduced baseline limitation;
see the results rather than interpreting the related 449-test pass as a full
repository acceptance pass.
