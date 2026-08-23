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
git diff -- SKILL.md SETUP.md RELEASE.md FOOTPRINTS.md PCB.md GUARDS.md scripts/ drafts/REVIEW-BRIEF.md
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

## Verified baseline at 2026-08-20

Re-run these probes before the review:

- Git HEAD was `6ef7e99 docs: prefer qualified inventory parts`.
- The five most recent commits were:
  - `6ef7e99 docs: prefer qualified inventory parts`
  - `0521815 docs: plan region-scoped KRT rules`
  - `14e7f2d docs: tighten routing and variant guidance`
  - `79c8260 docs: make KiCad checks project-aware`
  - `4f78b36 docs: refocus kicad design skill`
- `kicad-cli --version` and `pcbnew.GetBuildVersion()` both reported 10.0.5.
- Four `scripts/test_*.py` files collected 65 tests.
- The core documents were 301 lines in `SKILL.md`, 623 in `PCB.md`, 267 in `SETUP.md`, and
  320 in `GUARDS.md` before the current working-tree edits.

Do not copy these values into findings without re-running the commands.

## First priority: audit the current diff

The current inventory-guidance work extends `6ef7e99`. Check that it does all of the following
without broadening unrelated tasks:

1. Evidence class follows the record's provenance rather than the application that stores it.
   Imported order-history quantities must not become physical-stock claims merely because they are
   represented as InvenTree stock items.
2. Exact-MPN and family absence checks cover every fully paginated identity-bearing search surface
   that the interface exposes, preserve unavailable or failed surfaces, and label the conclusion
   exhaustive or scoped within the declared source. An alias-only search must not establish
   absence.
3. Inventory candidates are classified as exact replacements, requirement-preserving value or
   package changes, topology-changing alternatives, or unsuitable.
4. Power conversion, voltage regulation, voltage supervision, series load switching, gate drive,
   and isolation remain distinct circuit roles and requirement sets. A multifunction part may
   satisfy several verified roles, but a part that changes the power architecture must not be
   called a drop-in substitute.
5. Inventory preference remains subordinate to electrical, mechanical, thermal, safety,
   lifecycle, condition, and available-to-project requirements.
6. The workflow still avoids credentials, authentication, and account-scoped access that the user
   did not authorize.

Look for neighboring claims introduced with the fix. In particular, test whether the new wording
works for inventory systems whose schemas expose equivalent information under different record
names.

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
- Autoroute promotion is limited to exact compatibility cells in
  `kicad-autoroute-compatibility.json`; other environments remain report-only.
- `FOOTPRINTS.md` intentionally supplies no authoritative numeric IPC-2221C verdict. A project must
  provide the binding current standard and complete derivation.

## Superseded findings from the old brief

Do not re-report these without new evidence:

- The repository now has four test files and 65 collected tests; the old “no test suite” statement
  is no longer current.
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
