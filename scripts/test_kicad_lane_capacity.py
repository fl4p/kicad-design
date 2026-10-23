"""Calibration for kicad_lane_capacity.py.

Geometry and demand arithmetic run under any Python. The CLI tier needs
KiCad's ``pcbnew`` and SKIPS LOUDLY without it; run it as

    /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3 \
        -m unittest test_kicad_lane_capacity

The board-level calibration (o2-probe Rev B, three real rule sets, one absurd
one) is recorded in ../reviews/2026-09-12-lane-capacity-map.md rather than
here: it depends on a board that is not a fixture of this skill.
"""

import os
import subprocess
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kicad_lane_capacity as lc  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
FIXTURE = os.path.join(HERE, "fixtures", "open-net.kicad_pcb")


def _pcbnew_python():
    try:
        import pcbnew  # noqa: F401
        return sys.executable
    except ImportError:
        pass
    cand = ("/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/"
            "Versions/Current/bin/python3")
    if os.path.exists(cand):
        r = subprocess.run([cand, "-c", "import pcbnew"], capture_output=True)
        if r.returncode == 0:
            return cand
    return None


class RuleSetTests(unittest.TestCase):
    def test_parse_roundtrip(self):
        rs = lc.RuleSet.parse("x:0.1,0.1,0.4,0.2,0.25,0.3")
        self.assertEqual((rs.track, rs.clearance, rs.via_dia, rs.via_drill,
                          rs.hole_clearance, rs.edge_clearance), (0.1, 0.1, 0.4, 0.2, 0.25, 0.3))

    def test_parse_rejects_short_and_bad(self):
        with self.assertRaises(ValueError):
            lc.RuleSet.parse("x:0.1,0.1")
        with self.assertRaises(ValueError):
            lc.RuleSet.parse("0.1,0.1,0.4,0.2,0.25,0.3")
        with self.assertRaises(ValueError):
            lc.RuleSet.parse("x:0,0.1,0.4,0.2,0.25,0.3")      # zero track
        with self.assertRaises(ValueError):
            lc.RuleSet.parse("x:nan,0.1,0.4,0.2,0.25,0.3")
        with self.assertRaises(ValueError):
            lc.RuleSet.parse("x:0.1,-0.1,0.4,0.2,0.25,0.3")


