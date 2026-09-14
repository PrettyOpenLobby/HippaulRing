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

DELIBERATELY ITS OWN FILE, NOT A TABLE IN accounts.db, for the same reason
fmostore.py is: accounts.db is opened by every other service here and has
already been truncated once by a container restart landing on a schema write
. The connection disciplines below are copied
from `accounts.connect()` via `fmostore.connect()` -- they were each paid for by
a live failure -- but the FILE is separate, and separate from FMO's too.

DROP-IN SHAPE. `load_roster` / `save_roster` / `store_accounts` / `next_charid`
/ `update_roster` take and return exactly what felobby's JSON versions did: a
list of plain dicts, roster order. felobby.py dispatches to them and does its
own tuple normalisation afterwards, so NO CALL SITE IN feworld.py CHANGED.
Keys this schema does not name are kept verbatim in an `extra` JSON column --
which matters more here than in FMO, because a character record carries ~48
positional wire fields (`w70`, `fac`, `dc4`...) that exist only to be echoed
back and must not be dropped by a store that does not recognise them.

WARNING: THE ARGUMENT ORDER IS (account, path), MATCHING fmostore -- felobby's public
functions keep their own (path, account) order. The two stores get read side by
side often enough that making them disagree internally is worse than the one
adapter line in felobby.

Run standalone:
    python festore.py --selftest              # no files touched but a temp one
    python festore.py --show [<db>]           # what is on file
    python festore.py --import <json> [<db>]  # one-shot migration
