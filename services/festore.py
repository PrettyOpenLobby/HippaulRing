#!/usr/bin/env python3
"""festore.py -- the Fantasy Earth player database.

WHY THIS FILE EXISTS. Fantasy Earth on this server has been driven by ~160
`feworld.py` command-line knobs, and a knob is GLOBAL: `--gold`, `--crystal`,
`--class-levels`, `--bag-size` and the rest are one number for every player on
the server, so nothing a player does can change what they -- or anybody else --
sees next login. `_wallet_value()` in feworld.py already took the first step for
the four money rows: seed from the flag once, then serve the STORE. This is the
store those numbers move into, and the shape every other knob follows.

The character records themselves lived in `data/fe_characters.json`: one JSON
object, whole-file read-modify-write, written by TWO CONTAINERS (felobby
creates and deletes, feworld writes nation / skills / items / equip / palette /
blacklist / comment) over a shared `/data` bind mount, serialised by an advisory
lock on a sidecar `.lock` file. That worked, but the atomicity is hand-built and
the whole file is rewritten on every skill purchase.

WHERE IT LIVES. The `fe_character` table in the stack's PostgreSQL database,
through OpenLobby's `polcore.db` (POL_DATABASE_URL; see fedb.py for how the
package is found and how HippaulRing's migrations are numbered). Until
2026-09-27 it was its own SQLite file, `data/fe.db`, kept apart from
accounts.db because a container restart had truncated that file once
(memory: accounts-db-wal-hazard). A server database has no such file to
share, so the table sits beside OpenLobby's; the `fe_` prefix keeps the names
apart.

DROP-IN SHAPE. `load_roster` / `save_roster` / `store_accounts` / `next_charid`
/ `update_roster` take and return exactly what felobby's JSON versions did: a
list of plain dicts, roster order. felobby.py dispatches to them and does its
own tuple normalisation afterwards, so NO CALL SITE IN feworld.py CHANGED.
Keys this schema does not name are kept verbatim in an `extra` JSON column --
which matters more here than in FMO, because a character record carries ~48
positional wire fields (`w70`, `fac`, `dc4`...) that exist only to be echoed
back and must not be dropped by a store that does not recognise them.

Run standalone (POL_DATABASE_URL names the database):
    python festore.py --selftest              # a throwaway database (tools/fepg.py)
    python festore.py --show                  # what is on file
    python festore.py --import <json>         # one-shot migration off the JSON store
"""
import json
import os
import sys
import time

import fedb
from fedb import db

#: The table. Every HippaulRing table starts with fe_ (fedb.py).
TABLE = "fe_character"

#: Scalar keys that get their own column. Everything else rides in `extra`.
COLUMNS = (
    "charid", "name", "unit", "sex", "look1", "look2", "look3", "look4",
    "f24", "f28", "f2d", "s38", "force",
    "period_from", "period_to", "comment",
    "gold", "ring", "crystal", "total_score",
    "tutorial", "profile_comment",
    "exp", "bag_size",
)

#: The scalar columns that hold text; every other one in COLUMNS is BIGINT.
TEXT_COLUMNS = ("name", "s38", "comment", "profile_comment")

#: Columns held as JSON text. Decoded on the way out, so a caller sees the same
#: lists the JSON store handed it.
JSON_COLUMNS = ("equip", "items", "skills", "palette", "blacklist",
                "class_levels")

#: Columns the loader must NOT hand back as record keys.
_INTERNAL = ("account", "slot_ord", "extra", "created_at", "updated_at")

_INT64 = (-(1 << 63), (1 << 63) - 1)


