#!/usr/bin/env python3
"""Explicit, reported selection of the KiCad board-access backend.

KiCad has two Python board APIs and they are not interchangeable:

- **`swig`** — the in-process `pcbnew` module. Deprecated in KiCad 9.0 and
  DELETED from KiCad's development branch on 2026-03-22 (upstream commit
  `65a442b1d2bf`, "REMOVED: SWIG, wxPython, and Python integration"). Still
  present and working in every 10.x release, including the 10.0.5 this module
  was written against. It loads a `.kicad_pcb` from a path with no GUI, which
  is the only reason every guard in this repository can run in CI.
- **`ipc`** — the client/server API, Python client `kicad-python` (`kipy`).
  On KiCad 9 and 10 the server is started **only by the GUI application**, and
  the protocol has no command that opens a file from disk. Headless serving
  (`kicad-cli api-server [PROJECT_OR_FILE] --socket PATH`) was added upstream on
  2026-03-21 (`caf1bcc4559b`) — one day before SWIG was removed — so it arrives
  with KiCad 11 and not before. The measurements behind those statements, and
  the operation-by-operation mapping, are in
  [`../plans/SWIG-to-IPC-inventory.md`](../plans/SWIG-to-IPC-inventory.md) and
  [`../PCBNEW.md`](../PCBNEW.md).

Contract, per [`../GUARDS.md`](../GUARDS.md):

- **Selection is explicit and reported.** A caller may name a backend on the
  command line or in `KICAD_BACKEND`; otherwise the default applies. Which of
  those three happened is recorded as the selection's *source* and printed
  beside every verdict, so no report is ambiguous about what produced its
  numbers.
- **There is no fallback, ever.** A named backend that cannot be used is an
  error with a stable failure ID. It never silently becomes the other backend,
  and it is never a skip and never a pass — an unusable backend means the guard
  is UNEVALUABLE.
- **Capability is separate from availability.** A backend that is reachable but
  cannot perform an operation the guard needs is refused at
  :func:`require_capabilities`, again as UNEVALUABLE. Capabilities are claimed
  only for operations this repository actually exercises.
- **No unverified backend is offered as working.** The `ipc` probe reports the
  first concrete reason the backend cannot serve this workload here. If a host
  ever satisfies every precondition, the probe still refuses, with
  `ipc-backend-not-implemented`: this repository ships no IPC implementation,
  because none could be verified against a server. A stub that looked ported
  would be worse than the gap.

Stable failure IDs:

| id | meaning |
|---|---|
| `backend-unknown` | a name that is not `swig` or `ipc` was requested |
| `swig-no-interpreter` | no Python that can `import pcbnew` was found |
| `swig-configured-interpreter-bad` | `KICAD_PYTHON` is set and fails the probe |
| `ipc-client-missing` | `kipy` is not importable in this interpreter |
| `ipc-no-kicad-cli` | no `kicad-cli` could be located to probe |
| `ipc-no-headless-server` | this `kicad-cli` has no `api-server` subcommand |
| `ipc-no-open-document` | this `kipy` cannot open a document by path |
| `ipc-backend-not-implemented` | preconditions met; no verified implementation here |
| `backend-missing-capability` | the selected backend cannot do what the guard needs |

Written for KiCad's bundled interpreter as well as the host one, so it stays
within Python 3.8 syntax and imports nothing outside the standard library.
"""

import glob
import os
import platform
import re
import subprocess
import sys

SWIG = "swig"
IPC = "ipc"
BACKENDS = (SWIG, IPC)
DEFAULT_BACKEND = SWIG
ENV_VAR = "KICAD_BACKEND"
INTERPRETER_ENV_VAR = "KICAD_PYTHON"

SOURCE_CLI = "cli"
SOURCE_ENV = "env"
SOURCE_DEFAULT = "default"

# Operations this repository actually performs. A capability is claimed only
# where a guard in this tree exercises it today; see the inventory.
CAP_OPEN_BOARD_FROM_PATH = "open_board_from_path"
CAP_EFFECTIVE_SHAPE_COLLIDE = "effective_shape_collide"
CAP_ZONE_FILL = "zone_fill"
CAP_SAVE_BOARD = "save_board"
CAP_SPECCTRA = "specctra_dsn_ses"

