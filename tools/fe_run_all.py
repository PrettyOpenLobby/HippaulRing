#!/usr/bin/env python3
"""Run the offline selftest suite: every tools/fe_*_test.py, plus the
service-embedded selftests. No Docker or client needed; suites that require
generated fedata skip themselves.

  python tools/fe_run_all.py            # everything
  python tools/fe_run_all.py -k combat  # only suites whose name contains
"""
import argparse
import glob
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.normpath(os.path.join(HERE, "..", "services"))
TIMEOUT = 300


def suites():
    out = [("festore", [sys.executable, "festore.py", "--selftest"], {})]
    for p in sorted(glob.glob(os.path.join(HERE, "fe_*_test.py"))):
        name = os.path.basename(p)[3:-8]  # fe_<name>_test.py -> <name>
        out.append((name, [sys.executable, p], {}))
    # the JSON store fallback path: FE_DB= (empty) means "no sqlite, use JSON"
    out.append(("store_json", [sys.executable,
                               os.path.join(HERE, "fe_store_test.py")],
                {"FE_DB": ""}))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("-k", action="append", default=[],
                    help="only suites whose name contains this substring")
    args = ap.parse_args()
    todo = [s for s in suites()
            if not args.k or any(k in s[0] for k in args.k)]
    failed = []
    for name, cmd, env in todo:
        t0 = time.time()
        e = dict(os.environ, **env)
        try:
            r = subprocess.run(cmd, cwd=SERVICES, env=e, timeout=TIMEOUT,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok, r = False, None
        print("  %-16s ... %s %6.1fs" % (name, "ok  " if ok else "FAIL",
                                         time.time() - t0))
        if not ok:
            failed.append(name)
            if r is not None:
                print("=" * 72)
                print((r.stdout or "")[-3000:])
                print((r.stderr or "")[-2000:])
                print("=" * 72)
    n = len(todo)
    if failed:
        print(f"{n - len(failed)}/{n} suites passed, "
              f"{len(failed)} FAILED: {', '.join(failed)}")
        sys.exit(1)
    print(f"{n}/{n} suites passed")


if __name__ == "__main__":
    main()
