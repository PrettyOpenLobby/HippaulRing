"""Equip and unequip mid-session, the worn-slot map, self redress."""
import struct
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, itemrecords, sess, skilllist, wire

# ---------------------------------------------------------------------------
# THE MID-SESSION EQUIP: 0x107A, AND THE WORN-SLOT MAP IT NEEDS
#
# Found 2026-09-05 by decoding the three field dispatchers' jump tables. The
# client validates an equip locally, sends 0x2021 [u32 uid][u8 worn slot], and
# accepts 0x102A as the OK -- but 0x102A's arm (0x0503c390) does nothing except
# clear the in-flight flag 0x200000 on the player. Nothing about the OK moves an
# item. The reflection is a SEPARATE server push, and there are two of them:
#
#   0x107A  (dispatcher 0x503a8d0 / idx 0x503a8f0 / base 0x107a, arm 0x0503a52b
#           -> worker 0x0503d540)   ONE item, add or remove
#   0x1006  type 0 mask3            THE WHOLE 13-SLOT WORN ARRAY at once
#
# 0x107A is the one to use, because it is the incremental one -- and finding it
# RETRACTS this file's own "no incremental item-add message exists".
#
#     [u8 add]        1 = add/update this item, 0 = REMOVE the one in `slot`
#     [u8 announce]   1 = draw the client's own "got item" notice (0x0511f500)
#     [u32 slot]      the bag slot; on a remove this is the slot to empty
#     if add:  [u32 uid] + the four masked field groups (item_fields)
#
# WARNING: THE SLOT IS A u32 AND I SHIPPED IT AS A u16, which put every byte after it
# TWO BYTES OUT. Proved live 2026-09-05 by the feitem shim hook, which logged
# Equip's arguments at the moment of the call:
#     healthy (field entry):  unit=1A71E7C0  uid=1001
#     our 0x107A:             unit=7F760000  uid=65601536 = 0x03E90000
# and 0x03E90000 is 1001 << 16 -- the uid read starting 2 bytes late. The reader
# at 0x0503d598 is 0x5045ef0, whose own advance of [0x5338360] is `add edx,4`;
# I had verified that width for IT_SLOT in item_fields and then packed the
# HEADER's copy of the same field as ">BBH". Six live probes, four crashes at two
# different RVAs and three dead mechanism theories all came from this one wrong
# format character: the client was reading a garbage uid, failing the lookup, and
# calling Equip on a garbage unit pointer.
#
# and the envelope object id is the PLAYER'S UNIT id, looked up in class 1.
# THAT LOOKUP HAS NO NULL CHECK (0x0503d570 calls 0x5077d10 on the result
# straight away), so a 0x107A addressed to an id the client does not know is an
# access violation, not a dropped message. The bag reader in 0x1000 takes the
# same id from 0x5077cc0(0x507d570()) -- the client's own unit -- which is the
# charid we spawn the player entity under, so unit_id_of() is the right answer
# and --unit-id 0 would be a crash.
#
# THE EQUIP ITSELF is the tail of the shared per-item reader 0x0503d280, the
# same one the bag record uses: after inserting the item into the unit it does
#
#     cmp byte [item+0x4c8], 0xff ; jne -> unit->Equip(uid, -1, 1)   0x0503d31d
#
# so an item whose marker is not 0xff is WORN, and Equip picks the worn slot
# itself from the item table. That is why this needs neither the worn-gear
# sub-record 0x0503c470 (which crashed field entry, RVA 0xAC647) nor the marker
# in the 0x1000 bag record (live #14: equipping DURING field entry dressed the
# character over the default outfit and froze movement). Same call, later.

#: [item+0x4c8] is the WORN INDEX, and Equip is its writer: the last thing
#: 0x0507b6c0 does is `push ebx; call 0x5073c60`, which stores that index
#: (0x05073c8b). So the marker is not a boolean -- 0xff means "not worn" and
#: anything else is the slot it is worn in. Equip derives the index from the
#: item table's slot type ([tbl+0x50], 1..0xB) through its own jump table
#: 0x0507b920: types 1/9/10 -> 0, 2..7 -> 1..6, 8 -> the first free of 7..10
#: (the scan at 0x0507b76a), 11 -> the first free of 11..12 (0x0507b7b9).
WORN_FIXED = {1: 0, 9: 0, 10: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6}
WORN_SCAN = {8: (7, 10), 11: (11, 12)}
POCKET_SLOT_TYPE = 11                   # worn 11/12: the quick-use pockets
WORN_SLOTS = 13                         # the array [unit+0x870], 13 dwords


