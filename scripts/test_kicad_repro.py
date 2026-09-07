#!/usr/bin/env python3
"""Calibration for kicad_repro, with the weight on `check_frozen`.

`run_and_check_reproducible` shipped without a test file; the cases below cover
its stated refusals too, because a module that is now imported by a release gate
should not have its oldest half uncalibrated.

The cases are named as the CLAIM the docstrings make, per GUARDS ("Use the
guard's own behavioural prose as a test inventory"). The negative claims come
first: a generator that behaves correctly never visits the branches that make
this module worth having.
"""

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicad_repro as R  # noqa: E402

FAST = dict(settle=0.0, tries=1, spacing=0.01)


def _gen(script_path, body):
    """A generator command: a python script that rewrites its outputs."""
    Path(script_path).write_text(body, encoding="utf-8")
    return [sys.executable, str(script_path)]


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        self.out = self.d / "board.kicad_pcb"
        self.out.write_text("(kicad_pcb A)\n", encoding="utf-8")

    def tearDown(self):
        self._td.cleanup()

    def gen(self, body):
        return _gen(self.d / "gen.py", body)

    def stable_gen(self, text="(kicad_pcb A)\n"):
        return self.gen(
            "import pathlib\n"
            "pathlib.Path(%r).write_text(%r)\n" % (str(self.out), text))


# ------------------------------------------------------------ check_frozen ---

class TestFrozenBaseline(_Tmp):
    def test_a_generator_that_reproduces_the_tracked_bytes_passes(self):
        res = R.check_frozen(self.stable_gen(), [self.out], **FAST)
        self.assertEqual(list(res), [str(self.out)])
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")

    def test_the_pass_leaves_no_scratch_files_behind(self):
        R.check_frozen(self.stable_gen(), [self.out], **FAST)
        self.assertEqual(sorted(p.name for p in self.d.iterdir()),
                         ["board.kicad_pcb", "gen.py"])


class TestFrozenKnownBad(_Tmp):
    def test_a_changed_artefact_raises_FrozenError(self):
        with self.assertRaises(R.FrozenError) as cm:
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           **FAST)
        self.assertIn("FROZEN", str(cm.exception))

    def test_the_tracked_bytes_are_restored_after_a_failure(self):
        with self.assertRaises(R.FrozenError):
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           **FAST)
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")

    def test_the_regenerated_bytes_are_preserved_for_inspection(self):
        with self.assertRaises(R.FrozenError):
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           **FAST)
        regen = self.d / R._keep_name(self.out, "regenerated")
        self.assertTrue(regen.exists())
        self.assertEqual(regen.read_text(), "(kicad_pcb B)\n")

    def test_the_message_names_both_paths_and_the_first_difference(self):
        with self.assertRaises(R.FrozenError) as cm:
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           **FAST)
        m = str(cm.exception)
        self.assertIn(R._keep_name(self.out, "tracked"), m)
        self.assertIn(R._keep_name(self.out, "regenerated"), m)
        self.assertIn("first difference at byte 11", m)   # the A/B position
        self.assertIn("line 1", m)

    def test_a_one_byte_change_is_caught_no_tolerance_exists(self):
        # The whole point of not inheriting atopile's float_precision=2.
        self.out.write_text("(at 10.000000 0)\n", encoding="utf-8")
        with self.assertRaises(R.FrozenError):
            R.check_frozen(self.stable_gen("(at 10.000001 0)\n"), [self.out],
                           **FAST)

    def test_restoring_the_generator_restores_the_baseline(self):
        with self.assertRaises(R.FrozenError):
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           **FAST)
        (self.d / R._keep_name(self.out, "regenerated")).unlink()
        R.check_frozen(self.stable_gen(), [self.out], **FAST)


