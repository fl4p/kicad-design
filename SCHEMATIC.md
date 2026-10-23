# Schematic capture

Use this companion whenever a task captures, generates, edits, reviews, or declares completion of a
schematic, and before beginning PCB work from that schematic. Before reading a datasheet or vendor
collateral, run the preflight in [`SETUP.md`](SETUP.md); do not wait for the first failed fetch.

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

If the datasheet fetch fails or returns an empty/denial response, do not record pinout or polarity
as an unverified assumption — follow the canonical access-recovery contract linked from
[`SETUP.md`](SETUP.md) §*Escalate through web defenses* (a 2xx with an empty body is a false-success
shape, not absence).

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

ERC and connectivity audits are necessary but cannot satisfy items 1, 5 or 7. A successful generator
run proves only that it emitted an artifact.

7. The schematic-wiring guard (`scripts/schematic_wiring_guard.py`) has been run, and every net it
   surfaces (`label-soup` and the wide-gap `many-fragments` signal) has an explicit disposition. It
   is **warn-only** — it does not block the gate — but a surfaced net that is neither wired nor
   justified in writing leaves the gate incomplete. An `UNEVALUABLE` result is a different, harder
   status: the guard could not analyse the schematic (parse failure, more than one sheet, a bad
   `--locality-mm`), so the check did not run — resolve that before the gate can be considered at all,
   never treat it as a pass.

## Wires, not labels — human-readable connectivity

A net label and a drawn wire produce the identical netlist, so ERC, connectivity audits and netlist
parity are all blind to the difference. A *reviewer* is not: a half-bridge drawn as four MOSFET
symbols joined by seven `SW_FAST` labels cannot be traced by following copper, and a board reviewed
that way hides real errors. Generators reach for labels because a label is cheap (place it at the
pin) while a wire is geometry (route between two pins) — so this degrades silently unless a gate
distinguishes drawn wire from label. Measured across the model benchmark, the boards a human reads
easily (Opus, Kimi-r2) draw their local nets as wires; the label-soup boards (Sonnet, GPT-Astra)
label everything, including the tightest bridge nets, and pass every automated gate.

Doctrine:

- **Local same-net connections are drawn wires.** Two pins in the same functional block — a
  half-bridge totem-pole, a gate resistor to its FET, an RC filter, a divider string — are joined by
  wire segments and junctions, not by repeating a label at each pin. Draw the H-bridge as a bridge.
- **Net labels are for rails and remote fan-out only.** Power/return rails (`GND`, `+BUS`, `DC_NEG`,
  `+12V`…) and genuinely distant connections (a sense net crossing the sheet to the driver module)
  are the correct place for labels; wiring them would hurt readability. Declare the project's rails
  to the guard with `--rail`.
- **Cross-sheet connections use hierarchical labels**, never repeated local labels.

The guard reads one sheet's wires, buses, junctions and local + global labels. It flags a net when
its labels fall in more than one drawn-connectivity fragment *and* the net is not a rail (a name the
project declares with `--rail`, or the conservative built-in `GND*`/`Vcc/Vdd/Vss` pattern — the
default deliberately matches almost nothing, so declare your rails). Two triggers: `label-soup` when
the closest inter-fragment pair is within `--locality-mm` (default 30; this corpus was reviewed at
15 — lower is stricter) — a wire was the readable option; and `many-fragments` when a net is split
across at least `--max-fragments` (default 5) pieces regardless of gap — the wide-gap case a
distance threshold alone misses. Both are **heuristics for review, not proofs**: label spacing is an
imperfect proxy for functional locality, and a legitimate multi-drop remote net can raise the
fragment count. A single wired lattice is one fragment and does not trip the wire-vs-label check —
but a clean result means only "no label-bridged local net was found", never that the wiring is
complete or correct; unconnected pins are ERC's job, not this guard's. Because it is warn-only,
treat each surfaced net as a disposition item — redraw it as wire, or record why the label is
correct (rail, cross-block distance) — exactly as a DRC class is dispositioned per pair. Do not
silence it by adding names to `--rail` that are not real rails.

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
