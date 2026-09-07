#!/usr/bin/env python3
"""Precondition probe: is a KiCad editor holding this artefact?

`SKILL.md` already instructs the agent to "check for a running KiCad GUI holding
a stale copy before regenerating". This makes that check executable, and it is a
PRECONDITION, not a verdict about the board: run it immediately before any script
that rewrites a tracked `.kicad_pcb` / `.kicad_sch` / `.kicad_pro`.

WHAT THIS CAN AND CANNOT ESTABLISH -- read before trusting NOT-HELD.

Two independent signals exist, and NEITHER can prove a file is free:

* **The lock file.** KiCad writes `<dir>/~<basename>.lck` beside a file it opens
  in an editor, and removes it on close. Measured on this machine (KiCad 10.0.5,
  2026-09-07) the content is exactly `{"hostname":"...","username":"..."}` --
  **there is no pid and no timestamp inside it**, so a lock left by a crashed or
  killed KiCad is byte-identical to a live one. A lock therefore means REFUSE TO
  WRITE; it never means "a human is sitting there". Four such files were found on
  this machine at the time of writing, one naming a hostname that is not this
  host, which is what a stale lock looks like.
* **The IPC socket.** KiCad 9+ can expose an API server on a unix socket in
  `/tmp/kicad` (`%TEMP%\\kicad` on Windows). atopile enumerates these and asks
  each client which board it holds. That question needs the `kipy` package, which
  this repo will not take as a dependency, so the socket is only ever evidence
  that *some* KiCad is running with the API on -- never which document it has.
  And the API server is **off by default**, so an absent socket is worth nothing.

Consequently the verdict is deliberately asymmetric and monotone: evidence of
danger can only ever move the answer toward HELD, never toward NOT-HELD.

    HELD       a lock file exists for this exact path -- including when its
               CONTENT is malformed. A lock whose body cannot be parsed is
               still a lock; treating unreadable content as UNKNOWN would let
               a corrupt lock read as less dangerous than a valid one, and
               this scale is deliberately asymmetric the other way.
    UNKNOWN    the lock file exists but cannot be READ at all; or the
               containing directory cannot be listed; or no lock exists but a
               KiCad IPC socket does, so a KiCad is running and this probe
               cannot ask it what it has open.
    NOT-HELD   no lock file for this path, the directory WAS readable, and no
               KiCad IPC socket exists anywhere.

There is no branch that returns NOT-HELD without having positively read the
directory. An unreadable parent is UNKNOWN, never a pass -- that is GUARDS-1, and
it is the specific failure this probe exists to avoid: a probe that returns
"free" because it could not look is worse than no probe, because it reads as a
finding.

WHAT WAS TAKEN FROM atopile, AND WHAT WAS NOT
---------------------------------------------
Origin: atopile (https://github.com/atopile/atopile), MIT, release **0.15.8**
(PyPI sdist `atopile-0.15.8.tar.gz`,
sha256 76c42f33f151947dbe533d4d0a04b60623b84c38ced6e40d17c3099e5f59b955).
Not present in the git repository at any tag -- see `plans/atopile-adoption.md`.

Reimplemented from, not copied from:
  * `src/faebryk/libs/kicad/ipc.py:40-49`  (`_kicad_socket_files`) -- the socket
    enumeration: `base.glob("api*.sock")` on POSIX, named pipes under `\\.\\pipe\\`
    on Windows.
  * `src/faebryk/libs/kicad/paths.py:148-153` (`get_ipc_socket_path`) -- the
    socket directory: `%TEMP%/kicad` on Windows, `/tmp/kicad` elsewhere.
  * `src/faebryk/libs/kicad/ipc.py:97-99`   (`has_pending_changes`) -- the
    unconditional `return True` above its own dead implementation. That is
    fail-closed and it is the right default; this probe keeps the posture and
    drops the dead code.

Deliberately NOT taken:
  * `enable_plugin_api()` (`ipc.py:30-37`) rewrites the user's
    `kicad_common.json` to switch the API server on. A probe must not modify the
    machine it is probing to make itself work. Nothing here writes anything.
  * The `kipy` conversation (`ipc.py:71-93`) -- a third-party dependency.
  * `reload()` (`ipc.py:129-146`), which saves the editor's in-memory board to a
    timestamped backup and then reverts the client so the on-disk file wins. The
    intent is sound but it silently discards a human's unsaved work; in this
    repo that decision belongs to the human, so the probe reports and refuses.

The lock file is NOT from atopile -- atopile never reads it (grepped: `.lck`
appears nowhere in `src/`). It is the stronger signal of the two here, because it
is per-path and needs no configuration, and it is the reason this probe can say
anything specific at all.

Usage:
  kicad_open_probe.py PATH [PATH...] [--json]

Exit codes follow the house convention:
  0  NOT-HELD for every path   -- safe to write
  1  HELD                      -- refuse to write
  2  UNKNOWN                   -- unevaluable; refuse to write
"""

