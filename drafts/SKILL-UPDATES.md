# Proposed additions to the kicad-design skill

Staging file. Each entry below is a lesson from a real session, with the cost that earned it
and the target file/section it belongs in. Nothing here has been folded into `SKILL.md`,
`PCB.md` or `SETUP.md` yet — that is a separate, deliberate edit, so that a lesson can be
argued with before it becomes a rule.

**Source of every entry:** designing `pwr-metering/hw/shunt-reversal` (2026-08-17) — a
current-reversal H-bridge for shunt thermal-EMF cancellation. 16 paralleled 100 V MOSFETs,
50 A, 4-layer, a generated schematic (19 domain guards, 25 calibrations) and a generated
board. Relevant property of that board: 1 ppm of its working signal is **20 nV**, so a
geometric asymmetry an ordinary board would not notice is a first-order measurement error.
Several entries below are only *visible* on a board like that but are *true* of any board,
which is why they are proposed as general rules.

Provenance, because it argues for keeping the expensive parts of the process: §§16–21 came
from acting on an **independent codex review**; §§22–35 from a **routing subagent** that twice
corrected a number I had accepted and propagated; §§38–42 from the **user simply asking "why so
many resistors?"**, which deleted 29 % of the BOM. Three different external readers, none of
them a guard, and between them most of this file. §§36–37 and §43 have a fourth source —
**measuring instead of reasoning about a slow generator** — and are the only entries here where
a second codex review then attacked the fix rather than the design; two of its findings were
real defects, one of them in the guard I had just written to protect the change.

§§44–52 have a fifth source — **closing the board's last 9 unrouted connections** (2026-08-18)
— and they are the only entries here written while replacing an algorithm rather than fixing a
defect. Two of them (§47, §51) exist because a confident diagnosis of mine was measured and turned
out to be wrong, and one (§49) because the obvious form of a calibration I had just written for
§48 would have passed without testing anything.

**Before folding in, merge these clusters** — they are the same lesson found from different
directions, and folding them separately would bloat the skill: §33 + §34 + §38 (*a real number
attached to the wrong cause / population*), §29 + §35 (*a copper test must consider every
layer's fill*), §13 + §42 (*a table or range typed apart from its subject goes stale, including
across files*), §11 + §40 (*encode the premise as executable code, then interrogate it*),
§25 + §48 + §49 (*a calibration that fires for the wrong reason, has expired, or was never
differential*), §9 + §52 (*silk tests, and which object each one is actually about*).

Verified against the current skill text before writing: none of the greps for
`package page`, `assets.infineon`, `ground_pin_not_ground`, `centroid`, `pad centroid`,
`obstacle`, `connected-component`, `F.Fab`, `wrong reason`, or "text off the page" matched
anything in `SKILL.md`, `PCB.md` or `SETUP.md`. The two near-misses are noted in the entries
that touch them (§2 extends an existing RASTER bullet; §9 extends an existing silk bullet).

---

## 1. The land pattern may live on the vendor's PACKAGE page, not in the part datasheet

→ **`SETUP.md`**, new subsection under *Getting the PDF*; cross-reference from `SKILL.md`'s
*A stock footprint that matches by vendor and body size can still be the wrong land*.

`ISC022N10NM6.pdf` (Infineon, 12 pages) contains a package **outline** with a full dimension
table and **no land pattern at all**. Reading the whole PDF and grepping it — which the skill
already tells you to do — correctly returns nothing, and the honest conclusion from the
datasheet alone is "Infineon does not publish a recommended land for this package". That
conclusion is wrong.

Infineon publishes recommended footprints per **package**, not per part:

```
https://www.infineon.com/package/<PACKAGE>          e.g. PG-TSON-8-3
   → linked asset: assets.infineon.com/is/image/infineon/infineon-<package>-fpd-png-footprint-en.png
```

That drawing carried everything needed: copper shapes with dimensions, solder-mask note, and
the full stencil aperture set. **KiCad 10.0.5 ships no footprint for this package**, so
without it the only options were a wrong stock land or an invented one.

Generalise the rule, not the URL: **when a datasheet has an outline but no land, look for a
package-level document under the vendor's package/packaging section before concluding it is
unpublished.** TI (`ti.com/packaging`), Infineon (`/package/<X>`), Nexperia and onsemi all
have one. This is the same failure shape as the existing "package drawings live at the END of
the datasheet" bullet, one level up: the document is not in the file you are reading.

Two mechanics worth recording with it:

- **Infineon's document path is not guessable.** The current form is
  `/content/dam/infineon/row/public/documents/NN/NN/infineon-<mpn-lower>-datasheet-en.pdf`,
  and `NN/NN` is a content-management shard, not a family code. `24/49` served this part;
  `10/49`, `25/49` and `24/50` all 404'd. Get the path from the product page (which needs the
  same UA) and do not try to derive it. The old `/dgdl/?fileId=...` form no longer appears in
  the page source at all.
- **The image-server size presets are limited.** `?wid=2000` returned 200; `?wid=6000`
  returned **403**. Do not read a 403 on an asset URL as "blocked" when a smaller preset
  works — it is a preset whitelist, not a WAF.

---

## 2. Verify raster callouts by measuring the same image, programmatically

→ **`SKILL.md`**, extending the existing *The figure may be a RASTER* bullet (line ~1052),
which currently ends at "render and read it" and the callout-vs-scaled distinction.

The existing bullet gets you to *read* the callouts. It does not tell you how to check that
you assigned them to the right features — and on a footprint drawing with nine dimensions,
three of which are ambiguous from the picture, that assignment is the whole job.

Cheap method that settled it in one pass: **threshold the copper fill colour, take
connected-component bounding boxes, calibrate px/mm on one printed callout pair, then check
every other callout against its measured feature.**

```python
a = np.asarray(Image.open(png).convert("RGB")).astype(int)
g = (np.abs(a - 127).max(axis=2) <= 2)      # the drawing's copper grey, exactly
lab, n = ndimage.label(g)                    # bboxes per component
# calibrate on the ONE dimension you are certain of, then predict the others
```

Result on this drawing: the two dimensions used for calibration predicted **eight** further
features to within **0.04 mm** (source land 3.04 measured vs 3.08 printed, gate centre +1.934
vs +1.905, source left edge −2.159 vs −2.175, lead-row bottom −3.277 vs −3.25, drain top
+3.277 vs +3.25, …). That agreement is what justified using the printed numbers and discarding
the measured ones — the skill's rule that a scaled length is a model
still holds; measurement is used only to *verify the assignment*.

Two traps found doing it:

- **Threshold the FILL, not the ink.** Connected-component labelling over all non-white
  pixels merges every shape that a dimension line touches — the first attempt returned one
  component containing the whole drawing. Keying on the exact fill grey separated the copper
  cleanly.
- **Dimension lines still split a fill into several components.** Take the **union** of the
  components belonging to one shape, exactly as the existing multi-pad-net rule says: the
  drain land came back as four boxes plus the notch stubs.

---

## 3. When several instances feed one node from different pin groups, align them on the relevant PAD-GROUP axis, not the footprint origin

→ **`PCB.md`**, new bullet in the placement section. This is the highest-value entry here.

Infineon's `PG-TSON-8-3` land centres the merged **drain** land on the footprint origin
(x −2.225…+2.225) but not the merged **source** land (x −2.175…+0.905). The centre of
the rectangular source-land union therefore sits **0.635 mm** to the left of the drain-land
centre, in the footprint's own frame.

Place a group of these on a naive grid and the consequence is invisible: everything is on
pitch, every courtyard clears, ERC and DRC and the netlist are all clean. But in an H-bridge
two different leg groups connect to the *same* node through *different* pin groups — one leg
by its sources, the leg opposite by its drains — so the two enter that node **0.635 mm
apart**. On a four-terminal sense node that is a current-entry geometry that differs between
the two switch states, i.e. an effective resistance that changes synchronously with the
measurement. Nothing in the electrical model touches it.

Rule: **derive the per-instance offset from the footprint's real copper geometry, at build
time.** Be precise about the quantity: the mean of pad origins is a *terminal-centre axis*,
not a copper centroid. It only equals the copper centroid when the pad partition and shapes
make it so. Multiple physical pads may also share a pad number, so a dictionary keyed by pad
number silently discards geometry.

```python
def _pad_group_copper_axis(fp, nums):
    wanted = set(nums)
    pads = [p for p in fp.Pads() if p.GetNumber() in wanted]
    found = {p.GetNumber() for p in pads}
    need(found == wanted, f"missing pad numbers {sorted(wanted - found)}")
    shapes = [p.GetEffectiveShape(pcbnew.F_Cu) for p in pads]
    # Union before taking the area centroid: overlapping or repeated-number
    # pads must not be double-counted. These are project geometry helpers.
    return area_centroid(copper_union(shapes)).x

# groups that reach the shared node by SOURCE are shifted by (drain_cx - source_cx)
```

For this generated footprint the split strips partition rectangular source and drain unions,
so the copper-axis result is exactly −0.635 mm / 0 mm and the old mean-of-pad-origins probe
happened to agree. That coincidence is not the general rule.

…and then **audit the result on the built board**, from the real pads and filled copper, not
from the placement table that produced them. The first audit compares each leg pair's
pad-group axis and the terminal's own position; where current entry is load-bearing, also
compare the two entry gaps and the filled-copper spreading regions. A centroid is a placement
starting point, not proof that the routed current paths are equivalent.

Generalises beyond H-bridges to: paralleled devices sharing a sense node, current-sense
shunts with asymmetric lands, any Kelvin connection, and matched pairs where one is placed by
a different pin group than the other. **A footprint is not symmetric just because the package
is.**

---

## 4. Every requirement inherited from a companion board must be re-derived, and the number depends on the impedance the error lands in

→ **`SKILL.md`**, *Close every external interface* or a new *Inherited requirements* bullet.

The companion board's design document specified `R_leak > 3.4 × 10¹¹ Ω` for gate-net isolation
and stated it as a board requirement. Copying it onto this board would have been the obedient
thing to do, and it would have been wrong by six orders of magnitude — in the *loose*
direction on paper and in the tight direction in effort.

That figure is derived from 35 pA developing 35 nV across a **1 kΩ** series sense leg. On this
board the gate nets are adjacent to the **low-impedance power nodes** instead, where the same
leakage is diluted by a 1 mΩ shunt rather than amplified by 1 kΩ. Re-deriving gives

```
R_leak > V_gate / (ppm_budget × I_load) = 10.5 V / 13 µA = 0.8 MΩ      for 1 ppm
```

which FR4 bulk beats by six orders of magnitude. The big number still binds — on the *other*
board, where the 1 kΩ legs are and where the gate nets do not go.

Rule: **a leakage, coupling or noise requirement is a statement about a victim node's
impedance, not about a net name.** When you carry one across a board boundary, re-derive it
and write the victim impedance next to the number. And say explicitly where the original
figure still applies, so the next reader does not conclude it was dropped.

---

## 5. A part-class rejection is a claim about a part, not about a topology

→ **`SKILL.md`**, *Never quote a spec from memory*, beside the *value × voltage × package*
bullet.

Sizing a gate-source clamp, I read one candidate's leakage — an MMBZ A-series SOT-23 dual TVS
zener, `I_R` **max 50 µA** at `V_RWM` — computed what it would cost (3.8 ppm against a 1 ppm
allocation), and concluded *clamps cannot be used in this position*. I then wrote three
paragraphs of design rationale around a topology change that removed them.

`MMBZ15VDLT1`, the same SOT-23, same dual-zener function, same clamp voltage class,
specifies `I_R` **max 100 nA** — **500× lower**, and the clamp is fine. The rejection had
generalised from a sample of one, and the "engineering" built on top of it was fiction.

Two rules:

- **Before rejecting a component class on a parameter, check the spread of that parameter
  across the class.** Leakage, `I_R`, `I_CBO`, bias current and offset drift routinely span
  two to three decades within one package and function. One part's number bounds one part.
- **When a decision rests on one parameter of one MPN, put the MPN and that parameter in the
  BOM `Spec` field and have a guard check the MPN by name.** A same-looking substitution is
  invisible to ERC, DRC, the netlist and the render; here it would have been a 500× change in
  a measurement error. The guard is three lines:

  ```python
  if PROPS[ref].get("MPN") != "MMBZ15VDLT1G":
      raise AssertionError(f"{ref} is {PROPS[ref].get('MPN')!r}, not the part whose "
                           "100 nA IR the ppm budget rests on")
  ```

