#!/usr/bin/env python3
"""Calibration for design_ledger.

The inventory is the docstring's own claims, negative ones first. The interval
arithmetic gets its own algebraic cases, because a wrong bound produces a
plausible margin and no error at all -- the worst shape of defect a LEDGERS-tier
guard can have.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import design_ledger as L  # noqa: E402

I = L.Interval


def var(source="datasheet", origin="datasheet p.1", spec=None, actual=None,
        unit=None):
    d = {"source": source, "origin": origin}
    if spec is not None:
        d["spec"] = spec
    if actual is not None:
        d["actual"] = actual
    if unit:
        d["unit"] = unit
    return d


def ledger(variables, constraints, **kw):
    d = {"schema": "kicad-design/design-ledger/1", "design": "t",
         "variables": variables, "constraints": constraints}
    d.update(kw)
    return d


def con(cid, expr, origin="POWER.md section X", requirement="r"):
    return {"id": cid, "expr": expr, "origin": origin,
            "requirement": requirement}


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)

    def tearDown(self):
        self._td.cleanup()

    def write(self, doc, name="ledger.json"):
        p = self.d / name
        p.write_text(json.dumps(doc) if isinstance(doc, (dict, list))
                     else doc, encoding="utf-8")
        return p

    def run_cli(self, *argv):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = L.main(["design_ledger.py"] + list(argv))
        return rc, buf.getvalue()


# ------------------------------------------------------------- arithmetic ---

class TestIntervalArithmetic(unittest.TestCase):
    def test_subtraction_of_two_intervals_widens(self):
        self.assertEqual(I(10, 20) - I(10, 20), I(-10, 10))

    def test_a_singleton_cancels_against_itself(self):
        self.assertEqual(I(5) - I(5), I(0))

    def test_multiplication_takes_the_extreme_corner_over_signs(self):
        self.assertEqual(I(-2, 3) * I(-4, 5), I(-12, 15))

    def test_division_by_an_interval_spanning_zero_is_refused(self):
        with self.assertRaises(L.LedgerError):
            I(1, 2) / I(-1, 1)
        with self.assertRaises(L.LedgerError):
            I(1, 2) / I(0, 1)

    def test_an_inverted_interval_is_refused(self):
        with self.assertRaises(L.LedgerError):
            I(3, 1)

    def test_an_infinite_bound_is_refused(self):
        with self.assertRaises(L.LedgerError):
            I(0, float("inf"))

    def test_a_nan_bound_is_refused(self):
        with self.assertRaises(L.LedgerError):
            I(float("nan"), 1)

    def test_a_boolean_is_not_a_bound(self):
        with self.assertRaises(L.LedgerError):
            I(True, 1)


class TestCorrelation(unittest.TestCase):
    """The rule from atopile's solver README, made executable."""

    def _both(self, expr, env):
        lhs, op, rhs, _ = L.parse_constraint(expr + " <= 0")
        return L.eval_interval(lhs, env), L.eval_corners(lhs, env)

    def test_uncorrelated_X_minus_X_widens_correlated_cancels(self):
        env = {"X": I(10, 20)}
        unc, cor = self._both("X - X", env)
        self.assertEqual(unc, I(-10, 10))
        self.assertEqual(cor, I(0, 0))

    def test_the_uncorrelated_bound_always_contains_the_correlated_one(self):
        env = {"A": I(1, 2), "B": I(3, 5)}
        for expr in ["A * B - A", "A + B - A - B", "A / B + A",
                     "(A - B) * (A + B)"]:
            unc, cor = self._both(expr, env)
            self.assertLessEqual(unc.lo, cor.lo, expr)
            self.assertGreaterEqual(unc.hi, cor.hi, expr)

    def test_a_variable_used_once_gives_identical_results(self):
        env = {"A": I(1, 2), "B": I(3, 5)}
        unc, cor = self._both("A * B + 1", env)
        self.assertAlmostEqual(unc.lo, cor.lo)
        self.assertAlmostEqual(unc.hi, cor.hi)

    def test_too_many_variables_reports_unavailable_not_a_thin_sample(self):
        names = ["v%d" % i for i in range(L.MAX_CORNER_VARS + 1)]
        expr = " + ".join(names) + " <= 0"
        lhs, _, _, _ = L.parse_constraint(expr)
        env = {n: I(0, 1) for n in names}
        self.assertIsNone(L.eval_corners(lhs, env))


