# Codex xhigh review of skill fix commits fca0890..6fa56b9 (2026-09-06)

Run 2026-09-06 ~14:30–15:00 local, `codex exec` xhigh, network on, headed scratch Chrome attached over CDP (browser_navigate confirmed in the run log). Verdict: merge with listed edits. Six findings, all fixed in the commit that carries this file.

ACCESS

`curl -sI https://github.com/drandyhaas/KiCadRoutingTools | head -3`:

```text
HTTP/2 200
date: Sun, 06 Sep 2026 11:11:48 GMT
content-type: text/html; charset=utf-8
```

Playwright `<title>`:

```text
KiCadRoutingTools/py_router/route.py at main · drandyhaas/KiCadRoutingTools · GitHub
```

FIXES

1. FIXED — Body grep found no surviving project refdes, private net names, session names, `+5VH`, `GND_OUT`, or `krt_fab_floor.txt`; the remaining board-specific measured numbers are covered by the owner’s stated allowance.

2. PARTIAL — KRT source confirms cleanup-only protection, default-on stub swapping, and KRT-model connectivity, but AUTOROUTING attaches the measured sequence to the newly prescribed two-flag command even though the experiment used only `--keep-input-copper`.

3. FIXED — The canonical verifier reported exactly 14/57 missing on `final-bom39-zero`: nine CELL_WE tracks, two CELL_WE vias, and three GUARD_REPLICA tracks. The two copied generators are byte-identical, and both boards have identical CELL_WE/GUARD_REPLICA segment and via sets.

4. PARTIAL — “19 and 16,” “seventh further pass,” and `16 → 22 → 17` are correct; “seven to thirteen further passes” remains off by one.

5. FIXED — The three-bucket split is generic and operational, explicitly limits refill invariance to two candidates of one design, and matches the stored DRC records.

6. PARTIAL — The invalid equivalence claim was removed, but serial pass ranges are still treated as a separation test and replication is now required too absolutely for deterministic, fixed-input comparisons.

7. PARTIAL — AUTOROUTING’s “scout unless stabilized” caveat is sound, but ROUTING retains the unqualified assertion that every two-pass A/B is a scout.

8. PARTIAL — The header now points to §10/§11 and §11 faithfully preserves the rotation record; §10 contains a composed-condition table and an incorrect “three other sessions” summary.

9. FIXED — `pi_b39clean` contains exactly 29 `via_dangling`, 3 `track_dangling`, and 37 `track_not_centered_on_via` violations.

FINDINGS

1. (e) `ROUTING.md:446`, `ROUTING.md:456`, `SKILL.md:394` — Prior finding 6 is not fully fixed. An arm’s best falling within a control’s serial min/max still becomes a “has not shown separation” test, although those pass values are timepoints, not an empirical noise distribution. The blanket requirement for replication also forbids the valid scoped statement that deterministic A scored X and B scored Y on this exact board and predeclared budget. This conflicts directly with ROUTING’s own finding that this router produced identical copper on repeated runs at `ROUTING.md:401`; non-monotonicity is not non-determinism. Require replication for generalization or equivalence, while permitting explicitly scoped deterministic results.

2. (e) `ROUTING.md:425`, `AUTOROUTING.md:323` — Prior finding 7 remains internally inconsistent. AUTOROUTING correctly allows a fixed small budget after it has been shown to stabilize the metric on the board in hand, but ROUTING still states without qualification that “a two-pass A/B is a scout.” The ROUTING heading needs the same independent-stabilization exception.

3. (a) `ROUTING.md:432`, `reviews/2026-09-05-cross-session-routing-evidence.md:108` — “Seven to thirteen further passes” is still an off-by-one error. Treating each sequence’s first number as the completed two-stage recipe leaves 12 further passes for `b39base`, 9 for `b39c`, 7 for `b39no`, and 6 for `b39n1`: the range is six to twelve. Seven to thirteen counts the recipe’s own residue pass while simultaneously calling all passes “further.”