class StoreUnavailable(Exception):
    """The database could not be READ. Distinct from "this account has none".

    WARNING: THIS DISTINCTION IS THE DIFFERENCE BETWEEN A FAILED LOGIN AND A DELETED
    CHARACTER, and it is why load_roster raises instead of returning None on a
    fault. felobby's select screen does:

        roster = load_roster(...)
        if roster is None:
            roster = parse_characters(args.characters, ...)   # a NEW player

    -- so "I could not read the store" and "you have never made a character"
    take the same branch, the player is shown an empty list, and the moment
    they create a character the save REPLACES their real roster with the one
    row. The JSON store has the same shape (an unreadable file returns None),
    and adding a database as a second way to fail without fixing it would have
    made a latent hazard a likely one.

    Raising is safe here and is the honest failure: `_serve_one` catches
    everything per connection and closes the socket, so FE fails fast and shows
    its own dialog -- the player gets back to the Viewer and NOTHING IS
    WRITTEN. A bind that leaves the client waiting is the one thing this
    service must never do; a bind that refuses is fine.
    """


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# the database
# --------------------------------------------------------------------------- #
def configured():
    """True when this process has a database to use (POL_DATABASE_URL)."""
    try:
        db.database_url()
        return True
    except db.DatabaseNotConfigured:
        return False


def where():
    """The database, for a log line (no password)."""
    return fedb.where()


def forget_schema():
    """Drop the once-per-process migration memo -- for tests that switch
    databases."""
    fedb.forget_schema()


def _errors():
    return fedb.errors()


def _lock(account):
    """The advisory lock that serialises writes to one account's roster.

    SQLite serialised every writer in the file (BEGIN IMMEDIATE), which is what
    made update_roster's read-modify-write safe across the felobby and feworld
    containers. PostgreSQL does not, so every write names this lock: two
    writers of the same account wait for each other, two accounts do not.
    """
    return "fe_character:%s" % account


# --------------------------------------------------------------------------- #
# record <-> row
# --------------------------------------------------------------------------- #
def _storable(col, v):
    """True when column `col` can hold `v` and hand back the same value.

    Booleans are excluded ON PURPOSE: a boolean written to an integer column
    comes back as an int, and a caller testing `is True` would break. They ride
    in `extra`, which is JSON and round-trips them exactly. The same goes for a
    value of the wrong type for its column (SQLite stored whatever it was
    given; PostgreSQL refuses), an integer outside 64 bits, and text with a
    NUL in it, which PostgreSQL's text type cannot hold.
    """
    if v is None:
        return True
    if col in TEXT_COLUMNS:
        return isinstance(v, str) and "\x00" not in v
    return (isinstance(v, int) and not isinstance(v, bool)
            and _INT64[0] <= v <= _INT64[1])


def record_to_row(rec, account, slot_ord):
    """(column dict, extra dict) for one character record."""
    cols, extra = {}, {}
    for k, v in rec.items():
        if k in JSON_COLUMNS:
            # json.dumps turns the tuples felobby hands back into lists, which
            # is exactly what the JSON store did -- so the round trip through
            # here is unchanged for every existing call site.
            cols[k] = None if v is None else json.dumps(v, ensure_ascii=False)
        elif k in COLUMNS and _storable(k, v):
            cols[k] = v
        else:
            extra[k] = v
    cols["account"] = account
    cols["slot_ord"] = slot_ord
    cols["extra"] = json.dumps(extra, sort_keys=True) if extra else None
    return cols, extra


def row_to_record(row):
    """One database row back to the plain dict the rest of FE expects.

    A NULL column is a key the record did not have -- NOT a zero. That
    distinction is load-bearing: `_wallet_value` treats a MISSING `gold` as
    "seed from the flag" and a stored 0 as "this character is broke", and an
    unset flag must not look like a player with nothing.
    """
    rec = {}
    for k, v in row.items():
        if k in _INTERNAL or v is None:
            continue
        if k in JSON_COLUMNS:
            try:
                rec[k] = json.loads(v)
            except ValueError:
                continue        # a hand-edited row is not worth losing a login
        else:
            rec[k] = v
    blob = row.get("extra")
    if blob:
        try:
            rec.update(json.loads(blob))
        except ValueError:
            pass
    return rec


# --------------------------------------------------------------------------- #
# the API felobby.py calls
# --------------------------------------------------------------------------- #
_SELECT_ROSTER = ("SELECT * FROM fe_character WHERE account = %s"
                  " ORDER BY slot_ord, charid")


