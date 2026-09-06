# Cross-session routing evidence, 2026-09-05

Provenance for the rules added to `ROUTING.md`, `AUTOROUTING.md` and `GUARDS.md` in the commit
that carries this file. Five agents attacked the same brief (route the o2-probe, 20 × 107 mm,
113 footprints, 93 nets, on two layers to zero unconnected items) in parallel between 11:00 and
17:15 local: a Claude session that wrote the brief and `ROUTING.md` (`host-7695`), a Codex/GPT-5
session (`codex-7699`), a pi session on gemini-3.8-flash (`custom-7704`), and two further Claude
sessions. Nobody reached zero. What follows is what generalises, with who measured it and how far
I re-verified it. Full narrative: `~/dev/ee/hw/o2-probe-2layer-work/SESSION-ANALYSIS-2026-09-05.md`.

## Regrade under one harness

Each candidate copied to scratch, zones refilled with `pcbnew.ZONE_FILLER`, the canonical
`o2-probe.kicad_pro` / `.kicad_dru` (md5 `1b3814984628f69d9a377f7f18ed3125` /
`7f188440c76cfb7f96411cf96e4f0744`, byte-identical in every workspace) restored beside the board
*after* the save, `kicad-cli pcb drc --severity-all --format json`, then the project's
`verify_critical.py --expect-locked` run **from the canonical working copy** so it imports the
canonical `critical_routes.py`.

