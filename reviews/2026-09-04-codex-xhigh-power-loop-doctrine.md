# Codex xhigh review — POWER.md loop-layout doctrine patch (2026-09-04)

Verdict: **do not publish as-is**. 18 findings; core orientation thesis survived.

## ACCESS

- Live web query: `/Users/fab/bin/serper-search` for the DOI returned the [author/lab-hosted Bhargava paper PDF](https://www.esdemc.com/public/docs/Publications/Dr.%20Pommerenke%20Related/DC-DC%20Buck%20Converter%20EMI%20Reduction%20Using%20PCB%20Layout%20Modification.pdf) as its first result.
- External page fetched through the attached browser: `<title>"DC-DC buck converter EMI reduction using PCB layout modification" by Ankit Bhargava, David Pommerenke et al.</title>` on [Missouri S&T Scholars’ Mine](https://scholarsmine.mst.edu/ele_comeng_facwork/2587/).
- I checked the patch, report, PCB.md cross-references, and relevant corpus PDFs/text with `git diff`, `rg`, `pdftotext`, and rendered-page inspection. The signed-session browser surface had no active instance, but the attached web browser fetch succeeded.
- No individual claim was left `unverified`. No repository file was modified.

Disposition: **do not publish this patch as-is**. Its central layout lesson is valuable, but several lines invert data, misstate image theory, or turn condition-bound results into general doctrine.

## FINDINGS

1. **[a — arithmetic/data-mapping error] Bhargava’s layouts are assigned in the wrong order.**  
   [POWER.md:222](POWER.md:222) says `2.5 / 2.41 / 2.7 nH (P1 / P3 / P2)`. Table I actually gives:

   - P1 vertical: 2.50 nH, 4.5 mm²
   - P2 flat horizontal: 2.41 nH, 36 mm²
   - P3 two-sided vertical: 2.70 nH, 4.4 mm²

   The parenthetical must be `P1 / P2 / P3`. The stated 12% spread remains correct.

2. **[a — arithmetic/comparison error] The diode-commutation-speed direction is reversed.**  
   [POWER.md:176](POWER.md:176)–[180](POWER.md:180) says the datasheet may specify a “much lower maximum” than the `Q_rr` condition. The Infineon values are 100 A/µs for `Q_rr` characterization and **1300 A/µs maximum**, so the maximum is 13× higher. The resulting arithmetic is nevertheless correct:

   - `120 V / 0.1 A/ns = 1200 nH`
   - `120 V / 1.3 A/ns = 92.3 nH`

   The warning against using a `Q_rr` test condition as design slew rate is sound; only “lower” is wrong.

3. **[c — real design defect] The overshoot budget can double-count overshoot, and the margin direction is unspecified.**  
   [POWER.md:169](POWER.md:169) defines headroom against “bus + real overshoot.” If that measured overshoot already contains the `L·di/dt` term being budgeted, the derivation is circular. It should allocate:

   `ΔV_L = V_allowed − V_bus,max − V_other_transients/clamp/package allocations`

   The safe margin form should be explicit, e.g. `L_accept ≤ ΔV_L /(1.5·|di/dt|max)`. “Carry a ≥1.5× margin” at [POWER.md:171](POWER.md:171) could otherwise be read as multiplying the allowed inductance.

   The 39% basis is also narrow: one Wolfspeed module at two gate resistances gave implied values of about 8.76 and 12.24 nH. A 1.5× factor is reasonable for that example, not a universal uncertainty guarantee.

4. **[c — real design defect] The technology-level conclusion contradicts the patch’s own warning.**  
   [POWER.md:181](POWER.md:181)–[185](POWER.md:185) promotes “two to three orders” for slow Si as a general technology result. That came from the 0.1 A/ns `Q_rr` characterization condition the preceding paragraph says not to use. At the same device’s 1.3 A/ns maximum, the raw budget is about 92 nH—not generally two or three orders above a TO-247 PCB loop. Keep this as an illustrative scenario, not technology doctrine.

5. **[c — real design defect] The vector equation and one half of the image explanation are wrong.**  
   [POWER.md:217](POWER.md:217) should say `m⃗ = I·A·n̂`, or use vector area `A⃗`; `I·A` alone is scalar.

   For a magnetic dipole above a PEC plane:

   - dipole normal to the plane: opposite-polarity image, cancellation;
   - dipole parallel to the plane: same-polarity image, reinforcement.

   Thus P2 cancellation is correct, but [POWER.md:220](POWER.md:220)’s claim that P1 has “no image left over” is not the correct image-theory reasoning. There is an image; its sign does not cancel the tangential dipole. The sign rules are confirmed by the official [Purdue electromagnetic-field notes](https://engineering.purdue.edu/wcchew/ece604f23/EMFTEDX032124R.pdf).

6. **[b — unstated assumption that happens to hold] Image cancellation needs a plane qualifier.**  
   [POWER.md:218](POWER.md:218)–[220](POWER.md:220) silently assumes a close, continuous, electrically large, good-conductor plane without edge/aperture disruption. That approximately holds for Bhargava’s specimen and is supported by its measurement, but it is not automatic for a finite split PCB plane.

7. **[c — real design defect] The Fig. 11 prohibition is overstated.**  
   [POWER.md:337](POWER.md:337) correctly identifies Table III as the measured far-field/loop-current transfer function:

   - Board 1 vs 2: `−34.25 − (−44) = 9.75 dB`
   - Board 3 vs 2: `−34.5 − (−44) = 9.50 dB`

   Fig. 11’s 32/13/29 dBµV/m peaks do yield raw differences of 19 and 16 dB, and they are not current-normalized. But [POWER.md:338](POWER.md:338) should say “do not quote them as equal-excitation or geometry-only results,” not “do not quote” at all. They remain valid chamber results for those three operating boards.

8. **[b — unstated assumption that happens to hold] The parallel-plate formula is correct but underqualified.**  
   [POWER.md:233](POWER.md:233)–[235](POWER.md:235) is the wide-strip limit: `L ≈ μ₀hl/w`. It requires approximately `w ≫ h`, a return plane wider than the strip, and negligible fringing/end effects; preferably also `l ≫ h`. “Wide conductor over a close plane” gestures at this but is too vague for a numeric doctrine line.

   Within that model, the conclusion is correct: increasing `w` lowers inductance while the nominal vertical flux-cutting surface `l·h` is unchanged. See the [RPI parallel-plate derivation](https://hibp.ecse.rpi.edu/~connor/education/Fields/TwoWire-ParallelPlateLines.PDF).

9. **[c — real design defect] The critical-damping arithmetic survives, but the design rule omits the quantities that make it usable.**  
   [POWER.md:262](POWER.md:262)–[265](POWER.md:265) has the correct linear series-RLC condition:

   `R_total,crit = 2√(L_g/C_eff)`

   and the inversion is correct: `L = R²C/4 = 25·1 nF/4 = 6.25 nH`.

   However:

   - `R_g,total` must include external resistor, driver pull-up/pull-down output impedance, MOSFET internal/package gate resistance, and trace resistance.
   - Turn-on and turn-off totals differ; onsemi measured 1.6/1.8 Ω and 1.4/1.1 Ω.
   - The source differential equation uses `C_GS`; `C_iss` is only an approximate effective small-signal substitute. It is nonlinear and bias/frequency dependent, especially through the Miller interval.
   - The report explicitly retained “at the actual bias”; the patch dropped it.
   - The worked sentence switches from `R_g,total` to ambiguous `R_g = 5 Ω`.

   Consequently, 6.25 nH is an illustrative linearized calculation, not an unconditional “real ceiling.”

10. **[c — real design defect] The gate-loop heading, target, and onsemi quotation exceed their source.**  
    [POWER.md:253](POWER.md:253) generalizes “usually larger” from one 30 V synchronous-buck VRM. That does not establish the ordering for high-voltage TO-247/SiC designs.

    At [POWER.md:259](POWER.md:259)–[261](POWER.md:261):

    - `<10 nH` is an untagged synthesis target, not a source threshold.
    - AND9410 says typical 10–20 nH gate inductance requires more damping and that unoptimized layout can “easily” cause application problems. It does **not** say “above ~20 nH shoot-through could easily occur.” The patch splices separate statements into a stronger quoted assertion.
    - The 20.265/13.8/1.51 nH values are calculated from measured ringing periods and nominal capacitances, not directly measured inductances. [POWER.md:255](POWER.md:255)’s “Measured” tag is too strong.

11. **[a — arithmetic/direction error] The 5→15 mil percentage has the wrong denominator for the stated direction.**  
    [POWER.md:280](POWER.md:280)–[283](POWER.md:283) summarizes a simulated change from about 0.67 nH at 5 mil to 1.17 nH at 15 mil:

    - increasing 5→15 mil raises L by about 75%;
    - thinning 15→5 mil reduces L by about 43%.

    Therefore “~45% over a 5→15 mil change” is directionally ambiguous and, read normally, arithmetically wrong. The 3.45→0.67 nH FastHenry result is correct: an 80.6% reduction.

12. **[c — real design defect] The return-plane/via paragraph turns one simulated passive-shield result into universal measured doctrine.**  
    [POWER.md:280](POWER.md:280)’s “most field energy is in that dielectric” came from one Reusch GaN geometry; the thesis actually partitions leakage energy between substrate and air.

    More seriously, [POWER.md:288](POWER.md:288)–[290](POWER.md:290) says inductor vias, thermal vias, or splits in a **return plane** “were measured to dominate.” The primary showed Q3D-simulated inductor-via antipads interrupting a **passive shield plane** in one layout. It did not test thermal vias generally, and it was not a measurement. The continuity rule is good; its claimed evidence and scope are not.

13. **[c — real design defect] The capacitor-mount figures changed provenance and arrangement.**  
    [POWER.md:300](POWER.md:300)–[302](POWER.md:302):

    - 1.45 nH and 0.42 nH are model calculations obtained by multiplying Zhao’s 23.4 and 6.8 pH/mil values by 62 mil. The validation structures were 40 mil; these are not direct 62-mil extractions.
    - The ~9% 0805→0402 reduction is in the **aligned** column: 23.4→21.4 pH/mil. In the immediately preceding **doublet** arrangement, both are 6.8 pH/mil and the reduction is zero. “In the same arrangement” is therefore wrong or dangerously ambiguous.

14. **[c — real design defect] The 0.54/0.84 nH and via-shift results have lost necessary scope.**  
    [POWER.md:303](POWER.md:303)–[306](POWER.md:306):

    - 0.54 and 0.84 nH are FastHenry extractions at 200 MHz, with only indirect bench corroboration—not measured loop inductances.
    - The comparison is confounded by the µModule BGA pinout limiting the top-layer VIN width; it is not a clean “vias versus no vias” experiment.
    - The 1 cm / 0.4 nH / 5 dB result is from Bhargava’s particular P2 image-current topology. It should not be generalized to arbitrary commutation vias.

15. **[c — real design defect] The source block’s completeness claim is false.**  
    [POWER.md:330](POWER.md:330)–[332](POWER.md:332) says these are the works behind the numbers and that origins are tagged inline. Missing sources include:

    - Wolfspeed PRD-08710 for the 39% implied-L spread;
    - Infineon IPW65R060CFD7 for 1200/92 nH;
    - Chen et al., *Electronics* 2024, 13, 4758 for 16.7–32.4 nH;
    - Zhao et al., IEEE EMC Magazine 9(3) for 1.45/0.42 nH;
    - Wolfspeed PRD-06752 for the gate RLC model;
    - Rizzo et al. 2024 for the Kelvin turn-off mechanism.

    Several inline tags are also wrong or absent, as detailed above. For a public evidence-led skill, this is a material provenance defect.

16. **[c — real design defect] Two report-open questions were promoted too far.**  
    [POWER.md:246](POWER.md:246)–[251](POWER.md:251) says to “take P1” for PCB-limited chip-scale GaN. Its simulated inductance advantage is supported, but whether its radiation ordering versus P2 persists in that regime is explicitly open in the report. The recommendation needs that unresolved EMI trade stated.

    Likewise, [POWER.md:269](POWER.md:269)–[273](POWER.md:273) presents the Kelvin turn-off mechanism generically. The underlying experiment used one 650 V silicon SuperJunction pair in a boost converter; the report explicitly proposes repeating it on SiC to test technology dependence.

17. **[c — real design defect] The copper-weight explanation is too absolute.**  
    The 9–21 µm skin-depth range at 50–10 MHz is correct, but [POWER.md:291](POWER.md:291)–[293](POWER.md:293)’s “extra copper carries no HF current” is false. At 10 MHz, 1 oz copper is only about 1.7 skin depths thick; deeper copper carries diminished, not zero, current. The defensible conclusion is that loop inductance is dominated by external field geometry and additional thickness has diminishing inductance benefit.

18. **[c — real design defect] The companion is now too broad for its load trigger.**  
    POWER.md grew from 11,154 bytes/166 lines to 25,314 bytes/350 lines—2.27× in bytes, entirely from this 184-line patch. Yet it loads for HV rails, snubbers, gate drive, and current sense even when power-loop literature is irrelevant.

    I would create a narrowly routed loop-layout/evidence companion. Specifically:

    - condense [POWER.md:187](POWER.md:187)–[196](POWER.md:196) to one package-boundary warning;
    - retain the P1/P2/P3 table, but move the lineage discussion at [POWER.md:209](POWER.md:209)–[213](POWER.md:213);
    - move the study narrative and regime discussion at [POWER.md:222](POWER.md:222)–[251](POWER.md:251);
    - move detailed gate evidence at [POWER.md:253](POWER.md:253)–[276](POWER.md:276);
    - move the study-by-study numbers at [POWER.md:280](POWER.md:280)–[315](POWER.md:315);
    - cut or move the “one via per amp” bounded-absence excursus at [POWER.md:311](POWER.md:311)–[315](POWER.md:315) to thermal guidance;
    - move the source apparatus at [POWER.md:328](POWER.md:328)–[350](POWER.md:350) with the evidence it supports;
    - merge the retained compact loop rules with the existing “Power loop and placement” section instead of leaving two introductions.

## SURVIVED

- The core orientation thesis survives: scalar area alone is insufficient, and P2’s normal magnetic dipole receives an opposing image while P1’s tangential dipole does not cancel.
- Bhargava Table III is correctly used for the approximately 9.75/9.50 dB current-normalized comparison. Table IV independently supports roughly 10–11 dB.
- Bhargava’s areas are true flux-cutting surfaces; P1’s 4.5 mm² is genuinely `18 mm × 0.25 mm`, not a top-view footprint.
- `L ≈ μ₀hl/w` and “widening lowers L without changing nominal `l·h` area” survive within the wide parallel-plate limit.
- `R_total,crit = 2√(L/C)` and the 6.25 nH inversion are arithmetically correct.
- `L_max = ΔV/|di/dt|` is physically sound once the voltage allocation and margin direction are corrected.
- The warning not to treat `Q_rr` characterization slew as an intended operating slew survives, as do the raw ~1200 and ~92 nH calculations.
- The 2.4→0.5 nH package substitution is correctly tagged as a CST simulation comparison.
- The 16.7–32.4 nH TO-247 range and 1.94× ratio are correct Q3D results.
- The 70%/2.8% capacitor-current split is correctly scoped to one full-wave simulation.
- The raw 20.265/13.8/1.51 nH onsemi values are reproduced correctly, subject to the extraction-label caveat.
- The 3.45→0.67 nH FastHenry result is numerically correct.
- The four-row loop catalog does satisfy PCB.md’s forward reference and correctly covers commutation, gate, CSI, and mount/snubber loops without inventing a universal nH limit for every case.
- No contradiction was found with functional satellites, native DRC, creepage, HV-domain classification, or PCB.md’s stack-up/decoupling rules; there is duplication, not rule conflict.
- No IEC 60664 clause values, table values, or recognizable licensed standard text entered the patch.
- The report’s open DPT-extraction disagreement, heatsink-grounding trade, balance-capacitor band, and untested two-plane-sandwich hypothesis did not leak into POWER.md as settled doctrine.


=== EXIT 0 ===
