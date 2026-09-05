# Commutation- and gate-loop evidence

The actionable rules live in [`POWER.md`](POWER.md). This file holds the measurements and
simulations behind them, with their scope and sources, and is worth loading when you are
**deriving a loop budget, defending a loop number in review, or deciding whether a rule applies to
your regime** — not on every power board.

Read the origin tag on every number here. Several of these figures circulate widely with their
tags stripped, which is how a single Q3D simulation becomes "measured" in a third-hand app note.

## The P1/P2/P3 measurement set

A. Bhargava, D. Pommerenke, K. W. Kam, F. Centola, C. W. Lam, "DC-DC Buck Converter EMI Reduction
Using PCB Layout Modification", *IEEE Trans. EMC* 53(3):806–813, Aug 2011,
DOI `10.1109/TEMC.2011.2145421`. Three boards differing only in commutation-loop arrangement.

| | area (Table I) | L (Table I) | far field / loop current (Table III) |
|---|---|---|---|
| **P1** thin z-sandwich | 4.5 mm² = 18 mm × 0.25 mm | 2.50 nH | −34.25 dB |
| **P2** flat in-plane | 36 mm² = 6 mm × 6 mm | 2.41 nH | **−44 dB** |
| **P3** through-board | 4.4 mm² | 2.70 nH | −34.5 dB |

Two results, both load-bearing:

- **An 8× enclosed-area change moved L by 12 %, non-monotonically.** The areas are true
  flux-cutting surfaces, not top-view footprints — P1's 0.25 mm *is* the dielectric thickness.
  Scope: one package-dominated specimen, one stack-up. This does **not** show area is decoupled
  from L in general; it shows the package swamped the difference here.
- **P2 radiated ~9.75 dB below P1 and ~9.50 dB below P3 at equal excitation.** Table III is the
  measured far-field-to-loop-current transfer function, so it normalises out how hard each board
  happened to ring. Table IV's nine-orientation dipole moments (0.385 / 0.35 / 0.107 µ →
  10.3–11.1 dB) corroborate independently.

**On Fig. 11's 32 / 29 / 13 dBµV/m peaks (differences of 19 and 16 dB).** These are valid measured
chamber results for those three operating boards. They are *not* an equal-excitation or
geometry-only comparison — equal loop inductance does not establish equal ring current — so they
cannot be quoted as the penalty attributable to the arrangement. Use ~10 dB for that. An earlier
draft of the source report quoted 16–19 dB as the arrangement penalty; that was the single most
consequential error adversarial review found in it.

**Image theory, stated correctly.** Over a close, continuous, electrically large, good-conductor
plane, a magnetic dipole **normal** to the plane has an **antiparallel** image (cancellation),
while one **tangential** to the plane has a **parallel** image (**reinforcement**). Note these are
the reverse of the electric-current rules, which is where the sign is easy to invert. So P2's
normal dipole is cancelled by its image and P1's in-plane dipole is *reinforced* by it — P1 is not
merely "left uncancelled". P1's return current is *in* the plane, so read the image as the
equivalent source that replaces the plane in the upper half-space, not as a second physical loop to
add after solving the plane current; in the limiting vertical half-loop construction the return
segment and its mirror cancel on the boundary while the two tangential moments reinforce. The qualifier is load-bearing: a finite, split, or heavily perforated
plane does not deliver the cancellation, and the result above was measured over a solid one.

## The EPC / Reusch lineage — one source, and simulated

EPC White Paper WP010 (D. Reusch, "Optimizing PCB Layout", 2014) and EPC How2AppNote 007 (2021)
both trace to D. C. Reusch, *High Frequency, High Power Density Integrated Point of Load and Bus
Converters*, PhD dissertation, Virginia Tech, 2012 (handle `10919/26920`). The headline
P1 ≈ 0.35 nH vs P2 ≈ 1.0 nH vs P3 ≈ 1.0–1.8 nH figures are **Q3D simulations**; only efficiency
and overshoot were measured. A white paper, an app note and a trade article agreeing on these is
one source, not three. TI's shield-layer material cites Bhargava, so it is not independent either.

ADI's hot-loop table (J. Sun, L. Jiang, H. Zhang, "How to Optimize Switching Power Supply Layout
by Minimizing Hot Loop PCB ESRs and ESLs", *Analog Dialogue* RAQ Issue 207) is **FastHenry
extraction**, bench-verified only indirectly through efficiency and ripple frequency. **ADI built
no P1 variant**, so it cannot rank the arrangement EPC recommends.

