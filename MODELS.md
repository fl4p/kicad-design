# AI models for PCB design

Last updated: 2026-08-25. Rewritten after direct inspection of three 2026
benchmark papers (HWE-Bench, PCBSchemaGen, OmniSch); earlier web-research
content (Enginuity, Businessware, Autocuro) retained in condensed form.

This companion informs model routing only. The source-authority, verification, guard, and human
review requirements in [`SKILL.md`](SKILL.md) remain controlling regardless of model choice.

## Headline

There is no single best model — the winner depends on the task, and far more
on the **harness** than on the model:

| Task | Best measured | Evidence |
|---|---|---|
| Schematic generation inside a verifier/repair loop | Gemini 3.1 Pro ≈ GPT-5.x | PCBSchemaGen T3 |
| Raw one-shot design from datasheets (no feedback) | Claude Sonnet 4.5 — but everything ≤8% | HWE-Bench T5 |
| Reading schematics (image→netlist), agentic w/ tools | Gemini 3.1 Pro | OmniSch T3 |
| Best open-weight backbone (in a verifier loop) | Gemma-4-31B | PCBSchemaGen T3 |
| Datasheet/diagram description fidelity | Claude (Opus 4.7) | Enginuity |

PCBSchemaGen's defining result is that its task goes from ~40% (single-shot)
to ~94% (deterministic verifier + error localization + refinement loop).
In that benchmark the model is a ~97%-per-connection generator; the scaffold
produces the higher system result. Pick the harness first, the model second.

## 2026 benchmarks (primaries, inspected 2026-08-25)

### HWE-Bench — end-to-end board design (arXiv 2603.18102v1)

300 board-level tasks, 2,914 real IC datasheets. Scripted single-pass
4-stage pipeline (module partition → component assignment → module netlist →
system netlist), then post-hoc verification: static pin/protocol rule check
+ LTspice transient sim against a human-built golden testbench.

| Metric | DeepSeek-v3 | Claude Sonnet 4.5 | GPT-5.2 | Gemini 3.1 Pro | Grok-4 | Qwen3-Max |
|---|---:|---:|---:|---:|---:|---:|
| Static rules (partial credit) | 74.8% | **77.7%** | 72.2% | 73.3% | 63.5% | 69.6% |
| Dynamic sim (partial credit) | 54.2% | **67.9%** | 57.9% | 60.0% | 51.7% | 60.2% |
| Correct overall design (binary) | 5.1% | **8.15%** | 6.6% | 5.2% | 4.4% | 5.0% |

