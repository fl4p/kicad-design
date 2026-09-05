# Codex xhigh verification pass — POWER.md/LOOPS.md correction pass (2026-09-04)

Verdict: **do-not-publish** — the correction pass introduced a false denial contradicted by its own cited onsemi primary. 7 findings; A1-A9 correction checks otherwise all passed.

## ACCESS

- Live query: `/Users/fab/bin/serper-search --json 'site:onsemi.com AND9410 gate inductance higher than 20 nH shoot through'` returned the official [onsemi AND9410/D PDF](https://www.onsemi.com/pub/Collateral/AND9410-D.PDF) first. I did not rely on the search snippet; I inspected the primary.
- External page fetched through the attached Playwright browser and validated from its rendered DOM: `<title>"DC-DC buck converter EMI reduction using PCB layout modification" by Ankit Bhargava, David Pommerenke et al.</title>` at [Missouri S&T Scholars’ Mine](https://scholarsmine.mst.edu/ele_comeng_facwork/2587/).
- The default in-app browser binding reported no active browser; the attached Playwright surface passed live-navigation/DOM proof and was used instead.
- No claim remains `unverified`. A legacy IEEE URL for Fessler/Whites/Paul returned HTTP 418, but that work was de-scoped because Purdue’s authoritative treatment and Bhargava’s primary supplied the needed evidence.
- No target or out-of-scope repository file was modified. Browser-generated temporary repository artifacts were removed.

## CORRECTION CHECKS

### A1 — Image theory

The corrected signs are right.

For a PEC, `n̂·B = 0`. Introduce fictitious magnetic charge:

1. A magnetic monopole above the plane needs a same-sign image so the normal fields cancel at the boundary.
2. For a tangential magnetic dipole, reflection preserves the pole ordering along the plane, so `m′ = m`: parallel image, reinforcement.
3. For a normal dipole, reflection reverses the pole ordering, so `m′ = −m`: antiparallel image, cancellation.
4. Electric charge has an opposite-sign image, so electric-dipole/current-element rules reverse: tangential electric current images antiparallel, normal current images parallel.

This agrees exactly with Chew, Chapter 32 §32.2.2, printed pp. 440–441 of the official [Purdue electromagnetic-field notes](https://engineering.purdue.edu/wcchew/ece604f23/EMFTEDX032124R.pdf).

P1 needs a modeling caveat, but its qualitative direction remains correct. Because its return current is in the plane, the image is an equivalent-source construction—not another physical loop to add after separately solving the plane current. In the limiting vertical half-loop construction, the return segment and its mirror cancel on the boundary while the two tangential loop moments reinforce. Thus “P1’s image adds” is defensible as an upper-half-space equivalence, but the text should explicitly prevent double counting. See finding 5.

### A2–A9

| Check | Result |
|---|---|
| **A2 P1/P2/P3** | Correct. Bhargava Table I gives **2.50 / 2.41 / 2.70 nH** and **4.5 / 36 / 4.4 mm²** for P1/P2/P3. The inductance spread is `(2.70−2.41)/2.41 = 12.0%`. [POWER.md:230](POWER.md:230), [LOOPS.md:17](LOOPS.md:17). |
| **A3 Qrr** | Correct. Infineon specifies **100 A/µs** for `Q_rr` characterization and **1300 A/µs** maximum commutation speed: 13× faster. With 120 V, `120/0.1 = 1200 nH`; `120/1.3 = 92.3 nH`. [POWER.md:188](POWER.md:188). |
| **A4 voltage budget** | The circularity warning is correct; `1.5` now unambiguously divides; and `V/(A/s)=H`. It is physically sound subject to the allocation convention in finding 6. [POWER.md:178](POWER.md:178). |
| **A5 critical damping** | Correct series-RLC result: `Rcrit=2√(L/C)`, hence `L=R²C/4`; `5²·1 nF/4 = 6.25 nH`. Driver pull-up/down impedance, device/package resistance, trace resistance, transition asymmetry, actual-bias capacitance and the illustrative qualifier are now present. [POWER.md:270](POWER.md:270). The adjacent 20 nH statement is nevertheless wrong—finding 1. |
| **A6 dielectric sweep** | Correct direction and denominator: `(1.17−0.67)/1.17 = 42.7%`, or about **43% reduction from 15 to 5 mil**. Correctly tagged simulated. [LOOPS.md:137](LOOPS.md:137). |
| **A7 capacitor mounting** | Correct. `23.4/6.8 = 3.44×`; aligned 0805→0402 saves `(23.4−21.4)/23.4 = 8.55%`; doublet stays `6.8→6.8`, or 0%. [POWER.md:311](POWER.md:311). |
| **A8 skin depth** | Correct under nominal room-temperature copper assumptions. With `ρ=1.68×10⁻⁸ Ωm`, `δ=20.63 µm` at 10 MHz and `9.23 µm` at 50 MHz. Nominal 1 oz copper, 34.8 µm, is **1.69 skin depths** at 10 MHz. [LOOPS.md:145](LOOPS.md:145). |
| **A9 Fig. 11** | Correctly repaired. Fig. 11 contains valid maximized chamber measurements, but not a controlled equal-excitation comparison. Table III’s measured field/current transfer functions provide the approximately **9.75/9.50 dB** arrangement comparison. [LOOPS.md:29](LOOPS.md:29), [LOOPS.md:34](LOOPS.md:34). |

## CROSS-REFERENCE, ROUTING, AND LICENSE AUDIT

- Every local Markdown link in the reviewed files resolves.
- PCB.md’s “loop catalog per POWER.md” forward reference is satisfied by the four-row catalog at [POWER.md:168](POWER.md:168). See [PCB.md:150](PCB.md:150).
- No harmful POWER↔LOOPS circularity or internal contradiction was found apart from their shared onsemi error.
- [SKILL.md:52](SKILL.md:52) has the important “deciding whether a published result applies” trigger. [README.md:26](README.md:26) is shorter, but not materially misleading.
- Nothing exclusively retained in LOOPS is required to execute an ordinary qualitative placement rule: POWER includes the regime split and actionable summaries, while PCB.md makes numeric budget derivation—and therefore loading LOOPS—mandatory for hard-switched loops. The routing defect is instead that several load-bearing rules disappeared from both files.
- The THERMALS link is a domain handoff, not an IPC-2152 derivation; LOOPS correctly tells the reader to obtain IPC-2152 when that claim is load-bearing.
- No IEC 60664-1 clause/table values or recognizable licensed-standard prose entered POWER.md, LOOPS.md, or the router rows. Zhao’s isolated numeric results were paraphrased, not copied as expressive table content.

## CHALLENGE-SEARCH RECORD

- `site:onsemi.com AND9410 gate inductance higher than 20 nH shoot through` directly refuted the correction’s denial; the official primary was inspected.
- `magnetic dipole image PEC plane normal tangential opposite parallel authoritative notes` produced weaker results; the conclusion instead rests on the independently located Purdue notes and first-principles derivation.
- `power loop return plane magnetic dipole image P1 P2 PCB EMI Bhargava` found no authoritative refutation. Bhargava’s primary and the PEC equivalence construction control.
- Exact-title search for Fessler/Whites/Paul found a legacy IEEE PDF URL; direct retrieval returned HTTP 418. No conclusion depends on it.

## SOURCE ACCESS LOG

| Source | Access and validation |
|---|---|
| POWER.md, LOOPS.md, SKILL.md, README.md | Direct working-tree diff/full-text inspection. |
| PCB.md, THERMALS.md | Read only for cross-references and domain routing. |
| Prior 18-finding review | Read completely; treated as a challenge list, not evidence. |
| RESEARCH-smps-pcb-design.md | Relevant evidence, caveat and prior-content sections inspected with exact lines. |
| Bhargava et al. 2011 | Supplied PDF; Table I, Table III, Fig. 11 and surrounding prose checked by extraction and rendered-page inspection. |
| Chew, *Electromagnetic Field Theory* | Live-search discovery, browser MIME validation, official Purdue PDF; §32.2.2 inspected. |
| onsemi AND9410/D | Supplied PDF plus official live-search result; threshold prose and gate-loop tables inspected. |
| Infineon IPW65R060CFD7 | Supplied PDF; maximum-ratings and `Q_rr` tables inspected. |
| Wolfspeed PRD-08710 | Supplied PDF; Fig. 1 values checked and implied inductances recomputed. |
| Wolfspeed PRD-06752 | Supplied PDF; gate-RLC model and capacitance treatment inspected. |
| Reusch 2012 dissertation | Supplied PDF; Fig. 4.26 rendered and checked. |
| Zhao et al. 2020 | Supplied PDF; Table II rendered and checked; DOI `10.1109/MEMC.2020.9241560` confirmed. |
| ADI RAQ 207, TI SLPA009A, TDK MLCC guide | Supplied text/PDF inspected for FastHenry, CSI/package and anti-resonance claims. |
| Rizzo et al. 2024 | Supplied full text inspected; DOI `10.3390/electronics13204051` confirmed. |
| Chen et al. 2024 | Supplied article checked for source-list identity/scope; not load-bearing to A1–A9. |
| Fessler/Whites/Paul 1996 | Legacy IEEE URL returned 418; de-scoped because substituted authoritative evidence fully resolves A1. |

No archive was created; the supplied corpus was reused and temporary retrievals remained outside the repository.

## FINDINGS

1. **(c) Real design defect — the correction now expressly contradicts its cited onsemi primary.**  
   [POWER.md:276](POWER.md:276) and [LOOPS.md:115](LOOPS.md:115) say no source states a 20 nH shoot-through threshold. AND9410/D says: “Gate inductance higher than 20 nH could cause shoot through problem.” [Official primary](https://www.onsemi.com/pub/Collateral/AND9410-D.PDF).

   The accurate doctrine is: onsemi **asserts** that >20 nH could cause shoot-through; it does not report a measured threshold or observed onset. The word “easily” belongs to a later generic statement about unoptimized layouts, not to the >20 nH sentence. This is a fresh regression introduced by accepting the prior reviewer’s mistaken source reading.

2. **(c) Real design defect — load-bearing gate-drive material was deleted rather than moved.**  
   The prior review requested that detailed gate evidence be moved to the new companion, not silently discarded: [archived review:143](/Users/fab/.claude/skills/kicad-design/reviews/2026-09-04-codex-xhigh-power-loop-doctrine.md:143). The current union, particularly [POWER.md:263](POWER.md:263) and [LOOPS.md:99](LOOPS.md:99), has lost:

   - the 400 pH CSI loss calibration and `<100/450/850/>1500 pH` package ladder;
   - the qualified approximately 4 nH TO-247 source-bond-wire boundary;
   - driver-reference bounce and bias-network guidance;
   - Kelvin-only gate-source-capacitor and bleed-resistor guidance;
   - the 38 pF / 1.2–2.4 W switch-node-overlap calibration;
   - the measured CMTI warning;
   - the isolation-barrier keep-out versus floating-side source-plane distinction.

   The backing material remains at [RESEARCH-smps-pcb-design.md:242](/Users/fab/dev/pv/ee/plans/RESEARCH-smps-pcb-design.md:242). The last two losses are especially dangerous: [POWER.md:287](POWER.md:287) now commands a source plane below gate traces without retaining the isolator-body barrier exception.

   Lower-stakes material also vanished: the two-/three-via diminishing-return calibration and the 2 oz thermal comparison. Not every deleted prescription should be restored verbatim—the broad capacitor, bias and CMTI language deserves its own scope review—but each needs an explicit disposition.

3. **(c) Real design defect — the ten-source list is correct as far as it goes, but materially incomplete.**  
   [LOOPS.md:172](LOOPS.md:172) does not source several actionable claims used across both files:

   - generic magnetic-image theory at [POWER.md:219](POWER.md:219);
   - the parallel-plate derivation at [POWER.md:242](POWER.md:242);
   - CSI/package doctrine at [POWER.md:279](POWER.md:279), whose relevant source is TI SLPA009A;
   - the skin-depth inputs at [LOOPS.md:145](LOOPS.md:145);
   - input-ladder anti-resonance at [LOOPS.md:161](LOOPS.md:161), supported by the omitted TDK guide;
   - the search records supporting both bounded-absence claims.

   Rizzo and Zhao are also listed without their available titles/DOIs. More importantly, the onsemi entry points to a primary that contradicts the body text. POWER’s compact summary is therefore stronger than the evidence apparatus left behind it.

4. **(c) Real design defect — the “one via per amp” absence headline is unbounded.**  
   [LOOPS.md:165](LOOPS.md:165) categorically says the rule “has no measurement behind it,” while the same paragraph admits that IPC-2152—the most likely relevant source—was not obtained. The underlying research bounded this to the supplied corpus plus two named English web-query axes and assigned only low–medium confidence: [RESEARCH-smps-pcb-design.md:229](/Users/fab/dev/pv/ee/plans/RESEARCH-smps-pcb-design.md:229).

   The defensible headline is “No quantitative basis was found in the surveyed corpus and two recorded searches.” This matters because an agent may otherwise discard a thermal/ampacity heuristic on an unjustified universal-absence claim.

5. **(b) Unstated assumption that happens to hold — “P1’s image adds” needs its equivalence convention.**  
   [POWER.md:227](POWER.md:227) and [LOOPS.md:45](LOOPS.md:45) have the correct image sign, but P1’s return is the conducting plane itself. The statement is valid only when the PEC is replaced by its image-equivalent sources. The image is not an additional physical return current to add after an explicit plane-current/full-wave solution. A one-sentence clarification would prevent double counting without changing the P1/P2 recommendation.

6. **(b) Unstated assumption that happens to hold — the voltage-budget allocations need a defined ledger.**  
   [POWER.md:181](POWER.md:181) is correct only if “clamp, package and other transient allocations” are positive, incremental, mutually non-overlapping voltage shares evaluated at the same worst-case corner, `ΔV_L > 0`, and `L_accept` covers only the inductance not already represented by the package allocation. An absolute clamp voltage cannot simply be subtracted alongside `V_bus,max`. The factor direction, dimensions and circularity repair all survive; the allocation semantics remain implicit.

7. **(c) Real design defect — “undamped anti-resonance” is literal overstatement and unsourced in the companion.**  
   [POWER.md:318](POWER.md:318) and [LOOPS.md:161](LOOPS.md:161) describe a real risk, but physical MLCC/plane/trace networks have finite loss. The inspected TDK source says low ESR can produce intense anti-resonant impedance peaks, not zero damping. “Poorly damped/high-Q anti-resonance” is accurate; the TDK source should be listed.

## SURVIVED

- No new arithmetic error was found: A2, A3 and A6–A8 all recompute correctly.
- The magnetic-dipole image signs and their reversal relative to electric-current images are correct.
- The P1/P2/P3 nomenclature table is consistent across Bhargava, EPC/Reusch and ADI.
- The approximately 10 dB equal-excitation result and the corrected Fig. 11 characterization are sound.
- The voltage-budget formula now divides by the 1.5 factor and correctly warns against circular use of measured overshoot.
- The critical-damping formula, `R_total` composition, transition asymmetry, actual-bias capacitance warning and 6.25 nH illustration are correct.
- The dielectric, capacitor-mount and skin-depth restatements are numerically and directionally correct.
- Package-versus-PCB bookkeeping is appropriately warned not to be a literal additive identity across incompatible extractions.
- POWER retains the unresolved EMI trade for PCB-limited P1 layouts rather than presenting the simulated inductance advantage as an unconditional win.
- Return-plane, via and capacitor results are now substantially better tagged as simulated, extracted or topology-specific.
- PCB.md’s loop-catalog reference is satisfied; all local links resolve; the POWER/LOOPS split and SKILL.md route are structurally sound.
- No IEC 60664-1 licensed clause/table content leaked into the patch.

**PUBLISH VERDICT: do-not-publish — the correction pass now makes an explicit, safety-relevant denial that is directly contradicted by its cited onsemi primary.**


=== EXIT 0 ===
