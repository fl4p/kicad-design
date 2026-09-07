import contextlib
import hashlib
import datetime
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

import kicad_drc_connectivity as split


def item(description, uuid=None):
    """Real KiCad items each carry their OWN uuid.

    The helper used to hand every item the literal "u", which made two
    different records hash to the same `record_id` -- an artefact of the
    fixture, but one that hid how much `record_id` leans on uuid uniqueness.
    """
    if uuid is None:
        # Derived from the description, so two items with the SAME description
        # share a uuid -- which is what a real board would do only if they
        # were the same object. Pass `uuid=` explicitly where a test needs two
        # distinct objects that read alike.
        uuid = hashlib.sha256(description.encode("utf-8")).hexdigest()[:12]
    return {"description": description, "uuid": uuid, "pos": {"x": 1, "y": 2}}


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


def _now_stamp():
    """A report is taken AFTER the board is saved, which is what the guard
    checks. A fixed past timestamp would be refused as stale, correctly."""
    return datetime.datetime.now().replace(microsecond=0).isoformat()


def drc_report(records, source="fixture.kicad_pcb", date=None):
    """Shaped like a REAL KiCad 10.0.5 export, including `source` and `date`.

    Both are present in every real export -- verified against the committed
    fixtures/open-net.drc.json -- and the guard now requires them, because a
    report that cannot name its board cannot be checked against one."""
    return {
        "$schema": split.KICAD_DRC_SCHEMA,
        "coordinate_units": "mm",
        "date": date or _now_stamp(),
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
                            "--trust-external-report",
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
                                "--trust-external-report",
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
                            "--trust-external-report",
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
            ):
                # The positional became optional when --run-drc was added, so
                # omitting it is now a controlled configuration error instead
                # of an argparse usage error. Same exit code, and -- the point
                # of this test -- the pre-parse invalidation still fires, so a
                # stale clean report cannot survive the bad invocation.
                code = split.main(["--pour-net", "/GND", "--json", str(out)])
            self.assertEqual(code, 2)
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
                                "--trust-external-report",
                                "--no-pour-nets",
                                "--require-zero-signal-opens"]), 4)
                # The same record, relabelled: unevaluable, never a pass.
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--trust-external-report",
                                "--pour-net", "/SIG",
                                "--require-zero-signal-opens"]), 3)
                # The aggregate gate keeps its recorded behaviour.
                self.assertEqual(
                    split.main([str(drc), "--board", str(board),
                                "--trust-external-report",
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
                                "--trust-external-report",
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
                            "--trust-external-report",
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
                            "--trust-external-report",
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


def _find_kicad_cli():
    """Discover kicad-cli rather than hardcoding one macOS path.

    A hardcoded path meant an installed KiCad anywhere else silently became a
    skip, so "when a KiCad is installed" was false as written."""
    found = shutil.which("kicad-cli")
    if found:
        return pathlib.Path(found)
    candidates = [
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
        "/opt/homebrew/bin/kicad-cli",
    ]
    for candidate in candidates:
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return pathlib.Path(candidate)
    return None


KICAD_CLI = _find_kicad_cli()
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

    def test_the_committed_json_actually_describes_the_committed_board(self):
        """Correspondence, not just internal consistency.

        The previous tests would have passed after swapping the board for
        unrelated bytes of the same name, because nothing tied the report to
        it. Every item the report cites must be an object the board declares:
        UUIDs, nets and positions all have to line up.
        """
        board = (FIXTURES / "open-net.kicad_pcb").read_text(encoding="utf-8")
        report = json.loads(
            (FIXTURES / "open-net.drc.json").read_text(encoding="utf-8"))

        self.assertEqual(report["source"], "open-net.kicad_pcb")
        board_nets = set(split.BOARD_NET.findall(board))
        board_uuids = set(re.findall(r'\(uuid "([^"]+)"\)', board))
        pad_positions = {(10.0, 10.0), (13.0, 10.0), (30.0, 10.0), (33.0, 10.0)}

        cited = 0
        for row in report["unconnected_items"]:
            for entry in row["items"]:
                cited += 1
                self.assertIn(entry["uuid"], board_uuids,
                              "report cites an object the board does not have")
                net, _ = split._item_net(entry["description"])
                self.assertIn(net, board_nets,
                              "report cites a net the board does not declare")
                self.assertIn((entry["pos"]["x"], entry["pos"]["y"]),
                              pad_positions,
                              "report cites a position no pad occupies")
        self.assertEqual(cited, 4, "two records of two items each")

    def test_the_gate_fails_on_the_real_board(self):
        board = FIXTURES / "open-net.kicad_pcb"
        drc = FIXTURES / "open-net.drc.json"
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            code = split.main([str(drc), "--board", str(board),
                                "--trust-external-report",
                               # The fixture's mtime is its checkout time, so
                               # the committed export is "older than the
                               # board" by construction. This is the flag's
                               # documented purpose, not a workaround.
                               "--allow-report-older-than-board",
                               "--no-pour-nets", "--require-zero-signal-opens"])
        self.assertEqual(code, 4, "two real opens must fail the gate")

    @unittest.skipUnless(KICAD_CLI is not None, "kicad-cli not found")
    def test_kicad_still_exports_what_this_tool_parses(self):
        """Re-run the real exporter and re-parse it.

        This is the check that notices when a KiCad upgrade changes the
        report: the committed fixture alone would go stale silently.
        """
        with tempfile.TemporaryDirectory() as raw:
            board = pathlib.Path(raw) / "open-net.kicad_pcb"
            board.write_bytes((FIXTURES / "open-net.kicad_pcb").read_bytes())
            out = pathlib.Path(raw) / "fresh.drc.json"
            try:
                proc = subprocess.run(
                    [str(KICAD_CLI), "pcb", "drc", "--format", "json",
                     "--severity-all", "--units", "mm", "-o", str(out),
                     str(board)],
                    capture_output=True, text=True, timeout=120)
            except (OSError, subprocess.SubprocessError) as exc:
                self.skipTest("kicad-cli could not be executed: %s" % exc)
            # An exporter that CANNOT RUN is an environment fact, not a
            # finding: KiCad's CLI aborts under a sandbox that denies its GUI
            # bootstrap (measured: SIGABRT under a codex seatbelt, the same
            # denial that stops Chrome starting there). Skipping that is
            # honest. An exporter that DID run and produced something this
            # tool cannot parse, or a different open count, is a real failure
            # and must stay one -- that is the whole point of the tier.
            if proc.returncode < 0:
                # Killed by a signal: the process never got to run. That is
                # this machine's sandbox, not KiCad. (Measured: SIGABRT under
                # a codex seatbelt, the same denial that stops Chrome.)
                self.skipTest(
                    "kicad-cli was killed by signal %d before it could run; "
                    "an environment limitation, not a report-format finding"
                    % -proc.returncode)
            # A POSITIVE non-zero exit means kicad-cli RAN and reported a
            # problem, and a missing output after a clean exit means it ran
            # and produced nothing. Both are real findings about the exporter
            # this suite exists to track, so neither may be skipped -- the
            # earlier branch skipped on any non-zero status, which would have
            # hidden a genuine crash of an installed KiCad (codex review of
            # 298ed6d).
            self.assertEqual(proc.returncode, 0, proc.stderr)
            self.assertTrue(out.exists(), "kicad-cli wrote no report")
            report = json.loads(out.read_text(encoding="utf-8"))
            result = split.classify_report(
                report, str(out), [], [], strict_pour=True)
            self.assertEqual(result["counts"]["signal_open_records"], 2)
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main([str(out), "--board", str(board),
                                "--trust-external-report",
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
                code = split.main([str(drc), "--trust-external-report",
                                   "--no-pour-nets",
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
                                "--trust-external-report",
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
                                "--trust-external-report",
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


class ReportFreshnessAndCapEvidence(unittest.TestCase):
    """Findings 1 and 2 of the 2026-09-07 codex review of 75c10fa."""

    def _run(self, argv, report, directory):
        board = write_board(directory)
        drc = pathlib.Path(directory) / "drc.json"
        drc.write_text(json.dumps(report), encoding="utf-8")
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(errors):
            code = split.main([str(drc), "--board", str(board),
                               "--trust-external-report"] + argv)
        return code, errors.getvalue()

    def test_a_report_older_than_the_board_is_refused(self):
        """KNOWN-BAD CALIBRATION. Basename equality cannot see time: a clean
        report generated before the board changed named the right file and
        passed."""
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--no-pour-nets", "--require-zero-signal-opens"],
                drc_report([], date="2020-01-01T00:00:00"), raw)
        self.assertEqual(code, 2)
        self.assertIn("older than the board", errors)

    def test_the_staleness_refusal_can_be_overridden_explicitly(self):
        with tempfile.TemporaryDirectory() as raw:
            code, _ = self._run(
                ["--no-pour-nets", "--require-zero-signal-opens",
                 "--allow-report-older-than-board"],
                drc_report([], date="2020-01-01T00:00:00"), raw)
        self.assertEqual(code, 0)

    def test_an_unparseable_or_missing_date_is_refused(self):
        for bad in ("not-a-date", "", None, 17):
            with self.subTest(date=bad):
                with tempfile.TemporaryDirectory() as raw:
                    report = drc_report([])
                    report["date"] = bad
                    code, _ = self._run(
                        ["--no-pour-nets", "--require-zero-signal-opens"],
                        report, raw)
                self.assertEqual(code, 2)

    def test_a_report_taken_in_the_same_second_as_the_save_is_fine(self):
        """KiCad's `date` is second-resolution; the board mtime is not."""
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--no-pour-nets", "--require-zero-signal-opens"],
                drc_report([]), raw)
        self.assertEqual(code, 0, errors)

    def test_a_report_dated_in_the_future_is_refused(self):
        """A future timestamp satisfies any freshness check, forever."""
        ahead = (datetime.datetime.now()
                 + datetime.timedelta(days=365)).replace(
                     microsecond=0).isoformat()
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--no-pour-nets", "--require-zero-signal-opens"],
                drc_report([], date=ahead), raw)
        self.assertEqual(code, 2)
        self.assertIn("in the future", errors)

    def test_a_utc_z_timestamp_is_accepted_on_every_interpreter(self):
        """`Z` is legal ISO-8601; Python 3.9 will not parse it unaided."""
        stamp = (datetime.datetime.now(datetime.timezone.utc)
                 + datetime.timedelta(seconds=30)).replace(
                     microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--no-pour-nets", "--require-zero-signal-opens"],
                drc_report([], date=stamp), raw)
        self.assertEqual(code, 0, errors)

    def test_overriding_the_cap_requires_naming_the_release_probed(self):
        """KNOWN-BAD CALIBRATION for the unverified mute switch.

        `--report-cap none` and `--report-cap 200` silenced the cap with no
        evidence: the report did not improve, only the warning went away."""
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        for override in ("none", "200"):
            with self.subTest(cap=override):
                with tempfile.TemporaryDirectory() as raw:
                    code, errors = self._run(
                        ["--pour-net", "/GND", "--require-zero-signal-opens",
                         "--report-cap", override],
                        drc_report(rows), raw)
                self.assertEqual(code, 2, "an unqualified override passed")
                self.assertIn("--report-cap-probed-on", errors)

    def test_an_override_probed_on_another_release_is_refused(self):
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--pour-net", "/GND", "--require-zero-signal-opens",
                 "--report-cap", "none", "--report-cap-probed-on", "9.0.1"],
                drc_report(rows), raw)
        self.assertEqual(code, 2)
        self.assertIn("does not match the KiCad that wrote", errors)

    def test_an_override_without_a_probe_is_refused(self):
        """Naming a version only asserts that a probe happened."""
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        with tempfile.TemporaryDirectory() as raw:
            code, errors = self._run(
                ["--pour-net", "/GND", "--require-zero-signal-opens",
                 "--report-cap", "none", "--report-cap-probed-on", "10.0.5"],
                drc_report(rows), raw)
        self.assertEqual(code, 2)
        self.assertIn("--report-cap-probe", errors)

    def _probe_file(self, directory, findings, version="10.0.5"):
        """A DRC report carrying more findings of one type than the cap."""
        probe = drc_report([record("Pad 1 [/X] of R1 on F.Cu",
                                   "Track [/X] on F.Cu")
                            for _ in range(findings)])
        probe["kicad_version"] = version
        path = pathlib.Path(directory) / "probe.json"
        path.write_text(json.dumps(probe), encoding="utf-8")
        return path

    def test_an_override_backed_by_a_real_probe_is_accepted(self):
        """The measurement, not the label: a report from that release
        actually containing more findings than the cap."""
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        with tempfile.TemporaryDirectory() as raw:
            probe = self._probe_file(raw, split.KICAD_10_REPORT_CAP + 5)
            code, errors = self._run(
                ["--pour-net", "/GND", "--require-zero-signal-opens",
                 "--report-cap", "none", "--report-cap-probed-on", "10.0.5",
                 "--report-cap-probe", str(probe)],
                drc_report(rows), raw)
        self.assertEqual(code, 0, errors)

    def test_a_probe_that_does_not_exceed_the_cap_proves_nothing(self):
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        with tempfile.TemporaryDirectory() as raw:
            probe = self._probe_file(raw, 12)
            code, errors = self._run(
                ["--pour-net", "/GND", "--require-zero-signal-opens",
                 "--report-cap", "none", "--report-cap-probed-on", "10.0.5",
                 "--report-cap-probe", str(probe)],
                drc_report(rows), raw)
        self.assertEqual(code, 2)
        self.assertIn("does not demonstrate", errors)

    def test_a_probe_from_another_release_is_refused(self):
        rows = [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")
                for _ in range(split.KICAD_10_REPORT_CAP)]
        with tempfile.TemporaryDirectory() as raw:
            probe = self._probe_file(raw, split.KICAD_10_REPORT_CAP + 5,
                                     version="9.0.1")
            code, errors = self._run(
                ["--pour-net", "/GND", "--require-zero-signal-opens",
                 "--report-cap", "none", "--report-cap-probed-on", "10.0.5",
                 "--report-cap-probe", str(probe)],
                drc_report(rows), raw)
        self.assertEqual(code, 2)
        self.assertIn("was produced by KiCad", errors)


class PerRecordAcknowledgement(unittest.TestCase):
    """Finding 5: the strict gate must stay usable on real pour nets.

    ROUTING.md records that measured reports contain zone-track, zone-via and
    track-via records on pour-managed nets, so a blanket refusal would break
    the workflow the tool exists for. A blanket net LABEL is what let a real
    open through, though -- so the escape is per-record.
    """

    def _setup(self, directory, descriptions):
        board = write_board(directory)
        row = record(*descriptions)
        drc = pathlib.Path(directory) / "drc.json"
        drc.write_text(json.dumps(drc_report([row])), encoding="utf-8")
        return board, drc, split.record_id(row)

    def _run(self, board, drc, extra):
        errors = io.StringIO()
        with contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(errors):
            code = split.main([str(drc), "--board", str(board),
                                "--trust-external-report", "--pour-net",
                               "/GND", "--require-zero-signal-opens"] + extra)
        return code, errors.getvalue()

    def test_an_unacknowledged_zone_track_record_is_still_ambiguous(self):
        with tempfile.TemporaryDirectory() as raw:
            board, drc, _ = self._setup(
                raw, ("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu"))
            code, _ = self._run(board, drc, [])
        self.assertEqual(code, 3)

    def test_acknowledging_that_record_by_id_lets_it_through(self):
        with tempfile.TemporaryDirectory() as raw:
            board, drc, ident = self._setup(
                raw, ("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu"))
            code, errors = self._run(board, drc, ["--reviewed-record", ident])
        self.assertEqual(code, 0, errors)

    def test_an_acknowledgement_does_not_cover_a_record_with_the_same_uuids(self):
        """The sharper case: SAME objects, changed record.

        The sibling test below changes the descriptions, and this file's
        helper derives each uuid from its description -- so that test only
        ever proved that different uuids give different ids. Keying the id on
        uuids alone meant a zone-track record could become a moved zone-via
        record, keep its id, and keep its waiver (codex review of 298ed6d).
        """
        first = record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")
        uuids = [entry["uuid"] for entry in first["items"]]
        second = {**first, "items": [
            item("Zone [/GND] on F.Cu", uuids[0]),
            item("Via [/GND] on B.Cu", uuids[1]),
        ]}
        self.assertEqual([e["uuid"] for e in second["items"]], uuids)
        self.assertNotEqual(split.record_id(first), split.record_id(second))

    def test_a_record_whose_items_share_a_uuid_has_no_id(self):
        """Two items claiming one object cannot be acknowledged."""
        row = record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")
        row["items"][1]["uuid"] = row["items"][0]["uuid"]
        self.assertIsNone(split.record_id(row))

    def test_an_acknowledgement_does_not_cover_a_different_record(self):
        """The id is the record's items, so it cannot be reused."""
        with tempfile.TemporaryDirectory() as raw:
            board, drc, ident = self._setup(
                raw, ("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu"))
            other = split.record_id(
                record("Zone [/GND] on B.Cu", "Via [/GND] on B.Cu"))
            self.assertNotEqual(ident, other)
            code, errors = self._run(board, drc, ["--reviewed-record", other])
        self.assertEqual(code, 2, "a stale id must be refused, not ignored")
        self.assertIn("names no record in this report", errors)

    def test_an_acknowledgement_cannot_excuse_an_undeclared_net(self):
        """Two independent statements are required, not one."""
        with tempfile.TemporaryDirectory() as raw:
            board = write_board(raw)
            row = record("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
            drc = pathlib.Path(raw) / "drc.json"
            drc.write_text(json.dumps(drc_report([row])), encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main(
                    [str(drc), "--board", str(board),
                                "--trust-external-report", "--no-pour-nets",
                     "--require-zero-signal-opens",
                     "--reviewed-record", split.record_id(row)])
        self.assertEqual(code, 4, "a real signal open was acknowledged away")

    def test_the_record_id_is_stable_and_order_independent(self):
        first = record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")
        second = {**first, "items": list(reversed(first["items"]))}
        self.assertEqual(split.record_id(first), split.record_id(second))


class BoardNetTableIsParsedNotGrepped(unittest.TestCase):
    """Finding 3: a text regex counted net-shaped text anywhere in the file."""

    def test_kicad_10_bare_net_references_are_found(self):
        """KiCad 10 has NO top-level net section; a reference is all there is.

        Measured 2026-09-07 over 400 real boards: a KiCad 8 board (version
        20240108) carries 847 numbered `(net N "name")` expressions and no
        bare ones; a KiCad 10 board (version 20260206) carries 1207 bare
        `(net "name")` references and no numbered ones. Matching only the
        numbered form returned an EMPTY inventory for every KiCad 10 board,
        which would have refused every legitimate --pour-net on exactly the
        release this skill targets.
        """
        board = (FIXTURES / "kicad10-bare-nets.kicad_pcb").read_text(
            encoding="utf-8")
        self.assertIn("(version 20260206)", board)
        self.assertNotIn('(net 1 "', board)
        self.assertEqual(split.board_net_table(board), ["/GND", "/SIG"])

    def test_kicad_8_numbered_net_table_is_still_found(self):
        self.assertEqual(
            split.board_net_table(
                '(kicad_pcb\n (net 0 "")\n (net 1 "/SIG")\n (net 2 "/GND")\n)\n'),
            ["", "/GND", "/SIG"])

    def test_a_declared_pour_net_is_accepted_on_a_kicad_10_board(self):
        """The end-to-end consequence of the two spellings."""
        with tempfile.TemporaryDirectory() as raw:
            board = pathlib.Path(raw) / "b.kicad_pcb"
            board.write_bytes(
                (FIXTURES / "kicad10-bare-nets.kicad_pcb").read_bytes())
            drc = pathlib.Path(raw) / "drc.json"
            drc.write_text(
                json.dumps(drc_report(
                    [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")],
                    source="b.kicad_pcb")),
                encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(drc), "--board", str(board),
                                "--trust-external-report",
                                   "--pour-net", "/GND",
                                   "--require-zero-signal-opens"])
        self.assertEqual(code, 0, errors.getvalue())

    def test_a_truncated_board_is_refused(self):
        """A real board in the tree ends mid-structure; refusing it is right."""
        with self.assertRaisesRegex(split.ConnectivityError, "unbalanced"):
            split.board_net_table(
                '(kicad_pcb\n (footprint "x" (pad "1" (net 2))\n )\n)\n)\n')

    def test_a_file_that_is_not_a_board_is_refused(self):
        for text in ('hello (net 99 "/GND") world\n',
                     '# a comment (net 1 "/GND")\n',
                     ''):
            with self.subTest(text=text[:20]):
                with self.assertRaises(split.ConnectivityError):
                    split.board_net_table(text)

    def test_unbalanced_parentheses_are_refused(self):
        with self.assertRaisesRegex(split.ConnectivityError, "unbalanced"):
            split.board_net_table('(kicad_pcb\n (net 1 "/X")\n')

    def test_awkward_net_names_survive(self):
        self.assertEqual(
            split.board_net_table('(kicad_pcb\n (net 1 "/A)B")\n)\n'),
            ["/A)B"])

    def test_an_escaped_quote_is_returned_in_its_RAW_spelling(self):
        """Documented limitation, not an endorsement.

        The scanner returns the bytes between the quotes, so a net whose name
        contains a quote comes back as `/A\\"B` rather than the semantic
        `/A"B`. A --pour-net for such a net must therefore be spelled the way
        the file spells it. Nothing in the corpus of 400 real boards has one;
        this test exists so the behaviour is a recorded choice rather than an
        accident (codex review of 298ed6d).
        """
        raw = split.board_net_table('(kicad_pcb\n (net 1 "/A\\"B")\n)\n')
        self.assertEqual(raw, ['/A\\"B'])
        self.assertNotEqual(raw, ['/A"B'])

    def test_net_shaped_text_under_a_plausible_root_cannot_authorise(self):
        """The sharper case: input that DOES look like a board.

        The sibling test uses a file with no `(kicad_pcb` prefix at all, which
        the cheapest possible check already rejects. These do carry it, or
        nearly: a near-miss root symbol, and a net expression nested in a form
        that cannot carry a net (codex review of 298ed6d, which found both
        accepted after the KiCad 10 fix relaxed the depth rule).
        """
        for text in ('(kicad_pcbx (net "/PHANTOM"))\n',
                     '(kicad_pcb (metadata (net "/PHANTOM")))\n'):
            with self.subTest(text=text[:24]):
                try:
                    self.assertEqual(split.board_net_table(text), [])
                except split.ConnectivityError:
                    pass  # refusing outright is also correct

    def test_a_garbage_file_cannot_authorise_a_pour_declaration(self):
        """KNOWN-BAD CALIBRATION: a comment-only 'inventory' passed the gate."""
        with tempfile.TemporaryDirectory() as raw:
            board = pathlib.Path(raw) / "fixture.kicad_pcb"
            board.write_text('# not a board (net 1 "/GND")\n', encoding="utf-8")
            drc = pathlib.Path(raw) / "drc.json"
            drc.write_text(
                json.dumps(drc_report(
                    [record("Zone [/GND] on F.Cu", "Zone [/GND] on B.Cu")])),
                encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(drc), "--board", str(board),
                                "--trust-external-report",
                                   "--pour-net", "/GND",
                                   "--require-zero-signal-opens"])
        self.assertEqual(code, 2)
        self.assertIn("not a saved KiCad board", errors.getvalue())

    def test_an_oversized_board_is_refused_rather_than_read(self):
        with tempfile.TemporaryDirectory() as raw:
            board = pathlib.Path(raw) / "big.kicad_pcb"
            board.write_text("(kicad_pcb)\n", encoding="utf-8")
            with mock.patch.object(split, "MAX_BOARD_BYTES", 4):
                with self.assertRaisesRegex(split.ConnectivityError,
                                            "above the"):
                    split.board_identity(board)


class AVerdictMustBeBoundToTheBoard(unittest.TestCase):
    """P0 findings of the 2026-09-07 codex review of 298ed6d."""

    def test_a_clean_report_from_another_board_of_the_same_name_is_refused(self):
        """KNOWN-BAD CALIBRATION, the one that matters most.

        Measured before the fix: a board with 11 real opens exited 0 when
        handed a zero-item report from a DIFFERENT board that merely shared
        its filename and carried a later timestamp. Basename equality plus a
        timestamp ordering is not a binding.
        """
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory, "board.kicad_pcb")
            later = (datetime.datetime.now()
                     + datetime.timedelta(minutes=5)).replace(
                         microsecond=0).isoformat()
            clean = directory / "clean.json"
            clean.write_text(
                json.dumps(drc_report([], source="board.kicad_pcb",
                                      date=later)),
                encoding="utf-8")
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main([str(clean), "--board", str(board),
                                   "--no-pour-nets",
                                   "--require-zero-signal-opens"])
        self.assertEqual(code, 2, "an unowned report gated silently")
        self.assertIn("--run-drc", errors.getvalue())

    def test_trusting_an_external_report_is_explicit_and_recorded(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory)
            drc = directory / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            out = directory / "split.json"
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main([str(drc), "--board", str(board),
                                   "--trust-external-report", "--no-pour-nets",
                                   "--require-zero-signal-opens",
                                   "--json", str(out)])
            self.assertEqual(code, 0)
            written = json.loads(out.read_text(encoding="utf-8"))
        provenance = written["drc_provenance"]
        self.assertFalse(provenance["produced_by_this_tool"])
        self.assertTrue(provenance["trusted_external_report"])

    @unittest.skipUnless(KICAD_CLI is not None, "kicad-cli not found")
    def test_run_drc_grades_the_board_it_digested(self):
        """The owned path: the report cannot be about another board, because
        this tool made it from the bytes it hashed."""
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = directory / "open-net.kicad_pcb"
            board.write_bytes((FIXTURES / "open-net.kicad_pcb").read_bytes())
            out = directory / "split.json"
            errors = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(errors):
                code = split.main(["--run-drc", "--board", str(board),
                                   "--no-pour-nets",
                                   "--require-zero-signal-opens",
                                   "--json", str(out)])
            if code == 2 and "kicad-cli" in errors.getvalue():
                self.skipTest("kicad-cli could not run here: %s"
                              % errors.getvalue().strip()[:120])
            self.assertEqual(code, 4, "the board's two real opens must fail")
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertTrue(written["drc_provenance"]["produced_by_this_tool"])
        self.assertEqual(written["counts"]["signal_open_records"], 2)

    def test_run_drc_and_an_explicit_report_cannot_be_combined(self):
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory)
            drc = directory / "drc.json"
            drc.write_text(json.dumps(drc_report([])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main([str(drc), "--run-drc", "--board",
                                   str(board), "--no-pour-nets",
                                   "--require-zero-signal-opens"])
        self.assertEqual(code, 2)


class AnAcknowledgementIsNotEvidence(unittest.TestCase):
    """The regression I introduced in 9551cd4's predecessor, and its fix."""

    def _run(self, descriptions, extra):
        row = record(*descriptions)
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory)
            drc = directory / "drc.json"
            drc.write_text(json.dumps(drc_report([row])), encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main(
                    [str(drc), "--board", str(board),
                     "--trust-external-report",
                     "--require-zero-signal-opens"] + extra)
        return code, split.record_id(row)

    def test_two_caller_labels_cannot_launder_a_zoneless_open(self):
        """KNOWN-BAD CALIBRATION.

        Measured on a plain `Pad 1 [/SIG] <-> Track [/SIG]` record, no zone
        anywhere:
            no declaration, no acknowledgement -> 4
            declaration only                   -> 3
            acknowledgement only               -> 4
            declaration + acknowledgement      -> 0   <-- a REAL OPEN

        I had called those "two independent statements". They are two
        statements, but both come from the caller, so neither is evidence --
        and stacking two labels is exactly what the all-zone rule exists to
        stop.
        """
        descriptions = ("Pad 1 [/SIG] of R1 on F.Cu", "Track [/SIG] on F.Cu")
        _, ident = self._run(descriptions, ["--no-pour-nets"])
        code, _ = self._run(descriptions,
                            ["--pour-net", "/SIG",
                             "--reviewed-record", ident])
        self.assertEqual(code, 3, "a zoneless real open was acknowledged away")

    def test_an_acknowledgement_can_carry_its_reason(self):
        row = record("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")
        ident = split.record_id(row)
        with tempfile.TemporaryDirectory() as raw:
            directory = pathlib.Path(raw)
            board = write_board(directory)
            drc = directory / "drc.json"
            drc.write_text(json.dumps(drc_report([row])), encoding="utf-8")
            out = directory / "split.json"
            with contextlib.redirect_stdout(io.StringIO()), \
                    contextlib.redirect_stderr(io.StringIO()):
                code = split.main(
                    [str(drc), "--board", str(board),
                     "--trust-external-report", "--pour-net", "/GND",
                     "--require-zero-signal-opens", "--json", str(out),
                     "--reviewed-record",
                     "%s=thermal relief stub, reviewed on the plot" % ident])
            self.assertEqual(code, 0)
            written = json.loads(out.read_text(encoding="utf-8"))
        self.assertEqual(written["reviewed_records"][ident],
                         "thermal relief stub, reviewed on the plot")

    def test_an_acknowledgement_still_works_on_a_mixed_zone_record(self):
        """Its actual purpose: the zone-track records real pours produce."""
        descriptions = ("Zone [/GND] on F.Cu", "Track [/GND] on F.Cu")
        _, ident = self._run(descriptions, ["--no-pour-nets"])
        code, _ = self._run(descriptions,
                            ["--pour-net", "/GND",
                             "--reviewed-record", ident])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
