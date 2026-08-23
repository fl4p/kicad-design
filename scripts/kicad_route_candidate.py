#!/usr/bin/env python3
"""Create a scratch-only Freerouting candidate and report what changed.

This is deliberately NOT a route-promotion tool.  It copies a KiCad board and
its same-stem project sidecars to an isolated workspace, exports Specctra DSN
through KiCad's bundled ``pcbnew`` Python, optionally runs a pinned local
Freerouting JAR, imports the resulting SES into another scratch board, and
writes a JSON report.  The source board is hashed before and after the run and
is never passed to a writing API.

The report can reject a candidate for generic mechanical reasons (scope drift,
locked-route drift, non-routing semantic drift, DRC, or incomplete routing),
but it can never accept a board for fabrication.  Project-specific electrical
and geometric guards remain mandatory.
"""

from __future__ import annotations

import argparse
import collections
from decimal import Decimal, InvalidOperation
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

try:
    from kicad_autoroute import (
        AutorouteError,
        CONFIG_SCHEMA_V2,
        REPORT_SCHEMA,
        build_input_bundle,
        build_v2_input_bundle,
        canonical_json_sha256,
        compare_drc,
        config_path,
        filter_candidate_routes,
        load_config,
        make_seed_attestation,
        make_drc_baseline,
        nonrouting_projection_sha256,
        normalize_drc_report,
        resolve_project_netclasses,
        seed_context_bundle,
        verify_input_bundle,
        verify_project_styles,
    )
    from kicad_repro import digest
    from kicad_verify import (
        VerifyError,
        find_kicad_cli,
        ignored_checks_from_report,
        run_drc,
        severity_report,
    )
except ImportError:  # imported as scripts.kicad_route_candidate
    from .kicad_autoroute import (
        AutorouteError,
        CONFIG_SCHEMA_V2,
        REPORT_SCHEMA,
        build_input_bundle,
        build_v2_input_bundle,
        canonical_json_sha256,
        compare_drc,
        config_path,
        filter_candidate_routes,
        load_config,
        make_seed_attestation,
        make_drc_baseline,
        nonrouting_projection_sha256,
        normalize_drc_report,
        resolve_project_netclasses,
        seed_context_bundle,
        verify_input_bundle,
        verify_project_styles,
    )
    from .kicad_repro import digest
    from .kicad_verify import (
        VerifyError,
        find_kicad_cli,
        ignored_checks_from_report,
        run_drc,
        severity_report,
    )


SNAPSHOT_SCHEMA = "kicad-route-semantic-snapshot-v2"
LOG_TAIL_CHARS = 12000
# KiCad's DSN/SES import can canonicalize a decimal coordinate by one internal
# nanometre (for example 16.774999 mm to 16.775000 mm) without moving an item
# on any meaningful ECAD or manufacturing grid.  Routing geometry remains
# exact; only non-routing snapshot points use this explicitly recorded quantum.
NONROUTING_POINT_QUANTUM_NM = 10
# KiCad 10.0.5 writes routing coordinates in this DSN at integer micrometres
# even though other DSN geometry may carry decimal micrometres.  This quantum
# applies only to the pre-router fixed-copper geometry comparison; post-import
# locked routing remains exact at KiCad's native nanometre values.
DSN_LOCKED_POINT_QUANTUM_NM = 1000


class RouteReportError(RuntimeError):
    """The candidate workflow could not produce a trustworthy report."""


def _read_utf8(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise RouteReportError(
            "%s is not valid UTF-8 at byte %d" % (path, exc.start)
        ) from exc
    except OSError as exc:
        raise RouteReportError("cannot read %s: %s" % (path, exc)) from exc


def _write_json_atomic(path: Path, data: dict) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp = Path(handle.name)
        json.dump(data, handle, indent=2, sort_keys=True)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _write_text_atomic(path: Path, data: str) -> None:
    path = path.resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        dir=str(path.parent),
        prefix=path.name + ".",
        suffix=".tmp",
        delete=False,
    ) as handle:
        tmp = Path(handle.name)
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _json_digest(data) -> str:
    raw = json.dumps(
        data, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")
    return _sha256_bytes(raw)


def _run(
    cmd: list[str],
    *,
    cwd: Path | None = None,
    timeout: int = 30,
    env: dict[str, str] | None = None,
) -> dict:
    started = time.monotonic()
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd) if cwd else None,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        stderr = exc.stderr or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", "replace")
        raise RouteReportError(
            "command timed out after %ds: %s\nstdout tail:\n%s\nstderr tail:\n%s"
            % (timeout, cmd[0], stdout[-2000:], stderr[-2000:])
        ) from exc
    except OSError as exc:
        raise RouteReportError("cannot run %s: %s" % (cmd[0], exc)) from exc
    return {
        "command": [str(x) for x in cmd],
        "returncode": proc.returncode,
        "runtime_seconds": round(time.monotonic() - started, 3),
        "stdout": proc.stdout or "",
        "stderr": proc.stderr or "",
    }


def _log_record(run: dict) -> dict:
    stdout = run.get("stdout", "")
    stderr = run.get("stderr", "")
    return {
        "command": run.get("command", []),
        "returncode": run.get("returncode"),
        "runtime_seconds": run.get("runtime_seconds"),
        "stdout_bytes": len(stdout.encode("utf-8")),
        "stderr_bytes": len(stderr.encode("utf-8")),
        "stdout_sha256": _sha256_bytes(stdout.encode("utf-8")),
        "stderr_sha256": _sha256_bytes(stderr.encode("utf-8")),
        "stdout_tail": stdout[-LOG_TAIL_CHARS:],
        "stderr_tail": stderr[-LOG_TAIL_CHARS:],
    }


def _candidate_kicad_pythons() -> list[Path]:
    candidates: list[Path] = []
    if sys.platform == "darwin":
        candidates.extend(
            Path("/Applications/KiCad").glob(
                "KiCad*.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
            )
        )
        candidates.append(
            Path(
                "/Applications/KiCad/KiCad.app/Contents/Frameworks/"
                "Python.framework/Versions/Current/bin/python3"
            )
        )
    elif os.name == "nt":
        for base in (
            os.environ.get("ProgramFiles", r"C:\Program Files"),
            os.environ.get("ProgramFiles(x86)", ""),
        ):
            if base:
                candidates.extend(
                    sorted(
                        Path(base).glob("KiCad/*/bin/python.exe"),
                        reverse=True,
                    )
                )
    else:
        candidates.extend((Path("/usr/bin/python3"), Path("/usr/local/bin/python3")))
    return candidates


def _probe_kicad_python(path: Path) -> tuple[bool, str]:
    if not path.is_file():
        return False, ""
    probe = _run(
        [
            str(path),
            "-c",
            "import pcbnew; print(pcbnew.GetBuildVersion())",
        ],
        timeout=25,
    )
    output = (probe["stdout"] + "\n" + probe["stderr"]).strip()
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", output)
    return probe["returncode"] == 0 and bool(match), match.group(0) if match else ""


def find_kicad_python(explicit: str | None = None) -> tuple[Path, str]:
    configured = explicit or os.environ.get("KICAD_PYTHON")
    if configured:
        path = Path(configured).expanduser().resolve()
        ok, version = _probe_kicad_python(path)
        if not ok:
            raise RouteReportError(
                "%s is not a runnable Python with pcbnew" % path
            )
        return path, version

    seen: set[Path] = set()
    on_path = shutil.which("python3")
    candidates = ([Path(on_path)] if on_path else []) + _candidate_kicad_pythons()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        try:
            ok, version = _probe_kicad_python(resolved)
        except RouteReportError:
            continue
        if ok:
            return resolved, version
    raise RouteReportError(
        "KiCad Python with pcbnew was not found. Pass --kicad-python or set "
        "KICAD_PYTHON to KiCad's bundled interpreter."
    )


def _compatibility_cell(pcbnew_version: str, kicad_cli: Path) -> dict:
    """Resolve this host against the tracked, exact promotion matrix."""
    probe = _run([str(kicad_cli), "--version"], timeout=30)
    if probe["returncode"] != 0:
        raise RouteReportError("cannot determine kicad-cli version")
    match = re.search(r"\b\d+\.\d+(?:\.\d+)?\b", probe["stdout"] + probe["stderr"])
    if not match:
        raise RouteReportError("kicad-cli --version returned no parseable version")
    cli_version = match.group(0)
    matrix_path = Path(__file__).with_name("kicad-autoroute-compatibility.json")
    try:
        matrix = json.loads(_read_utf8(matrix_path))
    except json.JSONDecodeError as exc:
        raise RouteReportError("autoroute compatibility matrix is invalid JSON") from exc
    if matrix.get("schema") != "kicad-autoroute-compatibility-v1":
        raise RouteReportError("unsupported autoroute compatibility matrix schema")
    wanted = {
        "os": platform.system().lower(),
        "arch": platform.machine().lower(),
        "kicad_cli": cli_version,
        "pcbnew": pcbnew_version,
    }
    matches = [
        cell
        for cell in matrix.get("cells", [])
        if all(cell.get(key) == value for key, value in wanted.items())
    ]
    if len(matches) > 1:
        raise RouteReportError("autoroute compatibility matrix has duplicate host cells")
    if matches and matches[0].get("promotion_enabled") is True:
        evidence_sha = matches[0].get("evidence_sha256")
        if (
            not isinstance(matches[0].get("qualified_utc"), str)
            or not isinstance(evidence_sha, list)
            or not evidence_sha
            or not all(re.fullmatch(r"[0-9a-f]{64}", value or "") for value in evidence_sha)
        ):
            raise RouteReportError(
                "promotion-enabled compatibility cell lacks digest-bound qualification evidence"
            )
    return {
        **wanted,
        "matrix_path": str(matrix_path),
        "matrix_sha256": digest(matrix_path),
        "matched": len(matches) == 1,
        "promotion_enabled": bool(matches and matches[0].get("promotion_enabled") is True),
        "evidence": matches[0].get("evidence") if matches else "no exact compatibility cell",
        "version_probe": _log_record(probe),
    }


def find_java(explicit: str | None = None) -> tuple[Path, str]:
    configured = explicit or os.environ.get("FREEROUTING_JAVA")
    found = configured or shutil.which("java")
    if not found:
        raise RouteReportError(
            "Java was not found. Pass --java or set FREEROUTING_JAVA."
        )
    path = Path(found).expanduser().resolve()
    run = _run([str(path), "-version"], timeout=20)
    output = (run["stdout"] + "\n" + run["stderr"]).strip()
    if run["returncode"] != 0 or not re.search(r"\bversion\s+\"?\d+", output, re.I):
        raise RouteReportError(
            "%s is not a working Java runtime: %s" % (path, output[-1000:])
        )
    first = next((line.strip() for line in output.splitlines() if line.strip()), "")
    return path, first


def resolve_router_jar(
    explicit: str | None,
    expected_sha256: str | None,
    accept_unpinned: bool,
) -> tuple[Path, str]:
    configured = explicit or os.environ.get("FREEROUTING_JAR")
    if not configured:
        raise RouteReportError(
            "Freerouting JAR not configured. Pass --freerouting-jar or set "
            "FREEROUTING_JAR; the wrapper never downloads or installs it."
        )
    path = Path(configured).expanduser().resolve()
    if not path.is_file() or path.stat().st_size == 0:
        raise RouteReportError("Freerouting JAR is missing or empty: %s" % path)
    actual = digest(path)
    if expected_sha256:
        expected = expected_sha256.lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise RouteReportError("--router-sha256 must be 64 hexadecimal characters")
        if actual != expected:
            raise RouteReportError(
                "Freerouting JAR digest mismatch: expected %s, found %s"
                % (expected, actual)
            )
    elif not accept_unpinned:
        raise RouteReportError(
            "refusing an unpinned Freerouting JAR. Pass --router-sha256 %s "
            "after verifying its provenance, or explicitly use "
            "--accept-unpinned-router for a disposable report run." % actual
        )
    return path, actual


def _related_sources(board: Path, no_parity: bool) -> dict[str, Path]:
    related = {"board": board}
    for label, suffix in (
        ("project", ".kicad_pro"),
        ("schematic", ".kicad_sch"),
        ("rules", ".kicad_dru"),
    ):
        if no_parity and label == "schematic":
            continue
        path = board.with_suffix(suffix)
        if path.is_file():
            related[label] = path
    if not no_parity:
        missing = [
            label
            for label in ("project", "schematic")
            if label not in related
        ]
        if missing:
            raise RouteReportError(
                "schematic parity requires same-stem %s beside %s; provide "
                "them or explicitly use --no-schematic-parity"
                % (" and ".join(missing), board)
            )

    # Project-local library tables are part of the board's interpretation.
    # Omitting them from scratch makes KiCad report one missing-library DRC
    # finding per custom footprint and can hide a real candidate regression in
    # that synthetic noise.  Copy only ${KIPRJMOD}-relative resources; global
    # libraries remain KiCad-installation inputs and absolute/external paths
    # are intentionally not pulled into the workspace.
    project_dir = board.parent.resolve()
    for table_name in ("fp-lib-table", "sym-lib-table"):
        table = project_dir / table_name
        if not table.is_file():
            continue
        related["project-table:" + table_name] = table
        table_text = _read_utf8(table)
        all_uris = re.findall(r'\(uri\s+"([^"\r\n]+)"\)', table_text)
        if table_text.count("(uri") != len(all_uris):
            raise RouteReportError(
                "%s contains an unparseable/non-quoted library URI" % table
            )
        unsupported_uris = [
            uri for uri in all_uris if not uri.startswith("${KIPRJMOD}/")
        ]
        if unsupported_uris:
            raise RouteReportError(
                "%s contains non-hermetic library URI(s): %s; vendor them below "
                "${KIPRJMOD} before a promotable run"
                % (table, ", ".join(sorted(unsupported_uris)))
            )
        for raw_relative in re.findall(
            r'\(uri\s+"\$\{KIPRJMOD\}/([^"\r\n]+)"\)', table_text
        ):
            relative = Path(raw_relative)
            if relative.is_absolute() or ".." in relative.parts:
                raise RouteReportError(
                    "%s contains escaping KIPRJMOD library path %r"
                    % (table, raw_relative)
                )
            source = (project_dir / relative).resolve()
            try:
                source.relative_to(project_dir)
            except ValueError as exc:
                raise RouteReportError(
                    "%s resolves outside the project: %s" % (table, source)
                ) from exc
            if not source.exists():
                raise RouteReportError(
                    "%s references missing project library %s" % (table, source)
                )
            related["project-resource:" + relative.as_posix()] = source
    # Same-stem discovery alone is insufficient for hierarchical schematics
    # and vendored project libraries.  Preserve every KiCad context file that
    # participates in the seed attestation at its project-relative path.
    known = {path.resolve() for path in related.values()}
    for entry in seed_context_bundle(board):
        source = (project_dir / entry["path"]).resolve()
        if source in known:
            continue
        related["project-resource:" + entry["path"]] = source
        known.add(source)
    return related


