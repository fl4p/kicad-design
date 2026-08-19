# Generator guards, audits and calibration

Read this reference when writing or reviewing a generated KiCad schematic or board, a
domain-specific validator, or a calibration harness. KiCad's ERC/DRC and the verification
ladder in [`SKILL.md`](SKILL.md) still apply; this file covers the checks the generator owns.

## Classify every check by what it can observe

Do not put arithmetic, construction-model checks and emitted-artifact checks in one anonymous
`GUARDS` list. They answer different questions:

| tier | reads | can establish |
|---|---|---|
| `LEDGERS` | constants, limits and equations | the design arithmetic is internally consistent |
| `MODEL_GUARDS` | the generator's in-memory instances, nets, labels, segments and placement | the construction model is internally consistent |
| `ARTIFACT_GUARDS` | a saved and reparsed schematic/PCB or an exported netlist/report | the emitted artefact has the required property |

An `INST` or `NET_OF_PIN` table populated by the generator is still the generator's intent. It is
not an emitted artefact merely because `emit()` will consume it later. Run the tiers in order:

```text
build model -> LEDGERS + MODEL_GUARDS -> emit/save -> reparse/export -> ARTIFACT_GUARDS
```

Keep the tier visible in code and in the verification report. A static check that a function
mentions a model container is useful lint, but it is not proof that the guard observes its own
subject; the access may be incidental or may read the wrong object class.

For each load-bearing check, record at least:

| field | purpose |
|---|---|
| stable failure ID | distinguishes the intended refusal from another branch failing first |
| tier and subject | states what the check can actually observe |
| preconditions and coverage | makes empty, partial or ambiguous input fail closed |
| bad mutation | proves the intended failure mechanism is detected |
| legal mutation | proves valid variation remains accepted |

## Make the subject chain reach reality

- If a bound is provided by a component, verify that the component exists, has the required
  connectivity and carries the relevant value or MPN. Arithmetic over its datasheet constants
  proves intent only; a board with the part missing can otherwise pass.
- Enumerate the object classes the property depends on and the classes the parser actually
  visits. Text, symbol properties, labels, pads, tracks, vias, zone settings, zone fills and
  drawing-sheet items are separate classes. A calibration built from a class already visited
  cannot expose an omitted class.
- Derive names, membership, layer sets, endpoint nets and inventory counts from the model or
  reparsed artefact that defines them. A literal name must assert its subject exists; prefer a
  structural identity such as a refdes/pad relation when a rename should not require a code edit.
- Make preconditions executable. An empty parse, zero candidates, a missing zone pair or an
  ambiguous mask is `UNVERIFIED`, never `PASS`. Report the number of subjects examined beside the
  verdict.
- If a DRC rule, rule area or exemption is a guard precondition, independently remeasure the
  shipped geometry. A position-dependent rule can stay green after the object moves into a more
  permissive region.
- Scope exemptions to the exact object pair and mechanism that justify them. Bound the exemption
  itself so a new object cannot inherit it accidentally.
- Derive thresholds from the design requirement, not from the value the current artefact happens
  to achieve. A datasheet absolute maximum is not a design limit; apply the intended derating or
  error allocation. Enforce a cited compliance floor exactly, not floor minus an epsilon chosen to
  make current geometry pass.
- Compute every derived numeric limit in code from its authoritative inputs. Do not copy the
  evaluated result into a guard: the hardcoded number will stay plausible when a source input
  changes.
- Reject out-of-range requests instead of silently clamping them; clamping can make two requested
  setpoints produce one plausible-looking result.
- Use explicit exceptions or result objects, not load-bearing Python `assert` statements. Refuse
  optimized execution when legacy guards still depend on `assert`.

## Calibrate detection and acceptance

A guard is calibrated only after both directions are exercised. Use this sequence:

1. Run the unmodified baseline and require it to pass.
2. Mutate the guard's own subject into a known-bad state.
3. Prove the mutation actually created that state, then require the guard's stable failure ID.
4. Restore the subject and require the baseline to pass again.
5. Apply a legal subject mutation and require the guard to stay quiet.
6. Restore and run the baseline once more so a failed cleanup cannot poison later checks.

For a threshold guard, the known-bad mutation must include a plausible value just beyond the
intended boundary, and the legal mutation must include a value just inside it. Grossly invalid
values such as zero, full scale, infinity or `NaN` may test parser and arithmetic behavior, but
they do not prove that the intended threshold is current or correctly placed.

Match the mutation to the tier:

| tier | calibration mutates |
|---|---|
| `LEDGERS` | an owned constant or input corner |
| `MODEL_GUARDS` | the relevant construction-model feature, such as removing a component or one of its connections |
| `ARTIFACT_GUARDS` | a scratch copy of the emitted and reparsed artefact |

Calibrate each mechanism and branch, not merely each variable the implementation happens to read.
The injection must land in the guard's active region, outside its exemptions, and must be restored
in `finally`. If another branch fires first, the calibration failed even when the exception type
is right. Prefer a stable ID carried separately from prose; messages are for humans and may change.

When a real defect is the only thing exercising a branch, write and observe that branch's
calibration before fixing the defect. The evidence expires when the real fault disappears.

When the baseline already contains the fault, make the calibration differential: measure before
and after, require the intended count or geometry to change by the injected amount, and require the
report to identify the injected subject. A calibration whose assertion also passes without the
injection is decorative.

