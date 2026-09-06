#!/usr/bin/env python3
"""Copper-quality guards that KiCad DRC cannot express.

Two checks, both run on a SAVED, FILLED board file (fills must be current --
regenerate/refill and save before trusting results):

  vias        Per-via annular contact quality. DRC's connectivity (and
              via_dangling) is binary: a via touching a 0.05 mm sliver of a
              zone fill counts as connected. This check measures the fraction
              of each via's annular ring covered by same-net copper per layer
              and fails vias that rely on partial zone-edge contact.
              (Measured failure this closes: kimi3-r2 GND_BR pour-stitch vias
              half inside a pad's fill carve, 2026-09-01.)

  resistance  Effective DC resistance between two pads of one net, from a
              rasterized Laplacian solve over that net's real copper
              (fills + tracks + pads + vias). Catches constrictions DRC
              cannot see: necked pours, single-sliver stitches, one-via
              bottlenecks. Grid default 0.2 mm: neck widths below ~2 cells
              quantize coarsely -- the via-sliver case is check 1's job.

Fail-closed by design: an unfilled board, a missing pad, a terminal landing
on no copper, a disconnected pair, zero gradeable subjects, or a
non-finite/non-positive parameter is an ERROR / infinite resistance,
never a pass.

Scope and model limits (read before trusting a number):
  - 2-LAYER BOARDS ONLY (F.Cu/B.Cu). A board with enabled inner copper
    layers is refused, not silently truncated.
  - Net names are matched EXACTLY as stored in the board file (including
    a leading '/'); an unknown net errors with candidate suggestions.
  - Pad shapes rasterize as their bounding rect/oval approximation
    (roundrect/trapezoid/custom slightly OVERSTATE copper); routed arcs
    are polylined; copper graphics are ignored. Layer-to-layer
    transitions use a fixed lumped resistance (--via-mohm, default
    0.2 mOhm per via/thru-pad site), not barrel geometry.
  - --oz applies to every layer; take it from the board's real stackup.
  - Fill freshness cannot be proven from the file alone: refill and save
    (kicad-cli pcb drc --refill-zones --save-board) immediately before
    running. A net whose declared zone has no filled_polygon is refused.

Calibration record:
  - vias: kimi3-r2 GND_BR pour-stitch sliver vias (half inside a pad's
    fill carve) FAIL at contact fractions 0.02/0.07 vs the 0.6 default
    threshold; qwen3p8max 66-via known-good board grades clean (2026-09).
    The spoke criterion measures a strip that crosses the WHOLE annulus,
    checked at five radii from the drill edge to the pad edge. Constructed
    calibration in test_copper_guards_geometry.py: a 0.010 mm radially thin
    sector covering 0.5% of the ring reads 0.0 mm (it read 0.35 mm and
    PASSed under the earlier single-circle measurement, codex review of
    7b00165); two disjoint slivers at different radii read 0.0 mm; a real
    0.5 mm zone thermal spoke reads 0.44 mm and still passes the 0.3 mm
    default. The 0.44-vs-0.50 gap is the angular footprint of a straight
    strip narrowing with radius; conservative, in the safe direction.
Dependencies: the MEASUREMENT paths need numpy and shapely, and
`resistance` additionally needs scipy; the CLI boundary does not. An absent
measurement stack exits 1 FAIL-CLOSED at the point of use, never a pass and
never an import traceback (codex review of 7b00165).

  - resistance: gemini3p8flash incident board /AC_N Q1.3<->J_AC1.2
    measured 185.9 mOhm at 2 oz (the 0.4 mm autorouted neck), /GND_PWR
    plane 0.80 mOhm; grid convergence 0.2->0.05 mm moved a synthetic
    100x0.4 mm trace 0.17% and the /AC_N path 1.37% (2026-09-04).
"""

import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time

# The measurement code needs numpy/shapely (and scipy for `resistance`), but
# the CLI boundary must not: a usage error that dies in an import never
# reaches GuardArgumentParser and so escapes the exit contract, and the
# repository's default test invocation is a stdlib interpreter. Import
# lazily and refuse explicitly at the point of use.
try:
    import numpy as np
    import shapely
    import shapely.affinity
    from shapely.geometry import Point, LineString, Polygon, box
    from shapely.ops import unary_union
    _GEOMETRY_IMPORT_ERROR = None
except ImportError as _import_error:      # pragma: no cover - env dependent
    np = shapely = None
    Point = LineString = Polygon = box = unary_union = None
    _GEOMETRY_IMPORT_ERROR = _import_error

GEOMETRY_REQUIREMENT = "numpy and shapely (plus scipy for `resistance`)"


