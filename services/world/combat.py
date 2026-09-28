"""Combat: registering a target, hit and kill pushes, the battle tally."""
import struct
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import buildings, damage, drops, monsters, progression, sess, wire

# ---------------------------------------------------------------------------
# COMBAT -- the loop the client has been waiting for, decoded 2026-09-06.
#
# WHO DECIDES WHAT. FE's client is authoritative for the SWING and for the
# COLLISION; the server is authoritative for DAMAGE and for DEATH. The client
# says "skill S of mine landed on unit U at (x,y,z)" with 0xA011 and then does
# nothing else -- 0x05069700's only local act is `[unit+0x9fc] = now + 1`
# (0x050699d0), a re-hit lockout. It never subtracts HP. So a monster that
# takes no damage and never dies is not a missing client feature; it is a
# server that never answered 0xA011.
#
# HOW HP REACHES THE CLIENT. The stat-block appliers are the channel, and
# WHICH ONE depends on the target's class:
#
#   * the PLAYER (KIND-B [obj+0x20] == 3) can take 0x2024 -- and maskA bit
#     0x4's setter (0x04ff3be0) is the DAMAGE EVENT, which runs the hit
#     effect/number (the 0x66/0xff/0x33 colour bytes at 0x04ff3c85..).
#     WARNING: RETRACTED 2026-09-09: this said it "subtracts from +0x49e". IT STORES.
#     0x04ff3d19-2a reads the current hp into si, subtracts the value we sent
#     to get the DAMAGE NUMBER, and writes the value we sent into +0x49e. The
#     wire carries the NEW hp; sending "the damage" would set the player's HP
#     to the damage. See player_hp_push.
#   * a MONSTER is `CFeClientNPCObject` and is NOT kind 3 -- its ctor
#     (0x05068f80) sets [unit+0x24] to 0xBBC/0xBBB and the base classes set
#     [+0x20] to 2/4/5/7, never 3 -- so EVERY 0x2024/0x2025/0x2026 setter
#     silently skips it (`cmp eax, 3 / jne` in each one).
#
# THE WAY IN for a monster is the record that BUILT it. `0x1006`'s stat
# applier (0x0507a990, reached from the type-8 arm's mask1 bit 1) has NO kind
# gate at all, and the type-8 arm's lookup HIT path applies the masks to the
# EXISTING object instead of rebuilding it (0x0503afd9 -> 0x0503b0f7, the
# client logging "Npc %d already exists"). So re-sending the same type-8
# record with new stats is an in-place update.
#
# The stat bits used here are the ones _AVATAR_STATS already pins:
#     bit 1 (0x2) -> +0x49a  HP max base   (the gauge draws +0x49a + +0x49c)
#     bit 2 (0x4) -> +0x49e  HP current
#
# NOT LIVE-TESTED. Two things in particular are inference, and both are
# called out in the log lines so a live run can falsify them cheaply:
#   * that a monster's HP bar reads +0x49e/+0x49a the way the player's does
#     (the Status window's own printf proves it for the PLAYER only);
#   * that 0x1004 MSG_DEL is the right death message. Its arm (0x0503a079)
#     is header-only, so the object it removes can only be the envelope id --
#     which is how every other addressed record in this family works, but no
#     0x1004 has ever been sent from here.
_MOB_HP_BIT_MAX = 0x2
_MOB_HP_BIT_CUR = 0x4


