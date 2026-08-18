# Plan and tracker: opt-in PCB autorouting for the KiCad design skill

Status: implemented and forward-qualified for the exact Darwin arm64,
KiCad/pcbnew 10.0.5, Freerouting 2.3.0, pinned Temurin Java 25 cell. `PCB.md`
now makes Freerouting the default candidate backend for a project that opts in
with the full tracked contract; it is not an unconditional whole-board default.
The technical promotion/reproduction gates are complete. Phase 5's comparative
engineer-time-to-signoff measurement remains open and is not inferred from a
successful route.

## Implemented status

The plan's candidate, promotion, and reproduction boundaries are operational:

| Workstream | Result |
|---|---|
| Tool acquisition | `kicad_autoroute_tools.py` installs a platform-pinned JAR/JRE only after explicit `install --yes`, with TLS verification, archive safety, checksums, tree digest, and atomic receipt |
| Project contract | Strict `autoroute.json`, dedicated live KiCad class/style/layers, deterministic filled seed, exact DRC baseline, shell-free seed/final audits, and project-local applicator |
| Candidate wrapper | Scratch-only DSN/SES, fixed-seed proof, raw-addition filtering, complete non-routing control projection, protected-route proof, structured DRC, input/source integrity, and exact compatibility cell |
| Promotion | Digest-explicit `PROMOTABLE_CANDIDATE` -> canonical route manifest; no raw board/SES promotion |
| Reproduction | Normal project generator consumes only `routes.json`, verifies the exact seed and input bundle, applies canonical segments/vias, and proves exact final manifest geometry |
| Tests | 43 focused skill tests plus two project applicator contract/tamper tests pass |
| Skill wiring | `SKILL.md` routes board work to `PCB.md`; `PCB.md` carries the conditional-default policy and operating procedure; `scripts/README.md` is the command contract |

The representative `shunt-reversal` run delegated exactly nine connections on five
`AutorouteRoutine` nets. The reviewed manifest contains 82 canonical outer-layer
segments/through-vias with route digest
`e30859eb976293bf746b0665beea1b445b22ce2e30cd7bb9e478d7a740a3d49f`.
The critical `/GC` driver path is generator-owned instead of delegated; its
project audit measures the real U6.11-to-R3.1 copper as 63.45 mm and two vias
against an 84.95 mm bound.
The final production generator re-created seed
`5115fe971403586282dd2ee87e38b9ec557d9a01278501556c6c1cc1e2d6642d`,
applied the manifest, closed every connection, and passed:

- KiCad DRC: 0 violations, 0 unconnected items, 0 schematic-parity issues;
- the calibrated project audit, including isolation, power symmetry, thermal
  arrays, guard geometry, Kelvin keepout, orphan islands, and via-in-pad policy;
- exact seed/input/applicator/toolchain provenance and protected seed routing;
- visual F.Cu/B.Cu review;
- the normal generator's placement and final route-extraction guards; and
- the canonical final wrapper, whose two-run generation, JSON DRC/parity,
  calibrated audit, and exact manifest checks all passed for final board SHA-256
  `003e8d617023bc05b1fed55b7ba8d3632cdae5f97c62c63b96235604bdd660fe`.

Retained final evidence:

- [`route-report-final.json`](../../../pv/pwr-metering/hw/shunt-reversal/autoroute-default-v2.3.0-gc-native/route-report-final.json)
- [`routes.json`](../../../pv/pwr-metering/hw/shunt-reversal/routes.json)
- [`final-verification.json`](../../../pv/pwr-metering/hw/shunt-reversal/final-verification.json)
- [`shunt-reversal.kicad_pcb`](../../../pv/pwr-metering/hw/shunt-reversal/shunt-reversal.kicad_pcb)

Three forward-test failures became explicit contracts: Freerouting fanout had to
be disabled independently of automatic neckdown; pcbnew project-sidecar rewrites
had to be snapshot/restored; and the independent project applicator needed the
same field-wise canonical route key as the promoter. No bypass remains for any
of these failures.

