# Routing craft: topology first, layers second, copper last

This file is about *how to route*, and about how to drive a router so its output is a design
rather than a search log. [`AUTOROUTING.md`](AUTOROUTING.md) governs the external-router
pipeline — scope, pinning, promotion, manifests. [`PCB.md`](PCB.md) governs the floorplan gate
and the completion gates. This file sits between them: the method that makes the copper make
sense, and the executable check that says whether it does.

The evidence behind every numeric claim, with locators and an access log, is in two records:
[`reviews/2026-09-05-routing-methodology-research.md`](reviews/2026-09-05-routing-methodology-research.md)
for the literature and the first measurements, and
[`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md) for the escape, rotation, row-alignment, baseline and convergence experiments (§5, §8–§11).
Read them before changing a rule here.

## Contents

- [Route the topology, not the copper](#route-the-topology-not-the-copper)
- [Placement owns the route, and the ratsnest is the evidence](#placement-owns-the-route-and-the-ratsnest-is-the-evidence)
- [Commit a layer plan before the first track](#commit-a-layer-plan-before-the-first-track)
- [Escape is a stage, and it is where vias are decided](#escape-is-a-stage-and-it-is-where-vias-are-decided)
- [Author the skeleton by hand, in priority order](#author-the-skeleton-by-hand-in-priority-order)
- [On a repeated board, check what KiCad already carries before hand-rolling reuse](#on-a-repeated-board-check-what-kicad-already-carries-before-hand-rolling-reuse)
- [A router's defaults are tuned for completion, not for legibility](#a-routers-defaults-are-tuned-for-completion-not-for-legibility)
- [A sequence of per-net passes is not a routing pass](#a-sequence-of-per-net-passes-is-not-a-routing-pass)
- [Finish with endpoint-led local closure](#finish-with-endpoint-led-local-closure)
- [Grade the shape of the route, not only its DRC](#grade-the-shape-of-the-route-not-only-its-drc)
- [Lane capacity is not routability — measured three times, and the third one agreed](#lane-capacity-is-not-routability--measured-three-times-and-the-third-one-agreed)
- [Do not generalise the anti-autorouter folklore](#do-not-generalise-the-anti-autorouter-folklore)

## Route the topology, not the copper

The most common PCB routing objective is a weighted sum of total wirelength and via count —
"the most common objective function" in "many PCB routing problems", in He 2024's words (§3.4
p. 48); the same objective appears explicitly as `g(x) = C_wl + γ·N_via` in its post-processing
A* (Eq. 3.3, p. 59). That is a prevalent formulation, not a universal one. What matters here is
that it contains no term for "makes sense to a human", and that it is evaluated **per net, in an
arbitrary order** — He 2024's own main loop reads *"Randomly select a net"* (Fig. 3.2, p. 49),
and its experiments use "the default order provided by each PCB design" (p. 60).

So the thing you own, and the router does not, is **topology**: which net goes on which layer,
in which channel, in what order, crossing which other nets. Copper is the last and least
interesting step. Three consequences run through the rest of this file:

- A routing problem you cannot describe as a topology is not ready to route. Fix it in placement.
- The decisions that determine via count are taken before the first track: layer assignment and
  pin escape. Once topology is committed, the remaining lever is largely layer assignment — which
  is the shape of the classical split between *constrained* and *unconstrained* via minimisation.
  That framing is offered as orientation, not authority: the research file records its CVM/UVM
  evidence as second-hand and explicitly non-load-bearing.
- A router given a good topology is a fast draughtsman. A router given no topology is a search,
  and its output looks like one.

## Placement owns the route, and the ratsnest is the evidence

[`PCB.md`](PCB.md)'s route-readiness audit already requires clustering critical passives with the
pins they serve, orienting footprints from pad roles, and reserving escape corridors. Add the
topological reading of the same board, because it is what predicts routing effort:

- **Count crossings, not distances.** Two nets whose ratsnest lines cross must eventually be
  separated by a layer change or a detour — that is a via or a corridor, decided now. Fanout
  automation states this as its whole objective: minimise topology crossings `Tc(n)` subject to
  the routing constraints (FanoutNet, AAAI-23, "Problem Formulation"). A placement pass that
  removes a crossing is worth more than one that shortens a wire.
- **Try rotation as well as translation.** A 180° passive rotation can convert a crossing into a
  straight run, and it disturbs neighbouring parts less than moving one does, so it is worth
  enumerating alongside positions. No measurement here establishes that it succeeds more often
  than a small translation; do not treat it as a ranked rule.
- **Read the long ratsnest lines as placement defects, not routing work.** A line from an IC to
  its own decoupling or reference part crossing the board is already a finding
  ([`PCB.md`](PCB.md)); so is a bundle whose lines all cross the same neck.
- **When you consolidate passives into a row, align the shared pads.** Standing a row of chip
  passives up (see the rotation result below) frees lanes, but if two parts that share a net end
  up with that net's pads at opposite ends of the row, every foreign trace the router later
  accepts between them is a wall. The router then reports two pads one row pitch apart as
  "boxed in", and a finer routing grid does not fix it — an isolated fine-grid probe on the
  simplest such gap still failed, which is what identifies the blocker as topology rather than
  resolution. Flip alternate parts so shared pads face each other, order a divider chain as a
  chain, and pre-author the short row-local links before area routing. Measured once, by one
  agent, as a two-pass reading (see the convergence rule below); not replicated. Treat the rule
  as a design check and the measurement as one board's ([`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md) §5).
