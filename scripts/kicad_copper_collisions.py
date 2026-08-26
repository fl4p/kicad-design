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
     pcbnew interpreter, bad CLI value, worker timeout, or an exception
  2  collisions found

JSON lifecycle: when `--json` is given, a fresh "unevaluable / audit did not
complete" placeholder is written before any evaluation and is only replaced
by the real verdict when the audit finishes, so a crash, kill, or bogus
interpreter can never leave a stale clean report standing. Every artifact
carries an explicit `verdict` field and the audited board path. (The one gap:
an argparse-level error exits before the JSON path is known.)

Usage:
  kicad_copper_collisions.py BOARD.kicad_pcb [--max-report N] [--json OUT]
                             [--timeout SECONDS]

Runs itself under KiCad's bundled interpreter when `pcbnew` is not importable.
Set KICAD_PYTHON to override discovery; a configured interpreter that cannot
`import pcbnew` is an unevaluable failure, not a silent fallback. A worker
exit of 0 is trusted only when the worker printed the OK verdict line.
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

def _write_json(json_out, board_path, verdict, findings, inventory, reason=None):
    if not json_out:
        return
    payload = {
        "board": os.path.abspath(board_path),
        "verdict": verdict,  # "collisions" | "clean" | "unevaluable"
        "findings": findings,
        "inventory": inventory,
    }
    if reason:
        payload["reason"] = reason
    with open(json_out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)


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
    if inventory["items"] == 0:
        return _unevaluable(
            json_out, board_path, "no copper items on any layer", inventory
        )
    verdict = "collisions" if findings else "clean"
    _write_json(json_out, board_path, verdict, findings, inventory)
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

    Requires exit 0 AND stdout that is exactly the probe marker. The marker
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
            text=True,
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
            cmd, env=env, capture_output=True, text=True, timeout=args.timeout
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
    if proc.returncode == 0 and _OK_LINE not in (proc.stdout or ""):
        return _unevaluable(
            args.json_out, args.board,
            "worker exited 0 without producing the OK verdict line",
        )
    return proc.returncode


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("board")
    parser.add_argument("--max-report", type=int, default=MAX_REPORT_DEFAULT)
    parser.add_argument("--json", dest="json_out")
    parser.add_argument("--timeout", type=int, default=WORKER_TIMEOUT_DEFAULT,
                        help="worker re-execution timeout, seconds")
    args = parser.parse_args(argv)
    # Exit-code contract reserves 2 for collisions, so reject bad values with
    # the unevaluable code instead of argparse's exit(2).
    if args.max_report < 0:
        return _unevaluable(args.json_out, args.board, "--max-report must be >= 0")
    if args.timeout <= 0:
        return _unevaluable(args.json_out, args.board, "--timeout must be > 0")

    try:
        import pcbnew  # noqa: F401
    except ImportError:
        # Invalidate any stale artifact before launcher decisions, too.
        _write_json(
            args.json_out, args.board, "unevaluable", [], {},
            "audit did not complete",
        )
        if os.environ.get(_WORKER_ENV):
            return _unevaluable(
                args.json_out, args.board,
                "re-executed interpreter still cannot import pcbnew",
            )
        interpreter, error = _find_kicad_python()
        if not interpreter:
            return _unevaluable(args.json_out, args.board, error)
        return _run_worker(interpreter, args)

    return run_audit(args.board, args.max_report, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
