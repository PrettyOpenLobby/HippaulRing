"""The bag: size, sort, stack, capacity, new item uids."""
import struct
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import bank, character, equipment, itemrecords, movement, sess, wire

def bag_size_push(conn, outbound, mode, be, args):
    """`0x2024` maskA bit 0x20000000 -- THE BAG SIZE, and the reason the item
    window drew "0/0" no matter what the bag held.

    The window's constructor reads `word [player+0x4fc]` and hands it to its
    list as the ROW COUNT (0x050b884c -> 0x050b887e), so a zero there means
    the grid has no cells at all; its header then prints
    `sprintf("%2d/%2d", [list+0x8c], rowcount)` (0x050b9098) = 0/0. The
    client names the field itself in the applier's log line,
    `uPossessItemNum = %d` (0x052d2424).

    THE CHANNEL. 0x2024's applier 0x04ff36c0 reads [u32 maskA][u32 maskB]
    and then, per set bit, calls that bit's own reader -- bit 0x20000000 is
    0x04ff4b90, which reads exactly ONE u16 (0x5045e30), writes it to
    [unit+0x4fc] when the object is class 3, and, when that object is the
    player, pokes the window manager (0x050a6450) so an OPEN window resizes.
    WARNING: NOT the 0x1006 record's stat block: that applier (0x0507a990) stops at
    bit 23, so the same bit there is two bytes nothing reads.

    ONE BIT ONLY, for the reason in unit_speed_push: every set bit must be
    backed by exactly its reader's width.
    """
    # KEY: PER CHARACTER since 2026-09-08. `--bag-size` is now only the size a
    # character with none stored STARTS at -- see _seeded_value. A bag that a
    # player can expand (and whose expansion has to survive a relog) cannot be
    # one number for the whole server.
    #
    # WARNING: `--bag-size 0` IS THE OFF SWITCH AND MUST WIN OVER THE STORE, checked
    # BEFORE the store is read. Letting a stored 30 override a flag of 0 takes
    # the admin's only way to silence this channel away the moment any
    # character has been seeded -- fe_ui_test pins it ("0 disables the send").
    # Same rule as an empty --class-levels; see stored_class_levels.
    flag = getattr(args, "bag_size", None)
    if not flag or int(flag) <= 0:
        return
    n = int(character._seeded_value(args, "bag_size", int(flag)) or 0)
    if n <= 0:
        return
    body = struct.pack(">IIH", movement._U2024_BAGSIZE_BIT, 0, n & 0xFFFF)
    wire.send(conn, outbound, wire.inner_msg(0x2024, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 maskA=0x%X bag size=%d -- uPossessItemNum at "
          "[unit+0x4fc]; the item window sizes its grid from it (0 = a 0/0 "
          "window)" % (movement._U2024_BAGSIZE_BIT, n), flush=True)


# ---------------------------------------------------------------------------
# SORT (0x20AA) AND STACK (0x114D) -- THE SAME SHAPE AS THE EQUIP, AND THE SAME
# FIX
#
# 2026-09-06. Both buttons were answered (0x1179 / 0x2095) and both did nothing,
# because -- exactly like 0x102A -- the OK arms 0x0505846D and 0x05058747 make
# ZERO stream reads. The client does not sort or stack anything of its own: the
# SERVER owns the bag layout and has to push the result. The push is the same
# 0x107A the equip reflection uses.
#
# KEY: THE ORDER OF THE PUSHES DOES NOT MATTER, AND THAT IS MEASURED, NOT HOPED.
# The unit's insert (0x0507b330) keeps a uid -> slot map at [unit+0x560] and,
# before storing the new slot, clears the item's OLD array cell -- but only
# after checking the cell still holds THIS uid:
#
#     0507b3b0  eax = [node+0x10]                    ; the slot it used to be in
#     0507b3b3  cmp [unit + eax*4 + 0x570], edx      ; is that cell still me?
#     0507b3bc  mov [unit + eax*4 + 0x570], 0        ; only then, clear it
#     0507b3c5  cmp [unit + eax*4 + 0x6f0], edx      ; ...or the kind-1 array
#
# so an item that has already been overwritten by an earlier push in the same
# burst is not cleared a second time, and a straight swap (A -> B's slot,
# B -> A's slot) cannot blank itself. The new cell is written at 0x0507b547
# from [item+0x4c6]: 0 -> [unit + slot*4 + 0x570], 1 -> +0x6f0, anything else
# is refused.
#
# WARNING: THE ARRAY THAT DRAWS IS KIND 0. --field-item-kind `both` mirrors every item
# into the kind-1 array under a synthetic uid, and that mirror CANNOT be
# maintained incrementally: the 0x107A remove branch takes its uid from
# 0x0507ba90(unit, slot, 0) with the array argument HARDCODED 0 (0x0503d697), so
# there is no way to empty a kind-1 cell BY REMOVE. (KEY: 09-11: re-adding the
# SAME uid as kind 0 does empty it -- the insert clears the uid's previous
# cell in either array, 0x0507b3b0..ce -- which is how the BANK withdraws; it
# is only the mirror's synthetic uids that no add ever re-files.) It does not matter for what the player
# sees -- the inventory window draws kind 0, which is why clicking an item sends
# a real uid and why the equip works at all -- but it does mean the mirror goes
# stale after a sort and stays stale until the next 0x1000 rebuilds it.
#
# WARNING: THE REMOVE DESTROYS AN OBJECT. 0x0503d693 reads the uid out of the
# CLIENT's own array by slot, unlinks it (0x0507b1a0) and calls the object's
# destructor. A slot number that does not mean what we think it means destroys
# the wrong item. Two things keep that honest here: removes are issued FIRST, at
# the slots the store says those uids are in right now (the same indices the
# 0x1000 bag record used), and a slot whose cell is already empty is a safe
# no-op -- the uid comes back 0, the manager lookup misses and the branch falls
# through at 0x0503d6bb without touching anything.


def bag_sort_key(row):
    """CHOSEN. FE's own sort order is not in any table we have dumped, so this
    is a decision, not a measurement: equipment first, grouped by the item
    table's slot type (weapon, then each armour slot in the client's own worn
    order), then everything the table gives no slot to -- consumables, materials
    and Gold -- and within a group by item number, then uid so it is stable.

    Slot type is `[tbl+0x50]`, 1..0xB for wearables and 0 for the rest; -1 is an
    item FE_ITEM_DATA does not have, which sorts with the unwearables rather
    than vanishing.
    """
    uid, no, _flag, _count = row
    slot = fegamedata.items().get(int(no), {}).get("slot", -1)
    return (99 if slot is None or slot < 1 else slot, int(no), int(uid))


def bag_sorted(items):
    """The stored rows in the order above, unchanged rows and all."""
    return sorted(itemrecords.item_rows(items), key=bag_sort_key)


def bag_stacked(items, equip_uids=()):
    """Merge same-item rows into one, and say which uids were merged away.

    Returns (rows, gone) where `gone` is [(uid, the slot it is in now)] for the
    rows that disappeared -- the ones the caller has to remove from the client.

    WARNING: ONLY ITEMS THE TABLE GIVES NO SLOT TYPE TO. FE_ITEM_DATA has no maximum
    stack column that fefet.py has identified, so "what stacks" is a decision:
    slot type 0 (36 of the 598 rows -- Gold, materials, the consumables) merges
    and everything wearable does not. Two axes are two axes; merging them would
    make one of them unequippable and there would be no way to get it back.
    Anything currently WORN is left alone on top of that, whatever its type.
    """
    rows = itemrecords.item_rows(items)
    worn = {int(u) for u in equip_uids}
    out, gone, first = [], [], {}
    for slot, (uid, no, flag, count) in enumerate(rows):
        stackable = (fegamedata.items().get(int(no), {}).get("slot", -1) == 0
                     and uid not in worn)
        if stackable and no in first:
            i = first[no]
            out[i] = (out[i][0], out[i][1], out[i][2], out[i][3] + count)
            gone.append((uid, slot))
            continue
        if stackable:
            first[no] = len(out)
        out.append((uid, no, flag, count))
    return out, gone


def bag_layout_push(conn, outbound, mode, be, args, old, new, gone, why):
    """Make the client's bag look like `new`: remove `gone`, then move the rest.

    `old` and `new` are item_rows lists; the slot of a row is its index. Only
    rows whose slot actually changed are pushed, plus every row whose COUNT
    changed (a stack survivor that did not move still has to be told it is now
    a stack of three).

    REMOVES GO FIRST, at the slots the items occupy right now. Doing it the
    other way round leaves the merged-away objects alive in the item manager but
    orphaned out of the array -- an earlier survivor's move overwrites their cell
    without clearing their entry in the [unit+0x560] map, and then a remove aimed
    at that slot destroys the survivor instead.
    """
    if not sess._SESSION.get("in_field"):
        print("[feworld]    0x107A NOT sent (%s): the session is not in a field "
              "yet, and the client resolves this message's unit id with no null "
              "check -- sending now would be an ACCESS VIOLATION, not a no-op"
              % why, flush=True)
        return 0
    equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
    worn = equipment.worn_layout(equip, new)
    n = 0
    for uid, slot in gone:
        wire.send(conn, outbound, wire.inner_msg(0x107A, equipment.item_remove_body(slot),
                                       wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        n += 1
        print("[feworld] -> 0x30 inner 0x107A MSG_ITEM_ADD add=0 slot=%d "
              "(uid %d, %s) -- the client reads the uid out of its OWN array at "
              "that slot and DESTROYS the object" % (slot, uid, why), flush=True)
    was = {int(r[0]): (i, r[3]) for i, r in enumerate(itemrecords.item_rows(old))}
    for slot, (uid, no, _flag, count) in enumerate(new[:96]):
        prev = was.get(int(uid))
        if prev is not None and prev[0] == slot and prev[1] == count:
            continue                        # already there, already that many
        body = equipment.item_add_body(uid, no, slot, count, worn.get(int(uid)), 0, 0)
        wire.send(conn, outbound, wire.inner_msg(0x107A, body, wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        n += 1
        print("[feworld] -> 0x30 inner 0x107A MSG_ITEM_ADD uid=%d item=%d "
              "slot=%d%s count=%d marker=%s (%s)"
              % (uid, no, slot,
                 "" if prev is None else " (was %d)" % prev[0], count,
                 "0xff (not worn)" if worn.get(int(uid)) is None
                 else worn.get(int(uid)), why), flush=True)
    return n


def bag_organize(conn, outbound, mode, be, args, how):
    """The Sort and Stack buttons, end to end: decide, PERSIST, then push.

    Persisting is not optional. The bag slot an item is in is its index in the
    stored list and nothing else -- that is what the 0x1000 bag record sends at
    field entry -- so a sort that is not written back is undone by the next
    field entry, which is exactly the shape of a button that looks like it
    worked and did not.
    """
    old = itemrecords.item_rows(args)
    equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
    equip_uids = [int(u) for _sl, u in equip]
    if how == "stack":
        new, gone = bag_stacked(old, equip_uids)
    else:
        new, gone = bag_sorted(old), []
    if [tuple(r) for r in new] == [tuple(r) for r in old] and not gone:
        print("[feworld]    %s: the bag is already in that order (%d item%s) "
              "-- nothing to push" % (how, len(old), "" if len(old) == 1 else "s"),
              flush=True)
        return 0, True
    stored = character._store_char_field(args, "items", [list(r) for r in new])
    if not stored:
        print("[feworld]    WARNING: %s: no stored character matched account=%r "
              "charid=%s -- the new layout was NOT persisted, so it would be "
              "undone at the next field entry. Not pushing it either."
              % (how, sess._SESSION.get("account"), sess._SESSION.get("charid")),
              flush=True)
        return 0, False
    n = bag_layout_push(conn, outbound, mode, be, args, old, new, gone, how)
    print("[feworld]    %s: %d row%s -> %d, %d merged away, %d 0x107A pushed"
          % (how, len(old), "" if len(old) == 1 else "s", len(new), len(gone), n),
          flush=True)
    return n, True


def bag_capacity(args):
    """How many rows the bag holds: the character's uPossessItemNum (stored
    `bag_size`, seeded from --bag-size -- the grid the item window draws),
    never more than the client's 96-deep array."""
    flag = getattr(args, "bag_size", None)
    n = 96
    try:
        if flag and int(flag) > 0:
            n = int(character._seeded_value(args, "bag_size", int(flag)) or 96)
    except (TypeError, ValueError):
        pass
    return max(0, min(96, n))


def new_item_uid(args, rows=None):
    """The next unused item uid for this character: one past the highest uid
    in the bag (`rows`, or the stored bag) AND THE BANK, floored at charid*1000.

    WARNING: THE BANK IS WHY THIS EXISTS. A deposit KEEPS the item's uid (the move is
    the client's own insert re-filing the same object into the kind-1 array),
    so a bag-only `max(uid) + 1` would hand a later purchase the uid of an
    item sitting in the bank -- two objects, one id, in the client's item
    manager. Every allocator goes through here."""
    if rows is None:
        rows = itemrecords.item_rows(args)
    uids = [int(r[0]) for r in rows] + [int(r[0]) for r in bank.bank_rows(args)]
    charid = int(sess._SESSION.get("charid") or 0)
    return max(uids + [charid * 1000]) + 1