Independent review added further load-bearing gates: promotion now opens and
re-extracts the exact reviewed candidate board; manifests bind exact live
net-to-class/style scope; audits run in a minimal environment and must emit a
configured calibration marker; enabled tool cells pin the Java executable and
installed tree; compatibility evidence is digest-bound and revalidated live;
nested symlink inputs and configured SES reuse are rejected; and full
reproducibility can no longer silently select a cheaper generator stage.

Current limits are deliberate: only configured routine scopes, canonical
segments and F.Cu-to-B.Cu through-vias, and promotion-enabled exact compatibility
cells. Placement, critical/high-current routing, fanout, planes, zones, isolation,
and stitching remain generator-owned. Learned routing/placement methods remain
experimental ranking or advisory inputs, not a production backend.

## Historical prototype record (superseded)

The following findings explain why the implemented boundary is strict. They are
retained as failure evidence, not as the current workflow verdict.

Historical prototype findings:

- KiCad 10.0.5 `kicad-cli` has no Specctra command, but bundled Python exposes
  working `ExportSpecctraDSN` and `ImportSpecctraSES` functions; they require a
  `wxApp` initialization on macOS.
- The Freerouting 2.3.0 release JAR requires Java 25.
- Headless Freerouting 2.3.0 accepted `-inc Critical` but still routed the
  Critical net in a two-class calibration. Post-import scope comparison is
  load-bearing; `-inc` is advisory only.
- Project net-class assignments must be loaded from `.kicad_pro` and applied
  to the scratch board before DSN export.
- A constrained `shunt-reversal` run locked all 520 existing segments and 912
  vias, assigned the seven remaining nets to a 0.25 mm outer-layer class, and
  reduced KiCad opens from 9 to 1. The candidate was still rejected: headless
  Freerouting added 18 route items on six `Default`-class nets despite
  `-inc Default`, zone refill created three additional isolated-copper warnings,
  the isolation-barrier audit newly failed, and the inherited thermal-island
  failure worsened from six islands to nine.
- The wrapper now copies project-local library tables/resources, proves locked
  routes became fixed DSN copper, applies and post-audits allowed layers,
  preserves the raw SES import, refills zones before snapshot/DRC, supports
  fail-closed project audit argv hooks, and withholds `REVIEW` until a complete
  invariant/route-manifest layer exists.
- The next backend problem is pre-router scope enforcement. Post-import scope
  rejection is safe but wastes a run; Freerouting 2.3.0's `-inc` cannot be the
  authority. Characterize a DSN transformation that removes ignored-net
  routing tasks while retaining their fixed copper and obstacle effect, or do
  not expose scoped Freerouting through the skill.

Date: 2026-08-18.

Evidence base: `PCB-AUTOROUTING.md`. The operating contract and representative
prototype are now validated and linked from `PCB.md`.

## Historical tracking snapshot (superseded)

### Conclusion at that time

Freerouting is usable today as an **untrusted candidate generator**, but scoped
Freerouting is not ready to expose through the live skill. The constrained trial
found legal-looking geometry for eight of nine open KiCad connections while
preserving all locked seed copper. It also proved that successful connectivity is
not sufficient: the router escaped its requested net scope and violated board intent
that was visible only to project audits.

The immediate engineering problem is not routing capacity. It is translating and
enforcing intent across the KiCad -> DSN -> Freerouting -> SES -> KiCad boundary.

### Workstream status

| Workstream | Status | Evidence or exit condition |
|---|---|---|
| External-router research | Complete for initial decision | `PCB-AUTOROUTING.md` and `ROUTER-LITERATURE.md`; classical Freerouting remains the best immediately usable free baseline |
| Report-only wrapper | Prototype implemented and independently reviewed | `scripts/kicad_route_candidate.py`; 19 focused tests pass |
| KiCad 10.0.5 DSN/SES path on macOS | Characterized for the prototype | Export/import needs bundled `pcbnew` plus `wxApp`; no `kicad-cli` Specctra command |
| Locked-route preservation | Passing | All 520 segments and 912 vias have a DSN fixed-copper geometry match and survive the import exactly |
| Constrained `shunt-reversal` trial | Complete; candidate rejected | 9 -> 1 KiCad opens, but 18 out-of-scope items, a new isolation failure, and isolated islands 6 -> 9 |
| Pre-router net-scope enforcement | Open; highest priority | Must suppress excluded routing tasks while retaining their fixed copper and obstacle effect |
| Router-visible isolation and plane intent | Open; highest priority | Calibration must prove keepouts and plane-sensitive regions survive the round trip and fire on a known-bad route |
| Selective local rip-up/reroute | Open; next experiment | Give `BR_IN` a narrow corridor and unlock only the copper needed to use it |
| Route-manifest promotion | Not implemented | Deterministic extract -> generator apply -> extract must be stable before any candidate can become source input |
| Live `SKILL.md` / `PCB.md` integration | Deliberately deferred | Do not reference this workflow until scope enforcement and manifest promotion pass |