SWIG_CAPABILITIES = frozenset((
    CAP_OPEN_BOARD_FROM_PATH,
    CAP_EFFECTIVE_SHAPE_COLLIDE,
    CAP_ZONE_FILL,
    CAP_SAVE_BOARD,
    CAP_SPECCTRA,
))

# Built by concatenation so the marker is absent from the probe's command text:
# an executable that merely echoes its arguments must not satisfy the probe.
# Identical in intent to the per-script probes this module replaces.
_PROBE_MARKER = "PCBNEW-" + "PROBE-OK"
_PROBE_SOURCE = (
    "import pcbnew, sys; "
    "sys.stdout.write('PCBNEW-' + 'PROBE-OK' + ' ' + str(pcbnew.GetBuildVersion()))"
)
_PROBE_TIMEOUT_S = 60
_CLI_PROBE_TIMEOUT_S = 30


class BackendUnavailable(Exception):
    """The requested backend cannot be used. Callers must treat this as
    UNEVALUABLE — never as a skip, a fallback or a pass."""

    def __init__(self, backend, failure_id, reason):
        self.backend = backend
        self.failure_id = failure_id
        self.reason = reason
        Exception.__init__(self, "%s: %s" % (failure_id, reason))


class Selection(object):
    """A resolved backend, with everything a report needs to name it."""

    def __init__(self, name, source, detail, capabilities, interpreter=None):
        self.name = name
        self.source = source
        self.detail = detail
        self.capabilities = frozenset(capabilities)
        # None means "usable in this process"; a path means the caller must
        # re-execute itself under that interpreter.
        self.interpreter = interpreter

    def provenance(self):
        """One line naming the backend, how it was chosen, and what it is."""
        return "backend=%s source=%s (%s)" % (self.name, self.source, self.detail)

    def as_dict(self):
        return {
            "name": self.name,
            "source": self.source,
            "detail": self.detail,
            "capabilities": sorted(self.capabilities),
            "interpreter": self.interpreter,
        }


# --------------------------------------------------------------------------
# request parsing
# --------------------------------------------------------------------------

def requested_backend(explicit=None, environ=None):
    """Return ``(name, source)``.

    A name that is not a known backend is a configuration error, not a reason
    to fall through to the default: silently ignoring `--backend swog` would
    grade the board with a backend the caller did not ask for.
    """
    environ = os.environ if environ is None else environ
    if explicit is not None:
        name, source = explicit, SOURCE_CLI
    else:
        from_env = environ.get(ENV_VAR)
        if from_env:
            name, source = from_env, SOURCE_ENV
        else:
            return DEFAULT_BACKEND, SOURCE_DEFAULT
    if name not in BACKENDS:
        raise BackendUnavailable(
            name, "backend-unknown",
            "%r is not a known backend (choose from %s); requested via %s"
            % (name, ", ".join(BACKENDS), source),
        )
    return name, source


# --------------------------------------------------------------------------
# swig
# --------------------------------------------------------------------------

def _run(cmd, timeout):
    """Return ``(returncode, stdout+stderr)``; a launch failure is (None, "")."""
    try:
        proc = subprocess.run(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            universal_newlines=True, timeout=timeout,
        )
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None, ""
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def probe_interpreter(interpreter, runner=None):
    """Return the pcbnew build version this interpreter reports, else None.

    Requires exit 0 AND stdout beginning with the exact marker. The marker is
    built at runtime, so an executable that echoes its arguments (`/bin/echo`)
    fails the match and one that ignores them (`/usr/bin/true`) prints nothing.
    """
    runner = _run if runner is None else runner
    code, output = runner([interpreter, "-c", _PROBE_SOURCE], _PROBE_TIMEOUT_S)
    if code != 0:
        return None
    text = (output or "").strip()
    if not text.startswith(_PROBE_MARKER):
        return None
    return text[len(_PROBE_MARKER):].strip() or "unknown"


