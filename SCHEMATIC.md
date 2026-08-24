# Schematic capture

Use this companion whenever a task captures, generates, edits, reviews, or declares completion of a
schematic, and before beginning PCB work from that schematic.

## Preserve component semantics

Use the exact selected library symbol when it exists. When a project must embed or generate a
symbol, derive both its pins and its graphics from the authoritative library symbol, resolving
inheritance and units as required by `SKILL.md`.

The drawing must communicate electrical class and polarity without relying on the reference or
value text. Use class-specific symbols for resistors, capacitors, polarized capacitors, fuses,
inductors, ferrites, diodes, Zeners, TVS devices, transistors, potentiometers, connectors, power
symbols and test points. Use the device-specific symbol for an IC or special component when one is
available and verified.

Do not use any of these as a shortcut:

- one generic rectangular two-pin symbol for different passive classes;
- a generic IC box when a verified device symbol is available;
- a passive line or box for a polarized diode, Zener, TVS or electrolytic capacitor;
- reference prefixes, values, notes or footprints as substitutes for the correct graphic; or
- a clean ERC/netlist result as evidence that symbol semantics are correct.

If no suitable symbol exists, create a custom symbol whose conventional graphic, pin names,
electrical types, pin numbers, polarity and unit structure match the datasheet and intended
footprint. Record the symbol source and the package-pin mapping. A temporary placeholder is allowed
only in an explicitly named draft; it blocks capture completion and PCB implementation.

## Capture controlled states and timing

For muxes, relays, interlocks and actuators whose state combinations or transitions have material
consequence, define the project-derived safe or least-harmful state, forbidden combinations and
assertion/release ordering before capture. Cover startup, power loss, disconnected controls,
invalid or simultaneous requests and interrupted transitions. When an invalid combination has
material consequence, enforce exclusion in hardware; firmware sequencing may supplement but must
not be the sole interlock.

When timing or sequencing is load-bearing, keep an executable worst-case timing ledger using every
applicable tolerance, temperature, leakage, threshold, rail-slew, propagation, drive and load
limit. Identify dynamic behavior the schematic and CAD checks cannot establish, and carry each
unknown forward as an explicit qualification gate rather than treating the ledger as proof.

## Capture-completion gate

Do not report schematic capture complete until all of the following are true:

1. Every fitted and DNP component uses an appropriate class-specific or verified device-specific
   symbol. Any remaining placeholder is enumerated and makes the gate fail.
2. Polarity, pin names, pin numbers, power pins, no-connects, exposed pads and multi-unit ownership
   agree with the selected package and datasheet.
3. The exported netlist has a plausible nonzero inventory and matches the intended connectivity,
   including protection direction, power-source conventions and connector contracts.
4. ERC has been run with violation exit status enabled, and its effective severities, exclusions and
   report contents have been reviewed.
5. A fresh PDF or SVG render has been visually inspected page by page. Check symbol class and
   polarity, reference/value association, readable functional flow, label attachment, junctions,
   overlapping text/graphics, power and return paths, bypass/support circuitry and connector pin
   meaning. Record who or what performed this review and which artifact was inspected.
6. Project-specific schematic guards pass. For generated designs, include a semantic inventory
   check that maps each reference/component role to an allowed symbol library family and rejects a
   generic placeholder unless the draft explicitly permits it. Validate the resolved or embedded
   graphic and its authoritative provenance, not only the claimed library identifier; a generator
   must not hide a generic box behind an approved-looking name.

ERC and connectivity audits are necessary but cannot satisfy items 1 or 5. A successful generator
run proves only that it emitted an artifact.

## PCB-start gate

Do not create or delegate detailed PCB placement, routing or footprint transfer until the
capture-completion gate passes against the current generator/schematic and a fresh exported netlist.
A schematic structural change makes the prior gate and downstream parity evidence stale.

An explicitly requested parallel outline or mechanical study may proceed as a provisional artifact
when it does not depend on unresolved connectivity. Mark it provisional, keep it separate from the
implementation board, and re-import only from the gated netlist. Do not use parallel work to imply
that schematic capture passed.

## Generated-schematic guard pattern

Make semantic misuse fail close in the source of truth. Prefer an explicit type argument or
reference-family dispatch that rejects unknown classes over a helper that silently maps every
two-terminal part to one symbol. Representative invariants include:

- `R*` uses an approved resistor or resistor-network family;
- `C*` uses the appropriate polarized or unpolarized capacitor family;
- `L*` and ferrite references use inductor/ferrite graphics;
- `F*` uses fuse/PTC graphics;
- `D*` uses a diode/TVS family with audited anode/cathode or array pin mapping;
- `Q*` uses the correct FET/BJT family and package pin mapping; and
- custom device symbols carry a recorded datasheet/package map.

Calibrate every supported reference-family dispatch and fallback branch, including `R`, polarized
and unpolarized `C`, `L`/ferrite, `F`, each supported `D` family, `Q`, potentiometer, connector,
power/test-point and custom-device handling. Exercise polarity, unit and package-pin mappings where
they branch. A deliberate generic-symbol substitution for one resistor and one diode is only the
minimum example; those two mutations do not qualify the unexercised families or mappings. Unknown
and ambiguous classes must fail closed, and each legal family needs an accepted calibration case.