- **Fix the aspect-ratio trap explicitly.** On a long, narrow board the layer whose preferred
  direction runs across the short axis has almost no run length to offer, so the router pays vias
  to escape it. Freerouting's own geometry heuristic makes this concrete: it seeds preferred
  direction from `boardWidth < boardHeight` and sets the against-preferred penalty to
  `0.1 · round(10 · W/H)` (`RouterSettings.applyBoardSpecificOptimizations`). On a 20 × 107 mm
  outline that is +0.2 on one axis and +5.4 on the other; against a base trace cost of 1.0 the
  total against-preferred costs are 1.2 and 6.4, a **5.3× ratio between the axes** (the additive
  penalties alone differ by 27×). The seed is flipped before it reaches the first signal layer, so
  such a board starts vertical and then alternates. On a markedly slender board, assign directions
  yourself along the long axis and say so in the layer plan; do not let a geometry heuristic pick.
  This file gives no aspect-ratio threshold, because none has been measured.

## Commit a layer plan before the first track

Write it into the design documentation before routing, at the same rank as the stackup
([`PCB.md`](PCB.md)):

1. **What each copper layer is for.** A 4-layer board is not four routing layers. Name the
   reference planes and the routing layers separately, and state which plane each routing layer
   references — every layer change that also changes reference plane is a return-path
   discontinuity, and that is a circuit decision, not a routing convenience.
2. **The preferred direction of each routing layer**, and the long axis it is aligned to. Every
   serious router takes this as a first-class input: Freerouting exposes per-layer preferred
   direction in the Autoroute Parameter dialog and as
   `--router.layers.preferred_direction_horizontal=…` on the CLI; KiCadRoutingTools has
   `layer_direction_preferences` (0 = horizontal, 1 = vertical, 255 = none), added expressly to
   produce *"more organized, human-like routing patterns"* (`rust_router/README.md`).
3. **Which layers are routable at all.** Both routers let you switch a layer off
   (`--router.layers.routable=…`, KRT `layer_costs`). A plane layer that is nominally "signal" in
   the stackup and never fenced off will be routed into, and the split it leaves is invisible to
   DRC.
4. **The intended via classes.** If every via on the finished board spans the full stack, no layer
   plan was in force — the router was hopping, not transitioning.

Direction discipline is a real trade, so state it as one rather than as an aesthetic. KRT's
configuration notes record a dose curve — but check its arithmetic before quoting it: the claim
that 250 *"priced every off-axis move above 3 vias"* is inverted, because 250 cost units against a
via's 75 000 is **1/300 of one via**. Do not repeat that sentence. What the curve does support is
the shape of the trade: an off-axis penalty of 0 *"loses the layer organization entirely"*, and
5000 *"enforce[s] strict human-style lanes but make[s] dense boards' short diagonal hops
unroutable"*. Lanes cost routability on dense boards. Choose deliberately, per board, and record
the choice.

## Escape is a stage, and it is where vias are decided

Treat pin escape as its own pass with its own acceptance criterion, before area routing. This is
not a stylistic preference; it is how the tools are built. Freerouting runs it as a separate
pre-pass — *"In a fanout pass the connections will be routed only till the first via"* — and pairs
it with a **postroute pass "for reducing the via count and the cumulative trace length"**
(Reference Manual, "Autoroute Parameter"). The published formulation of the stage is
*"assigning layers, determining the fanout locations, and routing from the pin to the location"*,
minimising crossings (FanoutNet).

Rules:

- **Author the escape pattern for every dense cluster by hand**, and make it repeat. A pattern you
  can describe in one sentence ("pins 1–6 escape south to In1, pins 7–12 north to B.Cu") is a
  topology; a per-pin decision is not.
- **Every via placed in this stage is a layer *assignment*, so give it a destination layer.** A
  via that exists only because the search wanted to get out of the way belongs to a later,
  weaker problem.
