# PCB layout and footprints (KiCad)

Companion to `SKILL.md`. **Read this file when the task involves the board** —
`.kicad_pcb`, `.kicad_mod`, routing/autorouting, `pcbnew` scripting, DRC, zones,
footprints, land patterns, stackup, creepage, surface leakage, or **fab output and release**
("is this ready to order?"). Schematic-only work does not need it.

Everything in `SKILL.md` still applies here: generate rather than hand-place,
climb the whole verification ladder, and write guards that fail when they cannot
evaluate their input.

## Board-specific rungs of the verification ladder

DRC green means "no rule was broken", not "the design is right". In particular:

```sh
$K pcb drc --severity-all --schematic-parity --exit-code-violations -o drc.rpt x.kicad_pcb
```

- **`--exit-code-violations` is not optional either.** Without it `pcb drc` writes
  every violation to the report and still **exits 0** — measured at 175 violations
  exiting `0` bare and `5` with the flag. Any wrapper that trusts `$?` passes a
  board it never checked. See *The verification ladder* in `SKILL.md`.
- **`--schematic-parity` is not optional.** It is the only check that the board
  still matches the netlist.
- **`--severity-all` does not mean "all rules".** It selects error + warning +
  exclusions; it does **not** resurrect a rule set to `ignore` in
  `.kicad_pro` → `board.design_settings.rule_severities`. Calibrated: with a
  footprint's courtyard deleted, `--severity-all` reported **no**
  `missing_courtyard` while the rule was `ignore`, and reported it as soon as the
  same run had it at `error`. So "DRC: 0 violations" is a statement about the
  current severity map as much as about the board — and one real project quietly
  carried five rules at `ignore` (`footprint_filters_mismatch`,
  `footprint_type_mismatch`, `missing_courtyard`, `npth_inside_courtyard`,
  `pth_inside_courtyard`). Worse, that map lives in the `.kicad_pro` that
  `SKILL.md` warns a generator can rewrite wholesale, so it is a guard
  precondition that moves silently. **Before believing a green DRC, list every rule at `ignore` in the
  release report — including KiCad's own defaults.** `missing_courtyard`,
  `footprint_filters_mismatch` and both `*_inside_courtyard` rules ship at
  `ignore`, so a diff-against-defaults reports nothing and never fires on the
  very example above; enumerate, then diff to catch a map someone edited. (On the project above, flipping all five back produced no additional
  violations — the mechanism is real, that instance was clean.)
- **A rule area that relaxes a constraint is keyed on *position*.** Anything that
  later moves into it silently stops being held to the strict value, and DRC
  stays green. Any relaxation needs an independent geometric audit that measures
  real clearance (binary-search `SHAPE::Collide`) rather than asking the rules.
- **Re-run the layout script after *any* schematic change**, not just after
  connectivity changes — see the parity note below.
- **Only KiCad's own connectivity is authoritative.** Third-party analyzers
  rebuild nets with their own union-find over pads, tracks, vias and fills, and
  on a 2-layer board they routinely report "GND plane split, 2 islands, signals
  crossing" for F.Cu fragments that are bridged through the B.Cu pour — alarming,
  and entirely normal. Check any connectivity claim against
  `board.GetConnectivity().GetUnconnectedCount(True)` and DRC's unconnected count
  before acting on it. The same class of tool flags *membership* of a rule area
  without reading its restriction flags: a via inside a keepout that explicitly
  permits vias is not a violation. Triage third-party findings before promoting
  any of them to a blocker, and say in the review which ones you dismissed and why.

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

## No vias in pads — and DRC will not tell you

KiCad's DRC does **not** flag a via sitting inside a pad. If they share a net it
is simply "connected" — which is how a 0.6 mm via can sit inside an 0805 land
through a full adversarial review. At reflow the via barrel wicks solder out of
the joint; the result is a starved joint that looks fine under a microscope.

Write the check yourself — and make it **net-blind**, because the real cases are
same-net:

```python
bad, pairs = [], 0
# Derive the layer set -- do NOT hardcode (F_Cu, In1_Cu, In2_Cu, B_Cu).  On a
# 6-layer board that literal skips In3/In4, the count still comes out non-zero,
# and the guard reports coverage it did not have.  See SKILL.md, "every layer
# literal is now a liability".
layers = [l for l in board.GetEnabledLayers().CuStack()]
for v in vias:                           # build these explicitly, do not assume
    for ref, p in pads:
        shared = [l for l in layers if v.IsOnLayer(l) and p.IsOnLayer(l)]
        if not shared:
            continue
        pairs += 1                       # count PAIRS, not (via, pad, layer) triples
        if any(v.GetEffectiveShape(l).Collide(p.GetEffectiveShape(l),
                                              pcbnew.FromMM(0.2)) for l in shared):
            bad.append(...)              # no net comparison anywhere
# not `assert` -- python -O deletes it, and this is the only guard in the snippet
if not pairs:
    raise RuntimeError("UNVERIFIED: no via/pad pairs examined at all")
print(f"{pairs} via/pad pairs over {len(layers)} copper layers, {len(bad)} hits")
```

