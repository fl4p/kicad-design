# Generate board variants without moving the incumbent

Read this reference when one generator must emit a depopulated, regional, connector, assembly, or
other board variant. Apply the source-authority rules in [`SKILL.md`](SKILL.md) and the check tiers
in [`GUARDS.md`](GUARDS.md).

Treat the qualified incumbent's bytes and reports as regression evidence. A variant may add a new
artefact; it must not silently redefine the existing one.

## Contents

- [Choose native or generated variants](#choose-native-or-generated-variants)
- [Choose an artefact layout from the toolchain contract](#choose-an-artefact-layout-from-the-toolchain-contract)
- [Resolve configuration before side effects](#resolve-configuration-before-side-effects)
- [Freeze the incumbent](#freeze-the-incumbent)
- [Derive every variant-sensitive fact](#derive-every-variant-sensitive-fact)
- [Keep pinned files out of the variant](#keep-pinned-files-out-of-the-variant)
- [Verify against a copy, never the live artefact](#verify-against-a-copy-never-the-live-artefact)
- [Make guards variant-aware](#make-guards-variant-aware)
- [State the evidence a variant removes](#state-the-evidence-a-variant-removes)

## Choose native or generated variants

Use KiCad's native assembly-variant and DNP mechanisms when the difference is population or BOM
selection and the installed KiCad version can produce every required output consistently. Inspect
the current CLI for `--variant` and `--exclude-dnp` support on BOM, position, Gerber, and other
release exports, then bind the chosen variant name into the release evidence.

Use generator-level variants when the choice changes schematic topology, nets, copper, board
geometry, libraries, guards, or another artefact that native population filtering does not own. Do
not duplicate topology in generator conditionals merely to reproduce a native DNP selection, and
do not rely on native assembly variants to remove electrical connectivity they leave in the design.

## Choose an artefact layout from the toolchain contract

Do not prescribe one universal directory arrangement. Before choosing same-directory filenames,
sibling project directories, or another layout, inspect and test:

- project-relative library URIs and whether parent traversal is permitted;
- the input-set builder and whether it attests shared generator/library files;
- same-stem schematic, project, report, and board assumptions;
- route-manifest and release-output paths;
- whether shared libraries are immutable inputs or variant outputs.

Select the simplest arrangement the complete toolchain can attest hermetically. Assert that every
variant resolves the intended library and generator inputs and that no input set is empty. Keep
library identities stable when the physical land is unchanged; fork a library only when its
contents or ownership genuinely differ.

Allow an unfitted symbol definition to remain in an embedded symbol library when no instance, net,
or BOM row uses it. Document the cosmetic surplus rather than forking an otherwise identical
library.

## Resolve configuration before side effects

Do not make module identity depend on ambient `sys.argv` or environment state at import time:

1. Parse the variant strictly before importing modules that construct the design.
2. Reject unknown values and conflicting CLI, configuration-file, or environment selections.
3. Build one immutable configuration object containing the name, feature set, output paths, and
   variant-only identity seeds.
4. Pass the configuration explicitly into generator modules.
5. Forward it explicitly to every subprocess; do not rely on inherited environment alone.
6. Make every audit verify that the supplied artefact identifies the same variant as the active
   configuration.

A mismatch must refuse before reporting design findings. An audit run against the wrong variant can
produce plausible geometry for a configuration the board never represented.

## Freeze the incumbent

- Preserve the incumbent's established UUID and canonicalization seeds. Introduce variant-specific
  seeds only on the new branch.
- Keep separate cryptographic digests for schematic, board, manifest, and release outputs. Bind
  each digest to its named artefact.
- Regenerate the incumbent before evaluating the variant and require the expected byte result or a
  deliberately reviewed migration diff.
- Run the same incumbent regression again after variant generation so shared mutable state cannot
  hide an ordering-dependent change.

Do not predict whether a shared refactor is harmless. Measure the incumbent bytes, semantic
reports, and emitted interface contract.

## Derive every variant-sensitive fact

Search for the project name, feature names, output paths, reference lists, group membership, and
hardcoded counts. Route each through the immutable variant configuration or through a function that
derives the emitted set.

Common surfaces include:

| surface | required treatment |
|---|---|
| title block and project identifiers | derive from the active configuration |
| board, manifest, netlist, ERC, DRC, and report paths | construct from one output authority |
| `.kicad_pro` snapshot/restore | bind to the configured project path, never a base literal |
| placement groups | require membership appropriate to the active feature set |
| component, channel, net, or guard counts | derive from the emitted inventory once |
| symbol and footprint libraries | assert the resolved identity and digest per variant |

Make schematic notes, connector annotations, assembly options, and silkscreen variant-aware. ERC,
DRC, and topology checks do not prove prose is true. After changing any rendered string, remeasure
its actual KiCad extent and render the result; equal character count does not establish equal width
or clearance.

## Keep pinned files out of the variant

A promoted-route manifest, lockfile, or signature that covers **source** pins those files by
content digest. Adding a build flag to a pinned file changes its digest and breaks the
*incumbent's* reviewed path: the qualified board stops regenerating, and the refusal names the
pinned artefact rather than the flag, so the cause is not visible in the message.

**The invariant is that every intentional change to a pinned input requires reviewed
re-promotion.** A side module is a way to avoid changing one pinned file; it is NOT a way to keep
the manifest valid in general, and prescribing it as one is wrong. Where the input bundle is built
by globbing (e.g. every `*.py` in the project root), a NEW module is itself a new expected input,
so the applicator pin survives while the overall manifest does not. Enumerate what the bundle
actually covers before assuming either outcome.

- Where the pin is per-file and the bundle is explicit, put the variant's view of a pinned value in
  a separate module beside the pinned file: the pinned file keeps its reviewed bytes, and only
  non-manifest code reads the derived view.
- Where the bundle is derived by pattern, accept that the variant requires re-promotion and plan
  for it, rather than shipping a workaround that looks like it preserved the manifest.
- Enumerate what the manifest pins before editing anything. An input bundle normally covers the
  generator sources, the schematic, the netlist, and the project file, not just the applicator.
- Assert the restored digest equals the manifest's recorded value; do not infer it from a passing
  run that may not have reached the check.

Establish whether a manifest is stale **because of this change** by comparing its pinned digests
against the committed revision as well as the working tree. A manifest promoted against inputs that
no longer exist in the repository is a pre-existing defect; report it as one and do not re-promote
a reviewed seed to clear it.

## Verify against a copy, never the live artefact

A verification entry point that regenerates before checking will overwrite the artefact it is
verifying. When the purpose is to *audit an existing* artefact, redirect its board and report paths
to scratch, or copy the artefact first. (When the entry point's documented contract IS
generate-then-verify — a release build — overwriting is the intent; the rule below about output
paths still applies.)

- Variant-key every **output** path, not only the inputs. A report path left at the incumbent
  default lets the variant overwrite the incumbent's verification evidence with its own verdict —
  the one corruption that reads as a legitimate result rather than a crash.
- A check that cannot apply to the active variant is a third state, not `True` and not `False`.
  Folding it into a conjunction fails the board on an inapplicable check; coercing it to `True`
  claims a check that never ran. Name it in the report as not applicable, with its reason, and keep
  an empty check set a failure rather than a vacuous pass.

## Make guards variant-aware

A variant often removes the subject a guard expected. Prevent vacuous success:

- Give the incumbent an existence guard proving each required subsystem is placed, powered, and
  connected.
- Give the removing variant a positive absence guard over references, nets, BOM rows, and parasitic
  paths that must be gone.
- Derive the expected cardinality from the active configuration and assert it before iterating. Do
  not encode counts copied from one board.
- Replace calibrations that require a removed physical feature with a variant-specific absence or
  injected-ghost calibration.
- Run every incumbent calibration unchanged on the incumbent, and run the complete applicable set
  on every variant.
- Require audit reports to name the variant and bind the inspected artefact digest.

Removing a guard from a list is not proof that its subject was removed. Establish both sides from
the emitted artefacts.

## State the evidence a variant removes

Record in the design and release documents:

- each detector, diagnostic, protection, or calibration path removed;
- the states for which it was the only evidence source;
- bring-up or release steps that can no longer run;
- any compensating control and the states it does not cover;
- open blockers inherited from the incumbent;
- findings closed only because their physical subject is absent on this variant.

Do not report a variant as more releasable merely because it contains fewer parts. Separate a
failure mode from the evidence that detects it, and make variant-only closures explicit.