def require_geometry():
    """Fail closed when the measurement stack is absent.

    An absent dependency is unevaluable, never a pass and never a crash
    traceback that a caller might read as a tool bug rather than a verdict.
    """
    if _GEOMETRY_IMPORT_ERROR is not None:
        raise SystemExit(
            f"FAIL-CLOSED: this check needs {GEOMETRY_REQUIREMENT}, and "
            f"importing it failed ({_GEOMETRY_IMPORT_ERROR}). An absent "
            f"measurement stack is unevaluable, not a clean board")


# --------------------------------------------------------- report ownership

_REPORT_MARKER = "copper_guards"


def _is_own_report(path):
    """True only if `path` is absent, or is a JSON document this tool wrote.

    The writer replaces its target outright, so it must never be aimed at a
    file it does not own. Measured 2026-09-07: `vias BOARD --json BOARD`
    exited 0 and replaced a 343,731-byte board with 6,687 bytes of JSON.
    `kicad_route_shape.py` already held this contract; this guard did not.
    """
    if not os.path.exists(path):
        return True
    try:
        with open(path, encoding="utf-8") as stream:
            document = json.load(stream)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(document, dict) and document.get("tool") == _REPORT_MARKER


def _board_digest(board):
    """SHA-256 of the graded bytes, so a report cannot outlive its board."""
    if not board or not os.path.isfile(board):
        return None
    digest = hashlib.sha256()
    with open(board, "rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_report(path, check, board, verdict, payload):
    """Atomically replace `path`. A crash leaves the pre-written placeholder,
    never a stale clean report. Refuses a target this tool does not own."""
    if not _is_own_report(path):
        raise SystemExit(
            f"FAIL-CLOSED: --json {path} exists and is not a "
            f"{_REPORT_MARKER} report; refusing to overwrite a file this "
            f"guard does not own")
    document = {
        "tool": _REPORT_MARKER,
        "check": check,
        "verdict": verdict,
        "board": os.path.abspath(board) if board else None,
        "board_sha256": _board_digest(board),
        "pid": os.getpid(),
        "rows": payload,
    }
    directory = os.path.dirname(os.path.abspath(path)) or "."
    handle, temp = tempfile.mkstemp(dir=directory, prefix=".copper-guards-")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(document, stream, indent=1, sort_keys=True)
        os.replace(temp, path)
    except BaseException:
        try:
            os.unlink(temp)
        except OSError:
            pass
        raise

CU_LAYERS = ("F.Cu", "B.Cu")


def norm_net(name):
    # Exact match only: KiCad net names are compared as stored in the
    # board file. Stripping '/' conflated distinct names ("/GND" vs "GND").
    return name


def require_net_known(board, net):
    if net in board["nets"]:
        return
    cands = sorted(n for n in board["nets"]
                   if n.lstrip("/").lower() == net.lstrip("/").lower())
    hint = f"; did you mean {cands}?" if cands else ""
    raise SystemExit(
        f"FAIL-CLOSED: net {net!r} does not exist on this board{hint} "
        f"(names match exactly, including any leading '/')")


def arc_polyline(sx, sy, mx, my, ex, ey, n=16):
    """Approximate a KiCad 3-point arc as a polyline through its circle."""
    ax, ay, bx, by, cx, cy = sx, sy, mx, my, ex, ey
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-9:
        return LineString([(sx, sy), (ex, ey)])
    ux = ((ax * ax + ay * ay) * (by - cy) + (bx * bx + by * by) * (cy - ay)
          + (cx * cx + cy * cy) * (ay - by)) / d
    uy = ((ax * ax + ay * ay) * (cx - bx) + (bx * bx + by * by) * (ax - cx)
          + (cx * cx + cy * cy) * (bx - ax)) / d
    a0 = math.atan2(ay - uy, ax - ux)
    am = math.atan2(by - uy, bx - ux)
    a1 = math.atan2(ey - uy, ex - ux)
    # unwrap so the sweep passes through the mid-point
    def unwrap(frm, to):
        while to < frm:
            to += 2 * math.pi
        return to
    if unwrap(a0, am) <= unwrap(a0, a1):
        sweep = unwrap(a0, a1) - a0
        sgn = 1.0
    else:
        sweep = unwrap(a1, a0) - a1
        sgn = -1.0
    r = math.hypot(ax - ux, ay - uy)
    pts = [(ux + r * math.cos(a0 + sgn * sweep * i / n),
            uy + r * math.sin(a0 + sgn * sweep * i / n))
           for i in range(n + 1)]
    return LineString(pts)


# ---------------------------------------------------------------- parsing

def sexpr_blocks(text, tag):
    """Yield balanced top-of-tag S-expression blocks '(tag ...)'."""
    needle = "(" + tag
    i = 0
    while True:
        j = text.find(needle, i)
        if j < 0:
            return
        nxt = text[j + len(needle)]
        if nxt not in " \t\r\n(":  # avoid matching e.g. '(padstack' for 'pad'
            i = j + 1
            continue
        depth = 0
        k = j
        while k < len(text):
            c = text[k]
            if c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        yield text[j:k + 1]
        i = k + 1


NET_RE = re.compile(r'\(net\s+(?:\d+\s+)?"([^"]*)"\s*\)')
AT_RE = re.compile(r'\(at\s+([-\d.]+)\s+([-\d.]+)(?:\s+([-\d.]+))?\s*\)')


def _pad_shape(px, py, angle, shape, sx, sy):
    """Absolute-position shapely geometry for a pad's copper."""
    if shape == "circle":
        return Point(px, py).buffer(sx / 2, quad_segs=16)
    if shape == "oval":
        if sx >= sy:
            half = (sx - sy) / 2
            line = LineString([(-half, 0), (half, 0)])
            g = line.buffer(sy / 2, quad_segs=16)
        else:
            half = (sy - sx) / 2
            line = LineString([(0, -half), (0, half)])
            g = line.buffer(sx / 2, quad_segs=16)
    else:  # rect, roundrect, trapezoid, custom -> rectangle approximation
        g = box(-sx / 2, -sy / 2, sx / 2, sy / 2)
    if angle:
        # pad (at) angles are stored as absolute board angles (KiCad folds
        # the footprint rotation in); board y axis points down, so a CCW
        # library angle appears as -angle in shapely's y-up convention.
        g = shapely.affinity.rotate(g, -angle, origin=(0, 0))
    return shapely.affinity.translate(g, px, py)


def parse_board(path):
    text = open(path).read()
    board = {
        "pads": [],      # ref, pad, net, x, y, layers(set), geom, thru
        "segments": [],  # net, layer, geom(LineString), width
        "vias": [],      # net, x, y, size, drill
        "zone_fills": {},  # (net, layer) -> [Polygon]
        "zones_declared": set(),  # (net, layer)
        "any_fill": False,
        "nets": set(),
    }

    # 2-layer assertion: refuse boards with enabled inner copper layers
    # rather than silently grading half the stackup.
    for layer_table in sexpr_blocks(text, "layers"):
        inner = sorted(set(re.findall(r'"(In\d+\.Cu)"', layer_table)))
        if inner:
            raise SystemExit(
                f"FAIL-CLOSED: board enables inner copper layers {inner} - "
                f"this guard models F.Cu/B.Cu only and would miscompute a "
                f"multilayer board")
        break  # first (layers ...) block is the board's layer table

    board["nets"] = set(re.findall(r'\(net\s+\d+\s+"([^"]*)"\)', text)) | \
        set(re.findall(r'\(net\s+"([^"]*)"\)', text))
    board["nets"].discard("")

    for fp in sexpr_blocks(text, "footprint"):
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]*)"', fp)
        at_m = AT_RE.search(fp)
        if not (ref_m and at_m):
            continue
        ref = ref_m.group(1)
        fx, fy = float(at_m.group(1)), float(at_m.group(2))
        frot = math.radians(float(at_m.group(3) or 0))
        for pad in sexpr_blocks(fp, "pad"):
            head = re.match(
                r'\(pad\s+"([^"]*)"\s+(\S+)\s+(\S+)', pad)
            at = AT_RE.search(pad)
            size = re.search(r'\(size\s+([\d.]+)\s+([\d.]+)\)', pad)
            if not (head and at and size):
                continue
            name, kind, shape = head.groups()
            px, py = float(at.group(1)), float(at.group(2))
            pang = float(at.group(3) or 0)
            ax = fx + px * math.cos(frot) + py * math.sin(frot)
            ay = fy - px * math.sin(frot) + py * math.cos(frot)
            layers_m = re.search(r'\(layers\s+([^)]*)\)', pad)
            ltxt = layers_m.group(1) if layers_m else ""
            layers = set()
            for L in CU_LAYERS:
                if L in ltxt or "*.Cu" in ltxt:
                    layers.add(L)
            net_m = NET_RE.search(pad)
            geom = _pad_shape(ax, ay, pang,
                              shape, float(size.group(1)), float(size.group(2)))
            board["pads"].append({
                "ref": ref, "pad": name,
                "net": norm_net(net_m.group(1)) if net_m else None,
                "x": ax, "y": ay, "layers": layers, "geom": geom,
                "thru": kind == "thru_hole",
            })

    for seg in sexpr_blocks(text, "segment"):
        m = re.search(
            r'\(start\s+([-\d.]+)\s+([-\d.]+)\)\s*\(end\s+([-\d.]+)\s+([-\d.]+)\)'
            r'\s*\(width\s+([\d.]+)\)\s*\(layer\s+"([^"]+)"\)', seg)
        net_m = NET_RE.search(seg)
        if not (m and net_m):
            continue
        x1, y1, x2, y2, w = map(float, m.groups()[:5])
        board["segments"].append({
            "net": norm_net(net_m.group(1)), "layer": m.group(6),
            "geom": LineString([(x1, y1), (x2, y2)]), "width": w,
        })

    for arc in sexpr_blocks(text, "arc"):
        m = re.search(
            r'\(start\s+([-\d.]+)\s+([-\d.]+)\)\s*\(mid\s+([-\d.]+)\s+([-\d.]+)\)'
            r'\s*\(end\s+([-\d.]+)\s+([-\d.]+)\)'
            r'\s*\(width\s+([\d.]+)\)\s*\(layer\s+"([^"]+)"\)', arc)
        net_m = NET_RE.search(arc)
        if not (m and net_m):
            continue
        sx, sy, mx, my, ex, ey, w = map(float, m.groups()[:7])
        board["segments"].append({
            "net": norm_net(net_m.group(1)), "layer": m.group(8),
            "geom": arc_polyline(sx, sy, mx, my, ex, ey), "width": w,
        })

    for via in sexpr_blocks(text, "via"):
        m = re.search(
            r'\(at\s+([-\d.]+)\s+([-\d.]+)\)\s*\(size\s+([\d.]+)\)'
            r'\s*\(drill\s+([\d.]+)\)', via)
        net_m = NET_RE.search(via)
        if not (m and net_m):
            continue
        board["vias"].append({
            "net": norm_net(net_m.group(1)),
            "x": float(m.group(1)), "y": float(m.group(2)),
            "size": float(m.group(3)), "drill": float(m.group(4)),
        })

    for zone in sexpr_blocks(text, "zone"):
        net_m = NET_RE.search(zone)
        if not net_m:
            continue
        net = norm_net(net_m.group(1))
        zl = re.search(r'\(layers?\s+([^)]*)\)', zone)
        for L in CU_LAYERS:
            if zl and (L in zl.group(1) or "*.Cu" in zl.group(1)):
                board["zones_declared"].add((net, L))
        for fpoly in sexpr_blocks(zone, "filled_polygon"):
            lay_m = re.search(r'\(layer\s+"([^"]+)"\)', fpoly)
            pts = [(float(a), float(b)) for a, b in
                   re.findall(r'\(xy\s+([-\d.]+)\s+([-\d.]+)\)', fpoly)]
            if not (lay_m and len(pts) >= 3):
                continue
            poly = Polygon(pts)
            if not poly.is_valid:
                poly = poly.buffer(0)
            board["zone_fills"].setdefault(
                (net, lay_m.group(1)), []).append(poly)
            board["any_fill"] = True

    if not (board["pads"] or board["segments"] or board["vias"]
            or board["zones_declared"]):
        raise SystemExit(
            f"FAIL-CLOSED: {path!r} parsed to no pads, tracks, vias or "
            f"zones - not a usable board file; an empty parse is never "
            f"a clean result")

    return board