### Four-layer board interpretation

`shunt-reversal` has four copper layers, but the experiment intentionally gave
Freerouting only two routing layers:

- `F.Cu` and `B.Cu` were permitted for the new class;
- `In1.Cu` is designated `PWR` and `In2.Cu` is designated `GRD`;
- all 1,432 existing route items were locked, so the router could not rip up and
  rebalance a congested local area.

This was therefore a constrained outer-layer completion run, not an unconstrained
four-signal-layer route. Opening the inner layers would add geometric capacity but
could fragment power or return planes and invalidate return-path, isolation, and
plane-connectivity assumptions. Any future inner-layer use must be an explicit
project decision, limited to named nets or corridors and followed by plane-continuity
and return-path audits.

The remaining `BR_IN` open spans the existing F.Cu endpoint at x=83.85 mm to R42
pad 2 at x=106.75 mm. Eight other opens closed under the stricter two-layer/locked
conditions. Treat that as evidence that the board is routable and that `BR_IN` needs
local freedom, not as evidence that the whole board needs unrestricted autorouting.

### Constrained trial ledger

| Measure | Seed | Refilled candidate | Interpretation |
|---|---:|---:|---|
| KiCad open connections | 9 | 1 | Eight opens closed; `BR_IN` remains |
| Routing items | 1,432 | 1,543 | 111 items added |
| Intended-class additions | 0 | 93 | Useful candidate geometry |
| Excluded-net additions | 0 | 18 | Hard scope failure on six nets |
| KiCad DRC warnings | 7 | 10 | Nine isolated-copper plus the inherited silk warning |
| Orphaned pour islands | 6 | 9 | Inherited thermal failure worsened |
| Project audits passing | 8/9 | 7/9 | Thermal remained failed; isolation newly failed |
| Schematic-parity findings | 0 | 0 | Necessary, not sufficient |

The source PCB SHA-256 was unchanged before and after the experiment. The retained
experiment evidence is:

- [`RESULTS.md`](../../../pv/pwr-metering/hw/shunt-reversal/autoroute-freerouting-constrained-v2.3.0/RESULTS.md)
- [`FINAL-REPORT.json`](../../../pv/pwr-metering/hw/shunt-reversal/autoroute-freerouting-constrained-v2.3.0/FINAL-REPORT.json)
- the DSN, SES, raw import, refilled candidate, DRC output, project-audit output,
  router log, preparation metadata, and finalization metadata in the same directory.

The retained original `route-report.json` predates several wrapper corrections and is
superseded by `RESULTS.md` and `FINAL-REPORT.json`. Keep it only as raw historical
evidence; do not use it as the current verdict.

### Wrapper state after review

The prototype now:

- refuses any report, output, or recursively referenced project-library member that
  could collide with a source input;
- copies source inputs with before/copy/after digest verification;
- confines internal worker paths to the scratch workspace;
- scrubs ambient Java/Freerouting option variables and records provenance;
- resolves the allowed net classes to explicit net names;
- locks the seed and verifies every fixed DSN segment/via by net, layer, width, and
  geometry using the characterized 1,000 nm comparison quantum;
- preserves the raw SES import, refills zones, then snapshots and runs DRC;
- detects project-audit mutation of the candidate and rejects audit failures;
- reports `PROJECT_AUDITS_PASSED` only when configured audits pass, otherwise
  `GENERIC_CHECKS_ONLY`; it never promotes sparse checks to a `REVIEW` verdict;