def candidate_interpreters():
    """Bundled-KiCad interpreters, newest first, then the ordinary ones."""
    patterns = [
        "/Applications/KiCad/KiCad*.app/Contents/Frameworks/Python.framework/"
        "Versions/Current/bin/python3",
        "/usr/lib/kicad*/bin/python3",
    ]
    if platform.system() == "Windows":
        for key in ("ProgramFiles", "ProgramFiles(x86)"):
            base = os.environ.get(key)
            if base:
                patterns.append(os.path.join(base, "KiCad", "*", "bin", "python.exe"))
    found = []
    for pattern in patterns:
        found.extend(sorted(glob.glob(pattern), reverse=True))
    found.extend(["/usr/bin/python3", "/usr/local/bin/python3"])
    return found


def probe_swig(environ=None, runner=None, importer=None):
    """Resolve the SWIG backend, in-process if possible.

    A configured ``KICAD_PYTHON`` that fails the probe is an error, never a
    fallthrough to discovery — a misconfigured CI would otherwise grade with a
    different KiCad than the one it pinned.
    """
    environ = os.environ if environ is None else environ
    importer = _import_pcbnew if importer is None else importer

    version = importer()
    if version is not None:
        return Selection(
            SWIG, None,
            "pcbnew %s in-process under %s" % (version, sys.executable),
            SWIG_CAPABILITIES, interpreter=None,
        )

    configured = environ.get(INTERPRETER_ENV_VAR)
    if configured:
        version = probe_interpreter(configured, runner)
        if version is None:
            raise BackendUnavailable(
                SWIG, "swig-configured-interpreter-bad",
                "%s=%s cannot import pcbnew (the probe requires 'import pcbnew' "
                "to succeed)" % (INTERPRETER_ENV_VAR, configured),
            )
        return Selection(
            SWIG, None, "pcbnew %s via %s=%s" % (version, INTERPRETER_ENV_VAR, configured),
            SWIG_CAPABILITIES, interpreter=configured,
        )

    for candidate in candidate_interpreters():
        if not os.path.isfile(candidate):
            continue
        version = probe_interpreter(candidate, runner)
        if version is not None:
            return Selection(
                SWIG, None, "pcbnew %s via %s" % (version, candidate),
                SWIG_CAPABILITIES, interpreter=candidate,
            )
    raise BackendUnavailable(
        SWIG, "swig-no-interpreter",
        "no interpreter with pcbnew found; set %s" % INTERPRETER_ENV_VAR,
    )


def _import_pcbnew():
    """Return the in-process pcbnew build version, or None when unavailable.

    Catches every exception, not only ImportError: a broken native extension
    raises more than that, and an unusable pcbnew is not a usable one.
    """
    try:
        import pcbnew
    except Exception:
        return None
    try:
        return str(pcbnew.GetBuildVersion())
    except Exception:
        return "version-unreported"


# --------------------------------------------------------------------------
# ipc
# --------------------------------------------------------------------------

def find_kicad_cli(environ=None):
    """Locate a `kicad-cli`, or None."""
    environ = os.environ if environ is None else environ
    configured = environ.get("KICAD_CLI")
    if configured:
        return configured if os.path.isfile(configured) else None
    patterns = [
        "/Applications/KiCad/KiCad*.app/Contents/MacOS/kicad-cli",
        "/usr/bin/kicad-cli",
        "/usr/local/bin/kicad-cli",
    ]
    for pattern in patterns:
        for hit in sorted(glob.glob(pattern), reverse=True):
            if os.path.isfile(hit):
                return hit
    import shutil
    return shutil.which("kicad-cli")


def cli_serves_api(kicad_cli, runner=None):
    """True when this `kicad-cli` advertises the headless `api-server` mode.

    Read off the subcommand list in `--help`, which is side-effect free.
    Measured on KiCad 10.0.5/Darwin: the list is
    `{fp,jobset,pcb,sch,sym,version}` and `api-server` is rejected with
    "Failed to parse 'api-server'".
    """
    runner = _run if runner is None else runner
    code, output = runner([kicad_cli, "--help"], _CLI_PROBE_TIMEOUT_S)
    if code is None:
        return False
    return re.search(r"\bapi-server\b", output or "") is not None


