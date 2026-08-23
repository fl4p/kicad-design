#!/usr/bin/env python3
"""Run the KiCad verification ladder so that a PASS means something.

Project-agnostic. Nothing here knows about any particular board.

False PASSes this exists to close
---------------------------------
1.  **``--exit-code-violations`` is not optional.** Without it,
    ``kicad-cli sch erc`` and ``pcb drc`` write every violation into the report
    and then **exit 0**. Measured: a board carrying 175 DRC violations exits 0
    bare and 5 with the flag. A CI step, a ``set -e`` script, or an agent that
    "asserted the exit status is 0" passes a board it never checked. This
    module always passes the flag *and* independently parses the counts out of
    the report, so neither alone is load-bearing.

2.  **"ERC = 0" is a statement about the severity map as much as the
    schematic.** ``.kicad_pro`` carries ``erc.rule_severities``; a rule set to
    ``ignore`` is not resurrected by ``--severity-all``. Worse, that map is
    **sparse and frequently absent entirely** -- measured across four real
    projects: 0, 0, 33 and 44 entries. Enumerating ``ignore``s over a missing
    map yields ``[]``, which reports "no rules are ignored" at the exact moment
    you know least, while KiCad's built-in defaults are fully in force.

    So :func:`severity_report` treats the project's sparse maps as configured
    overrides, never as a complete rule universe. It returns ``UNVERIFIED``
    unless the caller also supplies a complete, version-bound resolution of
    the effective ERC and DRC maps.

3.  **A requested parity check may not run.** KiCad 10 can print that it
    failed to fetch the schematic netlist, still write an all-zero DRC report,
    and exit zero. Parity therefore requires a same-stem project/schematic,
    an independently exported and parsed annotated netlist, no parity-failure
    diagnostic, and the report's footprint-error summary. That category is
    present even without parity, so release use also requires a compatibility-
    cell negative control with a deliberate board/schematic mismatch.

Do not lead with "diff against defaults": the rules most likely to bite are
themselves stock defaults, so a diff reports no difference and the check that
was supposed to catch them fires never.
"""

from __future__ import annotations

import atexit
import json
import os
import re
import shutil
import subprocess
import tempfile
import sys
from pathlib import Path

from _util import read_utf8 as _read_utf8
from kicad_netlist import NetlistError, parse_netlist

__all__ = [
    "VerifyError",
    "find_kicad_cli",
    "run_erc",
    "run_drc",
    "severity_report",
    "KNOWN_STOCK_IGNORES",
]


class VerifyError(AssertionError):
    """A rung of the ladder could not be run, or its result cannot be trusted."""


#: Rules observed at ``ignore`` in KiCad's own built-in defaults. Provenance:
#: measured on KiCad 9.0.4 during the review that produced this module. These
#: are *stock* defaults, which is precisely why diffing a project's map against
#: defaults does not surface them. **Re-verify against your KiCad version**
#: before relying on the list; it is a prompt to look, not an authority.
KNOWN_STOCK_IGNORES = (
    "footprint_filter",
    "four_way_junction",
    "simulation_model_issue",
    "single_global_label",
)

def _version_key(path):
    """Sort key that orders 10.0 ABOVE 9.0.

    Reverse *lexical* sorting gives 9.0, 8.0, 10.0 and picks an old install.
    """
    import re as _re
    return [int(x) for x in _re.findall(r"\d+", str(path))] or [0]


def _candidate_clis():
    """Well-known install locations, per platform.

    `shutil.which` is tried first and handles PATHEXT on Windows, but KiCad's
    Windows installer does not put its bin directory on PATH, so the glob
    below is what actually finds it there.
    """
    if sys.platform == "darwin":
        return ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"]
    if os.name == "nt":
        out = []
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                     os.environ.get("ProgramFiles(x86)", "")):
            if base:
                out += [str(q) for q in
                        sorted(Path(base).glob("KiCad/*/bin/kicad-cli.exe"),
                               key=_version_key, reverse=True)]
        return out
    # NOTE: the Flatpak GUI export (org.kicad.KiCad) is NOT kicad-cli; the
    # real invocation is `flatpak run --command=kicad-cli org.kicad.KiCad`,
    # which this discovery cannot express as a single path. Pass it via the
    # `cli` argument if you use Flatpak.
    return ["/usr/bin/kicad-cli", "/usr/local/bin/kicad-cli",
            "/snap/bin/kicad.kicad-cli"]


