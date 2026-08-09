---
name: kicad-design
description: Create or modify KiCad schematics, symbols, footprints and PCB layouts, and review electronic designs against datasheets. Use whenever the task involves KiCad, .kicad_sch/.kicad_pcb/.kicad_sym/.kicad_mod files, schematic capture, PCB layout, ERC/DRC, footprint or land-pattern selection, noise budgets, or checking an analog/mixed-signal design against part datasheets — from any repo. Board-side material (pcbnew, DRC, footprints, stackup, creepage, surface leakage, fab output and release readiness) is in the companion file PCB.md, read on demand so schematic-only work does not pay for it; SETUP.md is the preflight for datasheet access — distributor API keys, vendor WAFs, PDF validation.
---

# KiCad schematic and PCB design

Every rule below exists because the failure it describes actually shipped and had to be
caught. Most were found on precision analog / high-voltage boards, which is where KiCad's
own checks are thinnest — but nothing here is specific to one design.

## Before you start: run the preflight in `SETUP.md`

Every rule in *Datasheet discipline* below assumes you can actually **get** the
datasheet. Confirm that first — local cache, which vendor sites this machine can
reach, which distributor API keys exist, and whether a real browser is available for
bot-walled vendors. [`SETUP.md`](SETUP.md) has the checks, how to ask the user for
missing keys, the rate-limit traps that masquerade as auth failures, and a verified
recipe for fetching PDFs from vendors that refuse curl.

Do it at the start, not when you hit a wall. An agent that discovers mid-task it
cannot read a datasheet tends to substitute a part and explain the substitution as
engineering.

## Working on the board? Read `PCB.md`

This file covers what is shared plus schematic capture. **PCB layout, footprints,
land patterns, `pcbnew` scripting, zones, DRC, stackup, creepage, surface leakage
and fab output live in [`PCB.md`](PCB.md)** — read that file as well when the task
touches the board, and skip it entirely for schematic-only work.

**"Is this ready to fab / ready to order?" is a board question**: go straight to
`PCB.md`'s last section, which separates *manufacturable* from *final* and gives
the export-and-measure checklist. Answering it from DRC alone gets it wrong in
both directions.

## Core principle: generate, never hand-place

Write a Python **generator** that emits the `.kicad_sch` (and a `pcbnew` script for the
`.kicad_pcb`). Then the design is diffable, reviewable, reproducible, and a fix applies
everywhere at once. Hand-editing a generated file is a bug waiting to happen — put a note in
the docs saying the artefact is generated and the generator is the source of truth.

**Verify reproducibility**: `md5` the output, re-run the generator, `md5` again. Equal or the
generator has hidden state — **but only if the generator actually ran**. Assert its exit
status is 0 *and* that the output's mtime moved, in the same breath as comparing the hashes: a
re-run under an interpreter that cannot import `pcbnew` leaves the file untouched, so the two
md5s match and the check reports PASS having tested nothing. See *Generator hygiene* below.

Warn the user that GUI edits will be overwritten on the next run, and check for a running
Eeschema/pcbnew holding a stale copy before regenerating.

Generator hygiene, each learned the hard way:

- **Never write a file another generator owns.** The schematic generator rewrote
  `<project>.kicad_pro` wholesale every run, deleting 286 of its 295 lines — the board design
  settings, net classes and custom-rules linkage. The PCB script carefully protected the
  *schematic's* keys from itself; the protection was one-directional. DRC then ran on KiCad
  defaults and went green on a board that was not compliant. Seed shared files only if absent.

  **When you must ADD a key to a shared file, merge — never rewrite.** Seeding-if-absent is
  right for a fresh checkout and does nothing for the file that already exists, so a setting
  introduced later never reaches any current project. Load the JSON, `setdefault` the one key,
  and write it back **preserving insertion order** (no `sort_keys`, or the diff is the whole
  file and the next reviewer cannot see what changed). Report what moved:

  ```python
  pro = json.load(open(path))
  changed = []
  sev = pro.setdefault("erc", {}).setdefault("rule_severities", {})
  for rule, level in WANT.items():
      if sev.get(rule) != level:
          changed.append(f"{rule}: {sev.get(rule, '<KiCad default>')} -> {level}")
          sev[rule] = level
  if changed:
      json.dump(pro, open(path, "w"), indent=2)     # NOT sort_keys
  ```

  Verify it was surgical rather than assuming: on one project this landed as **+12 lines**
  with all 13 top-level keys and the board's 62 DRC severity entries untouched. Check that,
  and the failure this bullet is about cannot recur through the back door.
- **Derive UUIDs from stable identity**, never from a counter. Counter-derived UUIDs meant
  inserting one resistor changed 78 of 81 symbol UUIDs, and KiCad matches footprints to
  symbols by that path — so a one-part edit re-orphans the whole board. Hash the reference
  designator / net name / coordinates instead. On the board side this **cannot** be done
  through the API: `pcbnew` gives every item it creates a random UUID and exposes `m_Uuid`
  **read-only** (there is no `SetUuid`), so it takes a post-save rewrite of the `.kicad_pcb`.
  [`PCB.md`](PCB.md) covers that and the second, less obvious cause of a wobbling md5.
- **Verify the generator actually ran before believing a reproducibility check.** (Stated in
  *Core principle* above because an agent that skims the principle box implements the broken
  version.) Cost: a confident "reproducibility verified" on a board whose generator had not
  executed once. Note this applies to **your own script's** exit status; `kicad-cli`'s means
  almost nothing unless you pass `--exit-code-violations` — see *The verification ladder*.
- **Do not put a load-bearing check behind a bare `assert`.** `python -O` / `PYTHONOPTIMIZE=1`
  deletes every `assert` statement in the file, silently and with no message — so a generator
  invoked from a Makefile or CI wrapper that happens to set `-O` emits the same artefact with
  *zero* checking. Anything whose absence is a false PASS must be `if not cond: raise ...`.
  Keep `assert` for genuine can't-happen invariants only, and put
  `if not __debug__: sys.exit("refusing to run under -O: the guards are gone")` at the top of
  any generator that has guards worth having.

  **A guard suite's own calibrations cannot detect this**, which is why it survives review in
  well-guarded projects. Calibrations inject a *non-empty* fault and watch the check fire; `-O`
  removes the check for *every* input, and the empty-input branch was never exercised anyway.
  Measured on a project with fifteen calibrated checks: `python3 -O audit_pcb.py` printed all
  15 calibrations `FIRED`, all 9 checks `PASS`, and exited **0**, having evaluated nothing.
  The more thorough the calibration story, the more convincing that output is.
- **Export the netlist after every structural edit and read it.** Two separate reroutes
  silently merged nets (SCLK+SDI+~CS, then VREF10+GND) because stub endpoints share a column.
  ERC reported *a* problem but not which nets had merged; only the netlist showed that.
- **Beware substring replaces hitting `def` lines.** `s.replace("check_foo()", …)` also matches
  inside `def check_foo():`. Anchor on the full line, or verify the file still parses.


## Ask before you assume: the choices that are the user's, not yours

Some parameters look like engineering defaults but are actually **procurement and
budget decisions the user owns**. Picking one silently and then writing three
pages of rationale for it makes it expensive to change later. Ask up front, in
one message, before any placement:

- **Layer count.** 4 layers is the reflexive answer for a mixed-signal board.
  Ask instead of assuming, and if you have a preference, give the *number* that
  supports it. Beware of writing the rationale after the choice: a stack defended
  by several plausible arguments with no quantity attached to any of them is a
  default wearing a justification. Note that inner planes are not automatically
  better for sensitive nodes — a plane 0.2 mm below a high-impedance node loads it
  ~8x harder than one 1.6 mm below it on a 2-layer board.
- **Board outline and mounting** — enclosure-driven.
- **Assembly process** — hand-solder vs reflow decides whether a QFN or a
  PowerPAD is acceptable at all.