def load_roster(account):
    """This account's characters, roster order. None when the account has none.

    WARNING: None, NOT []. felobby.load_roster returns None for "no store / no roster"
    and update_roster refuses to write when it sees None -- "a missing roster is
    not a place to create one". Returning [] here would make an unknown account
    look like a known one with no characters, and the first write would then
    invent a roster for it.

    WARNING: AND A FAULT RAISES StoreUnavailable RATHER THAN RETURNING THAT None --
    see its docstring. "The read failed" must never reach the caller wearing
    "you have no characters", because the next save would then replace a real
    roster with a freshly seeded one.
    """
    try:
        fedb.ensure_schema()
        rows = db.query(_SELECT_ROSTER, (account,))
    except _errors() as e:
        raise StoreUnavailable(
            "cannot read %r out of the character database %s: %r -- REFUSING "
            "to report an empty roster, because creating a character against "
            "one would replace whatever is really in there"
            % (account, where(), e))
    if not rows:
        return None                     # genuinely no characters: a NEW player
    return [row_to_record(r) for r in rows]


def _write_roster(conn, account, roster):
    """DELETE + INSERT of one account's list on `conn`, inside the caller's
    transaction. Raises on a database error."""
    now = _now()
    born = {r["charid"]: r["created_at"] for r in db.query(
        "SELECT charid, created_at FROM fe_character WHERE account = %s",
        (account,), conn=conn)}
    db.execute("DELETE FROM fe_character WHERE account = %s", (account,),
               conn=conn)
    for n, rec in enumerate(roster or []):
        cols, _ = record_to_row(rec, account, n)
        cols["created_at"] = born.get(cols.get("charid")) or now
        cols["updated_at"] = now
        names = list(cols)
        db.execute("INSERT INTO fe_character (%s) VALUES (%s)"
                   % (",".join('"%s"' % c for c in names),
                      ",".join("%s" for _ in names)),
                   [cols[c] for c in names], conn=conn)


def save_roster(account, roster, conn=None):
    """Replace this account's list. True when it was written.

    Whole-list replace, like the JSON store it stands in for -- the callers
    mutate their in-memory roster and then commit it, and matching that
    contract exactly is what let this swap in without touching a call site.
    It is a DELETE + INSERT inside ONE transaction scoped to ONE account, so
    unlike the JSON store two accounts cannot clobber each other even in
    principle, and a restart mid-write leaves the previous state intact --
    which is what the per-pid temp name, the `.bak` copy and the sidecar lock
    file were all hand-building.

    `conn` is for update_roster, which needs the read and the write inside the
    SAME transaction; there a database error propagates to it, so the whole
    read-modify-write rolls back together. Everybody else leaves it None and
    gets a transaction of its own.
    """
    if conn is not None:
        _write_roster(conn, account, roster)
        return True
    try:
        fedb.ensure_schema()
        with db.transaction(lock=_lock(account)) as own:
            _write_roster(own, account, roster)
        return True
    except _errors() as e:
        print("[festore] save_roster(%r) failed: %r -- %d character(s) NOT "
              "written" % (account, e, len(roster or [])), flush=True)
        return False


def update_roster(account, mutate):
    """Read `account`'s roster, apply `mutate(roster)`, write it back.

    KEY: THE WHOLE READ-MODIFY-WRITE IS ONE TRANSACTION UNDER THE ACCOUNT'S
    ADVISORY LOCK (see _lock), which is why this replaces the advisory lock on
    `<store>.lock` rather than keeping it. felobby and feworld run in SEPARATE
    CONTAINERS; the lock file was there because two whole-file rewrites could
    interleave and lose one player's skill purchase. The database lock does
    that job across processes, without a sidecar file that `os.replace()` can
    strand and without the "lock not taken -- this write is protected against
    other threads only" degraded mode.

    WARNING: THE LOCK IS TAKEN BEFORE THE SELECT, not at the first write. Two
    callers that both read first and then wait for each other's write is the
    exact read-modify-write race this exists to close; with the lock up front
    the second caller WAITS and then reads what the first one wrote.

    Returns what `mutate` returned, or None when the account has no roster --
    and writes NOTHING then, exactly as the JSON version did. An exception in
    `mutate` propagates and rolls the transaction back.
    """
    try:
        fedb.ensure_schema()
        with db.transaction(lock=_lock(account)) as conn:
            rows = db.query(_SELECT_ROSTER, (account,), conn=conn)
            if not rows:
                return None
            roster = [row_to_record(r) for r in rows]
            result = mutate(roster)
            save_roster(account, roster, conn=conn)
            return result
    except _errors() as e:
        print("[festore] update_roster(%r) failed: %r -- nothing written"
              % (account, e), flush=True)
        return None


