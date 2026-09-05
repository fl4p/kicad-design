# Routing methodology: what the literature and the tools actually say

Decision-grade research, 2026-09-05. Motivating case: the o2-probe board, whose routing
reads as trial-and-error with an unexplained via population. This file is the evidence
record; the rules distilled from it live in [`../ROUTING.md`](../ROUTING.md).

Browser surface: standalone Playwright (Python) driving installed Chrome
(`channel="chrome"`), headless for rungs 1–4 and a headed scratch-profile Chrome over CDP
(port 9333) for rung 5. Control proven before every fetch batch by navigating `about:blank`
and reading `document.documentElement.tagName` → `HTML`; each retrieval validated by title,
byte count, and `%PDF` magic where applicable.

## 1. The board that prompted this

Measured from `~/dev/ee/hw/o2-probe/o2-probe.kicad_pcb` (KiCad 10 format, `version 20260206`)
twice and independently: first with a regex reader over `(segment …)`/`(via …)` blocks, then with
`scripts/kicad_route_shape.py` under KiCad 10.0.5's bundled `pcbnew`. Every figure below agreed
between the two except the raw segment count, where the regex reader's strict field-order pattern
missed 20 segments; **`pcbnew` is authoritative and its numbers are the ones tabulated.** Direct
evidence, `origin: measured`, `transformation: calculated` (counts and Euclidean segment lengths;
`hypot(x2-x1, y2-y1)` per segment, summed per layer and per net).

| quantity | value |
|---|---|
| board | 20 × 107 mm, 4 copper layers (`F.Cu`, `In1.Cu`, `In2.Cu`, `B.Cu`), 113 footprints |
| track segments | 1793, total 2125.69 mm |
| segments by layer | F.Cu 893, B.Cu 580, In1.Cu 222, In2.Cu 98 |
| track length by layer | F.Cu 896.5 mm, B.Cu 606.7 mm, In1.Cu 503.2 mm, In2.Cu 119.4 mm |
| vias | 197, **all 197 spanning `F.Cu`→`B.Cu`** — zero blind/buried, zero terminating on an inner layer |
| routed nets | 61; vias/routed net **3.23**; 10 nets carry no via |
| segment length | median **0.500 mm**, mean 1.186 mm, p90 3.00 mm; **32 %** below 0.2 mm |
| worst nets | `/GND` 25 vias / 427 segments / 243 mm; `/3V3D` 21 / 167 / 167 mm; `/I2C_SDA` 12 / 105 / 116 mm |
| signal-net share of vias | 172 of 197 |

Two signatures matter, and neither is a matter of taste:

- **Every layer change is a full through-via.** The board has two inner layers and 623 mm of
  inner-layer routing, yet not one via terminates there. Layer transitions are being spent as
  undifferentiated F→B hops rather than as a planned escape to a chosen routing layer.
- **1793 segments for 2126 mm of copper.** 32 % of all segments are shorter than
  0.2 mm. The hypothesis this suggests is a per-cell grid path emitted verbatim rather than a
  routed polyline — human routing produces long runs joined at few corners. It stays a hypothesis
  until the collinearity-merge test in §4 runs: a file that merely records a straight run as many
  collinear segments would show the same distribution with identical copper.
- **Three of four layers run the same way, and the fourth carries almost nothing.** Measuring the axis-aligned share of copper *length* per
  layer (±5° tolerance) gives horizontal/vertical of 20 %/38 % on F.Cu, 19 %/45 % on B.Cu,
  10 %/57 % on In1.Cu and 30 %/14 % on In2.Cu. Three of the four layers run predominantly
  lengthwise, and In1.Cu — the only real inner routing layer, with 503 mm of copper — is the most
  lengthwise of all at 57 % vertical against 10 % horizontal. On the explicit criterion "a layer is
  a horizontal routing layer if more of its copper length runs horizontally than vertically", only
  In2.Cu qualifies (30 % h against 14 % v), and it carries 119 mm — 6 % of the board's copper. So
  crossing traffic has almost nowhere to go but into a via or a diagonal. The remainder
  in each case (33–56 % of length) is 45° copper belonging to neither axis.