- **Conformal coating** — it changes which IPC-2221 column applies, so it decides
  HV geometry, not just finish. Do **not** carry "0.8 mm uncoated / 0.4 mm coated"
  around as a constant. IPC-2221 Table 6-1 is banded (…101–150, 151–170, 171–250,
  251–300, 301–500 V…), the columns differ from each other within a band, and the
  values move by an order of magnitude across the table. Quote the **row you
  actually used**. Every spacing number you
  write down must carry `standard + revision + table + column + voltage band +
  the voltage actually used`, or it is not checkable and will be misapplied at a
  different voltage. Note the current revision is **IPC-2221C** (Dec 2023); the
  figures quoted here and in `PCB.md` are the B-era ones and have **not** been
  re-verified against C's Table 6-1 — read it before leaning on a marginal number,
  and see `PCB.md` for which column (A5–A7 assembly vs B1–B4 bare-board) applies.
- **Connector types and pinout** — usually fixed by what plugs into it.

**If the user forbids questions** ("don't ask me anything", or an autonomous run), you still
owe them the decision — you just cannot collect it. Make each call yourself, state it in the
brief you hand any downstream agent, and record it in the design doc as a *decision with its
rationale*, not as an emergent property of the layout. A choice made silently and a choice made
explicitly cost the same to make and wildly different amounts to revisit.

Converting a finished board between layer counts is very doable when a generator
is the source of truth — expect a handful of DRC violations, not a redesign — but
every inner-plane *decision* has to be re-derived, and the design document's
rationale sections have to be rewritten rather than patched. Cheaper to ask.

Related, when a stack changes: **every layer literal is now a liability.** A
hardcoded `CU = (F_Cu, In1_Cu, In2_Cu, B_Cu)` in an audit keeps "checking" layers
that no longer exist. Derive the layer set from `board.GetCopperLayerCount()` and
assert it equals what the audit was written for.


## Close every external interface before calling the schematic complete

Write an electrical contract for every connector pin: signal direction, normal and fault
voltage/current, power domain, reference/return, whether the signal is raw or already scaled,
who sources power, and behaviour when either side is unpowered. Put the same truth on the
schematic, board silkscreen and integration document. A name such as `VIN` does not implement
a divider, and a `5V` pin is unsafe until its input/output direction is unambiguous.

- **Trace every claimed function to components and nets.** If the brief says "divided voltage
  input", point to the divider and protection, or label the connector explicitly as a
  pre-scaled low-voltage input with its limit. Labels and prose are not circuitry.
- **Give every floating measurement domain an intentional DC reference.** For each isolated
  analog domain, prove how its ground and the source common mode are established relative to
  the converter input range. Capacitors, input leakage and protection diodes do not count as
  a DC reference. If several sensors cannot share that reference, revisit the topology rather
  than hoping differential inputs will absorb the common-mode difference.
- **Build a power-state matrix for independently powered domains.** Check every combination of
  supplies on and off. Follow driven outputs into unpowered receivers, clamp diodes and exposed
  power pins; compute or bound injection current, back-powering and rail contention. Apply an
  isolator's default-state table to the voltage actually present at VCCI/VCCO — "host off" is
  not the same as "isolator side unpowered" when a separate brick still feeds it.
- **A part that auto-detects a resource and silently falls back has moved a guard off the
  board and onto the host — say so, in the interface contract.** The ADS1262 takes an external
  clock, and SBAS661C §9.4.8 is explicit: *"If no external clock is detected, the ADC
  automatically selects the internal oscillator."* No error, no flag on any pin. A cut clock
  wire, an unfitted oscillator or an unplugged link does not stop conversions — the part keeps
  emitting well-formed, plausible, *unsynchronised* data, and nothing in the numbers looks
  wrong. Absence of the resource encodes "resource fine", which is the anti-monotone shape,
  living inside the silicon where no schematic or netlist guard can reach it.
  The schematic can still guard what it owns (is the pin on the right net, is that net
  *driven*, does the source exist) — do all of that, it catches the build errors. But the
  runtime case has exactly one answer: find the **status bit that reports which resource is in
  use** (here `EXTCLK`, bit 5 of the STATUS byte), require the host to read it **on every
  device at the start of every acquisition**, and make a run that could not confirm it
  `unverified` rather than good. Check what *enables* that status register too — on this part
  the byte only appears if `INTERFACE` bit 2 is set, so a host that skips it gets no error and
  no evidence, which is the same failure one level up. Write all of this into the interface
  contract; a host integrator cannot infer any of it from the pinout.
- **A rail clamp is not a current sink.** For every clamp path, identify what absorbs the
  current with the receiving rail both powered and unpowered. A logic rail that normally
  consumes more than the injected current is not proof: its load may be absent, disabled or
  disconnected in the fault state. For every fault inside the accepted envelope, provide a
  guaranteed shunt/return path, bound the resulting rail rise and back-power current, and
  include the sink in the maximum-fault stress ledger. Only a fault explicitly excluded from
  that envelope may be documented as unsupported; documentation is not a substitute for an
  in-scope protection path.
- **Size protection at the maximum credible fault, not the normal signal maximum.** Include
  supply tolerance and transients, component tolerance, working-voltage limits, continuous and
  pulse power, ambient-temperature derating, and the protection part's failure mode. A nominal
  package rating with only a few percent of room is not design margin. If prose calls the fault
  110 V while the arithmetic uses 100 V, the protection check has failed even if ERC and DRC pass.
- **Do not join two possible power sources directly.** State which connector powers which rail,
  or add ORing, current limiting or isolation that makes either connection order safe.
- **A series limiter belongs at the connector, and the pull-up on the exposed side of it.**
  What is exposed is usually the *run*, not just the pin: an open-collector status output on a
  +110 V op-amp sat **0.670 mm** from the 0–100 V output land — a spacing that meets
  IPC-2221C Table 6-1 **A7** (0.4 mm, coated) but not **A6** (0.8 mm, uncoated) in the
  110 V row, i.e. it is compliant only if the board is actually coated — and its track then
  crossed the board 1.02 mm from the
  output track, ending at a Raspberry Pi GPIO with nothing in series. Put the limiter at the
  *device pin* and that whole run is downstream of it, so a bridge or coating void onto the run
  simply bypasses it. Everything **upstream** of the limiter is what gets protected, so it goes
  last, beside the connector, with the clamp.
  The pull-up then has to move to the exposed side, and this is the part that is easy to get
  backwards. Leave it at the connector and it forms a divider with the limiter, so a valid
  `V_OL` caps the limiter at a few kΩ — and the bounding fault of 110 V across 3 kΩ is 37 mA
  and **4.0 W**, a fusible rather than a resistor. Moving the pull-up upstream removes the
  divider entirely and lets the limiter be large enough (47 kΩ → 2.34 mA, **0.257 W**) that
  nothing in the path becomes a fuse. Size the pull-up against the receiver's **worst-case**
  input leakage, not its typical: 100 kΩ × 5 µA is already 0.5 V of `V_OH` droop, and it is
  what bounds how large the pair can get. Record which side each part is on and why, or the
  next tidy-up moves the pull-up back.
  Two traps in those numbers, both of which this entry fell into before being corrected.
  **Bound the fault at the rail, not at the maximum output** — the first version sized it at
  100 V because that was the output swing, while the supply is 110 V, which is the same defect
  the *maximum credible fault* bullet above describes. And 110²/47 kΩ = **0.257 W is 103 % of
  a 0.25 W 1206** — over its nameplate before any derating at all — so the part goes to a 2010.
  Quote the series and rating the percentage is against: 1206 thick-film runs 0.125–0.5 W
  depending on series, and at a 110 V fault the part's **working-voltage** rating binds
  independently of power. A protection resistor chosen at the nominal fault and the nameplate
  rating is sized twice over at the wrong number.


## The verification ladder

Each rung catches what the one below cannot. Climb all of it; stopping early is how
plausible-but-wrong artefacts ship.

```sh
K=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli   # not on PATH by default
$K sch export pdf --black-and-white -o out.pdf x.kicad_sch  # 1. does it even parse?
$K sch erc --severity-all --exit-code-violations -o erc.rpt x.kicad_sch  # 2. ERC
$K sch export netlist --format kicadsexpr -o n.net x.kicad_sch  # 3. are the NETS right?
$K pcb drc --severity-all --schematic-parity --exit-code-violations -o drc.rpt x.kicad_pcb
```