Expect such a scan to find more than was reported: one instance typically comes
with several others (supply and ground vias inside SOIC lands are common) plus a
tail of near-misses in the 0.03–0.19 mm range. **A user reporting one instance of
a class of defect is reporting the class** — scan for all of it, and say what the
scan found.

When a via genuinely has nowhere to go — two chip lands 0.22 mm apart, a SOIC pin
0.5 mm from its decoupler — step it *off the axis* rather than squeezing it
between: run a short stub of track and put the via where there is room.


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


## PCB / `pcbnew` notes

Run layout scripts with KiCad's **bundled** Python — `pcbnew` is not importable from a normal
venv:

```
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
```

It prints a harmless `create wxApp before calling this` assert; ignore it. API traps met in
practice, with the name that actually works — **re-probed on KiCad 10.0.5 on 2026-08-09**:

| you reach for | it does not exist | use |
|---|---|---|
| `ZONE.SetDoNotAllowCopperPour(...)` | `AttributeError` **on 10.0.5** | `ZONE.SetDoNotAllowZoneFills(...)` — **this pair REVERSED between 9.0.4 and 10.0.5.** On 9.0.4 it was exactly the other way round, and this table said so. Probe both names and use whichever answers; do not hard-code either. |
| `LSET & LSET`, `LSET \| LSET` | `TypeError: unsupported operand type(s)` | `LSET.AddLayerSet()` / `RemoveLayerSet()` / `Contains()` — unchanged on 10.0.5 |
| `SHAPE::Collide((x, y), …)` | rejects the tuple | On the **base `SHAPE`** you get from `pad.GetEffectiveShape(layer)`, pass a real `VECTOR2I`; `Collide(shape, clearance)` is fine. **The concrete SWIG subclasses do not share one signature** — `SHAPE_CIRCLE.Collide` exposes *only* `SEG` overloads on 10.0.5, so `SHAPE_CIRCLE.Collide(VECTOR2I, 0)` is a `TypeError` too. Probe the class you actually hold; a stamp on this row that says "works" is only ever true of the type it was probed with. |
| `PAD.GetPos0()` / `SetPos0(...)` | `AttributeError` | `PAD.GetFPRelativePosition()` / `SetFPRelativePosition(...)` — and note it moves the pad's global position too, so don't "correct" that afterwards. Unchanged on 10.0.5 |
| `board.GetNetsByName().get(name)` | `NETNAMES_MAP` has no `.get` | `board.FindNet(name)`, and check for `None` — unchanged on 10.0.5 |

**Four of five survived the major-version bump and one inverted, which is the worst possible
ratio**: it is exactly high enough to make "the table still holds" the natural assumption, and
the one that moved fails as an `AttributeError` at the call site rather than as a wrong number,
so it is loud when hit — but only if that branch is exercised.

A **second** table, met on 10.0.5 while writing a footprint library out of a board and while
auditing vias and zones. These were not probed against 9.0.4, so no claim is made about the
bump — they are simply the next six that cost a round trip each:

| you reach for | what happens | use |
|---|---|---|
| `PCB_IO_MGR.PluginFind(...)` | `AttributeError` on the class | `PCB_IO_MGR.FindPlugin(PCB_IO_MGR.KICAD_SEXP)` |
| `io.FootprintLibCreate(path)` / `io.CreateLibrary(path)` | `AttributeError: 'PCB_IO' object has no attribute 'CreateLibrary'` | there is nothing to create — a `.pretty` **is** a directory. `os.makedirs(lib, exist_ok=True)` then `io.FootprintSave(lib, fp)` |
| `fp.Duplicate(False, True)` | `TypeError … argument 3 of type 'BOARD_COMMIT *'` | `Duplicate(bool)` or `Duplicate(bool, BOARD_COMMIT*)`; the return is a **`BOARD_ITEM`**, so `pcbnew.Cast_to_FOOTPRINT(...)` it or the next `SetOrientationDegrees` is an `AttributeError` |
| `via.GetWidth()` | wx assert on stderr: *"PCB_VIA::GetWidth called without a layer argument"* | `via.GetWidth(via.TopLayer())` — a via's width is now per-layer |
| `board.GetDesignSettings().GetDefault()` | `AttributeError` | read the netclass off an item, or the `.kicad_pro` JSON |
| `pcbnew.VIATYPE_BLIND_BURIED` | `AttributeError` — the name is not what the C++ enum suggests | enumerate `[n for n in dir(pcbnew) if n.startswith("VIATYPE_")]` and map values yourself |
| `cc.GetNetItems(code, pcbnew.PCB_PAD_T)` | `TypeError … argument 3 of type 'std::vector< KICAD_T >'` | avoid; use `kicad-cli pcb drc`'s unconnected pairs, per the note above |

