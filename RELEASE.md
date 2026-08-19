# Verifying and releasing a board

The board-side rungs of the verification ladder, and the separate question of whether a
board that is *manufacturable* is the one you actually want built. Read the ladder before
believing a green DRC; read the release half before ordering.

## Board-specific rungs of the verification ladder

DRC green means "no rule was broken", not "the design is right". In particular:

```sh
$K pcb drc --severity-all --schematic-parity --exit-code-violations -o drc.rpt x.kicad_pcb
```

- **`--exit-code-violations` is not optional either.** Without it `pcb drc` writes
  every violation to the report and still **exits 0** — measured at 175 violations
  exiting `0` bare and `5` with the flag. Any wrapper that trusts `$?` passes a
  board it never checked. See *The verification ladder* in `SKILL.md`.
- **`--schematic-parity` is not optional.** It is the only check that the board
  still matches the netlist.
- **`--severity-all` does not mean "all rules".** It selects error + warning +
  exclusions; it does **not** resurrect a rule set to `ignore` in
  `.kicad_pro` → `board.design_settings.rule_severities`. Calibrated: with a
  footprint's courtyard deleted, `--severity-all` reported **no**
  `missing_courtyard` while the rule was `ignore`, and reported it as soon as the
  same run had it at `error`. So "DRC: 0 violations" is a statement about the
  current severity map as much as about the board — and one real project quietly
  carried five rules at `ignore` (`footprint_filters_mismatch`,
  `footprint_type_mismatch`, `missing_courtyard`, `npth_inside_courtyard`,
  `pth_inside_courtyard`). Worse, that map lives in the `.kicad_pro` that
  `SKILL.md` warns a generator can rewrite wholesale, so it is a guard
  precondition that moves silently. **Before believing a green DRC, list every rule at `ignore` in the
  release report — including KiCad's own defaults.** `missing_courtyard`,
  `footprint_filters_mismatch` and both `*_inside_courtyard` rules ship at
  `ignore`, so a diff-against-defaults reports nothing and never fires on the
  very example above; enumerate, then diff to catch a map someone edited. (On the project above, flipping all five back produced no additional
  violations — the mechanism is real, that instance was clean.)
- **A rule area that relaxes a constraint is keyed on *position*.** Anything that
  later moves into it silently stops being held to the strict value, and DRC
  stays green. Any relaxation needs an independent geometric audit that measures
  real clearance (binary-search `SHAPE::Collide`) rather than asking the rules.
- **Re-run the layout script after *any* schematic change**, not just after
  connectivity changes — see the parity note below.
- **Only KiCad's own connectivity is authoritative.** Third-party analyzers
  rebuild nets with their own union-find over pads, tracks, vias and fills, and
  on a 2-layer board they routinely report "GND plane split, 2 islands, signals
  crossing" for F.Cu fragments that are bridged through the B.Cu pour — alarming,
  and entirely normal. Check any connectivity claim against
  `board.GetConnectivity().GetUnconnectedCount(True)` and DRC's unconnected count
  before acting on it. The same class of tool flags *membership* of a rule area
  without reading its restriction flags: a via inside a keepout that explicitly
  permits vias is not a violation. Triage third-party findings before promoting
  any of them to a blocker, and say in the review which ones you dismissed and why.

## Is it ready to fab? — manufacturable and final are different questions

A board can be DRC-clean, parity-clean and perfectly manufacturable and still be the **wrong
board to order**. Separate the two questions, because they have different blockers and people
conflate them:

- **Manufacturable** — can a fab build this from the data. Geometry, exports, stackup.
- **Final** — is this the revision you want in your hand. Any pending change that lands on the
  *fabricated artefact* is a blocker here, and that includes **silkscreen**, not just copper.
  A missing revision marker or a dropped hazard warning is a respin exactly like a missing
  resistor is.

Everything that is neither — the BOM's MPNs, an interface contract, a schematic note's
numbering — is an assembly or release concern. Say which bucket each finding is in when asked
"is it ready", or the answer collapses into an unhelpful "no".

### The manufacturability test is the export, not an opinion

Run it. It costs seconds and it is the only check that proves the data a fab receives is
complete:

```sh
$K pcb export gerbers --output fab  x.kicad_pcb
$K pcb export drill   --output fab/ x.kicad_pcb
$K sch export bom --output fab/bom.csv --group-by 'Value,Footprint,MPN' \
     --fields 'Reference,Value,Footprint,MPN,Manufacturer,${QUANTITY}' x.kicad_sch
$K pcb export pos --output fab/cpl.csv --format csv --units mm --side both x.kicad_pcb
```

Check every exit status **and** grep the logs — and confirm the copper layer count you expect
actually appears, since a 4-layer board that emits two copper gerbers is a stackup problem, not
an export problem.

Then measure the handful of numbers a fab quotes against, from the board rather than from
memory: min track width, via pad and drill, **annular ring** `(pad − drill) / 2`, min pad
drill, board outline extent, and an explicit `(stackup …)` block.

**Assert the outline is closed.** Count Edge.Cuts endpoints: every one must be shared by
exactly two shapes. An open outline still exports a plausible `.gm1`, and the fab discovers it.

```python
pts = collections.Counter()
for d in board.GetDrawings():
    if board.GetLayerName(d.GetLayer()) != "Edge.Cuts": continue
    if d.GetShape() in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_CIRCLE): continue  # closed already
    pts[k(d.GetStart())] += 1; pts[k(d.GetEnd())] += 1
open_ends = [p for p, c in pts.items() if c != 2]
```

### The BOM trap: MPN fields live in the schematic, not the footprint

Testing `footprint.HasFieldByName("MPN")` on a board reported **71 of 71 components missing an
MPN**, which was wrong and briefly went into a review. Symbol fields do not propagate to
footprints unless the project is configured to push them. Read MPNs from `netlist.net` or the
schematic — the same board had 67 of 71, the exceptions being mounting holes, which need none.

This is *"bounded searches lie"* in a new costume: the test returned data, the data was
uniform, and uniformity read as a finding rather than as a broken probe. A result that
condemns *everything* deserves the same suspicion as one that condemns nothing.

While there, flag components whose Value is still a placeholder — a library name like
`R_Small` or a bare `R` on a fitted part means nobody can build it.

### Commit the outputs with the hash they came from

Export, then record the board md5 alongside the gerbers, and commit both. Otherwise there is no
way to prove later which copper is in the boards on the bench — and a board file that has been
opened and re-saved in the GUI since the export will not match, for reasons that are pure
reordering (see *`LoadBoard` → `Save` does not round-trip*) and therefore invisible in a diff.
