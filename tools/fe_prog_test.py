#!/usr/bin/env python3
"""fe_prog_test.py -- FE PROGRESSION (services/feprog.py + feworld's
PROGRESSION block), end to end through the real _serve_loop, no client.

Run from the repo root:  python tools/fe_prog_test.py [-v]

What is pinned, each read back the way the CLIENT reads it (addresses in
feprog.py's docstring) -- and each check asserts the STATE that changed (the
store row, the pushed value), never just "a reply went out":

  * a NEW character is seeded Lv1 in its own class, not all:30
  * the curve (ours) and the SP schedule (FEZ-era) and the shipped SP cost
  * a Lv1 character's starting gear passes the equip validator (0x05076090,
    emulated from its disassembly) and its class's starting skills are owned
  * the HUD gauge's max ([unit+0x135c]) goes out ahead of the numerator
  * a kill pushes ONE 0x1075 mask 0x6: [u32 next][u8 1][u8 class][u32 exp]
  * 0x2059 is GRANTED only when the stored progress pays for it: 0x1091, then
    0x1075 mask 0x7 (level row, next, remainder) and 0x2024 maskA 0x1 (SP)
  * GET! (0x2049) charges the rank's [skill+0xF0], refuses when short, and
    pushes the owned flag BEFORE 0x1078
  * a skill use (0x1031) debits [skill+0xF4] Pw; 0x2028 regenerates it
Every one of these FAILS on the pre-2026-09-11 code (all:30, levelup off,
flat SP budget, no Pw handler).
"""
import inspect
import os
import struct
import types
import sys
import tempfile

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for p in (_SERVICES, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fegamedata  # noqa: E402
import fenet      # noqa: E402
import feworld    # noqa: E402
import feprog     # noqa: E402
from fe_ui_test import Reader, STORE, _args, _stub_store   # noqa: E402

feworld.load_extensions(["feprog"])

OUT = []
FAILED = []
SORC = 2          # Sorcerer: starting set 443 445 270 275, gear 531 + casual


def check(label, cond, detail=""):
    print("  %-62s %s%s" % (label, "PASS" if cond else "FAIL",
                            "" if cond or detail == "" else "  %r" % (detail,)))
    if not cond:
        FAILED.append(label)


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.append((msg, unit, body[6:]))


def _dispatch(a, inner):
    """ONE inner message through the real _serve_loop, crypto stubbed."""
    class _Conn:
        def recv(self, n):
            raise OSError("done")

        def settimeout(self, t):
            pass

        def getpeername(self):
            return ("127.0.0.1", 1)
    saved = (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
             fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    frames = [(0x30, inner)]
    fenet.recv_frame = lambda c: frames.pop(0) if frames else None
    fenet.bf_decrypt = lambda s, b, m, be: b
    fenet.traffic_unwrap = lambda p: (1, p)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        feworld._serve_loop(_Conn(), a, None, None, "ecb", False)
    finally:
        (fenet.recv_frame, fenet.bf_decrypt, fenet.traffic_unwrap,
         fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame) = saved


def _direct(fn, a, *extra):
    """Call a feworld push directly with send captured."""
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        return fn(None, None, "ecb", False, a, *extra)
    finally:
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved


def _gm(a, line):
    d = tempfile.mkdtemp()
    p = os.path.join(d, "gm.txt")
    with open(p, "w", encoding="utf-8") as fh:
        fh.write(line + "\n")
    a.gmcmd_file = p
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        feworld.gm_pump(None, None, "ecb", False, a)
    finally:
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved


_REAL_SELF = feworld._self_char


def _fresh(**kw):
    """A brand-new Sorcerer: no class table, no skills, the starting gear."""
    _stub_store()
    OUT[:] = []
    STORE["items"] = [[2001, 531, 0, 1], [2002, 1666, 0, 1],
                      [2003, 1668, 0, 1], [2004, 1667, 0, 1]]
    STORE["equip"] = [[10, 2001], [4, 2002], [6, 2003], [7, 2004]]
    for k in ("exp", "class_exp", "pw", "pw_cast_seen", "pw_regen_n"):
        feworld._SESSION.pop(k, None)
    feworld._SESSION["in_field"] = True
    feworld._self_char = lambda a: {"name": "Neo", "sex": 0, "look1": SORC}
    # the PRODUCTION defaults, not fe_ui_test's legacy pins -- EXCEPT the
    # curve and the SP schedule: this file's mechanism checks were written
    # against the first pass's `auto` curve and FEZ-era 2/1/0 SP, so those
    # two are PINNED here; the 2006 defaults (`rod`, 1 SP per level) are
    # tools/fe_rod_numbers_test.py's
    d = dict(class_levels=feworld.DEFAULT_CLASS_LEVELS,
             skill_grant=feworld.DEFAULT_SKILL_GRANT,
             sp_per_level=feworld.FEZ_SP_PER_LEVEL,
             exp_model=feworld.DEFAULT_EXP_MODEL,
             exp_curve="auto",
             class_levelup=feworld.DEFAULT_CLASS_LEVELUP,
             class_level_max=feworld.DEFAULT_CLASS_LEVEL_MAX,
             # 3 per tick HERE: these checks pin the tick ARITHMETIC; the
             # shipped amount (16, fewiki 2006) is fe_combat_test's to pin
             pw_cost=feworld.DEFAULT_PW_COST, pw_regen=3,
             pw_max=feworld.DEFAULT_PW_MAX, pw_dedupe_ms=250,
             skill_learn="sp", add_stats={0: 5, 7: 200, 8: 200},
             exp_display="on", exp_next="auto", unit_id="1")
    d.update(kw)
    return _args(**d)


def q(mid, payload=b""):
    return struct.pack(">H", mid) + payload


def ids():
    return [m for m, _, _ in OUT]


def read_1075(body):
    """0x1075 exactly as 0x05051E20 reads it, blocks in bit order."""
    r = Reader(body)
    mask = r.u32()
    got = {"mask": mask}
    if mask & 0x1:
        got["levels"] = [(r.u8(), r.u8()) for _ in range(r.u8())]
    if mask & 0x2:
        got["next"] = r.u32()
    if mask & 0x4:
        got["exp"] = [(r.u8(), r.u32()) for _ in range(r.u8())]
    if mask & 0x8:
        got["skills"] = [(r.u16(), r.u8()) for _ in range(r.u16())]
    r.done()
    return got


def read_2024_i16(body):
    r = Reader(body)
    ma, mb = r.u32(), r.u32()
    v = struct.unpack(">h", struct.pack(">H", r.u16()))[0]
    r.done()
    return ma, mb, v


def equip_validator(level_byte, owned, item_no, sex=0, atk=200, gate2=200):
    """0x05076090, code for code (see feworld's _AVATAR_STATS note and the
    disassembly at 0x050760C1..0x050761A8): 6 no asset for the sex, 1/2 the
    stat gates, 5 a named prerequisite not owned, 4 level too low."""
    it = fegamedata.items()[item_no]
    if it["gate1"] > atk:
        return 1
    if it["gate2"] > gate2:
        return 2
    if it["skills"] and not any(s in owned for s in it["skills"]):
        return 5
    if level_byte < it["level"]:
        return 4
    return 0


def main():
    print("\n-- the curve, the SP schedule, the shipped SP cost")
    a = _fresh()
    check("exp_need auto = 16*L*(L+5): 96 at Lv1, 2400 at Lv10, 27456 at Lv39",
          [feworld.exp_need(a, L) for L in (1, 10, 39)] == [96, 2400, 27456])
    check("exp_need flat:500 and an explicit table (last repeats)",
          feworld.exp_need(_fresh(exp_curve="flat:500"), 7) == 500
          and [feworld.exp_need(_fresh(exp_curve="10,20,30"), L)
               for L in (1, 2, 3, 9)] == [10, 20, 30, 30])
    sched = feworld.sp_schedule(a)
    check("SP earned (FEZ-era): Lv1 2, Lv5 10, Lv6 11, Lv35 40, Lv40 40",
          [feworld.sp_earned(sched, L) for L in (1, 5, 6, 35, 40)]
          == [2, 10, 11, 40, 40])
    check("[skill+0xF0] is 2 for learnable ranks, 0 for proficiencies",
          [feworld.skill_sp_cost(s) for s in (285, 286, 305, 443, 449)]
          == [2, 2, 2, 0, 0])
    check("--sp-per-level off keeps the legacy flat budget",
          feworld.sp_schedule(_fresh(sp_per_level="off")) is None)

    print("\n-- a NEW character starts at Lv1 in its own class")
    a = _fresh()
    rows, _bad = feworld.stored_class_levels(a, SORC)
    check("the default seed is self:1, not all:30", rows == [(SORC, 1)], rows)
    check("...and it is WRITTEN to the character", STORE["class_levels"] == {"2": 1})
    check("the cap is 40", feworld.class_level_cap(a) == 40)
    a2 = _fresh()
    STORE["class_levels"] = {str(i): 30 for i in range(7)}
    check("an EXISTING stored Lv30 table is kept (migration: no demotion)",
          feworld.self_level(a2) == 30)

    print("\n-- a Lv1 character is playable the 2006 way")
    a = _fresh()
    OUT[:] = []
    _direct(feworld.skill_list_push, a)
    body = read_1075(OUT[-1][2])
    owned = {s for s, _v in body["skills"]}
    check("0x1075 carries the level row (2, 1) LAST (it also lands in +0x3AC)",
          body["levels"][-1] == (SORC, 1), body.get("levels"))
    check("the class's starting set is owned: 443 wand, 445 cloth, 270, 275",
          {443, 445, 270, 275} <= owned, sorted(owned))
    check("...and not another class's family (439 axe, 442 bow)",
          not ({439, 442} & owned), sorted(owned))
    codes = {no: equip_validator(1, owned, no) for no in (531, 1666, 1668, 1667)}
    check("every piece of fet_initialize_equip passes the validator at Lv1",
          all(c == 0 for c in codes.values()), codes)
    check("the same kit at level 0 (never sent) fails with code 4 -- what "
          "all:30 was working around", equip_validator(0, owned, 531) == 4)
    hi = [no for no, it in fegamedata.items().items()
          if it["level"] >= 9 and 443 in it["skills"]][:1]
    check("a Lv9+ wand is REFUSED at Lv1 (code 4, the 2006 behaviour)",
          hi and equip_validator(1, owned, hi[0]) == 4, hi)
    check("an axe is refused to a Sorcerer (code 5, no 439)",
          equip_validator(1, owned, 301) == 5)
    rep = fegamedata.cast_report({0: 531, 1: 531}, [270, 275], 0x1E000000,
                                 pow_cur=100)
    check("270 basic magic and 275 cast with the starting wand",
          all(ok for _s, _n, ok, _r in rep), rep)
    check("SP at Lv1 with nothing learned = 2", feworld.skill_points(a) == 2)
    for cls, basic in ((0, 0), (1, 130), (2, 270)):
        start = set(fegamedata.class_start_skills(cls))
        for sex in (0, 1):
            gear = fegamedata.starting_gear(cls, sex)
            codes = {no: equip_validator(1, start, no) for no in gear}
            check("class %d sex %d: all starting gear equips at Lv1" % (cls, sex),
                  all(c == 0 for c in codes.values()), codes)
        rep = fegamedata.cast_report({0: gear[0], 1: gear[0]}, [basic],
                                     0x1E000000, pow_cur=100)
        check("class %d: basic attack %d casts with its starting weapon %d"
              % (cls, basic, gear[0]), rep[0][2] and basic in start, rep)

    print("\n-- field entry: next, SP, Pw")
    a = _fresh()
    OUT[:] = []
    _direct(feworld.exp_resume_push, a)
    check("a FRESH character still gets [unit+0x135c] = 96 (else 0x2059 "
          "can never fire)", read_1075(OUT[0][2]) == {"mask": 0x2, "next": 96},
          OUT and read_1075(OUT[0][2]))

    print("\n-- the HUD EXP gauge's MAX goes out FIRST (2026-09-12)")
    # The gauge caches [unit+0x135c] at construction (0x050CCA9D) and its
    # update bails before storing anything when the exp is above that cache
    # (0x050CE2C0 `ja`), so the max can never be refreshed afterwards. The
    # denominator therefore has to be on the wire ahead of the numerator
    # (skill_list_push carries the per-class EXP dword) and ahead of the
    # 0x100E that releases the field. Five reports of "the bar shows 100%"
    # came from it riding exp_resume_push at the END of that burst.
    OUT[:] = []
    _direct(feworld.exp_max_push, a, "test")
    check("exp_max_push = ONE 0x1075, bit 2 alone, [unit+0x135c] = 96",
          len(OUT) == 1 and read_1075(OUT[0][2]) == {"mask": 0x2, "next": 96},
          [(hex(m), b.hex()) for m, _u, b in OUT])
    src = inspect.getsource(feworld._serve_loop)
    where = {k: src.find(k + "(conn") for k in
             ("exp_max_push", "skill_list_push", "add_complete",
              "exp_resume_push")}
    check("...and the field-entry burst calls it before the numerator, "
          "before 0x100E and before the resume push",
          0 <= where["exp_max_push"] < where["skill_list_push"]
          < where["add_complete"] < where["exp_resume_push"], where)

    OUT[:] = []
    _direct(feworld.progress_entry_push, a)
    got = [read_2024_i16(b) for m, _u, b in OUT if m == 0x2024]
    check("entry pushes SP 2 (maskA 0x1) and Pw 100 (maskA 0x10)",
          got == [(0x1, 0, 2), (0x10, 0, 100)], got)

    print("\n-- a kill: ONE 0x1075 mask 0x6, the client's own EXP channel")
    a = _fresh()
    OUT[:] = []
    _direct(feworld.combat_exp_push, a, 60)
    frames = [read_1075(b) for m, _u, b in OUT if m == 0x1075]
    check("one 0x1075: next 96, [class 2 = 60]",
          frames == [{"mask": 0x6, "next": 96, "exp": [(SORC, 60)]}], frames)
    check("progress stored per class", STORE.get("class_exp") == {"2": 60})
    check("the lifetime total is still the ledger", STORE.get("exp") == 60)
    OUT[:] = []
    _direct(feworld.combat_exp_push, a, 100)
    check("a second kill: 160/96 -- past next, so the CLIENT will ask",
          read_1075(OUT[-1][2])["exp"] == [(SORC, 160)])

    print("\n-- 0x2059: granted only when the stored progress pays")
    OUT[:] = []
    _dispatch(a, q(0x2059))
    check("0x2059 -> 0x1091, 0x1075, 0x2024", ids() == [0x1091, 0x1075, 0x2024],
          [hex(i) for i in ids()])
    check("0x1091 is header-only", OUT and OUT[0][2] == b"")
    st = read_1075(OUT[1][2]) if len(OUT) > 1 else {}
    check("0x1075 mask 0x7: level (2, 2), next 224, remainder 64",
          st == {"mask": 0x7, "levels": [(SORC, 2)], "next": 224,
                 "exp": [(SORC, 64)]}, st)
    check("store: Lv2, 64 into it",
          STORE["class_levels"] == {"2": 2} and STORE["class_exp"] == {"2": 64})
    check("SP pushed = 4 (Lv2 earned 4, nothing learned)",
          len(OUT) > 2 and read_2024_i16(OUT[2][2]) == (0x1, 0, 4))
    OUT[:] = []
    _dispatch(a, q(0x2059))
    check("a request the store cannot pay: NO 0x1091, a resync 0x1075 mask 0x6",
          ids() == [0x1075] and read_1075(OUT[0][2]) ==
          {"mask": 0x6, "next": 224, "exp": [(SORC, 64)]},
          [hex(i) for i in ids()])
    check("...and the level did not move", STORE["class_levels"] == {"2": 2})

    print("\n-- the LEVEL-UP SAFETY NET: why the HUD EXP bar reads 100%")
    # the bar is progress/need CLAMPED (client 0x050CE2FB reads the per-class
    # EXP, 0x050CE346 the threshold, the gauge clamps its current to its max),
    # so an overflow we never convert into a level pins it at full -- and
    # nothing converted it unless the client sent 0x2059.
    import feprog
    b = _fresh(exp_curve="auto")
    STORE["class_levels"], STORE["class_exp"] = {"2": 1}, {"2": 500}
    feworld._SESSION.pop("class_exp", None)
    feworld._SESSION.pop("exp", None)
    cls, lvl, into, need, _sv = feworld.exp_view(b)
    check("the setup is an OVERFLOW: stored progress >= next",
          into >= need and lvl == 1, repr((cls, lvl, into, need)))
    ctx = types.SimpleNamespace(args=b, conn=None, outbound=None, mode=None,
                                be=None, session=feworld._SESSION)
    OUT[:] = []
    feprog.level_fallback(ctx, now=1000.0)
    check("the client gets its turn FIRST -- nothing taken inside "
          "--level-fallback-ms", STORE["class_levels"] == {"2": 1}, STORE["class_levels"])
    feprog.level_fallback(ctx, now=1000.0 + 4.9)
    check("...still nothing at 4.9 s of a 5 s window",
          STORE["class_levels"] == {"2": 1})
    feprog.level_fallback(ctx, now=1000.0 + 5.1)
    check("...and at 5.1 s the level is taken server-side",
          STORE["class_levels"] == {"2": 2}, STORE["class_levels"])
    check("...PAID OUT of the progress (500 - the old next), so the bar drops",
          int(STORE["class_exp"]["2"]) == 500 - need, STORE["class_exp"])
    # 500 still covers Lv2's own next, so another level is owed -- one per
    # window, which is what the client does when it asks repeatedly
    _c2, _l2, into2, need2, _s2 = feworld.exp_view(b)
    check("...and a remaining overflow is left for the NEXT window",
          into2 >= 0 and need2 > need, repr((into2, need2)))
    b.level_fallback = "off"
    STORE["class_levels"], STORE["class_exp"] = {"2": 1}, {"2": 500}
    feworld._SESSION.pop("class_exp", None); feworld._SESSION.pop("exp", None)
    feworld._SESSION.pop("level_owed_at", None)
    feprog.level_fallback(ctx, now=2000.0)
    feprog.level_fallback(ctx, now=2100.0)
    check("--level-fallback off restores the pre-2026-09-12 behaviour",
          STORE["class_levels"] == {"2": 1})

    a = _fresh()
    STORE["class_levels"] = {"2": 1}
    STORE["class_exp"] = {"2": 1000}
    _dispatch(a, q(0x2059))
    _dispatch(a, q(0x2059))
    check("one big kill walks up one level PER request (96 then 224)",
          STORE["class_levels"] == {"2": 3} and STORE["class_exp"] == {"2": 680},
          (STORE["class_levels"], STORE["class_exp"]))

    a = _fresh(class_level_max=5)
    STORE["class_levels"] = {"2": 5}
    STORE["class_exp"] = {"2": 99999}
    OUT[:] = []
    _dispatch(a, q(0x2059))
    r = Reader(OUT[0][2]) if OUT else None
    check("at the cap -> 0x1092 code 1 (the client's own 'max level')",
          ids()[:1] == [0x1092] and r.u32() == 1)
    st = read_1075(OUT[1][2]) if len(OUT) > 1 else {}
    check("...and the served progress is clamped below next (no re-ask loop)",
          st.get("exp") == [(SORC, feworld.exp_need(a, 5) - 1)], st)
    check("store untouched at the cap", STORE["class_levels"] == {"2": 5})
    a = _fresh(class_levelup="off")
    STORE["class_levels"] = {"2": 1}
    STORE["class_exp"] = {"2": 500}
    OUT[:] = []
    _dispatch(a, q(0x2059))
    check("--class-levelup off: logged, nothing sent, nothing stored",
          OUT == [] and STORE["class_levels"] == {"2": 1})

    print("\n-- GET! / LVUP (0x2049) spends the shipped SP cost")
    a = _fresh()
    feworld.stored_class_levels(a, SORC)             # Lv1: 2 SP
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 285, 1)))
    check("GET! 285 -> 0x1075 (owned flag), 0x2024 (SP), 0x1078 -- IN THAT ORDER",
          ids() == [0x1075, 0x2024, 0x1078], [hex(i) for i in ids()])
    check("the 0x1075 marks exactly 285",
          OUT and read_1075(OUT[0][2]) == {"mask": 0x8, "skills": [(285, 1)]})
    check("SP 2 -> 0", len(OUT) > 1 and read_2024_i16(OUT[1][2]) == (0x1, 0, 0))
    check("store: skills = [285]", STORE.get("skills") == [285])
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 305, 1)))
    r = Reader(OUT[0][2]) if OUT else None
    check("GET! 305 with 0 SP -> 0x1079 code 2, nothing stored",
          ids() == [0x1079] and r.u32() == 2 and STORE["skills"] == [285])
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 270, 1)))
    r = Reader(OUT[0][2]) if OUT else None
    check("GET! 270 (the class's own, already owned) -> 0x1079 code 1",
          ids() == [0x1079] and r.u32() == 1)
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 443, 1)))
    r = Reader(OUT[0][2]) if OUT else None
    check("GET! a proficiency (not a learnable rank) -> 0x1079 code 4",
          ids() == [0x1079] and r.u32() == 4)
    STORE["class_levels"] = {"2": 2}                 # Lv2: 4 SP, 2 spent
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 286, 1)))
    check("LVUP 285 -> 286 at Lv2: stored, SP 2 -> 0",
          STORE["skills"] == [285, 286]
          and read_2024_i16(OUT[1][2]) == (0x1, 0, 0))
    a = _fresh()
    STORE["skills"] = [270, 275, 285]
    check("sp_spent counts 285 (2) and not the free 270/275",
          feworld.sp_spent(a, SORC) == 2)
    a = _fresh(skill_learn="legacy")
    OUT[:] = []
    _dispatch(a, q(0x2049, struct.pack(">HB", 305, 1)))
    check("--skill-learn legacy declines -> feworld's builtin 0x1078",
          ids() == [0x1078] and STORE.get("skills") == [305])

    print("\n-- Pw: every skill costs it, and it comes back")
    a = _fresh()
    feworld._SESSION["pw"] = 100
    cast = struct.pack(">IIBIIHBII", 11, 12, 1, 0, 0, 270, 1, 0, 0) \
        + struct.pack(">fff", 1.0, 2.0, 3.0)
    check("the cast body decodes: skill id at offset 17",
          feprog.decode_cast(cast)[0] == 270)
    OUT[:] = []
    _dispatch(a, q(0x1031, cast))
    check("0x1031 skill 270 -> 0x2024 maskA 0x10 Pw 95 ([skill+0xF4] = 5)",
          ids() == [0x2024] and read_2024_i16(OUT[0][2]) == (0x10, 0, 95))
    OUT[:] = []
    _dispatch(a, q(0x1031, cast))
    check("the same skill again inside the dedupe window: not charged twice",
          OUT == [] and feworld._SESSION["pw"] == 95)
    feworld._SESSION["pw_cast_seen"] = {}
    OUT[:] = []
    _dispatch(a, q(0x1032, struct.pack(">IIBIIHBII", 1, 2, 1, 0, 0, 285, 1, 7, 0)))
    check("0x1032 skill 285 (Pow 15) -> 80",
          OUT and read_2024_i16(OUT[0][2]) == (0x10, 0, 80))
    OUT[:] = []
    _dispatch(a, q(0x2028, struct.pack(">I", 0x10) + b"\x02"))
    check("0x2028 bit 0x10, 2 ticks -> Pw 80 + 2*3 = 86",
          OUT and read_2024_i16(OUT[0][2]) == (0x10, 0, 86))
    feworld._SESSION["pw"] = 99
    OUT[:] = []
    _dispatch(a, q(0x2028, struct.pack(">I", 0x810) + b"\x05\x05"))
    check("regen is capped at --pw-max (99 + 15 -> 100)",
          OUT and read_2024_i16(OUT[0][2]) == (0x10, 0, 100))
    feworld._SESSION["pw"] = 3
    feworld._SESSION["pw_cast_seen"] = {}
    OUT[:] = []
    _dispatch(a, q(0x1031, cast))
    check("a debit never goes below 0", read_2024_i16(OUT[0][2]) == (0x10, 0, 0))
    a = _fresh(pw_cost="off")
    feworld._SESSION["pw"] = 100
    OUT[:] = []
    _dispatch(a, q(0x1031, cast))
    check("--pw-cost off: skills are free again", OUT == [])

    # the safety net: no 0x2028 for 7 s -> 2 standing ticks server-side
    a = _fresh(pw_regen_fallback_ms=6000)
    S = feworld._SESSION
    S["pw"], S["pw_tick_at"] = 50, 100.0
    ctx = feworld.Ctx(None, None, "ecb", False, a)
    OUT[:] = []
    _direct(lambda c, o, m, b, args, now: feprog.pump(ctx, now=now), a, 103.0)
    check("fallback idle while the last regen is recent (3 s < 6 s)", OUT == [])
    _direct(lambda c, o, m, b, args, now: feprog.pump(ctx, now=now), a, 107.0)
    check("fallback after 7 s without 0x2028: Pw 50 + 2 ticks * 3 = 56",
          OUT and read_2024_i16(OUT[-1][2]) == (0x10, 0, 56))
    S["pw"] = 100
    OUT[:] = []
    _direct(lambda c, o, m, b, args, now: feprog.pump(ctx, now=now), a, 500.0)
    check("at full Pw the fallback sends nothing and restarts its clock",
          OUT == [] and S["pw_tick_at"] == 500.0)

    print("\n-- !verbs")
    a = _fresh()
    STORE["class_levels"] = {str(i): 30 for i in range(7)}
    STORE["class_exp"] = {"2": 5}
    OUT[:] = []
    _gm(a, "!setlevel 1")
    check("!setlevel 1 resets a stored Lv30 to Lv1, progress 0",
          STORE["class_levels"]["2"] == 1 and STORE["class_exp"] == {"2": 0}
          and STORE["class_levels"]["0"] == 30)
    check("...and pushes the level", 0x1075 in ids()
          and read_1075(OUT[ids().index(0x1075)][2])["levels"] == [(SORC, 1)])
    _gm(a, "!levelup 3")
    check("!levelup 3 -> Lv4 without EXP", STORE["class_levels"]["2"] == 4)

    print()
    feworld._self_char = _REAL_SELF
    if FAILED:
        print("FAILED: %s" % ", ".join(FAILED))
        return 1
    print("[fe_prog_test] OK")
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    if "-v" not in sys.argv:
        _real = print
        _saved = sys.stdout

        class _Null:
            def write(self, *a):
                pass

            def flush(self):
                pass
        sys.stdout = _Null()

        def _check_print(*a, **k):        # noqa: E306
            _real(*a, file=_saved, **k)
        globals()["print"] = _check_print
    sys.exit(main())