"""
import contextlib
import json
import os
import sqlite3
import sys
import threading
import time

# --------------------------------------------------------------------------- #
# where it lives
# --------------------------------------------------------------------------- #
#: Same resolution trick as felobby's `_default_store()`: services/ is bind
#: mounted at /app in the container, so `_HERE/../data` is pol-server/data on
#: the host and /data in prod. Probing an absolute `/data` first is wrong on
#: Windows, where it means `<current drive>/data` -- the host wrote to `E:/data`
#: once and looked like it had worked.
_HERE = os.path.dirname(os.path.abspath(__file__))


def default_db():
    d = os.path.normpath(os.path.join(_HERE, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else _HERE, "fe.db")


#: Empty disables the database completely -- felobby then keeps using the JSON
#: store, which is the state every measurement before 2026-09-08 ran against.
DB_PATH = os.environ.get("FE_DB", default_db())

#: Shared with accounts.py and fmostore.py on purpose: one server, one journal
#: mode. TRUNCATE (not WAL) because /data is a Windows bind mount on the dev box
#: and WAL needs a shared-memory mapping those do not provide -- see
#: accounts-db-wal-hazard.
JOURNAL_MODE = os.environ.get("POL_SQLITE_JOURNAL", "TRUNCATE")

#: 1 = the original swap off fe_characters.json (2026-09-08).
#: 2 = PROGRESSION: exp, class_levels, bag_size (2026-09-08, same day).
SCHEMA_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

-- One Fantasy Earth character. `account` is feident's resolved store key
-- ("member:3", or "addr:<ip>" when no POL session names the box); `charid` is
-- the slot key the client echoes back in every 0xC003/0xC004 and the value
-- feworld sends as the unit login id, so it is unique across the WHOLE store,
-- not just one roster (see next_charid).
CREATE TABLE IF NOT EXISTS character (
    account       TEXT    NOT NULL,
    charid        INTEGER NOT NULL,
    slot_ord      INTEGER NOT NULL DEFAULT 0,   -- roster order, oldest first

    -- IDENTITY, as the 0xD002 character record carries it (felobby.CHAR_FIELDS)
    name          TEXT,
    unit          INTEGER,      -- game unit id; 0xFF = none
    sex           INTEGER,      -- picks the Male/m_ model table row vs the other
    look1         INTEGER,
    look2         INTEGER,
    look3         INTEGER,
    look4         INTEGER,
    f24           INTEGER,
    f28           INTEGER,
    f2d           INTEGER,
    s38           TEXT,

    -- THE NATION. 0 IS NOT "unset" -- nations are 1..5 and 0x7FFFFFFF is the
    -- client's sentinel for none. Written by feworld when 0x4012
    -- MSG_JOIN_FORCE_REQUEST arrives.
    force         INTEGER,

    -- THE SUSPENSION BLOCK the client prints verbatim (packed decimal YYMMDDHH)
    period_from   INTEGER,
    period_to     INTEGER,
    comment       TEXT,

    -- THE ECONOMY. Seeded from --gold / --ring / --crystal / --total-score the
    -- first time a character has no stored value, and served from here after.
    -- A STORED 0 IS NOT A MISSING KEY: `_wallet_value` treats absent as "seed
    -- from the flag" and 0 as "this player is broke". NULL means absent.
    gold          INTEGER,
    ring          INTEGER,
    crystal       INTEGER,
    total_score   INTEGER,

    -- PROGRESS
    tutorial      INTEGER,      -- 1 once the client has been through it
    profile_comment TEXT,       -- what /comment sets; read back on next login

    -- KEY: PROGRESSION (schema 2). These three were GLOBAL KNOBS, which is the
    -- thing this file exists to end: one EXP total, one class-level table and
    -- one bag size for every player on the server.
    --
    -- `exp` is the u32 the client keeps at [unit+0x135c], served by 0x1075
    -- mask bit 2. Its setter does not ADD -- the value on the wire is a TOTAL
    -- -- so combat_exp_push kept a running sum per SESSION and said outright
    -- that it "is not persisted to the character store yet". Now it is, which
    -- means a kill still counts after a relog.
    exp           INTEGER,
    -- {class index: level}, JSON. [unit+0x9F0 + [unit+0x3AB]] is what the
    -- equip validator compares against a skill's required level (0x05076195);
    -- at 0 every skill is refused and double-click does nothing, which is why
    -- --class-levels existed at all. Per character now, so one player's
    -- progress is not every player's.
    class_levels  TEXT,
    bag_size      INTEGER,      -- rows the bag holds; was --bag-size for all

    -- THE LISTS, as JSON. Held as text rather than child tables on purpose:
    -- every one of them is read and written WHOLE by feworld (bag_organize
    -- rewrites `items` entirely, the palette round-trips all 8 slots), so rows
    -- would buy nothing and cost the atomicity one column gives free.
    equip         TEXT,         -- [[uid, no], ...]
    items         TEXT,         -- [[uid, no, flag, count?], ...]
    skills        TEXT,         -- [skill_id, ...]
    palette       TEXT,         -- the 8 skill-palette slots (0x2029)
    blacklist     TEXT,         -- [{"id": u32, "name": str}, ...]

    -- ~48 positional wire fields (w70..wa8, fac..fc0, dc4, dc8, fcc..fd8) and
    -- anything a later decode adds. Kept verbatim so a store that does not
    -- recognise a field cannot silently drop it.
    extra         TEXT,

    created_at    TEXT,
    updated_at    TEXT,
    PRIMARY KEY (account, charid)
);

CREATE INDEX IF NOT EXISTS character_account ON character (account, slot_ord);
"""

#: Scalar keys that get their own column. Everything else rides in `extra`.
COLUMNS = (
    "charid", "name", "unit", "sex", "look1", "look2", "look3", "look4",
    "f24", "f28", "f2d", "s38", "force",
    "period_from", "period_to", "comment",
    "gold", "ring", "crystal", "total_score",
    "tutorial", "profile_comment",
    "exp", "bag_size",                                      # schema 2
)

#: Columns held as JSON text. Decoded on the way out, so a caller sees the same
#: lists the JSON store handed it.
JSON_COLUMNS = ("equip", "items", "skills", "palette", "blacklist",
                "class_levels")                             # class_levels: s2

