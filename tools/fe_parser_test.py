#!/usr/bin/env python3
"""fe_parser_test.py -- does feworld START with the knobs prod actually passes?

Run from services/:  python ../tools/fe_parser_test.py

WHY. Twice on 2026-09-11 a green 15/15 FE run shipped a feworld that could not
start: a knob registered twice (argparse conflict), then a --war-decide value
that was in the help text but not in the choices. Every FE suite hand-builds
its `args`; nothing built the real parser, and `feworld.py --help` alone does
not validate choices. Prod crash-looped both times.

This suite reads feworld's entrypoint list out of docker-compose.prod.yml --
the SAME argv prod runs -- resolves `${VAR:-default}`, appends `--help`, and
runs feworld.py with it. argparse validates each option as it consumes it, so
an unknown option, a duplicate registration or an invalid choice fails with
exit 2 before `--help` can exit 0. The same for felobby.
"""
import os
import re
import subprocess
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
_SERVICES = os.path.join(_ROOT, "services")
COMPOSE = os.path.join(_ROOT, "docker-compose.yml")

CHECKS = []


def say(*a):
    print(*a, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-64s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("\n" + detail) if (detail and not cond) else ""))


def prod_argv(service):
    """The quoted tokens of `service:`'s entrypoint/command list, comments
    stripped, `${VAR:-default}` -> default, `${VAR}` -> ''."""
    text = open(COMPOSE, encoding="utf-8").read()
    m = re.search(r"^  %s:\n(.*?)(?=^  [A-Za-z_][\w-]*:\n)" % re.escape(service),
                  text, re.S | re.M)
    assert m, "service %s not in %s" % (service, COMPOSE)
    # comments FIRST: a `]` inside one ("[scene+0x528]") would end the list
    block = "\n".join(l for l in m.group(1).splitlines()
                      if not l.strip().startswith("#"))
    m2 = re.search(r"^\s+(?:entrypoint|command):\s*\[(.*?)\]", block, re.S | re.M)
    assert m2, "no list-form entrypoint/command for %s" % service
    toks = re.findall(r'"([^"]*)"', m2.group(1))
    toks = [re.sub(r"\$\{[A-Z_0-9]+:-([^}]*)\}", r"\1", t) for t in toks]
    toks = [re.sub(r"\$\{[A-Z_0-9]+\}", "", t) for t in toks]
    return toks


def run(service, script):
    toks = prod_argv(service)
    assert toks[:2] == ["python", script], toks[:3]
    argv = [sys.executable, script] + toks[2:] + ["--help"]
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(argv, cwd=_SERVICES, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=120)
    out = p.stdout.decode("utf-8", "replace")
    check("%s starts with prod's own %d-token command line (exit 0)"
          % (script, len(toks) - 2), p.returncode == 0, out[-1500:])


def run_negative():
    """The mechanism must be able to fail: an invalid choice before --help."""
    toks = prod_argv("feworld")
    argv = ([sys.executable, "feworld.py"] + toks[2:]
            + ["--war-decide", "no-such-rule", "--help"])
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(argv, cwd=_SERVICES, env=env, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, timeout=120)
    check("...and an invalid choice ahead of --help is CAUGHT (exit 2)",
          p.returncode == 2, p.stdout.decode("utf-8", "replace")[-600:])


def main():
    say("the real parser, prod's real argv")
    toks = prod_argv("feworld")
    check("the compose feworld entrypoint was read (python feworld.py ...)",
          len(toks) >= 2 and toks[:2] == ["python", "feworld.py"],
          "%d tokens, tail %r" % (len(toks), toks[-4:]))
    run("feworld", "feworld.py")
    run_negative()
    run("felobby", "felobby.py")
    bad = [l for l, ok in CHECKS if not ok]
    say("[fe_parser_test] %s -- %d checks" % ("FAIL" if bad else "OK", len(CHECKS)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