- requires retained workspaces for full router runs so evidence is not discarded.

Independent review found no remaining concrete blocker in that closure set. This is
not approval to promote routes: the wrapper still has no complete invariant model and
no route-manifest boundary.

### Machine-learning position

Do not make a learned router the production backend today. Current published evidence
shows progress on small PCB benchmarks and much stronger commercial activity in chip
placement/routing, but it does not provide a free, general, locally auditable KiCad
router with stronger evidence than Freerouting on representative boards.

ML can be used experimentally for placement suggestions, congestion estimation,
candidate ordering, or ranking several conventionally routed candidates. It must not
be allowed to weaken deterministic DRC, project audits, scope enforcement, provenance,
or manifest promotion. Revisit a learned routing backend only when it ships runnable
code, supports the required KiCad constraint model, and beats the classical baseline
under the same hard acceptance gates.

### Next experiment

1. Build a small DSN calibration that has two net classes, fixed excluded copper, an
   isolation keepout, zones, and a deliberate open on the allowed class.
2. Characterize a DSN transformation that removes excluded-net routing tasks without
   removing their fixed copper or obstacle effect. Prove it with a known-bad fixture.
3. Encode the isolation band and plane-sensitive areas as router-visible restrictions,
   then prove the restrictions survive SES import and zone refill.
4. On a fresh `shunt-reversal` copy, unlock only a measured corridor around `BR_IN` and
   rerun with F.Cu/B.Cu as the default layer set.
5. Require zero out-of-scope additions, no new DRC findings, restored project-audit
   baseline or better, exact locked-copper preservation, and the declared connectivity
   target before considering route-manifest work.
6. If that passes, implement deterministic route extraction and generator-side
   application; do not integrate the workflow into `SKILL.md` before this boundary is
   proven.

## Outcome

Add autorouting as a reversible candidate-generation stage for PCB layout. It is
the conditional default when a project supplies the complete tracked contract;
otherwise routing remains native/manual. An external router must never become
the source of truth for a generated KiCad board and must never overwrite the
generator-owned `.kicad_pcb`.

The intended flow is:

```text
PCB generator -> deterministic route seed -> DSN -> router -> SES -> scratch PCB
                                                                      |
                                                   DRC + audits + review
                                                                      |
                                                                      v
                                                           route manifest
                                                                      |
PCB generator + approved manifest -> canonicalise -> fill zones -> final PCB
```

The route manifest is the promotion boundary. It contains only accepted routing
geometry and is a reviewable input to the existing board generator. Raw router
output remains untrusted and disposable.

## Goals

- Reduce time spent routing routine nets without weakening the skill's existing
  generator, reproducibility, verification, or release guarantees.
- Support scoped routing by explicit net or net-class allowlist before considering
  a whole-board run.
- Preserve critical, topology-bearing copper exactly.
- Make each accepted route reproducible without requiring the autorouter to produce
  identical output on every future run.
- Record enough provenance to reproduce or investigate the routing experiment.
- Compare candidates by time to an electrically acceptable result, not merely by
  ratsnest completion.

## Non-goals

- Autorouting every board by default.
- Treating 100% connectivity or zero ordinary DRC violations as electrical signoff.
- Asking a router to infer Kelvin topology, star points, current loops, return paths,
  guard structures, controlled impedance, isolation intent, or thermal intent.
- Importing a routed board wholesale into the generated design.
- Automatically installing a router, uploading a design, or using a cloud routing
  service without explicit authorization.
- Making Freerouting-specific data part of the final board generator interface.

## Operating policy

### Invocation

Run the autorouting path when any of the following is true:

- the user explicitly asks for autorouting;
- the project already enables the workflow and defines its route scope; or
- the skill proposes a reversible routing experiment and the user accepts it.

Do not silently choose external whole-board autorouting during ordinary PCB work.
The skill may suggest scoped autorouting when placement and rules are mature and the
remaining nets are low risk.

### Default scope

The default route scope is an empty allowlist. Resolve any selected net classes to
an explicit list of net names before invoking the router, and record that resolved
list in the run provenance.

Keep these structures generator-authored unless a project-specific requirement says
otherwise:

