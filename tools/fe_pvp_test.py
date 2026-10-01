#!/usr/bin/env python3
"""fe_pvp_test.py -- Fantasy Earth PLAYER VERSUS PLAYER (services/fepvp.py),
two sessions on two threads, no client. Run from the repo root:
    python tools/fe_pvp_test.py [-v]

One player swings at another's avatar (0xA011 with the peer's charid as the
target), and the checks follow it all the way round:

    attacker thread  0xA011 -> resolve the peer, nation rule, post "pvp.hit"
    victim thread    HP subtracted on ITS session, 0x2024 maskA 0x4 (the NEW
                     hp) to its own unit, death + revive, "pvp.report" back
    attacker thread  0x2024 maskA 0x4 on the VICTIM's avatar id (the damage
                     number over the target)

and the refusals that matter: off by default, same nation, spawn protection,
a hit at a monster id still reaching feworld's own arm.
"""
import os
import queue
import struct
import sys
import threading
import types

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import fenet       # noqa: E402
import feworld     # noqa: E402
import fepresence  # noqa: E402
import fepvp       # noqa: E402

feworld.load_extensions()

REPORT = sys.stdout
FAILS = []


def check(what, cond, detail=""):
    if cond:
        print("  %-66s PASS" % what, file=REPORT)
    else:
        print("  %-66s FAIL %s" % (what, detail), file=REPORT)
        FAILS.append(what)


CHARS = {
    7: {"name": "Lex", "sex": 0, "look1": 3, "f28": 0, "force": 1, "charid": 7},
    8: {"name": "Bob", "sex": 1, "look1": 2, "f28": 0, "force": 2, "charid": 8},
    9: {"name": "Ally", "sex": 0, "look1": 1, "f28": 0, "force": 1, "charid": 9},
}
STORE = {}
OUT = {}


def _stub_store():
    def _cid():
        return feworld._SESSION.get("charid")
    feworld._self_char = lambda a: CHARS.get(_cid())
    feworld._load_char_field = lambda a, k, d=None: (
        CHARS.get(_cid(), {}).get(k) if k == "force"
        else STORE.get(_cid(), {}).get(k, d))
    feworld._store_char_field = lambda a, k, v: True


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.setdefault(threading.get_ident(), []).append((msg, unit, body[6:]))


def _out():
    return OUT.setdefault(threading.get_ident(), [])


def _args(**kw):
    a = dict(unit_id="auto", seq_mode="count", world_prefix=4, monster_base=400,
             presence="on", presence_stale=10.0, presence_submask=0x2FF,
             presence_gear="off", presence_hp="off",
             pvp="on", pvp_damage=0, pvp_friendly="off", pvp_show_damage="on",
             # the checks below pin the OLD flat model (one number, one 10 u
             # sphere); --pvp-model table has its own section (table_checks)
             pvp_model="flat", status_effects="off",
             hit_damage=25, combat="off", monster_attack="off",
             revive_secs=8.0, unit_state=None,
             death_exp_pct=0, death_gold_pct=0, death_exp_of="next",
             war="offensive", war_fields="0,0,0,0", campaign="off",
             hp_max=None, class_levels="self:30")
    a.update(kw)
    return types.SimpleNamespace(**a)


def ctx_of(a):
    return feworld.Ctx(None, None, "ecb", False, a)


def hb(tick, pos):
    return (struct.pack(">IIIH", 0, tick, tick + 1, 0)
            + struct.pack(">hh", 1000, 0)
            + struct.pack(">hhh", *(int(round(v * 10)) for v in pos))
            + struct.pack(">I", 0))


def enter(charid, field=11, room=-1):
    s = feworld._SESSION
    s["account"] = "member:%d" % charid
    s["charid"] = charid
    s["in_field"] = True
    s["field"] = field
    s["room"] = room
    s["field_ready"] = True
    s.pop("player_dead", None)
    s.pop("player_hp", None)
    s.pop("protect_until", None)
    feworld._chat_room_join()
    feworld._chat_room_name(CHARS.get(charid, {}).get("name", "?"))


