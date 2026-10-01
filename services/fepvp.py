"""fepvp.py -- Fantasy Earth PLAYER VERSUS PLAYER (2026-09-11).

PARTIAL: BUILT 2026-09-11, NOT LIVE-TESTED. ON by default since 2026-09-11 (--pvp). Harness:
tools/fe_pvp_test.py.

FE is a nation-vs-nation war game and until now nobody could hit anybody. The
piece that was missing first was not combat -- it was a TARGET: no other
player existed in your client at all (see fepresence.py). With peers drawn under
their charid, a swing at one arrives here as an ordinary hit notify naming an
id we can resolve to a session.

WHY THE SERVER HAS TO SETTLE IT. `0xA011` is only ever sent by the ATTACKER's
client, and only when the attacker is the LOCAL player: the builder's gate
(0x05069700) tests `attacker id == [0x533998c]+0x9e0`. Nothing tells the
victim's client it was hit, and nothing tells it how hard. So the flow is
attacker -> server -> victim, exactly as monsters already work, and the same
three server-side pieces do the work:

    fw.player_hp_push()    0x2024 maskA 0x4 -- the NEW hp; the client draws
                           the difference as the damage number
    fw.player_dead_push()  CONDITION 0x800000
    fw.revive_tick()       stands them back up after --revive-secs

WHAT LANDS WHERE (three threads, no shared mutation):
  attacker thread  on_hit: resolve the target to a peer session in the same
                   (field, room), check the nation rule, post "pvp.hit"
  victim thread    on_hit_relay: ITS session owns its HP -- protection and
                   death are decided here, where spawn_protected() and the
                   store are correct, then it posts "pvp.report" back
  attacker thread  on_report_relay: log, and (--pvp-show-damage on) push the
                   victim's new HP onto the victim's AVATAR in this client, so
                   the attacker sees a damage number over the target

WARNING: THE REVIVE IS OURS TO DRIVE. `fw.revive_tick` is only called from
`monster_attack_tick`, which is gated on `--combat on` AND `--monster-attack
on`. A player killed by another player with monster combat off would stay
down forever, so this module pumps the revive itself whenever a session is
dead and the monster tick is not running.

DAMAGE (2026-10-01, audit 26; --pvp-model table, the default): the same
numbers a monster hit uses. The ATTACKER's thread takes damage.
player_hit_damage -- its ATTACK (class base + weapon) x the skill's POWER %
x the level-gap row for (victim level - attacker level) -- and the VICTIM's
thread applies what only it knows: armour and the resistance statuses, and
the crouch multiplier (damage.taken_modifiers). The reach is the skill's
own: SKILL_DATA range +0xF8 plus radius +0x134 (the child's radius when the
skill explodes), radius only when the skill is AIMED (+0x1d8 bit 0x800, the
attack centred on the aim point), plus --pvp-slack for a position up to one
heartbeat stale. The skill's bad statuses land on the victim (world/
status.py). --pvp-model flat restores the old flat --pvp-damage /
--hit-damage and the one --pvp-range sphere for every skill.

CHOSEN, NOT MEASURED:
  * the level-gap row is fet_npc_lv_diff_correction, a MONSTER table: no
    player-vs-player table ships, so the same row is used with the victim's
    level in the monster's place (equal levels read 1.4).
  * a hit is refused between players of the SAME nation unless
    `--pvp-friendly on`. RoD was nation-vs-nation; friendly fire is not.
  * the spawn-protection window and the death penalty are the monster rules,
    unchanged, because a death is a death.

WARNING: WHAT IS UNPROVEN AND WILL SHOW UP FIRST ON A SCREEN:
  * that the client SENDS 0xA011 at all for a served avatar. Its other gates
    (class tag 0x3E8, the target's invincibility window, the skill resolving)
    were read on a monster target; an avatar may satisfy them or may not. If
    no `0xA011 PvP` line appears in the log when one player swings at
    another, that is the finding and nothing below it matters.
  * that `0x2024` maskA 0x4 addressed to a kind-2 avatar draws a damage
    number. The applier resolves the unit by id and the bit-0x4 arm subtracts
    from +0x49e, which exists on any unit -- but the avatar carries no stat
    block unless `!presence hp on` gave it one, so the first number may be
    drawn from a zero. `--pvp-show-damage off` stops sending it.

OFF BY DEFAULT, AND FLIPPABLE LIVE. `--pvp off` is the shipped default; the
operator turns it on mid-session with `!pvp on` through --gmcmd-file, which
needs no restart and so cannot cost anyone a live field.
"""
import math
import struct
import sys
import time

