import contextlib
import io
import json
import pathlib
import subprocess
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


BOARD_TEXT = """(kicad_pcb
	(version 20241229)
	(net 0 "")
	(net 1 "/SIG")
	(net 2 "/GND")
)
"""


def write_board(directory, name="fixture.kicad_pcb"):
    """A saved board declaring /SIG and /GND.

    A gating run needs one: the report's digest binds the REPORT, and the
    board is what is being fabricated."""
    path = pathlib.Path(directory) / name
    path.write_text(BOARD_TEXT, encoding="utf-8")
    return path


def drc_report(records, source="fixture.kicad_pcb"):
    """Shaped like a REAL KiCad 10.0.5 export, including `source` and `date`.

    Both are present in every real export -- verified against the committed
    fixtures/open-net.drc.json -- and the guard now requires them, because a
    report that cannot name its board cannot be checked against one."""
    return {
        "$schema": split.KICAD_DRC_SCHEMA,
        "coordinate_units": "mm",
        "date": "2026-09-07T09:51:08",
        "ignored_checks": [],
        "included_severities": ["error", "warning", "exclusion"],
        "kicad_version": "10.0.5",
        "schematic_parity": [],
        "source": source,
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

    def test_a_count_above_the_cap_stays_unevaluable(self):
        """MONOTONICITY. `== cap` let a strictly worse report recover.

        Measured 2026-09-07 before the fix: 198 records exited 0, 199 exited 3,
        and 200 exited 0 again -- so 200 duplicated pour records passed the
        signal gate that 199 could not. A count above a cap this tool cannot
        qualify is not better evidence than a count at it; it means the report
        came from a release whose cap behaviour is unknown here.
        """
        for total in (split.KICAD_10_REPORT_CAP,
                      split.KICAD_10_REPORT_CAP + 1,
                      split.KICAD_10_REPORT_CAP * 3):
            with self.subTest(total=total):
                rows = [record("Pad 1 [/SIG] of R1 on F.Cu",
                               "Track [/SIG] on F.Cu")
                        for _ in range(total)]
                result = self.classify(rows)
                self.assertTrue(result["report_censored"])
                self.assertFalse(result["classification_evaluable"])

    def test_below_the_cap_is_still_evaluable(self):
        rows = [record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP - 1)]
        result = self.classify(rows)
        self.assertFalse(result["report_censored"])

    def test_the_cap_can_be_qualified_but_never_silently_disabled(self):
        """`none` is the documented escape once the release has been probed."""
        rows = [record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP + 1)]
        result = split.classify_report(
            drc_report(rows), "fixture.json", [], [], cap=None)
        self.assertFalse(result["report_censored"])
        self.assertIsNone(result["report_cap"])

    def test_a_malformed_report_cap_is_a_config_error_not_a_mute(self):
        for bad in ("banana", "0", "-1", "", None, "1.5"):
            with self.subTest(value=bad):
                with self.assertRaises(split.ConnectivityError):
                    split._parse_report_cap(bad)
        self.assertIsNone(split._parse_report_cap("none"))
        self.assertIsNone(split._parse_report_cap("NONE"))
        self.assertEqual(split._parse_report_cap("42"), 42)

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
            board = write_board(directory)
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
                            "--board",
                            str(board),
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
                        [str(drc), "--board", str(board),
                         "--pour-net", "/GND", "--require-zero-total"]
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
            board = write_board(directory)
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
                            "--board",
                            str(board),
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
            board = write_board(directory)
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
            board = write_board(directory)
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
            board = write_board(directory)
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
            board = write_board(directory)
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
            board = write_board(raw_dir)
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
            board = write_board(raw_dir)
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(["--no-pour-nets", str(drc), "--report-only"]),
                    0,
                )

    def test_declaring_a_net_pour_cannot_launder_a_real_signal_open(self):
        """A pad-to-track open is authored routing. Classifying every record
        on a declared net as pour topology let the caller turn a real open
        into a fabrication-closing PASS by labelling its net (codex review
        of 7b00165): --no-pour-nets exited 4, --pour-net /SIG exited 0."""
        opening = record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            board = write_board(raw_dir)
            drc.write_text(json.dumps(drc_report([opening])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--no-pour-nets",
                                "--require-zero-signal-opens"]), 4)
                # The same record, relabelled: unevaluable, never a pass.
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--pour-net", "/SIG",
                                "--require-zero-signal-opens"]), 3)
                # The aggregate gate keeps its recorded behaviour.
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--pour-net", "/SIG",
                                "--require-zero-total"]), 4)

    def test_a_genuine_pour_record_still_passes_the_signal_gate(self):
        """The tightening must not manufacture false failures.

        An all-zone record on a declared pour net -- a zone island, which is
        what a refill actually produces -- is what the split exists to excuse,
        and it still passes.
        """
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            board = write_board(raw_dir)
            drc.write_text(
                json.dumps(drc_report([
                    record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")])),
                encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--pour-net", "/GND",
                                "--require-zero-signal-opens"]), 0)

    def test_cli_refuses_an_ungraded_gating_run(self):
        """No gate and no --report-only must not read as a pass."""
        with tempfile.TemporaryDirectory() as raw_dir:
            drc = pathlib.Path(raw_dir) / "drc.json"
            board = write_board(raw_dir)
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
            board = write_board(raw_dir)
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
                record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu"),
            ]
        )
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            board = write_board(directory)
            drc.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--board",
                            str(board),
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
            [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")]
        )
        with tempfile.TemporaryDirectory() as raw_dir:
            directory = pathlib.Path(raw_dir)
            drc = directory / "drc.json"
            out = directory / "split.json"
            board = write_board(directory)
            drc.write_text(json.dumps(report), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(
                io.StringIO()
            ):
                self.assertEqual(
                    split.main(
                        [
                            str(drc),
                            "--board",
                            str(board),
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


KICAD_CLI = pathlib.Path(
    "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"


class RealKiCadExport(unittest.TestCase):
    """The artefact tier: a real board, a real `kicad-cli pcb drc` export.

    Every other test in this file builds its report from this file's own
    helpers, so they cannot catch exporter or schema drift, real
    item-description grammar, or the provenance fields KiCad actually writes.
    Finding 5 of the 2026-09-07 codex review of 7a99de9 asked for exactly this.
    """

    def test_the_committed_fixture_is_a_real_export_with_two_real_opens(self):
        report = json.loads(
            (FIXTURES / "open-net.drc.json").read_text(encoding="utf-8"))
        self.assertEqual(report["$schema"], split.KICAD_DRC_SCHEMA)
        self.assertEqual(report["kicad_version"], "10.0.5")
        self.assertEqual(report["source"], "open-net.kicad_pcb")
        result = split.classify_report(
            report, "open-net.drc.json", [], [], strict_pour=True)
        self.assertEqual(result["counts"]["signal_open_records"], 2)
        self.assertEqual(result["counts"]["ambiguous_records"], 0)
        self.assertTrue(result["classification_evaluable"])

    def test_the_gate_fails_on_the_real_board(self):
        board = FIXTURES / "open-net.kicad_pcb"
        drc = FIXTURES / "open-net.drc.json"
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = split.main([str(drc), "--board", str(board),
                               "--no-pour-nets", "--require-zero-signal-opens"])
        self.assertEqual(code, 4, "two real opens must fail the gate")

    @unittest.skipUnless(KICAD_CLI.exists(), "KiCad 10 CLI not installed")
    def test_kicad_still_exports_what_this_tool_parses(self):
        """Re-run the real exporter and re-parse it.

        This is the check that notices when a KiCad upgrade changes the
        report: the committed fixture alone would go stale silently.
        """
        with tempfile.TemporaryDirectory() as raw:
            board = pathlib.Path(raw) / "open-net.kicad_pcb"
            board.write_bytes((FIXTURES / "open-net.kicad_pcb").read_bytes())
            out = pathlib.Path(raw) / "fresh.drc.json"
            proc = subprocess.run(
                [str(KICAD_CLI), "pcb", "drc", "--format", "json",
                 "--severity-all", "--units", "mm", "-o", str(out), str(board)],
                capture_output=True, text=True)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            report = json.loads(out.read_text(encoding="utf-8"))
            result = split.classify_report(
                report, str(out), [], [], strict_pour=True)
            self.assertEqual(result["counts"]["signal_open_records"], 2)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main([str(out), "--board", str(board),
                                   "--no-pour-nets",
                                   "--require-zero-signal-opens"])
            self.assertEqual(code, 4)


class ReportMustBeAboutTheBoard(unittest.TestCase):
    """Finding 1: the report was bound to its own JSON bytes, not the board."""

    def test_a_gating_run_must_name_the_board(self):
        with tempfile.TemporaryDirectory() as raw:
            drc = pathlib.Path(raw) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(drc), "--no-pour-nets",
                                   "--require-zero-signal-opens"])
            self.assertEqual(code, 2)
            self.assertIn("--board", errors.getvalue())

    def test_a_clean_report_for_another_board_is_refused(self):
        """KNOWN-BAD CALIBRATION. A zero-open report naming a DIFFERENT design
        exited 0 and read as 'this board is connected'."""
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory, "the-board-being-made.kicad_pcb")
            drc = directory / "drc.json"
            drc.write_text(
                json.dumps(drc_report([], source="some-other-board.kicad_pcb")),
                encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(drc), "--board", str(board),
                                   "--no-pour-nets",
                                   "--require-zero-signal-opens"])
            self.assertEqual(code, 2, "a report for another board passed")
            self.assertIn("is not a verdict about this one", errors.getvalue())

    def test_report_only_may_still_inspect_a_report_alone(self):
        with tempfile.TemporaryDirectory() as raw:
            drc = pathlib.Path(raw) / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main([str(drc), "--no-pour-nets", "--report-only"])
            self.assertEqual(code, 0)

    def test_a_report_missing_its_provenance_is_refused(self):
        for key in ("source", "date"):
            with self.subTest(missing=key):
                fixture = drc_report([])
                del fixture[key]
                with self.assertRaisesRegex(split.ConnectivityError,
                                            "missing key"):
                    split.classify_report(fixture, "f.json", [])

    def test_a_declared_pour_net_must_exist_on_the_board(self):
        """A misspelled --pour-net declared nothing, silently."""
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory)
            drc = directory / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(drc), "--board", str(board),
                                   "--pour-net", "/GNDD",
                                   "--require-zero-signal-opens"])
            self.assertEqual(code, 2)
            self.assertIn("not nets on", errors.getvalue())


