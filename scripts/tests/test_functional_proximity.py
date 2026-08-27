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
     [], 1, "S1->Q1 15.10mm > 15.0mm"),
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
    ("fused_root_token_refused",
     board(ANCHOR).replace("(kicad_pcb ", "(kicad_pcbjunk ", 1), [], 2, "[E-PARSE]"),
    ("unbalanced_refused", board(ANCHOR)[:-1], [], 2, "[E-PARSE]"),
    ("malformed_at_refused",
     board(ANCHOR).replace("(at 100 100 0.0)", "(at 10..0 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("nonnumeric_at_refused",
     board(ANCHOR).replace("(at 100 100 0.0)", "(at BAD 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("malformed_pad_at_refused",
     board(ANCHOR).replace("(at 5 0)", "(at x5 0)", 1), [], 2, "[E-PARSE]"),
    ("selfpad_forces_far_pad",  # S1 pads at (100,110) and (100,120); SelfPad picks the far one
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))),
     [], 1, "20.00mm > 15.0mm"),
    ("selfpad_wrong_name",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "NOPE")))),
     [], 2, "[E-PADS]"),
    ("negative_min_expected_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--min-expected=-1"], 2, "[E-ARGS]"),
    ("ascii_stdout_still_verdicts",  # PYTHONIOENCODING=ascii must not suppress the verdict
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     [], 0, "FUNC-PROX-PASS"),
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
    # --- mutation-killing fixtures (round-3 review) ---
    ("exact_budget_equality_passes",  # kills d > budget -> d >= budget
     board(ANCHOR, fp("S1", 100, 115, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     [], 0, "15.00mm of 15.0mm"),
    ("asymmetric_rotation",  # pad (5,2) rot 90 at (110,100) -> (112,95); Q1.D -> 8.60mm; a sign flip changes this
     board(ANCHOR, fp("R2", 110, 100, rot=90, props=(("Anchor", "Q1"), ("MaxDist", "9")),
                      pads=(("1", 5, 2),))),
     [], 0, "8.60mm of 9.0mm"),
    ("footprint_after_root_refused",  # kills root-containment removal
     board(ANCHOR) + " " + fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))),
     ["--expect=S1"], 2, "[E-PARSE]"),
    ("balanced_trailing_text_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))) + " (x)",
     [], 2, "[E-PARSE]"),
    ("nan_coordinate_refused",  # kills numeric-grammar or finiteness relaxation
     board(ANCHOR).replace("(at 100 100 0.0)", "(at nan 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("plus_sign_coordinate_refused",  # KiCad rejects '+100'; Python float() accepts it
     board(ANCHOR).replace("(at 100 100 0.0)", "(at +100 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("paren_inside_string_ok",  # kills string-blind parenthesis counting
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("Note", "a)b(c")))),
     [], 0, "FUNC-PROX-PASS"),
    ("maxdist_beats_default",  # kills --default-max-mm overriding a declared MaxDist
     board(ANCHOR, fp("S1", 100, 120, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--default-max-mm=30"], 1, "20.00mm > 15.0mm"),
    ("two_bindings_one_fails",  # every other distance case grades a single binding
     board(ANCHOR,
           fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))),
           fp("S2", 100, 130, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     [], 1, "1 of 2 binding(s) exceed budget: S2->Q1 30.00mm"),
    ("tab_separated_property_honored",  # legal KiCad whitespace must not hide a selector
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10))).replace(
         '(property "SelfPad" "2"', '(property\t"SelfPad"\t"2"')),
     [], 1, "20.00mm > 15.0mm"),
    ("ascii_stdout_on_refusal",  # ascii env on the refusal path too
     "junk " + board(ANCHOR), [], 2, "[E-PARSE]"),
    ("newline_in_option_stays_one_line",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))),
     ["--oops\nnext=1"], 2, "[E-ARGS]"),
    # --- round-4: KiCad-accepted number forms must load; wider Python forms must not ---
    ("exponent_and_dot_forms_accepted",  # (at 1e2 110.) == (100,110) -> 10mm of 15
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))))
     .replace("(at 100 110 0.0)", "(at 1e2 110. 0.0)", 1),
     [], 0, "10.00mm of 15.0mm"),
    ("bare_dot_decimal_accepted",  # (at .5 ...) must parse like KiCad does
     board(ANCHOR, fp("S1", 0.5, 110, props=(("Anchor", "Q1"), ("MaxDist", "150"))))
     .replace("(at 0.5 110 0.0)", "(at .5 110 0.0)", 1),
     [], 0, "FUNC-PROX-PASS"),
    ("negative_coordinate_accepted",  # kills removing the leading '-' from the grammar
     board(ANCHOR, fp("S1", -0.5, 110, props=(("Anchor", "Q1"), ("MaxDist", "150"))))
     .replace("(at -0.5 110 0.0)", "(at -.5 110 0.0)", 1),
     [], 0, "101.00mm of 150.0mm"),
    ("underscore_number_refused",  # Python float() accepts 1_00; KiCad rejects it
     board(ANCHOR).replace("(at 100 100 0.0)", "(at 1_00 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("nbsp_separator_refused",  # KiCad rejects NBSP separators; Python \s would admit them
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))).replace(
         '(property "Anchor" "Q1"', '(property "Anchor" "Q1"'),
     [], 2, "[E-PARSE]"),
    # --- round-5: separators, exponent forms, underflow/range, unicode strings ---
    ("vt_separator_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))).replace(
         '(property "SelfPad"', '(property\x0b"SelfPad"', 1),
     [], 2, "[E-PARSE]"),
    ("punct_separator_refused",  # (property!"SelfPad" must refuse, never silently ignore
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))).replace(
         '(property "SelfPad"', '(property!"SelfPad"', 1),
     [], 2, "[E-PARSE]"),
    ("del_char_refused",
     board(ANCHOR).replace('(layer "F.Cu")', '(layer\x7f"F.Cu")', 1),
     [], 2, "[E-PARSE]"),
    ("exponent_plus_and_uppercase_ok",  # KiCad accepts 1e+2 / 1E2; distances must grade
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))))
     .replace("(at 100 110 0.0)", "(at 1e+2 1.1E2 0.0)", 1),
     [], 0, "10.00mm of 15.0mm"),
    ("underflow_to_zero_refused",  # KiCad rejects 1e-324; Python floats it to 0.0
     board(ANCHOR).replace("(at 100 100 0.0)", "(at 1e-324 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("outside_range_refused",  # KiCad clamps ~2147mm; the guard refuses instead of diverging
     board(ANCHOR).replace("(at 100 100 0.0)", "(at 1e308 100 0.0)", 1),
     [], 2, "[E-PARSE]"),
    ("multiline_board_ok",  # real newlines between elements (kills CR/LF separator removal)
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))))
     .replace(") (", ")\n("),
     [], 0, "10.00mm of 15.0mm"),
    ("unicode_in_quoted_string_ok",  # quoted UTF-8 values stay legal
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("Note", "1000\u00b5F")))),
     [], 0, "10.00mm of 15.0mm"),
    # --- round-6: duplicate (at), separators on SelfPad, variant, atom value ---
    ("duplicate_footprint_at_refused",  # KiCad uses the LAST at; grading the first diverges
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))))
     .replace('(at 100 110 0.0) ', '(at 100 110 0.0) (at 100 199 0.0) ', 1),
     ["--expect=S1"], 2, "[E-PARSE]"),
    ("duplicate_pad_at_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"))))
     .replace('(at 0.0 0.0) (size 1 1)', '(at 0.0 0.0) (at 0 89) (size 1 1)', 1),
     ["--expect=S1"], 2, "[E-PARSE]"),
    ("ff_separator_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))).replace(
         '(property "SelfPad"', '(property\x0c"SelfPad"', 1),
     [], 2, "[E-PARSE]"),
    ("nul_separator_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))).replace(
         '(property "SelfPad"', '(property\x00"SelfPad"', 1),
     [], 2, "[E-PARSE]"),
    ("del_on_selfpad_refused",
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15"),
                                             ("SelfPad", "2")),
                      pads=(("1", 0, 0), ("2", 0, 10)))).replace(
         '(property "SelfPad"', '(property\x7f"SelfPad"', 1),
     [], 2, "[E-PARSE]"),
    ("variant_block_refused",  # variant overrides can hide bindings; never allowlist blindly
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))
           .replace("(pad ", '(variant "V1" (property "Anchor" "QX")) (pad ', 1)),
     [], 2, "[E-PARSE]"),
    ("atom_property_value_refused",  # (property "Anchor" Q1) unquoted value
     board(ANCHOR, fp("S1", 100, 110, props=(("Anchor", "Q1"), ("MaxDist", "15")))
           .replace('(property "Anchor" "Q1"', '(property "Anchor" Q1_ATOM_VALUE (at 0 0 0) (layer "F.Fab") hide (effects (font (size 1 1) (thickness 0.15)))) (property "AnchorX" "x"', 1)),
     [], 2, "[E-PARSE]"),
]