import fegamedata
from world import damage as _damage_mod, staff as _staff, status as _status

fw = None                       # the feworld module, handed in by register()

#: None = follow --pvp; True/False = the operator said so with `!pvp`
_FORCE = [None]


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0xA011, on_hit, override=True)
    fw.register_relay("pvp.hit", on_hit_relay)
    fw.register_relay("pvp.report", on_report_relay)
    fw.register_pump(pump)
    fw.register_gm(gm)
    fw.register_args(add_args)


def add_args(ap):
    ap.add_argument("--pvp-range", type=float, default=10.0, metavar="UNITS",
                    help="KEY: THE CAST PATH (2026-09-12). A hit on an AVATAR is "
                         "never reported by the victim's client: the avatar "
                         "class's on-hit (0x050688A0) sends nothing, monsters "
                         "send 0xA011, buildings 0x2019. Live 2026-09-12: 30 h "
                         "of two players attacking each other, ZERO 0xA011. So "
                         "player damage is settled here from the CASTER's own "
                         "0x1031/0x1032 SKILL USE: every enemy peer within this "
                         "many units of the cast position takes --pvp-damage "
                         "(a targeted skill, c != 0, hits only its target). "
                         "KEY: 10.0 IS THE CLIENT'S OWN NUMBER (static "
                         "2026-09-12): every attack object carries a collision "
                         "sphere whose radius is the shape ctor's default 10.0 "
                         "(0x0502D1E9, never rewritten in the attack code), "
                         "centred on the attack's position -- the caster's "
                         "spot, or the aim point when the skill's +0x1d8 "
                         "bitfield has bit 0x800 (0x0501E2F8). The per-unit "
                         "arm (0x05021910) has NO distance test of its own: "
                         "type 0 hits the locked target, types 1-3 hit "
                         "whatever the sphere pass hands it. Unread: the "
                         "projectile's travel (speed x [effect+0x34] ms) "
                         "before it stops, so a shot that lands far away "
                         "is under-reached here. 0 turns the cast path off. "
                         "`!pvp range N` live.")
    ap.add_argument("--pvp-war", default="on", choices=("on", "off"),
                    help="only while a campaign WAR is on in this field "
                         "(fecampaign phase 2) -- RoD's rule: no player damage "
                         "in a capital, a peace field or during prep/truce. "
                         "`off` = anywhere, any time (needs no --campaign). "
                         "`!pvp war on|off` live.")
    ap.add_argument("--pvp", default="on", choices=("on", "off"),
                    help="players can damage each other (0xA011 aimed at "
                         "another player's avatar). WARNING: ON by default since "
                         "2026-09-11 -- it first shipped off and was flipped "
                         "so it can be tested live without a knob dance. "
                         "Needs fepresence (--unit-id auto): with no peer "
                         "drawn there is nothing to swing at. `!pvp off` "
                         "stops it live. PARTIAL: still not live-tested: whether "
                         "the client even SENDS 0xA011 at a served avatar is "
                         "the open question.")
    ap.add_argument("--pvp-damage", type=int, default=0, metavar="N",
                    help="damage one player hit does. 0 (default) = whatever "
                         "--hit-damage is, i.e. what this player does to a "
                         "monster. No weapon/skill/defence model exists yet.")
    ap.add_argument("--pvp-model", default="table", choices=("table", "flat"),
                    help="table (default, 2026-10-01): a player hit is the "
                         "attacker's ATTACK x the skill's POWER x the level "
                         "row (damage.player_hit_damage), less the victim's "
                         "armour and statuses, more if they are crouched; the "
                         "reach is the skill's own range + radius from "
                         "SKILL_DATA. flat = the old behaviour: --pvp-damage "
                         "(or --hit-damage) for every hit, one --pvp-range "
                         "sphere for every skill. `!pvp model flat` live.")
    ap.add_argument("--pvp-slack", type=float, default=3.0, metavar="UNITS",
                    help="--pvp-model table: added to a skill's reach for a "
                         "peer position up to one heartbeat (~2.5 s) stale "
                         "(CHOSEN, the same 3 u the chest pickup allows).")
    ap.add_argument("--pvp-friendly", default="off", choices=("on", "off"),
                    help="allow hits between players of the SAME nation. "
                         "Default off -- RoD was nation-vs-nation.")
    ap.add_argument("--pvp-show-damage", default="on", choices=("on", "off"),
                    help="push the victim's new HP onto their avatar in the "
                         "ATTACKER's client (0x2024 maskA 0x4), so the "
                         "attacker sees a damage number. Unproven on a "
                         "kind-2 avatar; `off` makes a hit silent but still "
                         "real.")


