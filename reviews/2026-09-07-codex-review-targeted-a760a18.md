# Codex targeted review of a760a18 (2026-09-07)

Fifth round, and the first that was SCOPED: only the seven fixes of `a760a18`, with an explicit
instruction to note anything else in one line and move on.

Browser access verified: 0 marker hits; Google returned 429 and the same live query succeeded
through Bing; `<title>` returned for docs.python.org. It confirmed independently that Python 3.9.6
rejects an ISO `Z` without normalisation while 3.14.7 accepts it.

**All six calibration claims of `a760a18` were verified TRUE** by re-applying each mutation to a
copy of the script. This is the first round in which nothing the commit message claimed was false.

**Disposition.** All seven findings are fixed in the commit that carries this file, each with a
known-bad calibration confirmed to fail without it. Two of those calibrations did not bite on the
first attempt -- `gr_line`/`target` and the `pos` omission had no test at all -- and tests were
added before the fixes were accepted.

Two corrections to the reviewed commit's own reporting, both from this review:

- Its "OK (skipped=6)" is environment-specific. Where `kicad-cli` cannot run, two further tests
  skip, so 8 skip. Both counts are honest about their own environment; neither is universal.
- Its corpus arithmetic was wrong, and this is the second time: the five entries reported as
  "nonexistent paths" are DIRECTORIES named `*.kicad_pcb`. Refusing them is correct. The corpus
  holds 401 matching entries, of which 5 are directories, 1 is a genuinely truncated board, and
  all 395 real boards parse.

---

## ACCESS