**Exporting footprints out of a board into a `.pretty`** is the workflow those first three block,
and it is worth having: it vendors a library that resolves only through some machine's global
`fp-lib-table` at an absolute path, and it guarantees the result matches what will be
fabricated. Reset the instance-specific state before saving, or the library carries one
placement's baggage:

```python
io = pcbnew.PCB_IO_MGR.FindPlugin(pcbnew.PCB_IO_MGR.KICAD_SEXP)
os.makedirs(lib, exist_ok=True)
cp = pcbnew.Cast_to_FOOTPRINT(fp.Duplicate(False))
cp.SetPosition(pcbnew.VECTOR2I(0, 0)); cp.SetOrientationDegrees(0)
cp.SetReference("REF**"); cp.SetPath(pcbnew.KIID_PATH())
for p in cp.Pads():
    p.SetNetCode(0)                     # or the library ships this board's nets
io.FootprintSave(lib, cp)
```

Then **read back what was written**: assert the `.kicad_mod` contains no `(net ` and has
balanced parens, and that every footprint you meant to export produced a file. A silent
half-export leaves a library that resolves for most parts and fails for one.

A stamp is only as good as what it was probed with. The first pass at this table stamped row 3
"unchanged on 10.0.5" having probed a single `SHAPE` subclass, and a review found the row was
wrong for every *other* subclass — the same shape as "a record of attention with the question
never asked" in `SKILL.md`. Re-probe on any version change, name the type you probed, and use
the script below rather than a fresh ad-hoc one:

```python
# kicad10-api-probe.py  --  run with the bundled python3 and -u
import pcbnew
print("build", pcbnew.GetBuildVersion())
def row(name, fn, expect):          # expect: "works" | "raises"
    try:    fn(); got = "works"
    except Exception as e: got = "raises(%s)" % type(e).__name__
    print(("PASS " if got.startswith(expect) else "FAIL "), name, "->", got)
bd = pcbnew.BOARD(); z = pcbnew.ZONE(bd)          # bind parents; never nest constructors
l1, l2 = pcbnew.LSET(), pcbnew.LSET()
row("ZONE.SetDoNotAllowZoneFills",  lambda: z.SetDoNotAllowZoneFills(True),  "works")
row("ZONE.SetDoNotAllowCopperPour", lambda: z.SetDoNotAllowCopperPour(True), "raises")
row("LSET & LSET",                  lambda: l1 & l2,                         "raises")
row("LSET.AddLayerSet",             lambda: l1.AddLayerSet(l2),              "works")
row("NETNAMES_MAP.get",             lambda: bd.GetNetsByName().get("X"),     "raises")
row("BOARD.FindNet",                lambda: bd.FindNet("X"),                 "works")
sc = pcbnew.SHAPE_CIRCLE(pcbnew.VECTOR2I(0, 0), 1000)
row("SHAPE_CIRCLE.Collide(VECTOR2I)", lambda: sc.Collide(pcbnew.VECTOR2I(0, 0), 0), "raises")
row("SHAPE_CIRCLE.Collide(SEG)",
    lambda: sc.Collide(pcbnew.SEG(pcbnew.VECTOR2I(0, 0), pcbnew.VECTOR2I(1, 1)), 0), "works")
# PAD needs a real parent; see the lifetime note above
fp = pcbnew.FOOTPRINT(bd); p = pcbnew.PAD(fp)
row("PAD.GetPos0",                  lambda: p.GetPos0(),                     "raises")
row("PAD.GetFPRelativePosition",    lambda: p.GetFPRelativePosition(),       "works")
```

**Probing `pcbnew` safely.** Two things will waste an hour otherwise:

- **Run the probe with `python3 -u`.** A `pcbnew` call that crashes the interpreter takes
  buffered stdout with it, and you get a bare non-zero exit with *no output at all* and no clue
  which line died. Unbuffered, the last line printed is the line before the crash.
- **Never pass a freshly-constructed `pcbnew` object straight into another constructor — bind
  every parent to a variable first.** SWIG does not keep the parent alive, so a temporary is
  refcount-freed the instant the child's constructor returns and the child is left holding a
  dangling pointer:

  ```python
  pcbnew.PAD(pcbnew.FOOTPRINT(pcbnew.BOARD())).GetFPRelativePosition()   # SIGBUS, exit 138
  bd = pcbnew.BOARD(); fp = pcbnew.FOOTPRINT(bd); pcbnew.PAD(fp).GetFPRelativePosition()  # (0,0)
  ```

  Both measured on 10.0.5. This applies to every parented class, not just `PAD`. A crash while
  probing is not evidence that the API is missing — it is usually this.

Distances come back in internal units — `pcbnew.ToMM()` everything before comparing.