#: Columns the loader must NOT hand back as record keys.
_INTERNAL = ("account", "slot_ord", "extra", "created_at", "updated_at")


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
    and adding sqlite as a second way to fail without fixing it would have made
    a latent hazard a likely one.

    Raising is safe here and is the honest failure: `_serve_one` catches
    everything per connection and closes the socket, so FE fails fast and shows
    its own dialog -- the player gets back to the Viewer and NOTHING IS
    WRITTEN. A bind that leaves the client waiting is the one thing this
    service must never do; a bind that refuses is fine.
    """


def _now():
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


# --------------------------------------------------------------------------- #
# the connection
# --------------------------------------------------------------------------- #
_SCHEMA_LOCK = threading.Lock()
_SCHEMA_READY = set()
_JOURNAL_WARNED = set()


def connect(path=None):
    """Open (creating if needed) the FE database with the schema applied.

    The three disciplines here are lifted from `accounts.connect()` and each
    one is a live failure someone already paid for:

    * THE SCHEMA IS APPLIED ONCE PER PROCESS. `CREATE TABLE IF NOT EXISTS`
      takes a write lock even when every statement is a no-op, so applying it
      per connection makes every reader contend with every other reader.
    * THE JOURNAL MODE IS SET PER CONNECTION. For the rollback modes it is a
      property of the CONNECTION, not the file, and sqlite opens every new one
      in DELETE -- mixing DELETE and TRUNCATE on one file raises `disk I/O
      error` on a Windows bind mount.
    * THE OPEN IS RETRIED. On that same bind mount an ordinary open comes back
      `unable to open database file` every so often.

    No pool: FE opens a connection per store call -- a handful per login and
    one per skill purchase, not the 7-9 per serve that made pooling worth it
    for the POL lobby.
    """
    path = path or DB_PATH
    key = os.path.abspath(path)
    parent = os.path.dirname(key)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = None
    for attempt in range(3):
        try:
            conn = sqlite3.connect(path, timeout=10, check_same_thread=False)
            break
        except sqlite3.OperationalError:
            if attempt == 2:
                raise
            time.sleep(0.1 * (attempt + 1))
    conn.row_factory = sqlite3.Row
    got = conn.execute("PRAGMA journal_mode = %s" % JOURNAL_MODE).fetchone()
    got = (got[0] if got else "?").lower()
    if got != JOURNAL_MODE.lower() and key not in _JOURNAL_WARNED:
        _JOURNAL_WARNED.add(key)
        print("[festore] journal_mode is %r, not the requested %r -- another "
              "connection holds %s" % (got, JOURNAL_MODE.lower(), path),
              flush=True)
    with _SCHEMA_LOCK:
        if key not in _SCHEMA_READY:
            conn.executescript(SCHEMA)
            _migrate(conn, path)
            conn.execute(
                "INSERT OR IGNORE INTO meta (key, value) VALUES ('schema', ?)",
                (str(SCHEMA_VERSION),))
            conn.commit()
            _SCHEMA_READY.add(key)      # only after it is genuinely ready
    return conn


def _migrate(conn, path):
    """Bring an EXISTING database up to SCHEMA_VERSION. Additive only.

    WARNING: `CREATE TABLE IF NOT EXISTS` DOES NOTHING TO A TABLE THAT ALREADY EXISTS,
    columns included -- so shipping a new column in SCHEMA above reaches a fresh
    database and no other. Prod's fe.db was created at schema 1 with three real
    characters in it within the hour; without this, every read of a schema-2
    column would have raised `no such column` and -- because load_roster turns a
    sqlite fault into StoreUnavailable -- refused every FE login.

    Additive ONLY, deliberately: `ADD COLUMN` is O(1) in sqlite, needs no table
    rewrite, and cannot lose a row. A change that needs a column DROPPED or
    RETYPED is a different and much more dangerous operation; it does not belong
    in a function that runs unattended on every process start.
    """
    # A FRESH database already has every column -- executescript ran just above,
    # so this finds nothing to do and the stamp at the end is the only write.
    # The work here is entirely for a database that predates a column.
    have = {r[1] for r in conn.execute("PRAGMA table_info(character)")}
    wanted = [("exp", "INTEGER"), ("class_levels", "TEXT"),
              ("bag_size", "INTEGER")]
    added, failed = [], []
    for col, kind in wanted:
        if col in have:
            continue
        try:
            conn.execute('ALTER TABLE character ADD COLUMN "%s" %s'
                         % (col, kind))
            added.append(col)
        except sqlite3.Error as e:      # pragma: no cover
            failed.append(col)
            print("[festore] WARNING: could not add column %s to %s: %r"
                  % (col, path or DB_PATH, e), flush=True)
    if added:
        was = conn.execute(
            "SELECT value FROM meta WHERE key = 'schema'").fetchone()
        n = conn.execute("SELECT COUNT(*) FROM character").fetchone()[0]
        print("[festore] schema %s -> %d on %s: added %s. %d character(s) kept "
              "-- ADD COLUMN rewrites no rows, and every new column reads NULL "
              "(= 'no stored value', so the knob still seeds it)."
              % (was[0] if was else "?", SCHEMA_VERSION, path or DB_PATH,
                 ", ".join(added), n), flush=True)
    if failed:
        # WARNING: DO NOT STAMP A VERSION THE FILE DOES NOT HAVE. A meta row claiming
        # schema 2 over a table missing a schema-2 column is worse than no row:
        # the next process reads the stamp, skips the migration it still needs,
        # and every read of that column raises -- which load_roster turns into
        # StoreUnavailable, i.e. a refused login.
        print("[festore] WARNING: leaving the schema stamp where it was -- %s still "
              "missing, so the next start retries the migration"
              % ", ".join(failed), flush=True)
        return
    conn.execute("INSERT OR REPLACE INTO meta (key, value) VALUES "
                 "('schema', ?)", (str(SCHEMA_VERSION),))


def forget_schema(path=None):
    """Drop the once-per-process memo -- for tests that delete the file."""
    with _SCHEMA_LOCK:
        _SCHEMA_READY.discard(os.path.abspath(path or DB_PATH))


# --------------------------------------------------------------------------- #
# record <-> row
# --------------------------------------------------------------------------- #
def _storable(v):
    """True when sqlite can hold `v` in a column without changing it.

    Booleans are excluded ON PURPOSE: sqlite stores True as 1 and hands it back
    as 1, so a `True` written here would come back as an int and a caller
    testing `is True` would break. They ride in `extra`, which is JSON and
    round-trips them exactly.
    """
    return v is None or (isinstance(v, (int, float, str))
                         and not isinstance(v, bool))


def record_to_row(rec, account, slot_ord):
    """(column dict, extra dict) for one character record."""
    cols, extra = {}, {}
    for k, v in rec.items():
        if k in JSON_COLUMNS:
            # json.dumps turns the tuples felobby hands back into lists, which
            # is exactly what the JSON store did -- so the round trip through
            # here is unchanged for every existing call site.
            cols[k] = None if v is None else json.dumps(v, ensure_ascii=False)
        elif k in COLUMNS and _storable(v):
            cols[k] = v
        else:
            extra[k] = v
    cols["account"] = account
    cols["slot_ord"] = slot_ord
    cols["extra"] = json.dumps(extra, sort_keys=True) if extra else None
    return cols, extra


def row_to_record(row):
    """One sqlite row back to the plain dict the rest of FE expects.

    A NULL column is a key the record did not have -- NOT a zero. That
    distinction is load-bearing: `_wallet_value` treats a MISSING `gold` as
    "seed from the flag" and a stored 0 as "this character is broke", and an
    unset flag must not look like a player with nothing.
    """
    rec = {}
    for k in row.keys():
        if k in _INTERNAL:
            continue
        v = row[k]
        if v is None:
            continue
        if k in JSON_COLUMNS:
            try:
                rec[k] = json.loads(v)
            except ValueError:
                continue        # a hand-edited row is not worth losing a login
        else:
            rec[k] = v
    blob = row["extra"]
    if blob:
        try:
            rec.update(json.loads(blob))
        except ValueError:
            pass
    return rec


# --------------------------------------------------------------------------- #
# the API felobby.py calls
# --------------------------------------------------------------------------- #
def load_roster(account, path=None):
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
        conn = connect(path)
    except sqlite3.Error as e:
        raise StoreUnavailable(
            "cannot open the character database %s: %r -- REFUSING to report "
            "an empty roster for %r, because creating a character against one "
            "would replace whatever is really in there"
            % (path or DB_PATH, e, account))
    try:
        rows = conn.execute(
            "SELECT * FROM character WHERE account = ?"
            " ORDER BY slot_ord, charid", (account,)).fetchall()
    except sqlite3.Error as e:
        raise StoreUnavailable(
            "cannot read %r out of the character database %s: %r -- REFUSING "
            "to report an empty roster, because creating a character against "
            "one would replace whatever is really in there"
            % (account, path or DB_PATH, e))
    finally:
        conn.close()
    if not rows:
        return None                     # genuinely no characters: a NEW player
    return [row_to_record(r) for r in rows]


def save_roster(account, roster, path=None, conn=None):
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
    SAME transaction; everybody else leaves it None and gets its own.
    """
    own = conn is None
    if own:
        try:
            conn = connect(path)
        except sqlite3.Error as e:
            print("[festore] save_roster(%r) failed to open the database: %r"
                  % (account, e), flush=True)
            return False
    try:
        now = _now()
        born = {r["charid"]: r["created_at"] for r in conn.execute(
            "SELECT charid, created_at FROM character WHERE account = ?",
            (account,))}
        # `with conn:` commits on the way out -- but only when this call owns
        # the connection. Inside update_roster's BEGIN IMMEDIATE it must NOT
        # commit: the caller does that after mutate() has been seen to return.
        ctx = conn if own else contextlib.nullcontext()
        with ctx:
            conn.execute("DELETE FROM character WHERE account = ?", (account,))
            for n, rec in enumerate(roster or []):
                cols, _ = record_to_row(rec, account, n)
                cols["created_at"] = born.get(cols.get("charid")) or now
                cols["updated_at"] = now
                names = list(cols)
                conn.execute(
                    "INSERT INTO character (%s) VALUES (%s)"
                    % (",".join('"%s"' % c for c in names),
                       ",".join("?" for _ in names)),
                    [cols[c] for c in names])
        return True
    except sqlite3.Error as e:
        print("[festore] save_roster(%r) failed: %r -- %d character(s) NOT "
              "written" % (account, e, len(roster or [])), flush=True)
        return False
    finally:
        if own:
            conn.close()