def store_accounts():
    """{account: character count} for every account with at least one.

    A DICT, not a list, because felobby.store_accounts returns one -- it is what
    reports a roster still sitting under the pre-2026-08-24 shared "TestPlayer"
    key. Ordered by the account's bytes (COLLATE "C"), as SQLite ordered it.
    """
    try:
        fedb.ensure_schema()
        return {r["account"]: r["n"] for r in db.query(
            'SELECT account, COUNT(*) AS n FROM fe_character'
            ' GROUP BY account ORDER BY account COLLATE "C"')}
    except _errors():
        return {}


def next_charid(roster):
    """A charid free across the WHOLE store, not just this roster.

    charid is NOT a private number -- feworld sends it as the unit login value
    and spawns the player entity under it, so two players in one world would be
    two entities claiming the same id. `roster` is still consulted because the
    caller may hold rows it has not committed yet.
    """
    top = max([int(c.get("charid") or 0) for c in (roster or [])] + [0])
    try:
        fedb.ensure_schema()
        row = db.query_one("SELECT MAX(charid) AS top FROM fe_character")
        if row and row["top"] is not None:
            top = max(top, int(row["top"]))
    except _errors():
        pass
    return top + 1


def count():
    """How many characters are on file, across all accounts."""
    try:
        fedb.ensure_schema()
        return db.query_one("SELECT COUNT(*) AS n FROM fe_character")["n"]
    except _errors():
        return 0


# --------------------------------------------------------------------------- #
# the one-shot migration off the JSON store
# --------------------------------------------------------------------------- #
def import_json(json_path, force=False):
    """Copy `fe_characters.json` in. Returns (accounts, characters).

    REFUSES a database that already holds characters unless `force` -- the
    import runs automatically on first use, and an import that overwrote live
    rows with a stale file would be indistinguishable from data loss. The JSON
    file is never modified or deleted: it stays as the backup, and FE_DB=
    (empty) falls back to it.
    """
    if not json_path or not os.path.exists(json_path):
        return 0, 0
    if not force and count():
        return 0, 0
    try:
        with open(json_path, encoding="utf-8") as fh:
            blob = json.load(fh)
    except (OSError, ValueError) as e:
        print("[festore] cannot read %s: %r" % (json_path, e), flush=True)
        return 0, 0
    if not isinstance(blob, dict):
        return 0, 0
    everyone = blob.get("accounts")
    if not isinstance(everyone, dict):
        return 0, 0
    accounts = chars = 0
    for account, roster in sorted(everyone.items()):
        if not roster:
            continue
        if save_roster(account, roster):
            accounts += 1
            chars += len(roster)
    return accounts, chars


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _show():
    print("database: %s" % where())
    for account, n in store_accounts().items():
        print("  %s  (%d character(s))" % (account, n))
        for c in load_roster(account) or []:
            print("    charid %-4s %-18s nation=%-4s gold=%-8s ring=%-6s "
                  "crystal=%-6s score=%-8s items=%-3d equip=%-3d skills=%d"
                  % (c.get("charid"), c.get("name", ""),
                     c.get("force", "-"), c.get("gold", "-"),
                     c.get("ring", "-"), c.get("crystal", "-"),
                     c.get("total_score", "-"),
                     len(c.get("items") or []), len(c.get("equip") or []),
                     len(c.get("skills") or [])))