**`LoadBoard` → `Save` DOES NOT round-trip bit-identically on 10.0.5.** This paragraph used to
claim it did, verified as zero diff lines on a 12 000-line `.kicad_pcb` under 9.0.4. Re-tested
on 10.0.5, it is false on every board tried, by two separate mechanisms:

- **Item order is scrambled, and unstably.** On a 10.0-written board the raw diff was 25 000
  lines while `diff <(sort a) <(sort b)` was **zero** — content preserved, order not. Saving
  twice gives two different orders, so this is the UUID-ordering nondeterminism documented
  below, reaching the whole file rather than just zone fills.
- **A 9.x board is silently MIGRATED — saving is a format upgrade.** Measured on a
  9.0-written board:

  ```
  src: (version 20241229) (generator_version "9.0")
  out: (version 20260206) (generator_version "10.0")
  injected: (epsilon_r 4.5) (loss_tangent 0.02) (material "FR4") (thickness 1.51) …
  ```

  **Loading someone's 9.x board through the bundled Python and saving it fabricates a stackup
  they never specified** — which is exactly what "The stackup is part of the design, not a fab
  preference" above says must never be left to a default. Opening a board you do not own is a
  *write*, and a lossy one.

What survives is the guard, not the claim: **assert the round-trip on the specific file before
trusting any diff from it.** That assertion was written as a cheap precondition for surgical
scripted edits on a board you did not generate; on 10.0.5 it correctly *refuses*, which is the
guard working. Until that assertion passes on your file and your version, treat scripted edits
to someone else's board as unavailable rather than as reviewable — the diff a human was
supposed to be able to read is 25 000 lines of reordering with the real change buried in it.

**`board.Remove()` invalidates the SWIG proxies of the items you did *not* remove.** A later
`board.GetTracks()` raises `'SwigPyObject' object is not iterable`, and — the nastier half —
a proxy you snapshotted into a Python list *before* the removal silently loses its downcast,
so `t.GetStart()` starts returning a bare `SwigPyObject` with no `.x`. Snapshotting is not
the fix. **Resolve every lookup first, then mutate; and do in-place edits before removals**,
because an edit after a `Remove()` is operating on a proxy that may already be stale. The
failure is loud on the second loop iteration, which makes it easy to misread as "my first
edit corrupted the board" rather than "the binding invalidated my handles".

**A probe that returns "nothing" for every input has failed, not answered.**
`board.GetConnectivity().GetConnectedPads(pad)` returned an empty list for *every* pad on a
partly-routed board, including pads whose nets were fully routed. Read as data that would
have meant "the board has no connectivity at all"; read correctly it means the call needs
setup the SWIG binding does not do. This is the `0/24` shape from `SKILL.md` in `pcbnew`
form. Fall back to something that is definitely computed: `kicad-cli pcb drc` writes the
ratsnest as `[unconnected_items]` **pairs**, and diffing that list before and after a change
tells you exactly which connections closed.

**KiCad's bundled `pcbnew` imports Altium boards.** `PCB_IO_MGR` (all caps — `PCB_IO_Mgr` is an `AttributeError`) converts a `.PcbDoc` to
`.kicad_pcb` programmatically, so an Altium design can be pulled into a scripted KiCad
pipeline rather than re-drawn. Expect to fix up layer mapping afterwards — the import does
not always land copper, mask and silk on the layers you would have chosen — and re-run the
full ladder on the result, because a converted board has had none of your generator's
invariants applied to it.

**A pad named as a route endpoint carries no net with it.** A router helper that resolves
`"R11.2"` to coordinates will happily let you route net *A* to a pad belonging to net *B*: the
track lands on the neighbouring land, and the only symptom is a DRC `shorting_items` that
reads like a clearance problem rather than the wiring error it is. Getting a two-pad part's
pad-1 direction letter backwards is enough to trigger it. Check the net at the endpoint, not
just its position:

```python
if isinstance(p, str) and self.pad_net(p) != netname:
    sys.exit(f"track({netname!r}): endpoint {p} is on net {self.pad_net(p)!r}")
```

Calibrate by re-introducing the swapped pad number and watching it exit non-zero. Expect this
to be free on an existing board — it found no false positives on ~60 routed polylines — which
is the point: it costs nothing and removes a whole silent failure mode from the generator.

**A schematic edit that changes no nets can still break `--schematic-parity`.** Renaming a
symbol's *Value* field desyncs it from the value stored in the `.kicad_pcb` footprint. Re-run
the layout script after any schematic change, not only after connectivity changes.

**Zone fills are a cache.** Changing a clearance *rule* does not move filled copper until
zones are re-filled, so any geometric measurement afterwards is against stale copper. Re-fill
(`ZONE_FILLER`) before measuring, or you will "verify" the previous state. This is a classic
false negative when *calibrating* a clearance guard: tightening the rule to force a violation
appears to do nothing, and the guard looks broken when in fact the test was.

