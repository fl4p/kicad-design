#!/usr/bin/env python3
"""Apply or promote deterministic KiCad route manifests.

``apply`` is the small project-generator API and must run under KiCad's bundled
Python.  ``promote`` is the separate explicit-approval boundary; it consumes a
completed candidate report and never edits the source board.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path
import sys

from kicad_autoroute import (
    AutorouteError,
    CONFIG_SCHEMA_V2,
    MANIFEST_SCHEMA,
    MANIFEST_SCHEMA_V2,
    canonical_json_sha256,
    canonical_routes,
    config_path,
    load_config,
    sha256_path,
    validate_promotion_report,
    validate_manifest,
    verify_input_bundle,
    write_json_atomic,
)


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
    raise AutorouteError(f"cannot read UUID for {type(item).__name__}")


def _point(value) -> list[int]:
    return [int(value.x), int(value.y)]


def _drawing_identity(drawing) -> dict:
    values = {"kind": type(drawing).__name__, "layer": int(drawing.GetLayer())}
    for name in ("GetStart", "GetEnd", "GetPosition"):
        if hasattr(drawing, name):
            try:
                values[name] = _point(getattr(drawing, name)())
            except Exception:
                pass
    if hasattr(drawing, "GetText"):
        try:
            values["text"] = str(drawing.GetText())
        except Exception:
            pass
    for name, key, converter in (
        ("GetShape", "shape", int),
        ("GetWidth", "width_nm", int),
        ("IsLocked", "locked", bool),
    ):
        if hasattr(drawing, name):
            try:
                values[key] = converter(getattr(drawing, name)())
            except Exception:
                pass
    return values


def _board_route(item, board, pcbnew) -> dict:
    if isinstance(item, pcbnew.PCB_VIA):
        return {
            "kind": "via",
            "net": str(item.GetNetname()),
            "at_nm": _point(item.GetPosition()),
            "diameter_nm": int(item.GetFrontWidth()),
            "drill_nm": int(item.GetDrillValue()),
            "layers": [
                str(board.GetLayerName(int(item.TopLayer()))),
                str(board.GetLayerName(int(item.BottomLayer()))),
            ],
        }
    if isinstance(item, pcbnew.PCB_ARC):
        raise AutorouteError("promotion v1 cannot extract route arcs")
    if isinstance(item, pcbnew.PCB_TRACK):
        return {
            "kind": "segment",
            "net": str(item.GetNetname()),
            "layer": str(item.GetLayerName()),
            "width_nm": int(item.GetWidth()),
            "start_nm": _point(item.GetStart()),
            "end_nm": _point(item.GetEnd()),
        }
    raise AutorouteError(f"unsupported board route object {type(item).__name__}")


def extract_routes(board, pcbnew, *, allowed_nets: set[str] | None = None) -> list[dict]:
    routes = []
    for item in board.GetTracks():
        if allowed_nets is not None and str(item.GetNetname()) not in allowed_nets:
            continue
        routes.append(_board_route(item, board, pcbnew))
    return canonical_routes(routes)


def _net(board, name: str):
    net = board.FindNet(name)
    if net is None or not str(net.GetNetname()):
        raise AutorouteError(f"manifest net {name!r} is absent from the board")
    return net


def apply_routes(board, routes: list[dict], pcbnew) -> dict:
    routes = canonical_routes(routes)
    target_nets = {route["net"] for route in routes}
    before = extract_routes(board, pcbnew, allowed_nets=target_nets)
    before_counts = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":")) for item in before
    )
    segments = vias = 0
    for route in routes:
        if route["kind"] == "segment":
            item = pcbnew.PCB_TRACK(board)
            item.SetNet(_net(board, route["net"]))
            item.SetLayer(board.GetLayerID(route["layer"]))
            item.SetWidth(route["width_nm"])
            item.SetStart(pcbnew.VECTOR2I(*route["start_nm"]))
            item.SetEnd(pcbnew.VECTOR2I(*route["end_nm"]))
            item.SetLocked(False)
            board.Add(item)
            segments += 1
        else:
            item = pcbnew.PCB_VIA(board)
            item.SetNet(_net(board, route["net"]))
            item.SetPosition(pcbnew.VECTOR2I(*route["at_nm"]))
            item.SetWidth(route["diameter_nm"])
            item.SetDrill(route["drill_nm"])
            item.SetLayerPair(pcbnew.F_Cu, pcbnew.B_Cu)
            if hasattr(item, "SetViaType") and hasattr(pcbnew, "VIATYPE_THROUGH"):
                item.SetViaType(pcbnew.VIATYPE_THROUGH)
            item.SetLocked(False)
            board.Add(item)
            vias += 1
    after = extract_routes(board, pcbnew, allowed_nets=target_nets)
    added = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":")) for item in after
    ) - before_counts
    actual = canonical_routes(
        [json.loads(raw) for raw, count in added.items() for _ in range(count)]
    )
    if actual != routes:
        raise AutorouteError("apply->extract route equality failed")
    return {
        "segments": segments,
        "vias": vias,
        "routes_sha256": canonical_json_sha256(actual),
    }


def apply_manifest(
    board,
    manifest_path: Path | str,
    *,
    seed_path: Path | str,
    input_bundle_root: Path | str,
    expected_input_bundle: list[dict],
    pcbnew=None,
) -> dict:
    if pcbnew is None:
        import pcbnew as pcbnew_module

        pcbnew = pcbnew_module
    manifest_path = Path(manifest_path)
    manifest = validate_manifest(json.loads(manifest_path.read_text(encoding="utf-8")))
    seed_path = Path(seed_path).resolve()
    if sha256_path(seed_path) != manifest["seed_sha256"]:
        raise AutorouteError("generated seed differs from the reviewed manifest seed")
    verify_input_bundle(input_bundle_root, expected_input_bundle)
    if manifest["input_bundle"] != expected_input_bundle:
        raise AutorouteError("manifest input bundle does not match the generator's expected bundle")
    return apply_routes(board, manifest["routes"], pcbnew)


def identity_map(board, pcbnew) -> dict[str, str]:
    """Map KiCad DRC UUIDs to stable board-semantic identities."""
    out = {}
    for fp in board.GetFootprints():
        ref = str(fp.GetReference())
        out[_uuid(fp)] = f"footprint:{ref}"
        for pad in fp.Pads():
            pos = _point(pad.GetPosition())
            identity = (
                f"pad:{ref}:{pad.GetNumber()}:{pad.GetNetname()}:"
                f"{pos[0]}:{pos[1]}"
            )
            out[_uuid(pad)] = identity
        for graphic in fp.GraphicalItems():
            values = _drawing_identity(graphic)
            values.update({
                "parent_reference": ref,
                "parent_uuid": _uuid(fp),
                "parent_attributes": int(fp.GetAttributes()),
            })
            out[_uuid(graphic)] = "footprint-graphic:" + json.dumps(
                values, sort_keys=True, separators=(",", ":")
            )
    for item in board.GetTracks():
        route = _board_route(item, board, pcbnew)
        out[_uuid(item)] = "route:" + json.dumps(
            route, sort_keys=True, separators=(",", ":")
        )
    for zone in board.Zones():
        corners = sorted(
            _point(zone.GetCornerPosition(index))
            for index in range(zone.GetNumCorners())
        )
        identity = {
            "kind": "zone",
            "name": str(zone.GetZoneName()),
            "net": str(zone.GetNetname()),
            "layers": sorted(int(x) for x in zone.GetLayerSet().Seq()),
            "priority": int(zone.GetAssignedPriority()),
            "corners_nm": corners,
        }
        out[_uuid(zone)] = "zone:" + json.dumps(
            identity, sort_keys=True, separators=(",", ":")
        )
    for drawing in board.GetDrawings():
        values = _drawing_identity(drawing)
        out[_uuid(drawing)] = "drawing:" + json.dumps(
            values, sort_keys=True, separators=(",", ":")
        )
    return out


def _init_pcbnew():
    try:
        import wx

        wx.Log.SetLogLevel(wx.LOG_Error)
        app = wx.App(False)
    except Exception:
        app = None
    import pcbnew

    return pcbnew, app


def _worker_apply(args) -> int:
    pcbnew, app = _init_pcbnew()
    board_path = Path(args.board).resolve()
    output = Path(args.output).resolve()
    routes = json.loads(Path(args.routes).read_text(encoding="utf-8"))
    board = pcbnew.LoadBoard(str(board_path))
    result = apply_routes(board, routes, pcbnew)
    if args.refill_zones:
        board.BuildConnectivity()
        if not pcbnew.ZONE_FILLER(board).Fill(board.Zones()):
            raise AutorouteError("zone refill failed after manifest application")
    output.parent.mkdir(parents=True, exist_ok=True)
    if not pcbnew.SaveBoard(str(output), board):
        raise AutorouteError(f"KiCad could not save {output}")
    check = pcbnew.LoadBoard(str(output))
    allowed_nets = {route["net"] for route in routes}
    result["applied_routes_after_reload_sha256"] = canonical_json_sha256(
        extract_routes(check, pcbnew, allowed_nets=allowed_nets)
    )
    result["board_sha256"] = sha256_path(output)
    if args.identity_map:
        write_json_atomic(args.identity_map, identity_map(check, pcbnew))
    write_json_atomic(args.summary, result)
    _ = app
    return 0


def _worker_identity(args) -> int:
    pcbnew, app = _init_pcbnew()
    board = pcbnew.LoadBoard(str(Path(args.board).resolve()))
    write_json_atomic(args.output, identity_map(board, pcbnew))
    _ = app
    return 0


def _promote(args) -> int:
    config = load_config(args.config)
    report_path = Path(args.report).resolve()
    report_sha = sha256_path(report_path)
    if report_sha != args.approve_report_sha256:
        raise AutorouteError(
            f"report approval digest mismatch: approved {args.approve_report_sha256}, actual {report_sha}"
        )
    report = validate_promotion_report(
        json.loads(report_path.read_text(encoding="utf-8"))
    )
    promotion = report["promotion"]
    if promotion.get("review_candidate_sha256") != args.approve_candidate_sha256:
        raise AutorouteError("candidate approval digest does not match the report")
    candidate = Path(args.candidate_board).resolve()
    actual_candidate_sha = sha256_path(candidate)
    if actual_candidate_sha != args.approve_candidate_sha256:
        raise AutorouteError(
            "approved candidate digest differs from the actual candidate board"
        )
    seed = Path(args.seed).resolve()
    if sha256_path(seed) != promotion.get("seed_sha256"):
        raise AutorouteError("promotion seed digest differs from the reviewed seed")
    if config["config_sha256"] != promotion.get("config_sha256"):
        raise AutorouteError("promotion config differs from the reviewed config")
    root = Path(args.project_root).resolve()
    expected_config_root = Path(config.get("project_root", config["config_dir"])).resolve()
    if root != expected_config_root:
        raise AutorouteError("promotion project root must match the configured project root")
    bundle = promotion.get("input_bundle")
    verify_input_bundle(root, bundle)
    # Reconstruct the complete live bundle instead of trusting the report to
    # enumerate the files whose omission it is supposed to detect.
    from kicad_route_candidate import _configured_input_bundle, _related_sources

    expected_root, expected_bundle = _configured_input_bundle(
        argparse.Namespace(_autoroute_config=config),
        _related_sources(
            seed,
            no_parity=(
                config.get("schema") == "kicad-autoroute-config-v2"
                and config["project"]["schematic_authority"] == "board-only"
            ),
        ),
    )
    if expected_root != root or expected_bundle != bundle:
        raise AutorouteError("candidate report input bundle is incomplete or stale")
    routes = canonical_routes(promotion.get("routes") or [])
    if not routes:
        raise AutorouteError("candidate report contains no promotable routes")
    if canonical_json_sha256(routes) != promotion.get("routes_sha256"):
        raise AutorouteError("candidate report route digest is inconsistent")
    pcbnew, app = _init_pcbnew()
    seed_board = pcbnew.LoadBoard(str(seed))
    candidate_board = pcbnew.LoadBoard(str(candidate))
    allowed_nets = set(promotion["scope"]["resolved_nets"])
    before = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in extract_routes(seed_board, pcbnew, allowed_nets=allowed_nets)
    )
    after = collections.Counter(
        json.dumps(item, sort_keys=True, separators=(",", ":"))
        for item in extract_routes(candidate_board, pcbnew, allowed_nets=allowed_nets)
    )
    removed = before - after
    if removed:
        raise AutorouteError("actual candidate removes scoped seed routing")
    actual_routes = canonical_routes(
        [json.loads(raw) for raw, count in (after - before).items() for _ in range(count)]
    )
    if actual_routes != routes:
        raise AutorouteError("actual candidate scoped route delta differs from the report")
    _ = app
    applicator = promotion.get("applicator")
    if not isinstance(applicator, dict):
        raise AutorouteError("candidate report has no project applicator evidence")
    source = root / applicator.get("bundle_path", "")
    if not source.is_file() or sha256_path(source) != applicator.get("source_sha256"):
        raise AutorouteError("project manifest applicator differs from the reviewed bundle")
    matrix_path = Path(__file__).with_name("kicad-autoroute-compatibility.json")
    if sha256_path(matrix_path) != promotion["toolchain"]["compatibility_matrix_sha256"]:
        raise AutorouteError("live compatibility matrix differs from the reviewed matrix")
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    if matrix.get("schema") != "kicad-autoroute-compatibility-v1":
        raise AutorouteError("live compatibility matrix schema is unsupported")
    wanted_cell = promotion["toolchain"]["compatibility_cell"]
    matches = [
        cell for cell in matrix.get("cells", [])
        if all(cell.get(key) == value for key, value in wanted_cell.items())
    ]
    if len(matches) != 1 or matches[0].get("promotion_enabled") is not True:
        raise AutorouteError("reviewed compatibility cell is no longer promotion-enabled")
    from kicad_autoroute_tools import default_cache, status as tool_status

    installed = tool_status(default_cache(), require_valid=True)
    if installed.get("promotion_integrity_pinned") is not True:
        raise AutorouteError("installed JRE is not bound to tracked integrity pins")
    if installed.get("receipt_sha256") != promotion["toolchain"]["install_receipt_sha256"]:
        raise AutorouteError("live tool installation receipt differs from the reviewed run")
    is_v2 = config.get("schema") == CONFIG_SCHEMA_V2
    manifest = {
        "schema": MANIFEST_SCHEMA_V2 if is_v2 else MANIFEST_SCHEMA,
        "seed_sha256": promotion["seed_sha256"],
        "applicator": applicator,
        "input_bundle": bundle,
        "toolchain": promotion.get("toolchain") or {},
        "scope": promotion.get("scope") or {},
        "candidate": {
            "raw_sha256": promotion.get("raw_candidate_sha256"),
            "review_sha256": promotion.get("review_candidate_sha256"),
            "report_sha256": report_sha,
        },
        "routes": routes,
        "routes_sha256": canonical_json_sha256(routes),
    }
    if is_v2:
        manifest["seed_attestation"] = promotion["seed_attestation"]
    validate_manifest(manifest)
    output = Path(args.output_manifest).resolve()
    expected_output = config_path(config, config["promotion"]["manifest"])
    if output != expected_output:
        raise AutorouteError(
            f"output manifest must match config promotion.manifest: {expected_output}"
        )
    if output.exists() and not args.replace:
        raise AutorouteError(f"manifest already exists; pass --replace to update it: {output}")
    write_json_atomic(output, manifest)
    print(f"MANIFEST_EMITTED: {output}")
    return 0


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    apply = sub.add_parser("apply")
    apply.add_argument("--board", required=True)
    apply.add_argument("--routes", required=True)
    apply.add_argument("--output", required=True)
    apply.add_argument("--summary", required=True)
    apply.add_argument("--identity-map")
    apply.add_argument("--refill-zones", action="store_true")
    identity = sub.add_parser("identity")
    identity.add_argument("--board", required=True)
    identity.add_argument("--output", required=True)
    promote = sub.add_parser("promote")
    promote.add_argument("--seed", required=True)
    promote.add_argument("--candidate-board", required=True)
    promote.add_argument("--config", required=True)
    promote.add_argument("--report", required=True)
    promote.add_argument("--project-root", required=True)
    promote.add_argument("--approve-candidate-sha256", required=True)
    promote.add_argument("--approve-report-sha256", required=True)
    promote.add_argument("--output-manifest", required=True)
    promote.add_argument("--replace", action="store_true")
    return parser


def main(argv=None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "apply":
            return _worker_apply(args)
        if args.command == "identity":
            return _worker_identity(args)
        return _promote(args)
    except (AutorouteError, OSError, json.JSONDecodeError) as exc:
        print(f"kicad_route_manifest: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
