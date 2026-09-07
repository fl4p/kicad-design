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
import tempfile
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
    def run_cli(self, argv, board_bytes=b"(kicad_pcb)\n"):
        """Run the CLI against a REAL file on disk carrying `board_bytes`.

        `pcbnew` stays faked -- these are CLI-contract tests, not parser tests
        -- but the board argument must name bytes that exist, because the
        report binds the board's digest and a path that cannot be read is
        unevaluable by design."""
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b.kicad_pcb"
            path.write_bytes(board_bytes)
            argv = [str(path) if token == "b.kicad_pcb" else token
                    for token in argv]
            with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
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

    def test_usage_errors_are_unevaluable_not_a_failed_gate(self):
        """argparse exits 2 by default, which collides with FAIL=2: a typo in
        a CI wrapper then reads as a board that failed its threshold. The
        exit contract already places a bad CLI value at 1."""
        import subprocess
        for argv in (["--bogus-flag"],
                     ["--max-vias-on-any-net", "notanumber"],
                     []):
            with self.subTest(argv=argv):
                proc = subprocess.run(
                    [sys.executable, audit.__file__] + argv,
                    capture_output=True, text=True)
                self.assertEqual(proc.returncode, 1)
                self.assertIn("ROUTE-SHAPE-UNEVALUABLE", proc.stderr)

    def test_vacuous_short_segment_definition_is_unevaluable(self):
        """`--short-segment-mm` defines the metric, so it can dilute both
        short-segment gates without touching the board: 1e-9 turned a
        measured 1.000-vs-0.15 FAIL into ROUTE-SHAPE-OK (codex review of
        7a99de9). Vacuity is judged against the board's own shortest
        segment, not a constant window -- a review of 7b00165 found a real
        board with four segments below 0.05 mm, which a fixed floor refused
        to grade at all. The fixture's only track is 10 mm long."""
        for value in ("1e-9", "0.0001", "10"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.run_cli(["b.kicad_pcb", "--short-segment-mm", value,
                                  "--max-short-segment-fraction", "0.15"]), 1)
        # Above the shortest segment the gate can fire, so it is a real gate.
        self.assertIn(
            self.run_cli(["b.kicad_pcb", "--short-segment-mm", "20",
                          "--max-short-segment-fraction", "0.15"]), (0, 2))
        # A report-only run may measure with any positive definition: it
        # grades nothing, so nothing can be vacuous.
        for value in ("1e-9", "0.03", "0.2", "1000"):
            with self.subTest(value=value):
                self.assertEqual(
                    self.run_cli(["b.kicad_pcb", "--short-segment-mm", value,
                                  "--report-only"]), 0)

    def test_abbreviated_json_flag_is_refused_not_silently_accepted(self):
        """The pre-parse invalidator matched the literal `--json` only. With
        argparse abbreviation on, `--jso stale.json --bogus` was ACCEPTED as
        --json, missed by the scan, and left a prior "pass" report standing
        (codex review of 7a99de9, reproduced 2026-09-07). `allow_abbrev=False`
        makes the abbreviation an unrecognised argument, so the run fails
        loudly -- but leaving the prior report standing was still the
        stale-clean-report failure GUARDS.md forbids (codex review of
        7b00165). Both halves are required: reject the invocation, AND
        invalidate the report the operator plainly named."""
        import tempfile, json as _json, os as _os
        stale = _json.dumps({"tool": "kicad_route_shape", "verdict": "pass"})
        with tempfile.TemporaryDirectory() as directory:
            report = _os.path.join(directory, "report.json")
            for flag in ("--j", "--js", "--jso"):
                with open(report, "w", encoding="utf-8") as handle:
                    handle.write(stale)
                with contextlib.redirect_stderr(io.StringIO()), \
                        self.assertRaises(SystemExit):
                    self.run_cli(["b.kicad_pcb", flag, report, "--bogus-flag"])
                with open(report, encoding="utf-8") as handle:
                    self.assertEqual(
                        _json.load(handle)["verdict"], "unevaluable",
                        f"{flag} left a stale report")
            with open(report, "w", encoding="utf-8") as handle:
                handle.write(stale)
            # Control: spelled in full, the same malformed command line does
            # invalidate the report it targets.
            with contextlib.redirect_stderr(io.StringIO()), \
                    self.assertRaises(SystemExit):
                self.run_cli(["b.kicad_pcb", "--json", report, "--bogus-flag"])
            with open(report, encoding="utf-8") as handle:
                self.assertEqual(_json.load(handle)["verdict"], "unevaluable")

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


