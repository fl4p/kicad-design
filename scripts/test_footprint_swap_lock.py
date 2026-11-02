"""Process-level calibration of transaction ownership, independent of KiCad."""
import errno
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import kicad_footprint_swap as swap


class TransactionLockTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'transaction.lock'

    def child(self, code):
        return subprocess.Popen(
            [sys.executable, '-c', code, str(self.path)],
            cwd=Path(swap.__file__).parent, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
        )

    def stop(self, process):
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=10)

    def test_contender_cannot_enter_before_first_acquisition_returns(self):
        # Pause *inside* acquisition, after the OS lock but before metadata read.
        p = self.child('''
import os, sys
from pathlib import Path
import kicad_footprint_swap as s
original = os.read
def paused(fd, count):
 print('locked', flush=True)
 sys.stdin.readline()
 return original(fd, count)
os.read = paused
fd = s._acquire_lock(Path(sys.argv[1]))
print('acquired', flush=True)
sys.stdin.readline()
s._release_lock(Path(sys.argv[1]), fd)
''')
        self.addCleanup(self.stop, p)
        self.assertEqual(p.stdout.readline().strip(), 'locked')
        inode = self.path.stat().st_ino
        with self.assertRaises(swap.RecoveryRequired):
            swap._acquire_lock(self.path)
        self.assertEqual(self.path.stat().st_ino, inode)
        p.stdin.write('\n'); p.stdin.flush()
        self.assertEqual(p.stdout.readline().strip(), 'acquired')
        p.stdin.write('\n'); p.stdin.flush()
        self.assertEqual(p.wait(timeout=10), 0)
        fd = swap._acquire_lock(self.path)
        swap._release_lock(self.path, fd)
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_contender_during_initial_publication_obtains_only_one_lock(self):
        p = self.child('''
import os, sys
from pathlib import Path
import kicad_footprint_swap as s
original = os.link
def paused(source, destination):
 print('prepared', flush=True)
 sys.stdin.readline()
 return original(source, destination)
os.link = paused
try:
 fd = s._acquire_lock(Path(sys.argv[1]))
except s.RecoveryRequired:
 print('refused', flush=True)
else:
 s._release_lock(Path(sys.argv[1]), fd)
 print('acquired', flush=True)
''')
        self.addCleanup(self.stop, p)
        self.assertEqual(p.stdout.readline().strip(), 'prepared')
        fd = swap._acquire_lock(self.path)
        try:
            p.stdin.write('\n'); p.stdin.flush()
            self.assertEqual(p.stdout.readline().strip(), 'refused')
            self.assertEqual(p.wait(timeout=10), 0)
        finally:
            swap._release_lock(self.path, fd)

    def test_process_death_releases_ownership_without_deleting_inode(self):
        p = self.child('''
import sys
from pathlib import Path
import kicad_footprint_swap as s
fd=s._acquire_lock(Path(sys.argv[1]))
print('locked', flush=True)
sys.stdin.readline()
''')
        self.addCleanup(self.stop, p)
        self.assertEqual(p.stdout.readline().strip(), 'locked')
        inode = self.path.stat().st_ino
        p.kill(); p.wait(timeout=10)
        fd = swap._acquire_lock(self.path)
        swap._release_lock(self.path, fd)
        self.assertEqual(self.path.stat().st_ino, inode)

    def test_unknown_empty_and_legacy_metadata_never_authorize_recovery(self):
        for data in (b'', b'pid=999999999\n', b'pid=1\n', b'garbage',
                     swap._LOCK_MARKER + b'garbage'):
            with self.subTest(data=data):
                self.path.write_bytes(data)
                inode = self.path.stat().st_ino
                with self.assertRaisesRegex(swap.RecoveryRequired, 'unrecognized'):
                    swap._acquire_lock(self.path)
                self.assertEqual(self.path.read_bytes(), data)
                self.assertEqual(self.path.stat().st_ino, inode)

    def test_failed_read_and_permission_failure_do_not_replace_lock(self):
        fd = swap._acquire_lock(self.path)
        swap._release_lock(self.path, fd)
        inode = self.path.stat().st_ino
        for operation in ('os.read', 'fcntl.flock'):
            with self.subTest(operation=operation):
                with mock.patch(operation, side_effect=PermissionError(errno.EPERM, 'denied')):
                    with self.assertRaises(swap.RecoveryRequired):
                        swap._acquire_lock(self.path)
                self.assertEqual(self.path.stat().st_ino, inode)
                fd = swap._acquire_lock(self.path)
                swap._release_lock(self.path, fd)

    def test_release_never_unlinks_replacement_inode(self):
        fd = swap._acquire_lock(self.path)
        self.path.unlink()
        self.path.write_bytes(b'other owner')
        swap._release_lock(self.path, fd)
        self.assertEqual(self.path.read_bytes(), b'other owner')

    def test_symlink_and_nonregular_lock_are_refused(self):
        target = self.path.with_name('target')
        target.write_bytes(swap._LOCK_MARKER)
        self.path.symlink_to(target)
        with self.assertRaises(swap.RecoveryRequired):
            swap._acquire_lock(self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaises(swap.RecoveryRequired):
            swap._acquire_lock(self.path)


if __name__ == '__main__':
    unittest.main()
