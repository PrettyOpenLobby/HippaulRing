#!/usr/bin/env python3
"""feimport.py -- bring an old server's Fantasy Earth files into PostgreSQL.

Run through fedb.py, with POL_DATABASE_URL naming the database:

    python fedb.py import fe_db FILE        old fe.db (SQLite) into fe_character
    python fedb.py import fe_mail_db FILE   old fe_mail.db (SQLite) into fe_mail
    python fedb.py import world DIR         the world state files of an old /data
                                            into fe_world_state and
                                            fe_blob_observation

    --dry-run   read everything, write nothing, print what would happen
    --merge     import into tables that already hold rows: only keys that are
                not there yet are added, and the keys whose contents differ are
                listed and keep the database's version

The source is opened read-only (SQLite `mode=ro`) and never written. Each run
is one transaction: it all goes in, or nothing does. A row that cannot be
carried over is reported and skipped; the rest goes in. A second run over the
same database finds nothing missing, changes nothing and says so.

Exit status: 0 done (or nothing to do), 1 an error (nothing written), 2
refused because the target already holds rows and --merge was not given.

The order on an old server: `fe_db` first. The old fe_characters.json is only
for a server that never had an fe.db (`python festore.py --import FILE`),
because it stopped being written when fe.db took over; felobby does not
import it automatically while an fe.db sits beside it.
"""
import argparse
import json
import os
import pathlib
import sqlite3
import sys
import time

import fedb
from fedb import db

EXIT_OK, EXIT_ERROR, EXIT_REFUSED = 0, 1, 2

_INT64 = (-(1 << 63), (1 << 63) - 1)

#: The advisory lock an import holds for its transaction, so two imports
#: cannot interleave.
IMPORT_LOCK = "fe_import"


class ImportFailed(Exception):
    """The source cannot be read at all; nothing was written."""


class Report:
    """What an import found, printed at the end."""

    def __init__(self, kind, source, dry_run, merge):
        self.kind, self.source = kind, source
        self.dry_run, self.merge = dry_run, merge
        self.counts = {}            # label -> n, in insertion order
        self.skipped = []           # (what, why)
        self.notes = []             # (what, note)
        self.kept = []              # keys already in the database, differing

    def count(self, label, n=1):
        self.counts[label] = self.counts.get(label, 0) + n

    def skip(self, what, why):
        self.skipped.append((what, why))

    def note(self, what, text):
        self.notes.append((what, text))

    def print(self, out):
        out("import %s from %s%s" % (self.kind, self.source,
                                     " (dry run: nothing written)"
                                     if self.dry_run else ""))
        for label, n in self.counts.items():
            out("  %-46s %d" % (label, n))
        if self.kept:
            out("  already in the database with other contents, kept as they "
                "are (%d):" % len(self.kept))
            for k in self.kept:
                out("    %s" % k)
        if self.skipped:
            out("  skipped, cannot be carried over (%d):" % len(self.skipped))
            for what, why in self.skipped:
                out("    %s: %s" % (what, why))
        if self.notes:
            out("  notes:")
            for what, text in self.notes:
                out("    %s: %s" % (what, text))


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool) \
        and _INT64[0] <= v <= _INT64[1]


def _open_ro(path):
    """A read-only SQLite connection to `path`. `mode=ro` never creates the
    file and never writes to it (no journal, no schema)."""
    if not os.path.isfile(path):
        raise ImportFailed("%s is not a file" % path)
    uri = pathlib.Path(os.path.abspath(path)).as_uri() + "?mode=ro"
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlite3.Error as e:
        raise ImportFailed("%s cannot be opened as SQLite: %s" % (path, e))
    return conn


def _tables(conn):
    return {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}


def _table_exists(name):
    return db.query_one("SELECT to_regclass(%s) IS NOT NULL AS ok",
                        (name,))["ok"]


def _prepare(dry_run):
    """Apply CrystalRing's migrations (not on a dry run, which writes
    nothing: a table that is not there yet reads as empty)."""
    if not dry_run:
        fedb.ensure_schema(log=lambda m: print("[fedb] %s" % m))


