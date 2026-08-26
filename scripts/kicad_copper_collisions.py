#!/usr/bin/env python3
"""Fail-closed certain-short audit for a saved `.kicad_pcb`.

Detects copper that is a short by construction — before DRC and independent of
it: any two of the audited copper items (track segments, arcs, vias, pads)
that share a copper layer, carry different nets, and whose *effective copper
shapes* touch or overlap. Shapes come from `GetEffectiveShape(layer)`, so pad
rotation, flipped footprints, custom pads, and arcs are handled exactly by
KiCad's own geometry.

Collision semantics, probed on KiCad 10.0.5 (2026-08-26): `Collide(shape, 0)`
misses exact tangency for tracks and circles (1-IU overlap collides, exact
edge contact does not), so the audit passes a 1-IU (1 nm) clearance, which
collides tangent shapes and still rejects a 1-IU gap — i.e. exactly "touch or
overlap".

Coverage policy:
- Zone fills are excluded: the zone filler owns keeping fills off foreign
  copper, and filled polygons legally approach other nets to clearance.
- NPTH pads are included only on layers where they are flashed
  (`FlashLayer`): an unflashed NPTH's effective shape is its drill disk,
  which is a hole, not conductive copper — a track crossing it is a DRC
  matter, not a short. PTH pads and vias are audited on every layer they are
  on, flashed or not, because the plated barrel conducts.

This is a generator guard, not a DRC replacement: it knows nothing about
clearance, unrouted nets, silk, mask, or courtyards. Its one claim is "no two
nets are touching", which is exactly the class of failure a blind waypoint
router produces wholesale and DRC then reports as hundreds of
`shorting_items` / `tracks_crossing` findings. Run it after every generated
routing pass; repeated collisions at the same pins are placement evidence,
not routing bad luck.

Exit codes (fail closed, per GUARDS.md):
  0  audited, no collisions — and only with the explicit OK verdict line
  1  unevaluable — board missing/unloadable, nothing to audit, no usable
     pcbnew interpreter, bad CLI value or parse error, worker timeout, or an
     exception
  2  collisions found
`--help`/`--version`-style informational exits follow CLI convention (exit 0
without an audit); never put them in a gating invocation.

JSON lifecycle: when `--json` is given, a fresh "unevaluable / audit did not
complete" placeholder is written before anything is evaluated — a best-effort
pre-parse scan writes it even when the command line is later rejected — and
is atomically replaced (`os.replace`, temp file cleaned up on failure) by the
real verdict only when the audit finishes, so a crash, kill, parse error, or
bogus interpreter cannot leave a stale clean report standing. Every artifact
carries an explicit `verdict`, the audited board path, the writer's pid, and
the board file's size/mtime *snapshotted before load and re-verified after
the audit* — a board replaced mid-audit is unevaluable, not clean. An empty
`--json` path, or one aliasing the board file, is rejected. Concurrent audits
sharing one `--json` path are unsupported — last completed writer wins.

Usage:
  kicad_copper_collisions.py BOARD.kicad_pcb [--max-report N] [--json OUT]
                             [--timeout SECONDS]

Runs itself under KiCad's bundled interpreter when `pcbnew` is not importable.
Set KICAD_PYTHON to override discovery; a configured interpreter that cannot
`import pcbnew` is an unevaluable failure, not a silent fallback. A worker
exit of 0 is trusted only when the worker printed a line starting with the OK
verdict prefix and, when `--json` is in play, the artifact's verdict matches
the exit status (0 needs "clean", 2 needs "collisions"); worker statuses
outside 0/1/2 (signals, foreign launchers) normalize to unevaluable. Captured
worker output is decoded as UTF-8 with replacement, so an exotic locale
cannot crash the parent. Trust boundary: KICAD_PYTHON is *trusted configuration* — the probe
defends against misconfiguration (wrong python, echo-style stubs), not
against a deliberately malicious executable, which no output marker can
authenticate. `--timeout` kills and reaps the direct worker process only;
descendants are not tracked (the pcbnew worker spawns none).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys
import traceback

MAX_REPORT_DEFAULT = 40
WORKER_TIMEOUT_DEFAULT = 600
# Probed on 10.0.5: clearance 0 misses exact tangency; clearance 1 IU (1 nm)
# collides tangent shapes and rejects a 1-IU gap.
TOUCH_CLEARANCE_IU = 1
_WORKER_ENV = "KICAD_COPPER_COLLISIONS_WORKER"
# Built by concatenation so the marker never appears literally in the probe
# command text: an executable that merely echoes its arguments (e.g.
# /bin/echo) must not be able to satisfy the probe.
_PROBE_MARKER = "PCBNEW-" + "PROBE-OK"
_OK_LINE = "COPPER-COLLISIONS-OK"


# --------------------------------------------------------------------------- audit

def _iu_to_mm(pcbnew, v):
    return round(pcbnew.ToMM(int(v)), 4)


def _describe(pcbnew, item):
    cls = item.GetClass()
    pos = item.GetPosition()
    at = f"({_iu_to_mm(pcbnew, pos.x)},{_iu_to_mm(pcbnew, pos.y)})"
    if cls == "PAD":
        fp = item.GetParentFootprint()
        ref = fp.GetReference() if fp else "?"
        return f"pad {ref}.{item.GetNumber()} at {at}"
    return f"{cls.lower()} at {at}"


def collect_copper_items(board, pcbnew):
    """Return {layer_id: [items]} for tracks, arcs, vias, and pads.

    NPTH pads join a layer only when flashed there (drill disks are holes,
    not copper); everything else joins every copper layer it is on.
    """
    layers = [
        layer
        for layer in board.GetEnabledLayers().Seq()
        if pcbnew.IsCopperLayer(layer)
    ]
    per_layer = {layer: [] for layer in layers}
    for track in board.GetTracks():
        for layer in layers:
            if track.IsOnLayer(layer):
                per_layer[layer].append(track)
    for footprint in board.GetFootprints():
        for pad in footprint.Pads():
            npth = pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH
            for layer in layers:
                if not pad.IsOnLayer(layer):
                    continue
                if npth and not pad.FlashLayer(layer):
                    continue
                per_layer[layer].append(pad)
    return per_layer


def _bbox_overlaps(a, b, margin):
    return (
        a.GetLeft() - margin <= b.GetRight()
        and b.GetLeft() - margin <= a.GetRight()
        and a.GetTop() - margin <= b.GetBottom()
        and b.GetTop() - margin <= a.GetBottom()
    )


def audit_board(board, pcbnew):
    """Return (findings, inventory). A finding is a dict; no side effects."""
    per_layer = collect_copper_items(board, pcbnew)
    findings = []
    inventory = {"layers": {}, "items": 0}
    for layer, items in per_layer.items():
        layer_name = board.GetLayerName(layer)
        inventory["layers"][layer_name] = len(items)
        inventory["items"] += len(items)
        # Shapes are the audit subject, so take prefilter boxes from the
        # shapes themselves — item bounding boxes are not guaranteed to
        # contain every layer-effective shape.
        shapes = [item.GetEffectiveShape(layer) for item in items]
        boxes = [shape.BBox() for shape in shapes]
        for i in range(len(items)):
            net_a = items[i].GetNetCode()
            for j in range(i + 1, len(items)):
                if net_a == items[j].GetNetCode():
                    continue
                if not _bbox_overlaps(boxes[i], boxes[j], TOUCH_CLEARANCE_IU):
                    continue
                if shapes[i].Collide(shapes[j], TOUCH_CLEARANCE_IU):
                    findings.append(
                        {
                            "layer": layer_name,
                            "nets": sorted(
                                (items[i].GetNetname(), items[j].GetNetname())
                            ),
                            "a": _describe(pcbnew, items[i]),
                            "b": _describe(pcbnew, items[j]),
                        }
                    )
    return findings, inventory


# --------------------------------------------------------------------------- driver

def _stat_board(board_path):
    """Best-effort (size, mtime) snapshot of the board file, or None."""
    try:
        st = os.stat(board_path)
    except OSError:
        return None
    return (st.st_size, st.st_mtime)


def _same_path(a, b):
    try:
        return os.path.realpath(a) == os.path.realpath(b)
    except OSError:
        return False


def _write_json(json_out, board_path, verdict, findings, inventory,
                reason=None, board_stat=None):
    if not json_out:
        return
    if _same_path(json_out, board_path):
        raise ValueError("--json path must not be the board file")
    payload = {
        "board": os.path.abspath(board_path),
        "verdict": verdict,  # "collisions" | "clean" | "unevaluable"
        "findings": findings,
        "inventory": inventory,
    }
    if reason:
        payload["reason"] = reason
    payload["pid"] = os.getpid()
    if board_stat is None:
        board_stat = _stat_board(board_path)
    if board_stat is not None:
        payload["board_size"], payload["board_mtime"] = board_stat
    tmp = f"{json_out}.tmp.{os.getpid()}"
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=1)
        os.replace(tmp, json_out)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _unevaluable(json_out, board_path, reason, inventory=None):
    _write_json(json_out, board_path, "unevaluable", [], inventory or {}, reason)
    print(f"COPPER-COLLISIONS-UNEVALUABLE: {reason}")
    return 1


def run_audit(board_path, max_report=MAX_REPORT_DEFAULT, json_out=None):
    max_report = max(0, max_report)
    # Invalidate any stale artifact before evaluating anything: if this run
    # dies mid-audit, the report on disk must say "did not complete".
    _write_json(json_out, board_path, "unevaluable", [], {}, "audit did not complete")
    try:
        import wx

        wx.Log.SetLogLevel(wx.LOG_Error)
        wx.App(False)
    except Exception:
        pass
    import pcbnew  # bundled interpreter only

    if not os.path.isfile(board_path):
        return _unevaluable(json_out, board_path, f"no such board {board_path}")
    # Snapshot the input before loading; the verdict must bind the revision
    # that was actually audited, and a file replaced mid-audit must fail.
    snapshot = _stat_board(board_path)
    try:
        board = pcbnew.LoadBoard(board_path)
        if board is None:
            return _unevaluable(json_out, board_path, f"failed to load {board_path}")
        findings, inventory = audit_board(board, pcbnew)
    except Exception:
        sys.stderr.write(traceback.format_exc())
        return _unevaluable(
            json_out, board_path, "exception during load/audit (see stderr)"
        )
    if _stat_board(board_path) != snapshot:
        return _unevaluable(
            json_out, board_path, "board file changed during the audit"
        )
    if inventory["items"] == 0:
        return _unevaluable(
            json_out, board_path, "no copper items on any layer", inventory
        )
    verdict = "collisions" if findings else "clean"
    _write_json(json_out, board_path, verdict, findings, inventory,
                board_stat=snapshot)
    for finding in findings[:max_report]:
        nets = "×".join(finding["nets"])
        print(
            f"COPPER-COLLISION: {finding['layer']} {nets}: "
            f"{finding['a']} vs {finding['b']}"
        )
    if len(findings) > max_report:
        print(f"... and {len(findings) - max_report} more (see --json)")
    layer_counts = ", ".join(
        f"{name}:{count}" for name, count in sorted(inventory["layers"].items())
    )
    if findings:
        print(f"COPPER-COLLISIONS-FAIL: {len(findings)} certain shorts "
              f"({inventory['items']} copper items; {layer_counts})")
        return 2
    print(f"{_OK_LINE}: 0 collisions "
          f"({inventory['items']} copper items; {layer_counts})")
    return 0


def _interpreter_has_pcbnew(interpreter):
    """Probe that the interpreter really imports pcbnew.

    Requires exit 0 AND stdout equal to the probe marker up to surrounding
    whitespace (some wrappers append a newline). The marker
    is concatenated at runtime so it is absent from the command text: a bogus
    executable that echoes its arguments and exits 0 (/bin/echo) fails the
    exact-match, and one that ignores them (/usr/bin/true) prints nothing.
    """
    probe = (
        "import pcbnew, sys; sys.stdout.write('PCBNEW-' + 'PROBE-OK')"
    )
    try:
        proc = subprocess.run(
            [interpreter, "-c", probe],
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == _PROBE_MARKER


def _find_kicad_python():
    """Return (interpreter, error). A configured KICAD_PYTHON that fails the
    pcbnew probe is an error, not a fallthrough."""
    configured = os.environ.get("KICAD_PYTHON")
    if configured:
        if _interpreter_has_pcbnew(configured):
            return configured, None
        return None, (
            f"KICAD_PYTHON={configured} cannot import pcbnew "
            "(probe requires 'import pcbnew' to succeed)"
        )
    patterns = [
        "/Applications/KiCad/KiCad*.app/Contents/Frameworks/Python.framework/"
        "Versions/Current/bin/python3",
        "/usr/lib/kicad*/bin/python3",
    ]
    candidates = []
    for pattern in patterns:
        candidates.extend(sorted(glob.glob(pattern), reverse=True))
    candidates.extend(["/usr/bin/python3", "/usr/local/bin/python3"])
    for candidate in candidates:
        if os.path.isfile(candidate) and _interpreter_has_pcbnew(candidate):
            return candidate, None
    return None, "no interpreter with pcbnew found; set KICAD_PYTHON"


def _run_worker(interpreter, args):
    """Re-execute under `interpreter`; trust exit 0 only with the OK line."""
    cmd = [interpreter, "-u", os.path.abspath(__file__), args.board,
           "--max-report", str(args.max_report)]
    if args.json_out:
        cmd += ["--json", args.json_out]
    env = dict(os.environ, **{_WORKER_ENV: "1"})
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=args.timeout,
        )
    except subprocess.TimeoutExpired:
        return _unevaluable(
            args.json_out, args.board,
            f"worker exceeded --timeout {args.timeout}s",
        )
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    def artifact_verdict():
        try:
            with open(args.json_out, encoding="utf-8") as fh:
                return json.load(fh).get("verdict")
        except (OSError, ValueError):
            return None

    if proc.returncode == 0:
        ok = any(
            line.startswith(f"{_OK_LINE}:")
            for line in (proc.stdout or "").splitlines()
        )
        if not ok:
            return _unevaluable(
                args.json_out, args.board,
                "worker exited 0 without producing the OK verdict line",
            )
        if args.json_out and artifact_verdict() != "clean":
            return _unevaluable(
                args.json_out, args.board,
                f"worker exited 0 but JSON verdict is {artifact_verdict()!r}",
            )
        return 0
    if proc.returncode == 2:
        if args.json_out and artifact_verdict() != "collisions":
            return _unevaluable(
                args.json_out, args.board,
                f"worker exited 2 but JSON verdict is {artifact_verdict()!r}",
            )
        return 2
    if proc.returncode == 1:
        return 1
    # Signals and foreign statuses must not masquerade as a defined verdict.
    return _unevaluable(
        args.json_out, args.board,
        f"worker exited with unexpected status {proc.returncode}",
    )


class _Parser(argparse.ArgumentParser):
    """Parse errors exit 1: the contract reserves 2 for collisions."""

    def error(self, message):
        self.exit(1, f"COPPER-COLLISIONS-UNEVALUABLE: {message}\n")


def _prescan_json_and_board(argv):
    """Best-effort extraction of the --json path and board from raw argv, so
    a stale artifact can be invalidated even when argparse later rejects the
    command line. Returns (board_guess, json_guess); either may be None."""
    board = json_out = None
    it = iter(range(len(argv)))
    for i in it:
        arg = argv[i]
        if arg == "--json" and i + 1 < len(argv):
            json_out = argv[i + 1]
        elif arg.startswith("--json="):
            json_out = arg.split("=", 1)[1]
        elif not arg.startswith("-") and board is None:
            if i == 0 or argv[i - 1] not in ("--json", "--max-report", "--timeout"):
                board = arg
    return board, json_out


def main(argv=None):
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    # Invalidate any stale artifact even if parsing fails below; skip when
    # the pre-scan cannot tell the artifact apart from the board.
    board_guess, json_guess = _prescan_json_and_board(raw_argv)
    if json_guess and board_guess and not _same_path(json_guess, board_guess):
        try:
            _write_json(
                json_guess, board_guess, "unevaluable", [], {},
                "audit did not complete",
            )
        except OSError:
            pass

    parser = _Parser(description=__doc__.splitlines()[0])
    parser.add_argument("board")
    parser.add_argument("--max-report", type=int, default=MAX_REPORT_DEFAULT)
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--timeout", type=int, default=WORKER_TIMEOUT_DEFAULT,
                        help="worker re-execution timeout, seconds")
    args = parser.parse_args(raw_argv)
    # Exit-code contract reserves 2 for collisions, so reject bad values with
    # the unevaluable code instead of argparse's exit(2).
    if args.max_report < 0:
        return _unevaluable(args.json_out, args.board, "--max-report must be >= 0")
    if args.timeout <= 0:
        return _unevaluable(args.json_out, args.board, "--timeout must be > 0")
    if args.json_out is not None and not args.json_out:
        return _unevaluable(None, args.board, "--json path must be non-empty")
    if args.json_out and _same_path(args.json_out, args.board):
        return _unevaluable(
            None, args.board, "--json path must not be the board file"
        )

    # Invalidate any stale artifact before anything is evaluated — including
    # the import attempt: a broken native pcbnew can raise more than
    # ImportError.
    _write_json(
        args.json_out, args.board, "unevaluable", [], {},
        "audit did not complete",
    )
    try:
        import pcbnew  # noqa: F401
    except ImportError:
        if os.environ.get(_WORKER_ENV):
            return _unevaluable(
                args.json_out, args.board,
                "re-executed interpreter still cannot import pcbnew",
            )
        interpreter, error = _find_kicad_python()
        if not interpreter:
            return _unevaluable(args.json_out, args.board, error)
        return _run_worker(interpreter, args)
    except Exception:
        sys.stderr.write(traceback.format_exc())
        return _unevaluable(
            args.json_out, args.board, "pcbnew import failed (see stderr)"
        )

    return run_audit(args.board, args.max_report, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
