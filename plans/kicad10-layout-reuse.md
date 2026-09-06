# KiCad 10 layout reuse: design blocks and Repeat Layout against ROUTING.md

A landscape survey claimed KiCad 10 ships native layout reuse that makes part of
[`ROUTING.md`](../ROUTING.md) a reinvented wheel: board-layout **design blocks**
(`*.kicad_blocks`) and multichannel **Repeat Layout**, which copies placement *and* routing from a
reference channel and refuses on topology mismatch. This record settles what those two mechanisms
are, what was verified rather than read, and which parts of `ROUTING.md` they touch.

Short answer: both are real and one of them is more useful to a generator than expected — a design
block library is a plain directory of ordinary `.kicad_sch` / `.kicad_pcb` files that a script can
write, and KiCad 10.0.5 places a wholly script-authored one, carrying its routing, into a board it
never came from. Neither mechanism replaces any rule in `ROUTING.md`. Both are *editors*, not
verifiers, and the two places they most nearly touch this file's doctrine — the topology refusal
and the locked-copper contract — are exactly where they fall short of it.

## Contents

- [Provenance of the evidence](#provenance-of-the-evidence)
- [What a design block is in 10.0.5](#what-a-design-block-is-in-1005)
- [Verified: a script-authored design block, placed with its routing, into another board](#verified-a-script-authored-design-block-placed-with-its-routing-into-another-board)
- [Repeat Layout: what it copies and what it refuses](#repeat-layout-what-it-copies-and-what-it-refuses)
- [The refusal is fail-closed as an edit and open as a report](#the-refusal-is-fail-closed-as-an-edit-and-open-as-a-report)
- [The reuse unit: one board, or two](#the-reuse-unit-one-board-or-two)
- [Programmatic access](#programmatic-access)
- [Verdict against ROUTING.md, rule by rule](#verdict-against-routingmd-rule-by-rule)
- [What was not exercised](#what-was-not-exercised)

## Provenance of the evidence

Three source tiers, in the order this record trusts them.

| tier | what | locator |
|---|---|---|
| behaviour | KiCad 10.0.5 on macOS, driven through its GUI | `/Applications/KiCad`, `kicad-cli version` → `10.0.5` |
| local manual | the bundled PCB Editor manual, §13 Multichannel layout and §14 Design blocks | `/Applications/KiCad/KiCad.app/Contents/SharedSupport/help/en/pcbnew.html` |
| source | the KiCad tree at git tag `10.0.5` | `gitlab.com/kicad/code/kicad` — note the tag is `10.0.5`, not `v10.0.5`, which 404s |

The source tag is tied to the installed build rather than assumed to match it: the strings
`Target Rule Area shares components with the reference area` and
`Rule Area topologies do not match: %s`, both introduced in the 10.0.5 `multichannel_tool.cpp`,
are present in the shipped `/Applications/KiCad/KiCad.app/Contents/PlugIns/_pcbnew.kiface`.

The local manual and the source did not disagree anywhere in this investigation. The local manual
and the **web** manual do disagree by version, usefully: `docs.kicad.org/9.0/en/pcbnew/pcbnew.html`
carries the multichannel chapter (30 occurrences of "multichannel", 7 of "Repeat Layout") but has
no design-block chapter at all — its only two mentions of "design block" are the
*Manage Design Block Libraries…* menu row. So **Repeat Layout is not new in 10; board-layout design
blocks are.** Schematic design blocks predate 10; the board half does not.

## What a design block is in 10.0.5

Both, not schematic-only. The local manual, §14: *"Design blocks allow you to save a portion of a
schematic and/or layout and reuse it later. You can reuse design blocks within the same project or
between different projects. … A single design block can contain a schematic fragment, a layout
fragment, or both."*

The on-disk layout, from `common/design_block_io.cpp` (`DESIGN_BLOCK_IO::DesignBlockLoad`,
`::DesignBlockSave`) and confirmed by the library this session built and KiCad then read:

```text
<library>.kicad_blocks/            <- the library is a directory, listed in a design-block-lib-table
  <block>.kicad_block/             <- one directory per block
    <block>.kicad_sch              <- optional schematic fragment
    <block>.kicad_pcb              <- optional layout fragment: an ordinary board file
    <block>.json                   <- {"description": …, "keywords": …, "fields": {…}}
```

The layout fragment is not a special format. It is written by the standard s-expression board
writer — `PCB_IO_MGR::KICAD_SEXP` → `SaveBoard`, in `PCB_EDIT_FRAME::saveBoardAsFile`
(`pcbnew/pcb_design_block_utils.cpp`) — and `DesignBlockSave` then merely *copies* that file into
the block directory. A save-from-selection first clones the selected items into a temporary
`BOARD` seeded with `SetDesignSettings( GetBoard()->GetDesignSettings() )`, recreating each
connected item's `NETINFO_ITEM` by net **name**.

So a block carries: footprint identity and placement, tracks, vias, zones, graphics, the `locked`
flag, and net *names* on connected items. It does not carry net classes or design rules — those
live in the project file, and a block directory has no project file. The source board's board
design settings are snapshotted into the fragment's `(setup …)`; whether any of that is imported at
placement was not tested.

## Verified: a script-authored design block, placed with its routing, into another board

Measured once, in one session, on 2026-09-07, KiCad 10.0.5 on macOS; not replicated.

A library was generated with **no KiCad GUI involved in authoring it** — a Python script against
the bundled SWIG `pcbnew` module built a `BOARD` with two `R_0603_1608Metric` footprints and one
0.4 mm F.Cu segment on a net named `MID`, marked the segment `locked`, called
`pcbnew.SaveBoard()` into `reuse.kicad_blocks/rc_snubber.kicad_block/rc_snubber.kicad_pcb`, and
wrote the sibling `rc_snubber.json` by hand.

Results, in order:

- **The project table filename is `design-block-lib-table`, with hyphens.** A file named
  `design_block_lib_table` — which is the s-expression head, and the spelling the underscore-named
  `fp-lib-table`/`sym-lib-table` pattern does *not* use — was ignored in silence: no error, no
  warning, and the Design Blocks panel showed only `-- Recently Used --`. Renaming the identical
  content fixed it. The global table KiCad writes itself is
  `<KICAD_CONFIG_HOME>/10.0/design-block-lib-table`; copy that spelling.
- **KiCad enumerated the script-authored library.** With
  `(lib (name "reuse")(type "KiCad")(uri "${KIPRJMOD}/reuse.kicad_blocks")…)` in the project table,
  the Design Blocks panel listed `reuse` → `rc_snubber` with the description and keywords from the
  hand-written JSON.
- **It placed into a board that never contained those items,** with the routing. The target board
  read `Pads 0 / Track Segments 0 / Nets 0` before and `Pads 4 / Track Segments 1 / Nets 1 /
  Unrouted 0` after. The saved target file contains the net `MID`, the segment with
  `(locked yes)`, and `(group "rc_snubber" (locked yes) (lib_id "reuse:rc_snubber"))` — the library
  link that later enables *Save to Linked Design Block* and *Apply Design Block Layout*.
- **The `locked` flag round-tripped and was enforced.** The first placement attempt copied nothing
  and raised *"Selection contains locked items. Enable 'Override locks' to operate on them."*
  Placement succeeded only with Override locks enabled — which also means the flag is a UI
  interlock the same toolbar checkbox turns off, not a preservation contract.

That is the finding that matters for a generator-based workflow: **the reuse unit is authorable by
a script even though every consumer of it is a GUI action.**

## Repeat Layout: what it copies and what it refuses

Tools → Multi-Channel → Repeat Layout…. The unit is a *placement rule area*: one reference area
whose footprints are placed and routed by hand, and every other placement rule area **on the same
board** as targets. Rule areas can be sourced from a hierarchical sheet, a component class or a
named group (manual §13.1), and can be generated in bulk by Tools → Multi-Channel → Generate
Placement Rule Areas….

What it copies, from the manual's own options table (§13.2):

| option | what it does |
|---|---|
| Anchor footprint | positions and rotates each channel relative to its anchor, else to the rule-area centre |
| Copy footprint placement | footprints enclosed by **or intersecting** the reference area |
| Copy routing | tracks and vias **fully enclosed** by the reference area; **existing target routing is deleted first** |
| Restrict to routing connected within the area | sub-option: skip tracks whose net has no pad inside the area |
| Copy other items | zones and graphics **fully enclosed** by the reference area |
| Group items with their target rule areas | wraps each target's copied items with its rule area |
| Include locked items | copies locked reference items **and updates locked target items** |

Nets are reassigned through the component match: the tool finds the reference pads on a track's
net, looks up the matched target footprints, and takes the target pad's net (§13.4.2).

Four documented ways the copy is silently partial, all from §13.4.4–§13.4.5 and §13.7:

- routing that leaves the reference rule area is not copied — *"the most common cause"* of missing
  target routing;
- an item on a layer not enabled in **both** rule areas is *"silently skipped"* / *"silently
  omitted"*, and the manual singles out forgotten inner copper layers;
- a track on a global or shared net that cannot be mapped *"may retain the reference channel's
  net"*;
- a zone or graphic only *intersecting* the reference area is not copied at all, so a plane that
  crosses the channel boundary does not travel.

## The refusal is fail-closed as an edit and open as a report

The topology check is a real graph isomorphism, not a name comparison: components are catalogued by
reference-designator prefix, pins by pad, edges by shared net, and
`CONNECTION_GRAPH::FindIsomorphism` searches for a one-to-one mapping preserving every connection
(§13.3.1). The manual is explicit that *"R1 in the reference does not necessarily map to R1 in the
target — it maps to whichever resistor has the same connectivity pattern."*

Where it lands against [`GUARDS.md`](../GUARDS.md):

- **Fail-closed as an edit.** A target that fails the check is not written. The dialog leaves its
  Copy checkbox unchecked and disabled (*"Targets with mismatches are unchecked and cannot be
  selected"*), and `MULTICHANNEL_TOOL::RepeatLayout()` guards the copy with
  `if( !compatData.m_isOk ) continue;`.
- **Not fail-closed as a report.** A skipped target raises nothing. The only completion signal is
  an info bar reading `Copied to %d Rule Areas.`, so an operator who does not read the dialog's
  Status column learns of a refused channel only as a number that is one smaller than expected. In
  this file's terms that is a `PASS`-shaped output over partially unevaluated input.
- **"OK" does not always mean the connectivity matched.** When `FindIsomorphism` fails,
  `resolveConnectionTopology()` falls back to `matchBySymbolInstancePath()`, and on success sets
  `m_isOk = true` and `m_errorMsg = _( "OK" )`. The fallback is not loose — it requires equal
  component counts, a unique non-nil schematic-symbol UUID on every footprint of both sets, and an
  identical `FPID` and pad count per pair — but **it checks no connectivity at all.** Its own
  comment states the intent: *"Net topology can legitimately differ between a design block and its
  placed instance once the user edits connectivity on the board."* So a channel whose nets really
  do differ can be reported OK and receive the reference channel's copper, with track nets
  reassigned through the pad mapping. The manual does not mention this path. Read in code at tag
  10.0.5; **not exercised.**
- **It will overwrite locked target copper.** *Include locked items* is one checkbox and its own
  documented effect is that *"items associated with target rule areas will be updated even if they
  are locked"*, on top of the unconditional deletion of existing target routing.

There is one genuinely fail-closed refusal worth naming, added in 10.0.5: if a target rule area
resolves to the *same* footprints as the reference — two areas pointing at one sheet or component
class — the tool refuses with *"Target Rule Area shares components with the reference area"* rather
than moving and deleting the reference's own items.

## The reuse unit: one board, or two

This is the whole question for this skill, and the two mechanisms answer it differently.

- **Repeat Layout is single-board.** Reference and targets are `ZONE` objects enumerated from
  `board()`. There is no cross-board form of it. It replicates *channels of one design*.
- **Design blocks cross boards and projects.** Verified this session by construction: the block
  was authored standalone and placed into a board that had never held its items. Libraries can sit
  in the global table (all projects) or the project table (one project).
- **The two meet.** `Apply Design Block Layout` — the path that applies a block's saved layout to
  footprints that came from the schematic rather than dropping unlinked copies — is implemented by
  the *same* matcher, through a synthetic rule area with
  `PLACEMENT_SOURCE_T::DESIGN_BLOCK` whose components come from an explicit item list rather than a
  rule-area query (`pcbnew/tools/multichannel_tool.cpp`, `findComponentsInRuleArea`). It requires
  the block to hold both fragments and to be grouped in **both** schematic and PCB (§14.3).
  Read in source and manual; **not exercised.**

So: yes, a proven route can be carried between two different boards, by design block, and the
schematic-linked form of that is the `Apply Design Block Layout` path this session did not run.

## Programmatic access

Decisive for how `ROUTING.md` should describe this, and the answer is split.

**Authoring a block: scriptable, verified.** See above — a library written entirely by a script was
read and placed by KiCad.

**Every consumer of a block: GUI.** Verified by enumeration, not by inference:

- `kicad-cli` 10.0.5 exposes `fp`, `jobset`, `pcb` (`drc`, `export`, `import`, `render`,
  `upgrade`), `sch`, `sym`, `version`. No design-block and no multichannel command.
- The IPC API at tag 10.0.5 — every `message` in `api/proto/board/board_commands.proto`,
  `common/commands/editor_commands.proto` and `common/commands/base_commands.proto` — contains
  nothing for design blocks or multichannel.
- The SWIG `pcbnew` Python module exposes exactly three related symbols, all on `EDA_GROUP`:
  `HasDesignBlockLink`, `SetDesignBlockLibId`, `GetDesignBlockLibId`. That is the group's library
  link, not block IO and not Repeat Layout.
- The action names exist and are reachable in principle through the API's `RunAction`
  (`pcbnew.Multichannel.repeatLayout`, `pcbnew.InteractiveDrawing.applyDesignBlockLayout`,
  `pcbnew.PcbDesignBlockControl.*`, all present in the shipped kiface), but the proto's own comment
  is *"WARNING: The TOOL_ACTIONs are specifically **not** an API. Command names may change as code
  is refactored, and commands may disappear. This API method is provided for low-level prototyping
  purposes only."* — and `MULTICHANNEL_TOOL::repeatLayout()` requires a pre-selected rule area, else
  it starts an interactive picker, then shows a modal dialog before doing anything. There is no
  headless path.

## Verdict against ROUTING.md, rule by rule

| `ROUTING.md` rule | verdict |
|---|---|
| Route the topology, not the copper; placement owns the route | **untouched.** Both features copy a route that already exists; neither decides a topology. |
| Commit a layer plan before the first track | **untouched, and quietly load-bearing for Repeat Layout.** Its layer filter is per-rule-area and its omissions are silent, so an undeclared layer plan turns into missing inner-layer copper with no message. |
| Escape is a stage / do not hand authored escapes to a router at its limit | **untouched.** This is about what a *router* does with authored copper. Neither feature routes. |
| Author the skeleton by hand, in priority order | **complemented, for repeated structures only.** Author one channel's skeleton, replicate it. The ordering rule still governs how that one channel is authored. |
| A change to the skeleton is a design change, whoever makes it | **unchanged, and given a new author.** Repeat Layout deletes existing target routing and, with one checkbox, updates locked target items. A replicated channel is a skeleton edit. |
| Verify the skeleton against the canonical generator, not its neighbour | **unchanged.** A design block is exactly the "generator sitting beside the board" hazard in library form: it is a copy of geometry with no link back to the authority that justified it, and `Update Design Block from Selection` *"replaces"* the library contents from a board. |
| "Locked" is not a preservation contract | **corroborated on a second tool.** Verified here that KiCad's own placement enforces `locked` — and that one toolbar checkbox and one dialog option each turn that enforcement off. |
| Verify authored copper's geometry *and* layer after the run | **necessary, and now for a second reason.** Repeat Layout's silent partial copies (out-of-area, layer-not-in-both, unmappable net) are exactly the class of defect a post-hoc geometry-and-layer check catches and a completion message does not. |
| Grade the shape of the route, not only its DRC | **untouched.** Neither feature grades anything. `Copied to N Rule Areas.` is a count of edits. |
| Fixed pass budget is not convergence; scope every A/B | **untouched.** No search, no seed, nothing to converge. |

Nothing in `ROUTING.md` is superseded. The honest correction to make in `ROUTING.md` is narrower
than "part of this file is reinvented": for a board with *repeated channels*, an agent should stop
before hand-rolling a copier, because the tool ships one — and should then treat its output the way
this file treats any other transformed board.

## What was not exercised

Named so a later session does not read them as settled:

- `Apply Design Block Layout` on schematic-linked, grouped footprints (§14.2.2/§14.3).
- Repeat Layout end to end. Everything above about its behaviour is manual text plus source at tag
  10.0.5, not observed runs. Its refusal, its silent partial copies and the
  `matchBySymbolInstancePath` fallback are all **documented-not-verified**.
- What `Update PCB from Schematic` does to footprints and nets introduced by a *directly placed*
  layout block, which are unlinked to any symbol.
- Whether a block's snapshotted `(setup …)` — board design settings from the source board —
  influences the target board on placement.
- Everything here is one session on one machine, KiCad 10.0.5 macOS, not replicated.
