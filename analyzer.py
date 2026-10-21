"""
codeviz analyzer: Python source -> geometry, without knowing any shapes.

The ONLY priors in this file are the compositional rules below. Nothing here
mentions squares, triangles, circles, grids, pyramids or any algorithm name.
Whatever shape appears is produced by running the program's own structure
through these rules.

    R1  A non-negative whole quantity is a length:      k        -> k cells in a row
    R2  Multiplying spans a new direction:               a * b    -> a's cells x b's cells
    R3  Adding lays quantities end to end:               a + b    -> b placed after a
    R4  A loop variable is a position along an axis:     for i .. -> coordinate i
    R5  Accumulating places each contribution at the     acc += e -> e's shape, placed at
        loop position where it happened                              the current loop point
    R6  A collection is slots; nesting adds directions   [[..]..] -> one axis per level
    R7  Only executed code counts (conditions carve)     if ...   -> skipped points vanish
    R8  A call sits one level deeper than its caller,    f(..)    -> its callees side by side
        and successive calls are laid end to end (R3)               underneath it

Similarity is decided extensionally, never by name:
    * same shape  = identical cell sets (up to rotation/reflection) when every
                    free input is set to the same size N, for several N
    * same law    = same count polynomial in N
"""
import ast
import itertools
import json
import re
import sys
from fractions import Fraction

CELL_CAP = 6000
NODE_CAP = 3000
STEP_CAP = 400_000
DEPTH_CAP = 14
SIZE_POOL = [6, 4, 5, 3, 7, 8, 2]
SIG_SIZES = [2, 3, 4, 5, 6, 7]
GROWTH_SIZES = [6, 7, 8, 9, 10]
LOG_SIZES = [8, 16, 32, 64]
PAD = "·"


# --------------------------------------------------------------------------- geometry

class Geom:
    """cells: {coordinate tuple: provenance tag}. A tag lists which factor each
    direction of the cell came from, so regions of a product stay distinguishable."""
    __slots__ = ("axes", "cells")

    def __init__(self, axes, cells):
        self.axes = list(axes)
        self.cells = dict(cells)


POINT = Geom([], {(): ()})


def bar(label, k):                                   # R1
    if isinstance(k, bool) or not isinstance(k, int) or k < 0 or k > CELL_CAP:
        return None
    return Geom([label], {(i,): (label,) for i in range(k)})


def product(a, b):                                   # R2
    if a is None or b is None or len(a.cells) * len(b.cells) > CELL_CAP:
        return None
    return Geom(a.axes + b.axes, {x + y: ta + tb for x, ta in a.cells.items()
                                  for y, tb in b.cells.items()})


def pad(g, d):
    extra = d - len(g.axes)
    if extra <= 0:
        return g
    return Geom(g.axes + [PAD] * extra, {c + (0,) * extra: t for c, t in g.cells.items()})


def merge_label(x, y):
    if x == y or y == PAD:
        return x
    if x == PAD:
        return y
    parts = x.split("|")
    parts += [q for q in y.split("|") if q not in parts]
    return "|".join(parts)


def concat(a, b):                                    # R3
    if a is None or b is None:
        return None
    d = max(len(a.axes), len(b.axes), 1)
    a, b = pad(a, d), pad(b, d)
    off = 1 + max((c[0] for c in a.cells), default=-1)
    axes = [merge_label(x, y) for x, y in zip(a.axes, b.axes)]
    cells = dict(a.cells)
    cells.update({(c[0] + off,) + c[1:]: t for c, t in b.cells.items()})
    return Geom(axes, cells) if len(cells) <= CELL_CAP else None


def place(labels, coords, g):                        # R4 / R5
    if g is None:
        return None
    coords = tuple(coords)
    return Geom(list(labels) + g.axes, {coords + c: t for c, t in g.cells.items()})


def union(parts):
    if any(p is None for p in parts):
        return None
    if not parts:
        return Geom([], {})
    d = max(len(p.axes) for p in parts)
    axes = [PAD] * d
    cells = {}
    for p in parts:
        p = pad(p, d)
        axes = [merge_label(x, y) for x, y in zip(axes, p.axes)]
        cells.update(p.cells)
        if len(cells) > CELL_CAP:
            return None
    return Geom(axes, cells)