def _copy_sources(related: dict[str, Path], workspace: Path) -> dict[str, Path]:
    copied: dict[str, Path] = {}
    for label, source in related.items():
        if label.startswith("project-resource:"):
            destination = workspace / label.partition(":")[2]
        else:
            destination = workspace / source.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, destination)
        else:
            shutil.copy2(source, destination)
        copied[label] = destination
    return copied


def _digest_path(path: Path) -> str:
    if path.is_file():
        return digest(path)
    if path.is_dir():
        members = {
            child.relative_to(path).as_posix(): digest(child)
            for child in sorted(path.rglob("*"))
            if child.is_file()
        }
        return _json_digest(members)
    raise RouteReportError("cannot digest missing source artifact: %s" % path)


def _source_digests(related: dict[str, Path]) -> dict[str, str]:
    return {str(path.resolve()): _digest_path(path) for path in related.values()}


def _verify_source_unchanged(before: dict[str, str]) -> tuple[bool, dict[str, dict]]:
    changes: dict[str, dict] = {}
    for raw, expected in before.items():
        path = Path(raw)
        if not path.exists():
            changes[raw] = {"before": expected, "after": None}
            continue
        actual = _digest_path(path)
        if actual != expected:
            changes[raw] = {"before": expected, "after": actual}
    return not changes, changes


def _verify_copied_sources(
    related: dict[str, Path],
    copied: dict[str, Path],
    expected_by_source: dict[str, str],
) -> dict:
    evidence = {}
    for label, source in related.items():
        expected = expected_by_source[str(source.resolve())]
        source_after = _digest_path(source)
        copy_digest = _digest_path(copied[label])
        matched = expected == source_after == copy_digest
        evidence[label] = {
            "source": str(source.resolve()),
            "copy": str(copied[label].resolve()),
            "expected_sha256": expected,
            "source_after_copy_sha256": source_after,
            "copy_sha256": copy_digest,
            "matched": matched,
        }
        if not matched:
            raise RouteReportError(
                "source/copy digest drift for %s: expected %s, source %s, copy %s"
                % (label, expected, source_after, copy_digest)
            )
    return evidence


def _scope_end(text: str, start: int) -> int:
    """Return the index after one parenthesized DSN scope."""
    if start >= len(text) or text[start] != "(":
        raise RouteReportError("DSN scope does not start with '('")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                quoted = False
            continue
        if char == '"':
            quoted = True
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
            if depth < 0:
                break
    raise RouteReportError("unterminated DSN scope")


def _apply_dsn_layer_scope(
    dsn: Path, class_names: list[str], layer_names: list[str]
) -> dict:
    """Add a Specctra ``use_layer`` circuit rule to selected classes.

    KiCad's net classes carry width/clearance into DSN, but KiCad does not
    expose a per-net-class allowed-layer setting through that export.  Apply
    the standard Specctra circuit rule after export and fail if the expected
    class structure cannot be proved.
    """
    if not layer_names:
        return {"applied": False, "classes": [], "layers": []}
    for atom in [*class_names, *layer_names]:
        if not re.fullmatch(r"[A-Za-z0-9_.+/-]+", atom):
            raise RouteReportError(
                "DSN class/layer name is not a safe unquoted atom: %r" % atom
            )
    text = _read_utf8(dsn)
    applied = []
    layer_rule = "(use_layer %s)" % " ".join(layer_names)
    for class_name in class_names:
        match = re.search(
            r"(?m)^(?P<indent>[ \t]*)\(class[ \t]+"
            + re.escape(class_name)
            + r"(?:[ \t\r\n])",
            text,
        )
        if not match:
            raise RouteReportError(
                "DSN has no unambiguous class scope for %s" % class_name
            )
        class_start = match.start() + len(match.group("indent"))
        class_end = _scope_end(text, class_start)
        class_text = text[class_start:class_end]
        if re.search(r"\(use_layer(?:\s|\))", class_text):
            raise RouteReportError(
                "DSN class %s already has a use_layer rule; refusing to merge "
                "ambiguous layer policy" % class_name
            )
        circuit = re.search(r"(?m)^(?P<indent>[ \t]*)\(circuit(?:[ \t\r\n])", class_text)
        if circuit:
            circuit_start = class_start + circuit.start() + len(circuit.group("indent"))
            circuit_end = _scope_end(text, circuit_start)
            insertion = circuit_end - 1
            indent = circuit.group("indent") + "  "
            payload = "\n%s%s\n%s" % (indent, layer_rule, circuit.group("indent"))
        else:
            insertion = class_end - 1
            indent = match.group("indent") + "  "
            payload = "\n%s(circuit\n%s  %s\n%s)\n%s" % (
                indent,
                indent,
                layer_rule,
                indent,
                match.group("indent"),
            )
        text = text[:insertion] + payload + text[insertion:]
        applied.append(class_name)
    _write_text_atomic(dsn, text)
    return {"applied": True, "classes": applied, "layers": layer_names}


def _dsn_semantic_sha256(path: Path) -> str:
    """Hash DSN content after removing KiCad's output-path-only header."""
    text = _read_utf8(path)
    normalized, count = re.subn(
        r'^\(pcb\s+"[^"\r\n]+"',
        '(pcb "<workspace>/board.dsn"',
        text,
        count=1,
    )
    if count != 1:
        raise RouteReportError("DSN has no single canonicalizable PCB header")
    return _sha256_bytes(normalized.encode("utf-8"))


def _dsn_atoms(scope: str) -> list[str]:
    tokens = re.findall(r'"(?:\\.|[^"\\])*"|\(|\)|[^\s()]+', scope)
    return [token for token in tokens if token not in {"(", ")"}]


def _dsn_unquote(atom: str) -> str:
    if atom.startswith('"'):
        try:
            value = json.loads(atom)
        except json.JSONDecodeError as exc:
            raise RouteReportError("invalid quoted DSN atom: %s" % atom) from exc
        if not isinstance(value, str):
            raise RouteReportError("quoted DSN atom is not a string: %s" % atom)
        return value
    return atom


def _dsn_nm(atom: str, *, invert: bool = False) -> int:
    try:
        scaled = Decimal(atom) * Decimal(1000)
    except InvalidOperation as exc:
        raise RouteReportError("invalid DSN coordinate/size: %r" % atom) from exc
    integral = scaled.to_integral_value()
    if scaled != integral:
        raise RouteReportError(
            "DSN value %s cannot be represented exactly in KiCad nanometres" % atom
        )
    value = int(integral)
    return -value if invert else value


def _dsn_locked_point_nm(value: int) -> int:
    return int(
        round(value / DSN_LOCKED_POINT_QUANTUM_NM)
        * DSN_LOCKED_POINT_QUANTUM_NM
    )


def _dsn_named_scope_atom(scope: str, name: str) -> str:
    match = re.search(r"\(" + re.escape(name) + r"(?=\s|\()", scope)
    if not match:
        raise RouteReportError("DSN scope has no %s field" % name)
    end = _scope_end(scope, match.start())
    atoms = _dsn_atoms(scope[match.start():end])
    if len(atoms) != 2:
        raise RouteReportError("DSN %s field is not scalar: %s" % (name, atoms))
    return _dsn_unquote(atoms[1])


def _dsn_fixed_route_report(dsn: Path, seed_snapshot: dict) -> dict:
    """Prove that every locked KiCad segment/via became fixed DSN copper."""
    text = _read_utf8(dsn)
    structure_match = re.search(r"\(structure(?=\s|\()", text)
    if not structure_match:
        raise RouteReportError("DSN has no structure scope")
    structure_end = _scope_end(text, structure_match.start())
    structure = text[structure_match.start():structure_end]
    layer_names = [
        _dsn_unquote(match.group(1))
        for match in re.finditer(
            r"(?m)^[ \t]*\(layer[ \t]+(\"(?:\\.|[^\"\\])*\"|[^\s()]+)",
            structure,
        )
    ]
    if not layer_names:
        raise RouteReportError("DSN structure has no layer definitions")

    fixed_wire_scopes = 0
    fixed_wire_edges = 0
    fixed_vias = 0
    fixed_segment_keys = collections.Counter()
    fixed_via_keys = collections.Counter()
    for match in re.finditer(r"\((wire|via)(?=\s|\()", text):
        kind = match.group(1)
        end = _scope_end(text, match.start())
        scope = text[match.start():end]
        if not re.search(r"\(type\s+fix\s*\)", scope):
            continue
        if kind == "via":
            fixed_vias += 1
            atoms = _dsn_atoms(scope)
            if len(atoms) < 4:
                raise RouteReportError("fixed DSN via is malformed: %s" % atoms)
            padstack = _dsn_unquote(atoms[1])
            via_spec = re.fullmatch(
                r"Via\[(\d+)-(\d+)\]_(\d+(?:\.\d+)?):(\d+(?:\.\d+)?)_um",
                padstack,
            )
            if not via_spec:
                raise RouteReportError(
                    "cannot prove fixed via geometry from padstack %r" % padstack
                )
            top_index, bottom_index = map(int, via_spec.group(1, 2))
            if top_index >= len(layer_names) or bottom_index >= len(layer_names):
                raise RouteReportError(
                    "fixed via padstack layer index exceeds DSN layer list: %s"
                    % padstack
                )
            fixed_via_keys[
                (
                    _dsn_named_scope_atom(scope, "net"),
                    _dsn_locked_point_nm(_dsn_nm(atoms[2])),
                    _dsn_locked_point_nm(_dsn_nm(atoms[3], invert=True)),
                    layer_names[top_index],
                    layer_names[bottom_index],
                    _dsn_nm(via_spec.group(3)),
                    _dsn_nm(via_spec.group(4)),
                )
            ] += 1
            continue
        fixed_wire_scopes += 1
        path_match = re.search(r"\(path(?=\s|\()", scope)
        if not path_match:
            raise RouteReportError("fixed DSN wire has no path scope")
        path_end = _scope_end(scope, path_match.start())
        atoms = _dsn_atoms(scope[path_match.start():path_end])
        # path, layer, width, followed by two or more x/y coordinate pairs.
        coordinate_atoms = atoms[3:]
        if len(coordinate_atoms) < 4 or len(coordinate_atoms) % 2:
            raise RouteReportError(
                "fixed DSN wire has malformed path coordinates: %s" % atoms[:12]
            )
        points = [
            (
                _dsn_locked_point_nm(_dsn_nm(coordinate_atoms[index])),
                _dsn_locked_point_nm(
                    _dsn_nm(coordinate_atoms[index + 1], invert=True)
                ),
            )
            for index in range(0, len(coordinate_atoms), 2)
        ]
        net = _dsn_named_scope_atom(scope, "net")
        layer = _dsn_unquote(atoms[1])
        width_nm = _dsn_nm(atoms[2])
        for start, end in zip(points, points[1:]):
            start, end = sorted((start, end))
            fixed_segment_keys[
                (net, layer, width_nm, start[0], start[1], end[0], end[1])
            ] += 1
            fixed_wire_edges += 1

    locked_by_kind = collections.Counter(
        item["kind"] for item in seed_snapshot["routing"]["locked_items"]
    )
    expected_segments = locked_by_kind.get("segment", 0)
    expected_vias = locked_by_kind.get("via", 0)
    expected_segment_keys = collections.Counter()
    expected_via_keys = collections.Counter()
    for item in seed_snapshot["routing"]["locked_items"]:
        if item["kind"] == "segment":
            start, end = sorted(
                (tuple(item["start_nm"]), tuple(item["end_nm"]))
            )
            expected_segment_keys[
                (
                    item["net"],
                    item["layer"],
                    item["width_nm"],
                    _dsn_locked_point_nm(start[0]),
                    _dsn_locked_point_nm(start[1]),
                    _dsn_locked_point_nm(end[0]),
                    _dsn_locked_point_nm(end[1]),
                )
            ] += 1
        elif item["kind"] == "via":
            expected_via_keys[
                (
                    item["net"],
                    _dsn_locked_point_nm(item["position_nm"][0]),
                    _dsn_locked_point_nm(item["position_nm"][1]),
                    item["top_layer"],
                    item["bottom_layer"],
                    item["width_nm"],
                    item["drill_nm"],
                )
            ] += 1
    unsupported = {
        kind: count
        for kind, count in locked_by_kind.items()
        if kind not in {"segment", "via"} and count
    }
    passed = (
        not unsupported
        and fixed_wire_edges == expected_segments
        and fixed_vias == expected_vias
        and fixed_segment_keys == expected_segment_keys
        and fixed_via_keys == expected_via_keys
    )

    def examples(counter, limit=5):
        return [list(item) for item in list(counter.elements())[:limit]]

    missing_segments = expected_segment_keys - fixed_segment_keys
    unexpected_segments = fixed_segment_keys - expected_segment_keys
    missing_vias = expected_via_keys - fixed_via_keys
    unexpected_vias = fixed_via_keys - expected_via_keys
    return {
        "passed": passed,
        "expected_locked": dict(sorted(locked_by_kind.items())),
        "fixed_wire_scopes": fixed_wire_scopes,
        "fixed_wire_edges": fixed_wire_edges,
        "fixed_vias": fixed_vias,
        "geometry_bijection": {
            "coordinate_quantum_nm": DSN_LOCKED_POINT_QUANTUM_NM,
            "passed": not any(
                (missing_segments, unexpected_segments, missing_vias, unexpected_vias)
            ),
            "missing_segments": sum(missing_segments.values()),
            "unexpected_segments": sum(unexpected_segments.values()),
            "missing_vias": sum(missing_vias.values()),
            "unexpected_vias": sum(unexpected_vias.values()),
            "missing_segment_examples": examples(missing_segments),
            "unexpected_segment_examples": examples(unexpected_segments),
            "missing_via_examples": examples(missing_vias),
            "unexpected_via_examples": examples(unexpected_vias),
        },
        "unsupported_locked_kinds": unsupported,
    }


