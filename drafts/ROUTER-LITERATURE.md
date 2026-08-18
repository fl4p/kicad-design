# What the literature calls what this router does

Research memo, 2026-08-18. Prompted by the profiling result that **96.4 % of routing
time is spent on connections that fail**, and the question of what to do next.

The short version: `pcb_route.py` implements a technique with a name, a known
complexity bound, and a known failure mode. The failure mode is the one we are
measuring. A common next step is to stop enlarging the pattern family and fall
back to a graph search. Monotonic dynamic programming is a promising intermediate
step, but whether it wins on this detailed PCB-routing problem is measurable, not
established by the literature alone.

---

## 1. This is pattern routing

Enumerating candidate polylines from a fixed family — L-shapes, Z-shapes,
staircases — is **pattern routing**, introduced under that name by Kastner,
Bozorgzadeh and Sarrafzadeh (ICCAD 2000). Their §2.2/§3 gives the complexity for a
2-terminal net with bounding box `A` and bounding-box perimeter `P`:

| family | complexity | quantity |
|---|---|---|
| L-shaped | `O(|P|)` | `|P| = 2·(|x1−x2| + |y1−y2|)` |
| Z-shaped | `O(|A|)` | `|A| = 2·|x1−x2|·|y1−y2| + |x1−x2| + |y1−y2|` |

with Theorem 3.1: `|P| ≤ |A| ≤ |E|`. At the algorithm-taxonomy level, our
`_paths()` families correspond to this: the offset family is Z-shaped pattern
routing with a fine offset step, and the effort-3 two-jog staircase is a 3-bend
extension.

That correspondence is not a drop-in equivalence. Kastner et al. and FastRoute
below address discretized IC/global-routing problems, while `pcb_route.py` emits
detailed PCB geometry and checks real pad escapes, widths, and clearances. Their
complexity results transfer only after we define a finite routing grid or graph
and account for the cost of our geometric legality tests. The literature names
the algorithmic structure; it does not by itself prove the runtime or quality of
an implementation here.

The paper is equally direct about the cost, and this is the sentence that matters
for our 96.4 %:

> "The maze router ensures that the least cost route (according to the cost
> function) is found. Pattern routing does not give you this luxury."
> — Kastner et al., ICCAD 2000, §3.1

**A fixed pattern family is incomplete relative to the broader modeled search
space.** It cannot prove that a physical connection is impossible; it can only
report that no member of its family fit. That is exactly why our failures are
expensive: a success stops at the first clear candidate (mean 0.04 s), a failure
exhausts every family and every escape pair (mean 0.31 s, and 411 such calls per
round).

## 2. The escalation never leaves the incomplete family

Our response to a failure is a **bigger pattern family** — effort 2 widens the
offsets, effort 3 adds 845 cubic staircases. A common literature response is to
**change algorithm class**:

> "A second problem is that routing inside of a small box with only L-shaped
> routes can lead to many infeasible routes. Maze routing is used to attempt to
> find routes for the nets that couldn't be routed with only L-shapes. This is
> expensive alternative in terms of time and number of bends because maze routing
> tends to make greedy decisions that can result in many bends."
> — Rakai, *Fast and Effective Methods for Alleviating Congestion in ILP-based
> Global Routing*, MSc thesis, University of Calgary, text-layer line 2655

Note what that says about cost: maze routing is called *expensive*. It may be
slower than a pattern attempt, especially on a large or fine graph. Its different
value is that exhausting a bounded finite graph proves that no route exists **in
that graph under the encoded rules**. It does not prove that no continuous-board
route exists, and it says nothing about electrical or manufacturing fitness. We
are currently paying the exhaustive cost of the largest incomplete pattern family
on each failure without obtaining even that model-relative result.

## 3. The concrete candidate: monotonic routing by dynamic programming

