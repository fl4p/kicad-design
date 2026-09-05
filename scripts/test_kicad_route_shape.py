#!/usr/bin/env python3
"""Tests for the routing-shape audit (pcbnew mocked).

Calibration against a real board was performed with the bundled interpreter on
KiCad 10.0.5 (2026-09-05), o2-probe.kicad_pcb: 197 vias all spanning
F.Cu->B.Cu, 61 routed nets, 1793 segments over 2125.69 mm, median 0.500 mm,
31% below 0.2 mm, per-layer segment counts 893/580/222/98
(F.Cu/B.Cu/In1.Cu/In2.Cu).

An independent regex reader over the same file agreed on every one of those
figures EXCEPT the raw segment count, where its strict field-order pattern
matched only 1773 of the 1793 segments. `pcbnew` is authoritative and its
numbers are the ones recorded here; the regex reader is recorded as a partial
cross-check, not as a reproduction.

These tests pin the pure logic: the metric arithmetic, the axis tolerance and
the diagonal remainder, the full-stack via classification, the fail-closed CLI
contract (no threshold and no --report-only, the two combined, non-finite
thresholds, malformed --layer-direction), and — the property that matters most
— that a threshold naming a metric which cannot be evaluated FAILS rather than
being skipped.
"""

from __future__ import annotations

import contextlib
import io
import math
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_route_shape as audit

NM = 1_000_000
F_CU, IN1_CU, B_CU = 0, 4, 2
_LAYER_NAMES = {F_CU: "F.Cu", IN1_CU: "In1.Cu", B_CU: "B.Cu"}


class FakePoint:
    def __init__(self, x, y):
        self.x, self.y = x, y


class FakeTrack:
    """A track segment in millimetres, converted to KiCad nanometres."""

    def __init__(self, net, layer, x1, y1, x2, y2):
        self._net, self._layer = net, layer
        self._start = FakePoint(int(x1 * NM), int(y1 * NM))
        self._end = FakePoint(int(x2 * NM), int(y2 * NM))

    def GetLength(self):
        return int(math.hypot(self._end.x - self._start.x,
                              self._end.y - self._start.y))

    def GetNetname(self):
        return self._net

    def GetLayer(self):
        return self._layer

    def GetStart(self):
        return self._start

    def GetEnd(self):
        return self._end


class FakeArc(FakeTrack):
    """An arc whose true copper length exceeds its chord, so a chord-based
    reader is detectable."""

    def __init__(self, net, layer, x1, y1, x2, y2, length_mm):
        super().__init__(net, layer, x1, y1, x2, y2)
        self._length = int(length_mm * NM)

    def GetLength(self):
        return self._length


class FakeVia:
    def __init__(self, net, top, bottom):
        self._net, self._top, self._bottom = net, top, bottom

    def GetNetname(self):
        return self._net

    def TopLayer(self):
        return self._top

    def BottomLayer(self):
        return self._bottom


class FakeLayerSet:
    def __init__(self, stack):
        self._stack = stack

    def CuStack(self):
        return self._stack


class FakeBoard:
    def __init__(self, tracks, stack=(F_CU, IN1_CU, B_CU)):
        self._tracks = tracks
        self._stack = list(stack)

    def GetTracks(self):
        return self._tracks

    def GetLayerName(self, layer):
        return _LAYER_NAMES[layer]

    def GetEnabledLayers(self):
        return FakeLayerSet(self._stack)


class FakePcbnew:
    PCB_VIA = FakeVia
    PCB_ARC = FakeArc

    def __init__(self, board):
        self._board = board

    def LoadBoard(self, path):
        return self._board


def measure(tracks, *, stack=(F_CU, IN1_CU, B_CU), short=0.2, directions=None):
    board = FakeBoard(tracks, stack)
    with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
            mock.patch.object(audit.os.path, "isfile", return_value=True):
        return audit.measure("board.kicad_pcb", short, directions or {})


