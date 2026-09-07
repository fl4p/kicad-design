# SWIG `pcbnew` inventory — every use in this skill, and what a port would have to replace

Scope: this repository (`kicad-design` skill) at `swig-to-ipc`, base `7b00165`. Produced by
`grep -rn "pcbnew" scripts/ *.md` on 2026-09-07 plus a read of each call site. This file records
**what the code does today through SWIG** — that half is read off the source and is settled.

The third column is the **port question**, not an answer. No cell in it was checked against
`kicad-python` when this file was written; a cell reading "exposed" was the author's expectation.
The answers are in [`SWIG-to-IPC-migration.md`](SWIG-to-IPC-migration.md), which reads
`kicad-python` 0.8.0's source and KiCad's own binaries and settles it: **the released IPC API
cannot open a saved board without a running KiCad GUI**, which makes every row here moot until
KiCad 11. Two of the four decisive operations below — `GetEffectiveShape`+`Collide` and the
Specctra DSN/SES boundary — have no IPC equivalent in any version examined, release or master.
Where this file's third column and that one disagree, that one is measured and this one is not.

Why this file exists: KiCad deprecated the SWIG `pcbnew` bindings in 9.0 and its developer
documentation names removal in a future major release. Everything this skill measured about
`Save()` semantics, `.kicad_pro` side effects and zone-fill settling is SWIG-specific
([`../PCBNEW.md`](../PCBNEW.md)) and does not transfer by assumption.

## The interpreter re-exec helper — there are three of them, not one

`pcbnew` is a native extension inside KiCad's own Python framework and is not importable from an
ordinary venv. Six scripts import it at HEAD -- `kicad_autoroute_scaffold.py`,
`kicad_backend.py`, `kicad_copper_collisions.py`, `kicad_route_candidate.py`,
`kicad_route_manifest.py`, `kicad_route_shape.py`. Five was correct against the base commit
`7b00165`; this branch then added `kicad_backend.py` itself, which imports `pcbnew` to probe it,
and the count was not updated (codex review of 95d1e48). Five of the six re-execute themselves
under KiCad's bundled interpreter.
Three *independent* implementations of that helper exist:

| implementation | file | probe | selection |
|---|---|---|---|
| `_interpreter_has_pcbnew` / `_find_kicad_python` | `scripts/kicad_copper_collisions.py:322-370` | `python -c "import pcbnew, sys; sys.stdout.write('PCBNEW-' + 'PROBE-OK')"`, requires exit 0 **and** exact stdout marker | `KICAD_PYTHON` (a configured-but-failing value is an error, never a fallthrough), then `/Applications/KiCad/KiCad*.app/.../bin/python3`, `/usr/lib/kicad*/bin/python3`, then system `python3` |
| `_interpreter_has_pcbnew` / `_find_kicad_python` | `scripts/kicad_route_shape.py:467-500` | same probe, same marker | same order — a near-verbatim copy of the above |
| `_probe_kicad_python` / `find_kicad_python` | `scripts/kicad_route_candidate.py:972-1018` | `python -c "import pcbnew; print(pcbnew.GetBuildVersion())"`, requires exit 0 and a parseable `N.N[.N]` version; **returns the version**, which becomes part of the compatibility cell | `--kicad-python` / `KICAD_PYTHON`, then `shutil.which("python3")`, then per-OS KiCad bundle globs (macOS `/Applications/KiCad`, Windows `%ProgramFiles%\KiCad\*\bin\python.exe`, else `/usr/bin/python3`) |

`scripts/kicad_autoroute_scaffold.py:327` calls `find_kicad_python` from `kicad_route_candidate`,
so it is a consumer of the third. `scripts/kicad_route_manifest.py` has **no** discovery at all —
`scripts/README.md` documents invoking it as `"$KICAD_PYTHON" scripts/kicad_route_manifest.py
promote …`, i.e. the caller supplies the interpreter.