This is the finding worth testing. Pan and Chu's FastRoute 2.0 (ASP-DAC 2007)
replaced pattern routing with **monotonic routing**, and their slide 11 gives the
path counts on an `m × n` grid:

| method | number of paths searched |
|---|---|
| L-pattern | 2 |
| Z-pattern | `m + n − 2` |
| **monotonic** | `C(m+n−2, m−1) = (m+n−2)! / ((m−1)!(n−1)!)` |

and slide 12 gives the cost for that grid model:

> "Dynamic programming to find the least cost monotonic path.
> Complexity: **O(mn)** — same as Z-pattern routing"

For a common grid and cost model, a monotonic DP searches a combinatorially larger
set of monotone paths than the L/Z families for the same asymptotic cost as the
Z-pattern search, because it never enumerates a path — it propagates a least-cost
frontier. We enumerate 845 staircases explicitly and clearance-test each one; a DP
over a matching bounding-box grid would consider every staircase expressible on
that grid. Whether it is faster in wall-clock time depends on grid resolution,
state count, obstacle representation, and the cost of legality checks.

Its limit is in the name: monotone paths only. Our detour families (`det`, the
±3/±6 mm out-and-back) and the diagonal pad escapes are deliberately
*non*-monotone, and a monotonic DP will not express them. So it is a candidate to
replace the L/Z/staircase families, not the whole search. It is a strict coverage
increase only if the DP grid contains the existing monotone candidates and the
same clearance semantics.

## 4. What the open-source routers actually implement

Worth knowing, because the inspected implementations do not use our exact
fixed-pattern-only escalation.

**Freerouting** (Alfons Wirtz, 2004; still the de-facto F/OSS batch autorouter)
uses a **maze search over a free-space decomposition**. From the inspected source
in `app/freerouting/autoroute/`:

- `MazeSearchEngine.java` — *"Class for auto-routing an incomplete connection via
  a maze search algorithm"*; the frontier is a `SortedSet<MazeListElement>` priority
  queue ordered by cost plus *"a good lower bound for the distance between a new
  MazeExpansionElement and the destination set"*. That is A*-like best-first
  search; calling it exactly A* would require checking the full cost-update and
  heuristic semantics.
- `FreeSpaceExpansionRoom.java`, `ObstacleExpansionRoom.java`, `ExpansionDoor.java`
  — the primary inspected search representation decomposes free space into
  **rooms** joined by **doors**, and searches the room adjacency graph rather than
  a uniform grid.
- `MazeTraceShover.java` — it shoves.

The documented **KiCad 9** workflow has no integrated batch autorouter; it exports
Specctra DSN for external routing and imports SES results. Its interactive
push-and-shove router is a different animal. Wlostowski's 2015 FOSDEM presentation
describes three architectural choices relevant to ours; these are historical
architecture evidence, not a claim that every detail is unchanged in KiCad 9:

- *"Shapes are stored in R-Trees. One R-tree per board layer. Separate R-trees for
  pads & vias (reduce overlap)."* — against our uniform-cell bucket `SpatialIndex`,
  whose `near()` + `_cells` was 49.9 % of runtime before the memo and is 27.1 %
  after.
- *"No floating point. Guaranteed 1 LSB error in all calculations."* — integer
  coordinates structurally eliminate the floating-point reversal instability the
  codex review found in `_seg_box_clear` (disagreement at ~1e-16 mm between
  `(a,b)` and `(b,a)`). They do not eliminate all boundary degeneracies or modeling
  mistakes.
- Copy-on-write branch cloning of the whole board database, for springback and for
  *"trying out different optimization strategies"* — the mature form of our
  `snapshot()`/`restore()`.

**gEDA `pcb` / pcb-rnd toporouter** (Anthony Blake, 2009) is **topological**: it
builds a **Constrained Delaunay Triangulation** of the board and routes over that,
following the SURF rubber-band lineage (Dai, Dayan & Staepelaere, DAC 1991;
Staepelaere et al., *IEEE Design & Test* 1993; Dayan PhD 1997). Its own header
warns it is *"EXPERIMENTAL code"*.

