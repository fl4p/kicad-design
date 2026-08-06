# PCB layout and footprints (KiCad)

Companion to `SKILL.md`. **Read this file when the task involves the board** —
`.kicad_pcb`, `.kicad_mod`, `pcbnew` scripting, DRC, zones, footprints, land
patterns, stackup or creepage. Schematic-only work does not need it.

Everything in `SKILL.md` still applies here: generate rather than hand-place,
climb the whole verification ladder, and write guards that fail when they cannot
evaluate their input.

## Board-specific rungs of the verification ladder

DRC green means "no rule was broken", not "the design is right". In particular:

```sh
$K pcb drc --severity-all --schematic-parity -o drc.rpt x.kicad_pcb
```

- **`--schematic-parity` is not optional.** It is the only check that the board
  still matches the netlist.
- **A rule area that relaxes a constraint is keyed on *position*.** Anything that
  later moves into it silently stops being held to the strict value, and DRC
  stays green. Any relaxation needs an independent geometric audit that measures
  real clearance (binary-search `SHAPE::Collide`) rather than asking the rules.
- **Re-run the layout script after *any* schematic change**, not just after
  connectivity changes — see the parity note below.

## No vias in pads — and DRC will not tell you

KiCad's DRC does **not** flag a via sitting inside a pad. If they share a net it
is simply "connected" — which is how a 0.6 mm via can sit inside an 0805 land
through a full adversarial review. At reflow the via barrel wicks solder out of
the joint; the result is a starved joint that looks fine under a microscope.

Write the check yourself — and make it **net-blind**, because the real cases are
same-net:

```python
for v in vias:
    for ref, p in pads:
        if v.GetEffectiveShape(layer).Collide(p.GetEffectiveShape(layer), mm(0.2)):
            bad.append(...)          # no net comparison anywhere
```

Expect such a scan to find more than was reported: one instance typically comes
with several others (supply and ground vias inside SOIC lands are common) plus a
tail of near-misses in the 0.03–0.19 mm range. **A user reporting one instance of
a class of defect is reporting the class** — scan for all of it, and say what the
scan found.

When a via genuinely has nowhere to go — two chip lands 0.22 mm apart, a SOIC pin
0.5 mm from its decoupler — step it *off the axis* rather than squeezing it
between: run a short stub of track and put the via where there is room.


## The stackup is part of the design, not a fab preference

If the board file has no `(stackup ...)` block, KiCad assumes a default and the
fab builds whatever is cheapest that week — while your design doc quotes
dielectric-dependent numbers (stray capacitance, impedance, creepage class) that
depend on a stackup nobody agreed to. Write it explicitly.

The SWIG `pcbnew` API does not usefully expose `BOARD_STACKUP` (you get an opaque
`SwigPyObject` with no methods), so this has to be a text edit on the saved
`.kicad_pcb`. Anchor it on a regex that captures the existing indentation —
KiCad indents with tabs, and a hardcoded two-space `"\n  (setup\n"` anchor will
not match. **Make the failure loud**: if the anchor is missing, exit; do not
return quietly, or the stackup silently stops being written and every number that
depends on it becomes unbacked. Then verify KiCad *parses* it rather than merely
tolerating it — load and re-save through `pcbnew` and confirm the block survives
the round-trip.


## PCB / `pcbnew` notes

Run layout scripts with KiCad's **bundled** Python — `pcbnew` is not importable from a normal
venv:

```
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
```

It prints a harmless `create wxApp before calling this` assert; ignore it. API traps met in
practice: `ZONE` has no `SetDoNotAllowZoneFills`; `LSET & LSET` is not supported; and
`SHAPE::Collide` wants a real `VECTOR2I`, not a tuple. Distances come back in internal units —
`pcbnew.ToMM()` everything before comparing.

**A schematic edit that changes no nets can still break `--schematic-parity`.** Renaming a
symbol's *Value* field desyncs it from the value stored in the `.kicad_pcb` footprint. Re-run
the layout script after any schematic change, not only after connectivity changes.

**Zone fills are a cache.** Changing a clearance *rule* does not move filled copper until
zones are re-filled, so any geometric measurement afterwards is against stale copper. Re-fill
(`ZONE_FILLER`) before measuring, or you will "verify" the previous state. This is a classic
false negative when *calibrating* a clearance guard: tightening the rule to force a violation
appears to do nothing, and the guard looks broken when in fact the test was.

- **A thermal pad wants solid copper, not thermal relief.** Zones default to
  `ZONE_CONNECTION_THERMAL`; spokes on an exposed-pad land starve exactly the
  connection the island exists to make. DRC's `starved_thermal` check catches it
  (`zone min spoke count 2; actual 1`) — set `ZONE_CONNECTION_FULL` on that zone.
- **A track endpoint sitting on top of a zone is not connected to it** if they
  are on different layers. It needs a via. DRC reports this as `track_dangling`
  plus an unconnected item; both point at the same missing via.


## Modifying a footprint: copper, mask and paste are three independent layers

If you narrow a pad's **copper** for creepage, `F.Mask` and `F.Paste` do **not** follow.
This nearly shipped: an exposed pad was cut 2.95 → 2.00 mm and its mask 2.71 → 1.80 mm for
HV clearance, while the four paste apertures stayed at their original size — printing paste
**2.49 mm wide**, 0.245 mm *outside* the copper and onto bare solder mask, right in the
0.675 mm channel between −15 V and +110 V. Creepage measured on copper said 0.675 mm; the
real post-reflow figure was **0.430 mm**, below IPC-2221B B2.

After editing any pad, measure all three layers:

```python
for layer, name in ((pcbnew.F_Cu,"copper"), (pcbnew.F_Mask,"mask"), (pcbnew.F_Paste,"paste")):
    ...  # min/max extents per layer, then the gap to the nearest foreign-net land
```

Also: **never print paste over an open via barrel** — solder wicks down it. If thermal vias
sit inside the pad's mask opening, either shape the apertures to miss them, or specify
plugged/filled vias (IPC-4761) in the fab notes.
