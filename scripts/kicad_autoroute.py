#!/usr/bin/env python3
"""Deterministic contracts shared by the KiCad autorouting tools.

This module deliberately has no pcbnew dependency.  Candidate generation,
promotion, and project-local manifest applicators all use the same strict JSON
and route canonicalization rules, so a board cannot be approved under one
interpretation and regenerated under another.
"""

from __future__ import annotations

import collections
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Any, Iterable


CONFIG_SCHEMA = "kicad-autoroute-config-v1"
CONFIG_SCHEMA_V2 = "kicad-autoroute-config-v2"
MANIFEST_SCHEMA = "kicad-route-manifest-v3"
MANIFEST_SCHEMA_V2 = "kicad-route-manifest-v4"
SEED_ATTESTATION_SCHEMA = "kicad-autoroute-seed-attestation-v1"
REPORT_SCHEMA = "kicad-route-candidate-report-v2"
SNAPSHOT_SCHEMA = "kicad-route-semantic-snapshot-v3"
COMPATIBILITY_SCHEMA = "kicad-autoroute-compatibility-v2"
DRC_BASELINE_SCHEMA = "kicad-drc-baseline-v1"
BACKEND_ID = "freerouting-2.3.0-temurin-25.0.4+7"
ROUTE_APPLICATOR_VERSION = "2"
PCB_WORKER_SCHEMA = "kicad-route-pcb-worker-v1"
ROUTE_APPLY_WORKER_SCHEMA = "kicad-route-apply-worker-v1"
IDENTITY_WORKER_SCHEMA = "kicad-route-identity-worker-v1"
PROMOTION_CHECKS = frozenset(
    {
        "source_unchanged",
        "input_bundle_unchanged",
        "nonrouting_unchanged",
        "locked_routes_unchanged",
        "structured_drc_baseline_passed",
        "seed_project_audits_passed",
        "final_project_audits_passed",
    }
)
PROMOTION_CHECKS_V2_ROUTINE = frozenset(
    (PROMOTION_CHECKS - {"seed_project_audits_passed", "final_project_audits_passed"})
    | {"selected_scope_routine_declared"}
)
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_SUBSTITUTION = re.compile(r"\{([^{}]+)\}")
_ALLOWED_SUBSTITUTIONS = {"board", "workspace", "config_dir"}


class AutorouteError(ValueError):
    """An autorouting input or result is unsafe or ambiguous."""


def _strict_object(value: Any, name: str, allowed: set[str], required: set[str]) -> dict:
    if not isinstance(value, dict):
        raise AutorouteError(f"{name} must be a JSON object")
    unknown = sorted(set(value) - allowed)
    missing = sorted(required - set(value))
    if unknown:
        raise AutorouteError(f"{name} contains unknown key(s): {', '.join(unknown)}")
    if missing:
        raise AutorouteError(f"{name} is missing required key(s): {', '.join(missing)}")
    return value


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise AutorouteError(f"{name} must be a positive integer")
    return value


def _nm(value: Any, name: str) -> int:
    value = _positive_int(value, name)
    if value > 10_000_000_000:
        raise AutorouteError(f"{name} is implausibly large: {value} nm")
    return value


def sha256_path(path: Path | str) -> str:
    path = Path(path)
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def canonical_json_bytes(value: Any) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def canonical_json_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def write_json_atomic(path: Path | str, value: Any) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(f".{path.name}.tmp")
    temp.write_bytes(canonical_json_bytes(value))
    temp.replace(path)


def _audit_entry(raw: Any, where: str) -> dict:
    entry = _strict_object(
        raw,
        where,
        {"interpreter", "argv", "timeout_seconds", "calibration_marker"},
        {"interpreter", "argv"},
    )
    if entry["interpreter"] != "kicad_python":
        raise AutorouteError(f"{where}.interpreter must be kicad_python for promotable audits")
    argv = entry["argv"]
    if not isinstance(argv, list) or not argv or not all(
        isinstance(x, str) and x for x in argv
    ):
        raise AutorouteError(f"{where}.argv must be a non-empty string array")
    found = {match for token in argv for match in _SUBSTITUTION.findall(token)}
    bad = sorted(found - _ALLOWED_SUBSTITUTIONS)
    if bad:
        raise AutorouteError(f"{where}.argv uses unsupported substitutions: {bad}")
    if "board" not in found:
        raise AutorouteError(f"{where}.argv must contain {{board}}")
    program = Path(argv[0])
    if program.is_absolute() or ".." in program.parts or "{" in argv[0]:
        raise AutorouteError(f"{where}.argv[0] must be a project-relative bundled script")
    timeout = entry.get("timeout_seconds", 300)
    _positive_int(timeout, f"{where}.timeout_seconds")
    marker = entry.get("calibration_marker")
    if marker is not None and (not isinstance(marker, str) or not marker):
        raise AutorouteError(f"{where}.calibration_marker must be a non-empty string")
    return {
        "interpreter": entry["interpreter"],
        "argv": argv,
        "timeout_seconds": timeout,
        "calibration_marker": marker,
    }


def _load_config_v1(path: Path) -> dict:
    path = Path(path).expanduser().resolve()
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutorouteError(f"cannot read autoroute config {path}: {exc}") from exc
    root = _strict_object(
        raw,
        "autoroute config",
        {"schema", "backend", "inputs", "scope", "limits", "seed", "final", "promotion"},
        {"schema", "backend", "inputs", "scope", "limits", "seed", "final", "promotion"},
    )
    if root["schema"] != CONFIG_SCHEMA:
        raise AutorouteError(f"unsupported config schema {root['schema']!r}")
    if root["backend"] != BACKEND_ID:
        raise AutorouteError(f"unsupported backend lock {root['backend']!r}")
    inputs = root["inputs"]
    if not isinstance(inputs, list) or not inputs or not all(
        isinstance(value, str) and value for value in inputs
    ):
        raise AutorouteError("inputs must be a non-empty relative-path array")
    if len(set(inputs)) != len(inputs):
        raise AutorouteError("inputs contains duplicates")
    for value in inputs:
        relative = Path(value)
        if relative.is_absolute() or ".." in relative.parts:
            raise AutorouteError(f"input path must stay below the config directory: {value!r}")

    scope = _strict_object(
        root["scope"],
        "scope",
        {"net_classes", "layers", "styles"},
        {"net_classes", "layers", "styles"},
    )
    classes = scope["net_classes"]
    layers = scope["layers"]
    if not isinstance(classes, list) or not classes or not all(
        isinstance(x, str) and x for x in classes
    ):
        raise AutorouteError("scope.net_classes must be a non-empty string array")
    if len(set(classes)) != len(classes):
        raise AutorouteError("scope.net_classes contains duplicates")
    if not isinstance(layers, list) or not layers or not all(
        isinstance(x, str) and x.endswith(".Cu") for x in layers
    ):
        raise AutorouteError("scope.layers must be a non-empty KiCad copper-layer array")
    if len(set(layers)) != len(layers):
        raise AutorouteError("scope.layers contains duplicates")
    if not isinstance(scope["styles"], dict) or set(scope["styles"]) != set(classes):
        raise AutorouteError("scope.styles must define exactly one style per selected class")
    styles = {}
    for class_name in classes:
        style = _strict_object(
            scope["styles"][class_name],
            f"scope.styles.{class_name}",
            {"track_width_nm", "clearance_nm", "via_diameter_nm", "via_drill_nm"},
            {"track_width_nm", "clearance_nm", "via_diameter_nm", "via_drill_nm"},
        )
        styles[class_name] = {
            key: _nm(style[key], f"scope.styles.{class_name}.{key}")
            for key in style
        }
        if styles[class_name]["via_drill_nm"] >= styles[class_name]["via_diameter_nm"]:
            raise AutorouteError(f"scope.styles.{class_name} via drill must be smaller than diameter")

    limits = _strict_object(
        root["limits"],
        "limits",
        {"max_passes", "max_threads", "timeout_seconds", "audit_timeout_seconds"},
        {"max_passes", "max_threads", "timeout_seconds", "audit_timeout_seconds"},
    )
    limits = {key: _positive_int(value, f"limits.{key}") for key, value in limits.items()}

    seed = _strict_object(
        root["seed"], "seed", {"drc_baseline", "audit_commands"}, {"drc_baseline", "audit_commands"}
    )
    final = _strict_object(root["final"], "final", {"audit_commands"}, {"audit_commands"})
    promotion = _strict_object(root["promotion"], "promotion", {"manifest"}, {"manifest"})
    for where, value in (("seed.drc_baseline", seed["drc_baseline"]), ("promotion.manifest", promotion["manifest"])):
        if not isinstance(value, str) or not value:
            raise AutorouteError(f"{where} must be a non-empty relative path")
        candidate = Path(value)
        if candidate.is_absolute() or ".." in candidate.parts:
            raise AutorouteError(f"{where} must stay below the config directory")
    for phase_name, phase in (("seed", seed), ("final", final)):
        commands = phase["audit_commands"]
        if not isinstance(commands, list):
            raise AutorouteError(f"{phase_name}.audit_commands must be an array")
        phase["audit_commands"] = [
            _audit_entry(entry, f"{phase_name}.audit_commands[{index}]")
            for index, entry in enumerate(commands)
        ]
    normalized = {
        "schema": CONFIG_SCHEMA,
        "backend": BACKEND_ID,
        "inputs": inputs,
        "scope": {
            "net_classes": classes,
            "layers": layers,
            "styles": styles,
        },
        "limits": limits,
        "seed": seed,
        "final": final,
        "promotion": promotion,
        "config_path": str(path),
        "config_dir": str(path.parent),
        "config_sha256": sha256_path(path),
    }
    return normalized


