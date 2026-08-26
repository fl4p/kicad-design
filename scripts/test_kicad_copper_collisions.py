#!/usr/bin/env python3
"""Tests for the certain-short copper audit (pcbnew mocked).

Calibration against a real known-bad board (hundreds of DRC-confirmed shorts,
295 collisions) and a real known-good board (1761 items, 0 collisions) was
performed with the bundled interpreter on KiCad 10.0.5; these tests pin the
pure logic: pair enumeration, net/layer filtering, the shape-derived bbox
prefilter and its touch margin, the exact touch clearance, the NPTH flashing
policy, fail-closed exit mapping, the JSON verdict lifecycle including stale-
report invalidation, and the launcher's probe/loop/timeout/verdict defenses.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_copper_collisions as guard

F_CU, B_CU = 0, 2


class FakeBox:
    def __init__(self, left, top, right, bottom):
        self._l, self._t, self._r, self._b = left, top, right, bottom

    def GetLeft(self):
        return self._l

    def GetRight(self):
        return self._r

    def GetTop(self):
        return self._t

    def GetBottom(self):
        return self._b


COLLIDING = set()
COLLIDE_CALLS = []


class FakeShape:
    def __init__(self, owner, box):
        self.owner = owner
        self._box = box

    def BBox(self):
        return self._box

    def Collide(self, other, clearance):
        COLLIDE_CALLS.append((self.owner, other.owner, clearance))
        return (self.owner, other.owner) in COLLIDING or (
            other.owner,
            self.owner,
        ) in COLLIDING


class FakePos:
    x, y = 1_000_000, 2_000_000


class FakeItem:
    def __init__(
        self,
        name,
        net,
        layers,
        box,
        cls="PCB_TRACK",
        netname=None,
        attribute="pth",
        flashed_layers=None,
    ):
        self.name = name
        self._net = net
        self._layers = layers
        self._box = box
        self._cls = cls
        self._netname = netname or f"net{net}"
        self._attribute = attribute
        self._flashed = layers if flashed_layers is None else flashed_layers

    def IsOnLayer(self, layer):
        return layer in self._layers

    def FlashLayer(self, layer):
        return layer in self._flashed

    def GetAttribute(self):
        return self._attribute

    def GetNetCode(self):
        return self._net

    def GetNetname(self):
        return self._netname

    def GetEffectiveShape(self, layer):
        return FakeShape(self.name, self._box)

    def GetClass(self):
        return self._cls

    def GetPosition(self):
        return FakePos()

    def GetParentFootprint(self):
        return None

    def GetNumber(self):
        return "1"


class FakeLayerSeq:
    def Seq(self):
        return [F_CU, B_CU, 99]  # 99 = non-copper, must be ignored


class FakeFootprint:
    def __init__(self, pads):
        self._pads = pads

    def Pads(self):
        return self._pads


class FakeBoard:
    def __init__(self, tracks=(), pads=()):
        self._tracks = list(tracks)
        self._pads = list(pads)

    def GetEnabledLayers(self):
        return FakeLayerSeq()

    def GetTracks(self):
        return self._tracks

    def GetFootprints(self):
        return [FakeFootprint(self._pads)] if self._pads else []

    def GetLayerName(self, layer):
        return {F_CU: "F.Cu", B_CU: "B.Cu"}[layer]


class FakePcbnew:
    PAD_ATTRIB_NPTH = "npth"

    @staticmethod
    def IsCopperLayer(layer):
        return layer in (F_CU, B_CU)

    @staticmethod
    def ToMM(value):
        return value / 1_000_000


BOX = FakeBox(0, 0, 10, 10)
FAR_BOX = FakeBox(100, 100, 110, 110)
# tangent to BOX: shares the x=10 edge. Measured on 10.0.5: tangent shapes
# have equal bbox edges and collide at clearance 1 — the inclusive prefilter
# comparison must admit this pair (a strict `<` would skip real tangency).
TANGENT_BOX = FakeBox(10, 0, 20, 10)


class AuditTests(unittest.TestCase):
    def setUp(self):
        COLLIDING.clear()
        COLLIDE_CALLS.clear()

    def test_different_nets_colliding_is_a_finding(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        COLLIDING.add(("a", "b"))
        findings, inventory = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["layer"], "F.Cu")
        self.assertEqual(findings[0]["nets"], ["net1", "net2"])
        self.assertEqual(inventory["items"], 2)

    def test_collide_uses_exactly_one_iu_touch_clearance(self):
        # Probed on 10.0.5: clearance 0 misses exact tangency, clearance 1
        # flags tangency and rejects a 1-IU gap. Any other value changes the
        # audit's meaning, so the exact constant is load-bearing.
        self.assertEqual(guard.TOUCH_CLEARANCE_IU, 1)
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(len(COLLIDE_CALLS), 1)
        self.assertEqual(COLLIDE_CALLS[0][2], 1)

    def test_same_net_never_collide_tested_or_reported(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 1, [F_CU], BOX)
        COLLIDING.add(("a", "b"))  # would fire if the net filter broke
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(findings, [])
        self.assertEqual(COLLIDE_CALLS, [])

    def test_bbox_prefilter_skips_disjoint_items(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], FAR_BOX)
        COLLIDING.add(("a", "b"))  # geometric lie the prefilter must not reach
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(findings, [])
        self.assertEqual(COLLIDE_CALLS, [])

    def test_bbox_prefilter_admits_tangent_box_pairs(self):
        # Tangent shapes (shared bbox edge) are real collisions at the touch
        # clearance; the inclusive prefilter comparison must let them reach
        # Collide. The margin adds 1-IU slack on top, as belt and braces.
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], TANGENT_BOX)
        COLLIDING.add(("a", "b"))
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(len(COLLIDE_CALLS), 1)
        self.assertEqual(len(findings), 1)

    def test_cross_layer_items_do_not_pair(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [B_CU], BOX)
        COLLIDING.add(("a", "b"))
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(findings, [])

    def test_through_items_report_on_each_shared_layer(self):
        a = FakeItem("a", 1, [F_CU, B_CU], BOX)
        b = FakeItem("b", 2, [F_CU, B_CU], BOX)
        COLLIDING.add(("a", "b"))
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual({f["layer"] for f in findings}, {"F.Cu", "B.Cu"})

    def test_pads_participate(self):
        track = FakeItem("t", 1, [F_CU], BOX)
        pad = FakeItem("p", 2, [F_CU], BOX, cls="PAD")
        COLLIDING.add(("t", "p"))
        findings, _ = guard.audit_board(FakeBoard([track], [pad]), FakePcbnew)
        self.assertEqual(len(findings), 1)

    def test_npth_pad_excluded_on_unflashed_layer(self):
        track = FakeItem("t", 1, [F_CU], BOX)
        npth = FakeItem(
            "p", 0, [F_CU], BOX, cls="PAD", attribute="npth", flashed_layers=[]
        )
        COLLIDING.add(("t", "p"))  # drill disk is a hole, not a short
        findings, inventory = guard.audit_board(
            FakeBoard([track], [npth]), FakePcbnew
        )
        self.assertEqual(findings, [])
        self.assertEqual(inventory["layers"]["F.Cu"], 1)

    def test_npth_pad_included_where_flashed(self):
        track = FakeItem("t", 1, [F_CU], BOX)
        npth = FakeItem(
            "p", 0, [F_CU], BOX, cls="PAD", attribute="npth",
            flashed_layers=[F_CU],
        )
        COLLIDING.add(("t", "p"))
        findings, _ = guard.audit_board(FakeBoard([track], [npth]), FakePcbnew)
        self.assertEqual(len(findings), 1)

    def test_unflashed_pth_pad_still_audited(self):
        # a plated barrel conducts even where the annulus is not flashed
        track = FakeItem("t", 1, [F_CU], BOX)
        pth = FakeItem("p", 2, [F_CU], BOX, cls="PAD", flashed_layers=[])
        COLLIDING.add(("t", "p"))
        findings, _ = guard.audit_board(FakeBoard([track], [pth]), FakePcbnew)
        self.assertEqual(len(findings), 1)


class ExitAndJsonTests(unittest.TestCase):
    """run_audit maps findings/empty/missing/exception paths to fail-closed
    exit codes and maintains the JSON verdict lifecycle."""

    def setUp(self):
        COLLIDING.clear()
        COLLIDE_CALLS.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.json_path = os.path.join(self._tmp.name, "audit.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, board, board_exists=True, tmp_path="x.kicad_pcb",
             audit=None, **kwargs):
        fake_pcbnew = mock.Mock()
        fake_pcbnew.LoadBoard.return_value = board
        real_audit = guard.audit_board
        audit = audit or (lambda b, p: real_audit(b, FakePcbnew))
        with (
            mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
            mock.patch.object(guard.os.path, "isfile", return_value=board_exists),
            mock.patch.object(guard, "_stat_board", return_value=(10, 1.0)),
            mock.patch.object(guard, "audit_board", side_effect=audit),
        ):
            return guard.run_audit(tmp_path, json_out=self.json_path, **kwargs)

    def _report(self):
        with open(self.json_path, encoding="utf-8") as fh:
            return json.load(fh)

    def test_missing_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(None, board_exists=False), 1)
        self.assertEqual(self._report()["verdict"], "unevaluable")

    def test_unloadable_board_is_unevaluable(self):
        self.assertEqual(self._run(None, board_exists=True), 1)
        self.assertEqual(self._report()["verdict"], "unevaluable")

    def test_empty_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(FakeBoard()), 1)
        self.assertEqual(self._report()["verdict"], "unevaluable")

    def test_audit_exception_is_unevaluable_and_overwrites_stale_clean(self):
        with open(self.json_path, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "clean", "findings": []}, fh)  # stale lie
        boom = mock.Mock(side_effect=RuntimeError("shape blew up"))
        self.assertEqual(self._run(FakeBoard([]), audit=boom), 1)
        self.assertEqual(self._report()["verdict"], "unevaluable")

    def test_stale_clean_report_replaced_by_placeholder_before_evaluation(self):
        with open(self.json_path, "w", encoding="utf-8") as fh:
            json.dump({"verdict": "clean", "findings": []}, fh)
        self.assertEqual(self._run(None, board_exists=False), 1)
        report = self._report()
        self.assertEqual(report["verdict"], "unevaluable")
        self.assertIn("board", report)

    def test_clean_board_exits_zero_with_board_field(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], FAR_BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 0)
        report = self._report()
        self.assertEqual(report["verdict"], "clean")
        self.assertTrue(report["board"].endswith("x.kicad_pcb"))

    def test_findings_exit_two(self):
        COLLIDING.add(("a", "b"))
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 2)
        self.assertEqual(self._report()["verdict"], "collisions")

    def test_negative_max_report_is_clamped(self):
        COLLIDING.add(("a", "b"))
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        self.assertEqual(self._run(FakeBoard([a, b]), max_report=-5), 2)


class LauncherTests(unittest.TestCase):
    def test_configured_interpreter_without_pcbnew_is_an_error(self):
        with (
            mock.patch.dict(guard.os.environ, {"KICAD_PYTHON": "/usr/bin/true"}),
            mock.patch.object(guard, "_interpreter_has_pcbnew", return_value=False),
        ):
            interpreter, error = guard._find_kicad_python()
        self.assertIsNone(interpreter)
        self.assertIn("KICAD_PYTHON", error)

    def test_worker_reexec_loop_is_broken(self):
        # a re-executed worker that still lacks pcbnew must fail, not respawn
        with (
            mock.patch.dict(
                guard.os.environ, {guard._WORKER_ENV: "1"}, clear=False
            ),
            mock.patch.dict(sys.modules, {"pcbnew": None}),
            mock.patch.object(guard.subprocess, "run") as spawned,
        ):
            rc = guard.main(["x.kicad_pcb"])
        self.assertEqual(rc, 1)
        spawned.assert_not_called()

    def test_probe_rejects_argument_echoing_executable(self):
        # /bin/echo prints the probe command (which contains the marker
        # pieces) and exits 0; exact-match stdout must reject it.
        def fake_run(cmd, **kwargs):
            return mock.Mock(returncode=0, stdout=" ".join(cmd[1:]))

        with mock.patch.object(guard.subprocess, "run", side_effect=fake_run):
            self.assertFalse(guard._interpreter_has_pcbnew("/bin/echo"))

    def test_probe_rejects_exit_zero_without_output(self):
        proc = mock.Mock(returncode=0, stdout="")  # /usr/bin/true
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertFalse(guard._interpreter_has_pcbnew("/usr/bin/true"))

    def test_probe_requires_exit_zero(self):
        proc = mock.Mock(returncode=1, stdout=guard._PROBE_MARKER)
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertFalse(guard._interpreter_has_pcbnew("/x/python3"))

    def test_probe_accepts_real_marker(self):
        proc = mock.Mock(returncode=0, stdout=guard._PROBE_MARKER)
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertTrue(guard._interpreter_has_pcbnew("/x/python3"))

    def _worker_args(self, json_out=None):
        return mock.Mock(
            board="x.kicad_pcb",
            max_report=guard.MAX_REPORT_DEFAULT,
            json_out=json_out,
            timeout=guard.WORKER_TIMEOUT_DEFAULT,
        )

    def test_worker_exit_zero_without_ok_line_is_unevaluable(self):
        proc = mock.Mock(returncode=0, stdout="something else\n", stderr="")
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 1)

    def test_worker_exit_zero_with_ok_line_passes_through(self):
        proc = mock.Mock(
            returncode=0, stdout=f"{guard._OK_LINE}: 0 collisions\n", stderr=""
        )
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 0)

    def test_worker_failure_codes_pass_through(self):
        proc = mock.Mock(returncode=2, stdout="COPPER-COLLISIONS-FAIL: ...\n",
                         stderr="")
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 2)

    def test_worker_ok_must_be_line_anchored_not_substring(self):
        proc = mock.Mock(
            returncode=0,
            stdout="NOT-COPPER-COLLISIONS-OK-AUDIT-SKIPPED\n",
            stderr="",
        )
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 1)

    def test_worker_nonzero_with_ok_line_stays_nonzero(self):
        # a crash after a real OK line must not read as success
        proc = mock.Mock(
            returncode=1, stdout=f"{guard._OK_LINE}: 0 collisions\n", stderr=""
        )
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 1)

    def test_worker_zero_requires_clean_json_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "unevaluable"}, fh)  # placeholder stood
            proc = mock.Mock(
                returncode=0, stdout=f"{guard._OK_LINE}: 0 collisions\n", stderr=""
            )
            with mock.patch.object(guard.subprocess, "run", return_value=proc):
                rc = guard._run_worker(
                    "/x/python3", self._worker_args(json_out=json_path)
                )
            self.assertEqual(rc, 1)
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "clean"}, fh)
            with mock.patch.object(guard.subprocess, "run", return_value=proc):
                rc = guard._run_worker(
                    "/x/python3", self._worker_args(json_out=json_path)
                )
            self.assertEqual(rc, 0)

    def test_worker_receives_configured_timeout(self):
        args = self._worker_args()
        args.timeout = 123
        proc = mock.Mock(
            returncode=0, stdout=f"{guard._OK_LINE}: 0 collisions\n", stderr=""
        )
        with mock.patch.object(
            guard.subprocess, "run", return_value=proc
        ) as spawned:
            guard._run_worker("/x/python3", args)
        self.assertEqual(spawned.call_args.kwargs["timeout"], 123)

    def test_worker_timeout_is_unevaluable(self):
        with mock.patch.object(
            guard.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(cmd="x", timeout=1),
        ):
            self.assertEqual(guard._run_worker("/x/python3", self._worker_args()), 1)

    def test_cli_negative_max_report_is_unevaluable_not_exit_two(self):
        # argparse's own error path exits 2, which the exit-code contract
        # reserves for collisions; the CLI must return 1 instead.
        rc = guard.main(["x.kicad_pcb", "--max-report", "-1"])
        self.assertEqual(rc, 1)

    def test_cli_nonpositive_timeout_is_unevaluable(self):
        rc = guard.main(["x.kicad_pcb", "--timeout", "0"])
        self.assertEqual(rc, 1)

    def test_launcher_failure_writes_unevaluable_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "clean", "findings": []}, fh)  # stale
            with (
                mock.patch.dict(sys.modules, {"pcbnew": None}),
                mock.patch.object(
                    guard, "_find_kicad_python", return_value=(None, "nope")
                ),
            ):
                rc = guard.main(["x.kicad_pcb", "--json", json_path])
            self.assertEqual(rc, 1)
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["verdict"], "unevaluable")


class ContractTests(unittest.TestCase):
    def test_parser_errors_exit_one_not_two(self):
        for argv in (
            ["x.kicad_pcb", "--max-report", "nope"],
            ["x.kicad_pcb", "--timeout", "nope"],
            ["x.kicad_pcb", "--unknown-option"],
        ):
            with self.assertRaises(SystemExit) as ctx:
                guard.main(argv)
            self.assertEqual(ctx.exception.code, 1, argv)

    def test_placeholder_written_before_pcbnew_import_attempt(self):
        # a broken native pcbnew can raise more than ImportError; the stale
        # artifact must already be invalidated when that happens
        import builtins

        real_import = builtins.__import__

        def imp(name, *a, **k):
            if name == "pcbnew":
                raise RuntimeError("native boom")
            return real_import(name, *a, **k)

        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "clean", "findings": []}, fh)  # stale
            with mock.patch.object(builtins, "__import__", side_effect=imp):
                rc = guard.main(["x.kicad_pcb", "--json", json_path])
            self.assertEqual(rc, 1)
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["verdict"], "unevaluable")

    def test_write_json_is_atomic_and_binds_input(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            board_path = os.path.join(tmp, "b.kicad_pcb")
            with open(board_path, "w") as fh:
                fh.write("(kicad_pcb)")
            with mock.patch.object(
                guard.os, "replace", wraps=os.replace
            ) as replaced:
                guard._write_json(json_path, board_path, "clean", [], {})
            replaced.assert_called_once()
            with open(json_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["verdict"], "clean")
            self.assertIn("pid", payload)
            self.assertIn("board_size", payload)
            leftovers = [f for f in os.listdir(tmp) if ".tmp." in f]
            self.assertEqual(leftovers, [])


class Round4Tests(unittest.TestCase):
    def test_bbox_overlaps_is_inclusive_at_zero_margin(self):
        # tangent boxes (shared edge) must overlap with margin 0 — pins the
        # inclusive `<=`; a strict `<` fails this without margin compensation
        a, b = FakeBox(0, 0, 10, 10), FakeBox(10, 0, 20, 10)
        self.assertTrue(guard._bbox_overlaps(a, b, 0))
        gap = FakeBox(11, 0, 20, 10)
        self.assertFalse(guard._bbox_overlaps(a, gap, 0))
        self.assertTrue(guard._bbox_overlaps(a, gap, 1))

    def test_board_replaced_during_audit_is_unevaluable(self):
        fake_pcbnew = mock.Mock()
        a = FakeItem("a", 1, [F_CU], BOX)
        fake_pcbnew.LoadBoard.return_value = FakeBoard([a])
        real_audit = guard.audit_board
        stats = iter([(10, 1.0), (20, 2.0)])  # snapshot, then post-audit
        with (
            mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
            mock.patch.object(guard.os.path, "isfile", return_value=True),
            mock.patch.object(guard, "_stat_board", side_effect=lambda p: next(stats)),
            mock.patch.object(
                guard, "audit_board", side_effect=lambda b, p: real_audit(b, FakePcbnew)
            ),
        ):
            rc = guard.run_audit("x.kicad_pcb")
        self.assertEqual(rc, 1)

    def test_clean_json_binds_preload_snapshot(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            fake_pcbnew = mock.Mock()
            a = FakeItem("a", 1, [F_CU], BOX)
            b = FakeItem("b", 2, [F_CU], FAR_BOX)
            fake_pcbnew.LoadBoard.return_value = FakeBoard([a, b])
            real_audit = guard.audit_board
            with (
                mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
                mock.patch.object(guard.os.path, "isfile", return_value=True),
                mock.patch.object(guard, "_stat_board", return_value=(10, 1.0)),
                mock.patch.object(
                    guard, "audit_board",
                    side_effect=lambda bd, p: real_audit(bd, FakePcbnew),
                ),
            ):
                rc = guard.run_audit("x.kicad_pcb", json_out=json_path)
            self.assertEqual(rc, 0)
            with open(json_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["board_size"], 10)
            self.assertEqual(payload["board_mtime"], 1.0)

    def test_parse_error_with_json_still_invalidates_stale_artifact(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "clean", "findings": []}, fh)  # stale
            with self.assertRaises(SystemExit) as ctx:
                guard.main(["x.kicad_pcb", "--json", json_path, "--unknown"])
            self.assertEqual(ctx.exception.code, 1)
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["verdict"], "unevaluable")

    def test_empty_json_path_is_rejected(self):
        self.assertEqual(guard.main(["x.kicad_pcb", "--json", ""]), 1)

    def test_json_path_aliasing_board_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, "b.kicad_pcb")
            with open(board, "w") as fh:
                fh.write("(kicad_pcb)")
            rc = guard.main([board, "--json", board])
            self.assertEqual(rc, 1)
            with open(board, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "(kicad_pcb)")  # board untouched

    def test_write_json_failure_cleans_temp_and_keeps_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                fh.write('{"verdict": "unevaluable"}')
            with mock.patch.object(
                guard.json, "dump", side_effect=OSError("disk full")
            ):
                with self.assertRaises(OSError):
                    guard._write_json(json_path, "b.kicad_pcb", "clean", [], {})
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["verdict"], "unevaluable")
            self.assertEqual(
                [f for f in os.listdir(tmp) if ".tmp." in f], []
            )

    def test_worker_unexpected_status_normalizes_to_unevaluable(self):
        proc = mock.Mock(returncode=-11, stdout="", stderr="")  # signal
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._args()), 1)
        proc = mock.Mock(returncode=247, stdout="", stderr="")
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertEqual(guard._run_worker("/x/python3", self._args()), 1)

    def test_worker_exit_two_requires_collisions_json_verdict(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "unevaluable"}, fh)
            proc = mock.Mock(returncode=2, stdout="COPPER-COLLISIONS-FAIL\n",
                             stderr="")
            with mock.patch.object(guard.subprocess, "run", return_value=proc):
                rc = guard._run_worker("/x/python3", self._args(json_out=json_path))
            self.assertEqual(rc, 1)
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "collisions"}, fh)
            with mock.patch.object(guard.subprocess, "run", return_value=proc):
                rc = guard._run_worker("/x/python3", self._args(json_out=json_path))
            self.assertEqual(rc, 2)

    def test_worker_capture_forces_utf8_with_replacement(self):
        proc = mock.Mock(
            returncode=0, stdout=f"{guard._OK_LINE}: 0 collisions\n", stderr=""
        )
        with mock.patch.object(
            guard.subprocess, "run", return_value=proc
        ) as spawned:
            guard._run_worker("/x/python3", self._args())
        kwargs = spawned.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    @staticmethod
    def _args(json_out=None):
        return mock.Mock(
            board="x.kicad_pcb",
            max_report=guard.MAX_REPORT_DEFAULT,
            json_out=json_out,
            timeout=guard.WORKER_TIMEOUT_DEFAULT,
        )


class Round5Tests(unittest.TestCase):
    def test_unstatable_board_is_unevaluable_even_if_isfile_lied(self):
        # fail closed: a None snapshot must never reach the audit, and
        # None == None on both sides must never mean "unchanged"
        fake_pcbnew = mock.Mock()
        with (
            mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
            mock.patch.object(guard.os.path, "isfile", return_value=True),
            mock.patch.object(guard, "_stat_board", return_value=None),
        ):
            rc = guard.run_audit("x.kicad_pcb")
        self.assertEqual(rc, 1)
        fake_pcbnew.LoadBoard.assert_not_called()

    def test_post_audit_stat_failure_is_unevaluable(self):
        fake_pcbnew = mock.Mock()
        a = FakeItem("a", 1, [F_CU], BOX)
        fake_pcbnew.LoadBoard.return_value = FakeBoard([a])
        real_audit = guard.audit_board
        stats = iter([(10, 1.0), None])  # snapshot ok, post-audit gone
        with (
            mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
            mock.patch.object(guard.os.path, "isfile", return_value=True),
            mock.patch.object(guard, "_stat_board", side_effect=lambda p: next(stats)),
            mock.patch.object(
                guard, "audit_board", side_effect=lambda b, p: real_audit(b, FakePcbnew)
            ),
        ):
            rc = guard.run_audit("x.kicad_pcb")
        self.assertEqual(rc, 1)

    def test_prescan_never_takes_option_shaped_json_value(self):
        # `--json --max-report` must not name a file "--max-report"
        self.assertEqual(
            guard._prescan_json_and_board(
                ["--json", "--max-report", "5", "board.kicad_pcb"]
            ),
            (None, None),
        )

    def test_prescan_stops_at_double_dash(self):
        # tokens after `--` are positionals to argparse; honoring --json=
        # there previously overwrote a real board file
        board, json_out = guard._prescan_json_and_board(
            ["decoy.kicad_pcb", "--", "--json=/path/to/real-board.kicad_pcb"]
        )
        self.assertIsNone(json_out)
        self.assertEqual(board, "decoy.kicad_pcb")

    def test_prescan_never_swallows_separator_as_option_value(self):
        # `--max-report -- --json=real.kicad_pcb` previously consumed `--`
        # as the option value and then wrote over the post-`--` path
        self.assertEqual(
            guard._prescan_json_and_board(
                ["--max-report", "--", "--json=real.kicad_pcb", "b.kicad_pcb"]
            ),
            (None, None),
        )

    def test_prescan_consumes_option_values_as_state_machine(self):
        board, json_out = guard._prescan_json_and_board(
            ["--max-report", "5", "b.kicad_pcb", "--json", "out.json"]
        )
        self.assertEqual(board, "b.kicad_pcb")
        self.assertEqual(json_out, "out.json")
        self.assertEqual(
            guard._prescan_json_and_board(["--json=", "b.kicad_pcb"]),
            (None, None),
        )

    def test_write_json_uses_exclusive_mkstemp(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with mock.patch.object(
                guard.tempfile, "mkstemp", wraps=tempfile.mkstemp
            ) as created:
                guard._write_json(json_path, "b.kicad_pcb", "clean", [], {})
            created.assert_called_once()
            self.assertEqual(created.call_args.kwargs["dir"], tmp)

    def test_hard_link_alias_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            board = os.path.join(tmp, "b.kicad_pcb")
            with open(board, "w") as fh:
                fh.write("(kicad_pcb)")
            alias = os.path.join(tmp, "alias.json")
            os.link(board, alias)
            with self.assertRaises(ValueError):
                guard._write_json(alias, board, "clean", [], {})
            with open(board, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "(kicad_pcb)")

    def test_replace_failure_cleans_temp_and_keeps_destination(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                fh.write('{"verdict": "unevaluable"}')
            with mock.patch.object(
                guard.os, "replace", side_effect=OSError("boom")
            ):
                with self.assertRaises(OSError):
                    guard._write_json(json_path, "b.kicad_pcb", "clean", [], {})
            with open(json_path, encoding="utf-8") as fh:
                self.assertEqual(json.load(fh)["verdict"], "unevaluable")
            self.assertEqual(
                [f for f in os.listdir(tmp) if ".tmp." in f], []
            )

    def test_probe_capture_forces_utf8_with_replacement(self):
        proc = mock.Mock(returncode=0, stdout=guard._PROBE_MARKER)
        with mock.patch.object(
            guard.subprocess, "run", return_value=proc
        ) as spawned:
            guard._interpreter_has_pcbnew("/x/python3")
        kwargs = spawned.call_args.kwargs
        self.assertEqual(kwargs["encoding"], "utf-8")
        self.assertEqual(kwargs["errors"], "replace")

    def test_worker_verdict_mismatch_rewrites_artifact_unevaluable(self):
        with tempfile.TemporaryDirectory() as tmp:
            json_path = os.path.join(tmp, "audit.json")
            with open(json_path, "w", encoding="utf-8") as fh:
                json.dump({"verdict": "unevaluable"}, fh)
            proc = mock.Mock(
                returncode=0, stdout=f"{guard._OK_LINE}: 0 collisions\n",
                stderr="",
            )
            args = mock.Mock(
                board="x.kicad_pcb",
                max_report=guard.MAX_REPORT_DEFAULT,
                json_out=json_path,
                timeout=guard.WORKER_TIMEOUT_DEFAULT,
            )
            with mock.patch.object(guard.subprocess, "run", return_value=proc):
                rc = guard._run_worker("/x/python3", args)
            self.assertEqual(rc, 1)
            with open(json_path, encoding="utf-8") as fh:
                payload = json.load(fh)
            self.assertEqual(payload["verdict"], "unevaluable")
            self.assertIn("reason", payload)


if __name__ == "__main__":
    unittest.main()
