"""Kill rewards: EXP and gold per kill, treasure chests on the ground."""
import os
import struct
import sys
import threading
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
import random as _random_mod                               # noqa: E402
from . import character, combat, deps, entities, equipment, ext, extrun, inventory, itemrecords, monsters, progression, sess, wallet, wire

#: 2026-09-11 (second pass): kills pay by the monster's LEVEL, the RoD way
DEFAULT_KILL_GOLD = "rod"
DEFAULT_MOB_EXP = "rod"


def mob_exp(args, m):
    """EXP one kill of monster `m` pays. --kill-exp N > 0 pins a flat N.

    `--mob-exp rod` (default) = ROD_MOB_EXP by the monster's LEVEL -- what
    the 2006 server paid, observed by players (fewiki `Monster`). KEY: CHOSEN
    OVER THE SHIPPED COLUMN: fet_npc_type ships an EXP column (4 for a L1
    Venomous, 92 at L10, 400 at L30, 823 for the L53 Duke Orc) and the 2006
    server paid 1, 10 and 195 at L1/L10/L30 (nothing is recorded above L44).
    EXP is computed by the
    SERVER and pushed as a number (0x1075 bit 0x4) -- the client never reads
    that column to pay anyone -- so the retail server's observed values win
    over a client-side table nothing is known to consume. `--mob-exp shipped`
    = that column (fegamedata.npc_exp), the 2026-09-09..11 behaviour."""
    n = int(getattr(args, "kill_exp", 0) or 0)
    if n > 0:
        return n
    if str(getattr(args, "mob_exp", DEFAULT_MOB_EXP)) == "shipped":
        return int(fegamedata.npc_exp(m.get("type", 0), m.get("type_id")) or 0)
    return progression.rod_mob_exp(m.get("level") or 1)


def kill_gold(args, m):
    """The gold one kill of monster `m` is worth under --kill-gold. `rod`
    (default) = ROD_MOB_GOLD by the monster's level (5 G at L1 ... 3,735 G at
    L40, fewiki `Monster` 2006); `exp` = the monster's SHIPPED EXP reward
    (OURS, the 2026-09-11 first pass); N = flat; off = nothing."""
    spec = str(getattr(args, "kill_gold", DEFAULT_KILL_GOLD) or "off")
    if spec == "off":
        return 0
    if spec == "rod":
        return progression.rod_mob_gold(m.get("level") or 1)
    if spec == "exp":
        return int(fegamedata.npc_exp(m.get("type", 0), m.get("type_id")) or 0)
    try:
        return max(0, int(spec, 0))
    except ValueError:
        return 0


def kill_gold_credit(conn, outbound, mode, be, args, m, amount=None):
    """Pay the kill (see kill_gold) into the stored wallet and push it.
    `amount` is this player's SHARE when the kill is split by damage
    (combat.kill_rewards, manual p.38); None = the whole kill_gold."""
    n = kill_gold(args, m) if amount is None else int(amount)
    if n > 0:
        return wallet.wallet_credit(conn, outbound, mode, be, args, n, 0,
                             "killed %s" % (m.get("name") or "a monster"))
    return 0


# ---------------------------------------------------------------------------
# KEY: MONSTER DROPS -- TREASURE CHESTS ON THE GROUND, like retail (2026-09-12).
#
# Design decisions: "we definitely want to have monster item drops", "Just like
# retail", "only the killer or their party", rates as proposed.
# What the client does, read off the dump (READ-OFF-CODE unless marked):
#
#   * a chest is 0x1006 TYPE 2 (arm 0x0503b7ff): [cstr name][u16 item][u8
#     +0x3ae][f32 x y z] -- the name copied UNBOUNDED into [item+0x380] (so we
#     clamp it to 32 bytes), y replaced by the terrain height, sound 0x74 (the
#     「宝箱落とす」 chest drop). SE's 2006-03-29 notice fixed "treasure chests
#     that sometimes can't be picked up" (MEASURED) -- retail drops WERE chests.
#   * right-click on a targeted item object -> 0x201F [u32 obj] (0x05152ba0,
#     which range-gates on [0x529700c] and sets the in-flight bit 0x200000).
#     WARNING: The EN text overlay OVERWROTE that float (JP 20.0f), so the client
#     never refuses "too far" -- the server enforces --drop-range itself.
#   * 0x107A with announce = 1 (0x0503d637) prints the client's own
#     「%sを入手しました。」 ("Got %s.") and plays the get sound, and clears the
#     in-flight bit; 0x1023 [u32 obj] plays the pickup effect at the chest (it
#     neither deletes it nor fills the bag); 0x1004 deletes it. 0x1024 is a
#     [u16 code]: 1 bag full, 2 no right, 3 too far, 4 another took it, 6 not
#     in this field.
#
# The monsters are per-SESSION (each player has their own copy), so a party's
# chest is placed by the killer's session and RELAYED (ext_post) to party
# members in the same field and room; _CHESTS is shared and locked, so the
# first pickup wins everywhere and the others see it vanish. Rights = the
# killer's party at the moment of the kill. Lifetime --drop-lifetime (90 s).
# The rates are OURS (fe-drops.tsv): no 2006 rate survives.
# ---------------------------------------------------------------------------
DROPS_TSV = os.path.join(deps._HERE, "fedata", "fe-drops.tsv")
_DROP_LOCK = threading.Lock()
_CHESTS = {}            # obj -> chest (shared by every session thread)
_DROP_SEQ = [0]
_DROP_CACHE = {}
_DROP_RNG = _random_mod.Random()