def mob_register(args, obj, mtype, kind, level, x, y, z, name="",
                 type_id=None):
    """Remember a spawned MONSTER so 0xA011 has something to hit.

    Only kind 0 (0xBBC, the hit-detection/level kind) is registered: kind 1
    is a town NPC (0xBBB, talk range) and the client does not swing at it.
    """
    if kind:
        return
    hp = int(getattr(args, "monster_hp", 0) or 0)
    if hp <= 0:
        hp = fegamedata.npc_hp(mtype, type_id) or 100
    # KEY: THE MONSTER'S OWN LEVEL, not the global knob (2026-09-12, live).
    # `level` here is --monster-level, ONE NUMBER FOR EVERY MONSTER IN THE
    # WORLD, default 1. fet_npc_type ships a level per type and the CLIENT
    # reads it for the target ring -- live testing met a level 47 Ice Dragon
    # while this registry had it as level 1 -- so the two disagreed, and the
    # side that pays the reward was the wrong one: mob_exp's default
    # `--mob-exp rod` is rod_mob_exp(m["level"]), i.e. a level-1 payout for
    # killing a level-47 monster. hp, attack and defence already came from the
    # type (npc_hp/npc_attack/npc_defence with type_id); the level was the one
    # column still taken from the knob.
    #
    # --monster-level stays the FALLBACK for a type with no shipped level, and
    # --monster-level-source knob restores the old behaviour.
    lvl = int(level)
    if str(getattr(args, "monster_level_source", "shipped")) == "shipped":
        real = 0
        try:
            real = int(fegamedata.npc_level(mtype, type_id) or 0)
        except Exception:                              # noqa: BLE001
            real = 0
        if real > 0 and real != lvl:
            print("[feworld]    monster %d (%s): LEVEL %d from fet_npc_type, "
                  "not --monster-level %d -- the reward is rod_mob_exp(level), "
                  "so this is what it is worth to kill"
                  % (int(obj), name or "type %s" % type_id, real, lvl),
                  flush=True)
            lvl = real
    mobs = sess._SESSION.setdefault("mobs", {})
    mobs[int(obj)] = {"type": int(mtype), "level": lvl, "name": name,
                      "type_id": type_id, "hp": hp, "hpmax": hp,
                      # fet_npc_type's own ATTACK/DEFENCE columns, named
                      # 2026-09-09. 0 when the type is unknown, and every
                      # caller falls back rather than inventing.
                      "attack": fegamedata.npc_attack(mtype, type_id),
                      "defence": fegamedata.npc_defence(mtype, type_id),
                      "pos": (x, y, z), "home": (x, y, z), "dead": False, "hits": 0}


def mob_stat_push(conn, outbound, mode, be, args, obj, m):
    """Re-send the type-8 record for `obj` carrying only the HP pair.

    mask2 = 0 and mask3 = 0 on purpose: the position is already right and a
    second position would re-run the ground snap (0x5000450) on every hit.
    """
    # WARNING: LIVE 2026-09-09 05:38:38Z, THE FIRST HIT EVER ANSWERED -- and it
    # crashed the client one frame later (FE_Client+0xE44EB, a null
    # [item+0x4e8]). The record's mask1 is the RECORD mask (bit 0x2 = "a stat
    # block follows"), and the stat block is [u32 statmask][u32 statmask2]
    # then the fields -- the type-8 HIT path (0x0503b144..0x0503b165) reads
    # BOTH masks before calling 0x0507a990, exactly as the type-0 record
    # add_entities already packs (`statmask, 0`). This used to put the stat
    # bits where the record mask goes and send no second mask, so the client
    # read the HP pair as a mask, ran past the message, and built an item
    # from garbage with no table record. The spawn record (mask1 = 0) never
    # exercised this, which is why nine monsters spawn fine.
    rec = (struct.pack(">HHB", m["type"] & 0xFFFF, 0, m["level"] & 0xFF)
           + struct.pack(">I", 0x2)                       # record mask1: stats
           + struct.pack(">II", _MOB_HP_BIT_MAX | _MOB_HP_BIT_CUR, 0)
           + struct.pack(">hh", m["hpmax"] & 0x7FFF, max(0, m["hp"]) & 0x7FFF)
           + struct.pack(">I", 0) + struct.pack(">I", 0))  # mask2, mask3
    body = struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 8) + rec
    wire.send(conn, outbound, wire.inner_msg(0x1006, body, obj), mode, be,
         args.seq_mode == "echo", args.world_prefix)


def mob_kill_push(conn, outbound, mode, be, args, obj):
    """0x1004 MSG_DEL -- header-only arm, so the ENVELOPE id is the object."""
    wire.send(conn, outbound, wire.inner_msg(0x1004, b"", obj), mode, be,
         args.seq_mode == "echo", args.world_prefix)


