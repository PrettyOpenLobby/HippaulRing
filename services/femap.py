#!/usr/bin/env python3
"""femap.py -- Fantasy Earth's LIVE DOMINION MAP (2026-09-12).

A web page and a Discord post that look like the client's own continent map
-- SE's art, SE's marker sprites, the client's own palette -- drawn from the
live board: who holds each of the 95 areas, which fields are at war and how
the two sides stand, and how many players stand in each.

DESIGN CHOICES (2026-09-12):
  * Discord through a WEBHOOK -- one map message edited in place, plus a
    short post when a field changes hands or a war starts. No bot account.
  * The website as plain HTTP; put a Cloudflare tunnel (or any reverse
    proxy) in front of it. Read-only and tokenless: this port runs no
    commands.
  * Player NAMES optional (--map-names / FE_MAP_NAMES). Off by default: names
    on a public page hand out live enemy positions during a war.

WHERE THE DRAWING COMES FROM -- all measured (static analysis, 2026-09-12):
  * art: fedata/mapart, pulled out of mapselect.tex by tools/fe_mapart_build.py
    -- world.png (MAP_BG0, the client's 800x600 layout), continent1..6
    (MAP1..6, 512x512) and mark.png (the circle / shield sheet).
  * a field's position is fet_area's map_x/map_y, in the continent image's own
    pixel space; the "choose a field" screen puts that image at (31,29) of its
    800x600 layout (calibrated off live client screenshots).
  * colour is the client's palette 0x5336fe8 indexed by nation id -- 1 red,
    2 green, 3 blue, 4 yellow, 5 cyan, confirmed on a live client screen.
  * the SHIELD marker is a capital (group flags bit 3), the circle a field.

THREADS. The page server and the watcher are daemon threads started from
feworld's main() through register_start. They only READ shared state: the
territory board (territory_owner, locked), fecampaign's rows (state_of,
locked) and the session table (ext_sessions, a locked copy). Nothing here
writes to a session or touches a client socket.
"""
import collections
import hashlib
import io
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid

try:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
except ImportError:                                   # pragma: no cover
    ThreadingHTTPServer = None

fw = None       # the feworld module, handed in by register()

ART_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "fedata", "mapart")
ART_FILES = frozenset(["world.png", "mark.png", "mark2.png"]
                      + ["continent%d.png" % n for n in range(1, 7)])

#: The client's nation palette (0x5336fe8, variant 0), by nation id. Index 0
#: is "nobody" and draws black -- what every nation's own circle showed until
#: the server started sending force+0x1bc.
PALETTE = {0: (0, 0, 0), 1: (255, 0, 0), 2: (0, 255, 0), 3: (0, 0, 255),
           4: (255, 255, 0), 5: (0, 255, 255)}

#: Where the "choose a field" screen draws a continent image in its 800x600
#: layout. Calibrated from two live client screenshots: Land of Beginnings
#: (268,241) and Noim Grasslands (54,201) both land within a pixel.
CONTINENT_ORIGIN = (31, 29)

#: island -> (scale, x, y) of that continent's 512x512 image inside world.png.
#: FITTED, not read from the client: each MAPn's alpha matched against the
#: land on MAP_BG0 (refit by hand, 2026-09-12). Good to a few pixels, which
#: is all the world overview's dots need.
WORLD_PLACE = {1: (0.280, 347, 244), 2: (0.270, 174, 172),
               3: (0.310, 204, 371), 4: (0.285, 307, 54),
               5: (0.270, 508, 153), 6: (0.310, 464, 362)}

#: Island 1 is the shared frontier; 2..6 are one home continent per nation
#: (fegamedata.areas docstring). The client names none of them on this screen.
ISLAND_NAMES = {1: "Central Continent", 2: "Netzawar Homeland",
                3: "Cathedira Homeland", 4: "Elsord Homeland",
                5: "Holdein Homeland", 6: "Gebrand Homeland"}

#: A capital's second half shares its marker (same map_x/map_y); the map
#: draws the outer half and counts both halves' players there.
CAPITAL_TWINS = {91: 21, 92: 39, 93: 57, 94: 62, 95: 78}

PHASE_NAME = {0: "Peace", 1: "War Prep", 2: "At war", 3: "Truce",
              4: "Server down"}

#: mark.png is a 2x2 sheet of 32 px cells.
MARK_CELLS = {"circle": (0, 0, 32, 32), "dot": (32, 0, 64, 32),
              "shield": (0, 32, 32, 64), "shield_s": (32, 32, 64, 64)}

_EVENTS = collections.deque(maxlen=40)
_EV_LOCK = threading.Lock()
_SEEN = {"owners": None, "phases": None}
_SNAP = {"t": 0.0, "snap": None}
_SNAP_LOCK = threading.Lock()
_RENDER = {}
_RENDER_LOCK = threading.Lock()
_ART_CACHE = {}
_WARNED = set()


# ---------------------------------------------------------------------------
# registration
# ---------------------------------------------------------------------------
def register(feworld):
    global fw
    fw = feworld
    fw.register_args(add_args)
    if hasattr(fw, "register_start"):
        fw.register_start(start)


def add_args(ap):
    ap.add_argument("--map-port", type=int, default=0, metavar="PORT",
                    help="serve the LIVE DOMINION MAP (a read-only web page "
                         "drawn like the client's continent map) on this port. "
                         "0 = off. It runs no commands, so it needs no token; "
                         "put a tunnel or proxy in front of it to publish it")
    ap.add_argument("--map-bind", default="127.0.0.1",
                    help="address the map page binds (loopback is right when "
                         "the tunnel runs on this box)")
    ap.add_argument("--map-names", default="off", choices=("on", "off"),
                    help="list CHARACTER NAMES per field on the page and in "
                         "Discord. Off by default: a public page with names "
                         "hands out live enemy positions during a war")
    ap.add_argument("--map-poll", type=float, default=3.0, metavar="S",
                    help="how often the page asks for fresh state")
    ap.add_argument("--map-title", default="Fantasy Earth - Dominion",
                    help="the page and Discord title")
    ap.add_argument("--map-url", default="",
                    help="the page's PUBLIC address, linked from Discord "
                         "(e.g. the Cloudflare tunnel's hostname)")
    ap.add_argument("--map-discord-webhook", default="",
                    help="a Discord WEBHOOK URL: the map is posted there once "
                         "and then that one message is edited in place. A "
                         "SECRET -- pass it from .env, never the repo")
    ap.add_argument("--map-discord-every", type=float, default=60.0,
                    metavar="S", help="minimum seconds between map edits")
    ap.add_argument("--map-discord-view", default="world",
                    choices=("world", "1", "2", "3", "4", "5", "6"),
                    help="which map the Discord image shows: the whole world "
                         "or one continent (1 = the Central Continent)")
    ap.add_argument("--map-discord-events", default="on", choices=("on", "off"),
                    help="post a short message when a field changes hands or "
                         "a war begins")
    ap.add_argument("--map-discord-event-ttl", type=float, default=600.0,
                    metavar="S",
                    help="DELETE a capture/war post this many seconds after it "
                         "goes up, so the map stays the channel's last message "
                         "(0 = keep them). A restart also deletes whatever the "
                         "last run left. A webhook can only delete ITS OWN "
                         "messages, by id -- it cannot list or clear a channel")
    ap.add_argument("--map-discord-state", default=None, metavar="PATH",
                    help="where the id of the map message is kept, so a "
                         "restart edits the same message instead of posting "
                         "a new one (default data/fe_map_discord.json, beside "
                         "the territory store)")


# ---------------------------------------------------------------------------
# the snapshot -- everything the page and the Discord post draw
# ---------------------------------------------------------------------------
def _hex(n):
    r, g, b = PALETTE.get(int(n or 0), (0, 0, 0))
    return "#%02x%02x%02x" % (r, g, b)


