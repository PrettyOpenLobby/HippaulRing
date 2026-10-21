#!/usr/bin/env python3
"""fe_shield_test.py -- the castle shield and the culture level (2026-10-08).

Run from the repo root:  python tools/fe_shield_test.py

What the client does with each, read off the dump (world/buildings.py and
world/campaignview.py carry the full reading):

    0x1142  [u32 mask][bit 0x4: u16] -> [field+0x58], the percent the
            shield_castle model fades to (0x050715a3, x2.55 -> alpha) on a
            type-15 castle outside a capital (0x05070602)
    0x3027  +0x5f4 u16 m_FormerCultureLevel (parser 0x05005090), read by the
            client's debug log only

The rules are OURS (SE's pages give no numbers): a nation at or above
--castle-shield-base fields has no shield, every field below adds 100/base
percent and the castle's war HP grows by --castle-shield-hp percent of
--keep-hp at a full shield; culture = 1 + held // --culture-step, capped 8.
"""
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.join(os.path.dirname(_HERE), "services")
for p in (_SERVICES, _HERE):
    if p not in sys.path:
        sys.path.insert(0, p)

import fegamedata  # noqa: E402
import fenet       # noqa: E402
import feworld     # noqa: E402
from fe_ui_test import Reader, _args, _stub_store   # noqa: E402

OUT = []
FAILS = []


def check(label, cond, detail=""):
    print("  %-66s %s%s" % (label, "PASS" if cond else "FAIL",
                             "" if cond or detail == "" else "  %r" % (detail,)))
    if not cond:
        FAILS.append(label)


def _capture(conn, mid, body, prefix):
    unit, msg = struct.unpack_from(">IH", body, 0)
    OUT.append((msg, unit, body[6:]))


def _hold(nation, areas):
    """Hand `areas` to `nation` in the in-memory territory map (no store)."""
    for a in areas:
        feworld.territory_set(a, nation)


def main():
    if not fegamedata.areas():
        print("[fe_shield_test] SKIP: no fedata (FE_GAMEDATA_DIR)")
        return 0
    _stub_store()
    saved = (fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame)
    fenet.bf_encrypt = lambda st, body, mode, be: body
    fenet.traffic_wrap = lambda data, seq=1: data
    feworld.send_world_frame = _capture
    try:
        run()
    finally:
        fenet.bf_encrypt, fenet.traffic_wrap, feworld.send_world_frame = saved
    if FAILS:
        print("[fe_shield_test] %d FAILED: %s" % (len(FAILS), FAILS))
        return 1
    print("[fe_shield_test] OK")
    return 0


