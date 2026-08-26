#!/usr/bin/env python3
"""Tests for the certain-short copper audit (pcbnew mocked).

Calibration against a real known-bad board (hundreds of DRC-confirmed shorts)
and a real known-good board was performed with the bundled interpreter when
the guard was written; these tests pin the pure logic: pair enumeration, net
and layer filtering, the shape-derived bbox prefilter, the touch clearance,
the NPTH flashing policy, fail-closed exit mapping, and the JSON verdict
lifecycle.
"""

from __future__ import annotations

import json
import os
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

    def test_collide_uses_touch_clearance_not_zero(self):
        # clearance 0 misses exact tangency on 10.0.5; the audit must pass
        # TOUCH_CLEARANCE_IU so tangent copper still counts as a short.
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(len(COLLIDE_CALLS), 1)
        self.assertEqual(COLLIDE_CALLS[0][2], guard.TOUCH_CLEARANCE_IU)
        self.assertGreater(guard.TOUCH_CLEARANCE_IU, 0)

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


class ExitMappingTests(unittest.TestCase):
    """run_audit maps findings/empty/missing to fail-closed exit codes and
    writes a verdict-bearing JSON artifact on every path."""

    def setUp(self):
        COLLIDING.clear()
        COLLIDE_CALLS.clear()
        self._tmp = tempfile.TemporaryDirectory()
        self.json_path = os.path.join(self._tmp.name, "audit.json")

    def tearDown(self):
        self._tmp.cleanup()

    def _run(self, board, board_exists=True, tmp_path="x.kicad_pcb", **kwargs):
        fake_pcbnew = mock.Mock()
        fake_pcbnew.LoadBoard.return_value = board
        real_audit = guard.audit_board
        with (
            mock.patch.dict(sys.modules, {"pcbnew": fake_pcbnew, "wx": None}),
            mock.patch.object(guard.os.path, "isfile", return_value=board_exists),
            mock.patch.object(
                guard, "audit_board", side_effect=lambda b, p: real_audit(b, FakePcbnew)
            ),
        ):
            return guard.run_audit(tmp_path, json_out=self.json_path, **kwargs)

    def _verdict(self):
        with open(self.json_path, encoding="utf-8") as fh:
            return json.load(fh)["verdict"]

    def test_missing_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(None, board_exists=False), 1)
        self.assertEqual(self._verdict(), "unevaluable")

    def test_empty_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(FakeBoard()), 1)
        self.assertEqual(self._verdict(), "unevaluable")

    def test_clean_board_exits_zero(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], FAR_BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 0)
        self.assertEqual(self._verdict(), "clean")

    def test_findings_exit_two(self):
        COLLIDING.add(("a", "b"))
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 2)
        self.assertEqual(self._verdict(), "collisions")

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

    def test_probe_requires_marker_not_just_exit_zero(self):
        proc = mock.Mock(stdout="")  # e.g. /usr/bin/true: exit 0, no output
        with mock.patch.object(guard.subprocess, "run", return_value=proc):
            self.assertFalse(guard._interpreter_has_pcbnew("/usr/bin/true"))
        proc_ok = mock.Mock(stdout="PCBNEW-OK")
        with mock.patch.object(guard.subprocess, "run", return_value=proc_ok):
            self.assertTrue(guard._interpreter_has_pcbnew("/x/python3"))


if __name__ == "__main__":
    unittest.main()
