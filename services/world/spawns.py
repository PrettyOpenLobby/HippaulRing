"""Where a player arrives: data/fe_spawn.json and the derived spawn points."""
import json
import os
import sys
import threading
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import buildings, deps, sess, territory, zones

# ---------------------------------------------------------------------------
# WHERE A PLAYER ARRIVES -- data/fe_spawn.json, area id -> (x, y, z, heading).
#
# WARNING: WHY THIS EXISTS. --spawn-pos is ONE position for the whole world and it
# defaults to `0:0:0`, which prod has never overridden. Every field entry, the
# self record's own position and the fallback arrival of every warp therefore
# put the player at the origin of whatever map just loaded -- world (0, 0, 0)
# is the CENTRE of the terrain grid at height zero, which on most maps is
# under it. Meanwhile --self-pos carries a real measured ground position
# (2.16, 24.76, -60.51) into a DIFFERENT record, so the server has been
# telling the client two different stories about where the player is.
#
# One position cannot be right for 95 areas anyway: each one is its own
# terrain. So this is a per-area store, filled the way the town file is --
# by standing somewhere and saying `!spawn`. The client reports its own
# position continuously (i16 x0.1 at _MV_POS, kept in _SESSION["cpos"]), so
# "where the player is standing" is measured ground, not a guess.
#
#   {"39": {"x": 186.5, "y": 21.0, "z": -132.5, "h": 0.0}}
# ---------------------------------------------------------------------------
_SPAWN = {}
_SPAWN_LOCK = threading.Lock()


def spawn_path(args):
    p = getattr(args, "spawn_file", None)
    if p is None:
        d = os.path.normpath(os.path.join(deps._HERE, os.pardir, "data"))
        p = os.path.join(d if os.path.isdir(d) else deps._HERE, "fe_spawn.json")
    # first start: seed the live store from the shipped positions, so a
    # fresh deployment does not drop players at the terrain origin
    seed = os.path.join(deps._HERE, "fedata", "fe_spawn.json")
    if not os.path.exists(p) and os.path.exists(seed):
        try:
            import shutil
            shutil.copyfile(seed, p)
            print("[feworld] spawn store seeded from fedata/fe_spawn.json")
        except OSError as e:
            print("[feworld] could not seed the spawn store: %s" % e)
    return p


def _spawn_rows(v):
    """One stored value -> a list of rows. Accepts the ORIGINAL single-dict
    shape as well as the list, so a store written before battlefields needed
    more than one point still loads."""
    if isinstance(v, dict):
        v = [v]
    out = []
    for r in v or []:
        try:
            out.append({"x": float(r["x"]), "y": float(r["y"]),
                        "z": float(r["z"]), "h": float(r.get("h", 0) or 0),
                        "tag": str(r.get("tag", "") or "")})
        except (TypeError, ValueError, KeyError):
            continue
    return out


def spawn_load(args):
    """The store, then --spawn-area on top of it."""
    global _SPAWN
    out = {}
    try:
        with open(spawn_path(args), encoding="utf-8") as fh:
            for k, v in (json.load(fh) or {}).items():
                try:
                    rows = _spawn_rows(v)
                except (TypeError, ValueError):
                    continue
                if rows:
                    out[int(k)] = rows
    except (OSError, ValueError):
        pass
    for part in [x for x in (getattr(args, "spawn_area", "") or "").split(",") if x.strip()]:
        f = part.split(":")
        if len(f) not in (4, 5, 6):
            raise SystemExit("--spawn-area wants GID:X:Y:Z[:HEADING[:TAG]], "
                             "got %r" % part)
        try:
            row = {"x": float(f[1]), "y": float(f[2]), "z": float(f[3]),
                   "h": float(f[4]) if len(f) > 4 and f[4] else 0.0,
                   "tag": f[5] if len(f) > 5 else ""}
            out.setdefault(int(f[0], 0), []).append(row)
        except ValueError:
            raise SystemExit("--spawn-area wants GID:X:Y:Z[:HEADING[:TAG]], "
                             "got %r" % part)
    with _SPAWN_LOCK:
        _SPAWN = out
    return out


