#!/usr/bin/env python3
"""A fresh PostgreSQL database for a self-test, from OpenLobby's tools/pgtest.py.

    import fepg
    url = fepg.fresh_database()     # sets POL_DATABASE_URL; None = no server

pgtest.py starts a throwaway postgres container on a free loopback port (or
uses POL_TEST_DATABASE_URL's server) and hands out one empty database per call.
It is found the way fe_title_test.py finds the OpenLobby core: OPENLOBBY_DIR,
else the checkout beside this repository.

When fe_run_all.py runs a suite it has already made that suite a database and
says so with FE_TEST_DATABASE=1; the suite then uses POL_DATABASE_URL as given.
Otherwise a suite always makes its own and never trusts a POL_DATABASE_URL it
finds in the environment, which could be a real stack's.

With no Docker and no POL_TEST_DATABASE_URL there is no server: fresh_database
returns None and the suite reports SKIP, unless POL_TEST_REQUIRE_DB=1 (CI),
which makes that a failure. The same convention as OpenLobby's polcore suites.
"""
import atexit
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENLOBBY = os.environ.get("OPENLOBBY_DIR",
                           os.path.join(ROOT, os.pardir, "openlobby"))
for _p in (os.path.join(OPENLOBBY, "tools"), os.path.join(OPENLOBBY, "services")):
    if _p not in sys.path:
        sys.path.append(_p)

import pgtest  # noqa: E402


def require_db():
    return os.environ.get("POL_TEST_REQUIRE_DB", "") == "1"


def server_available():
    """True when pgtest can reach or start a server."""
    try:
        pgtest.server_url()
        return True
    except Exception as exc:                                 # noqa: BLE001
        print("[fepg] no test database server: %s" % exc, file=sys.stderr)
        return False


def fresh_database():
    """POL_DATABASE_URL for this process: the runner's, or a new empty
    database dropped at exit. None when no server is available."""
    if os.environ.get("FE_TEST_DATABASE") == "1" and \
            os.environ.get("POL_DATABASE_URL"):
        return os.environ["POL_DATABASE_URL"]
    if not server_available():
        os.environ.pop("POL_DATABASE_URL", None)
        return None
    url = pgtest.create_database()
    atexit.register(_drop, url)
    os.environ["POL_DATABASE_URL"] = url
    return url


def _drop(url):
    try:
        from polcore import db
        db.close()
    except Exception:                                        # noqa: BLE001
        pass
    try:
        pgtest.drop_database(url)
    except Exception:                                        # noqa: BLE001
        pass


def pol_member(conn, name, fe_content_id=None):
    """A POL member with one (primary) handle called `name`, made through
    OpenLobby's own accounts functions. With `fe_content_id`, the handle also
    holds that Fantasy Earth Content ID (content code 11). Returns
    (member_id, handle_id)."""
    import accounts
    # a sealing key in the environment, so add_member never writes a key file
    os.environ.setdefault("POL_LOGIN_PW_KEY", "fe-selftest")
    accounts.create_polid(conn, name, "Passw0rdTest")
    mid = accounts.add_member(conn, name, name, "Passw0rdTest")
    hid = accounts.set_handle(conn, mid, name)
    if fe_content_id is not None:
        accounts.grant_content(conn, mid, 11)
        accounts.link_content_to_handle(conn, hid, 11, str(fe_content_id))
    return mid, hid


def pol_session(conn, member_id, nick, ip, age_s=0):
    """A POL sign-in from `ip` (accounts.open_session), backdated `age_s`
    seconds. Returns the session token."""
    import accounts
    import datetime
    token = accounts.open_session(conn, member_id, nick=nick, peer_ip=ip)
    if age_s:
        at = (datetime.datetime.now(datetime.timezone.utc)
              - datetime.timedelta(seconds=age_s)).strftime("%Y-%m-%dT%H:%M:%SZ")
        # backdating is a test-only move; open_session always stamps "now"
        with conn:
            conn.execute("UPDATE session SET created_at = %s WHERE token = %s",
                         (at, token))
    return token


def skip_or_fail(suite):
    """What a suite returns when there is no database: 0 (SKIP), or 1 when
    POL_TEST_REQUIRE_DB=1."""
    if require_db():
        print("[%s] FAIL: no PostgreSQL server and POL_TEST_REQUIRE_DB=1" % suite)
        return 1
    print("[%s] SKIP: no PostgreSQL server (Docker or POL_TEST_DATABASE_URL)"
          % suite)
    return 0
