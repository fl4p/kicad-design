# Codex xhigh review of skill commits 0aefe5b..fca0890 (2026-09-06)

Run 2026-09-06 ~12:45–13:50 local, `codex exec` xhigh, network on, headed scratch Chrome attached over CDP (two `browser_navigate` calls confirmed in the run log). Prompt: eight numbered attack targets including a project-agnosticism check. Verdict: do not merge. All nine findings were verified and fixed in the commit that carries this file; corrections are recorded as "Review correction" notes in `2026-09-05-cross-session-routing-evidence.md`.

ACCESS

`curl -sI https://github.com/drandyhaas/KiCadRoutingTools | head -3` returned:

```text
HTTP/2 200
date: Sun, 06 Sep 2026 10:46:59 GMT
content-type: text/html; charset=utf-8
```

Playwright `document.title` for [route.py](https://github.com/drandyhaas/KiCadRoutingTools/blob/main/py_router/route.py):

```text
KiCadRoutingTools/py_router/route.py at main · drandyhaas/KiCadRoutingTools · GitHub
```

FINDINGS

1. **(d) `ROUTING.md:81`, `AUTOROUTING.md:49`, `GUARDS.md:68`, `PCB.md@fca0890:535` — requirement B is not met.** The literal refdes and `/NET_NAME` greps are clean, but substantial project measurements remain in the body: `ROUTING.md` lines 81–84, 149–180, 199–212, 244–256, 357–455, and 479–568; `AUTOROUTING.md` lines 49–75, 95–103, 128–133, 156–159, 184–190, 233–289, 304–320; `GUARDS.md` lines 68–72, 310–315, and 337–342; and committed `PCB.md` lines 535–570 and 576–591. These contain exact one-board dimensions, counts, deltas, timings, topology incidents, and thresholds. `AUTOROUTING.md:98` still names `+5VH` and `GND_OUT`, and `AUTOROUTING.md:305` retains the project file `krt_fab_floor.txt`. Date-stamped provenance also survives at `ROUTING.md:10,76,149,245,411,428,455,486`, `AUTOROUTING.md:209,233,269,304,313,320`, and `GUARDS.md:68`. Those are project residue, not reusable examples; the publication years, tool versions, source line numbers and portable calculation examples listed under SURVIVED are generic.

2. **(c) `ROUTING.md:422`, `AUTOROUTING.md:315` — `--keep-input-copper` is not re-runnable in the claimed preservation sense.** KRT does skip nets its own `filter_already_routed()` model considers connected and routes those it considers open, but that is not equivalent to re-attacking only KiCad DRC open items. The flag’s own help and `cleanup_pipeline.py` restrict its guarantee to subtractive/rewriting cleanup passes. Stub-layer swapping remains enabled by default and can mutate input segment layers; the experiment commands used only `--keep-input-copper`, without `--no-stub-layer-swap`. This directly conflicts with `ROUTING.md:203`, which correctly documents seven locked tracks moved between layers. The iterative rule must require the preservation flags and post-pass geometry/layer verification, and describe “open” as KRT-model connectivity unless a fresh KiCad oracle supplies it.

3. **(a) `reviews/2026-09-05-cross-session-routing-evidence.md:110` — the canonical-copper regrade is materially wrong.** Running the supplied canonical `verify_critical.py --expect-locked` against `codex_bom39` reports **14 of 57** authored items missing, not four `/CELL_WE` items: three `/GUARD_REPLICA` tracks plus nine `/CELL_WE` tracks and two `/CELL_WE` vias. The verifier exits 2. The stored DRC still supports 0 signal opens, 3 same-net zone-island items, and no copper-rule violations, but the provenance’s “same four” statement does not survive the required canonical verification.

4. **(a) `ROUTING.md:425`, `AUTOROUTING.md:316` — the pass chronology is composed from incompatible numbering schemes, and “plateau” is a false quotation.** The raw control sequence is `24, 19, 16, ...`; therefore the pass immediately after the two-stage 24 result is 19, not 16. Sixteen occurs after two additional invocations, or at the third *keep-input* invocation if the initial rip stage is silently excluded. Likewise, “eight” and “thirteen” count keep-input iterations while “two passes” counts the initial rip plus residue stages. More importantly, `RESULTS-BOM39.md:71-72` explicitly calls these finite sequences “best-of over passes, not a run-to-plateau.” Thus “all sat at 16–17 at plateau” and “at plateau nothing separated” at lines 427–430 are unsupported and contradict `ROUTING.md:440`, which says none of the experiments was re-run to plateau.

5. **(c) `ROUTING.md:444` — the signal/island split is overgeneralized and not operationally actionable.** The 16/36 versus 16/26 and 21/5 versus 21/5 results cover two saved candidates from one design, not multiple arbitrary designs, so they do not establish that signal opens are generally refill-invariant. The wording “opens between pads and tracks” also omits pad–pad, track–track and via-involving signal opens. Worse, the cited `split.py` recognizes an island only when every item description contains `Zone`; it consequently classifies stored `codex_bom39` as 1 signal/2 islands rather than 0/3, and `pi_b39clean` as 17/4 rather than 15/6 because zone–track, zone–via and via–track components are missed. A future agent has no correct generic classifier in the body.

6. **(c) `ROUTING.md:432`, `SKILL.md:393` — step 4 is not a valid general comparison rule.** One serial control trajectory from one seed is not an empirical noise distribution, and its min/max is not a statistical acceptance band. An arm can be consistently worse while its best merely touches the control’s worst value, yet the rule mandates “no separation.” Steps 1–3 are sound experiment hygiene; step 4 should instead forbid a winner claim from overlapping finite trajectories and require replicated or otherwise predeclared comparison evidence before asserting equivalence or separation.

7. **(c) `AUTOROUTING.md:315` — caveat 6 duplicates ROUTING.md but strengthens it into an unsupported absolute.** “Never A/B two variants on their two-pass numbers” is justified for this recipe and board, not for arbitrary boards. A predeclared two-pass budget can be adequate when independently shown to stabilize the relevant metric. This also conflicts with ROUTING.md’s more general instruction to declare a budget or stopping rule rather than universally prohibiting a particular budget.

8. **(c) `ROUTING.md:9`, `ROUTING.md:149`, `ROUTING.md:516` — anonymization left two experiment passages without adequate in-skill provenance.** The file says its routing-methodology review carries evidence for every numeric claim, but that review does not contain the escape table or the complete rotation experiment; those details live in the external `REROUTE-EXPERIMENTS.md`. The row-alignment passage remains traceable through cross-session evidence §5, but the stripped escape and rotation descriptions are anonymous and not fully falsifiable by following the body’s stated evidence link. Move the measurements into an appropriate `reviews/*.md` record and leave only the supported generic rule in the body.

9. **(a) `reviews/2026-09-05-cross-session-routing-evidence.md:116` — “dangling only” is not literally true for `pi_b39clean`.** Its stored DRC contains 29 `via_dangling`, 3 `track_dangling`, and **37 `track_not_centered_on_via`** records, in addition to cosmetic/footprint findings. The requested 15 signal/6 island split and 57/57 locked-canonical verification do survive.

SURVIVED

- The raw finite sequences support 22–46 after the staged two-invocation recipe, best observed values of 16–17, the control subsequence 16→22→17, and 46→17 at the eighth keep-input iteration. They do not support the “plateau” label or the mixed pass ordinals.

- “Keep the best board, not the last” is directly supported: the control’s best is 16 while its last is 17; `b39n1` reaches 17 but ends at 22.

- The retroactive downgrade is correct. `REROUTE-EXPERIMENTS.md` shows the baseline deltas used one or two passes, escape F/G/H used the fixed two-stage recipe, and rotation used the identical fixed two-stage recipe. The row-alignment archive contains `stage1.log` and `stage2.log`. `ROUTING.md:519` now limits rotation to “at that budget” and explicitly says it was not run to plateau; no uncaveated rotation superiority claim remains.

- AUTOROUTING caveat 5 survives source inspection: normal routed output and optional oracle staging call `apply_routed_floors`, early passthrough paths do not, non-Default netclasses are not clamped there, and `KICAD_INRUN_FLOOR_SYNC` is the controlling environment switch.

- The numeric refill observation survives: one saved board gives 16 signal with 36 versus 26 islands, and another gives 21/5 under both refill paths. Only the cross-design generalization fails.

- “Rank on signal opens; gate on total” is sound once a correct split is available. “Zero unconnected items” remains zero total unless the owner explicitly amends the acceptance criterion; the “in words” requirement correctly prevents an agent from silently rewriting the gate.

- The SKILL.md condensation retained the useful content from a985929: freeze acceptance criteria, separate ranking from completion, report every component, fail false/unevaluable required items, name sub-phases honestly, predeclare the common stopping rule, retain the sequence, and keep the best valid artifact. Its only material problem is the inherited control-range rule in finding 6.

- Stored regrade artifacts confirm `codex_bom39` at 0 signal/3 island/no copper-rule violations; `fable_b39base1` and `host_bom2` have the identical 24-signal net set despite differing island totals; and `pi_b39clean` is 15 signal/6 island. The canonical verifier confirms `pi_b39clean` has all 57 authored items present and locked.

- Project-identity grep found no `U\d`/`R\d`/`C\d`/`J\d` refdes, no true `/[A-Z_]+` net-name survivor, and no actual `codex`, `pi`, `fable`, or `host` session name. “host-side” at AUTOROUTING.md:98 is a domain adjective, although the adjacent exact net names are residue.

- Generic, reusable numeric/name examples that survive appropriately are the published routing citations and equations at `ROUTING.md:29-34,220,573-577`, KRT constants/source pins at `ROUTING.md:113-117,269-320` and `AUTOROUTING.md:225-231,292-303,306-312`, the portable leakage calculation at committed `PCB.md:505-524`, and the ADS1262 component example at `SKILL.md:153`.

UNVERIFIED

- Fresh `kicad-cli pcb drc` regeneration on `codex_bom39` and `pi_b39clean` could not be completed. Direct runs and `/tmp` copies aborted inside macOS AppKit/wx application initialization (`_RegisterApplication`/`NSScreen`; refill attempts reached `ZONE_FILLER_TOOL::FillAllZones`) without producing reports. The existing `drc.json` files were inspected, but their findings were not independently regenerated in this run.

VERDICT

do not merge
