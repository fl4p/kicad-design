# Review brief: `kicad-design`

Use this brief for the next independent review of the reusable KiCad Agent Skill. Verify the
snapshot below before relying on it; the repository and installed KiCad version can change.

## Repository and review scope

The repository contains prose instructions and executable helpers for schematic capture, PCB
layout, component selection, verification, reproducibility, and constrained autorouting. A prose
error can produce a wrong design even when every Python test passes, while a helper error can turn a
failed or incomplete check into a false PASS. Review both surfaces.

Start with:

```sh
git status --short
git log --oneline -5
git diff --check
git diff -- README.md SKILL.md SETUP.md SCHEMATIC.md PCB.md PCBNEW.md FOOTPRINTS.md \
  GUARDS.md THERMALS.md RELEASE.md scripts/ drafts/REVIEW-BRIEF.md
python3 -m pytest -q scripts/test_*.py
K=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
KP=/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
"$K" --version
"$KP" -c 'import pcbnew; print(pcbnew.GetBuildVersion())'
```

The paths above are the verified macOS installation paths for this machine. On another host, locate
and record the equivalent application CLI and bundled Python rather than substituting an unverified
bare command.

Read the changed file and every companion it relies on. Check cross-references in both directions;
a correct rule in one file does not repair a contradictory example elsewhere.

## Reviewed change set at 2026-08-23

Re-run these probes before the review:

- The independent review covered `5ce9fd2 docs: tighten KiCad design review gates` and
  `90aac2d docs: require functional geometry evidence for PCB release`.
- Audit every commit after `90aac2d` as the corrective delta; do not assume the corrections are
  valid merely because they respond to a finding.
- `kicad-cli --version` and `pcbnew.GetBuildVersion()` both reported 10.0.5.
- Recount `scripts/test_*.py` and collected tests rather than relying on a recorded total.

Do not copy these values into findings without re-running the commands.

## First priority: audit the corrective delta

Reproduce the false-PASS probes and check that the corrective delta does all of the following
without weakening neighboring workflows:

1. Parity-enabled DRC fails closed without same-stem project/schematic context, independently parses
   a fresh annotated netlist and rejects parity-load diagnostics. Treat the footprint-error category
   as report-format evidence only, and qualify execution with a real mismatched-board negative control.
2. DRC, artifact guards and exports consume one finalized zone snapshot; refill or stale-zone
   handling cannot silently create different accepted candidates.
3. A canonical release manifest binds project/rule files, hierarchy, resolved libraries, generator,
   variants, route/domain inputs, tool versions, waivers and outputs—not only root schematic/board.
4. Manufacturability, functional validation, revision finality and order authorization remain four
   independent states; an experimental waiver cannot convert `UNVERIFIED` into functional `PASS`.
5. Board, external construction-model and physical thermal evidence have explicit carriers and
   authorities; unrepresented paths remain `UNVERIFIED`.
6. Schematic calibration covers every supported family/dispatch/polarity/package branch.
7. Intentional board-only footprint-hosted `Edge.Cuts` remain legal under explicit mechanical
   authority while unowned proxies fail.
8. Provisional pre-gate PCB work is limited to outline/mechanical studies independent of unresolved
   connectivity.
9. DRC persists its refill on the scratch board and a complete reparsed semantic snapshot must equal
   the provisional finalizer snapshot; zero DRC counts cannot override a mismatch. Bind the
   authoritative board digest only after the DRC save and comparison.
10. A canonical output receipt hashes every report and deliverable, and order authorization binds
    the receipt digest rather than only an input manifest.
11. Direct and footprint-hosted `Edge.Cuts` both reach outline audits and autoroute nonrouting
    snapshots; mutate a footprint contour and require the snapshot digest to change.
12. Parity compatibility uses a real same-stem annotated negative control; the footprint-error
    summary alone is not claimed as proof that parity executed.
13. Snapshot v5 dispatch covers complete segment, rectangle, arc, circle, polygon, Bézier and text
    geometry for direct and footprint-hosted board graphics, enforces exact fields per KiCad object
    kind and fails closed on unsupported classes. Mutate Bézier controls and polygon holes while
    preserving endpoints and require both snapshot and identity digests to change. Add a field from
    another graphic kind or remove `IsLocked` and require validation to fail. Swap two distinct
    object UUID associations independently for pads, routes and zones and require each swap to fail
    at the exact UUID-to-semantic binding.
