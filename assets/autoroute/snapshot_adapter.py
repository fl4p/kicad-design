#!/usr/bin/env python3
"""Built-in board-snapshot adapter for the KiCad autoroute scaffold."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import sys


PROTOCOL = "kicad-autoroute-adapter-v1"


def _write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    describe = sub.add_parser("describe")
    describe.add_argument("--report", required=True)
    for name in ("seed", "final"):
        command = sub.add_parser(name)
        command.add_argument("--output-dir", required=True)
        command.add_argument("--report", required=True)
        if name == "final":
            command.add_argument("--manifest", required=True)
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    config = root / "autoroute.json"
    applicator = root / "autoroute_apply.py"
    configured = json.loads(config.read_text(encoding="utf-8"))
    if args.command == "describe":
        report = Path(args.report).expanduser().resolve()
        protected_files = {
            config.resolve(), applicator.resolve(), Path(__file__).resolve(),
            root / "autoroute_audit.py",
        }
        protected_directories = set()
        for source in configured.get("sources") or []:
            path = (root / source["path"]).resolve()
            if source.get("kind") == "directory-recursive":
                protected_directories.add(path)
            else:
                protected_files.add(path)
        if report in protected_files or any(
            report == directory or report.is_relative_to(directory)
            for directory in protected_directories
        ):
            parser.error("describe report collides with adapter/config/applicator")
        details = {
            "protocol": PROTOCOL, "mode": "board-snapshot", "ready": True,
            "operations": ["seed", "final"], "config": str(config),
        }
        if configured["project"]["schematic_authority"] == "board-only":
            details["permanent_waiver"] = (
                "schematic parity and ERC unavailable; PCB is authoritative"
            )
        _write(report, details)
        return 0
    output_dir = Path(args.output_dir).expanduser().resolve()
    report = Path(args.report).expanduser().resolve()
    try:
        report.relative_to(output_dir)
    except ValueError:
        parser.error("seed/final report must stay below --output-dir")
    command = [
        sys.executable, str(applicator), args.command,
        "--config", str(config), "--output-dir", str(output_dir),
        "--report", str(report),
    ]
    if args.command == "final":
        command.extend(["--manifest", str(Path(args.manifest).expanduser().resolve())])
    timeout = (
        configured["limits"]["timeout_seconds"]
        + configured["limits"]["audit_timeout_seconds"]
    )
    try:
        completed = subprocess.run(command, check=False, timeout=timeout)
    except subprocess.TimeoutExpired:
        print(
            f"BLOCKED_TOOLCHAIN: applicator timed out after {timeout} seconds",
            file=sys.stderr,
        )
        return 124
    return int(completed.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
