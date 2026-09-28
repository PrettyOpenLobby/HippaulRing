"""Player HP, death, the return to base, respawn waits and spawn protection."""
import struct
import sys
import time
from . import character, events, progression, sess, skilllist, spawns, wallet, wire, zones

# ---------------------------------------------------------------------------
# THE OTHER HALF OF THE BATTLE -- monsters that hit back, 2026-09-09.
#
# KEY: THE PLAYER'S HP CHANNEL IS A **STORE**, NOT A SUBTRACT, and this file said
# the opposite until today. 0x2024 maskA bit 0x4's setter is 0x04ff3be0, and
# its player arm (KIND-B == 3) reads:
#
#     04ff3d19  mov si, [edi+0x49e]        ; si = the CURRENT hp
#     04ff3d22  mov ax, [esp+0x38]         ; ax = the value WE SENT
#     04ff3d27  sub si, ax                 ; si = old - new  -> the damage NUMBER
#     04ff3d2a  mov [edi+0x49e], ax        ; +0x49e = the value we sent
#
# so the wire carries the NEW hit points and the client draws the difference as
# the floating damage number and runs the hit effect. Sending "the damage" here
# would set the player's HP TO the damage. (The same function's kind-5 arm does
# the same for +0x778 -- that is how a BUILDING takes damage.)
#
# DEATH is ours too: CONDITION (`[unit+0x2b4]`) bit 0x800000 is DEAD and
# 0x1000000 is GHOST, the client names both itself, and that word has no local
# writer -- see unit_state_push. We set it through the same 0x2024 maskA
# 0x100000 push, keeping the skill-gate bits so nothing else changes.
#
# WARNING: EVERY NUMBER HERE IS CHOSEN. FE's real monster damage is a formula nobody
# has traced (fet_npc_lv_diff_correction's four floats per level band are the
# obvious input and have no traced consumer). --monster-damage is a flat number
# so that "does the player take damage at all" can be answered before anyone
# argues about how much.
#
# WARNING: AND THE REVIVE IS UNCONDITIONAL. A death we cannot undo is a wedged client
# and a relaunch for whoever is playing, so the revive timer is not optional:
# it always runs, and --revive-secs only chooses how long.
def player_hp_push(conn, outbound, mode, be, args, hp):
    """0x2024 maskA bit 0x4 -- the DAMAGE EVENT. The body carries the NEW hp."""
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">IIh", 0x4, 0, max(0, int(hp)) & 0x7FFF),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)


def player_dead_push(conn, outbound, mode, be, args, dead):
    """CONDITION DEAD (0x800000) on or off, through the state setter."""
    base = getattr(args, "unit_state", None)
    v = (base if base is not None else 0) & 0xFFFFFFFF
    v = (v | 0x800000) if dead else (v & ~0x800000)
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">III", 0x100000, 0, v & 0xFFFFFFFF),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 CONDITION 0x%08X -- %s"
          % (v, "DEAD (bit 0x800000)" if dead else "alive again (DEAD cleared)"),
          flush=True)


def player_hp_max(args):
    # KEY: 2006: a FLAT 1000 at every level -- SE's own 3rd-beta screenshot shows a
    # Lv25 Sorcerer at HP 1000 / Pw 100, beta-1 press says levelling does not
    # raise HP/Pw, and dat.pak's class table ships 1000 as the first base value
    # of all 7 classes. This was 200 until 2026-09-11.
    # A SUMMONED player (feunit's `morph`, 2026-09-11) is the summon: its HP
    # max is the summon's, so every clamp in here caps at the Giant's 5400
    # and not at the human's 1000. feunit ends the summon on death, so the
    # revive reads the human's number again.
    m = sess._SESSION.get("morph")
    if m and m.get("hp_max"):
        return max(1, int(m["hp_max"]))
    return max(1, int(getattr(args, "player_hp", 1000) or 1000))