def _project_edge_clearance_um(project: Path | None) -> int | None:
    if project is None:
        return None
    try:
        data = json.loads(_read_utf8(project).lstrip("\ufeff"))
    except json.JSONDecodeError as exc:
        raise RouteReportError("%s is not valid JSON: %s" % (project, exc)) from exc
    value = (
        ((data.get("board") or {}).get("design_settings") or {})
        .get("rules", {})
        .get("min_copper_edge_clearance")
    )
    if not isinstance(value, (int, float)) or isinstance(value, bool) or value < 0:
        return None
    return int(round(float(value) * 1000.0))


def _worker_call(
    kicad_python: Path,
    mode: str,
    args: list[Path],
    snapshot_path: Path,
    workspace: Path,
) -> dict:
    cmd = [
        str(kicad_python),
        str(Path(__file__).resolve()),
        "--_pcb-worker",
        mode,
        *[str(x) for x in args],
        str(snapshot_path),
    ]
    worker_env = os.environ.copy()
    worker_env["KICAD_ROUTE_WORKER_ROOT"] = str(workspace.resolve())
    run = _run(cmd, cwd=workspace, timeout=180, env=worker_env)
    if run["returncode"] != 0:
        raise RouteReportError(
            "KiCad %s worker failed (rc=%d)\nstdout:\n%s\nstderr:\n%s"
            % (mode, run["returncode"], run["stdout"][-3000:], run["stderr"][-3000:])
        )
    if not snapshot_path.is_file():
        raise RouteReportError(
            "KiCad %s worker exited 0 but did not write %s" % (mode, snapshot_path)
        )
    try:
        result = json.loads(_read_utf8(snapshot_path))
    except json.JSONDecodeError as exc:
        raise RouteReportError(
            "KiCad %s worker wrote invalid JSON: %s" % (mode, exc)
        ) from exc
    result["worker_log"] = _log_record(run)
    return result


def _attest_v2_adapter_seed(
    *,
    config: dict,
    input_bundle: list[dict],
    supplied_seed: Path,
    supplied_snapshot: dict,
    kicad_python: Path,
    workspace: Path,
) -> dict:
    """Re-run the pinned adapter and prove the reviewed seed is its output."""
    root = Path(config["project_root"])
    adapter = root / config["tools"]["adapter"]["path"]
    # Compute this before creating adapter scratch below the candidate
    # workspace; context enumeration must describe only the supplied bundle.
    supplied = make_seed_attestation(
        supplied_snapshot, supplied_seed, config, input_bundle
    )
    describe_path = workspace / "adapter-attestation-describe.json"
    described = _run(
        [str(kicad_python), str(adapter), "describe", "--report", str(describe_path)],
        cwd=root,
        timeout=config["limits"]["audit_timeout_seconds"],
    )
    if described["returncode"] != 0 or not describe_path.is_file():
        raise RouteReportError(
            "configured adapter describe failed during seed attestation: "
            + (described["stderr"].strip() or "no report")
        )
    try:
        description = json.loads(_read_utf8(describe_path))
    except json.JSONDecodeError as exc:
        raise RouteReportError("configured adapter emitted invalid describe JSON") from exc
    if (
        description.get("protocol") != config["tools"]["adapter"]["protocol"]
        or description.get("mode") != config["project"]["mode"]
        or description.get("ready") is not True
        or set(description.get("operations") or []) != {"seed", "final"}
    ):
        raise RouteReportError(
            "configured adapter does not attest the required protocol/mode/operations"
        )

    generated_dir = workspace / "adapter-attested-seed"
    report_path = generated_dir / "adapter-seed-report.json"
    generated = _run(
        [
            str(kicad_python), str(adapter), "seed",
            "--output-dir", str(generated_dir), "--report", str(report_path),
        ],
        cwd=root,
        timeout=config["limits"]["timeout_seconds"],
    )
    if generated["returncode"] != 0 or not report_path.is_file():
        raise RouteReportError(
            "configured adapter seed failed during attestation: "
            + (generated["stderr"].strip() or "no report")
        )
    try:
        adapter_report = json.loads(_read_utf8(report_path))
    except json.JSONDecodeError as exc:
        raise RouteReportError("configured adapter emitted invalid seed report JSON") from exc
    if adapter_report.get("status") != "PASS":
        raise RouteReportError("configured adapter seed report did not pass")
    generated_board = generated_dir / config["project"]["board_basename"]
    required = [generated_board, generated_board.with_suffix(".kicad_pro")]
    if config["project"]["schematic_authority"] == "parity":
        required.append(generated_board.with_suffix(".kicad_sch"))
    elif generated_board.with_suffix(".kicad_sch").exists():
        raise RouteReportError(
            "board-only adapter seed unexpectedly contains a same-stem schematic"
        )
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise RouteReportError(
            "configured adapter seed omitted same-stem project context: "
            + ", ".join(missing)
        )
    generated_snapshot = _worker_call(
        kicad_python,
        "snapshot",
        [generated_board],
        workspace / "adapter-attested-seed-semantic.json",
        workspace,
    )["snapshot"]
    regenerated = make_seed_attestation(
        generated_snapshot, generated_board, config, input_bundle
    )
    if supplied != regenerated:
        raise RouteReportError(
            "supplied seed is not semantically identical to the configured adapter output: "
            f"supplied {supplied['sha256']}, adapter {regenerated['sha256']}"
        )
    return {
        "description": description,
        "seed_report": adapter_report,
        "attestation": supplied,
        "regenerated_seed_sha256": digest(generated_board),
        "byte_seed_match": digest(supplied_seed) == digest(generated_board),
    }


def _drc_snapshot(board: Path, report: Path, parity: bool, cli: str) -> dict:
    try:
        rc, counts = run_drc(board, report=report, parity=parity, cli=cli)
        ignored = ignored_checks_from_report(report)
    except VerifyError as exc:
        raise RouteReportError("KiCad DRC could not be verified: %s" % exc) from exc
    return {
        "returncode": rc,
        "counts": counts,
        "ignored_checks": ignored,
        "report_sha256": digest(report),
    }


def _run_json_drc(
    board: Path,
    report: Path,
    *,
    parity: bool,
    cli: str,
    identity_map: dict[str, str],
) -> dict:
    """Run KiCad's structured DRC and normalize it fail-closed."""
    before = report.stat().st_mtime_ns if report.exists() else None
    command = [
        cli,
        "pcb",
        "drc",
        "--format",
        "json",
        "--refill-zones",
        "--severity-all",
        "--exit-code-violations",
        "-o",
        str(report),
    ]
    if parity:
        command.append("--schematic-parity")
    command.append(str(board))
    env = os.environ.copy()
    env["LC_ALL"] = "C"
    run = _run(command, cwd=board.parent, timeout=300, env=env)
    if run["returncode"] not in (0, 5):
        raise RouteReportError(
            "KiCad JSON DRC failed (rc=%d): %s"
            % (run["returncode"], run["stderr"][-3000:])
        )
    if not report.is_file() or (before is not None and report.stat().st_mtime_ns == before):
        raise RouteReportError("KiCad JSON DRC did not freshly write %s" % report)
    try:
        raw = json.loads(_read_utf8(report))
        normalized = normalize_drc_report(raw, identity_map)
    except (json.JSONDecodeError, AutorouteError) as exc:
        raise RouteReportError("KiCad JSON DRC cannot be normalized: %s" % exc) from exc
    return {
        "returncode": run["returncode"],
        "report_sha256": digest(report),
        "normalized": normalized,
        "command": command,
        "log": _log_record(run),
    }


def _identity_map(
    kicad_python: Path, board: Path, output: Path, workspace: Path
) -> dict[str, str]:
    command = [
        str(kicad_python),
        str(Path(__file__).with_name("kicad_route_manifest.py").resolve()),
        "identity",
        "--board",
        str(board),
        "--output",
        str(output),
    ]
    run = _run(command, cwd=workspace, timeout=180)
    if run["returncode"] != 0 or not output.is_file():
        raise RouteReportError(
            "KiCad identity-map worker failed (rc=%d): %s"
            % (run["returncode"], run["stderr"][-3000:])
        )
    try:
        value = json.loads(_read_utf8(output))
    except json.JSONDecodeError as exc:
        raise RouteReportError("identity map is invalid JSON: %s" % exc) from exc
    if not isinstance(value, dict) or not value:
        raise RouteReportError("identity map is empty")
    return value


def _expand_audit_tokens(tokens: list[str], *, board: Path, workspace: Path, config_dir: Path) -> list[str]:
    values = {
        "{board}": str(board),
        "{workspace}": str(workspace),
        "{config_dir}": str(config_dir),
    }
    return [
        token.replace("{board}", values["{board}"])
        .replace("{workspace}", values["{workspace}"])
        .replace("{config_dir}", values["{config_dir}"])
        for token in tokens
    ]


