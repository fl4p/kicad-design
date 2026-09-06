"""CLI contract tests for copper_guards.py.

The tool's physics is calibrated in its docstring against real boards; these
tests cover only the executable boundary, where the reviewed defects were:
a gate that could be omitted, a parameter that could be NaN, and a usage
error indistinguishable from a failed gate.

Exit contract: 0 PASS, 1 fail-closed/unevaluable, 2 the gate FAILED.
"""

import os
import subprocess
import sys
import tempfile
import unittest

# Deliberately NOT `import copper_guards`: that module needs numpy and
# shapely, and importing it here made the whole suite uncollectable on a
# stdlib interpreter (codex review of 7b00165 -- 269 tests, 1 error). These
# tests drive the CLI as a subprocess, so the path is all they need, and the
# CLI boundary itself is required to work without the measurement stack.
GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                     "copper_guards.py")


def run(argv, board=None):
    with tempfile.TemporaryDirectory() as directory:
        path = board or os.path.join(directory, "b.kicad_pcb")
        if board is None:
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("(kicad_pcb (version 20240108))\n")
        return subprocess.run(
            [sys.executable, GUARD] + [a.replace("BOARD", path) for a in argv],
            capture_output=True, text=True)


class UsageErrorsAreUnevaluable(unittest.TestCase):
    """argparse exits 2 by default, which collides with this guard's FAIL=2:
    a missing flag in a CI wrapper would read as a board that failed its
    resistance budget. loop_inductance_guard.py fixed the same collision."""

    CASES = (
        [],
        ["nosuchcmd"],
        ["vias", "--bogus"],
        ["resistance", "BOARD", "--net", "/A", "--pair", "a.1", "b.1",
         "--oz", "2"],                       # no --max-mohm
        ["resistance", "BOARD", "--net", "/A", "--pair", "a.1", "b.1",
         "--max-mohm", "10"],                # no --oz
        ["resistance", "BOARD", "--net", "/A", "--pair", "a.1", "b.1",
         "--oz", "2", "--max-mohm", "abc"],  # not a float
    )

    def test_usage_errors_exit_one(self):
        for argv in self.CASES:
            with self.subTest(argv=argv):
                proc = run(argv)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("FAIL-CLOSED", proc.stderr)


class OutOfDomainParametersNeverProduceAVerdict(unittest.TestCase):
    """A NaN or out-of-domain parameter is a configuration error. Each of
    these returned exit 0 before the 2026-09-04 rework."""

    CASES = (
        ["--max-mohm", "nan"], ["--max-mohm", "inf"],
        ["--max-mohm", "0"], ["--max-mohm", "-5"],
        ["--oz", "-2"], ["--oz", "nan"], ["--oz", "0"],
    )

    def test_domain_violations_fail_closed(self):
        for override in self.CASES:
            argv = ["resistance", "BOARD", "--net", "/A",
                    "--pair", "a.1", "b.1", "--oz", "2", "--max-mohm", "10"]
            argv[argv.index(override[0])+ 1] = override[1]
            with self.subTest(override=override):
                proc = run(argv)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("FAIL-CLOSED", proc.stderr)

    def test_via_thresholds_are_bounded(self):
        for argv in (["vias", "BOARD", "--min-zone-frac", "0"],
                     ["vias", "BOARD", "--min-zone-frac", "2"],
                     ["vias", "BOARD", "--min-contact-mm", "nan"]):
            with self.subTest(argv=argv):
                proc = run(argv)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("FAIL-CLOSED", proc.stderr)


class AnUnusableBoardIsNeverAPass(unittest.TestCase):
    def test_board_with_no_copper_is_unevaluable(self):
        """Zero gradeable subjects is not a clean board. An empty file and a
        misspelled --net both exited 0 before the rework."""
        for argv in (["vias", "BOARD"],
                     ["vias", "BOARD", "--net", "/NOSUCH"],
                     ["resistance", "BOARD", "--net", "/A", "--pair",
                      "a.1", "b.1", "--oz", "2", "--max-mohm", "10"]):
            with self.subTest(argv=argv):
                proc = run(argv)
                self.assertEqual(proc.returncode, 1, proc.stderr)
                self.assertIn("FAIL-CLOSED", proc.stderr)

    def test_missing_board_file_is_unevaluable(self):
        proc = run(["vias", "/nonexistent/board.kicad_pcb"])
        self.assertEqual(proc.returncode, 1, proc.stderr)


if __name__ == "__main__":
    unittest.main()
