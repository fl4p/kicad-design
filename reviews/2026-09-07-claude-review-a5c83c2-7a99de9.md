# Adversarial review of `a5c83c2..7a99de9` (pin-identity-rules fast-forward)

Reviewer: Claude Opus 5, 2026-09-07. Read-only review of the 16-commit range that local `master`
absorbed from `pin-identity-rules`. Scope requested: doc self-consistency, whether review findings
were actually applied, the two new scripts against `GUARDS.md`, claim hedging, and anything unsafe
in the range.

## Framing correction, first

**The range is already pushed.** `git ls-remote origin` returns `7a99de9` for `refs/heads/master`;
the local reflog records `origin/master@{2026-09-07 00:11:01 +0200}: update by push`. The premise
that `origin/master` is still at `a5c83c2` came from a stale `FETCH_HEAD` dated 2026-09-01. So
"fit to push" is moot — this is a post-hoc audit whose remedy is a follow-up commit, not a
withheld push.

The only genuinely unpushed state is the two subsumed branches `routing-cross-session` (ad95022)
and `routing-method-private` (0c1f78f), both confirmed ancestors of 7a99de9 and absent from the
remote.

## What was run

- 261 tests, `cd scripts && python3 -m unittest discover -p 'test_*.py'` → **OK** on
  `/opt/homebrew/bin/python3` 3.14.7. No venv or requirements file exists or is needed; both new
  scripts are stdlib-only and re-exec under KiCad's bundled Python for `pcbnew`.
- Full internal link/anchor sweep of all 29 tracked `.md`: **104/104 anchors and every relative
  file target resolve.** Zero broken links.
- Every `§N` citation in the docs resolves (the evidence file's §1–§12 are numbered list items;
  the research file's §1–§5 are headings; `SETUP.md §*Escalate through web defenses*` exists at
  `SETUP.md:250`).
- Adversarial probes against the real KiCad 10.0.5 `pcbnew`, plus direct reproduction of the
  `--jso` stale-pass defect and the argparse exit-code collision.

---

## FINDINGS

### 1. (c) blocker — the pin-identity review's 11 findings were never applied

`FOOTPRINTS.md:25,39,54,56,65,72` and `SETUP.md:323,339,348,353`.

`reviews/2026-09-01-codex-pin-identity-rules.md` lists **11 findings; none were applied.** The
exact text the reviewer disproved is still shipped verbatim:

- `FOOTPRINTS.md:39` states categorically "A package drawing that shows the pins as visible
  features on a solid body is a bottom view, and its left-to-right order reverses when
  transcribed." The reviewer refuted this against a primary source — Diodes DS30086 Rev 31-2 p.1
  labels a solid SOD123 with **both terminals visible** as "Top View". Following the rule mirrors
  that footprint wrongly.
- `FOOTPRINTS.md:25` "Never from prose" (F1) — over-broad; a 1N4148W's polarity is established by
  prose plus a marking drawing.
- `FOOTPRINTS.md:54` "three pads or more" (F3) — excludes polarized two-pad parts, whose terminals
  are not interchangeable.
- `FOOTPRINTS.md:56-57` the four `descr` fields "none can be written down without doing the work"
  (F4) — the self-certification sentence the reviewer called a false-confidence mute button.
- `FOOTPRINTS.md:65` "rotating an odd-pin-count inline part exchanges only its outer pins" (F10) —
  wrong for a 5-pin inline, where 180° maps 1↔5 *and* 2↔4.
- `SETUP.md:353-360` "its findings on that component are **void — discard them**" (F9) — destroys
  artefact-only findings (a DRC clearance violation at U1) that need no datasheet.

Commit `d0e3f8f`'s message discloses that F1, F2 and F4 are "unfixed here and flagged in the
report". **That disclosure lives only in the commit message; the documents carry no caveat**, and
F3, F5–F11 are not mentioned anywhere. Every other review file in `reviews/` has its findings
applied, so a reader will assume this one does too.

*Fix:* add a "Review correction" note at each of the five disproved passages — the pattern the
routing docs already use — citing `reviews/2026-09-01-codex-pin-identity-rules.md`, or apply the
eleven fixes.

### 2. (c) blocker — `--jso` leaves a stale `verdict: "pass"` report standing

`scripts/kicad_route_shape.py:521` (missing `allow_abbrev=False`).

Reproduced. With a pre-existing `{"tool":"kicad_route_shape","verdict":"pass",...}` file:

```
$ kicad_route_shape.py /nonexistent.kicad_pcb --jso stale.json --bogus-flag ; echo $?
2
$ shasum stale.json      # unchanged, still verdict "pass"
```

`--json` (unabbreviated) correctly overwrites it with `"verdict": "unevaluable"`. argparse accepts
the abbreviation, but the pre-parse invalidator at `:563-570` matches only literal
`--json`/`--json=`. This is precisely the stale-clean-report failure the pre-parse block exists to
prevent — the same code path whose earlier form destroyed a board under review. The sibling
`kicad_drc_connectivity.py:417` already sets `allow_abbrev=False` and carries a named regression
test (`test_abbreviated_json_option_is_rejected_without_leaving_stale_pass`).

*Fix:* `argparse.ArgumentParser(..., allow_abbrev=False)` in `build_parser()`.

### 3. (a) should-fix — two `scripts/README.md` claims are stronger than the code

`scripts/README.md:27`.

- "Each dilutable fraction has an undilutable absolute companion (`--max-full-stack-vias`,
  `--max-short-segments`)" — `--min-direction-conformance` is a *length* fraction with no
  companion (see the `add_argument` list, `kicad_route_shape.py:534-545`). Measured FAIL→PASS with
  the identical defect present: 20 mm of vertical copper on `F.Cu=h` alone gives
  `direction_conformance 0.048 vs limit 0.7`, exit 2; adding 200 mm of compliant horizontal copper
  gives `conformance 91%`, `ROUTE-SHAPE-OK`, exit 0.
- "the `--json` report named last on the command line is invalidated before argparse can reject
  it" — false under abbreviation, per finding 2.

*Fix:* add `--max-off-axis-mm` graded against `length_mm - h_mm - v_mm` per layer, or delete the
"each" and name the exception; and either fix the abbreviation or qualify the invalidation claim.

### 4. (c) should-fix — `--short-segment-mm` is an unguarded mute button

`scripts/kicad_route_shape.py:599-602`.

It defines the metric, not the threshold, and is validated only as finite and > 0. On a board of
100 × 0.05 mm segments: default → `short_segment_fraction 1.000 vs limit 0.15`,
`short_segments 100 vs 5`, exit 2; `--short-segment-mm 1e-9` → `below 1e-09 mm: 0%`,
`ROUTE-SHAPE-OK`, exit 0. The adjacent block at `:603-633` explicitly reasons that "a threshold
that can never fail … is a configuration error" but never applies that reasoning to the parameter
that makes the gate vacuous.

*Fix:* floor it at a manufacturable value (reject < 0.01 mm) and echo it in the OK line.

### 5. (c) should-fix — the DRC classifier's only gate ignores the split it exists to produce

`scripts/kicad_drc_connectivity.py:579-582`.

The tool's whole product is the signal-open / pour-topology split, but `--require-zero-total` gates
the aggregate. Verified: a report with real signal opens exits **0** with no flag (500 signal opens
→ exit 0), and any board with legitimate pour records can never use the flag. Its sibling closes
exactly this hole ("no threshold and no `--report-only` is unevaluable"). The docstring does say
"Exit 0 means the split was evaluable, not that the board is connected"; `scripts/README.md` does
not.

*Fix:* add `--require-zero-signal-opens`, and require one gate flag or an explicit report-only mode.

### 6. (a) should-fix — documented exit contract collides with argparse

`scripts/kicad_route_shape.py:55-62`.

The docstring documents "bad CLI value" as exit 1 and reserves exit 2 for "a graded metric failed
its threshold". Verified: `--max-vias-on-any-net notanumber` exits **2**, as does any unknown flag.
A CI wrapper reads a command-line typo as a board defect. The in-tree test
`test_last_json_is_the_one_invalidated` asserts `SystemExit` without checking the code, so this is
unobserved.

*Fix:* trap argparse's `SystemExit(2)` and re-emit as 1 with `ROUTE-SHAPE-UNEVALUABLE`, or correct
the docstring.

### 7. (c) should-fix — the route-shape JSON report is not bound to its artefact

`scripts/kicad_route_shape.py:141-147`.

The document records only `tool`, `verdict`, `board` (path), `pid`, `metrics` — no board digest, no
mtime, no `pcbnew` version, no timestamp. `GUARDS.md` requires binding a report to the artefact
digest it describes and confirming the report postdates the run. `kicad_drc_connectivity.py` does
this correctly (`source_sha256`, `source_size`, `drc_metadata.kicad_version`). A stale pass on a
since-edited board is indistinguishable from a fresh one.

