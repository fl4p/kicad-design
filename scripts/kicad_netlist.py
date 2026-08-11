#!/usr/bin/env python3
"""Parse a KiCad netlist export, and ASSERT THAT THE PARSE WORKED.

Project-agnostic. Nothing here knows about any particular board.

Why this exists
---------------
`kicad-cli sch export netlist` changed shape between major versions. KiCad 9
wrote it compactly::

    (net (code "1") (name "X")
      (node (ref "U1") (pin "3"))

KiCad 10.0.5 pretty-prints **every token onto its own line**. Any regex needing
a literal space after ``(net``, or ``(node (ref ...) (pin ...))`` on one line,
matches *nothing* against a 10.x export -- and a parser that returns an empty
dict looks exactly like "the netlist is fine, nothing matched my expectations".

Two independent parsers in one project broke this way in the same hour, and the
failure was loud only by luck: the expectations table happened to be non-empty.
With an empty table it would have reported a clean pass over a file it had
entirely failed to read.

So every entry point here refuses to return a result it cannot justify:

* the number of parsed nets must equal the number of ``(net`` openers,
* every net must have at least one node,
* the component count must be > 0,

and each of those raises rather than returning an empty container.

Count the openers with ``re`` over the whole file, never with a line-based
tool: the whitespace after ``(net`` is a *newline* in 10.x, so
``grep -cE '\\(net\\s'`` returns 0 on a file where ``re.findall`` returns 51.
"""

from __future__ import annotations

import re
from pathlib import Path

__all__ = [
    "NetlistError",
    "parse_netlist",
    "Netlist",
]


class NetlistError(AssertionError):
    """The netlist could not be parsed, or the parse could not be trusted.

    Deliberately an AssertionError subclass so existing guard harnesses that
    catch AssertionError keep working.
    """


# ``\s`` after the opener, never a bare "(net " -- and not matching "(nets".
_NET_OPEN = re.compile(r"\(net\s")
_COMP_OPEN = re.compile(r"\(comp\s")

_NAME = re.compile(r'\(name\s+"([^"]*)"\)')
_CODE = re.compile(r'\(code\s+"?([0-9]+)"?\)')
_REF = re.compile(r'\(ref\s+"([^"]*)"\)')
_PIN = re.compile(r'\(pin\s+"([^"]*)"\)')
_PINFUNC = re.compile(r'\(pinfunction\s+"([^"]*)"\)')


def _balanced_blocks(text: str, opener: str):
    """Yield each top-level ``(opener ...)`` block as a substring.

    Walks parentheses. Do NOT pair two fields with one DOTALL regex: a
    reviewer once matched a *property's* ``(at ...)`` with a later *pin's*
    ``(number ...)`` and produced two self-consistent, entirely fictional pin
    positions -- which then "proved" a correct filter was shorted.

    String literals are respected so a ``")"`` inside a quoted value cannot
    close a block early.
    """
    pat = re.compile(r"\(" + re.escape(opener) + r"\s")
    for m in pat.finditer(text):
        i = m.start()
        depth = 0
        in_str = False
        esc = False
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
            raise NetlistError(
                "unterminated (%s ...) block at offset %d -- truncated file?"
                % (opener, i))


def _require_single_root(text, root, path):
    """The document must be ONE balanced expression named `root`, with
    nothing outside it.

    Without this, a scan-for-openers parser happily reads a live symbol out of
    `(junk (net ...)) trailing garbage`, and never notices the file is not the
    thing it claims to be.
    """
    depth = 0
    in_str = esc = False
    first_open = None
    closed_at = None
    for i, c in enumerate(text):
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
                    raise NetlistError(
                        "%s has a second top-level expression at offset %d -- "
                        "a netlist is one (%s ...) form" % (path, i, root))
                first_open = i
            depth += 1
        elif c == ")":
            depth -= 1
            if depth < 0:
                raise NetlistError("%s: unbalanced ')' at offset %d" % (path, i))
            if depth == 0:
                closed_at = i
    if depth != 0 or in_str:
        raise NetlistError(
            "%s does not close cleanly (depth=%d, in_string=%s)"
            % (path, depth, in_str))
    if first_open is None:
        raise NetlistError("%s contains no S-expression" % path)
    head = re.match(r"\(\s*([A-Za-z_][\w-]*)", text[first_open:])
    if not head or head.group(1) != root:
        raise NetlistError(
            "%s root is %r, expected %r -- this is not a KiCad netlist export"
            % (path, head.group(1) if head else "<none>", root))
    if text[closed_at + 1:].strip():
        raise NetlistError(
            "%s has %d bytes of trailing content after the root form"
            % (path, len(text[closed_at + 1:].strip())))


class Netlist:
    """A parsed netlist that has proved it understood its input."""

    def __init__(self, path, nets, components):
        self.path = Path(path)
        self.nets = nets                  # {netname: [(ref, pin, pinfunc)]}
        self.components = components      # {ref: {field: value}}

    # -- convenience lookups -------------------------------------------
    def net_of(self, ref, pin):
        """Net carrying (ref, pin), or None. `pin` is compared as a string."""
        pin = str(pin)
        for name, nodes in self.nets.items():
            for r, p, _f in nodes:
                if r == ref and p == pin:
                    return name
        return None

    def refs_on(self, netname):
        """Set of refdeses with at least one pin on `netname`.

        Raises if the net does not exist -- a guard keyed on a net name must
        not silently evaluate to "nothing is connected, therefore fine" when
        somebody renames the net.
        """
        if netname not in self.nets:
            raise NetlistError(
                "no net named %r in %s (renamed?). Present: %s"
                % (netname, self.path.name,
                   ", ".join(sorted(self.nets)[:12]) or "<none>"))
        return {r for r, _p, _f in self.nets[netname]}

    def field(self, ref, name):
        """A component field (Value, Footprint, MPN, ...) or None."""
        return self.components.get(ref, {}).get(name)

    def __repr__(self):
        return "<Netlist %s: %d nets, %d components>" % (
            self.path.name, len(self.nets), len(self.components))