class TestExpressionLanguage(unittest.TestCase):
    def test_a_non_comparison_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("a + b")

    def test_a_chained_comparison_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("a <= b <= c")

    def test_equality_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("a == b")

    def test_a_function_call_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("min(a, b) <= c")

    def test_an_attribute_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("a.b <= c")

    def test_a_power_operator_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("a ** 2 <= c")

    def test_a_string_literal_is_refused(self):
        with self.assertRaises(L.LedgerError):
            L.parse_constraint("'x' <= c")

    def test_the_four_arithmetic_operators_and_unary_minus_parse(self):
        lhs, op, rhs, names = L.parse_constraint("-(a + b) * c / d < 2")
        self.assertEqual(op, "<")
        self.assertEqual(names, {"a", "b", "c", "d"})


class TestSatisfaction(unittest.TestCase):
    def test_worst_case_satisfaction_uses_the_touching_ends(self):
        self.assertTrue(L.satisfied(I(1, 2), "<=", I(2, 3)))
        self.assertFalse(L.satisfied(I(1, 2), "<", I(2, 3)))   # 2 < 2 is false
        self.assertFalse(L.satisfied(I(1, 3), "<=", I(2, 4)))

    def test_margin_is_positive_exactly_when_satisfied(self):
        for a, op, b in [(I(1, 2), "<=", I(2, 3)), (I(1, 3), "<=", I(2, 4)),
                         (I(5, 6), ">=", I(1, 2)), (I(0, 6), ">=", I(1, 2))]:
            ok = L.satisfied(a, op, b)
            m = L.margin(a, op, b)
            self.assertEqual(ok, m >= 0, (a, op, b, m))


# ------------------------------------------------------------ ledger rules ---

class TestLedgerRefusals(_Tmp):
    def test_a_missing_file_is_unverified(self):
        rc, out = self.run_cli(str(self.d / "nope.json"))
        self.assertEqual(rc, 2)
        self.assertIn("E-LEDGER", out)

    def test_invalid_json_is_unverified(self):
        p = self.write("{not json", "x.json")
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)

    def test_a_foreign_schema_is_unverified_not_empty(self):
        p = self.write({"variables": {}, "constraints": []})
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("schema", out)

    def test_zero_constraints_is_unverified_not_a_pass(self):
        p = self.write(ledger({}, []))
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("E-EMPTY", out)

    def test_an_untagged_source_is_refused(self):
        p = self.write(ledger(
            {"a": {"origin": "x", "actual": 1}}, [con("C", "a <= 2")]))
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("source", out)

    def test_a_variable_without_an_origin_is_refused(self):
        p = self.write(ledger(
            {"a": {"source": "user", "actual": 1}}, [con("C", "a <= 2")]))
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("origin", out)

    def test_a_constraint_without_an_origin_is_refused(self):
        p = self.write(ledger(
            {"a": var(actual=1)},
            [{"id": "C", "expr": "a <= 2"}]))
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("provenance", out)

    def test_duplicate_constraint_ids_are_refused(self):
        p = self.write(ledger(
            {"a": var(actual=1)},
            [con("C", "a <= 2"), con("C", "a <= 3")]))
        rc, out = self.run_cli(str(p))
        self.assertEqual(rc, 2)
        self.assertIn("duplicate", out)

    def test_min_constraints_below_one_is_refused(self):
        p = self.write(ledger({"a": var(actual=1)}, [con("C", "a <= 2")]))
        rc, out = self.run_cli(str(p), "--min-constraints=0")
        self.assertEqual(rc, 2)

    def test_a_truncated_ledger_fails_the_min_constraints_tripwire(self):
        p = self.write(ledger({"a": var(actual=1)}, [con("C", "a <= 2")]))
        self.assertEqual(self.run_cli(str(p), "--min-constraints=3")[0], 2)