# ---------------------------------------------------------------------------
def _log(msg):
    print("[fepvp] " + msg, flush=True)


_ONCE = set()


def _once(key, msg):
    if key in _ONCE:
        return
    _ONCE.add(key)
    _log(msg)


def _on(args):
    if _FORCE[0] is not None:
        return _FORCE[0]
    return getattr(args, "pvp", "on") == "on"


def _active(args):
    """ON, and the ids mean something (fepresence's rule, same reason)."""
    return _on(args) and str(getattr(args, "unit_id", "0")) == "auto"


def _presence():
    return sys.modules.get("fepresence")


def _flat_damage(args):
    n = int(getattr(args, "pvp_damage", 0) or 0)
    if n > 0:
        return n
    return max(1, int(getattr(args, "hit_damage", 25) or 25))


def _model(args):
    return "flat" if getattr(args, "pvp_model", "table") == "flat" else "table"


def _hit_model(args):
    """What the VICTIM is told to do with a hit: "table" = apply its own
    armour/status/crouch modifiers; "flat" = take the number as sent (the
    flat model, or a --pvp-damage pin)."""
    if _model(args) == "flat" or int(getattr(args, "pvp_damage", 0) or 0) > 0:
        return "flat"
    return "table"


def _damage(args, skill=None, victim=None):
    """(damage, why) of one hit by THIS session's player with `skill` on the
    peer session `victim`. --pvp-damage N > 0 still pins a flat N."""
    if _model(args) == "flat" or int(getattr(args, "pvp_damage", 0) or 0) > 0:
        n = _flat_damage(args)
        return n, "flat %d" % n
    lvl = None
    if victim is not None:
        try:
            lvl = int(victim.get("pvp_level") or 0) or None
        except (TypeError, ValueError):
            lvl = None
    if lvl is None:
        lvl = int(_staff.char_level(args))
    return _damage_mod.player_hit_damage(args, {"level": lvl}, skill=skill)


def _reach(args, skill, targeted):
    """How far from the attack's centre a skill hits: --pvp-range under
    --pvp-model flat; else range + radius (radius alone when AIMED) plus
    --pvp-slack. A skill with no SKILL_DATA row falls back to --pvp-range."""
    flat = float(getattr(args, "pvp_range", 10.0) or 0.0)
    if _model(args) == "flat":
        return flat
    rng, rad = fegamedata.skill_radius(skill)
    if not rng and not rad:
        return flat
    aimed = bool(fegamedata.skills().get(int(skill), {}).get("sel1d8", 0) & 0x800)
    reach = rad if (aimed and not targeted) else rng + rad
    return reach + float(getattr(args, "pvp_slack", 3.0) or 0.0)


