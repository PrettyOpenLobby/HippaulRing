"""Capital staff: role lines, the free weapon, the inn fee."""
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, events, inventory, itemrecords, progression, sess, shops, skilllist, town, wallet, zones

# The capital roster's NON-shop roles and what each says. The five capitals
# share the twenty ROLES and their script SLOTS (band 2 = the town, band 3 =
# the castle; `script // 1000, script % 100`) but NAME their people
# differently (39's Jade/Luke/Mo/Karin are 21's Lesley/Aiando/Domely/Ancel),
# so this is keyed by slot, not by name. SE's dialogue does not ship; slot
# (2, 12) is SE's line (Beinwatt's Aiando, atwiki 10000goku/243 -- the same
# slot in every capital), the rest are OURS. One balloon each: op 3 is a
# BALLOON that acks at once (live 09-05), so a second text would replace the
# first before anyone read it. The MANAGERS (3, 1..3) give the King's message
# and the nation's news -- what SE's own 2006 guide says the Manager is for
# (flow06: "国王からのメッセージや国情報") -- and that news is the campaign's
# own report.
# WARNING: 09-11: two of these lines said things the 2006 game never had -- "the
# war office is here in the castle" (ordinary people could not enter the
# castle: Azurwood's guard Lionel turns them away) and "five callings ...
# cestus and fencer" (RoD had three classes; those two came to FEZ in 2009).
ROLE_LINES = {
    (2, 1): "Welcome to the capital, traveller. The shops line the street, and "
            "the Managers have word from the King and news of how the nation "
            "fares.",
    (2, 12): "Once you've joined a war, you can't join any other until it's "
             "over. Keep that in mind!",
    # 09-11: the Prize Clerk HANDED OUT war rewards (RoD-press, game.watch
    # 20051107); spending Rings was the Ring Shop's job. Our war rewards are
    # credited straight to the wallet, so the clerk reports the balance --
    # see prize_clerk_line(); this is its fallback with no ring wallet.
    (2, 13): "War rewards are paid out here once a campaign is decided. "
             "Spend your Rings at your own class's Ring Shop.",
    (2, 15): "New here? Talk to the trainer about your class, buy a weapon you "
             "can actually hold, and do not go out alone before you know how "
             "to cast.",
    (3, 4): "Three callings: warrior, scout and sorcerer. Your weapon decides "
            "your cast, so carry the one your class can use. Skills come with "
            "levels -- and levels come from the field.",
}
ROLE_WAR_OFFICE = (3, 1)
#: the Prize_Clerk's roster slot (every capital's band-2 offset 13)
ROLE_PRIZE_CLERK = (2, 13)


def prize_clerk_line(args):
    """The Prize Clerk's one balloon: the war rewards already credited (the
    war code pays Rings straight into festore's `ring`, see rings_add), and
    where to spend them. Falls back to ROLE_LINES with no ring wallet."""
    r = wallet.rings_get(args)
    if r is None:
        return ROLE_LINES[ROLE_PRIZE_CLERK]
    return ("Your war rewards are paid in as soon as a campaign is decided -- "
            "you hold %d Ring%s. Spend them at your own class's Ring Shop."
            % (int(r), "" if int(r) == 1 else "s"))
#: every Manager slot gives the King's message + the nation's news
ROLE_MANAGERS = {(3, 1), (3, 2), (3, 3)}