# ---------------------------------------------------------------------------
# DEATH AND RETURN, 2006 rules (2026-09-11).
#
#   * IN A WAR (prep or battle) a dead player goes back to their side's BASE --
#     "HPが「0」になると拠点（キープもしくは城）に戻されます" = at 0 HP you are
#     returned to your base, keep or castle [SE flow08]; "new arrivals and
#     revived players sortie from that fort" [SE warsystem02]. So the base is
#     the side's ARRIVAL point: spawn_for(area, tag=side) -- a `!spawn <side>`
#     row, else the derived point beside that side's keep (derived_spawn).
#   * OUTSIDE A WAR no source says where a hunting death revives
#     (research-local-archive: "Outside war: the respawn location is NOT
#     FOUND"), so it stays what it was: in place. That is OURS, not SE's.
#   * 15 s PROTECTION after that return or after an area move, ended by any
#     action other than moving -- "デッドダウンから拠点に復帰したとき、または
#     エリア移動直後の15秒間…移動以外の行動をとると、即座に効果が切れ"
#     [SE interface05]. The server computes every hit a player takes (monster
#     swings, monster_attack_tick), so the mechanic is a server one: a
#     protected player's swings do nothing. See SPAWN_PROTECT_BREAKERS.
#   * A HUNTING death costs EXP and gold: 「平和時 : Nextの10%の経験値と所持金の
#     20%を失う」 -- 10% of NEXT and 20% of the gold carried [fewiki Guide/メモ
#     2006-05-26, RoD]. A WAR death costs no EXP or gold but -3 crystals and
#     damage to your side's base [same page; fewiki WAR/クリスタル]; a death
#     in a ceasefire costs nothing [SE 8/24]. See death_penalty.
#
# KEY: THE CLIENT HAS ITS OWN "RETURN TO BASE" WINDOW, found while building this
# (fedis, static, NOT seen on a screen). Its update is 0x050AA810 and it only
# stays open while the local player's CONDITION has DEAD or GHOST
# (`test [unit+0x2b4], 0x1800000` at 0x050AA86A -- clear both and it closes).
# The button (0x050AABB0) arms a countdown of [player+0x1384] ms (20000 when
# there is no player object), sends header-only 0x2015 (builder 0x0505E8C0,
# logged by feitems) and draws "Returning to base... %2d s left"; at zero
# (0x050AAA21) it walks the kind-0x7D0 objects for one whose [+0xec] has bit 8
# and whose side byte equals the player's [+0x94], and SAME-FIELD-WARPS ITSELF
# (0x04FF8020, the arm our 0x1166 reaches) to a point in front of it at y=1000.
# So retail's return may have been partly client-driven. What this server does
# is the server half the lead asked for and that does not depend on a keep
# being placed: at --revive-secs the player is revived and warped to the base
# by 0x1166. Reviving clears DEAD, which closes that window, so the two cannot
# both run. If a live run shows the button, 0x2015 is the thing to watch.
#: KEY: THE RESPAWN WAIT, RoD (sourced 2026-09-13):
#: 「約15秒…同一エリアで何度も死亡していると、復活までの時間が
#: 長くなる」 -- about 15 s, longer when you keep dying in the same area
#: [fewiki Guide/メモ 2006-04-12, num/fv_memo.txt:203-204]; the war version:
#: 「一回の戦争中での死亡回数が多くなると、復帰までの時間が長くなります」
#: [fv_warsys.txt:147]. BASE:STEP:CAP seconds -- the 15 is SOURCED, the +5 per
#: repeat death and the 60 ceiling are CHOSEN (no source gives the curve).
DEFAULT_RESPAWN_WAIT = "15:5:60"
#: 0x2024 maskA bit 0x1000000: one u32 of MILLISECONDS -> [player+0x1384],
#: the countdown the client's Return to Base button runs (arm 0x04ff38d2 ->
#: 0x04ff4970, the player only; the button 0x050AABB0). Static, never sent
#: before 2026-09-13.
_U2024_RESPAWN_WAIT_BIT = 0x1000000
#: CHOSEN: a death streak in one area ends after this long without dying
RESPAWN_STREAK_SECS = 600.0


