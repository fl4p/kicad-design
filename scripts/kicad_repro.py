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
candidate that is never selected leaves no trace in the output. See PCB.md,
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
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import time
from pathlib import Path

__all__ = [
    "ReproError",
    "digest",
    "digest_with_identity",
    "stable_digest",
    "run_and_check_reproducible",
    "verify_unchanged_since",
]


class ReproError(AssertionError):
    """A reproducibility claim could not be substantiated."""


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
            "  kicad_repro.py check <output> -- <cmd> [args...]")
    mode = sys.argv[1]
    if mode == "digest":
        print(digest(sys.argv[2]))
    elif mode == "stable":
        print(stable_digest(sys.argv[2]))
    elif mode == "check":
        sep = sys.argv.index("--")
        outs = sys.argv[2:sep]
        cmd = sys.argv[sep + 1:]
        for k, v in run_and_check_reproducible(cmd, outs).items():
            print("reproducible  %s  %s" % (v, k))
    else:
        raise SystemExit("unknown mode %r" % mode)
