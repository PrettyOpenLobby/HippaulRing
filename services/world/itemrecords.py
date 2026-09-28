"""Item rows: the bag and equip records and their fields."""
import struct
from . import character

# The item object's placement fields, measured in group 4's reader
# (0x5075b80). WARNING: LIVE 2026-09-05 #13: sending them as 0 put EVERY item at
# slot 0 with no equip marker, and the inventory drew nothing.
IT_COUNT = 0x00000002      # u16 -> +0x4a8  stack count
# WARNING: THE THIRD "Skill conditions not met." (2026-09-08, run 10, build 173 with
# gate 4 measured PASS on every operand). The ladder has a rung AFTER gate 4:
# 0x0507f7d7 -> 0x0504b8c0(held weapon uid) looks the item up and passes only
# if (i16)[item+0x4ac] > 0 or (i16)[item+0x4aa] == -1. Group 4's own applier
# names them: bit 0x4 -> +0x4aa (u16) is the durability MAXIMUM, bit 0x8 ->
# +0x4ac is the CURRENT value -- with max == -1 (infinite) every check is
# skipped (0x05075c07), otherwise current is compared against max/5 and the
# client prints its own "%s is about to break" line (0x52dea50). We had
# served neither, so every weapon was a broken weapon: current 0, max 0.
IT_DURMAX = 0x00000004     # i16 -> +0x4aa  durability maximum, -1 = infinite
IT_DURCUR = 0x00000008     # i16 -> +0x4ac  durability current; the cast rung
#                            0x0504b8c0 wants this > 0 unless max is -1
# The pair every item record carries. (-1, 100) is CHOSEN: infinite, so the
# client's wear checks never fire; --item-durability MAX:CUR overrides.
ITEM_DURABILITY = (-1, 100)
IT_KIND = 0x08000000       # u16 -> +0x4c6  0 = the bag array (unit+0x570),
#                            1 = the second array (+0x6f0); the unit's insert
#                            0x507b330 REFUSES anything else
IT_EQUIP = 0x10000000      # u8  -> +0x4c8  0xff = not worn; any other value
#                            makes the bag reader's tail (0x503d306) call
#                            unit->Equip(uid, -1, 1), so the client picks the
#                            slot itself from the item table
IT_SLOT = 0x20000000       # u32 -> +0x4cc  THE BAG SLOT: the unit's insert
#                            stores the uid at [unit + slot*4 + 0x570], and
#                            the inventory widget reads that array back
#                            (0x507ba90 from 0x50b65cf)


def item_rows(args_or_items):
    """The stored bag as (uid, item number, flag, COUNT), whatever shape it is in.

    The store has always held three-element rows -- `[uid, item, flag]`, where
    the flag is the u8 felobby puts in the 0xD002 record and nothing here has
    ever read. Stacking needs a fourth number (the count that lands in
    `[item+0x4a8]`), and a stacked bag has to survive a relogin, so the row grew
    an OPTIONAL fourth element. Old rosters have none and read back as count 1,
    which is what every row was before this existed -- so no migration, and a
    store written by this build is still readable by the previous one.

    Accepts either the args (loads the session's character) or an already-loaded
    list, because half the callers have one and half the other.
    """
    items = args_or_items
    if not isinstance(items, (list, tuple)):
        items = character._load_char_field(items, "items", []) or []
    out = []
    for x in items:
        x = tuple(x)
        if len(x) < 2:
            continue
        out.append((int(x[0]), int(x[1]),
                    int(x[2]) if len(x) > 2 else 1,
                    max(1, int(x[3])) if len(x) > 3 else 1))
    return out


def item_fields(no, count=1, slot=0, equip=None, kind=0):
    """The item object's four masked field groups, the way 0x503d280 (bag),
    0x503c470 (worn gear, group 1 only) and the shop's page reader consume
    them: [u32 mask1][fields][u32 mask2][fields][u32 mask3][fields][u32 mask4]
    [fields], each group's fields in ASCENDING bit order. mask1 bit1 = u16
    item number -> obj+0x3a2, the table key the row/model lookups need
    (0x5198150; 0xffff there CRASHED the shop list). Group 4 carries the
    stack count and the placement fields above. `equip` None = not worn."""
    dmax, dcur = ITEM_DURABILITY
    return (struct.pack(">IH", 2, no & 0xFFFF)          # group 1: item number
            + struct.pack(">I", 0)                      # group 2: nothing
            + struct.pack(">I", 0)                      # group 3: nothing
            + struct.pack(">I", IT_COUNT | IT_DURMAX | IT_DURCUR
                          | IT_KIND | IT_EQUIP | IT_SLOT)
            + struct.pack(">H", count & 0xFFFF)         # +0x4a8 stack count
            + struct.pack(">h", int(dmax))              # +0x4aa durability max
            + struct.pack(">h", int(dcur))              # +0x4ac durability current
            + struct.pack(">H", kind & 0xFFFF)          # +0x4c6 which array
            + struct.pack(">B", 0xFF if equip is None else (equip & 0xFF))
            + struct.pack(">I", slot & 0xFFFFFFFF))     # +0x4cc bag slot