def _refuse(report, out, what):
    report.print(out)
    out("REFUSED: %s already holds rows. Nothing was written. Run again with "
        "--merge to add only what is missing." % what)
    return EXIT_REFUSED


# --------------------------------------------------------------------------- #
# fe.db -> fe_character
# --------------------------------------------------------------------------- #
def _old_record(row, report, label):
    """One old `character` row as the record the old festore.row_to_record
    returned. A list column or `extra` that is not valid JSON is left out, as
    the old loader left it out at every login, and reported."""
    import festore
    rec = {}
    internal = ("account", "slot_ord", "extra", "created_at", "updated_at")
    for k in row.keys():
        v = row[k]
        if k in internal or v is None:
            continue
        if isinstance(v, bytes):
            raise ValueError("column %s holds binary data" % k)
        if k in festore.JSON_COLUMNS:
            try:
                rec[k] = json.loads(v)
            except (TypeError, ValueError):
                report.note(label, "column %s is not JSON and was left out, as "
                            "the old server read it" % k)
            continue
        rec[k] = v
    blob = row["extra"] if "extra" in row.keys() else None
    if blob:
        try:
            more = json.loads(blob)
            if not isinstance(more, dict):
                raise ValueError("not an object")
            rec.update(more)
        except (TypeError, ValueError):
            report.note(label, "`extra` is not a JSON object and was left out, "
                        "as the old server read it")
    return rec


def import_fe_db(path, merge=False, dry_run=False, out=print):
    import festore
    report = Report("fe_db", path, dry_run, merge)
    src = _open_ro(path)
    try:
        if "character" not in _tables(src):
            raise ImportFailed("%s has no `character` table; it is not an "
                               "fe.db" % path)
        rows = src.execute("SELECT * FROM character"
                           " ORDER BY account, slot_ord, charid").fetchall()
    finally:
        src.close()
    report.count("characters in the source", len(rows))

    plan = []                       # (key, cols, rec)
    seen = {}
    for row in rows:
        account, charid = row["account"], row["charid"]
        label = "character %r / %r" % (account, charid)
        if not isinstance(account, str) or not account or "\x00" in account:
            report.skip(label, "the account is not text")
            continue
        if not _is_int(charid):
            report.skip(label, "charid is not an integer")
            continue
        slot = row["slot_ord"] if "slot_ord" in row.keys() else 0
        if not _is_int(slot):
            report.skip(label, "slot_ord is not an integer")
            continue
        if charid in seen and seen[charid] != account:
            report.skip(label, "charid %d is also %r's in the source; a charid "
                        "is the unit id and must be unique" % (charid,
                                                               seen[charid]))
            continue
        try:
            rec = _old_record(row, report, label)
            cols, _ = festore.record_to_row(rec, account, slot)
        except (TypeError, ValueError) as e:
            report.skip(label, str(e))
            continue
        now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        for k in ("created_at", "updated_at"):
            v = row[k] if k in row.keys() else None
            cols[k] = v if isinstance(v, str) and "\x00" not in v else now
        seen[charid] = account
        plan.append(((account, charid), cols, rec))

    _prepare(dry_run)
    have, owner = {}, {}
    if _table_exists("fe_character"):
        for r in db.query("SELECT * FROM fe_character"):
            have[(r["account"], r["charid"])] = r
            owner[r["charid"]] = r["account"]
    missing, there = [], 0
    for key, cols, rec in plan:
        if key in have:
            there += 1
            if festore.row_to_record(have[key]) != festore.row_to_record(cols):
                report.kept.append("character %s / %d" % key)
            continue
        if key[1] in owner:
            report.skip("character %r / %d" % key, "charid %d already belongs "
                        "to %r in the database" % (key[1], owner[key[1]]))
            continue
        missing.append((key, cols))
    report.count("already in the database", there)
    if not missing:
        report.print(out)
        out("nothing to import: every character that can be carried over is "
            "already in fe_character")
        return EXIT_OK
    if have and not merge:
        return _refuse(report, out, "fe_character")
    report.count("characters to import" if dry_run else "characters imported",
                 len(missing))
    report.count("accounts", len({k[0] for k, _ in missing}))
    if dry_run:
        report.print(out)
        return EXIT_OK
    with db.transaction(lock=IMPORT_LOCK) as conn:
        for key, cols in missing:
            names = list(cols)
            db.execute("INSERT INTO fe_character (%s) VALUES (%s)"
                       % (",".join('"%s"' % c for c in names),
                          ",".join("%s" for _ in names)),
                       [cols[c] for c in names], conn=conn)
    report.print(out)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# fe_mail.db -> fe_mail
