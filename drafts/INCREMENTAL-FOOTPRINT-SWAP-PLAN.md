# Incremental footprint-swap implementation plan

## Objective

Make a routine footprint-only substitution, such as two 0603 LEDs to 0805 LEDs, a bounded transaction that completes in less than three minutes without regenerating or rerouting the board.

The workflow preserves accepted placement and copper by default. It changes only named footprint assignments and embedded footprints, an explicitly declared project-owned placement or local route delta when the larger lands actually conflict, and derived zone fills. It never invokes a whole-board router.

## Honest performance contract

A single project-level command must process all declared variants in less than 180 seconds total on the development machine when the project has a current reusable audit attestation. The timer starts at process entry and covers discovery, authority checks, adapter calls, ERC, DRC, refill, audits, report generation, and promotion. Every subprocess receives the remaining deadline; insufficient time for mandatory final checks causes a clean non-promoting stop.

The current shunt-reversal calibrated audits take approximately 319.7 seconds for ADC and 91.2 seconds for no-ADC, so a cold calibration cannot satisfy this limit. The implementation must split expensive calibration from candidate inspection: calibration exercises an immutable, versioned audit fixture and emits a digest-bound mechanism attestation accepted by `audit_pcb.py --no-calibrate`; every transaction still scans the complete candidate board freshly. The receipt must not bind candidate D4/D5 pad inventory, so one mechanism receipt can validate both old and final states. If that attestation is missing or stale, the command must stop quickly with `needs_calibration`; it must not claim that a cold run meets the three-minute target or skip candidate inspection.

The common no-route-change case should complete in less than 30 seconds per board. A case that needs geometry outside an already declared project route delta stops within the same budget with an actionable report rather than broadening scope.

## Success criteria

On the shunt-reversal ADC and no-ADC boards, one command changing D4 and D5 must:

- operate on both variants as one all-or-nothing, crash-recoverable transaction;
- complete in less than 180 seconds total with a valid audit attestation;
- use the authoritative same-stem schematic, project, rule, generator, route-manifest, and migration-overlay inputs;
- preserve every unrelated semantic object: footprints, copper, zone definitions, keepouts, graphics, and project settings;
- add no via and make no layer transition;
- finish with zero applicable completion-critical ERC, DRC, connectivity, and parity findings;
- preserve and explicitly report unchanged classified documentation findings, exclusions, ignored checks, and sparse severity-map uncertainty;
- pass every project domain/process audit, including reserved-layer, guard topology, symmetry, and net-blind via-in-pad checks;
- promote only after exact promotable bytes pass all checks and original authority inputs are rehashed; and
- emit a report with per-phase timing, audit-attestation identity, and exact semantic changes.

Textual diff size is reported but is not a correctness criterion: KiCad's required saved-board refill may reorder serialized items. Semantic preservation is authoritative. The workflow must not introduce additional rewrite noise before that required KiCad save.

## Non-goals

- General placement, fanout, escape routing, or autorouting.
- Repairing arbitrary pre-existing DRC findings.
- Moving a component automatically to make the larger package fit.
- Adding vias, changing layers, stackup, project rules, severities, or unrelated nets.
- Silently editing a generated board without recording the change in generator-owned authority.
- Proving package, land-pattern, electrical, mechanical, thermal, or lifecycle compatibility; these are prerequisites under `FOOTPRINTS.md`.
- Implementing a generic local router in the first release.

## Project-level interface

Add `scripts/kicad_footprint_swap.py` and a declarative multi-target specification:

```sh
python scripts/kicad_footprint_swap.py \
  --spec hw/shunt-reversal/footprint-swap-led-0805.json \
  --time-budget 180 \
  --apply
```

The specification contains:

- one or more targets, each with board, schematic, project, optional rule file, variant name, and expected old authority hashes;
- substitutions as `{reference, old_footprint, new_footprint}`;
- authority mode: `hand-maintained` or a typed generated-board adapter;
- audits as argv arrays, never shell command strings;
- the required audit-attestation path and validation policy;
- optional explicit project-owned placement and route deltas by target; and
- protected nets, layers, regions, and process constraints used by semantic guards.

Dry-run is default. `--apply` is required for promotion. The command produces one aggregate result; if any target fails, no target is promoted.

Exit status distinguishes `clean`, `valid_dry_run`, `needs_calibration`, `needs_local_layout`, `blocked_authority`, `verification_failed`, `concurrent_change`, `recovery_required`, and `time_budget_exceeded`.