**A zone SETTING can destroy copper asymmetrically, and nothing checks settings.**
`island_removal_mode` on a current-path plane had reverted from `NEVER` to `ALWAYS`, and with
it went **37 mm² from one inner plane and not its mirror** — In1 856.21 mm² against In2
825.65, a 30.56 mm² imbalance where the two had previously been bit-identical. No layout
changed. The mechanism is the one under *A via that lands outside its pour*, one level up: a
foreign-net via cuts a corner off both planes, then on one plane the stitching row is that
plane's own net and re-anchors the orphan, while on the other it is a keepout — so one plane
keeps the block and the other deletes it. DRC is silent, every geometric audit passes, and the
decision to disable island removal was recorded in prose with nothing enforcing it.

Two consequences worth carrying:

- **Put zone settings in the audit, not just zone geometry.** Fill settings, pad connection
  mode and island removal are all load-bearing and all revert invisibly — a `.kicad_pro`
  rewrite, a GUI round-trip, or a zone copied from elsewhere.
- **Turning island removal off trades the copper for `isolated_copper` violations** (12 of
  them here). That is the right trade on a plane whose job is thermal — an electrically
  orphaned island still conducts heat, and deleting 31 mm² from one side only is exactly the
  gradient the symmetry rule exists to prevent. Resolve it with **mirrored stitching vias**,
  never by lowering the rule's severity.

- **A thermal pad wants solid copper, not thermal relief.** Zones default to
  `ZONE_CONNECTION_THERMAL`; spokes on an exposed-pad land starve exactly the
  connection the island exists to make. DRC's `starved_thermal` check catches it
  (`zone min spoke count 2; actual 1`) — set `ZONE_CONNECTION_FULL` on that zone.
- **A track endpoint sitting on top of a zone is not connected to it** if they
  are on different layers. It needs a via. DRC reports this as `track_dangling`
  plus an unconnected item; both point at the same missing via.
- **A via that lands outside its pour is silently unconnected.** Re-placing an LDO
  block 3.5 mm moved its `+5V` pins past the edge of the L3 pour; the plane vias
  then dropped onto bare laminate. Nothing in the placement code knew. After any
  re-place, assert every plane via actually falls inside a filled area of the zone
  it is supposed to reach — or add a zone that covers the strays and assert the
  fill count.


## Making a `pcbnew` layout reproducible — there are two causes, not one

`--repro` on a board fails for a reason the schematic side never hits, and fixing
only the obvious half leaves the md5 still wobbling.

1. **Random UUIDs.** Every track, via, zone and text created through the API gets a
   random UUID, and SWIG exposes `m_Uuid` **read-only** — there is no `SetUuid`. So
   the canonicalisation has to be a string-aware s-expression pass over the *saved*
   file, assigning `uuid5` over each item's own identity (refdes for footprints;
   net/layer/width/coords otherwise, prefixed by the owning footprint's refdes).
2. **Zone fills depend on item order.** KiCad orders items by UUID, and `ZONE_FILLER`'s
   boolean operations walk that order — so with random UUIDs the *fills* came out
   differing run to run by the odd redundant collinear vertex. Geometrically
   identical, textually not, and it defeats any hash comparison.

Therefore: **canonicalise ids and item order first, then fill.** Filling before
canonicalising bakes the old order into the polygons and the md5 keeps moving while
every geometric check reports PASS.


## Geometry helpers are guards, and fail the same way

**A segment-to-segment distance that only compares endpoints reports a large number for
two segments crossing in a perfect X.** One returned **5 mm** for a genuine crossing and
hid **17 real track crossings** from the router's own overlap check. Endpoint-to-endpoint
distance is not segment distance: handle the intersecting case explicitly, then calibrate
by feeding the helper two segments that cross at their midpoints and watching it return 0.

The same applies to any `near()`-style spatial index used to prune clearance checks: if it
ever returns a *subset* of the true neighbours, every clearance check built on it silently
starts passing — absence of evidence encoding absence of the problem. Calibrate it as a
**superset** property against brute force on a real board, not on a toy case.

**An empty geometry result reads exactly like a clean one.**
`fp.GetCourtyard(pcbnew.F_CrtYd).BBox()` returns `(0, 0, 0, 0)` when the footprint has no
courtyard on that layer. A neighbour scan that computes gaps from those boxes then finds no
overlaps anywhere and prints nothing — which is indistinguishable from "nothing is in the
way", and was very nearly reported as "the larger part fits". Assert the box is
non-degenerate before using it, and make a scan that examined **zero** candidates say so
rather than falling through to silence. Cheap general fix: print the number of items
considered next to the verdict, so a scan of nothing cannot masquerade as a scan that passed.


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

