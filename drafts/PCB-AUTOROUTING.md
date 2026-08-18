# PCB trace autorouting in ECAD

Research snapshot: 2026-08-18.

This document covers two independent routing axes. **Automation scope** ranges from
interactive and sketch-guided routing through net-class, region, and full-board automation.
**Algorithm family** includes classical search/heuristic routers, learned policies, and
hybrid or generative systems. All of them may remain constrained by ECAD design rules, so
"rule-based" is not a precise synonym for classical routing. This is an evidence-backed
reference, not an instruction to autoroute every board.

## Contents

- [Conclusion](#conclusion)
- [The routing problem](#the-routing-problem)
- [Representative routing pipeline](#representative-routing-pipeline)
- [What current ECAD tools offer](#what-current-ecad-tools-offer)
- [What the evidence says](#what-the-evidence-says)
- [Recommended workflow](#recommended-workflow)
- [Routing decision guide](#routing-decision-guide)
- [Evaluation and signoff](#evaluation-and-signoff)
- [Conflicts and dependencies](#conflicts-and-dependencies)
- [What would discriminate](#what-would-discriminate)
- [Sources and access record](#sources-and-access-record)

## Conclusion

Treat autorouting as constraint-driven optimization and partial automation, not as
push-button PCB signoff. The most reliable default is a hybrid workflow:

1. Establish placement, stackup, fabrication limits, keepouts, and electrical rules.
2. Manually route and lock topology-sensitive nets.
3. Use auto-interactive, sketch, or full autorouting on suitable net classes or regions.
4. Inspect the result and change placement or constraints when routing quality is poor.
5. Verify more than connectivity and DRC: return paths, SI/PI, EMI, thermal behavior,
   current density, isolation, and manufacturability remain designer responsibilities.

Across the methods evaluated on the related PCBench and PCBWorld corpora, classical
search/heuristic Freerouting is the strongest demonstrated general baseline on the larger or
more diverse cases. Broader-router and production-board evidence remains limited.
Learning-based routing has become competitive on some small-board benchmarks, but current
evidence does not support treating it as a general replacement for an experienced layout
engineer.

## The routing problem

Multilayer PCB routing can be modeled as constrained multi-objective search: connect every
net with copper geometry while respecting obstacles, clearance, layer, via, and bend rules,
and minimize costs such as total wirelength and via count. This formulation is direct from
He 2024, section 3.4, printed page 48 (PDF page 57) [S1].

The objective is incomplete if it contains only length and vias. A manufacturable board may
also need controlled impedance, pair or bus skew, current-carrying capacity, continuous
reference planes, acceptable coupling, thermal performance, test access, isolation, and
assembly constraints. Many of these are expensive to evaluate inside a routing loop, and
several are absent from current academic benchmarks [S2, section 7, "Evaluation realism"].

## Representative routing pipeline

No single algorithm implements every router, but a representative flow is:

```text
Placement + stackup + netlist + design rules
                       |
                       v
             Pad escape / fan-out
                       |
                       v
       Global/topological path planning
           including layers and vias
                       |
                       v
      Detailed geometric track placement
          A*, push-and-shove, etc.
                       |
                       v
       Conflict detection and rip-up and reroute
                       |
                       v
       Straighten, spread, shorten, tune
                       |
                       v
        DRC + electrical/physical review
```

### Escape and area routing

Traditional PCB routing often separates:

- **Escape routing**: carry pads, especially dense BGA pins, to component boundaries.
- **Area routing**: connect escaped nodes between components while meeting topology and
  length requirements.

The stages can be poorly coupled: a locally successful escape pattern can make later area
routing impossible. Sequentially routing locally shortest paths can likewise block later
nets. He 2024 describes both failure modes in sections 1.1.3 and 1.2, printed pages 5-6
(PDF pages 14-15) [S1].

### Global and detailed routing

A global router selects approximate corridors, partitions, layers, and via transitions.
A detailed router turns those decisions into legal track geometry.

He 2024 demonstrates one implementation using polygon partitions, MCTS global paths,
A*-based detailed routing, dynamic repartitioning, rip-up and reroute, and post-processing
(sections 3.5.1-3.5.7) [S1]. Altium Situs instead documents topological path construction
followed by push-and-shove and successive fan-out, completion, conflict-cost, cleanup,
spread, and straightening passes [S3, "Routing Passes"].

### Rip-up and reroute

Rip-up and reroute is a core routing mechanism, not evidence that a router malfunctioned.
An early trace can be locally optimal and globally harmful. Effective routers remove or
re-cost conflicting traces, change net order or layer choices, and try another solution.
Altium's "Globally Optimised Main" pass increases conflict costs across iterations [S3];
He 2024 uses a separate tree-search rip-up-and-reroute phase [S1, section 3.5.1].

## What current ECAD tools offer

| Tool or workflow | Documented routing model | Practical interpretation |
|---|---|---|
| **Altium Designer Situs** | Integrated topological full-board autorouter with fan-out, push/shove, conflict rerouting, and cleanup passes | Mature classic autorouting. Placement and rule setup are load-bearing. Situs documentation says to pre-route critical nets and manually route and lock differential pairs before running it [S3]. |
| **KiCad 9** | Built-in interactive/semi-automatic router with push-and-shove, walk-around, collision highlighting, differential-pair routing, and length/skew tuning | The documented built-in workflow is guided routing. KiCad exports Specctra DSN and imports SES sessions for external full-board routers [S4]. |
| **Freerouting 2.3.0** | Open-source external autorouter using DSN input and SES output; GUI, CLI, local/API workflows | Practical external option for KiCad and other DSN-capable ECADs. The v2.3.0 notes keep DSN as the recommended production KiCad path; JSON/API integration is experimental [S8, S9]. |
| **Siemens Xpedition** | Interactive, automatic, and sketch-guided routing | A useful industry taxonomy: let the designer select how much topological control to retain [S5]. |
| **Cadence Allegro X AI** | Cloud search/generative flow covering placement, via planning, per-layer detailed routing, analysis, metal pour, and critical-net routing | Cadence markets the technology for small-to-medium boards. Treat its performance numbers as vendor claims until reproduced on representative boards [S6, S7]. |

### Placement and constraints dominate

Altium identifies component placement as having the greatest impact on routing performance.
Short, untangled connection lines, usable pin grids, appropriate layer directions, and
routable access to every pad make a larger difference than repeatedly changing routing
strategies [S3, "Component Placement" and "Layer Directions"].

Width, clearance, via style, permitted layers, net priority, fan-out controls, and rule
priority must be scoped correctly. Overly broad, contradictory, or unnecessary rules can
degrade route quality and runtime; rules that are too permissive may instead produce a route
that is geometrically legal but electrically unacceptable [S3, "Configuring the Design
Rules"].

Run DRC and inspect the autorouter's setup report before routing. Existing violations can
make an area impossible to route and can waste time as the router repeatedly explores it
[S3, "The Golden Rule"].

## What the evidence says

### Classical search/heuristic autorouting is useful, but not universal

Blanket statements that autorouting is useless are not supported by the available
benchmarks. In He 2024's PCBench evaluation, Freerouting completed 72.5% of 1,182 curated
boards [S1, section 4.7 and Table 4.2, printed pages 78-80].

Quantitative evidence tag:

- origin: measured experiment;
- transformation: direct table value;
- status: empirical sample result, not a guaranteed capability;
- conditions: real open-source boards after PCBench preprocessing, router defaults,
  single-core evaluation, and a geometric routing objective rather than full electrical
  signoff; the Freerouting release used is not identified in S1's evaluation section.

The unreviewed PCBWorld 2026 arXiv v1 preprint reported Freerouting 2.1.0 clean-pass means of
0.80 on 99 small real-board cases and 0.78 on ten medium cases [S2, Tables 3 and 20,
section 5.3, and Appendix K.5]. The reported values are four-seed means under PCBWorld's @5
selection protocol, not deterministic single-pass fractions; Table 20 reports 0.80 +/- 0.01
and 0.78 +/- 0.05. A clean pass required full connectivity and zero KiCad error-level DRC
violations. Those results show useful routine routing by the evaluated Freerouting 2.1.0
configuration, not measured performance of the current 2.3.0 release. They also do not prove
production readiness because the benchmark omitted SI, EMI, thermal, and other industrial
objectives.

### Learning-based routing is improving but size-limited

He 2024's experimental RL-MCTS router completed only 2 of 1,182 boards, while Freerouting's
success rate was 72.5% [S1, Tables 4.1-4.2]. That result applies to one model and routing
formulation; it is not evidence that all learning-based routing must fail.

The same unreviewed PCBWorld 2026 arXiv v1 preprint used segment-level KiCad engine operations
and continuous DRC feedback. Its PPO policy achieved a 0.86 clean-pass rate versus
Freerouting 2.1.0's 0.80 on the 99 small cases, but fell to 0.45 versus Freerouting's 0.78 on
the ten medium cases. All evaluated LLM agents had zero clean passes on those medium cases
[S2, Table 3 and section 5.3].

Quantitative evidence tag:

- origin: measured experiment;
- transformation: direct table values;
- status: empirical benchmark results;
- conditions: PCBWorld's @5 selection protocol and four-seed means for stochastic methods;
  Freerouting 2.1.0; RL trained on small synthetic boards; D3-A contained 99 small real-board
  cases and D3-B only ten medium cases; scoring covered KiCad connectivity/DRC, wirelength,
  vias, and time, not full electrical performance.

The newer results are consistent with two distinct comparisons within S2: segment-level
KiCad engine actions scaled better than cell-by-cell actions as grid resolution increased
(section 5.2), and closed-loop engine feedback outperformed open-loop LLM routing
(section 5.4). Because S1 and S2 used different methods, data selections, and protocols, they
do not establish that either choice caused the cross-paper performance difference.

### Vendor AI claims need separate treatment

Cadence states that Allegro X AI can reduce placement-and-routing turnaround by 10x or more
for its stated scope [S7]. Tag that figure as:

- origin: vendor assertion;
- transformation: direct press-release claim;
- status: marketing/performance claim, not an independently reproduced limit;
- conditions: small-to-medium designs and Cadence's unspecified customer boards and compute.

The architecture is technically relevant: Cadence documents separating via placement from
per-layer route planning so detailed layers can run in parallel, then evaluating alternatives
with routing, SI/PI, thermal, and other score functions [S6, "Via and Routing" and "Allegro X
AI Overview"]. Do not silently convert that architectural description into proof of the
turnaround claim.

## Recommended workflow

### 1. Prepare the board

- Confirm layer count, stackup, plane strategy, fabrication capabilities, and via types.
- Complete mechanically constrained placement and critical component grouping.
- Define keepouts, isolation barriers, high-voltage regions, and antenna/no-copper areas.
- Check that every pad is physically escapable at the selected width and clearance.
- Define net classes and rule priority before routing.

### 2. Encode design intent

At minimum encode:

- track width and clearance by net class;
- permitted and preferred layers;
- via type, size, drill, and transition restrictions;
- differential-pair geometry where the selected router supports it;
- length, skew, topology, or tuning targets where applicable;
- net or class routing priority;
- pad-entry, neck-down, and fan-out constraints;
- locked pre-routes and regions the router must not modify.

Do not expect a router to infer a return-current strategy, star point, Kelvin connection,
isolation boundary, sensitive-node guard, or intentional current loop from a netlist.

### 3. Pre-route topology-sensitive nets

Manually route and lock nets whose geometry carries electrical intent, including as
applicable:

- clocks and other edge-rate-sensitive nets;
- differential pairs when the selected full-board router cannot preserve their intent;
- controlled-impedance and tightly coupled buses;
- RF and antenna feeds;
- sensitive analog inputs, references, and guard structures;
- switching-current loops;
- high-current copper and unusual power shapes;
- isolation-boundary crossings;
- Kelvin sense connections and intentional star points.

This list is an engineering inference from the documented router constraints and the
electrical objectives omitted by geometric benchmarks. It is not a claim that every modern
router lacks every listed feature.

### 4. Increase automation scope gradually

Prefer this progression:

1. fan-out one component or package family;
2. route one noncritical net or class;
3. route one bus, corridor, or board region;
4. inspect completion, vias, path shape, return planes, and congestion;
5. only then route a larger class or the remainder of the board.

If the result is poor, first change placement, rule scope, routing priority, layer direction,
or locked geometry. Repeatedly running the same router against the same impossible search
space is not optimization.

### 5. Review and sign off

After importing or accepting the route:

- run connectivity and complete DRC with the intended severity map;
- inspect every automatically introduced via and neck-down on critical or current-carrying
  nets;
- check return-plane continuity, split-plane crossings, stubs, loops, and layer transitions;
- run applicable impedance, timing, SI/PI, crosstalk, EMI, thermal, and current-density
  analyses;
- inspect copper-to-edge, copper-to-slot, creepage, and isolation geometry independently;
- verify fabrication, assembly, test, and repair access;
- render all copper layers and inspect them rather than trusting completion percentage.

## Routing decision guide

| Board or net type | Suggested automation level | Reason |
|---|---|---|
| Roomy, low-speed, two-to-four-layer board with clean rules | Full autorouting can be a productive first pass | Geometric objectives dominate and manual cleanup is bounded. |
| Repetitive variants with stable placement and mature rules | Strong candidate for batch or scripted routing experiments | Constraint and placement setup can be amortized across variants. |
| Dense digital board or BGA fan-out | Hybrid: automated fan-out and routine nets, manual topology for critical groups | Router search is valuable, but net ordering, layer use, escape geometry, and return paths need review. |
| High-speed buses, clocks, RF, precision analog, power conversion, or high current | Route and lock critical structures; automate the low-risk remainder | Connectivity/DRC metrics do not capture the dominant electrical risks. |
| Board with unresolved placement or DRC problems | Do not autoroute yet | The router will optimize the wrong or infeasible problem. |

For precision or power-metering boards, manually control isolation/creepage areas,
current-carrying paths, shunt and Kelvin connections, ADC/reference routing, switched-current
loops, and any clock or synchronization path. Ordinary control, GPIO, and noncritical digital
communication nets are better candidates after the board's constraints and keepouts are
complete.

## Evaluation and signoff

### Useful routing metrics

- percent of connections completed;
- number and severity of DRC violations;
- total and critical-net wirelength;
- via count and layer transitions;
- runtime and engineer cleanup time;
- length/skew compliance;
- congestion and remaining routability;
- changed or damaged locked routes;
- number of rule or geometry exceptions introduced.

### Metrics that do not prove production readiness

Neither 100% completion nor zero geometric DRC proves:

- controlled impedance under the actual stackup;
- continuous high-frequency return paths;
- acceptable coupling or emissions;
- power integrity or current density;
- thermal margin;
- adequate creepage or surface-leakage behavior;
- a solderable land pattern or robust assembly;
- testability and serviceability.

Judge a router by time to a reviewed, electrically acceptable, fabrication-ready board, not
by time to an empty ratsnest.

## Conflicts and dependencies

- **Shared benchmark ancestry:** PCBWorld's real boards derive from PCBench. He 2024 and
  PCBWorld 2026 are not independent corroboration of performance on unrelated data [S1, S2].
- **Different methods and protocols:** The 2024 RL-MCTS and 2026 PPO results use different
  action spaces, training, datasets, and success definitions. Do not read the difference as a
  clean time-series improvement.
- **Small medium-board sample:** PCBWorld's reported D3-B comparison used only ten boards.
  Treat 0.78 versus 0.45 as a useful result, not a universal performance ratio.
- **Benchmark scope:** The academic evaluations score geometric connectivity and DRC, not the
  full SI/PI/EMI/thermal/manufacturing problem.
- **Vendor dependency:** Altium, Siemens, and Cadence documentation establishes product
  behavior and recommended workflow, but not independent comparative quality.
- **Tool-specific restrictions:** Altium's instruction to manually route differential pairs
  applies to Situs. Do not generalize it to every ECAD router.
- **Documented interchange gaps:** Freerouting 2.3.0 documents that KiCad's DSN export omits
  copper-to-edge clearance and that the experimental JSON/API path does not yet import some
  design-rule fields fully [S9]. Do not infer a broader SES reinterpretation claim from that
  evidence. Re-run KiCad DRC after every import and independently audit any load-bearing
  geometry.

## What would discriminate

The most useful evidence for adopting a router is an A/B evaluation on representative local
boards:

1. Freeze placement, stackup, net classes, and design rules.
2. Preserve one manual or previously released routing as the baseline.
3. Run the candidate router with a recorded version, configuration, seed, and time budget.
4. Measure completion, DRC, wirelength, vias, critical-net geometry, and engineer cleanup
   time.
5. Run the same SI/PI, isolation, thermal, fabrication, and visual reviews on both results.
6. Compare total time to signoff and defect count, not router runtime alone.

For proprietary AI claims, an independently repeatable netlist-to-signoff comparison on
production-class boards, with placement rules, compute cost, manual interventions, and all
electrical analyses disclosed, would settle whether the advertised turnaround applies to the
target design class.

## Sources and access record

All sources were retrieved on 2026-08-18. No source text below came from a search-result
snippet.

### S1 - He 2024 dissertation

- Stable identifier: <https://doi.org/10.31274/td-20240617-74>
- PDF: <https://dr.lib.iastate.edu/server/api/core/bitstreams/baa06fe6-541d-4f4a-888d-94f3083cd518/content>
- Identity: Youbiao He, *Towards automated PCB routing: Leveraging machine learning and
  heuristic techniques*, Iowa State University, 2024.
- Evidence state: inspected; title, sections 1.1.3, 1.2, 3.3-3.5, 4.7-4.8, and cited tables.
- Attempt history: DOI resolved to Iowa State; publisher REST metadata and PDF succeeded;
  DataCite lookup returned 404, but DOI resolution and publisher metadata matched.
- Route: raw PDF text.
- Validation: rendered title and PDF pages 14-15, 57-58, and 88-89.
- Temporary PDF SHA-256:
  `faf907d39c359bc1eb32ad41d6983567f7432260dd0ae10e7f0110fba62f3081`.
- Load-bearing: yes.

### S2 - PCBWorld 2026

- Stable version: <https://arxiv.org/html/2607.05915v1>
- Identity: Hyungseok Song et al., *PCBWorld: A Benchmark Environment for Engine-Grounded
  PCB Design Automation*, arXiv:2607.05915v1, 2026-07-07.
- Evidence state: inspected; abstract, sections 1, 4.1-4.3, 5.1-5.5, 7, and Appendix K.5.
- Attempt history: first HTML fetch succeeded; Crossref title lookup produced no exact DOI
  match.
- Route: raw HTML.
- Validation: live versioned HTML.
- Load-bearing: yes.

### S3 - Altium Situs documentation

- URL: <https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter>
- Version marker: page updated 2026-03-29.
- Evidence state: inspected; Board Setup, Pre-routing, Configuring Design Rules, The Golden
  Rule, Routing Strategies, and Routing Passes.
- Attempt history: first fetch succeeded.
- Route: raw HTML.
- Validation: live page.
- Load-bearing: yes.

### S4 - KiCad 9 PCB Editor documentation

- URL: <https://docs.kicad.org/9.0/en/pcbnew/pcbnew.html>
- Evidence state: inspected; Routing Tracks, Interactive Router Settings, Specctra DSN
  exporter, and Specctra session import.
- Attempt history: first fetch succeeded.
- Route: raw HTML.
- Validation: live page.
- Load-bearing: yes.

### S5 - Siemens Xpedition design automation

- URL: <https://www.siemens.com/en-us/products/pcb/engineering-productivity-and-efficiency/design-automation/>
- Evidence state: inspected; routing FAQ and key features.
- Attempt history: first fetch succeeded.
- Route: raw HTML.
- Validation: live page.
- Load-bearing: yes.

### S6 - Cadence Allegro X AI white paper

- URL: <https://www.cadence.com/en_US/home/resources/white-papers/allegro-x-ai-for-generative-system-design-wp.html>
- Version marker: page publication metadata 2025-02-20.
- Evidence state: inspected; Problem Statement, Via and Routing, Metal Pour, Allegro X AI
  Overview, and Conclusion.
- Attempt history: corrected-UA request returned a Cloudflare challenge; coherent browser
  fetch headers returned the real 199,645-byte HTML page.
- Route: raw HTML.
- Validation: live page.
- Load-bearing: yes.

### S7 - Cadence Allegro X AI announcement

- URL: <https://www.cadence.com/en_US/home/company/newsroom/press-releases/pr/2023/cadence-introduces-allegro-x-ai-accelerating-pcb-design-with.html>
- Date: 2023-04-06.
- Evidence state: inspected; full announcement and product scope.
- Attempt history: first metadata-header fetch succeeded.
- Route: raw HTML.
- Validation: live page.
- Load-bearing: yes; performance remains a vendor assertion.

### S8 - Freerouting README

- Stable version: <https://github.com/freerouting/freerouting/blob/a562fa5a7ec020f5116f18b07dc7f08518d90bef/README.md>
- Commit: `a562fa5a7ec020f5116f18b07dc7f08518d90bef`, 2026-08-11.
- Evidence state: inspected; Introduction, GUI, CLI, API, and integrations.
- Attempt history: raw GitHub content and commit API succeeded.
- Route: raw Markdown and JSON metadata.
- Validation: live GitHub content.
- Load-bearing: yes.

### S9 - Freerouting 2.3.0 release

- URL: <https://github.com/freerouting/freerouting/releases/tag/v2.3.0>
- Date: 2026-08-07.
- Evidence state: inspected; Highlights, Routing Engine, KiCad Integration, and Known
  Limitations.
- Attempt history: GitHub release API succeeded.
- Route: raw JSON/Markdown.
- Validation: live release page.
- Load-bearing: yes.

### Search record

Discovery searches covered official KiCad, Altium, Cadence, and Siemens documentation;
Freerouting documentation and releases; PCB global/detailed routing, A*, topological routing,
and rip-up and reroute.

Challenge searches covered both conclusion clusters:

- against "autorouting is always bad": searched for PCB autorouter limitations, differential
  pair restrictions, manual-versus-auto routing, and independent router benchmarks; the
  inspected benchmarks show useful classic-router completion on routine boards but do not
  establish electrical signoff;
- against "AI has solved PCB routing": searched for AI autorouting limitations, independent
  Allegro X AI benchmarks, learning-based PCB routing benchmarks, and engine-grounded routing;
  the inspected 2024 and 2026 primaries show substantial progress on small boards and a
  continuing large-board gap.

A bounded public, English-language search for an independent reproduction of Cadence's
turnaround claim used `Cadence Allegro X AI PCB routing independent benchmark limitations`
and follow-up searches through vendor and academic sources. No inspected apples-to-apples
production benchmark was found in that scope. Confidence in the absence is medium because
commercial customer evaluations may be private.
