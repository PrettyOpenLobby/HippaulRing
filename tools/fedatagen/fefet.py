#!/usr/bin/env python3
"""fefet.py -- dump Fantasy Earth's `fet_*` game tables out of dat.pak to TSV.

Reads the DECRYPTED dat.pak from your own Fantasy Earth client install (the
file this tool expects is conventionally named dat.jp.dec; pass its path with
--dat). Found 2026-09-05: dat.pak carries five binary tables that plain
string harvesting never read, and they are most of the game's PvE content:

    fet_npc_generator_pos    720 spawn points: field id (= the 0x3031 group id
                             space, 1..90; capitals 21/39/57/62/78 ABSENT),
                             world x/y/z, radius, spawner link
    fet_npc_generator_data   192 spawners: which npc types each emits
    fet_npc_type             1372 NPC types: name, modeltype, script id --
                             including the TOWN NPCs (Warrior_Weapon_Shop,
                             Item_Shop, ..., script ids 2101..2111)
    fet_castle_info          castle grid position per Hmap map (1..14, 100..105)
    fet_initialize_equip_item_data  starting equipment per class, male|female
    FE_ITEM_DATA             598 items: name, slot type, the two STAT gates,
                             the required class level and the up-to-two
                             PREREQUISITE SKILL ids the equip validator tests

WARNING: FE_ITEM_DATA's file record is NOT the client's struct. The client parses it
into a fixed-layout record (that is what `[tbl+0x50]`, `[tbl+0x88]` and friends
address); the file stores the name as `[u32 len][bytes]` where the struct has a
fixed 0x20-byte array, so every field AFTER the name sits at
`struct_off + namelen - 32` in the file. That constant was not guessed: it is
the one that makes `[tbl+0x88]/[tbl+0x8a]` read 0xffff for every material and
a single weapon-proficiency id for every weapon (436 dagger, 437-439 axe/sword,
440 great axe, 441 hammer, 442 bow, 443 wand, 444 shield, 445-447 armour), and
`[tbl+0x86]` read 0 for all 598 rows instead of nonsense. The slot type is the
one field that does NOT follow it -- it lands 3 bytes earlier (`0x50` in the
struct, `0x4d + namelen - 32` in the file), which is why it has its own
constant below. Both were cross-checked against the shipped table: the slot
column reproduces the previous dump byte for byte.

Record framing is the archive's own: `[u32 len][class tag][fields]`, the same
DATASTREAM serialisation febuild.py reads for the building tables, and the
records are found by scanning for the length-prefixed tag rather than by the
TOC, whose base offset does not line up. Field layouts below were read off
the bytes, not off the client's parsers (0x051a5xxx..0x051a8xxx) -- the
column NAMES are guesses where marked `?`.

    python fefet.py --dat /path/to/dat.jp.dec
        -> writes fe-fet-*.tsv into services/fedata/ (override with --out)
"""
import collections
import io
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
#: Default input: a decrypted dat.pak dropped next to this script.
DEFAULT_DAT = os.path.join(HERE, "dat.jp.dec")
#: Default output: the server's shipped-data directory.
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "..", "services",
                                            "fedata"))
#: Where dump() writes; set from --out in main().
OUTDIR = DEFAULT_OUT


def marks(blob, tag):
    out, o = [], 0
    while True:
        i = blob.find(tag, o)
        if i < 0:
            return out
        if i >= 4 and struct.unpack_from("<I", blob, i - 4)[0] == len(tag):
            out.append(i - 4)
        o = i + 1


def records(blob, tag, tail=256):
    ms = marks(blob, tag)
    for k, m in enumerate(ms):
        s = m + 4 + len(tag)
        e = ms[k + 1] if k + 1 < len(ms) else s + tail
        yield blob[s:e]


def f(v):
    return struct.unpack("<f", struct.pack("<I", v))[0]


# --------------------------------------------------------------------------
# 2026-09-06 (second pass): READ THESE TWO TABLES THE WAY THE CLIENT DOES.
#
# The first pass guessed a "skew" between the file record and the client's
# struct, because the file stores a name as [u32 len][bytes] where the struct
# has a fixed array. That works for the fields before the LAST variable-length
# thing and nowhere else, and SKILL_DATA has five of them, so the skill table
# was emitted as hex and its most useful columns were left unread.
#
# KEY: There is no skew to find, because the two layouts are not the same fields
# shifted -- they are the same fields DIFFERENTLY ENCODED. Every bitfield in
# the file is `[u32 n][n one-byte booleans]`, which the parser packs into a
# fixed byte array LSB-first; every string is length-prefixed; every array is
# counted. So the only correct reader is the client's own read ORDER, and that
# is recoverable in one pass: both parsers are a straight line of
# `push <size>; push <dest>; call 0x5186820` with the destination as
# `lea reg,[esi+OFFSET]`, so the sequence below was read off the disassembly,
# not inferred.
#
#     SKILL_DATA    parser 0x051b10c8   (found from the tag string 0x052f24c4)
#     FE_ITEM_DATA  parser 0x051976f0   (found from the tag string 0x052f1928)
#
# THE CHECK, and it is a real one: the item reader below reproduces the slot
# type and the prerequisite-skill columns of the previously shipped TSV for
# ALL 598 rows, and those two columns were themselves cross-checked months ago
# against the axe/bow/wand/armour split. Zero mismatches.


class _Rec(object):
    """The client's own stream primitive (0x5186820) over one file record."""

    def __init__(self, b):
        self.b, self.o = b, 0

    def u(self, n):
        v = int.from_bytes(self.b[self.o:self.o + n], "little")
        self.o += n
        return v

    def s(self):
        n = self.u(4)
        v = self.b[self.o:self.o + n]
        self.o += n
        return v.decode("cp932", "replace")

    def arr(self, w):
        return [self.u(w) for _ in range(self.u(4))]

    def bits(self):
        """[u32 n][n booleans] -> the packed bitfield the struct holds."""
        n, v = self.u(4), 0
        for i in range(n):
            if self.b[self.o + i]:
                v |= 1 << i
        self.o += n
        return v


