# Firmware pin contract from the netlist — scope

Status: **scoped, not started** (2026-10-22); revised after an independent review the same day
(findings folded in below; marked *rev*). Inspired by Seeed's
`kicad-mcp-server` (`tools/device_tree.py`, `tools/pin_analysis.py`). This plan uses that
feature set, not that implementation.

## Problem

A board's MCU pin map is restated by hand in firmware: which package pin carries which net,
which port/pin/alternate function that is, and which I2C/SPI/UART devices hang off it at
which address. Whenever a schematic moves a net, those restatements silently drift.

o2-probe has already solved this locally, and that solution is the reference design:

- `hw/o2-probe/firmware/tools/gen_hw_contract.py` imports `gen_sch.py` and emits
  `src/hw_contract.h`: net per LQFP pin, plus divider ratios. `--check` runs as a PlatformIO
  pre-build step.
- `src/board_pins.h` hand-binds PORT/PIN/AF and `_Static_assert`s the package pin against the
  contract.
- `board_pins.h` comments carry claims that were checked by hand against the datasheet, e.g.
  "PC6 carries NO USART1_DE alternate function".

The parts that do not carry over to other boards: the contract is read from the **generator**
(o2-probe-specific), and the AF/peripheral claims are **prose**, re-checked by hand on every
revision.

## Why not adopt Seeed's code

Seeed infers functions from **net names** (`SDA\d*`, `TX\d*`, `IO[_\d]+`, `P\d+`). That
matches almost anything, and an unrecognised net yields `None`, which their code treats as
"not a peripheral". MCU capability is a hard-coded stub (every STM32 `Px` pin gets
`["GPIO","ADC","TIM","USART","SPI","I2C","CAN"]`, 25 mA, not 5 V tolerant). I2C addresses
are parsed out of net names (`I2C_SDA_0x76`). The DTS output comes from a part-value →
`compatible` table of about 30 entries. Each of these is a proxy for the real quantity
(GUARDS.md, "proxy-for-the-thing"), so none of it is reused.

## Deliverable

`scripts/kicad_pin_contract.py`: project-agnostic, built on `kicad_netlist.py`.

```
kicad_pin_contract.py extract NETLIST --mcu U6 [--pindb DIR] -o contract.json
kicad_pin_contract.py emit-c contract.json --prefix O2P_HW -o hw_contract.h
kicad_pin_contract.py check contract.json --bindings bindings.yaml   # firmware-side claims
```

### 0. Netlist input and parser prerequisites (*rev*)

- `kicad_netlist.py` reads the **S-expression** export only; o2-probe's checked-in
  `o2-probe.xml` is KiCad XML and is rejected. The tool takes the `.kicad_sch`, runs
  `kicad-cli sch export netlist --format kicadsexpr` into a temp file itself, and hashes both
  the schematic and the export.
- Extend `kicad_netlist.py` (with tests), before anything else:
  - **keep `dnp`**: KiCad 10 writes a value-less `(property (name "dnp"))`, which the
    current property loop drops, so DNP parts (o2-probe: C4, C8, R4, R39) look populated;
  - **expose `pintype`** per node (needed for the rail definition in §3).

### 1. Pin map (netlist only, no inference)

For the MCU ref, which is resolved by the MPN field and not by Value, read every node and
record `(package pin, pinfunction, pintype, net)`.

- Pin name comes from the symbol's `pinfunction`. Strip a `_N` suffix **only when N equals
  the pad number** (`PA0_11` → `PA0`). Anything else goes through a declared alias table
  (`VDD/VDDA` ↔ DB `VDD`, `VSS/VSSA` ↔ `VSS`, `PF2-NRST` ↔ DB `PF2 - NRST`). If a pin name
  does not parse, raise. Never drop it. KiCad alternate-pin selections may change the
  reported pinfunction; this is unverified and must be tested.
- Unconnected (`unconnected-(…)`) pins are recorded as NC, not omitted.
- Raise if the MCU ref is missing or is not unique, or if its node set differs from the pin
  DB's `<Pin>` positions for the package. The netlist carries no footprint pad count, so the
  DB is the reference. Exposed pads (`HasPowerPad`, an EP numbered past the last pin, or
  several pads sharing a number) are declared explicitly.

### 2. Capability from the vendor database (not name patterns)

- **STM32:** `STMicroelectronics/STM32_open_pin_data`. `mcu/<RefName>.xml` gives
  per-package `Position` and `Signal` lists. AF numbers are in
  `mcu/IP/GPIO-<version>_Modes.xml` (*rev*: not `ip/`, which is 404), with the version
  picked from the MCU file's `<IP Name="GPIO" Version=…>`. AFs are macro strings to parse
  (PB6 `I2C1_SCL` → `GPIO_AF6_I2C1`, PB3 `USART1_DE` → `GPIO_AF4_USART1`).
