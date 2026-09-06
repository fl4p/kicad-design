"""Known-bad calibration for the via annular-bridge criterion.

Separate from `test_copper_guards.py`, which is stdlib-only by design: these
tests exercise the measurement itself and therefore need numpy and shapely.
They skip cleanly where those are absent rather than breaking collection.
"""

import math
import unittest

import test_copper_guards  # noqa: F401  (keeps the CLI suite's path helper)

try:
    import shapely  # noqa: F401
    from shapely.geometry import Point, Polygon
    import copper_guards
    HAVE_GEOMETRY = True
except ImportError:                       # pragma: no cover - env dependent
    HAVE_GEOMETRY = False


CX = CY = 0.0
R_DRILL = 0.25          # 0.5 mm drill
R_PAD = 0.40            # 0.8 mm via
R_MID = (R_DRILL + R_PAD) / 2


def sector(r0, r1, a0, a1, steps=200):
    """An annular sector: radially between r0 and r1, angularly a0..a1."""
    span = [a0 + (a1 - a0) * i / steps for i in range(steps + 1)]
    outer = [(CX + r1 * math.cos(a), CY + r1 * math.sin(a)) for a in span]
    inner = [(CX + r0 * math.cos(a), CY + r0 * math.sin(a))
             for a in reversed(span)]
    return Polygon(outer + inner)


def radial_strip(width):
    """A straight track of `width` passing over the via centre."""
    half, reach = width / 2, R_PAD * 1.2
    return Polygon([(-half, -reach), (half, -reach), (half, reach),
                    (-half, reach)])


def bridge(geom):
    return copper_guards.annulus_bridge_mm(geom, CX, CY, R_DRILL, R_PAD)


@unittest.skipUnless(HAVE_GEOMETRY, "needs numpy + shapely")
class AnnulusBridgeCalibration(unittest.TestCase):
    def test_tangential_sliver_bridges_nothing(self):
        """The constructed known-bad. A radially thin sector hugging the mid
        radius produced a long arc under the old single-circle measurement
        and PASSed as `spoke 0.35mm` (codex review of 7b00165) -- 0.5 % of
        the ring area, touching neither the drill edge nor the pad edge."""
        half = 0.005                       # 0.010 mm of radial extent
        sliver = sector(R_MID - half, R_MID + half, 0.0, 0.35 / R_MID)
        self.assertEqual(bridge(sliver), 0.0)

    def test_two_disjoint_slivers_do_not_add_up_to_a_bridge(self):
        """Guards the weaker min-of-maxima formulation: separate slivers at
        the inner and outer radii must not compose into a strip."""
        combo = sector(R_DRILL, R_DRILL + 0.01, 0.0, 1.0).union(
            sector(R_PAD - 0.01, R_PAD, 2.0, 3.0))
        self.assertEqual(bridge(combo), 0.0)

    def test_a_real_thermal_spoke_still_passes(self):
        """Representation independence: a 0.5 mm zone spoke is electrically a
        0.5 mm track and must not fail for being encoded as fill. Measured
        0.44 mm, not 0.50: the angular footprint of a straight strip narrows
        with radius, so the pad-edge crossing sets the width. Conservative by
        about 12 % here, in the safe direction."""
        measured = bridge(radial_strip(0.5))
        self.assertGreaterEqual(measured, 0.3)      # the shipped default
        self.assertAlmostEqual(measured, 0.44, places=2)

    def test_monotone_in_spoke_width(self):
        """As the input worsens the verdict must not improve."""
        widths = [0.1, 0.2, 0.3, 0.5, 0.7]
        measured = [bridge(radial_strip(w)) for w in widths]
        self.assertEqual(measured, sorted(measured))

    def test_full_coverage_is_the_whole_mid_circumference(self):
        full = Point(CX, CY).buffer(R_PAD * 2)
        self.assertAlmostEqual(bridge(full), 2 * math.pi * R_MID, places=3)

    def test_no_copper_is_not_a_bridge(self):
        self.assertEqual(bridge(None), 0.0)
        self.assertEqual(bridge(Point(10.0, 10.0).buffer(0.1)), 0.0)


if __name__ == "__main__":
    unittest.main()
