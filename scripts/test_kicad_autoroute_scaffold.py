#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_autoroute as autoroute
import kicad_autoroute_scaffold as scaffold


def project(class_name="AutorouteRoutine"):
    return {
        "meta": {"filename": "x.kicad_pro", "version": 1},
        "net_settings": {
            "classes": [
                {
                    "name": "Default", "track_width": 0.2, "clearance": 0.2,
                    "via_diameter": 0.6, "via_drill": 0.3,
                },
                {
                    "name": class_name, "track_width": 0.25, "clearance": 0.2,
                    "via_diameter": 0.6, "via_drill": 0.3,
                },
            ],
            "netclass_assignments": {"/A": [class_name], "/B": [class_name]},
            "netclass_patterns": [{"pattern": "/A*", "netclass": class_name}],
        },
    }


def inspection(class_name="AutorouteRoutine"):
    return {
        "pcbnew": "10.0.5",
        "copper_layers": ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"],
        "net_to_class": {"/A": class_name, "/B": class_name, "GND": "Default"},
        "routes": [
            {
                "uuid": "11111111-1111-1111-1111-111111111111",
                "route": {
                    "kind": "segment", "net": "/A", "layer": "F.Cu",
                    "width_nm": 250_000, "start_nm": [0, 0], "end_nm": [1_000_000, 0],
                },
                "locked": True, "primitive_type": "segment",
            },
            {
                "uuid": "22222222-2222-2222-2222-222222222222",
                "route": {
                    "kind": "via", "net": "/B", "at_nm": [2_000_000, 0],
                    "diameter_nm": 600_000, "drill_nm": 300_000,
                    "layers": ["F.Cu", "B.Cu"],
                },
                "locked": False, "primitive_type": 4,
            },
        ],
    }


