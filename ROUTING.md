# Routing craft: topology first, layers second, copper last

This file is about *how to route*, and about how to drive a router so its output is a design
rather than a search log. [`AUTOROUTING.md`](AUTOROUTING.md) governs the external-router
pipeline — scope, pinning, promotion, manifests. [`PCB.md`](PCB.md) governs the floorplan gate
and the completion gates. This file sits between them: the method that makes the copper make
sense, and the executable check that says whether it does.

The evidence behind every numeric claim, with locators and an access log, is
[`reviews/2026-09-05-routing-methodology-research.md`](reviews/2026-09-05-routing-methodology-research.md).
Read it before changing a rule here.

## Contents

- [Route the topology, not the copper](#route-the-topology-not-the-copper)
- [Placement owns the route, and the ratsnest is the evidence](#placement-owns-the-route-and-the-ratsnest-is-the-evidence)
- [Commit a layer plan before the first track](#commit-a-layer-plan-before-the-first-track)
- [Escape is a stage, and it is where vias are decided](#escape-is-a-stage-and-it-is-where-vias-are-decided)
- [Author the skeleton by hand, in priority order](#author-the-skeleton-by-hand-in-priority-order)
- [A router's defaults are tuned for completion, not for legibility](#a-routers-defaults-are-tuned-for-completion-not-for-legibility)
- [A sequence of per-net passes is not a routing pass](#a-sequence-of-per-net-passes-is-not-a-routing-pass)
- [Grade the shape of the route, not only its DRC](#grade-the-shape-of-the-route-not-only-its-drc)
- [Lane capacity is not routability — measured, twice](#lane-capacity-is-not-routability--measured-twice)
- [Do not generalise the anti-autorouter folklore](#do-not-generalise-the-anti-autorouter-folklore)

## Route the topology, not the copper

The most common PCB routing objective is a weighted sum of total wirelength and via count —
"the most common objective function" in "many PCB routing problems", in He 2024's words (§3.4
p. 48); the same objective appears explicitly as `g(x) = C_wl + γ·N_via` in its post-processing
A* (Eq. 3.3, p. 59). That is a prevalent formulation, not a universal one. What matters here is
that it contains no term for "makes sense to a human", and that it is evaluated **per net, in an
arbitrary order** — He 2024's own main loop reads *"Randomly select a net"* (Fig. 3.2, p. 48),
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

Measured 2026-09-05 on the 20 × 107 mm 2-layer board this file was written against, escapes
authored exactly as described above — uniform, radial, one length per package, every signal pad,
zero pads without a legal escape, `kicad_copper_collisions.py` clean, DRC clean of clearance and
edge findings — then locked and handed to the backend. Only the escapes differ between rows:

| escapes | stubs | unconnected items |
|---|---:|---:|
| none | 0 | **31** |
| U7 + U8 only, where package geometry forbids anything else | 21 | 33 |
| U7 + U8 + U6 | 68 | 37 |

**Every stub cost connectivity, and the cost was linear in stub count** — 0.095 and 0.088
unconnected items per stub. There was no subset that helped, *including the two packages where
radial escape is the only physically possible option*: U8's 0.32 mm pads on 0.5 mm pitch leave an
0.18 mm gap against the 0.48 mm a track needs, and U7's exposed pad fills its body.

Linearity is the tell. If the authored topology were merely *wrong*, the cost would concentrate on
the pads that were sent the wrong way. Instead each locked stub cost about the same, which is what
you see when the constraint, not the topology, is what hurts: a sequential router at the edge of
completion has fewer choices after every piece of copper it did not choose, and it cannot trade
your escape away when it needs that channel.

So:

- **On a board with routing margin, author escapes.** The stage decides layer assignment and
  crossing order, which is real design work no router does for you.
- **On a board that is barely routable, do not lock escapes before area routing.** Decide the
  escape *plan*, verify it is feasible, and then let the router realise it — or route the whole
  board by hand. Locking a plan into copper spends the router's remaining freedom on your
  hypothesis.
- **Measure it rather than assuming either way**, with a control that changes one variable. The
  run that produced the table above initially changed two — it also had to drop
  `--rip-existing-nets '*'`, because the backend deletes locked geometry under that flag — and the
  control showed the flag was worth 1 and the escapes 6.

## Author the skeleton by hand, in priority order

[`AUTOROUTING.md`](AUTOROUTING.md) already requires scouting first and authoring the critical
skeleton by hand. The ordering rule is the reason it works: sequential routers are order-dependent
by construction — *"Use of ripup-and-reroute to resolve DRC can rely heavily on net ordering"*
(TritonRoute-WXL, IEEE TCAD 2021) — so whichever nets you route first are the ones that get the
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

**Three ways a DRC result lies about a scratch board, all measured on one session:**

1. **No same-stem `.kicad_pro` beside the board** — `kicad-cli` silently grades against KiCad's
   built-in defaults (0.20 mm track, 0.30 mm hole, 0.20 mm clearance) instead of the project
   netclass. One board read 322 clearance + 199 each of `drill_out_of_range`, `track_width` and
   `via_diameter` without it, and 10 clearance with it.
2. **`pcbnew.BOARD.Save()` overwrites that `.kicad_pro`** with a default project — netclass
   0.18/0.45 became 0.20/0.60. This is the worse of the two, because the file still exists and
   looks right; only the numbers changed. **Re-copy the project and DRU after every scripted
   save**, or save through a helper that preserves them.
3. **Stale zone fill** — a zone filled before you added copper still carries its old polygons, so
   every new track reads as a clearance violation against the pour. Sixty-eight escape stubs
   produced exactly sixty-eight phantom violations this way. Refill (`pcbnew.ZONE_FILLER`) after
   adding copper and before DRC. This is distinct from the router saving zones *unfilled*
   ([`AUTOROUTING.md`](AUTOROUTING.md)): here the fill exists and is wrong.

**Measure connectivity, not violation counts, when comparing routes.** On the board this file
was written against, three runs of one identical recipe on one identical seed gave the identical
unconnected count (30, 30, 30) but clearance-violation counts of 4, 18 and 4. Connectivity was
deterministic; the violation tally was not. Comparing two routing strategies on a noisy metric
invents differences that are not there.

**Equal pass count is not equal convergence.** A router that accepts its previous copper as input
defines an iterative search trajectory, and its objective can improve, regress, then recover. Treat
one- or two-pass readings as scouts, not A/B verdicts. Before claiming that a placement, authored
copper topology, or cost setting wins:

1. Hold seed, router/tool versions, rules, refill path, and per-pass command constant within every
   arm.
2. Choose the same pass budget and stopping rule before running the arms (for example, stop after a
   fixed maximum or after a stated number of passes without a new best).
3. Record the complete objective sequence and retain the best valid artifact, not merely the last.
4. If the arms' best results overlap the control's own within-trajectory range, report no measured
   separation. Do not turn a transient early reversal into a layout conclusion.

This is separate from repeated grading of one saved board: its connectivity may be deterministic
while the router's evolving sequence is non-monotone. A comparison is not mature merely because
both arms ran the same small number of passes.

**Keep comparison metrics and the completion gate separate.** A DRC aggregate may itself combine
different mechanisms. When evidence splits it, retain and report both the component counts and the
original total. Use only the stable, routing-owned component to compare routing strategies, but do
not substitute that comparison score for [`PCB.md`](PCB.md)'s Completed-PCB predicate. In
particular, refill-dependent unconnected records between same-net zone components are not signal
routing results, yet they remain unfinished electrical CAD until stitched, eliminated by valid zone
topology, or explicitly removed from the project acceptance criteria by its owner. A statement that
changes how results are ranked is not such a waiver.

Before declaring completion, copy the task's original definition of done into a fail-closed ledger
and evaluate every clause from the final artifacts. Later metric-semantic findings go in a separate
amendment/history section. Any false or unevaluable clause makes the task-level verdict
`INCOMPLETE`, even when a named routing sub-phase is complete. This prevents a technically correct
reclassification from silently weakening the requested outcome; the measured failure and required
report shape are recorded in
[`reviews/2026-09-05-ranking-metric-is-not-completion.md`](reviews/2026-09-05-ranking-metric-is-not-completion.md).

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
1793 segments for 2126 mm of copper, median segment 0.500 mm with 31 % below 0.2 mm, and — with a
±5° axis tolerance — **no layer having an orthogonal partner**: F.Cu 20 % h / 38 % v, B.Cu 19 % / 45 %,
In1.Cu 10 % / 57 %, In2.Cu 30 % / 14 %, i.e. every routing layer running lengthwise. Full derivation
in [`reviews/2026-09-05-routing-methodology-research.md`](reviews/2026-09-05-routing-methodology-research.md) §1.

## Lane capacity is not routability — measured, twice

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
per-part sensitivity analysis that named R40 (+9 lanes) and R17 (+4) out of 23 candidates, moved
only those two, and doubled the row's lane count. Connectivity got worse anyway.

So: **an aggregate capacity metric — lanes per cut, persistent corridors, free width — is a
description of a board, not a prediction about it.** Use it to understand *where* a board is
tight and to argue about a floorplan. Do not use it as an objective to optimise, and never
report a lane gain as a routing improvement without routing the board.

Two things it is still good for, both diagnostic rather than predictive: finding a genuine hard
wall (on that board, a thermal neck where the only corridor on either layer was 4.4 mm wide —
which turned out to be *comfortable*, 26 lanes against 9 crossing nets, and so was correctly
ruled out as the constraint), and naming which specific parts gate a corridor, which is a far
better question to bring to a floorplan review than "which region looks crowded".

The mechanism behind the failures is worth stating, because it is the same one twice: moving a
part to free a corridor **moves its pads**, and therefore its own escape geometry and its
neighbours'. The lanes are gained where no net wanted to go, and paid for at the pins. Which is
the argument for doing the escape stage first and the corridor arithmetic second.

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