def run():
    a = _args(unit_id="1", castle_shield="on", castle_shield_base=19,
              castle_shield_hp=100, culture_level="on", culture_step=5,
              keep_hp=3000)
    areas = sorted(fegamedata.areas())
    # a clean board: nation 1 gets 19 (its share), nation 2 gets 9, nation 3
    # nothing; the rest stay with whoever dat.pak names
    with feworld.territory._TERRITORY_LOCK:
        feworld.territory._TERRITORY.clear()
    _hold(1, areas[:19])
    _hold(2, areas[19:28])
    _hold(5, areas[28:95])
    print("the castle shield (book 191/266)")
    check("a nation at its share (19 fields) has no shield",
          feworld.castle_shield_pct(a, 1) == 0, feworld.castle_shield_pct(a, 1))
    check("9 fields: 10 below the base -> 53% (round(10 * 100 / 19))",
          feworld.castle_shield_pct(a, 2) == 53, feworld.castle_shield_pct(a, 2))
    check("no fields at all: a full shield, 100%",
          feworld.castle_shield_pct(a, 3) == 100, feworld.castle_shield_pct(a, 3))
    check("more than the share is still 0, never negative",
          feworld.castle_shield_pct(a, 5) == 0, feworld.castle_shield_pct(a, 5))
    check("nation 0 (no holder) has none", feworld.castle_shield_pct(a, 0) == 0)
    check("the castle's war HP: 3000 x (1 + 0.53 x 1.0) = 4590 for nation 2",
          feworld.castle_shield_hp(a, 2, 3000) == 4590, feworld.castle_shield_hp(a, 2, 3000))
    check("...3000 unchanged for the nation at its share",
          feworld.castle_shield_hp(a, 1, 3000) == 3000)
    check("...6000 at a full shield (--castle-shield-hp 100)",
          feworld.castle_shield_hp(a, 3, 3000) == 6000)
    check("...clamped to the i16 the building record carries",
          feworld.castle_shield_hp(a, 3, 30000) == 32767)
    a_half = _args(unit_id="1", castle_shield_hp=50)
    check("--castle-shield-hp 50: 3000 -> 4500 at a full shield",
          feworld.castle_shield_hp(a_half, 3, 3000) == 4500)
    a_off = _args(unit_id="1", castle_shield="off")
    check("--castle-shield off: 0% and the plain HP",
          feworld.castle_shield_pct(a_off, 3) == 0
          and feworld.castle_shield_hp(a_off, 3, 3000) == 3000)

    print("the 0x1142 push at field entry")
    feworld._SESSION["in_field"] = True
    fld = [x for x in areas[19:28] if x not in feworld.CAPITAL_GROUP_IDS][0]
    feworld._SESSION["field"] = fld                     # held by nation 2, not a capital
    OUT[:] = []
    pct = feworld.castle_shield_push(None, None, "ecb", False, a)
    check("a field of nation 2 -> 0x1142 [u32 0x4][u16 53]",
          pct == 53 and [m for m, _, _ in OUT] == [0x1142]
          and OUT[0][2] == struct.pack(">IH", 0x4, 53), OUT)
    r = Reader(OUT[0][2])
    r.u32()
    r.u16()
    r.done()
    check("...the body is exactly mask + one u16 (the applier's bit-0x4 read)", True)
    feworld._SESSION["field"] = 21                      # a capital
    OUT[:] = []
    pct = feworld.castle_shield_push(None, None, "ecb", False, a)
    check("a capital gets 0 (the loader never mounts the model there)",
          pct == 0 and OUT and OUT[0][2] == struct.pack(">IH", 0x4, 0), OUT)
    feworld._SESSION.pop("field", None)
    OUT[:] = []
    check("no field -> nothing sent",
          feworld.castle_shield_push(None, None, "ecb", False, a) is None and OUT == [])
    OUT[:] = []
    feworld._SESSION["field"] = fld
    check("--castle-shield off sends nothing",
          feworld.castle_shield_push(None, None, "ecb", False, a_off) is None and OUT == [])

    print("the culture level (book 174: eight steps by fields held)")
    cl = feworld.culture_level
    check("0..4 fields = 1, 5..9 = 2, 19 = 4, 35+ = 8 (capped)",
          [cl(0, a), cl(4, a), cl(5, a), cl(9, a), cl(19, a), cl(35, a), cl(95, a)]
          == [1, 1, 2, 2, 4, 8, 8],
          [cl(n, a) for n in (0, 4, 5, 9, 19, 35, 95)])
    vals = feworld.campaign_force_vals(a, 2, {"name": "Two"})
    check("campaign_force_vals fills m_FormerCultureLevel from the holdings (9 -> 2)",
          vals.get("FormerCultureLevel") == 2 and vals.get("name") == "Two", vals)
    rec = feworld.build_force_record(vals)
    # the record: u32 f0c, cstr name, u32 f34, u32 pair count (+pairs), i8,
    # i16, cstr, cstr, u32 FormerNumMember, u32 FormerNumTerritory, u16 culture
    r = Reader(rec)
    r.u32()
    r.cstr()
    r.u32()
    n = r.u32()
    for _ in range(n):
        r.u32()
        r.u32()
    r.u8()
    r.u16()
    r.cstr()
    r.cstr()
    r.u32()
    r.u32()
    culture = r.u16()
    check("...and it lands at the parser's +0x5f4 slot (u16 after the two "
          "Former counts, 0x05005216)", culture == 2, culture)
    vals = feworld.campaign_force_vals(_args(unit_id="1", culture_level="off"), 2,
                                       {"name": "Two"})
    check("--culture-level off leaves the field alone",
          "FormerCultureLevel" not in vals, vals)


if __name__ == "__main__":
    sys.exit(main())
