"""Stat probes for decoding 0x2024/0x2025 fields."""
import struct
from . import wire

def _stat_num(x):
    """--add-stats value: an int (any base) or, for the f32 rows, a float."""
    x = x.strip()
    if x.lower().startswith("0x"):
        return int(x, 16)
    return float(x) if ("." in x or "e" in x.lower()) else int(x, 0)


# 0x2024 maskA bits whose destination is CONFIRMED, that read live as ZERO, and
# that nothing else writes -- so a value appearing on screen can only be ours.
# (bit, offset, struct code, sentinel). Ascending bit order IS the wire order.
#
# DELIBERATELY EXCLUDED, and each for its own reason:
#   0x1     +0x498  Skill Points -- another worker's --add-stats owns it
#   0x2/0x8/0x10    HP and Pw -- already carry 200/200 and 100/100, so they
#                   have a writer; a second one would silently drift
#   0x4             not a setter at all: it drives the damage-number colour
#                   palette at 0x5336b60
#   0x20000 +0x4d4  OUR WALK SPEED, live at 1.0. Overwriting it would break
#                   movement mid-probe and confuse the result.
#   0x100000, 0x400000, 0x800000, 0x1000000, 0x2000000, 0x8000000 -- their
#                   destinations did NOT fall out of the scan, so I cannot say
#                   the analyser tracked every read they make. A missed read is
#                   a desync of everything after it, not a wrong number.
#
# KEY: 2026-08-27: fe2024.py's ladder() never saw the eight `test ah,N` arms,
# so maskA bits 0x100..0x8000 (+0x4b0..+0x4ca, all i16 via 0x5045ec0) were
# absent from its table and read as "no such bit". They are in now, with the
# next sentinels, 4011..4017 -- EXCEPT bit 0x800: its setter 0x04ff41b0 is
# not a store, it is the KNOCKBACK EVENT (gates [+0x20]==3 AND [+0x24]==1,
# writes +0x4bc, and if the value >= [+0x4b8]+[+0x4ba] launches a 2500 ms
# flight with anims 0x57/0x58). A sentinel there throws the player.
_U2024_PROBE = [
    (0x00000020, 0x4A6, "H", 4001),
    (0x00000040, 0x4AA, "H", 4002),
    (0x00000080, 0x4AC, "H", 4003),
    (0x00000100, 0x4B0, "H", 4011),
    (0x00000200, 0x4B4, "H", 4012),
    (0x00000400, 0x4B8, "H", 4013),
    (0x00001000, 0x4BE, "H", 4014),
    (0x00002000, 0x4C2, "H", 4015),
    (0x00004000, 0x4C6, "H", 4016),
    (0x00008000, 0x4CA, "H", 4017),
    (0x00010000, 0x4CE, "H", 4004),
    (0x00040000, 0x4DC, "f", 4005.0),
    (0x00080000, 0x4E4, "f", 4006.0),
    (0x00200000, 0x4EC, "I", 4007),
    (0x04000000, 0x4F0, "I", 4008),
    (0x20000000, 0x4FC, "H", 4009),
    (0x40000000, 0x4FE, "H", 4010),
]


