# Power-electronics layout doctrine

Read this file when the board switches meaningful power: an inverter or converter stage,
a motor drive, HV rails above SELV, or any layout carrying gate drive, snubbers, or
current sense. The mechanisms referenced here (anchor bindings, the proximity guard, the
courtyard disposition) are domain-general and live in [`PCB.md`](PCB.md) and
[`scripts/`](scripts/README.md); this file supplies the power-specific constraints and
decision frameworks. Everything below is project-agnostic; project numbers override these
defaults only with a recorded reason.

## Functional satellites: declare the binding when you know it

A functional satellite is a part whose electrical value depends on *where it sits
relative to a named partner*. The knowledge of that partner exists at capture time; a
placement pass hours later will not re-derive it — one measured run placed four snubber
strings ~90 mm from the devices they snub, stated the correct function of the part while
looking at the wrong location, and passed every mechanical gate for thirty hours. Do not
rely on attention; record the binding.

At capture/generation time, emit on every satellite footprint:

    Anchor  = <partner refdes>     MaxDist = <mm budget>

At layout time, run `scripts/kicad_functional_proximity.py` (fail-closed; a vacuous run
with zero declared bindings is UNVERIFIED, not a pass). Release invocations pass
`--expect=ref:anchor:maxdist[:selfpad:anchorpad],...` — one entry per binding. A bare
refdes list is not enough and is refused: the on-board binding fields are mutable
layout-side state, and a ref-set expectation would still trust a `MaxDist` inflated or
an `AnchorPad` deleted after capture (measured FAIL→PASS flips). The guard refuses any
binding differing from the expectation (selectors byte-exact, an empty selector field
meaning *absent*, maxdist as a parsed number — `15` and `15.0` are the same budget),
refuses `--default-max-mm` alongside `--expect`, and without any expectation cannot see
a binding deleted before the run. The **emitter must refuse to encode** a binding whose
fields contain `:` or `,`, whitespace at what would become an entry's outer edge
(the start of the ref, the end of a final selector), or a present-but-empty
`SelfPad`/`AnchorPad` property (it serializes identically to an absent one): the flat
option string cannot express these unambiguously (the guard refuses whitespace-edged
and empty entries, delimiter-bearing board fields, and — under `--expect` —
declared-empty selectors; without an expectation an empty selector grades byte-exactly
against empty-numbered pads, which KiCad thermal pads really use), but
a naive capture-side encoding can authenticate a differently-shaped board (measured),
and no parser of the finished string can detect that afterwards. Where pin identity
matters (a decoupling cap's supply pin, a gate pad), add `SelfPad`/`AnchorPad` selectors;
a bare nearest-pad distance can otherwise pass a part parked near the wrong pin. DNP
provisions carry anchors like populated parts: this is how PCB.md's DNP feasibility
branch stays electrical, not just geometric.

The guard is a **gross-misplacement tripwire, not an acceptance limit**: it catches the
90-mm-from-its-switch class of defect, and a PASS says nothing about loop area, return
paths, or routed length — keep the current-loop audit ([`PCB.md`](PCB.md)) and the DRC
length tier below in force beside it. Tripwire budgets (pad-to-pad; tighten freely;
these are triage numbers, not derived limits — real limits come from the project's edge
rates, allowed loop inductance, and package geometry):

| satellite | anchor | tripwire budget |
|---|---|---|
| RC snubber string (per switch) | that switch's D-S / C-E pads | 15 mm, same layer as the loop |
| gate series resistor | the gate pad (`AnchorPad`) | 20 mm, routed with its source/kelvin return |
| bootstrap capacitor | driver VB-VS pins | 10 mm |
| HF loop / DC-link film caps | the bridge rail pads | 15 mm |
| IC decoupling | the supply pin it serves (`AnchorPad`) | 5 mm |
| sense-divider top / feedback tap | the tapped node | 20 mm, routed away from switch nodes |
| kelvin shunt sense pair | the shunt pad heads | from the pad heads, differential |

## Native DRC tier (KiCad 10 rules language)

Encode the same intent redundantly in the project DRC where possible, so violations land
in the authoritative DRC enumeration that [`PCB.md`](PCB.md) already governs:

- Put snubber/gate nets in named netclasses with a routed-length ceiling:
  `(rule snb_len (condition "A.hasNetclass('SNB')") (constraint length (max 20mm)))` —
  violations appear as `length_out_of_range`.
- Draw a named rule area over the bridge and assert membership (the rules language uses
  wildcard string compare, not regex, and `Reference` is inherited by pads and graphics,
  so scope to the footprint object):

      (rule snb_room
        (condition "A.Type == 'Footprint' && (A.Reference == 'RS*' || A.Reference == 'CS*')")
        (constraint assertion "A.intersectsArea('bridge_room')"))

  Prefer `intersectsArea()` over `enclosedByArea()` (the manual notes it is faster); use
  `enclosedByArea()` when full containment is the requirement.

Syntax verified against the installed KiCad 10.0.5 manual; still re-verify any new rule
file with a deliberately-failing probe rule before trusting it (a rule that never fires
is indistinguishable from a rule that passes).

## Creepage and clearance: decide with the framework, not a single number

A spec like "3 mm on all HV nodes" usually encodes three things at once — the standards
band, overshoot headroom, and environment margin. Untangle them before trading layout
against it:

1. **Standards floor.** Identify the standard that actually binds the product first —
   for anything with a safety-isolation function that is the applicable product/insulation
   standard (pollution degree, material group/CTI, overvoltage category, clearance and
   creepage as separate quantities), not IPC-2221, which is a generic printed-board
   design standard. Within IPC-2221's own scope, read the current binding revision's
   table for the working-voltage band and note the column scopes: the external
   uncoated vs polymer-coated columns apply to board conductors, and assembled component
   leads/terminations fall under different categories (see also
   [`FOOTPRINTS.md`](FOOTPRINTS.md) on never reusing a remembered table as a current
   verdict). Never propagate band values from memory or from this file into a release
   decision.
2. **Overshoot — but into the right quantity.** Switching nodes ring above the bus.
   Bound the real peak (measure or clamp), then apply it where the binding standard
   says it applies: clearance-type limits follow peak/transient voltages, creepage-type
   limits follow the RMS/DC working voltage — resolve the two separately rather than
   widening one figure "for overshoot". Extra distance beyond the resolved requirement
   is margin, not code — record it as margin.
3. **Environment.** Dust and condensation (mobile rigs, unsealed enclosures) push the
   pollution-degree classification up, and higher pollution degrees carry materially
   larger creepage requirements — read the factor from the binding standard's table for
   the actual voltage and material group, never from memory. A **qualified** conformal
   coating (the standard's permanent-coating construction, applied and inspected to its
   process requirements — not any sprayed acrylic) can both change the applicable
   construction and suppress the contamination mechanism that motivated the wide gap;
   it *reduces* the tracking risk, it does not abolish it (coverage defects, trapped
   ionic contamination, permeability, and mechanical damage remain), so coating plus a
   moderate gap is a trade to record, not a free pass. A milled slot can raise the
   effective creepage path without board growth, but whether and how a slot is credited
   (minimum slot width included) is construction- and standard-dependent — verify
   before relying on it.
4. **Module-interface floors.** A purchased module's own pin pitch may put HV and SELV
   at fractions of a millimetre (a 2.54 mm header interleaving gate drive and logic).
   For *functional or project-derived* specs, document that floor as a residual bound to
   the module decision, never silently widen the spec elsewhere to compensate, and never
   present the board-level figure as if the module met it too. If a **binding safety
   requirement** applies, a module below it is not a documentable residual — it
   disqualifies the module or blocks release.

Record the decision per net-class pair with measured minimum gaps per layer, the
constructions relied on (coating credited or not, slots credited or not), and who
accepted any figure below the untangled spec.

## Classify HV domains by inter-domain voltage, not by net name

Insulation requirements between two domains follow the binding standard's separate
quantities — clearance-type limits from the peak/transient voltage between them,
creepage-type limits from the RMS/DC working voltage between them, each evaluated under
normal operation and the relevant fault conditions, together with the insulation
function — not net names, and not nominal DC level alone. Within a single galvanic system, bootstrapped high-side gate
drive rides at bus potential relative to the bus return while low-side gate drive sits
at return potential, whatever the names suggest: one measured run classified low-side
gate nets as HV and blocked routing at an unmeetable clearance until it reclassified by
actual potential difference. But the *reference itself* must be established, not
assumed — in an offline or floating system, the bus return and everything referenced to
it can be hazardous relative to earth or touchable circuits, and that boundary carries
its own (usually stricter) insulation requirement. Sense-divider midpoints are classified by
calculation, not by the logic they feed: the divider's output tap sits near its
reference in normal operation (a 170 V → 3.3 V divider output is ~3.3 V), while nodes
*within* the divider string are elevated even in normal operation — and the binding
standard's single-fault analysis (top element short or open, source impedance, any
redundancy in the chain) decides what the tap must be insulated for, since a relevant
fault can put it at bus potential.