def skill_record(rec):
    """SKILL_DATA, in the client's read order. Struct offsets in comments.

    The four fields that decide whether a skill can be used at all:
      +0xd0  the required UNIT STATE mask, tested against [unit+0x2b4] by
             0x0504a500 -- gate 3, "Can't use a skill right now."
      +0x12c the required WEAPON-CATEGORY mask, tested by 0x0504b7d0 against
             [itemtable+0x1cc] of whatever is in worn slots 0 and 1 ONLY --
             gate 4, "Skill conditions not met."
      +0xf4  the Pow cost, vs [unit+0x4a4] -- gate 5.
      +0xd4  the crystal cost, vs [unit+0x8ac] -- gate 6.
    and the two the palette's Select tests: +0x1d8 bit 5 and +0x12e bit 2.
    """
    r = _Rec(rec)
    d = {"id": r.u(2), "x02": r.u(2), "family": r.u(2), "rank": r.u(1),
         "x07": r.u(1)}
    d["name"] = r.s()                      # +0x08
    d["effect"] = r.s()                    # +0x88
    d["xa8"] = r.u(4)
    d["str_ac"] = r.s()                    # +0xac
    d["xcc"] = r.u(4)
    d["state"] = r.bits()                  # +0xd0   GATE 3
    d["crystal"] = r.u(4)                  # +0xd4   GATE 6
    for o in (0xd8, 0xdc, 0xe0, 0xe4, 0xe8, 0xec):
        d["x%x" % o] = r.u(4)
    d["xf0"] = r.u(2)
    d["xf2"] = r.u(1)
    d["xf3"] = r.u(1)
    d["pow"] = r.u(2)                      # +0xf4   GATE 5
    d["xf6"] = r.u(2)
    d["xf8"] = r.u(4)
    d["xfc"] = r.u(4)
    d["x100"] = r.u(1)
    d["b101"] = r.bits()
    d["x102"] = r.u(2)
    d["x104"] = r.u(2)
    d["x106"] = r.u(2)
    d["a108"] = r.arr(2)
    d["b128"] = r.bits()
    d["x12a"] = r.u(2)
    d["weapon"] = r.bits()                 # +0x12c  GATE 4
    d["sel12e"] = r.bits()                 # +0x12e  palette Select gate 2
    d["x130"] = r.u(2)
    d["x134"] = r.u(4)
    d["x138"] = r.u(1)
    d["x139"] = r.u(1)
    d["x13a"] = r.u(2)
    d["x13c"] = r.u(4)
    d["x140"] = r.u(4)
    d["x144"] = r.u(4)
    d["x148"] = r.u(2)
    d["x14a"] = r.u(2)
    d["x14c"] = r.u(2)
    d["str_14e"] = r.s()
    d["x1ce"] = r.u(1)
    d["x1d0"] = r.u(4)
    d["x1d4"] = r.u(4)
    d["sel1d8"] = r.bits()                 # +0x1d8  palette Select gate 1
    return d


def item_record(rec):
    """FE_ITEM_DATA, in the client's read order.

    +0x1cc is the WEAPON-CATEGORY bitfield gate 4 matches a skill against. It
    is what makes a wand a wand: slot type 10 holds four weapon families and
    only the category bit tells them apart (0x200 staff, 0x08/0x20/0x40 the
    others), so "slot type" alone can never answer "can I cast with this".
    """
    r = _Rec(rec)
    d = {"id": r.u(2), "x04": r.u(4), "x08": r.u(4)}
    d["name"] = r.s()                      # +0x0c
    d["x34"] = r.u(1)
    for o in (0x36, 0x38, 0x3a, 0x3c, 0x3e):
        d["x%x" % o] = r.u(2)
    d["a40"] = r.arr(4)
    d["slot"] = r.u(4)                     # +0x50
    d["x54"] = r.u(2)
    d["x56"] = r.u(2)
    d["x58"] = r.u(1)
    d["x5a"] = r.u(2)
    d["x5c"] = r.u(4)
    for o in range(0x60, 0x88, 2):
        d["x%x" % o] = r.u(2)
    d["skills"] = r.arr(2)                 # +0x88 prerequisite proficiencies
    d["a8c"] = r.arr(1)
    d["x90"] = r.u(4)
    d["x94"] = r.u(2)
    d["x96"] = r.u(1)
    for k in ("a98", "aa4", "ab0", "abc"):
        d[k] = r.arr(4)
    d["model_m"] = r.s()                   # +0xc8
    d["model_f"] = r.s()                   # +0x148
    d["x1c8"] = r.u(4)
    d["category"] = r.bits()               # +0x1cc  GATE 4
    d["x1d0"] = r.u(4)
    d["x1d4"] = r.u(4)
    d["x1d8"] = r.u(4)
    d["s1dc"] = r.s()
    d["x25c"] = r.u(2)
    d["x25e"] = r.u(2)
    d["x260"] = r.u(2)
    d["b262"] = r.bits()
    d["a264"] = r.arr(1)
    return d


def _parsed(blob, tag, fn):
    out = []
    ms = marks(blob, tag)
    for k, m in enumerate(ms):
        s = m + 4 + len(tag)
        e = ms[k + 1] if k + 1 < len(ms) else len(blob)
        try:
            out.append(fn(blob[s:e]))
        except (struct.error, IndexError, ValueError):
            continue
    return out


def skill_rows(blob):
    out = []
    for d in _parsed(blob, b"SKILL_DATA", skill_record):
        out.append((d["id"], d["family"], d["rank"], d["name"], d["effect"],
                    "0x%08X" % d["state"], "0x%04X" % d["weapon"], d["pow"],
                    d["crystal"], "0x%04X" % d["sel12e"],
                    "0x%08X" % d["sel1d8"],
                    # +0xF0 the SP cost of learning this rank (2026-09-11: the
                    # skill window greys GET!/LVUP when it exceeds [unit+0x498];
                    # the server's learn path charges it)
                    d["xf0"]))
    return out


