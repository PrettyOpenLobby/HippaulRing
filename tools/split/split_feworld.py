#!/usr/bin/env python3
"""Split one large flat service module into a package, one module per concern.

Derived from release/split_core.py (OpenLobby's responders.py -> core/) and
used for CrystalRing's services/feworld.py -> services/world/. It is
deterministic: the same source and the same map give the same package.

    python split_feworld.py --src <repo>/services/feworld.py --map split_feworld_map.txt \
        --out <repo>/services/world --facade <repo>/services/feworld.py
    python split_feworld.py --src ... --map ... --check    # report only, write nothing
    python split_feworld.py --src ... --ranges ranges.txt  # derive a map from line ranges

The package name is the basename of --out; the facade's module name is the
basename of --facade. --src and --facade may be the same path (the flat file
is read completely before anything is written).

Additions over split_core.py (see CHANGES at the end of this docstring).

How it works
- Every top-level statement of responders.py is assigned to one module of the
  package by the map (defs and module globals by NAME; unnamed statements by a
  `~prefix` of their first line). The comment block above a statement travels
  with it, so the section banners and the per-function commentary survive.
- Inside a moved statement, every reference to a top-level name that now lives
  in ANOTHER module is rewritten to `<module>.<name>`. Scope analysis follows
  Python's rules (function scopes nest, class bodies do not), so a local that
  shadows a module name is left alone. There are no `global` statements in the
  source, so no function rebinds a module name.
- Import-like statements (plain imports, and `try: import x / except: x = None`
  blocks) go to core/deps.py; a module that uses such a name gets the plain
  import, or `from .deps import x` for the optional ones.
- The old services/responders.py becomes a facade: `import responders as R`
  keeps working for every tool and test, reads AND writes (`R._session_get = fake`)
  are forwarded to the owning module, and `python responders.py <modes>` still
  starts the server.

CHANGES over split_core.py
- Names are not hardcoded: package = basename of --out, facade module =
  basename of --facade. The facade keeps the flat file's own docstring
  verbatim (argparse's --help prints it), then says it is a facade in a
  comment.
- `[__facade__]` map section: statements that stay in the facade (by name or
  ~prefix), emitted verbatim after the docstring. feworld keeps its dead
  `if False:` import block there, which a test and the deploy stale-check read.
- `[deps]` map section may own non-import statements too (feworld's `_HERE`
  and its sys.path insert); they are emitted in source order among the
  imports.
- `global X` is supported. A function that declares a name global which now
  lives in another module has its loads and stores rewritten to
  `<module>.X`, and the name is dropped from the `global` statement (the line
  goes if nothing is left). A name global in its own module is untouched.
- `globals()["X"]` with a constant key naming another module's name becomes
  `<module>.X`.
- `__file__` in moved code meant the flat file: it becomes deps.FACADE_FILE
  (the facade's path). `sys.modules[__name__]` meant the whole module: it
  becomes deps.facade(). `__doc__` becomes deps.facade().__doc__.
- Import-time safety: a module-level statement that uses another package
  module while the package is being imported is only safe if that module is a
  SAFE: deps, or a module that imports only safe modules (so its import
  closure is acyclic). Anything else can meet a half-initialised module
  depending on import order, so the tool refuses.
- The module docstring is found as the first statement even after a
  shebang line.
- Blank lines above a moved statement are capped at two (a statement that
  used to follow code of another module no longer drags its gap along).
- A local variable named like a package module the function's module imports
  is reported whether or not that function uses the module (stricter).
- A map entry naming nothing in the source (STALE MAP ENTRY) and a module
  named like a top-level name (MODULE NAME) are refused.
"""
import argparse
import ast
import collections
import io
import os
import re
import sys

BANNER = re.compile(r"^# -{20,} #\s*$")


# --------------------------------------------------------------------------- #
# Source model
# --------------------------------------------------------------------------- #
class Stmt:
    __slots__ = ("node", "names", "kind", "first", "last", "span_first", "text", "module")

    def __init__(self, node, names, kind, first, last):
        self.node = node
        self.names = names        # top-level names this statement binds
        self.kind = kind          # def | assign | import | optimport | other
        self.first = first        # first line of the statement proper
        self.last = last
        self.span_first = None    # first line including the comment block above
        self.text = None
        self.module = None


