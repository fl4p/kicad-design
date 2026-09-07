# Codex review of 75c10fa (2026-09-07)

Run 2026-09-07 ~10:0x local, `codex exec`, `-s workspace-write` with
`sandbox_workspace_write.network_access=true`, headed scratch Chrome attached over CDP on port
63569. Browser access verified: the one hit for the `no connected browsers` marker in the run log
is the reviewer reading the previous review file's own header in a `git show` diff, not a browser
failure; the run returned a live `<title>Documentation | KiCad</title>` and fetched the live
`drc.v1.json` schema, which independently confirms `source` and `date` are required fields.

Commissioned to review the commit that closed the four findings of
`2026-09-07-codex-review-7a99de9.md` -- a commit whose message was the fixing agent's own report
card on itself, which in this repository has now produced false claims three times.

**Disposition.** All five findings are fixed in the commit that carries this file. Note finding 4's
environment claim: the live-export test genuinely failed in the reviewer's sandbox, where
`kicad-cli` aborts with SIGABRT because a seatbelt denies its bootstrap -- the same denial that
stops Chrome starting there. It passes here. That is not a disagreement about the code: it is a
real portability defect in the test, which now distinguishes "the exporter could not run" (skip,
an environment fact) from "the exporter ran and produced something unexpected" (fail, a finding).

## ACCESS

