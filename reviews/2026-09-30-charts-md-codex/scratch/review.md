## ACCESS

Checked on 2026-09-30.

1. `git -C /Users/fab/dev/pv/ee/datasheet-chart-digitizer log --oneline -1 origin/main` returned:

   ```text
   05057b7 Merge feat/rds-vgs: batch_all fixes (spec rows, locator, vector heads, log axes, rule-lattice binding), reviewer brief check 4, 41 goldens
   ```

2. Playwright MCP navigated to https://github.com/fl4p/datasheet-chart-digitizer and read `document.title`: `GitHub - fl4p/datasheet-chart-digitizer · GitHub`. The rendered repository page showed main at `05057b7`.

The alternate checkout `/Users/fab/dev/ee/dsdig-rds-vgs` is actually named `feat/rds-vgs`, but its HEAD, main, and origin/main all resolve to `05057b77d5613484060a4318ec5ee78445df30e6`. No diff against HEAD was present in the inspected digitizer source, reviewer brief, or golden provenance file.

Scope: the captured CHARTS.md (`CHARTS.reviewed.md`, SHA-256 `42f1df8d48969596d8ef1c6827477757d84c9a0dc1ac93a8f68371546363121d`) and the three added SKILL.md lines only. **CHARTS.md changed concurrently during this pass. All CHARTS.md line references below refer to the saved reviewed snapshot, not the subsequently edited live file.** The final comparison found added status wording, physics guidance, `--no-tools` flags, benchmark claims, and reviewer-calibration claims. Those later changes have not been reviewed; the supplied one-pass boundary was retained. `CHARTS.concurrent.diff` records the observed delta. The SKILL.md diff remained identical. SKILL.md and SETUP.md were read for integration consistency; the other sessions' named files were not reviewed. All deliberately created review files and configuration copies were placed in this scratch directory. No reviewed source/document was edited.

## COMMAND CHECKS

All five table entries were actually invoked through zsh from `/private/tmp`. The source image was `/Users/fab/dev/ee/solar-charger-eval/rds-vgs/work/v2/dmn4008_head_src.png`, SHA-256 `983e60d44808307cd2f24293f0bc517733269f7c6d51deffb9e7868c6a15c45e`. The question was exactly `List every drain-current label (I_D = ...) printed in this chart crop, verbatim, one per line.` The Claude prompt additionally named the absolute image path, as the table requires. Absolute paths replaced the table's crop/question-file placeholders; the option order and quoting were preserved.

CLI versions: codex 0.154.0, Claude Code 2.1.286, pi 0.84.3. PATH began with `/opt/homebrew/bin`. To contain state writes, pi and Claude used scratch configuration copies; unrelated pi extensions were omitted, and the required Antigravity provider was retained. Parent Claude session IPC variables were removed. These are contained command probes, not a test of every extension in the user's live CLI configuration.

| Table model | As-written result | Evidence |
|---|---|---|
| GPT Astra | **Unverified**, exit 1 before inference | `Error: failed to initialize in-process app-server client: Operation not permitted (os error 1)`; also a PATH-alias permission warning. This is an environment failure, not proof that the documented flags or image attachment are invalid. |
| Fable 5.1 | **Fails**, exit 1 | `Error: Input must be provided either through stdin or as a prompt argument when using --print` |
| Qwen 3.8 Max | **Works**, exit 0, 3.33 s | `I_D = 10.0A` and `I_D = 8.0A` |
| DeepSeek V4.1 Flash | **Works**, exit 0, 4.85 s | Same two labels |
| Gemini 3.8 Flash | **Works**, exit 0, 24.50 s | Same two labels |

These three successful as-written probes attached the source image via `@<absolute-path>`, with no expected answer or other model's reading in the prompt. The labels match the supplied crop. This is label transcription, not a sourced numeric design measurement.

Claude diagnosis: inserting `--` before the prompt fixes the variadic-option parsing problem. A diagnostic run additionally used `--verbose --output-format stream-json`. The first scratch-config attempt lacked the original profile's keychain authentication and returned `Not logged in`; a subsequent run used the existing original-profile access token in memory, with no keychain writes. That run exited 0, called `Read` on the exact absolute PNG path, received an image block, reported no permission denials, and returned both labels correctly. See `fable-read-receipt.json`. Thus `--allowedTools Read` does permit the absolute-path read after the command is correctly delimited.

Corrected command shape:

```sh
claude -p --model claude-fable-5-1 --allowedTools Read -- "<question naming the absolute image path>"
```

Pi's Anthropic OAuth claim reproduced using the scratch copy of its existing authentication: HTTP **400**, `invalid_grant`, `Refresh token not found or invalid`. The probe also warned that `claude-fable-5-1` was not in its Anthropic model catalog and used a custom ID; authentication failed before any inference. This verifies the observed account/session failure, not a universal limitation of pi's Anthropic provider.

Every dsdig command listed in CHARTS.md was invoked with `--help`, using the named venv Python and `PYTHONPATH=/Users/fab/dev/ee/dsdig-rds-vgs/src`. All ten exited 0. This establishes command availability and importability, not successful extraction on every chart family. The unmodified installed-branch help lacks `digitize-rds-vgs`, exactly as the document warns.

## FINDINGS

