# What was taken from atopile, and what was not

Companion to the evaluation at `pv/ee/plans/atopile-evaluation.md` (2026-09-07), which decided
*ignore the system, adopt one component, steal five ideas*. This file records the execution: what
landed, what was declined and why, what was measured rather than assumed, and the MIT provenance of
every line that traces back to atopile.

Nothing in this repository imports atopile, depends on it, or contains a copied line of its code.
Everything below was reimplemented from a read of the source. The reason for that posture is in the
evaluation's R5: the public repository has been frozen since 2026-03-11 by stated policy, the
shipping source exists only in PyPI sdists, and the 0.15 line is soft-deprecated.

## The source that was read

| | |
|---|---|
| artefact | `atopile-0.15.8.tar.gz`, PyPI sdist, 2026-08-07, 95 594 058 bytes |
| sha256 | `76c42f33f151947dbe533d4d0a04b60623b84c38ced6e40d17c3099e5f59b955` |
| licence | MIT (`LICENSE` in the sdist root) |
| git | **absent** — no v0.15.x tag or release exists on GitHub; `main` is frozen at 2026-03-11 |

Every `file:line` below is in that sdist. It was fetched with plain `curl` from
`files.pythonhosted.org`; no WAF, interstitial or CAPTCHA was encountered, so no rung above 1 of the
fetch ladder was needed.

---

## ADOPT — the Altium/Cadence→KiCad importer, tested

The evaluation recommended `src/faebryk/libs/eda/` (25 157 lines of Python across 63 files) as a
standalone tool and left four questions open. All four are now answered by measurement, on this
machine, on 2026-09-07, on two real Altium projects that were already on disk.

### It installs and runs, offline, with no account and no project

`uv venv --python 3.14` plus `uv pip install atopile==0.15.8` completes in about a minute; the
`cp314 macosx_11_0_arm64` wheel exists and is 7.7 MB. `python3.14` was already present at
`/opt/homebrew/bin/python3.14`.

* **Open question 3 (offline, no project) — answered YES.** `python -m faebryk.libs.eda convert
  <file>.PcbDoc` ran in a scratch directory with no `ato.yaml`, no project root and no network
  traffic. The import graph of `src/faebryk/libs/eda/` reaches `httpx`, `git` and `webbrowser` only
  through `altium/models/server/api.py` and `.../server/cli.py`, which the `convert` path does not
  touch.
* **Open question 6 (does it require `ato auth login`) — answered NO.** No authentication call is on
  the `convert` path and none was attempted. The 0.15.8 auth requirement is on part picking, which
  this path does not use.
* Telemetry was disabled for every run (`CI=1 FBRK_TELEMETRY=0`). `ConfigFlag("TELEMETRY", default=
  not os.getenv("CI"))` at `src/atopile/telemetry/config.py:11-15` means the `CI` environment
  variable alone is sufficient; no file needs to be written. This matters because the telemetry
  opt-out path itself *writes* `telemetry.yaml` into the user's config dir
  (`config.py:63-66`).

### It produces boards KiCad 10 loads and DRCs

| input | atopile output | kicad-cli 10.0.5 |
|---|---|---|
| `ReboostV2.1.PcbDoc` (5 988 352 B) | `ReboostV2.1.kicad_pcb` (641 862 B) | loads; DRC completes, 1007 violations / 256 unconnected |
| `tinyCurrent_Version1.PcbDoc` (3 468 800 B) | `tinyCurrent_Version1.kicad_pcb` (1 110 127 B) | loads; DRC completes, 84 violations / 1 unconnected |

The violation counts are properties of the *source design as converted*, not a grade of the
converter; they are recorded so a later run can be compared against them.

### A/B against KiCad's own Altium importer, on identical inputs

This is the comparison that decides whether the tool earns its place, because Fab already has a
converter wrapping KiCad's built-in importer. Both converters were run on the **same** `.PcbDoc`
files. (An earlier attempt compared atopile's output against
`~/dev/ee/hw/reboost-kicad/Reboostv2.kicad_pcb` and produced two apparent defects — dropped copper
arcs and a global layer flip. Both were artefacts of comparing against a *different board revision*
that had also been edited after import. Both are **retracted**; the numbers below come from a fresh
`PCB_IO_MGR.Load(ALTIUM_DESIGNER)` on the identical file. The retraction is recorded rather than
deleted because the error is easy to repeat.)

