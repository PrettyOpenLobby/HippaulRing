#!/usr/bin/env python3
"""fe_economy_test.py -- Fantasy Earth's ECONOMY: gold, Rings, the class shops,
the Ring Shop, selling, the inn's fee, kill gold and the bank. No client.

Run from services/:  python ../tools/fe_economy_test.py

WHAT THIS PINS (2026-09-11). Until today nothing on this server SPENT the
wallet: the shops listed everything at a flat 100 and 0x204B said so ("Gold is
NOT charged"), selling paid nothing, the inn was free and the bank answered
four requests with a bare OK and stored nothing. Every check below asserts a
STATE CHANGE -- the stored bag, the stored gold/rings, the stored bank -- and
each one fails on the code before this change, because the thing it asserts
was never written.

The prices are SHIPPED DATA (FE_ITEM_DATA +0x90 gold / +0x94 rings, see
fegamedata.items()); the inn's per-level fee, kill gold and the sell-back rate
are OUR choices behind knobs, and the checks say which is which.
"""
import os
import struct
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)
sys.path.insert(0, os.path.join(os.path.dirname(_HERE), "services"))

import fe_ui_test as U      # noqa: E402  -- the suite's stubs, reused
import fegamedata           # noqa: E402
import feworld              # noqa: E402

STORE = U.STORE
S = feworld._SESSION
FAILS = []


def check(what, cond, detail=""):
    print("  %-66s %s%s" % (what, "PASS" if cond else "FAIL",
                            "" if cond else "  " + str(detail)))
    if not cond:
        FAILS.append(what)


class Out:
    """feworld.send, captured as (msg id, envelope id, body)."""

    def __init__(self):
        self.box = []

    def __call__(self, conn, out, body, mode, be, echo=True, prefix=4):
        unit, mid = struct.unpack_from(">IH", body, 0)
        self.box.append((mid, unit, body[6:]))

    def ids(self):
        return [m for m, _u, _b in self.box]

    def clear(self):
        del self.box[:]


def _open_shop(a, name, npc=500, wtype=1):
    """Put the session where a shop's window step leaves it (event_step's
    shop context), without driving the whole conversation -- fe_ui_test part
    12 drives that end to end."""
    kind = feworld.shop_kind_for(a, name, wtype)
    cls = feworld.shop_class_for(name)
    S["shop"] = {"npc": npc, "kind": kind, "class": cls, "type": wtype,
                 "stock": feworld.shop_stock(a, kind, cls)}
    return S["shop"]