def item_rows(blob):
    out = []
    for d in _parsed(blob, b"FE_ITEM_DATA", item_record):
        sk = [x for x in d["skills"] if x != 0xFFFF]
        out.append((d["id"], d["name"], d["slot"], d["x82"], d["x84"],
                    d["x86"], sk[0] if sk else 65535,
                    sk[1] if len(sk) > 1 else 65535,
                    "0x%04X" % d["category"], d["model_m"], d["model_f"],
                    # +0x90 gold price, +0x94 Ring price (2026-09-11: inferred
                    # from the data -- beginner axe 30, bread 8; +0x94 is 1..20
                    # on exactly the "+1"/named gear = SE's Ring Shop). The
                    # server's shops read these two columns.
                    d["x90"], d["x94"]))
    return out



# --------------------------------------------------------------------------
# 2026-09-06: five more tables, found by scanning dat.jp.dec for length-
# prefixed class tags instead of reading the five we already knew. The scan
# is three lines (see `tag_census` below) and it should have been run on day
# one -- `fet_area` alone is the continent map, names and adjacency included,
# and feworld has been hand-authoring --field-names and --field-coords beside
# a shipped table that has both.
#
# Every column marked `?` is read off the bytes. The ones NOT marked were
# cross-checked against something outside the file and the check is named.

#: fet_npc_type, IN THE CLIENT'S READ ORDER (parser 0x051A8878, sizeof 0xD4,
#: recovered from the parser's disassembly 2026-09-09). This RETIRES the
#: fixed-offset NPC_TAIL table that used to live here.
#:
#: WHY REPLACE A READER THAT WORKED. The tail contains TWO COUNTED ARRAYS
#: (the skill list at +0xA4, a byte array at +0xB4), so in principle every
#: field after them sits at a per-row offset -- and the old table used FIXED
#: ones. WARNING: IT WAS NOT WRONG. Compared against this parse in memory, with no
#: TSV between them, it agreed on 1,371 of 1,371 interior rows for hp, the
#: attack pair, the reward, the level and the radius: every row in this file
#: happens to carry four skills and a four-byte trailing array, so the fixed
#: offsets landed. They were right by luck; this is right by construction,
#: and it is what names ATTACK and DEFENCE. WARNING: The 40 rows that DID read
#: blank were the TSV-writing bug in dump() below, and blaming the offsets
#: for them was a wrong diagnosis -- caught the same hour by running the two
#: readers against each other instead of against the file they both write.
#:
#: THE CHECK IS A MEASUREMENT, not a self-consistency argument. Row 1849 is
#: `Duke_Orc, hp 2464, exp 823, level 53` -- the exact three numbers the
#: 2026-09-09 06:09Z first kill put in the feworld log and in fe.db. And
#: 1,371 of 1,372 records consume exactly their own length.
#:
#:     +0x00 u32 type id          +0x04 u32 modeltype
#:     +0x10 str name             +0x40 u32 HP     +0x44 u32 HP (2nd)
#:     +0x48..+0x4E u16 x4        four 100s on every row seen
#:     +0x50 u16 ATTACK           +0x52 u16 DEFENCE
#:     +0x84 u32 EXP reward       +0xA4 u32[] skill ids
#:     +0xBA u16 LEVEL            +0xBC f32 100.0 / +0xC0 f32 80.0 (radii)
#:
#: ATTACK/DEFENCE are the pair that used to be `p1?`/`p2?`. They are named
#: now because they behave like it across the whole bestiary: both rise
#: monotonically with level (92 at L1 -> 816 at L70), they are EQUAL for most
#: monsters and split for the variants (Venomous L3 comes as 109/109 and as
#: 65/163, an "armoured" version of the same monster at the same level), and
#: nothing else in the record scales that way. PARTIAL: Still a reading: no screen
#: has shown a number computed from either.
def npc_record(rec):
    """One fet_npc_type row, streamed the way the client streams it."""
    r = _Rec(rec)
    d = {"type_id": r.u(4), "modeltype": r.u(4), "x08": r.u(2), "x0a": r.u(2),
         "script": r.u(4)}
    # WARNING: +0x08/+0x0A are ONE u32 in the old reader's `<IIIII` walk (its `b`,
    # the flags column) and +0x0C is its `c` -- the SCRIPT id the shop chain
    # keys off. Keeping both names pointing at the same bytes is why
    # fegamedata.npc_types() still resolves Warrior_Weapon_Shop -> 2102.
    d["flags"] = d["x08"] | (d["x0a"] << 16)
    d["name"] = r.s()
    d["x31"], d["x32"], d["x34"], d["x38"] = r.u(1), r.u(1), r.u(4), r.u(2)
    for o in (0x3a, 0x3b, 0x3c, 0x3d):
        d["x%x" % o] = r.u(1)
    d["x3e"] = r.u(2)
    d["hp"], d["hp2"] = r.u(4), r.u(4)
    d["r48"] = [r.u(2) for _ in range(4)]
    d["attack"], d["defence"], d["x54"] = r.u(2), r.u(2), r.u(2)
    d["x56"], d["x58"] = r.u(2), r.u(2)
    d["x5c"] = r.u(4)
    d["x60"], d["x62"], d["x64"] = r.u(2), r.u(2), r.u(2)
    d["m68"] = [f(r.u(4)) for _ in range(7)]
    d["exp"] = r.u(4)
    d["u88"] = [r.u(2) for _ in range(10)]
    d["b9c"] = [r.u(1) for _ in range(5)]
    d["skills"] = r.arr(4)
    d["ab4"] = r.arr(1)
    d["xb8"], d["level"] = r.u(2), r.u(2)
    d["fbc"] = [f(r.u(4)) for _ in range(5)]
    d["xd0"], d["xd2"] = r.u(2), r.u(2)
    d["_used"], d["_len"] = r.o, len(rec)
    return d


