"""Damage numbers both ways, resistance, item use effects."""
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import death, equipment, progression, sess, skilllist, staff

#: The weakest monster in the shipped bestiary, used to normalise the ATTACK
#: column into damage. Level 1 Venomous: 124 hp, attack 92.
_MOB_ATTACK_REF = 92


def player_attack(args):
    """(kind, class base, weapon, total) -- the player's ATTACK, off the
    client's own tables (2026-09-12):

      * the class base: FE_CLASS_BASIC_PARAM_DATA +0x28 physical / +0x2A
        magic (127 for Warrior/Scout/Sorcerer), copied onto the unit at
        +0x4AC / +0x4B0 by 0x0506A5E0; which one a class uses is the status
        window's own rule (classes 2/4/5 show magic, 0x050E14D9).
      * the weapon: FE_ITEM_DATA +0x38 physical / +0x3A magic of whatever is
        worn in the hands (worn indices 0/1, the same two cast gate 4 reads)
        -- beginner axe 56, beginner wand 45.
    WARNING: The SUM is ours: the client adds equipment into the paired bonus slot
    (+0x4AE / +0x4B2) but computes no damage, so how SE's server combined
    them is not in the client."""
    cls = skilllist.self_class_id(args)
    kind, base = fegamedata.class_attack(cls if cls is not None else 0)
    weapon = 0
    try:
        rows = equipment.stored_equip_rows(args)
    except Exception:                                  # noqa: BLE001
        rows = []
    for _uid, no, _slot, worn, _ct in rows:
        if worn in (0, 1):
            u = fegamedata.item_use(no)
            weapon = max(weapon, int(u.get("matk" if kind == "magic" else "atk", 0)))
    return kind, base, weapon, base + weapon


def player_hit_damage(args, m, level=None, skill=None, attack=None):
    """What ONE of the player's hits takes off monster `m`, and why.

    KEY: 2026-09-12, two parts, both off the client's own data:

    1. THE HIT (--player-damage table): the player's ATTACK (player_attack:
       class base + worn weapon) x the skill's POWER % (fegamedata.
       skill_power: Sonic Boom L1..L5 = 70..130, the basic attack 100). A
       skill with no damage effect, or no attack at all, falls back to the
       flat --hit-damage. WARNING: Monster DEFENCE is NOT applied: fet_npc_type
       ships a defence column but no era documents how it entered (FEZ's
       (1 - res/400) is a PLAYER resistance capped at 400; monster defence
       runs past 800), so using it would be a guess.
    2. THE LEVEL GAP: fet_npc_lv_diff_correction's f0 for (monster level -
       the player's current-class level), the row picked the way the client's
       own consumer 0x050DFEB0 picks it (fegamedata.lv_diff_correction). Even
       levels are 1.4, monster +3 is 1.0, +15 and above 0.05. Live-test report
       2026-09-12: "my level 2 scout was doing considerable damage to a level
       27 monster".

    --player-damage flat / --mob-level-correction off turn each off."""
    flat = max(1, int(getattr(args, "hit_damage", 25)))
    base, why = float(flat), "flat --hit-damage %d" % flat
    if getattr(args, "player_damage", "table") == "table" and skill is not None:
        power = fegamedata.skill_power(skill)
        if attack is None:
            _kind, cbase, wpn, attack = player_attack(args)
            parts = "class %d + weapon %d" % (cbase, wpn)
        else:
            parts = "given"
        if power > 0 and attack > 0:
            base = attack * power / 100.0
            why = ("attack %d (%s) x skill %d power %g%%"
                   % (attack, parts, skill, power))
        else:
            why = ("flat --hit-damage %d (skill %d: power %g, attack %d)"
                   % (flat, skill, power, attack))
    if getattr(args, "mob_level_correction", "on") != "on":
        return max(1, int(round(base))), why
    lv = int(staff.char_level(args) if level is None else level)
    gap = int(m.get("level") or 0) - lv
    row = fegamedata.lv_diff_correction(gap)
    if not row:
        return max(1, int(round(base))), why + ", no lv_diff row"
    dmg = max(1, int(round(base * row[0])))
    return dmg, ("%s x %g (monster L%d - player L%d = gap %+d)"
                 % (why, row[0], int(m.get("level") or 0), lv, gap))


