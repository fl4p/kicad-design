#!/usr/bin/env python3
"""Re-grade the design's load-bearing inequalities against the PARTS ON THE BOM.

The question this answers is not "were the design's constants consistent when
they were written". It is "are they still consistent with the exact MPNs that
are about to be ordered". Those are different questions and the second one is
the one a release turns on.

`LEDGERS`-tier (GUARDS: reads constants, limits and equations; establishes that
the design arithmetic is internally consistent). It reads no board and no
schematic: the subject is a JSON ledger the design owns. That is a real
limitation and it is stated in the report -- a ledger claiming `R12 = 1 mOhm`
proves nothing about whether R12 is on the board. Pair it with the artefact-tier
guards (`kicad_verify.py`, `kicad_netlist.py`) which do read the board, and with
`RELEASE.md`'s BOM reconciliation, which is what binds a ledger row to an MPN.

TWO TIERS, EVALUATED SEPARATELY, AND THE SECOND IS THE ONE THAT SHIPS
---------------------------------------------------------------------
Every variable carries a `spec` (the interval the design intended) and an
`actual` (the interval the chosen part actually delivers, from its datasheet
over the design's temperature and tolerance range). Every constraint is
evaluated twice:

  intent    over `spec`    -- did the design arithmetic ever close?
  as-built  over `actual`  -- does it still close with these parts?

A release gates on **as-built**. `intent` is reported beside it because the two
failing differently is diagnostic: intent FAIL is a design error, as-built FAIL
with intent PASS is a sourcing error, and they get fixed by different people.

FAIL-CLOSED, POINT BY POINT
---------------------------
* A constraint referencing a variable the ledger does not define is `E-UNDEF`,
  not a skip.
* A variable with no `actual` makes every constraint over it `UNVERIFIED` in the
  as-built tier -- **never** silently absent, and never inherited from `spec`.
  atopile's variable report `continue`s such parameters out of the file
  entirely (`json_parameters.py:187-189`) and swallows extraction failures with
  a bare `except Exception: continue` (`:217-219`); the coverage count below
  exists so that cannot happen here.
* A ledger with zero constraints is `UNVERIFIED`, not a pass over nothing.
* An unparseable or non-conforming ledger is `UNVERIFIED`.
* `meets` is tri-state (`True` / `False` / `None`) and is COMPUTED. atopile's
  `meetsSpec` is hardcoded `None` on the only branch that sets it
  (`json_parameters.py:205`) and is never calculated anywhere in the project.

WHAT THE ARITHMETIC DOES ABOUT CORRELATION -- read this before trusting a margin
--------------------------------------------------------------------------------
The verdict is taken from **uncorrelated interval arithmetic**: every operand is
allowed to sit anywhere in its interval independently of every other operand,
including another occurrence of the same variable. That is always an OUTER
bound, so the verdict can be pessimistic but never optimistic. A guard is
allowed to be too strict; it is not allowed to be too kind.

Alongside it the report prints a **symbol-correlated** value, obtained by
evaluating the expression at the corners of the variable box with each variable
taking ONE value across all of its occurrences. atopile's solver states the rule
this comes from exactly right (`src/faebryk/core/solver/README.md`): *"Singleton
sets are self-correlated / All other sets are uncorrelated with any other set
(including themselves)"* -- so `X - X` is `[-w, +w]` for an interval literal and
`0` for a symbol, and most tolerance calculators get this wrong in one direction
or the other.

When the two values differ, some variable appears more than once in the
expression and the report names it. That difference is the finding, not a
nuisance: it is the point at which someone has to say out loud whether the two
occurrences are the same physical quantity (then the correlated number is the
honest one, and the expression should be rewritten so the cancellation is
visible) or two different parts that happen to share a tolerance (then the
uncorrelated number is the honest one, and they need two variable names). The
corner value is a SAMPLED extremum and is exact only for expressions monotone in
each variable, so it is never the verdict. See POWER.md, "A tolerance does not
cancel against itself unless it is the same part".

LEDGER SCHEMA
-------------
    {
      "schema": "kicad-design/design-ledger/1",
      "design": "<free text naming what this ledger belongs to>",
      "variables": {
        "R_sense": {
          "source":  "picked",           # user | derived | picked | datasheet
          "unit":    "ohm",
          "origin":  "BOM R12 = WSLP2726L1000FEA; datasheet p.2 tab.1, "
                     "+-1% initial, 15 ppm/K over -40..125 C",
          "spec":    [0.00095, 0.00105], # intended interval; null if none
          "actual":  [0.00099, 0.00101]  # what the chosen part delivers; null
        }
      },
      "constraints": [
        {"id": "SENSE-BURDEN",
         "expr": "I_max * R_sense <= V_burden_max",
         "requirement": "burden voltage must stay inside the ADC input range",
         "origin": "ADS1262 datasheet SBAS661C table 7.5"}
      ]
    }

`source` is atopile's `VariableSource` vocabulary, taken verbatim
(`src/faebryk/exporters/parameters/json_parameters.py:25`): `user | derived |
picked | datasheet`. `origin` is this repo's addition and is REQUIRED -- the
whole reason the atopile mechanism is worth stealing is that its `Contradiction`
walks the mutation map back to the user's own expressions and prints them
(`src/faebryk/core/solver/utils.py:65-102`), so the error names both sides. A
tag without a citation cannot do that.

A scalar may be written in place of an interval (`"spec": 1.8`) and means the
singleton `[1.8, 1.8]`. Singletons are self-correlated under any reading, so
this is the one case where the two arithmetics cannot disagree.

MIT PROVENANCE
--------------
Origin: atopile (https://github.com/atopile/atopile), MIT, release **0.15.8**
(PyPI sdist `atopile-0.15.8.tar.gz`,
sha256 76c42f33f151947dbe533d4d0a04b60623b84c38ced6e40d17c3099e5f59b955).
Nothing is copied. Reimplemented from:
  * `src/faebryk/exporters/parameters/json_parameters.py:25-37` -- the
    `source` / `spec` / `actual` / `meetsSpec` vocabulary (the schema).
  * `src/faebryk/libs/picker/picker.py:645-654` -- the shape of a second,
    terminal pass over the whole constraint system after the parts are chosen,
    whose failure is a hard build error (`PickVerificationError`).
  * `src/faebryk/core/solver/utils.py:65-102` -- an error that carries the
    ORIGINS of both sides of a contradiction rather than only its own message.
  * `src/faebryk/core/solver/README.md` -- the correlation rule quoted above.
See `plans/atopile-adoption.md` for what was rejected and why.

Usage:
  design_ledger.py LEDGER.json [--tier=as-built|intent|both] [--json=OUT]
                               [--min-constraints=N]

Exit codes:
  0  PASS         every constraint holds in the gated tier
  1  FAIL         a constraint is violated
  2  UNVERIFIED   the ledger, or part of it, could not be evaluated
"""