*Fix:* hash the board bytes and record `pcbnew.GetBuildVersion()` and the interpreter used.

### 8. (b) should-fix — both new suites are model-tier only

`scripts/test_kicad_route_shape.py` (whole file), `scripts/test_kicad_drc_connectivity.py`.

`test_kicad_route_shape.py` never loads a `.kicad_pcb`: every case runs through
`FakePcbnew`/`FakeBoard`/`FakeTrack`, whose class hierarchy differs from pcbnew's
(`FakeArc(FakeTrack)`, `FakeVia` standalone), so the `isinstance(track, PCB_VIA)`-before-`PCB_ARC`
ordering, `GetEnabledLayers().CuStack()`, and `LoadBoard` parse failures are never exercised
against the real artefact. `test_kicad_drc_connectivity.py` uses hand-built dicts, never a
KiCad-exported DRC JSON. `GUARDS.md`'s `ARTIFACT_GUARDS` rule asks for a mutated scratch copy of the
emitted, reparsed artefact. Neither of the two mute paths above (findings 3, 4) has a known-bad
case.

*Fix:* commit one small real `.kicad_pcb` and one real DRC JSON as skip-if-no-pcbnew fixtures, plus
the two missing dilution/mute cases named as claims.

### 9. (a) should-fix — a review file's self-claim is false as written

`reviews/2026-09-06-codex-xhigh-skill-merge-review.md:3`.

"Verdict: do not merge. All nine findings were verified and fixed in the commit that carries this
file." Self-contradictory, and false: the next review in the same directory
(`…-skill-fix-review.md:23-39`) grades that same state as 4 FIXED / 5 PARTIAL. It became mostly
true only two commits later. **Findings 1 and 8 remain partial at HEAD** — both the same issue:
the board-specific measurements were *documented in `reviews/`* rather than removed from the skill
body (escape table still at `ROUTING.md:157-161`, rotation numbers at `:585-586`, the 4-layer via
census at `:551-557`).

*Fix:* restate as "fixed across 1348388 and 30994b8; findings 1 and 8 partially accepted", and say
why the body measurements stay.

### 10. (b) should-fix — a blocking finding is closed on an unrecorded waiver

`reviews/2026-09-06-codex-xhigh-skill-fix-review.md:23`.

Finding 1 (project-agnosticism) is closed on the grounds that "the remaining board-specific
measured numbers are covered by **the owner's stated allowance**." That allowance is recorded
nowhere in the repo — grep over `reviews/` and all docs finds only this sentence. Contrast the
CELL_WE waiver, which *is* quoted verbatim at
`reviews/2026-09-05-cross-session-routing-evidence.md:141`.

*Fix:* quote the owner's words, or reopen the finding.

### 11. (c) nit — a named helper that does not exist

`POWER.md:254`: "`loop_inductance_guard.py` gates `L`" names a script in monospace that is not in
`scripts/` and is mentioned nowhere else in the repo (introduced by `757436e`). Every other `.py`
the docs name either exists in `scripts/` or is plainly project-local.

*Fix:* write "the project's loop-inductance guard".

### 12. (a) nit — an unhedged invariance claim in the cited authority

`reviews/2026-09-05-cross-session-routing-evidence.md:122`: "Signal opens are refill-invariant,
islands are not." Stated flatly from n=2 candidates of one design. `ROUTING.md:511-513` hedges the
same result correctly ("not a proof of refill invariance on arbitrary designs"); the evidence file,
which is the cited authority, does not.

*Fix:* carry the same scope into §9.

### 13. (a) nit — internal tension on the 0603 land

`ROUTING.md:597`: "A flat 0603 land is 3.05 x 1.55" is stated as *the* 0603 land, while the bullet
eight lines below says two comparable 0603 lands on the same board declare 1.91 × 1.01 and
3.05 × 1.55.

*Fix:* "the 3.05 × 1.55 land used here".

### 14. (c) should-fix — untracked worktrees inside the repo, not ignored

`.claude/` is 4.0 MB, untracked, and absent from `.gitignore` (which contains only
`.pi-subagents/`). It holds two live git worktrees inside the repo; a `git add -A` would commit
them. The 2026-09-05 review also records an untracked `.playwright-mcp/` in this tree (absent now).

*Fix:* add `.claude/` and `.playwright-mcp/` to `.gitignore`.

