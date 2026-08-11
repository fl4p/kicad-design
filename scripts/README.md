# `kicad-design/scripts` — proposed

**Status: proposal, not yet referenced from `SKILL.md`.** Extracted 2026-08-11
from the `pwr-metering/hw/shunt-adc` generators after that project hit several
of the failures `SKILL.md` describes in prose.

## Why these four and not others

`SKILL.md` already *documents* every trap below. What it cannot do in prose is
stop the next project from re-implementing the workaround and getting it wrong
in a new way. These are exactly the pieces that are **project-agnostic** and
whose failure mode is a **silent false PASS**:

| module | closes |
|---|---|
| `kicad_netlist.py` | the KiCad 9→10 netlist format break; a parser that returns `{}` and reads as "nothing looks wrong" |
| `kicad_symlib.py` | `extends` (53.8 % of the stock library), unit-0 pins, rotate-then-mirror pin transforms |
| `kicad_verify.py` | `--exit-code-violations`; the sparse/absent `rule_severities` map read as "nothing ignored" |
| `kicad_repro.py` | "reproducible" claimed by a generator that never ran; **and** a concurrent writer replacing the artifact you verified |

Each raises rather than returning an empty container **on the paths listed
above**, and every error message says *which* condition failed — because "the
generator did not run" and "the generator is non-deterministic" need opposite
fixes. Two lookups deliberately still return `None` for "absent"
(`Netlist.net_of`, `Netlist.field`); see *Known gaps*.

## Deliberately **not** included

Board-specific physics stays with the board. From `shunt-adc` that means the
rail budget, PGA input window, matched-input-network, isolation-domain,
decoupling-loop, guard-copper and conversion-clock guards. They are excellent
guards and they encode one board's requirements; hoisting them here would
invite a future project to run them and believe the result.

## Verified against real inputs

Not self-tests over fixtures — measured against a live project and the stock
libraries, on KiCad 10.0.5 / macOS:

```
$ python3 kicad_netlist.py …/shunt-adc/netlist.net
<Netlist netlist.net: 52 nets, 72 components>

$ python3 kicad_symlib.py
stock libraries: 22784 symbols, 12249 extends (53.8 %), 223 files
calibration cells for transform_pin: 12 (x2 pins = 24 checks)

$ python3 kicad_symlib.py …/Amplifier_Operational.kicad_sym LM358
LM358  parent=LM2904  units=[1, 2, 3]     # extends resolved; unit 3 = V-/V+

$ python3 kicad_verify.py …/shunt-adc.kicad_pro
severity map: VERIFIED (ERC 46 entries, DRC 62 entries)
  ERC ignored: footprint_filter, four_way_junction, simulation_model_issue
  DRC ignored: footprint_filters_mismatch, footprint_type_mismatch, …
ERC rc=0  {'erc messages': 0, 'errors': 0, 'warnings': 0}
  ERC actually skipped 3 check(s): …
DRC rc=0  {'drc violations': 0, 'unconnected pads': 0, 'footprint errors': 0}
  DRC actually skipped 5 check(s): …
```

The renamed-net, pin-less-symbol, extends-cycle, digest-drift, empty-outputs
and `min_components<1` guards were each fired on a known-bad input before being
accepted.

**`transform_pin` is NOT calibrated.** `calibration_plan()` *enumerates* the 12
(angle, mirror) cells; nothing here compares any cell against KiCad ground
truth, and this module cannot do so — only an exported netlist can. Treat the
transform as unverified until you have run that comparison in your project.
Angles that are not multiples of 90 are now rejected rather than silently
producing a non-rotation.

## Review history

An adversarial review (Codex, 2026-08-11) found the first draft had
**release-blocking false passes of exactly the class these modules exist to
prevent**. Reproduced and fixed before this version:

- `'Raspberry_Pi_2_3' in lib` returned **False** for a real stock symbol —
  top-level entries were classified by name suffix, and that name ends `_2_3`.
  Now classified by parse depth.
- `pins('4001', 1)` returned **6 pins, numbers 1/2/3 twice** — body style was
  parsed and then ignored, merging the DeMorgan variant. Now `style` is a
  parameter, and duplicate pin numbers with conflicting geometry raise.
- `transform_pin(2, 3, 45)` returned `(-1, -5)`, changing the length from 3.606
  to 5.099 — `round(cos)`/`round(sin)` are only valid on the 90° grid.
- `units()` could return `[]`, making `for u in units(n)` perform zero checks.
- `min_components=0` disabled the only empty-export guard.
- Duplicate net names and duplicate report labels silently kept the last value,
  which can turn a real violation count into 0.
- `run_and_check_reproducible(cmd, [])` ran the command twice and returned `{}`
  as success; the mtime test also passed when only *one* of the two runs
  rewrote the file.
- Digests defaulted to MD5; now SHA-256.

### Round two — the first round of fixes had overshot

A second adversarial pass on the *fixes* found that two of them had swung into
the opposite error, which is just as wrong and much easier to miss:

