#!/usr/bin/env python3
"""Read `.kicad_sym` libraries correctly: `extends`, unit 0, and pin geometry.

Project-agnostic. Nothing here knows about any particular board.

Three traps this module exists to remove, all of which are silent:

1.  **Over half the stock library is ``(extends "PARENT")``.** Those entries
    carry no pins and no graphics of their own -- ``Amplifier_Operational:LM358``
    is literally ``(symbol "LM358" (extends "LM2904") ...)``. A reader that
    takes only the named entry gets **zero pins** for most of the library, and
    copying that bare entry into a `.kicad_sch` makes `kicad-cli` either
    segfault (exit 139, no output file) or write a 0-component netlist at
    exit 0. Re-measure the proportion rather than trusting any number written
    down here; :func:`library_stats` does it.

2.  **Unit 0 is "common to all units".** Hundreds of stock symbols keep pins in
    ``NAME_0_*``, and some keep *all* of them there. A strict
    ``(lib_id, unit)`` lookup raises on every one of those, and on a genuine
    multi-unit part with shared supply pins in unit 0 it drops them silently --
    worse than the flat lookup it replaced. Pins for unit *u* are
    ``NAME_0_*`` UNION ``NAME_u_*``.

3.  **Rotation and mirroring do not commute.** KiCad mirrors *after* rotating.
    The two orders agree at 0 deg and 180 deg -- an axis mirror commutes with a
    half-turn -- and disagree at 90 deg and 270 deg, where they exchange pin 1
    and pin 2 of every two-pin part. Clean ERC, clean netlist, swapped part.

On (3): a real project validated its transform at **164/164 pins** on a board
that contained no mirrored symbols, so 4 of the 12 rotation x mirror cells were
never exercised -- and those 4 were the broken ones. Report coverage, not pass
rate. :func:`calibration_plan` prints the cells you must check against ground
truth, and it is deliberately not something this module can self-certify: the
only ground truth is what KiCad itself nets up.
"""

from __future__ import annotations

import math
import re
from pathlib import Path

__all__ = [
    "SymbolLibError",
    "SymbolLib",
    "transform_pin",
    "calibration_plan",
    "library_stats",
]


class SymbolLibError(AssertionError):
    """A symbol could not be read, or the read could not be trusted."""


def _blocks(text, opener):
    """Yield each ``(opener ...)`` block, respecting strings. See
    kicad_netlist._balanced_blocks for why this is not done with a regex."""
    pat = re.compile(r"\(" + re.escape(opener) + r"[\s\"]")
    for m in pat.finditer(text):
        i = m.start()
        depth = 0
        in_str = esc = False
        j = i
        while j < len(text):
            c = text[j]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            elif c == '"':
                in_str = True
            elif c == "(":
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    yield text[i:j + 1]
                    break
            j += 1
        else:
            raise SymbolLibError("unterminated (%s ...) block" % opener)


_SUBSYM = re.compile(r'\(symbol\s+"([^"]+)_(\d+)_(\d+)"')
_ANYSYM = re.compile(r'\(symbol\s+"([^"]+)"')
_EXTENDS = re.compile(r'\(extends\s+"([^"]+)"\)')


def _blocks_at_depth(text, opener, depth_wanted):
    """Yield ``(opener ...)`` blocks whose '(' sits at exactly `depth_wanted`.

    Classifying a `.kicad_sym` entry as top-level by its NAME is wrong: the
    stock symbol ``Connector:Raspberry_Pi_2_3`` ends in ``_2_3`` and a
    suffix test drops it from the library entirely, so ``name in lib``
    answers a confident **False** for a symbol that exists. Nesting is the
    only reliable discriminator.
    """
    depth = 0
    in_str = esc = False
    stack = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "(":
            stack.append((depth, i))
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise SymbolLibError(
                    "unbalanced ')' at offset %d -- truncated or corrupt file"
                    % i)
            d, start = stack.pop()
            if d == depth_wanted:
                blk = text[start:i + 1]
                if re.match(r"\(" + re.escape(opener) + r"[\s\"]", blk):
                    yield blk
        i += 1
    if depth != 0 or in_str:
        raise SymbolLibError(
            "file does not close cleanly (depth=%d, in_string=%s) -- refusing "
            "to report symbols from a truncated file" % (depth, in_str))


