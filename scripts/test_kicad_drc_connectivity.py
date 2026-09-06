import contextlib
import io
import json
import pathlib
import tempfile
import unittest

import kicad_drc_connectivity as split


def item(description):
    return {"description": description, "uuid": "u", "pos": {"x": 1, "y": 2}}


def record(*descriptions):
    return {
        "description": "Missing connection between items",
        "items": [item(description) for description in descriptions],
        "severity": "error",
        "type": "unconnected_items",
    }


def drc_report(records):
    return {
        "$schema": split.KICAD_DRC_SCHEMA,
        "coordinate_units": "mm",
        "ignored_checks": [],
        "included_severities": ["error", "warning", "exclusion"],
        "kicad_version": "10.0.5",
        "schematic_parity": [],
        "unconnected_items": records,
        "violations": [],
    }


class ConnectivitySplitTests(unittest.TestCase):
    def classify(self, records, pour_nets=("/GND",), mixed_pour_nets=()):
        return split.classify_report(
            drc_report(records),
            "fixture.json",
            list(pour_nets),
            list(mixed_pour_nets),
        )

    def test_signal_pad_track(self):
        result = self.classify(
            [record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")]
        )
        self.assertEqual(result["counts"]["signal_open_records"], 1)
        self.assertEqual(result["records"][0]["bucket"], "signal_open")

    def test_real_kicad_pth_pad_description_is_a_pad(self):
        result = self.classify(
            [record("Pad 5 [/nCS_H] of U3 on F.Cu", "PTH pad 8 [/nCS_H] of J1")]
        )
        self.assertEqual(result["counts"]["signal_open_records"], 1)
        self.assertEqual(result["records"][0]["item_kinds"], ["pad", "pad"])

    def test_every_shape_on_declared_pour_net_is_pour_topology(self):
        shapes = [
            record("Zone 'front' [/GND] on F.Cu", "Zone 'back' [/GND] on B.Cu"),
            record("Zone 'front' [/GND] on F.Cu", "Track [/GND] on F.Cu"),
            record("Zone 'front' [/GND] on F.Cu", "Via [/GND] on F.Cu - B.Cu"),
            record("Track [/GND] on F.Cu", "Via [/GND] on F.Cu - B.Cu"),
            record("Pad 2 [/GND] of C1 on B.Cu", "Track [/GND] on B.Cu"),
        ]
        result = self.classify(shapes)
        self.assertEqual(result["counts"]["pour_topology_records"], len(shapes))
        self.assertEqual(result["counts"]["signal_open_records"], 0)
        self.assertTrue(result["classification_evaluable"])

    def test_mixed_pour_net_leaves_zone_less_record_ambiguous(self):
        result = self.classify(
            [record("Via [/GND] on F.Cu - B.Cu", "Track [/GND] on F.Cu")],
            pour_nets=(),
            mixed_pour_nets=("/GND",),
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("mixed-duty", result["records"][0]["reason"])
        self.assertFalse(result["classification_evaluable"])

    def test_mixed_pour_net_leaves_zone_record_ambiguous_too(self):
        result = self.classify(
            [record("Zone 'g' [/GND] on F.Cu", "Track [/GND] on F.Cu")],
            pour_nets=(),
            mixed_pour_nets=("/GND",),
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertFalse(result["classification_evaluable"])

    def test_same_net_cannot_be_pure_and_mixed_pour(self):
        with self.assertRaisesRegex(split.ConnectivityError, "both pure and mixed"):
            self.classify([], pour_nets=("/GND",), mixed_pour_nets=("/GND",))

    def test_zone_on_undeclared_net_is_ambiguous(self):
        result = self.classify(
            [record("Zone 'supply' [/VCC] on F.Cu", "Pad 1 [/VCC] of U1 on F.Cu")]
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("undeclared", result["records"][0]["reason"])

    def test_mismatched_item_nets_are_ambiguous(self):
        result = self.classify(
            [record("Pad 1 [/A] of R1 on F.Cu", "Track [/B] on F.Cu")]
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("different nets", result["records"][0]["reason"])

    def test_missing_net_token_is_ambiguous(self):
        result = self.classify(
            [record("Pad 1 of R1 on F.Cu", "Track [/A] on F.Cu")]
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)

    def test_counts_partition_total(self):
        result = self.classify(
            [
                record("Pad 1 [/SIG] of R1 on F.Cu", "Via [/SIG] on F.Cu - B.Cu"),
                record("Zone 'g' [/GND] on F.Cu", "Track [/GND] on F.Cu"),
                record("Zone 'v' [/VCC] on F.Cu", "Pad 1 [/VCC] of U1 on F.Cu"),
            ]
        )
        self.assertEqual(
            result["counts"],
            {
                "signal_open_records": 1,
                "pour_topology_records": 1,
                "ambiguous_records": 1,
                "total_unconnected_records": 3,
            },
        )

    def test_empty_unconnected_list_is_valid(self):
        result = self.classify([])
        self.assertEqual(result["counts"]["total_unconnected_records"], 0)
        self.assertTrue(result["classification_evaluable"])

    def test_missing_unconnected_field_is_rejected(self):
        fixture = drc_report([])
        del fixture["unconnected_items"]
        with self.assertRaisesRegex(split.ConnectivityError, "missing key.*unconnected_items"):
            split.classify_report(fixture, "fixture.json", ["/GND"])

    def test_unexpected_record_type_is_ambiguous(self):
        row = record("Pad 1 [/A] of R1 on F.Cu", "Track [/A] on F.Cu")
        row["type"] = "clearance"
        result = self.classify([row])
        self.assertEqual(result["counts"]["ambiguous_records"], 1)

    def test_record_requires_description(self):
        row = record("Pad 1 [/A] of R1 on F.Cu", "Track [/A] on F.Cu")
        del row["description"]
        result = self.classify([row])
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("no description", result["records"][0]["reason"])

    def test_record_requires_exactly_two_items(self):
        result = self.classify(
            [
                record(
                    "Pad 1 [/A] of R1 on F.Cu",
                    "Track [/A] on F.Cu",
                    "Via [/A] on F.Cu - B.Cu",
                )
            ]
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("exactly two", result["records"][0]["reason"])

    def test_record_severity_must_be_in_report_severities(self):
        row = record("Pad 1 [/A] of R1 on F.Cu", "Track [/A] on F.Cu")
        row["severity"] = "banana"
        result = self.classify([row])
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertIn("included_severities", result["records"][0]["reason"])

    def test_unknown_item_kind_is_ambiguous(self):
        result = self.classify(
            [record("Garbage [/A] on F.Cu", "Track [/A] on F.Cu")]
        )
        self.assertEqual(result["counts"]["ambiguous_records"], 1)

    def test_non_kicad_root_is_rejected(self):
        with self.assertRaisesRegex(split.ConnectivityError, "missing key"):
            split.classify_report({"unconnected_items": []}, "fixture.json", [])

    def test_all_kicad_coordinate_units_are_accepted(self):
        for units in ("mm", "in", "mils"):
            with self.subTest(units=units):
                fixture = drc_report([])
                fixture["coordinate_units"] = units
                result = split.classify_report(fixture, "fixture.json", [])
                self.assertTrue(result["classification_evaluable"])

    def test_unknown_coordinate_units_are_rejected(self):
        fixture = drc_report([])
        fixture["coordinate_units"] = "yards"
        with self.assertRaisesRegex(split.ConnectivityError, "coordinate_units"):
            split.classify_report(fixture, "fixture.json", [])

    def test_report_without_full_severity_is_rejected(self):
        fixture = drc_report([])
        fixture["included_severities"] = ["warning", "exclusion"]
        with self.assertRaisesRegex(split.ConnectivityError, "full-severity.*missing error"):
            split.classify_report(fixture, "fixture.json", [])

    def test_report_with_unknown_severity_is_rejected(self):
        fixture = drc_report([])
        fixture["included_severities"].append("garbage")
        with self.assertRaisesRegex(split.ConnectivityError, "unknown garbage"):
            split.classify_report(fixture, "fixture.json", [])

    def test_report_ignoring_unconnected_items_is_rejected(self):
        fixture = drc_report([])
        fixture["ignored_checks"] = [
            {"key": "unconnected_items", "description": "Unconnected items"}
        ]
        with self.assertRaisesRegex(split.ConnectivityError, "ignores unconnected_items"):
            split.classify_report(fixture, "fixture.json", [])

    def test_exact_199_records_is_marked_possibly_censored(self):
        rows = [
            record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
            for _ in range(split.KICAD_10_REPORT_CAP)
        ]
        result = self.classify(rows)
        self.assertEqual(result["counts"]["total_unconnected_records"], 199)
        self.assertTrue(result["report_censored"])
        self.assertFalse(result["classification_evaluable"])

    def test_cli_exit_codes_and_json(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(
                json.dumps(
                    drc_report(
                        [
                            record(
                                "Zone 'g' [/GND] on F.Cu",
                                "Via [/GND] on F.Cu - B.Cu",
                            )
                        ]
                    )
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--pour-net",
                            "/GND",
                            "--report-only",
                            "--json",
                            str(out),
                        ]
                    ),
                    0,
                )
                self.assertEqual(
                    split.main(
                        [str(drc), "--pour-net", "/GND", "--require-zero-total"]
                    ),
                    4,
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["schema"], split.SCHEMA)
            self.assertEqual(written["aggregate_gate"], "not_requested")
            self.assertEqual(len(written["source_sha256"]), 64)
            self.assertEqual(written["source_size"], drc.stat().st_size)

    def test_json_records_aggregate_gate_failure(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(
                json.dumps(
                    drc_report(
                        [record("Pad 1 [/SIG] of R1", "Track [/SIG] on F.Cu")]
                    )
                ),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--no-pour-nets",
                            "--require-zero-total",
                            "--json",
                            str(out),
                        ]
                    ),
                    4,
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertTrue(written["classification_evaluable"])
            self.assertEqual(written["aggregate_gate"], "fail")

    def test_failed_parse_invalidates_existing_report(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "bad.json"
            out = directory / "split.json"
            drc.write_text("not json", encoding="utf-8")
            out.write_text(
                json.dumps({"schema": split.SCHEMA, "classification_evaluable": True}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(["--no-pour-nets", str(drc), "--json", str(out)]),
                    2,
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(written["classification_evaluable"])
            self.assertEqual(written["error"], "classification did not complete")

    def test_refuses_to_replace_unrelated_output(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "important.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            out.write_text(json.dumps({"schema": "other"}), encoding="utf-8")
            original = out.read_bytes()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(split.main([str(drc), "--json", str(out)]), 2)
            self.assertEqual(out.read_bytes(), original)

    def test_preparse_does_not_destroy_owned_input_output_alias(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            aliased = pathlib.Path(raw_dir) / "drc.json"
            fixture = drc_report([])
            fixture["schema"] = split.SCHEMA
            original = json.dumps(fixture).encode("utf-8")
            aliased.write_bytes(original)
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(aliased),
                            "--no-pour-nets",
                            "--json",
                            str(aliased),
                        ]
                    ),
                    2,
                )
            self.assertEqual(aliased.read_bytes(), original)

    def test_argparse_failure_invalidates_existing_report(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            out.write_text(
                json.dumps({"schema": split.SCHEMA, "classification_evaluable": True}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main([str(drc), "--json", str(out), "--bad-option"])
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(written["classification_evaluable"])

    def test_argparse_failure_with_options_first_invalidates_existing_report(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            out.write_text(
                json.dumps({"schema": split.SCHEMA, "classification_evaluable": True}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main(
                    [
                        "--pour-net",
                        "/GND",
                        str(drc),
                        "--json",
                        str(out),
                        "--bad-option",
                    ]
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(written["classification_evaluable"])

    def test_abbreviated_json_option_is_rejected_without_leaving_stale_pass(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            original = json.dumps(
                {"schema": split.SCHEMA, "classification_evaluable": True}
            )
            out.write_text(original, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main(
                    [str(drc), "--no-pour-nets", "--j", str(out), "--bad-option"]
                )
            # Rejected as an argument (allow_abbrev is off) AND invalidated
            # as a report: the operator plainly meant `--j` as the target, so
            # a prior clean result must not survive the failed run.
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(written["classification_evaluable"])

    def test_argparse_failure_invalidates_last_duplicate_json_target(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            first = directory / "first.json"
            effective = directory / "effective.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            for output in (first, effective):
                output.write_text(
                    json.dumps(
                        {"schema": split.SCHEMA, "classification_evaluable": True}
                    ),
                    encoding="utf-8",
                )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main(
                    [
                        str(drc),
                        "--pour-net",
                        "/GND",
                        "--json",
                        str(first),
                        "--json",
                        str(effective),
                        "--bad-option",
                    ]
                )
            self.assertTrue(json.loads(first.read_text())["classification_evaluable"])
            self.assertFalse(
                json.loads(effective.read_text())["classification_evaluable"]
            )

    def test_missing_positional_still_invalidates_json_target(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            out = pathlib.Path(raw_dir) / "split.json"
            out.write_text(
                json.dumps({"schema": split.SCHEMA, "classification_evaluable": True}),
                encoding="utf-8",
            )
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main(["--pour-net", "/GND", "--json", str(out)])
            self.assertFalse(json.loads(out.read_text())["classification_evaluable"])

    def test_prescan_does_not_treat_tokens_after_terminator_as_options(self):
        self.assertIsNone(
            split._prescan_json_output(
                ["--no-pour-nets", "--", "--json", "not-an-output.json"]
            )
        )

    def test_argparse_failure_does_not_touch_post_terminator_owned_report(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "not-an-output.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            original = json.dumps(
                {"schema": split.SCHEMA, "classification_evaluable": True}
            )
            out.write_text(original, encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ), self.assertRaises(SystemExit):
                split.main(
                    ["--no-pour-nets", str(drc), "--", "--json", str(out)]
                )
            self.assertEqual(out.read_text(encoding="utf-8"), original)

    def test_cli_requires_explicit_pour_ownership(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(split.main([str(drc), "--json", str(out)]), 2)
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertFalse(written["classification_evaluable"])

    def test_cli_rejects_no_pour_with_declaration(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        ["--no-pour-nets", "--mixed-pour-net", "/GND", str(drc)]
                    ),
                    2,
                )

    def test_cli_accepts_explicit_no_pour_nets(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(["--no-pour-nets", str(drc), "--report-only"]),
                    0,
                )

    def test_cli_refuses_an_ungraded_gating_run(self):
        """No gate and no --report-only must not read as a pass."""
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            stderr = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                stderr
            ):
                self.assertEqual(split.main(["--no-pour-nets", str(drc)]), 2)
            self.assertIn("ungraded gating run", stderr.getvalue())

    def test_cli_rejects_report_only_with_a_gate(self):
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            "--no-pour-nets",
                            str(drc),
                            "--report-only",
                            "--require-zero-total",
                        ]
                    ),
                    2,
                )

    def test_signal_open_gate_fails_while_aggregate_gate_would_not_be_usable(self):
        """The gate the split exists for: pour records present, signal open."""
        report = drc_report(
            [
                record("Pad 1 [/SENSE] of R1 on F.Cu", "Track [/SENSE] on F.Cu"),
                record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu"),
            ]
        )
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--pour-net",
                            "/GND",
                            "--require-zero-signal-opens",
                            "--json",
                            str(out),
                        ]
                    ),
                    4,
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["signal_open_gate"], "fail")
            self.assertEqual(written["aggregate_gate"], "not_requested")
            self.assertEqual(written["counts"]["signal_open_records"], 1)
            self.assertEqual(written["counts"]["pour_topology_records"], 1)

    def test_signal_open_gate_passes_with_pour_records_present(self):
        report = drc_report(
            [record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")]
        )
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            drc.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--pour-net",
                            "/GND",
                            "--require-zero-signal-opens",
                            "--json",
                            str(out),
                        ]
                    ),
                    0,
                )
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["signal_open_gate"], "pass")


if __name__ == "__main__":
    unittest.main()
