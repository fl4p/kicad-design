## ACCESS

- Live query: `curl -sS -L https://example.com` → `<title>Example Domain</title>`.
- Attached Playwright browser: FastHenry2 → `<title>GitHub - ediloren/FastHenry2: FastHenry is the premium inductance solver originally developed at M.I.T. on Unix platform. A de-facto golden reference standard, FastHenry extracts the inductances and resistances of any arbitrary 3D conductive geometry by solving the Maxwell equations in quasi-static regime. For more information, visit our website. · GitHub</title>`.
- Initial status was `M PCB.md`, `M scripts/README.md`, `?? scripts/copper_guards.py`. A concurrent `scripts/loop_inductance_guard.py` later appeared and was excluded. Playwright’s transient snapshot was removed; no reviewed file was changed.

## FINDINGS

1. **(c) The resistance gate is optional at the executable boundary.** `--max-mohm` is not required; omitting it prints a resistance and exits 0, exactly as the incident’s 185.916 mΩ measurement did. Successful gated output also omits the limit and margin, contrary to the repository’s threshold-reporting contract. PCB.md:128:128, copper_guards.py:454:454, copper_guards.py:480:480, GUARDS.md:332:332. Impact: an automated completion pipeline can accept a measurement-only invocation as a passed gate.

2. **(c) Gate parameters are not domain-validated.** Runtime probes showed `--max-mohm nan`, `--oz nan`, and `--oz -2` returning 0; `--min-zone-frac 0` accepted zero contact. `--oz` also silently defaults to 2, halving resistance on a 1 oz board if omitted. copper_guards.py:442:442, copper_guards.py:451:451, copper_guards.py:483:483. Impact: malformed or missing configuration can produce an exit-0 false pass.

3. **(c) `vias` is vacuously fail-open.** An empty file and a misspelled `--net` both returned `vias checked: 0, FAIL rows: 0` with exit 0, contradicting “an unevaluable run is a failed gate” and the repository rule that zero candidates is never PASS. PCB.md:130:130, copper_guards.py:462:462, copper_guards.py:469:469, GUARDS.md:74:74. Impact: a typo or failed parse silently closes the via gate.

4. **(c) Zone-fill validation is board-global and cannot establish freshness.** One unrelated `filled_polygon` sets `any_fill`; a synthetic target net with an empty declared zone then returned a plausible resistance and exit 0. Existing but stale fills are indistinguishable from current fills. copper_guards.py:108:108, copper_guards.py:176:176, copper_guards.py:314:314. Impact: stale or partially unfilled power copper can be graded as real copper.

5. **(c) “Covering pad” is not what `vias` tests.** The implementation merely checks whether a same-net pad contains the via center; a synthetic 0.1 mm pad entirely inside a 0.5 mm drill passed a 1.0 mm via despite zero annular-ring contact. scripts/README.md:29:29, copper_guards.py:225:225. Impact: a physically contactless annulus can receive PASS.

6. **(c) Scope and terminal coverage have no authoritative manifest.** “Declared” is undefined, no expected net/pair inventory is bound, identical endpoints pass, and duplicate `REF.PAD` instances are resolved by taking the first match. The incident board itself has two physical `F1.2` pads, while multi-terminal power nets have many unchecked branches. PCB.md:123:123, PCB.md:127:127, PCB.md:153:153, copper_guards.py:317:317, inverter.kicad_pcb:27667:27667, inverter.kicad_pcb:27676:27676. Impact: an agent can omit a power net, choose a convenient pair, or leave capacitor/load branches ungraded.

7. **(c) The parser violates the skill’s generic-net and stackup contracts.** It strips root-sheet `/` from net names, hardcodes only `F.Cu`/`B.Cu`, and applies one copper weight to both layers—despite explicit instructions not to normalize `/` and to reject hardcoded layer tuples without asserting the stack. copper_guards.py:40:40, copper_guards.py:43:43, copper_guards.py:331:331, GUARDS.md:65:65, SKILL.md:116:116. Impact: distinct nets may be conflated, while inner-layer or mixed-weight boards are miscomputed or unclosable.