**`--exit-code-violations` is not optional, and leaving it off is the highest-leverage false
PASS available to you.** Without it, `kicad-cli sch erc` and `pcb drc` write every violation
into the report and then **exit 0**. Measured: a board carrying 175 DRC violations exits `0`
bare and `5` with the flag. So a CI step, a `set -e` script, or an agent that "asserted the
exit status is 0" passes a board it never checked — the anti-monotone false PASS this whole
document is about, sitting in its own ladder. Either pass the flag, or parse the report and
assert the violation count; never take `$?` alone as the verdict.

**`--severity-all` is not optional for ERC either, and "ERC = 0" is a statement about the
severity map as much as about the schematic.** `.kicad_pro` carries `erc.rule_severities`,
plus `erc_exclusions` and a `pin_map`, exactly parallel to the DRC map that `PCB.md` treats as
a first-class guard precondition. (This used to say "43 rules on KiCad 9.0.4". Do not quote a
count: the map is **sparse** — KiCad writes only entries it has reason to write, so the number
is a property of that file's edit history, not of KiCad. Measured across four real projects on
one machine: 0, 0, 33 and 44 entries.) A rule set to `ignore` is not
resurrected by `--severity-all`, and one real project silently carried four at `ignore`
(`footprint_filter`, `four_way_junction`, `simulation_model_issue`, `single_global_label`).
Worse, that map lives in the same `.kicad_pro` that a generator can rewrite wholesale — see
*Never write a file another generator owns* above, where doing exactly that reset DRC to
defaults. **Before believing a green ERC, list every rule sitting at `ignore` — INCLUDING KiCad's
own defaults — in the release report.** Do not lead with "diff against defaults": all four
rules above *are* the stock defaults, so a diff reports no difference and the guard that was
supposed to catch them fires never. Diffing is the secondary check, for spotting a map that
someone changed; enumerating the `ignore`s is the one that works, and do the same for `pin_map`, which decides whether
two outputs driving each other is an error at all.

**And the enumeration has its own false PASS, which a review of this file found in this very
paragraph.** Because `erc.rule_severities` is sparse, it is frequently **absent entirely** —
two of the four projects measured above have no map at all. Enumerate `ignore`s over a missing
map and you get `[]`, which reports *"no rules are ignored"* at the exact moment you know
least, while KiCad's built-in defaults — including the four named above — are fully in force.
Absence of the map encodes absence of the problem: the same anti-monotone shape this section
exists to prevent, sitting inside the remedy for it. **A missing or empty `rule_severities` is
`unverified`, not clean.** Resolve the enumeration against KiCad's built-in default map and
report the effective severity of every rule, or say the severity map could not be established
and refuse to call the ERC green. (`PCB.md`'s DRC half is not exposed to this: the same board
had 62 entries in `board.design_settings.rule_severities` — but that is luck, not structure,
so give the DRC side the same tri-state.)

1. **Parse.** A malformed file fails with a bare `Failed to load schematic` and no line number.
2. **ERC = 0.** Necessary, nowhere near sufficient — and see the severity-map caveat above.
3. **Read the netlist.** ERC cannot tell you that a feedback tap is on the wrong side of a
   resistor. Print every net with its nodes and read them against intent. This is the single
   highest-value check. **Assert the component count before reading anything else** — an
   export that instantiated nothing still exits 0 and still writes a plausible-looking file
   (827 bytes, `(nets))`, no `(comp …)` at all). "No nets look wrong" is trivially true of a
   netlist with no nets in it.

   **The netlist export format is not stable across major versions.** KiCad 9 wrote it
   compactly — `(net (code "1") (name "X")` with each `(node (ref "U1") (pin "3"))` on one
   line. KiCad 10.0.5 pretty-prints **every token onto its own line**. Any regex written
   against the 9.x shape — anything needing a literal space after `(net`, or `(node (ref …)
   (pin …))` on one line — matches **nothing** against a 10.x export. Observed live: a
   verifier that had passed all session began reporting every net "absent", and a second,
   independent parser in the same project broke the same way in the same hour.

   That failure was loud only by luck. The parser returned a near-empty dict, and it looked
   like a failure solely because the expectations table was non-empty; with an empty table it
   would have reported a clean pass over a file it had entirely failed to read. **A parser
   must assert it understood its input**: count the `(net` openers and require the parsed net
   count to equal it, and require every net to have at least one node. Then write the matcher
   whitespace-agnostically (`\(net\s`, `\s+` between tokens) so it spans both formats — verify
   that by parsing an old committed netlist *and* a fresh export.

   **Do that matching in Python `re` over the whole file, never in a line-based tool.** The
   whitespace after `(net` is now a *newline*, so `grep -cE '\(net\s'` returns **0** on a 10.x
   export while the identical pattern in `re.findall` over the file returns 51 — an agent that
   implements the opener-count in shell reproduces the exact bug this bullet is about. Count
   `\(net\s` and not a bare `(net`, which also matches the enclosing `(nets`.
4. **Render it and actually look.** Export the PDF and view the image. Overlapping text,
   symbols drawn over their own wires, and collided labels are invisible to every CLI check.
   Keep the worksheet frame out of board renders: it plots in the same colour family as copper
   and its rules run edge-to-edge — on an isolated board they look exactly like copper marching
   straight across your isolation barrier. Nearly cost a false "serious violation" finding;
   settle that class of question by asking the *file* whether anything lives on a copper layer,
   not by squinting at a render. The flag differs per subcommand, and guessing earns an
   `Unknown argument`: `sch export pdf` and `pcb export svg` take **`--exclude-drawing-sheet`**;
   `pcb export pdf` **omits the sheet by default** and takes `--include-border-title` to opt
   back in.
5. **Domain guards** for anything the tools don't model (see *Guards*, below).

**Text on a generated sheet does not reflow, and nothing checks it.** Adding one note
lands it silently on top of another; growing a component row pushes its east end into the
text column beside it. Only rung 4 sees this. Budget the extents before placing, because
the stroke font is wider than it looks — measured with KiCad's own text-extent engine
(`EDA_TEXT::GetTextBox`, the same stroke font that draws the sheet):

| quantity | multiple of the nominal text size |
|---|---|
| per-character advance, lowercase | ≈ **0.80** |
| per-character advance, **UPPERCASE** | ≈ **0.91** (p90 1.15) |
| line pitch | ≈ 1.61 |

Planning with 0.7 put five collisions on one sheet, and every one of them was on an ALL-CAPS
heading running 1.4× wider than budgeted. Use ~0.95 × size per character for anything with
capitals in it, and remember that a block of *n* lines occupies `1.61 × size × (n − 1)` plus
one line of height.

**Measure with KiCad, not with `pdftotext -bbox`.** The earlier lowercase figure here was
0.67, taken from a `pdftotext` pass over an exported sheet — about 20 % low, which is the same
class of error the rule exists to fix. KiCad's PDF export draws glyphs as vector strokes and
*additionally* emits an invisible selectable-text layer in a substituted base font;
`pdftotext` measures the substitute, not the strokes. (The discrepancy is confirmed; that
explanation of it is not.) Line pitch is unaffected — 1.610 either way.

Rungs 4 and 5 are where most real defects are caught, and both are easy to skip.
Board-side rungs — `--schematic-parity`, and why a green DRC can still hide a lost
clearance — are in [`PCB.md`](PCB.md).

Treat verification summaries as cached output. Regenerate ERC, DRC, parity and audit reports
before release, then derive or check the documented counts against those files. A design note
that says "two warnings" beside a current zero-warning report is a failed verification step,
not harmless stale prose.

After any value, net-name, topology or safety-limit change, sweep every representation of that
fact: generator comments and schematic annotations, connector labels and silkscreen, BOM and
assembly instructions, current integration/design documents, and firmware or host-side limits.
Search explicitly for the old value or name. Preserve dated reviews as point-in-time records;
mark findings resolved or superseded with a date and a reference to the current evidence rather
than rewriting the original finding. A generated artefact can be electrically current while the
instruction that tells someone what to fit remains dangerously stale; any contradiction among
the live release artefacts is a release failure, and historical records must be clearly marked
when they are no longer current.


