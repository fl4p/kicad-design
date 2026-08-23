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
- [Scope external autorouting](#scoped-external-autorouting-opt-in-when-native-routing-stalls)
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

## Scoped external autorouting: opt in when native routing stalls

Choose routing ownership before choosing a backend:

| Mode | Purpose | Authority |
|---|---|---|
| **Exploratory** | Probe placement, congestion, possible corridors, via pressure, and whether the current floor plan is plausibly routable | Disposable report only. Never promote it, and do not transplant its coordinates into generator source as if they were reviewed routes |
| **Critical** | Implement geometry whose shape carries an electrical, thermal, safety, or fabrication requirement | Generator-owned on generated boards; manually authored only on explicitly hand-maintained boards. Route and audit it before making the promotable seed |
| **Routine** | Complete explicitly allowlisted low-risk connectivity around the finished critical skeleton | Freerouting may propose it; only verified canonical manifest geometry becomes a generator input |

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

An exploratory route may include critical nets only as a congestion probe. Use
its existence, corridor choices, via hotspots, and failures to revise placement
or plan the critical skeleton; discard the trace geometry itself. Then author
critical routing, planes, keepouts, and their audits, then emit the deterministic
seed for the promotable routine scope. The project seed audit must prove each
declared critical route group is present with its required geometry. The wrapper
then locks every existing route only in the scratch export board and proves that
DSN represents it as fixed copper; scratch lock bits never enter the manifest or
final board. This order gives critical structures first claim on space while
still using the router as an early floor-planning instrument.

### Pin the router's configuration, not just its binary

A route manifest that records the router's SHA-256, its JRE and a compatibility cell still does not
make the routes reproducible. Measured on one board with the pinned jar and one unchanged DSN:
**62 track-width errors with the fanout stage enabled, 0 with it disabled.** Same binary, same
input, materially different copper. Whoever re-runs the seed gets whatever their local router
config happens to say.

Record the routing settings that change geometry in the manifest alongside the binary digest — at
minimum the fanout, neckdown, pass-count, thread and clearance-source values — and have the
verifier compare them, so a differently-configured re-run is a refusal rather than a silent
divergence.

### Disable fanout, or expect stubs narrower than the class width

On Freerouting **2.3.0**, with the configuration below, the **fanout stage sized pad escape stubs
independently of the net class**: a class declaring 0.2 mm got 0.15 mm stubs — below the board minimum, so every one is a
DRC error. They are easy to misread: they sit next to vias, a few per net, scattered across many
nets, which looks like congestion-driven narrowing rather than one stage applying its own policy.

- The setting is `router.fanout.enabled` — settable **either** in `freerouting.json`
  (`FanoutSettings`, `@SerializedName("enabled")`) **or** on the command line as
  `--router.fanout.enabled=false`. Both were observed working on 2.3.0.
- **Neckdown is a different thing, and on this board it was not the cause.**
  `router.automatic_neckdown` and `router.neck_width_um` are real settings; disabling both changed
  nothing, and only fanout did. Do not diagnose this from a filename in a repo or from the
  plausibility of the word — and do not generalise this attribution to another board without
  re-measuring, because both stages can undersize copper.
- The router's own log names the stage: `Fanout pass #1 ... N SMD pins fanouted, +M extra vias`.
  Read it before changing settings.

**Read the router's OWN log file, not your console redirect.** Freerouting confirms each accepted
override at DEBUG level — `Applied CLI router setting: router.fanout.enabled = false` — in the file
named by `--user_data_path`, which a `> log 2>&1` redirect does not contain. An investigation that
reads only the redirect sees silence and concludes the flag was ignored; that mistake cost a false
"the tool silently drops unknown settings" claim here. Confirm a setting took effect from the
`Applied CLI router setting` line AND from the output itself (SES wire widths, stage lines) — never
from the exit code.

### A router's DRC-clean result is not a design-conformant result

Run the project's own audits over any external router's output before believing it. DRC enforces
what has been *serialized* into rules it can read; it cannot know a barrier that exists only as
design intent, or any requirement that lives in the generator rather than in the board. A barrier
encoded as native keepout or custom-rule geometry **is** enforceable by DRC — the gap is
serialization, not capability.

Measured on one 4-layer isolated board, whole-board scout: an external router returned **0 DRC
violations and 0 unconnected**, routed every net including four gate escapes a second router could
not complete — and the project audit refused it on its **first** check, with **35 items of copper
inside the 4 mm galvanic isolation barrier**, host-side `+5VH` and isolated-side `GND_OUT` in the
same gap. KiCad scored that board clean because nothing had asked it about the barrier (see
*Isolated designs*, below).

The converse also holds, so neither layer subsumes the other: on the same project a **dangling via
on bare laminate passed all 11 audits** and was caught only by DRC. Report which layer produced a
verdict; "clean" without naming the checker is not a claim.

Read a router's failure honestly, too — but do not moralise it. A **reported** unrouted connection
is easier to reject than a silent violation, because it arrives labelled; that is a statement about
visibility, not about safety. Both are release blockers, and an open gate-drive, interlock,
shutdown, sense or return connection can be *more* hazardous than some constraint violations if the
downstream process does not fail closed. Do not infer motive either: a failed search is not
evidence that the router declined something as illegal, particularly when the constraint was never
given to it.

### Check that each constraint reaches an input the router consumes

The boundary that matters is not "board geometry" — it is **whether the constraint was serialized
into an input the chosen router actually consumes.** A constraint never serialized into any
consumed interface is invisible to the external tool; that is a property of the design, not of the
router.

Know what each carrier can hold. DSN is router *input* and can carry exported netclass widths and
clearances plus native keepouts. SES is returned routing, not a constraint authority. A `.kicad_dru`
expresses far richer KiCad rules but reaches an external router only if that router parses it.
Netclasses cannot encode a regional barrier at all. A generator may also emit router-specific
configuration directly, which is sometimes the only carrier that fits.

A rule area constrains only what its flags say. Measured on one board: **16 rule areas, every one
fill-only** — `noFill=True`, `noTrack=False`, `noVia=False`. They stop zone fill under the FET
bodies and permit tracks and vias everywhere. Its exported DSN carried 20 `(keepout` records, all
on one layer; the two counts are not the same population and were not traced to each other. The
routing constraints that mattered (layer restriction, bounded escape stubs, entry-band exclusion,
isolation barrier) existed **only as Python in the generator**.

If external routing is intended, do **not** hand-author the rule areas alongside the generator's
own constraint logic: two independently written expressions of one constraint will drift, and the
drift is invisible until a board ships. Keep **one authoritative constraint model** and derive from
it (a) the emitted board keepouts, custom rules or router configuration, and (b) the audit's
expectations. Verify after serialization and reload that the emitted carrier still says what the
model says, then produce and inspect the exact DSN, router configuration, or other input the chosen
router consumes. Check boundaries, layers, flags and precedence there, not merely that some rule
area exists in KiCad. Calibrate the consumed-input path with a known-bad route that the constraint
rejects and a legal route that remains accepted; a board reload alone does not prove enforcement by
the external router.

Preserve parity-sensitive board data exactly through the transformation: root-sheet net names keep
KiCad's leading `/`, footprint instance paths remain attached to the same instances, fitted/DNP and
BOM flags and custom fields remain unchanged, and one-pad `unconnected-(...)` pseudo-nets remain in
the authoritative saved board. If a pinned router version requires a pseudo-net or other
compatibility cleanup, apply it only to the temporary router input and audit the before/after
inventory against an explicit allowlist; never normalize the final board to suit the router.

### Diff the project file after any external router runs

A router may rewrite `.kicad_pro`. One measured case relaxed
`rules.min_hole_clearance` 0.25 → 0.175 mm and `net_class[Default].clearance` 0.2 → 0.175 mm, and
downgraded eight DRC severities to warning or ignore — on a run whose own reconciliation pass
reported nothing to route. It printed a loud banner saying so, which is more than most would.

**Never grade a router's output against an unreviewed rewrite of your rules.** Grade first against
the original authority, which is what exposes the change at all. A transformation may legitimately
need a local rule or project migration to represent its copper honestly, so a candidate project can
become a correct oracle — but only after an independent diff review approves it, and then it is
graded as a second, named result rather than silently replacing the first. Treat a relaxed fab
floor as a release blocker requiring an explicit waiver, never as a routing outcome. Note also that
lowered rules do not *guarantee* a clean read; they only remove the grounds on which the result
would have been refused.

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

**Which backend: judge by difficulty, and judge it from behaviour.** An owned pattern
router — enumerate candidate polylines per connection, take the first that clears —
is the right tool while its failures stay *diagnosable*: every constraint is a
function in your own generator, and an unrouted net names one connection you can
reason about. Reach for Freerouting when that stops being true.

Resist "small board / big board". Difficulty is density, layer count, placement
quality and how constrained the critical nets are — a large sparse board can be
easy and a small tightly-packed one impossible — so connection count is a poor
proxy and this file deliberately gives no threshold on it.

A pattern router's cost profile tells you **where the time goes, not how it grows**.
Measured on one 4-layer, 169-connection board: 96 % of routing runtime went into
calls that FAILED (411 failing calls / 128.3 s against 138 successes / 4.9 s),
because a successful enumeration stops at the first clear polyline while a failing
one usually — not always — exhausts every family. That makes failures the thing to
optimise and the thing to watch. It does **not** by itself establish superlinear
growth: a bounded candidate family with a stable failure fraction produces the same
96 % at any size, and one pathological family or a few congestion hotspots would
produce it too.

So the switch signal is behavioural, and you can observe it without a threshold:
the unrouted list stops being individually diagnosable; the failure set grows
between rip-up rounds instead of shrinking; or you catch yourself widening
candidate families instead of fixing placement. Any of those means the enumeration
has stopped being the right instrument.

When the native path is slow or is not converging, consider an exploratory Freerouting run as
placement guidance even if the native or pattern router will ultimately finish the board. Its first
pass can provide evidence about congestion and placement even when none of its geometry is
promoted. Discarded scout geometry commits nothing, but running and reviewing it still has a cost;
promotion remains a separate, explicit project decision governed by the scope and manifest
machinery below.

For a generated board whose project has explicitly opted into external routing, Freerouting can be
the **candidate backend for the project's declared routine scope** when placement and rules are
mature and all of these tracked inputs exist:

- `autoroute.json` with an exact backend, net-class allowlist, layer allowlist,
  styles, limits, seed baseline, reviewed selected-scope/audit policy, and
  manifest path;
- a dedicated KiCad net class whose live `.kicad_pro` assignments and dimensions
  match that configuration exactly;
- a generator stage that emits a deterministic, filled seed with only the named
  routing tasks open; and
- a project-local, Freerouting-independent manifest applicator.

If any item is absent, do not start a promotable run; onboard it with the
scaffold below or keep the existing native/manual routing path. This is not
permission for silent whole-board autorouting. Placement, fanout, high-current
copper, critical nets, differential/skew constraints, isolation, planes, zones,
and post-route stitching stay generator-owned unless the project explicitly
defines and audits a different boundary. Freerouting does not place footprints;
a poor resistor/capacitor grid is a placement problem and must be fixed before
routing.

For an opted-in project that does not yet have `autoroute.json`, use the v2 scaffold. It
supports generated projects through a small language-neutral adapter, existing
hand-maintained KiCad projects through an immutable board snapshot, and a
standalone `.kicad_pcb` through explicit board-only authority. The last mode
creates a minimal `.kicad_pro`; it never invents a schematic, and every report
retains the permanent parity/ERC waiver.

```sh
# First write a read-only, reviewable plan. The selected-scope declaration is
# mandatory: use --project-audited when geometry-dependent checks are needed.
python3 scripts/kicad_autoroute_scaffold.py plan project/board.kicad_pcb \
  --mode board-snapshot \
  --use-net-class AutorouteRoutine \
  --layer F.Cu --layer B.Cu \
  --reset-all-selected-routing \
  --selected-scope-routine \
  --output work/autoroute-scaffold-plan.json

python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-scaffold-plan.json \
  --approve-plan-sha256 PLAN_SHA256

python3 scripts/kicad_autoroute_scaffold.py check project/board.kicad_pcb \
  --report work/autoroute-scaffold-check.json
```

`check` is fail-closed and reports a phase, not a vague boolean: project
context, configuration, primitives, adapter, audit, toolchain, migration,
stale-source, report-only platform, baseline-ready, or candidate-ready. It
re-expands recursive source declarations, runs the adapter on temporary output,
checks every protected seed route, and refuses KiCad format/default-stackup
migration. The source board remains editable and authoritative; generated seed
and final boards live below `build/autoroute/` and are not edit targets.

Use `--create-net-class` only with an explicit reviewed net allowlist and exact
style dimensions. It rejects an existing class name and conflicting effective
assignments. `--use-net-class` freezes KiCad's effective board resolution,
including pattern results, as a finite `net_to_class` inventory. A reset is an
exact UUID/geometry/locked-state multiset, never “delete whatever happens to be
in this class now.” Initial snapshot support is deliberately limited to track
segments and F.Cu-to-B.Cu through-vias; arcs, blind/buried vias, and microvias
remaining anywhere in the protected seed block promotion.

The generated `autoroute_adapter.py` is complete in snapshot mode. In generator
mode it is intentionally a `BLOCKED_ADAPTER` template: implement `describe`,
`seed`, and `final` for the project's generator without AST rewriting. Its
`final` operation invokes the promotion-pinned applicator, which reruns the
adapter's `seed`, verifies the reviewed semantic/context attestation, and then
applies canonical route records. The generated `autoroute_apply.py` is
generator-neutral and owns source/reset/manifest validation plus canonical
segment/via application. For
`--project-audited`, replace the fail-closed audit stub with project physics
checks and a known-bad calibration; `--selected-scope-routine` waives only
geometry-dependent checks on the selected nets, never generic integrity checks
or critical copper elsewhere.

Editing either blocked template invalidates its configured digest. Repin those
tools through a new digest-approved plan, never by silently editing
`autoroute.json`:

```sh
python3 scripts/kicad_autoroute_scaffold.py repin-plan \
  --config project/autoroute.json \
  --output work/autoroute-repin-plan.json
python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-repin-plan.json \
  --approve-plan-sha256 PLAN_SHA256
```

The production flow is a candidate-and-promotion pipeline:

```text
accepted or authorized critical-footprint floorplan -> optional exploratory scout
-> revise placement/corridors if needed -> discard scout copper
-> update artifact and renew user approval or recorded autonomous decision as applicable
-> generator-owned critical skeleton -> deterministic seed with routine opens
-> Freerouting routine candidate -> verification -> route manifest -> final generator
```

```sh
# From the kicad-design skill root. Status is read-only; install needs explicit
# user authorization and an explicit --yes.
python3 scripts/kicad_autoroute_tools.py status
python3 scripts/kicad_autoroute_tools.py install --yes

# Set this to the Python interpreter shipped with the installed KiCad build.
# The candidate wrapper can discover it, but seed generation and promotion use
# pcbnew directly and therefore require an explicit executable.
KICAD_PYTHON=/path/to/kicad-bundled-python3

# Adapter command: emit the project-owned seed bundle with same-stem context.
"$KICAD_PYTHON" project/autoroute_adapter.py seed \
  --output-dir project/build/autoroute/seed \
  --report project/build/autoroute/seed/adapter-report.json

python3 scripts/kicad_route_candidate.py project/build/autoroute/seed/board.kicad_pcb \
  --config project/autoroute.json \
  --report work/route-report.json \
  --keep-workspace work/router-workspace \
  --fail-on-findings

# After visual review, copy candidate.board_path and the exact candidate/report
# digests from the report. Promotion refuses changed inputs, a substitute board,
# or a non-promotable verdict.
"$KICAD_PYTHON" scripts/kicad_route_manifest.py promote \
  --seed work/seed.kicad_pcb \
  --candidate-board CANDIDATE_BOARD_PATH \
  --config project/autoroute.json \
  --report work/route-report.json \
  --project-root project \
  --approve-candidate-sha256 CANDIDATE_SHA256 \
  --approve-report-sha256 REPORT_SHA256 \
  --output-manifest project/routes.json

# The adapter/applicator consumes only the reviewed manifest, not Java/DSN/SES.
"$KICAD_PYTHON" project/autoroute_adapter.py final \
  --manifest project/routes.json \
  --output-dir project/build/autoroute/final \
  --report project/build/autoroute/final/adapter-report.json

# Prefer one project-owned final wrapper that regenerates reproducibly and emits
# a canonical report covering DRC, parity, calibrated audits, and exact routes.
"$KICAD_PYTHON" project/verify_final_pcb.py
```

Treat DSN, SES, Freerouting's completion count, and the raw imported board as
untrusted evidence. KiCad's SES importer replaces routing rather than providing
a trustworthy edit script. The wrapper therefore locks and proves the seed,
extracts the raw addition delta, discards excluded-net/layer additions, applies
only canonical segments and F.Cu-to-B.Cu through-vias to a fresh seed, and then
proves that every protected seed primitive remains. Never promote the raw SES
board.

Freerouting 2.3.0 requires both `--router.automatic_neckdown=false` and
`--router.fanout.enabled=false` for exact-width manifests. Its fanout fallback can
emit micro-neckdown segments even when automatic neckdown is disabled. Its `-inc`
ignored-class option is advisory only; the post-import filter and manifest scope
are authoritative.

A green DRC is necessary but not sufficient. Promotion also requires the exact
position-sensitive seed DRC multiset, zero final unconnected items, schematic
parity, complete non-routing projection equality, protected-route equality,
calibrated final project audits for project-audited scope (or the explicit
reviewed routine-scope declaration),
unchanged source/input bundles, exact
toolchain receipts, and a promotion-enabled compatibility cell. A new KiCad,
`pcbnew`, OS, architecture, Java, or Freerouting version starts staged/report-only
until that exact cell is qualified.

The v2 manifest is the only generated source input: canonical segments and
through vias, exact nanometre geometry and style, the reviewed semantic/context
seed attestation, project input bundle, project applicator hash, toolchain
receipt, and candidate/report digests. The final adapter must re-create that
attestation before applying it and re-extract the final routes to prove exact
equality. `seed_sha256` remains byte evidence, not v2 authority; the legacy v1
contract still requires exact seed bytes. Re-running Freerouting is not part of
board reproduction.

Make the final verification result a canonical, tracked machine-readable report,
not a set of unrelated terminal transcripts. It must bind the final board digest
and promoted route digest and include a full two-run reproduction result, JSON DRC
with schematic parity, the calibrated project-audit result, and exact manifest
re-extraction. A failure in any member makes the report fail; a DRC-only report is
not release evidence.

See [`scripts/README.md`](scripts/README.md) for the command contract and
[`drafts/PCB-AUTOROUTING.md`](drafts/PCB-AUTOROUTING.md) for the research evidence
and limitations behind this policy.

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