TAGS = {0: "FUNC-PROX-PASS:", 1: "FUNC-PROX-FAIL:", 2: "FUNC-PROX-UNVERIFIED:"}


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
            env = dict(os.environ)
            if name in ("ascii_stdout_still_verdicts", "ascii_stdout_on_refusal"):
                env["PYTHONIOENCODING"] = "ascii"
            r = subprocess.run([sys.executable, GUARD, path, "--skip-load-check"] + extra,
                               capture_output=True, text=True, env=env)
            out = r.stdout.rstrip("\n")
            # The verdict is exactly one newline-terminated stdout line; stderr empty.
            ok = (r.returncode == want_exit and want_sub in out
                  and r.stdout.endswith("\n") and "\n" not in out
                  and r.stderr == ""
                  and out.startswith(TAGS[want_exit])
                  and (want_exit != 1 or "[OVER-BUDGET]" in out))
            print("%-30s %s  (exit %d)  %s" % (name, "ok" if ok else "FAIL",
                                               r.returncode, out[:110]))
            if not ok:
                failures.append((name, want_exit, want_sub, r.returncode, out))
    # E-INTERNAL: unreachable via input by construction, so force it in-process.
    import importlib.util
    import io
    spec = importlib.util.spec_from_file_location("fp_guard", GUARD)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.run = lambda _argv: 1 // 0  # type: ignore[attr-defined]
    buf = io.StringIO()
    old = sys.stdout
    sys.stdout = buf
    try:
        rc = mod.main(["guard", "whatever"])
    finally:
        sys.stdout = old
    out = buf.getvalue().strip()
    ok = rc == 2 and "[E-INTERNAL]" in out and out.startswith(TAGS[2]) \
        and out.count("\n") == 0
    print("%-30s %s  (exit %d)  %s" % ("internal_error_still_verdicts",
                                       "ok" if ok else "FAIL", rc, out[:110]))
    if not ok:
        failures.append(("internal_error_still_verdicts", 2, "[E-INTERNAL]", rc, out))
    # Load-check layer: bogus $KICAD_CLI must refuse E-TOOL (no --skip-load-check).
    with tempfile.TemporaryDirectory() as td2:
        bp = os.path.join(td2, "b.kicad_pcb")
        with open(bp, "w") as f:
            f.write(board(ANCHOR))
        env = dict(os.environ)
        env["KICAD_CLI"] = "/nonexistent/kicad-cli"
        r = subprocess.run([sys.executable, GUARD, bp], capture_output=True,
                           text=True, env=env)
        ok = r.returncode == 2 and "[E-TOOL]" in r.stdout and r.stderr == ""
        print("%-30s %s  (exit %d)  %s" % ("bogus_kicad_cli_e_tool",
                                           "ok" if ok else "FAIL", r.returncode,
                                           r.stdout.strip()[:110]))
        if not ok:
            failures.append(("bogus_kicad_cli_e_tool", 2, "[E-TOOL]",
                             r.returncode, r.stdout))
        # A file that exists but is not KiCad must fail identity -> E-TOOL,
        # never a fake load approval.
        for fake in ("/usr/bin/true", "/usr/bin/false"):
            env2 = dict(os.environ)
            env2["KICAD_CLI"] = fake
            r = subprocess.run([sys.executable, GUARD, bp], capture_output=True,
                               text=True, env=env2)
            name2 = "fake_cli_%s" % os.path.basename(fake)
            ok = r.returncode == 2 and "[E-TOOL]" in r.stdout and r.stderr == ""
            print("%-30s %s  (exit %d)  %s" % (name2, "ok" if ok else "FAIL",
                                               r.returncode, r.stdout.strip()[:110]))
            if not ok:
                failures.append((name2, 2, "[E-TOOL]", r.returncode, r.stdout))
        # With a real kicad-cli present, an unloadable synthetic fixture must E-LOAD.
        import importlib.util as _ilu
        spec2 = _ilu.spec_from_file_location("fp_guard2", GUARD)
        assert spec2 is not None and spec2.loader is not None
        g2 = _ilu.module_from_spec(spec2)
        spec2.loader.exec_module(g2)
        cli = g2._find_kicad_cli()
        if cli:
            badp = os.path.join(td2, "bad.kicad_pcb")
            with open(badp, "w") as f:
                f.write(board(ANCHOR).replace("(at 100 100 0.0)",
                                              "(at BAD 100 0.0)", 1))
            r = subprocess.run([sys.executable, GUARD, badp], capture_output=True,
                               text=True)
            ok = r.returncode == 2 and "[E-LOAD]" in r.stdout and r.stderr == ""
            print("%-30s %s  (exit %d)  %s" % ("unloadable_board_e_load",
                                               "ok" if ok else "FAIL", r.returncode,
                                               r.stdout.strip()[:110]))
            if not ok:
                failures.append(("unloadable_board_e_load", 2, "[E-LOAD]",
                                 r.returncode, r.stdout))
        else:
            print("%-30s skipped (no kicad-cli found)" % "unloadable_board_e_load")
    total = len(CASES) + 1 + 1 + 2 + (1 if cli else 0)
    if failures:
        print("\n%d/%d cases FAILED" % (len(failures), total))
        return 1
    print("\nall %d cases pass" % total)
    return 0


if __name__ == "__main__":
    sys.exit(main())