## 5. What I would take from this

Ranked by value per unit of risk:

1. **Failure memo** (already measured here: 41.8 % of routing time re-proves
   failures on an identical key). Independent of everything below, provided the
   key contains every route-affecting state input and is invalidated when any of
   them changes.
2. ~~**Prototype monotonic DP as a feasibility test for the L/Z/staircase
   families.**~~ **MEASURED AND WITHDRAWN — see §6.** The DP finds a monotone
   F.Cu path for **0 of the 31** connections the pattern router failed. It would
   not route one of them.
3. **Bounded A* / maze fallback for what monotonic DP cannot route**, replacing
   effort 2/3 escalation if the benchmark supports it. Exhausting its finite graph
   proves that the graph contains no legal route under the encoded rules; it does
   not prove physical impossibility, and a first exhaustive search need not be
   cheap. Caching an identical model-relative failure is cheap.
4. **R-tree instead of the uniform-cell index** — but only if profiling still shows
   it after 1–3; I measured a rewrite at 1.12 × overall and rejected it once already.
5. **Integer coordinates**, if `_seg_box_clear`'s float boundary ever stops being
   academic. It is academic today: ~1e-16 mm against a 1e-4 mm candidate grid.

Points 2 and 3 can change the board, so both need the byte-identical-or-explain
discipline the memo work used, and both are larger than anything done so far. They
also need the normal board signoff: preserve locked and topology-sensitive copper,
run connectivity and DRC, rerun the project-specific geometry audits, and inspect
critical-net topology, return paths, current density, isolation, and rendered
copper. Search completeness is not board correctness.

---

## Source access log

