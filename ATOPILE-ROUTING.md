# atopile does not route; it replays routed sub-boards

Unreferenced by [`SKILL.md`](SKILL.md) on purpose — nothing in this repo depends on atopile, and
this is background for evaluating "code-defined hardware" claims, not a workflow. Method for actual
routing stays in [`ROUTING.md`](ROUTING.md); external-router pipelines in
[`AUTOROUTING.md`](AUTOROUTING.md); ownership policy in [`PCB.md`](PCB.md). Adoption history:
[`plans/atopile-adoption.md`](plans/atopile-adoption.md).

Read against `github.com/atopile/atopile` at **`619eda7`, 2026-03-11** (shallow clone, full tree
searched). Every line number below is that commit.

## Contents

- [There is no autorouter, and none is intended](#there-is-no-autorouter-and-none-is-intended)
- [The unit of reuse is a build target's own `.kicad_pcb`](#the-unit-of-reuse-is-a-build-targets-own-kicad_pcb)
- [The interface is inferred by pad matching, and the vote that resolves it is dead code](#the-interface-is-inferred-by-pad-matching-and-the-vote-that-resolves-it-is-dead-code)
- [Placement registers to the highest-pad-count footprint, and cannot rotate](#placement-registers-to-the-highest-pad-count-footprint-and-cannot-rotate)
- [Sync is destructive-and-regenerate, one-directional, group-scoped](#sync-is-destructive-and-regenerate-one-directional-group-scoped)
- [Declared impedance never reaches the copper](#declared-impedance-never-reaches-the-copper)
- [What this is worth to a KiCad generator](#what-this-is-worth-to-a-kicad-generator)

## There is no autorouter, and none is intended

The only occurrence of "autorouter" in the tree is a line in the project template's `.gitignore`
("Autorouter files (exported from Pcbnew)"). No Freerouting integration, no maze or topological
router, no `exporters/pcb/route/`. `README.md:69` states the split outright: *"Layout — place and
route in KiCad"*.

The browser layout editor (`src/atopile/layout_server/`) is **placement-only**: its whole command
vocabulary is `move` / `rotate` / `flip` / `undo` / `redo` (`layout_server/models.py:278-300`). It
parses segments, arcs, vias and zones in order to *render* them (`pcb_manager.py:439-641`) and
offers no way to create one. The LLM agent surface is the same shape —
`layout_get_component_position`, `layout_set_component_position`, `layout_set_board_shape`,
`layout_run_drc` (`server/agent/tools.py:1642-2061`). Nothing in the product creates copper.

What the compiler does own is the netlist and footprint push into `.kicad_pcb`, plus DRC as a
*gate*: `F.PCB.requires_drc_check` runs via kicad-cli (`build_steps.py:849`), with `fail_on_drcs`
in config (`config.py:590`). Gate, not fixer — the same posture as [`RELEASE.md`](RELEASE.md).

## The unit of reuse is a build target's own `.kicad_pcb`

`attach_sub_pcbs_to_entry_points` (`atopile/layout.py:164`) walks the project **and every
dependency's** `ato.yaml` (`layout.py:138-160`), and wherever a build target's entry module matches
a module type instantiated in the current design, attaches a `has_subpcb` trait pointing at that
build's layout file. Consequences worth noting:

- A registry package can ship a laid-out, routed block; instantiating it pulls the copper in. That
  is the actual product claim behind "reusable hardware modules", and it is a packaging claim, not
  a synthesis one.
- Blocks nest. `examples/led_badge` has three build targets: `strip10` (its own board, 78 track
  segments, routed by hand once), `grid10x10` instantiating `OPSCO_SK6805_EC20_strip10[10]`, and
  `badge` instantiating the grid. Route ten LEDs, get a hundred.
- When a footprint is claimed by several candidate sub-layouts, `_choose_sublayout`
  (`exporters/pcb/layout/layout_sync.py:74`) prefers candidates whose PCB actually has `segments`
  — *prefer the layout that is routed* — then the most deeply-qualified module address. This is a
  nesting tie-break, not a variant mechanism.

Footprints carry `atopile_address` and `atopile_subaddresses` properties, so instances are matched
by hierarchical path, never by refdes. That part is sound and worth stealing: it is the same
stable-identity principle [`PCBNEW.md`](PCBNEW.md) applies to UUID derivation.

## The interface is inferred by pad matching, and the vote that resolves it is dead code

There is no port list, no boundary contract. `_generate_net_map` (`layout_sync.py:157`) matches
pads by pad *number* between the sub-PCB footprint and its top-level counterpart, falls back to
nearest pad *size* when a number is duplicated, reads the net on each side, and tallies into
`mapping_counts`. The comment says "Use most frequent mapping"; the code does not do that:

```python
mapping_counts[src_net][tgt_net] = mapping_counts[src_net].get(tgt_net, 0) + 1
# Use most frequent mapping
if src_net not in net_map or mapping_counts[src_net][tgt_net] > max(
    mapping_counts[src_net].values()
):
    net_map[src_net] = tgt_net
```
— `layout_sync.py:218-229`, identical in git `main` and in the 0.15.8 sdist

The just-incremented count is itself a member of `.values()`, so `x > max(… including x …)` is
always false. **The first observation wins and is never revised** — there is no vote. Whichever pad
happens to be visited first decides the mapping for that net, and a conflicting majority cannot
overturn it. (This was already recorded in `plans/atopile-adoption.md`'s declined-items list and in
R2 of the evaluation; an earlier draft of this file described it as a working majority vote and was
wrong.)

Nets that fail to map are assigned **net 0** (`layout_sync.py:322`, via `_get_net_number`) — copper
crossing the block edge silently loses its net rather than raising.

Note the asymmetry: the `.ato` language *does* type module interfaces (`ElectricPower`,
`DifferentialPair`, …), and none of that typing reaches the copper layer. The abstraction stops at
the netlist boundary.

## Placement registers to the highest-pad-count footprint, and cannot rotate

`_calculate_group_offset` (`layout_sync.py:347`) picks the anchor as
`max(group_fps, key=lambda fp: len(fp.pads))` and returns `top_anchor.at - sub_anchor.at`. Two
things follow:

1. The block's spatial registration depends on a part-selection outcome. If the solver picks a
   different part such that a different footprint wins that `max`, the block lands elsewhere.
2. The offset is an `Xy`, not an `Xyr`, with `# TODO rotation?` immediately above the return
   (`layout_sync.py:388`). `kicad.geo.add(sub_fp.at, offset)` (`libs/kicad/fileformats.py:272`)
   preserves each part's *internal* rotation but gives every instance of the block the same
   orientation. You cannot place instance 3 at 90°.

Point 2 alone disqualifies these from being hard macros: a placeable macro needs rotation. This is
copy-and-translate.

## Sync is destructive-and-regenerate, one-directional, group-scoped

`pull_group_layout` (`layout_sync.py:426`) is exposed as the KiCad IPC plugin action "Layout Sync"
(`kicad_plugin/lib.py:68`) and as `ato kicad-ipc layout-sync`. It:

1. `_clean_group` (`layout_sync.py:391`) deletes — by UUID membership in the old KiCad group —
   every segment, arc, via, zone, graphic and non-atopile footprint;
2. `_sync_footprints` moves each address-matched footprint to sub-position + offset, and clones
   non-atopile footprints;
3. `_sync_routes` (`layout_sync.py:293`) deep-copies every segment, arc, zone and via, assigns a
   fresh UUID, rewrites the net through the map, and translates;
4. re-adds everything to a named KiCad group, so a routed block drags as one object.

So re-syncing **is** a region replacement, and editing `strip10.kicad_pcb` propagates to all ten
instances. That is the closest thing here to a swappable region — but it is swapping by *editing
the file the block points at*, not by substituting implementation B for A at a fixed interface.
There is no push-back from top to sub.

The hazard for a reviewer: top-level copper routed *to* a block is not in the group, so
`_clean_group` leaves it — and nothing verifies it still lands on a pad afterwards. It happens to
survive because footprints are restored to identical relative positions, not because any invariant
holds it. Treat a post-sync board as requiring the full connectivity audit in
[`ROUTING.md`](ROUTING.md), not as incrementally verified.

## Declared impedance never reaches the copper

`USB3_IF.py:43-47` constrains `DifferentialPair.impedance` to 76.5–103.5 Ω, cited to USB 3.2
Table 6-12. That constraint lives in the parameter solver, where it can drive part selection; no
code turns it into a trace geometry, a net class, or a check against the routed board. Grepping the
tree for `net_class` / `track_width` / `trace_width` returns nothing in the layout path. A board
built by atopile carries a *stated* impedance requirement with no mechanism enforcing it — exactly
the "constraint that reaches no consumer" failure [`AUTOROUTING.md`](AUTOROUTING.md) warns about
for router inputs, arrived at from the other direction.

## What this is worth to a KiCad generator

Transferable:

- **Hierarchical-path identity on footprints** as the join key between a source layout and a target
  board, surviving renumbering. Already our practice for UUIDs; the property-based variant is
  cheap and inspectable.
- **Named KiCad groups as the unit of replay**, with regeneration scoped to group membership —
  a clean way to make a repeated subcircuit's copper reproducible without a router.
- **"Prefer the candidate that has segments"** as a defaulting rule when several sources could
  supply the same geometry.

Not transferable as-is, and the reasons are the interesting part:

- An inferred net map with a silent net-0 fallback is a guard that answers OK on unevaluable input
  — [`GUARDS.md`](GUARDS.md) rule 1. Any version of this we build must fail loudly on an unmapped
  net.
- Anchor-by-pad-count couples geometry to part selection. Register to an explicit datum instead.
- Translation-only replay forecloses the mirrored/rotated repeats that real boards need.