def normalize(g):
    """Drop directions with no extent, translate to the origin, fold >3 dims into height."""
    if g is None:
        return None
    if not g.cells:
        return Geom([], {})
    d = len(g.axes)
    keep = [k for k in range(d) if len({c[k] for c in g.cells}) > 1]
    mins = [min(c[k] for c in g.cells) for k in keep]
    cells = {tuple(c[k] - m for k, m in zip(keep, mins)): t for c, t in g.cells.items()}
    axes = [g.axes[k] for k in keep]
    if len(axes) > 3:
        counts = {}
        for c in cells:
            counts[c[:2]] = counts.get(c[:2], 0) + 1
        cells = {xy + (h,): () for xy, n in counts.items() for h in range(n)}
        axes = axes[:2] + ["×".join(axes[2:])]
    return Geom(axes, cells)


def region_key(tag):
    """Commutative: x·1 and 1·x are the same kind of piece."""
    return "·".join(sorted(tag)) if tag else ""


def canon(g):
    """Shape identity up to axis permutation and reflection."""
    cells = list(g.cells)
    d = len(g.axes)
    if not cells:
        return (0, ())
    ext = [max(c[k] for c in cells) for k in range(d)]
    best = None
    for perm in itertools.permutations(range(d)):
        for flips in itertools.product((0, 1), repeat=d):
            t = tuple(sorted(tuple(ext[p] - c[p] if f else c[p] for p, f in zip(perm, flips))
                             for c in cells))
            if best is None or t < best:
                best = t
    return (d, best)


def call_geom(root):
    """R8: leaf call = 1 cell wide; a call is as wide as its callees laid end to end;
    each call fills its width one row below its caller. Siblings alternate tags so
    the boundaries between separate calls stay visible."""
    cells = {}

    def width(n):
        n["w"] = max(1, sum(width(k) for k in n["kids"]))
        return n["w"]

    def put(n, x, depth, k):
        tag = (n["name"] + ("" if k % 2 == 0 else "′"),)
        for i in range(n["w"]):
            cells[(x + i, depth)] = tag
        for j, kid in enumerate(n["kids"]):
            put(kid, x, depth + 1, j)
            x += kid["w"]

    width(root)
    put(root, 0, 0, 0)
    return Geom(["calls", "depth"], cells) if len(cells) <= CELL_CAP else None


def tree_depth(n):
    return 1 + max((tree_depth(k) for k in n["kids"]), default=0)


# --------------------------------------------------------------------------- values

class Q:
    """A runtime value plus (optionally) the geometry it was built from.
    Geometry may be deferred (mk): conditions and indices only need the number,
    so shapes are built only when something actually looks at them."""
    __slots__ = ("v", "g", "label", "acc", "_cache", "mk")

    def __init__(self, v, g=None, label=None, acc=None, mk=None):
        self.v, self.g, self.label, self.acc, self._cache, self.mk = v, g, label, acc, None, mk


def raw_list_geom(v):                                # R6
    if not isinstance(v, (list, tuple)):
        return POINT
    return union([place(["slot"], (k,), raw_list_geom(e)) for k, e in enumerate(v)])


def geom_of(q):
    if q.mk is not None:
        q.g, q.mk = q.mk(), None
    if q.g is not None:
        return q.g
    if q.acc is not None:
        if q._cache is None:
            q._cache = union([place(l, c, g) for l, c, g in q.acc])
        return q._cache
    if isinstance(q.v, int) and not isinstance(q.v, bool):
        return bar(q.label or str(q.v), q.v)
    if isinstance(q.v, (list, tuple)):
        return raw_list_geom(q.v)
    if isinstance(q.v, range):
        return bar(q.label or "range", len(q.v))
    return None


def slot_geom(q):
    return geom_of(q) if isinstance(q.v, (list, tuple)) else POINT


class FuncRef:
    def __init__(self, node):
        self.node = node


# --------------------------------------------------------------------------- interpreter

class StepLimit(Exception):
    pass


class Ret(Exception):
    def __init__(self, q):
        self.q = q


class Brk(Exception):
    pass


class Cont(Exception):
    pass


class Env:
    def __init__(self, parent=None):
        self.vars, self.parent = {}, parent

    def lookup(self, name):
        e = self
        while e is not None:
            if name in e.vars:
                return e.vars[name]
            e = e.parent
        return None


class Frame:
    def __init__(self, env, is_module=False):
        self.env = env
        self.loops = []                 # [(label, coord)]
        self.visits = {}                # depth -> set of (labels, coords)
        self.is_module = is_module
        self.touched = {}               # accumulator name -> first line


SAFE_FUNCS = {"abs": abs, "min": min, "max": max, "int": int, "float": float,
              "round": round, "pow": pow, "len": len, "bool": bool}

BINOPS = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
          ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
          ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
          ast.Pow: lambda a, b: a ** b}

