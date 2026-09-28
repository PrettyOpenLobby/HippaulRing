#!/usr/bin/env python3
"""fe_worldstate_test.py -- the world state in PostgreSQL (services/festate.py).

Run from the repo root:  python tools/fe_worldstate_test.py

The stores the world rewrites while it runs (campaign, territory, spawn,
town, door arrivals, minimap calibration, forces, the Discord map message)
are rows of fe_world_state; feident's blob record is fe_blob_observation;
femap's board snapshot is a Valkey key. This proves, against a throwaway
database (tools/fepg.py, or the one fe_run_all.py made for this suite):

  * each module's load and save round-trips through its row, with no option
    given, and nothing is written into the repository;
  * the column is JSON, so a document keeps the key order it was written in;
  * the spawn row is seeded once from fedata/fe_spawn.json, and an edit is
    not replaced by the seed afterwards;
  * two writers of the town (two threads, two connections) keep both edits;
  * a store whose read failed is not written over;
  * with no database the stores stay in memory and no file appears;
  * the blob record keeps the newest FE_BLOB_LOG_MAX rows;
  * the board snapshot is published in kv with an expiry;
  * CrystalRing's migration numbers stay in its own range.
"""
import json
import os
import subprocess
import sys
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(_HERE)
_SERVICES = os.path.join(ROOT, "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fepg  # noqa: E402

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


def tree_status():
    r = subprocess.run(["git", "-C", ROOT, "status", "--porcelain",
                        "--untracked-files=all", "--", "services"],
                       capture_output=True, text=True)
    return r.stdout


def args(**kw):
    base = dict(territory_file=None, territory="", spawn_file=None,
                spawn_area="", town_file=None, campaign_file=None,
                force_file=None, force_table={}, door_arrive_file=None,
                mapcal_file=None, map_discord_state=None)
    base.update(kw)
    return types.SimpleNamespace(**base)


def main():
    url = fepg.fresh_database()
    if url is None:
        return fepg.skip_or_fail("fe_worldstate_test")
    import fedb
    from polcore import db
    db.configure()
    fedb.forget_schema()
    import festate
    import feworld
    import fecampaign
    import feforce
    import femap
    import feident
    from world import doors, mapcal, spawns, territory
    mods = types.SimpleNamespace(doors=doors, mapcal=mapcal, spawns=spawns,
                                 territory=territory)
    festate.forget()
    before = tree_status()
    try:
        run(db, fedb, festate, feworld, fecampaign, feforce, femap, feident,
            mods)
    except AssertionError as e:
        say("[fe_worldstate_test] FAIL: %s" % e)
        return 1
    finally:
        db.close()
    check("nothing under services/ changed while it ran", tree_status() == before,
          tree_status())
    say("[fe_worldstate_test] OK -- %d checks" % len(CHECKS))
    return 0


def run(db, fedb, festate, feworld, fecampaign, feforce, femap, feident, mods):
    doors, mapcal, spawns, territory = (mods.doors, mods.mapcal, mods.spawns,
                                        mods.territory)
    say("the schema")
    fedb.ensure_schema(log=lambda m: None)
    have = set(db.applied_migrations())
    check("1003 and 1004 applied", {1003, 1004} <= have, repr(sorted(have)))
    for version, name, _ in db.migration_files(fedb.MIGRATIONS_DIR):
        check("%s is numbered inside 1001..1999" % name, 1001 <= version <= 1999)
    core = db.migration_files()          # OpenLobby's own set
    check("no CrystalRing number is one of OpenLobby's",
          not {v for v, _, _ in core} & {v for v, _, _ in
                                          db.migration_files(fedb.MIGRATIONS_DIR)})
    t = db.query_one("SELECT data_type FROM information_schema.columns"
                     " WHERE table_name = 'fe_world_state' AND column_name = 'doc'")
    check("fe_world_state.doc is JSON, not JSONB", t["data_type"] == "json",
          repr(t))

    say("each store round-trips through its row")
    a = args()
    check("with no option a store is in the database",
          festate.in_database(feworld.territory_path(a))
          and str(feworld.territory_path(a)) == "fe_world_state[territory]")
    feworld.territory_load(a)
    with territory._TERRITORY_LOCK:
        territory._TERRITORY[12] = 4
    feworld.territory_save(a)
    check("territory", festate.read(feworld.territory_path(a)).get("12") == 4)
    with territory._TERRITORY_LOCK:
        territory._TERRITORY.clear()
    feworld.territory_load(a)
    check("...and territory_load reads it back", feworld.territory_owner(12, a) == 4)

    fecampaign.load(a)
    with fecampaign._LOCK:
        fecampaign._STATE[17] = fecampaign._row_from({"phase": 1, "atk": 2})
        fecampaign._NATIONS[2] = {"wars": 1, "wins": 0}
    fecampaign.save(a)
    with fecampaign._LOCK:
        fecampaign._STATE.clear()
    fecampaign.load(a)
    check("campaign", fecampaign.state_of(17)["atk"] == 2
          and fecampaign._NATIONS[2]["wars"] == 1)

    feforce._STATE.update(loaded=False, forces={}, judge=None)
    check("force", feforce.force_set(a, 3, "name", "Netzawar"))
    feforce._STATE.update(loaded=False, forces={}, judge=None)
    check("...and it survives a reload",
          feforce.force_rows(a)[3]["name"] == "Netzawar")

    feworld.doorarr_set(a, 1203, 186.5, -132.5)
    with doors._DOORARR_LOCK:
        doors._DOORARR.clear()
    feworld.doorarr_load(a)
    check("door arrivals", feworld.doorarr_get(1203) == (186.5, -132.5))
    check("...stored as version 2",
          festate.read(feworld.doorarr_path(a))["_version"] == 2)

    mapcal._MAPCAL.clear()
    mapcal._MAPCAL["map01_00"] = [{"x": 1.0, "z": 2.0, "px": 3.0,
                                   "py": 4.0, "note": "n"}]
    feworld.mapcal_save(a)
    mapcal._MAPCAL.clear()
    check("minimap calibration",
          feworld.mapcal_load(a)["map01_00"][0]["px"] == 3.0)

    d = types.SimpleNamespace(map_discord_webhook="https://example.invalid/h",
                              map_discord_every=60, map_discord_view="world",
                              map_discord_event_ttl=600, map_discord_state=None)
    m = femap.Discord(d)
    m.msg_id, m.events = "4242", [{"id": "1", "t": 2.0}]
    m._save()
    check("the Discord map message", femap.Discord(d).msg_id == "4242")

    say("key order: JSON keeps what was written")
    loc = festate.location("order_probe", None)
    festate.write(loc, {"b": 1, "a": {"z": 1, "y": 2}, "c": [3]})
    got = festate.read(loc)
    check("a document comes back in the order it was written",
          list(got) == ["b", "a", "c"] and list(got["a"]) == ["z", "y"],
          json.dumps(got))
    db.execute("DELETE FROM fe_world_state WHERE name = 'order_probe'")

    say("the spawn seed")
    seed = feworld.spawn_seed()
    with open(seed, encoding="utf-8") as fh:
        shipped = json.load(fh)
    db.execute("DELETE FROM fe_world_state WHERE name = 'spawn'")
    rows = feworld.spawn_load(a)
    check("an empty spawn row is seeded from fedata/fe_spawn.json",
          festate.read(feworld.spawn_path(a)) == shipped
          and sorted(rows) == sorted(int(k) for k in shipped))
    with spawns._SPAWN_LOCK:
        spawns._SPAWN.clear()
        spawns._SPAWN[39] = [{"x": 1.0, "y": 2.0, "z": 3.0, "h": 0.0,
                                      "tag": ""}]
    feworld.spawn_save(a)
    rows = feworld.spawn_load(a)
    check("...once: an edited store is not replaced by the seed",
          sorted(rows) == [39])

    say("the town: one change at a time, under the store's lock")
    db.execute("DELETE FROM fe_world_state WHERE name = 'town'")
    feworld.town_add(a, 39, "npcs", {"model": 1, "x": 0.0, "z": 0.0,
                                     "name": "first"})
    n_threads, per = 4, 5
    barrier = threading.Barrier(n_threads)

    def adder(i):
        barrier.wait()
        for j in range(per):
            # the thread lock is the module's; the advisory lock is what keeps
            # two PROCESSES apart, so go around the module lock here
            festate.update(feworld.town_path(a), lambda blob: (
                True, blob.setdefault("39", {}).setdefault("doors", []).append(
                    {"x": float(i), "z": float(j), "room": 10})), sort_keys=True)

    ts = [threading.Thread(target=adder, args=(i,)) for i in range(n_threads)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    check("%d concurrent writers lose no row" % n_threads,
          len(feworld.town_get(a, 39, "doors")) == n_threads * per,
          "%d rows" % len(feworld.town_get(a, 39, "doors")))
    check("town_undo drops the last npc",
          feworld.town_undo(a, 39, "npcs")["name"] == "first"
          and feworld.town_get(a, 39, "npcs") == [])
    check("town_undo on nothing returns None", feworld.town_undo(a, 39, "npcs")
          is None)
    row = feworld.town_set(a, 39, "doors", 0, {"room": "12"})
    check("town_set edits a row (text numbers become numbers)",
          row and row["room"] == 12
          and feworld.town_get(a, 39, "doors")[0]["room"] == 12)
    check("town_del deletes exactly one row",
          feworld.town_del(a, 39, "doors", 0) is not None
          and len(feworld.town_get(a, 39, "doors")) == n_threads * per - 1)
    check("--town-file '' still turns the town off",
          feworld.town_add(args(town_file=""), 39, "npcs", {}) is False)

    say("a store whose read failed is not written over")
    loc = feworld.territory_path(a)
    db.execute("ALTER TABLE fe_world_state RENAME TO fe_world_state_away")
    try:
        check("the read reports nothing (the module starts empty)",
              festate.read(loc) is None)
    finally:
        db.execute("ALTER TABLE fe_world_state_away RENAME TO fe_world_state")
    check("...and the next write is refused while the row exists",
          festate.write(loc, {}) is False)
    check("...so the stored board is intact",
          festate.read(loc).get("12") == 4)
    check("after a good read it writes again",
          festate.write(loc, {"12": 4, "18": 2}, sort_keys=True))

    say("no database: memory only, no file")
    saved = os.environ.pop("POL_DATABASE_URL")
    db.configure()
    festate.forget()
    try:
        check("a save without a database stores nothing and says so",
              festate.write(festate.location("territory", None), {"1": 1})
              is False)
        feworld.territory_save(a)
        feworld.spawn_save(a)
        fecampaign.save(a)
        check("...and no file appears in services/",
              not any(os.path.exists(os.path.join(_SERVICES, f)) for f in
                      ("fe_territory.json", "fe_spawn.json", "fe_campaign.json")))
    finally:
        os.environ["POL_DATABASE_URL"] = saved
        db.configure()
        festate.forget()
    check("the database kept the last good write",
          festate.read(loc) == {"12": 4, "18": 2})

    say("a --X-file still keeps the store in that file")
    tmp = os.path.join(os.environ.get("TEMP") or os.environ.get("TMPDIR")
                       or "/tmp", "fe_ws_%d.json" % os.getpid())
    try:
        f = args(territory_file=tmp)
        with territory._TERRITORY_LOCK:
            territory._TERRITORY[5] = 3
        feworld.territory_save(f)
        with open(tmp, encoding="utf-8") as fh:
            check("territory_save writes the named file", json.load(fh)["5"] == 3)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)

    say("feident's blob record: fe_blob_observation")
    feident.BLOB_LOG = None
    keep = feident.BLOB_LOG_MAX
    feident.BLOB_LOG_MAX = 3
    try:
        for n in range(5):
            feident.record_blob({"key": "member:%d" % n, "ip": "198.51.100.1"},
                                bytes([n]) * 52)
        recs = feident.blob_records()
        check("the newest FE_BLOB_LOG_MAX observations are kept, oldest first",
              [r["key"] for r in recs] == ["member:2", "member:3", "member:4"],
              repr([r["key"] for r in recs]))
        check("...each the record the file used to hold",
              recs[0]["fixed"] == (bytes([2]) * 52).hex()[8:16])
    finally:
        feident.BLOB_LOG_MAX = keep

    say("femap's board snapshot: a kv key with an expiry")
    from polcore import kv
    kv.reset()                           # the in-process store
    snap = {"title": "t", "updated": 1, "in_world": 2,
            "nations": [{"id": 1, "name": "N", "fields": 3, "online": 1}],
            "fields": [{"name": "F", "owner": 1,
                        "war": {"attacker": 2, "phase_name": "war",
                                "left_s": 5, "atk": 1, "def": 0}}]}
    check("--map-board off publishes nothing",
          femap.publish_board(types.SimpleNamespace(map_board="off",
                                                    map_board_file=""), snap)
          is False and femap.read_board() is None)
    check("--map-board on publishes",
          femap.publish_board(types.SimpleNamespace(map_board="on",
                                                    map_board_file=""), snap))
    got = femap.read_board()
    check("...the board the bot draws",
          got == femap.board_snapshot(snap) and got["wars"][0]["field"] == "F")
    ttl = kv.ttl(femap.BOARD_KEY)
    check("...under fe:map:board, expiring", femap.BOARD_KEY == "fe:map:board"
          and 0 < ttl <= femap.BOARD_TTL, repr(ttl))
    kv.reset()


if __name__ == "__main__":
    sys.exit(main())
