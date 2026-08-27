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
      [--min-expected=N] [--expect=REF1,REF2,...] [--skip-load-check]

  Release invocations MUST pass --expect with the exact set of reference
  designators the schematic/generator emitted bindings for; any missing or extra
  declaring footprint is UNVERIFIED. --min-expected is a weaker development-time
  tripwire only (a count cannot see one binding swapped for another) and is not a
  release substitute.

Two defense layers, both fail-closed:

1. **Authoritative loadability precondition.** Before grading, the board must load
   in the installed KiCad (kicad-cli, located via $KICAD_CLI or standard install
   paths). A board KiCad rejects is refused [E-LOAD]; kicad-cli missing is
   refused [E-TOOL]. --skip-load-check disables this layer for synthetic test
   fixtures and development; a release run must not pass it.
2. **Structural s-expression parsing.** The board is tokenized (parens, quoted
   strings with escapes, printable-ASCII atoms separated by ASCII whitespace) and
   walked structurally. Inside a footprint, only the known KiCad 10 child
   elements are permitted - an unrecognized child element is refused [E-PARSE],
   never silently ignored, so no separator or spelling trick can hide a binding
   field from the guard while KiCad still shows it (or vice versa). This is a
   deliberately NARROWER supported subset than KiCad's own parser (e.g. unquoted
   non-ASCII atoms refuse here; some legacy boards load in KiCad but refuse
   here) - narrowing is fail-safe, silence is not.

Numbers use a measured subset of KiCad 10.0.5's accepted forms: decimal,
bare-dot and exponent forms (explicit exponent sign included) grade; a leading
'+', digit underscores, and nan/inf refuse; a nonzero literal that underflows to
zero and any coordinate outside +-2000 mm refuse (KiCad clamps internally near
2147 mm; the guard refuses rather than diverging silently).

Verdicts (every run prints exactly one ASCII, control-scrubbed stdout line;
refusals carry a stable branch id, FAIL carries [OVER-BUDGET]):
  0  FUNC-PROX-PASS         every declared binding within budget, preconditions
                            clean, expectation (if given) met, >= 1 binding
  1  FUNC-PROX-FAIL         at least one binding exceeds its budget
  2  FUNC-PROX-UNVERIFIED   the run cannot be trusted; zero declared bindings is
                            always UNVERIFIED (a vacuous run is never a pass)