def combat_hit(conn, outbound, mode, be, args, f):
    """Answer one 0xA011 MSG_NPC_HIT_NOTIFY. See the block comment above."""
    if len(f) < 19:
        print("[feworld]    0xA011 HIT: %d-byte body, expected >= 19 -- "
              "not decoded: %s" % (len(f), f.hex()), flush=True)
        return
    target, attacker, b, c = struct.unpack_from(">IIII", f, 0)
    skill, kind = struct.unpack_from(">HB", f, 16)
    pos = struct.unpack_from(">fff", f, 19) if len(f) >= 31 else (0.0, 0.0, 0.0)
    mobs = sess._SESSION.setdefault("mobs", {})
    m = mobs.get(target)
    print("[feworld]    0xA011 HIT target=%d attacker=%d skill=%d kind=%d "
          "b=%d c=%d at (%g, %g, %g)%s"
          % (target, attacker, skill, kind, b, c, pos[0], pos[1], pos[2],
             "" if m else "  -- NOT a registered monster"), flush=True)
    if getattr(args, "combat", "off") != "on":
        if m:
            print("[feworld]       --combat is off: the hit is logged and "
                  "NOTHING takes damage.", flush=True)
        return
    if m is None and buildings.keep_hit(conn, outbound, mode, be, args, target,
                              why="(0xA011)"):
        return
    if m is None:
        return
    died = False
    with monsters._MOB_LOCK:
        # shared monsters: two players can land the killing blow at once
        if m["dead"]:
            return
        dmg, dwhy = damage.player_hit_damage(args, m, skill=skill)
        m["hits"] += 1
        m["hp"] -= dmg
        if m["hp"] <= 0:
            m["hp"], m["dead"], died = 0, True, True
            m["died_at"] = time.monotonic()
    if not died:
        mob_stat_push(conn, outbound, mode, be, args, target, m)
        monsters.mob_relay(args, "mob_hp", {"obj": target})
        print("[feworld]       -> 0x1006 type 8 HP %d/%d on %s (hit #%d, -%d "
              "= %s) -- mask1=0x6, +0x49a max / +0x49e current. If the bar "
              "does not move, the monster's gauge is NOT this pair."
              % (m["hp"], m["hpmax"], m["name"] or "monster %d" % target,
                 m["hits"], dmg, dwhy), flush=True)
        return
    mob_stat_push(conn, outbound, mode, be, args, target, m)
    mob_kill_push(conn, outbound, mode, be, args, target)
    monsters.mob_relay(args, "mob_del", {"obj": target})
    print("[feworld]       -> HP 0 then 0x1004 MSG_DEL: %s is DEAD after %d "
          "hit(s)." % (m["name"] or "monster %d" % target, m["hits"]),
          flush=True)
    drops.kill_gold_credit(conn, outbound, mode, be, args, m)     # gold from hunting
    drops.drop_on_kill(conn, outbound, mode, be, args, m)        # a chest, maybe
    battle_tally("kills", 1)
    exp = drops.mob_exp(args, m)
    if exp > 0 and getattr(args, "kill_reward", "on") == "on":
        progression.combat_exp_push(conn, outbound, mode, be, args, exp)
        # ...and the RANK ladder, which has never moved for anybody.
        ks = getattr(args, "kill_score", "exp")
        if ks != "off":
            gained = exp if ks == "exp" else int(ks)
            progression.combat_score_push(conn, outbound, mode, be, args, gained)


def battle_tally(key, n=1):
    """Count one thing that happened in THIS field visit (kills, EXP gained).

    The war-result window (0x1100, pushed on Field Out) draws Exp and Score
    off the wire, and until 2026-09-08 it was served all zeros because "we do
    not simulate a battle". Now a kill is a fact this loop produced, so the
    tallies feed it (--war-result-values session). Reset on every field entry
    by battle_reset(); a relog starts at zero, the STORED total is elsewhere
    (_SESSION["exp"], seeded from the character store)."""
    b = sess._SESSION.setdefault("battle", {})
    b[key] = b.get(key, 0) + int(n)
    return b[key]


def battle_reset():
    sess._SESSION["battle"] = {}