class GeometryTests(unittest.TestCase):
    def test_merge_and_subtract(self):
        inside = lc.merge_intervals([(0, 10)])
        obs = lc.merge_intervals([(2, 3), (2.5, 4), (8, 12)])
        self.assertEqual(obs, [[2, 4], [8, 12]])
        self.assertEqual(lc.subtract(inside, obs), [(0, 2), (4, 8)])

    def test_polygon_with_hole(self):
        outer = [(0, 0), (10, 0), (10, 10), (0, 10)]
        xs = lc.polygon_crossings(outer, 5)
        self.assertEqual(lc.crossings_to_intervals(xs), [(0, 10)])
        # a line through a vertex row is not double counted
        self.assertEqual(lc.crossings_to_intervals(lc.polygon_crossings(outer, 0)), [(0, 10)])
        self.assertEqual(lc.polygon_crossings(outer, 10), [])

    def test_lane_arithmetic_is_monotonic_in_rules(self):
        """Wider track+clearance never yields more lanes in a fixed interval."""
        def lanes(L, w, c):
            return 1 + int(L // (w + c)) if L >= 0 else 0
        prev = None
        for pitch in (0.2, 0.25, 0.3, 0.5, 1.0):
            n = lanes(4.0, pitch / 2, pitch / 2)
            if prev is not None:
                self.assertLessEqual(n, prev)
            prev = n
        self.assertEqual(lanes(0.0, 0.1, 0.1), 1)   # a zero-length free centreline still fits one track
        self.assertEqual(lanes(-0.01, 0.1, 0.1), 0)


class DemandTests(unittest.TestCase):
    def test_net_width_below_min_track_is_raised(self):
        rs = lc.RuleSet("r", 0.5, 0.5, 1.0, 0.6, 0.5, 0.5)
        extents = {"/A": (0.0, 10.0), "/B": (20.0, 30.0)}
        nets, lanes, mm = lc.demand_at(5.0, extents, {"/A": 0.18}, rs)
        self.assertEqual(nets, ["/A"])
        self.assertEqual(lanes, 1)
        self.assertAlmostEqual(mm, 1.0)          # max(0.18, 0.5) + 0.5
        nets, lanes, mm = lc.demand_at(15.0, extents, {}, rs)
        self.assertEqual(nets, [])

    def test_wide_net_counts_more_lanes(self):
        rs = lc.RuleSet("r", 0.1, 0.1, 0.4, 0.2, 0.25, 0.3)
        nets, lanes, mm = lc.demand_at(1.0, {"/P": (0.0, 2.0)}, {"/P": 0.8}, rs)
        self.assertEqual(lanes, 5)               # (0.8+0.1)/0.2 = 4.5 -> 5
        self.assertAlmostEqual(mm, 0.9)

    def test_already_crossed_nets_are_not_demand(self):
        rs = lc.RuleSet("r", 0.1, 0.1, 0.4, 0.2, 0.25, 0.3)
        nets, lanes, mm = lc.demand_at(1.0, {"/P": (0.0, 2.0)}, {}, rs, already={"/P"})
        self.assertEqual((nets, lanes, mm), ([], 0, 0.0))


@unittest.skipUnless(os.path.exists(FIXTURE), "fixture board missing: %s" % FIXTURE)
class CliTests(unittest.TestCase):
    """Runs the real CLI on the skill's real fixture board under KiCad's Python."""

    @classmethod
    def setUpClass(cls):
        cls.py = _pcbnew_python()
        if cls.py is None:
            raise unittest.SkipTest("SKIPPED LOUDLY: no Python with 'pcbnew' found; the CLI tier was NOT exercised")

    def run_cli(self, *args):
        cmd = [self.py, os.path.join(HERE, "kicad_lane_capacity.py"), FIXTURE] + list(args)
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        return r.returncode, r.stdout, r.stderr

    def test_requires_rules_without_project(self):
        rc, out, err = self.run_cli()
        self.assertEqual(rc, 1)
        self.assertIn("UNEVALUABLE", err)

    def test_runs_with_rules_and_flags_absurd_rules(self):
        rc, out, err = self.run_cli("--rules", "sane:0.1,0.1,0.4,0.2,0.25,0.3", "--show", "0")
        self.assertEqual(rc, 0, err)
        self.assertIn("== sane ==", out)
        self.assertIn("PLACEMENT", out)
        # the fixture is 40 x 30 mm with two nets running along x (pads share y=10),
        # so cut across x; 5 mm track + 5 mm clearance leaves 2 lanes per layer
        # against a demand of 2 nets, i.e. exactly at capacity on F.Cu alone
        rc, out2, err = self.run_cli("--rules", "absurd:5,5,6,4,5,5", "--show", "0",
                                     "--axis", "x", "--exclude-layer", "B.Cu")
        self.assertEqual(rc, 0, err)
        self.assertIn("OVER", out2)
        rc, out3, err = self.run_cli("--rules", "sane:0.1,0.1,0.4,0.2,0.25,0.3", "--show", "0",
                                     "--axis", "x", "--exclude-layer", "B.Cu")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("OVER", out3)
        self.assertIn("/SIG", out3)  # demand names the nets that must cross

    def test_unknown_pour_net_is_unevaluable(self):
        rc, out, err = self.run_cli("--rules", "sane:0.1,0.1,0.4,0.2,0.25,0.3", "--pour-net", "NO_SUCH_NET")
        self.assertEqual(rc, 1)
        self.assertIn("not a net on this board", err)

    def test_unknown_excluded_layer_is_unevaluable(self):
        rc, out, err = self.run_cli("--rules", "sane:0.1,0.1,0.4,0.2,0.25,0.3", "--exclude-layer", "In7.Cu")
        self.assertEqual(rc, 1)
        self.assertIn("not a copper layer", err)


if __name__ == "__main__":
    unittest.main()
