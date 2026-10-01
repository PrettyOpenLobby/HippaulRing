"""Combat: registering a target, hit and kill pushes, the battle tally."""
import struct
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import buildings, damage, drops, ext, extrun, monsters, progression, sess, status, wire

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
    s = sess._SESSION
    if status.on(args):
        # manual p.39: a STUNNED player can do nothing, a DISARMED one has no
        # weapon attacks and no skills. The client drew the swing; the server
        # refuses its damage (to a monster or a building alike).
        why = status.refusal(s)
        if why:
            print("[feworld]       the attacker is %s -- the hit does no "
                  "damage (--status-effects on)" % why, flush=True)
            return
    if m is None and buildings.keep_hit(conn, outbound, mode, be, args, target,
                              why="(0xA011)", skill=skill):
        return
    if m is None:
        return
    if status.on(args):
        if status.has(s, "hide"):
            # CHOSEN (FEZ): attacking out of Hide ends it
            status.table(s).pop("hide", None)
            status.cond_sync(conn, outbound, mode, be, args)
    dmg, dwhy = damage.player_hit_damage(args, m, skill=skill)
    mob_damage(conn, outbound, mode, be, args, target, m, dmg, _my_key(),
               dwhy, skill=skill)


def _my_key():
    """This session's key in a monster's damage ledger: its charid, 0 when
    the session has none (a harness, a lobby-less test)."""
    cid = sess._SESSION.get("charid")
    try:
        return int(cid) if cid is not None else 0
    except (TypeError, ValueError):
        return 0


def mob_damage(conn, outbound, mode, be, args, target, m, dmg, by, dwhy,
               skill=None):
    """Take `dmg` off monster `m` on behalf of charid `by`, record it in the
    monster's DAMAGE LEDGER, push the HP or the death, and pay the kill.

    THE LEDGER (2026-10-01, manual p.38): m["ledger"] = {charid: damage that
    LANDED} and m["first"] = {charid: order of their first hit}, kept on the
    monster record itself, so with --monster-share on every session's hits
    land in ONE ledger (the field's shared dict, under _MOB_LOCK). A respawn
    re-registers the record (mob_register), which starts a clean ledger."""
    died = False
    applied = []
    with monsters._MOB_LOCK:
        # shared monsters: two players can land the killing blow at once
        if m["dead"]:
            return False
        if status.on(args):
            status.break_on_hit(m)                     # root ends on a hit
            if skill is not None:
                applied = status.apply(m, status.bad_specs(skill), src=by,
                                       skill=skill)
        landed = max(0, min(int(dmg), int(m["hp"])))
        m["hits"] += 1
        m["hp"] -= int(dmg)
        led = m.setdefault("ledger", {})
        led[by] = led.get(by, 0) + landed
        first = m.setdefault("first", {})
        if by not in first:
            first[by] = len(first)
        if m["hp"] <= 0:
            m["hp"], m["dead"], died = 0, True, True
            m["died_at"] = time.monotonic()
    if applied:
        print("[feworld]       %s now %s (skill %s, --status-effects on)"
              % (m["name"] or "monster %d" % target, ", ".join(applied), skill),
              flush=True)
    if not died:
        mob_stat_push(conn, outbound, mode, be, args, target, m)
        monsters.mob_relay(args, "mob_hp", {"obj": target})
        print("[feworld]       -> 0x1006 type 8 HP %d/%d on %s (hit #%d, -%d "
              "= %s) -- mask1=0x6, +0x49a max / +0x49e current. If the bar "
              "does not move, the monster's gauge is NOT this pair."
              % (m["hp"], m["hpmax"], m["name"] or "monster %d" % target,
                 m["hits"], dmg, dwhy), flush=True)
        return False
    mob_stat_push(conn, outbound, mode, be, args, target, m)
    mob_kill_push(conn, outbound, mode, be, args, target)
    monsters.mob_relay(args, "mob_del", {"obj": target})
    print("[feworld]       -> HP 0 then 0x1004 MSG_DEL: %s is DEAD after %d "
          "hit(s)." % (m["name"] or "monster %d" % target, m["hits"]),
          flush=True)
    kill_rewards(conn, outbound, mode, be, args, target, m, by)
    return True


