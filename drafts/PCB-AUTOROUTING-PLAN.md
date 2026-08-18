# Plan: opt-in PCB autorouting for the KiCad design skill

Status: proposal; no skill behavior is wired to this plan yet.

Date: 2026-08-18.

Evidence base: `PCB-AUTOROUTING.md`. Keep that research document and this plan
unreferenced from `SKILL.md` and `PCB.md` until the operating contract and first
prototype have been validated.

## Outcome

Add autorouting as an opt-in, reversible candidate-generation stage for PCB
layout. An external router must never become the source of truth for a generated
KiCad board and must never overwrite the generator-owned `.kicad_pcb`.

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
does not imply that autorouting is the default.

### Phase 5: representative A/B evaluation

- Freeze placement, stackup, rules, and critical routes on a representative local board.
- Compare a manual or released baseline against scoped autorouting using the same signoff
  checks.
- Measure routing completion, DRC, wirelength, vias, immutable geometry, cleanup time,
  total time to signoff, and defects found during review.
- Begin with ordinary control/GPIO nets on a power-metering board.

Exit criterion: scoped autorouting demonstrably reduces total effort without weakening
electrical review, artifact reproducibility, or release confidence.

## Adoption criteria

The skill may offer scoped autorouting as a normal board-layout option only after:

- the DSN/SES compatibility matrix has no unexplained load-bearing gaps;
- manifest extraction and generator application are deterministic;
- scratch-path protections have been tested against the real artifact path;
- known-bad calibration cases fire for scope violations and critical-route changes;
- full KiCad and project-specific checks pass after manifest application;
- at least one representative A/B trial shows a net reduction in time to signoff.

Whole-board autorouting remains an explicit, board-specific choice even after scoped
routing is adopted.

## Open questions for Phase 1

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

These questions do not change the authority model: until they are answered, router output
remains an untrusted scratch artifact and cannot be promoted directly.