| # | source | identifier | evidence state | attempts | route | validation | load-bearing |
|---|---|---|---|---|---|---|---|
| S1 | Kastner, Bozorgzadeh, Sarrafzadeh, *Predictable Routing*, ICCAD 2000 | https://cseweb.ucsd.edu/~kastner/papers/ICCAD00-PredictableRouting.pdf | partial — §§2.2, 3.1 and the complexity list | WebFetch → PDF structure only, not readable; re-extracted locally with `pdftotext -layout` | raw text (PDF text layer) | validated — quoted text coherent, matches §-numbering | **yes** |
| S2 | Pan & Chu, *FastRoute 2.0*, ASP-DAC 2007 (slides) | https://www.aspdac.com/aspdac2007/pdf/archive/3A-2.pdf | partial — slides 2–14 | WebFetch → PDF structure only; re-extracted with `pdftotext -layout` | raw text (PDF text layer) | validated | **yes** |
| S3 | Rakai, *Fast and Effective Methods for Alleviating Congestion in ILP-based Global Routing*, MSc thesis, Univ. of Calgary | https://ucalgary.scholaris.ca/bitstreams/1db9f7f3-79d6-4a87-ba6a-46fd87f92fd6/download | partial — the pattern-vs-maze passage only | WebFetch 302 → redirect host; WebFetch refused (15.8 MB > limit); curl 200, 15 789 393 B, `pdftotext` | raw text (PDF text layer) | validated | **yes** |
| S4 | Freerouting source, `autoroute` package | https://github.com/freerouting/freerouting/tree/4dd7dad758bb4313d1613c2fbcf0a9cacb37d982/src/main/java/app/freerouting/autoroute | partial — file listing | first attempt, 200; pinned from `master` as retrieved 2026-08-18 | raw text (DOM) | validated against pinned tree | **yes** |
| S5 | Freerouting `MazeSearchEngine.java` | https://raw.githubusercontent.com/freerouting/freerouting/4dd7dad758bb4313d1613c2fbcf0a9cacb37d982/src/main/java/app/freerouting/autoroute/MazeSearchEngine.java | partial — class javadoc + field comments | first attempt, 200; pinned from `master` as retrieved 2026-08-18 | raw text | validated against pinned source | **yes** |
| S6 | Wlostowski, *Interactive PCB Routing*, FOSDEM 2015 | https://archive.fosdem.org/2015/schedule/event/pcb_routing/attachments/slides/796/export/events/attachments/pcb_routing/slides/796/fosdem_router.pdf | inspected — all 32 slides; historical architecture evidence | WebFetch → unreadable; re-extracted with `pdftotext -layout` | raw text (PDF text layer) | validated | **yes**, for the 2015 architecture only |
| S7 | `toporouter.c`, gEDA/pcb | https://raw.githubusercontent.com/russdill/pcb/5cf901321b96dd0a929391a9fad8b6680ee32631/src/toporouter.c | partial — file header only | doxygen mirror 302 → dead host; GitHub raw 200; pinned from `master` as retrieved 2026-08-18 | raw text | validated against pinned source | **yes** |
| S8 | Lee, *An Algorithm for Path Connections and Its Applications*, IRE Trans. Electronic Computers **EC-10(3)**, 346–365, Sept 1961 | doi:10.1109/TEC.1961.5219222 | metadata only | ieeexplore.ieee.org/document/5219222 → empty body; identity confirmed via IEEE "similar articles" page + 4 independent citing records | raw text (metadata) | validated — see note | no (cited for provenance only) |
| S9 | Wikipedia, *Routing (electronic design automation)* | https://en.wikipedia.org/wiki/Routing_(electronic_design_automation) | partial | first attempt, 200 | intermediary model | unvalidated extraction | no — used only for discovery of named algorithms |
| S10 | He, *Towards automated PCB routing*, Iowa State, 2024 | https://dr.lib.iastate.edu/server/api/core/bitstreams/baa06fe6-541d-4f4a-888d-94f3083cd518/content | available but not inspected for this memo | This memo's direct attempts returned 403; companion research in `PCB-AUTOROUTING.md` obtained the PDF through publisher REST/raw-PDF access and inspected the relevant routing sections | route recorded in companion research | identity and access reconciled against DOI `10.31274/td-20240617-74`; content not relied on here | no — no claim rests on it |

**Metadata note (S8).** Secondary sources disagree on the issue number: two
(Chegg, an EMU thesis bibliography) give **EC-10(2)**. IEEE's own record and four
independent citing papers give **EC-10(3), Sept 1961**. I have used **issue 3**.
The full text was not obtained and no claim here depends on its content — Lee is
cited as the origin of maze routing, which is uncontested across every source read.

**Challenge searches** (§2.1). Against "pattern routing is fast but incomplete, so
maze routing is the right fallback": searched for evidence that pattern routing
suffices alone. Found genuine disagreement — BoxRouter (Cho & Pan, TCAD 2007)
argues for *"a simple pattern based routing rather than maze routing for fast
runtime without incurring significant routing quality degradation"*. That is a
real counterweight, recorded rather than resolved. It does not overturn the
recommendation to test a fallback here, because our measured problem is not route
*quality* but repeated failure cost. A bounded model-complete search can turn an
exhaustive failure into a reusable negative result; making repeats cheap still
depends on a sound memo key and invalidation policy.

**Source dependence** (§2.2). S2 (FastRoute 2.0) is by the same authors as the
FastRoute line generally; the Wiley 2012 FastRoute survey found in searching is
the same group again, so it is **not** independent corroboration and I have not
cited it as such. S1 and S3 are independent of S2 and of each other. S4/S5 are one
codebase — one source, two files.

**What would discriminate.** Whether monotonic DP actually helps *this* board
turns on how much of the 32 unrouted set needs non-monotone paths. That is
directly measurable without implementing anything: take the 32 failing endpoint
pairs, and check whether any monotone staircase between them is clear, by running
the DP as a pure feasibility test. If most are monotone-infeasible, the value is
in point 3 (maze fallback), not point 2.