- Playwright live query: `Python datetime.fromisoformat Z support Python 3.9 3.11`. Google returned HTTP 429; the same live query succeeded through Bing. No offline fallback was used.
- Playwright fetched [Python’s datetime documentation](https://docs.python.org/3/library/datetime.html); `<title>` was: `datetime — Basic date and time types — Python 3.14.7 documentation`.
- [Python 3.11 documentation](https://docs.python.org/3.11/library/datetime.html#datetime.datetime.fromisoformat) shows a `Z` example and records the parser expansion in 3.11. The mutation run independently confirmed that Python 3.9.6 rejects `Z` without normalization while Python 3.14.7 accepts it.

The review started with `git show a760a18`. The scoped files are unchanged between `a760a18` and merged `master` at `a462f15`. The working tree remains clean.

## EXECUTION

- `python3` 3.14.7: 493 tests passed, 8 skipped, exit 0.
- `/usr/bin/python3` 3.9.6: 493 tests passed, 8 skipped, exit 0.
- Thus the 493 claim survives. The claimed “second skips 6” is false in this sandbox: six dependency skips plus two KiCad SIGABRT skips.
- Current corpus: 396 boards; 395 parsed, including 7 empty inventories; 1 refused. The `19f47b7` and current scanners returned identical results for every board.
- Net parents observed: `segment` 185,033; `via` 128,247; `pad` 126,241; top-level `kicad_pcb` 5,087; `zone` 4,616. No legitimate parent outside the new allowlist was found.
- The historical “395 parse, 7 empty, 6 refused” count is not reproducible against today’s corpus; because the external corpus may have changed, I treat the historical count as unverified rather than false.

## CALIBRATION CLAIMS

All six mutation claims are true. Mutations were applied only to a copied script and run against the 99-test connectivity suite.

| Reversion | Result |
|---|---|
| Remove net-parent restriction | 1 failure: plausible-root phantom net |
| Remove future ceiling | 1 failure: future report accepted |
| Remove duplicate-UUID refusal | 1 failure: duplicate UUID gained an ID |
| Revert to UUID-only ID | 1 failure: changed record with same UUIDs retained ID |
| Remove cap-probe requirement | 3 failures |
| Remove `Z` normalization | Python 3.14 passes; Python 3.9 fails exactly the `Z` test |

No calibration claim was false.

## OUT OF SCOPE

The older owned-run test also skips broadly on any exit-2 message containing `kicad-cli` at [test_kicad_drc_connectivity.py:1539](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:1539); this commit did not rework that branch.

## FINDINGS

1. **P1 — (a)/(c) `--report-cap none` is still not supported by its alleged proof.**

   For `cap is None`, any count above 199 is accepted as evidence that the release “does not cap AT ALL” at [kicad_drc_connectivity.py:720](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:720). A report containing 200 findings only proves that the cap is above 199; it is equally consistent with a cap of exactly 200. If later signal opens were truncated after 200 pour records, that capped report could pass.

   The counting also contradicts “one type”: `schematic_parity` is counted by total list length, although real reports contain multiple `type` values, while `unconnected_items` counts even non-object entries at [kicad_drc_connectivity.py:707](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:707). Reproduced:

   - 100 parity findings of type A plus 100 of type B qualified `none`;
   - 200 `null` unconnected entries qualified `none`;
   - a 200-record graded report was accepted as its own probe and exited 0.

   The probe is not schema-validated or bound to an exporter; its version is self-declared. The test named “real probe” creates synthetic JSON itself at [test_kicad_drc_connectivity.py:1192](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:1192). A safe observation can justify a higher finite cap, not “none”.

2. **P1 — (c) freshness still has two false-PASS paths.**

   A naive report time is interpreted in the checker’s current timezone at [kicad_drc_connectivity.py:638](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:638). The same stale `2025-07-01T12:00:00` report was rejected under `Europe/Berlin` but accepted under `UTC` against a board modified at 10:30 UTC. External-report operation therefore assumes, but neither enforces nor records, the exporter’s timezone.

   More directly, `--allow-report-older-than-board` skips the entire function at [kicad_drc_connectivity.py:1414](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:1414), including ISO parsing and the future ceiling. A report dated `2099-01-01T00:00:00Z` exited 0 with that flag. The escape should bypass only the board/report ordering comparison.

3. **P1 — (c) `record_id` still deterministically aliases materially different records.**

   The JSON encoding itself is unambiguous, but the payload omits each item’s `pos` at [kicad_drc_connectivity.py:188](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:188). Moving an item from `x=1` to `x=999` while preserving UUIDs and descriptions produced the same ID, `2d33f820f368a8ac`. An acknowledgement can therefore survive a geometry change—the exact position-dependent exemption problem [GUARDS.md:88](/Users/fab/dev/ee/kicad-design/GUARDS.md:88) warns about.

4. **P1 — (c) the net scanner still accepts net-shaped text under non-net objects.**

   `gr_line` and `target` are in `NET_BEARING_FORMS` at [kicad_drc_connectivity.py:117](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:117). The official KiCad grammar defines `gr_line` as a graphical, non-connectivity object with no `net` member, while the board track forms are `segment`, `via`, and `arc`. [KiCad format documentation](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#_graphic_items)

   Reproduced inventories:

   - `(kicad_pcb (gr_line (net "/PHANTOM")))` → `["/PHANTOM"]`
   - `(kicad_pcb (target (net "/PHANTOM")))` → `["/PHANTOM"]`
   - `(kicad_pcb # (net "/PHANTOM"))` → `["/PHANTOM"]`

   Exact-root checking and the `metadata` attack are fixed, and no legitimate omitted parent was found in the corpus. The allowlist nevertheless weakens the claimed “only forms that can carry a net” guarantee.

5. **P2 — (c) escape provenance is present in JSON but not guaranteed in output.**

   `--json` remains optional at [kicad_drc_connectivity.py:1194](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:1194); without it, stdout contains only counts at [kicad_drc_connectivity.py:1477](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:1477). A PASS using external-report trust, stale-report allowance, a cap override, or reviewed records therefore need not emit any provenance.

   In JSON, the explicit switches are represented, but the verdict-affecting assumed timezone is absent. Also, bare `ID` and `ID=` remain accepted with `null`/empty reasons at [kicad_drc_connectivity.py:768](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:768), so the claim that a human-judgement PASS carries “why” is not enforced. Embedded `=` characters are preserved and duplicate IDs are correctly rejected.

6. **P2 — (c) one documentation contradiction remains.**

   The module docstring, CLI help, README, and exit behavior now treat `--require-zero-total` as completion. But [ROUTING.md:551](/Users/fab/dev/ee/kicad-design/ROUTING.md:551) still calls the signal-open path “the gate that closes fabrication,” immediately before describing `--require-zero-signal-opens`. This contradicts both line 544 and the new wording.

7. **P2 — (c) the live-export skip/fail split cannot distinguish sandbox death from a genuine crash.**

   Every negative return code is skipped at [test_kicad_drc_connectivity.py:937](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:937). A genuine KiCad SIGSEGV or SIGABRT is also negative, so it would still be skipped. Positive nonzero exits now fail correctly, but the stated “genuine installed-KiCad crash now fails” claim does not survive.

## SURVIVED

- F5’s exact-root and direct-parent fixes work for the named `kicad_pcbx` and `metadata` attacks. All 396 corpus results match `19f47b7`; no legitimate net-bearing parent was missed.
- No unterminated-string false positive was found across the corpus. Strings containing net-shaped text were skipped correctly; malformed trailing unterminated text was refused.
- `Z` normalization is correct and necessary on Python 3.9.
- `fold=0` is the conservative fallback choice: the first 02:30 instant was treated as older than a board modified during the second 02:30.
- Explicit offsets are compared as instants and behave correctly. The documented two-second skew boundary also remains intact.
- The ordinary future ceiling works when freshness checking is enabled. A timestamp more than one hour ahead is refused; timestamps within that declared hour—including a future board/report pair—are intentionally admitted.
- `record_id`’s JSON encoding closes the delimiter ambiguity; duplicate item UUIDs produce no ID. Description changes deliberately void acknowledgements, which is appropriate fail-closed behavior despite upgrade churn. Random 64-bit collision risk at this scale is negligible compared with the deterministic `pos` omission.
- Three of the four named F9 reworks now test what they say: raw escaped spelling, changed content with retained UUIDs, and plausible-root garbage. Only the live-export crash distinction fails.
- Exit codes agree with the module contract: 0 pass/report-only evaluable, 2 input/configuration, 3 ambiguous/capped, 4 failed requested gate.

