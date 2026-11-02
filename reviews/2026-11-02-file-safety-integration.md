# Issues 5–7: integrated fixes and independent review

Three implementation agents and three separate reviewer agents handled the issues. All three reviews now have no unresolved blockers within their documented scopes. Fixes are based on `e1c4793`; final implementation candidate is `146e674` on `fix/review-file-safety`.

| Issue | Change | Independent review |
|---|---|---|
| #5 | Reject ERC/DRC report aliases of primary and same-stem design inputs, including normalized paths, symlinks and hard links | [Review](2026-11-02-issue5-independent-review.md); reviewer found Python 3.9-incompatible test API, corrected in `5d2cf54` and independently verified |
| #6 | Recheck destinations after staging temporary output, preserve conflicting bytes during promotion/recovery/report writing, and report incomplete promotion accurately | [Review](2026-11-02-issue6-independent-review.md); reviewer found failure-report symlink overwrite, corrected in `146e674` and independently verified |
| #7 | Persistent POSIX kernel lock with atomic marker publication; unknown ownership fails closed and release does not unlink | [Review](2026-11-02-issue7-independent-review.md); independent process contention and crash/reacquisition checks passed |

## Final integration validation

- KiCad Python 3.9.13: `python3 -m unittest discover -s scripts -p 'test_*.py'` — **536 tests passed, no skips**, 19.310 seconds. Used the interpreter inside the installed KiCad application, with numpy, shapely and pcbnew available.
- Host Python 3.14: `python3 -m unittest discover -s scripts -p 'test_*footprint_swap*.py'` — **30 tests passed**, 1.790 seconds.
- Independent issue #6 reviewer also ran the combined 30-test footprint suite successfully on candidate `146e674`.
- `git diff --check` passed. The only cherry-pick conflict was a shared import block; resolution retained both `stat` and `tempfile`. No behavior was discarded.
- Detailed known-bad calibrations, cost measurements and all eight guard checklist answers are in each issue's fix note.

## Remaining boundaries

Report alias checking covers primary/same-stem design context, not arbitrary hierarchical dependencies or concurrent path replacement. Promotion/recovery use optimistic checks, not atomic compare-and-swap; external editors and generators must remain quiescent. Unknown conflicts can leave earlier files promoted with a retained recovery journal. Locking is qualified for cooperating current-version runners on local POSIX filesystems; migration from legacy PID locks requires stopping old runners first. Windows and network filesystem locking are not qualified.

These boundaries are documented rather than claimed as closed by the tests. No GitHub push, issue closure or remote merge was performed as part of this local integration.