def bag_record(items, equip=None, kind="both"):
    """The 0x1000 bag sub-record (reader 0x0503d330, only ever called from the
    enter-area handler): [u8 pages][u8 ADD=1][u8 announce], then per page
    [u32 page][u32 slot mask] and, per set bit, [u32 uid] + item_fields.
    Slot = page*32 + bit (0x503d3e2). The header bytes land at [esp+0x14],
    [esp+0x34], [esp+0xb] before the two pushes; the per-bit test at
    0x503d3de reads [esp+0x3c] = the SECOND byte (add), the notice at
    0x503d46e reads [esp+0x23] = the THIRD (announce -> 0x511f500).
    WARNING: LIVE 2026-09-05 #11/#12: with them swapped the client took the REMOVE
    path (0x503d4b6), which reads no uid and no fields, so every item byte
    stayed in the stream and the worn-gear reader took them as its mask --
    the RVA 0xAC647 crash both times, worn mask 0 or not. `items` are the
    lobby's (uid, item, flag) tuples; slot = position in the list, 32 per
    page. `equip` maps uid -> the slot it is worn in, which sets the item's
    equip marker so the reader's tail dresses the character (no worn-gear
    sub-record needed -- that one crashed live #11)."""
    items = item_rows(items)[:96]                       # the client's array is 96 deep
    worn = dict(equip or {})
    # WARNING: LIVE 2026-09-05 #15: the unit has TWO 96-slot arrays -- +0x570
    # (kind 0) and +0x6f0 (kind 1), plus the 13-slot worn array at +0x870 --
    # and the inventory widget reads whichever its own +0x9c says (0x50b8632,
    # from a ctor argument at 0x50408d2), not a constant. Kind 0 alone drew
    # nothing ("0/0"), so `both` sends every item into BOTH arrays under
    # separate uids: whichever the window reads, it lists them.
    rows = [(uid, no, 0, ct) for uid, no, _f, ct in items]
    if kind == "1":
        rows = [(uid, no, 1, ct) for uid, no, _f, ct in items]
    elif kind == "both":
        alt_base = max([int(u) for u, _n, _f, _c in items] + [0]) + 100000
        rows = rows + [(alt_base + n, no, 1, ct)
                       for n, (_u, no, _f, ct) in enumerate(items)]
    pages = (len(rows) + 31) // 32
    # announce=1 as a PROBE (live #13: no crash, no items): the client prints
    # a got-item notice (0x511f500) per entry it actually processes, which
    # is the only visible sign the reader walked the record.
    body = struct.pack(">BBB", pages, 1, 1)
    for pg in range(pages):
        chunk = rows[pg * 32:(pg + 1) * 32]
        mask = (1 << len(chunk)) - 1
        body += struct.pack(">II", pg, mask)
        for i, (uid, no, arr, ct) in enumerate(chunk):
            body += struct.pack(">I", uid & 0xFFFFFFFF) + item_fields(
                no, ct, (pg * 32 + i) % 96, worn.get(int(uid)), arr)
    return body


def equip_record(equip, items):
    """The 0x1000 worn-gear sub-record (reader 0x0503c470): [u32 slot mask],
    then per set bit [u32 uid][u32 mask1][group-1 fields]. uid 0 clears the
    slot; a uid the item manager (0xbb8) does not know is CREATED from the
    fields, so group 1 must carry the item number. `equip` are the lobby's
    (slot, uid) pairs; the item number comes from the matching bag entry."""
    by_uid = {int(u): int(no) for u, no, _f, _c in item_rows(items)}
    rows = [(int(sl), int(u)) for sl, u in equip if 0 <= int(sl) < 32 and int(u) in by_uid]
    mask = 0
    for sl, _u in rows:
        mask |= 1 << sl
    body = struct.pack(">I", mask)
    for sl, u in sorted(rows):
        body += struct.pack(">I", u) + struct.pack(">IH", 2, by_uid[u] & 0xFFFF)
    return body