- **The `pins()` fix made the reader refuse 51 of 22,784 real stock symbols.**
  Deduplicating by pin *number* was the wrong model: **stacked pins are legal**
  and stock `74xx_IEEE:74278` carries pin 6 at several positions inside one
  unit. Now deduplicated on the whole record, never the number; the 4001
  double-count it was written for is handled by the `style` filter instead.
  Sweep result: **0 of 22,784 raise.**
- **Raising for pin-less symbols was also wrong.** 40 stock symbols
  (`MountingScrew`, `Logo_Open_Hardware_*`, `Generic_Outline`) legitimately
  have no pins. `pins()` now returns `[]` for those and `require_pins()` is the
  asserting variant, so "no pins by design" and "I failed to read the pins"
  stop being the same answer.
- **The both-runs mtime rule could false-fail a correct generator** on a
  coarse-timestamp filesystem. Runs are now separated in time so two genuine
  rewrites cannot share a timestamp.

Also fixed in round two: duplicate *ERC* summaries were still undetected
(`search` → `finditer`, conflicting summaries raise); a report containing only
`** Found 0 bananas **` passed as a clean DRC (required labels now enforced per
report kind); `min_components=float("nan")` slipped past both comparisons
(non-int floors rejected); a pin on two nets parsed silently; and the CLI
printed nothing for unit-0-only symbols like `DRV2510-Q1`.

### Round three — the documented gaps, closed

Everything the second review left open has since been fixed and re-verified:

- **Root-level validation.** Both readers now require the document to be one
  balanced expression with the expected head (`kicad_symbol_lib` / `export`)
  and nothing outside it. `(junk (symbol "FAKE" ...)) trailing garbage` is
  rejected instead of yielding `FAKE`.
- **TOCTOU.** `digest()` hashes through one descriptor, `fstat`s it before and
  after, and compares the descriptor's identity with what the pathname
  resolves to afterwards. A file replaced mid-read or swapped between open and
  check now raises instead of returning a digest that describes neither file.
- **Report freshness.** `run_erc`/`run_drc` require the tool to have rewritten
  the report during that call; a stale zero-count report is refused.
- **`find_kicad_cli` verifies identity**, running `--version` and requiring a
  version string. It no longer accepts an arbitrary file.
- **`severity_report` validates values**, returning `unverified` when a map
  contains anything that is not a KiCad severity.
- **Node refs are cross-checked** against the component list, and a pin on two
  nets is rejected.

## Remaining limitation

One, and it is a property of the approach rather than a bug:

**`transform_pin` is not calibrated against KiCad.** The rejection of non-90°
angles and the 12-cell enumeration are in place, but nothing here compares a
cell's output with what KiCad actually nets up, and no unit test can — the only
ground truth is an exported netlist. Run `calibration_plan()` in your own
project before trusting the transform.

## One improvement on what `SKILL.md` currently advises

`SKILL.md` says to enumerate `erc.rule_severities` from `.kicad_pro`, and warns
that an absent map makes that enumeration return `[]` — "no rules ignored" at
the moment you know least.

There is a **better source**: the ERC and DRC reports each carry an
`Ignored checks` section listing what KiCad *actually skipped on that run*,
including built-in defaults the map never mentions. `ignored_checks_from_report()`
reads it, and returns `None` — not `[]` — when the section is absent, so
"unanswered" stays distinguishable from "answered none".

On the project above the two sources agree exactly (ERC 3/3, DRC 5/5), which is
the cross-check worth having: the map says what was *configured*, the report
says what was *applied*.

Note the format differs between the two tools — ERC writes `** Ignored checks:`
and DRC writes `** Ignored checks **`. Requiring the colon made every DRC report
read as "section absent".

## Suggested `SKILL.md` wiring, if adopted

- *The verification ladder* → point at `kicad_verify.py`, and add the
  report-derived ignore list as the primary source with the `.kicad_pro` map as
  the cross-check.
- *Derive geometry from the library, never from arithmetic* → point at
  `kicad_symlib.py`; keep the inline snippet, since reading it is the point.
- *Generator hygiene* → point at `kicad_repro.py`, and add the concurrent-writer
  case, which is not currently in `SKILL.md` at all.

## The gap that prompted the last module

A full ERC/DRC/audit pass was run against board digest `b285f321…`. By commit
time the file on disk was `d4e5fc2a…`, and the commit message asserted numbers
that were no longer true of the tree. **Both digests verified clean**, so
nothing looked wrong from any single check.

The cause was not non-determinism — it was a diagnostic probe in a parallel
session that pointed its write at the tracked artifact:

```python
b = pcbnew.LoadBoard('shunt-adc.kicad_pcb')
b.Save('shunt-adc.kicad_pcb')          # should have been a scratch path
```

`Save()` bypasses that project's closing `canonical_order(canonical_uuids(...))`
pass, so every item got a fresh random UUID in KiCad's own ordering and the file
stopped being a deterministic function of its inputs. The tell was **diff size
against change size**: the real edit touched 15 lines, and the committed board
diff was 23,770. That ratio is worth checking by reflex — it is cheap, and no
digest comparison can surface it.

`stable_digest()` refuses to hash a file that is still moving, and
`verify_unchanged_since()` is meant to be called immediately before commit, so
that "I verified this" and "I shipped this" are the same bytes -- with the
TOCTOU caveat in *Known gaps*: it narrows the window, it does not close it.
