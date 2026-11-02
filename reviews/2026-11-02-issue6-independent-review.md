# Issue 6 independent review

Reviewed commit: `434dda3`, against `e1c4793`. Issue: https://github.com/fl4p/kicad-design/issues/6.

Initial result at `434dda3`: one blocker. **Resolved by independently verified follow-up `b29b540`; no remaining blocker found in this focused review.** The original evidence is retained below.

## Resolved P1: failure reporting overwrote a concurrently created broken symlink

Location: `scripts/kicad_footprint_swap.py:738-742`.

When the report was initially absent, the error handler maps `report_path.is_file() == False` to `current_report = None`, equating a broken symlink with confirmed absence. It then calls `_write_json_durable`, replacing the unexpected symlink. The journal can correctly retain `recovery_required` while this write nevertheless destroys the concurrent filesystem change. This path predates the fix but undermines its preservation guarantee for the report, which is itself a promotion destination.

Deterministic public-entry reproduction, run with `PYTHONPATH=scripts python3`:

```python
from pathlib import Path
import tempfile
from unittest.mock import patch
import kicad_footprint_swap as swap
from test_kicad_footprint_swap import _ADAPTER, _spec

with tempfile.TemporaryDirectory() as raw:
    root = Path(raw).resolve()
    adapter = root / "adapter.py"
    adapter.write_text(_ADAPTER)
    spec = _spec(root, adapter)
    report_path = root / "footprint-swap-report.json"
    copy = swap.shutil.copyfileobj

    def mutate(incoming, outgoing):
        copy(incoming, outgoing)
        if Path(incoming.name).name == "transaction-report.json":
            report_path.symlink_to(root / "external-target")

    with patch.object(swap.shutil, "copyfileobj", side_effect=mutate):
        rc, report = swap.run(spec, True, 10)
    print(rc, report["status"], report["promotion"], report_path.is_symlink())
```

Observed: `2 recovery_required incomplete False`. The symlink became a regular JSON report. The error string nevertheless correctly identified the nonregular interrupted destination.

Required correction: use the same explicit lstat/identity semantics for failure-report publication, preserving nonregular objects and unevaluable destinations; recheck after preparing the report temporary file and immediately before replacement. Skip report publication on conflict, return the failure in memory, and retain recovery state. Cover broken symlinks, probe errors and regular edits during report staging with regressions.

## SURVIVED

- Original issue regression, copy-time mutation cases, rollback overwrite/unlink interleavings, public report status, and successful controls: all 21 footprint-swap tests passed in this worktree (1.412 s).
- Independent boundary injections after staging verified that an ordinary external edit, lstat PermissionError, and stat PermissionError each raise ConcurrentChange; original/external bytes survive and the temporary replacement is removed.
- Source and added test file parse under Python 3.9 grammar. Callable/Optional annotations and Path.unlink(missing_ok=True) are compatible with Python 3.9; a Python 3.9 interpreter was not separately run.
- Combined candidate `ec51ba3` at `/Users/fab/dev/ee/kicad-design-file-safety`: all 28 footprint-swap tests passed (1.645 s). This does not resolve the independently reproduced report-path gap.
- The documentation accurately calls the checks optimistic and retains the external-writer quiescence precondition. This review does not claim atomic exclusion across the final check/replace or check/unlink interval.

## Limits

Focused deterministic filesystem review, not hardware or KiCad GUI validation. No source changes or commits made. Locking was assigned to another reviewer. Runtime figures above are test execution times, not a replacement for the author's measured guard overhead. The follow-up correction was reviewed as recorded below.

## Follow-up verification: b29b540

The author added an optional late validation callback and temporary-file cleanup to `_write_json_durable`. Failure and dry-run report publication use `_verify_destination`; the failure handler returns `skipped_concurrent_destination` for unknown identity and `skipped_write_error` for write failures.

Independently reran the exact public-entry broken-symlink reproduction above. Observed `recovery_required`, promotion `incomplete`, `skipped_concurrent_destination`, and the original symlink target preserved. The recovery journal remains, and the temporary report is removed.

Three further independent public-entry injections first promote one board, then externally change the next board to force incomplete recovery:

- Create an external report file during failure-report temporary-file fsync: external bytes preserved, report publication skipped.
- Create that external report file and raise PermissionError from the late lstat probe: external bytes preserved, no unhandled exception, report publication skipped.
- Raise PermissionError opening the failure-report temporary output: no unhandled exception; returns `skipped_write_error`, retains journal, and does not create a destination report.

All four cases returned code 2 with `recovery_required` and promotion `incomplete`, retained the recovery journal, and left no report temporary file.

The issue6 worktree's 23 focused tests passed (1.722 s). Python 3.9 grammar parsing passed for the implementation and regression tests; runtime validation under Python 3.9 is being performed separately by the integrating agent. Final combined candidate `146e674` passed all 30 footprint-swap tests (1.538 s). Its footprint-swap source differs from `b29b540` only in the independently assigned issue7 locking implementation/import.

No remaining blocker found for issue6 within this scope. The documented final check/replace race remains an explicit optimistic-concurrency limitation.
