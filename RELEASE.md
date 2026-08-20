# Verify and release a board

Read this reference for the board-side verification ladder, fabrication export, BOM evidence, or a
decision about whether a board is ready to order. Apply [`THERMALS.md`](THERMALS.md) separately
when temperature or heat flow is load-bearing.

## Contents

- [Run board verification](#run-board-verification)
- [Resolve effective rule maps](#resolve-effective-rule-maps)
- [Separate manufacturable from final](#separate-manufacturable-from-final)
- [Export and measure fabrication data](#export-and-measure-fabrication-data)
- [Verify BOM and sourcing evidence](#verify-bom-and-sourcing-evidence)
- [Bind outputs to their source artefact](#bind-outputs-to-their-source-artefact)

## Run board verification

Treat a green DRC as “no active rule reported a violation,” not as proof that the design is right:

```sh
K="${KICAD_CLI:-kicad-cli}"   # set KICAD_CLI to an absolute path when it is not on PATH
"$K" pcb drc --severity-all --schematic-parity --exit-code-violations -o drc.rpt x.kicad_pcb
```

- Keep `--exit-code-violations`; without it DRC can write violations and exit zero.
- Keep `--schematic-parity`; it checks that the board still agrees with the schematic.
- Capture the command status before piping output, and judge the report contents as well.
- Re-run the layout generator after every schematic change that can alter values, fields,
  footprints, or connectivity.
- Independently audit any rule area that relaxes a physical constraint. Position-dependent rules
  can remain green after a new object moves into the relaxed region.

Check third-party connectivity findings against KiCad's own connectivity and DRC. External tools
may reconstruct nets without understanding through-plane connections, zone fills, or rule-area
restriction flags. Record which findings were dismissed and the authoritative evidence used.

## Resolve effective rule maps

`--severity-all` does not resurrect rules whose effective severity is `ignore`. Before calling ERC
or DRC clean:

1. Resolve the effective severities, including KiCad defaults absent from a sparse project map.
2. List every ignored rule and every exclusion in the release report.
3. Record pin-map changes that alter ERC compatibility.
4. Diff `.kicad_pro` before and after each generator or GUI-assisted transformation.
5. Treat a missing, empty, or unreadable map as `UNVERIFIED`, not as an empty ignore list.

Do not rely only on a diff against defaults: a load-bearing rule may already default to `ignore`.
Enumerate first, then use the diff to expose project-specific changes. Restore unintended project
file mutations before generating the reports bound to the release.

## Separate manufacturable from final

Answer two questions independently:

- **Manufacturable:** can the fab build the board from the supplied geometry, stackup, drill, and
  other outputs?
- **Final:** is this the exact revision the user wants fabricated, including silkscreen, hazard
  markings, revision identifiers, and all pending board changes?

Classify BOM, assembly, integration, and documentation findings separately. A board can be
manufacturable while still blocked from ordering, and a sourcing problem does not necessarily make
its bare PCB unmanufacturable.

## Export and measure fabrication data

Run the actual exports:

```sh
K="${KICAD_CLI:-kicad-cli}"   # set KICAD_CLI to an absolute path when it is not on PATH
"$K" pcb export gerbers --output fab  x.kicad_pcb
"$K" pcb export drill   --output fab/ x.kicad_pcb
"$K" sch export bom --output fab/bom.csv --group-by 'Value,Footprint,MPN' \
     --fields 'Reference,Value,Footprint,MPN,Manufacturer,${QUANTITY}' x.kicad_sch
"$K" pcb export pos --output fab/cpl.csv --format csv --units mm --side both x.kicad_pcb
```

Check every exit status and log. Confirm that the expected copper layers appear. Measure from the
board rather than memory:

- minimum track width and clearance;
- via pad, drill, and annular ring `(pad - drill) / 2`;
- minimum plated and non-plated drill;
- board outline dimensions;
- explicit stackup and finished thickness;
- any fab-specific mask, paste, silk, slot, or edge constraint.

Assert that the outline is closed. Count `Edge.Cuts` endpoints and require each segment endpoint to
belong to a closed contour; handle inherently closed circles and rectangles explicitly:

```python
points = collections.Counter()
for drawing in board.GetDrawings():
    if board.GetLayerName(drawing.GetLayer()) != "Edge.Cuts":
        continue
    if drawing.GetShape() in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_CIRCLE):
        continue
    points[key(drawing.GetStart())] += 1
    points[key(drawing.GetEnd())] += 1
open_ends = [point for point, count in points.items() if count != 2]
```

Calibrate this for the outline primitives the project permits; endpoint degree alone is not a full
topology proof for arbitrary self-intersections.

## Verify BOM and sourcing evidence

Read `MPN`, manufacturer, and specification fields from the schematic or exported netlist. Symbol
fields do not automatically propagate to board footprints, so a footprint-only scan can falsely
report every component as missing its MPN. Treat a result that condemns everything with the same
suspicion as a check that finds nothing.

Enforce the BOM in both directions: every fitted part needs an appropriate BOM row, and every BOM
row must resolve to fitted references. Exempt only deliberate non-procured objects such as mounting
features, and make the exemption explicit. Reject fitted placeholder values such as a bare `R` or a
library symbol name.

For every procured line, verify these as separate facts:

- exact manufacturer ordering code and package;
- required tolerance, voltage, temperature, dielectric, matching, or performance grade;
- current lifecycle state;
- current stock or an explicitly accepted lead time;
- datasheet revision supporting the design constants.

Do not derive one ordering code by copying another part's packaging suffix. Do not treat a
datasheet ordering table as proof that a code remains active, and do not treat purchase history as
proof that stock remains available. When one component fails a coupled constraint such as value ×
voltage × dielectric × package, sweep the complete BOM by that predicate.

## Bind outputs to their source artefact

Record a cryptographic digest of the exact source schematic and board alongside fabrication and
assembly outputs. Commit or otherwise archive them together. Regenerate after any source change or
GUI serialization step; ordering-only churn can alter bytes without changing connectivity, so a
verbal revision label cannot prove which source produced the released copper.

Record the KiCad version, active rule maps, generator revision, and export commands with the
release. Treat reports and fabrication files as cached outputs that expire when any bound input
changes.
