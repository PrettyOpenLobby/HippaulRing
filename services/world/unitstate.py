"""The unit state word [unit+0x2b4] and the 0x2024 stat pushes."""
import struct
from . import progression, wire

# ---------------------------------------------------------------------------
# THE UNIT STATE WORD -- [unit+0x2b4], and why no skill has ever been usable.
#
# Read 2026-09-06 from the cast attempt 0x0507f5xx, which is a ladder of gates
# each with the client's OWN English string:
#
#   1. a selected palette slot        -> "No skill selected."      0x052deecc
#   2. cooldown: now - [unit+0x8d8] >= [unit+0x2a8]
#   3. 0x0504a500(skill, [unit+0x2b4]) -> "Can't use a skill right now."
#   4. 0x0504b7d0(unit, skill)         -> "Skill conditions not met."
#   5. [unit+0x4a4] >= [skill+0xf4]    -> "Not enough Pow to use skill."
#   6. [unit+0x8ac] >= [skill+0xd4]    -> "Not enough crystals (%d short)"
#
# Gate 3 in full (0x0504a500): assemble the 4 bytes at [skill+0xd0] into a
# dword; if it is zero the skill is unconditional, otherwise the skill is
# usable only when (skillmask & [unit+0x2b4]) != 0.
#
# And gate 1 has the same root. A palette slot is a 3-dword entry at
# pal + (slot*3 + 0x15)*4, and Select (vtbl+0x18, 0x051621c0) refuses unless
# the entry's third dword's low byte is set. That byte is written by
# vtbl+0x3c (0x051620f0), which computes, per slot:
#
#     usable = ([skill+0xd0] & 0x1E000000) == 0
#              or ([player+0x2b4] & [skill+0xd0] & 0x1E000000) != 0
#
# THE RETRACTION. an earlier note recorded that "our echo's
# auto-select runs BEFORE that pass". It does not: the inbound 0x2029 worker
# calls the local setter 0x05162360, which calls vtbl+0x3c at 0x0516238e and
# only THEN auto-selects at 0x05162391. The flag is always recomputed. What
# was actually missing is the other operand.
#
# [unit+0x2b4] IS SERVER-OWNED. It has exactly two writers reachable at
# runtime, and both are ours: 0x2024 maskA bit 0x100000 (setter 0x04ff4760 ->
# 0x0506caa0, the real state setter, which fires the transitions) and the
# 0x1006 stat block's own bit 0x100000 (a raw store, applier 0x0507a990).
# The client NEVER sets it locally -- equipping a weapon does not touch it.
# We have never sent either. So the value has been 0 since the ctor
# (0x0507a3f6), every skill whose mask has a 0x1E000000 bit has been
# unselectable, and gate 3 has refused every skill with any mask at all.
#
# WHY 0x1E000000 IS THE SAFE VALUE. 0x0506caa0's transition machinery tests
# 0x2000, 0x40, 0x100000, 0x800000, 0x1000000 and 0x20000000 on the new word
# and fires model/state changes for those. Bits 25..28 are inert to it -- they
# are only ever read as a requirement mask (0x0503cc44, 0x050a8222,
# 0x05162106, 0x0516213b). So 0x1E000000 satisfies both gates and asks the
# client to change nothing else. A wider value (0xFFFFFFFF) would fire every
# transition at once and is not what this flag is for.
#
# WHAT EACH OF BITS 25..28 MEANS is unread. The obvious reading is one bit per
# weapon family, since the same four bits appear in the ITEM channel's
# equipment refresh (0x0503cc20, the 0x1035 record's own player check). Do not
# write that down as fact until a live run separates them: serve 0x1E000000,
# see which skills become selectable, then narrow.
# The stat block the self record used to carry, on the 0x2024 channel (applier
# 0x04ff36c0, gated [+0x20]==3 = the PLAYER, per fe2024_map.py):
#     maskA 0x1    -> +0x498  skill points   (i16)
#     maskA 0x80   -> +0x4ac  equip gate 1   (i16)
#     maskA 0x100  -> +0x4b0  equip gate 2   (i16)
# the same three fields the 0x1006 stat block's bits 0/7/8 wrote, at the same
# widths, so --add-stats keeps its meaning. Only those three ride here: the
# other bits the record map lists (HP/Pw) come from the class table on the
# client's own unit and are not re-served.
_STAT_2024_BITS = {0: 0x1, 7: 0x80, 8: 0x100}


def stat_2024_push(conn, outbound, mode, be, args):
    stats = {b: v for b, v in (getattr(args, "add_stats", None) or {}).items()
             if b in _STAT_2024_BITS}
    if not stats:
        return 0
    if 0 in stats:
        stats[0] = progression.skill_points(args, stats[0])   # the same ledger as the 0x1006
    maskA = 0
    body = b""
    for b in sorted(stats, key=lambda k: _STAT_2024_BITS[k]):
        maskA |= _STAT_2024_BITS[b]
        body += struct.pack(">h", int(stats[b]))
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">II", maskA, 0) + body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 maskA=0x%X STAT BLOCK on the client's own unit: %s"
          % (maskA, ", ".join("bit%d(+0x%03X)=%d" % (b, {0: 0x498, 7: 0x4AC, 8: 0x4B0}[b],
                                                        stats[b]) for b in sorted(stats))),
          flush=True)
    return maskA


def unit_state_push(conn, outbound, mode, be, args):
    """0x2024 maskA bit 0x100000 -- [unit+0x2b4] through 0x0506caa0."""
    v = getattr(args, "unit_state", None)
    if v is None:
        return
    wire.send(conn, outbound,
         wire.inner_msg(0x2024, struct.pack(">III", 0x100000, 0, v & 0xFFFFFFFF),
                   wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 maskA=0x100000 UNIT STATE 0x%08X -> "
          "[unit+0x2b4] via 0x0506caa0. Bits 25..28 (0x1E000000) are the "
          "skill gate: with them clear, Select refuses every skill whose "
          "[skill+0xd0] names a weapon family, and the cast refuses with "
          "\"Can't use a skill right now.\"" % (v & 0xFFFFFFFF), flush=True)
