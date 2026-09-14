#!/usr/bin/env python3
"""fe_death_test.py -- HP, death and respawn under the 2006 rules (2026-09-11).

Run from services/:  python ../tools/fe_death_test.py

WHAT THIS PINS. Every check reads back the STATE the behaviour was meant to
change -- a stored row, a session field, a message that went out -- never that
a knob was accepted. Each section fails on the pre-2026-09-11 code:

  1. HP is a FLAT 1000 (2006) -- feworld --player-hp and felobby --char-hp
     default 1000, and --monster-damage 60 keeps the old ~17-swing lethality.
  2. felobby MIGRATES a character stored with the old 200 max: the served
     0xD002 record and the store both carry 1000 afterwards.
  3. A death in a PREP/WAR field revives at the side's BASE (a same-field
     0x1166 to spawn_for(area, side)); in peace or truce it stays in place.
  4. 15 s protection after that return and after an area move (driven
     through feworld.serve()'s own 0x2000 handler): monster swings do nothing,
     and any action other than moving ends it.
  5. At peace a player arrives on the CASTLE side whoever holds the field.
  6. The hunting-death penalty takes --death-exp-pct / --death-gold-pct out
     of the STORED exp and gold -- RoD's 10% of NEXT (stopping at 0 into the
     level) and 20% of gold by default -- no EXP/gold in a war or truce
     field, and a war death costs 3 crystals (floored at 0).

The parsers are the REAL ones (captured out of feworld.main / felobby.main),
so the defaults under test are the defaults prod would run.
"""
import argparse
import contextlib
import copy
import io
import os
import socket as _real_socket
import struct
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
SERVICES = os.path.join(os.path.dirname(HERE), "services")
sys.path.insert(0, SERVICES)

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

import fegamedata  # noqa: E402
import felobby  # noqa: E402
import fenet  # noqa: E402
import feworld  # noqa: E402

FAILED = []
OUT = sys.stdout


def check(name, ok, detail=""):
    print("  %-66s %s%s" % (name, "ok" if ok else "FAIL",
                            ("  " + repr(detail)) if detail != "" and not ok
                            else ""), file=OUT, flush=True)
    if not ok:
        FAILED.append(name)


class _Got(Exception):
    pass


class _SockShim(object):
    """feworld.main's `socket` with socket() replaced: the listener is the
    first thing main builds after every knob is parsed and post-processed, so
    raising there hands us main's own finished `args`."""
    def __getattr__(self, k):
        return getattr(_real_socket, k)

    def socket(self, *a, **k):
        raise _Got(sys._getframe(1).f_locals["args"])


