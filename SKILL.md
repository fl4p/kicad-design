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
| [`SCHEMATIC.md`](SCHEMATIC.md) | the task captures, generates, edits, reviews, or declares completion of a schematic, or begins PCB work from one |
| [`SETUP.md`](SETUP.md) | the task selects or substitutes a component; designs or reviews circuitry, placement, or layout around a critical component; validates procurement; or requires a datasheet, reference design, eval-board documentation, current part status, inventory, stock, or distributor data |
| [`PCB.md`](PCB.md) | the task touches layout, zones, stackup, creepage, surface leakage, or autorouting |
| [`FOOTPRINTS.md`](FOOTPRINTS.md) | selecting, creating, or modifying a footprint or land pattern |
| [`PCBNEW.md`](PCBNEW.md) | scripting `pcbnew`, preserving reproducibility, or improving generator performance |
| [`RELEASE.md`](RELEASE.md) | running board verification, inspecting severity maps, exporting fabrication data, or deciding release readiness |
| [`GUARDS.md`](GUARDS.md) | writing or reviewing generators, validators, audits, or calibration harnesses |
| [`THERMALS.md`](THERMALS.md) | heat, dissipation, temperature, gradients, thermal pads/vias, or temperature-dependent accuracy matter |
| [`VARIANTS.md`](VARIANTS.md) | one generator must emit multiple boards without changing a qualified incumbent |

Prefer the helpers in [`scripts/`](scripts/README.md) when they fit the project's existing
toolchain instead of reimplementing netlist parsing, library geometry, reproducibility, or
ERC/DRC invocation. Use the autoroute helpers only after the project opts into the external-routing
workflow in [`PCB.md`](PCB.md).

Run the device-evidence and sourcing preflight when the task selects or substitutes a component;
designs or reviews circuitry, placement, or layout around a critical component; validates
procurement; or needs datasheet, reference-circuit, or sourcing evidence. For this preflight, a
component is critical when its support circuit, location, orientation, thermal path, return path, or
routing materially controls an electrical, thermal, safety, mechanical, assembly, EMC, or
routability requirement. Apply the same test during board floorplanning; the component need not be
pre-labelled critical.

Do not delay a purely graphical edit or local file-format diagnosis with unrelated inventory,
network, or distributor checks. A fixed-part circuit or placement review needs device evidence, but
not inventory or distributor checks unless procurement is in scope.

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

Schematic capture is a human-reviewable engineering deliverable, not merely a connectivity model.
Apply the capture-completion and PCB-start gates in [`SCHEMATIC.md`](SCHEMATIC.md). In particular,
do not represent a typed component with a generic box or generic two-pin placeholder when its
electrical class has an established symbol: use the appropriate resistor, capacitor, polarized
capacitor, fuse, inductor, diode, Zener/TVS, transistor, potentiometer, connector, power, test-point,
or other class-specific graphic with correct pin and polarity semantics. A reference prefix, value,
footprint, or clean ERC result does not repair an incorrect or semantically empty graphic.

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

Select the rungs that cover the changed surface and the claim being made, then run the selected
rungs from inner to outer. Do not turn a focused edit or diagnosis into a release build merely
because an outer rung exists:

1. **Parse.** Always parse each modified KiCad artefact and require each invoked command to produce
   the expected output.
2. **ERC.** Run after schematic electrical, symbol-pin, power, or rule-severity changes. Resolve the
   effective severity and pin maps before calling zero violations clean.
3. **Netlist.** Run after structural schematic or connectivity changes. Require a plausible
   component count, parse every net, and compare connectivity with design intent.
4. **Render.** Run when geometry, text, symbols, footprints, zones, or other visible output changes.
   Check the visual properties relevant to the edit.
5. **Domain guards.** Run the project-specific checks whose subjects or assumptions changed, as
   described in [`GUARDS.md`](GUARDS.md).
6. **Board checks.** Run DRC after any change to board geometry, connectivity, effective
   rules/severities, or stackup that can affect its result. Examples include tracks, vias, copper
   and non-copper graphics, text, footprints or pads, zones, outlines, cutouts, slots, keepouts and
   rule areas. Include parity when board-to-schematic agreement can change.
7. **Release checks.** Apply [`RELEASE.md`](RELEASE.md), including fabrication exports and artefact
   binding, only when establishing or re-establishing release readiness. A change to a bound input
   makes earlier release evidence stale; mark it stale, but do not regenerate release outputs unless
   the task requires current release evidence.

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

