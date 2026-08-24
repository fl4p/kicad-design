#!/usr/bin/env python3

from __future__ import annotations

import json
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
    def test_explicit_no_refill_json_save_and_timeout_are_propagated(
            self, _find, _context):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            report = Path(raw) / "drc.json"

            def produce(cmd, report_path, timeout=None):
                self.assertNotIn("--refill-zones", cmd)
                self.assertIn("--save-board", cmd)
                self.assertEqual(timeout, 7.5)
                Path(report_path).write_text(json.dumps({
                    "violations": [],
                    "unconnected_items": [],
                    "schematic_parity": [],
                }), encoding="utf-8")
                return 0, "", ""

            with mock.patch.object(verify, "_run_producing", side_effect=produce):
                rc, counts = verify.run_drc(
                    board,
                    report=report,
                    refill=False,
                    save_board=True,
                    output_format="json",
                    timeout=7.5,
                )
            self.assertEqual(rc, 0)
            self.assertEqual(counts, {
                "drc violations": 0,
                "unconnected pads": 0,
                "footprint errors": 0,
            })
            _context.assert_called_once_with(board, "kicad-cli", timeout=7.5)

    def test_json_findings_and_ignored_checks_are_normalized(self):
        with tempfile.TemporaryDirectory() as raw:
            report = Path(raw) / "drc.json"
            report.write_text(json.dumps({
                "violations": [{
                    "type": "silk_overlap", "severity": "warning",
                    "description": "Silkscreen overlap", "items": [{"uuid": "abc"}],
                }],
                "unconnected_items": [],
                "schematic_parity": [{
                    "severity": "error", "description": "Field mismatch",
                    "items": [{"uuid": "def"}],
                }],
                "ignored_checks": [{"key": "footprint_filter", "description": "Footprint filters"}],
            }), encoding="utf-8")
            findings = verify.normalized_findings_from_json_report(report, "drc")
            self.assertEqual([item["category"] for item in findings], [
                "violations", "schematic_parity",
            ])
            self.assertEqual(findings[0]["type"], "silk_overlap")
            self.assertRegex(findings[0]["payload_sha256"], r"^[0-9a-f]{64}$")
            self.assertEqual(verify.ignored_checks_from_report(report), [
                "Footprint filters",
            ])

    def test_json_findings_reject_nonobject_entries(self):
        with tempfile.TemporaryDirectory() as raw:
            report = Path(raw) / "drc.json"
            report.write_text(json.dumps({
                "violations": ["bad"], "unconnected_items": [],
                "schematic_parity": [], "ignored_checks": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(verify.VerifyError, "not an object"):
                verify.normalized_findings_from_json_report(report, "drc")

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_board_snapshot_rejects_changed_semantics_despite_clean_drc(
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
                        verify.VerifyError, "persisted board semantics"):
                    verify.run_drc(
                        board,
                        report=report,
                        expected_board_snapshot="finalized geometry",
                        board_snapshotter=lambda p: Path(p).read_text(
                            encoding="utf-8").strip(),
                    )

    @mock.patch.object(verify, "_verify_parity_context")
    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_board_snapshot_accepts_identical_persisted_semantics(
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
                    expected_board_snapshot="finalized geometry",
                    board_snapshotter=lambda p: Path(p).read_text(
                        encoding="utf-8").strip(),
                )
            self.assertEqual(rc, 0)
            self.assertEqual(counts["drc violations"], 0)

    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_release_board_snapshot_arguments_are_paired(self, _find):
        with tempfile.TemporaryDirectory() as raw:
            board = self._board(raw)
            with self.assertRaisesRegex(verify.VerifyError, "supplied together"):
                verify.run_drc(
                    board,
                    report=Path(raw) / "drc.rpt",
                    expected_board_snapshot={},
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


class VerifyReportTests(unittest.TestCase):
    def _report(self, root, text, name="report.rpt"):
        path = Path(root) / name
        path.write_text(text, encoding="utf-8")
        return path

    def test_live_kicad10_style_drc_and_erc_summaries_parse(self):
        with tempfile.TemporaryDirectory() as raw:
            drc = self._report(
                raw,
                "** Found 64 DRC violations **\n"
                "** Found 0 unconnected pads **\n"
                "** Found 0 Footprint errors **\n",
                "drc.rpt",
            )
            self.assertEqual(
                verify._counts_from_report(drc, kind="drc"),
                {
                    "drc violations": 64,
                    "unconnected pads": 0,
                    "footprint errors": 0,
                },
            )
            erc = self._report(
                raw,
                "** ERC messages: 3 Errors 2 Warnings 1 **\n",
                "erc.rpt",
            )
            self.assertEqual(
                verify._counts_from_report(erc, kind="erc"),
                {"erc messages": 3, "errors": 2, "warnings": 1},
            )

    def test_malformed_and_truncated_reports_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            malformed = self._report(raw, "no summary here\n")
            with self.assertRaisesRegex(
                    verify.VerifyError, "missing required DRC summary"):
                verify._counts_from_report(malformed, kind="drc")
            truncated = self._report(
                raw, "** Found 0 DRC violations **\n", "truncated.rpt")
            with self.assertRaisesRegex(verify.VerifyError, "unconnected pads"):
                verify._counts_from_report(truncated, kind="drc")

    def test_conflicting_duplicate_drc_and_erc_summaries_fail_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            drc = self._report(
                raw,
                "** Found 2 DRC violations **\n"
                "** Found 0 DRC violations **\n"
                "** Found 0 unconnected pads **\n",
                "drc.rpt",
            )
            with self.assertRaisesRegex(verify.VerifyError, "different counts"):
                verify._counts_from_report(drc, kind="drc")
            erc = self._report(
                raw,
                "** ERC messages: 0 Errors 0 Warnings 0 **\n"
                "** ERC messages: 1 Errors 1 Warnings 0 **\n",
                "erc.rpt",
            )
            with self.assertRaisesRegex(verify.VerifyError, "conflicting ERC"):
                verify._counts_from_report(erc, kind="erc")

    def test_both_ignored_check_header_shapes_and_absence(self):
        with tempfile.TemporaryDirectory() as raw:
            erc = self._report(
                raw,
                "** Ignored checks:\n"
                "- Pin not connected\n"
                "** ERC messages: 0 Errors 0 Warnings 0 **\n",
                "erc.rpt",
            )
            self.assertEqual(
                verify.ignored_checks_from_report(erc), ["Pin not connected"])
            drc = self._report(
                raw,
                "** Ignored checks **\n"
                "- Footprint doesn't match filters\n"
                "** Found 0 DRC violations **\n",
                "drc.rpt",
            )
            self.assertEqual(
                verify.ignored_checks_from_report(drc),
                ["Footprint doesn't match filters"],
            )
            empty = self._report(
                raw, "** Ignored checks **\n    - None\n** End of Report **\n",
                "empty.rpt")
            self.assertEqual(verify.ignored_checks_from_report(empty), [])
            contradictory = self._report(
                raw,
                "** Ignored checks **\n- None\n- Real ignored check\n"
                "** End of Report **\n",
                "contradictory.rpt",
            )
            with self.assertRaisesRegex(verify.VerifyError, "contradictory"):
                verify.ignored_checks_from_report(contradictory)
            absent = self._report(raw, "** Found 0 DRC violations **\n", "absent.rpt")
            self.assertIsNone(verify.ignored_checks_from_report(absent))

    def test_run_producing_rejects_missing_and_stale_reports(self):
        with tempfile.TemporaryDirectory() as raw:
            report = Path(raw) / "drc.rpt"
            with mock.patch.object(verify, "_run", return_value=(0, "", "")):
                with self.assertRaisesRegex(verify.VerifyError, "did not write"):
                    verify._run_producing(["kicad-cli", "pcb"], report)
            report.write_text("stale\n", encoding="utf-8")
            with mock.patch.object(verify, "_run", return_value=(0, "", "")):
                with self.assertRaisesRegex(verify.VerifyError, "mtime unchanged"):
                    verify._run_producing(["kicad-cli", "pcb"], report)

    def test_subprocess_timeout_fails_closed(self):
        with mock.patch.object(
                verify.subprocess, "run",
                side_effect=verify.subprocess.TimeoutExpired(["kicad-cli"], 0.1)):
            with self.assertRaisesRegex(verify.VerifyError, "deadline"):
                verify._run(["kicad-cli", "pcb"], timeout=0.1)

    def test_run_producing_accepts_current_rewrite(self):
        with tempfile.TemporaryDirectory() as raw:
            report = Path(raw) / "erc.rpt"

            def rewrite(_cmd):
                report.write_text("fresh report\n", encoding="utf-8")
                return 0, "out", "err"

            with mock.patch.object(verify, "_run", side_effect=rewrite):
                self.assertEqual(
                    verify._run_producing(["kicad-cli", "sch"], report),
                    (0, "out", "err"),
                )

    @mock.patch.object(verify, "find_kicad_cli", return_value="kicad-cli")
    def test_run_erc_parses_fresh_report_and_preserves_nonclean_status(self, _find):
        with tempfile.TemporaryDirectory() as raw:
            schematic = Path(raw) / "probe.kicad_sch"
            schematic.write_text("(kicad_sch)\n", encoding="utf-8")
            report = Path(raw) / "erc.rpt"

            def produce(_cmd, report_path):
                Path(report_path).write_text(
                    "** ERC messages: 1 Errors 1 Warnings 0 **\n",
                    encoding="utf-8",
                )
                return 5, "", ""

            with mock.patch.object(
                    verify, "_run_producing", side_effect=produce):
                rc, counts = verify.run_erc(schematic, report=report)
            self.assertEqual(rc, 5)
            self.assertEqual(counts["errors"], 1)

    def test_severity_report_rejects_missing_and_malformed_project_maps(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "probe.kicad_pro"
            project.write_text("{}\n", encoding="utf-8")
            self.assertEqual(verify.severity_report(project)["state"], "unverified")

            project.write_text(json.dumps({
                "erc": {"rule_severities": []},
                "board": {"design_settings": {"rule_severities": "bad"}},
            }), encoding="utf-8")
            invalid = verify.severity_report(project)
            self.assertEqual(invalid["state"], "unverified")
            self.assertIn("invalid shape", invalid["note"])

            project.write_text("[]\n", encoding="utf-8")
            invalid_root = verify.severity_report(project)
            self.assertEqual(invalid_root["state"], "unverified")
            self.assertIn("project root", invalid_root["note"])

    def test_severity_report_keeps_nonempty_sparse_maps_unverified(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "probe.kicad_pro"
            project.write_text(json.dumps({
                "erc": {"rule_severities": {"pin_not_connected": "ignore"}},
                "board": {"design_settings": {
                    "rule_severities": {"invalid_outline": "error"}
                }},
            }), encoding="utf-8")
            sparse = verify.severity_report(project)
            self.assertEqual(sparse["state"], "unverified")
            self.assertIn("sparse overrides", sparse["note"])
            self.assertIn("complete rule universe", sparse["note"])
            self.assertEqual(
                sparse["configured_erc_ignored"], ["pin_not_connected"])
            self.assertEqual(sparse["effective_erc_ignored"], [])

    def test_severity_report_rejects_invalid_configured_value(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "probe.kicad_pro"
            project.write_text(json.dumps({
                "erc": {"rule_severities": {"x": "silenced"}},
                "board": {"design_settings": {"rule_severities": {"y": "error"}}},
            }), encoding="utf-8")
            bad_value = verify.severity_report(project)
            self.assertEqual(bad_value["state"], "unverified")
            self.assertIn("non-KiCad severity", bad_value["note"])

    def test_severity_report_rejects_self_attested_effective_resolution(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "probe.kicad_pro"
            project.write_text(json.dumps({
                "erc": {"rule_severities": {"pin_not_connected": "ignore"}},
                "board": {"design_settings": {
                    "rule_severities": {"invalid_outline": "error"}
                }},
            }), encoding="utf-8")
            result = verify.severity_report(project, {
                "complete": True,
                "kicad_version": "10.0.5",
                "erc": {
                    "pin_not_connected": "ignore",
                    "pin_not_driven": "warning",
                },
                "drc": {
                    "invalid_outline": "error",
                    "clearance": "error",
                    "footprint_type_mismatch": "ignore",
                },
            })
            self.assertEqual(result["state"], "unverified")
            self.assertIsNone(result["kicad_version"])
            self.assertEqual(result["effective_erc_entries"], 0)
            self.assertEqual(result["effective_drc_ignored"], [])
            self.assertIn("untrusted self-attestation", result["note"])

    def test_severity_report_rejects_arbitrary_resolution_versions_and_shapes(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw) / "probe.kicad_pro"
            project.write_text(json.dumps({
                "erc": {"rule_severities": {"pin_not_connected": "ignore"}},
                "board": {"design_settings": {
                    "rule_severities": {"invalid_outline": "error"}
                }},
            }), encoding="utf-8")

            invented = verify.severity_report(project, {
                "complete": True,
                "kicad_version": "definitely-not-a-KiCad-version",
                "erc": {"pin_not_connected": "ignore"},
                "drc": {"invalid_outline": "error"},
            })
            self.assertEqual(invented["state"], "unverified")
            self.assertIn("untrusted self-attestation", invented["note"])

            malformed = verify.severity_report(project, "complete")
            self.assertEqual(malformed["state"], "unverified")
            self.assertIn("untrusted self-attestation", malformed["note"])


if __name__ == "__main__":
    unittest.main()