# ---------------------------------------------------------------------------
# WHO GETS THE KILL (2026-10-01, audit A5, manual p.38, as translated in
# GameFiles/Manuals/FE-Manual-EN.md): solo, the character who did the most
# damage gets the loot right; in a party, the party that did the most damage
# gets it. Equal damage: whoever hit first. Gold is split automatically in
# proportion to damage dealt.
#
# So at death the ledger is grouped by PARTY (feparty, as it stands at the
# kill; a solo player is a party of one), the group with the most summed
# damage wins, a tie goes to the group whose member hit first, and inside the
# winning group the member with the most damage (first hit on a tie) is the
# chest's OWNER -- the rights are the owner's party, as drop_on_kill always
# made them.
#
#   * GOLD (--kill-gold): split over EVERY contributor by share of landed
#     damage, rounded down; the remainder goes to the owner (CHOSEN).
#   * EXP and rank score: the manual says nothing. CHOSEN: the winning group
#     only, split inside it by damage share (at least 1 each), so a party
#     earns what one player would and a solo winner gets it all -- feparty
#     has no EXP-sharing design to follow.
#   * the CHEST: rolled and placed by the OWNER's own session (its sex, its
#     field), relayed to it when the owner is not the killer.
#
# A reward for another player runs on THAT player's thread (the
# "kill_reward" relay), because only it may touch its wallet and its client.
# --loot-rule last restores the old rule: everything to the killing blow.
# ---------------------------------------------------------------------------
def loot_winner(m, killer=0):
    """(owner, group, contrib, why): the loot owner's charid, the charids of
    the winning group, the ledger {charid: landed damage}, and the reason."""
    led = {int(k): int(v) for k, v in (m.get("ledger") or {}).items()}
    first = {int(k): int(v) for k, v in (m.get("first") or {}).items()}
    if not led:
        return int(killer), {int(killer)}, {int(killer): 0}, "no ledger"
    groups = {}
    for cid in led:
        key = frozenset(drops.party_charids(cid)) if cid else frozenset((0,))
        groups.setdefault(key, []).append(cid)

    def rank(item):
        key, cids = item
        return (-sum(led[c] for c in cids),
                min(first.get(c, 1 << 30) for c in cids))
    best_key, cids = sorted(groups.items(), key=rank)[0]
    owner = sorted(cids, key=lambda c: (-led[c], first.get(c, 1 << 30)))[0]
    why = ("%s with %d of %d damage" % (
        ("party of %d" % len(best_key)) if len(best_key) > 1
        else "charid %d" % owner, sum(led[c] for c in cids), sum(led.values())))
    if len(groups) > 1:
        tied = [k for k, v in groups.items()
                if sum(led[c] for c in v) == sum(led[c] for c in cids)]
        if len(tied) > 1:
            why += ", tie broken by the first hit"
    return owner, set(cids) | set(best_key), led, why