def respawn_wait_ms(args, deaths):
    """The wait for this area's `deaths`-th death in a row, in ms (0 = off)."""
    spec = str(getattr(args, "respawn_wait", DEFAULT_RESPAWN_WAIT) or "off")
    if spec.strip().lower() == "off":
        return 0
    try:
        base, step, cap = (float(v) for v in spec.split(":"))
    except ValueError:
        base, step, cap = 15.0, 5.0, 60.0
    secs = min(cap, base + step * max(0, int(deaths) - 1))
    return int(round(max(0.0, secs) * 1000.0))


def respawn_wait_arm(conn, outbound, mode, be, args):
    """A death just happened: count it against this area, tell the client how
    long its Return to Base countdown runs, and keep the safety-net revive
    from coming sooner than that. Returns the wait in ms. Every death site
    calls this (the monster swing here, fepvp, feunit's summon collapse)."""
    now = time.monotonic()
    area = sess._SESSION.get("field")
    st = sess._SESSION.get("death_streak") or {}
    if st.get("area") != area or now - st.get("t", 0.0) > RESPAWN_STREAK_SECS:
        st = {"area": area, "n": 0}
    st["n"] = int(st.get("n", 0)) + 1
    st["t"] = now
    sess._SESSION["death_streak"] = st
    sess._SESSION.pop("rtb_pressed", None)
    ms = respawn_wait_ms(args, st["n"])
    sess._SESSION["respawn_wait_ms"] = ms
    if ms <= 0:
        return 0
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">III", _U2024_RESPAWN_WAIT_BIT, 0,
                                       ms & 0xFFFFFFFF), wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    sess._SESSION["revive_at"] = max(float(sess._SESSION.get("revive_at", 0) or 0),
                                now + ms / 1000.0)
    print("[feworld]    -> 0x2024 bit 0x1000000 RESPAWN WAIT %d ms ([player+0x1384]) "
          "-- death #%d in a row in area %s (--respawn-wait %s: RoD's ~15 s, "
          "longer for repeated deaths in one area)"
          % (ms, st["n"], area, getattr(args, "respawn_wait",
                                        DEFAULT_RESPAWN_WAIT)), flush=True)
    return ms


def return_to_base(conn, outbound, mode, be, args):
    """The player pressed RETURN TO BASE -- 0x2015, header-only.

    KEY: 2026-09-12, design choice: a death WAITS for this button. The
    window is the client's own (0x050AA810, open while CONDITION has DEAD or
    GHOST); its button (0x050AABB0) sends 0x2015 and counts down
    [player+0x1384] ms. That field is set by the 0x2024 bit 0x1000000 arm
    (0x04ff38d2 -> 0x04ff4970: one u32, applied only to the player) and we
    had never sent it, so the countdown was 0 and the press immediate. KEY:
    2026-09-13: respawn_wait_arm sends it at every death (RoD: ~15 s, longer
    for repeated deaths in one area), and a press now STARTS the wait -- the
    revive follows the client's own countdown. --respawn-wait off = the old
    immediate revive.

    Until today --revive-secs 8 revived everyone before the button mattered
    ("I don't have to hit return to base... which is odd"). Now --revive-secs
    is the SAFETY NET (60 s) and this is the way back: the same revive the
    timer runs -- in place in a hunting field, warped to the side's base by
    0x1166 in a PREP/WAR field [SE flow08]. Reviving clears DEAD, which closes
    the client's window. A 0x2015 from a living player does nothing."""
    if not sess._SESSION.get("player_dead"):
        print("[feworld]    <- 0x2015 RETURN TO BASE from a LIVING player -- "
              "nothing to do", flush=True)
        return False
    wait = int(sess._SESSION.get("respawn_wait_ms") or 0)
    if wait > 0:
        if sess._SESSION.get("rtb_pressed"):
            print("[feworld]    <- 0x2015 RETURN TO BASE pressed again -- the "
                  "countdown is already running", flush=True)
            return True
        sess._SESSION["rtb_pressed"] = True
        sess._SESSION["revive_at"] = time.monotonic() + wait / 1000.0
        print("[feworld]    <- 0x2015 RETURN TO BASE pressed -- the client counts "
              "down %.0f s (the respawn wait sent at the death); the revive "
              "follows it" % (wait / 1000.0), flush=True)
        return True
    print("[feworld]    <- 0x2015 RETURN TO BASE pressed -- reviving now "
          "(not waiting out the --revive-secs %gs safety net)"
          % float(getattr(args, "revive_secs", 60) or 60), flush=True)
    sess._SESSION["revive_at"] = 0
    revive_tick(conn, outbound, mode, be, args)
    return True


