#!/usr/bin/env python3
"""Routing-shape audit for a saved `.kicad_pcb`.

DRC answers "is this legal". `kicad_copper_collisions.py` answers "is anything
shorted". Neither can see that a board is routed *badly*. This tool measures
the shape of the route — the signatures that distinguish an authored topology
from a search log — and, when thresholds are supplied, grades them.

Reported metrics (all from `pcbnew`, never from a text parse of the file):

- **vias per routed net** (reported only): total vias divided by the number of
  nets carrying track copper. This mean is DILUTABLE — adding via-free nets
  lowers it without removing a via — so it is never graded. `--max-vias-on-any-
  net` grades **max_vias_on_a_net**, the per-net maximum, which no added net
  can lower.
- **arcs**: arc count. Arc length comes from `GetLength()`, not the chord, and
  arcs are attributed to neither routing axis.
- **via layer-span histogram**: how many vias span which layer pair. On a board
  with THREE OR MORE copper layers, a high full-stack fraction is evidence that
  layer transitions were not planned — but it is evidence, not proof: a design
  that deliberately restricts itself to economical through-vias is also 100 %
  full-stack. On a 2-layer board every ordinary via is full-stack by
  construction, so the fraction is reported `unevaluable` there and no
  threshold can grade it. A through-via's endpoint span never reveals which
  inner-layer copper it electrically serves.
- **segment length distribution**: median, mean, p90, and the fraction of
  segments below `--short-segment-mm` (default 0.2 mm). A large short-segment
  fraction is a per-cell search path emitted verbatim.
- **per-layer copper length and segment count**.
- **direction conformance**: for each layer named in `--layer-direction`, the
  fraction of that layer's copper *length* running along the declared preferred
  axis. Diagonal copper counts towards neither axis; it is reported separately
  as the off-axis remainder. Computed only for layers explicitly declared —
  an undeclared layer has no preferred direction to conform to, and is reported
  `unevaluable`, never as a pass.

Every metric that cannot be computed from the inputs given is `unevaluable`. In
a gating run an unevaluable metric that a threshold names is a FAIL, per
`../GUARDS.md`: missing data never becomes OK, and a threshold whose input is
absent has not been satisfied.

Gating contract (fail closed):

- `--report-only` prints the metrics, grades nothing, and exits 0 with an
  explicit NOT-GRADED verdict line. It is not a pass and must never be used as
  a gate.
- A gating run needs at least one threshold. No threshold and no
  `--report-only` is UNEVALUABLE (exit 1), not a silent pass — the same shape
  as `copper_guards.py`'s mandatory `--max-mohm`.
- Thresholds are review triggers the project sets from its own history. This
  tool ships no defaults for them, because none have been calibrated against a
  corpus; inventing one here would be exactly the fake-threshold failure
  `../GUARDS.md` forbids.

Exit codes:
  0  audited (graded and passing) — only with the explicit OK verdict line —
     or `--report-only`, with the NOT-GRADED verdict line
  1  unevaluable — board missing/unloadable, no track copper, no usable pcbnew
     interpreter, no threshold and no --report-only, bad CLI value, worker
     timeout, or an exception
  2  a graded metric failed its threshold, or a threshold named a metric that
     could not be evaluated

Runs itself under KiCad's bundled interpreter when `pcbnew` is not importable.
Set `KICAD_PYTHON` to override discovery; a configured interpreter that cannot
`import pcbnew` is an unevaluable failure, not a silent fallback.

Usage:
  kicad_route_shape.py BOARD.kicad_pcb --report-only
  kicad_route_shape.py BOARD.kicad_pcb \\
      --layer-direction F.Cu=v,In1.Cu=h,B.Cu=v \\
      --max-vias-on-any-net 2 --max-full-stack-via-fraction 0.5 \\
      --max-short-segment-fraction 0.15 --min-direction-conformance 0.70
"""

import argparse
import glob
import json
import math
import os
import subprocess
import sys

_OK_LINE = "ROUTE-SHAPE-OK"
_NOT_GRADED_LINE = "ROUTE-SHAPE-REPORTED-NOT-GRADED"
_FAIL_LINE = "ROUTE-SHAPE-FAIL"
_UNEVALUABLE_LINE = "ROUTE-SHAPE-UNEVALUABLE"
_PROBE_MARKER = "PCBNEW-PROBE-OK"
_WORKER_ENV = "KICAD_ROUTE_SHAPE_WORKER"

