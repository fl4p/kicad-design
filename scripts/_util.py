"""Shared helpers for the kicad-design verification scripts.

Two helpers are duplicated across :mod:`kicad_netlist`, :mod:`kicad_symlib`
and :mod:`kicad_verify`.  They live here so a fix in one place propagates
to every consumer.  Nothing in this module knows about a particular board
or file format; each function takes an exception class to raise so the
caller controls the error type.
"""

from __future__ import annotations

import pathlib


def read_utf8(path, err_cls):
    """Read a KiCad text file as UTF-8, STRICTLY.

    KiCad writes UTF-8 on every platform.  ``Path.read_text()`` without an
    encoding uses ``locale.getpreferredencoding()``, which on a typical
    Windows host is cp1252 -- so ``10 uF +-10%`` written as UTF-8 comes back
    as mojibake (``10 AuF A+-10%``), and ``errors="replace"`` guarantees
    that happens SILENTLY.  A guard comparing such a value then mismatches
    for a reason nothing reports.  Decode strictly and raise: undecodable
    input is unreadable input, not input that happens to contain
    replacement characters.
    """
    try:
        return pathlib.Path(path).read_text(encoding="utf-8")
    except UnicodeDecodeError as e:
        raise err_cls(
            "%s is not valid UTF-8 at byte %d (%s). KiCad writes UTF-8; a file "
            "that does not decode is unreadable, not partially readable."
            % (path, e.start, e.reason))
    except OSError as e:
        raise err_cls("cannot read %s: %s" % (path, e))


def mask_strings(text):
    """Same-length copy of *text* with the *contents* of quoted literals
    blanked to spaces.

    Opener scans must run on this, not on the raw text.  The paren walkers
    already track string state, but the ``finditer`` that locates candidate
    openers did not -- so a value such as ::

        (value "Exposed pad is FLOATING (net TPAD), not ground")

    contributed a phantom ``(net `` opener, and two real project netlists
    failed to parse with "net block <no code> has no (name ...)".  Offsets
    are preserved, so a match position in the mask indexes the original.
    """
    out = []
    in_str = esc = False
    for c in text:
        if in_str:
            if esc:
                esc = False
                out.append(" ")
            elif c == "\\":
                esc = True
                out.append(" ")
            elif c == '"':
                in_str = False
                out.append(c)
            else:
                out.append(" " if c not in "\r\n" else c)
        else:
            out.append(c)
            if c == '"':
                in_str = True
    return "".join(out)