def restore_push(conn, outbound, mode, be, args, stat, amount, why):
    """Give the player `amount` HP or Pw (an item's effect). HP is only the
    server's while --monster-attack on owns it (as --item-heal always was);
    Pw goes through pw_push, the setter the regen already uses."""
    amount = int(amount)
    if stat == "pw":
        before = progression.pw_now(args)
        progression.pw_push(conn, outbound, mode, be, args, before + amount,
                "%s: +%d Pw (%d -> %d)" % (why, amount, before,
                                           min(progression.pw_max(args), before + amount)))
        return
    if getattr(args, "monster_attack", "off") != "on":
        print("[feworld]    %s: +%d HP NOT applied -- the server owns HP only "
              "under --monster-attack on" % (why, amount), flush=True)
        return
    was = int(sess._SESSION.get("player_hp", death.player_hp_max(args)))
    now_hp = min(death.player_hp_max(args), was + amount)
    sess._SESSION["player_hp"] = now_hp
    if sess._SESSION.get("player_dead") and now_hp > 0:
        sess._SESSION["player_dead"] = False
        sess._SESSION.pop("revive_at", None)
        death.player_dead_push(conn, outbound, mode, be, args, False)
    death.player_hp_push(conn, outbound, mode, be, args, now_hp)
    print("[feworld]    %s: +%d HP -- HP %d -> %d/%d"
          % (why, amount, was, now_hp, death.player_hp_max(args)), flush=True)