class TestBaselineAndKnownBad(_Tmp):
    def base(self):
        return ledger(
            {"I_max": var("user", "spec sheet: 20 A continuous",
                          spec=20, actual=20),
             "R_sense": var("picked",
                            "BOM R12 WSLP2726L1000FEA, datasheet p.2",
                            spec=[0.00095, 0.00105],
                            actual=[0.00099, 0.00101]),
             "V_burden_max": var("datasheet", "ADS1262 SBAS661C table 7.5",
                                 spec=0.05, actual=0.05)},
            [con("SENSE-BURDEN", "I_max * R_sense <= V_burden_max")])

    def test_the_baseline_passes(self):
        rc, out = self.run_cli(str(self.write(self.base())))
        self.assertEqual(rc, 0, out)
        self.assertIn("LEDGER-PASS", out)

    def test_a_part_just_outside_the_budget_fails(self):
        # Known-bad just beyond the boundary, per GUARDS: 20 A * 2.51 mOhm
        # = 50.2 mV against a 50 mV limit.
        d = self.base()
        d["variables"]["R_sense"]["actual"] = [0.00249, 0.00251]
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 1, out)
        self.assertIn("LEDGER-FAIL", out)
        self.assertIn("SENSE-BURDEN", out)

    def test_a_part_just_inside_the_budget_stays_quiet(self):
        d = self.base()
        d["variables"]["R_sense"]["actual"] = [0.00249, 0.00249]
        self.assertEqual(self.run_cli(str(self.write(d)))[0], 0)

    def test_restoring_the_part_restores_the_baseline(self):
        d = self.base()
        d["variables"]["R_sense"]["actual"] = [0.00249, 0.00251]
        self.assertEqual(self.run_cli(str(self.write(d, "bad.json")))[0], 1)
        self.assertEqual(self.run_cli(str(self.write(self.base())))[0], 0)

    def test_the_failure_names_the_origins_of_both_sides(self):
        d = self.base()
        # 20 A * 2.51 mOhm = 50.2 mV against a 50 mV limit
        d["variables"]["R_sense"]["actual"] = [0.00251, 0.00251]
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 1)
        self.assertIn("WSLP2726L1000FEA", out)
        self.assertIn("SBAS661C", out)
        self.assertIn("[picked]", out)
        self.assertIn("[datasheet]", out)


class TestTiersAreIndependent(_Tmp):
    def test_a_sourcing_error_fails_as_built_while_intent_passes(self):
        d = ledger(
            {"a": var("derived", "derivation X", spec=1, actual=9),
             "b": var("datasheet", "limit Y", spec=5, actual=5)},
            [con("C", "a <= b")])
        rc, out = self.run_cli(str(self.write(d)), "--tier=as-built")
        self.assertEqual(rc, 1)
        self.assertIn("intent tier (report only)", out)
        self.assertIn("as-built tier (GATED)", out)

    def test_gating_on_intent_ignores_the_as_built_violation(self):
        d = ledger(
            {"a": var("derived", "derivation X", spec=1, actual=9),
             "b": var("datasheet", "limit Y", spec=5, actual=5)},
            [con("C", "a <= b")])
        self.assertEqual(self.run_cli(str(self.write(d)), "--tier=intent")[0],
                         0)

    def test_both_gates_on_the_worse_of_the_two(self):
        d = ledger(
            {"a": var("derived", "derivation X", spec=1, actual=9),
             "b": var("datasheet", "limit Y", spec=5, actual=5)},
            [con("C", "a <= b")])
        self.assertEqual(self.run_cli(str(self.write(d)), "--tier=both")[0], 1)