CMPOPS = {ast.Lt: lambda a, b: a < b, ast.LtE: lambda a, b: a <= b,
          ast.Gt: lambda a, b: a > b, ast.GtE: lambda a, b: a >= b,
          ast.Eq: lambda a, b: a == b, ast.NotEq: lambda a, b: a != b,
          ast.In: lambda a, b: a in b, ast.NotIn: lambda a, b: a not in b}


def safe(fn, *args):
    try:
        if any(a is None for a in args):
            return None
        r = fn(*args)
        if isinstance(r, int) and abs(r) > 10 ** 12:
            return None
        return r
    except Exception:
        return None


class Interp:
    def __init__(self, src, size_of):
        self.src = src
        self.lines = src.splitlines()
        self.size_of = size_of          # (name, order) -> int
        self.free_order = []
        self.steps = 0
        self.depth = 0
        self.structures = []
        self.errors = []
        self.globals = Env()
        self.call_stack = []            # R8: open call nodes
        self.last_root = None
        self.truncated = False
        self.only = None                # restrict standalone analysis to these functions

    # ---- helpers
    def tick(self):
        self.steps += 1
        if self.steps > STEP_CAP:
            raise StepLimit()

    def free(self, name):
        if name not in self.free_order:
            self.free_order.append(name)
        return Q(self.size_of(name, self.free_order.index(name)), label=name)

    def code(self, node):
        try:
            return ast.unparse(node)
        except Exception:
            return "?"

    def line_text(self, lineno):
        return self.lines[lineno - 1].strip() if 0 < lineno <= len(self.lines) else ""

    def record(self, title, lineno, q=None, geom=None):
        g = geom if geom is not None else (geom_of(q) if q is not None else None)
        g = normalize(g)
        mag = len(g.cells) if g is not None else (
            q.v if q is not None and isinstance(q.v, (int, float)) and not isinstance(q.v, bool) else None)
        self.structures.append({"title": title, "line": lineno,
                                "code": self.line_text(lineno), "geom": g, "magnitude": mag})

    def visit(self, fr):
        if fr.loops:
            d = len(fr.loops)
            fr.visits.setdefault(d, set()).add((tuple(l for l, _ in fr.loops),
                                                tuple(c for _, c in fr.loops)))

    def visits_geom(self, fr):
        if not fr.visits:
            return None
        pts = fr.visits[max(fr.visits)]
        return union([place(l, c, POINT) for l, c in pts])

    # ---- expressions
    def ev(self, node, fr):
        self.tick()
        m = getattr(self, "ev_" + type(node).__name__, None)
        return m(node, fr) if m else Q(None)

    def ev_Constant(self, n, fr):
        return Q(n.value, label=repr(n.value))

    def ev_Name(self, n, fr):
        q = fr.env.lookup(n.id)
        if q is not None:
            return q
        if n.id in SAFE_FUNCS or n.id in ("range", "sum", "print", "list"):
            return Q(None)
        q = self.free(n.id)
        self.globals.vars[n.id] = q
        return q

    def ev_BinOp(self, n, fr):
        a, b = self.ev(n.left, fr), self.ev(n.right, fr)
        fn = BINOPS.get(type(n.op))
        v = safe(fn, a.v, b.v) if fn else None
        label = self.code(n)
        mk = None
        if isinstance(n.op, ast.Add):
            mk = lambda: concat(geom_of(a), geom_of(b))
        elif isinstance(n.op, ast.Mult):
            mk = lambda: product(geom_of(a), geom_of(b))
        elif isinstance(n.op, ast.Pow) and isinstance(b.v, int) and 0 <= b.v <= 3:
            def mk():
                g = POINT
                for _ in range(b.v):
                    g = product(g, geom_of(a))
                return g
        return Q(v, None, label, mk=mk)

    def ev_UnaryOp(self, n, fr):
        a = self.ev(n.operand, fr)
        if isinstance(n.op, ast.USub):
            return Q(safe(lambda x: -x, a.v))
        if isinstance(n.op, ast.Not):
            return Q(safe(lambda x: not x, a.v))
        return Q(a.v, None, a.label, mk=lambda: geom_of(a))

    def ev_Compare(self, n, fr):
        left = self.ev(n.left, fr).v
        for op, rnode in zip(n.ops, n.comparators):
            right = self.ev(rnode, fr).v
            r = safe(CMPOPS.get(type(op), lambda a, b: None), left, right)
            if not r:
                return Q(r)
            left = right
        return Q(True)

    def ev_BoolOp(self, n, fr):
        is_and = isinstance(n.op, ast.And)
        v = None
        for x in n.values:
            v = self.ev(x, fr).v
            if is_and and not v:
                return Q(v)
            if not is_and and v:
                return Q(v)
        return Q(v)

    def ev_IfExp(self, n, fr):
        return self.ev(n.body if self.ev(n.test, fr).v else n.orelse, fr)

    def ev_List(self, n, fr):
        items = [self.ev(e, fr) for e in n.elts]
        g = union([place(["slot"], (k,), slot_geom(q)) for k, q in enumerate(items)])
        return Q([q.v for q in items], g)

    ev_Tuple = ev_List

    def ev_Subscript(self, n, fr):
        a, i = self.ev(n.value, fr), self.ev(n.slice, fr)
        return Q(safe(lambda x, y: x[y], a.v, i.v))

    def ev_Slice(self, n, fr):
        part = lambda x: self.ev(x, fr).v if x is not None else None
        return Q(slice(part(n.lower), part(n.upper), part(n.step)))

    def iter_items(self, q):
        v = q.v
        if isinstance(v, range):
            return [(x, x) for x in v]
        if isinstance(v, (list, tuple)):
            return [(k, x) for k, x in enumerate(v)]
        return []

    def bind(self, target, value, env, fr=None):
        if isinstance(target, ast.Name):
            env.vars[target.id] = Q(value, label=target.id)
        elif isinstance(target, (ast.Tuple, ast.List)) and isinstance(value, (list, tuple)):
            for t, x in zip(target.elts, list(value)):
                self.bind(t, x, env, fr)
        elif isinstance(target, ast.Subscript) and fr is not None:
            box, key = self.ev(target.value, fr).v, self.ev(target.slice, fr).v
            safe(lambda b, k: b.__setitem__(k, value), box, key)

    def comp_items(self, gens, fr, labels=(), coords=()):
        if not gens:
            yield labels, coords, fr
            return
        g = gens[0]
        it = self.ev(g.iter, fr)
        sub = Frame(Env(fr.env))
        for coord, x in self.iter_items(it):
            self.tick()
            self.bind(g.target, x, sub.env)
            if all(self.ev(c, sub).v for c in g.ifs):
                lab = g.target.id if isinstance(g.target, ast.Name) else self.code(g.target)
                yield from self.comp_items(gens[1:], sub, labels + (lab,), coords + (coord,))

    def ev_ListComp(self, n, fr):
        parts, vals = [], []
        for labels, coords, sub in self.comp_items(n.generators, fr):
            q = self.ev(n.elt, sub)
            vals.append(q.v)
            parts.append(place(labels, coords, slot_geom(q)))
        return Q(vals, union(parts))

    ev_GeneratorExp = ev_ListComp

    MUTATORS = {"append", "extend", "insert", "pop", "remove", "reverse", "sort"}

    def ev_Call(self, n, fr):
        if isinstance(n.func, ast.Attribute):
            recv = self.ev(n.func.value, fr)
            args = [self.ev(a, fr).v for a in n.args]
            if n.func.attr in self.MUTATORS and isinstance(recv.v, list):
                r = safe(lambda: getattr(recv.v, n.func.attr)(*args))
                recv.g, recv._cache = None, None        # slots changed: rebuild from the data
                return Q(r)
            return Q(None)
        name = n.func.id if isinstance(n.func, ast.Name) else None
        if name == "sum" and n.args:
            return self.ev_sum(n.args[0], fr)
        args = [self.ev(a, fr) for a in n.args]
        if name == "range":
            v = safe(lambda *a: range(*a), *[a.v for a in args])
            return Q(v, label=self.code(n))
        if name == "list" and args:
            return Q(safe(list, args[0].v), geom_of(args[0]))
        if name in SAFE_FUNCS:
            return Q(safe(SAFE_FUNCS[name], *[a.v for a in args]))
        target = fr.env.lookup(name) if name else None
        if target is not None and isinstance(target.v, FuncRef):
            return self.call(target.v.node, args)
        return Q(None)

    def ev_sum(self, arg, fr):                       # R5 for sum(...)
        if isinstance(arg, (ast.GeneratorExp, ast.ListComp)):
            parts, total = [], 0
            for labels, coords, sub in self.comp_items(arg.generators, fr):
                q = self.ev(arg.elt, sub)
                total = safe(lambda a, b: a + b, total, q.v)
                parts.append(place(labels, coords, geom_of(q)))
            return Q(total, union(parts))
        it = self.ev(arg, fr)
        parts, total = [], 0
        for coord, x in self.iter_items(it):
            q = Q(x, label="k")
            total = safe(lambda a, b: a + b, total, x)
            parts.append(place([it.label or "k"], (coord,), geom_of(q)))
        return Q(total, union(parts))

    def call(self, fdef, args, capture=None):
        if self.depth >= DEPTH_CAP:
            self.truncated = True
            return Q(None)
        node = {"name": fdef.name, "kids": []}
        if self.call_stack:
            if len(self.call_stack[0].get("all", ())) < NODE_CAP:
                self.call_stack[-1]["kids"].append(node)
                self.call_stack[0].setdefault("all", []).append(node)
        self.call_stack.append(node)
        fr = Frame(Env(self.globals))
        params = [a.arg for a in fdef.args.args]
        for k, p in enumerate(params):
            fr.env.vars[p] = args[k] if k < len(args) else self.free(p)
        self.depth += 1
        result = Q(None)
        try:
            self.exec_block(fdef.body, fr)
        except Ret as r:
            result = r.q
        finally:
            self.depth -= 1
            self.call_stack.pop()
            if not self.call_stack:
                self.last_root = node
        if capture is not None:
            capture.append(fr)
        return result

    # ---- statements
    def exec_block(self, stmts, fr):
        for s in stmts:
            self.exec_stmt(s, fr)

    def exec_stmt(self, s, fr):
        self.tick()
        m = getattr(self, "ex_" + type(s).__name__, None)
        if m:
            m(s, fr)

    def ex_FunctionDef(self, s, fr):
        fr.env.vars[s.name] = Q(FuncRef(s))

    def accumulate(self, name, contrib, fr, lineno):
        cur = fr.env.lookup(name) or Q(0)
        newv = safe(lambda a, b: a + b, cur.v, contrib.v)
        if fr.loops:
            acc = cur.acc
            if acc is None:
                acc = []
                if cur.v not in (0, None):
                    acc.append(((), (), geom_of(cur)))
            acc.append((tuple(l for l, _ in fr.loops), tuple(c for _, c in fr.loops),
                        geom_of(contrib)))
            fr.env.vars[name] = Q(newv, label=name, acc=acc)
            fr.touched.setdefault(name, lineno)
        else:
            fr.env.vars[name] = Q(newv, concat(geom_of(cur), geom_of(contrib)), name)

    def ex_Assign(self, s, fr):
        t = s.targets[0]
        v = s.value
        if (fr.loops and isinstance(t, ast.Name) and isinstance(v, ast.BinOp)
                and isinstance(v.op, ast.Add) and isinstance(v.left, ast.Name) and v.left.id == t.id):
            self.accumulate(t.id, self.ev(v.right, fr), fr, s.lineno)
            self.visit(fr)
            return
        q = self.ev(v, fr)
        for extra in s.targets[1:]:                  # a = b = value
            self.bind(extra, q.v, fr.env, fr)
        if isinstance(t, ast.Name):
            fr.env.vars[t.id] = q
            if fr.is_module and not fr.loops and not isinstance(v, (ast.Constant, ast.Name)):
                self.record(t.id, s.lineno, q)
        else:
            self.bind(t, q.v, fr.env, fr)
        self.visit(fr)

    def ex_AugAssign(self, s, fr):
        if isinstance(s.target, ast.Subscript):
            box, key = self.ev(s.target.value, fr).v, self.ev(s.target.slice, fr).v
            q = self.ev(s.value, fr)
            fn = BINOPS.get(type(s.op))
            safe(lambda b, k, v: b.__setitem__(k, fn(b[k], v)), box, key, q.v)
            self.visit(fr)
            return
        if not isinstance(s.target, ast.Name):
            return
        q = self.ev(s.value, fr)
        if isinstance(s.op, ast.Add):
            self.accumulate(s.target.id, q, fr, s.lineno)
        else:
            cur = fr.env.lookup(s.target.id) or Q(None)
            fn = BINOPS.get(type(s.op))
            fr.env.vars[s.target.id] = Q(safe(fn, cur.v, q.v) if fn else None)
        self.visit(fr)

    def ex_Expr(self, s, fr):
        self.ev(s.value, fr)
        self.visit(fr)

    def ex_Return(self, s, fr):
        raise Ret(self.ev(s.value, fr) if s.value is not None else Q(None))

    def ex_Break(self, s, fr):
        raise Brk()

    def ex_Continue(self, s, fr):
        raise Cont()

    def ex_If(self, s, fr):                          # R7
        self.visit(fr)                               # the test itself runs at this point
        self.exec_block(s.body if self.ev(s.test, fr).v else s.orelse, fr)

    def run_loop(self, fr, label, items, body_fn, lineno):
        outermost_module = fr.is_module and not fr.loops
        if outermost_module:
            fr.visits, fr.touched = {}, {}
        for coord, x in items:
            fr.loops.append((label, coord))
            try:
                body_fn(x)
            except Cont:
                pass
            except Brk:
                fr.loops.pop()
                break
            fr.loops.pop()
        if outermost_module:
            for name, line in fr.touched.items():
                self.record(name, line, fr.env.lookup(name))
            vg = self.visits_geom(fr)
            if vg is not None and len(normalize(vg).axes) >= 2 and not fr.touched:
                self.record("loop body", lineno, geom=vg)

    def ex_For(self, s, fr):
        it = self.ev(s.iter, fr)
        label = s.target.id if isinstance(s.target, ast.Name) else self.code(s.target)

        def body(x):
            self.bind(s.target, x, fr.env)
            self.exec_block(s.body, fr)
        self.run_loop(fr, label, self.iter_items(it), body, s.lineno)

    def ex_While(self, s, fr):
        def items():
            k = 0
            while self.ev(s.test, fr).v and k < 10_000:
                yield k, k
                k += 1
        self.run_loop(fr, "step", items(), lambda _: self.exec_block(s.body, fr), s.lineno)

    # ---- driver
    def run(self, tree):
        mod = Frame(self.globals, is_module=True)
        funcs = []
        for s in tree.body:
            try:
                self.exec_stmt(s, mod)
            except StepLimit:
                self.errors.append(f"line {s.lineno}: too many steps, skipped")
                self.steps = 0
            except Exception as e:      # keep going while the user is mid-edit
                self.errors.append(f"line {s.lineno}: {type(e).__name__}: {e}")
            if isinstance(s, ast.FunctionDef):
                funcs.append(s)
        called = set()
        for f in funcs:
            for x in ast.walk(f):
                if isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id != f.name:
                    called.add(x.func.id)
        entries = [f for f in funcs if f.name not in called] or funcs
        if self.only is not None:
            entries = [f for f in entries if f.name in self.only]
        for f in entries:
            self.steps = 0
            cap = []
            try:
                q = self.call(f, [], capture=cap)
            except StepLimit:
                self.errors.append(f"{f.name}(): too many steps, skipped")
                continue
            ret_line = next((n.lineno for n in ast.walk(f) if isinstance(n, ast.Return)), f.lineno)
            params = ", ".join(a.arg for a in f.args.args)
            if q.v is not None or q.g is not None or q.mk is not None:
                self.record(f"{f.name}({params})", ret_line, q)
            root = self.last_root
            if root is not None and tree_depth(root) >= 2:
                self.record(f"{f.name}({params}) calls", f.lineno, geom=call_geom(root))
            if cap:
                vg = self.visits_geom(cap[0])
                ret = self.structures[-1]["geom"] if self.structures and self.structures[-1]["line"] == ret_line else None
                nv = normalize(vg)
                if nv is not None and (ret is None or canon(nv) != canon(ret)):
                    loop_line = next((x.lineno for x in ast.walk(f) if isinstance(x, (ast.For, ast.While))), f.lineno)
                    self.record(f"{f.name}({params}) loop body", loop_line, geom=vg)
        if self.truncated:
            self.errors.append(f"recursion deeper than {DEPTH_CAP} was cut off")
        self.structures.sort(key=lambda d: d["line"])
        return self.structures


