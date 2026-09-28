"""The bank: 40 item slots and a gold balance per character."""
import struct
from . import character, equipment, inventory, itemrecords, sess, wallet, wire

# ---------------------------------------------------------------------------
# THE BANK -- 40 slots of items and a gold balance, stored per character.
#
# 2026-09-11. Until today 0x1149..0x114C were rows of UI_HEADER_ONLY_OK:
# answered, "acknowledged, not simulated", and nothing was stored. Everything
# below is STATIC (the bank-window read of 09-11), not yet verified live:
#
#   the window     event op 8 type 0x18 (EV_WIN_BANK); the only creation site
#                  0x05174661 puts it at [app+0xd8]. Its BANK list [win+0xa4]
#                  (flag 1) fills from 0x050b7ab0: slots 0..95 of the unit's
#                  SECOND item array, 0x0507ba90(unit, slot, 1) = [unit+0x6f0],
#                  keeping items whose [item+0x4c6] == 1, each drawn in cell
#                  [item+0x4cc]. Its BAG list [win+0xa0] (flag 0) is the bag.
#   its cells      = [unit+0x4fe] "uBankItemNum" -- a CAPACITY, not a count:
#                  the list ctor makes exactly that many (0x050e8885), and
#                  0x2024 maskA bit 0x40000000 (u16) resizes an open window.
#                  Live 08-25 it read 0, i.e. a bank with NO cells. 40 is SE's
#                  (flow06: 「保管数の上限は40個」), not the client's.
#   its gold       [unit+0x4f8] "sBankGold", 0x2024 maskA bit 0x10000000 (u32);
#                  the window's OK button is disabled locally when a deposit
#                  exceeds GOLD on hand or a withdrawal exceeds this.
#   the requests   0x1149 [u32 gold]  0x114A [u32 gold]
#                  0x114B [u32 uid][u16 count]   (uid = a bag item)
#                  0x114C [u32 uid][u16 count]   (uid = a bank item)
#                  and nothing at all is sent when the window opens.
#   the OKs        0x208D / 0x208F / 0x2091 / 0x2093 read NOTHING: each only
#                  clears the window's in-flight lock [win+0xc4] (while it is
#                  set every input is ignored, so an unanswered request locks
#                  the window). Nothing moves locally -- the server PUSHES the
#                  result: 0x107A for items, 0x2024 for gold.
#   WARNING: NEITHER THE OK NOR THE NG ARMS NULL-CHECK [app+0xd8] (0x05058576): a
#                  bank reply with no bank window open is an access violation.
#                  So they are only ever sent while _SESSION["bank_open"].
#
# KEY: MOVING AN ITEM IS RE-FILING THE SAME uid. 0x107A add with IT_KIND = 1
# inserts at [unit+0x6f0 + slot*4] (0x0507b526), and the unit's insert first
# clears the uid's PREVIOUS cell -- by the slot its map node remembers, in the
# kind-0 array if that cell still holds the uid, else the kind-1 cell at the
# same index (0x0507b3b0..0x0507b3ce). So a deposit is one add (same uid, kind
# 1, a bank slot) and a withdrawal one add (same uid, kind 0, a bag slot); the
# 0x107A REMOVE branch, which only knows kind 0 (0x0503d697), is never used on
# the bank. WARNING: The insert does not bounds-check the slot: 96+ would write into
# the worn array at +0x870, so bank slots stay below min(--bank-slots, 96).
# WARNING: --field-item-kind both mirrors the bag into the kind-1 array, i.e. INTO
# THE BANK WINDOW; the bank refuses to open its cells while that is set.
#
# The bank's rows keep an EXPLICIT slot ([uid, item, flag, count, slot]) --
# unlike the bag, whose slot is its list index -- so a withdrawal from the
# middle shifts nobody and costs one push.
BANK_NG_COUNT, BANK_NG_FULL, BANK_NG_NOT_HELD, BANK_NG_EQUIPPED = 7, 8, 9, 15
#: 0x2092 DEPOSIT_ITEM_NG: 7 "Bad item count", 8 "The bank's item slots are
#: full", 9 "That is not an item you hold", 15 "Can't deposit equipped items".
#: 0x2094 WITHDRAW_ITEM_NG: 7 count, 8 "Your item slots are full", 9 "That is
#: not a bank item". 0x208E / 0x2090: 5 "Not enough on hand" / "Deposit too
#: low", 6 "Exceeds deposit limit" / "Exceeds carry limit". All read off the
#: NG table record by record (see BUY_NG_*).
BANK_GOLD_NG_SHORT, BANK_GOLD_NG_LIMIT = 5, 6
_U2024_BANKGOLD_BIT = 0x10000000          # u32 -> [unit+0x4f8] sBankGold
_U2024_BANKCAP_BIT = 0x40000000           # u16 -> [unit+0x4fe] uBankItemNum
#: request: (OK, NG, name)
BANK_REQUESTS = {0x1149: (0x208D, 0x208E, "MSG_DEPOSIT_GOLD"),
                 0x114A: (0x208F, 0x2090, "MSG_WITHDRAW_GOLD"),
                 0x114B: (0x2091, 0x2092, "MSG_DEPOSIT_ITEM"),
                 0x114C: (0x2093, 0x2094, "MSG_WITHDRAW_ITEM")}