from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
import tempfile
from pathlib import Path

__all__ = [
    "HELD",
    "UNKNOWN",
    "NOT_HELD",
    "lock_path_for",
    "ipc_socket_dir",
    "ipc_sockets",
    "kicad_holding",
]

HELD = "HELD"
UNKNOWN = "UNKNOWN"
NOT_HELD = "NOT-HELD"

_CODE = {NOT_HELD: 0, HELD: 1, UNKNOWN: 2}


def lock_path_for(path):
    """`<dir>/~<basename>.lck` -- KiCad's own naming, verified on 10.0.5.

    Uses the path as given: resolving symlinks here would look for the lock
    beside the *target*, and KiCad locks the name it was asked to open.
    """
    p = Path(path)
    if p.name in ("", ".", ".."):
        raise ValueError("%r does not name a file" % str(path))
    return p.parent / ("~" + p.name + ".lck")


def ipc_socket_dir():
    """atopile `paths.py:148-153`, reimplemented."""
    if sys.platform.startswith("win"):
        return Path(tempfile.gettempdir()) / "kicad"
    return Path("/tmp") / "kicad"


def ipc_sockets():
    """(sockets, readable). `readable` False means the answer is UNKNOWN-worthy.

    A MISSING directory is readable-and-empty: KiCad creates `/tmp/kicad` only
    when the API server starts, so its absence is a real observation. A
    directory that exists but cannot be listed is NOT an observation.
    """
    d = ipc_socket_dir()
    try:
        if not d.is_dir():
            return [], True
    except OSError:
        return [], False
    try:
        if sys.platform.startswith("win"):
            names = os.listdir(r"\\.\pipe" "\\")
            return [n for n in names if n.startswith("kicad")], True
        # os.listdir, NOT Path.glob. Measured by this module's own
        # calibration: `Path.glob` on a directory the process cannot read
        # yields nothing and raises nothing, so the unreadable case came back
        # as "no sockets" -- unevaluable input reading as a pass, GUARDS-1, in
        # the very function written to prevent it. listdir raises.
        names = os.listdir(d)
    except OSError:
        return [], False
    return sorted(d / n for n in names
                  if fnmatch.fnmatch(n, "api*.sock")), True


def _read_lock(lock):
    """(state, detail) for an existing lock file."""
    try:
        raw = lock.read_text(encoding="utf-8")
    except OSError as e:
        return UNKNOWN, "lock file %s exists but cannot be read (%s)" % (lock, e)
    except UnicodeDecodeError:
        return UNKNOWN, "lock file %s exists but is not UTF-8" % lock
    try:
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            raise ValueError("not an object")
    except (ValueError, TypeError):
        # Held is still the safe reading: the file's EXISTENCE is the lock;
        # its content is only there to name the holder.
        return HELD, ("lock file %s exists (content unparseable, %d bytes) -- "
                      "KiCad holds this document" % (lock, len(raw)))
    who = "%s@%s" % (obj.get("username", "?"), obj.get("hostname", "?"))
    return HELD, (
        "lock file %s exists, held by %s. The lock carries NO pid and NO "
        "timestamp, so this probe cannot distinguish a live editor from a "
        "lock left by a crashed one -- close KiCad, or delete the lock "
        "deliberately after confirming no editor has the file open."
        % (lock, who))


