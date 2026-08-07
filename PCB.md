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
  precondition that moves silently. **Before believing a green DRC, diff
  `rule_severities` against defaults and list every `ignore` in the release
  report.** (On the project above, flipping all five back produced no additional
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

## Decoupling is a current loop, not a placement radius

Validate every datasheet-critical bypass by following actual copper from the IC supply pin to
the capacitor and back to the specified return pin or plane entry. Check both capacitor pads,
vias and layer changes. Centre-to-centre or hot-pad-to-pin Euclidean distance can pass a long,
inductive return path and cannot prove that the capacitor is connected across the required
two nodes.

- Place the smallest/highest-frequency capacitor first and route its loop short and direct;
  keep bulk capacitors from displacing it.
- Verify topology as well as distance. Two capacitors in series through a ground net do not
  implement a datasheet-required direct rail-to-rail capacitor.
- Derive any numeric audit limit from a datasheet, package/application geometry or an explicit
  loop-inductance target. Do not raise a failed limit to the distance the finished placement
  happens to provide and then describe that value as verified.
- Make the audit fail when it cannot reconstruct the complete loop. Reporting only the nearest
  pad-to-pin distance creates a precise number for the wrong property.

## No vias in pads — and DRC will not tell you

KiCad's DRC does **not** flag a via sitting inside a pad. If they share a net it
is simply "connected" — which is how a 0.6 mm via can sit inside an 0805 land
through a full adversarial review. At reflow the via barrel wicks solder out of
the joint; the result is a starved joint that looks fine under a microscope.

Write the check yourself — and make it **net-blind**, because the real cases are
same-net:

```python
n = 0
for layer in (pcbnew.F_Cu, pcbnew.In1_Cu, pcbnew.In2_Cu, pcbnew.B_Cu):
    for v in vias:                       # build these explicitly, do not assume
        for ref, p in pads:
            if not (v.IsOnLayer(layer) and p.IsOnLayer(layer)):
                continue
            n += 1
            if v.GetEffectiveShape(layer).Collide(p.GetEffectiveShape(layer), mm(0.2)):
                bad.append(...)          # no net comparison anywhere
assert n, "UNVERIFIED: no via/pad pairs examined at all"
print(f"{n} via/pad pairs examined, {len(bad)} hits")   # count beside the verdict
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
practice, with the name that actually works — all three confirmed on KiCad 9.0.4:

| you reach for | it does not exist | use |
|---|---|---|
| `ZONE.SetDoNotAllowZoneFills(...)` | `AttributeError` | `ZONE.SetDoNotAllowCopperPour(...)` |
| `LSET & LSET`, `LSET \| LSET` | `TypeError: unsupported operand type(s)` | `LSET.AddLayerSet()` / `RemoveLayerSet()` / `Contains()` |
| `SHAPE::Collide((x, y), …)` | rejects the tuple | pass a real `VECTOR2I`; `Collide(shape, clearance)` is fine |

Distances come back in internal units — `pcbnew.ToMM()` everything before comparing.

**KiCad's bundled `pcbnew` imports Altium boards.** `PCB_IO_MGR` (all caps — `PCB_IO_Mgr` is an `AttributeError`) converts a `.PcbDoc` to
`.kicad_pcb` programmatically, so an Altium design can be pulled into a scripted KiCad
pipeline rather than re-drawn. Expect to fix up layer mapping afterwards — the import does
not always land copper, mask and silk on the layers you would have chosen — and re-run the
full ladder on the result, because a converted board has had none of your generator's
invariants applied to it.

**A pad named as a route endpoint carries no net with it.** A router helper that resolves
`"R11.2"` to coordinates will happily let you route net *A* to a pad belonging to net *B*: the
track lands on the neighbouring land, and the only symptom is a DRC `shorting_items` that
reads like a clearance problem rather than the wiring error it is. Getting a two-pad part's
pad-1 direction letter backwards is enough to trigger it. Check the net at the endpoint, not
just its position:

```python
if isinstance(p, str) and self.pad_net(p) != netname:
    sys.exit(f"track({netname!r}): endpoint {p} is on net {self.pad_net(p)!r}")
```

Calibrate by re-introducing the swapped pad number and watching it exit non-zero. Expect this
to be free on an existing board — it found no false positives on ~60 routed polylines — which
is the point: it costs nothing and removes a whole silent failure mode from the generator.

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
- **A via that lands outside its pour is silently unconnected.** Re-placing an LDO
  block 3.5 mm moved its `+5V` pins past the edge of the L3 pour; the plane vias
  then dropped onto bare laminate. Nothing in the placement code knew. After any
  re-place, assert every plane via actually falls inside a filled area of the zone
  it is supposed to reach — or add a zone that covers the strays and assert the
  fill count.


## Making a `pcbnew` layout reproducible — there are two causes, not one

`--repro` on a board fails for a reason the schematic side never hits, and fixing
only the obvious half leaves the md5 still wobbling.

1. **Random UUIDs.** Every track, via, zone and text created through the API gets a
   random UUID, and SWIG exposes `m_Uuid` **read-only** — there is no `SetUuid`. So
   the canonicalisation has to be a string-aware s-expression pass over the *saved*
   file, assigning `uuid5` over each item's own identity (refdes for footprints;
   net/layer/width/coords otherwise, prefixed by the owning footprint's refdes).
2. **Zone fills depend on item order.** KiCad orders items by UUID, and `ZONE_FILLER`'s
   boolean operations walk that order — so with random UUIDs the *fills* came out
   differing run to run by the odd redundant collinear vertex. Geometrically
   identical, textually not, and it defeats any hash comparison.

Therefore: **canonicalise ids and item order first, then fill.** Filling before
canonicalising bakes the old order into the polygons and the md5 keeps moving while
every geometric check reports PASS.


## Geometry helpers are guards, and fail the same way

**A segment-to-segment distance that only compares endpoints reports a large number for
two segments crossing in a perfect X.** One returned **5 mm** for a genuine crossing and
hid **17 real track crossings** from the router's own overlap check. Endpoint-to-endpoint
distance is not segment distance: handle the intersecting case explicitly, then calibrate
by feeding the helper two segments that cross at their midpoints and watching it return 0.

The same applies to any `near()`-style spatial index used to prune clearance checks: if it
ever returns a *subset* of the true neighbours, every clearance check built on it silently
starts passing — absence of evidence encoding absence of the problem. Calibrate it as a
**superset** property against brute force on a real board, not on a toy case.

**An empty geometry result reads exactly like a clean one.**
`fp.GetCourtyard(pcbnew.F_CrtYd).BBox()` returns `(0, 0, 0, 0)` when the footprint has no
courtyard on that layer. A neighbour scan that computes gaps from those boxes then finds no
overlaps anywhere and prints nothing — which is indistinguishable from "nothing is in the
way", and was very nearly reported as "the larger part fits". Assert the box is
non-degenerate before using it, and make a scan that examined **zero** candidates say so
rather than falling through to silence. Cheap general fix: print the number of items
considered next to the verdict, so a scan of nothing cannot masquerade as a scan that passed.


## Isolated designs: the binding clearance is zone-to-zone, and DRC is not asked

On a board with primary and secondary domains, the minimum copper-to-copper distance almost
never occurs between tracks — it occurs **between the two ground/power pours on the inner
layers**, which is exactly where nobody looks. Measured on one 4-layer board: F.Cu 4.020,
GND 4.000, PWR 4.295, B.Cu 4.000 mm — the two binding numbers were both zone-to-zone.

DRC will not check any of this unless a rule asks it to, so an **independent audit must
measure real geometry on every copper layer**, zones included, and enforce the stated figure.
A secondary track sitting 3.638 mm from primary pads passed DRC cleanly for exactly this
reason. Set the floor *at* the standard, calibrate it by injecting a known-bad geometry, and
scope any package-bridging exemption (an isolator or DC/DC straddles the barrier by
construction) to pairs where **both** items belong to that package **and** touch its pads —
bounded by its own measured floor, so a new object cannot inherit the excuse.

An exemption does **not** satisfy the original requirement. If a package's own measured floor
is below the stated board minimum, report the normal clearance and package deviation
separately and fail release unless the design records an approved waiver or a revised derived
requirement tied to that exact part and working voltage. Never print an overall `PASS >= 4 mm`
for a design whose approved geometry includes 3.5 mm; bounded is not compliant.

**Do not slot a plane to steer digital return current on a precision analog board.** It is
the textbook move and it is usually wrong here: the converter datasheet asks for a
*continuous* return beneath it, and a moat raises the impedance of the **analog** return to
fix a **digital** problem. Solve it with placement — put the noisy return corridor physically
away from the sensitive part and measure the clearance you achieved.


## Modifying a footprint: copper, mask and paste are three independent layers

If you narrow a pad's **copper** for creepage, `F.Mask` and `F.Paste` do **not** follow.
This nearly shipped: an exposed pad was cut 2.95 → 2.00 mm and its mask 2.71 → 1.80 mm for
HV clearance, while the four paste apertures stayed at their original size — printing paste
**2.49 mm wide**, 0.245 mm *outside* the copper and onto bare solder mask, right in the
0.675 mm channel between −15 V and +110 V. Creepage measured on copper said 0.675 mm; the
real post-reflow figure was **0.430 mm**, below IPC-2221C Table 6-1 **B2** (0.6 mm, internal
/ external-with-permanent-coating, 101–170 V).

**Which column applies is not obvious, and getting it wrong is worth 0.2 mm here.** A5–A7 are
the *assembly* columns — component leads and their terminations, i.e. the pad-to-pad case
above once parts are on. B1–B4 are the *bare-board* conductor columns — track to track, track
to land. They disagree by enough to flip a verdict: 0.670 mm passes A7 and fails A6, while
0.675 mm passes B2 — three numbers within 5 µm of each other with three different answers.
State the column, the voltage band and the coating status every time, or the number means
nothing. And note this is IPC-2221**C** (Dec 2023), which supersedes B; the B-era values
quoted historically in this file were not re-verified against C's Table 6-1, so re-read it
before leaning on a marginal figure.

**None of this covers an isolation barrier.** IPC-2221 is a PCB design standard and does not
address reinforced/functional isolation. If the board has a barrier — mains, or any
safety-relevant separation — the binding documents are IEC 60664-1 / 62368-1, where clearance
(through air) and creepage (across surface) are *separate* quantities derived from working
voltage, pollution degree, overvoltage category and material group/CTI, and where slotting the
board is a legitimate remedy. Do not enforce a round number nobody derived: write down which
standard, which table, and the four inputs, or say plainly that the figure is a house rule.

After editing any pad, measure all three layers:

```python
for layer, name in ((pcbnew.F_Cu,"copper"), (pcbnew.F_Mask,"mask"), (pcbnew.F_Paste,"paste")):
    ...  # min/max extents per layer, then the gap to the nearest foreign-net land
```

Also: **never print paste over an open via barrel** — solder wicks down it. If thermal vias
sit inside the pad's mask opening, either shape the apertures to miss them, or specify
plugged/filled vias (IPC-4761) in the fab notes.


## Substituting a larger package: make the change, don't reason about it

Parts grow for real reasons — 0805 → 1210 for a voltage rating, 0805 → 2512 for fault power.
"Does it still fit" is answered by **doing it on a copy of the board and running DRC**, not by
measuring the neighbours you thought of.

A review that reasoned about the space around a capacitor concluded an 1812 would drop in
"without moving C8, J1 or the +110 V rail", citing ≥3 mm of clear board **north and east**. It
was wrong: the binding neighbours were **west** (an 0805 at 2.5 mm centre-to-centre, where the
new part needed 3.25 mm) and **south-east** (a connector courtyard). Dropping it in produced
seven violations and the corner had to be re-laid out. The failure mode is checking the
directions that have room — and it is the same shape as an empty-scan false pass, one level up.

```sh
cp board.kicad_pcb /tmp/t.kicad_pcb    # then swap the footprint via pcbnew,
                                       # keeping position, rotation and pad nets
$K pcb drc --format json --severity-all --exit-code-violations \
    -o /tmp/drc.json /tmp/t.kicad_pcb
```

Thirty seconds, and it settles courtyard, clearance and silkscreen at once. Re-run any
independent geometric audit on the copy too: a bigger package usually **improves** creepage
(a 1210's terminations are ~1.5 mm apart against an 0805's 0.9 mm), so the substitution can
drop the part out of a package-exception list entirely. That is worth knowing before you argue
for it, and worth recording after — an exception that no longer needs to exist should be
deleted, not left standing as a precedent for the next part.