def field_war_phase(args, area=None):
    """The campaign phase (fecampaign's int) of `area`, default the field this
    session stands in -- or None when nothing tracks a war there: no campaign
    module, --campaign off, a capital, a room, not in a field. Every caller
    reads None as PEACE."""
    if area is None:
        if not sess._SESSION.get("in_field") or sess._SESSION.get("room", -1) != -1:
            return None
        area = sess._SESSION.get("field")
    try:
        area = int(area)
    except (TypeError, ValueError):
        return None
    if area in zones.CAPITAL_GROUP_IDS:
        return None
    mod = sys.modules.get("fecampaign")
    if mod is None or getattr(args, "campaign", "off") != "on":
        return None
    try:
        return int(mod.phase_of(area))
    except Exception:                                  # noqa: BLE001
        return None


def _war_phases(*names):
    mod = sys.modules.get("fecampaign")
    return tuple(getattr(mod, n) for n in names) if mod is not None else ()


def war_death_side(args):
    """'atk'/'def' when a death in this field returns the player to their
    side's base (a PREP or WAR field, --war-death-return base), else None."""
    if getattr(args, "war_death_return", "base") != "base":
        return None
    ph = field_war_phase(args)
    if ph is None or ph not in _war_phases("PREP", "WAR"):
        return None
    return spawns.spawn_side(args, sess._SESSION.get("field"))