def shops():
    """THE CLASS SHOPS AND THE RING SHOP -- stock and prices."""
    print("[fe_economy_test] shops per class, prices from the item table")
    a = U._args()
    items = fegamedata.items()
    check("the item table ships a price column", fegamedata.item_prices_ship())
    check("...the beginner axe is 30 gold, the axe 60, bread 8 (FE_ITEM_DATA +0x90)",
          (items[301]["price"], items[302]["price"], items[4]["price"]) == (30, 60, 8))
    check("...and the +1 axe carries a RING price (+0x94) of 1",
          items[303]["ring_price"] == 1 and items[302]["ring_price"] == 0)
    check("an item's class is its prerequisite proficiency's class",
          fegamedata.item_classes(301) == (0,) and fegamedata.item_classes(483) == (1,)
          and fegamedata.item_classes(531) == (2,) and fegamedata.item_classes(1666) == ())
    for name, cls in (("Warrior_Weapon_Shop", 0), ("Scout_Weapon_Shop", 1),
                      ("Sorcerer_Weapon_Shop", 2), ("Heavy_Armor_Shop", 0),
                      ("Light_Armor_Shop", 1), ("Cloth_Armor_Shop", 2),
                      ("Warrior_Ring_Shop", 0), ("Item_Shop", None)):
        check("%s is class %s" % (name, cls), feworld.shop_class_for(name) == cls)
    w = feworld.shop_stock(a, "weapon", 0)
    s = feworld.shop_stock(a, "weapon", 1)
    z = feworld.shop_stock(a, "weapon", 2)
    ids = lambda st: {r[0] for r in st}
    check("the Warrior's weapon stall sells the beginner axe, not the bow or wand",
          301 in ids(w) and 483 not in ids(w) and 531 not in ids(w))
    check("the Scout's sells the bow, the Sorcerer's the wand",
          483 in ids(s) and 531 in ids(z) and 301 not in ids(s) | ids(z))
    check("every weapon-stall row is its class's, priced in gold only",
          all(0 in fegamedata.item_classes(n) and g == items[n]["price"] > 0
              and r == 0 for n, g, r in w), w[:3])
    check("no higher-grade (+1) gear at a gold stall", 303 not in ids(w))
    check("stock sizes stay sensible (1..120 rows per class stall)",
          all(1 <= len(feworld.shop_stock(a, k, c)) <= 120
              for k in ("weapon", "armor") for c in (0, 1, 2)),
          [(k, c, len(feworld.shop_stock(a, k, c)))
           for k in ("weapon", "armor") for c in (0, 1, 2)])
    arm = feworld.shop_stock(a, "armor", 2)
    check("the Cloth armour stall sells cloth (445) and the classless Casual set",
          all(set(fegamedata.item_classes(n)) <= {2} for n, _g, _r in arm)
          and 1666 in ids(arm))
    check("...and gloves are armour now (slot 5 was sold as 'rings')",
          any(items[n]["slot"] == 5 for n in ids(arm)))
    ring = feworld.shop_stock(a, "ring", 0)
    check("the Warrior's Ring Shop sells the +1 axe for 1 RING and 0 gold",
          (303, 0, 1) in ring)
    check("...every Ring Shop row is priced in Rings off +0x94 and is gear",
          ring and all(g == 0 and r == items[n]["ring_price"] > 0
                       and items[n]["slot"] in feworld.SHOP_SLOTS["ring"]
                       for n, g, r in ring), ring[:3])
    check("...and no Sorcerer gear at the Warrior's Ring Shop",
          all(set(fegamedata.item_classes(n)) <= {0} for n, _g, _r in ring))
    item = feworld.shop_stock(a, "item")
    check("the Item Shop sells consumables at their table price (bread 8)",
          (4, 8, 0) in item and all(items[n]["slot"] == 11 for n, _g, _r in item))
    check("nothing above --shop-level-max 40 is stocked",
          all(items[n]["level"] <= 40 for k in ("weapon", "armor", "ring")
              for c in (0, 1, 2) for n, _g, _r in feworld.shop_stock(a, k, c)))
    check("--shop-prices flat is the old flat price, still per class",
          all(g == 100 and r == 0
              for _n, g, r in feworld.shop_stock(U._args(shop_prices="flat"), "weapon", 0)))
    # the page carries B = rings for the Ring Shop
    body = feworld.shop_page_body(a, [(303, 0, 1)], 0)
    r = U.Reader(body)
    check("0x107B carries [item][A gold][B rings] as served",
          (r.u32(), r.u16(), r.u32(), r.u32(), r.u32()) == (0, 1, 303, 0, 1))


def buying():
    """0x204B: the purchase DEBITS the wallet, or is refused and changes nothing."""
    print("[fe_economy_test] buying charges gold / Rings")
    U._stub_store()
    out = Out()
    real = feworld.send
    feworld.send = out
    S["in_field"] = True
    try:
        a = U._args(gold=1000, ring=1)
        STORE.clear()
        STORE.update({"items": [[7001, 4, 1]], "gold": 100, "ring": 1})
        _open_shop(a, "Warrior_Weapon_Shop")
        out.clear()
        U._dispatch(a, struct.pack(">HIIH", 0x204B, 500, 301, 2))   # 2 x 30
        check("buying 2 beginner axes (30 each) takes 60 of 100 gold",
              STORE["gold"] == 40, STORE.get("gold"))
        check("...the axes are in the stored bag",
              [r[1] for r in STORE["items"]] == [4, 301, 301], STORE["items"])
        check("...answered 0x107D, the wallet pushed, then two 0x107A",
              out.ids()[:2] == [0x107D, 0x2024] and out.ids().count(0x107A) == 2,
              [hex(m) for m in out.ids()])
        # the 0x2024 carries the new gold (maskA bit 0x200000)
        w = [b for m, _u, b in out.box if m == 0x2024][0]
        mA = struct.unpack_from(">I", w, 0)[0]
        check("...the 0x2024 GOLD field reads 40",
              mA & 0x200000 and struct.unpack_from(">I", w, 8)[0] == 40, w.hex())
        out.clear()
        U._dispatch(a, struct.pack(">HIIH", 0x204B, 500, 302, 1))   # 60 > 40
        check("an axe at 60 with 40 in the purse is REFUSED with 0x107E",
              out.ids() == [0x107E], [hex(m) for m in out.ids()])
        check("...with the client's own 'not enough gold' code",
              struct.unpack(">I", out.box[0][2])[0] == feworld.BUY_NG_NO_GOLD)
        check("...and neither the gold nor the bag moved",
              STORE["gold"] == 40 and len(STORE["items"]) == 3)
        # the Ring Shop spends RINGS
        _open_shop(a, "Warrior_Ring_Shop", wtype=4)
        out.clear()
        U._dispatch(a, struct.pack(">HIIH", 0x204B, 500, 303, 1))   # 1 ring
        check("the Ring Shop's +1 axe costs 1 Ring and no gold",
              STORE["ring"] == 0 and STORE["gold"] == 40
              and STORE["items"][-1][1] == 303, (STORE.get("ring"), STORE.get("gold")))
        out.clear()
        U._dispatch(a, struct.pack(">HIIH", 0x204B, 500, 303, 1))
        check("...and a second with 0 Rings is refused, 'not enough rings'",
              out.ids() == [0x107E]
              and struct.unpack(">I", out.box[0][2])[0] == feworld.BUY_NG_NO_RINGS
              and STORE["ring"] == 0 and len(STORE["items"]) == 4)
        # an item of another class is not in the Warrior's stock
        _open_shop(a, "Warrior_Weapon_Shop")
        out.clear()
        U._dispatch(a, struct.pack(">HIIH", 0x204B, 500, 531, 1))   # the wand
        check("a Sorcerer's wand is not for sale at the Warrior's stall",
              out.ids() == [0x107E] and len(STORE["items"]) == 4)
        # no wallet served (--gold unset): the old free behaviour, logged
        STORE["gold"] = 5
        out.clear()
        U._dispatch(U._args(), struct.pack(">HIIH", 0x204B, 500, 301, 1))
        check("with --gold unset the purchase is waived, not refused",
              out.ids()[0] == 0x107D and STORE["gold"] == 5)
    finally:
        feworld.send = real
        S.pop("shop", None)


