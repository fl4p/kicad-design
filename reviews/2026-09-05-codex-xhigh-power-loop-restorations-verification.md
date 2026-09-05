# Codex xhigh review 3 — F1–F7 restorations (POWER.md / LOOPS.md)

Run 2026-09-05, detached, `-s workspace-write`, network on, headed Chrome attached over CDP :9336.
Scope: the seven fixes applied after review 2. Verdict: do-not-publish (7 findings), all now applied.

## ACCESS

- Live query: `site:onsemi.com AND9410 gate inductance higher than 20 nH shoot through`. The first result was the official [onsemi AND9410/D PDF](https://www.onsemi.com/pub/Collateral/AND9410-D.PDF).
- External page fetched through the attached browser had `<title>`: `"DC-DC buck converter EMI reduction using PCB layout modification" by Ankit Bhargava, David Pommerenke et al.` ([Scholars’ Mine record](https://scholarsmine.mst.edu/ele_comeng_facwork/2587/)).
- I used search snippets only for discovery; load-bearing claims came from primary text plus rendered PDF pages. No requested check remained `unverified`.
- I modified no files.

## F1 — settled

AND9410’s preceding sentence gives the exact value phrase “10 nH to 20 nH” and calls that range typical for modern VRMs and layout-dependent. Its next sentence is:

> “Gate inductance higher than 20 nH could cause shoot through problem and harm system efficiency of synchronous buck converter.”

The later statement containing the adverb *easily* concerns unoptimized layout generally; it is not part of the 20 nH statement.

Therefore [POWER.md:282](POWER.md:282) and [LOOPS.md:118](LOOPS.md:118) now agree and are faithful in:

- value: typical 10–20 nH; higher than 20 nH;
- scope: a modern synchronous-buck VRM;
- qualifier: “could cause,” not “will” or “easily”;
- epistemic status: vendor assertion, not a measured threshold or observed onset.

F1 passes.

## F2–F7 disposition

| Item | Result | Judgment |
|---|---|---|
| F2 restored gate-drive material | **Fail** | Most numbers are real, but restoration introduced scope, topology, and meaning defects detailed below. |
| F3 source-list completion | **Fail** | All DOIs printed in `LOOPS.md` resolve correctly, but several restored claims remain uncited and the “records” do not record their queries. |
| F4 “one via per amp” | Pass | Explicitly bounded to the corpus and two English query axes, low–medium confidence, IPC-2152 unavailable; surrounding prose disclaims global absence. |
| F5 image equivalence | Pass | Correct upper-half-space equivalent source, not extra solved current. It preserves the P1/P2 recommendation. |
| F6 voltage ledger | Pass | Positive, incremental, non-overlapping, same-corner allocations; explicit `ΔV_L > 0`; clamp and package double-counting are addressed. |
| F7 anti-resonance | Pass | TDK directly covers different-SRF MLCC anti-resonance and intense impedance peaks caused by low ESR; “poorly damped, high-Q” is accurate. |

Key F2 arithmetic that does survive:

- TI’s calculated 400 pH comparison is 0.41→1.18 W: 2.88× and `(1.18−0.41)/0.4 = 1.925 W/nH`. Aggregate calculated/measured converter losses were 3.50/3.45 W. The four package entries are exactly `<100 / 450 / 850 / >1500 pH`. [TI SLPA009A](https://www.ti.com/lit/slpa009)
- The overlap calculation gives approximately 38.1 pF. `½CV²f = 1.218 W` and `CV²f = 2.437 W` at 800 V/100 kHz.
- Infineon reports approximately ±20 V or higher driver-reference peaks in its C7 topology, and derives an approximate `di/dt < (U_g−V_th)/L_s` limit.
- Wolfspeed recommends a gate-source capacitor only with a Kelvin-source package, not TO-247-3, and gives a typical 10 kΩ discharge resistor—but specifically for discrete SiC MOSFET gate drive. [Wolfspeed PRD-06752](https://assets.wolfspeed.com/uploads/2024/10/Wolfspeed_PRD-06752_PCB_Layout_Techniques_for_Discrete_SiC_MOSFETs_Application_Note.pdf)

## DOI, cross-reference, and license audit

Every DOI actually printed in `LOOPS.md` resolves to the named work:

- `10.1109/TEMC.2011.2145421` → Bhargava et al., *DC-DC Buck Converter EMI Reduction Using PCB Layout Modification*.
- `10.1109/MEMC.2020.9241560` → Zhao et al., *Decoupling Capacitor Power Ground Via Layout Analysis for Multi-Layered PCB PDNs*.
- `10.3390/electronics13204051` → Rizzo et al., *A Critical Analysis and Comparison of the Effect of Source Inductance on 3- and 4-Lead SuperJunction MOSFETs Turn-Off*.

Chew §32.2.2 exists in the current Purdue ECE604 notes and gives the cited magnetic-dipole image signs. The skin-depth recomputation gives 20.63 µm at 10 MHz and 9.23 µm at 50 MHz, matching [LOOPS.md:237](LOOPS.md:237).

All reviewed Markdown cross-references resolve. The new router rows are appropriate at [SKILL.md:50](SKILL.md:50) and [README.md:24](README.md:24). PCB’s forward reference at [PCB.md:150](PCB.md:150) reaches the four-entry catalog in POWER. `git diff --check` is clean.

No IEC 60664-1 or CISPR 16-1-4 clause/table values—or even those identifiers—appear in the reviewed target text. B5 passes.

## SOURCE ACCESS LOG

| Source | Route and validation | Status |
|---|---|---|
| onsemi AND9410/D | Official live result; corpus PDF text and rendered pp. 3/8 | Validated |
| Bhargava et al. | Live DOI resolution and external metadata; rendered Tables I–II | Validated |
| TI SLPA009A | Corpus PDF; Tables 1–4, rendered package table, arithmetic recomputed | Validated |
| Wolfspeed PRD-06752 | Corpus text/PDF; §§2.3.1–2.3.4 and figures inspected | Validated |
| Infineon AN 2013-05 | Corpus PDF; driver-reference, CSI equation and measured package comparison | Validated |
| TI SLLA284G / Infineon IRS28x7 | Corpus PDFs; relevant pages rendered | Validated; sources do not establish the claimed vendor consensus |
| Büttner et al. | [Live DOI article](https://doi.org/10.3390/electronics14071297), §4.4 | Validated |
| Yun et al. | [Live correct DOI](https://doi.org/10.3390/mi13071075), full article text | Validated |
| TDK MLCC guidance | [Live rendered page](https://product.tdk.com/en/techlibrary/solutionguide/mlcc_replace-guide.html) | Validated |
| Chew ECE604 | Official Purdue PDF; §32.2.2 text and rendered page | Validated |
| Infineon IPW65R060CFD7 / Wolfspeed PRD-08710 | Corpus PDFs and independent arithmetic | Validated |

## FINDINGS

1. **(c) Real design defect — the restored zero-overlap rule contradicts the required high-side return plane.** [POWER.md:294](POWER.md:294), [LOOPS.md:157](LOOPS.md:157)

   For a high-side device, the source reference is the switch node. A source-referenced plane under its gate traces therefore intentionally is a switch-node plane. The adjacent instruction to keep *zero overlap* between gate-loop conductors and the switch-node plane is impossible to satisfy simultaneously and can make an agent strip the required return plane.

   Wolfspeed’s source-plane rule applies to the corresponding device; its zero-overlap example explicitly concerns the **low-side** MOSFET gate loop overlapping the switch-node plane. The 800 V/38 pF loss argument likewise does not apply between a high-side gate and its own source plane—the relevant differential swing there is gate-source voltage, not the bus swing. Scope the prohibition to low-side or otherwise unrelated-domain gate conductors.

2. **(c) Real design defect — a Kelvin package does not remove the source bond wire.** [POWER.md:311](POWER.md:311), [LOOPS.md:150](LOOPS.md:150)

   A four-pin/Kelvin connection excludes the power-source inductance from the shared gate-drive/CSI path; it does not physically eliminate that inductance or remove it from every loop. The current wording can cause an agent to omit package/source inductance from the commutation budget.

   The roughly 4 nH evidence is also specimen-specific: Yun calculates 3.75/4.33 nH bond-wire networks while measuring 8.88/8.52 nH total source paths; Infineon’s separate bench fit gives approximately 2 or 4 nH effective source inductance depending on lead length. State “removed from the gate-drive feedback path,” not “removed.”

3. **(c) Real design defect — the restored CMTI numbers and scope do not match the paper.** [LOOPS.md:161](LOOPS.md:161), with the stronger standalone warning at [POWER.md:305](POWER.md:305)

   Büttner reports Si8275 malfunctions for negative transients **exceeding 120 V/ns**; its room-temperature plotted example is −130 V/ns. That is not “malfunctioned at −120 V/ns.” The 2021/2024 evidence is replacement units from two production batches within the same study, not independent reproduction.

   Permanent UCC-driver damage followed repeated over-spec transient testing during cryogenic cooling, with failures appearing around −125 to −150 °C. “Exceeding it permanently destroyed three other driver types” drops that material test history and temperature scope.

4. **(c) Real design defect — the claimed TI/Infineon isolation-keepout consensus is not established.** [POWER.md:298](POWER.md:298), [LOOPS.md:165](LOOPS.md:165)

   [TI SLLA284G](https://www.ti.com/lit/pdf/slla284) really does require the space under an isolator to be free of traces, vias and pads on all layers for maximum creepage. The cited Infineon IRS28x7 device is a non-isolated bootstrap/level-shift driver; its rule says system ground should not be under or near the high-voltage floating side to limit noise coupling. It does not discuss an isolation barrier, isolator-body keepout, all layers, or creepage.

   The actionable exception itself is now clear—keep out only under the isolator body and retain the floating-side source return—but the “vendors disagree only on why” provenance claim is false.

5. **(c) Real design defect — restored package and bleed rules lost their technology scope.** [POWER.md:286](POWER.md:286), [POWER.md:308](POWER.md:308), [LOOPS.md:144](LOOPS.md:144)

   TI labels the package ladder as **estimated typical values for low-voltage power MOSFET packages** and tells designers to obtain accurate CSI from the manufacturer. `LOOPS.md` drops all three qualifiers and then calls clip versus wire bond “the whole delta,” although the table is not a controlled same-device measurement. That is unsafe evidence transfer into a skill explicitly covering 48–800 V SiC/GaN.

   Similarly, Wolfspeed’s capacitor/10 kΩ recommendation is for discrete SiC MOSFETs. “Always fit” is broader than its primary and should be limited by device/package/vendor guidance.

6. **(b) Unstated assumption that happens to hold — the 1.2–2.4 W range silently repairs two errors in its source.** [POWER.md:294](POWER.md:294), [LOOPS.md:157](LOOPS.md:157)

   The current capacitance is correct if the intended geometry is 1 cm² across 0.1 mm FR-4. The primary prints inconsistent area/separation units and prints `CV²f = 1.2 W`, although that expression gives approximately 2.4 W. The lower endpoint instead equals `½CV²f`.

   The skill needs to state that 1.2 versus 2.4 W represents one stored-energy event versus a full charge/discharge loss convention, and that the actual loss depends on the two conductors’ differential waveform and energy recovery. A bare range is not a calibration.

7. **(c) Real design defect — F3 remains incomplete, and its upstream ledger contains the anticipated composed DOI error.** [LOOPS.md:209](LOOPS.md:209), [LOOPS.md:240](LOOPS.md:240)

   The source list omits the primaries supporting ±20 V/R–C biasing, isolation keepout, the 4 nH/8.88 nH package paragraph, and CMTI. PRD-06752 is listed only as the gate-RLC source even though it is also being used for capacitor, bleed, shielding and overlap rules.

   The “bounded-absence search records” record only the number of query axes—not the query strings, search surface, result/stop condition, or degraded response. They are summaries, not reproducible records.

   There is also a concrete trap upstream: [manifest.d/lane-b.md:20](lane-b.md:20) assigns Yun’s paper `10.3390/mi13071016`, which resolves to an unrelated Ising-machine paper. The correct DOI is `10.3390/mi13071075`. No wrong Yun DOI is currently printed in `LOOPS.md`, but copying that ledger during the required source-list repair would introduce one.

## SURVIVED

- F1’s onsemi correction now survives exactly and consistently.
- F4’s “one via per amp” statement is honestly bounded and no longer reads as a global absence claim.
- F5’s image-equivalence convention is correct. Independently: the PEC boundary requires a same-sign magnetic-pole image; reflection preserves tangential pole ordering, giving a parallel/reinforcing tangential dipole, and reverses normal ordering, giving an antiparallel/cancelling normal dipole. This preserves the P2-over-P1 package-limited recommendation.
- F6’s ledger is non-circular and prevents silent negative or double-counted budgets.
- F7’s “poorly damped, high-Q” wording and TDK citation survive.
- Bhargava mapping survives: P1/P2/P3 = 2.50/2.41/2.70 nH.
- `Q_rr` direction survives: 1300 A/µs is 13× **faster** than 100 A/µs; 120 V implies 1200 versus 92.3 nH before the separate 1.5 margin.
- The earlier fixes for the ~10 dB normalized EMI comparison, stack-up percentages, parallel-plate qualifiers, capacitor-mount provenance, via-topology scope, and diminished-not-zero skin effect remain intact.
- Cross-references, router rows, formatting, and licensed-content audit survive.

**PUBLISH VERDICT: do-not-publish — the unscoped zero-overlap rule can make an agent remove the required high-side source/switch-node return plane.**