The board's provenance (`o2-probe/ROUTING.md`) records it as the union of **bounded per-net
KiCadRoutingTools passes** at two different commits, plus a series of local repair and adoption
scripts. That is the mechanism behind the appearance: many independent greedy searches, each
optimal only for itself, welded together afterwards.

## 2. Why a router's output looks like trial and error — the structural reasons

### F1. The published objective is wirelength *plus vias*, and vias are just a weight
> "In many PCB routing problems, an objective function is defined to minimize certain metrics.
> The most common objective function is a weighted sum of the total wirelength and the number
> of vias."

He 2024, §3.4, p. 48. Direct evidence. The same work makes the weight explicit in its
post-processing A*: `g(x) = C_wl + γ_g · N_via` (Eq. 3.3, p. 59), where `γ_g` is "the weights
assigned to the number of vias". **Inference:** via count is therefore not an emergent property
you diagnose after the fact — it is a dial, and if you do not set it, the tool's default sets it
for you.

### F2. Routing is net-by-net, and the net order is arbitrary
He 2024's Figure 3.2 (p. 49) shows the main routing cycle as *"Randomly select a net → global
routing → detailed routing → all nets connected & all rules met? → rip-up and reroute"*; §3.5.1
names the three phases as main routing, rip-up-and-reroute, and post-processing. In the
experiments the net order used is "the default order provided by each PCB design" (p. 60).
Direct evidence.

*(Page numbers corrected 2026-09-05 after two independent reviewers re-fetched the same PDF: the
objective statement is on printed p. 48, Eq. 3.3 on p. 59, the experimental net order on p. 60, and
Figure 3.2 with "Randomly select a net" on p. 49. The quoted wording was confirmed unchanged.)*

TritonRoute-WXL states the consequence for the DRC-clean-up phase plainly: *"Use of
ripup-and-reroute to resolve DRC can rely heavily on net ordering"* — accepted manuscript p. 9,
UCSD j136. **Manifestation note:** the inspected file is the 2021 accepted manuscript; the version
of record is IEEE TCAD 41(4), April 2022, DOI 10.1109/TCAD.2021.3079268. Direct evidence.

**Inference:** an agent that runs "bounded per-net passes" and then repairs is not doing a
degenerate version of the algorithm — it *is* the algorithm, minus the global rip-up loop that
normally repairs the order dependence. Removing that loop is what turns net-order sensitivity
into visible incoherence.

### F3. The search is deliberately suboptimal, by a factor you can read off the config
Weighted A* evaluates `f'(n) = g(n) + w · h(n)`; with an admissible `h` and `w > 1`,
*"the solution cost cannot be greater than the optimal cost by more than a factor of w"*
— Hansen & Zhou, *Anytime Heuristic Search*, JAIR 28 (2007) 267–297, §1 p. 269
(arXiv:1110.2737 p. 3, attributing the bound to Davis et al. 1988). Direct evidence.

KiCadRoutingTools ships `HEURISTIC_WEIGHT = 2.3` (`py_router/routing_defaults.py:107`),
raised from 1.9 on a corpus dose-response. Its heuristic is 3D Chebyshev,
`max(dx,dy) + |Δlayer|·via_cost`, scaled by the weight (`docs/routing-architecture.md`,
"Heuristic"). Direct evidence from source.

**Inference (mine, mark it as such):** that heuristic underestimates the true cost on this
move set, so the `w`-admissibility bound applies, and each net's route may legitimately cost
up to **2.3×** the optimal for that net's own cost function — a cost function in which a via
is priced at 75 grid steps. A path 2.3× off optimal is exactly what "no sense to how it is
routed" looks like. This is the tool working as configured, not misbehaving.

### F4. The cost weights are tuned for completion rate, not for legibility
KRT's live defaults (`py_router/routing_defaults.py`, checkout at tag v0.21.3,
commit `749cfa83`):

| constant | value | line |
|---|---|---|
| `VIA_COST` | 75 (grid steps; ×1000 cost units) | :41 |
| `TURN_COST` | 1000 (= one grid step) | :43 |
| `HEURISTIC_WEIGHT` | 2.3 | :107 |
| `DIRECTION_PREFERENCE_COST` | 250 (per off-axis move; 0 = disabled) | :135 |

