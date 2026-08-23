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
import kicad_route_manifest as manifest
import kicad_graphics as graphics


def segment(
    net="N",
    locked=False,
    start=(0, 0),
    end=(10, 0),
    width=200_000,
    uuid="11111111-1111-4111-8111-111111111111",
):
    return {
        "uuid": uuid,
        "kind": "segment",
        "net": net,
        "locked": locked,
        "width_nm": width,
        "layer": "F.Cu",
        "start_nm": list(start),
        "end_nm": list(end),
        "length_nm": 10,
    }


def semantic_snapshot():
    board = {
        "copper_layer_count": 0,
        "copper_layers": [],
        "enabled_layers": [],
    }
    routes = []
    locked = []
    nonrouting_items = {
        "board": board, "footprints": [], "zones": [], "drawings": []
    }
    return {
        "schema": route.SNAPSHOT_SCHEMA,
        "board": board,
        "netclasses": {"class_names": [], "net_to_class": {}},
        "routing": {
            "items": routes,
            "locked_items": locked,
            "summary": {
                "count": 0,
                "by_kind": {},
                "by_net": {},
                "total_track_length_mm": 0.0,
                "sha256": route._json_digest(routes),
                "locked_count": 0,
                "locked_sha256": route._json_digest(locked),
            },
        },
        "nonrouting_sha256": route._json_digest(nonrouting_items),
        "nonrouting_point_quantum_nm": 10,
        "nonrouting_category_sha256": {
            key: route._json_digest(value)
            for key, value in nonrouting_items.items()
        },
        "nonrouting_items": nonrouting_items,
        "nonrouting_counts": {"footprints": 0, "zones": 0, "drawings": 0},
        "identities": {},
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

    def test_semantic_snapshot_schema_is_exact_at_worker_boundary(self):
        snapshot = semantic_snapshot()
        self.assertIs(
            route._validate_semantic_snapshot(snapshot, "test"), snapshot
        )
        snapshot["nonrouting_point_quantum_nm"] = 100
        with self.assertRaisesRegex(route.RouteReportError, "point quantum"):
            route._validate_semantic_snapshot(snapshot, "test")
        snapshot["nonrouting_point_quantum_nm"] = 10
        snapshot["board"] = {"forged": True}
        with self.assertRaisesRegex(route.RouteReportError, "board.*fields"):
            route._validate_semantic_snapshot(snapshot, "test")
        snapshot = semantic_snapshot()
        snapshot["schema"] = "kicad-route-semantic-snapshot-v1"
        with self.assertRaisesRegex(route.RouteReportError, "schema"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_boundary_recomputes_digests_and_counts(self):
        snapshot = semantic_snapshot()
        snapshot["nonrouting_items"]["board"]["enabled_layers"].append(1)
        with self.assertRaisesRegex(route.RouteReportError, "digest"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_rejects_self_consistent_nonrouting_forgery(self):
        snapshot = semantic_snapshot()
        snapshot["nonrouting_items"]["footprints"] = [
            {"uuid": "forged-only"}
        ]
        snapshot["nonrouting_category_sha256"] = {
            key: route._json_digest(value)
            for key, value in snapshot["nonrouting_items"].items()
        }
        snapshot["nonrouting_sha256"] = route._json_digest(
            snapshot["nonrouting_items"]
        )
        snapshot["nonrouting_counts"]["footprints"] = 1
        with self.assertRaisesRegex(route.RouteReportError, "unsupported fields"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_boundary_rejects_forged_routing_summary(self):
        snapshot = semantic_snapshot()
        item = segment(locked=True)
        snapshot["routing"]["items"] = [item]
        snapshot["routing"]["locked_items"] = [item]
        with self.assertRaisesRegex(route.RouteReportError, "summary"):
            route._validate_semantic_snapshot(snapshot, "test")

        snapshot = semantic_snapshot()
        snapshot["routing"]["locked_items"] = [segment(locked=True)]
        with self.assertRaisesRegex(route.RouteReportError, "locked_items"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_boundary_preserves_zero_width_for_drc(self):
        snapshot = semantic_snapshot()
        item = segment(width=0)
        snapshot["routing"] = {
            "items": [item],
            "locked_items": [],
            "summary": {
                "count": 1,
                "by_kind": {"segment": 1},
                "by_net": {"N": 1},
                "total_track_length_mm": 0.00001,
                "sha256": route._json_digest([item]),
                "locked_count": 0,
                "locked_sha256": route._json_digest([]),
            },
        }
        uuid = item["uuid"]
        snapshot["identities"] = {
            uuid: {
                "kind": "route",
                "semantic": {
                    key: value for key, value in item.items() if key != "uuid"
                },
            }
        }
        self.assertIs(
            route._validate_semantic_snapshot(snapshot, "test"), snapshot
        )

        snapshot["routing"]["summary"]["locked_count"] = False
        with self.assertRaisesRegex(route.RouteReportError, "locked_count"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_boundary_rejects_forged_netclasses(self):
        snapshot = semantic_snapshot()
        snapshot["netclasses"] = {
            "class_names": ["Default"],
            "net_to_class": {"N": "Invented"},
        }
        with self.assertRaisesRegex(route.RouteReportError, "net_to_class"):
            route._validate_semantic_snapshot(snapshot, "test")

        snapshot = semantic_snapshot()
        snapshot["netclasses"]["forged"] = True
        with self.assertRaisesRegex(route.RouteReportError, "unsupported fields"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_rejects_non_kicad_identity_uuid(self):
        snapshot = semantic_snapshot()
        item = segment()
        snapshot["routing"] = {
            "items": [item],
            "locked_items": [],
            "summary": {
                "count": 1,
                "by_kind": {"segment": 1},
                "by_net": {"N": 1},
                "total_track_length_mm": 0.00001,
                "sha256": route._json_digest([item]),
                "locked_count": 0,
                "locked_sha256": route._json_digest([]),
            },
        }
        snapshot["identities"] = {
            "not-a-uuid": {
                "kind": "route",
                "semantic": {
                    key: value for key, value in item.items() if key != "uuid"
                },
            }
        }
        with self.assertRaisesRegex(route.RouteReportError, "identity UUID"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_semantic_snapshot_rejects_route_uuid_semantic_swap(self):
        snapshot = semantic_snapshot()
        first = segment(
            start=(0, 0), end=(10, 0),
            uuid="11111111-1111-4111-8111-111111111111",
        )
        second = segment(
            start=(0, 20), end=(10, 20),
            uuid="22222222-2222-4222-8222-222222222222",
        )
        items = sorted(
            [first, second],
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":")
            ),
        )
        snapshot["routing"] = {
            "items": items,
            "locked_items": [],
            "summary": {
                "count": 2,
                "by_kind": {"segment": 2},
                "by_net": {"N": 2},
                "total_track_length_mm": 0.00002,
                "sha256": route._json_digest(items),
                "locked_count": 0,
                "locked_sha256": route._json_digest([]),
            },
        }
        snapshot["identities"] = {
            item["uuid"]: {
                "kind": "route",
                "semantic": {
                    key: value for key, value in item.items() if key != "uuid"
                },
            }
            for item in items
        }
        self.assertIs(
            route._validate_semantic_snapshot(snapshot, "test"), snapshot
        )
        first_uuid, second_uuid = sorted(snapshot["identities"])
        snapshot["identities"][first_uuid], snapshot["identities"][second_uuid] = (
            snapshot["identities"][second_uuid],
            snapshot["identities"][first_uuid],
        )
        with self.assertRaisesRegex(route.RouteReportError, "differs"):
            route._validate_semantic_snapshot(snapshot, "test")

    def test_drawing_schema_is_exact_per_kicad_kind(self):
        drawing = {
            "uuid": "33333333-3333-4333-8333-333333333333",
            "kind": "PCB_SHAPE",
            "layers": [44],
            "GetPosition": [0, 0],
            "GetStart": [0, 0],
            "GetEnd": [1_000_000, 0],
            "GetWidth": 100_000,
            "GetShape": 0,
            "IsLocked": False,
            "complete_geometry": {
                "shape_geometry": {
                    "shape": 0,
                    "shape_kind": "segment",
                    "width_nm": 100_000,
                    "stroke_type": "solid",
                    "fill_mode": 0,
                    "hatch_line_width_nm": 0,
                    "hatch_line_spacing_nm": 0,
                    "start_nm": [0, 0],
                    "end_nm": [1_000_000, 0],
                }
            },
        }
        route._validate_drawing(drawing, "test drawing")
        drawing["GetText"] = "impossible"
        with self.assertRaisesRegex(route.RouteReportError, "unsupported fields"):
            route._validate_drawing(drawing, "test drawing")
        drawing.pop("GetText")
        drawing.pop("IsLocked")
        with self.assertRaisesRegex(route.RouteReportError, "unsupported fields"):
            route._validate_drawing(drawing, "test drawing")

    def test_identity_semantics_bind_pad_route_and_zone_uuids(self):
        snapshot = semantic_snapshot()
        pad_uuid = "44444444-4444-4444-8444-444444444444"
        route_uuid = "55555555-5555-4555-8555-555555555555"
        zone_uuid = "66666666-6666-4666-8666-666666666666"
        snapshot["nonrouting_items"]["footprints"] = [{
            "uuid": "77777777-7777-4777-8777-777777777777",
            "reference": "R1",
            "attributes": 0,
            "pads": [{"uuid": pad_uuid, "number": "1"}],
            "graphics": [],
        }]
        snapshot["routing"]["items"] = [
            segment(uuid=route_uuid)
        ]
        snapshot["nonrouting_items"]["zones"] = [{
            "uuid": zone_uuid, "name": "GND",
        }]
        _expected, known = route._identity_semantics(snapshot)
        self.assertEqual(
            {pad_uuid, route_uuid, zone_uuid} - set(known), set()
        )

    def test_pcb_worker_requires_fresh_output(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            board = workspace / "x.kicad_pcb"
            board.write_text("seed\n", encoding="utf-8")
            output = workspace / "snapshot.json"
            output.write_text("{}\n", encoding="utf-8")
            run = {"returncode": 0, "stdout": "", "stderr": ""}
            with mock.patch.object(route, "_run", return_value=run):
                with self.assertRaisesRegex(
                    route.RouteReportError, "did not freshly write"
                ):
                    route._worker_call(
                        Path("python3"), "snapshot", [board], output,
                        workspace, "10.0.5",
                    )

    def test_pcb_worker_envelope_is_bound_to_request_and_input_digest(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            board = workspace / "x.kicad_pcb"
            board.write_text("seed\n", encoding="utf-8")
            output = workspace / "snapshot.json"

            def produce(_command, *, cwd, timeout, env):
                self.assertEqual(cwd, workspace)
                self.assertEqual(timeout, 180)
                output.write_text(json.dumps({
                    "schema": route.PCB_WORKER_SCHEMA,
                    "request_id": env["KICAD_ROUTE_WORKER_REQUEST_ID"],
                    "mode": "snapshot",
                    "pcbnew_version": "10.0.5",
                    "inputs": {"board_sha256": "0" * 64},
                    "outputs": {},
                    "snapshot": semantic_snapshot(),
                }), encoding="utf-8")
                return {"returncode": 0, "stdout": "", "stderr": ""}

            with mock.patch.object(route, "_run", side_effect=produce):
                with self.assertRaisesRegex(
                    route.RouteReportError, "input digests"
                ):
                    route._worker_call(
                        Path("python3"), "snapshot", [board], output,
                        workspace, "10.0.5",
                    )

    def test_ses_import_worker_rejects_failed_zone_refill(self):
        with tempfile.TemporaryDirectory() as raw:
            workspace = Path(raw)
            board_path = workspace / "x.kicad_pcb"
            ses_path = workspace / "x.ses"
            snapshot_path = workspace / "snapshot.json"
            board_path.write_text("seed\n", encoding="utf-8")
            ses_path.write_text("session\n", encoding="utf-8")
            board = mock.Mock()
            board.Zones.return_value = []
            filler = mock.Mock()
            filler.Fill.return_value = False
            pcbnew = mock.Mock()
            pcbnew.ImportSpecctraSES.return_value = True
            pcbnew.SaveBoard.return_value = True
            pcbnew.ZONE_FILLER.return_value = filler
            env = {
                "KICAD_ROUTE_WORKER_ROOT": str(workspace),
                "KICAD_ROUTE_WORKER_REQUEST_ID": "a" * 48,
            }
            with (
                mock.patch.dict(route.os.environ, env, clear=False),
                mock.patch.object(route, "_init_pcbnew", return_value=(object(), pcbnew)),
                mock.patch.object(
                    route, "_load_board_with_project",
                    return_value=(board, None, None),
                ),
            ):
                with self.assertRaisesRegex(route.RouteReportError, "zone refill failed"):
                    route._pcb_worker([
                        "import", str(board_path), str(ses_path),
                        str(snapshot_path),
                    ])
            filler.Fill.assert_called_once_with([])

    def test_identity_envelope_recomputes_digest_count_and_kind_coverage(self):
        snapshot = semantic_snapshot()
        item = segment()
        snapshot["routing"] = {
            "items": [item],
            "locked_items": [],
            "summary": {
                "count": 1,
                "by_kind": {"segment": 1},
                "by_net": {"N": 1},
                "total_track_length_mm": 0.00001,
                "sha256": route._json_digest([item]),
                "locked_count": 0,
                "locked_sha256": route._json_digest([]),
            },
        }
        uuid = "11111111-1111-4111-8111-111111111111"
        snapshot["identities"] = {
            uuid: {
                "kind": "route",
                "semantic": {
                    key: value for key, value in item.items() if key != "uuid"
                },
            }
        }
        identities = route.identity_map_from_snapshot(snapshot)
        value = {
            "schema": route.IDENTITY_WORKER_SCHEMA,
            "request_id": "a" * 48,
            "pcbnew_version": "10.0.5",
            "board_sha256": "b" * 64,
            "identity_map": identities,
            "identity_count": 1,
            "identity_kinds": {"route": 1},
            "identity_sha256": route.canonical_json_sha256(identities),
        }
        self.assertEqual(
            route._validate_identity_envelope(
                value,
                request_id="a" * 48,
                pcbnew_version="10.0.5",
                board_sha256="b" * 64,
                expected_snapshot=snapshot,
                where="test",
            ),
            identities,
        )
        value["identity_count"] = 0
        with self.assertRaisesRegex(route.RouteReportError, "count"):
            route._validate_identity_envelope(
                value,
                request_id="a" * 48,
                pcbnew_version="10.0.5",
                board_sha256="b" * 64,
                expected_snapshot=snapshot,
                where="test",
            )
        forged = {uuid: "route:{\"kind\":\"invented\"}"}
        value.update({
            "identity_map": forged,
            "identity_count": 1,
            "identity_kinds": {"route": 1},
            "identity_sha256": route.canonical_json_sha256(forged),
        })
        with self.assertRaisesRegex(route.RouteReportError, "validated board snapshot"):
            route._validate_identity_envelope(
                value,
                request_id="a" * 48,
                pcbnew_version="10.0.5",
                board_sha256="b" * 64,
                expected_snapshot=snapshot,
                where="test",
            )

    def test_route_apply_summary_is_exact_and_digest_bound(self):
        routes = [{
            "kind": "segment",
            "net": "N",
            "width_nm": 200_000,
            "layer": "F.Cu",
            "start_nm": [0, 0],
            "end_nm": [1_000_000, 0],
        }]
        routes_sha = route.canonical_json_sha256(routes)
        reloaded_snapshot = semantic_snapshot()
        reloaded_item = segment(
            start=(0, 0), end=(1_000_000, 0), width=200_000
        )
        reloaded_item["length_nm"] = 1_000_000
        reloaded_snapshot["routing"]["items"] = [reloaded_item]
        value = {
            "schema": route.ROUTE_APPLY_WORKER_SCHEMA,
            "request_id": "c" * 48,
            "pcbnew_version": "10.0.5",
            "input_board_sha256": "d" * 64,
            "input_routes_sha256": routes_sha,
            "segments": 1,
            "vias": 0,
            "routes_sha256": routes_sha,
            "applied_routes_after_reload_sha256": routes_sha,
            "output_board_sha256": "f" * 64,
        }
        self.assertIs(
            route._validate_route_apply_summary(
                value,
                request_id="c" * 48,
                pcbnew_version="10.0.5",
                input_board_sha256="d" * 64,
                routes=routes,
                output_board_sha256="f" * 64,
                reloaded_snapshot=reloaded_snapshot,
            ),
            value,
        )
        value["input_routes_sha256"] = "0" * 64
        with self.assertRaisesRegex(route.RouteReportError, "requested inputs"):
            route._validate_route_apply_summary(
                value,
                request_id="c" * 48,
                pcbnew_version="10.0.5",
                input_board_sha256="d" * 64,
                routes=routes,
                output_board_sha256="f" * 64,
                reloaded_snapshot=reloaded_snapshot,
            )
        value["input_routes_sha256"] = routes_sha
        value["applied_routes_after_reload_sha256"] = "e" * 64
        with self.assertRaisesRegex(route.RouteReportError, "reload digest"):
            route._validate_route_apply_summary(
                value,
                request_id="c" * 48,
                pcbnew_version="10.0.5",
                input_board_sha256="d" * 64,
                routes=routes,
                output_board_sha256="f" * 64,
                reloaded_snapshot=reloaded_snapshot,
            )

    def test_footprint_graphic_mutation_changes_nonrouting_snapshot(self):
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class LayerSet:
            def Seq(self):
                return [44]

        class Kiid:
            def __init__(self, value):
                self.value = value

            def AsString(self):
                return self.value

        class PCB_SHAPE:
            def __init__(self):
                self.start = Point(1_000_000, 2_000_000)
                self.end = Point(3_000_000, 2_000_000)
                self.m_Uuid = Kiid("graphic-uuid")

            def GetLayerSet(self):
                return LayerSet()

            def GetStart(self):
                return self.start

            def GetEnd(self):
                return self.end

            def GetWidth(self):
                return 50_000

            def GetShape(self):
                return 0

            def GetLineStyle(self):
                return 0

            def GetFillMode(self):
                return 0

            def GetHatchLineWidth(self):
                return 0

            def GetHatchLineSpacing(self):
                return 0

            def IsLocked(self):
                return True

        class Fpid:
            def GetUniStringLibId(self):
                return "Local:Slot"

        class Footprint:
            def __init__(self, graphic):
                self.graphic = graphic
                self.m_Uuid = Kiid("footprint-uuid")

            def Pads(self):
                return []

            def GraphicalItems(self):
                return [self.graphic]

            def GetReference(self):
                return "MH1"

            def GetFPID(self):
                return Fpid()

            def GetPosition(self):
                return Point(10_000_000, 20_000_000)

            def GetOrientationDegrees(self):
                return 90.0

            def IsFlipped(self):
                return False

            def IsLocked(self):
                return True

            def GetAttributes(self):
                return 2

        graphic = PCB_SHAPE()
        footprint = Footprint(graphic)
        before = route._footprint_item(
            footprint, {"graphic-uuid": {"stroke_type": "solid"}}, set()
        )
        self.assertEqual(before["graphics"][0]["uuid"], "graphic-uuid")
        self.assertEqual(before["attributes"], 2)

        graphic.end.x += 500_000
        after = route._footprint_item(
            footprint, {"graphic-uuid": {"stroke_type": "solid"}}, set()
        )
        self.assertNotEqual(
            route._json_digest(before), route._json_digest(after)
        )

    def test_identity_map_includes_footprint_hosted_graphic(self):
        class Point:
            x = 1_000_000
            y = 2_000_000

        class Kiid:
            def __init__(self, value):
                self.value = value

            def AsString(self):
                return self.value

        class PCB_SHAPE:
            m_Uuid = Kiid("33333333-3333-4333-8333-333333333333")

            def GetLayerSet(self):
                return LayerSet()

            def GetLayer(self):
                return 44

            def GetPosition(self):
                return Point()

            def GetStart(self):
                return Point()

            def GetEnd(self):
                return Point()

            def GetShape(self):
                return 0

            def GetLineStyle(self):
                return 0

            def GetFillMode(self):
                return 0

            def GetHatchLineWidth(self):
                return 0

            def GetHatchLineSpacing(self):
                return 0

            def GetWidth(self):
                return 50_000

            def IsLocked(self):
                return True

        class Footprint:
            m_Uuid = Kiid("44444444-4444-4444-8444-444444444444")

            def GetReference(self):
                return "MH1"

            def Pads(self):
                return []

            def GraphicalItems(self):
                return [PCB_SHAPE()]

            def GetAttributes(self):
                return 2

            def GetFPID(self):
                return Fpid()

            def GetPosition(self):
                return Point()

            def GetOrientationDegrees(self):
                return 0.0

            def IsFlipped(self):
                return False

            def IsLocked(self):
                return True

        class LayerSet:
            def Seq(self):
                return [44]

        class EmptyLayerSet:
            def Seq(self):
                return []

        class Fpid:
            def GetUniStringLibId(self):
                return "Local:Slot"

        class NetInfo:
            def NetsByName(self):
                return {}

        class Board:
            def GetFootprints(self):
                return [Footprint()]

            def GetTracks(self):
                return []

            def Zones(self):
                return []

            def GetDrawings(self):
                return []

            def GetAllNetClasses(self):
                return {}

            def GetNetInfo(self):
                return NetInfo()

            def GetCopperLayerCount(self):
                return 0

            def IsLayerEnabled(self, _layer):
                return False

            def GetEnabledLayers(self):
                return EmptyLayerSet()

        pcbnew = mock.Mock()
        pcbnew.IsCopperLayer.return_value = False
        graphic_uuid = "33333333-3333-4333-8333-333333333333"
        identities = manifest.identity_map(
            Board(), pcbnew,
            persisted_graphics={graphic_uuid: {"stroke_type": "solid"}},
        )
        self.assertIn(graphic_uuid, identities)
        self.assertIn("footprint-graphic:", identities[graphic_uuid])
        self.assertIn('"parent_reference":"MH1"', identities[graphic_uuid])

    def test_identity_map_rejects_duplicate_board_uuids(self):
        class Kiid:
            def AsString(self):
                return "55555555-5555-4555-8555-555555555555"

        class Item:
            m_Uuid = Kiid()

        identities = {}
        route._record_identity(identities, Item(), "drawing", {"value": 1})
        with self.assertRaisesRegex(route.RouteReportError, "occurs more than once"):
            route._record_identity(
                identities, Item(), "drawing", {"value": 2}
            )

    def test_bezier_control_mutation_changes_complete_geometry(self):
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class Bezier:
            def __init__(self):
                self.c1 = Point(1_000_000, 2_000_000)
                self.c2 = Point(3_000_000, 2_000_000)

            def GetShape(self):
                return 5

            def GetWidth(self):
                return 50_000

            def GetFillMode(self):
                return 0

            def GetHatchLineWidth(self):
                return 0

            def GetHatchLineSpacing(self):
                return 0

            def GetLayer(self):
                return 31

            def IsLocked(self):
                return False

            def GetLineStyle(self):
                return 0

            def GetStart(self):
                return Point(0, 0)

            def GetEnd(self):
                return Point(4_000_000, 0)

            def GetBezierC1(self):
                return self.c1

            def GetBezierC2(self):
                return self.c2

        curve = Bezier()
        convert = lambda p: [p.x, p.y]
        persisted = {"stroke_type": "solid"}
        before = graphics.complete_graphic_geometry(
            curve, convert, persistence=persisted
        )
        curve.c1.y += 500_000
        after = graphics.complete_graphic_geometry(
            curve, convert, persistence=persisted
        )
        self.assertNotEqual(before, after)
        before_identity = "drawing:" + json.dumps(
            before, sort_keys=True, separators=(",", ":")
        )
        after_identity = "drawing:" + json.dumps(
            after, sort_keys=True, separators=(",", ":")
        )
        self.assertNotEqual(before_identity, after_identity)
        self.assertEqual(
            after["shape_geometry"]["control1_nm"], [1_000_000, 2_500_000]
        )

    def test_polygon_hole_and_text_layout_are_complete_geometry(self):
        class Point:
            def __init__(self, x, y):
                self.x = x
                self.y = y

        class Chain:
            def __init__(self, points):
                self.points = points

            def PointCount(self):
                return len(self.points)

            def CPoint(self, index):
                return self.points[index]

            def IsClosed(self):
                return True

        class PolySet:
            def __init__(self):
                self.outline = Chain([
                    Point(0, 0), Point(10, 0), Point(10, 10), Point(0, 10)
                ])
                self.hole = Chain([
                    Point(2, 2), Point(4, 2), Point(4, 4), Point(2, 4)
                ])

            def OutlineCount(self):
                return 1

            def COutline(self, _index):
                return self.outline

            def HoleCount(self, _index):
                return 1

            def CHole(self, _outline_index, _hole_index):
                return self.hole

        class Polygon:
            def __init__(self):
                self.poly = PolySet()

            def GetShape(self):
                return 4

            def GetWidth(self):
                return 50_000

            def GetFillMode(self):
                return 1

            def GetHatchLineWidth(self):
                return 0

            def GetHatchLineSpacing(self):
                return 0

            def GetLineStyle(self):
                return 0

            def GetPolyShape(self):
                return self.poly

        class Text:
            def GetText(self):
                return "probe"

            def GetPosition(self):
                return Point(1, 2)

            def GetTextSize(self):
                return Point(3, 4)

            def GetTextThickness(self):
                return 5

            def GetTextAngleDegrees(self):
                return 90.0

            def GetHorizJustify(self):
                return -1

            def GetVertJustify(self):
                return 1

            def IsMirrored(self):
                return True

            def IsBold(self):
                return True

            def IsItalic(self):
                return False

            def IsKnockout(self):
                return True

            def GetFontName(self):
                return "KiCad Font"

            def GetTextStyleName(self):
                return "default"

            def GetLineSpacing(self):
                return 1.25

            def IsKeepUpright(self):
                return False

        convert = lambda point: [point.x, point.y]
        polygon = Polygon()
        persisted = {"stroke_type": "dash"}
        before = graphics.complete_graphic_geometry(
            polygon, convert, persistence=persisted
        )
        polygon.poly.hole.points[1].x = 5
        after = graphics.complete_graphic_geometry(
            polygon, convert, persistence=persisted
        )
        self.assertNotEqual(before, after)
        text = graphics.complete_graphic_geometry(Text(), convert)["text_geometry"]
        self.assertEqual(text["horizontal_justify"], -1)
        self.assertEqual(text["font_name"], "KiCad Font")
        self.assertTrue(text["mirrored"])
        self.assertTrue(text["knockout"])
        self.assertEqual(text["line_spacing"], 1.25)
        self.assertFalse(text["keep_upright"])

    def test_saved_graphic_stroke_type_is_bound_by_uuid(self):
        template = """\
(kicad_pcb
  (gr_line (start 0 0) (end 1 1)
    (stroke (width 0.1) (type {direct_style}))
    (layer \"Edge.Cuts\") (uuid direct-uuid))
  (footprint \"Test:Part\"
    (layer \"F.Cu\") (at 0 0)
    (fp_curve (pts (xy 0 0) (xy 1 1) (xy 2 1) (xy 3 0))
      (stroke (width 0.1) (type {footprint_style}))
      (fill none) (layer \"Edge.Cuts\") (uuid footprint-uuid))))
"""
        with tempfile.TemporaryDirectory() as raw:
            board = Path(raw) / "probe.kicad_pcb"
            board.write_text(
                template.format(
                    direct_style="default", footprint_style="solid"
                ),
                encoding="utf-8",
            )
            before = graphics.graphic_persistence(board)
            self.assertEqual(before, {
                "direct-uuid": {"stroke_type": "default"},
                "footprint-uuid": {"stroke_type": "solid"},
            })
            board.write_text(
                template.format(
                    direct_style="dash", footprint_style="solid"
                ),
                encoding="utf-8",
            )
            after = graphics.graphic_persistence(board)
            self.assertEqual(after["direct-uuid"]["stroke_type"], "dash")
            self.assertNotEqual(before, after)

    def test_saved_graphic_stroke_parser_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            board = Path(raw) / "probe.kicad_pcb"
            board.write_text(
                "(kicad_pcb (gr_line (stroke (type solid)) "
                "(uuid duplicate)) (gr_arc (stroke (type dash)) "
                "(uuid duplicate)))\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(graphics.GraphicError, "duplicate"):
                graphics.graphic_persistence(board)
            board.write_text(
                "(kicad_pcb (gr_line (uuid missing-stroke)))\n",
                encoding="utf-8",
            )
            with self.assertRaisesRegex(graphics.GraphicError, "stroke"):
                graphics.graphic_persistence(board)

    def test_unknown_graphic_shape_fails_closed(self):
        class Unknown:
            def GetShape(self):
                return 99

        with self.assertRaisesRegex(graphics.GraphicError, "unsupported"):
            graphics.complete_graphic_geometry(
                Unknown(), lambda point: point,
                persistence={"stroke_type": "solid"},
            )

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
