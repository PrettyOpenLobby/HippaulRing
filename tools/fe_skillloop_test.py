#!/usr/bin/env python3
"""fe_skillloop_test.py -- levels, skill points and GET!/LVUP, real defaults.

Run from services/:  python ../tools/fe_skillloop_test.py

2026-10-08 (round 2). Prod has had four level-ups and not one 0x2049 since
09-23. A fresh Warrior goes through the real read loop:

  * the budget: 1 SP per level (RoD, fewiki memo 2006-05-26; a Lv1 holds 1,
    the manual's Status screenshot), every learnable rank costs its shipped
    [skill+0xF0] = 2 (the window's own test, 0x050D3A64), the class's
    starting set (FE_CLASS_BASIC_PARAM_DATA +0x70) is free
  * 0x2059 -> 0x1091 + one 0x1075 (level, next, progress) + the new SP on
    0x2024 maskA 0x1, paid from stored EXP only
  * 0x2049 [u16 rank][u8 1] -> the owned flag (0x1075 bit 0x8) BEFORE 0x1078,
    the SP after; short of SP -> 0x1079 [u32 2]
  * the client's own tree (fegamedata.skill_tree, read off 0x050D5010): a
    rank 1 needs every prerequisite group, a rank N needs rank N-1, another
    class's family is code 4
  * what was learned is served again on the next entry
"""
import struct
import sys

import fe_harness as H
from fe_harness import check, ids, bodies, say

U2024_SP = 0x1


def sp_of(box):
    out = []
    for b in bodies(box, 0x2024):
        if struct.unpack_from(">I", b)[0] == U2024_SP:
            out.append(struct.unpack_from(">h", b, 8)[0])
    return out


def skill_bits(box):
    """The skill words a 0x1075 sets (mask bit 0x8, 0x05052180)."""
    out = []
    for b in bodies(box, 0x1075):
        mask = struct.unpack_from(">I", b)[0]
        if mask == 0x8:
            n = struct.unpack_from(">H", b, 4)[0]
            out += [struct.unpack_from(">HB", b, 6 + 3 * i) for i in range(n)]
    return out


def main():
    H.setup()
    import fegamedata
    from world import progression
    args = H.real_args()
    check("real defaults: SP 1 a level, --skill-learn sp, --skill-tree check, "
          "RoD curve", args.skill_learn == "sp" and args.skill_tree == "check"
          and str(args.sp_per_level).startswith("1:1-40")
          and args.exp_curve == "rod", (args.sp_per_level, args.exp_curve))
    p = H.Peer("Warrior", 71, args, nation=1, field=17, char={
        "look1": 0, "class_levels": {"0": 1}, "class_exp": {"0": 0},
        "skills": []})
    try:
        sp, _ = p.call(progression.skill_points, args)
        check("a Lv1 Warrior holds 1 SP (the manual's Status screen)", sp == 1, sp)
        out = p.send(0x2049, struct.pack(">HB", 10, 1))
        check("GET! Heavy Slash L1 (2 SP) with 1 -> 0x1079 [u32 2]",
              bodies(out, 0x1079) == [struct.pack(">I", 2)], out)
        # ---- a level-up, paid from stored EXP (Lv1 needs 32)
        p.char["class_exp"] = {"0": 31}
        p.session.pop("class_exp", None)
        out = p.send(0x2059)
        check("0x2059 with 31 of 32 EXP: no level (a resync, no 0x1091)",
              0x1091 not in ids(out), ids(out))
        p.char["class_exp"] = {"0": 40}
        p.session.pop("class_exp", None)
        out = p.send(0x2059)
        check("0x2059 with 40: 0x1091, the 0x1075 and SP 2 on 0x2024",
              0x1091 in ids(out) and 0x1075 in ids(out) and sp_of(out) == [2],
              (ids(out), sp_of(out)))
        check("stored: Lv2, 8 EXP carried into it",
              p.char["class_levels"] == {"0": 2}
              and int(p.char["class_exp"]["0"]) == 8, p.char["class_exp"])
        # ---- GET! with the SP for it
        out = p.send(0x2049, struct.pack(">HB", 10, 1))
        order = [m for m in ids(out) if m in (0x1075, 0x2024, 0x1078)]
        check("GET! Heavy Slash L1: 0x1075 owned flag, SP 0, then 0x1078",
              skill_bits(out) == [(10, 1)] and sp_of(out) == [0]
              and order.index(0x1075) < order.index(0x1078)
              and bodies(out, 0x1078) == [b""], (order, skill_bits(out)))
        out = p.send(0x2049, struct.pack(">HB", 10, 1))
        check("the same rank again -> 0x1079 [u32 1] already learned",
              bodies(out, 0x1079) == [struct.pack(">I", 1)], out)
        # ---- the tree, the client's own rule
        p.char["class_levels"] = {"0": 10}
        out = p.send(0x2049, struct.pack(">HB", 12, 1))
        check("LVUP to Heavy Slash L3 without L2 -> 0x1079 [u32 2]",
              bodies(out, 0x1079) == [struct.pack(">I", 2)], out)
        out = p.send(0x2049, struct.pack(">HB", 15, 1))
        check("Behemoth Tail L1 without Heavy Slash L3 (its group) -> [u32 2]",
              bodies(out, 0x1079) == [struct.pack(">I", 2)], out)
        out = p.send(0x2049, struct.pack(">HB", 155, 1))
        check("a Scout's Fast Shot as a Warrior -> 0x1079 [u32 4]",
              bodies(out, 0x1079) == [struct.pack(">I", 4)], out)
        for sid in (11, 12):
            out = p.send(0x2049, struct.pack(">HB", sid, 1))
            check("LVUP Heavy Slash to rank %d -> 0x1078" % (sid - 9),
                  bodies(out, 0x1078) == [b""], out)
        out = p.send(0x2049, struct.pack(">HB", 15, 1))
        check("...now Behemoth Tail L1 is offered and taken",
              bodies(out, 0x1078) == [b""], out)
        sp, _ = p.call(progression.skill_points, args)
        check("Lv10 earned 10 SP, four ranks at 2 spent: 2 left", sp == 2, sp)
        check("the store holds the four ranks",
              sorted(p.char["skills"]) == [10, 11, 12, 15], p.char["skills"])
        served, _ = p.call(progression.served_skills, args)
        check("served on entry: the four plus the free start set (0, 5)",
              {0, 5, 10, 11, 12, 15} <= set(served), sorted(served))
        ok, why = fegamedata.skill_tree_ok(0, 25, [14, 22])
        check("the tree's groups: Crumble Storm needs HS L5 AND (BT L3 or SS L3)",
              ok and not fegamedata.skill_tree_ok(0, 25, [17, 22])[0], why)
    finally:
        p.leave()
    return H.finish("fe_skillloop_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