def area_rows(blob):
    """fet_area -- THE CONTINENT MAP: 95 areas, name, map pixel, adjacency.

    LAYOUT READ OFF THE CLIENT'S OWN PARSER (0x0519b308, sizeof 0x4c0),
    2026-09-09. The previous version of this function read it off the BYTES
    and got two things wrong -- see the retraction below.

        +0x000 u32 x10   id, map id, extents(3 x f32 = 640/8/640),
                         80, 100|500, NATION, 0, 100|0
        +0x028 u32 namelen, then that many cp932 bytes (buffer is 0x400)
        +0x428 u16 ISLAND      1..6, see below
        +0x42c u32 FLAGS       0 war field / 15 capital / 79 capital inner half
        +0x430 u16 (always 0)
        +0x432 u16 HMAP        Data\\Hmap\\mapNN.pak, 1..14 (101..105 capitals)
        +0x434 u16 MAP X       pixel on the continent map
        +0x436 u16 MAP Y
        +0x438 u32 x33         NEIGHBOUR area ids, slot 0 unused, zero-padded
        +0x4bc u16             1..5 on war fields, 0 on all ten capitals; ?

    WARNING: RETRACTS the byte-derived reading. It started the neighbour array at
    +0x43c instead of +0x438 -- harmless only because slot 0 is zero in all 95
    rows -- and it never reported ISLAND or FLAGS at all. It also claimed area
    5 has seven neighbours; it has NINE.

    ISLAND is the continent, and it is the structure of the whole game:
    1 = the shared frontier continent (areas 1..15, three fields per nation),
    then 2..6 = one home continent per nation, areas 16..30, 31..45, 46..60,
    61..75, 76..90, each with its capital and that capital's inner half (91..95).

    Checks: all 95 records consume exactly their own length; every neighbour
    value is in 1..95; the 154 edges are reciprocal except the five inner
    capital halves, which are reached only through fet_area_portal doors; and
    the 90-node war graph is connected. HMAP is confirmed against the install
    tree, which ships exactly map01..map14.pak + MapSpot01..14.cfg.
    """
    out = []
    for r in records(blob, b"fet_area", tail=256):
        try:
            aid, mapid = struct.unpack_from("<II", r, 0)
            ex, ey, ez = struct.unpack_from("<fff", r, 8)
            u = struct.unpack_from("<IIIII", r, 20)
            nl = struct.unpack_from("<I", r, 40)[0]
            name = r[44:44 + nl].decode("cp932", "replace")
            t = r[44 + nl:]
            island = struct.unpack_from("<H", t, 0)[0]
            flags = struct.unpack_from("<I", t, 2)[0]
            hmap, mx, my = struct.unpack_from("<HHH", t, 8)
            adj = [v for v in struct.unpack_from("<33I", t, 14) if v]
            tail = struct.unpack_from("<H", t, 146)[0]
        except (struct.error, IndexError):
            continue
        out.append((aid, mapid, "%g" % ex, "%g" % ey, "%g" % ez, u[0], u[1],
                    u[2], u[4], name, island, flags, hmap, mx, my, tail,
                    " ".join(str(v) for v in adj)))
    return out


def area_portal_rows(blob):
    """fet_area_portal -- 64 portals: WHERE a door is and WHERE it puts you.

    LAYOUT READ OFF THE CLIENT'S OWN PARSER (0x0519D85A, found in the
    disassembly), not off the bytes -- which is why the earlier reading of
    this table was wrong in a way the data could not show:

        +0x00 u32   portal id
        +0x04 u32   AREA the portal is in
        +0x08 f32[] GATE      (count 2)  -- x, z  of the door itself
        +0x10 f32   RADIUS               -- 3.0 or 3.5, the door's volume
        +0x14 f32[] SPAWN     (count 2)  -- x, z  you arrive at
        +0x1C f32[] FACING    (count 2)  -- x, z  unit-ish vector
        +0x24 u32   DESTINATION PORTAL id

    WARNING: RETRACTS the 2026-09-06 reading, which took +0x08 (an ARRAY COUNT of 2)
    for a type tag and then read THREE floats from +0x0C as (x, y, z). Those
    three were really (gate x, gate z, radius) -- so the "height of -51.7 with
    a z of 3.0 is the wrong way round" note was right that the frame was
    wrong, and wrong about what the third float is. THERE IS NO THIRD
    COORDINATE IN THIS TABLE AT ALL: every position is a 2-float (x, z) pair
    and the height comes off the heightmap, exactly as feworld's own --door
    already assumed.

    THE CHECK, and it is a real one: all 64 records consume exactly their own
    52 bytes; every one of the 64 destinations is reciprocal (dest's dest is
    me); the areas are exactly the ten capital halves 21/39/57/62/78 and
    91..95, and their per-area counts MIRROR (21 and 91 have 7 each, 39/92
    have 4, 57/93 have 7, 62/94 have 12, 78/95 have 2). A wrong frame does not
    produce a graph that closes 64 times out of 64.
    """
    out = []
    for r in records(blob, b"fet_area_portal", tail=52):
        if len(r) < 52:
            continue
        R = _Rec(r)
        try:
            pid, area = R.u(4), R.u(4)
            gate = [f(v) for v in R.arr(4)]
            radius = f(R.u(4))
            spawn = [f(v) for v in R.arr(4)]
            face = [f(v) for v in R.arr(4)]
            dest = R.u(4)
        except (struct.error, IndexError):
            continue
        if len(gate) != 2 or len(spawn) != 2 or len(face) != 2:
            continue
        out.append((pid, area, "%g" % gate[0], "%g" % gate[1], "%g" % radius,
                    "%g" % spawn[0], "%g" % spawn[1],
                    "%g" % face[0], "%g" % face[1], dest))
    return out