## Authority model

### Old accepted state and transition state

The accepted baseline and requested new authority are distinct states; the workflow must not require both simultaneously.

1. Establish the old accepted baseline against old schematic/generator authority and old board.
2. If the user has already changed only the schematic footprint field, permit exactly the declared footprint-assignment parity delta for the named references while still requiring zero other baseline regressions. Record both old authority from the specification/hash and current transition authority.
3. In the isolated transaction, apply the declared footprint assignment to scratch schematic/generator authority.
4. Verify final parity only against the new scratch authority.

This removes the impossible requirement for an old board to be parity-clean against a schematic that already names the new footprint.

### Generated boards and strict route manifests

For each generated target, the adapter declares whether an exact route manifest applies.

For a target with an exact v1 route manifest:

1. Reproduce the exact accepted old final board through the existing seed and route manifest, with all bound source hashes unchanged.
2. Before changing live source, create an immutable old-base bundle containing every manifest-bound seed, schematic, generator, library, rule, project, audit, and input file. Bind its inventory and SHA-256 digests in the migration overlay.
3. Apply the separate canonical footprint-migration overlay after that exact board materializes. The overlay records schematic footprint changes, embedded-footprint substitutions, and any explicit local route delta.
4. Promote the immutable old-base bundle and overlay alongside new live source so every subsequent reproduction starts from durable old authority rather than changed live paths.
5. Never ask the old v1 route manifest to accept changed bytes, and never defer authority repair to a later rebaseline.

For a target where no route manifest applies, such as shunt-reversal no-ADC with empty routine routing scope, reproduce its generator-owned base directly and then apply the same overlay protocol. The adapter must not invent manifest authority.

The accepted ADC routes remain authoritative through the immutable old v1 bundle; the overlay is independently authoritative for the bounded post-route migration. A later route-manifest version may natively include overlays, but the first implementation must not rewrite or weaken v1 matching.

### Typed adapter protocol

A generated-board adapter is an executable using JSON request/response messages with argv invocation, not shell interpolation:

- `probe`: declare authority mode, targets, per-target route-manifest applicability, source hashes, compatibility cell, and required files;
- `materialize_base`: reproduce each old accepted board from its declared authority—exact immutable manifest bundle where applicable, direct generator base otherwise;
- `write_overlay`: create generator-owned migration overlays and immutable old-base bundles in the transaction tree;
- `materialize_final`: reproduce old accepted boards and apply those overlays; and
- `inventory`: return semantic authority and protected-object inventories.

For a hand-maintained board, the tool edits scratch schematic and board authority directly. Generated-board promotion is forbidden without an adapter.

## Transaction pipeline

### 1. Start deadline, recover, lock, and snapshot

1. Start a monotonic deadline at process entry.
2. Detect an incomplete prior journal and require recovery or rollback before new work.
3. Resolve KiCad CLI and bundled `pcbnew`, then resolve every target file.
4. Refuse active KiCad locks.
5. Hash all authority inputs and copy the complete multi-target authority set into one transaction directory on the destination filesystem.
6. Record original hashes in the journal and report.

Every subprocess call, including adapters and audits, receives the remaining timeout. No phase may reset or extend the deadline.

### 2. Verify old baseline

1. For generated targets, call `materialize_base` and require byte/hash identity where the current manifest promises it, plus semantic identity in all cases.
2. Run old-state ERC and DRC/parity using the old authority. If the working tree is already in the exact declared schematic-only transition, permit only that named footprint parity delta.
3. Require zero applicable completion-critical baseline findings; classify and record documentation findings, exclusions, ignored checks, and severity-map uncertainty.
4. Validate the reusable audit attestation against the old/current audit inputs before relying on fast audit mode.

### 3. Apply authority migration in scratch

For every target/reference:

1. Change the schematic or generator-owned footprint assignment from the declared old identity to the declared new identity.
2. Load the requested new footprint through KiCad's configured footprint libraries using bundled `pcbnew`; never synthesize geometry from a package name.
3. Preserve reference, value, position, orientation, side, path/sheet identity, locked state, attributes, user properties, and pad-net assignments.
4. Map pads by pad number. Repeated pad numbers are represented as sets: every connected old copper-bearing pad must map to a compatible new-pad set, and all newly connected pad sets must be explicitly declared. Ambiguous topology fails closed.
5. Preserve stable UUIDs where KiCad supports it without corrupting the object model; qualify KiCad 9 and 10 behavior in integration tests rather than promising unsupported UUID mutation.
6. Save the scratch candidate through KiCad. Compare semantic inventories and require that only declared footprint assignment/geometry changed before any route delta is applied.