This is the mirror image of the existing "the incumbent string can be the stale one" rule:
there, an existing part number was wrongly trusted; here, a single datasheet was wrongly
generalised.

---

## 6. Verify a footprint's pin-row axis by measuring pad-row coordinates, not by reasoning from its name

→ **`PCB.md`**, placement section.

`Package_SO:SOIC-16W_7.5x10.3mm_P1.27mm`. Which of 7.5 and 10.3 is the pin-row axis? I
reasoned it out twice and got it backwards once, then "fixed" a correct placement into a
wrong one on the strength of the second reasoning. For a part that must straddle a **vertical**
isolation barrier, getting it backwards puts the body across the band with **both** pin rows
on the same side of it.

Five lines of probe settle it for good, and cost less than the reasoning did:

```python
xs = [pcbnew.ToMM(p.GetPosition().x) for p in fp.Pads()]
ys = [pcbnew.ToMM(p.GetPosition().y) for p in fp.Pads()]
print(ref, fp.GetOrientationDegrees(), "pad x", min(xs), max(xs), "y", min(ys), max(ys))
# SOIC-16W rot 0  -> pads x 126.35..135.65   (rows LEFT/RIGHT)
# MIE1W LGA-12 rot 90 -> pads x 128.91..133.09
```

Rule: **for any placement whose correctness is relative to an axis — an isolation barrier, a
board edge, a plane split, a connector face — print the pad centres (and, where the physical
edge matters, each pad shape's bounding box) after placing and assert the span, rather than
deriving it from the footprint name.** Body dimensions in KiCad footprint names are
`width x length`, and which one carries the pins is not recoverable from the name. Entry 7
adds the domain-aware check needed to distinguish the two possible 180° orientations.

---

## 7. Barrier-crossing parts need a domain-aware replacement for the check that exempts them

→ **`PCB.md`**, isolation section. Companion to the entry above and to the existing
*An exemption must be scoped to the pair* bullet, which is about clearance exemptions; this is
about an exemption swallowing the very thing it should verify.

The barrier check is necessarily written as *"no copper in the band, except for the parts
whose own body provides the isolation"*. Those parts are then the **only** ones whose
geometry and electrical orientation relative to the band matter, and they are exactly the
ones the check skips. U3 rotated through 90°, with its pin rows on the wrong axis, passed in
silence. Later, all three bridging parts were found rotated **180°**: they still straddled
the band, but presented their host pins to the isolated side and their isolated pins to the
host side.

Add the **positive, domain-aware** half: every bridging part must present both domains, and
the host and isolated pin groups must be on their intended sides. Do **not** require pad
centres to lie outside the band. A legitimate bridging package may have row centres inside
the nominal band while its own package geometry supplies the certified isolation; the router
then reaches the appropriate pad edge rather than routing to the centre.

```python
for ref in BRIDGING:
    rows = {}
    for p in board.FindFootprintByReference(ref).Pads():
        domain = domain_of(p.GetNetname())       # "H", "I", or None
        if domain is not None:
            bb = p.GetEffectiveShape(pcbnew.F_Cu).BBox()
            rows.setdefault(domain, []).append((
                tomm(p.GetPosition().x),
                tomm(bb.GetLeft()),
                tomm(bb.GetRight()),
            ))
    need(set(rows) == {"H", "I"},
         f"{ref} does not present both electrical domains: {sorted(rows)}")
    need(min(v[1] for v in rows["I"]) < BARRIER_LO and
         max(v[2] for v in rows["H"]) > BARRIER_HI,
         f"{ref} does not physically bridge both edges of the barrier")
    need(max(v[0] for v in rows["I"]) < min(v[0] for v in rows["H"]),
         f"{ref} is rotated or mirrored: isolated {rows['I']}, host {rows['H']}")
```

Keep the package exemption scoped to that package's own pad shapes, and separately bound the
minimum host-to-isolated gap inside the package against its land pattern. Merely checking
`min(xs)` / `max(xs)` catches a 90° error but cannot catch the 180° error that swaps domains.

General shape, worth stating once in the skill: **whenever a guard has an exemption list, ask
what checks the exempted items.** An exemption without a compensating positive check is a
hole the size of the list.

---

## 8. Text running off the sheet is not an overlap, so an overlap guard cannot see it

→ **`SKILL.md`**, *The verification ladder* rung 4 / the text-extents box.

The skill's text-overlap guard is there and it works. A 28-line note anchored to a
*computed* y (`_bridge_bottom`, itself derived from a leg pitch) ran **12 mm off the bottom of
the A1 sheet**. It collided with nothing, so the overlap guard passed; ERC does not see text;
the netlist does not either. Only the render showed it, and only because I looked.

```python
PAPER_W, PAPER_H, MARGIN = 841.0, 594.0, 12.0
TITLE_BLOCK = (620.0, 555.0, PAPER_W, PAPER_H)

def guard_text_inside_sheet():
    if not TEXTS:
        raise AssertionError("UNVERIFIED: no text items to bound")
    for s, x, y, size in TEXTS:
        x0, y0, x1, y1 = _text_box(s, x, y, size)
        if x0 < MARGIN or y0 < MARGIN or x1 > PAPER_W - MARGIN or y1 > PAPER_H - MARGIN:
            bad.append(...)                      # off the paper
        elif x1 > TITLE_BLOCK[0] and y1 > TITLE_BLOCK[1]:
            bad.append(...)                      # under the title block
```

The title-block clause matters as much as the edge clause: text there plots *on the page* and
*under the frame*, so it is present, legible-looking in a thumbnail, and unreadable in the
plot. Both fire in the calibration.

Broader form of the lesson, worth one sentence in the ladder: **a bounds check and an overlap
check are different guards.** Anything positioned by arithmetic rather than by a literal
needs the bounds check, because the arithmetic is exactly what moves.

---

## 9. KiCad's `silk_overlap` does not compare silk against courtyards

→ **`PCB.md`**, silkscreen section; extends `SKILL.md`'s existing "silk needs a side choice,
not a fixed offset" (line ~684), which is about DRC's own rules.

`silk_overlap` is KiCad's silk-to-silk readability check. The separate
`silk_over_copper` check covers solder-mask openings / exposed copper. Neither compares silk
against footprint courtyards or body extents, so a board-level caption drawn straight through
a row of transistor bodies can still be DRC-clean. Two captions did exactly that on the first
render here, and a third struck through two terminal lugs.

```python
for tag, t in texts:                       # PCB_TEXT items you added
    tb = t.GetBoundingBox()                # DOES account for rotation - verified
    for fp in board.GetFootprints():
        cb = courtyard_bbox(fp)
        need(cb is not None, f"{fp.GetReference()}: no courtyard, silk unverifiable")
        if boxes_overlap(tb, cb): bad.append(...)
```

`GetBoundingBox()` on a rotated `PCB_TEXT` **is** rotation-aware (measured: a 25-character
1 mm string returns 22.18 × 1.60 mm at 0° and 1.60 × 22.18 mm at 90°), so rotating a label
into a narrow strip is a legitimate fix and the guard follows it correctly.

Related, and a real decision rather than a bug: **on a densely packed board the passive
reference designators may not fit on silk.** 0603s packed at 0.4 mm are narrower than their
own refdes text, so on `F.SilkS` each one necessarily overstrikes a neighbour's outline —
three of five silk DRC warnings here. Moving R/C references to **`F.Fab`** and keeping silk
references only for parts a human identifies by eye (the ICs, the transistors, the diodes, the
connectors) took the board to 0 violations. `F.Fab` preserves the references on a plotted
assembly drawing. Position files are generated from footprint records and do **not** read
`F.Fab`, so they are unaffected either way. What is lost is the passive's identifier on the
physical board, which can matter during inspection and rework; record that deliberate
trade-off in the design document rather than claiming nothing is lost.

---

## 10. A calibration needs its own guard: it must fire for the RIGHT reason, and its injection site must not be exempt

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Guards* as *a calibration is code, and it breaks in ways that look like it working*, compressed to one bullet naming all five failures.

→ **`SKILL.md`**, *Guards* — this is the single largest addition proposed here, because five
calibration failures in one session exposed five different ways the harness or injection can
be wrong, and every one of them *looked* like it was working.

The skill already says to calibrate against a known-bad input and watch it fire. It does not
say that the calibration itself is code that can be broken. Observed failures, all in one
guard suite:

1. **The harness expected the wrong exception contract.** `_expect()` caught only
   `AssertionError`, while the ledger functions the guards call deliberately raise
   `ValueError` for invalid inputs. Those calibrations reported "did not fire" for guards that
   had reached their intended refusal. The fix is **not** to count every `ValueError` or
   `AssertionError` as success: require the expected exception type **and** a message fragment
   identifying the intended arm. A silent return, an unexpected exception, and an expected
   type with the wrong message are three different calibration failures. Restore every
   injected mutation in `finally` (or a context manager) so a failed calibration cannot poison
   the ones that follow.
2. **The injection did not create the fault.** "Delete a wire and watch a pin dangle" popped
   the **last** `SEGS` entry — which was a `PWR_FLAG` stub whose removal dangles nothing. The
   calibration passed a guard that had evaluated a healthy design. Fix: remove every segment
   touching one **named** pin, and assert that the removal actually changed something:
   ```python
   kept = [g for g in SEGS if pp not in ((g[0], g[1]), (g[2], g[3]))]
   if len(kept) == len(SEGS):
       raise AssertionError("UNVERIFIED: nothing touches that pin, cannot calibrate")
   ```
3. **The injection site was exempt from the guard.** "Carry a host net onto the isolated side"
   injected on the isolator — which the isolation guard skips by design, as a declared barrier
   crosser. Injecting on any *ordinary* part fired immediately. **Check that your injection
   site is not on the guard's exemption list**, which is the same hole as entry 7 above seen
   from the other side.
4. **The injection tripped a different check first.** Moving a merged land's split lines to
   test pin-containment also changed the land's *union*, so the union check fired and the
   containment check was never exercised. Fix: keep the injection minimal enough that only
   the intended check can see it, **and match the message**:
   ```python
   except ValueError as e:
       if "wrong side" not in str(e):
           raise AssertionError(f"guard fired for the wrong reason: {e}")
   ```
5. **The chosen input was in the guard's blind region.** Flipping `RAILS[0]` to test
   "rail glyph drawn over its own wire" picked a rail whose wire is **horizontal**, and that
   arm of the guard only has a direction to compare for vertical wires. Fix: search for an
   input in the guard's *active* region and raise `UNVERIFIED` if none exists:
   ```python
   ri = next((i for i, r in enumerate(RAILS) if (r[1], r[2]) in vertical_wire_ends), None)
   if ri is None:
       raise AssertionError("UNVERIFIED: no rail sits on a vertical wire")
   ```

Proposed one-line summary for the skill: **a calibration counts only when the injection
actually created the intended fault, the input lies in the guard's active region, the site is
not exempt, and the guard raises the expected type and message. Check each claim explicitly.**

---

## 11. Encode the quantitative premise for rejecting an alternative as a guard, not as prose

→ **`SKILL.md`**, *Guards*. Writing this predicate exposed a documentation error that no
other check could, and the resulting guard prevents the corrected rationale from going stale.

When a design deliberately departs from a specification or a reference document, the design
note explaining why is prose — and prose does not get re-evaluated when the numbers move. So
add an arm to the relevant guard that encodes **the quantitative premise the rejection rests
on**. That premise may be outright failure, or—as here—a cost large enough to consume an
unacceptable share of an allocation:

```python
alt = pulldown_to_source_ppm(worst_case_current, GATE_PULLDOWN)
if alt < 0.25 * budget:
    raise AssertionError(
        f"the rejected alternative costs only {alt:.3f} ppm, so the note arguing "
        "against it buys nothing at these numbers -- re-derive it or delete the note")
```

At the fitted 1 MΩ value this arm does **not** fire: **0.81 ppm** is above the 0.25 ppm
"hollow rationale" threshold. Writing the predicate forced the alternative to be recomputed
and exposed that the existing prose was too strong; the note was softened from "all three
fail" to "one fails outright, one eats 81 % of a whole term for no benefit". The guard now
prevents a future value change from making that rationale pointless. Its known-bad calibration
sets the pull-down high enough to drive `alt` below 0.25 ppm and verifies the expected message.

So the accurate claim is: **turning a prose rationale into an executable predicate can expose
a documentation error while the predicate is being written, and thereafter keeps the revised
rationale true.** It is not evidence that the nominal 0.81 ppm case fired this particular
guard.

Cheap, and it keeps working: if a future rail voltage or resistor value makes the departure
pointless, the build fails instead of silently carrying a rationale that has stopped being
true.

---

## 12. Do not disarm a board-wide ERC rule to preserve one local net name

→ **`SKILL.md`**, the ERC severity-map discussion.

The bridge's fourth node is called `OUT` in the design document — it is the H-bridge's output,
and it is also the reference for the whole isolated domain. Naming the net `OUT` made KiCad's
**`ground_pin_not_ground`** fire. KiCad 10 documents the rule precisely: a power input/output
pin whose **pin name contains `GND`** is connected to a net whose **net name does not contain
`GND`**, while another power pin in the same symbol is connected to a `GND` net. The match is
case-insensitive. It is not decided by the power symbol's graphic, and "power symbol name" is
too imprecise a description of the mechanism. Renaming this genuine reference net to
`GND_OUT` was the only change needed to satisfy the heuristic.

The tempting fix is one line in `erc.rule_severities`. It is also the wrong one: setting that
rule to `ignore` disarms it for every ground pin on the board, permanently, to silence one
naming choice. Renaming the net to `GND_OUT` cost nothing and kept the rule armed.

Rule: **understand the rule's exact predicate before changing its severity.** If the net really
is a ground/reference, use a name that communicates that and satisfies the check. If it is not,
the warning may be exposing a misconnection; otherwise use a narrowly scoped waiver with a
reason. Do not globally ignore a board-wide rule to silence one naming choice. Worth adding
this as the worked example of the cheap way out, since the existing text explains *how* the
map can silently disarm checks but not *why* someone reaches for it.

---

## 13. A region packer must be obstacle-aware, and its membership table must not be a second copy of the BOM

→ **`PCB.md`**, placement section. Two failures of the same generator.

Packing ninety passives into declared rectangles is the right way to avoid hand-placing them —
a deterministic packer is still a generator. Two things it must do that the first version did
not:

- **Know about the explicitly placed parts.** Rows walked straight through the three ICs
  inside the same region: **sixteen courtyard overlaps** on the first run, fifteen of them a
  packed row sitting on top of an IC. Seed the obstacle
  list from the explicit placements, jump `cx` past a blocking courtyard, and bound the retry
  loop so an unplaceable part raises instead of spinning.
- **Derive the membership of the catch-all group from the board, not from typed refdes
  ranges.** The first version listed the diagnostic dividers as `R41..R55` and `C22..C32`; the
  schematic numbers its filter capacitors from the channel index, so `C25`–`C27` never
  existed and the packer refused to run. Name groups by *rule* where a rule exists, and put
  the remainder into a catch-all computed from the footprints actually on the board:
  ```python
  expected = on_board - explicitly_placed
  named = [r for refs in rule_groups.values() for r in refs]
  if len(named) != len(set(named)):
      raise ValueError("a footprint appears in more than one rule group")
  claimed = set(named)
  rest = sorted(expected - claimed, key=refdes_key)
  groups[CATCH_ALL] = rest                 # an empty remainder is valid
  assigned = claimed | set(rest)
  if assigned != expected:
      raise ValueError(f"group coverage mismatch: {expected ^ assigned}")
  ```
  A catch-all that is empty because the rule-derived groups intentionally cover everything is
  valid; rejecting it makes a fully classified board fail. Prove the catch-all path separately
  with a calibration footprint that matches no named rule, and assert that it lands in the
  remainder. The production invariant is complete, duplicate-free coverage of every
  non-explicit footprint, plus a nonzero placed count when the board actually has parts to
  pack.

Also: read each footprint's **real courtyard** off the loaded board rather than keeping a
table of sizes. Call `fp.BuildCourtyardCaches()` before `fp.GetCourtyard(...).BBox()` when
working with a freshly built or loaded footprint, and assert that the resulting box is
non-degenerate. `PCB.md` already warns about degenerate courtyard results; this addition
should extend it with the cache-building mechanic. A footprint change then reflows the row
instead of silently overlapping.

---

## 14. Record the documents you deliberately did NOT keep, and why

→ **`SETUP.md`**, datasheet-cache section.

The FET's real thermal resistance on a 4-layer board wants Infineon's *SSO8 Package Thermal
Data Sheet*, which gives `Rth-CA` for 1s0p and 2s2p stacks with 12 thermal vias. It covers
**PG-TDSON-8-33/34/43/53** — not the PG-TSON-8-3 actually fitted. I fetched it, read it,
and then **deleted it**, because a non-applicable thermal document filed next to an applicable
datasheet is an invitation to quote it, and the numbers in it are exactly the numbers someone
would want.

The design document instead uses the part datasheet's own `R_thJA` = 50 °C/W (6 cm², **one**
layer, 70 µm, vertical in still air), states that the real figure is better, and **claims no
number for it**.