# --------------------------------------------------------------------------- similarity

def overrides_of(src):
    out = {}
    for m in re.finditer(r"(?m)^\s*#\s*viz:?\s+(\w+\s*=.+)$", src):
        for part in m.group(1).split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                try:
                    out[k.strip()] = int(v.strip())
                except ValueError:
                    pass
    return out


def analyze(src, size_of, only=None):
    it = Interp(src, size_of)
    it.only = only
    structs = it.run(ast.parse(src))
    return structs, it.errors, it.free_order


def fit_poly(points):
    """Exact polynomial (degree <= len-2) through points, verified on the last point."""
    xs = [Fraction(x) for x, _ in points[:-1]]
    ys = [Fraction(y) for _, y in points[:-1]]
    n = len(xs)
    A = [[x ** k for k in range(n)] + [y] for x, y in zip(xs, ys)]
    for col in range(n):
        piv = next(r for r in range(col, n) if A[r][col] != 0)
        A[col], A[piv] = A[piv], A[col]
        for r in range(n):
            if r != col and A[r][col] != 0:
                f = A[r][col] / A[col][col]
                A[r] = [a - f * b for a, b in zip(A[r], A[col])]
    coef = [A[k][n] / A[k][k] for k in range(n)]
    x, y = points[-1]
    if sum(c * Fraction(x) ** k for k, c in enumerate(coef)) != y:
        return None
    while len(coef) > 1 and coef[-1] == 0:
        coef.pop()
    return tuple(coef)