from __future__ import annotations

import argparse
import ast
import itertools
import json
import math
import os
import re
import sys

__all__ = ["LedgerError", "Interval", "load_ledger", "evaluate"]

SOURCES = ("user", "derived", "picked", "datasheet")
CMPS = {"<=": "Lt|Eq", "<": "Lt", ">=": "Gt|Eq", ">": "Gt"}

# 2**MAX_CORNER_VARS corner evaluations. Above this the correlated diagnostic
# is reported as unavailable rather than sampled thinly -- a diagnostic that
# quietly degrades is worse than one that says it did not run.
MAX_CORNER_VARS = 14


class LedgerError(Exception):
    """The ledger cannot be evaluated. Always UNVERIFIED, never FAIL."""


# --------------------------------------------------------------- intervals ---

class Interval:
    """A closed real interval. Uncorrelated with everything, itself included."""

    __slots__ = ("lo", "hi")

    def __init__(self, lo, hi=None):
        if hi is None:
            hi = lo
        if not (isinstance(lo, (int, float)) and isinstance(hi, (int, float))):
            raise LedgerError("interval bounds must be numbers, got %r/%r"
                              % (lo, hi))
        if isinstance(lo, bool) or isinstance(hi, bool):
            raise LedgerError("booleans are not interval bounds")
        # An integer that binary64 cannot hold EXACTLY silently moves the
        # bound, and it moves it inward as readily as outward: the pair
        # [9007199254740992, 9007199254740993] collapsed to a singleton, so
        # `X - X <= 0` passed where the uncorrelated interval [-1, 1] must
        # fail (codex review of 0f709ed). That falsifies the outer-bound
        # guarantee this whole module rests on, so it is unevaluable.
        for raw in (lo, hi):
            if isinstance(raw, int) and int(float(raw)) != raw:
                raise LedgerError(
                    "interval bound %d cannot be represented exactly in "
                    "binary64 (nearest double is %d) -- an inexact bound is "
                    "not an outer bound; give it as a rounded-outward pair"
                    % (raw, int(float(raw))))
        lo, hi = float(lo), float(hi)
        if not (math.isfinite(lo) and math.isfinite(hi)):
            raise LedgerError(
                "non-finite interval bound [%r, %r] -- an infinite budget is "
                "not a budget" % (lo, hi))
        if lo > hi:
            raise LedgerError("inverted interval [%g, %g]" % (lo, hi))
        self.lo, self.hi = lo, hi

    @property
    def singleton(self):
        return self.lo == self.hi

    def __repr__(self):
        if self.singleton:
            return "%.6g" % self.lo
        return "[%.6g, %.6g]" % (self.lo, self.hi)

    def __eq__(self, o):
        return isinstance(o, Interval) and (self.lo, self.hi) == (o.lo, o.hi)

    def __add__(self, o):
        return Interval(self.lo + o.lo, self.hi + o.hi)

    def __sub__(self, o):
        return Interval(self.lo - o.hi, self.hi - o.lo)

    def __neg__(self):
        return Interval(-self.hi, -self.lo)

    def __mul__(self, o):
        c = [self.lo * o.lo, self.lo * o.hi, self.hi * o.lo, self.hi * o.hi]
        return Interval(min(c), max(c))

    def __truediv__(self, o):
        if o.lo <= 0.0 <= o.hi:
            raise LedgerError(
                "division by an interval spanning zero (%r) -- the result is "
                "unbounded, and an unbounded operand cannot substantiate a "
                "margin" % o)
        c = [self.lo / o.lo, self.lo / o.hi, self.hi / o.lo, self.hi / o.hi]
        return Interval(min(c), max(c))


