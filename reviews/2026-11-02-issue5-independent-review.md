# Issue 5 independent review

Reviewed commit `5426948` against base `e1c4793` on 2026-11-02. Read the GitHub issue, implementation diff, regression tests and author validation note. Source was not modified.

## Resolved finding: P2 — new hard-link tests break Python 3.9

`scripts/test_kicad_verify.py:56` and `:77` call `Path.hardlink_to`, which is absent on the installed Python 3.9.6. This blocks the targeted regression suite on that interpreter before its hard-link assertions execute.

Reproduction:

```sh
/usr/bin/python3 --version
/usr/bin/python3 -m unittest discover -s scripts -p test_kicad_verify.py
```

Observed: `Python 3.9.6`; 32 tests run with 11 errors, all `AttributeError: 'PosixPath' object has no attribute 'hardlink_to'` at the two new call sites. Ten are the ERC/DRC context matrix's hard-link subcases; the eleventh is the symlinked-input context test.

Fix: use `os.link(target, report)` at both sites, adding the import if needed, and rerun the suite on Python 3.9 as well as the default interpreter. The production guard itself remained functional on Python 3.9 in an independent existing-hard-link refusal check.

## SURVIVED

- Default-interpreter targeted suite independently ran: 32 tests passed.
- Both public entrypoints perform separation checking before CLI discovery or parity/export work. The shared guard compares resolved paths and existing device/inode identities; supplied and resolved input stems are both protected.
- Independent disposable real KiCad CLI 10.0.5 checks rejected direct, hard-link, symlink, parent-directory symlink, normalized `sub/../` and case-variant aliases of the input board. Original board bytes remained identical after every call.
- Independent real safe-report controls before and after those refusals returned `(5, {'drc violations': 2, 'unconnected pads': 2, 'footprint errors': 0})`; original board bytes remained identical. Calls used `parity=False`, `refill=False`, and default `save_board=False`.
- Failure paths for permission errors and symlink loops are covered by the independently executed targeted suite and raise `REPORT_PATH_UNVERIFIED`, rather than claiming safe separation.
- Scope and concurrency limits are accurately stated in the fix note: same-stem context only; no filesystem exclusion after preflight. No cache or stored verified state is introduced.

## Disposition and limits

The Python 3.9 compatibility blocker is resolved in the reviewed integration working tree as detailed below. No additional production correctness blocker found within the stated same-stem, nonconcurrent scope. The real CLI checks exercised DRC; ERC protection was assessed by source and mocked preflight tests, not a real ERC invocation. No independent full-suite run or runtime benchmark was performed; author results for those are not treated as independently confirmed. Arbitrary hierarchical sheets/dependencies and concurrent path replacement remain outside the implemented protection.

## Correction verification

Independently reviewed the correction in `/Users/fab/dev/ee/kicad-design-file-safety/scripts/test_kicad_verify.py` on 2026-11-02: added `import os` and replaced both new `Path.hardlink_to` calls with `os.link(target, report)`. The diff has no other changes to that test file. Independently reran `/usr/bin/python3 -m unittest discover -s scripts -p test_kicad_verify.py` in that integration worktree using Python 3.9.6: all 32 tests passed in 0.089 seconds. The original P2 finding is resolved by this correction; no remaining blocker from this review. This follow-up verifies the integration working-tree correction, not a claim that original commit `5426948` already contains it.
