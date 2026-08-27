#!/usr/bin/env python3
"""Functional-proximity guard: verify declared satellite->anchor placement bindings.

Motivation (measured): a placement generator binned DNP snubber strings as "small
passives, bottom row" ~90 mm from the switches they snub. Every mechanical gate
(courtyards, body gaps, populate-ability) passed, because none asks the electrical
question. The knowledge existed at capture time ("D-S per device" in the design
record) and was never consulted again. This guard replays capture-time intent at
layout time, mechanically.

Scope - read this before trusting a PASS: the guard is a gross-misplacement
tripwire over DECLARED intent. It measures minimum pad-centre to pad-centre
distance, optionally restricted to named pads. It does not reconstruct current
loops, return paths, or routed length, and a PASS is not evidence of a good
switching layout - keep the loop/topology audits and the DRC length tier
(POWER.md) in place beside it.

Binding contract (emitted at capture/generation time, when the partner is known):

  Anchor    = <reference designator of the partner footprint>   (declares a binding)
  MaxDist   = <millimetres, finite, > 0>                        (per-binding budget;
              required unless the invocation supplies --default-max-mm)
  SelfPad   = <pad name/number on the satellite>                (optional selector)
  AnchorPad = <pad name/number on the anchor>                   (optional selector)

A footprint carrying MaxDist/SelfPad/AnchorPad WITHOUT Anchor is a capture defect
and makes the run UNVERIFIED - deleting an Anchor from a failing binding must
never improve the verdict.

Usage:
  kicad_functional_proximity.py BOARD.kicad_pcb [--default-max-mm=MM]
      [--min-expected=N] [--expect=REF1,REF2,...]

  Release invocations MUST pass --expect with the exact set of reference
  designators the schematic/generator emitted bindings for; any missing or extra
  declaring footprint is UNVERIFIED. --min-expected is a weaker development-time
  tripwire only (a count cannot see one binding swapped for another) and is not a
  release substitute. Without an expectation, a binding deleted before the run is
  invisible to this guard.

Verdicts (fail-closed; every run prints exactly one ASCII verdict line - the
printer transliterates, so no locale/encoding can suppress the verdict; refusal
lines carry a stable branch id in brackets, and FAIL lines carry [OVER-BUDGET]):
  0  FUNC-PROX-PASS         every declared binding within budget, all
                            preconditions clean, expectation (if given) met,
                            at least one binding checked
  1  FUNC-PROX-FAIL         at least one binding exceeds its budget
  2  FUNC-PROX-UNVERIFIED   the run cannot be trusted; zero declared bindings is
                            always UNVERIFIED (a vacuous run is never a pass)

Parsing is deliberately strict for a regex-based reader: strict UTF-8; the
(kicad_pcb ...) root expression is scanned string-aware to its matching close and
must span the file (footprints after the root are refused, not graded); property
and pad tokens accept any KiCad whitespace, so a tab cannot hide a selector;
coordinates use KiCad's numeric grammar (no '+', underscores, exponents, nan/inf),
validated for every footprint and every pad; duplicate properties and duplicate
refdes refuse. Anything the reader cannot positively interpret is E-PARSE, never
a guess. The verdict line is ASCII-transliterated and control-character-scrubbed,
so it is exactly one stdout line on any host. The pad transform matches KiCad 10
writer output for front and back footprints (validated against pcbnew on
mixed-side boards).
"""

import math
import re
import sys


def verdict(code: int, msg: str, branch: str = "") -> int:
    tag = {0: "FUNC-PROX-PASS", 1: "FUNC-PROX-FAIL", 2: "FUNC-PROX-UNVERIFIED"}[code]
    bid = "[%s] " % branch if branch else ""
    line = "%s: %s%s" % (tag, bid, msg)
    # ASCII-transliterated, control-character-scrubbed write: no locale,
    # PYTHONIOENCODING, or embedded newline in echoed input can raise here or
    # break the exactly-one-stdout-line contract.
    line = line.encode("ascii", "backslashreplace").decode("ascii")
    line = re.sub(r"[\x00-\x1f\x7f]", " ", line)
    sys.stdout.write(line + "\n")
    return code


_NUM = re.compile(r"-?\d+(\.\d+)?\Z")  # KiCad's numeric grammar, not Python's:
                                       # no '+', no underscores, no exponents, no nan/inf