class TestUnevaluableNeverPasses(_Tmp):
    def test_a_missing_actual_is_unverified_not_inherited_from_spec(self):
        d = ledger(
            {"a": var("derived", "o", spec=1),
             "b": var("datasheet", "o", spec=5, actual=5)},
            [con("C", "a <= b")])
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 2, out)
        self.assertIn("E-NOVALUE", out)
        # and the intent tier, which HAS both spec values, still passes
        self.assertIn("intent tier (report only): 1 constraint(s) -- 1 PASS",
                      out)

    def test_an_unverified_constraint_is_present_in_the_report_not_omitted(self):
        d = ledger(
            {"a": var("derived", "o", spec=1),
             "b": var("datasheet", "o", spec=5, actual=5)},
            [con("C", "a <= b")])
        out = self.d / "rows.json"
        self.run_cli(str(self.write(d)), "--json=%s" % out)
        rows = json.loads(out.read_text())["tiers"]["as-built"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["state"], "UNVERIFIED")

    def test_an_undefined_variable_is_unverified_not_a_skip(self):
        d = ledger({"a": var(actual=1)}, [con("C", "a <= zzz")])
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 2)
        self.assertIn("E-UNDEF", out)

    def test_unverified_outranks_fail(self):
        d = ledger(
            {"a": var("derived", "o", actual=9),
             "b": var("datasheet", "o", actual=5),
             "c": var("derived", "o", spec=1)},
            [con("C1", "a <= b"), con("C2", "c <= b")])
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 2, out)

    def test_a_division_by_a_zero_spanning_interval_is_unverified(self):
        d = ledger(
            {"a": var("derived", "o", actual=1),
             "b": var("derived", "o", actual=[-1, 1])},
            [con("C", "a / b <= 10")])
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 2)
        self.assertIn("E-ARITH", out)


class TestCorrelationIsReportedNeverAppliedSilently(_Tmp):
    def test_the_verdict_comes_from_the_uncorrelated_bound(self):
        # a - a <= 0 is trivially true for one physical quantity, and the
        # uncorrelated bound is [-2, 2], so the gate FAILS. That is the
        # intended, conservative answer.
        d = ledger({"a": var("picked", "part P", actual=[9, 11])},
                   [con("C", "a - a <= 0")])
        rc, out = self.run_cli(str(self.write(d)))
        self.assertEqual(rc, 1, out)

    def test_the_report_says_correlation_is_the_reason_and_names_the_variable(self):
        d = ledger({"a": var("picked", "part P", actual=[9, 11])},
                   [con("C", "a - a <= 0")])
        _, out = self.run_cli(str(self.write(d)))
        self.assertIn("CORRELATION MATTERS HERE", out)
        self.assertIn("a appear(s) more than once", out)

    def test_no_correlation_note_when_no_variable_repeats(self):
        d = ledger({"a": var("picked", "part P", actual=[9, 11]),
                    "b": var("picked", "part Q", actual=[9, 11])},
                   [con("C", "a - b <= 5")])
        _, out = self.run_cli(str(self.write(d)))
        self.assertNotIn("CORRELATION MATTERS HERE", out)

    def test_the_json_carries_the_correlated_numbers_beside_the_verdict(self):
        d = ledger({"a": var("picked", "part P", actual=[9, 11])},
                   [con("C", "a - a <= 0")])
        out = self.d / "rows.json"
        self.run_cli(str(self.write(d)), "--json=%s" % out)
        row = json.loads(out.read_text())["tiers"]["as-built"][0]
        self.assertEqual(row["meets"], False)
        self.assertEqual(row["correlated_meets"], True)
        self.assertEqual(row["repeated_variables"], ["a"])


class TestReporting(_Tmp):
    def test_the_constraint_count_in_the_summary_is_derived(self):
        d = ledger({"a": var(actual=1), "b": var(actual=5)},
                   [con("C1", "a <= b"), con("C2", "a <= 100")])
        _, out = self.run_cli(str(self.write(d)))
        self.assertIn("2 constraint(s)", out)
        self.assertIn("over 2 variable(s)", out)

    def test_a_bad_invocation_is_unevaluable_not_a_pass(self):
        with self.assertRaises(SystemExit) as cm:
            L.main(["design_ledger.py"])
        self.assertIn("UNVERIFIED", str(cm.exception))

    def test_an_unwritable_json_target_is_unverified(self):
        d = ledger({"a": var(actual=1)}, [con("C", "a <= 2")])
        rc, _ = self.run_cli(str(self.write(d)),
                             "--json=%s" % (self.d / "no" / "dir" / "x.json"))
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()