Move costs are orthogonal 1000, diagonal 1414, via `via_cost × 1000`
(`docs/routing-architecture.md`, "Cost Function"). Direct evidence.

Arithmetic (`transformation: calculated`, recomputed once): the turn cost scales with the turn
angle, so **a 90° corner costs 1/75 of a via and a 45° corner 1/150**, and off-axis travel breaks
even against a via only after 300 off-axis moves — 30 mm at a 0.1 mm grid. So under these weights the router has almost no reason to keep a run straight
and no reason to detour laterally instead of diving to another layer. Fragmentation and via
population are the priced-in outcome.

The project's own history says the same thing from the other side. Commit `aaa60331`
("revert #663: DIRECTION_PREFERENCE_COST back to 250, re-screened with KiCad live") records
that the value was briefly lowered to 5 on a screen that ran **with the DRC oracle disabled**
— *"find_kicad_cli() returned None, oracle_reconnect returned available=False, and every
oracle leg … was a no-op. The oracle welds copper, and copper is what DRC grades, so a change
screened with it disabled can look flat and not be."* The re-screen at one commit with KiCad
live gave 95 incomplete nets at 5 versus 116 at 25 and 117 at 50. Direct evidence from the
commit message.

`docs/api-routing-config.md:335` (now stale against that revert — record the divergence)
adds the boundary condition: 250 *"priced every off-axis move above 3 vias (`VIA_COST` 75),
forcing detours"*, 0 *"loses the layer organization entirely"*, and 5000 *"enforce[s] strict
human-style lanes but make[s] dense boards' short diagonal hops unroutable"*. **The tool's own
documentation states that strict human-style lanes and dense-board routability trade against
each other.** That is the honest framing: lane discipline is not free, and it is not the
router's default.

### F5. Fanout is a separate stage with its own objective, and its objective is crossings
> "the PCB fanout includes assigning layers, determining the fanout locations, and routing from
> the pins to the fanout locations … The fanout locations of a net must be in the same layer, and
> the topology crossings `Tc(n)` of nets must be minimized while satisfying the routing
> constraints C."

Li et al., *FanoutNet*, AAAI-23, pp. 8554–8555, "Introduction" and "Problem Formulation".
Direct evidence. The paper's stated motive is that *"PCB fanout is very important for reducing"*
the difficulty of the routing that follows, and its results table reports routability, via count
and wirelength as the joint outcome.

Freerouting implements the same decomposition operationally: *"You can also define, if before
autorouting a fanout pass and after autorouting a postroute pass for reducing the via count and
the cumulative trace length should run … In a fanout pass the connections will be routed only
till the first via."* — Freerouting Reference Manual, "Routing Options" → "Autoroute Parameter".
Direct evidence.

**Inference:** layer assignment and pin escape are a *decision*, taken before area routing, whose
objective is minimising crossings. The o2-probe board has no such stage — which is exactly why
all 197 of its vias are undifferentiated full-stack hops.