8. **(c) “Rasterized real copper” materially overstates the model.** Roundrect, trapezoid, and custom pads become rectangles; routed arcs and copper graphics are omitted; every via and through-hole transition is a fixed 0.2 mΩ independent of barrel length, drill, and plating. scripts/README.md:29:29, copper_guards.py:93:93, copper_guards.py:150:150, copper_guards.py:308:308, copper_guards.py:367:367. Impact: fictional pad copper can lower resistance, omitted arcs can break valid paths, and via-heavy paths can be optimistically wrong by mΩ.

9. **(c) The required calibration evidence does not exist where claimed.** The README says the 0.02/0.07 failures and 66-via clean run are recorded in the tool docstring, but the docstring contains neither record and no test/self-test covers the new negative claims; the README’s argparse-CLI list also omits this argparse CLI. scripts/README.md:29:29, scripts/README.md:39:39, copper_guards.py:2:2, GUARDS.md:110:110. Impact: the standing fail-closed claims have no regression protection and their cited audit trail is false.

10. **(c) The incident says “0 DRC violations,” but the transcript recorded 230.** The final declaration narrowed that to zero unconnected, clearance, and shorting errors; the 230 findings were described as cosmetic silk warnings. PCB.md:136:136, incident transcript:706:706, incident transcript:707:707. Impact: the motivating record should say “zero completion-critical/electrical DRC findings,” not literally zero violations.

## SURVIVED

- CLI spellings and arity are correct: `resistance`, `vias`, `--net`, two-value `--pair`, `--oz`, `--max-mohm`, and KRT’s `--power-nets`/`--power-nets-widths` all match live help. KRT validates unequal nonempty lists. Its default may neck power taps to the layer width, but the prose correctly assigns final authority to measured emitted copper.
- Under valid inputs, missing pads exit 1, wrong-net pads exit 1, off-grid terminals exit 1, disconnected pairs print `INFINITE` and exit 2, and a board whose declared zones all lack fills exits 1.
- The default via threshold is 0.60 and below-threshold zone contact exits 2.
- Incident fidelity otherwise holds: the original route used global 0.4 mm, the original bus zone was y=42…76 while FET pads were at y=22, and `/AC_N` `Q1.3 ↔ J_AC1.2` measured 185.916 mΩ. \(10.7^2 \times 0.185916 = 21.286\) W, so there is no arithmetic error (a). incident transcript:675:675, incident transcript:677:677, incident transcript:717:717.
- Grid resolution is not the incident’s dominant error: a synthetic 100 mm × 0.4 mm trace changed only 0.17% between 0.2 and 0.05 mm grids; the current complex `/AC_N` path changed 1.37%. A convergence margin should nevertheless be part of the threshold evidence.
- The DC scope is honest. At 23.4 kHz, copper skin depth is about 0.432 mm, over six times 70 µm 2 oz foil, so skin effect alone does not invalidate the DC baseline. The solver still does not establish proximity, switching-harmonic, or thermal performance.
- No contradiction exists with the collision guard’s zone-fill exclusion. Drafts may skip the completion-only guard provided they remain explicitly drafts, and fabrication release transitively inherits the Completed-PCB gate through `RELEASE.md`. The “route-readiness section” reference resolves clearly to “Prove placement is route-ready and define completion.”

## VERDICT

Rework before landing. The prose points in the right direction, but it binds a safety-critical completion gate to an executable that is optional, vacuous on important paths, unable to establish fill freshness or complete scope, and physically incomplete for generic KiCad boards. Require finite positive parameters and mandatory thresholds, bind an authoritative net/terminal inventory and derivation record, reject ambiguous or zero-subject runs, consume semantically finalized per-zone copper and actual layer stackup, model all supported copper objects and via geometry, and add the mandatory adversarial calibration suite before making this a standing completion gate.