def skill_learn_rows(blob):
    """SKILL_LEARN_DATA -- 56 rows, layout off the parser at 0x051B3A18:

        [u16 a][u16 skill][u8 c][u8 d][u16 prereq]

    Every `prereq` that is not 0xFFFF is a REAL SKILL_DATA id (checked: all 27
    distinct values resolve), and rows come in the pattern (c=1,d=1,p),
    (c=2,d=1,p+3), (c=2,d=2,p+5) for the same skill.

    WARNING: IT ONLY COVERS SKILLS 0..38 of 734. Like PARAMETER_CLIENT (which turned
    out to be a TYPE-DECLARATION demo: rows literally named INT8/UINT8/FLOAT
    plus five keep-building window strings), this is a partial/sample table,
    not the shipped skill tree. Dumped so nobody re-derives that.
    """
    out = []
    for r in records(blob, b"SKILL_LEARN_DATA", tail=8):
        if len(r) < 8:
            continue
        R = _Rec(r)
        out.append((R.u(2), R.u(2), R.u(1), R.u(1), R.u(2)))
    return out


def class_param_rows(blob):
    """FE_CLASS_BASIC_PARAM_DATA -- 7 classes, IN THE CLIENT'S READ ORDER.

    Layout off the parser at 0x05195198:

        +0x00 u16    class id
        +0x02 str    name (0x20)
        +0x22 u16 x12   base block
        +0x3C f32 x5    five multipliers, ALL 1.0 in this build
        +0x50 i16 x16   a per-class modifier vector -- Warrior +10 in slot 12
                        and -10 in slot 11, Scout +10 in 10 / -10 in 12,
                        Sorcerer -10 in 10 / +10 in 11, and classes 3..6 all
                        zero (the three launch classes are the only ones
                        filled in, which is itself the check)
        +0x70 u16[]  proficiency + STARTING SKILL ids, 0xFFFF-padded
        +0x90 u8[]   trailing flag bytes

    WARNING: RETRACTS the previous reading of this table, which walked the bytes
    looking for anything in 400..470 and emitted a raw hex head. That head was
    IDENTICAL for all seven classes -- the visible symptom of a parse that had
    never found the record's real start -- and the per-class numbers below
    were invisible.

    The +0x70 array is not proficiencies alone: Sorcerer's is 443, 445, 270,
    275, and 270/275 are the two SKILLS the live character already owns. So it
    is "what this class starts knowing", which is what --skill-grant wants.
    """
    out = []
    for r in records(blob, b"FE_CLASS_BASIC_PARAM_DATA", tail=600):
        R = _Rec(r)
        try:
            cid = R.u(2)
            name = R.s()
            base = [R.u(2) for _ in range(12)]
            mult = [f(R.u(4)) for _ in range(5)]
            vec = [R.u(2) for _ in range(16)]
            ids = R.arr(2)
            flags = R.bits()
        except (struct.error, IndexError):
            continue
        if len(name) > 64:
            continue
        sgn = lambda v: v - 0x10000 if v > 0x8000 else v
        out.append((cid, name,
                    " ".join(str(sgn(v)) for v in base),
                    " ".join("%g" % v for v in mult),
                    " ".join(str(sgn(v)) for v in vec),
                    " ".join(str(v) for v in ids if v != 0xFFFF),
                    "0x%x" % flags))
    return out


def effect_record(rec):
    """EFFECT_DATA in the client loader's read order (0x051880F8, sizeof 0x84).

    2026-09-12. The first reader of this table (a scratch script) took +0x44
    for one u32 and parsed 94 of 386 records; it is a COUNTED array (`read 4`
    at 0x0518837E, then a loop filling [esi+0x44]). Read this way, 385/386
    records consume exactly their own bytes (the last has no successor).

      bitsA  +0x34  the STAT a restore touches: 0x4 = HP, 0x10 = Pw -- the
                    same bit values as the 0x2028 regen ask's mask. Food and
                    the regen potions are 0x4, the power pots 0x10.
      bitsC  +0x38  bit 0x20 (with bitsA/bitsB clear) = a DAMAGE effect, and
                    then vals[0] is the skill's POWER in percent: Sonic Boom
                    L1..L5 = 70/80/90/100/130. SKILL_DATA +0x108 lists the
                    effect ids a skill applies (0x0505F79C -> 0x0506E900).
      vals   +0x44  f32 array: the amount (bread 50 HP, a regen potion 48
                    per tick, a power pot 10 Pw per tick, a power %).
      x24    total duration ms (0 = instant), x30 tick ms, x69 potion TIER.
    The amounts are the server's to apply: the client's only reader of +0x44
    (0x0505F790) sums it into a display stat vector."""
    r = _Rec(rec)
    d = {"id": r.u(2), "name": r.s()}
    for o in (0x24, 0x28, 0x2c, 0x30):
        d["x%x" % o] = r.u(4)
    d["bitsA"], d["bitsB"], d["bitsC"], d["bitsD"] = (r.bits(), r.bits(),
                                                     r.bits(), r.bits())
    d["vals"] = [f(v) for v in r.arr(4)]
    d["bitsE"], d["bitsF"] = r.bits(), r.bits()
    d["x5c"], d["x60"], d["x64"] = r.u(4), r.u(4), r.u(4)
    d["bitsG"] = r.bits()
    d["x69"] = r.u(1)
    d["bitsH"] = r.bits()
    d["x6c"], d["x70"] = r.u(4), r.u(2)
    d["s72"] = r.s()
    d["x82"] = r.u(1)
    return d


def effect_rows(blob):
    out = []
    for d in _parsed(blob, b"EFFECT_DATA", effect_record):
        out.append((d["id"], d["name"], d["x24"], d["x28"], d["x2c"], d["x30"],
                    "0x%X" % d["bitsA"], "0x%X" % d["bitsB"],
                    "0x%X" % d["bitsC"], "0x%X" % d["bitsD"],
                    " ".join("%g" % v for v in d["vals"]), d["x69"]))
    return out


def skill_combat_rows(blob):
    """SKILL_DATA's combat fields, each named by a CLIENT reader (2026-09-12):
    kind +0x02 (switch 0x0504ABC0), range f32 +0xF8 and min range f32 +0x1D0
    (the building auto-attack's distance test 0x05070D58/0x05070DAB), radius
    f32 +0x134 (target display 0x050DF11C), wind-up +0xF6 / active +0x13C /
    impact +0x140 / recovery +0x144 ms (0x0505F4C0..0x0505F520; the client's
    own busy-until is their sum, 0x0505F500), hit stun +0x102 ms
    (0x0507F0CF), child skill +0x104, effect ids +0x108."""
    out = []
    for d in _parsed(blob, b"SKILL_DATA", skill_record):
        out.append((d["id"], d["x02"], "%g" % f(d["xf8"]),
                    "%g" % f(d.get("x1d0", 0)), "%g" % f(d["x134"]),
                    d["xf6"], d["x13c"], d["x140"], d["x144"], d["x102"],
                    d["x104"],
                    " ".join(str(v) for v in d["a108"] if v != 0xFFFF)))
    return out