def drop_table(path=None):
    """The drop rows, validated against the client's item table. A row naming
    an item the client does not have, or the wrong sex for its model row, is
    REFUSED (logged) -- granting it would be the null-item crash."""
    p = path or DROPS_TSV
    if p in _DROP_CACHE:
        return _DROP_CACHE[p]
    items = fegamedata.items()
    rows, refused, header = [], [], None
    try:
        fh = open(p, encoding="utf-8")
    except OSError as e:
        print("[feworld]    drops: no table %s (%s) -- nothing drops" % (p, e),
              flush=True)
        _DROP_CACHE[p] = []
        return []
    with fh:
        for ln in fh:
            ln = ln.rstrip("\r\n")
            if not ln.strip() or ln.lstrip().startswith("#"):
                continue
            cols = ln.split("\t")
            if header is None:
                header = cols
                continue
            r = dict(zip(header, cols))
            try:
                item = int(r["item_no"])
                lo, hi = int(r.get("lv_min") or 0), int(r.get("lv_max") or 999)
                ppm = int(r["rate_ppm"])
            except (KeyError, TypeError, ValueError):
                refused.append((ln, "unreadable row"))
                continue
            sex = (r.get("sex") or "-").strip().upper() or "-"
            it = items.get(item)
            if it is None:
                refused.append((ln, "item %d is not in the client's item table"
                                    % item))
                continue
            if (sex == "M" and not it.get("model_m")) or \
                    (sex == "F" and not it.get("model_f")):
                refused.append((ln, "item %d has no %s model" % (item, sex)))
                continue
            rows.append({"family": (r.get("family") or "").strip(), "lo": lo,
                         "hi": hi, "item": item, "sex": sex, "ppm": ppm,
                         "source": r.get("source", "")})
    print("[feworld]    drops: %d row(s) from %s%s" % (
        len(rows), os.path.basename(p),
        "; REFUSED %d: %s" % (len(refused), "; ".join(
            "%s (%s)" % (w, l.split("\t")[0]) for l, w in refused))
        if refused else ""), flush=True)
    _DROP_CACHE[p] = rows
    return rows


def char_sex(args):
    """'M' or 'F' for the session character ([unit+0x3aa]: 0 = male)."""
    ch = entities._self_char(args) or {}
    return "F" if int(ch.get("sex") or 0) else "M"


def drop_roll(args, m, sex, rng=None):
    """The drop row monster `m` gives a killer of `sex`, or None. Every row
    for its family and level rolls on its own; the RAREST row that hit wins
    (one item per kill)."""
    import fnmatch
    rng = rng or _DROP_RNG
    name, lvl = str(m.get("name") or ""), int(m.get("level") or 0)
    mult = float(getattr(args, "drop_rate_mult", 1.0) or 0.0)
    hits = []
    for r in drop_table(getattr(args, "drop_table", None)):
        if not fnmatch.fnmatchcase(name, r["family"]):
            continue
        if not (r["lo"] <= lvl <= r["hi"]) or r["sex"] not in ("-", sex):
            continue
        if rng.random() * 1000000.0 < r["ppm"] * mult:
            hits.append(r)
    if not hits:
        return None
    best = min(r["ppm"] for r in hits)
    return rng.choice([r for r in hits if r["ppm"] == best])


