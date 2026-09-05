# Codex xhigh review 4 — N1–N8 rescope pass (POWER.md / LOOPS.md)

Run 2026-09-05, detached, `-s workspace-write`, network on.
**Browser: NOT attached** — the reviewer reported `<title>: unverified` honestly rather than
inventing one. Network worked (live DOI resolution corroborated independently). All findings are
corpus-/DOI-internal, so none depended on a rendered page.

Verdict: do-not-publish (5 findings, no arithmetic errors). All applied.

## ACCESS

- Live web query, 2026-09-05: `Wolfspeed PRD-06752 PCB Layout Techniques for Discrete SiC MOSFETs PDF` returned Wolfspeed’s official SiC MOSFET page first; it links PRD-06752. The narrower gate-loop query returned zero results, and I drew no absence inference from that.
- Attached-browser `<title>`: **unverified**. The attached-browser registry was empty, while locally installed Playwright browsers could not launch under the sandbox. Raw HTTP fallback returned `<title>GitHub - fl4p/kicad-design · GitHub</title>`, but that is not browser-rendered proof.
- I continued with the supplied primary corpus, `pdftotext`, rendered PDF pages, official URLs, and live DOI metadata. No reviewed file was modified.

## N1–N8 verification

| Edit | Result | Verification |
|---|---|---|
| N1 zero-overlap rescope | **Fails** | Wolfspeed §2.3.3 does require the corresponding device-source plane; §2.3.4/Fig. 16 is specifically the low-side gate loop over the midpoint/switch-node plane and describes added gate-to-drain capacitance. The current low-side rule catches that. But the high-side rule now classifies copper by device ownership and ground-referenced `dv/dt`, which is not electrically stable. See F1. |
| N2 Kelvin/source inductance | **Mostly passes** | All values and main attributions check: Yun 3.75/4.33 nH calculated and 8.88/8.52 nH VNA-extracted; ST 9.2 nH analytic, 8.9 nH HFSS, about 4 nH with two parallel wires; Infineon about 2/4 nH depending on lead length. Main-source inductance remains in the commutation path while the Kelvin branch removes it from the shared gate-feedback path. The Kelvin lead and any pre-split internal inductance remain gate-loop terms. The missing Yun frequency and omitted ST bench corroboration are F4. |
| N3 CMTI | **Fails in LOOPS; POWER compact form passes** | Si8275 ratings/results, polarity, temperature range, −130 V/ns example, and replacement-unit lineage all check. The grouped survival sentence incorrectly includes UCC21551 in the ±130 V/ns result; see F3. POWER’s room-temperature example is sufficient for its load-bearing claim—failure can occur below rated CMTI. Omitting cryogenic destruction avoids falsely generalizing an over-specification, cryogenic stress result. |
| N4 isolation keepout | **Passes with provenance caveat** | TI SLLA284G alone says to keep traces, vias and pads off all layers below the isolator for maximum creepage. IRS28x7 is indeed a non-isolated 600 V bootstrap/level-shift HVIC whose ground-plane advice concerns noise coupling. The all-layer rule is supportable as a conservative TI-derived default, not a universal compliance rule. The floating-side source plane is an engineering synthesis of TI’s body-only keepout and Wolfspeed’s local-reference guidance, not a second independently published isolation rule. |
| N5 CSI ladder / bleed | **CSI passes; bleed fails** | TI explicitly calls the ladder estimated typical values for low-voltage MOSFET packages and says to obtain accurate CSI from the manufacturer. The clip/wire-bond mechanism qualifier is fair. The bleed rewrite swings too far; see F2. |
| N6 overlap arithmetic | **Passes** | The Wolfspeed figure really prints inconsistent formula, value, and geometry. For 1 cm², 0.1 mm, εr≈4.3: `C=38.073 pF`; `½CV²f=1.218 W`; `CV²f=2.437 W`. Its printed `0.01 sq.mm, d=0.1 m` would yield only about 3.8 aF. For a capacitance fully charged and discharged once per cycle, `CV²f` is the conventional full-cycle loss; `½CV²f` is stored energy or one dissipative transition. With two driven conductors and possible recovery, neither is a universal board-loss model. LOOPS’ order-of-magnitude framing is correct. |
| N7 sources / absence records | **Fails completion test** | All listed DOIs resolve correctly, including the Yun warning. However two numeric claims remain untraceable from LOOPS, and the new absence-record headlines contradict their bounded qualifications; see F5. |
| N8 onsemi scope | **Passes** | AND9410 is a 30 V synchronous-buck VRM note. It says typical gate-loop inductance is 10–20 nH and asserts that over 20 nH could cause shoot-through. It does not report a measured onset. POWER retains both the useful number and its limits; this is not over-hedged. |

## POWER-alone and consistency audit

`POWER.md` is unsafe to follow alone for N1 and N5:

- N1 leaves a high-side gate loop with an ambiguous prohibition and an over-broad own-source exemption.
- N5 can dissuade an agent from fitting a passive fail-safe bleed on a discrete SiC gate.

Its compact versions of N2, N3, N4, N6 and N8 are sufficient. N7 is an evidence-record problem rather than standalone layout doctrine.

No other POWER/LOOPS contradictions appeared. Router rows are correct at [SKILL.md:52](SKILL.md:52) and [README.md:26](README.md:26). PCB’s forward reference resolves to POWER’s four-row loop catalog at [POWER.md:168](POWER.md:168); the calling text remains coherent at [PCB.md:153](PCB.md:153).

No IEC 60664-1 or CISPR 16-1-4 identifiers, copied tables, clause text, or distinctive standard prose appeared in the reviewed target changes. I found no licensed-standard leakage.

## DOI audit

| DOI | Live resolved title | Result |
|---|---|---|
| `10.1109/TEMC.2011.2145421` | *DC-DC Buck Converter EMI Reduction Using PCB Layout Modification* | Correct |
| `10.3390/electronics14071297` | *Detailed Characterization of Isolated Single and Half-Bridge Gate Drivers from Room Temperature to Cryogenic Temperatures* | Correct |
| `10.3390/mi13071075` | *Bond Wire Damage Detection Method on Discrete MOSFETs Based on Two-Port Network Measurement* | Correct Yun paper |
| `10.1109/MEMC.2020.9241560` | *Decoupling capacitor power ground via layout analysis for multi-layered PCB PDNs* | Correct |
| `10.3390/electronics13204051` | *A Critical Analysis and Comparison of the Effect of Source Inductance on 3- and 4-Lead SuperJunction MOSFETs Turn-Off* | Correct |
| `10.3390/mi13071016` | *Oscillator-Network-Based Ising Machine* | Correctly warned as unrelated |

## Regression sweep

All requested prior corrections remain intact:

- P1/P2/P3: **2.50/2.41/2.70 nH**; normalized P2 advantage is 9.75 dB versus P1 and 9.50 dB versus P3.
- `1300 A/µs` is **13× faster** than `100 A/µs`; with 120 V allowance, the implied limits are **92.3 nH** versus **1200 nH**.
- Independent image derivation: a PEC requires zero normal magnetic flux. Reflecting a normal magnetic-pole pair reverses its ordering, producing an antiparallel image; reflecting a tangential pair preserves its ordering, producing a parallel image. Therefore P2’s normal dipole cancels and P1’s tangential dipole reinforces, subject to the stated close, continuous-plane limit.
- The `ΔV_L` ledger correctly subtracts positive, non-overlapping allocations before division and puts the 1.5 factor in the denominator.
- Dielectric sweep: `(1.17−0.67)/1.17 = 42.7%`.
- Capacitor doublet: `23.4/6.8 = 3.441×`.
- Copper at 10 MHz: `δ≈20.63 µm`; `34.8/20.63 = 1.687` skin depths.
- onsemi’s 20 nH sentence is preserved as an assertion.
- P2-over-P1 remains properly restricted to the package-limited, close-solid-plane regime.
- PRD-08710’s two inferred values remain **8.76 and 12.24 nH**, supporting the bounded 1.5× calibration.

## Source log

