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
bad, pairs = [], 0
# Derive the layer set -- do NOT hardcode (F_Cu, In1_Cu, In2_Cu, B_Cu).  On a
# 6-layer board that literal skips In3/In4, the count still comes out non-zero,
# and the guard reports coverage it did not have.  See SKILL.md, "every layer
# literal is now a liability".
layers = [l for l in board.GetEnabledLayers().CuStack()]
for v in vias:                           # build these explicitly, do not assume
    for ref, p in pads:
        shared = [l for l in layers if v.IsOnLayer(l) and p.IsOnLayer(l)]
        if not shared:
            continue
        pairs += 1                       # count PAIRS, not (via, pad, layer) triples
        if any(v.GetEffectiveShape(l).Collide(p.GetEffectiveShape(l),
                                              pcbnew.FromMM(0.2)) for l in shared):
            bad.append(...)              # no net comparison anywhere
# not `assert` -- python -O deletes it, and this is the only guard in the snippet
if not pairs:
    raise RuntimeError("UNVERIFIED: no via/pad pairs examined at all")
print(f"{pairs} via/pad pairs over {len(layers)} copper layers, {len(bad)} hits")
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
practice, with the name that actually works — **re-probed on KiCad 10.0.5 on 2026-08-09**:

| you reach for | it does not exist | use |
|---|---|---|
| `ZONE.SetDoNotAllowCopperPour(...)` | `AttributeError` **on 10.0.5** | `ZONE.SetDoNotAllowZoneFills(...)` — **this pair REVERSED between 9.0.4 and 10.0.5.** On 9.0.4 it was exactly the other way round, and this table said so. Probe both names and use whichever answers; do not hard-code either. |
| `LSET & LSET`, `LSET \| LSET` | `TypeError: unsupported operand type(s)` | `LSET.AddLayerSet()` / `RemoveLayerSet()` / `Contains()` — unchanged on 10.0.5 |
| `SHAPE::Collide((x, y), …)` | rejects the tuple | pass a real `VECTOR2I`; `Collide(shape, clearance)` is fine — unchanged on 10.0.5 |
| `PAD.GetPos0()` / `SetPos0(...)` | `AttributeError` | `PAD.GetFPRelativePosition()` / `SetFPRelativePosition(...)` — and note it moves the pad's global position too, so don't "correct" that afterwards. Unchanged on 10.0.5 |
| `board.GetNetsByName().get(name)` | `NETNAMES_MAP` has no `.get` | `board.FindNet(name)`, and check for `None` — unchanged on 10.0.5 |

**Four of five survived the major-version bump and one inverted, which is the worst possible
ratio**: it is exactly high enough to make "the table still holds" the natural assumption, and
the one that moved fails as an `AttributeError` at the call site rather than as a wrong number,
so it is loud when hit — but only if that branch is exercised. Re-probe the table on any
version change; it costs one script.

**Probing `pcbnew` safely.** Two things will waste an hour otherwise:

- **Run the probe with `python3 -u`.** A `pcbnew` call that crashes the interpreter takes
  buffered stdout with it, and you get a bare non-zero exit with *no output at all* and no clue
  which line died. Unbuffered, the last line printed is the line before the crash.
- **Probe against a real `LoadBoard()`, not a synthetic object.** Constructing an orphan
  `pcbnew.PAD(pcbnew.FOOTPRINT(board))` and calling `GetFPRelativePosition()` on it **SIGBUSes
  the interpreter** (exit 138) on 10.0.5; the same call on a pad from a loaded board returns
  fine. A crash while probing is not evidence that the API is missing.

Distances come back in internal units — `pcbnew.ToMM()` everything before comparing.

**`LoadBoard` → `Save` round-trips bit-identically** on a board KiCad 9 wrote (verified: zero
diff lines on a 12 000-line `.kicad_pcb`). That is worth knowing because it makes *surgical*
scripted edits viable on a board you did **not** generate — someone's hand-drawn layout — with
a diff a human can actually review. The "generate, never hand-place" rule in `SKILL.md`
assumes you own the file; when you don't, a script that loads, changes exactly the objects it
asserted it found, re-fills and saves gives you most of the same reproducibility without
seizing ownership of someone else's board. Assert the round-trip on the specific file first —
it is the cheap precondition for trusting the diff.

**`board.Remove()` invalidates the SWIG proxies of the items you did *not* remove.** A later
`board.GetTracks()` raises `'SwigPyObject' object is not iterable`, and — the nastier half —
a proxy you snapshotted into a Python list *before* the removal silently loses its downcast,
so `t.GetStart()` starts returning a bare `SwigPyObject` with no `.x`. Snapshotting is not
the fix. **Resolve every lookup first, then mutate; and do in-place edits before removals**,
because an edit after a `Remove()` is operating on a proxy that may already be stale. The
failure is loud on the second loop iteration, which makes it easy to misread as "my first
edit corrupted the board" rather than "the binding invalidated my handles".