def _campaign(args):
    if getattr(args, "campaign", "off") != "on":
        return None
    return sys.modules.get("fecampaign")


def _nation_name(args, n):
    rec = (getattr(args, "force_table", None) or {}).get(int(n or 0)) or {}
    return rec.get("name") or ("Nation %d" % int(n or 0) if n else "nobody")


def snapshot(args, now=None):
    """One JSON-able dict of the whole board. Cheap enough to build every few
    seconds: 95 areas, a locked copy of the session table, fecampaign's rows."""
    now = time.time() if now is None else float(now)
    gd = fw.fegamedata
    areas = gd.areas()
    try:
        names_en = gd.area_names_en()
    except Exception:                                  # noqa: BLE001
        names_en = {}
    forces = getattr(args, "force_table", None) or {}
    show_names = getattr(args, "map_names", "off") == "on"

    players, by_nat, who, online = {}, {}, {}, {}
    in_world = 0
    try:
        sessions = fw.ext_sessions()
    except Exception:                                  # noqa: BLE001
        sessions = []
    for ent in sessions:
        ps = ent.get("session") or {}
        in_world += 1
        # KEY: a peer's nation is its presence card's `force` -- there is no
        # ps["nation"] (the arrow-tower bug, 2026-09-12)
        try:
            nat = int((ps.get("pres_card") or {}).get("force") or 0)
        except (TypeError, ValueError):
            nat = 0
        online[nat] = online.get(nat, 0) + 1
        if not ps.get("in_field") or ps.get("field") is None:
            continue
        try:
            a = int(ps["field"])
        except (TypeError, ValueError):
            continue
        a = CAPITAL_TWINS.get(a, a)
        players[a] = players.get(a, 0) + 1
        by_nat.setdefault(a, {})
        by_nat[a][nat] = by_nat[a].get(nat, 0) + 1
        if show_names:
            who.setdefault(a, []).append({"name": str(ent.get("name") or "?"),
                                          "nation": nat})

    owners, held = {}, {}
    for a in areas:
        own = int(fw.territory_owner(a, args) or 0)
        owners[a] = own
        held[own] = held.get(own, 0) + 1

    camp = _campaign(args)
    fields = []
    for a in sorted(areas):
        if a in CAPITAL_TWINS:
            continue
        r = areas[a]
        war = None
        if camp is not None:
            try:
                st = camp.state_of(a)
            except Exception:                          # noqa: BLE001
                st = None
            if st and int(st.get("phase") or 0) != 0:
                until = float(st.get("until") or 0)
                su = st.get("signups") or {}
                keeps = {}
                for side, v in (st.get("keeps") or {}).items():
                    if isinstance(v, (list, tuple)) and len(v) >= 2:
                        keeps[str(side)] = [int(v[0]), int(v[1])]
                war = {"phase": int(st["phase"]),
                       "phase_name": PHASE_NAME.get(int(st["phase"]), "?"),
                       "attacker": int(st.get("atk") or 0),
                       "left_s": max(0, int(until - now)) if until else None,
                       "def": int(su.get("def", 0) or 0),
                       "atk": int(su.get("atk", 0) or 0),
                       "keeps": keeps}
        nb = []
        for n in r.get("neighbours") or []:
            n = CAPITAL_TWINS.get(int(n), int(n))
            if n != a and n in areas and n not in nb:
                nb.append(n)
        f = {"id": a,
             "name": names_en.get(a) or r.get("name") or ("Field %d" % a),
             "island": int(r.get("island") or 0),
             "x": int(r.get("map_x") or 0), "y": int(r.get("map_y") or 0),
             "capital": bool(r.get("capital")),
             "owner": owners[a],
             "neighbours": nb,
             "players": players.get(a, 0),
             "by_nation": {str(k): v for k, v in sorted(by_nat.get(a, {}).items())},
             "war": war}
        if show_names:
            f["names"] = sorted(who.get(a, []), key=lambda d: d["name"].lower())
        fields.append(f)

    pop = {}
    if camp is not None and hasattr(camp, "_population"):
        try:
            pop = camp._population(args) or {}
        except Exception:                              # noqa: BLE001
            pop = {}
    nations = [{"id": fid, "name": _nation_name(args, fid), "color": _hex(fid),
                "fields": held.get(fid, 0), "online": online.get(fid, 0),
                "population": int(pop.get(fid, 0) or 0)}
               for fid in sorted(forces)]
    with _EV_LOCK:
        events = list(_EVENTS)[-15:][::-1]
    return {"title": getattr(args, "map_title", "") or "Fantasy Earth",
            "updated": int(now), "poll_s": float(getattr(args, "map_poll", 3.0) or 3.0),
            "names": show_names, "in_world": in_world,
            "campaign": camp is not None,
            "nations": nations, "fields": fields, "events": events,
            "islands": {str(i): {"name": ISLAND_NAMES.get(i, "Island %d" % i),
                                 "world": list(WORLD_PLACE[i])}
                        for i in sorted(WORLD_PLACE)},
            "continent_origin": list(CONTINENT_ORIGIN),
            "palette": {str(k): _hex(k) for k in PALETTE}}


def cached_snapshot(args, ttl=1.0):
    """The page polls every few seconds from every viewer; build at most once
    a second no matter how many are watching."""
    now = time.time()
    with _SNAP_LOCK:
        if _SNAP["snap"] is not None and now - _SNAP["t"] < ttl:
            return _SNAP["snap"]
    snap = snapshot(args, now)
    with _SNAP_LOCK:
        _SNAP.update(t=now, snap=snap)
    return snap


# ---------------------------------------------------------------------------
# events -- captures and war starts, found by diffing successive snapshots
# ---------------------------------------------------------------------------
def observe(snap):
    """Compare `snap` with the last one seen and append what changed. The
    first call only seeds (a restart is not twenty captures). Returns the new
    events."""
    owners = {f["id"]: f["owner"] for f in snap["fields"]}
    phases = {f["id"]: (f["war"] or {}).get("phase", 0) for f in snap["fields"]}
    atk = {f["id"]: (f["war"] or {}).get("attacker", 0) for f in snap["fields"]}
    names = {f["id"]: f["name"] for f in snap["fields"]}
    new = []
    if _SEEN["owners"] is not None:
        for a, own in owners.items():
            was = _SEEN["owners"].get(a)
            if was is not None and was != own:
                new.append({"t": snap["updated"], "kind": "capture", "area": a,
                            "name": names[a], "from": was, "to": own})
        for a, ph in phases.items():
            was = _SEEN["phases"].get(a, 0)
            if was == 0 and ph in (1, 2):
                new.append({"t": snap["updated"], "kind": "war", "area": a,
                            "name": names[a], "attacker": atk[a],
                            "defender": owners[a]})
    _SEEN["owners"], _SEEN["phases"] = owners, phases
    if new:
        with _EV_LOCK:
            _EVENTS.extend(new)
    return new


def event_text(args, ev):
    nm = lambda n: _nation_name(args, n)                # noqa: E731
    if ev["kind"] == "capture":
        return "\U0001F3F3 **%s** was taken by **%s** from %s." % (
            ev["name"], nm(ev["to"]), nm(ev["from"]))
    if ev["kind"] == "war":
        return "⚔ **%s** declared war on **%s** at **%s**." % (
            nm(ev["attacker"]), nm(ev["defender"]), ev["name"])
    return ev.get("kind", "?")


