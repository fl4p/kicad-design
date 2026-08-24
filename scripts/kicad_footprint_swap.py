#!/usr/bin/env python3
"""Run a bounded, adapter-owned incremental footprint migration transaction."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import uuid
from typing import AbstractSet, Optional


SPEC_SCHEMA = "kicad-footprint-swap-spec-v1"
REQUEST_SCHEMA = "kicad-footprint-swap-request-v1"
RESULT_SCHEMA = "kicad-footprint-swap-adapter-result-v1"
EVIDENCE_SCHEMA = "kicad-footprint-swap-evidence-v1"
REPORT_SCHEMA = "kicad-footprint-swap-report-v1"
JOURNAL_SCHEMA = "kicad-footprint-swap-journal-v1"
_HEX = frozenset("0123456789abcdef")


class SwapError(RuntimeError):
    status = "verification_failed"


class TimeBudgetExceeded(SwapError):
    status = "time_budget_exceeded"


class ConcurrentChange(SwapError):
    status = "concurrent_change"


class RecoveryRequired(SwapError):
    status = "recovery_required"


class Deadline:
    def __init__(self, seconds: float):
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            raise SwapError("time budget must be positive")
        self.started = time.monotonic()
        self.ends = self.started + float(seconds)

    def remaining(self, reserve: float = 0.0) -> float:
        value = self.ends - time.monotonic() - reserve
        if value <= 0:
            raise TimeBudgetExceeded("transaction time budget exhausted")
        return value

    def timeout(self, cap: Optional[float] = None, reserve: float = 0.0) -> float:
        value = self.remaining(reserve)
        return value if cap is None else min(value, float(cap))

    def elapsed(self) -> float:
        return time.monotonic() - self.started

    def check(self, reserve: float = 0.0) -> None:
        self.remaining(reserve)


def _strict(
    value: object,
    where: str,
    required: AbstractSet[str],
    optional: AbstractSet[str] = frozenset(),
):
    if not isinstance(value, dict):
        raise SwapError(f"{where} must be an object")
    keys = set(value)
    missing = set(required) - keys
    extra = keys - set(required) - set(optional)
    if missing or extra:
        raise SwapError(
            f"{where} fields differ: missing={sorted(missing)}, extra={sorted(extra)}"
        )
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _identity(path: Path) -> dict:
    stat = path.stat()
    return {
        "sha256": _sha256(path),
        "device": stat.st_dev,
        "inode": stat.st_ino,
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _valid_digest(value) -> bool:
    return isinstance(value, str) and len(value) == 64 and set(value) <= _HEX


def _fsync_dir(path: Path) -> None:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _write_json_durable(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    payload = (json.dumps(value, sort_keys=True, indent=2) + "\n").encode()
    with temporary.open("wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    _fsync_dir(path.parent)


def _copy_durable(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.name}.{uuid.uuid4().hex}.tmp")
    with source.open("rb") as incoming, temporary.open("wb") as outgoing:
        shutil.copyfileobj(incoming, outgoing)
        outgoing.flush()
        os.fsync(outgoing.fileno())
    os.replace(temporary, destination)
    _fsync_dir(destination.parent)


def _resolve_under(root: Path, value: str, where: str, must_exist=False) -> Path:
    if not isinstance(value, str) or not value:
        raise SwapError(f"{where} must be a nonempty path")
    path = Path(value)
    path = path if path.is_absolute() else root / path
    path = path.resolve()
    if path != root and root not in path.parents:
        raise SwapError(f"{where} escapes project root: {path}")
    if must_exist and not path.is_file():
        raise SwapError(f"{where} does not exist: {path}")
    return path


def _load_spec(path: Path) -> tuple[Path, dict]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SwapError(f"cannot read footprint-swap spec: {exc}") from exc
    _strict(
        value,
        "spec",
        {"schema", "adapter_argv", "targets", "substitutions", "promotion_destinations"},
        {"journal", "lock", "report", "adapter_timeout_seconds"},
    )
    if value["schema"] != SPEC_SCHEMA:
        raise SwapError(f"unsupported spec schema {value['schema']!r}")
    if not isinstance(value["adapter_argv"], list) or not value["adapter_argv"] or not all(
        isinstance(item, str) and item for item in value["adapter_argv"]
    ):
        raise SwapError("spec.adapter_argv must be a nonempty argv array")
    if not isinstance(value["targets"], list) or not value["targets"]:
        raise SwapError("spec.targets must be a nonempty array")
    target_names = set()
    for index, target in enumerate(value["targets"]):
        _strict(
            target, f"spec.targets[{index}]",
            {"name", "board", "schematic", "project"},
            {"rules", "variant", "field_updates"},
        )
        if not isinstance(target["name"], str) or not target["name"] or target["name"] in target_names:
            raise SwapError("target names must be unique nonempty strings")
        for field in ("board", "schematic", "project"):
            if not isinstance(target[field], str) or not target[field]:
                raise SwapError(f"target {target['name']} {field} must be a nonempty path")
        if "rules" in target and target["rules"] is not None and (
            not isinstance(target["rules"], str) or not target["rules"]
        ):
            raise SwapError(f"target {target['name']} rules must be a path or null")
        if "field_updates" in target and not isinstance(target["field_updates"], dict):
            raise SwapError(f"target {target['name']} field_updates must be an object")
        target_names.add(target["name"])
    if not isinstance(value["promotion_destinations"], list) or not value["promotion_destinations"] or not all(
        isinstance(item, str) and item for item in value["promotion_destinations"]
    ):
        raise SwapError("spec.promotion_destinations must be a nonempty path array")
    if len(set(value["promotion_destinations"])) != len(value["promotion_destinations"]):
        raise SwapError("spec.promotion_destinations contains duplicates")
    if not isinstance(value["substitutions"], list) or not value["substitutions"]:
        raise SwapError("spec.substitutions must be a nonempty array")
    for index, substitution in enumerate(value["substitutions"]):
        _strict(
            substitution,
            f"spec.substitutions[{index}]",
            {"reference", "old_footprint", "new_footprint"},
            {"placements", "properties"},
        )
    return path.parent.resolve(), value


def _authority_paths(root: Path, spec_path: Path, spec: dict) -> set[Path]:
    paths = {spec_path.resolve()}
    for target in spec["targets"]:
        for field in ("board", "schematic", "project", "rules"):
            value = target.get(field)
            if value is not None:
                paths.add(_resolve_under(
                    root, value, f"target {target['name']} {field}", True
                ))
    return paths


def _snapshots(paths) -> dict[Path, dict]:
    return {path: _identity(path) for path in paths}


def _verify_snapshots(snapshots: dict[Path, dict]) -> None:
    for path, expected in snapshots.items():
        if not path.is_file() or _identity(path) != expected:
            raise ConcurrentChange(f"authority input changed during transaction: {path}")


def _check_kicad_locks(paths) -> None:
    active = set()
    for path in paths:
        if path.suffix not in {".kicad_pcb", ".kicad_sch", ".kicad_pro"}:
            continue
        for candidate in (
            path.with_name(f"~{path.name}.lck"),
            path.with_name(f"~{path.stem}.lck"),
            path.with_name(f"{path.stem}.lck"),
        ):
            if candidate.exists():
                active.add(candidate)
    if active:
        raise SwapError("active KiCad lock: " + ", ".join(map(str, sorted(active))))


def _acquire_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    for _attempt in range(2):
        try:
            fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            break
        except FileExistsError as exc:
            try:
                text = path.read_text(encoding="utf-8").strip()
                pid = int(text.removeprefix("pid="))
                os.kill(pid, 0)
            except (OSError, ValueError):
                path.unlink(missing_ok=True)
                _fsync_dir(path.parent)
                continue
            raise RecoveryRequired(
                f"live footprint-swap transaction lock exists for pid {pid}: {path}"
            ) from exc
    else:
        raise RecoveryRequired(f"could not recover stale transaction lock: {path}")
    os.write(fd, f"pid={os.getpid()}\n".encode())
    os.fsync(fd)
    _fsync_dir(path.parent)
    return fd


def _release_lock(path: Path, fd) -> None:
    os.close(fd)
    try:
        path.unlink()
        _fsync_dir(path.parent)
    except FileNotFoundError:
        pass


def _run_adapter(
    argv, request_path: Path, result_path: Path, deadline: Deadline,
    cap=None, cwd=None,
):
    command = [*argv, "--request", str(request_path), "--result", str(result_path)]
    environment = {
        "LC_ALL": "C",
        "LANG": "C",
        "PATH": os.defpath,
        "PYTHONNOUSERSITE": "1",
    }
    for name in ("HOME", "TMPDIR", "TMP", "TEMP", "SYSTEMROOT", "WINDIR"):
        if name in os.environ:
            environment[name] = os.environ[name]
    started = time.monotonic()
    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            errors="replace",
            env=environment,
            cwd=cwd,
            timeout=deadline.timeout(cap=cap, reserve=2.0),
        )
    except subprocess.TimeoutExpired as exc:
        raise TimeBudgetExceeded(f"adapter exceeded transaction deadline: {command}") from exc
    evidence = {
        "argv": command,
        "returncode": process.returncode,
        "runtime_seconds": round(time.monotonic() - started, 6),
        "stdout_sha256": hashlib.sha256(process.stdout.encode()).hexdigest(),
        "stderr_sha256": hashlib.sha256(process.stderr.encode()).hexdigest(),
        "stdout_tail": process.stdout[-4000:],
        "stderr_tail": process.stderr[-4000:],
    }
    if process.returncode != 0:
        raise SwapError(
            f"adapter failed with rc={process.returncode}: "
            f"{(process.stderr or process.stdout)[-1000:]}"
        )
    if not result_path.is_file():
        raise SwapError("adapter returned success without writing its result")
    return evidence


def _load_adapter_result(
    root: Path, transaction: Path, path: Path, expected_destinations: dict
) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SwapError(f"cannot read adapter result: {exc}") from exc
    _strict(value, "adapter result", {"schema", "status", "promotions", "evidence"})
    if value["schema"] != RESULT_SCHEMA:
        raise SwapError(f"unsupported adapter result schema {value['schema']!r}")
    if value["status"] != "clean":
        error = SwapError(f"adapter status is {value['status']!r}")
        error.status = str(value["status"])
        raise error
    if not isinstance(value["promotions"], list) or not value["promotions"]:
        raise SwapError("clean adapter result has no promotions")
    destinations = set()
    for index, operation in enumerate(value["promotions"]):
        _strict(
            operation,
            f"adapter result promotions[{index}]",
            {"staged", "destination", "original_identity", "staged_sha256"},
        )
        staged = _resolve_under(transaction, operation["staged"], "staged promotion", True)
        declared_identity = operation["original_identity"]
        destination = _resolve_under(
            root,
            operation["destination"],
            "promotion destination",
            must_exist=declared_identity is not None,
        )
        if declared_identity is None and destination.exists():
            raise ConcurrentChange(f"new promotion destination already exists: {destination}")
        if destination not in expected_destinations:
            raise SwapError(f"adapter returned undeclared promotion destination: {destination}")
        if destination in destinations:
            raise SwapError(f"duplicate promotion destination: {destination}")
        destinations.add(destination)
        identity = operation["original_identity"]
        if identity != expected_destinations[destination]:
            raise ConcurrentChange(
                f"adapter original identity differs from pre-adapter snapshot: {destination}"
            )
        if identity is not None:
            _strict(identity, "original_identity", {"sha256", "device", "inode", "size", "mtime_ns"})
            if not _valid_digest(identity["sha256"]):
                raise SwapError("promotion contains invalid original SHA-256")
        if not _valid_digest(operation["staged_sha256"]):
            raise SwapError("promotion contains invalid staged SHA-256")
        if _sha256(staged) != operation["staged_sha256"]:
            raise SwapError(f"adapter staged digest mismatch: {staged}")
        operation["staged"] = str(staged)
        operation["destination"] = str(destination)
    if destinations != set(expected_destinations):
        raise SwapError(
            "adapter promotion set differs: missing=%s" %
            sorted(str(path) for path in set(expected_destinations) - destinations)
        )
    return value


def _validate_evidence(evidence, root: Path, targets: list, promotions: list) -> None:
    _strict(evidence, "adapter evidence", {"schema", "targets"})
    if evidence["schema"] != EVIDENCE_SCHEMA:
        raise SwapError(f"unsupported evidence schema {evidence['schema']!r}")
    target_values = evidence["targets"]
    if not isinstance(target_values, dict):
        raise SwapError("adapter evidence.targets must be an object")
    expected_names = {target["name"] for target in targets}
    if set(target_values) != expected_names:
        raise SwapError("adapter evidence target set differs")
    promotion_by_destination = {
        Path(operation["destination"]): operation for operation in promotions
    }
    for target in targets:
        name = target["name"]
        value = _strict(
            target_values[name], f"adapter evidence.targets.{name}",
            {"destination", "staged_sha256", "erc", "drc", "settlement", "audits"},
        )
        destination = _resolve_under(
            root, value["destination"], f"adapter evidence target {name} destination"
        )
        expected_destination = _resolve_under(
            root, target["board"], f"target {name} board", True
        )
        if destination != expected_destination or destination not in promotion_by_destination:
            raise SwapError(f"adapter evidence target {name} is not bound to its board promotion")
        operation = promotion_by_destination[destination]
        if value["staged_sha256"] != operation["staged_sha256"]:
            raise SwapError(f"adapter evidence target {name} staged digest differs")
        erc = _strict(value["erc"], f"adapter evidence.targets.{name}.erc", {"passed", "errors", "warnings"})
        if erc != {"passed": True, "errors": 0, "warnings": 0}:
            raise SwapError(f"adapter evidence target {name} ERC is not clean")
        drc = _strict(
            value["drc"], f"adapter evidence.targets.{name}.drc",
            {"passed", "violations", "allowed_documentation", "unconnected", "parity"},
        )
        if drc["passed"] is not True or any(
            not isinstance(drc[field], int) or isinstance(drc[field], bool) or drc[field] < 0
            for field in ("violations", "allowed_documentation", "unconnected", "parity")
        ) or drc["violations"] != drc["allowed_documentation"] or drc["unconnected"] or drc["parity"]:
            raise SwapError(f"adapter evidence target {name} DRC is not accepted")
        settlement = _strict(
            value["settlement"], f"adapter evidence.targets.{name}.settlement",
            {"passed", "zone_layers"},
        )
        if settlement["passed"] is not True or not isinstance(settlement["zone_layers"], int) or isinstance(settlement["zone_layers"], bool) or settlement["zone_layers"] < 0:
            raise SwapError(f"adapter evidence target {name} did not settle")
        audits = value["audits"]
        if not isinstance(audits, list) or not audits:
            raise SwapError(f"adapter evidence target {name} has no audits")
        for index, audit in enumerate(audits):
            _strict(audit, f"adapter evidence.targets.{name}.audits[{index}]", {"name", "passed"})
            if not isinstance(audit["name"], str) or not audit["name"] or audit["passed"] is not True:
                raise SwapError(f"adapter evidence target {name} audit did not pass")


def _prepare_journal(
    path: Path, transaction_id: str, operations: list[dict], deadline: Deadline
) -> dict:
    entries = []
    backup_root = path.parent / ".footprint-swap-backups" / transaction_id
    for index, operation in enumerate(operations):
        destination = Path(operation["destination"])
        backup = backup_root / f"{index:03d}-{destination.name}"
        if operation["original_identity"] is not None:
            deadline.check(reserve=0.5)
            _copy_durable(destination, backup)
            deadline.check(reserve=0.5)
            if _sha256(backup) != operation["original_identity"]["sha256"]:
                raise ConcurrentChange(f"backup differs from declared original: {destination}")
            backup_value = str(backup)
        else:
            backup_value = None
        entries.append({
            **operation,
            "backup": backup_value,
            "state": "prepared",
        })
    journal = {
        "schema": JOURNAL_SCHEMA,
        "transaction_id": transaction_id,
        "state": "prepared",
        "next_index": 0,
        "operations": entries,
    }
    _write_json_durable(path, journal)
    deadline.check(reserve=0.5)
    return journal


def _verify_original(operation: dict) -> None:
    destination = Path(operation["destination"])
    if operation["original_identity"] is None:
        if destination.exists():
            raise ConcurrentChange(f"new destination appeared before promotion: {destination}")
        return
    observed = _identity(destination)
    if observed != operation["original_identity"]:
        raise ConcurrentChange(f"authority input changed before promotion: {destination}")


def _promote(journal_path: Path, journal: dict, deadline: Deadline) -> None:
    for operation in journal["operations"]:
        _verify_original(operation)
    journal["state"] = "applying"
    _write_json_durable(journal_path, journal)
    for index, operation in enumerate(journal["operations"]):
        deadline.remaining(reserve=0.2)
        journal["next_index"] = index
        operation["state"] = "intent"
        _write_json_durable(journal_path, journal)
        destination = Path(operation["destination"])
        _copy_durable(Path(operation["staged"]), destination)
        deadline.check(reserve=0.2)
        if _sha256(destination) != operation["staged_sha256"]:
            raise RecoveryRequired(f"promoted digest mismatch: {destination}")
        operation["state"] = "applied"
        journal["next_index"] = index + 1
        _write_json_durable(journal_path, journal)
    deadline.check(reserve=0.1)
    journal["state"] = "committed"
    _write_json_durable(journal_path, journal)
    try:
        deadline.check()
    except TimeBudgetExceeded:
        journal["state"] = "deadline_exceeded"
        _write_json_durable(journal_path, journal)
        raise


def _recover(journal_path: Path) -> Optional[str]:
    if not journal_path.exists():
        return None
    try:
        journal = json.loads(journal_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RecoveryRequired(f"cannot parse promotion journal: {exc}") from exc
    _strict(journal, "journal", {"schema", "transaction_id", "state", "next_index", "operations"})
    if journal["schema"] != JOURNAL_SCHEMA:
        raise RecoveryRequired("unsupported promotion journal schema")
    if journal["state"] == "committed":
        journal_path.unlink()
        _fsync_dir(journal_path.parent)
        return "cleared_committed_journal"
    for operation in reversed(journal["operations"]):
        destination = Path(operation["destination"])
        present = os.path.lexists(str(destination))
        if present and (destination.is_symlink() or not destination.is_file()):
            raise RecoveryRequired(
                f"non-regular object at interrupted destination: {destination}"
            )
        current_digest = _sha256(destination) if present else None
        staged_digest = operation["staged_sha256"]
        original = operation["original_identity"]
        if original is None:
            if current_digest is None:
                continue
            if current_digest != staged_digest:
                raise RecoveryRequired(
                    f"unknown bytes at interrupted new destination: {destination}"
                )
            destination.unlink()
            _fsync_dir(destination.parent)
            continue
        original_digest = original["sha256"]
        if current_digest == original_digest:
            continue
        if current_digest != staged_digest:
            raise RecoveryRequired(
                f"unknown bytes at interrupted destination: {destination}"
            )
        backup = Path(operation["backup"])
        if not backup.is_file() or _sha256(backup) != original_digest:
            raise RecoveryRequired(f"verified rollback backup unavailable: {backup}")
        _copy_durable(backup, destination)
        if _sha256(destination) != original_digest:
            raise RecoveryRequired(f"rollback verification failed: {destination}")
    journal["state"] = "rolled_back"
    _write_json_durable(journal_path, journal)
    journal_path.unlink()
    _fsync_dir(journal_path.parent)
    return "rolled_back_incomplete_transaction"


def run(spec_path: Path, apply: bool, seconds: float, report_override=None) -> tuple[int, dict]:
    deadline = Deadline(seconds)
    spec_path = spec_path.resolve()
    root, spec = _load_spec(spec_path)
    journal_path = _resolve_under(root, spec.get("journal", ".kicad-footprint-swap-journal.json"), "journal")
    lock_path = _resolve_under(root, spec.get("lock", ".kicad-footprint-swap.lock"), "lock")
    report_path = _resolve_under(
        root, report_override or spec.get("report", "footprint-swap-report.json"), "report"
    )
    authority_paths = _authority_paths(root, spec_path, spec)
    verifier_paths = {
        Path(__file__).resolve(),
        Path(__file__).with_name("kicad_verify.py").resolve(),
        Path(__file__).with_name("_util.py").resolve(),
        Path(__file__).with_name("kicad_netlist.py").resolve(),
    }
    missing_verifier = [path for path in verifier_paths if not path.is_file()]
    if missing_verifier:
        raise SwapError("shared verifier authority is missing: " + ", ".join(map(str, missing_verifier)))
    promotion_paths = set()
    for index, value in enumerate(spec["promotion_destinations"]):
        promotion_paths.add(_resolve_under(root, value, f"promotion_destinations[{index}]"))
    if len(promotion_paths) != len(spec["promotion_destinations"]):
        raise SwapError("promotion destinations alias after path normalization")
    reserved = {journal_path, lock_path, report_path}
    if len(reserved) != 3:
        raise SwapError("journal, lock, and report paths must be distinct")
    collisions = reserved & (authority_paths | promotion_paths)
    if collisions:
        raise SwapError("reserved path aliases authority or promotion: " + ", ".join(map(str, sorted(collisions))))
    report_original = _identity(report_path) if report_path.exists() else None
    report = {
        "schema": REPORT_SCHEMA,
        "spec": str(spec_path),
        "status": "verification_failed",
        "apply_requested": bool(apply),
        "started_unix_ns": time.time_ns(),
    }
    _check_kicad_locks(authority_paths | promotion_paths)
    lock_fd = _acquire_lock(lock_path)
    try:
        recovered = _recover(journal_path)
        if recovered:
            report["recovery"] = recovered
        deadline.check(reserve=2.0)
        authority_snapshots = _snapshots(authority_paths | verifier_paths)
        expected_destinations = {
            path: (_identity(path) if path.exists() else None)
            for path in promotion_paths
        }
        if (report_path.exists() and report_original is None) or (
            report_original is not None and (
                not report_path.is_file() or _identity(report_path) != report_original
            )
        ):
            raise ConcurrentChange(f"report destination changed before adapter: {report_path}")
        transaction_id = uuid.uuid4().hex
        transaction_root = _resolve_under(root, ".footprint-swap-transactions", "transaction root")
        transaction = transaction_root / transaction_id
        transaction.mkdir(parents=True)
        _fsync_dir(transaction.parent)
        request_path = transaction / "request.json"
        result_path = transaction / "adapter-result.json"
        request = {
            "schema": REQUEST_SCHEMA,
            "transaction_id": transaction_id,
            "root": str(root),
            "transaction_dir": str(transaction),
            "remaining_seconds": deadline.remaining(reserve=2.0),
            "targets": spec["targets"],
            "substitutions": spec["substitutions"],
            "original_identities": {
                str(path): identity for path, identity in expected_destinations.items()
            },
            "shared_verifier": {
                path.name: {"path": str(path), "sha256": authority_snapshots[path]["sha256"]}
                for path in sorted(verifier_paths)
            },
        }
        _write_json_durable(request_path, request)
        cap = spec.get("adapter_timeout_seconds")
        report["adapter"] = _run_adapter(
            spec["adapter_argv"], request_path, result_path, deadline, cap, cwd=root
        )
        _verify_snapshots(authority_snapshots)
        adapter = _load_adapter_result(root, transaction, result_path, expected_destinations)
        _validate_evidence(adapter["evidence"], root, spec["targets"], adapter["promotions"])
        _verify_snapshots(authority_snapshots)
        report["adapter_evidence"] = adapter["evidence"]
        report["promotions"] = adapter["promotions"]
        report["transaction_id"] = transaction_id
        if apply:
            report["status"] = "clean"
            report["promotion"] = "committed"
            report["elapsed_seconds"] = round(deadline.elapsed(), 6)
            staged_report = transaction / "transaction-report.json"
            _write_json_durable(staged_report, report)
            report_operation = {
                "staged": str(staged_report),
                "destination": str(report_path),
                "original_identity": report_original,
                "staged_sha256": _sha256(staged_report),
            }
            operations = [*adapter["promotions"], report_operation]
            journal = _prepare_journal(journal_path, transaction_id, operations, deadline)
            _verify_snapshots(authority_snapshots)
            _promote(journal_path, journal, deadline)
            report["elapsed_seconds"] = round(deadline.elapsed(), 6)
        else:
            deadline.check(reserve=0.1)
            report["status"] = "valid_dry_run"
            report["promotion"] = "not_requested"
            report["elapsed_seconds"] = round(deadline.elapsed(), 6)
            _write_json_durable(report_path, report)
            deadline.check()
        return 0, report
    except SwapError as exc:
        try:
            recovered = _recover(journal_path)
            if recovered:
                report["recovery"] = recovered
        except RecoveryRequired as recovery_exc:
            exc = recovery_exc
        report["status"] = exc.status
        report["error"] = str(exc)
        report["elapsed_seconds"] = round(deadline.elapsed(), 6)
        current_report = _identity(report_path) if report_path.is_file() else None
        if current_report == report_original or (
            current_report is None and report_original is None
        ):
            _write_json_durable(report_path, report)
        else:
            report["report_write"] = "skipped_concurrent_destination"
        return 2, report
    finally:
        _release_lock(lock_path, lock_fd)


def _parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--time-budget", type=float, default=180.0)
    parser.add_argument("--report")
    parser.add_argument("--apply", action="store_true")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    try:
        rc, report = run(args.spec, args.apply, args.time_budget, args.report)
    except SwapError as exc:
        print(f"{exc.status}: {exc}", file=sys.stderr)
        return 2
    print(f"{report['status']}: {report.get('elapsed_seconds', 0):.3f}s")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
