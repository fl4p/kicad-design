#!/usr/bin/env python3

from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_verify as verify


_CLEAN_DRC = """\
** Found 0 DRC violations **
** Found 0 unconnected pads **
** Found 0 footprint errors **
"""

_NETLIST = """\
(export
  (components
    (comp (ref "R1") (value "1k") (footprint "R_0603")))
  (nets
    (net (code "1") (name "GND")
      (node (ref "R1") (pin "1")))))
"""


class VerifyDrcTests(unittest.TestCase):
    def _board(self, root, context=True):
        board = Path(root) / "probe.kicad_pcb"
        board.write_text("(kicad_pcb)\n", encoding="utf-8")
        if context:
            board.with_suffix(".kicad_pro").write_text("{}\n", encoding="utf-8")
            board.with_suffix(".kicad_sch").write_text("(kicad_sch)\n", encoding="utf-8")
        return board

    def _producer(self, text=_CLEAN_DRC, out="", err=""):
        def produce(cmd, report):
            Path(report).write_text(text, encoding="utf-8")
            return 0, out, err
        return produce

    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_parity_requires_same_stem_project_and_schematic(self, _find):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw, context=False)
            (Path(raw) / "different.kicad_pro").write_text("{}", encoding="utf-8")
            (Path(raw) / "different.kicad_sch").write_text("x", encoding="utf-8")
            with self.assertRaisesRegex(verify.VerifyError, "same-stem"):
                verify.run_drc(board, report=Path(raw) / "drc.rpt")

    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_board_only_path_must_explicitly_disable_parity(self, _find):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw, context=False)
            report = Path(raw) / "drc.rpt"
            with mock.patch.object(
                    verify, "_run_producing", side_effect=self._producer()) as producing:
                rc, counts = verify.run_drc(board, report=report, parity=False)
            self.assertEqual(rc, 0)
            self.assertEqual(counts["drc violations"], 0)
            cmd = producing.call_args.args[0]
            self.assertIn("--refill-zones", cmd)
            self.assertNotIn("--schematic-parity", cmd)

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_parity_command_refills_zones(self, _find, _context):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            report = Path(raw) / "drc.rpt"
            with mock.patch.object(
                    verify, "_run_producing", side_effect=self._producer()) as producing:
                verify.run_drc(board, report=report)
            cmd = producing.call_args.args[0]
            self.assertIn("--refill-zones", cmd)
            self.assertIn("--schematic-parity", cmd)
            self.assertNotIn("--save-board", cmd)

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_zone_snapshot_rejects_changed_refill_despite_clean_drc(
            self, _find, _context):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            report = Path(raw) / "drc.rpt"

            def changed_refill(cmd, report_path):
                self.assertIn("--save-board", cmd)
                board.write_text("changed filled geometry\n", encoding="utf-8")
                Path(report_path).write_text(_CLEAN_DRC, encoding="utf-8")
                return 0, "", ""

            with mock.patch.object(
                    verify, "_run_producing", side_effect=changed_refill):
                with self.assertRaisesRegex(
                        verify.VerifyError, "persisted refill differs"):
                    verify.run_drc(
                        board,
                        report=report,
                        expected_zone_snapshot="finalized geometry",
                        zone_snapshotter=lambda p: Path(p).read_text(
                            encoding="utf-8").strip(),
                    )

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_zone_snapshot_accepts_identical_persisted_refill(
            self, _find, _context):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            board.write_text("finalized geometry\n", encoding="utf-8")
            report = Path(raw) / "drc.rpt"

            def stable_refill(cmd, report_path):
                self.assertIn("--save-board", cmd)
                Path(report_path).write_text(_CLEAN_DRC, encoding="utf-8")
                return 0, "", ""

            with mock.patch.object(
                    verify, "_run_producing", side_effect=stable_refill):
                rc, counts = verify.run_drc(
                    board,
                    report=report,
                    expected_zone_snapshot="finalized geometry",
                    zone_snapshotter=lambda p: Path(p).read_text(
                        encoding="utf-8").strip(),
                )
            self.assertEqual(rc, 0)
            self.assertEqual(counts["drc violations"], 0)

    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_zone_snapshot_arguments_are_paired(self, _find):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            with self.assertRaisesRegex(verify.VerifyError, "supplied together"):
                verify.run_drc(
                    board,
                    report=Path(raw) / "drc.rpt",
                    expected_zone_snapshot={},
                )

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_parity_failure_diagnostic_cannot_return_clean(self, _find, _context):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            report = Path(raw) / "drc.rpt"
            producer = self._producer(
                out="Failed to fetch schematic netlist for parity tests.")
            with mock.patch.object(verify, "_run_producing", side_effect=producer):
                with self.assertRaisesRegex(verify.VerifyError, "did not execute"):
                    verify.run_drc(board, report=report)

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_parity_requires_footprint_summary(self, _find, _context):
        without_footprints = (
            "** Found 0 DRC violations **\n"
            "** Found 0 unconnected pads **\n")
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            report = Path(raw) / "drc.rpt"
            with mock.patch.object(
                    verify, "_run_producing",
                    side_effect=self._producer(without_footprints)):
                with self.assertRaisesRegex(verify.VerifyError, "footprint errors"):
                    verify.run_drc(board, report=report)

    @mock.patch.object(verify, "_run")
    def test_parity_preflight_exports_and_parses_fresh_netlist(self, run):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)

            def export(cmd):
                Path(cmd[cmd.index("-o") + 1]).write_text(
                    _NETLIST, encoding="utf-8")
                return 0, "", ""

            run.side_effect = export
            verify._verify_parity_context(board, "kicad-cli")
            cmd = run.call_args.args[0]
            self.assertEqual(cmd[1:4], ["sch", "export", "netlist"])
            self.assertEqual(Path(cmd[-1]), board.with_suffix(".kicad_sch"))

    @mock.patch.object(verify, "_run")
    def test_parity_preflight_rejects_empty_export(self, run):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)

            def export(cmd):
                Path(cmd[cmd.index("-o") + 1]).write_text("", encoding="utf-8")
                return 0, "", ""

            run.side_effect = export
            with self.assertRaisesRegex(verify.VerifyError, "empty, unannotated"):
                verify._verify_parity_context(board, "kicad-cli")

    @mock.patch.object(verify, "_run")
    def test_parity_preflight_rejects_unannotated_component(self, run):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)

            def export(cmd):
                Path(cmd[cmd.index("-o") + 1]).write_text(
                    _NETLIST.replace('(ref "R1")', '(ref "R?")'),
                    encoding="utf-8")
                return 0, "", ""

            run.side_effect = export
            with self.assertRaisesRegex(verify.VerifyError, "not fully annotated"):
                verify._verify_parity_context(board, "kicad-cli")


if __name__ == "__main__":
    unittest.main()