## KiCad file-format gotchas

| Trap | Reality |
|---|---|
| **Raw newlines in quoted strings** | Break the parser. `Failed to load schematic`, no line number. Escape as `\n`. Cost: a 175-violation file that turned out to be unparseable. |
| **Symbol Y axis is inverted** | Library Y is up, schematic Y is down. Global pin pos = `(X + px, Y - py)` for angle 0. |
| `Device:R` / `Device:C` | Both connect at **±3.81 mm**, regardless of the drawn body size. Do not infer from the graphic. The `_Small` variants (`R_Small`, `C_Small`, `C_Polarized_Small`, `L_Small`, `D_Small`) connect at **±2.54 mm** — generators reach for them constantly and the 1.27 mm error dangles every wire silently. |
| `Device:R_Pack02` | Elements are **1↔4 and 2↔3**, bodies drawn *vertically*. Not 1↔2 / 3↔4. Get it backwards on a matched filter pair and you short the source across one resistor and the ADC inputs across the other — netlist and ERC both stay clean. Resistor packs are the natural way to make a matched pair un-mismatchable, so this row earns its keep. |
| `Connector_Generic:Conn_01xNN` | Pins face **left** at `x = X - 5.08`, but the body is **vertically centred on the placement point**, so pin 1 sits *above* it: `y = Y - 2.54*floor((N-1)/2) + 2.54*(n-1)`. Verified N = 1…12. Dropping the centring term is right only for N ≤ 2 and puts an 8-way header 7.62 mm out — every wire off-grid and dangling, silently. For 2-row parts there is no bare `Conn_02xNN` **for N ≥ 2** (`Conn_02x01` is the one exception and does exist) — the library ships `_Odd_Even`, `_Counter_Clockwise`, `_Row_Letter_First`, `_Row_Letter_Last` and `_Top_Bottom`, and using the bare name is a symbol-not-found, i.e. the exit-139/0-component failure below. Even pins sit on the **right** at `x = X + 7.62` for **`_Odd_Even` only**; the other variants number differently, so which pins are on which side changes with the suffix. N in the centring formula is the number of **positions per row** — 8 for `Conn_02x08`, not 2. Better: don't encode any of this, call `pn()`. |
| **Power symbols** | Pin is at `(0,0)` with length 0 → the connection point *is* the placement point. |
| **Labels** | Attach only if placed exactly **on** the wire. 1.27 mm off = dangling, silently. |
| **NC pins** | Either omit them from the symbol or place explicit `(no_connect …)`; otherwise ERC complains forever. |
| **Multi-pad nets in footprints** | An exposed pad and its thermal vias often share one pad number — take the **union** of every pad carrying that number, not the first and not the largest. On an EP-plus-thermal-vias footprint the vias sit inside the land, so largest and union agree (verified: `SOIC-8-1EP_…_ThermalVias`, 10 pads numbered `9`, both give 2.95 × 4.90 mm). On a **notched split land** they do not — vendors merge same-net pins into one land and mark the split with a notch, which is modelled as two *equal* overlapping pads, so "largest" is a coin flip returning about half the real land. Union is identical in the common case and correct in the rare one. See *Vendors merge same-net lands* below. |
| **`lib_symbols` entry names** | Must be the full `lib_id` (`"Device:R"`), not the bare name you grabbed out of the source library (`"R"`). KiCad never says *symbol not found*: the same one-line mismatch either **segfaults `kicad-cli` (exit 139, no output file)** or writes a netlist with **zero components and exit 0**, depending on unrelated details of the same file. Both reproduced on 9.0.4 from one string. Rename on the way in, and assert the netlist's component count. |
| **`PWR_FLAG`** | Needed once per net whose only source is a passive connector pin, else `power_pin_not_driven`. Put them in an isolated block — branching off a live stub collides with neighbouring pins. |

### Derive geometry from the library, never from arithmetic

The single biggest source of defects is hand-computed pin offsets. Parse the `lib_symbols`
you are about to embed and expose `pn(ref, unit, pin)`:

```python
def _xf(px, py, ang, mirror):
    x, y = px, -py                       # schematic Y is flipped vs the symbol editor
    a = math.radians(ang); ca, sa = round(math.cos(a)), round(math.sin(a))
    x, y = x*ca + y*sa, -x*sa + y*ca     # rotate FIRST...
    if mirror == 'x':   y = -y           # ...then mirror, in global coordinates
    elif mirror == 'y': x = -x
    return (x, y)

def pn(ref, unit, num):                  # -> exact global coords of that pin
    lid, X, Y, ang, mir = INST[(ref, unit)]     # keyed on (ref, UNIT), see below
    for n, lx, ly in LIBPINS[(lid, unit)]:
        if n == str(num):
            dx, dy = _xf(lx, ly, ang, mir)
            return (round(X+dx, 4), round(Y+dy, 4))
    raise KeyError(f"{ref} unit {unit} has no pin {num}")   # never fall through
```

Then wire with `poly(pn("U3",1,"2"), pn("U5",1,"5"))` and the coordinates cannot drift.

**`unit` is mandatory, not decoration.** A dual or quad op-amp is **one refdes with several
unit instances**, each placed at a *different* (X, Y), and the units do not share a pin space.
`Amplifier_Operational:LM2904` on 9.0.4:

```
LM2904_1_1  pins 3(-7.62,2.54) 2(-7.62,-2.54) 1(7.62,0)
LM2904_2_1  pins 5(-7.62,2.54) 6(-7.62,-2.54) 7(7.62,0)
LM2904_3_1  pins 8(-2.54,7.62) 4(-2.54,-7.62)      <- the SUPPLY unit
```

Key `INST` on refdes alone and it cannot even hold unit A and unit B; flatten `LIBPINS` across
units and a lookup by pin number returns unit 2's offset applied to unit 1's placement point.
Every offset above is on-grid, so the grid/orthogonality guard below **cannot catch it** — you
get a wire onto a neighbouring pin, or a dangle, in silence. Unit 3 is where pins 8 and 4 live,
which is the whole *Decoupling is a current loop* section.

**Unit 0 is "common to all units" and you must union it in, or the fix becomes a regression.**
Measured on the stock **10.0.5** libraries (2026-08-09): **666 `NAME_0_*` sub-symbols carry
pins, and 193 symbols keep *all* their pins there** — `Driver:DRV2510-Q1` has a
`DRV2510-Q1_0_0` with 17 pins and no `_1_1` at all. (On 9.0.4 these were 664 and 191, and that
part lived in `Driver_Haptic`, a library **10.0.5 no longer ships** — it was folded into
`Driver.kicad_sym`. A stale `lib_id` in a worked example is not cosmetic here: by the
`lib_symbols` row above it either segfaults `kicad-cli` at exit 139 or writes a 0-component
netlist at exit 0.) A strict `LIBPINS[(lid, unit)]` lookup raises `KeyError`
on every one of those, and on a genuine multi-unit part with shared supply pins in unit 0 it
drops them silently — worse than the flat lookup it replaced. So: pins for unit *u* =
`NAME_0_*` **∪** `NAME_u_*`, and the same for body style (0 is common to 1 and 2). Then raise
if the pin is in neither, rather than searching the other units. Add unit number to the enumerated parameter space under *Guards* as a **required**
dimension.

**Resolve `extends` before embedding.** **12 249 of the 22 784 top-level symbols in the stock
10.0.5 libraries (53.8 %, 223 libraries) are `(extends "PARENT")`** — one line to re-measure,
so re-measure rather than quoting this:

```
cd .../SharedSupport/symbols
grep -h -c $'^\t(symbol "'    *.kicad_sym | paste -sd+ - | bc     # 22784
grep -h -c $'^\t\t(extends '  *.kicad_sym | paste -sd+ - | bc     # 12249
```

These carry no pins and no graphics of their
own — `Amplifier_Operational:LM358` is literally `(symbol "LM358" (extends "LM2904") …)`. A
parser that reads only the named entry gets **zero pins for over half the library**, and
copying that bare entry into the `.kicad_sch` is exactly the one-line `lib_symbols` defect in
the table above: `kicad-cli` either segfaults (exit 139) or writes a 0-component netlist at
exit 0. Flatten the parent's pins and graphics into the emitted entry — which is what the GUI
writes — and assert the emitted symbol has ≥ 1 pin.