class MetricArithmetic(unittest.TestCase):
    def test_lengths_counts_and_layers(self):
        metrics = measure([
            FakeTrack("/A", F_CU, 0, 0, 10, 0),     # 10 mm horizontal
            FakeTrack("/A", F_CU, 10, 0, 10, 4),    # 4 mm vertical
            FakeTrack("/B", IN1_CU, 0, 0, 0, 6),    # 6 mm vertical
        ])
        self.assertEqual(metrics["segments"], 3)
        self.assertEqual(metrics["routed_nets"], 2)
        self.assertAlmostEqual(metrics["copper_length_mm"], 20.0, places=2)
        self.assertEqual(metrics["per_layer"]["F.Cu"]["segments"], 2)
        self.assertAlmostEqual(metrics["per_layer"]["F.Cu"]["length_mm"], 14.0,
                               places=2)

    def test_zero_length_segment_is_ignored_not_counted(self):
        metrics = measure([
            FakeTrack("/A", F_CU, 1, 1, 1, 1),
            FakeTrack("/A", F_CU, 0, 0, 1, 0),
        ])
        self.assertEqual(metrics["segments"], 1)

    def test_short_segment_fraction_uses_the_declared_threshold(self):
        tracks = [FakeTrack("/A", F_CU, 0, 0, 0.1, 0),
                  FakeTrack("/A", F_CU, 1, 0, 2, 0)]
        self.assertAlmostEqual(measure(tracks)["short_segment_fraction"], 0.5)
        self.assertAlmostEqual(
            measure(tracks, short=0.05)["short_segment_fraction"], 0.0)

    def test_vias_per_routed_net_ignores_copperless_nets(self):
        metrics = measure([
            FakeTrack("/A", F_CU, 0, 0, 1, 0),
            FakeVia("/A", F_CU, B_CU),
            FakeVia("/ONLY_VIA", F_CU, B_CU),   # no track copper anywhere
        ])
        self.assertEqual(metrics["vias"], 2)
        self.assertEqual(metrics["routed_nets"], 1)
        self.assertAlmostEqual(metrics["vias_per_routed_net"], 2.0)


class ViaSpanClassification(unittest.TestCase):
    def test_full_stack_is_outer_pair_only(self):
        metrics = measure([
            FakeTrack("/A", F_CU, 0, 0, 1, 0),
            FakeVia("/A", F_CU, B_CU),      # full stack
            FakeVia("/A", F_CU, IN1_CU),    # blind
        ])
        self.assertEqual(metrics["full_stack_vias"], 1)
        self.assertAlmostEqual(metrics["full_stack_via_fraction"], 0.5)
        self.assertEqual(metrics["via_layer_spans"],
                         {"F.Cu->In1.Cu": 1, "F.Cu->B.Cu": 1})

    def test_single_layer_board_cannot_classify_full_stack(self):
        metrics = measure([FakeTrack("/A", F_CU, 0, 0, 1, 0)], stack=(F_CU,))
        self.assertIsNone(metrics["full_stack_vias"])
        self.assertIsNone(metrics["full_stack_via_fraction"])


class DirectionConformance(unittest.TestCase):
    def test_axis_tolerance_and_diagonal_remainder(self):
        # 10 mm horizontal, 10 mm vertical, and one 45-degree run that must
        # count towards neither axis.
        tracks = [
            FakeTrack("/A", F_CU, 0, 0, 10, 0),
            FakeTrack("/A", F_CU, 0, 0, 0, 10),
            FakeTrack("/A", F_CU, 0, 0, 10, 10),
        ]
        diagonal = math.hypot(10, 10)
        total = 20 + diagonal
        horizontal = measure(tracks, directions={"F.Cu": "h"})
        vertical = measure(tracks, directions={"F.Cu": "v"})
        self.assertAlmostEqual(
            horizontal["direction_conformance"]["F.Cu"], 10 / total, places=6)
        self.assertAlmostEqual(
            vertical["direction_conformance"]["F.Cu"], 10 / total, places=6)
        # The two shares do not sum to 1: the remainder is the diagonal.
        self.assertLess(
            horizontal["direction_conformance"]["F.Cu"]
            + vertical["direction_conformance"]["F.Cu"], 1.0)

    def test_undeclared_layer_is_unevaluable_not_zero(self):
        metrics = measure([FakeTrack("/A", IN1_CU, 0, 0, 5, 0)],
                          directions={"F.Cu": "h"})
        self.assertIsNone(metrics["direction_conformance"]["In1.Cu"])