def item_use_rows(blob):
    """FE_ITEM_DATA fields the server needs and items.tsv never carried:
    +0x56 the USE skill (0x05075330: -1 = not usable; a use is a cast of it,
    0x050753DA), +0x38 physical ATTACK (non-zero only on weapons, rising with
    the required level: beginner axe 56) and +0x3A magic ATTACK (wands only:
    beginner wand 45). The attack pair is read by the loader
    (0x051977AD/0x051977BA); no arithmetic client reader was found."""
    out = []
    for d in _parsed(blob, b"FE_ITEM_DATA", item_record):
        out.append((d["id"], d["x56"], d["x38"], d["x3a"]))
    return out


def lv_diff_rows(blob):
    """fet_npc_lv_diff_correction -- 17 rows of
    `[i16 lo][i16 hi][f32 a][f32 b][f32 c][f32 d]`: the level-difference
    multipliers. Row 0 is (-1, -1) i.e. the catch-all, and the four floats
    fall away as the difference grows (1.4 / 0.8 / 0.4 / 0.7 at the top),
    which is the shape of an EXP/damage penalty band. Which float is which
    reward is unread -- there is no consumer traced yet.
    """
    out = []
    for r in records(blob, b"fet_npc_lv_diff_correction", tail=20):
        if len(r) < 20:
            continue
        lo, hi = struct.unpack_from("<hh", r, 0)
        a, b, c, d = struct.unpack_from("<ffff", r, 4)
        out.append((lo, hi, "%g" % a, "%g" % b, "%g" % c, "%g" % d))
    return out


def fame_rank_rows(blob):
    """fet_fame_rank -- FE's RANK LADDER, keyed by total score.

    Layout off the parser at 0x051A308A: `[u32 id][u16 rank][u32 threshold]
    [str name]`. All 11 records consume exactly their own length.

    VERIFIED: THE NAMES ARE ALREADY ENGLISH IN THE JAPANESE BUILD -- Beginner,
    Apprentice, Followership, Chaser, ChiefChaser, Attacker, ChiefAttacker,
    Guardian, ChiefGuardian, GrandGuardian, Commander -- so unlike the 95 area
    names there is nothing to translate and nothing to invent.

    The thresholds are the score at which each rank is reached: 0, 10k, 50k,
    110k, 225k, 495k, 1.195M, 2.885M, 4M, 6M, 12M. They rise monotonically,
    which is the check that the u32 is the threshold and not an id.
    """
    out = []
    for r in records(blob, b"fet_fame_rank", tail=1100):
        R = _Rec(r)
        try:
            rid, rank, thresh = R.u(4), R.u(2), R.u(4)
            name = R.s()
        except (struct.error, IndexError):
            continue
        if not name or len(name) > 64:
            continue
        out.append((rid, rank, thresh, name))
    return out


def area_client_rows(blob):
    """fet_area_client_data -- 95 rows of `[u32 area][u16 kind]`, parser
    0x0519C70A, sizeof 8. One row per area, and NO STRINGS AT ALL.

    WARNING: That matters twice over. The dat.pak string harvest reports 88 strings
    for this member and every one of them is a FALSE POSITIVE -- the harvester
    scans for `[u32 len][bytes]` and misfires on a table of six-byte records.
    Any "untranslated string" count that includes this member is inflated.

    WHAT THE KIND IS, as far as it is checked:
      5 = exactly the ten capital halves 21/39/57/62/78/91..95 (a THIRD table
          agreeing on that set, after fet_area's marker and fet_area_portal's
          areas)
      8 = area 5 alone, 始まりの大地, the starting land
      6 = 14 fields whose monsters average level 51.9 (45..60)
      3 / 4 / 7 = 20 / 25 / 25 fields averaging 18.6 / 17.8 / 20.1

    So it separates the level-50 fields from the level-20 fields and names the
    capitals and the starting land. PARTIAL: Calling it a "difficulty tier" is a
    reading; what is measured is the level split above.
    """
    out = []
    for r in records(blob, b"fet_area_client_data", tail=40):
        if len(r) < 6:
            continue
        R = _Rec(r)
        aid = R.u(4)          # key first -- `d[R.u(4)] = R.u(2)` evaluates the
        kind = R.u(2)         # VALUE first in CPython and scrambles both
        out.append((aid, kind))
    return out


def metamorphosis_rows(blob):
    """METAMORPHOSIS_DATA -- the six forms a character can transform into,
    parser 0x051A9D28, sizeof 0x22C. All six consume exactly their own length.

    VERIFIED: NAMED IN ENGLISH in the Japanese build: GIANT, DRAGON, CHIMAIRA,
    KNIGHT, WRAITH, FLYNGSHIP, each with its model path.

    This is what [unit+0x3b4] (feworld's `f28`) selects, and the same value
    picks the MOVEMENT ROW (movement is a per-form multiplier). So the rows
    previously listed only as "metamorphosis forms 1..5" have names now.
    """
    out = []
    for r in records(blob, b"METAMORPHOSIS_DATA", tail=700):
        R = _Rec(r)
        try:
            i = R.u(2)
            name = R.s()
            fl = [f(R.u(4)) for _ in range(4)]
            model = R.s()
            x114 = R.u(4)
            settings = R.s()
            form = R.u(1)
        except (struct.error, IndexError):
            continue
        if not name:
            continue
        out.append((i, name, form, model, x114,
                    " ".join("%g" % v for v in fl), settings))
    return out