def see(a, tick, pos):
    """one heartbeat + one presence pump on THIS thread"""
    feworld.ext_dispatch(ctx_of(a), 0x2023, struct.pack(">H", 0x2023) + hb(tick, pos))
    fepresence.pump(ctx_of(a))


def swing(a, target):
    """0xA011 through the REAL seam: [u32 target][u32 attacker][u32][u32]
    [u16 skill][u8 kind][f32 x3] (feworld.combat_hit's own decode)."""
    body = (struct.pack(">IIII", target, feworld._SESSION.get("charid") or 0, 0, 0)
            + struct.pack(">HB", 270, 1) + struct.pack(">fff", 0.0, 0.0, 0.0))
    return feworld.ext_dispatch(ctx_of(a), 0xA011, struct.pack(">H", 0xA011) + body)


def pvp_pump(a):
    """ext_pump runs EVERY module's pump, so other modules' once-per-session
    pushes (feunit's 0x1070 cost table, presence relays) ride along. The
    checks below filter for the ids under test rather than pretending this
    session is quiet."""
    feworld.ext_pump(ctx_of(a))


def only(rows, *mids):
    return [(m, u) for m, u, _ in rows if m in mids]


def bodies(rows, mid):
    return [b for m, _u, b in rows if m == mid]


class _Worker(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.tasks, self.results = queue.Queue(), queue.Queue()

    def run(self):
        while True:
            fn = self.tasks.get()
            if fn is None:
                return
            try:
                self.results.put((True, fn()))
            except BaseException as e:            # noqa: BLE001
                import traceback
                traceback.print_exc()
                self.results.put((False, e))

    def do(self, fn):
        self.tasks.put(fn)
        good, val = self.results.get(timeout=10)
        if not good:
            raise val
        return val


def decode_2024(body):
    maskA, maskB = struct.unpack_from(">II", body, 0)
    rest = body[8:]
    val = struct.unpack_from(">h", rest, 0)[0] if len(rest) == 2 else None
    if len(rest) == 4:
        val = struct.unpack_from(">I", rest, 0)[0]
    return maskA, maskB, val


def cast(a, pos, skill=270, target=0, a_=1000, b_=2000):
    """a client 0x1032 SKILL USE, feprog.decode_cast's layout, through the
    REAL seam (feprog.on_cast taps fepvp.on_cast)."""
    body = struct.pack(">IIBIIHBII", a_, b_, 1, 1, 40, skill, 1, target, 3001)
    if target == 0:
        body += struct.pack(">fff", *pos)
    return feworld.ext_dispatch(ctx_of(a), 0x1032, struct.pack(">H", 0x1032) + body)


def cast_checks():
    """THE CAST PATH (2026-09-12): the avatar class's on-hit sends nothing,
    so player damage is settled from the caster's 0x1032."""
    print("\nthe cast path: player damage from the caster's SKILL USE", file=REPORT)
    _stub_store()
    a = _args(pvp_war="off")
    out = _out()
    out[:] = []
    enter(7)
    w = _Worker(); w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8))
    bob_out = w.do(_out)
    w.do(lambda: see(_args(pvp_war="off"), 900, (10.0, 20.0, 0.0)))
    see(a, 100, (12.0, 20.0, 0.0))
    w.do(lambda: see(_args(pvp_war="off"), 902, (10.0, 20.0, 0.0)))
    out[:] = []; bob_out[:] = []

    # count the pvp.hit posts themselves: fepresence posts the cast to the
    # same queues (the peers see the animation), so queue sizes say nothing
    POSTS = []
    real_post = feworld.ext_post

    def spy(kind, payload, to=None, include_me=False):
        n = real_post(kind, payload, to=to, include_me=include_me)
        if kind == "pvp.hit":
            POSTS.append((int(payload.get("to") or 0), n))
        return n
    feworld.ext_post = spy
    try:
        cast(a, (12.0, 20.0, 0.0))
        check("a cast 2 u from Bob (enemy nation) -> ONE pvp.hit to charid 8",
              POSTS == [(8, 1)], POSTS)
        w.do(lambda: pvp_pump(_args(pvp_war="off")))
        hp = w.do(lambda: feworld._SESSION.get("player_hp"))
        hpmax = w.do(lambda: feworld.player_hp_max(_args(pvp_war="off")))
        check("...Bob's thread applied it: stored HP = max - 25",
              hp == hpmax - 25, (hp, hpmax))
        del POSTS[:]
        cast(a, (12.0, 20.0, 0.0), a_=1000, b_=2000)
        check("the SAME cast record again (a, b, skill) is not applied twice",
              POSTS == [], POSTS)
        cast(a, (40.0, 20.0, 0.0), a_=1001, b_=2001)
        check("a cast 30 u away hits nobody", POSTS == [], POSTS)
        cast(a, (12.0, 20.0, 0.0), target=9, a_=1002, b_=2002)
        check("a TARGETED skill (c = 9) at someone else does not hit Bob",
              POSTS == [], POSTS)
        cast(a, (12.0, 20.0, 0.0), target=8, a_=1003, b_=2003)
        check("a targeted skill at Bob (c = 8) hits him from the caster's own spot",
              POSTS == [(8, 1)], POSTS)
        del POSTS[:]
        cast(_args(pvp_war="off", pvp_range=0), (12.0, 20.0, 0.0), a_=1004, b_=2004)
        check("--pvp-range 0 turns the cast path off", POSTS == [], POSTS)
        cast(_args(pvp_war="on", campaign="off"), (12.0, 20.0, 0.0), a_=1005, b_=2005)
        check("--pvp-war on with no campaign: no player damage", POSTS == [], POSTS)
        cast(_args(pvp_war="off", pvp="off"), (12.0, 20.0, 0.0), a_=1006, b_=2006)
        check("--pvp off: nothing", POSTS == [], POSTS)
        # same nation: Ally (9, nation 1) casts on top of Lex (7, nation 1)
        w.do(lambda: enter(9))
        w.do(lambda: see(_args(pvp_war="off"), 950, (12.0, 20.0, 1.0)))
        see(a, 101, (12.0, 20.0, 0.0))
        w.do(lambda: cast(_args(pvp_war="off"), (12.0, 20.0, 1.0), a_=1007, b_=2007))
        check("Ally (same nation) casting on top of Lex hits nobody (--pvp-friendly off)",
              POSTS == [], POSTS)
        w.do(lambda: cast(_args(pvp_war="off", pvp_friendly="on"), (12.0, 20.0, 1.0),
                          a_=1008, b_=2008))
        check("...--pvp-friendly on: it does", POSTS == [(7, 1)], POSTS)
    finally:
        feworld.ext_post = real_post
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    feworld._chat_room_leave()
    out[:] = []