- **Escape and area routing have different owners.** Escape stays generator- or hand-owned
  ([`AUTOROUTING.md`](AUTOROUTING.md) already keeps fanout out of the promoted scope, and warns
  that the router's own fanout emits stubs narrower than the class width). Area routing is what a
  router is good at.
- **Judge the stage on crossings and on the escape layer histogram**, not on completion.

### Measured caveat: do not hand authored escapes to a router that is already at its limit

This is the rule in this file with the strongest published backing and the worst measured result,
so take the rule as being about **design**, not about copper you give an autorouter.

Measured on the long, narrow 2-layer board this file was written against (evidence record §10),
escapes
authored exactly as described above — uniform, radial, one length per package, every signal pad,
zero pads without a legal escape, `kicad_copper_collisions.py` clean, DRC clean of clearance and
edge findings — then locked and handed to the backend. Only the escapes differ between rows:

| escapes | stubs | unconnected items |
|---|---:|---:|
| none | 0 | **31** |
| the two finest-pitch packages only, where package geometry forbids anything else | 21 | 33 |
| those two plus the third IC | 68 | 37 |

**Both escape sets cost connectivity, at a similar rate per stub** — 0.095 and 0.088
unconnected items per stub. Two nonzero treatments cannot establish that the relationship is
linear, that each individual stub carries a cost, or that the mechanism below is the operative
one rather than something specific to these packages; what they show is that the aggregate
penalty was roughly proportional across the two sets measured. There was no subset that helped, *including the two packages where
radial escape is the only physically possible option*: one package's 0.32 mm pads on 0.5 mm pitch
leave an 0.18 mm gap against the 0.48 mm a track needs, and the other's exposed pad fills its
body.

Every count in this table is a fixed two-pass reading. The convergence rule below was measured
after it, on a board where a variant reading 46 at two passes read 17 on its seventh further pass,
so the table shows that escapes did not help *at that budget*; run both arms under a declared
stopping rule before citing it as more.

The proportionality is suggestive, not diagnostic. If the authored topology were merely *wrong*,
you would expect the cost to concentrate on the pads that were sent the wrong way; instead the
two sets cost about the same per stub, which is at least consistent with the constraint rather
than the topology being what hurts — a sequential router at the edge of completion has fewer
choices after every piece of copper it did not choose, and cannot trade your escape away when it
needs that channel. Separating that from a package-specific effect needs a third treatment level
and per-net attribution, neither of which was run.

So:

- **On a board with routing margin, author escapes.** The stage decides layer assignment and
  crossing order, which is real design work no router does for you.
- **On a board that is barely routable, do not lock escapes before area routing.** Decide the
  escape *plan*, verify it is feasible, and then let the router realise it — or route the whole
  board by hand. Locking a plan into copper spends the router's remaining freedom on your
  hypothesis.

  This is the one case where the escape *design* is yours and the escape *copper* is not, so say
  which explicitly. Fanout stays generator-owned by default
  ([`AUTOROUTING.md`](AUTOROUTING.md)); relaxing that for one board is a project decision recorded
  with the layer plan, not a silent handover. Record the plan (which pad leaves on which layer, in
  what order), leave the copper unauthored, and grade the router's realisation against that plan
  afterwards rather than treating whatever it emitted as the design. Note that Freerouting's own
  fanout must still be disabled for track-width correctness, so "let the router realise it" means
  the area router realising it in passing, not a fanout pass.
- **Measure it rather than assuming either way**, with a control that changes one variable. The
  run that produced the table above initially changed two — it also had to drop
  `--rip-existing-nets '*'`, because the backend deletes locked geometry under that flag — and the
  control showed the flag was worth 1 and the escapes 6.
- **"Locked" is not a preservation contract.** This KRT build has two measured paths that
  alter locked geometry: the rip path above, which deletes it, and the stub layer-swap pass,
  which moves it. A second session handing KRT the same kind of locked F.Cu escapes, with
  `--keep-input-copper` and without the rip flag, found seven of them on B.Cu afterwards — the
  exact geometries survived and still carried the locked flag, on the wrong layer, because the
  swap mutates a segment's layer without consulting `locked`. `--no-stub-layer-swap
  --no-smoothing` (both in `py_router/route.py`, alongside `--can-swap-to-top-layer`,
  `--swappable-nets` and `--mps-layer-swap`) held them in place on every later stage. So: pass
  `--keep-input-copper --no-stub-layer-swap`, decide smoothing explicitly, and verify every
  authored item's geometry *and layer* after the run rather than trusting the flag — the
  subsection below says how.

## Author the skeleton by hand, in priority order

[`AUTOROUTING.md`](AUTOROUTING.md) already requires scouting first and authoring the critical
skeleton by hand. The ordering rule is the reason it works: sequential routers are order-dependent
by construction — *"Use of ripup-and-reroute to resolve DRC can rely heavily on net ordering"*
(TritonRoute-WXL, IEEE TCAD 41(4), 2022) — so whichever nets you route first are the ones that get the
good channels.

Route in this order, and stop to re-place rather than to widen the search:

1. Returns and reference geometry: plane boundaries, stitching, the ground the critical nets will
   reference.
2. High-current and thermal copper, at its declared width ([`PCB.md`](PCB.md)'s DC copper-capacity
   gate applies, and never hand a power net to a router at the default signal width).
3. Constrained pairs and sensitive analogue: Kelvin/sense pairs, differential pairs, guarded
   nodes, anything with a length, skew, or loop-area budget.
4. Buses and repeated structures, as bundles that keep their order across the board.
5. Everything else — this, and only this, is the router's scope.

In KiCad, do this in the interactive router. It has three modes — Highlight Collisions (fully
manual), Walk Around, and Shove — and Shove and Walk Around *"always create horizontal, vertical,
and 45-degree (H/V/45) track segments"* (KiCad 9 PCB Editor docs, "Routing Tracks"). Use Shove for
the skeleton; reserve Highlight Collisions with Free Angle for the rare geometry that needs it,
and never with Allow DRC Violations left on.

### The authored skeleton is a design artefact, and the board cannot certify it

Once the skeleton is authored, it is the one part of the board whose geometry carries a
requirement — a leakage budget, a via ban, a guard topology — rather than a routing outcome. Two
things follow, both measured on one board in one day
([`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md)
§3):

- **A change to the skeleton is a design change, whoever makes it and however it is made.** Of
  three candidate boards produced for the same brief, two had altered the locked critical copper.
  One agent edited the generator to open a 1 mm doorway through a guard wall so a blocked pin
  could escape, and reported all authored items intact — which was true against *its* generator.
  The other left the generator untouched and straightened a locked trunk of the guarded net
  directly on the board, so the generator
  and the board silently disagreed. Both boards routed better for it. Neither change was wrong to
  try; both are decisions for the person who owns the leakage budget, and both were reported as
  routing results. Surface a skeleton diff as its own finding, before the unconnected count.
- **Verify the skeleton against the canonical generator, never against whatever generator sits
  beside the board.** A verifier that imports the expected geometry from the generator in its own
  directory certifies the board against the copy of the generator that produced it, so a modified
  generator always passes its own board. Run the verifier from the
  upstream project checkout, or make it take the generator path as an explicit pinned input and
  record that path in the result. This is [`GUARDS.md`](GUARDS.md)'s source-of-truth rule: the
  expectation must come from the authority, not from the artefact's neighbourhood. Check the layer
  as well as the coordinates — see the layer-swap caveat above.

## On a repeated board, check what KiCad already carries before hand-rolling reuse