def as_interval(v, what):
    if v is None:
        return None
    if isinstance(v, bool):
        raise LedgerError("%s: booleans are not values" % what)
    if isinstance(v, (int, float)):
        return Interval(v)
    if isinstance(v, (list, tuple)):
        if len(v) != 2:
            raise LedgerError("%s: an interval is [lo, hi], got %r" % (what, v))
        return Interval(v[0], v[1])
    raise LedgerError("%s: expected a number or [lo, hi], got %r" % (what, v))


# ------------------------------------------------------------- expressions ---

_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div)


def parse_constraint(expr):
    """('lhs_ast', op, 'rhs_ast', names) for a single comparison.

    Deliberately tiny: `+ - * /`, parentheses, numeric literals, bare names,
    unary minus, and EXACTLY ONE top-level comparison. No calls, no attributes,
    no subscripts, no chained comparisons, no `==`. `==` is excluded on purpose
    -- an equality over floating-point intervals is almost never the constraint
    anyone means, and accepting it would let a typo read as a requirement.
    """
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise LedgerError("cannot parse %r: %s" % (expr, e))
    node = tree.body
    if not isinstance(node, ast.Compare):
        raise LedgerError(
            "%r is not a comparison -- a constraint must state a bound "
            "(<=, <, >=, >)" % expr)
    if len(node.ops) != 1:
        raise LedgerError(
            "%r chains comparisons; write them as separate constraints so "
            "each gets its own verdict and its own id" % expr)
    op = node.ops[0]
    sym = {ast.LtE: "<=", ast.Lt: "<", ast.GtE: ">=", ast.Gt: ">"}.get(type(op))
    if sym is None:
        raise LedgerError(
            "%r uses an unsupported comparison; allowed: <=, <, >=, >" % expr)

    names = set()

    def _check(n):
        if isinstance(n, ast.BinOp):
            if not isinstance(n.op, _ALLOWED_BINOPS):
                raise LedgerError("%r: unsupported operator %s"
                                  % (expr, type(n.op).__name__))
            _check(n.left)
            _check(n.right)
        elif isinstance(n, ast.UnaryOp):
            if not isinstance(n.op, (ast.UAdd, ast.USub)):
                raise LedgerError("%r: unsupported unary operator" % expr)
            _check(n.operand)
        elif isinstance(n, ast.Name):
            names.add(n.id)
        elif isinstance(n, ast.Constant):
            if isinstance(n.value, bool) or not isinstance(
                    n.value, (int, float)):
                raise LedgerError("%r: only numeric literals are allowed"
                                  % expr)
        else:
            raise LedgerError("%r: %s is not allowed in a constraint"
                              % (expr, type(n).__name__))

    _check(node.left)
    _check(node.comparators[0])
    return node.left, sym, node.comparators[0], names


def occurrence_counts(node):
    out = {}
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            out[n.id] = out.get(n.id, 0) + 1
    return out


def eval_interval(node, env):
    """Uncorrelated interval evaluation. Always an outer bound."""
    if isinstance(node, ast.Constant):
        return Interval(float(node.value))
    if isinstance(node, ast.Name):
        return env[node.id]
    if isinstance(node, ast.UnaryOp):
        v = eval_interval(node.operand, env)
        return -v if isinstance(node.op, ast.USub) else v
    a = eval_interval(node.left, env)
    b = eval_interval(node.right, env)
    if isinstance(node.op, ast.Add):
        return a + b
    if isinstance(node.op, ast.Sub):
        return a - b
    if isinstance(node.op, ast.Mult):
        return a * b
    return a / b