def _floats(text, what, want=(2, 3)):
    fields = text.split()
    if len(fields) not in want:
        raise ValueError("%s: malformed (at %s)" % (what, text))
    if not all(_NUM.match(x) for x in fields):
        raise ValueError("%s: non-numeric (at %s)" % (what, text))
    nums = [float(x) for x in fields]
    if not all(math.isfinite(v) for v in nums):
        raise ValueError("%s: non-finite (at %s)" % (what, text))
    return nums


def _root_span_end(s):
    """Index of the char closing the first top-level expression (string-aware)."""
    i = s.find("(")
    if i < 0:
        raise ValueError("no expression found")
    depth = 0
    instr = esc = False
    for j in range(i, len(s)):
        c = s[j]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        elif c == '"':
            instr = True
        elif c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("root expression never closes")


def parse_board(path):
    """Return {refdes: {"pads": [(name, gx, gy)], "props": {...}}}; raises ValueError."""
    with open(path, encoding="utf-8") as f:  # strict UTF-8: undecodable input raises
        s = f.read()
    if not re.match(r"\s*\(kicad_pcb[\s(]", s):
        raise ValueError("root expression is not (kicad_pcb ...)")
    root_end = _root_span_end(s)
    if s[root_end + 1:].strip():
        raise ValueError("content after the (kicad_pcb ...) root expression")
    s = s[:root_end]  # grade only children of the root, exactly like KiCad would
    fps = {}
    starts = [m.start() for m in re.finditer(r"\(footprint\s", s)]
    starts.append(len(s))
    for i in range(len(starts) - 1):
        b = s[starts[i]:starts[i + 1]]
        ref_m = re.search(r'\(property\s+"Reference"\s+"([^"]+)"', b)
        at_m = re.search(r"\(at\s+([^)]*)\)", b)  # first (at ...) is the footprint's own
        if not ref_m or not at_m:
            raise ValueError("footprint without Reference or (at ...): %r" % b[:60])
        ref = ref_m.group(1)
        nums = _floats(at_m.group(1), "footprint %s" % ref)
        fx, fy = nums[0], nums[1]
        th = math.radians(nums[2] if len(nums) == 3 else 0.0)
        props = {}
        for k, v in re.findall(r'\(property\s+"([^"]+)"\s+"([^"]*)"', b):
            if k in props:
                raise ValueError("%s: duplicate property %r" % (ref, k))
            props[k] = v
        pads = []
        pstarts = [m.start() for m in re.finditer(r'\(pad\s+"', b)]
        pstarts.append(len(b))
        for k in range(len(pstarts) - 1):
            pb = b[pstarts[k]:pstarts[k + 1]]
            nm = re.match(r'\(pad\s+"([^"]*)"', pb)
            pat = re.search(r"\(at\s+([^)]*)\)", pb)
            if not nm or not pat:
                raise ValueError("%s: pad without a parseable (at ...)" % ref)
            pn = _floats(pat.group(1), "%s pad %r" % (ref, nm.group(1)))
            x, y = pn[0], pn[1]
            gx = fx + x * math.cos(th) + y * math.sin(th)
            gy = fy - x * math.sin(th) + y * math.cos(th)
            pads.append((nm.group(1), gx, gy))
        if ref in fps:
            raise ValueError("reference designator %r is ambiguous "
                             "(multiple footprints)" % ref)
        fps[ref] = {"pads": pads, "props": props}
    if not fps:
        raise ValueError("no footprints parsed")
    return fps


BINDING_FIELDS = ("MaxDist", "SelfPad", "AnchorPad")


