#!/usr/bin/env python3
"""fe_import_test.py -- `python fedb.py import fe_db | fe_mail_db | world`.

Run from the repo root:  python tools/fe_import_test.py

Every OLD source here is written by the code that wrote it on a real server:
festore.py, femail.py and the world modules as they were at PRE_MIGRATION,
the last commit before PostgreSQL, taken from git (`git archive`) into a
temporary directory and run in a process of their own. A shallow clone lacks
that commit; run `git fetch --unshallow` first.

The importer runs as the operator runs it, `python fedb.py import ...` in a
subprocess, against a throwaway PostgreSQL database (tools/fepg.py, or the one
fe_run_all.py made for this suite). What it proves, per source:

  * --dry-run writes nothing, not even the schema;
  * the import brings every row over, read back through today's festore,
    femail and world modules equal to what the old code read;
  * a second run changes nothing and exits 0;
  * a target that holds rows is refused (exit 2) unless --merge, and --merge
    adds only the missing keys and keeps the database's version of the rest;
  * a row that cannot be carried over is reported and skipped;
  * the source is not written: same bytes, same mtime, no journal beside it.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
_SERVICES = os.path.join(ROOT, "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fepg  # noqa: E402

#: The last commit whose festore, femail and world modules wrote files.
PRE_MIGRATION = "b266a26b9f12641a380e83130980a1a2913761de"

OUT = sys.__stdout__
CHECKS = []


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))
    if not cond:
        raise AssertionError(label + (": " + detail if detail else ""))


# --------------------------------------------------------------------------- #
# the old code
# --------------------------------------------------------------------------- #
def old_tree(tmp):
    """services/ as it was at PRE_MIGRATION, unpacked under `tmp`."""
    z = os.path.join(tmp, "old.zip")
    r = subprocess.run(["git", "-C", ROOT, "archive", "--format=zip", "-o", z,
                        PRE_MIGRATION, "services"], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit("git archive %s failed (a shallow clone? run git fetch "
                         "--unshallow): %s" % (PRE_MIGRATION, r.stderr.strip()))
    with zipfile.ZipFile(z) as zf:
        zf.extractall(tmp)
    os.remove(z)
    return os.path.join(tmp, "services")


def old_run(old, script, *argv):
    """Run `script` with the old services/ first on sys.path and as the
    working directory. Returns what it printed on stdout, parsed as JSON when
    it is."""
    env = dict(os.environ)
    for k in ("POL_DATABASE_URL", "FE_DB", "FE_MAIL_DB", "FE_BLOB_LOG"):
        env.pop(k, None)
    r = subprocess.run([sys.executable, "-c",
                        "import sys; sys.path.insert(0, '.')\n" + script] + list(argv),
                       cwd=old, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    if r.returncode != 0:
        raise AssertionError("old code failed:\n%s\n%s" % (r.stdout, r.stderr))
    lines = [x for x in r.stdout.splitlines() if x.startswith("@@")]
    return json.loads(lines[-1][2:]) if lines else None


def fedb_import(url, *argv):
    """`python fedb.py import ...` against `url`: (exit code, output)."""
    env = dict(os.environ, POL_DATABASE_URL=url)
    r = subprocess.run([sys.executable, "fedb.py", "import"] + list(argv),
                       cwd=_SERVICES, env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=120)
    return r.returncode, r.stdout + r.stderr


def fingerprint(path):
    """What must not change about a source: every file's bytes and mtime, and
    the names in its directory (a journal would appear there)."""
    d = path if os.path.isdir(path) else os.path.dirname(path)
    out = {"names": sorted(os.listdir(d))}
    files = [os.path.join(d, n) for n in out["names"]] if os.path.isdir(path) \
        else [path]
    for f in files:
        if os.path.isfile(f):
            with open(f, "rb") as fh:
                out[f] = (hashlib.sha256(fh.read()).hexdigest(),
                          os.stat(f).st_mtime_ns)
    return out


def use(url):
    """Point this process's polcore at `url`."""
    from polcore import db
    import fedb
    import festate
    db.close()
    os.environ["POL_DATABASE_URL"] = url
    db.configure()
    fedb.forget_schema()
    festate.forget()


def norm(x):
    return json.loads(json.dumps(x))