- Kelvin and remote-sense connections;
- ADC inputs, references, guards, and other precision analog nodes;
- clocks, synchronization paths, differential pairs, controlled-impedance nets,
  high-speed buses, RF, and antenna feeds;
- switching-current and decoupling loops;
- high-current copper, planes, unusual copper shapes, and thermal structures;
- isolation-boundary crossings, creepage structures, and intentional star points;
- any route whose geometry implements a documented circuit requirement.

On a precision power-metering board, begin with ordinary GPIO and low-risk control
nets. Do not begin with shunt, Kelvin, ADC/reference, clock, isolation, guard, or
current-carrying paths.

### Initial backend

Use a local Freerouting process through KiCad's production DSN/SES interchange path
for the first prototype. Pin and record the router release and executable or JAR
digest. Do not start with Freerouting's experimental JSON/API path.

The implementation must not assume that DSN/SES preserves every KiCad constraint.
In particular, post-import KiCad checks and independent copper-to-edge and
domain-specific geometry audits remain mandatory.

## Artifact authority

| Artifact | Purpose | Authority and retention |
|---|---|---|
| Board generator and project inputs | Placement, rules, critical routes, zones, and board construction | Source of truth; tracked |
| Route-seed `.kicad_pcb` | Deterministic input board for a routing experiment | Generated; never edited in place |
| DSN | Router interchange input | Scratch by default; record its SHA-256 |
| SES | Raw router result | Untrusted scratch output; retain when useful for diagnosis or review |
| Imported scratch `.kicad_pcb` | KiCad interpretation of the SES candidate | Disposable; never promote wholesale |
| Route manifest | Accepted track, arc, and via geometry plus provenance linkage | Generator input; tracked and reviewed |
| Final `.kicad_pcb` | Generator output after manifest application, canonicalisation, and zone fill | Generated artifact; tracked according to project policy |

The final board must reproduce from the ordinary generator inputs plus the route
manifest. Re-running Freerouting is not required to reproduce an accepted board.

## Proposed project inputs

Use a project-local configuration such as `autoroute.json` for routing intent and a
separate `routes.json` for accepted geometry. Exact names can be chosen during the
prototype, but the responsibilities must stay separate.

The routing configuration should contain:

- router backend and pinned version;
- explicit included nets and/or net classes;
- explicit exclusions and immutable critical-route groups;
- allowed copper layers;
- runtime, pass, and via budgets;
- router configuration and a seed if the backend exposes a reliable seed;
- expected connectivity target;
- project-specific postconditions and audit commands.

The route manifest should contain only normalized generator-level primitives:

- schema version;
- source route-seed digest;
- routing-run provenance digest;
- resolved allowlist of net names;
- segments and supported arcs: net, layer, width, and coordinates;
- vias: net, position, layer span, diameter, drill, and supported via type;
- normalized geometry digest for every immutable route group.

Do not store KiCad UUIDs as route identity. Derive identity from stable routing data
such as net, layer, primitive type, and normalized geometry. Reject unsupported
routing objects rather than silently omitting them.

## Candidate workflow

### 1. Establish routing intent

- Complete mechanically constrained placement, stackup, plane strategy, keepouts,
  net classes, and rule priorities.
- Route critical topology in the generator and mark it immutable for the experiment.
- Resolve the autoroute allowlist to explicit nets.
- Record acceptance targets and any permitted baseline exceptions.

### 2. Generate and qualify the route seed

- Run the real PCB generator and prove that it ran.
- Use the established canonicalize-before-fill pipeline.
- Confirm that KiCad parses the board and that schematic parity holds.
- Run DRC and distinguish expected unrouted connections from actual geometry or rule
  violations. The seed may have unconnected pads; it may not use that fact to hide
  other violations.
- Enumerate applied and ignored DRC checks.
- Record the seed digest, unconnected baseline, stackup, layer set, project-file
  digest, and immutable copper geometry digests.

### 3. Create a disposable routing workspace

- Copy the qualified seed to an isolated temporary directory.
- Export DSN from the copy using a characterized KiCad path.
- Verify that the real project files and generated board digests did not change.
- Record the DSN digest and export logs.

