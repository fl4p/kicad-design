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
with zero declared bindings is UNVERIFIED, not a pass — set `--min-expected` to the count
the schematic declares). DNP provisions carry anchors like populated parts: this is how
PCB.md's DNP feasibility branch stays electrical, not just geometric.

Default budgets (pad-to-pad; tighten freely, loosen only with a recorded reason):

| satellite | anchor | default budget |
|---|---|---|
| RC snubber string (per switch) | that switch's D-S / C-E pads | 15 mm, same layer as the loop |
| gate series resistor | the gate pad | 20 mm, routed with its source/kelvin return |
| bootstrap capacitor | driver VB-VS pins | 10 mm |
| HF loop / DC-link film caps | the bridge rail pads | 15 mm |
| IC decoupling | the supply pin it serves | 5 mm |
| sense-divider top / feedback tap | the tapped node | 20 mm, routed away from switch nodes |
| kelvin shunt sense pair | the shunt pad heads | from the pad heads, differential |

## Native DRC tier (KiCad 10 rules language)

Encode the same intent redundantly in the project DRC where possible, so violations land
in the authoritative DRC enumeration that [`PCB.md`](PCB.md) already governs:

- Put snubber/gate nets in named netclasses with a routed-length ceiling:
  `(rule snb_len (condition "A.hasNetclass('SNB')") (constraint length (max 20mm)))` —
  violations appear as `length_out_of_range`.
- Draw a named rule area over the bridge and assert membership:
  `(rule snb_room (condition "A.Reference == /^(RS|CS)\d/"))` with
  `(constraint assertion "A.intersectsArea('bridge_room')")` — prefer
  `intersectsArea()` over `enclosedByArea()` (the manual notes it is faster); use
  `enclosedByArea()` when full containment is the requirement.

Function names verified against the installed KiCad 10.0.5 manual; re-verify the exact
rule syntax with a deliberately-failing probe rule before trusting a new rule file
(a rule that never fires is indistinguishable from a rule that passes).

## Creepage and clearance: decide with the framework, not a single number

A spec like "3 mm on all HV nodes" usually encodes three things at once — the standards
band, overshoot headroom, and environment margin. Untangle them before trading layout
against it:

1. **Standards floor.** Find the working-voltage band in IPC-2221B Table 6-1 and read
   *both* columns: external uncoated (B2) and polymer-coated (B4) differ by several×.
   Values must be re-verified against the actual table at release time — never propagate
   band values from memory or from this file into a release decision.
2. **Overshoot.** Switching nodes ring above the bus. Bound the real peak (measure or
   clamp) and band on the peak, not the DC bus. A wider gap "for overshoot" that jumps
   an entire band is margin, not code — record it as margin.
3. **Environment.** Dust and condensation (mobile rigs, unsealed enclosures) push toward
   pollution-degree-3 creepage, which is several× the PD2 figure. **Conformal coating
   beats bare distance here**: it both moves the board to the coated column and removes
   the contamination mechanism that motivated the wide gap — bare FR-4 at 3 mm can still
   track when damp and dirty; a coated 1.5 mm gap cannot get damp. Milled slots raise
   creepage without board growth where coating is not wanted.
4. **Module-interface floors.** A purchased module's own pin pitch may put HV and SELV
   at fractions of a millimetre (a 2.54 mm header interleaving gate drive and logic).
   That floor is *inherent to the chosen interface*: document it as a residual bound to
   the module decision, never silently widen the spec elsewhere to compensate, and never
   present the board-level figure as if the module met it too.

Record the decision per net-class pair with measured minimum gaps per layer, the chosen
column (coated/uncoated), and who accepted any figure below the untangled spec.

## Classify HV domains by DC potential, not by name

Creepage classes follow the actual potential relative to the touchable reference —
bootstrapped high-side gate drive rides at bus potential (HV) while low-side gate drive
and the bus return sit at reference potential, whatever their names suggest. One measured
run first classified low-side gate nets as HV and blocked routing at an unmeetable
clearance; reclassifying by DC potential dissolved the blockage. Sense-divider midpoints
are elevated (HV-side) even though they feed logic.

## Power loop and placement

Minimal loop first: DC+ → high-side → output → low-side → DC−, with the DC-link/loop
caps at the bridge, then everything else placed around that loop — never a floorplan
that bins parts by category (all axials in a row, all discs in a row): category binning
is exactly how satellites drift from their anchors. Devices switching an inductive bus
must be rated for bus + real overshoot with margin: one measured failure put devices
rated ~1.2× the bus voltage on the bridge and lost three of four to avalanche under a
load transient, while a co-packaged device rated ~3.8× survived. Derate meaningfully or
verify the clamp path.
