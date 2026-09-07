#!/usr/bin/env python3
"""Classify KiCad DRC unconnected records by explicit net ownership.

KiCad's JSON DRC report contains item descriptions rather than structured net
and item-kind fields.  This helper parses those descriptions fail-closed and
splits records into:

* signal opens: records on nets not declared as pour-managed;
* pour topology: records on an explicitly declared pour-managed net. In the
  DEFAULT classification this includes zone-track, zone-via, track-via and
  records containing no zone. Under `--require-zero-signal-opens` it narrows
  to ALL-ZONE records only, plus any record the caller has acknowledged
  individually with `--reviewed-record`;
* ambiguous: missing/mismatched nets, malformed items, a zone on an undeclared
  net, any record on a declared mixed-duty pour net, and under the strict gate
  any unacknowledged record naming a pad, track or via on a declared pour net.
  The DRC text cannot assign these to authored routing versus refill-owned
  topology.

WHICH GATE MEANS "DONE": `--require-zero-total`. Not this one. The signal gate
ranks progress and closes the signal component; `../ROUTING.md` is explicit
that "the split changes the ranking, not the definition of done" -- islands are
unfinished copper until stitched or removed, and a criterion written as "zero
unconnected items" is met only when the TOTAL is zero, unless its owner amends
it in words. This docstring previously called the signal gate "the gate that
closes fabrication", which contradicted the doctrine it is implementing
(codex review of 298ed6d). A run that reaches zero signal opens should report
"signal routing complete, board incomplete", not "done".

The strict narrowing under that gate exists because a declaration is a claim
about a NET's role and is never evidence about a particular record: a `Zone [/SIG] <->
Track [/SIG]` record on a net labelled `--pour-net /SIG` used to exit 0 while
being a real open. Because measured reports genuinely do contain zone-track,
zone-via and track-via records on real pour nets (see `../ROUTING.md`), a
blanket refusal would make the gate unusable on the boards it was written for
-- so the escape is per-record and auditable rather than per-net.

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
import datetime
import hashlib
import json
import math
import os
import pathlib
import re
import shutil
import subprocess
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
# Net declarations in a saved board: `(net 3 "/SIG")`. Kept for the fixture
# correspondence test; the guard itself uses `board_net_table`, because a bare
# text regex counts this expression wherever it appears -- inside a pad, in a
# comment, or in a file that is not a board at all.
BOARD_NET = re.compile(r'\(net\s+\d+\s+"([^"]*)"\)')

# A board this large is refused rather than read into memory. Real 4-layer
# boards of the size this skill targets are single-digit MB; the cap exists so
# an unbounded read cannot become a MemoryError escaping the tool's contract.
MAX_BOARD_BYTES = 256 * 1024 * 1024
# Tolerance for KiCad's second-resolution `date` against a
# nanosecond-resolution board mtime. Quantisation only.
# Tolerance for KiCad's second-resolution `date` against the board's
# mtime. One second covers the truncation itself; two covers a
# filesystem whose mtime granularity is 2 s (FAT/exFAT, which a board
# on a USB stick really does live on). It is deliberately far too
# small to admit a report from an earlier editing session.
REPORT_DATE_SKEW_S = 2
# A report dated beyond this far ahead of now is a broken clock, not a
# fresh report -- and a future timestamp satisfies any freshness check
# forever, so it has to be refused rather than tolerated.
REPORT_FUTURE_CEILING_S = 3600
# Two spellings, because KiCad changed the file format underneath this check:
#   KiCad 8  (version 20240108): a numbered table entry, `(net 3 "/SIG")`
#   KiCad 10 (version 20260206): a bare reference, `(net "/SIG")`, and NO
#                                top-level net section whatsoever
# Measured 2026-09-07 over 400 real boards: an 8.0 board carries 847 numbered
# expressions and no bare ones; a 10.0 board carries 1207 bare ones and no
# numbered ones. Matching only the numbered form returned an EMPTY inventory
# for every KiCad 10 board, which would have refused every legitimate
# --pour-net on exactly the release this skill targets.
# The head symbol of an s-expression: `(footprint ...` -> "footprint".
_ROOT_SYMBOL = re.compile(r'\(\s*([A-Za-z_][A-Za-z_0-9]*)')

# Forms that can carry a net. A `(net ...)` anywhere else is not a
# mention of a net by this board -- it is net-shaped text.
NET_BEARING_FORMS = frozenset((
    "pad", "segment", "via", "zone", "arc",
))
# `gr_line` and `target` were in this set and should never have been: KiCad's
# grammar defines gr_line as a GRAPHICAL item with no net member, and the
# track forms are segment/via/arc (codex review of a760a18, citing
# dev-docs.kicad.org). Including them let `(kicad_pcb (gr_line (net
# "/PHANTOM")))` name a net, which is the same net-shaped-text acceptance the
# allowlist exists to stop. Nothing in the 400-board corpus used them.

_NET_EXPR = re.compile(
    r'\(net\s+(\d+\s+)?"((?:[^"\\]|\\.)*)"\s*\)')
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


def record_id(record: Any) -> Optional[str]:
    """A stable id for one unconnected record: its items' UUIDs, hashed.

    KiCad gives each ITEM a uuid but the record itself none, so a caller who
    wants to acknowledge one specific record needs a handle. Sorting the
    uuids makes the id independent of item order; hashing keeps it short
    enough to paste into a command line. It changes if the record's items
    change, which is the point -- an acknowledgement must not survive the
    record it was about."""
    if not isinstance(record, dict):
        return None
    items = record.get("items")
    if not isinstance(items, list) or not items:
        return None
    parts = []
    seen = set()
    for entry in items:
        if not isinstance(entry, dict):
            return None
        uuid = entry.get("uuid")
        description = entry.get("description")
        if uuid is None or not isinstance(description, str):
            # No uuid, or no description to pin the object's identity: there
            # is nothing stable to acknowledge, so there is no id. The record
            # stays ambiguous, which is the safe end.
            return None
        if uuid in seen:
            # Two items claiming one object. Whatever that report means, an
            # acknowledgement keyed on it would not identify one record.
            return None
        seen.add(uuid)
        # Position is part of what was reviewed. Omitting it meant an item
        # could move from x=1 to x=999 and keep both its id and its waiver
        # (codex review of a760a18) -- the position-dependent exemption
        # GUARDS.md warns about. Rounded to nanometres, KiCad's own internal
        # resolution, so float formatting cannot churn ids.
        position = entry.get("pos")
        if isinstance(position, dict):
            located = tuple(
                None if not isinstance(position.get(axis), (int, float))
                else round(float(position[axis]), 6)
                for axis in ("x", "y")
            )
        else:
            located = None
        parts.append((str(uuid), description, located))
    # Hash the record's CONTENT, not just which objects it names. Keying on
    # uuids alone meant an acknowledgement survived the record changing
    # underneath it: a zone-track record could become a moved zone-via record
    # and keep both its id and its waiver (codex review of 298ed6d). The net
    # and the item descriptions are what make the record the thing that was
    # reviewed.
    payload = {
        "description": record.get("description"),
        "severity": record.get("severity"),
        "type": record.get("type"),
        "items": sorted(parts),
    }
    # json.dumps with sorted keys is an UNAMBIGUOUS encoding. The previous
    # "|".join let ["a|b", "c"] and ["a", "b|c"] hash identically without any
    # attack on SHA-256 at all.
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:16]


def classify_record(
    record: Any,
    index: int,
    pour_nets: Iterable[str],
    mixed_pour_nets: Iterable[str] = (),
    included_severities: Optional[Iterable[str]] = None,
    strict_pour: bool = False,
    reviewed_records: Iterable[str] = (),
) -> Dict[str, Any]:
    declared = frozenset(pour_nets)
    mixed = frozenset(mixed_pour_nets)
    reviewed = frozenset(reviewed_records)
    result: Dict[str, Any] = {
        "index": index,
        "record_id": record_id(record),
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
        if strict_pour and set(kinds) - {"zone"} and "zone" in kinds \
                and result["record_id"] in reviewed:
            # An acknowledgement can only ever narrow a MIXED record -- one
            # that actually names a zone -- to refill topology. It cannot make
            # a record with no zone in it into pour topology.
            #
            # REGRESSION FIXED 2026-09-07 (codex review of 298ed6d). This
            # branch shipped without the `"zone" in kinds` clause and reopened
            # the exact laundering the all-zone rule had closed one commit
            # earlier. Measured on a plain `Pad 1 [/SIG] <-> Track [/SIG]`
            # record with no zone anywhere:
            #     no declaration, no acknowledgement -> exit 4 (signal open)
            #     declaration only                   -> exit 3 (ambiguous)
            #     acknowledgement only               -> exit 4 (signal open)
            #     declaration + acknowledgement      -> exit 0   <-- a REAL
            #                                                        OPEN
            # I had argued the two flags were "two independent statements".
            # They are two statements, but both come from the caller, so they
            # are not two pieces of EVIDENCE -- and stacking two labels was
            # precisely the failure the all-zone rule exists to prevent. The
            # zone is the only physical evidence in the record; requiring one
            # keeps the acknowledgement to its actual purpose, which is the
            # zone-track/zone-via/track-via records real pours do produce.
            result["bucket"] = "pour_topology"
            result["reason"] = (
                "mixed zone record acknowledged by the caller as refill "
                "topology"
            )
            return result
        if strict_pour and set(kinds) - {"zone"}:
            # Tightened 2026-09-07 (codex review of 7a99de9). Requiring only
            # that a zone be PRESENT still let a real open through: a
            # `Zone [/SIG] <-> Track [/SIG]` record on a net the caller
            # declared as pour was classified as refill topology and exited 0,
            # even though a track stranded from its pour is authored copper
            # that does not connect. A declaration is a claim about the NET's
            # role; it is never evidence about a particular record. Under the
            # signal gate only an all-zone record -- a zone
            # island, which is what refill actually produces -- is pour
            # topology. Anything naming a pad, track or via stays ambiguous,
            # which forces exit 3 rather than a pass.
            result["reason"] = (
                f"record on declared pour net {net!r} names non-zone copper "
                f"({', '.join(sorted(set(kinds))) or 'no recognised items'}); "
                "under a signal-open gate a pad, track or via stranded from "
                "its pour is an authored-routing open, not refill topology"
            )
            return result
        if strict_pour and "zone" not in kinds:
            # A record with no zone item on a declared pure-pour net is not
            # self-evidently refill topology: a pad-to-track open is
            # authored routing, and the DRC text cannot tell them apart.
            # Classifying it as pour turned a real signal open into a PASS
            # purely because the caller labelled its net (codex review of
            # 7b00165). Only the signal gate is strict; the
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


def board_net_table(text: str) -> List[str]:
    """Every net name this board NAMES, gathered structurally.

    Not "the net table": KiCad 10 has no top-level net section at all (verified
    by walking the depth-2 heads of a real 10.0 board -- footprint, segment,
    via, zone, and no `net`). A net exists there only as a reference inside the
    objects that carry it, so the set of names the file references is the only
    inventory the file offers, and that is what this returns. On a KiCad 8
    board the numbered table entries are matched by the same expression.

    The point of the check this feeds is catching a MISSPELLED --pour-net, and
    for that "a name this board mentions" is the right question.

    A text regex matched the same expression inside a file that was not a
    board at all, so a garbage file containing that substring "declared" a net
    (codex review of 75c10fa). Structure is what tells a real mention from
    net-shaped text, and the structure required here is threefold: the root
    symbol is exactly `kicad_pcb`, the parens balance, and every accepted
    `(net ...)` sits directly inside a form that can actually carry a net.

    That last clause was missing after the KiCad 10 fix, which relaxed the
    depth restriction and accidentally accepted `(kicad_pcbx (net "/PHANTOM"))`
    and `(kicad_pcb (metadata (net "/PHANTOM")))` (codex review of 298ed6d) --
    a partial undoing of the guarantee the previous round had added.

    This is a scanner, not a full s-expression parser: it tracks depth, string
    literals, escapes and the head symbol of each open form, which is what is
    needed to answer "does this board name this net".
    """
    stripped = text.lstrip()
    root = _ROOT_SYMBOL.match(stripped)
    if not root or root.group(1) != "kicad_pcb":
        raise ConnectivityError(
            "board's root symbol is %r, not `kicad_pcb`; this is not a saved "
            "KiCad board, and a file that merely contains net-shaped text "
            "does not name nets"
            % (root.group(1) if root else stripped[:24])
        )
    nets: List[str] = []
    stack: List[Optional[str]] = []
    depth = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if char == '"':
            index += 1
            closed = False
            while index < length:
                if text[index] == "\\":
                    index += 2
                    continue
                if text[index] == '"':
                    closed = True
                    break
                index += 1
            if not closed:
                raise ConnectivityError(
                    "board contains an unterminated string; refusing to read "
                    "its nets"
                )
            index += 1
            continue
        if char == "(":
            depth += 1
            head = _ROOT_SYMBOL.match(text, index)
            symbol = head.group(1) if head else None
            parent = stack[-1] if stack else None
            stack.append(symbol)
            # A net mention counts only where a net can actually live: the
            # KiCad 8 top-level table, or -- KiCad 10, which has no table --
            # directly inside the object that carries the net.
            if symbol == "net":
                match = _NET_EXPR.match(text, index)
                if match:
                    numbered = match.group(1) is not None
                    if parent == "kicad_pcb":
                        # At the top level only the KiCad 8 NUMBERED table
                        # entry is a net table entry. A bare `(net "x")` there
                        # is not something KiCad writes, and accepting it let
                        # `(kicad_pcb # (net "/PHANTOM"))` name a net, because
                        # the stray token left the expression parented to the
                        # board itself.
                        if numbered:
                            nets.append(match.group(2))
                    elif parent in NET_BEARING_FORMS:
                        nets.append(match.group(2))
            index += 1
            continue
        if char == ")":
            depth -= 1
            if stack:
                stack.pop()
            if depth < 0:
                raise ConnectivityError(
                    "board has unbalanced parentheses; refusing to guess at "
                    "its net table"
                )
            index += 1
            continue
        index += 1
    if depth != 0:
        raise ConnectivityError(
            "board has unbalanced parentheses; refusing to guess at its net "
            "table"
        )
    return sorted(set(nets))


def find_kicad_cli(explicit: Optional[pathlib.Path] = None) -> pathlib.Path:
    """Locate kicad-cli, or refuse. A missing exporter is never a clean run."""
    if explicit is not None:
        if not (explicit.is_file() and os.access(explicit, os.X_OK)):
            raise ConnectivityError(f"--kicad-cli {explicit} is not executable")
        return explicit
    found = shutil.which("kicad-cli")
    if found:
        return pathlib.Path(found)
    for candidate in (
        "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
        "/opt/homebrew/bin/kicad-cli",
    ):
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return pathlib.Path(candidate)
    raise ConnectivityError(
        "--run-drc needs kicad-cli and none was found; pass --kicad-cli PATH"
    )


def run_drc(board: pathlib.Path, destination: pathlib.Path,
            kicad_cli: pathlib.Path) -> Dict[str, Any]:
    """Produce the DRC report ourselves, from the board we digested.

    This is the binding every metadata check was standing in for. A report the
    tool did not produce can only be tied to a board by what it says about
    itself -- a filename and a timestamp -- and both are satisfied by a report
    from a different board that happens to share the name."""
    command = [str(kicad_cli), "pcb", "drc", "--format", "json",
               "--severity-all", "--units", "mm",
               "-o", str(destination), str(board)]
    try:
        proc = subprocess.run(command, capture_output=True, text=True,
                              timeout=600)
    except (OSError, subprocess.SubprocessError) as exc:
        raise ConnectivityError(f"could not run kicad-cli: {exc}") from exc
    if proc.returncode != 0 or not destination.exists():
        raise ConnectivityError(
            "kicad-cli exited %s and produced no usable report; a DRC that "
            "did not run is not a clean DRC. stderr: %s"
            % (proc.returncode, (proc.stderr or "").strip()[:400])
        )
    return {
        "kicad_cli": str(kicad_cli),
        "command": command,
        "kicad_cli_sha256": hashlib.sha256(
            kicad_cli.read_bytes()).hexdigest() if kicad_cli.is_file() else None,
    }


def board_identity(path: pathlib.Path) -> Dict[str, Any]:
    """Digest the board, and read the net names it actually declares.

    The DRC JSON's own digest proves which REPORT bytes were parsed, never
    which board produced them. Gating fabrication on a report whose board is
    unnamed is gating on a filename."""
    try:
        with path.open("rb") as handle:
            before = os.fstat(handle.fileno())
            if before.st_size > MAX_BOARD_BYTES:
                raise ConnectivityError(
                    f"board {path} is {before.st_size} bytes, above the "
                    f"{MAX_BOARD_BYTES}-byte cap; refusing to read it rather "
                    "than risking a MemoryError outside this tool's contract"
                )
            payload = handle.read()
            after = os.fstat(handle.fileno())
        current = path.stat()
    except ConnectivityError:
        raise
    except MemoryError as exc:
        raise ConnectivityError(f"board {path} could not be read: {exc}") from exc
    except OSError as exc:
        raise ConnectivityError(f"cannot read board {path}: {exc}") from exc
    identity = lambda stat: (stat.st_dev, stat.st_ino,
                             stat.st_size, stat.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(current):
        raise ConnectivityError(f"board {path} changed while it was read")
    try:
        text = payload.decode("utf-8")
    except UnicodeError as exc:
        raise ConnectivityError(f"board {path} is not UTF-8: {exc}") from exc
    return {
        "board_path": str(path),
        "board_name": path.name,
        "board_sha256": hashlib.sha256(payload).hexdigest(),
        "board_size": len(payload),
        "board_mtime_ns": after.st_mtime_ns,
        "board_nets": board_net_table(text),
    }


def check_report_is_about_the_board(report: Any, identity: Dict[str, Any]) -> None:
    """The report's own `source` must name the board that was digested.

    KiCad writes `source` as the board file it was run on. Comparing basenames
    is deliberately weak -- it cannot prove the bytes match, only that nobody
    handed us a report for a different design. Refusing is the only safe
    action: the alternative is a zero-open report for another board exiting 0.
    """
    named = report.get("source")
    if not isinstance(named, str) or not named.strip():
        raise ConnectivityError(
            "DRC report does not name the board it describes (`source`)"
        )
    if pathlib.PurePath(named).name != identity["board_name"]:
        raise ConnectivityError(
            "DRC report is about %r but --board names %r; a report for another "
            "board is not a verdict about this one"
            % (pathlib.PurePath(named).name, identity["board_name"])
        )


def check_report_is_not_stale(report: Any, identity: Dict[str, Any],
                            skew_s: int = REPORT_DATE_SKEW_S,
                            compare_to_board: bool = True) -> None:
    """The report must not predate the board it claims to describe.

    Basename equality cannot see time: a clean report generated before the
    board was last edited still named the right file and passed (codex review
    of 75c10fa). KiCad writes `date` as a local ISO-8601 timestamp with no
    zone, so this compares it against the board's mtime in local time. It is
    a one-sided check on purpose -- a report NEWER than the board is the
    normal case, and a report OLDER than the board cannot describe its current
    bytes.
    """
    stamp = report.get("date")
    if not isinstance(stamp, str) or not stamp.strip():
        raise ConnectivityError(
            "DRC report has no usable `date`; a report that will not say when "
            "it was taken cannot be shown to describe the board as it stands"
        )
    # `Z` is valid ISO-8601 and Python 3.11+ accepts it; 3.9 -- which this
    # suite also runs under -- does not. Normalising it here means the guard
    # does not silently reject a legal timestamp on the older interpreter.
    normalised = stamp.strip()
    if normalised.endswith(("Z", "z")):
        normalised = normalised[:-1] + "+00:00"
    try:
        taken = datetime.datetime.fromisoformat(normalised)
    except ValueError as exc:
        raise ConnectivityError(
            f"DRC report `date` {stamp!r} is not an ISO-8601 timestamp: {exc}"
        ) from exc
    # Compare instants, not wall clocks. KiCad writes a naive LOCAL timestamp,
    # so a naive value is localised -- and where local time is ambiguous (the
    # hour repeated at a DST fallback) it is read as the EARLIER of the two
    # possible instants. That is the fail-closed choice: it can only make the
    # report look older, never newer, so an ambiguous timestamp errs towards
    # refusing a stale report rather than admitting one. Comparing naive local
    # values directly let a first-occurrence 02:30 report pass against a
    # second-occurrence 02:30 board an hour later (codex review of 298ed6d).
    if taken.tzinfo is None:
        taken = taken.replace(fold=0).astimezone()
    edited = datetime.datetime.fromtimestamp(
        identity["board_mtime_ns"] / 1_000_000_000, datetime.timezone.utc)
    now = datetime.datetime.now(datetime.timezone.utc)
    if taken > now + datetime.timedelta(seconds=REPORT_FUTURE_CEILING_S):
        raise ConnectivityError(
            "DRC report claims to have been taken at %s, which is in the "
            "future; a timestamp that cannot have happened yet is a broken "
            "clock, and it would satisfy any freshness check forever"
            % taken.isoformat()
        )
    # KiCad writes `date` to SECOND resolution while the board's mtime is
    # nanoseconds, so a DRC run started in the same second as the save reads
    # as up to a second older than the board it just measured. The tolerance
    # is that quantisation, nothing more -- it is deliberately far too small
    # to admit a report from an earlier editing session.
    if compare_to_board and taken < edited - datetime.timedelta(seconds=skew_s):
        raise ConnectivityError(
            "DRC report was taken %s but %s was last modified %s; a report "
            "older than the board cannot be a verdict about its current bytes "
            "-- re-run the DRC" % (taken.isoformat(), identity["board_name"],
                                   edited.isoformat())
        )


def check_pour_nets_exist(declared: Iterable[str], identity: Dict[str, Any]) -> None:
    """A declared pour net must be a net the board actually has.

    A misspelled or phantom `--pour-net` silently declared nothing, so every
    record on the net the caller MEANT stayed graded -- or, worse, a typo that
    happened to match nothing left a real pour net undeclared and the run
    unevaluable for the wrong reason. Either way the caller's declaration was
    never checked against the design."""
    unknown = sorted(set(declared) - set(identity["board_nets"]))
    if unknown:
        raise ConnectivityError(
            "declared pour net(s) %s are not nets on %s; the board declares %s"
            % (", ".join(repr(net) for net in unknown), identity["board_name"],
               ", ".join(repr(net) for net in identity["board_nets"][:12]) or "none")
        )


def provenance_notes(provenance: Dict[str, Any],
                     reviewed: Dict[str, Optional[str]]) -> List[str]:
    """One line per escape in force. Silence means none were used."""
    notes: List[str] = []
    if provenance.get("produced_by_this_tool"):
        notes.append("PROVENANCE: report produced by this tool from the board")
    if provenance.get("trusted_external_report"):
        notes.append(
            "PROVENANCE: graded a DRC report this tool did not produce; its "
            "correspondence to the board rests on a filename and a timestamp")
    if provenance.get("staleness_check_skipped"):
        notes.append(
            "PROVENANCE: the report may predate the board "
            "(--allow-report-older-than-board)")
    if provenance.get("report_cap_probed_on"):
        notes.append(
            "PROVENANCE: report cap overridden, probed on KiCad %s"
            % provenance["report_cap_probed_on"])
    if provenance.get("assumed_timezone"):
        notes.append(
            "PROVENANCE: the report's naive timestamp was read as local time "
            "in %s" % provenance["assumed_timezone"])
    for identifier, reason in sorted(reviewed.items()):
        notes.append(
            "PROVENANCE: record %s accepted as refill topology by the caller%s"
            % (identifier, ": %s" % reason if reason else
               " WITH NO REASON GIVEN"))
    return notes


def read_cap_probe(path: pathlib.Path, cap: Optional[int]) -> Dict[str, Any]:
    """Read a DRC report offered as EVIDENCE that a release does not cap.

    A version string is an assertion; this is a measurement. To show that a
    KiCad does not truncate a violation list at `cap`, produce a report from
    that KiCad containing MORE than `cap` findings of some single type. That
    is a fact about the release, checkable here, and it is what
    `--report-cap-probed-on` was standing in for while checking nothing
    (codex review of 298ed6d: "a mute is not a probe" implemented a label as
    the probe)."""
    try:
        payload = path.read_bytes()
        probe = json.loads(payload.decode("utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConnectivityError(
            f"cannot read --report-cap-probe {path}: {exc}") from exc
    if not isinstance(probe, dict):
        raise ConnectivityError("--report-cap-probe is not a DRC report")
    # Count PER TYPE, and only real records. Counting a whole list conflated
    # several types (100 parity findings of type A plus 100 of type B
    # "qualified" an override), and counting non-object entries let 200 nulls
    # qualify one (codex review of a760a18).
    by_type: Dict[str, int] = {}
    for key in ("unconnected_items", "violations", "schematic_parity"):
        rows = probe.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            kind = row.get("type")
            if not isinstance(kind, str) or not kind:
                continue
            by_type[kind] = by_type.get(kind, 0) + 1
    best = max(by_type.values(), default=0)
    # An observation is bounded, so it can only ever justify a HIGHER FINITE
    # cap. A report of M findings of one type proves the release's cap is at
    # least M; it says nothing about M+1, and is equally consistent with the
    # cap being exactly M. `none` claims no cap at all, which no finite
    # report can evidence -- so `none` is not a qualifiable override.
    if cap is None:
        raise ConnectivityError(
            "--report-cap none cannot be evidenced: a probe showing %d "
            "findings proves the cap is at least %d, never that there is no "
            "cap. Set a finite --report-cap you have actually observed"
            % (best, best)
        )
    if best + 1 < cap:
        raise ConnectivityError(
            "--report-cap-probe %s shows at most %d findings of a single "
            "type, which supports a cap of at most %d, not %d; a probe has "
            "to contain the counts it claims are possible"
            % (path, best, best + 1, cap)
        )
    return {
        "path": str(path),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "kicad_version": probe.get("kicad_version"),
        "largest_single_type_count": best,
    }


def check_cap_override_is_qualified(cap: Optional[int], report: Any,
                                    probed_on: Optional[str]) -> None:
    """An override away from the default must name the release it was probed on.

    `--report-cap none` and `--report-cap 200` silenced the cap check with no
    evidence at all, which is the mute button `../GUARDS.md` forbids: the
    report does not improve, only the warning goes away (codex review of
    75c10fa). An override now has to say which KiCad it was measured against,
    and that has to be the KiCad that wrote this report."""
    if cap == KICAD_10_REPORT_CAP:
        return
    if not probed_on:
        raise ConnectivityError(
            "--report-cap overrides the default %d, so it must be accompanied "
            "by --report-cap-probed-on <kicad_version> naming the release you "
            "measured. Without that this is a mute button, not a calibration"
            % KICAD_10_REPORT_CAP
        )
    written_by = report.get("kicad_version")
    if str(written_by) != str(probed_on):
        raise ConnectivityError(
            "--report-cap-probed-on %r does not match the KiCad that wrote "
            "this report (%r); a cap measured on another release says nothing "
            "about this one" % (probed_on, written_by)
        )


def parse_reviewed_records(values: Iterable[str]) -> Dict[str, Optional[str]]:
    """`--reviewed-record ID` or `ID=why it was accepted`.

    An acknowledgement with no stated reason is still accepted -- requiring
    prose would only produce prose -- but where one is given it is carried
    into the result, so a pass taken on a human judgement records the
    judgement alongside it (codex review of 298ed6d, finding 8)."""
    parsed: Dict[str, Optional[str]] = {}
    for value in values:
        if not isinstance(value, str) or not value.strip():
            raise ConnectivityError("--reviewed-record values must be nonempty")
        identifier, separator, reason = value.partition("=")
        identifier = identifier.strip()
        if not identifier:
            raise ConnectivityError(
                "--reviewed-record %r has no record id" % (value,))
        if identifier in parsed:
            raise ConnectivityError("duplicate --reviewed-record %r" % identifier)
        parsed[identifier] = reason.strip() if separator else None
    return parsed


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
    reviewed_records: Sequence[str] = (),
) -> Dict[str, Any]:
    if not isinstance(report, dict):
        raise ConnectivityError("DRC report root is not an object")
    required = {
        "$schema",
        "coordinate_units",
        # `source` and `date` name the board this report is ABOUT and when it
        # was taken. Both are present in every real KiCad 10.0.5 export
        # (verified against scripts/fixtures/open-net.drc.json). Without them
        # a report cannot be checked against the board being gated, and a
        # zero-open report for a DIFFERENT board exits 0 (codex review of
        # 7a99de9).
        "date",
        "ignored_checks",
        "included_severities",
        "kicad_version",
        "schematic_parity",
        "source",
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

    acknowledgements = parse_reviewed_records(reviewed_records or ())
    reviewed = list(acknowledgements)
    classified = [
        classify_record(record, index, pour_nets, mixed_pour_nets, severities,
                        strict_pour, reviewed)
        for index, record in enumerate(records)
    ]
    # A stale acknowledgement is refused, never ignored. An id that names no
    # record in this report is an acknowledgement that has outlived the record
    # it was about -- the board moved on and the waiver did not, which is
    # exactly how a stale exemption silently keeps excusing something new.
    present = {row["record_id"] for row in classified if row["record_id"]}
    orphaned = sorted(set(reviewed) - present)
    if orphaned:
        raise ConnectivityError(
            "--reviewed-record %s names no record in this report; an "
            "acknowledgement that outlived its record must be re-made, not "
            "carried forward" % ", ".join(repr(one) for one in orphaned)
        )
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
        "reviewed_records": acknowledgements,
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
    parser.add_argument("drc_json", type=pathlib.Path, nargs="?")
    parser.add_argument(
        "--run-drc",
        action="store_true",
        help=(
            "run the DRC on --board and grade THAT, instead of grading a "
            "report someone else produced. This is the only mode in which the "
            "verdict is bound to the board's bytes: the tool digests the "
            "board, invokes kicad-cli on that exact file, and classifies the "
            "output it just produced. Without it the report's correspondence "
            "to the board rests on a filename and a timestamp, which a report "
            "from another board of the same name satisfies"
        ),
    )
    parser.add_argument(
        "--kicad-cli",
        type=pathlib.Path,
        help="path to kicad-cli for --run-drc; discovered if not given",
    )
    parser.add_argument(
        "--trust-external-report",
        action="store_true",
        help=(
            "gate on a DRC report this tool did not produce. Required for a "
            "gating run without --run-drc, because a report is bound to the "
            "board only by its filename and timestamp: measured 2026-09-07, a "
            "board with 11 real opens exited 0 when handed a clean report "
            "from a different board of the same name. Recorded in the result "
            "so a pass taken on that basis is auditable"
        ),
    )
    parser.add_argument(
        "--board",
        type=pathlib.Path,
        help=(
            "the .kicad_pcb this DRC report describes. REQUIRED for a gating "
            "run: the report's own digest proves which report bytes were "
            "parsed, never which board produced them, so without this a "
            "zero-open report for a DIFFERENT board exits 0. The board is "
            "digested into the result, its `source` is checked against the "
            "report's, and every --pour-net is checked against the nets the "
            "board actually declares"
        ),
    )
    parser.add_argument(
        "--reviewed-record",
        action="append",
        metavar="ID",
        help=(
            "acknowledge ONE specific record on a declared pure-pour net as "
            "refill topology, by the `record_id` the JSON reports. Real pour "
            "nets do carry zone-track, zone-via and track-via records, so the "
            "strict gate would otherwise be unusable on them -- but a blanket "
            "net LABEL is what let a real open through, so the escape is "
            "per-record and auditable. An id naming no record in the report "
            "is refused, so an acknowledgement cannot outlive its record. "
            "Repeat as needed"
        ),
    )
    parser.add_argument(
        "--report-cap-probe",
        type=pathlib.Path,
        help=(
            "a DRC report from the same KiCad release containing MORE "
            "findings of one type than the cap you are overriding. This is "
            "the measurement --report-cap-probed-on only asserted: a version "
            "string is a label, and a label is not a probe. Required "
            "alongside --report-cap-probed-on whenever the cap is overridden"
        ),
    )
    parser.add_argument(
        "--report-cap-probed-on",
        help=(
            "the KiCad version you measured the cap on. REQUIRED whenever "
            "--report-cap differs from the default, and it must match the "
            "report's own kicad_version: a cap measured on another release "
            "says nothing about this one"
        ),
    )
    parser.add_argument(
        "--allow-report-older-than-board",
        action="store_true",
        help=(
            "accept a DRC report taken BEFORE the board was last modified. "
            "Off by default: such a report cannot describe the board's "
            "current bytes. Use only when the board's mtime moved without its "
            "content changing, and say so in the review"
        ),
    )
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
            "signal open, whatever the pour topology count. This ranks "
            "progress and closes the SIGNAL component; it is not the "
            "definition of done -- ../ROUTING.md keeps that at "
            "--require-zero-total, because an island is unfinished copper. "
            "Use it on a board whose legitimate pour records mean "
            "--require-zero-total cannot yet pass, and report the result as "
            "\"signal routing complete, board incomplete\". It also "
            "tightens classification: under this gate only an ALL-ZONE "
            "record on a declared pour net counts as pour topology. Any "
            "record naming a pad, track or via stays ambiguous (exit 3) "
            "unless you acknowledge it individually with --reviewed-record, "
            "because copper stranded from its pour is authored routing and a "
            "net declaration is not evidence about a particular record"
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
    owned_drc = None
    owned_dir = None
    try:
        if args.run_drc:
            if args.board is None:
                raise ConnectivityError("--run-drc needs --board")
            if args.drc_json is not None:
                raise ConnectivityError(
                    "--run-drc produces the report; do not also name one"
                )
            kicad_cli = find_kicad_cli(args.kicad_cli)
            owned_dir = tempfile.mkdtemp(prefix=".drc-owned-")
            produced = pathlib.Path(owned_dir) / "owned.drc.json"
            owned_drc = run_drc(args.board, produced, kicad_cli)
            args.drc_json = produced
        elif args.drc_json is None:
            raise ConnectivityError(
                "name a DRC JSON, or use --run-drc to produce one"
            )
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
        if not args.run_drc and not args.report_only \
                and not args.trust_external_report:
            raise ConnectivityError(
                "a gating run must either produce its own report (--run-drc) "
                "or say explicitly that it is trusting one it did not make "
                "(--trust-external-report). A report is tied to a board only "
                "by its filename and its timestamp, and a clean report from a "
                "different board of the same name satisfies both: measured "
                "2026-09-07, a board with 11 real opens exited 0 that way"
            )
        identity = None
        if args.board is not None:
            identity = board_identity(args.board)
        elif not args.report_only:
            # Fail closed, exactly as "no gate and no --report-only" does.
            raise ConnectivityError(
                "a gating run must name the board with --board: this tool "
                "grades a DRC JSON, and a report is bound to its own bytes, "
                "not to the copper they describe. Without the board a "
                "zero-open report for another design passes. Use "
                "--report-only to inspect a report on its own"
            )
        report, receipt = load_report(source)
        check_cap_override_is_qualified(cap, report, args.report_cap_probed_on)
        cap_probe = None
        if cap != KICAD_10_REPORT_CAP:
            if args.report_cap_probe is None:
                raise ConnectivityError(
                    "overriding --report-cap needs --report-cap-probe: a "
                    "report from that release containing more findings of one "
                    "type than the cap. Naming the version only asserts the "
                    "probe happened"
                )
            if args.report_cap_probe.resolve() == source:
                raise ConnectivityError(
                    "--report-cap-probe is the report being graded; a report "
                    "cannot be its own evidence that it was not truncated"
                )
            cap_probe = read_cap_probe(args.report_cap_probe, cap)
            if str(cap_probe["kicad_version"]) != str(args.report_cap_probed_on):
                raise ConnectivityError(
                    "--report-cap-probe was produced by KiCad %r but "
                    "--report-cap-probed-on says %r"
                    % (cap_probe["kicad_version"], args.report_cap_probed_on)
                )
        if identity is not None:
            check_report_is_about_the_board(report, identity)
            # The escape suppresses ONE comparison, not the whole check. It
            # used to skip the function outright, so `--allow-report-older-
            # than-board` also disabled ISO parsing and the future ceiling: a
            # report dated 2099 exited 0 (codex review of a760a18). An
            # unparseable or impossible timestamp is wrong regardless of which
            # side of the board's mtime it claims to fall on.
            check_report_is_not_stale(
                report, identity,
                compare_to_board=not args.allow_report_older_than_board)
            check_pour_nets_exist(list(pour_nets) + list(mixed_pour_nets),
                                  identity)
        result = classify_report(
            report, str(source), pour_nets, mixed_pour_nets,
            strict_pour=args.require_zero_signal_opens,
            cap=cap,
            reviewed_records=list(args.reviewed_record or ()),
        )
        result["strict_pour"] = bool(args.require_zero_signal_opens)
        # null rather than absent: a result that cannot name the board behind
        # its verdict must say so, not omit the field.
        result["board"] = identity
        # The report's OWN source/date, not just the JSON pathname: a result
        # that drops them cannot be re-checked against the board later.
        # Provenance of the verdict's own trust basis, so a pass taken on a
        # weaker footing is auditable rather than indistinguishable.
        result["drc_provenance"] = {
            "produced_by_this_tool": bool(args.run_drc),
            "owned_run": owned_drc,
            "trusted_external_report": bool(args.trust_external_report),
            "staleness_check_skipped": bool(args.allow_report_older_than_board),
            "report_cap_probed_on": args.report_cap_probed_on,
            "report_cap_probe": cap_probe,
            "pour_nets_explicitly_none": bool(args.no_pour_nets),
            # A naive KiCad timestamp is read as local time, so the checker's
            # zone is part of the freshness verdict: the same stale report was
            # refused under Europe/Berlin and accepted under UTC.
            "assumed_timezone": str(
                datetime.datetime.now().astimezone().tzinfo),
        }
        result["report_source"] = report.get("source")
        result["report_date"] = report.get("date")
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
    # Every escape that could have changed this verdict, on stdout, next to
    # the verdict. `--json` is optional, so provenance recorded only there was
    # provenance a CI log need never show (codex review of a760a18): a PASS
    # resting on a trusted external report or a cap override read exactly like
    # one that rested on nothing.
    for line in provenance_notes(result.get("drc_provenance") or {},
                                 result.get("reviewed_records") or {}):
        print(line)
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
