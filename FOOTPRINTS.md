# Footprints and land patterns

Editing a footprint, choosing a land pattern, and changing a part's package. The three
layers of a pad are independent and DRC checks almost none of this. Layout judgement is
in [`PCB.md`](PCB.md); `pcbnew` scripting in [`PCBNEW.md`](PCBNEW.md). Read
[`THERMALS.md`](THERMALS.md) for exposed heat-transfer lands, thermal vias and their fab process.

## Contents

- [Verify the land pattern, not the name](#verify-the-land-pattern-not-the-name)
- [Keep ordinary vias out of pads](#keep-ordinary-vias-out-of-pads)
- [Treat copper, mask, and paste independently](#treat-copper-mask-and-paste-independently)
- [Test package substitutions on a copy](#test-package-substitutions-on-a-copy)
- [Enumerate placement candidates](#enumerate-placement-candidates)

## Verify the land pattern, not the name

Treat a stock footprint matching the vendor, pin count, and body dimensions as a candidate only.
Compare its copper pad size, pad centres, orientation, pin-1 corner, mask, and paste against the
selected part's recommended land pattern. Record the datasheet page and package code used.

**Example failure shape — same family and body, different land.** Parts sharing a vendor, pin count,
body size, and package family can still use different pad centres or pin-1 orientation. The example
changes the action: compare the installed footprint with the exact selected part's current drawing
instead of accepting a plausible library name.

When creating a custom footprint, calibrate the generator against a known land pattern before
using it for the new one. Require the emitted pad inventory and unioned geometry to match the
datasheet, including repeated pad numbers and notched or merged same-net lands.

## Keep ordinary vias out of pads

KiCad's DRC does **not** flag a via sitting inside a pad. If they share a net it
is simply "connected" — which is how a 0.6 mm via can sit inside an 0805 land
through a full adversarial review. At reflow the via barrel wicks solder out of
the joint; the result is a starved joint that looks fine under a microscope.

Write the check yourself — and make it **net-blind**, because the real cases are
same-net:

```python
bad, pairs = [], 0
# Derive the layer set. A hardcoded tuple can skip added inner layers while the
# guard still reports nonzero coverage.
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

## Treat copper, mask, and paste independently

If you narrow a pad's **copper** for creepage, `F.Mask` and `F.Paste` do **not** follow. A paste
aperture can remain wider than the new land and deposit conductive material into the spacing the
copper edit was intended to create. Recompute the assembled geometry from copper, mask, paste, and
component terminations rather than reporting the copper-only gap.

Select the compliance rule from the current binding standard. Record its revision, table, column,
voltage band, actual working/fault voltage, coating state, and whether it applies to bare-board
conductors or an assembled termination. Do not reuse historical IPC-2221B numbers as IPC-2221C
verdicts; no numeric IPC pass/fail claim in this reference is authoritative without that complete
citation.

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

When vias intentionally share an exposed land for heat transfer, use the paste, barrel and fab
construction rules in [`THERMALS.md`](THERMALS.md); an ordinary same-net via in a pad remains a
manufacturing defect under the net-blind check above.

## Test package substitutions on a copy

Parts grow for real reasons, such as voltage rating or fault power. Answer "does it fit" by changing
the footprint on a scratch copy and running DRC, not by measuring only the neighbours or directions
that first look tight.

```sh
K="${KICAD_CLI:-kicad-cli}"             # set KICAD_CLI when it is not on PATH
cp board.kicad_pcb /tmp/t.kicad_pcb    # then swap the footprint via pcbnew,
                                       # keeping position, rotation and pad nets
"$K" pcb drc --format json --severity-all --exit-code-violations \
    -o /tmp/drc.json /tmp/t.kicad_pcb
```

Re-run independent geometry and thermal audits on the copy too. A larger package can improve
terminal spacing while worsening courtyard, placement, or loop geometry. Remove any package
exception that the new measured geometry no longer needs.

## Enumerate placement candidates

When placement is crowded, enumerate candidates instead of nudging after each newly discovered
constraint. Grid the region and reject a candidate that collides with **any** of:

1. every footprint courtyard (`GetCourtyard(F_CrtYd).BBox()` — and assert it is non-degenerate,
   per *An empty geometry result reads exactly like a clean one*, in
   [`PCBNEW.md`](PCBNEW.md));
2. **every existing `F.Silkscreen` item's bounding box** — see below;
3. any lane a generator reserves for text it has not placed yet, such as a connector's
   per-pin label column at `pad_x − offset`;
4. a requirement-derived margin rather than zero clearance.

Report the number and regions of legal candidates. That converts "find somewhere" into measurable
placement evidence and makes an empty search fail visibly.

**Silkscreen is territory.** This is the non-obvious one. A placement can be geometrically
legal, route cleanly, pass DRC — and still be wrong because it took the only lane on that side
of the board wide enough for a hazard warning. Silk has no courtyard and DRC will not defend
it; only a generator that *refuses when a `Silk` property fails to place* will catch it, which
is an argument for having that check before you need it.

When a part genuinely cannot go where the heuristic prefers, record the quantitative condition that
makes the exception acceptable. For signal-integrity placement, derive propagation delay and the
relevant edge-time fraction rather than declaring a distance harmless by inspection.