# ------------------------------------------------------------ check: vias

_BRIDGE_ANGLES = 720          # 0.5 deg; 0.003 mm of arc at r = 0.35 mm
_BRIDGE_RADII = 5             # inner edge, outer edge and three between


def annulus_bridge_mm(cover_u, cx, cy, r_drill, r_pad):
    """Width of the widest copper strip that crosses the WHOLE annulus.

    Measured as an angular interval that is covered at every sampled radius
    from the drill edge to the pad edge, converted to arc length at the mid
    radius.

    The previous implementation intersected copper with a single
    mid-annulus circle and took the resulting arc length. That is not a
    strip width: a radially thin sliver hugging the mid radius produces a
    long arc while bridging nothing. Measured 2026-09-07 (codex review of
    7b00165): a 0.010 mm-wide annular sector covering 0.5 % of the ring
    passed as `spoke 0.35mm`, which is precisely the sliver this check
    exists to catch. Requiring the same angular interval at every radius
    fails that sector at the drill and pad edges while still passing a real
    thermal-relief spoke, which is the representation-independence the
    criterion was introduced for.
    """
    if cover_u is None or r_pad <= r_drill:
        return 0.0
    angles = np.linspace(0.0, 2.0 * math.pi, _BRIDGE_ANGLES, endpoint=False)
    cos_a, sin_a = np.cos(angles), np.sin(angles)
    covered = np.ones(_BRIDGE_ANGLES, dtype=bool)
    for radius in np.linspace(r_drill, r_pad, _BRIDGE_RADII):
        xs, ys = cx + radius * cos_a, cy + radius * sin_a
        try:
            here = shapely.intersects_xy(cover_u, xs, ys)
        except AttributeError:          # pragma: no cover - shapely < 2.0
            here = np.array([cover_u.intersects(Point(x, y))
                             for x, y in zip(xs, ys)])
        covered &= here
        if not covered.any():
            return 0.0
    if covered.all():
        run = _BRIDGE_ANGLES
    else:
        # longest contiguous run, wrapping around 0 rad
        doubled = np.concatenate([covered, covered])
        run = best = 0
        for flag in doubled:
            run = run + 1 if flag else 0
            best = max(best, run)
        run = min(best, _BRIDGE_ANGLES)
    r_mid = (r_drill + r_pad) / 2.0
    return run / _BRIDGE_ANGLES * 2.0 * math.pi * r_mid


