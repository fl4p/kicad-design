#!/usr/bin/env python3
"""Lane-capacity feasibility map for a saved KiCad board (report-only diagnostic).

Answers the global-routing question a detailed router cannot: at every cross
section of the board, how many track lanes does each copper layer offer under a
given rule set, and how many nets MUST cross that section because they have
pads on both sides of it. Where demand exceeds capacity no amount of rip-up and
reroute can finish the board; the fix is placement, a rule relaxation at that
spot, or a layer. Where capacity comfortably exceeds demand the cluster is not
capacity-bound and the router is failing for another reason (escape, keepouts,
ordering).

Model, deliberately simple and stated:

* Cuts are straight lines perpendicular to the chosen axis (default ``y``, i.e.
  cross sections across the board's width) every ``--step`` mm.
* Inside intervals come from the board outline polygon(s) including internal
  slots, shrunk by copper-to-edge clearance plus half a track.
* Obstacles per layer: copper pads on that layer (bounding box, so rotated pads
  are conservative), NPTH holes with hole clearance, rule areas that forbid
  tracks on that layer. In ``--residual`` mode existing tracks and vias are
  obstacles too, so the map shows what is LEFT, not what the placement offers.
* Lanes in a free interval of centreline length L: ``1 + floor(L / (w + c))``.
* Demand at a cut: nets (excluding declared pour nets) with pad centres on both
  sides. Each is weighted by its net-class width plus clearance so a wide
  supply counts as more than one signal lane. This is a LOWER bound: a net may
  need to cross a cut more than once, and vias are not modelled in placement
  mode. A ratio near 1.0 is therefore already infeasible in practice.

Exit 0 after writing the report; exit 1 when the board cannot be loaded,
``pcbnew`` is unavailable, or a requested net or rule set is malformed. There
are no gating thresholds: this is a map for deciding where to look, not a
guard. Run under KiCad's own Python (``import pcbnew`` must work).

Calibration record (2026-09-12, o2-probe Rev B, 20 x 89 mm, 2 layers, 85 nets):
see ``../reviews/2026-09-12-lane-capacity-map.md``.
"""

import argparse
import csv
import json
import math
import os
import re
import sys
from collections import defaultdict


# --------------------------------------------------------------------------- rules

RULE_FIELDS = ("track", "clearance", "via_dia", "via_drill", "hole_clearance", "edge_clearance")


class RuleSet:
    def __init__(self, name, track, clearance, via_dia, via_drill, hole_clearance, edge_clearance):
        self.name = name
        self.track = float(track)
        self.clearance = float(clearance)
        self.via_dia = float(via_dia)
        self.via_drill = float(via_drill)
        self.hole_clearance = float(hole_clearance)
        self.edge_clearance = float(edge_clearance)
        for f in RULE_FIELDS:
            v = getattr(self, f)
            if not (math.isfinite(v) and v >= 0):
                raise ValueError("rule set %s: %s=%r is not a finite non-negative number" % (name, f, v))
        if self.track <= 0:
            raise ValueError("rule set %s: track must be > 0" % name)

    @classmethod
    def parse(cls, spec):
        # NAME:track,clearance,via_dia,via_drill,hole_clearance,edge_clearance
        if ":" not in spec:
            raise ValueError("rule spec %r lacks NAME:" % spec)
        name, rest = spec.split(":", 1)
        vals = rest.split(",")
        if len(vals) != len(RULE_FIELDS):
            raise ValueError("rule spec %r needs %d comma-separated values (%s)"
                             % (spec, len(RULE_FIELDS), ",".join(RULE_FIELDS)))
        return cls(name, *vals)

    @classmethod
    def from_project(cls, pro_path, name="project"):
        with open(pro_path, "r", encoding="utf-8") as fh:
            pro = json.load(fh)
        r = pro["board"]["design_settings"]["rules"]
        return cls(name, r["min_track_width"], r["min_clearance"], r["min_via_diameter"],
                   r["min_through_hole_diameter"], r["min_hole_clearance"],
                   r["min_copper_edge_clearance"])

    def describe(self):
        return ("%s: track %.3f clr %.3f via %.2f/%.2f hole-clr %.3f edge-clr %.3f"
                % (self.name, self.track, self.clearance, self.via_dia, self.via_drill,
                   self.hole_clearance, self.edge_clearance))