class BackendSelection(unittest.TestCase):
    """The audit must name the backend behind its numbers, and refuse a backend
    it cannot use rather than quietly using the other one."""

    def setUp(self):
        self._saved = audit._BACKEND
        self._saved_identity = audit._BOARD_IDENTITY
        audit._BACKEND = None
        audit._BOARD_IDENTITY = None

    def tearDown(self):
        audit._BACKEND = self._saved
        audit._BOARD_IDENTITY = self._saved_identity

    def _run(self, argv, **patches):
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b.kicad_pcb"
            path.write_bytes(b"(kicad_pcb)\n")
            argv = [str(path) if token == "b.kicad_pcb" else token
                    for token in argv]
            with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                    contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                code = audit.main(argv)
        return code, out.getvalue(), err.getvalue()

    def test_a_requested_backend_that_is_unavailable_is_unevaluable(self):
        def refuse(environ=None, **kwargs):
            raise audit.kicad_backend.BackendUnavailable(
                "ipc", "ipc-test-refusal", "not here")

        with mock.patch.object(audit.kicad_backend, "probe_ipc", refuse):
            code, _, err = self._run(["b.kicad_pcb", "--report-only",
                                      "--backend", "ipc"])
        self.assertEqual(code, 1)
        self.assertIn("ipc-test-refusal", err)

    def test_an_unavailable_backend_never_falls_back_to_the_other_one(self):
        def refuse(environ=None, **kwargs):
            raise audit.kicad_backend.BackendUnavailable(
                "ipc", "ipc-test-refusal", "not here")

        def swig(environ=None):  # pragma: no cover - must not run
            raise AssertionError("fell back to swig")

        with mock.patch.object(audit.kicad_backend, "probe_ipc", refuse), \
                mock.patch.object(audit, "_swig_selection", swig):
            code, _, _ = self._run(["b.kicad_pcb", "--report-only",
                                    "--backend", "ipc"])
        self.assertEqual(code, 1)

    def test_an_unknown_backend_in_the_environment_is_unevaluable(self):
        with mock.patch.dict(audit.os.environ,
                             {audit.kicad_backend.ENV_VAR: "swog"}):
            code, _, err = self._run(["b.kicad_pcb", "--report-only"])
        self.assertEqual(code, 1)
        self.assertIn("backend-unknown", err)

    def test_a_backend_that_cannot_open_a_board_is_refused(self):
        def crippled(environ=None):
            return audit.kicad_backend.Selection(
                "swig", None, "cannot open a file", frozenset())

        with mock.patch.object(audit, "_swig_selection", crippled):
            code, _, err = self._run(["b.kicad_pcb", "--report-only"])
        self.assertEqual(code, 1)
        self.assertIn("backend-missing-capability", err)
        self.assertIn(audit.kicad_backend.CAP_OPEN_BOARD_FROM_PATH, err)

    def test_the_resolved_backend_reaches_the_verdict_line(self):
        code, out, _ = self._run(["b.kicad_pcb", "--report-only"])
        self.assertEqual(code, 0)
        self.assertIn(audit._NOT_GRADED_LINE, out)
        self.assertIn("backend=swig", out)
        self.assertIn("source=default", out)

    def test_the_report_carries_the_backend_and_null_before_it_is_resolved(self):
        import json as _json
        import os as _os
        import tempfile as _tempfile
        handle, path = _tempfile.mkstemp(suffix=".json")
        _os.close(handle)
        _os.unlink(path)
        try:
            code, _, _ = self._run(["b.kicad_pcb", "--report-only",
                                    "--json", path])
            self.assertEqual(code, 0)
            with open(path) as stream:
                document = _json.load(stream)
            self.assertEqual(document["backend"]["name"], "swig")
            self.assertIn("provenance", document["backend"])
        finally:
            _os.unlink(path)
        # And before resolution the field is present and null, never absent.
        audit._BACKEND = None
        handle, path = _tempfile.mkstemp(suffix=".json")
        _os.close(handle)
        _os.unlink(path)
        try:
            audit._write_json(path, "b.kicad_pcb", "unevaluable", {})
            with open(path) as stream:
                self.assertIsNone(_json.load(stream)["backend"])
        finally:
            _os.unlink(path)


