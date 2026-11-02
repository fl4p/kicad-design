# Independent review of issue #7 fix

Reviewed commit `841fc36` against `e1c4793`, including GitHub issue #7,
the lock implementation, its callers, seven added tests, README migration
instructions, and `reviews/2026-11-02-issue7-fix.md`.

## Result

No blocking findings for the documented cooperating-runner, local-filesystem
protocol. The initialization race and release-time unlink race are addressed.
This review covers locking, not the separate promotion fixes for issue #6.

## SURVIVED

- `scripts/kicad_footprint_swap.py:267-288`: publication links a completely
  written marker without replacing an existing pathname; ownership requires
  exclusive kernel locking before metadata acceptance. Independent processes
  cannot acquire the same persistent inode simultaneously.
- `scripts/kicad_footprint_swap.py:280-299`: symlinks, nonregular files,
  malformed markers and permission failures do not authorize deletion or
  recovery. Failure after opening closes the descriptor.
- `scripts/kicad_footprint_swap.py:302-305`: release only closes the owned
  descriptor, retaining the inode for waiters and preserving replacement paths.
- `scripts/kicad_footprint_swap.py:639-641,735`: production acquisition precedes
  journal recovery; the transaction releases its descriptor in `finally`.
- `scripts/README.md:244-253`: persistent inode, upgrade/mixed-version hazard,
  local-filesystem boundary and manual recovery requirements are explicit.

## Independent validation

Both commands passed all 20 targeted tests:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest discover -s scripts -p 'test_*footprint_swap*.py'
PYTHONDONTWRITEBYTECODE=1 /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 -m unittest discover -s scripts -p 'test_*footprint_swap*.py'
```

Interpreters were Python 3.14.0 and KiCad Python 3.9.13 on local macOS.
These runs include forced process death followed by reacquisition, pauses before
publication and after kernel acquisition, malformed and legacy metadata,
permission errors, replacement-safe release, symlinks and FIFOs.

An additional reviewer-authored inline harness launched 12 independent Python
processes, each making 60 acquisition attempts against one initially nonexistent
path. After acquisition each created a separate exclusive-create sentinel,
held it for 1 ms, removed it, and released the lock. A sentinel collision would
fail the child. All children exited successfully: 106 critical-section entries
and no overlap. This is a stress sample, not an exhaustive schedule proof.

Additional inline checks passed: 100 release/reacquisition cycles retained one
inode; simulating unavailable `fcntl` refused acquisition; injected permission
failures in `os.open`, `os.link` and `os.read` preserved the existing inode and
allowed normal reacquisition after removing the fault. The missing-module probe
tests the fail-closed branch, not native Windows execution.

Independent timing of 200 acquire/release cycles on the local filesystem gave
median 0.523 ms and p95 0.567 ms. This corroborates the order of magnitude in the
implementer's cost note; the baseline comparison was not repeated here.

## Guard assessment and limitations

Unevaluable metadata refuses acquisition; worsening marker contents cannot
become evidence of a dead owner. Production callers use the required acquire/
finally-release sequence. Ownership is a live kernel lock, with no cached
verification result. Failures preserve ownership files, and tests distinguish
refusal from success. Deterministic known-bad schedules are covered by the added
process fixtures; the reviewer also independently checked concurrent entry.
The change replaces the broken ownership mechanism rather than suppressing a
warning, with measured sub-millisecond local cost.

Advisory locking assumes all participants preserve the inode and use this
protocol. External unlink/replacement, simultaneous legacy runners, network
filesystem semantics, native Windows, and power-loss durability were not
qualified. A crash before publication can leave an unused seed file; a
malformed persistent marker still requires manual recovery. No implementation
files were modified and no commits were made by this reviewer.
