# General KiCad Autoroute Scaffold

Status: implemented in `scripts/kicad_autoroute_scaffold.py`,
`assets/autoroute/`, and the v2 paths in the existing candidate/promoter.

## Summary

Support three project contexts without coupling the skill to any existing
repository:

- **Generated project:** the project supplies a language-neutral seed/final
  adapter.
- **Hand-maintained project:** the existing PCB, project, and schematic are
  authoritative sources.
- **Standalone PCB:** the scaffold creates a `.kicad_pro`, declares the PCB
  authoritative, and records a permanent schematic-parity waiver. It must not
  fabricate a schematic.

Existing boards remain editable sources. Autorouted boards are non-editable
build outputs under `build/autoroute/`.

## Configuration and CLI

Add `kicad-autoroute-config-v2` as a discriminated union while retaining v1
unchanged.

V2 explicitly declares:

- `project_root` and project mode;
- the source board or generator adapter;
- `schematic_authority: parity | board-only`;
- typed source files and recursive directories;
- adapter and applicator protocol/path;
- exact `net_to_class`, layers, and styles;
- selected-scope policy: `project-audited` or explicitly reviewed `routine`;
- an optional route-reset manifest;
- baseline, audits, limits, manifest, and output paths.

The implemented v2 adapter contract currently fixes `project.root` to `.` and
requires top-level same-stem board/project/schematic authority files. Extra
hierarchical sheets and vendored KiCad libraries are explicit sources and part
of the seed context attestation; ambient library-table URIs are not promotable.

Add `scripts/kicad_autoroute_scaffold.py` with the following interface:

```sh
# Hand-maintained project
python3 scripts/kicad_autoroute_scaffold.py plan BOARD \
  --mode board-snapshot \
  --use-net-class AutorouteRoutine \
  --layer F.Cu --layer B.Cu \
  --reset-all-selected-routing \
  --selected-scope-routine \
  --output work/scaffold-plan.json

# Standalone board without project/schematic
python3 scripts/kicad_autoroute_scaffold.py plan BOARD \
  --mode board-snapshot \
  --board-only-authority \
  --create-net-class AutorouteRoutine \
  --net /A --net /B \
  --track-width-mm 0.25 \
  --clearance-mm 0.20 \
  --via-diameter-mm 0.60 \
  --via-drill-mm 0.30 \
  --layer F.Cu --layer B.Cu \
  --selected-scope-routine \
  --output work/scaffold-plan.json

# Generated project
python3 scripts/kicad_autoroute_scaffold.py plan BOARD \
  --mode generator-adapter \
  --use-net-class AutorouteRoutine \
  --layer F.Cu --layer B.Cu \
  --project-audited \
  --source generator=src/board_generator \
  --output work/scaffold-plan.json

python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/scaffold-plan.json \
  --approve-plan-sha256 SHA256

python3 scripts/kicad_autoroute_scaffold.py check BOARD \
  --report work/scaffold-check.json

# After implementing a blocked generator/audit template, review and apply its
# exact new digest instead of hand-editing the configured pin.
python3 scripts/kicad_autoroute_scaffold.py repin-plan \
  --config autoroute.json \
  --output work/repin-plan.json
```

`plan` is read-only except for its report. `apply` must revalidate every source
hash and require the exact canonical plan digest.

## Project and Ownership Onboarding

For existing projects:

- Resolve effective net classes through KiCad, including patterns and
  priorities.
- Freeze the result as an exact finite `net_to_class` inventory.
- Copy routing dimensions exactly into integer nanometres.

For explicit class creation:

- Require a new class name; reject it if it already exists.
- Require explicit nets and routing dimensions, or clone an existing style.
- Add assignments only for the reviewed nets.
- Reject conflicting effective non-default assignments.
- Verify a temporary merged project through KiCad.
- Include the surgical `.kicad_pro` diff in the approved plan.

For standalone boards:

- Create a version-qualified minimal `.kicad_pro` containing the reviewed
  net-class assignments.
- Do not generate a placeholder schematic.
- Record `schematic_authority: board-only`; every report must prominently state
  that schematic parity and ERC are unavailable.
- Continue requiring PCB connectivity, DRC, protected-route, reset/addition,
  source-integrity, and project-physics checks.

Replace “no critical copper” with a narrower reviewed declaration that every
selected net is routine and has no geometry-dependent requirements. Critical
copper elsewhere remains protected.

## Snapshot and Reset Semantics

The snapshot adapter is generic, complete, copied create-only from a versioned
skill asset, and bound by digest:

```text
source board
  - exact approved reset multiset
  + exact promoted route multiset
  = routed build board
```

The source board is never modified.

`--reset-all-selected-routing` means all existing route primitives on the exact
selected-net inventory, not an inferred subset. The plan generates
`autoroute-route-reset.json` containing every removed item with:

- stable board UUID;
- canonical kind, net, layer, geometry, and style;
- original locked state;
- multiplicity and aggregate digest.

