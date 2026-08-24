# PCB layout and footprints (KiCad)

Companion to `SKILL.md`. **Read this file when the task involves the board** —
`.kicad_pcb` layout, routing/autorouting, zones, stackup, creepage, surface leakage, or
board-side placement judgement. Route footprint, scripting, verification, release, thermal, and
variant work to the concern-specific companions below. Schematic-only work does not need this file.

Everything in `SKILL.md` still applies here: preserve the project's declared
source authority, select the verification rungs that cover the change and the claim, and write
guards that fail when they cannot evaluate their input.

**This file is the board-layout core.** Concern-specific companions carry the rest, so a task
pays only for what it needs:

| file | read it when |
|---|---|
| [`SETUP.md`](SETUP.md) | the task designs or reviews circuitry, placement, or layout around a component that meets `SKILL.md`'s device-evidence criticality test |
| [`GUARDS.md`](GUARDS.md) | writing or reviewing board audits, geometry checks or calibration harnesses |
| [`FOOTPRINTS.md`](FOOTPRINTS.md) | editing a footprint, choosing a land pattern, changing a package |
| [`PCBNEW.md`](PCBNEW.md) | scripting `pcbnew`, chasing a wobbling md5, or a slow generator |
| [`RELEASE.md`](RELEASE.md) | verifying a board, or answering "is this ready to fab?" |
| [`THERMALS.md`](THERMALS.md) | dissipation, heat paths, thermal pads/vias, gradients, or temperature validation |
| [`VARIANTS.md`](VARIANTS.md) | native or generated assembly/board variants |

## Contents

