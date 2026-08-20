# KiCadRoutingTools: region-scoped design rules

Status: **proposed, not implemented.** This patches a third-party repository
(`github.com/drandyhaas/KiCadRoutingTools`, MIT), so it is an upstream
contribution plan, not skill work. Measured against `d4831b9` with the prebuilt
Rust core `grid_router v0.21.1` (macOS arm64).

## Summary

KRT's README lists "No design rules by region/area support" as a limitation.
The observed failure is three defects with different fixes:

1. **Clearance is consumed at stamp time and discarded**, so no region can carry
   a rule. This is the documented limitation.
2. **The clearance ledger is an untyped global monotonic minimum**, so one local
   copper exception silently becomes the whole board's default grading value.
3. **Project writeback conflates independent constraints**: copper clearance is
   used as the fallback for hole-to-copper clearance, and KiCad's absolute
   `rules.min_clearance` is treated as though it were the board-wide default.

Defects 2 and 3 form a separable fail-closed safety patch: distinguish local
from board-wide use, distinguish copper from hole clearance, preserve defaults,
and reject output that cannot be represented honestly. Land that before the
region feature. Defect 1 then requires explicit KiCad rule semantics, not an
obstacle-local approximation.

## Evidence

Observed on a 4-layer 50 A board (41 nets, 542 pads, 75 footprints).

Two runs, and the fabricable one is the slower:

| run | widths | result | time |
|---|---|---|---|
| uniform | 0.2 mm everywhere — **not fabricable**, power nets at signal width | 34/35 nets | 14.2 s |
| constrained | 0.25 signal / 0.4 gate / 2.0 power, from the shipped board | 33/33 single nets, 0 unconnected | **54.5 s** |

Both graded 0 DRC violations against the board's *original* rules — after
subtracting 24 `lib_footprint_issues` warnings proven by control to be the
scratch workspace failing to resolve the footprint library, not the routing.
Both routed the four gate nets Freerouting leaves unrouted; `GND_OUT` (and
`BR_N` in the constrained run) failed as multipoint nets, though the pours
complete them.

**The DRC-clean result does not survive the design's own audit.** `audit_pcb.py`
refuses the constrained board on its first check:

```
-- the isolation barrier (this audit, not DRC, enforces 4.0 mm)
AssertionError: 35 item(s) put copper inside the 129.0..133.0 barrier:
   GND_OUT on F.Cu spans x 128.150..129.150
   +5VH    on F.Cu spans x 132.963..133.225   ...
```

Host-side `+5VH` and isolated-side `GND_OUT` copper, 35 items, inside the 4 mm
galvanic isolation gap. KiCad DRC scores that board 0 violations because DRC
does not know the barrier exists. So KRT did not solve a harder problem than
Freerouting; it solved an easier one, by ignoring the constraint that defines
the board — and reported success. Freerouting's 16 unrouted gate connections are
the more *visible* failure mode — a reported gap is easier to reject than a
silent violation — but nothing here shows legality caused those failed searches,
and neither router received the constraint.

In the same run it wrote into the output project:

```
rules.min_hole_clearance:      0.25 -> 0.175 mm
net_class[Default].clearance:  0.2  -> 0.175 mm
severity[...]: 8 rules downgraded to warning/ignore
FAB FLOOR RELAXED -- the output project declares a smaller minimum than the
board originally did. Every checker grades against the NEW value.
```

The banner is honest and loud, and the relaxation still happened on a run whose
reconciliation pass reported `No valid nets to route!`. A user who trusts the
output project gets a board that reads clean against rules the router lowered.

### Where the rule goes

| site | fact |
|---|---|
| `py_router/routing_config.py:97` | `clearance: float = 0.1` — one scalar per run |
| `py_router/plane_obstacle_builder.py:1055` | `build_routing_obstacle_map()` dilates obstacles by `route_track_w/2 + clearance`, then `add_blocked_cells_batch` |
| `rust_router/src/obstacle_map.rs:196` | `GridObstacleMap` holds refcounts and bitmaps. **No clearance field exists** |

