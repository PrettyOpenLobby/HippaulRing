#!/usr/bin/env python3
"""fegamedata.py -- Fantasy Earth's SHIPPED game tables, served back to it.

dat.pak carries five `fet_*` tables (dumped by tools/fedatagen/fefet.py,
2026-09-05) plus the item table. Together they are most of the PvE content the
client expects a server to know:

    fedata/fe-fet-npc_generator_pos.tsv   720 monster spawn points keyed by
                                          FIELD id (= the 0x3031 group id
                                          space, 1..90): world x/y/z, radius,
                                          and a link to a spawner
    fedata/fe-fet-npc_generator_data.tsv  192 spawners -> npc type ids
    fedata/fe-fet-npc_type.tsv            1,372 NPC types: name, MODELTYPE
                                          (the u16 the 0x1006 type-8 record
                                          carries), script id -- the town's
                                          shopkeepers included
    fedata/fe-fet-castle_info.tsv         castle grid position per Hmap map
    fedata/fe-fet-initialize_equip.tsv    starting equipment per class,
                                          male | female item id lists
    fedata/fe-fet-items.tsv               598 items: name, SLOT TYPE, the two
                                          stat gates, the required class level
                                          and the up-to-two PREREQUISITE SKILL
                                          ids -- every field the equip
                                          validator 0x05076090 tests -- and,
                                          appended 2026-09-11, `price` (+0x90,
                                          u32) and `ring_price` (+0x94, u16):
                                          see items() for why those names

WARNING: WHAT DOES NOT SHIP, said once here so nobody hunts for it again: town NPC
POSITIONS (the spawn table has no rows for the capitals 21/39/57/62/78 nor
for field 5) and door destinations. Those were SE's server data. The town is
authored from the minimap by walking it -- see feworld's fe_town.json.

WARNING: COLUMN NAMES MARKED `?` IN THE TSVs ARE GUESSES read off the bytes, not off
the client's parsers. The columns used HERE are the ones cross-checked:
field ids match the FIELD log line's ids, modeltypes match
data_NPC_ModelType.dat, item ids match fet_initialize_equip's lists and the
slot type matches the axe/bow/wand/armour split.
"""
import csv
import io
import os
import struct

_HERE = os.path.dirname(os.path.abspath(__file__))
_DIR = os.environ.get("FE_GAMEDATA_DIR") or os.path.join(_HERE, "fedata")

#: Modeltypes data_NPC_ModelType.dat actually has (fenpc.py, 2026-08-27);
#: 0x051ab3f0 misses on anything else and the miss is a null deref later.
SHIPPED_MODELTYPES = frozenset(range(0, 48)) | frozenset(range(52, 275))

_cache = {}


def _rows(name, prefix="fe-fet-"):
    """The TSV as a list of dicts, cached; [] when the file is missing (a
    missing table disables what depends on it, it never breaks a login).

    `prefix` exists because not everything here comes out of a fet_* table:
    fe-map-heights.tsv is derived from the Hmap sound files, and naming it
    fe-fet-* would claim a provenance it does not have.
    """
    if name in _cache:
        return _cache[name]
    p = os.path.join(_DIR, "%s%s.tsv" % (prefix, name))
    rows = []
    try:
        # Plain split, not the csv module: the NPC name column carries raw
        # cp932-decoded junk for a few rows (quotes, NULs) that csv rejects.
        with io.open(p, encoding="utf-8", errors="replace") as fh:
            lines = [l.rstrip("\r\n").replace("\0", "") for l in fh]
        if lines:
            head = lines[0].split("\t")
            for l in lines[1:]:
                if not l:
                    continue
                f = l.split("\t")
                rows.append(dict(zip(head, f + [""] * (len(head) - len(f)))))
    except OSError:
        pass
    _cache[name] = rows
    return rows


def _int(v, default=0):
    try:
        return int(v, 0)
    except (TypeError, ValueError):
        return default


def npc_types():
    """{type_id: {"name", "modeltype", "script"}}"""
    if "npc_types" not in _cache:
        d = {}
        for r in _rows("npc_type"):
            d[_int(r["type_id"])] = {"name": r["name"],
                                     "modeltype": _int(r["modeltype"]),
                                     "script": _int(r.get("script_id?", "0"))}
        _cache["npc_types"] = d
    return _cache["npc_types"]


def spawners():
    """{gen_id: [npc type ids]}"""
    if "spawners" not in _cache:
        d = {}
        for r in _rows("npc_generator_data"):
            d[_int(r["gen_id"])] = [_int(x) for x in r["npc_type_ids"].split() if x]
        _cache["spawners"] = d
    return _cache["spawners"]


def spawn_points(field_id):
    """Every shipped spawn point on `field_id`, resolved to a modeltype:
    [(x, y, z, radius, modeltype, type_name, gen_id, npc_type_id)] in
    table order.

    Each spawner lists up to five npc types; the FIRST that resolves to a
    shipped modeltype is taken -- one monster per point. A spawner id with no
    row, or a type with no model, drops the point with nothing served,
    because a modeltype the client cannot load is a null deref a few frames
    later (feworld.monster_push).
    """
    out = []
    types, gens = npc_types(), spawners()
    for r in _rows("npc_generator_pos"):
        if _int(r["field_id"]) != int(field_id):
            continue
        gen = _int(r["tail_byte0"], -1)
        for tid in gens.get(gen, []):
            t = types.get(tid)
            if t and t["modeltype"] in SHIPPED_MODELTYPES:
                # `tid` is APPENDED (2026-09-06): the caller needs the npc
                # TYPE id, not just the model, to look the monster's hit
                # points and reward up in fet_npc_type -- several types share
                # one model, so the modeltype cannot get back to the row.
                out.append((float(r["x"]), float(r["y"]), float(r["z"]),
                            float(r["radius"]), t["modeltype"], t["name"],
                            gen, tid))
                break
    return out


def spawn_populations():
    """gen_id -> {"pop", "pop2", "types": [(npc_type_id, weight, cap)]}.

    KEY: HOW MANY MONSTERS A SPAWN POINT HOLDS (live report, 2026-09-12:
    "they're pretty sparse"). We served ONE per point. fet_npc_generator_data's
    record,
    read off the client's own parser (0x051a539a, sizeof 0x44): an id, three
    dwords, a u8 at +0x10 and a u8 at +0x11, then three counted arrays -- the
    npc type ids (+0x14), float WEIGHTS (+0x28) and a byte per type (+0x3c).
    fedata/fe-npc-generator-pop.tsv was read straight out of dat.pak in that
    order (the older fe-fet-npc_generator_data.tsv reader is two bytes skewed:
    its `u3` is really this +0x10/+0x11 pair).

    WHAT EACH IS -- A READING, not a measurement (nothing in the client was
    found consuming them; the spawner was SE's server's):
      * weights sum to 100 on 190 of 192 rows: the per-type SHARE.
      * +0x10 is 9 on 159 rows; on the others (+0x11 not 255) it EQUALS the
        sum of the +0x3c bytes on most -- which ties +0x10 to the count domain.
        So +0x10 = monsters alive at once; +0x3c = a cap per type.
    WARNING: A first reading off three rows ("12-20 per point") held on only 32 of 191
    and was retracted; this one was checked against every row.
    """
    if "spawn_pop" not in _cache:
        d = {}
        for r in _rows("npc-generator-pop", prefix="fe-"):
            try:
                tids = [int(x) for x in r["type_ids"].split()]
                ws = [float(x) for x in r["weights"].split()]
                caps = [int(x) for x in r["caps"].split()]
                d[int(r["gen_id"])] = {"pop": int(r["pop_10"]),
                                        "pop2": int(r["pop_11"]),
                                        "types": list(zip(tids, ws, caps))}
            except (KeyError, TypeError, ValueError):
                continue
        _cache["spawn_pop"] = d
    return _cache["spawn_pop"]


