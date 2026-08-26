# `pcbnew` scripting: API notes, reproducibility and generator speed

Board *scripting* — driving `pcbnew` from Python, making its output reproducible, and
making a slow generator fast. Layout judgement lives in [`PCB.md`](PCB.md); land
patterns in [`FOOTPRINTS.md`](FOOTPRINTS.md); release checks in
[`RELEASE.md`](RELEASE.md). Read [`GUARDS.md`](GUARDS.md) when the script emits or
calibrates domain checks, and for the semantic zone-fill finalization contract.

## Contents

- [Probe the installed pcbnew API](#pcb--pcbnew-notes)
- [Make layout generation reproducible](#making-a-pcbnew-layout-reproducible--there-are-two-causes-not-one)
- [Treat geometry helpers as guards](#geometry-helpers-are-guards-and-fail-the-same-way)
- [Profile slow generators by outcome](#a-slow-generator-profile-by-outcome-and-measure-reuse-before-you-cache)

## PCB / `pcbnew` notes

Run layout scripts with KiCad's **bundled** Python — `pcbnew` is not importable from a normal
venv:

```
/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3
```

Allow only the exact `assert "traits" failed in Get(): create wxApp before calling this` diagnostic
for a recorded, qualified KiCad/bundled-Python/OS/architecture/invocation cell. Accept that run only
when its exit status succeeds and every required artefact is freshly produced, parseable and passes
its postconditions. Do not generalize this allowance to another assertion or toolchain cell. API
traps met in practice, with the name that actually works — **re-probed on KiCad 10.0.5 on
2026-08-09**:

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
| `cc.GetNetItems(code, pcbnew.PCB_PAD_T)` | `TypeError … argument 3 of type 'std::vector< KICAD_T >'` | avoid; use `kicad-cli pcb drc`'s unconnected pairs, per the DRC note in [`RELEASE.md`](RELEASE.md) |

A **third** batch, measured 2026-08-26 on 10.0.5 while generating an inverter board (zone-fill
settle checks and netclass setup):

| you reach for | what happens | use |
|---|---|---|
| `SHAPE_POLY_SET.PolygonCount()` | `AttributeError` | `OutlineCount()` / `FullPointCount()`; for settle comparisons prefer `Area()` |
| `BooleanXor(...)` as a pure function | returns `None` — it **mutates the receiver in place**, and XOR against an identical set empties it | snapshot first with the copy constructor `SHAPE_POLY_SET(other)`, XOR the copy, then read `OutlineCount()`/`Area()` off the copy |
| netclass creation via `BOARD_DESIGN_SETTINGS.m_NetClasses` / `.Add` | `AttributeError` — those legacy names are gone on 10.0.5 | in-memory mutation exists via `BOARD_DESIGN_SETTINGS.m_NetSettings` (`NET_SETTINGS.SetNetclass(name, NETCLASS)` inserts and reads back). But netclasses do not live in `.kicad_pcb` anymore — `(net_class …)` blocks in a board file are KiCad ≤5 format — so for *persistent* netclasses write the `.kicad_pro` JSON under `net_settings.classes` |

Two zone-fill settle-check lessons from the same run, companions to the UUID-ordering section
below: key fill snapshots by **zone UUID** (`zone.m_Uuid.AsString()`), never by `(net, layer)` —
multiple zones legitimately share a net and layer, and the key collision makes the settle check
compare one zone's fill against another's (measured: 5 zones collapsed to 3 keys, reported as
"did not settle"). And two consecutive fills of an unchanged in-memory board were once observed
to differ by an nm²-scale XOR sliver (3 nm², one zone, one board, one machine) — cause not
established; UUID-ordering effects are documented below for *cross-run* order, not for two fills
of the same loaded board. Per `GUARDS.md`, the default verdict for any nonzero XOR remains
unsettled; if a project decides to tolerate slivers, derive its threshold from that board's
smallest copper feature, record the provenance, and always report the sliver's area and zone
rather than silencing it.

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
wrong for every *other* subclass. Re-probe on any version change, name the exact type and method
you probed, and use
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
  preference" ([`PCB.md`](PCB.md)) says must never be left to a default. Opening a board you do not own is a
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
setup the SWIG binding does not do. Treat it as the empty-result failure described in
[`GUARDS.md`](GUARDS.md). Fall back to something that is definitely computed: `kicad-cli pcb drc` writes the
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

For a generator-owned board, a refill is not complete merely because the board was filled once
or because the fill happened to run inside an orphan-stitching branch. Use the flow in
[`GUARDS.md`](GUARDS.md): initial fill → discover and place legal orphan fixes → unconditional
final refill outside that branch → snapshot filled geometry → refill the same loaded board →
require an empty per-zone `BooleanXor` with unchanged topology → save. Compare zones by stable
semantic identity and fail if pairing is ambiguous. Measure save/reload/refill behavior separately
on a scratch copy; report a cycle and any guard verdict it flips instead of gating an empirically
unreachable fixed point. Keep in-memory semantic settling, save/reload stability and byte
reproducibility separate; a stable P−N difference can hide equal common-mode movement of both
pours.

**A zone SETTING can destroy copper asymmetrically, and nothing checks settings.**
`island_removal_mode` on a current-path plane had reverted from `NEVER` to `ALWAYS`, and with
it went **37 mm² from one inner plane and not its mirror** — In1 856.21 mm² against In2
825.65, a 30.56 mm² imbalance where the two had previously been bit-identical. No layout
changed. The mechanism is the one under *A via that lands outside its pour* below: a
foreign-net via cuts a corner off both planes, then on one plane the stitching row is that
plane's own net and re-anchors the orphan, while on the other it is a keepout — so one plane
keeps the block and the other deletes it. DRC is silent, every geometric audit passes, and the
decision to disable island removal was recorded in prose with nothing enforcing it.

Carry these checks:

- **Put zone settings in the audit, not just zone geometry.** Fill settings, pad connection
  mode and island removal are all load-bearing and all revert invisibly — a `.kicad_pro`
  rewrite, a GUI round-trip, or a zone copied from elsewhere.
- **A track endpoint sitting on top of a zone is not connected to it** if they
  are on different layers. It needs a via. DRC reports this as `track_dangling`
  plus an unconnected item; both point at the same missing via.
- **A via that lands outside its pour is silently unconnected.** After any placement change,
  assert every plane via falls inside a filled area of the zone it is supposed to reach; otherwise
  move the via or extend the intended zone and assert the resulting fill inventory.

For planes that spread heat, exposed-pad connection mode, island retention and mirrored stitching
are thermal design choices rather than generic `pcbnew` mechanics. Apply
[`THERMALS.md`](THERMALS.md) before setting them or waiving `isolated_copper` or
`starved_thermal` findings.

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
every geometric check reports PASS. After any fill-dependent edits, run the unconditional final
fill and in-memory semantic-settle check above. Characterize save/reload/refill separately. Do not
iterate until scalar `Area()` stabilizes; equal areas can describe different shapes and repeated
filling can settle on the wrong property.

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

For an owned deterministic router, bind route and task order, candidate ordering, cost weights,
objective and tie-break rules as tracked generator inputs whenever they affect the emitted board.
Report the actual objective achieved; fewer vias or segments is not proof of optimality without an
exhaustive result or solver certificate.

## A slow generator: profile by OUTCOME, and measure reuse before you cache

**Scope: a generator or router you OWN and can instrument.** For an external tool — FreeRouting, a
fab DRC, a solver — you cannot bucket a return value or key a cache inside someone else's binary;
the levers there are inputs, staging and parallel runs, and only the artefact-hash and
load-bearing-omission rules at the end of this section apply.

- **Ship the instrumentation resident, default-on, and write it in before you need it.** "Why did
  this take so long?" must be answerable from an ordinary run; a second instrumented run means the
  data is missing exactly when the question is asked. Print wall time per coarse phase (placement,
  routing, zone fill, canonicalisation, save) and search/retry counts bucketed by OUTCOME with mean
  and share of total — in stdout and in the returned stats. Keep phases coarse and aligned with
  actionable decisions, and check callers before wrapping an entry point: recursion and retries
  double-count, and a bucket that silently sums a nested sub-search into its parent misattributes
  the very asymmetry you are hunting. Legitimate opt-outs, provided rather than argued away:
  timers inside functions called millions of times, timing-perturbed concurrent or deadline code,
  and byte-reproducible artefacts, which must exclude wall times by construction.
- **Report timings as measurements, never impressions.** A duration recalled from waiting is not a
  measurement and invites diagnosing a phase that was never the cost.
- **Slice by outcome or phase, not only by function.** Success and failure share the same stacks,
  so a function profiler cannot show which outcome pays; one timer bucketed on the return value
  can. Measured: 96 % of one router's runtime was in FAILED searches, while the profiler-suggested
  index rewrite was worth only 1.12x end to end.
- **Measure reuse before you cache — under the cache's own lifetime.** Global duplicate counts
  overestimate cacheability; count hits that survive the proposed invalidation boundary. Measured:
  a cache invalidating correctly on every mutation scored 1142 invalidations against 14 hits and no
  speedup; keying the one varying dependency instead of clearing on it made the same cache 1.53x.
  Weigh churn against reuse distance, whether the key names EVERY correctness dependency, hit, miss
  and key-construction cost, and whether superseded generations are bounded. Do not cache when the
  dependency cannot be completely named or reliably invalidated, when an end-to-end benchmark shows
  no material win, or when an algorithmic change removes the repetition instead.
- **Verify SERVED hits, not stored ones.** The one cache failure that matters is serving a verdict
  the current state no longer supports. Add a diagnostic mode that recomputes on the hit and raises
  on disagreement — insertion-time checks recompute what was just computed and can never fail — and
  calibrate it by storing a negated verdict and watching it fire.
- **An artefact hash does not verify the mechanism.** A byte-identical A/B is a strong whole-file
  regression oracle, yet a deliberately unsound cache key still produced a byte-identical file
  because the unsound branch never changed a verdict in the tested runs. Hash the complete intended
  artefact after confirming the compared run succeeded — see *Making a `pcbnew` layout
  reproducible* above and `SKILL.md`'s reproducibility check, which this does not replace.
- **If an omission is load-bearing, say so at the omission.** An invalidation removed because its
  dependency moved into the cache key looks exactly like a lost one; a later reader will restore
  it and silently give back the speedup. Note it where the code is missing, not at the
  compensating mechanism — for surprising, load-bearing absences only.

The complete measured case (one 4-layer, 169-connection board, `--full` from not finishing a
second round within 44 min to 2 min 58 s, byte-identical `.kicad_pcb` throughout, with the
duplicate counts, hit rates and A/B numbers) is
preserved in this file's history at commit `1a4ce0a` and in the knowledge-base note
`~/dev/kb/tooling/pcbnew-generator-outcome-profiling.md`. Treat its numbers as one board's
asymmetry, not as thresholds.