**A probe that returns "nothing" for every input has failed, not answered.**
`board.GetConnectivity().GetConnectedPads(pad)` returned an empty list for *every* pad on a
partly-routed board, including pads whose nets were fully routed. Read as data that would
have meant "the board has no connectivity at all"; read correctly it means the call needs
setup the SWIG binding does not do. This is the `0/24` shape from `SKILL.md` in `pcbnew`
form. Fall back to something that is definitely computed: `kicad-cli pcb drc` writes the
ratsnest as `[unconnected_items]` **pairs**, and diffing that list before and after a change
tells you exactly which connections closed.

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


## Symmetry and matching are invisible to DRC

A board can be DRC-clean, parity-clean and **completely asymmetric**. Nothing in KiCad checks
that a differential pair is matched, that two halves of a current path mirror, or that a
matched-resistor pair sits symmetrically in a thermal gradient. If the design's accuracy rests
on any of that, it rests on a guard you write, and the design docs must say *that* guard — not
DRC — is what enforces it, or the next tidy-up deletes it as redundant.

Four traps, all met on one precision current-sense board:

- **Derive the mirror's net map from the schematic, not from the net names.** Under a
  left/right mirror the nets swap in pairs, and the pairing is a circuit fact: `Shunt+ ↔
  Shunt-` is obvious, but on that board `Net-(JP1-A) ↔ GND` too, because those were the
  shunt's two Kelvin sense terminals. Assume every net maps to itself and the audit reports
  the correctly-mirrored sense pair as broken while missing the pours.

- **A footprint's `(at …)` is a proxy for where the part is; the pads are the truth.**
  `Wuerth_PowerPlus_M5_Nut` carries its origin **1.5 mm off its own pad cluster**, so two lugs
  at x = 109.5 and 132.5 — apparently centred on 121.0 — actually have their pad clusters at
  108.0 and 131.0, i.e. exactly symmetric about the board centre at 119.5. An origin-based
  check reports a false asymmetry. Worse, the *same* proxy error had already reached a design
  review, which derived "the symmetry axis is x ≈ 121" from those origins and concluded the
  wrong one of two matched resistor networks was the thermally exposed one. Compare pad sets.

- **Audit the zone FILL, not the zone outline.** The outline is intent; the fill is what ships,
  and it is shaped by pads, tracks, clearances and the board edge. On that board the two
  current pours had outlines that differed only cosmetically while their *fills* differed by
  3.4 mm².

- **Compare fills geometrically, never by vertex equality.** KiCad segments arcs into chords,
  and two mirror-image arcs get their chords in different places even when the shapes are
  identical — so point-set equality reports pure noise as a defect. Measure (a) filled area and
  (b) the largest distance from any mirrored vertex to the other polygon's boundary. Measured
  on that board: chord noise **~0.005 mm**, real defects **0.8 – 2.5 mm**. Put the tolerance an
  order of magnitude above the noise and state both numbers next to it, so the next reader can
  see the check has headroom rather than being tuned to pass.

**Symmetry is not the whole objective — check the loop area too.** A mirror-symmetric
differential pair can still be a large pickup loop, and the audit that proves the symmetry
will happily pass it. On that board the sense pair was laid out perfectly symmetric at 4.2 mm
spacing, enclosing **143 mm²** between the two conductors; re-laying it at 0.6 mm inside the
existing 1.0 mm gap between the two current pours cut that to **26.5 mm²** with no change to
the symmetry verdict, no new DRC violations, and *more* pour copper than before, because the
pair now runs in a gap that already existed instead of slicing a fresh void through each pour.
Compute the enclosed area explicitly — shoelace over `[source pad A, …trace A…, load, …trace B
reversed…, source pad B]` — and put the number in the design doc, because nothing else will
ever tell you it is too big. Two corollaries: the fan-out from the source's pad pitch is often
the dominant remaining term, so converge it steeply rather than at a tidy 45°; and running the
pair down the gap between two pours *guards* it, provided each conductor is flanked by the
pour nearest its own potential rather than the other one's.

Asymmetries hide in places a placement check never looks. On that board the last one left,
after every coordinate matched, was **pad 1 of a 4-terminal shunt being `rect` while pad 4 was
`circle`** — same size, same drill, the ordinary pin-1 marker. It was 1.65 mm² of extra copper
on one terminal and it carved a correspondingly larger void out of the opposing current plane.
Before changing it, check the part still has a pin-1 marker somewhere else (silkscreen), and
make the script *refuse* if it does not — symmetry is not worth losing orientation over.

Finally: a symmetry audit is exactly the kind of guard that must fail closed. An unreadable
outline, an object class the parser never visited, or a pair that could not be compared has to
raise — "0 asymmetries" out of a scan that examined nothing is the anti-monotone false PASS,
and it is very easy to write here because the happy path prints the same thing.


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
real post-reflow figure was **0.430 mm**. Which column condemns it depends on coating: this is
paste on assembled parts, so it is the **assembly** case (A5–A7), where 0.430 mm clears A7
(0.4 mm, coated) and fails A6 (0.8 mm, uncoated) — i.e. it is a defect on an uncoated board and
marginal-at-best on a coated one. The bare-board copper-to-copper figure in the same channel is
the 0.675 mm, and *that* is the one to rule against B1–B4.

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