**Regime split, and what is still open.** In the package-limited regime (SO-8, POWER56, D²PAK,
TO-247) P1 buys almost no inductance and carries the reinforced dipole, so P2 over a close solid
plane is the better trade. In the PCB-limited regime (LGA, chip-scale GaN) P1 cuts inductance ~3×
(*simulated*, single lineage). **What is not measured is whether the ~10 dB radiation ordering
survives into that regime** — Bhargava's boards were package-limited, where P1's inductance
advantage does not appear. Taking P1 for chip-scale GaN therefore accepts an unquantified EMI
trade; make the prepreg as thin as the fab allows, since the reinforced dipole scales with it.

## Package share

- One CST study: a synchronous-buck loop fell 2.4 nH → 0.5 nH when POWER56 devices were replaced
  with ideally-flat models. *Difference between two simulations, not a measured package
  inductance.*
- One Q3D study: a TO-247 half-bridge spread 16.7–32.4 nH across arrangements — only 1.94×
  best-to-worst. (Chen et al., *Electronics* 2024, 13, 4758.)

`L_loop = L_package + L_PCB` is bookkeeping, not an identity: connected conductors have mutual
terms, and separately-quoted package, CSI, lead and PCB figures rarely share extraction boundaries
or frequency. Do not add them or assign percentage shares across sources.

## Budget calibration

The **1.5×** default in POWER.md's budget is calibrated on one Wolfspeed module (PRD-08710)
measured at two gate resistances, whose implied loop L came out ~8.76 and ~12.24 nH — a 39 %
spread at one operating point pair. Reasonable as a default; not a universal uncertainty bound.

**The `Q_rr` trap, with real numbers.** Infineon IPW65R060CFD7 characterizes `Q_rr` at 100 A/µs
while specifying a **1300 A/µs** maximum diode-commutation speed — the device's own limit is 13×
*faster* than its characterization condition. On a 400 V bus with ΔV = 120 V that is ~1200 nH from
the characterization figure against ~92 nH from the maximum. Both are scenario arithmetic on one
part; the lesson is that the characterization condition is not a slew rate, in either direction.

Consequently **"layout is not the constraint for slow Si" is a scenario result, not a technology
law.** At that part's own maximum commutation speed the budget is ~92 nH, which a sloppy TO-247
layout can approach. Re-derive with your device's real slew before concluding layout is free.

## Gate loop

onsemi AND9410/D, on one **30 V synchronous-buck VRM** (NTMFD4C85). Values are **extracted from
measured ringing periods with nominal capacitances** via `L = (T/2π)²/C` — not directly measured
inductances.

| quantity | period | C used | extracted |
|---|---|---|---|
| HS gate loop | 40 ns | C_iss = 2 nF | **20.265 nH** |
| LS gate loop | 60 ns | C_iss = 6.6 nF | **13.8 nH** |
| power loop | 16 ns | C_oss = 4.3 nF @ 12 V | **1.51 nH** |

An order of magnitude between the gate and power loops **on that board**. It is a warning worth
carrying to other designs, not an established ordering for high-voltage TO-247/SiC layouts, which
have larger commutation loops and different gate topologies.

What AND9410 actually says: gate-loop inductance is *"typically around 10 nH to 20 nH, depending
on PCB layout"*, and *"Gate inductance higher than 20 nH could cause shoot through problem."* That
is a **vendor assertion** — no measured threshold, no reported onset. Quote it as such. Note that
the note's separate remark about un-optimized layout causing problems *"easily"* belongs to a
different sentence: splicing the two into "above 20 nH shoot-through could easily occur" overstates
the source, and denying the 20 nH statement altogether understates it. Both errors were made in
successive drafts of this file.

**Critical damping.** `R_total,crit = 2√(L_g/C_eff)` is the linear series-RLC condition. `R_total`
must include the external resistor, the driver's pull-up/pull-down output impedance, the device's
internal and package gate resistance, and trace resistance — turn-on and turn-off totals differ
(onsemi measured 1.6/1.8 Ω and 1.4/1.1 Ω). The source derivation uses `C_GS`; `C_iss` is an
approximate small-signal substitute that is nonlinear and bias-dependent, especially through the
Miller interval, so evaluate it **at the actual bias**. The worked inversion `L = R²C/4 =
25 × 1 nF / 4 = 6.25 nH` is arithmetically correct but illustrative and linearized — not an
unconditional ceiling. (Wolfspeed PRD-06752 for the gate RLC model.)