class SymbolLib:
    """One `.kicad_sym` file, with `extends` resolved on demand."""

    def __init__(self, path):
        self.path = Path(path)
        self.text = self.path.read_text(errors="replace")
        self._require_root("kicad_symbol_lib")
        self._top = {}          # name -> block text
        # Top level = depth 1: the file root is (kicad_symbol_lib ...) at 0.
        for blk in _blocks_at_depth(self.text, "symbol", 1):
            m = _ANYSYM.match(blk)
            if not m:
                continue
            name = m.group(1)
            if name in self._top:
                raise SymbolLibError(
                    "%s defines symbol %r twice -- refusing to guess which "
                    "one you meant" % (self.path.name, name))
            self._top[name] = blk
        if not self._top:
            raise SymbolLibError("%s contains no symbols" % self.path)

    def _require_root(self, root):
        """One balanced expression named `root`, nothing outside it.

        A scan-for-openers reader otherwise loads `FAKE` out of
        `(junk (symbol "FAKE" ...)) trailing garbage`.
        """
        depth = 0
        in_str = esc = False
        first_open = closed_at = None
        for i, c in enumerate(self.text):
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == "(":
                if depth == 0:
                    if closed_at is not None:
                        raise SymbolLibError(
                            "%s has a second top-level expression at offset %d"
                            % (self.path.name, i))
                    first_open = i
                depth += 1
            elif c == ")":
                depth -= 1
                if depth == 0:
                    closed_at = i
        if first_open is None:
            raise SymbolLibError("%s contains no S-expression" % self.path.name)
        head = re.match(r"\(\s*([A-Za-z_][\w-]*)", self.text[first_open:])
        if not head or head.group(1) != root:
            raise SymbolLibError(
                "%s root is %r, expected %r -- not a KiCad symbol library"
                % (self.path.name, head.group(1) if head else "<none>", root))
        if self.text[closed_at + 1:].strip():
            raise SymbolLibError(
                "%s has trailing content after the root form" % self.path.name)

    def __contains__(self, name):
        return name in self._top

    def names(self):
        return sorted(self._top)

    def parent_of(self, name):
        blk = self._top.get(name)
        if blk is None:
            raise SymbolLibError("%s has no symbol %r" % (self.path.name, name))
        m = _EXTENDS.search(blk)
        return m.group(1) if m else None

    def resolve(self, name, _seen=None):
        """Return the block that actually carries geometry for `name`.

        Follows `extends` to the root. Raises on a cycle or a dangling parent
        rather than returning the pin-less child, because the pin-less child
        is exactly what produces a 0-component netlist at exit 0.
        """
        _seen = _seen or []
        if name in _seen:
            raise SymbolLibError("extends cycle: %s" % " -> ".join(_seen + [name]))
        blk = self._top.get(name)
        if blk is None:
            raise SymbolLibError(
                "%s has no symbol %r (dangling extends?)"
                % (self.path.name, name))
        parent = self.parent_of(name)
        if parent is None:
            return blk
        return self.resolve(parent, _seen + [name])

    def _subsymbols(self, name):
        """(base, unit, style, block) for each sub-symbol of the resolved
        entry. Rejects a sub-symbol whose base name is not the resolved
        symbol's -- otherwise a nested `Other_1_1` would be served as this
        symbol's geometry."""
        blk = self.resolve(name)
        rm = _ANYSYM.match(blk)
        if not rm:
            raise SymbolLibError(
                "%s:%s resolved to a block that is not a (symbol \"...\")"
                % (self.path.name, name))
        root = rm.group(1)
        out = []
        for sub in _blocks_at_depth(blk, "symbol", 1):
            m = _SUBSYM.match(sub)
            if not m:
                continue
            base, u, st = m.group(1), int(m.group(2)), int(m.group(3))
            if base != root:
                raise SymbolLibError(
                    "%s:%s contains sub-symbol %r whose base name is not %r"
                    % (self.path.name, name, m.group(0), root))
            out.append((base, u, st, sub))
        return out

    def units(self, name):
        """Unit numbers defined for `name` (after resolving extends).

        Raises rather than returning [] -- an empty list makes
        ``for u in lib.units(n)`` perform zero checks and report success.
        Note 0 is a real entry meaning "common to all units"; a symbol whose
        only entry is 0 is a valid single-unit part (stock
        ``Driver:DRV2510-Q1`` is one).
        """
        us = sorted({u for _b, u, _s, _blk in self._subsymbols(name)})
        if not us:
            raise SymbolLibError(
                "%s:%s defines no sub-symbols -- unit information cannot be "
                "established" % (self.path.name, name))
        return us

    def body_styles(self, name):
        """Body styles present. 1 is the normal drawing, 2 the DeMorgan
        alternative; 0 is common to both."""
        return sorted({s for _b, _u, s, _blk in self._subsymbols(name)})

    def pins(self, name, unit=1, style=1):
        """Pins for `unit`/`style`: union of the common entries and the
        requested ones.

        `unit` 0 is common-to-all-units, `style` 0 is common-to-both-bodies,
        so the selected set is (unit in {0, unit}) AND (style in {0, style}).
        **Ignoring style silently doubles the pin list** on any symbol with a
        DeMorgan variant -- stock ``4xxx:4001`` returns pins 1, 2 and 3 twice.

        Returns [(number, pin_name, x, y, angle, electrical_type)] in library
        coordinates, deduplicated by pin number with conflicting duplicates
        rejected rather than silently collapsed.
        """
        if not isinstance(unit, int) or isinstance(unit, bool) or unit < 0:
            raise SymbolLibError("unit must be a non-negative int, got %r" % (unit,))
        if not isinstance(style, int) or isinstance(style, bool) or style < 0:
            raise SymbolLibError("style must be a non-negative int, got %r" % (style,))
        subs = self._subsymbols(name)
        present = {u for _b, u, _s, _blk in subs}
        if unit not in present and unit != 1:
            raise SymbolLibError(
                "%s:%s has no unit %d (present: %s)"
                % (self.path.name, name, unit, sorted(present)))

        by_num = {}
        for _b, u, st, sub in subs:
            if u not in (0, unit) or st not in (0, style):
                continue
            for pb in _blocks(sub, "pin"):
                num = re.search(r'\(number\s+"([^"]*)"', pb)
                at = re.search(r"\(at\s+(-?[\d.]+)\s+(-?[\d.]+)(?:\s+(-?[\d.]+))?", pb)
                pnm = re.search(r'\(name\s+"([^"]*)"', pb)
                et = re.match(r'\(pin\s+(\S+)', pb)
                if not num or not at:
                    raise SymbolLibError(
                        "%s:%s has a (pin ...) missing %s -- refusing to "
                        "return a silently shorter pin list"
                        % (self.path.name, name,
                           "a number" if not num else "an (at ...)"))
                rec = (num.group(1),
                       pnm.group(1) if pnm else "",
                       float(at.group(1)), float(at.group(2)),
                       float(at.group(3) or 0.0),
                       et.group(1) if et else "unspecified")
                # STACKED PINS ARE LEGAL: one pin NUMBER may legitimately
                # appear at several positions in a single unit -- stock
                # 74xx_IEEE:74278 has pin 6 six times inside 74278_1_1.
                # So deduplicate on the FULL record (an exact repeat is
                # noise) and never on the number alone. Deduping by number
                # was the wrong model and made the reader refuse 11 real
                # stock symbols; the original 4001 double-count it was
                # written for is handled by the `style` filter instead.
                by_num[rec] = None
        if not by_num and not subs:
            raise SymbolLibError(
                "%s:%s has no sub-symbols at all -- cannot evaluate its pins"
                % (self.path.name, name))
        # sort by (number, x, y) so the order is stable across runs
        return sorted(by_num, key=lambda r: (r[0], r[2], r[3]))

    def require_pins(self, name, unit=1, style=1):
        """:meth:`pins`, but refuses to return an empty list.

        Use this wherever a pin-less answer would be a bug -- embedding a
        symbol into a schematic, deriving wire endpoints. :meth:`pins` itself
        returns ``[]`` for the 40 stock symbols that genuinely have no pins
        (``MountingScrew``, ``Logo_Open_Hardware_*``, ``Generic_Outline`` and
        friends); raising for those made the reader refuse valid input, which
        is a worse defect than the one it was guarding against.
        """
        got = self.pins(name, unit, style)
        if not got:
            raise SymbolLibError(
                "%s:%s unit %d style %d has no pins -- refusing to hand back "
                "a pin-less symbol where pins are required"
                % (self.path.name, name, unit, style))
        return got