**The order of those two operations is not a style choice, and getting it wrong is invisible
on most boards.** KiCad mirrors *after* rotating. Mirror-first and rotate-first agree at 0°
and 180° — an axis mirror commutes with a half-turn — and disagree at 90° and 270°, where
they exchange **pin 1 and pin 2 of every two-pin part**. Clean ERC, clean netlist, swapped
part. Calibrate `pn()` by placing one part at 90° **with** `(mirror y)`, exporting the
netlist, and checking which pin reached which net — never by confirming that the board you
already have comes out right; an earlier version of this snippet had the order backwards and
still scored 164/164 on a real board. See *A perfect score on your own design may have tested
nothing* under **Guards** for why.

**Parse balanced blocks per item; never pair two fields with one regex.** A reviewer checking a
resistor pack's element mapping wrote `\(at ([-\d.]+) ([-\d.]+).*?\(number "(\d+)"` with
`DOTALL` and got coordinates that belonged to a *property's* `(at …)` paired with a later
*pin's* `(number …)` — two self-consistent, entirely fictional pin positions, which then
"proved" a correct filter was shorted. The same regex run over the instance and the library
definition disagreed, which was the only reason it was caught. Walk parens to extract each
`(pin …)` block, then read `(at …)` and `(number …)` from *inside that block*. This is the
mismatched-pairing cousin of "bounded searches lie": the search returned data, and the data
was invented.

### Assert what you can, and know what the assert misses

```python
def wire(x1, y1, x2, y2):
    # `raise`, not `assert`: python -O deletes asserts, and these two are the only
    # thing standing between a typo and a silently dangling wire.
    for v in (x1, y1, x2, y2):
        if not ongrid(v):
            raise ValueError(f"off-grid endpoint {(x1,y1,x2,y2)}")   # 1.27 mm grid
    if not (x1 == x2 or y1 == y2):
        raise ValueError(f"diagonal wire {(x1,y1,x2,y2)}")
```

A grid assert alone does **not** catch non-orthogonal wires — two diagonals shipped past it
and had to be found by eye. Add the orthogonality assert. Labels, junctions and no-connects
are not covered by either; check them separately.

### Power-symbol orientation — derive it, don't trust call sites

Stock power symbols draw their graphic *upward* from the connection point — `-15V` and
`PWR_FLAG` included, because the polarity is in the glyph shape, not its direction — except
for the **`GND*` and `Earth*` families**, which draw downward. That is **12 of the 101**
symbols in `power.kicad_sym`: `GND`, `GND1`, `GND2`, `GND3`, `GNDA`, `GNDD`, `GNDPWR`,
`GNDREF`, `GNDS`, `Earth`, `Earth_Clean`, `Earth_Protective`. So a symbol at the bottom of a
downward stub, or a `GND` at the top of an upward stub, is drawn back over its own wire. It is
purely graphical, so **ERC never sees it**, and it is easy to get right in one place and wrong
in another.

**Derive `graphic_down` from the symbol's own geometry, not from its name**, and do not
special-case `GND` alone: an earlier version of this rule said "every symbol except `GND`",
which draws `GNDA` — the obvious choice on the split-ground analog boards this skill is aimed
at — straight over its own wire, and the audit below inherits the same wrong premise and
passes it. Name-sniffing for "ground" fails in the other direction too: `VSS` and `VEE` sound
like grounds and draw **upward**.

Audit it instead of eyeballing:

```python
def check_rail_orientation():   # graphic must point AWAY from the attached wire
    if not _RAILS or not _SEGS:          # empty input is UNVERIFIED, never a pass
        raise ValueError(f"UNVERIFIED: {len(_RAILS)} rails, {len(_SEGS)} segments")
    bad, matched = [], 0
    for libid, x, y, graphic_down, gbox in _RAILS:
        hits = 0
        for (ax, ay, bx, by) in _SEGS:
            for (px, py), (qx, qy) in (((ax,ay),(bx,by)), ((bx,by),(ax,ay))):
                if (px, py) != (x, y):
                    continue             # this segment does not start on the rail
                hits += 1
                if px == qx:                                  # vertical
                    if (qy > py) == graphic_down:
                        bad.append(f"{libid} at ({x},{y}) drawn over its own wire")
                # horizontal attachment is legal and was previously SKIPPED, which
                # made "no matching segment" read as PASS
        if hits == 0:
            bad.append(f"{libid} at ({x},{y}) has NO wire on it -- dangling")
        else:
            matched += 1
    # ...and the glyph must not be drawn across some OTHER net's wire either.
    for libid, x, y, graphic_down, gbox in _RAILS:
        # gbox is the symbol's OWN graphic bbox -- min/max over the library
        # entry's polyline/rectangle/circle primitives, excluding property text
        # -- NOT a hardcoded 1.27 x 2.54.  Measured on power.kicad_sym (101
        # symbols, KiCad 10.0.5, 2026-08-09): SIX glyphs exceed that height --
        # Earth_Protective (5.080), +VDC (4.318), Earth_Clean (3.810), AC and
        # VAC (3.807 each), -VDC (3.175).  An earlier revision of this comment
        # said "exactly four" and omitted AC and VAC; it had been computed
        # without the polyline (xy ...) points, which are most of the geometry.
        # Earth_Clean is also the WIDTH outlier at x -2.540..+2.540, twice the
        # box.  GNDPWR is SMALLER (h 2.032) but is the one symbol whose glyph is
        # not x-symmetric about the pin (x -1.270..+1.016), so take x0/x1 from
        # the bbox's real min/max rather than from a width centred on the
        # connection point.  Recompute rather than trusting these six: the
        # enumeration is what moves, the rule (use the real bbox) is what holds.
        if gbox.w <= 0 or gbox.h <= 0:      # the (0,0,0,0) BBox failure, again
            raise ValueError(f"UNVERIFIED: degenerate glyph box for {libid}")
        x0, x1, h = x + gbox.x0, x + gbox.x1, gbox.h
        y0, y1 = (y, y + h) if graphic_down else (y - h, y)
        for (ax, ay, bx, by) in _SEGS:
            if ax == bx and x0 < ax < x1 and min(ay,by) < y1 and max(ay,by) > y0:
                bad.append(f"{libid} at ({x},{y}) glyph crosses vertical wire")
            elif ay == by and y0 < ay < y1 and min(ax,bx) < x1 and max(ax,bx) > x0:
                bad.append(f"{libid} at ({x},{y}) glyph crosses horizontal wire")
    print(f"{len(_RAILS)} rails, {matched} with an attached wire, "
          f"{len(_SEGS)} segments considered")     # count beside the verdict
    if bad:
        raise ValueError("\n  ".join(sorted(set(bad))))
```

The second loop matters: the first version only checked a symbol's *own* wire and passed a
`GND` whose triangle was drawn straight across an unrelated signal running underneath. When
you add a check *after* seeing a defect, reproduce the defect and watch the new check fire —
otherwise you have only asserted that the fixed version is fine.

**Property text renders only at 0° or 90°, whatever the symbol's rotation.** Deriving a
power symbol's label angle as `(360 - ang) % 360` yields **180** for a 180°-rotated
symbol, and KiCad then prints the net name **upside down**. Every rotated rail on one
sheet was affected, including a whole `PWR_FLAG` block, and neither ERC nor the netlist
can see it — it is purely visual. Use `90 if ang in (90, 270) else 0`.

### Schematic annotation is not board annotation

A label on the schematic helps whoever reads the schematic. It does **nothing** for whoever
solders the board. Connector pinouts, polarity, danger markings and voltage callouts belong on
`F.Silkscreen`, added from the layout script — derive their position from the real **pad
centres** so they follow the footprint if it moves or rotates:

```python
fp  = board.FindFootprintByReference(ref)
pad = next(p for p in fp.Pads() if p.GetNumber() == num)
px, py = pcbnew.ToMM(pad.GetPosition().x), pcbnew.ToMM(pad.GetPosition().y)
```