def _run_structured_audits(
    entries: list[dict],
    *,
    board: Path,
    workspace: Path,
    config_dir: Path,
    kicad_python: Path,
) -> dict:
    results = []
    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
    }
    for name in ("HOME", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            environment[name] = os.environ[name]
    for index, entry in enumerate(entries):
        tokens = _expand_audit_tokens(
            entry["argv"], board=board, workspace=workspace, config_dir=config_dir
        )
        if entry["interpreter"] != "kicad_python":
            raise RouteReportError("promotable project audits require kicad_python")
        command = [str(kicad_python), *tokens]
        # Resolve the script/executable token only when it is a relative path.
        program_index = 1 if entry["interpreter"] != "direct" else 0
        if program_index < len(command):
            candidate = Path(command[program_index])
            if not candidate.is_absolute() and (config_dir / candidate).exists():
                command[program_index] = str((config_dir / candidate).resolve())
        before = digest(board)
        run = _run(
            command,
            cwd=config_dir,
            timeout=entry["timeout_seconds"],
            env=environment,
        )
        after = digest(board)
        marker = entry.get("calibration_marker")
        marker_passed = marker is None or marker in run["stdout"]
        record = _log_record(run)
        record.update(
            {
                "index": index,
                "interpreter": entry["interpreter"],
                "passed": (
                    run["returncode"] == 0
                    and before == after
                    and marker_passed
                ),
                "board_sha256_before": before,
                "board_sha256_after": after,
                "board_unchanged": before == after,
                "interpreter_sha256": digest(kicad_python),
                "program_sha256": digest(Path(command[1])),
                "calibration_marker": marker,
                "calibration_marker_passed": marker_passed,
                "environment_policy": "minimal-v1",
            }
        )
        results.append(record)
    calibrated = [item for item in results if item["calibration_marker"] is not None]
    return {
        "configured": bool(entries),
        "count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "board_unchanged": all(item["board_unchanged"] for item in results),
        "calibration_configured": bool(calibrated),
        "calibration_passed": bool(calibrated) and all(
            item["calibration_marker_passed"] for item in calibrated
        ),
        "results": results,
    }


def _apply_filtered_routes(
    kicad_python: Path,
    seed_copies: dict[str, Path],
    workspace: Path,
    routes: list[dict],
    *,
    label: str = "filtered-candidate",
) -> tuple[Path, dict]:
    directory = workspace / label
    directory.mkdir()
    copies = _copy_sources(seed_copies, directory)
    board = copies["board"]
    routes_path = workspace / f"{label}-routes.json"
    summary_path = workspace / f"{label}-apply.json"
    identity_path = workspace / f"{label}-identity.json"
    _write_json_atomic(routes_path, routes)
    command = [
        str(kicad_python),
        str(Path(__file__).with_name("kicad_route_manifest.py").resolve()),
        "apply",
        "--board",
        str(board),
        "--routes",
        str(routes_path),
        "--output",
        str(board),
        "--summary",
        str(summary_path),
        "--identity-map",
        str(identity_path),
        "--refill-zones",
    ]
    run = _run(command, cwd=workspace, timeout=300)
    if run["returncode"] != 0 or not summary_path.is_file():
        raise RouteReportError(
            "filtered candidate application failed (rc=%d): %s"
            % (run["returncode"], run["stderr"][-3000:])
        )
    result = json.loads(_read_utf8(summary_path))
    result["identity_map"] = json.loads(_read_utf8(identity_path))
    result["worker_log"] = _log_record(run)
    return board, result


def _counter(items: list[dict]) -> collections.Counter[str]:
    return collections.Counter(
        json.dumps(x, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for x in items
    )


def _decode_item(raw: str) -> dict:
    return json.loads(raw)


def _route_geometry_items(snapshot: dict) -> list[dict]:
    items = []
    for raw in snapshot["routing"]["items"]:
        item = dict(raw)
        # Lock state controls what the external router may rip up; it is not
        # copper geometry.  Length is derived from the endpoints and can be
        # rounded differently by a harmless DSN/SES round trip.
        item.pop("locked", None)
        item.pop("length_nm", None)
        items.append(item)
    return items


def _route_delta(seed: dict, candidate: dict) -> dict:
    before = _counter(_route_geometry_items(seed))
    after = _counter(_route_geometry_items(candidate))
    added_counter = after - before
    removed_counter = before - after
    added = [
        _decode_item(raw)
        for raw, count in added_counter.items()
        for _ in range(count)
    ]
    removed = [
        _decode_item(raw)
        for raw, count in removed_counter.items()
        for _ in range(count)
    ]

    def summarize(items: list[dict]) -> dict:
        by_net = collections.Counter(x.get("net", "") for x in items)
        by_kind = collections.Counter(x.get("kind", "unknown") for x in items)
        return {
            "count": len(items),
            "by_net": dict(sorted(by_net.items())),
            "by_kind": dict(sorted(by_kind.items())),
            "examples": items[:20],
        }

    return {"added": summarize(added), "removed": summarize(removed), "_added": added,
            "_removed": removed}


def _scope_report(
    seed: dict,
    delta: dict,
    allowed_classes: list[str],
    allow_all: bool,
) -> dict:
    class_names = set(seed["netclasses"]["class_names"])
    unknown = sorted(set(allowed_classes) - class_names)
    if unknown:
        raise RouteReportError(
            "allowed net class(es) not present in the board: %s; available: %s"
            % (", ".join(unknown), ", ".join(sorted(class_names)))
        )
    if not allow_all and not allowed_classes:
        raise RouteReportError(
            "full routing requires at least one --allow-net-class, or the "
            "explicit --allow-all-net-classes override"
        )
    selected = class_names if allow_all else set(allowed_classes)
    ignored = sorted(class_names - selected)
    net_to_class = seed["netclasses"]["net_to_class"]
    resolved_allowed_nets = sorted(
        net for net, net_class in net_to_class.items() if net_class in selected
    )
    violations = []
    for direction in ("_added", "_removed"):
        for item in delta[direction]:
            net = item.get("net", "")
            net_class = net_to_class.get(net)
            if net_class not in selected:
                violations.append(
                    {
                        "direction": direction.lstrip("_"),
                        "net": net,
                        "net_class": net_class,
                        "kind": item.get("kind"),
                    }
                )
    return {
        "allow_all_net_classes": allow_all,
        "allowed_net_classes": sorted(selected),
        "resolved_allowed_nets": resolved_allowed_nets,
        "ignored_net_classes": ignored,
        "enforcement": (
            "post-import semantic audit; the characterized headless -inc is "
            "advisory and was observed routing an ignored class"
        ),
        "violations_count": len(violations),
        "violation_examples": violations[:50],
    }


def _layer_scope_report(delta: dict, allowed_layers: list[str]) -> dict:
    allowed = set(allowed_layers)
    violations = []
    if allowed:
        for direction in ("_added", "_removed"):
            for item in delta[direction]:
                if item.get("kind") == "via":
                    item_layers = {
                        item.get("top_layer"), item.get("bottom_layer")
                    }
                    item_layers.discard(None)
                else:
                    item_layers = {item.get("layer")}
                outside = sorted(item_layers - allowed)
                if outside:
                    violations.append(
                        {
                            "direction": direction.lstrip("_"),
                            "net": item.get("net", ""),
                            "kind": item.get("kind"),
                            "item_layers": sorted(item_layers),
                            "outside_allowed_layers": outside,
                        }
                    )
    return {
        "allowed_layers": sorted(allowed),
        "enforcement": (
            "Specctra use_layer rule plus post-import semantic audit"
            if allowed
            else "no layer scope requested"
        ),
        "violations_count": len(violations),
        "violation_examples": violations[:50],
    }


def _locked_route_report(seed: dict, candidate: dict) -> dict:
    before = _counter(seed["routing"]["locked_items"])
    after = _counter(candidate["routing"]["locked_items"])
    missing = before - after
    added = after - before
    return {
        "seed_count": sum(before.values()),
        "candidate_count": sum(after.values()),
        "missing_count": sum(missing.values()),
        "new_count": sum(added.values()),
        "missing_examples": [_decode_item(x) for x in list(missing)[:20]],
        "new_examples": [_decode_item(x) for x in list(added)[:20]],
    }


def _protected_route_report(seed: dict, candidate: dict) -> dict:
    """Prove every seed primitive survived; candidate additions are allowed."""
    before = _counter(_route_geometry_items(seed))
    after = _counter(_route_geometry_items(candidate))
    missing = before - after
    return {
        "seed_count": sum(before.values()),
        "candidate_count": sum(after.values()),
        "missing_count": sum(missing.values()),
        "new_count": 0,
        "missing_examples": [_decode_item(x) for x in list(missing)[:20]],
        "new_examples": [],
        "policy": "all scratch seed copper protected; route additions permitted",
    }


def _clean_delta_for_report(delta: dict) -> dict:
    return {"added": delta["added"], "removed": delta["removed"]}


def _validate_positive(name: str, value: int, *, allow_zero: bool = False) -> int:
    if isinstance(value, bool) or value < (0 if allow_zero else 1):
        qualifier = "non-negative" if allow_zero else "positive"
        raise RouteReportError("%s must be a %s integer" % (name, qualifier))
    return value


def _router_command(
    java: Path,
    jar: Path,
    dsn: Path,
    ses: Path,
    workspace: Path,
    ignored_classes: list[str],
    max_passes: int,
    threads: int,
    copper_edge_um: int,
) -> list[str]:
    for name in ignored_classes:
        if "," in name:
            raise RouteReportError(
                "Freerouting's -inc syntax cannot safely represent a net class "
                "containing a comma: %r" % name
            )
    cmd = [
        str(java),
        "-jar",
        str(jar),
        "-de",
        str(dsn),
        "-do",
        str(ses),
        "-mp",
        str(max_passes),
        "-mt",
        str(threads),
        "-dct",
        "0",
        "-da",
        "--gui.enabled=false",
        "--api_server.enabled=false",
        "--logging.console.enabled=true",
        "--logging.file.enabled=true",
        "--logging.file.location=%s" % (workspace / "router-logs"),
        "--user_data_path=%s" % (workspace / "router-user-data"),
        # RouterSettings v2.3.0 uses the SerializedName spelling here.  The
        # release note showed camelCase, but the shipped JAR rejects it as an
        # unknown property; the source and a live CLI run both confirm snake
        # case.
        "--router.copper_to_edge_clearance_um=%d" % copper_edge_um,
        # Promotion v1 has one exact width per route class.  Freerouting 2.3.0
        # otherwise introduces undocumented-by-DSN 75%-ish micro-neckdowns
        # near pads, which cannot satisfy that deterministic style contract.
        "--router.automatic_neckdown=false",
        # v2.3.0's fanout micro-neckdown fallback is intentionally independent
        # of automatic_neckdown.  Disable the fanout pre-pass as well so every
        # promoted segment can obey the declared class width.
        "--router.fanout.enabled=false",
    ]
    if ignored_classes:
        cmd.extend(("-inc", ",".join(ignored_classes)))
    return cmd


def _router_environment() -> tuple[dict[str, str], list[str]]:
    """Remove ambient settings that can silently alter the pinned router run."""
    blocked_exact = {"_JAVA_OPTIONS", "JAVA_TOOL_OPTIONS", "JDK_JAVA_OPTIONS"}
    removed = sorted(
        key
        for key in os.environ
        if key.upper().startswith("FREEROUTING") or key in blocked_exact
    )
    env = {key: value for key, value in os.environ.items() if key not in removed}
    env["LC_ALL"] = "C"
    return env, removed


def _router_version(
    java: Path,
    jar: Path,
    workspace: Path,
    timeout_seconds: int,
    env: dict[str, str],
) -> tuple[str | None, dict]:
    run = _run(
        [
            str(java),
            "-jar",
            str(jar),
            "-help",
            "--gui.enabled=false",
            "-da",
            "--api_server.enabled=false",
            "--logging.file.enabled=false",
            "--user_data_path=%s" % (workspace / "router-version-user-data"),
        ],
        cwd=workspace,
        timeout=min(timeout_seconds, 60),
        env=env,
    )
    output = run["stdout"] + "\n" + run["stderr"]
    match = re.search(r"(?:freerouting[^\d]{0,20}|version\s+)(\d+\.\d+(?:\.\d+)?)", output, re.I)
    if run["returncode"] != 0:
        raise RouteReportError(
            "Freerouting help/version probe failed (rc=%d): %s"
            % (run["returncode"], output[-2000:])
        )
    return (match.group(1) if match else None), _log_record(run)


def _run_audit_commands(
    raw_commands: list[str],
    candidate_board: Path,
    workspace: Path,
    timeout_seconds: int,
) -> dict:
    results = []
    for index, raw in enumerate(raw_commands, start=1):
        try:
            command = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RouteReportError(
                "--audit-command-json #%d is invalid JSON: %s" % (index, exc)
            ) from exc
        if (
            not isinstance(command, list)
            or not command
            or any(not isinstance(token, str) or not token for token in command)
        ):
            raise RouteReportError(
                "--audit-command-json #%d must be a non-empty JSON array of "
                "non-empty strings" % index
            )
        if "{board}" not in command:
            raise RouteReportError(
                "--audit-command-json #%d must contain a standalone {board} token"
                % index
            )
        resolved = [
            str(candidate_board)
            if token == "{board}"
            else str(workspace) if token == "{workspace}" else token
            for token in command
        ]
        run = _run(
            resolved,
            cwd=candidate_board.parent,
            timeout=timeout_seconds,
        )
        record = _log_record(run)
        record["index"] = index
        record["passed"] = run["returncode"] == 0
        results.append(record)
    return {
        "configured": bool(raw_commands),
        "count": len(results),
        "passed": sum(item["passed"] for item in results),
        "failed": sum(not item["passed"] for item in results),
        "results": results,
    }


def _make_workspace(keep: str | None):
    if keep:
        path = Path(keep).expanduser().resolve()
        if path.exists():
            raise RouteReportError(
                "--keep-workspace target already exists; refusing to mix a "
                "new report with old artifacts: %s" % path
            )
        path.mkdir(parents=True)

        class PersistentWorkspace:
            def __enter__(self):
                return str(path)

            def __exit__(self, exc_type, exc, tb):
                return False

        return PersistentWorkspace()
    return tempfile.TemporaryDirectory(prefix="kicad-route-candidate-")


def _report_mode(args: argparse.Namespace) -> str:
    if args.prepare_only:
        return "prepare-only"
    if args.exploratory:
        return "exploratory-report"
    return "route-and-report"


def _finalize_report(
    report: dict,
    *,
    findings: list[str],
    promotion_blocks: list[str],
    args: argparse.Namespace,
    config: dict | None,
    project_audits: dict,
) -> int:
    """Set the terminal report state without letting exploration carry approval data."""
    if args.exploratory:
        # Defense in depth: even if an earlier refactor accidentally constructs
        # promotion evidence, an exploratory report cannot retain it.
        report.pop("promotion", None)
    report["findings"] = findings
    if findings:
        report["verdict"] = "REJECT"
    elif args.exploratory:
        report["verdict"] = "EXPLORATORY"
    elif config is not None and not promotion_blocks:
        report["verdict"] = "PROMOTABLE_CANDIDATE"
    elif config is not None:
        report["verdict"] = "REPORT_ONLY"
    else:
        report["verdict"] = (
            "PROJECT_AUDITS_PASSED"
            if project_audits["configured"]
            else "GENERIC_CHECKS_ONLY"
        )
    report["verdict_reason"] = (
        "; ".join(findings)
        if findings
        else (
            "Exploratory report completed without a generic rejection condition. "
            "Its geometry may inform placement and critical-route planning, but "
            "cannot be promoted or copied as accepted routing."
            if report["verdict"] == "EXPLORATORY"
            else
            "Every promotion gate passed; an explicit candidate/report digest approval is still required."
            if report["verdict"] == "PROMOTABLE_CANDIDATE"
            else "; ".join(promotion_blocks)
            if report["verdict"] == "REPORT_ONLY"
            else
            "Generic checks and every configured project audit command passed. "
            "The partial non-routing snapshot cannot prove complete invariant "
            "preservation, so REVIEW is intentionally withheld."
            if project_audits["configured"]
            else "Generic checks found no rejection condition, but no project "
                 "audit command was configured. Project guards and visual review "
                 "remain mandatory; REVIEW is intentionally withheld."
        )
    )
    return 3 if findings and args.fail_on_findings else 0


def run_report(args: argparse.Namespace) -> tuple[dict, int]:
    board = Path(args.board).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    if not board.is_file() or board.suffix != ".kicad_pcb":
        raise RouteReportError("board is not a .kicad_pcb file: %s" % board)
    if report_path == board:
        raise RouteReportError("report path must not be the source board")
    if args.keep_workspace and Path(args.keep_workspace).expanduser().resolve() == board.parent:
        raise RouteReportError(
            "--keep-workspace must be a new directory, not the source project directory"
        )
    if not args.prepare_only and not args.keep_workspace:
        raise RouteReportError(
            "route-and-report requires --keep-workspace so full router logs, "
            "DSN, SES, raw import, and candidate evidence are retained"
        )

    _validate_positive("--max-passes", args.max_passes)
    # Freerouting 2.3.0's shipped source normalizes 0 to all available
    # processors, despite older CLI prose describing 0 as disabled.  Require
    # a positive explicit bound instead of letting that ambiguity consume the
    # host unexpectedly.
    _validate_positive("--threads", args.threads)
    _validate_positive("--timeout-seconds", args.timeout_seconds)
    _validate_positive("--audit-timeout-seconds", args.audit_timeout_seconds)
    _validate_positive("--expected-unconnected", args.expected_unconnected,
                       allow_zero=True)

    related = _related_sources(board, args.no_schematic_parity)
    before_digests = _source_digests(related)
    config = getattr(args, "_autoroute_config", None)
    input_bundle = getattr(args, "_input_bundle", None)
    input_bundle_root = getattr(args, "_input_bundle_root", None)
    initial = {
        "schema": REPORT_SCHEMA,
        "mode": _report_mode(args),
        "created_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": {
            "board": str(board),
            "digests_before": before_digests,
        },
        "tools": {
            "host": {
                "platform": platform.platform(),
                "python": sys.version.split()[0],
            },
        },
        "limitations": [
            "REPORT ONLY: this wrapper cannot promote or approve routing.",
            "The non-routing semantic snapshot is a partial drift detector; it omits some KiCad object properties and cannot prove full invariant preservation.",
            "The generic semantic snapshot does not replace project-specific electrical or geometry guards.",
            "DSN does not carry every KiCad constraint; KiCad DRC and independent audits remain mandatory.",
            "Freerouting 2.3.0 headless -inc was observed routing an ignored net class; post-import scope audit is load-bearing.",
        ],
    }
    if args.exploratory:
        initial["limitations"].append(
            "EXPLORATORY ONLY: use this geometry to diagnose placement, congestion, "
            "and possible corridors; it is never promotion evidence or accepted routing."
        )
    if config is not None:
        initial["configuration"] = {
            "schema": config["schema"],
            "path": config["config_path"],
            "sha256": config["config_sha256"],
            "input_bundle_root": str(input_bundle_root),
            "input_bundle": input_bundle,
            "input_bundle_sha256": canonical_json_sha256(input_bundle),
        }
        if config.get("schema") == CONFIG_SCHEMA_V2:
            initial["configuration"]["schematic_authority"] = config["project"][
                "schematic_authority"
            ]
            if config["project"]["schematic_authority"] == "board-only":
                initial["limitations"].append(
                    "PERMANENT WAIVER: schematic parity and ERC are unavailable; "
                    "the PCB is the declared authority."
                )
    # Keep a live reference on the parsed arguments so main() can still emit
    # all completed evidence if a later tool probe or router stage fails.
    # Without this, the most useful failure (for example a Java/JAR mismatch)
    # collapses to an error string and loses the successful DSN/DRC preflight.
    args._partial_report = initial

    kicad_python, pcbnew_version = find_kicad_python(args.kicad_python)
    try:
        kicad_cli = find_kicad_cli(args.kicad_cli)
    except VerifyError as exc:
        raise RouteReportError(str(exc)) from exc
    initial["tools"].update(
        {
            "kicad_python": str(kicad_python),
            "pcbnew_version": pcbnew_version,
            "kicad_cli": str(kicad_cli),
        }
    )
    compatibility = None
    if config is not None:
        compatibility = _compatibility_cell(pcbnew_version, Path(kicad_cli))
        initial["tools"]["compatibility"] = compatibility

    with _make_workspace(args.keep_workspace) as raw_workspace:
        workspace = Path(raw_workspace).resolve()
        initial["workspace"] = str(workspace) if args.keep_workspace else None
        copied = _copy_sources(related, workspace)
        initial["scratch_copies"] = _verify_copied_sources(
            related, copied, before_digests
        )
        seed = copied["board"]
        dsn = workspace / (board.stem + ".dsn")
        ses = workspace / (board.stem + ".ses")
        # Keep the original basename in a separate directory.  KiCad discovers
        # the project and schematic for --schematic-parity by same-stem name;
        # renaming the candidate board to "*-candidate.kicad_pcb" silently
        # severs that association.
        candidate_dir = workspace / "candidate"
        candidate_board = candidate_dir / board.name
        export_snapshot_path = workspace / "seed-semantic.json"
        import_snapshot_path = workspace / "candidate-semantic.json"

        adapter_seed_evidence = None
        if config is not None and config.get("schema") == CONFIG_SCHEMA_V2:
            supplied_snapshot = _worker_call(
                kicad_python,
                "snapshot",
                [seed],
                workspace / "supplied-seed-attestation-semantic.json",
                workspace,
            )["snapshot"]
            adapter_seed_evidence = _attest_v2_adapter_seed(
                config=config,
                input_bundle=input_bundle,
                supplied_seed=seed,
                supplied_snapshot=supplied_snapshot,
                kicad_python=kicad_python,
                workspace=workspace,
            )

        export_result = _worker_call(
            kicad_python,
            "export",
            [seed, dsn],
            export_snapshot_path,
            workspace,
        )
        if not export_result.get("export_ok") or not dsn.is_file() or dsn.stat().st_size == 0:
            raise RouteReportError("KiCad did not produce a non-empty DSN")
        seed_snapshot = export_result["snapshot"]
        empty_delta = {"_added": [], "_removed": []}
        scope = _scope_report(
            seed_snapshot,
            empty_delta,
            args.allow_net_class,
            args.allow_all_net_classes,
        )
        if config is not None and config.get("schema") == CONFIG_SCHEMA_V2:
            live_mapping = {
                net: class_name
                for net, class_name in seed_snapshot["netclasses"]["net_to_class"].items()
                if class_name in config["scope"]["net_classes"]
            }
            expected_mapping = config["scope"]["net_to_class"]
            if live_mapping != expected_mapping:
                raise RouteReportError(
                    "live KiCad net-class resolution differs from frozen v2 net_to_class: "
                    f"expected {expected_mapping}, live {live_mapping}"
                )
        requested_layers = list(dict.fromkeys(args.allow_layer))
        available_layers = set(seed_snapshot["board"]["copper_layers"])
        unknown_layers = sorted(set(requested_layers) - available_layers)
        if unknown_layers:
            raise RouteReportError(
                "allowed copper layer(s) not present in the board: %s; available: %s"
                % (", ".join(unknown_layers), ", ".join(sorted(available_layers)))
            )
        dsn_export_sha = digest(dsn)
        dsn_layer_scope = _apply_dsn_layer_scope(
            dsn, scope["allowed_net_classes"], requested_layers
        )
        dsn_fixed_routes = _dsn_fixed_route_report(dsn, seed_snapshot)
        layer_scope = _layer_scope_report(empty_delta, requested_layers)
        initial["scope"] = {**scope, "layers": layer_scope}
        initial["seed"] = {
            "board_sha256": digest(seed),
            "dsn_sha256": digest(dsn),
            "dsn_semantic_sha256": _dsn_semantic_sha256(dsn),
            "dsn_export_sha256": dsn_export_sha,
            "dsn_bytes": dsn.stat().st_size,
            "dsn_layer_scope": dsn_layer_scope,
            "dsn_fixed_routes": dsn_fixed_routes,
            "semantic": {
                "routing": seed_snapshot["routing"]["summary"],
                "nonrouting_point_quantum_nm": seed_snapshot[
                    "nonrouting_point_quantum_nm"
                ],
                "nonrouting_sha256": seed_snapshot["nonrouting_sha256"],
                "nonrouting_category_sha256": seed_snapshot[
                    "nonrouting_category_sha256"
                ],
                "netclasses": seed_snapshot["netclasses"],
                "board": seed_snapshot["board"],
            },
            "export_worker": export_result["worker_log"],
        }
        if adapter_seed_evidence is not None:
            initial["seed"]["adapter_attestation"] = adapter_seed_evidence
        if not dsn_fixed_routes["passed"]:
            raise RouteReportError(
                "DSN export did not preserve every locked route as fixed copper: %s"
                % dsn_fixed_routes
            )

        qualified_seed = seed
        if config is not None:
            qualified_seed, qualification = _apply_filtered_routes(
                kicad_python,
                copied,
                workspace,
                [],
                label="qualified-seed",
            )
            initial["seed"]["qualification_roundtrip"] = {
                key: value
                for key, value in qualification.items()
                if key != "identity_map"
            }
        seed_drc = _drc_snapshot(
            qualified_seed,
            workspace / "seed-drc.rpt",
            not args.no_schematic_parity,
            str(kicad_cli),
        )
        initial["seed"]["drc"] = seed_drc
        seed_findings = []
        # Configured projects use the structured, position-sensitive baseline
        # below.  The aggregate text count is retained as evidence but cannot
        # express must-resolve versus explicitly dispositioned findings.
        if config is None and seed_drc["counts"].get("drc violations", 0) != 0:
            seed_findings.append(
                "route seed has %d DRC violation(s); fix geometry before autorouting"
                % seed_drc["counts"].get("drc violations", 0)
            )
        if config is None and seed_drc["counts"].get("footprint errors", 0) != 0:
            seed_findings.append(
                "route seed has %d footprint error(s); fix them before autorouting"
                % seed_drc["counts"].get("footprint errors", 0)
            )

        baseline = None
        if config is not None:
            seed_identity = _identity_map(
                kicad_python,
                qualified_seed,
                workspace / "seed-identity.json",
                workspace,
            )
            seed_json_drc = _run_json_drc(
                qualified_seed,
                workspace / "seed-drc.json",
                parity=not args.no_schematic_parity,
                cli=str(kicad_cli),
                identity_map=seed_identity,
            )
            initial["seed"]["json_drc"] = seed_json_drc
            baseline_path = config_path(config, config["seed"]["drc_baseline"])
            if args.create_seed_drc_baseline or args.replace_seed_drc_baseline:
                if args.create_seed_drc_baseline and baseline_path.exists():
                    raise RouteReportError(
                        "refusing to overwrite existing seed DRC baseline %s"
                        % baseline_path
                    )
                if args.replace_seed_drc_baseline and not baseline_path.is_file():
                    raise RouteReportError(
                        "cannot replace missing seed DRC baseline %s"
                        % baseline_path
                    )
                old_sha256 = digest(baseline_path) if baseline_path.is_file() else None
                _write_json_atomic(
                    baseline_path,
                    make_drc_baseline(seed_json_drc["normalized"]),
                )
                initial["seed"][
                    "baseline_replaced" if args.replace_seed_drc_baseline else "baseline_created"
                ] = {
                    "path": str(baseline_path),
                    "old_sha256": old_sha256,
                    "new_sha256": digest(baseline_path),
                }
                initial["findings"] = seed_findings
                initial["verdict"] = "PREPARED"
                initial["verdict_reason"] = (
                    "Tracked seed DRC baseline written; review dispositions before routing."
                )
                unchanged, changes = _verify_source_unchanged(before_digests)
                initial["source"]["unchanged"] = unchanged
                initial["source"]["changes"] = changes
                return initial, 0 if unchanged else 2
            if not baseline_path.is_file():
                raise RouteReportError(
                    "configured seed DRC baseline is missing: %s; create it with "
                    "--prepare-only --create-seed-drc-baseline" % baseline_path
                )
            try:
                baseline = json.loads(_read_utf8(baseline_path))
                baseline_problems = compare_drc(
                    seed_json_drc["normalized"], baseline, final=False
                )
            except (json.JSONDecodeError, AutorouteError) as exc:
                raise RouteReportError("seed DRC baseline is invalid: %s" % exc) from exc
            initial["seed"]["drc_baseline"] = {
                "path": str(baseline_path),
                "sha256": digest(baseline_path),
                "problems": baseline_problems,
            }
            seed_findings.extend(baseline_problems)

            seed_audits = _run_structured_audits(
                config["seed"]["audit_commands"],
                board=qualified_seed,
                workspace=workspace,
                config_dir=Path(config["config_dir"]),
                kicad_python=kicad_python,
            )
            initial["seed"]["project_audits"] = seed_audits
            if seed_audits["failed"]:
                seed_findings.append(
                    "route seed failed %d/%d configured project audit command(s)"
                    % (seed_audits["failed"], seed_audits["count"])
                )
            if not seed_audits["board_unchanged"]:
                seed_findings.append("a configured seed audit command changed the route seed")

        project = related.get("project")
        if project:
            initial["seed"]["severity_map"] = severity_report(project)

        if args.prepare_only:
            unchanged, changes = _verify_source_unchanged(before_digests)
            initial["source"]["unchanged"] = unchanged
            initial["source"]["changes"] = changes
            if not unchanged:
                seed_findings.append("a source artifact changed during the report run")
            initial["findings"] = seed_findings
            initial["verdict"] = (
                "ERROR"
                if not unchanged
                else "PREPARED_WITH_FINDINGS" if seed_findings else "PREPARED"
            )
            initial["verdict_reason"] = (
                "; ".join(seed_findings)
                if seed_findings
                else "DSN export and seed preflight completed; no router was invoked."
            )
            if not unchanged:
                return initial, 2
            return initial, 3 if seed_findings and args.fail_on_findings else 0

        if seed_findings and not args.route_with_seed_findings:
            unchanged, changes = _verify_source_unchanged(before_digests)
            initial["source"]["unchanged"] = unchanged
            initial["source"]["changes"] = changes
            if not unchanged:
                seed_findings.append("a source artifact changed during the report run")
            initial["findings"] = seed_findings
            initial["verdict"] = "REJECT" if unchanged else "ERROR"
            initial["verdict_reason"] = "; ".join(seed_findings)
            return initial, 3 if unchanged else 2
        if seed_findings:
            initial["seed"]["continued_despite_findings"] = True
            initial["seed"]["continue_authority"] = (
                "explicit --route-with-seed-findings; final report remains "
                "ineligible for a clean REVIEW verdict"
            )

        configured_tool_receipt = None
        if config is not None and not args.java and not args.freerouting_jar:
            try:
                try:
                    from kicad_autoroute_tools import default_cache, status
                except ImportError:
                    from .kicad_autoroute_tools import default_cache, status
                installed = status(default_cache(), require_valid=True)
            except (AutorouteError, OSError) as exc:
                raise RouteReportError(
                    "configured autorouting requires the verified installer receipt; "
                    "run kicad_autoroute_tools.py install --yes after authorization: %s"
                    % exc
                ) from exc
            args.java = installed["java"]
            args.freerouting_jar = installed["jar"]
            configured_tool_receipt = installed
        if config is not None:
            lock = json.loads(
                _read_utf8(Path(__file__).with_name("freerouting-tools-lock.json"))
            )
            args.router_sha256 = lock["freerouting"]["sha256"]
            args.expected_router_version = lock["freerouting"]["version"]

        java, java_version = find_java(args.java)
        router_jar, router_sha = resolve_router_jar(
            args.freerouting_jar,
            args.router_sha256,
            args.accept_unpinned_router,
        )
        initial["tools"].update(
            {
                "java": str(java),
                "java_version": java_version,
                "freerouting_jar": str(router_jar),
                "freerouting_sha256": router_sha,
            }
        )
        initial["tools"]["installer_receipt"] = configured_tool_receipt
        initial["tools"]["promotion_toolchain_eligible"] = bool(
            config is not None
            and configured_tool_receipt
            and configured_tool_receipt.get("promotion_integrity_pinned") is True
            and compatibility
            and compatibility["promotion_enabled"]
        )
        router_env, removed_router_env = _router_environment()
        initial["tools"]["router_environment"] = {
            "policy": (
                "ambient FREEROUTING* and Java option variables removed; "
                "LC_ALL=C; remaining environment inherited"
            ),
            "removed_variable_names": removed_router_env,
        }
        router_version, version_log = _router_version(
            java, router_jar, workspace, args.timeout_seconds, router_env
        )
        if args.expected_router_version and router_version != args.expected_router_version:
            raise RouteReportError(
                "Freerouting version mismatch: expected %s, detected %s"
                % (args.expected_router_version, router_version or "unknown")
            )
        initial["tools"].update(
            {
                "freerouting_version": router_version,
                "freerouting_version_probe": version_log,
            }
        )

        # Scope was validated before seed qualification so prepare-only reports
        # exercise the same DSN rules as an actual router run.  Derive
        # Freerouting's exclusion list from that proved board scope.

        derived_edge = _project_edge_clearance_um(project)
        if args.copper_edge_clearance_um is not None:
            copper_edge_um = _validate_positive(
                "--copper-edge-clearance-um",
                args.copper_edge_clearance_um,
                allow_zero=True,
            )
            edge_source = "explicit"
        elif derived_edge is not None:
            copper_edge_um = derived_edge
            edge_source = "project board.design_settings.rules.min_copper_edge_clearance"
        else:
            raise RouteReportError(
                "KiCad DSN omits copper-to-edge clearance and it could not be "
                "derived from the project. Pass --copper-edge-clearance-um."
            )
        initial["router_settings"] = {
            "max_passes": args.max_passes,
            "threads": args.threads,
            "timeout_seconds": args.timeout_seconds,
            "copper_edge_clearance_um": copper_edge_um,
            "copper_edge_clearance_source": edge_source,
            "automatic_neckdown": False,
            "fanout_enabled": False,
        }

        router_cmd = _router_command(
            java,
            router_jar,
            dsn,
            ses,
            workspace,
            scope["ignored_net_classes"],
            args.max_passes,
            args.threads,
            copper_edge_um,
        )
        if args.reuse_router_report:
            prior_path = Path(args.reuse_router_report).expanduser().resolve()
            try:
                prior = json.loads(_read_utf8(prior_path))
            except json.JSONDecodeError as exc:
                raise RouteReportError("reused router report is invalid JSON") from exc
            prior_workspace = Path(prior.get("workspace") or "").resolve()
            prior_ses = prior_workspace / ses.name
            prior_dsn = prior_workspace / dsn.name
            prior_dsn_valid = (
                prior_dsn.is_file()
                and digest(prior_dsn) == (prior.get("seed") or {}).get("dsn_sha256")
            )
            prior_dsn_semantic = (
                _dsn_semantic_sha256(prior_dsn) if prior_dsn_valid else None
            )
            checks = {
                "seed board": (
                    (prior.get("seed") or {}).get("board_sha256"),
                    initial["seed"]["board_sha256"],
                ),
                "scoped DSN": (
                    prior_dsn_semantic,
                    _dsn_semantic_sha256(dsn),
                ),
                "input bundle": (
                    (prior.get("configuration") or {}).get("input_bundle_sha256"),
                    initial["configuration"]["input_bundle_sha256"],
                ),
                "Freerouting JAR": (
                    (prior.get("tools") or {}).get("freerouting_sha256"),
                    router_sha,
                ),
                "Freerouting version": (
                    (prior.get("tools") or {}).get("freerouting_version"),
                    router_version,
                ),
                "router settings": (
                    prior.get("router_settings"), initial["router_settings"]
                ),
            }
            mismatches = [name for name, (old, new) in checks.items() if old != new]
            prior_router = prior.get("router_run") or {}
            if prior_router.get("returncode") != 0:
                mismatches.append("prior router completion")
            if prior.get("source", {}).get("unchanged") is not True:
                mismatches.append("prior source integrity")
            if not prior_dsn_valid:
                mismatches.append("retained DSN digest")
            if not prior_ses.is_file() or digest(prior_ses) != prior_router.get("ses_sha256"):
                mismatches.append("retained SES digest")
            if mismatches:
                raise RouteReportError(
                    "cannot reuse prior router evidence; mismatch in "
                    + ", ".join(mismatches)
                )
            shutil.copy2(prior_ses, ses)
            initial["router_run"] = {
                **prior_router,
                "reused": True,
                "prior_report": str(prior_path),
                "prior_report_sha256": digest(prior_path),
                "prior_workspace": str(prior_workspace),
                "reuse_checks": sorted(checks),
            }
        else:
            router_run = _run(
                router_cmd,
                cwd=workspace,
                timeout=args.timeout_seconds,
                env=router_env,
            )
            initial["router_run"] = _log_record(router_run)
            if router_run["returncode"] != 0:
                raise RouteReportError(
                    "Freerouting failed (rc=%d): %s"
                    % (router_run["returncode"], router_run["stderr"][-3000:])
                )
        if not ses.is_file() or ses.stat().st_size == 0:
            raise RouteReportError(
                "Freerouting exited 0 but did not write a non-empty SES"
            )
        initial["router_run"].update(
            {"ses_sha256": digest(ses), "ses_bytes": ses.stat().st_size}
        )
        fr_drc = workspace / "freerouting-drc.json"
        if fr_drc.is_file():
            initial["router_run"]["freerouting_drc_sha256"] = digest(fr_drc)
            initial["router_run"]["freerouting_drc_bytes"] = fr_drc.stat().st_size

        candidate_dir.mkdir()
        candidate_copied = _copy_sources(copied, candidate_dir)
        candidate_board = candidate_copied["board"]
        import_result = _worker_call(
            kicad_python,
            "import",
            [candidate_board, ses],
            import_snapshot_path,
            workspace,
        )
        if not import_result.get("import_ok"):
            raise RouteReportError("KiCad reported SES import failure")
        raw_candidate_board = candidate_board
        raw_candidate_snapshot = import_result["snapshot"]
        raw_delta = _route_delta(seed_snapshot, raw_candidate_snapshot)
        filtered_result = None
        filtered_apply = None
        if config is not None:
            # KiCad's SES importer replaces the scratch board's routing with
            # the routing carried by the SES; fixed DSN copper is generally
            # absent from that session file.  Therefore raw "removals" are an
            # interchange representation detail, not authority to remove seed
            # copper.  Only raw additions are allowlisted, then applied to a
            # fresh seed copy.  The protected-route report below proves that
            # this reconstructed candidate retained every seed primitive.
            try:
                filtered_result = filter_candidate_routes(
                    raw_delta["_added"], config, args._project_scope
                )
            except AutorouteError as exc:
                raise RouteReportError("candidate route geometry is not promotable: %s" % exc) from exc
            if not filtered_result["routes"]:
                raise RouteReportError("Freerouting produced no allowlisted route additions")
            candidate_board, filtered_apply = _apply_filtered_routes(
                kicad_python,
                copied,
                workspace,
                filtered_result["routes"],
            )
            control_board, control_apply = _apply_filtered_routes(
                kicad_python,
                copied,
                workspace,
                [],
                label="control-roundtrip",
            )
            control_nonrouting_sha = nonrouting_projection_sha256(control_board)
            filtered_nonrouting_sha = nonrouting_projection_sha256(candidate_board)
            filtered_apply["control_board_sha256"] = digest(control_board)
            filtered_apply["control_nonrouting_projection_sha256"] = control_nonrouting_sha
            filtered_apply["nonrouting_projection_sha256"] = filtered_nonrouting_sha
            filtered_apply["nonrouting_projection_unchanged"] = (
                control_nonrouting_sha == filtered_nonrouting_sha
            )
            filtered_apply["control_worker"] = {
                key: value for key, value in control_apply.items() if key != "identity_map"
            }
            filtered_snapshot_result = _worker_call(
                kicad_python,
                "snapshot",
                [candidate_board],
                workspace / "filtered-semantic.json",
                workspace,
            )
            candidate_snapshot = filtered_snapshot_result["snapshot"]
            delta = _route_delta(seed_snapshot, candidate_snapshot)
        else:
            candidate_snapshot = raw_candidate_snapshot
            delta = raw_delta
        scope = _scope_report(
            seed_snapshot,
            delta,
            args.allow_net_class,
            args.allow_all_net_classes,
        )
        layer_scope = _layer_scope_report(delta, requested_layers)
        locked = (
            _protected_route_report(seed_snapshot, candidate_snapshot)
            if config is not None
            else _locked_route_report(seed_snapshot, candidate_snapshot)
        )
        nonrouting_unchanged = (
            filtered_apply["nonrouting_projection_unchanged"]
            if config is not None
            else seed_snapshot["nonrouting_sha256"]
            == candidate_snapshot["nonrouting_sha256"]
        )
        nonrouting_changed_categories = sorted(
            key
            for key, value in seed_snapshot["nonrouting_category_sha256"].items()
            if candidate_snapshot["nonrouting_category_sha256"].get(key) != value
        )

        candidate_drc = _drc_snapshot(
            candidate_board,
            workspace / "candidate-drc.rpt",
            not args.no_schematic_parity,
            str(kicad_cli),
        )
        candidate_json_drc = None
        final_drc_problems = []
        if config is not None:
            if baseline is None:
                raise RouteReportError("configured run lost its loaded DRC baseline")
            candidate_json_drc = _run_json_drc(
                candidate_board,
                workspace / "candidate-drc.json",
                parity=not args.no_schematic_parity,
                cli=str(kicad_cli),
                identity_map=filtered_apply["identity_map"],
            )
            try:
                final_drc_problems = compare_drc(
                    candidate_json_drc["normalized"], baseline, final=True
                )
            except AutorouteError as exc:
                raise RouteReportError("final DRC baseline comparison failed: %s" % exc) from exc
            project_audits = _run_structured_audits(
                config["final"]["audit_commands"],
                board=candidate_board,
                workspace=workspace,
                config_dir=Path(config["config_dir"]),
                kicad_python=kicad_python,
            )
        else:
            candidate_before_audits_sha = digest(candidate_board)
            project_audits = _run_audit_commands(
                args.audit_command_json,
                candidate_board,
                workspace,
                args.audit_timeout_seconds,
            )
            candidate_after_audits_sha = digest(candidate_board)
            project_audits.update(
                {
                    "board_sha256_before": candidate_before_audits_sha,
                    "board_sha256_after": candidate_after_audits_sha,
                    "board_unchanged": (
                        candidate_before_audits_sha == candidate_after_audits_sha
                    ),
                }
            )
        initial["candidate"] = {
            "board_sha256": digest(candidate_board),
            "board_path": str(candidate_board),
            "raw_import": import_result.get("raw_import"),
            "zones_refilled": bool(import_result.get("zones_refilled")),
            "semantic": {
                "routing": candidate_snapshot["routing"]["summary"],
                "nonrouting_point_quantum_nm": candidate_snapshot[
                    "nonrouting_point_quantum_nm"
                ],
                "nonrouting_sha256": candidate_snapshot["nonrouting_sha256"],
                "nonrouting_category_sha256": candidate_snapshot[
                    "nonrouting_category_sha256"
                ],
            },
            "route_delta": _clean_delta_for_report(delta),
            "scope": scope,
            "layer_scope": layer_scope,
            "locked_routes": locked,
            "nonrouting_unchanged": nonrouting_unchanged,
            "nonrouting_coverage": (
                "control-roundtrip-s-expression-v1"
                if config is not None
                else "partial-unverified"
            ),
            "nonrouting_changed_categories": nonrouting_changed_categories,
            "drc": candidate_drc,
            "json_drc": candidate_json_drc,
            "drc_baseline_problems": final_drc_problems,
            "project_audits": project_audits,
            "import_worker": import_result["worker_log"],
        }
        if config is not None:
            initial["candidate"]["raw_candidate"] = {
                "board_path": str(raw_candidate_board),
                "board_sha256": digest(raw_candidate_board),
                "route_delta": _clean_delta_for_report(raw_delta),
            }
            initial["candidate"]["filtered"] = {
                "routes": filtered_result["routes"],
                "routes_sha256": canonical_json_sha256(filtered_result["routes"]),
                "discarded_drift_count": len(filtered_result["discarded_drift"]),
                "discarded_drift_examples": filtered_result["discarded_drift"][:50],
                "application": {
                    key: value
                    for key, value in filtered_apply.items()
                    if key != "identity_map"
                },
            }

        findings = list(seed_findings)
        if not nonrouting_unchanged:
            findings.append("non-routing semantic fingerprint changed")
        if locked["missing_count"] or locked["new_count"]:
            findings.append("locked routing changed")
        if scope["violations_count"]:
            findings.append("routing changed outside allowed net classes")
        if layer_scope["violations_count"]:
            findings.append("routing changed outside allowed copper layers")
        if config is None and candidate_drc["counts"].get("drc violations", 0):
            findings.append("candidate has DRC violations")
        if config is None and candidate_drc["counts"].get("footprint errors", 0):
            findings.append("candidate has footprint errors")
        findings.extend(final_drc_problems)
        if project_audits["failed"]:
            findings.append(
                "candidate failed %d/%d configured project audit command(s)"
                % (project_audits["failed"], project_audits["count"])
            )
        if not project_audits["board_unchanged"]:
            findings.append("a configured project audit command changed the candidate board")
        unconnected = candidate_drc["counts"].get("unconnected pads")
        if unconnected != args.expected_unconnected:
            findings.append(
                "candidate unconnected-pad count is %s, expected %d"
                % (unconnected, args.expected_unconnected)
            )

        unchanged, source_changes = _verify_source_unchanged(before_digests)
        initial["source"]["unchanged"] = unchanged
        initial["source"]["changes"] = source_changes
        if not unchanged:
            findings.append("a source artifact changed during the report run")

        bundle_unchanged = True
        bundle_error = None
        if config is not None:
            try:
                verify_input_bundle(input_bundle_root, input_bundle)
                if config.get("schema") == CONFIG_SCHEMA_V2:
                    live_bundle = build_v2_input_bundle(config)
                    if live_bundle != input_bundle:
                        raise AutorouteError(
                            "v2 input bundle membership changed during the run"
                        )
            except AutorouteError as exc:
                bundle_unchanged = False
                bundle_error = str(exc)
                findings.append("the hermetic autoroute input bundle changed during the run")
            initial["configuration"]["input_bundle_unchanged"] = bundle_unchanged
            initial["configuration"]["input_bundle_error"] = bundle_error

        promotion_blocks = []
        if config is not None and not args.exploratory:
            seed_audits = initial["seed"].get("project_audits") or {}
            routine_scope = (
                config.get("schema") == CONFIG_SCHEMA_V2
                and config["scope"].get("selected_scope_policy") == "routine"
            )
            if not routine_scope:
                if not seed_audits.get("configured") or not project_audits.get("configured"):
                    promotion_blocks.append("both seed and final project audits must be configured")
                if not project_audits.get("calibration_passed"):
                    promotion_blocks.append(
                        "final project audits must execute their tracked known-bad calibration"
                    )
            if not initial["tools"].get("promotion_toolchain_eligible"):
                promotion_blocks.append(
                    "installed tool receipt and an enabled exact compatibility cell are required"
                )
            if config.get("schema") == CONFIG_SCHEMA_V2:
                configured_applicator = config["tools"]["applicator"]
                applicators = [
                    item
                    for item in input_bundle
                    if item["path"] == configured_applicator["path"]
                    and item["sha256"] == configured_applicator["sha256"]
                ]
            else:
                applicators = [
                    item
                    for item in input_bundle
                    if item["role"] == "project-code:autoroute_apply.py"
                    and item["path"].endswith("autoroute_apply.py")
                ]
            if len(applicators) != 1:
                promotion_blocks.append(
                    "the hermetic bundle must contain exactly one configured manifest applicator"
                )
                applicator = None
            else:
                applicator = {
                    "schema_version": "1",
                    "bundle_path": applicators[0]["path"],
                    "source_sha256": applicators[0]["sha256"],
                }
            promotion_checks = {
                "source_unchanged": unchanged,
                "input_bundle_unchanged": bundle_unchanged,
                "nonrouting_unchanged": nonrouting_unchanged,
                "locked_routes_unchanged": not (
                    locked["missing_count"] or locked["new_count"]
                ),
                "structured_drc_baseline_passed": not final_drc_problems,
            }
            if routine_scope:
                promotion_checks["selected_scope_routine_declared"] = (
                    config["scope"]["selected_scope_policy"] == "routine"
                )
            else:
                promotion_checks.update({
                    "seed_project_audits_passed": (
                        seed_audits.get("failed") == 0
                        and seed_audits.get("board_unchanged") is True
                    ),
                    "final_project_audits_passed": (
                        project_audits.get("failed") == 0
                        and project_audits.get("board_unchanged") is True
                    ),
                })
            initial["promotion"] = {
                "seed_sha256": initial["seed"]["board_sha256"],
                "config_sha256": config["config_sha256"],
                "input_bundle": input_bundle,
                "input_bundle_sha256": canonical_json_sha256(input_bundle),
                "applicator": applicator,
                "toolchain": {
                    "backend": config["backend"],
                    "freerouting_version": router_version,
                    "freerouting_sha256": router_sha,
                    "java_version": java_version,
                    "install_receipt_sha256": (
                        configured_tool_receipt or {}
                    ).get("receipt_sha256"),
                    "compatibility_matrix_sha256": (
                        compatibility or {}
                    ).get("matrix_sha256"),
                    "compatibility_cell": {
                        key: (compatibility or {}).get(key)
                        for key in ("os", "arch", "kicad_cli", "pcbnew")
                    },
                },
                "scope": {
                    "net_classes": config["scope"]["net_classes"],
                    "resolved_nets": sorted(args._project_scope["net_to_class"]),
                    "net_to_class": dict(sorted(args._project_scope["net_to_class"].items())),
                    "layers": config["scope"]["layers"],
                    "styles": config["scope"]["styles"],
                },
                "raw_candidate_sha256": digest(raw_candidate_board),
                "review_candidate_sha256": digest(candidate_board),
                "routes": filtered_result["routes"],
                "routes_sha256": canonical_json_sha256(filtered_result["routes"]),
                "checks": promotion_checks,
                "blocks": promotion_blocks,
            }
            if config.get("schema") == CONFIG_SCHEMA_V2:
                initial["promotion"].update({
                    "selected_scope_policy": config["scope"]["selected_scope_policy"],
                    "seed_attestation": initial["seed"]["adapter_attestation"]["attestation"],
                })

        exit_code = _finalize_report(
            initial,
            findings=findings,
            promotion_blocks=promotion_blocks,
            args=args,
            config=config,
            project_audits=project_audits,
        )
        return initial, exit_code


def _point(value) -> list[int]:
    return [int(value.x), int(value.y)]


def _nonrouting_point(value) -> list[int]:
    def quantize(raw) -> int:
        value_nm = int(raw)
        return int(
            round(value_nm / NONROUTING_POINT_QUANTUM_NM)
            * NONROUTING_POINT_QUANTUM_NM
        )

    return [quantize(value.x), quantize(value.y)]


def _layers(item) -> list[int]:
    try:
        return [int(x) for x in item.GetLayerSet().Seq()]
    except Exception as exc:
        raise RouteReportError(
            "cannot enumerate layers for %s: %s" % (type(item).__name__, exc)
        ) from exc


def _optional_call(item, name, converter=lambda x: x):
    if not hasattr(item, name):
        return None
    try:
        return converter(getattr(item, name)())
    except Exception as exc:
        raise RouteReportError(
            "%s.%s failed: %s" % (type(item).__name__, name, exc)
        ) from exc


def _item_uuid(item) -> str:
    try:
        value = str(item.m_Uuid.AsString())
    except Exception as exc:
        raise RouteReportError(
            "cannot resolve UUID for %s: %s" % (type(item).__name__, exc)
        ) from exc
    if not value:
        raise RouteReportError("%s has an empty UUID" % type(item).__name__)
    return value


def _route_item(item, board, pcbnew) -> dict:
    common = {
        "net": str(item.GetNetname()),
        "locked": bool(item.IsLocked()),
    }
    if isinstance(item, pcbnew.PCB_VIA):
        common.update(
            {
                "kind": "via",
                # KiCad 10.0.5 exposes PCB_VIA.GetWidth() but its no-argument
                # overload raises SystemError; GetFrontWidth() is the working
                # diameter accessor for an ordinary through/blind/micro via.
                "width_nm": int(item.GetFrontWidth()),
                "position_nm": _point(item.GetPosition()),
                "top_layer": board.GetLayerName(int(item.TopLayer())),
                "bottom_layer": board.GetLayerName(int(item.BottomLayer())),
                "drill_nm": int(item.GetDrillValue()),
                "via_type": int(item.GetViaType()),
            }
        )
    elif isinstance(item, pcbnew.PCB_ARC):
        arc_ends = sorted((_point(item.GetStart()), _point(item.GetEnd())))
        common.update(
            {
                "kind": "arc",
                "width_nm": int(item.GetWidth()),
                "layer": str(item.GetLayerName()),
                # Routing primitives are geometrically undirected.  KiCad or
                # an SES round-trip may swap start/end without moving copper;
                # canonical endpoint order avoids reporting that as removal
                # plus addition.
                "start_nm": arc_ends[0],
                "mid_nm": _point(item.GetMid()),
                "end_nm": arc_ends[1],
                "length_nm": int(round(item.GetLength())),
            }
        )
    elif isinstance(item, pcbnew.PCB_TRACK):
        segment_ends = sorted((_point(item.GetStart()), _point(item.GetEnd())))
        common.update(
            {
                "kind": "segment",
                "width_nm": int(item.GetWidth()),
                "layer": str(item.GetLayerName()),
                "start_nm": segment_ends[0],
                "end_nm": segment_ends[1],
                "length_nm": int(round(item.GetLength())),
            }
        )
    else:
        raise RouteReportError(
            "unsupported routing object %s; refusing an incomplete snapshot"
            % type(item).__name__
        )
    return common


def _pad_item(pad) -> dict:
    return {
        "number": str(pad.GetNumber()),
        "net": str(pad.GetNetname()),
        "position_nm": _nonrouting_point(pad.GetPosition()),
        "size_nm": _nonrouting_point(pad.GetSize()),
        "drill_nm": _nonrouting_point(pad.GetDrillSize()),
        "shape": int(pad.GetShape()),
        "orientation_deg": round(float(pad.GetOrientationDegrees()), 9),
        "layers": _layers(pad),
        "locked": bool(pad.IsLocked()),
    }


def _footprint_item(fp) -> dict:
    pads = sorted(
        (_pad_item(pad) for pad in fp.Pads()),
        key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")),
    )
    graphics = sorted(
        (_drawing_item(item) for item in fp.GraphicalItems()),
        key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")),
    )
    return {
        "uuid": _item_uuid(fp),
        "reference": str(fp.GetReference()),
        # str(LIB_ID) is the SWIG proxy repr and contains a process-specific
        # pointer, so it makes identical boards hash differently.  The
        # UniString form is the stable "Library:Footprint" identity.
        "fpid": str(fp.GetFPID().GetUniStringLibId()),
        "position_nm": _nonrouting_point(fp.GetPosition()),
        "orientation_deg": round(float(fp.GetOrientationDegrees()), 9),
        "flipped": bool(fp.IsFlipped()),
        "locked": bool(fp.IsLocked()),
        "attributes": int(fp.GetAttributes()),
        "pads": pads,
        # PCB_SHAPE coordinates returned by KiCad for footprint graphics are
        # transformed into board space.  Keeping them here makes a moved,
        # rotated, mirrored, opened, or replaced footprint-hosted Edge.Cuts
        # contour part of the nonrouting digest.
        "graphics": graphics,
    }


def _zone_item(zone) -> dict:
    # Corner enumeration can rotate or reverse after a harmless KiCad save.
    # This v1 invariant is a vertex multiset; project-specific zone audits must
    # still compare polygon/fill topology before promotion.
    corners = sorted(
        _nonrouting_point(zone.GetCornerPosition(i))
        for i in range(zone.GetNumCorners())
    )
    return {
        "name": str(zone.GetZoneName()),
        "net": str(zone.GetNetname()),
        "layers": _layers(zone),
        "priority": int(zone.GetAssignedPriority()),
        "rule_area": bool(zone.GetIsRuleArea()),
        "locked": bool(zone.IsLocked()),
        "min_thickness_nm": int(zone.GetMinThickness()),
        "corners_nm": corners,
    }


def _drawing_item(item) -> dict:
    data = {
        "uuid": _item_uuid(item),
        "kind": type(item).__name__,
        "layers": _layers(item),
    }
    converters = {
        "GetPosition": _nonrouting_point,
        "GetStart": _nonrouting_point,
        "GetEnd": _nonrouting_point,
        "GetWidth": int,
        "GetShape": int,
        "GetText": str,
        "GetTextSize": _point,
        "GetTextAngleDegrees": lambda x: round(float(x), 9),
        "IsLocked": bool,
    }
    for method, converter in converters.items():
        value = _optional_call(item, method, converter)
        if value is not None:
            data[method] = value
    if "GetStart" in data and "GetEnd" in data:
        data["GetStart"], data["GetEnd"] = sorted(
            (data["GetStart"], data["GetEnd"])
        )
    return data


def _semantic_snapshot(board, pcbnew) -> dict:
    routes = [_route_item(item, board, pcbnew) for item in board.GetTracks()]
    routes.sort(key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")))
    locked = [x for x in routes if x["locked"]]
    footprints = sorted(
        (_footprint_item(fp) for fp in board.GetFootprints()),
        key=lambda x: (x["reference"], json.dumps(x, sort_keys=True)),
    )
    zones = sorted(
        (_zone_item(z) for z in board.Zones()),
        key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")),
    )
    drawings = sorted(
        (_drawing_item(d) for d in board.GetDrawings()),
        key=lambda x: json.dumps(x, sort_keys=True, separators=(",", ":")),
    )
    nets = {}
    class_names = set(str(x) for x in board.GetAllNetClasses().keys())
    for net in board.GetNetInfo().NetsByName().values():
        name = str(net.GetNetname())
        if not name:
            continue
        class_name = str(net.GetNetClassName())
        nets[name] = class_name
        class_names.add(class_name)

    by_kind = collections.Counter(x["kind"] for x in routes)
    by_net = collections.Counter(x["net"] for x in routes)
    total_length = sum(int(x.get("length_nm", 0)) for x in routes)
    nonrouting = {
        "board": {
            "copper_layer_count": int(board.GetCopperLayerCount()),
            "copper_layers": [
                str(board.GetLayerName(layer))
                for layer in range(64)
                if board.IsLayerEnabled(layer) and pcbnew.IsCopperLayer(layer)
            ],
            "enabled_layers": [int(x) for x in board.GetEnabledLayers().Seq()],
        },
        "footprints": footprints,
        "zones": zones,
        "drawings": drawings,
    }
    nonrouting_category_sha256 = {
        key: _json_digest(value) for key, value in nonrouting.items()
    }
    return {
        "schema": SNAPSHOT_SCHEMA,
        "board": nonrouting["board"],
        "netclasses": {
            "class_names": sorted(class_names),
            "net_to_class": dict(sorted(nets.items())),
        },
        "routing": {
            "items": routes,
            "locked_items": locked,
            "summary": {
                "count": len(routes),
                "by_kind": dict(sorted(by_kind.items())),
                "by_net": dict(sorted(by_net.items())),
                "total_track_length_mm": round(total_length / 1_000_000.0, 6),
                "sha256": _json_digest(routes),
                "locked_count": len(locked),
                "locked_sha256": _json_digest(locked),
            },
        },
        "nonrouting_sha256": _json_digest(nonrouting),
        "nonrouting_point_quantum_nm": NONROUTING_POINT_QUANTUM_NM,
        "nonrouting_category_sha256": nonrouting_category_sha256,
        # Kept in the scratch semantic file for diagnosis.  The public report
        # carries only hashes/counts so a large board does not produce a
        # multi-megabyte report.
        "nonrouting_items": nonrouting,
        "nonrouting_counts": {
            "footprints": len(footprints),
            "zones": len(zones),
            "drawings": len(drawings),
        },
    }


def _init_pcbnew():
    try:
        import wx
        wx.Log.SetLogLevel(wx.LOG_Error)
        app = wx.AppConsole()
        import pcbnew
    except Exception as exc:
        raise RouteReportError("cannot initialize KiCad console runtime: %s" % exc) from exc
    # wx.App(False) is a GUI app and hangs or degrades GetSettingsManager() to
    # a raw SwigPyObject in headless KiCad 10.0.5/Darwin.  AppConsole provides
    # wx standard paths while preserving the typed SETTINGS_MANAGER API.
    return app, pcbnew


def _load_board_with_project(board_path: Path, pcbnew):
    """Load a scratch board and apply same-stem project net-class settings.

    Net-class assignments live in ``.kicad_pro``, not ``.kicad_pcb``.  Calling
    LoadBoard alone therefore maps every net to Default and turns a scoped
    Freerouting run into a whole-board run.  Attach the scratch project and
    synchronize before snapshotting or exporting DSN.
    """
    board = pcbnew.LoadBoard(str(board_path))
    project_path = board_path.with_suffix(".kicad_pro")
    manager = None
    project = None
    if project_path.is_file():
        manager = pcbnew.GetSettingsManager()
        if not manager.LoadProject(str(project_path)):
            raise RouteReportError("KiCad could not load scratch project %s" % project_path)
        project = manager.GetProject(str(project_path))
        if project is None:
            raise RouteReportError(
                "KiCad loaded %s but returned no PROJECT object" % project_path
            )
        board.SetProject(project)
        board.SynchronizeNetsAndNetClasses(False)
    return board, manager, project


def _pcb_worker(argv: list[str]) -> int:
    if len(argv) not in (3, 4, 5):
        raise RouteReportError(
            "internal worker usage: export BOARD DSN SNAPSHOT or "
            "import BOARD SES SNAPSHOT or snapshot BOARD SNAPSHOT"
        )
    mode = argv[0]
    worker_root_raw = os.environ.get("KICAD_ROUTE_WORKER_ROOT")
    if not worker_root_raw:
        raise RouteReportError(
            "internal PCB worker requires an orchestrator workspace boundary"
        )
    worker_root = Path(worker_root_raw).resolve()
    for raw_path in argv[1:]:
        path = Path(raw_path).resolve()
        try:
            path.relative_to(worker_root)
        except ValueError as exc:
            raise RouteReportError(
                "internal PCB worker path escapes workspace %s: %s"
                % (worker_root, path)
            ) from exc
    app, pcbnew = _init_pcbnew()
    # Keep the wx application referenced for the entire worker.  Releasing it
    # before ExportSpecctraDSN/ImportSpecctraSES recreates the standard-path
    # assertion this worker exists to avoid.
    _ = app
    if mode == "export" and len(argv) == 4:
        board_path, dsn_path, snapshot_path = map(Path, argv[1:])
        board, manager, project = _load_board_with_project(board_path, pcbnew)
        _ = (manager, project)
        # The source board remains untouched, but every existing scratch route
        # is fixed for DSN export.  Freerouting is permitted to add routine
        # copper, never to optimize or rip up generator-authored copper.
        for item in board.GetTracks():
            item.SetLocked(True)
        snapshot = _semantic_snapshot(board, pcbnew)
        ok = bool(pcbnew.ExportSpecctraDSN(board, str(dsn_path)))
        _write_json_atomic(
            snapshot_path,
            {
                "mode": mode,
                "pcbnew_version": pcbnew.GetBuildVersion(),
                "export_ok": ok,
                "snapshot": snapshot,
            },
        )
        return 0 if ok else 2
    if mode == "import" and len(argv) == 4:
        board_path, ses_path, snapshot_path = map(Path, argv[1:])
        board, manager, project = _load_board_with_project(board_path, pcbnew)
        ok = bool(pcbnew.ImportSpecctraSES(board, str(ses_path)))
        raw_import = None
        if ok:
            pcbnew.SaveBoard(str(board_path), board)
            raw_path = board_path.with_name(
                board_path.stem + "-raw-import" + board_path.suffix
            )
            shutil.copy2(board_path, raw_path)
            raw_import = {
                "path": str(raw_path),
                "sha256": digest(raw_path),
            }
            # Zone fills in the seed predate the SES routes.  DRC against those
            # stale polygons can report false collisions and, conversely, miss
            # islands/clearance created by the new routing.  The candidate
            # snapshot and every downstream DRC must see the refilled board.
            filler = pcbnew.ZONE_FILLER(board)
            filler.Fill(board.Zones())
            pcbnew.SaveBoard(str(board_path), board)
            board = pcbnew.LoadBoard(str(board_path))
            if project is not None:
                board.SetProject(project)
                board.SynchronizeNetsAndNetClasses(False)
        _ = manager
        snapshot = _semantic_snapshot(board, pcbnew)
        _write_json_atomic(
            snapshot_path,
            {
                "mode": mode,
                "pcbnew_version": pcbnew.GetBuildVersion(),
                "import_ok": ok,
                "zones_refilled": bool(ok),
                "raw_import": raw_import,
                "snapshot": snapshot,
            },
        )
        return 0 if ok else 2
    if mode == "snapshot" and len(argv) == 3:
        board_path, snapshot_path = map(Path, argv[1:])
        board, manager, project = _load_board_with_project(board_path, pcbnew)
        _ = (manager, project)
        snapshot = _semantic_snapshot(board, pcbnew)
        _write_json_atomic(
            snapshot_path,
            {
                "mode": mode,
                "pcbnew_version": pcbnew.GetBuildVersion(),
                "snapshot": snapshot,
            },
        )
        return 0
    raise RouteReportError("unknown or malformed internal worker mode: %r" % mode)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run Freerouting only on a scratch KiCad board and write a JSON "
            "change report. This tool cannot promote or approve routes."
        )
    )
    parser.add_argument("board", help="source .kicad_pcb (never written)")
    parser.add_argument("--report", required=True, help="JSON report path")
    parser.add_argument(
        "--config",
        help="tracked kicad-autoroute-config-v1/v2; supplies immutable scope and safety policy",
    )
    parser.add_argument(
        "--prepare-only",
        action="store_true",
        help="export DSN and run seed preflight without requiring or invoking Freerouting",
    )
    parser.add_argument(
        "--exploratory",
        action="store_true",
        help=(
            "run a non-promotable routing scout for placement, congestion, and "
            "corridor evidence; generated geometry is inspiration only"
        ),
    )
    parser.add_argument("--freerouting-jar")
    parser.add_argument("--router-sha256")
    parser.add_argument("--expected-router-version")
    parser.add_argument(
        "--accept-unpinned-router",
        action="store_true",
        help="explicitly allow an unpinned JAR for this disposable report run",
    )
    parser.add_argument("--java")
    parser.add_argument("--kicad-python")
    parser.add_argument("--kicad-cli")
    parser.add_argument(
        "--allow-net-class",
        action="append",
        default=[],
        help="net class Freerouting may change; repeat for more classes",
    )
    parser.add_argument(
        "--allow-all-net-classes",
        action="store_true",
        help="explicitly permit a whole-board report candidate",
    )
    parser.add_argument(
        "--allow-layer",
        action="append",
        default=[],
        help=(
            "copper layer selected net classes may use (for example F.Cu); "
            "repeat for more layers"
        ),
    )
    parser.add_argument("--max-passes", type=int)
    parser.add_argument("--threads", type=int)
    parser.add_argument("--timeout-seconds", type=int)
    parser.add_argument("--audit-timeout-seconds", type=int)
    parser.add_argument("--expected-unconnected", type=int, default=0)
    parser.add_argument("--copper-edge-clearance-um", type=int)
    parser.add_argument(
        "--no-schematic-parity",
        action="store_true",
        help="explicitly permit operation without same-stem project/schematic parity",
    )
    parser.add_argument(
        "--keep-workspace",
        help="new directory in which to retain scratch DSN/SES/logs",
    )
    parser.add_argument(
        "--reuse-router-report",
        help=(
            "legacy diagnostic option; retained SES evidence is never eligible "
            "for configured/promotable runs"
        ),
    )
    parser.add_argument(
        "--fail-on-findings",
        action="store_true",
        help="exit 3 when a complete report rejects the candidate",
    )
    parser.add_argument(
        "--route-with-seed-findings",
        action="store_true",
        help=(
            "explicitly run a disposable experiment despite seed DRC findings; "
            "the findings remain in the final report and prevent REVIEW"
        ),
    )
    parser.add_argument(
        "--audit-command-json",
        action="append",
        default=[],
        help=(
            "project audit argv as a JSON string array containing a standalone "
            "{board} token; repeat for multiple fail-closed audits"
        ),
    )
    parser.add_argument(
        "--create-seed-drc-baseline",
        action="store_true",
        help="write the config's missing seed baseline and stop; never overwrite one",
    )
    parser.add_argument(
        "--replace-seed-drc-baseline",
        action="store_true",
        help=(
            "explicitly replace the tracked seed baseline after an intentional "
            "seed-scope change, then stop for disposition review"
        ),
    )
    return parser


def _configure_args(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    legacy = {
        "max_passes": 5,
        "threads": 1,
        "timeout_seconds": 600,
        "audit_timeout_seconds": 300,
    }
    if args.exploratory and args.prepare_only:
        parser.error("--exploratory cannot be combined with --prepare-only")
    if not args.config:
        if args.replace_seed_drc_baseline:
            parser.error("--replace-seed-drc-baseline requires a tracked --config")
        if args.reuse_router_report:
            parser.error("--reuse-router-report requires a tracked --config")
        for name, default in legacy.items():
            if getattr(args, name) is None:
                setattr(args, name, default)
        if not args.prepare_only and not args.exploratory:
            parser.error(
                "unconfigured router runs require --exploratory; promotable runs "
                "require a tracked --config"
            )
        if args.exploratory:
            if not args.allow_all_net_classes and not args.allow_net_class:
                parser.error(
                    "exploratory runs require --allow-net-class or the explicit "
                    "--allow-all-net-classes override"
                )
            if not args.allow_layer:
                parser.error(
                    "exploratory runs require at least one explicit --allow-layer"
                )
        args._autoroute_config = None
        args._project_scope = None
        return
    try:
        config = load_config(args.config)
    except AutorouteError as exc:
        parser.error(str(exc))
    if args.allow_net_class or args.allow_all_net_classes or args.allow_layer:
        parser.error("--config owns net-class and layer scope; CLI may not broaden it")
    if args.audit_command_json:
        parser.error("--config owns structured audit commands")
    if args.accept_unpinned_router:
        parser.error("configured runs cannot use --accept-unpinned-router")
    if args.create_seed_drc_baseline and args.replace_seed_drc_baseline:
        parser.error("choose create or replace seed DRC baseline, not both")
    if (args.create_seed_drc_baseline or args.replace_seed_drc_baseline) and not args.prepare_only:
        parser.error("seed DRC baseline writes require --prepare-only")
    if args.reuse_router_report:
        parser.error(
            "configured/promotable runs cannot reuse SES evidence; run Freerouting again"
        )
    board = Path(args.board).expanduser().resolve()
    project = board.with_suffix(".kicad_pro")
    if not project.is_file():
        parser.error("configured autorouting requires a same-stem .kicad_pro")
    if config.get("schema") == CONFIG_SCHEMA_V2:
        expected_project_name = Path(config["project"]["project_file"]).name
        if project.name != expected_project_name:
            parser.error(
                "v2 seed project sidecar basename differs from project.project_file"
            )
        if config["project"]["schematic_authority"] == "board-only":
            if board.with_suffix(".kicad_sch").exists():
                parser.error(
                    "board-only seed unexpectedly contains a same-stem schematic"
                )
            args.no_schematic_parity = True
    try:
        project_scope = resolve_project_netclasses(
            project,
            config["scope"]["net_classes"],
            expected_mapping=(
                config["scope"]["net_to_class"]
                if config.get("schema") == CONFIG_SCHEMA_V2
                else None
            ),
        )
        verify_project_styles(config, project_scope)
    except AutorouteError as exc:
        parser.error(str(exc))
    args.allow_net_class = list(config["scope"]["net_classes"])
    args.allow_layer = list(config["scope"]["layers"])
    for arg_name, config_name in (
        ("max_passes", "max_passes"),
        ("threads", "max_threads"),
        ("timeout_seconds", "timeout_seconds"),
        ("audit_timeout_seconds", "audit_timeout_seconds"),
    ):
        configured = config["limits"][config_name]
        requested = getattr(args, arg_name)
        setattr(args, arg_name, configured if requested is None else min(requested, configured))
    args._autoroute_config = config
    args._project_scope = project_scope


def _configured_input_bundle(
    args: argparse.Namespace, related: dict[str, Path]
) -> tuple[Path, list[dict]] | tuple[None, None]:
    config = getattr(args, "_autoroute_config", None)
    if config is None:
        return None, None
    if config.get("schema") == CONFIG_SCHEMA_V2:
        try:
            return Path(config["project_root"]).resolve(), build_v2_input_bundle(config)
        except AutorouteError as exc:
            raise RouteReportError(str(exc)) from exc
    root = Path(config["config_dir"]).resolve()
    entries: dict[str, Path] = {
        "autoroute-config": Path(config["config_path"]),
        # The board is the generated route seed and has its own exact digest
        # in promotion evidence.  Including it here would make the manifest
        # circular: applying accepted routes changes the generated board.
        **{
            f"project-{role}": path
            for role, path in related.items()
            if role != "board"
        },
    }
    baseline = config_path(config, config["seed"]["drc_baseline"])
    if baseline.exists():
        entries["drc-baseline"] = baseline
    for index, relative in enumerate(config["inputs"]):
        entries[f"declared-input-{index}"] = config_path(config, relative)
    for phase in ("seed", "final"):
        for index, audit in enumerate(config[phase]["audit_commands"]):
            token = audit["argv"][0]
            token = token.replace("{config_dir}", str(root))
            if "{" in token:
                continue
            script = Path(token)
            if not script.is_absolute():
                script = root / script
            if script.is_file():
                entries[f"{phase}-audit-{index}"] = script
    # Generated PCB projects in this skill keep their source next to the
    # tracked config.  Bind all top-level Python sources so changing the
    # generator or vendored applicator invalidates the manifest.
    for script in sorted(root.glob("*.py")):
        entries[f"project-code:{script.name}"] = script
    try:
        return root, build_input_bundle(root, entries)
    except AutorouteError as exc:
        raise RouteReportError(str(exc)) from exc


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--_pcb-worker"]:
        try:
            return _pcb_worker(argv[1:])
        except Exception as exc:
            print("kicad_route_candidate worker: %s" % exc, file=sys.stderr)
            return 2

    parser = _parser()
    args = parser.parse_args(argv)
    _configure_args(args, parser)
    if args.allow_all_net_classes and args.allow_net_class:
        parser.error("use --allow-net-class or --allow-all-net-classes, not both")
    board_path = Path(args.board).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    # This guard must run outside the catch-and-report block.  A collision
    # discovered inside run_report() would otherwise be caught and the error
    # JSON would then overwrite the very source artifact we meant to protect.
    try:
        prewrite_sources = _related_sources(board_path, no_parity=True)
    except RouteReportError as exc:
        parser.error(str(exc))
    try:
        bundle_root, input_bundle = _configured_input_bundle(args, prewrite_sources)
    except RouteReportError as exc:
        parser.error(str(exc))
    args._input_bundle_root = bundle_root
    args._input_bundle = input_bundle
    colliding_sources = set()
    protected_source_dirs = set()
    for source in prewrite_sources.values():
        if source.is_file():
            colliding_sources.add(source.resolve())
        elif source.is_dir():
            protected_source_dirs.add(source.resolve())
            colliding_sources.update(
                child.resolve() for child in source.rglob("*") if child.is_file()
            )
    if bundle_root is not None:
        colliding_sources.update(
            (bundle_root / item["path"]).resolve() for item in input_bundle
        )
    config = getattr(args, "_autoroute_config", None)
    if config is not None and config.get("schema") == CONFIG_SCHEMA_V2:
        root = Path(config["project_root"])
        for declaration in config["sources"]:
            if declaration["kind"] == "directory-recursive":
                protected_source_dirs.add((root / declaration["path"]).resolve())

    targets = [("report", report_path)]
    if args.keep_workspace:
        targets.append(("workspace", Path(args.keep_workspace).expanduser().resolve()))
    for label, target in targets:
        if target in colliding_sources or any(
            target == directory
            or target.is_relative_to(directory)
            or (label == "workspace" and directory.is_relative_to(target))
            for directory in protected_source_dirs
        ):
            parser.error(f"{label} path overlaps an immutable source: {target}")
    report = {
        "schema": REPORT_SCHEMA,
        "mode": _report_mode(args),
        "verdict": "ERROR",
    }
    exit_code = 2
    try:
        report, exit_code = run_report(args)
    except Exception as exc:
        partial = getattr(args, "_partial_report", None)
        if isinstance(partial, dict):
            report = partial
            before = (report.get("source") or {}).get("digests_before")
            if isinstance(before, dict):
                try:
                    unchanged, changes = _verify_source_unchanged(before)
                    report["source"]["unchanged"] = unchanged
                    report["source"]["changes"] = changes
                except Exception as drift_exc:
                    report["source"]["unchanged"] = None
                    report["source"]["digest_check_error"] = str(drift_exc)
        report.update(
            {
                "created_utc": time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ", time.gmtime()
                ),
                "error_type": type(exc).__name__,
                "error": str(exc),
                "verdict": "ERROR",
            }
        )
        print("kicad_route_candidate: %s" % exc, file=sys.stderr)
    try:
        _write_json_atomic(report_path, report)
    except Exception as exc:
        print("cannot write report %s: %s" % (report_path, exc), file=sys.stderr)
        return 2

    print("%s: %s" % (report.get("verdict", "ERROR"), report_path))
    reason = report.get("verdict_reason") or report.get("error")
    if reason:
        print(reason)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