def allocate_population(pop, types):
    """{npc_type_id: count} for `pop` monsters over [(tid, weight, cap)].

    Largest remainder by weight, never past a type's cap, deterministic (so
    every player's copy of a field matches). Types with no cap get none; if
    every weight is 0 they share equally. The total is min(pop, sum of caps)."""
    elig = [(t, max(0.0, float(w)), int(c)) for t, w, c in types if int(c) > 0]
    if not elig or int(pop) <= 0:
        return {}
    total = min(int(pop), sum(c for _t, _w, c in elig))
    wsum = sum(w for _t, w, _c in elig)
    shares = [(t, ((w / wsum) if wsum > 0 else 1.0 / len(elig)) * total, c)
              for t, w, c in elig]
    counts = {t: min(c, int(q)) for t, q, c in shares}
    order = sorted(shares, key=lambda s: (-(s[1] - int(s[1])), -s[1], s[0]))
    left = total - sum(counts.values())
    while left > 0:
        moved = False
        for t, _q, c in order:
            if left <= 0:
                break
            if counts[t] < c:
                counts[t] += 1
                left -= 1
                moved = True
        if not moved:
            break
    return {t: n for t, n in counts.items() if n > 0}


def spawn_groups(field_id):
    """[(x, y, z, radius, gen_id, [(tid, modeltype, name, count)])] -- every
    shipped spawn point on `field_id` with its WHOLE population (see
    spawn_populations). Only types with a shipped modeltype are placed; a
    point with no population row falls back to spawn_points' one monster."""
    out = []
    types, pops, gens = npc_types(), spawn_populations(), spawners()
    for r in _rows("npc_generator_pos"):
        if _int(r["field_id"]) != int(field_id):
            continue
        gen = _int(r["tail_byte0"], -1)
        g = pops.get(gen)
        counts = {}
        if g:
            ship = [(tid, w, cap) for tid, w, cap in g["types"]
                    if tid >= 0 and types.get(tid)
                    and types[tid]["modeltype"] in SHIPPED_MODELTYPES]
            counts = allocate_population(g["pop"], ship)
        if not counts:
            for tid in gens.get(gen, []):
                t = types.get(tid)
                if t and t["modeltype"] in SHIPPED_MODELTYPES:
                    counts = {tid: 1}
                    break
        if not counts:
            continue
        members = sorted(((tid, types[tid]["modeltype"], types[tid]["name"], n)
                          for tid, n in counts.items()),
                         key=lambda m: (-m[3], m[0]))
        out.append((float(r["x"]), float(r["y"]), float(r["z"]),
                    float(r["radius"]), gen, members))
    return out


def fields_with_spawns():
    return sorted({_int(r["field_id"]) for r in _rows("npc_generator_pos")})


def items():
    """{item_id: {"name", "slot", "gate1", "gate2", "level", "skills"}}

    `skills` is the item's PREREQUISITE SKILL list -- the up-to-two ids at the
    table record's +0x88, with the 0xffff "none" entries dropped. The client's
    equip validator (0x05076090, arm 0x050761AC) refuses an item with code 5
    unless ONE of them is owned in [unit+0xa14 + id*2]; an item that lists none
    skips the check entirely. These are weapon/armour PROFICIENCIES, one per
    family (436 dagger, 439 axe, 442 bow, 443 wand, 444 shield, 445..447
    armour weights), which is why "grant them all" and "grant none" both look
    like a class problem and neither is.

    `price` and `ring_price` (2026-09-11) are the record's +0x90 (u32) and
    +0x94 (u16), read in the client's own parse order (itemparse.py /
    fefet.item_record). PARTIAL: NAMED BY THEIR SHAPE, not by a client reader:
    +0x90 is monotone in the required level down every family (beginner axe
    30, axe 60, axe+1 240, the Lv5 axe 820, Lv12 1860, Lv35 14780; bread 8,
    apple 20, the three regen potions 12/58/150) -- a shop price list, and
    the only one the data has. +0x94 is ZERO on 365 of 598 rows and 1..20 on
    exactly the "+1" variants, the named higher-grade sets and the jewels --
    what SE's 2006 guide says the per-class RING SHOP sold (higher-grade gear,
    bought with Rings). Absent columns read as 0, which the shop treats as
    "no table price" and falls back to --shop-price rather than stocking an
    empty shop.
    """
    if "items" not in _cache:
        d = {}
        for r in _rows("items"):
            skills = [s for s in (_int(r.get("skill1", ""), 0xFFFF),
                                  _int(r.get("skill2", ""), 0xFFFF))
                      if 0 < s < 0xFFFF]
            d[_int(r["item_id"])] = {"name": r["name_jp"],
                                     "category": r.get("category", "0"),
                                     "slot": _int(r["slot_type"], -1),
                                     "gate1": _int(r.get("stat_gate1", ""), 0),
                                     "gate2": _int(r.get("stat_gate2", ""), 0),
                                     "level": _int(r.get("req_level", ""), 0),
                                     "skills": skills,
                                     "price": _int(r.get("price", ""), 0),
                                     "ring_price": _int(r.get("ring_price", ""), 0),
                                     # the per-SEX model paths: many armour rows
                                     # exist twice with only one filled, and the
                                     # equip validator refuses (code 6) a row whose
                                     # path for the wearer's sex is empty (drops)
                                     "model_m": (r.get("model_m") or "").strip(),
                                     "model_f": (r.get("model_f") or "").strip()}
        _cache["items"] = d
    return _cache["items"]


def item_prices_ship():
    """True when the item table carries the price columns at all -- a TSV
    regenerated by a fefet.py that predates them reads every price as 0, and
    that must look like "no data", not like "everything is free"."""
    return any(it.get("price") for it in items().values())


#: class id -> the weapon/armour proficiency ids FE_CLASS_BASIC_PARAM_DATA
#: gives it (see class_proficiencies). The three RoD classes only.
PLAYER_CLASSES = (0, 1, 2)


def item_classes(item_no):
    """The player classes (0 Warrior, 1 Scout, 2 Sorcerer) whose proficiency
    list names one of this item's prerequisite skills -- the same two tables
    the client's own equip validator crosses (code 5 at 0x050761AC). () for an
    item with no prerequisite: anybody can wear it, so no class OWNS it."""
    need = set(items().get(int(item_no), {}).get("skills", ()))
    if not need:
        return ()
    return tuple(c for c in PLAYER_CLASSES
                 if need & set(class_proficiencies(c)))


def item_skills(item_ids):
    """Every prerequisite skill id the given items name, sorted and unique."""
    tbl = items()
    out = set()
    for no in item_ids:
        out.update(tbl.get(int(no), {}).get("skills", ()))
    return sorted(out)


def starting_gear(class_id, sex=0):
    """The item ids fet_initialize_equip gives class `class_id` (0..6), the
    male list for sex 0 and the female list otherwise. [] for an unknown
    class -- a class with no row gets nothing rather than somebody else's."""
    for r in _rows("initialize_equip"):
        if _int(r["class"]) == int(class_id):
            col = "male_items" if not sex else "female_items"
            return [_int(x) for x in r[col].split() if x]
    return []


def castle(map_no):
    """(grid_x, grid_z) of the castle on Hmap map `map_no`, or None."""
    for r in _rows("castle_info"):
        if _int(r["map"]) == int(map_no):
            return _int(r["grid_x"]), _int(r["grid_z"])
    return None