Silk needs a side choice, not a fixed offset: near a board edge or a neighbouring part, text
running the default direction trips `silk_edge_clearance` or `silk_overlap`. Set the side per
connector and let DRC confirm.

**Respect fab minimums, which DRC does not check by default.** JLCPCB's minimum silkscreen
stroke is 6 mil (0.153 mm) and PCBWay's is 0.15 mm; below that the fab drops the text or ships
it broken. A default of `thickness = height × 0.15` gives 0.12 mm at 0.8 mm text — under both.
Use `max(height × 0.15, 0.15)` and ≥1.0 mm height. Also state the **stackup** explicitly: a
board with no `(stackup …)` block gets the fab's house build, and every dielectric-dependent
number you computed (trace-to-plane stray, return-path coupling) silently assumes one.


## Datasheet discipline

Build a requirement ledger while reading each datasheet. Record every mandatory or explicitly
recommended supply, reference, bypass, protection, sequencing and exposed-pad requirement,
then map it to concrete refdeses and nets. Check the ledger against the exported netlist; a
pair of rail-to-ground capacitors is not a substitute for a specifically required direct
rail-to-rail capacitor. Do not declare the design complete with an unmapped requirement.

Before release, replace descriptive BOM placeholders with exact, orderable manufacturer part
numbers including package and performance grade. Verify the selected ordering code against the
same datasheet used for the design. A string such as `2x1k-0.05%-ratio` is a requirement, not
an MPN, and does not prevent procurement from substituting a part that breaks the error budget.

**The datasheet cannot tell you whether a part is still made, and "orderable" is a lifecycle
question, not a document question.** A datasheet lists every ordering code the part ever had;
discontinued ones stay on the page. TI PDN 20240530001.3 (2024-05-31) discontinued the *tube*
part numbers across the whole ISO776x family — *"TI will no longer support the tube part
number. The recommended replacement product is an exact replacement device shipped in large
tape and reel."* `ISO7762DW` is Obsolete; `ISO7762DWR` is Active. Same die, same package, same
footprint, **one letter apart**, and SLLSER1H lists both as perfectly valid devices. Checking
the ordering code against the datasheet — which is what the paragraph above asks for — cannot
catch this. Check **Part Status** (Active / NRND / Obsolete) and real stock at a distributor.

**Never derive one MPN by copying a suffix from another, including from the design in front of
you.** A part number proposed for a new position was built by lifting the packaging suffix off
the incumbent part already in the schematic. Both were the obsolete tube variant: the
incumbent string *was itself the stale one*, so the copy looked like consistency and was a
second instance of the same bug. An existing string in the repo is evidence of what was
ordered once, never evidence of what is orderable now.

**A value is not a part until value × voltage × package has been checked together.** Each of
the three is individually reasonable and the combination does not exist. `1u/250V` in an 0805
was caught in review; **`100n/250V` in an 0805 sat two rows below it in the same BOM and
survived**, because the fix was applied to the instance rather than the class.

Do **not** memorise where the frontier is — this section originally claimed "at 250 V an 0805
tops out in the tens of nF, and the smallest 100 nF/250 V X7R is a 1206", and that is simply
false: Holy Stone `C0805X104K251T` is a stocked 0.1 µF ±10 % **250 V X7R in 0805**, and Samsung
shipped an 0805 250 V 100 nF X7T in 2025. Ceramic energy density moves every year, and quoting
a limit from memory here is the exact defect *Never quote a spec from memory* below forbids.
**The rule is the method, not the number**: run a distributor parametric query on
(capacitance, voltage, dielectric, package) together and take the answer from what is actually
in stock. One example done properly — `CGA5L3X7R2E104K160AE` is a 100 nF/250 V X7R where TDK's
size code `CGA5` is 3216 metric, i.e. 1206 (`CGA4` = 0805, `CGA6` = 1210); a
non-soft-termination sibling `C3216X7R2E104K160AA` is the same size. Take
the size code from the vendor's own dimension table rather than reading the digits as an EIA
code, which is how this was first written down here as a 1210. Then derate: a 250 V X7R at
110 V of bias keeps roughly a third of
its nominal value, so put the effective capacitance next to the nominal one rather than
letting a decoupling calculation quietly use the label.

Worse than missing it outright: a later review *did* examine that capacitor and confirmed its
**creepage** — leaving a record of attention with the question never asked. When you fix one
instance of a defect class, sweep the BOM for siblings **by predicate** (here: every part
whose value carries a voltage suffix, checked against its package) and state what the sweep
covered. A fix that lands on one row and a sweep that lands on the class cost about the same.

**Put the BOM in the generator and enforce it in both directions** — a placed part with no BOM
row fails the build, and a BOM row with no placed part fails it too. Emit `MPN`,
`Manufacturer` and a compact `Spec` as symbol properties so the requirement travels on the
schematic rather than in a design note beside it. Without this, "R2/R3 are 0.1 %" is prose
while the schematic says `4k22` and will accept any 5 % thick-film part with the right
footprint — and if a firmware safety limit is *derived* from that 0.1 %, the limit silently
stops bounding anything. (Real case: a DAC code cap protecting an 85 V absolute-maximum ADC
input was computed from a 5 % worst-case gain that 0.1 % parts satisfy at 3.92 % and 1 %
parts blow through at 6.35 %.) Verifying ~30 MPNs is cheap enough to have no excuse: DigiKey
v4 takes **two-legged OAuth** (`grant_type=client_credentials` → bearer token → POST
`products/v4/search/keyword`), which is ~40 lines of `urllib` with no browser, no callback
port and no token store, and it returns the canonical part number, stock and the parametrics.
Several "obvious" part numbers will be wrong or dead; guessing is how `OPA455AIDDA` (does not
exist; it is `OPA455IDDA`) reaches a purchase order.

Build a **corner ledger** for every quantity that establishes bias, gain, safety margin or
component stress. Combine supply tolerance, passive tolerance, device min/max specifications,
and temperature or ageing terms where material; then prove every result stays inside the
datasheet's characterized operating range. Typical-value arithmetic is useful for nominal
performance, never for demonstrating compliance. In particular, do not infer that a pin named
`SENSE`, `REF` or `FB` is high impedance — use its specified current when calculating copper
drop, bias current and drift.

**A DC error that calibration removes still has a temperature coefficient, and that part
survives.** It is tempting to wave away an IR drop on a board whose reference instrument reads
the true output at every sweep point — the static term genuinely does vanish. Its *tempco* does
not: copper is **+3930 ppm/K**, so 259 µV of *uncancelled* drop in a reference return was
1.0 µV/K at the buffer and, through the ×10 output stage behind it, **10.2 µV/K** at the 100 V
output — ±51 µV over a ±5 K room swing. Carry the drop through the gain before applying the
tempco: the coefficient acts where the error is, the budget lives at the output, and collapsing
those two into one multiplication (as an earlier version of this paragraph did, quoting a
single leg's 182 µV against the output's 10.2 µV/K) under-reports drift by whatever gain sits
downstream. Two further traps compound here. First, a thermal term is
sub-0.1 Hz, so it falls outside a 0.1–10 Hz noise budget and gets dismissed as "not in band"
rather than bounded. Second, the copper is usually widened or re-routed for a reason nobody
records, and the next person narrows it back. Compute the tempco, write it next to the static
figure, and say explicitly which one the calibration removes.

**Never quote a spec from memory.** Download the PDF and read the electrical-characteristics
table. Every one of these was a real error caught by doing so:

- **Noise gain ≠ signal gain.** An op-amp's input-referred noise is multiplied by
  `1 + Rf/Rin`, not by the inverting gain `Rf/Rin`. Using 10 instead of 11 made a whole noise
  budget 10 % optimistic.
- **rms vs p-p.** Pick one per table and label it. Mixing them understated a term by 6.8× —
  and harmlessly for the part chosen, materially for the part rejected, so the comparison that
  justified the decision was not the one computed.
- **Land pattern vs stencil.** The same number appears on both pages meaning different things.
  On a TI PowerPAD the *land* page gives metal and solder-mask opening; the *stencil* page
  gives a paste aperture. Read the "EXAMPLE BOARD LAYOUT" page, not "EXAMPLE STENCIL DESIGN".