def update_roster(account, mutate, path=None):
    """Read `account`'s roster, apply `mutate(roster)`, write it back.

    KEY: THE WHOLE READ-MODIFY-WRITE IS ONE `BEGIN IMMEDIATE` TRANSACTION, which
    is why this replaces the advisory lock on `<store>.lock` rather than
    keeping it. felobby and feworld run in SEPARATE CONTAINERS over one /data;
    the lock file was there because two whole-file rewrites could interleave
    and lose one player's skill purchase. sqlite's own writer lock does that job
    across processes, without a sidecar file that `os.replace()` can strand and
    without the "lock not taken -- this write is protected against other threads
    only" degraded mode.

    Returns what `mutate` returned, or None when the account has no roster --
    and writes NOTHING then, exactly as the JSON version did.
    """
    try:
        conn = connect(path)
    except sqlite3.Error as e:
        print("[festore] update_roster(%r) failed to open the database: %r"
              % (account, e), flush=True)
        return None
    try:
        # WARNING: IMMEDIATE, not the implicit DEFERRED. A deferred transaction takes
        # only a READ lock at the SELECT and upgrades at the first write, and
        # two of those upgrading at once is `database is locked` with the other
        # side's work already done -- the exact read-modify-write race this
        # exists to close. IMMEDIATE takes the writer lock up front, so the
        # second caller WAITS (timeout=10 in connect) instead of failing.
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT * FROM character WHERE account = ?"
            " ORDER BY slot_ord, charid", (account,)).fetchall()
        if not rows:
            conn.rollback()
            return None
        roster = [row_to_record(r) for r in rows]
        result = mutate(roster)
        save_roster(account, roster, path, conn=conn)
        conn.commit()
        return result
    except sqlite3.Error as e:
        print("[festore] update_roster(%r) failed: %r -- nothing written"
              % (account, e), flush=True)
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        return None
    finally:
        conn.close()