class ReportBindsTheBoardBytes(unittest.TestCase):
    """A verdict names a board's BYTES, not its path.

    Without this binding a `"pass"` written for one revision of a board still
    reads as a verdict about whatever now sits at that path -- the stale
    clean-report failure `../GUARDS.md` exists to prevent. Findings 7 of
    `reviews/2026-09-07-claude-review-a5c83c2-7a99de9.md`.
    """

    def setUp(self):
        self._saved = audit._BOARD_IDENTITY
        audit._BOARD_IDENTITY = None

    def tearDown(self):
        audit._BOARD_IDENTITY = self._saved

    def _run(self, argv, board_bytes, directory, measure=None):
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        path = Path(directory) / "b.kicad_pcb"
        path.write_bytes(board_bytes)
        argv = [str(path) if token == "b.kicad_pcb" else token
                for token in argv]
        stack = contextlib.ExitStack()
        with stack:
            stack.enter_context(
                mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            errors = stack.enter_context(
                contextlib.redirect_stderr(io.StringIO()))
            if measure is not None:
                stack.enter_context(
                    mock.patch.object(audit, "measure", measure))
            code = audit.main(argv)
        return code, path, errors.getvalue()

    def test_the_report_records_the_digest_of_the_bytes_it_graded(self):
        import hashlib as _hashlib
        import json as _json
        payload = b"(kicad_pcb (version 20240108))\n"
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "r.json"
            code, path, _ = self._run(
                ["b.kicad_pcb", "--report-only", "--json", str(report)],
                payload, directory)
            self.assertEqual(code, 0)
            document = _json.loads(report.read_text())
            self.assertEqual(document["verdict"], "reported")
            self.assertEqual(document["source"]["sha256"],
                             _hashlib.sha256(payload).hexdigest())
            self.assertEqual(document["source"]["size"], len(payload))

    def test_a_report_written_before_the_digest_carries_a_null_source(self):
        """The placeholder must not claim a binding it does not have."""
        import json as _json
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "r.json"
            # No threshold and no --report-only: unevaluable before measuring.
            code, _, _ = self._run(["b.kicad_pcb", "--json", str(report)],
                                   b"(kicad_pcb)\n", directory)
            self.assertEqual(code, 1)
            document = _json.loads(report.read_text())
            self.assertNotEqual(document["verdict"], "pass")
            self.assertIsNone(document["source"])

    def test_a_board_rewritten_mid_measurement_is_unevaluable_not_a_pass(self):
        """KNOWN-BAD CALIBRATION: construct the failure, watch the guard fail.

        A board edited while the audit runs yields metrics that describe no
        single revision. Graded against a threshold the old bytes satisfied,
        it would exit 0."""
        import json as _json
        real_measure = audit.measure

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b.kicad_pcb"
            report = Path(directory) / "r.json"

            def mutating_measure(board_path, *args, **kwargs):
                metrics = real_measure(board_path, *args, **kwargs)
                # The edit a human makes in KiCad while the guard is running.
                Path(board_path).write_bytes(b"(kicad_pcb (edited))\n")
                return metrics

            code, _, errors = self._run(
                ["b.kicad_pcb", "--max-vias-on-any-net", "2",
                 "--json", str(report)],
                b"(kicad_pcb)\n", directory, measure=mutating_measure)

            self.assertEqual(code, 1)
            self.assertIn("changed while it was being measured", errors)
            document = _json.loads(report.read_text())
            self.assertEqual(document["verdict"], "unevaluable")

    def test_the_digest_does_not_leak_into_the_next_run(self):
        """Module state must not publish run N's digest in run N+1's report.

        Measured 2026-09-07 (codex review of 9732ec7): `main()` reset
        `_BACKEND` but not `_BOARD_IDENTITY`, so a second board that failed
        BEFORE it was ever hashed published the FIRST board's sha256 as its
        own `source`. The tests' own setUp reset was what hid it.
        """
        import hashlib as _hashlib
        import json as _json
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        with tempfile.TemporaryDirectory() as directory:
            first = Path(directory) / "A.kicad_pcb"
            second = Path(directory) / "B.kicad_pcb"
            first.write_bytes(b"BOARD-A\n")
            second.write_bytes(b"BOARD-B-is-a-different-board\n")
            report = Path(directory) / "r.json"
            with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                audit.main([str(first), "--max-vias-on-any-net", "2",
                            "--json", str(report)])
                # No gate and no --report-only: run 2 fails before hashing.
                audit.main([str(second), "--json", str(report)])
            document = _json.loads(report.read_text())
            self.assertEqual(document["verdict"], "unevaluable")
            self.assertIsNone(
                document["source"],
                "run 2 published a digest for a board it never hashed")
            self.assertNotEqual(
                (document["source"] or {}).get("sha256"),
                _hashlib.sha256(b"BOARD-A\n").hexdigest())

    def test_a_board_that_cannot_be_digested_is_unevaluable(self):
        """A failed observation is not a clean observation."""
        board = FakeBoard([FakeTrack("/A", F_CU, 0, 0, 10, 0)])
        with tempfile.TemporaryDirectory() as directory:
            missing = Path(directory) / "gone.kicad_pcb"
            with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                    mock.patch.object(audit.os.path, "isfile",
                                      return_value=True), \
                    contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()) as errors:
                code = audit.main([str(missing), "--report-only"])
        self.assertEqual(code, 1)
        self.assertIn("to digest it", errors.getvalue())