def revive_tick(conn, outbound, mode, be, args):
    """Stand a dead player back up when the timer is done, from ANY clock --
    in place, or (a war field) at their side's base. See the block above."""
    if not sess._SESSION.get("player_dead"):
        return
    if time.monotonic() < sess._SESSION.get("revive_at", 0):
        return
    hp = player_hp_max(args)
    sess._SESSION["player_hp"] = hp
    sess._SESSION["player_dead"] = False
    sess._SESSION.pop("rtb_pressed", None)
    player_dead_push(conn, outbound, mode, be, args, False)
    player_hp_push(conn, outbound, mode, be, args, hp)
    side = war_death_side(args)
    if side is None:
        # KEY: A HUNTING DEATH RETURNS TO THE FIELD'S ARRIVAL POINT (2026-09-12,
        # a design choice). No source says where a hunting death
        # revives; in place put the player back beside whatever killed them.
        # The arrival point is where this field puts anyone who walks in --
        # at peace the CASTLE side [FEZ-early, spawn_side] -- and the client's
        # own Return to Base logic also only ever moves the player within the
        # same field. door=False: the area's own point, not a door's stash.
        area = sess._SESSION.get("field")
        # Two ways to land on this branch, and they must not be confused:
        # a death OUTSIDE a war (--hunt-death-return decides), or a war death
        # whose return to base was declined by --war-death-return place --
        # whose documented meaning is "the old revive where they fell", so it
        # must stay in place rather than be quietly re-routed to arrival.
        in_war = field_war_phase(args) in _war_phases("PREP", "WAR")
        hunt = str(getattr(args, "hunt_death_return", "arrival") or "arrival")
        why = ("--war-death-return place, a war field" if in_war
               else "--hunt-death-return place" if hunt != "arrival"
               else "no arrival point resolves for this area")
        pos = None
        if area is not None and not in_war and hunt == "arrival":
            try:
                pos = spawns.spawn_for(args, int(area), tag=spawns.spawn_side(args, int(area)),
                                door=False)
            except (TypeError, ValueError):
                pos = None
        if pos is not None:
            events.goto_push(conn, outbound, mode, be, args, int(area), pos=pos)
            sess._SESSION["cpos"] = (float(pos[0]), float(pos[1]), float(pos[2]))
            print("[feworld]    REVIVED -- HP %d/%d, at field %s's ARRIVAL "
                  "POINT (%.1f, %.1f, %.1f) by a same-field 0x1166"
                  % (hp, hp, area, pos[0], pos[1], pos[2]), flush=True)
        else:
            print("[feworld]    REVIVED -- HP %d/%d, where the player fell (%s)"
                  % (hp, hp, why), flush=True)
        # WARNING: THE 15 s PROTECTION ON THIS BRANCH TOO (2026-09-12, live). It was
        # only started in the WAR branch below, so a hunting revive stood the
        # player up at full HP beside the monster that had just killed them,
        # unprotected -- and it hit TEN MILLISECONDS later:
        #   19:19:16.864  REVIVED -- HP 1000/1000, where the player fell
        #   19:19:16.874  Duke_Orc hits the player for 329 -- HP 671/1000
        #   19:19:19.088  ... HP 342/1000   ("maybe 1/3 of my hp bar")
        #   19:19:22.639  ... HP 13/1000
        # A death loop. [SE interface05] gives the protection after coming
        # back, and in-place is still coming back.
        spawn_protect_start(args, "revived after a death")
        return
    area = int(sess._SESSION["field"])
    # door=False: a door's one-off arrival stash is not the base
    pos = spawns.spawn_for(args, area, tag=side, door=False)
    events.goto_push(conn, outbound, mode, be, args, area, pos=pos)
    # We just put the player there; until the client's next 0x2023 says so,
    # the monster tick must not swing at the spot they died on.
    sess._SESSION["cpos"] = (float(pos[0]), float(pos[1]), float(pos[2]))
    print("[feworld]    REVIVED -- HP %d/%d, RETURNED TO BASE: a war death in "
          "field %d sends the %s back to %s at (%.1f, %.1f, %.1f) by a "
          "same-field 0x1166 [SE flow08]"
          % (hp, hp, area, "attacker" if side == "atk" else "defender",
             "its keep" if side == "atk" else "the castle",
             pos[0], pos[1], pos[2]), flush=True)
    spawn_protect_start(args, "returned to base after a war death")


#: client->server ids that are an ACTION, i.e. end the 15 s protection
#: ("移動以外の行動" -- any action other than moving, [SE interface05]).
#: WARNING: CHOSEN from what this server can see. NOT here, on purpose: movement
#: telemetry (0x2023/0x2002/0x209F), chat, and UI bookkeeping -- the skill
#: PALETTE (0x2029/0x202A is assigning a skill to a slot, not using it), shop
#: lists, sorting, equip changes. WARNING: A swing that hits NOTHING may send none of
#: these (the client is authoritative for its own hits), so such a swing does
#: not end protection until something lands -- a known gap.
SPAWN_PROTECT_BREAKERS = {
    0xA011: "a hit on an NPC (MSG_NPC_HIT_NOTIFY)",
    0x2010: "a hit on a building (the 0x2010 swing notify)",
    0x2019: "a skill cast request (the cast-shaped 0x2019)",
    0x1031: "a cast record (client form)",
    0x1032: "a cast record (client form, sibling)",
    0x2007: "building (MSG_BUILD_BUILDING)",
    0x2046: "talking to an NPC (MSG_NPC_EVENT_ACTION)",
    0x2051: "using an item",
    0x2044: "a summon (MSG_METAMORPHOSIS)",
    0x20AD: "recovering from a crystal",
    0x2081: "drawing crystal",
    0x201F: "gathering an item",
    0x2052: "a trade request",
}