class GradingIsFailClosed(unittest.TestCase):
    class Args:
        max_vias_on_any_net: float | None = None
        max_full_stack_via_fraction: float | None = None
        max_full_stack_vias: float | None = None
        max_short_segment_fraction: float | None = None
        max_short_segments: float | None = None
        min_direction_conformance: float | None = None

    def test_unevaluable_metric_named_by_a_threshold_fails(self):
        metrics = measure([FakeTrack("/A", IN1_CU, 0, 0, 5, 0)],
                          directions={"F.Cu": "h"})
        args = self.Args()
        args.min_direction_conformance = 0.7
        findings, graded = audit.grade(metrics, args)
        # Two unevaluable layers, not one: F.Cu is declared and carries no
        # copper, and In1.Cu carries copper with no declaration. Grading only
        # the declared set let the second one pass silently (codex review of
        # 0aefe5b).
        self.assertEqual(graded, 2)
        self.assertEqual(len(findings), 2)
        self.assertTrue(all("UNEVALUABLE" in f for f in findings))
        self.assertTrue(any("In1.Cu" in f for f in findings))

    def test_conformance_threshold_without_any_declaration_fails(self):
        metrics = measure([FakeTrack("/A", F_CU, 0, 0, 5, 0)])
        args = self.Args()
        args.min_direction_conformance = 0.7
        findings, graded = audit.grade(metrics, args)
        self.assertEqual(graded, 1)
        self.assertIn("UNEVALUABLE", findings[0])

    def test_thresholds_that_pass_produce_no_findings(self):
        metrics = measure([
            FakeTrack("/A", F_CU, 0, 0, 10, 0),
            FakeVia("/A", F_CU, IN1_CU),
        ])
        args = self.Args()
        args.max_vias_on_any_net = 2.0
        args.max_full_stack_via_fraction = 0.5
        args.max_short_segment_fraction = 0.5
        findings, graded = audit.grade(metrics, args)
        self.assertEqual(graded, 3)
        self.assertEqual(findings, [])

    def test_dilution_by_via_free_nets_cannot_flip_a_fail_to_pass(self):
        """The defect the graded metric was changed to close: the mean over
        nets-carrying-copper is diluted by every via-free net."""
        args = self.Args()
        args.max_vias_on_any_net = 1.0
        base = [FakeTrack("/A", F_CU, 0, 0, 10, 0),
                FakeVia("/A", F_CU, B_CU), FakeVia("/A", F_CU, B_CU)]
        diluted = base + [FakeTrack(f"/N{i}", F_CU, i, 50, i + 0.1, 50)
                          for i in range(10)]
        before, after = measure(base), measure(diluted)
        # the dilutable mean really does collapse ...
        self.assertGreater(before["vias_per_routed_net"],
                           after["vias_per_routed_net"] * 5)
        # ... and the graded metric does not move at all
        self.assertEqual(before["max_vias_on_a_net"],
                         after["max_vias_on_a_net"])
        self.assertTrue(audit.grade(before, args)[0])
        self.assertTrue(audit.grade(after, args)[0])

    def test_monotonic_worse_input_never_flips_back_to_pass(self):
        args = self.Args()
        args.max_vias_on_any_net = 1.0
        for via_count in range(1, 8):
            tracks = [FakeTrack("/A", F_CU, 0, 0, 10, 0)]
            tracks += [FakeVia("/A", F_CU, B_CU) for _ in range(via_count)]
            findings, _ = audit.grade(measure(tracks), args)
            with self.subTest(vias=via_count):
                self.assertEqual(bool(findings), via_count > 1)


