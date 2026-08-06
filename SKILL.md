---
name: kicad-design
description: Create or modify KiCad schematics, symbols, footprints and PCB layouts, and review electronic designs against datasheets. Use whenever the task involves KiCad, .kicad_sch/.kicad_pcb/.kicad_sym/.kicad_mod files, schematic capture, PCB layout, ERC/DRC, footprint or land-pattern selection, noise budgets, or checking an analog/mixed-signal design against part datasheets — from any repo. Board-side material (pcbnew, DRC, footprints, stackup, creepage) is in the companion file PCB.md, read on demand so schematic-only work does not pay for it.
---

# KiCad schematic and PCB design

Every rule below exists because the failure it describes actually shipped and had to be
caught. Most were found on precision analog / high-voltage boards, which is where KiCad's
own checks are thinnest — but nothing here is specific to one design.

## Working on the board? Read `PCB.md`

This file covers what is shared plus schematic capture. **PCB layout, footprints,
land patterns, `pcbnew` scripting, zones, DRC, stackup and creepage live in
[`PCB.md`](PCB.md)** — read that file as well when the task touches the board, and
skip it entirely for schematic-only work.

## Core principle: generate, never hand-place

Write a Python **generator** that emits the `.kicad_sch` (and a `pcbnew` script for the
`.kicad_pcb`). Then the design is diffable, reviewable, reproducible, and a fix applies
everywhere at once. Hand-editing a generated file is a bug waiting to happen — put a note in
the docs saying the artefact is generated and the generator is the source of truth.

**Verify reproducibility**: `md5` the output, re-run the generator, `md5` again. Equal or the
generator has hidden state.

Warn the user that GUI edits will be overwritten on the next run, and check for a running
Eeschema/pcbnew holding a stale copy before regenerating.

Generator hygiene, each learned the hard way:

- **Never write a file another generator owns.** The schematic generator rewrote
  `<project>.kicad_pro` wholesale every run, deleting 286 of its 295 lines — the board design
  settings, net classes and custom-rules linkage. The PCB script carefully protected the
  *schematic's* keys from itself; the protection was one-directional. DRC then ran on KiCad
  defaults and went green on a board that was not compliant. Seed shared files only if absent.
- **Derive UUIDs from stable identity**, never from a counter. Counter-derived UUIDs meant
  inserting one resistor changed 78 of 81 symbol UUIDs, and KiCad matches footprints to
  symbols by that path — so a one-part edit re-orphans the whole board. Hash the reference
  designator / net name / coordinates instead.
- **Export the netlist after every structural edit and read it.** Two separate reroutes
  silently merged nets (SCLK+SDI+~CS, then VREF10+GND) because stub endpoints share a column.
  ERC reported *a* problem but not which nets had merged; only the netlist showed that.
- **Beware substring replaces hitting `def` lines.** `s.replace("check_foo()", …)` also matches
  inside `def check_foo():`. Anchor on the full line, or verify the file still parses.


## Ask before you assume: the choices that are the user's, not yours

Some parameters look like engineering defaults but are actually **procurement and
budget decisions the user owns**. Picking one silently and then writing three
pages of rationale for it makes it expensive to change later. Ask up front, in
one message, before any placement:

- **Layer count.** 4 layers is the reflexive answer for a mixed-signal board.
  Ask instead of assuming, and if you have a preference, give the *number* that
  supports it. Beware of writing the rationale after the choice: a stack defended
  by several plausible arguments with no quantity attached to any of them is a
  default wearing a justification. Note that inner planes are not automatically
  better for sensitive nodes — a plane 0.2 mm below a high-impedance node loads it
  ~8x harder than one 1.6 mm below it on a 2-layer board.
- **Board outline and mounting** — enclosure-driven.
- **Assembly process** — hand-solder vs reflow decides whether a QFN or a
  PowerPAD is acceptable at all.
- **Conformal coating** — it changes which IPC-2221B column applies (A6 0.8 mm
  uncoated vs A7 0.4 mm coated), so it decides HV geometry, not just finish.
- **Connector types and pinout** — usually fixed by what plugs into it.

Converting a finished board between layer counts is very doable when a generator
is the source of truth — expect a handful of DRC violations, not a redesign — but
every inner-plane *decision* has to be re-derived, and the design document's
rationale sections have to be rewritten rather than patched. Cheaper to ask.