class TestFrozenNeverPassesOnAGeneratorThatDidNotRun(_Tmp):
    """The founding lesson of this module, restated for the one-run case."""

    def test_a_generator_that_exits_nonzero_is_a_refusal_not_a_pass(self):
        cmd = self.gen("import sys\nsys.exit(7)\n")
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], **FAST)
        self.assertNotIsInstance(cm.exception, R.FrozenError)
        self.assertIn("exited 7", str(cm.exception))
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")

    def test_a_generator_that_silently_does_nothing_is_a_refusal(self):
        # Exits 0, writes nothing. Under the default recreate witness the
        # output is gone, so there is nothing to mistake for a pass.
        cmd = self.gen("pass\n")
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], **FAST)
        self.assertNotIsInstance(cm.exception, R.FrozenError)
        self.assertIn("is gone", str(cm.exception))
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")

    def test_a_generator_that_only_touches_the_output_is_a_refusal(self):
        """mtime movement is not proof of generation: `os.utime` alone exited
        0 and PASSED under the old witness (codex review of 0f709ed), which
        contradicts SKILL.md's own rule against mtime-only proof."""
        cmd = self.gen("import os\ntry:\n os.utime(%r, None)\nexcept OSError:\n"
                       " pass\n" % str(self.out))
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], **FAST)
        self.assertNotIsInstance(cm.exception, R.FrozenError)
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")

    def test_the_mtime_witness_remains_available_and_says_what_it_is(self):
        """The documented escape for an in-place generator. It only requires
        the mtime to move, so it does NOT witness generation."""
        cmd = self.gen("pass\n")
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], witness="mtime", **FAST)
        self.assertIn("mtime did not move", str(cm.exception))
        with self.assertRaises(R.ReproError):
            R.check_frozen(cmd, [self.out], witness="nonsense", **FAST)

    def test_a_generator_that_deletes_the_output_is_a_refusal(self):
        # Tolerant of the recreate witness having already removed it: the
        # case under test is "the generator left no output", either way.
        cmd = self.gen("import os\ntry:\n os.remove(%r)\nexcept OSError:\n"
                       " pass\n" % str(self.out))
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], **FAST)
        self.assertIn("is gone", str(cm.exception))

    def test_a_generator_that_truncates_the_output_is_a_refusal(self):
        cmd = self.gen("open(%r,'w').close()\n" % str(self.out))
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(cmd, [self.out], **FAST)
        self.assertIn("zero bytes", str(cm.exception))


class TestFrozenPreconditions(_Tmp):
    def test_no_outputs_is_a_refusal_not_an_empty_pass(self):
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(self.stable_gen(), [], **FAST)
        self.assertIn("reads as a pass", str(cm.exception))

    def test_an_absent_artefact_is_not_a_frozen_check(self):
        missing = self.d / "nope.kicad_pcb"
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(self.stable_gen(), [missing], **FAST)
        self.assertIn("no committed artefact", str(cm.exception))

    def test_a_zero_byte_artefact_is_refused(self):
        z = self.d / "z.kicad_pcb"
        z.touch()
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(self.stable_gen(), [z], **FAST)
        self.assertIn("zero bytes", str(cm.exception))

    def test_duplicate_outputs_are_refused(self):
        with self.assertRaises(R.ReproError) as cm:
            R.check_frozen(self.stable_gen(), [self.out, self.out], **FAST)
        self.assertIn("duplicate", str(cm.exception))

    def test_the_same_file_named_two_ways_is_still_a_duplicate(self):
        alias = self.d / "." / "board.kicad_pcb"
        with self.assertRaises(R.ReproError):
            R.check_frozen(self.stable_gen(), [self.out, alias], **FAST)

    def test_a_moving_artefact_is_refused_before_anything_runs(self):
        # stable_digest must fire first: a check against a file another
        # process is writing is attributed to a state that no longer exists.
        real = R.digest

        calls = {"n": 0}

        def flapping(path, algo="sha256"):
            calls["n"] += 1
            return "%040x" % calls["n"]

        R.digest = flapping
        try:
            with self.assertRaises(R.ReproError) as cm:
                R.check_frozen(self.stable_gen(), [self.out],
                               settle=0.0, tries=2, spacing=0.01)
            self.assertIn("still changing", str(cm.exception))
        finally:
            R.digest = real