# --------------------------------------------------------------------------- #
# fe.db
# --------------------------------------------------------------------------- #
OLD_FE_DB = r'''
import json, sqlite3, sys, festore
path = sys.argv[1]
what = sys.argv[2]
if what == "build":
    rec = {"charid": 1, "name": "Lex", "unit": 255, "sex": 1,
           "look1": 2, "look2": 3, "look3": 4, "look4": 5,
           "f24": 0, "f28": 0, "f2d": 0, "s38": "",
           "force": 3, "period_from": 0, "period_to": 0, "comment": "",
           "gold": 0, "ring": 12, "crystal": 7, "total_score": 90,
           "tutorial": 1, "profile_comment": "hello",
           "exp": 4242, "class_levels": {"2": 30}, "bag_size": 40,
           "equip": [[1, 0x1234]], "items": [[1, 0x1234, 0], [2, 0x5678, 0, 3]],
           "skills": [0x2200, 0x2201], "palette": [0, 0x2200, 0, 0, 0, 0, 0, 0],
           "blacklist": [{"id": 0x40000000, "name": "Rival"}],
           "w70": -1, "fac": 1.5, "dc4": 7, "tutorial_seen": True}
    assert festore.save_roster("member:3", [rec, dict(rec, charid=4, name="Kit",
                                                      gold=None)], path)
    assert festore.save_roster("member:6", [dict(rec, charid=2, name="Elena",
                                                 force=1, items=[])], path)
    assert festore.save_roster("addr:198.51.100.7", [dict(rec, charid=9,
                                                          name="Guest")], path)
    c = sqlite3.connect(path)
    # a charid SQLite kept as text: it cannot be a unit id
    c.execute("INSERT INTO character (account, charid, slot_ord, name) "
              "VALUES ('member:8', 'x7', 0, 'Broken')")
    # a list column a hand edit broke: the old loader dropped it at login
    c.execute("INSERT INTO character (account, charid, slot_ord, name, items) "
              "VALUES ('member:8', 11, 1, 'Mended', '[[1, 2')")
    c.commit()
    c.close()
elif what == "add":
    festore.update_roster("member:6", lambda r: r.append(
        dict(r[0], charid=12, name="Late")), path)
    assert festore.save_roster("member:20", [{"charid": 20, "name": "New"}], path)
elif what == "v1":
    c = sqlite3.connect(path)
    c.executescript("""
        CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE character (
            account TEXT NOT NULL, charid INTEGER NOT NULL,
            slot_ord INTEGER NOT NULL DEFAULT 0,
            name TEXT, "force" INTEGER, gold INTEGER, items TEXT, extra TEXT,
            created_at TEXT, updated_at TEXT,
            PRIMARY KEY (account, charid));
        INSERT INTO meta VALUES ('schema', '1');
        INSERT INTO character (account, charid, name, "force", gold, items,
                               created_at, updated_at)
            VALUES ('member:3', 1, 'Lex', 3, 777, '[[1,4660,0]]',
                    '2026-09-01T10:00:00Z', '2026-09-02T10:00:00Z');
    """)
    c.commit()
    c.close()
out = {}
c = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
accts = [r[0] for r in c.execute("SELECT DISTINCT account FROM character")]
born = {"%s/%s" % (r[0], r[1]): r[2] for r in c.execute(
    "SELECT account, charid, created_at FROM character")}
c.close()
for a in accts:
    try:
        out[a] = festore.load_roster(a, path)
    except Exception as e:
        out[a] = repr(e)
print("@@" + json.dumps({"rosters": out, "born": born}))
'''