def run(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    default_max = None
    min_expected = 0
    expect = None
    for o in (a for a in argv[1:] if a.startswith("--")):
        name, eq, val = o.partition("=")
        if not eq:
            return verdict(2, "option %s needs =VALUE (e.g. --min-expected=4)" % name,
                           "E-ARGS")
        try:
            if name == "--default-max-mm":
                default_max = float(val)
                if not math.isfinite(default_max) or default_max <= 0:
                    return verdict(2, "--default-max-mm must be finite and > 0", "E-ARGS")
            elif name == "--min-expected":
                min_expected = int(val)
                if min_expected < 0:
                    return verdict(2, "--min-expected must be >= 0", "E-ARGS")
            elif name == "--expect":
                expect = sorted(set(r.strip() for r in val.split(",") if r.strip()))
                if not expect:
                    return verdict(2, "--expect lists no reference designators", "E-ARGS")
            else:
                return verdict(2, "unknown option %s" % name, "E-ARGS")
        except ValueError:
            return verdict(2, "malformed value in %s" % o, "E-ARGS")
    if len(args) != 1:
        return verdict(2, "want exactly one board file (plus --options)", "E-ARGS")

    try:
        fps = parse_board(args[0])
    except OSError as exc:
        return verdict(2, "cannot read board: %s" % exc, "E-READ")
    except (ValueError, UnicodeDecodeError) as exc:
        return verdict(2, "board not parseable as trusted input: %s" % exc, "E-PARSE")

    declaring = sorted(r for r, fp in fps.items() if "Anchor" in fp["props"])
    orphans = sorted(r for r, fp in fps.items()
                     if "Anchor" not in fp["props"]
                     and any(k in fp["props"] for k in BINDING_FIELDS))
    if orphans:
        return verdict(2, "binding fields without Anchor on %s - a deleted or "
                          "never-emitted Anchor must not improve the verdict"
                       % ", ".join(orphans), "E-ORPHAN")
    if expect is not None:
        missing = sorted(set(expect) - set(declaring))
        extra = sorted(set(declaring) - set(expect))
        if missing or extra:
            return verdict(2, "declared bindings do not match --expect "
                              "(missing: %s; unexpected: %s)"
                           % (",".join(missing) or "-", ",".join(extra) or "-"),
                           "E-EXPECT")
    if len(declaring) < min_expected:
        return verdict(2, "%d binding(s) declared but --min-expected=%d - bindings "
                          "missing at capture time" % (len(declaring), min_expected),
                       "E-EXPECT")
    if not declaring:
        return verdict(2, "0 bindings declared - nothing to verify, and a vacuous "
                          "run is never a pass; emit Anchor/MaxDist at capture time "
                          "(see POWER.md)", "E-EMPTY")

    failures, results = [], []
    for ref in declaring:
        fp = fps[ref]
        anchor = fp["props"]["Anchor"].strip()
        if not anchor:
            return verdict(2, "%s declares an empty Anchor" % ref, "E-ANCHOR")
        if anchor == ref:
            return verdict(2, "%s anchors to itself" % ref, "E-SELF")
        afp = fps.get(anchor)
        if afp is None:
            return verdict(2, "%s anchors to %r which is not on the board"
                           % (ref, anchor), "E-ANCHOR")
        if "MaxDist" in fp["props"]:
            try:
                budget = float(fp["props"]["MaxDist"])
            except ValueError:
                budget = float("nan")
            if not math.isfinite(budget) or budget <= 0:
                return verdict(2, "%s has non-finite or non-positive MaxDist %r"
                               % (ref, fp["props"]["MaxDist"]), "E-BUDGET")
        elif default_max is not None:
            budget = default_max
        else:
            return verdict(2, "%s declares Anchor without MaxDist and no "
                              "--default-max-mm was given - budgets are "
                              "project-derived, the guard has no built-in number"
                           % ref, "E-BUDGET")
        spads = fp["pads"]
        if "SelfPad" in fp["props"]:
            spads = [p for p in spads if p[0] == fp["props"]["SelfPad"]]
        apads = afp["pads"]
        if "AnchorPad" in fp["props"]:
            apads = [p for p in apads if p[0] == fp["props"]["AnchorPad"]]
        if not spads or not apads:
            return verdict(2, "%s->%s: no pads match (SelfPad/AnchorPad selector "
                              "wrong, or a footprint has no pads)" % (ref, anchor),
                           "E-PADS")
        best = min(((math.hypot(px - qx, py - qy), pn, qn)
                    for pn, px, py in spads for qn, qx, qy in apads))
        d, pn, qn = best
        results.append((ref, anchor, d, budget, pn, qn))
        if d > budget:
            failures.append((ref, anchor, d, budget, pn, qn))

    if failures:
        detail = "; ".join("%s->%s %.2fmm > %.1fmm (pads %s<->%s)" % f
                           for f in failures)
        return verdict(1, "%d of %d binding(s) exceed budget: %s"
                       % (len(failures), len(results), detail), "OVER-BUDGET")
    worst = max(results, key=lambda r: r[2] / r[3])
    return verdict(0, "%d binding(s) within budget; tightest margin %s->%s "
                      "%.2fmm of %.1fmm"
                   % (len(results), worst[0], worst[1], worst[2], worst[3]))


def main(argv) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001 - a guard must never die without a verdict
        return verdict(2, "internal error, refusing to grade: %r" % exc, "E-INTERNAL")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