def check_vias(board, nets=None, min_zone_frac=0.6, min_contact_mm=0.3,
               verbose=False):
    if board["zones_declared"] and not board["any_fill"]:
        raise SystemExit(
            "FAIL-CLOSED: board declares zones but contains no filled_polygon "
            "data - refill and save before running this check.")
    if nets:
        for n in nets:
            require_net_known(board, n)
    graded_nets = nets if nets else {v["net"] for v in board["vias"]}
    for n in graded_nets:
        for L in CU_LAYERS:
            if (n, L) in board["zones_declared"] and \
                    not board["zone_fills"].get((n, L)):
                raise SystemExit(
                    f"FAIL-CLOSED: net {n!r} declares a zone on {L} but "
                    f"has no filled_polygon there - refill and save before "
                    f"grading this net's vias")
    findings = []
    rows = []
    for v in board["vias"]:
        if nets and v["net"] not in nets:
            continue
        center = Point(v["x"], v["y"])
        r_pad, r_drill = v["size"] / 2, v["drill"] / 2
        ring = center.buffer(r_pad, quad_segs=32).difference(
            center.buffer(r_drill, quad_segs=32))
        for layer in CU_LAYERS:
            zone_polys = board["zone_fills"].get((v["net"], layer), [])
            # pads count via their real annular-ring coverage, never by
            # merely containing the via center (a sub-drill-size pad
            # "contains" the center with zero physical contact)
            pad_polys = [p["geom"] for p in board["pads"]
                         if p["net"] == v["net"] and layer in p["layers"]]
            cover_u = unary_union(zone_polys + pad_polys) \
                if (zone_polys or pad_polys) else None
            cover_frac = (ring.intersection(cover_u).area / ring.area
                          if cover_u is not None else 0.0)
            # widest contiguous copper strip crossing the WHOLE annulus:
            # a zone thermal-relief spoke is electrically a track and must
            # not fail for being encoded as fill (representation
            # independence) - grade its width like a track's. A strip that
            # does not reach both the drill edge and the pad edge is a
            # sliver, not a spoke.
            contact_w = annulus_bridge_mm(cover_u, v["x"], v["y"],
                                          r_drill, r_pad)
            track_conn = any(
                s["net"] == v["net"] and s["layer"] == layer
                and s["geom"].distance(center) <= s["width"] / 2 + 1e-6
                for s in board["segments"])
            zone_here = (v["net"], layer) in board["zones_declared"]
            status = "PASS"
            if track_conn:
                why = "track"
            elif not zone_here and not pad_polys and cover_frac == 0.0:
                # no zone or pad for this net on this layer and no track:
                # nothing to grade here - plain connectivity is DRC's job
                why = "no-copper-layer"
            elif cover_frac >= min_zone_frac:
                why = f"contact {cover_frac:.2f}"
            elif contact_w >= min_contact_mm:
                why = f"spoke {contact_w:.2f}mm"
            else:
                status = "FAIL"
                why = (f"annular contact {cover_frac:.2f} < "
                       f"{min_zone_frac:.2f} and widest strip "
                       f"{contact_w:.2f} < {min_contact_mm:.2f} mm, "
                       f"no track through {layer}")
            row = {
                "net": v["net"], "x": v["x"], "y": v["y"], "layer": layer,
                "zone_frac": round(cover_frac, 3), "track": track_conn,
                "status": status, "why": why,
            }
            rows.append(row)
            if status == "FAIL":
                findings.append(row)
            if verbose or status == "FAIL":
                print(f'{status}  via {v["net"]} ({v["x"]},{v["y"]}) '
                      f'{layer}: {why}')
    if not rows:
        scope = f" on net(s) {sorted(nets)}" if nets else ""
        raise SystemExit(
            f"UNEVALUABLE: zero vias to grade{scope} - zero candidates is "
            f"never a PASS; check the net filter and the board")
    return findings, rows