- **Compare fills geometrically, never by vertex equality.** KiCad segments arcs into chords,
  and two mirror-image arcs get their chords in different places even when the shapes are
  identical — so point-set equality reports pure noise as a defect. Measure (a) filled area and
  (b) the largest distance from any mirrored vertex to the other polygon's boundary. Measured
  on that board: chord noise **~0.005 mm**, real defects **0.8 – 2.5 mm**. Put the tolerance an
  order of magnitude above the noise and state both numbers next to it, so the next reader can
  see the check has headroom rather than being tuned to pass.

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
raise — "0 asymmetries" out of a scan that examined nothing is the anti-monotone false PASS,
and it is very easy to write here because the happy path prints the same thing.


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


## Modifying a footprint: copper, mask and paste are three independent layers

If you narrow a pad's **copper** for creepage, `F.Mask` and `F.Paste` do **not** follow.
This nearly shipped: an exposed pad was cut 2.95 → 2.00 mm and its mask 2.71 → 1.80 mm for
HV clearance, while the four paste apertures stayed at their original size — printing paste
**2.49 mm wide**, 0.245 mm *outside* the copper and onto bare solder mask, right in the
0.675 mm channel between −15 V and +110 V. Creepage measured on copper said 0.675 mm; the
real post-reflow figure was **0.430 mm**. Which column condemns it depends on coating: this is
paste on assembled parts, so it is the **assembly** case (A5–A7), where 0.430 mm clears A7
(0.4 mm, coated) and fails A6 (0.8 mm, uncoated) — i.e. it is a defect on an uncoated board and
marginal-at-best on a coated one. The bare-board copper-to-copper figure in the same channel is
the 0.675 mm, and *that* is the one to rule against B1–B4.

**Which column applies is not obvious, and getting it wrong is worth 0.2 mm here.** A5–A7 are
the *assembly* columns — component leads and their terminations, i.e. the pad-to-pad case
above once parts are on. B1–B4 are the *bare-board* conductor columns — track to track, track
to land. They disagree by enough to flip a verdict: 0.670 mm passes A7 and fails A6, while
0.675 mm passes B2 — three numbers within 5 µm of each other with three different answers.
State the column, the voltage band and the coating status every time, or the number means
nothing. And note this is IPC-2221**C** (Dec 2023), which supersedes B; the B-era values
quoted historically in this file were not re-verified against C's Table 6-1, so re-read it
before leaning on a marginal figure.

**None of this covers an isolation barrier.** IPC-2221 is a PCB design standard and does not
address reinforced/functional isolation. If the board has a barrier — mains, or any
safety-relevant separation — the binding documents are IEC 60664-1 / 62368-1, where clearance
(through air) and creepage (across surface) are *separate* quantities derived from working
voltage, pollution degree, overvoltage category and material group/CTI, and where slotting the
board is a legitimate remedy. Do not enforce a round number nobody derived: write down which
standard, which table, and the four inputs, or say plainly that the figure is a house rule.

After editing any pad, measure all three layers:

```python
for layer, name in ((pcbnew.F_Cu,"copper"), (pcbnew.F_Mask,"mask"), (pcbnew.F_Paste,"paste")):
    ...  # min/max extents per layer, then the gap to the nearest foreign-net land
```

Also: **never print paste over an open via barrel** — solder wicks down it. If thermal vias
sit inside the pad's mask opening, either shape the apertures to miss them, or specify
plugged/filled vias (IPC-4761) in the fab notes.


## Substituting a larger package: make the change, don't reason about it

Parts grow for real reasons — 0805 → 1210 for a voltage rating, 0805 → 2512 for fault power.
"Does it still fit" is answered by **doing it on a copy of the board and running DRC**, not by
measuring the neighbours you thought of.

A review that reasoned about the space around a capacitor concluded an 1812 would drop in
"without moving C8, J1 or the +110 V rail", citing ≥3 mm of clear board **north and east**. It
was wrong: the binding neighbours were **west** (an 0805 at 2.5 mm centre-to-centre, where the
new part needed 3.25 mm) and **south-east** (a connector courtyard). Dropping it in produced
seven violations and the corner had to be re-laid out. The failure mode is checking the
directions that have room — and it is the same shape as an empty-scan false pass, one level up.

```sh
cp board.kicad_pcb /tmp/t.kicad_pcb    # then swap the footprint via pcbnew,
                                       # keeping position, rotation and pad nets
$K pcb drc --format json --severity-all --exit-code-violations \
    -o /tmp/drc.json /tmp/t.kicad_pcb
```

Thirty seconds, and it settles courtyard, clearance and silkscreen at once. Re-run any
independent geometric audit on the copy too: a bigger package usually **improves** creepage
(a 1210's terminations are ~1.5 mm apart against an 0805's 0.9 mm), so the substitution can
drop the part out of a package-exception list entirely. That is worth knowing before you argue
for it, and worth recording after — an exception that no longer needs to exist should be
deleted, not left standing as a precedent for the next part.