### F6. Per-layer preferred direction is a first-class router input, everywhere
- Freerouting: *"you can define the layers which may be used by the autorouter, the preferred
  direction for traces on each layer, and if vias may be inserted"* ("Autoroute Parameter"), and
  on the CLI `--router.layers.preferred_direction_horizontal=true,false` and
  `--router.layers.routable=false,true` (`docs/command_line_arguments.md`, "Layer-specific
  settings (Arrays)"). Direct evidence.
- Freerouting derives the assignment from board geometry when you do not: preferred direction
  alternates across signal layers seeded by `horizontalWidth < verticalWidth`, and the
  against-preferred penalty is `0.1 · round(10 · W/H)` — `RouterSettings.java`,
  `applyBoardSpecificOptimizations`. Direct evidence from source. **Inference:** on a
  20 × 107 mm board that formula yields roughly +0.2 on one axis and +5.4 on the other against a
  base cost of 1.0, i.e. a ~6× asymmetry — a long, narrow board is a geometry these heuristics
  handle very badly, because half the layers get a preferred direction only 20 mm long.
- KRT: `layer_direction_preferences` (0 = horizontal, 1 = vertical, 255 = none) plus
  `direction_preference_cost`, added in 0.13.0 to produce *"more organized, human-like routing
  patterns"* (`rust_router/README.md:506`). Direct evidence.

### F7. Via minimisation is a named problem, and its two forms tell you when to act
The VLSI physical-design literature splits it into **constrained via minimisation (CVM)** — the
routing topology is fixed and only layer assignment is free — and **unconstrained via
minimisation (UVM)**, where topology is free too (Sarrafzadeh & Wong, *An Introduction to VLSI
Physical Design*, McGraw-Hill 1996, §8.1; Sherwani, *Algorithms for VLSI Physical Design
Automation*, ch. 8 "Via Minimization"). Evidence state for both: **metadata only** — the
formulations were confirmed only from catalogue/front-matter and secondary quotation, and nothing
load-bearing rests on the exact clause. The usable point, which is architectural rather than
numeric: **once the topology is committed, the only remaining lever on via count is layer
assignment.** Fixing vias after routing is the constrained, weaker problem.

## 3. The claim that does *not* survive

A blanket "autorouters make too many vias" is not supported by the strongest evidence *in this
set* — a bounded statement, because the challenge search below reached only the open web. He 2024,
Chapter 4, on the 317 boards routed successfully by all three compared routers:
*"Among these routers, the FreeRouting has the highest success rate and longest wirelength and it
has the lowest number of vias … FreeRouting generates fewer vias than the original solution."*
Measured: FreeRouting averaged 2.6 vias against 8.1 for the original solutions. Direct evidence.

**Correction, 2026-09-05.** This file previously called those originals "the human-routed board as
published". That is unsupported — the dissertation does not state how the original solutions were
produced — and the claim is withdrawn. What survives is the router-to-router comparison and the
router-to-original via ratio, neither of which needs the originals to be hand-routed.

So the defensible claim is narrower, and it is the one this file makes: **this backend, in this
configuration, driven as a sequence of bounded per-net passes without a global rip-up or postroute
stage, produced 3.23 vias per net and 32 % sub-0.2 mm segments on this board.** The remedy is
configuration and staging, not abstinence from automation.

Challenge searches run against the conclusions (§3 of the research contract): "autorouter produces
fewer vias than manual routing evidence criticism manual routing myth study" (Serper/Google,
2026-09-05) returned only vendor blogs and forum opinion — no rigorous refutation and no rigorous
confirmation. The discriminating evidence above therefore comes from a primary I inspected, not
from the challenge search; confidence that no stronger published comparison exists is **low**,
bounded by having searched only the open web and not the ACM/IEEE full-text corpora.

## 4. What would discriminate

- **Whether the fragmentation is cosmetic or electrical.** Segment count does not change copper
  geometry if the segments are collinear. A collinearity-merge pass over the o2-probe board would
  settle it: if merging collapses the authoritative 1793 segments to a few hundred without moving copper, the
  fragmentation is a file-hygiene finding; if it does not, the paths genuinely wander.
- **Whether the via population is placement or configuration.** Re-running one dense cluster with
  `direction_preference_cost` unchanged but `via_cost` raised, on identical placement, separates
  "the weights bought these vias" from "the placement forced them". The literature predicts the
  second dominates; the board's history (a two-layer re-floorplan already attempted and abandoned,
  per `TWO-LAYER-REFLOORPLAN.md`) is consistent with congestion being real.
- **Whether an explicit fanout stage helps here.** FanoutNet's objective is crossing count; the
  cheap local test is to count topology crossings at the two dense clusters before and after a
  hand-authored escape pattern.

## 5. Source access log

Every row: identifier · evidence state · attempt history · route · validation · load-bearing.