class ShortSegmentGateIsCalibrated(unittest.TestCase):
    """Findings 3 and 4: the two dilution/mute paths need a known-bad case."""

    def _run(self, argv, tracks):
        board = FakeBoard(tracks)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "b.kicad_pcb"
            path.write_bytes(b"(kicad_pcb)\n")
            argv = [str(path) if token == "b.kicad_pcb" else token
                    for token in argv]
            with mock.patch.dict(sys.modules, {"pcbnew": FakePcbnew(board)}), \
                    contextlib.redirect_stdout(io.StringIO()) as out, \
                    contextlib.redirect_stderr(io.StringIO()) as err:
                code = audit.main(argv)
        return code, out.getvalue(), err.getvalue()

    def _short_board(self):
        """Every segment 0.1 mm: a 1.000 short fraction at the 0.2 mm default."""
        return [FakeTrack("/N%d" % index, F_CU,
                          index * 1.0, 0.0, index * 1.0 + 0.1, 0.0)
                for index in range(6)]

    def test_the_short_segment_gate_fails_on_a_board_built_to_fail_it(self):
        code, _, _ = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15"],
            self._short_board())
        self.assertEqual(code, 2)

    def _tuned_board(self):
        """One 0.10 mm segment and nine 0.19 mm ones.

        At the 0.20 mm definition every segment is short (10/10). At 0.15 only
        one is (1/10) -- and 0.15 clears the vacuity check, because it is above
        the 0.10 mm shortest segment. Identical copper, opposite verdicts.
        """
        tracks = [FakeTrack("/N0", F_CU, 0.0, 0.0, 0.10, 0.0)]
        tracks += [FakeTrack("/N%d" % index, F_CU,
                             index * 2.0, 1.0, index * 2.0 + 0.19, 1.0)
                   for index in range(1, 10)]
        return tracks

    def test_a_pass_that_exists_only_at_the_chosen_definition_is_refused(self):
        """KNOWN-BAD CALIBRATION for the mute button that vacuity does NOT catch.

        Measured 2026-09-07 (codex review of 9732ec7): this exact board went
        from ROUTE-SHAPE-FAIL to ROUTE-SHAPE-OK by moving --short-segment-mm
        from 0.20 to 0.15, with no copper changed. Refusing only the fully
        vacuous definition was never enough.
        """
        board = self._tuned_board()
        code, _, _ = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15",
             "--short-segment-mm", "0.20"], board)
        self.assertEqual(code, 2, "the board fails at the honest definition")

        code, out, err = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15",
             "--short-segment-mm", "0.15"], board)
        self.assertEqual(code, 1, "the tuned definition must not buy a pass")
        self.assertNotIn(audit._OK_LINE, out)
        self.assertIn("property of the definition, not of the board", err)

    def test_a_verdict_stable_across_the_band_still_passes(self):
        """The guard must not turn every real pass into noise."""
        tracks = [FakeTrack("/S", F_CU, 0.0, 0.0, 0.10, 0.0)]
        tracks += [FakeTrack("/L%d" % index, F_CU,
                             0.0, index + 1.0, 1.0, index + 1.0)
                   for index in range(99)]
        code, out, _ = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15",
             "--short-segment-mm", "0.20"], tracks)
        self.assertEqual(code, 0)
        self.assertIn(audit._OK_LINE, out)

    def test_a_genuinely_bad_board_still_fails_rather_than_going_unevaluable(self):
        tracks = [FakeTrack("/B%d" % index, F_CU,
                            index * 2.0, 0.0, index * 2.0 + 0.05, 0.0)
                  for index in range(10)]
        code, _, _ = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15",
             "--short-segment-mm", "0.20"], tracks)
        self.assertEqual(code, 2)

    def test_shrinking_the_definition_cannot_mute_that_failure(self):
        """KNOWN-BAD CALIBRATION for the mute button of finding 4.

        `--short-segment-mm 1e-9` once turned the FAIL above into
        ROUTE-SHAPE-OK without touching one millimetre of copper."""
        code, out, err = self._run(
            ["b.kicad_pcb", "--max-short-segment-fraction", "0.15",
             "--short-segment-mm", "1e-9"],
            self._short_board())
        self.assertEqual(code, 1)
        self.assertNotIn(audit._OK_LINE, out)
        self.assertIn("a gate that cannot fail", err)

    def test_the_graded_via_metric_is_the_undilutable_maximum(self):
        """KNOWN-BAD CALIBRATION for the dilution of finding 3.

        Adding via-free nets lowers the MEAN. The graded metric is the per-net
        maximum, which no added net can lower, so the verdict must not move."""
        offending = [FakeTrack("/HOT", F_CU, 0, 0, 1, 0)]
        offending += [FakeVia("/HOT", F_CU, B_CU) for _ in range(4)]
        code, _, _ = self._run(
            ["b.kicad_pcb", "--max-vias-on-any-net", "2"], offending)
        self.assertEqual(code, 2)

        diluted = list(offending)
        diluted += [FakeTrack("/CLEAN%d" % index, F_CU,
                              0, index + 5.0, 10, index + 5.0)
                    for index in range(50)]
        code, _, _ = self._run(
            ["b.kicad_pcb", "--max-vias-on-any-net", "2"], diluted)
        self.assertEqual(code, 2, "50 via-free nets diluted the graded metric")


if __name__ == "__main__":
    unittest.main()