For a small discrete parameter space, enumerate every cell against independent ground truth. A
perfect score on the current design says nothing about rotation, mirror, layer, package or unit
branches the design never exercises.

For threshold checks, measure valid-domain headroom with legal variants such as supported board
variants, build order and tool-version/fill behavior. Do not call this a false-positive *rate*
unless an input distribution and sampling method are defined; normally the useful quantities are
noise floor, margin and the legal range exercised.

## Zone fills need semantic finalization

Zone fills are cached derived geometry. Byte-identical generation and a fill fixed point are
different properties: serialization may change while geometry does not, and a differential area
may stay constant while both pours move together.

For a generator-owned board:

1. Canonicalize stable identities and item order before filling.
2. Fill once so orphaned islands and other derived conditions can be discovered.
3. Apply any legal island stitching or other fill-dependent edits.
4. Refill unconditionally outside the branch that happened to find edits.
5. Snapshot each zone's filled geometry, refill the same loaded board once more, then pair zones
   by stable semantic identity and require an empty per-zone geometric symmetric difference
   (`BooleanXor`) with unchanged topology. Refuse ambiguous zone pairing.
6. Save only after that in-memory semantic-settle gate passes.

Do not iterate until scalar `Area()` stops changing: equal areas can hide different shapes, and
repeated filling may converge on a different geometry rather than the required property. Do not
assume that save/reload/refill has the same fixed point. Measure that cycle on a scratch copy and
report zone motion and any guard verdict it flips, but do not gate it when the pinned KiCad build
has a demonstrated limit cycle. Promote save/reload stability to a gate only after a pinned
finalizer has demonstrated that the property is reachable. Never use this diagnostic cycle to
overwrite the tracked artefact; record the KiCad build and finalizer used.

Keep these results separate:

- byte reproducibility across clean generator runs;
- per-zone in-memory semantic settling;
- save/reload/refill stability and any observed cycle;
- individual raw areas and other absolute quantities; and
- differential quantities such as P minus N.

A differential check cannot see a common-mode fill change. If absolute copper is load-bearing,
give it an independent bound or report rather than inferring it from a stable difference.

## Matched copper needs shape and quantity gates

Use this pattern only when mirrored or matched filled copper carries an electrical or thermal
requirement. DRC and placement symmetry do not establish it.

**Gate A — artifact-derived residual shape.** Validate the expected feature inventory and topology
before masking anything. Derive the allowed-asymmetry mask from actual placed pads, tracks, vias
and rule geometry in the reparsed artefact, never from design constants alone. Fail closed when
derivation is incomplete or ambiguous, and cap both total mask area and each connected component.
Then evaluate the unexpected mirrored symmetric difference, connected components and boundary
displacement. Calibrate with missing-feature, neck and island faults, plus legal variants and fill
behavior that must remain quiet.

**Gate B — unmasked raw quantity.** Keep the individual raw totals and their difference independent
of Gate A; never apply the allowed-asymmetry mask to this gate. Total area is a scalar proxy, not
the physical property itself, so derive its limit from an allowed temperature, resistance, error
or other physical budget with sensitivity to where copper is removed. Report each side as well as
the delta so common-mode movement stays visible.

Shape and area cover different failure classes: equal areas can hide a neck or severed region,
while a masked shape residual can hide gross copper loss inside the allowed region. Land Gate A
before loosening an existing area gate. Until Gate B has a derived limit, retain the existing
fail-closed limit or require an explicit recorded waiver; do not silently tune a threshold to the
current board.

## Reporting and review hygiene

- Emit stable failure IDs, tier, subject count, observed value, limit, margin and units. Keep the
  comparison in one native unit and test any display conversion with a known value; a correct
  comparison with a mislabeled unit still misleads the operator.
- Treat `PASS` over empty or unparsed input as a harness failure. Treat a result that condemns
  everything with the same suspicion; uniform output often means the probe is wrong.
- When retracting a claim, search source, runtime output and current published artefacts for the
  distinctive number as well as the prose. Search normalized whole-file text when line wrapping
  is possible; a single-line grep misses phrases split across Markdown or generated HTML lines.
- If a search reports absence, prove its parser or pattern could have matched the relevant object
  class and formatting. A bounded or line-oriented search can return convincing partial data.
- Preserve dated reviews as historical records. Mark findings resolved or superseded and bind
  current reports to the artefact digest they describe.

## Review checklist

- [ ] Every check has a visible tier, subject and stable failure ID.
- [ ] Artifact claims come from emitted and reparsed data, not only generator containers.
- [ ] Preconditions fail closed and the report states coverage counts.
- [ ] Each mechanism has a subject mutation that triggers the intended failure ID.
- [ ] Each threshold or variant-sensitive guard has a legal mutation that stays quiet.
- [ ] Mutations restore state and the baseline passes before and after calibration.
- [ ] Names, layers, memberships and inventory counts come from one structural authority.
- [ ] Geometry guards cover every relevant object class and bound their exemptions.
- [ ] Zone-dependent guards run only after unconditional final fill and in-memory semantic-settle proof.
- [ ] Matched copper uses independent artifact-shape and unmasked-quantity gates when required.
- [ ] Reports use correct units and distinguish absolute, differential, semantic and byte evidence.
