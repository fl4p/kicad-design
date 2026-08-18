# `kicad-design/scripts` — reusable verification and routing helpers

**Status: live and referenced from `SKILL.md` / `PCB.md`.** The first helpers
were extracted 2026-08-11 from `pwr-metering/hw/shunt-adc`; the qualified
autorouting boundary was added and forward-tested 2026-08-18.

## Why these helpers and not others

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
| `kicad_autoroute.py` | strict shared config, route, manifest, DRC-baseline, and non-routing-projection contracts |
| `kicad_autoroute_tools.py` | unapproved downloads, ambient Java/JAR drift, unsafe archives, or a cache receipt that does not bind installed contents |
| `kicad_route_candidate.py` | an external router or diagnostic writing the generated board; an unpinned router, lost DSN constraints, or out-of-scope copper reading as an acceptable route |
| `kicad_route_manifest.py` | a reviewed candidate being promoted without exact seed/input/applicator/toolchain and route-digest equality |

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

## Qualified Freerouting candidate and manifest workflow

`kicad_route_candidate.py` is a scratch-only, fail-closed wrapper for the
pinned Freerouting toolchain. `kicad_route_manifest.py promote` is the separate,
digest-explicit approval boundary. A project generator consumes the resulting
canonical route manifest; Java, DSN, SES, and the raw imported board never become
build dependencies or production artifacts.

Use tracked `autoroute.json` configuration for promotable work. It binds the
backend, hermetic input list, exact KiCad net classes and styles, allowed layers,
limits, position-sensitive seed DRC baseline, shell-free seed/final project
audits, project-local applicator, and manifest output.

Check or install the locked toolchain:

```sh
python3 scripts/kicad_autoroute_tools.py status
# Network/cache mutation requires user authorization and the explicit flag:
python3 scripts/kicad_autoroute_tools.py install --yes
```

The lock pins Freerouting 2.3.0 and a Java 25 Temurin JRE by URL, size, and
SHA-256 for each staged platform. Install uses verified TLS, traversal-safe
archive extraction, and an atomic receipt. A promotion-enabled cell additionally
pins and rechecks the installed Java executable and complete installed-tree
digests, so the receipt cannot authorize locally replaced runtime contents. It
never falls back to an ambient JAR/JRE for configured promotion.

Prepare or route a project-owned seed:

```sh
python3 scripts/kicad_route_candidate.py project/seed.kicad_pcb \
  --config project/autoroute.json \
  --prepare-only \
  --report work/prepare-report.json

python3 scripts/kicad_route_candidate.py project/seed.kicad_pcb \
  --config project/autoroute.json \
  --report work/route-report.json \
  --keep-workspace work/router-workspace \
  --fail-on-findings
```

Full runs must retain a workspace. The source board and every declared input are
hashed before and after; only scratch paths are passed to KiCad's
`ExportSpecctraDSN`/`ImportSpecctraSES`, Freerouting, saves, DRC, and audits.
The input bundle rejects symlinks and includes same-stem sidecars, declared
inputs, top-level project Python, and project-local library resources.

KiCad 10's SES importer replaces board routing. Raw SES “removals” are therefore
not accepted as edit authority. The wrapper:

1. refills and qualifies a fresh seed against the exact structured-DRC baseline
   and seed project audits;
2. exports DSN after proving every seed route appears as fixed copper;
3. runs the bounded pinned router with ambient Java/Freerouting options scrubbed;
4. retains the raw import but computes only its additions relative to the seed;
5. discards excluded-net/layer additions and rejects wrong styles or unsupported
   primitives;
6. applies the canonical allowlisted additions to another fresh seed;
7. compares that board against an empty-apply control using a complete
   S-expression non-routing projection;
8. proves every protected seed route remains, then reruns structured DRC,
   connectivity, parity, input/source integrity, and configured project audits.

Freerouting 2.3.0 must run with automatic neckdown and fanout disabled. Fanout
has an independent micro-neckdown fallback, so disabling automatic neckdown
alone can still produce wrong-width segments. The wrapper also passes `-inc`
as defense in depth, but live calibration proved ignored-class routing can still
occur; post-import filtering is the authority.

Verdicts are `PREPARED`, `PREPARED_WITH_FINDINGS`,
`PROMOTABLE_CANDIDATE`, `REPORT_ONLY`, `REJECT`, or `ERROR`.
`PROMOTABLE_CANDIDATE` requires every promotion check to be exactly true and
no promotion blocks. Exit 0 means the report completed; use
`--fail-on-findings` when rejection must be a failing process status.

After visual review, promote exact digests:

```sh
"$KICAD_PYTHON" scripts/kicad_route_manifest.py promote \
  --seed project/seed.kicad_pcb \
  --candidate-board CANDIDATE_BOARD_PATH_FROM_REPORT \
  --config project/autoroute.json \
  --report work/route-report.json \
  --project-root project \
  --approve-candidate-sha256 CANDIDATE_SHA256 \
  --approve-report-sha256 REPORT_SHA256 \
  --output-manifest project/routes.json
```

Promotion opens the actual candidate board and re-verifies its digest and exact
scoped route delta in addition to the report verdict, every check, report/seed
digest, configuration, reconstructed full live input bundle, project applicator
source, route digest, current compatibility cell/tool receipt, and configured
output path. The strict manifest contains only canonical
segments and F.Cu-to-B.Cu through-vias with integer-nanometre geometry, exact
style/scope, the reviewed seed digest, input/toolchain/applicator provenance,
and review digests. The project-local applicator must compare the generated seed
to `seed_sha256` before applying anything and re-extract exact routes after the
final save.

`--candidate-board` must be the exact `candidate.board_path` recorded by the
report (normally the fresh-seed, filtered candidate), not a guessed workspace
path and not the raw SES import. The promoter opens that board, verifies its
digest, and independently extracts its scoped delta against the seed.

After promotion, use a project-owned final verification wrapper where available.
It should run the normal full generator in genuine two-run reproduction mode and
write one canonical report binding the final board digest, JSON DRC and schematic
parity counts, calibrated project-audit result, and exact manifest route digest.
This wrapper is the release-evidence boundary; separate successful commands must
not be assembled into an implicit PASS.

Promotion is enabled only for an exact qualified
`(OS, architecture, kicad-cli, pcbnew)` cell in
`kicad-autoroute-compatibility.json`. Other cells remain report-only until
forward-qualified. The demonstrated cell is KiCad/pcbnew 10.0.5 on Darwin
arm64 with Freerouting 2.3.0 and the pinned Java 25 runtime.

Focused tests cover strict config/manifest schemas, canonical route order and
digest, duplicates/overlaps, KiCad 10 through-via enums, exact DRC
multiplicity/positions, hermetic/symlink handling, installer authorization and
archive safety, tool pins, scope/style filters, route-lock normalization,
protected-route preservation, DSN fixed copper, path collisions, shell-free
audits, environment scrubbing, and atomic reports.

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

### Round four — portability, and a parser bug it exposed

Asked whether these ran on Linux/Windows, the answer was no. Four breaks, one
of them silent:

- **Every text read used the locale encoding.** `read_text()` without
  `encoding=` uses `locale.getpreferredencoding()` — UTF-8 on macOS/Linux,
  typically **cp1252 on Windows**. KiCad writes UTF-8 everywhere, so
  `10 µF ±10%` would arrive as `10 ÂµF Â±10%`, and `errors="replace"`
  guaranteed it could never raise. All reads now go through `_read_utf8()`,
  which decodes strictly and raises with the byte offset.
- `kicad-cli` discovery was macOS/Linux only, and reverse *lexical* sorting
  ordered `9.0` above `10.0`, picking an old install. Now per-platform with a
  numeric version key. The Flatpak GUI export is **not** `kicad-cli`
  (`flatpak run --command=kicad-cli org.kicad.KiCad` is), so it is documented
  rather than offered as a path.
- `library_stats()` had the same two defects, plus a missing
  `ProgramFiles(x86)`; a `KICAD_SYMBOL_DIR` override was added.
- `/tmp/...` was hardcoded, and `mkdtemp()` leaked on both success and
  exception paths (two stale directories were found). Now `tempfile` plus
  `atexit`.

**The parser bug the portability review turned up is the important one.** The
paren walkers respected string literals, but the `finditer` that *locates
candidate openers* did not. A netlist containing

```
(value "Exposed pad is FLOATING (net TPAD), not ground")
```

counted a phantom `(net ` opener and failed to parse — **two real project
netlists were affected**. Opener scans now run over `_mask_strings()`, which
blanks quoted contents while preserving offsets.

Also: BOM-prefixed `.kicad_pro` is tolerated (RFC 8259 §8.1 lets parsers ignore
it), `subprocess` output is decoded as UTF-8 rather than by locale, and a
non-existent body style is rejected instead of returning `[]`.

Independently swept during that review: all 223 stock libraries and all 212
KiCad artifacts under `~/dev` (60 PCB, 53 project, 72 schematic, 15 symbol,
3 netlist, 9 report) decode as strict UTF-8 — zero invalid, zero BOMs, three
CRLF. So strict decoding is sound for modern KiCad, not an overshoot.

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
