#!/usr/bin/env python3
"""Calibration for kicad_open_probe.

Every negative claim the docstring makes gets a case here, named as the CLAIM
(GUARDS, "Use the guard's own behavioural prose as a test inventory"). The
important direction is that nothing reaches NOT-HELD without having positively
read the directory -- ordinary use on a clean tree visits only the happy path,
so it proves nothing about that.
"""

import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import kicad_open_probe as K  # noqa: E402


class _Tmp(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.d = Path(self._td.name)
        # Point the socket probe at an empty scratch dir so the host's real
        # /tmp/kicad cannot decide any verdict here.
        self.sockdir = self.d / "sockets"
        self.sockdir.mkdir()
        self._orig = K.ipc_socket_dir
        K.ipc_socket_dir = lambda: self.sockdir

    def tearDown(self):
        K.ipc_socket_dir = self._orig
        self._td.cleanup()

    def board(self, name="b.kicad_pcb"):
        p = self.d / name
        p.write_text("(kicad_pcb)\n", encoding="utf-8")
        return p

    def lock(self, board, content='{"hostname":"h","username":"u"}'):
        lp = K.lock_path_for(board)
        lp.write_text(content, encoding="utf-8")
        return lp


class TestLockNaming(_Tmp):
    def test_lock_name_is_tilde_basename_dot_lck(self):
        # Verified against a real KiCad 10.0.5 lock on this machine:
        #   /Users/.../o2-probe/~o2-probe.kicad_pcb.lck
        got = K.lock_path_for(Path("/x/y/o2-probe.kicad_pcb"))
        self.assertEqual(got, Path("/x/y/~o2-probe.kicad_pcb.lck"))

    def test_a_path_with_no_basename_is_unverified_not_free(self):
        state, _ = K.kicad_holding(Path("/"))
        self.assertEqual(state, K.UNKNOWN)


class TestBaselineAndKnownBad(_Tmp):
    def test_baseline_clean_tree_is_not_held(self):
        state, detail = K.kicad_holding(self.board())
        self.assertEqual(state, K.NOT_HELD, detail)

    def test_known_bad_a_lock_file_makes_it_held(self):
        b = self.board()
        self.lock(b)
        state, detail = K.kicad_holding(b)
        self.assertEqual(state, K.HELD)
        self.assertIn("u@h", detail)

    def test_restoring_the_subject_restores_the_baseline(self):
        b = self.board()
        lp = self.lock(b)
        self.assertEqual(K.kicad_holding(b)[0], K.HELD)
        lp.unlink()
        self.assertEqual(K.kicad_holding(b)[0], K.NOT_HELD)

    def test_a_legal_neighbouring_lock_stays_quiet(self):
        # Another board in the same directory being open must not condemn
        # this one: the signal is per-path, and a guard that condemns
        # everything is as wrong as one that passes everything.
        b = self.board("mine.kicad_pcb")
        other = self.board("theirs.kicad_pcb")
        self.lock(other)
        self.assertEqual(K.kicad_holding(b)[0], K.NOT_HELD)
        self.assertEqual(K.kicad_holding(other)[0], K.HELD)


class TestHeldNeverImpliesLive(_Tmp):
    def test_a_lock_naming_a_foreign_host_is_still_held(self):
        # A stale lock from another host is byte-indistinguishable from a
        # live one; the docstring says so, so HELD must not be softened.
        b = self.board()
        self.lock(b, '{"hostname":"some-other-box","username":"nobody"}')
        state, detail = K.kicad_holding(b)
        self.assertEqual(state, K.HELD)
        self.assertIn("NO pid", detail)

    def test_an_unparseable_lock_is_held_not_unknown(self):
        b = self.board()
        self.lock(b, "not json at all")
        self.assertEqual(K.kicad_holding(b)[0], K.HELD)

    def test_an_empty_lock_file_is_held(self):
        b = self.board()
        self.lock(b, "")
        self.assertEqual(K.kicad_holding(b)[0], K.HELD)


class TestUnevaluableIsNeverAPass(_Tmp):
    def test_an_unreadable_parent_directory_is_unknown(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores the read bit")
        sub = self.d / "walled"
        sub.mkdir()
        b = sub / "b.kicad_pcb"
        b.write_text("(kicad_pcb)\n", encoding="utf-8")
        os.chmod(sub, 0)
        try:
            state, detail = K.kicad_holding(b)
            self.assertEqual(state, K.UNKNOWN, detail)
            self.assertIn("not an observation", detail)
        finally:
            os.chmod(sub, stat.S_IRWXU)

    def test_a_missing_directory_is_unknown_not_free(self):
        state, detail = K.kicad_holding(self.d / "nope" / "b.kicad_pcb")
        self.assertEqual(state, K.UNKNOWN, detail)

    def test_a_live_ipc_socket_downgrades_a_clean_tree_to_unknown(self):
        b = self.board()
        self.assertEqual(K.kicad_holding(b)[0], K.NOT_HELD)
        (self.sockdir / "api.sock").write_bytes(b"")
        state, detail = K.kicad_holding(b)
        self.assertEqual(state, K.UNKNOWN)
        self.assertIn("kipy", detail)

    def test_a_non_api_file_in_the_socket_dir_is_not_a_socket(self):
        # The glob is `api*.sock`; a stray file must not manufacture UNKNOWN,
        # or the probe condemns every board on the machine forever.
        b = self.board()
        (self.sockdir / "README").write_bytes(b"")
        self.assertEqual(K.kicad_holding(b)[0], K.NOT_HELD)

    def test_a_missing_socket_dir_is_a_real_observation(self):
        # KiCad creates /tmp/kicad only when the API server starts, so its
        # absence is evidence, not an unreadable subject.
        K.ipc_socket_dir = lambda: self.d / "no-such-dir"
        self.assertEqual(K.kicad_holding(self.board())[0], K.NOT_HELD)

    def test_an_unlistable_socket_dir_is_unknown(self):
        if os.geteuid() == 0:
            self.skipTest("root ignores the read bit")
        os.chmod(self.sockdir, 0)
        try:
            state, detail = K.kicad_holding(self.board())
            self.assertEqual(state, K.UNKNOWN, detail)
            self.assertIn("could not be listed", detail)
        finally:
            os.chmod(self.sockdir, stat.S_IRWXU)


class TestMonotonicity(_Tmp):
    def test_adding_evidence_never_moves_the_verdict_toward_free(self):
        b = self.board()
        order = [K.NOT_HELD, K.UNKNOWN, K.HELD]
        seen = [K.kicad_holding(b)[0]]
        (self.sockdir / "api.sock").write_bytes(b"")     # +socket
        seen.append(K.kicad_holding(b)[0])
        self.lock(b)                                      # +lock
        seen.append(K.kicad_holding(b)[0])
        idx = [order.index(s) for s in seen]
        self.assertEqual(idx, sorted(idx), seen)
        self.assertEqual(seen[-1], K.HELD)


class TestNothingIsWritten(_Tmp):
    def test_the_probe_creates_no_files_anywhere_it_looks(self):
        # atopile's equivalent turns the KiCad API server on by editing
        # kicad_common.json. This probe must never modify its subject.
        b = self.board()
        before = sorted(p.relative_to(self.d).as_posix()
                        for p in self.d.rglob("*"))
        K.kicad_holding(b)
        after = sorted(p.relative_to(self.d).as_posix()
                       for p in self.d.rglob("*"))
        self.assertEqual(before, after)


class TestCli(_Tmp):
    def _run(self, argv):
        import io
        import contextlib
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            rc = K.main(["kicad_open_probe.py"] + argv)
        return rc, buf.getvalue()

    def test_exit_codes_are_zero_one_two(self):
        b = self.board()
        rc, out = self._run([str(b)])
        self.assertEqual(rc, 0, out)
        self.assertIn("KICAD-OPEN-PASS", out)
        self.lock(b)
        rc, out = self._run([str(b)])
        self.assertEqual(rc, 1, out)
        self.assertIn("KICAD-OPEN-HELD", out)

    def test_the_worst_state_across_paths_decides_the_exit(self):
        a = self.board("a.kicad_pcb")
        b = self.board("b.kicad_pcb")
        self.lock(b)
        rc, out = self._run([str(a), str(b)])
        self.assertEqual(rc, 1, out)

    def test_the_reported_path_count_is_derived(self):
        a = self.board("a.kicad_pcb")
        b = self.board("b.kicad_pcb")
        _, out = self._run([str(a), str(b)])
        self.assertIn("2 path(s) probed", out)

    def test_duplicate_paths_refuse(self):
        b = self.board()
        rc, out = self._run([str(b), str(b)])
        self.assertEqual(rc, 2, out)

    def test_json_rows_carry_the_state(self):
        b = self.board()
        self.lock(b)
        rc, out = self._run([str(b), "--json"])
        rows = json.loads(out[:out.rindex("]") + 1])
        self.assertEqual(rows[0]["state"], K.HELD)
        self.assertEqual(rc, 1)

    def test_a_bad_invocation_is_unevaluable_not_a_pass(self):
        with self.assertRaises(SystemExit) as cm:
            K.main(["kicad_open_probe.py"])
        self.assertIn("UNVERIFIED", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
