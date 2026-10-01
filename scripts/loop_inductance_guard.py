#!/usr/bin/env python3
"""Loop-inductance guard: gate high-di/dt copper loops against nH budgets.

Thin fail-closed adapter over the validated KiCad->FastHenry extractor in
dcdc-tools/parasitics (extract_parasitics.py). That tool owns geometry,
meshing, solving, and its own hard-error paths (a probe pad with no copper
contact, a floating port, an unclosable loop are extractor errors, never
silent skips). This guard adds exactly three things:

  1. one command that runs the extraction and applies explicit nH budgets
     with a machine verdict (exit 0 PASS / 2 FAIL / 3 unevaluable);
  2. fail-closed budget semantics: no budgets -> error; a budget naming a
     quantity the extraction did not produce -> FAIL, never a skip;
  3. artifact binding: a reused parasitics.json is accepted only if its
     meta.pcb_sha256 matches the SHA-256 of the board file being gated.

Budgets are DERIVED VALUES, never defaults: take them from the design's
ringing/overshoot analysis (POWER.md loop catalogs) or the datasheet-driven
budget the placement was accepted against. This tool refuses to invent one.

Binding limits: meta.pcb_sha256 ties an extraction to the board BYTES, not
to the intended loop — an extraction configured with the wrong nets/refs
measures a different loop than the budget means. Bind the extraction
CONFIG in the completion record (commit the extractor YAML per project;
parasitics.json meta.extract_config_sha256 records its hash) so a reviewer
can check budget and measurement name the same loop.

Typical loops (see dcdc-tools/parasitics/README.md for the port model):
  L_loop        effective commutation loop (all ported Cin in parallel)
  L_loop_single nearest single Cin - conservative upper bound
  L_loop_ring   commutation loop read at the ring frequency
  L_gate_hs/ls  gate-drive loops
  csi_hs/ls     common-source inductance (power di/dt into the gate)
  probe:<name>  any declared REF.PAD:REF.PAD loop (snubber lands, mount loops)
  probe_ring:<name>  the same probe loop read at the ring frequency

Frequency semantics: unsuffixed L keys are the extractor's low-MHz PLATEAU
values; *_ring keys are read at the configured ring frequency. For a
budget that comes from a ringing/overshoot analysis at a specific ring
frequency, gate the *_ring quantity (L is usually flat - measured 28.73 vs
28.73 nH plateau-vs-ring on the calibration board - but that flatness is a
result, not an assumption).

Usage:
  loop_inductance_guard.py BOARD.kicad_pcb -o OUTDIR \
      --config extraction.yaml \
      --max-nh L_loop=60 probe:snub_q3=25 csi_hs=4

  # gate an existing extraction (sha-verified against BOARD):
  loop_inductance_guard.py BOARD.kicad_pcb --json OUT/parasitics.json \
      --max-nh L_loop=60

Extractor location: $DCDC_PARASITICS (default
~/dev/pv/ee/dcdc-tools/parasitics). FastHenry: $FASTHENRY (default
~/dev/tools/fasthenry/bin/fasthenry).

Extractor provenance: the extractor stamps meta.extractor_commit and
meta.extractor_status (git status lines) into every parasitics.json. Fresh
or reused, the verdict requires that stamp, a commit containing
MIN_EXTRACTOR_COMMIT, and no uncommitted change to extractor code (tests,
docs, examples and untracked non-.py files excepted). $DCDC_PARASITICS must
be the root of that git checkout, and a fresh run's stamp must equal the
checkout's HEAD. Anything else is unevaluable; --allow-dirty-extractor
accepts uncommitted code knowingly and says so in the verdict.
"""

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from typing import NoReturn, TypeGuard

EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_UNEVALUABLE = 3

SCALAR_KEYS = {
    "L_loop", "L_loop_single", "L_loop_ideal", "L_loop_physical",
    "L_loop_ring", "L_gate_hs", "L_gate_ls", "csi_hs", "csi_ls",
}
PROBE_PREFIXES = {"probe:": "L", "probe_ring:": "L_ring"}

