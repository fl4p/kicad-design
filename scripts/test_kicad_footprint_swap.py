#!/usr/bin/env python3

from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

swap = importlib.import_module("kicad_footprint_swap")


_ADAPTER = r'''
import argparse, hashlib, json, os
from pathlib import Path
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');a=p.parse_args()
r=json.loads(Path(a.request).read_text()); root=Path(r['root']); tx=Path(r['transaction_dir'])
def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def ident(path):
 s=path.stat();return {'sha256':sha(path),'device':s.st_dev,'inode':s.st_ino,'size':s.st_size,'mtime_ns':s.st_mtime_ns}
ops=[]; evidence={}
for target in r['targets']:
 dst=root/target['board']; staged=tx/(target['name']+'.stage')
 staged.write_text(dst.read_text()+'migrated '+target['name']+'\n')
 digest=sha(staged)
 ops.append({'staged':str(staged),'destination':str(dst),'original_identity':ident(dst),'staged_sha256':digest})
 evidence[target['name']]={'destination':str(dst),'staged_sha256':digest,'erc':{'passed':True,'errors':0,'warnings':0},'drc':{'passed':True,'violations':0,'allowed_documentation':0,'unconnected':0,'parity':0},'settlement':{'passed':True,'zone_layers':0},'audits':[{'name':'fixture','passed':True}]}
if os.environ.get('FAKE_MUTATE'):
 (root/r['targets'][0]['board']).write_text('concurrent\n')
Path(a.result).write_text(json.dumps({'schema':'kicad-footprint-swap-adapter-result-v1','status':'clean','promotions':ops,'evidence':{'schema':'kicad-footprint-swap-evidence-v1','targets':evidence}}))
'''

_SLEEP_ADAPTER = r'''
import argparse,time
p=argparse.ArgumentParser();p.add_argument('--request');p.add_argument('--result');p.parse_args();time.sleep(10)
'''


def _spec(root: Path, adapter: Path, names=("base", "noadc")) -> Path:
    targets = []
    for name in names:
        for suffix, text in ((".kicad_pcb", "board"), (".kicad_sch", "sch"), (".kicad_pro", "{}")):
            (root / f"{name}{suffix}").write_text(f"{text} {name}\n", encoding="utf-8")
        targets.append({
            "name": name,
            "board": f"{name}.kicad_pcb",
            "schematic": f"{name}.kicad_sch",
            "project": f"{name}.kicad_pro",
        })
    spec = root / "swap.json"
    spec.write_text(json.dumps({
        "schema": swap.SPEC_SCHEMA,
        "adapter_argv": [sys.executable, str(adapter)],
        "targets": targets,
        "promotion_destinations": [target["board"] for target in targets],
        "substitutions": [{
            "reference": "D4",
            "old_footprint": "LED_SMD:LED_0603_1608Metric",
            "new_footprint": "LED_SMD:LED_0805_2012Metric",
        }],
    }), encoding="utf-8")
    return spec


class DeadlineTests(unittest.TestCase):
    def test_deadline_never_resets(self):
        deadline = swap.Deadline(0.5)
        first = deadline.remaining()
        time.sleep(0.01)
        self.assertLess(deadline.remaining(), first)
        with self.assertRaises(swap.TimeBudgetExceeded):
            deadline.remaining(reserve=1.0)