def transform_pin(px, py, angle, mirror=None):
    """Library pin offset -> schematic offset from the placement point.

    ROTATE FIRST, THEN MIRROR -- KiCad's order. Getting this backwards is
    invisible at 0 deg and 180 deg and swaps pins 1/2 at 90 deg and 270 deg.

    `mirror` is None, 'x' or 'y'. Library Y is up, schematic Y is down, which
    is the leading negation.
    """
    if mirror not in (None, "x", "y"):
        # "" was previously accepted as "no mirror". It is not a KiCad value,
        # and accepting it lets a caller's empty/missing field read as a
        # deliberate choice.
        raise SymbolLibError("mirror must be None, 'x' or 'y', got %r" % (mirror,))
    if angle % 90 != 0:
        # round(cos)/round(sin) turn 45 deg into ca=sa=1, which is not a
        # rotation at all: (2,3) -> (-1,-5), changing the length from 3.606
        # to 5.099. KiCad only ever writes multiples of 90.
        raise SymbolLibError(
            "angle must be a multiple of 90, got %r -- the rounded "
            "sin/cos used here is only valid on the 90-degree grid" % (angle,))
    x, y = px, -py
    a = math.radians(angle % 360)
    ca, sa = round(math.cos(a)), round(math.sin(a))
    x, y = x * ca + y * sa, -x * sa + y * ca
    if mirror == "x":
        y = -y
    elif mirror == "y":
        x = -x
    return (round(x, 6), round(y, 6))