### When placement refuses, enumerate — do not nudge and retry

Adding one 0603 to a full board took four refusals, and each one named a *different*
constraint: 0.670 mm into a connector's courtyard; then the only lane wide enough for that
connector's hazard warning; then 1.550 × 1.970 mm into an isolator's courtyard; then a
header's pin-label column, where the label could no longer be placed. Nudging after each would
have kept finding the next one at random.

Enumerate instead. Grid the region and reject a candidate that collides with **any** of:

1. every footprint courtyard (`GetCourtyard(F_CrtYd).BBox()` — and assert it is non-degenerate,
   per *An empty geometry result reads exactly like a clean one* above);
2. **every existing `F.Silkscreen` item's bounding box** — see below;
3. any lane a generator reserves for text it has not placed yet, such as a connector's
   per-pin label column at `pad_x − offset`;
4. a real margin, not zero. A 0.3 mm margin turned "fits" into "does not" on two candidates
   that were clearing a neighbour by 0.01 mm.

The output is the useful thing: on that board the entire primary side had **nine** free
positions for one 0603, all at the same spot. That is a fact worth knowing before arguing
about where the part should go, and it converts "find somewhere" into "there is one place".

**Silkscreen is territory.** This is the non-obvious one. A placement can be geometrically
legal, route cleanly, pass DRC — and still be wrong because it took the only lane on that side
of the board wide enough for a hazard warning. Silk has no courtyard and DRC will not defend
it; only a generator that *refuses when a `Silk` property fails to place* will catch it, which
is an argument for having that check before you need it.

When the part genuinely cannot go where it belongs, say so with the number that makes it
acceptable rather than moving it quietly. A series damping resistor 18.5 mm from its connector
instead of beside it is fine at a 10 ns edge — FR4 propagates at ~6.7 ps/mm, so that is ~250 ps
round trip, ~2.5 % of the edge, and the resistor still damps the cable, which is the reflection
that matters. Write the derivation down; the next reader will otherwise see only that it is in
the wrong place.


## A slow generator: profile by OUTCOME, and measure reuse before you cache

**Scope: this is for a generator or router you OWN and can instrument.** If the slow stage is an
external tool — FreeRouting, a fab DRC, a solver — only the last two paragraphs apply; you cannot
bucket a return value or key a cache inside someone else's binary, and the lever there is inputs,
staging and parallel runs instead. Evidence below is one 4-layer, 169-connection board whose
`--full` went from never finishing a second round in 44 minutes to 2 min 58 s, byte-identical
`.kicad_pcb` throughout. Treat the numbers as a worked example, not as thresholds.

**Slice the profile by outcome or phase, not only by function.** A function-level sampler put the
spatial index at about half the run, which reads as "make the index faster" — and rewriting it,
benchmarked against 40 000 recorded real queries, was worth **1.12x on whole-generator runtime**.
One `perf_counter` around the top-level routing call, bucketed by return value, said something the
function profile structurally could not, because both outcomes share the same stacks:

```
routed  n=138    4.9s    mean=0.04s
FAILED  n=411  128.3s    mean=0.31s     <- 96% of the 133.2 s inside route()
```

Nearly all the time went to searches that **fail**. In this bounded candidate search a success
stopped at the first candidate that cleared, while an expensive failure usually exhausted the
candidate families — not always, since some calls returned early. Your generator will have a
different asymmetry; the transferable part is that aggregate function attribution hides which
*outcome* pays, and one timer keyed on the result exposes it.

**Measure reuse before you cache, and measure it under the cache's own lifetime.** Reading the
source suggested 20–35 % duplicated work; counting measured **85.9 %** (22 887 753 repeats of
26 638 958 clearance tests). That gap is worth one instrumented run — but a global duplicate count
is *not* the number that decides it, and the same board proves why. A first cache invalidated
correctly on every mutation and scored **1142 invalidations against 14 hits, no measurable
speedup**. Cache utility is temporal locality relative to the invalidation boundary, so count hits
that would **survive the proposed lifetime**; global duplication overestimates cacheability.

**Then decide keying versus invalidation as a trade-off.** Naming the one varying dependency in the
key, instead of clearing on it, took that cache from 14 hits to 116 and 1.53x — measured
instrumented-build against instrumented-build, 138.6 s to 90.4 s user; the same design with the
counters stripped runs 86.4 s. Weigh at least: churn rate against reuse distance, whether the key
names *every* correctness dependency, hit and miss cost, key construction cost, and whether
superseded generations are bounded. Keying on a monotonically rising revision, for instance, leaves
every old generation resident but unreachable — memory grows per edit unless eviction bounds it,
where a clear would have been simpler and cheaper.

**Do not cache at all** when the correctness dependency cannot be completely named or reliably
invalidated, when an end-to-end benchmark including cache overhead shows no material win, or when
an algorithmic change removes the repeated work instead of memoising it.