Related, when a stack changes: **every layer literal is now a liability.** A
hardcoded `CU = (F_Cu, In1_Cu, In2_Cu, B_Cu)` in an audit keeps "checking" layers
that no longer exist. Derive the layer set from `board.GetCopperLayerCount()` and
assert it equals what the audit was written for.


## The verification ladder

Each rung catches what the one below cannot. Climb all of it; stopping early is how
plausible-but-wrong artefacts ship.

```sh
K=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli   # not on PATH by default
$K sch export pdf --black-and-white -o out.pdf x.kicad_sch  # 1. does it even parse?
$K sch erc -o erc.rpt x.kicad_sch                           # 2. ERC
$K sch export netlist --format kicadsexpr -o n.net x.kicad_sch  # 3. are the NETS right?
$K pcb drc --severity-all --schematic-parity -o drc.rpt x.kicad_pcb
```

1. **Parse.** A malformed file fails with a bare `Failed to load schematic` and no line number.
2. **ERC = 0.** Necessary, nowhere near sufficient.
3. **Read the netlist.** ERC cannot tell you that a feedback tap is on the wrong side of a
   resistor. Print every net with its nodes and read them against intent. This is the single
   highest-value check.
4. **Render it and actually look.** Export the PDF and view the image. Overlapping text,
   symbols drawn over their own wires, and collided labels are invisible to every CLI check.
5. **Domain guards** for anything the tools don't model (see *Guards*, below).

Rungs 4 and 5 are where most real defects are caught, and both are easy to skip.
Board-side rungs — `--schematic-parity`, and why a green DRC can still hide a lost
clearance — are in [`PCB.md`](PCB.md).


## KiCad file-format gotchas

| Trap | Reality |
|---|---|
| **Raw newlines in quoted strings** | Break the parser. `Failed to load schematic`, no line number. Escape as `\n`. Cost: a 175-violation file that turned out to be unparseable. |
| **Symbol Y axis is inverted** | Library Y is up, schematic Y is down. Global pin pos = `(X + px, Y - py)` for angle 0. |
| `Device:R` / `Device:C` | Both connect at **±3.81 mm**, regardless of the drawn body size. Do not infer from the graphic. |
| `Connector_Generic:Conn_01xNN` | Pins face **left**, at `(X-5.08, Y + 2.54*(n-1))`. |
| **Power symbols** | Pin is at `(0,0)` with length 0 → the connection point *is* the placement point. |
| **Labels** | Attach only if placed exactly **on** the wire. 1.27 mm off = dangling, silently. |
| **NC pins** | Either omit them from the symbol or place explicit `(no_connect …)`; otherwise ERC complains forever. |
| **Multi-pad nets in footprints** | An exposed pad and its thermal vias often share one pad number. Take the **largest** pad, not the first. |
| **`PWR_FLAG`** | Needed once per net whose only source is a passive connector pin, else `power_pin_not_driven`. Put them in an isolated block — branching off a live stub collides with neighbouring pins. |

### Derive geometry from the library, never from arithmetic

The single biggest source of defects is hand-computed pin offsets. Parse the `lib_symbols`
you are about to embed and expose `pn(ref, pin)`:

```python
def _xf(px, py, ang, mirror):
    x, y = px, -py                       # schematic Y is flipped vs the symbol editor
    if mirror == 'x':   y = -y
    elif mirror == 'y': x = -x
    a = math.radians(ang); ca, sa = round(math.cos(a)), round(math.sin(a))
    return (x*ca + y*sa, -x*sa + y*ca)

def pn(ref, num):                        # -> exact global coords of that pin
    lid, X, Y, ang, mir = INST[ref]
    for n, lx, ly in LIBPINS[lid]:
        if n == str(num):
            dx, dy = _xf(lx, ly, ang, mir)
            return (round(X+dx, 4), round(Y+dy, 4))
    raise KeyError(f"{ref} has no pin {num}")
```

Then wire with `poly(pn("U3","2"), pn("U5","5"))` and the coordinates cannot drift.

### Assert what you can, and know what the assert misses

```python
def wire(x1, y1, x2, y2):
    for v in (x1, y1, x2, y2):
        assert ongrid(v), f"off-grid endpoint {(x1,y1,x2,y2)}"   # 1.27 mm grid
    assert x1 == x2 or y1 == y2, f"diagonal wire {(x1,y1,x2,y2)}"
```