def selling():
    """0x204C: the sale CREDITS the wallet with the value the window shows."""
    print("[fe_economy_test] selling pays")
    U._stub_store()
    out = Out()
    real = feworld.send
    feworld.send = out
    S["in_field"] = True
    try:
        a = U._args(gold=1000)
        STORE.clear()
        STORE.update({"items": [[7001, 313, 1], [7002, 4, 1, 3]], "equip": [],
                      "gold": 10})
        U._dispatch(a, struct.pack(">HIIH", 0x204C, 502, 7001, 1))
        check("selling the Battle Axe (table 1860) pays 50% = 930 gold",
              STORE["gold"] == 940, STORE.get("gold"))
        check("...and it left the bag", [r[0] for r in STORE["items"]] == [7002])
        U._dispatch(a, struct.pack(">HIIH", 0x204C, 502, 7002, 2))
        check("selling 2 of a stack of 3 bread (8) pays 2 x 4",
              STORE["gold"] == 948 and STORE["items"][0][3] == 1,
              (STORE.get("gold"), STORE["items"]))
        check("the value the window is shown is the value paid",
              feworld.sell_value_rows(a) == [(7002, 4)])
    finally:
        feworld.send = real


def hunting():
    """A kill pays gold (--kill-gold exp: the monster's shipped EXP reward)."""
    print("[fe_economy_test] kill gold")
    U._stub_store()
    out = Out()
    real = feworld.send
    feworld.send = out
    S["in_field"] = True
    try:
        STORE.clear()
        STORE["gold"] = 0
        # --kill-gold exp PINNED: the first pass's rule; the 2006 default
        # (`rod`, by monster level) is pinned in fe_rod_numbers_test
        # player_damage flat: the one-hit kill needs --hit-damage itself; the
        # table model (attack x skill power, 2026-09-12) is fe_combat_test's
        a = U._args(gold=1000, combat="on", hit_damage=100000, kill_reward="off",
                    kill_gold="exp", player_damage="flat")
        m = {"type": 0, "type_id": 1849, "name": "Duke_Orc", "hp": 10, "hpmax": 10,
             "level": 53, "dead": False, "hits": 0}
        check("the shipped EXP reward of type 1849 is 823",
              fegamedata.npc_exp(0, 1849) == 823)
        S["mobs"] = {4242: m}
        U._dispatch(a, struct.pack(">H", 0xA011)
                    + struct.pack(">IIIIHB", 4242, 1, 0, 0, 0, 0)
                    + struct.pack(">fff", 0, 0, 0))
        check("killing it credits 823 gold (--kill-gold exp, OUR rule)",
              STORE["gold"] == 823 and m["dead"], (STORE.get("gold"), m["dead"]))
        check("...and pushes the wallet (the client prints its own 'Gold' line)",
              0x2024 in out.ids(), [hex(i) for i in out.ids()])
        check("--kill-gold N pays a flat N, off pays nothing",
              feworld.kill_gold(U._args(kill_gold="7"), m) == 7
              and feworld.kill_gold(U._args(kill_gold="off"), m) == 0)
    finally:
        feworld.send = real
        S.pop("mobs", None)


