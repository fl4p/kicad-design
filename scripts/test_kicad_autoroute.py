#!/usr/bin/env python3

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_autoroute as autoroute


def config_dict():
    return {
        "schema": autoroute.CONFIG_SCHEMA,
        "backend": autoroute.BACKEND_ID,
        "inputs": ["netlist.net"],
        "scope": {
            "net_classes": ["AutorouteRoutine"],
            "layers": ["F.Cu", "B.Cu"],
            "styles": {
                "AutorouteRoutine": {
                    "track_width_nm": 250_000,
                    "clearance_nm": 200_000,
                    "via_diameter_nm": 600_000,
                    "via_drill_nm": 300_000,
                }
            },
        },
        "limits": {
            "max_passes": 20,
            "max_threads": 4,
            "timeout_seconds": 1200,
            "audit_timeout_seconds": 300,
        },
        "seed": {
            "drc_baseline": "autoroute-seed-drc.json",
            "audit_commands": [
                {
                    "interpreter": "kicad_python",
                    "argv": ["audit.py", "{board}"],
                }
            ],
        },
        "final": {"audit_commands": []},
        "promotion": {"manifest": "routes.json"},
    }


def segment(start=(0, 0), end=(1_000_000, 0), net="N"):
    return {
        "kind": "segment",
        "net": net,
        "layer": "F.Cu",
        "width_nm": 250_000,
        "start_nm": list(start),
        "end_nm": list(end),
    }