def fe_db_checks(tmp, old, url):
    say("fe_db -- the old fe.db into fe_character")
    import festore
    from polcore import db
    use(url)
    src = os.path.join(tmp, "data", "fe.db")
    os.makedirs(os.path.dirname(src), exist_ok=True)
    old_view = old_run(old, OLD_FE_DB, src, "build")
    before = fingerprint(src)

    rc, out = fedb_import(url, "fe_db", src, "--dry-run")
    check("--dry-run exits 0", rc == 0, out)
    check("...and says what it would import (5 of the 6 rows)",
          re.search(r"characters to import\s+5\n", out) is not None, out)
    check("...and writes nothing, not even the schema",
          db.query_one("SELECT to_regclass('fe_character') IS NULL AS n")["n"],
          out)

    rc, out = fedb_import(url, "fe_db", src)
    check("the import exits 0", rc == 0, out)
    check("...and prints its counts", "characters imported" in out, out)
    check("the text charid is reported and skipped",
          "'member:8' / 'x7'" in out and "not an integer" in out, out)
    check("the broken list column is reported",
          "column items is not JSON" in out, out)
    want = {a: r for a, r in old_view["rosters"].items()}
    got = {a: festore.load_roster(a) for a in want}
    for a in ("member:3", "member:6", "addr:198.51.100.7"):
        check("%s reads back as the old festore read it" % a,
              norm(got[a]) == norm(want[a]),
              "%r != %r" % (got[a], want[a]))
    check("the row with the broken column came over as the old server read it",
          norm(got["member:8"]) == norm([c for c in want["member:8"]
                                         if c["charid"] != "x7"])
          and "items" not in got["member:8"][0], repr(got["member:8"]))
    check("a stored 0 stays 0 and an absent key stays absent",
          got["member:3"][0]["gold"] == 0 and "gold" not in got["member:3"][1])
    check("a bool rides through `extra` as a bool",
          got["member:3"][0]["tutorial_seen"] is True)
    born = {"%s/%s" % (r["account"], r["charid"]): r["created_at"]
            for r in db.query("SELECT account, charid, created_at FROM fe_character")}
    check("created_at is the old row's",
          born["member:3/1"] == old_view["born"]["member:3/1"])
    check("the source is untouched (bytes, mtime, no journal)",
          fingerprint(src) == before)

    n = festore.count()
    rc, out = fedb_import(url, "fe_db", src)
    check("a second run exits 0", rc == 0, out)
    check("...changes nothing", festore.count() == n)
    check("...and says so", "nothing to import" in out, out)

    # the old server went on: a new character, a new account
    old_run(old, OLD_FE_DB, src, "add")
    festore.update_roster("member:3", lambda r: r[0].update(gold=5555))
    rc, out = fedb_import(url, "fe_db", src)
    check("a target with rows is refused (exit 2) without --merge", rc == 2, out)
    check("...and nothing was written", festore.count() == n)
    rc, out = fedb_import(url, "fe_db", src, "--merge")
    check("--merge exits 0", rc == 0, out)
    check("...adds only the new characters",
          festore.count() == n + 2
          and [c["name"] for c in festore.load_roster("member:20")] == ["New"]
          and "Late" in [c["name"] for c in festore.load_roster("member:6")])
    check("...keeps the database's version of a character that differs",
          festore.load_roster("member:3")[0]["gold"] == 5555)
    check("...and lists it", "character member:3 / 1" in out, out)
    rc, out = fedb_import(url, "fe_db", src, "--merge")
    check("a second --merge is a no-op", rc == 0 and "nothing to import" in out
          and festore.count() == n + 2, out)

    say("fe_db -- an fe.db still at schema 1")
    v1 = os.path.join(tmp, "v1", "fe.db")
    os.makedirs(os.path.dirname(v1))
    url2 = fepg.pgtest.create_database()
    try:
        old_run(old, OLD_FE_DB, v1, "v1")
        before = fingerprint(v1)
        rc, out = fedb_import(url2, "fe_db", v1)
        check("a schema-1 fe.db imports", rc == 0, out)
        use(url2)
        got = festore.load_roster("member:3")
        check("...with its values", got and got[0]["gold"] == 777
              and got[0]["items"] == [[1, 4660, 0]] and "exp" not in got[0],
              repr(got))
        check("...and is not migrated in place (the old code would have)",
              fingerprint(v1) == before)
    finally:
        use(url)
        fepg.pgtest.drop_database(url2)