Everything above is about authoring *one* good channel. If the board has several equivalent ones,
do not also write the copier: KiCad 10 ships two reuse mechanisms, and the evidence behind this
section — what was verified by running it, what was only read — is in
[`plans/kicad10-layout-reuse.md`](plans/kicad10-layout-reuse.md).

- **Multichannel Repeat Layout** (Tools → Multi-Channel) copies placement, tracks, vias, zones
  and graphics from one placement rule area to the other rule areas **of the same board**, reassigning
  each copied track's net through a component match. Present since KiCad 9.
- **Board-layout design blocks** save a schematic fragment, a layout fragment, or both, into a
  `*.kicad_blocks` library, and carry them **between boards and projects**. New in KiCad 10; the
  KiCad 9 PCB Editor manual has no design-block chapter.

Two things follow for a generator-based workflow.

**A design block library is script-authorable, and that is the part worth using.** The library is a
directory, `<lib>.kicad_blocks/<block>.kicad_block/`, holding an ordinary `<block>.kicad_pcb`, an
optional `<block>.kicad_sch`, and a `<block>.json` of description/keywords/fields — no special
format, written by the same s-expression board writer as any board. Verified once, KiCad 10.0.5 on
macOS, 2026-09-07, not replicated: a library generated entirely from the bundled `pcbnew` Python
module, with no GUI involved in authoring it, was enumerated by the Design Blocks panel and placed
into a board that had never held its items, carrying its routed segment, its net and its `locked`
flag, and creating a `(group … (lib_id "reuse:rc_snubber"))` link on the target board. Note the
table filename: **`design-block-lib-table`, with hyphens** — a project file named
`design_block_lib_table` was ignored in silence, no error and an empty panel.

**Every consumer of it is GUI-only, so it does not close the loop by itself.** Verified by
enumeration on 10.0.5: `kicad-cli` has no design-block or multichannel subcommand; the IPC API at
tag `10.0.5` defines no message for either; the SWIG `pcbnew` module exposes only `EDA_GROUP`'s
design-block *link* accessors. The tool actions exist and the API's `RunAction` can name them, but
that proto's own comment is *"the TOOL_ACTIONs are specifically **not** an API … provided for
low-level prototyping purposes only"*, and `repeatLayout` opens a modal dialog. So a generator can
emit the reuse unit; applying it must currently go through an interactive GUI path. That is a
statement about the interface, not about the operator — GUI automation is possible, and nothing
here establishes that a person has to be the one clicking.

Neither mechanism relaxes anything else in this file, and two rules above apply to their output
with more force than usual:

- **A replicated channel is a skeleton edit, so the skeleton rules apply to it.** Before copying,
  Repeat Layout removes *eligible* existing routing in the target area — inside the rule area, on
  layers enabled in that rule area, narrowed further by *Restrict to routing connected within the
  area*, and skipping locked target items unless *Include locked items* is set, which then updates
  them too. Read that as "routing can survive a purported clean copy" in both directions: some
  target copper is deleted, and some is not. A block placed from a library is a copy of geometry
  with no link back to the authority that justified it — the same hazard as verifying a board
  against the generator sitting beside it.
- **Do not read a completion message as verification.** Documented-not-verified, from the 10.0.5
  manual and source: a target whose topology does not match is skipped silently and the only
  completion signal is an info bar reading `Copied to N Rule Areas.`; routing that leaves the
  reference area, or sits on a layer not enabled in *both* rule areas, is *"silently omitted"*; and
  when the connectivity isomorphism fails, the tool falls back to matching by schematic-symbol
  instance UUID — which checks footprint identity and pad count but **no connectivity** — and
  still reports the target as "OK". Verify each replicated channel's authored copper by geometry *and*
  layer, as after any router pass.

## A router's defaults are tuned for completion, not for legibility

Never run a backend on its shipped weights and then read the result as a design opinion. Measured
defaults, KiCadRoutingTools v0.21.3 (`py_router/routing_defaults.py`, commit `749cfa83`):

| constant | default | what it prices |
|---|---|---|
| `VIA_COST` | 75 | a via, in grid steps (× 1000 cost units) |
| `TURN_COST` | 1000 | a **90°** direction change = one grid step; KRT scales it by angle, so a 45° corner is 500 and a 180° reversal 2000 |
| `DIRECTION_PREFERENCE_COST` | 250 | one off-axis move = **a quarter of a grid step** |
| `HEURISTIC_WEIGHT` | 2.3 | A* greediness |

Read the arithmetic before you read the board. A 90° corner costs **1/75 of a via**; a 45°
corner — what an H/V/45 route is mostly made of — costs **1/150**. Off-axis travel breaks even
against a via only after 300 off-axis moves: 30 mm of axis displacement at a 0.1 mm grid, and
42 mm of copper if those moves are diagonal. So corners are nearly free, and a lateral detour must
be long before it costs what a layer change costs. On a board whose short axis is 20 mm the detour
can never get that long, so across that axis the weights genuinely do favour a via; along the long
axis they do not. Fragmentation is priced in everywhere; the via population is priced in only
where the geometry is narrow.

The heuristic weight is the one that most often surprises. With an admissible heuristic and
`w > 1`, weighted A* guarantees only that *"the solution cost cannot be greater than the optimal
cost by more than a factor of w"* (Hansen & Zhou, *Anytime Heuristic Search*, JAIR 28 (2007), §1
p. 269). KRT's heuristic is octile distance plus at most one via, multiplied once by the weight
(`rust_router/src/router.rs`; `docs/routing-architecture.md`'s description of a 3D Chebyshev
heuristic with a per-layer via term is stale). Because it never over-estimates on that move set,
**the inference — mine, not the source's — is that at `w = 2.3` each net's route may legitimately
cost up to 2.3× the optimum of the router's own cost function**, in which one via is 75 grid
steps. Treat that as a bound worth checking on a real board, not as a measured property. A path
well off optimal is what "no sense to how it is routed" looks like from outside, and it is the
configuration, not a defect.