**That test has now been run — see §6. The answer was point 3.**

---

## 6. MEASURED: the monotone DP would not route a single failure

`monotone_probe.py`, run as `gen_pcb.py --full --rounds 1 --monotone-probe`.
Read-only: it calls the router's own `_clear` and writes nothing. The DP
propagates a reachability frontier over a 0.1 mm grid — half the router's 0.2 mm
offset step — with the grid landing **exactly** on both endpoints, so a positive
verdict is a path to the real pad and not to a rounded neighbour of it.

```
calibration A (clear site (81.0, 2.0)->(84.0, 5.0), 0.25 mm): True  expect True
calibration B (same site, 40 mm wide):                        False expect False
calibration C (DP on 25 ROUTED connections):    16 feasible -- expect non-zero
monotone-DP: 0/31 FEASIBLE on F.Cu that the pattern router failed,
             0 unevaluable, 1.6 s total
```

**Result: 0 of 31.** Not one connection the pattern router failed has a clear
monotone F.Cu path. So §3's proposal is dead for this board: a monotonic DP would
search a combinatorially larger space and come back with nothing, because the
space it searches is empty here. The failures need non-monotone paths, a layer
change, or both — which is what `route()` step 2 already attempts and what a maze
/ A* search is for.

Three notes on why this result is believable, since a clean zero is exactly the
shape a broken probe produces:

* **Calibration C is the one that counts.** A and B only prove the edge test can
  return True somewhere empty and False when nothing could fit. C runs the same
  DP on connections the router *actually routed*, in the same congested control
  section, and 16 of 25 come back feasible — so the DP demonstrably finds paths
  that exist there. The other 9 are the ones that escaped to an inner layer via
  step 2, which are correctly infeasible for a single-layer F.Cu test. A zero
  here would have voided the headline result, and the probe refuses to print it.
* **Zero unevaluable.** No pair was skipped for a degenerate span, a budget
  overrun or an exception, so the 31 are 31 real verdicts rather than 31 silences.
* **The early frontier death is real, not a shortcut.** Many pairs return in
  0.00–0.02 s despite spans of 10⁵–10⁶ cells because the reachable frontier
  empties within the first few columns and the DP stops — the connection cannot
  get out of its own neighbourhood monotonically. That is a stronger statement
  than "no monotone path reaches the target": for most of these, no monotone path
  gets *anywhere*.

**Scope, stated plainly.** This tests monotone paths on F.Cu at one width pair
(`c["w"]` and the 0.15 mm fallback), single-layer, no vias. It is exactly the
subspace the L/Z/staircase families search, which is what makes it the right test
of §3 — but "no monotone F.Cu path" is not "no path". It says the pattern
families are leaving nothing on the table on F.Cu, not that these connections are
unroutable.

---

## 7. Handing the routing to FreeRouting: the mechanism, verified end to end

Researched 2026-08-18. The question was whether a `pcbnew` generator can keep its
hand-laid, measured copper and hand only the *control section* to an external
autorouter. **The mechanism exists, and it is the item lock.** The chain below was
read in the source on both sides, not inferred from documentation.

### 7.1 The lock is the protection, and it is airtight

| step | source | what it does |
|---|---|---|
| export | KiCad `specctra_export.cpp` | unlocked track → `T_protect`; **locked track → `T_fix`**, with the comment *"tracks with fix property are not returned in .ses files"*. Vias identically. |
| read | Freerouting `io/specctra/parser/Wiring.java`, `calcFixed()` | `FIX` → `FixedState.SYSTEM_FIXED`; anything that is not `NORMAL` — which includes `protect` — → `FixedState.USER_FIXED` |
| semantics | Freerouting `board/model/structure/FixedState.java` | *"Sorted fixed states of board items. The strongest fixed states came last"* — `UNFIXED < SHOVE_FIXED < USER_FIXED < SYSTEM_FIXED` |
| enforcement | Freerouting `board/model/items/Item.java` | `isDeletionForbidden()` is true when `isUserFixed()`; `isUserFixed()` is `fixedState.ordinal() >= USER_FIXED.ordinal()` |
| import | KiCad `specctra_import.cpp` | `if( !track->IsLocked() ) aCommit.Remove( track );` — *"Remove unlocked tracks/vias (locked ones stay; they are exported as fixed and omitted from the .ses)"* |