# Copper running within this angle of an axis counts as on-axis. Segments
# further off (45-degree diagonals, arcs' chords) count towards neither axis
# and appear in the off-axis remainder.
_AXIS_TOLERANCE_DEG = 5.0


class Unevaluable(Exception):
    """Raised for every input the audit cannot grade. Never caught into OK."""


def _fail_unevaluable(message, json_out=None, board=None):
    if json_out:
        try:
            _write_json(json_out, board, "unevaluable", {"error": message})
        except (Unevaluable, OSError) as error:
            print(f"{_UNEVALUABLE_LINE}: {error}", file=sys.stderr)
    print(f"{_UNEVALUABLE_LINE}: {message}", file=sys.stderr)
    return 1


_REPORT_MARKER = "kicad_route_shape"


def _is_own_report(path):
    """True only if `path` is absent, or is a JSON document this tool wrote.

    The report writer replaces its target outright, so it must never be aimed
    at a file it does not own. `BOARD --json BOARD` destroyed the board under
    review (measured 2026-09-05, codex review of 0aefe5b): the pre-parse
    invalidation fired before argparse could reject anything, and the board
    became a two-line placeholder."""
    if not os.path.exists(path):
        return True
    try:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(document, dict) and document.get("tool") == _REPORT_MARKER


def _write_json(path, board, verdict, payload):
    """Atomically replace `path`. A crash leaves the pre-written placeholder,
    never a stale clean report. Refuses a target this tool does not own."""
    import tempfile
    if not _is_own_report(path):
        raise Unevaluable(
            f"--json {path} exists and is not a {_REPORT_MARKER} report; "
            "refusing to overwrite a file this audit does not own"
        )
    document = {
        "tool": _REPORT_MARKER,
        "verdict": verdict,
        "board": os.path.abspath(board) if board else None,
        "pid": os.getpid(),
        "metrics": payload,
    }
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".route-shape-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=2, sort_keys=True)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------
# measurement (runs under the pcbnew interpreter)
# --------------------------------------------------------------------------

def _percentile(sorted_values, fraction):
    if not sorted_values:
        raise Unevaluable("no segments to take a percentile of")
    index = min(len(sorted_values) - 1, int(fraction * len(sorted_values)))
    return sorted_values[index]