# --------------------------------------------------------------------------- #
# fe_mail.db
# --------------------------------------------------------------------------- #
OLD_FE_MAIL = r'''
import json, sqlite3, sys, types, femail
path, what = sys.argv[1], sys.argv[2]
args = types.SimpleNamespace(mail_db=path, mail_order="old")
if what == "build":
    ids = []
    for n in range(5):
        ids.append(femail.box_put(args, "member:3/1", 1, "Elena", "Lex",
                                  "Hello %d" % n, "Text %d" % n,
                                  attach="Attach:%d" % n if n == 2 else ""))
        femail.box_put(args, "member:6/2", 0, "Elena", "Lex", "Hello %d" % n,
                       "Text %d" % n, read=1)
    femail.box_mark_read(args, ids[0])
    # the newest mail is deleted: its id must never be handed out again
    top = max(r[0] for r in sqlite3.connect(path).execute("SELECT id FROM mail"))
    femail.box_delete(args, "member:6/2", 0, top)
    c = sqlite3.connect(path)
    c.execute("INSERT INTO mail (owner, folder, sent_at, from_name, to_name, "
              "date, subject, text) VALUES ('member:3/1', 1, 'soon', 'A', 'B', "
              "'26/09/01 10:00', 'bad time', 'x')")
    c.commit()
    c.close()
elif what == "add":
    femail.box_put(args, "member:3/1", 1, "Late", "Lex", "Later", "Text")
c = sqlite3.connect("file:%s?mode=ro" % path.replace("\\", "/"), uri=True)
c.row_factory = sqlite3.Row
rows = [dict(r) for r in c.execute("SELECT * FROM mail ORDER BY id")]
seq = c.execute("SELECT seq FROM sqlite_sequence WHERE name='mail'").fetchone()[0]
c.close()
print("@@" + json.dumps({"rows": rows, "seq": seq}))
'''


def fe_mail_checks(tmp, old, url):
    say("fe_mail_db -- the old fe_mail.db into fe_mail")
    import femail
    from polcore import db
    use(url)
    src = os.path.join(tmp, "data", "fe_mail.db")
    view = old_run(old, OLD_FE_MAIL, src, "build")
    good = [r for r in view["rows"] if isinstance(r["sent_at"], int)]
    before = fingerprint(src)

    rc, out = fedb_import(url, "fe_mail_db", src, "--dry-run")
    # (the fe_db import above applied the migrations, so the table is there)
    check("--dry-run exits 0 and writes nothing", rc == 0 and db.query_one(
        "SELECT COUNT(*) AS n FROM fe_mail")["n"] == 0, out)
    rc, out = fedb_import(url, "fe_mail_db", src)
    check("the import exits 0", rc == 0, out)
    check("the mail with a text sent_at is reported and skipped",
          "sent_at is not an integer" in out, out)
    got = {r["id"]: r for r in db.query("SELECT * FROM fe_mail")}
    check("every other mail came over, id for id",
          sorted(got) == sorted(r["id"] for r in good),
          "%r" % sorted(got))
    check("...column for column",
          all(all(got[r["id"]][k] == v for k, v in r.items()) for r in good))
    args = type("A", (), {"mail_order": "old"})()
    box = femail.box_page(args, "member:3/1", 1, 0, 50)
    check("today's femail reads the box in the old order",
          [m["subject"] for m in box] == ["Hello %d" % n for n in range(5)])
    new_id = femail.box_put(args, "member:9/1", 1, "A", "B", "s", "t")
    check("a new mail's id is above every id the old server handed out "
          "(including the deleted one)", new_id > view["seq"],
          "%d <= %d" % (new_id, view["seq"]))
    db.execute("DELETE FROM fe_mail WHERE id = %s", (new_id,))
    check("the source is untouched", fingerprint(src) == before)

    rc, out = fedb_import(url, "fe_mail_db", src)
    check("a second run is a no-op", rc == 0 and "nothing to import" in out, out)

    old_run(old, OLD_FE_MAIL, src, "add")
    db.execute("UPDATE fe_mail SET subject = 'Edited' WHERE id = %s",
               (good[1]["id"],))
    n = db.query_one("SELECT COUNT(*) AS n FROM fe_mail")["n"]
    rc, out = fedb_import(url, "fe_mail_db", src)
    check("a target with rows is refused (exit 2) without --merge",
          rc == 2 and db.query_one("SELECT COUNT(*) AS n FROM fe_mail")["n"] == n,
          out)
    rc, out = fedb_import(url, "fe_mail_db", src, "--merge")
    check("--merge adds only the new mail",
          rc == 0 and db.query_one("SELECT COUNT(*) AS n FROM fe_mail")["n"] == n + 1,
          out)
    check("...keeps the database's version of a mail that differs",
          db.query_one("SELECT subject FROM fe_mail WHERE id = %s",
                       (good[1]["id"],))["subject"] == "Edited"
          and "mail %d" % good[1]["id"] in out, out)