def stat_probe_push(conn, outbound, mode, be, args):
    """Name the unnamed half of the 0x2024 stat block with DISTINCT SENTINELS.

    2026-08-25. `felive --avatar` read the whole block before anything was
    served, and it sorted three ways: HP and Pw already carry correct values
    (so they have a writer), Skill Points has another channel, and TEN fields
    are zero AND unnamed. Serving plausible numbers into those would be
    an earlier note exactly -- every screen still renders
    and nothing is learned.

    So each gets an IMPOSSIBLE, DISTINCT value, 4001..4010, and one look at the
    Status screen and the gauges names them all at once. It is the same trick
    that settled `0x303E` in a single screenshot: nine sentinels made "the
    message did nothing" readable, where nine plausible numbers would have
    rendered a believable screen full of our own data and taught us nothing.

    The last digit identifies the field, so a number on screen maps straight
    back: 4001 = +0x4a6, 4005 = +0x4dc, 4010 = +0x4fe.

    WARNING: THIS PUTS DELIBERATELY WRONG NUMBERS ON SCREEN. It is off by default and
    is not something to leave running.

    WARNING: ASCENDING BIT ORDER IS THE WIRE ORDER -- 0x04ff36c0 tests bit 0 first and
    each setter reads as it goes, so the fields must be laid down in that
    order. A bit set with nothing behind it desynchronises the rest.
    """
    if args.stat_probe == "off":
        return
    maskA = 0
    body = b""
    for bit, _off, code, val in _U2024_PROBE:
        maskA |= bit
        body += struct.pack(">" + code, val)
    out = struct.pack(">II", maskA, 0) + body
    wire.send(conn, outbound, wire.inner_msg(0x2024, out, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 STAT PROBE maskA=0x%08X, %d fields, %d bytes. "
          "Sentinels 4001..4010; the LAST DIGIT names the field: %s"
          % (maskA, len(_U2024_PROBE), len(out),
             ", ".join("%d=+0x%x" % (v if isinstance(v, int) else int(v), o)
                       for _b, o, _c, v in _U2024_PROBE)), flush=True)
    print("[feworld]       open the Status screen AND look at the gauges. A "
          "4001..4010 anywhere on screen names that offset. Nothing anywhere "
          "means these fields are not drawn, which is also an answer.",
          flush=True)


# 0x2025 (arm 0x0503a6d1 -> 0x04ff39a0) is the SECOND COLUMN of the same
# stat block: ONE u32 mask (no maskB), and every setter is the 0x2024 one
# called with index 1, so it writes [unit + base + 2] (i16) or [+4] (f32).
# Mapped 2026-08-27 by the stat-block bit sweep:
#     bit 0x2 -> +0x49c  HP bonus (drawn: HP max = +0x49a + THIS)
#     bit 0x4 -> +0x4a2  Pw bonus          0x8 -> +0x4a8
#     bit 0x10 -> +0x4ae 攻撃力 2nd term  (Status: +0x4ac + +0x4ae + +0x514 + +0x516)
#     bit 0x20 -> +0x4b2 equip-gate-2 2nd term   0x40 -> +0x4b6
#     bit 0x80 -> +0x4ba knockback-threshold 2nd term
#     0x100/0x200/0x400/0x800/0x1000 -> +0x4c0/+0x4c4/+0x4c8/+0x4cc/+0x4d0
#     0x2000 -> +0x4d8 (f32, the controller's OWN speed slot -- excluded)
#     0x4000 -> +0x4e0 / 0x8000 -> +0x4e8 (jump physics 2nd terms -- excluded)
#     0x10000 -> +0x508 / 0x20000 -> +0x510 (f32 -- excluded)
# Sentinels 5001..5012, last two digits name the field; HP/Pw bonuses are
# INCLUDED on purpose -- the gauge draws max = base + bonus, so "HP 200/5201"
# is the readable proof that +0x49c is the bonus column.
_U2025_PROBE = [
    (0x00000002, 0x49C, "h", 5001),
    (0x00000004, 0x4A2, "h", 5002),
    (0x00000008, 0x4A8, "h", 5003),
    (0x00000010, 0x4AE, "h", 5004),
    (0x00000020, 0x4B2, "h", 5005),
    (0x00000040, 0x4B6, "h", 5006),
    (0x00000080, 0x4BA, "h", 5007),
    (0x00000100, 0x4C0, "h", 5008),
    (0x00000200, 0x4C4, "h", 5009),
    (0x00000400, 0x4C8, "h", 5010),
    (0x00000800, 0x4CC, "h", 5011),
    (0x00001000, 0x4D0, "h", 5012),
]


def stat_probe2_push(conn, outbound, mode, be, args):
    """DISTINCT SENTINELS into the 0x2025 second column. Off by default."""
    if args.stat_probe2 == "off":
        return
    mask = 0
    body = b""
    for bit, _off, code, val in _U2025_PROBE:
        mask |= bit
        body += struct.pack(">" + code, val)
    out = struct.pack(">I", mask) + body
    wire.send(conn, outbound, wire.inner_msg(0x2025, out, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2025 STAT PROBE (2nd column) mask=0x%08X, %d "
          "fields, %d bytes. Sentinels 5001..5012: %s"
          % (mask, len(_U2025_PROBE), len(out),
             ", ".join("%d=+0x%x" % (v, o) for _b, o, _c, v in _U2025_PROBE)),
          flush=True)
    print("[feworld]       expect HP x/5201 and Pw x/5102 on the gauges (max = "
          "base + bonus), 攻撃力 up by 5004 on the Status screen; anything "
          "else on screen names its offset by its last two digits.", flush=True)