After stamping, the rule survives only as the *shape* of the blocked set. A cell
cannot be asked which rule blocked it, which is exactly the query a region rule
needs.

### What already exists

Region geometry reaches the Rust core today — `bga_exclusion_zones`
(`routing_config.py:115`), `package_proximity_zones` (`:130`, whose fifth float
is a radius), and `bga_zones: Vec<(i32,i32,i32,i32)>` in the struct. Rectangles
are plumbed all the way down; they carry *blocked* or *added cost*, never a rule
value. The gap is narrower than "no region concept": regions cannot carry rules.

`blocked_vias_small` (`#568`) is a working precedent for a second rule set — an
entire parallel via map for a second fab rung, empty meaning "not populated,
fall back conservatively". That is the shape of the fix and its price: one
duplicate structure per rule set.

### Blast radius

| | |
|---|---|
| `py_router` | 113 modules, 114,352 LOC |
| `.clearance` reads | 288, across 40 modules |
| obstacle-map build sites | 18 |
| `clearance_ledger` call sites | 20 |
| tests | 381 files, plus a stress corpus and CLI/GUI parity, both required |

## Constraints

- **Target Python-only.** `CONTRIBUTING.md` prefers it, and the Rust core ships
  as a prebuilt binary — a Rust change forces a release cut. The core accepts an
  opaque blocked-cell set, so a Python-only design is plausible, but the P2B
  prototype must prove its time and memory cost before this becomes a promise.
- **Never under-block.** Any unsupported condition is rejected before routing;
  supported conditions use KiCad's reverse-order, first-match semantics.
- **No persistent per-cell rule values.** `#422` moved permanent keep-out from
  ~38 B/cell hashmap entries to 1 bit each because cell count dominates memory
  on sparse boards. Temporary NumPy masks during stamping are acceptable;
  storing an `f32` beside every Rust grid cell is not.
- **One implementation path.** Every obstacle builder calls a shared regional
  resolver/stamper. A feature present only in plane routing is not support.

## P1 — typed, fail-closed clearance accounting (do this first)

Standalone safety fix. It does **not** attempt to preserve a locally relaxed
route as a clean output; without region geometry that cannot be represented.

1. Replace `_min_clearance: Optional[float]` with a `ClearanceUse` record:
   `(constraint, scope, value, reason, rule_ref, affected_items)`, where
   `constraint` is at least `copper` or `hole_to_copper`, `scope` is `board` or
   `local`, and the last two fields are empty unless a later regional resolver
   can name the authorizing rule and the exact generated primitives it covers.
2. Convert every `record()` caller explicitly. `plane_pad_tap.py` records a
   local copper exception; a hole rule is recorded only by code that actually
   routes or places copper against a hole at that value. No copper fallback may
   manufacture a hole-clearance record.
3. Split project writeback into three meanings:
   - `rules.min_clearance` is KiCad's absolute enable floor. Set it no higher
     than the smallest honestly represented board-wide or regional copper rule;
     never use it as the board-wide default.
   - netclass/default clearance remains the board-wide design default and moves
     only for an explicit board-wide copper record.
   - `rules.min_hole_clearance` changes only from a board-wide
     `hole_to_copper` record, never from a copper record.
4. A local record with no attached, serializable rule is an **output error**:
   the CLI returns non-zero and publishes no routed PCB, `.kicad_pro` or
   `.kicad_dru`; the GUI aborts the operation and restores its pre-operation
   live-board state. Reporting a warning while leaving ungradeable copper is
   not fail-closed.
5. Add a structured `clearance_usage` object to `JSON_SUMMARY`, containing the
   typed records and the output verdict. `check_drc` and the GUI consume the
   verdict; they do not pretend a local record is gradeable without geometry.
6. Stage PCB/project/custom-rule outputs under temporary names and validate the
   complete set. Publish through a small recovery journal: record original
   digests/backups, replace project and rules first, replace the PCB last as the
   commit marker, then remove the journal. An in-process exception rolls back;
   the next invocation recovers any journal left by a crash. Do not claim
   cross-file atomicity that the filesystem cannot provide.