### 4. Generate one or more candidates

- Run the pinned local router with recorded configuration and a bounded time or pass
  budget.
- Record exit status, logs, runtime, version, executable digest, configuration digest,
  and seed when available.
- Preserve failed candidates only when they help diagnose constraints or placement.
- If several candidates are requested, apply the same budgets and acceptance gates to
  each before comparing metrics.

### 5. Import into another scratch board

- Import the SES into a fresh copy of the same qualified route seed.
- Do not save over the seed or the tracked board.
- Characterize the actual KiCad import mechanism before automating it; do not invent
  an unsupported `kicad-cli` or `pcbnew` API.
- Refill zones before measuring copper-dependent properties.

### 6. Enforce invariant preservation

Reject the candidate if it changes anything outside the approved route scope,
including:

- footprint, pad, placement, orientation, or net assignments;
- board outline, drawings, text, rule areas, keepouts, stackup, or project settings;
- zones, zone settings, or intended plane topology;
- critical or locked routing geometry;
- routes on nets outside the resolved allowlist.

Do not trust a router's interpretation of `locked`. Compare normalized pre-route and
post-route copper geometry and fail closed on unreadable or unsupported objects.

### 7. Verify and score the candidate

Hard acceptance gates:

- every promoted primitive belongs to an allowlisted net;
- every named endpoint pad belongs to that same net;
- no new short, clearance, keepout, or other DRC violation exists;
- schematic parity and the intended severity map still hold;
- the unconnected count reaches the declared target;
- immutable route and non-routing object digests match;
- via-in-pad and all applicable independent geometric guards pass;
- isolation, creepage, guard, loop-area, symmetry, return-path, current-density,
  stackup, zone-fill, and fabrication checks applicable to the board pass;
- all copper layers render and receive visual review.

After all hard gates pass, compare candidates using:

- completed connections;
- total and per-critical-net length;
- via count and layer transitions;
- length/skew compliance where applicable;
- congestion, cleanup required, and remaining routability;
- runtime and total engineer review/cleanup time.

A metric must not compensate for a failed hard gate.

### 8. Promote only routing primitives

- Extract supported track, arc, and via objects from the accepted scratch board.
- Filter by the resolved allowlist even if the router produced other routes.
- Normalize and sort the geometry, then write the route manifest and provenance.
- Re-run the extractor and require byte-identical manifest output.
- Review the manifest diff before making it a generator input.

### 9. Regenerate and release normally

- Run the board generator from its normal inputs plus the accepted route manifest.
- Validate endpoint nets while applying every manifest primitive.
- Use the existing UUID/order canonicalisation and zone-fill sequence.
- Run the complete verification ladder, project-specific guards, renders, and
  reproducibility test on the generated result.
- Verify that the bytes released are the same bytes that were checked.

## Proposed helper contract

After the interchange path is characterized, add a helper such as
`scripts/kicad_route_candidate.py`. Its first version should orchestrate and report;
it must not decide electrical route scope.

Candidate operations:

- `prepare`: qualify the seed, create scratch space, export DSN, and record provenance;
- `route`: invoke a configured local router with bounded resources;
- `inspect`: import or locate an imported scratch candidate and enforce invariants;
- `promote`: extract only allowlisted routing primitives into a deterministic manifest;
- `verify`: regenerate from the manifest and run generic verification gates.

Required helper properties:

- refuse paths that resolve to the tracked/generated board for any diagnostic write;
- use explicit paths rather than broad globs or unresolved environment variables;
- never download, install, or call a hosted routing service implicitly;
- fail closed when the board, report, object class, net, layer, or geometry cannot be
  interpreted;
- preserve subprocess logs and state which artifact each result applies to;
- avoid load-bearing bare `assert` statements;
- make dry-run/report-only behavior available before promotion.

Project-specific electrical guards remain with each PCB project. The helper may invoke
them, but it must not replace or silently generalize them.

## Implementation phases

### Phase 0: approve the contract

- Review this plan and settle the promotion boundary and default routing scope.
- Keep the research and plan documents unhooked from the skill entrypoint.

Exit criterion: the artifact authority, hard gates, and non-goals are accepted.