def bound_targets(target):
    out = []
    for n in ast.walk(target):
        if isinstance(n, ast.Name):
            out.append(n.id)
    return out


def classify(node):
    """(names, kind) for a top-level statement."""
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return [node.name], "def"
    if isinstance(node, ast.Assign):
        names = []
        for t in node.targets:
            names += bound_targets(t)
        return names, "assign"
    if isinstance(node, ast.AnnAssign):
        return bound_targets(node.target), "assign"
    if isinstance(node, ast.AugAssign):
        return [], "other"
    if isinstance(node, (ast.Import, ast.ImportFrom)):
        return [(a.asname or a.name).split(".")[0] for a in node.names], "import"
    if isinstance(node, ast.Try):
        body_imports = all(isinstance(b, (ast.Import, ast.ImportFrom)) for b in node.body)
        if body_imports and node.handlers:
            names = []
            for b in node.body:
                names += [(a.asname or a.name).split(".")[0] for a in b.names]
            return names, "optimport"
    return [], "other"


def load_source(path):
    text = open(path, encoding="utf-8").read()
    lines = text.splitlines(keepends=True)
    tree = ast.parse(text)
    stmts = []
    for node in tree.body:
        names, kind = classify(node)
        first = node.lineno
        # decorators sit above the def line
        for d in getattr(node, "decorator_list", []):
            first = min(first, d.lineno)
        stmts.append(Stmt(node, names, kind, first, node.end_lineno))
    prev_end = 0
    for s in stmts:
        s.span_first = prev_end + 1
        s.text = "".join(lines[s.span_first - 1:s.last])
        prev_end = s.last
    trailing = "".join(lines[prev_end:])
    return text, lines, tree, stmts, trailing


# --------------------------------------------------------------------------- #
# The map
# --------------------------------------------------------------------------- #
def read_map(path):
    """{module: {"doc": str, "names": set, "prefixes": [str]}} in file order."""
    mods = collections.OrderedDict()
    cur = None
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\[([A-Za-z_][A-Za-z0-9_]*)\]\s*(.*)$", line)
        if m:
            cur = m.group(1)
            mods[cur] = {"doc": m.group(2).strip(), "names": set(), "prefixes": []}
            continue
        if cur is None:
            raise SystemExit(f"{path}: entry before any [module] header: {line!r}")
        if line.startswith("~"):
            mods[cur]["prefixes"].append(line[1:].rstrip())
        else:
            for tok in line.split():
                mods[cur]["names"].add(tok)
    return mods


def assign_modules(stmts, mods, facade_prefixes):
    owner = {}
    for mod, spec in mods.items():
        for n in spec["names"]:
            if n in owner:
                raise SystemExit(f"map: {n} listed in both {owner[n]} and {mod}")
            owner[n] = mod
    unmapped = []
    for s in stmts:
        head = s.text.splitlines()[-(s.last - s.first + 1)] if s.text else ""
        first_line = "".join(s.text.splitlines(keepends=True)[s.first - s.span_first:s.first - s.span_first + 1]).rstrip()
        if s.kind in ("import", "optimport"):
            s.module = "deps"
            continue
        if any(first_line.startswith(p) for p in facade_prefixes):
            s.module = "__facade__"
            continue
        hit = None
        for mod, spec in mods.items():
            if any(first_line.startswith(p) for p in spec["prefixes"]):
                hit = mod
                break
        if hit is None and s.names:
            owners = {owner.get(n) for n in s.names}
            owners.discard(None)
            if len(owners) > 1:
                raise SystemExit(f"L{s.first}: names {s.names} map to several modules {owners}")
            if owners:
                hit = owners.pop()
        if hit is None:
            if s.kind == "other" and isinstance(s.node, ast.Expr) and isinstance(
                    getattr(s.node, "value", None), ast.Constant) and s is stmts[0]:
                s.module = "__facade__"      # the module docstring
                continue
            unmapped.append(s)
            continue
        s.module = hit
    return owner, unmapped