# ------------------------------------------------------ check: resistance

def rasterize_net(board, net, grid):
    """Boolean copper masks per layer for one net, plus the grid transform."""
    require_net_known(board, net)
    for L in CU_LAYERS:
        if (net, L) in board["zones_declared"] and \
                not board["zone_fills"].get((net, L)):
            raise SystemExit(
                f"FAIL-CLOSED: net {net!r} declares a zone on {L} but has "
                f"no filled_polygon there - refill and save before grading "
                f"this net (a stale/empty fill would be graded as absent "
                f"copper)")
    geoms = {L: [] for L in CU_LAYERS}
    for L, polys in ((k[1], v) for k, v in board["zone_fills"].items()
                     if k[0] == net):
        geoms[L].extend(polys)
    for s in board["segments"]:
        if s["net"] == net:
            geoms[s["layer"]].append(s["geom"].buffer(s["width"] / 2,
                                                      quad_segs=8))
    for p in board["pads"]:
        if p["net"] == net:
            for L in p["layers"]:
                geoms[L].append(p["geom"])
    for v in board["vias"]:
        if v["net"] == net:
            d = Point(v["x"], v["y"]).buffer(v["size"] / 2, quad_segs=8)
            for L in CU_LAYERS:
                geoms[L].append(d)
    allg = [g for L in CU_LAYERS for g in geoms[L]]
    if not allg:
        raise SystemExit(f"FAIL-CLOSED: no copper found for net {net!r}")
    minx, miny, maxx, maxy = unary_union(
        [box(*g.bounds) for g in allg]).bounds
    minx -= grid; miny -= grid; maxx += grid; maxy += grid
    nx = int(math.ceil((maxx - minx) / grid))
    ny = int(math.ceil((maxy - miny) / grid))
    xs = minx + (np.arange(nx) + 0.5) * grid
    ys = miny + (np.arange(ny) + 0.5) * grid
    masks = {}
    for L in CU_LAYERS:
        mask = np.zeros((ny, nx), dtype=bool)
        for g in geoms[L]:
            gminx, gminy, gmaxx, gmaxy = g.bounds
            ix0 = max(0, int((gminx - minx) / grid) - 1)
            ix1 = min(nx, int((gmaxx - minx) / grid) + 2)
            iy0 = max(0, int((gminy - miny) / grid) - 1)
            iy1 = min(ny, int((gmaxy - miny) / grid) + 2)
            if ix1 <= ix0 or iy1 <= iy0:
                continue
            gx, gy = np.meshgrid(xs[ix0:ix1], ys[iy0:iy1])
            pts = shapely.points(gx.ravel(), gy.ravel())
            hit = shapely.intersects(g, pts).reshape(gy.shape)
            mask[iy0:iy1, ix0:ix1] |= hit
        masks[L] = mask
    return masks, (minx, miny, grid, nx, ny, xs, ys)