def spawn_save(args):
    p = spawn_path(args)
    # os.devnull means "do not persist". Writing a temp file beside it and
    # renaming onto `nul` fails on Windows and strands nul.tmp.<pid> in the
    # working directory -- 33 of them had piled up from test runs.
    if not p or p == os.devnull:
        return
    with _SPAWN_LOCK:
        snap = {str(k): list(v) for k, v in _SPAWN.items()}
    tmp = "%s.tmp.%d" % (p, os.getpid())
    try:
        os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(snap, indent=1, sort_keys=True))
        os.replace(tmp, p)
    except OSError as e:
        print("[feworld] spawn save failed: %s" % e, flush=True)


def spawn_rows_for(area):
    with _SPAWN_LOCK:
        return list(_SPAWN.get(int(area), [])) if area is not None else []


def spawn_side(args, area):
    """'atk'/'def' for the player in war field `area`, or None (a capital, an
    unknown area, no nation). The campaign's own side when a war is on there.

    KEY: AT PEACE, THE CASTLE SIDE FOR EVERYONE (2026-09-11): "非戦争時のエリア
    入口" -- a field's entrance outside a war is at the castle, whoever holds
    it [FEZ-early, atwiki 251 / FFSKY; SE's own site is SILENT on this, so it
    is the early-FEZ reading]. Until now a peaceful field held by another
    nation put the player at the reflected KEEP point. --peace-arrival holder
    restores that (defender when their nation holds the field, attacker when
    it does not). With no campaign running nothing tracks a war, so every
    field reads as peace here."""
    try:
        area = int(area)
    except (TypeError, ValueError):
        return None
    if area in zones.CAPITAL_GROUP_IDS or area not in fegamedata.areas():
        return None
    mine = territory.nation_of(args)
    if not mine:
        return None
    at_peace = True
    mod = sys.modules.get("fecampaign")
    if mod is not None and getattr(args, "campaign", "off") == "on":
        try:
            r = mod.state_of(area)
            at_peace = r["phase"] == mod.PEACE
            if not at_peace and int(r["atk"]) == mine:
                return "atk"
            if not at_peace and territory.territory_owner(area, args) == mine:
                return "def"
        except Exception:                              # noqa: BLE001
            pass
    if at_peace and getattr(args, "peace_arrival", "castle") == "castle":
        return "def"
    return "def" if territory.territory_owner(area, args) == mine else "atk"


def derived_spawn(args, area, side):
    """Where a war field puts `side` when nobody has stood there and said
    `!spawn` (2026-09-11): beside that side's keep -- the castle for the
    defender, the keep for the attacker (keep_grids, off dat.pak's
    fet_castle_info) -- five cells further toward the map's centre, so the
    defender stands between the castle and the field and the attacker
    behind the keep. y is the map's ground height (the median of its shipped
    sound emitters, the same estimate the height fallback uses). No player
    spawn table ships; this is derived from what does. A stored row wins,
    a capital is never derived, --spawn-derive off restores the old fallback."""
    if getattr(args, "spawn_derive", "castle") != "castle":
        return None
    if getattr(args, "spawn_height", "dat") != "dat":
        return None
    try:
        area = int(area)
    except (TypeError, ValueError):
        return None
    if area in zones.CAPITAL_GROUP_IDS:
        return None
    grids = buildings.keep_grids(area)
    if not grids:
        return None
    h = fegamedata.area_ground_height(area)
    if h is None:
        # maps 7 and 10 ship ZERO height samples (fe-map-heights.tsv); the
        # starting land is one of them. Take the median of the other maps'
        # medians (44) rather than serve (0,0,0) -- LIVE 09-11 02:1xZ the
        # player arrived at the origin, 240 u from the keeps, and saw none.
        import statistics
        meds = [v["median"] for v in fegamedata.map_heights().values()
                if v.get("median") is not None]
        if not meds:
            return None
        h = float(statistics.median(meds))
    gx, gz = grids["atk" if side == "atk" else "def"]
    step = -5 if gx > buildings.GRID_ORIGIN else 5              # toward the centre
    gx2 = max(0, min(255, gx + step))
    return (buildings.grid_to_world(gx2), float(h), buildings.grid_to_world(gz))