def spawn_protect_start(args, why):
    """Open the protection window (--spawn-protect-secs; 0 = off)."""
    secs = float(getattr(args, "spawn_protect_secs", 15.0) or 0)
    if secs <= 0:
        sess._SESSION.pop("protect_until", None)
        return False
    sess._SESSION["protect_until"] = time.monotonic() + secs
    sess._SESSION.pop("protect_logged", None)
    print("[feworld]    PROTECTED for %gs (%s): monster hits do no damage until "
          "it runs out or the player does anything but move [SE interface05]"
          % (secs, why), flush=True)
    return True


def spawn_protected(args, now=None):
    """True while the protection window is open; closes it when it runs out."""
    until = sess._SESSION.get("protect_until")
    if not until:
        return False
    if (time.monotonic() if now is None else now) < until:
        return True
    sess._SESSION.pop("protect_until", None)
    print("[feworld]    protection ran out (--spawn-protect-secs)", flush=True)
    return False


def spawn_protect_note(real_id):
    """Every inbound id passes here: an ACTION ends the protection."""
    if not sess._SESSION.get("protect_until"):
        return False
    why = SPAWN_PROTECT_BREAKERS.get(real_id)
    if why is None:
        return False
    sess._SESSION.pop("protect_until", None)
    print("[feworld]    protection ENDED by 0x%04X -- %s is an action other "
          "than moving [SE interface05]" % (real_id, why), flush=True)
    return True


def protect_flag_sync(conn, outbound, mode, be, args):
    """--spawn-protect-flag on: mirror the window into CONDITION bit 0x1000,
    which the client's own table names INVINCIBLE (feunit.COND_INVINCIBLE).
    WARNING: UNPROVEN -- nobody has seen what the client does with it, and the
    mechanic above does not rely on it. Off by default."""
    if getattr(args, "spawn_protect_flag", "off") != "on":
        return False
    want = bool(sess._SESSION.get("protect_until"))
    if want == bool(sess._SESSION.get("protect_flag_sent")):
        return False
    base = getattr(args, "unit_state", None)
    v = (base if base is not None else 0) & 0xFFFFFFFF
    if sess._SESSION.get("player_dead"):
        v |= 0x800000
    v = (v | 0x1000) if want else (v & ~0x1000)
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">III", 0x100000, 0, v & 0xFFFFFFFF),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    sess._SESSION["protect_flag_sent"] = want
    print("[feworld]    -> 0x2024 CONDITION 0x%08X -- INVINCIBLE (0x1000) %s "
          "(--spawn-protect-flag, UNPROVEN)" % (v, "set" if want else "cleared"),
          flush=True)
    return True


#: RoD (fewiki Guide/メモ 2006-05-26): 10% of NEXT, 20% of the gold carried
DEFAULT_DEATH_EXP_PCT = 10.0
DEFAULT_DEATH_GOLD_PCT = 20.0
DEFAULT_DEATH_EXP_OF = "next"
#: RoD: a war death costs 3 crystals, floored at 0 (fewiki WAR/クリスタル,
#: the Netz lecture, Famitsu)
DEFAULT_DEATH_WAR_CRYSTALS = 3


#: The HUD EXP gauge's animation constant at 0x0525F5EC: float32(0.01).
GAUGE_STEP_K = 0.009999999776482582


def gauge_step(maxv):
    """The HUD EXP gauge's per-frame step for a cached max of `maxv`.

    (long)(maxv * float32(0.01)) at 0x050CE306, TRUNCATED: __ftol (0x051B7678)
    sets the x87 rounding control to 0xC. So 150000 -> 1499, not 1500 -- and
    any max under 101 gives 0, a bar that can never animate at all."""
    return int(max(0, int(maxv)) * GAUGE_STEP_K)