### 15. (b) nit — the tip commit is the only unreviewed one

`7a99de9` ships a whole new 586-line guard (`scripts/kicad_drc_connectivity.py`), a new
`FOOTPRINTS.md` inventory rule, and two new doc sections, with no review file covering it.
Everything before it went through an xhigh pass. Findings 5 and 8 above are in that unreviewed
material.

*Fix:* run the same review loop on 7a99de9.

### Clean on the rest

No secrets, no API keys, no credentials, no binaries (largest added blob is `ROUTING.md` at 47 kB),
no `.playwright-mcp/`, no browser profile, no scratch files, no `TODO`/`FIXME`/conflict markers.
26 absolute `/Users/fab/...` paths appear, all inside `reviews/*.md` as evidence locators
(`/Users/fab/bin/serper-search`, `~/dev/ee/hw/o2-probe-*`) — a personal-directory-layout leak in a
public repo, no more than that.

---

## SURVIVED

- **All 7 findings of `reviews/2026-09-05-codex-xhigh-cross-session-routing-review.md` are applied
  at HEAD.**
  - F1 → `ROUTING.md:208-217`, "'Locked' is not a preservation contract", both mutation paths
    named, `--keep-input-copper --no-stub-layer-swap` prescribed.
  - F2 → `AUTOROUTING.md:294-315`, narrowed to the routed-output/oracle paths, both call sites
    (~3701 and ~3151), "summary line … plus one line per changed value", non-Default netclasses
    excluded.
  - F3 → `ROUTING.md:444-446` plus evidence §8's exact 20-pass trace; the "never graded" claim is
    gone and the 42 unconnected pads are stated.
  - F4 → `GUARDS.md:63-71` explicitly separates "pin the authority" from "make running the check
    mandatory" ("These are two separate failures").
  - F5 → the "1.75 mm gap" wording and the copper-clean 26→24 attribution are both gone;
    `ROUTING.md:73-79` says "one row pitch apart" and "Measured once, by one agent … not
    replicated".
  - F6 → evidence:31-32 lists `final-best/` at 22 as a mid-session snapshot *and* `final-best-21/`
    at 21 as the delivered board.
  - F7 → `GUARDS.md:71` quotes the exact full heading, which exists at `ROUTING.md:244`.
- **All 6 findings of the third review (`…-skill-fix-review.md`) are applied by `30994b8`** and not
  reverted by `7a99de9`, including the verbatim owner quote at evidence:141 with its internal quote
  marks and doubled period.
- **All 18 findings of `…-power-loop-doctrine.md` are applied,** and all 7 blocking findings of its
  verification pass are closed in `757436e`. Recomputed and confirmed: the 12 % P1/P2/P3 spread,
  8× area ratio, 13× di/dt, 9.75/9.50 dB and the 19/16 dB Fig. 11 differences, 42.7 %/74.6 %/80.6 %
  copper-thickness reductions, 3.44×/8.55 % mount ratios, 20.6 µm and 9.2 µm skin depths.
- **The router cost arithmetic in `ROUTING.md:282-288` is correct.** With `VIA_COST` 75 grid steps
  × 1000: 90° = 1/75, 45° = 1/150, break-even at 300 off-axis moves = 30 mm axial / 42 mm diagonal.
  The baseline census is consistent: 197/61 = 3.23 vias per routed net. The escape table correctly
  uses the G/H/F control rows (31/33/37, all `--keep-input-copper`, no project file) rather than
  splicing in the C row, and its per-stub costs (33−31)/21 = 0.095 and (37−31)/68 = 0.088 are right.
- **The convergence claims match the evidence.** "six to twelve further passes" and "best values
  16, 17, 17, 17" recompute exactly from evidence §8's four sequences; "a variant reading 46 … read
  17 on its seventh further pass" is correct for `b39no`; the refill delta "moved by ten" is 36→26,
  and signal counts 16/16 and 21/21 did hold.
- **Hedging discipline is genuinely strong in the new routing prose.** Repeated, specific, correct
  scoping: "Measured once, by one agent … not replicated"; "two nonzero treatments cannot establish
  that the relationship is linear"; "the inference — mine, not the source's"; "the thresholds above
  are review triggers, not validated limits"; "hypotheses to test on the board in front of you";
  "this file gives no aspect-ratio threshold, because none has been measured". No routing number is
  stated more strongly than its cited evidence, and no `§N` citation points at a section that does
  not exist.