## Power loop, placement and budgets

Minimal loop first: DC+ → high-side → output → low-side → DC−, with the DC-link/loop
caps at the bridge, then everything else placed around that loop — never a floorplan
that bins parts by category (all axials in a row, all discs in a row): category binning
is exactly how satellites drift from their anchors. Devices switching an inductive bus
must be rated for bus + real overshoot with margin: one measured failure put devices
rated ~1.2× the bus voltage on the bridge and lost three of four to avalanche under a
load transient, while a co-packaged device rated ~3.8× survived. Derate meaningfully or
verify the clamp path.

[`PCB.md`](PCB.md)'s loop-inductance backstop gates a board against numeric nH budgets and refuses
to invent one. Enumerate, per switching cell:

| loop | endpoints | why it has a budget |
|---|---|---|
| commutation ("hot") loop | DC-link cap pads → high-side → low-side → back to the cap | overshoot at turn-off rides on `L·di/dt` |
| gate loop, per device | driver output → gate pad → source/Kelvin → driver return | sets ringing and damping |
| common-source inductance (CSI) | shared power/gate source path | caps achievable `di/dt` regardless of driver strength |
| snubber and other mount loops | the `probe_ports` REF.PAD pairs | a snubber works through its own mount inductance |

**Allocate the voltage first, then divide.** Do not derive `ΔV` from a measured overshoot that
already contains the `L·di/dt` term you are budgeting — that is circular:

    ΔV_L      = V_rated,derated − V_bus,max − (clamp, package and other transient allocations)
    L_accept ≤ ΔV_L / (1.5 · |di/dt|_max)