def measure(board_path, short_segment_mm, layer_directions):
    """Return the metrics dict. Raises Unevaluable on anything ungradeable."""
    import pcbnew  # bundled interpreter only

    if not os.path.isfile(board_path):
        raise Unevaluable(f"board not found: {board_path}")
    try:
        board = pcbnew.LoadBoard(board_path)
    except Exception as error:  # pcbnew raises bare Exception on parse failure
        raise Unevaluable(f"cannot load board: {error}")
    if board is None:
        raise Unevaluable(f"pcbnew returned no board for {board_path}")

    nm_per_mm = 1e6
    segments = []          # (length_mm, layer_name, angle_deg or None for arcs)
    arcs = 0
    per_layer = {}         # layer -> {"segments": n, "length_mm": x, "on_axis_mm": ...}
    via_spans = {}         # "F.Cu->B.Cu" -> count
    via_nets = {}
    net_has_copper = set()
    total_vias = 0

    for track in board.GetTracks():
        net_name = track.GetNetname() or "<no net>"
        if isinstance(track, pcbnew.PCB_VIA):
            total_vias += 1
            top, bottom = track.TopLayer(), track.BottomLayer()
            span = f"{board.GetLayerName(top)}->{board.GetLayerName(bottom)}"
            via_spans[span] = via_spans.get(span, 0) + 1
            via_nets[net_name] = via_nets.get(net_name, 0) + 1
            continue
        layer = board.GetLayerName(track.GetLayer())
        is_arc = isinstance(track, pcbnew.PCB_ARC)
        # GetLength() is arc-aware; hypot(start,end) would return the CHORD and
        # silently understate curved copper.
        length = track.GetLength() / nm_per_mm
        if length <= 0.0:
            continue  # zero-length artefact: no shape to attribute
        net_has_copper.add(net_name)
        if is_arc:
            arcs += 1
            angle = None          # curved copper has no single axis
        else:
            start, end = track.GetStart(), track.GetEnd()
            dx = (end.x - start.x) / nm_per_mm
            dy = (end.y - start.y) / nm_per_mm
            angle = math.degrees(math.atan2(abs(dy), abs(dx)))  # 0 = horizontal
        segments.append((length, layer, angle))
        bucket = per_layer.setdefault(
            layer, {"segments": 0, "length_mm": 0.0, "h_mm": 0.0, "v_mm": 0.0,
                    "arc_mm": 0.0}
        )
        bucket["segments"] += 1
        bucket["length_mm"] += length
        if angle is None:
            bucket["arc_mm"] += length
        elif angle <= _AXIS_TOLERANCE_DEG:
            bucket["h_mm"] += length
        elif angle >= 90.0 - _AXIS_TOLERANCE_DEG:
            bucket["v_mm"] += length

    if not segments:
        raise Unevaluable(
            "board carries no track copper — nothing to grade "
            "(an unrouted board is unevaluable, not clean)"
        )

    lengths = sorted(length for length, _, _ in segments)
    short = sum(1 for length in lengths if length < short_segment_mm)
    routed_nets = len(net_has_copper)

    copper_layers = [
        board.GetLayerName(layer_id)
        for layer_id in board.GetEnabledLayers().CuStack()
    ]
    outer = {copper_layers[0], copper_layers[-1]} if len(copper_layers) >= 2 else set()
    # On a 2-layer board every ordinary via spans the outer pair BY
    # CONSTRUCTION, so the fraction carries no information about layer
    # planning: report it as unevaluable rather than as a finding.
    full_stack = sum(
        count for span, count in via_spans.items()
        if set(span.split("->")) == outer
    ) if (outer and len(copper_layers) > 2) else None

    conformance = {}
    for layer, bucket in sorted(per_layer.items()):
        declared = layer_directions.get(layer)
        if declared is None:
            conformance[layer] = None  # unevaluable: no direction declared
            continue
        along = bucket["h_mm"] if declared == "h" else bucket["v_mm"]
        conformance[layer] = (
            along / bucket["length_mm"] if bucket["length_mm"] > 0 else None
        )

    return {
        "board": os.path.abspath(board_path),
        "arcs": arcs,
        "max_vias_on_a_net": max(via_nets.values()) if via_nets else 0,
        "max_via_net": max(via_nets, key=via_nets.get) if via_nets else None,
        "copper_layers": copper_layers,
        "routed_nets": routed_nets,
        "vias": total_vias,
        "vias_per_routed_net": total_vias / routed_nets if routed_nets else None,
        "via_layer_spans": dict(sorted(via_spans.items())),
        "full_stack_vias": full_stack,
        "full_stack_via_fraction": (
            full_stack / total_vias if full_stack is not None and total_vias else None
        ),
        "vias_by_net": dict(sorted(via_nets.items(), key=lambda kv: -kv[1])),
        "segments": len(segments),
        "copper_length_mm": round(sum(lengths), 2),
        "segment_length_mm": {
            "median": round(_percentile(lengths, 0.5), 3),
            "mean": round(sum(lengths) / len(lengths), 3),
            "p90": round(_percentile(lengths, 0.9), 3),
        },
        "short_segment_threshold_mm": short_segment_mm,
        "short_segments": short,
        "short_segment_fraction": short / len(lengths),
        "per_layer": {
            layer: {
                "segments": bucket["segments"],
                "length_mm": round(bucket["length_mm"], 2),
            }
            for layer, bucket in sorted(per_layer.items())
        },
        "direction_conformance": conformance,
        "declared_directions": dict(sorted(layer_directions.items())),
    }


# --------------------------------------------------------------------------
# grading
# --------------------------------------------------------------------------