So, before any promotable run:

- **Set `via_cost`, the direction costs, and the heuristic weight explicitly, and record them in
  the routing provenance.** A default you did not choose is a design decision you did not make.
  [`AUTOROUTING.md`](AUTOROUTING.md)'s pinning rule covers the binary and the configuration; these
  four numbers are part of the configuration. **Note where they can live:** the skill's
  `autoroute.json` schema is Freerouting-only and rejects unknown root keys
  ([`scripts/kicad_autoroute.py`](scripts/README.md)), so it has nowhere to put KRT weights today.
  Until that schema grows a backend section, record KRT weights in the project's own routing
  provenance and pass them on the command line — do not write them into `autoroute.json` and
  expect it to validate.
- **Trade the heuristic weight down when legibility matters more than runtime.** Lowering `w`
  towards 1 tightens the suboptimality bound at the cost of search time; that is the whole trade,
  and it is yours to make per board.
- **Turn the postroute/optimisation pass on where the backend has one**, and say so. Freerouting's
  postroute pass exists specifically to reduce via count and cumulative trace length, and is off
  unless requested.
- **Do not tune weights on a screen whose oracle is disabled.** KRT's own history is the
  cautionary record: `DIRECTION_PREFERENCE_COST` was taken 250 → 5 on a corpus screen that ran
  with no KiCad in the image, so every DRC-oracle leg was a silent no-op; the re-screen with KiCad
  live reversed the decision (commit `aaa60331`). This is [`GUARDS.md`](GUARDS.md)'s
  unevaluable-input rule wearing a router's clothes: a screen that could not observe the outcome
  did not measure it.

## A sequence of per-net passes is not a routing pass

A full router is three phases: main routing, **global rip-up and reroute**, and post-processing
(He 2024 §3.5.1). The middle phase is what repairs the arbitrary net order, and the third is what
recovers the vias the first phase spent.

Driving a backend as a series of *bounded per-net passes*, each committed before the next starts,
deletes both. What remains is a set of independent greedy searches welded together, each optimal
only for itself, with the order dependence baked in permanently instead of relaxed. Repair scripts
run afterwards cannot recover it: they operate on committed topology, so they are working the
constrained problem.

If a board's routing provenance reads as a list of per-net or per-cluster passes plus repairs,
that is not an implementation detail to record — it is the finding. Re-run the whole scope in one
pass with the rip-up loop enabled, or route it by hand; do not accumulate.

## Finish with endpoint-led local closure

A global router reaching its declared stopping rule does not imply that its lowest-count candidate
is the easiest one to finish. This is a late-stage repair over a preserved global-route checkpoint,
not a substitute for global rip-up and reroute:

1. Choose the branch from exact open endpoint identities and local escape/stitch evidence, not the
   aggregate alone; keep the declared ranking comparator visible.
2. Close one signal at a time. A scripted path must collide effective copper against pads, tracks,
   arcs, vias, holes, **filled polygons**, edges and rule areas. Disconnected same-net copper is a
   target or obstacle until the proposed path actually joins it.
3. Withdraw only a named local blocker when needed. After each closure, refill and compare gained
   and lost endpoints, then rerun DRC, the independent collision audit and protected-geometry check.
4. Only after signal opens are zero, stitch pour components at legal overlapping fill. Verify the
   intended component merge after refill and require a calibrated minimum annular contact; KiCad
   can accept a DRC-clean sliver. Remove dangling/duplicate residue and rerun the complete board
   gate before promotion.

