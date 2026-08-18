#!/usr/bin/env python3
"""Install the pinned local Freerouting/JRE toolchain after explicit consent."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import shutil
import ssl
import stat
import sys
import tarfile
import tempfile
import urllib.request
import zipfile

from kicad_autoroute import BACKEND_ID, AutorouteError, sha256_path, write_json_atomic


LOCK = Path(__file__).with_name("freerouting-tools-lock.json")
RECEIPT = "install-receipt.json"


def platform_key() -> str:
    system = {"Darwin": "darwin", "Linux": "linux", "Windows": "windows"}.get(platform.system())
    machine = platform.machine().lower()
    architecture = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64"} else None
    if not system or not architecture:
        raise AutorouteError(f"unsupported autorouter platform {platform.system()}/{platform.machine()}")
    key = f"{system}-{architecture}"
    if key == "windows-arm64":
        raise AutorouteError("Windows arm64 is not in the promotion v1 tool matrix")
    return key


def default_cache() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Caches"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return base / "kicad-design" / "autoroute"


def load_lock(path: Path = LOCK) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    if data.get("schema") != "kicad-autoroute-tools-lock-v1" or data.get("backend") != BACKEND_ID:
        raise AutorouteError(f"unsupported autorouter lock {path}")
    if set(data) != {"schema", "backend", "freerouting", "jre"}:
        raise AutorouteError(f"autorouter lock has unexpected top-level fields: {path}")
    if set(data["freerouting"]) != {"version", "url", "size", "sha256"}:
        raise AutorouteError("Freerouting lock entry has unexpected fields")
    if set(data["jre"]) != {"version", "platforms"} or not isinstance(data["jre"]["platforms"], dict):
        raise AutorouteError("JRE lock entry is malformed")
    for key, spec in data["jre"]["platforms"].items():
        base = {"archive", "url", "size", "sha256"}
        pins = {"java_path", "java_sha256", "tree_sha256"}
        if not isinstance(spec, dict) or not base <= set(spec) or set(spec) - (base | pins):
            raise AutorouteError(f"JRE lock entry for {key} is malformed")
        if set(spec) & pins and not pins <= set(spec):
            raise AutorouteError(f"JRE integrity pins for {key} must be complete")
    return data


def _safe_member(name: str) -> PurePosixPath:
    path = PurePosixPath(name.replace("\\", "/"))
    if path.is_absolute() or not path.parts or ".." in path.parts:
        raise AutorouteError(f"archive member escapes extraction root: {name!r}")
    return path


def extract_archive(archive: Path, kind: str, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    if kind == "tar.gz":
        with tarfile.open(archive, "r:gz") as handle:
            members = handle.getmembers()
            for member in members:
                _safe_member(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise AutorouteError(f"refusing link/device archive member {member.name!r}")
            handle.extractall(destination, members=members)
        return
    if kind == "zip":
        with zipfile.ZipFile(archive) as handle:
            for info in handle.infolist():
                relative = _safe_member(info.filename)
                mode = (info.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise AutorouteError(f"refusing symlink archive member {info.filename!r}")
                target = destination.joinpath(*relative.parts)
                target.resolve().relative_to(destination.resolve())
                if info.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with handle.open(info) as source, target.open("wb") as output:
                        shutil.copyfileobj(source, output)
        return
    raise AutorouteError(f"unsupported archive type {kind!r}")


def tree_digest(root: Path, *, exclude: set[str] | None = None) -> str:
    digest = hashlib.sha256()
    excluded = exclude or set()
    descendants = sorted(root.rglob("*"))
    links = [path for path in descendants if path.is_symlink()]
    if links:
        raise AutorouteError(f"installed tree contains a symlink: {links[0]}")
    files = [
        path for path in descendants
        if path.is_file() and path.relative_to(root).as_posix() not in excluded
    ]
    if not files:
        raise AutorouteError(f"installed tree is empty: {root}")
    for path in files:
        relative = path.relative_to(root).as_posix().encode("utf-8")
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(bytes.fromhex(sha256_path(path)))
    return digest.hexdigest()


def _download(url: str, target: Path) -> None:
    request = urllib.request.Request(url, headers={"User-Agent": "kicad-design-autoroute-installer/1"})
    paths = ssl.get_default_verify_paths()
    context = None
    if paths.cafile is None:
        # The python.org macOS framework does not point OpenSSL at the OS/Homebrew
        # CA bundle until its optional certificate installer has been run.
        # Use an existing system bundle explicitly; never disable verification.
        for candidate in (Path("/etc/ssl/cert.pem"), Path("/private/etc/ssl/cert.pem")):
            if candidate.is_file():
                context = ssl.create_default_context(cafile=str(candidate))
                break
    with urllib.request.urlopen(request, timeout=120, context=context) as source, target.open("wb") as output:
        shutil.copyfileobj(source, output)


def _verify_artifact(path: Path, spec: dict, label: str) -> None:
    if path.stat().st_size != spec["size"]:
        raise AutorouteError(f"{label} size mismatch")
    actual = sha256_path(path)
    if actual != spec["sha256"]:
        raise AutorouteError(f"{label} SHA-256 mismatch: {actual}")


def install(cache: Path, *, approved: bool, lock_path: Path = LOCK) -> dict:
    if not approved:
        raise AutorouteError("installation requires explicit --yes authorization")
    lock = load_lock(lock_path)
    key = platform_key()
    jre = lock["jre"]["platforms"].get(key)
    if not jre:
        raise AutorouteError(f"tool lock has no entry for {key}")
    target = cache.resolve() / BACKEND_ID / key
    if target.exists():
        return status(cache, lock_path=lock_path, require_valid=True)
    target.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".install-", dir=target.parent))
    try:
        jar_archive = scratch / "freerouting.jar.download"
        jre_archive = scratch / ("jre.zip" if jre["archive"] == "zip" else "jre.tar.gz")
        _download(lock["freerouting"]["url"], jar_archive)
        _download(jre["url"], jre_archive)
        _verify_artifact(jar_archive, lock["freerouting"], "Freerouting JAR")
        _verify_artifact(jre_archive, jre, "Temurin JRE")
        install_root = scratch / "root"
        install_root.mkdir()
        shutil.copy2(jar_archive, install_root / "freerouting.jar")
        extract_archive(jre_archive, jre["archive"], install_root / "jre")
        java_name = "java.exe" if os.name == "nt" else "java"
        java_candidates = sorted(install_root.joinpath("jre").rglob(java_name))
        java_candidates = [path for path in java_candidates if path.parent.name == "bin"]
        if len(java_candidates) != 1:
            raise AutorouteError(f"expected one JRE java executable, found {java_candidates}")
        java_path = java_candidates[0].relative_to(install_root).as_posix()
        if jre.get("java_path") is not None and java_path != jre["java_path"]:
            raise AutorouteError("extracted Java path differs from the tracked lock")
        java_sha = sha256_path(java_candidates[0])
        if jre.get("java_sha256") is not None and java_sha != jre["java_sha256"]:
            raise AutorouteError("extracted Java executable differs from the tracked lock")
        installed_tree_sha = tree_digest(install_root)
        if jre.get("tree_sha256") is not None and installed_tree_sha != jre["tree_sha256"]:
            raise AutorouteError("extracted tool tree differs from the tracked lock")
        receipt = {
            "schema": "kicad-autoroute-install-receipt-v1",
            "backend": BACKEND_ID,
            "platform": key,
            "lock_sha256": sha256_path(lock_path),
            "freerouting": {**lock["freerouting"], "path": "freerouting.jar"},
            "jre": {
                "version": lock["jre"]["version"],
                **jre,
                "java_path": java_path,
                "java_sha256": java_sha,
            },
            "tree_sha256": installed_tree_sha,
        }
        write_json_atomic(install_root / RECEIPT, receipt)
        install_root.replace(target)
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    return status(cache, lock_path=lock_path, require_valid=True)


def status(cache: Path, *, lock_path: Path = LOCK, require_valid: bool = False) -> dict:
    lock = load_lock(lock_path)
    key = platform_key()
    target = cache.resolve() / BACKEND_ID / key
    receipt_path = target / RECEIPT
    result = {"installed": False, "backend": BACKEND_ID, "platform": key, "root": str(target)}
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        if not isinstance(receipt, dict) or set(receipt) != {
            "schema", "backend", "platform", "lock_sha256", "freerouting",
            "jre", "tree_sha256",
        }:
            raise AutorouteError("install receipt schema fields are invalid")
        if receipt["schema"] != "kicad-autoroute-install-receipt-v1":
            raise AutorouteError("install receipt schema is unsupported")
        if receipt["backend"] != BACKEND_ID or receipt["platform"] != key:
            raise AutorouteError("install receipt backend/platform differs from this installation")
        if receipt.get("lock_sha256") != sha256_path(lock_path):
            raise AutorouteError("install receipt uses a different tool lock")
        expected_jre = lock["jre"]["platforms"].get(key)
        if expected_jre is None:
            raise AutorouteError(f"tool lock has no entry for {key}")
        expected_router_receipt = {**lock["freerouting"], "path": "freerouting.jar"}
        if receipt["freerouting"] != expected_router_receipt:
            raise AutorouteError("install receipt Freerouting entry differs from the lock")
        expected_jre_receipt = {
            "version": lock["jre"]["version"],
            **expected_jre,
        }
        if set(expected_jre) >= {"java_path", "java_sha256", "tree_sha256"}:
            expected_jre_receipt.pop("tree_sha256")
        else:
            expected_jre_receipt.update(
                {
                    "java_path": receipt["jre"].get("java_path"),
                    "java_sha256": receipt["jre"].get("java_sha256"),
                }
            )
        if receipt["jre"] != expected_jre_receipt:
            raise AutorouteError("install receipt JRE entry differs from the lock")
        jar_relative = _safe_member(receipt["freerouting"]["path"])
        java_relative = _safe_member(receipt["jre"]["java_path"])
        jar = target.joinpath(*jar_relative.parts)
        java = target.joinpath(*java_relative.parts)
        if jar.is_symlink() or java.is_symlink():
            raise AutorouteError("installed Java/JAR path is a symlink")
        _verify_artifact(jar, lock["freerouting"], "installed Freerouting JAR")
        if not java.is_file():
            raise AutorouteError("installed Java executable is missing")
        if sha256_path(java) != receipt["jre"]["java_sha256"]:
            raise AutorouteError("installed Java executable digest mismatch")
        # The receipt file is excluded from its own tree digest.
        actual_tree = tree_digest(target, exclude={RECEIPT})
        if actual_tree != receipt["tree_sha256"]:
            raise AutorouteError("installed tool tree digest mismatch")
        integrity_pinned = all(
            key_name in expected_jre
            for key_name in ("java_path", "java_sha256", "tree_sha256")
        )
        if integrity_pinned and actual_tree != expected_jre["tree_sha256"]:
            raise AutorouteError("installed tool tree differs from the tracked lock")
        result.update(
            {
                "installed": True,
                "jar": str(jar),
                "java": str(java),
                "receipt": str(receipt_path),
                "receipt_sha256": sha256_path(receipt_path),
                "promotion_integrity_pinned": integrity_pinned,
            }
        )
    except (OSError, KeyError, json.JSONDecodeError, AutorouteError) as exc:
        result["error"] = str(exc)
        if require_valid:
            raise AutorouteError(f"invalid autorouter installation: {exc}") from exc
    return result


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, default=default_cache())
    parser.add_argument("--lock", type=Path, default=LOCK)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status")
    install_parser = sub.add_parser("install")
    install_parser.add_argument("--yes", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = status(args.cache_dir, lock_path=args.lock) if args.command == "status" else install(args.cache_dir, approved=args.yes, lock_path=args.lock)
    except (AutorouteError, OSError) as exc:
        print(f"kicad_autoroute_tools: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("installed") or args.command == "status" else 2


if __name__ == "__main__":
    raise SystemExit(main())
