#!/usr/bin/env python3
"""Schematic wiring guard — flag local same-net connections realised by net
labels instead of drawn wires ("label soup").

DRC, ERC and netlist parity are all label-blind: a net stitched together by
repeating a net label at every pin is electrically identical to one drawn with
wires, so it passes every automated gate. It is not identical to a *reviewer*:
a half-bridge drawn as four symbols joined by seven `SW_FAST` labels cannot be
traced by following a wire. This guard is the executable backstop for
SCHEMATIC.md's "local connections are wires, labels are for rails/remote"
doctrine — the one gate ERC cannot satisfy.

Contract (warn-only, reviewer-surfaced): the guard reports every net whose pins
sit in more than one wire-connected fragment bridged *only* by a shared local
label, where at least one pair of those fragments is physically local (a wire
was the cheap, readable option) and the net is not a declared rail. Each finding
is a concrete disposition item — wire it, or justify the label — never a silent
pass. It does NOT block by itself; the caller records the disposition.

Scope and honesty: this checks *wire-vs-label*, NOT wiring completeness. A board
with pins left unconnected, or wires deleted, is an ERC/connectivity defect that
this guard does not and cannot catch — a clean result here means "no
label-bridged local net was found", never "the wiring is complete or correct".
Pair it with ERC.

Metric (per net name N, over local + global labels; hierarchical labels are the
correct cross-sheet mechanism and are counted only, never flagged):

  fragments(N) = distinct wire/bus-connected components carrying a label N
                 (a label on a bare pin with no wire stub is a faithful
                  singleton fragment keyed by its own coordinate; labels never
                  union two wire fragments — they only read which they touch)
  if fragments(N) >= 2 and N is not a rail:
      gap = min distance between labels of N in DIFFERENT fragments
      if gap <= --locality-mm:               -> label_soup (a local adjacency
                                                 was labelled, not wired)
      elif fragments(N) >= --max-fragments:   -> many_fragments (a heuristic
                                                 second signal for wide-gap soup;
                                                 NOT a calibrated threshold —
                                                 disposition legitimate remote
                                                 fan-out explicitly)

Both signals are heuristics for human/agent review, not proofs. Label spacing is
an imperfect proxy for functional locality; the fragment count complements it but
legitimate multi-drop remote nets can raise it too.

Fail-closed: an unparseable or malformed schematic (unbalanced parens,
unterminated string, a coordinate that is not a finite number, more than one or
no `(kicad_sch)` root), a multi-sheet design (this guard reads one sheet), or a
non-finite/non-positive `--locality-mm` is reported UNEVALUABLE — never OK.

Exit status: 0 = evaluated, no findings; 2 = one or more findings OR unevaluable.
Advisory for a warn-only gate — the caller reads the findings and records a
disposition per SCHEMATIC.md; UNEVALUABLE is its own must-resolve status, not a
net to disposition.

    schematic_wiring_guard.py board.kicad_sch [--locality-mm 30]
        [--max-fragments 5] [--rail GND --rail DC_NEG ...] [--json]
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path


def _read_utf8(p):
    return Path(p).read_text(encoding="utf-8")


class Unevaluable(Exception):
    """Raised for any malformed/unsupported input — always fail closed."""


# Names that unambiguously fan out by label (a rail). Deliberately CONSERVATIVE:
# a soup detector must err toward flagging, so the default matches only names no
# reasonable schematic uses for a local signal. Everything else — +BUS, DC_NEG,
# HV_BUS, P12V, project buses — is declared with --rail and reviewed there. The
# +/- prefix and "nV" heuristics are intentionally NOT used: they exempt real
# signals like +VFB or ADC_5V.
_RAIL_RE = re.compile(r"^\s*(?:[APD]?GND[0-9A-Z_]*|V(?:CC|DD|EE|SS)[0-9A-Z_]*)\s*$",
                      re.IGNORECASE)


def _tokenize(text: str):
    out, i, n = [], 0, len(text)
    while i < n:
        c = text[i]
        if c in "()":
            out.append(c)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            closed = False
            while j < n:
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                if text[j] == '"':
                    closed = True
                    break
                buf.append(text[j])
                j += 1
            if not closed:
                raise Unevaluable("unterminated quoted string")
            out.append(("STR", "".join(buf)))
            i = j + 1
        elif c.isspace():
            i += 1
        else:
            j = i
            while j < n and not text[j].isspace() and text[j] not in '()"':
                j += 1
            out.append(("ATOM", text[i:j]))
            i = j
    return out


def _parse(text: str):
    toks = _tokenize(text)
    top, stack, cur = [], [], None
    for t in toks:
        if t == "(":
            new = []
            if cur is None:
                top.append(new)
            else:
                cur.append(new)
            stack.append(cur)
            cur = new
        elif t == ")":
            if not stack:
                raise Unevaluable("unbalanced parentheses (extra ')')")
            cur = stack.pop()
        else:
            if cur is None:
                raise Unevaluable("token outside any list")
            cur.append(t)
    if stack:
        raise Unevaluable("unbalanced parentheses (unclosed '(')")
    return top


def _head(node):
    return node[0][1] if node and isinstance(node[0], tuple) and node[0][0] == "ATOM" else None


def _find(node, name):
    for x in node:
        if isinstance(x, list) and _head(x) == name:
            yield x


_NUM = re.compile(r"-?\d+(?:\.\d+)?(?:[eE]-?\d+)?$")


def _num(tok, where):
    if not (isinstance(tok, tuple) and tok[0] == "ATOM" and _NUM.match(tok[1])):
        raise Unevaluable(f"non-numeric coordinate in {where}: {tok!r}")
    try:
        v = float(tok[1])
    except (ValueError, OverflowError):
        raise Unevaluable(f"unparseable coordinate in {where}: {tok!r}")
    if not math.isfinite(v):
        raise Unevaluable(f"non-finite coordinate in {where}: {tok!r}")
    return v


def _xy(node):
    # (xy X Y) — exactly two numeric args required, validated positionally.
    args = [a for a in node[1:] if isinstance(a, tuple) and a[0] == "ATOM"]
    if len(args) < 2:
        raise Unevaluable("(xy ...) needs two coordinates")
    return _num(args[0], "xy"), _num(args[1], "xy")


def _at(node):
    args = [a for a in node[1:] if isinstance(a, tuple) and a[0] == "ATOM"]
    if len(args) < 2:
        raise Unevaluable("(at ...) needs at least two coordinates")
    return _num(args[0], "at"), _num(args[1], "at")


def _first_str(node):
    for a in node:
        if isinstance(a, tuple) and a[0] == "STR":
            return a[1]
    return None


class DSU:
    def __init__(self):
        self.p = {}

    def find(self, x):
        self.p.setdefault(x, x)
        r = x
        while self.p[r] != r:
            r = self.p[r]
        while self.p[x] != r:
            self.p[x], x = r, self.p[x]
        return r

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[ra] = rb


_Q = 10000.0  # 0.1 µm snap — KiCad geometry is exact on a nm grid


def _key(x, y):
    return (round(x * _Q) / _Q, round(y * _Q) / _Q)


def _on_segment(px, py, ax, ay, bx, by, tol=0.001):
    dx, dy = bx - ax, by - ay
    L2 = dx * dx + dy * dy
    if L2 == 0:
        return math.hypot(px - ax, py - ay) <= tol
    t = ((px - ax) * dx + (py - ay) * dy) / L2
    if t < -1e-9 or t > 1 + 1e-9:
        return False
    cx, cy = ax + t * dx, ay + t * dy
    return math.hypot(px - cx, py - cy) <= tol


def analyze(path, locality_mm=30.0, max_fragments=5, rails=None):
    if not (isinstance(locality_mm, (int, float)) and math.isfinite(locality_mm) and locality_mm > 0):
        return {"ok": False, "findings": [{"kind": "unevaluable",
                "detail": f"--locality-mm must be finite and positive, got {locality_mm}"}]}
    rails = {r.upper() for r in (rails or [])}
    findings = []
    import hashlib
    try:
        text = _read_utf8(path)
        sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
        meta = {"path": str(path), "sha256": sha, "locality_mm": locality_mm,
                "max_fragments": max_fragments, "rails": sorted(rails)}
        top = _parse(text)
    except Unevaluable as e:
        return {"ok": False, "findings": [{"kind": "unevaluable", "detail": f"{path}: {e}"}]}
    except Exception as e:  # read/decoding failure — fail closed
        return {"ok": False, "findings": [{"kind": "unevaluable", "detail": f"cannot read {path}: {e}"}]}

    roots = [n for n in top if _head(n) == "kicad_sch"]
    if len(roots) != 1:
        return {"ok": False, "meta": meta, "findings": [{"kind": "unevaluable",
                "detail": f"{path}: expected exactly one (kicad_sch) root, found {len(roots)}"}]}
    sch = roots[0]

    # Multi-sheet: this guard reads a single sheet. A design with sheet instances
    # must be evaluated per sheet, or its cross-sheet nets are silently under-read.
    if any(True for _ in _find(sch, "sheet")):
        return {"ok": False, "meta": meta, "findings": [{"kind": "unevaluable",
                "detail": f"{path}: multi-sheet design (contains (sheet ...)); "
                          "run the guard on each sheet file, or flatten first"}]}

    try:
        # ---- drawn connectivity: wires AND buses ----
        segs = []  # (ax, ay, bx, by)
        for tag in ("wire", "bus"):
            for w in _find(sch, tag):
                for pts in _find(w, "pts"):
                    xy = [_xy(p) for p in _find(pts, "xy")]
                    for a, b in zip(xy, xy[1:]):
                        segs.append((a[0], a[1], b[0], b[1]))

        # ---- labels: local + global drive connectivity; hierarchical exempt ----
        labels = []  # (name, x, y, kind)
        for kind in ("label", "global_label"):
            for lb in _find(sch, kind):
                name = _first_str(lb)
                at = next(_find(lb, "at"), None)
                if name is None or at is None:
                    raise Unevaluable(f"malformed {kind} (missing name or position)")
                x, y = _at(at)
                labels.append((name, x, y, kind))
        hier = sum(1 for _ in _find(sch, "hierarchical_label"))
        # junctions: a confirmed connection point (KiCad places one only where
        # segments join), so it unions everything passing through its coordinate
        # — including two unsplit wires crossing at an interior point.
        juncs = []
        for jn in _find(sch, "junction"):
            at = next(_find(jn, "at"), None)
            if at is not None:
                juncs.append(_at(at))
    except Unevaluable as e:
        return {"ok": False, "meta": meta, "findings": [{"kind": "unevaluable", "detail": f"{path}: {e}"}]}

    stats = {"segments": len(segs), "labels": len(labels), "hier_labels": hier}
    if not segs and not labels:
        return {"ok": True, "meta": meta, "findings": [], "stats": stats, "note": "no wires or labels"}

    # ---- union-find over drawn-segment geometry ONLY (labels never union) ----
    dsu = DSU()
    for (ax, ay, bx, by) in segs:
        dsu.union(_key(ax, ay), _key(bx, by))
    hubs = ([(ax, ay) for (ax, ay, _, _) in segs]
            + [(bx, by) for (_, _, bx, by) in segs] + juncs)
    for (px, py) in hubs:  # T-joins + junctions: a point on a segment interior
        kp = _key(px, py)
        for (ax, ay, bx, by) in segs:
            if _key(ax, ay) == kp or _key(bx, by) == kp:
                continue
            if _on_segment(px, py, ax, ay, bx, by):
                dsu.union(kp, _key(ax, ay))

    # ---- each label reads its fragment; a bare pin-label is a singleton by
    #      COORDINATE (two labels at one point are one fragment) ----
    nets = {}  # name -> list of (root, x, y)
    for (name, x, y, kind) in labels:
        k = _key(x, y)
        if k in dsu.p:
            root = dsu.find(k)
        else:
            root = None
            for (ax, ay, bx, by) in segs:
                if _on_segment(x, y, ax, ay, bx, by):
                    root = dsu.find(_key(ax, ay))
                    break
            if root is None:
                root = ("bare", k)  # singleton keyed by coordinate, not occurrence
        nets.setdefault(name, []).append((root, x, y))

    for name, members in sorted(nets.items()):
        frag_roots = {r for (r, _, _) in members}
        if len(frag_roots) < 2:
            continue
        if name.upper() in rails or _RAIL_RE.match(name):
            continue
        best = None
        for i in range(len(members)):
            ri, xi, yi = members[i]
            for j in range(i + 1, len(members)):
                rj, xj, yj = members[j]
                if ri == rj:
                    continue
                d = math.hypot(xi - xj, yi - yj)
                if best is None or d < best:
                    best = d
        if best is None:
            continue
        nf = len(frag_roots)
        if best <= locality_mm:
            findings.append({"kind": "label_soup", "net": name, "fragments": nf,
                             "labels": len(members), "local_gap_mm": round(best, 2),
                             "detail": (f"net '{name}' is {nf} fragments joined only by labels; "
                                        f"closest pair {best:.1f} mm (<= {locality_mm:.0f} mm) — draw a wire")})
        elif nf >= max_fragments:
            findings.append({"kind": "many_fragments", "net": name, "fragments": nf,
                             "labels": len(members), "local_gap_mm": round(best, 2),
                             "detail": (f"net '{name}' is {nf} label-bridged fragments "
                                        f"(>= {max_fragments}); closest {best:.1f} mm — wire the local "
                                        "ones or justify remote fan-out")})

    return {"ok": True, "meta": meta, "findings": findings, "stats": {**stats,
            "label_soup": sum(1 for f in findings if f["kind"] == "label_soup"),
            "many_fragments": sum(1 for f in findings if f["kind"] == "many_fragments")}}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Flag label-soup schematics (warn-only).")
    ap.add_argument("sch", type=Path)
    ap.add_argument("--locality-mm", type=float, default=30.0,
                    help="max fragment gap that should have been a wire (default 30; lower = stricter)")
    ap.add_argument("--max-fragments", type=int, default=5,
                    help="wide-gap nets with at least this many label-bridged fragments are surfaced too (default 5)")
    ap.add_argument("--rail", action="append", default=[],
                    help="declare a rail net exempt from the check (repeatable)")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)

    res = analyze(a.sch, locality_mm=a.locality_mm, max_fragments=a.max_fragments, rails=a.rail)
    if a.json:
        print(json.dumps(res, indent=2))
        return 0 if (res.get("ok") and not res["findings"]) else 2

    st = res.get("stats", {})
    fs = res["findings"]
    print(f"# schematic wiring guard: {a.sch}")
    if not res.get("ok", False):
        for f in fs:
            print(f"  UNEVALUABLE  {f['detail']}")
        print("# UNEVALUABLE — fail closed; resolve before trusting the gate.")
        return 2
    print(f"#   segments={st.get('segments','?')} labels={st.get('labels','?')} "
          f"hier={st.get('hier_labels','?')}  locality={a.locality_mm:.0f}mm max_frags={a.max_fragments}")
    if not fs:
        print("OK — no label-bridged local net found. "
              "(Checks wire-vs-label only, not wiring completeness — pair with ERC.)")
    for f in sorted(fs, key=lambda z: (z["kind"] != "label_soup", -z["fragments"], z["local_gap_mm"])):
        tag = "label-soup " if f["kind"] == "label_soup" else "many-frags "
        print(f"  WARN {tag} {f['net']:<16} {f['fragments']} frags, {f['labels']} labels, "
              f"closest {f['local_gap_mm']} mm — {'wire it or justify' if f['kind']=='label_soup' else 'wire local / justify remote'}")
    print(f"# {st.get('label_soup',0)} label-soup, {st.get('many_fragments',0)} many-fragment net(s). "
          "Warn-only: disposition each per SCHEMATIC.md.")
    return 0 if not fs else 2


if __name__ == "__main__":
    sys.exit(main())