def self_cast(ctx, skill):
    """The caster's own statuses from a cast (any 0x1031/0x1032, PvP or
    not): a GOOD status (Hide, chant, Ender Pain) lands on the caster; a
    damaging skill ends the caster's Hide (CHOSEN, FEZ). Returns the kinds
    set."""
    args = ctx.args
    if not _status.on(args) or skill is None:
        return []
    s = fw._SESSION
    good = _status.good_specs(skill)
    if good:
        return _status.player_apply(ctx.conn, ctx.outbound, ctx.mode, ctx.be,
                                    args, good, src=s.get("charid"),
                                    skill=skill, why="own skill %d" % skill)
    if _status.has(s, "hide") and fegamedata.skill_power(skill) > 0:
        _status.table(s).pop("hide", None)
        _log("charid %s attacked (skill %d) -- Hide ends" % (s.get("charid"), skill))
        _status.cond_sync(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
    return []


def _my_nation(args):
    try:
        return int(fw.nation_of(args) or 0)
    except Exception:                                   # noqa: BLE001
        return 0


def _nation_of_session(ps):
    card = ps.get("pres_card") or {}
    try:
        return int(card.get("force") or 0)
    except (TypeError, ValueError):
        return 0


# ---------------------------------------------------------------------------
# the attacker's thread
# ---------------------------------------------------------------------------
def on_hit(ctx, inner):
    """0xA011 MSG_NPC_HIT_NOTIFY. Handled ONLY when the target resolves to
    another player standing here; anything else is declined so feworld's own
    monster/keep arm answers it exactly as before.

    WARNING: MUST NOT RAISE: ext_dispatch counts a raising handler as HANDLED, and
    the monster path would then be starved of every hit."""
    try:
        args = ctx.args
        f = inner[2:]
        if len(f) < 19 or not _active(args):
            return False
        target = struct.unpack_from(">I", f, 0)[0]
        skill = struct.unpack_from(">H", f, 16)[0]
        pres = _presence()
        if pres is None:
            return False
        victim = pres.peer_session(target)
        if victim is None:
            return False                   # a monster, a keep, or nobody
        me = fw._SESSION.get("charid")
        mine, theirs = _my_nation(args), _nation_of_session(victim)
        if (mine and theirs and mine == theirs
                and getattr(args, "pvp_friendly", "off") != "on"):
            _log("charid %s swung at charid %d -- SAME NATION (%d), no damage "
                 "(--pvp-friendly off)" % (me, target, mine))
            return True
        if _status.on(args):
            why = _status.refusal(fw._SESSION)
            if why:
                _log("charid %s is %s -- the hit on charid %d does nothing"
                     % (me, why, target))
                return True
            if _status.hidden_from(fw._SESSION, victim):
                _log("charid %d is HIDDEN -- it can't be target-locked, no "
                     "damage" % target)
                return True
        dmg, dwhy = _damage(args, skill, victim)
        n = fw.ext_post("pvp.hit", {"from": me, "to": int(target),
                                    "dmg": dmg, "skill": int(skill),
                                    "model": _hit_model(args)},
                        to=lambda s, _n: s is victim)
        _log("charid %s HIT charid %d for %d (skill %d: %s) -- posted to %d "
             "session(s); their own thread applies it"
             % (me, target, dmg, skill, dwhy, n))
        return True
    except Exception as e:                              # noqa: BLE001
        _log("0xA011 handler failed (%r) -- declined, feworld's arm answers" % (e,))
        return False


def _peer_pos(ps):
    mv = ps.get("pres_mv")
    if not mv or len(mv) < 24:
        return None
    x, y, z = struct.unpack_from(">hhh", mv, 18)
    return (x * 0.1, y * 0.1, z * 0.1)


def _dist(p, q):
    return math.sqrt(sum((a - b) * (a - b) for a, b in zip(p, q)))


def on_cast(ctx, inner):
    """0x1031/0x1032 SKILL USE from feprog.on_cast: settle player hits HERE.

    The record (feprog.decode_cast): [u32 a][u32 b][u8][u32][u32][u16 skill]
    [u8][u32 c = target, 0 for an attack][u32 weapon uid][f32 x3 pos when
    c == 0]. Every enemy peer in this (field, room) within --pvp-range of the
    cast position is posted a "pvp.hit" -- the victim's own thread applies it
    (on_hit_relay), exactly as the 0xA011 path does. Returns how many were
    posted. Never raises out (feprog's tap is inside a try as well)."""
    try:
        args = ctx.args
        f = inner[2:]
        if len(f) >= 19:
            try:
                self_cast(ctx, struct.unpack_from(">H", f, 17)[0])
            except Exception as e:                      # noqa: BLE001
                _log("status on cast failed (%r) -- the cast still counts" % (e,))
        if not _active(args):
            return 0
        rng = float(getattr(args, "pvp_range", 10.0) or 0.0)
        if rng <= 0:
            return 0
        if len(f) < 28:
            return 0
        a, b, _flag, _t1, _t2, skill, _one, c, _wpn = struct.unpack_from(
            ">IIBIIHBII", f, 0)
        s = fw._SESSION
        if _status.on(args):
            why = _status.refusal(s)
            if why:
                _log("charid %s cast skill %d while %s -- no player damage"
                     % (s.get("charid"), skill, why))
                return 0
        me = int(s.get("charid") or 0)
        if not me or not s.get("in_field"):
            return 0
        if s.get("pvp_cast_seen") == (a, b, skill):
            return 0                     # the client repeats a cast record
        s["pvp_cast_seen"] = (a, b, skill)
        if getattr(args, "pvp_war", "on") == "on":
            camp = sys.modules.get("fecampaign")
            if camp is None or getattr(args, "campaign", "off") != "on":
                _once("war-gate", "cast by charid %d: --pvp-war on but no "
                      "campaign runs -- no player damage anywhere (--pvp-war "
                      "off to allow it)" % me)
                return 0
            area = camp._here(args)
            if area is None or camp.state_of(area).get("phase") != camp.WAR:
                s["pvp_cast_nowar"] = int(s.get("pvp_cast_nowar", 0) or 0) + 1
                if s["pvp_cast_nowar"] <= 2:
                    _log("cast by charid %d (skill %d): not at war here -- no "
                         "player damage (--pvp-war on)" % (me, skill))
                return 0
        if c == 0 and len(f) >= 40:
            pos = struct.unpack_from(">fff", f, 28)
        else:
            pos = _peer_pos(s)
        if pos is None:
            return 0
        key = (s.get("field"), s.get("room"))
        mine = _my_nation(args)
        friendly = getattr(args, "pvp_friendly", "off") == "on"
        rng = _reach(args, skill, bool(c))
        n = 0
        for ent in fw.ext_sessions():
            ps = ent["session"]
            cid = int(ps.get("charid") or 0)
            if not cid or cid == me or not ps.get("in_field"):
                continue
            if (ps.get("field"), ps.get("room")) != key:
                continue
            if c and int(c) != cid:
                continue                 # a targeted skill hits its target
            ppos = _peer_pos(ps)
            if ppos is None:
                continue
            d = _dist(pos, ppos)
            if d > rng:
                continue
            theirs = _nation_of_session(ps)
            if mine and theirs and mine == theirs and not friendly:
                continue
            if c and _status.on(args) and _status.hidden_from(s, ps):
                continue                 # manual p.38: Hide can't be locked
            dmg, dwhy = _damage(args, skill, ps)
            k = fw.ext_post("pvp.hit", {"from": me, "to": cid, "dmg": dmg,
                                        "skill": int(skill),
                                        "model": _hit_model(args)},
                            to=lambda s_, _n, ps=ps: s_ is ps)
            if k:
                n += k
                _log("charid %d cast skill %d at (%.1f, %.1f, %.1f): charid %d "
                     "is %.1f u away (reach %.1f) -- HIT for %d (%s), their "
                     "thread applies it" % (me, skill, pos[0], pos[1], pos[2],
                                            cid, d, rng, dmg, dwhy))
        if not n:
            k = int(s.get("pvp_cast_miss", 0) or 0) + 1
            s["pvp_cast_miss"] = k
            if k <= 3 or k % 100 == 0:
                _log("charid %d cast skill %d at (%.1f, %.1f, %.1f): no enemy "
                     "peer within %.1f u" % (me, skill, pos[0], pos[1], pos[2], rng))
        return n
    except Exception as e:                              # noqa: BLE001
        _log("cast handler failed (%r) -- the cast still counts" % (e,))
        return 0


def on_report_relay(ctx, payload):
    """Back on the ATTACKER's thread: what the victim's side decided."""
    cid = int(payload.get("to") or 0)
    hp, hpmax = payload.get("hp"), payload.get("hpmax")
    why = payload.get("why") or ""
    if why:
        _log("charid %d: %s" % (cid, why))
        return
    _log("charid %d is at HP %s/%s%s"
         % (cid, hp, hpmax, " -- DEAD" if payload.get("dead") else ""))
    _credit_war(ctx, payload)
    if getattr(ctx.args, "pvp_show_damage", "on") != "on" or hp is None:
        return
    drawn = cid in (ctx.session.get("pres_seen") or {})
    if not drawn:
        _log("   not drawing the damage: charid %d is not on this screen" % cid)
        return
    # the SAME channel a keep's damage rides: [u32 maskA 0x4][u32 0][i16 hp],
    # the NEW value, addressed to the object -- here the peer's avatar
    fw.building_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                        cid, hp)
    _log("   -> 0x2024 maskA 0x4 hp=%d on avatar %d (the client draws the "
         "difference as the damage number)" % (hp, cid))