**Verify served hits, not stored ones.** A cache can only get one thing wrong that matters:
*serving* a verdict the current state no longer supports. So add a diagnostic mode that recomputes
on the **hit** and raises on disagreement — checking at insertion recomputes what you just computed
and can never fail. Calibrate it by storing a negated verdict and watching it fire.

**An artefact hash will not do that job for you.** Comparing a byte-identical output before and
after a change is an excellent whole-file regression oracle and far stronger than comparing
violation counts — but it establishes that *the artefact of the two tested runs is identical*, not
that the mechanism producing it is correct. On this board an A/B with a deliberately **unsound**
cache key produced a byte-identical file, because the unsound branch was never exercised in a
verdict-changing way. Hash the complete intended artefact, after confirming the compared run
actually succeeded — see *Making a `pcbnew` layout reproducible* above and `SKILL.md`'s
reproducibility check for the preconditions, which this does not replace.

**Finally: if an omission is load-bearing, say so at the omission.** Removing an invalidation
because its dependency moved into the cache key leaves a site that looks exactly like one where the
invalidation was *lost*. Here a second reader restored it, reinstating the version already measured
as useless and silently giving back the speedup. Note it where the code is missing, not 500 lines
away at the compensating mechanism — this is for surprising, load-bearing absences that a
conventional invariant says should be there, not for ordinary deletions.


## Is it ready to fab? — manufacturable and final are different questions

A board can be DRC-clean, parity-clean and perfectly manufacturable and still be the **wrong
board to order**. Separate the two questions, because they have different blockers and people
conflate them:

- **Manufacturable** — can a fab build this from the data. Geometry, exports, stackup.
- **Final** — is this the revision you want in your hand. Any pending change that lands on the
  *fabricated artefact* is a blocker here, and that includes **silkscreen**, not just copper.
  A missing revision marker or a dropped hazard warning is a respin exactly like a missing
  resistor is.

Everything that is neither — the BOM's MPNs, an interface contract, a schematic note's
numbering — is an assembly or release concern. Say which bucket each finding is in when asked
"is it ready", or the answer collapses into an unhelpful "no".

### The manufacturability test is the export, not an opinion

Run it. It costs seconds and it is the only check that proves the data a fab receives is
complete:

```sh
$K pcb export gerbers --output fab  x.kicad_pcb
$K pcb export drill   --output fab/ x.kicad_pcb
$K sch export bom --output fab/bom.csv --group-by 'Value,Footprint,MPN' \
     --fields 'Reference,Value,Footprint,MPN,Manufacturer,${QUANTITY}' x.kicad_sch
$K pcb export pos --output fab/cpl.csv --format csv --units mm --side both x.kicad_pcb
```

Check every exit status **and** grep the logs — and confirm the copper layer count you expect
actually appears, since a 4-layer board that emits two copper gerbers is a stackup problem, not
an export problem.

Then measure the handful of numbers a fab quotes against, from the board rather than from
memory: min track width, via pad and drill, **annular ring** `(pad − drill) / 2`, min pad
drill, board outline extent, and an explicit `(stackup …)` block.

**Assert the outline is closed.** Count Edge.Cuts endpoints: every one must be shared by
exactly two shapes. An open outline still exports a plausible `.gm1`, and the fab discovers it.

```python
pts = collections.Counter()
for d in board.GetDrawings():
    if board.GetLayerName(d.GetLayer()) != "Edge.Cuts": continue
    if d.GetShape() in (pcbnew.SHAPE_T_RECT, pcbnew.SHAPE_T_CIRCLE): continue  # closed already
    pts[k(d.GetStart())] += 1; pts[k(d.GetEnd())] += 1
open_ends = [p for p, c in pts.items() if c != 2]
```

### The BOM trap: MPN fields live in the schematic, not the footprint

Testing `footprint.HasFieldByName("MPN")` on a board reported **71 of 71 components missing an
MPN**, which was wrong and briefly went into a review. Symbol fields do not propagate to
footprints unless the project is configured to push them. Read MPNs from `netlist.net` or the
schematic — the same board had 67 of 71, the exceptions being mounting holes, which need none.

This is *"bounded searches lie"* in a new costume: the test returned data, the data was
uniform, and uniformity read as a finding rather than as a broken probe. A result that
condemns *everything* deserves the same suspicion as one that condemns nothing.

While there, flag components whose Value is still a placeholder — a library name like
`R_Small` or a bare `R` on a fitted part means nobody can build it.

### Commit the outputs with the hash they came from

Export, then record the board md5 alongside the gerbers, and commit both. Otherwise there is no
way to prove later which copper is in the boards on the bench — and a board file that has been
opened and re-saved in the GUI since the export will not match, for reasons that are pure
reordering (see *`LoadBoard` → `Save` does not round-trip*) and therefore invisible in a diff.
