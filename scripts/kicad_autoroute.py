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
MANIFEST_SCHEMA = "kicad-route-manifest-v1"
REPORT_SCHEMA = "kicad-route-candidate-report-v1"
DRC_BASELINE_SCHEMA = "kicad-drc-baseline-v1"
BACKEND_ID = "freerouting-2.3.0-temurin-25.0.4+7"
ROUTE_APPLICATOR_VERSION = "1"
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


def load_config(path: Path | str) -> dict:
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


def config_path(config: dict, relative: str) -> Path:
    return (Path(config["config_dir"]) / relative).resolve()


def resolve_project_netclasses(project_path: Path | str, selected: Iterable[str]) -> dict:
    project_path = Path(project_path)
    try:
        project = json.loads(project_path.read_text(encoding="utf-8-sig"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise AutorouteError(f"cannot read KiCad project {project_path}: {exc}") from exc
    settings = project.get("net_settings")
    if not isinstance(settings, dict):
        raise AutorouteError("KiCad project has no net_settings object")
    if settings.get("netclass_patterns"):
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
        if not isinstance(raw["layer"], str) or not raw["layer"].endswith(".Cu"):
            raise AutorouteError("segment.layer must be a KiCad copper layer")
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
    return {"routes": canonical_routes(accepted), "discarded_drift": drift}


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
            relative = path.relative_to(root)
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
        isinstance(value, str) and value.endswith(".Cu") for value in layers
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
    promotion = _strict_object(
        root["promotion"],
        "candidate report promotion",
        {
            "seed_sha256", "config_sha256", "input_bundle", "input_bundle_sha256",
            "applicator", "toolchain", "scope", "raw_candidate_sha256",
            "review_candidate_sha256", "routes", "routes_sha256", "checks", "blocks",
        },
        {
            "seed_sha256", "config_sha256", "input_bundle", "input_bundle_sha256",
            "applicator", "toolchain", "scope", "raw_candidate_sha256",
            "review_candidate_sha256", "routes", "routes_sha256", "checks", "blocks",
        },
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
    if not isinstance(checks, dict) or set(checks) != PROMOTION_CHECKS:
        raise AutorouteError("candidate report promotion checks differ from the exact required set")
    if any(value is not True for value in checks.values()):
        raise AutorouteError("candidate report does not prove every promotion check")
    bundle = promotion["input_bundle"]
    if canonical_json_sha256(bundle) != promotion["input_bundle_sha256"]:
        raise AutorouteError("candidate report input bundle digest is inconsistent")
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
    configuration = root["configuration"]
    if not isinstance(configuration, dict) or configuration.get("sha256") != promotion["config_sha256"]:
        raise AutorouteError("candidate report configuration digest differs from promotion")
    validate_manifest(
        {
            "schema": MANIFEST_SCHEMA,
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
    )
    return root


def validate_manifest(manifest: dict) -> dict:
    root = _strict_object(
        manifest,
        "route manifest",
        {"schema", "seed_sha256", "applicator", "input_bundle", "toolchain", "scope", "candidate", "routes", "routes_sha256"},
        {"schema", "seed_sha256", "applicator", "input_bundle", "toolchain", "scope", "candidate", "routes", "routes_sha256"},
    )
    if root["schema"] != MANIFEST_SCHEMA:
        raise AutorouteError(f"unsupported route manifest schema {root['schema']!r}")
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
        {"os", "arch", "kicad_cli", "pcbnew"},
        {"os", "arch", "kicad_cli", "pcbnew"},
    )
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