**Kelvin at turn-off.** Turn-off `di/dt ∝ 1/(L_g + L_k)`, demonstrated by adding the removed
inductance back into a 4-pin device's gate path and recovering the 3-pin slew rate — so the
benefit is lower total driver-loop inductance, not decoupling. **Scope: one 650 V silicon
superjunction pair in a boost converter** (Rizzo et al., 2024); whether the mechanism is
technology-dependent is untested on SiC. At turn-on the same work supports the decoupling
explanation — the mechanism differs between transitions.

## Gate drive: CSI, packages and isolation

**CSI is a picohenry budget, and the package sets it.** At 12 V / 25 A / 500 kHz, 400 pH of
high-side common-source inductance took switching loss from 0.41 W to 1.18 W — nearly 3×, ≈1.9 W/nH.
*Vendor **model-calculated** figure; only aggregate converter loss was measured to validate the
model (3.50 W calculated against 3.45 W measured).* By package: **<100 pH** stacked die,
**450 pH** clip-bond QFN, **850 pH** wire-bond QFN, **>1.5 nH** SO-8.

**Scope that ladder before using it.** TI labels the table *estimated typical values for
**low-voltage** power MOSFET packages* and says to obtain accurate CSI from the device
manufacturer. It is not a controlled same-device measurement, so read clip-vs-wire-bond as the
suggested mechanism rather than a measured delta — and do not transfer these numbers to 650 V+
SiC/GaN packages without vendor data. (TI SLPA009A, Appendix A / Table 4.)

**The 3-pin TO-247 source path is several nH; "~4 nH" is a family of non-commensurate numbers.**
Yun *calculates* 3.75/4.33 nH bond-wire networks while *measuring* 8.88 nH (SiC C2M0160120D) and
8.52 nH (Si IXFK32N100P) for the whole small-signal source path — **two-port VNA extractions read
at 400 MHz**, and those are the *fault-free baselines* in a paper whose subject is bond-wire damage
detection (they climb to 11.4 and 13.5 nH as wires are cut). The frequency is load-bearing: the
same paper notes skin effect *reduces* extracted inductance as frequency rises, so 400 MHz is a
low reading, not a gate-drive-band one. ST AN4407 computes 9.2 nH analytically, gets 8.9 nH in
HFSS for a single source bond wire and states that figure was **confirmed on the bench**, then
reports ~4 nH **only because that source path used two wires in parallel**. Infineon's separate
bench fit gives ~2 or ~4 nH effective source inductance depending on lead length. These are a
calculation, a bench-corroborated field solve, a VNA extraction at one high frequency and a fit —
not independent measurements of one quantity, and the discriminators (wire count, lead length,
extraction frequency, what the extraction boundary includes) change the answer by more than 2×.
Wolfspeed's widely-quoted "12 nH" circulates without an identified publication or method attached
and should not be used.

A 4-pin/Kelvin package **removes this inductance from the gate-drive feedback path** — it stops
being *common* to the gate and power loops. It does not physically disappear: the source
inductance remains in the commutation loop and must still be budgeted there. Writing "only a
Kelvin package removes it" invites an agent to drop it from the power-loop budget entirely.

**Gate-loop overlap: scope it by reference, not by ground.** ~1 cm² of gate-loop conductor
overlapping across 0.1 mm is ~38 pF. Two Wolfspeed sections carry this, and they are easy to
collide: **§2.3.3** requires a plane "connected to the source of the corresponding device" under
the gate drive, for shielding and a short return; **§2.3.4** forbids gate-loop/power-loop overlap
because it adds gate-to-**drain** capacitance (higher Q_GD, enhanced crosstalk, possible
shoot-through). Its worked case (Fig. 16) is a **low-side** gate loop over the switch-node plane.

Read together, the rule is *relative to the device's own source/Kelvin reference*, and the first
target is that device's own **drain**:

| device | source reference | drain | overlap to forbid |
|---|---|---|---|
| low-side | power ground | switch node | gate loop over the **switch node** (the measured case) |
| high-side | **switch node** | DC+ rail | gate loop over **DC+** |

A ground-referenced reading of "high dv/dt" gets the high-side backwards twice: it flags the switch
node, which is that device's own required reference plane, and it clears DC+, which is quiet
against ground but slews the full bus against the high-side source.