# ---------------------------------------------------------------------------
# KEY: THE KING'S MESSAGE IS RECORDED (2026-10-01, audit B10). Manual p.30:
# "You can only join wars after receiving the king's message" from the
# Manager. The Manager's talk sets KING_MESSAGE_KEY on the stored character
# (festore keeps unknown keys in its `extra` column), and
# king_message_heard(charid) reads it back for the war join.
#
# The same talk hands a new character two books, once (manual p.30 side box:
# "new players get two books from the Manager with basic instructions and
# controls"). STARTER_BOOKS, from the client's own book tables (dat.pak
# fet_bookset_title / fet_bookset_content), both by the 兵士教練協会 (Soldier
# Training Association), both in FE_ITEM_DATA:
#   1850 兵士手帳 "how to move as a soldier": the controls (W/S/A/D, Q/E side
#        steps, Space jump, left click attack/talk, right click pick up,
#        Z target menu, Alt cursor, H control help, Field Out).
#   1841 ＨＰ・Ｐｗブック: the two-volume set of 1838 (HP: what it is, the
#        Regenerate items, using an item) and 1840 (Pw: what it is, C to
#        crouch, Power Pots, the item pockets and F/G).
# CHOSEN: which two. The pick is by content; nothing names the retail pair.
# The 世界学入門 "Tutorial Book" series (1785..1799) would read more like a
# primer, but those item numbers are NOT in this client's FE_ITEM_DATA, and an
# item number the client cannot look up is a crash (shops.py, 2026-09-05 #5).
# 1835 訓練紹介状 (the letter sending you to the Trainer) is the other
# candidate.
# ---------------------------------------------------------------------------
KING_MESSAGE_KEY = "king_message"
STARTER_BOOKS_KEY = "starter_books"
STARTER_BOOKS = (1850, 1841)
#: charids already seen to have heard it (it is never un-heard)
_KING_HEARD = set()


def king_message_record(args):
    """Mark the session character as having heard the King's message. True
    when it is stored (or already was)."""
    charid = sess._SESSION.get("charid")
    if charid is not None and int(charid) in _KING_HEARD:
        return True
    if character._load_char_field(args, KING_MESSAGE_KEY, None):
        ok = True
    else:
        ok = character._store_char_field(args, KING_MESSAGE_KEY, 1)
        print("[feworld]    the King's message: heard by charid %s -- %s"
              % (charid, "stored" if ok else "NOT stored (no stored character)"),
              flush=True)
    if ok and charid is not None:
        _KING_HEARD.add(int(charid))
    return bool(ok)


def king_message_heard(charid):
    """True when the character `charid` (any account) has heard the King's
    message from a Manager. Reads the store; a store fault answers False."""
    try:
        cid = int(charid)
    except (TypeError, ValueError):
        return False
    if cid in _KING_HEARD:
        return True
    try:
        import felobby
        path = felobby._default_store()
        for acct in (felobby.store_accounts(path) or {}):
            for c in felobby.load_roster(path, acct) or []:
                if int(c.get("charid") or -1) == cid:
                    if c.get(KING_MESSAGE_KEY):
                        _KING_HEARD.add(cid)
                        return True
                    return False
    except Exception:                                  # noqa: BLE001
        pass
    return False


def starter_books_due(args):
    """The STARTER_BOOKS this character has not been given yet ([] once they
    have, or under --starter-books off)."""
    if str(getattr(args, "starter_books", "on") or "on") == "off":
        return []
    given = character._load_char_field(args, STARTER_BOOKS_KEY, None) or []
    have = fegamedata.items()
    return [no for no in STARTER_BOOKS
            if no not in given and int(no) in have]


def starter_books_give(conn, outbound, mode, be, args):
    """Put the due starter books in the bag (one push, the free weapon's path)
    and record them as given. A bag without room for them gets nothing and
    nothing is recorded, so the next talk tries again. -> the item numbers."""
    due = starter_books_due(args)
    if not due:
        return []
    old = itemrecords.item_rows(args)
    if len(old) + len(due) > 96:
        print("[feworld]    the Manager's books: the bag is full (%d rows) -- "
              "not given, next talk tries again" % len(old), flush=True)
        return []
    new, uid = list(old), inventory.new_item_uid(args, old)
    for no in due:
        new.append((uid, int(no), 1, 1))
        uid += 1
    character._store_char_field(args, "items", [list(x) for x in new])
    inventory.bag_layout_push(conn, outbound, mode, be, args, old, new, [],
                              "the Manager's starter books %s" % due)
    given = list(character._load_char_field(args, STARTER_BOOKS_KEY, None) or [])
    character._store_char_field(args, STARTER_BOOKS_KEY, given + list(due))
    return due
