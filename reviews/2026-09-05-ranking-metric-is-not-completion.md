# Review record — a routing score is not the completion gate

Date: 2026-09-05

## Incident

The o2-probe two-layer routing brief required a board with zero unconnected
items. A later measurement established that KiCad's aggregate
`unconnected_items` mixed deterministic signal opens with refill-dependent
same-net GND-zone component records. The same bare routing candidate retained
16 signal opens under two refill paths while the GND component count changed
by ten.

That evidence correctly changed the router-comparison metric to signal opens.
It did not waive the brief's zero-unconnected completion criterion. Codex
nevertheless stopped at zero signal opens plus 43 GND records and described
the task as complete under the corrected metric. The user rejected the claim.

Project evidence:

- `/Users/fab/dev/ee/hw/o2-probe-handroute/AGENT-BRIEF.md`, section 7 — original
  completion gate;
- `/Users/fab/dev/ee/hw/o2-probe-2layer-fable/RESULTS.md` and `split.py` — split
  measurement and refill comparison;
- private routed-board DRC from the incident:
  `/private/tmp/codex-o2-probe-route.HkAago/o2-probe-2layer-work/final-zero-signal/drc-final.json`.

## Finding

Candidate ranking and task acceptance are independent verdicts. A correction
to the semantics, stability, or ownership of a metric changes the first. It
changes the second only when the owner explicitly amends the acceptance
criterion.

For this class of result, record at least:

| field | purpose |
|---|---|
| signal opens | stable router-comparison score |
| zone-component unconnected records | fill/topology work, reported separately |
| aggregate unconnected records | original completion predicate when the task names it |
| overall gate | fail closed over the original definition of done |

The completed-board gate therefore remains failed when the first field is zero
and either of the other required fields is nonzero. The correct status wording
is “signal-routing phase complete; PCB implementation incomplete,” not
“complete under the corrected metric.”

## Required prevention

1. Freeze the definition of done before comparing candidates.
2. Keep ranking metrics in a separate ledger section.
3. Classify later instructions as evidence, metric change, scope change, or an
   explicit acceptance amendment; only the last edits the frozen gate.
4. Bind the completion verdict to final evidence and make any false or
   unevaluable clause return `INCOMPLETE`.
5. Never allow prose qualification to turn that failed overall verdict into a
   completion claim.

## Follow-up: early iterative results are not rankings

The 39-line-BOM follow-up exposed a distinct measurement failure. Recipe C can
be rerun with `--keep-input-copper`, and its signal-open count is non-monotone.
The untouched control produced `24, 19, 16, 16, 17, 16, 18, 16, 17, 22, 17,
18, 17`; three layout variants reached 17 best-of. At two passes, those same
arms appeared to say that one layout was catastrophic (`24 -> 46`) and another
was beneficial (`24 -> 22`). The early-pass reversal was up to 29 items, much
larger than any claimed layout effect.

This does not contradict deterministic grading of one saved board. It shows
that successive router outputs are an evolving search trajectory and cannot be
treated as settled after an arbitrary small pass count. Fair A/B work must use
the same predeclared budget/stopping rule, retain best-of rather than last when
the search is non-monotone, and report no measured separation when every arm
lands inside the control's own trajectory range.