def parse_netlist(path, min_components=1):
    """Parse `path`, or raise NetlistError.

    `min_components` is a floor, not a target: an export that instantiated
    nothing still exits 0 and still writes a plausible-looking file (827
    bytes, ``(nets))``, no ``(comp ...)`` at all). "No nets look wrong" is
    trivially true of a netlist with no nets in it.
    """
    if not isinstance(min_components, int) or isinstance(min_components, bool):
        # float("nan") < 1 is False AND nan >= 1 is False, so a NaN floor
        # slipped past both comparisons and disabled the guard entirely.
        raise NetlistError(
            "min_components must be an int, got %r" % (min_components,))
    if min_components < 1:
        raise NetlistError(
            "min_components=%r disables the only empty-export guard; an "
            "export that instantiated nothing still exits 0 and writes a "
            "plausible file" % (min_components,))
    path = Path(path)
    try:
        text = path.read_text(errors="replace")
    except OSError as e:
        raise NetlistError("cannot read %s: %s" % (path, e))
    if not text.strip():
        raise NetlistError("%s is empty" % path)

    _require_single_root(text, "export", path)

    expect_nets = len(_NET_OPEN.findall(text))
    expect_comps = len(_COMP_OPEN.findall(text))

    # components ------------------------------------------------------
    components = {}
    for blk in _balanced_blocks(text, "comp"):
        m = _REF.search(blk)
        if not m:
            raise NetlistError("a (comp ...) block has no (ref ...)")
        ref = m.group(1)
        fields = {}
        vm = re.search(r'\(value\s+"([^"]*)"\)', blk)
        if vm:
            fields["Value"] = vm.group(1)
        fm = re.search(r'\(footprint\s+"([^"]*)"\)', blk)
        if fm:
            fields["Footprint"] = fm.group(1)
        # KiCad writes custom fields as (field (name "MPN") "value") in some
        # versions and (property (name "MPN") (value "...")) in others.
        for fb in _balanced_blocks(blk, "field"):
            n = _NAME.search(fb)
            v = re.search(r'\)\s*"([^"]*)"\s*\)\s*$', fb)
            if n and v:
                fields[n.group(1)] = v.group(1)
        for pb in _balanced_blocks(blk, "property"):
            n = _NAME.search(pb)
            v = re.search(r'\(value\s+"([^"]*)"\)', pb)
            if n and v:
                fields.setdefault(n.group(1), v.group(1))
        components[ref] = fields

    # nets ------------------------------------------------------------
    nets = {}
    seen_net_blocks = 0
    for blk in _balanced_blocks(text, "net"):
        seen_net_blocks += 1
        nm = _NAME.search(blk)
        if not nm:
            cm = _CODE.search(blk)
            raise NetlistError(
                "net block %s has no (name ...)"
                % ("code " + cm.group(1) if cm else "<no code>"))
        name = nm.group(1)
        nodes = []
        for nb in _balanced_blocks(blk, "node"):
            r = _REF.search(nb)
            p = _PIN.search(nb)
            if not (r and p):
                raise NetlistError(
                    "a (node ...) in net %r lacks ref or pin" % name)
            f = _PINFUNC.search(nb)
            nodes.append((r.group(1), p.group(1), f.group(1) if f else None))
        if not nodes:
            raise NetlistError(
                "net %r has no nodes -- the parser did not understand this "
                "file's shape (KiCad 9 vs 10 layout?)" % name)
        if name in nets:
            raise NetlistError(
                "duplicate net name %r -- the later block would silently "
                "replace the earlier one" % name)
        nets[name] = nodes

    unknown = sorted({r for nodes in nets.values() for r, _p, _f in nodes}
                     - set(components))
    if unknown:
        raise NetlistError(
            "net nodes name component(s) that the file does not define: %s -- "
            "a lookup against them would return a confident answer about a "
            "part that is not there" % ", ".join(unknown[:10]))

    # a pin may sit on exactly one net; two memberships is a contradiction
    # that net_of() would otherwise hide by returning whichever it met first
    owner = {}
    for nname, nodes in nets.items():
        for r, pin, _f in nodes:
            prev = owner.get((r, pin))
            if prev is not None and prev != nname:
                raise NetlistError(
                    "%s.%s appears on both %r and %r -- a pin cannot be on "
                    "two nets; net_of() would silently report only the first"
                    % (r, pin, prev, nname))
            owner[(r, pin)] = nname

    # -- the parse must account for its own input ----------------------
    if seen_net_blocks != expect_nets:
        raise NetlistError(
            "parsed %d net blocks but the file contains %d '(net' openers -- "
            "the parser did not understand this file" % (seen_net_blocks,
                                                         expect_nets))
    if len(components) != expect_comps:
        raise NetlistError(
            "parsed %d components but the file contains %d '(comp' openers"
            % (len(components), expect_comps))
    if len(components) < min_components:
        raise NetlistError(
            "%s has %d components (floor %d) -- an export that instantiated "
            "nothing still exits 0 and writes a plausible file"
            % (path.name, len(components), min_components))

    return Netlist(path, nets, components)


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 2:
        raise SystemExit("usage: kicad_netlist.py <netlist.net>")
    nl = parse_netlist(sys.argv[1])
    print(nl)
    for name in sorted(nl.nets):
        nodes = nl.nets[name]
        print("  %-24s %s" % (name, ", ".join(
            "%s.%s%s" % (r, p, "/" + f if f else "") for r, p, f in nodes)))
