#!/usr/bin/env python3
"""fe_devtool_roles_test.py -- what the world builder says an NPC is FOR, and
whose capital it is looking at.

Run from services/:  python ../tools/fe_devtool_roles_test.py

The world builder colours every NPC by its function (world/devtool.py
npc_function) and badges every capital with its nation (capital_nation).
Both are read off data the server already acts on: the talk dispatcher's own
branches (events.event_script_for), the townsfolk table's kind column, the
roster's script slots, dat.pak's fet_area nation column, the --forces names
and the client's nation palette. These checks hold the derivation to that
data, so a wrong colour fails here rather than misleading an editor.
"""
import os
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import feworld      # noqa: E402,F401  (sets up the world package)
import fegamedata   # noqa: E402
import femap        # noqa: E402
from world import devtool, events, shops, territory, zones  # noqa: E402

CHECKS = []
OUT = sys.__stdout__
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

FORCES = "1:Netzawar,2:Cathedira,3:Elsord,4:Holdein,5:Gebrand"
CAPITALS = sorted(zones.INNER_CAPITAL_PAIRS)


def say(*a):
    print(*a, file=OUT, flush=True)


def check(label, cond, detail=""):
    CHECKS.append((label, bool(cond)))
    say("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                          ("  " + detail) if (detail and not cond) else ""))


class _Args:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def fn(name, script=0, event=None):
    return devtool.npc_function(name, script, event)[0]


def test_roster():
    say("\nthe twenty roster roles, in all five capitals")
    keys = [k for k, _ in devtool.NPC_FUNCTIONS]
    for cap in CAPITALS:
        roster = fegamedata.capital_roster(cap)
        got = {r["name"]: fn(r["name"], r["script"]) for r in roster}
        counts = {}
        for v in got.values():
            counts[v] = counts.get(v, 0) + 1
        check("%d: 20 roles, none unknown, every key in the legend" % cap,
              len(roster) == 20 and "unknown" not in got.values()
              and all(v in keys for v in got.values()), repr(got))
        check("%d: 10 shops, 1 bank, 1 inn, 3 managers, 1 trainer, 1 prize "
              "clerk, 3 guides" % cap,
              counts == {"shop": 10, "bank": 1, "inn": 1, "manager": 3,
                         "trainer": 1, "prize": 1, "guide": 3}, repr(counts))
        args = _Args(shop_types=None)
        check("%d: 'shop' is exactly what the dispatcher opens a shop for" % cap,
              all((got[r["name"]] == "shop")
                  == (shops.shop_type_for(args, r["name"]) is not None)
                  for r in roster))


def test_townsfolk():
    say("\nthe townsfolk")
    tf = fegamedata.townsfolk()
    quest = set(events.QUEST_BY_GIVER)
    check("every quest giver in the dispatcher's table is 'quest'",
          all(fn(t["name"], t["script"]) == "quest" for t in tf
              if t["script"] in quest)
          and sum(1 for t in tf if t["script"] in quest) == len(quest),
          "%d givers" % len(quest))
    want = {"weapon": "weapon", "porter": "guard", "guard": "guard",
            "gate": "guard", "market": "guide", "villager": "townsfolk"}
    bad = [(t["name"], t["kind"], fn(t["name"], t["script"])) for t in tf
           if t["script"] not in quest
           and fn(t["name"], t["script"]) != want.get(t["kind"])]
    check("everyone else follows the table's kind column", not bad, repr(bad[:5]))
    check("the free-weapon men are 'weapon' (events gives them a weapon)",
          [t["script"] for t in tf if t["kind"] == "weapon"]
          and all(fn(t["name"], t["script"]) == "weapon"
                  for t in tf if t["kind"] == "weapon"))
    check("plain villagers stay ambient ('townsfolk')",
          sum(1 for t in tf if fn(t["name"], t["script"]) == "townsfolk") > 100)


def test_overrides():
    say("\na row's own event override, and rows nobody knows")
    check("event goto = a warp, whatever the row is called",
          devtool.npc_function("Bank_Keeper", 2314, "say:Hi;goto:15")
          == ("warp", "to 15"))
    check("event shop:N = a shop, event bank = a bank",
          fn("Somebody", 0, "shop:2") == "shop"
          and fn("Somebody", 0, "bank") == "bank")
    check("event say only = a guide (it says a set line and nothing else)",
          fn("Inn_Master", 3105, "say:Closed today") == "guide")
    check("a model-only row is 'unknown', not a guess",
          fn("model 233", 0) == "unknown" and fn("", None) == "unknown")
    check("a bad script id does not raise", fn("x", "abc") == "unknown")


def test_nations():
    say("\nwhose capital is it")
    args = _Args(force_table=territory.parse_forces(FORCES))
    seen = set()
    for outer, inner in sorted(zones.INNER_CAPITAL_PAIRS.items()):
        a = fegamedata.areas()[outer]
        n_out = devtool.capital_nation(args, outer)
        n_in = devtool.capital_nation(args, inner)
        r, g, b = femap.PALETTE[a["nation"]]
        check("%d/%d: dat.pak's nation %d, named by --forces, client colour"
              % (outer, inner, a["nation"]),
              n_out == n_in and n_out["id"] == a["nation"]
              and n_out["name"] == args.force_table[a["nation"]]["name"]
              and n_out["color"] == "#%02x%02x%02x" % (r, g, b), repr(n_out))
        seen.add(n_out["id"])
    check("five capitals, five different nations", seen == {1, 2, 3, 4, 5},
          repr(seen))
    check("a war field is not a capital: no badge",
          devtool.capital_nation(args, 5) is None
          and devtool.capital_nation(args, "x") is None)
    check("with no --forces the badge says the number, not a made-up name",
          devtool.capital_nation(_Args(), 21)["name"] == "Nation %d"
          % fegamedata.areas()[21]["nation"])


def test_state():
    say("\nthe page's state carries both")
    tmp = tempfile.mkdtemp(prefix="fe-roles-")
    args = _Args(town_file=os.path.join(tmp, "town.json"), door_radius=15.0,
                 gmcmd_file=None, spawn_file=os.path.join(tmp, "spawn.json"),
                 territory_file=os.path.join(tmp, "terr.json"), spawn_area="",
                 spawn_height="dat", mapcal_file=os.path.join(tmp, "cal.json"),
                 door_arrive_file=os.path.join(tmp, "arr.json"),
                 doors="dat", door_height="dest",
                 force_table=territory.parse_forces(FORCES))
    args.spawn_xyz = args.self_xyz = (0.0, 0.0, 0.0)
    feworld.territory_load(args)
    feworld.mapcal_load(args)
    feworld.doorarr_load(args)
    r = devtool.devtool_edit(args, {"op": "townsfolk", "area": 39})
    check("fixture: townsfolk placed in 39", r.get("ok") and r.get("placed"), repr(r))
    feworld._TLS.session = {}
    devtool.devtool_snapshot()
    st = devtool.devtool_state(args, 39)
    caps = st.get("capitals") or []
    check("every capital in the picker has its nation",
          len(caps) == 5 and all(c.get("nation") and c["nation"]["name"]
                                 for c in caps), repr(caps[:1]))
    hs = st.get("halves") or []
    check("both halves of 39 carry the same nation",
          len(hs) == 2 and hs[0]["nation"] == hs[1]["nation"]
          and hs[0]["nation"]["name"] == "Cathedira", repr([h.get("nation") for h in hs]))
    h39 = next(h for h in hs if h["area"] == 39)
    check("every placed NPC has a function from the legend",
          h39["npcs"] and all(n.get("fn") in dict(devtool.NPC_FUNCTIONS)
                              for n in h39["npcs"]))
    check("...and the castle guard is 'guard', a quest giver 'quest' when present",
          all(n["fn"] == fn(n["name"], n["script"]) for n in h39["npcs"]))
    check("missing roles and townsfolk carry a function too",
          all(m.get("fn") for m in h39["missing"])
          and all(t.get("fn") for t in h39["townsfolk"]))
    check("the legend is served in order",
          [f["key"] for f in st.get("functions", [])]
          == [k for k, _ in devtool.NPC_FUNCTIONS])


def _fresh_args():
    tmp = tempfile.mkdtemp(prefix="fe-roles-")
    args = _Args(town_file=os.path.join(tmp, "town.json"), door_radius=15.0,
                 mapcal_file=os.path.join(tmp, "cal.json"),
                 force_table=territory.parse_forces(FORCES))
    feworld.mapcal_load(args)
    with devtool._UNDO_LOCK:
        devtool._UNDO.clear()
    return args


def test_shop_spots():
    say("\nmissing shopkeepers' painted spots (the ghosts)")
    args = _fresh_args()
    area = 92
    p = feworld.mapcal_proj(fegamedata.minimap_name(area))
    icons = {i["role"]: i for i in fegamedata.shop_icons(area) if i["role"]}
    spots = devtool.shop_spots(args, area)
    check("one spot per roster role with a trusted icon in the half",
          sorted(s["name"] for s in spots) == sorted(icons), repr([s["name"] for s in spots]))
    far = []
    for sp in spots:
        i = icons[sp["name"]]
        ix, iz = (i["px"] - p["cx"]) * p["ax"], (i["py"] - p["cz"]) * p["az"]
        d = ((sp["x"] - ix) ** 2 + (sp["z"] - iz) ** 2) ** 0.5
        if d > devtool.SPOT_REACH + 0.2 or sp["px"] != i["px"]:
            far.append((sp["name"], round(d, 1)))
    check("each spot is its icon's pixel through this half's alignment, "
          "moved at most SPOT_REACH to floor", not far, repr(far))
    check("...and has floor under it, the y the collision gives",
          all(sp["y"] is None or abs(fegamedata.capital_ground(area, sp["x"], sp["z"])
                                     - sp["y"]) < 0.01 for sp in spots)
          and any(sp["y"] is not None for sp in spots))
    st = devtool.devtool_state(args, area)
    h = next(x for x in st["halves"] if x["area"] == area)
    check("the half's ghosts are exactly its unplaced spots",
          sorted(g["name"] for g in h["ghosts"]) == sorted(icons))
    nofloor = [sp["name"] for a in (21, 93, 78) for sp in devtool.shop_spots(args, a)
               if sp["y"] is None]
    check("a spot with no floor near its icon says so (y None) rather than "
          "inventing a height", nofloor, repr(nofloor))
    check("not a capital: no spots", devtool.shop_spots(args, 5) == [])


def test_bulk_and_undo():
    say("\nplace all shopkeepers on their icons, and undo")
    args = _fresh_args()
    area = 92
    spots = devtool.shop_spots(args, area)
    first = spots[0]
    devtool.devtool_edit(args, {"op": "place", "area": area, "role": first["name"],
                                "x": first["x"], "z": first["z"]})
    res = devtool.devtool_edit(args, {"op": "shops", "area": area})
    rows = feworld.town_get(args, area, "npcs")
    names = [r["name"] for r in rows]
    check("bulk places every unplaced one, skipping the role already there",
          res["ok"] and sorted(res["placed"]) == sorted(s["name"] for s in spots[1:])
          and names.count(first["name"]) == 1 and len(rows) == len(spots),
          "%r / %r" % (res, names))
    check("...each on its spot, on the floor, as the roster's model and script",
          all(any(r["name"] == s["name"] and r["x"] == s["x"] and r["z"] == s["z"]
                  and r["y"] == s["y"] and r["script"] == s["script"]
                  and r["model"] == s["modeltype"] for r in rows) for s in spots))
    again = devtool.devtool_edit(args, {"op": "shops", "area": area})
    check("running it again places nobody (idempotent)",
          again["placed"] == [] and len(feworld.town_get(args, area, "npcs")) == len(rows))
    check("...and an edit that changed nothing is not an undo step",
          [u["label"] for u in devtool.undo_list()][:1] == ["placed %d shopkeepers in %d"
                                                           % (len(spots) - 1, area)],
          repr(devtool.undo_list()))
    u = devtool.devtool_edit(args, {"op": "undo"})
    check("undo takes the whole bulk placement back",
          u["ok"] and [r["name"] for r in feworld.town_get(args, area, "npcs")]
          == [first["name"]], repr(u))
    u = devtool.devtool_edit(args, {"op": "undo"})
    check("...and the next undo the single place before it",
          u["ok"] and feworld.town_get(args, area, "npcs") == [], repr(u))
    check("with nothing left, undo says so", not devtool.devtool_edit(
        args, {"op": "undo"})["ok"])
    # every kind of map edit, then undone newest first, back to the start
    sp = spots[1]
    steps = [{"op": "place", "area": area, "role": sp["name"], "x": sp["x"], "z": sp["z"]},
             {"op": "turn", "area": area, "idx": 0, "yaw": 90},
             {"op": "retype", "area": area, "idx": 0, "role": "Bank_Keeper"},
             {"op": "move", "area": area, "idx": 0, "x": spots[2]["x"], "z": spots[2]["z"]},
             {"op": "place", "area": area, "role": spots[3]["name"],
              "x": spots[3]["x"], "z": spots[3]["z"]},
             {"op": "remove", "area": area, "idx": 0}]
    states = [feworld.town_get(args, area, "npcs")]
    for op in steps:
        r = devtool.devtool_edit(args, op)
        if not r.get("ok"):
            check("fixture edit %s" % op["op"], False, repr(r))
        states.append(feworld.town_get(args, area, "npcs"))
    labels = [u["label"] for u in devtool.undo_list()]
    check("place, turn, change type, move, remove are each one undo step, "
          "newest first", len(labels) == 6 and labels[0].startswith("removed Bank Keeper")
          and labels[3].startswith("changed ") and labels[4].startswith("turned "),
          repr(labels))
    ok = True
    for k in range(len(steps), 0, -1):
        r = devtool.devtool_edit(args, {"op": "undo"})
        ok = ok and r["ok"] and feworld.town_get(args, area, "npcs") == states[k - 1]
    check("undoing all six walks the half back through every earlier state", ok)
    # a change made OUTSIDE the editor since: undo refuses, changes nothing
    devtool.devtool_edit(args, {"op": "place", "area": area, "role": sp["name"],
                                "x": sp["x"], "z": sp["z"]})
    feworld.town_add(args, area, "npcs", {"model": 1, "name": "x", "x": 0, "z": 0})
    before = feworld.town_get(args, area, "npcs")
    r = devtool.devtool_edit(args, {"op": "undo"})
    check("undo refuses when the half changed since (a `!npc` in game), and "
          "leaves it alone", not r["ok"] and "changed since" in r["msg"]
          and feworld.town_get(args, area, "npcs") == before, repr(r))
    for _ in range(devtool.UNDO_KEEP + 5):
        devtool.devtool_edit(args, {"op": "turn", "area": area, "idx": 0, "yaw": _ % 360})
    check("the server keeps the last %d, the page shows 10" % devtool.UNDO_KEEP,
          len(devtool._UNDO) == devtool.UNDO_KEEP and len(devtool.undo_list()) == 10)
    check("a refused edit is not an undo step", (lambda n: (devtool.devtool_edit(
        args, {"op": "place", "area": area, "role": "Nobody", "x": 0, "z": 0}),
        len(devtool._UNDO) == n)[1])(len(devtool._UNDO)))


def test_silent():
    say("\ntownsfolk with no recorded lines")
    args = _fresh_args()
    caps = devtool.devtool_capitals(None, args)
    for c in caps:
        tf = fegamedata.townsfolk(c["area"], capital=True)
        want = sum(1 for t in tf if not (t["line_en"] or "").strip())
        check("%s: %d of %d have no recorded line" % (c["name"], want, len(tf)),
              c["townsfolk"] == len(tf) and c["silent"] == want and len(tf) > 0)
    h = devtool._devtool_halves(args, 62, None)[0]
    check("each townsperson in a half says whether it has a line",
          all(t["silent"] == (not (fegamedata.townsfolk_by_script(t["script"])["line_en"]
                                   or "").strip()) for t in h["townsfolk"]))


def main():
    test_roster()
    test_townsfolk()
    test_overrides()
    test_nations()
    test_state()
    test_shop_spots()
    test_bulk_and_undo()
    test_silent()
    bad = [c for c in CHECKS if not c[1]]
    say("\n%d checks, %d failed" % (len(CHECKS), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
