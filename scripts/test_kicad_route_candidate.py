#!/usr/bin/env python3
"""Focused tests for the pure safety/reporting parts of the route wrapper."""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_route_candidate as route


def segment(net="N", locked=False, start=(0, 0), end=(10, 0), width=200_000):
    return {
        "kind": "segment",
        "net": net,
        "locked": locked,
        "width_nm": width,
        "layer": "F.Cu",
        "start_nm": list(start),
        "end_nm": list(end),
        "length_nm": 10,
    }


class RouteCandidateTests(unittest.TestCase):
    @staticmethod
    def exploratory_args(board: Path, report: Path) -> list[str]:
        return [
            str(board),
            "--report",
            str(report),
            "--exploratory",
            "--allow-all-net-classes",
            "--allow-layer",
            "F.Cu",
        ]

    def test_unconfigured_router_run_requires_explicit_exploratory_scope(self):
        parser = route._parser()
        args = parser.parse_args(["x.kicad_pcb", "--report", "report.json"])
        with self.assertRaises(SystemExit):
            route._configure_args(args, parser)

        args = parser.parse_args(
            [
                "x.kicad_pcb",
                "--report",
                "report.json",
                "--exploratory",
                "--allow-net-class",
                "Routine",
                "--allow-layer",
                "F.Cu",
            ]
        )
        route._configure_args(args, parser)
        self.assertEqual(route._report_mode(args), "exploratory-report")
        self.assertIsNone(args._autoroute_config)

    def test_exploratory_requires_explicit_layer_and_rejects_prepare_only(self):
        parser = route._parser()
        missing_layer = parser.parse_args(
            [
                "x.kicad_pcb",
                "--report",
                "report.json",
                "--exploratory",
                "--allow-all-net-classes",
            ]
        )
        with self.assertRaises(SystemExit):
            route._configure_args(missing_layer, parser)

        contradictory = parser.parse_args(
            [
                "x.kicad_pcb",
                "--report",
                "report.json",
                "--prepare-only",
                "--exploratory",
            ]
        )
        with self.assertRaises(SystemExit):
            route._configure_args(contradictory, parser)

    def test_exploratory_finalization_strips_promotion_evidence(self):
        args = argparse.Namespace(
            prepare_only=False,
            exploratory=True,
            fail_on_findings=False,
        )
        report = {
            "mode": route._report_mode(args),
            "promotion": {"must_not_survive": True},
        }
        exit_code = route._finalize_report(
            report,
            findings=[],
            promotion_blocks=[],
            args=args,
            config={"tracked": True},
            project_audits={"configured": True},
        )
        self.assertEqual(exit_code, 0)
        self.assertEqual(report["mode"], "exploratory-report")
        self.assertEqual(report["verdict"], "EXPLORATORY")
        self.assertNotIn("promotion", report)
        self.assertIn("cannot be promoted", report["verdict_reason"])

    def test_router_command_is_local_bounded_and_not_drc_only(self):
        command = route._router_command(
            Path("/java"),
            Path("/router.jar"),
            Path("/scratch/in.dsn"),
            Path("/scratch/out.ses"),
            Path("/scratch"),
            ["Power", "Critical"],
            7,
            2,
            300,
        )
        self.assertNotIn("-drc", command)
        self.assertIn("--gui.enabled=false", command)
        self.assertIn("--api_server.enabled=false", command)
        self.assertIn("--router.copper_to_edge_clearance_um=300", command)
        self.assertIn("--router.automatic_neckdown=false", command)
        self.assertIn("--router.fanout.enabled=false", command)
        self.assertEqual(command[command.index("-mp") + 1], "7")
        self.assertEqual(command[command.index("-mt") + 1], "2")
        self.assertEqual(command[command.index("-inc") + 1], "Power,Critical")

    def test_router_command_refuses_ambiguous_class_name(self):
        with self.assertRaisesRegex(route.RouteReportError, "comma"):
            route._router_command(
                Path("/java"), Path("/r.jar"), Path("/i.dsn"),
                Path("/o.ses"), Path("/scratch"), ["A,B"], 1, 1, 250
            )

    def test_router_environment_drops_ambient_router_and_java_options(self):
        with mock.patch.dict(
            route.os.environ,
            {
                "FREEROUTING__ROUTER__MAX_PASSES": "999",
                "JAVA_TOOL_OPTIONS": "-javaagent:surprise.jar",
                "KEEP_ME": "yes",
            },
            clear=True,
        ):
            env, removed = route._router_environment()
        self.assertEqual(env["KEEP_ME"], "yes")
        self.assertEqual(env["LC_ALL"], "C")
        self.assertNotIn("FREEROUTING__ROUTER__MAX_PASSES", env)
        self.assertNotIn("JAVA_TOOL_OPTIONS", env)
        self.assertEqual(
            removed,
            ["FREEROUTING__ROUTER__MAX_PASSES", "JAVA_TOOL_OPTIONS"],
        )

    def test_route_delta_preserves_duplicate_multiplicity(self):
        item = segment()
        seed = {"routing": {"items": [item]}}
        candidate = {"routing": {"items": [item, item]}}
        delta = route._route_delta(seed, candidate)
        self.assertEqual(delta["added"]["count"], 1)
        self.assertEqual(delta["removed"]["count"], 0)

    def test_route_delta_treats_canonical_segment_as_undirected(self):
        forward = segment(start=(0, 0), end=(10, 0))
        reverse = segment(start=(10, 0), end=(0, 0))
        # _route_item canonicalizes this before the delta layer; model that
        # normalized form explicitly so the regression stays a pure test.
        reverse["start_nm"], reverse["end_nm"] = sorted(
            (reverse["start_nm"], reverse["end_nm"])
        )
        delta = route._route_delta(
            {"routing": {"items": [forward]}},
            {"routing": {"items": [reverse]}},
        )
        self.assertEqual(delta["added"]["count"], 0)
        self.assertEqual(delta["removed"]["count"], 0)

    def test_route_delta_ignores_lock_and_derived_length(self):
        before = segment(locked=True)
        after = segment(locked=False)
        after["length_nm"] = 11
        delta = route._route_delta(
            {"routing": {"items": [before]}},
            {"routing": {"items": [after]}},
        )
        self.assertEqual(delta["added"]["count"], 0)
        self.assertEqual(delta["removed"]["count"], 0)

    def test_scope_reports_disallowed_change(self):
        seed = {
            "netclasses": {
                "class_names": ["Default", "Critical"],
                "net_to_class": {"GPIO": "Default", "CLK": "Critical"},
            }
        }
        delta = {"_added": [segment("CLK")], "_removed": []}
        report = route._scope_report(seed, delta, ["Default"], False)
        self.assertEqual(report["ignored_net_classes"], ["Critical"])
        self.assertEqual(report["resolved_allowed_nets"], ["GPIO"])
        self.assertEqual(report["violations_count"], 1)

    def test_scope_requires_explicit_choice(self):
        seed = {
            "netclasses": {
                "class_names": ["Default"],
                "net_to_class": {"N": "Default"},
            }
        }
        with self.assertRaisesRegex(route.RouteReportError, "requires"):
            route._scope_report(seed, {"_added": [], "_removed": []}, [], False)

    def test_layer_scope_reports_segment_outside_allowlist(self):
        item = segment()
        item["layer"] = "In1.Cu"
        report = route._layer_scope_report(
            {"_added": [item], "_removed": []}, ["F.Cu", "B.Cu"]
        )
        self.assertEqual(report["violations_count"], 1)

    def test_dsn_layer_scope_is_inserted_into_selected_class(self):
        with tempfile.TemporaryDirectory() as raw:
            dsn = Path(raw) / "board.dsn"
            dsn.write_text(
                "(pcb x\n  (network\n    (class Auto N1\n"
                "      (circuit\n        (use_via V1)\n      )\n"
                "      (rule (width 250))\n    )\n  )\n)\n",
                encoding="utf-8",
            )
            result = route._apply_dsn_layer_scope(
                dsn, ["Auto"], ["F.Cu", "B.Cu"]
            )
            self.assertTrue(result["applied"])
            self.assertIn(
                "(use_layer F.Cu B.Cu)", dsn.read_text(encoding="utf-8")
            )

    def test_dsn_fixed_route_gate_counts_path_edges_and_vias(self):
        with tempfile.TemporaryDirectory() as raw:
            dsn = Path(raw) / "board.dsn"
            dsn.write_text(
                "(pcb x (structure\n"
                "  (layer F.Cu (type signal))\n"
                "  (layer B.Cu (type signal))\n"
                ") (wiring\n"
                "  (wire (path F.Cu 250 0 0 10 0 10 10)"
                " (net N)(type fix))\n"
                '  (via "Via[0-1]_600:300_um" 10 10 (net N)(type fix))\n))\n',
                encoding="utf-8",
            )
            seed = {
                "routing": {
                    "locked_items": [
                        segment(
                            start=(0, 0), end=(10_000, 0), locked=True,
                            width=250_000,
                        ),
                        segment(
                            start=(10_000, 0),
                            end=(10_000, -10_000),
                            locked=True,
                            width=250_000,
                        ),
                        {
                            "kind": "via",
                            "net": "N",
                            "position_nm": [10_000, -10_000],
                            "top_layer": "F.Cu",
                            "bottom_layer": "B.Cu",
                            "width_nm": 600_000,
                            "drill_nm": 300_000,
                        },
                    ]
                }
            }
            report = route._dsn_fixed_route_report(dsn, seed)
            self.assertTrue(report["passed"])
            self.assertEqual(report["fixed_wire_edges"], 2)
            self.assertEqual(report["fixed_vias"], 1)
            dsn.write_text(
                dsn.read_text(encoding="utf-8").replace(
                    "(path F.Cu 250 0 0 10 0 10 10)",
                    "(path F.Cu 250 0 0 11 0 10 10)",
                ),
                encoding="utf-8",
            )
            mismatch = route._dsn_fixed_route_report(dsn, seed)
            self.assertFalse(mismatch["passed"])
            self.assertGreater(
                mismatch["geometry_bijection"]["missing_segments"], 0
            )

    def test_project_local_libraries_are_part_of_scratch_inputs(self):
        with tempfile.TemporaryDirectory() as raw:
            project = Path(raw)
            board = project / "x.kicad_pcb"
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            table = project / "fp-lib-table"
            table.write_text(
                '(fp_lib_table (lib (name "x")(type "KiCad")'
                '(uri "${KIPRJMOD}/x.pretty")(options "")(descr "")))\n',
                encoding="utf-8",
            )
            library = project / "x.pretty"
            library.mkdir()
            (library / "A.kicad_mod").write_text("(footprint A)\n", encoding="utf-8")
            related = route._related_sources(board, no_parity=True)
            self.assertIn("project-table:fp-lib-table", related)
            self.assertEqual(
                related["project-resource:x.pretty"], library.resolve()
            )
            scratch = project / "scratch"
            scratch.mkdir()
            copied = route._copy_sources(related, scratch)
            self.assertTrue(
                copied["project-resource:x.pretty"].joinpath("A.kicad_mod").is_file()
            )

    def test_locked_route_loss_is_reported(self):
        item = segment(locked=True)
        seed = {"routing": {"locked_items": [item]}}
        candidate = {"routing": {"locked_items": []}}
        report = route._locked_route_report(seed, candidate)
        self.assertEqual(report["missing_count"], 1)

    def test_protected_route_report_allows_additions_but_not_seed_loss(self):
        seed_item = segment(locked=True)
        addition = segment(net="M", start=(0, 1), end=(10, 1))
        seed = {"routing": {"items": [seed_item]}}
        candidate = {"routing": {"items": [segment(locked=False), addition]}}
        report = route._protected_route_report(seed, candidate)
        self.assertEqual(report["missing_count"], 0)
        self.assertEqual(report["new_count"], 0)
        missing = route._protected_route_report(
            seed, {"routing": {"items": [addition]}}
        )
        self.assertEqual(missing["missing_count"], 1)

    def test_jar_pin_is_enforced(self):
        with tempfile.TemporaryDirectory() as raw:
            jar = Path(raw) / "router.jar"
            jar.write_bytes(b"not really a jar")
            actual = route.digest(jar)
            path, found = route.resolve_router_jar(str(jar), actual, False)
            self.assertEqual(path, jar.resolve())
            self.assertEqual(found, actual)
            with self.assertRaisesRegex(route.RouteReportError, "digest mismatch"):
                route.resolve_router_jar(str(jar), "0" * 64, False)

    def test_atomic_json_report(self):
        with tempfile.TemporaryDirectory() as raw:
            target = Path(raw) / "report.json"
            route._write_json_atomic(target, {"verdict": "REVIEW"})
            self.assertEqual(
                json.loads(target.read_text(encoding="utf-8")),
                {"verdict": "REVIEW"},
            )

    def test_report_collision_cannot_overwrite_source_board(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Path(raw) / "x.kicad_pcb"
            original = b"(kicad_pcb source-must-survive)\n"
            board.write_bytes(original)
            with self.assertRaises(SystemExit):
                route.main(self.exploratory_args(board, board))
            self.assertEqual(board.read_bytes(), original)

    def test_report_collision_cannot_overwrite_source_sidecar(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Path(raw) / "x.kicad_pcb"
            project = board.with_suffix(".kicad_pro")
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            original = b'{"source":"must survive"}\n'
            project.write_bytes(original)
            with self.assertRaises(SystemExit):
                route.main(self.exploratory_args(board, project))
            self.assertEqual(project.read_bytes(), original)

    def test_report_collision_cannot_overwrite_project_library_member(self):
        with tempfile.TemporaryDirectory() as raw:
            project_dir = Path(raw)
            board = project_dir / "x.kicad_pcb"
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            (project_dir / "fp-lib-table").write_text(
                '(fp_lib_table (lib (name "x")(type "KiCad")'
                '(uri "${KIPRJMOD}/x.pretty")(options "")(descr "")))\n',
                encoding="utf-8",
            )
            library = project_dir / "x.pretty"
            library.mkdir()
            member = library / "A.kicad_mod"
            original = b"(footprint A source-must-survive)\n"
            member.write_bytes(original)
            with self.assertRaises(SystemExit):
                route.main(self.exploratory_args(board, member))
            self.assertEqual(member.read_bytes(), original)

    def test_nonrouting_points_absorb_only_nanometre_canonicalization(self):
        class Point:
            x = 16_774_999
            y = 65_379_999

        self.assertEqual(route._nonrouting_point(Point()), [16_775_000, 65_380_000])

    def test_project_audit_commands_are_argv_not_shell(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            board = workspace / "x.kicad_pcb"
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            command = json.dumps(
                [
                    sys.executable,
                    "-c",
                    "import pathlib,sys; assert pathlib.Path(sys.argv[1]).suffix == '.kicad_pcb'",
                    "{board}",
                ]
            )
            report = route._run_audit_commands(
                [command], board, workspace, timeout_seconds=10
            )
            self.assertEqual(report["passed"], 1)
            self.assertEqual(report["failed"], 0)

    def test_promotable_audit_requires_calibration_marker_and_records_digests(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = root / "x.kicad_pcb"
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            audit = root / "audit.py"
            audit.write_text("print('CALIBRATION PASS: fixture fired')\n", encoding="utf-8")
            entry = {
                "interpreter": "kicad_python",
                "argv": ["audit.py", "{board}"],
                "timeout_seconds": 10,
                "calibration_marker": "CALIBRATION PASS:",
            }
            report = route._run_structured_audits(
                [entry], board=board, workspace=root,
                config_dir=root, kicad_python=Path(sys.executable),
            )
            self.assertEqual(report["failed"], 0)
            self.assertTrue(report["calibration_passed"])
            self.assertEqual(len(report["results"][0]["program_sha256"]), 64)
            entry["calibration_marker"] = "MISSING MARKER"
            rejected = route._run_structured_audits(
                [entry], board=board, workspace=root,
                config_dir=root, kicad_python=Path(sys.executable),
            )
            self.assertEqual(rejected["failed"], 1)
            self.assertFalse(rejected["calibration_passed"])


if __name__ == "__main__":
    unittest.main()