The successful measured instance and its limits are recorded in
[`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md)
§12. It supports this closure workflow; it does not establish that one placement, router or path
planner is generally superior.

## Grade the shape of the route, not only its DRC

DRC and [`scripts/kicad_copper_collisions.py`](scripts/README.md) answer "is this legal" and "is
anything shorted". Neither can see that a board is routed badly. Add the shape audit:

```sh
# report-only: prints the metrics, grades nothing, exits 0 with an explicit NOT-GRADED verdict
scripts/kicad_route_shape.py BOARD.kicad_pcb --report-only

# gating: at least one threshold is mandatory; no thresholds and no --report-only is UNEVALUABLE
scripts/kicad_route_shape.py BOARD.kicad_pcb \
    --layer-direction F.Cu=v,In1.Cu=h,B.Cu=v \
    --max-vias-on-any-net 6 \
    --max-full-stack-via-fraction 0.5 \
    --max-short-segment-fraction 0.15 \
    --min-direction-conformance 0.70
```

**The sibling project governs the router, not only DRC.** The same trap has a routing-side
half that is worse, because it produces a plausible board instead of a bad report: a router
invoked on a board with no same-stem `.kicad_pro` resolves clearances from the *stock* netclass.
KiCadRoutingTools says so in its own banner — "CLI and GUI runs will route DIFFERENT copper from
this same board" — and it is right. Measured on one board: the identical recipe on a
byte-identical seed produced **30 unconnected items routed without the project and 39 with it**
(the stock netclass gave hole-to-hole 0.2 mm against the board's 0.25 mm, and no edge constraint).
Every earlier number in this file's own experiment log was collected the first way, so the
comparisons between them survived — all were equally wrong — and none of the absolute values did.
**Copy the project and the DRU beside the board before routing it, not only before grading it.**

**Three ways a DRC result lies about a scratch board, all measured on one session:**

1. **No same-stem `.kicad_pro` beside the board** — `kicad-cli` silently grades against KiCad's
   built-in defaults (0.20 mm track, 0.30 mm hole, 0.20 mm clearance) instead of the project
   netclass. One board read 322 clearance + 199 each of `drill_out_of_range`, `track_width` and
   `via_diameter` without it, and 10 clearance with it.
2. **`pcbnew.BOARD.Save()` writes the sibling `.kicad_pro` too**, and if the board was *loaded*
   without one, what it writes is KiCad's defaults — netclass 0.18/0.45 became 0.20/0.60. This is
   the worse of the two, because the file still exists and looks right; only the numbers changed.
   The dependency is on the load, not the save: in KiCad 10.0.5 `Save()` delegates to
   `pcbnew.SaveBoard(path, board, aSkipSettings=False)`, which writes the project attached to the
   board by `LoadBoard`. Copy the project **before** loading, so the project that gets written back
   is yours; `pcbnew.SaveBoard(path, board, True)` suppresses the project write outright. Re-copying
   after every save also works, but it treats a lifecycle dependency as a ritual and will not save
   you when something else reads the project between the save and the re-copy.
3. **Stale zone fill** — a zone filled before you added copper still carries its old polygons, so
   every new track reads as a clearance violation against the pour. Sixty-eight escape stubs
   produced exactly sixty-eight phantom violations this way. Refill (`pcbnew.ZONE_FILLER`) after
   adding copper and before DRC. This is distinct from the router saving zones *unfilled*
   ([`AUTOROUTING.md`](AUTOROUTING.md)): here the fill exists and is wrong.

**Measure connectivity, not violation counts, when comparing routes.** On the board this file
was written against, three runs of one identical recipe on one identical seed gave the identical
unconnected count (30, 30, 30) but clearance-violation counts of 4, 18 and 4. The scalar
unconnected count repeated; the violation tally did not.

A later pair of runs settled what those three could not. **Compare the copper, not the file.**
Hashing the saved `.kicad_pcb` says the two runs differ — UUIDs and timestamps differ on every
save. Hashing the sorted track and via *geometry* (start, end, width, layer, net) showed two runs
of one recipe producing **identical copper**: 3449 items, same hash, on the baseline placement,
and 3641 items, same hash, on a rotated one. So on this board, backend and recipe the router is
deterministic in fact, and the earlier "three identical counts" was weaker evidence for a claim
that happens to be true. Do the geometry hash when determinism matters; a repeated scalar is
consistent with different nets failing each time.

**Reproduce the baseline in your own harness before quoting a delta.** The project-file trap
above has a comparison-side consequence that caught a careful session: it *regraded* the inherited
best board (30 unconnected, routed without a project) under the correct rules and adopted 30 as
its baseline, then ran all of its own experiments with the project present. Its 22 was therefore
reported as 30 → 22 when the same recipe with the project present starts at 39. Regrading a saved
board re-measures its copper; it cannot re-route it, so a baseline you did not route in your own
harness is not the same experiment as your candidates. Route it again, under the same rules and
the same runner, and compare copper produced the same way
([`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md)
§4).

**A router's own incomplete count is not an unconnected count, on any backend.**
[`AUTOROUTING.md`](AUTOROUTING.md) says this of KRT's `JSON_SUMMARY`; it is equally true of
Freerouting's per-pass `N incompletes across M items` lines. One scout on a bare placement ran
178 incompletes in its first pass and oscillated between 16 and 19 over passes 17–20 — a live
search count — and when its SES was imported and the board refilled and graded by `kicad-cli`, the same run
read **42 unconnected pads**. Sixteen and forty-two are the same board. Quote the progress line
as what it is, and never beside a KiCad-graded number in the same table.

**A fixed pass budget is not convergence, and a two-pass A/B is a scout until that budget has been
shown to stabilise the metric on the board in hand.** A router that can take
its previous output as input defines a search that can simply be run again — KRT's
`--keep-input-copper` re-routes the nets its *own* connectivity model still reports open and leaves
the rest — and a recipe that stops after a fixed number of such passes reports wherever that budget
happened to land. Measured on one board, one recipe, one seed ([`reviews/2026-09-05-cross-session-routing-evidence.md`](reviews/2026-09-05-cross-session-routing-evidence.md) §8): the two-stage recipe
read 24 unconnected and the next two further passes read 19 and 16; a variant that read 46 after
the recipe read 17 on its seventh further pass; four variants spanning 22 to 46 after the recipe had
best values of 16, 17, 17 and 17 over six to twelve further passes; and the control's own
sequence wandered 16 → 22 → 17. So the sequence is not monotone, and these are best-of readings
over a bounded number of passes, not a converged plateau. The fixed-budget readings ranked one
variant as catastrophic and another as a small win; the best-of readings separated none of them
from the untouched seed. So:

1. Hold seed, tool versions, rules, refill path and per-pass command constant within every arm.
   With KRT, pass `--keep-input-copper --no-stub-layer-swap` on every further pass — the flag
   alone only protects the input copper from this run's cleanup prunes, and stub layer swapping
   stays on by default (the locked-copper caveat above) — and verify the authored copper's
   geometry *and layer* after each pass.
2. Declare the pass budget and stopping rule before running the arms — a maximum, or N passes
   without a new best — and apply it to every arm, the control included.
3. Record the whole sequence and keep the **best** valid board, not the last.
4. Say exactly what one trajectory per arm supports. Where the router is deterministic (the
   geometry-hash paragraph above), "A's best was X and B's best was Y on this seed under this
   stopping rule" is a valid scoped statement. It is not a generalisation, and an arm whose best
   lies inside the control's own range of pass values has not shown a difference — a serial
   sequence is timepoints, not a noise distribution. A claim that one layout *generally* routes
   better, or that two are equivalent, needs replication declared in advance: more seeds, or
   repeated runs where the router is not deterministic.