#: [item table +0x50] slot types a WEAPON goes in: 1/9/10 two-handed (worn
#: 0), 2 one-handed (worn 1) -- the two worn indices the cast gate reads.
WEAPON_SLOT_TYPES = (1, 2, 9, 10)
FREE_WEAPON_GIVE = ("Hm? Hold on. That won't do -- without a weapon you can't fight "
                    "properly. Well, I'll pick one out for you, specially. Don't "
                    "just fight: keep an eye on your own condition, or you won't "
                    "last. Come back if you lose it; I'll always see you right.")
FREE_WEAPON_FULL = ("Hm? Your pack's just about full. Sorry, but sort it out and "
                    "come back.")


def free_weapon_script(args, tf):
    """Kakutas / Aiai / Roland: a weapon for anyone who has none.

    SE's own lines say it (atwiki 243): Beinwatt's Dean -- "the nation hands
    out weapons for free; ask Kakutas, near the weapon shop" -- and Kakutas's
    three states: you have a weapon (a word of advice), you have none and room
    in your pack (he picks one out for you), your pack is full (sort it
    first). The weapon is your class's own starting weapon from SE's
    fet_initialize_equip, so it is always one your class can use."""
    line = tf.get("line_en") or "Good day."
    end = ("window", events.EV_WIN_END, 0)
    rows = itemrecords.item_rows(args)
    slot = lambda no: int(fegamedata.items().get(int(no), {}).get("slot") or 0)
    if any(slot(no) in WEAPON_SLOT_TYPES for _u, no, _f, _c in rows):
        return [("text", line), end]
    weapon = next((n for n in fegamedata.starting_gear(skilllist.self_class_id(args))
                   if slot(n) in WEAPON_SLOT_TYPES), None)
    if weapon is None:
        return [("text", line), end]
    if len(rows) + 1 > 96:
        return [("text", FREE_WEAPON_FULL), end]
    return [("give", weapon), ("text", FREE_WEAPON_GIVE), end]


