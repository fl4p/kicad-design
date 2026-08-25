# Footprints and land patterns

Editing a footprint, choosing a land pattern, and changing a part's package. The three
layers of a pad are independent and DRC checks almost none of this. Layout judgement is
in [`PCB.md`](PCB.md); `pcbnew` scripting in [`PCBNEW.md`](PCBNEW.md). Read
[`THERMALS.md`](THERMALS.md) for exposed heat-transfer lands, thermal vias and their fab process.

## Contents

- [Verify the land pattern, not the name](#verify-the-land-pattern-not-the-name)
- [Qualify external spacing and lead fit](#qualify-external-spacing-and-lead-fit)
- [Classify via-in-pad by process](#classify-via-in-pad-by-process)
- [Treat copper, mask, and paste independently](#treat-copper-mask-and-paste-independently)
- [Test package substitutions on a copy](#test-package-substitutions-on-a-copy)
- [Enumerate placement candidates](#enumerate-placement-candidates)

## Verify the land pattern, not the name

Treat a stock footprint matching the vendor, pin count, and body dimensions as a candidate only.
Compare its copper pad size, pad centres, orientation, pin-1 corner, mask, and paste against the
selected part's recommended land pattern. Record the datasheet page and package code used. If the
datasheet fetch fails or returns an empty/denial response, follow the canonical access-recovery
contract linked from [`SETUP.md`](SETUP.md) §*Escalate through web defenses* before accepting any
pad/pin-1 value — a 2xx with an empty body is a false-success shape, not absence.

**Example failure shape — same family and body, different land.** Parts sharing a vendor, pin count,
body size, and package family can still use different pad centres or pin-1 orientation. The example
changes the action: compare the installed footprint with the exact selected part's current drawing
instead of accepting a plausible library name.

When creating a custom footprint, calibrate the generator against a known land pattern before
using it for the new one. Require the emitted pad inventory and unioned geometry to match the
datasheet, including repeated pad numbers and notched or merged same-net lands.

## Qualify external spacing and lead fit

Device-internal dielectric or isolation ratings do not qualify external package-pin, termination,
pad, clearance or creepage geometry on the assembled PCB; derive those requirements separately.
For PTH leads, compare the maximum lead envelope, including non-round geometry, with the minimum
finished-hole opening after the applicable fabrication tolerance and required assembly allowance.
Account for drill and plating variation only when the source dimension is pre-plate; do not
subtract plating again from a specified finished-hole limit. Require insertion testing when
drawings, tolerances, lead finish or the assembly process do not establish fit.

Mark unresolved mechanical or interface footprints with machine-readable draft status and
enumerate them in release evidence. Any such draft prevents revision `FINAL` and functional design
release; a deliberately scoped experimental order remains subject to [`RELEASE.md`](RELEASE.md)'s
waiver contract.

## Classify via-in-pad by process

KiCad's DRC does **not** flag a via sitting inside a pad. If they share a net it is simply
"connected." Whether that construction is acceptable depends on the via type and finish, pad and
paste geometry, assembly method, board side, and the fabricator's and assembler's qualified
process. An untreated open through-via in a reflowed solderable land can wick solder and starve the
joint. A filled and capped via-in-pad, a qualified microvia, or another construction explicitly
qualified by the fabricator and assembler may be intentional. Qualification must identify the via
dimensions and finish, board side, paste aperture, and assembly method; tenting alone is not
acceptance evidence.

Establish the project's via-in-pad policy before writing a guard. When the declared process forbids
overlap or requires a keepaway, make the check **net-blind** because the relevant cases are normally
same-net. Derive the forbidden margin from that process; do not turn the 0.2 mm margin from one
board into a universal value. Scope reviewed exceptions to the exact via, pad, construction, and
assembly condition that justify them:

```python
violations, exceptions, pairs, policy_hits = [], [], 0, 0
forbidden_margin = pcbnew.FromMM(project_via_pad_margin_mm)
# Derive the layer set. A hardcoded tuple can skip added inner layers while the
# guard still reports nonzero coverage.
layers = [l for l in board.GetEnabledLayers().CuStack()]
for v in vias:                           # build these explicitly, do not assume
    for ref, p in pads:
        shared = [l for l in layers if v.IsOnLayer(l) and p.IsOnLayer(l)]
        if not shared:
            continue
        pairs += 1                       # count PAIRS, not (via, pad, layer) triples
        policy_hit = any(v.GetEffectiveShape(l).Collide(p.GetEffectiveShape(l),
                                                        forbidden_margin) for l in shared)
        if not policy_hit:
            continue
        policy_hits += 1
        record = ...                     # geometry, policy, construction and margin
        if reviewed_via_in_pad_exception(v, ref, p, shared):
            exceptions.append(record)
        else:
            violations.append(record)    # no net comparison anywhere
# not `assert` -- python -O deletes it, and this is the only guard in the snippet
if not pairs:
    raise RuntimeError("UNVERIFIED: no via/pad pairs examined at all")
print(f"{pairs} pairs over {len(layers)} copper layers: {policy_hits} policy hits, "
      f"{len(violations)} violations, {len(exceptions)} reviewed exceptions")
```

When one construction violates the declared process, scan the entire board for the same class and
report both violations and reviewed exceptions. A user reporting one instance may be reporting a
repeated process mismatch rather than an isolated coordinate.

When the process forbids via-in-pad and a via has nowhere to go, step it off the axis rather than
squeezing it between lands: run a short stub of track and put the via where the required clearance
and routing geometry allow it.

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
construction rules in [`THERMALS.md`](THERMALS.md). For other via-in-pad uses, record the qualified
construction and assembly process and make the net-blind guard enforce that project policy rather
than a universal prohibition.

## Test package substitutions on a copy

Parts grow for real reasons, such as voltage rating or fault power. Answer "does it fit" by changing
the footprint on a scratch copy and running DRC, not by measuring only the neighbours or directions
that first look tight.

For an accepted routed board, prefer the incremental transaction described in [`PCB.md`](PCB.md):

```sh
python3 scripts/kicad_footprint_swap.py --spec project/footprint-swap.json
```

Dry-run first. Map connected pads by pad-number **sets** because repeated pad numbers are valid;
reject missing, extra, or ambiguous connected sets. Preserve accepted copper unless a refilled DRC
finding requires an exact project-owned local delta. Re-run independent geometry, process, and
thermal audits on the exact DRC-saved candidate. A larger package can improve terminal spacing while
worsening courtyard, placement, via-in-pad, or loop geometry. Remove any package exception that the
new measured geometry no longer needs.

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
