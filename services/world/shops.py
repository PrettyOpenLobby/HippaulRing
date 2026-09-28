"""Shops: stock, pages, sell values."""
import struct
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, itemrecords, wire

def shop_type_for(args, name):
    """Which op-8 window type a shopkeeper opens, from --shop-types."""
    kinds = {}
    spec = getattr(args, "shop_types", None) or "weapon=1,armor=2,item=3,ring=4"
    for part in spec.split(","):
        k, _, v = part.partition("=")
        try:
            kinds[k.strip().lower()] = int(v, 0)
        except ValueError:
            pass
    n = name.lower()
    for key in ("weapon", "armor", "ring", "item"):
        if key in n:
            return kinds.get(key)
    return None


def shop_kind_for(args, name, wtype=None):
    """'weapon' | 'armor' | 'item' | 'ring' for a keeper's dat.pak type name,
    or for an op-8 window type (the reverse of --shop-types)."""
    n = (name or "").lower()
    for key in ("weapon", "armor", "ring", "item"):
        if key in n:
            return key
    if wtype is not None:
        for key in ("weapon", "armor", "ring", "item"):
            if shop_type_for(args, key) == wtype:
                return key
    return "item"


# fet_items' slot_type -> what a shop sells. 2 = one-hand weapons (axes,
# swords), 9 = bows, 10 = two-hand weapons and wands; 1 shield, 3 head, 4 body,
# 5 HANDS, 6 legs, 7 feet; 11 = food and potions (bread, apple, the regen and
# power pots).
#
# WARNING: 09-11: slot 5 was "the 69 accessories (rings, CHOSEN)" and the Ring Shop
# sold it for gold. It is the GLOVES -- ファーグラヴス, カクタスガントレット,
# ラッフルブレスレット are all slot 5, and Equip's own map puts 5 on worn 4
# (WORN_FIXED) between body and legs. RoD's Ring Shop was never about rings on
# fingers: it sold HIGHER-GRADE GEAR FOR RINGS, the war currency (SE flow06).
SHOP_SLOTS = {"weapon": (2, 9, 10), "armor": (1, 3, 4, 5, 6, 7),
              "item": (11,), "ring": (1, 2, 3, 4, 5, 6, 7, 9, 10)}

#: 0x107E MSG_BUY_SHOP_ITEM_NG's u32 code, read off the client's own NG table
#: (0x70-byte records from 0x052620b0, resolver 0x05061240: [T+0x60] = code,
#: the msgid among u16[T+0x40..], text at [T+0x6c]; the first match wins).
#: WARNING: an early extraction of this table was framed ONE FIELD OFF (each msgid's last code
#: is credited to the next msgid) -- these were re-read record by record.
#:   2 "Not enough gold to buy."   3 "Your item slots are full, so the item
#:   cannot be bought."   9 "System error / Bad item ID."
#:   20 "Not enough tokens to buy." -- PARTIAL: the only currency-shortage text
#:      besides gold, so it is taken as the RINGS refusal (a GUESS; the
#:      client's own pre-check prints "Not enough Rings" before it ever sends).
BUY_NG_DEFAULT = 9
BUY_NG_NO_GOLD = 2
BUY_NG_NO_RINGS = 20
BUY_NG_BAG_FULL = 3
#: 0x1081 MSG_SELL_ITEM_NG, same table: 2 "You do not have the item to sell",
#: 4 "You do not have that many to sell", 5 "Can't sell equipped items."
SELL_NG_NOT_HELD, SELL_NG_COUNT, SELL_NG_EQUIPPED = 2, 4, 5

#: The keeper's dat.pak type name carries its class. SE's 2006 guide (flow06):
#: one Weapon_Shop and one Armor_Shop PER CLASS, each selling that class's own
#: gear, and one Ring_Shop per class. Heavy / Light / Cloth armour are the
#: Warrior's / Scout's / Sorcerer's -- the same split the proficiencies make
#: (447 heavy, 446 light, 445 cloth, fet class_param).
SHOP_CLASS_PREFIX = (("warrior_", 0), ("heavy_", 0), ("scout_", 1),
                     ("light_", 1), ("sorcerer_", 2), ("cloth_", 2))