Two consequences, and the first is stronger than expected:

* **Freerouting cannot delete anything KiCad sends it.** Even an *unlocked* track
  exports as `protect`, which lands at `USER_FIXED`, which forbids deletion. The
  autorouter adds copper; it does not rip up what it was given.
* **Locking additionally buys `SYSTEM_FIXED` (not shovable) and omission from the
  `.ses`,** so KiCad's importer never sees a replacement for it and its own
  `IsLocked()` test preserves the original. Lock the power copper and it survives
  the round trip *by two independent mechanisms*.

Zones survive too, explicitly: *"The board already has its zones; keep those and
ignore the session pour geometry."*

### 7.2 What the round trip carries

Verified in `specctra_export.cpp`: **rule areas export as keepouts** — `T_keepout`,
`T_via_keepout`, or `T_wire_keepout` by flag, with layer information. **Net classes
export** `(width …)` and `(clearance …)` via `exportNETCLASS()`. Copper zones export
as `(wire (polygon …))` rather than `(plane …)`, because *"Specctra treats (plane
...) as pins"*.

So a region policy that today lives in Python — "In1/In2 barred, F.Cu/B.Cu barred
west of `POWER_KEEP_X` except in N gate windows" — is expressible, but **only if it
is emitted as KiCad rule areas**. A policy that exists solely as a predicate in the
generator does not cross the DSN boundary and the external router will happily
route through it.

### 7.3 Three things that must be handled, not assumed

**Determinism is not free, and it is the direct conflict with "generate, never
hand-place".** A `-random_seed` CLI option exists, but a maintainer confirmed on
discussion #583 that it did not work in CLI mode: *"I think there was an issue with
the random seed parameter in CLI mode in version 2.1."* — fix pending after v2.1.0.
Two runs with identical inputs produced different `.ses` files. **Any generator that
calls an external autorouter must verify md5 stability empirically on the exact
version installed**, and must treat a wobbling hash as a blocker rather than noise.
This is the reproducibility guard's whole purpose meeting a heuristic third-party
tool.

**The optimizer can create clearance violations.** Issue #103, *"Route optimization
creates clearance violations"*, is **open and unfixed**, labelled `enhancement` and
`help wanted`. Autorouter output is therefore a *proposal*, never a result: run
KiCad DRC and every project-specific audit after import, with the same severity as
on hand-laid copper.

**The importer moves footprints.** `specctra_import.cpp` repositions, re-orients and
flips footprints from the session's placement data, and deletes markers. For a flow
where a `place.py` owns placement and its positions are load-bearing, that is a
silent overwrite of the placement authority. Verify positions after import, or
confirm the session carries no placement changes.

### 7.4 Compatibility check on this board

Local, measured 2026-08-18: `SetLocked()` is available on both `PCB_TRACK` and
`PCB_VIA` from the scripting API, so the protection is reachable from a generator.
Pad shapes are **377 rectangle, 276 roundrect, 19 circle — no custom pads**, so the
exporter's convex-hull caveat (*"Freerouter does not handle them very well: too
complex shapes are not accepted, especially shapes with holes"*) does not apply.
34 zones, of which 16 are already rule areas.

### 7.5 The shape of the flow this suggests

1. generator lays **all** hand copper — power section, escapes, clamps, thermal
   vias, stitching — exactly as now, and **locks every piece of it**;