def _is_kicad_cli(path):
    """Prove the thing at `path` really is kicad-cli, not just a file."""
    p = Path(path)
    # os.access(X_OK) is meaningless on Windows (it reports True for any
    # existing file), which is why identity is proved by running --version
    # below rather than by the mode bits.
    if not p.is_file():
        return False
    if os.name != "nt" and not os.access(p, os.X_OK):
        return False
    try:
        out = subprocess.run([str(p), "--version"], capture_output=True,
                             text=True, encoding="utf-8",
                             errors="replace", timeout=20)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and bool(
        re.search(r"\d+\.\d+", (out.stdout or "") + (out.stderr or "")))


def find_kicad_cli(explicit=None):
    """Locate `kicad-cli`. It is not on PATH by default on macOS.

    Existence is not identity: the previous version accepted any path that
    existed, including a README.
    """
    if explicit:
        if not _is_kicad_cli(explicit):
            raise VerifyError(
                "%s is not a runnable kicad-cli (--version did not report a "
                "version)" % explicit)
        return explicit
    onpath = shutil.which("kicad-cli")
    if onpath and _is_kicad_cli(onpath):
        return onpath
    for c in _candidate_clis():
        if _is_kicad_cli(c):
            return c
    raise VerifyError(
        "kicad-cli not found on PATH or at any known install location for "
        "%s. Pass its path explicitly. (macOS: inside KiCad.app/Contents/"
        "MacOS; Windows: Program Files\\KiCad\\<ver>\\bin\\kicad-cli.exe -- "
        "neither is on PATH by default.)" % sys.platform)


#: DRC writes "** Found N unconnected pads **"; ERC writes
#: "** ERC messages: N  Errors N  Warnings N". Both shapes must parse, and a
#: file matching NEITHER must raise -- "I found no violation lines" is not the
#: same as "there are no violations".
_COUNT_DRC = re.compile(r"\*\*\s*Found\s+(\d+)\s+(.+?)\s*\*\*", re.I)
_COUNT_ERC = re.compile(
    r"ERC messages:\s*(\d+)\s+Errors\s+(\d+)\s+Warnings\s+(\d+)", re.I)
_PARITY_FAILURE = re.compile(
    r"failed to fetch schematic netlist|schematic parity tests require",
    re.I,
)


def _counts_from_report(path, kind=None):
    """Parse the violation counts out of an ERC or DRC report.

    Returns {label: count}. Raises when neither shape is present.
    """
    p = Path(path)
    if not p.exists():
        raise VerifyError("report %s was not written" % p)
    txt = _read_utf8(p, VerifyError)

    found = {}
    for m in _COUNT_DRC.finditer(txt):
        label = m.group(2).strip().lower()
        if label in found and found[label] != int(m.group(1)):
            raise VerifyError(
                "%s reports '%s' twice with different counts (%d then %d) -- "
                "a dict comprehension would keep the last and could turn a "
                "real violation count into 0" % (p, label, found[label],
                                                 int(m.group(1))))
        found[label] = int(m.group(1))
    # finditer, not search: a report carrying two ERC summaries (a stale
    # zero one followed by the real one) would otherwise return only the
    # first and read as clean.
    ercs = list(_COUNT_ERC.finditer(txt))
    if ercs:
        vals = {(int(m.group(1)), int(m.group(2)), int(m.group(3)))
                for m in ercs}
        if len(vals) > 1:
            raise VerifyError(
                "%s carries %d conflicting ERC summaries %s -- refusing to "
                "pick one" % (p, len(ercs), sorted(vals)))
        msgs, errs, warns = vals.pop()
        found["erc messages"], found["errors"], found["warnings"] = (
            msgs, errs, warns)
    # A report must carry the labels its tool is known to emit. Without
    # this, "** Found 0 bananas **" alone parses as a clean DRC run.
    if kind == "drc":
        need = {"drc violations", "unconnected pads"}
        missing = need - set(found)
        if missing:
            raise VerifyError(
                "%s is missing required DRC summary line(s): %s -- a report "
                "that omits a category cannot be called clean (truncated?)"
                % (p, ", ".join(sorted(missing))))
    elif kind == "erc":
        if "errors" not in found:
            raise VerifyError(
                "%s has no 'ERC messages: N Errors N Warnings N' line" % p)
    if not found:
        raise VerifyError(
            "%s matches neither the DRC ('** Found N ... **') nor the ERC "
            "('ERC messages: N Errors N Warnings N') shape -- cannot "
            "establish the violation count, so this run is UNVERIFIED rather "
            "than clean. Did the report format change?" % p)
    return found