4. (a) `AUTOROUTING.md:320`, `reviews/2026-09-05-cross-session-routing-evidence.md:105` — Caveat 6 presents the 24→19→16 and 46→17 measurements immediately after specifying `--keep-input-copper --no-stub-layer-swap`, but the evidence explicitly says `iterate.sh` did not pass `--no-stub-layer-swap`. The connectivity-selection statement is supported by source, but the numerical trajectory under the newly prescribed command is unmeasured. Separate the source-derived prescription from the keep-only measurement.

5. (a) `reviews/2026-09-05-cross-session-routing-evidence.md:151`, `reviews/2026-09-05-cross-session-routing-evidence.md:161` — §10 is not wholly faithful. “Identical two-stage recipe throughout” sits over a C row containing both the no-project result 30 and a separate project-present rerun of 39, while G/H/F’s 31/33/37 measurements were all made without the project. That condition should be explicit. It then says three “other sessions” refuted escapes while listing only Codex and Fable as worsening and Pi as a contrary improvement. The accurate summary is three negative sessions total—host, Codex, Fable—and one unpromoted contrary Pi result.

6. (a) `reviews/2026-09-05-cross-session-routing-evidence.md:136` — The owner waiver is substantively valid, but its purported direct quotation is normalized inaccurately. The transcript says `i never put these "CELL_WE and guard" rules, i dont care. if it works..`; the evidence removes the internal quotation marks and one period. Quote it exactly or present it as a paraphrase. In context, it does satisfy ROUTING’s requirement for an amendment “in words.”

SURVIVED

- KRT HEAD is `749cfa83`. `route.py:5597–5605` limits `--keep-input-copper` to cleanup rewrites; `:5571–5572` and `:6190` confirm stub swapping is enabled by default; `filter_already_routed()` supplies KRT’s own connectivity decision. Caveat 6 neither duplicates caveat 5 nor contradicts the locked-copper warning.

- The specific pass ordinals hold: 24→19→16 is two further passes, 46→17 occurs on the seventh further pass, and `16 → 22 → 17` is a valid control subsequence. Every current use of “plateau” negates or recounts the rejected claim; “not a converged plateau” is acceptable.

- The verifier’s 14/57 breakdown is exact. Both final-board generators have SHA-256 `61c2a869…`, their relevant CELL_WE/GUARD_REPLICA copper sets are identical, and the stated 301-line zero-context `+`/`-` diff count against the canonical generator is reproducible.

- Stored `codex_bom39/drc.json` contains two zone–zone GND records and one zone–track GND record. `pi_b39clean` gives 17/4 under the zone-only classifier and 15/6 under the net-based classifier. The body’s two-candidate hedge is sufficient.

- ROUTING’s evidence header correctly assigns escape and rotation to cross-session evidence §10/§11. Rotation’s 19/24 parts, exclusions, 0.68 mm displacement, 173 seed count, 39→25 result, 2→12 lanes, hashes, and reduced-BOM 24→21 result match `REROUTE-EXPERIMENTS.md`.

- No project-identity residue survived in ROUTING.md, AUTOROUTING.md, GUARDS.md, or SKILL.md. Generic placeholders such as `board.kicad_pcb`, `GND`, and `<floor file>` are not residue.

- The model timeline is accurate: Fable-model messages run from 11:08:33–13:12:03 UTC, or 13:08–15:12 local; the first Opus message is 13:45:45 UTC, or 15:45 local. Freerouting, `split.py`, and `iterate.sh` work appears under Opus. The second cited transcript is Opus throughout.

- The owner’s statement followed an explicit explanation of the 14 missing CELL_WE/guard items and the refill-path difference, so the waiver’s meaning—notwithstanding the quotation error—is accurately characterized.

UNVERIFIED

- Fresh DRC regeneration was not attempted. Per the run constraint, the stored `drc.json` files were used after the single KiCad-Python attempt was spent successfully on the canonical verifier.

- The canonical verifier was not invoked separately on `final-zero-signal`; its specific 14-item result was instead checked through byte-identical generators and identical CELL_WE/GUARD_REPLICA copper sets.

- The revised iterative command with both `--keep-input-copper` and `--no-stub-layer-swap` has no measured convergence trace; only the original keep-only trace exists.

VERDICT

merge with listed edits