Two scripts also construct a wx application object before importing `pcbnew`
(`kicad_route_manifest.py:183` uses `wx.App(False)`; `kicad_route_candidate.py:4156` uses
`wx.AppConsole()` and its comment records that `wx.App(False)` "hangs or degrades
`GetSettingsManager()` to a raw `SwigPyObject` in headless KiCad 10.0.5/Darwin"). That is a
wx-lifetime workaround for a headless SWIG process, not a GUI: nothing is displayed.

The worker boundary itself is load-bearing and is *not* a pcbnew concern: exit 0 from a re-executed
worker is trusted only together with a printed verdict line, and `--json` targets are pre-stamped
`unevaluable` before evaluation. A port must keep that boundary intact whatever runs inside it.

## Per-script symbol inventory

### `scripts/kicad_copper_collisions.py` — certain-short audit (ARTIFACT_GUARD)

| pcbnew symbol | used for | port question (UNVERIFIED at commit time) |
|---|---|---|
| `pcbnew.LoadBoard(path)` | open the saved board headlessly (`:281`) | see note below on document-opening |
| `board.GetEnabledLayers().Seq()`, `pcbnew.IsCopperLayer(l)`, `board.GetLayerName(l)` | enumerate and name copper layers (`:123-127`, `:157`) | layer enumeration is exposed; `LSET`/`Seq()` is a SWIG type with no IPC analogue |
| `board.GetTracks()`, `track.IsOnLayer(l)` | collect track/arc/via copper per layer | item listing exposed; per-layer membership must be recomputed client-side |
| `board.GetFootprints()`, `fp.Pads()`, `pad.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH`, `pad.IsOnLayer(l)`, `pad.FlashLayer(l)` | pad copper, with NPTH pads counted only where flashed | `FlashLayer` is a KiCad-internal geometric decision with **no** client-side equivalent |
| `item.GetEffectiveShape(layer)` → `SHAPE`, `shape.BBox()`, `shape.Collide(other, 1)` | **the audit itself**: effective per-layer copper shape and a 1-IU touch collision | this is the load-bearing one. `GetEffectiveShape` resolves KiCad's internal shape model (pad shape, teardrops, arc approximation); a client re-implementation is a different measurement, not a port |
| `item.GetNetCode()`, `item.GetNetname()`, `item.GetClass()`, `item.GetPosition()`, `item.GetNumber()`, `item.GetParentFootprint().GetReference()` | finding identity and report strings | nets/refdes/positions are exposed |
| `pcbnew.ToMM(iu)` | internal-unit → mm for the report | trivial arithmetic (1 IU = 1 nm), no dependency |

### `scripts/kicad_route_shape.py` — routing-shape audit (ARTIFACT_GUARD)

| pcbnew symbol | used for | port question (UNVERIFIED at commit time) |
|---|---|---|
| `pcbnew.LoadBoard(path)` | open the saved board (`:188`) | as above |
| `board.GetTracks()` | iterate all track copper | exposed |
| `isinstance(t, pcbnew.PCB_VIA)` / `pcbnew.PCB_ARC` | discriminate via / arc / segment | IPC has distinct message types for track, arc, via; the discrimination survives, the `isinstance` spelling does not |
| `t.TopLayer()`, `t.BottomLayer()`, `board.GetLayerName(l)` | via layer-span histogram | via layer pair is exposed |
| `t.GetLength()` | **arc-aware** copper length — the docstring records that `hypot(start,end)` would return the chord and understate curved copper | a length accessor on an arc is the specific thing to verify; if IPC returns only geometry, the port must compute arc length itself and re-derive the docstring's claim |
| `t.GetLayer()`, `t.GetStart()`, `t.GetEnd()`, `t.GetNetname()` | segment length/angle distribution, per-net via counts | exposed |

### `scripts/kicad_route_manifest.py` — promote a reviewed candidate (mutating)

| pcbnew symbol | used for | port question (UNVERIFIED at commit time) |
|---|---|---|
| `pcbnew.LoadBoard`, `pcbnew.SaveBoard(path, board)` | load a seed, write the promoted board, reload it to re-extract (`:203`, `:210`, `:212`) | **saving to an arbitrary path** is the crux; see the note below |
| `pcbnew.PCB_TRACK(board)`, `pcbnew.PCB_VIA(board)`, `board.Add(item)` | materialise manifest routes as board items (`:96-115`) | IPC creates items through the server; ownership and commit semantics differ |
| `item.SetStart/SetEnd/SetWidth/SetLayer/SetNet/SetLocked/SetPosition/SetDrill/SetLayerPair/SetViaType` | route geometry and net binding | mutation is exposed but through a create/update request, not attribute setters |
| `pcbnew.VECTOR2I`, `pcbnew.F_Cu`, `pcbnew.B_Cu`, `pcbnew.VIATYPE_THROUGH` | value types and enums | IPC has its own protobuf value types and enums |
| `board.FindNet(name)`, `board.GetLayerID/GetLayerName` | resolve net and layer by name | exposed |
| `board.BuildConnectivity()`, `pcbnew.ZONE_FILLER(board).Fill(board.Zones())` | refill zones after applying routes (`:207`) | **zone filling** — see the note below |
| `pcbnew.GetBuildVersion()` | stamped into every worker envelope as `pcbnew_version` | an IPC backend has no `pcbnew` version to stamp; the envelope field's meaning changes |

### `scripts/kicad_autoroute_scaffold.py` — project onboarding, seed inspection

| pcbnew symbol | used for | port question (UNVERIFIED at commit time) |
|---|---|---|
| `pcbnew.LoadBoard`, `pcbnew.SaveBoard` (`:1420`, `:1469`) | seed inspection and scratch save | as above |
| `pcbnew.GetSettingsManager()` → `LoadProject`/`GetProject`, `board.SetProject`, `board.SynchronizeNetsAndNetClasses(False)` (`:1425`) | attach the same-stem `.kicad_pro` so net-class assignments resolve — without it every net maps to Default and a scoped route becomes a whole-board route | `SETTINGS_MANAGER`/`PROJECT` are application objects. A running application has a project by definition; a headless client addressing a file does not |
| `board.GetTracks/Zones/GetNetInfo/GetLayerName/IsLayerEnabled/GetCopperLayerCount/BuildConnectivity` | snapshot inventory | exposed |
| `item.GetUuid()`, `item.GetViaType()`, `item.IsLocked()`, `Get{Start,End,Position,Width,FrontWidth,DrillValue,Netname,LayerName}`, `Top/BottomLayer()` | v5 semantic snapshot fields | mostly exposed; `GetFrontWidth`/per-layer via width and `GetViaType` need per-field verification |
| `pcbnew.ZONE_FILLER(board).Fill(board.Zones())` (`:1467`) | refill before save | see the note below |
| `pcbnew.IsCopperLayer`, `pcbnew.GetBuildVersion` | layer filter, version stamp | as above |
| compatibility-cell key `"pcbnew"` (`:1148`, `:1319`, `:1329`) | one axis of the exact OS/arch/kicad-cli/pcbnew promotion matrix | a backend change **invalidates every recorded cell**: the matrix keys on a `pcbnew` build version that an IPC backend does not have |

### `scripts/kicad_route_candidate.py` — candidate creation, DSN/SES, semantic snapshot

| pcbnew symbol | used for | port question (UNVERIFIED at commit time) |
|---|---|---|
| `pcbnew.ExportSpecctraDSN(board, path)` (`:4239`) | export the DSN Freerouting consumes | **not known to be exposed by IPC**; `kicad-cli` has no `pcb export specctra` subcommand either (verify against the installed CLI before relying on this row) |
| `pcbnew.ImportSpecctraSES(board, path)` (`:4264`) | import the router's SES back onto the board | same |
| `pcbnew.LoadBoard`/`SaveBoard`, `GetSettingsManager`+`SetProject`+`SynchronizeNetsAndNetClasses` (`:4170-4191`) | scratch load with project net classes | as in the scaffold row |
| `pcbnew.ZONE_FILLER(board)` … `Fill()` (`:4281`) | refill after SES import | see the note below |
| `board.GetTracks/GetFootprints/GetDrawings/Zones/GetNetInfo/GetAllNetClasses/GetEnabledLayers/IsLayerEnabled/GetCopperLayerCount/GetLayerName/GetFileName` | v5 semantic snapshot inventory | mostly exposed; `GetAllNetClasses` and drawing-sheet objects need per-field verification |
| `fp.GetFPID/GetPosition/GetOrientationDegrees/GetReference/GetAttributes/IsFlipped/IsLocked/GraphicalItems/Pads` | footprint identity and graphics for the snapshot | exposed |
| `pad.GetNumber/GetNetname/GetPosition/GetOrientationDegrees/GetShape/GetSize/GetDrillSize/IsLocked` | pad semantics | exposed |
| `zone.GetZoneName/GetNetname/GetAssignedPriority/GetMinThickness/GetIsRuleArea/GetNumCorners/GetCornerPosition/IsLocked` | zone **outline** semantics (not fill geometry) | zone outline is exposed; **filled** polygons are the open question |
| `item.GetLayerSet()`, `GetMid()`, `GetLength()`, `GetViaType()`, `GetFrontWidth()`, `GetDrillValue()` | track/arc/via snapshot fields | per-field verification needed |
| `pcbnew.GetBuildVersion()` stamped into every worker envelope and checked against the expected value (`:1720`, `:1962`, `:2187`) | cross-process version binding | as in the scaffold row — the envelope contract itself has to gain a backend field |

### Non-importing references

| site | what it is |
|---|---|
| `scripts/kicad_autoroute.py:4` | docstring: "This module deliberately has no pcbnew dependency" |
| `scripts/kicad_autoroute.py:1571-1572` | the compatibility-cell key set, including `"pcbnew"` |
| `scripts/kicad_graphics.py:4` | docstring: deliberately avoids importing `pcbnew` so pure tests can run — it *serializes* graphics the pcbnew worker hands it |
| `scripts/kicad_repro.py:22` | docstring: the case of running a `pcbnew` generator under an interpreter that cannot import `pcbnew` |
| `scripts/kicad-autoroute-compatibility.json` | records a concrete `pcbnew` version per cell |
| `scripts/test_*.py` (5 files) | fakes/stubs standing in for pcbnew objects; no real import |

### Documentation references

| file | what it asserts about SWIG |
|---|---|
| `PCBNEW.md` (33 hits) | the whole file. API traps re-probed on 10.0.5, the `LoadBoard`→`Save` non-round-trip, the 9.x silent migration, `board.Remove()` proxy invalidation, the empty-`GetConnectedPads` failure, UUID-ordering nondeterminism, SWIG object-lifetime SIGBUS |
| `ROUTING.md:401-412` | `pcbnew.BOARD.Save()` writes the sibling `.kicad_pro` too; `SaveBoard(path, board, True)` suppresses it; refill after |
| `PCB.md:501-508` | the SWIG API does not usefully expose `BOARD_STACKUP` |
| `AUTOROUTING.md:246,265,338,468,527` | `ZONE_FILLER` refill before DRC; `pcbnew` version as a staging axis |
| `FOOTPRINTS.md:194,253`, `RELEASE.md:158` | `pcbnew.FromMM`, layer constants, `GetShape()` in illustrative snippets |
| `README.md:22,43`, `SKILL.md:48`, `THERMALS.md:9` | pointers to `PCBNEW.md` |

## The four operations that decide whether a port is possible

Ordered by how much of the skill they carry:

1. **Open a `.kicad_pcb` from disk, headlessly, with no GUI.** Every guard here runs in CI against
   a saved file. `pcbnew.LoadBoard(path)` is in five scripts and is the first line of every worker.
2. **`GetEffectiveShape(layer)` + `Collide`.** The certain-short audit is nothing else. It is
   KiCad's own shape resolution; re-implementing it client-side produces a different measurement
   with different calibration, and `scripts/README.md` records that audit as calibrated on
   2026-08-26 against a 295-collision known-bad and a clean 1761-item board.
3. **`ZONE_FILLER` + `SaveBoard`.** The `GUARDS.md` semantic-settle contract is written in terms of
   filling the *same loaded board* twice and comparing per-zone `BooleanXor`. That requires a fill
   API and access to filled polygons.
4. **`ExportSpecctraDSN` / `ImportSpecctraSES`.** The entire Freerouting boundary.

Anything an IPC backend cannot do in this list is not a rough edge; it is a guard this repo cannot
run on that backend, and by `GUARDS.md` that is UNEVALUABLE, never a skip and never a pass.