| candidate | unconnected | copper violations | authored critical copper vs canonical |
|---|---:|---|---|
| host baseline, no rotation (`rt-base`) | 39 | hole_clearance 4 | 57/57 present, locked |
| host, 19 passives rotated (`rt-rot`) | 25 | hole_clearance 6 | 57/57 present, locked |
| codex `final-best/o2-probe.kicad_pcb` (snapshot graded 17:20 local, while the session was still running) | 22 | none | 1 missing: `/GUARD_REPLICA` B.Cu 0.25 mm (102.17..110.885, 129.35) — codex edited `critical_routes.py` (228 diff lines: 221 added, 7 deleted) to open a 1 mm F.Cu doorway at x=7..8 for U7 pin 11 |
| codex `final-best-21/o2-probe.kicad_pcb` (written 18:24 local, after this commit's parent; the board `FINAL-RESULT.md` now names) | **21** | **none** | same single `/GUARD_REPLICA` change as above |
| pi `route-pi.kicad_pcb` (bg-12) | 25 | clearance 2, copper_edge_clearance 2 | 4 missing: `/CELL_WE` B.Cu trunk (110.0,132.3)→(110.4,131.9), (110.0,132.3)→(110.0,134.5), F.Cu (110.0,134.5)→(110.0,141.0), via (110.0,134.5) — pi's `critical_routes.py` is byte-identical to canonical, so the board copper was edited directly |

Self-reports against this regrade: host 25 ✔. Codex 22 then 21, copper-clean ✔, "all 154
authored items intact" ✔ *against its own modified generator*. The codex session was still
running when this file was first written; the 22 row is the state at 17:20 and the 21 row was
added after review. Pi reported "25, 2 clearance, 2 edge, 16
hole_clearance"; the regrade finds 0 hole_clearance — its counts do not reproduce and I did not
find out why.

## Items and their verification status

1. **KRT in-run project floor sync (#650).** Verified in source: `py_router/route.py` ~3672 calls
   `fix_kicad_drc_settings.apply_routed_floors(output_file, clearance=config.clearance, ...)`
   after every written output, gated only by `env_knobs.INRUN_FLOOR_SYNC`
   (`KICAD_INRUN_FLOOR_SYNC`, default on), not by `--no-fix-drc-settings` (that flag gates
   `main()`'s final writeback at ~6281). Observed: pi's stage-1 log line 1 shows
   `--fab-overrides krt_fab_floor.txt --no-fix-drc-settings` and line 1403 shows
   `In-run DRC floors (#650): lowered 1 value(s) in route-pi.kicad_pro` followed by
   `rules.min_hole_clearance: 0.2 -> 0.15 mm`. Codex observed the same and built
   `run_krt_staged.sh`: `shasum -a 256` of pro+dru before, `cp` back and `shasum -c` after each
   stage. Review correction: the call sits at ~3701 on the routed-output path and again at ~3151
   in the oracle staging (one invocation can write twice); the no-valid-nets and
   already-connected passthrough exits (~1146, ~1254) do not reach it; it clamps rule floors plus
   the Default netclass only (`clamp_nondefault_netclasses=False`); it prints a summary line plus
   one line per change.
2. **KRT layer-swaps locked stubs.** Codex observation (11:31: "layer-swapped seven locked
   F.Cu stubs onto B.Cu"), confirmed by the reviewer from the `escapes-c` experiment: its
   verifier lists seven missing locked F.Cu tracks and all seven exact geometries are present,
   still flagged locked, on B.Cu; the stub-swap code mutates the segment layer without consulting
   `locked`. I did not reproduce it myself. Flags verified to exist in `route.py`:
   `--no-stub-layer-swap` (5571), `--no-smoothing` (5594), alongside `--can-swap-to-top-layer`,
   `--swappable-nets`, `--mps-layer-swap`. Codex's runner passes `--keep-input-copper
   --no-stub-layer-swap --no-smoothing` on every stage.
3. **Authored copper self-certifies.** Verified by the regrade above: two of three candidates
   changed locked critical copper and neither change was visible to the candidate's own
   verification. `verify_critical.py` does `from critical_routes import CRITICAL_TRACKS` — the
   generator in its own directory — so a modified generator validates its own board.
4. **Same harness both sides of a comparison.** Codex regraded the saved `route-C` board (30
   opens, routed by host without a project file) instead of re-routing the baseline under its own
   project-present runner; host later measured the same recipe at 39 with the project present. All
   of codex's deltas are therefore understated by 9. Its own numbers: 30→31 (escapes), →26, →24,
   →22.
5. **Pad-aligned standing rows.** Codex measurement, single agent, not replicated by me: after
   consolidating B.Cu passives into standing rows, shared-net pads landed on opposite ends and
   "accepted foreign traces now form complete barriers between them" (12:25). Flipping alternate
   parts so shared pads face each other, ordering the CE divider as a chain, and pre-authoring the
   row-local links took the *unrouted* seed from 173 to 161 opens and the routed result from 26 to
   24. Review correction: that `topology-locked-route` board grades at 24 with two
   `copper_edge_clearance` findings; the copper-clean 24 is the later `guarddoor-route`, which
   also changed the guard copper. "1.75 mm" is the row pitch, centre to centre, from codex's
   `critical_routes.py` comment, not a gap. Its isolated fine-grid probe (0.09 mm / 0.05 mm grid)
   on the simplest adjacent-pad gap still reported "boxed in", which is what identifies the blocker
   as topology rather than resolution.
6. **Router incompletes are not unconnected items, on Freerouting too.** Codex's Freerouting 2.3.0
   scout on the bare placement, `experiments/freerouting-bare/workspace/router-logs/freerouting.log`,
   passes 1–20: 178, 51, 37, 34, 31, 25, 29, 23, 22, 21, 24, 21, 20, 17, 18, 18, 16, 19, 16, 16.
   Its SES was imported and the board graded: `candidate-drc.rpt` reports **42 unconnected pads**.
   Review correction: my first draft spliced the 67 → 55 → 50 → 47 prefix from a different run
   (`freerouting-scout/workspace7`, fixed critical skeleton, starting at 162) and wrongly said the
   bare run was never graded. The corrected pair, 16 live vs 42 graded on one board, is the
   stronger evidence for the rule.
7. **Selective GND stitch pruning.** Codex: removing the two stitching vias that terminated in
   isolated B.Cu fragments took 24 → 22; removing every stitch gave 23. Single measurement, kept
   as a hypothesis in the write-up, not promoted to a rule.

8. **A fixed pass budget is not convergence (measured 2026-09-06, fable session, reduced BOM: 100
   footprints, 85 nets).** Recipe C runs KRT's `--keep-input-copper` residue stage once. Re-run
   repeatedly on the same board (`iterate.sh`), signal opens per pass:
   `b39base` (untouched generator) 24 19 16 16 17 16 18 16 17 22 17 18 17; `b39c` (standing
   columns) 22 19 19 19 17 17 17 17 17 17; `b39no` (neck rewrite alone) 46 24 21 21 19 18 23 17;
   `b39n1` (both) 42 17 17 20 17 23 22. At two passes the variants span 22–46; at plateau all four
   sit at 16–17; the control's own band is 16–22. My regrade (ZONE_FILLER refill, canonical
   project, canonical verifier): fable `b39base1` 24 signal / 2 islands with the identical signal-net
   set to host's `rt-bom2` (same seed, same recipe, independent harness); `b39base3` and
   `b39base4` both 16 signal / 4 islands, copper clean apart from one `hole_clearance` inherited
   from the seed, 57/57 authored items present and locked. Fable's own write-up:
   `~/dev/ee/hw/o2-probe-2layer-fable-b39/RESULTS-BOM39.md`. Consequence recorded in `ROUTING.md`:
   every two-pass A/B in the file (escapes, rotation, row alignment, baseline deltas) is a
   fixed-budget reading, not a plateau result.
9. **Signal opens vs pour islands (measured 2026-09-05, fable session).** One Freerouting board
   graded 16 signal / 36 islands / 52 total under `pcbnew.ZONE_FILLER` and 16 / 26 / 42 under
   `kicad-cli --refill-zones`; another 21 / 5 / 26 under both. Signal opens are refill-invariant,
   islands are not. Codex's `final-zero-signal` (113 footprints) then reached 0 signal opens with
   43 islands (my harness: 47) and reported the task complete; its later `final-bom39-zero`
   (100 footprints, 2026-09-06 01:34) grades in my harness at **0 signal / 3 islands / no copper
   violations** (0 / 0 / 0 under codex's `kicad-cli` refill), with the same four canonical
   `/CELL_WE` items missing as before (generator byte-identical to `final-zero-signal`'s), plus
   declared hand edits: J105/J106 swapped to no-relief wire footprints, C34 moved 0.8 mm, three
   power nets hand-detoured. It is the only board that meets the brief's literal gate, and it does
   so with a rewritten electrometer topology that is the owner's decision. Pi's `b39clean-best`
   (00:28): 15 signal / 6 islands, dangling only, 57/57 canonical items intact — its first board
   to pass the canonical verifier. Host's `rt-bomrot2` (two passes, 19 rotated): 20 / 5, 57/57.

## Not promoted

- Pi's placement ideas (U8 rotated 270° to face U7; WE_BUF filter R37/C30 moved under U8 pin 5
  removing a 10.5 mm ratsnest; C10/R41 swap to uncross VREF_SHUNT/VREF_TRIM). The board is not
  copper-clean, locked copper was edited on the board, and its DRC counts do not reproduce.
- Pi's claim that 11 escape stubs on U7/U8 improved 28 → 25, contradicting three other sessions.
  Same caveats.
- Codex's "four layers is the credible next step". It was withdrawn by its author twenty minutes
  later when Freerouting had not been tried.
