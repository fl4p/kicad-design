#!/usr/bin/env python3
"""Functional-proximity guard: verify declared satellite→anchor placement bindings.

Motivation (measured): a placement generator binned DNP snubber strings as "small
passives, bottom row" ~90 mm from the switches they snub. Every mechanical gate
(courtyards, body gaps, populate-ability) passed, because none asks the electrical
question. The knowledge existed at capture time ("D-S per device" in the design
record) and was never consulted again. This guard replays capture-time intent at
layout time, mechanically.

Contract: a footprint DECLARES a binding by carrying these properties (fields):

  Anchor   = <reference designator of the partner footprint>   (declares the binding)
  MaxDist  = <millimetres>                                      (optional per-part
             budget; otherwise --default-max-mm applies)

For every declaring footprint, the guard computes the minimum pad-centre to
pad-centre distance to its anchor footprint and compares it against the budget.
The generator/schematic phase is responsible for EMITTING the bindings (see
POWER.md "Functional satellites"); this guard can only verify recorded intent —
it cannot invent intent that was never declared.

Usage:
  kicad_functional_proximity.py <board.kicad_pcb> [--default-max-mm 15]
                                [--min-expected N] [--allow-empty]

Verdicts (fail-closed; every run prints exactly one verdict line):
  0  FUNC-PROX-PASS        all declared bindings within budget (and at least
                           --min-expected declared; at least one, unless
                           --allow-empty explicitly accepts a vacuous run)
  1  FUNC-PROX-FAIL        at least one binding exceeds its budget
  2  FUNC-PROX-UNVERIFIED  the guard could not be trusted: unreadable board,
                           anchor refdes missing or ambiguous, self-anchor,
                           malformed MaxDist, fewer declared than --min-expected,
                           or zero declared without --allow-empty (a vacuous run
                           must be visible, never silently green)

A PASS line reports the count checked and the worst (largest) margin-consuming
pair; FAIL lists every violating binding with its measured distance, budget, and
closest pad pair. Distances are pad-centre based: adequate at millimetre-scale
budgets, and deliberately simpler than the body-volume measurement PCB.md's DNP
feasibility branch requires — this guard gates proximity, not stuffability.
"""

import math
import re
import sys

MM = 1.0


def verdict(code: int, msg: str) -> int:
    tag = {0: "FUNC-PROX-PASS", 1: "FUNC-PROX-FAIL", 2: "FUNC-PROX-UNVERIFIED"}[code]
    print("%s: %s" % (tag, msg))
    return code


def parse_board(path):
    """Return {refdes: {"at": (x, y, rot), "pads": [(name, gx, gy)], "props": {...}}}."""
    with open(path, encoding="utf-8", errors="replace") as f:
        s = f.read()
    fps = {}
    starts = [m.start() for m in re.finditer(r"\(footprint ", s)]
    starts.append(len(s))
    for i in range(len(starts) - 1):
        b = s[starts[i]:starts[i + 1]]
        ref_m = re.search(r'\(property "Reference" "([^"]+)"', b)
        at_m = re.search(r"\(at ([-\d.]+) ([-\d.]+)(?: ([-\d.]+))?\)", b)
        if not ref_m or not at_m:
            continue
        ref = ref_m.group(1)
        fx, fy = float(at_m.group(1)), float(at_m.group(2))
        rot = float(at_m.group(3) or 0)
        th = math.radians(rot)
        props = dict(re.findall(r'\(property "([^"]+)" "([^"]*)"', b))
        pads = []
        for pm in re.finditer(r'\(pad "([^"]*)"[^(]*\(at ([-\d.]+) ([-\d.]+)', b):
            x, y = float(pm.group(2)), float(pm.group(3))
            gx = fx + x * math.cos(th) + y * math.sin(th)
            gy = fy - x * math.sin(th) + y * math.cos(th)
            pads.append((pm.group(1), gx, gy))
        if ref in fps:
            fps[ref] = None  # ambiguous refdes: poison it, never guess
        else:
            fps[ref] = {"pads": pads, "props": props}
    return fps