# --------------------------------------------------------------------------- #
_MAIL_INT = ("id", "folder", "ref", "sent_at", "read")
_MAIL_TEXT = ("owner", "from_name", "to_name", "date", "subject", "text",
              "attach")


def _mail_row(row):
    """The fe_mail columns for one old `mail` row. Raises ValueError with the
    reason when it cannot be carried over."""
    keys = row.keys()
    out = {}
    for k in _MAIL_INT:
        v = row[k] if k in keys else None
        if v is None and k == "ref":
            v = 0xFFFFFFFF
        if v is None and k == "read":
            v = 0
        if not _is_int(v):
            raise ValueError("%s is not an integer (%r)" % (k, v))
        out[k] = v
    for k in _MAIL_TEXT:
        v = row[k] if k in keys else None
        if v is None and k == "attach":
            v = ""
        if not isinstance(v, str):
            raise ValueError("%s is not text" % k)
        if "\x00" in v:
            raise ValueError("%s holds a NUL character, which PostgreSQL's text "
                             "cannot store" % k)
        out[k] = v
    if out["id"] <= 0:
        raise ValueError("id %d is not a mail id" % out["id"])
    if not -(1 << 31) <= out["folder"] < (1 << 31) or \
            not -(1 << 31) <= out["read"] < (1 << 31):
        raise ValueError("folder or read is out of range")
    return out


def import_fe_mail_db(path, merge=False, dry_run=False, out=print):
    report = Report("fe_mail_db", path, dry_run, merge)
    src = _open_ro(path)
    try:
        tables = _tables(src)
        if "mail" not in tables:
            raise ImportFailed("%s has no `mail` table; it is not an "
                               "fe_mail.db" % path)
        rows = src.execute("SELECT * FROM mail ORDER BY id").fetchall()
        seq = 0
        if "sqlite_sequence" in tables:
            r = src.execute("SELECT seq FROM sqlite_sequence WHERE name = 'mail'"
                            ).fetchone()
            seq = r[0] if r and _is_int(r[0]) else 0
    finally:
        src.close()
    report.count("mails in the source", len(rows))
    plan = []
    for row in rows:
        label = "mail %r" % (row["id"],)
        try:
            plan.append(_mail_row(row))
        except ValueError as e:
            report.skip(label, str(e))

    _prepare(dry_run)
    have = {}
    if _table_exists("fe_mail"):
        have = {r["id"]: r for r in db.query("SELECT * FROM fe_mail")}
    missing = []
    for m in plan:
        got = have.get(m["id"])
        if got is None:
            missing.append(m)
        elif any(got[k] != m[k] for k in m):
            report.kept.append("mail %d" % m["id"])
    report.count("already in the database", len(plan) - len(missing))
    if not missing:
        report.print(out)
        out("nothing to import: every mail that can be carried over is "
            "already in fe_mail")
        return EXIT_OK
    if have and not merge:
        return _refuse(report, out, "fe_mail")
    report.count("mails to import" if dry_run else "mails imported", len(missing))
    report.count("mailboxes", len({m["owner"] for m in missing}))
    top = max([seq] + [m["id"] for m in plan])
    if dry_run:
        report.note("ids", "the id sequence would move past %d" % top)
        report.print(out)
        return EXIT_OK
    with db.transaction(lock=IMPORT_LOCK) as conn:
        cols = list(_MAIL_INT + _MAIL_TEXT)
        sql = "INSERT INTO fe_mail (%s) VALUES (%s)" % (
            ",".join('"%s"' % c for c in cols), ",".join("%s" for _ in cols))
        for m in missing:
            db.execute(sql, [m[c] for c in cols], conn=conn)
        # never hand out an id the old server used, including those of mails
        # deleted since (SQLite's AUTOINCREMENT counter)
        n = db.query_one(
            "SELECT setval(pg_get_serial_sequence('fe_mail', 'id'),"
            " GREATEST(%s, (SELECT COALESCE(MAX(id), 1) FROM fe_mail),"
            " (SELECT last_value FROM fe_mail_id_seq))) AS n",
            (top,), conn=conn)["n"]
    report.note("ids", "the next new mail gets an id above %d" % n)
    report.print(out)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# the world state files -> fe_world_state, fe_blob_observation