class UnroutedAndUnloadableAreUnevaluable(unittest.TestCase):
    def test_board_with_no_track_copper_raises(self):
        with self.assertRaises(audit.Unevaluable):
            measure([FakeVia("/A", F_CU, B_CU)])

    def test_missing_board_raises(self):
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 1, 0)])
        with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                mock.patch.object(audit.os.path, "isfile", return_value=False):
            with self.assertRaises(audit.Unevaluable):
                audit.measure("missing.kicad_pcb", 0.2, {})

    def test_loadboard_returning_none_raises(self):
        class NoneLoader(FakePcbnew):
            def LoadBoard(self, path):
                return None
        with mock.patch.dict(sys.modules, {"pcbnew": NoneLoader(None)}), \
                mock.patch.object(audit.os.path, "isfile", return_value=True):
            with self.assertRaises(audit.Unevaluable):
                audit.measure("board.kicad_pcb", 0.2, {})


class ArcsAreNotChords(unittest.TestCase):
    def test_arc_contributes_true_length_not_chord(self):
        # chord 10 mm, true copper 15.7 mm
        metrics = measure([FakeArc("/A", F_CU, 0, 0, 10, 0, 15.7)])
        self.assertAlmostEqual(metrics["copper_length_mm"], 15.7, places=2)
        self.assertEqual(metrics["arcs"], 1)

    def test_arc_counts_towards_neither_axis(self):
        metrics = measure([FakeArc("/A", F_CU, 0, 0, 10, 0, 15.7)],
                          directions={"F.Cu": "h"})
        # a chord reader would call this 100% horizontal
        self.assertEqual(metrics["direction_conformance"]["F.Cu"], 0.0)


class TwoLayerFullStackIsUnevaluable(unittest.TestCase):
    def test_two_layer_board_cannot_grade_full_stack(self):
        """Every ordinary via on a 2-layer board spans the outer pair by
        construction, so the fraction says nothing about layer planning."""
        metrics = measure([FakeTrack("/A", F_CU, 0, 0, 1, 0),
                           FakeVia("/A", F_CU, B_CU)], stack=(F_CU, B_CU))
        self.assertIsNone(metrics["full_stack_via_fraction"])

    def test_four_layer_board_still_grades_it(self):
        metrics = measure([FakeTrack("/A", F_CU, 0, 0, 1, 0),
                           FakeVia("/A", F_CU, B_CU)])
        self.assertAlmostEqual(metrics["full_stack_via_fraction"], 1.0)


