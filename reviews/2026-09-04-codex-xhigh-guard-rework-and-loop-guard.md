ACCESS

- Initial state: `M PCB.md`, `M scripts/README.md`, plus untracked prior review, `copper_guards.py`, and `loop_inductance_guard.py`. `git diff --check` passed; final status was unchanged.
- Live query: `serper-search --json "site:docs.kicad.org KiCad documentation"` returned Documentation | KiCad as result 1.
- Attached-browser `<title>`: **unverified**. The requested browser runtime returned `No browser is available`, and browser discovery returned `[]`; I did not substitute another browser.

FIX-VERIFICATION

1. **CLOSED.** `--max-mohm` is required at scripts/copper_guards.py:594:594; omission exited 2, while a successful run reported verdict, limit, and margin at scripts/copper_guards.py:640:640.

2. **NOT CLOSED.** NaN, infinity, zero, negative, and bounded-domain checks now refuse correctly at scripts/copper_guards.py:601:601. However, `--oz` still silently defaults to 2.0 at scripts/copper_guards.py:587:587; omitting it produced an exit-0 19.389 mΩ verdict, so the missing-stackup-input path remains.

3. **CLOSED.** An empty file and misspelled net both refused; zero selected vias is also rejected at scripts/copper_guards.py:365:365.

4. **CLOSED for the reported resistance probe.** A target net with an unfilled declared zone and an unrelated filled zone now refuses per net/layer at scripts/copper_guards.py:378:378. The analogous `vias` loophole remains as new finding 2 below.

5. **REGRESSION.** The sub-drill-size centered-pad probe now correctly fails because real ring overlap is measured at scripts/copper_guards.py:327:327. But the new semantics flag Kimi’s `/GND` via at `(114,44)` as 0.477 contact even though the actual B.Cu fill is a centered 0.499 mm thermal bridge matching the declared `thermal_bridge_width 0.5`; no pad contributed to the union. An equivalent 0.5 mm track would automatically pass, making the verdict representation-dependent.

6. **NOT CLOSED.** Self-pairs are refused and duplicate `REF.PAD` lands are correctly tied into one supernode; a constructed duplicate-land case solved finite at 2.273 mΩ. The prose now demands every source-to-load branch at PCB.md:126:126, but the executable still accepts only one free-form net/pair and has no expected manifest against which omissions can be detected at scripts/copper_guards.py:581:581.

7. **NOT CLOSED.** Exact net names and the 2-layer refusal work; `/AC_N` passed and `AC_N` refused with a leading-slash hint. No documented repository workflow depended on normalization. Mixed-weight 2-layer boards remain unsupported but are not refused: one `--oz` value is applied to both layers at scripts/copper_guards.py:464:464.

8. **NOT CLOSED.** The new three-point-arc unwrap survived constructed CW and CCW 20°, 180°, and 340° sweeps. Nevertheless, arbitrary custom/trapezoid/roundrect pads still become bounding rectangles and every transition defaults to 0.2 mΩ at scripts/copper_guards.py:32:32. Disclosure does not prevent optimistic resistance from fictional copper or underestimated via resistance.

9. **NOT CLOSED.** The docstring now records calibration and the README CLI list is repaired. Live evidence confirms the stated 0.02/0.07 Kimi failures and 66-via Qwen clean result. There is still no in-repository fixture or self-test, so the durable regression-protection part of the finding remains absent; scripts/copper_guards.py:42:42 is only prose.

10. **CLOSED.** The incident now says “zero electrical DRC findings (cosmetic silk findings open)” at PCB.md:144:144, matching the archived transcript rather than claiming literally zero violations.

FINDINGS

1. **(c) Thermal-relief vias can false-FAIL based only on object encoding.** The Kimi 0.48 case is a centered 0.5 mm zone thermal bridge, but scripts/copper_guards.py:336:336 exempts geometrically equivalent tracks while fraction-grading filled spokes. Impact: normal filled thermal connections can block completion while an equivalent track passes.

2. **(c) `vias` still accepts an unfilled declared zone on the graded net.** Its only fill precondition is board-global at scripts/copper_guards.py:309:309; a constructed target net with an empty zone, unrelated fill, and a center track returned PASS. Impact: the README’s “both fail closed” fill claim at scripts/README.md:29:29 is false.

3. **(c) Non-finite budgets and invalid extracted quantities can produce PASS.** Budget validation checks only `<= 0` at scripts/loop_inductance_guard.py:87:87, while extracted values receive only a Python numeric-type check at scripts/loop_inductance_guard.py:113:113. Live probes gave exit 0 for `L_loop=inf` budget and for SHA-matched JSON containing `L_loop=-1e-9` or `-Infinity`. Impact: malformed or forged inputs can close the safety gate.

4. **(c) The documented 0/2/3 exit contract is not maintained.** Missing `--max-nh` exits 2 through argparse at scripts/loop_inductance_guard.py:160:160, and a valid JSON array crashes with exit 1 at scripts/loop_inductance_guard.py:191:191. Impact: automation cannot reliably distinguish electrical FAIL from unevaluable or malformed input.

5. **(c) A successful extractor process can gate a pre-existing stale JSON.** After subprocess exit 0, the adapter checks only whether `OUT/parasitics.json` exists at scripts/loop_inductance_guard.py:139:139. A stub that wrote nothing exited 0 and the guard passed a pre-existing 1 nH file. Impact: an incomplete/current extraction can inherit a prior PASS.