# --------------------------------------------------------------------------- #
#: Files an old /data may hold that this import does not carry, and why.
WORLD_NOT_IMPORTED = {
    "fe.db": "the characters: python fedb.py import fe_db FILE",
    "fe_mail.db": "the mail: python fedb.py import fe_mail_db FILE",
    "fe_characters.json": "only for a server that never had fe.db: "
                          "python festore.py --import FILE",
    "fe_map_board.json": "live state, republished in Valkey (fe:map:board) "
                         "within seconds of the world starting with "
                         "--map-board on",
    "fe_gmcmd.txt": "the operator's command file; it stays a file",
}

BLOB_FILE = "fe_blobid.jsonl"


def _canon(doc):
    return json.dumps(doc, sort_keys=True, ensure_ascii=False)


def _world_sources(directory, report):
    """{name: document} for every world state file in `directory` that can be
    read, and the list of blob records."""
    import festate
    docs = {}
    files = dict(festate.FILES)
    files["door_arrive.v1"] = festate.FILES["door_arrive"] + ".v1"
    for name, fname in files.items():
        p = os.path.join(directory, fname)
        if not os.path.exists(p):
            continue
        try:
            with open(p, encoding="utf-8") as fh:
                doc = json.load(fh)
        except (OSError, ValueError) as e:
            report.skip(fname, "not readable JSON (%s)" % e)
            continue
        if not isinstance(doc, dict):
            report.skip(fname, "not a JSON object")
            continue
        try:
            json.dumps(doc, allow_nan=False)
        except ValueError:
            report.skip(fname, "holds NaN or Infinity, which PostgreSQL's JSON "
                        "cannot store")
            continue
        docs[name] = doc
    report.count("world state files read", len(docs))
    blobs = []
    p = os.path.join(directory, BLOB_FILE)
    if os.path.exists(p):
        try:
            with open(p, encoding="utf-8") as fh:
                lines = fh.read().splitlines()
        except (OSError, ValueError) as e:
            report.skip(BLOB_FILE, "not readable (%s)" % e)
            lines = []
        for n, line in enumerate(lines, 1):
            if not line.strip():
                continue
            try:
                rec = json.loads(line)
                if not isinstance(rec, dict):
                    raise ValueError("not a JSON object")
                json.dumps(rec, allow_nan=False)
            except ValueError as e:
                report.skip("%s line %d" % (BLOB_FILE, n), str(e))
                continue
            blobs.append(rec)
        report.count("blob observations read", len(blobs))
    for fname, why in WORLD_NOT_IMPORTED.items():
        if os.path.exists(os.path.join(directory, fname)):
            report.note(fname, "not imported here - " + why)
    return docs, blobs


