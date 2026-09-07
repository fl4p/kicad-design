#!/usr/bin/env python3
"""Classify KiCad DRC unconnected records by explicit net ownership.

KiCad's JSON DRC report contains item descriptions rather than structured net
and item-kind fields.  This helper parses those descriptions fail-closed and
splits records into:

* signal opens: records on nets not declared as pour-managed;
* pour topology: every record on an explicitly declared pour-managed net,
  including zone-track, zone-via, track-via, and records containing no zone;
* ambiguous: missing/mismatched nets, malformed items, a zone on an undeclared
  net, or any record on a declared mixed-duty pour net. The DRC text cannot
  assign mixed-net records to authored routing versus refill-owned topology.

The declaration is intentionally project-owned.  The script does not assume
that a net named GND is a plane, or that every plane net is named GND.

A gating run needs a gate.  The whole product of this tool is the
signal/pour split, so `--require-zero-total` -- which ignores that split --
is not the only gate on offer: `--require-zero-signal-opens` grades the
signal side alone, which is the gate a board with legitimate pour records can
actually use.  A run with neither gate and no explicit `--report-only` is a
configuration error, not a pass: exit 0 with no gate used to mean "the split
was evaluable" while reading exactly like "the board is connected".

Exit 2 is an input/configuration error, exit 3 means the classification is
unevaluable (ambiguous or possibly capped), and exit 4 means a requested gate
failed.  Exit 0 means a requested gate passed, or -- under `--report-only` --
that the split was evaluable and nothing was graded.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import json
import math
import os
import pathlib
import re
import sys
import tempfile
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


SCHEMA = "kicad-drc-connectivity-v1"
KICAD_DRC_SCHEMA = "https://schemas.kicad.org/drc.v1.json"
NET_TOKEN = re.compile(r"\[([^\[\]]+)\]")
KNOWN_ITEM_KINDS = frozenset(("pad", "track", "via", "zone"))
PAD_QUALIFIERS = frozenset(("pth", "smd", "npth"))
REQUIRED_SEVERITIES = frozenset(("error", "warning", "exclusion"))
SUPPORTED_COORDINATE_UNITS = frozenset(("mm", "in", "mils"))
KICAD_10_REPORT_CAP = 199
# The 199 figure is measured, but it is measured for `silk_overlap` and
# `silk_over_copper` on KiCad 10.0.5 -- NOT for `unconnected_items`, and not on
# any other release. See ~/dev/kb/tooling/kicad-drc-caps-reports-at-199-per-type.md,
# which ends "re-probe other releases rather than assuming the cap's value or
# its existence". So this is an unqualified boundary for the count this tool
# actually ranks, and every total AT OR ABOVE it is unqualified with it.
CAP_UNQUALIFIED = "unqualified"


class ConnectivityError(ValueError):
    pass


def _item_net(description: Any) -> Tuple[Optional[str], Optional[str]]:
    if not isinstance(description, str) or not description.strip():
        return None, "item description is missing or empty"
    matches = NET_TOKEN.findall(description)
    if len(matches) != 1:
        return None, f"expected one [net] token, found {len(matches)}"
    return matches[0], None


def _item_kind(description: Any) -> str:
    if not isinstance(description, str):
        return "unknown"
    words = description.strip().split()
    if not words:
        return "unknown"
    first = words[0].lower()
    if first in PAD_QUALIFIERS:
        return "pad" if len(words) > 1 and words[1].lower() == "pad" else "unknown"
    return first if first in KNOWN_ITEM_KINDS else "unknown"


def classify_record(
    record: Any,
    index: int,
    pour_nets: Iterable[str],
    mixed_pour_nets: Iterable[str] = (),
    included_severities: Optional[Iterable[str]] = None,
    strict_pour: bool = False,
) -> Dict[str, Any]:
    declared = frozenset(pour_nets)
    mixed = frozenset(mixed_pour_nets)
    result: Dict[str, Any] = {
        "index": index,
        "bucket": "ambiguous",
        "net": None,
        "item_kinds": [],
        "reason": None,
        "items": [],
    }
    if not isinstance(record, dict):
        result["reason"] = "unconnected record is not an object"
        return result
    if record.get("type") != "unconnected_items":
        result["reason"] = f"unexpected record type {record.get('type')!r}"
        return result
    if not isinstance(record.get("description"), str) or not record["description"]:
        result["reason"] = "unconnected record has no description"
        return result
    if not isinstance(record.get("severity"), str) or not record["severity"]:
        result["reason"] = "unconnected record has no severity"
        return result
    if (
        included_severities is not None
        and record["severity"] not in frozenset(included_severities)
    ):
        result["reason"] = (
            f"unconnected record severity {record['severity']!r} is absent "
            "from included_severities"
        )
        return result
    items = record.get("items")
    if not isinstance(items, list) or len(items) != 2:
        result["reason"] = "unconnected record must contain exactly two items"
        return result

    parsed_items: List[Dict[str, Any]] = []
    nets: List[str] = []
    errors: List[str] = []
    kinds: List[str] = []
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            parsed_items.append({"description": None, "uuid": None})
            kinds.append("unknown")
            errors.append(f"item {item_index} is not an object")
            continue
        description = item.get("description")
        net, error = _item_net(description)
        kind = _item_kind(description)
        parsed_items.append(
            {
                "description": description,
                "uuid": item.get("uuid"),
                "pos": item.get("pos"),
            }
        )
        kinds.append(kind)
        if kind not in KNOWN_ITEM_KINDS:
            errors.append(f"item {item_index}: unsupported item kind {kind!r}")
        if not isinstance(item.get("uuid"), str) or not item["uuid"]:
            errors.append(f"item {item_index}: uuid is missing or empty")
        pos = item.get("pos")
        if not isinstance(pos, dict) or set(pos) != {"x", "y"}:
            errors.append(f"item {item_index}: pos must contain exactly x and y")
        else:
            for axis in ("x", "y"):
                value = pos[axis]
                if (
                    isinstance(value, bool)
                    or not isinstance(value, (int, float))
                    or not math.isfinite(value)
                ):
                    errors.append(f"item {item_index}: pos.{axis} is not finite numeric")
        if error:
            errors.append(f"item {item_index}: {error}")
        else:
            if net is None:
                errors.append(f"item {item_index}: net parser returned no result")
            else:
                nets.append(net)

    result["items"] = parsed_items
    result["item_kinds"] = kinds
    if errors:
        result["reason"] = "; ".join(errors)
        return result
    unique_nets = sorted(set(nets))
    if len(unique_nets) != 1:
        result["reason"] = f"items name different nets: {unique_nets}"
        return result

    net = unique_nets[0]
    result["net"] = net
    if net in mixed:
        result["reason"] = (
            f"record on mixed-duty pour net {net!r}; DRC text cannot distinguish "
            "authored routing from refill-owned topology"
        )
        return result
    if net in declared:
        if strict_pour and "zone" not in kinds:
            # A record with no zone item on a declared pure-pour net is not
            # self-evidently refill topology: a pad-to-track open is
            # authored routing, and the DRC text cannot tell them apart.
            # Classifying it as pour turned a real signal open into a PASS
            # purely because the caller labelled its net (codex review of
            # 7b00165). Only the fabrication-closing gate is strict; the
            # default classification keeps its recorded calibration.
            result["reason"] = (
                f"record on declared pour net {net!r} contains no zone item "
                f"({', '.join(kinds) or 'no recognised items'}); under a "
                "signal-open gate an authored-routing open cannot be "
                "assumed to be refill topology"
            )
            return result
        result["bucket"] = "pour_topology"
        return result
    if "zone" in kinds:
        result["reason"] = (
            f"zone appears on undeclared pour-managed net {net!r}; "
            "declare it with --pour-net or leave the split unevaluable"
        )
        return result
    result["bucket"] = "signal_open"
    return result


def _parse_report_cap(value: Any) -> Optional[int]:
    """`none` disables the cap check; anything else must be a positive int.

    A malformed value is a configuration error, never a silently disabled
    check -- muting the cap is exactly the mute-button failure `../GUARDS.md`
    forbids, so it has to be spelled out."""
    if isinstance(value, str) and value.strip().lower() == "none":
        return None
    try:
        cap = int(value)
    except (TypeError, ValueError):
        raise ConnectivityError(
            "--report-cap must be a positive integer or 'none', not %r" % (value,)
        )
    if cap <= 0:
        raise ConnectivityError(
            "--report-cap must be positive or 'none', not %d" % cap
        )
    return cap


def classify_report(
    report: Any,
    source: str,
    pour_nets: Sequence[str],
    mixed_pour_nets: Sequence[str] = (),
    strict_pour: bool = False,
    cap: Optional[int] = KICAD_10_REPORT_CAP,
) -> Dict[str, Any]:
    if not isinstance(report, dict):
        raise ConnectivityError("DRC report root is not an object")
    required = {
        "$schema",
        "coordinate_units",
        "ignored_checks",
        "included_severities",
        "kicad_version",
        "schematic_parity",
        "unconnected_items",
        "violations",
    }
    missing = sorted(required - set(report))
    if missing:
        raise ConnectivityError(f"KiCad DRC JSON is missing key(s): {', '.join(missing)}")
    if report["$schema"] != KICAD_DRC_SCHEMA:
        raise ConnectivityError(f"unsupported KiCad DRC schema {report['$schema']!r}")
    if report["coordinate_units"] not in SUPPORTED_COORDINATE_UNITS:
        raise ConnectivityError(
            f"unsupported DRC coordinate_units {report['coordinate_units']!r}"
        )
    if not isinstance(report["kicad_version"], str) or not report["kicad_version"]:
        raise ConnectivityError("DRC kicad_version is missing or empty")
    for key in (
        "ignored_checks",
        "included_severities",
        "schematic_parity",
        "unconnected_items",
        "violations",
    ):
        if not isinstance(report[key], list):
            raise ConnectivityError(f"DRC {key} is not a list")
    severities = report["included_severities"]
    if any(not isinstance(value, str) or not value for value in severities):
        raise ConnectivityError("DRC included_severities contains an invalid value")
    if len(set(severities)) != len(severities):
        raise ConnectivityError("DRC included_severities contains a duplicate")
    severity_set = set(severities)
    if severity_set != REQUIRED_SEVERITIES:
        missing_severities = sorted(REQUIRED_SEVERITIES - severity_set)
        unknown_severities = sorted(severity_set - REQUIRED_SEVERITIES)
        details = []
        if missing_severities:
            details.append("missing " + ", ".join(missing_severities))
        if unknown_severities:
            details.append("unknown " + ", ".join(unknown_severities))
        raise ConnectivityError(
            "DRC report does not contain exactly the supported full-severity set; "
            + "; ".join(details)
        )
    ignored_keys: List[str] = []
    for index, ignored in enumerate(report["ignored_checks"]):
        if not isinstance(ignored, dict):
            raise ConnectivityError(f"DRC ignored_checks[{index}] is not an object")
        key = ignored.get("key")
        description = ignored.get("description")
        if not isinstance(key, str) or not key:
            raise ConnectivityError(f"DRC ignored_checks[{index}].key is invalid")
        if not isinstance(description, str) or not description:
            raise ConnectivityError(
                f"DRC ignored_checks[{index}].description is invalid"
            )
        ignored_keys.append(key)
    if len(set(ignored_keys)) != len(ignored_keys):
        raise ConnectivityError("DRC ignored_checks contains a duplicate key")
    if "unconnected_items" in ignored_keys:
        raise ConnectivityError("DRC report ignores unconnected_items")
    records = report["unconnected_items"]
    if any(not isinstance(net, str) or not net for net in pour_nets):
        raise ConnectivityError("--pour-net values must be nonempty strings")
    if len(set(pour_nets)) != len(pour_nets):
        raise ConnectivityError("duplicate --pour-net declaration")
    if any(not isinstance(net, str) or not net for net in mixed_pour_nets):
        raise ConnectivityError("--mixed-pour-net values must be nonempty strings")
    if len(set(mixed_pour_nets)) != len(mixed_pour_nets):
        raise ConnectivityError("duplicate --mixed-pour-net declaration")
    overlap = sorted(set(pour_nets) & set(mixed_pour_nets))
    if overlap:
        raise ConnectivityError(
            "net(s) declared as both pure and mixed-duty pour: " + ", ".join(overlap)
        )

    classified = [
        classify_record(record, index, pour_nets, mixed_pour_nets, severities,
                        strict_pour)
        for index, record in enumerate(records)
    ]
    counts = collections.Counter(row["bucket"] for row in classified)
    by_net: Dict[str, collections.Counter[str]] = {
        "signal_open": collections.Counter(),
        "pour_topology": collections.Counter(),
        "ambiguous": collections.Counter(),
    }
    for row in classified:
        by_net[row["bucket"]][row["net"] or "?"] += 1

    total = len(classified)
    signal = counts["signal_open"]
    pour = counts["pour_topology"]
    ambiguous = counts["ambiguous"]
    if signal + pour + ambiguous != total:
        raise ConnectivityError("internal classification count mismatch")
    # KiCad 10.0.5 is measured to cap each violation type at exactly 199 in
    # both text and JSON reports. Treat that exact boundary as suspicious on
    # every version until the installed release is explicitly re-probed.
    # `== cap` broke monotonicity: 198 evaluable, 199 unevaluable, 200
    # evaluable again, so a strictly WORSE report recovered from UNEVALUABLE to
    # PASS at the far tail (measured 2026-09-07, codex review of 7a99de9: 200
    # duplicated pour records passed the signal gate). A count above a cap this
    # tool cannot qualify is not better evidence than a count at it -- it means
    # the report came from a release whose cap behaviour is unknown here.
    report_censored = cap is not None and total >= cap
    censor_reason = None
    if report_censored:
        censor_reason = (
            "unconnected_items contains %d records, at or above the "
            "unqualified per-type report cap %d (measured on KiCad 10.0.5 for "
            "silk_overlap/silk_over_copper, never for unconnected_items); "
            "qualify the installed KiCad version's cap behaviour with "
            "--report-cap before ranking this value" % (total, cap)
        )
    return {
        "schema": SCHEMA,
        "source": source,
        "pour_nets": sorted(pour_nets),
        "mixed_pour_nets": sorted(mixed_pour_nets),
        "counts": {
            "signal_open_records": signal,
            "pour_topology_records": pour,
            "ambiguous_records": ambiguous,
            "total_unconnected_records": total,
        },
        "by_net": {
            bucket: dict(sorted(counter.items()))
            for bucket, counter in by_net.items()
        },
        "records": classified,
        "report_cap": cap,
        "report_censored": report_censored,
        "censor_reason": censor_reason,
        "classification_evaluable": ambiguous == 0 and not report_censored,
    }


def load_report(path: pathlib.Path) -> Tuple[Any, Dict[str, Any]]:
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            payload = handle.read()
            after = os.fstat(handle.fileno())
        current = path.stat()
        identity = lambda stat: (
            stat.st_dev,
            stat.st_ino,
            stat.st_size,
            stat.st_mtime_ns,
        )
        if identity(before) != identity(after) or identity(after) != identity(current):
            raise ConnectivityError(f"DRC JSON {path} changed while it was read")
        decoded = payload.decode("utf-8")
        report = json.loads(decoded)
        return report, {
            "source_sha256": hashlib.sha256(payload).hexdigest(),
            "source_size": len(payload),
        }
    except ConnectivityError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectivityError(f"cannot read DRC JSON {path}: {exc}") from exc


def _atomic_write(path: pathlib.Path, result: Dict[str, Any]) -> None:
    descriptor = None
    temporary = None
    try:
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            descriptor = None
            json.dump(result, handle, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        temporary = None
    except (OSError, UnicodeError, TypeError, ValueError) as exc:
        raise ConnectivityError(f"cannot write JSON report {path}: {exc}") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if temporary is not None:
            try:
                os.unlink(temporary)
            except OSError:
                pass


def prepare_json(path: pathlib.Path, source: Optional[pathlib.Path]) -> None:
    if source is not None and path.resolve() == source.resolve():
        raise ConnectivityError("--json output must not overwrite the DRC input")
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ConnectivityError(
                f"refusing to replace non-report JSON output {path}: {exc}"
            ) from exc
        if not isinstance(existing, dict) or existing.get("schema") != SCHEMA:
            raise ConnectivityError(
                f"refusing to replace {path}: existing file is not a {SCHEMA} report"
            )
    _atomic_write(
        path,
        {
            "schema": SCHEMA,
            "source": str(source) if source is not None else None,
            "classification_evaluable": False,
            "aggregate_gate": "unevaluable",
            "signal_open_gate": "unevaluable",
            "error": "classification did not complete",
        },
    )


def write_json(path: pathlib.Path, result: Dict[str, Any]) -> None:
    _atomic_write(path, result)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("drc_json", type=pathlib.Path)
    parser.add_argument(
        "--report-cap",
        default=str(KICAD_10_REPORT_CAP),
        help=(
            "the installed KiCad's measured per-type report cap for "
            "unconnected_items. A total AT OR ABOVE it is unevaluable, because "
            "a capped count is not a measurement. Default %d, which is measured "
            "for silk_overlap/silk_over_copper on KiCad 10.0.5 and NOT for "
            "unconnected_items -- pass 'none' only once you have probed the "
            "installed release and shown it does not cap this type"
            % KICAD_10_REPORT_CAP
        ),
    )
    parser.add_argument(
        "--pour-net",
        action="append",
        help="exact net whose records are all pour topology; repeat as needed",
    )
    parser.add_argument(
        "--mixed-pour-net",
        action="append",
        help=(
            "exact net mixing authored routing and refill-owned topology; "
            "all its records remain ambiguous; repeat as needed"
        ),
    )
    parser.add_argument(
        "--no-pour-nets",
        action="store_true",
        help="explicitly declare that this board has no pour-managed nets",
    )
    parser.add_argument("--json", type=pathlib.Path, help="write the complete split as JSON")
    parser.add_argument(
        "--require-zero-total",
        action="store_true",
        help="gate the aggregate completion criterion after classification",
    )
    parser.add_argument(
        "--require-zero-signal-opens",
        action="store_true",
        help=(
            "gate the signal side of the split: fail if any record is a "
            "signal open, whatever the pour topology count. This is the gate "
            "a board with legitimate pour records can use; "
            "--require-zero-total cannot pass on such a board. It also "
            "tightens classification: under this gate a record on a "
            "declared pour net that contains no zone item stays ambiguous "
            "(exit 3) instead of counting as pour topology, because a "
            "pad-to-track open is authored routing and a declaration is "
            "not evidence"
        ),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help=(
            "classify and report without grading. Exit 0 means the split was "
            "evaluable, never that the board is connected; not valid as a gate"
        ),
    )
    return parser


def _prescan_json_output(argv: Sequence[str]) -> Optional[pathlib.Path]:
    """Find argparse's last syntactically supplied --json target.

    The target does not depend on resolving the positional input. This lets a
    malformed invocation invalidate its effective prior report before
    argparse exits. An existing non-owned target is still never overwritten.
    """
    json_out: Optional[pathlib.Path] = None
    for index, token in enumerate(argv):
        if token == "--":
            break
        # An abbreviation counts for INVALIDATION only: allow_abbrev is off
        # so `--jso` is rejected as an argument, but the operator meant it as
        # the report target and a stale prior report must not survive the
        # failed run (codex review of 7b00165).
        if _names_json(token):
            if index + 1 < len(argv) and not argv[index + 1].startswith("-"):
                json_out = pathlib.Path(argv[index + 1])
        if token.startswith("--json="):
            value = token.split("=", 1)[1]
            if value:
                json_out = pathlib.Path(value)
    return json_out


def _names_json(token: str) -> bool:
    """True for `--json` and for any prefix argparse would have abbreviated.

    Used for invalidation and alias detection only. `allow_abbrev` is off, so
    an abbreviation is still REJECTED as an argument -- but the operator
    plainly meant it as the report target, and both the stale-report rule and
    the alias guard have to see it that way (codex review of 7b00165).
    """
    return (token.startswith("--j") and len(token) >= 3
            and "--json".startswith(token))


def _preparse_target_may_be_input(
    argv: Sequence[str], target: pathlib.Path
) -> bool:
    """Conservatively detect an input/output alias before invalidation.

    argparse has not established which non-option is the positional yet. Treat
    any other path-like operand resolving to the JSON target as a possible
    input. False refusals are safer than overwriting an owned input report.
    """
    json_operands = set()
    before_terminator = True
    for index, token in enumerate(argv):
        if before_terminator and token == "--":
            before_terminator = False
            continue
        if not before_terminator:
            continue
        if (
            _names_json(token)
            and index + 1 < len(argv)
            and not argv[index + 1].startswith("-")
        ):
            json_operands.add(index + 1)

    resolved_target = target.resolve()
    before_terminator = True
    for index, token in enumerate(argv):
        if index in json_operands:
            continue
        if before_terminator and token == "--":
            before_terminator = False
            continue
        if before_terminator and token.startswith("-"):
            continue
        try:
            if pathlib.Path(token).resolve() == resolved_target:
                return True
        except (OSError, ValueError):
            continue
    return False


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_argv = list(sys.argv[1:] if argv is None else argv)
    json_guess = _prescan_json_output(raw_argv)
    if json_guess is not None:
        if _preparse_target_may_be_input(raw_argv, json_guess):
            print(
                "UNEVALUABLE: --json target may alias the DRC input; refusing "
                "pre-parse invalidation",
                file=sys.stderr,
            )
            return 2
        try:
            prepare_json(json_guess, None)
        except ConnectivityError as exc:
            print(f"UNEVALUABLE: {exc}", file=sys.stderr)
            return 2
    args = build_parser().parse_args(raw_argv)
    try:
        source = args.drc_json.resolve()
        if args.json:
            prepare_json(args.json, source)
        pour_nets = args.pour_net or []
        mixed_pour_nets = args.mixed_pour_net or []
        if args.no_pour_nets and (pour_nets or mixed_pour_nets):
            raise ConnectivityError(
                "--no-pour-nets cannot be combined with a pour-net declaration"
            )
        if not args.no_pour_nets and not (pour_nets or mixed_pour_nets):
            raise ConnectivityError(
                "declare --pour-net/--mixed-pour-net, or explicitly pass "
                "--no-pour-nets"
            )
        gates = (args.require_zero_total, args.require_zero_signal_opens)
        if args.report_only and any(gates):
            raise ConnectivityError(
                "--report-only cannot be combined with a gate: a run is "
                "either graded or not"
            )
        if not args.report_only and not any(gates):
            raise ConnectivityError(
                "no gate given and --report-only not set: an ungraded gating "
                "run is unevaluable, not a pass. Pass "
                "--require-zero-signal-opens (the signal side alone), "
                "--require-zero-total (the aggregate), or --report-only"
            )
        cap = _parse_report_cap(args.report_cap)
        report, receipt = load_report(source)
        result = classify_report(
            report, str(source), pour_nets, mixed_pour_nets,
            strict_pour=args.require_zero_signal_opens,
            cap=cap,
        )
        result["strict_pour"] = bool(args.require_zero_signal_opens)
        result.update(receipt)
        result["drc_metadata"] = {
            key: report.get(key)
            for key in ("date", "kicad_version", "coordinate_units")
            if key in report
        }
        if not result["classification_evaluable"]:
            result["aggregate_gate"] = "unevaluable"
            result["signal_open_gate"] = "unevaluable"
        else:
            if args.require_zero_total:
                result["aggregate_gate"] = (
                    "pass"
                    if result["counts"]["total_unconnected_records"] == 0
                    else "fail"
                )
            else:
                result["aggregate_gate"] = "not_requested"
            if args.require_zero_signal_opens:
                result["signal_open_gate"] = (
                    "pass"
                    if result["counts"]["signal_open_records"] == 0
                    else "fail"
                )
            else:
                result["signal_open_gate"] = "not_requested"
        if args.json:
            write_json(args.json, result)
    except ConnectivityError as exc:
        print(f"UNEVALUABLE: {exc}", file=sys.stderr)
        return 2

    counts = result["counts"]
    print(
        "signal opens {signal_open_records} | pour topology "
        "{pour_topology_records} | ambiguous {ambiguous_records} | total "
        "{total_unconnected_records}".format(**counts)
    )
    if counts["ambiguous_records"]:
        print("UNEVALUABLE: ambiguous unconnected records remain", file=sys.stderr)
        return 3
    if result["report_censored"]:
        print(f"UNEVALUABLE: {result['censor_reason']}", file=sys.stderr)
        return 3
    if args.require_zero_signal_opens and counts["signal_open_records"]:
        print(
            "FAIL: {signal_open_records} signal open record(s)".format(**counts),
            file=sys.stderr,
        )
        return 4
    if args.require_zero_total and counts["total_unconnected_records"]:
        print("FAIL: aggregate unconnected total is nonzero", file=sys.stderr)
        return 4
    if args.report_only:
        print(
            "CONNECTIVITY-REPORTED-NOT-GRADED: the split was evaluable; "
            "nothing was graded"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