The exemption for a device's own source plane is only from **this** mechanism (added C_GD). It does
not license an extended source pour: that plane still adds C_GS, its capacitance to other domains
is common-mode current across the isolation barrier, and it does not address crosstalk into the
complementary device's gate domain. Keep it local to the gate-drive area.

Its loss figure needs care: the note prints `P = C·V²·f = 1.2 W` at 800 V / 100 kHz, but
`C·V²·f` with 38 pF evaluates to **2.44 W**; 1.2 W is `½C·V²·f`. It also prints the geometry as
"0.01 sq.mm, d = 0.1 m", which cannot give 38 pF — the figure reproduces only for ~1 cm² across
0.1 mm of FR-4 (ε_r ≈ 4.3 → 38.1 pF). So the source is internally inconsistent in both units and
convention. Use 1.2–2.4 W as an order-of-magnitude flag that overlap is a *loss* problem as well as
a noise problem; the real dissipation depends on the two conductors' differential waveform and on
how much of the stored energy is recovered, neither of which a plain `CV²f` captures.

**CMTI is not a guarantee.** Büttner et al. (2025) tested an Si8275 rated "min 200 V/ns, max
400 V/ns": it behaved correctly at +250 V/ns, but on **negative** transients **above 120 V/ns**
showed brief output deactivation — consistently, across the whole range from 25 °C down to
−194 °C (the plotted room-temperature example is −130 V/ns). CMTI is polarity-asymmetric, and a
part can misbehave well inside its rating.

Two scope limits on that work. The 2021 and 2024 "batches" are *replacement units within the same
study*, not independent reproduction. And the permanent-damage results — UCC5304, UCC5350,
UCC21551 — occurred **after repeated over-specification transients (±200 to ±300 V/ns) applied
during cryogenic cooling**, with failures appearing around −125 to −150 °C. The recovery runs are
device-specific and must not be merged: replacement **UCC5304** and **UCC5350** units held to
±130 V/ns stayed functional to −194 °C, whereas the **UCC21551**'s clean run was at only
**≈±70 V/ns** — it still showed missing and shortened pulses at −100 °C and +170 V/ns. Read
"exceeding CMTI can destroy a driver" as a real hazard, but not as a room-temperature,
single-event result, and not as a ±130 V/ns safe limit for all three parts.

**Isolation-barrier keep-out.** The requirement is a keep-out on all layers under the isolator
body. **TI SLLA284G** states it directly: the space under an isolator must be free of traces, vias
and pads on all layers, for maximum creepage. The Infineon **IRS28x7** is *not* a second vendor
agreeing — it is a 600 V bootstrap/level-shift driver with **no isolation barrier at all**; its
rule is that system ground should not sit under or near the high-voltage floating side, to limit
noise coupling. Related, but a different claim about a different part; an earlier draft of this
file presented the two as a vendor consensus differing only on mechanism, which is false.

The blanket folk version — "no copper under the isolator" extended to the whole gate-drive area —
removes the floating-side source-referenced plane that the gate loop needs for its return.

## Stack-up, capacitors and vias

- **Dielectric separation.** One simulated sweep: ~1.17 nH at 15 mil against ~0.67 nH at 5 mil, so
  thinning 15→5 mil cut L ~43 % (equivalently, 5→15 mil raises it ~75 %). A placement change in a
  separate FastHenry study moved 3.45 → 0.67 nH, an 80.6 % reduction. **Routing can beat
  stack-up; do both.** The "most field energy is in the dielectric" framing comes from one Reusch
  GaN geometry, whose thesis actually partitions leakage energy between substrate and air.
- **Return-plane continuity.** One Q3D-simulated layout showed inductor-via antipads interrupting
  a **passive shield plane** dominating the result. Thermal vias were not tested, and this is
  simulation. The continuity rule is sound engineering; this evidence is narrower than the rule.
- **Copper weight.** Skin depth is ~9–21 µm over 50–10 MHz, so 1 oz is only ~1.7 skin depths at
  10 MHz — deeper copper carries **diminished, not zero** HF current. The defensible conclusion is
  that loop inductance is set by external field geometry and extra thickness has sharply
  diminishing inductance benefit. Choose copper weight for DC resistance and heat spreading.
- **Nearest capacitor.** One CST full-wave **simulation**: 70 % of the ringing current returned
  through the nearest decoupling cap, 2.8 % through the bulk bank.