A grid assert alone does **not** catch non-orthogonal wires — two diagonals shipped past it
and had to be found by eye. Add the orthogonality assert. Labels, junctions and no-connects
are not covered by either; check them separately.

### Power-symbol orientation — derive it, don't trust call sites

Every stock power symbol **except `GND`** draws its graphic *upward* from the connection
point (`-15V` and `PWR_FLAG` included — the polarity is in the glyph shape, not its
direction). So a symbol at the bottom of a downward stub, or a `GND` at the top of an upward
stub, is drawn back over its own wire. It is purely graphical, so **ERC never sees it**, and
it is easy to get right in one place and wrong in another.

Audit it instead of eyeballing:

```python
def check_rail_orientation():   # graphic must point AWAY from the attached wire
    bad = []
    for libid, x, y, graphic_down in _RAILS:
        for (x1, y1, x2, y2) in _SEGS:
            for (ax, ay), (bx, by) in (((x1,y1),(x2,y2)), ((x2,y2),(x1,y1))):
                if (ax, ay) != (x, y) or ax != bx:   # not here, or not vertical
                    continue
                if (by > ay) == graphic_down:
                    bad.append(f"{libid} at ({x},{y}) drawn over its own wire")
    # ...and the glyph must not be drawn across some OTHER net's wire either.
    for libid, x, y, graphic_down in _RAILS:
        y0, y1 = (y, y + 2.54) if graphic_down else (y - 2.54, y)
        x0, x1 = x - 1.27, x + 1.27
        for (ax, ay, bx, by) in _SEGS:
            if ax == bx and x0 < ax < x1 and min(ay,by) < y1 and max(ay,by) > y0:
                bad.append(f"{libid} at ({x},{y}) glyph crosses vertical wire")
            elif ay == by and y0 < ay < y1 and min(ax,bx) < x1 and max(ax,bx) > x0:
                bad.append(f"{libid} at ({x},{y}) glyph crosses horizontal wire")
    if bad: raise AssertionError("\n  ".join(sorted(set(bad))))
```

The second loop matters: the first version only checked a symbol's *own* wire and passed a
`GND` whose triangle was drawn straight across an unrelated signal running underneath. When
you add a check *after* seeing a defect, reproduce the defect and watch the new check fire —
otherwise you have only asserted that the fixed version is fine.

### Schematic annotation is not board annotation

A label on the schematic helps whoever reads the schematic. It does **nothing** for whoever
solders the board. Connector pinouts, polarity, danger markings and voltage callouts belong on
`F.Silkscreen`, added from the layout script — derive their position from the real **pad
centres** so they follow the footprint if it moves or rotates:

```python
fp  = board.FindFootprintByReference(ref)
pad = next(p for p in fp.Pads() if p.GetNumber() == num)
px, py = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
```

Silk needs a side choice, not a fixed offset: near a board edge or a neighbouring part, text
running the default direction trips `silk_edge_clearance` or `silk_overlap`. Set the side per
connector and let DRC confirm.

**Respect fab minimums, which DRC does not check by default.** JLCPCB's minimum silkscreen
stroke is 6 mil (0.153 mm) and PCBWay's is 0.15 mm; below that the fab drops the text or ships
it broken. A default of `thickness = height × 0.15` gives 0.12 mm at 0.8 mm text — under both.
Use `max(height × 0.15, 0.15)` and ≥1.0 mm height. Also state the **stackup** explicitly: a
board with no `(stackup …)` block gets the fab's house build, and every dielectric-dependent
number you computed (trace-to-plane stray, return-path coupling) silently assumes one.


## Datasheet discipline

**Never quote a spec from memory.** Download the PDF and read the electrical-characteristics
table. Every one of these was a real error caught by doing so:

- **Noise gain ≠ signal gain.** An op-amp's input-referred noise is multiplied by
  `1 + Rf/Rin`, not by the inverting gain `Rf/Rin`. Using 10 instead of 11 made a whole noise
  budget 10 % optimistic.
- **rms vs p-p.** Pick one per table and label it. Mixing them understated a term by 6.8× —
  and harmlessly for the part chosen, materially for the part rejected, so the comparison that
  justified the decision was not the one computed.