def feworld_args():
    saved = (feworld.socket, sys.argv)
    feworld.socket, sys.argv = _SockShim(), ["feworld.py"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            feworld.main()
    except _Got as g:
        return g.args[0]
    finally:
        feworld.socket, sys.argv = saved
    raise AssertionError("feworld.main() never reached its listener")


def felobby_parser():
    orig = argparse.ArgumentParser.parse_args

    def grab(self, *a, **k):
        raise _Got(self)
    argparse.ArgumentParser.parse_args = grab
    saved = sys.argv
    sys.argv = ["felobby.py"]
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            felobby.main()
    except _Got as g:
        return g.args[0]
    finally:
        argparse.ArgumentParser.parse_args = orig
        sys.argv = saved
    raise AssertionError("felobby.main() never parsed")


# --- capture every world message, direct calls and serve() alike ------------
SENT = []


def _install_capture():
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
             fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_decrypt = lambda st, body, mode, be: body
    fenet.traffic_unwrap = lambda plain: (1, plain)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data

    def _capture(conn, mid, body, prefix):
        _unit, msg = struct.unpack_from(">IH", body, 0)
        SENT.append((msg, body[6:]))
    feworld.send_world_frame = _capture
    return saved


def _restore_capture(saved):
    (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
     fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame) = saved


def ids():
    return [m for m, _b in SENT]


def of(mid):
    return [b for m, b in SENT if m == mid]


class _Conn(object):
    def settimeout(self, t):
        pass

    def getpeername(self):
        return ("127.0.0.1", 1)


def session(**kw):
    feworld._TLS.session = dict(kw)
    return feworld._TLS.session


def with_(base, **kw):
    a = copy.copy(base)
    for k, v in kw.items():
        setattr(a, k, v)
    return a


# ---------------------------------------------------------------------------
def part_defaults(wa, lp):
    print("\n1. HP is a flat 1000 (2006), and monster damage scales with it",
          file=OUT)
    check("feworld --player-hp defaults to 1000", wa.player_hp == 1000,
          wa.player_hp)
    check("...and player_hp_max says so with the knob absent too",
          feworld.player_hp_max(argparse.Namespace()) == 1000)
    check("felobby --char-hp defaults to 1000",
          lp.get_default("char_hp") == 1000, lp.get_default("char_hp"))
    check("--monster-damage defaults to 60 (x5 the old 12)",
          wa.monster_damage == 60, wa.monster_damage)
    flat = with_(wa, damage_model="flat")
    dmg = feworld.monster_swing_damage(flat, {"attack": 92})[0]
    check("...so the weakest monster still needs 17 swings to kill (1000/60)",
          dmg == 60 and -(-1000 // dmg) == 17, dmg)
    knobs = tuple(getattr(wa, k, None) for k in (
        "spawn_protect_secs", "war_death_return", "peace_arrival",
        "death_exp_pct", "death_gold_pct", "spawn_protect_flag",
        "death_exp_of", "death_war_crystals"))
    # 2026-09-11 (second pass): the penalty amounts were 0 / 0 ("unknown");
    # the RoD numbers turned up -- 10% of NEXT, 20% of gold carried, -3
    # crystals on a war death (fewiki Guide/メモ, WAR/クリスタル, 2006)
    check("the new knobs' defaults are the 2006 ones (penalty: 10% of NEXT, "
          "20% gold, -3 crystals in war)",
          knobs == (15.0, "base", "castle", 10.0, 20.0, "off", "next", 3), knobs)


def _field_off(vals, key):
    off = 0
    for kind, k, _d, _n in felobby.CHAR_FIELDS:
        if k == key:
            return off
        off += (len(vals[k].encode("cp932", "replace")) + 1 if kind == "cstr"
                else felobby.CHAR_WIDTH[kind])
    raise KeyError(key)


def part_lobby(lp, tmp):
    print("\n2. felobby migrates a character stored with the old 200 max",
          file=OUT)
    store = os.path.join(tmp, "lobby", "fe_characters.json")
    os.makedirs(os.path.dirname(store), exist_ok=True)
    acct = "member:77"
    old = felobby.char_defaults(False)
    old.update(name="Veteran", charid=501, gear_seeded=True,
               w72=200, w76=200, w78=100, w7c=100)
    new = felobby.char_defaults(False)
    new.update(name="Fresh", charid=502, gear_seeded=True)
    felobby.save_roster(store, acct, [old, new])
    args = lp.parse_args([])
    args.char_store = store
    inbox, sent = [(0x30, struct.pack(">H", 0xC001))], []
    saved = (felobby.recv_frame, felobby.bf_decrypt, felobby.traffic_unwrap,
             felobby.bf_encrypt, felobby.traffic_wrap, felobby.send_frame)
    felobby.recv_frame = lambda conn: inbox.pop(0) if inbox else None
    felobby.bf_decrypt = lambda st, body, mode, be: body
    felobby.traffic_unwrap = lambda plain: (1, plain)
    felobby.bf_encrypt = lambda st, body, mode, be: body
    felobby.traffic_wrap = lambda data, seq=1: data
    felobby.send_frame = lambda conn, mid, body: sent.append(body)
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            felobby.serve_lobby(_Conn(), args, None, None, 0, False,
                                {"key": acct})
    finally:
        (felobby.recv_frame, felobby.bf_decrypt, felobby.traffic_unwrap,
         felobby.bf_encrypt, felobby.traffic_wrap, felobby.send_frame) = saved
    d002 = [b for b in sent if struct.unpack_from(">H", b, 0)[0] == 0xD002]
    check("the character list went out (0xD002)", len(d002) == 1,
          [struct.unpack_from(">H", b, 0)[0] for b in sent])
    payload = d002[0][6:]
    back = felobby.load_roster(store, acct)
    by = {c["charid"]: c for c in back}
    check("the STORED veteran now has w72 = w76 = 1000 (was 200/200)",
          (by[501].get("w72"), by[501].get("w76")) == (1000, 1000),
          (by[501].get("w72"), by[501].get("w76")))
    check("...and the stored fresh one (was all zero) too",
          (by[502].get("w72"), by[502].get("w76")) == (1000, 1000),
          (by[502].get("w72"), by[502].get("w76")))
    off = 6 + _field_off(by[501], "w72")
    check("the served record carries HP max 1000 at +0x72 on the wire",
          struct.unpack_from(">h", payload, off)[0] == 1000
          and struct.unpack_from(">h", payload, off + 4)[0] == 1000,
          payload[off:off + 6].hex())
    check("...and the PW triple is untouched (100)",
          (by[501].get("w78"), by[501].get("w7c")) == (100, 100))


# ---------------------------------------------------------------------------
def _war(fecampaign, area, phase, atk):
    with fecampaign._LOCK:
        fecampaign._STATE[area] = {"phase": phase, "until": time.time() + 999,
                                   "atk": atk, "signups": {}, "keeps": {}}


def _kill(a, area=17):
    """A player on 5 hp beside a live monster in `area`, then the swing."""
    S = session(in_field=True, field=area, room=-1, cpos=(100.0, 30.0, 100.0),
                player_hp=5, account="member:88", charid=601)
    S["mobs"] = {}
    feworld.mob_register(a, 1401, 20, 0, 1, 103.0, 30.0, 100.0, name="Venomous",
                         type_id=1004)
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    return S


def _revive(a):
    feworld._SESSION["revive_at"] = 0
    del SENT[:]
    feworld.revive_tick(None, None, None, None, a)


def _warp(body):
    area, b, x, y, z = struct.unpack(">IIfff", body)
    return area, b, (round(x, 3), round(y, 3), round(z, 3))


def _r3(p):
    return tuple(round(float(v), 3) for v in p)


def part_war_return(wa, fecampaign):
    print("\n3. a war death returns you to your side's base", file=OUT)
    a = with_(wa, combat="on", monster_attack="on", campaign="on",
              damage_model="flat", unit_state=0x1E000000, unit_id="1",
              revive_secs=8.0)
    base_atk = feworld.derived_spawn(a, 17, "atk")
    base_def = feworld.derived_spawn(a, 17, "def")
    check("area 17 derives two different base points (keep vs castle)",
          base_atk and base_def and base_atk != base_def, (base_atk, base_def))

    feworld.nation_of = lambda args: 3
    feworld.territory_owner = lambda area, args=None: {17: 1}.get(int(area), 1)
    _war(fecampaign, 17, fecampaign.WAR, 3)       # nation 3 attacks 17
    S = _kill(a)
    check("the swing kills: DEAD, revive scheduled",
          S.get("player_dead") and S.get("revive_at", 0) > 0)
    _revive(a)
    warps = of(0x1166)
    check("at --revive-secs the dead ATTACKER is warped (0x1166)",
          len(warps) == 1, [hex(i) for i in ids()])
    if warps:
        area, b, pos = _warp(warps[0])
        check("...inside the same field (A = 17, B = -1: a same-field warp)",
              (area, b) == (17, 0xFFFFFFFF), (area, hex(b)))
        check("...to the attacker's KEEP side, not where they fell",
              pos == _r3(base_atk) and pos != (100.0, 30.0, 100.0),
              (pos, base_atk))
    check("...alive with full HP 1000, DEAD cleared first",
          not S.get("player_dead") and S.get("player_hp") == 1000
          and ids()[:2] == [0x2024, 0x2024]
          and struct.unpack_from(">h", of(0x2024)[1], 8)[0] == 1000,
          [hex(i) for i in ids()])
    check("...and the session's position is the base (no swing at the corpse)",
          _r3(S.get("cpos")) == _r3(base_atk), S.get("cpos"))

    # a door's one-off arrival must not become the base
    S = _kill(a)
    S["arrive_at"] = (17, (1.0, 2.0, 3.0))
    _revive(a)
    check("a door's arrival stash is NOT the base",
          of(0x1166) and _warp(of(0x1166)[0])[2] == _r3(base_atk),
          of(0x1166) and _warp(of(0x1166)[0])[2])

    # the defender goes to the castle
    feworld.territory_owner = lambda area, args=None: {17: 3}.get(int(area), 1)
    _war(fecampaign, 17, fecampaign.WAR, 1)
    _kill(a)
    _revive(a)
    check("a dead DEFENDER goes to the castle side",
          of(0x1166) and _warp(of(0x1166)[0])[2] == _r3(base_def),
          of(0x1166) and _warp(of(0x1166)[0])[2])

    _war(fecampaign, 17, fecampaign.PREP, 1)
    _kill(a)
    _revive(a)
    check("in War PREP too", len(of(0x1166)) == 1, [hex(i) for i in ids()])

    # 2026-09-12: these USED TO assert the revive stays in place outside a
    # war. Live testing settled on the field's ARRIVAL POINT instead (Return
    # to Base put the player back beside their killer) --
    # --hunt-death-return arrival.
    for ph, name in ((fecampaign.PEACE, "PEACE"), (fecampaign.TRUCE, "TRUCE")):
        _war(fecampaign, 17, ph, 1)
        S = _kill(a)
        _revive(a)
        want = feworld.spawn_for(a, 17, tag=feworld.spawn_side(a, 17),
                                 door=False)
        check("in %s the revive goes to the field's ARRIVAL POINT" % name,
              of(0x1166) and _warp(of(0x1166)[-1])[2] == _r3(want)
              and not S.get("player_dead") and S.get("player_hp") == 1000,
              ([hex(i) for i in ids()], _r3(want)))
    _war(fecampaign, 17, fecampaign.PEACE, 1)
    _kill(a)
    _revive(with_(a, hunt_death_return="place"))
    check("--hunt-death-return place restores the in-place revive",
          not of(0x1166), [hex(i) for i in ids()])

    _war(fecampaign, 17, fecampaign.WAR, 1)
    _kill(a)
    _revive(with_(a, war_death_return="place"))
    check("--war-death-return place keeps the old revive-in-place",
          not of(0x1166), [hex(i) for i in ids()])
    with fecampaign._LOCK:
        fecampaign._STATE.pop(17, None)
    return a


def part_protection(a, fecampaign):
    print("\n4. 15 s protection after a return or an area move", file=OUT)
    _war(fecampaign, 17, fecampaign.WAR, 1)
    S = _kill(a)
    _revive(a)
    check("the return to base opens the protection window",
          feworld.spawn_protected(a) and S.get("protect_until", 0) > time.monotonic() + 14,
          S.get("protect_until"))
    # a monster right beside the base point swings -- and does nothing
    S["mobs"][1401]["pos"] = (S["cpos"][0] + 2.0, S["cpos"][1], S["cpos"][2])
    S["mobs"][1401]["swing_at"] = 0
    S["player_hp"] = 1000
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("a protected player's HP does not move (no 0x2024 hp push)",
          S["player_hp"] == 1000 and not of(0x2024), [hex(i) for i in ids()])
    check("...but the swing DID happen (its interval restarted)",
          S["mobs"][1401]["swing_at"] > 0)
    # moving is not an action
    feworld.spawn_protect_note(0x2023)
    check("movement (0x2023) does not end it", feworld.spawn_protected(a))
    feworld.spawn_protect_note(0x2029)
    check("assigning a skill to the palette (0x2029) does not end it",
          feworld.spawn_protected(a))
    feworld.spawn_protect_note(0xA011)
    check("a HIT (0xA011) ends it at once", not feworld.spawn_protected(a)
          and "protect_until" not in S)
    S["mobs"][1401]["swing_at"] = 0
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("...after which the next swing lands (HP 940)",
          S["player_hp"] == 940 and len(of(0x2024)) == 1, S["player_hp"])
    for mid in (0x2046, 0x2051, 0x2007):
        feworld.spawn_protect_start(a, "t")
        feworld.spawn_protect_note(mid)
        check("0x%04X ends it too" % mid, not feworld.spawn_protected(a))
    # it runs out on its own
    feworld.spawn_protect_start(a, "t")
    S["protect_until"] = time.monotonic() - 0.01
    S["mobs"][1401]["swing_at"] = 0
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("after 15 s it runs out and swings land again",
          "protect_until" not in S and len(of(0x2024)) == 1)
    feworld.spawn_protect_start(with_(a, spawn_protect_secs=0), "t")
    check("--spawn-protect-secs 0 turns it off", not feworld.spawn_protected(a))

    # the optional, UNPROVEN client flag
    fa = with_(a, spawn_protect_flag="on")
    S.pop("protect_flag_sent", None)
    feworld.spawn_protect_start(fa, "t")
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, fa)
    conds = [struct.unpack_from(">III", b, 0) for b in of(0x2024)
             if struct.unpack_from(">I", b, 0)[0] == 0x100000]
    check("--spawn-protect-flag on pushes CONDITION with 0x1000 set",
          conds and conds[0][2] == 0x1E001000, conds)
    feworld.spawn_protect_note(0xA011)
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, fa)
    conds = [struct.unpack_from(">III", b, 0) for b in of(0x2024)
             if struct.unpack_from(">I", b, 0)[0] == 0x100000]
    check("...and clears it when the protection ends",
          conds and conds[0][2] == 0x1E000000, conds)
    feworld.spawn_protect_start(a, "t")
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("with the flag off (default) no CONDITION push at all",
          not [b for b in of(0x2024)
               if struct.unpack_from(">I", b, 0)[0] == 0x100000])
    with fecampaign._LOCK:
        fecampaign._STATE.pop(17, None)


def part_serve(wa):
    print("\n4b. through feworld.serve(): an area move (0x2000) protects, "
          "a hit (0xA011) ends it", file=OUT)
    inbox = []
    fenet.recv_frame = lambda conn: inbox.pop(0) if inbox else None
    session(account="member:88", charid=601)
    inbox.append((0x30, struct.pack(">HI", 0x2000, 39)))
    del SENT[:]
    with contextlib.redirect_stdout(io.StringIO()):
        feworld.serve(_Conn(), wa, None, None, 0, False)
    S = feworld._SESSION
    check("0x2000 MSG_ENTER_AREA was answered (0x1000)", 0x1000 in ids(),
          [hex(i) for i in ids()][:8])
    check("...and the field entry opened a 15 s protection window",
          S.get("in_field") and S.get("protect_until", 0) > time.monotonic() + 14,
          S.get("protect_until"))
    inbox.append((0x30, struct.pack(">H", 0x2023) + bytes(28)))
    with contextlib.redirect_stdout(io.StringIO()):
        feworld.serve(_Conn(), wa, None, None, 0, False)
    check("a movement heartbeat through serve() leaves it open",
          feworld.spawn_protected(wa))
    inbox.append((0x30, struct.pack(">H", 0xA011) + bytes(31)))
    with contextlib.redirect_stdout(io.StringIO()):
        feworld.serve(_Conn(), wa, None, None, 0, False)
    check("a 0xA011 through serve() closes it",
          not feworld.spawn_protected(wa) and "protect_until" not in S)


def part_peace_arrival(wa, fecampaign):
    print("\n5. at peace you arrive on the castle side, whoever holds it",
          file=OUT)
    a = with_(wa, campaign="on")
    feworld.nation_of = lambda args: 3
    feworld.territory_owner = lambda area, args=None: {17: 1}.get(int(area), 1)
    with fecampaign._LOCK:
        fecampaign._STATE.pop(17, None)
    session(in_field=True, field=17, room=-1)
    check("nation 3 in nation 1's PEACEFUL field arrives on the castle side",
          feworld.spawn_side(a, 17) == "def", feworld.spawn_side(a, 17))
    check("...at the castle's derived point",
          _r3(feworld.spawn_for(a, 17)) == _r3(feworld.derived_spawn(a, 17, "def")),
          feworld.spawn_for(a, 17))
    check("--peace-arrival holder restores the keep side",
          feworld.spawn_side(with_(a, peace_arrival="holder"), 17) == "atk")
    check("with no campaign at all, every field is at peace: castle side",
          feworld.spawn_side(with_(a, campaign="off"), 17) == "def")
    _war(fecampaign, 17, fecampaign.WAR, 3)
    check("in a WAR the attacker still arrives on the keep side",
          feworld.spawn_side(a, 17) == "atk")
    with fecampaign._LOCK:
        fecampaign._STATE.pop(17, None)


def part_penalty(wa, fecampaign, tmp):
    print("\n6. the hunting-death penalty takes from the STORED exp and gold",
          file=OUT)
    store = os.path.join(tmp, "world", "fe_characters.json")
    os.makedirs(os.path.dirname(store), exist_ok=True)
    acct = "member:88"
    vals = felobby.char_defaults(False)
    vals.update(name="Hunter", charid=601, exp=2000, gold=1000)
    felobby.save_roster(store, acct, [vals])
    felobby._default_store = lambda: store
    a = with_(wa, combat="on", monster_attack="on", campaign="on",
              damage_model="flat", unit_state=0x1E000000, unit_id="1",
              gold=500, death_exp_pct=10.0, death_gold_pct=20.0,
              exp_model="flat")        # the lifetime-total shape; `level` below

    def stored():
        c = felobby.load_roster(store, acct)[0]
        return c.get("exp"), c.get("gold")

    _war(fecampaign, 17, fecampaign.PEACE, 1)
    _kill(a)
    check("a PEACE-field death took 10% EXP and 20% gold, IN THE STORE",
          stored() == (1800, 800), stored())
    check("...and re-served both (0x1075 exp + the 0x2024 wallet)",
          0x1075 in ids() and any(struct.unpack_from(">I", b, 0)[0] & 0x200000
                                  for b in of(0x2024)),
          [hex(i) for i in ids()])
    check("...and the session's EXP agrees", feworld._SESSION.get("exp") == 1800,
          feworld._SESSION.get("exp"))
    for ph, name in ((fecampaign.WAR, "WAR"), (fecampaign.PREP, "PREP"),
                     (fecampaign.TRUCE, "TRUCE")):
        _war(fecampaign, 17, ph, 1)
        _kill(a)
        check("a %s-field death costs nothing personal" % name,
              stored() == (1800, 800), stored())
    _war(fecampaign, 17, fecampaign.PEACE, 1)
    _kill(with_(a, death_exp_pct=0.0, death_gold_pct=0.0))
    check("--death-exp-pct 0 / --death-gold-pct 0 take nothing",
          stored() == (1800, 800), stored())
    # --exp-model level (the default since the levels work): the loss is 10%
    # of NEXT (RoD), taken out of PROGRESS INTO THE LEVEL, never a level, and
    # the lifetime total drops by the same amount
    c0 = felobby.load_roster(store, acct)[0]
    cls_ = int(c0.get("look1") or 0)
    c0.update(class_levels={str(cls_): 3}, class_exp={str(cls_): 500})
    felobby.save_roster(store, acct, [c0])
    feworld._SESSION.pop("class_exp", None)
    feworld._SESSION.pop("exp", None)
    _war(fecampaign, 17, fecampaign.PEACE, 1)
    lv = with_(a, exp_model="level", death_gold_pct=0.0,
               death_exp_pct=feworld.DEFAULT_DEATH_EXP_PCT)
    need3 = feworld.exp_need(lv, 3)
    _kill(lv)
    c1 = felobby.load_roster(store, acct)[0]

    def into(c):
        ce = c.get("class_exp") or {}
        return ce.get(str(cls_), ce.get(cls_))
    check("level model: a hunting death takes 10%% of NEXT (Lv3 next %d -> "
          "-%d), not 10%% of the progress (-50)" % (need3, need3 // 10),
          into(c1) == 500 - need3 // 10, repr(c1.get("class_exp")))
    check("...never the level itself, and the lifetime total drops by as much",
          (c1.get("class_levels") or {}).get(str(cls_), (c1.get("class_levels") or {}).get(cls_)) == 3
          and c1.get("exp") == 1800 - need3 // 10,
          repr((c1.get("class_levels"), c1.get("exp"))))
    c1.update(class_exp={str(cls_): 2})
    felobby.save_roster(store, acct, [c1])
    feworld._SESSION.pop("class_exp", None)
    feworld._SESSION.pop("exp", None)
    _kill(lv)
    c2 = felobby.load_roster(store, acct)[0]
    check("...and it STOPS AT 0 progress: 2 into Lv3 -> 0, still Lv3 "
          "(de-levelling is unknown for RoD, so nobody loses a level)",
          into(c2) == 0 and (c2.get("class_levels") or {}).get(str(cls_)) == 3,
          repr((c2.get("class_exp"), c2.get("class_levels"))))
    c2.update(class_exp={str(cls_): 500})
    felobby.save_roster(store, acct, [c2])
    feworld._SESSION.pop("class_exp", None)
    feworld._SESSION.pop("exp", None)
    _kill(with_(lv, death_exp_of="progress"))
    c3 = felobby.load_roster(store, acct)[0]
    check("--death-exp-of progress keeps the first pass's rule (10% of 500)",
          into(c3) == 450, repr(c3.get("class_exp")))
    # 2026-09-13: at the level CAP a death costs no EXP [Netz wiki 2006-05-05
    # 「死んでも経験値減らなくなります」, RoD]
    c3.update(class_levels={str(cls_): 40}, class_exp={str(cls_): 450})
    felobby.save_roster(store, acct, [c3])
    feworld._SESSION.pop("class_exp", None)
    feworld._SESSION.pop("exp", None)
    _kill(lv)
    c3b = felobby.load_roster(store, acct)[0]
    check("at the Lv40 cap a hunting death takes NO EXP (RoD)",
          into(c3b) == 450, repr(c3b.get("class_exp")))
    c3b.update(class_levels={str(cls_): 3}, class_exp={str(cls_): 450})
    felobby.save_roster(store, acct, [c3b])
    c3 = c3b
    # a WAR death: no EXP or gold, -3 crystals carried, floored at 0
    c3.update(crystal=5, gold=800)
    felobby.save_roster(store, acct, [c3])
    _war(fecampaign, 17, fecampaign.WAR, 1)
    wc = with_(a, crystal=0)
    _kill(wc)
    c4 = felobby.load_roster(store, acct)[0]
    check("a WAR death takes 3 crystals (5 -> 2) and no EXP or gold",
          c4.get("crystal") == 2 and c4.get("gold") == 800
          and into(c4) == 450, repr((c4.get("crystal"), c4.get("gold"))))
    check("...and re-serves the crystal total (0x2035)",
          any(struct.unpack_from(">II", b, 0) == (2, 2) for b in of(0x2035)),
          [hex(i) for i in ids()])
    _kill(wc)
    c5 = felobby.load_roster(store, acct)[0]
    check("...floored at 0: 2 -> 0", c5.get("crystal") == 0, c5.get("crystal"))
    with fecampaign._LOCK:
        fecampaign._STATE.pop(17, None)


def part_return_to_base(wa, fecampaign):
    print("", file=OUT)
    print("8. a death WAITS for Return to Base (0x2015), 2026-09-12", file=OUT)
    check("--revive-secs defaults to 60 -- a safety net, not the way back",
          wa.revive_secs == 60.0, wa.revive_secs)
    # (2026-09-13: this part is the BUTTON's contract, so no respawn wait --
    # part 9 tests the wait)
    a = with_(wa, combat="on", monster_attack="on", campaign="off",
              damage_model="flat", unit_state=0x1E000000, unit_id="1",
              respawn_wait="off")
    S = _kill(a, 17)
    check("the swing kills, and the safety net is a minute out",
          S.get("player_dead")
          and S.get("revive_at", 0) - time.monotonic() > 50,
          S.get("revive_at", 0) - time.monotonic())
    del SENT[:]
    feworld.revive_tick(None, None, None, None, a)
    check("...so the tick does NOT stand them up on its own",
          S.get("player_dead") and not SENT, ids())
    feworld.return_to_base(None, None, None, None, a)
    check("0x2015 Return to Base revives them: DEAD cleared, full HP",
          not S.get("player_dead")
          and S.get("player_hp") == feworld.player_hp_max(a),
          (S.get("player_dead"), S.get("player_hp")))
    want = feworld.spawn_for(a, 17, tag=feworld.spawn_side(a, 17), door=False)
    warps = [_warp(b) for b in of(0x1166)]
    check("...at the field's ARRIVAL POINT in a hunting field (a same-field "
          "0x1166), not where they fell",
          warps and warps[-1][0] == 17 and warps[-1][2] == _r3(want)
          and _r3(want) != _r3((100.0, 30.0, 100.0)),
          (warps, _r3(want)))
    # the killer is back at the corpse; put it beside the arrival point so
    # the protection checks below are about protection, not about range
    S["mobs"][1401]["pos"] = (want[0] + 3.0, want[1], want[2])
    # 2026-09-12, live: the revive stood them up at full HP and the monster
    # that killed them hit 10 ms later -- 671, 342 ("maybe 1/3 of my hp"),
    # 13, dead again. The 15 s protection [SE interface05] was only started
    # in the WAR branch.
    check("...and PROTECTED, so the monster that killed them cannot again",
          feworld.spawn_protected(a), S.get("protect_until"))
    S["mobs"][1401]["swing_at"] = 0
    feworld.monster_attack_tick(None, None, None, None, a)
    check("...a swing during that protection does no damage",
          S.get("player_hp") == feworld.player_hp_max(a), S.get("player_hp"))
    S.pop("protect_until", None)
    S["mobs"][1401]["swing_at"] = 0
    feworld.monster_attack_tick(None, None, None, None, a)
    check("(non-vacuous: without it the same swing DOES land)",
          S.get("player_hp") < feworld.player_hp_max(a), S.get("player_hp"))
    S["player_hp"] = feworld.player_hp_max(a)
    del SENT[:]
    check("a 0x2015 from a LIVING player does nothing",
          feworld.return_to_base(None, None, None, None, a) is False
          and not SENT, ids())
    # a WAR field: the button returns you to your side's base [SE flow08]
    w = with_(a, campaign="on")
    feworld.nation_of = lambda args: 3
    feworld.territory_owner = lambda area, args=None: {17: 1}.get(int(area), 1)
    _war(fecampaign, 17, fecampaign.WAR, 3)
    S = _kill(w, 17)
    del SENT[:]
    feworld.return_to_base(None, None, None, None, w)
    check("in a WAR field Return to Base warps to the side's base (0x1166)",
          not S.get("player_dead") and of(0x1166), ids())


def part_respawn_wait(wa, fecampaign):
    print("", file=OUT)
    print("9. the RESPAWN WAIT grows with repeated deaths (RoD), 2026-09-13",
          file=OUT)
    check("--respawn-wait defaults to 15:5:60 (the 15 s SOURCED, the growth "
          "CHOSEN)", wa.respawn_wait == "15:5:60", wa.respawn_wait)
    check("the curve: 15 s, +5 per repeat death, capped at 60",
          [feworld.respawn_wait_ms(wa, n) for n in (1, 2, 3, 20)]
          == [15000, 20000, 25000, 60000])
    check("--respawn-wait off: no wait",
          feworld.respawn_wait_ms(with_(wa, respawn_wait="off"), 3) == 0)
    a = with_(wa, combat="on", monster_attack="on", campaign="off",
              damage_model="flat", unit_state=0x1E000000, unit_id="1")

    def waits():
        return [struct.unpack(">III", b)[2] for b in of(0x2024)
                if len(b) == 12 and struct.unpack_from(">I", b, 0)[0] == 0x1000000]
    S = _kill(a, 17)
    check("a death sends the wait: 0x2024 bit 0x1000000 = 15000 ms "
          "([player+0x1384], the button's countdown)",
          S.get("player_dead") and waits() == [15000], waits())
    check("...and the safety net is not sooner than the wait",
          S["revive_at"] - time.monotonic() >= 14.0,
          S["revive_at"] - time.monotonic())
    del SENT[:]
    feworld.return_to_base(None, None, None, None, a)
    r0 = S["revive_at"]
    check("Return to Base STARTS the countdown: still down, the revive in "
          "~15 s, nothing warped yet",
          S.get("player_dead") and 13.0 < r0 - time.monotonic() <= 15.1
          and not of(0x1166), (S.get("player_dead"), r0 - time.monotonic()))
    feworld.return_to_base(None, None, None, None, a)
    check("...a second press does not restart it", S["revive_at"] == r0)
    del SENT[:]
    feworld.revive_tick(None, None, None, None, a)
    check("...nor does a tick before it runs out", S.get("player_dead"))
    S["revive_at"] = 0                         # the countdown has run out
    feworld.revive_tick(None, None, None, None, a)
    check("...when it runs out the player stands up (0x1166 to the arrival "
          "point)", not S.get("player_dead") and of(0x1166), ids())
    # die again in the SAME area, same session
    S.pop("protect_until", None)
    S["player_hp"] = 5
    m = S["mobs"][1401]
    m["pos"] = (S["cpos"][0] + 3.0, S["cpos"][1], S["cpos"][2])
    m["swing_at"] = 0
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("a second death in the same area waits LONGER: 20000 ms",
          S.get("player_dead") and waits() == [20000], waits())
    # a different area starts the streak again
    S["player_dead"], S["player_hp"], S["field"] = False, 5, 18
    m["swing_at"] = 0
    del SENT[:]
    feworld.monster_attack_tick(None, None, None, None, a)
    check("...a death in ANOTHER area is back to 15000 ms",
          S.get("player_dead") and waits() == [15000], waits())
    del SENT[:]
    feworld.revive_tick(None, None, None, None, with_(a, respawn_wait="off"))


def main():
    tmp = tempfile.mkdtemp(prefix="fe-death-test-")
    saved_store = felobby._default_store
    felobby._default_store = lambda: os.path.join(tmp, "none",
                                                  "fe_characters.json")
    real = (feworld.nation_of, feworld.territory_owner)
    cap = _install_capture()
    try:
        wa = feworld_args()
        wa.spawn_file = os.path.join(tmp, "fe_spawn.json")
        wa.spawn_area = ""
        wa.campaign_file = os.path.join(tmp, "fe_campaign.json")
        wa.town_file = os.path.join(tmp, "fe_town.json")
        feworld.spawn_load(wa)
        lp = felobby_parser()
        import fecampaign
        fecampaign.load(wa)
        war = with_(wa, combat="on", monster_attack="on", campaign="on",
                    damage_model="flat", unit_state=0x1E000000, unit_id="1",
                    revive_secs=8.0)
        # Each part runs on its own: a part that RAISES (as the old code
        # does, lacking the functions) is one failure, not the end of the run.
        for name, fn in (
                ("defaults", lambda: part_defaults(wa, lp)),
                ("lobby", lambda: part_lobby(lp, tmp)),
                ("war return", lambda: part_war_return(wa, fecampaign)),
                ("protection", lambda: part_protection(war, fecampaign)),
                ("serve", lambda: part_serve(wa)),
                ("peace arrival", lambda: part_peace_arrival(wa, fecampaign)),
                ("penalty", lambda: part_penalty(wa, fecampaign, tmp)),
                ("return to base",
                 lambda: part_return_to_base(wa, fecampaign)),
                ("respawn wait",
                 lambda: part_respawn_wait(wa, fecampaign))):
            try:
                fn()
            except Exception as e:                     # noqa: BLE001
                check("part '%s' ran to the end" % name, False,
                      "%s: %s" % (type(e).__name__, e))
            finally:
                with fecampaign._LOCK:
                    fecampaign._STATE.pop(17, None)
    finally:
        feworld.nation_of, feworld.territory_owner = real
        felobby._default_store = saved_store
        _restore_capture(cap)
    print("\n[fe_death_test] %s -- %d failed" %
          ("OK" if not FAILED else "FAIL", len(FAILED)), file=OUT)
    for f in FAILED:
        print("   FAILED: %s" % f, file=OUT)
    return 1 if FAILED else 0


if __name__ == "__main__":
    sys.exit(main())