1. **[P1] (c) Section 2 presents one chart family's schema as the contract for all dsdig outputs.** `CHARTS.md:35-51` follows a table covering capacitance, gate charge, diode and core-loss charts, but its `ok/review_required/refused`, `reasons`, four table-validation verdicts, and mandatory VGS coordinate describe RDS(on)-versus-VGS. Other listed commands materially differ. `gate_charge.py:97-115` emits `diagnostics` and `physical_output_available`, withholding physical values unless status is `ok`; `gate_charge.py:806-836` also produces `unresolved`, `axis_assumed`, `axis_grid_inferred`, and `low_confidence`. `mosfet_capacitance.py:641-668` emits `status_reasons`, separate physical-availability gates, `qoss_validation_status`, and `trace_validation_status`. Its top-level status is derived separately at lines 392-400; it is not a substitute for the Qoss/Eoss gate. `capacitance_validation.py:42-76` distinguishes charge-integral validation, energy validation, and the materially weaker single-point `coss_anchor_only` tier. An agent following only this summary can lose refusal reasons or treat a chart-level result as validation of a loss-budget input. **Fix:** explicitly scope the existing vocabulary to RDS(VGS), link or enumerate the other families' contracts, preserve per-output availability/diagnostic fields, and require an unverified/refused outcome for unknown or missing states. Record the actual independent variable and chart-specific conditions, rather than VGS for every family.

2. **[P2] (c) The Claude command does not pass its question as a prompt.** `CHARTS.md:85`: `--allowedTools` accepts multiple following arguments, so the quoted question is consumed as another tools argument. The literal documented shape reproducibly exits with the missing-input error above. This also means `CHARTS.md:82` cannot serve as a reproducible assertion that all commands work as printed. **Fix:** insert `--` before the question, or move the prompt before the variadic option. The corrected absolute-path `Read` route was verified, not merely inferred from help text. Attach a dated crop/prompt/result receipt to the measured claim and keep the Astra check unverified for this run.

3. **[P2] (a) The coverage table overstates `annotate` and hides existing library-only chart support.** `CHARTS.md:15-26`, especially line 26, promises an annotated PDF of “every supported chart.” On main, `annotate_pdf.py:284-360` runs capacitance, transfer, breakdown, body diode, RDS(on)-versus-current, RDS(on)-versus-temperature, and gate charge. It does **not** dispatch RDS(on)-versus-VGS, reverse recovery, reverse leakage, or core loss, despite all four having dedicated CLI commands in the same table. Its embedding loop at lines 373-387 also filters by status. Conversely, `rdson_temperature.py`, `rdson_current.py`, and `diode_forward_voltage.py` already implement digitizers and are invoked by `annotate_pdf.py:292-321`, but have no dedicated `dsdig` subcommands in `cli.py:15-28` and no named rows here. Agents can consequently mistake “no named CLI command” for unsupported functionality and enter the hand/extension fallback, or expect a complete review PDF when important families were never dispatched. **Fix:** enumerate `annotate`'s actual family/status coverage and add rows naming the three existing module/API routes, explicitly saying they have no dedicated CLI command.

## SURVIVED

- The listed command names and their dedicated chart-class mappings match `cli.py:15-42`; all ten help calls succeeded. Omission of the two export commands is appropriate for a chart-reading table. The coverage defect is the universal `annotate` description and the unnamed module routes, not invented/misspelled command names.
- The editable-install claim is verified directly: `.venv/lib/python3.11/site-packages/datasheet_chart_digitizer-0.1.0.dist-info/direct_url.json` contains `dir_info.editable=true` and points to `/Users/fab/dev/pv/ee/datasheet-chart-digitizer`; `__editable__.datasheet_chart_digitizer-0.1.0.pth` loads its `src`. That checkout was on `hornresp-spl-digitizer`, and its help lacked the RDS(VGS) command. The alternate-checkout/PYTHONPATH workaround worked without changing branches or installing anything.
- Within **RDS(VGS)**, the documented panel statuses and all four aggregate validation verdicts are correct (`rdson_gate_voltage.py:21-27,315-317`; `rdson_gate_voltage_report.py:168-195`). The prose “not on chart” and “not traced” matches `not_on_chart` and `not_in_extracted_trace`, including the distinction between a genuine curve end and missing extraction. The readout code does not extrapolate or bridge trace gaps (`rdson_gate_voltage_report.py:29-71`). These claims survive when scoped to that implementation.
- The golden-fixture path and `PROVENANCE.md` exist. Provenance records 41 panels, human review, superseded fixtures and explicit re-bless records. The review-packet builder exists at the stated path; its `--rows-jsonl`, layer controls, Green/Flag/Needs-rework choices and JSON export are present. The reviewer brief contains the cited checks 4, 12 and 17 and its self-test.
- The numeric-use policy is explicit enough: `CHARTS.md:95-108` permits identity questions and bars model-read curve quantities from design inputs, even when several models agree. It is consistent with the global “digitize it or label it unverified” rule and is stricter about design use of estimates. Calibrated manual measurement is distinguishable from accepting an eyeballed model number; the requirement to extend dsdig and human-approve new categories before bulk use remains stated.
- At least three different vendors, a neutral cwd, the same neutral question, no prior answers, a source crop rather than an overlay, and `unknown` on disagreement are already specified (`CHARTS.md:76-102`). “Agree with each other and with the page” requires agreement, not a majority vote. Do not invent a missing-consensus-rule finding. Full dsdig agent reviews must still follow the linked brief's more specific Astra/DeepSeek/Opus roster; section 4 explicitly directs reviewers to that brief.
- `SKILL.md:44` sits under “Route the task before acting,” while `CHARTS.md:3` explicitly says before using chart values. The trigger is timely. `SKILL.md:357-358` reinforces the same numeric-use rule. Grepping SKILL.md and SETUP.md found complementary evidence, vision, source and reviewer requirements rather than a competing chart-digitization workflow. The existing typical-versus-guaranteed-value distinction remains applicable. Both new Markdown links resolve, as does CHARTS.md's SKILL.md link.
- The dated pi Anthropic HTTP 400 observation reproduced. Three commands worked as written, and corrected Claude successfully read the absolute image. Astra's actual image-reading result remains **unverified** because local initialization failed; this does not become a factual defect in the command without further evidence.