6. **(c) Documented relative paths break under the extractor `cwd`.** The adapter validates paths in the caller directory, then passes them unchanged while changing cwd to the extractor root at scripts/loop_inductance_guard.py:134:134. The documented relative `BOARD`, `OUTDIR`, and config usage failed live. Impact: normal documented invocations become unevaluable and can interact with stale relative output directories.

7. **(c) Board-hash checking does not bind extraction configuration, loop identity, or output authenticity.** The reuse branch ignores `--config`, never checks `meta.extract_config_sha256`, and trusts the JSON’s self-asserted board hash at scripts/loop_inductance_guard.py:175:175. Supplying a nonexistent `--config` with the real reused JSON still passed. Impact: a stale, edited, or wrong-net/wrong-probe extraction for the same board bytes can obtain PASS.

8. **(b) Snubber and ringing budgets are silently evaluated using plateau L rather than ring-frequency L.** Probe lookup selects `probe_ports[].L` at scripts/loop_inductance_guard.py:112:112, and `L_loop_ring` is not an accepted scalar key, although the extractor emits both `L` and `L_ring`. The calibration happens to agree closely—28.733 versus 28.729 nH—but this assumption is undocumented. Impact: a frequency-sensitive snubber loop can be compared against the wrong quantity.

9. **(c) The third backstop is optional by never deriving a budget.** PCB.md:153:153 requires the guard only after a catalog or analysis already assigns a numeric limit, and PCB.md:163:163 permits an unspecified “inapplicability” record. No required fields bind loop endpoints, config hash, source, formula, or reviewer. Impact: a high-di/dt board can bypass the gate by recording “no budget,” or pass with an arbitrary finite nH value.

SURVIVED

- Recorded calibration held: Kimi retained 0.02/0.07 failures, Qwen’s 66 vias were clean, Gemini `/AC_N` was 19.389 mΩ, and loop results were 28.70 nH at pitch 3.0 versus 28.73 nH at pitch 2.0.
- The H→nH conversion is correct, and real SHA-bound artifacts gated correctly.
- Unknown and duplicate keys, zero/negative finite budgets, missing board/JSON, syntactically invalid JSON, missing hash, hash mismatch, nonzero extractor exit, and exit 0 with a genuinely absent JSON all exited 3. Absent probes and null quantities exited 2.
- Absolute `$DCDC_PARASITICS` and `$FASTHENRY` paths worked, and arguments after `--` arrived intact.
- A nonzero extractor result is rejected before any existing JSON is read.
- Exact-net matching caused no documented workflow regression.
- The new arc sweep logic and duplicate-pad supernode behavior are sound.
- Draft semantics remain distinct from completion, and `RELEASE.md` transitively inherits the Completed-PCB gate without contradiction.
- No arithmetic-error `(a)` finding was found.

VERDICT

Rework. The copper changes close several concrete defects, but four historical findings remain materially open and the via fix adds a real false-failure mode. More importantly, the new loop guard has multiple exit-0 bypasses—non-finite budgets, invalid JSON quantities, stale output reuse, and unbound or fabricated wrong-loop extraction data—while PCB.md makes the entire backstop avoidable by declining to derive a budget. These are incompatible with a fail-closed completion gate for 172 V, 500/1500 W hardware.

---

DISPOSITION (evaluator, 2026-09-04 ~16:00Z, after verifying each finding)

Fixed and re-probed (all probes re-run, expected exits observed):
- FV-2 remainder: `--oz` now REQUIRED (silent 2 oz default removed).
- FV-5 regression / finding 1: vias adds a widest-contiguous-strip criterion
  (`--min-contact-mm`, default 0.3) measured on the mid-annulus circle -
  the kimi (114,44) 0.5 mm thermal bridge now PASSes ("spoke 0.50mm") while
  the true slivers still FAIL (strip 0.00 - edge contact never crosses
  mid-annulus). Representation-independent for spoke-vs-track.
- Finding 2: vias now refuses a graded net with declared-but-unfilled zone.
- Finding 3: budgets must be finite (>0, non-inf/nan); extracted quantities
  must be finite non-negative non-bool (forged -1e-9 -> FAIL 2).
- Finding 4: argparse usage errors exit 3 (GuardArgumentParser); non-object
  JSON exits 3.
- Finding 5: extractor exit 0 with an unchanged/absent parasitics.json
  refuses (mtime+size compared; stub-extractor probe -> exit 3).
- Finding 6: board/out/config absolutized before the cwd change;
  relative-path invocation re-probed working.
- Finding 7: `--json` + `--config` now binds meta.extract_config_sha256
  (mismatch or absent -> exit 3); missing config file -> exit 3.
- Finding 8: `L_loop_ring` and `probe_ring:<name>` gate ring-frequency L;
  plateau-vs-ring semantics documented in the guard docstring and PCB.md.
- Finding 9: PCB.md - budget derivation is itself part of the completion
  record; inapplicability records must enumerate candidate loops with
  endpoints, reason and source; extractor-config binding required.

Deferred, recorded as open (not silently dropped):
- FV-6 remainder: no executable expected-inventory manifest for
  resistance pairs (a --expect file mode like kicad_functional_proximity
  is the natural shape). PCB.md carries the enumeration duty meanwhile.
- FV-7 remainder: mixed-weight 2-layer stackups (one --oz for both
  layers); inner-layer boards are refused outright.
- FV-8 remainder: pad-shape approximations and lumped --via-mohm remain;
  disclosed in docstring/README; barrel-geometry model not planned.
- FV-9 remainder: no in-repo regression fixture suite yet; calibration
  lives in the docstring record and this review trail.
- Review #2's browser attach failed (honestly reported); its one browser
  claim (docs.kicad.org title) stayed unverified - no repo finding
  depended on it.