**Run an outer rung whenever its inputs or claimed result may have changed and the task relies on a
current result from that rung.** Otherwise mark the previous result stale rather than silently
treating it as current or rebuilding it without need. Domain guards test what someone thought to
test; DRC tests what KiCad knows. A board that passed every project audit — all of them green, with
a calibrated mutation suite behind them — carried a dangling via and a stub of dead copper that
board DRC found immediately. The guards were not wrong; the defect was outside the question they
asked.

Agreement between a design note, generator constants and an audit copied from those constants is
one repeated assumption, not independent evidence. When physical function depends on geometry,
derive the guard's topology and limit from the named requirement or validated model, then calibrate
it with a DRC-clean mutation that preserves the feature's appearance while defeating its function.

After a refactor, build flag, or change to a guard's scope, explicitly reassess which checks still
cover the resulting artefact and rerun every affected rung. Budget the full release workflow only
when making a release claim; otherwise report the narrower verification scope instead of calling
the complete board verified.

Run any regenerating verification against a **copy**. An entry point that rebuilds before checking
will overwrite the artefact it is verifying, including artefacts that are not yet committed.

## Ground component decisions in current evidence

Run [`SETUP.md`](SETUP.md) before relying on a datasheet or current sourcing information.
Before broad-market search in a component-selection, substitution, or procurement-validation task:

- Derive the mandatory electrical, mechanical, thermal, environmental, safety, compliance,
  assembly, lifecycle, package, and quantity constraints before evaluating candidates.
- Check user-owned inventory through a user-declared, already-authorized read-only source and query
  any similarly authorized order history the user declares for candidate discovery. If no source
  has been declared, ask the user whether an inventory exists and how it can be queried. If a valid
  inventory query reports that the inventory itself is empty, ask whether the source is
  intentionally empty, stale, or uninitialized before proceeding. Ask once per task and reuse the
  answer; a non-empty inventory with no qualifying match is not an empty inventory.
- Never initiate authentication or ask for credentials merely to complete the check. If the
  declared source is inaccessible, ask whether another already-authorized source is available,
  record the outcome, and continue only after the user answers.
- Prefer an owned part only when the inventory record establishes its exact manufacturer, MPN,
  and package and the candidate satisfies every mandatory constraint with suitable condition and
  sufficient available-to-project quantity.
  Record the lookup outcome and selection rationale when the decision is made; inventory preference
  never compensates for missing engineering or lifecycle evidence.
- After checking exact replacements, sweep owned inventory by required function and classify each
  relevant result as an exact replacement, a requirement-preserving value or package change, a
  topology-changing alternative, or unsuitable. Treat power conversion, regulation, supervision,
  series load switching, gate drive, and isolation as distinct circuit roles and requirement sets;
  one part may satisfy several roles only when each is verified. Report topology-changing
  candidates separately rather than hiding them under “no replacement” or presenting
  non-interchangeable roles as drop-ins.

Then:

- Build a requirement ledger for every mandatory or explicitly recommended supply, bypass,
  reference, protection, sequencing, and exposed-pad requirement. Map each item to refdeses and
  nets in the emitted design.
- For each critical component, inspect the vendor's typical-application circuits and the relevant,
  comparable official reference designs or evaluation-board schematic, BOM, layout, and user
  guide. Compare the applicable support circuitry and control or layout-sensitive pins and paths
  with the proposed design, and record intentional deviations that affect a project requirement.
  Treat these implementations as evidence, not requirements; the current datasheet, errata, and
  the project's actual operating conditions remain authoritative.
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

Preserve the user's requested outcome through the handoff. Distinguish an exploratory placement or
routing draft, a completed PCB implementation, and a fabrication release; do not let a tool, retry
limit or delegated agent silently downgrade one into another. For board work, apply the placement
readiness and hard completion gates in [`PCB.md`](PCB.md). If a requested completed board still has
electrical ratsnests or true copper DRC failures, continue by revising placement or routing strategy,
or report it blocked with exact evidence—never report it complete merely because a bounded run
ended.

Recompute important arithmetic independently, remeasure geometry from the emitted files, and
separate confirmation of a defect from confirmation of the reported number.

After changing a value, topology, net name, interface, or safety limit, search every live
representation: generator source, schematic annotations, board markings, BOM, assembly and
integration documents, firmware limits, and current verification reports. Preserve dated reviews
as historical records; mark findings resolved or superseded instead of rewriting the original
observation.

Report what was verified, what remains `unverified`, which tool and datasheet revisions were used,
and which source artefact owns future edits.
