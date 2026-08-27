#!/usr/bin/env python3
"""Calibration harness for kicad_functional_proximity.py (GUARDS.md contract).

Builds deterministic fixture boards inline (no external files, no randomness) and
asserts the exact verdict, exit code, and — for refusals — the stable branch id.
Boundary calibration exercises just-inside and just-outside the SAME threshold.

Run:  python3 scripts/tests/test_functional_proximity.py   (exit 0 = all pass)
"""

import os
import subprocess
import sys
import tempfile

GUARD = os.path.join(os.path.dirname(__file__), "..", "kicad_functional_proximity.py")


def fp(ref, x, y, rot=0.0, layer="F.Cu", props=(), pads=(("1", 0.0, 0.0),)):
    p = "".join('(property "%s" "%s" (at 0 0 0) (layer "F.Fab") hide '
                '(effects (font (size 1 1) (thickness 0.15)))) ' % kv for kv in props)
    pd = "".join('(pad "%s" thru_hole circle (at %s %s) (size 1 1) (drill 0.5) '
                 '(layers "*.Cu")) ' % (n, px, py) for n, px, py in pads)
    return ('(footprint "test:FP" (layer "%s") (at %s %s %s) '
            '(property "Reference" "%s" (at 0 0 0) (layer "F.SilkS") '
            '(effects (font (size 1 1) (thickness 0.15)))) %s%s)'
            % (layer, x, y, rot, ref, p, pd))


def board(*footprints):
    return ('(kicad_pcb (version 20260206) (generator "test") '
            + " ".join(footprints) + ")")


ANCHOR = fp("Q1", 100, 100, pads=(("G", 0, 0), ("D", 5, 0)))

CASES = [
    # name, board text (None = missing file), argv extras, want exit, want substring
    ("bad_over_budget",
     board(ANCHOR, fp("S1", 100, 115.1, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     [], 1, "S1→Q1 15.10mm > 15.0mm"),
    ("good_under_budget",
     board(ANCHOR, fp("S1", 100, 114.9, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     [], 0, "14.90mm of 15.0mm"),
    ("rotated_satellite",  # pad (5,0) rotated 90° at (110,100) → (110,95); Q1.D (105,100) → 7.07mm
     board(ANCHOR, fp("R1", 110, 100, rot=90, props=(("Anchor", "Q1"), ("MaxDist", "8")),
                      pads=(("1", 5, 0),))),
     [], 0, "7.07mm of 8.0mm"),
    ("backside_satellite",  # B.Cu at (130,100), pad (2,0) → (132,100); Q1.D (105,100) → 27mm
     board(ANCHOR, fp("B1", 130, 100, layer="B.Cu",
                      props=(("Anchor", "Q1"), ("MaxDist", "26")), pads=(("1", 2, 0),))),
     [], 1, "27.00mm > 26.0mm"),
    ("pad_selector",  # AnchorPad=G forces the far pad: (100,110)→(100,100)=10 > 8; any-pad would be 5.59
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "8"),
                                             ("AnchorPad", "G")))),
     [], 1, "10.00mm > 8.0mm"),
    ("pad_selector_wrong_name",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "8"),
                                             ("AnchorPad", "NOPE")))),
     [], 2, "[E-PADS]"),
    ("orphan_maxdist_is_refused",  # deleting Anchor from a failing binding must not improve verdict
     board(ANCHOR, fp("S1", 100, 199, props=(("MaxDist", "15"),))),
     [], 2, "[E-ORPHAN]"),
    ("nan_budget_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "nan")))),
     [], 2, "[E-BUDGET]"),
    ("negative_budget_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "-5")))),
     [], 2, "[E-BUDGET]"),
    ("no_budget_no_default_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"),))),
     [], 2, "[E-BUDGET]"),
    ("no_budget_with_default_ok",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"),))),
     ["--default-max-mm=12"], 0, "10.00mm of 12.0mm"),
    ("dangling_anchor",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q9"), ("MaxDist", "15")))),
     [], 2, "[E-ANCHOR]"),
    ("self_anchor",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "S1"), ("MaxDist", "15")))),
     [], 2, "[E-SELF]"),
    ("empty_is_unverified", board(ANCHOR), [], 2, "[E-EMPTY]"),
    ("expect_mismatch_missing",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--expect=S1,S2"], 2, "[E-EXPECT]"),
    ("expect_mismatch_extra",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--expect=S9"], 2, "[E-EXPECT]"),
    ("expect_exact_ok",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--expect=S1"], 0, "1 binding(s) within budget"),
    ("min_expected_short",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--min-expected=3"], 2, "[E-EXPECT]"),
    ("junk_root_refused", "junk " + board(ANCHOR), [], 2, "[E-PARSE]"),
    ("malformed_at_refused",
     board(ANCHOR).replace("(at 100 100 0.0)", "(at 10..0 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("duplicate_ref_refused", board(ANCHOR, ANCHOR), [], 2, "[E-PARSE]"),
    ("duplicate_property_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("Anchor", "Q1"),
                                             ("MaxDist", "15")))),
     [], 2, "[E-PARSE]"),
    ("non_utf8_refused", None, [], 2, "[E-PARSE]"),  # bytes written by the runner
    ("spaced_option_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--min-expected"], 2, "[E-ARGS]"),
    ("missing_file_refused", "MISSING", [], 2, "[E-READ]"),
]


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as td:
        for name, text, extra, want_exit, want_sub in CASES:
            path = os.path.join(td, name + ".kicad_pcb")
            if text == "MISSING":
                path = os.path.join(td, "does-not-exist.kicad_pcb")
            elif text is None:
                with open(path, "wb") as f:
                    f.write(b"(kicad_pcb \xff\xfe broken")
            else:
                with open(path, "w") as f:
                    f.write(text)
            r = subprocess.run([sys.executable, GUARD, path] + extra,
                               capture_output=True, text=True)
            out = (r.stdout + r.stderr).strip()
            ok = r.returncode == want_exit and want_sub in out and out.count("\n") == 0
            print("%-28s %s  (exit %d)  %s" % (name, "ok" if ok else "FAIL",
                                               r.returncode, out[:110]))
            if not ok:
                failures.append((name, want_exit, want_sub, r.returncode, out))
    if failures:
        print("\n%d/%d cases FAILED" % (len(failures), len(CASES)))
        return 1
    print("\nall %d cases pass" % len(CASES))
    return 0


if __name__ == "__main__":
    sys.exit(main())
