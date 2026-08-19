#!/usr/bin/env python3
"""Fail-closed adapter template for an arbitrary project generator.

Implement seed by invoking the project generator without a shell.  Seed
must retain the configured board basename and same-stem sidecars.  Final must
invoke autoroute_apply.py final; that pinned applicator calls this adapter's
seed operation again, verifies the reviewed semantic/context attestation, and
applies the canonical manifest before project-owned post-processing.  A custom
integration may move that application to the generator's correct pre-save
point only if its applicator remains promotion-pinned and emits the same
attestation.  Never patch generator source from this template automatically.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


PROTOCOL = "kicad-autoroute-adapter-v1"


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
    if args.command == "describe":
        path = Path(args.report).expanduser().resolve()
        config = Path(__file__).resolve().with_name("autoroute.json")
        protected = {
            Path(__file__).resolve(),
            config,
            Path(__file__).resolve().with_name("autoroute_apply.py"),
            Path(__file__).resolve().with_name("autoroute_audit.py"),
        }
        protected_directories = set()
        configured = None
        if config.is_file():
            configured = json.loads(config.read_text(encoding="utf-8"))
            root = config.parent
            for source in configured.get("sources") or []:
                source_path = (root / source["path"]).resolve()
                if source.get("kind") == "directory-recursive":
                    protected_directories.add(source_path)
                else:
                    protected.add(source_path)
        if path in protected or any(
            path == directory or path.is_relative_to(directory)
            for directory in protected_directories
        ):
            parser.error("describe report collides with adapter/config/tool")
        path.parent.mkdir(parents=True, exist_ok=True)
        details = {
            "protocol": PROTOCOL, "mode": "generator-adapter", "ready": False,
            "operations": ["seed", "final"],
            "status": "BLOCKED_ADAPTER",
            "reason": "implement and audit this project-specific adapter",
        }
        if configured is not None:
            if configured["project"]["schematic_authority"] == "board-only":
                details["permanent_waiver"] = (
                    "schematic parity and ERC unavailable; PCB is authoritative"
                )
        path.write_text(json.dumps(details, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
        return 0
    print("BLOCKED_ADAPTER: generator adapter template is not implemented", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