def drop_chest_record(obj, name, item, pos):
    """0x1006 type 2, the arm 0x0503b7ff shape (fewar.item_ground_record),
    name clamped to 32 cp932 bytes + NUL (the copy is unbounded)."""
    raw = str(name).encode("cp932", "replace")[:32]
    return (struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 2) + raw + b"\0"
            + struct.pack(">HB", item & 0xFFFF, 0)
            + struct.pack(">fff", float(pos[0]), float(pos[1]), float(pos[2])))


def party_charids(cid):
    """{charid} of `cid`'s party (feparty), always including `cid`."""
    out = {int(cid)}
    fp = sys.modules.get("feparty")
    try:
        p = fp._party_of(int(cid)) if fp is not None else None
    except Exception:                                  # noqa: BLE001
        p = None
    if p:
        for c in (p.get("members") or {}):
            try:
                out.add(int(c))
            except (TypeError, ValueError):
                pass
    return out


def _chest_here(s, c):
    return (s.get("in_field") and s.get("field") == c["field"]
            and s.get("room", -1) == c["room"])


def drop_on_kill(conn, outbound, mode, be, args, m, owner=None):
    """Roll monster `m`'s drop and, on a hit, put a chest on the ground where
    it died -- for the LOOT OWNER (combat.loot_winner: most damage, by party;
    this session's own player unless `owner` names another), and relayed to
    the owner's party in this field. A chest placed for another owner is
    only drawn here when this player is in that party."""
    if str(getattr(args, "drops", "on") or "on") != "on":
        return None
    me = sess._SESSION.get("charid")
    cid = me if owner is None else owner
    if not sess._SESSION.get("in_field") or cid is None:
        return None
    row = drop_roll(args, m, char_sex(args))
    if row is None:
        return None
    item = row["item"]
    name = fegamedata.items().get(item, {}).get("name") or "Item"
    base = int(getattr(args, "drop_obj_base", 900000) or 900000)
    life = float(getattr(args, "drop_lifetime", 90.0) or 90.0)
    # where it was DRAWN when it died, not the end of its last move
    x, y, z = monsters.mob_pos(m) if m.get("pos") else (0.0, 0.0, 0.0)
    with _DROP_LOCK:
        while True:
            _DROP_SEQ[0] += 1
            obj = base + (_DROP_SEQ[0] % 90000)
            if obj not in _CHESTS:
                break
        chest = {"obj": obj, "item": item, "name": name,
                 "pos": (float(x), float(y), float(z)),
                 "field": sess._SESSION.get("field"), "room": sess._SESSION.get("room", -1),
                 "owner": int(cid), "party": party_charids(cid),
                 "expires": time.monotonic() + life, "taken": False,
                 "shown": set(), "from": m.get("name"), "ppm": row["ppm"]}
        _CHESTS[obj] = chest
    here = me is not None and int(me) in chest["party"]
    if here:
        wire.send(conn, outbound, wire.inner_msg(0x1006, drop_chest_record(obj, name, item,
                                                                 chest["pos"]), obj),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        with _DROP_LOCK:
            chest["shown"].add(int(me))
    combat.battle_tally("drops", 1)
    others = chest["party"] - ({int(me)} if here else set())
    sent_to = 0
    if others:
        sent_to = extrun.ext_post("drop_chest", {"obj": obj}, to=lambda s, _n: (
            s.get("charid") is not None and int(s["charid"]) in others
            and _chest_here(s, chest)))
    print("[feworld]    DROP %s (L%s) -> chest obj %d: item %d %s (row %s, %d "
          "ppm) at (%.1f, %.1f, %.1f) -- 0x1006 type 2; the killer%s may pick it "
          "up for %gs"
          % (m.get("name"), m.get("level"), obj, item, name, row["family"],
             row["ppm"], x, y, z,
             " and %d party member(s) (%d relayed)" % (len(others), sent_to)
             if others else "", life), flush=True)
    return chest


def _drop_relay_chest(ctx, pl):
    """A party member's session: show the killer's chest here too."""
    with _DROP_LOCK:
        c = _CHESTS.get(pl.get("obj"))
    if not c or c["taken"] or not _chest_here(sess._SESSION, c):
        return
    ctx.reply(0x1006, drop_chest_record(c["obj"], c["name"], c["item"], c["pos"]),
              unit_id=c["obj"], why="drop chest %d (item %d) from a party "
              "member's kill" % (c["obj"], c["item"]))
    with _DROP_LOCK:
        c["shown"].add(int(sess._SESSION.get("charid") or 0))


def _drop_relay_gone(ctx, pl):
    """Another session picked the chest up: take it off this screen."""
    obj = int(pl.get("obj") or 0)
    if sess._SESSION.get("in_field"):
        ctx.reply(0x1004, b"", unit_id=obj,
                  why="MSG_DEL chest %d -- a party member picked it up" % obj)


ext.register_relay("drop_chest", _drop_relay_chest)
ext.register_relay("drop_gone", _drop_relay_gone)


def drop_pickup(ctx, obj):
    """0x201F on a DROP chest (feitems.on_gather asks first). None when `obj`
    is not one of ours -- the caller's own probe answers then."""
    with _DROP_LOCK:
        c = _CHESTS.get(int(obj)) if obj is not None else None
    if c is None:
        return None
    args, s = ctx.args, sess._SESSION
    cid = s.get("charid")

    def ng(code, why):
        ctx.reply(0x1024, struct.pack(">H", code),
                  why="MSG_ITEM_GATHER_NG [u16 %d] -- %s" % (code, why))
        return True

    if cid is None or int(cid) not in c["party"]:
        return ng(2, "chest %d is %s's party's" % (c["obj"], c["owner"]))
    if not _chest_here(s, c):
        return ng(6, "chest %d is in another field" % c["obj"])
    pos = s.get("cpos")
    reach = float(getattr(args, "drop_range", 20.0) or 20.0)
    if pos:
        d = ((pos[0] - c["pos"][0]) ** 2 + (pos[2] - c["pos"][2]) ** 2) ** 0.5
        # +3 u for a player position up to one heartbeat stale
        if d > reach + 3.0:
            return ng(3, "%.1f u away (--drop-range %g; the client's own check "
                         "is broken by the EN overlay)" % (d, reach))
    rows = itemrecords.item_rows(args)
    if len(rows) >= 96:
        return ng(1, "the bag is full (%d)" % len(rows))
    with _DROP_LOCK:
        if c["taken"]:
            gone = True
        else:
            c["taken"], gone = True, False
    if gone:
        return ng(4, "chest %d was already taken" % c["obj"])
    uid = inventory.new_item_uid(args, rows)
    new = rows + [(uid, c["item"], 1, 1)]
    if not character._store_char_field(args, "items", [list(x) for x in new]):
        with _DROP_LOCK:
            c["taken"] = False
        return ng(7, "the character store refused the write")
    ctx.reply(0x107A, equipment.item_add_body(uid, c["item"], len(rows), 1, None, 0, 1),
              why="MSG_ITEM_ADD item %d uid %d bag slot %d announce=1 -- the "
                  "client prints 'Got %s.' itself" % (c["item"], uid, len(rows),
                                                      c["name"]))
    ctx.reply(0x1023, struct.pack(">I", c["obj"]),
              why="MSG_ITEM_GATHER_OK (the pickup effect at the chest)")
    ctx.reply(0x1004, b"", unit_id=c["obj"], why="MSG_DEL the chest")
    with _DROP_LOCK:
        others = set(c["shown"]) - {int(cid)}
        _CHESTS.pop(c["obj"], None)
    if others:
        extrun.ext_post("drop_gone", {"obj": c["obj"]}, to=lambda s2, _n: (
            s2.get("charid") is not None and int(s2["charid"]) in others))
    combat.battle_tally("pickups", 1)
    print("[feworld]    PICKUP chest %d -> item %d %s into bag slot %d (uid %d)%s"
          % (c["obj"], c["item"], c["name"], len(rows), uid,
             "; gone from %d other screen(s)" % len(others) if others else ""),
          flush=True)
    return True


def drop_pump(conn, outbound, mode, be, args):
    """Expire this session's chests (--drop-lifetime), forget ones left behind
    in another field, and reap any a vanished session left in _CHESTS."""
    if not _CHESTS:
        return 0
    cid = sess._SESSION.get("charid")
    now = time.monotonic()
    with _DROP_LOCK:
        for o in [o for o, c in _CHESTS.items() if now > c["expires"] + 300]:
            _CHESTS.pop(o, None)
        mine = ([c for c in _CHESTS.values() if int(cid) in c["shown"]]
                if cid is not None else [])
    n = 0
    for c in mine:
        here = _chest_here(sess._SESSION, c)
        if here and now < c["expires"]:
            continue
        if here:
            wire.send(conn, outbound, wire.inner_msg(0x1004, b"", c["obj"]), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            n += 1
        with _DROP_LOCK:
            c["shown"].discard(int(cid))
            if not c["shown"]:
                _CHESTS.pop(c["obj"], None)
    if n:
        print("[feworld]    drops: %d chest(s) expired unclaimed -> 0x1004"
              % n, flush=True)
    return n