The margin **divides the budget**; it never multiplies the allowed inductance. Use your own
measured or bounded instantaneous `di/dt`. Keep the allocation an explicit ledger: each subtracted
term must be a positive, incremental, non-overlapping voltage share evaluated at the same
worst-case corner, `ΔV_L` must come out positive, and `L_accept` covers only the inductance not
already represented by the package allocation. A clamp's absolute voltage is not a term you can
subtract alongside `V_bus,max` — convert it to the incremental share it actually consumes. The 1.5× default is calibrated on one module at two
operating points ([`LOOPS.md`](LOOPS.md)) — a reasonable default, not a universal bound.

- **A datasheet's `Q_rr` characterization condition is not a slew rate**, and it can be far slower
  than the device's own limit — one 650 V part characterizes at 100 A/µs while specifying a
  1300 A/µs maximum, 13× faster. Substitute a real number and re-derive.
- **"Layout is not the constraint for slow Si" is a scenario result, not a technology law.** The
  same part yields ~1200 nH from its characterization figure and ~92 nH from its own maximum
  commutation speed — and 92 nH is reachable by a sloppy TO-247 layout. Derive before concluding
  layout is free.
- **Find the package share before optimizing copper.** `L_loop = L_package + L_PCB` is bookkeeping,
  not an identity — do not add separately-quoted package, CSI, lead and PCB figures or assign
  percentage shares across sources, since they rarely share extraction boundaries or frequency.
  But the bound decides how much effort layout deserves: package-dominated specimens leave layout
  little to win (parallel devices or change the package instead), while chip-scale LGA is the
  opposite regime. Figures and their scope: [`LOOPS.md`](LOOPS.md).

## Name the loop arrangement before quoting any number about it