class ScaffoldTests(unittest.TestCase):
    @staticmethod
    def _worker_for(value):
        def run(
            _python, mode, _board, _output, _extra=None, timeout_seconds=300
        ):
            _ = timeout_seconds
            if mode == "probe":
                return {"needs_migration": False, "reason": None}
            return value
        return run

    def _existing(self, root: Path):
        board = root / "x.kicad_pcb"
        board.write_text("(kicad_pcb (version 20260206))\n", encoding="utf-8")
        (root / "x.kicad_pro").write_text(json.dumps(project()), encoding="utf-8")
        (root / "x.kicad_sch").write_text("(kicad_sch (version 20231120))\n", encoding="utf-8")
        return board

    def _plan_existing(self, root: Path) -> Path:
        board = self._existing(root)
        plan = root / "work" / "plan.json"
        with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
            scaffold, "_worker", side_effect=self._worker_for(inspection())
        ):
            result = scaffold.main([
                "plan", str(board), "--mode", "board-snapshot",
                "--use-net-class", "AutorouteRoutine",
                "--layer", "F.Cu", "--layer", "B.Cu",
                "--reset-all-selected-routing", "--selected-scope-routine",
                "--output", str(plan),
            ])
        self.assertEqual(result, 0)
        return plan

    def test_snapshot_plan_apply_is_digest_approved_and_idempotent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            digest = autoroute.sha256_path(plan)
            self.assertEqual(
                scaffold.main(["apply", "--plan", str(plan), "--approve-plan-sha256", digest]),
                0,
            )
            self.assertEqual(
                scaffold.main(["apply", "--plan", str(plan), "--approve-plan-sha256", digest]),
                0,
            )
            config = autoroute.load_config(root / "autoroute.json")
            self.assertEqual(config["schema"], autoroute.CONFIG_SCHEMA_V2)
            self.assertEqual(config["scope"]["net_to_class"], {"/A": "AutorouteRoutine", "/B": "AutorouteRoutine"})
            reset = json.loads((root / "autoroute-route-reset.json").read_text())
            self.assertEqual(len(reset["items"]), 2)
            self.assertTrue(reset["items"][0]["locked"])
            autoroute.build_v2_input_bundle(config)

    def test_apply_refuses_stale_plan_and_existing_create_only_file(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            (root / "autoroute_adapter.py").write_text("user file\n", encoding="utf-8")
            with self.assertRaisesRegex(scaffold.ScaffoldError, "create-only"):
                scaffold._apply(mock.Mock(
                    plan=str(plan), approve_plan_sha256=autoroute.sha256_path(plan)
                ))
            with self.assertRaisesRegex(scaffold.ScaffoldError, "approval digest"):
                scaffold._apply(mock.Mock(plan=str(plan), approve_plan_sha256="0" * 64))

    def test_apply_refuses_source_changed_after_plan(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            (root / "x.kicad_pcb").write_text("changed\n", encoding="utf-8")
            with self.assertRaisesRegex(scaffold.ScaffoldError, "changed since approval"):
                scaffold._apply(mock.Mock(
                    plan=str(plan), approve_plan_sha256=autoroute.sha256_path(plan)
                ))

    def test_plan_blocks_migration_before_onboarding(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = self._existing(root)
            def migrating(
                _python, mode, _board, _output, _extra=None,
                timeout_seconds=300,
            ):
                _ = timeout_seconds
                if mode == "probe":
                    return {"needs_migration": True, "reason": "board format changes"}
                return inspection()
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=migrating
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "board-snapshot",
                    "--use-net-class", "AutorouteRoutine", "--layer", "F.Cu",
                    "--selected-scope-routine", "--output", str(root / "plan.json"),
                ]), 2)
            self.assertFalse((root / "plan.json").exists())

    def test_standalone_board_creates_project_but_no_fake_schematic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = root / "standalone.kicad_pcb"
            board.write_text("(kicad_pcb (version 20260206))\n", encoding="utf-8")
            plan = root / "plan.json"
            standalone_inspection = inspection("Default")
            standalone_inspection["net_to_class"] = {"/A": "Default", "/B": "Default"}
            standalone_inspection["routes"] = []
            merged_inspection = inspection("AutorouteRoutine")
            merged_inspection["net_to_class"] = {
                "/A": "AutorouteRoutine", "/B": "AutorouteRoutine"
            }
            merged_inspection["routes"] = []
            inspect_calls = iter([standalone_inspection, merged_inspection])
            def standalone_worker(
                _python, mode, _board, _output, _extra=None,
                timeout_seconds=300,
            ):
                _ = timeout_seconds
                if mode == "probe":
                    return {"needs_migration": False, "reason": None}
                return next(inspect_calls)
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=standalone_worker
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "board-snapshot",
                    "--board-only-authority", "--create-net-class", "AutorouteRoutine",
                    "--net", "/A", "--net", "/B", "--track-width-mm", "0.25",
                    "--clearance-mm", "0.20", "--via-diameter-mm", "0.60",
                    "--via-drill-mm", "0.30", "--layer", "F.Cu", "--layer", "B.Cu",
                    "--selected-scope-routine", "--output", str(plan),
                ]), 0)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            config = autoroute.load_config(root / "autoroute.json")
            self.assertEqual(config["project"]["schematic_authority"], "board-only")
            self.assertTrue((root / "standalone.kicad_pro").is_file())
            self.assertFalse((root / "standalone.kicad_sch").exists())

    def test_generator_template_reports_blocked_adapter(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = self._existing(root)
            source = root / "generator-src"
            source.mkdir()
            (source / "generate.sh").write_text("generator input\n", encoding="utf-8")
            plan = root / "plan.json"
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=self._worker_for(inspection())
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "generator-adapter",
                    "--use-net-class", "AutorouteRoutine", "--layer", "F.Cu",
                    "--layer", "B.Cu", "--project-audited",
                    "--source", "generator=generator-src", "--output", str(plan),
                ]), 0)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            report = root / "describe.json"
            completed = subprocess.run(
                [sys.executable, str(root / "autoroute_adapter.py"), "describe", "--report", str(report)],
                check=False,
            )
            self.assertEqual(completed.returncode, 0)
            self.assertEqual(json.loads(report.read_text())["status"], "BLOCKED_ADAPTER")

    def test_recursive_source_membership_change_is_stale(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ])
            config_path = root / "autoroute.json"
            value = json.loads(config_path.read_text())
            source = root / "src"
            source.mkdir()
            (source / "a.txt").write_text("a", encoding="utf-8")
            value["sources"].append({
                "role": "generator", "kind": "directory-recursive", "path": "src",
                "sha256": autoroute.declared_source_digest(source, "directory-recursive"),
            })
            config_path.write_bytes(autoroute.canonical_json_bytes(value))
            config = autoroute.load_config(config_path)
            autoroute.build_v2_input_bundle(config)
            (source / "b.txt").write_text("b", encoding="utf-8")
            with self.assertRaisesRegex(autoroute.AutorouteError, "digest mismatch"):
                autoroute.build_v2_input_bundle(config)

    def test_reset_rejects_coincident_duplicate_geometry(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = self._existing(root)
            duplicated = inspection()
            second = dict(duplicated["routes"][0])
            second["uuid"] = "33333333-3333-3333-3333-333333333333"
            duplicated["routes"] = [duplicated["routes"][0], second]
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=self._worker_for(duplicated)
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "board-snapshot",
                    "--use-net-class", "AutorouteRoutine", "--layer", "F.Cu",
                    "--reset-all-selected-routing", "--selected-scope-routine",
                    "--output", str(root / "plan.json"),
                ]), 2)

    def test_plan_and_check_reports_cannot_overwrite_schematic(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = self._existing(root)
            schematic = board.with_suffix(".kicad_sch")
            before = schematic.read_bytes()
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=self._worker_for(inspection())
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "board-snapshot",
                    "--use-net-class", "AutorouteRoutine", "--layer", "F.Cu",
                    "--selected-scope-routine", "--output", str(schematic),
                ]), 2)
            self.assertEqual(schematic.read_bytes(), before)

            plan = self._plan_existing(root)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            self.assertEqual(scaffold.main([
                "check", str(board), "--report", str(schematic),
            ]), 2)
            self.assertEqual(schematic.read_bytes(), before)

    def test_generator_recursive_root_source_is_rejected_before_apply(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = self._existing(root)
            plan = root.parent / (root.name + "-plan.json")
            with mock.patch.object(scaffold, "_find_kicad_python", return_value=Path(sys.executable)), mock.patch.object(
                scaffold, "_worker", side_effect=self._worker_for(inspection())
            ):
                self.assertEqual(scaffold.main([
                    "plan", str(board), "--mode", "generator-adapter",
                    "--use-net-class", "AutorouteRoutine", "--layer", "F.Cu",
                    "--project-audited", "--source", "generator=.",
                    "--output", str(plan),
                ]), 2)
            self.assertFalse((root / "autoroute.json").exists())
            self.assertFalse((root / "autoroute_adapter.py").exists())

    def test_null_netclass_assignments_are_normalized_for_class_creation(self):
        value = scaffold._minimal_project("x.kicad_pro")
        value["net_settings"]["netclass_assignments"] = None
        merged = scaffold._merge_new_class(
            value, "Routine", ["/A"], {
                "track_width_nm": 250_000, "clearance_nm": 200_000,
                "via_diameter_nm": 600_000, "via_drill_nm": 300_000,
            },
        )
        self.assertEqual(
            merged["net_settings"]["netclass_assignments"],
            {"/A": ["Routine"]},
        )

    def test_tool_repin_is_digest_planned_and_applied(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            adapter = root / "autoroute_adapter.py"
            adapter.write_text(adapter.read_text(encoding="utf-8") + "\n# reviewed edit\n", encoding="utf-8")
            with self.assertRaisesRegex(autoroute.AutorouteError, "configured adapter digest mismatch"):
                autoroute.build_v2_input_bundle(autoroute.load_config(root / "autoroute.json"))
            repin = root / "work" / "repin.json"
            self.assertEqual(scaffold.main([
                "repin-plan", "--config", str(root / "autoroute.json"),
                "--output", str(repin),
            ]), 0)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(repin),
                "--approve-plan-sha256", autoroute.sha256_path(repin),
            ]), 0)
            config = autoroute.load_config(root / "autoroute.json")
            self.assertEqual(config["tools"]["adapter"]["sha256"], autoroute.sha256_path(adapter))
            autoroute.build_v2_input_bundle(config)

    def test_v2_generated_targets_must_be_pairwise_disjoint(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            config_path = root / "autoroute.json"
            value = json.loads(config_path.read_text(encoding="utf-8"))
            value["promotion"]["manifest"] = "autoroute.json"
            config_path.write_bytes(autoroute.canonical_json_bytes(value))
            with self.assertRaisesRegex(autoroute.AutorouteError, "shared by"):
                autoroute.load_config(config_path)

    def test_internal_scaffold_worker_requires_scratch_root(self):
        with mock.patch.dict(scaffold.os.environ, {}, clear=True):
            with self.assertRaisesRegex(scaffold.ScaffoldError, "orchestrator scratch root"):
                scaffold._pcb_worker(["inspect", "x.kicad_pcb", "report.json"])

    def test_check_rejects_board_argument_that_differs_from_config(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            self._existing(root)
            plan = self._plan_existing(root)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            wrong = root / "wrong.kicad_pcb"
            report = root / "work" / "mismatch-report.json"
            self.assertEqual(scaffold.main([
                "check", str(wrong), "--config", str(root / "autoroute.json"),
                "--report", str(report),
            ]), 3)
            result = json.loads(report.read_text(encoding="utf-8"))
            self.assertEqual(result["status"], "BLOCKED_PROJECT_CONTEXT")
            self.assertIn("does not match", result["reason"])

    def test_snapshot_adapter_describe_cannot_overwrite_audit_tool(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            plan = self._plan_existing(root)
            self.assertEqual(scaffold.main([
                "apply", "--plan", str(plan),
                "--approve-plan-sha256", autoroute.sha256_path(plan),
            ]), 0)
            audit = root / "autoroute_audit.py"
            before = autoroute.sha256_path(audit)
            completed = subprocess.run(
                [
                    sys.executable, str(root / "autoroute_adapter.py"),
                    "describe", "--report", str(audit),
                ],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                check=False, timeout=10,
            )
            self.assertNotEqual(completed.returncode, 0)
            self.assertEqual(autoroute.sha256_path(audit), before)


if __name__ == "__main__":
    unittest.main()