This patch fixes the unsafe success mode independently of regional routing. It
may turn today's fine-pitch rescue into an explicit failure; preserving that
route requires P2A/P2B support and an existing rule that authorizes it.

**Cost: 3–4 days.** The code change is small, but every CLI/GUI exit path and
transactional writeback needs parity coverage.

## P2A — make the rule source real

Required before P2B, both upstream and on the evidence board.

1. Extend the board parser to retain **named non-keepout rule areas**, including
   polygon holes and layer sets. The current `BoardInfo.keepouts` model is not a
   substitute: it retains only keepout zones and drops the names referenced by
   `insideArea()` / `intersectsArea()`.
2. Replace the separate layer/track outputs of `kicad_dru.py` with one ordered
   `ClearanceRule` sequence preserving file order. Its closed variants are:
   - the existing unscoped/layer-only rule subset;
   - the existing track-pair/netclass subset;
   - one regional form with exactly one `constraint clearance (min ...)`, the
     symmetric condition `A.intersectsArea('name') ||
     B.intersectsArea('name')`, and an optional already-supported concrete,
     `outer` or `inner` `(layer ...)` clause.
   No variant may add `insideArea()` or combine predicates from another variant.
3. When any regional clearance rule is present, reject the board before routing
   if **any** clearance rule falls outside those variants; an unparsed rule could
   otherwise outrank a parsed one. Evaluate the unified sequence in KiCad
   reverse-file-order, first-matching-constraint order. Also reject every
   area-scoped width rule: regional width is not part of P2B. Non-clearance
   constraints remain the responsibility of their existing channels.
4. Change the native generator to emit the isolation barrier as a native
   track/via/copper-pour keepout matching `audit_pcb.py`, and emit named
   clearance areas only for the gate-escape rules that actually require them.
   Prove with `audit_pcb.py` plus KiCad DRC that the serialized constraints match
   the existing Python-only checks before asking any external router to consume
   them.

**Exit gate:** parse -> serialize -> KiCad reload preserves names, holes, layers,
conditions, and precedence; the evidence board's barrier is visible to the
router and a deliberate crossing fails both KiCad DRC and `audit_pcb.py`.

## P2B — pair-aware, fail-safe region-valued clearance

1. Introduce a shared `RegionClearanceResolver` called by all 18 obstacle-map
   build paths and by `check_drc`. Its input includes the unified ordered rule
   sequence, candidate route context (net, layer, track/via type and width), the
   obstacle item, and candidate geometry. Existing layer, track/netclass and
   regional custom rules therefore share one precedence decision.
2. Stop using one candidate-independent map when rule outcomes depend on the
   routed net. The correctness-first path routes one net at a time with a map
   keyed by `RouteRuleContext` (candidate net ID and class memberships, layer,
   width, candidate item type, normalized rules and area digest). After accepting
   a route, add its copper to the board model before building the next net's map.
   Never collapse candidate-net requirements to a maximum: that destroys legal
   relaxations and first-match precedence.
3. Preserve the existing shared-map fast path only when the resolver proves a
   set of candidate nets has an identical outcome signature against every
   supported rule and obstacle class. This equivalence optimization is optional
   for the first patch; per-net rebuild is the reference behavior. If its corpus
   cost is unacceptable, optimize with an immutable geometric base plus
   candidate-specific overlays, not candidate-net OR.
4. For each obstacle, generate candidate cells out to the largest value from the
   board default, netclasses, local pad/footprint overrides and every supported
   custom rule. For tracks, evaluate every legal incoming transition shape
   (horizontal, vertical and diagonal capsule); vias use their disc. Partition
   those temporary `(cell, transition)` entries by the first matching custom
   rule in KiCad precedence order; that rule replaces the class-resolved value,
   while local pad/footprint overrides retain their existing higher precedence.
   Keep an entry only when its real item-to-item distance is below the resolved
   value.