def role_slot(row):
    """(band, offset) of a town-file row's roster script, or None."""
    try:
        s = int(row.get("script") or 0)
    except (TypeError, ValueError):
        return None
    return (s // 1000, s % 100) if s else None

#: where the castle staff stand, relative to the castle's exit guide (the one
#: row the admin placed by standing there) -- a loose arc facing the door.
STAFF_OFFSETS = [(-6.0, 4.0), (-3.0, 6.5), (0.0, 7.5), (3.0, 6.5), (6.0, 4.0),
                 (-8.0, 0.0), (8.0, 0.0), (-5.0, -3.0), (5.0, -3.0)]
CAPITAL_STAFF_ROOM = 15


def capital_staff_fill(args):
    """Put the capital roster's non-shop roles in the castle (room 15), once.

    Town NPC POSITIONS ship nowhere (fe-capital-roster-ships-positions-do-not),
    and `!dress` only knows the shops (their minimap icons). The castle staff
    -- Jade, Luke, Mo, the three managers, the trainer, the prize clerk, the
    bank keeper -- had nowhere to stand and said 'Good day.'. Anchor: the
    castle's exit guide, a row the admin placed at their own feet, so its
    y is the floor. Idempotent: a role already there (by name or script) is
    skipped, and every row is an ordinary town NPC (`!remove` takes it back).
    Refuses rather than guesses when the castle has no anchor row."""
    if getattr(args, "capital_staff", "off") != "on":
        return 0
    key = "room:%d" % CAPITAL_STAFF_ROOM
    have = town.town_get(args, key, "npcs")
    if not have:
        print("[feworld]    --capital-staff: %s has no NPC row to anchor on -- "
              "stand in the castle and `!npc` one (the exit guide) first"
              % key, flush=True)
        return 0
    anchor = have[0]
    have_n = {str(r.get("name") or "") for r in have}
    have_s = {int(r.get("script") or 0) for r in have}
    # THIS deployment's capital (--capital ISLAND:GID): the five rosters name
    # their people differently, and the castle room is one shared key.
    caps = [g for gids in zones.parse_capitals(getattr(args, "capital", "")).values()
            for g in gids]
    if not caps:
        print("[feworld]    --capital-staff: no --capital, so no roster to "
              "place", flush=True)
        return 0
    roster = [r for r in fegamedata.capital_roster(caps[0])
              if shops.shop_type_for(args, r["name"]) is None
              and r["name"] != "Inn_Master"]
    placed = 0
    for i, role in enumerate(roster):
        if role["name"] in have_n or role["script"] in have_s:
            continue
        dx, dz = STAFF_OFFSETS[i % len(STAFF_OFFSETS)]
        rec = {"model": role["modeltype"], "kind": 1, "level": 1,
               "x": round(float(anchor.get("x", 0)) + dx, 1),
               "y": round(float(anchor.get("y", 0)), 2),
               "z": round(float(anchor.get("z", 0)) + dz, 1),
               "yaw": int(anchor.get("yaw", 0) or 0),
               "name": role["name"], "script": role["script"]}
        if town.town_add(args, key, "npcs", rec):
            placed += 1
    print("[feworld] --capital-staff: %d of %d castle roles placed in %s "
          "(anchor %s at (%.1f, %.1f))"
          % (placed, len(roster), key, anchor.get("name"),
             float(anchor.get("x", 0)), float(anchor.get("z", 0))), flush=True)
    return placed


def char_level(args):
    """The session character's level in its OWN class, at least 1 -- the
    level system's own reading (self_level: the stored class-level table,
    seeded from --class-levels once), so the inn's fee and the equip gate can
    never disagree about it."""
    return max(1, int(progression.self_level(args) or 0))

def inn_fee(args):
    """The Inn's price for this character, per stay.

    `--inn-fees rod` (default, 2026-09-11 second pass) = INN_FEES_ROD: free
    to Lv5, 10 G at Lv6-10, 20 G at Lv11-20, 50 G at Lv21-40 -- fewiki
    Guide/Q&A 2006-05-26 「宿屋利用料はレベル5まで無料以降一回につき10です。
    LV11から20です。LV21から50です。」, the same brackets in the 2007-02 メモ.
    `level` = level x --inn-price-per-level above Lv5 (OURS, the first
    pass); `G:LO-HI,...` = explicit brackets (a level in none rests free,
    a level past the last bracket pays the top one)."""
    spec = str(getattr(args, "inn_fees", DEFAULT_INN_FEES) or "").strip()
    lv = char_level(args)
    if spec == "level":
        per = int(getattr(args, "inn_price_per_level", 10) or 0)
        return 0 if per <= 0 or lv <= INN_FREE_LEVEL else lv * per
    rows = parse_level_brackets(INN_FEES_ROD if spec == "rod" else spec)
    for fee, lo, hi in rows:
        if lo <= lv <= hi:
            return max(0, fee)
    top = max(rows, key=lambda r: r[2]) if rows else None
    return max(0, top[0]) if top and lv > top[2] else 0


def parse_level_brackets(spec):
    """'V:LO-HI,...' -> [(V, LO, HI)] (the --sp-per-level shape)."""
    out = []
    for part in str(spec or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            v, rng = part.split(":")
            lo, _, hi = rng.partition("-")
            out.append((int(v), int(lo), int(hi or lo)))
        except ValueError:
            continue
    return out


#: fewiki Guide/Q&A (RoD, 2006-05-26): the inn's fee per stay by level
INN_FEES_ROD = "0:1-5,10:6-10,20:11-20,50:21-40"
DEFAULT_INN_FEES = "rod"


#: SE's 2006 guide (flow06): 「レベル5までは無料」 -- the Inn is free to Lv5.
INN_FREE_LEVEL = 5
