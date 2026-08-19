#!/usr/bin/env python3
"""Plan, apply, and check a project-neutral KiCad autoroute scaffold."""

from __future__ import annotations

import argparse
import collections
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile

from kicad_autoroute import (
    AutorouteError,
    BACKEND_ID,
    CONFIG_SCHEMA_V2,
    build_v2_input_bundle,
    canonical_json_bytes,
    canonical_json_sha256,
    declared_source_digest,
    load_config,
    resolve_project_netclasses,
    sha256_path,
    verify_project_styles,
    write_json_atomic,
)


PLAN_SCHEMA = "kicad-autoroute-scaffold-plan-v1"
RESET_SCHEMA = "kicad-autoroute-route-reset-v1"
CHECK_SCHEMA = "kicad-autoroute-scaffold-check-v1"
ASSET_ROOT = Path(__file__).resolve().parent.parent / "assets" / "autoroute"
ASSETS = {
    "snapshot-adapter": ASSET_ROOT / "snapshot_adapter.py",
    "generator-adapter": ASSET_ROOT / "generator_adapter.py",
    "applicator": ASSET_ROOT / "autoroute_apply.py",
    "audit": ASSET_ROOT / "audit_stub.py",
}


class ScaffoldError(RuntimeError):
    pass


def _project_relative(root: Path, path: Path | str, name: str) -> str:
    lexical = Path(os.path.abspath(Path(path).expanduser()))
    resolved = lexical.resolve()
    try:
        relative = resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ScaffoldError(f"{name} must stay below project root {root}: {resolved}") from exc
    probe = root.resolve()
    for part in relative.parts:
        probe = probe / part
        if probe.is_symlink():
            raise ScaffoldError(f"{name} contains a symlink: {probe}")
    return relative.as_posix()


def _nm(raw: str | None, name: str) -> int:
    if raw is None:
        raise ScaffoldError(f"{name} is required")
    try:
        value = Decimal(raw) * Decimal(1_000_000)
        integral = value.to_integral_exact()
    except (InvalidOperation, ValueError) as exc:
        raise ScaffoldError(f"{name} must convert exactly to integer nanometres") from exc
    if integral != value or integral <= 0:
        raise ScaffoldError(f"{name} must be positive and exact to one nanometre")
    return int(integral)


def _style_from_class(entry: dict, name: str) -> dict:
    result = {}
    for source, target in (
        ("track_width", "track_width_nm"),
        ("clearance", "clearance_nm"),
        ("via_diameter", "via_diameter_nm"),
        ("via_drill", "via_drill_nm"),
    ):
        try:
            exact = Decimal(str(entry[source])) * Decimal(1_000_000)
            integral = exact.to_integral_exact()
        except (KeyError, InvalidOperation, ValueError) as exc:
            raise ScaffoldError(f"net class {name!r} has invalid {source}") from exc
        if integral != exact or integral <= 0:
            raise ScaffoldError(f"net class {name!r} {source} is not exact positive nm")
        result[target] = int(integral)
    if result["via_drill_nm"] >= result["via_diameter_nm"]:
        raise ScaffoldError(f"net class {name!r} via drill is not smaller than diameter")
    return result


def _route_record(raw: dict) -> dict:
    route = raw.get("route")
    if not isinstance(route, dict) or route.get("kind") not in {"segment", "via"}:
        raise ScaffoldError(f"unsupported route primitive in reset scope: {route}")
    if route["kind"] == "segment":
        if route.get("start_nm") == route.get("end_nm") or int(route.get("width_nm", 0)) <= 0:
            raise ScaffoldError("reset scope contains a zero-length or zero-width segment")
    elif (
        route.get("layers") != ["F.Cu", "B.Cu"]
        or int(route.get("drill_nm", 0)) <= 0
        or int(route.get("diameter_nm", 0)) <= int(route.get("drill_nm", 0))
    ):
        raise ScaffoldError("reset scope contains an invalid or non-through via")
    if route["kind"] == "via" and raw.get("primitive_type") not in (0, 4):
        raise ScaffoldError("reset scope contains a non-through via")
    uuid = raw.get("uuid")
    if not isinstance(uuid, str) or not uuid:
        raise ScaffoldError("selected route has no stable UUID")
    return {
        "uuid": uuid,
        "route": route,
        "locked": bool(raw.get("locked")),
        "primitive_type": raw.get("primitive_type", route["kind"]),
        "multiplicity": 1,
    }


def _read_project(path: Path) -> dict:
    try:
        project = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ScaffoldError(f"cannot read KiCad project {path}: {exc}") from exc
    if not isinstance(project, dict):
        raise ScaffoldError(f"KiCad project is not a JSON object: {path}")
    return project


def _class_map(project: dict) -> dict[str, dict]:
    settings = project.get("net_settings")
    if not isinstance(settings, dict) or not isinstance(settings.get("classes"), list):
        raise ScaffoldError("KiCad project has no net_settings.classes array")
    result = {}
    for entry in settings["classes"]:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise ScaffoldError("KiCad project contains a malformed net class")
        if entry["name"] in result:
            raise ScaffoldError(f"duplicate net class {entry['name']!r}")
        result[entry["name"]] = entry
    return result


def _minimal_project(filename: str) -> dict:
    return {
        "board": {},
        "boards": [],
        "cvpcb": {},
        "erc": {},
        "libraries": {},
        "meta": {"filename": filename, "version": 1},
        "net_settings": {
            "classes": [{
                "bus_width": 12, "clearance": 0.2, "diff_pair_gap": 0.25,
                "diff_pair_via_gap": 0.25, "diff_pair_width": 0.2,
                "line_style": 0, "microvia_diameter": 0.3,
                "microvia_drill": 0.1, "name": "Default",
                "pcb_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647,
                "schematic_color": "rgba(0, 0, 0, 0.000)",
                "track_width": 0.2, "tuning_profile": "",
                "via_diameter": 0.6, "via_drill": 0.3, "wire_width": 6,
            }],
            "meta": {"version": 5}, "net_colors": None,
            "netclass_assignments": {}, "netclass_patterns": [],
        },
        "pcbnew": {}, "schematic": {}, "text_variables": {},
    }


def _new_class(name: str, style: dict, default: dict | None) -> dict:
    entry = deepcopy(default) if isinstance(default, dict) else {}
    entry.update({
        "name": name,
        "priority": 0,
        "track_width": style["track_width_nm"] / 1_000_000,
        "clearance": style["clearance_nm"] / 1_000_000,
        "via_diameter": style["via_diameter_nm"] / 1_000_000,
        "via_drill": style["via_drill_nm"] / 1_000_000,
    })
    return entry


def _merge_new_class(project: dict, class_name: str, nets: list[str], style: dict) -> dict:
    result = deepcopy(project)
    settings = result.setdefault("net_settings", {})
    classes = settings.setdefault("classes", [])
    by_name = _class_map(result)
    if class_name in by_name:
        raise ScaffoldError(
            f"net class {class_name!r} already exists; use --use-net-class"
        )
    classes.append(_new_class(class_name, style, by_name.get("Default")))
    assignments = settings.get("netclass_assignments")
    if assignments is None:
        assignments = {}
        settings["netclass_assignments"] = assignments
    if not isinstance(assignments, dict):
        raise ScaffoldError("netclass_assignments is not an object")
    for net in nets:
        existing = assignments.get(net) or []
        if existing and existing != ["Default"]:
            raise ScaffoldError(
                f"net {net!r} already has explicit non-default assignment {existing}"
            )
        assignments[net] = [class_name]
    settings.setdefault("netclass_patterns", [])
    settings.setdefault("meta", {"version": 5})
    settings.setdefault("net_colors", None)
    return result