def split_by_damage(total, contrib, owner):
    """{charid: share of `total`} by landed damage, rounded down; the
    remainder to `owner`. Everyone with damage > 0 is listed."""
    total = int(total)
    dealt = {c: d for c, d in contrib.items() if d > 0}
    s = sum(dealt.values())
    if total <= 0:
        return {}
    if s <= 0:
        return {int(owner): total}
    out = {c: total * d // s for c, d in dealt.items()}
    out[int(owner)] = out.get(int(owner), 0) + total - sum(out.values())
    return {c: n for c, n in out.items() if n > 0}


def _mob_snapshot(m):
    """What a reward relay needs of a dead monster (the record itself may be
    re-registered by a respawn before the relay lands)."""
    return {"name": m.get("name"), "level": m.get("level"),
            "type": m.get("type"), "type_id": m.get("type_id"),
            "pos": tuple(monsters.mob_pos(m)) if m.get("pos") else None,
            "field": sess._SESSION.get("field"),
            "room": sess._SESSION.get("room", -1)}


def _pay_exp(conn, outbound, mode, be, args, exp):
    if exp <= 0 or getattr(args, "kill_reward", "on") != "on":
        return
    progression.combat_exp_push(conn, outbound, mode, be, args, exp)
    # ...and the RANK ladder, which has never moved for anybody.
    ks = getattr(args, "kill_score", "exp")
    if ks != "off":
        gained = exp if ks == "exp" else int(ks)
        progression.combat_score_push(conn, outbound, mode, be, args, gained)


def kill_rewards(conn, outbound, mode, be, args, target, m, killer=None):
    """Pay monster `m`'s death: gold, EXP and the chest, per loot_winner."""
    me = _my_key()
    killer = me if killer is None else int(killer)
    snap = _mob_snapshot(m)
    battle_tally("kills", 1)
    if str(getattr(args, "loot_rule", "damage") or "damage") == "last":
        owner, group, contrib, why = killer, {killer}, {killer: 1}, "--loot-rule last"
        exp_share = {killer: drops.mob_exp(args, m)}
    else:
        owner, group, contrib, why = loot_winner(m, killer)
        exp_share = split_by_damage(drops.mob_exp(args, m),
                                    {c: d for c, d in contrib.items() if c in group},
                                    owner)
        for c in group:                 # at least 1 to a winner who hit
            if contrib.get(c, 0) > 0 and exp_share.get(c, 0) <= 0 and \
                    drops.mob_exp(args, m) > 0:
                exp_share[c] = 1
    gold_share = split_by_damage(drops.kill_gold(args, m), contrib, owner)
    print("[feworld]       KILL %s: loot to charid %d (%s); gold %s, exp %s"
          % (m.get("name") or "monster %d" % target, owner, why,
             gold_share or "none", exp_share or "none"), flush=True)
    away = {}
    for cid in set(gold_share) | set(exp_share) | {owner}:
        pay = {"gold": int(gold_share.get(cid, 0)),
               "exp": int(exp_share.get(cid, 0)), "drop": cid == owner}
        if cid == me or (cid == 0 and me == 0):
            if pay["gold"] > 0:
                drops.kill_gold_credit(conn, outbound, mode, be, args, m,
                                       amount=pay["gold"])
            if pay["drop"]:
                drops.drop_on_kill(conn, outbound, mode, be, args, m)
            _pay_exp(conn, outbound, mode, be, args, pay["exp"])
        else:
            away[cid] = pay
    for cid, pay in sorted(away.items()):
        n = extrun.ext_post("kill_reward", dict(pay, mob=snap, by=me, to=cid),
                            to=lambda ps, _n, cid=cid: (
                                ps.get("charid") is not None
                                and int(ps["charid"]) == cid))
        if n:
            continue
        if pay["drop"]:
            # the owner's session is gone: the killer places the chest so the
            # loot is not lost, with the OWNER's party's rights (CHOSEN)
            drops.drop_on_kill(conn, outbound, mode, be, args, m, owner=cid)
        print("[feworld]       charid %d has no live session -- %d gold / %d "
              "exp not paid" % (cid, pay["gold"], pay["exp"]), flush=True)


def _kill_reward_relay(ctx, pl):
    """On the RECEIVING player's thread: their share of a kill another
    session resolved."""
    m = dict(pl.get("mob") or {})
    args = ctx.args
    if int(pl.get("gold") or 0) > 0:
        drops.kill_gold_credit(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                               m, amount=int(pl["gold"]))
    if pl.get("drop"):
        s = sess._SESSION
        if (s.get("in_field") and s.get("field") == m.get("field")
                and s.get("room", -1) == m.get("room", -1) and m.get("pos")):
            drops.drop_on_kill(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, m)
        else:
            print("[feworld]    kill reward: the chest of %s is not rolled -- "
                  "charid %s left that field" % (m.get("name"), s.get("charid")),
                  flush=True)
    _pay_exp(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, int(pl.get("exp") or 0))
    print("[feworld]    kill reward from charid %s's kill of %s: %d gold, %d exp%s"
          % (pl.get("by"), m.get("name"), int(pl.get("gold") or 0),
             int(pl.get("exp") or 0), ", the chest" if pl.get("drop") else ""),
          flush=True)


ext.register_relay("kill_reward", _kill_reward_relay)


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
