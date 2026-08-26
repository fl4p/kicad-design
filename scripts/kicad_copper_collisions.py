#!/usr/bin/env python3
"""Fail-closed certain-short audit for a saved `.kicad_pcb`.

Detects copper that is a short by construction — before DRC and independent of
it: any two copper items (track segment, arc, via, pad) that share a copper
layer, carry different nets, and whose *effective copper shapes* touch or
overlap. Shapes come from `GetEffectiveShape(layer)`, so pad rotation, flipped
footprints, custom pads, and arcs are handled exactly by KiCad's own geometry.

This is a generator guard, not a DRC replacement: it knows nothing about
clearance, silk, mask, or courtyards. Its one claim is "no two nets are
touching", which is exactly the class of failure a blind waypoint router
produces wholesale and DRC then reports as hundreds of `shorting_items` /
`tracks_crossing` findings. Run it after every generated routing pass; repeated
collisions at the same pins are placement evidence, not routing bad luck.

Zone fills are deliberately excluded: the zone filler owns keeping fills off
foreign copper, and filled polygons legally approach other nets to clearance.

Exit codes (fail closed, per GUARDS.md):
  0  audited, no collisions
  1  unevaluable input — board missing/unloadable, or no copper items to audit
     (an empty audit is not a pass)
  2  collisions found

Usage:
  kicad_copper_collisions.py BOARD.kicad_pcb [--max-report N] [--json OUT]

Runs itself under KiCad's bundled interpreter when `pcbnew` is not importable
(set KICAD_PYTHON to override discovery).
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import subprocess
import sys

MAX_REPORT_DEFAULT = 40


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
    """Return {layer_id: [items]} for tracks, arcs, vias, and pads."""
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
            for layer in layers:
                if pad.IsOnLayer(layer):
                    per_layer[layer].append(pad)
    return per_layer


def _bbox_overlaps(a, b):
    return (
        a.GetLeft() <= b.GetRight()
        and b.GetLeft() <= a.GetRight()
        and a.GetTop() <= b.GetBottom()
        and b.GetTop() <= a.GetBottom()
    )


def audit_board(board, pcbnew):
    """Return (findings, inventory). A finding is a dict; no side effects."""
    per_layer = collect_copper_items(board, pcbnew)
    findings = []
    inventory = {"layers": {}, "items": 0}
    shape_cache = {}

    def shape_of(item, layer):
        key = (id(item), layer)
        if key not in shape_cache:
            shape_cache[key] = item.GetEffectiveShape(layer)
        return shape_cache[key]

    for layer, items in per_layer.items():
        layer_name = board.GetLayerName(layer)
        inventory["layers"][layer_name] = len(items)
        inventory["items"] += len(items)
        boxes = [item.GetBoundingBox() for item in items]
        for i in range(len(items)):
            item_a, box_a = items[i], boxes[i]
            net_a = item_a.GetNetCode()
            for j in range(i + 1, len(items)):
                item_b = items[j]
                if net_a == item_b.GetNetCode():
                    continue
                if not _bbox_overlaps(box_a, boxes[j]):
                    continue
                if shape_of(item_a, layer).Collide(shape_of(item_b, layer), 0):
                    findings.append(
                        {
                            "layer": layer_name,
                            "nets": sorted(
                                (item_a.GetNetname(), item_b.GetNetname())
                            ),
                            "a": _describe(pcbnew, item_a),
                            "b": _describe(pcbnew, item_b),
                        }
                    )
    return findings, inventory


# --------------------------------------------------------------------------- driver

def run_audit(board_path, max_report=MAX_REPORT_DEFAULT, json_out=None):
    try:
        import wx

        wx.Log.SetLogLevel(wx.LOG_Error)
        wx.App(False)
    except Exception:
        pass
    import pcbnew  # bundled interpreter only

    if not os.path.isfile(board_path):
        print(f"COPPER-COLLISIONS-UNEVALUABLE: no such board {board_path}")
        return 1
    board = pcbnew.LoadBoard(board_path)
    if board is None:
        print(f"COPPER-COLLISIONS-UNEVALUABLE: failed to load {board_path}")
        return 1
    findings, inventory = audit_board(board, pcbnew)
    if json_out:
        with open(json_out, "w", encoding="utf-8") as fh:
            json.dump({"findings": findings, "inventory": inventory}, fh, indent=1)
    if inventory["items"] == 0:
        print("COPPER-COLLISIONS-UNEVALUABLE: no copper items on any layer")
        return 1
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
    print(f"COPPER-COLLISIONS-OK: 0 collisions "
          f"({inventory['items']} copper items; {layer_counts})")
    return 0


def _find_kicad_python():
    configured = os.environ.get("KICAD_PYTHON")
    if configured:
        return configured
    patterns = [
        "/Applications/KiCad/KiCad*.app/Contents/Frameworks/Python.framework/"
        "Versions/Current/bin/python3",
        "/usr/lib/kicad*/bin/python3",
    ]
    for pattern in patterns:
        hits = sorted(glob.glob(pattern))
        if hits:
            return hits[-1]
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("board")
    parser.add_argument("--max-report", type=int, default=MAX_REPORT_DEFAULT)
    parser.add_argument("--json", dest="json_out")
    args = parser.parse_args(argv)

    try:
        import pcbnew  # noqa: F401
    except ImportError:
        interpreter = _find_kicad_python()
        if not interpreter:
            print(
                "COPPER-COLLISIONS-UNEVALUABLE: pcbnew not importable and no "
                "bundled interpreter found; set KICAD_PYTHON"
            )
            return 1
        cmd = [interpreter, "-u", os.path.abspath(__file__), args.board,
               "--max-report", str(args.max_report)]
        if args.json_out:
            cmd += ["--json", args.json_out]
        proc = subprocess.run(cmd)
        return proc.returncode

    return run_audit(args.board, args.max_report, args.json_out)


if __name__ == "__main__":
    sys.exit(main())
