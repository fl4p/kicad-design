# Verify and release a board

Read this reference for the board-side verification ladder, fabrication export, BOM evidence, or a
decision about whether a board is ready to order. Apply [`THERMALS.md`](THERMALS.md) separately
when temperature or heat flow is load-bearing.

## Contents

- [Run board verification](#run-board-verification)
- [Resolve effective rule maps](#resolve-effective-rule-maps)
- [Keep four release states independent](#keep-four-release-states-independent)
- [Export and measure fabrication data](#export-and-measure-fabrication-data)
- [Verify BOM and sourcing evidence](#verify-bom-and-sourcing-evidence)
- [Bind outputs to their source artefact](#bind-outputs-to-their-source-artefact)

## Run board verification

Treat a green DRC as “no active rule reported a violation,” not as proof that the design is right:

```sh
K="${KICAD_CLI:-kicad-cli}"   # set KICAD_CLI to an absolute path when it is not on PATH
"$K" pcb drc --severity-all --refill-zones --schematic-parity \
     --exit-code-violations -o drc.rpt x.kicad_pcb
```

Grade from an isolated same-stem project bundle: the candidate `.kicad_pcb` and authoritative
`.kicad_pro` must share a basename, and a parity run also needs the matching root `.kicad_sch`.
Copy the whole verification context: any `.kicad_dru`, hierarchical schematic sheets, library
tables and project libraries, route manifest, and other project inputs required to resolve the
board and schematic. To grade a differently named candidate, copy it under the authoritative stem
inside that scratch bundle. Do not run DRC on `candidate.kicad_pcb` beside `board.kicad_pro` and
assume KiCad applied the intended rule and parity authority.

Create one finalized release candidate in that scratch bundle. Run the project's pinned zone
finalizer and in-memory semantic-settle gate, save once, and bind the resulting board digest and
per-zone geometry snapshot. Run DRC, artifact guards, Gerber/drill export and measurements from
that exact saved board. If a consumer refills zones, prove its per-zone filled geometry equals the
bound snapshot; a second independently accepted fill is a different candidate, not corroboration.
Any detected refill difference invalidates the reports and exports and returns the release to the
finalization step.

- Keep `--exit-code-violations`; without it DRC can write violations and exit zero.
- Keep `--schematic-parity`; it checks that the board still agrees with the schematic. Require a
  fresh independently parsed annotated netlist and the DRC report's footprint-error summary; KiCad
  can otherwise print that parity could not run while returning a clean DRC report.
- Keep `--refill-zones`, and compare the resulting fill with the candidate's bound semantic
  snapshot. Do not silently allow DRC and fabrication export to consume different fills.
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

## Keep four release states independent

Report four independent states; never collapse them into one “ready” verdict:

- **Manufacturability — `PASS`, `FAIL`, or `UNVERIFIED`:** can the fab build the supplied geometry,
  stackup, drill and other outputs?
- **Functional validation — `PASS`, `FAIL`, or `UNVERIFIED`:** do the emitted electrical, thermal,
  mechanical, fluidic, safety and EMC structures satisfy every load-bearing design requirement?
- **Revision finality — `FINAL` or `DRAFT`:** is this the intended revision, including silkscreen,
  hazard markings, identifiers, accepted waivers and all requested board changes?
- **Order authorization — `AUTHORIZED` or `NOT AUTHORIZED`:** has the user or delegated release
  authority approved ordering this exact bound candidate for its stated purpose and quantity?

A waiver never changes `UNVERIFIED` or `FAIL` into functional `PASS`. It may authorize a deliberately
scoped experiment while functional validation remains `UNVERIFIED`, provided the waiver names the
unknown quantity, measurement, decision criterion, quantity/revision limit and accepting authority.
Describe that result as an authorized experimental order, not as fit for purpose, design-released,
or functionally validated.

Classify BOM, assembly, integration, and documentation findings separately. A board can be
manufacturable while still blocked from ordering, and a sourcing problem does not necessarily make
its bare PCB unmanufacturable.

Do not report a merely manufacturable board as “ready for fabrication” when an unresolved
load-bearing functional geometry claim remains. Say that the fabrication data is manufacturable
and that design release is blocked. A deliberately ordered experiment is permissible only when its
scope, unresolved claim, measurements and decision criterion are recorded and explicitly accepted;
DRC-clean geometry alone is not that acceptance.

## Export and measure fabrication data

Run the actual exports:

```sh
K="${KICAD_CLI:-kicad-cli}"   # set KICAD_CLI to an absolute path when it is not on PATH
"$K" pcb export gerbers --check-zones --output fab x.kicad_pcb
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

For each line selected, substituted, or procurement-validated during the task, preserve the
inventory decision record made under [`SETUP.md`](SETUP.md). Record:

- the logical source label and query timestamp with timezone, without credentials, sensitive
  locations, private endpoints, or raw account responses;
- for each query that produced a result corpus, every identity-bearing search surface queried, its
  pagination status, and any unavailable or failed surface; label the conclusion `exhaustive` or
  `scoped` within that declared source. For `user-confirmed-no-source`, record these fields as
  `not-applicable`. For `inaccessible-no-authorized-alternative`, preserve any known logical surface
  and failure, but record pagination and conclusion scope as `not-applicable`;
- one lookup outcome: `checked-qualified`, `checked-no-qualified-match`,
  `user-confirmed-no-source`, `user-confirmed-empty`, `empty-source-untrusted`, or
  `inaccessible-no-authorized-alternative`;
- exact manufacturer, MPN, and package mapping for any inventory candidate;
- quantity required and, only when the source provenance establishes them, recorded on-hand,
  reserved/allocated, and available-to-project; otherwise record those fields as unknown;
- one quantity-evidence state: `recorded-available`, `recorded-insufficient`,
  `purchase-history-only`, or `unknown`; keep any historical ordered quantity separate from
  recorded on-hand;
- the selection outcome and the engineering or procurement rationale. If a fully qualifying,
  sufficiently available owned part was not selected, state the applicable tradeoff such as
  condition, lifecycle margin, performance margin, assembly risk, or cost.

Release review must verify that inventory preference was applied only after complete engineering,
package, lifecycle, condition, and quantity qualification. Inventory status and purchase history
must not fill gaps in those facts.

Do not derive one ordering code by copying another part's packaging suffix. Do not treat a
datasheet ordering table as proof that a code remains active, and do not treat purchase history as
proof that stock remains available. When one component fails a coupled constraint such as value ×
voltage × dielectric × package, sweep the complete BOM by that predicate.

## Bind outputs to their source artefact

Create one canonical, path-sorted release-input manifest and hash the manifest itself. Give every
input an immutable identity or cryptographic digest. Include at least:

- the finalized board, root and hierarchical schematic sheets, `.kicad_pro`, `.kicad_dru`, project
  variables and all CLI `-D` values;
- library tables and the exact project/global symbol, footprint and 3D-model files actually
  resolved, using archived immutable copies when a global nickname could later resolve elsewhere;
- generator source/revision and generated-input data, selected variants, route manifest and
  applicator identity;
- stackup/construction data plus any domain model, MCAD/enclosure, connector, shield, fastener or
  other external input consumed by a load-bearing guard; and
- pinned KiCad/finalizer/tool versions, commands, configuration and approved waivers.

Record the manifest digest inside or alongside every DRC/ERC report, artifact-guard result,
fabrication output set, assembly output and order authorization. Commit or otherwise archive the
manifest, its resolvable inputs and outputs together. A report bound only to the root schematic and
board is insufficient: changing `.kicad_pro` severity, a hierarchical sheet, a rule file or a
library can change the verdict or copper while those two digests remain unchanged.

Regenerate after any bound-input change or GUI serialization step. Treat reports and fabrication
files as cached outputs that expire when the manifest digest changes; a verbal revision label
cannot prove which inputs produced the released copper.
