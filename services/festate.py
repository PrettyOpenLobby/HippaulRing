#!/usr/bin/env python3
"""festate.py -- the Fantasy Earth world state in PostgreSQL.

The world keeps a handful of stores that the game itself rewrites while it
runs: the war campaign, who holds each field, where a player arrives, the
placed town, the door arrivals, the minimap calibration, the force rows and
the id of the Discord map message. Each used to be a JSON file under /data,
read whole at start and written whole (a temp file and a rename) after every
change. They are now rows of one table, `fe_world_state`, one row per store:

    name         the store ("campaign", "territory", "spawn", "town",
                 "door_arrive", "minimap_cal", "force", "map_discord")
    doc          the document the file held, as JSON
    updated_at   when it was last written

A row holds the whole document because every one of these is read and written
whole by the module that owns it, as festore's list columns are. The column is
JSON and not JSONB, so the document comes back with its keys in the order it
was written, which is the order the file had; several of them are served to a
client or a page as they are stored.

Each module keeps its load and save functions and decides where its store
lives from the option it always had (--campaign-file, --territory-file and so
on), through `location()`:

    not given (None)       the database, in the row named after the store
    a path                 that file, as before (a test, or a one-off run)
    "" or os.devnull       nowhere: the store lives in memory only

Without POL_DATABASE_URL a store that would be in the database is kept in
memory and a warning says so once, so a checkout with no database never
writes into the working tree.

A write is one transaction under an advisory lock named after the store, so
two processes that write the same store take turns. `update()` does a
read-modify-write under that lock, for stores that change one entry at a time
(the town).

A store whose read FAILED (the database was down when the module loaded it)
is not written blind: the module would be saving its empty in-memory copy over
the real one. `write()` reads the row again first and refuses while the row
exists, and says so. Once a read succeeds the store writes normally.

Seed data that ships with the repository (fedata/fe_spawn.json) is loaded
into an empty row once, the way the spawn file used to be copied into /data
on the first start.
"""
import json
import os
import sys
import threading

TABLE = "fe_world_state"

#: Every store in the table, by the file it used to be. The importer
#: (`python fedb.py import world DIR`) reads these names from an old /data.
FILES = {
    "campaign": "fe_campaign.json",
    "territory": "fe_territory.json",
    "spawn": "fe_spawn.json",
    "town": "fe_town.json",
    "door_arrive": "fe_door_arrive.json",
    "minimap_cal": "fe_minimap_cal.json",
    "force": "fe_force.json",
    "map_discord": "fe_map_discord.json",
}

_lock = threading.Lock()
_failed = set()         # stores whose last read hit a database fault
_warned = set()


class InDatabase(str):
    """The location of a store kept in the database. A str, so a log line or
    a truthiness test that used to see the file path still works."""

    def __new__(cls, name):
        s = str.__new__(cls, "%s[%s]" % (TABLE, name))
        s.name = name
        return s


def location(name, explicit):
    """Where store `name` lives, given its --X-file option (see the module
    docstring)."""
    if explicit is None:
        return InDatabase(name)
    return explicit


def in_database(loc):
    return isinstance(loc, InDatabase)


def persists(loc):
    """False for the in-memory locations ("" and os.devnull)."""
    return bool(loc) and loc != os.devnull


def _say(msg):
    print("[festate] %s" % msg, flush=True)


def _warn_once(key, msg):
    with _lock:
        if key in _warned:
            return
        _warned.add(key)
    _say(msg)


def _fedb():
    """fedb and polcore.db, or None when there is no database to use."""
    try:
        import fedb
    except ImportError as e:
        _warn_once("import", "WARNING: OpenLobby's polcore is not importable "
                   "(%s); world state is kept in memory only" % e)
        return None
    try:
        fedb.db.database_url()
    except fedb.db.DatabaseNotConfigured:
        _warn_once("url", "WARNING: POL_DATABASE_URL is not set; world state "
                   "is kept in memory only and is lost at exit")
        return None
    return fedb


def database():
    """fedb (its `db` is polcore.db) when this process has a database to use,
    else None, with a warning the first time."""
    return _fedb()


def _lock_name(name):
    return "%s:%s" % (TABLE, name)


def dumps(doc, sort_keys=False, indent=None):
    """The JSON text a document is stored as. NaN and Infinity raise
    ValueError: PostgreSQL's JSON cannot hold them."""
    return json.dumps(doc, ensure_ascii=False, sort_keys=sort_keys,
                      indent=indent, allow_nan=False)


# --------------------------------------------------------------------------- #
# the database side
# --------------------------------------------------------------------------- #
def db_read(name, seed=None):
    """The stored document, or None when there is none. Raises the database's
    own errors (fedb.errors()). With `seed`, a missing row is filled from that
    file once and its content returned."""
    fedb = _fedb()
    if fedb is None:
        return None
    db = fedb.db
    fedb.ensure_schema()
    row = db.query_one("SELECT doc FROM fe_world_state WHERE name = %s", (name,))
    if row is not None:
        return row["doc"]
    if not seed or not os.path.exists(seed):
        return None
    with open(seed, encoding="utf-8") as fh:
        text = fh.read()
    json.loads(text)                    # a broken seed raises ValueError here
    with db.transaction(lock=_lock_name(name)) as conn:
        n = db.execute("INSERT INTO fe_world_state (name, doc) VALUES (%s, %s::json)"
                       " ON CONFLICT (name) DO NOTHING", (name, text), conn=conn)
        row = db.query_one("SELECT doc FROM fe_world_state WHERE name = %s",
                           (name,), conn=conn)
    if n:
        _say("%s seeded from %s" % (name, seed))
    return row["doc"] if row else None