# ---------------------------------------------------------------------------
# the PNG -- the same drawing as the page, for Discord (needs Pillow)
# ---------------------------------------------------------------------------
def _pil():
    try:
        from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFont
    except ImportError:
        if "pil" not in _WARNED:
            _WARNED.add("pil")
            print("[femap] Pillow is not installed -- the Discord post goes "
                  "out as text only (the web page is unaffected)", flush=True)
        return None
    return Image, ImageChops, ImageDraw, ImageEnhance, ImageFont


def _art(name):
    img = _ART_CACHE.get(name)
    if img is None:
        Image = _pil()[0]
        img = Image.open(os.path.join(ART_DIR, name)).convert("RGBA")
        _ART_CACHE[name] = img
    return img


_BITMAP = [False]
_LATIN = str.maketrans({"\u2014": "-", "\u2013": "-", "\u2019": "'",  # transliteration table; polcheck: allow
                        "\u2018": "'", "\u201c": '"', "\u201d": '"',
                        "\u2026": "..."})


def _font(size):
    """Pillow's own default font at `size` -- a scalable one since Pillow
    10.1 (what the image installs). An older Pillow only has the tiny
    Latin-1 bitmap font; _draw() then keeps text inside Latin-1."""
    ImageFont = _pil()[4]
    try:
        return ImageFont.load_default(size=size)
    except TypeError:                                  # Pillow < 10.1
        _BITMAP[0] = True
        return ImageFont.load_default()


def _latin(s):
    return str(s).translate(_LATIN).encode("latin-1", "replace").decode("latin-1")


def _draw(img):
    """ImageDraw.Draw with text Pillow's default fonts can draw. The em dash
    in the title crashed the bitmap font (UnicodeEncodeError, Pillow 10.0)
    and drew as a tofu box in the scalable one (Pillow 12.3) -- neither font
    has it -- so typographic punctuation is always swapped for plain, and the
    bitmap font additionally gets Latin-1 only."""
    d = _pil()[2].Draw(img)
    _font(12)
    fix = _latin if _BITMAP[0] else (lambda t: str(t).translate(_LATIN))
    raw_text, raw_bbox = d.text, d.textbbox
    d.text = lambda xy, t, *a, **k: raw_text(xy, fix(t), *a, **k)
    d.textbbox = lambda xy, t, *a, **k: raw_bbox(xy, fix(t), *a, **k)
    return d


def _sprite(cell, rgb, size):
    key = ("s", cell, rgb, size)
    s = _ART_CACHE.get(key)
    if s is None:
        Image, ImageChops = _pil()[0], _pil()[1]
        src = _art("mark.png").crop(MARK_CELLS[cell])
        tint = ImageChops.multiply(src.convert("RGB"), Image.new("RGB", src.size, rgb))
        tint.putalpha(src.getchannel("A"))
        s = tint.resize((size, size), Image.LANCZOS)
        _ART_CACHE[key] = s
    return s


def _marker(img, x, y, f, size):
    rgb = PALETTE.get(f["owner"], (0, 0, 0))
    if f["capital"]:
        s = _sprite("shield", rgb, size)
        img.alpha_composite(s, (int(round(x - size / 2)), int(round(y - size / 2))))
        return
    s = _sprite("circle", rgb, size)
    img.alpha_composite(s, (int(round(x - size / 2)), int(round(y - size / 2))))
    inner = max(4, int(size * 0.55))
    s2 = _sprite("circle", rgb, inner)
    img.alpha_composite(s2, (int(round(x - inner / 2)), int(round(y - inner / 2))))


#: How opaque the neighbour lines are, over the map (design choice, 2026-09-12:
#: "slightly transparent, so the areas stand out a bit more"). The lines are
#: drawn on their own layer at full strength and the LAYER is faded, so the
#: dark edge and the colour do not darken each other where they overlap.
LINK_ALPHA = 0.6


def _links(img, draw_fn):
    """Run draw_fn(ImageDraw) on a transparent layer, fade it, composite it."""
    Image = _pil()[0]
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    draw_fn(_draw(lay))
    lay.putalpha(lay.getchannel("A").point(lambda v: int(v * LINK_ALPHA)))
    img.alpha_composite(lay)