def npc_stats(type_id):
    """fet_npc_type's per-monster numbers.

    {"hp", "attack", "defence", "level", "reward", "skills", "resists"} --
    or {} for an unknown type, so a caller falls back rather than inventing.

    KEY: 2026-09-09: these now come from a STREAMING parse of the record in
    the client's own read order (fefet.npc_record, off the parser at
    0x051A8878) rather than from fixed offsets into the tail -- and, more to
    the point, from a TSV that no longer loses columns.

    WARNING: THE BUG THIS FIXED: fet_npc_type's `model_file` column is a raw
    slice of NPC_ModelType, and 40 of those slices contain a newline. Written
    into a TSV unescaped, each of those rows split in two and everything after
    `model_file` -- hp, level, reward -- read back EMPTY, so npc_hp fell back
    to 100 and npc_exp to 0. The whole Harpy line, Bone_Hunter and 38 others
    spawned as 100-HP monsters worth nothing to kill. Harpy_Queen is 1,260 HP,
    level 30, 436 EXP.

    WARNING: The offsets were never the problem, and saying so was a wrong
    diagnosis before the two readers were run against each other in memory:
    the old fixed-offset table agreed with this parse on 1,371 of 1,371
    interior rows.

    THE ANCHOR IS THE FIRST KILL. Row 1849 reads hp 2464, exp 823, level 53 --
    the three numbers the 2026-09-09 06:09Z Duke_Orc kill put in the feworld
    log and in fe.db, from a parse that had never seen them.

    ATTACK and DEFENCE are new and are a READING, not a measurement: they are
    the pair the old dump called `p1?`/`p2?`, and they earn the names by
    behaving like them across the whole bestiary -- monotone in level (92 at
    L1 to 816 at L70), equal for most monsters and split for the variants
    (Venomous L3 exists as both 109/109 and 65/163). PARTIAL: No screen has yet
    shown a number computed from either.
    """
    if "npc_stats" not in _cache:
        d = {}
        for r in _rows("npc_type"):
            d[_int(r["type_id"])] = {
                "hp": _int(r.get("hp", ""), 0),
                "attack": _int(r.get("attack", ""), 0),
                "defence": _int(r.get("defence", ""), 0),
                "level": _int(r.get("level", ""), 0),
                "reward": _int(r.get("exp", ""), 0),
                "skills": [_int(x) for x in r.get("skills", "").split() if x],
                "resists": [_int(x) for x in r.get("resists?", "").split() if x],
            }
        _cache["npc_stats"] = d
    return _cache["npc_stats"].get(int(type_id), {})


def _by_modeltype():
    if "by_modeltype" not in _cache:
        d = {}
        for tid, t in sorted(npc_types().items()):
            d.setdefault(t["modeltype"], tid)
        _cache["by_modeltype"] = d
    return _cache["by_modeltype"]


def npc_hp(modeltype, type_id=None):
    """Hit points for a monster, by npc type id when the caller has one and
    by MODELTYPE otherwise.

    WARNING: The modeltype route is lossy on purpose: several npc types share a
    model (that is what a modeltype IS), so it answers with the LOWEST type
    id that uses the model. `--monster 20:...` names a model, not a monster,
    and there is no id in that spelling to be more exact with. 0 when nothing
    matches -- the caller supplies its own default.
    """
    if type_id is not None:
        s = npc_stats(type_id)
        if s.get("hp"):
            return s["hp"]
    tid = _by_modeltype().get(int(modeltype))
    return npc_stats(tid).get("hp", 0) if tid is not None else 0


def npc_exp(modeltype, type_id=None):
    """The per-kill reward column, resolved the same way as npc_hp."""
    return _npc_field("reward", modeltype, type_id)


def _npc_field(key, modeltype, type_id=None):
    """One fet_npc_type column, by type id when the caller has one and by
    MODELTYPE otherwise (the lossy route npc_hp documents). 0 when nothing
    matches, so the caller supplies its own default."""
    if type_id is not None:
        s = npc_stats(type_id)
        if s.get(key):
            return s[key]
    tid = _by_modeltype().get(int(modeltype))
    return npc_stats(tid).get(key, 0) if tid is not None else 0


def npc_attack(modeltype, type_id=None):
    """What this monster hits for, resolved the same way as npc_hp."""
    return _npc_field("attack", modeltype, type_id)


def npc_defence(modeltype, type_id=None):
    """What this monster's defence subtracts from, resolved like npc_hp."""
    return _npc_field("defence", modeltype, type_id)


def npc_level(modeltype, type_id=None):
    """The monster's own level, resolved the same way as npc_hp."""
    return _npc_field("level", modeltype, type_id)