def main(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    opts = [a for a in argv[1:] if a.startswith("--")]
    default_max = 15.0
    min_expected = 0
    allow_empty = False
    try:
        for o in opts:
            if o.startswith("--default-max-mm"):
                default_max = float(o.split("=", 1)[1])
            elif o.startswith("--min-expected"):
                min_expected = int(o.split("=", 1)[1])
            elif o == "--allow-empty":
                allow_empty = True
            else:
                return verdict(2, "unknown option %s" % o)
    except (ValueError, IndexError):
        return verdict(2, "malformed option (use --default-max-mm=MM, --min-expected=N)")
    if len(args) != 1:
        return verdict(2, "want exactly one board file (plus options)")
    try:
        fps = parse_board(args[0])
    except OSError as exc:
        return verdict(2, "cannot read board: %s" % exc)
    if not fps:
        return verdict(2, "no footprints parsed — wrong or corrupt file?")

    declared = []
    for ref, fp in sorted(fps.items()):
        if fp is None or "Anchor" not in (fp or {}).get("props", {}):
            continue
        declared.append((ref, fp))
    for ref, fp in list(fps.items()):
        if fp is None:
            return verdict(2, "reference designator %r is ambiguous (multiple footprints)" % ref)

    if len(declared) < min_expected:
        return verdict(2, "%d binding(s) declared but --min-expected=%d — bindings "
                          "missing at capture time" % (len(declared), min_expected))
    if not declared:
        if allow_empty:
            return verdict(0, "0 bindings declared (vacuous run explicitly allowed "
                              "by --allow-empty; this verifies nothing)")
        return verdict(2, "0 bindings declared — nothing to verify; a vacuous run is "
                          "not a pass (use --allow-empty only for boards with no "
                          "functional satellites)")

    failures = []
    results = []
    for ref, fp in declared:
        assert fp is not None  # filtered when collecting `declared`
        anchor = fp["props"]["Anchor"].strip()
        if not anchor:
            return verdict(2, "%s declares an empty Anchor" % ref)
        if anchor == ref:
            return verdict(2, "%s anchors to itself" % ref)
        afp = fps.get(anchor)
        if afp is None:
            return verdict(2, "%s anchors to %r which is not on the board" % (ref, anchor))
        budget = default_max
        if "MaxDist" in fp["props"]:
            try:
                budget = float(fp["props"]["MaxDist"])
            except ValueError:
                return verdict(2, "%s has malformed MaxDist %r" % (ref, fp["props"]["MaxDist"]))
        if not fp["pads"] or not afp["pads"]:
            return verdict(2, "%s or its anchor %s has no pads to measure" % (ref, anchor))
        best = None
        for pn, px, py in fp["pads"]:
            for qn, qx, qy in afp["pads"]:
                d = math.hypot(px - qx, py - qy)
                if best is None or d < best[0]:
                    best = (d, pn, qn)
        if best is None:
            return verdict(2, "no measurable pad pair for %s→%s" % (ref, anchor))
        d, pn, qn = best
        results.append((ref, anchor, d, budget, pn, qn))
        if d > budget:
            failures.append((ref, anchor, d, budget, pn, qn))

    if failures:
        detail = "; ".join(
            "%s→%s %.2fmm > %.1fmm (pads %s↔%s)" % f for f in failures)
        return verdict(1, "%d of %d binding(s) exceed budget: %s"
                       % (len(failures), len(results), detail))
    worst = max(results, key=lambda r: r[2] / r[3])
    return verdict(0, "%d binding(s) within budget; tightest margin %s→%s "
                      "%.2fmm of %.1fmm" % (len(results), worst[0], worst[1],
                                            worst[2], worst[3]))


if __name__ == "__main__":
    sys.exit(main(sys.argv))
