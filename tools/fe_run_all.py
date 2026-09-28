#!/usr/bin/env python3
"""Run the offline selftest suite: every tools/fe_*_test.py, plus the
service-embedded selftests and the feworld facade check. No client needed;
suites that require generated fedata skip themselves.

Every suite gets a throwaway PostgreSQL database of its own in
POL_DATABASE_URL (tools/fepg.py, on OpenLobby's tools/pgtest.py: Docker, or
the server POL_TEST_DATABASE_URL names), so a suite never sees another's
characters or mail and never the database of a real stack. Without a server
the database suites report SKIP (POL_TEST_REQUIRE_DB=1 makes that a failure)
and the rest run with no POL_DATABASE_URL, on the JSON character store.

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

sys.path.insert(0, HERE)
import fepg  # noqa: E402


def suites():
    out = [("festore", [sys.executable, "festore.py", "--selftest"], {})]
    # services/feworld.py is a facade over services/world/: every name a test
    # or an extension rebinds must reach the module that runs it, and the
    # check must be able to fail (--selftest switches the forwarding off)
    check = os.path.join(HERE, "facade_rebind_check.py")
    out.append(("facade", [sys.executable, check], {}))
    out.append(("facade_selftest", [sys.executable, check, "--selftest"], {}))
    for p in sorted(glob.glob(os.path.join(HERE, "fe_*_test.py"))):
        name = os.path.basename(p)[3:-8]  # fe_<name>_test.py -> <name>
        out.append((name, [sys.executable, p], {}))
    # the JSON store fallback path: FE_DB= (empty) means "no database, use JSON"
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
    have_db = fepg.server_available()
    if not have_db:
        print("  (no PostgreSQL server: database suites report SKIP)")
    failed = []
    for name, cmd, env in todo:
        t0 = time.time()
        e = dict(os.environ, **env)
        # never a POL_DATABASE_URL from the environment: it could be a real
        # stack's
        e.pop("POL_DATABASE_URL", None)
        e.pop("FE_TEST_DATABASE", None)
        url = fepg.pgtest.create_database() if have_db else None
        if url:
            e.update(POL_DATABASE_URL=url, FE_TEST_DATABASE="1")
        try:
            r = subprocess.run(cmd, cwd=SERVICES, env=e, timeout=TIMEOUT,
                               capture_output=True, text=True,
                               encoding="utf-8", errors="replace")
            ok = r.returncode == 0
        except subprocess.TimeoutExpired:
            ok, r = False, None
        finally:
            if url:
                fepg.pgtest.drop_database(url)
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