- **The doc set is self-consistent.** 104/104 anchors resolve, no missing files, no missing
  scripts, every `## Contents` TOC complete. `SKILL.md:45,53` and `README.md:20,27` both announce
  `ROUTING.md` and `LOOPS.md` in the same relative positions. (Pre-existing, out of range:
  `SKILL.md`'s table omits `MODELS.md`.)
- **`kicad_route_shape.py`'s core fail-closed behaviour is real and calibrated,** verified by live
  probe against KiCad 10.0.5: empty / zero-track / truncated / directory-as-board all exit 1
  `ROUTE-SHAPE-UNEVALUABLE`, with "an unrouted board is unevaluable, not clean"; a threshold naming
  an unevaluable metric FAILs rather than skipping; `--report-only` writes `verdict: "reported"`,
  never `"pass"`; a gating run with no threshold is unevaluable; `BOARD --json BOARD` is refused
  before any write with the board intact; the placeholder is written before measurement; writes are
  atomic via `mkstemp` + `os.replace`; `_run_worker` refuses to trust a bare exit 0 without a
  verdict line; arc length is genuinely arc-aware (a 4 mm chord contributed 6.28 mm); the per-net
  **maximum** is monotone in via count and undiluted by via-free nets; the 2-layer full-stack
  carve-out reports unevaluable and then FAILs a threshold rather than passing it.
- **`kicad_drc_connectivity.py`'s parsing and provenance are sound:** the severity-filter attack is
  closed (`included_severities` must be exactly the full set), a report ignoring
  `unconnected_items` is rejected outright, every malformed record degrades to `ambiguous` (which
  forces exit 3 and `classification_evaluable: false`), `source_sha256`/`source_size` bind the exact
  bytes with a dev/ino/size/mtime_ns re-stat rejecting a mid-read change, `aggregate_gate` is forced
  to `unevaluable` whenever the classification is, `--json` aliasing the input is refused both pre-
  and post-parse, and the 199-cap heuristic fires at 199 and not 200.
- **No merge damage.** The one merge commit `2dccb52` is clean, and both subsumed branch tips
  (`ad95022`, `0c1f78f`) are confirmed ancestors of 7a99de9.

---

## VERDICT

**Push after listed edits — except it is already pushed, so: land a follow-up commit for findings
1–5.** The range is substantively good work with unusually disciplined evidence hedging and a real
review-and-fix chain that verifies end to end; but it ships one skill rule its own committed review
disproved against a primary source, with no caveat in the document (finding 1), and a guard whose
stale-clean-report protection is bypassable by a one-character CLI abbreviation (finding 2,
reproduced).


---

## Disposition, 2026-09-07 (added after an independent review of the fixes)

Findings 1-6, 11, 12, 13 and 14: **closed.**

**Finding 7 (report not bound to its artefact): PARTIAL, not closed.** The
report now carries the board's digest, taken either side of the measurement.
But `pcbnew.LoadBoard` is handed the pathname and opens the file a second
time, so the digest bounds the run without sealing it: a board swapped to B
and restored to A around the load is graded as B and reported under A's
digest. Demonstrated with a path-faithful LoadBoard double. Sealing it needs a
loader that reads from an open descriptor or from bytes, which the SWIG API
does not offer. The docstring and `scripts/README.md` now state the limit
instead of claiming the stronger property.

**Finding 8 (model-tier suites): PARTIAL, not closed.** The commit that
claimed to close it added no real `.kicad_pcb` and no real DRC-export fixture.
`FakePcbnew.LoadBoard` ignores the bytes it is given and returns a
preconstructed board, so `test_the_report_records_the_digest_of_the_bytes_it
_graded` verifies the file's digest while grading an unrelated fake. The new
tests are worth having and the four fake-path CLI tests are genuinely better,
but the finding asked for a reparsed artefact and that has not been done.

**Finding 15: still open.** Now partly addressed: `7a99de9` did get its review
(2026-09-07), which found the cap-monotonicity defect fixed in `a2624a4`, plus
four findings that remain open against `kicad_drc_connectivity.py` -- the
report binds the DRC JSON rather than the board, a declared pour net can still
launder a zone-bearing signal open, no test exercises a real KiCad export, and
the footprint inventory rule accepts model-tier evidence for an emitted-artefact
claim.

Recorded because a fix that closes six findings and quietly narrows two is the
same self-certification failure this file's findings 9 and 10 are about.