- **Variant matching (*rev*):** MPN `STM32G071CBT7` maps to RefName `STM32G071C(6-8-B)Tx`
  (expand the parenthesised alternatives; `x` is the temperature digit only). `x` must not
  absorb an `N` suffix, because the repo has separate `…TxN`/`…UxN` files. Never emit DB
  temperature data: the Tx file says 85 °C, which is wrong for suffix 7.
- **One position, several `<Pin>` entries (*rev*):** take the `Type="I/O"` entry. On
  o2-probe, positions 29/32 also carry a `UCPD1_DBCC` MonoIO entry, and 33/34 carry
  `PA11 [PA9]`/`PA9 [PA11]` remap pairs. A binding that uses a remapped pin must name the
  SYSCFG remap, or `check` fails. Measured
  2026-10-22: the `STM32G071C(6-8-B)Tx.xml` PC6 entry lists TIM2_CH3, TIM3_CH1,
  UCPD1_FRSTX1/2 and GPIO, which agrees with board_pins.h's no-USART1_DE claim.
- Vendor and pin the DB revision (commit SHA + file hash) in the contract. Do not fetch at
  run time.
- **Cross-check 1:** the symbol's pin name at package pin N must equal the DB's pin at
  `Position=N`. This catches a wrong symbol or package variant (same class as
  `PIN-IDENTITY-POSTMORTEM.md`).
- Other families (ESP32, nRF, RP2040) are **out of scope** until a machine-readable vendor
  pin DB is identified for each. Until then, `extract` on them emits pin map only, with
  `capability: unverified`. It never emits an invented capability.

### 3. Bus inventory (topology, with addresses as evidence)

- **I2C:** a net is an I2C line only if it lands on an MCU pin whose DB signal list contains
  `I2Cx_SDA`/`I2Cx_SCL` **and** both lines of the same instance are used. For each pair:
  devices with pins on both nets, pull-up resistors (value + the rail they go to), and that
  rail's domain. **Required, not just reported (*rev*):** every I2C line needs ≥ 1 populated
  (non-DNP) pull-up to a power net, or `check` fails.
- **Device-end role check (*rev*):** each device on the pair must have its `SCL`/`SDA`
  pinfunction on the net with the matching MCU role. Without this, a swap at the device end
  (e.g. U12 `SDA_1`/`SCL_2`) is invisible to an MCU-side binding check.
- **Rail definition (*rev*):** a power net is one carrying a `power_in`/`power_out` pin or a
  power symbol, never a name match. Series-R traversal is depth 1 (pull-up behind a series
  R, CS through a resistor). Anything behind a level shifter or mux is `unclassified` and
  outside duplicate-address scope.
- **Device address:** resolve strap pins by pinfunction (A0/A1/ADDR) to a net, never by
  pad number. GND, a rail, or "via R to
  X" is reported as **topology**. Turning straps into a 7-bit address needs a per-part
  table with a datasheet citation (page + revision hash) in a small curated YAML **keyed by
  MPN** (*rev*). Some addresses come from the ordering code, not straps (SHT45-**AD1F**).
  Parts without an entry give `address: unverified`, never a guess. Duplicate verified
  addresses on one bus are an error. A bus that reaches a connector (o2-probe J108/J109 carry
  I2C to an off-board lead with `TMP117_EXT` 0x4A) is reported as `off-board: unverified`,
  so the duplicate check never claims the bus is complete.
- **SPI / UART:** same pattern. SCK/MISO/MOSI and TX/RX go by DB signals; CS by
  per-device trace from a GPIO to a `CS`/`NSS`-function pin of the device. RS-485 DE/RE
  are recorded as plain GPIOs with their passive pull direction (R to GND/rail), because
  board_pins.h's safety argument rests on exactly that.
- Anything that matches no rule is listed as `unclassified`. It is never dropped.

### 4. Outputs

- `contract.json` is canonical: deterministic ordering, plus the sha256 of the netlist and
  the pin DB.
- `emit-c` writes a header in the shape of o2-probe's `hw_contract.h`
  (`<PFX>_PIN_<n>_NET`, `<PFX>_NET_<name>_PIN`), plus `<PFX>_NET_<name>_PORT/_PIN` derived
  from the DB, so `board_pins.h` can assert port/pin and not only the package pin. Declared
  emitter rules (*rev*): NC pins are omitted, the leading `/` is stripped, and `_PIN` is
  emitted only for single-pin nets. Multi-pin nets get `_PINS` (o2-probe's current header
  maps 3V3D → 4, its lowest pin, though 3V3D is also on 6). No port/pin for power nets.