def netclass_widths(pro_path):
    """Return (default_width, [(compiled_pattern, width, classname)]) from a .kicad_pro."""
    with open(pro_path, "r", encoding="utf-8") as fh:
        pro = json.load(fh)
    ns = pro.get("net_settings", {})
    default = None
    by_name = {}
    for c in (ns.get("classes") or []):
        w = c.get("track_width")
        if c.get("name") == "Default":
            default = w
        if w is not None:
            by_name[c["name"]] = float(w)
    pats = []
    for p in (ns.get("netclass_patterns") or []):
        cls_name = p.get("netclass")
        pat = p.get("pattern")
        if cls_name in by_name and pat:
            pats.append((re.compile(fnmatch_to_re(pat)), by_name[cls_name], cls_name))
    assigned = {}
    for net, cls_name in (ns.get("netclass_assignments") or {}).items():
        if isinstance(cls_name, list):
            cls_name = cls_name[0] if cls_name else None
        if cls_name in by_name:
            assigned[net] = (by_name[cls_name], cls_name)
    return default, pats, assigned


def fnmatch_to_re(pat):
    out = "^"
    for ch in pat:
        if ch == "*":
            out += ".*"
        elif ch == "?":
            out += "."
        else:
            out += re.escape(ch)
    return out + "$"


# --------------------------------------------------------------------------- geometry

def merge_intervals(ivs):
    ivs = sorted(i for i in ivs if i[1] > i[0])
    out = []
    for a, b in ivs:
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return out


def subtract(inside, obstacles):
    """inside, obstacles: merged interval lists. Returns free intervals."""
    free = []
    for a, b in inside:
        cur = a
        for oa, ob in obstacles:
            if ob <= cur:
                continue
            if oa >= b:
                break
            if oa > cur:
                free.append((cur, oa))
            cur = max(cur, ob)
        if cur < b:
            free.append((cur, b))
    return free


def polygon_crossings(points, y):
    """x values where the closed polygon `points` crosses horizontal line y."""
    xs = []
    n = len(points)
    for i in range(n):
        (x1, y1) = points[i]
        (x2, y2) = points[(i + 1) % n]
        if y1 == y2:
            continue
        if (y1 <= y < y2) or (y2 <= y < y1):
            xs.append(x1 + (y - y1) * (x2 - x1) / (y2 - y1))
    xs.sort()
    return xs


def crossings_to_intervals(xs):
    return [(xs[i], xs[i + 1]) for i in range(0, len(xs) - 1, 2)]


def chain_points(chain, swap):
    pts = []
    for k in range(chain.PointCount()):
        p = chain.CPoint(k)
        x, y = p.x / 1e6, p.y / 1e6
        pts.append((y, x) if swap else (x, y))
    return pts


# --------------------------------------------------------------------------- board model