def inn():
    """The inn: free to Lv5, then level x --inn-price-per-level gold."""
    print("[fe_economy_test] the inn's fee")
    U._stub_store()
    out = Out()
    real = (feworld.send, feworld.self_class_id)
    feworld.send = out
    feworld.self_class_id = lambda a: 1
    S["in_field"] = True
    try:
        # --inn-fees level PINNED: the first pass's per-level rule; the RoD
        # brackets (the default) are pinned in fe_rod_numbers_test
        a = U._args(gold=1000, inn_fees="level", inn_price_per_level=10)
        row = {"name": "Inn_Master", "script": 3105}
        STORE.clear()
        STORE.update({"gold": 500, "class_levels": {"1": 5}})
        steps = feworld.event_script_for(a, row)
        check("level 5 rests FREE (SE flow06)", feworld.inn_fee(a) == 0
              and steps[0] == ("heal",), steps[:2])
        STORE["class_levels"] = {"1": 12, "0": 30}
        check("level 12 (in the character's OWN class) pays 12 x 10 = 120",
              feworld.inn_fee(a) == 120)
        S["player_hp"] = 1
        S["event"] = {"npc": 601, "script": 3105, "pc": 0, "name": "Inn_Master",
                      "steps": feworld.event_script_for(a, row)}
        out.clear()
        feworld.event_step(None, None, 0, False, a)
        check("the rest takes 120 of 500 gold and heals",
              STORE["gold"] == 380 and S["player_hp"] == feworld.player_hp_max(a),
              (STORE.get("gold"), S.get("player_hp")))
        STORE["gold"] = 50
        steps = feworld.event_script_for(a, row)
        check("with 50 gold the innkeeper REFUSES and nothing is charged",
              ("heal",) not in steps and not any(s[0] == "pay" for s in steps)
              and "gold" in steps[0][1] and STORE["gold"] == 50, steps)
    finally:
        feworld.send, feworld.self_class_id = real
        S.pop("event", None)
        S.pop("player_hp", None)


def rings_and_clerk():
    """rings_get / rings_add -- the war worker AWARDS through these."""
    print("[fe_economy_test] Rings and the Prize Clerk")
    U._stub_store()
    STORE.clear()
    a = U._args(ring=1)
    check("rings_get seeds from --ring once", feworld.rings_get(a) == 1
          and STORE["ring"] == 1)
    check("rings_add(3) -> 4, stored", feworld.rings_add(a, 3) == 4
          and STORE["ring"] == 4)
    check("rings_add never goes below 0", feworld.rings_add(a, -10) == 0)
    feworld.rings_add(a, 2)
    line = feworld.event_script_for(a, {"name": "Prize_Clerk", "script": 2113})[0][1]
    check("the Prize Clerk reports the Rings held and where to spend them",
          "2 Rings" in line and "Ring Shop" in line, line)
    STORE.clear()
    b = U._args()
    check("with --ring unset an award is still STORED (served once it is set)",
          feworld.rings_add(b, 5) == 5 and STORE["ring"] == 5)


def add107a(body):
    """A 0x107A add, read the way 0x0503d540 + 0x0503d280 read it:
    (slot, uid, item, count, kind, marker)."""
    r = U.Reader(body)
    assert r.u8() == 1, "an ADD"
    r.u8()
    slot, uid = r.u32(), r.u32()
    assert r.u32() == 2
    no = r.u16()
    assert (r.u32(), r.u32()) == (0, 0)
    r.u32()                                   # group-4 mask
    count = r.u16()
    r.u16(); r.u16()                          # durability max / current
    kind, marker, slot2 = r.u16(), r.u8(), r.u32()
    r.done()
    assert slot2 == slot, "the header slot and [item+0x4cc] agree"
    return slot, uid, no, count, kind, marker