def building_portal_rows(blob):
    """FE_BUILDING_PORTAL_DATA -- a building's doorway, parser 0x05192E5A,
    sizeof 0x30, 108 records of which 28 carry real geometry:

        [u16 id][f32[3] IN][f32 radius][f32[3] OUT][f32[3] facing][u8]

    WARNING: THE FIRST FOURTEEN ROWS ARE ALL ZERO, so a look at the head of this
    table says "it ships nothing" -- which is what the town NPC positions and
    the door destinations really do. It is wrong here: 28 rows further down
    have positions.

    The rows are RECIPROCAL PAIRS, the way fet_area_portal's are: id 56 goes
    IN (-1.40, 0.50, 2.60) -> OUT (-3.00, 0, 6.00) facing (0,0,-1), and id 58
    goes back the other way with the two swapped and facing (0,0,+1). Every
    non-zero id is EVEN and pairs with the next even id.

    WARNING: The coordinates are BUILDING-LOCAL (all within ~17 units of the origin),
    not world positions, so using them needs the building's own placement.
    WARNING: AND IT MAY NOT BE THE SHOP EXIT: inside the shop the client sent NOTHING
    at its door (2026-09-05), so this table is plausibly consumed CLIENT-SIDE
    for placement and the way out of a shop is still a UI action. Opened here,
    not wired to anything.
    """
    out = []
    for r in records(blob, b"FE_BUILDING_PORTAL_DATA", tail=80):
        R = _Rec(r)
        try:
            i = R.u(2)
            a = [f(v) for v in R.arr(4)]
            rad = f(R.u(4))
            b = [f(v) for v in R.arr(4)]
            c = [f(v) for v in R.arr(4)]
            z = R.u(1)
        except (struct.error, IndexError):
            continue
        if len(a) != 3 or len(b) != 3 or len(c) != 3:
            continue
        out.append((i, "%g" % rad, z,
                    " ".join("%g" % v for v in a),
                    " ".join("%g" % v for v in b),
                    " ".join("%g" % v for v in c)))
    return out


def tag_census(blob, least=4):
    """Every length-prefixed class tag in the archive, with its record count.

    This is the three lines that found the five tables above. Run it before
    concluding a table does not ship.
    """
    seen = collections.Counter()
    for m in re.finditer(rb"[A-Za-z_][A-Za-z0-9_]{3,48}", blob):
        i = m.start()
        if i >= 4 and struct.unpack_from("<I", blob, i - 4)[0] == len(m.group()):
            seen[m.group().decode("latin1")] += 1
    return [(t, n) for t, n in seen.most_common() if n >= least]


#: Characters that must never reach a TSV cell.
#:
#: 2026-09-09, A REAL BUG THAT HAD BEEN COSTING MONSTERS: several columns
#: carry raw bytes out of dat.pak (fet_npc_type's `model_file` is a slice of
#: NPC_ModelType, complete with its length prefixes), and 40 of those slices
#: contain a 0x0A. Written straight out, each of those rows became TWO short
#: lines, so every column after `model_file` -- hp, level, the reward -- read
#: back EMPTY. fegamedata then fell back to 100 HP and 0 EXP, which is why
#: the whole Harpy line, Bone_Hunter and 38 others were worth nothing to
#: kill. A dump is not a parse: this one silently lost a column boundary.
_TSV_BAD = ("\t", "\n", "\r", "\0")


def _cell(x):
    s = str(x)
    for c in _TSV_BAD:
        s = s.replace(c, " " if c != "\0" else "")
    return "".join(c if c >= " " else "." for c in s)


def dump(name, header, rows):
    p = os.path.join(OUTDIR, "fe-fet-%s.tsv" % name)
    n_clean = 0
    with io.open(p, "w", encoding="utf-8", newline="") as fh:
        fh.write("\t".join(header) + "\n")
        for r in rows:
            cells = [_cell(x) for x in r]
            if len(cells) != len(header):
                raise SystemExit("%s: row has %d cells, header has %d"
                                 % (name, len(cells), len(header)))
            if any(c != str(x) for c, x in zip(cells, r)):
                n_clean += 1
            fh.write("\t".join(cells) + "\n")
    print("%-40s %5d rows%s"
          % (p, len(rows),
             "  (%d rows had control bytes stripped)" % n_clean
             if n_clean else ""))