# Oldest dcdc-parasitics commit this guard trusts: the first that stamps its
# own provenance into meta (085948f). It also contains the fixes after which
# older extractors are not trusted: floating-port pruning (47c93c2),
# deterministic terminal bonding (5a891b9), finite-impedance terminal spokes
# instead of .equiv (f2dae41, f0ef514), and module-mode port connectivity
# validation (eab60ee). That validation covers the MODULE path only; the
# discrete path checks that required ports exist, not that P_pwr's terminals
# are connected (~/dev/kb/tooling/
# fasthenry-ports-across-disconnected-conductors.md).
MIN_EXTRACTOR_COMMIT = "085948f8c22fc1fb366d626750b4f889ee6047fd"

# Uncommitted changes under these prefixes do not alter what the extractor
# computes; the caller's own config is bound separately by its hash.
EXTRACTOR_NONCODE_PREFIXES = ("test/", "docs/", "examples/")


def die(msg, code=EXIT_UNEVALUABLE) -> NoReturn:
    print(f"UNEVALUABLE: {msg}", file=sys.stderr)
    sys.exit(code)


class GuardArgumentParser(argparse.ArgumentParser):
    """argparse's default usage-error exit code is 2, which collides with
    this guard's FAIL=2; a malformed invocation is unevaluable, not an
    electrical FAIL."""

    def error(self, message):
        self.print_usage(sys.stderr)
        die(f"bad invocation: {message}")


def is_real_number(x) -> TypeGuard[float]:
    return (isinstance(x, (int, float)) and not isinstance(x, bool)
            and math.isfinite(x))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def parse_budgets(items):
    budgets = {}
    for it in items:
        key, sep, val = it.partition("=")
        if not sep:
            die(f"budget {it!r} is not KEY=NANOHENRY")
        try:
            nh = float(val)
        except ValueError:
            die(f"budget {it!r}: {val!r} is not a number")
        if not (math.isfinite(nh) and nh > 0):
            die(f"budget {it!r}: budget must be a finite number > 0 nH "
                f"(an inf/nan budget gates nothing)")
        if not (key in SCALAR_KEYS
                or any(key.startswith(p) for p in PROBE_PREFIXES)):
            die(f"budget key {key!r} unknown; scalar keys: "
                f"{sorted(SCALAR_KEYS)}, or probe:<name> / "
                f"probe_ring:<name>")
        if key in budgets:
            die(f"budget key {key!r} given twice")
        budgets[key] = nh
    if not budgets:
        die("no budgets given - a guard run with nothing to gate is "
            "vacuous; pass at least one --max-nh KEY=NH")
    return budgets


def lookup(data, key):
    """Return (value_henries, provenance) or (None, reason).

    A value that is not a finite non-negative real number - null, bool,
    NaN, +/-inf, or negative (a physically impossible self-inductance,
    i.e. a corrupted or forged artifact) - is unevaluable, never gated.
    """
    for prefix, field in PROBE_PREFIXES.items():
        if key.startswith(prefix):
            name = key[len(prefix):]
            probes = data.get("probe_ports") or {}
            if not isinstance(probes, dict) or name not in probes:
                return None, (f"probe {name!r} absent from extraction "
                              f"(present: {sorted(probes) if isinstance(probes, dict) else '?'})")
            entry = probes[name]
            val = entry.get(field) if isinstance(entry, dict) else None
            if not (is_real_number(val) and float(val) >= 0):
                return None, (f"probe {name!r} field {field!r} is "
                              f"{val!r} - not a finite non-negative "
                              f"inductance")
            return float(val), (entry.get("label", "")
                                if isinstance(entry, dict) else "")
    val = data.get(key)
    if not (is_real_number(val) and float(val) >= 0):
        return None, (f"{key} is {val!r} in the extraction output - not "
                      f"a finite non-negative inductance")
    return float(val), ""


def git(root, *a):
    try:
        return subprocess.run(["git", "-C", root, *a],
                              capture_output=True, text=True)
    except OSError as e:
        die(f"cannot run git ({e}) - the extractor's provenance cannot be "
            f"established without it")