class PourDeclarationIsNotEvidenceAboutARecord(unittest.TestCase):
    """Finding 2: requiring only that a zone be PRESENT still laundered opens."""

    def _classify(self, *descriptions):
        return split.classify_report(
            drc_report([record(*descriptions)]), "f.json", ["/SIG"],
            [], strict_pour=True)

    def test_a_track_stranded_from_its_pour_is_not_refill_topology(self):
        """KNOWN-BAD CALIBRATION for the laundering the reviewer demonstrated.

        `Zone [/SIG] <-> Track [/SIG]` with `--pour-net /SIG` was classified
        as pour topology and exited 0. A track that does not reach its pour is
        authored copper that does not connect; DRC text cannot tell it from a
        refill artefact, so it is ambiguous -- which forces exit 3, never a
        pass.
        """
        result = self._classify("Zone [/SIG] on F.Cu", "Track [/SIG] on F.Cu")
        self.assertEqual(result["counts"]["signal_open_records"], 0)
        self.assertEqual(result["counts"]["pour_topology_records"], 0)
        self.assertEqual(result["counts"]["ambiguous_records"], 1)
        self.assertFalse(result["classification_evaluable"])

    def test_a_pad_stranded_from_its_pour_is_not_refill_topology(self):
        result = self._classify("Pad 1 [/SIG] of R1 on F.Cu",
                                "Zone [/SIG] on F.Cu")
        self.assertEqual(result["counts"]["ambiguous_records"], 1)

    def test_a_via_stranded_from_its_pour_is_not_refill_topology(self):
        result = self._classify("Via [/SIG] on F.Cu", "Zone [/SIG] on F.Cu")
        self.assertEqual(result["counts"]["ambiguous_records"], 1)

    def test_an_all_zone_record_is_still_pour_topology(self):
        result = self._classify("Zone [/SIG] on F.Cu", "Zone [/SIG] on B.Cu")
        self.assertEqual(result["counts"]["pour_topology_records"], 1)
        self.assertTrue(result["classification_evaluable"])


if __name__ == "__main__":
    unittest.main()