- **Mount over case code.** Zhao et al., *IEEE EMC Magazine* 9(3), give 23.4 pH/mil aligned and
  6.8 pH/mil doublet; scaled to a 62-mil via run that is ≈1.45 nH vs ≈0.42 nH. These are **model
  calculations** — the validation structures were 40 mil. Note the case-code comparison carefully:
  0805→0402 saves ~9 % in the **aligned** column (23.4→21.4 pH/mil) and **nothing** in the doublet
  column (both 6.8 pH/mil). The arrangement dominates the case code, not the reverse.
- **Vias in the loop.** 0.54 nH (via-connected, cap under the device) vs 0.84 nH (via-free top
  layer) — **FastHenry at 200 MHz**, and confounded by the µModule BGA pinout limiting top-layer
  VIN width, so not a clean vias-versus-no-vias experiment. Bhargava's 1 cm via shift moving
  0.4 nH and 5 dB is specific to his P2 image-current topology; do not generalize it to arbitrary
  commutation vias.
- **Input-ladder damping.** An all-ceramic ladder of dissimilar values has a poorly damped,
  high-Q anti-resonance between the large cap's C and the small cap's ESL; TDK's MLCC guidance
  describes low ESR producing intense anti-resonant impedance peaks, not zero damping. Keep one lossy
  (electrolytic/polymer) element, or parallel same-value parts.

> **No quantitative basis for "one via per amp" was found in the surveyed corpus** and two
> recorded English web-query axes (2026-09-04). This is a **bounded absence at low–medium
> confidence**, not a demonstration that no basis exists: **IPC-2152 was not obtained**, and it is
> the document most likely to carry a real thermal derivation. It is a *current* rule routinely cited to
> justify an *inductance* outcome, while via count near the device was measured not to affect loop
> inductance. Treat it as thermal guidance — [`THERMALS.md`](THERMALS.md) — and go to IPC-2152 if
> it is load-bearing.

## Sources

Verify against the primary before a release decision.

- Bhargava, Pommerenke, Kam, Centola, Lam, *IEEE Trans. EMC* 53(3):806–813, 2011,
  DOI `10.1109/TEMC.2011.2145421` — P1/P2/P3 areas (Table I), equal-excitation far field
  (Table III), dipole moments (Table IV), chamber peaks (Fig. 11), via-shift result.
- EPC WP010 (Reusch, 2014); EPC How2AppNote 007 (2021); D. C. Reusch, PhD dissertation, Virginia
  Tech, 2012, handle `10919/26920` — P1 "optimal layout", inner-layer return vs shield, the
  dielectric sweep. One lineage, Q3D-simulated.
- Sun, Jiang, Zhang (Analog Devices), *Analog Dialogue* RAQ Issue 207 — hot-loop arrangements,
  0.54/0.84 nH FastHenry extractions.
- onsemi AND9410/D — gate-loop and power-loop ringing extractions, gate-resistance totals, and
  the 10–20 nH typical / >20 nH shoot-through **assertion** (quote it as an assertion).
- Wolfspeed PRD-08710 — the two-operating-point implied-L spread behind the 1.5× margin.
- Wolfspeed PRD-06752, *PCB Layout Techniques for Discrete SiC MOSFETs* — gate-drive RLC model;
  also §2.3.1–2.3.4, the source of the gate–source capacitor + ~10 kΩ bleed recommendation (scoped
  to **discrete SiC MOSFETs**), gate-loop shielding, the separate gate-return rule for TO-247-3,
  and the gate/power-loop overlap case with its 38 pF / 1.2 W figure (Fig. 14).
- TI SLLA284G, *Digital Isolator Design Guide* — the isolator-body keep-out on all layers, for
  creepage. The only primary here that is actually about an isolation barrier.
- Infineon IRS28x7 datasheet — a 600 V **non-isolated** bootstrap/level-shift driver; cited only
  to record that its "no system ground under the floating side" rule is a *noise-coupling* rule
  about a different part, not corroboration of the isolation keep-out.
- Infineon AN 2013-05 (CoolMOS C7, Kelvin source) — the ±20 V driver-reference bounce, the
  R_bias–C_bias network, the `di/dt < (V_drive − V_th)/L_s` CSI limit, and the bench fit giving
  ~2–4 nH effective source inductance depending on lead length.