# --------------------------------------------------------------------------- #
# the world state files
# --------------------------------------------------------------------------- #
OLD_WORLD = r'''
import json, os, sys, types
import feworld as fw, fecampaign, feforce, femap, feident
from world import doors, mapcal, spawns, territory
d = sys.argv[1]
args = types.SimpleNamespace(
    territory_file=os.path.join(d, "fe_territory.json"),
    spawn_file=os.path.join(d, "fe_spawn.json"),
    town_file=os.path.join(d, "fe_town.json"),
    door_arrive_file=os.path.join(d, "fe_door_arrive.json"),
    mapcal_file=os.path.join(d, "fe_minimap_cal.json"),
    campaign_file=os.path.join(d, "fe_campaign.json"),
    force_file=os.path.join(d, "fe_force.json"), force_table={},
    map_discord_state=os.path.join(d, "fe_map_discord.json"),
    map_discord_webhook="https://discord.com/api/webhooks/1/token",
    map_discord_every=60, map_discord_view="world", map_discord_event_ttl=600,
    spawn_area="", territory="")
territory._TERRITORY = {17: 1, 12: 4, 11: 2}
fw.territory_save(args)
spawns.spawn_load(args)                     # seeds the file from fedata
spawns._SPAWN.setdefault(39, []).append(
    {"x": 186.5, "y": 21.0, "z": -132.5, "h": 90.0, "tag": "atk"})
fw.spawn_save(args)
fw.town_add(args, 39, "npcs", {"model": 233, "kind": 1, "level": 1, "x": 1.5,
                               "y": 2.0, "z": -3.25, "name": "Warrior_Weapon_Shop",
                               "script": 2102})
fw.town_add(args, 39, "doors", {"x": 186.5, "z": -132.5, "room": 10})
fw.town_add(args, 91, "buildings", {"type": 10, "model": 10, "gx": 201, "gz": 75})
fw.doorarr_set(args, 1203, 186.5, -132.5)
mapcal._MAPCAL = {"map01_00": [{"x": 117.2, "z": -18.2, "px": 197.0,
                                "py": 139.2, "note": "door"}]}
fw.mapcal_save(args)
fecampaign.load(args)
fecampaign._STATE[12] = fecampaign._row_from(
    {"phase": 1, "until": 1789341381.5, "atk": 3,
     "members": {"m3": {"a": "member:3", "c": 1, "n": 1, "side": "atk"}}})
fecampaign._NATIONS[3] = {"wars": 2, "wins": 1}
fecampaign.save(args)
feforce.force_set(args, 1, "name", "Hordaine Army")
feforce.force_set(args, 2, "judge", 1)
m = femap.Discord(args)
m.msg_id, m.events = "4242", [{"id": "4243", "t": 1000.0}]
m._save()
for n, key in enumerate(("member:3", "member:6")):
    feident.record_blob({"key": key, "member_id": n + 3, "nick": key[7:],
                         "ip": "198.51.100.%d" % n, "content_id": 7},
                        bytes(range(n, n + 52)),
                        path=os.path.join(d, "fe_blobid.jsonl"))
print("@@" + json.dumps(sorted(os.listdir(d))))
'''