- [Gate detailed placement and routing on floorplan review](#gate-detailed-placement-and-routing-on-floorplan-review)
- [Prove placement is route-ready and define completion](#prove-placement-is-route-ready-and-define-completion)
- [Use the incremental footprint-swap path](#use-the-incremental-footprint-swap-path)
- [Scope external autorouting](#scoped-external-autorouting-opt-in-when-native-routing-stalls)
- [Record and share layout experience](#record-and-share-layout-experience)
- [Place board annotations from board geometry](#place-board-annotations-from-board-geometry)
- [Validate decoupling loops](#decoupling-is-a-current-loop-not-a-placement-radius)
- [Declare the stackup](#the-stackup-is-part-of-the-design-not-a-fab-preference)
- [Guard symmetry and matching](#symmetry-and-matching-are-invisible-to-drc)
- [Measure surface-leakage paths](#surface-leakage-measure-the-path-not-the-gap)
- [Audit isolated-domain clearance](#isolated-designs-the-binding-clearance-is-zone-to-zone-and-drc-is-not-asked)

## Gate detailed placement and routing on floorplan review

Before creating the floorplan, require the schematic-capture completion gate in
[`SCHEMATIC.md`](SCHEMATIC.md) to pass. Do not start or delegate PCB placement from a connectivity-
only schematic, generic-symbol draft, stale netlist, or schematic whose rendered semantic review is
still open. If the user explicitly requests a parallel outline or mechanical study that does not
depend on unresolved connectivity, label it provisional, keep it separate, and do not promote it to
the implementation board until the gate passes. Critical-footprint placement starts only after the
gate.

Before committing a new or materially changed critical floorplan, create a review artifact that
shows the proposed critical footprints. Use either an image or a provisional `.kicad_pcb`. An
existing board or previously approved design brief is the baseline unless the task changes it.

The artifact must be scaled well enough to review relative size and position and show:

- the board outline, mounting features, and fixed connectors;
- each critical footprint, labelled by reference and function;
- important keepouts, isolation barriers, thermal areas, and intended routing corridors; and
- the main power, signal, and return-flow relationships that drive placement.

Treat a footprint as critical when its location or orientation carries an electrical, thermal,
safety, mechanical, assembly, EMC, or routability requirement, or materially constrains another
critical part. This is the same device-evidence criticality test used in `SKILL.md`; load
[`SETUP.md`](SETUP.md) for every such component. For an image, derive component blocks from actual
footprint body or courtyard
extents when available. For a KiCad artifact, use the selected footprints and leave fine placement
and copper unfinished.

Present the artifact with a short layout rationale covering only the major decisions: functional
ordering, required adjacency, partitioning, orientation, return paths, thermal flow, and mechanical
constraints. State important tradeoffs or uncertainties. Obtain explicit approval when the user or
project retained the choice. When the user requested an autonomous run or the accepted design brief
already authorizes those choices, record the assumptions and rationale and continue without adding
an approval stop.

After approval or a documented authorized autonomous decision, record the reviewed artifact or
board revision and the accepted decisions in the design documentation, then proceed with detailed
placement and routing. Re-open the gate when a change to the board outline, fixed connectors,
critical component or footprint, isolation scheme, major partitioning, or other floorplan
constraint materially invalidates the accepted arrangement. If an exploratory routing scout changes
any critical footprint position, orientation or intended corridor shown in the artifact, update the
artifact. Obtain renewed approval before authoring the critical routing skeleton when the user or
project retained that choice; otherwise update the recorded autonomous decision and continue.

## Prove placement is route-ready and define completion

An accepted or authorized floorplan is necessary but does not prove that detailed placement is
routable. Before promotable routing, audit the placed board using actual pad, body, courtyard,
drilled-hole and side-specific geometry:

- cluster each datasheet-critical bypass, compensation, timing, reference, protection and analogue
  support part with the pins and return it serves; do not assign a critical passive through a
  generic grid fallback;
- orient passives and other routability-sensitive footprints from their pad roles and legal escape
  directions, not only their body outline or schematic order; a 180-degree rotation can determine
  whether the required connection is direct or crosses another constrained pad;
- include the body, courtyard, hole and keepout envelopes on every occupied side and layer,
  especially opposite-side parts around through-hole connectors, fixtures and cable-relief lands;
- reserve explicit pad-escape and layer-transition corridors for constrained nets, including
  guards, Kelvin/sense pairs, differential pairs, slot or barrier crossings, high-current paths and
  their returns;
- check that buses, planes and routine routes have not consumed those corridors;
- assert topology and project-derived maximum path or loop bounds for critical groups. Use a
  datasheet, electrical model, accepted floorplan or explicit routing budget for numeric limits;
  never raise a failed bound to match the accidental placement; and
- treat an exploratory router plateau or repeated congestion at the same pins as evidence to
  re-evaluate placement and corridor assumptions, constraint serialization, routing ownership and
  layer strategy. Long cross-board ratsnests from an IC to its critical support parts are direct
  evidence of bad placement. Diagnose the cause rather than adding unsafe jumpers or lowering
  completion criteria.

Represent board-level routed slots and cutouts as closed `Edge.Cuts` contours under a declared
mechanical authority. Direct board drawings are valid; an intentional board-only footprint is also
valid when it owns a reusable local contour, is marked not-in-schematic, is protected from
annotation/update replacement, and its emitted contour is audited. Reject only unowned or
unverified helper-footprint proxies. Remeasure outline, count and datums from the saved board;
generator or footprint constants alone do not prove the interface that will be fabricated.

Classify the requested outcome before routing:

| outcome | permissible residual |
|---|---|
| **Placement or routing draft** | May retain ratsnests and named DRC findings when the user explicitly requested a draft; enumerate them and do not call the board complete or fabrication-ready |
| **Completed PCB implementation** | Zero electrical unconnected items; zero unresolved applicable electrical, copper, outline or other completion-critical DRC findings when graded against the authoritative rule map, regardless of an accidental or unapproved warning/ignore severity; a valid closed outline; applicable schematic parity and project-critical route/return/guard audits passing; every inapplicable check, exclusion or approved waiver explicit and scoped |
| **Fabrication release** | Completed PCB gate plus the release evidence in [`RELEASE.md`](RELEASE.md), relevant physical-sample and enclosure decisions, fabrication outputs and bound reports |

An operational limit—router pass count, time budget, flattening search progress, tool failure or
agent cutoff—does not change the requested outcome. Preserve the best candidate, then revise
agent-owned placement, routing ownership, constraint encoding or layer strategy within the accepted
design space and continue. If a user-owned mechanical, stackup or interface constraint must change,
propose the change and obtain approval. If the completed-board gate genuinely cannot be reached
within the authorized design space, report the task as blocked with exact nets/endpoints and the
structural cause; never rename that checkpoint a completed or bounded deliverable. An exclusion or
waiver can bound a specific physical rule deviation; it can never excuse unrouted electrical
connectivity.

Classify silkscreen and documentation findings separately so cosmetic volume does not hide an
electrical failure. Conversely, open physical-sample, enclosure, potting, thermal or bench-test
gates may prevent fabrication release without excusing unfinished electrical CAD.

The agent responsible for the final handoff must independently reproduce the final artefact from
its declared authority—or verify the exact explicitly hand-maintained final board—and apply the
verification ladder in [`SKILL.md`](SKILL.md). For external routing, verify the promoted
manifest-generated final, never the raw candidate or imported SES board. Require a fresh report
bound to the final artefact, review effective severities, exclusions and applicable board-only
waivers, prove zero electrical unconnected items, and rerun project connectivity/geometry guards.
Do not accept another agent's, autorouter's or wrapper's summary as the completion verdict.

## Use the incremental footprint-swap path

A package substitution on an accepted routed board does not authorize regeneration or whole-board
routing. For a named, already-qualified land-pattern change:

1. preserve the accepted board as the transaction base;
2. replace only the named footprints and map connected pads by pad-number sets;
3. refill before grading, because the old cached fill is not evidence about the new lands;
4. apply only explicit project-owned placement or same-layer local route deltas when DRC proves they
   are needed—no router fallback, new via, or layer transition;
5. require the project's in-memory semantic-settle fill gate, then compare the provisional snapshot
   with the DRC-saved board; and
6. promote all variants and generator-owned overlays through one deadline-bound recoverable
   transaction only after parity, DRC, and project audits pass.

Use `scripts/kicad_footprint_swap.py --spec ...`; dry-run is the default. Generated boards require a
typed project adapter that records the migration in source authority. A missing adapter, stale audit
mechanism receipt, non-settling fill, unrelated semantic change, or local conflict without a declared
delta is a quick refusal, not permission to broaden routing scope. See
[`scripts/README.md`](scripts/README.md) for the transaction contract.

## Scoped external autorouting: opt in when native routing stalls

Choose routing ownership before choosing a backend:

| Mode | Purpose | Authority |
|---|---|---|
| **Exploratory** | Probe placement, congestion, possible corridors, via pressure, and whether the current floor plan is plausibly routable | Disposable report only. Never promote it, and do not transplant its coordinates into generator source as if they were reviewed routes |
| **Critical** | Implement geometry whose shape carries an electrical, thermal, safety, or fabrication requirement | Generator-owned on generated boards; manually authored only on explicitly hand-maintained boards. Route and audit it before making the promotable seed |
| **Routine** | Complete explicitly allowlisted low-risk connectivity around the finished critical skeleton | Freerouting may propose it; only verified canonical manifest geometry becomes a generator input |

Keep at least these structures critical:

- low-inductance switching, gate-drive, and decoupling loops, including their
  return paths and via count;
- high-current paths where width is only one part of the electrical structure: neckdowns,
  parallel layers, pours, connector entries and via arrays matter too; when temperature rise or
  heat spreading is load-bearing, also apply [`THERMALS.md`](THERMALS.md);
- creepage/isolation barriers, bounded crossings, slots, keepouts, and any copper
  whose all-layer distance implements a safety requirement;
- RF/HF, controlled-impedance, differential/skew, clock, and other
  stackup/return-path-sensitive routes; and
- Kelvin, sense, guard, star-point, plane-entry, and other topology-bearing nets.

A driven guard is a topology, not merely copper carrying the guard net. Audit its driver and
reference, electrical continuity, required enclosure and adjacency, and any requirement-derived
layer-transition, via, solder-mask or exposure constraints. Same-net decorative copper is not proof
that the guarded path is continuously protected.

A uniform trace width and clearance can remain routine when those dimensions are
the whole requirement and the exact class/style is checked after import. If the
requirement is really current density, temperature rise, impedance, inductance,
loop area, creepage, or return continuity, DRC-clean width/spacing is insufficient
and the route is critical. For thermal cases, apply [`THERMALS.md`](THERMALS.md).
For generated boards, “manual” means deliberately authoring the route in generator
source—not editing the generated `.kicad_pcb`.

Treat every routing or repair pass as a transaction against a preserved, KiCad-graded checkpoint:

- clear placement-origin shorts, ordinary clearance, hole and edge failures before routing so later
  findings have an attributable cause;
- allow rip-up only for named blocking nets or an explicitly named interacting bundle, and protect
  all unrelated accepted copper;
- compare the exact gained and lost unconnected endpoint identities, DRC findings and protected
  primitives after every pass; a lower total count can hide a reopened accepted connection; and
- keep and restore the best checkpoint by an explicit comparator. Reject a later pass that regresses
  it, including a routed board re-exported through Specctra or another router representation.

When a local nudge trades one corridor violation for another, restore the clean checkpoint and
reroute the whole interacting bundle rather than promoting a less visible defect.

External autorouting is optional. Start with the project's established native, manual, interactive,
or generator-owned routing path. Consider an exploratory Freerouting run when routing is in scope
and the native path consumes disproportionate time, repeated rip-up iterations stop reducing the
unrouted set, or individual failures are no longer diagnosable. A board with more than roughly 50
routing-relevant nets is a useful prompt to consider it, not a threshold: remaining connection
count, density, layer count, placement and constraint complexity matter more than net count. Do not
invoke Freerouting solely because the board crosses that heuristic.

The operational workflow lives in [`AUTOROUTING.md`](AUTOROUTING.md): scout-first ordering,
router pinning and configuration, fanout hazards, constraint serialization, project-file
diffing, backend choice, scaffold onboarding, and the candidate-and-promotion pipeline through
the route manifest. Read it before invoking any `scripts/kicad_autoroute*` helper or promoting
external routes.

## Record and share layout experience

For every non-trivial PCB layout phase, start or continue a project-local layout retrospective
before detailed placement and routing. Use the project's existing engineering journal when it has
one; otherwise create `PCB-LAYOUT-RETROSPECTIVE.md` beside the design documentation. Update it while
working, not from memory at handoff, and keep board-specific facts separate from lessons that could
change how another project is laid out.

Record:

- the board revision, layout objective, constraints, and final outcome;
- the routing strategy and why it was chosen, including routing ownership, critical-first ordering,
  layer and via policy, reserved corridors, zone ordering, and any external-router role;
- start and stop timestamps plus measured elapsed wall-clock time for each material phase, such as
  floorplanning, placement, critical routing, routine routing, zones and cleanup, verification, and
  rework. Separate active work from known tool or approval waits when practical; do not replace
  measurements with retrospective estimates;
- failed or abandoned approaches, their observable symptoms, and what changed the outcome; and
- genuinely reusable findings with the evidence that supports them. Do not generalize a result that
  depends on an unrecorded board, toolchain, stackup, or constraint.

At the end of the design phase, summarize the reusable findings in the retrospective. Before
asserting a cross-project finding, check `~/dev/kb/INDEX.md` and read applicable notes. Apply the
full `~/dev/kb/AGENTS.md` contract: create or update the canonical note when the finding
generalizes, or use a pointer when canonical material belongs elsewhere. Do not duplicate canonical
material merely to satisfy this step.

When a finding can improve the KiCad design workflow, search
<https://github.com/fl4p/kicad-design/issues> for an existing report, then prepare an issue or an
update/comment containing the routing strategy, measured phase-time breakdown, failed approaches,
outcome, verification scope, and reusable recommendation. Update the existing issue when it covers
the same experience instead of opening a duplicate. Publishing to GitHub is an external side
effect: obtain explicit authorization unless the user has already authorized it for the task, then
publish the issue or update/comment and record its URL in the retrospective. If the phase produced
no genuinely reusable finding, record that conclusion and do not create a noise issue.

## Place board annotations from board geometry

Schematic text does not help the assembler looking at the PCB. Put connector pinouts, polarity,
hazard markings, voltage callouts, and revision identifiers on the appropriate board layer and
derive their anchors from the placed footprint's real pad centres so they follow moves and
rotation:

```python
footprint = board.FindFootprintByReference(reference)
pad = next(p for p in footprint.Pads() if p.GetNumber() == pad_number)
position = pad.GetPosition()
```

Choose the text side and orientation from available board geometry rather than applying one fixed
offset to every connector. Check footprint courtyards, edges, nearby silkscreen, reserved label
lanes, and readable orientation; then run DRC and render both board sides.

KiCad DRC does not establish a fabricator's minimum silkscreen stroke or text height. Read the
current selected fabricator's published capability, record its source/date, and guard every emitted
text object against those limits. Do not carry an undated numeric minimum from another fab or
process into a new project.

Treat silkscreen as reserved layout territory. A legal component placement can still consume the
only lane wide enough for a hazard or connector label, so make required annotation placement a
fail-closed generator step rather than a final cosmetic pass.

## Decoupling is a current loop, not a placement radius

Validate every datasheet-critical bypass by following actual copper from the IC supply pin to
the capacitor and back to the specified return pin or plane entry. Check both capacitor pads,
vias and layer changes. Centre-to-centre or hot-pad-to-pin Euclidean distance can pass a long,
inductive return path and cannot prove that the capacitor is connected across the required
two nodes.

- Place the smallest/highest-frequency capacitor first and route its loop short and direct;
  keep bulk capacitors from displacing it.
- Verify topology as well as distance. Two capacitors in series through a ground net do not
  implement a datasheet-required direct rail-to-rail capacitor.
- Derive any numeric audit limit from a datasheet, package/application geometry or an explicit
  loop-inductance target. Do not raise a failed limit to the distance the finished placement
  happens to provide and then describe that value as verified.
- Make the audit fail when it cannot reconstruct the complete loop. Reporting only the nearest
  pad-to-pin distance creates a precise number for the wrong property.

## The stackup is part of the design, not a fab preference

If the board file has no `(stackup ...)` block, KiCad assumes a default and the
fab builds whatever is cheapest that week — while your design doc quotes
dielectric-dependent numbers (stray capacitance, impedance, creepage class) that
depend on a stackup nobody agreed to. Write it explicitly.

The SWIG `pcbnew` API does not usefully expose `BOARD_STACKUP` (you get an opaque
`SwigPyObject` with no methods), so this has to be a text edit on the saved
`.kicad_pcb`. Anchor it on a regex that captures the existing indentation —
KiCad indents with tabs, and a hardcoded two-space `"\n  (setup\n"` anchor will
not match. **Make the failure loud**: if the anchor is missing, exit; do not
return quietly, or the stackup silently stops being written and every number that
depends on it becomes unbacked. Then verify KiCad *parses* it rather than merely
tolerating it — load and re-save through `pcbnew` and confirm the block survives
the round-trip.

## Symmetry and matching are invisible to DRC

A board can be DRC-clean, parity-clean and **completely asymmetric**. Nothing in KiCad checks
that a differential pair is matched or that two halves of a current path mirror. If the
design's accuracy rests on symmetry, it rests on a guard you write, and the design docs must
say *that* guard — not DRC — is what enforces it, or the next tidy-up deletes it as redundant.
For matched placement under thermal gradients, read [`THERMALS.md`](THERMALS.md).

Four traps, all met on one precision current-sense board:

- **Derive the mirror's net map from the schematic, not from the net names.** Under a
  left/right mirror the nets swap in pairs, and the pairing is a circuit fact: `Shunt+ ↔
  Shunt-` is obvious, but on that board `Net-(JP1-A) ↔ GND` too, because those were the
  shunt's two Kelvin sense terminals. Assume every net maps to itself and the audit reports
  the correctly-mirrored sense pair as broken while missing the pours.

- **A footprint's `(at …)` is a proxy for where the part is; the pads are the truth.**
  `Wuerth_PowerPlus_M5_Nut` carries its origin **1.5 mm off its own pad cluster**, so two lugs
  at x = 109.5 and 132.5 — apparently centred on 121.0 — actually have their pad clusters at
  108.0 and 131.0, i.e. exactly symmetric about the board centre at 119.5. An origin-based
  check reports a false asymmetry. Worse, the *same* proxy error had already reached a design
  review, which derived "the symmetry axis is x ≈ 121" from those origins and concluded the
  wrong one of two matched resistor networks occupied the critical side. Compare pad sets.

Matched current pours need the filled-geometry, topology and raw-quantity contract in
[`GUARDS.md`](GUARDS.md), not outline or area comparison alone. Apply
[`THERMALS.md`](THERMALS.md) additionally only when temperature or heat spreading is load-bearing.

**Symmetry is not the whole objective — check the loop area too.** A mirror-symmetric
differential pair can still be a large pickup loop, and the audit that proves the symmetry
will happily pass it. On that board the sense pair was laid out perfectly symmetric at 4.2 mm
spacing, enclosing **143 mm²** between the two conductors; re-laying it at 0.6 mm inside the
existing 1.0 mm gap between the two current pours cut that to **26.5 mm²** with no change to
the symmetry verdict, no new DRC violations, and *more* pour copper than before, because the
pair now runs in a gap that already existed instead of slicing a fresh void through each pour.
Compute the enclosed area explicitly — shoelace over `[source pad A, …trace A…, load, …trace B
reversed…, source pad B]` — and put the number in the design doc, because nothing else will
ever tell you it is too big. Two corollaries: the fan-out from the source's pad pitch is often
the dominant remaining term, so converge it steeply rather than at a tidy 45°; and running the
pair down the gap between two pours *guards* it, provided each conductor is flanked by the
pour nearest its own potential rather than the other one's.

Finally: a symmetry audit is exactly the kind of guard that must fail closed. An unreadable
outline, an object class the parser never visited, or a pair that could not be compared has to
raise — "0 asymmetries" out of a scan that examined nothing is the anti-monotone false PASS.
Follow [`GUARDS.md`](GUARDS.md)'s subject inventory, stable failure IDs and bad/legal calibration
pair so the audit proves both detection and valid-domain headroom.

## Surface leakage: measure the PATH, not the gap

Clearance asks *how far apart are these two nets*. Guarding asks *is there anything at a
harmless potential in between* — a different question with a different answer. Two nets 4 mm
apart with open laminate between them are worse than two 0.75 mm apart with a guard trace
interposed, because a guard absorbs the leakage and what reaches the victim is set only by the
**guard-to-victim** potential difference.

This matters wherever a high-impedance node meets a rail. The arithmetic is short: leakage
through `R_leak` into a source impedance `R_s`, referred to the input, is `V_driver · R_s /
R_leak`. On a 1 kΩ source with a 3.3 V rail 0.75 mm away and a flux- or humidity-degraded
surface insulation resistance of 10⁹ Ω, that is 3.3 nA → **3.3 µV**, which on a ±35 mV
converter is ~94 ppm — the same order as the silicon's own input-current term, and *offset*
rather than gain, so calibration does not remove it and it moves with humidity. Clean masked
FR4 at 10¹² Ω makes the same geometry irrelevant. **The budget therefore lives in the assembly
process, not the layout**: say whether the board is washed, and whether it is coated.

Sort the paths by what they cost you:

- Leakage **inside the feedback network** (output to summing node, divider to tap) is in
  parallel with a feedback element, so it is a **gain** error — `Rf / R_leak` — and calibrates
  out. 100 kΩ against a contaminated 3 × 10⁹ Ω is 33 ppm, and 0.1 ppm clean.
- Leakage **from a rail** is signal-independent, so it is an **offset**. That is the one to
  engineer against.

### Measuring it

Rasterise the layer, flood-fill from the victim net through everything that is **not** guard
copper, and report the geodesic length of the shortest surviving path to each driver net. The
flood routes *around* the guard, which is the point — a straight line does not.

Do not substitute a proxy. Sampling the straight line between two nets and reporting "what
fraction of it is covered by guard copper" looks reasonable and is not: measured on one board
it gave 1.170 mm where the flood-fill gave **0.72 mm**, wrong in the *optimistic* direction,
because the line test walked tracks only while the real shortest approach was to a **pad**.
Include pads.

Fail closed, or this becomes the anti-monotone false PASS in its purest form — "nothing
reached the victim" is exactly what a rasteriser that found no victim copper also reports:

```python
if not src:    raise SystemExit("UNVERIFIED: rasteriser found no victim copper")
if not nguard: raise SystemExit("UNVERIFIED: rasteriser found no guard copper")
```

Report the geodesic per driver rather than a verdict, because the useful output is a
before/after. On the board above, pushing the guard between the summing node and the rails
moved `+5V` from 0.72 mm to **10.64 mm** and `V−` from 10.16 to 21.20 mm, while the paths that
stayed short were all inside the feedback network — which is the correct end state, not a
failure. Note that **no driver will ever be fully blocked**: a closed guard ring is impossible
on the layer the victim's own traces have to leave by, so ~10 mm is the practical maximum and
"BLOCKED" is not the target.

Two corollaries that decide what to do about it:

- **More copper, not less.** A guard is only a guard while it is adjacent. On a low-level
  front end the input nodes sit within tens of millivolts of ground, so ground pour beside them
  has ~20 mV of driving voltage against 2–5 V for the output and rails — two decades less. The
  instinct to "keep copper away from the sensitive node" is backwards here.
- **A driven guard usually is not worth it.** At 20 mV of signal a plain ground guard is within
  20 mV of the victim, and its residual works out to `I · R_s / R_leak` — a fixed ppm **of
  reading**, i.e. a gain term, 1 ppm at 10⁹ Ω and 1 ppb clean. Driven guards earn their keep at
  gigaohm source impedances or with volts of common mode, neither of which applies.

**The package usually sets the floor anyway.** Before re-routing a pin escape to buy 0.1 mm,
measure the part's own pad-to-pad gap: on a SOT-23 divider the pads are 0.656 mm apart, so a
trace approach of 0.512 mm was chasing a number the package had already capped. Measure pad
copper edge to pad copper edge, not centre to centre.

## Isolated designs: the binding clearance is zone-to-zone, and DRC is not asked

On a board with primary and secondary domains, the minimum copper-to-copper distance almost
never occurs between tracks — it occurs **between the two ground/power pours on the inner
layers**, which is exactly where nobody looks. Measured on one 4-layer board: F.Cu 4.020,
GND 4.000, PWR 4.295, B.Cu 4.000 mm — the two binding numbers were both zone-to-zone.

DRC will not check any of this unless a rule asks it to, so an **independent audit must
measure real geometry on every copper layer**, zones included, and enforce the stated figure.
A secondary track sitting 3.638 mm from primary pads passed DRC cleanly for exactly this
reason. Set the floor *at* the standard, calibrate it by injecting a known-bad geometry, and
scope any package-bridging exemption (an isolator or DC/DC straddles the barrier by
construction) to pairs where **both** items belong to that package **and** touch its pads —
bounded by its own measured floor, so a new object cannot inherit the excuse.

An exemption does **not** satisfy the original requirement. If a package's own measured floor
is below the stated board minimum, report the normal clearance and package deviation
separately and fail release unless the design records an approved waiver or a revised derived
requirement tied to that exact part and working voltage. Never print an overall `PASS >= 4 mm`
for a design whose approved geometry includes 3.5 mm; bounded is not compliant.

**Do not slot a plane to steer digital return current on a precision analog board.** It is
the textbook move and it is usually wrong here: the converter datasheet asks for a
*continuous* return beneath it, and a moat raises the impedance of the **analog** return to
fix a **digital** problem. Solve it with placement — put the noisy return corridor physically
away from the sensitive part and measure the clearance you achieved.