#: ERC writes "** Ignored checks:" and DRC writes "** Ignored checks **".
#: Accept both; requiring the colon silently made every DRC report read as
#: "no such section", i.e. UNKNOWN, which is the right failure but the wrong
#: reason.
_IGNORED_HDR = re.compile(r"\*\*\s*Ignored checks\s*(?::|\*\*)\s*$", re.I | re.M)


def ignored_checks_from_report(path):
    """The checks KiCad reports it actually skipped, from the report itself.

    This is stronger evidence than `.kicad_pro`'s `rule_severities`: the map is
    sparse and often absent, whereas the report states what the run really
    applied -- including built-in defaults the map never mentions. Returns a
    list of human-readable check descriptions (possibly empty, which here
    genuinely means "none were skipped", because the header is present).

    Returns None if the report has no 'Ignored checks' section at all, i.e.
    the question is UNANSWERED rather than answered "none".
    """
    p = Path(path)
    if not p.exists():
        raise VerifyError("report %s was not written" % p)
    lines = _read_utf8(p, VerifyError).splitlines()
    idx = next((i for i, ln in enumerate(lines)
                if _IGNORED_HDR.search(ln.rstrip())), None)
    if idx is None:
        return None
    out = []
    for ln in lines[idx + 1:]:
        s = ln.strip()
        if s.startswith("-"):
            out.append(s.lstrip("- ").strip())
        elif s.startswith("**") or (s and not s.startswith("-")):
            break
    none_markers = [value for value in out if value.casefold() == "none"]
    if none_markers:
        if len(out) != 1:
            raise VerifyError(
                "%s lists the ignored-check sentinel 'None' together with "
                "real checks -- the section is contradictory" % p)
        return []
    return out