def shop_class_for(name):
    """0 / 1 / 2 for a class shop's keeper name, None for a shop that serves
    everyone (the Item_Shop, or a hand-placed `shop:N` row with no class)."""
    n = (name or "").lower()
    for pre, cls in SHOP_CLASS_PREFIX:
        if n.startswith(pre):
            return cls
    return None


def shop_stock(args, kind, cls=None):
    """[(item_id, gold, rings)] a shop of `kind` sells, by item id.

    KEY: THE PRICES ARE THE ITEM TABLE'S (--shop-prices table, the default):
    FE_ITEM_DATA's +0x90 is the gold price and +0x94 the Ring price (see
    fegamedata.items() for why those names). A GOLD shop (weapon / armor /
    item) stocks the rows with a gold price and NO ring price; the RING shop
    stocks the rows WITH a ring price and charges only Rings for them -- that
    split is what SE's guide describes (higher-grade gear for Rings) and it is
    exactly the "+1" / named-set rows +0x94 marks. A price of 0 is not for
    sale (the Maiden set, the books, Gold itself).

    `cls` (0/1/2) keeps a class shop to its own class's gear: an item is the
    class's when its prerequisite proficiency is one the class has, and an
    item with no prerequisite (the Casual / Civic clothes) belongs to every
    class. None = no class filter.

    CHOSEN, and labelled so: nothing above --shop-level-max (40, RoD's launch
    level cap) is stocked -- the table carries Lv45/50 rows no 2006 character
    could wear. `--shop-prices flat` is the old behaviour: every row of the
    slot types at --shop-price gold (the ring shop at --shop-price Rings)."""
    try:
        import fegamedata
        items = fegamedata.items()
        shipped = fegamedata.item_prices_ship()
    except Exception:
        return []
    flat = int(getattr(args, "shop_price", 100) or 100)
    mode = getattr(args, "shop_prices", "table") or "table"
    if mode == "table" and not shipped:
        print("[feworld]    WARNING: --shop-prices table: fe-fet-items.tsv has NO "
              "price column (regenerated by an older fefet.py?) -- every row "
              "is priced at --shop-price %d instead" % flat, flush=True)
        mode = "flat"
    lvmax = int(getattr(args, "shop_level_max", 40) or 0)
    slots = SHOP_SLOTS.get(kind, ())
    out = []
    for no, it in sorted(items.items()):
        if it.get("slot") not in slots:
            continue
        if lvmax and int(it.get("level") or 0) > lvmax:
            continue
        if cls is not None:
            owners = fegamedata.item_classes(no)
            if owners and cls not in owners:
                continue
        gold, rings = int(it.get("price") or 0), int(it.get("ring_price") or 0)
        if mode != "table":
            if kind == "ring":
                out.append((no, 0, flat))
            else:
                out.append((no, flat, 0))
        elif kind == "ring":
            if rings > 0:
                out.append((no, 0, rings))
        elif gold > 0 and rings == 0:
            out.append((no, gold, 0))
    return out


def shop_page_body(args, stock, page):
    """0x107B MSG_GET_SHOP_ITEM_LIST_OK, the way the list data object reads
    it (0x50f6290 -> the page reader 0x50f5df0):

        [u32 page][u16 n] n x { [u32 item][u32 A][u32 B]
                                [u32 mask1][u32 mask2][u32 mask3][u32 mask4] }

    `item` lands in the row's item object at +0x3c (0x5077cb0), A and B in
    the 0x504-byte record's first two dwords. VERIFIED: LIVE 2026-09-05 #7: the
    list rendered with A in the price column ("the beginner axe is 100")
    and B in the RINGS column ("the beginner wand requires 57 rings" -- it
    was the 58th weapon: our stock index). So A = the GOLD price, B = the
    RING price. The four masks select optional item-object fields
    (0x5075980/a80/ad0/b80 -- the same serialisation as the inventory's
    item objects).

    WARNING: LIVE 2026-09-05 #5 (CRASH, errlog c0000005 at DLL+0x125e6b): the
    masks can NOT all be zero. The row setup 0x50b5dd0 looks the item's
    TABLE ENTRY up by the u16 at obj+0x3a2 (0x5198150: a map at 0x535cad8
    keyed by item number, filled from the client's item table) and reads
    the entry unchecked; the object's ctor leaves +0x3a2 = 0xffff and only
    mask1 bit1 (u16 -> +0x3a2, then the lookup into +0x4e8) fills it. The
    client's own setter (0x5073916) writes the SAME number to +0x3c and
    +0x3a2, so mask1 = 2 followed by the item number as u16."""
    per = max(1, int(getattr(args, "shop_page", 30) or 30))
    rows = stock[page * per:(page + 1) * per]
    body = struct.pack(">IH", page & 0xFFFFFFFF, len(rows))
    for no, gold, rings in rows:
        body += struct.pack(">III", no, gold, rings)     # item, A gold, B rings
        body += struct.pack(">IH", 2, no & 0xFFFF)      # mask1 bit1: table key
        body += struct.pack(">III", 0, 0, 0)            # mask2..4: nothing
    return body