**Read the 8.15% carefully.** It is all-or-nothing over hundreds of checks
per design, with **no verifier feedback to the model** — the gates run only
for scoring. At ~97–98% per-connection accuracy, 0.98^100 ≈ 13%, so ~8%
one-shot is what "very good but never perfect, with no repair loop"
arithmetically produces. It is a floor measurement of ungated capability,
not what a gated agent delivers. Their own case study shows the dominant
errors (cross-module pin-reuse conflicts) vanish under prompt-level
constraint intervention. Caveats: their gates need per-task human-authored
golden rules/testbenches (which a novel design doesn't have), and fixed
rules can fail valid alternative topologies — so 8% has false negatives too.

Failure analysis ("lack physical intuition"): connectivity accuracy is high
for semantically-named pins (SDA, TXD, CLK, RST — pretrained conventions)
and collapses for multiplexed pins / parallel grounds; pins get reused
across modules (no global exclusivity ledger); models pass simulation more
often than static rules (functionally-equivalent ≠ physically-spec'd);
long homogeneous pin lists trigger context-retrieval collapse.

### PCBSchemaGen — verifier-gated generation (arXiv 2602.00510v2)

227 real-IC tasks. Frozen LLM + datasheet-derived schema + deterministic
5-layer continuous-reward verifier with pin-level error localization +
Thompson-sampling refinement (T=4). Pass@1, PCBBench overall / hard tier:

| Model | Overall | Hard |
|---|---:|---:|
| Gemini 3.1 Pro | **94.3** | 84.3 |
| GPT-5.4 | **94.0** | **85.1** |
| DeepSeek V3.2 | 81.6 | 53.7 |
| Gemma-4-31B (open) | 81.3 | 47.1 |
| MiniMax M2.5 | 76.0 | 36.9 |
| Gemini 3.1 Flash-Lite | 75.9 | 35.7 |
| Devstral-24B | 65.6 | 16.9 |
| Llama-4 Scout | 50.5 | 2.0 |
| Qwen3-Coder-30B | 48.8 | 5.9 |
| Qwen3.5-27B | 40.2 | 21.2 |

Held-out suite (OSE, zero verifier changes): GPT-5.4 79.3 > Gemma-4-31B
69.8 > DeepSeek 45.3 (Gemini Pro not run — API cost). Claude not tested.
Refinement gain averaged across models: +26 pp overall, +27 pp on hard.
Verifier + benchmarks public: github.com/HZou9/PCBSchemaGen_v2.

### OmniSch — schematic image → netlist graph (arXiv 2604.00270v4)

1,854 real schematics; grounding, diagram-to-graph, geometric reasoning,
agentic visual search. Zero-shot: **every** model (GPT-5.2, Claude Sonnet
4.6, Gemini) scores ~0.000–0.003 F1 reading component *values* into a
netlist; graph structure (1-GED) ≤ ~0.29 even few-shot. Agentic tool
setting without ground-truth boxes: Gemini 3.1 Pro-Preview clearly best
(0.78–0.89 attribute F1), GPT-5.2 second, Claude mid; open VLMs
(Qwen3-VL-235B, Llama-4-Maverick-400B) collapse without a detector and
recover when handed bounding boxes.

**Consequence:** no VLM can be trusted to extract values/connectivity from
schematic images unsupervised. Netlist-level review must come from the EDA
tool's netlist, not from pictures.

## Older benchmarks (2025–early 2026, condensed)

- **Enginuity** (arXiv 2606.03410v1): engineering-diagram parts extraction.
  Claude Opus 4.7 best description fidelity (2× open models); Qwen3-VL-32B
  ties on recall but paraphrases exact part names.
- **Businessware** engineering-drawing benchmark: Gemini 2.5 Pro best
  (~80%) at dimensioned-drawing extraction; GPT-4o poor.
- **Autocuro** deep-dive: VLMs cannot do geometric verification (clearance,
  routing topology) — consistent with OmniSch's stronger 2026 evidence.

## Cross-benchmark conflicts & caveats

- HWE-Bench ranks Claude > GPT > Gemini; PCBSchemaGen ranks Gemini ≈ GPT
  top (Claude untested); OmniSch-agentic ranks Gemini top. Different task
  formulations, non-overlapping lineups — no single-champion claim holds.
- All three are new and unreplicated; generic benchmark-contamination
  critiques apply (HWE-Bench tasks come from GitHub/OSHWLab). Do not treat
  gaps below 5 percentage points as decisive without uncertainty or replication.
- Local evidence (EGS002 inverter schematic-capture bench,
  `~/dev/pv/ee/pa/inverter-pcb-glm`, verified 2026-08-25): GLM-5.2's clean
  re-spawn produced a 52-component KiCad schematic that passes KiCad 10 ERC
  with 0 errors and 0 warnings. Kimi K3, Qwen3.8-Max, and DeepSeek-V4-Pro
  produced no usable output, mostly because of provider or harness failures.
  Agentic reliability, not benchmark rank alone, was the binding constraint.

## Practical picks

1. **Schematic generation / KiCad automation:** Gemini 3.1 Pro or GPT-5.x
   inside a verification loop (generate → ERC/netlist checks → repair).
   The loop is mandatory, but ERC and netlist gates do not replace
   project-specific functional checks, model validation, or human review.
2. **Design reasoning, datasheet-grounded review, written analysis:**
   Claude (best HWE-Bench end-to-end + ERC/sim rates, best Enginuity
   description fidelity).
3. **Open weights:** Gemma-4-31B (81% in the PCBSchemaGen harness — beats
   Qwen3.5-27B, Qwen3-Coder-30B, Llama-4 Scout there).
4. **Reading schematic images:** Gemini 3.1 Pro agentic with a detector in
   the loop; never unsupervised value extraction.
5. **Verification (DRC/ERC, clearances, routing):** no model. kicad-cli +
   human review, unchanged.

## Sources

Inspected directly (live DOM, abstract + results/analysis sections):

- HWE-Bench: <https://arxiv.org/html/2603.18102v1> (2026-03-18)
- PCBSchemaGen: <https://arxiv.org/html/2602.00510v2> (2026-06-17)
- OmniSch: <https://arxiv.org/html/2604.00270v4> (2026-06-05)

Context only; no recommendation above depends on it:

- PCB-Bench: live OpenReview DOM and abstract inspected 2026-08-25,
  <https://openreview.net/forum?id=Q5QLu7XTWx>

Condensed carry-over sources:

- Enginuity: <https://arxiv.org/html/2606.03410v1>
- Businessware: <https://www.businesswaretech.com/blog/benchmarking-ai-on-tables-and-engineering-drawings-results-findings>
- Autocuro: <https://autocuro.com/blog/can-llms-verify-pcb-designs>
- Practitioner cross-check (vendor): <https://www.protoflow.ai/blog/ai-pcb-design-2026-guide>