def _run(cmd):
    proc = subprocess.run(cmd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace")
    return proc.returncode, proc.stdout, proc.stderr


def _run_producing(cmd, report):
    """Run `cmd`, requiring it to (re)write `report` during this call.

    Otherwise a stale zero-count report left by an earlier run is parsed and
    reported as a clean result for a tool invocation that wrote nothing.
    """
    rp = Path(report)
    before = rp.stat().st_mtime_ns if rp.exists() else None
    rc, out, err = _run(cmd)
    if not rp.exists():
        raise VerifyError(
            "%s did not write %s -- nothing to parse, so this run is "
            "UNVERIFIED" % (cmd[1] if len(cmd) > 1 else cmd[0], rp))
    if before is not None and rp.stat().st_mtime_ns == before:
        raise VerifyError(
            "%s was not rewritten by this run (mtime unchanged) -- refusing "
            "to report a stale report as a fresh result" % rp)
    return rc, out, err


def run_erc(schematic, report="erc.rpt", cli=None):
    """ERC with --severity-all --exit-code-violations.

    Returns (rc, counts). Raises if the report is missing or unparseable.
    Both signals are returned; callers should require rc == 0 AND every count
    == 0, because each has been seen to be wrong alone.
    """
    cli = find_kicad_cli(cli)
    if not Path(schematic).exists():
        raise VerifyError("schematic %s does not exist" % schematic)
    rc, _out, err = _run_producing(
        [cli, "sch", "erc", "--severity-all", "--exit-code-violations",
         "-o", str(report), str(schematic)], report)
    counts = _counts_from_report(report, kind="erc")
    if rc not in (0, 5):
        raise VerifyError("kicad-cli sch erc failed (rc=%d): %s" % (rc, err[:400]))
    return rc, counts


def _verify_parity_context(board, cli):
    """Require and independently parse the schematic context used by parity.

    KiCad can return a clean DRC report and exit zero after failing to load the
    schematic netlist for parity.  Prove that the same-stem project context
    exists and that a fresh, nonempty annotated netlist can be exported before
    asking DRC for the parity comparison.
    """
    board = Path(board)
    project = board.with_suffix(".kicad_pro")
    schematic = board.with_suffix(".kicad_sch")
    missing = [p.name for p in (project, schematic) if not p.is_file()]
    if missing:
        raise VerifyError(
            "schematic parity for %s requires same-stem project context; "
            "missing: %s. Use parity=False only for an explicitly authorized "
            "board-only workflow" % (board, ", ".join(missing)))

    with tempfile.TemporaryDirectory(prefix="kicad-parity-") as raw:
        exported = Path(raw) / "parity.net"
        rc, out, err = _run([
            cli, "sch", "export", "netlist", "--format", "kicadsexpr",
            "-o", str(exported), str(schematic),
        ])
        if rc != 0:
            raise VerifyError(
                "could not export the fresh schematic netlist required for "
                "parity (rc=%d): %s" % (rc, (err or out)[:400]))
        if not exported.is_file():
            raise VerifyError(
                "schematic netlist export returned success but did not write "
                "%s" % exported)
        try:
            netlist = parse_netlist(exported, min_components=1)
        except NetlistError as exc:
            raise VerifyError(
                "fresh schematic netlist for parity is empty, unannotated, "
                "or unparseable: %s" % exc) from exc
        unannotated = sorted(ref for ref in netlist.components if "?" in ref)
        if unannotated:
            raise VerifyError(
                "fresh schematic netlist for parity is not fully annotated; "
                "unresolved references: %s"
                % ", ".join(unannotated[:12]))


def run_drc(board, report="drc.rpt", parity=True, cli=None,
            expected_board_snapshot=None, board_snapshotter=None):
    """DRC with zone refill, violation status, and fail-closed parity.

    `parity` defaults True: footprint/symbol field mismatches are invisible
    without it, and they are how a schematic-only edit silently leaves the
    board contradicting the schematic.  True requires same-stem `.kicad_pro`
    and `.kicad_sch` files plus a fresh parseable annotated netlist.  Pass
    False only for an explicitly authorized board-only workflow.

    A fabrication-release call must also provide both
    ``expected_board_snapshot`` (the provisional complete semantic snapshot)
    and ``board_snapshotter`` (a callable that reparses the saved board and
    returns all non-zone objects plus per-zone filled geometry in the same
    canonical shape). That mode adds ``--save-board`` and rejects a clean
    report when KiCad's persisted board differs. Run it only on the isolated
    scratch release bundle, then hash the post-DRC board as release authority.
    """
    board = Path(board)
    if not board.exists():
        raise VerifyError("board %s does not exist" % board)
    if (expected_board_snapshot is None) != (board_snapshotter is None):
        raise VerifyError(
            "expected_board_snapshot and board_snapshotter must be supplied "
            "together; otherwise refill equality is UNVERIFIED")
    if board_snapshotter is not None and not callable(board_snapshotter):
        raise VerifyError("board_snapshotter must be callable")
    cli = find_kicad_cli(cli)
    if parity:
        _verify_parity_context(board, cli)
    cmd = [cli, "pcb", "drc", "--severity-all", "--refill-zones",
           "--exit-code-violations", "-o", str(report)]
    if board_snapshotter is not None:
        cmd.append("--save-board")
    if parity:
        cmd.append("--schematic-parity")
    cmd.append(str(board))
    rc, out, err = _run_producing(cmd, report)
    diagnostics = ((out or "") + "\n" + (err or "") + "\n" +
                   _read_utf8(report, VerifyError))
    if parity and _PARITY_FAILURE.search(diagnostics):
        raise VerifyError(
            "KiCad did not execute schematic parity even though DRC produced "
            "a report: %s" % diagnostics.strip()[:400])
    if board_snapshotter is not None:
        try:
            observed_board_snapshot = board_snapshotter(board)
        except Exception as exc:
            raise VerifyError(
                "could not reparse the DRC-saved board for semantic comparison: "
                "%s" % exc) from exc
        if observed_board_snapshot != expected_board_snapshot:
            raise VerifyError(
                "DRC returned a report but its persisted board semantics "
                "differ from the provisional finalizer snapshot; reports and "
                "exports are invalid until the scratch candidate is finalized "
                "again")
    counts = _counts_from_report(report, kind="drc")
    if parity and "footprint errors" not in counts:
        raise VerifyError(
            "%s is missing the required 'footprint errors' report-format "
            "category; this category alone does not prove parity executed"
            % report)
    if rc not in (0, 5):
        raise VerifyError("kicad-cli pcb drc failed (rc=%d): %s" % (rc, err[:400]))
    return rc, counts


def severity_report(kicad_pro, effective_rule_maps=None):
    """Report configured overrides and, when supplied, effective severities.

    Returns a dict::

        {"state": "verified" | "unverified",
         "configured_erc_ignored": [...],
         "configured_drc_ignored": [...],
         "configured_erc_entries": int,
         "configured_drc_entries": int,
         "effective_erc_ignored": [...],
         "effective_drc_ignored": [...],
         "effective_erc_entries": int,
         "effective_drc_entries": int,
         "kicad_version": str | None,
         "note": str}

    ``.kicad_pro`` rule maps are sparse overrides even when nonempty, so the
    project file alone can never make this report ``verified``. To establish
    that state, pass ``effective_rule_maps`` with ``complete is True``, a
    nonempty ``kicad_version``, and nonempty ``erc`` and ``drc`` maps resolved
    for that exact KiCad compatibility cell. This function validates that
    attestation and checks it against explicit project overrides; the caller
    remains responsible for proving that its rule universe is complete.

    :data:`KNOWN_STOCK_IGNORES` is a starting list, not an authority.
    """
    p = Path(kicad_pro)
    if not p.exists():
        raise VerifyError("%s does not exist" % p)
    try:
        pro = json.loads(_read_utf8(p, VerifyError).lstrip("\ufeff"))
    except json.JSONDecodeError as e:
        raise VerifyError("%s is not valid JSON: %s" % (p, e))

    invalid_maps = []
    if not isinstance(pro, dict):
        pro = {}
        invalid_maps.append("project root is not an object")

    erc_section = pro.get("erc")
    if erc_section is None:
        erc_section = {}
    elif not isinstance(erc_section, dict):
        invalid_maps.append("erc is not an object")
        erc_section = {}
    erc_raw = erc_section.get("rule_severities")
    if erc_raw is None:
        erc = {}
    elif not isinstance(erc_raw, dict):
        invalid_maps.append("erc.rule_severities is not an object")
        erc = {}
    else:
        erc = erc_raw

    board_section = pro.get("board")
    if board_section is None:
        board_section = {}
    elif not isinstance(board_section, dict):
        invalid_maps.append("board is not an object")
        board_section = {}
    settings = board_section.get("design_settings")
    if settings is None:
        settings = {}
    elif not isinstance(settings, dict):
        invalid_maps.append("board.design_settings is not an object")
        settings = {}
    drc_raw = settings.get("rule_severities")
    # Some versions put it at the top level of the board section.
    if drc_raw is None:
        drc_raw = board_section.get("rule_severities")
    if drc_raw is None:
        drc = {}
    elif not isinstance(drc_raw, dict):
        invalid_maps.append("DRC rule_severities is not an object")
        drc = {}
    else:
        drc = drc_raw

    out = {
        "configured_erc_entries": len(erc),
        "configured_drc_entries": len(drc),
        "configured_erc_ignored": sorted(
            k for k, v in erc.items() if v == "ignore"),
        "configured_drc_ignored": sorted(
            k for k, v in drc.items() if v == "ignore"),
        "effective_erc_entries": 0,
        "effective_drc_entries": 0,
        "effective_erc_ignored": [],
        "effective_drc_ignored": [],
        "kicad_version": None,
    }
    legal_configured = {"error", "warning", "ignore", "exclusion", "unset"}
    bad_configured = {k: v for m in (erc, drc) for k, v in m.items()
                      if (not isinstance(k, str) or not isinstance(v, str)
                          or v not in legal_configured)}
    if invalid_maps:
        out["state"] = "unverified"
        out["note"] = (
            "severity configuration has an invalid shape: %s; effective "
            "severities cannot be established"
            % "; ".join(invalid_maps))
    elif bad_configured:
        out["state"] = "unverified"
        out["note"] = (
            "configured map contains %d entr%s with an invalid rule name or "
            "non-KiCad severity (%s); effective severities cannot be "
            "established"
            % (len(bad_configured),
               "y" if len(bad_configured) == 1 else "ies",
               ", ".join("%s=%r" % kv
                         for kv in sorted(bad_configured.items())[:4])))
    elif effective_rule_maps is None:
        missing = [name for name, values in (("ERC", erc), ("DRC", drc))
                   if not values]
        out["state"] = "unverified"
        out["note"] = (
            "project rule_severities maps are sparse overrides%s; KiCad's "
            "defaults and the complete rule universe are unresolved. A "
            "nonempty sparse map is not a complete effective map, and an "
            "empty configured-ignore list means 'unknown', not 'nothing "
            "ignored'. Supply a complete version-bound effective-rule "
            "resolution. Stock defaults known to sit at ignore include: %s "
            "(verify against your KiCad version)."
            % ((" (absent/empty for " + ", ".join(missing) + ")")
               if missing else "",
               ", ".join(KNOWN_STOCK_IGNORES)))
    else:
        resolution_errors = []
        if not isinstance(effective_rule_maps, dict):
            resolution_errors.append("resolution is not an object")
            resolved_erc = {}
            resolved_drc = {}
            version = None
        else:
            if effective_rule_maps.get("complete") is not True:
                resolution_errors.append("complete is not true")
            version = effective_rule_maps.get("kicad_version")
            if not isinstance(version, str) or not version.strip():
                resolution_errors.append("kicad_version is missing")
                version = None
            resolved_erc = effective_rule_maps.get("erc")
            resolved_drc = effective_rule_maps.get("drc")
            if not isinstance(resolved_erc, dict) or not resolved_erc:
                resolution_errors.append("ERC effective map is absent/empty")
                resolved_erc = {}
            if not isinstance(resolved_drc, dict) or not resolved_drc:
                resolution_errors.append("DRC effective map is absent/empty")
                resolved_drc = {}

        legal_effective = {"error", "warning", "ignore", "exclusion"}
        for label, resolved in (("ERC", resolved_erc),
                                ("DRC", resolved_drc)):
            invalid = {k: v for k, v in resolved.items()
                       if (not isinstance(k, str) or not isinstance(v, str)
                           or v not in legal_effective)}
            if invalid:
                resolution_errors.append(
                    "%s effective map has invalid entries: %s"
                    % (label, ", ".join("%s=%r" % kv for kv in
                                       sorted(invalid.items())[:4])))

        for label, configured, resolved in (
                ("ERC", erc, resolved_erc), ("DRC", drc, resolved_drc)):
            for rule, configured_value in configured.items():
                if rule not in resolved:
                    resolution_errors.append(
                        "%s effective map omits configured rule %s"
                        % (label, rule))
                elif (configured_value != "unset"
                      and resolved[rule] != configured_value):
                    resolution_errors.append(
                        "%s effective %s=%s conflicts with configured %s"
                        % (label, rule, resolved[rule], configured_value))

        if resolution_errors:
            out["state"] = "unverified"
            out["note"] = (
                "effective-rule resolution is invalid: %s"
                % "; ".join(resolution_errors))
        else:
            out.update({
                "effective_erc_entries": len(resolved_erc),
                "effective_drc_entries": len(resolved_drc),
                "effective_erc_ignored": sorted(
                    k for k, v in resolved_erc.items() if v == "ignore"),
                "effective_drc_ignored": sorted(
                    k for k, v in resolved_drc.items() if v == "ignore"),
                "kicad_version": version.strip(),
            })
            out["state"] = "verified"
            out["note"] = (
                "complete effective maps supplied for KiCad %s and checked "
                "against configured project overrides" % version.strip())
    return out


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        raise SystemExit(
            "usage: kicad_verify.py <project-dir-or-.kicad_pro> "
            "[schematic] [board]")
    target = Path(sys.argv[1])
    pro = target if target.suffix == ".kicad_pro" else next(
        iter(sorted(target.glob("*.kicad_pro"))), None)
    if pro is None:
        raise SystemExit("no .kicad_pro found at %s" % target)
    base = pro.with_suffix("")
    sch = Path(sys.argv[2]) if len(sys.argv) > 2 else base.with_suffix(".kicad_sch")
    pcb = Path(sys.argv[3]) if len(sys.argv) > 3 else base.with_suffix(".kicad_pcb")

    sev = severity_report(pro)
    print("severity map: %s (configured ERC %d entries, DRC %d entries)"
          % (sev["state"].upper(), sev["configured_erc_entries"],
             sev["configured_drc_entries"]))
    if sev["configured_erc_ignored"]:
        print("  configured ERC ignores:",
              ", ".join(sev["configured_erc_ignored"]))
    if sev["configured_drc_ignored"]:
        print("  configured DRC ignores:",
              ", ".join(sev["configured_drc_ignored"]))
    if sev["state"] == "verified":
        print("  effective maps: KiCad %s, ERC %d entries, DRC %d entries"
              % (sev["kicad_version"], sev["effective_erc_entries"],
                 sev["effective_drc_entries"]))
        if sev["effective_erc_ignored"]:
            print("  effective ERC ignores:",
                  ", ".join(sev["effective_erc_ignored"]))
        if sev["effective_drc_ignored"]:
            print("  effective DRC ignores:",
                  ", ".join(sev["effective_drc_ignored"]))
    if sev["state"] == "unverified":
        print("  !!", sev["note"])

    bad = sev["state"] == "unverified"
    tmp = tempfile.mkdtemp(prefix="kicad_verify_")
    atexit.register(shutil.rmtree, tmp, True)   # success AND exception paths
    for label, art, runner, rpt in (
            ("ERC", sch, run_erc, str(Path(tmp) / "erc.rpt")),
            ("DRC", pcb, run_drc, str(Path(tmp) / "drc.rpt"))):
        if not art.exists():
            print("%s: %s missing -- UNVERIFIED" % (label, art.name))
            bad = True
            continue
        rc, counts = runner(art, rpt)
        print("%s rc=%d  %s" % (label, rc, counts))
        bad |= rc != 0 or any(counts.values())
        skipped = ignored_checks_from_report(rpt)
        if skipped is None:
            print("  !! %s report states no 'Ignored checks' section -- "
                  "effective skips UNKNOWN" % label)
            bad = True
        elif skipped:
            print("  %s actually skipped %d check(s): %s"
                  % (label, len(skipped), "; ".join(skipped)))
    raise SystemExit(1 if bad else 0)