def main_checks():
    _stub_store()
    a = _args()
    out = _out()
    out[:] = []
    enter(7)
    w = _Worker(); w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8))
    bob_out = w.do(_out)

    # both see each other
    w.do(lambda: see(_args(), 900, (10.0, 20.0, 0.0)))
    see(a, 100, (12.0, 20.0, 0.0))
    w.do(lambda: see(_args(), 902, (10.0, 20.0, 0.0)))
    out[:] = []
    bob_out[:] = []

    print("\nthe seam and the refusals", file=REPORT)
    check("0xA011 is claimed by fepvp",
          feworld.EXT_HANDLERS.get(0xA011).__module__ == "fepvp")
    # a hit at a MONSTER id is declined, so feworld's own arm still answers it
    check("0xA011 at a non-player id -> DECLINED (feworld's monster arm runs)",
          swing(a, 405) is False)
    # --pvp off
    check("--pvp off -> declined, nothing posted",
          swing(_args(pvp="off"), 8) is False)
    # --unit-id 1
    check("--unit-id 1 -> declined (no peer can be addressed)",
          swing(_args(unit_id="1"), 8) is False)
    out[:] = []; bob_out[:] = []

    print("\none hit, all the way round", file=REPORT)
    check("0xA011 at Bob's charid -> HANDLED by fepvp", swing(a, 8) is True)
    check("...nothing goes to the attacker's client yet", out == [], out)
    w.do(lambda: pvp_pump(_args()))
    ids = only(bob_out, 0x2024)
    check("Bob's OWN thread applies it: 0x2024 to Bob's own unit id 8",
          ids == [(0x2024, 8)], ids)
    hp_bodies = bodies(bob_out, 0x2024)
    maskA, _mb, val = decode_2024(hp_bodies[0]) if hp_bodies else (0, 0, None)
    hpmax = feworld.player_hp_max(a)
    check("...maskA 0x4 (the damage event) carrying the NEW hp",
          (maskA, val) == (0x4, hpmax - 25), (hex(maskA), val, hpmax))
    check("...and Bob's session holds that HP",
          w.do(lambda: feworld._SESSION.get("player_hp")) == hpmax - 25)
    bob_out[:] = []

    # the report comes back to the attacker
    pvp_pump(a)
    ids = only(out, 0x2024)
    check("the attacker is told: 0x2024 maskA 0x4 on the VICTIM's avatar (8)",
          ids == [(0x2024, 8)], ids)
    hp_bodies = bodies(out, 0x2024)
    maskA, _mb, val = decode_2024(hp_bodies[0]) if hp_bodies else (0, 0, None)
    check("...same channel, the victim's new hp", (maskA, val) == (0x4, hpmax - 25),
          (hex(maskA), val))
    out[:] = []

    # --pvp-show-damage off
    swing(_args(pvp_show_damage="off"), 8)
    w.do(lambda: pvp_pump(_args()))
    bob_out[:] = []
    pvp_pump(_args(pvp_show_damage="off"))
    check("--pvp-show-damage off -> the hit lands, the attacker sees nothing",
          only(out, 0x2024) == []
          and w.do(lambda: feworld._SESSION.get("player_hp")) == hpmax - 50,
          only(out, 0x2024))
    out[:] = []

    print("\nthe rules", file=REPORT)
    # same nation
    g = _Worker(); g.start()
    g.do(_stub_store)
    g.do(lambda: enter(9))
    g.do(lambda: see(_args(), 1, (10.0, 20.0, 0.0)))
    ally_out = g.do(_out)
    ally_out[:] = []
    check("a hit on the SAME nation is handled and does NOTHING",
          swing(a, 9) is True)
    g.do(lambda: pvp_pump(_args()))
    check("...no damage reached them", only(ally_out, 0x2024) == [],
          only(ally_out, 0x2024))
    check("--pvp-friendly on -> it lands", swing(_args(pvp_friendly="on"), 9) is True)
    g.do(lambda: pvp_pump(_args()))
    check("...and their HP moved",
          g.do(lambda: feworld._SESSION.get("player_hp")) == hpmax - 25,
          g.do(lambda: feworld._SESSION.get("player_hp")))
    ally_out[:] = []
    g.do(feworld._chat_room_leave)
    g.tasks.put(None)

    # spawn protection
    w.do(lambda: feworld.spawn_protect_start(_args(), "a test"))
    hp_before = w.do(lambda: feworld._SESSION.get("player_hp"))
    swing(a, 8)
    w.do(lambda: pvp_pump(_args()))
    check("a PROTECTED player takes no damage (--spawn-protect-secs)",
          w.do(lambda: feworld._SESSION.get("player_hp")) == hp_before)
    w.do(lambda: feworld._SESSION.pop("protect_until", None))
    bob_out[:] = []
    out[:] = []

    print("\na kill, and standing back up", file=REPORT)
    big = _args(pvp_damage=100000)
    check("a lethal hit is handled", swing(big, 8) is True)
    w.do(lambda: pvp_pump(_args()))
    hp_bodies = bodies(bob_out, 0x2024)
    # 2026-09-13: + RoD's respawn wait (0x2024 bit 0x1000000, 15000 ms), the
    # same a monster kill sends
    check("the victim gets HP 0 and the DEAD condition (0x2024 maskA 0x100000)",
          len(hp_bodies) == 3
          and decode_2024(hp_bodies[2])[0] == 0x1000000
          and decode_2024(hp_bodies[2])[2] == 15000
          and decode_2024(hp_bodies[0])[2] == 0
          and decode_2024(hp_bodies[1])[0] == 0x100000
          and decode_2024(hp_bodies[1])[2] & 0x800000,
          [decode_2024(b) for b in hp_bodies])
    check("...and the victim's session says dead",
          w.do(lambda: feworld._SESSION.get("player_dead")) is True)
    bob_out[:] = []
    pvp_pump(a)
    out[:] = []

    # the revive: feworld's own tick is gated on monster combat, so fepvp
    # drives it -- with the timer still running nothing happens
    w.do(lambda: pvp_pump(_args()))
    check("before --revive-secs: still down, nothing sent",
          w.do(lambda: feworld._SESSION.get("player_dead")) is True
          and only(bob_out, 0x2024) == [], only(bob_out, 0x2024))
    w.do(lambda: feworld._SESSION.__setitem__("revive_at", 0))
    w.do(lambda: pvp_pump(_args()))
    ids = only(bob_out, 0x2024)
    check("after it: fepvp's pump revives them (alive again, HP back to max)",
          w.do(lambda: feworld._SESSION.get("player_dead")) is False
          and w.do(lambda: feworld._SESSION.get("player_hp")) == hpmax
          and len(ids) == 2, ids)
    bob_out[:] = []

    # with monster combat ON, feworld's own tick owns the revive -- fepvp
    # must not double-drive it
    w.do(lambda: (feworld._SESSION.__setitem__("player_dead", True),
                  feworld._SESSION.__setitem__("revive_at", 0)))
    w.do(lambda: pvp_pump(_args(combat="on", monster_attack="on")))
    check("with --monster-attack on, fepvp leaves the revive to feworld",
          w.do(lambda: feworld._SESSION.get("player_dead")) is True, bob_out)
    w.do(lambda: feworld._SESSION.__setitem__("player_dead", False))

    print("\n!pvp, the live switch", file=REPORT)
    off = _args(pvp="off")
    check("!pvp on forces it on for a session whose flag says off",
          fepvp.gm(ctx_of(off), "!pvp on") is True and swing(off, 8) is True)
    w.do(lambda: pvp_pump(_args()))
    bob_out[:] = []
    check("!pvp off forces it off even when the flag says on",
          fepvp.gm(ctx_of(off), "!pvp off") is True and swing(a, 8) is False)
    fepvp._FORCE[0] = None
    check("!pvp damage 7 changes the number",
          fepvp.gm(ctx_of(a), "!pvp damage 7") is True and fepvp._damage(a)[0] == 7)
    a.pvp_damage = 0
    check("!pvp status is answered", fepvp.gm(ctx_of(a), "!pvp") is True)
    check("an unrelated !verb is declined", fepvp.gm(ctx_of(a), "!war") is False)

    print("\nthe peer HP block (what a damage number subtracts from)", file=REPORT)
    hp_args = _args(presence_hp="on")
    card = fepresence.build_card(hp_args, (11, -1))
    body = fepresence.avatar_body(7, card, (1.0, 2.0, 3.0))
    # [u16 count][u32 obj][u8 type] = 7, [u32 mask1] = 11, [u32 sub] = 15,
    # then the identity fields, then [u32 statmask][u32 0] and the i16 pair
    mask1 = struct.unpack_from(">I", body, 7)[0]
    at = 15 + len(card["ident"])
    statmask = struct.unpack_from(">I", body, at)[0]
    hpmax_i, hpcur_i = struct.unpack_from(">hh", body, at + 8)
    check("--presence-hp on -> mask1 bit 1 and the HP pair ride the record",
          mask1 == 0x3 and statmask == (1 << 1) | (1 << 2)
          and hpmax_i == hpmax and hpcur_i == hpmax,
          (hex(mask1), hex(statmask), hpmax_i, hpcur_i))
    plain = fepresence.avatar_body(7, fepresence.build_card(_args(), (11, -1)),
                                   (1.0, 2.0, 3.0))
    check("...and OFF (the default) leaves mask1 at 1, the proven shape",
          struct.unpack_from(">I", plain, 7)[0] == 0x1)
    n_before = feworld._SESSION.get("pres_epoch")
    check("!presence hp on flips it live and bumps the re-state epoch",
          fepresence.gm(ctx_of(a), "!presence hp on") is True
          and a.presence_hp == "on"
          and fepresence._EPOCH[0] != n_before)
    a.presence_hp = "off"
    fepresence._EPOCH[0] += 1

    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    feworld._chat_room_leave()