14. Snapshot schema is validated at every worker boundary and carried through seed/candidate report,
    compatibility cell and route manifest. Promotion stays disabled until full v5 requalification.

Look for neighboring claims introduced with the fix, especially board-only workflows, global
library resolution, project-variable overrides and external construction models.

## Second priority: false-PASS behavior

For each described or implemented check, ask:

1. What happens when an input is missing, stale, empty, unparseable, or outside the supported
   domain?
2. Does the result move toward failure or `UNVERIFIED`, or can worse input return PASS?
3. Does the check prove that it examined a nonempty subject population?
4. Does a cache or digest cover the code and inputs that derive the result?
5. Can a failed check persist a stale success result?
6. Does the provenance claim exactly what was run?
7. Has the check been calibrated with both a known-bad and a legal input?
8. Does the check establish the named physical function, or only confirm that the generator's
   expected geometry is present? For a load-bearing barrier or path, require a geometry-preserving,
   DRC-clean mutation that defeats the function while leaving the proxy checks green.

Also look for checks that cannot fail on the examples used to justify them. Dead checks and
anti-monotone checks are the same release risk.

## Third priority: current toolchain facts

Re-run rather than recall claims about:

- KiCad CLI exit behavior, report content, serialization, and netlist shape;
- `pcbnew` API return types, units, layer order, and zone-fill behavior;
- stock symbol and footprint geometry;
- current ERC/DRC default severities;
- external-router input, output, and project-file mutations;
- standards, fabrication capabilities, lifecycle, and distributor availability.

Every numeric clearance, creepage, or conductor-spacing standards claim must identify revision,
table, column, voltage band, actual voltage, and applicable coating or environmental assumptions.
For other standards claims, record the revision and the controlling inputs relevant to that claim.
Treat vendor capabilities and catalogue state as dated facts.

## Fourth priority: source authority and cross-document consistency

Check that each workflow preserves the declared authority:

- generated designs are edited through their generator;
- hand-maintained designs are not silently converted into generated ones;
- diagnostics operate on scratch copies;
- transformed or autorouted boards remain candidates until explicitly promoted;
- release evidence is bound to the exact artefact and becomes stale when an input changes;
- examples in companions do not weaken the invariant stated in `SKILL.md`.

Search the complete repository after correcting a term, number, command, or ownership rule. A local
fix is incomplete while another live representation states the old contract.

## Current known limitations

Confirm these, but do not report them as new unless the current text understates their impact:

- `scripts/kicad_verify.py` states that `KNOWN_STOCK_IGNORES` was measured on KiCad 9.0.4 and must
  be re-verified before use with the installed version.
- `scripts/README.md` leaves `transform_pin()` unverified until calibrated against KiCad for the
  project's supported transform cells.
- Autoroute promotion is disabled for all cells pending snapshot-v5 and parity-negative-control
  requalification. A cell must not be re-enabled with the invalidated pre-v5 evidence.
- `FOOTPRINTS.md` intentionally supplies no authoritative numeric IPC-2221C verdict. A project must
  provide the binding current standard and complete derivation.

## Superseded findings from the old brief

Do not re-report these without new evidence:

- The repository has a collected test suite; the old “no test suite” statement is no longer current.
- `check_rail_orientation` is no longer present in the current skill or helper code.
- The PDF exact-MPN example uses `rg --fixed-strings` rather than a regex MPN search.
- The browser preflight and persistent-context example both currently use headless Chrome.
- Current reproducibility and report-freshness helpers use `st_mtime_ns`.
- The old gain-percentage, 1210-versus-0805, silkscreen-minimum, and “smallest 100 nF/250 V X7R”
  claims are absent from the current core documents.
- IPC-2221B figures are no longer presented as IPC-2221C pass/fail values.

## Review output

Make no edits during the review. For every finding:

- label it `VERIFIED` when supported by a command, current source, or authoritative document;
- label it `SUSPECT` when it is reasoned doubt that still needs a probe;
- cite `file:line`;
- state the failure mode and a concrete correction;
- rank it by likelihood of causing a wrong design or false release verdict.

Include a short “verified correct” section so later reviews do not repeatedly challenge settled
behavior. Report the commands run and any surface that could not be checked.
