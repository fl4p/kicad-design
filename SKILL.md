---
name: kicad-design
description: Create, modify, generate, verify, or review KiCad schematics, symbols, footprints, and PCB layouts. Use for .kicad_sch, .kicad_pcb, .kicad_sym, and .kicad_mod work; schematic capture; PCB layout; ERC/DRC; land-pattern selection; reproducible KiCad generators; datasheet-grounded analog or mixed-signal review; release readiness; and domain-specific design guards. Load the task-specific companions named in this skill instead of treating the main file as a complete reference.
---

# KiCad schematic and PCB design

Apply the invariant first, then the required action. Treat named parts and measured failures as
examples, not as defaults for unrelated projects. Verify every device-specific claim against the
selected device and the current toolchain.

## Route the task before acting

Read only the companions required by the task:

| file | read it when |
|---|---|
| [`SETUP.md`](SETUP.md) | the task requires a datasheet, current part status, stock, or distributor data |
| [`PCB.md`](PCB.md) | the task touches layout, zones, stackup, creepage, surface leakage, or autorouting |
| [`FOOTPRINTS.md`](FOOTPRINTS.md) | selecting, creating, or modifying a footprint or land pattern |
| [`PCBNEW.md`](PCBNEW.md) | scripting `pcbnew`, preserving reproducibility, or improving generator performance |
| [`RELEASE.md`](RELEASE.md) | running board verification, inspecting severity maps, exporting fabrication data, or deciding release readiness |
| [`GUARDS.md`](GUARDS.md) | writing or reviewing generators, validators, audits, or calibration harnesses |
| [`THERMALS.md`](THERMALS.md) | heat, dissipation, temperature, gradients, thermal pads/vias, or temperature-dependent accuracy matter |
| [`VARIANTS.md`](VARIANTS.md) | one generator must emit multiple boards without changing a qualified incumbent |

Use the helpers in [`scripts/`](scripts/README.md) instead of reimplementing netlist parsing,
library geometry, reproducibility, ERC/DRC invocation, or autoroute promotion.

Run the datasheet preflight only when the task needs datasheet or sourcing evidence. Do not delay a
purely graphical edit or a local file-format diagnosis with unrelated network and distributor
checks.

## Preserve the declared source authority

Determine the authority before editing:

- Regenerate generator-owned schematics and boards from their source.
- Edit an explicitly hand-maintained board directly; do not invent a generator merely to enable a
  transformation.
- Treat an autorouted or otherwise transformed hand-maintained board as a derived candidate until
  the project explicitly promotes it.
- Warn that GUI edits to generated artefacts will be overwritten, and check for a running KiCad GUI
  holding a stale copy before regenerating.

Keep generators surgical and reproducible:

- Never rewrite a file owned by another generator. Merge a required key into shared JSON such as
  `.kicad_pro`; preserve unrelated keys and insertion order.
- Derive schematic UUIDs from stable identity, not counters. Follow [`PCBNEW.md`](PCBNEW.md) for
  board UUID canonicalization and deterministic item ordering.
- Run diagnostics on scratch copies. Never call `SaveBoard` or an equivalent serializer on the
  tracked artefact merely to inspect it.
- Reject optimized Python execution when load-bearing checks still use `assert`; use explicit
  exceptions for conditions whose disappearance would create a false pass.
- Prove regeneration by checking the process status and the output produced in that run. Prefer a
  temporary output or an explicit generator receipt; do not infer execution only from equal hashes
  or require an mtime change from a generator that intentionally avoids unchanged writes.
- Compare the diff size with the intended change. A small field edit that rewrites thousands of
  lines indicates serialization or canonicalization churn; regenerate from authority before
  proceeding.
- Export and inspect the netlist after every structural schematic edit.

## Resolve user-owned design choices

Ask before placement when the answer controls procurement, enclosure fit, compliance, or process:

- layer count and stackup;
- board outline and mounting;
- hand assembly versus reflow and acceptable package styles;
- coating and the resulting spacing standard/table/column;
- connector family and pinout.

When the user requests an autonomous run, make each choice explicitly and record its rationale in
the design document. Do not let a layout accident become an undocumented decision.

After a stackup change, derive the enabled copper layers from the board. Reject hardcoded layer
tuples in generators and audits unless the code also asserts the exact supported stack.

## Close every external interface

Write an electrical contract for every connector pin. Include direction, normal and fault voltage
and current, domain, return/reference, scaling, power ownership, and behavior while either side is
unpowered. Put the same truth in the schematic, board markings, and integration document.

Apply these checks:

- Trace every promised function to concrete components and nets. A label such as `VIN` does not
  implement a divider.
- Give every floating measurement domain an intentional DC reference. Do not count capacitance,
  leakage, or protection diodes as a DC reference.
