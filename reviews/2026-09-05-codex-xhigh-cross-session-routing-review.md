# Codex xhigh review of 423ba27 (cross-session routing rules)

Run 2026-09-05 ~18:30–18:50 local, codex-cli 0.153.2, model_reasoning_effort xhigh, -s workspace-write with network, Playwright MCP attached to a headed scratch Chrome on 127.0.0.1:9412 (browser use confirmed: the GitHub route.py tab was open in that profile; no 'no connected browsers' in the run). Prompt: eight numbered attack targets on the commit. Extracted from the last ACCESS..VERDICT block of the session log; file:// wrappers stripped. All seven findings were verified against sources and fixed in the follow-up commit; see the provenance file's 'Review correction' notes.

# ACCESS

`curl -sI https://github.com/drandyhaas/KiCadRoutingTools | head -3` returned:

```text
HTTP/2 200
date: Sat, 05 Sep 2026 16:47:23 GMT
content-type: text/html; charset=utf-8
```

Playwright navigated to the requested [route.py page](https://github.com/drandyhaas/KiCadRoutingTools/blob/main/py_router/route.py) and read:

```text
KiCadRoutingTools/py_router/route.py at main · drandyhaas/KiCadRoutingTools · GitHub
```

Playwright control was first validated against the live `about:blank` DOM. The GitHub page supplied page identity only; the load-bearing inspection used local KRT commit `749cfa83` (`v0.21.3`). Repository status now reports untracked `.playwright-mcp/`; I did not alter or remove it under the read-only rule, and cannot establish whether it pre-existed the browser operation.

# FINDINGS

1. **(c)** ROUTING.md:199 (ROUTING.md:199) gives the unsafe categorical rule that locking “protects against rip-up,” immediately after ROUTING.md:195 (ROUTING.md:195) records that `--rip-existing-nets '*'` deleted locked geometry. Future agents could rely on precisely the protection this document says failed. Replace the heading with “Locked is not a preservation contract”: this KRT build has a measured rip path that deletes locked geometry and a stub-layer-swap path that moves it; require `--keep-input-copper --no-stub-layer-swap`, any needed smoothing control, and post-run geometry/layer verification.

2. **(c)** AUTOROUTING.md:294 (AUTOROUTING.md:294) overstates #650 as running after every saved output and at every stage. Normal file output calls `apply_routed_floors` at KRT `route.py:3701`, and its optional oracle staging calls it at `route.py:3151`, so one invocation can call it twice. Conversely, the no-valid-net and already-connected paths write passthrough outputs at `route.py:1146` and `route.py:1254` and return without calling it; undo and later repair writes also do not re-invoke it. AUTOROUTING.md:299 (AUTOROUTING.md:299) also says it prints one line, but the implementation prints a summary plus one detail line per change, exactly as the Pi log demonstrates. Finally, non-Default netclasses are deliberately not clamped because the call passes `clamp_nondefault_netclasses=False`. Narrow this rule to the normal routed-output/oracle paths and say “rule floors plus the Default netclass.”

3. **(a)** ROUTING.md:410 (ROUTING.md:410) and the provenance:65 (reviews/2026-09-05-cross-session-routing-evidence.md:65) splice two different Freerouting runs. The cited `freerouting-bare/workspace` log starts at 178 and records passes `51, 37, 34, 31, 25, 29, …, 16` at pass 16, `19` at pass 17, and `16` at pass 18—not `178 → 67 → 55 → 50 → 47 … → 16` at pass 17 and `19` at pass 18. The `67,55,50,51,47` prefix belongs to `freerouting-scout/workspace7`, which starts at 162. The same cited workspace also did import the SES successfully and run KiCad DRC, producing 42 unconnected pads; therefore “never imported, refilled or graded” is false for it. Use one run’s exact trace and distinguish live progress from the completed imported candidate.

4. **(c)** GUARDS.md:62 (GUARDS.md:62) conflates two failure mechanisms. An edited generator can self-certify its matching board, but a hand-edited board does not pass an unchanged generator merely because the verifier runs locally: Pi’s generator and verifier are byte-identical to the canonical copies, and running Pi’s local verifier locally failed on the same four `/CELL_WE` items. Thus GUARDS.md:65 (GUARDS.md:65) is inverted, while the provenance:48 (reviews/2026-09-05-cross-session-routing-evidence.md:48) supplies no evidence that Pi actually ran its verifier. Separate “pin immutable authority” from “make verifier execution mandatory.”

5. **(a)** ROUTING.md:70 (ROUTING.md:70) calls 1.75 mm a gap between pads, while the experiment scripts define it as row-slot/pad-centre pitch. More materially, ROUTING.md:75 (ROUTING.md:75) and the provenance:61 (reviews/2026-09-05-cross-session-routing-evidence.md:61) attribute a copper-clean `26 → 24` result to pad alignment. The actual `topology-locked-route` board has 24 unconnected items and two copper-edge violations; the later clean 24 result was the separate guard-door route. Keep the verified `173 → 161` seed result, but do not call the topology-only routed result copper-clean.

6. **(b)** the provenance:24 (reviews/2026-09-05-cross-session-routing-evidence.md:24) silently treats `final-best/` as the Codex session result. That specific snapshot does grade at 22, but `FINAL-RESULT.md`, written before this commit, identifies `final-best-21/` as the delivered best candidate with 21 unconnected items. Either regrade that final candidate or explicitly label the 22 board as an intermediate snapshot and explain the selection cutoff. The 30-versus-39 baseline lesson remains valid.

7. **(a)** GUARDS.md:68 (GUARDS.md:68) quotes “The authored skeleton is a design artefact” as the target, but the actual heading is “The authored skeleton is a design artefact, and the board cannot certify it” (ROUTING.md:234), and the link has no anchor. Use the exact heading or an anchor.

# SURVIVED

- The central #650 claim survives: `--no-fix-drc-settings` is consulted only for the later `fix_project_for_output` path at KRT `route.py:6281`; the in-run sync’s only configurable feature switch is `env_knobs.INRUN_FLOOR_SYNC`. Pi’s stage-1 command includes `--no-fix-drc-settings`, and its log contains the #650 summary followed by `rules.min_hole_clearance: 0.2 -> 0.15 mm`.

- “Copper floors only” is substantially correct when qualified: `apply_routed_floors` changes clearance/hole-to-copper rule floors and the Default netclass, leaving track, via, annular, and non-Default-netclass floors to final writeback. The `~3672` citation points to the explanatory block; the call is currently at line 3701.

- The locked-layer-swap observation survives. The first Codex run used `--keep-input-copper` but not the two disabling flags; its verifier reports seven missing locked F.Cu tracks, and all seven exact geometries remain locked on B.Cu. KRT’s stub switch mutates segment layers without checking `locked`; later stages pass `--no-stub-layer-swap --no-smoothing`.

- The stored four-way regrade reproduces exactly: host 25 with six hole-clearance findings; Codex 22 with no physical-copper findings; Pi 25 with two clearance and two copper-edge findings; host baseline 39 with four hole-clearance findings. All relevant `.kicad_pro` and `.kicad_dru` copies are byte-identical.

- The canonical critical-route verifier reproduces Codex’s 1/57 missing guard segment and Pi’s exact four `/CELL_WE` failures. Codex’s generator diff is correctly described as 228 changed lines: 221 additions plus seven deletions.

- The baseline rule survives. Host evidence records 30 when routed without same-stem project context and repeated 39 with it; the Codex transcript explicitly regraded the saved 30-open board rather than rerouting the baseline. The hash-and-restore prescription does not contradict the earlier warning against blind re-copy ritual.

- The pad-row experiment’s `173 → 161` seed result and the isolated `0.09/0.05 mm` “boxed in” probe are present in the supplied evidence. Only the 1.75-mm terminology and copper-clean routed attribution fail.

- The general rule that Freerouting incompletes are not KiCad `unconnected_items` survives; only its supporting trace and “never graded” characterization are wrong.

# UNVERIFIED

- A fresh independent KiCad DRC rerun: `kicad-cli 10.0.5` aborted with exit 134 before producing a report, including with temporary config/output locations. The supplied stored JSON reports were still inspected directly.

- Whether the Pi session historically invoked `verify_critical.py`: no invocation record or Pi transcript was available. The verifier’s behavior itself was checked locally and fails on the four changes.

- Runtime behavior with `KICAD_INRUN_FLOOR_SYNC=0` was not executed. Its source-level gate was verified.

# VERDICT

merge with listed edits