This downgrades every A/B in this file whose evidence is an unconnected count from a fixed one- or
two-pass recipe — the escape table, the rotation-in-place result, the row-alignment result and the
baseline deltas. Each was measured honestly and none has been re-run under a declared stopping
rule; read them as "did
not help at that budget" until someone does. Repeated *grading* of one saved board is a different
question — that is deterministic (the geometry-hash paragraph above); the router's sequence is not.

**Rank on signal opens; the gate is still the total.** KiCad's `unconnected_items` mixes records
of different kinds, but its JSON contains the net only inside each item's human-readable
description. A zone-only classifier is wrong: measured reports also contain zone-track, zone-via
and track-via records on a pour-managed net. Conversely, not every net with a familiar power name
is necessarily pour-managed. Make the project declare the exact pure-pour nets and classify every
record on those nets as pour topology; leave a zone on an undeclared net, a missing net, or
mismatched item nets ambiguous. If one net mixes routed-return obligations with refill-owned plane
work, declare it with `--mixed-pour-net`; every record on it remains ambiguous because DRC text
alone cannot assign the obligation safely.

Use the shipped parser on a fresh JSON DRC report:

```sh
python3 scripts/kicad_drc_connectivity.py drc.json \
    --pour-net /GND --json connectivity.json
```

The report must come from a full-severity run whose authoritative configuration does not ignore
`unconnected_items`. The command requires `--pour-net`, `--mixed-pour-net`, or the explicit
`--no-pour-nets` declaration when the board truly has none. It handles KiCad's `mm`, `in` and `mils`
JSON reports and pad, qualified PTH/SMD/NPTH pad, track, via and zone descriptions; any ambiguity
exits nonzero. `--require-zero-total` gates the original aggregate criterion. KiCad 10.0.5 was
measured to cap a violation type at exactly 199 records. The parser conservatively refuses that
exact count on every version, which can reject a legitimate 199; qualify the installed release's
cap behaviour before changing that refusal or ranking the value. Do not isolate connectivity by
deleting foreign nets, because doing so can change zone fill and topology.
On two candidates of one measured design, signal counts stayed fixed under two refill paths while
the pour-topology count moved by ten on one candidate (§9 of the evidence file); that observation
is not a proof of refill invariance on arbitrary designs. Rank routing strategies on the evaluable
signal component and report signal, pour, ambiguous and total beside each other. The split changes
the ranking, not the definition of done:
islands are unfinished copper until stitched or removed by zone topology, and a criterion written
as "zero unconnected items" is met only when the total is zero, unless its owner amends it in
words. A session that reached zero signal opens and called the task complete "under the corrected
metric" was wrong to; write "signal routing complete, board incomplete" and carry the original
criterion into the final report as its own line
([`reviews/2026-09-05-ranking-metric-is-not-completion.md`](reviews/2026-09-05-ranking-metric-is-not-completion.md)).

It reports, per board and per net: vias per routed net, the via layer-span histogram, the segment
length distribution, per-layer copper length, and — only when `--layer-direction` is supplied —
the fraction of copper length running along each layer's declared preferred direction. Every
metric that cannot be computed from the inputs given is reported `unevaluable` and, in a gating
run, fails; none of them silently becomes a pass. See [`GUARDS.md`](GUARDS.md) for why that is
correctness rather than friction.

**The thresholds above are review triggers, not validated limits.** They have not been calibrated
against a corpus, and this file does not pretend otherwise: use them to open a conversation about
a board, set the project's own numbers from its own history, and record them where the project
records its other budgets. The signatures below are **hypotheses to test on the board in front of
you**, not conclusions the numbers establish — each names the cheapest thing that would confirm
or kill it:

| signature | hypothesis | what settles it |
|---|---|---|
| high full-stack fraction on a board with 3+ layers | layer transitions were not planned | check whether the design deliberately restricts itself to through-vias — that is also 100 % full-stack, and legitimately so. On a 2-layer board the metric is meaningless and the tool refuses to grade it |
| a high per-net via maximum on a short net | escape was never authored for that net's cluster | look at the cluster: are the vias at the pins (escape) or in open copper (area routing)? |
| a large fraction of very short segments | a per-cell search path emitted verbatim | merge collinear segments and re-measure. If the count collapses without copper moving, it is file fragmentation, not wandering — a cosmetic finding, not a routing one |
| low H/V direction conformance on a layer | that layer has no lane structure | remember the metric scores only H and V: a coherent **45° lane** scores zero and is not disorder. Look at the layer before believing the number |
| the same cluster failing every attempt | placement, per [`PCB.md`](PCB.md) | re-place, do not widen the search — but confirm the cluster is actually capacity-bound first, by measuring free lanes per y rather than assuming density implies congestion |

The measured baseline that motivated this file, so the numbers above are not abstract: a 20 × 107 mm
4-layer board, 61 routed nets, **197 vias all spanning F.Cu→B.Cu**, 3.23 vias per routed net,
1793 segments for 2126 mm of copper, median segment 0.500 mm with 32 % below 0.2 mm, and — with a
±5° axis tolerance — **three of four layers running the same way**: F.Cu 20 % h / 38 % v,
B.Cu 19 % / 45 %, In1.Cu 10 % / 57 %, In2.Cu 30 % / 14 %. In2.Cu is the one predominantly
horizontal layer, so an orthogonal partner does exist on paper; it carries about 6 % of the
copper, which is why the board reads as running lengthwise throughout. Full derivation
in [`reviews/2026-09-05-routing-methodology-research.md`](reviews/2026-09-05-routing-methodology-research.md) §1.