Do not reconstruct or transplant blocks after verification. The final saved-board serialization is the authority.

### 4. Refill before deciding whether repair is needed

Run one scratch DRC with saved-board zone refill and parity against the new scratch schematic. Structured JSON normalization must distinguish:

- findings attributable to changed footprint envelopes or their connected copper;
- zone-only findings that disappear after refill;
- unchanged classified baseline findings; and
- unrelated new findings, which abort immediately.

Never grade the new footprint against stale zone fills and never send a stale zone conflict into route repair.

If the refilled candidate has no migration findings, skip all route work.

### 5. Apply only declared local deltas

The first release has no generic placement search or dogleg router.

If the refilled candidate has a footprint-local conflict:

1. Look for an exact placement and/or route delta declared by the project adapter for that target and migration.
2. A placement delta must name the reference and exact old/new position/orientation; it may not be inferred by search. A route delta must bind old primitive UUIDs/geometry, net, layer, and width.
3. Permit route changes only to the minimum declared segment/arc chain in the declared local envelope.
4. Reject vias, layer transitions, undeclared footprint movement, protected-object changes, unrelated nets, clearance regression, or an unmatched old shape.
5. Apply the delta deterministically, refill, and regrade.

If no declared delta solves the observed finding, stop with `needs_local_layout` and report exact pads, segments, coordinates, blocking objects, and a suggested viewport. An interactive shove can then be reviewed and encoded as a project-owned delta. KRT, Freerouting, and whole-net rerouting are not fallbacks.

Only after the no-repair path and real project deltas are measured may a separate proposal add a generic same-layer local dogleg algorithm. That proposal requires its own tests and review.

### 6. Semantic-settle finalization and exact saved-board verification

For each target after route decisions:

1. Run the pinned project finalizer in memory, refill once to create a provisional snapshot, refill a second time, and require per-zone `BooleanXor` emptiness plus topology equality between the two in-memory fills. Non-settling fills abort.
2. Save the settled provisional board, then run final DRC/parity through a common verifier with explicit `refill`, `save_board`, output-format, and timeout controls.
3. Reload the DRC-saved board and require semantic equality of every zone definition and settled fill against the provisional snapshot; serializer ordering is ignored, geometry/topology changes are not.
4. Bind fresh baseline ERC to an unchanged schematic hash, or rerun ERC when scratch authority changed—which it does for this migration.
5. Validate the immutable-fixture mechanism attestation and run every project audit in fast mode; each audit still scans the complete exact candidate.
6. Require zero applicable completion-critical findings and explicit unchanged disposition of documentation findings, exclusions, ignored checks, and sparse severity maps.
7. Compare semantic inventories. Permit only:
   - declared footprint assignments and footprint geometry;
   - declared local route primitives;
   - derived filled geometry changed by settled refill; and
   - immutable old-base bundles and generator-owned overlay files.
8. Reject changed zone outlines/properties, project/rule settings, unrelated copper, vias, layers, severities, exclusions, or audit policy.
9. Reopen and parse the exact saved board bytes that will be promoted. Audits and inventories run against those exact bytes; there is no post-verification reconstruction.

### 7. Audit-attestation contract

A reusable calibration-mechanism receipt must bind at least:

- audit implementation and dependency digests;
- exact KiCad CLI and `pcbnew` compatibility cell;
- immutable calibration-fixture bytes and expected injected-failure detections;
- guard-policy and calibration-algorithm digests;
- calibration parameters and outputs;
- creation time and explicit validity policy; and
- receipt schema version.

The fixture must exercise the via-in-pad and other calibrated guards without using candidate-board object identities. Fast audit mode recomputes every mechanism-bound digest, verifies the fixture receipt, then scans the complete candidate and variant freshly. Candidate project, rule, stackup, guard inventory, and semantic objects belong in the transaction audit report, not the reusable mechanism receipt. A changed candidate therefore requires a new scan but not a new calibration unless it changes the audit implementation, compatibility cell, immutable fixture, guard policy, or calibration algorithm. Missing, stale, mismatched, or unverifiable receipts produce `needs_calibration`, not a skipped audit.