def bank():
    """THE BANK: 40 cells of items and a gold balance, stored per character."""
    print("[fe_economy_test] the bank")
    U._stub_store()
    out = Out()
    real = feworld.send
    feworld.send = out
    S["in_field"] = True
    S.pop("bank_open", None)
    try:
        a = U._args(gold=1000, bag_size=30)
        STORE.clear()
        STORE.update({"items": [[7001, 301, 1], [7002, 4, 1, 3], [7003, 531, 1]],
                      "equip": [[2, 7001]], "gold": 500})
        # no window open: the reply arms deref [app+0xd8] unchecked
        out.clear()
        U._dispatch(a, struct.pack(">HIH", 0x114B, 7003, 1))
        check("a bank request with NO bank window open is not answered at all",
              out.ids() == [] and len(STORE["items"]) == 3, [hex(m) for m in out.ids()])
        # the Bank_Keeper's window step serves the bank BEFORE op 8 builds it
        S["event"] = {"npc": 700, "script": 0, "pc": 1, "name": "Bank_Keeper",
                      "steps": [("text", "x"), ("window", feworld.EV_WIN_BANK, 0),
                                ("window", feworld.EV_WIN_END, 0)]}
        out.clear()
        feworld.event_step(None, None, 0, False, a)
        ids = out.ids()
        check("opening the bank pushes 0x2024 (cells + bank gold) BEFORE the op-8 window",
              0x2024 in ids and ids[-1] == 0x1174 and ids.index(0x2024) < len(ids) - 1,
              [hex(m) for m in ids])
        st = [b for m, _u, b in out.box if m == 0x2024][0]
        r = U.Reader(st)
        check("...[maskA 0x50000000][maskB 0][u32 bank gold 0][u16 40 cells]",
              (r.u32(), r.u32(), r.u32(), r.u16()) == (0x50000000, 0, 0, 40)
              and r.i == len(st), st.hex())
        check("...and marks the window open", S.get("bank_open"))
        # DEPOSIT the wand (the last bag row)
        out.clear()
        U._dispatch(a, struct.pack(">HIH", 0x114B, 7003, 1))
        check("depositing the wand answers 0x2091 LAST, after the pushes",
              out.ids()[-1] == 0x2091 and out.box[-1][2] == b"", [hex(m) for m in out.ids()])
        check("...the stored bank holds it in slot 0, the bag no longer does",
              STORE["bank"] == [[7003, 531, 1, 1, 0]]
              and [r_[0] for r_ in STORE["items"]] == [7001, 7002],
              (STORE.get("bank"), STORE["items"]))
        mv = add107a(out.box[0][2])
        check("...pushed as a 0x107A add of the SAME uid into the kind-1 array",
              mv == (0, 7003, 531, 1, 1, 0xFF), mv)
        # refusals change nothing
        for body, code, what in (
                (struct.pack(">IH", 7001, 1), 15, "a WORN item (15 'Can't deposit equipped')"),
                (struct.pack(">IH", 424242, 1), 9, "a uid the bag lacks (9)"),
                (struct.pack(">IH", 7002, 4), 7, "more than the stack holds (7)")):
            out.clear()
            U._dispatch(a, struct.pack(">H", 0x114B) + body)
            check("depositing %s is refused" % what,
                  out.ids() == [0x2092] and struct.unpack(">I", out.box[0][2])[0] == code
                  and len(STORE["bank"]) == 1 and len(STORE["items"]) == 2,
                  [hex(m) for m in out.ids()])
        # PART of a stack: a new object in the bank, the bag row keeps its uid
        out.clear()
        U._dispatch(a, struct.pack(">HIH", 0x114B, 7002, 2))
        nb = STORE["bank"][-1]
        check("depositing 2 of 3 bread banks a NEW uid (x2) in slot 1, the bag keeps x1",
              nb[1:] == [4, 1, 2, 1] and nb[0] > 7003
              and STORE["items"][1] == [7002, 4, 1, 1], (STORE["bank"], STORE["items"]))
        check("...pushed: the bank add, the bag row re-sent with its new count, OK",
              out.ids() == [0x107A, 0x107A, 0x2091]
              and add107a(out.box[0][2])[4] == 1 and add107a(out.box[1][2])[3:5] == (1, 0),
              [hex(m) for m in out.ids()])
        # a purchase after this must not reuse a banked uid
        check("new_item_uid never reuses a uid sitting in the bank",
              feworld.new_item_uid(a) > max(r_[0] for r_ in STORE["bank"]))
        # a full bank
        out.clear()
        U._dispatch(U._args(gold=1000, bag_size=30, bank_slots=2),
                    struct.pack(">HIH", 0x114B, 7002, 1))
        check("a full bank refuses with 8 'The bank's item slots are full'",
              out.ids() == [0x2092] and struct.unpack(">I", out.box[0][2])[0] == 8
              and len(STORE["bank"]) == 2)
        # WITHDRAW the wand: onto the end of the bag, the same uid as kind 0
        out.clear()
        U._dispatch(a, struct.pack(">HIH", 0x114C, 7003, 1))
        check("withdrawing the wand puts it back at the END of the bag",
              [r_[0] for r_ in STORE["items"]] == [7001, 7002, 7003]
              and [r_[0] for r_ in STORE["bank"]] == [nb[0]],
              (STORE["items"], STORE["bank"]))
        check("...pushed as the same uid re-filed as kind 0 in bag slot 2, then 0x2093",
              out.ids() == [0x107A, 0x2093] and add107a(out.box[0][2])[:5] == (2, 7003, 531, 1, 0),
              [hex(m) for m in out.ids()])
        out.clear()
        U._dispatch(a, struct.pack(">HIH", 0x114C, 7003, 1))
        check("withdrawing what the bank does not hold is refused (9)",
              out.ids() == [0x2094] and struct.unpack(">I", out.box[0][2])[0] == 9)
        out.clear()
        STORE["bag_size"] = 3              # the CHARACTER's grid, not the flag
        U._dispatch(a, struct.pack(">HIH", 0x114C, nb[0], 1))
        check("withdrawing into a full bag is refused (8 'Your item slots are full')",
              out.ids() == [0x2094] and struct.unpack(">I", out.box[0][2])[0] == 8
              and len(STORE["items"]) == 3)
        STORE["bag_size"] = 30
        # GOLD both ways
        out.clear()
        U._dispatch(a, struct.pack(">HI", 0x1149, 200))
        check("depositing 200 of 500 gold: 300 on hand, 200 in the bank, 0x208D",
              STORE["gold"] == 300 and STORE["bank_gold"] == 200
              and out.ids()[-1] == 0x208D and out.ids().count(0x2024) == 2,
              (STORE.get("gold"), STORE.get("bank_gold"), [hex(m) for m in out.ids()]))
        out.clear()
        U._dispatch(a, struct.pack(">HI", 0x1149, 301))
        check("depositing more than is on hand: 0x208E 5, nothing moves",
              out.ids() == [0x208E] and struct.unpack(">I", out.box[0][2])[0] == 5
              and (STORE["gold"], STORE["bank_gold"]) == (300, 200))
        out.clear()
        U._dispatch(a, struct.pack(">HI", 0x114A, 50))
        check("withdrawing 50: 350 on hand, 150 saved, 0x208F",
              (STORE["gold"], STORE["bank_gold"]) == (350, 150) and out.ids()[-1] == 0x208F)
        out.clear()
        U._dispatch(a, struct.pack(">HI", 0x114A, 151))
        check("withdrawing more than is saved: 0x2090 5 'Deposit too low'",
              out.ids() == [0x2090] and struct.unpack(">I", out.box[0][2])[0] == 5
              and STORE["bank_gold"] == 150)
        # --bank off is the old acknowledge-only row
        out.clear()
        U._dispatch(U._args(gold=1000, bank="off"), struct.pack(">HI", 0x114A, 10))
        check("--bank off answers the old header-only OK and moves nothing",
              out.ids() == [0x208F] and STORE["bank_gold"] == 150)
        # the window closes: replies stop
        U._dispatch(a, struct.pack(">HI", 0x2085, 0))
        out.clear()
        U._dispatch(a, struct.pack(">HI", 0x114A, 10))
        check("after the window's 0x2085 close nothing is answered",
              out.ids() == [] and STORE["bank_gold"] == 150)
    finally:
        feworld.send = real
        S.pop("event", None)
        S.pop("bank_open", None)


def main():
    for part in (shops, buying, selling, hunting, inn, rings_and_clerk, bank):
        part()
    if FAILS:
        print("[fe_economy_test] %d FAILED: %s" % (len(FAILS), FAILS))
        sys.exit(1)
    print("[fe_economy_test] OK")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    quiet = "-v" not in sys.argv
    real_print = print
    if quiet:
        # keep feworld's log quiet; the check lines still print
        import builtins
        builtins.print = lambda *a, **k: (real_print(*a, **k)
                                          if a and isinstance(a[0], str)
                                          and (a[0].startswith("  ")
                                               or a[0].startswith("[fe_economy"))
                                          and not a[0].startswith("  [")
                                          and not a[0].startswith("   ") else None)
    main()