### Phase 1: characterize KiCad/Freerouting interchange

- Build a small calibration board containing locked routing, a keepout, zones, a
  copper-to-edge constraint, multiple layers, and at least one deliberately unrouted net.
- Exercise DSN export, Freerouting, SES import, and KiCad re-save on copies.
- Measure which rules, locks, vias, layers, arcs, zones, and object properties survive.
- Inject known failures to prove the invariant checks and DRC parsing fire.
- Determine whether any supported command-line or API path can import SES safely.

Exit criterion: a written compatibility matrix and a candidate that survives the
round trip without an unexplained object or rule change.

### Phase 2: prove the route-manifest boundary

- Define the smallest schema that represents actual Freerouting output encountered in
  Phase 1.
- Implement deterministic extraction from a scratch candidate.
- Implement generator-side application with net, layer, width, and endpoint validation.
- Prove that extract -> generate -> extract is stable.
- Prove that critical routes and non-routing board objects cannot enter through the
  manifest.

Exit criterion: the final board reproduces from generator inputs plus the manifest,
without retaining DSN or SES as a build dependency.

### Phase 3: add candidate orchestration

- Add the helper with scratch-path protection, local-router invocation, provenance,
  bounded execution, reporting, and promotion gates.
- Add realistic tests and known-bad calibration cases.
- Update `scripts/README.md` only after the helper works on real KiCad artifacts.

Exit criterion: a report-only run and a promotion run both work without modifying the
real project until the generator consumes an explicitly accepted manifest.

### Phase 4: wire progressive disclosure

- Add one short routing instruction to `SKILL.md` explaining when to read the detailed
  autorouting guidance.
- Add the operational policy and verification requirements to `PCB.md`.
- Link the research document as background rather than duplicating its evidence.
- Keep ordinary schematic and non-autorouted board work from loading the detailed
  autorouting material.

Exit criterion: the skill selects the workflow only for relevant PCB routing tasks and
does not imply an unconditional whole-board autorouting default.

### Phase 5: representative A/B evaluation

- Freeze placement, stackup, rules, and critical routes on a representative local board.
- Compare a manual or released baseline against scoped autorouting using the same signoff
  checks.
- Measure routing completion, DRC, wirelength, vias, immutable geometry, cleanup time,
  total time to signoff, and defects found during review.
- Begin with ordinary control/GPIO nets on a power-metering board.

Exit criterion: scoped autorouting demonstrably reduces total effort without weakening
electrical review, artifact reproducibility, or release confidence.

## Adoption criteria — technical gates satisfied; effort evidence pending

The exact qualified cell may be used as the candidate backend for an explicitly
opted-in project because these technical gates are satisfied:

- the DSN/SES compatibility matrix has no unexplained load-bearing gaps;
- manifest extraction and generator application are deterministic;
- scratch-path protections have been tested against the real artifact path;
- known-bad calibration cases fire for scope violations and critical-route changes;
- full KiCad and project-specific checks pass after manifest application.

Promoting scoped autorouting from a qualified opt-in pilot to an unqualified
normal board-layout option still requires a representative A/B trial showing a
net reduction in time to signoff. That criterion is **pending**: the successful
`shunt-reversal` route proves correctness and reproducibility, not comparative
engineer effort.

Whole-board autorouting remains an explicit, board-specific choice even after scoped
routing is adopted.

## Original Phase 1 questions (resolved or deliberately staged)

- Which KiCad versions and platforms are initially supported?
- What is the safest characterized path for SES import on each platform?
- Does the selected Freerouting release expose a stable random seed, and which settings
  affect repeatability?
- Which routing primitives and via types appear in real SES imports and therefore belong
  in manifest schema version 1?
- Which constraints are absent or weakened in the exported DSN?
- Should successful raw SES files be committed for audit, or are the route manifest,
  provenance, logs, and hashes sufficient for the target project's release policy?
- Which small calibration board and representative production board should be used for
  the first tests?

The initially blocking questions are now resolved for the one qualified cell or
explicitly staged for future cells. The authority model is permanent: raw router
output remains an untrusted scratch artifact and is never promoted directly;
only a verified canonical route manifest can become a generator input.