def worn_index(slot_type, taken=()):
    """The worn slot Equip would choose for an item of this slot type, or None.

    `taken` are the indices already occupied, which only matters for the two
    scanning types; Equip itself falls back to the first slot of the range when
    every one is full, and so does this."""
    slot_type = int(slot_type)
    if slot_type in WORN_FIXED:
        return WORN_FIXED[slot_type]
    if slot_type in WORN_SCAN:
        lo, hi = WORN_SCAN[slot_type]
        for i in range(lo, hi + 1):
            if i not in taken:
                return i
        return lo
    return None


def worn_layout(equip, items):
    """{uid: worn index} for the stored (slot_type, uid) pairs, in list order.

    The store keeps the item table's slot TYPE (that is what felobby seeds and
    what the 0xD002 record carries); the marker the client wants is the worn
    INDEX. This is the one place that converts, so the two cannot drift.
    """
    known = {int(u) for u, _no, _f, _c in itemrecords.item_rows(items)}
    out, taken = {}, set()
    for slot, uid in equip:
        uid = int(uid)
        if uid not in known:
            continue
        i = worn_index(slot, taken)
        if i is None:
            continue
        taken.add(i)
        out[uid] = i
    return out


def item_add_body(uid, no, slot, count=1, equip=None, kind=0, announce=0):
    """0x107A, the add form (worker 0x0503d540, per-item reader 0x0503d280)."""
    return (struct.pack(">BBI", 1, announce & 0xFF, slot & 0xFFFFFFFF)
            + struct.pack(">I", uid & 0xFFFFFFFF)
            + itemrecords.item_fields(no, count, slot, equip, kind))


def item_remove_body(slot):
    """0x107A, the remove form: the client reads the uid out of its own array
    (0x0507ba90(unit, slot, 0)), unlinks it (0x0507b1a0) and DELETES the object
    -- so no uid goes on the wire and a wrong slot destroys the wrong item."""
    return struct.pack(">BBI", 0, 0, slot & 0xFFFFFFFF)