"""

import math
import os
import re
import subprocess
import sys
import tempfile


def verdict(code: int, msg: str, branch: str = "") -> int:
    tag = {0: "FUNC-PROX-PASS", 1: "FUNC-PROX-FAIL", 2: "FUNC-PROX-UNVERIFIED"}[code]
    bid = "[%s] " % branch if branch else ""
    line = "%s: %s%s" % (tag, bid, msg)
    line = line.encode("ascii", "backslashreplace").decode("ascii")
    line = re.sub(r"[\x00-\x1f\x7f]", " ", line)
    sys.stdout.write(line + "\n")
    return code


# ---------------------------------------------------------------- tokenizer ---

_WS = " \t\r\n"


def _tokenize(s):
    """Yield ('(',), (')',), ('str', text), ('atom', text). Anything else raises."""
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if c in _WS:
            i += 1
        elif c == "(":
            yield ("(",)
            i += 1
        elif c == ")":
            yield (")",)
            i += 1
        elif c == '"':
            j = i + 1
            buf = []
            while j < n and s[j] != '"':
                if s[j] == "\\":
                    if j + 1 >= n:
                        raise ValueError("unterminated escape at offset %d" % j)
                    buf.append(s[j + 1])
                    j += 2
                else:
                    buf.append(s[j])
                    j += 1
            if j >= n:
                raise ValueError("unterminated string starting at offset %d" % i)
            yield ("str", "".join(buf))
            i = j + 1
        else:
            j = i
            while j < n and s[j] not in _WS and s[j] not in '()"':
                if not (0x21 <= ord(s[j]) <= 0x7E):
                    raise ValueError("unsupported character %r at offset %d "
                                     "(outside the guard's supported subset)"
                                     % (s[j], j))
                j += 1
            yield ("atom", s[i:j])
            i = j


def _read_tree(s):
    """Parse exactly one top-level list; trailing tokens refuse."""
    stack = []
    root = None
    for tok in _tokenize(s):
        if root is not None:
            raise ValueError("content after the root expression")
        if tok[0] == "(":
            stack.append([])
        elif tok[0] == ")":
            if not stack:
                raise ValueError("unbalanced ')'")
            done = stack.pop()
            if stack:
                stack[-1].append(done)
            else:
                root = done
        else:
            if not stack:
                raise ValueError("token outside any expression")
            stack[-1].append(tok)
    if stack:
        raise ValueError("root expression never closes")
    if root is None:
        raise ValueError("no expression found")
    return root


def _head(node):
    return node[0][1] if (isinstance(node, list) and node
                          and node[0][0] == "atom") else None


# ------------------------------------------------------------------ numbers ---

# Measured against KiCad 10.0.5: 1e2, 100., .5, -.5, 1e+3, 1E+3 load; +100 and
# 1_00 are rejected. Python float() is wider, so validate lexically first.
_NUM = re.compile(r"-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?\Z")
_RANGE_MM = 2000.0


def _coord(tok, what):
    if tok[0] != "atom" or not _NUM.match(tok[1]):
        raise ValueError("%s: non-numeric coordinate %r" % (what, tok[1]))
    v = float(tok[1])
    if not math.isfinite(v):
        raise ValueError("%s: non-finite coordinate %r" % (what, tok[1]))
    if v == 0.0 and re.search(r"[1-9]", tok[1].split("e")[0].split("E")[0]):
        raise ValueError("%s: coordinate %r underflows to zero" % (what, tok[1]))
    if abs(v) > _RANGE_MM:
        raise ValueError("%s: coordinate %r outside the supported +-%g mm range"
                         % (what, tok[1], _RANGE_MM))
    return v


def _at_args(node, what, want=(2, 3)):
    args = node[1:]
    if len(args) not in want:
        raise ValueError("%s: malformed (at ...)" % what)
    return [_coord(t, what) for t in args]


# ------------------------------------------------------------------- walker ---

# Direct children a KiCad 10 footprint may carry. An unknown child is refused,
# never ignored: silent tolerance is exactly how a mangled "property" token
# would hide a binding field. Extend this list deliberately when KiCad does.
_FP_CHILD_LISTS = {
    "at", "descr", "tags", "property", "path", "sheetname", "sheetfile", "attr",
    "uuid", "tstamp", "layer", "autoplace_cost", "solder_mask_margin",
    "solder_paste_margin", "solder_paste_margin_ratio", "solder_paste_ratio",
    "clearance", "zone_connect", "thermal_width", "thermal_gap", "fp_text",
    "fp_text_box", "fp_line", "fp_rect", "fp_circle", "fp_arc", "fp_poly",
    "fp_curve", "pad", "model", "zone", "group", "embedded_fonts",
    "net_tie_pad_groups", "private_layers", "duplicate_pad_numbers_are_jumpers",
    "jumper_pad_groups", "embedded_files", "units", "locked", "tedit",
    "autoplace_cost90", "autoplace_cost180", "generator", "version",
    "component_classes",
}
_FP_CHILD_ATOMS = {"locked", "placed"}


def _parse_footprint(node):
    ref = None
    ats = []
    props = {}
    pads = []
    for ch in node[1:]:
        if isinstance(ch, tuple):
            if ch[0] == "str":
                continue  # the library id
            if ch[0] == "atom" and ch[1] in _FP_CHILD_ATOMS:
                continue
            raise ValueError("unexpected token %r inside footprint" % (ch[1],))
        h = _head(ch)
        if h == "variant":
            raise ValueError("footprint contains a (variant ...) block - variant "
                             "field overrides can hide bindings; the guard does "
                             "not support variant boards (resolve the variant "
                             "before grading)")
        if h is None or h not in _FP_CHILD_LISTS:
            raise ValueError("unrecognized footprint child %r - refusing rather "
                             "than silently ignoring it" % (h,))
        if h == "at":
            ats.append(ch)
        elif h == "property":
            # Real KiCad 10 output writes some property names as bare atoms
            # (e.g. ki_fp_filters); names may be str or atom, values must be str.
            if (len(ch) < 3 or ch[1][0] not in ("str", "atom")
                    or ch[2][0] != "str"):
                raise ValueError("malformed (property ...) inside footprint")
            k, v = ch[1][1], ch[2][1]
            if k in props:
                raise ValueError("duplicate property %r" % k)
            props[k] = v
        elif h == "pad":
            if len(ch) < 2 or ch[1][0] != "str":
                raise ValueError("malformed (pad ...) inside footprint")
            pname = ch[1][1]
            pats = [g for g in ch[2:]
                    if isinstance(g, list) and _head(g) == "at"]
            if len(pats) != 1:
                # KiCad keeps the LAST duplicate; grading the first would diverge
                # silently, so any count other than one refuses.
                raise ValueError("pad %r must have exactly one (at ...), found %d"
                                 % (pname, len(pats)))
            pads.append((pname, pats[0]))
    ref = props.get("Reference")
    if ref is None or len(ats) != 1:
        raise ValueError("footprint needs a Reference property and exactly one "
                         "(at ...) (found %d)" % len(ats))
    nums = _at_args(ats[0], "footprint %s" % ref)
    fx, fy = nums[0], nums[1]
    th = math.radians(nums[2] if len(nums) == 3 else 0.0)
    gpads = []
    for pname, pat in pads:
        pn = _at_args(pat, "%s pad %r" % (ref, pname))
        x, y = pn[0], pn[1]
        gx = fx + x * math.cos(th) + y * math.sin(th)
        gy = fy - x * math.sin(th) + y * math.cos(th)
        gpads.append((pname, gx, gy))
    return ref, {"pads": gpads, "props": props}


def parse_board(path):
    with open(path, encoding="utf-8") as f:  # strict UTF-8
        s = f.read()
    root = _read_tree(s)
    if _head(root) != "kicad_pcb":
        raise ValueError("root expression is not (kicad_pcb ...)")
    fps = {}
    for ch in root[1:]:
        if isinstance(ch, list) and _head(ch) == "footprint":
            ref, fp = _parse_footprint(ch)
            if ref in fps:
                raise ValueError("reference designator %r is ambiguous "
                                 "(multiple footprints)" % ref)
            fps[ref] = fp
    if not fps:
        raise ValueError("no footprints parsed")
    return fps


# ---------------------------------------------------------- KiCad load check ---

_KICAD_CLI_CANDIDATES = (
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
    "/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli", "/snap/bin/kicad.kicad-cli",
)


def _authenticated_cli(path):
    """Return path iff it runs and reports a KiCad version (prove identity)."""
    try:
        r = subprocess.run([path, "--version"], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    if r.returncode == 0 and re.search(r"\d+\.\d+\.\d+", r.stdout):
        return path
    return None


def _find_kicad_cli():
    env = os.environ.get("KICAD_CLI")
    if env:
        return _authenticated_cli(env) if os.path.isfile(env) else None
    import shutil
    onpath = shutil.which("kicad-cli")
    if onpath and _authenticated_cli(onpath):
        return onpath
    for c in _KICAD_CLI_CANDIDATES:
        if os.path.isfile(c) and _authenticated_cli(c):
            return c
    return None


def _kicad_loads(cli, board):
    """True iff the installed KiCad can load the board (authoritative)."""
    with tempfile.TemporaryDirectory() as td:
        out = os.path.join(td, "p.pos")
        r = subprocess.run(
            [cli, "pcb", "export", "pos", "-o", out, board],
            capture_output=True, text=True, timeout=120)
        return r.returncode == 0 and os.path.isfile(out)


# --------------------------------------------------------------------- main ---

BINDING_FIELDS = ("MaxDist", "SelfPad", "AnchorPad")


def run(argv) -> int:
    args = [a for a in argv[1:] if not a.startswith("--")]
    default_max = None
    min_expected = 0
    expect = None
    skip_load = False
    for o in (a for a in argv[1:] if a.startswith("--")):
        name, eq, val = o.partition("=")
        if name == "--skip-load-check" and not eq:
            skip_load = True
            continue
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

    # Snapshot once: the KiCad load check and the structural parser must grade
    # the SAME bytes - a mutable path between the two would be a TOCTOU hole.
    try:
        with open(args[0], "rb") as f:
            board_bytes = f.read()
    except OSError as exc:
        return verdict(2, "cannot read board: %s" % exc, "E-READ")
    snap_dir = tempfile.TemporaryDirectory()
    snap = os.path.join(snap_dir.name, "snapshot.kicad_pcb")
    with open(snap, "wb") as f:
        f.write(board_bytes)

    if not skip_load:
        cli = _find_kicad_cli()
        if cli is None:
            return verdict(2, "kicad-cli not found ($KICAD_CLI or standard paths) - "
                              "the authoritative load check cannot run; "
                              "--skip-load-check exists for development only",
                           "E-TOOL")
        try:
            loadable = _kicad_loads(cli, snap)
        except (OSError, subprocess.SubprocessError) as exc:
            return verdict(2, "KiCad load check could not run: %s" % exc, "E-TOOL")
        if not loadable:
            return verdict(2, "the installed KiCad refuses to load this board - "
                              "not grading what the authority rejects", "E-LOAD")

    try:
        fps = parse_board(snap)
    except OSError as exc:
        return verdict(2, "cannot read board snapshot: %s" % exc, "E-READ")
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
    note = " (advisory: KiCad load check skipped)" if skip_load else ""
    return verdict(0, "%d binding(s) within budget; tightest margin %s->%s "
                      "%.2fmm of %.1fmm%s"
                   % (len(results), worst[0], worst[1], worst[2], worst[3], note))


def main(argv) -> int:
    try:
        return run(argv)
    except Exception as exc:  # noqa: BLE001 - a guard must never die without a verdict
        return verdict(2, "internal error, refusing to grade: %r" % exc, "E-INTERNAL")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