Three vocabularies are in circulation and **two use "vertical" for opposite physical objects**.
Normalise to the arrangement, never the word; getting this backwards inverts the design rule.

| | arrangement | Bhargava | EPC / Reusch | ADI |
|---|---|---|---|---|
| **P1** | **Thin z-sandwich.** Out along the component layer, return in the plane on the *immediately adjacent* layer. Enclosed surface is **vertical**: path length × dielectric thickness. Magnetic dipole lies **in** the board plane. | "vertical loop" | **"optimal power loop"** | *not built* |
| **P2** | **Flat in-plane loop.** Go and return both on the component layer, returning through laterally-placed caps. Enclosed surface is **horizontal**; dipole **normal to** the board. | "flat horizontal loop" | "lateral power loop" | "Horizontal Hot Loop" |
| **P3** | **Through-board loop.** Devices on one face, capacitors on the other; the loop spans the board thickness. | "two-sided vertical loop" | **"vertical power loop"** | "Vertical Hot Loop 1/2" |

ADI's naming follows EPC's; Bhargava's is the odd one out, and **ADI built no P1 variant**. Before
citing any ranking from this literature, check [`LOOPS.md`](LOOPS.md) for which arrangement was
actually built, whether the figure is measured or simulated, and which sources are one lineage.

## Enclosed area is necessary but not sufficient — orientation decides the field

The magnetic dipole is a **vector**, `m⃗ = I·A·n̂`; area alone is a scalar. Over a close,
continuous, electrically large, good-conductor plane, image theory gives **opposite** results for
the two orientations:

- dipole **normal** to the plane → **antiparallel** image → largely cancels (this is P2);
- dipole **tangential** to the plane → **parallel** image → **reinforced** (this is P1).

These are the reverse of the electric-current image rules, which is where the sign is easy to
invert. P1 is not merely "left uncancelled" — its image adds. Two qualifiers: a finite, split, or
heavily perforated plane delivers neither behaviour reliably; and the image is an **equivalent
source that replaces the plane**, not an extra return current to add on top of a solved plane
current — do not double-count it in a full-wave or plane-current model.

**Measured, one specimen:** across an 8× enclosed-area increase *accompanied by a 90°
reorientation*, inductance moved only 2.50 / 2.41 / 2.70 nH (P1 / P2 / P3 — a 12 % spread) while
the **largest-area loop radiated ~10 dB less** than the smallest at equal excitation. Those areas
are true flux-cutting surfaces, not top-view footprints: P1's 4.5 mm² is 18 mm × 0.25 mm, the
0.25 mm being the dielectric. Scope, tables and the sources: [`LOOPS.md`](LOOPS.md).

Required action:

- **Specify orientation and area, never area alone.** An acceptance criterion written as a mm²
  budget cannot express the term that separated those loops by 10 dB.
- **Inspect the loop side-on in the 3D viewer** — to read which way its plane faces. The 2D editor
  shows exactly the projection that discards it.
- Remember what area also omits: **conductor width**. In the wide parallel-plate limit
  (`w ≫ h`, return plane wider than the strip, negligible fringing, preferably `l ≫ h`)
  `L ≈ µ₀·h·l/w`, so widening the conductor lowers inductance while leaving `A = l×h` unchanged.
  *Model, not a measurement.*
- Within the area, path length is set by where the devices and capacitors must sit. **Separation to
  the return plane is the factor you can actually move.**
- **A loop-inductance PASS is not an EMI verdict.** `loop_inductance_guard.py` gates `L`; the
  measurement above is precisely a case where equal `L` and opposite radiated outcomes coexist.
  Treat the nH budget and the field behaviour as two requirements.

Do **not** read the inductance half of that result as "area does not affect L" — it shows the
package swamped an 8× area difference in one package-dominated specimen.

**If you have one return plane and must choose:** in the package-limited regime (SO-8, POWER56,
D²PAK, TO-247 — verify by comparing the package figure against your PCB estimate) P1 buys almost
no inductance and carries the reinforced dipole, so **prefer P2 over a close, solid plane**. In the
PCB-limited regime (LGA, chip-scale GaN) P1 cuts inductance ~3× (*simulated*, one lineage) — take
P1 and make the prepreg as thin as the fab allows, **accepting an unquantified EMI trade**: the
~10 dB ordering was measured on package-limited boards and has not been tested where P1's
inductance advantage actually appears ([`LOOPS.md`](LOOPS.md)).