def _selftest():
    import tempfile
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                    os.pardir, "tools"))
    import fepg
    if fepg.fresh_database() is None:
        return fepg.skip_or_fail("festore")
    db.configure()                      # the URL fepg just put in the environment
    forget_schema()
    ok = True

    def check(label, cond):
        nonlocal ok
        print("  %s: %s" % (label, "OK" if cond else "FAIL"))
        ok &= bool(cond)

    print("festore selftest (%s)" % where())

    # KEY: THE MIGRATION NUMBERING. HippaulRing's files share OpenLobby's
    # schema_migrations table, which is keyed by version number alone, so a
    # number both sets used would be skipped by whichever set ran second.
    # Apply OpenLobby's set FIRST, as a stack does, then ours.
    ol = db.migrate(log=lambda m: None)
    fedb.ensure_schema(log=lambda m: None)
    have = db.applied_migrations()
    check("OpenLobby's migrations applied first (%s)" % ", ".join(ol), bool(ol))
    check("...and HippaulRing's still applied after them (1001, 1002)",
          {1001, 1002} <= set(have))
    check("fe_character and fe_mail exist",
          db.query_one("SELECT to_regclass('fe_character') IS NOT NULL AS a,"
                       " to_regclass('fe_mail') IS NOT NULL AS b")
          == {"a": True, "b": True})
    check("a second run applies nothing (idempotent)",
          db.migrate(directory=fedb.MIGRATIONS_DIR, log=lambda m: None) == [])

    rec = {"charid": 1, "name": "Fox", "unit": 0xFF, "sex": 1,
           "look1": 2, "look2": 3, "look3": 4, "look4": 5,
           "f24": 0, "f28": 0, "f2d": 0, "s38": "",
           "force": 3, "period_from": 0, "period_to": 0, "comment": "",
           "equip": [[1, 0x1234]], "items": [[1, 0x1234, 0], [2, 0x5678, 0, 3]],
           "skills": [0x2200, 0x2201], "palette": [0, 0x2200],
           "blacklist": [{"id": 0x40000000, "name": "Rival"}],
           # keys with no column, and a bool: these must survive via `extra`
           "w70": -1, "fac": 1.5, "dc4": 7, "u8_b": 0, "tutorial_seen": True}
    check("an unknown account reads None, not []",
          load_roster("member:3") is None)
    check("save", save_roster("member:3", [rec]))
    got = load_roster("member:3")
    check("round trip is key-for-key identical", got == [rec])
    check("no cross-account bleed", load_roster("member:9") is None)
    check("store_accounts", store_accounts() == {"member:3": 1})

    # a value of the wrong type for its column must survive too (SQLite kept
    # whatever it was given; PostgreSQL would refuse the row)
    odd = dict(rec, gold=12.5, name=7, exp=1 << 70, comment="a\x00b",
               tutorial=True)
    check("off-type values save", save_roster("member:3", [odd]))
    check("...and come back exactly, through `extra`",
          load_roster("member:3") == [odd])

    # a stored ZERO must not read back as absent -- _wallet_value depends on it
    save_roster("member:3", [dict(rec, gold=0)])
    check("a stored 0 survives as 0", load_roster("member:3")[0]["gold"] == 0)
    save_roster("member:3", [dict(rec)])
    check("an ABSENT key stays absent",
          "gold" not in load_roster("member:3")[0])

    # an EMPTY list is not a missing one either -- an emptied bag must persist
    save_roster("member:3", [dict(rec, items=[])])
    check("an emptied bag stays empty (not absent)",
          load_roster("member:3")[0]["items"] == [])

    # tuples go in, lists come back -- exactly what the JSON store did
    save_roster("member:3", [dict(rec, equip=[(1, 0x1234)])])
    check("tuples round-trip as lists, as JSON did",
          load_roster("member:3")[0]["equip"] == [[1, 0x1234]])

    # order is the roster order, not the charid order
    save_roster("member:3", [dict(rec, charid=7), dict(rec, charid=2)])
    check("roster order is preserved",
          [c["charid"] for c in load_roster("member:3")] == [7, 2])
    check("next_charid clears the whole store", next_charid([]) == 8)
    check("and an uncommitted roster too",
          next_charid([{"charid": 40}]) == 41)

    check("a duplicate charid is refused, not half-written",
          save_roster("member:3", [dict(rec, charid=5), dict(rec, charid=5)])
          is False)
    check("...and the previous roster is intact",
          [c["charid"] for c in load_roster("member:3")] == [7, 2])

    save_roster("member:3", [])
    check("empty save clears the account", load_roster("member:3") is None)
    check("and takes it out of store_accounts", store_accounts() == {})

    # created_at survives a rewrite; updated_at moves
    save_roster("member:3", [rec])
    born = db.query_one("SELECT created_at FROM fe_character")["created_at"]
    time.sleep(1.01)                    # _now() has one-second resolution
    save_roster("member:3", [dict(rec, name="Renamed")])
    row = db.query_one("SELECT created_at, updated_at FROM fe_character")
    check("created_at survives a rewrite", row["created_at"] == born)
    check("updated_at moves", row["updated_at"] != born)

    # update_roster: the read-modify-write feworld uses for every mutation
    def bump(roster):
        roster[0]["gold"] = 12345
        return "done"

    check("update_roster returns what mutate did",
          update_roster("member:3", bump) == "done")
    check("and committed it", load_roster("member:3")[0]["gold"] == 12345)
    check("update_roster on an unknown account writes nothing",
          update_roster("member:404", bump) is None)
    check("and did not create it", load_roster("member:404") is None)

    def boom(roster):
        roster[0]["gold"] = 999
        raise ValueError("mutate blew up")

    try:
        update_roster("member:3", boom)
        check("an exception in mutate propagates", False)
    except ValueError:
        check("an exception in mutate propagates", True)
    check("and rolls the whole transaction back",
          load_roster("member:3")[0]["gold"] == 12345)

    # two writers of one account, from two threads: the lock makes the second
    # read what the first wrote, so neither increment is lost
    import threading
    barrier = threading.Barrier(2)

    def slow_add(roster):
        roster[0]["gold"] += 1
        time.sleep(0.3)
        return True

    def worker():
        barrier.wait()
        update_roster("member:3", slow_add)

    ts = [threading.Thread(target=worker) for _ in range(2)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("two concurrent update_rosters lose neither write",
          load_roster("member:3")[0]["gold"] == 12347)

    # WARNING: A FAULT MUST NOT LOOK LIKE AN EMPTY ROSTER -- see StoreUnavailable
    db.execute("ALTER TABLE fe_character RENAME TO fe_character_away")
    try:
        load_roster("member:3")
        check("an unreadable table raises rather than reading empty", False)
    except StoreUnavailable:
        check("an unreadable table raises rather than reading empty", True)
    finally:
        db.execute("ALTER TABLE fe_character_away RENAME TO fe_character")

    saved_url = os.environ.pop("POL_DATABASE_URL")
    db.configure()
    try:
        load_roster("member:3")
        check("no database configured raises too", False)
    except StoreUnavailable:
        check("no database configured raises too", True)
    finally:
        os.environ["POL_DATABASE_URL"] = saved_url
        db.configure()
    check("configured() says whether there is a database", configured())

    check("...while a genuinely unknown account still reads None",
          load_roster("member:404") is None)

    # the JSON import
    jp = os.path.join(tempfile.mkdtemp(prefix="festore"), "chars.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"accounts": {
            "member:5": [{"charid": 1, "name": "Old", "force": 2}],
            "member:6": []}}, fh)
    save_roster("member:3", [])
    n_a, n_c = import_json(jp)
    check("import brings the JSON store in", (n_a, n_c) == (1, 1))
    check("and skips accounts with no characters",
          list(store_accounts()) == ["member:5"])
    check("a second import is refused (rows exist)",
          import_json(jp) == (0, 0))
    check("the JSON file is left alone", os.path.exists(jp))

    db.close()
    print("ALL OK" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
    elif argv[0] == "--selftest":
        raise SystemExit(_selftest())
    elif argv[0] == "--show":
        _show()
    elif argv[0] == "--import":
        if len(argv) < 2:
            raise SystemExit("--import wants the fe_characters.json path")
        a, c = import_json(argv[1], force="--force" in argv)
        print("imported %d character(s) for %d account(s) into %s"
              % (c, a, where())
              if c else "nothing imported (the database already holds "
                        "characters, or the file is empty) -- --force overrides")
    else:
        raise SystemExit("unknown option %r; try --help" % argv[0])