class LayerDirectionParsing(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(
            audit._parse_layer_directions("F.Cu=v, In1.Cu=H"),
            {"F.Cu": "v", "In1.Cu": "h"})

    def test_empty_string_declares_nothing(self):
        self.assertEqual(audit._parse_layer_directions(""), {})

    def test_rejects_bad_direction_and_shape(self):
        for bad in ("F.Cu=x", "F.Cu", "=v", ","):
            with self.subTest(value=bad):
                with self.assertRaises(audit.Unevaluable):
                    audit._parse_layer_directions(bad)


class CliContract(unittest.TestCase):
    def run_cli(self, argv):
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                mock.patch.object(audit.os.path, "isfile", return_value=True), \
                contextlib.redirect_stdout(io.StringIO()):
            return audit.main(argv)

    def test_no_threshold_and_no_report_only_is_unevaluable(self):
        self.assertEqual(self.run_cli(["b.kicad_pcb"]), 1)

    def test_report_only_with_a_threshold_is_unevaluable(self):
        self.assertEqual(
            self.run_cli(["b.kicad_pcb", "--report-only",
                          "--max-vias-on-any-net", "2"]), 1)

    def test_report_only_exits_zero_without_grading(self):
        self.assertEqual(self.run_cli(["b.kicad_pcb", "--report-only"]), 0)

    def test_non_finite_threshold_is_unevaluable(self):
        self.assertEqual(
            self.run_cli(["b.kicad_pcb", "--max-vias-on-any-net", "nan"]), 1)
        self.assertEqual(
            self.run_cli(["b.kicad_pcb", "--max-vias-on-any-net", "inf"]), 1)

    def test_non_positive_short_segment_length_is_unevaluable(self):
        self.assertEqual(
            self.run_cli(["b.kicad_pcb", "--short-segment-mm", "0",
                          "--max-vias-on-any-net", "2"]), 1)

    def test_vacuous_out_of_domain_thresholds_are_unevaluable(self):
        """A fraction outside [0,1] can never fail, so it is a configuration
        error, not a pass."""
        for argv in (["b.kicad_pcb", "--max-full-stack-via-fraction", "2.0"],
                     ["b.kicad_pcb", "--max-short-segment-fraction", "-1"],
                     ["b.kicad_pcb", "--min-direction-conformance", "-1",
                      "--layer-direction", "F.Cu=h"],
                     ["b.kicad_pcb", "--max-vias-on-any-net", "-5"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(argv), 1)

    def test_json_refuses_a_target_it_does_not_own(self):
        """`BOARD --json BOARD` destroyed the board under review before the
        ownership marker existed (codex review of 0aefe5b)."""
        import tempfile, os as _os
        handle, path = tempfile.mkstemp(suffix=".kicad_pcb")
        with _os.fdopen(handle, "w") as stream:
            stream.write("(kicad_pcb (version 20240108))\n")
        try:
            self.assertEqual(
                self.run_cli([path, "--json", path, "--report-only"]), 1)
            with open(path) as stream:
                self.assertIn("kicad_pcb", stream.read())
        finally:
            _os.unlink(path)

    def test_last_json_is_the_one_invalidated(self):
        """argparse keeps the last --json; stopping at the first left the
        effective report standing."""
        import json as _json, tempfile, os as _os
        paths = []
        for _ in range(2):
            handle, path = tempfile.mkstemp(suffix=".json")
            with _os.fdopen(handle, "w") as stream:
                _json.dump({"tool": "kicad_route_shape", "verdict": "pass",
                            "metrics": {}}, stream)
            paths.append(path)
        try:
            with self.assertRaises(SystemExit):
                self.run_cli(["b.kicad_pcb", "--max-vias-on-any-net",
                              "notanumber", "--json", paths[0],
                              "--json", paths[1]])
            with open(paths[1]) as stream:
                self.assertEqual(_json.load(stream)["verdict"], "unevaluable")
        finally:
            for path in paths:
                _os.unlink(path)

    def test_count_thresholds_must_be_whole_numbers(self):
        for argv in (["b.kicad_pcb", "--max-vias-on-any-net", "2.9"],
                     ["b.kicad_pcb", "--max-short-segments", "1.5"],
                     ["b.kicad_pcb", "--max-full-stack-vias", "0.5"]):
            with self.subTest(argv=argv):
                self.assertEqual(self.run_cli(argv), 1)

    def test_parse_error_invalidates_a_previous_clean_report(self):
        import json as _json, tempfile, os as _os
        handle, path = tempfile.mkstemp(suffix=".json")
        with _os.fdopen(handle, "w") as stream:
            _json.dump({"tool": "kicad_route_shape", "verdict": "pass",
                        "metrics": {}}, stream)
        try:
            with self.assertRaises(SystemExit):
                self.run_cli(["b.kicad_pcb", "--max-vias-on-any-net",
                              "notanumber", "--json", path])
            with open(path) as stream:
                self.assertEqual(_json.load(stream)["verdict"], "unevaluable")
        finally:
            _os.unlink(path)


if __name__ == "__main__":
    unittest.main()
