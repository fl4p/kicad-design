# SWIG → IPC: what is blocking, what is measured, and the plan

Companion to [`SWIG-to-IPC-inventory.md`](SWIG-to-IPC-inventory.md), which lists every place
this skill calls `pcbnew`. This file answers the question that inventory poses: **can the IPC
API run this workload?**

**Short answer, 2026-09-07: no, not on any released KiCad.** The IPC API in KiCad 9 and 10 is
served only by the running GUI application, and its protocol has no command that opens a file
from disk. Headless serving exists — `kicad-cli api-server` — but only on KiCad's development
branch, which is also the branch that has already deleted SWIG. The two land together in
KiCad 11.

Every guard in this repository grades a **saved file with no GUI**. That is precisely the mode
the released IPC API does not have. So the port is not blocked on effort; it is blocked on a
release.

## Contents

- [The evidence for the GUI question](#the-evidence-for-the-gui-question)
- [The removal timeline](#the-removal-timeline)
- [What kicad-python 0.8.0 can and cannot do](#what-kicad-python-080-can-and-cannot-do)
- [Operation-by-operation, if headless arrives](#operation-by-operation-if-headless-arrives)
- [What was built instead](#what-was-built-instead)
- [The plan](#the-plan)
- [What is NOT established here](#what-is-not-established-here)

## The evidence for the GUI question

All local measurements are single-host, single-session — **KiCad 10.0.5 on macOS 15 (Darwin
24.6.0), 2026-09-07, not replicated.** Upstream quotations are dated by their source.

**KiCad's own documentation says it outright.** `dev-docs.kicad.org`, IPC API for add-on
developers:

> The IPC API in KiCad 9 and 10 only supports communication with a running instance of the
> KiCad GUI. Support for running in headless mode through kicad-cli was added for KiCad 11.
> **There are no plans to support a standalone library for loading and manipulating KiCad files
> separate from the client-server (IPC) model.**

The `kicad-python` 0.8.0 package README, read out of the installed
`kicad_python-0.8.0.dist-info/METADATA`, says the same thing twice:

> Using the IPC API requires a suitable version of KiCad (9.0 or higher) and requires that
> KiCad be running with the API server enabled in Preferences > Plugins.

> Note: Unlike the SWIG-based Python bindings, the IPC API requires communication with a
> running instance of KiCad. **It is not possible to use `kicad-python` to manipulate KiCad
> design files without KiCad running.**

**The installed binaries agree.** The API server class lives in the shared library, but only
the GUI binary calls it:

(The `-i` matters and was missing when this was first written: the symbols are spelled
`KICAD_API_SERVER` in uppercase, so a case-sensitive `grep -c api_server` prints 0 even against
the six GUI symbols, making the saved proof vacuous. Re-run case-insensitively: the GUI still
matches, `kicad-cli` still returns 0. Codex review of 95d1e48.)

```
$ nm -u /Applications/KiCad/KiCad.app/Contents/MacOS/kicad | grep KICAD_API_SERVER
__ZN16KICAD_API_SERVER15RegisterHandlerEP11API_HANDLER
__ZN16KICAD_API_SERVER4StopEv
__ZN16KICAD_API_SERVER5StartEv
__ZN16KICAD_API_SERVERC1Ev
__ZNK16KICAD_API_SERVER10SocketPathEv
__ZNK16KICAD_API_SERVER7RunningEv
$ nm -u /Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli | grep -ci api_server
0
```

`kicad-cli` links `libkicommon` (where `KICAD_API_SERVER` is defined) but references none of
its symbols and exposes no entry point:

```
$ kicad-cli --help
Usage: kicad-cli [--version] [--help] {fp,jobset,pcb,sch,sym,version}
$ kicad-cli api-server --socket /tmp/kicad/x.sock
Failed to parse 'api-server', did you mean 'jobset'    (exit 1)
```

**The server is off by default and is a GUI preference.** `libkicommon` carries the settings
key `api.enable_server`; this host's `~/Library/Preferences/kicad/{9.0,10.0}/kicad_common.json`
both read `"enable_server": false`. KiCad's own local manual, §9.8 *Plugins preferences*:
"**Enable KiCad API**: When enabled, you can use plugins that interact with KiCad's IPC API. If
this option is not enabled, such plugins will not function."

**The socket is per-process.** `libkicommon` carries `ipc://{}` and the diagnostic "Server: PID
socket path %s already exists!" — a client attaches to one specific KiCad process, not to a
service.

**Measured, with no KiCad serving:**

```
>>> from kipy import KiCad; KiCad(timeout_ms=1500).ping()
kipy.errors.ConnectionError : Failed to connect to KiCad: Connection refused
```

**There is no open-a-file command in the released protocol.** The complete set of public names
in `kipy.proto.common.commands.editor_commands_pb2` (enumerated in the venv) contains
`GetOpenDocuments`, `SaveDocument`, `SaveCopyOfDocument`, `SaveDocumentToString`,
`RevertDocument` — and nothing that opens one. `KiCad.get_board`'s own docstring is the whole
story:

```python
def get_board(self) -> Board:
    """Retrieves a reference to the PCB open in KiCad, if one exists"""
```

**Headless serving exists only on master.** From GitLab's unauthenticated tree API:
`kicad/cli/command_api_server.cpp` and `.h` are present on `master` and absent from both the
`10.0.5` and `10.0.6` tags. Its earliest commit is **`caf1bcc4559b`, 2026-03-21, "ADDED:
Headless API server mode for kicad-cli"**. The command it registers:

```cpp
COMMAND( "api-server" )
add_description( _( "Run the KiCad IPC API server in headless mode" ) )
ARG_PATH  help: "Optional path to a .kicad_pro, .kicad_pcb, or .kicad_sch file to pre-load"
--socket  help: "Override API socket path"
```

`master`'s `cmake/KiCadVersion.cmake` reads `10.99.0-unknown` — the KiCad 11 development
branch.

**A live IPC session was not exercised.** Confirming the read path against a running KiCad 10
GUI would need the API server enabled and a board open, and this host already had the owner's
own KiCad session running; a second instance did not take a scratch `KICAD_CONFIG_HOME`. That
experiment would in any case not change the answer — a backend that needs a GUI cannot serve
CI — so it is recorded as **not attempted to completion**, not as a result.

## The removal timeline

| claim | source |
|---|---|
| deprecated in 9.0, "the current plan is to remove the SWIG bindings in KiCad 11.0" | dev-docs.kicad.org, pcbnew Python bindings page |
| "The SWIG bindings still exist in KiCad 9 and 10, but are removed in KiCad 11." | kicad-python 0.8.0 shipped README |
| already deleted on master | commit **`65a442b1d2bf`, 2026-03-22, "REMOVED: SWIG, wxPython, and Python integration"**. `pcbnew/python` holds `examples, plugins, scripting, swig` on the `10.0.6` tag and only `wizards` on `master` |
| the runtime says so on every import | KiCad 10.0.5's own `pcbnew.py:63` warns that the SWIG interface "is deprecated and will be removed in a future version of KiCad" |

**Headless serving was added one day before SWIG was removed** (2026-03-21 and 2026-03-22).
That is the shape of the transition: KiCad 11 takes away `pcbnew` and hands back
`kicad-cli api-server` in the same release. There is no released version where both exist, and
none where neither does.

**Date for KiCad 11: unpublished.** KiCad's release policy says "Major releases will occur
annually by January 31st of a given calendar year"; the last two majors actually shipped
2025-02-20 (9.0.0) and 2026-03-20 (10.0.0). Expect Q1 2027 and treat that as an extrapolation
from two data points, not a schedule.

## What kicad-python 0.8.0 can and cannot do

Installed into `.venv-ipc/` under this worktree (Python 3.14, `pip install kicad-python==0.8.0`).
`kipy/kicad_api_version.py` reads `KICAD_API_VERSION = "10.0.6-0-gcaf7377e9c"`.

**Three of the modules in the wheel do not import at all.** Measured:

```
kipy.board          OK        kipy.board_rules   ImportError: cannot import name
kipy.board_types    OK                           'CustomRuleConstraintType'
kipy.geometry       OK        kipy.board_jobs    TypeError: couldn't resolve name
kipy.server         OK                           '.kiapi.common.types.Units'
kipy.wizards        OK        kipy.schematic     ImportError: cannot import name
                                                 'PageSettings'
```

So the board-design-rules surface — DRC severities, custom rules, minimum constraints — is not
usable from the released package, and the shipped `board_commands` protocol has no
`GetBoardDesignRules` message either.

**`kipy/server.py` is the headless client helper, and it is inert in 0.8.0.** Its docstring is
"Helpers for running a headless KiCad API server via kicad-cli" and it spawns
`[kicad_cli, "api-server", "--socket", path, file]`, waiting for a line beginning
`KiCad API server listening at `. Two things stop it being usable:

- `KiCadServer.wait_for_ready` calls `probe.close()` (server.py:231/234/238); `KiCadClient`
  (client.py, 91 lines) defines no `close` — so the readiness wait raises `AttributeError`;
- `KiCad.__init__` takes `socket_path, client_name, kicad_token, timeout_ms` and nothing else,
  so no released entry point wires a spawned server to a client.

Neither `server.py` nor headless mode appears in the 0.8.0 release notes, while the same
package's README still states that operating without a running KiCad is impossible. Read
`server.py` as a forward-looking artifact of the KiCad 11 work, not as a released capability.

## Operation-by-operation, if headless arrives

Read out of `kipy` 0.8.0's source. **None of it was exercised against a server** — there is no
server on this host to exercise it against — so every row is a reading of the client, not a
measurement of behaviour.

| operation the skill needs | IPC 0.8.0 | note |
|---|---|---|
| open a `.kicad_pcb` from a path | **absent** in the release; on master `kicad-cli api-server FILE` pre-loads one and an `OpenDocument` command exists | the single blocking item |
| zone fill | `Board.refill_zones(block=True, …)` | all-zones only in 0.8.0; a per-zone form is a KiCad 11 addition |
| filled zone geometry | `Zone.filled_polygons -> dict[layer, list[PolygonWithHoles]]`, plus `Zone.filled` | the `GUARDS.md` semantic-settle gate needs per-zone filled geometry, and this is it. No `BooleanXor`: the symmetric difference would have to be computed client-side |
| zone SETTINGS (the ones `PCBNEW.md` says revert invisibly) | `Zone.island_mode`, `min_island_area`, `min_thickness`, `priority`, `fill_mode`, `connection`, `is_rule_area` | present, and better named than the SWIG originals |
| save the board | `Board.save()`, `Board.save_as(filename, overwrite, include_project)` | note `include_project`, which is the IPC face of the `.kicad_pro` side effect `ROUTING.md` documents for SWIG |
| **`GetEffectiveShape(layer)` + `Collide`** | **absent** | the nearest surfaces are `get_pad_shapes_as_polygons(pads, layer)`, `get_item_bounding_box(...)` and `hit_test(item, position, tolerance)`. None of them is a shape-to-shape clearance query. `kicad_copper_collisions.py` therefore has no port: it would have to be **re-implemented** over polygons, which is a different measurement and needs its own calibration against the 2026-08-26 known-bad and known-good boards |
| Specctra DSN export / SES import | **absent** — a case-insensitive grep for `specctra`, `freerouting`, `.dsn` and `ses` across the whole installed package returns nothing | the entire Freerouting boundary in `kicad_route_candidate.py` has no IPC equivalent |
| DRC | **absent** — no `RunDrc` message anywhere; the only DRC surface is `InjectDrcError` (a plugin reporting its own violation) | `kicad-cli pcb drc` stays in the pipeline regardless of backend, which is what `RELEASE.md` already does |
| `pad.FlashLayer(layer)` | `Board.check_padstack_presence_on_layers` | the 0.4.0 notes name it as the FlashLayer replacement |
| track/via/pad/footprint/net/layer inventory | `get_tracks`, `get_vias`, `get_pads`, `get_footprints`, `get_zones`, `get_nets`, `get_enabled_layers`, `get_copper_layer_count`, `get_layer_name`, `get_layer_by_name` | the snapshot fields are largely there; per-field equivalence is unverified |
| board stackup | `Board.get_stackup()` with per-layer εr, loss tangent, thickness, material, finish, impedance control | **better than SWIG**, where `PCB.md` records `BOARD_STACKUP` as an opaque `SwigPyObject` and forces a text edit |
| design rules / netclass constraints | **absent from the release** — `board_rules` does not import and no protocol message exists | `Project.get_net_classes()` gives netclass membership only |

Two of the four operations the inventory named as decisive — effective-shape collision and the
Specctra boundary — have **no IPC equivalent at all**, in any version examined. Those are not
waiting on a release.

## What was built instead

`scripts/kicad_backend.py`, plus wiring in `kicad_copper_collisions.py` and
`kicad_route_shape.py`:

- the backend is named explicitly (`--backend`, `$KICAD_BACKEND`, or the default) and both the
  name **and the source of the choice** appear in every verdict line and JSON report;
- an unavailable backend is UNEVALUABLE with a stable failure ID and never falls back;
- capability is checked separately from availability, so a backend that is reachable but cannot
  do the job is refused rather than used;
- the `ipc` probe reports the first concrete obstacle on this host, and refuses with
  `ipc-backend-not-implemented` even when a host satisfies every precondition — because no IPC
  implementation was written, because none could be verified against a server.

The autoroute/promotion chain (`kicad_route_candidate.py`, `kicad_autoroute_scaffold.py`,
`kicad_route_manifest.py`) was deliberately **not** wired. Its worker envelopes validate exact
field sets and its compatibility matrix keys a cell on a `pcbnew` build version; adding a
backend axis is a schema revision that requires requalifying every cell, and promotion is
already disabled for every cell pending the snapshot-v5 work. Recording the reason here is
cheaper and more honest than a half-migration that makes those envelopes ambiguous.

## The plan

1. **Now — KiCad 10.** Stay on SWIG; it is the only headless board API that exists. Keep the
   backend layer so no report is ambiguous about what produced it. Prefer `kicad-cli`
   (`pcb drc --refill-zones --save-board`, the `export` family, `jobset`) wherever it can
   express a check, because that path survives KiCad 11 untouched.
2. **When a KiCad 11 nightly is available.** Re-run the `ipc` probe: it should reach
   `ipc-no-open-document` or `ipc-backend-not-implemented` rather than `ipc-no-headless-server`.
   Then, on a scratch board, establish the three things this file could not: that
   `kicad-cli api-server FILE` really serves a pre-loaded board headlessly; that `refill_zones`
   plus `filled_polygons` can express the `GUARDS.md` settle gate; and that `save_as` does or
   does not carry the `.kicad_pro` side effect `ROUTING.md` measured for SWIG. Re-measure, do
   not assume, the `LoadBoard`→`Save` round-trip and the UUID-ordering nondeterminism: those
   are SWIG measurements and the IPC path serialises through a different code path.
3. **`kicad_route_shape.py` first.** It is the cheapest port — track/arc/via inventory and
   lengths — and it is the one whose numbers can be cross-checked against the existing SWIG
   backend on the same board, which is the only calibration that can prove an IPC backend
   agrees rather than merely runs.
4. **`kicad_copper_collisions.py` is a rewrite, not a port.** Without `GetEffectiveShape` and
   `Collide` the audit becomes client-side polygon intersection. It must be recalibrated from
   scratch against the 2026-08-26 boards (295 collisions / exit 2, and 1761 items / 0
   collisions) before it may gate anything, and until it is, `--backend ipc` on that tool stays
   UNEVALUABLE.
5. **The Freerouting boundary needs a decision, not a port.** With no Specctra API on either
   side of IPC, `kicad_route_candidate.py` either keeps a SWIG path for as long as a KiCad 10
   is installable, or the DSN/SES round trip moves to a different mechanism entirely.
6. **Do not delete the SWIG backend when the IPC one lands.** Two backends that agree on the
   same board is the only evidence that the port preserved the measurement; one backend that
   replaced another is a claim.

## What is NOT established here

- Anything about a running IPC session's actual behaviour. No server was reached.
- That the kipy surfaces listed above behave as their names suggest. They were read, not run.
- Any KiCad 11 release date. None is published.
- That `kicad-cli api-server` works. Its existence on `master` is established from the source
  tree and its help text; nothing here built or ran it.