SUP = {2: "²", 3: "³", 4: "⁴"}


def fmt_poly(coef):
    den = 1
    for c in coef:
        den = den * c.denominator // __import__("math").gcd(den, c.denominator)
    ints = [int(c * den) for c in coef]
    terms = []
    for k in range(len(ints) - 1, -1, -1):
        c = ints[k]
        if c == 0:
            continue
        mag = abs(c)
        var = "" if k == 0 else "N" + SUP.get(k, "" if k == 1 else f"^{k}")
        body = (str(mag) if mag != 1 or k == 0 else "") + var
        terms.append(("-" if c < 0 else "+", body))
    if not terms:
        return "0"
    s = ("-" if terms[0][0] == "-" else "") + terms[0][1]
    for sign, body in terms[1:]:
        s += f" {sign} {body}"
    if den > 1:
        s = f"({s})/{den}" if len(terms) > 1 else f"{s}/{den}"
    return s


PALETTE = [(0.95, 0.55, 0.20), (0.25, 0.60, 0.95), (0.35, 0.80, 0.45), (0.85, 0.35, 0.65),
           (0.95, 0.80, 0.25), (0.55, 0.45, 0.90), (0.30, 0.80, 0.80), (0.90, 0.40, 0.35)]
LONE = (0.70, 0.72, 0.78)


