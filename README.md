# kicad-design

A Claude Code skill for KiCad schematic capture, PCB layout, and datasheet-grounded
review of analog / mixed-signal designs.

Every rule here exists because the failure it describes actually shipped and had to
be caught — not because it is general advice. Each one names the failure it prevents,
and keeps the concrete numbers where they make the rule easier to apply.

Two files, so schematic-only work does not have to load the board material:

| File | Read it when |
|---|---|
| `SKILL.md` | always — shared practice plus schematic capture |
| `PCB.md` | the task touches the board: `pcbnew`, DRC, footprints, zones, stackup, creepage |

Topics covered:

- **Generate, never hand-place** — the schematic and board as generator output, and the
  hygiene rules that keeps honest (byte-reproducibility, stable-identity UUIDs, netlist
  export after structural edits, never writing a file another generator owns).
- **Verification ladder** — what ERC catches, what DRC catches, and the large class of
  defects that neither does.
- **File-format gotchas** for `.kicad_sch` / `.kicad_pcb` / `.kicad_sym` / `.kicad_mod`.
- **`pcbnew` scripting** notes, including zone fills as a cache.
- **Copper, mask and paste are three independent layers** — narrowing a pad for creepage
  does not move its solder-mask or paste aperture.
- **Datasheet discipline** — read the land-pattern page, don't recall it.
- **Guards** — the anti-monotone false-PASS failure mode, and how to calibrate a check
  against the case that actually matters.
- **Reviewing someone else's numbers.**

## Install

Symlink it into the skills directory:

```sh
ln -s ~/dev/ee/kicad-design ~/.claude/skills/kicad-design
```
