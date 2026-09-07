# Codex review of 7a99de9 (2026-09-07)

Run 2026-09-07 ~09:09-09:5x local, `codex exec`, `-s workspace-write` with
`sandbox_workspace_write.network_access=true`, headed scratch Chrome attached over CDP on port
53748 (launched with `~/bin/agent-chrome`, armed to the reviewer pid). Browser access verified:
0 hits for `no connected browsers` and `node_repl/js (failed)` in the run log, and the run
returned a live `<title>` for docs.kicad.org.

Commissioned because finding 15 of `2026-09-07-claude-review-a5c83c2-7a99de9.md` observed that
`7a99de9` -- which shipped the 586-line `kicad_drc_connectivity.py`, a FOOTPRINTS.md inventory
rule and two doc sections -- was the only substantive commit in the reviewed range that no review
covered, and that two of that review's own findings lived in exactly that material.

**Disposition.** Findings 1, 2, 5 and 6 are fixed in the commit that carries this file; finding 3
was fixed in `a2624a4`; finding 4 was already fixed at HEAD; finding 7 is a citation gap, corrected
below. See the commit message for the measurements.

Finding 7's remedy: `ROUTING.md`'s DRC-clean-sliver statement cites only evidence section 12, which
records no sliver construction. The substantive support now exists in `copper_guards.py`'s
calibration record, and the citation should name it. Left as recorded rather than rewritten,
because the routing prose is accurate as it stands -- it is the pointer that is incomplete.

---

## ACCESS

Live query on 2026-09-07:

`KiCad kicad-cli pcb drc JSON report format unconnected_items included_severities`

