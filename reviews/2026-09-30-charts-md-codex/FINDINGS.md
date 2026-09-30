# Codex review of CHARTS.md + SKILL.md routing (2026-09-30)

- **Reviewer:** GPT Astra (`codex exec -m gpt-6-astra`, xhigh), with network access and an
  attached scratch Chrome.
- **Prompt:** `prompt.txt`.
- **Full report:** `scratch/review.md`.
- **Access proven:** the dsdig `origin/main` line was read, and the GitHub page title was
  fetched through the browser.

**Reviewed version.** CHARTS.md was edited concurrently by another session during the run.
Those additions cover the status vocabulary across classes, "print beats physics", pi
`--no-tools`, model choice from the dsdig benchmark, and reviewer FAIL calibration. The
reviewer captured the version it reviewed as `scratch/CHARTS.reviewed.md` and left the
later additions unreviewed.

## Findings and what was done

| # | finding | verified | action |
|---|---|---|---|
| P1 (c) | RDS(on)-vs-VGS output rules (reasons, statuses, validation verdicts, VGS readouts) were presented as universal. Gate charge uses `diagnostics` and `physical_output_available`; capacitance has separate chart/points/Qoss availability fields | yes: `gate_charge.py:108`, `mosfet_capacitance.py` ~641 | §2 now states per-class contracts and scopes readouts/validation to RDS(on) vs VGS; it also adds the `validation.assumptions` caveat |
| P2 (c) | The `claude` command fails as written: variadic `--allowedTools` swallows the prompt | yes: reproduced (rc 1, "Input must be provided…"); with `--` rc 0 and correct labels | command fixed with `--` |
| P2 (a) | `annotate` is not "every supported chart". It skips RDS-VGS, reverse recovery, reverse leakage and core loss, and filters by status. RDS vs Tj, RDS vs I_D and body diode exist with no CLI command | yes: `annotate_pdf.py:241-334` | table row corrected; the non-CLI digitizers are named with their entry points |

Also recorded: the Astra command could not run from inside the reviewer's own Codex sandbox
(`Operation not permitted`). It works from a normal shell (tested by Claude the same day),
and that limitation is now noted in the table.

## Survived

- All ten listed commands exist.
- The editable-install claim holds (`direct_url.json`, `.pth`), as does the `PYTHONPATH`
  workaround.
- The RDS-VGS vocabulary matches the code.
- The golden, review-tool and brief references check out.
- The identity-only model policy agrees with the global rule.
- The SKILL.md routing is consistent and the relative links resolve.
