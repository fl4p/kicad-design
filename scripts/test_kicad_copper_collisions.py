#!/usr/bin/env python3
"""Tests for the certain-short copper audit (pcbnew mocked).

Calibration against a real known-bad board (hundreds of DRC-confirmed shorts)
and a real known-good board was performed with the bundled interpreter when the
guard was written; these tests pin the pure logic: pair enumeration, net and
bbox filtering, fail-closed exit mapping, and the unevaluable path.
"""

from __future__ import annotations

import sys
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


class FakeShape:
    def __init__(self, owner):
        self.owner = owner

    def Collide(self, other, clearance):
        return (self.owner, other.owner) in COLLIDING or (
            other.owner,
            self.owner,
        ) in COLLIDING


COLLIDING = set()


class FakePos:
    x, y = 1_000_000, 2_000_000


class FakeItem:
    def __init__(self, name, net, layers, box, cls="PCB_TRACK", netname=None):
        self.name = name
        self._net = net
        self._layers = layers
        self._box = box
        self._cls = cls
        self._netname = netname or f"net{net}"
        self.shape_requests = []

    def IsOnLayer(self, layer):
        return layer in self._layers

    def GetNetCode(self):
        return self._net

    def GetNetname(self):
        return self._netname

    def GetBoundingBox(self):
        return self._box

    def GetEffectiveShape(self, layer):
        self.shape_requests.append(layer)
        return FakeShape(self.name)

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

    def test_different_nets_colliding_is_a_finding(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        COLLIDING.add(("a", "b"))
        findings, inventory = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["layer"], "F.Cu")
        self.assertEqual(findings[0]["nets"], ["net1", "net2"])
        self.assertEqual(inventory["items"], 2)

    def test_same_net_never_tested_or_reported(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 1, [F_CU], BOX)
        COLLIDING.add(("a", "b"))  # would fire if the net filter broke
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(findings, [])
        self.assertEqual(a.shape_requests, [])

    def test_bbox_prefilter_skips_disjoint_items(self):
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], FAR_BOX)
        COLLIDING.add(("a", "b"))  # geometric lie the prefilter must not reach
        findings, _ = guard.audit_board(FakeBoard([a, b]), FakePcbnew)
        self.assertEqual(findings, [])
        self.assertEqual(a.shape_requests, [])

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


class ExitMappingTests(unittest.TestCase):
    """run_audit maps findings/empty/missing to fail-closed exit codes."""

    def _run(self, board, board_exists=True, tmp_path="x.kicad_pcb"):
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
            return guard.run_audit(tmp_path)

    def test_missing_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(None, board_exists=False), 1)

    def test_empty_board_is_unevaluable_not_ok(self):
        self.assertEqual(self._run(FakeBoard()), 1)

    def test_clean_board_exits_zero(self):
        COLLIDING.clear()
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], FAR_BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 0)

    def test_findings_exit_two(self):
        COLLIDING.clear()
        COLLIDING.add(("a", "b"))
        a = FakeItem("a", 1, [F_CU], BOX)
        b = FakeItem("b", 2, [F_CU], BOX)
        self.assertEqual(self._run(FakeBoard([a, b])), 2)


if __name__ == "__main__":
    unittest.main()