def _relative_path(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise AutorouteError(f"{name} must be a non-empty relative path")
    candidate = Path(value)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise AutorouteError(f"{name} must stay below project_root")
    return candidate.as_posix()


def _optional_relative_path(value: Any, name: str) -> str | None:
    if value is None:
        return None
    return _relative_path(value, name)


def _v2_source_entry(raw: Any, where: str) -> dict:
    entry = _strict_object(
        raw,
        where,
        {"role", "kind", "path", "sha256"},
        {"role", "kind", "path", "sha256"},
    )
    if not isinstance(entry["role"], str) or not entry["role"]:
        raise AutorouteError(f"{where}.role must be a non-empty string")
    if entry["kind"] not in {"file", "directory-recursive"}:
        raise AutorouteError(f"{where}.kind must be file or directory-recursive")
    path = _relative_path(entry["path"], f"{where}.path")
    if not _HEX64.fullmatch(str(entry["sha256"])):
        raise AutorouteError(f"{where}.sha256 is invalid")
    return {
        "role": entry["role"],
        "kind": entry["kind"],
        "path": path,
        "sha256": entry["sha256"],
    }


def _v2_tool_entry(raw: Any, where: str, protocol: str) -> dict:
    entry = _strict_object(
        raw,
        where,
        {"path", "protocol", "sha256"},
        {"path", "protocol", "sha256"},
    )
    if entry["protocol"] != protocol:
        raise AutorouteError(
            f"{where}.protocol must be {protocol!r}, got {entry['protocol']!r}"
        )
    path = _relative_path(entry["path"], f"{where}.path")
    if not _HEX64.fullmatch(str(entry["sha256"])):
        raise AutorouteError(f"{where}.sha256 is invalid")
    return {"path": path, "protocol": protocol, "sha256": entry["sha256"]}


def _load_config_v2(path: Path) -> dict:
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutorouteError(f"cannot read autoroute config {path}: {exc}") from exc
    root = _strict_object(
        raw,
        "autoroute config v2",
        {
            "schema", "backend", "project", "sources", "tools", "scope",
            "reset", "limits", "seed", "final", "promotion", "outputs",
        },
        {
            "schema", "backend", "project", "sources", "tools", "scope",
            "reset", "limits", "seed", "final", "promotion", "outputs",
        },
    )
    if root["schema"] != CONFIG_SCHEMA_V2:
        raise AutorouteError(f"unsupported config schema {root['schema']!r}")
    if root["backend"] != BACKEND_ID:
        raise AutorouteError(f"unsupported backend lock {root['backend']!r}")

    project = _strict_object(
        root["project"],
        "project",
        {
            "root", "mode", "board_basename", "schematic_authority",
            "source_board", "project_file", "schematic_file",
        },
        {
            "root", "mode", "board_basename", "schematic_authority",
            "source_board", "project_file", "schematic_file",
        },
    )
    project_root_rel = _relative_path(project["root"], "project.root")
    if project_root_rel != ".":
        raise AutorouteError(
            "v2 currently requires project.root='.' so adapters, audits, and "
            "source paths share one unambiguous root"
        )
    project_root = (path.parent / project_root_rel).resolve()
    try:
        project_root.relative_to(path.parent.resolve())
    except ValueError as exc:
        raise AutorouteError("project.root resolves outside the config directory") from exc
    if project["mode"] not in {"board-snapshot", "generator-adapter"}:
        raise AutorouteError("project.mode must be board-snapshot or generator-adapter")
    if (
        not isinstance(project["board_basename"], str)
        or Path(project["board_basename"]).name != project["board_basename"]
        or not project["board_basename"].endswith(".kicad_pcb")
    ):
        raise AutorouteError("project.board_basename must be a .kicad_pcb basename")
    if project["schematic_authority"] not in {"parity", "board-only"}:
        raise AutorouteError("project.schematic_authority must be parity or board-only")
    source_board = _optional_relative_path(project["source_board"], "project.source_board")
    project_file = _optional_relative_path(project["project_file"], "project.project_file")
    schematic_file = _optional_relative_path(project["schematic_file"], "project.schematic_file")
    if project["mode"] == "board-snapshot" and source_board is None:
        raise AutorouteError("board-snapshot mode requires project.source_board")
    if project["mode"] == "generator-adapter" and source_board is not None:
        raise AutorouteError("generator-adapter mode must not declare a generated board as source")
    if project_file is None:
        raise AutorouteError("v2 autorouting requires project.project_file")
    if project["schematic_authority"] == "parity" and schematic_file is None:
        raise AutorouteError("parity authority requires project.schematic_file")
    if project["schematic_authority"] == "board-only" and schematic_file is not None:
        raise AutorouteError("board-only authority must not name a schematic")
    wanted_stem = Path(project["board_basename"]).stem
    if Path(project_file).name != wanted_stem + ".kicad_pro":
        raise AutorouteError("project.project_file must have the board basename")
    if schematic_file is not None and Path(schematic_file).name != wanted_stem + ".kicad_sch":
        raise AutorouteError("project.schematic_file must have the board basename")
    if source_board is not None and Path(source_board).name != project["board_basename"]:
        raise AutorouteError("project.source_board must have project.board_basename")
    for name, relative in (
        ("project.source_board", source_board),
        ("project.project_file", project_file),
        ("project.schematic_file", schematic_file),
    ):
        if relative is not None and Path(relative).parent != Path("."):
            raise AutorouteError(
                f"{name} must be top-level in project.root for same-stem adapter context"
            )

    sources_raw = root["sources"]
    if not isinstance(sources_raw, list) or not sources_raw:
        raise AutorouteError("sources must be a non-empty typed source array")
    sources = [
        _v2_source_entry(entry, f"sources[{index}]")
        for index, entry in enumerate(sources_raw)
    ]
    source_roles = [entry["role"] for entry in sources]
    source_paths = [entry["path"] for entry in sources]
    if len(source_roles) != len(set(source_roles)):
        raise AutorouteError("sources contains duplicate roles")
    if len(source_paths) != len(set(source_paths)):
        raise AutorouteError("sources contains duplicate paths")
    def source_covers(relative: str) -> bool:
        target = (project_root / relative).resolve()
        for declaration in sources:
            declared = (project_root / declaration["path"]).resolve()
            if declaration["kind"] == "file" and target == declared:
                return True
            if declaration["kind"] == "directory-recursive" and (
                target == declared or target.is_relative_to(declared)
            ):
                return True
        return False

    consumed_context = [project_file]
    if source_board is not None:
        consumed_context.append(source_board)
    if schematic_file is not None:
        consumed_context.append(schematic_file)
    missing_source_paths = sorted(
        relative for relative in consumed_context if not source_covers(relative)
    )
    if missing_source_paths:
        raise AutorouteError(
            "sources omit adapter-consumed project context: "
            + ", ".join(missing_source_paths)
        )

    tools = _strict_object(
        root["tools"],
        "tools",
        {"adapter", "applicator", "audit"},
        {"adapter", "applicator", "audit"},
    )
    adapter = _v2_tool_entry(
        tools["adapter"], "tools.adapter", "kicad-autoroute-adapter-v1"
    )
    applicator = _v2_tool_entry(
        tools["applicator"], "tools.applicator", "kicad-autoroute-applicator-v2"
    )
    audit = _v2_tool_entry(
        tools["audit"], "tools.audit", "kicad-autoroute-audit-v1"
    )
    tool_paths = [adapter["path"], applicator["path"], audit["path"]]
    if len(tool_paths) != len(set(tool_paths)):
        raise AutorouteError("adapter, applicator, and audit paths must be distinct")

    scope = _strict_object(
        root["scope"],
        "scope",
        {
            "net_classes", "net_to_class", "layers", "styles",
            "selected_scope_policy",
        },
        {
            "net_classes", "net_to_class", "layers", "styles",
            "selected_scope_policy",
        },
    )
    if scope["selected_scope_policy"] not in {"routine", "project-audited"}:
        raise AutorouteError(
            "scope.selected_scope_policy must be routine or project-audited"
        )
    normalized_scope = _validate_manifest_scope(
        {
            "net_classes": scope["net_classes"],
            "resolved_nets": sorted(scope["net_to_class"])
            if isinstance(scope["net_to_class"], dict)
            else [],
            "net_to_class": scope["net_to_class"],
            "layers": scope["layers"],
            "styles": scope["styles"],
        },
        "scope",
    )

    reset = _strict_object(
        root["reset"],
        "reset",
        {"policy", "manifest", "manifest_sha256"},
        {"policy", "manifest", "manifest_sha256"},
    )
    if reset["policy"] not in {"none", "all-selected-routing"}:
        raise AutorouteError("reset.policy must be none or all-selected-routing")
    reset_manifest = _optional_relative_path(reset["manifest"], "reset.manifest")
    reset_sha = reset["manifest_sha256"]
    if reset["policy"] == "none":
        if reset_manifest is not None or reset_sha is not None:
            raise AutorouteError("reset.policy none must not name a manifest or digest")
    else:
        if reset_manifest is None or not _HEX64.fullmatch(str(reset_sha)):
            raise AutorouteError(
                "all-selected-routing requires reset.manifest and manifest_sha256"
            )

    limits = _strict_object(
        root["limits"],
        "limits",
        {"max_passes", "max_threads", "timeout_seconds", "audit_timeout_seconds"},
        {"max_passes", "max_threads", "timeout_seconds", "audit_timeout_seconds"},
    )
    limits = {key: _positive_int(value, f"limits.{key}") for key, value in limits.items()}
    seed = _strict_object(
        root["seed"], "seed", {"drc_baseline", "audit_commands"},
        {"drc_baseline", "audit_commands"},
    )
    final = _strict_object(
        root["final"], "final", {"audit_commands"}, {"audit_commands"}
    )
    promotion = _strict_object(
        root["promotion"], "promotion", {"manifest"}, {"manifest"}
    )
    seed_baseline = _relative_path(seed["drc_baseline"], "seed.drc_baseline")
    promotion_manifest = _relative_path(promotion["manifest"], "promotion.manifest")
    for phase_name, phase in (("seed", seed), ("final", final)):
        commands = phase["audit_commands"]
        if not isinstance(commands, list):
            raise AutorouteError(f"{phase_name}.audit_commands must be an array")
        phase["audit_commands"] = [
            _audit_entry(entry, f"{phase_name}.audit_commands[{index}]")
            for index, entry in enumerate(commands)
        ]
    if scope["selected_scope_policy"] == "routine":
        if seed["audit_commands"] or final["audit_commands"]:
            raise AutorouteError(
                "routine selected-scope policy must not carry project-physics audit commands"
            )
    else:
        for phase_name, phase in (("seed", seed), ("final", final)):
            if not phase["audit_commands"]:
                raise AutorouteError(
                    f"project-audited policy requires {phase_name} audit commands"
                )
            if any(command["argv"][0] != audit["path"] for command in phase["audit_commands"]):
                raise AutorouteError(
                    f"{phase_name} audit command must use the configured audit adapter"
                )
            if any(not command.get("calibration_marker") for command in phase["audit_commands"]):
                raise AutorouteError(
                    f"{phase_name} audit commands require a known-bad calibration marker"
                )

    outputs = _strict_object(
        root["outputs"],
        "outputs",
        {"root", "seed", "final"},
        {"root", "seed", "final"},
    )
    output_root = _relative_path(outputs["root"], "outputs.root")
    output_seed = _relative_path(outputs["seed"], "outputs.seed")
    output_final = _relative_path(outputs["final"], "outputs.final")
    output_root_path = (project_root / output_root).resolve()
    for name, relative in (("outputs.seed", output_seed), ("outputs.final", output_final)):
        resolved = (project_root / relative).resolve()
        try:
            resolved.relative_to(output_root_path)
        except ValueError as exc:
            raise AutorouteError(f"{name} must be below outputs.root") from exc
        if resolved == output_root_path:
            raise AutorouteError(f"{name} must be a strict descendant of outputs.root")
        if Path(relative).name != project["board_basename"]:
            raise AutorouteError(f"{name} must end with project.board_basename")

    config_relative = Path(path).name
    file_targets = [
        ("autoroute config", config_relative),
        ("tools.adapter", adapter["path"]),
        ("tools.applicator", applicator["path"]),
        ("tools.audit", audit["path"]),
        ("seed.drc_baseline", seed_baseline),
        ("promotion.manifest", promotion_manifest),
    ]
    if reset_manifest is not None:
        file_targets.append(("reset.manifest", reset_manifest))
    by_target: dict[str, list[str]] = {}
    for name, relative in file_targets:
        by_target.setdefault(relative, []).append(name)
    collisions = {target: names for target, names in by_target.items() if len(names) > 1}
    if collisions:
        target, names = sorted(collisions.items())[0]
        raise AutorouteError(
            f"generated file target {target!r} is shared by: {', '.join(names)}"
        )
    if output_seed == output_final:
        raise AutorouteError("outputs.seed and outputs.final must be distinct")
    output_files = {
        "outputs.seed": (project_root / output_seed).resolve(),
        "outputs.final": (project_root / output_final).resolve(),
    }
    for name, relative in file_targets:
        resolved = (project_root / relative).resolve()
        if (
            resolved == output_root_path
            or resolved.is_relative_to(output_root_path)
            or output_root_path.is_relative_to(resolved)
        ):
            raise AutorouteError(f"{name} must be disjoint from outputs.root")
        for output_name, output_path in output_files.items():
            if resolved == output_path:
                raise AutorouteError(f"{name} collides with {output_name}")

    generated_paths = {
        output_root, output_seed, output_final, seed_baseline, promotion_manifest,
        config_relative,
        *tool_paths,
        *(value for value in (reset_manifest,) if value is not None),
    }
    for source in sources:
        source_path = (project_root / source["path"]).resolve()
        for output in generated_paths:
            output_path = (project_root / output).resolve()
            if (
                source_path == output_path
                or (source["kind"] == "directory-recursive" and output_path.is_relative_to(source_path))
                or source_path.is_relative_to(output_path)
            ):
                raise AutorouteError(
                    f"generated path {output!r} overlaps source {source['path']!r}"
                )

    return {
        "schema": CONFIG_SCHEMA_V2,
        "backend": BACKEND_ID,
        "project": {
            "root": project_root_rel,
            "mode": project["mode"],
            "board_basename": project["board_basename"],
            "schematic_authority": project["schematic_authority"],
            "source_board": source_board,
            "project_file": project_file,
            "schematic_file": schematic_file,
        },
        "sources": sources,
        "tools": {"adapter": adapter, "applicator": applicator, "audit": audit},
        "scope": {
            "net_classes": normalized_scope["net_classes"],
            "net_to_class": normalized_scope["net_to_class"],
            "layers": normalized_scope["layers"],
            "styles": normalized_scope["styles"],
            "selected_scope_policy": scope["selected_scope_policy"],
        },
        "reset": {
            "policy": reset["policy"],
            "manifest": reset_manifest,
            "manifest_sha256": reset_sha,
        },
        "limits": limits,
        "seed": {"drc_baseline": seed_baseline, "audit_commands": seed["audit_commands"]},
        "final": {"audit_commands": final["audit_commands"]},
        "promotion": {"manifest": promotion_manifest},
        "outputs": {"root": output_root, "seed": output_seed, "final": output_final},
        "config_path": str(path),
        "config_dir": str(path.parent),
        "project_root": str(project_root),
        "config_sha256": sha256_path(path),
    }


def load_config(path: Path | str) -> dict:
    """Load v1 unchanged or the project-neutral v2 discriminated union."""
    path = Path(path).expanduser().resolve()
    try:
        header = json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutorouteError(f"cannot read autoroute config {path}: {exc}") from exc
    if not isinstance(header, dict):
        raise AutorouteError("autoroute config must be a JSON object")
    if header.get("schema") == CONFIG_SCHEMA:
        return _load_config_v1(path)
    if header.get("schema") == CONFIG_SCHEMA_V2:
        return _load_config_v2(path)
    raise AutorouteError(f"unsupported config schema {header.get('schema')!r}")


def config_path(config: dict, relative: str) -> Path:
    base = config.get("project_root", config["config_dir"])
    return (Path(base) / relative).resolve()


def resolve_project_netclasses(
    project_path: Path | str,
    selected: Iterable[str],
    *,
    expected_mapping: dict[str, str] | None = None,
) -> dict:
    project_path = Path(project_path)
    try:
        project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutorouteError(f"cannot read KiCad project {project_path}: {exc}") from exc
    settings = project.get("net_settings")
    if not isinstance(settings, dict):
        raise AutorouteError("KiCad project has no net_settings object")
    if expected_mapping is None and settings.get("netclass_patterns"):
        raise AutorouteError("netclass_patterns are unsupported for promotion v1")
    classes = settings.get("classes")
    if not isinstance(classes, list):
        raise AutorouteError("KiCad project net_settings.classes is not an array")
    by_name = {}
    for entry in classes:
        if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
            raise AutorouteError("KiCad project contains a malformed net class")
        if entry["name"] in by_name:
            raise AutorouteError(f"duplicate KiCad net class {entry['name']!r}")
        by_name[entry["name"]] = entry
    selected = list(selected)
    missing = sorted(set(selected) - set(by_name))
    if missing:
        raise AutorouteError(f"project is missing selected net class(es): {', '.join(missing)}")
    if expected_mapping is not None:
        if not isinstance(expected_mapping, dict) or not expected_mapping:
            raise AutorouteError("v2 net_to_class must be a non-empty object")
        bad = sorted(
            net
            for net, class_name in expected_mapping.items()
            if not isinstance(net, str)
            or not net
            or not isinstance(class_name, str)
            or class_name not in selected
        )
        if bad:
            raise AutorouteError(
                "v2 net_to_class contains invalid nets/classes: %s" % ", ".join(bad)
            )
        resolved = {
            name: sorted(net for net, class_name in expected_mapping.items() if class_name == name)
            for name in selected
        }
        empty = [name for name, nets in resolved.items() if not nets]
        if empty:
            raise AutorouteError(
                f"selected net class(es) have no frozen assignments: {', '.join(empty)}"
            )
        return {
            "classes": {name: by_name[name] for name in selected},
            "nets_by_class": resolved,
            "net_to_class": dict(sorted(expected_mapping.items())),
        }

    assignments = settings.get("netclass_assignments") or {}
    if not isinstance(assignments, dict):
        raise AutorouteError("netclass_assignments must be an object or null")
    resolved = {name: [] for name in selected}
    for net, value in assignments.items():
        if not isinstance(net, str) or not isinstance(value, list) or not all(isinstance(x, str) for x in value):
            raise AutorouteError(f"malformed netclass assignment for {net!r}")
        hits = [name for name in selected if name in value]
        if len(hits) > 1:
            raise AutorouteError(f"net {net!r} belongs to multiple selected classes: {hits}")
        if hits:
            resolved[hits[0]].append(net)
    empty = [name for name, nets in resolved.items() if not nets]
    if empty:
        raise AutorouteError(f"selected net class(es) have no exact assignments: {', '.join(empty)}")
    return {
        "classes": {name: by_name[name] for name in selected},
        "nets_by_class": {name: sorted(nets) for name, nets in resolved.items()},
        "net_to_class": {net: name for name, nets in resolved.items() for net in nets},
    }


def verify_project_styles(config: dict, project_scope: dict) -> None:
    for class_name, style in config["scope"]["styles"].items():
        actual = project_scope["classes"][class_name]
        checks = {
            "track_width": style["track_width_nm"],
            "clearance": style["clearance_nm"],
            "via_diameter": style["via_diameter_nm"],
            "via_drill": style["via_drill_nm"],
        }
        for key, want_nm in checks.items():
            try:
                got_nm = int((Decimal(str(actual[key])) * Decimal(1_000_000)).to_integral_exact())
            except (KeyError, InvalidOperation, ValueError) as exc:
                raise AutorouteError(f"project class {class_name!r} has invalid {key}") from exc
            if got_nm != want_nm:
                raise AutorouteError(
                    f"project class {class_name!r} {key} is {got_nm} nm, config requires {want_nm} nm"
                )


def _point_nm(value: Any, name: str) -> list[int]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise AutorouteError(f"{name} must contain two integer nanometre coordinates")
    out = []
    for index, coordinate in enumerate(value):
        if isinstance(coordinate, bool) or not isinstance(coordinate, int):
            raise AutorouteError(f"{name}[{index}] must be an integer")
        if not -(2**63) <= coordinate < 2**63:
            raise AutorouteError(f"{name}[{index}] is outside signed 64-bit range")
        out.append(coordinate)
    return out


def canonical_route(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise AutorouteError("route primitive must be an object")
    kind = raw.get("kind")
    if kind == "segment":
        allowed = {"kind", "net", "layer", "width_nm", "start_nm", "end_nm"}
        _strict_object(raw, "segment", allowed, allowed)
        if not isinstance(raw["net"], str) or not raw["net"]:
            raise AutorouteError("segment.net must be a non-empty exact net name")
        if not isinstance(raw["layer"], str) or not raw["layer"]:
            raise AutorouteError("segment.layer must be a non-empty KiCad copper layer")
        start = _point_nm(raw["start_nm"], "segment.start_nm")
        end = _point_nm(raw["end_nm"], "segment.end_nm")
        start, end = sorted((start, end))
        if start == end:
            raise AutorouteError("zero-length segment")
        return {
            "kind": "segment",
            "net": raw["net"],
            "layer": raw["layer"],
            "width_nm": _nm(raw["width_nm"], "segment.width_nm"),
            "start_nm": start,
            "end_nm": end,
        }
    if kind == "via":
        allowed = {"kind", "net", "at_nm", "diameter_nm", "drill_nm", "layers"}
        _strict_object(raw, "via", allowed, allowed)
        if not isinstance(raw["net"], str) or not raw["net"]:
            raise AutorouteError("via.net must be a non-empty exact net name")
        if raw["layers"] != ["F.Cu", "B.Cu"]:
            raise AutorouteError("promotion v1 supports F.Cu-B.Cu through-vias only")
        diameter = _nm(raw["diameter_nm"], "via.diameter_nm")
        drill = _nm(raw["drill_nm"], "via.drill_nm")
        if drill >= diameter:
            raise AutorouteError("via drill must be smaller than diameter")
        return {
            "kind": "via",
            "net": raw["net"],
            "at_nm": _point_nm(raw["at_nm"], "via.at_nm"),
            "diameter_nm": diameter,
            "drill_nm": drill,
            "layers": ["F.Cu", "B.Cu"],
        }
    raise AutorouteError(f"unsupported route primitive kind {kind!r}")


def _route_key(route: dict) -> tuple:
    if route["kind"] == "segment":
        return (
            "segment", route["net"], route["layer"], route["width_nm"],
            *route["start_nm"], *route["end_nm"],
        )
    return (
        "via", route["net"], *route["layers"], route["diameter_nm"],
        route["drill_nm"], *route["at_nm"],
    )


def _collinear_overlap(a: dict, b: dict) -> bool:
    if a["kind"] != "segment" or b["kind"] != "segment":
        return False
    if (a["net"], a["layer"], a["width_nm"]) != (b["net"], b["layer"], b["width_nm"]):
        return False
    ax, ay = a["start_nm"]
    bx, by = a["end_nm"]
    cx, cy = b["start_nm"]
    dx, dy = b["end_nm"]
    vx, vy = bx - ax, by - ay
    if vx * (cy - ay) - vy * (cx - ax) or vx * (dy - ay) - vy * (dx - ax):
        return False
    if abs(vx) >= abs(vy):
        left, right = max(min(ax, bx), min(cx, dx)), min(max(ax, bx), max(cx, dx))
    else:
        left, right = max(min(ay, by), min(cy, dy)), min(max(ay, by), max(cy, dy))
    return right > left


def canonical_routes(routes: Iterable[dict]) -> list[dict]:
    normalized = sorted((canonical_route(route) for route in routes), key=_route_key)
    for first, second in zip(normalized, normalized[1:]):
        if first == second:
            raise AutorouteError(f"duplicate route primitive: {first}")
    segments = [route for route in normalized if route["kind"] == "segment"]
    for index, first in enumerate(segments):
        for second in segments[index + 1 :]:
            if _collinear_overlap(first, second):
                raise AutorouteError(f"positive-length collinear route overlap: {first} / {second}")
    return normalized


def _merge_collinear_segments(first: dict, second: dict) -> dict | None:
    """Union two same-style collinear segments when their copper touches."""
    if first["kind"] != "segment" or second["kind"] != "segment":
        return None
    if (first["net"], first["layer"], first["width_nm"]) != (
        second["net"], second["layer"], second["width_nm"]
    ):
        return None
    ax, ay = first["start_nm"]
    bx, by = first["end_nm"]
    cx, cy = second["start_nm"]
    dx, dy = second["end_nm"]
    vx, vy = bx - ax, by - ay
    if vx * (cy - ay) - vy * (cx - ax) or vx * (dy - ay) - vy * (dx - ax):
        return None
    def projection(point):
        return point[0] * vx + point[1] * vy

    first_interval = sorted((projection(first["start_nm"]), projection(first["end_nm"])))
    second_interval = sorted((projection(second["start_nm"]), projection(second["end_nm"])))
    if max(first_interval[0], second_interval[0]) > min(first_interval[1], second_interval[1]):
        return None
    endpoints = sorted(
        (first["start_nm"], first["end_nm"], second["start_nm"], second["end_nm"]),
        key=projection,
    )
    return {
        "kind": "segment", "net": first["net"], "layer": first["layer"],
        "width_nm": first["width_nm"],
        "start_nm": min(endpoints[0], endpoints[-1]),
        "end_nm": max(endpoints[0], endpoints[-1]),
    }


def canonical_candidate_routes(routes: Iterable[dict]) -> list[dict]:
    """Normalize redundant router copper without weakening manifest strictness."""
    normalized = [canonical_route(route) for route in routes]
    unique = {
        json.dumps(route, sort_keys=True, separators=(",", ":")): route
        for route in normalized
    }
    working = list(unique.values())
    changed = True
    while changed:
        changed = False
        for first_index, first in enumerate(working):
            for second_index in range(first_index + 1, len(working)):
                merged = _merge_collinear_segments(first, working[second_index])
                if merged is None:
                    continue
                working = [
                    item
                    for index, item in enumerate(working)
                    if index not in {first_index, second_index}
                ] + [merged]
                changed = True
                break
            if changed:
                break
    return canonical_routes(working)


def candidate_item_to_route(item: dict) -> dict:
    kind = item.get("kind")
    if kind == "segment":
        return canonical_route(
            {
                "kind": "segment",
                "net": item.get("net"),
                "layer": item.get("layer"),
                "width_nm": item.get("width_nm"),
                "start_nm": item.get("start_nm"),
                "end_nm": item.get("end_nm"),
            }
        )
    if kind == "via":
        # KiCad 10's SWIG enum value for VIATYPE_THROUGH is 4 (older
        # snapshots/tools have exposed 0).  The exact F.Cu-to-B.Cu span below
        # remains the load-bearing proof that this is an ordinary through-via.
        if item.get("via_type") not in (None, 0, 4):
            raise AutorouteError("only ordinary through-vias are promotable")
        return canonical_route(
            {
                "kind": "via",
                "net": item.get("net"),
                "at_nm": item.get("position_nm"),
                "diameter_nm": item.get("width_nm"),
                "drill_nm": item.get("drill_nm"),
                "layers": [item.get("top_layer"), item.get("bottom_layer")],
            }
        )
    raise AutorouteError(f"candidate added unsupported route object {kind!r}")


def filter_candidate_routes(items: Iterable[dict], config: dict, project_scope: dict) -> dict:
    allowed_nets = set(project_scope["net_to_class"])
    allowed_layers = set(config["scope"]["layers"])
    accepted, drift = [], []
    for item in items:
        net = item.get("net")
        if net not in allowed_nets:
            drift.append({"reason": "excluded_net", "item": item})
            continue
        route = candidate_item_to_route(item)
        class_name = project_scope["net_to_class"][net]
        style = config["scope"]["styles"][class_name]
        if route["kind"] == "segment":
            if route["layer"] not in allowed_layers:
                drift.append({"reason": "excluded_layer", "item": item})
                continue
            if route["width_nm"] != style["track_width_nm"]:
                raise AutorouteError(f"segment on {net} has width {route['width_nm']}, expected {style['track_width_nm']}")
        else:
            if route["diameter_nm"] != style["via_diameter_nm"] or route["drill_nm"] != style["via_drill_nm"]:
                raise AutorouteError(f"via on {net} does not match declared class style")
        accepted.append(route)
    return {"routes": canonical_candidate_routes(accepted), "discarded_drift": drift}


def build_input_bundle(root: Path | str, entries: dict[str, Path | str]) -> list[dict]:
    root_lexical = Path(os.path.abspath(Path(root).expanduser()))
    root = root_lexical.resolve()
    out = []
    seen_paths = set()
    for role, raw_path in sorted(entries.items()):
        lexical = Path(os.path.abspath(Path(raw_path).expanduser()))
        try:
            lexical_relative = lexical.relative_to(root_lexical)
        except ValueError as exc:
            raise AutorouteError(
                f"input {role!r} is outside hermetic root {root}: {lexical}"
            ) from exc
        probe = root_lexical
        for component in lexical_relative.parts:
            probe = probe / component
            if probe.is_symlink():
                raise AutorouteError(
                    f"input {role!r} contains a symlink below the hermetic root: {probe}"
                )
        path = lexical.resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AutorouteError(f"input {role!r} is outside hermetic root {root}: {path}") from exc
        if path.is_dir():
            descendants = sorted(path.rglob("*"))
            symlinks = [candidate for candidate in descendants if candidate.is_symlink()]
            if symlinks:
                raise AutorouteError(
                    f"input {role!r} contains a symlink: {symlinks[0]}"
                )
            files = [candidate for candidate in descendants if candidate.is_file()]
        else:
            files = [path] if path.is_file() else []
        if not files:
            raise AutorouteError(f"input {role!r} is missing or empty: {path}")
        for file_path in files:
            if file_path.is_symlink():
                raise AutorouteError(
                    f"input {role!r} contains a symlink: {file_path}"
                )
            resolved = file_path.resolve()
            try:
                file_relative = resolved.relative_to(root)
            except ValueError as exc:
                raise AutorouteError(f"input {role!r} escapes hermetic root through {file_path}") from exc
            key = file_relative.as_posix()
            if key in seen_paths:
                continue
            seen_paths.add(key)
            out.append({"role": role, "path": key, "sha256": sha256_path(resolved)})
    return sorted(out, key=lambda item: (item["role"], item["path"]))


def verify_input_bundle(root: Path | str, bundle: list[dict]) -> None:
    root = Path(root).resolve()
    if not isinstance(bundle, list) or not bundle:
        raise AutorouteError("input_bundle must be a non-empty array")
    canonical = sorted(bundle, key=lambda item: (item.get("role", ""), item.get("path", "")) if isinstance(item, dict) else ("", ""))
    if bundle != canonical:
        raise AutorouteError("input_bundle is not in canonical role/path order")
    seen_paths = set()
    seen_entries = set()
    for index, item in enumerate(bundle):
        _strict_object(item, f"input_bundle[{index}]", {"role", "path", "sha256"}, {"role", "path", "sha256"})
        if not isinstance(item["role"], str) or not item["role"]:
            raise AutorouteError(f"input_bundle[{index}].role is invalid")
        if not isinstance(item["path"], str) or not item["path"]:
            raise AutorouteError(f"input_bundle[{index}].path is invalid")
        entry_key = (item["role"], item["path"])
        if entry_key in seen_entries:
            raise AutorouteError(f"duplicate input bundle entry for {item['path']}")
        if item["path"] in seen_paths:
            raise AutorouteError(f"input bundle path has multiple roles: {item['path']}")
        seen_entries.add(entry_key)
        seen_paths.add(item["path"])
        if not _HEX64.match(str(item["sha256"])):
            raise AutorouteError(f"input_bundle[{index}].sha256 is invalid")
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise AutorouteError(f"input_bundle[{index}].path escapes its root")
        probe = root
        for component in relative.parts:
            probe = probe / component
            if probe.is_symlink():
                raise AutorouteError(
                    f"input_bundle[{index}] contains a symlink: {probe}"
                )
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError as exc:
            raise AutorouteError(f"input_bundle[{index}] resolves outside its root") from exc
        if not path.is_file() or sha256_path(path) != item["sha256"]:
            raise AutorouteError(f"input bundle mismatch for {item['path']}")


def declared_source_digest(path: Path | str, kind: str) -> str:
    """Return the v2 declaration digest, including a directory's membership."""
    path = Path(path)
    if kind == "file":
        if path.is_symlink() or not path.is_file():
            raise AutorouteError(f"declared source file is missing or a symlink: {path}")
        return sha256_path(path)
    if kind != "directory-recursive":
        raise AutorouteError(f"unsupported declared source kind {kind!r}")
    if path.is_symlink() or not path.is_dir():
        raise AutorouteError(f"declared source directory is missing or a symlink: {path}")
    descendants = sorted(path.rglob("*"))
    links = [candidate for candidate in descendants if candidate.is_symlink()]
    if links:
        raise AutorouteError(f"declared source directory contains a symlink: {links[0]}")
    members = [
        {
            "path": candidate.relative_to(path).as_posix(),
            "sha256": sha256_path(candidate),
        }
        for candidate in descendants
        if candidate.is_file()
    ]
    if not members:
        raise AutorouteError(f"declared source directory is empty: {path}")
    return canonical_json_sha256(members)


def build_v2_input_bundle(config: dict) -> list[dict]:
    """Expand and verify every explicit v2 source/tool declaration."""
    if config.get("schema") != CONFIG_SCHEMA_V2:
        raise AutorouteError("build_v2_input_bundle requires a v2 config")
    root = Path(config["project_root"]).resolve()
    entries: dict[str, Path] = {
        "autoroute-config": Path(config["config_path"]),
    }
    for source in config["sources"]:
        source_path = root / source["path"]
        actual = declared_source_digest(source_path, source["kind"])
        if actual != source["sha256"]:
            raise AutorouteError(
                f"declared source digest mismatch for {source['path']}: "
                f"expected {source['sha256']}, actual {actual}"
            )
        entries[f"source:{source['role']}"] = source_path
    for name in ("adapter", "applicator", "audit"):
        tool = config["tools"][name]
        tool_path = root / tool["path"]
        if tool_path.is_symlink() or not tool_path.is_file():
            raise AutorouteError(f"configured {name} is missing or a symlink: {tool_path}")
        actual = sha256_path(tool_path)
        if actual != tool["sha256"]:
            raise AutorouteError(
                f"configured {name} digest mismatch: expected {tool['sha256']}, actual {actual}"
            )
        entries[f"tool:{name}"] = tool_path
    reset = config["reset"]
    if reset["policy"] != "none":
        reset_path = root / reset["manifest"]
        if not reset_path.is_file() or sha256_path(reset_path) != reset["manifest_sha256"]:
            raise AutorouteError("route-reset manifest is missing or stale")
        entries["route-reset-manifest"] = reset_path
    baseline = config_path(config, config["seed"]["drc_baseline"])
    if baseline.exists():
        entries["drc-baseline"] = baseline
    return build_input_bundle(root, entries)


def _pos_nm(raw: Any, units: str, name: str) -> list[int]:
    if not isinstance(raw, dict) or set(raw) != {"x", "y"}:
        raise AutorouteError(f"{name} must contain exactly x and y")
    factor = Decimal(1_000_000) if units == "mm" else None
    if factor is None:
        raise AutorouteError(f"unsupported DRC coordinate_units {units!r}")
    out = []
    for axis in ("x", "y"):
        try:
            out.append(int((Decimal(str(raw[axis])) * factor).to_integral_exact()))
        except (InvalidOperation, ValueError) as exc:
            raise AutorouteError(f"{name}.{axis} is not an exact nanometre coordinate") from exc
    return out


def normalize_drc_report(report: dict, identity_map: dict[str, str]) -> dict:
    required = {
        "$schema", "coordinate_units", "ignored_checks", "included_severities",
        "kicad_version", "schematic_parity", "unconnected_items", "violations",
    }
    missing = sorted(required - set(report))
    if missing:
        raise AutorouteError(f"KiCad DRC JSON is missing key(s): {', '.join(missing)}")
    if report["$schema"] != "https://schemas.kicad.org/drc.v1.json":
        raise AutorouteError(f"unsupported KiCad DRC schema {report['$schema']!r}")
    units = report["coordinate_units"]

    ignored = report["ignored_checks"]
    if not isinstance(ignored, list):
        raise AutorouteError("ignored_checks must be an array")
    ignored_keys = []
    for index, item in enumerate(ignored):
        if not isinstance(item, dict) or not isinstance(item.get("key"), str):
            raise AutorouteError(f"ignored_checks[{index}] is malformed")
        ignored_keys.append(item["key"])

    findings = []
    for category in ("violations", "unconnected_items", "schematic_parity"):
        values = report[category]
        if not isinstance(values, list):
            raise AutorouteError(f"{category} must be an array")
        for finding_index, finding in enumerate(values):
            if not isinstance(finding, dict):
                raise AutorouteError(f"{category}[{finding_index}] must be an object")
            if not isinstance(finding.get("type"), str) or not isinstance(finding.get("severity"), str):
                raise AutorouteError(f"{category}[{finding_index}] lacks type/severity")
            raw_items = finding.get("items")
            if not isinstance(raw_items, list) or not raw_items:
                raise AutorouteError(f"{category}[{finding_index}] has no referenced items")
            item_keys = []
            for item_index, item in enumerate(raw_items):
                if not isinstance(item, dict) or not isinstance(item.get("uuid"), str):
                    raise AutorouteError(f"{category}[{finding_index}].items[{item_index}] lacks uuid")
                uuid = item["uuid"]
                identity = identity_map.get(uuid)
                if identity is None:
                    raise AutorouteError(f"DRC item UUID {uuid} cannot be mapped to board semantics")
                item_keys.append(
                    {"identity": identity, "position_nm": _pos_nm(item.get("pos"), units, f"{category}[{finding_index}].items[{item_index}].pos")}
                )
            key = {
                "category": category,
                "type": finding["type"],
                "severity": finding["severity"],
                "items": sorted(item_keys, key=lambda x: (x["identity"], x["position_nm"])),
            }
            findings.append(key)
    counts = collections.Counter(canonical_json_bytes(key).decode("utf-8") for key in findings)
    normalized_findings = [
        {"key": json.loads(raw), "count": count}
        for raw, count in sorted(counts.items())
    ]
    return {
        "drc_schema": report["$schema"],
        "coordinate_units": units,
        "kicad_version": report["kicad_version"],
        "ignored_checks": sorted(ignored_keys),
        "included_severities": sorted(report["included_severities"]),
        "findings": normalized_findings,
        "unconnected_count": len(report["unconnected_items"]),
    }


def make_drc_baseline(normalized: dict) -> dict:
    return {
        "schema": DRC_BASELINE_SCHEMA,
        "drc_contract": {
            key: normalized[key]
            for key in ("drc_schema", "coordinate_units", "kicad_version", "ignored_checks", "included_severities")
        },
        "findings": [
            {"key": item["key"], "count": item["count"], "disposition": "must_resolve"}
            for item in normalized["findings"]
        ],
    }


def compare_drc(normalized: dict, baseline: dict, *, final: bool) -> list[str]:
    _strict_object(baseline, "DRC baseline", {"schema", "drc_contract", "findings"}, {"schema", "drc_contract", "findings"})
    if baseline["schema"] != DRC_BASELINE_SCHEMA:
        raise AutorouteError(f"unsupported DRC baseline schema {baseline['schema']!r}")
    actual_contract = {
        key: normalized[key]
        for key in ("drc_schema", "coordinate_units", "kicad_version", "ignored_checks", "included_severities")
    }
    if actual_contract != baseline["drc_contract"]:
        return ["KiCad DRC contract differs from the tracked baseline"]
    allowed = collections.Counter()
    expected = collections.Counter()
    for index, item in enumerate(baseline["findings"]):
        _strict_object(item, f"baseline.findings[{index}]", {"key", "count", "disposition"}, {"key", "count", "disposition"})
        count = _positive_int(item["count"], f"baseline.findings[{index}].count")
        disposition = item["disposition"]
        if disposition not in {"must_resolve", "may_persist"}:
            raise AutorouteError(f"baseline.findings[{index}].disposition is invalid")
        raw = canonical_json_bytes(item["key"]).decode("utf-8")
        expected[raw] += count
        if disposition == "may_persist":
            allowed[raw] += count
    actual = collections.Counter(
        {canonical_json_bytes(item["key"]).decode("utf-8"): item["count"] for item in normalized["findings"]}
    )
    if not final:
        return [] if actual == expected else ["seed DRC finding multiset differs from its exact baseline"]
    problems = []
    if actual - allowed:
        problems.append("final DRC contains a new or must-resolve finding")
    if normalized["unconnected_count"]:
        problems.append(f"final DRC contains {normalized['unconnected_count']} unconnected item(s)")
    return problems


def _validate_manifest_scope(raw: Any, name: str = "manifest.scope") -> dict:
    scope = _strict_object(
        raw,
        name,
        {"net_classes", "resolved_nets", "net_to_class", "layers", "styles"},
        {"net_classes", "resolved_nets", "net_to_class", "layers", "styles"},
    )
    classes = scope["net_classes"]
    nets = scope["resolved_nets"]
    layers = scope["layers"]
    if not isinstance(classes, list) or not classes or not all(
        isinstance(value, str) and value for value in classes
    ) or len(classes) != len(set(classes)):
        raise AutorouteError(f"{name}.net_classes must be a non-empty unique string array")
    if not isinstance(nets, list) or not nets or not all(
        isinstance(value, str) and value for value in nets
    ) or nets != sorted(set(nets)):
        raise AutorouteError(f"{name}.resolved_nets must be a sorted non-empty unique string array")
    if not isinstance(layers, list) or not layers or not all(
        isinstance(value, str) and value for value in layers
    ) or len(layers) != len(set(layers)):
        raise AutorouteError(f"{name}.layers must be a non-empty unique copper-layer array")
    mapping = scope["net_to_class"]
    if not isinstance(mapping, dict) or set(mapping) != set(nets):
        raise AutorouteError(f"{name}.net_to_class must map every resolved net exactly once")
    if any(not isinstance(value, str) or value not in classes for value in mapping.values()):
        raise AutorouteError(f"{name}.net_to_class names an undeclared net class")
    styles = scope["styles"]
    if not isinstance(styles, dict) or set(styles) != set(classes):
        raise AutorouteError(f"{name}.styles must define exactly one style per net class")
    normalized_styles = {}
    for class_name in classes:
        style = _strict_object(
            styles[class_name],
            f"{name}.styles.{class_name}",
            {"track_width_nm", "clearance_nm", "via_diameter_nm", "via_drill_nm"},
            {"track_width_nm", "clearance_nm", "via_diameter_nm", "via_drill_nm"},
        )
        normalized_styles[class_name] = {
            key: _nm(value, f"{name}.styles.{class_name}.{key}")
            for key, value in style.items()
        }
        if normalized_styles[class_name]["via_drill_nm"] >= normalized_styles[class_name]["via_diameter_nm"]:
            raise AutorouteError(f"{name}.styles.{class_name} via drill must be smaller than diameter")
    if normalized_styles != styles:
        raise AutorouteError(f"{name}.styles is not canonical")
    return scope


def validate_promotion_report(report: Any) -> dict:
    """Validate every report field used by the promotion trust boundary."""
    root_keys = {
        "schema", "mode", "created_utc", "source", "tools", "limitations",
        "configuration", "workspace", "scratch_copies", "seed", "router_settings",
        "router_run", "candidate", "scope", "findings", "promotion", "verdict",
        "verdict_reason",
    }
    root = _strict_object(report, "candidate report", root_keys, root_keys)
    if root["schema"] != REPORT_SCHEMA:
        raise AutorouteError(f"unsupported candidate report schema {root['schema']!r}")
    if root["mode"] != "route-and-report" or root["verdict"] != "PROMOTABLE_CANDIDATE":
        raise AutorouteError("candidate report is not a promotable route-and-report run")
    if root["findings"] != []:
        raise AutorouteError("promotable candidate report contains findings")
    configuration = root["configuration"]
    if not isinstance(configuration, dict):
        raise AutorouteError("candidate report configuration is malformed")
    is_v2 = configuration.get("schema") == CONFIG_SCHEMA_V2
    promotion_keys = {
        "seed_sha256", "config_sha256", "input_bundle", "input_bundle_sha256",
        "applicator", "toolchain", "scope", "raw_candidate_sha256",
        "review_candidate_sha256", "routes", "routes_sha256", "checks", "blocks",
        "snapshot_schema",
    }
    if is_v2:
        promotion_keys |= {"seed_attestation", "selected_scope_policy"}
    promotion = _strict_object(
        root["promotion"],
        "candidate report promotion",
        promotion_keys,
        promotion_keys,
    )
    if promotion["snapshot_schema"] != SNAPSHOT_SCHEMA:
        raise AutorouteError("candidate report promotion snapshot schema is unsupported")
    for where in ("seed", "candidate"):
        semantic = root.get(where, {}).get("semantic")
        if not isinstance(semantic, dict) or semantic.get("snapshot_schema") != SNAPSHOT_SCHEMA:
            raise AutorouteError(
                f"candidate report {where} semantic snapshot schema is unsupported"
            )
    for key in (
        "seed_sha256", "config_sha256", "input_bundle_sha256",
        "raw_candidate_sha256", "review_candidate_sha256", "routes_sha256",
    ):
        if not _HEX64.fullmatch(str(promotion[key])):
            raise AutorouteError(f"candidate report promotion.{key} is invalid")
    if promotion["blocks"] != []:
        raise AutorouteError("candidate report still contains promotion blocks")
    checks = promotion["checks"]
    required_checks = PROMOTION_CHECKS
    if is_v2:
        policy = promotion["selected_scope_policy"]
        if policy not in {"routine", "project-audited"}:
            raise AutorouteError("candidate report selected_scope_policy is invalid")
        if policy == "routine":
            required_checks = PROMOTION_CHECKS_V2_ROUTINE
        attestation = validate_seed_attestation(
            promotion["seed_attestation"], "candidate report seed_attestation"
        )
        evidence = attestation["evidence"]
        if evidence["config_sha256"] != promotion["config_sha256"]:
            raise AutorouteError("seed attestation config digest differs from promotion")
        if evidence["input_bundle_sha256"] != promotion["input_bundle_sha256"]:
            raise AutorouteError("seed attestation input bundle differs from promotion")
    if not isinstance(checks, dict) or set(checks) != required_checks:
        raise AutorouteError("candidate report promotion checks differ from the exact required set")
    if any(value is not True for value in checks.values()):
        raise AutorouteError("candidate report does not prove every promotion check")
    bundle = promotion["input_bundle"]
    if canonical_json_sha256(bundle) != promotion["input_bundle_sha256"]:
        raise AutorouteError("candidate report input bundle digest is inconsistent")
    if is_v2:
        adapter_evidence = promotion["seed_attestation"]["evidence"]["adapter"]
        adapter_matches = [
            item for item in bundle
            if item.get("role") == "tool:adapter"
            and item.get("path") == adapter_evidence.get("path")
            and item.get("sha256") == adapter_evidence.get("sha256")
        ]
        if len(adapter_matches) != 1:
            raise AutorouteError("seed attestation adapter is not bound by the input bundle")
    routes = canonical_routes(promotion["routes"])
    if not routes or routes != promotion["routes"]:
        raise AutorouteError("candidate report routes are empty or noncanonical")
    if canonical_json_sha256(routes) != promotion["routes_sha256"]:
        raise AutorouteError("candidate report route digest is inconsistent")
    scope = _validate_manifest_scope(promotion["scope"], "candidate report promotion.scope")
    candidate = root["candidate"]
    if not isinstance(candidate, dict) or candidate.get("board_sha256") != promotion["review_candidate_sha256"]:
        raise AutorouteError("candidate report does not bind its review board digest")
    filtered = candidate.get("filtered")
    if not isinstance(filtered, dict) or filtered.get("routes") != routes or filtered.get("routes_sha256") != promotion["routes_sha256"]:
        raise AutorouteError("candidate report filtered routes differ from promotion routes")
    if configuration.get("sha256") != promotion["config_sha256"]:
        raise AutorouteError("candidate report configuration digest differs from promotion")
    manifest_probe = {
            "schema": MANIFEST_SCHEMA_V2 if is_v2 else MANIFEST_SCHEMA,
            "snapshot_schema": promotion["snapshot_schema"],
            "seed_sha256": promotion["seed_sha256"],
            "applicator": promotion["applicator"],
            "input_bundle": promotion["input_bundle"],
            "toolchain": promotion["toolchain"],
            "scope": scope,
            "candidate": {
                "raw_sha256": promotion["raw_candidate_sha256"],
                "review_sha256": promotion["review_candidate_sha256"],
                "report_sha256": "0" * 64,
            },
            "routes": routes,
            "routes_sha256": promotion["routes_sha256"],
    }
    if is_v2:
        manifest_probe["seed_attestation"] = promotion["seed_attestation"]
    validate_manifest(manifest_probe)
    return root


def validate_manifest(manifest: dict) -> dict:
    if not isinstance(manifest, dict):
        raise AutorouteError("route manifest must be a JSON object")
    schema = manifest.get("schema")
    if schema not in {MANIFEST_SCHEMA, MANIFEST_SCHEMA_V2}:
        raise AutorouteError(f"unsupported route manifest schema {schema!r}")
    root_keys = {
        "schema", "snapshot_schema", "seed_sha256", "applicator", "input_bundle", "toolchain",
        "scope", "candidate", "routes", "routes_sha256",
    }
    if schema == MANIFEST_SCHEMA_V2:
        root_keys.add("seed_attestation")
    root = _strict_object(
        manifest,
        "route manifest",
        root_keys,
        root_keys,
    )
    if root["snapshot_schema"] != SNAPSHOT_SCHEMA:
        raise AutorouteError("manifest snapshot_schema is unsupported")
    if schema == MANIFEST_SCHEMA_V2:
        validate_seed_attestation(root["seed_attestation"], "manifest.seed_attestation")
    if not _HEX64.match(str(root["seed_sha256"])):
        raise AutorouteError("manifest seed_sha256 is invalid")
    applicator = _strict_object(
        root["applicator"],
        "manifest.applicator",
        {"schema_version", "bundle_path", "source_sha256"},
        {"schema_version", "bundle_path", "source_sha256"},
    )
    if applicator["schema_version"] != ROUTE_APPLICATOR_VERSION:
        raise AutorouteError("manifest applicator schema version is unsupported")
    bundle_path = Path(applicator["bundle_path"])
    if bundle_path.is_absolute() or ".." in bundle_path.parts:
        raise AutorouteError("manifest applicator bundle_path escapes its root")
    if not _HEX64.match(str(applicator["source_sha256"])):
        raise AutorouteError("manifest applicator source_sha256 is invalid")
    if not isinstance(root["input_bundle"], list) or not root["input_bundle"]:
        raise AutorouteError("manifest input_bundle must be a non-empty array")
    if root["input_bundle"] != sorted(
        root["input_bundle"],
        key=lambda item: (item.get("role", ""), item.get("path", ""))
        if isinstance(item, dict) else ("", ""),
    ):
        raise AutorouteError("manifest input_bundle is not in canonical role/path order")
    bundle_matches = []
    seen_bundle_paths = set()
    for index, item in enumerate(root["input_bundle"]):
        _strict_object(
            item,
            f"manifest.input_bundle[{index}]",
            {"role", "path", "sha256"},
            {"role", "path", "sha256"},
        )
        relative = Path(item["path"])
        if not isinstance(item["role"], str) or not item["role"]:
            raise AutorouteError(f"manifest.input_bundle[{index}].role is invalid")
        if not isinstance(item["path"], str) or not item["path"]:
            raise AutorouteError(f"manifest.input_bundle[{index}].path is invalid")
        if item["path"] in seen_bundle_paths:
            raise AutorouteError(f"manifest input bundle path is duplicated: {item['path']}")
        seen_bundle_paths.add(item["path"])
        if relative.is_absolute() or ".." in relative.parts:
            raise AutorouteError(f"manifest.input_bundle[{index}].path escapes its root")
        if not _HEX64.match(str(item["sha256"])):
            raise AutorouteError(f"manifest.input_bundle[{index}].sha256 is invalid")
        if item["path"] == applicator["bundle_path"]:
            bundle_matches.append(item)
    if len(bundle_matches) != 1 or bundle_matches[0]["sha256"] != applicator["source_sha256"]:
        raise AutorouteError("manifest applicator does not match exactly one bundled source")
    toolchain = _strict_object(
        root["toolchain"],
        "manifest.toolchain",
        {
            "backend", "freerouting_version", "freerouting_sha256",
            "java_version", "install_receipt_sha256",
            "compatibility_matrix_sha256", "compatibility_cell",
        },
        {
            "backend", "freerouting_version", "freerouting_sha256",
            "java_version", "install_receipt_sha256",
            "compatibility_matrix_sha256", "compatibility_cell",
        },
    )
    if toolchain["backend"] != BACKEND_ID:
        raise AutorouteError("manifest toolchain backend differs from the supported backend")
    for key in ("freerouting_sha256", "install_receipt_sha256", "compatibility_matrix_sha256"):
        if not _HEX64.match(str(toolchain[key])):
            raise AutorouteError(f"manifest.toolchain.{key} is invalid")
    _strict_object(
        toolchain["compatibility_cell"],
        "manifest.toolchain.compatibility_cell",
        {"os", "arch", "kicad_cli", "pcbnew", "snapshot_schema"},
        {"os", "arch", "kicad_cli", "pcbnew", "snapshot_schema"},
    )
    if toolchain["compatibility_cell"]["snapshot_schema"] != root["snapshot_schema"]:
        raise AutorouteError("manifest compatibility cell snapshot schema differs from manifest")
    scope = _validate_manifest_scope(root["scope"])
    candidate = _strict_object(
        root["candidate"],
        "manifest.candidate",
        {"raw_sha256", "review_sha256", "report_sha256"},
        {"raw_sha256", "review_sha256", "report_sha256"},
    )
    for key, value in candidate.items():
        if not _HEX64.match(str(value)):
            raise AutorouteError(f"manifest.candidate.{key} is invalid")
    routes = canonical_routes(root["routes"])
    if routes != root["routes"]:
        raise AutorouteError("manifest routes are not in canonical order/form")
    if canonical_json_sha256(routes) != root["routes_sha256"]:
        raise AutorouteError("manifest routes_sha256 does not match routes")
    allowed_nets = set(scope["resolved_nets"])
    allowed_layers = set(scope["layers"])
    for route in routes:
        if route["net"] not in allowed_nets:
            raise AutorouteError(f"manifest route uses out-of-scope net {route['net']!r}")
        route_layers = {route["layer"]} if route["kind"] == "segment" else set(route["layers"])
        if not route_layers <= allowed_layers:
            raise AutorouteError(f"manifest route uses out-of-scope layer(s): {sorted(route_layers - allowed_layers)}")
        class_name = scope["net_to_class"][route["net"]]
        style = scope["styles"][class_name]
        if route["kind"] == "segment" and route["width_nm"] != style["track_width_nm"]:
            raise AutorouteError(
                f"manifest segment on {route['net']} has width {route['width_nm']}, expected {style['track_width_nm']}"
            )
        if route["kind"] == "via" and (
            route["diameter_nm"] != style["via_diameter_nm"]
            or route["drill_nm"] != style["via_drill_nm"]
        ):
            raise AutorouteError(f"manifest via on {route['net']} differs from its net-class style")
    return root


def _sexpr_tokens(text: str) -> list[str]:
    tokens: list[str] = []
    index = 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char in "()":
            tokens.append(char)
            index += 1
            continue
        if char == '"':
            start = index
            index += 1
            escaped = False
            while index < len(text):
                current = text[index]
                index += 1
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    break
            else:
                raise AutorouteError("unterminated string in KiCad S-expression")
            tokens.append(text[start:index])
            continue
        start = index
        while index < len(text) and not text[index].isspace() and text[index] not in "()":
            index += 1
        tokens.append(text[start:index])
    return tokens


def _parse_sexpr(text: str) -> list:
    tokens = _sexpr_tokens(text)
    stack: list[list] = []
    root: list = []
    current = root
    for token in tokens:
        if token == "(":
            child: list = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ")":
            if not stack:
                raise AutorouteError("unbalanced ')' in KiCad S-expression")
            current = stack.pop()
        else:
            current.append(token)
    if stack:
        raise AutorouteError("unbalanced '(' in KiCad S-expression")
    if len(root) != 1 or not isinstance(root[0], list):
        raise AutorouteError("KiCad file does not contain one root S-expression")
    return root[0]


def _project_nonrouting(node):
    if not isinstance(node, list):
        return node
    head = node[0] if node and isinstance(node[0], str) else None
    if head in {"segment", "arc", "via", "filled_polygon", "fill_segments", "filled_segments"}:
        return None
    projected = []
    for child in node:
        value = _project_nonrouting(child)
        if value is not None:
            projected.append(value)
    return projected


def nonrouting_projection(path: Path | str) -> list:
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise AutorouteError(f"cannot read KiCad board {path}: {exc}") from exc
    root = _parse_sexpr(text)
    if not root or root[0] != "kicad_pcb":
        raise AutorouteError(f"{path} is not a KiCad PCB S-expression")
    return _project_nonrouting(root)


def nonrouting_projection_sha256(path: Path | str) -> str:
    return canonical_json_sha256(nonrouting_projection(path))


def seed_context_bundle(board_path: Path | str) -> list[dict]:
    """Hash the KiCad context an adapter must emit beside a seed board."""
    board_path = Path(board_path).resolve()
    root = board_path.parent
    suffixes = {".kicad_pro", ".kicad_sch", ".kicad_dru", ".kicad_sym", ".kicad_mod"}
    names = {"fp-lib-table", "sym-lib-table"}
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file()
        and path != board_path
        and (path.suffix in suffixes or path.name in names)
    )
    for path in files:
        if path.is_symlink():
            raise AutorouteError(f"seed context contains a symlink: {path}")
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": sha256_path(path)}
        for path in files
    ]