def kicad_holding(path):
    """(state, detail). See the module docstring for the state semantics."""
    p = Path(path)
    try:
        lock = lock_path_for(p)
    except ValueError as e:
        return UNKNOWN, str(e)

    parent = lock.parent
    try:
        exists = lock.exists()
    except OSError as e:
        return UNKNOWN, ("cannot stat %s (%s); a read that failed is not an "
                         "observation" % (lock, e))
    # LIST the directory rather than asking access(2) whether we could. A
    # negative `exists()` plus `R_OK` is a prediction about a read, not a read
    # (codex review of 0f709ed found NOT-HELD returned with no listdir on the
    # board's own directory). os.access can also disagree with the kernel
    # under ACLs, and Path.glob on an unreadable directory yields nothing and
    # raises nothing -- the same silent-empty trap this module's own IPC probe
    # already had.
    try:
        listing = os.listdir(parent)
        parent_ok = True
    except OSError as e:
        listing, parent_ok = None, False
        parent_error = e

    if exists:
        return _read_lock(lock)

    if not parent_ok:
        return UNKNOWN, (
            "%s could not be listed (%s), so the absence of %s is not an "
            "observation" % (parent, parent_error, lock.name))
    if lock.name in listing:
        # The listing is the observation; `exists()` raced or lied.
        return _read_lock(lock)

    socks, sock_ok = ipc_sockets()
    if not sock_ok:
        return UNKNOWN, (
            "no lock file, but %s could not be listed -- a running KiCad "
            "cannot be ruled out" % ipc_socket_dir())
    if socks:
        return UNKNOWN, (
            "no lock file for this path, but %d KiCad IPC socket(s) exist in "
            "%s. Asking a client which document it holds needs the `kipy` "
            "package, which this repo does not depend on, so a KiCad is "
            "running and this probe cannot say what it has open."
            % (len(socks), ipc_socket_dir()))

    return NOT_HELD, (
        "no lock file %s, and no KiCad IPC socket in %s"
        % (lock.name, ipc_socket_dir()))


# ------------------------------------------------------------------- CLI ---

def verdict(code, msg, branch=""):
    tag = {0: "KICAD-OPEN-PASS", 1: "KICAD-OPEN-HELD",
           2: "KICAD-OPEN-UNVERIFIED"}[code]
    bid = "[%s] " % branch if branch else ""
    line = "%s: %s%s" % (tag, bid, msg)
    line = line.encode("ascii", "backslashreplace").decode("ascii")
    sys.stdout.write(re.sub(r"[\x00-\x1f\x7f]", " ", line) + "\n")
    return code


class GuardArgumentParser(argparse.ArgumentParser):
    """argparse's usage-error exit is 2, which collides with UNVERIFIED here;
    both mean "do not write", so the collision is benign in effect, but the
    message must still say which happened."""

    def error(self, message):
        self.print_usage(sys.stderr)
        raise SystemExit(
            "KICAD-OPEN-UNVERIFIED: [E-ARGS] bad invocation: %s -- a malformed "
            "command line is unevaluable, not a statement about any file"
            % message)


def run(argv):
    ap = GuardArgumentParser(
        prog="kicad_open_probe.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", metavar="PATH")
    ap.add_argument("--json", action="store_true",
                    help="emit one JSON object per path on stdout")
    args = ap.parse_args(argv[1:])

    if len(set(str(Path(p)) for p in args.paths)) != len(args.paths):
        return verdict(2, "duplicate paths in the argument list", "E-ARGS")

    rows = [(p,) + kicad_holding(p) for p in args.paths]
    if args.json:
        sys.stdout.write(json.dumps(
            [{"path": p, "state": s, "detail": d} for p, s, d in rows],
            indent=2) + "\n")

    worst = max(_CODE[s] for _, s, _ in rows)
    # Report the count from the data, never a literal (GUARDS: derived counts).
    tally = {}
    for _, s, _ in rows:
        tally[s] = tally.get(s, 0) + 1
    summary = ", ".join("%d %s" % (tally[k], k) for k in sorted(tally))
    detail = "; ".join("%s: %s" % (p, d) for p, s, d in rows
                       if _CODE[s] == worst)
    return verdict(worst, "%d path(s) probed (%s). %s"
                   % (len(rows), summary, detail))


def main(argv):
    try:
        return run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - never die without a verdict
        return verdict(2, "internal error, refusing to grade: %r" % exc,
                       "E-INTERNAL")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