def store_accounts(path=None):
    """{account: character count} for every account with at least one.

    A DICT, not a list, because felobby.store_accounts returns one -- it is what
    reports a roster still sitting under the pre-2026-08-24 shared "TestPlayer"
    key.
    """
    try:
        conn = connect(path)
    except sqlite3.Error:
        return {}
    try:
        return {r[0]: r[1] for r in conn.execute(
            "SELECT account, COUNT(*) FROM character"
            " GROUP BY account ORDER BY account")}
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def next_charid(roster, path=None):
    """A charid free across the WHOLE store, not just this roster.

    charid is NOT a private number -- feworld sends it as the unit login value
    and spawns the player entity under it, so two players in one world would be
    two entities claiming the same id. `roster` is still consulted because the
    caller may hold rows it has not committed yet.
    """
    top = max([int(c.get("charid") or 0) for c in (roster or [])] + [0])
    try:
        conn = connect(path)
    except sqlite3.Error:
        return top + 1
    try:
        row = conn.execute("SELECT MAX(charid) FROM character").fetchone()
        if row and row[0] is not None:
            top = max(top, int(row[0]))
    except sqlite3.Error:
        pass
    finally:
        conn.close()
    return top + 1


def count(path=None):
    """How many characters are on file, across all accounts."""
    try:
        conn = connect(path)
    except sqlite3.Error:
        return 0
    try:
        return conn.execute("SELECT COUNT(*) FROM character").fetchone()[0]
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# the one-shot migration off the JSON store
# --------------------------------------------------------------------------- #
def import_json(json_path, path=None, force=False):
    """Copy `fe_characters.json` in. Returns (accounts, characters).

    REFUSES a database that already holds characters unless `force` -- the
    import runs automatically on first use, and an import that overwrote live
    rows with a stale file would be indistinguishable from data loss. The JSON
    file is never modified or deleted: it stays as the backup, and clearing
    FE_DB falls back to it.
    """
    if not json_path or not os.path.exists(json_path):
        return 0, 0
    if not force and count(path):
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
        if save_roster(account, roster, path):
            accounts += 1
            chars += len(roster)
    return accounts, chars


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _show(path):
    print("database: %s" % os.path.abspath(path))
    if not os.path.exists(path):
        print("  (does not exist yet)")
        return
    for account, n in store_accounts(path).items():
        print("  %s  (%d character(s))" % (account, n))
        for c in load_roster(account, path) or []:
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
    ok = True
    db = os.path.join(tempfile.mkdtemp(prefix="festore"), "fe.db")

    def check(label, cond):
        nonlocal ok
        print("  %s: %s" % (label, "OK" if cond else "FAIL"))
        ok &= bool(cond)

    print("festore selftest")
    rec = {"charid": 1, "name": "Lex", "unit": 0xFF, "sex": 1,
           "look1": 2, "look2": 3, "look3": 4, "look4": 5,
           "f24": 0, "f28": 0, "f2d": 0, "s38": "",
           "force": 3, "period_from": 0, "period_to": 0, "comment": "",
           "equip": [[1, 0x1234]], "items": [[1, 0x1234, 0], [2, 0x5678, 0, 3]],
           "skills": [0x2200, 0x2201], "palette": [0, 0x2200],
           "blacklist": [{"id": 0x40000000, "name": "Rival"}],
           # keys with no column, and a bool: these must survive via `extra`
           "w70": -1, "fac": 1.5, "dc4": 7, "u8_b": 0, "tutorial_seen": True}
    check("an unknown account reads None, not []",
          load_roster("member:3", db) is None)
    check("save", save_roster("member:3", [rec], db))
    got = load_roster("member:3", db)
    check("round trip is key-for-key identical", got == [rec])
    check("no cross-account bleed", load_roster("member:9", db) is None)
    check("store_accounts", store_accounts(db) == {"member:3": 1})

    # a stored ZERO must not read back as absent -- _wallet_value depends on it
    save_roster("member:3", [dict(rec, gold=0)], db)
    check("a stored 0 survives as 0", load_roster("member:3", db)[0]["gold"] == 0)
    save_roster("member:3", [dict(rec)], db)
    check("an ABSENT key stays absent",
          "gold" not in load_roster("member:3", db)[0])

    # an EMPTY list is not a missing one either -- an emptied bag must persist
    save_roster("member:3", [dict(rec, items=[])], db)
    check("an emptied bag stays empty (not absent)",
          load_roster("member:3", db)[0]["items"] == [])

    # tuples go in, lists come back -- exactly what the JSON store did
    save_roster("member:3", [dict(rec, equip=[(1, 0x1234)])], db)
    check("tuples round-trip as lists, as JSON did",
          load_roster("member:3", db)[0]["equip"] == [[1, 0x1234]])

    # order is the roster order, not the charid order
    save_roster("member:3", [dict(rec, charid=7), dict(rec, charid=2)], db)
    check("roster order is preserved",
          [c["charid"] for c in load_roster("member:3", db)] == [7, 2])
    check("next_charid clears the whole store", next_charid([], db) == 8)
    check("and an uncommitted roster too",
          next_charid([{"charid": 40}], db) == 41)

    save_roster("member:3", [], db)
    check("empty save clears the account", load_roster("member:3", db) is None)
    check("and takes it out of store_accounts", store_accounts(db) == {})

    # created_at survives a rewrite; updated_at moves
    save_roster("member:3", [rec], db)
    conn = connect(db)
    born = conn.execute("SELECT created_at FROM character").fetchone()[0]
    conn.close()
    time.sleep(1.01)                    # _now() has one-second resolution
    save_roster("member:3", [dict(rec, name="Renamed")], db)
    conn = connect(db)
    row = conn.execute(
        "SELECT created_at, updated_at FROM character").fetchone()
    conn.close()
    check("created_at survives a rewrite", row[0] == born)
    check("updated_at moves", row[1] != born)

    # update_roster: the read-modify-write feworld uses for every mutation
    def bump(roster):
        roster[0]["gold"] = 12345
        return "done"

    check("update_roster returns what mutate did",
          update_roster("member:3", bump, db) == "done")
    check("and committed it", load_roster("member:3", db)[0]["gold"] == 12345)
    check("update_roster on an unknown account writes nothing",
          update_roster("member:404", bump, db) is None)
    check("and did not create it", load_roster("member:404", db) is None)

    def boom(roster):
        roster[0]["gold"] = 999
        raise ValueError("mutate blew up")

    try:
        update_roster("member:3", boom, db)
        check("an exception in mutate propagates", False)
    except ValueError:
        check("an exception in mutate propagates", True)
    check("and rolls the whole transaction back",
          load_roster("member:3", db)[0]["gold"] == 12345)

    # WARNING: A FAULT MUST NOT LOOK LIKE AN EMPTY ROSTER -- see StoreUnavailable
    broken = os.path.join(os.path.dirname(db), "not-a-database.db")
    with open(broken, "wb") as fh:
        fh.write(b"this is definitely not a sqlite file" * 8)
    forget_schema(broken)
    try:
        load_roster("member:3", broken)
        check("a corrupt database raises rather than reading empty", False)
    except StoreUnavailable:
        check("a corrupt database raises rather than reading empty", True)
    except sqlite3.Error:
        check("a corrupt database raises StoreUnavailable, not a raw "
              "sqlite3.Error", False)

    unopenable = os.path.dirname(db)            # a DIRECTORY, not a file
    forget_schema(unopenable)
    try:
        load_roster("member:3", unopenable)
        check("an unopenable database raises too", False)
    except StoreUnavailable:
        check("an unopenable database raises too", True)

    check("...while a genuinely unknown account still reads None",
          load_roster("member:404", db) is None)

    # KEY: THE SCHEMA-1 -> 2 MIGRATION, against a database built the way the real
    # one was: prod's fe.db was created at schema 1 with three live characters
    # in it, so "the new column only reaches a fresh database" is data loss with
    # extra steps.
    v1 = os.path.join(os.path.dirname(db), "v1.db")
    forget_schema(v1)
    c = sqlite3.connect(v1)
    c.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE character (
            account TEXT NOT NULL, charid INTEGER NOT NULL,
            slot_ord INTEGER NOT NULL DEFAULT 0,
            name TEXT, "force" INTEGER, gold INTEGER, items TEXT, extra TEXT,
            created_at TEXT, updated_at TEXT,
            PRIMARY KEY (account, charid));
        INSERT INTO meta VALUES ('schema', '1');
        INSERT INTO character (account, charid, name, "force", gold, items)
            VALUES ('member:3', 1, 'Lex', 3, 777, '[[1,4660,0]]');
        INSERT INTO character (account, charid, name, "force", gold, items)
            VALUES ('member:6', 2, 'Elena', 1, 0, '[]');
    """)
    c.commit()
    c.close()
    pre = {"cols": None}
    conn = connect(v1)                  # <- the migration runs here
    pre["cols"] = {r[1] for r in conn.execute("PRAGMA table_info(character)")}
    ver = conn.execute(
        "SELECT value FROM meta WHERE key = 'schema'").fetchone()[0]
    conn.close()
    check("the migration adds the schema-2 columns",
          {"exp", "class_levels", "bag_size"} <= pre["cols"])
    check("and stamps the new version", ver == str(SCHEMA_VERSION))
    got = load_roster("member:3", v1)
    check("every schema-1 character survives",
          [c2["name"] for c2 in (load_roster("member:3", v1) or [])
           + (load_roster("member:6", v1) or [])] == ["Lex", "Elena"])
    check("...with its old values intact",
          got[0]["gold"] == 777 and got[0]["force"] == 3
          and got[0]["items"] == [[1, 4660, 0]])
    check("a new column reads ABSENT, not 0 -- so the knob still seeds it",
          "exp" not in got[0] and "bag_size" not in got[0])
    # and the new columns are writable on the migrated file
    update_roster("member:3", lambda r: r[0].update(
        {"exp": 4242, "bag_size": 40, "class_levels": {"2": 30}}), v1)
    got = load_roster("member:3", v1)
    check("the migrated database stores progression",
          got[0]["exp"] == 4242 and got[0]["bag_size"] == 40
          and got[0]["class_levels"] == {"2": 30})
    forget_schema(v1)
    check("re-running the migration is a no-op (idempotent)",
          connect(v1) is not None and load_roster("member:3", v1)[0]["exp"] == 4242)

    # the JSON import
    jp = os.path.join(os.path.dirname(db), "chars.json")
    with open(jp, "w", encoding="utf-8") as fh:
        json.dump({"accounts": {
            "member:5": [{"charid": 1, "name": "Old", "force": 2}],
            "member:6": []}}, fh)
    save_roster("member:3", [], db)
    n_a, n_c = import_json(jp, db)
    check("import brings the JSON store in", (n_a, n_c) == (1, 1))
    check("and skips accounts with no characters",
          list(store_accounts(db)) == ["member:5"])
    check("a second import is refused (rows exist)",
          import_json(jp, db) == (0, 0))
    check("the JSON file is left alone", os.path.exists(jp))

    print("ALL OK" if ok else "FAILURES ABOVE")
    return 0 if ok else 1


if __name__ == "__main__":
    argv = sys.argv[1:]
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
    elif argv[0] == "--selftest":
        raise SystemExit(_selftest())
    elif argv[0] == "--show":
        _show(argv[1] if len(argv) > 1 else DB_PATH)
    elif argv[0] == "--import":
        if len(argv) < 2:
            raise SystemExit("--import wants the fe_characters.json path")
        _db = argv[2] if len(argv) > 2 and not argv[2].startswith("-") \
            else DB_PATH
        a, c = import_json(argv[1], _db, force="--force" in argv)
        print("imported %d character(s) for %d account(s) into %s" % (c, a, _db)
              if c else "nothing imported (the database already holds "
                        "characters, or the file is empty) -- --force overrides")
    else:
        raise SystemExit("unknown option %r; try --help" % argv[0])