| # | source | evidence state | attempt history | route | validation | LB |
|---|---|---|---|---|---|---|
| 1 | He, Youbiao. *Towards Automated PCB Routing: Leveraging Machine Learning and Heuristic Techniques.* PhD dissertation, Iowa State University, 2024. `https://dr.lib.iastate.edu/server/api/core/bitstreams/baa06fe6-541d-4f4a-888d-94f3083cd518/content` — no DOI found (dissertation; queried title+institution) | **partial** — §1.4.1, §3.4, §3.5.1, Fig. 3.2, Eq. 3.3, §3.6 and Ch. 4 results read; remaining chapters not read | curl+UA → 403; Playwright request context → 403; headed CDP Chrome, same-origin `fetch()` from `dr.lib.iastate.edu` → 200, 1 587 971 B, `%PDF` | raw text (`pdftotext -layout`) | validated (title page, pagination and section numbers coherent) | **yes** |
| 2 | Hansen & Zhou, *Anytime Heuristic Search*, JAIR 28 (2007) 267–297. arXiv:1110.2737 | **partial** — §1 and §2.3 read for the `w`-admissibility statement | Playwright request context → 200, 305 952 B, `%PDF` | raw text | validated | **yes** |
| 3 | Li, Haiyun et al., *FanoutNet: A Neuralized PCB Fanout Automation Method Using Deep Reinforcement Learning.* AAAI-23, pp. 8554–8561. `https://ojs.aaai.org/index.php/AAAI/article/view/26030/25802` | **partial** — Introduction and Problem Formulation read; experiments skimmed | first attempt → 200, 725 923 B, `%PDF` | raw text | validated | **yes** |
| 4 | Freerouting Reference Manual, "Routing Options". `https://freerouting.org/freerouting/manual/routing-options` | **inspected** for the Autoroute/Route Parameter and Net Class clauses relied on | first attempt → 200, 21 214 B | raw text (live DOM) | validated (self-validating DOM) | **yes** |
| 5 | Freerouting `docs/command_line_arguments.md`, master. `https://raw.githubusercontent.com/freerouting/freerouting/master/docs/command_line_arguments.md` | **partial** — router/layer settings sections | first attempt → 200 | raw text | validated | **yes** |
| 6 | Freerouting `RouterSettings.java` / `ScoringSettings.java` / `LayerSettings.java`, master | **partial** — `applyBoardSpecificOptimizations` and the scoring field set | first attempt → 200 each | raw text | validated | **yes** |
| 7 | KiCadRoutingTools, local checkout `~/dev/tools/KiCadRoutingTools`, detached HEAD at `749cfa83` (v0.21.3), origin `github.com/drandyhaas/KiCadRoutingTools` | **inspected** for `routing_defaults.py`, `docs/routing-architecture.md` cost function, `docs/api-routing-config.md`, `rust_router/README.md`, commit `aaa60331` | local repository read (not a network fetch) | raw text | validated (constants cross-checked against both the Rust signature defaults and the Python defaults module) | **yes** |
| 8 | UCSD VLSI CAD, *TritonRoute-WXL*, IEEE TCAD, `https://vlsicad.ucsd.edu/Publications/Journals/j136.pdf`, DOI 10.1109/TCAD.2021.3079268 | **partial** — the net-ordering sentence and its surrounding paragraph | first attempt → 200, 1 160 061 B, `%PDF` | raw text | validated | yes |
| 9 | Chen, J. et al., *A Novel Global Routing Algorithm for Printed Circuit Boards Based on Triangular Grid*, Electronics 12(24):4942, 2023 | **partial** — abstract and framing only; nothing load-bearing drawn | first attempt → 200, 53 770 B | raw text (live DOM) | validated | no |
| 10 | KiCad 9.0 PCB Editor documentation, `https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html` | **partial** — "Routing Tracks", interactive-router modes and settings | first attempt → 200, 461 474 B | raw text (live DOM) | validated | **yes** |
| 11 | Analog Devices AN-1142, *Techniques for High Speed ADC PCB Layout* (Rob Reeder). `https://www.analog.com/en/resources/app-notes/an-1142.html` | **inspected** (full article text) | curl+UA → HTTP/2 INTERNAL_ERROR; Playwright request context on the `/media/…/AN-1142.pdf` path → 301 to the HTML page, then timeout; `failover-www` host → NXDOMAIN; headed CDP Chrome on the HTML page → 200. **Version note:** the PDF manifestation now redirects to HTML; the HTML page is the current manifestation | raw text (live DOM) | validated | yes |
| 12 | Analog Devices MT-031, *Grounding Data Converters and Solving the Mystery of "AGND" and "DGND"*. `https://www.analog.com/media/en/training-seminars/tutorials/MT-031.pdf` | **metadata only** — retrieved and extracted, not relied on in this file | Playwright request context → 200, 148 151 B, `%PDF` | raw text | validated | no |
| 13 | TI SLYT512, *Grounding in mixed-signal systems demystified, Part 2*. `https://www.ti.com/lit/pdf/slyt512` | **metadata only** — retrieved, not relied on here | first attempt → 200, 122 800 B, `%PDF` | raw text | validated | no |
| 14 | Xilinx UG483, *7 Series FPGAs PCB Design Guide* | **metadata only** — identity verified, content not relied on | `xilinx.com/content/dam/…` → 200 but HTML (2 575 B), i.e. not the PDF; mirror `0x04.net/~mwk/xidocs/ug/xc7-pcb.pdf` → 200, 3 043 907 B, `%PDF`, self-identifying as **UG483 (v1.13) 2017-08-18**; publisher landing page `docs.amd.com/v/u/en-US/ug483_7Series_PCB` confirms the current revision is **v1.14, 2019-05-21**. Version divergence recorded; nothing load-bearing drawn from the stale mirror | raw text | validated (identity block read) | no |
| 15 | Chen, T. et al., *TRouter: Thermal-Driven PCB Routing*, IEEE TCAD 42(10), 2023 | **metadata only** | first attempt → 200, 5 274 220 B, `%PDF` | raw text | validated | no |
| 16 | Sun, N. et al., *3D LineExplore*, PMC12913939 | **metadata only** | first attempt → 203, 61 003 B | raw text (live DOM) | validated | no |
| 17 | Röder, S. G., *Minimizing Switches in Cased Graph Drawings*, TU Wien, 2025 | **metadata only** — retrieved for the via-minimisation framing, not relied on | first attempt → 200, 6 865 116 B, `%PDF` | raw text | validated | no |
| 18 | Sarrafzadeh & Wong, *An Introduction to VLSI Physical Design*, McGraw-Hill 1996, §8.1; Sherwani, *Algorithms for VLSI Physical Design Automation*, Springer | **second-hand** — CVM/UVM formulation taken from quoting secondary pages and the Springer front-matter TOC; the chapters themselves were not obtained | Springer front-matter PDF listed in search results; no full-text access attempted beyond it (licensed monographs) | route: none for the chapters | n/a | no — §2 F7 is explicitly marked non-load-bearing |
| 19 | IPC-2221A TOC, `https://www.electronics.org/TOC/IPC-2221A.pdf` | **de-scoped** — the retrieved file is an 8-page table of contents, not the standard; IPC-2221 is licensed and nothing here rests on it | curl+UA → 200, 50 615 B, `%PDF`, 8 pages | raw text | validated (identified as TOC) | no |
| 20 | Cadence, *PCB Manhattan Routing Techniques*, `https://resources.pcb.cadence.com/blog/2020-pcb-manhattan-routing-techniques` | **de-scoped** — the URL renders a client-side article index, not the article; nothing depends on it, because per-layer preferred direction is carried by rows 4–7, which are stronger (they give the mechanism and the numbers) | headless Chrome → 200 but 1 796 B index shell; headed CDP Chrome → 200, identical 1 796 B shell | raw text (live DOM) | validated as *not* the requested resource | no |
| 21 | TI SLOA089, *Circuit Board Layout Techniques* | **de-scoped** — `ti.com/lit/an/sloa089/sloa089.pdf` and `ti.com/lit/pdf/sloa089` both 404 (the first was a URL I composed — a rung-0 provenance error, corrected by search, which found only third-party mirrors of the parent book). Nothing here depends on it | two 404s, both content-validated as TI error pages | route: none | n/a | no |

Rows 12, 13, 15, 16, 17 were retrieved and remain in the working corpus at
`/private/tmp/claude-503/…/scratchpad/research/`; they are recorded as metadata-only because
no claim in this file draws on them. No row is left `retrieval pending`, and no `inaccessible`
verdict is claimed anywhere — the two genuinely unresolved sources (rows 20, 21) are de-scoped
with the dependency argument stated.

**Archive:** `archive unavailable` — no user-authorized literature archive exists for this skill
repository, so the PDFs stay in session working storage and are cited by stable URL above.