def equip_reflect_push(conn, outbound, mode, be, args, rows, why, kind="request"):
    """Push one 0x107A per (uid, item, bag slot, worn) row -- the reflection.

    `worn` None sends the 0xff marker, which is how an UNEQUIP is reflected:
    the item is re-sent into the same bag slot with no marker, so the reader's
    tail skips the Equip call. Nothing here removes the item from the bag.

    WARNING: LIVE 2026-09-05, FIRST RUN: the ENTRY replay (four 0x107A in the ADD
    burst, after the enter-area round trip) CRASHED THE CLIENT on field load,
    twice, at RVA 0xE3FCF = 0x05073FCF -- `cmp dword [eax+0x50], 0xb` with
    eax = 0, a NULL [item+0x4e8] inside 0x5073f90 (no null check). Measured off
    errlog260905.csv, and that RVA is in no earlier errlog. Default is `off`
    until the WHY is understood -- see the --equip-reflect help. `kind` is which of the
    two callers this is, so the switch can enable them one at a time: the
    entry replay fires unprompted during a load, the request reflection only
    after the player clicks in a field that is already built, and they are not
    equally risky.
    """
    # WARNING: NEVER BEFORE THE FIELD EXISTS. 0x0503d540 resolves the envelope id
    # through 0x504d410(mgr, id, class 1) and hands the result straight to
    # 0x5077d10 with NO NULL CHECK (0x0503d570), so a 0x107A that arrives while
    # the client has no unit by that id is an ACCESS VIOLATION, not a dropped
    # message. Proved live 2026-09-05T00:34:22Z: a probe queued in the gmcmd
    # file fired 20 ms after CONNECT, before any field entry, and the client
    # died at RVA 0xE7D14 = 0x05077D14 with eax=0x200000 -- the flag argument,
    # on a null object. A different crash from the alignment bug, and one this
    # guard makes unreachable from the shipping path.
    if not sess._SESSION.get("in_field"):
        print("[feworld]    0x107A NOT sent (%s): the session is not in a field "
              "yet, and the client resolves this message's unit id with no null "
              "check -- sending now would be an ACCESS VIOLATION, not a no-op"
              % why, flush=True)
        return 0
    want = getattr(args, "equip_reflect", "off")
    # kind "client" is the --add-self client replay after the first clock
    # sample: the field is live, so it is the request-shaped case, not the
    # load-time entry replay that crashed 09-05.
    if want == "off" or (want == "request" and kind == "entry"):
        print("[feworld]    --equip-reflect %s: %d equip change(s) NOT "
              "reflected (%s); the client will keep showing the item unworn "
              "however many 0x102A it gets" % (want, len(rows), why), flush=True)
        return 0
    for row in rows:
        # (uid, item, bag slot, worn) with an OPTIONAL stack count -- the count
        # only exists since the Stack button, and a row without one is one.
        uid, no, slot, worn = row[0], row[1], row[2], row[3]
        count = row[4] if len(row) > 4 else 1
        body = item_add_body(uid, no, slot, count, worn, 0, 0)
        wire.send(conn, outbound, wire.inner_msg(0x107A, body, wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        print("[feworld] -> 0x30 inner 0x107A MSG_ITEM_ADD uid=%d item=%d "
              "slot=%d marker=%s (%s) -- the per-item reader 0x0503d280 "
              "re-reads the object and its tail calls Equip(uid,-1,1) when the "
              "marker is not 0xff"
              % (uid, no, slot, "0xff (not worn)" if worn is None else worn,
                 why), flush=True)
    return len(rows)


# ---------------------------------------------------------------------------
# WARNING:KEY: THE SELF RECORD STRIPS THE ARMOUR THE BAG READER JUST PUT ON
# (measured 2026-09-12 in a live client log, polshim.641768.log, all three
# field entries of one session).
#
#   0x1000  the bag reader Equips the 4 marked worn items (Equip #14..17 on the
#           field unit 7F760000 -- --field-equip on, worn 0/1 left unmarked)
#   0x1006  our self record HITS ("Chara 1 already exists"); the hit path ends
#           in the unit's virtual +0x4c (0x0503adb6) -> 0x0507dbe0 ->
#           0x0507dc00, which returns early only when [unit+0x3c] == 0 (it is 1
#           on a live unit) and otherwise calls the MODEL BUILDER 0x5079f90:
#           m_face / m_hair / m_body ::Release> and rebuilt -- and the armour
#           the bag reader attached is gone with the old body
#   0x100E  the loading screen comes off, and the unit is in its all00 parts
#   +1.5 s  the 0x107A replay (field_ready) Equips again, and each Equip
#           RELEASES all00_m_body / _leg / _foot -- the armour was gone
#
# That 1.5 s of all00 under the fade is the reported "base outfit clipping
# for a split second when loading in". Nothing in the record can skip the
# rebuild (the only gate is +0x3c), and the self record is the one carrier of
# Nation/Comment, so the fix is to put the armour back BEFORE 0x100E. The
# weapon (worn 0/1) stays on the field-ready replay: a weapon in hand when a
# self record applies sets STANCE (run 7), which is also what froze the second
# area on 2026-09-12 -- that replay landed BEFORE the self record.
# WARNING: A 0x107A in this window crashed the client on 2026-09-05 (0x05073FCF, a
# NULL item record) -- in the wrong-marker era, before the bag reader
# registered these items. That is why --self-redress defaults OFF.
# ---------------------------------------------------------------------------
def self_redress_rows(args):
    """The stored worn rows the self record's rebuild strips: every worn item
    EXCEPT worn 0/1 (the weapon), exactly the set the bag reader Equips."""
    return [r for r in stored_equip_rows(args)
            if r[3] is not None and r[3] not in (0, 1)]


def self_redress_push(conn, outbound, mode, be, args):
    """0x107A for the stripped armour, straight after the self 0x1006 and
    before 0x100E. Returns the rows sent; records their uids so the
    field-ready replay does not Equip them a second time."""
    sess._SESSION["self_redressed"] = set()
    if getattr(args, "self_redress", "off") != "armour":
        return []
    if (getattr(args, "field_equip", "off") != "on"
            or getattr(args, "field_items", "bag") not in ("on", "bag")):
        # the bag reader did not dress the unit, so there is nothing to restore
        return []
    rows = self_redress_rows(args)
    if not rows:
        return []
    n = equip_reflect_push(conn, outbound, mode, be, args, rows,
                           "--self-redress: the self record's model rebuild "
                           "stripped it -- re-dressed under the loading screen",
                           "redress")
    if not n:
        return []
    sess._SESSION["self_redressed"] = {int(r[0]) for r in rows}
    return rows


def replay_rows_after_redress(rows):
    """The field-ready replay minus whatever self_redress_push already put
    back this entry -- an Equip of a worn uid releases and reloads its model,
    which would be a second, later flicker."""
    done = sess._SESSION.get("self_redressed") or set()
    return [r for r in rows if int(r[0]) not in done]


# ---------------------------------------------------------------------------
# UNEQUIP -- `0x2004`, and why the OK plus a 0x107A was never enough.
#
# Reported live 2026-09-06: "the game removes the item but doesn't restore the
# default mesh underneath and the item still shows as equipped in my
# inventory." Both halves are one missing call.
#
# What we did until now: answer `0x102C` (which reads no body -- its worker
# 0x0503c440 clears the in-flight flag 0x200000 and returns) and push a
# `0x107A` with the 0xff marker. That sets `[item+0x4c8] = 0xff` and re-inserts
# the object into the bag array, and it NEVER TOUCHES THE UNIT. The unit's own
# 13-slot worn array `[unit+0x870]` still names the item, so the inventory
# still draws it worn; and nothing calls the client's unequip, so the body part
# the equip swapped out is never swapped back.
#
# KEY: The client's unequip is `0x0507b950(unit, slot, 1)`:
#     * clears `[unit + slot*4 + 0x870]`, and for the two-handed slot types
#       (1, 9, 10) also `[unit+0x874]` -- Equip writes BOTH, so a weapon
#       occupies worn 0 and worn 1;
#     * calls `0x05073f00(item)` -> `[item+0x4e0] = 0` and
#       `0x05073a00(item, 1)`, whose restore flag is the whole point: for slot
#       types 4..7 it picks a body-part offset (0xC0/0x100/0x140/0x180) and
#       calls `0x04fbf580` to PUT THE DEFAULT PART BACK.
#
# And exactly one wire path reaches it: the WORN-GEAR sub-record `0x0503c470`,
# whose uid == 0 branch (`0x0503c50f`) does GetWorn(slot) -> unequip. That
# sub-record is bit 0 of message **`0x2004`** (c0 arm `0x0503a5fd` ->
# `0x0503aaa0`, mask bit 0 -> `0x0503c470`; bit 1 -> `0x0503c740`). The reader
# also accumulates a flag over all 13 slots and, if any unequipped item's
# `[tbl+0x1c8]` is 0..0xa, finishes with `0x0507be60(unit, 0)` -- the full
# appearance rebuild. So one 12-byte message does the whole job:
#
#     0x2004  [u32 mask=1][u32 slot mask][u32 0]
#
# WARNING: ORDER MATTERS. Send this BEFORE the 0x107A: the uid == 0 branch reads the
# item back out of the unit's worn array, so the array has to still name it.
#
# WARNING: It must be addressed to the LOCAL PLAYER. When the record's unit is not
# the local player the same branch DESTROYS the item object (`0x0504cda0`
# then `0x04fcf880(6)` at `0x0503c578`), which would delete the thing we are
# trying to put back in the bag.
def unequip_push(conn, outbound, mode, be, args, slot, why=""):
    """0x2004 worn-gear: slot `slot` is now EMPTY. See the block comment."""
    if getattr(args, "unequip_reflect", "on") != "on":
        print("[feworld]    --unequip-reflect off: worn slot %s NOT cleared "
              "(%s); the client keeps the item in [unit+0x870] and the body "
              "part stays swapped out" % (slot, why), flush=True)
        return False
    if not sess._SESSION.get("in_field"):
        return False
    if slot is None or not (0 <= int(slot) <= 12):
        print("[feworld]    0x2004 NOT sent: worn slot %r is outside the "
              "client's 13-slot array (%s)" % (slot, why), flush=True)
        return False
    body = struct.pack(">III", 0x1, 1 << int(slot), 0)
    wire.send(conn, outbound, wire.inner_msg(0x2004, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x2004 WORN-GEAR slot %d = EMPTY (%s) -- "
          "0x0503c470's uid==0 branch runs the client's own unequip "
          "(0x0507b950 -> 0x05073f00), which clears [unit+0x870+%d], puts the "
          "default body part back for slot types 4..7 and triggers the "
          "appearance rebuild 0x0507be60"
          % (int(slot), why, int(slot)), flush=True)
    return True


# ---------------------------------------------------------------------------
# WHY A SKILL WILL OR WILL NOT CAST -- printed, not guessed.
#
# 2026-09-06, after "Skill conditions not met when I try to use it on an
# enemy (this is for the basic spell)". That string is gate 4 of the cast
# ladder (0x0507f5xx), and every input to gates 3..6 is in dat.pak:
#
#   gate 3  0x0504a500(skill, [unit+0x2b4])   "Can't use a skill right now."
#   gate 4  0x0504b7d0(unit, skill)           "Skill conditions not met."
#   gate 5  [unit+0x4a4] >= [skill+0xf4]      "Not enough Pow to use skill."
#   gate 6  [unit+0x8ac] >= [skill+0xd4]      "Not enough crystals"
#
# KEY: GATE 4 LOOKS AT WORN INDEX 0 AND 1 ONLY. Its loop is `cmp esi,1 / jle`
# over `[unit + slot*4 + 0x870]`, and for each it takes the item's table
# record and tests `[tbl+0x1cc] & [skill+0x12c]`. So a wand in the right hand
# satisfies 基本魔法攻撃 (270, mask 0x0202) because a Beginner Wand's category
# is 0x0200 -- and the same character wearing only clothes gets exactly the
# message seen in live testing, because a tunic's category is 0x0000.
#
# This prints the whole ladder from the STORED equipment, so the next report of
# a refusal comes with its own answer instead of another round trip.
def cast_report_log(args, why=""):
    if getattr(args, "cast_report", "on") != "on":
        return
    items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
    equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
    if not items:
        return
    by_uid = {int(u): int(no) for u, no, _f, _c in itemrecords.item_rows(items)}
    tbl = fegamedata.items()
    worn = {}
    for sl, u in equip:
        no = by_uid.get(int(u))
        if no is None:
            continue
        idx = fegamedata.worn_index_for(tbl.get(no, {}).get("slot", -1))
        if idx is None:
            continue
        worn[idx] = no
        # Slot types 1/9/10 are two-handed: Equip writes worn 0 AND worn 1.
        if int(tbl.get(no, {}).get("slot", -1)) in (1, 9, 10):
            worn[1] = no
    # acquired_skills(), not a raw field read: it is the one place that
    # tolerates the store's row shapes, and this list is only a report.
    sids = skilllist.acquired_skills(args)
    state = int(getattr(args, "unit_state", None) or 0)
    print("[feworld]    CAST READINESS%s -- worn 0/1 = %s; [unit+0x2b4] = "
          "0x%08X" % ((" (" + why + ")") if why else "",
                      {k: "%d %s" % (v, tbl.get(v, {}).get("name", "?"))
                       for k, v in sorted(worn.items()) if k in (0, 1)}
                      or "EMPTY -- gate 4 refuses every weapon-gated skill",
                      state), flush=True)
    for sid, name, ok, reason in fegamedata.cast_report(worn, sids, state):
        print("[feworld]       skill %-5d %-24s %s  %s"
              % (sid, name, "CAST" if ok else "NO  ", reason), flush=True)


def worn_gear_block(rows):
    """mask3 + body for the 0x1006 type-0 worn-gear array.

    `rows` are stored_equip_rows() tuples (uid, item, bag slot, worn index,
    count); a row with no worn index is skipped. Per set bit, ASCENDING (the
    arm walks bits 0..12 in order, `cmp ebx,0xd` at 0x0503ae9c):
    [u32 uid][u32 mask1=2][u16 item number]. Two rows on one worn index is a
    store fault (worn_layout never produces it); the first wins and the second
    is dropped rather than desynchronising the record."""
    by_worn = {}
    for uid, no, _slot, worn, _ct in rows:
        if worn is None or not 0 <= int(worn) <= 12 or int(worn) in by_worn:
            continue
        by_worn[int(worn)] = (int(uid), int(no))
    m3, body, desc = 0, b"", []
    for worn in sorted(by_worn):
        uid, no = by_worn[worn]
        m3 |= 1 << worn
        body += struct.pack(">IIH", uid & 0xFFFFFFFF, 2, no & 0xFFFF)
        desc.append("%d:uid%d/item%d" % (worn, uid, no))
    return m3, body, ", ".join(desc)


def stored_equip_rows(args, only_uid=None):
    """(uid, item, bag slot, worn index) for the character's stored equipment.

    The bag slot is the item's position in the stored `items` list, which is
    exactly what bag_record() sends -- the two must agree or a 0x107A moves the
    item to a different slot than the one the inventory is drawing it in.
    """
    items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
    equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
    worn = worn_layout(equip, items)
    rows = []
    for i, (uid, no, _f, ct) in enumerate(itemrecords.item_rows(items)[:96]):
        uid = int(uid)
        if only_uid is not None and uid != int(only_uid):
            continue
        if uid in worn or only_uid is not None:
            rows.append((uid, int(no), i, worn.get(uid), ct))
    return rows