def make_seed_attestation(
    snapshot: dict,
    board_path: Path | str,
    config: dict,
    input_bundle: list[dict],
) -> dict:
    """Bind a v2 seed's complete route state and non-routing projection.

    The byte digest remains useful evidence, but is deliberately not the
    authority: KiCad may reorder or migrate serialization.  Promotion instead
    binds canonical routing state, effective net classes/layers, the complete
    non-routing S-expression projection, and the adapter/source/reset inputs.
    """
    if config.get("schema") != CONFIG_SCHEMA_V2:
        raise AutorouteError("seed attestation requires a v2 config")
    try:
        routing_items = snapshot["routing"]["items"]
        board = snapshot["board"]
        net_to_class = snapshot["netclasses"]["net_to_class"]
    except (KeyError, TypeError) as exc:
        raise AutorouteError("seed semantic snapshot is incomplete") from exc
    states = []
    for index, item in enumerate(routing_items):
        if not isinstance(item, dict):
            raise AutorouteError(f"seed route state {index} is malformed")
        kind = item.get("kind")
        if kind == "segment":
            route = canonical_route(
                {
                    "kind": "segment",
                    "net": item.get("net"),
                    "layer": item.get("layer"),
                    "width_nm": item.get("width_nm"),
                    "start_nm": item.get("start_nm"),
                    "end_nm": item.get("end_nm"),
                }
            )
        elif kind == "via":
            if item.get("via_type") not in (0, 4):
                raise AutorouteError(
                    "seed attestation supports ordinary F.Cu-to-B.Cu through-vias only"
                )
            route = canonical_route(
                {
                    "kind": "via",
                    "net": item.get("net"),
                    "at_nm": item.get("position_nm"),
                    "diameter_nm": item.get("width_nm"),
                    "drill_nm": item.get("drill_nm"),
                    "layers": [item.get("top_layer"), item.get("bottom_layer")],
                }
            )
            if route["layers"] != ["F.Cu", "B.Cu"]:
                raise AutorouteError(
                    "seed attestation supports F.Cu-to-B.Cu through-vias only"
                )
        else:
            raise AutorouteError(
                f"seed contains unsupported route kind for attestation: {kind!r}"
            )
        states.append({"route": route, "locked": item.get("locked") is True})
    states.sort(key=lambda value: json.dumps(value, sort_keys=True, separators=(",", ":")))
    semantic = {
        "board": board,
        "net_to_class": dict(sorted(net_to_class.items())),
        "route_state_count": len(states),
        "route_states_sha256": canonical_json_sha256(states),
        "nonrouting_projection_sha256": nonrouting_projection_sha256(board_path),
        "context_bundle": seed_context_bundle(board_path),
    }
    semantic["context_bundle_sha256"] = canonical_json_sha256(
        semantic["context_bundle"]
    )
    evidence = {
        "config_sha256": config["config_sha256"],
        "input_bundle_sha256": canonical_json_sha256(input_bundle),
        "adapter": config["tools"]["adapter"],
        "project_mode": config["project"]["mode"],
        "reset": config["reset"],
    }
    attestation = {
        "schema": SEED_ATTESTATION_SCHEMA,
        "semantic": semantic,
        "evidence": evidence,
    }
    attestation["sha256"] = canonical_json_sha256(attestation)
    return attestation