- Build a power-state matrix for independently powered domains. Follow driven signals into
  unpowered receivers and bound injection, back-powering, and rail contention.
- For every clamp path, identify what absorbs current with the receiving rail powered and
  unpowered. A rail that normally consumes more than the injected current is not a guaranteed sink.
- Place series fault limiting so the exposed run is downstream of it, and place associated pull-ups
  on the side that preserves both valid logic levels and the fault-current bound.
- Size protection at the maximum credible fault, including tolerances and transients, not at the
  normal signal maximum. Include leakage and capacitance in precision-node budgets.
- Reject direct connection of two possible power sources unless ORing, current limiting, or
  isolation makes every connection order safe.
- Compute feasible intervals before selecting a component value. If the lower bound exceeds the
  upper bound, require a topology change rather than suggesting another value.

### Silent fallback requires runtime evidence

For any device that automatically substitutes an internal clock, reference, configuration source,
or other resource, determine whether loss of the expected resource produces an error or merely
plausible output. When fallback can preserve syntax while invalidating meaning, require runtime
evidence of the resource actually in use and mark results `unverified` when that evidence is absent.

**Example — ADS1262 external clock.** An ADS1262 intended to use an external clock can continue on
its internal oscillator when that clock disappears. Schematic checks can prove that the clock pin
is connected and driven; they cannot prove which oscillator is active during an acquisition. The
host contract should therefore enable the status byte and verify the device's `EXTCLK` status at
the acquisition boundary. Verify the register and bit definitions against the exact datasheet
revision rather than copying this example to another ADC.

## Build the schematic from authoritative geometry

Prefer library-derived pins and graphics over memorized offsets. When generating a schematic:

1. Resolve the selected library symbol, including inherited `extends` content.
2. Union unit-0 pins and graphics with the selected unit.
3. Key instances by reference and unit.
4. Transform pin coordinates in KiCad's order: rotate, then mirror.
5. Raise when a requested pin cannot be resolved; never search other units as a fallback.
6. Emit and export a calibration schematic, then compare every supported angle/mirror cell from
   `kicad_symlib.calibration_plan()` with KiCad's netlist. One exercised cell is not calibration of
   the transform space.

Use balanced S-expression blocks when parsing KiCad files. Do not pair fields with a single
cross-block `.*?` regular expression; it can combine a property coordinate with an unrelated pin
number and return plausible fabricated geometry.

Keep wiring helpers fail-closed:

```python
def wire(x1, y1, x2, y2):
    for v in (x1, y1, x2, y2):
        if not ongrid(v):
            raise ValueError(f"off-grid endpoint {(x1, y1, x2, y2)}")
    if not (x1 == x2 or y1 == y2):
        raise ValueError(f"diagonal wire {(x1, y1, x2, y2)}")
```

Check labels, junctions, no-connects, and power-symbol attachment separately; the wire helper cannot
observe them.

### Compact file-format traps

| trap | required action |
|---|---|
| raw newlines in quoted strings | escape as `\n` |
| schematic and symbol Y axes differ | transform from library coordinates; do not copy offsets by eye |
| symbol variants have different pin lengths | resolve pin positions from the embedded library symbol |
| multi-unit parts | retain unit identity and include common unit 0 |
| multi-element symbols | derive each element's pin grouping; do not assume consecutive pin numbers (for example, a resistor-pack element may use pins 1 and 4) |
| inherited symbols | flatten or otherwise resolve `extends` before embedding |
| connector library IDs | resolve an installed suffixed symbol name; do not synthesize a bare `Conn_02xNN` name and assume it exists |
| labels | place exactly on a wire endpoint or segment |
| NC package pins | represent them intentionally and verify symbol-to-footprint pin mapping |
| repeated pad numbers | union every pad carrying that number when deriving land geometry |
| embedded `lib_symbols` names | use the full `lib_id` and require a nonzero component count after export |
| passive connector as a power source | use the project's ERC convention, commonly a `PWR_FLAG`, and verify the resulting net |

Treat these as failure classes, not as permission to hardcode another library release's geometry.

## Verify in layers

Use a ladder because no single KiCad command establishes design correctness:

```sh
K="${KICAD_CLI:-kicad-cli}"   # set KICAD_CLI to an absolute path when it is not on PATH
"$K" sch export pdf --black-and-white --exclude-drawing-sheet -o out.pdf x.kicad_sch
"$K" sch erc --severity-all --exit-code-violations -o erc.rpt x.kicad_sch
"$K" sch export netlist --format kicadsexpr -o netlist.net x.kicad_sch
"$K" pcb drc --severity-all --schematic-parity --exit-code-violations -o drc.rpt x.kicad_pcb
```