class BoardModel:
    """Everything the scan needs, extracted once, in mm, with the scan axis mapped to y."""

    def __init__(self, board, pcbnew, swap_axes):
        self.pcbnew = pcbnew
        self.swap = swap_axes
        self.layers = [pcbnew.F_Cu, pcbnew.B_Cu]
        if board.GetCopperLayerCount() != 2:
            inner = board.GetCopperLayerCount() - 2
            # inner layers: In1_Cu .. ; keep order F, In1.., B
            self.layers = [pcbnew.F_Cu] + [pcbnew.In1_Cu + i for i in range(inner)] + [pcbnew.B_Cu]
        self.layer_names = [board.GetLayerName(l) for l in self.layers]

        outlines = pcbnew.SHAPE_POLY_SET()
        try:
            ok = board.GetBoardPolygonOutlines(outlines, False)
        except TypeError:  # KiCad < 10 signature
            ok = board.GetBoardPolygonOutlines(outlines)
        if not ok:
            raise RuntimeError("board outline is not a closed polygon; cannot compute inside intervals")
        self.outer = []
        self.holes = []
        for i in range(outlines.OutlineCount()):
            self.outer.append(chain_points(outlines.Outline(i), swap_axes))
            for j in range(outlines.HoleCount(i)):
                self.holes.append(chain_points(outlines.Hole(i, j), swap_axes))
        ys = [p[1] for poly in self.outer for p in poly]
        xs = [p[0] for poly in self.outer for p in poly]
        self.ymin, self.ymax = min(ys), max(ys)
        self.xmin, self.xmax = min(xs), max(xs)

        # pads: (layerset, x0, x1, y0, y1, kind, netname, ref, cx, cy, drill)
        self.pads = []
        for fp in board.GetFootprints():
            ref = fp.GetReference()
            for pad in fp.Pads():
                bb = pad.GetBoundingBox()
                x0, y0 = bb.GetX() / 1e6, bb.GetY() / 1e6
                x1, y1 = x0 + bb.GetWidth() / 1e6, y0 + bb.GetHeight() / 1e6
                pos = pad.GetPosition()
                cx, cy = pos.x / 1e6, pos.y / 1e6
                if swap_axes:
                    x0, y0, x1, y1 = y0, x0, y1, x1
                    cx, cy = cy, cx
                attr = pad.GetAttribute()
                kind = "npth" if attr == pcbnew.PAD_ATTRIB_NPTH else (
                    "tht" if attr == pcbnew.PAD_ATTRIB_PTH else "smd")
                drill = (pad.GetDrillSizeX() / 1e6, pad.GetDrillSizeY() / 1e6)
                if swap_axes:
                    drill = (drill[1], drill[0])
                ls = pad.GetLayerSet()
                on = tuple(ls.Contains(l) for l in self.layers)
                self.pads.append(dict(on=on, x0=x0, x1=x1, y0=y0, y1=y1, kind=kind,
                                      net=pad.GetNetname(), ref=ref, cx=cx, cy=cy,
                                      drill=drill, number=pad.GetNumber(), fp=fp))

        # rule areas forbidding tracks
        self.keepouts = []  # (on-tuple, polygon points list)
        self.zone_count = 0
        for z in board.Zones():
            self.zone_count += 1
            if not z.GetIsRuleArea():
                continue
            if not z.GetDoNotAllowTracks():
                continue
            ls = z.GetLayerSet()
            on = tuple(ls.Contains(l) for l in self.layers)
            poly = z.Outline()
            for i in range(poly.OutlineCount()):
                self.keepouts.append((on, chain_points(poly.Outline(i), swap_axes), z.GetZoneName()))

        # tracks and vias
        self.segments = []  # dict(layer_index, x1,y1,x2,y2,w,net)
        self.vias = []      # dict(x,y,dia,drill,net)
        for t in board.GetTracks():
            if t.Type() == pcbnew.PCB_VIA_T:
                p = t.GetPosition()
                x, y = p.x / 1e6, p.y / 1e6
                if swap_axes:
                    x, y = y, x
                self.vias.append(dict(x=x, y=y, dia=t.GetWidth() / 1e6,
                                      drill=t.GetDrillValue() / 1e6, net=t.GetNetname()))
            elif t.Type() in (pcbnew.PCB_TRACE_T, pcbnew.PCB_ARC_T):
                if t.GetLayer() not in self.layers:
                    continue
                li = self.layers.index(t.GetLayer())
                s, e = t.GetStart(), t.GetEnd()
                x1, y1, x2, y2 = s.x / 1e6, s.y / 1e6, e.x / 1e6, e.y / 1e6
                if swap_axes:
                    x1, y1, x2, y2 = y1, x1, y2, x2
                self.segments.append(dict(li=li, x1=x1, y1=y1, x2=x2, y2=y2,
                                          w=t.GetWidth() / 1e6, net=t.GetNetname()))

        self.net_names = sorted({p["net"] for p in self.pads if p["net"]})

    # ---- per-cut queries ----------------------------------------------------

    def inside(self, y, shrink):
        ivs = []
        for poly in self.outer:
            ivs += crossings_to_intervals(polygon_crossings(poly, y))
        holes = []
        for poly in self.holes:
            holes += crossings_to_intervals(polygon_crossings(poly, y))
        ivs = subtract(merge_intervals(ivs), merge_intervals(holes))
        return [(a + shrink, b - shrink) for a, b in ivs if b - a > 2 * shrink]

    def obstacles(self, y, li, rules, residual):
        w2 = rules.track / 2
        obs = []
        for p in self.pads:
            if p["kind"] == "npth":
                # hole plus hole clearance; the pad bbox of an NPTH can include a
                # copper-free courtyard-like ring, so use the drill itself
                hx, hy = p["drill"]
                y0, y1 = p["cy"] - hy / 2, p["cy"] + hy / 2
                if y0 <= y <= y1:
                    grow = rules.hole_clearance + w2
                    obs.append((p["cx"] - hx / 2 - grow, p["cx"] + hx / 2 + grow))
                continue
            if not p["on"][li]:
                continue
            if p["y0"] <= y <= p["y1"]:
                grow = rules.clearance + w2
                obs.append((p["x0"] - grow, p["x1"] + grow))
                if p["kind"] == "tht":
                    hx, hy = p["drill"]
                    grow = rules.hole_clearance + w2
                    obs.append((p["cx"] - hx / 2 - grow, p["cx"] + hx / 2 + grow))
        for on, poly, _name in self.keepouts:
            if on[li]:
                for a, b in crossings_to_intervals(polygon_crossings(poly, y)):
                    obs.append((a - w2, b + w2))
        if residual:
            for s in self.segments:
                if s["li"] != li:
                    continue
                grow = s["w"] / 2 + rules.clearance + w2
                ylo, yhi = min(s["y1"], s["y2"]), max(s["y1"], s["y2"])
                if y < ylo - grow or y > yhi + grow:
                    continue
                if abs(s["y2"] - s["y1"]) < 1e-9:
                    obs.append((min(s["x1"], s["x2"]) - grow, max(s["x1"], s["x2"]) + grow))
                    continue
                # centreline x at y, clamped to the segment, plus the round cap
                t = (y - s["y1"]) / (s["y2"] - s["y1"])
                t = min(1.0, max(0.0, t))
                xc = s["x1"] + t * (s["x2"] - s["x1"])
                yc = s["y1"] + t * (s["y2"] - s["y1"])
                dy = y - yc
                # half-width of the swept stroke at this y: for a slanted segment the
                # stroke is a parallelogram; approximate with the perpendicular growth
                # divided by the sine of the angle to the cut, capped by the cap radius
                dx = s["x2"] - s["x1"]
                dyy = s["y2"] - s["y1"]
                L = math.hypot(dx, dyy)
                sin_a = abs(dyy) / L
                half = grow / sin_a if sin_a > 1e-6 else 1e9
                half = min(half, math.hypot(grow, L) + grow)
                if abs(dy) > grow:
                    continue
                cap = math.sqrt(max(0.0, grow * grow - dy * dy))
                half = max(cap, min(half, cap + abs(dx)))
                obs.append((xc - half, xc + half))
            for v in self.vias:
                r = max(v["dia"] / 2 + rules.clearance, v["drill"] / 2 + rules.hole_clearance) + w2
                dy = y - v["y"]
                if abs(dy) <= r:
                    dx = math.sqrt(r * r - dy * dy)
                    obs.append((v["x"] - dx, v["x"] + dx))
        return merge_intervals(obs)

    def capacity(self, y, li, rules, residual):
        shrink = rules.edge_clearance + rules.track / 2
        free = subtract(self.inside(y, shrink), self.obstacles(y, li, rules, residual))
        pitch = rules.track + rules.clearance
        lanes = 0
        free_mm = 0.0
        for a, b in free:
            L = b - a
            if L < 0:
                continue
            lanes += 1 + int(math.floor(L / pitch + 1e-9))
            # n tracks fit when n*(w+c) <= L + w + c, so this is the mm budget
            # the (w_net + c) demand terms are measured against
            free_mm += L + pitch
        return lanes, free_mm, free

    def used_tracks(self, y, li):
        n = 0
        for s in self.segments:
            if s["li"] != li:
                continue
            ylo, yhi = min(s["y1"], s["y2"]), max(s["y1"], s["y2"])
            if ylo < y <= yhi and abs(s["y2"] - s["y1"]) > 1e-9:
                n += 1
        return n

    def nets_crossing(self, y):
        """Nets that already have a track segment crossing the cut, on any layer."""
        out = set()
        for s in self.segments:
            ylo, yhi = min(s["y1"], s["y2"]), max(s["y1"], s["y2"])
            if ylo < y <= yhi and abs(s["y2"] - s["y1"]) > 1e-9:
                out.add(s["net"])
        return out