def check_resistance(board, net, pair_a, pair_b, grid=0.2, oz=2.0,
                     via_mohm=0.2):
    from scipy.sparse import coo_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse.linalg import spsolve

    if board["zones_declared"] and not board["any_fill"]:
        raise SystemExit("FAIL-CLOSED: zones declared but board is unfilled.")

    def find_pads(spec):
        """All pads matching REF.PAD - a footprint may carry several pads
        with the same number (e.g. a fuse holder's two '2' lands); they are
        one electrical terminal and are all tied into the supernode."""
        ref, _, num = spec.partition(".")
        matches = [p for p in board["pads"]
                   if p["ref"] == ref and p["pad"] == num]
        if not matches:
            raise SystemExit(f"FAIL-CLOSED: pad {spec} not found")
        bad = [p for p in matches if norm_net(p["net"]) != net]
        if bad:
            raise SystemExit(
                f"FAIL-CLOSED: {spec} is on net {bad[0]['net']!r}, "
                f"not {net!r}")
        return matches

    if pair_a == pair_b:
        raise SystemExit(
            f"FAIL-CLOSED: terminal pair is {pair_a} twice - a self-pair "
            f"measures nothing")
    pads_a, pads_b = find_pads(pair_a), find_pads(pair_b)
    masks, (minx, miny, g, nx, ny, xs, ys) = rasterize_net(board, net, grid)

    rsq = 1.7241e-8 / (35e-6 * oz)      # ohms/square
    g_sheet = 1.0 / rsq

    idx = {}
    n_nodes = 0
    for L in CU_LAYERS:
        iy, ix = np.nonzero(masks[L])
        ids = np.full(masks[L].shape, -1, dtype=np.int64)
        ids[iy, ix] = np.arange(n_nodes, n_nodes + len(iy))
        idx[L] = ids
        n_nodes += len(iy)
    if n_nodes == 0:
        raise SystemExit(f"FAIL-CLOSED: net {net!r} rasterized to no copper")

    e_a, e_b, e_g = [], [], []

    def add_edges(a, b, cond):
        a = np.atleast_1d(np.asarray(a, dtype=np.int64))
        b = np.atleast_1d(np.asarray(b, dtype=np.int64))
        e_a.append(a); e_b.append(b)
        e_g.append(np.full(len(a), cond))

    def add_edge(a, b, cond):
        add_edges([a], [b], cond)

    for L in CU_LAYERS:
        ids = idx[L]
        m = masks[L]
        h = m[:, :-1] & m[:, 1:]
        add_edges(ids[:, :-1][h], ids[:, 1:][h], g_sheet)
        vlink = m[:-1, :] & m[1:, :]
        add_edges(ids[:-1, :][vlink], ids[1:, :][vlink], g_sheet)

    def cell_of(x, y):
        return int((y - miny) / g), int((x - minx) / g)

    g_via = 1.0 / (via_mohm * 1e-3)
    for v in [v for v in board["vias"] if v["net"] == net] + \
             [{"x": p["x"], "y": p["y"]} for p in board["pads"]
              if p["net"] == net and p["thru"]]:
        iy, ix = cell_of(v["x"], v["y"])
        if not (0 <= iy < ny and 0 <= ix < nx):
            continue
        fa, ba = idx["F.Cu"][iy, ix], idx["B.Cu"][iy, ix]
        if fa >= 0 and ba >= 0:
            add_edge(fa, ba, g_via)

    # terminal supernodes tied to every cell under the pad geometry
    # (all same-numbered pads of the terminal feed one supernode)
    term_ids = []
    for group, spec in ((pads_a, pair_a), (pads_b, pair_b)):
        tid = n_nodes
        n_nodes += 1
        term_ids.append(tid)
        tied = 0
        for p in group:
            gminx, gminy, gmaxx, gmaxy = p["geom"].bounds
            for L in p["layers"] & set(CU_LAYERS):
                ids = idx[L]
                ix0 = max(0, int((gminx - minx) / g)); ix1 = min(nx, int((gmaxx - minx) / g) + 1)
                iy0 = max(0, int((gminy - miny) / g)); iy1 = min(ny, int((gmaxy - miny) / g) + 1)
                for iy in range(iy0, iy1):
                    for ix in range(ix0, ix1):
                        if ids[iy, ix] >= 0 and p["geom"].intersects(
                                Point(xs[ix], ys[iy])):
                            add_edge(tid, ids[iy, ix], g_via * 10)
                            tied += 1
        if tied == 0:
            raise SystemExit(
                f"FAIL-CLOSED: terminal {spec} touches no "
                f"copper cell of net {net!r}")

    ea = np.concatenate(e_a); eb = np.concatenate(e_b)
    eg = np.concatenate(e_g)
    # symmetric adjacency (conductances)
    row = np.concatenate([ea, eb]); col = np.concatenate([eb, ea])
    dat = np.concatenate([eg, eg])
    A = coo_matrix((dat, (row, col)), shape=(n_nodes, n_nodes)).tocsr()

    _, labels = connected_components(A, directed=False)
    if labels[term_ids[0]] != labels[term_ids[1]]:
        return float("inf"), n_nodes

    diag = np.asarray(A.sum(axis=1)).ravel()
    Acoo = A.tocoo()
    Lap = coo_matrix(
        (np.concatenate([diag, -Acoo.data]),
         (np.concatenate([np.arange(n_nodes), Acoo.row]),
          np.concatenate([np.arange(n_nodes), Acoo.col]))),
        shape=(n_nodes, n_nodes)).tocsr()
    # ground terminal B by deleting its row/col
    keep = np.ones(n_nodes, dtype=bool)
    keep[term_ids[1]] = False
    Lr = Lap[keep][:, keep]
    b = np.zeros(n_nodes)
    b[term_ids[0]] = 1.0
    v = spsolve(Lr.tocsc(), b[keep])
    full = np.zeros(n_nodes)
    full[keep] = v
    return float(full[term_ids[0]]), n_nodes