def _project_text(project: dict) -> str:
    return json.dumps(project, ensure_ascii=False, indent=2) + "\n"


def _source_declaration(root: Path, role: str, path: Path, *, planned_sha: str | None = None) -> dict:
    relative = _project_relative(root, path, f"source {role}")
    if planned_sha is not None:
        kind, digest = "file", planned_sha
    elif path.is_file():
        kind, digest = "file", declared_source_digest(path, "file")
    elif path.is_dir():
        kind, digest = "directory-recursive", declared_source_digest(path, "directory-recursive")
    else:
        raise ScaffoldError(f"source is missing: {path}")
    return {"role": role, "kind": kind, "path": relative, "sha256": digest}


def _parse_source(raw: str, root: Path) -> tuple[str, Path]:
    role, separator, value = raw.partition("=")
    if not separator or not role or not value:
        raise ScaffoldError("--source must be ROLE=PATH")
    if not re.fullmatch(r"[A-Za-z0-9_.:-]+", role):
        raise ScaffoldError(f"invalid source role {role!r}")
    path = Path(value).expanduser()
    if not path.is_absolute():
        path = root / path
    return role, path.resolve()


def _related_project_sources(board: Path, root: Path) -> list[tuple[str, Path]]:
    result = []
    for role, suffix in (
        ("source-board", ".kicad_pcb"), ("project", ".kicad_pro"),
        ("schematic", ".kicad_sch"), ("rules", ".kicad_dru"),
    ):
        path = board.with_suffix(suffix)
        if path.exists():
            result.append((role, path))
    for table_name in ("fp-lib-table", "sym-lib-table"):
        table = root / table_name
        if not table.is_file():
            continue
        result.append(("project-table:" + table_name, table))
        text = table.read_text(encoding="utf-8")
        parsed_uris = re.findall(r'\(uri\s+"([^"\r\n]+)"\)', text)
        if text.count("(uri") != len(parsed_uris):
            raise ScaffoldError(f"{table} contains an unparseable/non-quoted library URI")
        unsupported = [
            uri for uri in parsed_uris
            if not uri.startswith("${KIPRJMOD}/")
        ]
        if unsupported:
            raise ScaffoldError(
                f"{table} contains non-hermetic library URI(s): {sorted(unsupported)}"
            )
        for relative in re.findall(r'\(uri\s+"\$\{KIPRJMOD\}/([^"\r\n]+)"\)', text):
            candidate = (root / relative).resolve()
            _project_relative(root, candidate, "KIPRJMOD library")
            result.append(("project-resource:" + relative, candidate))
    return result


def _path_overlaps(path: Path, other: Path) -> bool:
    path, other = path.resolve(), other.resolve()
    return path == other or path.is_relative_to(other) or other.is_relative_to(path)


def _guard_write_target(
    target: Path,
    *,
    protected_files: list[Path],
    protected_directories: list[Path],
    label: str,
) -> None:
    resolved = target.resolve()
    if any(resolved == path.resolve() for path in protected_files) or any(
        resolved == directory.resolve() or resolved.is_relative_to(directory.resolve())
        for directory in protected_directories
    ):
        raise ScaffoldError(f"{label} collides with immutable project/source context: {resolved}")


def _asset_operations(mode: str) -> tuple[list[dict], dict]:
    selected_adapter = "snapshot-adapter" if mode == "board-snapshot" else "generator-adapter"
    specs = {
        "adapter": (ASSETS[selected_adapter], "autoroute_adapter.py", "kicad-autoroute-adapter-v1"),
        "applicator": (ASSETS["applicator"], "autoroute_apply.py", "kicad-autoroute-applicator-v1"),
        "audit": (ASSETS["audit"], "autoroute_audit.py", "kicad-autoroute-audit-v1"),
    }
    operations, tools = [], {}
    for name, (asset, destination, protocol) in specs.items():
        if not asset.is_file():
            raise ScaffoldError(f"skill asset is missing: {asset}")
        digest = sha256_path(asset)
        operations.append({
            "kind": "copy-asset-create-only", "asset": str(asset),
            "asset_sha256": digest, "path": destination,
        })
        tools[name] = {"path": destination, "protocol": protocol, "sha256": digest}
    return operations, tools


def _find_kicad_python(explicit: str | None) -> Path:
    try:
        from kicad_route_candidate import find_kicad_python
        path, _ = find_kicad_python(explicit)
        return path
    except Exception as exc:
        raise ScaffoldError(f"cannot find a KiCad Python with pcbnew: {exc}") from exc


