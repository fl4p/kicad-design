# Scoped external autorouting

Operational companion for projects that have opted into external autorouting under the
routing-ownership policy in [`PCB.md`](PCB.md): the exploratory/critical/routine classification,
the structures that must stay critical, and the transactional checkpoint discipline live there.
Classify ownership first; this file owns the workflow that turns a routine-scope candidate into
promoted, manifest-bound routes.

## Contents

- [Scout first, then author the critical skeleton](#scout-first-then-author-the-critical-skeleton)
- [Pin the router's configuration, not just its binary](#pin-the-routers-configuration-not-just-its-binary)
- [Disable fanout, or expect stubs narrower than the class width](#disable-fanout-or-expect-stubs-narrower-than-the-class-width)
- [A router's DRC-clean result is not a design-conformant result](#a-routers-drc-clean-result-is-not-a-design-conformant-result)
- [Check that each constraint reaches an input the router consumes](#check-that-each-constraint-reaches-an-input-the-router-consumes)
- [Diff the project file after any external router runs](#diff-the-project-file-after-any-external-router-runs)
- [Choose the backend from behaviour](#choose-the-backend-from-behaviour)
- [KiCadRoutingTools (KRT): a native-format backend, measured](#kicadroutingtools-krt-a-native-format-backend-measured)
- [Inputs required for a promotable run](#inputs-required-for-a-promotable-run)
- [Onboard with the v2 scaffold](#onboard-with-the-v2-scaffold)
- [Run the candidate-and-promotion pipeline](#run-the-candidate-and-promotion-pipeline)

## Scout first, then author the critical skeleton

An exploratory route may include critical nets only as a congestion probe. Use
its existence, corridor choices, via hotspots, and failures to revise placement
or plan the critical skeleton; discard the trace geometry itself. Then author
critical routing, planes, keepouts, and their audits, then emit the deterministic
seed for the promotable routine scope. The project seed audit must prove each
declared critical route group is present with its required geometry. The wrapper
then locks every existing route only in the scratch export board and proves that
DSN represents it as fixed copper; scratch lock bits never enter the manifest or
final board. This order gives critical structures first claim on space while
still using the router as an early floor-planning instrument.

## Pin the router's configuration, not just its binary


A route manifest that records the router's SHA-256, its JRE and a compatibility cell still does not
make the routes reproducible. Measured on one board with the pinned jar and one unchanged DSN:
**62 track-width errors with the fanout stage enabled, 0 with it disabled.** Same binary, same
input, materially different copper. Whoever re-runs the seed gets whatever their local router
config happens to say.

Record the routing settings that change geometry in the manifest alongside the binary digest — at
minimum the fanout, neckdown, pass-count, thread and clearance-source values — and have the
verifier compare them, so a differently-configured re-run is a refusal rather than a silent
divergence.

## Disable fanout, or expect stubs narrower than the class width


On Freerouting **2.3.0**, with the configuration below, the **fanout stage sized pad escape stubs
independently of the net class**: a class declaring 0.2 mm got 0.15 mm stubs — below the board minimum, so every one is a
DRC error. They are easy to misread: they sit next to vias, a few per net, scattered across many
nets, which looks like congestion-driven narrowing rather than one stage applying its own policy.

- The setting is `router.fanout.enabled` — settable **either** in `freerouting.json`
  (`FanoutSettings`, `@SerializedName("enabled")`) **or** on the command line as
  `--router.fanout.enabled=false`. Both were observed working on 2.3.0.
- **Neckdown is a different thing, and on this board it was not the cause.**
  `router.automatic_neckdown` and `router.neck_width_um` are real settings; disabling both changed
  nothing, and only fanout did. Do not diagnose this from a filename in a repo or from the
  plausibility of the word — and do not generalise this attribution to another board without
  re-measuring, because both stages can undersize copper.
- The router's own log names the stage: `Fanout pass #1 ... N SMD pins fanouted, +M extra vias`.
  Read it before changing settings.

**Read the router's OWN log file, not your console redirect.** Freerouting confirms each accepted
override at DEBUG level — `Applied CLI router setting: router.fanout.enabled = false` — in the file
named by `--user_data_path`, which a `> log 2>&1` redirect does not contain. An investigation that
reads only the redirect sees silence and concludes the flag was ignored; that mistake cost a false
"the tool silently drops unknown settings" claim here. Confirm a setting took effect from the
`Applied CLI router setting` line AND from the output itself (SES wire widths, stage lines) — never
from the exit code.

## A router's DRC-clean result is not a design-conformant result


Run the project's own audits over any external router's output before believing it. DRC enforces
what has been *serialized* into rules it can read; it cannot know a barrier that exists only as
design intent, or any requirement that lives in the generator rather than in the board. A barrier
encoded as native keepout or custom-rule geometry **is** enforceable by DRC — the gap is
serialization, not capability.

Measured on one 4-layer isolated board, whole-board scout: an external router returned **0 DRC
violations and 0 unconnected**, routed every net including four gate escapes a second router could
not complete — and the project audit refused it on its **first** check, with **35 items of copper
inside the 4 mm galvanic isolation barrier**, host-side `+5VH` and isolated-side `GND_OUT` in the
same gap. KiCad scored that board clean because nothing had asked it about the barrier (see
*Isolated designs* in [`PCB.md`](PCB.md)).

The converse also holds, so neither layer subsumes the other: on the same project a **dangling via
on bare laminate passed all 11 audits** and was caught only by DRC. Report which layer produced a
verdict; "clean" without naming the checker is not a claim.

Read a router's failure honestly, too — but do not moralise it. A **reported** unrouted connection
is easier to reject than a silent violation, because it arrives labelled; that is a statement about
visibility, not about safety. Both are release blockers, and an open gate-drive, interlock,
shutdown, sense or return connection can be *more* hazardous than some constraint violations if the
downstream process does not fail closed. Do not infer motive either: a failed search is not
evidence that the router declined something as illegal, particularly when the constraint was never
given to it.

## Check that each constraint reaches an input the router consumes


The boundary that matters is not "board geometry" — it is **whether the constraint was serialized
into an input the chosen router actually consumes.** A constraint never serialized into any
consumed interface is invisible to the external tool; that is a property of the design, not of the
router.

Know what each carrier can hold. DSN is router *input* and can carry exported netclass widths and
clearances plus native keepouts. SES is returned routing, not a constraint authority. A `.kicad_dru`
expresses far richer KiCad rules but reaches an external router only if that router parses it.
Netclasses cannot encode a regional barrier at all. A generator may also emit router-specific
configuration directly, which is sometimes the only carrier that fits.

A rule area constrains only what its flags say. Measured on one board: **16 rule areas, every one
fill-only** — `noFill=True`, `noTrack=False`, `noVia=False`. They stop zone fill under the FET
bodies and permit tracks and vias everywhere. Its exported DSN carried 20 `(keepout` records, all
on one layer; the two counts are not the same population and were not traced to each other. The
routing constraints that mattered (layer restriction, bounded escape stubs, entry-band exclusion,
isolation barrier) existed **only as Python in the generator**.

If external routing is intended, do **not** hand-author the rule areas alongside the generator's
own constraint logic: two independently written expressions of one constraint will drift, and the
drift is invisible until a board ships. Keep **one authoritative constraint model** and derive from
it (a) the emitted board keepouts, custom rules or router configuration, and (b) the audit's
expectations. Verify after serialization and reload that the emitted carrier still says what the
model says, then produce and inspect the exact DSN, router configuration, or other input the chosen
router consumes. Check boundaries, layers, flags and precedence there, not merely that some rule
area exists in KiCad. Calibrate the consumed-input path with a known-bad route that the constraint
rejects and a legal route that remains accepted; a board reload alone does not prove enforcement by
the external router.

Preserve parity-sensitive board data exactly through the transformation: root-sheet net names keep
KiCad's leading `/`, footprint instance paths remain attached to the same instances, fitted/DNP and
BOM flags and custom fields remain unchanged, and one-pad `unconnected-(...)` pseudo-nets remain in
the authoritative saved board. If a pinned router version requires a pseudo-net or other
compatibility cleanup, apply it only to the temporary router input and audit the before/after
inventory against an explicit allowlist; never normalize the final board to suit the router.

## Diff the project file after any external router runs


A router may rewrite `.kicad_pro`. One measured case relaxed
`rules.min_hole_clearance` 0.25 → 0.175 mm and `net_class[Default].clearance` 0.2 → 0.175 mm, and
downgraded eight DRC severities to warning or ignore — on a run whose own reconciliation pass
reported nothing to route. It printed a loud banner saying so, which is more than most would.

**Never grade a router's output against an unreviewed rewrite of your rules.** Grade first against
the original authority, which is what exposes the change at all. A transformation may legitimately
need a local rule or project migration to represent its copper honestly, so a candidate project can
become a correct oracle — but only after an independent diff review approves it, and then it is
graded as a second, named result rather than silently replacing the first. Treat a relaxed fab
floor as a release blocker requiring an explicit waiver, never as a routing outcome. Note also that
lowered rules do not *guarantee* a clean read; they only remove the grounds on which the result
would have been refused.

## Choose the backend from behaviour

**Which backend: judge by difficulty, and judge it from behaviour.** An owned pattern
router — enumerate candidate polylines per connection, take the first that clears —
is the right tool while its failures stay *diagnosable*: every constraint is a
function in your own generator, and an unrouted net names one connection you can
reason about. Reach for Freerouting when that stops being true.

Resist "small board / big board". Difficulty is density, layer count, placement
quality and how constrained the critical nets are — a large sparse board can be
easy and a small tightly-packed one impossible — so connection count is a poor
proxy and this file deliberately gives no threshold on it.

A pattern router's cost profile tells you **where the time goes, not how it grows**.
Measured on one 4-layer, 169-connection board: 96 % of routing runtime went into
calls that FAILED (411 failing calls / 128.3 s against 138 successes / 4.9 s),
because a successful enumeration stops at the first clear polyline while a failing
one usually — not always — exhausts every family. That makes failures the thing to
optimise and the thing to watch. It does **not** by itself establish superlinear
growth: a bounded candidate family with a stable failure fraction produces the same
96 % at any size, and one pathological family or a few congestion hotspots would
produce it too.

So the switch signal is behavioural, and you can observe it without a threshold:
the unrouted list stops being individually diagnosable; the failure set grows
between rip-up rounds instead of shrinking; or you catch yourself widening
candidate families instead of fixing placement. Any of those means the enumeration
has stopped being the right instrument.

When the native path is slow or is not converging, consider an exploratory Freerouting run as
placement guidance even if the native or pattern router will ultimately finish the board. Its first
pass can provide evidence about congestion and placement even when none of its geometry is
promoted. Discarded scout geometry commits nothing, but running and reviewing it still has a cost;
promotion remains a separate, explicit project decision governed by the scope and manifest
machinery below.

## KiCadRoutingTools (KRT): a native-format backend, measured

[KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) ("KRT", announced
2026-02) is the default scout backend (`PCB.md` makes the pre-pass scout itself the
default; Freerouting remains the alternative where KRT is unsuitable): a Python CLI + KiCad plugin
with a prebuilt Rust A* core that **reads and writes `.kicad_pcb` directly** (KiCad 9/10) —
no DSN/SES round-trip, no JRE. That sidesteps the DSN carrier limits in the
constraint-serialization section, but only for constraints you pass it explicitly:
`--track-width` and `--power-nets <patterns> --power-nets-widths <mm...>` were measured
working; what it derives from board netclasses was not measured. Everything else in this
file — scout-first, never trust the router's summary, diff the project file, promotion
governance — applies to KRT unchanged.

Install (measured on macOS arm64, release v0.21.3): clone, `python3 -m venv .venv`,
`pip install -r requirements.txt` (numpy/scipy/shapely), then `python build_router.py`,
which downloads a prebuilt `grid_router` binary. Pin **both** the release tag and the
binary's self-reported version: release v0.21.3 shipped a binary announcing
`grid_router v0.21.1` — record the pair, or a re-run cannot prove it used the same core.

Measured scout, 2026-08-27, on a 2-layer 59-footprint / 31-net EGS002 inverter board
(placement taken mid-session from an agent's manual-routing attempt, all copper stripped):

- `py_router/route_planes.py board.kicad_pcb --nets GND --plane-layers B.Cu`: ~9 s wall,
  with a per-net plane resistance / max-current JSON report.
- `py_router/route.py board_routed.kicad_pcb --overwrite --track-width 0.5 --power-nets
  "BUS+" "SW_L" ... --power-nets-widths 2.0 ...`: ~35 s wall, 30/30 nets routed, 51 vias,
  0 failed, ending in a "KiCad-oracle" recheck that refills zones through pcbnew and
  confirms connectivity.
- Independent verification (rule above: never the summary): `kicad-cli pcb drc` plus the
  fail-closed `scripts/kicad_copper_collisions.py` audit. Verdict: **zero
  router-introduced shorts, crossings, clearance errors, or unrouted connections**. The
  only `shorting_items` (2) were a pad-on-pad placement overlap already present on the
  stripped input — confirmed by running the same audit on the bare board. The same
  placement had held a manual agent routing loop at 37–104 certain shorts across ~5 h;
  as placement-feasibility evidence the scout was decisive either way.

Measured caveats — all three bit during the scout:

1. **Zones are saved unfilled.** KRT's own completion oracle refills in memory, but the
   artifact on disk has no `filled_polygon`; `kicad-cli pcb drc` on it reported 24
   unconnected GND items that a `pcbnew.ZONE_FILLER` refill reduced to 0. Refill before
   any DRC, audit, or fab export of KRT output.
2. **It rewrites the sibling `.kicad_pro`.** In one run it relaxed the copper-to-hole
   floor 0.25 → 0.2 mm (disclosed loudly as "FAB FLOOR RELAXED") and downgraded DRC
   severities to ignore (`solder_mask_bridge`, `pth`/`npth_inside_courtyard`,
   `annular_width`, `lib_footprint_*`; `starved_thermal` → warning). Every subsequent
   DRC — KiCad's included — grades against the relaxed set, so a "clean" report and any
   before/after comparison are meaningless until you diff the project file (section
   above) and restore the original floor and severities, or accept them as recorded
   project decisions.
3. **The version pair diverges** (release tag vs binary self-report, above) — pin both.

## Inputs required for a promotable run

For a generated board whose project has explicitly opted into external routing, Freerouting can be
the **candidate backend for the project's declared routine scope** when placement and rules are
mature and all of these tracked inputs exist:

- `autoroute.json` with an exact backend, net-class allowlist, layer allowlist,
  styles, limits, seed baseline, reviewed selected-scope/audit policy, and
  manifest path;
- a dedicated KiCad net class whose live `.kicad_pro` assignments and dimensions
  match that configuration exactly;
- a generator stage that emits a deterministic, filled seed with only the named
  routing tasks open; and
- a project-local, Freerouting-independent manifest applicator.

If any item is absent, do not start a promotable run; onboard it with the
scaffold below or keep the existing native/manual routing path. This is not
permission for silent whole-board autorouting. Placement, fanout, high-current
copper, critical nets, differential/skew constraints, isolation, planes, zones,
and post-route stitching stay generator-owned unless the project explicitly
defines and audits a different boundary. Freerouting does not place footprints;
a poor resistor/capacitor grid is a placement problem and must be fixed before
routing.

## Onboard with the v2 scaffold

For an opted-in project that does not yet have `autoroute.json`, use the v2 scaffold. It
supports generated projects through a small language-neutral adapter, existing
hand-maintained KiCad projects through an immutable board snapshot, and a
standalone `.kicad_pcb` through explicit board-only authority. The last mode
creates a minimal `.kicad_pro`; it never invents a schematic, and every report
retains the permanent parity/ERC waiver.

```sh
# First write a read-only, reviewable plan. The selected-scope declaration is
# mandatory: use --project-audited when geometry-dependent checks are needed.
python3 scripts/kicad_autoroute_scaffold.py plan project/board.kicad_pcb \
  --mode board-snapshot \
  --use-net-class AutorouteRoutine \
  --layer F.Cu --layer B.Cu \
  --reset-all-selected-routing \
  --selected-scope-routine \
  --output work/autoroute-scaffold-plan.json

python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-scaffold-plan.json \
  --approve-plan-sha256 PLAN_SHA256

python3 scripts/kicad_autoroute_scaffold.py check project/board.kicad_pcb \
  --report work/autoroute-scaffold-check.json
```

`check` is fail-closed and reports a phase, not a vague boolean: project
context, configuration, primitives, adapter, audit, toolchain, migration,
stale-source, report-only platform, baseline-ready, or candidate-ready. It
re-expands recursive source declarations, runs the adapter on temporary output,
checks every protected seed route, and refuses KiCad format/default-stackup
migration. The source board remains editable and authoritative; generated seed
and final boards live below `build/autoroute/` and are not edit targets.

Use `--create-net-class` only with an explicit reviewed net allowlist and exact
style dimensions. It rejects an existing class name and conflicting effective
assignments. `--use-net-class` freezes KiCad's effective board resolution,
including pattern results, as a finite `net_to_class` inventory. A reset is an
exact UUID/geometry/locked-state multiset, never “delete whatever happens to be
in this class now.” Initial snapshot support is deliberately limited to track
segments and F.Cu-to-B.Cu through-vias; arcs, blind/buried vias, and microvias
remaining anywhere in the protected seed block promotion.

The generated `autoroute_adapter.py` is complete in snapshot mode. In generator
mode it is intentionally a `BLOCKED_ADAPTER` template: implement `describe`,
`seed`, and `final` for the project's generator without AST rewriting. Its
`final` operation invokes the promotion-pinned applicator, which reruns the
adapter's `seed`, verifies the reviewed semantic/context attestation, and then
applies canonical route records. The generated `autoroute_apply.py` is
generator-neutral and owns source/reset/manifest validation plus canonical
segment/via application. For
`--project-audited`, replace the fail-closed audit stub with project physics
checks and a known-bad calibration; `--selected-scope-routine` waives only
geometry-dependent checks on the selected nets, never generic integrity checks
or critical copper elsewhere.

Editing either blocked template invalidates its configured digest. Repin those
tools through a new digest-approved plan, never by silently editing
`autoroute.json`:

```sh
python3 scripts/kicad_autoroute_scaffold.py repin-plan \
  --config project/autoroute.json \
  --output work/autoroute-repin-plan.json
python3 scripts/kicad_autoroute_scaffold.py apply \
  --plan work/autoroute-repin-plan.json \
  --approve-plan-sha256 PLAN_SHA256
```

## Run the candidate-and-promotion pipeline

The production flow is a candidate-and-promotion pipeline:

```text
accepted or authorized critical-footprint floorplan -> optional exploratory scout
-> revise placement/corridors if needed -> discard scout copper
-> update artifact and renew user approval or recorded autonomous decision as applicable
-> generator-owned critical skeleton -> deterministic seed with routine opens
-> Freerouting routine candidate -> verification -> route manifest -> final generator
```

```sh
# From the kicad-design skill root. Status is read-only; install needs explicit
# user authorization and an explicit --yes.
python3 scripts/kicad_autoroute_tools.py status
python3 scripts/kicad_autoroute_tools.py install --yes

# Set this to the Python interpreter shipped with the installed KiCad build.
# The candidate wrapper can discover it, but seed generation and promotion use
# pcbnew directly and therefore require an explicit executable.
KICAD_PYTHON=/path/to/kicad-bundled-python3

# Adapter command: emit the project-owned seed bundle with same-stem context.
"$KICAD_PYTHON" project/autoroute_adapter.py seed \
  --output-dir project/build/autoroute/seed \
  --report project/build/autoroute/seed/adapter-report.json

python3 scripts/kicad_route_candidate.py project/build/autoroute/seed/board.kicad_pcb \
  --config project/autoroute.json \
  --report work/route-report.json \
  --keep-workspace work/router-workspace \
  --fail-on-findings

# After visual review, copy candidate.board_path and the exact candidate/report
# digests from the report. Promotion refuses changed inputs, a substitute board,
# or a non-promotable verdict.
"$KICAD_PYTHON" scripts/kicad_route_manifest.py promote \
  --seed work/seed.kicad_pcb \
  --candidate-board CANDIDATE_BOARD_PATH \
  --config project/autoroute.json \
  --report work/route-report.json \
  --project-root project \
  --approve-candidate-sha256 CANDIDATE_SHA256 \
  --approve-report-sha256 REPORT_SHA256 \
  --output-manifest project/routes.json

# The adapter/applicator consumes only the reviewed manifest, not Java/DSN/SES.
"$KICAD_PYTHON" project/autoroute_adapter.py final \
  --manifest project/routes.json \
  --output-dir project/build/autoroute/final \
  --report project/build/autoroute/final/adapter-report.json

# Prefer one project-owned final wrapper that regenerates reproducibly and emits
# a canonical report covering DRC, parity, calibrated audits, and exact routes.
"$KICAD_PYTHON" project/verify_final_pcb.py
```

Treat DSN, SES, Freerouting's completion count, and the raw imported board as
untrusted evidence. KiCad's SES importer replaces routing rather than providing
a trustworthy edit script. The wrapper therefore locks and proves the seed,
extracts the raw addition delta, discards excluded-net/layer additions, applies
only canonical segments and F.Cu-to-B.Cu through-vias to a fresh seed, and then
proves that every protected seed primitive remains. Never promote the raw SES
board.

Freerouting 2.3.0 requires both `--router.automatic_neckdown=false` and
`--router.fanout.enabled=false` for exact-width manifests. Its fanout fallback can
emit micro-neckdown segments even when automatic neckdown is disabled. Its `-inc`
ignored-class option is advisory only; the post-import filter and manifest scope
are authoritative.

A green DRC is necessary but not sufficient. Promotion also requires the exact
position-sensitive seed DRC multiset, zero final unconnected items, schematic
parity, complete non-routing projection equality, protected-route equality,
calibrated final project audits for project-audited scope (or the explicit
reviewed routine-scope declaration),
unchanged source/input bundles, exact
toolchain receipts, and a promotion-enabled compatibility cell. A new KiCad,
`pcbnew`, OS, architecture, Java, or Freerouting version starts staged/report-only
until that exact cell is qualified.

The v2 manifest is the only generated source input: canonical segments and
through vias, exact nanometre geometry and style, the reviewed semantic/context
seed attestation, project input bundle, project applicator hash, toolchain
receipt, and candidate/report digests. The final adapter must re-create that
attestation before applying it and re-extract the final routes to prove exact
equality. `seed_sha256` remains byte evidence, not v2 authority; the legacy v1
contract still requires exact seed bytes. Re-running Freerouting is not part of
board reproduction.

Make the final verification result a canonical, tracked machine-readable report,
not a set of unrelated terminal transcripts. It must bind the final board digest
and promoted route digest and include a full two-run reproduction result, JSON DRC
with schematic parity, the calibrated project-audit result, and exact manifest
re-extraction. A failure in any member makes the report fail; a DRC-only report is
not release evidence.

See [`scripts/README.md`](scripts/README.md) for the command contract. The research evidence and
limitations behind this policy are archived on the `drafts-archive` branch
(`git show drafts-archive:drafts/PCB-AUTOROUTING.md`).