5. The current Rust map stores one track-blocked bit per cell, not per incoming
   direction. Collapse the retained transition entries with OR: if any legal
   transition into a cell must be blocked, block the cell. This cannot
   under-block, but can reject a direction that would be legal. Its excess is
   bounded to cells whose possible transition capsules disagree on area
   intersection; measure that boundary band. If the corpus cost is unacceptable
   or the excess is not so bounded, the Python-only constraint fails and the
   design needs direction-specific Rust maps.
6. Evaluate area predicates on **both items**, using their real copper shapes
   and KiCad item identity. Polygon holes and layer scope participate; testing
   only a grid-cell centre is insufficient. This covers both failure directions:
   the route enters a region while the obstacle remains outside, and the
   obstacle intersects a region while the route remains outside. A long existing
   segment intersecting an area may therefore carry that item-level rule along
   its full length if KiCad does; that is exact item semantics, not an
   obstacle-local maximum.
7. Cover track and via candidates against pad, segment, via and zone copper.
   Hole-to-copper and board-edge constraints remain separate typed channels and
   are not regionalized by this patch. If an engine cannot supply the required
   copper-item context, it rejects the rule rather than falling back to scalar
   clearance.
8. Preserve rule identity in generated copper: emit a segment boundary wherever
   its regional rule resolution changes, and prevent cleanup/reconciliation from
   merging across that boundary. Otherwise KiCad would grade a newly merged
   segment as one item and could apply a different area rule than the router did.
9. Replace direct scalar dilation in `obstacle_map.py`, `obstacle_cache.py`,
   `plane_obstacle_builder.py`, `plane_region_connector.py` and every remaining
   build site with the shared API. Cache keys include the complete
   `RouteRuleContext`; a map built for one candidate context must never be reused
   for another unless step 3 proved equivalence.
10. Extend `check_drc` with the same resolver, passing the actual item pair and
    geometry without the routing map's directional OR. `JSON_SUMMARY` carries
    the normalized rules, source digests, support verdict and measured
    overblock; CLI and GUI use the same object.
11. A local P1 record succeeds only when `rule_ref` names an existing parsed
    rule and the resolver proves every `affected_items` primitive was authorized
    at the recorded value. P2B does not invent rule areas around generated
    copper. Lower only the absolute `rules.min_clearance` as required, preserve
    the board-wide netclass default, publish transactionally and re-run KiCad
    DRC. Otherwise retain P1's hard failure.

**Cost: 10–15 days** on top of P1/P2A. Candidate-specific map lifecycle, pair
resolution, transition partitioning, segment-boundary preservation, all-engine
conversion, cache invalidation and oracle parity dominate; this is not a
one-function stamp change.

## Non-goals

- Push-and-shove, blind/buried vias — out of scope and unrelated.
- Regional track-width selection. P2A rejects those rules until a separate
  patch defines width transitions and impedance consequences.
- The full KiCad expression language. P2A supports and tests a closed grammar;
  everything else fails before routing.
- Rewriting the DRC severity map. The eight downgrades observed alongside the
  fab-floor relaxation are a separate question; a router should not be editing a
  project's severity policy at all, and that argument should be made on its own.

## Tests

To the repo's stated bar — stress corpus, KiCad-oracle comparison and CLI/GUI
parity are all required.

### P1 safety contract

- A local copper record returns non-zero, publishes no successful candidate and
  leaves PCB, `.kicad_pro` and `.kicad_dru` byte-identical; the GUI path leaves
  an identical serialized live board and no committed undo entry.
- A copper record never changes `min_hole_clearance`; only an explicit
  board-wide hole-to-copper record can do that.
- A board-wide copper record updates the default and absolute minimum without
  affecting the hole floor.
- Failure injected after each publication step restores every file; simulated
  crash remnants recover from the journal on the next invocation.
- CLI and GUI produce the same `clearance_usage` records and verdict.

### P2A parser and rule semantics

- Named areas round-trip with holes and layer sets.
- Overlapping regional, layer-only and track/netclass rules prove one global
  reverse-order, first-match decision for both tightening and relaxation.
