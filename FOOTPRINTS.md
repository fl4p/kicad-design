# Footprints and land patterns

Editing a footprint, choosing a land pattern, and changing a part's package. The three
layers of a pad are independent and DRC checks almost none of this. Layout judgement is
in [`PCB.md`](PCB.md); `pcbnew` scripting in [`PCBNEW.md`](PCBNEW.md).

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

### When placement refuses, enumerate — do not nudge and retry

Adding one 0603 to a full board took four refusals, and each one named a *different*
constraint: 0.670 mm into a connector's courtyard; then the only lane wide enough for that
connector's hazard warning; then 1.550 × 1.970 mm into an isolator's courtyard; then a
header's pin-label column, where the label could no longer be placed. Nudging after each would
have kept finding the next one at random.

Enumerate instead. Grid the region and reject a candidate that collides with **any** of:

1. every footprint courtyard (`GetCourtyard(F_CrtYd).BBox()` — and assert it is non-degenerate,
   per *An empty geometry result reads exactly like a clean one*, in
   [`PCBNEW.md`](PCBNEW.md));
2. **every existing `F.Silkscreen` item's bounding box** — see below;
3. any lane a generator reserves for text it has not placed yet, such as a connector's
   per-pin label column at `pad_x − offset`;
4. a real margin, not zero. A 0.3 mm margin turned "fits" into "does not" on two candidates
   that were clearing a neighbour by 0.01 mm.

The output is the useful thing: on that board the entire primary side had **nine** free
positions for one 0603, all at the same spot. That is a fact worth knowing before arguing
about where the part should go, and it converts "find somewhere" into "there is one place".

**Silkscreen is territory.** This is the non-obvious one. A placement can be geometrically
legal, route cleanly, pass DRC — and still be wrong because it took the only lane on that side
of the board wide enough for a hazard warning. Silk has no courtyard and DRC will not defend
it; only a generator that *refuses when a `Silk` property fails to place* will catch it, which
is an argument for having that check before you need it.

When the part genuinely cannot go where it belongs, say so with the number that makes it
acceptable rather than moving it quietly. A series damping resistor 18.5 mm from its connector
instead of beside it is fine at a 10 ns edge — FR4 propagates at ~6.7 ps/mm, so that is ~250 ps
round trip, ~2.5 % of the edge, and the resistor still damps the cable, which is the reflection
that matters. Write the derivation down; the next reader will otherwise see only that it is in
the wrong place.