def spawn_for(args, area=None, tag=None, door=True):
    """(x, y, z) for `area` -- best matching row, else a per-map height, else
    --spawn-pos.

    KEY: A BATTLEFIELD WANTS MORE THAN ONE. A war has two sides and they do not
    arrive in the same place, so a row carries a TAG and the caller says which
    it wants ("atk"/"def", or a nation id as a string). The order is: a row
    tagged as asked, else an untagged row, else the first row of any tag --
    so an area with one plain row behaves exactly as it did before tags
    existed, and an area with sides never hands a defender the attackers' spot
    silently.

    The last resort before --spawn-pos is the area's own map height
    (fegamedata.area_ground_height, the median of that Hmap's shipped sound
    emitters). It is not standable ground, but every one of those maps has its
    terrain somewhere between y=8 and y=65, and --spawn-pos unset is y=0 --
    which is under all of them.
    """
    if area is None:
        area = sess._SESSION.get("field")
    # A door that named where to come out beats the area's spawn point, for
    # this one entry. See goto_push for why it has to travel this way.
    # (door=False: the caller wants the area's own point -- a war death's
    # return to base, which is not the door the player came in by.)
    pend = sess._SESSION.get("arrive_at") if door else None
    if pend and area is not None and pend[0] == int(area):
        return pend[1]
    rows = spawn_rows_for(area)
    if rows:
        pick = None
        if tag is not None:
            pick = next((r for r in rows if r["tag"] == str(tag)), None)
        if pick is None:
            pick = next((r for r in rows if not r["tag"]), None)
        if pick is None:
            pick = rows[0]
        return (pick["x"], pick["y"], pick["z"])
    if area is not None:
        side = tag if tag in ("atk", "def") else spawn_side(args, area)
        d = derived_spawn(args, area, side)
        if d is not None:
            return d
    if area is not None and getattr(args, "spawn_height", "dat") == "dat":
        try:
            h = fegamedata.area_ground_height(int(area))
        except (TypeError, ValueError):
            h = None
        if h is not None:
            x, _y, z = args.spawn_xyz
            return (x, float(h), z)
    return args.spawn_xyz


def spawn_add(args, area, pos, tag="", heading=0.0):
    """Record a point. A repeated TAG REPLACES its row rather than stacking a
    second one -- standing somewhere better and saying it again is a
    correction, not another spawn."""
    area, tag = int(area), str(tag or "")
    row = {"x": float(pos[0]), "y": float(pos[1]), "z": float(pos[2]),
           "h": float(heading), "tag": tag}
    with _SPAWN_LOCK:
        rows = _SPAWN.setdefault(area, [])
        was = next((r for r in rows if r["tag"] == tag), None)
        if was is not None:
            rows[rows.index(was)] = row
        else:
            rows.append(row)
    spawn_save(args)
    return was


def spawn_drop(args, area, tag=None):
    """Drop one tag's row, or every row for the area when tag is None."""
    area = int(area)
    with _SPAWN_LOCK:
        rows = _SPAWN.get(area, [])
        if tag is None:
            gone = len(rows)
            _SPAWN.pop(area, None)
        else:
            keep = [r for r in rows if r["tag"] != str(tag)]
            gone = len(rows) - len(keep)
            if keep:
                _SPAWN[area] = keep
            else:
                _SPAWN.pop(area, None)
    spawn_save(args)
    return gone
