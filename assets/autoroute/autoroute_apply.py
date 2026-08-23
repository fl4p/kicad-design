#!/usr/bin/env python3
"""Generic, fail-closed KiCad autoroute snapshot applicator.

Run this file with KiCad's Python.  It owns exact route reset and canonical
manifest application; project adapters own only source regeneration and any
project-specific post-processing around this helper.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys


CONFIG_SCHEMA = "kicad-autoroute-config-v2"
MANIFEST_SCHEMA = "kicad-route-manifest-v4"
SNAPSHOT_SCHEMA = "kicad-route-semantic-snapshot-v5"
SEED_ATTESTATION_SCHEMA = "kicad-autoroute-seed-attestation-v1"
RESET_SCHEMA = "kicad-autoroute-route-reset-v1"
REPORT_SCHEMA = "kicad-autoroute-apply-report-v1"
_HEX = re.compile(r"^[0-9a-f]{64}$")
_SETTINGS_MANAGER = None


class ApplyError(RuntimeError):
    pass


def _init_pcbnew():
    try:
        import wx
        wx.Log.SetLogLevel(wx.LOG_Error)
        app = wx.AppConsole()
        import pcbnew
    except Exception as exc:
        raise ApplyError(f"cannot initialize KiCad pcbnew runtime: {exc}") from exc
    # wx.App(False) is a GUI app and hangs or degrades GetSettingsManager() to
    # a raw SwigPyObject in headless KiCad 10.0.5/Darwin.  AppConsole supplies
    # standard paths while preserving the typed SETTINGS_MANAGER API.
    return pcbnew, app


def _json_bytes(value) -> bytes:
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        + "\n"
    ).encode("utf-8")


def _json_sha(value) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name("." + path.name + ".tmp")
    temp.write_bytes(_json_bytes(value))
    temp.replace(path)


def _run_external(
    command: list[str],
    *,
    timeout_seconds: int,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            command,
            cwd=None if cwd is None else str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        raise ApplyError(
            f"subprocess timed out after {timeout_seconds} seconds: {command[0]}"
        ) from exc


def _inside(path: Path, root: Path, name: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ApplyError(f"{name} must stay below output-dir: {resolved}") from exc
    return resolved


def _relative(root: Path, raw, name: str) -> Path:
    if not isinstance(raw, str) or not raw:
        raise ApplyError(f"{name} must be a non-empty relative path")
    value = Path(raw)
    if value.is_absolute() or ".." in value.parts:
        raise ApplyError(f"{name} escapes project_root")
    resolved = (root / value).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise ApplyError(f"{name} resolves outside project_root") from exc
    return resolved


def _load_config(path: Path) -> tuple[dict, Path]:
    config = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(config, dict) or config.get("schema") != CONFIG_SCHEMA:
        raise ApplyError("applicator requires kicad-autoroute-config-v2")
    if set(config.get("project", {})) != {
        "root", "mode", "board_basename", "schematic_authority",
        "source_board", "project_file", "schematic_file",
    }:
        raise ApplyError("config.project has an unsupported shape")
    if config["project"]["root"] != ".":
        raise ApplyError("v2 applicator requires project.root='.'")
    root = _relative(path.parent.resolve(), config["project"]["root"], "project.root")
    if not root.is_dir():
        raise ApplyError(f"project_root is missing: {root}")
    return config, root


def _directory_digest(path: Path) -> str:
    descendants = sorted(path.rglob("*"))
    links = [item for item in descendants if item.is_symlink()]
    if links:
        raise ApplyError(f"source directory contains a symlink: {links[0]}")
    members = [
        {"path": item.relative_to(path).as_posix(), "sha256": _sha(item)}
        for item in descendants if item.is_file()
    ]
    if not members:
        raise ApplyError(f"source directory is empty: {path}")
    return _json_sha(members)


def _verify_sources(config: dict, root: Path) -> dict[str, str]:
    result = {}
    for source in config.get("sources", []):
        path = _relative(root, source.get("path"), "sources[].path")
        kind = source.get("kind")
        if path.is_symlink():
            raise ApplyError(f"source is a symlink: {path}")
        if kind == "file" and path.is_file():
            actual = _sha(path)
        elif kind == "directory-recursive" and path.is_dir():
            actual = _directory_digest(path)
        else:
            raise ApplyError(f"source kind/path mismatch: {source}")
        if actual != source.get("sha256"):
            raise ApplyError(f"source digest mismatch: {source.get('path')}")
        result[source["path"]] = actual
    if not result:
        raise ApplyError("config declares no immutable sources")
    for name in ("adapter", "applicator", "audit"):
        spec = config["tools"][name]
        path = _relative(root, spec["path"], f"tools.{name}.path")
        if not path.is_file() or path.is_symlink() or _sha(path) != spec["sha256"]:
            raise ApplyError(f"configured {name} is missing, linked, or stale")
    return result


def _live_input_bundle(config: dict, root: Path, config_path: Path) -> list[dict]:
    entries: list[tuple[str, Path]] = [("autoroute-config", config_path)]
    for source in config["sources"]:
        path = _relative(root, source["path"], "sources[].path")
        if source["kind"] == "file":
            entries.append(("source:" + source["role"], path))
        else:
            for member in sorted(path.rglob("*")):
                if member.is_symlink():
                    raise ApplyError(f"source directory contains a symlink: {member}")
                if member.is_file():
                    entries.append(("source:" + source["role"], member))
    for name in ("adapter", "applicator", "audit"):
        entries.append(("tool:" + name, _relative(root, config["tools"][name]["path"], f"tools.{name}.path")))
    if config["reset"]["policy"] != "none":
        entries.append(("route-reset-manifest", _relative(root, config["reset"]["manifest"], "reset.manifest")))
    baseline = _relative(root, config["seed"]["drc_baseline"], "seed.drc_baseline")
    if baseline.is_file():
        entries.append(("drc-baseline", baseline))
    bundle = []
    seen = set()
    for role, path in sorted(entries, key=lambda item: item[0]):
        relative = path.resolve().relative_to(root).as_posix()
        if relative in seen:
            continue
        seen.add(relative)
        bundle.append({"role": role, "path": relative, "sha256": _sha(path)})
    return sorted(bundle, key=lambda item: (item["role"], item["path"]))


def _point(value) -> list[int]:
    return [int(value.x), int(value.y)]


def _uuid(item) -> str:
    for getter in (
        lambda: item.m_Uuid.AsString(),
        lambda: item.GetUuid().AsString(),
        lambda: str(item.m_Uuid),
    ):
        try:
            value = getter()
        except Exception:
            continue
        if value:
            return str(value)
    raise ApplyError(f"cannot read route UUID for {type(item).__name__}")


def _canonical_route(item, board, pcbnew) -> dict:
    if isinstance(item, pcbnew.PCB_VIA):
        via_type = int(item.GetViaType())
        layers = [
            str(board.GetLayerName(int(item.TopLayer()))),
            str(board.GetLayerName(int(item.BottomLayer()))),
        ]
        if via_type not in (0, 4) or layers != ["F.Cu", "B.Cu"]:
            raise ApplyError("only F.Cu-to-B.Cu through-vias are supported")
        diameter = int(item.GetFrontWidth())
        drill = int(item.GetDrillValue())
        if drill <= 0 or diameter <= drill:
            raise ApplyError("invalid through-via dimensions")
        return {
            "kind": "via", "net": str(item.GetNetname()),
            "at_nm": _point(item.GetPosition()), "diameter_nm": diameter,
            "drill_nm": drill, "layers": layers,
        }
    if isinstance(item, pcbnew.PCB_ARC):
        raise ApplyError("route arcs are unsupported by this applicator")
    if isinstance(item, pcbnew.PCB_TRACK):
        start, end = sorted((_point(item.GetStart()), _point(item.GetEnd())))
        if start == end:
            raise ApplyError("zero-length route segment")
        return {
            "kind": "segment", "net": str(item.GetNetname()),
            "layer": str(item.GetLayerName()), "width_nm": int(item.GetWidth()),
            "start_nm": start, "end_nm": end,
        }
    raise ApplyError(f"unsupported routing object {type(item).__name__}")


def _record(item, board, pcbnew) -> dict:
    route = _canonical_route(item, board, pcbnew)
    return {
        "uuid": _uuid(item),
        "route": route,
        "locked": bool(item.IsLocked()),
        "primitive_type": (
            int(item.GetViaType()) if isinstance(item, pcbnew.PCB_VIA)
            else route["kind"]
        ),
        "multiplicity": 1,
    }


def _route_key(route: dict) -> str:
    return json.dumps(route, sort_keys=True, separators=(",", ":"))


def _routes(board, pcbnew) -> list[dict]:
    return sorted(
        (_canonical_route(item, board, pcbnew) for item in board.GetTracks()),
        key=_route_key,
    )


def _route_states(board, pcbnew) -> list[dict]:
    return sorted(
        (
            {"route": _canonical_route(item, board, pcbnew), "locked": bool(item.IsLocked())}
            for item in board.GetTracks()
        ),
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )


def _load_board_with_project(path: Path, pcbnew):
    global _SETTINGS_MANAGER
    board = pcbnew.LoadBoard(str(path))
    project_path = path.with_suffix(".kicad_pro")
    manager = None
    project = None
    if project_path.is_file():
        if _SETTINGS_MANAGER is None:
            _SETTINGS_MANAGER = pcbnew.GetSettingsManager()
        manager = _SETTINGS_MANAGER
        if not hasattr(manager, "LoadProject"):
            raise ApplyError("KiCad returned an untyped settings-manager proxy")
        if not manager.LoadProject(str(project_path)):
            raise ApplyError(f"KiCad could not load project context {project_path}")
        project = manager.GetProject(str(project_path))
        if project is None:
            raise ApplyError(f"KiCad returned no project object for {project_path}")
        board.SetProject(project)
        board.SynchronizeNetsAndNetClasses(False)
    return board, manager, project


def _seed_context_bundle(board_path: Path) -> list[dict]:
    root = board_path.parent
    suffixes = {".kicad_pro", ".kicad_sch", ".kicad_dru", ".kicad_sym", ".kicad_mod"}
    names = {"fp-lib-table", "sym-lib-table"}
    files = sorted(
        path for path in root.rglob("*")
        if path.is_file() and path != board_path
        and (path.suffix in suffixes or path.name in names)
    )
    if any(path.is_symlink() for path in files):
        raise ApplyError("seed context contains a symlink")
    return [
        {"path": path.relative_to(root).as_posix(), "sha256": _sha(path)}
        for path in files
    ]


def _seed_attestation(
    board,
    board_path: Path,
    config: dict,
    config_path: Path,
    input_bundle: list[dict],
    pcbnew,
) -> dict:
    states = _route_states(board, pcbnew)
    net_to_class = {}
    for net in board.GetNetInfo().NetsByName().values():
        name = str(net.GetNetname())
        if name:
            net_to_class[name] = str(net.GetNetClassName())
    semantic = {
        "board": {
            "copper_layer_count": int(board.GetCopperLayerCount()),
            "copper_layers": [
                str(board.GetLayerName(layer)) for layer in range(64)
                if board.IsLayerEnabled(layer) and pcbnew.IsCopperLayer(layer)
            ],
            "enabled_layers": [int(value) for value in board.GetEnabledLayers().Seq()],
        },
        "net_to_class": dict(sorted(net_to_class.items())),
        "route_state_count": len(states),
        "route_states_sha256": _json_sha(states),
        "nonrouting_projection_sha256": _nonrouting_sha(board_path),
        "context_bundle": _seed_context_bundle(board_path),
    }
    semantic["context_bundle_sha256"] = _json_sha(semantic["context_bundle"])
    evidence = {
        "config_sha256": _sha(config_path),
        "input_bundle_sha256": _json_sha(input_bundle),
        "adapter": config["tools"]["adapter"],
        "project_mode": config["project"]["mode"],
        "reset": config["reset"],
    }
    result = {
        "schema": SEED_ATTESTATION_SCHEMA,
        "semantic": semantic,
        "evidence": evidence,
    }
    result["sha256"] = _json_sha(result)
    return result


def _validate_additions(routes) -> list[dict]:
    if not isinstance(routes, list) or not routes:
        raise ApplyError("manifest routes must be a non-empty array")
    normalized = []
    for route in routes:
        if not isinstance(route, dict) or route.get("kind") not in {"segment", "via"}:
            raise ApplyError("manifest contains an unsupported route")
        item = dict(route)
        if item["kind"] == "segment":
            required = {"kind", "net", "layer", "width_nm", "start_nm", "end_nm"}
            if set(item) != required or not isinstance(item["layer"], str) or not item["layer"]:
                raise ApplyError("malformed manifest segment")
            item["start_nm"], item["end_nm"] = sorted((item["start_nm"], item["end_nm"]))
            if item["start_nm"] == item["end_nm"] or int(item["width_nm"]) <= 0:
                raise ApplyError("invalid manifest segment geometry")
        else:
            required = {"kind", "net", "at_nm", "diameter_nm", "drill_nm", "layers"}
            if set(item) != required or item["layers"] != ["F.Cu", "B.Cu"]:
                raise ApplyError("malformed manifest through-via")
            if int(item["drill_nm"]) <= 0 or int(item["diameter_nm"]) <= int(item["drill_nm"]):
                raise ApplyError("invalid manifest via geometry")
        normalized.append(item)
    normalized.sort(key=_route_key)
    if normalized != routes or len({_route_key(item) for item in routes}) != len(routes):
        raise ApplyError("manifest routes are noncanonical or duplicated")
    return normalized


def _validate_scope(config: dict, manifest: dict, routes: list[dict]) -> None:
    scope = manifest.get("scope") or {}
    expected = config["scope"]
    if scope.get("net_classes") != expected["net_classes"]:
        raise ApplyError("manifest net classes differ from config")
    if scope.get("resolved_nets") != sorted(expected["net_to_class"]):
        raise ApplyError("manifest resolved nets differ from frozen config inventory")
    if scope.get("net_to_class") != expected["net_to_class"]:
        raise ApplyError("manifest net_to_class differs from config")
    if scope.get("layers") != expected["layers"] or scope.get("styles") != expected["styles"]:
        raise ApplyError("manifest layers/styles differ from config")
    allowed_layers = set(expected["layers"])
    for route in routes:
        class_name = expected["net_to_class"].get(route["net"])
        if class_name is None:
            raise ApplyError("manifest contains a route outside the frozen selected-net scope")
        style = expected["styles"][class_name]
        if route["kind"] == "segment":
            if route["layer"] not in allowed_layers or route["width_nm"] != style["track_width_nm"]:
                raise ApplyError(f"manifest segment on {route['net']} violates layer/style scope")
        elif route["diameter_nm"] != style["via_diameter_nm"] or route["drill_nm"] != style["via_drill_nm"]:
            raise ApplyError(f"manifest via on {route['net']} violates style scope")


def _validate_manifest_envelope(config: dict, manifest: dict) -> None:
    required = {
        "schema", "snapshot_schema", "seed_sha256", "applicator",
        "input_bundle", "toolchain", "scope", "candidate", "routes",
        "routes_sha256", "seed_attestation",
    }
    if not isinstance(manifest, dict) or set(manifest) != required:
        raise ApplyError("manifest top-level fields differ from the v4 contract")
    if manifest["schema"] != MANIFEST_SCHEMA or not _HEX.fullmatch(str(manifest["seed_sha256"])):
        raise ApplyError("manifest schema or seed digest is invalid")
    if manifest["snapshot_schema"] != SNAPSHOT_SCHEMA:
        raise ApplyError("manifest snapshot schema is unsupported")
    if not _HEX.fullmatch(str(manifest["routes_sha256"])):
        raise ApplyError("manifest route digest is invalid")
    applicator = manifest["applicator"]
    if not isinstance(applicator, dict) or set(applicator) != {
        "schema_version", "bundle_path", "source_sha256",
    } or applicator["schema_version"] != "2" or not _HEX.fullmatch(str(applicator["source_sha256"])):
        raise ApplyError("manifest applicator evidence is malformed")
    bundle = manifest["input_bundle"]
    if not isinstance(bundle, list) or not bundle or bundle != sorted(
        bundle, key=lambda item: (item.get("role", ""), item.get("path", ""))
    ):
        raise ApplyError("manifest input bundle is empty or noncanonical")
    for item in bundle:
        if not isinstance(item, dict) or set(item) != {"role", "path", "sha256"}:
            raise ApplyError("manifest input bundle entry is malformed")
        path = Path(str(item["path"]))
        if path.is_absolute() or ".." in path.parts or not _HEX.fullmatch(str(item["sha256"])):
            raise ApplyError("manifest input bundle path/digest is invalid")
    toolchain = manifest["toolchain"]
    if not isinstance(toolchain, dict) or set(toolchain) != {
        "backend", "freerouting_version", "freerouting_sha256", "java_version",
        "install_receipt_sha256", "compatibility_matrix_sha256", "compatibility_cell",
    } or toolchain.get("backend") != config["backend"]:
        raise ApplyError("manifest toolchain evidence is malformed")
    for key in ("freerouting_sha256", "install_receipt_sha256", "compatibility_matrix_sha256"):
        if not _HEX.fullmatch(str(toolchain.get(key))):
            raise ApplyError(f"manifest toolchain {key} is invalid")
    cell = toolchain.get("compatibility_cell")
    if not isinstance(cell, dict) or set(cell) != {
        "os", "arch", "kicad_cli", "pcbnew", "snapshot_schema",
    }:
        raise ApplyError("manifest compatibility cell is malformed")
    if cell["snapshot_schema"] != manifest["snapshot_schema"]:
        raise ApplyError("manifest compatibility cell snapshot schema differs")
    candidate = manifest["candidate"]
    if not isinstance(candidate, dict) or set(candidate) != {
        "raw_sha256", "review_sha256", "report_sha256",
    } or any(not _HEX.fullmatch(str(value)) for value in candidate.values()):
        raise ApplyError("manifest candidate evidence is malformed")
    attestation = manifest["seed_attestation"]
    if not isinstance(attestation, dict) or set(attestation) != {
        "schema", "semantic", "evidence", "sha256",
    } or attestation.get("schema") != SEED_ATTESTATION_SCHEMA:
        raise ApplyError("manifest seed attestation is malformed")
    unsigned = {
        key: attestation[key] for key in ("schema", "semantic", "evidence")
    }
    if not _HEX.fullmatch(str(attestation.get("sha256"))) or _json_sha(unsigned) != attestation["sha256"]:
        raise ApplyError("manifest seed attestation digest is invalid")
    evidence = attestation.get("evidence") or {}
    if (
        evidence.get("config_sha256") != next(
            (item["sha256"] for item in manifest["input_bundle"] if item["role"] == "autoroute-config"),
            None,
        )
        or evidence.get("input_bundle_sha256") != _json_sha(manifest["input_bundle"])
        or evidence.get("adapter") != config["tools"]["adapter"]
        or evidence.get("project_mode") != config["project"]["mode"]
        or evidence.get("reset") != config["reset"]
    ):
        raise ApplyError("manifest seed attestation evidence differs from config/bundle")


def _sexpr_tokens(text: str) -> list[str]:
    tokens, index = [], 0
    while index < len(text):
        char = text[index]
        if char.isspace():
            index += 1
        elif char in "()":
            tokens.append(char)
            index += 1
        elif char == '"':
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
                raise ApplyError("unterminated string in KiCad board")
            tokens.append(text[start:index])
        else:
            start = index
            while index < len(text) and not text[index].isspace() and text[index] not in "()":
                index += 1
            tokens.append(text[start:index])
    return tokens


def _parse_sexpr(text: str) -> list:
    root, stack = [], []
    current = root
    for token in _sexpr_tokens(text):
        if token == "(":
            child = []
            current.append(child)
            stack.append(current)
            current = child
        elif token == ")":
            if not stack:
                raise ApplyError("unbalanced KiCad board expression")
            current = stack.pop()
        else:
            current.append(token)
    if stack:
        raise ApplyError("unbalanced KiCad board expression")
    if len(root) != 1 or not isinstance(root[0], list):
        raise ApplyError("KiCad file does not contain one root expression")
    return root[0]


def _nonrouting_node(node):
    if not isinstance(node, list):
        return node
    head = node[0] if node and isinstance(node[0], str) else None
    if head in {"segment", "via", "arc", "filled_polygon", "fill_segments", "filled_segments"}:
        return None
    result = []
    for child in node:
        projected = _nonrouting_node(child)
        if projected is not None:
            result.append(projected)
    return result


def _nonrouting_sha(path: Path) -> str:
    return _json_sha(_nonrouting_node(_parse_sexpr(path.read_text(encoding="utf-8"))))


def _reset(board, config: dict, root: Path, pcbnew) -> dict:
    reset = config["reset"]
    selected = set(config["scope"]["net_to_class"])
    selected_items = [item for item in board.GetTracks() if str(item.GetNetname()) in selected]
    if reset["policy"] == "none":
        return {"removed": 0, "selected_routes_preserved": len(selected_items)}
    path = _relative(root, reset["manifest"], "reset.manifest")
    if not path.is_file() or _sha(path) != reset["manifest_sha256"]:
        raise ApplyError("route-reset manifest is missing or stale")
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("schema") != RESET_SCHEMA:
        raise ApplyError("unsupported route-reset manifest")
    if manifest.get("source_board_sha256") != _sha(
        _relative(root, config["project"]["source_board"], "project.source_board")
    ):
        raise ApplyError("route-reset source digest mismatch")
    if manifest.get("selected_nets") != sorted(selected):
        raise ApplyError("route-reset selected-net inventory mismatch")
    actual = sorted((_record(item, board, pcbnew) for item in selected_items), key=lambda x: x["uuid"])
    if actual != manifest.get("items"):
        raise ApplyError("live selected routing differs from the approved reset multiset")
    if _json_sha(actual) != manifest.get("aggregate_sha256"):
        raise ApplyError("route-reset aggregate digest mismatch")
    by_uuid = {_uuid(item): item for item in selected_items}
    if len(by_uuid) != len(selected_items):
        raise ApplyError("duplicate selected-route UUID")
    for record in actual:
        board.Remove(by_uuid[record["uuid"]])
    if any(str(item.GetNetname()) in selected for item in board.GetTracks()):
        raise ApplyError("selected routing remains after exact reset")
    return {"removed": len(actual), "selected_routes_preserved": 0}


def _copy_context(config: dict, root: Path, output_dir: Path) -> Path:
    project = config["project"]
    board_name = project["board_basename"]
    for key, suffix in (
        ("project_file", ".kicad_pro"),
        ("schematic_file", ".kicad_sch"),
    ):
        raw = project.get(key)
        if raw is None:
            continue
        source = _relative(root, raw, f"project.{key}")
        shutil.copy2(source, output_dir / Path(board_name).with_suffix(suffix).name)
    source_board = _relative(root, project["source_board"], "project.source_board")
    rules = source_board.with_suffix(".kicad_dru")
    if rules.is_file():
        shutil.copy2(rules, output_dir / Path(board_name).with_suffix(".kicad_dru").name)
    for table in ("fp-lib-table", "sym-lib-table"):
        source = root / table
        if source.is_file():
            shutil.copy2(source, output_dir / table)
            for raw_relative in re.findall(
                r'\(uri\s+"\$\{KIPRJMOD\}/([^"\r\n]+)"\)',
                source.read_text(encoding="utf-8"),
            ):
                relative = Path(raw_relative)
                if relative.is_absolute() or ".." in relative.parts:
                    raise ApplyError(f"project library path escapes project_root: {raw_relative}")
                library = (root / relative).resolve()
                try:
                    library.relative_to(root)
                except ValueError as exc:
                    raise ApplyError(f"project library resolves outside project_root: {library}") from exc
                destination = output_dir / relative
                if library.is_dir():
                    shutil.copytree(library, destination, dirs_exist_ok=True)
                elif library.is_file():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(library, destination)
                else:
                    raise ApplyError(f"project library is missing: {library}")
    # Explicit snapshot sources may include hierarchical sheets and other
    # vendored KiCad context.  Recreate their project-relative layout.
    context_suffixes = {".kicad_sch", ".kicad_dru", ".kicad_sym", ".kicad_mod"}
    primary_paths = {
        _relative(root, raw, f"project.{name}").resolve()
        for name, raw in (
            ("source_board", project.get("source_board")),
            ("project_file", project.get("project_file")),
            ("schematic_file", project.get("schematic_file")),
        )
        if raw is not None
    }
    if project.get("source_board") is not None:
        primary_paths.add(
            _relative(root, project["source_board"], "project.source_board")
            .with_suffix(".kicad_dru")
            .resolve()
        )
    for declaration in config["sources"]:
        source = _relative(root, declaration["path"], "sources[].path")
        members = [source] if source.is_file() else [
            path for path in source.rglob("*") if path.is_file()
        ]
        for member in members:
            if member.resolve() in primary_paths:
                continue
            if member.suffix not in context_suffixes and member.name not in {
                "fp-lib-table", "sym-lib-table",
            }:
                continue
            relative = member.relative_to(root)
            destination = output_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.resolve() == (output_dir / board_name).resolve():
                continue
            shutil.copy2(member, destination)
    return output_dir / board_name


def _copy_generated_context(seed: Path, output_dir: Path, config: dict) -> Path:
    destination = output_dir / config["project"]["board_basename"]
    shutil.copy2(seed, destination)
    for entry in _seed_context_bundle(seed):
        source = seed.parent / entry["path"]
        target = output_dir / entry["path"]
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    return destination


def _prepare_seed(config: dict, root: Path, output_dir: Path, pcbnew):
    if config["project"]["mode"] != "board-snapshot":
        raise ApplyError("built-in applicator supports board-snapshot mode only")
    destination = _copy_context(config, root, output_dir)
    source = _relative(root, config["project"]["source_board"], "project.source_board")
    before = _sha(source)
    board, manager, project = _load_board_with_project(source, pcbnew)
    if config["reset"]["policy"] == "none":
        shutil.copy2(source, destination)
        reset_result = {"removed": 0, "selected_routes_preserved": None}
    else:
        reset_result = _reset(board, config, root, pcbnew)
        if not pcbnew.SaveBoard(str(destination), board):
            raise ApplyError("KiCad failed to save the reset seed")
    if _sha(source) != before:
        raise ApplyError("source board changed while constructing the seed")
    return destination, {
        "source_board": str(source), "source_board_sha256": before,
        "seed_sha256": _sha(destination), "reset": reset_result,
    }, board, manager, project


def _net(board, name: str):
    net = board.FindNet(name)
    if net is None or not str(net.GetNetname()):
        raise ApplyError(f"manifest net is absent from board: {name!r}")
    return net


def _apply_routes(board, routes: list[dict], pcbnew) -> None:
    for route in routes:
        if route["kind"] == "segment":
            item = pcbnew.PCB_TRACK(board)
            item.SetNet(_net(board, route["net"]))
            item.SetLayer(board.GetLayerID(route["layer"]))
            item.SetWidth(int(route["width_nm"]))
            item.SetStart(pcbnew.VECTOR2I(*route["start_nm"]))
            item.SetEnd(pcbnew.VECTOR2I(*route["end_nm"]))
        else:
            item = pcbnew.PCB_VIA(board)
            item.SetNet(_net(board, route["net"]))
            item.SetPosition(pcbnew.VECTOR2I(*route["at_nm"]))
            item.SetWidth(int(route["diameter_nm"]))
            item.SetDrill(int(route["drill_nm"]))
            item.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            if hasattr(item, "SetViaType") and hasattr(pcbnew, "VIATYPE_THROUGH"):
                item.SetViaType(pcbnew.VIATYPE_THROUGH)
        item.SetLocked(False)
        board.Add(item)


def _final(
    config: dict,
    root: Path,
    config_path: Path,
    manifest_path: Path,
    output_dir: Path,
    work: Path,
    seed: Path,
    seed_report: dict,
    live_bundle: list[dict],
    pcbnew,
) -> dict:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_manifest = _relative(
        root, config["promotion"]["manifest"], "promotion.manifest"
    )
    if manifest_path != expected_manifest:
        raise ApplyError("manifest path differs from config promotion.manifest")
    _validate_manifest_envelope(config, manifest)
    routes = _validate_additions(manifest.get("routes"))
    if _json_sha(routes) != manifest.get("routes_sha256"):
        raise ApplyError("manifest route digest mismatch")
    if manifest.get("input_bundle") != _live_input_bundle(config, root, config_path):
        raise ApplyError("manifest input bundle differs from the live v2 source/tool bundle")
    _validate_scope(config, manifest, routes)
    applicator = manifest.get("applicator") or {}
    configured = config["tools"]["applicator"]
    if (
        applicator.get("bundle_path") != configured["path"]
        or applicator.get("source_sha256") != configured["sha256"]
    ):
        raise ApplyError("manifest applicator evidence differs from this scaffold")
    seed_board, seed_manager, seed_project = _load_board_with_project(seed, pcbnew)
    actual_attestation = _seed_attestation(
        seed_board, seed, config, config_path, live_bundle, pcbnew
    )
    if actual_attestation != manifest["seed_attestation"]:
        raise ApplyError(
            "regenerated seed differs from the reviewed adapter seed attestation"
        )
    before = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in _route_states(seed_board, pcbnew)
    )
    control = pcbnew.LoadBoard(str(seed))
    control.BuildConnectivity()
    if not pcbnew.ZONE_FILLER(control).Fill(control.Zones()):
        raise ApplyError("zone refill failed for empty-apply control")
    control_path = work / "control" / config["project"]["board_basename"]
    control_path.parent.mkdir(parents=True, exist_ok=False)
    if not pcbnew.SaveBoard(str(control_path), control):
        raise ApplyError("KiCad failed to save the empty-apply control")
    _apply_routes(seed_board, routes, pcbnew)
    seed_board.BuildConnectivity()
    if not pcbnew.ZONE_FILLER(seed_board).Fill(seed_board.Zones()):
        raise ApplyError("zone refill failed after manifest application")
    destination = (
        _copy_context(config, root, output_dir)
        if config["project"]["mode"] == "board-snapshot"
        else _copy_generated_context(seed, output_dir, config)
    )
    if not pcbnew.SaveBoard(str(destination), seed_board):
        raise ApplyError("KiCad failed to save the final routed board")
    check = pcbnew.LoadBoard(str(destination))
    _ = (seed_manager, seed_project)
    after = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in _route_states(check, pcbnew)
    )
    additions = after - before
    removals = before - after
    expected = collections.Counter(
        json.dumps({"route": route, "locked": False}, sort_keys=True, separators=(",", ":"))
        for route in routes
    )
    if removals or additions != expected:
        raise ApplyError("final route multiset is not exactly seed plus promoted additions")
    control_nonrouting = _nonrouting_sha(control_path)
    final_nonrouting = _nonrouting_sha(destination)
    if control_nonrouting != final_nonrouting:
        raise ApplyError("manifest application changed non-routing board semantics")
    if config["project"]["mode"] == "board-snapshot":
        source = _relative(root, config["project"]["source_board"], "project.source_board")
        if _sha(source) != seed_report["source_board_sha256"]:
            raise ApplyError("source board changed during final application")
    return {
        **seed_report,
        "manifest": str(manifest_path),
        "manifest_sha256": _sha(manifest_path),
        "manifest_seed_sha256": manifest.get("seed_sha256"),
        "byte_seed_match": manifest.get("seed_sha256") == seed_report["seed_sha256"],
        "seed_attestation": actual_attestation,
        "semantic_seed_authority": "reviewed adapter seed attestation",
        "protected_routes_unchanged": not removals,
        "route_additions_sha256": _json_sha(routes),
        "route_additions_count": len(routes),
        "control_nonrouting_projection_sha256": control_nonrouting,
        "final_nonrouting_projection_sha256": final_nonrouting,
        "nonrouting_unchanged": True,
        "final_board": str(destination),
        "final_board_sha256": _sha(destination),
        "semantic_reproducibility_passed": True,
        "byte_reproducibility_claimed": False,
    }


def main(argv=None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    if raw_argv[:1] == ["--_attest-seed"]:
        if len(raw_argv) != 4:
            raise ApplyError("internal seed attestation requires CONFIG BOARD REPORT")
        config_path = Path(raw_argv[1]).expanduser().resolve()
        board_path = Path(raw_argv[2]).expanduser().resolve()
        report_path = Path(raw_argv[3]).expanduser().resolve()
        scratch_raw = os.environ.get("KICAD_AUTOROUTE_ATTEST_ROOT")
        if not scratch_raw:
            raise ApplyError("internal seed attestation requires an orchestrator scratch root")
        scratch = Path(scratch_raw).resolve()
        for name, path in (("board", board_path), ("report", report_path)):
            try:
                path.relative_to(scratch)
            except ValueError as exc:
                raise ApplyError(f"internal attestation {name} escapes scratch root") from exc
        config, root = _load_config(config_path)
        _verify_sources(config, root)
        bundle = _live_input_bundle(config, root, config_path)
        pcbnew, wx_app = _init_pcbnew()
        board, manager, project = _load_board_with_project(board_path, pcbnew)
        _ = (wx_app, manager, project)
        _write_json(
            report_path,
            _seed_attestation(board, board_path, config, config_path, bundle, pcbnew),
        )
        return 0
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    for name in ("seed", "final"):
        command = sub.add_parser(name)
        command.add_argument("--config", required=True)
        command.add_argument("--output-dir", required=True)
        command.add_argument("--report", required=True)
        if name == "final":
            command.add_argument("--manifest", required=True)
    args = parser.parse_args(raw_argv)
    safe_failure_report = None
    try:
        config_path = Path(args.config).expanduser().resolve()
        config, root = _load_config(config_path)
        sources_before = _verify_sources(config, root)
        output_dir = Path(args.output_dir).expanduser().resolve()
        for source in config["sources"]:
            protected = _relative(root, source["path"], "sources[].path")
            if (
                output_dir == protected
                or output_dir.is_relative_to(protected)
                or protected.is_relative_to(output_dir)
            ):
                raise ApplyError("output-dir overlaps an immutable source declaration")
        for protected in (
            config_path,
            *(
                _relative(root, config["tools"][name]["path"], f"tools.{name}.path")
                for name in ("adapter", "applicator", "audit")
            ),
        ):
            if output_dir == protected or protected.is_relative_to(output_dir):
                raise ApplyError("output-dir encloses config/tool inputs")
        if output_dir.exists() and any(output_dir.iterdir()):
            raise ApplyError("output-dir must be fresh and empty")
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = _inside(Path(args.report), output_dir, "report")
        if report_path.suffix in {
            ".kicad_pcb", ".kicad_pro", ".kicad_sch", ".kicad_dru",
            ".kicad_sym", ".kicad_mod",
        } or report_path.name in {"fp-lib-table", "sym-lib-table"}:
            raise ApplyError("report path collides with a possible KiCad context artifact")
        reserved = {
            output_dir / config["project"]["board_basename"],
            output_dir / Path(config["project"]["board_basename"]).with_suffix(".kicad_pro").name,
            output_dir / Path(config["project"]["board_basename"]).with_suffix(".kicad_sch").name,
            output_dir / Path(config["project"]["board_basename"]).with_suffix(".kicad_dru").name,
        }
        if report_path in {path.resolve() for path in reserved}:
            raise ApplyError("report path collides with a generated KiCad artifact")
        safe_failure_report = report_path
        live_bundle = _live_input_bundle(config, root, config_path)
        pcbnew, wx_app = _init_pcbnew()
        _ = wx_app
        if args.command == "seed":
            if config["project"]["mode"] != "board-snapshot":
                raise ApplyError("generator-adapter mode owns seed generation")
            board, details, prepared, prepared_manager, prepared_project = _prepare_seed(
                config, root, output_dir, pcbnew
            )
            details["board"] = str(board)
            _ = (prepared, prepared_manager, prepared_project)
            attestation_path = output_dir / ".seed-attestation.json"
            completed = _run_external(
                [
                    sys.executable, str(Path(__file__).resolve()), "--_attest-seed",
                    str(config_path), str(board), str(attestation_path),
                ],
                timeout_seconds=config["limits"]["timeout_seconds"],
                env={**os.environ, "KICAD_AUTOROUTE_ATTEST_ROOT": str(output_dir)},
            )
            if completed.returncode != 0 or not attestation_path.is_file():
                raise ApplyError(
                    "isolated seed attestation failed: "
                    + (completed.stderr.strip() or "no report")
                )
            details["seed_attestation"] = json.loads(
                attestation_path.read_text(encoding="utf-8")
            )
            attestation_path.unlink()
        else:
            work = output_dir / ".applicator-work"
            work.mkdir(parents=True, exist_ok=False)
            if config["project"]["mode"] == "board-snapshot":
                seed_dir = work / "seed"
                seed_report_path = seed_dir / "report.json"
                completed = _run_external(
                    [
                        sys.executable, str(Path(__file__).resolve()), "seed",
                        "--config", str(config_path), "--output-dir", str(seed_dir),
                        "--report", str(seed_report_path),
                    ],
                    timeout_seconds=config["limits"]["timeout_seconds"],
                )
                if completed.returncode != 0 or not seed_report_path.is_file():
                    raise ApplyError(
                        "isolated seed reconstruction failed: "
                        + (completed.stderr.strip() or "no report")
                    )
                seed_result = json.loads(seed_report_path.read_text(encoding="utf-8"))
                if seed_result.get("status") != "PASS":
                    raise ApplyError("isolated seed reconstruction did not pass")
                seed_details = seed_result["details"]
                seed = Path(seed_details["board"])
            else:
                seed_dir = work / "seed"
                seed_report_path = seed_dir / "report.json"
                adapter = _relative(
                    root, config["tools"]["adapter"]["path"], "tools.adapter.path"
                )
                completed = _run_external(
                    [
                        sys.executable, str(adapter), "seed",
                        "--output-dir", str(seed_dir), "--report", str(seed_report_path),
                    ],
                    cwd=root,
                    timeout_seconds=config["limits"]["timeout_seconds"],
                )
                if completed.returncode != 0 or not seed_report_path.is_file():
                    raise ApplyError(
                        "configured generator adapter seed failed: "
                        + (completed.stderr.strip() or "no report")
                    )
                seed_result = json.loads(seed_report_path.read_text(encoding="utf-8"))
                if seed_result.get("status") != "PASS":
                    raise ApplyError("configured generator adapter seed did not pass")
                seed = seed_dir / config["project"]["board_basename"]
                required = [seed, seed.with_suffix(".kicad_pro")]
                schematic = seed.with_suffix(".kicad_sch")
                if config["project"]["schematic_authority"] == "parity":
                    required.append(schematic)
                elif schematic.exists():
                    raise ApplyError("board-only adapter seed must not contain a schematic")
                missing = [str(path) for path in required if not path.is_file()]
                if missing:
                    raise ApplyError("generator seed bundle is incomplete: " + ", ".join(missing))
                seed_details = dict(seed_result.get("details") or {})
                seed_details.update({
                    "board": str(seed), "seed_sha256": _sha(seed),
                    "adapter_generated": True,
                })
            details = _final(
                config, root, config_path,
                Path(args.manifest).expanduser().resolve(), output_dir,
                work, seed, seed_details, live_bundle, pcbnew,
            )
        if _verify_sources(config, root) != sources_before:
            raise ApplyError("immutable source bundle changed during applicator run")
        report = {
            "schema": REPORT_SCHEMA, "command": args.command,
            "status": "PASS", "schematic_authority": config["project"]["schematic_authority"],
            "output_authority": "derived-non-editable",
            "sources": sources_before, "details": details,
        }
        if config["project"]["schematic_authority"] == "board-only":
            report["permanent_waiver"] = "schematic parity and ERC unavailable; PCB is authoritative"
        _write_json(report_path, report)
        return 0
    except (ApplyError, OSError, ValueError, KeyError, json.JSONDecodeError) as exc:
        try:
            if safe_failure_report is not None:
                _write_json(safe_failure_report, {
                    "schema": REPORT_SCHEMA, "command": args.command,
                    "status": "ERROR", "error": str(exc),
                })
        except Exception:
            pass
        print(f"autoroute_apply: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    # Some KiCad 10/macOS pcbnew builds crash while SWIG destroys removed
    # PCB_TRACK objects after a successful save.  All outputs above are closed
    # and atomically replaced; skip only the faulty extension teardown.
    return_code = main()
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(return_code)
