# Review brief: the `kicad-design` skill

Hand this file to a reviewing agent. It is written for the **third** pass; the
first two are on record and their frontier is recorded below, so the reviewer
starts where they stopped rather than re-finding what is already fixed.

---

## What this repo is, and why a wrong sentence is expensive

`kicad-design` is a Claude Code skill: prose instructions an agent follows to
create and review KiCad schematics and PCBs. It has no test suite and nothing
executes it. **A wrong fact in it becomes a wrong board**, and it will do so
silently, because the agent reading it will not doubt it.

Files: `SKILL.md` (~750 lines, schematic + shared), `PCB.md` (board side),
`SETUP.md` (datasheet-access preflight), `README.md` (index).

## Environment — verify, do not reason

KiCad **10.0.5** is installed. It was **9.0.4** until 2026-08-09, and that
upgrade silently changed observable behaviour that this skill had recorded as
settled — one `pcbnew` call **reversed** (`PCB.md`, trap 1) and the netlist
export format changed shape (`SKILL.md`, "The netlist export format is not
stable across major versions"). Neither announced itself; both surfaced as
downstream code that suddenly matched nothing.

Treat every version-stamped claim in these files as **provisional**, including
this line: check `kicad-cli --version` and `pcbnew.GetBuildVersion()` at the
start of a session rather than trusting the number written here. Run things
rather than recalling them:

```
python   /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
cli      /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
symbols  /Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols
```

Real `.kicad_pro` / `.kicad_pcb` / `.kicad_sch` files to test against live under
`~/dev/pv/pwr-metering/hw/` and elsewhere on this machine.

**Label every finding `VERIFIED` (you ran it or read the source) or `SUSPECT`
(reasoned doubt).** Do not present suspicion as verification. A `SUSPECT` finding
that says so is useful; one dressed as verified is worse than silence.

---

## Priority 1 — audit the previous rounds' fixes

**This is the highest-yield task, and it is not optional.** Read
`git log -3` and `git show` each of the last commits before reading anything
else.

The history so far, which tells you what to expect:

| pass | outcome |
|---|---|
| review 1 → `612b4cf` | 36 findings, applied |
| review 2 | found that **`612b4cf` introduced new bugs**, two of which made the file worse than before it |
| fixes → `9330f63` | applied |
| **you** | audit `9330f63` the same way |

A fix applied from a review is **unreviewed code**. `612b4cf` shipped a `pn()`
rewrite that `KeyError`s on 191 stock symbols, a code sample that `NameError`s on
every run, a new guard with no non-degeneracy check, and a comment naming the
wrong symbol — all while correctly fixing nine other things. Assume `9330f63`
has the same character.

Specific things to re-derive rather than trust:

- The unit-0 union claim: 664 `NAME_0_*` sub-symbols with pins, 191 symbols with
  *all* pins in unit 0. Is the prescribed union rule actually correct, including
  body style 0 vs 1/2?
- `GetEnabledLayers().CuStack()` — does it return what the snippet assumes, in
  stack order, on a 2- and a 6-layer board? Does the rewritten via-in-pad snippet
  now run? (`bad` initialised, `pcbnew.FromMM` correct, pair count honest?)
- The glyph-bbox numbers: Earth_Protective 5.080, +VDC 4.318, Earth_Clean 3.810,
  −VDC 3.175, GNDPWR 2.032 and asymmetric at −1.270..+1.016. Recompute from
  `power.kicad_sym`.
- `Conn_02x01` exists; the five suffixed variants; "positions per row".
- The IPC column reassignment in `PCB.md` — is the 0.430 mm figure now ruled
  against the right column, and is the 0.675 mm figure still ruled against B?
- Whether any **other** passage still cites a number that a fix changed. This is
  how the "97 % of a 1206" anecdote was orphaned when the resistor was corrected
  to 103 %: the fix was right and it silently broke a paragraph 500 lines away.
- Whether every code sample still parses **and is correctly indented** — a
  previous fix pass broke two blocks by losing indentation during a replace.
- Whether a rule the skill states is actually applied to the skill's **own**
  examples. The `-O`/bare-assert rule was added in one commit and contradicted by
  four samples in the same two files.

---

## Priority 2 — the false-PASS audit

The repo owner's documented failure mode, and the reason this skill exists:

> **the anti-monotone false PASS** — a check that, when it *cannot evaluate its
> input*, returns the value meaning "fine" instead of "unverified". It disappears
> precisely when the situation is worst, and does so silently.

For every check, validator, assertion or cache the skill **describes or
contains**, answer in writing:

1. **What does it return when it cannot evaluate its input?** Missing data, too
   few samples, an unparseable file, a probe that failed, an exception. If any of
   those paths yield "OK", it is broken. Absence of evidence must never encode
   absence of the problem.
2. **Is it monotone?** As input gets worse, does the verdict move monotonically
   toward failure, with no region flipping back to PASS? Test the far tail, not
   the near miss.
3. **Is its own precondition checked?** A gate keyed on a flag no caller passes is
   dead. A cache gated on a file existing proves the file exists, not that it
   works.
4. **Is the signature the source of truth, or a PROXY?** Caches and fingerprints
   must cover the code that *derives* the value, not just the data it reads.
5. **Can a failed check persist its own false verdict?** Tri-state and round-trip
   the unknown.
6. **Does the provenance claim more than was done?** A cached or fallback result
   must say so.
7. **Is it calibrated against a known-bad input?** A guard never seen to fire is
   not a guard.

**And the inverse, which round 2 found and you should hunt for more of:** a check
that can *never* fail. "Diff `rule_severities` against defaults" was recommended
in two files as the guard against silently-ignored rules — but every rule both
files cite as the problem *is* a stock default, so the diff reports nothing on
every example given. Dead weight dressed as safety is the same defect wearing the
opposite mask.

---

## Priority 3 — facts

Any numeric claim, datasheet citation, standards reference, KiCad API behaviour,
library geometry or vendor capability that is wrong, stale, or stated without a
checkable source. Particular rot:

- **KiCad API/CLI claims** — these break between versions. Run them.
- **Library geometry** — pin offsets, symbol counts, variant names. Parse the
  library.
- **Standards numbers** — every spacing figure must carry `standard + revision +
  table + column + voltage band + the voltage actually used`. IPC-2221**C**
  (Dec 2023) supersedes B, and the file flags its values as *not* re-verified
  against C. If you can read C, that is high value.
- **Vendor capability figures** (fab minimums, part availability) — these move
  yearly and should carry a date and a source.
- **Any "rule of thumb" presented as a hard limit.** The skill already contains
  one such fossil that was wrong (`the smallest 100 nF/250 V X7R is a 1206`).
  Look for siblings.

---

## Priority 4 — structure

~750 lines of dense prose, no table of contents. Round 2 flagged an **ordering
trap**: the file-format table hands the agent hardcoded pin arithmetic for
`Device:R` and `Conn_01xNN` *before* the section that says never to hardcode pin
arithmetic. An agent reading top-to-bottom does the wrong thing and never learns
otherwise. Look for more of these: advice that is correct but arrives too late,
or is buried where a skimming agent will miss it.

---

## Known outstanding — do not re-report these as new

Round 2 raised these and they are **not yet fixed**. Confirm, refine or refute
them; do not spend the pass rediscovering them:

- `SKILL.md` — the 5 % / 3.92 % / 1 % / 6.35 % gain figures cannot be re-derived
  from a two-resistor divider; other terms must dominate and are unnamed.
- `PCB.md` — "a 1210's terminations are ~1.5 mm apart against an 0805's 0.9 mm"
  is unsourced and reconciles only at one end of the dimensional envelope.
- `SKILL.md` — JLCPCB 6 mil / PCBWay 0.15 mm silkscreen minimums carry no date or
  URL.
- `check_rail_orientation` — still assumes a vertical glyph axis; a 90°-rotated
  rail over its own horizontal wire passes silently. `matched` is computed but
  never affects the verdict.
- `SETUP.md` check 4 — proves `channel="chrome"` headless, while §3a needs
  `launch_persistent_context(..., headless=False)`. It passes in exactly the
  situation §3a cannot run.
- `SETUP.md` — the MPN grep treats the part number as a regex (`grep -ciF`), and
  `pdftotext` hyphenation can fail a genuine datasheet.
- `SETUP.md` — the `pdfinfo` exit-status gate may reject the encrypted-stream
  PDFs the file elsewhere defends; needs calibrating against one.
- `SKILL.md` — "43 ERC rules on 9.0.4" is unreproducible; counts of 43/44/45/48
  were measured depending on the writing version.
- The mtime half of the reproducibility rule does not specify `st_mtime_ns`; a
  sub-second regeneration on a coarse filesystem re-creates the false PASS.

---

## Rules of engagement

- **Make no edits.** Review only. Run no git command that changes state.
- Cite `file:line` for every finding.
- For each: what it says, what is wrong, and the **concrete correction** you would
  make — not "consider revising".
- Rank by *likelihood of producing a wrong board*, not by how interesting the
  finding is.
- If you find nothing in a category, say so plainly. An honest "I checked X and it
  is correct" is worth more than a padded list, and the *Verified correct* sections
  of the previous two reviews are actively useful — they stop the next pass
  re-litigating settled facts. Include one.
