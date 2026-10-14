# Sprint 1 import failure feedback: team review summary

## Story and outcome

Users should understand why a Jira or OneNote import failed, know how to recover,
and keep valid results without exposing sensitive information.

Local implementation and related automated testing are complete: **424 tests passed**.
Representative manual live checks passed for both sources: connection, selected
import, repeat import, saved-result preservation and failure/recovery.
This is ready for team review, not a merged release or final Story sign-off.
The [test results](sprint1-test-results.md) contain the coverage, live outcomes,
defect/retest register and known limitations. Test account details are omitted.

## Work completed

- Ran the developers' existing tests and added 114 Story-focused test instances,
  including 87 final-round checks. Existing tests are regression evidence, not
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
- Checked both sources with 100 and 1000 synthetic items, plus duplicate imports
  and the OneNote selection bound.
- Consolidated 14 locally fixed and retested defect groups into the results
  register, including severity and proposed component ownership.

## Strategy and acceptance matrix for confirmation

Use isolated mocked tests for controlled failures and dedicated live scopes for
representative import/recovery flows. Check output, exit codes and saved records,
not only whether a command finishes. Never manufacture live throttling or expose
credentials to demonstrate an error.

| Area | What was checked | Evidence |
| --- | --- | --- |
| Functional / related features | Discovery, selected import, repeat import, exports, shared record consumers and cross-source isolation | Automated; representative live discovery/import/repetition |
| Negative / permission | Invalid arguments/scope, authentication, permission, malformed metadata/content and storage failure | Automated; live Jira authentication and OneNote invalid-scope recovery |
| Pagination / rate / network | Multiple pages, loop/foreign-link guards, bounded retry and actionable failure feedback | Automated |
| Valid results / recovery | Valid empty success, partial results retained, all-failed outcomes, retry without losing saved data | Automated; saved-file comparisons and corrected live retries |
| Privacy / machine output | Safe messages/configuration, source/scope, stable JSON fields, stdout/stderr separation and Unicode compatibility | Automated; live JSON parsing and redacted records |
| Volume | 100/1000 items per source, accurate unique counts, repeat writes zero and oversized OneNote selection rejected | Synthetic volume checks, not a production performance SLA |
| UX / demo | Clear failure cause and next action; successful import and recoverable failure per source | Automated and live CLI rehearsal; formal demo pending |

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

Story key / branch: `F13FCAKE-17` (remote push pending).
Team reviewer(s): pending. Review outcome: pending.
Fix commit: `356eb88b`. MR / merge: pending. Formal demo outcome: pending.

Representative manual testing is complete; team approval remains pending.
Additional manual scenarios can be agreed during team review.
Real 403/429/outage tests, production latency/memory
benchmarks, frontend import and automatic rem registration were not performed.
An additional CLI color-display check has a reproduced baseline limitation;
see the results rather than interpreting the related 424-test pass as a full
repository acceptance pass.