def sell_value_push(conn, outbound, mode, be, args, rows):
    """0x1086 MSG_GET_SELL_ITEM_VALUE_OK -- THE SELL PRICE, and its only writer.

    2026-09-05. The sell price draws as -1 because the item object's ctor sets
    it to -1 (0x05073976 -> [item+0x4f0]) and NOTHING ELSE EVER WRITES IT except
    this message. It is not one of the four masked field groups: the readers
    0x5075980/a80/ad0/b80 between them touch +0x380..+0x4cc and never +0x4f0, so
    no amount of item_fields() would have carried it -- the field looked like a
    hole in the bag record and is not one.

        [u32 uid][u32 value]        body reader 0x050f6470
        arm 0x05023f36 (table 0x5025954, base 0x107B) -> window 0x050efb80,
        which looks the uid up in category 0xbb8 and stores [item+0x4f0]

    IT IS ROUTED TO THE SHOP WINDOW, [mgr+0xe0]. The arm tests that pointer and
    DROPS the message when it is null (0x05023f5d), so this only lands while a
    shop is open -- sending it at field entry writes nothing. And the client
    never asks: no request anywhere in the image registers 0x1086/0x1087, so the
    value has to be volunteered.
    """
    if not rows:
        return 0
    for uid, value in rows:
        wire.send(conn, outbound,
             wire.inner_msg(0x1086, struct.pack(">II", uid & 0xFFFFFFFF,
                                           value & 0xFFFFFFFF),
                       wire.unit_id_of(args)),
             mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1086 MSG_GET_SELL_ITEM_VALUE_OK x%d "
          "[u32 uid][u32 value] -- writes [item+0x4f0], the field that was "
          "drawing -1; routed to the shop window at [mgr+0xe0] and dropped if "
          "no shop is open. Values: %s"
          % (len(rows), ", ".join("%d=%d" % r for r in rows[:6])), flush=True)
    return len(rows)


def item_sell_value(args, no):
    """What ONE of item `no` sells for: --sell-price percent of its buy price.

    The buy price is the item table's own (+0x90, see fegamedata.items());
    the PERCENTAGE is ours -- FE's real buy-back rate is not in any table we
    have read, and 50 is the common MMO convention, labelled as a choice. An
    item with no table price (Gold, the books, the Maiden set) sells for
    nothing. `--shop-prices flat` keeps the old flat --shop-price basis."""
    pct = int(getattr(args, "sell_price", 0) or 0)
    if pct <= 0:
        return 0
    buy = int(getattr(args, "shop_price", 100) or 100)
    if (getattr(args, "shop_prices", "table") or "table") == "table":
        try:
            if fegamedata.item_prices_ship():
                buy = int(fegamedata.items().get(int(no), {}).get("price") or 0)
        except Exception:
            pass
    return max(0, buy * pct // 100)


def sell_value_rows(args):
    """(uid, value) for everything in the bag -- item_sell_value per row."""
    if int(getattr(args, "sell_price", 0) or 0) <= 0:
        return []
    items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
    return [(int(u), item_sell_value(args, no))
            for u, no, _f, _c in itemrecords.item_rows(items)[:96]]


def shop_pages(args, stock):
    per = max(1, int(getattr(args, "shop_page", 30) or 30))
    return max(1, (len(stock) + per - 1) // per)