class TransactionTests(unittest.TestCase):
    def _adapter(self, root: Path, body=_ADAPTER) -> Path:
        path = root / "adapter.py"
        path.write_text(body, encoding="utf-8")
        return path

    def test_dry_run_verifies_but_does_not_promote(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root))
            before = (root / "base.kicad_pcb").read_bytes()
            rc, report = swap.run(spec, False, 10)
            self.assertEqual(rc, 0)
            self.assertEqual(report["status"], "valid_dry_run")
            self.assertEqual((root / "base.kicad_pcb").read_bytes(), before)
            self.assertEqual(len(report["promotions"]), 2)

    def test_apply_promotes_both_targets_and_records_committed_journal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root))
            rc, report = swap.run(spec, True, 10)
            self.assertEqual(rc, 0)
            self.assertEqual(report["status"], "clean")
            self.assertIn("migrated base", (root / "base.kicad_pcb").read_text())
            self.assertIn("migrated noadc", (root / "noadc.kicad_pcb").read_text())
            journal = json.loads((root / ".kicad-footprint-swap-journal.json").read_text())
            self.assertEqual(journal["state"], "committed")
            report_operation = journal["operations"][-1]
            self.assertEqual(
                Path(report_operation["destination"]),
                (root / "footprint-swap-report.json").resolve(),
            )
            self.assertEqual(
                swap._sha256(root / "footprint-swap-report.json"),
                report_operation["staged_sha256"],
            )
            self.assertEqual(swap._recover(root / ".kicad-footprint-swap-journal.json"), "cleared_committed_journal")

    def test_second_target_failure_is_recoverable_and_rolls_back_all(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            adapter = self._adapter(root)
            spec = _spec(root, adapter)
            originals = {name: (root / f"{name}.kicad_pcb").read_bytes() for name in ("base", "noadc")}
            _, base = swap._load_spec(spec)
            transaction = root / ".footprint-swap-transactions" / "fault"
            transaction.mkdir(parents=True)
            operations = []
            for name in ("base", "noadc"):
                destination = root / f"{name}.kicad_pcb"
                staged = transaction / f"{name}.stage"
                staged.write_text(f"new {name}\n")
                operations.append({
                    "staged": str(staged),
                    "destination": str(destination),
                    "original_identity": swap._identity(destination),
                    "staged_sha256": swap._sha256(staged),
                })
            journal_path = root / ".kicad-footprint-swap-journal.json"
            journal = swap._prepare_journal(
                journal_path, "fault", operations, swap.Deadline(10)
            )
            swap._copy_durable(Path(operations[0]["staged"]), Path(operations[0]["destination"]))
            journal["state"] = "applying"
            journal["operations"][0]["state"] = "applied"
            journal["next_index"] = 1
            swap._write_json_durable(journal_path, journal)
            self.assertEqual(swap._recover(journal_path), "rolled_back_incomplete_transaction")
            for name in originals:
                self.assertEqual((root / f"{name}.kicad_pcb").read_bytes(), originals[name])

    def test_concurrent_destination_change_is_rejected_before_promotion(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            adapter = self._adapter(root, _ADAPTER.replace(
                "if os.environ.get('FAKE_MUTATE'):", "if True:"))
            spec = _spec(root, adapter, names=("base",))
            rc, report = swap.run(spec, True, 10)
            self.assertEqual(rc, 2)
            self.assertEqual(report["status"], "concurrent_change")
            self.assertEqual((root / "base.kicad_pcb").read_text(), "concurrent\n")

    def test_adapter_timeout_uses_end_to_end_budget(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root, _SLEEP_ADAPTER), names=("base",))
            rc, report = swap.run(spec, False, 3.0)
            self.assertEqual(rc, 2)
            self.assertEqual(report["status"], "time_budget_exceeded")
            self.assertGreater(report["elapsed_seconds"], 0.5)
            self.assertLess(report["elapsed_seconds"], 5.0)

    def test_opaque_adapter_evidence_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            adapter_body = _ADAPTER.replace(
                "'schema':'kicad-footprint-swap-evidence-v1'", "'schema':'opaque'"
            )
            spec = _spec(root, self._adapter(root, adapter_body), names=("base",))
            rc, report = swap.run(spec, False, 10)
            self.assertEqual(rc, 2)
            self.assertEqual(report["status"], "verification_failed")
            self.assertIn("unsupported evidence schema", report["error"])

    def test_reserved_path_collision_fails_before_deleting_authority(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root), names=("base",))
            value = json.loads(spec.read_text())
            original = (root / "base.kicad_pcb").read_bytes()
            for field in ("lock", "report", "journal"):
                changed = dict(value)
                changed[field] = "base.kicad_pcb"
                spec.write_text(json.dumps(changed))
                with self.assertRaisesRegex(swap.SwapError, "reserved path aliases"):
                    swap.run(spec, True, 10)
                self.assertEqual((root / "base.kicad_pcb").read_bytes(), original)

    def test_unknown_new_file_blocks_recovery(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); tx = root / "tx"; tx.mkdir()
            staged = tx / "new.stage"; staged.write_text("promoted\n")
            destination = root / "new.json"
            operation = {"staged": str(staged), "destination": str(destination),
                         "original_identity": None, "staged_sha256": swap._sha256(staged)}
            journal_path = root / ".journal"
            swap._prepare_journal(journal_path, "new", [operation], swap.Deadline(10))
            destination.write_text("concurrent\n")
            with self.assertRaisesRegex(swap.RecoveryRequired, "unknown bytes"):
                swap._recover(journal_path)
            self.assertEqual(destination.read_text(), "concurrent\n")

    def test_nonregular_new_destination_blocks_recovery(self):
        for kind in ("directory", "broken_symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw); tx = root / "tx"; tx.mkdir()
                staged = tx / "new.stage"; staged.write_text("promoted\n")
                destination = root / "new.json"
                operation = {"staged": str(staged), "destination": str(destination),
                             "original_identity": None, "staged_sha256": swap._sha256(staged)}
                journal_path = root / ".journal"
                swap._prepare_journal(
                    journal_path, "new", [operation], swap.Deadline(10)
                )
                if kind == "directory":
                    destination.mkdir()
                else:
                    destination.symlink_to(root / "missing-target")
                with self.assertRaisesRegex(
                    swap.RecoveryRequired, "non-regular object"
                ):
                    swap._recover(journal_path)
                self.assertTrue(os.path.lexists(str(destination)))

    def test_staged_new_file_is_removed_during_recovery(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw); tx = root / "tx"; tx.mkdir()
            staged = tx / "new.stage"; staged.write_text("promoted\n")
            destination = root / "new.json"
            operation = {"staged": str(staged), "destination": str(destination),
                         "original_identity": None, "staged_sha256": swap._sha256(staged)}
            journal_path = root / ".journal"
            swap._prepare_journal(journal_path, "new", [operation], swap.Deadline(10))
            swap._copy_durable(staged, destination)
            self.assertEqual(
                swap._recover(journal_path), "rolled_back_incomplete_transaction"
            )
            self.assertFalse(destination.exists())

    def test_active_schematic_lock_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root), names=("base",))
            lock = root / "~base.kicad_sch.lck"; lock.write_text("open")
            with self.assertRaisesRegex(swap.SwapError, "active KiCad lock"):
                swap.run(spec, False, 10)

    def test_unknown_spec_field_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            spec = _spec(root, self._adapter(root), names=("base",))
            value = json.loads(spec.read_text())
            value["unsafe"] = True
            spec.write_text(json.dumps(value))
            with self.assertRaisesRegex(swap.SwapError, "extra"):
                swap.run(spec, False, 10)


if __name__ == "__main__":
    unittest.main()
