# PCB thermal design and validation

Read this file only when heat or temperature is a design variable: component or fault
dissipation, junction/case temperature, current-density heating, exposed pads or thermal vias,
heat-spreading copper, thermally matched placement, temperature-dependent accuracy, enclosure
ambient, or transient thermal response. Ordinary low-power placement and routing do not need it.

The source-authority, datasheet, verification and guard rules in [`SKILL.md`](SKILL.md) still
apply. Board layout mechanics live in [`PCB.md`](PCB.md), `pcbnew` fill behavior in
[`PCBNEW.md`](PCBNEW.md), land construction in [`FOOTPRINTS.md`](FOOTPRINTS.md), and calibrated
checks in [`GUARDS.md`](GUARDS.md).

## Contents

- [Decide whether thermal analysis is in scope](#decide-whether-thermal-analysis-is-in-scope)
- [Build the thermal ledger](#build-the-thermal-ledger)
- [Treat fault and pulse power as separate cases](#treat-fault-and-pulse-power-as-separate-cases)
- [Carry temperature through the accuracy budget](#carry-temperature-through-the-accuracy-budget)
- [Place sources, sinks and sensitive parts deliberately](#place-sources-sinks-and-sensitive-parts-deliberately)
- [Design the whole heat path](#design-the-whole-heat-path)
- [Prove thermal barriers interrupt the dominant path](#prove-thermal-barriers-interrupt-the-dominant-path)
- [Handle exposed pads, vias, paste and zones together](#handle-exposed-pads-vias-paste-and-zones-together)
- [Guard thermally matched copper](#guard-thermally-matched-copper)
- [Validate models and the finished board](#validate-models-and-the-finished-board)

## Decide whether thermal analysis is in scope

Do not add thermal ceremony merely because every part has an operating-temperature rating.
Load this workflow when at least one answer is yes:

- Can normal, startup, shutdown or fault dissipation set a component or copper temperature?
- Does resistance, leakage, offset, gain or timing change materially with temperature?
- Does the board, enclosure, airflow, chassis or cable carry a specified share of the heat?
- Is an exposed pad, copper island, via array or matched pour part of the heat path?
- Can a gradient between nominally matched parts change accuracy or current sharing?
- Does a pulse or duty cycle require transient thermal impedance rather than steady state?

If none applies, keep the normal electrical, manufacturing and safety checks and do not load the
rest of this file.

## Build the thermal ledger

Create one executable row per heat source and operating state. Record at least:

| field | required content |
|---|---|
| state | normal, overload, startup, shutdown, reverse power, or accepted fault |
| dissipation | worst-case value and the electrical corner that produces it |
| time | continuous duration or pulse width, repetition and duty cycle |
| environment | maximum local ambient, enclosure/airflow assumption and nearby sources |
| heat path | junction-to-case/pad/board/air/chassis path actually used |
| model | cited thermal resistance, characterization board, transient curve or measured value |
| limit | design temperature or derated stress limit, not only destruction rating |
| margin | temperature and stress headroom with units |

Compute dissipation from the worst credible electrical corner. For resistive paths use the form
that exposes the controlling tolerance (`I²R`, `V²/R`, or `VI`) and include resistance change when
it materially affects the result. Keep simultaneous sources simultaneous; a per-part calculation
with every neighbor assumed cold is not a board thermal case.

Do not apply a package `θJA` blindly. It is characterized under a stated board and environment,
often by measurement or simulation, and is not an intrinsic part constant. Use it only when that
construction is a defensible proxy. Do not
substitute `ψJT`, `ψJB`, or a transient impedance curve for a steady-state junction-to-ambient
resistance. State which metric is used and why it maps to the actual path.

Use maximum local ambient, not room temperature by habit. Include enclosure rise, sunlight,
neighbor heating, airflow loss and fan-off states when they are in the accepted envelope. Apply
the project's chosen derating before comparing against package or junction ratings.

## Treat fault and pulse power as separate cases

Size protection at the maximum credible fault, not at the normal signal maximum. Include supply
tolerance and transients, component tolerance, working-voltage limits, continuous and pulse
power, ambient derating and the protection part's failure mode. Working-voltage and pulse limits
remain independent even when average power passes.

A real limiter calculation shows why the distinction matters: `110² / 47 kΩ = 0.257 W`, already
103% of a nominal 0.25 W part before ambient or reliability derating. The electrical topology
allowed 47 kΩ only after its pull-up moved upstream; the thermal result then required a larger
package. State the resistor series behind a package rating—nominal 1206 ratings vary widely—and
check working voltage separately.

For pulses, use the vendor pulse-load/energy curve or transient thermal impedance at the actual
pulse width and repetition. Average power alone can miss a peak-film, die or junction failure;
steady-state nameplate alone can reject a safe short pulse. Preserve both results in the ledger.

When two constraints are evaluated at different temperatures, keep separate hot and cold corners.
A resistor can establish one bound cold while a clamp establishes another hot; collapsing both
onto one junction temperature can make a feasible topology appear impossible or hide an empty
feasible interval.

## Carry temperature through the accuracy budget

Build temperature and ageing into the same corner ledger as supply, passive tolerance and device
min/max specifications. Calibration removes only the terms it actually observes.

**A calibrated DC error can retain its temperature coefficient.** Copper is about
`+3930 ppm/K`. In one reference return, 259 µV of uncancelled drop therefore produced about
1.0 µV/K at the buffer and, through the following ×10 stage, 10.2 µV/K at the output. Carry the
error through downstream gain before applying the output budget; applying the coefficient to one
unamplified leg under-reports drift.

Do not discard a thermal term because it lies below a noise-band lower limit. Slow temperature
drift is not 0.1–10 Hz noise, but it still consumes DC accuracy over the specified time and
temperature range. Report static error, calibrated residual and temperature coefficient as
separate quantities, and record why thermally important copper was widened or rerouted.

Validate matching over gradient as well as uniform temperature. Two components with identical
tempco can still differ when one sits near a heat source. When the accuracy claim depends on
thermal symmetry, make source distance, orientation and intervening copper explicit artifact
checks rather than placement prose.

## Place sources, sinks and sensitive parts deliberately

Map heat sources, intended sinks and temperature-sensitive parts before routing. Repeat the map
for operating states that move the dominant source. Keep precision references, matched networks,
oscillators and high-impedance leakage-sensitive nodes away from gradients their budgets do not
include.

Use placed pad geometry, not footprint origins, when comparing exposure or symmetry. A mounting
lug with an origin 1.5 mm off its pad cluster caused an origin-based review to infer the wrong
symmetry axis and identify the wrong matched network as exposed. The copper and package interface
are the physical locations.

Do not claim that mirrored coordinates prove equal temperature. Airflow, board edges, slots,
connector metal, chassis contacts, unequal copper connectivity and nearby sources can break the
thermal mirror. Treat geometric symmetry as an input to the thermal argument, not its conclusion.

## Design the whole heat path

A trace width is not a heat path. For high-current or thermally load-bearing copper, include
neckdowns, connector entries, layer changes, copper weight, parallel pours, via arrays and the
distance to spreading area. Make this geometry critical and generator/manual-owned under
[`PCB.md`](PCB.md)'s routing-ownership contract; a DRC-clean imported route is insufficient.

Write the stackup and copper weights explicitly. A thermal estimate tied to unnamed default foil
or dielectric is not reproducible. Check current sharing between layers and through every via
transition; a large plane behind one narrow entry is still bottlenecked by the entry.

Total copper area is only a scalar proxy. Heat spreading and transient response depend on where
copper is removed, whether it remains connected to the source, neck width and boundary location.
Do not turn an area percentage into a thermal limit without a temperature or resistance budget
and a sensitivity to removal at the relevant location.

## Prove thermal barriers interrupt the dominant path

A routed slot, cutout, neck or sensor tab is geometry, not evidence of thermal isolation. For each
claimed barrier, name the source region, protected component or region, dominant source-to-target
path, allowed bridges and required temperature or thermal-resistance result. The barrier must lie
topologically between the source and target: a slot parallel to the dominant path, or a sensor at
the source-side root of a nominal tab, does not interrupt that path.

Check the saved and reparsed board across the complete construction. Include filled copper on every
layer, tracks, pads, vias, laminate bridges, connectors, shields, fasteners and enclosure contacts
that can bypass the intended neck. Establish that the protected component is on the isolated side,
then quantify the remaining copper and laminate cross-section, path length and any parallel bypass.
Slot count, dimensions and coordinates establish only that the cutouts can be fabricated.

Give this functional claim its own artifact guard and stable failure ID. Calibrate it with an
appearance-preserving known-bad mutation: retain legal, closed slots with the expected count and
dimensions while moving the target to the source side, rotating or relocating the barrier so it no
longer crosses the path, or adding an all-layer copper bypass. Also exercise a legal geometry change
that preserves the derived physical margin. Derive the expected topology and limit from the thermal
requirement or model, not from the generator constants that produced the current board.

When the thermal result still requires a physical prototype, state the unverified quantity and the
experiment that will resolve it. A board may be manufacturable as a thermal experiment, but it is
not a functionally validated implementation of the isolation claim.

## Handle exposed pads, vias, paste and zones together

Use solid zone connection on a heat-carrying exposed pad unless the part's land guidance says
otherwise. KiCad zones default to `ZONE_CONNECTION_THERMAL`; relief spokes can starve the very
connection the exposed land exists to provide. KiCad's `starved_thermal` DRC may catch too few
spokes, but it does not prove junction temperature or heat spreading.

Treat the exposed land and all same-number pads as a union. An exposed pad and its internal via
lands often share one pad number; taking only the first or largest pad can omit split or notched
geometry.

Do not print paste over an untreated open via barrel unless the assembly process explicitly
qualifies that construction; solder can wick away from the exposed land. Shape paste apertures
around the barrels or specify the exact via protection/fill/cap construction in the fab notes,
using IPC-4761 terminology when applicable. Plugging, filling and capping are not interchangeable;
verify fabricator and assembler compatibility with the footprint and paste process. Make every
via-in-pad construction explicit and apply the process-aware, net-blind classification in
[`FOOTPRINTS.md`](FOOTPRINTS.md).

Audit zone settings as well as filled geometry. Fill mode, pad connection and island removal can
change through a GUI or project rewrite without moving the outline. Turning island removal off
may preserve useful heat-spreading copper but creates `isolated_copper` findings and possibly
floating coupling plates; resolve intended islands with deliberate, usually mirrored stitching
and re-check electrical safety. Never lower the finding severity merely to retain copper.

Refill before measuring, and use [`GUARDS.md`](GUARDS.md)'s in-memory semantic-settle contract.
A first-pass or stale fill is not thermal evidence.

## Guard thermally matched copper

Use [`GUARDS.md`](GUARDS.md)'s independent matched-copper gates when filled copper carries a
thermal symmetry requirement:

- Gate A checks artifact-derived residual shape and topology after validating and bounding every
  allowed-asymmetry mask.
- Gate B checks unmasked raw quantities and common-mode loss against a physically derived limit.

Never apply Gate A's mask to Gate B; doing so can hide the lost heat-spreading copper that the raw
quantity gate exists to catch. Do not derive Gate B from filler noise or the current board. Start
from allowed temperature, resistance/current-sharing or device-parameter mismatch, then model or
measure sensitivity to copper removed at location.

Shape and quantity cover different faults. Equal areas can hide a narrow neck or disconnected
region; a shape mask can intentionally ignore an area where gross copper loss still matters. Keep
both individual sides and their difference so a common-mode fill change remains visible.

Audit the zone fill, not only its outline. The outline is intent; pads, tracks, clearances and the
board edge determine the heat-carrying copper that ships. In one matched pair, cosmetically
similar outlines produced fills differing by 3.4 mm².

Apply [`GUARDS.md`](GUARDS.md)'s general pad-shape and orientation-evidence rule before treating a
matched placement as thermally symmetric; the thermal analysis consumes that artifact result
rather than owning a second copy of the rule.

Calibrate with a missing region, neck and island plus legal placement/fill changes that must stay
quiet. Keep an existing fail-closed limit until the physically derived gate lands, or record an
explicit prototype waiver; never loosen it silently to pass a filler residual.

## Validate models and the finished board

Trust order remains datasheet table, datasheet chart, then vendor model. Temperature blocks in
vendor SPICE models are often the least validated part. One Schottky model matched its datasheet
at 25 °C but predicted 0.863 V against 0.66 V typical at 125 °C and 15 A, with error increasing
at higher current. Validate every load-bearing model at the operating current/voltage and at the
temperature corners; fit from datasheet data when it disagrees.

DRC verifies manufacturability rules, not temperature. Bind each thermal claim to one or more of:

- a cited calculation with explicit environment and construction assumptions;
- a simulation whose model has been checked against datasheet points;
- a calibrated artifact guard over load-bearing geometry; and
- a measurement at the specified load, ambient, airflow/enclosure and time profile.

Test the state matrix, not only nominal steady state. Include startup, overload, accepted faults,
neighbor heating, fan/airflow loss and pulses whose time constants differ from the final soak.
Measure the point the requirement names—junction estimate, case, pad, copper, ambient or matched
gradient—and state sensor placement and uncertainty.

Report observed/estimated temperature, limit, margin, operating state, duration, ambient,
airflow/enclosure, stackup/copper construction, model/source revision and artifact digest. A
thermal PASS without those bindings is not reproducible.