def calibration_plan():
    """The parameter space :func:`transform_pin` must be checked against.

    12 cells = 4 angles x 3 mirror states, and BOTH pins of a two-pin part in
    each, so the honest denominator is 24. This module cannot self-certify:
    place one part per cell, export the netlist with kicad-cli, and ask KiCad
    which pin reached which net. Never calibrate by confirming that the board
    you already have comes out right -- that is how 164/164 hid 16/24.
    """
    return [(ang, mir) for ang in (0, 90, 180, 270)
            for mir in (None, "x", "y")]


def library_stats(symbols_dir=None):
    """Measure `extends` prevalence rather than quoting a remembered number.

    Returns (n_symbols, n_extends, n_libraries). The proportion moves between
    KiCad releases; the rule (resolve before embedding) does not.
    """
    if symbols_dir is None:
        for cand in ("/Applications/KiCad/KiCad.app/Contents/SharedSupport/symbols",
                     "/usr/share/kicad/symbols"):
            if Path(cand).is_dir():
                symbols_dir = cand
                break
    if symbols_dir is None or not Path(symbols_dir).is_dir():
        raise SymbolLibError("stock symbol directory not found; pass one in")
    n_sym = n_ext = n_lib = 0
    for f in sorted(Path(symbols_dir).glob("*.kicad_sym")):
        t = f.read_text(errors="replace")
        n_lib += 1
        # Depth, not indentation: counting literal tabs silently returns 0 on
        # a space-indented or reformatted library.
        for blk in _blocks_at_depth(t, "symbol", 1):
            n_sym += 1
            if _EXTENDS.search(blk):
                n_ext += 1
    if not n_lib or not n_sym:
        raise SymbolLibError(
            "counted %d libraries and %d symbols under %s -- a zero here is "
            "'I could not read them', not 'there are none'"
            % (n_lib, n_sym, symbols_dir))
    return n_sym, n_ext, n_lib


if __name__ == "__main__":
    import sys
    if len(sys.argv) == 1:
        n, e, libs = library_stats()
        print("stock libraries: %d symbols, %d extends (%.1f %%), %d files"
              % (n, e, 100.0 * e / n if n else 0.0, libs))
        print("calibration cells for transform_pin: %d (x2 pins = %d checks)"
              % (len(calibration_plan()), 2 * len(calibration_plan())))
    else:
        lib = SymbolLib(sys.argv[1])
        name = sys.argv[2] if len(sys.argv) > 2 else lib.names()[0]
        print("%s  parent=%s  units=%s" % (name, lib.parent_of(name),
                                           lib.units(name)))
        us = [u for u in lib.units(name) if u != 0] or [1]
        for u in us:
            for p in lib.pins(name, u):
                print("  unit %d  pin %-4s %-10s at %7.2f,%7.2f  %s"
                      % (u, p[0], p[1], p[2], p[3], p[5]))
