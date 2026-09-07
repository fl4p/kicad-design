# Reusable KiCad verification and routing helpers

Use these helpers for project-agnostic parsing, verification, reproducibility, and qualified
autorouting. Keep board-specific electrical and physical requirements in the project and calibrate
them through [`../GUARDS.md`](../GUARDS.md).

Run command examples from the skill repository root unless a section says otherwise.

## Contents

- [Choose the helper](#choose-the-helper)
- [Apply the common contracts](#apply-the-common-contracts)
- [Use verification helpers](#use-verification-helpers)
- [Run an incremental footprint swap](#run-an-incremental-footprint-swap)
- [Use the autorouting boundary](#use-the-autorouting-boundary)
- [Onboard a project](#onboard-a-project)
- [Respect the limitations](#respect-the-limitations)

## Choose the helper

| module | use it for |
|---|---|
| `kicad_backend.py` | resolve the board-access backend (`swig` or `ipc`) explicitly and report it: the choice comes from `--backend`, then `$KICAD_BACKEND`, then the default, and both the name and the SOURCE of the choice appear in the caller's verdict line and JSON report. There is no fallback — a named backend that is unavailable, or that lacks a capability the caller requires, raises with a stable failure ID (`backend-unknown`, `swig-no-interpreter`, `swig-configured-interpreter-bad`, `ipc-client-missing`, `ipc-no-kicad-cli`, `ipc-no-headless-server`, `ipc-no-open-document`, `ipc-backend-not-implemented`, `backend-missing-capability`) and the caller must treat it as UNEVALUABLE. **Only `swig` can serve anything today**: the released IPC API is served solely by a running KiCad GUI and its protocol cannot open a file from disk, so the `ipc` probe always refuses — and it refuses with `ipc-backend-not-implemented` even on a host that satisfies every precondition, because no IPC implementation was written and none could be verified against a live server. Run it as a script (`python3 scripts/kicad_backend.py`) to see what each backend would do on this host. Evidence and migration plan: [`../plans/SWIG-to-IPC-migration.md`](../plans/SWIG-to-IPC-migration.md); calibration: `test_kicad_backend.py` |
| `kicad_graphics.py` | internal complete semantic serialization of footprint graphic shapes and text |
| `kicad_netlist.py` | parse KiCad netlists across supported pretty-print formats and reject empty or inconsistent exports |
| `kicad_symlib.py` | resolve inherited symbols, common unit 0, body styles, and pin transforms |
| `kicad_verify.py` | run ERC/DRC safely and resolve ignored checks from reports plus project configuration |
| `kicad_route_shape.py` | routing-shape audit on a saved board: per-net via maximum and total, via layer-span histogram, segment-length distribution and short-segment fraction, arc count, per-layer copper length, and per-layer direction conformance against a declared `--layer-direction`. Grades the per-net **maximum** (`--max-vias-on-any-net`), never the mean — the mean over nets-carrying-copper is diluted by via-free nets and is report-only. Arc length comes from `GetLength()`, not the chord. Full-stack via fraction is `unevaluable` on a 2-layer board, where every ordinary via spans the outer pair by construction. Fail closed: `--report-only` grades nothing (exit 0, explicit NOT-GRADED line, never valid as a gate); a gating run needs at least one threshold; a threshold naming an unevaluable metric FAILS; an out-of-domain threshold (a fraction outside [0,1], a negative count) is a vacuous gate and is rejected as a configuration error; and the `--json` report named last on the command line is invalidated before argparse can reject it — the parser sets `allow_abbrev=False`, because with abbreviation on `--jso stale.json --bogus` was accepted as `--json`; the invalidation scan matches abbreviations too, so the abbreviation is REJECTED as an argument and the report it named is still invalidated -- rejecting the invocation alone left the prior `pass` report standing, which is the stale-clean-report failure `../GUARDS.md` forbids. `kicad_drc_connectivity.py` holds the same two-part contract, in its prescan and its input/output alias guard. The report writer replaces its target outright, so it refuses any target that is not absent or a `kicad_route_shape` report — `BOARD --json BOARD` destroyed the board under review before that check existed. Each dilutable **maximum** fraction has an undilutable absolute companion (`--max-full-stack-vias`, `--max-short-segments`); a fraction threshold alone can be flipped to a pass by added copper without removing any offending geometry, so a gate meant to survive a growing board pairs them. `--min-direction-conformance` is the exception and has no companion: it is a length fraction with a floor, and compliant copper added elsewhere raises it without touching the offending copper — pair it with a review of the per-layer lengths rather than trusting it alone. `--short-segment-mm` defines the short-segment metric rather than grading one, so it can dilute both short-segment gates without touching the board; it must be finite and > 0, and when a short-segment gate is graded it must also exceed the board's own shortest segment, or no segment can ever count as short and the gate cannot fail. Vacuity is measured against the board, not a fixed window -- a constant floor refused a real board carrying four segments below 0.05 mm. A layer carrying copper with no `--layer-direction` declaration is UNEVALUABLE under `--min-direction-conformance`, not silently ungraded. Count thresholds must be whole numbers. An argparse usage error exits 1, not argparse's default 2, so a typo cannot read as a failed threshold. Tests run from this directory (`cd scripts && python3 -m unittest discover -p 'test_*.py'`), as for every other suite here; they are not discoverable from the repository root. Ships no default thresholds — none are corpus-calibrated; see [`../ROUTING.md`](../ROUTING.md). The JSON report records the board's bytes as well as its path: `source.sha256`/`source.size`/`source.mtime_ns` are digested before the measurement and re-digested after it, and a board whose content differs between those two reads is UNEVALUABLE rather than graded as a mixture of two revisions. The limit is stated in the tool docstring and is real: `pcbnew.LoadBoard` is handed the PATHNAME and opens the file a second time, so equal hashes bound the run without sealing it — an A→B→A swap around the load is graded as B and reported under A's digest. It is a same-run consistency check on an uncontended file, not a defence against an adversary editing the board mid-audit; sealing it needs a loader that reads from bytes, which SWIG does not offer — without that binding a `pass` written for one revision still reads as a verdict about whatever now sits at that path. `source` is null (never absent) until the digest is taken, so a null `source` beside a graded verdict is itself the signal. A board that cannot be digested is unevaluable, because a read that failed is not an observation. Takes `--backend` and names the resolved backend in every verdict line and in the JSON report's `backend` field (null, never absent, before it is resolved); a requested backend that is unavailable or lacks a required capability is UNEVALUABLE with a stable failure ID and never falls back. Calibration record in the tool docstring and `test_kicad_route_shape.py` |
| `kicad_drc_connectivity.py` | fail-closed classifier for a fresh full-severity KiCad JSON DRC report that does not ignore `unconnected_items`: exact `--pour-net` names make every record on those pure-pour nets topology; `--mixed-pour-net` keeps every record ambiguous when authored routing and refill-owned work share a net; `--no-pour-nets` explicitly declares none. Non-pour records without zones are signal opens; zones on undeclared nets plus missing/mismatched net tokens remain ambiguous. Reports signal, pour, ambiguous and aggregate totals; a total AT OR ABOVE the per-type report cap is unevaluable — `==` broke monotonicity (198 passed, 199 was unevaluable, 200 passed again), and the 199 itself was measured for `silk_overlap`/`silk_over_copper` on KiCad 10.0.5, never for `unconnected_items`; `--report-cap N|none` is the escape once a release has actually been probed, and a malformed value is a configuration error rather than a silently disabled check; Fail closed on the gate as well as the measurement: `--require-zero-signal-opens` grades the signal side alone -- and tightens classification while it does, because the pour declaration is caller-supplied and is not evidence: under that gate only an **all-zone** record — a zone island, which is what a refill actually produces — counts as pour topology; a record naming any pad, track or via stays **ambiguous** (exit 3), because copper stranded from its pour is authored routing and the DRC text cannot tell it from a refill artefact. Requiring merely that a zone be PRESENT was not enough: a `Zone [/SIG] ↔ Track [/SIG]` record on a net declared `--pour-net /SIG` still exited 0. Relabelling a real open's net as pure pour turned exit 4 into exit 0 before this — the gate a board with legitimate pour records can actually use, since `--require-zero-total` can never pass on one — `--require-zero-total` gates the original aggregate completion criterion, and a run with neither gate and no explicit `--report-only` is a configuration error (exit 2), not a pass. A gating run must name the board with `--board`: this tool grades a DRC JSON, and `source_sha256` binds the report's own bytes, never the copper they describe — without it a zero-open report for a *different* design exited 0. The board is digested into the result (`board.board_sha256`), the report's own `source` must name it, and every declared pour net must be a net the board actually has, so a misspelled `--pour-net` can no longer declare nothing silently. `--report-only` may still inspect a report on its own. `source` and `date` are now required report keys; both are present in every real KiCad 10.0.5 export. The JSON records `signal_open_gate` and `aggregate_gate` separately from measurement evaluability. Accepts KiCad's `mm`, `in`, and `mils` JSON coordinate units; calibration: `test_kicad_drc_connectivity.py`, including an artefact tier — `fixtures/open-net.kicad_pcb` is a real board with two real opens and `fixtures/open-net.drc.json` is its real `kicad-cli pcb drc` export, re-run against the installed KiCad when one is present so a release that changes the report format is noticed rather than silently tolerated |
| `kicad_copper_collisions.py` | fail-closed certain-short audit: tracks/arcs/vias/pads of different nets whose effective shapes touch or overlap on a shared copper layer. Takes `--backend` and names the resolved backend in the verdict line and in the JSON report's `backend` field (null, never absent, when the run died before resolving one). `swig` only — `GetEffectiveShape`/`Collide` has no IPC equivalent in any KiCad examined, so an IPC port of this tool is a rewrite over polygons and needs its own calibration |
| `kicad_functional_proximity.py` | fail-closed satellite→anchor placement tripwire: verifies every footprint declaring an `Anchor` binding (with per-binding `MaxDist`, optional `SelfPad`/`AnchorPad` selectors) sits within its pad-to-pad budget; binding fields without `Anchor`, vacuous runs, and `--expect` mismatches are all UNVERIFIED — release runs pass `--expect=ref:anchor:maxdist[:selfpad:anchorpad],...` binding the full captured tuple, never a bare refdes list, and the capture side must refuse to emit fields containing `:` or `,`, whitespace at an entry's outer edge, or a present-but-empty selector property (see [`POWER.md`](../POWER.md)); calibration harness: `tests/test_functional_proximity.py` |
| `copper_guards.py` | copper-quality checks DRC cannot express, on a saved FILLED **2-layer** board: `vias` grades each via's annular-ring contact fraction against unioned same-net zone+pad copper per layer, plus a spoke criterion that measures the widest strip crossing the WHOLE annulus -- an angular interval covered at every radius from the drill edge to the pad edge, not arc length at one radius, because a radially thin sliver hugging the mid radius produced a long arc and passed (KiCad connectivity and via_dangling are binary — a 2% sliver "connects"; PASS needs a track through the center or contact fraction ≥ threshold), `resistance` solves terminal-pair DC resistance over the net's rasterized copper model (pad-shape/via-R approximations and 2-layer scope documented in the docstring) with **mandatory** `--max-mohm` gating; both fail closed (unfilled board, unfilled declared zone on the graded net, missing pad, off-copper terminal, disconnected pair, zero gradeable subjects, non-finite/out-of-domain parameter → error/INF, never PASS); calibration record in the tool docstring (known-bad sliver vias fail at 0.02/0.07; 66-via known-good board clean); **not stdlib-only** -- the measurement paths need numpy and shapely, `resistance` also scipy, and an absent stack exits 1 FAIL-CLOSED at the point of use rather than crashing in an import; the `--json` report is owned (a target that is not a `copper_guards` report is refused, so `BOARD --json BOARD` can no longer replace the board with JSON -- measured 2026-09-07 destroying a 343,731-byte board), written atomically, carries the graded board's SHA-256, and is invalidated before argparse can reject a malformed command line; exit contract 0 PASS / 1 fail-closed or unevaluable / 2 the gate FAILED, with argparse usage errors mapped to 1 so a missing flag is never mistaken for a board that failed (`GuardArgumentParser`, ported from `loop_inductance_guard.py`); CLI calibration in `test_copper_guards.py`; the executable backstop for the DC copper-capacity gate in `PCB.md` — required on every declared power path before the Completed-PCB gate, `--max-mohm` derived from the project current budget |
| `loop_inductance_guard.py` | Tier-2 loop-inductance gate for high-di/dt copper loops (commutation, gate-drive, snubber lands, CSI): a fail-closed adapter over the validated KiCad→FastHenry extractor in `dcdc-tools/parasitics` (`$DCDC_PARASITICS`), gating explicit nH budgets (`--max-nh L_loop=.. probe:<name>=.. csi_hs=..`) with exit 0/2/3 = pass/fail/unevaluable; budgets are derived values the caller must supply (no defaults, no budgets → error, absent quantity → FAIL); a reused `parasitics.json` is accepted only when its `meta.pcb_sha256` matches the gated board **and** `--config` binds `meta.extract_config_sha256` (mandatory on the reuse path -- the board hash binds the BYTES, so an extraction of the right board configured for the wrong loop used to pass); a `*_ring` budget additionally requires a positive `freq_Hz`, because naming the field is not establishing the measurement; and after a fresh extraction the output must differ in CONTENT, not merely in mtime and size -- a stub that only touched the file passed a stale 0.1 nH result -- with `--allow-unchanged-extraction` as the deliberate, documented override for a deterministic extractor; extractor warnings are surfaced; calibrated on the gemini3p8flash fast-leg commutation loop (28.7 nH, pitch 3.0 vs 2.0 drift 0.1%) |
| `kicad_footprint_swap.py` | orchestrate a deadline-bound, adapter-owned multi-target footprint migration and recoverable promotion |
| `kicad_repro.py` | bind reproducibility evidence to outputs actually produced and detect replacement after verification; `frozen` additionally holds a COMMITTED artefact against today's generator, restoring the tracked bytes and exiting 3 on any difference |
| `kicad_open_probe.py` | fail-closed precondition: is a KiCad editor holding this artefact? Reads KiCad's `~<name>.lck` lock file and enumerates its IPC sockets; tri-state (0 NOT-HELD / 1 HELD / 2 UNKNOWN) and never reaches NOT-HELD without having read the directory. Writes nothing |
| `design_ledger.py` | `LEDGERS`-tier re-grade of the design's load-bearing inequalities against the parts actually on the BOM: provenance-tagged variables (`user\|derived\|picked\|datasheet`) with required origin citations, separate intent and as-built verdicts, uncorrelated interval arithmetic for the verdict with a symbol-correlated diagnostic beside it |
| `kicad_autoroute.py` | load strict autoroute configuration and shared route/report contracts |
| `kicad_autoroute_tools.py` | verify or explicitly install the pinned Freerouting/JRE toolchain |
| `kicad_route_candidate.py` | create a scratch candidate, enforce route scope, and emit a review report |
| `vision_probe.py` | blind image round-trip proving the serving can actually see images (skill-wide session gate, `SKILL.md` "Vision is a precondition" / `SETUP.md`) |
| `kicad_route_manifest.py` | promote a reviewed candidate through explicit digest approval |
| `kicad_autoroute_scaffold.py` | generate and verify project-owned autoroute configuration, adapters, applicators, and audits |

Use `--help` on the argparse CLIs: `kicad_repro.py`, `kicad_footprint_swap.py`,
`kicad_autoroute_tools.py`, `kicad_route_candidate.py`, `kicad_route_manifest.py`,
`kicad_autoroute_scaffold.py`, `copper_guards.py` (and its subcommands),
`kicad_drc_connectivity.py`, `kicad_route_shape.py`, and `loop_inductance_guard.py`.
The remaining modules expose a small positional diagnostic or are import-only:

```sh
python3 scripts/kicad_netlist.py NETLIST.net
python3 scripts/kicad_symlib.py SYMBOL_LIBRARY.kicad_sym SYMBOL_NAME
python3 scripts/kicad_verify.py PROJECT_DIR_OR_FILE.kicad_pro
python3 scripts/kicad_copper_collisions.py BOARD.kicad_pcb   # re-execs under KiCad's python
python3 scripts/kicad_backend.py                            # what each backend would do here
```

`kicad_copper_collisions.py` is the executable backstop for the route-readiness audit in
`PCB.md`: run it after every generated routing pass, before interpreting DRC. Never grade copper by
KiCad's `shorting_items` count alone: crossed same-layer tracks file under `tracks_crossing` and
near-touches under `clearance`, so `shorting_items = 0` can be reported on a board that still
carries cross-net copper contacts (measured: an agent drove `shorting_items` 66&rarr;0 over ~12 h
while this audit still found 18 cross-net contacting item pairs — a different counting unit than
DRC violation records). Do not adjudicate a DRC copper finding by
re-deriving track coordinates in reasoning; if a finding seems wrong, check it with this audit or
another executable tool, never by overruling the tool from memory. Exit 0 means
audited-clean, 1 means unevaluable (missing/unloadable board, nothing to audit, or no usable
pcbnew interpreter — none of which is a pass), 2 means certain shorts. It checks tracks, arcs,
vias, and pads via `GetEffectiveShape` on each shared copper layer with a probed 1-IU touch
clearance (`Collide(…, 0)` misses exact tangency on 10.0.5); zone fills are excluded because
the filler owns them, and NPTH pads count only where flashed. With `--json`, a fresh
"unevaluable / audit did not complete" placeholder is written before evaluation and replaced by
the real verdict (with the board path) only when the audit finishes, so a crash or bogus
interpreter cannot leave a stale clean report; exit 0 from a re-executed worker is trusted only
with the printed OK verdict line. Calibrated 2026-08-26 on KiCad 10.0.5 against a board with
DRC-confirmed shorts (295 collisions, exit 2) and a clean 1761-item production board
(0 collisions, exit 0).

Treat examples here as workflow illustrations; the script parser, module API, and tracked schemas
are authoritative.

## Apply the common contracts

Expect helpers to fail closed on inputs they cannot establish:

- Parse KiCad text as strict UTF-8 and require the expected root expression.
- Distinguish absence (`None`) from an empty-but-verified result where the API permits absence.
- Reject duplicate or contradictory component, net, pin, severity, and report records.
- Require nonempty subject counts when the caller claims coverage.
- Hash through stable file descriptors and detect path replacement or mutation during verification.
- Require fresh ERC/DRC reports written by the current command.
- Use scratch copies for saves, fills, routing, imports, and diagnostics.
- Emit stable failure details that distinguish “tool did not run,” “input was unverified,” and
  “outputs differ.”

Do not hoist board physics into these modules. Rail limits, amplifier windows, matched networks,
isolation domains, decoupling loops, guarded copper, and device-runtime contracts belong to the
board that owns those requirements.

## Use verification helpers

### Parse netlists with `kicad_netlist.py`

Use the parser after a fresh KiCad export. Set a positive component floor appropriate to the
design, require every parsed net to contain nodes, and compare the parsed net-block count with the
input structure. The parser masks quoted strings before locating S-expression openers so text such
as `(net ...)` inside a property does not fabricate a net.

`Netlist.net_of()` and `Netlist.field()` may return `None` for a genuinely absent lookup. Callers
must decide whether absence is legal; do not translate it automatically into an empty success.

### Resolve symbols with `kicad_symlib.py`

Use it to resolve `extends`, select body style, union common unit 0 with the requested unit, and
enumerate supported pin transforms. Require pins when the selected component must be electrical;
pinless logos and mechanical outlines can be legitimate.

`transform_pin()` rejects angles outside the supported grid, but enumeration is not KiCad ground
truth. Run `calibration_plan()` and compare its cells with an exported netlist from the project
before trusting generated connectivity.

### Run checks with `kicad_verify.py`

Use `run_erc()` and `run_drc()` so `--exit-code-violations`, report freshness, command-level zone
refill and required report labels remain load-bearing. Parity-enabled `run_drc()` requires same-stem
`.kicad_pcb`, `.kicad_pro` and `.kicad_sch` files, independently exports and parses a fresh annotated
netlist, rejects known KiCad parity-load diagnostics, and requires the footprint-error report
category. That category does not prove parity ran: KiCad emits it without parity too. Qualify every
supported KiCad cell with a real same-stem annotated negative control whose scratch board deliberately
omits or mismatches a footprint, and require nonzero footprint errors. Pass `parity=False` only for an
explicitly authorized board-only workflow and preserve that waiver in the release record.

For fabrication release, call `run_drc()` on the isolated scratch bundle with both a provisional
`expected_board_snapshot` and a project `board_snapshotter` that reparses all saved non-zone objects
and per-zone filled geometry. This adds `--save-board` and rejects the report when the persisted
semantics differ. Only then hash the post-DRC board as the authoritative release input. Without those
arguments, the helper has run a refill but has not established equality with release geometry. Treat
exit zero as command completion, then judge the structured report.

Resolve ignored checks from two sources:

1. The report's `Ignored checks` section states what KiCad skipped during that run.
2. `.kicad_pro` states sparse configured overrides and provides a cross-check; even a nonempty map
   does not enumerate KiCad defaults or the complete rule universe.

`severity_report()` therefore returns `UNVERIFIED` for project maps and for caller-supplied
`effective_rule_maps`. A dict that calls itself complete cannot bind the executed KiCad version,
authoritative rule inventory, compatibility evidence, or generated report. A future `VERIFIED` path
must consume those artifacts from a compatibility-qualified resolver. A missing or merely nonempty
sparse map is never proof that no defaults are ignored.

### Prove reproducibility with `kicad_repro.py`

Use a command that produces explicit output paths, require successful runs, and compare complete
artefact digests. Use `stable_digest()` and `verify_unchanged_since()` at the handoff boundary so a
concurrent writer cannot replace the artefact between verification and release unnoticed.

A matching digest proves byte identity for the tested outputs. It does not prove that an unexercised
branch, cache key, or physical model is correct on another input.

For release, inventory every produced file in a canonical receipt with path, type, size and SHA-256;
bind authorization to the receipt digest and call `verify_unchanged_since()` immediately before
transfer. An input-manifest digest stored beside an unhashed output does not prevent replacement.

### Hold a committed artefact frozen with `kicad_repro.py frozen`

`check` answers "is the generator deterministic *today*". It cannot answer "does today's generator
still produce the artefact that is *committed*" — run it on a tree whose generator has since drifted
and it passes happily, because both of its runs drifted together. `frozen` answers the second
question: it copies the tracked bytes aside and verifies the copy, runs the generator ONCE, requires
the mtime to move (a generator that dies leaves the file untouched, which is byte-identical to a
perfect pass), compares the result exactly, and on any difference restores the tracked bytes from the
verified copy, keeps the regenerated file as `<name>.regenerated`, and exits **3** naming both paths
and the first differing byte. Exit 2 stays reserved for "the check could not be run", because a
harness fault and a moved artefact need different fixes.

```sh
python3 scripts/kicad_repro.py frozen board.kicad_pcb -- python3 gen_board.py
```

The comparison is exact bytes and no tolerance will be added. A KiCad upgrade or a formatting change
in the generator therefore fails this gate — which is the correct verdict, since the committed
artefact really is no longer what the generator produces — and the fix is to regenerate and commit
deliberately with the toolchain change recorded, never to widen the comparison. The tool this idea
came from compares at 0.01 mm, so a footprint displaced by 9 µm passes its frozen check; it also
fails on trailing zeros alone, which is what made its users abandon the mode. Establish `check` on a
generator before gating it with `frozen`: a non-deterministic generator fails `frozen` every run for
a reason that has nothing to do with the committed artefact.

### Refuse to write a board KiCad has open, with `kicad_open_probe.py`

Run before any script that rewrites a tracked artefact. Tri-state and deliberately asymmetric: exit 0
NOT-HELD, 1 HELD, 2 UNKNOWN. It reads KiCad's own `~<name>.lck` lock file (per-path, but carrying no
pid and no timestamp — so a lock left by a crashed KiCad is byte-identical to a live one, and HELD
means *refuse to write*, never *a human is there*) and enumerates KiCad's IPC sockets in `/tmp/kicad`
(live, but path-blind without `kipy`, and the API server is off by default so an absent socket is
worth nothing). Neither signal can prove a file is free, so no branch reaches NOT-HELD without having
positively read the directory: an unreadable parent, an unlistable socket directory, or any live
socket is UNKNOWN. The probe writes nothing — in particular it does not switch KiCad's API server on
to make itself work, which is what the tool this was reimplemented from does.

### Re-grade the design arithmetic against the BOM with `design_ledger.py`

`LEDGERS` tier. Reads a JSON ledger, not a board: every load-bearing quantity with its provenance tag
(`user | derived | picked | datasheet`), a required free-text `origin` citation, the interval the
design intended (`spec`), and the interval the chosen part actually delivers (`actual`). Every
constraint is evaluated in both tiers; a release gates on **as-built**. An intent FAIL is a design
error and an as-built FAIL under an intent PASS is a sourcing error, and they are fixed by different
people, so the two verdicts are printed separately and never merged.

```sh
python3 scripts/design_ledger.py project/design-ledger.json --tier=as-built --json=out/ledger.json
```

Fail-closed throughout: a missing `actual` is UNVERIFIED and is never inherited from `spec` nor
omitted from the report; an undefined variable, a zero-constraint ledger, an untagged `source`, a
variable or constraint with no `origin`, and a division by an interval spanning zero all refuse;
UNVERIFIED outranks FAIL. Every violation prints the requirement, the constraint's origin, and the
origin and source tag of each variable on both sides.

The verdict comes from **uncorrelated** interval arithmetic, which is always an outer bound, so the
gate can be pessimistic but never kind. A **symbol-correlated** value is printed beside it whenever a
variable appears more than once; see [`../POWER.md`](../POWER.md), "A tolerance does not cancel
against itself unless it is the same part", for what to do when they disagree.

## Run an incremental footprint swap

`kicad_footprint_swap.py` requires Python 3.9 or newer and owns the project-level deadline,
typed adapter boundary, target set, concurrent-input rehash, journal, rollback/recovery, and
aggregate report. Board physics and KiCad
mutation stay in a project adapter:

```sh
python3 scripts/kicad_footprint_swap.py \
  --spec project/footprint-swap.json \
  --time-budget 180             # dry-run
python3 scripts/kicad_footprint_swap.py \
  --spec project/footprint-swap.json \
  --time-budget 180 --apply
```

The adapter receives `--request` and `--result` paths and returns schema
`kicad-footprint-swap-adapter-result-v1`. It must stage every target inside the transaction
directory, bind original identities and staged SHA-256 values, and return the strict neutral evidence
schema `kicad-footprint-swap-evidence-v1`. Each target's evidence binds its staged board digest to
zero-error ERC, accepted classified DRC findings, semantic zone settlement,
provisional-versus-DRC-saved equality, and named project audits. Audit commands are argv arrays; a reusable fast-mode receipt may attest an
immutable calibration fixture/mechanism, but every candidate still receives a fresh complete scan.

Promotion is multi-file crash-*recoverable*, not filesystem-atomic. The durable journal records
intent and completion for each same-filesystem replace; startup either clears a committed journal or
rolls an incomplete transaction back only from recognized original/staged digests. The aggregate
report is itself a journaled promotion. The tool snapshots every declared authority input before
running the adapter, rechecks it before promotion, normalizes and separates reserved paths, and
refuses active board, schematic, project, or transaction locks. Missing
adapter authority, stale calibration evidence, timeout, unrelated semantic changes, and unsupported
local conflicts are non-promoting results.

## Use the autorouting boundary

Classify routing ownership in [`../PCB.md`](../PCB.md) and follow the workflow contract in
[`../AUTOROUTING.md`](../AUTOROUTING.md) before invoking these tools. Keep critical geometry
generator/manual-owned. Use exploratory mode for placement and congestion evidence, and
allow only declared routine scope to cross the promotion boundary.

### Explore without promotion

Require explicit net classes and layers:

```sh
python3 scripts/kicad_route_candidate.py project/board.kicad_pcb \
  --exploratory \
  --allow-net-class ScoutRoutine \
  --allow-layer F.Cu --allow-layer B.Cu \
  --java VERIFIED_JAVA_PATH \
  --freerouting-jar VERIFIED_JAR_PATH \
  --router-sha256 VERIFIED_JAR_SHA256 \
  --expected-router-version VERIFIED_VERSION \
  --report work/scout-report.json \
  --keep-workspace work/scout-workspace
```

Treat the result as non-promotable evidence. Revise placement or author critical routing from the
lessons; do not copy exploratory coordinates into production authority.

### Prepare and route configured scope

Track `autoroute.json` and bind backend, inputs, net classes, styles, layers, position-sensitive DRC
baseline, project audits, applicator, and output paths.

Check the pinned tools first:

```sh
python3 scripts/kicad_autoroute_tools.py status
```

Installation changes network/cache state and requires explicit user authorization:

```sh
python3 scripts/kicad_autoroute_tools.py install --yes
```

Prepare or route only after status and project checks succeed:

```sh
python3 scripts/kicad_route_candidate.py project/seed.kicad_pcb \
  --config project/autoroute.json \
  --prepare-only \
  --report work/prepare-report.json

python3 scripts/kicad_route_candidate.py project/seed.kicad_pcb \
  --config project/autoroute.json \
  --report work/route-report.json \
  --keep-workspace work/router-workspace \
  --fail-on-findings
```

Retain full-run workspaces. The wrapper must keep the source read-only, prove declared inputs remain
unchanged, qualify a fresh seed, preserve protected routes, filter additions by scope/style/layer,
apply accepted additions to another fresh seed, and rerun DRC, connectivity, parity, and project
audits.

Treat every KiCad-Python subprocess as an untrusted serialization boundary. Require a freshly
written, exact-version envelope with a per-invocation nonce; exact mode and schema; digests of every
input and emitted board/DSN artifact; and a fully revalidated semantic snapshot. Route-applicator
summaries must bind the requested board and canonical route digest to the live output-board digest.
Identity-map envelopes must bind that board digest and recompute the UUID-map digest, item count,
and object-kind coverage. Independently recompute the selected-net route digest from a fresh v5
output snapshot, and require every identity UUID/value to equal that snapshot's schema-validated
object semantics. Reject stale files, extra fields, duplicate UUIDs, and bare JSON objects even when
the worker exits zero.

The v5 semantic snapshot includes direct board drawings plus every footprint graphic with
transformed, shape-dispatched geometry, layer, width/fill, lock state, footprint attributes and UUID
identity. Segment, rectangle, arc, circle, polygon and Bézier dispatch records their complete
geometry and binds saved stroke type plus hatch width/spacing by UUID; text records size, thickness,
angle, justification, font/style, line spacing, keep-upright state and mirroring. Unknown or
unreadable direct or footprint-hosted graphic mechanisms, or mismatches between the saved file and
pcbnew's object inventory, fail closed. It validates exact object-kind field sets, records the UUID
on every pad/route/zone, and binds the complete UUID identity map back to those validated semantics.
Keep the snapshot schema in
the seed/candidate report, compatibility cell and route manifest: footprint-hosted `Edge.Cuts` must
change the digest when opened, curved, moved, mirrored or replaced. Snapshot v5 uses the
structured DRC identity representation and requires the v2 baseline schema; regenerate and review
tracked seed baselines rather than comparing v5 output to a v1 identity baseline.

Interpret exit zero as “the report completed.” Require the report's promotable verdict and all
promotion checks; use `--fail-on-findings` when rejection must also fail the process.

### Promote reviewed digests

After visual review, promote the exact candidate and report recorded by the run:

```sh
"$KICAD_PYTHON" scripts/kicad_route_manifest.py promote \
  --seed project/seed.kicad_pcb \
  --candidate-board CANDIDATE_BOARD_FROM_REPORT \
  --config project/autoroute.json \
  --report work/route-report.json \
  --project-root project \
  --approve-candidate-sha256 CANDIDATE_SHA256 \
  --approve-report-sha256 REPORT_SHA256 \
  --output-manifest project/routes.json
```

Do not guess the candidate path. Promotion must reopen the reported candidate, verify its digest,
reconstruct the live input bundle, and independently extract the scoped delta. Production generation
must reproduce the pinned seed attestation before applying the canonical route manifest.

## Onboard a project

Use the scaffold's reviewed plan/apply/check flow. Do not handwrite `autoroute.json` or overwrite an
existing board:

```sh
python3 scripts/kicad_autoroute_scaffold.py plan project/board.kicad_pcb \
  --mode board-snapshot \
  --use-net-class AutorouteRoutine \
  --layer F.Cu --layer B.Cu \
  --reset-all-selected-routing \
  --selected-scope-routine \
  --output work/autoroute-plan.json
python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-plan.json \
  --approve-plan-sha256 PLAN_SHA256
python3 scripts/kicad_autoroute_scaffold.py check project/board.kicad_pcb \
  --config project/autoroute.json \
  --report work/autoroute-check.json
```

Generator and audit templates begin blocked. Implement them, then update pinned source hashes only
through a new reviewed repin plan:

```sh
python3 scripts/kicad_autoroute_scaffold.py repin-plan \
  --config project/autoroute.json \
  --output work/autoroute-repin-plan.json
python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-repin-plan.json \
  --approve-plan-sha256 PLAN_SHA256
```

Run the adapter with KiCad's bundled Python and pass the resulting same-basename seed to the
candidate tool. Keep hierarchical sheets and local libraries as explicit typed inputs. Reject
ambient absolute or environment-variable library URIs until the resources are vendored or otherwise
bound below the project root.

## Respect the limitations

- Every helper here that reads a board reads it through the **SWIG `pcbnew`** bindings, which KiCad
  deprecated in 9.0 and has already deleted from its development branch (`65a442b1d2bf`,
  2026-03-22); removal ships with KiCad 11. The replacement IPC API cannot serve this workload on
  any released KiCad — it needs a running GUI and cannot open a file from disk — and two of the
  operations these tools depend on (`GetEffectiveShape`+`Collide`, Specctra DSN/SES) have no IPC
  equivalent at all. `kicad_backend.py` makes the backend explicit and reported; it does not make
  a second backend exist. Plan and blocking evidence:
  [`../plans/SWIG-to-IPC-migration.md`](../plans/SWIG-to-IPC-migration.md).
- The autoroute/promotion chain (`kicad_route_candidate.py`, `kicad_autoroute_scaffold.py`,
  `kicad_route_manifest.py`) is **not** backend-aware. Its worker envelopes validate exact field
  sets and its compatibility matrix keys each cell on a `pcbnew` build version, so adding a backend
  axis is a schema revision requiring requalification of every cell. Those tools remain SWIG-only
  and stamp `pcbnew_version` as before.
- `transform_pin()` remains unverified until calibrated against KiCad on the project and supported
  transform cells.
- Autoroute promotion is currently disabled for every compatibility cell because snapshot v5 and
  the parity negative control have not completed the full DSN/SES/promotion requalification. Do not
  re-enable a cell until new dated, digest-bound evidence covers those mechanisms. **This disables
  promotion only.** An exploratory scout (Freerouting or KRT, per `PCB.md` and `AUTOROUTING.md`)
  needs none of this machinery: it runs on a scratch copy, commits nothing, and its output is
  placement/congestion evidence — do not read this bullet as a reason to skip the scout. (One
  agent session did exactly that and hand-routed for hours on a placement a 45-second scout
  proved routable.)
- Snapshot adapters can prove semantic reproduction without byte identity. Do not claim the latter
  unless the project-owned generator establishes it.
- Third-party routing does not become design authority merely because DRC passes. Preserve the
  manifest, input, toolchain, review, and final artefact bindings.
- Generic helpers cannot validate board-specific electrical, thermal, safety, or measurement
  assumptions. Run the project's calibrated guards and release workflow after every application.