def render_png(snap, view="world"):
    """The map as PNG bytes, or None without Pillow."""
    pil = _pil()
    if pil is None:
        return None
    Image, _chops, ImageDraw, ImageEnhance, _font_mod = pil
    W, H, PANEL = 800, 600, 290
    img = Image.new("RGBA", (W + PANEL, H), (32, 22, 15, 255))
    world = _art("world.png")
    fields = snap["fields"]
    by_id = {f["id"]: f for f in fields}
    if view == "world":
        img.alpha_composite(world, (0, 0))
        d = _draw(img)

        def wpos(f):
            s, ox, oy = WORLD_PLACE[f["island"]]
            return ox + f["x"] * s, oy + f["y"] * s
        # the neighbour lines, as on a continent but thinner (design choice,
        # 2026-09-12: "have the connecting lines show on the world map too")
        def world_links(ld):
            for f in fields:
                for n in f["neighbours"]:
                    g = by_id.get(n)
                    if g is None or g["island"] != f["island"] or n < f["id"]:
                        continue
                    a0, a1 = wpos(f), wpos(g)
                    ld.line((a0, a1), fill=(20, 12, 8, 255), width=4)
                    col = (PALETTE.get(f["owner"], (0, 0, 0)) if f["owner"] == g["owner"]
                           else (70, 52, 38))
                    ld.line((a0, a1), fill=col + (255,), width=2)
        _links(img, world_links)
        for f in fields:
            s, ox, oy = WORLD_PLACE[f["island"]]
            x, y = ox + f["x"] * s, oy + f["y"] * s
            if f["war"]:
                col = PALETTE.get(f["war"]["attacker"], (255, 255, 255))
                d.ellipse((x - 11, y - 11, x + 11, y + 11), outline=col + (255,), width=3)
            _marker(img, x, y, f, 16)
    else:
        isl = int(view)
        bg = ImageEnhance.Brightness(world.convert("RGB")).enhance(0.55).convert("RGBA")
        img.alpha_composite(bg, (0, 0))
        img.alpha_composite(_art("continent%d.png" % isl), CONTINENT_ORIGIN)
        ox, oy = CONTINENT_ORIGIN
        d = _draw(img)
        mine = [f for f in fields if f["island"] == isl]
        def continent_links(ld):
            for f in mine:
                for n in f["neighbours"]:
                    g = by_id.get(n)
                    if g is None or g["island"] != isl or n < f["id"]:
                        continue
                    a0, a1 = (ox + f["x"], oy + f["y"]), (ox + g["x"], oy + g["y"])
                    ld.line((a0, a1), fill=(20, 12, 8, 255), width=7)
                    same = f["owner"] == g["owner"]
                    col = PALETTE.get(f["owner"], (0, 0, 0)) if same else (60, 44, 32)
                    ld.line((a0, a1), fill=col + (255,), width=3)
        _links(img, continent_links)
        fnt = _font(12)
        for f in mine:
            x, y = ox + f["x"], oy + f["y"]
            if f["war"]:
                col = PALETTE.get(f["war"]["attacker"], (255, 255, 255))
                d.ellipse((x - 19, y - 19, x + 19, y + 19), outline=col + (255,), width=4)
            _marker(img, x, y, f, 30)
            label = f["name"] + (" (%d)" % f["players"] if f["players"] else "")
            tb = d.textbbox((0, 0), label, font=fnt)
            tw, th = tb[2] - tb[0], tb[3] - tb[1]
            lx, ly = x - tw / 2, y + 16
            d.rounded_rectangle((lx - 6, ly - 3, lx + tw + 6, ly + th + 5), radius=6,
                                fill=(46, 38, 34, 225), outline=(150, 130, 110, 255))
            d.text((lx, ly - tb[1]), label, font=fnt, fill=(240, 232, 220, 255))
    # the side panel
    d = _draw(img)
    px = W + 16
    d.rectangle((W, 0, W + PANEL, H), fill=(38, 27, 19, 255))
    d.line((W, 0, W, H), fill=(110, 80, 50, 255), width=2)
    d.text((px, 14), snap["title"], font=_font(17), fill=(250, 236, 210, 255))
    sub = "World" if view == "world" else ISLAND_NAMES.get(int(view), "")
    d.text((px, 38), sub, font=_font(12), fill=(200, 180, 150, 255))
    y = 70
    d.text((px, y), "Nation", font=_font(12), fill=(170, 150, 120, 255))
    d.text((px + 150, y), "Fields", font=_font(12), fill=(170, 150, 120, 255))
    d.text((px + 205, y), "Online", font=_font(12), fill=(170, 150, 120, 255))
    y += 20
    for n in sorted(snap["nations"], key=lambda n: (-n["fields"], n["id"])):
        rgb = PALETTE.get(n["id"], (0, 0, 0))
        img.alpha_composite(_sprite("circle", rgb, 16), (px, y - 1))
        d.text((px + 22, y), n["name"], font=_font(14), fill=(245, 235, 220, 255))
        d.text((px + 158, y), str(n["fields"]), font=_font(14), fill=(245, 235, 220, 255))
        d.text((px + 215, y), str(n["online"]), font=_font(14), fill=(245, 235, 220, 255))
        y += 24
    y += 10
    wars = [f for f in fields if f["war"]]
    d.text((px, y), "Wars" if wars else "No wars under way", font=_font(13),
           fill=(230, 200, 150, 255))
    y += 22
    for f in wars[:8]:
        w = f["war"]
        left = ""
        if w["left_s"] is not None:
            left = " · %d:%02d" % (w["left_s"] // 60, w["left_s"] % 60)
        d.text((px, y), f["name"], font=_font(13), fill=(245, 235, 220, 255))
        y += 17
        d.text((px + 10, y), "%s vs %s · %s%s" % (
            _short(snap, w["attacker"]), _short(snap, f["owner"]),
            w["phase_name"], left), font=_font(11), fill=(200, 185, 165, 255))
        y += 20
    d.text((px, H - 22), time.strftime("updated %Y-%m-%d %H:%M UTC",
                                       time.gmtime(snap["updated"])),
           font=_font(11), fill=(150, 130, 105, 255))
    out = io.BytesIO()
    img.convert("RGB").save(out, "PNG", optimize=True)
    return out.getvalue()


def _short(snap, nid):
    for n in snap["nations"]:
        if n["id"] == nid:
            return n["name"]
    return "nobody" if not nid else "Nation %d" % nid


def render_cached(args, view):
    snap = cached_snapshot(args)
    sig = (view, snap["updated"] // 5, _material(snap))
    with _RENDER_LOCK:
        hit = _RENDER.get(view)
        if hit and hit[0] == sig:
            return hit[1]
    png = render_png(snap, view)
    with _RENDER_LOCK:
        _RENDER[view] = (sig, png)
    return png


def _material(snap):
    """What changes the picture: owners, wars, crowds -- not the clock."""
    h = hashlib.sha1()
    for f in snap["fields"]:
        w = f["war"] or {}
        h.update(("%d:%d:%d:%d:%d:%d:%d;" % (
            f["id"], f["owner"], f["players"], w.get("phase", 0),
            w.get("attacker", 0), w.get("def", 0), w.get("atk", 0))).encode())
    for n in snap["nations"]:
        h.update(("n%d:%d:%d;" % (n["id"], n["fields"], n["online"])).encode())
    return h.hexdigest()


# ---------------------------------------------------------------------------
# Discord -- one webhook message, edited in place
# ---------------------------------------------------------------------------
_UA = "DiscordBot (https://playonline.invalid/femap, 1) femap"


def _multipart(payload, png):
    boundary = "femap" + uuid.uuid4().hex
    parts = []
    parts.append(("--%s\r\nContent-Disposition: form-data; name=\"payload_json\"\r\n"
                  "Content-Type: application/json\r\n\r\n" % boundary).encode()
                 + json.dumps(payload).encode("utf-8") + b"\r\n")
    if png is not None:
        parts.append(("--%s\r\nContent-Disposition: form-data; name=\"files[0]\"; "
                      "filename=\"dominion.png\"\r\nContent-Type: image/png\r\n\r\n"
                      % boundary).encode() + png + b"\r\n")
    parts.append(("--%s--\r\n" % boundary).encode())
    return b"".join(parts), "multipart/form-data; boundary=%s" % boundary


def message_payload(args, snap, png):
    """The map message: an embed with the nations and wars as text, and the
    rendered map as its image when there is one."""
    marks = {1: "\U0001F534", 2: "\U0001F7E2", 3: "\U0001F535", 4: "\U0001F7E1",
             5: "\U0001FA75"}
    lines = []
    for n in sorted(snap["nations"], key=lambda n: (-n["fields"], n["id"])):
        lines.append("%s **%s** - %d fields · %d online" % (
            marks.get(n["id"], "⚪"), n["name"], n["fields"], n["online"]))
    wars = [f for f in snap["fields"] if f["war"]]
    if wars:
        lines.append("")
        for f in wars[:10]:
            w = f["war"]
            left = ""
            if w["left_s"] is not None:
                left = " · %d:%02d left" % (w["left_s"] // 60, w["left_s"] % 60)
            lines.append("⚔ **%s** - %s vs %s · %s%s · %d v %d" % (
                f["name"], _short(snap, w["attacker"]), _short(snap, f["owner"]),
                w["phase_name"], left, w["atk"], w["def"]))
    else:
        lines.append("\n*All quiet on every front.*")
    if snap.get("names"):
        crowd = [f for f in snap["fields"] if f.get("names")]
        if crowd:
            lines.append("")
            for f in crowd[:10]:
                lines.append("\U0001F4CD %s: %s" % (
                    f["name"], ", ".join(p["name"] for p in f["names"][:12])))
    embed = {"title": snap["title"],
             "description": "\n".join(lines)[:4000],
             "color": 0xC8763A,
             "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(snap["updated"])),
             "footer": {"text": "%d player%s in the world" % (
                 snap["in_world"], "" if snap["in_world"] == 1 else "s")}}
    url = (getattr(args, "map_url", "") or "").strip()
    if url:
        embed["url"] = url
    payload = {"embeds": [embed], "allowed_mentions": {"parse": []}}
    if png is not None:
        embed["image"] = {"url": "attachment://dominion.png"}
        payload["attachments"] = [{"id": 0, "filename": "dominion.png"}]
    else:
        payload["attachments"] = []
    return payload


def state_path(args):
    """--map-discord-state, else data/fe_map_discord.json resolved exactly as
    feworld.territory_path resolves its store (services/../data -- /data on
    prod, where /app is the read-only live mount)."""
    p = getattr(args, "map_discord_state", None)
    if p:
        return p
    here = os.path.dirname(os.path.abspath(__file__))
    d = os.path.normpath(os.path.join(here, os.pardir, "data"))
    return os.path.join(d if os.path.isdir(d) else here, "fe_map_discord.json")


class Discord:
    """The webhook side. Every network error is logged and survived."""

    def __init__(self, args, opener=None):
        self.args = args
        self.url = (getattr(args, "map_discord_webhook", "") or "").strip().rstrip("/")
        self.every = max(10.0, float(getattr(args, "map_discord_every", 60) or 60))
        self.view = str(getattr(args, "map_discord_view", "world") or "world")
        self.path = state_path(args)
        self.open = opener or urllib.request.urlopen
        self.ttl = max(0.0, float(getattr(args, "map_discord_event_ttl", 600.0) or 0))
        self.events = []        # [{"id", "t"}] -- status posts still up
        self.swept = False      # the first sweep clears the last run's posts
        self.msg_id = self._load()
        self.last_sig = None
        self.last_edit = 0.0
        self.hold_until = 0.0

    def _load(self):
        try:
            with open(self.path, encoding="utf-8") as fh:
                d = json.load(fh) or {}
            # the id belongs to ONE webhook; a new webhook starts fresh
            if d.get("hook") == hashlib.sha1(self.url.encode()).hexdigest():
                self.events = [{"id": str(e["id"]), "t": float(e.get("t") or 0)}
                               for e in (d.get("events") or []) if e.get("id")]
                return str(d.get("message_id") or "") or None
        except (OSError, ValueError, AttributeError):
            pass
        return None

    def _save(self):
        if not self.path or self.path == os.devnull:
            return
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = "%s.tmp.%d" % (self.path, os.getpid())
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump({"hook": hashlib.sha1(self.url.encode()).hexdigest(),
                           "message_id": self.msg_id,
                           "events": self.events}, fh)
            os.replace(tmp, self.path)
        except OSError as e:
            print("[femap] discord: could not save the message id (%s)" % e, flush=True)

    def _call(self, method, url, body=None, ctype=None):
        req = urllib.request.Request(url, data=body, method=method)
        req.add_header("User-Agent", _UA)
        if ctype:
            req.add_header("Content-Type", ctype)
        try:
            with self.open(req, timeout=20) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else {})
        except urllib.error.HTTPError as e:
            raw = b""
            try:
                raw = e.read()
            except Exception:                          # noqa: BLE001
                pass
            try:
                data = json.loads(raw) if raw else {}
            except ValueError:
                data = {"raw": raw[:200].decode("utf-8", "replace")}
            return e.code, data
        except (urllib.error.URLError, OSError, ValueError) as e:
            return 0, {"error": str(e)}

    def _limited(self, status, data):
        if status == 429:
            try:
                self.hold_until = time.time() + float(data.get("retry_after", 5))
            except (TypeError, ValueError):
                self.hold_until = time.time() + 5
            print("[femap] discord: rate limited, holding %.0f s"
                  % (self.hold_until - time.time()), flush=True)
            return True
        return False

    def tick(self, snap, now=None, force=False):
        """Edit (or first post) the map message when something changed, or
        every ten minutes so its clock stays honest. Returns what it did."""
        now = time.time() if now is None else now
        if not self.url or now < self.hold_until:
            return None
        self.sweep(now)
        sig = _material(snap)
        if not force:
            if now - self.last_edit < self.every:
                return None
            if sig == self.last_sig and now - self.last_edit < 600:
                return None
        png = render_png(snap, self.view)
        body, ctype = _multipart(message_payload(self.args, snap, png), png)
        did = None
        if self.msg_id:
            st, data = self._call("PATCH", "%s/messages/%s" % (self.url, self.msg_id),
                                  body, ctype)
            if st == 200:
                did = "edited"
            elif st == 404:
                print("[femap] discord: the map message is gone -- posting a "
                      "new one", flush=True)
                self.msg_id = None
            elif not self._limited(st, data):
                print("[femap] discord: edit failed (%s %s)" % (st, _brief(data)),
                      flush=True)
                return "failed"
            else:
                return "limited"
        if not self.msg_id:
            st, data = self._call("POST", self.url + "?wait=true", body, ctype)
            if st in (200, 201) and data.get("id"):
                self.msg_id = str(data["id"])
                self._save()
                did = "posted"
                print("[femap] discord: map message posted (id %s)" % self.msg_id,
                      flush=True)
            elif self._limited(st, data):
                return "limited"
            else:
                print("[femap] discord: post failed (%s %s)" % (st, _brief(data)),
                      flush=True)
                return "failed"
        self.last_sig, self.last_edit = sig, now
        return did

    def say(self, text):
        """One short message (a capture, a war). Posted with ?wait=true so its
        id comes back: that id is how sweep() deletes it later."""
        if not self.url or time.time() < self.hold_until:
            return False
        body = json.dumps({"content": text[:1900],
                           "allowed_mentions": {"parse": []}}).encode("utf-8")
        st, data = self._call("POST", self.url + "?wait=true", body, "application/json")
        if st in (200, 204):
            if data.get("id"):
                self.events.append({"id": str(data["id"]), "t": time.time()})
                self._save()
            return True
        if not self._limited(st, data):
            print("[femap] discord: message failed (%s %s)" % (st, _brief(data)),
                  flush=True)
        return False


    def sweep(self, now=None):
        """Delete status posts older than --map-discord-event-ttl -- and, on
        the first call after a restart, every one the last run left behind --
        so the map message stays the one thing this webhook keeps in the
        channel (design choice, 2026-09-12). A 404 counts as done: someone already
        deleted it. Returns how many went."""
        now = time.time() if now is None else now
        if not self.url or now < self.hold_until:
            return 0
        first, self.swept = not self.swept, True
        doomed = [e for e in self.events
                  if first or (self.ttl > 0 and now - e["t"] >= self.ttl)]
        gone = 0
        for e in doomed:
            st, data = self._call("DELETE", "%s/messages/%s" % (self.url, e["id"]))
            if st in (200, 204, 404):
                self.events = [x for x in self.events if x["id"] != e["id"]]
                gone += 1
            elif self._limited(st, data):
                break
            else:
                print("[femap] discord: could not delete message %s (%s %s)"
                      % (e["id"], st, _brief(data)), flush=True)
        if gone:
            self._save()
            print("[femap] discord: deleted %d %s status post(s)"
                  % (gone, "left-over" if first else "expired"), flush=True)
        return gone


def _brief(data):
    s = json.dumps(data)[:160]
    return s


# ---------------------------------------------------------------------------
# the watcher thread and the page server
# ---------------------------------------------------------------------------
def _watch(args, discord, period=5.0):
    last_err = 0.0
    while True:
        try:
            snap = snapshot(args)
            with _SNAP_LOCK:
                _SNAP.update(t=time.time(), snap=snap)
            new = observe(snap)
            for ev in new:
                print("[femap] %s" % event_text(args, ev).replace("**", ""),
                      flush=True)
            if discord is not None:
                if new and getattr(args, "map_discord_events", "on") == "on":
                    for ev in new[:5]:
                        discord.say(event_text(args, ev))
                discord.tick(snap, force=bool(new))
        except Exception as e:                         # noqa: BLE001
            if time.time() - last_err > 300:
                last_err = time.time()
                import traceback
                print("[femap] watcher error (%s) -- still running" % e, flush=True)
                traceback.print_exc()
        time.sleep(period)


class _Handler(BaseHTTPRequestHandler):
    server_version = "femap/1"

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body, ctype, cache="no-store"):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        path = u.path
        args = self.server.map_args
        if path in ("/", "/index.html"):
            return self._send(200, PAGE, "text/html; charset=utf-8")
        if path == "/state.json":
            try:
                snap = cached_snapshot(args)
            except Exception as e:                     # never take the server down
                return self._send(500, json.dumps({"error": "%s: %s" % (
                    type(e).__name__, e)}), "application/json")
            return self._send(200, json.dumps(snap, separators=(",", ":")),
                              "application/json; charset=utf-8")
        if path.startswith("/art/"):
            name = path[5:]
            if name not in ART_FILES:
                return self._send(404, "no such art", "text/plain")
            try:
                with open(os.path.join(ART_DIR, name), "rb") as fh:
                    blob = fh.read()
            except OSError:
                return self._send(404, "missing", "text/plain")
            return self._send(200, blob, "image/png", "public, max-age=86400")
        if path == "/map.png":
            view = (urllib.parse.parse_qs(u.query).get("view") or ["world"])[0]
            if view not in ("world", "1", "2", "3", "4", "5", "6"):
                return self._send(400, "view is world or 1..6", "text/plain")
            png = render_cached(args, view)
            if png is None:
                return self._send(503, "this server has no Pillow", "text/plain")
            return self._send(200, png, "image/png", "no-cache")
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        return self._send(404, "not found", "text/plain")


def serve(args, port, bind="127.0.0.1"):
    """Start the page server on a daemon thread and return it."""
    if ThreadingHTTPServer is None:                    # pragma: no cover
        raise SystemExit("--map-port needs Python 3.7+")
    srv = ThreadingHTTPServer((bind or "127.0.0.1", int(port)), _Handler)
    srv.daemon_threads = True
    srv.map_args = args
    threading.Thread(target=srv.serve_forever, name="fe-map", daemon=True).start()
    return srv


def start(args):
    """feworld's register_start hook: the page, the watcher, the webhook."""
    port = int(getattr(args, "map_port", 0) or 0)
    hook = (getattr(args, "map_discord_webhook", "") or "").strip()
    if not port and not hook:
        return
    if port:
        serve(args, port, getattr(args, "map_bind", "127.0.0.1"))
        print("[femap] live dominion map on http://%s:%d/ (names %s)"
              % (getattr(args, "map_bind", "127.0.0.1"), port,
                 getattr(args, "map_names", "off")), flush=True)
    discord = None
    if hook:
        if not hook.startswith("https://") or "/api/webhooks/" not in hook:
            print("[femap] --map-discord-webhook does not look like a Discord "
                  "webhook URL -- Discord posting is OFF", flush=True)
        else:
            discord = Discord(args)
            print("[femap] Discord webhook set: the map message is %s"
                  % ("message %s, edited in place" % discord.msg_id
                     if discord.msg_id else "posted on the first tick"), flush=True)
    threading.Thread(target=_watch, args=(args, discord), name="fe-map-watch",
                     daemon=True).start()


# ---------------------------------------------------------------------------
# the page
# ---------------------------------------------------------------------------
PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Fantasy Earth - Dominion</title>
<style>
:root{--bg:#1a120c;--panel:#2a1c13;--edge:#6b4a30;--ink:#f3e6cf;--dim:#bfa98a;--head:#4a2c1a;--card:#efe2c8;--cardink:#2b1d14}
*{box-sizing:border-box}
html,body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.4 "Trebuchet MS","Segoe UI",system-ui,sans-serif}
header{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap;padding:12px 18px;border-bottom:1px solid var(--edge);background:linear-gradient(#2e1e14,#22160f)}
header h1{margin:0;font-size:20px;letter-spacing:.5px}
#live{font-size:12px;color:var(--dim)}
#live.bad{color:#ff9a7a}
main{display:flex;flex-wrap:wrap;gap:16px;padding:14px 18px;align-items:flex-start}
#mapcol{flex:1 1 640px;min-width:0;max-width:1100px}
#tabs{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px}
#tabs button{font:inherit;font-size:13px;color:var(--ink);background:var(--panel);border:1px solid var(--edge);border-radius:14px;padding:4px 12px;cursor:pointer}
#tabs button[aria-pressed=true]{background:#7a4a26;border-color:#c98a50}
#wrap{position:relative;width:100%;aspect-ratio:4/3;border:2px solid #4a3020;border-radius:6px;overflow:hidden;background:#3a2616}
#map{position:absolute;inset:0;width:100%;height:100%;display:block;cursor:default}
#card{position:absolute;min-width:220px;max-width:280px;pointer-events:none;background:var(--card);color:var(--cardink);border:1px solid #5a3a22;border-radius:6px;box-shadow:0 4px 14px #0008;font-size:13px}
#card .h{background:var(--head);color:#fff;padding:4px 10px;border-radius:5px 5px 0 0;font-weight:bold}
#card .b{padding:6px 10px}
#card .r{display:flex;justify-content:space-between;gap:10px;border-bottom:1px dotted #b9a17f;padding:2px 0}
#card .r:last-child{border-bottom:0}
#card .names{margin-top:4px;font-size:12px;color:#4a3526}
aside{flex:0 1 300px;min-width:260px;display:flex;flex-direction:column;gap:14px}
.box{background:var(--panel);border:1px solid var(--edge);border-radius:8px;padding:10px 12px}
.box h2{margin:0 0 8px;font-size:14px;color:#e8c592;letter-spacing:.4px}
table{width:100%;border-collapse:collapse}
td,th{padding:3px 4px;text-align:left}
th{font-weight:normal;color:var(--dim);font-size:12px}
td.n{text-align:right;font-variant-numeric:tabular-nums}
.sw{display:inline-block;width:12px;height:12px;border-radius:50%;border:2px solid #000;vertical-align:-2px;margin-right:6px}
ul{margin:0;padding:0;list-style:none}
li{padding:4px 0;border-bottom:1px solid #3a281b}
li:last-child{border-bottom:0}
li.war{cursor:pointer}
li.war:hover{color:#fff}
.muted{color:var(--dim);font-size:12px}
footer{padding:4px 18px 16px;color:var(--dim);font-size:12px}
@media (max-width:760px){main{padding:10px}aside{flex:1 1 100%;min-width:0}header{padding:10px 12px}}
</style></head>
<body>
<header><h1 id="title">Fantasy Earth - Dominion</h1><span id="live">connecting…</span></header>
<main>
  <div id="mapcol">
    <nav id="tabs" aria-label="Continents"></nav>
    <div id="wrap"><canvas id="map" width="800" height="600"></canvas><div id="card" hidden></div></div>
  </div>
  <aside>
    <section class="box"><h2>Nations</h2>
      <table><thead><tr><th>Nation</th><th class="n">Fields</th><th class="n">Online</th></tr></thead><tbody id="nations"></tbody></table>
    </section>
    <section class="box"><h2>Wars</h2><ul id="wars"></ul></section>
    <section class="box"><h2>Recent</h2><ul id="events"></ul></section>
  </aside>
</main>
<footer>Map art © SQUARE ENIX, from the Fantasy Earth client. Live data from this server.</footer>
<script>
"use strict";
const $ = s => document.querySelector(s);
const cv = $('#map'), ctx = cv.getContext('2d');
let S = null, VIEW = 'world', HOVER = null, PIN = null, MOUSE = null, OK_AT = 0, FAILS = 0;
const IMG = {};
const TINT = new Map();
const CELL = {circle:[0,0,32,32], shield:[0,32,32,32]};

function loadImg(name){
  return new Promise(res => { const i = new Image(); i.onload = () => res(i); i.onerror = () => res(null); i.src = 'art/' + name; IMG[name] = i; });
}
function tinted(cell, hex){
  const key = cell + hex; let c = TINT.get(key);
  if (c) return c;
  const m = IMG['mark.png']; const [sx, sy, w, h] = CELL[cell];
  c = document.createElement('canvas'); c.width = w; c.height = h;
  const g = c.getContext('2d');
  g.drawImage(m, sx, sy, w, h, 0, 0, w, h);
  g.globalCompositeOperation = 'multiply'; g.fillStyle = hex; g.fillRect(0, 0, w, h);
  g.globalCompositeOperation = 'destination-in'; g.drawImage(m, sx, sy, w, h, 0, 0, w, h);
  TINT.set(key, c); return c;
}
const colorOf = n => (S && S.palette[String(n)]) || '#000000';
const nationName = n => { if (!S) return ''; const r = S.nations.find(x => x.id === n); return r ? r.name : (n ? 'Nation ' + n : 'nobody'); };
const heldBy = n => { const r = S && S.nations.find(x => x.id === n); return r ? r.fields : 0; };
function fmtLeft(s){ if (s == null) return ''; return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); }
function ago(t){ const d = Math.max(0, Math.round(Date.now() / 1000 - t)); if (d < 60) return d + 's ago'; if (d < 3600) return Math.round(d / 60) + 'm ago'; return Math.round(d / 3600) + 'h ago'; }

function fieldPos(f){
  if (VIEW === 'world'){ const w = S.islands[String(f.island)].world; return [w[1] + f.x * w[0], w[2] + f.y * w[0]]; }
  const o = S.continent_origin; return [o[0] + f.x, o[1] + f.y];
}
function visibleFields(){ return !S ? [] : (VIEW === 'world' ? S.fields : S.fields.filter(f => f.island === VIEW)); }

function drawMarker(f, x, y, size, t){
  const col = colorOf(f.owner);
  if (f.war){
    const pulse = 0.45 + 0.55 * Math.abs(Math.sin(t / 380));
    ctx.save(); ctx.globalAlpha = pulse; ctx.strokeStyle = colorOf(f.war.attacker);
    ctx.lineWidth = VIEW === 'world' ? 2.5 : 4;
    ctx.beginPath(); ctx.arc(x, y, size * (0.62 + 0.12 * pulse), 0, Math.PI * 2); ctx.stroke(); ctx.restore();
  }
  if (f.capital){ ctx.drawImage(tinted('shield', col), x - size / 2, y - size / 2, size, size); }
  else {
    ctx.drawImage(tinted('circle', col), x - size / 2, y - size / 2, size, size);
    const s2 = size * 0.55; ctx.drawImage(tinted('circle', col), x - s2 / 2, y - s2 / 2, s2, s2);
  }
  if (f === HOVER || f === PIN){ ctx.save(); ctx.strokeStyle = '#fff6d8'; ctx.lineWidth = 2; ctx.beginPath(); ctx.arc(x, y, size * 0.62, 0, Math.PI * 2); ctx.stroke(); ctx.restore(); }
}
function plate(text, x, y){
  ctx.font = '12px "Trebuchet MS", sans-serif';
  const w = ctx.measureText(text).width + 12, h = 17, lx = x - w / 2;
  ctx.fillStyle = 'rgba(46,38,34,0.88)'; ctx.strokeStyle = 'rgba(160,140,118,0.95)'; ctx.lineWidth = 1;
  ctx.beginPath(); ctx.roundRect ? ctx.roundRect(lx, y, w, h, 7) : ctx.rect(lx, y, w, h); ctx.fill(); ctx.stroke();
  ctx.fillStyle = '#f2ebe0'; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(text, x, y + h / 2 + 0.5);
}
// neighbour lines in both owners' colours (a gradient), dashed and moving
// where either end is at war -- the continent view's look, at any width
const LINK_ALPHA = 0.6;   // lines slightly transparent, so areas stand out
const LC = document.createElement('canvas'), lctx = LC.getContext('2d');
function drawLinks(fs, t, dark, lw, dash, speed){
  // drawn at full strength on their own layer, then the LAYER is faded, so the
  // dark edge and the colour never darken each other
  if (LC.width !== cv.width || LC.height !== cv.height){ LC.width = cv.width; LC.height = cv.height; }
  lctx.setTransform(cv.width / 800, 0, 0, cv.height / 600, 0, 0);
  lctx.clearRect(0, 0, 800, 600);
  drawLinksOn(lctx, fs, t, dark, lw, dash, speed);
  ctx.save(); ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.globalAlpha = LINK_ALPHA;
  ctx.drawImage(LC, 0, 0); ctx.restore();
}
function drawLinksOn(ctx, fs, t, dark, lw, dash, speed){
  const byId = new Map(S.fields.map(f => [f.id, f]));
  ctx.save(); ctx.lineCap = 'round';
  for (const f of fs){
    for (const n of f.neighbours){
      const g = byId.get(n); if (!g || g.island !== f.island || n < f.id) continue;
      const [x0, y0] = fieldPos(f), [x1, y1] = fieldPos(g);
      ctx.strokeStyle = 'rgb(18,10,6)'; ctx.lineWidth = dark;
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke();
      const gr = ctx.createLinearGradient(x0, y0, x1, y1);
      gr.addColorStop(0, colorOf(f.owner)); gr.addColorStop(1, colorOf(g.owner));
      ctx.strokeStyle = gr; ctx.lineWidth = lw;
      if (f.war || g.war){ ctx.setLineDash(dash); ctx.lineDashOffset = -t / speed; }
      ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke(); ctx.setLineDash([]);
    }
  }
  ctx.restore();
}
function draw(t){
  const r = cv.getBoundingClientRect(), dpr = window.devicePixelRatio || 1;
  const W = Math.max(1, Math.round(r.width * dpr)), H = Math.max(1, Math.round(r.height * dpr));
  if (cv.width !== W || cv.height !== H){ cv.width = W; cv.height = H; }
  ctx.setTransform(W / 800, 0, 0, H / 600, 0, 0);
  ctx.clearRect(0, 0, 800, 600);
  if (!IMG['world.png'] || !IMG['world.png'].complete) return;
  if (VIEW === 'world'){
    ctx.drawImage(IMG['world.png'], 0, 0, 800, 600);
    if (!S) return;
    const hovIsl = HOVER && HOVER.island_only ? HOVER.island_only : null;
    for (const k in S.islands){
      const w = S.islands[k].world, sz = 512 * w[0];
      if (String(hovIsl) === k){
        ctx.save(); ctx.fillStyle = 'rgba(255,240,200,0.14)'; ctx.strokeStyle = 'rgba(255,236,190,0.8)'; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.roundRect ? ctx.roundRect(w[1] + sz * 0.05, w[2] + sz * 0.05, sz * 0.9, sz * 0.9, 18) : ctx.rect(w[1], w[2], sz, sz); ctx.fill(); ctx.stroke(); ctx.restore();
        plate(S.islands[k].name + ' - click to open', w[1] + sz / 2, w[2] + sz * 0.93);
      }
    }
    drawLinks(S.fields, t, 3.4, 1.5, [4, 3], 60);
    for (const f of S.fields){ const [x, y] = fieldPos(f); drawMarker(f, x, y, 13, t); }
    return;
  }
  ctx.drawImage(IMG['world.png'], 0, 0, 800, 600);
  ctx.fillStyle = 'rgba(25,14,8,0.5)'; ctx.fillRect(0, 0, 800, 600);
  const img = IMG['continent' + VIEW + '.png'], o = S ? S.continent_origin : [31, 29];
  if (img && img.complete) ctx.drawImage(img, o[0], o[1], 512, 512);
  if (!S) return;
  const fs = visibleFields(), byId = new Map(S.fields.map(f => [f.id, f]));
  drawLinks(fs, t, 8, 3.5, [10, 7], 40);
  for (const f of fs){ const [x, y] = fieldPos(f); drawMarker(f, x, y, 26, t); }
  for (const f of fs){ const [x, y] = fieldPos(f); plate(f.name + (f.players ? '  ·  ' + f.players : ''), x, y + 15); }
}
function loop(t){ draw(t); requestAnimationFrame(loop); }

function toLogical(ev){ const r = cv.getBoundingClientRect(); return [(ev.clientX - r.left) * 800 / r.width, (ev.clientY - r.top) * 600 / r.height]; }
function hit(x, y){
  let best = null, bd = VIEW === 'world' ? 10 : 16;
  for (const f of visibleFields()){ const [fx, fy] = fieldPos(f), d = Math.hypot(fx - x, fy - y); if (d < bd){ bd = d; best = f; } }
  if (best || VIEW !== 'world' || !S) return best;
  for (const k in S.islands){ const w = S.islands[k].world, sz = 512 * w[0]; if (x >= w[1] && x <= w[1] + sz && y >= w[2] && y <= w[2] + sz) return {island_only: +k}; }
  return null;
}
function esc(s){ return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function showCard(f, ev){
  const card = $('#card');
  if (!f || f.island_only){ card.hidden = true; return; }
  let rows = '<div class="r"><span>Fields Held</span><b>' + heldBy(f.owner) + '</b></div>'
    + '<div class="r"><span>Total Players</span><b>' + f.players + 'p</b></div>'
    + '<div class="r"><span>Status</span><b>' + esc(f.war ? f.war.phase_name : 'Peace') + '</b></div>';
  if (f.war){
    rows += '<div class="r"><span>Attacker</span><b>' + esc(nationName(f.war.attacker)) + '</b></div>'
      + '<div class="r"><span>Defenders / Attackers</span><b>' + f.war.def + ' / ' + f.war.atk + '</b></div>';
    if (f.war.left_s != null) rows += '<div class="r"><span>Time left</span><b>' + fmtLeft(f.war.left_s) + '</b></div>';
    const k = f.war.keeps || {};
    if (k.def) rows += '<div class="r"><span>Castle</span><b>' + k.def[0] + ' / ' + k.def[1] + '</b></div>';
    if (k.atk) rows += '<div class="r"><span>Keep</span><b>' + k.atk[0] + ' / ' + k.atk[1] + '</b></div>';
  }
  if (f.names && f.names.length) rows += '<div class="names">' + f.names.map(p => '<span class="sw" style="background:' + colorOf(p.nation) + '"></span>' + esc(p.name)).join('<br>') + '</div>';
  card.innerHTML = '<div class="h">' + esc(nationName(f.owner)) + ' / ' + esc(f.name) + (f.capital ? ' (capital)' : '') + '</div><div class="b">' + rows + '</div>';
  card.hidden = false;
  const wr = $('#wrap').getBoundingClientRect(), cw = card.offsetWidth, ch = card.offsetHeight;
  let lx = ev ? ev.clientX - wr.left + 16 : wr.width - cw - 10, ly = ev ? ev.clientY - wr.top + 16 : 10;
  if (lx + cw > wr.width - 6) lx = Math.max(6, lx - cw - 32);
  if (ly + ch > wr.height - 6) ly = Math.max(6, wr.height - ch - 6);
  card.style.left = lx + 'px'; card.style.top = ly + 'px';
}
cv.addEventListener('mousemove', ev => { MOUSE = ev; const [x, y] = toLogical(ev); HOVER = hit(x, y); cv.style.cursor = HOVER ? 'pointer' : 'default'; if (!PIN) showCard(HOVER, ev); });
cv.addEventListener('mouseleave', () => { HOVER = null; if (!PIN) showCard(null); });
cv.addEventListener('click', ev => {
  const [x, y] = toLogical(ev), h = hit(x, y);
  if (h && h.island_only){ setView(h.island_only); return; }
  if (h && VIEW === 'world'){ setView(h.island); PIN = h; showCard(h, null); return; }
  PIN = (h && h !== PIN) ? h : null; showCard(PIN || h, ev);
});
function setView(v, fromHash){ VIEW = v; PIN = null; HOVER = null; showCard(null); renderTabs(); if (!fromHash) { try { history.replaceState(null, '', v === 'world' ? '#' : '#c' + v); } catch (e) {} } }
function viewFromHash(){ const m = /^#c([1-6])$/.exec(location.hash); return m ? +m[1] : 'world'; }
window.addEventListener('hashchange', () => setView(viewFromHash(), true));
function renderTabs(){
  const nav = $('#tabs'); const items = [['world', 'World']];
  if (S) for (const k in S.islands) items.push([+k, S.islands[k].name]);
  nav.innerHTML = '';
  for (const [v, label] of items){ const b = document.createElement('button'); b.textContent = label; b.setAttribute('aria-pressed', String(v === VIEW)); b.onclick = () => setView(v); nav.appendChild(b); }
}
function renderSide(){
  $('#title').textContent = S.title; document.title = S.title;
  const tb = $('#nations'); tb.innerHTML = '';
  for (const n of [...S.nations].sort((a, b) => b.fields - a.fields || a.id - b.id)){
    const tr = document.createElement('tr');
    tr.innerHTML = '<td><span class="sw" style="background:' + n.color + '"></span>' + esc(n.name) + '</td><td class="n">' + n.fields + '</td><td class="n">' + n.online + '</td>';
    tb.appendChild(tr);
  }
  const wl = $('#wars'); wl.innerHTML = '';
  const wars = S.fields.filter(f => f.war);
  if (!wars.length) wl.innerHTML = '<li class="muted">All quiet on every front.</li>';
  for (const f of wars){
    const li = document.createElement('li'); li.className = 'war';
    li.innerHTML = '<b>' + esc(f.name) + '</b><br><span class="muted">' + esc(nationName(f.war.attacker)) + ' vs ' + esc(nationName(f.owner)) + ' · ' + esc(f.war.phase_name) + (f.war.left_s != null ? ' · ' + fmtLeft(f.war.left_s) + ' left' : '') + ' · ' + f.war.atk + ' v ' + f.war.def + '</span>';
    li.onclick = () => { setView(f.island); PIN = f; showCard(f, null); };
    wl.appendChild(li);
  }
  const el = $('#events'); el.innerHTML = '';
  if (!S.events.length) el.innerHTML = '<li class="muted">Nothing has changed hands since the server started.</li>';
  for (const e of S.events){
    const li = document.createElement('li');
    const txt = e.kind === 'capture' ? esc(nationName(e.to)) + ' took <b>' + esc(e.name) + '</b> from ' + esc(nationName(e.from))
      : e.kind === 'war' ? esc(nationName(e.attacker)) + ' declared war at <b>' + esc(e.name) + '</b>' : esc(e.kind);
    li.innerHTML = txt + ' <span class="muted">' + ago(e.t) + '</span>'; el.appendChild(li);
  }
}
async function poll(){
  try {
    const r = await fetch('state.json', {cache: 'no-store'});
    if (!r.ok) throw new Error('HTTP ' + r.status);
    const fresh = await r.json();
    const first = !S; S = fresh; OK_AT = Date.now(); FAILS = 0;
    if (PIN) PIN = S.fields.find(f => f.id === PIN.id) || null;
    if (HOVER && !HOVER.island_only) HOVER = S.fields.find(f => f.id === HOVER.id) || null;
    if (first){ VIEW = viewFromHash(); renderTabs(); }
    renderSide();
    if (PIN) showCard(PIN, null);
  } catch (e) { FAILS++; }
  const live = $('#live');
  if (S && FAILS === 0){ live.textContent = 'live · ' + S.in_world + ' in the world · updated ' + new Date(S.updated * 1000).toLocaleTimeString(); live.className = ''; }
  else { live.textContent = S ? 'reconnecting… (last update ' + ago(OK_AT / 1000) + ')' : 'connecting…'; live.className = 'bad'; }
  setTimeout(poll, Math.max(1500, ((S && S.poll_s) || 3) * 1000) * (FAILS ? Math.min(4, FAILS) : 1));
}
Promise.all(['world.png', 'mark.png', 'continent1.png', 'continent2.png', 'continent3.png', 'continent4.png', 'continent5.png', 'continent6.png'].map(loadImg)).then(() => { TINT.clear(); });
renderTabs();
poll();
requestAnimationFrame(loop);
</script>
</body></html>
"""