def grade(metrics, args):
    """Return (findings, graded_count). A threshold whose metric is
    unevaluable is a finding, never a skip."""
    findings = []
    graded = 0

    def check(name, value, limit, worse):
        nonlocal graded
        if limit is None:
            return
        graded += 1
        if value is None:
            findings.append(
                f"{name}: UNEVALUABLE (threshold {limit} given, metric could "
                "not be computed from the inputs supplied)"
            )
            return
        if worse(value, limit):
            findings.append(f"{name}: {value:.3f} vs limit {limit}")

    # Graded on the per-net MAXIMUM, never the mean: a mean over "nets carrying
    # copper" is diluted by every via-free net, so adding ten short via-free
    # nets turned a FAIL into a PASS with the same two vias (measured
    # 2026-09-05). A maximum cannot be lowered by adding nets.
    check("max_vias_on_a_net", metrics["max_vias_on_a_net"],
          args.max_vias_on_any_net, lambda v, l: v > l)
    # Every fraction here is dilutable: adding compliant copper lowers it
    # without removing any offending geometry (codex review of 0aefe5b flipped
    # 1.0 FAIL -> 0.5 PASS with one added via, and again with one added
    # segment). The absolute companions below cannot be diluted that way, so a
    # gate that must survive a growing board pairs each fraction with its
    # count.
    check("full_stack_via_fraction", metrics["full_stack_via_fraction"],
          args.max_full_stack_via_fraction, lambda v, l: v > l)
    check("full_stack_vias", metrics["full_stack_vias"],
          args.max_full_stack_vias, lambda v, l: v > l)
    check("short_segment_fraction", metrics["short_segment_fraction"],
          args.max_short_segment_fraction, lambda v, l: v > l)
    check("short_segments", metrics["short_segments"],
          args.max_short_segments, lambda v, l: v > l)

    if args.min_direction_conformance is not None:
        declared = metrics["declared_directions"]
        if not declared:
            graded += 1
            findings.append(
                "direction_conformance: UNEVALUABLE (--min-direction-conformance "
                "given without --layer-direction; no layer has a declared "
                "preferred direction)"
            )
        else:
            # Grading only the declared layers let a board with noncompliant
            # copper on an undeclared layer PASS (codex review of 0aefe5b).
            # A layer carrying copper is part of the route whether or not the
            # caller remembered to declare it.
            for layer in sorted(metrics["per_layer"]):
                if layer in declared:
                    continue
                graded += 1
                findings.append(
                    f"direction_conformance[{layer}]: UNEVALUABLE (layer "
                    f"carries {metrics['per_layer'][layer]['length_mm']} mm of "
                    "copper but --layer-direction declares no direction for "
                    "it; declare it or route no copper on it)"
                )
            for layer in sorted(declared):
                value = metrics["direction_conformance"].get(layer)
                check(f"direction_conformance[{layer}]", value,
                      args.min_direction_conformance, lambda v, l: v < l)
    return findings, graded


def _parse_layer_directions(raw):
    if not raw:
        return {}
    directions = {}
    for entry in raw.split(","):
        entry = entry.strip()
        if not entry:
            continue
        if "=" not in entry:
            raise Unevaluable(
                f"--layer-direction entry {entry!r} is not LAYER=h|v"
            )
        layer, value = entry.split("=", 1)
        layer, value = layer.strip(), value.strip().lower()
        if not layer:
            raise Unevaluable("--layer-direction entry has an empty layer name")
        if value not in ("h", "v"):
            raise Unevaluable(
                f"--layer-direction {layer}={value!r}: direction must be h or v"
            )
        if layer in directions and directions[layer] != value:
            raise Unevaluable(
                f"--layer-direction declares {layer} as both "
                f"{directions[layer]!r} and {value!r}"
            )
        if layer in directions:
            raise Unevaluable(
                f"--layer-direction declares {layer} more than once"
            )
        directions[layer] = value
    if not directions:
        raise Unevaluable("--layer-direction was given but declares no layer")
    return directions


def _report(metrics):
    print(f"board:              {metrics['board']}")
    print(f"copper layers:      {', '.join(metrics['copper_layers'])}")
    print(f"routed nets:        {metrics['routed_nets']}")
    vpn = metrics["vias_per_routed_net"]
    print(f"vias:               {metrics['vias']}"
          f"  ({'n/a' if vpn is None else f'{vpn:.2f}'} per routed net)")
    for span, count in metrics["via_layer_spans"].items():
        print(f"  span {span}: {count}")
    fraction = metrics["full_stack_via_fraction"]
    print("full-stack vias:    "
          + (("unevaluable (2-layer board: every ordinary via spans the outer "
              "pair by construction)"
              if len(metrics["copper_layers"]) <= 2
              else "unevaluable (fewer than two copper layers)")
             if fraction is None
             else f"{metrics['full_stack_vias']} ({fraction:.0%})"))
    distribution = metrics["segment_length_mm"]
    print(f"segments:           {metrics['segments']}"
          f" over {metrics['copper_length_mm']} mm")
    print(f"  length mm:        median {distribution['median']}"
          f"  mean {distribution['mean']}  p90 {distribution['p90']}")
    print(f"  below {metrics['short_segment_threshold_mm']} mm:"
          f"     {metrics['short_segment_fraction']:.0%}")
    for layer, bucket in metrics["per_layer"].items():
        value = metrics["direction_conformance"].get(layer)
        declared = metrics["declared_directions"].get(layer)
        shape = ("conformance unevaluable (no --layer-direction)"
                 if declared is None else
                 "conformance unevaluable (no copper length)" if value is None
                 else f"conformance {value:.0%} along {declared}")
        print(f"  {layer:<10} {bucket['segments']:>5} seg"
              f"  {bucket['length_mm']:>8.1f} mm  {shape}")
    top = list(metrics["vias_by_net"].items())[:8]
    if top:
        print("worst nets by via count: "
              + ", ".join(f"{net} {count}" for net, count in top))