def kipy_opens_documents(module=None):
    """True when the installed `kipy` can open a document by path.

    kicad-python 0.8.0 has `KiCad.get_open_documents` and `KiCad.get_board`
    only; `get_board`'s own docstring is "Retrieves a reference to the PCB open
    in KiCad, if one exists". `open_document` arrives with the KiCad 11
    headless work.
    """
    if module is None:
        try:
            import kipy
            module = kipy
        except Exception:
            return False
    return hasattr(getattr(module, "KiCad", object), "open_document")


def _import_kipy():
    import kipy
    return kipy


def probe_ipc(environ=None, runner=None, kipy_module=None, importer=None):
    """Always raises today. Each rung names the concrete thing that is missing.

    Ordered cheapest first, and stopping at the first real obstacle, so the
    reason a caller gets is the one they can act on.
    """
    environ = os.environ if environ is None else environ
    importer = _import_kipy if importer is None else importer

    if kipy_module is None:
        try:
            kipy_module = importer()
        except Exception as exc:
            raise BackendUnavailable(
                IPC, "ipc-client-missing",
                "kicad-python (kipy) is not importable in %s: %s"
                % (sys.executable, exc),
            )

    kicad_cli = find_kicad_cli(environ)
    if kicad_cli is None:
        raise BackendUnavailable(
            IPC, "ipc-no-kicad-cli",
            "no kicad-cli found to probe for headless API serving; set KICAD_CLI",
        )
    if not cli_serves_api(kicad_cli, runner):
        raise BackendUnavailable(
            IPC, "ipc-no-headless-server",
            "%s has no 'api-server' subcommand, so the IPC API here is served "
            "only by a running KiCad GUI with Preferences > Plugins > Enable "
            "KiCad API switched on. Headless serving arrives with KiCad 11 "
            "(upstream caf1bcc4559b, 2026-03-21)." % kicad_cli,
        )
    if not kipy_opens_documents(kipy_module):
        raise BackendUnavailable(
            IPC, "ipc-no-open-document",
            "this kicad-python has no KiCad.open_document, so it can only "
            "address a board already open in the session; there is no "
            "equivalent of pcbnew.LoadBoard(path)",
        )
    raise BackendUnavailable(
        IPC, "ipc-backend-not-implemented",
        "this host could serve the IPC API, but this repository ships no IPC "
        "implementation: none could be verified against a live server when the "
        "port was written. Implement and calibrate it before selecting it.",
    )


# --------------------------------------------------------------------------
# selection
# --------------------------------------------------------------------------

def select(explicit=None, environ=None, probes=None):
    """Resolve the requested backend or raise :class:`BackendUnavailable`."""
    environ = os.environ if environ is None else environ
    name, source = requested_backend(explicit, environ)
    probes = {SWIG: probe_swig, IPC: probe_ipc} if probes is None else probes
    probe = probes.get(name)
    if probe is None:
        raise BackendUnavailable(
            name, "backend-unknown", "no probe is registered for %r" % name
        )
    selection = probe(environ=environ)
    # The probe reports what it found; the caller's request decides the source.
    selection.name = name
    selection.source = source
    return selection


def require_capabilities(selection, *capabilities):
    """Refuse a backend that cannot do what the caller needs."""
    missing = [c for c in capabilities if c not in selection.capabilities]
    if missing:
        raise BackendUnavailable(
            selection.name, "backend-missing-capability",
            "backend %s cannot %s" % (selection.name, ", ".join(sorted(missing))),
        )


def add_backend_argument(parser):
    """Add the shared `--backend` option to an argparse parser."""
    parser.add_argument(
        "--backend", choices=list(BACKENDS), default=None,
        help="board-access backend (default: %s, or $%s). A named backend that "
             "is unavailable is an error; there is no fallback."
             % (DEFAULT_BACKEND, ENV_VAR),
    )
    return parser


if __name__ == "__main__":
    # Diagnostic: report what each backend would do on this host.
    for backend in BACKENDS:
        try:
            print(select(backend).provenance())
        except BackendUnavailable as error:
            print("backend=%s UNAVAILABLE %s" % (backend, error))