def _credit_war(ctx, payload):
    """The war result's PC-damage and kill columns, credited to the ATTACKER.

    This runs on the attacker's own thread with the victim's verdict in hand,
    so the number credited is what LANDED (`dealt`) rather than what was
    swung. Only a live war counts -- fecampaign.pc_damage declines anything
    else, which is also RoD's rule (there is no PvP outside a war field)."""
    dealt = int(payload.get("dealt") or 0)
    if dealt <= 0 and not payload.get("dead"):
        return
    camp = sys.modules.get("fecampaign")
    if camp is None or not hasattr(camp, "pc_damage"):
        return
    try:
        area = camp._here(ctx.args)
        got = camp.pc_damage(ctx.args, area, dealt, kill=bool(payload.get("dead")),
                             victim=payload.get("to"))
    except Exception as e:                              # noqa: BLE001
        _log("   war score credit failed (%r) -- the damage still landed" % (e,))
        return
    if got is not None:
        _log("   war score: %d PC damage, %d kill(s) this war" % got)


# ---------------------------------------------------------------------------
# the victim's thread -- ITS session owns its HP
# ---------------------------------------------------------------------------
def on_hit_relay(ctx, payload):
    args = ctx.args
    s = fw._SESSION
    attacker = int(payload.get("from") or 0)
    dmg = max(1, int(payload.get("dmg") or 1))

    def report(**kw):
        fw.ext_post("pvp.report", dict(kw, to=int(s.get("charid") or 0)),
                    to=lambda ps, _n: int(ps.get("charid") or 0) == attacker)

    if not s.get("in_field"):
        report(why="not in a field -- no damage")
        return
    if s.get("player_dead"):
        report(why="already down -- no damage")
        return
    if fw.spawn_protected(args):
        _log("charid %s was hit by charid %d while PROTECTED -- no damage "
             "(--spawn-protect-secs)" % (s.get("charid"), attacker))
        report(why="protected (spawn window) -- no damage")
        return
    hpmax = fw.player_hp_max(args)
    if "player_hp" not in s:
        s["player_hp"] = hpmax
    if payload.get("model") == "table":
        # what only the VICTIM's thread knows: its armour, its statuses, its
        # posture (damage.taken_modifiers, the same as a monster's hit)
        dmg, twhy = _damage_mod.taken_modifiers(args, dmg, "swing %d" % dmg, s)
        _log("charid %s: %s -> %d" % (s.get("charid"), twhy, dmg))
    before = int(s.get("player_hp", hpmax))
    hp = max(0, before - dmg)
    s["player_hp"] = hp
    # what LANDED, not what was swung -- the last hit of a kill is clamped by
    # the hp that was left, and the result screen's PC damage is a sum of
    # landed damage (SE warsystem04 「敵国のプレイヤーに与えたダメージ」)
    dealt = before - hp
    fw.player_hp_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, hp)
    who = ("the war's tower %d" % payload["tower"]) if payload.get("tower")         else "charid %d" % attacker
    _log("charid %s took %d from %s -- HP %d/%d"
         % (s.get("charid"), dmg, who, hp, hpmax))
    if hp > 0:
        if _status.on(args):
            _status.player_hit_taken(ctx.conn, ctx.outbound, ctx.mode, ctx.be,
                                     args, who)
            sk = payload.get("skill")
            if sk is not None and not payload.get("tower"):
                _status.player_apply(ctx.conn, ctx.outbound, ctx.mode, ctx.be,
                                     args, _status.bad_specs(int(sk)),
                                     src=attacker, skill=int(sk),
                                     why="%s's skill %d" % (who, int(sk)))
        report(hp=hp, hpmax=hpmax, dead=False, dealt=dealt)
        return
    s["player_dead"] = True
    s["revive_at"] = time.monotonic() + float(getattr(args, "revive_secs", 8) or 8)
    fw.player_dead_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, True)
    # the RoD respawn wait, the same as a monster kill's (feworld)
    fw.respawn_wait_arm(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
    _log("charid %s was KILLED by %s -- reviving in %.0fs"
         % (s.get("charid"), who, float(getattr(args, "revive_secs", 8) or 8)))
    # a death is a death: the same penalty a monster kill costs
    fw.death_penalty(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args)
    report(hp=hp, hpmax=hpmax, dead=True, dealt=dealt)


def pump(ctx):
    """Stand a PvP-killed player back up. feworld's own revive_tick only runs
    inside monster_attack_tick (--combat on AND --monster-attack on), so with
    monster combat off a PvP death would never end."""
    try:
        if not _active(ctx.args):
            return
        s = fw._SESSION
        now = time.monotonic()
        if s.get("in_field") and now - float(s.get("pvp_level_at", -1e9)) > 10.0:
            # this player's level, published for an ATTACKER's thread
            # (_damage reads it off this dict; it can't read our store)
            s["pvp_level_at"] = now
            try:
                s["pvp_level"] = int(_staff.char_level(ctx.args))
            except Exception:                           # noqa: BLE001
                s["pvp_level"] = None
        if not fw._SESSION.get("player_dead"):
            return
        if (getattr(ctx.args, "combat", "off") == "on"
                and getattr(ctx.args, "monster_attack", "off") == "on"):
            return                      # the monster tick already drives it
        fw.revive_tick(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args)
    except Exception as e:                              # noqa: BLE001
        _log("revive pump failed (%r) -- session kept" % (e,))


# ---------------------------------------------------------------------------
def gm(ctx, line):
    if not line.startswith("!pvp"):
        return False
    words = line.split()
    verb = words[1].lower() if len(words) > 1 else "status"
    if verb in ("on", "off"):
        _FORCE[0] = (verb == "on")
        _log("!pvp %s -- server-wide, for every session, until the next "
             "restart (the compose default is --pvp %s)"
             % (verb, getattr(ctx.args, "pvp", "off")))
        if verb == "on" and str(getattr(ctx.args, "unit_id", "0")) != "auto":
            _log("   WARNING: but --unit-id is not auto, so no peer is drawn and "
                 "nothing can be swung at -- fepresence is off too")
        return True
    if verb == "damage" and len(words) > 2:
        try:
            ctx.args.pvp_damage = int(words[2], 0)
        except ValueError:
            _log("!pvp damage N -- N must be a number")
            return True
        _log("!pvp damage: one hit now does %d" % _flat_damage(ctx.args))
        return True
    if verb == "model" and len(words) > 2:
        ctx.args.pvp_model = "flat" if words[2].lower() == "flat" else "table"
        _log("!pvp model %s" % ctx.args.pvp_model)
        return True
    if verb == "range" and len(words) > 2:
        try:
            ctx.args.pvp_range = float(words[2])
        except ValueError:
            _log("!pvp range N -- N must be a number (units)")
            return True
        _log("!pvp range: a cast hits enemy peers within %.1f u" % ctx.args.pvp_range)
        return True
    if verb == "war" and len(words) > 2:
        ctx.args.pvp_war = "on" if words[2].lower() == "on" else "off"
        _log("!pvp war %s" % ctx.args.pvp_war)
        return True
    if verb == "friendly" and len(words) > 2:
        ctx.args.pvp_friendly = "on" if words[2].lower() == "on" else "off"
        _log("!pvp friendly %s" % ctx.args.pvp_friendly)
        return True
    _log("!pvp: %s (--pvp %s, %s), model %s, flat damage %d, friendly %s, "
         "show-damage %s"
         % ("ON" if _on(ctx.args) else "OFF", getattr(ctx.args, "pvp", "off"),
            "forced by !pvp" if _FORCE[0] is not None else "from the flag",
            _model(ctx.args), _flat_damage(ctx.args),
            getattr(ctx.args, "pvp_friendly", "off"),
            getattr(ctx.args, "pvp_show_damage", "on")))
    _log("   cast path: range %.1f u, war gate %s"
         % (float(getattr(ctx.args, "pvp_range", 6.0) or 0),
            getattr(ctx.args, "pvp_war", "on")))
    _log("   verbs: !pvp on|off, !pvp damage N, !pvp friendly on|off, "
         "!pvp range N, !pvp war on|off, !pvp model table|flat")
    return True