def item_use_effect(conn, outbound, mode, be, args, item_no, fx):
    """Apply what item `item_no` does, after its 0x1089 OK.

    --item-effects table (default, 2026-09-12): the client's own
    EFFECT_DATA row (fegamedata.item_effect): food heals at once (bread 50,
    cheese 90, steak 150), a regen potion ticks 25/48/100 HP every 4 s for
    32 s, a power pot 5/10/15 Pw every 4 s for 60 s -- the ticks are run by
    item_effect_pump. `flat` is the old uniform --item-heal."""
    if getattr(args, "item_effects", "table") != "table":
        heal = int(getattr(args, "item_heal", 0) or 0)
        if heal > 0:
            restore_push(conn, outbound, mode, be, args, "hp", heal,
                         "item %d, flat --item-heal" % item_no)
        return
    if not fx:
        print("[feworld]    item %d restores nothing: no use skill (+0x56) or "
              "no HP/Pw effect behind it in EFFECT_DATA" % item_no, flush=True)
        return
    if not fx["total_ms"]:
        restore_push(conn, outbound, mode, be, args, fx["stat"], fx["amount"],
                     "item %d (EFFECT_DATA %d)" % (item_no, fx["effect"]))
        return
    tick = max(0.1, fx["tick_ms"] / 1000.0)
    n = max(1, fx["total_ms"] // max(1, fx["tick_ms"]))
    sess._SESSION.setdefault("item_fx", {})[fx["stat"]] = {
        "amount": fx["amount"], "tick": tick, "left": n, "tier": fx["tier"],
        "item": item_no, "next_at": time.monotonic() + tick}
    print("[feworld]    item %d (EFFECT_DATA %d): +%d %s every %gs x%d, tier %d"
          % (item_no, fx["effect"], fx["amount"], fx["stat"].upper(), tick, n,
             fx["tier"]), flush=True)


def item_effect_pump(conn, outbound, mode, be, args):
    """Run the ticks of a potion in effect (item_use_effect). Death ends
    them -- a regen must not revive a corpse."""
    fx = sess._SESSION.get("item_fx")
    if not fx:
        return
    if sess._SESSION.get("player_dead"):
        fx.clear()
        print("[feworld]    the player died -- potion effects end", flush=True)
        return
    now = time.monotonic()
    for stat in list(fx):
        e = fx[stat]
        while e["left"] > 0 and now >= e["next_at"]:
            e["left"] -= 1
            e["next_at"] += e["tick"]
            restore_push(conn, outbound, mode, be, args, stat, e["amount"],
                         "item %d tick (%d left)" % (e["item"], e["left"]))
        if e["left"] <= 0:
            del fx[stat]


def monster_swing_damage(args, m):
    """What ONE monster hits the player for, and the sentence that explains it.

    KEY: 2026-09-09. Until now every monster in the game hit for the same flat
    --monster-damage, so a level 1 Venomous and a level 53 Duke Orc were the
    same fight. fet_npc_type ships an ATTACK column (92 at level 1 rising to
    816 at level 70) and this scales the configured number by it:

        damage = --monster-damage * monster_attack / 92

    so --monster-damage keeps meaning exactly what it meant -- what the
    WEAKEST monster in the game does -- and everything above level 1 hurts
    more, in the proportion SE shipped.

    WARNING: THE RATIO IS SHIPPED; THE SCALE IS OURS. The monsters' attack values are
    on the monsters' own scale (their HP runs 124..30,000) and the player's
    max HP is our --player-hp, so nothing here claims to reproduce SE's
    numbers. What it does claim is that a Duke Orc should hit about seven
    times as hard as a Venomous, and that IS in the shipped table.

    WARNING: NO LEVEL CORRECTION HERE, deliberately. 2026-09-12: the table's KEY is
    traced (monster level - player level, the client's 0x050DFEB0) and f0 is
    the player's damage dealt (player_hit_damage applies it), but damage
    TAKEN is f2 (0.4..4) or f3 (0.7..5) and nothing says which -- the client
    never reads the floats. Multiplying by the wrong one would launder a
    guess into the numbers.

    --damage-model flat restores one number for every monster.
    """
    base = max(1, int(getattr(args, "monster_damage", 60) or 60))
    if getattr(args, "damage_model", "table") != "table":
        return base, "flat --monster-damage"
    atk = int(m.get("attack") or 0)
    if atk <= 0:
        return base, "flat (this type has no ATTACK column)"
    dmg = max(1, int(round(base * atk / float(_MOB_ATTACK_REF))))
    return dmg, ("attack %d / %d ref x --monster-damage %d"
                 % (atk, _MOB_ATTACK_REF, base))


#: The resistance cap and divisor of the one published formula (FEZ, 2009).
RESIST_CAP = 400


def player_resistance(args):
    """(total 耐性, pieces counted, worn pieces with no known value).

    The SUM of every worn piece's defence (fegamedata.armour_defence: fewiki's
    2006 tables) -- a player in 2006 reported a set reading as its pieces'
    sum (+1 at most: fewiki 防具/手 comments, 2006-03-04). Capped at
    RESIST_CAP."""
    total = n = unknown = 0
    try:
        rows = equipment.stored_equip_rows(args)
    except Exception:                                  # noqa: BLE001
        rows = []
    for _uid, no, _slot, worn, _ct in rows:
        if worn is None:
            continue
        d = fegamedata.armour_defence(no)
        if d is None:
            if fegamedata.item_use(no).get("atk") or fegamedata.item_use(no).get("matk"):
                continue                                # a weapon: no defence
            unknown += 1
            continue
        total += d
        n += 1
    return min(RESIST_CAP, total), n, unknown


def monster_hit_damage(args, m):
    """monster_swing_damage, then the player's ARMOUR (--armour-defence on).

    KEY: 2026-09-12, BEST EFFORT and labelled so: damage x (1 - 耐性/400), 耐性
    capped at 400 -- the only published formula (FEZ, 2009: the fezdmg
    calculator's effectDef/getRealDef). No RoD-era formula survives; what
    RoD players did write fits it ("the real effect is small for how much the
    耐性 number rises", fv_class.txt:68; Guard Reinforce's +44 "noticeably"
    changes damage taken, hw_5.txt:210). WARNING: Not modelled: sitting (-70 % in
    FEZ, "1.2-1.5x damage" felt in RoD), buffs, and the level-gap multiplier
    on damage TAKEN (lv_diff f2/f3 -- which one is unsettled)."""
    dmg, why = monster_swing_damage(args, m)
    if getattr(args, "armour_defence", "on") != "on":
        return dmg, why
    res, n, unknown = player_resistance(args)
    if res <= 0:
        return dmg, why + ("; no armour defence (%d worn piece(s) unknown)"
                           % unknown if unknown else "")
    out = max(1, int(round(dmg * (1.0 - res / float(RESIST_CAP)))))
    return out, ("%s; armour 耐性 %d over %d piece(s)%s -> x%.3f"
                 % (why, res, n, (", %d unknown" % unknown) if unknown else "",
                    1.0 - res / float(RESIST_CAP)))
