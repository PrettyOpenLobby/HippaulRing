"""fedb.py -- where CrystalRing reaches OpenLobby's storage layer.

The player database (festore.py) and the in-game mail (femail.py) live in the
PostgreSQL database the whole stack shares, through OpenLobby's `polcore.db`
(POL_DATABASE_URL, a pool per process, numbered migrations). This module finds
that package, applies CrystalRing's own migrations once per process, and names
the exceptions that mean "the database could not be used".

Finding polcore:

  * in the image, which is built FROM the OpenLobby image, it is /app/polcore
    and imports directly;
  * in a checkout, OPENLOBBY_DIR names the OpenLobby repository, and failing
    that the one checked out beside this repository (../openlobby) is used,
    the same rule tools/fe_title_test.py follows.

CrystalRing's migrations are services/fe_migrations/NNNN_name.sql, applied with
`polcore.db.migrate(directory=...)`. They share OpenLobby's schema_migrations
table, which is keyed by the version number alone, so CrystalRing numbers its
files from 1001 up: OpenLobby's own files start at 0001, and a version number
that appears in both sets would be taken as already applied by whichever set
ran second. Every table CrystalRing creates starts with `fe_`.

    python fedb.py migrate     apply what is pending (uses POL_DATABASE_URL)
    python fedb.py status      list CrystalRing's migrations and their state

    python fedb.py import fe_db FILE        an old fe.db into fe_character
    python fedb.py import fe_mail_db FILE   an old fe_mail.db into fe_mail
    python fedb.py import world DIR         an old /data's world state files
                                            into fe_world_state

The imports take --dry-run and --merge; feimport.py describes them.
"""
import os
import sys
import threading

_HERE = os.path.dirname(os.path.abspath(__file__))

#: CrystalRing's migration files. Version numbers 1001..1999 are this
#: repository's; see the module docstring.
MIGRATIONS_DIR = os.path.join(_HERE, "fe_migrations")


def _openlobby_services():
    """Candidate OpenLobby `services/` directories, most specific first."""
    out = []
    env = os.environ.get("OPENLOBBY_DIR", "").strip()
    if env:
        out.append(os.path.join(env, "services"))
    out.append(os.path.normpath(os.path.join(_HERE, os.pardir, os.pardir,
                                             "openlobby", "services")))
    return out


try:
    from polcore import db  # noqa: E402
except ImportError:
    for _cand in _openlobby_services():
        if os.path.isdir(os.path.join(_cand, "polcore")):
            if _cand not in sys.path:
                sys.path.append(_cand)
            break
    from polcore import db  # noqa: E402,F811

_schema_lock = threading.Lock()
_schema_ready = set()


def errors():
    """The exceptions that mean the database could not be reached or read.

    A tuple for `except`. It deliberately does not include RuntimeError as a
    whole: festore.update_roster lets whatever `mutate` raises propagate, and a
    broad catch here would swallow it.
    """
    errs = [db.DatabaseNotConfigured, db.MigrationError]
    if db.psycopg is not None:
        errs.append(db.psycopg.Error)
        try:
            from psycopg_pool import PoolTimeout
            errs.append(PoolTimeout)
        except ImportError:                                  # pragma: no cover
            pass
    return tuple(errs)


def ensure_schema(log=None):
    """Apply CrystalRing's pending migrations, once per process and database.

    Cheap after the first call. Raises what `polcore.db.migrate` raises when
    the database cannot be reached, and remembers nothing then, so the next
    call tries again.
    """
    key = db.database_url()
    if key in _schema_ready:
        return
    with _schema_lock:
        if key in _schema_ready:
            return
        db.migrate(directory=MIGRATIONS_DIR,
                   log=log or (lambda msg: print("[fedb] %s" % msg, flush=True)))
        _schema_ready.add(key)


def forget_schema():
    """Drop the once-per-process memo (tests that switch databases)."""
    with _schema_lock:
        _schema_ready.clear()


def where():
    """The database this process uses, for a log line: host, port and name,
    never the password."""
    try:
        url = db.database_url()
    except db.DatabaseNotConfigured:
        return "(POL_DATABASE_URL is not set)"
    try:
        from urllib.parse import urlsplit
        u = urlsplit(url)
        return "postgresql://%s%s%s" % (u.hostname or "", ":%d" % u.port
                                        if u.port else "", u.path or "")
    except ValueError:
        return "(an unparsable POL_DATABASE_URL)"


def _main(argv):
    if argv and argv[0] == "import":
        import feimport
        return feimport.main(argv[1:])
    if not argv or argv[0] not in ("migrate", "status"):
        print(__doc__)
        return 2
    try:
        if argv[0] == "migrate":
            names = db.migrate(directory=MIGRATIONS_DIR)
            print("applied: " + ", ".join(names) if names else "up to date")
        else:
            have = db.applied_migrations()
            for version, name, _path in db.migration_files(MIGRATIONS_DIR):
                row = have.get(version)
                print("%-32s %s" % (name, "applied %s" % row["applied_at"]
                                    if row else "pending"))
        return 0
    except errors() as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