# --------------------------------------------------------------------------- demand

def net_extents(model, pour_nets):
    ext = {}
    for p in model.pads:
        n = p["net"]
        if not n or n in pour_nets or p["kind"] == "npth":
            continue
        lo, hi = ext.get(n, (math.inf, -math.inf))
        ext[n] = (min(lo, p["cy"]), max(hi, p["cy"]))
    return {n: e for n, e in ext.items() if e[1] > e[0]}


def demand_at(y, extents, widths, rules, already=frozenset()):
    nets = [n for n, (lo, hi) in extents.items() if lo < y < hi and n not in already]
    # a net class narrower than the rule's minimum track is routed at the minimum
    wn = {n: max(widths.get(n, rules.track), rules.track) for n in nets}
    mm = sum(wn[n] + rules.clearance for n in nets)
    lanes = sum(max(1, int(math.ceil((wn[n] + rules.clearance)
                                     / (rules.track + rules.clearance) - 1e-9))) for n in nets)
    return nets, lanes, mm


# --------------------------------------------------------------------------- escape check

def escape_check(model, rules, pitch_limit):
    """For fine-pitch footprints: can one track pass between adjacent pads on the same layer?"""
    rows = []
    by_fp = defaultdict(list)
    for p in model.pads:
        if p["kind"] != "npth":
            by_fp[p["ref"]].append(p)
    need = rules.track + 2 * rules.clearance
    for ref, pads in sorted(by_fp.items()):
        if len(pads) < 4:
            continue
        for li, lname in enumerate(model.layer_names):
            lp = [p for p in pads if p["on"][li]]
            if len(lp) < 4:
                continue
            # adjacent = nearest neighbour gap along x for pads sharing a y-band, and vice versa
            gaps = []
            for a in lp:
                best = None
                for b in lp:
                    if a is b:
                        continue
                    # overlap in y band -> horizontal neighbour
                    if a["y0"] < b["y1"] and b["y0"] < a["y1"]:
                        g = b["x0"] - a["x1"] if b["x0"] >= a["x1"] else (a["x0"] - b["x1"] if a["x0"] >= b["x1"] else -1)
                    elif a["x0"] < b["x1"] and b["x0"] < a["x1"]:
                        g = b["y0"] - a["y1"] if b["y0"] >= a["y1"] else (a["y0"] - b["y1"] if a["y0"] >= b["y1"] else -1)
                    else:
                        continue
                    if g >= 0 and (best is None or g < best):
                        best = g
                if best is not None:
                    gaps.append(best)
            if not gaps:
                continue
            gmin = min(gaps)
            if gmin > pitch_limit:
                continue
            rows.append(dict(ref=ref, layer=lname, pads=len(lp), min_gap=gmin, need=need,
                             pass_between=gmin + 1e-9 >= need,
                             fp=lp[0]["fp"].GetFPIDAsString().split(":")[-1]))
    return rows


