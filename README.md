# kicad-design

A Claude Code skill for KiCad schematic capture, PCB layout, and datasheet-grounded
review of analog / mixed-signal designs.

Every rule here exists because the failure it describes actually shipped and had to
be caught — not because it is general advice. Each one names the failure it prevents,
and keeps the concrete numbers where they make the rule easier to apply.

Focused companion files keep schematic-only work from loading board, footprint, release or
guard detail it does not need:

| File | Read it when |
|---|---|
| `SKILL.md` | always — shared practice plus schematic capture |
| `SETUP.md` | before **any** task that will read a datasheet, schematic-only included — can this machine actually fetch and validate them? Run it at the start, not when you hit the wall. |
| `GUARDS.md` | writing or reviewing generator checks, audits, validators or calibration harnesses |
| `PCB.md` | the task touches board layout, zones, stackup, creepage or autorouting |
| `PCBNEW.md` | scripting `pcbnew`, reproducibility or generator performance |
| `FOOTPRINTS.md` | selecting, generating or modifying footprints and land patterns |
| `RELEASE.md` | DRC severity, fab output or release readiness |
| `THERMALS.md` | dissipation, junction/ambient limits, heat paths, gradients, thermal pads/vias or validation |

Topics covered:

- **Preserve source authority** — generated designs remain generator-owned and explicitly
  hand-maintained boards remain board-owned, with hygiene for reproducibility and shared files.
- **Verification ladder** — what ERC catches, what DRC catches, and the large class of
  defects that neither does.
- **File-format gotchas** for `.kicad_sch` / `.kicad_pcb` / `.kicad_sym` / `.kicad_mod`.
- **`pcbnew` scripting** notes, including zone fills as a cache.
- **Copper, mask and paste are three independent layers** — narrowing a pad for creepage
  does not move its solder-mask or paste aperture.
- **Datasheet discipline** — read the land-pattern page, don't recall it.
- **Guards** — explicit ledger/model/artifact tiers, subject-specific bad and legal
  calibrations, semantic zone-fill finalization and matched-copper checks.
- **Thermals** — opt-in heat, temperature, power-stress and thermally load-bearing copper workflow.
- **Reviewing someone else's numbers.**

## Install

Symlink it into the skills directory:

```sh
ln -s ~/dev/ee/kicad-design ~/.claude/skills/kicad-design
```