- `check` reads a small firmware-side bindings file (net → peripheral signal, e.g.
  `RS485_TX: USART1_TX`, `I2C_SCL: I2C1_SCL`) and fails if the pin's DB signals don't
  contain it, or if the AF number differs. This turns board_pins.h's prose AF claims into a
  checked table.
- **Zephyr DTS overlay: deferred.** Neither of our firmware stacks (o2-probe bare-metal
  PlatformIO, Fugu ESP-IDF) consumes device tree today. Add it only when a Zephyr target
  exists, as one more emitter over the same JSON. No `compatible` guessing table.
- **Not doing:** Seeed-style pytest "hardware test" generation.

## Guard checklist (GUARDS.md / CLAUDE.md)

1. **Unevaluable input:** missing MCU ref, unparsable pin name, DB file or package not
   found, or netlist parse failure all raise. No capability data means `unverified`, never
   OK.
2. **Monotonicity:** more mismatches can only add failures. The test is a far-tail
   calibration: every MCU pin re-assigned.
3. **Preconditions:** `check` takes the `.kicad_sch`, re-exports, and fails if the schematic
   or export hash differs from the contract's (*rev*: a hash of a stale export would match a
   stale contract).
4. **Source of truth:** the contract fingerprints the netlist, the pin DB revision and this
   script's own hash.
5. **Persistence:** no cache of verdicts. The DB is vendored data, not a cached result.
6. **Provenance:** every field records where it came from (`netlist`, `pindb@sha`,
   `curated:<file>#<part>`), and `unverified` is explicit.
7. **Known-bad calibration** on o2-probe's netlist, each observed to fail:
   - swap I2C_SCL/I2C_SDA;
   - move RS485_DE to a pin and bind it as `USART1_DE`: PC6 must be rejected;
   - substitute a wrong-package symbol (pin-name ≠ DB position);
   - remove the R10 pull-up;
   - add a second device at the same verified address;
   - use a stale contract against an edited netlist.
8. **Fix vs mute:** there is no allow-list for mismatches. A deliberate exception is a
   bindings-file entry with a reason, and it is printed in every report.

## Acceptance

- The as-is o2-probe Rev B passes position cross-check 1. This is a test: without the alias
  table it fails on pins 6, 7 and 10.
- On o2-probe Rev B, an exact two-way diff of `O2P_HW_PIN_*_NET` and single-pin
  `O2P_HW_NET_*_PIN` lines against the current `hw_contract.h` is empty under the declared
  emitter rules. The
  divider-ratio part stays in `gen_hw_contract.py`, because it is generator-specific.
- `check` passes on the as-is bindings and fails on every calibration case above.
- Runtime: under 1 s on o2-probe (measured, not assumed).
- Unit tests sit next to the existing `scripts/test_*.py`. SKILL.md routing gets a row
  ("firmware pin map / bus inventory → `kicad_pin_contract.py`"), and `scripts/README.md`
  gets an entry.

## Effort and order

0. `kicad_netlist.py` dnp/pintype extension + tests: about 2 h.
1. Pin map + C emitter + o2-probe reproduction: about half a day.
2. STM32 pin DB vendoring, position cross-check, `check` with AF numbers: about a day.
3. I2C/SPI/UART inventory + curated address YAML for parts on our boards (LMP91000,
   TMP117, SHT4x, ADS1119, INA228): about a day.
4. Calibration suite: about half a day.

Adopting it in o2-probe (replacing the pin-map half of `gen_hw_contract.py`) is a separate,
opt-in change in that repo.

## Side finding for o2-probe (separate fix, *rev*)

`firmware/tools/gen_hw_contract.py:160` checks the TMP117 strap as `net_of("U10", 5)` and
labels it ADD0. In the netlist, U10 pin 4 is `ADD0_4` and pin 5 is `V+_5`; both sit on
`/3V3D`. So the guard tests that V+ is on 3V3D, which is always true, and passes today only
by coincidence. This is the pad-number-as-identity class this tool's pinfunction-based
resolution is meant to catch. Verified 2026-10-22 against `o2-probe.xml`.

## Open questions for Fab

- **Generator vs netlist authority.** Recommendation (reviewer and author agree): keep
  `gen_sch.py` authoritative for o2-probe, and run the netlist contract as an
  **independent cross-check** in `--check`, against a fresh export. The side finding above is
  exactly the generator/schematic disagreement that replacing one with the other would hide.
- **Non-STM32 (ESP32 on Fugu boards).** Recommendation: defer. Emit the pin map only, with
  `capability: unverified`. Whether Espressif publishes a machine-readable IO-MUX DB is
  unresearched.