- With regional mode active, any unparsed clearance rule and every regional
  width rule reject before routing.
- The generated isolation barrier fails both KiCad DRC and `audit_pcb.py` on an
  intentional crossing.

### P2B routing semantics

- Route inside / obstacle outside a stricter area cannot under-block; a second
  case keeps the route centre outside while its track capsule intersects.
- Two transitions entering the same cell but disagreeing on area intersection
  prove the directional OR never under-blocks and quantify its refusal band.
- Two candidate nets routed in one requested batch but with different
  track/netclass outcomes receive distinct `RouteRuleContext` maps; a relaxing
  rule proves no candidate-net maximum was taken. A no-rule control retains the
  current shared-map path.
- Obstacle inside / route outside exercises KiCad's swapped A/B evaluation.
- A long existing segment crossing a small area matches KiCad item-level
  behavior along its full length.
- Generated tracks split where rule identity changes, and every cleanup pass
  preserves the split.
- Track and via candidates cover pad, segment, via and zone-copper pairings;
  hole and edge rules remain unchanged in their separate channels.
- Tightening and relaxing calibrations differ from the scalar control by the
  injected amount, preserve the Default/netclass clearance, lower only the
  required absolute minimum and match KiCad DRC exactly.
- A local record succeeds only when its `rule_ref` exists and every
  `affected_items` primitive resolves to that value; missing, stale or
  over-broad item references retain P1's hard failure.
- A table-driven instrumentation test asserts every obstacle build path called
  the shared resolver; cache tests prove area/rule changes invalidate maps.
- Corpus diff reports routed length, via count, clean-pass and runtime across
  `tests/stress/`, with every regression named.

## Recommendation

**Do not adopt current KRT for the shunt-reversal board.** The "patched or not"
form of this claim is not supported: no conformant KRT output exists yet, so no
runtime for a constraint-respecting run has been measured, and the comparison
below is between a fully constrained native generator and a tool that received a
subset of the constraints. It is uncontrolled, and it cannot estimate what a
compliant KRT run would cost. The native
generator already produces 0 unrouted and 0 DRC *and passes the audits*. KRT
buys wall time — 54.5 s against ~270 s, a 5x gain on the fabricable run, not the
19x the unfabricable one suggests — on a board that is regenerated rarely, and
that 270 s is 83% failed searches, fixable in code we own. Set against a board
whose isolation barrier it routes straight through, the wall-time argument does
not start. Against that, adoption means taking a
114 kLOC dependency, forking it, and owning region-rule semantics its author has
not settled.

**P1 is worth contributing upstream regardless.** It removes the unsafe success
mode: one tight pad tap can no longer rewrite unrelated copper and hole defaults
and publish a board graded against them. The first safe version may reject that
route; accepting it requires P2A/P2B geometry and a KiCad-verifiable existing rule.

Keep the exploratory-scout rule intact throughout: KRT output is inspiration,
never promoted copper, until a manifest path attests it.

## Assumptions and proof obligations

- The evidence board currently carries no usable routing-area rules: all 16 rule
  areas are fill-only, while gate-escape and barrier constraints live in the
  generator's Python. P2A must change that before P2B can claim relevance to
  this board. This refutes "the constraint is inexpressible to a router":
  neither router was told.
- The closed P2A grammar is sufficient for the generated rules. Confirm against
  the serialized board and `.kicad_dru`; do not infer sufficiency from Python
  objects before KiCad reloads them.
- Python can build temporary candidate-cell partitions within acceptable memory
  and time. Benchmark the first resolver spike on the largest stress board; if
  it cannot, the Python-only constraint must be revisited rather than weakening
  semantics.
- The Rust core needs no change because it receives the final blocked-cell union.
  Confirm with track and via prototypes before converting all build sites.
- Effort figures assume familiarity with the codebase. A newcomer should roughly
  double them, and the 381-file test suite plus KiCad-oracle matrix is the main
  schedule risk.