def build_scene(src):
    """Full pipeline: display run + signature runs -> scene IR (plain dicts)."""
    ov = overrides_of(src)
    display_sizes = {}

    def disp(name, order):
        if name in ov:
            return ov[name]
        return display_sizes.setdefault(name, SIZE_POOL[order % len(SIZE_POOL)])

    structs, errors, free = analyze(src, disp)

    sig = {}
    for N in SIG_SIZES:
        ss, _, _ = analyze(src, lambda name, order, N=N: N)   # pins only affect display size
        for s in ss:
            sig.setdefault((s["title"], s["line"]), {})[N] = s

    # no polynomial law: does each step multiply the count by a steady ratio?
    growth_runs = {}
    for N in GROWTH_SIZES:
        ss, _, _ = analyze(src, lambda name, order, N=N: N)
        for s in ss:
            growth_runs.setdefault(s["title"], []).append(s["magnitude"])
    growth_titles = {}
    for title, ms in growth_runs.items():
        if len(ms) == len(GROWTH_SIZES) and all(isinstance(m, int) and m > 0 for m in ms):
            ratios = [b / a for a, b in zip(ms, ms[1:])]
            if min(ratios) > 1.2 and max(ratios) - min(ratios) < 0.03 * ratios[-1]:
                growth_titles[title] = f"≈ {ratios[-1]:.3f}^N"

    # still nothing: does doubling N add a steady amount?  (count ~ c * log2 N)
    # Large N is expensive, so only functions that still lack a law are re-run.
    lawless = set()
    for s in structs:
        runs = sig.get((s["title"], s["line"]), {})
        if "(" in s["title"] and s["title"] not in growth_titles and not (
                all(N in runs and isinstance(runs[N]["magnitude"], int) for N in SIG_SIZES)
                and fit_poly([(N, runs[N]["magnitude"]) for N in SIG_SIZES]) is not None):
            lawless.add(s["title"].split("(")[0])
    log_runs = {}
    for N in LOG_SIZES:
        if not lawless:
            break
        ss, _, _ = analyze(src, lambda name, order, N=N: N, only=lawless)
        for s in ss:
            log_runs.setdefault(s["title"], []).append(s["magnitude"])
        # more than doubling when N doubles can never be logarithmic: stop paying for it
        alive = set()
        for title, ms in log_runs.items():
            ok = all(isinstance(m, int) for m in ms) and not (len(ms) >= 2 and ms[-1] > 2 * ms[-2])
            if ok:
                alive.add(title.split("(")[0])
        lawless &= alive
    for title, ms in log_runs.items():
        if title in growth_titles or len(ms) != len(LOG_SIZES):
            continue
        if all(isinstance(m, int) and m > 0 for m in ms):
            steps = [b - a for a, b in zip(ms, ms[1:])]
            if min(steps) >= 1 and max(steps) - min(steps) <= 1:
                c = sum(steps) / len(steps)
                growth_titles[title] = "≈ log₂N" if round(c) == c == 1 else f"≈ {c:g}·log₂N"

    for s in structs:
        runs = sig.get((s["title"], s["line"]), {})
        shape_key = None
        if all(N in runs and runs[N]["geom"] is not None for N in (4, 5, 6)):
            shape_key = tuple(canon(runs[N]["geom"]) for N in (4, 5, 6))
            if shape_key[0][0] < 2:                       # every line looks like every other line
                shape_key = None
        law = None
        if all(N in runs and isinstance(runs[N]["magnitude"], int) for N in SIG_SIZES):
            law = fit_poly([(N, runs[N]["magnitude"]) for N in SIG_SIZES])
        s["shape_key"], s["law"], s["growth"] = shape_key, law, None
        if law is None and s["title"] in growth_titles:
            s["growth"] = growth_titles[s["title"]]

    # loop bodies are only worth showing when they span more than one direction
    structs = [s for s in structs if not (s["title"].endswith("loop body") and
                                          (s["geom"] is None or len(s["geom"].axes) < 2))]
    structs = [s for s in structs if not (s["title"].endswith(" calls") and
                                          (s["magnitude"] or 0) < 3)]
    shape_groups, law_groups = {}, {}
    for s in structs:
        if s["shape_key"] is not None:
            shape_groups.setdefault(s["shape_key"], []).append(s)
        if s["law"] is not None and len(s["law"]) > 2:          # linear coincidences are noise
            law_groups.setdefault(s["law"], []).append(s)

    color_i = 0
    for members in shape_groups.values():
        col = LONE
        if len(members) > 1:
            col = PALETTE[color_i % len(PALETTE)]
            color_i += 1
        for s in members:
            s["color"] = col

    out = []
    for i, s in enumerate(structs):
        g = s["geom"]
        same_shape = [t["title"] for t in shape_groups.get(s["shape_key"], []) if t is not s]
        same_law = [t["title"] for t in law_groups.get(s["law"], [])
                    if t is not s and t["title"] not in same_shape]
        regions, cells = [], []
        if g is not None:
            for c in sorted(g.cells):
                key = region_key(g.cells[c])
                if key not in regions:
                    regions.append(key)
                cells.append(list(c) + [regions.index(key)])
        if len(regions) == 1:
            cells = [c[:-1] + [0] for c in cells]
        out.append({
            "id": i,
            "title": s["title"],
            "line": s["line"],
            "code": s["code"],
            "axes": g.axes if g is not None else [],
            "cells": cells,
            "regions": regions,
            "count": s["magnitude"],
            "law": fmt_poly(s["law"]) if s["law"] is not None else s["growth"],
            "same_shape_as": same_shape,
            "same_law_as": same_law,
            "color": s.get("color", LONE),
        })
    return {"structures": out, "errors": errors,
            "inputs": {k: disp(k, i) for i, k in enumerate(free)}}


