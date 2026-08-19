# PCB layout and footprints (KiCad)

Companion to `SKILL.md`. **Read this file when the task involves the board** —
`.kicad_pcb`, `.kicad_mod`, routing/autorouting, `pcbnew` scripting, DRC, zones,
footprints, land patterns, stackup, creepage, surface leakage, or **fab output and release**
("is this ready to order?"). Schematic-only work does not need it.

Everything in `SKILL.md` still applies here: generate rather than hand-place,
climb the whole verification ladder, and write guards that fail when they cannot
evaluate their input.

**This file is the board-layout core.** Four companions carry the rest, so a task pays
only for what it needs:

| file | read it when |
|---|---|
| [`GUARDS.md`](GUARDS.md) | writing or reviewing board audits, geometry checks or calibration harnesses |
| [`FOOTPRINTS.md`](FOOTPRINTS.md) | editing a footprint, choosing a land pattern, changing a package |
| [`PCBNEW.md`](PCBNEW.md) | scripting `pcbnew`, chasing a wobbling md5, or a slow generator |
| [`RELEASE.md`](RELEASE.md) | verifying a board, or answering "is this ready to fab?" |

## Scoped external autorouting: default only after the project opts in

Choose routing ownership before choosing a backend:

| Mode | Purpose | Authority |
|---|---|---|
| **Exploratory** | Probe placement, congestion, possible corridors, via pressure, and whether the current floor plan is plausibly routable | Disposable report only. Never promote it, and do not transplant its coordinates into generator source as if they were reviewed routes |
| **Critical** | Implement geometry whose shape carries an electrical, thermal, safety, or fabrication requirement | Generator-owned on generated boards; manually authored only on explicitly hand-maintained boards. Route and audit it before making the promotable seed |
| **Routine** | Complete explicitly allowlisted low-risk connectivity around the finished critical skeleton | Freerouting may propose it; only verified canonical manifest geometry becomes a generator input |

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

Keep at least these structures critical:

- low-inductance switching, gate-drive, and decoupling loops, including their
  return paths and via count;
- high-current or thermal paths where width is only one part of the structure:
  neckdowns, parallel layers, pours, connector entries, and via arrays matter too;
- creepage/isolation barriers, bounded crossings, slots, keepouts, and any copper
  whose all-layer distance implements a safety requirement;
- RF/HF, controlled-impedance, differential/skew, clock, and other
  stackup/return-path-sensitive routes; and
- Kelvin, sense, guard, star-point, plane-entry, and other topology-bearing nets.

A uniform trace width and clearance can remain routine when those dimensions are
the whole requirement and the exact class/style is checked after import. If the
requirement is really current density, temperature rise, impedance, inductance,
loop area, creepage, or return continuity, DRC-clean width/spacing is insufficient
and the route is critical. For generated boards, “manual” means deliberately
authoring the route in generator source—not editing the generated `.kicad_pcb`.

**Which backend, and it is a size question before it is anything else.** An owned
pattern router — enumerate candidate polylines per connection, take the first that
clears — stays the right tool for a *small* board: it is inspectable, its failures
name a connection you can reason about, and every constraint lives in your own
generator where a region policy or a barrier rule is a function you can read. It
does not scale, and the reason is structural rather than a tuning problem: on a
169-connection board **96 % of its runtime went into calls that FAIL**, because a
candidate enumeration that succeeds stops at the first clear path while one that
fails must exhaust every family. Congestion turns successes into failures, so cost
climbs precisely where the board gets hard.

So: **pattern router for small boards; Freerouting for complex ones, and as initial
guidance on any board.** The second half of that is the part worth keeping — an
external router's first pass is useful as *evidence about the placement* even when
none of its geometry is promoted. Where it struggles, the floor plan is telling you
something the connection list alone does not, and that reading costs nothing and
commits nothing. Promotion is a separate decision, governed by the scope and
manifest machinery below.

For a generated board with mature placement and rules, use Freerouting as the
default **candidate backend for the project's declared routine scope** when all
of these tracked inputs exist:

- `autoroute.json` with an exact backend, net-class allowlist, layer allowlist,
  styles, limits, seed baseline, audits, and manifest path;
- a dedicated KiCad net class whose live `.kicad_pro` assignments and dimensions
  match that configuration exactly;
- a generator stage that emits a deterministic, filled seed with only the named
  routing tasks open; and
- a project-local, Freerouting-independent manifest applicator.

If any item is absent, keep the existing native/manual routing path. This is not
permission for silent whole-board autorouting. Placement, fanout, high-current
copper, critical nets, differential/skew constraints, isolation, planes, zones,
and post-route stitching stay generator-owned unless the project explicitly
defines and audits a different boundary. Freerouting does not place footprints;
a poor resistor/capacitor grid is a placement problem and must be fixed before
routing.