def import_world(directory, merge=False, dry_run=False, out=print):
    import festate
    report = Report("world", directory, dry_run, merge)
    if not os.path.isdir(directory):
        raise ImportFailed("%s is not a directory" % directory)
    docs, blobs = _world_sources(directory, report)

    _prepare(dry_run)
    have_docs, have_blobs = {}, []
    if _table_exists("fe_world_state"):
        have_docs = {r["name"]: r["doc"] for r in db.query(
            "SELECT name, doc FROM fe_world_state")}
    if _table_exists("fe_blob_observation"):
        have_blobs = [r["rec"] for r in db.query(
            "SELECT rec FROM fe_blob_observation ORDER BY id")]

    inserts, merges = [], []            # (name, doc)
    for name, doc in docs.items():
        cur = have_docs.get(name)
        if cur is None:
            inserts.append((name, doc))
            continue
        if _canon(cur) == _canon(doc):
            continue
        if not isinstance(cur, dict):
            report.kept.append("%s (the stored document is not an object)" % name)
            continue
        if cur.get("_version") != doc.get("_version"):
            report.kept.append("%s (stored as version %s, the file is version "
                               "%s: not merged)" % (name, cur.get("_version", 1),
                                                    doc.get("_version", 1)))
            continue
        new_keys = [k for k in doc if k not in cur]
        differ = [k for k in doc if k in cur and _canon(cur[k]) != _canon(doc[k])]
        for k in differ:
            report.kept.append("%s: %s" % (name, k))
        if new_keys:
            merged = dict(cur)
            for k in new_keys:
                merged[k] = doc[k]
            merges.append((name, merged, len(new_keys)))

    pool = {}
    for rec in have_blobs:
        c = _canon(rec)
        pool[c] = pool.get(c, 0) + 1
    new_blobs = []
    for rec in blobs:
        c = _canon(rec)
        if pool.get(c):
            pool[c] -= 1
        else:
            new_blobs.append(rec)

    nonempty = bool(have_docs) or bool(have_blobs)
    work = bool(inserts) or bool(new_blobs) or (merge and bool(merges))
    report.count("stores already in the database",
                 len(docs) - len(inserts))
    if not work:
        report.print(out)
        if merges and not merge:
            out("nothing missing; %d store(s) in the database lack keys the "
                "files have, which --merge would add" % len(merges))
        else:
            out("nothing to import: the world state is already in "
                "fe_world_state")
        return EXIT_OK
    if nonempty and not merge:
        return _refuse(report, out, "fe_world_state or fe_blob_observation")
    verb = "to import" if dry_run else "imported"
    report.count("stores %s" % verb, len(inserts))
    for name, _ in inserts:
        report.note(name, "from %s" % (festate.FILES.get(name)
                                        or festate.FILES["door_arrive"] + ".v1"))
    if merge:
        report.count("stores merged (keys added)", len(merges))
        for name, _, n in merges:
            report.note(name, "%d key(s) added from the file" % n)
    report.count("blob observations %s" % verb, len(new_blobs))
    if dry_run:
        report.print(out)
        return EXIT_OK
    with db.transaction(lock=IMPORT_LOCK) as conn:
        for name, doc in inserts:
            festate.db_write(name, json.dumps(doc, ensure_ascii=False,
                                              allow_nan=False), conn=conn)
        if merge:
            for name, doc, _ in merges:
                festate.db_write(name, json.dumps(doc, ensure_ascii=False,
                                                  allow_nan=False), conn=conn)
        for rec in new_blobs:
            db.execute("INSERT INTO fe_blob_observation (at, member_key, rec)"
                       " VALUES (%s, %s, %s::json)",
                       (rec.get("at") if isinstance(rec.get("at"), str) else None,
                        rec.get("key") if isinstance(rec.get("key"), str) else None,
                        json.dumps(rec, sort_keys=True)), conn=conn)
    report.print(out)
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI (python fedb.py import ...)
# --------------------------------------------------------------------------- #
KINDS = {"fe_db": import_fe_db, "fe_mail_db": import_fe_mail_db,
         "world": import_world}


def main(argv, out=print):
    ap = argparse.ArgumentParser(
        prog="fedb.py import",
        description="Import an old server's Fantasy Earth files into the "
                    "database POL_DATABASE_URL names.")
    ap.add_argument("kind", choices=sorted(KINDS))
    ap.add_argument("source", help="the old file (fe_db, fe_mail_db) or the "
                                   "old /data directory (world)")
    ap.add_argument("--merge", action="store_true",
                    help="import into tables that already hold rows, adding "
                         "only keys that are not there yet")
    ap.add_argument("--dry-run", action="store_true",
                    help="read everything, write nothing")
    args = ap.parse_args(argv)
    try:
        return KINDS[args.kind](args.source, merge=args.merge,
                                dry_run=args.dry_run, out=out)
    except ImportFailed as e:
        print("error: %s" % e, file=sys.stderr)
        return EXIT_ERROR
    except fedb.errors() as e:
        print("error: the database: %s -- nothing was written" % e,
              file=sys.stderr)
        return EXIT_ERROR
    finally:
        db.close()