# --------------------------------------------------------------------------- #
# Scope analysis
# --------------------------------------------------------------------------- #
def local_bindings(scope_node):
    """Names bound directly in this function/lambda/comprehension scope."""
    bound = set()
    args = getattr(scope_node, "args", None)
    if args is not None:
        for a in args.posonlyargs + args.args + args.kwonlyargs:
            bound.add(a.arg)
        if args.vararg:
            bound.add(args.vararg.arg)
        if args.kwarg:
            bound.add(args.kwarg.arg)
    if isinstance(scope_node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for g in scope_node.generators:
            bound |= set(bound_targets(g.target))

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                bound.add(c.name)
                for d in c.decorator_list:
                    walk(d)
                if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    for d in c.args.defaults + c.args.kw_defaults:
                        if d is not None:
                            walk(d)
                continue
            if isinstance(c, ast.Lambda):
                for d in c.args.defaults + c.args.kw_defaults:
                    if d is not None:
                        walk(d)
                continue
            if isinstance(c, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                # the first iterable is evaluated in the enclosing scope
                walk(c.generators[0].iter)
                continue
            if isinstance(c, ast.Name) and isinstance(c.ctx, (ast.Store, ast.Del)):
                bound.add(c.id)
            elif isinstance(c, ast.ExceptHandler) and c.name:
                bound.add(c.name)
            elif isinstance(c, (ast.Import, ast.ImportFrom)):
                for a in c.names:
                    bound.add((a.asname or a.name).split(".")[0])
            elif isinstance(c, ast.NamedExpr):
                bound.add(c.target.id)
            elif isinstance(c, ast.MatchAs) and c.name:
                bound.add(c.name)
            elif isinstance(c, ast.MatchStar) and c.name:
                bound.add(c.name)
            elif isinstance(c, ast.MatchMapping) and c.rest:
                bound.add(c.rest)
            walk(c)

    if isinstance(scope_node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
        for g in scope_node.generators:
            for cond in g.ifs:
                walk(cond)
            if g is not scope_node.generators[0]:
                walk(g.iter)
        if isinstance(scope_node, ast.DictComp):
            walk(scope_node.key)
            walk(scope_node.value)
        else:
            walk(scope_node.elt)
    else:
        walk(scope_node)
    # `global X` makes X module scope for this function even where it is
    # assigned; nested functions keep their own declarations
    return bound - declared_globals(scope_node)


def declared_globals(scope_node):
    """Names a function scope declares `global` (not counting nested scopes)."""
    out = set()

    def walk(n):
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Lambda)):
                continue
            if isinstance(c, ast.Global):
                out.update(c.names)
            walk(c)
    if isinstance(scope_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        walk(scope_node)
    return out


def global_name_refs(stmt_node):
    """Yield every Name node in the statement that resolves to MODULE scope."""
    out = []

    def visit(n, fn_scopes, in_class):
        # fn_scopes: list of sets of names bound in enclosing FUNCTION scopes
        for c in ast.iter_child_nodes(n):
            if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                for d in getattr(c, "decorator_list", []):
                    visit_expr(d, fn_scopes)
                for d in c.args.defaults + c.args.kw_defaults:
                    if d is not None:
                        visit_expr(d, fn_scopes)
                for a in c.args.posonlyargs + c.args.args + c.args.kwonlyargs:
                    if a.annotation is not None:
                        visit_expr(a.annotation, fn_scopes)
                if getattr(c, "returns", None) is not None:
                    visit_expr(c.returns, fn_scopes)
                inner = local_bindings(c)
                body = c.body if isinstance(c.body, list) else [c.body]
                for b in body:
                    visit(b, fn_scopes + [inner], False)
                    if isinstance(b, ast.Name):
                        resolve(b, fn_scopes + [inner])
                continue
            if isinstance(c, ast.ClassDef):
                visit_class(c, fn_scopes)
                continue
            if isinstance(c, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
                visit(c.generators[0].iter, fn_scopes, in_class)
                if isinstance(c.generators[0].iter, ast.Name):
                    resolve(c.generators[0].iter, fn_scopes)
                inner = local_bindings(c)
                scopes = fn_scopes + [inner]
                parts = [g.ifs for g in c.generators] + [[g.iter] for g in c.generators[1:]]
                elts = [c.key, c.value] if isinstance(c, ast.DictComp) else [c.elt]
                for group in parts + [elts]:
                    for p in group:
                        visit(p, scopes, False)
                        if isinstance(p, ast.Name):
                            resolve(p, scopes)
                continue
            if isinstance(c, ast.Name):
                resolve(c, fn_scopes)
            visit(c, fn_scopes, in_class)

    def visit_expr(node, fn_scopes):
        if isinstance(node, ast.Name):
            resolve(node, fn_scopes)
        else:
            visit(node, fn_scopes, False)

    def resolve(name_node, fn_scopes):
        for sc in fn_scopes:
            if name_node.id in sc:
                return
        out.append(name_node)

    def visit_class(c, fn_scopes):
        for d in c.decorator_list:
            visit_expr(d, fn_scopes)
        for b in c.bases + [k.value for k in c.keywords]:
            visit_expr(b, fn_scopes)
        # names assigned directly in the class body are class attributes: they
        # shadow module names for the body's own statements, not for methods
        class_bound = set()
        for b in c.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                class_bound.add(b.name)
                continue
            for n in ast.walk(b):
                if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
                    class_bound.add(n.id)
        for b in c.body:
            if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
                visit(ast.Module(body=[b], type_ignores=[]), fn_scopes, False)
            else:
                visit(b, fn_scopes + [class_bound], True)
                if isinstance(b, ast.Name):
                    resolve(b, fn_scopes + [class_bound])

    if isinstance(stmt_node, ast.Name):
        out.append(stmt_node)
        return out
    if isinstance(stmt_node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
        # the statement IS a function: its body runs in its own local scope
        for d in getattr(stmt_node, "decorator_list", []):
            visit_expr(d, [])
        for d in stmt_node.args.defaults + stmt_node.args.kw_defaults:
            if d is not None:
                visit_expr(d, [])
        for a in stmt_node.args.posonlyargs + stmt_node.args.args + stmt_node.args.kwonlyargs:
            if a.annotation is not None:
                visit_expr(a.annotation, [])
        if getattr(stmt_node, "returns", None) is not None:
            visit_expr(stmt_node.returns, [])
        inner = local_bindings(stmt_node)
        for b in stmt_node.body:
            visit(b, [inner], False)
        return out
    if isinstance(stmt_node, ast.ClassDef):
        visit_class(stmt_node, [])
        return out
    visit(stmt_node, [], False)
    return out


def inside_fstring(tree_stmt):
    """Set of id()s of Name nodes that sit inside f-strings (positions unreliable < 3.12)."""
    ids = set()
    for n in ast.walk(tree_stmt):
        if isinstance(n, ast.JoinedStr):
            for m in ast.walk(n):
                if isinstance(m, ast.Name):
                    ids.add(id(m))
    return ids


# --------------------------------------------------------------------------- #
# Rewriting
# --------------------------------------------------------------------------- #
def rewrite_statement(stmt, owner, lines, import_names, report):
    """Return the statement text with cross-module names qualified, plus the
    set of modules it references and the import-like names it uses."""
    edits = []          # (lineno, col, end_col, new); new=None deletes the line
    deps = set()
    used_imports = set()
    fstr = inside_fstring(stmt.node)
    skip = set()        # id()s of Name nodes already covered by a wider edit

    def qual(mod, ident):
        return ident if mod == stmt.module else f"{mod}.{ident}"

    def span_edit(node, new):
        if node.lineno != node.end_lineno:
            report.append(f"L{node.lineno}: multi-line construct -- fix by hand")
            return
        edits.append((node.lineno, node.col_offset, node.end_col_offset, new))
        for m in ast.walk(node):
            skip.add(id(m))

    for n in ast.walk(stmt.node):
        # sys.modules[__name__] -> deps.facade()
        if (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Attribute)
                and isinstance(n.value.value, ast.Name) and n.value.value.id == "sys"
                and n.value.attr == "modules" and isinstance(n.slice, ast.Name)
                and n.slice.id == "__name__"):
            span_edit(n, qual("deps", "facade") + "()")
            deps.add("deps")
        # globals()["X"] -> <owner>.X
        elif (isinstance(n, ast.Subscript) and isinstance(n.value, ast.Call)
                and isinstance(n.value.func, ast.Name) and n.value.func.id == "globals"
                and not n.value.args and isinstance(n.slice, ast.Constant)
                and isinstance(n.slice.value, str)):
            mod = owner.get(n.slice.value)
            if mod is None:
                report.append(f"L{n.lineno}: globals()[{n.slice.value!r}] names no top-level name")
            elif mod != stmt.module:
                span_edit(n, f"{mod}.{n.slice.value}")
                deps.add(mod)
        elif isinstance(n, ast.Name) and n.id == "__file__":
            span_edit(n, qual("deps", "FACADE_FILE"))
            deps.add("deps")
        elif isinstance(n, ast.Name) and n.id == "__doc__":
            span_edit(n, qual("deps", "facade") + "().__doc__")
            deps.add("deps")
        elif isinstance(n, ast.Global):
            moved = [g for g in n.names if owner.get(g) not in (None, stmt.module)]
            if not moved:
                continue
            keep = [g for g in n.names if g not in moved]
            line = lines[n.lineno - 1]
            if keep:
                span_edit(n, "global " + ", ".join(keep))
            elif line.strip() == line[n.col_offset:n.end_col_offset].strip() and \
                    n.lineno == n.end_lineno:
                edits.append((n.lineno, 0, len(line), None))
            else:
                report.append(f"L{n.lineno}: `global` shares its line -- fix by hand")
    if deps & {"deps"} and stmt.module == "deps":
        deps.discard("deps")
    for nm in global_name_refs(stmt.node):
        if id(nm) in skip:
            continue
        ident = nm.id
        if ident in import_names:
            used_imports.add(ident)
            continue
        mod = owner.get(ident)
        if mod is None or mod == stmt.module:
            continue
        if isinstance(nm.ctx, ast.Store) and stmt.module != "boot" and stmt.module != "__facade__":
            report.append(f"L{nm.lineno}: {stmt.module} STORES {ident} owned by {mod}")
        line = lines[nm.lineno - 1]
        if line[nm.col_offset:nm.end_col_offset] != ident or id(nm) in fstr and False:
            report.append(f"L{nm.lineno}:{nm.col_offset} position mismatch for {ident} "
                          f"(f-string?) -- fix by hand: {line.strip()[:80]}")
            continue
        if id(nm) in fstr:
            # position verified above; f-string names are rewritten too
            pass
        edits.append((nm.lineno, nm.col_offset, nm.end_col_offset, f"{mod}.{ident}"))
        deps.add(mod)
    # apply edits right-to-left per line
    text_lines = lines[stmt.span_first - 1:stmt.last]
    by_line = collections.defaultdict(list)
    for ln, c0, c1, new in edits:
        by_line[ln].append((c0, c1, new))
    for ln, eds in by_line.items():
        idx = ln - stmt.span_first
        s = text_lines[idx]
        if any(new is None for _c0, _c1, new in eds):
            text_lines[idx] = ""        # a `global` line with nothing left to declare
            continue
        for c0, c1, new in sorted(eds, key=lambda e: e[0], reverse=True):
            s = s[:c0] + new + s[c1:]
        text_lines[idx] = s
    return "".join(text_lines), deps, used_imports


def module_docstring(doc):
    return '"""' + doc + '"""\n' if doc else ""


def import_lines_for(used, import_stmts, optional_names):
    """Emit the import statements a module needs, in the head's order."""
    plain = []
    optional = sorted(n for n in used if n in optional_names)
    seen = set()
    for names, text, is_from in import_stmts:
        want = [n for n in names if n in used and n not in optional_names]
        if not want:
            continue
        if is_from and len(names) > 1:
            mod = re.match(r"\s*from\s+(\S+)\s+import", text).group(1)
            plain.append(f"from {mod} import {', '.join(sorted(want))}\n")
        else:
            plain.append(text.strip() + "\n")
    out = "".join(plain)
    if optional:
        out += f"from .deps import {', '.join(optional)}\n"
    return out


FACADE_TEMPLATE = '''{docstring}
# ----------------------------------------------------------------------------
# THIS FILE IS A FACADE. The code lives in the `{package}` package beside it,
# one module per concern ({package}/__init__.py lists them). This module is
# still the entry point (`python {facade}.py ...`), and `import {facade}` still
# resolves every name, reading or writing, to the module that owns it, so the
# extension modules (which receive this module in register()), the tools and
# the tests keep working unchanged. Generated by tools/split/split_feworld.py
# from the flat file and tools/split/split_feworld_map.txt.
# ----------------------------------------------------------------------------
import sys
import types

# ONE COPY OF THIS MODULE. The container runs `python {facade}.py`, so the
# running copy is `__main__`; a later `import {facade}` would otherwise load a
# second copy.
if __name__ == "__main__":
    sys.modules.setdefault("{facade}", sys.modules[__name__])

from {package} import (  # noqa: E402,F401
{module_imports}
)
from {package}.{main_module} import main  # noqa: E402
{facade_head}
# Which {package} module owns each top-level name of the old {facade}.py.
_OWNERS = {{
{owners}
}}
_MODULES = {{
{modules_dict}
}}


class _Facade(types.ModuleType):
    """`{facade}.<name>` reads and writes go to the owning module."""

    def __getattr__(self, name):
        mod = _OWNERS.get(name)
        if mod is None:
            raise AttributeError(f"module '{facade}' has no attribute {{name!r}}")
        return getattr(_MODULES[mod], name)

    def __setattr__(self, name, value):
        mod = _OWNERS.get(name)
        if mod is None:
            super().__setattr__(name, value)
            return
        setattr(_MODULES[mod], name, value)
        if mod == "deps":
            # an imported name (socket, fenet, ...) is a copy in every module
            # that imported it; a patch has to reach each copy
            for other in _MODULES.values():
                if other is not deps and hasattr(other, name):
                    setattr(other, name, value)

    def __dir__(self):
        return sorted(set(super().__dir__()) | set(_OWNERS))


sys.modules[__name__].__class__ = _Facade

if __name__ == "__main__":
    main()
'''

DEPS_PREAMBLE = '''import os as _os
import sys as _sys

#: The facade's path ({facade}.py beside this package): what `__file__` meant
#: in the flat file.
FACADE_FILE = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
                            "{facade}.py")


def facade():
    """The {facade} module object: what `sys.modules[__name__]` meant in the flat file."""
    return _sys.modules["{facade}"]


'''


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", required=True)
    ap.add_argument("--map")
    ap.add_argument("--out")
    ap.add_argument("--facade")
    ap.add_argument("--main-module", default="main",
                    help="the package module whose main() the facade runs")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--ranges", help="derive a map from 'lo hi module' lines and print it")
    args = ap.parse_args()

    text, lines, tree, stmts, trailing = load_source(args.src)

    if args.ranges:
        ranges = []
        for raw in open(args.ranges, encoding="utf-8"):
            raw = raw.split("#")[0].strip()
            if not raw:
                continue
            lo, hi, mod = raw.split()
            ranges.append((int(lo), int(hi), mod))
        per = collections.OrderedDict()
        for lo, hi, mod in ranges:
            per.setdefault(mod, [])
        for s in stmts:
            if s.kind in ("import", "optimport") or not s.names:
                continue
            for lo, hi, mod in ranges:
                if lo <= s.first <= hi:
                    per[mod].extend(s.names)
                    break
            else:
                print(f"# UNCOVERED L{s.first}: {s.names}", file=sys.stderr)
        for mod, names in per.items():
            print(f"[{mod}]")
            buf = ""
            for n in names:
                if len(buf) + len(n) + 1 > 96:
                    print(buf.rstrip())
                    buf = ""
                buf += n + " "
            if buf:
                print(buf.rstrip())
            print()
        return

    package = os.path.basename(os.path.normpath(args.out)) if args.out else "pkg"
    facade_name = os.path.splitext(os.path.basename(args.facade))[0] if args.facade else "facade"
    mods = read_map(args.map)
    facade_spec = mods.pop("__facade__", {"names": set(), "prefixes": []})
    if facade_spec["names"]:
        raise SystemExit("map: [__facade__] takes ~prefixes only")
    if "deps" not in mods:
        mods["deps"] = {"doc": "Imports shared by the package modules.",
                        "names": set(), "prefixes": []}
    facade_prefixes = ["if __name__ == \"__main__\":"] + facade_spec["prefixes"]
    owner, unmapped = assign_modules(stmts, mods, facade_prefixes)

    # import-like names and their statements
    import_stmts = []       # (names, text, is_from)
    optional_names = set()
    import_names = set()
    for s in stmts:
        if s.kind == "import":
            import_stmts.append((s.names, "".join(lines[s.first - 1:s.last]),
                                 isinstance(s.node, ast.ImportFrom)))
            import_names |= set(s.names)
        elif s.kind == "optimport":
            optional_names |= set(s.names)
            import_names |= set(s.names)
    # names bound in deps are owned by deps for the facade
    for n in import_names:
        owner.setdefault(n, "deps")

    report = []
    if unmapped:
        for s in unmapped:
            first_line = lines[s.first - 1].rstrip()
            report.append(f"UNMAPPED L{s.first}-{s.last} {s.kind} {s.names or ''}: {first_line[:90]}")
    mapped_names = set()
    for spec in mods.values():
        mapped_names |= spec["names"]
    bound_names = set()
    for s in stmts:
        bound_names |= set(s.names)
    for n in sorted(mapped_names - bound_names):
        report.append(f"STALE MAP ENTRY {n}: not a top-level name of the source")

    # build module bodies
    bodies = collections.OrderedDict((m, []) for m in mods)
    mod_deps = collections.defaultdict(set)
    mod_imports = collections.defaultdict(set)
    facade_head = []
    stats = collections.Counter()
    for s in stmts:
        if s.module is None:
            continue
        if s.module == "__facade__":
            facade_head.append(s)
            continue
        if s.kind in ("import", "optimport"):
            if s.kind == "optimport" or s.module == "deps":
                bodies["deps"].append("".join(lines[s.span_first - 1:s.last]))
            continue
        new_text, deps, used = rewrite_statement(s, owner, lines, import_names, report)
        # the blank lines above a statement: at most two, none at a module's top
        stripped = new_text.lstrip("\n")
        gap = len(new_text) - len(stripped)
        gap = min(gap, 2) if bodies[s.module] else 0
        new_text = "\n" * gap + stripped
        bodies[s.module].append(new_text)
        mod_deps[s.module] |= deps
        mod_imports[s.module] |= used
        stats[s.module] += 1
    deps_uses_helpers = any("FACADE_FILE" in b or "facade()" in b
                            for m in bodies for b in bodies[m])

    # module-name collisions with local variables: a function in module A that
    # binds a local named like module B while A imports B
    for s in stmts:
        if s.module in (None, "__facade__", "deps") or s.kind != "def":
            continue
        for fn in ast.walk(s.node):
            if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                loc = local_bindings(fn)
                clash = loc & mod_deps[s.module]
                for c in sorted(clash):
                    report.append(f"L{fn.lineno}: local {c!r} in {s.module}."
                                  f"{getattr(fn, 'name', '<lambda>')} shadows module {c} "
                                  f"that the module imports")
    # a module named like a top-level name would be shadowed by `from . import`
    for m in mods:
        if m in owner:
            report.append(f"MODULE NAME {m} is also a top-level name owned by {owner[m]}")

    # import-time dependencies (module-level non-def statements using other modules)
    import_time = collections.defaultdict(set)

    def def_time_refs(node):
        """Names evaluated when a def/class statement itself executes."""
        exprs = list(getattr(node, "decorator_list", []))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exprs += [d for d in node.args.defaults + node.args.kw_defaults if d is not None]
            exprs += [a.annotation for a in node.args.posonlyargs + node.args.args
                      + node.args.kwonlyargs if a.annotation is not None]
            if node.returns is not None:
                exprs.append(node.returns)
        elif isinstance(node, ast.ClassDef):
            exprs += node.bases + [k.value for k in node.keywords]
            for b in node.body:
                if isinstance(b, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    exprs += def_time_exprs(b)
                else:
                    exprs.append(b)
        out = []
        for e in exprs:
            out += global_name_refs(e)
        return out

    def def_time_exprs(node):
        exprs = list(getattr(node, "decorator_list", []))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            exprs += [d for d in node.args.defaults + node.args.kw_defaults if d is not None]
        return exprs

    for s in stmts:
        if s.module in (None, "__facade__", "deps"):
            continue
        refs = def_time_refs(s.node) if s.kind == "def" else global_name_refs(s.node)
        for nm in refs:
            mod = owner.get(nm.id)
            if mod and mod not in (s.module, "deps") and nm.id not in import_names:
                import_time[s.module].add(f"{mod}.{nm.id}")
    # a module is SAFE when everything it imports is safe (deps is): its import
    # closure is then acyclic, so once its body starts it runs to the end before
    # anything can import it half-done. Only a safe module may be used while
    # another module's body is being executed.
    leaves = {"deps"}
    grew = True
    while grew:
        grew = False
        for m in mods:
            if m not in leaves and not (mod_deps[m] - leaves - {m}) and m not in import_time:
                leaves.add(m)
                grew = True
    for a, bs in sorted(import_time.items()):
        for b in sorted({x.split(".")[0] for x in bs}):
            if b not in leaves:
                used = sorted(x for x in bs if x.startswith(b + "."))
                report.append(f"IMPORT-TIME UNSAFE {a} -> {b}: {b} imports a module that is not safe "
                              f"({', '.join(used)})")

    print(f"statements: {len(stmts)}  modules: {len(bodies)}  unmapped: {len(unmapped)}")
    for m, c in stats.most_common():
        print(f"  {c:>4}  {m}  (uses: {', '.join(sorted(mod_deps[m]))})")
    if import_time:
        print("import-time dependencies:")
        for a, bs in sorted(import_time.items()):
            print(f"  {a} -> {', '.join(sorted(bs))}")
    if report:
        print("\nREPORT (%d):" % len(report))
        for r in report:
            print("  " + r)
    if args.check or not args.out:
        return
    fatal = ("IMPORT-TIME UNSAFE", "UNMAPPED", "STALE MAP ENTRY", "MODULE NAME")
    if unmapped or any(r.startswith(fatal) or "shadows module" in r or "by hand" in r
                       or "position mismatch" in r or "names no top-level" in r
                       for r in report):
        raise SystemExit("refusing to write: fix the report first")

    # the facade keeps the flat file's shebang and docstring verbatim
    docstring = ""
    if facade_head and isinstance(facade_head[0].node, ast.Expr) and facade_head[0].first <= 2:
        docstring = "".join(lines[:facade_head[0].last]).rstrip("\n")
        facade_head = facade_head[1:]
    # the __main__ guard is the template's own; keep the other statements
    head_text = "".join(
        s.text for s in facade_head
        if lines[s.first - 1].rstrip() != 'if __name__ == "__main__":')

    os.makedirs(args.out, exist_ok=True)
    order = list(mods)  # map order = import order; deps first
    if "deps" in order:
        order.remove("deps")
    order.insert(0, "deps")
    for m in order:
        spec = mods[m]
        buf = io.StringIO()
        buf.write(module_docstring(spec["doc"]))
        if m == "deps":
            if deps_uses_helpers:
                buf.write(DEPS_PREAMBLE.format(facade=facade_name))
            buf.write("".join(bodies["deps"]))
        else:
            imp = import_lines_for(mod_imports[m], import_stmts, optional_names)
            buf.write(imp)
            deps = sorted(mod_deps[m])
            if deps:
                buf.write("from . import " + ", ".join(deps) + "\n")
            buf.write("\n")
            buf.write("".join(bodies[m]))
        content = buf.getvalue()
        if not content.endswith("\n"):
            content += "\n"
        with open(os.path.join(args.out, m + ".py"), "w", encoding="utf-8", newline="\n") as f:
            f.write(content)
    # package init: the layout, in map order, with each module's docstring
    with open(os.path.join(args.out, "__init__.py"), "w", encoding="utf-8", newline="\n") as f:
        f.write(f'"""The {package} package: {facade_name}.py split into one module per concern.\n\n')
        for m in order:
            f.write(f"    {m + '.py':<18} {mods[m]['doc']}\n")
        f.write(f"\n{facade_name}.py (one directory up) is the entry point and the compatibility\n"
                'facade over these modules.\n"""\n')
    # facade
    if args.facade:
        owners_lines = "\n".join(f"    {n!r}: {m!r}," for n, m in sorted(owner.items()))
        facade = FACADE_TEMPLATE.format(
            docstring=docstring,
            package=package,
            facade=facade_name,
            main_module=args.main_module,
            module_imports="\n".join(f"    {m}," for m in order),
            facade_head=("\n" + head_text.strip("\n") + "\n\n") if head_text.strip() else "\n",
            owners=owners_lines,
            modules_dict="\n".join(f"    {m!r}: {m}," for m in order),
        )
        with open(args.facade, "w", encoding="utf-8", newline="\n") as f:
            f.write(facade)
    print("written:", args.out, "and", args.facade)


if __name__ == "__main__":
    main()