def db_write(name, text, conn=None):
    """Upsert the row for `name` with the JSON text `text`."""
    fedb = _fedb()
    db = fedb.db
    sql = ("INSERT INTO fe_world_state (name, doc, updated_at)"
           " VALUES (%s, %s::json, now()) ON CONFLICT (name) DO UPDATE"
           " SET doc = EXCLUDED.doc, updated_at = now()")
    if conn is not None:
        db.execute(sql, (name, text), conn=conn)
        return
    fedb.ensure_schema()
    with db.transaction(lock=_lock_name(name)) as own:
        db.execute(sql, (name, text), conn=own)


def _errors():
    fedb = _fedb()
    return fedb.errors() if fedb is not None else ()


# --------------------------------------------------------------------------- #
# what the modules call
# --------------------------------------------------------------------------- #
def read(loc, seed=None, tag="feworld"):
    """The document at `loc`, or None when there is none or it cannot be read
    (the modules have always started empty then). A database fault is logged
    and remembered, so that the next write() does not save the empty copy over
    the real one."""
    if not persists(loc):
        return None
    if not in_database(loc):
        try:
            with open(loc, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return None
    name = loc.name
    try:
        doc = db_read(name, seed)
    except (OSError, ValueError) as e:
        _say("WARNING: the seed for %s is unreadable (%r); starting empty"
             % (name, e))
        doc = None
    except _errors() as e:
        with _lock:
            _failed.add(name)
        _say("WARNING: %s could not be read from the database (%r). The %s "
             "store starts empty and is NOT written until a read succeeds"
             % (loc, e, name))
        return None
    with _lock:
        _failed.discard(name)
    return doc


def _clear_to_write(name):
    """False while a read of `name` has failed and the row exists: writing now
    would replace state this process never loaded."""
    with _lock:
        if name not in _failed:
            return True
    try:
        fedb = _fedb()
        row = fedb.db.query_one("SELECT 1 AS x FROM fe_world_state WHERE name = %s",
                                (name,))
    except _errors():
        return False
    if row is None:
        with _lock:
            _failed.discard(name)
        return True
    return False


def write(loc, doc, sort_keys=False, indent=None, tag="feworld"):
    """Write the whole document. True when it was stored. A file is written
    to a temp name and renamed, as the modules did; the database row is one
    transaction under the store's advisory lock."""
    if not persists(loc):
        return False
    if not in_database(loc):
        tmp = "%s.tmp.%d" % (loc, os.getpid())
        try:
            os.makedirs(os.path.dirname(os.path.abspath(loc)), exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as fh:
                fh.write(json.dumps(doc, ensure_ascii=False, sort_keys=sort_keys,
                                    indent=1 if indent is None else indent))
            os.replace(tmp, loc)
            return True
        except OSError as e:
            print("[%s] %s save failed: %s" % (tag, loc, e), flush=True)
            return False
    if _fedb() is None:
        return False
    name = loc.name
    if not _clear_to_write(name):
        _say("WARNING: %s NOT written: its read failed at start and the row "
             "exists, so this would replace it with what this process has"
             % loc)
        return False
    try:
        db_write(name, dumps(doc, sort_keys, indent))
        return True
    except (TypeError, ValueError) + _errors() as e:
        print("[%s] %s save failed: %r" % (tag, loc, e), flush=True)
        return False


def update(loc, mutate, sort_keys=False, indent=None, tag="feworld"):
    """Read the document (a dict, {} when there is none), call `mutate(doc)`,
    and write it back when `mutate` returns (True, result). `mutate` returns
    (False, result) to write nothing. Returns `result`, or None when the store
    could not be read or written.

    In the database the read and the write are one transaction under the
    store's advisory lock, so two processes adding an entry each keep both.
    A file is read and replaced as the modules always did; the caller holds
    its own thread lock around it.
    """
    if not persists(loc):
        return None
    if not in_database(loc):
        doc = read(loc)
        doc = doc if isinstance(doc, dict) else {}
        changed, result = mutate(doc)
        if changed and not write(loc, doc, sort_keys, indent, tag):
            return None
        return result
    fedb = _fedb()
    if fedb is None:
        return None
    db = fedb.db
    name = loc.name
    try:
        fedb.ensure_schema()
        with db.transaction(lock=_lock_name(name)) as conn:
            row = db.query_one("SELECT doc FROM fe_world_state WHERE name = %s",
                               (name,), conn=conn)
            doc = row["doc"] if row and isinstance(row["doc"], dict) else {}
            changed, result = mutate(doc)
            if changed:
                db_write(name, dumps(doc, sort_keys, indent), conn=conn)
        with _lock:
            _failed.discard(name)
        return result
    except (TypeError, ValueError) + _errors() as e:
        print("[%s] %s not written: %r" % (tag, loc, e), flush=True)
        return None


def names():
    """{name: updated_at} of every store in the database."""
    fedb = _fedb()
    if fedb is None:
        return {}
    fedb.ensure_schema()
    return {r["name"]: r["updated_at"] for r in fedb.db.query(
        "SELECT name, updated_at FROM fe_world_state ORDER BY name")}


def forget():
    """Drop what this process remembers (tests that switch databases)."""
    with _lock:
        _failed.clear()
        _warned.clear()


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _main(argv):
    if not argv or argv[0] not in ("list", "show"):
        print(__doc__)
        print("    python festate.py list          the stores in the database\n"
              "    python festate.py show NAME     one store's document")
        return 2
    if argv[0] == "list":
        for name, at in names().items():
            print("%-14s %s" % (name, at))
        return 0
    if len(argv) < 2:
        print("show wants a store name", file=sys.stderr)
        return 2
    doc = read(location(argv[1], None))
    if doc is None:
        print("(no %s in the database)" % argv[1])
        return 1
    print(json.dumps(doc, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