## Lane capacity is not routability — measured three times, and the third one agreed

The tempting move on a congested board is to count lanes: how many tracks can cross this row,
how many nets must. It is easy to compute, it feels like the channel-routing theory it borrows
from, and on one measured board it **predicted the wrong answer twice**.

A 20 × 107 mm 2-layer board, 113 footprints, 61 nets, routed by the same backend and command
each time; connectivity measured off the board by DRC and verified deterministic (three repeats
of one recipe gave the identical unconnected count):

| placement change | lane effect | unconnected items |
|---|---|---:|
| none (control) | — | 39 |
| 12 AFE passives spread out of the dense row | more lanes, ratsnest 30 mm *shorter* | **68** |
| the 2 parts a sensitivity analysis named as gating | AFE row −6 → **+7 lanes** (16 → 29) | **63** |

Both "improvements" made the board route worse, and the second was the *targeted* one — a
per-part sensitivity analysis that named two resistors (+9 and +4 lanes) out of 23 candidates, moved
only those two, and doubled the row's lane count. Connectivity got worse anyway.

So, on the evidence of these two interventions on this one board and backend: **a lane gain is
not by itself a routing improvement.** Both times the capacity metric improved and connectivity
got worse. That refutes "more lanes, therefore better route" as an inference; it does not
establish that capacity is never predictive, and two treatments on one board could not. Treat an
aggregate capacity metric as a description of a board rather than a prediction about it, use it
to understand *where* a board is tight, and never report a lane gain as a routing improvement
without routing the board.

**A third intervention on the same board then improved both**, which is why the rule above is
about the inference and not about capacity itself. Rotating 19 flat B.Cu passives 90 degrees in
place — no part moved to a new location, mean displacement 0.68 mm — took B.Cu persistent lanes
from 2 to 12 through the tight band and unconnected items from **39 to 25** at the recipe's two-pass
budget, the only intervention here that improved connectivity at that budget, and repeatable to
identical copper. It has not been re-run beyond that budget (convergence rule above); the full
record, with the excluded parts and the seed checks, is evidence record §11.

The difference worth carrying is *what the intervention did to the pads*:

| intervention | what moved | lanes | connectivity |
|---|---|---|---:|
| spread 12 passives out of the dense row | parts, to new locations | up | 39 → 68 |
| move the 2 parts a sensitivity analysis named | parts, to new locations | up | 39 → 63 |
| rotate 19 passives in place | footprint aspect; pads stay put | up | **39 → 25** |

A flat 0603 land is 3.05 x 1.55; rotated it is 1.55 x 3.05. On a long narrow board whose useful
lanes run lengthwise, that hands back 1.5 mm of the scarce axis per part without relocating
anything. That is a placement change that does not disturb escape geometry, and it is a different
lever from moving a part. **The hypothesis this supports — that the two failures cost more in
pad-local geometry than they bought in corridor width — is consistent with all three results and
is not established by them**; no experiment here varied corridor capacity while holding pad
positions fixed except this one, and one confirming case is not a separation.

Rotation is not free, and two of its costs are invisible to a capacity metric:

- **A pad that terminates authored copper cannot be rotated.** Turning the part moves the pad out
  from under the track endpoint. Nothing overlaps, so no clearance or courtyard check fires; the
  net simply comes apart. Rotating all 24 candidates silently disconnected the guard net carrying
  the front end's leakage budget, and only a diff of unconnected counts against
  the unrotated seed caught it. Exclude any candidate whose pad contains a track endpoint.
- **Courtyards are not a copper model.** They are not even self-consistent: two comparable 0603
  lands on this board declare 1.91 x 1.01 and 3.05 x 1.55. Rotating against courtyards alone put
  four shorts across four nets, two of them guard-critical. Union the courtyard with
  the pad copper plus clearance, and include existing tracks and vias as obstacles — with real
  segment geometry, since a 45-degree track's bounding box claims its whole diagonal envelope and
  will exclude parts that in fact have room.

And rotation changes the part's own electrical geometry: which pad faces the pin it decouples,
and therefore the decoupling loop. That is a separate question from routability and this
experiment did not ask it.

Two things it is still good for, both diagnostic rather than predictive: finding a genuine hard
wall (on that board, a thermal neck where the only corridor on either layer was 4.4 mm wide —
which turned out to be *comfortable*, 26 lanes against 9 crossing nets, and so was correctly
ruled out as the constraint), and naming which specific parts gate a corridor, which is a far
better question to bring to a floorplan review than "which region looks crowded".

The likeliest mechanism — untested, because no experiment here separated pad movement from the
other effects of moving a part — is that moving a part to free a corridor **moves its pads**, and
therefore its own escape geometry and its neighbours'. On that reading the lanes are gained where
no net wanted to go and paid for at the pins, which would be an argument for doing the escape
stage first and the corridor arithmetic second. Testing it needs a treatment that changes corridor
capacity without moving pads, or per-net attribution of where the new failures landed.

## Do not generalise the anti-autorouter folklore

"Autorouters use too many vias" is not supported by the best measurement available, and repeating
it will make you tune the wrong thing. On 317 real boards routed successfully by three routers,
He 2024 (Chapter 4) found FreeRouting had the highest success rate and the **lowest via count** of
the three, averaging 2.6 vias against 8.1 for the boards' original solutions. Note what that
comparison does *not* establish: the dissertation does not state how those original solutions were
produced, so they cannot be called hand-routed.

The defensible statement is narrower and more useful: **a backend driven without a layer plan,
without an escape stage, on shipped cost weights, as a sequence of per-net passes, produces
incoherent copper.** Every clause there is something you control. Fix the clauses; do not abandon
the tool.