ReboostV2.1, atopile 0.15.8 vs KiCad 10.0.5:

| quantity | atopile | KiCad built-in |
|---|---|---|
| footprints | 184 | 184 |
| pads | 589 | 585 |
| board track segments | 1436 | 1436 |
| segment layer histogram | B.Cu 1184 / F.Cu 167 / In1 82 / In2 3 | **identical** |
| footprint layer histogram | B.Cu 160 / F.Cu 24 | B.Cu 160 / F.Cu 23 |
| vias | 278 | 278 |
| zones | 39 | 27 |
| distinct net names | 114 | 113 |
| net names in common | 112 | 112 |
| zero-sized pads | 6 | 12 |
| polygon-outline arcs | 0 | 64 |

tinyCurrent: footprints 45 vs 44, pads 110 vs 109, segments 325 vs 325, vias 4 vs 4, zones **1 vs
4**, nets 23 vs 22 (all 22 in common).

**What this says.**

* The two converters agree on the load-bearing structure — footprint count, track count, track layer
  assignment, via count, and the net set. Two independently written readers of an undocumented
  binary format agreeing to that degree is the strongest evidence either of them is right that is
  available without Altium itself.
* **atopile preserves layers KiCad drops.** KiCad's importer emitted nine `Layer 'Internal Plane N'
  could not be mapped and will be skipped` warnings on ReboostV2.1; atopile's 39 zones against
  KiCad's 27 is consistent with that (atopile also splits a multi-layer zone into one zone per layer,
  so the two effects are mixed and the split was not separated out).
* **atopile emits half as many zero-sized pads** (6 vs 12); the 6 it does emit are real and draw a
  KiCad load warning (`Invalid zero-sized pad pinned to 1µm`).
* **atopile loses two things.** Polygon-outline arcs are polygonised into line segments (0 vs 64
  `(arc` items inside `(polygon (pts ...))`), so a curved zone boundary comes back faceted. And an
  Altium overbar net name leaks through raw: `R\\S\\T\\` where KiCad's importer writes `~{RST}`.
  That one net is the *only* net-name disagreement across both boards.
* **tinyCurrent's 1-vs-4 zone count is unexplained** and is the one result that should be settled
  before the tool is trusted on a zone-critical board.

### Verdict on A1

**Adopt as a cross-check, not as a replacement.** It is a genuinely independent second reader of a
binary format nobody documents, it runs offline in a throwaway venv, and it disagrees with KiCad's
importer in exactly the places worth knowing about. That is worth more than either converter alone:
the skill's own doctrine is to take the expectation from an independent authority rather than from
the artefact's neighbourhood, and until now there was only one authority for an Altium import.

**Integration path, with the honest cost.**

1. `uv venv --python 3.14 /path/to/ato-venv && uv pip install atopile==0.15.8` (~1 min, ~40 MB
   installed, Python 3.14 exactly).
2. Always `CI=1 FBRK_TELEMETRY=0` in the environment.
3. `python -m faebryk.libs.eda convert board.PcbDoc` → `board.kicad_pcb`.
4. Convert the same file with KiCad's own importer (`PCB_IO_MGR.Load(ALTIUM_DESIGNER)` under KiCad's
   bundled Python — note it needs a `wx.App` constructed first on KiCad 10, or the load aborts on
   `assert "traits" failed in Get(): create wxApp before calling this`).
5. Diff the inventories. Treat every disagreement as unresolved until settled against the Altium
   source, and treat **both** outputs as unverified candidates graded by `kicad_verify.py`,
   `kicad_drc_connectivity.py` and `kicad_netlist.py`, never as trusted because a converter said so.

**Cost that must be carried.** Python 3.14 exactly; ~55 runtime dependencies in that venv; the output
is a KiCad **9** board (`KICAD_PCB_VERSION = 20241229`, `src/faebryk/core/zig/src/sexp/kicad/pcb.zig:12`)
even though KiCad 10 reads it; the Windows wheels are miscompiled with AVX-512 and crash on import
(upstream #1838); and the whole thing is a soft-deprecated line of a frozen repository, so pin the
sdist hash above and expect to be forked from day one.

**Not copied into this repo, and it cannot be.** The converter emits through the compiled Zig
s-expression layer (`faebryk.libs.kicad.fileformats.kicad`), so there is no subset of loose Python
files that would work. It is installed or it is not. This repo stays stdlib-only.

### Not adopted: A2 (the KiCad paths table) and A3 (the Zig schema as documentation)

A2's platform table is 25 lines of `match sys.platform` that this repo can write for itself when it
needs it; the one piece that was actually useful — `get_ipc_socket_path()` — is reimplemented inside
`scripts/kicad_open_probe.py` with attribution, and the rest was not needed. Lifting a table of
KiCad-9-pinned paths (`KICAD_VERSION = "9.0"`, `paths.py:14-15`) into a repo that already handles
KiCad 10 would import a defect for no gain.

A3 is documentation, not code, and needs no adoption decision: `pcb.zig` remains a useful free
cross-check when extending a parser, and saying so here is the whole of it.

---

## STEAL — four of the five landed, as three artefacts

The five items collapsed to four, and those four to three files, because **S1 and S3 are the same
mechanism**. A post-pick re-verification needs somewhere to read the picked values from; a
provenance-tagged variable report is exactly that place. Building them separately would have
produced a schema with no consumer and a checker with no input.

### S1 + S3 → `scripts/design_ledger.py` (+ `test_design_ledger.py`, 52 cases)

`LEDGERS` tier. Reads a JSON ledger of provenance-tagged variables and inequality constraints;
evaluates every constraint twice, over the design-intent intervals and over the intervals the chosen
parts actually deliver; gates on the second.

Taken from atopile:

| what | where in 0.15.8 | how it appears here |
|---|---|---|
| the `user \| derived \| picked \| datasheet` vocabulary | `src/faebryk/exporters/parameters/json_parameters.py:25` | the `source` field, verbatim |
| the `spec` / `actual` / `meetsSpec` triple | `json_parameters.py:29-37` | `spec` / `actual` / `meets`, same meaning |
| a second, *terminal* pass over the whole constraint system after the parts are chosen, whose failure is a hard error | `src/faebryk/libs/picker/picker.py:645-654` (`PickVerificationError`) | the as-built tier, gated |
| an error that carries the ORIGINS of both sides, not only its own message | `src/faebryk/core/solver/utils.py:65-102` (`Contradiction.__str__` walks the mutation map to the user's own expressions) | required `origin` on every variable and constraint, printed on both sides of every violation |
| the correlation rule | `src/faebryk/core/solver/README.md:118-121` | see S5 below |

Rejected, and the rejections are the reason this is a reimplementation:

* `meetsSpec` is hardcoded `None` on the only branch that sets it (`json_parameters.py:205`) and is
  never computed anywhere in the project. Here it is computed and tri-state.
* A parameter with no extractable superset is `continue`d out of the report entirely
  (`json_parameters.py:187-189`) — so an unconstrained value is *absent* rather than flagged. Here a
  missing value is `UNVERIFIED`, present in the report, and counted.
* A bare `except Exception: ... continue` (`json_parameters.py:217-219`) swallows every extraction
  failure into silent omission. There is no such branch here.
* atopile's asymmetry — over-constraining raises a hard `DslRichException`
  (`src/faebryk/libs/picker/api/picker_lib.py:168-185`) while under-constraining logs
  `logger.warning("has no constrained parameters")` and picks anyway (`picker_lib.py:163-165`) — is
  not reproduced. Both directions refuse here.
* No solver. The skill's derivations are explicit; what was worth stealing is the *discipline of a
  second pass* plus the origin-carrying error, and neither needs symbolic rewriting.

House additions that atopile has no equivalent of: `origin` is mandatory (a tag without a citation
cannot name a side of a contradiction, which is the whole point of tagging); the intent and as-built
tiers are reported separately because their failures have different owners; `UNVERIFIED` outranks
`FAIL`; a zero-constraint ledger refuses; the expression language is a whitelisted `ast` walk that
rejects calls, attributes, `**`, `==` and chained comparisons.

### S2 → `kicad_repro.py check_frozen` + the `frozen` verb (+ `test_kicad_repro.py`, 28 cases)

Shape taken from `src/atopile/build_steps.py:757-812` and `src/atopile/config.py:595`, `:619-639`:
a mode that fails if the artefact would change at all, writing both the original and the updated
file so the failure is inspectable rather than merely reported.

Three deliberate departures:

* **Exact bytes, no tolerance, ever.** atopile compares its parsed model at `float_precision=2`
  (`src/faebryk/libs/kicad/fileformats.py:543-550`), i.e. 0.01 mm, so a footprint displaced by 9 µm
  passes its frozen check. This repo's generators write exact integer nanometres and are proven
  byte-reproducible, so the reproducibility floor is genuinely zero. The consequence — a KiCad
  upgrade fails the gate — is stated in the docstring as the correct verdict rather than papered
  over. atopile's issue #1543 is what the other choice looks like in practice.
* **Prove the generator ran.** atopile's frozen mode compares two in-memory models within one build,
  so it cannot have this failure. `check_frozen` runs an external command, so it inherits this
  module's founding lesson: exit 0 is required, the mtime must move, and the output must be
  non-empty, because a generator that dies leaves the file untouched and byte-identical to a perfect
  pass.
* **Restore the tracked bytes.** atopile writes `.original` and `.updated` beside the layout and
  leaves the modified file in place. `check_frozen` copies the tracked bytes aside *first*, verifies
  the copy by digest before running anything, and puts them back — verifying the restore — on any
  failure, keeping the regenerated bytes as `<name>.regenerated`. Exit 3 means the artefact moved;
  exit 2 stays "the check could not be run".

`test_kicad_repro.py` also adds the calibration `run_and_check_reproducible` never had: it shipped
without a test file, and it is now imported by a release gate.

### S4 → `scripts/kicad_open_probe.py` (+ `test_kicad_open_probe.py`, 23 cases)

Reimplemented from `src/faebryk/libs/kicad/ipc.py:40-49` (socket enumeration) and
`src/faebryk/libs/kicad/paths.py:148-153` (socket directory). The unconditional `return True` in
`has_pending_changes` (`ipc.py:97-99`), sitting above its own dead implementation, is fail-closed and
is the right default; the posture is kept and the dead code is not.

Not taken: `enable_plugin_api()` (`ipc.py:30-37`) silently rewrites the user's `kicad_common.json` to
turn the API server on — a probe must not modify the machine it is probing to make itself work. Not
taken: the `kipy` conversation (a dependency). Not taken: `reload()` (`ipc.py:129-146`), which backs
up the editor's in-memory board and then reverts the client so the on-disk file wins; the intent is
sound but it discards a human's unsaved work, and in this repo that is the human's decision.

**Found something the evaluation missed, and it is the stronger signal.** atopile never reads
KiCad's lock file — `.lck` appears nowhere in its `src/`. KiCad writes `<dir>/~<basename>.lck` beside
any file it opens in an editor, which is *per-path* and needs no configuration, where the IPC socket
is path-blind without `kipy` and the API server is off by default. Measured on this machine on
2026-09-07 (KiCad 10.0.5), the lock content is exactly `{"hostname":"...","username":"..."}` — **no
pid, no timestamp** — so a lock left by a crashed KiCad is byte-identical to a live one. Four such
files were sitting on disk at the time of writing, one naming a hostname that is not this host.
HELD therefore means *refuse to write*, never *a human is there*, and the probe says so in its own
failure message.

The verdict is deliberately asymmetric and monotone: no branch reaches NOT-HELD without having
positively read the directory. The calibration caught a real instance of the failure the probe exists
to prevent — `Path.glob` on an unreadable directory yields nothing and raises nothing, so the
unlistable-socket-directory case came back as "no sockets", i.e. unevaluable input reading as a pass.
It uses `os.listdir` now, and the comment in the source says why.

### S5 → doctrine in `POWER.md` and `GUARDS.md`, made executable inside `design_ledger.py`

The rule from `src/faebryk/core/solver/README.md:118-121` — *"Singleton sets are self-correlated /
All other sets are uncorrelated with any other set (including themselves)"* — is quoted in
`POWER.md`, "A tolerance does not cancel against itself unless it is the same part", and is
implemented rather than merely described: `design_ledger.py` evaluates every constraint twice, once
with each occurrence of a variable independent (`eval_interval`) and once with one value per variable
shared across all its occurrences (`eval_corners`).

The verdict comes from the **uncorrelated** reading, always. It is an outer bound, so the gate can be
pessimistic but never kind, and a guard is allowed to be too strict. The correlated value is printed
beside it whenever a variable repeats, and a disagreement between them is escalated in the report as
a question about part identity that has to be answered in writing — the tighter number is never
adopted silently. The corner evaluation is a sampled extremum, exact only for expressions monotone in
each variable, so it is a diagnostic and can never be the verdict; above 14 variables it reports
itself unavailable rather than sampling thinly.

`Units.py`'s `BasisVector` was **not** lifted. The evaluation offered it as copyable, but this repo
has no dimensional-analysis need that a `unit` string on a ledger row does not cover, and importing a
basis-vector algebra to carry a label would be the wrong trade.

---

## Declined

**Nothing from the design-check framework.** The staged `POST_INSTANTIATION_GRAPH_CHECK → SETUP →
DESIGN_CHECK → POST_SOLVE → POST_PCB` pipeline (`src/faebryk/library/implements_design_check.py:21-26`)
and the `UnfulfilledCheckException` / `MaybeUnfulfilledCheckException` fail/warn pair (`:28-35`) are a
reasonable shape, but this repo already has a strictly better one: three named tiers with declared
subjects (`GUARDS.md`), a tri-state verdict where atopile has two, and a per-guard calibration
contract atopile has nowhere. Adopting the shape would be a downgrade.

**`Units.py`'s dimensional analysis.** Correct and impressive — angle and solid angle are in the
basis, so torque and energy are not commensurable (`Units.py:31-35`) — but the first `fabll.Node`-derived class starts at line 221, not immediately after 210, and everything from there
derives from `fabll.Node` and is welded to the type graph. Not liftable, and not needed.

**Layout reuse (`layout_sync.py`).** The idea of a proven sub-block as a reusable addressable unit
with routing attached is sound and this repo has no equivalent. The implementation is not the thing
to copy: rotation is unhandled (`:390`, `# TODO rotation?`; an earlier draft cited `:387`), the anchor is chosen by pad count
(`:365-369`) so adding a bigger part silently re-anchors the group, a missing net map returns net `0`
(`:487-491`) putting copper on the no-net net, and the "most frequent mapping" vote at `:218-229` is
`x > max(... including x ...)`, which is always false, so the first observation wins and is never
revised. This repo's route-manifest machinery already does the provenance job properly.

**A fifth new script for the correlation arithmetic.** Considered and rejected: the arithmetic has
exactly one consumer, and a standalone interval library with no caller is a thing to maintain rather
than a thing to use. It lives inside `design_ledger.py` where it is exercised on every run.

---

## What landed

| file | status |
|---|---|
| `scripts/design_ledger.py` | new — S1 + S3 |
| `scripts/test_design_ledger.py` | new — 52 cases |
| `scripts/kicad_open_probe.py` | new — S4 |
| `scripts/test_kicad_open_probe.py` | new — 23 cases |
| `scripts/kicad_repro.py` | `FrozenError`, `check_frozen()`, `frozen` verb — S2 |
| `scripts/test_kicad_repro.py` | new — 28 cases, including the calibration `run_and_check_reproducible` never had |
| `POWER.md` | new section: "A tolerance does not cancel against itself unless it is the same part" — S5 |
| `GUARDS.md` | three rules under threshold provenance, two checklist items |
| `RELEASE.md` | new subsection: "Re-grade the design arithmetic against the pinned parts" |
| `SKILL.md` | the running-KiCad check now names an executable probe and its tri-state contract |
| `scripts/README.md` | three helper-table rows and three usage sections |

Suite: 273 tests at the base commit, **386** after, all passing under both `/opt/homebrew/bin/python3` and `/usr/bin/python3` (6 skipped on the latter, where numpy/shapely are absent)
(`cd scripts && python3 -m unittest discover -p 'test_*.py'`). No third-party dependency was added;
every new script is stdlib-only.

## Not replicated

Every measurement in the ADOPT section is a **single session on one machine** — macOS 24.6.0 /
arm64, KiCad 10.0.5, Python 3.14.7, atopile 0.15.8 — against **two** Altium projects. Two boards is
not a corpus, and the tinyCurrent zone deficit (1 vs 4) is an open discrepancy, not a characterised
behaviour. Nothing here establishes how either converter behaves on a board with blind/buried vias,
rigid-flex, or an Altium version other than whatever wrote these two files.