def extractor_checkout():
    """Return (resolved root, HEAD sha) of the extractor checkout.

    $DCDC_PARASITICS must resolve to the TOP of a git checkout that tracks
    extract_parasitics.py: git discovers repositories upward, so an
    untracked copy nested inside some checkout would otherwise inherit that
    checkout's commit (Codex review of 342b5e7, finding 1).
    """
    root = os.path.realpath(os.environ.get(
        "DCDC_PARASITICS",
        os.path.expanduser("~/dev/pv/ee/dcdc-tools/parasitics")))
    if not os.path.isfile(os.path.join(root, "extract_parasitics.py")):
        die(f"extractor not found at {root} (set $DCDC_PARASITICS)")
    top = git(root, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        die(f"extractor at {root} is not a git checkout "
            f"({top.stderr.strip()}) - its version cannot be established")
    if os.path.realpath(top.stdout.strip()) != root:
        die(f"extractor at {root} is inside the checkout "
            f"{top.stdout.strip()} but is not its root - a copy there "
            f"would inherit that checkout's commit")
    if git(root, "ls-files", "--error-unmatch",
           "extract_parasitics.py").returncode != 0:
        die(f"extract_parasitics.py is not tracked in {root}")
    head = git(root, "rev-parse", "HEAD")
    if head.returncode != 0:
        die(f"git rev-parse HEAD failed in {root}: {head.stderr.strip()}")
    return root, head.stdout.strip()


def require_min_commit(root, commit):
    anc = git(root, "merge-base", "--is-ancestor", MIN_EXTRACTOR_COMMIT,
              commit)
    if anc.returncode == 1:
        die(f"extractor commit {commit[:12]} is older than "
            f"{MIN_EXTRACTOR_COMMIT[:12]}, the minimum this guard trusts; "
            f"update the extractor and re-extract")
    if anc.returncode != 0:
        # 128: a commit unknown here (shallow clone, another repository) -
        # "not proven older" is not "proven newer"
        die(f"cannot establish that extractor commit {commit[:12]} "
            f"contains {MIN_EXTRACTOR_COMMIT[:12]}: {anc.stderr.strip()}")


def is_code_change(line):
    """A `git status --porcelain` line that can change what is computed.

    A rename names both endpoints ("R  a -> b") and either one can be code:
    docs/x.py -> lib/x.py adds code, lib/x.py -> docs/x.md removes it. Paths
    with spaces come quoted, so strip the quotes before testing the suffix
    (Codex review of d43ba52, finding 2).
    """
    xy = line[:2]
    for path in line[3:].split(" -> "):
        path = path.strip('"')
        if path.startswith(EXTRACTOR_NONCODE_PREFIXES) or path.endswith(".md"):
            continue
        if xy == "??" and not path.endswith(".py"):
            continue
        return True
    return False


# What can be imported from a directory on the extractor's sys.path (root
# and lib/): modules, extension modules, and regular packages. Keep in step
# with _IMPORTABLE_GLOBS in dcdc-parasitics extract_parasitics.py, which
# stamps the same set.
IMPORTABLE_GLOBS = ("*.py", "*.pyc", "*.so", "*/__init__.py",
                    "*/__init__.pyc", "*/__init__.so")


def importable_strays(root):
    """Untracked modules where the extractor imports from, ignored or not.

    `git status` never lists ignored files, and an ignored lib/numpy.py
    shadows the real one (Codex review of d43ba52, finding 1); nor does the
    porcelain filter keep an untracked .so or a package that has only an
    __init__.pyc (Codex review of 5466ec5, finding 1). `ls-files --others`
    without exclude options lists untracked files whether ignored or not.
    __pycache__ is Python's own cache, validated against its source.
    """
    pats = [f":(glob){d}{p}" for d in ("", "lib/") for p in IMPORTABLE_GLOBS]
    r = git(root, "ls-files", "--others", "--", *pats)
    if r.returncode != 0:
        die(f"git ls-files failed in {root}: {r.stderr.strip()}")
    return [f"!! {p}" for p in r.stdout.splitlines()]


def checkout_code_changes(root):
    st = git(root, "status", "--porcelain", "--untracked-files=all")
    if st.returncode != 0:
        die(f"git status failed in {root}: {st.stderr.strip()}")
    return ([s for s in st.stdout.splitlines() if is_code_change(s)]
            + importable_strays(root))


def check_provenance(meta, root, allow_dirty, expect_commit=None):
    """Validate the extractor stamp in meta; return a printable summary.

    An unstamped extraction is unevaluable, fresh or reused: an ancestry
    check on the checkout says nothing about the code that produced a
    stored artifact (Codex review of 342b5e7, findings 2 and 3).
    """
    commit = meta.get("extractor_commit")
    status = meta.get("extractor_status")
    if not (isinstance(commit, str) and commit
            and isinstance(status, list)
            and all(isinstance(s, str) for s in status)):
        die(f"extraction carries no extractor provenance "
            f"(meta.extractor_commit={commit!r}) - it predates "
            f"{MIN_EXTRACTOR_COMMIT[:12]} or its extractor could not run "
            f"git; re-extract")
    if expect_commit is not None and commit != expect_commit:
        die(f"the extraction was stamped {commit[:12]} but the extractor "
            f"checkout is at {expect_commit[:12]} - the output was not "
            f"produced by the checked code")
    require_min_commit(root, commit)
    code = [s for s in status if is_code_change(s)]
    if code and not allow_dirty:
        die(f"extractor {commit[:12]} ran with {len(code)} uncommitted "
            f"code change(s) ({', '.join(s[3:] for s in code[:5])}"
            f"{', ...' if len(code) > 5 else ''}) - the result is not bound "
            f"to a revision. Commit them, or pass --allow-dirty-extractor "
            f"knowingly")
    summary = f"{commit[:12]} (>= {MIN_EXTRACTOR_COMMIT[:12]})"
    if code:
        summary += (f" with {len(code)} UNCOMMITTED code change(s), "
                    f"accepted by --allow-dirty-extractor: not bound to a "
                    f"revision")
    return summary, bool(code)


def run_extractor(root, board, outdir, config, extra_args,
                  allow_unchanged=False):
    tool = os.path.join(root, "extract_parasitics.py")
    env = dict(os.environ)
    # a caller's PYTHONPATH puts modules from outside the checkout ahead of
    # the site ones - a clean checkout would then not be the code that ran
    # (Codex review of d43ba52, finding 1). The extractor puts lib/ on its
    # own path and needs nothing from the environment. User site-packages
    # (and their .pth / usercustomize hooks) are likewise outside the
    # checkout; the module example extracts identically without them
    # (3.67 nH, Codex review of 5466ec5, finding 2). The interpreter's own
    # site-packages are the installation, and are not covered.
    for var in ("PYTHONPATH", "PYTHONHOME", "PYTHONSTARTUP",
                "PYTHONUSERBASE"):
        env.pop(var, None)
    env["PYTHONNOUSERSITE"] = "1"
    env.setdefault("FASTHENRY", os.path.expanduser(
        "~/dev/tools/fasthenry/bin/fasthenry"))
    if not os.path.isfile(env["FASTHENRY"]):
        die(f"FastHenry binary not found at {env['FASTHENRY']}")
    # the extractor runs with cwd=root, so caller-relative paths must be
    # absolutized or they resolve against the wrong directory
    board = os.path.abspath(board)
    outdir = os.path.abspath(outdir)
    cmd = [sys.executable, tool, board, "-o", outdir]
    if config:
        if not os.path.isfile(config):
            die(f"--config {config!r} not found")
        cmd += ["--config", os.path.abspath(config)]
    cmd += extra_args
    jpath = os.path.join(outdir, "parasitics.json")
    # a pre-existing artifact must not be able to masquerade as this
    # run's output if the extractor exits 0 without (re)writing it
    pre_digest = sha256(jpath) if os.path.isfile(jpath) else None
    print("+ " + " ".join(cmd))
    r = subprocess.run(cmd, cwd=root, env=env)
    if r.returncode != 0:
        die(f"extractor exited {r.returncode} - no verdict without a "
            f"completed extraction")
    if not os.path.isfile(jpath):
        die(f"extractor exited 0 but wrote no {jpath}")
    if pre_digest is not None and not allow_unchanged \
            and sha256(jpath) == pre_digest:
        # Metadata is not content. This compared (mtime_ns, size) until a
        # stub extractor that only touched the file passed a stale 0.1 nH
        # result (codex review of 7b00165). A byte-identical result from a
        # genuinely deterministic re-extraction is indistinguishable from
        # an extractor that did nothing, so it is unevaluable and the
        # caller must say knowingly which one it was.
        die(f"extractor exited 0 but {jpath} is byte-identical to the file "
            f"that was there before the run - it may not have been "
            f"rewritten at all. Delete it and re-extract, or pass "
            f"--allow-unchanged-extraction if the extractor is known "
            f"deterministic")
    return jpath


def main():
    ap = GuardArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("board", help=".kicad_pcb to gate")
    ap.add_argument("--json", help="existing parasitics.json to gate "
                    "(sha-verified against BOARD) instead of extracting")
    ap.add_argument("-o", "--out", help="extraction output dir "
                    "(required unless --json)")
    ap.add_argument("--allow-unchanged-extraction", action="store_true",
                    help="accept an extractor run that left its output "
                         "byte-identical; only for an extractor known to "
                         "be deterministic, and never to silence a stub")
    ap.add_argument("--allow-dirty-extractor", action="store_true",
                    help="accept an extraction made with uncommitted "
                         "extractor code changes; the verdict then states "
                         "it is not bound to a revision")
    ap.add_argument("--config", help="extractor YAML (sw/gnd/vin/"
                    "probe_ports/... - the reproducible per-project way)")
    ap.add_argument("--max-nh", nargs="+", required=True,
                    metavar="KEY=NH", help="budgets in nanohenries; keys: "
                    "L_loop, L_loop_single, L_gate_hs, L_gate_ls, csi_hs, "
                    "csi_ls, probe:<name>")
    ap.add_argument("extractor_args", nargs="*", default=[],
                    help="extra args passed to extract_parasitics.py "
                    "after '--' (e.g. -- --sw /SW2 --gnd /GND_PWR)")
    args = ap.parse_args()

    budgets = parse_budgets(args.max_nh)

    if not os.path.isfile(args.board):
        die(f"board {args.board!r} not found")
    board_sha = sha256(args.board)

    # a reused extraction needs the checkout too: its stamped commit is
    # checked for ancestry against the minimum there
    root, head = extractor_checkout()
    own = []
    if args.json:
        jpath = args.json
        if not os.path.isfile(jpath):
            die(f"--json {jpath!r} not found")
    else:
        if not args.out:
            die("need -o OUTDIR to run the extraction (or --json to gate "
                "an existing one)")
        # refuse an old or dirty checkout before spending minutes on a
        # solve; the dirty check is also our own reading, not the stamp's
        require_min_commit(root, head)
        own = checkout_code_changes(root)
        if own and not args.allow_dirty_extractor:
            die(f"extractor checkout {root} has {len(own)} uncommitted code "
                f"change(s) ({', '.join(s[3:] for s in own[:5])}"
                f"{', ...' if len(own) > 5 else ''}) - commit them, or pass "
                f"--allow-dirty-extractor knowingly")
        jpath = run_extractor(root, args.board, args.out, args.config,
                              args.extractor_args,
                              args.allow_unchanged_extraction)
        # a solve takes minutes and other sessions edit this checkout; the
        # extractor samples its stamp once, so re-read HEAD and the tree
        # after the run (Codex review of d43ba52, finding 4)
        _, head_after = extractor_checkout()
        if head_after != head:
            die(f"extractor HEAD moved during the run ({head[:12]} -> "
                f"{head_after[:12]}) - the code that ran is not identified")
        after = checkout_code_changes(root)
        if after != own:
            die(f"extractor checkout changed during the run "
                f"({len(own)} -> {len(after)} code change(s)) - the code "
                f"that ran is not identified; re-extract")

    try:
        data = json.load(open(jpath))
    except Exception as e:
        die(f"cannot parse {jpath}: {e}")
    if not isinstance(data, dict):
        die(f"{jpath} does not contain a JSON object "
            f"(got {type(data).__name__}) - not a parasitics.json")

    meta = data.get("meta") or {}
    if not isinstance(meta, dict):
        die(f"{jpath} meta is not an object")
    extractor, dirty = check_provenance(
        meta, root, args.allow_dirty_extractor,
        expect_commit=None if args.json else head)
    if not args.json and own and not dirty:
        # the guard saw code changes the stamp did not record; an override
        # must not turn that into an unqualified PASS (Codex review of
        # 5466ec5, finding 3)
        dirty = True
        extractor += (f" with {len(own)} UNCOMMITTED code change(s) seen by "
                      f"the guard but missing from the stamp, accepted by "
                      f"--allow-dirty-extractor: not bound to a revision")
    if args.json and not args.config:
        # meta.pcb_sha256 binds the BOARD BYTES, never the loop the budget
        # means: an extraction of the same board configured for the wrong
        # nets, refs or probes carries a matching hash and used to PASS
        # (codex review of 7b00165). PCB.md requires the config binding as
        # part of the completion record, so the executable requires it too.
        die("--json needs --config: meta.pcb_sha256 binds the board bytes "
            "only, so without the extractor config a reused extraction of "
            "the right board measuring the WRONG loop passes. PCB.md makes "
            "this binding part of the completion record")
    if args.json and args.config:
        # bind a reused extraction to its config, not just the board bytes
        if not os.path.isfile(args.config):
            die(f"--config {args.config!r} not found")
        cfg_sha = sha256(args.config)
        ext_cfg_sha = meta.get("extract_config_sha256")
        if not ext_cfg_sha:
            die(f"{jpath} carries no meta.extract_config_sha256 - it was "
                f"not extracted through --config, so it cannot be bound "
                f"to {args.config}; re-extract via --config")
        if ext_cfg_sha != cfg_sha:
            die(f"reused extraction was made with a DIFFERENT config: "
                f"meta.extract_config_sha256={ext_cfg_sha[:16]}.. but "
                f"{args.config} hashes {cfg_sha[:16]}.. - it may measure "
                f"a different loop than the budget means")
    ext_sha = meta.get("pcb_sha256")
    if not ext_sha:
        die(f"{jpath} carries no meta.pcb_sha256 - cannot bind the "
            f"extraction to the board being gated")
    if ext_sha != board_sha:
        die(f"extraction is for a DIFFERENT board: parasitics.json "
            f"pcb_sha256={ext_sha[:16]}.. but {args.board} hashes "
            f"{board_sha[:16]}.. - re-extract from the saved board")

    freq = data.get("freq_Hz")
    # A *_ring budget compares against a quantity that only MEANS anything
    # at a stated ring frequency. `freq_Hz` was read for display only, so a
    # SHA-matched extraction with no frequency at all gated `L_loop_ring`
    # and printed "freq None Hz" (codex review of 7b00165). Naming the
    # field is not establishing the measurement.
    wants_ring = any(key.endswith("_ring") or key.startswith("probe_ring:")
                     for key in budgets)
    if wants_ring and not is_real_number(freq):
        die(f"a ring-frequency budget was given, but {jpath} carries no "
            f"usable freq_Hz (got {freq!r}) - a *_ring quantity is only a "
            f"ring-frequency measurement if the extraction states the "
            f"frequency it was read at")
    if wants_ring and freq <= 0:
        die(f"freq_Hz={freq} is not a positive frequency")
    failures = []
    print(f"\nloop-inductance gate on {args.board}")
    print(f"  extraction: {jpath} (freq {freq} Hz, board sha "
          f"{board_sha[:16]}..)")
    print(f"  extractor: {extractor}")
    warns = [w for k in ("reduce_warn", "reduce_warn_base")
             for w in (data.get(k) or [])]
    for w in warns:
        print(f"  WARN  {w}")
    if warns:
        print(f"  ({len(warns)} extractor warning(s) above - they degrade "
              f"the numbers being gated; resolve or justify before "
              f"trusting a marginal PASS)")
    for key, max_nh in sorted(budgets.items()):
        val, note = lookup(data, key)
        if val is None:
            failures.append(key)
            print(f"  FAIL  {key:<24} UNEVALUABLE: {note}")
            continue
        nh = val * 1e9
        status = "PASS" if nh <= max_nh else "FAIL"
        if status == "FAIL":
            failures.append(key)
        print(f"  {status}  {key:<24} {nh:8.2f} nH  (budget {max_nh} nH)"
              f"{'  ' + note if note else ''}")

    if failures:
        print(f"\nFAIL: {len(failures)}/{len(budgets)} budget(s) violated "
              f"or unevaluable: {', '.join(sorted(failures))}")
        sys.exit(EXIT_FAIL)
    print(f"\nPASS: all {len(budgets)} budget(s) met"
          f"{' - by an extractor with UNCOMMITTED code changes, not release evidence' if dirty else ''}")
    sys.exit(EXIT_PASS)


if __name__ == "__main__":
    main()