A live Bing query for `KiCad DRC JSON report source date fields drc.v1.json` returned KiCad’s official [`RC_JSON::DRC_REPORT` reference](https://docs.kicad.org/doxygen/structRC__JSON_1_1DRC__REPORT.html) first; it exposes `source` and `date`. The live [DRC v1 schema](https://schemas.kicad.org/drc.v1.json) defines `source` as the source-file path and `date` as the report-generation time in date-time format, and requires both. Google first returned HTTP 429; Bing and the schema fetch succeeded, so this review did not silently go offline.

Through the supplied Playwright CDP browser, `https://docs.kicad.org/` returned `<title>Documentation | KiCad</title>`.

Commit `75c10fa` is merged into `master` at `0f86faa`; their trees are identical. The worktree remained clean.

## FINDINGS

1. **P1 — (c) The claimed board binding is false: same-name and stale reports still PASS.**

   The board is read and hashed at [kicad_drc_connectivity.py:245](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:245), but the only report-to-board comparison is:

   ```python
   PurePath(report["source"]).name == identity["board_name"]
   ```

   at [kicad_drc_connectivity.py:276](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:276). Consequently:

   - Two unrelated boards named `board.kicad_pcb` are indistinguishable.
   - A clean report generated hours before the board changed still passes.
   - `date` is merely a required key; it is not type-checked, parsed, compared with board mtime, or checked for freshness. Both a 1900 timestamp and `null` passed my gating probes.
   - `board_sha256` is merely recorded. No report field or independent authority is compared with it.
   - An arbitrary UTF-8 file named `board.kicad_pcb` passed a clean signal gate with exit 0.
   - The output also discards the report’s original `source`: top-level `source` is instead the DRC JSON pathname at [kicad_drc_connectivity.py:472](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:472), while `drc_metadata` omits report `source` at [kicad_drc_connectivity.py:811](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:811).

   The known-bad test checks only different basenames at [test_kicad_drc_connectivity.py:859](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:859). It therefore passes while the materially stronger commit claim remains false.

2. **P1 — (c) `--report-cap` is an unverified mute switch.**

   The fixed default boundary is monotone, but the caller can raise or disable it without supplying any calibration evidence. The same 199-record all-zone report produced:

   - default cap 199: exit 3, UNEVALUABLE;
   - `--report-cap 200`: exit 0, PASS;
   - `--report-cap none`: exit 0, PASS.

   The help text says to use the override only after probing the installed release, but the code verifies no probe, version, command, or digest ([kicad_drc_connectivity.py:314](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:314), [kicad_drc_connectivity.py:597](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:597)). The test at [test_kicad_drc_connectivity.py:275](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:275) explicitly blesses this unbound escape. This violates the binding `GUARDS.md` requirement to verify preconditions: the underlying report does not improve; only its warning is silenced.

3. **P1 — (c) The board “net inventory” is not a board parse and can falsely authorize a pour declaration.**

   `BOARD_NET` is an unanchored text regex at [kicad_drc_connectivity.py:55](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:55), run over the entire decoded file at [kicad_drc_connectivity.py:245](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:245). My probes established:

   - A net expression inside a footprint/pad is counted without establishing a top-level net table.
   - Comment-like or arbitrary garbage text containing `(net 99 "/GND")` is counted.
   - A non-s-expression file containing that substring is accepted.
   - A comment-only `/GND` “inventory,” paired with one all-zone report record, passed the signal gate with exit 0.
   - A net containing `)` parses correctly.
   - An escaped quote causes the legitimate declaration not to match, so that case refuses a legitimate net rather than failing open.
   - The whole file is read into memory with no size bound; `MemoryError` is not converted into the tool’s controlled UNEVALUABLE result.

   Thus misparsing can either refuse a legitimate board or falsely accept text as a net. `check_pour_nets_exist` at [kicad_drc_connectivity.py:297](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:297) does not prove what its name and error message claim.

4. **P2 — (c) The artefact tests do not prove the committed fixture is real or corresponds to the board.**

   `test_the_committed_fixture_is_a_real_export_with_two_real_opens` only loads the JSON, trusts its self-declared version/source, and classifies its records ([test_kicad_drc_connectivity.py:795](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:795)). It never reads or invokes KiCad on the board. It would still pass after replacing the board with unrelated same-name bytes.

   Likewise, `test_the_gate_fails_on_the_real_board` proves that the committed JSON contains two records, not that KiCad derived them from the committed board ([test_kicad_drc_connectivity.py:807](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:807)).

   The fixture is internally consistent—its pad UUIDs, nets, and positions match the JSON—but independent correspondence is **unverified** because the live exporter aborted in this environment. The only real export contains pad–pad records; it exercises none of the zone/track/via grammar on which the pour fix depends.

   The live test does genuinely invoke and reparse KiCad, but only when this hardcoded macOS path exists ([test_kicad_drc_connectivity.py:781](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:781)). An installed KiCad elsewhere silently becomes a skip, so “when a KiCad is installed” is false as written.

   Both requested suite runs executed 455 tests and both failed this test:

   - `python3` 3.14.0: **FAILED (failures=1, skipped=6)**; exporter exited `-6`.
   - `/usr/bin/python3` 3.9.6: **FAILED (failures=1, skipped=6)**; exporter exited `-6`.

   A sequential isolated rerun failed identically. Therefore the commit-message claim “455 tests, OK under both interpreters” is false for this review.

5. **P2 — (c) The tightening is fail-closed, but it breaks the documented real-pour workflow and leaves contradictory instructions.**

   I found no way to launder a pad, track, or via record through the strict signal gate: zone/track, zone/pad, zone/via, track-only, mixed-net, more-than-two-item, unknown-kind, and `--mixed-pour-net` records all remain non-PASS. Unicode and kind case/leading whitespace are handled; exact net-name case/whitespace mismatches do not pass; a net literally named `Zone` cannot spoof the item kind.

   However, “all-zone … is what refill actually produces” is too strong. The repository’s own measured evidence records a zero-signal candidate with two zone–zone and one zone–track GND records at [cross-session-routing-evidence.md:129](/Users/fab/dev/ee/kicad-design/reviews/2026-09-05-cross-session-routing-evidence.md:129). The root guidance also says real reports contain zone-track, zone-via, and track-via pour records ([ROUTING.md:544](/Users/fab/dev/ee/kicad-design/ROUTING.md:544)).

   The new behavior is defensible as a conservative refusal, but the signal-only gate is now unusable whenever a real declared pour net has any such record: signal gate exits 3, mixed-pour exits 3, and total gate exits 4. This does **not** make every board with pours unusable—zero-record or all-zone cases still pass—but it breaks a measured workflow the tool was introduced to support.

   Documentation was not reconciled:

   - The module docstring still says every pure-pour record, including zone-track/zone-via/track-via, is topology ([kicad_drc_connectivity.py:8](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:8)).
   - CLI help says only records “containing no zone” become ambiguous, although zone-track now does too ([kicad_drc_connectivity.py:634](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:634)).
   - `ROUTING.md` still calls a zone-only classifier wrong and instructs classification of every declared pure-pour record as topology.
   - Its sample invocation omits newly mandatory `--board` and also supplies neither a gate nor `--report-only`, so the documented command now exits 2 ([ROUTING.md:554](/Users/fab/dev/ee/kicad-design/ROUTING.md:554)).

## SURVIVED

- The default far-tail fix is correct: my 198/199/200/597 probes produced evaluable/unevaluable/unevaluable/unevaluable. `total >= cap` at [kicad_drc_connectivity.py:462](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:462) closes the earlier recovery-to-PASS bug.

- The pour mutation calibration is causal: disabling only the new all-zone condition fails exactly the pad, track, and via tests—3 failures—and the all-zone test remains green.

- Bypassing only the basename comparison fails exactly one test, as claimed. It fails for the right narrow reason: a different basename exits 0. It does not calibrate freshness or byte identity.

- A gating run without `--board` is refused, and a genuinely different basename is refused. Missing `source` or `date` keys are rejected. The official schema supports requiring those fields; the defect is that their values do not bind the report to current board bytes.

- The live-export test would fail rather than silently pass if the exact configured KiCad executable ran successfully but produced an incompatible schema or a different open count. Its present exporter abort also fails safely.
