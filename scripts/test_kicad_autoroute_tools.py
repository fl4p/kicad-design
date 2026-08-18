#!/usr/bin/env python3

from __future__ import annotations

import io
import json
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parent))

import kicad_autoroute as autoroute
import kicad_autoroute_tools as tools


class AutorouteToolsTests(unittest.TestCase):
    def test_lock_covers_current_platform_shape(self):
        lock = tools.load_lock()
        self.assertEqual(lock["backend"], autoroute.BACKEND_ID)
        self.assertEqual(len(lock["jre"]["platforms"]), 5)
        for value in lock["jre"]["platforms"].values():
            self.assertEqual(len(value["sha256"]), 64)
            self.assertGreater(value["size"], 1_000_000)

    def test_install_requires_explicit_yes_before_network(self):
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(autoroute.AutorouteError, "explicit --yes"):
                tools.install(Path(raw), approved=False)

    def test_tar_traversal_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "bad.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                info = tarfile.TarInfo("../escape")
                payload = b"bad"
                info.size = len(payload)
                handle.addfile(info, io.BytesIO(payload))
            with self.assertRaisesRegex(autoroute.AutorouteError, "escapes"):
                tools.extract_archive(archive, "tar.gz", root / "out")

            archive = root / "link.tar.gz"
            with tarfile.open(archive, "w:gz") as handle:
                info = tarfile.TarInfo("link")
                info.type = tarfile.SYMTYPE
                info.linkname = "target"
                handle.addfile(info)
            with self.assertRaisesRegex(autoroute.AutorouteError, "link/device"):
                tools.extract_archive(archive, "tar.gz", root / "out2")

    def test_zip_traversal_is_rejected(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            archive = root / "bad.zip"
            with zipfile.ZipFile(archive, "w") as handle:
                handle.writestr("../escape", "bad")
            with self.assertRaisesRegex(autoroute.AutorouteError, "escapes"):
                tools.extract_archive(archive, "zip", root / "out")

    def test_tree_digest_binds_paths_and_contents(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "a").write_text("one", encoding="utf-8")
            first = tools.tree_digest(root)
            (root / "a").write_text("two", encoding="utf-8")
            self.assertNotEqual(first, tools.tree_digest(root))

    def test_forged_receipt_cannot_reauthorize_modified_java(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            key = "darwin-arm64"
            target = root / autoroute.BACKEND_ID / key
            java = target / "jre" / "bin" / "java"
            java.parent.mkdir(parents=True)
            jar = target / "freerouting.jar"
            jar.write_bytes(b"jar")
            java.write_bytes(b"trusted-java")
            lock = {
                "schema": "kicad-autoroute-tools-lock-v1",
                "backend": autoroute.BACKEND_ID,
                "freerouting": {"version": "2.3.0", "url": "https://example.invalid/router", "size": 3, "sha256": autoroute.sha256_path(jar)},
                "jre": {"version": "25", "platforms": {key: {
                    "archive": "tar.gz", "url": "https://example.invalid/jre",
                    "size": 10, "sha256": "a" * 64,
                    "java_path": "jre/bin/java",
                    "java_sha256": autoroute.sha256_path(java),
                    "tree_sha256": tools.tree_digest(target),
                }}},
            }
            lock_path = root / "lock.json"
            autoroute.write_json_atomic(lock_path, lock)
            java.write_bytes(b"malicious-java")
            receipt = {
                "schema": "kicad-autoroute-install-receipt-v1",
                "backend": autoroute.BACKEND_ID,
                "platform": key,
                "lock_sha256": autoroute.sha256_path(lock_path),
                "freerouting": {**lock["freerouting"], "path": "freerouting.jar"},
                "jre": {
                    "version": "25", "archive": "tar.gz",
                    "url": "https://example.invalid/jre", "size": 10,
                    "sha256": "a" * 64, "java_path": "jre/bin/java",
                    "java_sha256": autoroute.sha256_path(java),
                },
                "tree_sha256": tools.tree_digest(target),
            }
            autoroute.write_json_atomic(target / tools.RECEIPT, receipt)
            with mock.patch.object(tools, "platform_key", return_value=key):
                with self.assertRaisesRegex(autoroute.AutorouteError, "receipt JRE entry differs"):
                    tools.status(root, lock_path=lock_path, require_valid=True)


if __name__ == "__main__":
    unittest.main()