# --------------------------------------------------------------------------- svg

def write_svg(path, model, rulesets, rows_by_rule, axis):
    # rows: list of dict(y, cap_per_layer, demand_lanes)
    COL = 320
    PX = 2.5  # px per lane
    W = COL * len(rulesets) + 60
    span = model.ymax - model.ymin
    scale = 6.0  # px per mm
    H = int(span * scale) + 60
    out = ['<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" font-family="monospace" font-size="10">' % (W, H)]
    out.append('<rect width="100%" height="100%" fill="white"/>')
    for ri, rs in enumerate(rulesets):
        x0 = 40 + ri * COL
        out.append('<text x="%d" y="14">%s</text>' % (x0, rs.name))
        out.append('<text x="%d" y="26" fill="#555">cap F | cap B | demand (lanes)</text>' % x0)
        rows = rows_by_rule[rs.name]
        for r in rows:
            yy = 40 + (r["y"] - model.ymin) * scale
            cap = sum(r["cap"])
            dem = r["demand_lanes"]
            fm = sum(r["free_mm"])
            ratio = r["demand_mm"] / fm if fm > 0 else (0.0 if r["demand_mm"] == 0 else 9.0)
            if ratio >= 1.0:
                col = "#c0392b"
            elif ratio >= 0.7:
                col = "#e67e22"
            elif ratio >= 0.5:
                col = "#f1c40f"
            else:
                col = "#2ecc71"
            out.append('<rect x="%d" y="%.1f" width="12" height="%.1f" fill="%s"/>' % (x0, yy, scale * r["step"], col))
            bx = x0 + 20
            for li, c in enumerate(r["cap"]):
                out.append('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="#95a5a6"/>' % (bx, yy, c * PX, max(1.0, scale * r["step"] - 0.5)))
                bx += PX * max(c, 0) + 6
            out.append('<rect x="%d" y="%.1f" width="%.1f" height="%.1f" fill="#2c3e50"/>' % (x0 + 20, yy + scale * r["step"] * 0.35, dem * PX, max(1.0, scale * r["step"] * 0.3)))
        # y ticks
        for yt in range(int(math.floor(model.ymin)), int(math.ceil(model.ymax)) + 1, 5):
            yy = 40 + (yt - model.ymin) * scale
            out.append('<text x="%d" y="%.1f" fill="#888">%s=%d</text>' % (x0 - 38, yy + 3, axis, yt))
    out.append('</svg>')
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


