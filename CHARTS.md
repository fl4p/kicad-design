# Reading datasheet charts

Read this before using any value that comes from a datasheet *chart* rather than a
table: curves such as RDS(on) vs VGS or Tj, capacitance vs VDS, gate charge, transfer,
body diode, reverse recovery, leakage, breakdown vs Tj, or core loss. A number read off a
chart carries the same evidence duty as a table value
([`SKILL.md`](SKILL.md), "Ground component decisions in current evidence"), plus the
duties below.

## 1. dsdig first

Use the datasheet-chart-digitizer at `/Users/fab/dev/pv/ee/datasheet-chart-digitizer`
(its own `.venv`, CLI `dsdig`, `dsdig --help`), never a one-off extractor.

| command | chart class |
|---|---|
| `find` | locate chart panels, emit `charts.json` |
| `digitize-capacitance` | Ciss/Coss/Crss vs VDS |
| `digitize-vpl` | gate charge, Miller plateau Vpl |
| `digitize-rds-vgs` | RDS(on) vs VGS, checked against the RDS(on) table |
| `digitize-transfer` | ID(VGS, Tj) saturation transfer, tempco |
| `digitize-reverse-recovery` | diode Qrr/Irm/trr/S |
| `digitize-reverse-leakage` | diode Ir(Vr) at several Tj |
| `digitize-breakdown-voltage` | V(BR)DSS vs Tj |
| `digitize-core-loss` | powder-core loss vs the printed law |
| `annotate` | annotated PDF copy of the charts it dispatches: capacitance, transfer, breakdown voltage, body diode, RDS(on) vs I_D, RDS(on) vs Tj, gate charge. It does **not** run RDS(on) vs VGS, reverse recovery, reverse leakage or core loss, and it filters overlays by status |

A missing CLI command does not mean a missing digitizer. **RDS(on) vs Tj**
(`rdson_temperature`), **RDS(on) vs I_D** (`rdson_current`) and the **body-diode**
V_SD curve (`diode_forward_voltage`) have no `digitize-*` command. Reach them through
`annotate`, or call `digitize_pdf(pdf, out_dir)` / `digitize_pdf_fail_closed(...)` in the
module (checked on `main`, 2026-09-30).

**The venv's `dsdig` runs whatever branch the main checkout is on.** It is an editable
install. On 2026-09-30 that checkout sat on another session's branch, and
`digitize-rds-vgs` was missing from `dsdig --help`. Check with
`git -C /Users/fab/dev/pv/ee/datasheet-chart-digitizer branch --show-current`. If it is
not `main`, run from a worktree at `main` with `PYTHONPATH=<worktree>/src` and the same
venv Python.

## 2. Using dsdig's output

- **Status is a claim, not a pass.** Only `ok` means the automated gates passed. The
  vocabulary differs by chart class. On `main` (2026-09-30), dsdig emits:
  - usable only as *unverified*: `unverified`, `review_required` (RDS(on) vs VGS),
    `overlay-review-required`, `suspect`;
  - no value: `refused`, `unresolved`, `not_evaluable`, and exceptions carrying a refusal reason.

  Treat any status other than `ok` as at best unverified. Carry the class's reasons or
  diagnostics with the value.
- **Each class has its own output contract. Read it; don't assume the RDS(on)-vs-VGS
  one.**
  - **Gate charge:** carries `diagnostics` and `physical_output_available`. No physical
    curve is served unless the status is `ok`.
  - **Capacitance:** the chart, its points and the Qoss/Eoss metrics each carry their own
    availability and reasons (`physical_output_available`,
    `points_physical_output_available`, `qoss_metrics_physical_output_available`, …). A
    chart-level `ok` does not replace them.
  - Record the chart's actual independent variable (VDS, Tj, I_D, Qg, …), not "VGS" by
    habit.
- **RDS(on) vs VGS specifically:**
  - Readouts are never extrapolated. "not on chart" means the curve does not reach that
    VGS. "not traced" means the print may have it and the trace does not. Neither is
    zero, and neither is a value.
  - Validation against the datasheet table is `verified`,
    `consistent_at_approximate_conditions`, `inconsistent` or `not_evaluable`. An
    `inconsistent` can be a datasheet fact. Report it; do not tune it away.
  - Check `validation.assumptions`. As of 2026-09-30 a `verified` can rest on an
    unproven equivalence (a Tc = 25 °C curve against a Ta = 25 °C table row), which is
    not shown as a reason.
- **Human-verified panels** are the golden fixtures, e.g.
  `tests/fixtures/rds_vgs_golden/` with `PROVENANCE.md`. Only those carry Fab's check.
- **Record per value:** the part, PDF page and figure, curve (its I_D and T with kind
  Tj/Tc/Ta), VGS, value and unit, status, validation, and the dsdig commit.
  - Use the curve whose conditions match the operating point.
  - A 25 °C curve is not the value at 100 °C.
  - A 5 A curve is not the value at 50 A.

## 3. When dsdig refuses, is unsupported, or answers "unknown"

**An honest refusal is still a defect when the page answers it.** `unknown`, `unbound`,
`not_evaluable` and every calibration warning (e.g. `axis_ticks_not_bound_to_grid`) are
claims that the information is unavailable. Test each against the page before accepting
it. A trace lying on the ink proves nothing about calibration: a squeezed axis still
looks right and reads wrong. Measure the ticks against their printed rules.

