"""Deterministic external-writer interleavings for promotion and recovery."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import kicad_footprint_swap as swap


class PromotionConflictTests(unittest.TestCase):
    def prepare(self, root, *, new=False):
        operations = []
        for name in ('a', 'b'):
            destination = root / name
            if not new:
                destination.write_text('original ' + name)
            staged = root / (name + '.stage')
            staged.write_text('migrated ' + name)
            operations.append(dict(destination=str(destination), staged=str(staged),
                                   original_identity=None if new else swap._identity(destination),
                                   staged_sha256=swap._sha256(staged)))
        journal_path = root / 'journal.json'
        journal = swap._prepare_journal(journal_path, 'test', operations, swap.Deadline(10))
        return journal_path, journal

    def test_edit_between_promotions_survives_and_retains_recovery_journal(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, journal = self.prepare(root)
            copy = swap._copy_durable

            def edit_after_first(source, destination, **kwargs):
                copy(source, destination, **kwargs)
                if destination == root / 'a':
                    (root / 'b').write_text('external edit')

            with patch.object(swap, '_copy_durable', side_effect=edit_after_first):
                with self.assertRaises(swap.ConcurrentChange):
                    swap._promote(path, journal, swap.Deadline(10))
            self.assertEqual((root / 'b').read_text(), 'external edit')
            self.assertEqual((root / 'a').read_text(), 'migrated a')
            with self.assertRaisesRegex(swap.RecoveryRequired, 'unknown bytes'):
                swap._recover(path)
            self.assertEqual(json.loads(path.read_text())['state'], 'applying')
            self.assertEqual((root / 'b').read_text(), 'external edit')

    def test_destination_mutations_during_temp_copy_fail_closed(self):
        for kind in ('edit', 'delete', 'directory', 'symlink', 'new', 'new_broken_symlink'):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path, journal = self.prepare(root, new=kind.startswith('new'))
                destination = root / 'a'
                copy = swap.shutil.copyfileobj

                def mutate(incoming, outgoing):
                    copy(incoming, outgoing)
                    if Path(incoming.name) != root / 'a.stage':
                        return
                    if not kind.startswith('new'):
                        destination.unlink()
                    if kind in ('edit', 'new'):
                        destination.write_text('external edit')
                    elif kind == 'directory':
                        destination.mkdir()
                    elif kind in ('symlink', 'new_broken_symlink'):
                        destination.symlink_to(root / 'missing')

                with patch.object(swap.shutil, 'copyfileobj', side_effect=mutate):
                    with self.assertRaises(swap.ConcurrentChange):
                        swap._promote(path, journal, swap.Deadline(10))
                self.assertNotEqual(json.loads(path.read_text())['state'], 'committed')
                self.assertFalse(list(root.glob('.a.*.tmp')))
                if kind in ('edit', 'new'):
                    self.assertEqual(destination.read_text(), 'external edit')
                elif kind == 'delete':
                    self.assertFalse(os.path.lexists(destination))
                elif kind == 'directory':
                    self.assertTrue(destination.is_dir())
                else:
                    self.assertTrue(destination.is_symlink())

    def test_unreadable_destination_fails_closed(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, journal = self.prepare(root)
            with patch.object(swap, '_identity', side_effect=PermissionError('denied')):
                with self.assertRaisesRegex(swap.ConcurrentChange, 'cannot verify destination'):
                    swap._promote(path, journal, swap.Deadline(10))
            self.assertEqual((root / 'a').read_text(), 'original a')
            self.assertEqual(json.loads(path.read_text())['state'], 'prepared')

    def test_failed_absence_probe_is_not_treated_as_absent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, journal = self.prepare(root, new=True)
            real_lstat = Path.lstat

            def denied(destination, *args, **kwargs):
                if destination == root / 'a':
                    raise PermissionError('denied')
                return real_lstat(destination, *args, **kwargs)

            with patch.object(Path, 'lstat', denied):
                with self.assertRaisesRegex(swap.ConcurrentChange, 'cannot verify destination'):
                    swap._promote(path, journal, swap.Deadline(10))
                with self.assertRaisesRegex(swap.RecoveryRequired, 'cannot verify interrupted'):
                    swap._recover(path)
            self.assertFalse((root / 'a').exists())
            self.assertTrue(path.exists())

    def test_edit_during_rollback_copy_survives(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, journal = self.prepare(root)
            swap._copy_durable(root / 'a.stage', root / 'a')
            copy = swap.shutil.copyfileobj

            def edit_during_copy(incoming, outgoing):
                copy(incoming, outgoing)
                (root / 'a').write_text('external edit during recovery')

            with patch.object(swap.shutil, 'copyfileobj', side_effect=edit_during_copy):
                with self.assertRaisesRegex(swap.RecoveryRequired, 'destination changed'):
                    swap._recover(path)
            self.assertEqual((root / 'a').read_text(), 'external edit during recovery')
            self.assertTrue(path.exists())
            self.assertFalse(list(root.glob('.a.*.tmp')))

    def test_edit_before_rollback_unlink_survives(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            path, journal = self.prepare(root, new=True)
            swap._copy_durable(root / 'a.stage', root / 'a')
            sha = swap._sha256
            edited = False

            def edit_after_digest(destination):
                nonlocal edited
                digest = sha(destination)
                if destination == root / 'a' and not edited:
                    edited = True
                    destination.write_text('external edit before unlink')
                return digest

            with patch.object(swap, '_sha256', side_effect=edit_after_digest):
                with self.assertRaises(swap.RecoveryRequired):
                    swap._recover(path)
            self.assertEqual((root / 'a').read_text(), 'external edit before unlink')
            self.assertTrue(path.exists())

    def test_run_reports_incomplete_promotion_when_conflict_blocks_recovery(self):
        from test_kicad_footprint_swap import _ADAPTER, _spec
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw).resolve()
            adapter = root / 'adapter.py'
            adapter.write_text(_ADAPTER)
            spec = _spec(root, adapter)
            copy = swap._copy_durable

            def edit_after_first(source, destination, **kwargs):
                copy(source, destination, **kwargs)
                if destination == root / 'base.kicad_pcb':
                    (root / 'noadc.kicad_pcb').write_text('external edit')

            with patch.object(swap, '_copy_durable', side_effect=edit_after_first):
                rc, report = swap.run(spec, True, 10)
            self.assertEqual(rc, 2)
            self.assertEqual(report['status'], 'recovery_required')
            self.assertEqual(report['promotion'], 'incomplete')
            self.assertEqual((root / 'noadc.kicad_pcb').read_text(), 'external edit')
            self.assertEqual(json.loads((root / 'footprint-swap-report.json').read_text())
                             ['promotion'], 'incomplete')
            self.assertEqual(json.loads((root / '.kicad-footprint-swap-journal.json').read_text())
                             ['state'], 'applying')

    def test_unchanged_existing_and_new_destinations_commit(self):
        for new in (False, True):
            with self.subTest(new=new), tempfile.TemporaryDirectory() as raw:
                root = Path(raw)
                path, journal = self.prepare(root, new=new)
                swap._promote(path, journal, swap.Deadline(10))
                self.assertEqual(json.loads(path.read_text())['state'], 'committed')
                for name in ('a', 'b'):
                    self.assertEqual((root / name).read_text(), 'migrated ' + name)


if __name__ == '__main__':
    unittest.main()