# --------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0], allow_abbrev=False)
    ap.add_argument("board", help="saved .kicad_pcb")
    ap.add_argument("--pro", help=".kicad_pro for rules and net classes (default: beside the board)")
    ap.add_argument("--rules", action="append", default=[],
                    help="NAME:track,clearance,via_dia,via_drill,hole_clearance,edge_clearance (mm); repeatable. "
                         "Default: the project's minimums as rule set 'project'")
    ap.add_argument("--axis", choices=("y", "x"), default="y",
                    help="scan along this axis, cuts perpendicular to it (default y: cross sections across the width)")
    ap.add_argument("--step", type=float, default=0.5, help="cut spacing in mm (default 0.5)")
    ap.add_argument("--pour-net", action="append", default=[],
                    help="net served by a copper pour, excluded from lane demand (repeatable). Must exist on the board.")
    ap.add_argument("--net-width", action="append", default=[], metavar="NET=MM",
                    help="override a net's track width for demand weighting")
    ap.add_argument("--exclude-layer", action="append", default=[], metavar="LAYER",
                    help="treat this copper layer as unavailable for tracks (e.g. a layer kept as a pour); repeatable")
    ap.add_argument("--residual", action="store_true",
                    help="count existing tracks and vias as obstacles: report remaining lanes on the routed board")
    ap.add_argument("--show", type=float, default=0.7,
                    help="print cuts whose demand/capacity ratio is at least this (default 0.7); 0 prints all")
    ap.add_argument("--escape-pitch", type=float, default=0.8,
                    help="run the pass-between check on footprints whose min pad gap is at most this (mm)")
    ap.add_argument("--csv", help="write the full per-cut table here")
    ap.add_argument("--svg", help="write a heat strip here")
    args = ap.parse_args(argv)

    try:
        import pcbnew  # noqa: F401
    except ImportError:
        print("UNEVALUABLE: 'pcbnew' is not importable; run under KiCad's Python", file=sys.stderr)
        return 1
    if not os.path.isfile(args.board):
        print("UNEVALUABLE: board not found: %s" % args.board, file=sys.stderr)
        return 1
    pro = args.pro or os.path.splitext(args.board)[0] + ".kicad_pro"
    have_pro = os.path.isfile(pro)
    if not have_pro and not args.rules:
        print("UNEVALUABLE: project file not found: %s (pass --pro, or --rules for every rule set)" % pro, file=sys.stderr)
        return 1

    try:
        rulesets = [RuleSet.parse(s) for s in args.rules] or [RuleSet.from_project(pro)]
    except (ValueError, KeyError) as exc:
        print("UNEVALUABLE: %s" % exc, file=sys.stderr)
        return 1

    board = pcbnew.LoadBoard(args.board)
    try:
        model = BoardModel(board, pcbnew, swap_axes=(args.axis == "x"))
    except RuntimeError as exc:
        print("UNEVALUABLE: %s" % exc, file=sys.stderr)
        return 1

    for n in args.pour_net:
        if n not in model.net_names:
            print("UNEVALUABLE: --pour-net %r is not a net on this board; GND-like nets: %s"
                  % (n, ", ".join(x for x in model.net_names if "GND" in x.upper()) or "none"), file=sys.stderr)
            return 1
    pour = set(args.pour_net)

    default_w, pats, assigned = netclass_widths(pro) if have_pro else (None, [], {})
    widths = {}
    for n in model.net_names:
        w = default_w
        if n in assigned:
            w = assigned[n][0]
        else:
            for rx, ww, _c in pats:
                if rx.match(n):
                    w = ww
                    break
        if w is not None:
            widths[n] = float(w)
    for spec in args.net_width:
        if "=" not in spec:
            print("UNEVALUABLE: --net-width needs NET=MM, got %r" % spec, file=sys.stderr)
            return 1
        n, w = spec.rsplit("=", 1)
        if n not in model.net_names:
            print("UNEVALUABLE: --net-width net %r is not on this board" % n, file=sys.stderr)
            return 1
        widths[n] = float(w)

    excluded = []
    for ln in args.exclude_layer:
        if ln not in model.layer_names:
            print("UNEVALUABLE: --exclude-layer %r is not a copper layer here (%s)" % (ln, "/".join(model.layer_names)), file=sys.stderr)
            return 1
        excluded.append(model.layer_names.index(ln))
    extents = net_extents(model, pour)
    wcount = defaultdict(int)
    for n in model.net_names:
        wcount[widths.get(n)] += 1
    print("board %s" % args.board)
    print("  demand widths from %s: %s%s" % (pro if have_pro else "NO PROJECT FILE (each rule set's minimum track)",
          ", ".join("%d nets @ %s mm" % (c, w if w is not None else "min-track") for w, c in sorted(wcount.items(), key=lambda t: str(t[0]))),
          "; excluded layers: " + ", ".join(args.exclude_layer) if args.exclude_layer else ""))
    print("  %d footprints, %d pads, %d nets with pads (%d excluded as pour: %s), %d tracks, %d vias"
          % (len({p['ref'] for p in model.pads}), len(model.pads), len(model.net_names),
             len(pour), ", ".join(sorted(pour)) or "-", len(model.segments), len(model.vias)))
    print("  outline %s: %.2f .. %.2f mm along %s; layers %s; %d track keepouts"
          % ("with %d internal cutouts" % len(model.holes) if model.holes else "solid",
             model.ymin, model.ymax, args.axis, "/".join(model.layer_names), len(model.keepouts)))
    print("  mode: %s" % ("RESIDUAL (existing tracks and vias are obstacles; demand counts only nets with no track across the cut yet)" if args.residual
                          else "PLACEMENT (existing copper ignored; vias not modelled)"))
    for rs in rulesets:
        print("  rules %s" % rs.describe())

    n_cuts = int(math.floor((model.ymax - model.ymin) / args.step))
    ys = [model.ymin + args.step * (i + 0.5) for i in range(n_cuts)]
    rows_by_rule = {}
    csv_rows = []
    for rs in rulesets:
        rows = []
        for y in ys:
            caps = []
            free_mm = []
            for li in range(len(model.layers)):
                if li in excluded:
                    caps.append(0)
                    free_mm.append(0.0)
                    continue
                c, fmm, _ = model.capacity(y, li, rs, args.residual)
                caps.append(c)
                free_mm.append(fmm)
            already = model.nets_crossing(y) if args.residual else frozenset()
            nets, dl, dmm = demand_at(y, extents, widths, rs, already)
            used = [model.used_tracks(y, li) for li in range(len(model.layers))]
            rows.append(dict(y=y, step=args.step, cap=caps, free_mm=free_mm, demand_lanes=dl,
                             demand_mm=dmm, nets=nets, used=used))
            csv_rows.append(dict(rules=rs.name, axis=args.axis, pos=round(y, 3),
                                 **{"cap_%s" % ln: c for ln, c in zip(model.layer_names, caps)},
                                 **{"free_mm_%s" % ln: round(f, 3) for ln, f in zip(model.layer_names, free_mm)},
                                 **{"used_%s" % ln: u for ln, u in zip(model.layer_names, used)},
                                 demand_lanes=dl, demand_mm=round(dmm, 3),
                                 ratio=round(dmm / sum(free_mm), 3) if sum(free_mm) > 0 else ("inf" if dmm else 0),
                                 nets=" ".join(nets)))
        rows_by_rule[rs.name] = rows

    # ---- report
    for rs in rulesets:
        rows = rows_by_rule[rs.name]
        def ratio_of(r):
            fm = sum(r["free_mm"])
            cl = sum(r["cap"])
            rm = (r["demand_mm"] / fm) if fm > 0 else (math.inf if r["demand_mm"] > 0 else 0.0)
            rl = (r["demand_lanes"] / cl) if cl > 0 else (math.inf if r["demand_lanes"] > 0 else 0.0)
            return max(rm, rl)
        worst = sorted(rows, key=lambda r: -ratio_of(r))[:1]
        over = [r for r in rows if ratio_of(r) >= 1.0]
        print("\n== %s ==  cuts %d, cuts at or over capacity %d (ratio = max of lane and mm ratios; demand is a LOWER bound)"
              % (rs.name, len(rows), len(over)))
        if model.vias:
            r_dead = [max(v["dia"] / 2 + rs.clearance, v["drill"] / 2 + rs.hole_clearance) + rs.track / 2 for v in model.vias]
            area = sum(math.pi * r * r for r in r_dead)
            board_area = (model.xmax - model.xmin) * (model.ymax - model.ymin)
            print("  vias: %d; each blocks a %.2f mm wide strip on EVERY layer under these rules (mean); %.1f mm^2 total = %.1f%% of the bounding box"
                  % (len(model.vias), 2 * sum(r_dead) / len(r_dead), area, 100 * area / board_area))
        hdr = "  %8s | %s | %6s | %7s | %7s | %5s | %s" % (
            args.axis + " mm", " ".join("lanes%-5s" % ln[:5] for ln in model.layer_names),
            "demand", "free mm", "dem mm", "ratio", "used " + "/".join(model.layer_names) if model.segments else "")
        print(hdr)
        for r in rows:
            ratio = ratio_of(r)
            if ratio < args.show and r not in worst:
                continue
            flag = " OVER" if ratio >= 1.0 else ""
            print("  %8.2f | %s | %6d | %7.2f | %7.2f | %5s | %s%s" % (
                r["y"], " ".join("%10d" % c for c in r["cap"]), r["demand_lanes"],
                sum(r["free_mm"]), r["demand_mm"],
                ("%.2f" % ratio) if math.isfinite(ratio) else "inf",
                "/".join(str(u) for u in r["used"]) if model.segments else "", flag))
        if worst:
            w = worst[0]
            print("  worst cut %s=%.2f: nets that must cross: %s" % (args.axis, w["y"], " ".join(w["nets"])))

    # ---- escape check
    print("\n== pass-between check (footprints with pad gap <= %.2f mm) ==" % args.escape_pitch)
    for rs in rulesets:
        rows = escape_check(model, rs, args.escape_pitch)
        blocked = [r for r in rows if not r["pass_between"]]
        print("  %s: need gap >= %.3f mm for one track between pads; %d/%d footprint-layers allow it"
              % (rs.name, rs.track + 2 * rs.clearance, len(rows) - len(blocked), len(rows)))
        for r in rows:
            print("    %-5s %-38s %-5s pads %2d min-gap %.3f  %s"
                  % (r["ref"], r["fp"][:38], r["layer"], r["pads"], r["min_gap"],
                     "pass" if r["pass_between"] else "NO PASS-BETWEEN"))

    if args.csv:
        keys = list(csv_rows[0].keys()) if csv_rows else []
        with open(args.csv, "w", newline="", encoding="utf-8") as fh:
            wr = csv.DictWriter(fh, fieldnames=keys)
            wr.writeheader()
            wr.writerows(csv_rows)
        print("\ncsv: %s" % args.csv)
    if args.svg:
        write_svg(args.svg, model, rulesets, rows_by_rule, args.axis)
        print("svg: %s" % args.svg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