Rule: **a `datasheets/README.md` should have a short "deliberately not kept" section.** It is
the only place that distinguishes "we never looked" from "we looked and it does not apply" —
and the second is a finding worth as much as a spec.

---

## 15. Minor, verified, one line each

→ wherever they fit.

- **The orderable code may populate fewer pins than the package name implies.** TI's
  `DCH010515S` is documented as a 7-pin SIP; the packaging table shows the single-output
  variant as **`SIP MODULE (EDJ) | 4`** and the orderable part number as **`DCH010515SN7`**,
  not `DCH010515S`. Belongs with the existing MPN-suffix rules: check the *packaging
  information* table, not only the ordering guide.
- **`pdftotext` on a TI datasheet may omit the mechanical drawing entirely**, with the last
  pages carrying only the disclaimer. The package dimensions are then in a separate document,
  which is entry 1's lesson again.
- **A regulated isolated module stack bounds a rail from a datasheet accuracy spec; an
  unregulated module plus an LDO bounds it from a load-regulation curve.** Two 5 V regulated
  1 W modules with stacked secondaries gave a 9.51–10.51 V gate rail *and* a 5 V logic rail at
  the midpoint with **no LDO at all** — where the unregulated-plus-LDO version needed a
  preload resistor to stay inside its 10 %-load specification and a post-regulator whose
  input bound was itself unspecified at no load. Prefer the stack when the rail's *bound*, not
  its typical value, is what a downstream calculation rests on.
- **`kicad-cli pcb export svg` needs `--exclude-drawing-sheet`** for a placement render, per
  the existing rule — and `--page-size-mode 2` (board size) plus `rsvg-convert -w 2400` gives
  a reviewable raster without the frame's edge-to-edge rules masquerading as copper.
- **Derive an obstacle's extent from the footprint, and let the load fail closed.** Placing
  parts around an M4 lug needed the lug's courtyard half-width. Read from the footprint via
  `FootprintLoad` it is one call; typed as a literal it goes stale *silently* the moment the
  lug is resized — the parts would still be placed, just on top of it. The fail-closed load
  also earned its keep immediately: the footprint is `Lug_M4_Ring`, I wrote `M4_RingLug`, and
  because `FootprintLoad` returning `None` raises rather than defaulting, the error named the
  library and the generator it came from instead of producing a board with a wrong obstacle.
- **PROMOTED 2026-08-18 (this bullet only, into `SKILL.md` -> *The verification ladder*):**
  **`kicad-cli ... --exit-code-violations | tail` always exits 0** — the pipe makes `$?`
  belong to `tail`. Capture the status before piping, and judge DRC by the report contents
  regardless. Measured: exit 0 with 42 violations present.

---

## 16. A datasheet table's unit column is shared down the rows — take the unit from YOUR row

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Never quote a spec from memory*, as the first bullet of that list.

→ **`SKILL.md`**, *Never quote a spec from memory*. Added after the entries above, but it is
the highest-value item in this file: it is the one that changed a circuit.

Infineon's static-characteristics table, as `pdftotext -layout` renders it:

```
Parameter                          Symbol      Min. Typ. Max.  Unit  Note/TestCondition
                                               -    0.1  1.0         VDS=80V, VGS=0V, Tj=25°C
Zero gate voltage drain current    IDSS                          µA
                                               -    10   100         VDS=80V, VGS=0V, Tj=125°C
Gate-source leakage current        IGSS        -    10   100    nA    VGS=20V, VDS=0V
```

Both leakages read `- 10 100`. I took `nA` for both and recorded `I_DSS` max as 100 nA. It is
**100 µA** — the `µA` sits on the `IDSS` row, one line above the numbers it governs, and `nA`
belongs to `IGSS`. **Wrong by 1000×**, and it survived a full guard suite because the guard
was fed the constant rather than the table.

Cost: the two resistors sized to bound the all-off gate-source voltage bound it to **400 V
instead of 0.4 V**, i.e. bound nothing, and the design note explaining that they made a
protection clamp unnecessary was exactly backwards. Caught only by an independent review, and
only because that review had been told what the units were worth.

Three things generalise:

- **A multi-row parameter block puts the unit on the parameter's own row, not next to each
  value line.** In `-layout` output that row can be above, below, or between the value lines.
  When two parameters with similar names and identical digits sit adjacent — `IDSS`/`IGSS`,
  `ICBO`/`ICEO`, `IIL`/`IIH` — the unit is the only thing distinguishing them, and it is the
  one column the eye skips.
- **Extract the unit and the test condition together with the value, as one tuple**, and put
  all three in the code comment beside the constant. `IDSS_MAX = 100e-9` says nothing; a
  comment reading `100 uA at VDS=80V, VGS=0V, Tj=125C` would have made the mismatch visible
  the next time anyone read the line.
- **A guard fed a constant cannot check the constant.** Every domain guard here passed a
  design whose central protection number was off by three decades, because they all consumed
  the same wrong value. The only defences are quoting the unit and condition next to the
  constant (above) and an independent reader with the datasheet — which is an argument for the
  codex-review step, not for more guards.

---

## 17. If a drawing labels details with letters, assert the letter sequence is CONTIGUOUS

→ **`PCB.md`**, beside the existing RASTER footprint rules; a natural companion to §2.

Infineon's PG-TSON-8-3 land drawing labels its stencil apertures with detail letters and a
multiplier: `D 4×`, `E 4×`, `F 3×`, `G 2×`, `H 1×`. I transcribed **D, E, F, H** — 12
apertures — and wrote `if len(APERTURES) != 12: raise` as the self-check. The check passed
for eighteen hours because it was asserting the same wrong number the generator produced.

`G` is two 0.43 × 0.45 apertures sitting on the strip of copper that the source land's notches
leave behind. Missing them is a real paste defect on a bottom-cooled package, and nothing in
KiCad would ever have said so.

What generalises:

- **A letter sequence with a gap in it is a transcription error until proven otherwise.**
  D, E, F, H is not a drawing convention, it is a missed row. Assert
  `letters == contiguous run from min to max` — the check costs one line and it is the only
  automatic defence against *omission*, which is the failure class a self-check written from
  the same transcription cannot catch.
- **A hardcoded expected count is a copy of the bug.** Spell the count out by its parts —
  `!= 14` is weak, `"(D 4x, E 4x, F 3x, G 2x, H 1x)"` in the failure message is what makes the
  next reader compare it against the drawing.
- **Prefer a cross-check that ties the new feature to an INDEPENDENT dimension.** `G`'s 0.45 mm
  height is not free: it must equal the land height minus the notch depth, both of which are
  separate printed callouts. Asserting `AP_G[1] == SRC_H - NOTCH_DEPTH` proves the aperture was
  placed on the feature it belongs to, not merely that some aperture of some size exists.

---

## 18. When two requirements squeeze a value from both sides, assert the FEASIBLE INTERVAL, not the chosen value

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Close every external interface*, as the closing bullet.

→ **`SKILL.md`**, *Close every external interface* / component-sizing rules. This is the
structural lesson behind §16, and it is more general than the unit error that exposed it.

Two node-definition resistors had to satisfy two requirements at once:

```
reverse V_GS under the 20 V absolute maximum   ->  R <= 5.75 kOhm
leakage injection under the 0.1 ppm allocated  ->  R >= 25 kOhm
```

**The feasible interval is empty**, and no value of `R` exists. The guard never said so,
because it was written the way component-sizing guards are usually written: take the chosen
value, compute both consequences, check each against its limit. Fed the (wrong) leakage number
it passed; fed the right one it would have failed with "R is too large" — which is *the wrong
diagnosis*, and would have sent the next person to try a smaller resistor, which fails the
other requirement. Neither message says "you cannot get there from here".

What generalises:

- **Compute the interval, then check membership.** `lo = f(constraint_a)`, `hi = g(constraint_b)`,
  `if lo > hi: raise "no value satisfies both"` before ever looking at the value actually
  fitted. An empty feasible set is a *different fault* from a badly chosen value and needs a
  different message, because the fix is a topology change rather than a BOM change.
- **An empty feasible set usually means a component is missing from the design, not that a
  number is wrong.** Here it meant the gate clamps — which the design already carried as
  belt-and-braces — were in fact the *primary* protection and always had been. The circuit
  was right; the story about why it was right was wrong, which is the more dangerous state,
  because the next person to "simplify" it deletes the part doing the work.
- **State which corner each bound comes from.** The two limits above are evaluated at different
  junction temperatures. A guard that collapses both onto one operating point cannot express
  "cold corner held by the resistor, hot corner held by the clamp", which is what the design
  actually does.

---

## 19. When a bound comes from a PART rather than from a formula, the guard must check the part is fitted — and calibrate one injection per MECHANISM

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Guards*, cross-referencing the promoted SS18 bullet.

→ **`SKILL.md`**, extending §10 (*a calibration needs its own guard*) and §11 (*encode the
quantitative premise as a guard*).

Once §18 resolved to "the clamps carry the hot corner", the guard's arithmetic was fine and
completely hollow: it computed a bound from `CLAMP_VBR_MAX + CLAMP_VF` and asserted it was
inside the absolute maximum. Both are module constants. **A board with no clamps on it at all
passes that check**, because nothing in it reaches for a component.

The repair is two lines — count the clamps, require `2 * N_PAR` of them — but the reasoning is
the transferable part:

- **A guard is only as strong as its weakest link to a real object.** If a safety bound is
  provided by a component, the guard must assert the component exists, is on the right net, and
  is the right MPN. Arithmetic over constants proves the *design intent*, never the *design*.
- **Calibrate one known-bad input per MECHANISM, not per variable.** The pre-existing
  calibration perturbed the resistor, which is the only variable the guard read. Both real
  failure modes — a substituted higher-breakdown clamp, and no clamp fitted — change no
  resistor, so a passing calibration suite proved nothing about either. Enumerate the things
  that *produce* the bound and inject a failure into each: here that turned one calibration
  into three.
- **Corollary for reviews:** when a review moves a bound from one mechanism to another, the
  guard *and* its calibrations both have to move. Fixing only the number leaves a guard
  watching the wrong thing, which is worse than the original error because it now looks
  deliberate.

---

## 20. Placement and router policy are coupled — a placement guard must prove every net is REACHABLE under the router's own region policy

→ **`PCB.md`**, new subsection near the placement checks. This is the most expensive entry in
this file: it cost two full routing passes, one mine and one an agent's.

The board's router policy is deliberately absolute — no auto-routing on either inner layer, and
none on the outer layers west of the power keep-out except inside sixteen hand-derived gate
escape windows. That policy is correct: copper in the power section perforates a 50 A pour and
runs beside a Kelvin-tapped node, so it must be explicit and measured.

Eight gate-clamp diodes were then floor-planned into the control section, where their *other*
terminal's net does not exist — it is a pour, in the half the router may not enter. Result: 12
unconnected pads and every dangling via on the board. Moving them into the power section fixed
that and created the mirror-image failure: now their *gate* nets had to cross a region the
router is barred from, and all eight failed again.

Every placement guard passed both times. They check courtyard overlap, isolation-barrier
crossing, thermal centroid and equipotential entry — none of them knows the router exists.

What generalises:

- **Reachability is a placement property, not a routing property, and it is checkable before
  any routing runs.** For each part, for each net it touches: does that net have another member
  or a pour *within the region set this part can legally be routed in*? A cheap version — "no
  net may have members in two regions with no legal corridor between them" — catches both
  failures above in milliseconds, against tens of minutes per routing attempt.
- **A part whose second terminal is a PLANE is placed by that plane, not by its schematic
  neighbours.** The instinct to group a part with the circuitry it logically belongs to is
  exactly wrong when one of its pads is served by a pour: it must live in the pour.
- **When a region is router-forbidden by policy, connections into it must be hand-laid and
  independently measured** — the same treatment the escapes already get. "The auto-router
  failed" is the *expected* outcome there, not a bug to route around, and the generator should
  say which connections it intends to lay by hand so the failure list is never ambiguous.
- **Silk offsets are relative to neighbours, so moving parts invalidates them.** Two lug labels
  at a fixed `+8.0 mm` offset had sat on empty copper for the board's whole life; the new
  clamps landed under them. Caught by the courtyard-aware silk check from §9 — which is that
  entry paying for itself.

---

## 21. A spanning tree over a net that already has hand-laid copper must be SEEDED with the pre-connected points

→ **`PCB.md`**, with the routing-order rules.

The point-to-point router builds a minimum spanning tree per net, `order = [0]`, then repeatedly
attaches the nearest unattached point. For a net whose FET pad is already wired by a hand-placed
escape, the code correctly substituted the escape's transition via for that pad — **one** of the
two ends of copper it had already laid. The escape via at the far end was not in the point set
at all.

So when a new part joined that net in the power section, the tree could root itself there and
then had to reach the control section directly, 80 mm across two power pours, when its own
escape via sat 8 mm away on the same net. Eight nets, all failing, with a plausible-looking
"unroutable" verdict.

What generalises:

- **Seed the tree with every point that existing copper already connects**, and mark them all as
  attached before the loop starts. `order = sorted(pre_connected) or [0]` is the whole fix.
- **The symptom is a long-distance failure on a net that has a short-distance option.** When a
  router reports an unroutable connection, compare the reported span against the net's own
  minimum spanning distance before believing the geometry is at fault — a large ratio means the
  tree, not the board, is wrong.
- **Report the anchors.** This was only diagnosable because the failure log printed the anchor
  list alongside each unplaced connection; with the endpoint alone it reads as a congestion
  problem.

---

# Third batch — finishing the routing of the same board (2026-08-17, later session)

Same board, same generator. These come from closing out the eight hand-placed gate-clamp
links, the residual control-section connections and the DRC/audit sweep.

## 22. A connection point that is not a pad CENTRE defeats a centre-keyed index — and the fail-closed fallback turns the omission into a total failure

**[SKILL.md Guards]** — this is §1 of the first batch happening a SECOND time, to a second
class of caller, in the same helper. That is what makes it worth a rule of its own.

`Router.pad_half(x, y)` answers "how big is the thing at this point" and is what keeps a via
out of the land it escapes from. It matched on an exact centre, and its fallback was correct:
*with nothing found, use the LARGEST half-extent on the board, never zero.*

But a router's endpoints are not always pad centres. This board's `route_all()` deliberately
moves the connection point of every isolation-bridging pad to the pad **EDGE**, because the
pad's own centre lies inside the 4 mm barrier band (first batch, §2). `pad_half` answered
`None` for every one of them, the fallback picked **5.5 mm** (an M4 lug), the escape radius
came out at 6.0 mm against a 6.0 mm reach, and the escape list was **empty for every
connection landing on U1, U2 or U3**. Six nets — `/MISO`, `/MOSI`, `/SCLK`, `/nEN_H`,
`/SCLK_H`, `+10VG` — failed in every pass and read as congestion.

The fix is not a bigger index, it is the right question:

```python
def pad_box(self, x, y):        # -> (ox, oy, hw, hh) or None
    # (a) the shape CENTRED here, and (b) the SMALLEST shape CONTAINING the point
```

…and the caller measures clearance from that box's **edge**, not as a radius from the point:
a radius from a point 1 mm inside a 2 mm pad is the wrong shape as well as the wrong size.
The same box's centre is then what gets passed as the `skip` argument — skipping "the pad we
are escaping from" by the *connection point* skips nothing when the connection point is on
the pad edge, so the pad blocks its own escape.

> **Rule.** For a helper keyed on identity, enumerate the ways a caller can *denote* the
> object, not just the way the index stores it. Centre, interior point, and via are three
> different denotations of "the thing here".

**Corollary, and it is a diagnostic:** *fixing this made the router slower, and that is
correct.* With no escapes the search returned instantly; with real escapes it runs the
quadratic escape × escape × path sweep it was always supposed to run. **A pass that got
FASTER after a fix in this area is evidence the fix did not take.**

## 23. A column of vias is a WALL unless its pitch admits a track

**[PCB.md]**

Sixteen gate nets change layer in a column of through vias between the inner-layer channels
and the packed resistor rows. The column pitch was derived, and the derivation was sound —
it just had one constraint in it instead of two:

* **fill between**: the guard plane must fill between neighbouring columns, so
  pitch ≥ `TRACK + 2*CLEAR + min_zone_thickness` = 0.95 mm. Set at 1.15 mm.
* **route through**: a track crossing the column between two barrels needs
  pitch ≥ `VIA_D/2 + CLEAR + w + CLEAR + VIA_D/2` = **1.35 mm**. Not considered.

At 1.15 mm the gap between barrels is 0.55 mm and nothing can cross, so eight vias were a
continuous **8.05 mm wall** lying across the only corridor between the transition rows and
the rows they feed. Measured symptom: every gate net whose resistor sat behind the wall was
unroutable, and the failures presented as 8–12 mm hops *straight down* — which is what a wall
looks like from the router's side, and nothing like a pitch problem. At 1.50 mm the gap is
0.90 mm against the 0.75 mm a track and its two clearances occupy.

> The forgettable constraint is the one with **no zone-fill symptom**. "Fill between" fails
> visibly, in the render and in `isolated_copper`; "route through" fails as congestion 40 mm
> away.

## 24. Row pitch is a routing BUDGET — count the crossings, count the lanes, and put the arithmetic next to the region table

**[new section — "Placement decides routability, and the packer does not know it"]**, extending
first-batch §6.

A packer's row pitch looks cosmetic. It is a track budget:

```
lanes_per_gap = (pitch - part_height) // (w + 2*CLEAR)
capacity      = lanes_per_gap * (n_rows + 1)     >=  crossings(region)
```

Measured on the diagnostic block: 25 passives at 3.5 mm pitch, parts 1.55 mm tall → 1.95 mm
gaps → **2 tracks per gap, 5 gaps, capacity 10**. The nets that must cross it horizontally are
3 SPI + 8 ADC channels + one enable = **12**. Over-subscribed *by construction*, and the
symptom was those same twelve reported unrouted — i.e. it looked like a router problem and
three rounds of router work could not have fixed it. At 4.5 mm pitch the gap is 2.95 mm.

Note the second-order effect that makes this worth checking rather than eyeballing: raising the
pitch **costs rows**, and the region then has to grow or refuse. Here the packer refused with
the ref and the y it would have needed (`R52 would sit at y 56.00..57.55`), which is the packer
working — and is exactly the feedback that makes the budget calculation cheap to iterate.

## 25. Two identical packages in mirrored bands are NOT mirror images — so state the congruence requirement on a quantity that CAN be equal

**[PCB.md — "Symmetry and matching are invisible to DRC"]**

Eight SOT-23 gate clamps, four in the P band and four in the N band, placed by a rule that is
itself mirror-symmetric: the eight **footprint origins** mirror about the pour axis to
0.0000 mm.

Their **pads** do not, and cannot. A SOT-23 has pad 1 at x −0.9375 and pad 3 at +0.9375 in its
own frame. Both legs sit at rotation 0, so both legs' pad 1 is 0.9375 mm *west* of its origin;
the mirror image needs it 0.9375 mm *east*. Rotation 180° mirrors x but also flips y, moving
the gate pad to the wrong side of the package. **A top-side asymmetric package cannot be
mirrored about a vertical axis at all** — only a flip to the other side would do it. So the
clamp pads' knockouts in the two pours are congruent but permanently displaced by 1.875 mm,
and the clamp row's own mirror axis (x 41.0625) is not the pour pair's (x 42.000).