The plan must first measure `audit_pcb.py --no-calibrate` with this validation. If mandatory fast audits plus KiCad checks cannot fit the remaining deadline, optimize them before claiming acceptance.

### 8. Crash-consistent promotion

Ordinary per-file renames are not multi-file atomic. Use a journaled, recoverable commit protocol:

1. Materialize and digest-bind every immutable old-base/input bundle, snapshot every unchanged authority input, and stage every artifact that will actually be promoted—including the aggregate report—in a transaction directory on the destination filesystem; fsync files plus directory metadata.
2. Immediately before commit, rehash every original authority input; mismatch yields `concurrent_change` with no promotion.
3. Write and fsync a journal containing original hashes, staged hashes, destinations, backups, and commit phase.
4. Replace files one by one with same-filesystem atomic rename while advancing and fsyncing the journal.
5. On restart, classify each destination as the recorded original digest, staged digest, absent-new-file state, or unknown. Roll back only recognized original/staged states; unknown bytes require manual recovery and are never deleted or overwritten.
6. Mark committed only after every destination and directory entry is durable, then retain a bounded recovery record.

All targets share one journal. Failure or interruption during the second variant cannot leave an unrecognized half-promoted state.

## Shared verifier work

Current verification helpers do not provide the exact interface required here. Refactor existing JSON normalization from autoroute/candidate tooling into a neutral module and extend `kicad_verify.py` with explicit:

- `refill=True|False`;
- `save_board=True|False`;
- JSON and text report normalization;
- same-stem parity validation;
- subprocess timeout/deadline propagation;
- applicable/completion-critical versus classified documentation findings; and
- ignored-check/severity-map evidence.

The footprint tool must call this shared implementation; it must not create a third DRC parser.

## Machine-readable report

One aggregate JSON report contains:

- tool/KiCad versions and command;
- transaction ID, journal state, deadline, and terminal status;
- every authority path and initial/final SHA-256;
- targets and variants;
- old/new footprint identities and pad-set mappings by reference;
- changed pad envelopes and implicated nets;
- baseline, post-refill, post-delta, and final ERC/DRC/parity dispositions;
- exact semantic change inventory by object kind, UUID, net, and layer, including every separately declared field update and exact removed/added route primitive digest;
- zone definition changes separately from derived fill changes;
- audit argv, attestation digest/status, output digest, and elapsed time;
- declared route deltas applied or rejected;
- per-phase/target and total monotonic elapsed time;
- textual diff statistics as diagnostic information; and
- promotion/recovery status for the whole target set.

## Implementation sequence

### Phase 0 — measure and unblock the timing contract

1. Measure current baseline ERC, refill DRC/parity, `audit_pcb.py --no-calibrate`, and serialization time for both shunt variants.
2. Define and implement the audit calibration receipt and invalidation checks.
3. Prove mandatory attested audits can fit within the aggregate 180-second budget, or optimize them before proceeding.
4. Refactor shared verifier JSON/no-refill/refill/deadline support.

### Phase A — no-repair multi-target transaction

1. Add the project-level spec, aggregate report, monotonic deadline, and typed adapter protocol.
2. Implement old baseline versus scratch transition authority.
3. Implement per-target base reproduction: immutable exact v1 manifest bundle plus overlay for ADC, direct generated base plus overlay for no-ADC.
4. Implement KiCad footprint loading, repeated-pad-set mapping, scratch save, refill-before-grade, two-refill semantic settlement, provisional-versus-DRC-saved fill equality, semantic inventories, and exact saved-board verification.
5. Implement journaled promotion, concurrent-input rehashing, recovery, and all-or-nothing multi-target status.
6. Benchmark a fixture where larger pads require no copper change; assert less than 30 seconds per board and less than 180 seconds aggregate.

### Phase B — shunt-reversal observed route deltas

1. Run Phase A dry-run on the real D4/D5 substitution for both variants.
2. For each actual refilled footprint-local conflict, create the smallest reviewed same-layer project route delta; do not infer a general router.
3. Add those deltas to the generator-owned overlay and adapter.
4. Reproduce ADC from its immutable old exact route-manifest bundle plus overlay, and no-ADC directly from its generated base plus overlay.
5. Run aggregate verification and promotion benchmark under 180 seconds.