2. generator emits the region policy as **rule areas**, not only as a Python
   predicate, so the constraint crosses the boundary;
3. export DSN; run `freerouting -de … -do … --gui.enabled=false -mp N`, optionally
   `-inc` to exclude power net classes from routing entirely;
4. import SES — locked copper untouched, zones untouched, footprints **checked**;
5. run DRC **and** the project's own audits, which know things no autorouter does
   (on this board: equipotential entry, P/N pour congruence, gate guard, thermal
   vias, barrier bands);
6. verify the md5 is stable across two identical runs before trusting any of it.

The prize is that steps 1–2 are where the engineering judgement lives, and the
control-section point-to-point search — the part that costs 96.4 % of our runtime
proving connections impossible — is the part a mature maze router with shove
already does better.

### 7.6 Sources for §7

| # | source | evidence state | attempts | route | validation | load-bearing |
|---|---|---|---|---|---|---|
| S11 | KiCad `specctra_export.cpp` (source-mirror, master) | partial — wiring/zone/keepout/netclass export paths | first attempt, 200 | intermediary model over raw file | validated against the doxygen rendering of the same file | **yes** |
| S12 | KiCad `specctra_import.cpp` (source-mirror, master) | partial — removal logic, zone and footprint handling | first attempt, 200 | intermediary model over raw file | unvalidated extraction — see note | **yes** |
| S13 | Freerouting `io/specctra/parser/Wiring.java` | partial — `calcFixed()` | tree listing via `gh api` after two 404s on guessed paths; then raw, 200 | intermediary model over raw file | unvalidated extraction — see note | **yes** |
| S14 | Freerouting `board/model/structure/FixedState.java` | inspected — whole file | first attempt, 200 | intermediary model over raw file | unvalidated extraction | **yes** |
| S15 | Freerouting `board/model/items/Item.java` | partial — `isDeletionForbidden`, `isShoveFixed`, `isUserFixed` | first attempt, 200 | intermediary model over raw file | unvalidated extraction | **yes** |
| S16 | Freerouting `docs/command_line_arguments.md` | partial — argument list | first attempt, 200 | raw text | validated | **yes** |
| S17 | Freerouting discussion #583 (random seed determinism) | partial — question + maintainer reply | first attempt, 200 | intermediary model | unvalidated extraction | **yes** |
| S18 | Freerouting issue #103 (optimizer creates clearance violations) | partial | first attempt, 200 | intermediary model | unvalidated extraction | **yes** |
| S19 | KiCad doxygen rendering of `specctra_export.cpp` | partial — lines 64–69 | first attempt, 200 | raw text (DOM) | validated | yes |
| S20 | this board, `shunt-reversal.kicad_pcb` via `pcbnew` | inspected — pad shapes, zone/rule-area counts, lock API | local | measured | validated | **yes** |

**Extraction caveat.** S12–S15, S17 and S18 were read through WebFetch's summarising
model rather than character-by-character. The quoted code fragments are short and
mutually corroborating — the export side, the read side, the enum ordering and the
enforcement predicate independently agree on the same conclusion, and S11 was
cross-checked against an independent doxygen rendering of the same file. But before
any of this is built on, **the `FixedState`/`isDeletionForbidden` chain should be
re-read from a local clone**, because the entire protection argument rests on it.

**A source-comment inconsistency worth knowing.** In `FixedState.java` the comments
appear transposed: `SHOVE_FIXED` is described as *"fixed by the user"* and
`USER_FIXED` as *"fixed by the system"*. The enum *ordering* is what the code acts
on, and the ordering is unambiguous, but do not read those comments as the contract.

**Challenge search.** Against "the autorouter's output can be trusted once locking
protects our copper": searched for round-trip failures and output defects. Found
both, and they are recorded above as blockers rather than footnotes — determinism
(#583) and optimizer-induced clearance violations (#103). Neither defeats the
handoff; both defeat trusting it without verification.