# --------------------------------------------------------------------- cli

class GuardArgumentParser(argparse.ArgumentParser):
    """argparse's default usage-error exit code is 2, which collides with
    this guard's FAIL=2; a malformed invocation is unevaluable, not a board
    that failed its gate. Ported from `loop_inductance_guard.py`, which
    fixed the same collision. `add_subparsers` defaults `parser_class` to
    `type(self)`, so `vias` and `resistance` inherit this."""

    def error(self, message):
        self.print_usage(sys.stderr)
        raise SystemExit(
            f"FAIL-CLOSED: bad invocation: {message} - a malformed command "
            f"line is unevaluable, not a verdict about the board")


def main(argv=None):
    ap = GuardArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    ap_v = sub.add_parser("vias", help="via annular contact quality")
    ap_v.add_argument("board")
    ap_v.add_argument("--net", action="append",
                      help="restrict to net(s); default all")
    ap_v.add_argument("--min-zone-frac", type=float, default=0.6)
    ap_v.add_argument("--min-contact-mm", type=float, default=0.3,
                      help="widest contiguous copper strip crossing the "
                      "annulus that passes on its own (a zone thermal "
                      "spoke is electrically a track; default 0.3)")
    ap_v.add_argument("--verbose", action="store_true")
    ap_v.add_argument("--json", help="write full per-via rows to file")

    ap_r = sub.add_parser("resistance", help="terminal-pair DC resistance")
    ap_r.add_argument("board")
    ap_r.add_argument("--net", required=True)
    ap_r.add_argument("--pair", nargs=2, required=True,
                      metavar=("REF.PAD", "REF.PAD"))
    ap_r.add_argument("--grid", type=float, default=0.2)
    ap_r.add_argument("--oz", type=float, required=True,
                      help="copper weight per layer, from the board's real "
                      "stackup; REQUIRED - a silent default halves or "
                      "doubles every resistance on the wrong board")
    ap_r.add_argument("--via-mohm", type=float, default=0.2,
                      help="lumped per-site layer-transition resistance "
                      "(default 0.2 mOhm; an assumption, not barrel "
                      "geometry)")
    ap_r.add_argument("--max-mohm", type=float, required=True,
                      help="fail (exit 2) above this; REQUIRED - derive it "
                      "from the current budget (I2R/drop bound), and pass "
                      "an explicit large bound for a diagnostic-only read")

    # Invalidate any pre-existing report BEFORE argparse can exit: a bad
    # argument used to leave a previous all-PASS artefact standing (codex
    # review of 7b00165). Refuse a target we do not own first, so a
    # malformed command line can never damage the board it names.
    argv = list(sys.argv[1:] if argv is None else argv)
    target = None
    for index, token in enumerate(argv):
        if token == "--json" and index + 1 < len(argv):
            target = argv[index + 1]
        elif token.startswith("--json="):
            target = token.split("=", 1)[1]
    if target:
        if not _is_own_report(target):
            raise SystemExit(
                f"FAIL-CLOSED: --json {target} exists and is not a "
                f"{_REPORT_MARKER} report; refusing to overwrite a file "
                f"this guard does not own")
        try:
            write_report(target, None, None, "unevaluable",
                         {"error": "guard did not complete (pre-parse)"})
        except (SystemExit, OSError):
            pass

    args = ap.parse_args(argv)

    def pos_finite(val, name, lo=0.0, hi=None):
        if val is None or not math.isfinite(val) or val <= lo or \
                (hi is not None and val > hi):
            rng = f"({lo}, {hi}]" if hi is not None else f"> {lo}"
            raise SystemExit(
                f"FAIL-CLOSED: {name}={val!r} is not a finite number "
                f"{rng} - a NaN or out-of-domain parameter must never "
                f"produce a verdict")
        return val

    t0 = time.time()

    if args.cmd == "vias":
        pos_finite(args.min_zone_frac, "--min-zone-frac", lo=0.0, hi=1.0)
        pos_finite(args.min_contact_mm, "--min-contact-mm", lo=0.0, hi=5.0)
        require_geometry()
        board = parse_board(args.board)
        nets = set(norm_net(n) for n in args.net) if args.net else None
        findings, rows = check_vias(board, nets, args.min_zone_frac,
                                    args.min_contact_mm, args.verbose)
        if args.json:
            write_report(args.json, "vias", args.board,
                         "fail" if findings else "pass", rows)
        print(f"vias graded: {len(rows)//2} ({len(rows)} via-layer "
              f"subjects), FAIL rows: {len(findings)}, threshold "
              f"{args.min_zone_frac} ({time.time()-t0:.1f}s)")
        sys.exit(2 if findings else 0)

    if args.cmd == "resistance":
        require_geometry()
        pos_finite(args.grid, "--grid", lo=0.0, hi=5.0)
        pos_finite(args.oz, "--oz", lo=0.0, hi=20.0)
        pos_finite(args.via_mohm, "--via-mohm", lo=0.0, hi=100.0)
        pos_finite(args.max_mohm, "--max-mohm", lo=0.0)
        board = parse_board(args.board)
        r, n = check_resistance(board, norm_net(args.net),
                                args.pair[0], args.pair[1],
                                args.grid, args.oz, args.via_mohm)
        if math.isinf(r):
            print(f"R({args.pair[0]} <-> {args.pair[1]}) on {args.net}: "
                  f"INFINITE - terminals are in disconnected copper "
                  f"({n} nodes, {time.time()-t0:.1f}s)")
            sys.exit(2)
        mohm = r * 1000
        margin = args.max_mohm - mohm
        verdict = "PASS" if mohm <= args.max_mohm else "FAIL"
        print(f"{verdict} R({args.pair[0]} <-> {args.pair[1]}) on "
              f"{args.net}: {mohm:.3f} mOhm, limit {args.max_mohm} mOhm, "
              f"margin {margin:+.3f} mOhm  [grid {args.grid} mm, "
              f"{args.oz} oz, via {args.via_mohm} mOhm, {n} nodes, "
              f"{time.time()-t0:.1f}s]")
        sys.exit(0 if verdict == "PASS" else 2)


if __name__ == "__main__":
    main()