# --------------------------------------------------------------------------- CLI

def ascii_view(st):
    d = len(st["axes"])
    cells = [tuple(c[:d]) for c in st["cells"]]
    marks = {tuple(c[:d]): "#abcdefgh"[c[d] % 9] if len(st["regions"]) > 1 else "#" for c in st["cells"]}
    if d == 0:
        return "    (point)" if cells else "    (empty)"
    if d == 1:
        return "    " + "#" * len(cells)
    if d == 2:
        mx = max(c[0] for c in cells)
        my = max(c[1] for c in cells)
        s = set(map(tuple, cells))
        rows = []
        for y in range(my, -1, -1):
            rows.append("    " + "".join(marks.get((x, y), ".") for x in range(mx + 1)))
        return "\n".join(rows)
    layers = {}
    for c in cells:
        layers[c[2]] = layers.get(c[2], 0) + 1
    return "    3D layers (bottom->top): " + " ".join(str(layers[k]) for k in sorted(layers))


def main(argv):
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    path = argv[1] if len(argv) > 1 else "demo.py"
    with open(path, encoding="utf-8") as f:
        scene = build_scene(f.read())
    if "--json" in argv:
        out = argv[argv.index("--json") + 1]
        with open(out, "w", encoding="utf-8") as f:
            json.dump(scene, f)
    print("inputs:", scene["inputs"])
    for st in scene["structures"]:
        print(f"\n[{st['line']}] {st['title']}   ({st['code']})")
        print(f"    dims={len(st['axes'])} axes={st['axes']} count={st['count']} law={st['law']}")
        if len(st["regions"]) > 1:
            print("    pieces:", ", ".join(f"{'#abcdefgh'[i]}={r}" for i, r in enumerate(st["regions"])))
        if st["same_shape_as"]:
            print(f"    same shape as: {', '.join(st['same_shape_as'])}")
        if st["same_law_as"]:
            print(f"    same law as:   {', '.join(st['same_law_as'])}")
        print(ascii_view(st))
    for e in scene["errors"]:
        print("!", e)


if __name__ == "__main__":
    main(sys.argv)