| Primary | What was inspected | Status |
|---|---|---|
| [Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf) | §§2.2, 2.3.1–2.3.4; rendered Figs. 14 and 16 | Validated |
| TI SLPA009A | Appendix A/Table 4 and validation description | Validated |
| [Yun et al.](https://doi.org/10.3390/mi13071075) | Calculation, two-port/VNA method, extraction frequency and results | Validated |
| ST AN4407 | Analytic, HFSS, parallel-wire and bench-corroboration passages | Validated |
| Infineon AN 2013-05 | Kelvin topology, source path, ±20 V bounce and 2/4 nH fits | Validated |
| [Büttner et al.](https://doi.org/10.3390/electronics14071297) | §4.4 per-device CMTI tests and failure sequences | Validated |
| TI SLLA284G | Under-body all-layer keepout wording | Validated |
| Infineon IRS28x7 datasheet | Device topology and floating-side plane warning | Validated |
| onsemi AND9410/D | 30 V VRM context and 10–20/>20 nH sentences | Validated |
| [Bhargava et al.](https://doi.org/10.1109/TEMC.2011.2145421) | Tables I, III and IV | Validated |
| Infineon IPW65R060CFD7 datasheet | `Q_rr` test condition and maximum commutation rate | Validated |
| Wolfspeed PRD-08710 | Two operating-point values used for inferred inductance | Validated |
| [Zhao et al.](https://doi.org/10.1109/MEMC.2020.9241560) | Table II pH/mil values and validation geometry | Validated |
| [Rizzo et al.](https://doi.org/10.3390/electronics13204051) | 650 V superjunction scope and turn-off denominator | Validated |
| Reusch/EPC lineage | Dielectric sweep, simulated topology comparison and provenance | Validated from supplied corpus |

No new archive copy was created; the user-supplied hashed corpus was reused.

## FINDINGS

1. **(c) Real design defect — N1 still scopes the prohibition by an electrically meaningless notion of node ownership.**  
   [POWER.md:297](POWER.md:297) requires the high-side switch-node/source plane, but [POWER.md:301](POWER.md:301) forbids “the power loop” or a “different device’s switch node.” The midpoint is simultaneously the high-side source and low-side drain, and it is part of the commutation loop. Moreover, DC+ is nearly static to power ground but undergoes the full differential slew relative to the high-side source; a rule keyed to “high-dv/dt copper” can therefore misclassify it.  
   The statement that own source is “never the target” is valid only for Wolfspeed’s added-`C_GD` mechanism. It does not license unlimited source-plane extent, added `C_GS`, source-plane common-mode capacitance, or coupling into the complementary gate domain. Replace ownership/global-`dv/dt` language with: prohibit overlap with copper whose voltage changes materially relative to that device’s Kelvin/source reference; retain only a compact local source-reference plane.

2. **(c) Real design defect — the bleed-resistor correction has over-shot Wolfspeed’s fail-safe rule.**  
   [POWER.md:315](POWER.md:315) tells an agent to follow vendor guidance rather than fit the approximately 10 kΩ resistor unconditionally and grammatically pairs it with the Kelvin-only capacitor. PRD-06752 instead calls the resistor critical for discharging a discrete SiC gate when disconnected from its driver and warns of false turn-on without it. The capacitor is package-dependent; the passive bleed’s safety function is not. The safe default is to fit a gate-source bleed on discrete SiC unless an explicitly verified passive off-state path provides the same function; its value remains device/driver-specific.

3. **(c) Real design defect — the CMTI survival sentence drops a device-specific test condition.**  
   [LOOPS.md:191](LOOPS.md:191) groups UCC5304, UCC5350 and UCC21551, then says the same parts survived ±130 V/ns to −194 °C. Replacement UCC5304 and UCC5350 units received the limited ±130 V/ns runs; UCC21551’s within-range run was only approximately ±70 V/ns. This is the recurring scope failure: a result from two devices has been copied across all three.

4. **(b) Unstated conditions that happen to hold — the source-inductance comparison remains less scoped than its primaries.**  
   [LOOPS.md:156](LOOPS.md:156) omits that Yun’s 8.88/8.52 nH small-signal extractions were taken at **400 MHz**, despite the paper reporting frequency dependence. Its provenance summary at [LOOPS.md:161](LOOPS.md:161) also reduces ST to analysis/HFSS even though AN4407 says the result was corroborated on the bench. The numbers themselves are correct, and [POWER.md:319](POWER.md:319) remains safe, but LOOPS does not yet meet its own “origin tag on every number” standard.

5. **(c) Real evidence-integrity defect — the bounded-absence rewrite reintroduces categorical non-existence claims and leaves two numbers unauditable.**  
   [LOOPS.md:300](LOOPS.md:300) says neither search supports a strong non-existence claim, but the headings at [LOOPS.md:303](LOOPS.md:303) and [LOOPS.md:316](LOOPS.md:316) state categorical absences. The latter is especially unsupported given its admitted degraded query and open vendor corpus. Both headings should say “was not found in the surveyed corpus/searches.”  
   Source completion is also not literal: the Wolfspeed “12 nH” at [LOOPS.md:163](LOOPS.md:163) does not identify its actual Wolfspeed publication, and the 0.8–1.5 A / 0.3 mm / 1 oz / 10 °C cluster at [LOOPS.md:307](LOOPS.md:307) names no calculator or URL.

No category **(a)** arithmetic errors were found.

## SURVIVED

- Low-side gate-loop-over-switch-node prohibition.
- Kelvin removal from the shared gate-feedback path while retaining main-source inductance in the commutation budget.
- Every N2 numerical value and its principal source.
- Si8275 rating, polarity, threshold, temperature persistence and same-study replacement-unit qualification.
- TI-only isolation provenance and correct IRS28x7 characterization.
- Low-voltage scope of TI’s CSI ladder.
- Wolfspeed overlap capacitance and both 1.2/2.4 W conventions, now correctly presented as an order flag.
- All audited DOI mappings, including the Yun/Ising-machine warning.
- onsemi’s VRM-scoped 10–20 nH assertion.
- Router rows, cross-references, loop catalog, licensed-content check, and every requested regression item.

**PUBLISH VERDICT: do-not-publish — the high-side gate-loop rule still uses contradictory node ownership/global-dv/dt scoping and can authorize the wrong copper beneath a safety-critical gate loop.**