Apply the rungs in order:

1. **Parse.** Require each command to produce the expected output.
2. **ERC.** Resolve the effective severity and pin maps before calling zero violations clean.
3. **Netlist.** Require a plausible component count, parse every net, and compare connectivity with
   design intent.
4. **Render.** View the exported schematic or board. Check overlaps, orientation, labels, notes,
   connector readability, and isolation-barrier interpretation.
5. **Domain guards.** Run the project-specific checks described in [`GUARDS.md`](GUARDS.md).
6. **Board and release checks.** Apply [`RELEASE.md`](RELEASE.md), including parity, effective DRC
   severity, fabrication exports, and artefact binding.

Do not omit `--exit-code-violations`: ERC and DRC can report violations while exiting zero. Do not
hide its status behind a pipeline; capture the command status before filtering output or enable
`pipefail`. Judge the report contents as well as the process status.

Treat a missing or empty severity map as `unverified`, not as proof that nothing is ignored. Diff
`.kicad_pro` before and after generators and restore unintended changes before running verification.

Parse exported netlists independently of KiCad's pretty-printing. Accept arbitrary whitespace,
count the input `(net` blocks, require the parsed count to match, require nodes, and test an older
committed export plus a fresh export when supporting multiple KiCad versions. A zero-component or
zero-net export is a failed verification input, never a clean design.

Use KiCad's own text and graphic extents when available, then render. Do not turn font measurements
from one project or release into universal constants. Include labels, symbol properties, power
symbols, and drawing-sheet objects explicitly when a visual guard depends on them; they are
different object classes. Put guard implementations and their calibrations in
[`GUARDS.md`](GUARDS.md), not in this core file.

Treat verification summaries as cached output. Regenerate reports before release and bind them to
the artefact digest they describe.

**Run the outer rungs whenever you change what the inner ones cover.** Domain guards test what
someone thought to test; DRC tests what KiCad knows. A board that passed every project audit — all
of them green, with a calibrated mutation suite behind them — carried a dangling via and a stub of
dead copper that rung 6 found immediately. The guards were not wrong; the defect was outside the
question they asked.

The trap is that the expensive rung looks least affordable exactly when it matters most: after a
refactor, a build flag, or any change to a guard's scope, when the cheap checks have quietly stopped
covering what they used to. Budget the full run at that point rather than deferring it, and do not
report a board as verified on inner-rung evidence alone.

Run any regenerating verification against a **copy**. An entry point that rebuilds before checking
will overwrite the artefact it is verifying, including artefacts that are not yet committed.

## Ground component decisions in current evidence

Run [`SETUP.md`](SETUP.md) before relying on a datasheet or current sourcing information. Then:

- Build a requirement ledger for every mandatory or explicitly recommended supply, bypass,
  reference, protection, sequencing, and exposed-pad requirement. Map each item to refdeses and
  nets in the emitted design.
- Extract value, unit, test conditions, and temperature corner as one tuple. Do not copy digits
  without the unit column or infer a pin's impedance from its name.
- Use typical values for nominal estimates only. Combine min/max device limits, supply tolerance,
  passive tolerance, temperature, and ageing where they establish compliance or stress.
- Distinguish recommended operation, characterized operation, and absolute maximum.
- Validate models against datasheet tables and charts at every load-bearing operating point.
- Verify exact orderable MPN, package, performance grade, lifecycle status, and stock as separate
  questions. Follow [`RELEASE.md`](RELEASE.md) for BOM and sourcing evidence.
- Query value, voltage rating, dielectric, package, and other coupled constraints together. Sweep
  the BOM by predicate after fixing one instance of a defect class.
- Compare every selected land pattern with the datasheet's pad size, pad centres, and pin-1 corner.
  Follow [`FOOTPRINTS.md`](FOOTPRINTS.md); matching vendor and body size is only a hypothesis.
- Inspect the whole PDF before claiming information is absent. Render raster drawings and use only
  printed callouts as datasheet values; label dimensions scaled from a picture as models.
- Record the source and page/table beside each load-bearing constant.

If the needed evidence remains inaccessible, stop and request the document. Do not substitute a
part or quote a specification from memory merely to keep moving.

## Review and hand off

Recompute important arithmetic independently, remeasure geometry from the emitted files, and
separate confirmation of a defect from confirmation of the reported number.

After changing a value, topology, net name, interface, or safety limit, search every live
representation: generator source, schematic annotations, board markings, BOM, assembly and
integration documents, firmware limits, and current verification reports. Preserve dated reviews
as historical records; mark findings resolved or superseded instead of rewriting the original
observation.

Report what was verified, what remains `unverified`, which tool and datasheet revisions were used,
and which source artefact owns future edits.