def _eval_point(node, point):
    if isinstance(node, ast.Constant):
        return float(node.value)
    if isinstance(node, ast.Name):
        return point[node.id]
    if isinstance(node, ast.UnaryOp):
        v = _eval_point(node.operand, point)
        return -v if isinstance(node.op, ast.USub) else v
    a = _eval_point(node.left, point)
    b = _eval_point(node.right, point)
    if isinstance(node.op, ast.Add):
        return a + b
    if isinstance(node.op, ast.Sub):
        return a - b
    if isinstance(node.op, ast.Mult):
        return a * b
    if b == 0.0:
        raise LedgerError("division by zero at a corner of the variable box")
    return a / b


def eval_corners(node, env):
    """Symbol-correlated extremum, sampled at the corners of the box.

    One value per VARIABLE, shared by all of its occurrences -- so `X - X` is
    exactly 0 here and `[-w, +w]` under `eval_interval`. Exact only when the
    expression is monotone in each variable, which is why this is a diagnostic
    and never the verdict. Returns None when there are too many variables to
    enumerate honestly.
    """
    names = sorted(occurrence_counts(node))
    if len(names) > MAX_CORNER_VARS:
        return None
    if not names:
        v = _eval_point(node, {})
        return Interval(v, v)
    lo = hi = None
    for combo in itertools.product(*[(env[n].lo, env[n].hi) for n in names]):
        v = _eval_point(node, dict(zip(names, combo)))
        if not math.isfinite(v):
            return None
        lo = v if lo is None else min(lo, v)
        hi = v if hi is None else max(hi, v)
    return Interval(lo, hi)


DIMENSIONLESS = "dimensionless"


def _parse_unit(text):
    """A unit token to an exponent map: 'V/A' -> {'V': 1, 'A': -1}.

    Atoms are compared as written -- this does not know that mV and V share a
    dimension, and does not pretend to. It exists to catch the failure that
    was actually reproduced (volts compared with amperes) and to let a product
    cancel, so that A * (V/A) is V rather than an uncomparable string.
    """
    if text is None:
        return {}
    flat = text.strip()
    if not flat or flat.lower() == DIMENSIONLESS:
        return {}
    exps = {}
    sign = 1
    token = ""
    for ch in flat + "*":
        if ch in "*/":
            atom = token.strip()
            if atom and atom.lower() != DIMENSIONLESS:
                exps[atom] = exps.get(atom, 0) + sign
            token = ""
            sign = -1 if ch == "/" else 1
        else:
            token += ch
    return {k: v for k, v in exps.items() if v}


def _unit_str(exps):
    if not exps:
        return DIMENSIONLESS
    num = [k if v == 1 else "%s^%d" % (k, v)
           for k, v in sorted(exps.items()) if v > 0]
    den = [k if v == -1 else "%s^%d" % (k, -v)
           for k, v in sorted(exps.items()) if v < 0]
    return ("*".join(num) or "1") + ("/" + "*".join(den) if den else "")


def _mul(a, b, scale=1):
    out = dict(a)
    for k, v in b.items():
        out[k] = out.get(k, 0) + scale * v
    return {k: v for k, v in out.items() if v}


def unit_of(node, units, path="<ledger>", cid="<constraint>"):
    """The unit of an expression, as an exponent map.

    `+` and `-` require their operands to agree; `*` and `/` compose and
    cancel; a literal is dimensionless and therefore comparable with anything,
    so `X - X <= 0` still works.
    """
    if isinstance(node, ast.Constant):
        return {}
    if isinstance(node, ast.Name):
        return _parse_unit(units.get(node.id))
    if isinstance(node, ast.UnaryOp):
        return unit_of(node.operand, units, path, cid)
    if isinstance(node, ast.BinOp):
        left = unit_of(node.left, units, path, cid)
        right = unit_of(node.right, units, path, cid)
        if isinstance(node.op, (ast.Add, ast.Sub)):
            if left != right and left and right:
                raise LedgerError(
                    "%s: constraint %s adds or subtracts %s and %s -- that is "
                    "not a quantity"
                    % (path, cid, _unit_str(left), _unit_str(right)))
            return left or right
        if isinstance(node.op, ast.Mult):
            return _mul(left, right)
        if isinstance(node.op, ast.Div):
            return _mul(left, right, -1)
        if isinstance(node.op, ast.Pow):
            if isinstance(node.right, ast.Constant) and left:
                try:
                    n = int(node.right.value)
                except (TypeError, ValueError):
                    return left
                return {k: v * n for k, v in left.items()}
            return left
    return {}


def satisfied(lhs, op, rhs):
    """Worst-case satisfaction of `lhs OP rhs` over the two intervals.

    True  -- holds for every pair of points.
    False -- fails for at least one pair.
    """
    if op == "<=":
        return lhs.hi <= rhs.lo
    if op == "<":
        return lhs.hi < rhs.lo
    if op == ">=":
        return lhs.lo >= rhs.hi
    return lhs.lo > rhs.hi


