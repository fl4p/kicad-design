# kicad-design

A reusable agent skill for KiCad schematic capture, PCB layout, and datasheet-grounded
review of analog and mixed-signal designs.

`SKILL.md` contains the invariant workflow and required actions. Named devices and measured
failures are labeled examples; detailed procedures and scoped case evidence live in companions so
unrelated tasks load only the domain they need.

Focused companion files keep schematic-only work from loading board, footprint, release or
guard detail it does not need:

| File | Read it when |
|---|---|
| `SKILL.md` | always — shared workflow, source authority and verification ladder |
| `SETUP.md` | before **any** task that will read a datasheet, schematic-only included — can this machine actually fetch and validate them? Run it at the start, not when you hit the wall. |
| `SCHEMATIC.md` | capturing, generating, editing, reviewing or declaring completion of a schematic, and before PCB implementation |
| `GUARDS.md` | writing or reviewing generator checks, audits, validators or calibration harnesses |
| `PCB.md` | the task touches board layout, zones, stackup, creepage or routing ownership |
| `AUTOROUTING.md` | any routing pass is planned — default pre-pass scout; promotion (Freerouting/KRT, route manifests) needs the project opt-in |
| `PCBNEW.md` | scripting `pcbnew`, reproducibility or generator performance |
| `FOOTPRINTS.md` | selecting, generating or modifying footprints and land patterns |
| `RELEASE.md` | DRC severity, fab output or release readiness |
| `THERMALS.md` | dissipation, junction/ambient limits, heat paths, gradients, thermal pads/vias or validation |
| `VARIANTS.md` | one generator must emit more than one board |
| `MODELS.md` | choosing or delegating to an AI model/agent for schematic generation, review, or KiCad automation |

Datasheet and sourcing work depends on the
[`online-research`](https://github.com/fl4p/online-research-skill) skill. Install it beside this skill
as `<skills-root>/online-research`; without that canonical access contract, external retrieval must
stop rather than fall back to an abbreviated WAF procedure.

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
- **Board variants** — one generator, two boards, without moving the qualified one.
- **Reviewing someone else's numbers.**

## Install

Symlink a checkout into the skills directory for the runtime that should discover it:

```sh
kicad_skill_repo=/absolute/path/to/kicad-design

# Codex
codex_skill_root=${CODEX_HOME:-$HOME/.codex}/skills
mkdir -p "$codex_skill_root"
ln -s "$kicad_skill_repo" "$codex_skill_root/kicad-design"

# Claude Code, when that is the target runtime
claude_skill_root=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills
mkdir -p "$claude_skill_root"
ln -s "$kicad_skill_repo" "$claude_skill_root/kicad-design"
```

Install only the links for the runtimes in use. For another agent runtime, use its documented
skills directory rather than assuming either layout.
