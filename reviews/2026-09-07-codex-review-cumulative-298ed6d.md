# Codex review of the cumulative guard at 298ed6d (2026-09-07)

Fourth round. Unlike the first three this reviewed the CUMULATIVE state of
`scripts/kicad_drc_connectivity.py` rather than one commit -- five rounds of fixes had been layered
on it in one day, each patching the previous patch -- and it was pointed at the ~400 real
`.kicad_pcb` files on this machine and a real `kicad-cli`.

Browser access verified: 0 marker hits, live `<title>` returned.

**Disposition.** The two P0s are fixed in the commit that carries this file, both with known-bad
calibrations that were confirmed to fail without the fix. Findings 3 through 9 are NOT yet fixed
and are recorded here as open: `record_id`'s encoding ambiguity and its omission of record content,
the cap "probe" still being a label rather than a measurement, the net scan matching at any depth
after the KiCad 10 fix (so `(kicad_pcbx (net "/PHANTOM"))` is accepted), the freshness comparison's
timezone/DST and future-clock holes, the unreconciled fabrication-gate semantics between
`ROUTING.md` and the module, incomplete escape provenance, and the tests that pass for narrower
reasons than their names claim.

Its closing judgement stands as the honest summary of where the guard was:
*"the real 11-open-board false pass means the cumulative result is not safe as a
fabrication-closing fail-closed guard."*

---

## FINDINGS

1. **P0 — (c) The report is not bound to the board; a real known-bad board false-passes.**

   The board SHA-256 is calculated and merely written into the result at [kicad_drc_connectivity.py:431](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:431). The only correspondence check is basename equality at [kicad_drc_connectivity.py:441](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:441), followed by an ordering check on mutable timestamps.

   Concrete reproduction:

   - Target board’s adjacent report: 11 unconnected items, gate exit 4.
   - Different board’s report: zero items, same basename, later timestamp.
   - Zero report against the 11-open target: exit **0**.
   - Board SHA-256 values differ.
   - Target contains 34 zones despite `--no-pour-nets`.

   This directly violates the authority/digest contract in [GUARDS.md:62](/Users/fab/dev/ee/kicad-design/GUARDS.md:62). The safe design must own the export or consume a trusted receipt binding exact board bytes, report bytes, executable, and run.

2. **P0 — (c) `--reviewed-record` reopens the exact real-open laundering that the all-zone fix closed.**

   The acknowledgment branch at [kicad_drc_connectivity.py:261](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:261) executes before the non-zone refusal at [kicad_drc_connectivity.py:276](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:276). A pad-track `/SIG` record behaves as follows:

   - no declaration, no acknowledgment → `signal_open`
   - declaration only → `ambiguous`
   - acknowledgment only → `signal_open`
   - declaration plus acknowledgment → `pour_topology`

   Thus two caller-supplied labels turn a real pad-track open into a passing signal gate without any zone evidence. They are not independent evidence. This weakens the `75c10fa` all-zone guarantee and violates the exact mechanism requirement in [GUARDS.md:91](/Users/fab/dev/ee/kicad-design/GUARDS.md:91).

3. **P1 — (c) `record_id` is not an identity for one immutable record.**

   At [kicad_drc_connectivity.py:127](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:127), the ID is only the sorted UUID strings joined with `|`, SHA-256 hashed, then truncated to 48 bits. It ignores board digest, net, descriptions, item kinds, positions, severity, and record description.

   Consequences reproduced:

   - Changing a zone-track record into a moved zone-via record while retaining UUIDs preserves the ID and the old acknowledgment.
   - Two different records with the same UUID pair share one acknowledgment.
   - Invalid-but-accepted pairs `["a|b","c"]` and `["a","b|c"]` produce the same full hash input, without attacking SHA-256.
   - A board edit that preserves object UUIDs preserves the waiver even when geometry and mechanism change.
   - The “stale ID” rule at [kicad_drc_connectivity.py:678](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:678) is bypassed whenever the new record retains or collides with that ID.
   - Missing UUIDs remain safely ambiguous; they do not bypass the gate.
   - An ordinary genuinely orphaned ID causes exit 2. That is an intentional operational denial of service, not a pass.

   Random 48-bit collision probability at 199 records is only about `7×10⁻¹¹`; that alone is not the practical defect. The deterministic encoding ambiguity, duplicate UUID acceptance, and omission of record content are.

   The current 102-report corpus contained no missing UUID, duplicate UUID within a record, or duplicate record ID. That is only an unstated premise that currently holds, not an enforced precondition.

4. **P1 — (c) The cap “probe” remains a mute switch carrying a matching label.**

   [kicad_drc_connectivity.py:521](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:521) validates no probe artifact, command, known-bad result, executable identity, or violation type. It merely compares the caller’s string with `kicad_version` from the report at line 539. Both values can be asserted without a measurement.

   `--report-cap none --report-cap-probed-on 10.0.5` therefore disables censoring without proving `unconnected_items` behavior. The output records neither `report_cap_probed_on` nor probe provenance. Commit `a496a23` says “a mute is not a probe,” but implements a label as the probe.