#: gold is a u32 on the wire and the client prints it '%lu'; a balance past
#: this is refused with the client's own "limit" codes rather than wrapped.
GOLD_MAX = 0x7FFFFFFF


def bank_slots(args):
    """The bank's cell count: --bank-slots (SE's 40), below the 96-deep array."""
    return max(0, min(96, int(getattr(args, "bank_slots", 40) or 0)))


def bank_rows(args):
    """The stored bank as [(uid, item, flag, count, slot)]."""
    out = []
    for x in (character._load_char_field(args, "bank", []) or []):
        x = tuple(x)
        if len(x) < 5:
            continue
        out.append((int(x[0]), int(x[1]), int(x[2]), max(1, int(x[3])),
                    int(x[4])))
    return out


def bank_gold_get(args):
    try:
        return max(0, int(character._load_char_field(args, "bank_gold", 0) or 0))
    except (TypeError, ValueError):
        return 0


def bank_state_push(conn, outbound, mode, be, args, why=""):
    """0x2024 maskA 0x10000000|0x40000000: [u32 bank gold][u16 cells] --
    ascending bit order, each field exactly its setter's width (0x04ff4b30
    reads a u32, 0x04ff4c30 a u16)."""
    if not sess._SESSION.get("in_field"):
        return False
    cap = bank_slots(args)
    if str(getattr(args, "field_item_kind", "0")) == "both":
        print("[feworld]    WARNING: bank: --field-item-kind both mirrors the BAG into "
              "the kind-1 array the bank window lists -- serving 0 cells "
              "rather than a bank full of copies", flush=True)
        cap = 0
    gold = bank_gold_get(args)
    body = struct.pack(">II", _U2024_BANKGOLD_BIT | _U2024_BANKCAP_BIT, 0)
    body += struct.pack(">I", gold & 0xFFFFFFFF) + struct.pack(">H", cap & 0xFFFF)
    wire.send(conn, outbound, wire.inner_msg(0x2024, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    -> 0x2024 BANK gold=%d (+0x4f8) cells=%d (+0x4fe)%s"
          % (gold, cap, (" (%s)" % why) if why else ""), flush=True)
    return True


def bank_items_push(conn, outbound, mode, be, args, rows=None, why=""):
    """One 0x107A add per bank row: kind 1, its bank slot, marker 0xff (a
    bank item must never reach the reader's Equip tail). Re-sending a row the
    client already has is harmless -- the insert re-files the same uid."""
    if not sess._SESSION.get("in_field"):
        return 0
    cap = bank_slots(args)
    n = 0
    for uid, no, _f, ct, slot in (bank_rows(args) if rows is None else rows):
        if not 0 <= slot < cap:
            continue
        wire.send(conn, outbound,
             wire.inner_msg(0x107A, equipment.item_add_body(uid, no, slot, ct, None, 1, 0),
                       wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        n += 1
        print("[feworld] -> 0x30 inner 0x107A BANK uid=%d item=%d bank slot=%d "
              "count=%d kind=1 (%s)" % (uid, no, slot, ct, why), flush=True)
    return n


def bank_open_push(conn, outbound, mode, be, args):
    """Everything the bank window reads at construction, BEFORE the op-8
    window step builds it: the cell count and bank gold (0x2024) and every
    stored item in the kind-1 array (0x107A)."""
    sess._SESSION["bank_open"] = True
    if getattr(args, "bank", "on") != "on":
        return
    bank_state_push(conn, outbound, mode, be, args, "the bank opens")
    bank_items_push(conn, outbound, mode, be, args, None, "the bank opens")


def bank_request(conn, outbound, mode, be, args, real_id, f):
    """0x1149..0x114C, simulated. Returns the reply id sent (or None)."""
    ok, ng, name = BANK_REQUESTS[real_id]

    def reply(mid, code=None, why=""):
        body = b"" if code is None else struct.pack(">I", code)
        wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        print("[feworld] -> 0x30 inner 0x%04X %s_%s%s -- %s"
              % (mid, name, "OK" if code is None else "NG",
                 "" if code is None else " err=%d" % code, why), flush=True)
        return mid

    if not sess._SESSION.get("bank_open"):
        # WARNING: the reply arms deref [app+0xd8] unchecked -- no window, no reply
        print("[feworld]    0x%04X %s with NO bank window open (body %s) -- NOT "
              "answered: the reply arm dereferences the window unchecked"
              % (real_id, name, f.hex()), flush=True)
        return None
    if real_id in (0x1149, 0x114A):
        amt = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
        have, saved = wallet.gold_get(args), bank_gold_get(args)
        if have is None:
            return reply(ng, BANK_GOLD_NG_SHORT,
                         "--gold unset: there is no gold wallet to move")
        if real_id == 0x1149:
            if amt > have:
                return reply(ng, BANK_GOLD_NG_SHORT,
                             "deposit %d, %d on hand" % (amt, have))
            if saved + amt > GOLD_MAX:
                return reply(ng, BANK_GOLD_NG_LIMIT, "bank balance limit")
            have, saved = have - amt, saved + amt
        else:
            if amt > saved:
                return reply(ng, BANK_GOLD_NG_SHORT,
                             "withdraw %d, %d saved" % (amt, saved))
            if have + amt > GOLD_MAX:
                return reply(ng, BANK_GOLD_NG_LIMIT, "carry limit")
            have, saved = have + amt, saved - amt
        if not (character._store_char_field(args, "gold", have)
                and character._store_char_field(args, "bank_gold", saved)):
            return reply(ng, 0, "no stored character to write to")
        wallet.wallet_push(conn, outbound, mode, be, args)
        bank_state_push(conn, outbound, mode, be, args, name)
        return reply(ok, None, "%s %d: gold %d on hand, %d in the bank"
                     % ("deposited" if real_id == 0x1149 else "withdrew",
                        amt, have, saved))
    uid = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
    count = struct.unpack_from(">H", f, 4)[0] if len(f) >= 6 else 0
    bag = itemrecords.item_rows(args)
    bank = bank_rows(args)
    cap = bank_slots(args)
    if real_id == 0x114B:                               # DEPOSIT an item
        hit = [(i, r) for i, r in enumerate(bag) if r[0] == uid]
        worn = {int(u) for _sl, u in
                (character._load_char_field(args, "equip", []) or [])}
        free = sorted(set(range(cap)) - {r[4] for r in bank})
        if not hit:
            return reply(ng, BANK_NG_NOT_HELD, "uid %d is not in the bag" % uid)
        i, row = hit[0]
        if uid in worn:
            return reply(ng, BANK_NG_EQUIPPED, "uid %d is worn" % uid)
        if not 1 <= count <= row[3]:
            return reply(ng, BANK_NG_COUNT, "count %d of %d" % (count, row[3]))
        if not free:
            return reply(ng, BANK_NG_FULL, "all %d bank slots taken" % cap)
        slot = free[0]
        if count == row[3]:
            new_bag = bag[:i] + bag[i + 1:]
            moved = (uid, row[1], row[2], count, slot)
        else:
            # a part of a stack: the bag row keeps its uid and slot with fewer
            # in it, the bank gets a NEW object
            new_bag = list(bag)
            new_bag[i] = (row[0], row[1], row[2], row[3] - count)
            moved = (inventory.new_item_uid(args, bag), row[1], row[2], count, slot)
        new_bank = bank + [moved]
        if not (character._store_char_field(args, "items", [list(r) for r in new_bag])
                and character._store_char_field(args, "bank", [list(r) for r in new_bank])):
            return reply(ng, 0, "no stored character to write to")
        # the bank add FIRST: it re-files the uid, clearing its bag cell, and
        # only then do the rows behind it move up into that cell
        bank_items_push(conn, outbound, mode, be, args, [moved],
                        "deposited uid %d" % uid)
        inventory.bag_layout_push(conn, outbound, mode, be, args, bag, new_bag, [],
                        "deposited uid %d x%d" % (uid, count))
        return reply(ok, None, "uid %d x%d -> bank slot %d (bag %d -> %d, bank "
                     "%d/%d)" % (uid, count, slot, len(bag), len(new_bag),
                                 len(new_bank), cap))
    # WITHDRAW an item
    hit = [(i, r) for i, r in enumerate(bank) if r[0] == uid]
    if not hit:
        return reply(ng, BANK_NG_NOT_HELD, "uid %d is not in the bank" % uid)
    i, row = hit[0]
    if not 1 <= count <= row[3]:
        return reply(ng, BANK_NG_COUNT, "count %d of %d" % (count, row[3]))
    if len(bag) + 1 > inventory.bag_capacity(args):
        return reply(ng, BANK_NG_FULL, "the bag holds %d of %d"
                     % (len(bag), inventory.bag_capacity(args)))
    if count == row[3]:
        new_bank = bank[:i] + bank[i + 1:]
        out_row = (uid, row[1], row[2], count)
        rest = []
    else:
        new_bank = list(bank)
        new_bank[i] = (row[0], row[1], row[2], row[3] - count, row[4])
        out_row = (inventory.new_item_uid(args, bag), row[1], row[2], count)
        rest = [new_bank[i]]
    new_bag = bag + [out_row]
    if not (character._store_char_field(args, "items", [list(r) for r in new_bag])
            and character._store_char_field(args, "bank", [list(r) for r in new_bank])):
        return reply(ng, 0, "no stored character to write to")
    # onto the END of the bag (nobody shifts): the same uid re-filed as kind 0
    # clears its bank cell; a partial withdrawal re-sends the bank row smaller
    inventory.bag_layout_push(conn, outbound, mode, be, args, bag, new_bag, [],
                    "withdrew uid %d x%d" % (uid, count))
    if rest:
        bank_items_push(conn, outbound, mode, be, args, rest,
                        "uid %d now x%d" % (uid, rest[0][3]))
    return reply(ok, None, "bank slot %d uid %d x%d -> bag slot %d (bank %d/%d)"
                 % (row[4], uid, count, len(bag), len(new_bank), cap))