- **A stock footprint that matches by vendor and body size can still be the wrong land.**
  KiCad ships `Oscillator_SMD_ECS_2520MV-xxx-xx-4Pin_2.5x2.0mm`. An ECS-2033 is the same
  vendor, the same 4-pad package, the same 2.5 × 2.0 mm body — and the wrong land:

  | | ECS-2033 datasheet | KiCad `ECS_2520MV` |
  |---|---|---|
  | pad size | 1.10 × 0.90 | 0.80 × 0.90 |
  | pad centres | (±0.90, ±0.70) | (±0.725, ±0.925) |
  | pin 1, top view | bottom-left | top-left |

  The stock land is the same family drawn at **90°** with pads 0.30 mm narrower, giving a
  **negative 0.125 mm toe fillet** — the terminal hangs off the end of its own pad. That is
  not conservative, it is unsolderable, and it passes DRC and every netlist check silently,
  because a footprint's *identity* is never checked against the part it is assigned to.

  So: **matching by name, vendor and body size is a hypothesis, not a verification.** Compare
  **pad size, pad centres and the pin-1 corner** against the datasheet's Suggested Land
  Pattern before accepting any stock footprint for a part it is not named after. When you must
  generate the correct land, make the stock one the **calibration case** — the generator
  should reproduce the stock geometry from the stock part's numbers, so you know it is
  building lands correctly and not just building *a* land.
- **Datasheets contradict themselves.** One part listed abs max as both 150 V and 160 V in
  different sections. Quote the conservative one and say why.
- **Recommended operating ≠ absolute maximum.** And an absolute maximum is not a design target.
- **The datasheet outranks the vendor's own SPICE model.** Trust order: datasheet *table* >
  datasheet *chart* > vendor `.lib`. A model is a *derivative* of the datasheet, usually
  auto-fitted, so it cannot hold more information — only lose or distort it, and the
  temperature block (`TRS1`/`TRS2`, `EG`, `XTI`) is the least validated part. One Schottky's
  vendor model matched its own datasheet at 25 °C but gave 0.863 V against a 0.66 V typ at
  125 °C / 15 A — 200 mV in the wrong direction, and worse at higher current. It contradicted
  the datasheet it shipped with. Validate any model at the operating point **and** at
  temperature before relying on it; if it disagrees, fit from the datasheet.
- **Stock KiCad footprints are not safety-checked.** A stock exposed-pad footprint left
  0.200 mm between a −15 V pad and a +110 V pin. Always measure pad-to-pad clearance for HV
  parts; TI land drawings often carry a note explicitly permitting a narrower pad for creepage.
- **Exposed pads are often electrically connected to a rail**, not ground — and if the symbol has no pin
  for it, the netlist cannot enforce it and DRC will not complain. Add an `EP` pin.
- **Diode-clamped pins need series current limiting** — think about power sequencing, e.g. a
  logic rail up before an HV rail.
- **Logic-level compatibility**: a 5 V pull-up into a 3.3 V-only GPIO destroys it.

### Getting the PDF: vendor WAFs, and the part substitution they cause

Several vendor sites (Analog Devices and ST among them) sit behind **Akamai Bot Manager**. The
signature is distinctive: the TLS handshake completes normally, then `curl` **hangs** on
HTTP/1.1 and gets `INTERNAL_ERROR` on HTTP/2, while a browser gets a 403 whose body carries an
`errors.edgesuite.net` reference. Handshake success rules out certs, network and auth — the
WAF is dropping you on fingerprint.

**A *default* headless browser does not help — a real one does.** The distinction is
`channel="chrome"` driving the installed Chrome, and [`SETUP.md`](SETUP.md) has the working
recipe plus the preflight that proves it is available on this machine; do not read the
paragraph below as "browsers are useless here". Playwright's default Chrome advertises
`HeadlessChrome/<version>` in its User-Agent and Akamai 403s on that token alone —
`navigator.webdriver` was already `false`, so stealth patches miss the point. Verified against
both a product page and the direct `…/media/…/*.pdf` path: 403 on each.

What works is a **headed** browser with a throwaway profile that forces PDFs to download
instead of opening in the built-in viewer:

```sh
P=/tmp/dl-profile; D=/tmp/dl; rm -rf $P $D; mkdir -p $P/Default $D
printf '%s' '{"download":{"default_directory":"'$D'","prompt_for_download":false},
  "plugins":{"always_open_pdf_externally":true}}' > $P/Default/Preferences
open -na 'Google Chrome' --args --user-data-dir=$P --no-first-run --new-window "<pdf-url>"
```

Wait for the `.crdownload` to disappear, then check `file -b --mime-type` and `pdfinfo` —
Chrome's viewer shell and vendor stub pages both masquerade as a download. Do **not** use CDP
`Network.getResponseBody` on a PDF tab; it returns the viewer's HTML, not the document.

Also check whether the project stores **distributor API credentials** (DigiKey v4 and similar
serve datasheets and bypass the WAF entirely). One agent grepped only `~/.claude`, `~/.config`
and the environment, reported "no distributor API key configured", and never looked inside the
repo it was working in — where three rotating API keys were sitting.

**A part substituted because you could not read its datasheet is a design change made for
tooling reasons, and it must be labelled as one.** Blocked on two ADI fixed-output LDOs, an
agent switched to *adjustable* TI parts — correctly refusing to quote specs from memory, but
the swap added eight divider resistors and moved a rail from an exact −2.500 V to −2.446 V.
That is a real change to the board, justified by nothing electrical. Exhaust the access routes
above first; if you still must substitute, say plainly in the design doc that the reason was
access, not engineering, so it can be revisited.

### Reading the PDF: four failures that each cost a rework

- **Package and land drawings live at the END, after the application notes.** An agent read
  pages 1–6 of an 18-page datasheet, found no land pattern, and reported the footprint
  "unresolvable — the drawing has merged multi-pin pads and is marked not-to-scale". The
  recommended land pattern was on page **16**, fully dimensioned, with twelve *individual*
  pads. It had also misread three separate same-net pads as one merged pad. **Before concluding
  a datasheet lacks something, `pdftotext` the whole file and grep it** — "LAND PATTERN",
  "PACKAGE INFORMATION", "RECOMMENDED". A bounded read produced a confident false negative and
  blocked the board.
- **"DRAWING IS NOT TO SCALE" does not mean the vectors are worthless.** Extract the page's
  geometry (`pdftocairo -svg`) and check whether a view is *internally* to scale by testing a
  known callout against it — one bottom view came out at 19.837 pt/mm, confirmed by the body
  outline to better than 0.5 %. Then assign callouts to features by matching feature-to-feature
  distances against the callout values. Use the picture **only** to decide which callout points
  at which feature; take every *number* from the callout text. This turns an unreadable drawing
  into a checkable one.
- **Vendors merge same-net lands and mark the split with a notch.** One MPS LGA merges each
  same-net pin pair into a single 0.90 mm land with a 0.30 × 0.125 mm notch. Model it as two
  pads that *overlap slightly* (0.45 + 0.02) so their union is bit-for-bit the recommended
  land and no gerber hairline appears between them, with each half containing its own pin's
  nominal centre. Cross-check the pairing against the pin-function table **without using
  either to derive the other** — if geometry and netlist agree independently, the mapping is
  right; if they disagree, stop rather than renumbering.
- **The figure may be a RASTER, and then `pdftotext` returns nothing — which reads exactly
  like "the datasheet does not specify it".** A small-vendor oscillator datasheet (ECS
  2025/2033) has its package drawing *and* its Suggested Land Pattern as embedded images with
  no text layer at all, so `pdftotext` yielded the parameter tables and silently dropped every
  dimension. The first pass concluded the land pattern "could not be read out of the
  datasheet" and shipped a footprint chosen by vendor-and-body-size instead. It was the wrong
  land (see *A stock footprint that matches by vendor and body size can still be the wrong
  land*, **above**, under *Never quote a spec from memory*).
  `pdftocairo -svg` also gives you nothing here — there are no vectors to extract.

  **Render and read it**: `pdfimages -png` (or `pdftoppm -r 300`) the page, then *look*. The
  callouts are printed in the image and are perfectly legible at 300 dpi. Then hold the line
  the previous bullet draws, because it is the whole difference between evidence and a guess:
  a **dimension callout printed in the figure** is a datasheet number and may be used; a
  length **scaled off the picture** is a model and must be labelled as one wherever it lands.
  In the same drawing the pad dimensions were callouts (used, and they overturned the
  footprint) while two unlabelled mechanical terminals had no callout anywhere — those were
  scaled, recorded as "≈", and every downstream tolerance was widened by more than the
  scaling error could be.