If the page answers it, the fix belongs in dsdig: extend the library, per the global
rule. A new chart class first needs a few human-verified overlays approved by Fab before
it is used in bulk. Meanwhile, for the design task, climb this ladder in order and stop
at the first rung that gives a definite answer:

1. **Read it by hand.**
   - `pdftotext -bbox` on the page.
   - Vector glyphs and paths (PyMuPDF `get_text("words")`, `get_drawings()`).
   - Re-render the page at 600 dpi or more; crop at 4× and 8×.
   - Follow every leader line and arrow to its tip.
   - Bind labels by what the page prints: placement, leader lines, legend. Physics is a
     cross-check, never an override. At equal VGS, a higher I_D gives a higher RDS(on);
     well above threshold, a higher temperature gives a higher RDS(on). When the print
     contradicts physics, the print stands and the conflict is recorded as a finding.
     Example: on STK295N10F8AG the "25 °C" arrow points at the leftmost transfer curve.
     Fab's verified GT follows the arrow. A model that re-ordered the curves by physics
     was wrong.
2. **Ask independent frontier models.**
   - Give each one the *source* crop, never an overlay, and the same neutral question.
   - Include no prior reading, tool output, or other model's answer.
   - Use at least three models from different vendors.
   - Run from a neutral working directory: pi loads project context from its cwd.

   | model | command (each run 2026-09-30 on `work/v2/dmn4008_head_src.png` in the rds-vgs eval; each returned both printed labels, "I_D = 10.0A" and "I_D = 8.0A") |
   |---|---|
   | GPT Astra | `codex exec -m gpt-6-astra -s read-only --skip-git-repo-check -i crop.png - < question.txt`. It works from a normal shell, but not from inside another Codex sandbox (`failed to initialize in-process app-server client`). |
   | Fable 5.1 | `claude -p --model claude-fable-5-1 --allowedTools Read -- "<question naming the absolute image path>"`. **The `--` is required:** `--allowedTools` is variadic and otherwise swallows the question ("Input must be provided …"). pi's Anthropic provider fails its OAuth refresh (HTTP 400 `invalid_grant`, 2026-09-30), so Fable goes through the `claude` CLI. |
   | Qwen 3.8 Max | `pi -p --no-session --no-tools --model fireworks/accounts/fireworks/models/qwen3p8-max @crop.png "<question>"` |
   | DeepSeek V4.1 Flash | `pi -p --no-session --no-tools --model fireworks/accounts/fireworks/models/deepseek-v4p1-flash @crop.png "<question>"` |
   | Gemini 3.8 Flash | `pi -p --no-session --no-tools --model antigravity/gemini-3.8-flash @crop.png "<question>"` |

   **Always pass `--no-tools` to pi.** Without it, pi gives the model its default read, bash,
   edit and write tools in the working directory, with Fab's permissions. On 2026-09-30 a
   Gemini 3.1 Pro run given tools pip-installed `pytesseract` into Homebrew's Python
   (`--break-system-packages`). An identity question needs no tools.

   **Model choice.** The dsdig benchmark (`docs/model-benchmark-2026-09.md` in the dsdig
   repo) measured image-only reading on five sets. Astra is by far the strongest without
   tools: for example, 377/436 vendor-data curves within 1 % of span, against 158–338 for
   the others. Gemini 3.8 Flash is among the weakest. Prefer Astra (OpenAI), one Claude
   model (Opus or Fable) and Qwen 3.8 Max (Alibaba) as the three vendors.

   `pi --list-models <search>` shows what is configured, including whether a model takes
   images. Pin `PATH=/opt/homebrew/bin:$PATH` when launching detached (see the
   `codex-review` skill).
3. **Only then `unknown`.** Record the rungs tried and each model's verbatim answer.

**What a model reading may be used for.** Model readings settle *identity* questions:
- which label belongs to which curve;
- what a legend or condition box says;
- whether an axis is log or linear;
- how many curves there are.

When the answers agree with each other and with the page, the reading stands, with the
answers cited. When they disagree, `unknown` stands, with the disagreement recorded.

**Never promote a model's numeric reading to a sourced value.** A load-bearing number
comes from dsdig, or from a hand digitization with stated calibration (ticks, rules,
pixel mapping). A model's number is at most a labelled estimate ("model estimate,
unverified: Astra 3.3, Qwen 3.4, Fable 3.2 mΩ") and a sanity range, never a design
input.

## 4. Review

- **Human review packets:**
  `/Users/fab/dev/pv/ee/dsdig-verify-backlog/tools/build_html_review_packets.py`
  (`--rows-jsonl` for batches outside the backlog). It gives per-chart layer toggles,
  Green/Flag/Needs-rework verdicts and a JSON export. Changes to that tool go upstream in
  that file, never into a packet-local copy.
- **Agent reviewers** follow `docs/CHART-REVIEWER-PROMPT.md` in the dsdig repo (on
  `main`). It carries the checks that caught what reviewers missed, including ticks
  measured against their rules (check 4), printed-text inventory (check 12) and source
  visibility (check 17), plus a self-test a new reviewer setup must pass.
- **An agent reviewer's FAIL is a claim to check, not a verdict.** Measured on 25 blind
  controls (dsdig `out/astra-review-50/reviewer-calibration.md`), Astra as reviewer failed
  24/24 known-bad charts, naming each defect. It passed only 6–7 of 13 known-good ones.
  Check every FAIL against the PDF render with tick labels visible before acting on it.
- A panel Fab passes becomes a golden fixture and is not shown to him again. A change to
  a golden is re-blessed explicitly, never silently.