class AutorouteContractsTests(unittest.TestCase):
    def write_json(self, directory: Path, name: str, value) -> Path:
        path = directory / name
        path.write_text(json.dumps(value), encoding="utf-8")
        return path

    def test_config_is_strict_and_audits_are_shell_free(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path = self.write_json(root, "autoroute.json", config_dict())
            got = autoroute.load_config(path)
            self.assertEqual(got["scope"]["net_classes"], ["AutorouteRoutine"])
            self.assertEqual(
                got["seed"]["audit_commands"][0]["timeout_seconds"], 300
            )
            broken = config_dict()
            broken["surprise"] = True
            self.write_json(root, "broken.json", broken)
            with self.assertRaisesRegex(autoroute.AutorouteError, "unknown key"):
                autoroute.load_config(root / "broken.json")

    def test_config_rejects_unknown_audit_substitution(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            value = config_dict()
            value["seed"]["audit_commands"][0]["argv"] = [
                "audit.py",
                "{source_board}",
            ]
            path = self.write_json(root, "autoroute.json", value)
            with self.assertRaisesRegex(autoroute.AutorouteError, "unsupported substitutions"):
                autoroute.load_config(path)

    def test_project_class_is_authoritative_and_style_must_match(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            project = {
                "net_settings": {
                    "classes": [
                        {
                            "name": "Default",
                            "track_width": 0.2,
                            "clearance": 0.2,
                            "via_diameter": 0.6,
                            "via_drill": 0.3,
                        },
                        {
                            "name": "AutorouteRoutine",
                            "track_width": 0.25,
                            "clearance": 0.2,
                            "via_diameter": 0.6,
                            "via_drill": 0.3,
                        },
                    ],
                    "netclass_assignments": {
                        "/A": ["AutorouteRoutine"],
                        "/B": ["AutorouteRoutine"],
                    },
                    "netclass_patterns": [],
                }
            }
            project_path = self.write_json(root, "x.kicad_pro", project)
            config_path = self.write_json(root, "autoroute.json", config_dict())
            config = autoroute.load_config(config_path)
            scope = autoroute.resolve_project_netclasses(
                project_path, config["scope"]["net_classes"]
            )
            self.assertEqual(scope["nets_by_class"]["AutorouteRoutine"], ["/A", "/B"])
            autoroute.verify_project_styles(config, scope)
            scope["classes"]["AutorouteRoutine"]["track_width"] = 0.2
            with self.assertRaisesRegex(autoroute.AutorouteError, "config requires"):
                autoroute.verify_project_styles(config, scope)

    def test_routes_are_undirected_sorted_and_hashed(self):
        a = segment(start=(10, 0), end=(0, 0), net="B")
        b = segment(start=(0, 0), end=(0, 10), net="A")
        routes = autoroute.canonical_routes([a, b])
        self.assertEqual(routes[0]["net"], "A")
        self.assertEqual(routes[1]["start_nm"], [0, 0])
        self.assertEqual(len(autoroute.canonical_json_sha256(routes)), 64)
        custom_inner = segment()
        custom_inner["layer"] = "PWR"
        self.assertEqual(autoroute.canonical_route(custom_inner)["layer"], "PWR")

    def test_routes_reject_duplicate_zero_and_overlap(self):
        with self.assertRaisesRegex(autoroute.AutorouteError, "duplicate"):
            autoroute.canonical_routes([segment(), segment()])
        with self.assertRaisesRegex(autoroute.AutorouteError, "zero-length"):
            autoroute.canonical_routes([segment(end=(0, 0))])
        with self.assertRaisesRegex(autoroute.AutorouteError, "collinear"):
            autoroute.canonical_routes(
                [segment(start=(0, 0), end=(10, 0)), segment(start=(5, 0), end=(15, 0))]
            )

    def test_candidate_filter_discards_excluded_net_but_rejects_wrong_style(self):
        config = config_dict()
        config["config_dir"] = "/tmp"
        scope = {"net_to_class": {"N": "AutorouteRoutine"}}
        raw = {
            **segment(net="N"),
            "locked": False,
            "length_nm": 1_000_000,
        }
        excluded = {**raw, "net": "X"}
        result = autoroute.filter_candidate_routes([raw, excluded], config, scope)
        self.assertEqual(len(result["routes"]), 1)
        self.assertEqual(result["discarded_drift"][0]["reason"], "excluded_net")
        raw["width_nm"] = 150_000
        with self.assertRaisesRegex(autoroute.AutorouteError, "expected 250000"):
            autoroute.filter_candidate_routes([raw], config, scope)

    def test_candidate_filter_unions_redundant_collinear_router_copper(self):
        config = config_dict()
        scope = {"net_to_class": {"N": "AutorouteRoutine"}}
        short = {
            **segment(start=(0, 0), end=(500_000, 0)),
            "locked": False, "length_nm": 500_000,
        }
        long = {
            **segment(start=(0, 0), end=(1_000_000, 0)),
            "locked": False, "length_nm": 1_000_000,
        }
        result = autoroute.filter_candidate_routes([short, long], config, scope)
        self.assertEqual(result["routes"], [segment()])

    def test_v2_seed_attestation_binds_routes_nonrouting_and_context(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            board = root / "x.kicad_pcb"
            board.write_text(
                '(kicad_pcb (version 20260206) (generator pcbnew) '
                '(general (thickness 1.6)) '
                '(segment (start 0 0) (end 1 0) (width 0.25) (layer "F.Cu") (net 1)))\n',
                encoding="utf-8",
            )
            (root / "x.kicad_pro").write_text('{"unicode":"µ"}\n', encoding="utf-8")
            snapshot = {
                "board": {
                    "copper_layer_count": 2,
                    "copper_layers": ["F.Cu", "B.Cu"],
                    "enabled_layers": [0, 31],
                },
                "netclasses": {"net_to_class": {"Nµ": "Routine"}},
                "routing": {"items": [{
                    "kind": "segment", "net": "Nµ", "locked": True,
                    "width_nm": 250_000, "layer": "F.Cu",
                    "start_nm": [0, 0], "end_nm": [1_000_000, 0],
                    "length_nm": 1_000_000,
                }]},
            }
            config = {
                "schema": autoroute.CONFIG_SCHEMA_V2,
                "config_sha256": "1" * 64,
                "tools": {"adapter": {
                    "path": "autoroute_adapter.py",
                    "protocol": "kicad-autoroute-adapter-v1",
                    "sha256": "2" * 64,
                }},
                "project": {"mode": "board-snapshot"},
                "reset": {"policy": "none", "manifest": None, "manifest_sha256": None},
            }
            bundle = [{
                "role": "autoroute-config", "path": "autoroute.json",
                "sha256": "1" * 64,
            }]
            attestation = autoroute.make_seed_attestation(snapshot, board, config, bundle)
            autoroute.validate_seed_attestation(attestation)
            self.assertEqual(
                attestation["semantic"]["context_bundle"][0]["path"],
                "x.kicad_pro",
            )
            tampered = json.loads(json.dumps(attestation))
            tampered["semantic"]["route_state_count"] = 2
            with self.assertRaisesRegex(autoroute.AutorouteError, "inconsistent"):
                autoroute.validate_seed_attestation(tampered)

    def test_kicad_10_through_via_enum_is_promotable(self):
        config = config_dict()
        config["config_dir"] = "/tmp"
        scope = {"net_to_class": {"N": "AutorouteRoutine"}}
        raw = {
            "kind": "via",
            "net": "N",
            "position_nm": [1_000_000, 2_000_000],
            "top_layer": "F.Cu",
            "bottom_layer": "B.Cu",
            "width_nm": 600_000,
            "drill_nm": 300_000,
            "via_type": 4,
        }
        result = autoroute.filter_candidate_routes([raw], config, scope)
        self.assertEqual(result["routes"][0]["kind"], "via")

    def test_input_bundle_rejects_external_and_detects_drift(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as other:
            root = Path(raw)
            a = root / "a.txt"
            a.write_text("a", encoding="utf-8")
            bundle = autoroute.build_input_bundle(root, {"config": a})
            autoroute.verify_input_bundle(root, bundle)
            a.write_text("changed", encoding="utf-8")
            with self.assertRaisesRegex(autoroute.AutorouteError, "mismatch"):
                autoroute.verify_input_bundle(root, bundle)
            outside = Path(other) / "b.txt"
            outside.write_text("b", encoding="utf-8")
            with self.assertRaisesRegex(autoroute.AutorouteError, "outside hermetic root"):
                autoroute.build_input_bundle(root, {"external": outside})

    def test_input_bundle_rejects_symlink_files(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            target = root / "real.txt"
            target.write_text("real", encoding="utf-8")
            alias = root / "alias.txt"
            alias.symlink_to(target.name)
            with self.assertRaisesRegex(autoroute.AutorouteError, "symlink"):
                autoroute.build_input_bundle(root, {"alias": alias})
            bundle = [
                {
                    "role": "alias",
                    "path": "alias.txt",
                    "sha256": autoroute.sha256_path(target),
                }
            ]
            with self.assertRaisesRegex(autoroute.AutorouteError, "symlink"):
                autoroute.verify_input_bundle(root, bundle)

    def test_input_bundle_rejects_nested_symlink_directories(self):
        with tempfile.TemporaryDirectory() as raw, tempfile.TemporaryDirectory() as other:
            root = Path(raw)
            library = root / "library"
            library.mkdir()
            external = Path(other) / "external"
            external.mkdir()
            (external / "part.kicad_mod").write_text("(footprint X)\n", encoding="utf-8")
            (library / "escape").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(autoroute.AutorouteError, "symlink"):
                autoroute.build_input_bundle(root, {"library": library})

    def test_drc_identity_includes_position_and_multiplicity(self):
        report = {
            "$schema": "https://schemas.kicad.org/drc.v1.json",
            "coordinate_units": "mm",
            "ignored_checks": [{"key": "known", "description": "Known"}],
            "included_severities": ["warning", "error"],
            "kicad_version": "10.0.5",
            "schematic_parity": [],
            "unconnected_items": [],
            "violations": [
                {
                    "type": "clearance",
                    "severity": "error",
                    "items": [
                        {"uuid": "u1", "pos": {"x": 1.25, "y": 2.5}},
                        {"uuid": "u2", "pos": {"x": 3, "y": 4}},
                    ],
                },
                {
                    "type": "clearance",
                    "severity": "error",
                    "items": [
                        {"uuid": "u1", "pos": {"x": 1.25, "y": 2.5}},
                        {"uuid": "u2", "pos": {"x": 3, "y": 4}},
                    ],
                },
            ],
        }
        normalized = autoroute.normalize_drc_report(report, {"u1": "pad:A.1", "u2": "track:T"})
        self.assertEqual(normalized["findings"][0]["count"], 2)
        self.assertEqual(
            normalized["findings"][0]["key"]["items"][0]["position_nm"],
            [1_250_000, 2_500_000],
        )
        baseline = autoroute.make_drc_baseline(normalized)
        self.assertEqual(autoroute.compare_drc(normalized, baseline, final=False), [])
        self.assertTrue(autoroute.compare_drc(normalized, baseline, final=True))
        baseline["findings"][0]["disposition"] = "may_persist"
        self.assertEqual(autoroute.compare_drc(normalized, baseline, final=True), [])

    def test_manifest_rejects_noncanonical_route_or_bad_digest(self):
        routes = autoroute.canonical_routes([segment(net="N")])
        manifest = {
            "schema": autoroute.MANIFEST_SCHEMA,
            "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
            "seed_sha256": "7" * 64,
            "applicator": {
                "schema_version": autoroute.ROUTE_APPLICATOR_VERSION,
                "bundle_path": "autoroute_apply.py",
                "source_sha256": "0" * 64,
            },
            "input_bundle": [
                {
                    "role": "project-code:autoroute_apply.py",
                    "path": "autoroute_apply.py",
                    "sha256": "0" * 64,
                }
            ],
            "toolchain": {
                "backend": autoroute.BACKEND_ID,
                "freerouting_version": "2.3.0",
                "freerouting_sha256": "1" * 64,
                "java_version": "25.0.4+7",
                "install_receipt_sha256": "2" * 64,
                "compatibility_matrix_sha256": "3" * 64,
                "compatibility_cell": {
                    "os": "darwin",
                    "arch": "arm64",
                    "kicad_cli": "10.0.5",
                    "pcbnew": "10.0.5",
                    "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
                },
            },
            "scope": {
                "net_classes": ["AutorouteRoutine"],
                "resolved_nets": ["N"],
                "net_to_class": {"N": "AutorouteRoutine"},
                "layers": ["F.Cu", "B.Cu"],
                "styles": config_dict()["scope"]["styles"],
            },
            "candidate": {
                "raw_sha256": "4" * 64,
                "review_sha256": "5" * 64,
                "report_sha256": "6" * 64,
            },
            "routes": routes,
            "routes_sha256": autoroute.canonical_json_sha256(routes),
        }
        autoroute.validate_manifest(manifest)
        manifest["snapshot_schema"] = "kicad-route-semantic-snapshot-v1"
        with self.assertRaisesRegex(autoroute.AutorouteError, "snapshot_schema"):
            autoroute.validate_manifest(manifest)
        manifest["snapshot_schema"] = autoroute.SNAPSHOT_SCHEMA
        manifest["seed_sha256"] = "not-a-digest"
        with self.assertRaisesRegex(autoroute.AutorouteError, "seed_sha256"):
            autoroute.validate_manifest(manifest)
        manifest["seed_sha256"] = "7" * 64
        manifest["routes_sha256"] = "f" * 64
        with self.assertRaisesRegex(autoroute.AutorouteError, "does not match"):
            autoroute.validate_manifest(manifest)

    def test_manifest_rejects_wrong_style_and_incomplete_mapping(self):
        routes = autoroute.canonical_routes([segment(net="N")])
        scope = {
            "net_classes": ["AutorouteRoutine"],
            "resolved_nets": ["N"],
            "net_to_class": {"N": "AutorouteRoutine"},
            "layers": ["F.Cu", "B.Cu"],
            "styles": config_dict()["scope"]["styles"],
        }
        manifest = {
            "schema": autoroute.MANIFEST_SCHEMA,
            "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
            "seed_sha256": "7" * 64,
            "applicator": {"schema_version": autoroute.ROUTE_APPLICATOR_VERSION, "bundle_path": "autoroute_apply.py", "source_sha256": "0" * 64},
            "input_bundle": [{"role": "project-code:autoroute_apply.py", "path": "autoroute_apply.py", "sha256": "0" * 64}],
            "toolchain": {
                "backend": autoroute.BACKEND_ID,
                "freerouting_version": "2.3.0",
                "freerouting_sha256": "1" * 64,
                "java_version": "25.0.4+7",
                "install_receipt_sha256": "2" * 64,
                "compatibility_matrix_sha256": "3" * 64,
                "compatibility_cell": {"os": "darwin", "arch": "arm64", "kicad_cli": "10.0.5", "pcbnew": "10.0.5", "snapshot_schema": autoroute.SNAPSHOT_SCHEMA},
            },
            "scope": scope,
            "candidate": {"raw_sha256": "4" * 64, "review_sha256": "5" * 64, "report_sha256": "6" * 64},
            "routes": routes,
            "routes_sha256": autoroute.canonical_json_sha256(routes),
        }
        manifest["routes"][0]["width_nm"] = 150_000
        manifest["routes_sha256"] = autoroute.canonical_json_sha256(manifest["routes"])
        with self.assertRaisesRegex(autoroute.AutorouteError, "expected 250000"):
            autoroute.validate_manifest(manifest)
        good_routes = autoroute.canonical_routes([segment(net="N")])
        manifest["routes"] = good_routes
        manifest["routes_sha256"] = autoroute.canonical_json_sha256(good_routes)
        manifest["scope"]["net_to_class"] = {}
        with self.assertRaisesRegex(autoroute.AutorouteError, "map every resolved net"):
            autoroute.validate_manifest(manifest)

    def test_promotion_report_requires_exact_checks_and_binds_filtered_routes(self):
        routes = autoroute.canonical_routes([segment(net="N")])
        bundle = [{"role": "project-code:autoroute_apply.py", "path": "autoroute_apply.py", "sha256": "0" * 64}]
        scope = {
            "net_classes": ["AutorouteRoutine"],
            "resolved_nets": ["N"],
            "net_to_class": {"N": "AutorouteRoutine"},
            "layers": ["F.Cu", "B.Cu"],
            "styles": config_dict()["scope"]["styles"],
        }
        promotion = {
            "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
            "seed_sha256": "7" * 64,
            "config_sha256": "8" * 64,
            "input_bundle": bundle,
            "input_bundle_sha256": autoroute.canonical_json_sha256(bundle),
            "applicator": {"schema_version": autoroute.ROUTE_APPLICATOR_VERSION, "bundle_path": "autoroute_apply.py", "source_sha256": "0" * 64},
            "toolchain": {
                "backend": autoroute.BACKEND_ID,
                "freerouting_version": "2.3.0",
                "freerouting_sha256": "1" * 64,
                "java_version": "25.0.4+7",
                "install_receipt_sha256": "2" * 64,
                "compatibility_matrix_sha256": "3" * 64,
                "compatibility_cell": {"os": "darwin", "arch": "arm64", "kicad_cli": "10.0.5", "pcbnew": "10.0.5", "snapshot_schema": autoroute.SNAPSHOT_SCHEMA},
            },
            "scope": scope,
            "raw_candidate_sha256": "4" * 64,
            "review_candidate_sha256": "5" * 64,
            "routes": routes,
            "routes_sha256": autoroute.canonical_json_sha256(routes),
            "checks": {key: True for key in autoroute.PROMOTION_CHECKS},
            "blocks": [],
        }
        report = {
            "schema": autoroute.REPORT_SCHEMA,
            "mode": "route-and-report",
            "created_utc": "2026-08-18T00:00:00Z",
            "source": {}, "tools": {}, "limitations": [],
            "configuration": {"sha256": "8" * 64},
            "workspace": "/tmp/work", "scratch_copies": {},
            "seed": {"semantic": {"snapshot_schema": autoroute.SNAPSHOT_SCHEMA}},
            "router_settings": {}, "router_run": {},
            "candidate": {"board_sha256": "5" * 64, "semantic": {"snapshot_schema": autoroute.SNAPSHOT_SCHEMA}, "filtered": {"routes": routes, "routes_sha256": autoroute.canonical_json_sha256(routes)}},
            "scope": {}, "findings": [], "promotion": promotion,
            "verdict": "PROMOTABLE_CANDIDATE", "verdict_reason": "all checks passed",
        }
        autoroute.validate_promotion_report(report)
        report["promotion"]["snapshot_schema"] = "kicad-route-semantic-snapshot-v1"
        with self.assertRaisesRegex(autoroute.AutorouteError, "snapshot schema"):
            autoroute.validate_promotion_report(report)
        report["promotion"]["snapshot_schema"] = autoroute.SNAPSHOT_SCHEMA
        report["mode"] = "exploratory-report"
        with self.assertRaisesRegex(autoroute.AutorouteError, "not a promotable"):
            autoroute.validate_promotion_report(report)
        report["mode"] = "route-and-report"
        report["promotion"]["checks"] = {"made_up": True}
        with self.assertRaisesRegex(autoroute.AutorouteError, "exact required set"):
            autoroute.validate_promotion_report(report)

    def test_v2_routine_promotion_cannot_fabricate_project_audit_passes(self):
        routes = autoroute.canonical_routes([segment(net="N")])
        bundle = [
            {"role": "tool:adapter", "path": "autoroute_adapter.py", "sha256": "a" * 64},
            {"role": "tool:applicator", "path": "autoroute_apply.py", "sha256": "0" * 64},
        ]
        evidence = {
            "config_sha256": "8" * 64,
            "input_bundle_sha256": autoroute.canonical_json_sha256(bundle),
            "adapter": {
                "path": "autoroute_adapter.py",
                "protocol": "kicad-autoroute-adapter-v1",
                "sha256": "a" * 64,
            },
            "project_mode": "board-snapshot",
            "reset": {"policy": "none", "manifest": None, "manifest_sha256": None},
        }
        semantic = {
            "board": {}, "net_to_class": {"N": "AutorouteRoutine"},
            "route_state_count": 0,
            "route_states_sha256": autoroute.canonical_json_sha256([]),
            "nonrouting_projection_sha256": "b" * 64,
            "context_bundle": [],
            "context_bundle_sha256": autoroute.canonical_json_sha256([]),
        }
        attestation = {
            "schema": autoroute.SEED_ATTESTATION_SCHEMA,
            "semantic": semantic, "evidence": evidence,
        }
        attestation["sha256"] = autoroute.canonical_json_sha256(attestation)
        scope = {
            "net_classes": ["AutorouteRoutine"], "resolved_nets": ["N"],
            "net_to_class": {"N": "AutorouteRoutine"},
            "layers": ["F.Cu", "B.Cu"],
            "styles": config_dict()["scope"]["styles"],
        }
        promotion = {
            "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
            "seed_sha256": "7" * 64, "config_sha256": "8" * 64,
            "input_bundle": bundle,
            "input_bundle_sha256": autoroute.canonical_json_sha256(bundle),
            "applicator": {
                "schema_version": autoroute.ROUTE_APPLICATOR_VERSION, "bundle_path": "autoroute_apply.py",
                "source_sha256": "0" * 64,
            },
            "toolchain": {
                "backend": autoroute.BACKEND_ID,
                "freerouting_version": "2.3.0", "freerouting_sha256": "1" * 64,
                "java_version": "25.0.4+7", "install_receipt_sha256": "2" * 64,
                "compatibility_matrix_sha256": "3" * 64,
                "compatibility_cell": {
                    "os": "darwin", "arch": "arm64",
                    "kicad_cli": "10.0.5", "pcbnew": "10.0.5",
                    "snapshot_schema": autoroute.SNAPSHOT_SCHEMA,
                },
            },
            "scope": scope, "raw_candidate_sha256": "4" * 64,
            "review_candidate_sha256": "5" * 64, "routes": routes,
            "routes_sha256": autoroute.canonical_json_sha256(routes),
            "checks": {key: True for key in autoroute.PROMOTION_CHECKS_V2_ROUTINE},
            "blocks": [], "seed_attestation": attestation,
            "selected_scope_policy": "routine",
        }
        report = {
            "schema": autoroute.REPORT_SCHEMA, "mode": "route-and-report",
            "created_utc": "2026-08-18T00:00:00Z", "source": {}, "tools": {},
            "limitations": [],
            "configuration": {"schema": autoroute.CONFIG_SCHEMA_V2, "sha256": "8" * 64},
            "workspace": "/tmp/work", "scratch_copies": {},
            "seed": {"semantic": {"snapshot_schema": autoroute.SNAPSHOT_SCHEMA}},
            "router_settings": {}, "router_run": {},
            "candidate": {
                "board_sha256": "5" * 64,
                "semantic": {"snapshot_schema": autoroute.SNAPSHOT_SCHEMA},
                "filtered": {"routes": routes, "routes_sha256": autoroute.canonical_json_sha256(routes)},
            },
            "scope": {}, "findings": [], "promotion": promotion,
            "verdict": "PROMOTABLE_CANDIDATE", "verdict_reason": "all checks passed",
        }
        autoroute.validate_promotion_report(report)
        report["promotion"]["checks"] = {key: True for key in autoroute.PROMOTION_CHECKS}
        with self.assertRaisesRegex(autoroute.AutorouteError, "exact required set"):
            autoroute.validate_promotion_report(report)

    def test_nonrouting_projection_drops_routes_and_fill_but_not_zone_rules(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "a.kicad_pcb"
            second = root / "b.kicad_pcb"
            first.write_text(
                '(kicad_pcb (version 1) (zone (net 1) (thermal_gap 0.3) '
                '(filled_polygon (layer "F.Cu") (pts (xy 1 2)))) '
                '(segment (start 0 0) (end 1 0) (width 0.2)))\n',
                encoding="utf-8",
            )
            second.write_text(
                '(kicad_pcb (version 1) (zone (net 1) (thermal_gap 0.3) '
                '(filled_polygon (layer "F.Cu") (pts (xy 9 9)))) '
                '(via (at 1 1) (size 0.6) (drill 0.3)))\n',
                encoding="utf-8",
            )
            self.assertEqual(
                autoroute.nonrouting_projection_sha256(first),
                autoroute.nonrouting_projection_sha256(second),
            )
            second.write_text(
                second.read_text(encoding="utf-8").replace("thermal_gap 0.3", "thermal_gap 0.4"),
                encoding="utf-8",
            )
            self.assertNotEqual(
                autoroute.nonrouting_projection_sha256(first),
                autoroute.nonrouting_projection_sha256(second),
            )


if __name__ == "__main__":
    unittest.main()