def _f(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


#: EFFECT_DATA bitsA values for what a restore effect touches (the same bits
#: as the 0x2028 regen ask's mask: 0x4 HP, 0x10 Pw).
EFFECT_HP, EFFECT_PW = 0x4, 0x10


def effects():
    """EFFECT_DATA (fedata/fe-fet-effect.tsv, fefet.effect_rows) by id:
    {"name", "total_ms", "tick_ms", "a", "b", "c", "vals", "tier"}. See
    fefet.effect_record for what each field is and how it was read."""
    if "effects" not in _cache:
        d = {}
        for r in _rows("effect"):
            d[_int(r["effect_id"])] = {
                "name": r.get("name", ""),
                "total_ms": _int(r.get("total_ms", "0")),
                "tick_ms": _int(r.get("tick_ms", "0")),
                "a": _int(r.get("bitsA", "0")), "b": _int(r.get("bitsB", "0")),
                "c": _int(r.get("bitsC", "0")),
                "vals": [_f(v) for v in r.get("vals", "").split()],
                "tier": _int(r.get("tier", "0"))}
        _cache["effects"] = d
    return _cache["effects"]


def skill_combat(skill_id):
    """SKILL_DATA's combat fields for one skill (fe-fet-skill_combat.tsv), or
    {} when unknown: range / min_range / radius (world units), windup /
    active / impact / recovery (ms), stun (ms), child (skill id), effects.
    Every field is named by a client reader -- see fefet.skill_combat_rows."""
    if "skill_combat" not in _cache:
        d = {}
        for r in _rows("skill_combat"):
            d[_int(r["skill_id"])] = {
                "kind": _int(r.get("kind", "0")),
                "range": _f(r.get("range")), "min_range": _f(r.get("min_range")),
                "radius": _f(r.get("radius")),
                "windup": _int(r.get("windup_ms", "0")),
                "active": _int(r.get("active_ms", "0")),
                "impact": _int(r.get("impact_ms", "0")),
                "recovery": _int(r.get("recovery_ms", "0")),
                "stun": _int(r.get("stun_ms", "0")),
                "child": _int(r.get("child", "65535"), 0xFFFF),
                "effects": [_int(x) for x in r.get("effects", "").split() if x]}
        _cache["skill_combat"] = d
    return _cache["skill_combat"].get(int(skill_id), {})


def skill_power(skill_id, _depth=0):
    """A skill's POWER in percent, or 0 when it deals no damage.

    KEY: 2026-09-12: SKILL_DATA +0x108 lists EFFECT_DATA ids (traced,
    0x0505F79C -> 0x0506E900); a DAMAGE effect is one with bitsC 0x20 and
    bitsA/bitsB clear, and its vals[0] is the power -- Sonic Boom L1..L5 =
    70/80/90/100/130, Heavy Slash 100..160, every monster basic skill 100,
    Ender Pain (a buff) none. A skill that spawns a child (+0x104, the
    `_ダメージ`/`_爆発` record) carries the power there, so the child is
    followed when the parent has none. The NAMING is DATA-SHAPE (it rises
    with rank in every damage family and is absent on every buff); the
    client's only reader (0x0505F790) sums it for display."""
    c = skill_combat(skill_id)
    for e in c.get("effects", []):
        x = effects().get(e)
        if x and x["c"] & 0x20 and not (x["a"] or x["b"]) and x["vals"]:
            return x["vals"][0]
    child = c.get("child", 0xFFFF)
    if _depth < 3 and child not in (0xFFFF, int(skill_id)):
        return skill_power(child, _depth + 1)
    return 0.0


def item_use(item_id):
    """{"use_skill", "atk", "matk"} for one item (fe-fet-item_use.tsv), or {}.
    use_skill = FE_ITEM_DATA +0x56 (0xFFFF = cannot be used); atk/matk =
    +0x38 / +0x3A, the weapon's physical / magic attack."""
    if "item_use" not in _cache:
        d = {}
        for r in _rows("item_use"):
            d[_int(r["item_id"])] = {"use_skill": _int(r.get("use_skill", "65535"), 0xFFFF),
                                     "atk": _int(r.get("atk", "0")),
                                     "matk": _int(r.get("matk", "0"))}
        _cache["item_use"] = d
    return _cache["item_use"].get(int(item_id), {})


def item_effect(item_id):
    """What USING an item restores: {"stat": "hp"|"pw", "amount", "total_ms",
    "tick_ms", "tier", "effect"} or None.

    item +0x56 (the use skill) -> that skill's +0x108 effects -> the first
    EFFECT_DATA row with a restore stat in bitsA. From the client's own data:
    bread 50, apple 70, cheese/bacon 90, meat pie 120, steak 150 HP at once;
    the three regen potions 25/48/100 HP every 4 s for 32 s; the power pots
    5/10/15 Pw every 4 s for 60 s. Bread 50, steak 150, the regen 25x8 / 48x8
    and the power pots all match fewiki's 2006 table (fv_consum.txt); where
    the wiki differs (cheese 125, apple 75) this client's own data wins."""
    u = item_use(item_id)
    sk = u.get("use_skill", 0xFFFF)
    if sk == 0xFFFF:
        return None
    for e in skill_combat(sk).get("effects", []):
        x = effects().get(e)
        if not x or not x["vals"] or x["vals"][0] <= 0:
            continue
        if x["a"] & (EFFECT_HP | EFFECT_PW):
            return {"stat": "hp" if x["a"] & EFFECT_HP else "pw",
                    "amount": int(round(x["vals"][0])),
                    "total_ms": x["total_ms"], "tick_ms": x["tick_ms"],
                    "tier": x["tier"], "effect": e}
    return None


def armour_defence(item_id):
    """An armour piece's (or shield's) DEFENCE, or None when unknown.

    KEY: 2026-09-12. NOT in the client: FE_ITEM_DATA's armour-only +0x72 (0..6)
    follows the shop TIER, not defence (at +0x72 = 6 the three classes' body
    armour is 28 / 21 / 19). The numbers are fewiki.com's own RoD launch
    tables, `Item/装備品/防具/{頭,鎧,脚,手,足,盾}` (columns 名称/防御/Lv/価格,
    Wayback captures 2006-05-26, unchanged through 2007-02), matched to the
    client's items BY NAME -- fe-armour-defence.tsv, built by
    tools/fe_armour_defence_build.py.
    Players read the same number off the equip window as 能力値."""
    if "armour_def" not in _cache:
        _cache["armour_def"] = {_int(r["item_id"]): _int(r.get("defence", "0"))
                                for r in _rows("armour-defence", prefix="fe-")}
    return _cache["armour_def"].get(int(item_id))


#: Classes whose status "Atk" row shows MAGIC attack (+0x4B0, 0x050E14D9);
#: the rest show physical (+0x4AC, 0x050E14F9).
MAGIC_CLASSES = frozenset((2, 4, 5))


def class_attack(class_id):
    """("phys"|"magic", base attack) for a class, from
    FE_CLASS_BASIC_PARAM_DATA: +0x28 physical / +0x2A magic (base_u16 slots 3
    and 4: 127 for Warrior/Scout/Sorcerer). The class's kind is the status
    window's own rule. ("phys", 0) for an unknown class."""
    kind = "magic" if int(class_id) in MAGIC_CLASSES else "phys"
    for r in _rows("class_param"):
        if _int(r["class_id"]) == int(class_id):
            v = [_int(x) for x in r.get("base_u16", "").split()]
            if len(v) > 4:
                return kind, v[4] if kind == "magic" else v[3]
    return kind, 0


def npc_attack_skill(type_id, slot=0):
    """A monster's attack skill timing from its own SKILL_DATA record:
    {"skill", "range", "min_range", "period", "impact"} (seconds / units), or
    {} when the type or the skill is unknown.

    fet_npc_type lists four skills per monster (slot 0 = 直接攻撃, the melee
    swing; 1 magic, 2 sonic, 3 bow). The client's own building auto-attack
    (0x05070C50) fires a skill only when min_range <= distance <= range and
    re-fires after windup + active; the client's busy-until for a skill is
    windup + active + recovery (0x0505F500) -- the swing PERIOD used here."""
    sk = (npc_stats(type_id).get("skills") or [])
    if not 0 <= slot < len(sk):
        return {}
    c = skill_combat(sk[slot])
    if not c:
        return {}
    return {"skill": sk[slot], "range": c["range"], "min_range": c["min_range"],
            "period": (c["windup"] + c["active"] + c["recovery"]) / 1000.0,
            "impact": (c["windup"] + c["impact"]) / 1000.0,
            "power": skill_power(sk[slot])}


def lv_diff_correction(gap):
    """fet_npc_lv_diff_correction's row for `gap` = MONSTER level - PLAYER
    level: (f0, f1, f2, f3), or None when the table has no rows.

    17 rows of [i16 gap][i16 marks][f32 x4] (parser 0x051A7748, sizeof 0x14),
    filed in a std::map at 0x535CC80 keyed by the FIRST i16 (signed compare).

    KEY: 2026-09-12, STATIC -- the one client consumer, 0x050DFEB0, runs only
    when the target is a monster (kind 0xBBC) and computes
        gap = [target+0x3ac] (the monster's level)
            - [player+0x9f0+[player+0x3ab]] (the level of the CURRENT class)
    then takes the row whose key == gap, else CLAMPS: gap <= the lowest key
    -> the lowest row, gap >= the highest key -> the highest row. This
    reproduces that exactly. (The old reading -- "attacker - target", the
    second column an upper bound, (-1,-1) a catch-all -- was a guess; the
    second column is the number of DANGER MARKS the target display draws,
    -1..3 -> 0..4 via the jump table at 0x050DFE88, and it is the only part
    of the row the client ever reads.)

    The four floats are SERVER data the client ships and never reads. Only
    f0 has a meaning with evidence behind it: YOUR DAMAGE to the monster --
    the only column that falls monotonically to ~0 (1.4 at -1 .. 0.05 at
    +15), and ffsky (RoD, 2007) says of the level correction 「于比自己等级高的
    给于的伤害极少，而被攻击受到的损伤则更高」 (to monsters above you, damage you
    deal is tiny and damage you take is higher); fewiki Q&A: 「対mob狩りの場合は
    レベル補正があり、ダメージが通りません」. Damage TAKEN is f2 (0.4..4) or f3
    (0.7..5) -- both rise, and nothing yet says which. f1 is unknown.
    """
    rows = {}
    for r in _rows("lv_diff"):
        try:
            rows[_int(r["lo"])] = tuple(float(r["f%d?" % i]) for i in range(4))
        except (KeyError, TypeError, ValueError):
            continue
    if not rows:
        return None
    gap = int(gap)
    if gap in rows:
        return rows[gap]
    lo, hi = min(rows), max(rows)
    if gap <= lo:
        return rows[lo]
    if gap >= hi:
        return rows[hi]
    return None                       # a hole in the keys: the client finds no row


def areas():
    """fet_area -- the continent map, 95 rows keyed by area (= group) id:
    {"name", "map", "nation", "capital", "island", "flags", "hmap",
     "map_x", "map_y", "neighbours"}.

    dat.pak has shipped the field NAMES, the map pixel each field draws at,
    the owning NATION and the ADJACENCY graph all along; feworld's
    --field-names / --field-coords / --field-nations have been hand-authored
    beside them. `capital` is the 500 marker: it is true for exactly
    21/39/57/62/78 and their 91..95 twins, which is exactly the set of areas
    the spawn table gives no monsters.

    2026-09-09: three more columns, after fefet re-read the table off the
    CLIENT's parser (0x0519b308) instead of off the bytes.

    * `island` 1..6 -- THE STRUCTURE OF THE GAME. 1 is a shared frontier
      continent (areas 1..15, exactly three fields per nation, area 5 the
      nine-neighbour hub); 2..6 are one home continent per nation, 16..30,
      31..45, 46..60, 61..75, 76..90, each carrying its capital and that
      capital's inner half (91..95). feworld had been inventing five islands
      of one field each.
    * `flags` -- 0 on a war field, 15 on an outer capital, 79 on an inner
      half. 15 carries bit 3, which is the client's CAPITAL bit at
      [group+0x8c]. PARTIAL: that the column IS that record field is a reading; what
      is measured is that the bit and the value agree on which areas are
      capitals.
    * `hmap` -- Data\\Hmap\\mapNN.pak, the terrain the war is fought on and a
      NULL DEREF in the client if it is not 1..14. Confirmed against the
      install tree, which ships exactly map01..map14. WARNING: map02 is used by no
      area at all.
    """
    if "areas" not in _cache:
        d = {}
        for r in _rows("area"):
            d[_int(r["area_id"])] = {
                "name": r.get("name_jp", ""),
                "map": _int(r.get("map_id", "")),
                "nation": _int(r.get("nation", "")),
                "capital": _int(r.get("is_capital_500", "")) == 500,
                "island": _int(r.get("island", "")),
                "flags": _int(r.get("flags", "")),
                "hmap": _int(r.get("hmap", "")),
                "map_x": _int(r.get("map_x", "")),
                "map_y": _int(r.get("map_y", "")),
                "neighbours": [_int(x) for x in r.get("neighbours", "").split() if x],
            }
        _cache["areas"] = d
    return _cache["areas"]


#: Capital -> the two script-id bands that hold its NPC roster, in capital
#: order (21 Beinwatt, 39 Azurwood, 57 Liberberg, 62 Nutsberry, 78 Runewall).
#: 15 roles in the 2x00 band and 5 in the 3x00 band = 20 per capital, the same
#: structure in all five.
CAPITAL_SCRIPT_BANDS = {21: (2100, 3100), 39: (2300, 3300), 57: (2500, 3500),
                        62: (2700, 3700), 78: (2900, 3900)}
#: An inner half carries its outer half's roster -- the shops hang off the
#: INNER map's doors (proved live 2026-09-06), so that is where the keepers
#: most likely stood. PARTIAL: WHICH HALF each role belongs to is a reading; the
#: table says only that the capital has twenty.
_CAPITAL_PAIR = {91: 21, 92: 39, 93: 57, 94: 62, 95: 78}

#: Which district each capital half is. The client does not name them; the
#: names are the old-capital districts from atwiki 10000goku/243 (early FEZ,
#: pre-2008 capitals), and which half is which was worked out from the maps
#: -- see tools/fe_townsfolk_build.py.
CAPITAL_DISTRICTS = {
    21: ("Inner", "内部"), 91: ("Outer", "外部"),
    39: ("Forest Ward", "森の区"), 92: ("Wall Ward", "壁の区"),
    57: ("Residential", "居住区"), 93: ("Commercial", "商業区"),
    62: ("West", "西部"), 94: ("East", "東部"),
    78: ("Nobles' Quarter", "貴族街"), 95: ("Slums", "貧民街"),
}


def capital_district(area_id):
    """The district name of a capital half ("Wall Ward"), or ""."""
    return CAPITAL_DISTRICTS.get(int(area_id), ("", ""))[0]


def townsfolk(area_id=None, capital=False):
    """The capitals' TOWNSFOLK -- the second NPC band (script 1xx/3xx/5xx/7xx/
    9xx) the 20-role roster does not cover: named villagers, gatekeepers, the
    free-weapon men, Azurwood's castle guard Lionel.

    Each row: script, type_id, name, modeltype, capital, area (the half they
    live in), district, kind, x/z/yaw (a CHOSEN spot on that half's floor),
    line_en (OUR translation), line_ja (SE's line as transcribed), source.
    `area_id` filters to one half; with `capital=True`, to both halves of
    that half's capital. Built by tools/fe_townsfolk_build.py.
    """
    out = []
    for r in _rows("townsfolk", prefix="fe-"):
        if not r.get("script", "").isdigit():
            continue
        f = lambda k: float(r[k]) if r.get(k) not in (None, "") else None
        out.append({"script": _int(r["script"]), "type_id": _int(r["type_id"]),
                    "name": r["name"], "modeltype": _int(r["modeltype"]),
                    "capital": _int(r["capital"]), "area": _int(r["area"]),
                    "district": r["district"], "kind": r["kind"],
                    "x": f("x"), "z": f("z"), "yaw": f("yaw"),
                    "line_en": r["line_en"], "line_ja": r["line_ja"],
                    "source": r["source"]})
    if area_id is None:
        return out
    a = int(area_id)
    if capital:
        cap = _CAPITAL_PAIR.get(a, a)
        return [t for t in out if t["capital"] == cap]
    return [t for t in out if t["area"] == a]


def townsfolk_by_script(script):
    try:
        s = int(script or 0)
    except (TypeError, ValueError):
        return None
    return next((t for t in townsfolk() if t["script"] == s), None)


def roster_line(offset):
    """A 20-role roster slot's line where the wiki has one: (en, ja) or None."""
    for r in _rows("townsfolk", prefix="fe-"):
        if r.get("script") == "roster:%d" % int(offset):
            return r["line_en"], r["line_ja"]
    return None


def capital_roster(area_id):
    """The 20 roles a capital is supposed to have: [{script, type_id, name,
    modeltype}], in script order. [] for anything that is not a capital.

    KEY: WHO should be standing there ships, keyed by script id, identical in
    all five capitals; positions did not (SE server data).
    WARNING: NOT the whole population: a second band (script 1xx/3xx/5xx/7xx/9xx,
    ~40 townsfolk per capital incl. the castle guard Lionel) is townsfolk()
    -- 09-11, see tools/fe_townsfolk_build.py.
    """
    a = int(area_id)
    a = _CAPITAL_PAIR.get(a, a)
    bands = CAPITAL_SCRIPT_BANDS.get(a)
    if not bands:
        return []
    out = []
    for r in _rows("npc_type"):
        s = r.get("script_id?", "")
        if not s.isdigit():
            continue
        s = int(s)
        if any(b <= s < b + 100 for b in bands):
            out.append({"script": s, "type_id": _int(r["type_id"]),
                        "name": r["name"], "modeltype": _int(r["modeltype"])})
    return sorted(out, key=lambda r: r["script"])


def area_expected(area_id):
    """What SHIPS for this area, as a checklist a tool can diff against what
    has actually been placed.

    Capitals get a 20-role roster and the shipped door graph; war fields get
    their monster generator points (which DO carry positions) and their battle
    map's castle. Nothing here is a spawn point -- no player-spawn data ships
    at all, which is exactly why it is worth saying what DOES.
    """
    a = int(area_id)
    row = areas().get(a)
    if not row:
        return None
    out = {"area": a, "name": row["name"], "hmap": row["hmap"],
           "capital": bool(row["capital"]), "roster": [], "doors": [],
           "monsters": [], "castle": None,
           "ground": area_ground_height(a)}
    if row["capital"]:
        out["roster"] = capital_roster(a)
        out["doors"] = area_portals(a)
    else:
        out["monsters"] = spawn_points(a)
        out["castle"] = castle(row["hmap"])
    return out


#: The minimap pixel <-> world mapping, fitted against the shipped door
#: positions by tools/fedatagen/feicons.py (worst error 2.2 units in x,
#: 4.7 in z across six door pairs from four capitals). Kept here as well so
#: the panel can draw a marker on the art without importing the generator.
MINIMAP_AX, MINIMAP_CX = 2.4646, 127.10
MINIMAP_AZ, MINIMAP_CZ = -2.4625, 127.45
MINIMAP_SIZE = 256

#: capital area -> the minimap that IS that half, from the client's table at
#: 0x052D1858. Getting this pairing off by one row once put a player in a
#: different city, falling.
MINIMAP_FILE = {21: "map00_00", 91: "map00_01", 39: "map01_00",
                92: "map01_01", 57: "map02_01", 93: "map02_00",
                62: "map03_00", 94: "map03_01", 78: "map04_00",
                95: "map04_01"}


def minimap_name(area_id):
    return MINIMAP_FILE.get(int(area_id))


#: Where the painted grid labels sit on every capital minimap, measured off
#: the art 2026-09-11 (identical on map00_01, map01_01 and map03_00): row
#: letters A..H centred at y = 25 + 32*r, column digits 1..8 at
#: x = 6.5 + 32*(k-1). Column 1's digit is drawn squeezed beside the "H"; its
#: slot on the 32 px pitch is 6.5.
GRID_X0, GRID_Y0, GRID_PITCH = 6.5, 25.0, 32.0


def minimap_grid(px, py):
    """The nearest painted grid label to a minimap pixel, as "E5".

    This is the NEAREST LABEL, not a proven cell: the cell boundaries were not
    measured (terrain drowns the grid lines), and nothing here needs them. It
    is how a person reads the map -- "the doors at E5" -- and that is all it is
    for. A point near a boundary can honestly go either way.
    """
    col = int(round((float(px) - GRID_X0) / GRID_PITCH)) + 1
    row = int(round((float(py) - GRID_Y0) / GRID_PITCH))
    col = min(8, max(1, col))
    row = min(7, max(0, row))
    return "%s%d" % ("ABCDEFGH"[row], col)


def _ground_grid(stem):
    """The baked 1-unit ground grid for one capital map, decoded once."""
    key = "ground:" + str(stem)
    if key not in _cache:
        import base64
        import json
        import zlib
        if "ground_json" not in _cache:
            try:
                with open(os.path.join(_DIR, "fe-capital-ground.json"),
                          encoding="utf-8") as fh:
                    _cache["ground_json"] = json.load(fh)
            except (OSError, ValueError):
                _cache["ground_json"] = {}
        g = _cache["ground_json"].get(str(stem))
        if g is None:
            _cache[key] = None
        else:
            raw = zlib.decompress(base64.b64decode(g["data"]))
            _cache[key] = (g["x0"], g["z0"], g["nx"], g["nz"],
                           struct.unpack("<%dh" % (g["nx"] * g["nz"]), raw))
    return _cache[key]


def capital_ground(area_or_stem, x, z):
    """The ground height a capital's own COLLISION gives at (x, z), or None.

    WARNING: THIS IS THE QUESTION THAT DECIDES WHETHER A PLAYER CAN LAND. On every
    area entry the client stores y = 10000.0 over whatever height we sent
    (0x1027 handler, 0x05054dcb), then mode 0xb substate 0 asks its terrain
    manager for the ground under (x, z) (0x05000450 from 0x04ffa0f4). When that
    finds nothing, the height write at 0x04ffa10b is SKIPPED and the player
    hangs at 10000 over empty space -- which is every capital door until
    2026-09-11, because the server was handing out arrivals that lie outside
    the destination map's collision.

    Answered from fe-capital-ground.json: the game's own DATA/capital/*_hit.oct
    baked to a 1-unit grid by tools/fedatagen/feoct.py, sampled by the same
    downward ray. Validated against a measured point: the client reported the
    player standing at y = 35.0 at (-4.5, -167) in area 91, and the grid says
    35.0. Returns None off the grid or where the cell has no ground; a point
    within a unit of an edge can go either way.
    """
    stem = area_or_stem
    if not isinstance(stem, str):
        stem = minimap_name(stem)
    g = _ground_grid(stem) if stem else None
    if g is None:
        return None
    x0, z0, nx, nz, cells = g
    ix, iz = int((float(x) - x0) // 1.0), int((float(z) - z0) // 1.0)
    if not (0 <= ix < nx and 0 <= iz < nz):
        return None
    v = cells[iz * nx + ix]
    return None if v == -32768 else v / 10.0


def minimap_anchors(stem=None):
    """Default world->pixel anchors per map half, from fe-minimap-cal.tsv.

    KEY: THE SHIPPED MINIMAP_A*/C* ARE ONE FIT AND THERE ARE TWO SCALES. Held
    against the shipped portal table, the painted doors on map00 and map02 span
    ~1.45x more art than those constants project them to: those halves are
    drawn at ~1.7 world units per pixel, while map01/03/04 really are at
    ~2.46. One constant could not be right for both, which is why four halves
    were 15-33 px out.

    Each row here is a MEASUREMENT: a door whose world position the portal
    table gives, matched to the pixel SE painted it at, recovered by RANSAC
    over every possible pairing (tools/fedatagen/femapfit.py) and kept only when at
    least two painted doors land on shipped doors under the same projection.
    They are a DEFAULT: feworld's own store overrides them per half, so a
    `!mapcal here` from somebody standing in the town always wins.
    """
    if "minimap_cal" not in _cache:
        out = {}
        for r in _rows("minimap-cal", prefix="fe-"):
            try:
                out.setdefault(r["stem"], []).append(
                    {"x": float(r["x"]), "z": float(r["z"]),
                     "px": float(r["px"]), "py": float(r["py"]),
                     "note": r.get("note", "")})
            except (KeyError, TypeError, ValueError):
                continue
        _cache["minimap_cal"] = out
    cal = _cache["minimap_cal"]
    if stem is None:
        return cal
    return list(cal.get(str(stem), []))


def world_to_pixel(x, z):
    """World (x, z) -> minimap pixel (px, py). The inverse of feicons.world."""
    return (x / MINIMAP_AX + MINIMAP_CX, z / MINIMAP_AZ + MINIMAP_CZ)


def shop_icons(area_id=None, trusted_only=True):
    """Shop positions recovered from the capital minimap art.

    See tools/fedatagen/feicons.py for how and why this exists: the client
    does not draw these icons from entity data, it has them PAINTED INTO the
    minimap where SE put each shop, so a pixel->world mapping reads the
    positions back. Accurate to a couple of metres.

    `trusted_only` drops every half whose classifier produced a duplicated role
    -- a capital has exactly one Warrior_Weapon_Shop, so two means scenery
    landed in the same bucket as a shop and there is no way to know which row
    is which. Those halves are a lead, not data.
    """
    if "shop_icons" not in _cache:
        out = []
        for r in _rows("shop-icons", prefix="fe-"):
            try:
                # KEY: px/py ARE THE MEASUREMENT; x/z are derived from them
                # through the shipped fit. Anything that re-projects x/z back
                # to a pixel is only correct while the fit is unchanged -- and
                # the panel now calibrates per map, so the pixel has to travel
                # with the row or every icon slides off the art it was read
                # from the moment a half is aligned.
                out.append({"area": _int(r["area"]), "kind": r["kind"],
                            "colour": r["colour"], "role": r["role"],
                            "x": float(r["x"]), "z": float(r["z"]),
                            "px": float(r["px"]), "py": float(r["py"]),
                            "trust": r.get("trust", "noisy")})
            except (KeyError, TypeError, ValueError):
                continue
        _cache["shop_icons"] = out
    rows = _cache["shop_icons"]
    if trusted_only:
        rows = [r for r in rows if r["trust"] != "noisy"]
    if area_id is not None:
        rows = [r for r in rows if r["area"] == int(area_id)]
    return rows


def model_speeds():
    """modeltype -> (walk, run) in world units per second, off NPC_ModelType.

    KEY: THE SHIPPED MONSTER SPEED (2026-09-12). `--monster-speed`'s help said
    "fet_npc_type ships no speed column", which is true -- the speeds live in
    the MODEL table. NPC_ModelType's parser (0x051aafa8, sizeof 0x424) reads
    an id, four strings, then eight dwords, the last two landing at +0x41c and
    +0x420; it inserts the row into the map at 0x535cce0, keyed on the id. The
    monster move reads that same map by the unit's [+0x9fa] -- the modeltype
    the type-8 ctor stores at 0x05068ff8 -- and compares its speed against
    ([+0x41c] + [+0x420]) * 0.5 to choose walk (motion 5) or run (motion 8),
    then plays the motion at speed / that speed, CLAMPED to 0.4..2.0
    (0x04feac0a / 0x04feac25). So a server speed above a model's run speed
    slides the body faster than its legs can go -- the 09-12 "he moving very
    fast" at a flat 10 u/s, 2.7x a Duke_Orc's 3.7.

    fedata/fe-npc-model-speeds.tsv was read straight out of dat.pak in the
    client's own record order; the same walk reproduces the six dwords
    fenpc.py already dumped on all 271 rows, and walk <= run holds on all 271,
    which is what says the two floats are what they are named.
    """
    if "model_speeds" not in _cache:
        d = {}
        for r in _rows("npc-model-speeds", prefix="fe-"):
            try:
                d[_int(r["modeltype"])] = (float(r["walk_ups"]),
                                           float(r["run_ups"]))
            except (KeyError, TypeError, ValueError):
                continue
        _cache["model_speeds"] = d
    return _cache["model_speeds"]


def model_speed(modeltype):
    """(walk, run) u/s for `modeltype`, or None when the model has no row."""
    try:
        return model_speeds().get(int(modeltype))
    except (TypeError, ValueError):
        return None


def map_heights():
    """hmap number -> {"median", "min", "max", "samples"} ground height.

    KEY: THE ONLY SHIPPED PER-MAP HEIGHT WE HAVE. dat.pak has no player-spawn
    table and no terrain we can read, but `DATA/Hmap/MapSpotNN.cfg` -- which
    decrypts to ambient SOUND placement and nothing else -- puts every emitter
    at a real world `@POS x y z`, and the y is a height in the same space the
    player walks in. Twelve of the fourteen battle maps carry samples.

    What it is for: a FALLBACK, so an area with no `!spawn` row lands somewhere
    inside its own map instead of at height zero. Medians run 8 (map09) to 62
    (map05), and the LOWEST sample anywhere is 8 -- so y=0, which is what
    --spawn-pos gives unset, is under the terrain on every map that ships data.

    WARNING: A sound emitter is not standable ground. It is inside the map's height
    range, which is the whole claim; a real spawn still comes from standing
    somewhere and saying `!spawn`. Maps 7 and 10 ship no samples at all.
    """
    if "map_heights" not in _cache:
        d = {}
        for r in _rows("map-heights", prefix="fe-"):
            try:
                d[_int(r["hmap"])] = {
                    "median": _int(r["y_median"]), "min": _int(r["y_min"]),
                    "max": _int(r["y_max"]), "samples": _int(r["samples"])}
            except (KeyError, TypeError, ValueError):
                continue
        _cache["map_heights"] = {k: v for k, v in d.items() if v["samples"]}
    return _cache["map_heights"]


def area_ground_height(area_id):
    """A plausible height for `area_id`, off its battle map. None if unknown."""
    row = areas().get(int(area_id))
    if not row:
        return None
    h = map_heights().get(row.get("hmap"))
    return h["median"] if h else None


def areas_on_island(island):
    """Every area id on `island`, in id order. [] for an unknown island.

    The 0x3031 answer for an island is built from this, so it is also what
    decides the record COUNT in that message's header -- one list, read once.
    """
    return sorted(a for a, r in areas().items() if r["island"] == int(island))


def island_ids():
    """Every island the shipped table knows, in order. 1..6."""
    return sorted({r["island"] for r in areas().values() if r["island"]})


def fame_ranks():
    """FE's rank ladder: [(rank, score_threshold, name)] ascending.

    dat.pak's fet_fame_rank, and its names ship in ENGLISH even in the
    Japanese build: Beginner / Apprentice / Followership / Chaser /
    ChiefChaser / Attacker / ChiefAttacker / Guardian / ChiefGuardian /
    GrandGuardian / Commander, at 0 / 10k / 50k / 110k / 225k / 495k / 1.195M
    / 2.885M / 4M / 6M / 12M total score.
    """
    if "fame_ranks" not in _cache:
        rows = []
        for r in _rows("fame_rank"):
            try:
                rows.append((_int(r["rank"]), _int(r["score_threshold"]),
                             r["name"]))
            except (KeyError, TypeError, ValueError):
                continue
        _cache["fame_ranks"] = sorted(rows, key=lambda t: t[1])
    return _cache["fame_ranks"]


def fame_rank(score):
    """(rank, name, next_threshold) for a total score; next is None at the top.

    WARNING: THAT THE CLIENT DRAWS THIS FROM [unit+0x500] IS NOT PROVED. What is
    known: the ladder is keyed by a score, and +0x500 is the TOTAL SCORE the
    Status window draws. Serving a score and seeing which title appears is a
    one-look test -- until then this is only how the ladder reads.
    """
    rows = fame_ranks()
    if not rows:
        return (0, "", None)
    cur = rows[0]
    nxt = None
    for i, row in enumerate(rows):
        if int(score) >= row[1]:
            cur = row
            nxt = rows[i + 1][1] if i + 1 < len(rows) else None
        else:
            break
    return (cur[0], cur[2], nxt)


def area_names_en(_cache_key="area_en"):
    """{area_id: English name} from fedata/fe-area-names-en.tsv.

    WARNING: OUR TRANSLATIONS, NOT SE's. dat.pak ships all 95 names in Japanese and
    ships no English at all -- the string harvest finds 7 of the 95 and pairs
    them with the wrong English, so there is nothing to recover. See the
    header of that file for the suffix glossary a correction should follow.

    {} when the file is missing, which makes --field-names dat-en fall back to
    "Field N" exactly as it did before this existed.
    """
    if _cache_key in _cache:
        return _cache[_cache_key]
    d = {}
    path = os.path.join(_DIR, "fe-area-names-en.tsv")
    try:
        with io.open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) < 2 or not parts[0].strip().isdigit():
                    continue
                d[int(parts[0])] = parts[1].strip()
    except OSError:
        pass
    _cache[_cache_key] = d
    return d


def area_portals(area_id):
    """Every shipped door in one capital half.

    [{"portal", "area", "gate": (x, z), "radius", "spawn": (x, z),
      "face": (x, z), "dest"}] -- `dest` is another PORTAL id, and that
    portal's `area` is where the door comes out.

    KEY: 2026-09-09: the frame is settled, and it is settled STATICALLY, off the
    client's own parser for this table (0x0519D85A) rather than off a live
    door touch. THERE IS NO THIRD COORDINATE: each position is a two-float
    (x, z) pair, and the float that used to be read as the third coordinate
    is the door's RADIUS (3.0 or 3.5). See fefet.area_portal_rows for the
    retraction and the 64/64 reciprocity check that stands behind it.
    """
    out = []
    for r in _rows("area_portal"):
        if _int(r["area_id"]) != int(area_id):
            continue
        try:
            out.append({
                "portal": _int(r["portal_id"]),
                "area": _int(r["area_id"]),
                "gate": (float(r["gate_x"]), float(r["gate_z"])),
                "radius": float(r["radius"]),
                "spawn": (float(r["spawn_x"]), float(r["spawn_z"])),
                "face": (float(r["face_x"]), float(r["face_z"])),
                "dest": _int(r["dest_portal"]),
            })
        except (KeyError, TypeError, ValueError):
            # An old-format TSV (the pre-2026-09-09 columns) reads as no
            # doors at all rather than as doors in the wrong place.
            continue
    return out


def portal(portal_id):
    """One portal by id, from any area, or None."""
    for a in areas():
        for p in area_portals(a):
            if p["portal"] == int(portal_id):
                return p
    return None


def class_start_skills(class_id):
    """FE_CLASS_BASIC_PARAM_DATA's +0x70 array for one class.

    It is NOT proficiencies alone: Sorcerer's row is 443, 445, 270, 275 and
    270/275 are two of the skills the live character already owns, so the
    array is "what this class starts knowing". Warrior gets 0 (基本攻撃, the
    basic attack) and 5; Scout gets 130/135/140.
    """
    for r in _rows("class_param"):
        if _int(r["class_id"]) == int(class_id):
            return [_int(x) for x in r.get("proficiency_skills", "").split() if x]
    return []


def class_proficiencies(class_id):
    """The weapon/armour proficiency skill ids FE_CLASS_BASIC_PARAM_DATA gives
    a class. Warrior (0) -> 437..441, 444, 447; Scout (1) -> 442, 436, 446;
    Sorcerer (2) -> 443, 445. Classes 3..6 list none of their own.

    This is the same id space the item table's +0x88 prerequisite names, so
    the two tables agree from opposite directions -- which is the check.

    WARNING: The shipped array also carries ORDINARY skill ids (Sorcerer's row ends
    270, 275). The 436..447 filter is what keeps this function meaning what
    its name says; class_start_skills() returns the whole array.
    """
    return [s for s in class_start_skills(class_id) if 436 <= s <= 447]


def skills():
    """SKILL_DATA keyed by id: the four numbers that decide whether a skill can
    be used, read off the CLIENT's own parser (0x051b10c8) rather than off a
    guessed file/struct skew.

        state   [skill+0xd0]   required [unit+0x2b4] bits  -> gate 3
        weapon  [skill+0x12c]  required item CATEGORY bits -> gate 4
        pow     [skill+0xf4]   vs [unit+0x4a4]             -> gate 5
        crystal [skill+0xd4]   vs [unit+0x8ac]             -> gate 6
        sel12e / sel1d8        the palette Select's own two gates
        sp      [skill+0xf0]   the SKILL POINTS a GET!/LVUP of this rank
                               costs -- the skill window greys the button when
                               it exceeds [unit+0x498] (0x050D3A6A / 0x050D3B70).
                               Read as a u16 by the parser (0x051B131D); 2 for
                               every learnable rank 1..5, 100 on the 436..447
                               proficiencies (never learned), 0 on monster
                               skills. Column added 2026-09-11 off
                               tools/fedatagen/fefet.py's skill_record()["xf0"].
    """
    if "skills" not in _cache:
        d = {}
        for r in _rows("skill"):
            d[_int(r["skill_id"])] = {
                "name": r.get("name_jp", ""),
                "family": _int(r.get("family", "")),
                "rank": _int(r.get("rank", "")),
                "state": _int(r.get("state_mask", "0")),
                "weapon": _int(r.get("weapon_mask", "0")),
                "pow": _int(r.get("pow_cost", "0")),
                "crystal": _int(r.get("crystal_cost", "0")),
                "sel12e": _int(r.get("sel_12e", "0")),
                "sel1d8": _int(r.get("sel_1d8", "0")),
                "sp": _int(r.get("sp_cost", "0")),
            }
        _cache["skills"] = d
    return _cache["skills"]


def item_category(item_no):
    """[itemtable+0x1cc] -- the weapon-family bits gate 4 matches a skill's
    [skill+0x12c] against. Slot type alone cannot answer this: slot 10 holds
    staffs (0x200), great axes, hammers and daggers, and only this field tells
    them apart."""
    return _int(items().get(int(item_no), {}).get("category", "0"), 0)


#: [item table +0x50] slot type -> the worn INDEX Equip picks for it
#: (0x0507b920's jump table, read 2026-09-06). Types 1, 9 and 10 are
#: two-handed: Equip writes the uid into worn 0 AND worn 1.
def worn_index_for(slot_type):
    t = int(slot_type)
    if t in (1, 9, 10):
        return 0
    if 2 <= t <= 7:
        return t - 1
    if t == 8:
        return 7          # first free of 7..10; 7 is the best guess a table can make
    if t == 11:
        return 11         # first free of 11..12
    return None


def cast_report(worn_item_numbers, skill_ids, unit_state=0, pow_cur=None,
                crystal=None):
    """Why each skill would or would not cast, from the shipped tables.

    `worn_item_numbers` is {worn index: item number} for the unit's 13-slot
    array. GATE 4 LOOKS AT WORN INDEX 0 AND 1 ONLY (0x0504b7d0's loop is
    `cmp esi,1 / jle`), which is the single most surprising thing here: a wand
    in the right hand satisfies it and the same wand anywhere else does not.

    Returns [(skill_id, name, ok, reason)].
    """
    have = 0
    for idx in (0, 1):
        no = worn_item_numbers.get(idx)
        if no:
            have |= item_category(no)
    out = []
    for sid in skill_ids:
        s = skills().get(int(sid))
        if not s:
            out.append((sid, "?", False, "not in SKILL_DATA"))
            continue
        why = None
        if s["state"] and not (s["state"] & int(unit_state)):
            why = ("gate 3 'Can't use a skill right now.' -- needs "
                   "[unit+0x2b4] & 0x%08X, have 0x%08X (--unit-state)"
                   % (s["state"], int(unit_state)))
        elif s["weapon"] and not (s["weapon"] & have):
            why = ("gate 4 'Skill conditions not met.' -- needs an item in "
                   "worn slot 0 or 1 with category & 0x%04X, worn 0/1 give "
                   "0x%04X" % (s["weapon"], have))
        elif pow_cur is not None and s["pow"] > int(pow_cur):
            why = ("gate 5 'Not enough Pow' -- costs %d, have %d"
                   % (s["pow"], pow_cur))
        elif crystal is not None and s["crystal"] > int(crystal):
            why = ("gate 6 'Not enough crystals' -- costs %d, have %d"
                   % (s["crystal"], crystal))
        out.append((sid, s["name"], why is None,
                    why or "castable by the shipped tables"))
    return out


def town_npc_types():
    """The named town NPCs (modeltype 228..274): [(type_id, name, modeltype, script)]"""
    return sorted((k, v["name"], v["modeltype"], v["script"])
                  for k, v in npc_types().items() if 228 <= v["modeltype"] <= 274)


if __name__ == "__main__":
    import sys
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    print("tables under", _DIR)
    print("fields with spawns:", fields_with_spawns())
    for fid in (1, 2, 3):
        pts = spawn_points(fid)
        print("field %d: %d spawn points, e.g. %s" % (fid, len(pts), pts[:3]))
    print("starting gear class 0 male:", [(i, items().get(i)) for i in starting_gear(0)])
    print("starting gear class 2 female:", [(i, items().get(i)) for i in starting_gear(2, 1)])
    print("castle on map 1:", castle(1))
    print("town NPC types:", town_npc_types()[:12])
