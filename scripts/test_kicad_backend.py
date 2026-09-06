"""Calibration for the backend-selection layer.

Every case is named as the CLAIM the module makes about itself, per
`../GUARDS.md`: the negative claims come first, because ordinary use never
visits them. The claims under test are: selection is explicit and its source is
recorded; an unavailable backend never becomes the other one; a configured
interpreter that fails its probe is an error rather than a fallthrough; and the
`ipc` backend refuses with the first concrete reason it finds, including when
the host would in principle support it.
"""

import unittest

import kicad_backend as backend


class FakeKiCad(object):
    pass


class FakeKiCadWithOpen(object):
    def open_document(self, path):  # pragma: no cover - presence is the point
        raise NotImplementedError


class FakeKipy(object):
    def __init__(self, kicad_cls):
        self.KiCad = kicad_cls


def runner_for(mapping, default=(1, "")):
    """Build a `_run`-shaped fake from {first-arg-substring: (code, output)}."""
    def run(cmd, timeout):
        for needle, result in mapping.items():
            if needle in " ".join(cmd):
                return result
        return default
    return run


class RequestedBackend(unittest.TestCase):
    def test_no_request_selects_the_default_and_says_the_source_was_default(self):
        self.assertEqual(
            backend.requested_backend(None, {}),
            (backend.DEFAULT_BACKEND, backend.SOURCE_DEFAULT),
        )

    def test_environment_selects_and_is_recorded_as_the_source(self):
        self.assertEqual(
            backend.requested_backend(None, {backend.ENV_VAR: "ipc"}),
            ("ipc", backend.SOURCE_ENV),
        )

    def test_an_explicit_request_overrides_the_environment(self):
        self.assertEqual(
            backend.requested_backend("swig", {backend.ENV_VAR: "ipc"}),
            ("swig", backend.SOURCE_CLI),
        )

    def test_an_empty_environment_value_is_not_a_request(self):
        self.assertEqual(
            backend.requested_backend(None, {backend.ENV_VAR: ""}),
            (backend.DEFAULT_BACKEND, backend.SOURCE_DEFAULT),
        )

    def test_an_unknown_name_is_an_error_not_a_fallthrough_to_the_default(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.requested_backend(None, {backend.ENV_VAR: "swog"})
        self.assertEqual(caught.exception.failure_id, "backend-unknown")
        self.assertIn("env", caught.exception.reason)


class NoFallback(unittest.TestCase):
    def test_an_unavailable_backend_never_becomes_the_other_one(self):
        def refuse(environ=None):
            raise backend.BackendUnavailable("ipc", "ipc-test", "nope")

        called = []

        def swig(environ=None):
            called.append(True)
            return backend.Selection("swig", None, "d", backend.SWIG_CAPABILITIES)

        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.select("ipc", {}, probes={"swig": swig, "ipc": refuse})
        self.assertEqual(caught.exception.failure_id, "ipc-test")
        self.assertEqual(called, [], "the other backend's probe must not run")

    def test_an_unknown_name_never_reaches_a_probe(self):
        def explode(environ=None):  # pragma: no cover - must not run
            raise AssertionError("probe ran for an unknown backend")

        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.select(None, {backend.ENV_VAR: "swog"},
                           probes={"swig": explode, "ipc": explode})
        self.assertEqual(caught.exception.failure_id, "backend-unknown")

    def test_the_selection_carries_the_request_source_not_the_probe_s(self):
        def swig(environ=None):
            return backend.Selection("swig", "invented-by-the-probe", "d",
                                     backend.SWIG_CAPABILITIES)

        selection = backend.select("swig", {}, probes={"swig": swig})
        self.assertEqual(selection.source, backend.SOURCE_CLI)
        self.assertIn("source=cli", selection.provenance())


class InterpreterProbe(unittest.TestCase):
    def test_an_argument_echoing_executable_is_rejected(self):
        # /bin/echo prints the probe source, which contains the marker pieces.
        def echo(cmd, timeout):
            return 0, " ".join(cmd[1:])

        self.assertIsNone(backend.probe_interpreter("/bin/echo", echo))

    def test_exit_zero_with_no_output_is_rejected(self):
        self.assertIsNone(
            backend.probe_interpreter("/usr/bin/true", lambda c, t: (0, "")))

    def test_a_nonzero_exit_is_rejected_even_with_the_marker(self):
        self.assertIsNone(
            backend.probe_interpreter(
                "/x/python3", lambda c, t: (1, backend._PROBE_MARKER + " 10.0.5")))

    def test_a_failed_launch_is_rejected(self):
        self.assertIsNone(
            backend.probe_interpreter("/x/python3", lambda c, t: (None, "")))

    def test_the_marker_plus_a_version_is_accepted_and_the_version_returned(self):
        self.assertEqual(
            backend.probe_interpreter(
                "/x/python3", lambda c, t: (0, backend._PROBE_MARKER + " 10.0.5\n")),
            "10.0.5")

    def test_a_bare_marker_is_accepted_as_an_unknown_version(self):
        self.assertEqual(
            backend.probe_interpreter(
                "/x/python3", lambda c, t: (0, backend._PROBE_MARKER)),
            "unknown")


class SwigProbe(unittest.TestCase):
    def test_a_configured_interpreter_that_fails_the_probe_is_an_error(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_swig(
                environ={backend.INTERPRETER_ENV_VAR: "/usr/bin/true"},
                runner=lambda c, t: (0, ""),
                importer=lambda: None,
            )
        self.assertEqual(caught.exception.failure_id,
                         "swig-configured-interpreter-bad")

    def test_a_configured_interpreter_is_not_silently_replaced_by_discovery(self):
        seen = []

        def runner(cmd, timeout):
            seen.append(cmd[0])
            return 1, ""

        with self.assertRaises(backend.BackendUnavailable):
            backend.probe_swig(
                environ={backend.INTERPRETER_ENV_VAR: "/configured/python3"},
                runner=runner, importer=lambda: None)
        self.assertEqual(seen, ["/configured/python3"])

    def test_no_usable_interpreter_anywhere_is_unevaluable(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_swig(environ={}, runner=lambda c, t: (1, ""),
                               importer=lambda: None)
        self.assertEqual(caught.exception.failure_id, "swig-no-interpreter")

    def test_an_in_process_pcbnew_needs_no_interpreter(self):
        selection = backend.probe_swig(
            environ={}, runner=lambda c, t: (1, ""), importer=lambda: "10.0.5")
        self.assertIsNone(selection.interpreter)
        self.assertIn("10.0.5", selection.detail)
        self.assertIn(backend.CAP_EFFECTIVE_SHAPE_COLLIDE, selection.capabilities)


class IpcProbe(unittest.TestCase):
    """The ipc backend refuses today. Each case pins WHICH reason it gives."""

    def test_a_missing_client_is_named_as_such(self):
        def no_kipy():
            raise ImportError("No module named 'kipy'")

        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_ipc(environ={"KICAD_CLI": "/nope"}, importer=no_kipy)
        self.assertEqual(caught.exception.failure_id, "ipc-client-missing")

    def test_no_kicad_cli_to_probe_is_named_as_such(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_ipc(environ={"KICAD_CLI": "/definitely/not/here"},
                              kipy_module=FakeKipy(FakeKiCad))
        self.assertEqual(caught.exception.failure_id, "ipc-no-kicad-cli")

    def test_a_kicad_cli_without_api_server_means_no_headless_serving(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_ipc(
                environ={"KICAD_CLI": __file__},
                runner=runner_for({"--help": (0, "{fp,jobset,pcb,sch,sym,version}")}),
                kipy_module=FakeKipy(FakeKiCad))
        self.assertEqual(caught.exception.failure_id, "ipc-no-headless-server")

    def test_a_client_that_cannot_open_a_document_is_named_as_such(self):
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_ipc(
                environ={"KICAD_CLI": __file__},
                runner=runner_for({"--help": (0, "{api-server,pcb,version}")}),
                kipy_module=FakeKipy(FakeKiCad))
        self.assertEqual(caught.exception.failure_id, "ipc-no-open-document")

    def test_a_host_that_could_serve_ipc_still_gets_an_honest_gap(self):
        # The point of this case: satisfying every precondition must NOT
        # produce a working backend, because none was implemented or verified.
        # If someone implements it, this test is the one that must change.
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.probe_ipc(
                environ={"KICAD_CLI": __file__},
                runner=runner_for({"--help": (0, "{api-server,pcb,version}")}),
                kipy_module=FakeKipy(FakeKiCadWithOpen))
        self.assertEqual(caught.exception.failure_id,
                         "ipc-backend-not-implemented")

    def test_the_api_server_token_is_matched_whole(self):
        # "no-api-servers-here" in help text must not read as support.
        self.assertFalse(backend.cli_serves_api(
            "/x/kicad-cli", runner_for({"--help": (0, "napi-serverx")})))
        self.assertTrue(backend.cli_serves_api(
            "/x/kicad-cli", runner_for({"--help": (0, "{api-server,pcb}")})))

    def test_an_unlaunchable_cli_does_not_read_as_api_support(self):
        self.assertFalse(
            backend.cli_serves_api("/x/kicad-cli", lambda c, t: (None, "")))


class Capabilities(unittest.TestCase):
    def test_a_missing_capability_is_refused_not_ignored(self):
        selection = backend.Selection("swig", "cli", "d", frozenset(("a",)))
        with self.assertRaises(backend.BackendUnavailable) as caught:
            backend.require_capabilities(selection, "a", "b")
        self.assertEqual(caught.exception.failure_id,
                         "backend-missing-capability")
        self.assertIn("b", caught.exception.reason)

    def test_present_capabilities_pass_quietly(self):
        selection = backend.Selection("swig", "cli", "d", frozenset(("a", "b")))
        self.assertIsNone(backend.require_capabilities(selection, "a", "b"))

    def test_the_report_dict_names_the_backend_and_its_capabilities(self):
        selection = backend.Selection("swig", "cli", "pcbnew 10.0.5",
                                      backend.SWIG_CAPABILITIES)
        payload = selection.as_dict()
        self.assertEqual(payload["name"], "swig")
        self.assertEqual(payload["source"], "cli")
        self.assertEqual(sorted(backend.SWIG_CAPABILITIES),
                         payload["capabilities"])
        self.assertIn("pcbnew 10.0.5", selection.provenance())


if __name__ == "__main__":
    unittest.main()