def death_penalty(conn, outbound, mode, be, args):
    """A death's personal loss, the RoD rules.

    HUNTING (peace, or no campaign): --death-exp-pct 10 of NEXT (the EXP the
    current level needs -- `--death-exp-of next`, RoD: 「Nextの10%の経験値」)
    and --death-gold-pct 20 of the gold CARRIED (the bank is safe -- the wiki
    tells you to bank it). The EXP comes out of PROGRESS INTO THE LEVEL and
    stops at 0: nobody loses a level here. WARNING: Whether RoD could de-level is
    NOT KNOWN -- no source says -- so clamping is the conservative reading.
    `--death-exp-of progress` = the first pass's share of the progress.

    WAR (a PREP or WAR field): no EXP or gold, -3 crystals carried
    (--death-war-crystals, floored at 0). TRUCE: nothing (SE 8/24, no loss in
    a ceasefire). Returns {what: lost} or None."""
    exp_pct = max(0.0, min(100.0, float(getattr(args, "death_exp_pct",
                                                DEFAULT_DEATH_EXP_PCT) or 0)))
    gold_pct = max(0.0, min(100.0, float(getattr(args, "death_gold_pct",
                                                 DEFAULT_DEATH_GOLD_PCT) or 0)))
    of = str(getattr(args, "death_exp_of", DEFAULT_DEATH_EXP_OF) or "next")
    ph = field_war_phase(args)
    if ph is not None and ph in _war_phases("PREP", "WAR"):
        return war_death_penalty(conn, outbound, mode, be, args, ph)
    if ph is not None and ph in _war_phases("TRUCE"):
        print("[feworld]    no death penalty: a death in a truce field costs "
              "nothing (SE 8/24)", flush=True)
        return None
    if exp_pct <= 0 and gold_pct <= 0:
        return None
    lost = {}
    pct_cfg = exp_pct
    if exp_pct > 0 and progression.level_model_on(args) and skilllist.self_class_id(args) is not None:
        # --exp-model level: EXP is PROGRESS INTO THE LEVEL per class. Take
        # 10% of NEXT out of it, stopping at 0 -- never a level -- and the
        # lifetime total by as much.
        cls, level, into, need, _served = progression.exp_view(args)
        base = need if of == "next" else into
        want = min(into, int(base * exp_pct / 100.0))
        if level >= progression.class_level_cap(args):
            # KEY: NOTHING AT THE CAP (2026-09-13): 「死んでも経験値減らなくなります」
            # -- at Lv.40 dying no longer costs EXP [Netz wiki 2006-05-05,
            # num/nz_solo.txt:146, RoD]. The gold loss still applies.
            print("[feworld]    no EXP loss: Lv%d is the level cap "
                  "(--class-level-max) -- RoD took no EXP at the cap"
                  % level, flush=True)
            want = 0
        # WARNING: ROUNDED DOWN TO WHOLE GAUGE STEPS (2026-09-12, live: "I died and
        # my exp bar went up to 100%"). When EXP goes DOWN the HUD gauge drains
        # its fill by a fixed step with NO FLOOR (0x050CE372 `sub edx, eax`),
        # until fill == exp. Our "stops at 0 into the level" clamp took exactly
        # the progress there was -- 2258 at 19:18Z -- and the drain went
        # 2258 -> 759 -> 759-1499, a u32 UNDERFLOW to ~4.29e9, drawn
        # (fill*170/max) as a full bar and effectively permanent. A loss that
        # is a whole number of steps lands the drain EXACTLY on the new value;
        # any other amount can overshoot below zero near the bottom of the bar
        # -- including RoD's own unclamped 10% of Next, which is 15000 while
        # ten TRUNCATED steps are 14990. But the drain can only go past zero
        # from a fill UNDER ONE STEP, so only a death that takes the progress
        # below one step is at risk -- the 19:18Z case exactly (2258 -> 0).
        # Every other death keeps RoD's exact number (fe_rod_numbers_test's
        # Lv10: 35 of 350, not 33); that one is rounded down to whole steps.
        s = gauge_step(need)
        loss = (want // s) * s if s > 0 and into - want < s else want
        if loss != want:
            print("[feworld]    death penalty %d -> %d: rounded down to whole "
                  "HUD gauge steps of %d, so the gauge's floorless drain "
                  "(0x050CE372) lands exactly instead of underflowing"
                  % (want, loss, s), flush=True)
        if loss > 0:
            progression.class_exp_store(args, cls, into - loss)
            total = max(0, progression.exp_seed(args) - loss)
            sess._SESSION["exp"] = total
            character._store_char_field(args, "exp", total)
            cls, level, into, need, served = progression.exp_view(args)
            wire.send(conn, outbound,
                 wire.inner_msg(0x1075, progression.exp_1075_body(nxt=need, exp_rows=[(cls, served)]),
                           wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            lost["exp"] = loss
        exp_pct, of = 0, "%s (level model)" % of
    if exp_pct > 0:
        # --exp-model flat (the 09-09 display-only bar): there is no real
        # NEXT, so this keeps the old rule -- a share of the lifetime total
        of = "the lifetime total (flat model)"
        total = progression.exp_seed(args)
        loss = int(total * exp_pct / 100.0)
        if loss > 0:
            new = total - loss
            sess._SESSION["exp"] = new
            character._store_char_field(args, "exp", new)
            wire.send(conn, outbound,
                 wire.inner_msg(0x1075, struct.pack(">II", 0x2,
                                               progression.exp_denominator(args, new)
                                               & 0xFFFFFFFF),
                           wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            progression.exp_numerator_push(conn, outbound, mode, be, args, new)
            lost["exp"] = loss
    if gold_pct > 0:
        gold = character._seeded_value(args, "gold", getattr(args, "gold", None))
        if gold is not None:
            loss = int(int(gold) * gold_pct / 100.0)
            if loss > 0 and character._store_char_field(args, "gold", int(gold) - loss):
                wallet.wallet_push(conn, outbound, mode, be, args)
                lost["gold"] = loss
    print("[feworld]    DEATH PENALTY (hunting): %s (--death-exp-pct %g of %s, "
          "--death-gold-pct %g of the gold carried -- RoD: 10%% of NEXT, 20%% "
          "of gold; the EXP stops at 0 into the level, never a level)"
          % (", ".join("-%d %s" % (v, k) for k, v in sorted(lost.items()))
             or "nothing to take", pct_cfg, of, gold_pct), flush=True)
    return lost


def war_death_penalty(conn, outbound, mode, be, args, phase=None):
    """A death in a PREP/WAR field: no EXP, no gold [fewiki メモ: 戦争時 :
    経験値や金のロストは無い], -3 crystals carried floored at 0 (fewiki
    WAR/クリスタル, RoD), and the death drains the side's base
    (fecampaign.base_hit, --base-dots: 1 of 480 dots). Returns
    {what: lost}."""
    lost = {}
    n = max(0, int(getattr(args, "death_war_crystals",
                           DEFAULT_DEATH_WAR_CRYSTALS) or 0))
    if n > 0:
        have = character._seeded_value(args, "crystal", getattr(args, "crystal", None))
        if have is not None and int(have) > 0:
            take = min(int(have), n)
            if character._store_char_field(args, "crystal", int(have) - take):
                wallet.crystal_push(conn, outbound, mode, be, args,
                             value=int(have) - take)
                lost["crystal"] = take
    mod = sys.modules.get("fecampaign")
    side = spawns.spawn_side(args, sess._SESSION.get("field")) if mod is not None else None
    if mod is not None and side in ("atk", "def") and hasattr(mod, "base_hit"):
        try:
            hp = mod.base_hit(args, sess._SESSION.get("field"), side, "death",
                              conn=conn, outbound=outbound, mode=mode, be=be)
        except Exception as e:                         # noqa: BLE001
            hp = None
            print("[feworld]    war death: base damage failed: %s" % e,
                  flush=True)
        if hp is not None:
            lost["base_hp_left"] = hp
    print("[feworld]    DEATH PENALTY (war, phase %s): %s -- no EXP or gold "
          "in a war (RoD); -%d crystals (--death-war-crystals), and the %s "
          "base takes a death's damage"
          % (phase, ", ".join("%s %s" % (k, v) for k, v in sorted(lost.items()))
             or "nothing carried", n, side or "?"), flush=True)
    return lost