Consequences, and they are the transferable part:

* the **shape** metric (mirror symmetric-difference area) cannot be driven to zero here, and
  its bound has to carry an explicit, derived term for the package's own non-mirrorability —
  which is a real weakening of the guard, and has to be recorded as one rather than absorbed;
* the **area** metric can and must stay exact. So the requirement to design to is *"each pour
  loses the same AREA"*, and the construction has to pair every part with its opposite number
  and give both the same treatment. Here that meant assigning the two hand-routed link lanes
  in mirror pairs, so BR_P and BR_N lose exactly 4 vias and 3.300 mm of stub copper each —
  a property the audit measures directly rather than inferring from the fill areas.

> **Rule.** Before writing a congruence tolerance, ask which of the two metrics the *packages*
> permit to be zero. Putting the requirement on the metric that physically cannot reach zero is
> how a tolerance gets widened later to fit the board — which is fixing the check, not the number.

## 26. "Longest first" fixes one shape of greedy failure and creates its mirror image — so rip up and re-run with the FAILURES first

**[PCB.md]**, extending first-batch §12.

First batch §12 established that a greedy router should take the LONGEST connections first: a
two-pad net crossing the whole section has one corridor, and giving it last pick loses exactly
the connections that had no alternative. That is right and it moved the count a lot.

The residue it leaves is the same defect pointing the other way: **2.54 mm hops between two
pins of ONE package** (three nets interleaved in the 4.7 mm channel beside a SOIC-16W) and
**9.2 mm hops between two parts in the same packed row**. Those have exactly one corridor too.
It is just a short one, and sorting by span puts them last. Proof that it is congestion and not
geometry: routing the same two endpoints on the *empty* board succeeds immediately.

Neither order is right and the answer is not a third heuristic. The router already had
`reset()` for rip-up; what it lacked was `snapshot()/restore()` so that a pass can be re-run
**without re-laying the hand-placed copper** (which is expensive and whose own checks would
then run several times over). With those the loop is a few lines: run the pass; if anything
failed, move the failures to the front keeping their relative order; re-run from the same
hand-copper state; keep the better result.

Three properties to build in, all ordinary guard hygiene applied to a search:

* **keep the BEST pass, not the last**, by explicit comparison, and `restore()` it before
  commit — so a later round that did worse is discarded rather than shipped;
* **carry each round's counters with its snapshot.** Reporting the last round's fanout count
  beside the best round's copper is a provenance claim the board does not support;
* **stop when the ORDER stops changing**, not after a fixed number of rounds. That is a fixed
  point; a round budget is a guess.

## 27. Hand-placed copper needs an audit that WALKS it, not one that only measures it

**[SKILL.md Guards]**

Eight gate-clamp links had to be laid by hand, because the router is barred from the inner
layers entirely and from the outer layers inside the power section. Three things are worth
measuring about such a link and only the first two are obvious:

1. **the length of the part that sits in the pour** — bounded by the same constant the
   equivalent auto-generated copper carries (here the device's own gate escape), because
   "hand-placed" is not a reason to be allowed more copper beside a 50 A pour than the router
   would have been;
2. **the length of the inner-layer run, against its OWN direct L** (|Δx| + |Δy| between its two
   endpoints) rather than against a round number. That measures *"this went straight there"*
   instead of *"this is short"*, and it fires on a detour of any length;
3. **that the copper actually CONNECTS** — a graph walk from the link via to the target via over
   the real segments. This is the one that is easy to skip and the only one that can tell a
   good link from a link that was slid 2 mm sideways: the length is still right, the window is
   still satisfied, and every measurement passes.

The corresponding calibration (3) has to break connectivity **without** breaking length or
window, or it fires on the wrong branch — see first-batch §20.

And the exemption bookkeeping: adding a second legal piece of copper to a net that a
confinement check already governs means the check now allows **two** windows. Derive both from
the same function the measurement check uses, so a link that grew cannot widen its own
exemption.

## 28. `blocked()` in a packer is not enough — an obstacle-aware packer still needs the region's own y budget checked against the pitch

**[minor, one line]** Raising a region's row pitch changes how many rows fit, and a region sized
for the old pitch overflows into its neighbour's band. The packer refusing with the ref and the
y it would have needed is what makes this a 30-second fix instead of a render inspection; a
packer that silently overflows would have put diagnostic passives inside the gate rows.

## 29. A region/keep-out policy that tests the CENTRELINE is not a copper rule — and a zone will fill around the violation without a word

**[PCB.md]**, the routing-policy counterpart of second-batch §15 ("an isolation barrier bounds the TRACK WIDTH, not only the position").

The scripted router's region hook answered *"may this polyline exist here"* by testing its vertices:

```python
for x, y in pts:
    if x >= POWER_KEEP_X:  continue      # legal
```

A polyline is a **centreline**. A 0.25 mm track whose every vertex is legally east of the boundary still has copper `w/2` west of them, and a pour keeps a further `CLEAR_POUR` away from that copper. Measured: two ADC tracks sat at x 80.15 and 80.70 against `POWER_KEEP_X = 80.0`, and the 50 A `BR_N` pour retreated to **79.625 over a 13.5 mm run**.

The cost is the whole point. That single oversight was **the entire 2.804 mm² F.Cu filled-area difference between the P and N pours** — on a board where P/N congruence *is* the FWD/REV thermal symmetry, and where 1 ppm of the working signal is 20 nV. Fixing the policy to
`keep = POWER_KEEP_X + w/2 + CLEAR_POUR` (and the via policy to `x - VIA_D/2 - CLEAR_POUR`) took it to **6.5 µm²** against a 50 µm² limit.

Three things generalise:

* **Pass the width into the policy.** It is not optional and it is not decoration; a policy that cannot see `w` cannot express a copper rule. The signature change is one line and it is the fix.
* **DRC will never tell you.** The zone simply filled around the track, which is what zones do. Nothing was closer than its clearance to anything. The only instrument that saw it was the pour-area comparison — which is an argument for having one.
* **Localise before theorising.** The area difference was attributed to three plausible-sounding causes in turn (the clamp pads, the clamp links, the TVS fanout) and all three were wrong. A **2 mm strip scan of the mirrored P fill against the N fill** put 2.8046 of the 2.8046 mm² in one 0.86 mm strip at the pour's east edge, and the culprit was then obvious. Ten lines of `clip_rect` + `poly_area` beats three rounds of reasoning.

## 30. Moving a part class into a dense area moves its SILKSCREEN too, and the reference designator is the part nobody re-checks

**[minor, verified]** Eight SOT-23s moved from the control section into the power section between two FET rows. Their footprints put the reference designator 2.4 mm ABOVE the body by default, which landed it at y 27.9 — straight through the FETs' own silk outline at y 27.7. **Eight `silk_overlap` violations**, every one of them "Reference field of D7/D10/D12/D13 vs Segment of Q1/Q4/Q5".

Two notes worth keeping:

* the generator's own `check_silk_clear()` did not see it, because that guard covers the **connector labels the generator places** and not the **reference fields the footprints bring with them**. An exemption by omission (see first-batch §3) — the replacement check is "flip the reference to the side of the body that is empty", derived from the part's own courtyard;
* the derivation of *which* parts to flip must be a rule ("everything in the power section that is not a leg device, a lug or a mounting hole"), not a refdes list, or it goes stale the next time a clamp is renumbered — and the symptom is a silkscreen collision nobody looks for.

## 31. A slow generator needs a stage that lays ONLY the copper under test

**[SKILL.md Guards / PCB.md]** — cost discipline, and it is a correctness point too.

A full pass on this board is ~25 minutes of single-threaded search over 164 connections plus eighteen zone fills. Six of them were spent testing changes that affected **eight hand-placed links** and could not have been affected by the router at all. That is the wrong iteration loop, and it also makes each experiment expensive enough that you start reasoning instead of measuring (see §29).

The stage to add lays every piece of hand copper, runs its checks and stops *before* the point-to-point router. Two properties keep it from becoming a lie:

* it must **report the entire connection list as unrouted**, because that is what it is. A cheap stage that prints "0 unrouted" is the anti-monotone false pass in its purest form;
* it must **refuse to print an md5** and print a banner instead. A reproducibility claim belongs to the full pipeline, and a cheap stage that prints a hash invites the number being quoted for the real board.

## 32. Two parts on the same net class can need OPPOSITE answers to "move the part or run a conductor"

**[new section — the floor-plan/netlist collision, continued from §21]**

§21 established the check: for each section rule, assert every net of every part assigned to that section is *reachable* from there. On this board it fired twice, on two part classes that both tap the 50 A bridge nodes and both sat in the control section:

* **eight gate-source clamps.** Moved into the power section. A clamp is a *protection path* carrying the gate charge when the device turns off; putting it 45 mm away behind two thin conductors makes the protection worse, and the conductors would touch a Kelvin-tapped node.
* **three diagnostic divider taps.** *Not* moved. ~100 kΩ series sense taps carrying microamps into a filtered ADC input, so a thin conductor costs nothing measurable — and moving them would put signal parts inside a 50 A pour for no gain.

The transferable point is that "electrically the conductor is cheap" is not a *property of the net*, it is a property of **what the part does with the net**. The same sentence — *"it only carries microamps, so a thin trace is fine"* — was the losing argument in the first case and the winning one in the second. Write the decision down per part class with the reason, because the next reader will otherwise generalise whichever one they meet first.

A corollary about the guard plane: once you have one inner plane that is not a current path, it becomes the answer to *every* "this has to cross a pour" problem, and that is a slot budget nobody is tracking. Here it took the eight clamp links **and** a 42.6 mm `BR_P` sense tap. Scope the exemption in the guard's own check (an explicit allow-list of nets), and measure the length of what uses it, or the plane quietly becomes a routing layer.

## 33. Fixing the placement fixed the measurement — and dissolved a second "pre-existing defect" that was never real

**[SKILL.md Guards]**

Two failures were attributed, in writing, to two different pre-existing causes:

* `check_pn_congruence`: F.Cu fills differing by **2.8046 mm²**;
* `check_equipotential_entry` on the second terminal: **1.0248 mm²**, still present with the clamps removed, and reported as "a separate pre-existing asymmetry, not diagnosed".

Both were the **same** defect — the region policy testing a track's centreline (§29) — and neither was separate, pre-existing, or a second cause. After the one-line width fix they read **6.5 µm²** and **1.4 µm²**.

The reasoning error is worth naming: the second number was measured on a board that still contained the first defect, and "still fails with X removed" was read as "X is not the cause" when it only means "X is not the *only* cause". With two overlapping defects, an ablation tells you about the one you removed and nothing about the one you left.

> **Rule.** Do not classify a residual as a separate defect while a known defect is still in the board. Fix what you have diagnosed, re-measure, and only then attribute what is left. And prefer *localisation* (a strip scan, a bisection over the geometry) to *ablation* (remove a suspect and see) — localisation names the place, ablation only ever exonerates.

## 34. Count the failures by CAUSE before designing the fix — "30 identical violations" was 8 + 22

**[SKILL.md Guards]**

A DRC report said **30 `isolated_copper`**. Both I and the reviewer described them as "30 × 0.19 mm² slivers pinched off by the gate-escape knockouts", and a fix was designed for that: a zone keepout over each of the sixteen escape windows.

Enumerating them with their **areas and bounding boxes** — ten lines of `SHAPE_POLY_SET` walking, which the audit already had — gave a completely different population:

| n | zone | area each | cause |
|---|---|---|---|
| 8 | `BR_P`/`BR_N` on F.Cu | 0.186 mm² | the gate-escape slivers |
| 13 | `GND_OUT` on In2 | 0.14…72.88 mm² | plane pockets with **no legal via position** |
| 7 | `GND_OUT` on B.Cu | 0.53…83.93 mm² | routing carved the pour, stitching grid missed the piece |
| 2 | `GND_H` on B.Cu | 0.55, 16.67 mm² | same, in the host strip |

The keepout fixed **8 of 30**, exactly and only the ones it was aimed at. The largest violation in the report — an **83.93 mm² floating plate** under the control section — has nothing to do with gate escapes and is fixed by a via, not a keepout. And 13 of them cannot be fixed by a via at all: a through via in a `GND_OUT` pocket surrounded by the `BR_IN` pour would short a 50 A node on three layers.

> **Rule.** A violation *count* is not a population. Before designing a fix, enumerate the violations with a quantity that distinguishes causes — area, layer, net, bounding box — and check that the distribution is unimodal. Here the areas spanned **500×**, which is by itself proof that "30 identical slivers" was wrong.

Two corollaries:

* **A uniform fix for a non-uniform population reports success on the wrong denominator.** "Orphans 30 → 22" reads like a partial fix of one problem; it is a complete fix of one problem and no progress on three others.
* When the fix *is* right, hold it to the same standard as the thing it fixes. This keepout is derived from the escape geometry, so all sixteen are identical and each pour loses the same area — but the first version's y bound had `copysign` inverted (keepout 0.3 mm *inside* the pour, sliver untouched, 8 orphans still there) and the second reached 0.10 mm into the equipotential entry band, costing **0.0600 mm² against a 0.05 limit**. Both were caught by re-measuring, not by reading the code. Geometry written to remove copper needs the same measurement discipline as the copper it removes.

## 35. An island-anchoring pass must test the position against EVERY layer's fill, not against its own island

**[PCB.md — "A zone SETTING can destroy copper asymmetrically"]**, the constructive half of §10 and §17.

`PCB.md` says to turn island removal off and resolve `isolated_copper` with mirrored stitching vias. §17 added that a uniform denser grid manufactures more pinched islands than it cures. The pass that actually works is neither: **fill, find the orphans, and anchor each one individually** — because an island is a property of the fill, and the fill does not exist until the filler has run.

The one thing that makes it correct rather than dangerous:

> A through via lands on **all four layers**. A position that is comfortably inside a `GND_OUT` island on In2 can be inside the `BR_IN` pour on F.Cu, In1 and B.Cu — and placing a via there does not fix an island, it **shorts a 50 A node**. The legality test must therefore collide the candidate against every *foreign net's zone fill on every layer*, not merely against pads and tracks.

Measured on this board: 22 islands / 304.14 mm² → 19 / 186.09 mm², three vias placed. Only three were legal, and the pass's real value was the other nineteen — it printed zone, layer, area and bounding box for each, which is what turned "21 `isolated_copper` violations" into a design statement (13 guard-plane pockets that cannot be anchored, 5 small B.Cu pieces, 1 in the host strip).

Guard hygiene the pass needs, all of which caught something or would have:

* **fail closed** — an island with no legal position is REPORTED with its geometry, never skipped. An empty diff and "all fixed" are the same output otherwise;
* **monotone** — assert the total orphan AREA strictly decreases and the COUNT does not rise. A stitch pass that fragments a pour further is the failure mode, and it looks like success if you only count vias placed;
* **no new islands** — every survivor must be one that existed before, matched by zone, layer and position. Otherwise the pass can trade a big island for two small ones and report progress;
* **re-verify the invariants it could plausibly break, rather than predicting them.** Here: P/N pour congruence and equipotential entry. The prediction ("these are GND nets, congruence is untouched") was correct — 6.5 µm² and 0.0/1.4 µm², unchanged — but on this board predictions about pour area had a poor record (§29, §33), and the check costs seconds.

Worth stating plainly because it is the reason to bother: the largest island was an **83.93 mm² floating plate on B.Cu directly beneath the ADC's eight high-impedance inputs**. Floating copper spanning several nets' return paths is a coupling path *between* them; the DRC violation count was the least important thing about it.

## 36. When the clearance test is the profile, count how often it is asked the SAME question

> **PROMOTED (speed-up content only), 2026-08-18** — folded into `PCB.md` → *A slow generator: profile by OUTCOME…*. The measurement method and the hit-rate discipline went across; the router-specific candidate-family analysis and the 12 % threshold did not, and the board this was measured on is moving to FreeRouting, so they are evidence rather than rules.

**[SKILL.md Guards / PCB.md — router performance, continued from §31]**

A candidate-polyline router spends its life in one function: is this segment clear of everything near it. On this board a stack sampler put `SpatialIndex.near` + `_cells` at **49.9 %** of the run and the geometry tests at another 10.6 % — so the obvious reading is "make the index faster". That reading is wrong, and it is wrong in a way worth recognising early: **half the time went into *finding* obstacles, which is a call-frequency symptom, not an index-quality one.** An index rewrite, benchmarked against 40 000 recorded real queries, returned identical results at **1.12 × overall**. It was correctly rejected — a structure whose failure mode is silently dropping an obstacle should not be touched for 12 %.

The frequency is structural, and you can read it off the candidate families without measuring anything:

* the two-jog staircase enumerates `13 x1 × 5 y1 × 13 x2` = 845 candidates, but its **first segment depends only on `x1`** — 13 distinct — and its last only on `x2`. Only the middle horizontal is genuinely 845;
* the escape step tries `N_ESCAPE²` = 64 escape pairs × ~180 paths × 2 layers, and **every family's first segment out of escape point `ea` is independent of `eb`**, so each escape's own stub is re-tested 8 times over.

Measured before writing any cache: **26 638 958 segment tests per round, 22 887 753 of them repeats — 85.9 %.** Memoise the per-segment verdict on `(a, b, layer, netname, width)`, cleared on every obstacle mutation, and `--full --rounds 1` went **6:48 → 2:21 (2.88 ×) on a byte-identical board** (`c861bf13…` from both runs). The memo kills the `near()` query *along with* the geometry, so it collects the 49.9 % and the 10.6 % in one change.

Two pieces of method, both of which did work here:

* **instrument the hit rate before implementing the cache.** A counting set costs one run and turns "this might help" into a number that sizes the change. The estimate from reading the families alone was 20–35 %; the measurement was 85.9 %, which is the difference between "probably not worth it" and "do it first";
* **md5 the artefact, not the violation count — but state precisely what the hash proves.** A matching unrouted count is weak evidence: two runs can fail differently and tie. Byte identity is a far stronger *regression* oracle — it covers every serialized field rather than a summary statistic — and it costs one command, so take it whenever deterministic output is an intended invariant. The formulation to write down: **strong evidence that the artefact of the tested run is unchanged; never proof that the mechanism producing it is correct, and never proof it generalises to another input.** Measured, not supposed: a later cache in the same router was A/B'd with a deliberately *unsound* key and produced the identical board (§43). Note the honest reason, because the first draft of this bullet guessed wrong — it was not that a wrong verdict left no trace in the output, it was that **the unsound branch was never exercised in a verdict-changing way at all**. Both are real ways a hash stays green over a broken mechanism, along with a wrong FAIL suppressing a candidate that was not needed, two paths converging on identical copper, and a defect that appears only on another board or another ordering. Independent codex review reached the same limit from the other side — *"the hash proves final-artifact identity for that run, not memo correctness"*. Keep this distinct from §31, which rejects hashing a deliberately *incomplete* artefact, and from `SKILL.md`'s use of md5 to prove *reproducibility*: three different claims that must not be collapsed.

## 37. Verify a cache on the HIT — and a calibration that does not fire has told you something, so say which thing

> **NOT PROMOTED — still staged.** Only the speed-up material was folded in. This entry is guard/calibration epistemology, and codex's review of it recommends splitting it and merging the unfired-calibration half into the existing calibration checklist rather than adding it whole. Its factual corrections have been applied; the restructure has not.

**[SKILL.md Guards — the guard-review checklist, items 1, 5 and 7]**

A memo whose entries outlive the state they were computed against is the anti-monotone false PASS with a new hat on: it serves "clear" for a segment that a track now crosses, silently, and the router lays copper through it. So the cache in §36 shipped with a `--memo-verify` mode. The detail that makes it a guard rather than decoration:

**verify on the cache HIT, not on the miss.** The first version checked at the point of insertion, which recomputes the value it just computed and can only ever agree — a guard that cannot fail. The only thing a cache can get wrong is *serving* a verdict the current state no longer supports, so the check belongs on the branch that serves one. Calibrated by storing the negation of each verdict under verify mode; it raised on the first hit, naming segment, layer and net.

Then the part worth the entry, **including the way I first got it wrong**. The *first* calibration attempt was to delete the invalidation from the mutating path outright and watch the verifier catch it. It did not raise: 22 904 170 hits, every one still correct, identical board md5. I wrote that up as *"the invalidation is inert on this board — no cached key is ever queried across an obstacle mutation"*. **That was false, and the disproof was already on my screen.** The run *with* invalidation reported 22 887 753 hits. The difference is **16 417 hits — 0.072 %** — and those exist only because entries survived a mutation. A later codex review found it by subtracting two numbers I had quoted side by side for hours.

What actually survives is the weaker, more useful claim: **no verdict-changing cross-mutation reuse occurred.** The mechanism was live the whole time; keys did cross mutations; none of them changed an answer on this board.

The lesson is therefore stronger than the one I nearly recorded. It is not "the mechanism is inert" but: **a calibration that compares totals by eye cannot see a 0.07 % behavioural difference. Subtract the counters; do not read them.** Two seven-digit numbers differing in the fifth digit look identical in a terminal and are not.

There are two honest responses and they are not the same:

* **keep the code, drop the claim.** The invalidation stays. An independent review later supplied the structural argument the calibration could not: removing the clear is *not* safe, and the failure is three lines long — cache segment S clear for net A, add a foreign-net segment crossing S, re-check S for net A and get a stale True. So the mechanism is load-bearing by construction even though no input on this board exercised it into a wrong answer;
* **do not let the non-firing calibration read as a pass.** "I tried to break it and couldn't" is the sentence that turns an untested mechanism into a trusted one. What was actually established is narrower: *on this input, no reuse across a mutation changed an answer.* Absence of evidence, again — this time about the guard rather than about the board. Note this is NOT the same as "never exercised": keys did cross mutations (the 16 417 above), they simply never crossed one that mattered.

**Twice in a row, which is the part that generalises.** The same shape recurred on the same router. A second cache — one that records *failed* route attempts — is sound only while copper is added and never removed, with one exception: an endpoint that gains a via of its own net becomes easier to route, so a recorded failure can stop being true. That dependency went into the cache key. Deleting it again — the deliberately unsound key — produced an **identical board**, on an input where the condition holds in **62 of 549 calls**.

And that 62 does *not* establish the dependency was exercised, which is the same distinction this entry just finished repairing. Soundness needs the reduced key **cached before an endpoint's via state changes and reused after**; 62 calls merely had the flag set at call time. All of them could have had stable flags, or unique remaining key fields, or entries inserted only after the transition. The honest statement is weaker: *reasoned-correct, and never observed to matter.*

So: two mechanisms — invalidation on the segment cache, `has_via` in the failure key — and a mutant for each that would not fire. (Two, not three: the two endpoint flags are one dependency, and "the calibration attempt on each" describes the tests, not further mechanisms. Counting the tests as mechanisms is how a small sample gets talked up into a trend.)

The tempting conclusion is that a narrow state space is the common cause — one deterministic board from fixed inputs, so guards against state *transitions* have nothing to fire on. That is a plausible **description** and not an established cause, and this same run contains the evidence against treating it as one: 16 417 reuses did cross a mutation. Competing explanations survive equally well — the mutant never forced the harmful transition, the wrong verdict never reached a selected route, other key fields prevented the reuse, or the oracle (a whole-file hash) was too coarse to see it.

Two non-firing mutants in one router support none of that. What survives is only the discipline: **stop counting an unfired calibration as evidence, and source the argument elsewhere** — from review, from a proof, or from a test that *forces* the transition. Both mechanisms here survive on structural argument, not on any test that passed, and the right fix is not a second board (which may miss the same transition) but the minimal forced transition: cache a verdict, mutate the state it depends on, demand the stale answer.

A cross-check fell out for free, and it needs stating carefully because I first overstated it too. The verify run reported **22 887 753 hits**, matching the instrumented baseline's repeat count exactly. I wrote that the memo therefore "serves precisely the set of duplicate questions and nothing else". **Equal cardinality is not set identity** — two different sets of that size would produce the same line. What the match licenses is that the memo serves *as many* questions as were measured to repeat, which is good evidence and not the set-equality claim. Proving the stronger version needs a trace of the hit keys, which I did not take.

---

## 38. A standard practice's PRECONDITION must be checked against YOUR topology — and an inconsistency inside your own document is the tell

→ **`SKILL.md`**, new bullet near *Never quote a spec from memory*. Merge with §33/§34: the
shared shape is a correct fact attached to a situation it does not govern.

"Give every paralleled MOSFET its own gate resistor" is genuine, textbook, and correct — for
the reason it exists. Paralleled devices oscillate because each is a high-gain amplifier while
it traverses the linear region, so the array needs damping. That reason has a **precondition**:
the device must reach *saturation*, `V_DS > V_GS − V_th`, which on this part is 6–7 V.

In this topology it never does. The bridge is make-before-break, so the diagonal being switched
always has the *other* diagonal holding its drain and source together. Whole-bridge drop at
50 A is `50 × 1.35 mΩ` = **67.5 mV**; a device sees ~**34 mV**, on or off. `g_fs` is 48.5–97 S
and is never engaged, because the device goes from off straight into triode. There is no loop
gain anywhere in the array at any time. **32 resistors, and 6 of the 8 gate clamps, were bought
on a rule of thumb whose precondition did not hold** — 38 parts, 29 % of the BOM.

What generalises, and the second half matters more than the first:

- **A design rule is a conclusion with a premise attached. Write the premise down next to the
  part, in the units of your own circuit.** "Per-device gate R, damps the parallel array" is
  the conclusion and it survived review three times. "…requires `V_DS` > 6 V; this topology
  gives 34 mV" would not have survived once.
- **The tell was an inconsistency inside my own document, and I had written both halves.** §4
  already argued *"no switching loss to speak of — the transition happens during the overlap,
  when every device is on and `V_DS` is millivolts"* — and then two paragraphs later justified
  sixteen gate resistors. The premise was already on the page, applied to one conclusion and
  not carried to the neighbouring one. **Grep your own design doc for the premise before
  defending the part**: a rationale that contradicts a rationale three paragraphs up is a
  design smell, and it is cheap to search for and free to find.
- **The catch came from a reader who knew the circuit and not the convention.** "The MOSFETs
  switch at almost zero voltage, just parallel them" is not an expert objection; it is someone
  reading the topology instead of the habit. Neither a guard, a review nor a DRC can produce
  that, because all three check the design against rules — and the rule was the problem.

---

## 39. Before reducing a parallel network, compute whether the reduction is EQUIVALENT or merely ADEQUATE

→ **`SKILL.md`**, with §38. This is what turns a risky simplification into a safe one.

Four 100 Ω gate resistors in parallel present **25 Ω** to the driver. So one 25 Ω per leg is
not a compromise that has to be re-argued against every downstream number — it is the *same
network*. Confirmed by diffing the generator's own printed derived quantities before and after:

```
driver peak 0.35 A of 1.2 A            <- identical
overlap 14.6..38.8 us vs 3.3 us rise   <- identical
gate leakage 0.031 ppm                 <- identical
```

- **Do the arithmetic before the argument.** "Adequate" obliges you to re-verify every
  dependent figure — here the make-before-break margin, the driver peak against a 1.2 A limit,
  and the enhancement time. "Equivalent" obliges you to verify nothing, because nothing moved.
  These are very different amounts of work and the difference is one division.
- **A generator that PRINTS its derived quantities makes equivalence checkable by diff.** This
  is the payoff of §11 that was not obvious when it was written: the value of encoding premises
  as executable code is not only that guards can fire on them, it is that a refactor's blast
  radius becomes a text diff.
- **Reducing part count can REMOVE a blind spot.** Counterintuitive, so record it: per-device,
  an open gate resistor killed one device of four — a 33 % leg-resistance change worth
  millivolts, invisible to the diagnostics. Per-leg, it kills the whole leg, which the node
  channels see unmistakably. Fewer parts, strictly better observability.

---

## 40. Sweep the parameter through the ledger instead of arguing about it

→ **`SKILL.md`**, extending §11 (*encode the quantitative premise as a guard*).

Asked whether 16 devices were really needed, the honest answer took one command, because the
dissipation ledger was already executable:

```
 N  W/dev    Tj      verdict
 2   3.38     --     REFUSED: 113 % of the 3.0 W nameplate, before derating
 3   1.50   100.0    exactly the thermal guard's refusal threshold
 4   0.84    67.2    28 % of nameplate
```

`N = 4` is the first value with margin, and `N = 3` landing on **exactly 100.0 °C** is not luck
— that guard exists precisely because the ×2 `R_DS(on)` bound is only honest where ×2 is
obviously generous, and at 100 °C it is not.

- **The point of encoding a premise as code is that you can then interrogate it.** §11 sells
  guards as things that *fire*; this is the other half — a ledger you can sweep answers "is
  this over-designed?" in seconds, with numbers, instead of with a paragraph of judgement.
- **Sweep past the chosen value in both directions.** `N = 5` (52 °C, 18 %) is what shows the
  choice was not simply the smallest thing that passed.
- **A guard that refuses is a better answer than a number.** `N = 2` did not return a bad
  figure, it raised — so the sweep reports the design's own reason for rejecting it, in the
  design's own words, rather than my paraphrase of it.

---

## 41. A retraction that lands only in the document is HALF a retraction — grep the runtime output too

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Guards*, near the end of the list.

→ **`SKILL.md`**, near the provenance rules; pairs with the guard checklist's *"provenance must
not claim more than was actually done"*.

An independent review showed a claimed "bottom-leg match resolved to **0.027 mΩ** at 50 A" was
code granularity, not accuracy: the ADC's ±1 µA input leakage into the divider's 90.9 kΩ is
±2 mΩ-equivalent, which swamps it. I withdrew the claim from the design document, rewrote the
section, and added an open item.

The generator carried on printing `bottom-leg match resolved to 0.027 mOhm at 50 A` to the
console on every run.

- **The operator reads the console, not §8.** A number that survives in runtime output is still
  in circulation no matter what the document now says — and it arrives with more authority,
  because it looks like a measurement the tool just made.
- **When you retract a claim, grep the whole repo for the NUMBER**, not just the sentence. The
  string `0.027` was the only reliable handle; the surrounding prose differed everywhere.
- **Fix it by re-labelling, not deleting.** The figure is still the true LSB size and is worth
  printing — it now reads `bottom-leg LSB 0.027 mOhm (granularity, NOT accuracy — ADC leakage
  is +/-2 mOhm here)`. Deleting it would have lost a real number; leaving it bare asserted
  something false.

---

## 42. Push every spatial guard to the EARLIEST stage that can evaluate it — and re-check a relocated part against ALL of them

→ **`PCB.md`**, with the placement checks. Merge with §13 and §20.

Two lessons, both from moving eight clamps twice.

**A relocation satisfies one constraint and can silently break another.** The clamps started in
the control section, where their `BR_P`/`BR_N` terminal did not exist (§20 — unroutable, and no
placement guard could see it). Moving them into the power section fixed that and put them inside
the **equipotential current-entry band**, where they perforated a 50 A pour on one side of the
terminal only — a state-synchronous entry asymmetry, measured at **12.794 mm² against a
0.05 limit**, and again invisible to DRC and to every placement guard. Each move was correct
against the constraint that motivated it. **When a part moves, re-run every spatial requirement,
not the one you were thinking about.**

**And the guard that caught it was in the wrong place.** It lived in the post-route audit, so it
cost a 25-minute routing run to learn. Nothing in it needs routing: the entry band is derived
from the lug land and the leg pad rows, all of which exist in the footprints. Moved into the
placement generator's own `sanity()` it returns the legal window — `[31.450, 39.550]` — in
**two seconds**, with a calibration proving the shipped value fires.

- **Ask of each check: what is the earliest artefact that could answer this?** Footprint library
  → placement → routed board. A check sitting a stage later than it needs to be is a check you
  will be tempted to skip.
- **Have it return the legal WINDOW, not just a verdict.** "30.3 is wrong" sends the next person
  guessing; "must lie in [31.450, 39.550]" ends it. Deriving the window also forced the
  keep-out, the pad extents and the lug land to be read from the real footprints rather than
  typed — so resizing any of them moves the window with it (§13, one repo-file over).
- **Corollary found the same day:** a netlist change stales refdes ranges typed in *other*
  files. Collapsing 32 gate resistors to 8 broke a placement rule that indexed `R1..R32` by
  device. Same staleness bug as §13's membership table, one file away and outside the reviewed
  diff.

---

## 43. Profile by OUTCOME, not only by function — and when a cache is unsound, put the dependency in the KEY rather than invalidating

> **PARTLY PROMOTED, 2026-08-18** — the outcome-profiling, temporal-locality and key-vs-invalidation trade-off went into `PCB.md`, along with the deliberate-absence lesson. The monotonicity proof and its preconditions did not: they are specific to a router being retired.

**[SKILL.md Guards / PCB.md — router performance, continued from §31 and §36]**

§36 memoised the clearance test and the profile went flat: no item above 27 %, which normally
reads as "stop optimising, change the interpreter". That reading was premature, because the
profile was sliced the wrong way. Slicing by *outcome* instead of by function:

```
SPLIT routed  n=138    4.9s   3.6%   mean=0.04s
SPLIT FAILED  n=411  128.3s  96.4%   mean=0.31s
```

**96.4 % of routing time is spent on searches that fail.** Not on proving connections
impossible — that distinction is the whole of the first correction this entry needed.
`_paths()` is *pattern routing*: it enumerates a bounded family of candidate shapes, so an
exhausted family is silent about whether a path exists. A success stops at the first clear
candidate; a failure usually exhausts the families — 64 escape pairs x ~180 paths x 2 layers,
plus the 845-candidate staircases — though not always, since the call also returns early when
there is no non-F.Cu layer or no escape at either endpoint. And 411 failed calls served ~31
connections *this router did not route*, so the same fruitless search was repeated: 116 exact
repeats, **41.8 % of the 133.2 s spent inside `route()`**.

Whether those 31 are unroutable at all is a **separate question needing a separate oracle**, and
"the search gave up" must never be written down as "no path exists". On this board a monotone-path
DP was run afterwards and returned 0/31 feasible on F.Cu — with the calibration that matters,
16/25 feasible on connections the router *did* route, so the oracle demonstrably finds paths that
exist in the same congested area. That is a real result and still a narrower claim than
"unroutable": it covers monotone paths on one layer. One `perf_counter`
around the top-level call, bucketed by return value, found what a function-level sampler
structurally cannot — it attributes cost to `near()` and `_clear` no matter which outcome pays.

The memo that follows is sound by monotonicity: within a round copper is only ever *added*, and
adding an obstacle can turn PASS into FAIL but never FAIL into PASS. **Find the exception before
trusting that sentence.** Here there was exactly one: `route()` short-circuits the escape search
when an endpoint already carries a via of its own net — that point is already on every layer, so
no stub and no second barrel are needed. A via appearing *there* makes the connection **easier**,
which is the one way a recorded failure stops being true.

The first fix was the obvious one, and it was **sound and useless**:

```
FAILMEMO hits=14 clears=1142 peak=116     <- clear the memo on every via()
FAILMEMO hits=116 clears=0   peak=295     <- put has_via(a), has_via(b) in the key
```

Clearing is correct and destroys the cache 1142 times for 14 hits: **no speedup at all**.
Carrying the dependency in the key is *more* precise, needs no invalidation, and caught all 116
repeats for 1.53x on a byte-identical board.

The generalisable lesson is **not** "widen the key rather than invalidate" — that was the first
draft of this entry and it is wrong as an imperative. Widening the key wins only when the
dependency is **low-cardinality, cheap to hash, and stable**, and when retaining the superseded
generations is acceptable. Invert any of those and clearing is better: key on a monotonically
increasing design revision and every past generation is retained but unreachable, so memory grows
with every edit where a clear would have bounded it. The same goes for timestamps, large config
objects, and any change that invalidates most entries anyway — there, clearing is both cheaper
and simpler.

The actual rule is a **test, not a preference**: name the varying input, then ask its cardinality,
its churn rate against the reuse distance, and whether stale generations may be retained. Here the
input was one boolean per endpoint, changing rarely, with everything else reused — which is why
keying won by 1.53x. And the deeper reason the first design failed is worth stating on its own,
because it is what the 1142/14 split actually measures: **cache utility depends on temporal
locality relative to the invalidation boundary, not on the total count of duplicate questions.**
A sound cache is useless when mutations arrive faster than reuse. Measure hits *surviving the
proposed lifetime*, not global duplicate keys — counting duplicates globally overestimates
cacheability, and it overestimated it here.

Two guard results, and the second is the reason this entry exists:

* **the mechanism is live** — 62 of 549 `route()` calls take the `has_via` branch, 11 %. This
  is not a theoretical hazard;
* **and the guard is still unfired.** Removing the `has_via` flags from the key — the knowingly
  unsound version — produced the **identical board**, same hit count. The stale transition (a
  failure recorded with no endpoint via, re-queried after one appears) never occurs on this
  input. Second unfired guard in one session, after §37's invalidation. Keep the code, drop the
  claim, and say which.

That second result is what corrects §36's md5 bullet above, so it must be written down where the
advice lives and not only where the finding was made: **byte-identical output did not detect an
unsound cache key.** A verification tool that has been seen to miss the class of defect you are
using it against has to be labelled with that miss, or the next reader will spend it as proof.


---

## 44. A pattern router's ceiling is its FREE-COORDINATE COUNT — when the residue is structural, change the algorithm, do not enlarge the table

**[PCB.md]**

A hand-written router usually starts as a family of candidate polylines: L-shapes, Z-shapes with
an offset, staircases. Each family has some number of *free coordinates* — the offset, the jog
position — and that number is the hard limit on how many walls of parts it can thread through
slots it chooses. Two free coordinates cross two walls. A dense control section is four to six
walls deep, and the connections that survive every family are exactly the ones that have to cross
all of them: on this board, the driver outputs going from y 20 to the gate rows at y 56..73 past
the interlock rows, the diagnostic rows and two ICs.

The tell that you are at the ceiling rather than at a tuning problem: **the residue stops
shrinking and stops changing shape.** Enlarging the family is quartic in the wrong place — the
effort-3 two-jog staircase here already spends `13 x 13 x 5 = 845` candidates to buy *one* more
free coordinate, and a fourth would be `~11 000`. Measured on this board: pattern families alone
left 9 unrouted across two rip-up rounds, stable across runs.

The fix is a different algorithm, not a bigger table — a bounded A\* over a uniform grid, which
has as many free coordinates as it has cells. **Run it only on what the pattern families have
already refused**, and the cost stops mattering: 12 connections at 0.6–9.7 s each is affordable
where the same search on all 133 would not be. The board went from 9 unrouted to **0**, DRC from
7 violations + 9 unconnected to 1 + 0.

Keep the pattern families. They are cheap, they run on everything, and they produce geometry a
human recognises; a board routed by grid search everywhere is a board whose geometry nobody chose.
The grid is the last resort, and saying so in the code is what stops a later edit from promoting
it.

## 45. A grid used as a search structure must be a SOUND proxy for the continuous test — and the exact test stays the authority

**[PCB.md]** — the companion to §44, and the part that decides whether the search is trustworthy.

Two rules, and each one was load-bearing.

**The grid never decides.** Every polyline the search produces goes back through the *same* exact
clearance test every other path on the board passes. A grid that is subtly too permissive then
costs a refusal, not a bad board — the failure mode is bounded on the safe side by construction.
This is worth the double work: rasterised occupancy and closed-form segment geometry disagree at
the margins, and you do not want to find out which was right from a fab.

**And the grid must still be sound, or the exact test rejects paths the search already committed
to.** A cell is tested as a *point* carrying the track's half-width plus its clearance — and plus
**half a grid pitch**. That last term is the whole argument: moves are axis-aligned and exactly
one cell long, so every point of the segment joining two free cell centres lies within `G/2` of
one of them. Drop it and the grid is a *sampling* of the segment rather than a proxy for it, with
exactly the same defect as a clearance test that walks a segment every 0.1 mm and cannot see a
violation between two samples.

Note the asymmetry: the inflation makes the grid slightly *conservative*, which costs a little
reach in tight slots and buys the guarantee. At `G = 0.10` mm the inflation is 0.05 mm and a
1.075 mm pad-to-pad slot still admits a 0.25 mm track with clearance, so it cost nothing
measurable here.

## 46. A router's "routed" is its own RETURN VALUE — check that the copper it emitted actually joins the two points

**[PCB.md]**, and it generalises past routers to anything that reports its own success.

Nothing between a greedy router and DRC asks whether a connection's copper is connected. The
router returns `True`, the caller records the connection as routed, the unrouted list is empty,
and the generator reports a finished board. DRC will find it — but DRC is the last rung, and on a
25-minute pipeline that is a slow way to learn it.

The concrete defect: emitting a multi-layer path as per-layer runs plus vias between them, the
first version replaced the first cell's coordinate with the exact endpoint. When a path changes
layer on its *first* cell that run is one cell long, collapses to a single point, gets dropped as
degenerate — and leaves the via with **no copper joining it to the pad**. Reproduced exactly:

```
polys = [(B.Cu, [(1.0,1.0),(1.25,1.0)])]   vias = [(1.0,1.0)]
```

a barrel on the bottom layer, nothing on the top, and a cheerful `True`.

Two fixes and only the second is general. *Prepend* the endpoint instead of overwriting it — that
repairs the case I had thought of. Then **walk the chain**: run 0 starts at `a`, run *n* ends at
`b`, consecutive runs meet at the via between them, and no "via" joins two runs on the same layer.
That one does not depend on my having enumerated the causes, and it is the one to write.

## 47. A search's refusal must say WHICH refusal — "no path" and "path rejected" point in opposite directions

**[SKILL.md Guards]**

A bounded search has at least three distinct ways to fail and they demand opposite responses:

* **the open list emptied** — the goal is genuinely unreachable in this obstacle set. Change the
  board: rip up a neighbour, move a part, re-order;
* **the budget was exhausted** — the search gave up. Raise the budget, improve the heuristic, or
  accept it; the board may be fine;
* **a path was found and the exact test rejected it** — the *grid* is wrong, not the board. This
  one points at your own code and is the one a single boolean hides completely.

Collapsing these into `False` costs real diagnosis time. Recording them cost four lines and
immediately overturned a wrong conclusion of mine: several connections were refusing in 0.1 s, and
I had diagnosed pad escape — the search starting at a pad centre without the escape logic the
pattern router has for stepping out of a package. I measured it: all four neighbouring cells were
free, identical to a known-good control. **The hypothesis was dead and the instrumentation gave
the real answer** — `open list emptied after 432 pops`, a sealed pocket of ~4 mm² in a narrow
strip. Same symptom, different cause, opposite fix.

Note also what the measurement itself got wrong, because it is the more transferable lesson: I
measured the four immediate neighbours and the nearest free cell. Both were fine. The quantity
that mattered was **the size of the reachable region**, which I had not thought to measure. When a
probe comes back clean, ask whether it measured the quantity the hypothesis was actually about.

## 48. A guard branch exercised only by a REAL defect has an expiry date — calibrate it BEFORE you fix the defect

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Guards*, immediately after *calibrate against a known-bad input*.

**[SKILL.md Guards]** — this is §"a calibration can stop exercising its branch when the BOARD
changes" with the timing made explicit, and it is the version that would have caught it.

A check with two branches had a calibration for one of them. The other — orphaned pour islands —
had none, and nobody noticed, because the real board had orphans and the branch fired on every
run. That is genuine evidence. It is also evidence that **expires the moment you fix the board**:
remove the islands and the branch goes silent, untested, still reporting PASS, and the loss is
invisible precisely because the output improved.

The rule: when you are about to remove the condition that has been exercising a guard, the
calibration for that guard is part of the fix, not follow-up work. Write it first, watch it fire
while the defect is still there, then fix the defect.

## 49. On a board that ALREADY exhibits the fault, a calibration must be DIFFERENTIAL

> **PROMOTED, 2026-08-18** — folded into `SKILL.md` -> *Guards*, paired with SS48.

**[SKILL.md Guards]**, and it is the trap directly under §48.

Having written the missing calibration, the obvious form is: inject the fault, assert the check
raises, assert the message matches. On a board with 16 orphaned islands already, **that passes
without testing the injection at all** — the check raises because of the 16, the message matches
because it always would, and the calibration reports FIRED having proved nothing. The naive form
is exactly wrong on precisely the boards where the guard matters most.

Make it differential instead: count the fault instances *without* the injection, count them
*with* it, and require the count to rise **by exactly one** and the report to name the object you
injected. That is sound whether the clean board has zero instances or sixteen, and it is what
turned "FIRED" into `orphan-island 0 -> 1`, which is a claim with content.

The general shape: **a calibration must be a measurement of the injection's effect, not of the
board's state.** Any calibration whose assertion could pass on the un-injected board is decorative.

## 50. Scope a global policy to the objects whose requirement motivated it — the measurement that set it may be narrower than the setting

**[PCB.md]**

Zone island removal was `NEVER` board-wide, and it had been *measured*: a compromise setting took
two inner fills from congruent-to-0.0 µm² to 189.7 µm² against a 50 µm² limit. Good decision, real
number, correctly documented — and applied to every pour on the board.

But re-read what the number is about. It is about the pours whose **shape is a requirement**: two
mirrored power pours whose congruence *is* the board's thermal symmetry, where removal destroys
copper asymmetrically. The control- and host-section ground pours have no mirror partner and are
not in the congruence check at all. For them an island is not a shape question — it is a floating
plate under eight high-impedance ADC inputs, i.e. a coupling path *between* them, which is a harm
the codebase's own stitching routine already names in its docstring. Those pours should remove
theirs; the power pours should not.

Result: 6 islands / 7.99 mm² → **0**, congruence unchanged at 6.5 µm². The fix was not a better
threshold — it was noticing that a well-measured decision had been generalised past the objects
its measurement covered. When you inherit a setting with a number attached, check *which objects
the number was measured on* before assuming it applies to all of them.

## 51. A plausible mechanism can be exactly BACKWARDS — measure it, and record the number that killed it

**[PCB.md]**, and the entry exists because the argument still sounds right.

Bottom-layer copper here carries the reference pours under the control and host sections, so a
track routed there does not merely occupy space — it *cuts a plane*, and a cut plane is what turns
into the orphaned islands something else then has to chase. The first grid-search pass, costing
both layers equally, had taken the island count from 6 to 9 with **every new one a bottom-layer
ground island**. Charging bottom-layer travel 3× is the obvious targeted fix: short hops stay
cheap, long runs down the plane get expensive.

It made it worse. Islands went **9 → 17 before stitching, 16 after**, and the stitcher's success
collapsed from 5 anchored to 1. Pushing the search onto the top layer displaces *other*
connections onto the bottom layer in worse places — a second-order effect the argument never
considered and had no way to.

Reverted, with the constant left at parity and the killing number written **beside it in the
code**, not only in a commit message:

```python
# Travelling on B.Cu was charged 3x here, and it was MEASURED and REVERTED.
# ... the board disagreed: at 3x the count went to SEVENTEEN before stitching
# and sixteen after, and the stitcher went from anchoring 5 to anchoring 1.
MAZE_LAYER_COST = {pcbnew.F_Cu: 1.0, pcbnew.B_Cu: 1.0}
```

A reverted experiment that leaves no trace gets re-run. The comment is the artefact, and it has to
live where the next person will be tempted to change the constant.

## 52. Reference designators need their own over-PAD check — courtyards are the wrong test and so are tracks

**[PCB.md]**

`silk_over_copper` on a reference field is the one silk violation the usual guards miss, and each
of the three obvious tests is wrong in a different way:

* KiCad's own `silk_overlap` compares silk against silk;
* a hand-written note check typically compares board **notes** against **courtyards** — it does
  not look at reference fields at all;
* and testing references against *courtyards* fails every densely packed passive row for a
  non-problem, while testing them against *tracks* fails for something the solder mask covers.

The right object is the **mask opening**: silk is clipped where it crosses an exposed pad, so the
test is visible silk reference bbox vs. every *other* footprint's pads on the mask layer. That is
a dozen lines and it closed the last DRC violation on this board — one introduced when a
diagnostic block was re-packed and two passives moved under an IC's reference field, which had sat
in empty space until then.

Two details worth copying. Key the fix as an **offset from the footprint origin**, never a
board-absolute position, or it silently stops tracking the part the next time placement moves.
And exempt a part's own pads — a reference over its own body is normal and flagging it makes the
check unusable.
