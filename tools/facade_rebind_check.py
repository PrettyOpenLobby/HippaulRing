#!/usr/bin/env python3
"""Prove that monkeypatching through the feworld facade still reaches the code.

    python tools/facade_rebind_check.py        # fail on any rebinding the facade does not forward
    python tools/facade_rebind_check.py -v     # also list every rebinding found
    python tools/facade_rebind_check.py --selftest   # positive control: with the
                                               # forwarding switched off, every name must FAIL

WHY. services/feworld.py is a facade over the services/world package. Reads of
`feworld.<name>` go to the module that owns the name. That is not enough for
the self-tests: they REBIND names to capture output or fake a clock,

    feworld.send = fake_send              # tools/fe_ui_test.py

and a rebinding that lands on the facade alone would leave every caller inside
the package still calling the real function. Nothing would raise and the test
would keep passing while testing nothing. The same goes for an extension
module, which receives the facade in register(fw) and may assign `fw.<name>`.

The facade therefore forwards writes to the owning module, and for a name the
modules import by copy (`socket`, `fenet`, ...) to every module that holds a
copy. This tool checks that promise against the rebindings the code actually
makes, empirically: it sets a sentinel through the facade and reads it back
through the owning module and every module that has the name.

Found as rebindings:
  <alias>.<name> = ...  and  <alias>.<name> += ...   (alias = any name bound
      by `import feworld [as x]`, or the parameter of an extension's register())
  setattr(<alias>, "<name>", ...)
  setattr(<alias>, <variable>, ...)   every string constant in the enclosing
      function that names a facade attribute counts (the tests restore a saved
      dict this way)
"""
import argparse
import ast
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SERVICES = os.path.join(ROOT, "services")
SCAN_DIRS = ("tools", "services")
FACADE = "feworld"


def module_aliases(tree):
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == FACADE:
                    out.add(a.asname or a.name)
        # an extension module: def register(fw): the parameter is the facade
        if isinstance(node, ast.FunctionDef) and node.name == "register" and node.args.args:
            out.add(node.args.args[0].arg)
            for sub in ast.walk(node):
                if isinstance(sub, ast.Global):
                    out.update(sub.names)
    return out


def rebindings(owned):
    """[(file, line, attr)] for every rebinding of a facade attribute."""
    found = []
    for d in SCAN_DIRS:
        base = os.path.join(ROOT, d)
        if not os.path.isdir(base):
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # the package itself assigns its own globals; only callers count
            dirnames[:] = [x for x in dirnames if x not in ("world", "__pycache__")]
            for fn in sorted(filenames):
                if not fn.endswith(".py") or fn == FACADE + ".py":
                    continue
                path = os.path.join(dirpath, fn)
                rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
                try:
                    tree = ast.parse(open(path, encoding="utf-8").read())
                except (SyntaxError, UnicodeDecodeError):
                    continue
                aliases = module_aliases(tree)
                if not aliases:
                    continue
                parents = {}
                for node in ast.walk(tree):
                    for c in ast.iter_child_nodes(node):
                        parents[c] = node
                for node in ast.walk(tree):
                    targets = []
                    if isinstance(node, ast.Assign):
                        targets = node.targets
                    elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                        targets = [node.target]
                    for tgt in targets:
                        for t in ast.walk(tgt):
                            if (isinstance(t, ast.Attribute) and isinstance(t.ctx, ast.Store)
                                    and isinstance(t.value, ast.Name)
                                    and t.value.id in aliases):
                                found.append((rel, node.lineno, t.attr))
                    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                            and node.func.id == "setattr" and len(node.args) >= 2
                            and isinstance(node.args[0], ast.Name)
                            and node.args[0].id in aliases):
                        key = node.args[1]
                        if isinstance(key, ast.Constant) and isinstance(key.value, str):
                            found.append((rel, node.lineno, key.value))
                            continue
                        scope = node
                        while scope in parents and not isinstance(
                                scope, (ast.FunctionDef, ast.AsyncFunctionDef)):
                            scope = parents[scope]
                        names = {c.value for c in ast.walk(scope)
                                 if isinstance(c, ast.Constant) and isinstance(c.value, str)
                                 and c.value in owned}
                        for n in sorted(names):
                            found.append((rel, node.lineno, n))
    return found


def forwards(W, attr):
    """Does a write of `attr` through the facade reach the owning module (and
    every module holding a copy)? Returns a reason string on failure."""
    owners = W._OWNERS
    modules = W._MODULES
    home = owners.get(attr)
    if home is None:
        return "not a name of the old feworld.py; the patch lands on the facade only"
    sentinel = object()
    saved = {m: vars(mod)[attr] for m, mod in modules.items() if attr in vars(mod)}
    try:
        setattr(W, attr, sentinel)
        if getattr(modules[home], attr, None) is not sentinel:
            return f"write did not reach the owner world.{home}"
        for m, mod in modules.items():
            if m in saved and getattr(mod, attr) is not sentinel:
                return f"world.{m} still holds the old copy"
        if getattr(W, attr) is not sentinel:
            return "read-back through the facade returned something else"
    finally:
        for m, old in saved.items():
            setattr(modules[m], attr, old)
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-v", "--verbose", action="store_true")
    ap.add_argument("--selftest", action="store_true",
                    help="switch the facade's write forwarding off and require every check to fail")
    args = ap.parse_args()
    sys.path.insert(0, SERVICES)
    import feworld as W
    if not hasattr(W, "_OWNERS"):
        print("services/feworld.py is not the facade (no _OWNERS/_MODULES)")
        return 1
    if args.selftest:
        import types
        type(W).__setattr__ = types.ModuleType.__setattr__
        found = rebindings(set(W._OWNERS))
        attrs = sorted({a for _f, _l, a in found})
        caught = [a for a in attrs if forwards(W, a)]
        print(f"selftest: forwarding off, {len(caught)}/{len(attrs)} name(s) caught")
        return 0 if attrs and len(caught) == len(attrs) else 1

    found = rebindings(set(W._OWNERS))
    attrs = sorted({a for _f, _l, a in found})
    print(f"{len(found)} rebinding(s) of {len(attrs)} name(s) across tools/ and services/")
    failures = []
    for attr in attrs:
        why = forwards(W, attr)
        sites = sorted({f"{f}:{ln}" for f, ln, a in found if a == attr})
        if why:
            failures.append((attr, why, sites))
        elif args.verbose:
            print(f"  [PASS] {attr:<28} -> world.{W._OWNERS[attr]}  ({', '.join(sites)})")
    for attr, why, sites in failures:
        print(f"  [FAIL] {attr}: {why}\n         at {', '.join(sites)}")
    if failures:
        print(f"\n{len(failures)} name(s) are patched but not forwarded by the facade")
        return 1
    print("every rebinding is forwarded to the module that runs it")
    return 0


if __name__ == "__main__":
    sys.exit(main())