The first result was KiCad’s official 8.0 CLI manual; 9.0 and master manuals also appeared. I inspected the current [KiCad 10 CLI “PCB DRC” section](https://docs.kicad.org/10.0/en/cli/cli.html), which confirms JSON output via `--format json` and full severity via `--severity-all`.

The supplied Playwright surface passed the `about:blank` DOM-control probe. Through it, `https://docs.kicad.org/` returned the title **`Documentation | KiCad`**.

Source access log: KiCad documentation root inspected for title, first attempt, live DOM, validated; CLI manual inspected at “PCB DRC”, first attempt, live DOM, validated; [DRC v1 schema](https://schemas.kicad.org/drc.v1.json) partially inspected for top-level provenance fields, first attempt, live raw text, validated. No browser was launched locally; the supplied CDP endpoint had already disappeared when cleanup was attempted.

Verification:

- `python3 -m unittest discover -p 'test_*.py'`: **435 tests passed, 6 skipped, exit 0**, run directly without a pipeline.
- `git diff --check 7a99de9^ 7a99de9`: clean.
- Worktree and index remained clean.
- `master` HEAD was `8fe0b00c7cbaaa596f927791c6a7039637fb7009`.
- The requested script diff is +119/−16 and includes the two gates, `--report-only`, `_names_json`, and `strict_pour`. Contrary to the supplied current-state description, `kicad_drc_connectivity.py` does **not** contain `GuardArgumentParser`; HEAD still constructs `argparse.ArgumentParser` at [scripts/kicad_drc_connectivity.py:445](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:445).

## FINDINGS

1. **P1 — (c) The report is bound to its JSON bytes, not to the board being fabricated.**

   At landing, the required-key set omitted the DRC’s `source` and `date`; the output’s `source` is instead the path of the input JSON, and `drc_metadata` drops the DRC’s board source (`scripts/kicad_drc_connectivity.py:200`, `:310`, `:332`, `:546` in `7a99de9`). A valid zero-open report naming `different-board.kicad_pcb` exits 0 under the total gate. A stale report from the right path behaves identically.

   `source_sha256` therefore proves which report bytes were parsed, not which PCB produced them. A safe binding needs the board as an input, a board digest, validation of the DRC’s internal source/date, and ideally ownership of the DRC invocation itself.

   **HEAD: still stands.** The corresponding logic remains at [lines 226–255](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:226), [359–379](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:359), and [621–631](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:621).

2. **P1 — (c) A caller-supplied pour declaration can still launder a real, zone-bearing signal open.**

   Landed code classified every record on a declared net as pour topology, regardless of item kinds (`scripts/kicad_drc_connectivity.py:171–181`). Its own test expressly approved pad-track records as pour (`scripts/test_kicad_drc_connectivity.py:60–71`). A `/SIG` pad-track open supplied with `--pour-net /SIG` became zero signal opens.

   At landing this did not defeat `--require-zero-total`, because the aggregate still included pour records. It did corrupt the ranked signal count, and the ungraded default still exited 0.

   HEAD’s `strict_pour` correctly makes zone-less pad-track records ambiguous, but only checks for the presence of a zone. A real `/SIG` zone-track open with `--pour-net /SIG --require-zero-signal-opens` still exits 0. Declarations are not checked against board net inventory, so phantom or misspelled nets are also accepted. See [lines 181–213](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:181).

   **HEAD: partially fixed, still exploitable.** Bind the pure/mixed classification authority and verify every named net against the actual board.

3. **P1 — (c) The exact-199 censor rule violates monotonicity beyond the boundary.**

   `report_censored = total == 199` (`scripts/kicad_drc_connectivity.py:299–328` landed; [current lines 326–355](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:326)) produces:

   - 198 records: evaluable
   - 199: unevaluable
   - 200 or 500: evaluable again

   With 200 duplicated zone-bearing records declared pour, HEAD’s signal gate returns PASS. Thus a strictly worse accepted input can recover from UNEVALUABLE to PASS at the far tail.

   The number comes from the [cross-project 10.0.5 calibration](/Users/fab/dev/kb/tooling/kicad-drc-caps-reports-at-199-per-type.md:1), but that calibration measured `silk_overlap` and `silk_over_copper`, not `unconnected_items`. The conservative refusal at 199 is safe; accepting `>199` as uncensored is not. Cap handling must be version/calibration-aware and monotone, normally refusing `>= cap` while cap state is applicable or unknown.

   **HEAD: still stands.**

4. **P1 — (c) The landed CLI had no usable signal-open gate and allowed an ungraded run to return success.**

   A landed in-memory CLI probe with 500 real signal opens and no gate returned exit 0. Only `--require-zero-total` existed (`scripts/kicad_drc_connectivity.py:416–443`, `:551–582`), which cannot serve workflows that intentionally tolerate pour records.

   **HEAD: fixed.** `--require-zero-signal-opens`, mandatory gate-or-`--report-only` validation, and separate gate fields now appear at [lines 444–495](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:444) and [608–679](/Users/fab/dev/ee/kicad-design/scripts/kicad_drc_connectivity.py:608).

5. **P2 — (c) No known-bad test exercises an actual KiCad DRC export.**

   Every report and affected item is built by the test’s own helpers at [scripts/test_kicad_drc_connectivity.py:11](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:11). Even `test_real_kicad_pth_pad_description_is_a_pad` is one hand-written string at [line 53](/Users/fab/dev/ee/kicad-design/scripts/test_kicad_drc_connectivity.py:53).

   A real fixture would catch exporter/schema drift, real item-description grammar, UUID and provenance fields, source/date handling, actual severity/exclusion representation, and whether `unconnected_items` really exhibits the 199 cap. A mutated scratch PCB followed by a real `kicad-cli pcb drc --severity-all --format json` would additionally prove the guard detects an open created in the artifact rather than one asserted by its test factory.

   **HEAD: still stands.** Later tests add useful cases, but remain hand-built dictionaries.

6. **P2 — (c) The footprint inventory rule permits model-tier evidence for an emitted-artifact claim.**

   The new rule requires “every emitted pad and hole” but says a “project generator assertion or a board parser” may produce the inventory (`FOOTPRINTS.md:98–104` landed; [current lines 142–150](/Users/fab/dev/ee/kicad-design/FOOTPRINTS.md:142)). That equates a construction-model assertion with a saved-board observation, contrary to [GUARDS.md:21–41](/Users/fab/dev/ee/kicad-design/GUARDS.md:21).

   This is particularly risky because the motivating remote NPTH was omitted by the placement model itself. The release check must reparse the authoritative footprint or saved board; a generator assertion can be supplemental but cannot establish emitted inventory.

   **HEAD: still stands.**

7. **P2 — (b) The DRC-clean-sliver statement was stronger than its cited evidence at landing.**

   `ROUTING.md:356–364` claimed “KiCad can accept a DRC-clean sliver” while citing evidence §12. That evidence records legal stitches, zero aggregate opens, and zero copper-rule findings, but no sliver construction or annular-contact measurement ([evidence lines 184–206](/Users/fab/dev/ee/kicad-design/reviews/2026-09-05-cross-session-routing-evidence.md:184)).

   **HEAD: substantively supported later, citation still incomplete.** The later [copper guard calibration](/Users/fab/dev/ee/kicad-design/scripts/copper_guards.py:7) now supplies measured sliver evidence, but the routing section at [ROUTING.md:414](/Users/fab/dev/ee/kicad-design/ROUTING.md:414) still cites only §12.

## SURVIVED

- Truncated/invalid UTF-8 JSON, missing `unconnected_items`, non-list fields, incomplete or unknown severity coverage, ignored connectivity checks, malformed records, mismatched nets, and unsupported item kinds all fail closed.

- Unicode net names work. A normalization mismatch on a zone record becomes ambiguous rather than passing.

- Except for the 199 boundary, duplicate records cannot reduce the aggregate count. `--require-zero-total` counts every bucket, so a false pour classification alone cannot pass that gate.

- `source_sha256` and `source_size` do bind the exact payload passed to `json.loads`; the before/after/current dev/inode/size/mtime check rejects ordinary mid-read replacement. I found no path where the recorded report digest describes bytes other than those graded.

- The remote-footprint incident’s quantitative prose matches its evidence: 2.2 mm NPTH, 10.2 mm offset, 0.1853 mm observed clearance against 0.200 mm, with no universal offset claimed.

- The AUTOROUTING ledger section’s non-monotone-sequence premise and scoped prescriptions are supported by the cited pass history. The endpoint-closure section correctly limits its generalization to one completion instance.

- All 158 local Markdown links and anchors checked in the changed documentation resolve at `7a99de9`.

- No arithmetic-error finding and no additional unsafe magic-number boundary were found.