- **Land pattern vs stencil.** The same number appears on both pages meaning different things.
  On a TI PowerPAD the *land* page gives metal and solder-mask opening; the *stencil* page
  gives a paste aperture. Read the "EXAMPLE BOARD LAYOUT" page, not "EXAMPLE STENCIL DESIGN".
- **Datasheets contradict themselves.** One part listed abs max as both 150 V and 160 V in
  different sections. Quote the conservative one and say why.
- **Recommended operating ≠ absolute maximum.** And an absolute maximum is not a design target.
- **Stock KiCad footprints are not safety-checked.** A stock exposed-pad footprint left
  0.200 mm between a −15 V pad and a +110 V pin. Always measure pad-to-pad clearance for HV
  parts; TI land drawings often carry a note explicitly permitting a narrower pad for creepage.
- **Exposed pads are often electrically connected to a rail**, not ground — and if the symbol has no pin
  for it, the netlist cannot enforce it and DRC will not complain. Add an `EP` pin.
- **Diode-clamped pins need series current limiting** — think about power sequencing, e.g. a
  logic rail up before an HV rail.
- **Logic-level compatibility**: a 5 V pull-up into a 3.3 V-only GPIO destroys it.


## Guards (checks, validators, audits)

Apply the global guard checklist in `~/.claude/CLAUDE.md`. EDA-specific instances:

- **A guard whose precondition moves silently stops guarding.** A track moved inside a rule
  area that relaxed clearance to 0.6 mm; the plane pulled back to 0.601 mm; DRC stayed green
  while the stated 1.0 mm design minimum was gone. DRC was not wrong — it was answering a
  different question than the one that mattered. Keep an independent audit that re-measures
  real geometry, and say in the docs that *the audit*, not DRC, enforces the figure, so nobody
  deletes it as redundant.
- **Calibrate against a known-bad input.** Copy the board, inject the exact fault the guard
  exists to catch (e.g. widen the EP land back to the unsafe stock size), and watch it exit
  non-zero. A guard never seen to fire is not a guard.
- **Bounded searches lie.** A `\(text "([^"]{5,600})"` regex silently returned 19 of 26 text
  items and produced a confident "not found" for content that was present. If a search reports
  absence, verify the search could have seen the thing.
- **Derive limits from constants**, never hardcode. A DAC code cap computed from the reference,
  divider ratio and gain moves when those change; a magic `0xD999` silently goes stale.
- **Fail closed, and raise rather than clamp.** Silently clamping an over-limit request makes
  a sweep record two different setpoints at the same actual voltage — data that looks valid.
- **A guard keyed on name literals must assert its subject exists.** `HV_NETS = {"+110V", …}`
  with no existence check meant renaming the net silently removed the entire rail from the
  audit — which still printed PASS. Same flaw in the matching `.kicad_dru` rules, so DRC went
  green in lockstep. Assert the named nets are present, or key on a netclass instead.
- **An exemption must be scoped to the pair, not to one object.** A "package floor" that fires
  when *either* object belongs to that package is a mute button: a router-placed HV track
  0.70 mm from an exposed pad inherited a 0.60 mm package excuse and passed both DRC and the
  audit. Require both objects to belong to the package, and bound any genuine exception
  (e.g. pin escapes, which pitch really does fix) by a measured floor so a *new* closer object
  cannot inherit it.
- **Set floors to the standard, not the standard minus epsilon.** Every floor in one audit sat
  0.01 mm under the figure it cited (`0.79` for "exactly IPC A6 = 0.80"), so it passed
  geometry that did not meet the standard it claimed to enforce.
- **Calibration must cover the case that matters, not the case you already fixed.** A cap guard
  tested `NaN` and `0.01` — both outside its acceptance band — and never tested a *plausible*
  bad measurement inside it, which is the one that raised the cap to full scale.
- **Protection on a precision node has a cost.** A TVS sized for an 85 V input leaks µA near
  breakdown — comparable to the entire load on a node built for 134 µVpp. Clamp at the victim
  end, disconnect with a relay, or document the residual risk; don't reflexively fit the part.


## Reviewing someone else's numbers

Recompute, don't read. Re-derive the arithmetic independently, re-run the statistics from the
raw data, and re-measure geometry from the files. Reviews in this domain are frequently right
about the *defect* and wrong about the *number* — one correctly identified an unsafe footprint
but quoted a dimension that conflated a mask width with a metal width. Confirm the finding and
the figure separately.
