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
| `kicad_graphics.py` | internal complete semantic serialization of footprint graphic shapes and text |
| `kicad_netlist.py` | parse KiCad netlists across supported pretty-print formats and reject empty or inconsistent exports |
| `kicad_symlib.py` | resolve inherited symbols, common unit 0, body styles, and pin transforms |
| `kicad_verify.py` | run ERC/DRC safely and resolve ignored checks from reports plus project configuration |
| `kicad_copper_collisions.py` | fail-closed certain-short audit: tracks/arcs/vias/pads of different nets whose effective shapes touch or overlap on a shared copper layer |
| `kicad_functional_proximity.py` | fail-closed satellite→anchor placement tripwire: verifies every footprint declaring an `Anchor` binding (with per-binding `MaxDist`, optional `SelfPad`/`AnchorPad` selectors) sits within its pad-to-pad budget; binding fields without `Anchor`, vacuous runs, and `--expect` mismatches are all UNVERIFIED — release runs pass `--expect=ref:anchor:maxdist[:selfpad:anchorpad],...` binding the full captured tuple, never a bare refdes list, and the capture side must refuse to emit fields containing `:` or `,`, whitespace at an entry's outer edge, or a present-but-empty selector property (see [`POWER.md`](../POWER.md)); calibration harness: `tests/test_functional_proximity.py` |
| `kicad_footprint_swap.py` | orchestrate a deadline-bound, adapter-owned multi-target footprint migration and recoverable promotion |
| `kicad_repro.py` | bind reproducibility evidence to outputs actually produced and detect replacement after verification |
| `kicad_autoroute.py` | load strict autoroute configuration and shared route/report contracts |
| `kicad_autoroute_tools.py` | verify or explicitly install the pinned Freerouting/JRE toolchain |
| `kicad_route_candidate.py` | create a scratch candidate, enforce route scope, and emit a review report |
| `vision_probe.py` | blind image round-trip proving the serving can actually see images (skill-wide session gate, `SKILL.md` "Vision is a precondition" / `SETUP.md`) |
| `kicad_route_manifest.py` | promote a reviewed candidate through explicit digest approval |
| `kicad_autoroute_scaffold.py` | generate and verify project-owned autoroute configuration, adapters, applicators, and audits |

Use `--help` on the argparse CLIs: `kicad_repro.py`, `kicad_footprint_swap.py`,
`kicad_autoroute_tools.py`, `kicad_route_candidate.py`, `kicad_route_manifest.py`, and
`kicad_autoroute_scaffold.py`.
The remaining modules expose a small positional diagnostic or are import-only:

```sh
python3 scripts/kicad_netlist.py NETLIST.net
python3 scripts/kicad_symlib.py SYMBOL_LIBRARY.kicad_sym SYMBOL_NAME
python3 scripts/kicad_verify.py PROJECT_DIR_OR_FILE.kicad_pro
python3 scripts/kicad_copper_collisions.py BOARD.kicad_pcb   # re-execs under KiCad's python
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