## Guards (checks, validators, audits)

Apply the global guard checklist in `~/.claude/CLAUDE.md`. EDA-specific instances:

- **A guard whose precondition moves silently stops guarding.** A track moved inside a rule
  area that relaxed clearance to 0.6 mm; the plane pulled back to 0.601 mm; DRC stayed green
  while the stated 1.0 mm design minimum was gone. DRC was not wrong — it was answering a
  different question than the one that mattered. Keep an independent audit that re-measures
  real geometry, and say in the docs that *the audit*, not DRC, enforces the figure, so nobody
  deletes it as redundant.
- **Calibrate against a known-bad input.** Copy the board, inject the exact fault the guard
  exists to catch (e.g. widen the EP land back to the unsafe stock size), and watch it exit
  non-zero. A guard never seen to fire is not a guard.
- **A guard is blind to whatever its data model omits, and that blindness is silent.** A
  text-overlap check iterated the generator's list of *text* objects and passed a caption
  printed straight through a power symbol's net name — because a net name is a symbol
  **property**, not a text object, so it was never in the collection being compared. The
  guard was not wrong about the objects it saw; it could not see the colliding one. When
  you write a guard, enumerate what the design contains and ask which of those object
  classes your loop actually visits — properties, symbol graphics, zone fills and
  drawing-sheet items are the usual omissions. Calibrating with a fault built from the
  class you already iterate will never reveal this.
- **A perfect score on your own design may have tested nothing.** The `pn()` transform above
  was validated at **164/164 pins** on a real board and was still wrong for a third of its
  input space: that board contained no mirrored symbols, so four of the twelve rotation ×
  mirror combinations (4 angles × 3 mirror states) were never exercised, and those four swap
  pin 1 with pin 2. The denominator below is 24 because the enumeration checks **both pins**
  of a two-pin part in each of the twelve cells — say which, or the arithmetic reads as a typo
  in the one paragraph that is about reporting coverage honestly. Your design
  is a *sample*, and the branches it never reaches are precisely the ones nobody has looked
  at. For any helper with a small discrete parameter space — rotation × mirror, layer set,
  package variant, pad shape, unit number — **enumerate the space and check every cell against
  ground truth** (here: place one part per combination, export the netlist, ask KiCad which pin
  reached which net). Report coverage, not pass rate: "164/164" and "16/24" were the same code.
- **Read the shape of a failure, not its count — and test your theory of it.** A harness
  reporting **0/24** is almost never telling you the thing under test is maximally wrong; it is
  telling you the harness did not run. A wrong coordinate transform puts labels on the *other
  pin*; it does not make them land on nothing. Distinguish "wrong answer" from "no answer"
  before concluding anything. Then be equally sceptical of your diagnosis: *"the invalid sheet
  UUID broke it"* was a confident, plausible and entirely wrong explanation of one such failure
  — a mismatched sheet UUID netlists fine, and the real cause was a `lib_symbols` name that did
  not match its `lib_id`. Isolating the two took one minute and reversed the conclusion. A
  cause you did not test is a guess you are about to write down as a fact.
- **Bounded searches lie.** A `\(text "([^"]{5,600})"` regex silently returned 19 of 26 text
  items and produced a confident "not found" for content that was present. If a search reports
  absence, verify the search could have seen the thing.
- **Derive limits from constants**, never hardcode. A DAC code cap computed from the reference,
  divider ratio and gain moves when those change; a magic `0xD999` silently goes stale.
- **Fail closed, and raise rather than clamp.** Silently clamping an over-limit request makes
  a sweep record two different setpoints at the same actual voltage — data that looks valid.
- **A guard keyed on name literals must assert its subject exists.** `HV_NETS = {"+110V", …}`
  with no existence check meant renaming the net silently removed the entire rail from the
  audit — which still printed PASS. Same flaw in the matching `.kicad_dru` rules, so DRC went
  green in lockstep. Assert the named nets are present, or key on a netclass instead.
- **Then make the guard survive a rename, or it becomes the thing that gets deleted.** The
  bullet above is necessary and not sufficient. A symmetry audit that hardcoded
  `"Net-(JP1-A)" ↔ "GND"` did assert its subject, and when the net was renamed to `/sense+` it
  correctly refused with `UNVERIFIED` rather than reporting a clean board — the guard working
  exactly as designed. But a rename is not a design change, and a guard that demands a code
  edit every time one happens is the guard someone eventually "fixes" by removing the
  assertion. **Derive the name from the thing that defines it.** Those two nets are whatever
  sits on R1 pads 2 and 3, by definition of a 4-terminal shunt:

  ```python
  pos, neg = pads["2"].GetNetname(), pads["3"].GetNetname()
  need(pos and neg and pos != neg, "R1's Kelvin taps are unnamed or shorted")
  SWAP[pos], SWAP[neg] = neg, pos
  ```

  Now a rename cannot unclassify anything, and the guard still fails closed on a missing part,
  an unnamed net, or both taps on one net. Note [`PCB.md`](PCB.md)'s *derive the mirror's net
  map from the schematic* already said this in spirit — and the implementation still hardcoded
  strings, which is why it is worth saying as a mechanic and not only as a principle.
- **An exemption must be scoped to the pair, not to one object.** A "package floor" that fires
  when *either* object belongs to that package is a mute button: a router-placed HV track
  0.70 mm from an exposed pad inherited a 0.60 mm package excuse and passed both DRC and the
  audit. Require both objects to belong to the package, and bound any genuine exception
  (e.g. pin escapes, which pitch really does fix) by a measured floor so a *new* closer object
  cannot inherit it.
- **Set floors to the standard, not the standard minus epsilon.** Every floor in one audit sat
  0.01 mm under the figure it cited (`0.79` for "exactly IPC A6 = 0.80"), so it passed
  geometry that did not meet the standard it claimed to enforce.
- **A rating is not a limit.** The sibling failure to the one above: a check whose threshold is
  the *destruction* point instead of the *design* point passes everything that is not already
  broken. A fault-power guard compared dissipation against nameplate, so it passed parts
  sitting in the high nineties of their rated power — precisely the margins that had motivated
  writing it, and it reported them as fine. (The worked example under *Close every external
  interface* is a sibling case that lands just **over** nameplate at 103 %; a nameplate
  comparison catches that one and still waves through everything at 99 %, which is the point.) It only became a guard once it carried an explicit
  derating factor (60 % of nameplate for a permanent fault). Whenever a check compares against
  a datasheet maximum — power, voltage, current, temperature — ask what fraction of it you are
  actually willing to ship, and put *that* number in the comparison. Then re-run the
  known-bad calibration, because a threshold this loose passes the calibration inputs too.
- **Calibration must cover the case that matters, not the case you already fixed.** A cap guard
  tested `NaN` and `0.01` — both outside its acceptance band — and never tested a *plausible*
  bad measurement inside it, which is the one that raised the cap to full scale.
- **Protection on a precision node has a cost.** A TVS sized for an 85 V input leaks µA near
  breakdown — comparable to the entire load on a node built for 134 µVpp. Clamp at the victim
  end, disconnect with a relay, or document the residual risk; don't reflexively fit the part.


## Reviewing someone else's numbers

Recompute, don't read. Re-derive the arithmetic independently, re-run the statistics from the
raw data, and re-measure geometry from the files. Reviews in this domain are frequently right
about the *defect* and wrong about the *number* — one correctly identified an unsafe footprint
but quoted a dimension that conflated a mask width with a metal width. Confirm the finding and
the figure separately.