## Budget the gate loop too — it may be the larger one

On one 30 V synchronous-buck VRM the gate loops extracted at 20.3 and 13.8 nH against a power loop
of 1.51 nH. That ordering is not established for high-voltage TO-247/SiC layouts, but it is a
warning worth carrying: the loop everyone optimizes may be the one that was already fine. Extract
the gate loop alongside the commutation loop rather than assuming it.

- **Size `R_g` for critical damping:** `R_total,crit = 2·√(L_g/C_eff)`. `R_total` includes the
  external resistor, the driver's output impedance, the device's internal and package gate
  resistance, and trace resistance — and differs between turn-on and turn-off. Evaluate `C_eff` at
  the actual bias (`C_iss` is a bias-dependent small-signal substitute for `C_GS`, worst through
  the Miller interval). Inverted it bounds the loop — illustratively, `C = 1 nF` with
  `R_total = 5 Ω` gives `L_g ≤ 6.25 nH`. Treat that as a linearized estimate, not a ceiling.
  A <10 nH gate loop is a reasonable synthesis target, and no measured driver-to-gate distance
  limit was found in the surveyed corpus. onsemi puts typical **VRM** gate-loop inductance at
  10–20 nH depending on layout and states that **above 20 nH could cause a shoot-through problem**
  — a vendor assertion about a 30 V synchronous buck, not a measured threshold, an observed onset,
  or a figure established for high-voltage SiC/GaN gate loops ([`LOOPS.md`](LOOPS.md)).
- **CSI caps `di/dt` regardless of driver strength:** `di/dt_max = (V_drive − V_th)/L_s`. Once
  common-source inductance dominates, a stronger driver buys nothing — budget CSI in **picohenries**
  and treat package construction as a dominant lever. The published clip-bond/wire-bond ladder is
  *estimated typical* values for **low-voltage** MOSFET packages; get CSI for your actual part from
  its manufacturer ([`LOOPS.md`](LOOPS.md)).
- **A Kelvin/4-pin package plus a sloppy gate loop wins little at turn-off.** Turn-off
  `di/dt ∝ 1/(L_g + L_k)`, so the benefit is lower total driver-loop inductance rather than
  decoupling as usually claimed. Fit the 4-pin part **and** keep the gate loop tight. Demonstrated
  on one 650 V Si superjunction pair; at turn-on the decoupling explanation does hold, and the
  mechanism differs between transitions.
- Put a **source-referenced** plane — that device's source, not generic ground — under the
  gate-drive traces (Wolfspeed: a plane "connected to the source of the corresponding device").
  **For a high-side device that plane *is* the switch node**, and it is required there. Keep it a
  *local* plane under the gate-drive area rather than an extended pour: it still adds C_GS, and
  its capacitance to other domains is common-mode current across the isolation barrier. Route the
  Kelvin trace as a tight pair with the gate trace, returning only to the driver's V_EE/COM.
- Keep **zero overlap** between a gate loop and any copper that swings materially **relative to
  that device's own source/Kelvin reference** — *not* "relative to ground". The measured mechanism
  is added gate-to-**drain** capacitance (higher Q_GD, worse crosstalk), so the first target is
  always **that device's own drain**:
  - **low-side** gate loop over the **switch node** — Wolfspeed's worked case;
  - **high-side** gate loop over the **DC+ rail**. DC+ is quiet against ground but slews the full
    bus against the high-side source, so a ground-referenced reading of "high dv/dt" misses it.

  Keep the gate loop clear of the power loop and of the complementary device's gate domain too.
  (~1 cm² across 0.1 mm is ~38 pF; at 800 V / 100 kHz that is 1.2 W read as ½CV²f or 2.4 W as
  CV²f — an order-of-magnitude flag, not a calibration; see [`LOOPS.md`](LOOPS.md).)
- **"No copper under the isolation barrier" is narrower than the folk version, and the blanket
  form costs the gate loop its return.** Keep the barrier keep-out on all layers **under the
  isolator body** (TI SLLA284G, for creepage); on the *floating side*, a source-referenced plane
  under the gate traces is required, not forbidden.