def main():
    import argparse
    global OUTDIR
    ap = argparse.ArgumentParser(
        description="dump Fantasy Earth's fet_* tables out of a decrypted "
                    "dat.pak to TSV")
    ap.add_argument("--dat", default=DEFAULT_DAT,
                    help="the DECRYPTED dat.pak from your client install "
                         "(default: dat.jp.dec beside this script)")
    ap.add_argument("--out", default=DEFAULT_OUT,
                    help="directory the fe-fet-*.tsv files are written to "
                         "(default: services/fedata/)")
    ap.add_argument("--census", action="store_true",
                    help="also print every length-prefixed class tag in the "
                         "archive with its record count")
    args = ap.parse_args()
    OUTDIR = args.out
    os.makedirs(OUTDIR, exist_ok=True)
    blob = open(args.dat, "rb").read()

    # modeltype id -> model file, for the type table
    mt = {}
    for r in records(blob, b"NPC_ModelType"):
        try:
            tid = struct.unpack_from("<H", r, 0)[0]
            n = struct.unpack_from("<I", r, 2)[0]
            mt[tid] = r[6:6 + n].decode("latin1")
        except struct.error:
            pass

    types = []
    tname = {}
    short = 0
    for r in records(blob, b"fet_npc_type", tail=400):
        try:
            t = npc_record(r)
        except (struct.error, IndexError):
            continue
        # The LAST record has no successor to bound its slice, so it always
        # measures long; only an interior row that misses is a layout bug.
        if t["_used"] != t["_len"] and t["_len"] < 400:
            short += 1
        tname[t["type_id"]] = t["name"]
        types.append((t["type_id"], t["name"], t["modeltype"],
                      mt.get(t["modeltype"], ""), "0x%X" % t["flags"],
                      t["script"], t["hp"], t["hp2"], t["attack"], t["defence"],
                      t["exp"], t["level"],
                      "%g" % t["fbc"][0], "%g" % t["fbc"][1],
                      " ".join(str(v) for v in t["skills"]),
                      " ".join(str(v) for v in t["r48"])))
    # Columns are APPENDED, never inserted: fegamedata reads these rows by
    # header NAME, so an insert is invisible until something reads wrong.
    dump("npc_type", ("type_id", "name", "modeltype", "model_file", "flags?",
                      "script_id?", "hp", "hp2", "attack", "defence",
                      "exp", "level", "radius_a?", "radius_b?", "skills",
                      "resists?"), types)
    if short:
        print("  !! %d fet_npc_type rows did not consume their own length "
              "-- the layout is wrong for those" % short)

    gens = {}
    grows = []
    for r in records(blob, b"fet_npc_generator_data"):
        u = list(struct.unpack_from("<18I", r, 0))
        ids = [v for v in u[5:10] if v != 0xFFFFFFFF]
        names = [tname.get(v, "?") for v in ids]
        gens[u[0]] = names
        grows.append((u[0], u[1], "0x%X" % u[2], "0x%X" % u[3], u[4], " ".join(str(v) for v in ids),
                      " ".join(names), u[10], " ".join("%g" % f(v) for v in u[11:16]), u[16], r[68:73].hex()))
    dump("npc_generator_data", ("gen_id", "u1", "u2", "u3", "u4", "npc_type_ids", "npc_type_names",
                                "u10", "floats11_15", "u16", "tail_hex"), grows)

    prow = []
    for r in records(blob, b"fet_npc_generator_pos"):
        gid, fld, u2, kind, u4 = struct.unpack_from("<HHHHH", r, 0)
        x, y, z, rad = struct.unpack_from("<ffff", r, 10)
        link = r[26] if len(r) > 26 else -1
        prow.append((gid, fld, kind, "%g" % x, "%g" % y, "%g" % z, "%g" % rad, link, r[26:28].hex()))
    dump("npc_generator_pos", ("pos_id", "field_id", "kind", "x", "y", "z", "radius", "tail_byte0", "tail_hex"), prow)

    crow = []
    for r in records(blob, b"fet_castle_info"):
        v = struct.unpack_from("<IIHHHHHHHhhhhHHHii", r, 0)
        crow.append(v)
    dump("castle_info", ("idx", "map", "grid_x", "grid_z", "flags?", "u5", "u6", "u7", "u8", "s9", "s10", "s11", "s12",
                         "u13", "u14", "u15", "i16", "i17"), crow)

    erow = []
    for r in records(blob, b"fet_initialize_equip_item_data"):
        cls, n1 = struct.unpack_from("<II", r, 0)
        l1 = struct.unpack_from("<%dH" % n1, r, 8)
        n2 = struct.unpack_from("<I", r, 8 + 2 * n1)[0]
        l2 = struct.unpack_from("<%dH" % n2, r, 12 + 2 * n1)
        erow.append((cls, " ".join(str(v) for v in l1 if v != 0xFFFF), " ".join(str(v) for v in l2 if v != 0xFFFF)))
    dump("initialize_equip", ("class", "male_items", "female_items"), erow)

    dump("items", ("item_id", "name_jp", "slot_type", "stat_gate1",
                   "stat_gate2", "req_level", "skill1", "skill2",
                   "category", "model_m", "model_f", "price", "ring_price"),
         item_rows(blob))

    # `nation` and `is_capital` are NOT guesses: rows 21/39/57/62/78 are
    # exactly the five areas the spawn table gives no monsters, they are the
    # only ones whose +0x18 reads 500 instead of 100, their +0x1c runs 1..5
    # once each, and their names are the five nations' capitals (ベインワット,
    # アズルウッド, リベルバーグ, ナッツベリー, ルーンワール). 91..95 repeat
    # them -- the second half of each capital, previously mapped by hand. On
    # a war field +0x1c is that field's OWNING nation, which is what
    # feworld's --field-nations sets by hand today.
    dump("area", ("area_id", "map_id", "ext_x", "ext_y", "ext_z", "u20?",
                  "is_capital_500", "nation", "u36?", "name_jp", "island",
                  "flags", "hmap", "map_x", "map_y", "tail?",
                  "neighbours"), area_rows(blob))
    dump("area_portal", ("portal_id", "area_id", "gate_x", "gate_z", "radius",
                         "spawn_x", "spawn_z", "face_x", "face_z",
                         "dest_portal"),
         area_portal_rows(blob))
    dump("skill_learn", ("a", "skill_id", "c", "d", "prereq_skill"),
         skill_learn_rows(blob))
    dump("skill", ("skill_id", "family", "rank", "name_jp", "effect",
                   "state_mask", "weapon_mask", "pow_cost", "crystal_cost",
                   "sel_12e", "sel_1d8", "sp_cost"), skill_rows(blob))
    dump("class_param", ("class_id", "name", "base_u16", "mult_f32",
                         "mod_vector", "proficiency_skills", "flag_bits"),
         class_param_rows(blob))
    dump("lv_diff", ("lo", "hi", "f0?", "f1?", "f2?", "f3?"), lv_diff_rows(blob))
    dump("fame_rank", ("id", "rank", "score_threshold", "name"),
         fame_rank_rows(blob))
    dump("area_client_data", ("area_id", "kind"), area_client_rows(blob))
    dump("metamorphosis", ("id", "name", "form", "model", "x114", "floats",
                           "settings"), metamorphosis_rows(blob))
    dump("building_portal", ("id", "radius", "flag", "in_xyz", "out_xyz",
                             "facing"), building_portal_rows(blob))

    fields = collections.Counter(p[1] for p in prow)
    print("spawn points per field:", dict(sorted(fields.items())))
    print("fields with NONE (capitals?):", [i for i in range(1, 96) if i not in fields])
    if args.census:
        print("class tags in dat.pak (>= 4 records):")
        for t, n in tag_census(blob):
            print("   %6d  %s" % (n, t))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    main()