5. **P1 — (c) The structural net-scan guarantee was undone by the KiCad 10 fix.**

   The old prose still says only depth-1 declarations count at [kicad_drc_connectivity.py:335](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:335), while current code matches `(net ...)` at every depth at line 372.

   Reproduced accepted inputs:

   - `(kicad_pcbx (net "/PHANTOM"))`
   - `(kicad_pcb # (net "/PHANTOM"))`
   - `(kicad_pcb (metadata (net "/PHANTOM")))`
   - `(kicad_pcb)` followed by an unterminated string

   The root check is only `startswith("(kicad_pcb")` at [kicad_drc_connectivity.py:347](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:347), and comments/forms are not parsed. This reintroduces the “net-shaped text authorizes a pour net” failure that `a496a23` claimed to close.

   Separately, `_NET_EXPR` at line 91 omits legacy unquoted names and returns raw escaped strings rather than semantic names.

6. **P1 — (c) Freshness is neither a byte binding nor a sound time comparison.**

   [kicad_drc_connectivity.py:462](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:462) compares KiCad’s naive local timestamp with a local rendering of the board’s epoch mtime.

   Reproduced:

   - Board modified exactly two seconds after the report: accepted.
   - Two seconds plus one microsecond: rejected.
   - Report dated 2099: accepted; there is no future-clock ceiling.
   - At Berlin’s DST fallback, a first-occurrence 02:30 report against a second-occurrence 02:30 board passes despite a real one-hour gap.
   - A Berlin-created naive 12:00 report checked under UTC can appear newer than a board actually modified 30 minutes later.
   - An explicit `+02:00` offset normalizes correctly.
   - Valid ISO `Z` is accepted by Python 3.14 but rejected by `/usr/bin/python3` 3.9.

   Touching or checking out unchanged board bytes can reject a valid report; a future clock or same-name newer report can admit an invalid one. Two seconds is unnecessarily loose for the stated sub-second quantization on APFS and is not a principled bound for coarse filesystems.

7. **P1 — (c) The fabrication-gate semantics contradict their own governing documentation.**

   [ROUTING.md:544](/Users/fab/dev/ee/kicad-design/ROUTING.md:544) says “the gate is still the total,” and [ROUTING.md:583](/Users/fab/dev/ee/kicad-design/ROUTING.md:583) says the split changes ranking, not the definition of done. Yet the module calls the signal gate “the gate that closes fabrication” at [kicad_drc_connectivity.py:11](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:11), and its help says it is the usable board gate at line 924.

   The suite deliberately proves that one all-zone unconnected record exits 0 at [test_kicad_drc_connectivity.py:676](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:676). That is incompatible with ROUTING’s explicit statement that islands remain unfinished until the total is zero. The module docstring, CLI help, `scripts/README.md`, and `ROUTING.md` are not reconciled.

8. **P2 — (c) Escape provenance is incomplete.**

   Output records reviewed IDs and the resulting cap, but not:

   - whether `--allow-report-older-than-board` was used;
   - the purported `--report-cap-probed-on` evidence;
   - whether an empty pour list came from explicit `--no-pour-nets`;
   - any reviewer, reason, mechanism, or board revision for an acknowledgment.

   The result assembly at [kicad_drc_connectivity.py:1098](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:1098) silently loses these facts. A pass produced under an exception is therefore not accurately auditable.

9. **P2 — (b)/(c) Several tests pass for narrower or contrary reasons than their names claim.**

   - `test_awkward_net_names_survive` at [test_kicad_drc_connectivity.py:1268](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:1268) blesses the raw escaped spelling, not the semantic net name.
   - `test_an_acknowledgement_does_not_cover_a_different_record` at line 1170 changes descriptions; the helper hashes descriptions into UUIDs, so the test merely proves different UUIDs make different IDs.
   - The helper’s claim that every item has its “own” UUID at line 18 is false for repeated identical descriptions.
   - `test_an_override_probed_on_this_release_is_accepted` at line 1121 conducts no probe.
   - `test_a_garbage_file_cannot_authorise_a_pour_declaration` only exercises garbage lacking the accepted prefix.
   - The live-export test’s skip at line 917 treats every CLI nonzero status as environmental; here SIGABRT is appropriately skipped, but the same branch would also skip a genuine installed-KiCad exporter crash.

   Commit claims now disproved include `a496a23`’s board binding, probe evidence, immutable one-record acknowledgment, and garbage rejection. At current HEAD, `9551cd4`’s “five nonexistent paths” are five directories, not nonexistent paths, and its six-skip suite count is seven.

## SURVIVED

- No arithmetic error `(a)` was found. The `total >= cap` monotonicity correction remains intact at [kicad_drc_connectivity.py:714](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:714).
- All 323 real version-20260206 inventories matched KiCad 10.0.5 exactly; the critical KiCad 10 empty-inventory regression is fixed.
- All seven empty inventories and the one malformed-board refusal were correct.
- Without `--reviewed-record`, the strict all-zone rule still refuses zone-track, zone-via, track-via, and pad-track records.
- Missing/empty item UUIDs remain ambiguous. Ordinary orphaned reviewed IDs are rejected.
- `--no-pour-nets` plus an acknowledgment cannot directly excuse an undeclared signal record.
- `--report-only` plus a gate is rejected, and its successful console output explicitly says not graded.
- `allow_abbrev=False`, pre-parse stale-output invalidation, and input/output alias protection remain present.
- JSON writing still uses same-directory temporary creation, flush, `fsync`, and `os.replace` at [kicad_drc_connectivity.py:775](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:775).
- Both required suites pass, and the sandbox-blocked live export skips rather than fails.
- Despite those surviving pieces, the real 11-open-board false pass means the cumulative result is **not safe as a fabrication-closing fail-closed guard**.