- **Expect the driver reference to bounce ±20 V or more against power ground.** Use an isolated or
  coreless-transformer driver with an R_bias–C_bias network rather than assuming a quiet reference.
- **Isolated drivers can fail below their datasheet CMTI.** One rated "min 200 V/ns" malfunctioned
  on *negative* transients above 120 V/ns at room temperature — CMTI is polarity-asymmetric. Treat
  the rating as a specification to design margin against, not a guarantee ([`LOOPS.md`](LOOPS.md)).
- **Fit a gate–source capacitor only on Kelvin/4-pin (KS-pin) packages, never on a 3-pin TO-247** —
  on a 3-pin part it sits across the common-source inductance and slows turn-off, and Wolfspeed
  explicitly does not recommend one for TO-247-3.
- **Fit the ~10 kΩ gate–source bleed resistor by default.** This is a *separate*, unconditional
  rule — not part of the capacitor recommendation above. Wolfspeed calls it **critical** to
  discharge the gate if the MOSFET is disconnected from its driver, and warns that **false turn-on
  may occur** without it. Omit it only for an explicitly verified passive off-state path that does
  the same job; the value stays device- and driver-specific.
- **A 3-pin TO-247 carries several nH of source inductance that no layout removes** (~4 nH is the
  common figure; extractions of the whole source path run to ~8–9 nH). A 4-pin/Kelvin package
  removes it **from the gate-drive feedback path**, not from the board — it stays in the
  commutation loop and must still be budgeted there. Budget CSI against the package, not the
  copper ([`LOOPS.md`](LOOPS.md)).

## Stack-up, return path and the capacitors in the loop

- **Set the layer-2 dielectric before routing anything.** Thinning it is a large, cheap lever —
  one simulated sweep cut L ~43 % from 15 mil to 5 mil — but not a superlative: a placement change
  in another study moved 3.45 → 0.67 nH. Do both.
- Route go and return on the two layers sharing a **thin prepreg**, never a pair straddling the
  thick core (on a symmetric 1.6 mm 4-layer: L1↔L2, never L1↔L4).
- Use inner layer 1 as the **return path**, not as a passive shield — same geometry, materially
  lower inductance and no shield eddy loss.
- **Do not break the return plane under the loop.** Inductor vias, thermal vias or a plane split
  inside the loop footprint force the return current to detour. This is the power-electronics case
  of [`PCB.md`](PCB.md)'s return-continuity requirement; the supporting study is simulated and
  narrower than the rule ([`LOOPS.md`](LOOPS.md)).
- **Copper weight is a weak loop-inductance lever.** At 10 MHz 1 oz copper is only ~1.7 skin
  depths, so extra thickness carries diminished — not zero — HF current, and loop inductance is set
  by external field geometry regardless. Choose copper weight for DC resistance and heat spreading,
  where [`PCB.md`](PCB.md)'s copper-capacity backstop applies.
- **The nearest capacitor carries the loop** (70 % vs 2.8 % for the bulk bank in one full-wave
  simulation). Put the smallest, lowest-ESL ceramic hard against the high-side drain / low-side
  source, bulk behind it — the HF power case of [`PCB.md`](PCB.md)'s "decoupling is a current loop".
- **Judge a capacitor by its mount, not its case code**, and note the direction: in one pH/mil
  model a doublet arrangement beat an aligned one ~3.4×, while dropping 0805→0402 saved ~9 % in the
  aligned arrangement and **nothing** in the doublet one. Interleave the +/− via pairs; never run
  both power vias on one side.
- **Vias in the commutation path are not disqualifying.** Judge the loop, not the vias. Via
  *position* mattered where it was tested and via *count* near the device did not — both results
  are topology-specific ([`LOOPS.md`](LOOPS.md)).
- **Damp the input ladder deliberately.** An all-ceramic ladder of dissimilar values has a
  poorly damped, high-Q anti-resonance between the large cap's C and the small cap's ESL — low ESR
  produces an intense impedance peak, not literally zero damping. Keep one lossy
  (electrolytic/polymer) element, or parallel same-value parts.