class TestFrozenMultipleOutputs(_Tmp):
    def test_one_changed_output_of_several_fails_and_all_are_restored(self):
        b = self.d / "second.kicad_sch"
        b.write_text("(kicad_sch X)\n", encoding="utf-8")
        cmd = self.gen(
            "import pathlib\n"
            "pathlib.Path(%r).write_text('(kicad_pcb A)\\n')\n"
            "pathlib.Path(%r).write_text('(kicad_sch Y)\\n')\n"
            % (str(self.out), str(b)))
        with self.assertRaises(R.FrozenError) as cm:
            R.check_frozen(cmd, [self.out, b], **FAST)
        self.assertIn("1 of 2 artefact(s) changed", str(cm.exception))
        self.assertEqual(self.out.read_text(), "(kicad_pcb A)\n")
        self.assertEqual(b.read_text(), "(kicad_sch X)\n")

    def test_the_changed_count_is_derived_not_literal(self):
        b = self.d / "second.kicad_sch"
        b.write_text("(kicad_sch X)\n", encoding="utf-8")
        cmd = self.gen(
            "import pathlib\n"
            "pathlib.Path(%r).write_text('(kicad_pcb B)\\n')\n"
            "pathlib.Path(%r).write_text('(kicad_sch Y)\\n')\n"
            % (str(self.out), str(b)))
        with self.assertRaises(R.FrozenError) as cm:
            R.check_frozen(cmd, [self.out, b], **FAST)
        self.assertIn("2 of 2 artefact(s) changed", str(cm.exception))


class TestFrozenKeepDir(_Tmp):
    def test_keep_dir_puts_the_evidence_outside_the_tracked_tree(self):
        scratch = self.d / "scratch"
        with self.assertRaises(R.FrozenError):
            R.check_frozen(self.stable_gen("(kicad_pcb B)\n"), [self.out],
                           keep_dir=scratch, **FAST)
        self.assertTrue(
            (scratch / R._keep_name(self.out, "regenerated")).exists())
        self.assertEqual(sorted(p.name for p in self.d.iterdir()),
                         ["board.kicad_pcb", "gen.py", "scratch"])


class TestFirstDifference(unittest.TestCase):
    def test_it_reports_the_offset_and_line_of_the_first_differing_byte(self):
        a = b"line1\nline2\nabc\n"
        b = b"line1\nline2\nabd\n"
        off, line, _, _ = R._first_difference(a, b)
        self.assertEqual((off, line), (14, 3))

    def test_a_pure_truncation_differs_at_the_shorter_length(self):
        off, line, _, _ = R._first_difference(b"abcdef", b"abc")
        self.assertEqual(off, 3)


# ------------------------------------------- run_and_check_reproducible ---

class TestReproducibleWasUncalibrated(_Tmp):
    """Cases for the older half of the module, which shipped without any."""

    def test_a_deterministic_generator_passes(self):
        res = R.run_and_check_reproducible(self.stable_gen(), [self.out])
        self.assertEqual(list(res), [str(self.out)])

    def test_a_nondeterministic_generator_fails_as_nondeterminism(self):
        cmd = self.gen(
            "import pathlib, time\n"
            "pathlib.Path(%r).write_text('(kicad_pcb %%s)\\n' %% time.time_ns())\n"
            % str(self.out))
        with self.assertRaises(R.ReproError) as cm:
            R.run_and_check_reproducible(cmd, [self.out])
        self.assertIn("NOT reproducible", str(cm.exception))

    def test_a_generator_that_never_runs_is_not_reported_as_reproducible(self):
        cmd = self.gen("pass\n")
        with self.assertRaises(R.ReproError) as cm:
            R.run_and_check_reproducible(cmd, [self.out])
        self.assertIn("mtime did not move", str(cm.exception))

    def test_no_outputs_is_refused(self):
        with self.assertRaises(R.ReproError):
            R.run_and_check_reproducible(self.stable_gen(), [])


class TestDigestIdentity(_Tmp):
    def test_verify_unchanged_since_catches_a_rewrite(self):
        d0 = R.digest(self.out)
        self.assertEqual(R.verify_unchanged_since(self.out, d0), d0)
        self.out.write_text("(kicad_pcb B)\n", encoding="utf-8")
        with self.assertRaises(R.ReproError):
            R.verify_unchanged_since(self.out, d0)


if __name__ == "__main__":
    unittest.main()