- Büttner et al., *Electronics* 2025, 14, 1297, DOI `10.3390/electronics14071297`, §4.4 — the
  Si8275 negative-transient CMTI result and the cryogenic over-stress damage cases.
- Yun et al., *Micromachines* 2022, 13, 1075, DOI `10.3390/mi13071075` — calculated 3.75/4.33 nH
  bond-wire networks against measured 8.88/8.52 nH total source paths. (Note: some working notes
  carry `10.3390/mi13071016` for this paper. That DOI resolves to an unrelated Ising-machine
  article — do not copy it.)
- ST AN4407 (DocID025572) — 9.2 nH analytic and 8.9 nH HFSS for a single source bond wire, and
  ~4 nH for a two-parallel-wire source path.
- Infineon IPW65R060CFD7 datasheet — `Q_rr` characterization condition and maximum commutation
  speed.
- Chen et al., *Electronics* 2024, 13, 4758 — TO-247 half-bridge 16.7–32.4 nH Q3D study.
- Zhao et al., *IEEE Electromagnetic Compatibility Magazine* 9(3), DOI `10.1109/MEMC.2020.9241560`
  — capacitor mount pH/mil figures (Table II).
- Rizzo et al., *Electronics* 2024, DOI `10.3390/electronics13204051` — Kelvin turn-off mechanism
  falsification (one 650 V Si superjunction pair, boost converter).
- TI SLPA009A — common-source inductance loss calibration and the package pH ladder.
- TDK MLCC application guidance — anti-resonance in mixed-value ceramic banks.
- W. C. Chew, *Electromagnetic Field Theory* (Purdue ECE604 notes), §32.2.2 — the magnetic-dipole
  image signs over a PEC, and their reversal relative to electric-current images.
- Any standard transmission-line text for the wide parallel-plate limit `L ≈ µ₀hl/w` (`w ≫ h`).
- Skin depth computed from `δ = √(ρ/πfµ)` with `ρ = 1.68×10⁻⁸ Ω·m`: 20.6 µm at 10 MHz, 9.2 µm at
  50 MHz, against nominal 1 oz copper of 34.8 µm.

## Bounded-absence records

Both searched on **2026-09-04** via Serper (Google), English, web-wide, no date bound. Neither
space is a closed corpus, so neither claim is a strong non-existence claim.

**No measurement behind "one via per amp", and no published criticism of it, was found in these
searches.**
Axis 1, the named rule: `"one via per amp" rule of thumb via current capacity measured IPC-2152
thermal via ampacity criticism` → **zero results**. Axis 2, the measurement method: `via current
carrying capacity measurement IPC-2152 plated through hole ampacity study results amps per via` →
only vendor calculators and blog posts (protoexpress, allpcb, queenems, Altium, Signal Integrity
Journal), clustering at ~0.8–1.5 A for a 0.3 mm / 1 oz via at ~10 °C rise — *uncited vendor
calculator output, recorded as the shape of what the search returned, not as a usable figure.*
Stopped on saturation of the second axis. **IPC-2152 is paywalled and was not obtained** —
that is the coverage gap. Confidence in absence: **low–medium**. The rule is asserted in TI SLYT614
and repeated in SLVA773 (one lineage). Note it is stated as a *current* rule while the reason for
vias in a hot loop is *inductance*: Bhargava measured that the **number** of ground vias near the
FET changed neither `L_loop` nor the far field significantly, while their **position** moved
`L_loop` by 0.4 nH and the far field by 5 dB. Treat "one via per amp" as a thermal/ampacity
heuristic only, never as a hot-loop rule.

**No published measurement establishing a maximum gate-driver-to-gate distance was found in the
surveyed corpus or these searches.**
Three axes: mechanism/rule (`gate driver distance from MOSFET rule of thumb "no measurement" OR
unsupported OR myth gate loop length limit millimetres evidence` — **the engine returned garbage
for this query**, logged); measurement/method (`how close must gate driver be to MOSFET measured
comparison gate trace length switching waveform experiment`); and layout (`gate driver placement
distance from MOSFET "gate loop" inductance nH per mm measured layout SiC evaluation`). Every
layout section of the four vendor layout primaries in the source lane was read in full: all say
"as close as possible", none names a distance tied to a measurement. Confidence in absence:
**medium**, capped there because one of the three queries ran on a degraded engine response and
because vendor app notes are an open search space. The nearest derivable limit is the
critical-damping inequality above.