def validate_seed_attestation(value: Any, where: str = "seed_attestation") -> dict:
    root = _strict_object(
        value,
        where,
        {"schema", "semantic", "evidence", "sha256"},
        {"schema", "semantic", "evidence", "sha256"},
    )
    if root["schema"] != SEED_ATTESTATION_SCHEMA:
        raise AutorouteError(f"{where}.schema is unsupported")
    semantic = _strict_object(
        root["semantic"],
        f"{where}.semantic",
        {
            "board", "net_to_class", "route_state_count",
            "route_states_sha256", "nonrouting_projection_sha256",
            "context_bundle", "context_bundle_sha256",
        },
        {
            "board", "net_to_class", "route_state_count",
            "route_states_sha256", "nonrouting_projection_sha256",
            "context_bundle", "context_bundle_sha256",
        },
    )
    route_count = semantic["route_state_count"]
    if isinstance(route_count, bool) or not isinstance(route_count, int) or route_count < 0:
        raise AutorouteError(f"{where}.semantic.route_state_count must be a non-negative integer")
    if not isinstance(semantic["net_to_class"], dict):
        raise AutorouteError(f"{where}.semantic.net_to_class must be an object")
    if not isinstance(semantic["context_bundle"], list):
        raise AutorouteError(f"{where}.semantic.context_bundle must be an array")
    if canonical_json_sha256(semantic["context_bundle"]) != semantic["context_bundle_sha256"]:
        raise AutorouteError(f"{where}.semantic.context bundle digest is inconsistent")
    for key in (
        "route_states_sha256", "nonrouting_projection_sha256",
        "context_bundle_sha256",
    ):
        if not _HEX64.fullmatch(str(semantic[key])):
            raise AutorouteError(f"{where}.semantic.{key} is invalid")
    evidence = _strict_object(
        root["evidence"],
        f"{where}.evidence",
        {"config_sha256", "input_bundle_sha256", "adapter", "project_mode", "reset"},
        {"config_sha256", "input_bundle_sha256", "adapter", "project_mode", "reset"},
    )
    for key in ("config_sha256", "input_bundle_sha256"):
        if not _HEX64.fullmatch(str(evidence[key])):
            raise AutorouteError(f"{where}.evidence.{key} is invalid")
    if evidence["project_mode"] not in {"board-snapshot", "generator-adapter"}:
        raise AutorouteError(f"{where}.evidence.project_mode is invalid")
    if not isinstance(evidence["adapter"], dict) or not isinstance(evidence["reset"], dict):
        raise AutorouteError(f"{where} adapter/reset evidence is malformed")
    if not _HEX64.fullmatch(str(root["sha256"])):
        raise AutorouteError(f"{where}.sha256 is invalid")
    unsigned = {key: root[key] for key in ("schema", "semantic", "evidence")}
    if canonical_json_sha256(unsigned) != root["sha256"]:
        raise AutorouteError(f"{where}.sha256 is inconsistent")
    return root