Reject coincident duplicates, missing or duplicate UUIDs, zero-length items,
unsupported route types, or any apply-time mismatch. Final generation must
rederive the exact source, remove exactly this multiset, apply exactly the
promoted additions, and prove that no other route changed.

After reset, every remaining protected seed route must be either:

- a track segment; or
- an F.Cu-to-B.Cu through-via.

Arcs, blind/buried vias, microvias, and unknown route primitives anywhere in
the protected seed produce `BLOCKED_PRIMITIVES`. Promoted additions remain
limited to canonical segments and F.Cu-to-B.Cu through-vias.

## Adapter and Applicator Ownership

Use one language-neutral adapter protocol:

```text
adapter describe --report REPORT
adapter seed --output-dir DIR --report REPORT
adapter final --manifest MANIFEST --output-dir DIR --report REPORT
```

- Snapshot mode uses the complete built-in project-local adapter.
- Generator mode receives a fail-closed adapter template for the design agent
  to implement.
- Operations may write only below `--output-dir`.
- All declared sources must remain byte-identical.
- The adapter reconstructs the seed and invokes the applicator.
- The applicator alone validates provenance and applies canonical route records.
- Do not inspect generator ASTs or modify generator sources automatically.

Generated output, workspace, and report paths must be disjoint from all source
paths and recursive source directories. Re-expand recursive directories during
every check and promotion so added or removed files invalidate provenance.

## Verification and Reproducibility

Universal release requirements are semantic:

- exact immutable source bundle;
- exact source-board digest in snapshot mode;
- exact reset multiset and v2 semantic/context seed attestation (`seed_sha256`
  remains byte evidence; v1 retains exact-byte authority);
- exact manifest addition multiset;
- protected-route equality, including locked state;
- complete non-routing semantic projection equality against an empty-apply
  control;
- DRC, PCB connectivity, applicable parity, and calibrated project audits for
  `project-audited` scope, or the explicit reviewed routine-scope declaration;
- final manifest-route re-extraction;
- final output and report digests.

Byte-identical reproduction is optional and may be claimed only for a
separately qualified compatibility cell covering KiCad, pcbnew, OS,
architecture, board format, and canonicalizer. Never infer byte reproduction
from deterministic route UUIDs alone.

Before onboarding, perform a scratch LoadBoard/save/refill probe. If KiCad
changes the board format, injects stackup defaults, or changes non-routing
semantics, report `NEEDS_MIGRATION`. Migration is a separate digest-approved
workflow and is not part of autorouting.

Any source edit invalidates the seed, DRC baseline, candidate, route manifest,
and final build. The build board is explicitly non-authoritative; edits must
return to the source board.

Statuses include:

- `BLOCKED_PROJECT_CONTEXT`
- `BLOCKED_CONFIGURATION`
- `BLOCKED_PRIMITIVES`
- `BLOCKED_ADAPTER`
- `BLOCKED_AUDIT`
- `BLOCKED_TOOLCHAIN`
- `REPORT_ONLY_PLATFORM`
- `NEEDS_MIGRATION`
- `STALE_SOURCE`
- `READY_FOR_BASELINE`
- `READY_FOR_CANDIDATE`

## Skill Guidance

Revise the existing “generate, never hand-place” rule:

- Preserve the project’s declared source authority.
- Regenerate generator-owned boards.
- Keep hand-maintained boards board-source-owned.
- Never retrofit a generator merely to enable autorouting.
- Produce routed snapshot builds as derived, non-editable artifacts.

## Tests

- V1 compatibility and strict v2 union validation.
- Existing project, standalone board-only, and generator-adapter onboarding.
- Net-class patterns, priorities, collisions, and surgical merges.
- Missing sidecars and permanent board-only parity-waiver reporting.
- Source/output overlap, recursive-source changes, symlink swaps, stale plans,
  rollback, and idempotence.
- Exact reset behavior for partial/full routing, duplicates, locked state, and
  multiplicity.
- Blocking arcs, blind/buried vias, microvias, and unsupported primitives on
  selected and protected nets.
- Source-to-reset-to-manifest-to-final route-multiset proof.
- Empty-apply non-routing projection comparison.
- KiCad 9-to-10 migration and default-stackup injection blocking.
- Save/fill nondeterminism and semantic-versus-byte reproduction.
- Source PCB/project/schematic immutability across success and failure.
- Two hand-maintained snapshot fixtures, two structurally different generator
  adapters, and shunt-reversal only as a regression fixture.
- Fresh Claude Code forward tests for generated, hand-maintained, and
  board-only projects.

## Assumptions

- Do not modify the only board file in place.
- Fabrication uses the verified routed build output.
- Standalone board mode intentionally waives schematic parity but not PCB-level
  or project-physics verification.
- Toolchain installation remains separate and explicitly authorized.
- Platform and primitive qualification remains fail-closed.
