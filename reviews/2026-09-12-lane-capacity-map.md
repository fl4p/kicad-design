# 2026-09-12 — lane-capacity map: calibration on o2-probe Rev B

Tool: `scripts/kicad_lane_capacity.py`. Written because a Codex session spent
2026-09-11 11:47 → 2026-09-12 09:25 producing **698** `o2-probe.kicad_pcb`
variants under `/tmp` while re-routing the 2-layer o2-probe after a rule
change, and nobody had asked whether the placement could route at all under
the new rules. `ROUTING.md` already says "confirm the cluster is actually
capacity-bound first, by measuring free lanes per y"; this is that measurement.

## Boards and rule sets

| board | what |
|---|---|
| `~/dev/ee/hw/o2-probe/routing/revb-89-tmp302/o2-probe.kicad_pcb` | source: 20 × 89 mm, 2 layers, 101 footprints, 84 nets with pads, routed under the old rules (2552 tracks, 283 vias) |
| `/private/tmp/o2-bus-review-ground-leaf/o2-probe.kicad_pcb` | the Codex session's latest candidate at 09:25, DRC-clean under the override (2250 tracks, 210 vias) |

| rule set | track | clearance | via | hole clr | edge clr |
|---|---|---|---|---|---|
| `old` (source `.kicad_pro`) | 0.09 | 0.15 | 0.25/0.15 | 0.20 | 0.20 |
| `aisler` (candidate `.kicad_pro` before the override) | 0.125 | 0.125 | 0.45/0.25 | 0.25 | 0.30 |
| `override` (Fab, 2026-09-12 00:47) | 0.10 | 0.10 | 0.40/0.20 | 0.25 | 0.30 |
| `fat` (known-bad) | 0.5 | 0.5 | 1.0/0.6 | 0.5 | 0.5 |

`/GND` excluded as pour. Demand widths from the source project: 83 nets at
0.18 mm, one (CELL_RE) at 0.25 mm. Step 0.5 mm, 178 cuts across the width.

## Results

Placement mode, source board, worst cut (y = 117.75 mm, the 5 mm neck between
the thermal-barrier slots; nets that must cross: +12V_SW, 3V3D, HEATER_PWM,
I2C_SCL, I2C_SDA, CELL_CE, CELL_RE, CELL_WE):

| rule set | both layers | F.Cu only (B.Cu reserved as pour) |
|---|---|---|
| old | 0.36 | 0.73 |
| aisler | 0.42 | 0.84 |
| override | 0.35 | 0.70 |
| fat | **3 cuts OVER** (y 104.75, 117.75, 118.25) | — |

Cuts along the other axis (x, both layers, override): worst 0.07.

Via cost, source board's 283 vias, dead strip per via on every layer and total
as a share of the bounding box: old 0.66 mm / 5.5 %, aisler 0.78 mm / 7.7 %,
override 0.76 mm / 7.2 %. The candidate's 210 vias at 0.45/0.25 under the
override: 0.85 mm / 6.7 %.

Residual mode on the candidate (existing copper as obstacles, demand only for
nets with no track across the cut yet), F.Cu only: no cut at or over capacity;
one unrouted-across-cut net (VA_MON at y = 90.25). Densest F.Cu cuts by
used/(used+free) lanes: y = 107.75 at 0.64 (14 used, 8 free), y = 107.25 at
0.50, everything else ≤ 0.38.

Pass-between check (one track between adjacent pads, need = track + 2 ×
clearance): U4 (VSSOP-8, 0.15 mm gap), U6 (LQFP-48, 0.20), U7 (WSON-14, 0.25),
U8 (WQFN-16, 0.25), U10 (WSON-6, 0.25) and U16 (DRL6, 0.20) allow no
pass-between under ANY of the three real rule sets; the override newly allows
it on the SOT-23-5 / TSOT-23-6 parts (0.35 ≥ 0.30). The rule change did not
change any fine-pitch escape topology.

## Reading

- The placement is **not capacity-bound** at any cross section under any real
  rule set, even with B.Cu wholly reserved for the pour. The tightest place on
  the board is the barrier neck at 70–84 %, and that is a lower-bound demand
  against an optimistic capacity, so it deserves a hand-authored corridor, but
  it is not infeasible.
- The override is slightly **looser for tracks** than the old rules (0.70 vs
  0.73 at the neck). What it took away is via room: every via now blocks a
  0.76 mm strip on both layers instead of 0.66, with 200–280 of them. On a
  2-layer board with a pour on B.Cu that is the constraint the churn was
  actually fighting, together with the ground-return and guard constraints the
  session's own reviewer kept raising — none of which a router or this map sees.
- Consequence for method: do not widen the candidate search. Author the via
  budget and the B.Cu corridor plan (which nets may cut the pour, where),
  hand-route the neck and the AFE row (y ≈ 104–112), then let the router close
  the rest once.

## Known-bad and monotonicity

`fat` flags three cuts at exactly 1.00 (lane count and mm agree there); before
the counter was made consistent with the row flag it reported "0 over-capacity
cuts" beside three `OVER` rows. Widening the rules from `old` through `fat`
never increased a lane count at any cut (checked on the CSVs).

## Model limits, stated so the numbers are not over-read

- Pads are bounding boxes; rotated pads are conservative.
- Demand is one crossing per net, pad-centre based. Nets that must cross a cut
  twice, or via-hop across it, are under-counted.
- Placement mode ignores vias entirely; residual mode treats them as obstacles.
- Rule areas count only where they forbid tracks on the layer. Net-specific
  clearance rules (the CELL_RE 0.25 mm track-to-track rule) are not modelled.
- `--exclude-layer` is a declared assumption, not something read from the board.
- No SVG legend beyond the column header; the heat strip colours ratio at
  0.5 / 0.7 / 1.0, the grey bars are lanes per layer, the dark bar is demand.
