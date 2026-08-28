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
and empty entries, delimiter-bearing board fields, and declared-empty selectors), but
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

## Power loop and placement

Minimal loop first: DC+ → high-side → output → low-side → DC−, with the DC-link/loop
caps at the bridge, then everything else placed around that loop — never a floorplan
that bins parts by category (all axials in a row, all discs in a row): category binning
is exactly how satellites drift from their anchors. Devices switching an inductive bus
must be rated for bus + real overshoot with margin: one measured failure put devices
rated ~1.2× the bus voltage on the bridge and lost three of four to avalanche under a
load transient, while a co-packaged device rated ~3.8× survived. Derate meaningfully or
verify the clamp path.