The production flow is a candidate-and-promotion pipeline:

```text
optional exploratory scout -> revise placement/corridors -> discard scout copper
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

# Project-specific command: emit a deterministic seed, not a final board.
"$KICAD_PYTHON" project/gen_pcb.py --autoroute-seed --output work/seed.kicad_pcb

python3 scripts/kicad_route_candidate.py work/seed.kicad_pcb \
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

# The normal generator consumes only the reviewed manifest, not Java/DSN/SES.
"$KICAD_PYTHON" project/gen_pcb.py --full

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
final project audits that emit their required known-bad calibration marker,
unchanged source/input bundles, exact
toolchain receipts, and a promotion-enabled compatibility cell. A new KiCad,
`pcbnew`, OS, architecture, Java, or Freerouting version starts staged/report-only
until that exact cell is qualified.

The manifest is the only generated source input: canonical segments and through
vias, exact nanometre geometry and style, the reviewed seed digest, project input
bundle, project applicator hash, toolchain receipt, and candidate/report digests.
The normal generator must re-create the seed digest before applying it and must
re-extract the final routes to prove exact equality. Re-running Freerouting is not
part of board reproduction.

Make the final verification result a canonical, tracked machine-readable report,
not a set of unrelated terminal transcripts. It must bind the final board digest
and promoted route digest and include a full two-run reproduction result, JSON DRC
with schematic parity, the calibrated project-audit result, and exact manifest
re-extraction. A failure in any member makes the report fail; a DRC-only report is
not release evidence.

See [`scripts/README.md`](scripts/README.md) for the command contract and
[`drafts/PCB-AUTOROUTING.md`](drafts/PCB-AUTOROUTING.md) for the research evidence
and limitations behind this policy.

## Decoupling is a current loop, not a placement radius

Validate every datasheet-critical bypass by following actual copper from the IC supply pin to
the capacitor and back to the specified return pin or plane entry. Check both capacitor pads,
vias and layer changes. Centre-to-centre or hot-pad-to-pin Euclidean distance can pass a long,
inductive return path and cannot prove that the capacitor is connected across the required
two nodes.

- Place the smallest/highest-frequency capacitor first and route its loop short and direct;
  keep bulk capacitors from displacing it.
- Verify topology as well as distance. Two capacitors in series through a ground net do not
  implement a datasheet-required direct rail-to-rail capacitor.
- Derive any numeric audit limit from a datasheet, package/application geometry or an explicit
  loop-inductance target. Do not raise a failed limit to the distance the finished placement
  happens to provide and then describe that value as verified.
- Make the audit fail when it cannot reconstruct the complete loop. Reporting only the nearest
  pad-to-pin distance creates a precise number for the wrong property.

## The stackup is part of the design, not a fab preference

If the board file has no `(stackup ...)` block, KiCad assumes a default and the
fab builds whatever is cheapest that week — while your design doc quotes
dielectric-dependent numbers (stray capacitance, impedance, creepage class) that
depend on a stackup nobody agreed to. Write it explicitly.

The SWIG `pcbnew` API does not usefully expose `BOARD_STACKUP` (you get an opaque
`SwigPyObject` with no methods), so this has to be a text edit on the saved
`.kicad_pcb`. Anchor it on a regex that captures the existing indentation —
KiCad indents with tabs, and a hardcoded two-space `"\n  (setup\n"` anchor will
not match. **Make the failure loud**: if the anchor is missing, exit; do not
return quietly, or the stackup silently stops being written and every number that
depends on it becomes unbacked. Then verify KiCad *parses* it rather than merely
tolerating it — load and re-save through `pcbnew` and confirm the block survives
the round-trip.

## Symmetry and matching are invisible to DRC

A board can be DRC-clean, parity-clean and **completely asymmetric**. Nothing in KiCad checks
that a differential pair is matched, that two halves of a current path mirror, or that a
matched-resistor pair sits symmetrically in a thermal gradient. If the design's accuracy rests
on any of that, it rests on a guard you write, and the design docs must say *that* guard — not
DRC — is what enforces it, or the next tidy-up deletes it as redundant.

Four traps, all met on one precision current-sense board:

- **Derive the mirror's net map from the schematic, not from the net names.** Under a
  left/right mirror the nets swap in pairs, and the pairing is a circuit fact: `Shunt+ ↔
  Shunt-` is obvious, but on that board `Net-(JP1-A) ↔ GND` too, because those were the
  shunt's two Kelvin sense terminals. Assume every net maps to itself and the audit reports
  the correctly-mirrored sense pair as broken while missing the pours.

- **A footprint's `(at …)` is a proxy for where the part is; the pads are the truth.**
  `Wuerth_PowerPlus_M5_Nut` carries its origin **1.5 mm off its own pad cluster**, so two lugs
  at x = 109.5 and 132.5 — apparently centred on 121.0 — actually have their pad clusters at
  108.0 and 131.0, i.e. exactly symmetric about the board centre at 119.5. An origin-based
  check reports a false asymmetry. Worse, the *same* proxy error had already reached a design
  review, which derived "the symmetry axis is x ≈ 121" from those origins and concluded the
  wrong one of two matched resistor networks was the thermally exposed one. Compare pad sets.

- **Audit the zone FILL, not the zone outline.** The outline is intent; the fill is what ships,
  and it is shaped by pads, tracks, clearances and the board edge. On that board the two
  current pours had outlines that differed only cosmetically while their *fills* differed by
  3.4 mm².

- **Compare fills semantically, never by vertex equality or area alone.** KiCad segments arcs
  into chords, and two mirror-image arcs get their chords in different places even when the
  shapes are equivalent. Conversely, equal areas can hide a neck or a severed region. For a
  load-bearing matched pour, use [`GUARDS.md`](GUARDS.md)'s two independent gates: an
  artifact-derived masked shape residual with bounded masks and topology validation, plus an
  unmasked raw-quantity limit derived from the physical error or thermal budget. Measure the
  legal fill noise before setting either geometric tolerance.

**Symmetry is not the whole objective — check the loop area too.** A mirror-symmetric
differential pair can still be a large pickup loop, and the audit that proves the symmetry
will happily pass it. On that board the sense pair was laid out perfectly symmetric at 4.2 mm
spacing, enclosing **143 mm²** between the two conductors; re-laying it at 0.6 mm inside the
existing 1.0 mm gap between the two current pours cut that to **26.5 mm²** with no change to
the symmetry verdict, no new DRC violations, and *more* pour copper than before, because the
pair now runs in a gap that already existed instead of slicing a fresh void through each pour.
Compute the enclosed area explicitly — shoelace over `[source pad A, …trace A…, load, …trace B
reversed…, source pad B]` — and put the number in the design doc, because nothing else will
ever tell you it is too big. Two corollaries: the fan-out from the source's pad pitch is often
the dominant remaining term, so converge it steeply rather than at a tidy 45°; and running the
pair down the gap between two pours *guards* it, provided each conductor is flanked by the
pour nearest its own potential rather than the other one's.

Asymmetries hide in places a placement check never looks. On that board the last one left,
after every coordinate matched, was **pad 1 of a 4-terminal shunt being `rect` while pad 4 was
`circle`** — same size, same drill, the ordinary pin-1 marker. It was 1.65 mm² of extra copper
on one terminal and it carved a correspondingly larger void out of the opposing current plane.
Before changing it, check the part still has a pin-1 marker somewhere else (silkscreen), and
make the script *refuse* if it does not — symmetry is not worth losing orientation over.

Finally: a symmetry audit is exactly the kind of guard that must fail closed. An unreadable
outline, an object class the parser never visited, or a pair that could not be compared has to
raise — "0 asymmetries" out of a scan that examined nothing is the anti-monotone false PASS.
Follow [`GUARDS.md`](GUARDS.md)'s subject inventory, stable failure IDs and bad/legal calibration
pair so the audit proves both detection and valid-domain headroom.

## Surface leakage: measure the PATH, not the gap

Clearance asks *how far apart are these two nets*. Guarding asks *is there anything at a
harmless potential in between* — a different question with a different answer. Two nets 4 mm
apart with open laminate between them are worse than two 0.75 mm apart with a guard trace
interposed, because a guard absorbs the leakage and what reaches the victim is set only by the
**guard-to-victim** potential difference.

This matters wherever a high-impedance node meets a rail. The arithmetic is short: leakage
through `R_leak` into a source impedance `R_s`, referred to the input, is `V_driver · R_s /
R_leak`. On a 1 kΩ source with a 3.3 V rail 0.75 mm away and a flux- or humidity-degraded
surface insulation resistance of 10⁹ Ω, that is 3.3 nA → **3.3 µV**, which on a ±35 mV
converter is ~94 ppm — the same order as the silicon's own input-current term, and *offset*
rather than gain, so calibration does not remove it and it moves with humidity. Clean masked
FR4 at 10¹² Ω makes the same geometry irrelevant. **The budget therefore lives in the assembly
process, not the layout**: say whether the board is washed, and whether it is coated.

Sort the paths by what they cost you:

- Leakage **inside the feedback network** (output to summing node, divider to tap) is in
  parallel with a feedback element, so it is a **gain** error — `Rf / R_leak` — and calibrates
  out. 100 kΩ against a contaminated 3 × 10⁹ Ω is 33 ppm, and 0.1 ppm clean.
- Leakage **from a rail** is signal-independent, so it is an **offset**. That is the one to
  engineer against.

### Measuring it

Rasterise the layer, flood-fill from the victim net through everything that is **not** guard
copper, and report the geodesic length of the shortest surviving path to each driver net. The
flood routes *around* the guard, which is the point — a straight line does not.

Do not substitute a proxy. Sampling the straight line between two nets and reporting "what
fraction of it is covered by guard copper" looks reasonable and is not: measured on one board
it gave 1.170 mm where the flood-fill gave **0.72 mm**, wrong in the *optimistic* direction,
because the line test walked tracks only while the real shortest approach was to a **pad**.
Include pads.

Fail closed, or this becomes the anti-monotone false PASS in its purest form — "nothing
reached the victim" is exactly what a rasteriser that found no victim copper also reports:

```python
if not src:    raise SystemExit("UNVERIFIED: rasteriser found no victim copper")
if not nguard: raise SystemExit("UNVERIFIED: rasteriser found no guard copper")
```

Report the geodesic per driver rather than a verdict, because the useful output is a
before/after. On the board above, pushing the guard between the summing node and the rails
moved `+5V` from 0.72 mm to **10.64 mm** and `V−` from 10.16 to 21.20 mm, while the paths that
stayed short were all inside the feedback network — which is the correct end state, not a
failure. Note that **no driver will ever be fully blocked**: a closed guard ring is impossible
on the layer the victim's own traces have to leave by, so ~10 mm is the practical maximum and
"BLOCKED" is not the target.

Two corollaries that decide what to do about it:

- **More copper, not less.** A guard is only a guard while it is adjacent. On a low-level
  front end the input nodes sit within tens of millivolts of ground, so ground pour beside them
  has ~20 mV of driving voltage against 2–5 V for the output and rails — two decades less. The
  instinct to "keep copper away from the sensitive node" is backwards here.
- **A driven guard usually is not worth it.** At 20 mV of signal a plain ground guard is within
  20 mV of the victim, and its residual works out to `I · R_s / R_leak` — a fixed ppm **of
  reading**, i.e. a gain term, 1 ppm at 10⁹ Ω and 1 ppb clean. Driven guards earn their keep at
  gigaohm source impedances or with volts of common mode, neither of which applies.

**The package usually sets the floor anyway.** Before re-routing a pin escape to buy 0.1 mm,
measure the part's own pad-to-pad gap: on a SOT-23 divider the pads are 0.656 mm apart, so a
trace approach of 0.512 mm was chasing a number the package had already capped. Measure pad
copper edge to pad copper edge, not centre to centre.

## Isolated designs: the binding clearance is zone-to-zone, and DRC is not asked

On a board with primary and secondary domains, the minimum copper-to-copper distance almost
never occurs between tracks — it occurs **between the two ground/power pours on the inner
layers**, which is exactly where nobody looks. Measured on one 4-layer board: F.Cu 4.020,
GND 4.000, PWR 4.295, B.Cu 4.000 mm — the two binding numbers were both zone-to-zone.

DRC will not check any of this unless a rule asks it to, so an **independent audit must
measure real geometry on every copper layer**, zones included, and enforce the stated figure.
A secondary track sitting 3.638 mm from primary pads passed DRC cleanly for exactly this
reason. Set the floor *at* the standard, calibrate it by injecting a known-bad geometry, and
scope any package-bridging exemption (an isolator or DC/DC straddles the barrier by
construction) to pairs where **both** items belong to that package **and** touch its pads —
bounded by its own measured floor, so a new object cannot inherit the excuse.

An exemption does **not** satisfy the original requirement. If a package's own measured floor
is below the stated board minimum, report the normal clearance and package deviation
separately and fail release unless the design records an approved waiver or a revised derived
requirement tied to that exact part and working voltage. Never print an overall `PASS >= 4 mm`
for a design whose approved geometry includes 3.5 mm; bounded is not compliant.

**Do not slot a plane to steer digital return current on a precision analog board.** It is
the textbook move and it is usually wrong here: the converter datasheet asks for a
*continuous* return beneath it, and a moat raises the impedance of the **analog** return to
fix a **digital** problem. Solve it with placement — put the noisy return corridor physically
away from the sensitive part and measure the clearance you achieved.