def _worker(
    kicad_python: Path,
    mode: str,
    board: Path,
    output: Path,
    extra: list[str] | None = None,
    timeout_seconds: int = 300,
) -> dict:
    command = [str(kicad_python), str(Path(__file__).resolve()), "--_pcb-worker", mode, str(board), str(output)]
    command.extend(extra or [])
    worker_env = os.environ.copy()
    worker_env["KICAD_AUTOROUTE_SCAFFOLD_WORKER_ROOT"] = str(output.parent.resolve())
    try:
        completed = subprocess.run(
            command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            check=False, env=worker_env, timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScaffoldError(
            f"KiCad board inspection timed out after {timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0:
        raise ScaffoldError(
            f"KiCad board inspection failed ({completed.returncode}): {completed.stderr.strip()}"
        )
    try:
        return json.loads(output.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ScaffoldError(f"KiCad worker emitted no valid report: {exc}") from exc


def _plan(args) -> int:
    board = Path(args.board).expanduser().resolve()
    if not board.is_file() or board.suffix != ".kicad_pcb":
        raise ScaffoldError(f"board is not a .kicad_pcb file: {board}")
    root = Path(args.project_root).expanduser().resolve() if args.project_root else board.parent.resolve()
    _project_relative(root, board, "board")
    if bool(args.use_net_class) == bool(args.create_net_class):
        raise ScaffoldError("choose exactly one of --use-net-class or --create-net-class")
    if bool(args.selected_scope_routine) == bool(args.project_audited):
        raise ScaffoldError(
            "choose exactly one of --selected-scope-routine or --project-audited"
        )
    if not args.layer or len(args.layer) != len(set(args.layer)):
        raise ScaffoldError("provide one or more unique --layer values")

    kicad_python = _find_kicad_python(args.kicad_python)
    with tempfile.TemporaryDirectory(prefix="kicad-autoroute-plan-") as raw:
        scratch = Path(raw)
        inspect_path = scratch / "inspect.json"
        inspection = _worker(
            kicad_python, "inspect", board, inspect_path,
            timeout_seconds=args.timeout_seconds,
        )
        migration_probe = _worker(
            kicad_python, "probe", board, scratch / "probe.json",
            [str(scratch / board.name)],
            args.timeout_seconds,
        )
    if migration_probe["needs_migration"]:
        raise ScaffoldError(
            "NEEDS_MIGRATION: " + str(migration_probe.get("reason") or "KiCad changes the source board")
        )
    available_nets = set(inspection["net_to_class"])
    unknown_layers = sorted(set(args.layer) - set(inspection["copper_layers"]))
    if unknown_layers:
        raise ScaffoldError(
            f"selected layers are not enabled: {unknown_layers}; available {inspection['copper_layers']}"
        )

    project_path = board.with_suffix(".kicad_pro")
    schematic_path = board.with_suffix(".kicad_sch")
    board_only = bool(args.board_only_authority)
    if not board_only and (not project_path.is_file() or not schematic_path.is_file()):
        raise ScaffoldError(
            "parity authority requires same-stem .kicad_pro and .kicad_sch; "
            "use --board-only-authority only when the PCB is intentionally authoritative"
        )
    if board_only and schematic_path.exists():
        raise ScaffoldError(
            "--board-only-authority conflicts with an existing same-stem schematic"
        )

    project_before = _read_project(project_path) if project_path.is_file() else _minimal_project(project_path.name)
    classes = _class_map(project_before)
    project_after = project_before
    project_operation = None
    if args.use_net_class:
        class_name = args.use_net_class
        if not project_path.is_file():
            raise ScaffoldError("--use-net-class requires an existing .kicad_pro")
        if class_name not in classes:
            raise ScaffoldError(f"project has no net class {class_name!r}")
        mapping = {
            net: class_value
            for net, class_value in inspection["net_to_class"].items()
            if class_value == class_name
        }
        if not mapping:
            raise ScaffoldError(f"KiCad resolves no board nets to class {class_name!r}")
        style = _style_from_class(classes[class_name], class_name)
    else:
        class_name = args.create_net_class
        nets = sorted(set(args.net or []))
        if not nets:
            raise ScaffoldError("--create-net-class requires one or more explicit --net values")
        missing_nets = sorted(set(nets) - available_nets)
        if missing_nets:
            raise ScaffoldError(f"selected nets are absent from the board: {missing_nets}")
        if class_name in classes:
            raise ScaffoldError(
                f"net class {class_name!r} already exists; use --use-net-class"
            )
        conflicts = {
            net: inspection["net_to_class"][net]
            for net in nets if inspection["net_to_class"][net] != "Default"
        }
        if conflicts:
            raise ScaffoldError(f"selected nets already resolve to non-default classes: {conflicts}")
        style = {
            "track_width_nm": _nm(args.track_width_mm, "--track-width-mm"),
            "clearance_nm": _nm(args.clearance_mm, "--clearance-mm"),
            "via_diameter_nm": _nm(args.via_diameter_mm, "--via-diameter-mm"),
            "via_drill_nm": _nm(args.via_drill_mm, "--via-drill-mm"),
        }
        if style["via_drill_nm"] >= style["via_diameter_nm"]:
            raise ScaffoldError("via drill must be smaller than via diameter")
        mapping = {net: class_name for net in nets}
        project_after = _merge_new_class(project_before, class_name, nets, style)
        after_text = _project_text(project_after)
        project_operation = {
            "kind": "replace-project-approved",
            "path": _project_relative(root, project_path, "project"),
            "before_sha256": sha256_path(project_path) if project_path.is_file() else None,
            "after_sha256": __import__("hashlib").sha256(after_text.encode()).hexdigest(),
            "after_text": after_text,
            "semantic_change": {
                "create_net_class": class_name, "assign_nets": nets,
                "preserve_unrelated_top_level_keys": sorted(project_before),
            },
        }
        with tempfile.TemporaryDirectory(prefix="kicad-autoroute-project-merge-") as raw:
            merge_root = Path(raw)
            merge_board = merge_root / board.name
            shutil.copy2(board, merge_board)
            (merge_root / project_path.name).write_text(after_text, encoding="utf-8")
            if schematic_path.is_file():
                shutil.copy2(schematic_path, merge_root / schematic_path.name)
            merged = _worker(
                kicad_python, "inspect", merge_board, merge_root / "inspect.json",
                timeout_seconds=args.timeout_seconds,
            )
            live_selected = {
                net: class_value
                for net, class_value in merged["net_to_class"].items()
                if class_value == class_name
            }
            if live_selected != mapping:
                raise ScaffoldError(
                    "temporary KiCad project merge does not resolve the exact reviewed mapping: "
                    f"expected {mapping}, live {live_selected}"
                )

    selected_nets = sorted(mapping)
    reset_manifest = None
    if args.reset_all_selected_routing:
        records = [
            _route_record(item)
            for item in inspection["routes"]
            if item["route"]["net"] in set(selected_nets)
        ]
        records.sort(key=lambda item: item["uuid"])
        if len({item["uuid"] for item in records}) != len(records):
            raise ScaffoldError("selected route UUIDs are not unique")
        geometry = [_route_record(item)["route"] for item in inspection["routes"] if item["route"]["net"] in set(selected_nets)]
        duplicate_geometry = [key for key, count in collections.Counter(json.dumps(item, sort_keys=True) for item in geometry).items() if count > 1]
        if duplicate_geometry:
            raise ScaffoldError("selected routing contains coincident duplicate primitives")
        reset_manifest = {
            "schema": RESET_SCHEMA,
            "policy": "all-selected-routing",
            "source_board": _project_relative(root, board, "source board"),
            "source_board_sha256": sha256_path(board),
            "selected_nets": selected_nets,
            "items": records,
            "aggregate_sha256": canonical_json_sha256(records),
        }

    operations, tools = _asset_operations(args.mode)
    if project_operation is not None:
        operations.append(project_operation)
    if reset_manifest is not None:
        operations.append({
            "kind": "write-json-create-only", "path": "autoroute-route-reset.json",
            "content": reset_manifest, "sha256": canonical_json_sha256(reset_manifest),
        })

    sources = []
    seen_source_paths = set()
    candidates = []
    if args.mode == "board-snapshot":
        candidates.extend(_related_project_sources(board, root))
        candidates.extend(_parse_source(value, root) for value in (args.source or []))
        if project_operation is not None and not any(path.resolve() == project_path.resolve() for _, path in candidates):
            candidates.append(("project", project_path))
    else:
        if not args.source:
            raise ScaffoldError("generator-adapter mode requires at least one explicit --source ROLE=PATH")
        candidates.extend(_parse_source(value, root) for value in args.source)
        if project_path.is_file():
            candidates.append(("project-context", project_path))
        if not board_only and schematic_path.is_file():
            candidates.append(("schematic-context", schematic_path))
    planned_project_sha = project_operation["after_sha256"] if project_operation else None
    for role, path in candidates:
        relative = _project_relative(root, path, f"source {role}")
        if relative in seen_source_paths:
            continue
        seen_source_paths.add(relative)
        sources.append(_source_declaration(
            root, role, path,
            planned_sha=(planned_project_sha if path.resolve() == project_path.resolve() else None),
        ))
    if not sources:
        raise ScaffoldError("scaffold has no immutable source declarations")
    preconditions = []
    for role, path in candidates:
        relative = _project_relative(root, path, f"precondition {role}")
        if any(item["path"] == relative for item in preconditions):
            continue
        if path.is_file():
            kind = "file"
            allowed = [sha256_path(path)]
            allow_missing = False
        elif path.is_dir():
            kind = "directory-recursive"
            allowed = [declared_source_digest(path, kind)]
            allow_missing = False
        elif project_operation is not None and path.resolve() == project_path.resolve():
            kind = "file"
            allowed = []
            allow_missing = True
        else:
            raise ScaffoldError(f"planned source is missing: {path}")
        if project_operation is not None and path.resolve() == project_path.resolve():
            allowed.append(project_operation["after_sha256"])
        preconditions.append({
            "role": role, "kind": kind, "path": relative,
            "allowed_sha256": sorted(set(allowed)), "allow_missing": allow_missing,
        })

    policy = "routine" if args.selected_scope_routine else "project-audited"
    audit_commands = [] if policy == "routine" else [{
        "interpreter": "kicad_python",
        "argv": ["autoroute_audit.py", "--phase", "seed", "--board", "{board}"],
        "timeout_seconds": args.audit_timeout_seconds,
        "calibration_marker": "AUTOROUTE_AUDIT_CALIBRATION_PASSED",
    }]
    final_audits = [] if policy == "routine" else [{
        "interpreter": "kicad_python",
        "argv": ["autoroute_audit.py", "--phase", "final", "--board", "{board}"],
        "timeout_seconds": args.audit_timeout_seconds,
        "calibration_marker": "AUTOROUTE_AUDIT_CALIBRATION_PASSED",
    }]
    source_board = _project_relative(root, board, "source board") if args.mode == "board-snapshot" else None
    project_file = _project_relative(root, project_path, "project")
    schematic_file = None if board_only else _project_relative(root, schematic_path, "schematic")
    output_root = "build/autoroute"
    config = {
        "schema": CONFIG_SCHEMA_V2,
        "backend": BACKEND_ID,
        "project": {
            "root": ".", "mode": args.mode, "board_basename": board.name,
            "schematic_authority": "board-only" if board_only else "parity",
            "source_board": source_board, "project_file": project_file,
            "schematic_file": schematic_file,
        },
        "sources": sorted(sources, key=lambda item: (item["role"], item["path"])),
        "tools": tools,
        "scope": {
            "net_classes": [class_name], "net_to_class": dict(sorted(mapping.items())),
            "layers": args.layer, "styles": {class_name: style},
            "selected_scope_policy": policy,
        },
        "reset": {
            "policy": "all-selected-routing" if reset_manifest else "none",
            "manifest": "autoroute-route-reset.json" if reset_manifest else None,
            "manifest_sha256": canonical_json_sha256(reset_manifest) if reset_manifest else None,
        },
        "limits": {
            "max_passes": args.max_passes, "max_threads": args.max_threads,
            "timeout_seconds": args.timeout_seconds,
            "audit_timeout_seconds": args.audit_timeout_seconds,
        },
        "seed": {"drc_baseline": "autoroute-seed-drc.json", "audit_commands": audit_commands},
        "final": {"audit_commands": final_audits},
        "promotion": {"manifest": "routes.json"},
        "outputs": {
            "root": output_root,
            "seed": f"{output_root}/seed/{board.name}",
            "final": f"{output_root}/final/{board.name}",
        },
    }
    operations.append({
        "kind": "write-json-create-only", "path": "autoroute.json",
        "content": config, "sha256": canonical_json_sha256(config),
    })
    # Validate the complete contract before an approval can authorize any
    # project mutation.  The v2 loader is structural and digest-declarative;
    # source existence is rechecked by apply/check.
    with tempfile.TemporaryDirectory(prefix="kicad-autoroute-config-validate-") as raw:
        validation_config = Path(raw) / "autoroute.json"
        validation_config.write_bytes(canonical_json_bytes(config))
        load_config(validation_config)

    source_files = [
        path.resolve() for _, path in candidates if path.is_file()
    ]
    source_directories = [
        path.resolve() for _, path in candidates if path.is_dir()
    ]
    operation_targets = []
    for operation in operations:
        target = (root / operation["path"]).resolve()
        if target in operation_targets:
            raise ScaffoldError(f"multiple scaffold operations target {target}")
        operation_targets.append(target)
        if operation["kind"] == "replace-project-approved" and target == project_path.resolve():
            continue
        if any(_path_overlaps(target, path) for path in source_files) or any(
            target == directory or target.is_relative_to(directory)
            for directory in source_directories
        ):
            raise ScaffoldError(
                f"scaffold operation {target} overlaps an immutable source"
            )
    plan = {
        "schema": PLAN_SCHEMA, "project_root": str(root),
        "source_board_sha256": sha256_path(board), "mode": args.mode,
        "schematic_authority": config["project"]["schematic_authority"],
        "selected_scope_policy": policy,
        "preconditions": sorted(preconditions, key=lambda item: (item["role"], item["path"])),
        "operations": operations,
        "review": {
            "selected_nets": selected_nets, "net_class": class_name,
            "layers": args.layer, "style": style,
            "reset_items": len(reset_manifest["items"]) if reset_manifest else 0,
            "migration_probe": migration_probe,
            "permanent_waiver": (
                "schematic parity and ERC unavailable; PCB is authoritative"
                if board_only else None
            ),
        },
    }
    output = Path(args.output).expanduser().resolve()
    _guard_write_target(
        output,
        protected_files=source_files + operation_targets,
        protected_directories=source_directories,
        label="plan output",
    )
    write_json_atomic(output, plan)
    print(f"PLAN_SHA256: {sha256_path(output)}")
    print(f"PLAN_WRITTEN: {output}")
    return 0


def _operation_bytes(operation: dict) -> bytes:
    if operation["kind"] == "copy-asset-create-only":
        asset = Path(operation["asset"])
        if not asset.is_file() or sha256_path(asset) != operation["asset_sha256"]:
            raise ScaffoldError(f"skill asset changed since planning: {asset}")
        return asset.read_bytes()
    if operation["kind"] == "write-json-create-only":
        data = canonical_json_bytes(operation["content"])
        if __import__("hashlib").sha256(data).hexdigest() != operation["sha256"]:
            raise ScaffoldError(f"planned JSON digest is inconsistent: {operation['path']}")
        return data
    if operation["kind"] == "replace-project-approved":
        data = operation["after_text"].encode("utf-8")
        if __import__("hashlib").sha256(data).hexdigest() != operation["after_sha256"]:
            raise ScaffoldError("planned project replacement digest is inconsistent")
        return data
    if operation["kind"] == "replace-config-approved":
        data = canonical_json_bytes(operation["content"])
        if __import__("hashlib").sha256(data).hexdigest() != operation["after_sha256"]:
            raise ScaffoldError("planned config replacement digest is inconsistent")
        return data
    raise ScaffoldError(f"unsupported plan operation {operation.get('kind')!r}")


def _repin_plan(args) -> int:
    config_path = Path(args.config).expanduser().resolve()
    config = load_config(config_path)
    if config["schema"] != CONFIG_SCHEMA_V2:
        raise ScaffoldError("tool repinning requires kicad-autoroute-config-v2")
    root = Path(config["project_root"])
    raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
    before_sha = sha256_path(config_path)
    preconditions = [{
        "role": "autoroute-config", "kind": "file",
        "path": _project_relative(root, config_path, "config"),
        "allowed_sha256": [before_sha], "allow_missing": False,
    }]
    for source in config["sources"]:
        path = root / source["path"]
        actual = declared_source_digest(path, source["kind"])
        if actual != source["sha256"]:
            raise ScaffoldError(f"cannot repin with stale source {source['path']}")
        preconditions.append({
            "role": "source:" + source["role"], "kind": source["kind"],
            "path": source["path"], "allowed_sha256": [actual],
            "allow_missing": False,
        })
    review = {}
    for name in ("adapter", "applicator", "audit"):
        path = root / config["tools"][name]["path"]
        if not path.is_file() or path.is_symlink():
            raise ScaffoldError(f"cannot repin missing/linked {name}: {path}")
        actual = sha256_path(path)
        review[name] = {
            "path": config["tools"][name]["path"],
            "before_sha256": config["tools"][name]["sha256"],
            "after_sha256": actual,
        }
        raw["tools"][name]["sha256"] = actual
        preconditions.append({
            "role": "tool:" + name, "kind": "file",
            "path": config["tools"][name]["path"],
            "allowed_sha256": [actual], "allow_missing": False,
        })
    after_sha = canonical_json_sha256(raw)
    operation = {
        "kind": "replace-config-approved",
        "path": _project_relative(root, config_path, "config"),
        "before_sha256": before_sha,
        "after_sha256": after_sha,
        "content": raw,
    }
    with tempfile.TemporaryDirectory(prefix="kicad-autoroute-repin-validate-") as temp:
        probe = Path(temp) / "autoroute.json"
        probe.write_bytes(canonical_json_bytes(raw))
        load_config(probe)
    plan = {
        "schema": PLAN_SCHEMA,
        "project_root": str(root),
        "mode": "repin-tools",
        "preconditions": sorted(preconditions, key=lambda item: (item["role"], item["path"])),
        "operations": [operation],
        "review": {"tools": review},
    }
    output = Path(args.output).expanduser().resolve()
    source_directories = [
        root / item["path"] for item in config["sources"]
        if item["kind"] == "directory-recursive"
    ]
    _guard_write_target(
        output,
        protected_files=[
            config_path, *(root / item["path"] for item in config["sources"] if item["kind"] == "file"),
            *(root / config["tools"][name]["path"] for name in ("adapter", "applicator", "audit")),
        ],
        protected_directories=source_directories,
        label="repin plan output",
    )
    write_json_atomic(output, plan)
    print(f"PLAN_SHA256: {sha256_path(output)}")
    print(f"PLAN_WRITTEN: {output}")
    return 0


def _apply(args) -> int:
    plan_path = Path(args.plan).expanduser().resolve()
    actual_plan_sha = sha256_path(plan_path)
    if actual_plan_sha != args.approve_plan_sha256:
        raise ScaffoldError(
            f"plan approval digest mismatch: approved {args.approve_plan_sha256}, actual {actual_plan_sha}"
        )
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    if not isinstance(plan, dict) or plan.get("schema") != PLAN_SCHEMA:
        raise ScaffoldError("unsupported scaffold plan")
    root = Path(plan["project_root"]).resolve()
    operations = plan.get("operations")
    if not isinstance(operations, list) or not operations:
        raise ScaffoldError("scaffold plan has no operations")

    preconditions = plan.get("preconditions")
    if not isinstance(preconditions, list) or not preconditions:
        raise ScaffoldError("scaffold plan has no source preconditions")
    for item in preconditions:
        if not isinstance(item, dict) or set(item) != {
            "role", "kind", "path", "allowed_sha256", "allow_missing",
        }:
            raise ScaffoldError("malformed scaffold source precondition")
        path = (root / item["path"]).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise ScaffoldError(f"precondition escapes project root: {path}") from exc
        if not path.exists():
            if item["allow_missing"]:
                continue
            raise ScaffoldError(f"planned source disappeared: {path}")
        actual = declared_source_digest(path, item["kind"])
        if actual not in item["allowed_sha256"]:
            raise ScaffoldError(f"planned source changed since approval: {path}")

    protected_files = [
        (root / item["path"]).resolve()
        for item in preconditions if item["kind"] == "file"
    ]
    protected_directories = [
        (root / item["path"]).resolve()
        for item in preconditions if item["kind"] == "directory-recursive"
    ]

    prepared = []
    for operation in operations:
        if not isinstance(operation, dict) or not isinstance(operation.get("path"), str):
            raise ScaffoldError("malformed scaffold operation")
        target = (root / operation["path"]).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ScaffoldError(f"operation escapes project root: {target}") from exc
        approved_source_replacement = (
            operation["kind"] in {"replace-project-approved", "replace-config-approved"}
            and target in protected_files
        )
        if not approved_source_replacement and (
            target in protected_files
            or any(target == directory or target.is_relative_to(directory) for directory in protected_directories)
        ):
            raise ScaffoldError(f"scaffold operation overlaps immutable source: {target}")
        data = _operation_bytes(operation)
        after_sha = __import__("hashlib").sha256(data).hexdigest()
        if target.exists():
            current = sha256_path(target)
            if current == after_sha:
                prepared.append((operation, target, data, True))
                continue
            if operation["kind"] not in {"replace-project-approved", "replace-config-approved"}:
                raise ScaffoldError(f"create-only target already exists with different content: {target}")
            if current != operation.get("before_sha256"):
                raise ScaffoldError(f"project changed since plan approval: {target}")
        elif operation["kind"] in {"replace-project-approved", "replace-config-approved"} and operation.get("before_sha256") is not None:
            raise ScaffoldError(f"planned project source disappeared: {target}")
        prepared.append((operation, target, data, False))

    written = []
    backups = {}
    try:
        for operation, target, data, already_done in prepared:
            if already_done:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            if target.exists():
                backups[target] = target.read_bytes()
            temp = target.with_name("." + target.name + ".scaffold-tmp")
            temp.write_bytes(data)
            temp.replace(target)
            if sha256_path(target) != __import__("hashlib").sha256(data).hexdigest():
                raise ScaffoldError(f"post-write digest mismatch: {target}")
            written.append(target)
        live_config = load_config(root / "autoroute.json")
        build_v2_input_bundle(live_config)
    except Exception:
        for target in reversed(written):
            if target in backups:
                target.write_bytes(backups[target])
            elif target.exists():
                target.unlink()
        raise
    print(f"SCAFFOLD_APPLIED: {root}")
    return 0


def _adapter_describe(
    kicad_python: Path, adapter: Path, report: Path, timeout_seconds: int
) -> dict:
    try:
        completed = subprocess.run(
            [str(kicad_python), str(adapter), "describe", "--report", str(report)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ScaffoldError(
            f"adapter describe timed out after {timeout_seconds} seconds"
        ) from exc
    if completed.returncode != 0 or not report.is_file():
        raise ScaffoldError(f"adapter describe failed: {completed.stderr.strip()}")
    return json.loads(report.read_text(encoding="utf-8"))


def _guard_check_report(board: Path, config_path: Path, report_path: Path) -> None:
    protected_files = {
        board.resolve(), config_path.resolve(),
        board.with_suffix(".kicad_pro").resolve(),
        board.with_suffix(".kicad_sch").resolve(),
        board.with_suffix(".kicad_dru").resolve(),
        (board.parent / "fp-lib-table").resolve(),
        (board.parent / "sym-lib-table").resolve(),
    }
    protected_directories = set()
    if config_path.is_file():
        try:
            raw = json.loads(config_path.read_text(encoding="utf-8-sig"))
            root = config_path.parent.resolve()
            for source in raw.get("sources") or []:
                path = (root / str(source.get("path", ""))).resolve()
                if source.get("kind") == "directory-recursive":
                    protected_directories.add(path)
                else:
                    protected_files.add(path)
            for spec in (raw.get("tools") or {}).values():
                if isinstance(spec, dict) and spec.get("path"):
                    protected_files.add((root / spec["path"]).resolve())
            project = raw.get("project") or {}
            for key in ("source_board", "project_file", "schematic_file"):
                if project.get(key):
                    protected_files.add((root / project[key]).resolve())
            reset = raw.get("reset") or {}
            if reset.get("manifest"):
                protected_files.add((root / reset["manifest"]).resolve())
            seed = raw.get("seed") or {}
            promotion = raw.get("promotion") or {}
            outputs = raw.get("outputs") or {}
            for relative in (
                seed.get("drc_baseline"), promotion.get("manifest"),
                outputs.get("seed"), outputs.get("final"),
            ):
                if relative:
                    protected_files.add((root / relative).resolve())
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            # The known board/config paths are still protected; config parsing
            # will report the structural problem without writing over them.
            pass
    _guard_write_target(
        report_path,
        protected_files=list(protected_files),
        protected_directories=list(protected_directories),
        label="check report",
    )


def _check(args) -> int:
    board_arg = Path(args.board).expanduser().resolve()
    config_path = Path(args.config).expanduser().resolve() if args.config else board_arg.parent / "autoroute.json"
    report_path = Path(args.report).expanduser().resolve()
    _guard_check_report(board_arg, config_path, report_path)
    report = {"schema": CHECK_SCHEMA, "status": "BLOCKED_CONFIGURATION", "config": str(config_path)}
    exit_code = 2
    try:
        config = load_config(config_path)
        if config["schema"] != CONFIG_SCHEMA_V2:
            raise ScaffoldError("scaffold check requires kicad-autoroute-config-v2")
        root = Path(config["project_root"])
        report["project"] = config["project"]
        report["scope"] = config["scope"]
        expected_board_arg = (root / config["project"]["board_basename"]).resolve()
        if board_arg != expected_board_arg:
            report.update({
                "status": "BLOCKED_PROJECT_CONTEXT",
                "reason": (
                    "BOARD does not match project_root/project.board_basename: "
                    f"expected {expected_board_arg}, got {board_arg}"
                ),
            })
            write_json_atomic(report_path, report)
            return 3
        if config["project"]["schematic_authority"] == "board-only":
            report["permanent_waiver"] = (
                "schematic parity and ERC unavailable; PCB is authoritative"
            )
        context_paths = [root / config["project"]["project_file"]]
        if config["project"]["mode"] == "board-snapshot":
            context_paths.append(root / config["project"]["source_board"])
        if config["project"]["schematic_authority"] == "parity":
            context_paths.append(root / config["project"]["schematic_file"])
        missing_context = [str(path) for path in context_paths if not path.is_file()]
        if missing_context:
            report.update({
                "status": "BLOCKED_PROJECT_CONTEXT",
                "reason": "missing declared project context",
                "missing": missing_context,
            })
            write_json_atomic(report_path, report)
            return 3
        try:
            bundle = build_v2_input_bundle(config)
        except AutorouteError as exc:
            report.update({"status": "STALE_SOURCE", "reason": str(exc)})
            write_json_atomic(report_path, report)
            return 3
        report["input_bundle_sha256"] = canonical_json_sha256(bundle)
        kicad_python = _find_kicad_python(args.kicad_python)
        report["kicad_python"] = str(kicad_python)
        with tempfile.TemporaryDirectory(prefix="kicad-autoroute-check-") as raw:
            scratch = Path(raw)
            adapter_report_path = scratch / "adapter.json"
            adapter = root / config["tools"]["adapter"]["path"]
            adapter_report = _adapter_describe(
                kicad_python, adapter, adapter_report_path,
                config["limits"]["audit_timeout_seconds"],
            )
            report["adapter"] = adapter_report
            if (
                adapter_report.get("protocol") != config["tools"]["adapter"]["protocol"]
                or adapter_report.get("mode") != config["project"]["mode"]
                or set(adapter_report.get("operations") or []) != {"seed", "final"}
            ):
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "adapter protocol/mode/operations differ from config",
                })
                write_json_atomic(report_path, report)
                return 3
            if adapter_report.get("ready") is not True:
                report.update({"status": "BLOCKED_ADAPTER", "reason": adapter_report.get("reason")})
                write_json_atomic(report_path, report)
                return 3
            if config["project"]["mode"] == "board-snapshot":
                source_board = root / config["project"]["source_board"]
                inspection = _worker(
                    kicad_python, "inspect", source_board, scratch / "source.json",
                    timeout_seconds=config["limits"]["timeout_seconds"],
                )
                reset_nets = set(config["scope"]["net_to_class"]) if config["reset"]["policy"] != "none" else set()
                remaining = [item for item in inspection["routes"] if item["route"]["net"] not in reset_nets]
                unsupported = []
                for item in remaining:
                    route = item["route"]
                    if route.get("kind") == "segment":
                        supported = (
                            route.get("start_nm") != route.get("end_nm")
                            and int(route.get("width_nm", 0)) > 0
                            and route.get("layer") in inspection["copper_layers"]
                        )
                    elif route.get("kind") == "via":
                        supported = (
                            route.get("layers") == ["F.Cu", "B.Cu"]
                            and item.get("primitive_type") in (0, 4)
                            and int(route.get("drill_nm", 0)) > 0
                            and int(route.get("diameter_nm", 0)) > int(route.get("drill_nm", 0))
                        )
                    else:
                        supported = False
                    if not supported:
                        unsupported.append(item)
                if unsupported:
                    report.update({"status": "BLOCKED_PRIMITIVES", "unsupported": unsupported})
                    write_json_atomic(report_path, report)
                    return 3
                probe_board = scratch / source_board.name
                probe = _worker(
                    kicad_python, "probe", source_board, scratch / "probe.json",
                    [str(probe_board)], config["limits"]["timeout_seconds"],
                )
                report["migration_probe"] = probe
                if probe["needs_migration"]:
                    report.update({"status": "NEEDS_MIGRATION", "reason": probe["reason"]})
                    write_json_atomic(report_path, report)
                    return 3
            seed_dir = scratch / "seed"
            seed_report_path = seed_dir / "adapter-seed-report.json"
            try:
                completed = subprocess.run(
                    [str(kicad_python), str(adapter), "seed", "--output-dir", str(seed_dir), "--report", str(seed_report_path)],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                    check=False, timeout=config["limits"]["timeout_seconds"],
                )
            except subprocess.TimeoutExpired:
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "adapter seed operation timed out",
                })
                write_json_atomic(report_path, report)
                return 3
            if completed.returncode != 0 or not seed_report_path.is_file():
                report.update({"status": "BLOCKED_ADAPTER", "reason": completed.stderr.strip() or "seed operation emitted no report"})
                write_json_atomic(report_path, report)
                return 3
            seed_report = json.loads(seed_report_path.read_text(encoding="utf-8"))
            report["seed"] = seed_report
            if seed_report.get("status") != "PASS":
                report.update({"status": "BLOCKED_ADAPTER", "reason": "seed report did not pass"})
                write_json_atomic(report_path, report)
                return 3
            seed_board = seed_dir / config["project"]["board_basename"]
            required_seed_context = [seed_board, seed_board.with_suffix(".kicad_pro")]
            seed_schematic = seed_board.with_suffix(".kicad_sch")
            if config["project"]["schematic_authority"] == "parity":
                required_seed_context.append(seed_schematic)
            elif seed_schematic.exists():
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "board-only seed unexpectedly contains a schematic",
                })
                write_json_atomic(report_path, report)
                return 3
            missing_seed_context = [
                str(path) for path in required_seed_context if not path.is_file()
            ]
            if missing_seed_context:
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "seed omitted required same-stem project context",
                    "missing": missing_seed_context,
                })
                write_json_atomic(report_path, report)
                return 3
            seed_integrity_sha256 = sha256_path(seed_board)
            seed_inspection = _worker(
                kicad_python, "inspect", seed_board, scratch / "seed-inspect.json",
                timeout_seconds=config["limits"]["timeout_seconds"],
            )
            report["seed_inspection"] = {
                "pcbnew": seed_inspection["pcbnew"],
                "copper_layers": seed_inspection["copper_layers"],
                "route_count": len(seed_inspection["routes"]),
            }
            if seed_inspection["copper_layers"] != config["scope"]["layers"]:
                # The board may enable more layers than the selected routing
                # scope; require the selected list to be an ordered subset.
                if not set(config["scope"]["layers"]).issubset(seed_inspection["copper_layers"]):
                    report.update({
                        "status": "BLOCKED_ADAPTER",
                        "reason": "seed does not enable every configured copper layer",
                    })
                    write_json_atomic(report_path, report)
                    return 3
            live_selected = {
                net: class_name
                for net, class_name in seed_inspection["net_to_class"].items()
                if class_name in config["scope"]["net_classes"]
            }
            if live_selected != config["scope"]["net_to_class"]:
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "seed effective net-class inventory differs from frozen scope",
                })
                write_json_atomic(report_path, report)
                return 3
            try:
                project_scope = resolve_project_netclasses(
                    seed_board.with_suffix(".kicad_pro"),
                    config["scope"]["net_classes"],
                    expected_mapping=config["scope"]["net_to_class"],
                )
                verify_project_styles(config, project_scope)
            except AutorouteError as exc:
                report.update({"status": "BLOCKED_ADAPTER", "reason": str(exc)})
                write_json_atomic(report_path, report)
                return 3
            unsupported_seed = []
            for item in seed_inspection["routes"]:
                route = item["route"]
                if route.get("kind") == "segment":
                    supported = (
                        route.get("start_nm") != route.get("end_nm")
                        and int(route.get("width_nm", 0)) > 0
                        and route.get("layer") in seed_inspection["copper_layers"]
                    )
                elif route.get("kind") == "via":
                    supported = (
                        route.get("layers") == ["F.Cu", "B.Cu"]
                        and item.get("primitive_type") in (0, 4)
                        and int(route.get("drill_nm", 0)) > 0
                        and int(route.get("diameter_nm", 0)) > int(route.get("drill_nm", 0))
                    )
                else:
                    supported = False
                if not supported:
                    unsupported_seed.append(item)
            if unsupported_seed:
                report.update({"status": "BLOCKED_PRIMITIVES", "unsupported": unsupported_seed})
                write_json_atomic(report_path, report)
                return 3
            seed_probe = _worker(
                kicad_python, "probe", seed_board,
                scratch / "seed-probe.json", [str(scratch / "probe-seed" / seed_board.name)],
                config["limits"]["timeout_seconds"],
            )
            report["seed_migration_probe"] = seed_probe
            if seed_probe["needs_migration"]:
                report.update({"status": "NEEDS_MIGRATION", "reason": seed_probe["reason"]})
                write_json_atomic(report_path, report)
                return 3
            if build_v2_input_bundle(config) != bundle:
                report.update({"status": "STALE_SOURCE", "reason": "source bundle changed during adapter seed"})
                write_json_atomic(report_path, report)
                return 3
            if config["scope"]["selected_scope_policy"] == "project-audited":
                audit = root / config["tools"]["audit"]["path"]
                seed_before_audit = sha256_path(seed_board)
                try:
                    audited = subprocess.run(
                        [str(kicad_python), str(audit), "--phase", "seed", "--board", str(seed_board)],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        check=False, timeout=config["limits"]["audit_timeout_seconds"],
                    )
                except subprocess.TimeoutExpired:
                    report.update({"status": "BLOCKED_AUDIT", "reason": "project audit timed out"})
                    write_json_atomic(report_path, report)
                    return 3
                report["audit_probe"] = {"returncode": audited.returncode, "stdout": audited.stdout, "stderr": audited.stderr}
                if (
                    audited.returncode != 0
                    or "AUTOROUTE_AUDIT_CALIBRATION_PASSED" not in audited.stdout
                    or sha256_path(seed_board) != seed_before_audit
                ):
                    report.update({"status": "BLOCKED_AUDIT", "reason": "project audit is missing or has no known-bad calibration"})
                    write_json_atomic(report_path, report)
                    return 3
            baseline = root / config["seed"]["drc_baseline"]
            if baseline.is_file():
                preflight_report = scratch / "candidate-preflight.json"
                try:
                    completed = subprocess.run(
                        [
                            sys.executable,
                            str(Path(__file__).with_name("kicad_route_candidate.py")),
                            str(seed_board), "--config", str(config_path),
                            "--prepare-only", "--kicad-python", str(kicad_python),
                            "--report", str(preflight_report), "--fail-on-findings",
                        ],
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                        check=False,
                        timeout=config["limits"]["timeout_seconds"] + config["limits"]["audit_timeout_seconds"],
                    )
                except subprocess.TimeoutExpired:
                    report.update({"status": "BLOCKED_CONFIGURATION", "reason": "candidate preflight timed out"})
                    write_json_atomic(report_path, report)
                    return 3
                if not preflight_report.is_file():
                    report.update({
                        "status": "BLOCKED_CONFIGURATION",
                        "reason": completed.stderr.strip() or "candidate preflight emitted no report",
                    })
                    write_json_atomic(report_path, report)
                    return 3
                preflight = json.loads(preflight_report.read_text(encoding="utf-8"))
                report["candidate_preflight"] = {
                    "returncode": completed.returncode,
                    "verdict": preflight.get("verdict"),
                    "reason": preflight.get("verdict_reason") or preflight.get("error"),
                    "findings": preflight.get("findings"),
                }
                if completed.returncode != 0 or preflight.get("verdict") != "PREPARED":
                    report.update({
                        "status": "BLOCKED_AUDIT"
                        if config["scope"]["selected_scope_policy"] == "project-audited"
                        else "BLOCKED_CONFIGURATION",
                        "reason": "candidate seed/baseline preflight did not pass",
                    })
                    write_json_atomic(report_path, report)
                    return 3
            if sha256_path(seed_board) != seed_integrity_sha256:
                report.update({
                    "status": "BLOCKED_ADAPTER",
                    "reason": "generated seed changed during scaffold checks",
                })
                write_json_atomic(report_path, report)
                return 3
        if build_v2_input_bundle(config) != bundle:
            report.update({
                "status": "STALE_SOURCE",
                "reason": "source bundle changed during scaffold checks",
            })
            write_json_atomic(report_path, report)
            return 3
        try:
            from kicad_autoroute_tools import default_cache, status as tool_status
            tools = tool_status(default_cache(), require_valid=True)
        except Exception as exc:
            tools = {"installed": False, "error": str(exc)}
        report["toolchain"] = tools
        if (
            tools.get("installed") is not True
            or tools.get("promotion_integrity_pinned") is not True
        ):
            report.update({"status": "BLOCKED_TOOLCHAIN", "reason": tools.get("error", "pinned toolchain is not installed")})
            write_json_atomic(report_path, report)
            return 3
        try:
            from kicad_route_candidate import _compatibility_cell
            from kicad_verify import find_kicad_cli
            compatibility = _compatibility_cell(
                seed_inspection["pcbnew"], Path(find_kicad_cli(None))
            )
        except Exception as exc:
            report.update({"status": "REPORT_ONLY_PLATFORM", "reason": str(exc)})
            write_json_atomic(report_path, report)
            return 3
        report["compatibility"] = compatibility
        if compatibility.get("promotion_enabled") is not True:
            report.update({
                "status": "REPORT_ONLY_PLATFORM",
                "reason": "the exact OS/architecture/KiCad/pcbnew cell is not promotion-enabled",
            })
            write_json_atomic(report_path, report)
            return 3
        try:
            final_bundle = build_v2_input_bundle(config)
        except AutorouteError as exc:
            report.update({"status": "STALE_SOURCE", "reason": str(exc)})
            write_json_atomic(report_path, report)
            return 3
        if final_bundle != bundle:
            report.update({
                "status": "STALE_SOURCE",
                "reason": "source bundle changed during final toolchain/compatibility checks",
            })
            write_json_atomic(report_path, report)
            return 3
        baseline = root / config["seed"]["drc_baseline"]
        if baseline.is_file():
            report["status"] = "READY_FOR_CANDIDATE"
            report["next"] = "run kicad_route_candidate.py on outputs.seed with this config"
        else:
            report["status"] = "READY_FOR_BASELINE"
            report["next"] = "create and review the seed DRC baseline before routing"
        report["reason"] = "all scaffold preconditions for this phase passed"
        exit_code = 0
    except (AutorouteError, ScaffoldError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        report["reason"] = str(exc)
    write_json_atomic(report_path, report)
    return exit_code


def _pcb_route(item, board, pcbnew) -> dict:
    if isinstance(item, pcbnew.PCB_VIA):
        return {
            "kind": "via", "net": str(item.GetNetname()),
            "at_nm": [int(item.GetPosition().x), int(item.GetPosition().y)],
            "diameter_nm": int(item.GetFrontWidth()), "drill_nm": int(item.GetDrillValue()),
            "layers": [str(board.GetLayerName(int(item.TopLayer()))), str(board.GetLayerName(int(item.BottomLayer())))],
        }
    if isinstance(item, pcbnew.PCB_ARC):
        return {"kind": "arc", "net": str(item.GetNetname()), "layer": str(item.GetLayerName())}
    if isinstance(item, pcbnew.PCB_TRACK):
        start = [int(item.GetStart().x), int(item.GetStart().y)]
        end = [int(item.GetEnd().x), int(item.GetEnd().y)]
        start, end = sorted((start, end))
        return {
            "kind": "segment", "net": str(item.GetNetname()), "layer": str(item.GetLayerName()),
            "width_nm": int(item.GetWidth()), "start_nm": start, "end_nm": end,
        }
    return {"kind": type(item).__name__, "net": str(item.GetNetname())}


def _pcb_uuid(item) -> str:
    for getter in (lambda: item.m_Uuid.AsString(), lambda: item.GetUuid().AsString(), lambda: str(item.m_Uuid)):
        try:
            value = getter()
        except Exception:
            continue
        if value:
            return str(value)
    raise ScaffoldError("route item has no readable UUID")


def _board_header(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    version = re.search(r"\(kicad_pcb\s+\(version\s+(\d+)\)", text)
    return {
        "version": int(version.group(1)) if version else None,
        "has_stackup": "(stackup" in text,
    }


def _pcb_worker(argv: list[str]) -> int:
    if len(argv) < 3:
        raise ScaffoldError("malformed pcb worker invocation")
    mode, board_path, report_path, *extra = argv
    scratch_raw = os.environ.get("KICAD_AUTOROUTE_SCAFFOLD_WORKER_ROOT")
    if not scratch_raw:
        raise ScaffoldError("internal PCB worker requires an orchestrator scratch root")
    scratch = Path(scratch_raw).resolve()
    write_paths = [Path(report_path).resolve()]
    if mode == "probe" and extra:
        write_paths.append(Path(extra[0]).resolve())
    for path in write_paths:
        try:
            path.relative_to(scratch)
        except ValueError as exc:
            raise ScaffoldError(f"internal PCB worker write escapes scratch root: {path}") from exc
    import pcbnew
    board_file = Path(board_path).resolve()
    board = pcbnew.LoadBoard(str(board_file))
    manager = None
    project = None
    project_path = board_file.with_suffix(".kicad_pro")
    if project_path.is_file():
        manager = pcbnew.GetSettingsManager()
        if not manager.LoadProject(str(project_path)):
            raise ScaffoldError(f"KiCad could not load project {project_path}")
        project = manager.GetProject(str(project_path))
        if project is None:
            raise ScaffoldError(f"KiCad returned no project object for {project_path}")
        board.SetProject(project)
        board.SynchronizeNetsAndNetClasses(False)
    _ = (manager, project)
    if mode == "inspect":
        net_to_class = {}
        for net in board.GetNetInfo().NetsByName().values():
            name = str(net.GetNetname())
            if name:
                net_to_class[name] = str(net.GetNetClassName())
        routes = [{
            "uuid": _pcb_uuid(item), "route": _pcb_route(item, board, pcbnew),
            "locked": bool(item.IsLocked()),
            "primitive_type": (
                int(item.GetViaType()) if isinstance(item, pcbnew.PCB_VIA)
                else _pcb_route(item, board, pcbnew)["kind"]
            ),
        } for item in board.GetTracks()]
        result = {
            "pcbnew": str(pcbnew.GetBuildVersion()),
            "copper_layer_count": int(board.GetCopperLayerCount()),
            "copper_layers": [
                str(board.GetLayerName(layer)) for layer in range(64)
                if board.IsLayerEnabled(layer) and pcbnew.IsCopperLayer(layer)
            ],
            "net_to_class": dict(sorted(net_to_class.items())),
            "routes": sorted(routes, key=lambda item: item["uuid"]),
            "header": _board_header(board_file),
        }
    elif mode == "probe":
        if len(extra) != 1:
            raise ScaffoldError("probe worker requires output board path")
        destination = Path(extra[0]).resolve()
        destination.parent.mkdir(parents=True, exist_ok=True)
        from kicad_route_candidate import _semantic_snapshot
        semantic_before = _semantic_snapshot(board, pcbnew)
        board.BuildConnectivity()
        if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
            raise ScaffoldError("migration probe zone refill failed")
        if not pcbnew.SaveBoard(str(destination), board):
            raise ScaffoldError("migration probe save failed")
        before, after = _board_header(board_file), _board_header(destination)
        reloaded = pcbnew.LoadBoard(str(destination))
        semantic_after = _semantic_snapshot(reloaded, pcbnew)
        reasons = []
        if before["version"] != after["version"]:
            reasons.append(f"board format changes {before['version']} -> {after['version']}")
        if not before["has_stackup"] and after["has_stackup"]:
            reasons.append("KiCad injects a default stackup")
        changed_categories = sorted(
            key
            for key, digest in semantic_before["nonrouting_category_sha256"].items()
            if semantic_after["nonrouting_category_sha256"].get(key) != digest
        )
        if changed_categories:
            reasons.append(
                "KiCad changes non-routing semantic categories: "
                + ", ".join(changed_categories)
            )
        result = {
            "before": before, "after": after, "needs_migration": bool(reasons),
            "reason": "; ".join(reasons) if reasons else None,
            "probe_board_sha256": sha256_path(destination),
            "nonrouting_before_sha256": semantic_before["nonrouting_sha256"],
            "nonrouting_after_sha256": semantic_after["nonrouting_sha256"],
            "nonrouting_changed_categories": changed_categories,
        }
    else:
        raise ScaffoldError(f"unknown pcb worker mode {mode!r}")
    write_json_atomic(report_path, result)
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    plan = sub.add_parser("plan")
    plan.add_argument("board")
    plan.add_argument("--mode", choices=("board-snapshot", "generator-adapter"), required=True)
    plan.add_argument("--project-root")
    classes = plan.add_mutually_exclusive_group(required=True)
    classes.add_argument("--use-net-class")
    classes.add_argument("--create-net-class")
    plan.add_argument("--net", action="append")
    plan.add_argument("--track-width-mm")
    plan.add_argument("--clearance-mm")
    plan.add_argument("--via-diameter-mm")
    plan.add_argument("--via-drill-mm")
    plan.add_argument("--layer", action="append", required=True)
    plan.add_argument("--reset-all-selected-routing", action="store_true")
    plan.add_argument("--board-only-authority", action="store_true")
    policy = plan.add_mutually_exclusive_group(required=True)
    policy.add_argument("--selected-scope-routine", action="store_true")
    policy.add_argument("--project-audited", action="store_true")
    plan.add_argument("--source", action="append")
    plan.add_argument("--kicad-python")
    plan.add_argument("--max-passes", type=int, default=20)
    plan.add_argument("--max-threads", type=int, default=4)
    plan.add_argument("--timeout-seconds", type=int, default=1200)
    plan.add_argument("--audit-timeout-seconds", type=int, default=300)
    plan.add_argument("--output", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--plan", required=True)
    apply.add_argument("--approve-plan-sha256", required=True)
    repin = sub.add_parser("repin-plan")
    repin.add_argument("--config", required=True)
    repin.add_argument("--output", required=True)
    check = sub.add_parser("check")
    check.add_argument("board")
    check.add_argument("--config")
    check.add_argument("--kicad-python")
    check.add_argument("--report", required=True)
    return parser


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_pcb-worker"]:
        try:
            return _pcb_worker(argv[1:])
        except Exception as exc:
            print(f"kicad_autoroute_scaffold worker: {exc}", file=sys.stderr)
            return 2
    args = _parser().parse_args(argv)
    try:
        if args.command == "plan":
            return _plan(args)
        if args.command == "apply":
            return _apply(args)
        if args.command == "repin-plan":
            return _repin_plan(args)
        return _check(args)
    except (ScaffoldError, AutorouteError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        print(f"kicad_autoroute_scaffold: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