def table_checks():
    """--pvp-model table (2026-10-01, audit 26): the skill's own damage and
    reach, the victim's own modifiers, statuses, Hide."""
    print("\n--pvp-model table: skill damage, skill reach, statuses, Hide",
          file=REPORT)
    import fegamedata
    from world import status
    _stub_store()
    a = _args(pvp_war="off", pvp_model="table", status_effects="on")
    bargs = _args(pvp_war="off", pvp_model="table", status_effects="on")
    out = _out()
    out[:] = []
    enter(7)
    w = _Worker(); w.start()
    w.do(_stub_store)
    w.do(lambda: enter(8))
    w.do(lambda: see(bargs, 900, (10.0, 20.0, 0.0)))
    see(a, 100, (12.0, 20.0, 0.0))
    w.do(lambda: see(bargs, 902, (10.0, 20.0, 0.0)))
    bob = w.do(lambda: feworld._SESSION)
    my_lvl = int(feworld.char_level(a))
    bob["pvp_level"] = my_lvl                  # what Bob's pump publishes
    POSTS = []
    real_post = feworld.ext_post

    def spy(kind, payload, to=None, include_me=False):
        n = real_post(kind, payload, to=to, include_me=include_me)
        if kind == "pvp.hit":
            POSTS.append(dict(payload, n=n))
        return n
    feworld.ext_post = spy
    try:
        _k, _b, _w, atk = feworld.player_attack(a)
        row = fegamedata.lv_diff_correction(0)
        want = max(1, int(round(atk * fegamedata.skill_power(0) / 100.0
                                * (row[0] if row else 1.0))))
        cast(a, (12.0, 20.0, 0.0), skill=0, a_=3000, b_=4000)
        check("the basic attack 2 u from Bob hits for ATTACK x POWER x level row "
              "(%d), not the flat 25" % want,
              len(POSTS) == 1 and POSTS[0]["dmg"] == want and want != 25
              and POSTS[0]["model"] == "table", (POSTS, want))
        rng, rad = fegamedata.skill_radius(0)
        reach = rng + rad + 3.0
        check("the basic attack's reach is SKILL_DATA range %g + radius %g + "
              "slack 3" % (rng, rad), abs(fepvp._reach(a, 0, False) - reach) < 1e-6,
              fepvp._reach(a, 0, False))
        del POSTS[:]
        cast(a, (10.0 + reach + 0.5, 20.0, 0.0), skill=0, a_=3001, b_=4001)
        check("...a cast just past that reach misses (the old sphere was 10 u)",
              POSTS == [], POSTS)
        cast(a, (10.0 + reach - 0.5, 20.0, 0.0), skill=0, a_=3002, b_=4002)
        check("...and just inside it hits", len(POSTS) == 1, POSTS)
        w.do(lambda: pvp_pump(bargs))
        hp = w.do(lambda: feworld._SESSION.get("player_hp"))
        hpmax = w.do(lambda: feworld.player_hp_max(bargs))
        check("Bob's thread took the two hits (no armour in the stub store)",
              hp == hpmax - 2 * want, (hp, hpmax, want))
        # crouched: the victim's own multiplier
        w.do(lambda: status.crouch_note(feworld._SESSION, "test"))
        del POSTS[:]
        cast(a, (12.0, 20.0, 0.0), skill=0, a_=3003, b_=4003)
        w.do(lambda: pvp_pump(bargs))
        hp2 = w.do(lambda: feworld._SESSION.get("player_hp"))
        check("a CROUCHED Bob takes --crouch-damage 1.3x (%d)"
              % int(round(want * 1.3)), hp - hp2 == int(round(want * 1.3)),
              (hp, hp2))
        w.do(lambda: feworld._SESSION.pop("crouch_at", None))
        # Viper Bite (190) poisons
        del POSTS[:]
        cast(a, (12.0, 20.0, 0.0), skill=190, target=8, a_=3004, b_=4004)
        w.do(lambda: pvp_pump(bargs))
        poisoned = w.do(lambda: status.has(feworld._SESSION, "poison"))
        check("Viper Bite (skill 190, EFFECT_DATA 253 D81_PoisonStatus) "
              "POISONS Bob", len(POSTS) == 1 and poisoned, (POSTS, poisoned))
        # Hide: Bob hides; a TARGETED skill can't lock him, an area one can
        w.do(lambda: status.apply(feworld._SESSION, status.good_specs(140)))
        del POSTS[:]
        cast(a, (12.0, 20.0, 0.0), skill=190, target=8, a_=3005, b_=4005)
        check("Bob in HIDE (skill 140): a targeted skill can't lock him",
              POSTS == [], POSTS)
        cast(a, (12.0, 20.0, 0.0), skill=0, a_=3006, b_=4006)
        w.do(lambda: pvp_pump(bargs))
        check("...an area swing still hits, and the hit ends his Hide",
              len(POSTS) == 1
              and not w.do(lambda: status.has(feworld._SESSION, "hide")), POSTS)
        # a stunned attacker does nothing
        status.apply(feworld._SESSION, status.bad_specs(35))      # Shield Bash
        del POSTS[:]
        cast(a, (12.0, 20.0, 0.0), skill=0, a_=3007, b_=4007)
        check("Lex STUNNED (Shield Bash 35): his cast does no player damage",
              POSTS == [], POSTS)
        status.clear(feworld._SESSION)
        # --pvp-model flat = the rollback
        cast(_args(pvp_war="off", pvp_model="flat"), (12.0, 20.0, 0.0), skill=0,
             a_=3008, b_=4008)
        check("--pvp-model flat: the old flat 25 again",
              len(POSTS) == 1 and POSTS[0]["dmg"] == 25
              and POSTS[0]["model"] == "flat", POSTS)
        # Ender Pain (5) on the caster's own cast
        cast(a, (12.0, 20.0, 0.0), skill=5, a_=3009, b_=4009)
        check("Ender Pain (skill 5) on the CASTER: no flinch + resist up",
              status.has(feworld._SESSION, "noflinch")
              and status.resist_delta(feworld._SESSION) == 5.0,
              status.active(feworld._SESSION))
        status.clear(feworld._SESSION)
    finally:
        feworld.ext_post = real_post
    w.do(feworld._chat_room_leave)
    w.tasks.put(None)
    feworld._chat_room_leave()


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet = "-v" not in sys.argv
    saved = (feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap)
    feworld.send_world_frame = _capture
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    if quiet:
        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        _saved_out = sys.stdout
        sys.stdout = _Null()
    try:
        main_checks()
        cast_checks()
        table_checks()
    finally:
        if quiet:
            sys.stdout = _saved_out
        feworld.send_world_frame, fenet.bf_encrypt, fenet.traffic_wrap = saved
    if FAILS:
        print("[fe_pvp_test] %d FAILED: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("[fe_pvp_test] OK")