def world_checks(tmp, old, url):
    say("world -- an old /data's world state into fe_world_state")
    import festate
    import feident
    from polcore import db
    use(url)
    src = os.path.join(tmp, "olddata")
    os.makedirs(src)
    names = old_run(old, OLD_WORLD, src)
    check("the old code wrote every world state file",
          set(festate.FILES.values()) | {"fe_blobid.jsonl"} <= set(names),
          repr(names))
    # the files an old /data also holds, which this import leaves alone
    for extra in ("fe_gmcmd.txt", "fe_map_board.json"):
        with open(os.path.join(src, extra), "w", encoding="utf-8") as fh:
            fh.write("{}\n" if extra.endswith(".json") else "/say hi\n")
    before = fingerprint(src)

    rc, out = fedb_import(url, "world", src, "--dry-run")
    check("--dry-run exits 0 and writes nothing", rc == 0 and db.query_one(
        "SELECT (SELECT COUNT(*) FROM fe_world_state)"
        " + (SELECT COUNT(*) FROM fe_blob_observation) AS n")["n"] == 0, out)
    rc, out = fedb_import(url, "world", src)
    check("the import exits 0", rc == 0, out)
    check("...and names the files it leaves alone",
          "fe_gmcmd.txt: not imported" in out
          and "fe_map_board.json: not imported" in out, out)
    for name, fname in festate.FILES.items():
        with open(os.path.join(src, fname), encoding="utf-8") as fh:
            want = json.load(fh)
        got = festate.read(festate.location(name, None))
        check("%s is the old %s, key order included" % (name, fname),
              json.dumps(got) == json.dumps(want),
              "%s != %s" % (json.dumps(got)[:80], json.dumps(want)[:80]))
    with open(os.path.join(src, "fe_blobid.jsonl"), encoding="utf-8") as fh:
        want = [json.loads(x) for x in fh.read().splitlines() if x.strip()]
    check("the blob observations came over, in order",
          feident.blob_records() == want)
    check("the source is untouched", fingerprint(src) == before)

    # today's modules read what was imported
    import types
    import feworld
    import fecampaign
    import feforce
    a = types.SimpleNamespace(territory_file=None, territory="", spawn_file=None,
                              spawn_area="", town_file=None, campaign_file=None,
                              force_file=None, force_table={})
    feworld.territory_load(a)
    check("territory_load serves the imported owner",
          feworld.territory_owner(12, a) == 4)
    check("the spawn store serves the imported point",
          any(r["tag"] == "atk" for r in feworld.spawn_load(a).get(39, [])))
    check("the town serves the imported keeper",
          feworld.town_get(a, 39, "npcs")[0]["script"] == 2102)
    fecampaign.load(a)
    check("the campaign is the imported one",
          fecampaign.state_of(12)["atk"] == 3)
    check("the force rows are the imported ones",
          feforce.force_rows(a)[1]["name"] == "Hordaine Army")

    rc, out = fedb_import(url, "world", src)
    check("a second run is a no-op", rc == 0 and "nothing to import" in out, out)

    say("world -- a broken file, a store that is already there, --merge")
    url2 = fepg.pgtest.create_database()
    try:
        bad = os.path.join(tmp, "baddata")
        shutil.copytree(src, bad)
        with open(os.path.join(bad, "fe_town.json"), "w", encoding="utf-8") as fh:
            fh.write('{"39": {"npcs": [')
        with open(os.path.join(bad, "fe_blobid.jsonl"), "a", encoding="utf-8") as fh:
            fh.write("not json\n")
        rc, out = fedb_import(url2, "world", bad)
        check("a broken file is reported and skipped, the rest imported",
              rc == 0 and "fe_town.json: not readable JSON" in out
              and "fe_blobid.jsonl line 3" in out, out)
        use(url2)
        check("...so there is no town row",
              festate.read(festate.location("town", None)) is None)
        festate.write(festate.location("territory", None),
                      {"12": 5, "30": 1}, sort_keys=True)
        rc, out = fedb_import(url2, "world", src)
        check("a target with rows is refused (exit 2) without --merge",
              rc == 2 and festate.read(festate.location("town", None)) is None,
              out)
        rc, out = fedb_import(url2, "world", src, "--merge")
        check("--merge exits 0", rc == 0, out)
        check("...adds the missing store",
              festate.read(festate.location("town", None))["39"]["npcs"][0]
              ["script"] == 2102)
        terr = festate.read(festate.location("territory", None))
        check("...adds the keys a store lacks, keeps the ones it has",
              terr == {"12": 5, "30": 1, "11": 2, "17": 1}, repr(terr))
        check("...and lists the one it kept", "territory: 12" in out, out)
        rc, out = fedb_import(url2, "world", src, "--merge")
        check("a second --merge is a no-op", rc == 0 and "nothing" in out, out)
    finally:
        use(url)
        fepg.pgtest.drop_database(url2)


def main():
    ap = argparse.ArgumentParser()
    ap.parse_args()
    url = fepg.fresh_database()
    if url is None:
        return fepg.skip_or_fail("fe_import_test")
    tmp = tempfile.mkdtemp(prefix="fe_import_")
    try:
        old = old_tree(tmp)
        fe_db_checks(tmp, old, url)
        fe_mail_checks(tmp, old, url)
        world_checks(tmp, old, url)
    except AssertionError as e:
        say("[fe_import_test] FAIL: %s" % e)
        return 1
    finally:
        from polcore import db
        db.close()
        shutil.rmtree(tmp, ignore_errors=True)
    say("[fe_import_test] OK -- %d checks" % len(CHECKS))
    return 0


if __name__ == "__main__":
    sys.exit(main())