def margin(lhs, op, rhs):
    """Worst-case margin, positive when satisfied. Same units as the operands."""
    if op in ("<=", "<"):
        return rhs.lo - lhs.hi
    return lhs.lo - rhs.hi


# An origin that points back at the document being graded is the ledger
# agreeing with itself: every origin equal to "this ledger" passed validation
# and contributed to an exit-0 run (codex review of 0f709ed). This cannot
# police a citation's truth, only its self-reference, which is the failure
# actually observed.
_SELF_CITATIONS = frozenset((
    "this ledger", "the ledger", "ledger", "this document", "this file",
    "self", "itself", "as above", "see above", "n/a", "na", "none",
    "tbd", "todo", "unknown", "-", "?",
))


def _reject_self_citation(path, what, origin, design):
    flat = " ".join(origin.split()).strip().strip(".").lower()
    if flat in _SELF_CITATIONS or (design and flat == str(design).lower()) \
            or flat == os.path.basename(str(path)).lower():
        raise LedgerError(
            "%s: %s cites %r as its origin, which names this ledger rather "
            "than anything outside it. An origin has to be somewhere a "
            "reviewer can go and disagree with -- a document and page, a "
            "measurement, a computation naming its inputs."
            % (path, what, origin))


_REPORT_MARKER = "design_ledger"


def _is_own_report(path):
    """True only when `path` is a JSON document this tool wrote."""
    try:
        with open(path, encoding="utf-8") as stream:
            doc = json.load(stream)
    except (OSError, ValueError, UnicodeDecodeError):
        return False
    return isinstance(doc, dict) and doc.get("tool") == _REPORT_MARKER


