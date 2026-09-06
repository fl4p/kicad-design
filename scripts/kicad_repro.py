#!/usr/bin/env python3
"""Prove a generator is reproducible -- and that it actually RAN.

REPRODUCIBILITY IS NOT EQUIVALENCE, AND THIS MODULE ONLY DOES THE FIRST.
`run_and_check_reproducible` runs the SAME command twice, so it answers "is
this generator deterministic". It does not, and cannot, answer "is my change
to the generator safe", which is a different question people reach for a hash
to settle -- run before, change code, run after, compare digests. That
comparison is worth making and is much stronger than comparing violation
counts, but its result is: STRONG EVIDENCE THAT THE ARTEFACT OF THE TESTED RUN
IS UNCHANGED, NEVER PROOF THAT THE MECHANISM PRODUCING IT IS CORRECT.
Measured, not supposed: a router cache A/B'd with a deliberately UNSOUND key
produced a byte-identical board, because the unsound branch was never
exercised in a verdict-changing way on that input. A wrong verdict on a
candidate that is never selected leaves no trace in the output. See PCBNEW.md,
"A slow generator", for the full statement.

Project-agnostic. Nothing here knows about any particular board.

Comparing two hashes cannot distinguish "reproducible" from "never
regenerated". The classic way to get a confident false PASS is to re-run a
`pcbnew` generator under an interpreter that cannot import `pcbnew`: the run
dies, the output file is left untouched, the two digests match, and the check
reports PASS having tested nothing.

So a run counts only if ALL of:

* the generator exited 0,
* the output file's mtime moved,
* the digest is unchanged.

There is a fourth failure this module adds, learned the hard way: **another
process may be writing the same artifact.** Two agent sessions regenerating one
board produced two different digests minutes apart, and a verification run was
attributed to a file that had already been replaced. :func:`stable_digest`
re-hashes after a settle delay and refuses to certify a file that is moving,
and :func:`verify_unchanged_since` lets a caller prove the artifact it is about
to commit is the one it verified.

A THIRD QUESTION, WHICH THE FIRST TWO DO NOT ANSWER: does regenerating from
TODAY's generator still produce the artefact that is COMMITTED?
:func:`check_frozen` answers that one. See its docstring for the contract and
for what was deliberately not copied from the tool the idea came from.

Provenance for :func:`check_frozen`: the *shape* is atopile's `--frozen` build
mode (MIT, release 0.15.8; `src/atopile/build_steps.py:757-812` and
`src/atopile/config.py:595`, `:619-639`). Nothing was copied; see
`plans/atopile-adoption.md`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import time
from pathlib import Path

__all__ = [
    "ReproError",
    "FrozenError",
    "digest",
    "digest_with_identity",
    "stable_digest",
    "run_and_check_reproducible",
    "check_frozen",
    "verify_unchanged_since",
]


class ReproError(AssertionError):
    """A reproducibility claim could not be substantiated."""


class FrozenError(ReproError):
    """Regenerating did not reproduce the tracked artefact.

    A subclass so a caller can tell "the artefact moved" (a real, reviewable
    finding about the design) from "the check could not be run" (a harness
    problem) -- `ReproError` alone conflates them.
    """


def digest(path, algo="sha256"):
    """Digest of `path`, verified to be one consistent file.

    Hashes through ONE descriptor and fstats it before and after, so the
    result cannot combine one inode's bytes with another's metadata, and a
    replacement mid-read is detected rather than averaged. The descriptor's
    identity is then compared with what the pathname resolves to, which is
    what closes the replace-between-open-and-check race.
    """
    return digest_with_identity(path, algo)[0]


def digest_with_identity(path, algo="sha256"):
    """(digest, (st_dev, st_ino, st_size)) for `path`, or raise."""
    h = hashlib.new(algo)
    with open(path, "rb") as f:
        st0 = os.fstat(f.fileno())
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
        st1 = os.fstat(f.fileno())
        if (st0.st_ino, st0.st_size, st0.st_mtime_ns) != \
           (st1.st_ino, st1.st_size, st1.st_mtime_ns):
            raise ReproError(
                "%s changed while it was being hashed -- another process is "
                "writing it" % path)
        ident = (st0.st_dev, st0.st_ino, st0.st_size)
    now = os.stat(path)
    if (now.st_dev, now.st_ino) != (ident[0], ident[1]):
        raise ReproError(
            "%s was REPLACED between opening it and checking it (inode %s -> "
            "%s) -- the bytes just hashed are no longer at that path"
            % (path, ident[1], now.st_ino))
    return h.hexdigest(), ident


def stable_digest(path, settle=2.0, tries=3, algo="sha256"):
    """Digest a file only if it stops changing.

    Guards the concurrent-writer case: if another process is mid-write, the
    hash you take is of a transient state. Raises rather than returning a
    digest that moved.
    """
    p = Path(path)
    last = (digest(p, algo), p.stat().st_mtime_ns, p.stat().st_size)
    for _ in range(tries):
        time.sleep(settle)
        now = (digest(p, algo), p.stat().st_mtime_ns, p.stat().st_size)
        if now == last:
            return now[0]
        last = now
    raise ReproError(
        "%s is still changing after %d settles of %.1fs -- another process is "
        "writing it. Any verification of this file is attributed to a state "
        "that no longer exists." % (p, tries, settle))


def run_and_check_reproducible(cmd, outputs, cwd=None, env=None, algo="sha256"):
    """Run `cmd`, then run it again, and require identical outputs.

    `outputs` is a list of paths the generator writes.

    Returns {path: digest}. Raises ReproError naming which of the three
    conditions failed -- never a bare "not reproducible", because "the
    generator did not run" and "the generator is non-deterministic" call for
    completely different fixes.
    """
    outs = [Path(o) for o in outputs]
    if not outs:
        raise ReproError(
            "no outputs given -- running a command twice and comparing "
            "nothing returns {} and reads as a pass")
    if len(set(outs)) != len(outs):
        raise ReproError("duplicate paths in outputs: %s" % outputs)
    missing = [o for o in outs if not o.exists()]

    def _snapshot():
        return {o: (digest(o, algo), o.stat().st_mtime_ns) for o in outs}

    def _run(label):
        proc = subprocess.run(cmd, cwd=cwd, env=env,
                              capture_output=True, text=True)
        if proc.returncode != 0:
            raise ReproError(
                "%s: generator exited %d -- a non-zero exit leaves the output "
                "untouched, which is exactly what makes two equal hashes "
                "meaningless.\nstderr:\n%s"
                % (label, proc.returncode, proc.stderr[-2000:]))
        return proc

    if missing:
        _run("initial run (outputs absent)")
        still = [o for o in outs if not o.exists()]
        if still:
            raise ReproError("generator exited 0 but did not write: %s"
                             % ", ".join(str(s) for s in still))

    # Separate the runs in time so a coarse-timestamp filesystem cannot
    # make two genuine rewrites share one mtime -- which would turn the
    # strict both-runs check below into a FALSE FAILURE on a correct
    # generator (the opposite error, and just as wrong).
    before = _snapshot()
    time.sleep(1.1)
    _run("run 1")
    time.sleep(1.1)
    mid = _snapshot()
    _run("run 2")
    after = _snapshot()

    for o in outs:
        # BOTH runs must have rewritten it. Requiring movement in only
        # one lets run 2 be a no-op while run 1's mtime carries the check.
        if mid[o][1] == before[o][1]:
            raise ReproError(
                "%s: mtime did not move on run 1 -- the generator did not "
                "rewrite it, so an identical digest proves nothing" % o)
        if after[o][1] == mid[o][1]:
            raise ReproError(
                "%s: mtime did not move on run 2 -- run 2 was a no-op, so "
                "the digests being equal is not evidence of determinism "
                "(coarse-timestamp filesystem? add spacing)" % o)
        if o.stat().st_size == 0:
            raise ReproError(
                "%s: output is zero bytes -- a generator that truncates "
                "deterministically is not 'reproducible'" % o)
        if mid[o][0] != after[o][0]:
            raise ReproError(
                "%s: NOT reproducible -- run 1 %s, run 2 %s. This is "
                "non-determinism in the generator, not a failure to run."
                % (o, mid[o][0][:12], after[o][0][:12]))
    return {str(o): after[o][0] for o in outs}


def _first_difference(a_bytes, b_bytes, context=60):
    """(offset, line_no, a_excerpt, b_excerpt) for the first differing byte.

    Purely for the human reading the failure. The VERDICT is the digest; this
    only says where to look, and it must never be mistaken for the comparison
    itself.
    """
    n = min(len(a_bytes), len(b_bytes))
    off = n
    for i in range(n):
        if a_bytes[i] != b_bytes[i]:
            off = i
            break
    line = a_bytes.count(b"\n", 0, off) + 1
    lo = max(0, off - context)

    def _exc(buf):
        return buf[lo:off + context].decode("utf-8", "backslashreplace")

    return off, line, _exc(a_bytes), _exc(b_bytes)


def check_frozen(cmd, outputs, cwd=None, env=None, algo="sha256",
                 keep_dir=None, settle=2.0, tries=3, spacing=1.1):
    """Regenerate, and require the tracked artefact to come back UNCHANGED.

    This is the "the committed board is still what the generator produces"
    gate. `run_and_check_reproducible` cannot answer it: running the same
    command twice today says nothing about whether today's command agrees with
    what is in git.

    Contract, in order. Every step is a refusal, not a warning:

    1. Each output must already exist, be non-empty and be settled
       (:func:`stable_digest`) -- a check against an artefact another process
       is writing is attributed to a state that no longer exists.
    2. The tracked bytes are copied aside FIRST, and the copy is verified by
       digest. Nothing else runs until the original is recoverable.
    3. The generator runs. It must exit 0 and it must move every output's
       mtime. **A generator that fails to run leaves the file untouched, which
       is byte-identical to a perfect pass** -- that is this module's founding
       lesson and it applies with more force here than to the two-run check,
       because here there is only one run to prove.
    4. The regenerated bytes are compared with the tracked bytes EXACTLY.
    5. On any difference the tracked bytes are RESTORED from the verified copy
       (and the restore is itself verified by digest), the regenerated file is
       preserved beside it as `<name>.regenerated`, and `FrozenError` names
       both paths plus the first differing byte offset and line.

    Returns {str(path): digest} on success.

    WHY EXACT BYTES, AND WHY NO TOLERANCE WILL BE ADDED.
    atopile's frozen mode compares its parsed model with `float_precision=2`
    (`src/faebryk/libs/kicad/fileformats.py:543-550`), i.e. 0.01 mm, so a
    footprint displaced by 9 um passes `--frozen`. A gate whose resolution is
    coarser than the change it is meant to catch reports the absence of large
    change and is read as the absence of change; GUARDS calls that out
    directly ("a threshold below the tool's own reproducibility reports tool
    noise" -- here the error is in the other direction and hides real change).
    This repo's generators write exact integer nanometres and are proven
    byte-reproducible by `run_and_check_reproducible`, so the reproducibility
    floor IS zero and an exact comparison sits on it legitimately.

    The consequence is honest and must be stated rather than papered over: a
    KiCad version bump, or any change to the generator's *formatting*, fails
    this gate. That is the correct verdict -- the committed artefact really is
    no longer what the generator produces -- and the fix is to regenerate and
    commit deliberately, with the toolchain change recorded, not to widen the
    comparison. atopile's issue #1543 is the demonstration of the other
    choice: their frozen mode fails on trailing zeros alone, was closed as
    stale, and the mode is now unusable.

    Establish `run_and_check_reproducible` on a generator BEFORE gating it with
    this. A non-deterministic generator fails `check_frozen` on every run for a
    reason that has nothing to do with the committed artefact, and the two
    failures are indistinguishable from the message alone.
    """
    outs = [Path(o) for o in outputs]
    if not outs:
        raise ReproError(
            "no outputs given -- regenerating and comparing nothing returns "
            "{} and reads as a pass")
    if len(set(o.resolve() for o in outs)) != len(outs):
        raise ReproError("duplicate paths in outputs: %s" % outputs)

    scratch = Path(keep_dir) if keep_dir else None
    if scratch is not None:
        scratch.mkdir(parents=True, exist_ok=True)

    # --- 1 & 2: settle, then save the tracked bytes and PROVE the save ------
    saved = {}
    for o in outs:
        if not o.exists():
            raise ReproError(
                "%s does not exist -- there is no committed artefact to hold "
                "frozen, so this is not a frozen check" % o)
        if o.stat().st_size == 0:
            raise ReproError("%s is zero bytes -- refusing to certify it"
                             % o)
        before = stable_digest(o, settle=settle, tries=tries, algo=algo)
        keep = (scratch or o.parent) / (o.name + ".tracked")
        shutil.copyfile(o, keep)
        if digest(keep, algo) != before:
            raise ReproError(
                "could not take a verified copy of %s (copy hashed "
                "differently) -- refusing to run a generator over a file this "
                "check cannot put back" % o)
        saved[o] = (before, keep, o.stat().st_mtime_ns)

    def _restore():
        problems = []
        for o, (d0, keep, _) in saved.items():
            try:
                shutil.copyfile(keep, o)
                if digest(o, algo) != d0:
                    problems.append("%s (restored bytes hash wrong)" % o)
            except OSError as e:
                problems.append("%s (%s)" % (o, e))
        return problems

    def _fail(exc_cls, msg):
        problems = _restore()
        if problems:
            msg += ("\n\nRESTORE FAILED for %s -- the verified copies are at "
                    "%s and must be put back BY HAND before anything else "
                    "reads these files."
                    % (", ".join(problems),
                       ", ".join(str(k) for _, k, _ in saved.values())))
        raise exc_cls(msg)

    # --- 3: run, and prove it ran -------------------------------------------
    # Space the run so a coarse-timestamp filesystem cannot make a genuine
    # rewrite share the tracked file's mtime and read as "did not run".
    time.sleep(spacing)
    proc = subprocess.run(cmd, cwd=cwd, env=env, capture_output=True,
                          text=True)
    if proc.returncode != 0:
        _fail(ReproError,
              "generator exited %d -- a non-zero exit leaves the output "
              "untouched, which is exactly what makes an unchanged digest "
              "meaningless.\nstderr:\n%s"
              % (proc.returncode, proc.stderr[-2000:]))

    for o, (_, _, mt0) in saved.items():
        if not o.exists():
            _fail(ReproError,
                  "generator exited 0 but %s is gone" % o)
        if o.stat().st_mtime_ns == mt0:
            _fail(ReproError,
                  "%s: mtime did not move -- the generator did not rewrite "
                  "it, so an unchanged digest proves nothing about whether "
                  "today's generator still produces the committed artefact"
                  % o)
        if o.stat().st_size == 0:
            _fail(ReproError, "%s: regenerated output is zero bytes" % o)

    # --- 4 & 5: compare exactly ---------------------------------------------
    changed = []
    for o, (d0, keep, _) in saved.items():
        now = digest(o, algo)
        if now != d0:
            regen = (scratch or o.parent) / (o.name + ".regenerated")
            shutil.copyfile(o, regen)
            off, line, was, isnow = _first_difference(
                Path(keep).read_bytes(), Path(regen).read_bytes())
            changed.append(
                "%s\n    tracked      %s  (%s)\n    regenerated  %s  (%s)\n"
                "    first difference at byte %d (line %d)\n"
                "      tracked      ...%s...\n      regenerated  ...%s..."
                % (o, d0, keep, now, regen, off, line, was, isnow))

    if changed:
        _fail(FrozenError,
              "FROZEN: %d of %d artefact(s) changed when regenerated. The "
              "committed artefact is no longer what the generator produces. "
              "The tracked files have been RESTORED; the regenerated bytes "
              "are kept beside them.\n\n%s"
              % (len(changed), len(outs), "\n\n".join(changed)))

    for _, keep, _ in saved.values():
        try:
            if keep_dir is None:
                Path(keep).unlink()
        except OSError:
            pass
    return {str(o): d0 for o, (d0, _, _) in saved.items()}


def verify_unchanged_since(path, expected_digest, algo="sha256"):
    """Assert `path` still hashes to `expected_digest`.

    Call this immediately before committing or publishing an artifact you
    verified earlier. If it raises, your ERC/DRC/audit results belong to a
    different file than the one you are about to ship.
    """
    now = digest(path, algo)
    if now != expected_digest:
        raise ReproError(
            "%s changed since it was verified: expected %s, found %s. The "
            "verification results do not apply to the current file (another "
            "process rewrote it?)." % (path, expected_digest, now))
    return now


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 3:
        raise SystemExit(
            "usage:\n"
            "  kicad_repro.py digest <file>\n"
            "  kicad_repro.py stable <file>\n"
            "  kicad_repro.py check  <output>... -- <cmd> [args...]\n"
            "  kicad_repro.py frozen <output>... -- <cmd> [args...]\n"
            "\n"
            "  check   runs <cmd> twice and requires the outputs to agree\n"
            "          (determinism)\n"
            "  frozen  runs <cmd> once and requires the outputs to come back\n"
            "          BYTE-IDENTICAL to what is already on disk, restoring\n"
            "          them if they do not (the committed artefact is still\n"
            "          what the generator produces). Exit 3 means the artefact\n"
            "          moved; exit 2 means the check could not be run.")
    mode = sys.argv[1]
    if mode == "digest":
        print(digest(sys.argv[2]))
    elif mode == "stable":
        print(stable_digest(sys.argv[2]))
    elif mode in ("check", "frozen"):
        try:
            sep = sys.argv.index("--")
        except ValueError:
            raise SystemExit(
                "%s needs `-- <cmd>`; without a command there is nothing to "
                "run and an empty result would read as a pass" % mode)
        outs = sys.argv[2:sep]
        cmd = sys.argv[sep + 1:]
        if not cmd:
            raise SystemExit("empty command after `--`")
        if mode == "check":
            for k, v in run_and_check_reproducible(cmd, outs).items():
                print("reproducible  %s  %s" % (v, k))
        else:
            try:
                for k, v in check_frozen(cmd, outs).items():
                    print("frozen-ok  %s  %s" % (v, k))
            except FrozenError as e:
                # Distinct from exit 2 (unevaluable): the check RAN and the
                # artefact moved. Conflating them would let a broken harness
                # be reported as a design change and vice versa.
                sys.stderr.write(str(e) + "\n")
                raise SystemExit(3)
            except ReproError as e:
                sys.stderr.write(str(e) + "\n")
                raise SystemExit(2)
    else:
        raise SystemExit("unknown mode %r" % mode)