### Phase C — documentation and optional later repair proposal

1. Document the fast path in `PCB.md` before external autorouting guidance.
2. Document footprint/pad-set prerequisites in `FOOTPRINTS.md`.
3. Add CLI, specification, adapter, attestation, journal recovery, and report examples to `scripts/README.md`.
4. Link the implementation and measured benchmark from GitHub issue #3.
5. Consider a generic local dogleg proposal only if multiple projects demonstrate repeated route-delta patterns worth generalizing.

## Acceptance tests

### Authority and variants

- Old clean authority → scratch footprint assignment → final parity-clean board.
- Schematic already changed only at declared references → exact expected transition delta accepted; any second parity delta rejected.
- Current ADC v1 route-manifest reproduction from a promoted immutable old-base bundle followed by migration overlay without rerouting or weakened hashes.
- No-ADC direct generator-base reproduction plus overlay, with manifest applicability explicitly false.
- Two variants in one transaction; second-target failure promotes neither.
- Generated-board apply without adapter is refused.

### Footprints and geometry

- Larger same-pad-set footprint requiring no copper change.
- Rotated and back-side footprints.
- Repeated pad numbers mapped as compatible sets.
- Missing/ambiguous/newly connected pads fail closed.
- Zone-only stale findings disappear after refill without route work.
- Explicit bounded project placement and same-layer route deltas succeed.
- Delta requesting a via, layer transition, unmatched primitive, protected object, or unrelated net fails.
- DRC-clean process violation is caught by a supplied project audit.

### Verification and timing

- One end-to-end deadline applies to adapters, ERC, DRC, refill, audits, reporting, and commit.
- Hung adapter/audit/CLI is killed at remaining deadline and nothing is promoted.
- Fast no-route fixture asserts less than 30 seconds per board.
- Real two-board benchmark asserts less than 180 seconds total with valid receipt.
- Missing/stale audit receipt returns `needs_calibration` quickly.
- Mechanism receipt invalidates on audit code, dependency, KiCad cell, immutable fixture, guard policy, or calibration-algorithm change, but not merely because candidate D4/D5 geometry changed.
- Both old and final candidate boards are freshly scanned under one valid mechanism receipt.
- KiCad parity negative control for every supported compatibility cell.
- A non-settling second refill fails, and a DRC-saved fill semantically different from the provisional settled snapshot fails.
- Every final report binds exact promoted saved-board hashes.

### Promotion safety

- Authority mutation and path replacement immediately before commit produce `concurrent_change`.
- Interruption after each journal phase recovers by deterministic finish or rollback.
- Second-variant interruption cannot appear as a complete transaction.
- Textual reordering is tolerated only when semantic inventory proves allowed changes.

## Risks and controls

- **Cold audit exceeds budget:** attest the audit mechanism with an immutable fixture, scan every candidate freshly in fast mode, and make no cold-calibration timing claim.
- **Old/new authority confusion:** model baseline and scratch transition explicitly.
- **Strict route manifest rejects changed sources:** promote a digest-bound immutable old-base bundle, reproduce ADC from it, and apply a separately hashed canonical overlay; declare no-ADC manifest applicability false.
- **KiCad save rewrites order:** accept exact verified saved serialization and judge semantic inventory, not textual minimality.
- **Stale or unstable fills create false confidence:** refill before route-decision grading, require a second in-memory semantic-settle refill, and compare provisional settled fills with the DRC-saved board.
- **Deadline bypass:** propagate remaining time to every subprocess and phase.
- **Generated source/board divergence:** require adapter reproduction from overlay authority.
- **Concurrent work is overwritten:** rehash all originals immediately before journal commit.
- **Tool becomes another autorouter:** first release supports no repair except reviewed project-owned route deltas.
- **Repeated pad numbers:** map topology as sets and test it explicitly.
- **Shell quoting or interpreter ambiguity:** all adapters and audits use typed argv arrays and JSON protocols.

## Definition of done

The work is complete when the shared verifier changes, audit-attestation contract, generic multi-target transaction, tests, documentation, shunt-reversal adapter/overlay, and measured two-board benchmark are landed; a valid-attestation run completes in less than 180 seconds total; exact promoted saved-board bytes pass all applicable electrical and project audits; unrelated semantic objects remain unchanged; interruption and concurrent-edit tests pass; and an independent reviewer reports no unresolved blocker or high-severity finding.