# ------------------------------------------------------------------ ledger ---

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def load_ledger(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise LedgerError("cannot read %s: %s" % (path, e))
    except UnicodeDecodeError as e:
        raise LedgerError("%s is not valid UTF-8 (%s)" % (path, e.reason))
    try:
        doc = json.loads(raw)
    except ValueError as e:
        raise LedgerError("%s is not valid JSON: %s" % (path, e))
    if not isinstance(doc, dict):
        raise LedgerError("%s: the ledger must be a JSON object" % path)
    if doc.get("schema") != "kicad-design/design-ledger/1":
        raise LedgerError(
            "%s: schema is %r, expected 'kicad-design/design-ledger/1' -- an "
            "unversioned or foreign ledger is unevaluable, not empty"
            % (path, doc.get("schema")))

    variables = doc.get("variables")
    if not isinstance(variables, dict):
        raise LedgerError("%s: 'variables' must be an object" % path)
    out_vars = {}
    for name, v in variables.items():
        if not _NAME_RE.match(name):
            raise LedgerError(
                "%s: %r is not a usable variable name (it must be a plain "
                "identifier, because constraints reference it as one)"
                % (path, name))
        if not isinstance(v, dict):
            raise LedgerError("%s: variable %s must be an object"
                              % (path, name))
        src = v.get("source")
        if src not in SOURCES:
            raise LedgerError(
                "%s: variable %s has source %r; it must be one of %s. An "
                "untagged value cannot be told apart from a guess."
                % (path, name, src, "/".join(SOURCES)))
        origin = v.get("origin")
        if not isinstance(origin, str) or not origin.strip():
            raise LedgerError(
                "%s: variable %s has no 'origin'. A provenance TAG without a "
                "citation cannot name either side of a contradiction, which "
                "is the entire point of tagging it." % (path, name))
        _reject_self_citation(path, "variable %s" % name, origin,
                              doc.get("design"))
        unit = v.get("unit")
        if not isinstance(unit, str) or not unit.strip():
            raise LedgerError(
                "%s: variable %s has no 'unit'. It was optional and never "
                "interpreted, so a ledger comparing volts with amperes graded "
                "PASS (codex review of 0f709ed). Give the unit as a plain "
                "token ('V', 'A', 'mohm'); use 'dimensionless' for a ratio."
                % (path, name))
        out_vars[name] = {
            "source": src,
            "origin": origin,
            "unit": unit.strip(),
            "spec": as_interval(v.get("spec"), "%s.spec" % name),
            "actual": as_interval(v.get("actual"), "%s.actual" % name),
        }

    constraints = doc.get("constraints")
    if not isinstance(constraints, list):
        raise LedgerError("%s: 'constraints' must be a list" % path)
    out_cons = []
    seen = set()
    for i, c in enumerate(constraints):
        if not isinstance(c, dict):
            raise LedgerError("%s: constraint %d must be an object" % (path, i))
        cid = c.get("id")
        if not isinstance(cid, str) or not cid.strip():
            raise LedgerError("%s: constraint %d has no id" % (path, i))
        if cid in seen:
            raise LedgerError(
                "%s: duplicate constraint id %r -- two verdicts under one id "
                "cannot be told apart in a report" % (path, cid))
        seen.add(cid)
        expr = c.get("expr")
        if not isinstance(expr, str):
            raise LedgerError("%s: constraint %s has no 'expr'" % (path, cid))
        origin = c.get("origin")
        if not isinstance(origin, str) or not origin.strip():
            raise LedgerError(
                "%s: constraint %s has no 'origin' -- a threshold whose "
                "provenance is unrecorded cannot be audited or re-derived "
                "(GUARDS, 'Establish a threshold's provenance and floor')"
                % (path, cid))
        _reject_self_citation(path, "constraint %s" % cid, origin,
                              doc.get("design"))
        lhs, op, rhs, names = parse_constraint(expr)
        # An undefined name is NOT refused here: evaluation already reports it
        # per-constraint as UNVERIFIED, which names the row instead of killing
        # the whole ledger. It contributes no unit, which is the conservative
        # reading.
        # `.get` because an undefined name reaches evaluation as UNVERIFIED
        # rather than being refused here; it contributes no unit.
        units = {n: out_vars[n]["unit"] for n in names if n in out_vars}
        lu, ru = unit_of(lhs, units, path, cid), unit_of(rhs, units, path, cid)
        if lu != ru and lu and ru:
            raise LedgerError(
                "%s: constraint %s compares %s with %s. A comparison across "
                "units is not a weak result, it is a meaningless one: this "
                "ledger graded volts against amperes as PASS before units "
                "were interpreted (codex review of 0f709ed)."
                % (path, cid, _unit_str(lu), _unit_str(ru)))
        out_cons.append({
            "id": cid, "expr": expr, "lhs": lhs, "op": op, "rhs": rhs,
            "names": names, "origin": origin,
            "requirement": c.get("requirement", ""),
        })

    return {"design": doc.get("design", ""), "variables": out_vars,
            "constraints": out_cons}


def evaluate(ledger, tier):
    """[row] for one tier. Each row: id, state, and everything a reader needs.

    `state` is "PASS" / "FAIL" / "UNVERIFIED". A row is never absent.
    """
    key = {"intent": "spec", "as-built": "actual"}[tier]
    rows = []
    for c in ledger["constraints"]:
        undef = sorted(n for n in c["names"] if n not in ledger["variables"])
        if undef:
            rows.append(_row(c, "UNVERIFIED", "E-UNDEF",
                             "undefined variable(s): %s" % ", ".join(undef)))
            continue
        missing = sorted(n for n in c["names"]
                         if ledger["variables"][n][key] is None)
        if missing:
            rows.append(_row(
                c, "UNVERIFIED", "E-NOVALUE",
                "no %s value for %s. An absent value is UNVERIFIED; it is "
                "never inherited from the other tier and never omitted from "
                "this report." % (key, ", ".join(missing))))
            continue
        env = {n: ledger["variables"][n][key] for n in c["names"]}
        try:
            lhs = eval_interval(c["lhs"], env)
            rhs = eval_interval(c["rhs"], env)
        except LedgerError as e:
            rows.append(_row(c, "UNVERIFIED", "E-ARITH", str(e)))
            continue

        ok = satisfied(lhs, c["op"], rhs)
        mg = margin(lhs, c["op"], rhs)
        row = _row(c, "PASS" if ok else "FAIL", "",
                   "%r %s %r (worst-case margin %.6g)"
                   % (lhs, c["op"], rhs, mg))
        row.update(lhs=repr(lhs), rhs=repr(rhs), margin=mg, meets=ok)
        row["origins"] = {n: ledger["variables"][n]["origin"]
                          for n in sorted(c["names"])}
        row["sources"] = {n: ledger["variables"][n]["source"]
                          for n in sorted(c["names"])}

        # --- correlation diagnostic (never the verdict) ---
        repeated = sorted(n for n, k in _repeats(c).items() if k > 1)
        row["repeated_variables"] = repeated
        if repeated:
            # Correlate across the WHOLE comparison. Evaluating each side's
            # corners and then comparing the two intervals re-decorrelates
            # them: `X <= X` with X=[1,2] reported a correlated FAIL, because
            # lhs.hi=2 was compared against rhs.lo=1 (codex review of
            # 0f709ed). The relation has to be sampled at one consistent
            # assignment per variable, so the difference is what gets
            # cornered.
            try:
                diff = eval_corners(
                    ast.BinOp(left=c["lhs"], op=ast.Sub(), right=c["rhs"]),
                    env)
            except LedgerError:
                diff = None
            if diff is not None:
                zero = Interval(0.0, 0.0)
                row["correlated_difference"] = repr(diff)
                row["correlated_margin"] = margin(diff, c["op"], zero)
                row["correlated_meets"] = satisfied(diff, c["op"], zero)
                if row["correlated_meets"] != ok:
                    row["detail"] += (
                        ". CORRELATION MATTERS HERE: %s appear(s) more than "
                        "once, and treating each occurrence as the SAME "
                        "physical quantity gives %s while treating them as "
                        "independent gives %s. The verdict above is the "
                        "independent (outer-bound) one, which is sound but "
                        "may be pessimistic. Decide which reading is true and "
                        "either rewrite the expression or split the variable "
                        "-- do not adopt the tighter number silently."
                        % (", ".join(repeated),
                           "PASS" if row["correlated_meets"] else "FAIL",
                           "PASS" if ok else "FAIL"))
            else:
                row["correlated_margin"] = None
                row["detail"] += (
                    ". Correlation diagnostic UNAVAILABLE (more than %d "
                    "variables, or a corner is undefined): %s appear(s) more "
                    "than once, so the margin above may be pessimistic by an "
                    "unmeasured amount."
                    % (MAX_CORNER_VARS, ", ".join(repeated)))
        rows.append(row)
    return rows


def _repeats(c):
    out = {}
    for node in (c["lhs"], c["rhs"]):
        for n, k in occurrence_counts(node).items():
            out[n] = out.get(n, 0) + k
    return out


def _row(c, state, branch, detail):
    return {"id": c["id"], "expr": c["expr"], "state": state,
            "branch": branch, "detail": detail,
            "requirement": c["requirement"], "constraint_origin": c["origin"]}


# --------------------------------------------------------------------- CLI ---

def verdict(code, msg, branch=""):
    tag = {0: "LEDGER-PASS", 1: "LEDGER-FAIL", 2: "LEDGER-UNVERIFIED"}[code]
    bid = "[%s] " % branch if branch else ""
    line = "%s: %s%s" % (tag, bid, msg)
    line = line.encode("ascii", "backslashreplace").decode("ascii")
    sys.stdout.write(re.sub(r"[\x00-\x1f\x7f]", " ", line) + "\n")
    return code


class GuardArgumentParser(argparse.ArgumentParser):
    """argparse's usage-error exit is 2, which is this guard's UNVERIFIED;
    both refuse, but the message must say which happened."""

    def error(self, message):
        self.print_usage(sys.stderr)
        raise SystemExit(
            "LEDGER-UNVERIFIED: [E-ARGS] bad invocation: %s -- a malformed "
            "command line is unevaluable, not a verdict about the design"
            % message)


def _print_tier(name, rows, gated):
    tally = {}
    for r in rows:
        tally[r["state"]] = tally.get(r["state"], 0) + 1
    head = "%s tier%s: %d constraint(s) -- %s" % (
        name, " (GATED)" if gated else " (report only)", len(rows),
        ", ".join("%d %s" % (tally[k], k) for k in sorted(tally)))
    sys.stdout.write(head + "\n")
    for r in rows:
        sys.stdout.write("  %-11s %-22s %s\n"
                         % (r["state"], r["id"], r["detail"]))
        if r["state"] != "PASS":
            sys.stdout.write("      requirement: %s\n"
                             % (r["requirement"] or "(none recorded)"))
            sys.stdout.write("      constraint origin: %s\n"
                             % r["constraint_origin"])
            for n, o in sorted(r.get("origins", {}).items()):
                sys.stdout.write("      %s [%s]: %s\n"
                                 % (n, r["sources"][n], o))


def run(argv):
    ap = GuardArgumentParser(
        prog="design_ledger.py", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("ledger")
    ap.add_argument("--tier", choices=("as-built", "intent", "both"),
                    default="as-built",
                    help="which tier the EXIT CODE is taken from; both tiers "
                         "are always printed (default: as-built)")
    ap.add_argument("--expect-variables", default=None, metavar="A,B,C",
                    help="the variable names the AUTHORITY says this ledger "
                         "must define. Without it, coverage is counted from "
                         "the ledger being graded, so deleting a variable and "
                         "its constraint together still passes")
    ap.add_argument("--expect-constraints", default=None, metavar="c1,c2",
                    help="the constraint ids the authority says must be "
                         "graded; same reason")
    ap.add_argument("--min-constraints", type=int, default=1,
                    help="refuse if the ledger has fewer (default 1); a "
                         "ledger that lost its constraints must not pass")
    ap.add_argument("--json", dest="json_out",
                    help="write the full per-constraint rows to this file")
    args = ap.parse_args(argv[1:])

    if args.min_constraints < 1:
        return verdict(2, "--min-constraints must be at least 1: a run over "
                          "zero constraints is not a pass", "E-ARGS")
    try:
        ledger = load_ledger(args.ledger)
    except LedgerError as e:
        return verdict(2, str(e), "E-LEDGER")

    n = len(ledger["constraints"])
    if n < args.min_constraints:
        return verdict(2, "%s declares %d constraint(s), fewer than the "
                          "required %d -- an empty or truncated ledger is "
                          "unevaluable, not a pass"
                       % (args.ledger, n, args.min_constraints), "E-EMPTY")

    # Take the expectation from the AUTHORITY, not from the artefact's own
    # neighbourhood (GUARDS.md). Counting variables and constraints out of the
    # ledger being graded means a deleted variable AND its constraint leave a
    # smaller, still-passing ledger -- and a constant-only tautology counts as
    # a graded constraint (codex review of 0f709ed).
    for flag, label, present in (
        (args.expect_variables, "variable", set(ledger["variables"])),
        (args.expect_constraints, "constraint",
         {c["id"] for c in ledger["constraints"]}),
    ):
        if flag is None:
            continue
        expected = {s.strip() for s in flag.split(",") if s.strip()}
        if not expected:
            return verdict(2, "--expect-%ss was given but names nothing; an "
                              "empty inventory is not an expectation"
                              % label, "E-LEDGER")
        missing = sorted(expected - present)
        extra = sorted(present - expected)
        if missing or extra:
            return verdict(
                2, "%s inventory does not match the authority: %s%s%s"
                % (label,
                   "missing " + ", ".join(missing) if missing else "",
                   "; " if missing and extra else "",
                   "unexpected " + ", ".join(extra) if extra else ""),
                "E-INVENTORY")

    tiers = {t: evaluate(ledger, t) for t in ("intent", "as-built")}
    gated = ("intent", "as-built") if args.tier == "both" else (args.tier,)

    for t in ("intent", "as-built"):
        _print_tier(t, tiers[t], t in gated)

    if args.json_out:
        # The ledger is the AUTHORITY. `--json=ledger.json ledger.json`
        # returned PASS and replaced it with a report that has no schema
        # (codex review of 0f709ed) -- the guard destroying the thing it
        # grades, the same defect copper_guards had.
        try:
            same = (os.path.exists(args.json_out)
                    and os.path.samefile(args.json_out, args.ledger))
        except OSError:
            same = os.path.abspath(args.json_out) == os.path.abspath(args.ledger)
        if same:
            return verdict(2, "--json %s is the ledger being graded; refusing "
                              "to overwrite the authority with a report"
                              % args.json_out, "E-LEDGER")
        if os.path.exists(args.json_out) and not _is_own_report(args.json_out):
            return verdict(2, "--json %s exists and is not a design_ledger "
                              "report; refusing to overwrite a file this "
                              "guard does not own" % args.json_out, "E-LEDGER")
        try:
            with open(args.json_out, "w", encoding="utf-8") as f:
                json.dump({"tool": _REPORT_MARKER,
                           "design": ledger["design"],
                           "gated_tiers": list(gated),
                           "tiers": {t: tiers[t] for t in tiers}},
                          f, indent=2, default=str)
        except OSError as e:
            return verdict(2, "cannot write %s: %s" % (args.json_out, e),
                           "E-OUT")

    # UNVERIFIED outranks FAIL: "I could not tell" is not "it is fine", and
    # it is not "it is broken" either -- the two need different fixes, so the
    # louder one must be the one that cannot be acted on blindly.
    worst, branch = 0, ""
    for t in gated:
        for r in tiers[t]:
            if r["state"] == "UNVERIFIED" and worst < 2:
                worst, branch = 2, r["branch"] or "E-UNVERIFIED"
            elif r["state"] == "FAIL" and worst < 1:
                worst, branch = 1, "VIOLATED"

    bad = [(t, r["id"]) for t in gated for r in tiers[t]
           if r["state"] != "PASS"]
    if worst == 0:
        return verdict(0, "%d constraint(s) hold in the gated tier(s) %s, "
                          "over %d variable(s)"
                       % (n, "+".join(gated), len(ledger["variables"])))
    return verdict(worst, "%d of %d gated constraint-tier results are not "
                          "PASS: %s"
                   % (len(bad), n * len(gated),
                      ", ".join("%s/%s" % (t, i) for t, i in bad)), branch)


def main(argv):
    try:
        return run(argv)
    except SystemExit:
        raise
    except Exception as exc:  # noqa: BLE001 - never die without a verdict
        return verdict(2, "internal error, refusing to grade: %r" % exc,
                       "E-INTERNAL")


if __name__ == "__main__":
    sys.exit(main(sys.argv))
