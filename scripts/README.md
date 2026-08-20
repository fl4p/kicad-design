# Reusable KiCad verification and routing helpers

Use these helpers for project-agnostic parsing, verification, reproducibility, and qualified
autorouting. Keep board-specific electrical and physical requirements in the project and calibrate
them through [`../GUARDS.md`](../GUARDS.md).

Run command examples from the skill repository root unless a section says otherwise.

## Contents

- [Choose the helper](#choose-the-helper)
- [Apply the common contracts](#apply-the-common-contracts)
- [Use verification helpers](#use-verification-helpers)
- [Use the autorouting boundary](#use-the-autorouting-boundary)
- [Onboard a project](#onboard-a-project)
- [Respect the limitations](#respect-the-limitations)

## Choose the helper

| module | use it for |
|---|---|
| `kicad_netlist.py` | parse KiCad netlists across supported pretty-print formats and reject empty or inconsistent exports |
| `kicad_symlib.py` | resolve inherited symbols, common unit 0, body styles, and pin transforms |
| `kicad_verify.py` | run ERC/DRC safely and resolve ignored checks from reports plus project configuration |
| `kicad_repro.py` | bind reproducibility evidence to outputs actually produced and detect replacement after verification |
| `kicad_autoroute.py` | load strict autoroute configuration and shared route/report contracts |
| `kicad_autoroute_tools.py` | verify or explicitly install the pinned Freerouting/JRE toolchain |
| `kicad_route_candidate.py` | create a scratch candidate, enforce route scope, and emit a review report |
| `kicad_route_manifest.py` | promote a reviewed candidate through explicit digest approval |
| `kicad_autoroute_scaffold.py` | generate and verify project-owned autoroute configuration, adapters, applicators, and audits |

Use `--help` on the argparse CLIs: `kicad_repro.py`, `kicad_autoroute_tools.py`,
`kicad_route_candidate.py`, `kicad_route_manifest.py`, and `kicad_autoroute_scaffold.py`.
The remaining modules expose a small positional diagnostic or are import-only:

```sh
python3 scripts/kicad_netlist.py NETLIST.net
python3 scripts/kicad_symlib.py SYMBOL_LIBRARY.kicad_sym SYMBOL_NAME
python3 scripts/kicad_verify.py PROJECT_DIR_OR_FILE.kicad_pro
```

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

Use `run_erc()` and `run_drc()` so `--exit-code-violations`, report freshness, and required report
labels remain load-bearing. Treat exit zero as command completion, then judge the structured report.

Resolve ignored checks from two sources:

1. The report's `Ignored checks` section states what KiCad skipped during that run.
2. `.kicad_pro` states configured project severities and provides a cross-check.

Return `UNVERIFIED` when either source cannot be parsed. A missing sparse map is not proof that no
defaults are ignored.

### Prove reproducibility with `kicad_repro.py`

Use a command that produces explicit output paths, require successful runs, and compare complete
artefact digests. Use `stable_digest()` and `verify_unchanged_since()` at the handoff boundary so a
concurrent writer cannot replace the artefact between verification and release unnoticed.

A matching digest proves byte identity for the tested outputs. It does not prove that an unexercised
branch, cache key, or physical model is correct on another input.

## Use the autorouting boundary

Classify routing ownership in [`../PCB.md`](../PCB.md) before invoking these tools. Keep critical
geometry generator/manual-owned. Use exploratory mode for placement and congestion evidence, and
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
- Autoroute promotion remains enabled only for exact compatibility cells tracked in
  `kicad-autoroute-compatibility.json`; other environments are report-only until qualified.
- Snapshot adapters can prove semantic reproduction without byte identity. Do not claim the latter
  unless the project-owned generator establishes it.
- Third-party routing does not become design authority merely because DRC passes. Preserve the
  manifest, input, toolchain, review, and final artefact bindings.
- Generic helpers cannot validate board-specific electrical, thermal, safety, or measurement
  assumptions. Run the project's calibrated guards and release workflow after every application.