# --------------------------------------------------------------------------
# interpreter plumbing
# --------------------------------------------------------------------------

def _interpreter_has_pcbnew(interpreter):
    probe = "import pcbnew, sys; sys.stdout.write('PCBNEW-' + 'PROBE-OK')"
    try:
        proc = subprocess.run(
            [interpreter, "-c", probe], capture_output=True,
            encoding="utf-8", errors="replace", timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return proc.returncode == 0 and (proc.stdout or "").strip() == _PROBE_MARKER


def _find_kicad_python():
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


def _run_worker(interpreter, argv, timeout):
    cmd = [interpreter, "-u", os.path.abspath(__file__)] + list(argv)
    env = dict(os.environ, **{_WORKER_ENV: "1"})
    try:
        proc = subprocess.run(
            cmd, env=env, capture_output=True, encoding="utf-8",
            errors="replace", timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return _fail_unevaluable(f"worker exceeded --timeout {timeout}s")
    if proc.stdout:
        sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    # Trust exit 0 only when the worker actually printed a verdict line.
    if proc.returncode == 0 and not (
        _OK_LINE in (proc.stdout or "")
        or _NOT_GRADED_LINE in (proc.stdout or "")
    ):
        return _fail_unevaluable(
            "worker exited 0 without a verdict line (treated as unevaluable)"
        )
    return proc.returncode


def build_parser():
    parser = argparse.ArgumentParser(
        description="Routing-shape audit for a saved .kicad_pcb",
    )
    parser.add_argument("board", help="path to the saved .kicad_pcb")
    parser.add_argument(
        "--layer-direction", default="",
        help="comma-separated LAYER=h|v declaring each routing layer's "
             "preferred direction, e.g. 'F.Cu=v,In1.Cu=h'. Layers not named "
             "have no declared direction and their conformance is unevaluable",
    )
    parser.add_argument("--short-segment-mm", type=float, default=0.2,
                        help="segments shorter than this count as short "
                             "(default 0.2)")
    parser.add_argument("--max-vias-on-any-net", type=float,
                        help="fail if any single net carries more vias than "
                             "this; the per-net maximum is used because the "
                             "mean is diluted by via-free nets")
    parser.add_argument("--max-full-stack-via-fraction", type=float)
    parser.add_argument("--max-full-stack-vias", type=float,
                        help="absolute companion to the fraction above; a "
                             "fraction alone is diluted by added copper")
    parser.add_argument("--max-short-segment-fraction", type=float)
    parser.add_argument("--max-short-segments", type=float,
                        help="absolute companion to the fraction above")
    parser.add_argument("--min-direction-conformance", type=float)
    parser.add_argument("--report-only", action="store_true",
                        help="print metrics and exit 0 without grading; NOT a "
                             "pass and never valid as a gate")
    parser.add_argument("--json", dest="json_out",
                        help="write the metrics and verdict here atomically")
    parser.add_argument("--timeout", type=float, default=600.0,
                        help="worker timeout in seconds (default 600)")
    return parser


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    # Invalidate any pre-existing report BEFORE argparse can exit: a bad
    # argument used to leave a previous "pass" artefact standing (measured
    # 2026-09-05). Best effort by design -- a malformed command line still
    # gets its stale report replaced.
    target = None
    for index, token in enumerate(argv):
        # argparse keeps the LAST occurrence, so invalidate that one; stopping
        # at the first left the effective report standing (codex review of
        # 0aefe5b).
        if token == "--json" and index + 1 < len(argv):
            target = argv[index + 1]
        elif token.startswith("--json="):
            target = token.split("=", 1)[1]
    if target:
        if not _is_own_report(target):
            print(f"{_UNEVALUABLE_LINE}: --json {target} exists and is not a "
                  f"{_REPORT_MARKER} report; refusing to overwrite a file this "
                  "audit does not own", file=sys.stderr)
            return 1
        try:
            _write_json(target, None, "unevaluable",
                        {"error": "audit did not complete (pre-parse)"})
        except (Unevaluable, OSError):
            pass
    parser = build_parser()
    args = parser.parse_args(argv)

    thresholds = [
        args.max_vias_on_any_net, args.max_full_stack_via_fraction,
        args.max_full_stack_vias, args.max_short_segment_fraction,
        args.max_short_segments, args.min_direction_conformance,
    ]
    given = [value for value in thresholds if value is not None]
    if args.report_only and given:
        return _fail_unevaluable(
            "--report-only cannot be combined with a threshold: a run is "
            "either graded or not", args.json_out, args.board)
    if not args.report_only and not given:
        return _fail_unevaluable(
            "no threshold given and --report-only not set: an ungraded gating "
            "run is unevaluable, not a pass", args.json_out, args.board)
    if args.short_segment_mm <= 0 or not math.isfinite(args.short_segment_mm):
        return _fail_unevaluable(
            f"--short-segment-mm must be finite and positive, got "
            f"{args.short_segment_mm}", args.json_out, args.board)
    for name, value, lo, hi, integral in (
        ("--max-vias-on-any-net", args.max_vias_on_any_net, 0.0, None, True),
        ("--max-full-stack-via-fraction", args.max_full_stack_via_fraction, 0.0, 1.0, False),
        ("--max-full-stack-vias", args.max_full_stack_vias, 0.0, None, True),
        ("--max-short-segment-fraction", args.max_short_segment_fraction, 0.0, 1.0, False),
        ("--max-short-segments", args.max_short_segments, 0.0, None, True),
        ("--min-direction-conformance", args.min_direction_conformance, 0.0, 1.0, False),
    ):
        if value is None:
            continue
        if not math.isfinite(value):
            return _fail_unevaluable(
                f"{name}={value} is not finite", args.json_out, args.board)
        # A threshold outside its metric's range is a configuration error
        # either way: above the range it can never fail, below it can never
        # pass. Neither is a gate, and neither is a verdict about the board.
        if value < lo or (hi is not None and value > hi):
            return _fail_unevaluable(
                f"{name}={value} is outside its metric's range "
                f"[{lo}, {'inf' if hi is None else hi}]; a threshold that can "
                "never fail, or never pass, is a configuration error, not a "
                "verdict about the board",
                args.json_out, args.board)
        # These metrics are counts. `--max-vias-on-any-net 2.9` silently means
        # 2, which reads as a limit the board never had (codex review of
        # 0aefe5b).
        if integral and value != int(value):
            return _fail_unevaluable(
                f"{name}={value} is not an integer, but the metric it grades "
                "is a count; give a whole number",
                args.json_out, args.board)

    try:
        layer_directions = _parse_layer_directions(args.layer_direction)
    except Unevaluable as error:
        return _fail_unevaluable(str(error), args.json_out, args.board)

    if args.json_out:
        # Placeholder first: a crash below must not leave a clean report.
        _write_json(args.json_out, args.board, "unevaluable",
                    {"error": "audit did not complete"})

    try:
        import pcbnew  # noqa: F401
    except ImportError:
        if os.environ.get(_WORKER_ENV):
            return _fail_unevaluable(
                "re-executed interpreter still cannot import pcbnew",
                args.json_out, args.board)
        interpreter, error = _find_kicad_python()
        if not interpreter:
            return _fail_unevaluable(error, args.json_out, args.board)
        return _run_worker(interpreter, argv, args.timeout)

    try:
        metrics = measure(args.board, args.short_segment_mm, layer_directions)
    except Unevaluable as error:
        return _fail_unevaluable(str(error), args.json_out, args.board)
    except Exception as error:  # never let an exception read as clean
        return _fail_unevaluable(
            f"{type(error).__name__}: {error}", args.json_out, args.board)

    _report(metrics)

    if args.report_only:
        if args.json_out:
            _write_json(args.json_out, args.board, "reported", metrics)
        print(f"{_NOT_GRADED_LINE}: metrics only, no threshold applied")
        return 0

    findings, graded = grade(metrics, args)
    if not graded:
        return _fail_unevaluable(
            "no metric was graded despite a threshold being supplied",
            args.json_out, args.board)
    if args.json_out:
        _write_json(args.json_out, args.board,
                    "fail" if findings else "pass",
                    dict(metrics, findings=findings, graded=graded))
    for finding in findings:
        print(f"ROUTE-SHAPE: {finding}")
    if findings:
        print(f"{_FAIL_LINE}: {len(findings)} of {graded} graded metrics failed")
        return 2
    print(f"{_OK_LINE}: {graded} graded metrics within their thresholds")
    return 0


if __name__ == "__main__":
    sys.exit(main())
