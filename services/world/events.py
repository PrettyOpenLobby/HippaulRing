"""The event VM: NPC talk, windows, quests, event scripts and steps, goto and room exits."""
import struct
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import bank, campaignview, character, death, inventory, itemrecords, movement, progression, sess, shops, spawns, staff, wallet, wire, zones

EV_TEXT, EV_TEXT2, EV_WINDOW, EV_MENU, EV_U32, EV_FADE = 3, 6, 8, 0x107, 9, 0x109
EV_WIN_END, EV_WIN_DIALOG, EV_WIN_BANK = 0x13, 0x14, 0x18
EV_TEXT_MAX = 0x250
#: the client's own line break inside a talk balloon: its text layout
#: (0x05172379) compares each '<'-token against "<br/>" (0x052de28c) and
#: starts a new line -- the same tag dat.pak's multi-line strings use.
TALK_BREAK = "<br/>"


def _talk_cols(w):
    return sum(2 if len(c.encode("cp932", "replace")) > 1 else 1 for c in w)


def _talk_lines(t, width):
    """Greedy word wrap of `t` into lines of at most `width` columns (a
    full-width character counts 2 and may break anywhere)."""
    cols = _talk_cols
    toks, word = [], ""
    for ch in t:
        wide = len(ch.encode("cp932", "replace")) > 1
        if ch.isspace() or wide:
            if word:
                toks.append(word)
                word = ""
            toks.append(ch)
        else:
            word += ch
    if word:
        toks.append(word)
    lines, cur = [], ""
    for tok in toks:
        if tok.isspace():
            if cur:
                cur += tok          # as written: a full-width space stays one
            continue
        while cols(tok) > width:            # a word wider than a whole line
            room = max(1, width - cols(cur.rstrip()) - (1 if cur.strip() else 0))
            if cur.strip() and room < 4:
                lines.append(cur.rstrip())
                cur = ""
                continue
            head = ""
            for c in tok:
                if cols(head + c) > (room if cur.strip() else width):
                    break
                head += c
            lines.append((cur + head).rstrip())
            cur, tok = "", tok[len(head):]
        if cur.strip() and cols(cur + tok) > width:
            lines.append(cur.rstrip())
            cur = ""
        cur += tok
    if cur.strip():
        lines.append(cur.rstrip())
    return lines


def talk_wrap(text, width, lines=4, max_width=140):
    """Lay a talk line out for the balloon, which does neither of the two
    things a text box usually does. Both MEASURED LIVE 2026-09-11 with test
    NPCs (screenshots):

    * it never WRAPS, and it is a fixed FOUR lines tall -- a fifth line is
      drawn below its bottom edge and clipped. So a line is broken with the
      client's own <br/> (its layout at 0x05172379 honours it), into at most
      `lines` lines: start at `width` columns (or wider, so the text fits in
      `lines`), and widen by 4 until it does, up to `max_width`.
    * it sizes its WIDTH to the text, a few percent SHORT: a 68-column line
      drew its last word past the right border -- and the same line with six
      trailing spaces sat inside it. So every line gets trailing spaces,
      ~8% of the widest line (at least 3).

    A full-width (cp932 double-byte) character counts two columns and may
    break anywhere, so Japanese wraps too. A line that already carries <br/>
    keeps the author's breaks (it is only padded). width <= 0 = off."""
    t = str(text)
    if width <= 0:
        return t
    if TALK_BREAK in t:
        out = t.split(TALK_BREAK)
    else:
        w = max(int(width), -(-_talk_cols(t) // max(1, int(lines))))
        out = _talk_lines(t, w)
        while len(out) > lines and w < max_width:
            w += 4
            out = _talk_lines(t, w)
    widest = max((_talk_cols(x) for x in out), default=0)
    pad = " " * max(3, -(-widest * 8 // 100))
    return TALK_BREAK.join(x.rstrip() + pad for x in out)


def ev_body(evt, op, *fields):
    """[u32 event][u16 opcode] + the opcode's fields, the way 0x5173a70 reads
    them. Text is cp932 with a u16 length and NO terminator (0x5045b10)."""
    body = struct.pack(">IH", evt & 0xFFFFFFFF, op & 0xFFFF)
    if op in (EV_TEXT, EV_TEXT2):
        raw = str(fields[0]).encode("cp932", "replace")[:EV_TEXT_MAX]
        body += struct.pack(">H", len(raw)) + raw
    elif op == EV_WINDOW:
        body += struct.pack(">HI", fields[0] & 0xFFFF, fields[1] & 0xFFFFFFFF)
    elif op == EV_MENU:
        prompt = str(fields[0]).encode("cp932", "replace")[:EV_TEXT_MAX]
        opts = list(fields[1])
        body += struct.pack(">I", len(prompt)) + prompt + struct.pack(">I", len(opts))
        for i, o in enumerate(opts):
            raw = str(o).encode("cp932", "replace")[:EV_TEXT_MAX]
            body += struct.pack(">II", i, len(raw)) + raw
    elif op in (EV_U32, EV_FADE):
        body += struct.pack(">I", fields[0] & 0xFFFFFFFF)
    return body


# ---------------------------------------------------------------------------
# KEY: QUESTS (2026-09-12). This client has NO quest system: it predates SE's
# 2006-08-03 quest log (PE stamp 2005-12-19), carries no quest string, table or
# message (researched 2026-09-12). In
# 2006 a quest was a server-driven CONVERSATION on the event VM that already
# runs our shops -- SE's guide (flow10, 2006-03-17): capital NPCs ask for an
# item ("bring me cheese") and pay gold for it. The givers and rewards are the
# 2006 fewiki Quest page's (s_fv_quest.txt, 2006-03-17); the LINES are OURS
# (the cheese givers' words did not survive; Bakin/Roil's did, atwiki 243).
#
# The one new VM op is the CHOICE MENU (0x107, never sent live before). Its
# pick returns as 0x20A8 [u32 a][u16 0x107][u32 c], c = the option's own u32
# (0x05172c46 / 0x0517302c; that c is exactly what we sent is INFERRED -- we
# send each option's index AS its value, so value-or-index reads the same).
# Anything else -- Esc, an unexpected ack -- takes the 'cancel' branch: never
# a reward for anything but a clear Yes, and never a hang.
#
# The bag change and the gold are DEFERRED until the conversation has closed
# (event_after, after 0x1175): whether a 0x107A REMOVE is safe inside event
# mode is untested, and the inn's live 'pay' only ever touched the wallet.
#
# Repeatable with a COOLDOWN per character (design note 2026-09-12: "wouldn't
# quests have like a 'you can only do this once' and/or a cooldown timer ...
# to prevent cheese farming"). 2006 Cheese was 何回でも可能 (any number of
# times) -- its supply was monster drops; ours can be bought at 50 G, so the
# default is one hand-in per in-game day (40 real minutes). CHOSEN.
# ---------------------------------------------------------------------------
QUEST_DAY_S = 2400          # one in-game day (0x249f00 ms), see the calendar

QUESTS = {
    "cheese": {
        "givers": (115, 315, 515, 715, 915),   # Cassius Alethea Wanda Norma Bettina
        "item": 7, "gold": 200, "repeat": "cooldown", "cooldown": QUEST_DAY_S,
        "offer": "Oh, a soldier! Might I trouble you? I've a terrible craving "
                 "for cheese and not a crumb in the house.",
        "prompt": "Will you bring me some cheese?",
        "accept": "Wonderful! Bring me a piece of cheese and I'll pay you "
                  "200 gold for it.",
        "refuse": "Oh... well, if you change your mind, you know where I am.",
        "remind": "Any luck finding cheese? I'll pay 200 gold for a piece.",
        "ask": "Is that cheese I smell? Oh, it is!",
        "hand": "Give it to me for 200 gold?",
        "thanks": "Thank you, thank you! Here's your 200 gold, as promised.",
        "later": "No? Oh, please -- if you change your mind...",
        "rest": "I've had my fill for today, thank you. Come back another "
                "day -- I'm sure I'll be hungry again.",
    },
    "goblin_book": {
        "givers": (137, 340, 536, 738, 936),   # Naonz Bakin Lito Pican Roil
        "item": 1836, "gold": 50, "repeat": "cooldown", "cooldown": QUEST_DAY_S,
        "offer": "Hey there, soldier, a small favour... I'm a bit of a reader, "
                 "and I'm after a Goblin Book.",
        "prompt": "Will you find me a Goblin Book?",
        "accept": "If you come by a Goblin Book, bring it here. I'll buy it "
                  "for 50 gold.",
        "refuse": "Sob... ah well.",
        "remind": "If you have a Goblin Book, would you let me have it? I'll "
                  "buy it.",
        "ask": "...! That's a Goblin Book!",
        "hand": "Give it to me for 50 gold?",
        "thanks": "Thanks! Here, your reward. Still, it isn't enough -- bring "
                  "me more if you find them.",
        "later": "Please...",
        "rest": "I'm still reading the last one. Come back another day.",
    },
}
QUEST_BY_GIVER = {s: q for q, d in QUESTS.items() for s in d["givers"]}
QUEST_END = ("window", EV_WIN_END, 0)


def quest_state(args):
    """{quest id: {"on": bool, "n": hand-ins, "last": epoch s}} off the
    stored character (_store_char_field 'quests')."""
    st = character._load_char_field(args, "quests", None)
    return dict(st) if isinstance(st, dict) else {}


def quest_cooldown(args, q):
    over = int(getattr(args, "quest_cooldown_secs", -1))
    return over if over >= 0 else int(QUESTS[q].get("cooldown", 0))


def quest_script_for(args, row, now=None):
    """The conversation a quest giver plays for THIS character, or None when
    the row gives no quest (or --quests off)."""
    if str(getattr(args, "quests", "on") or "on") != "on":
        return None
    try:
        q = QUEST_BY_GIVER.get(int(row.get("script") or 0))
    except (TypeError, ValueError):
        return None
    if q is None:
        return None
    d, st = QUESTS[q], quest_state(args).get(q, {})
    if not st.get("on"):
        return [("text", d["offer"]),
                ("menu", d["prompt"], ["Yes", "No"],
                 {0: [("qset", q, "accept"), ("text", d["accept"]), QUEST_END],
                  1: [("text", d["refuse"]), QUEST_END],
                  "cancel": [("text", d["refuse"]), QUEST_END]})]
    if d.get("repeat") == "once" and int(st.get("n", 0) or 0) > 0:
        return [("text", d["rest"]), QUEST_END]
    if not any(int(r[1]) == int(d["item"]) for r in itemrecords.item_rows(args)):
        return [("text", d["remind"]), QUEST_END]
    now = time.time() if now is None else now
    wait = float(st.get("last", 0) or 0) + quest_cooldown(args, q) - now
    if wait > 0:
        return [("text", d["rest"]), QUEST_END]
    return [("text", d["ask"]),
            ("menu", d["hand"], ["Yes", "No"],
             {0: [("take", d["item"], q), ("credit", d["gold"], q),
                  ("qset", q, "done"), ("text", d["thanks"]), QUEST_END],
              1: [("text", d["later"]), QUEST_END],
              "cancel": [("text", d["later"]), QUEST_END]})]


def event_menu_pick(ev, a, b, c):
    """Splice the branch the player picked into the running event. Only a
    real menu reply (b == 0x107) naming an option we offered takes it; any
    other ack while a menu is up is the 'cancel' branch."""
    branches = ev.pop("await_menu", None)
    if branches is None:
        return None
    key = int(c) if (int(b) == EV_MENU and int(c) in branches) else "cancel"
    rest = branches.get(key) or branches.get("cancel") or [QUEST_END]
    ev["steps"] = ev["steps"][:ev["pc"]] + list(rest)
    print("[feworld]    menu pick a=%d b=0x%X c=%d -> branch %r (%d step(s))%s"
          % (a, b, c, key, len(rest),
             "" if int(b) == EV_MENU else " -- not a menu reply (0x107): "
             "treated as CANCEL"), flush=True)
    return key


def event_after(conn, outbound, mode, be, args, ev):
    """Run the steps a conversation deferred until it CLOSED: take an item,
    credit gold, update a quest. A take that finds nothing (the item left
    the bag mid-conversation) stops the rest -- no reward without it."""
    todo = list((ev or {}).get("after") or [])
    for step in todo:
        kind = step[0]
        if kind == "take":
            item, q = int(step[1]), step[2]
            rows = itemrecords.item_rows(args)
            hit = next((i for i, r in enumerate(rows) if int(r[1]) == item), None)
            if hit is None:
                print("[feworld]    quest %s: item %d is no longer in the bag "
                      "-- nothing taken, NO reward" % (q, item), flush=True)
                return False
            uid, no, flag, count = rows[hit]
            new, gone = list(rows), []
            if int(count) > 1:
                new[hit] = (uid, no, flag, int(count) - 1)
            else:
                del new[hit]
                gone = [(uid, hit)]
            character._store_char_field(args, "items", [list(x) for x in new])
            inventory.bag_layout_push(conn, outbound, mode, be, args, rows, new, gone,
                            "quest %s hand-in" % q)
        elif kind == "credit":
            wallet.wallet_credit(conn, outbound, mode, be, args, gold=int(step[1]),
                          why="quest %s reward" % step[2])
        elif kind == "qset":
            q, what = step[1], step[2]
            st = quest_state(args)
            cur = dict(st.get(q, {}))
            if what == "accept":
                cur["on"] = True
            elif what == "done":
                cur["on"] = True
                cur["n"] = int(cur.get("n", 0) or 0) + 1
                cur["last"] = int(time.time())
            st[q] = cur
            character._store_char_field(args, "quests", st)
            print("[feworld]    quest %s: %s -> %r" % (q, what, cur), flush=True)
    return True


def event_script_for(args, row):
    """The conversation a town-file NPC row plays, as a list of steps:
    ("text", s) | ("window", type, arg) | ("menu", prompt, [opts])."""
    name = str(row.get("name") or "")
    greet = getattr(args, "event_greeting", None)
    # WARNING: THE NAME GUARD COMES AFTER THE OVERRIDE, and it used to come before.
    # A row's dat.pak type NAME is what picks a shop or a bank script, so a
    # nameless row has nothing to play -- but an `event=` override is exactly
    # the case where the name does not matter, and returning [] first meant a
    # hand-placed guard with `event=say:...;goto:15` silently said nothing.
    # Found 2026-09-06 while wiring the castle gate: `!npc` fills the name from
    # the modeltype, so it is usually non-empty by luck, and any model the type
    # table does not name would have failed with no message at all.
    if not name and not row.get("event"):
        return []
    # A row-level OVERRIDE: `"event": "goto:15"` (a room), `"goto:39"` (a
    # field), `"shop:2"`, `"bank"`, `"say:Hello there"`; steps separated by
    # `;`. This is how the castle gets a door: a guard at the gate whose
    # conversation ends in the transition the street doors use (0x1166).
    # Set with `!town set npcs IDX event=goto:15`.
    if row.get("event"):
        steps = []
        for part in str(row["event"]).split(";"):
            kind, _, val = part.strip().partition(":")
            if kind == "say":
                steps.append(("text", val))
            elif kind == "goto":
                try:
                    tgt = int(val, 0)
                except ValueError:
                    continue
                steps.append(("goto", zones.ROOM_AREA_BASE + tgt if tgt < 0x100 and tgt in zones.ROOM_INDEX_WINDOW else tgt))
            elif kind == "shop":
                steps.append(("window", int(val or 1, 0), int(row.get("script") or 0)))
            elif kind == "bank":
                steps.append(("window", EV_WIN_BANK, 0))
            elif kind == "end":
                steps.append(("window", EV_WIN_END, 0))
        if steps and steps[0][0] != "text":
            steps.insert(0, ("text", greet or "..."))
        if steps and steps[-1][0] == "window" and steps[-1][1] != EV_WIN_END:
            steps.append(("window", EV_WIN_END, 0))
        return steps
    # A shop/bank script ENDS with the END window (op 8 type 0x13): the
    # client clears its own talk mode and sends 0x2099 -> 0x1152 -- the path
    # the tavern proved live. WARNING: LIVE 2026-09-05 #1 and #4: answering the
    # shop's close ack with 0x1172 instead left the client STUCK both times.
    # A TOWNSFOLK row (script 1xx..9xx) says its own line -- SE's words from
    # atwiki 243, in our English -- whatever its row is named.
    # a QUEST GIVER plays its quest for this character (quest_script_for)
    quest = quest_script_for(args, row)
    if quest is not None:
        return quest
    tf = fegamedata.townsfolk_by_script(row.get("script"))
    if tf is not None:
        if tf["kind"] == "weapon":
            return staff.free_weapon_script(args, tf)
        return [("text", greet or tf["line_en"] or "Good day."),
                ("window", EV_WIN_END, 0)]
    shop = shops.shop_type_for(args, name)
    if shop is not None:
        return [("text", greet or "Welcome. Have a look."),
                ("window", shop, int(row.get("script") or 0)),
                ("window", EV_WIN_END, 0)]
    if name == "Bank_Keeper":
        return [("text", greet or "Welcome to the bank."),
                ("window", EV_WIN_BANK, int(row.get("script") or 0)),
                ("window", EV_WIN_END, 0)]
    if name == "Inn_Master":
        # SE's 2006 guide (flow06): the inn restores HP and Pw in full -- free
        # to level 5, then priced by level. The brackets are fewiki's 2006
        # Q&A (inn_fee, --inn-fees rod): 10 / 20 / 50 G.
        fee = staff.inn_fee(args)
        if fee and wallet.wallet_short(args, fee):
            return [("text", greet or "A room is %d gold at your level, "
                                      "traveller, and your purse won't cover "
                                      "it. Come back when it will." % fee),
                    ("window", EV_WIN_END, 0)]
        line = ("Welcome to the inn. That's %d gold -- rest a while, you're "
                "back to full strength." % fee if fee else
                "Welcome to the inn. Rest a while -- you're back to full "
                "strength.")
        return ([("pay", fee)] if fee else []) + [
                ("heal",),
                ("text", greet or line),
                ("window", EV_WIN_END, 0)]
    slot = staff.role_slot(row)
    if slot in staff.ROLE_MANAGERS:
        # 2026-10-01 (audit B10): the talk is RECORDED (king_heard, after the
        # balloon went out) -- the war join reads it -- and a new character
        # gets the two starter books once (manual p.30; staff.STARTER_BOOKS).
        books = staff.starter_books_due(args)
        line = greet or ("A message from the King. "
                         + campaignview.campaign_report(args))
        if books and not greet:
            line += " And take these two books -- read them before you fight."
        # the balloon stays FIRST (every caller reads steps[0] as the line)
        return ([("text", line), ("king_heard",)]
                + ([("starter_books",)] if books else [])
                + [("window", EV_WIN_END, 0)])
    if slot == staff.ROLE_PRIZE_CLERK:
        return [("text", greet or staff.prize_clerk_line(args)),
                ("window", EV_WIN_END, 0)]
    if slot in staff.ROLE_LINES:
        return [("text", greet or staff.ROLE_LINES[slot]), ("window", EV_WIN_END, 0)]
    return [("text", greet or "Good day."), ("window", EV_WIN_END, 0)]


def event_step(conn, outbound, mode, be, args):
    """Send the current command of the session's event, or END it."""
    ev = sess._SESSION.get("event")
    if not ev:
        return
    pc = ev["pc"]
    if pc >= len(ev["steps"]):
        wire.send(conn, outbound, wire.inner_msg(0x1175, b"", ev["npc"]),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        print("[feworld] -> 0x30 inner 0x1175 event END (%s, %d step(s) done)"
              % (ev.get("name"), pc), flush=True)
        sess._SESSION.pop("event", None)
        # what the conversation deferred (a quest's take / gold / state)
        event_after(conn, outbound, mode, be, args, ev)
        # A conversation ends TWO ways and both have to offer the room exit.
        # This is the one where the script simply runs out (a `say`-only NPC);
        # the other is the END window -> 0x2099 -> conversation_close. Only
        # that second one called the exit until 2026-09-06, which would have
        # made a plain talking NPC inside a room a dead end -- the same trap
        # the tavern sprang, one step further in.
        room_exit_push(conn, outbound, mode, be, args, "the script ran out")
        return
    step = ev["steps"][pc]
    ev["pc"] = pc + 1
    if step[0] in ("take", "credit", "qset"):
        # a quest's bag/wallet/state change waits for the CLOSE (event_after)
        ev.setdefault("after", []).append(step)
        return event_step(conn, outbound, mode, be, args)
    # WARNING: LIVE 2026-09-05 #4: the head u32 of every 0x1174 is the NPC UNIT id,
    # not the script. The interpreter (0x5173a70) reads it into a local and
    # hands its ADDRESS to every op handler unchecked; the op-8 handler
    # passes it as the window ctor's argument (0x51746bb: [esp+0x20] = arg
    # 1), which lands at [win+0x84] -- the unit the shop polices and the
    # (id, 8, 0) it acks with. We sent the script id (2102) there for four
    # attempts; the window's own u32 argument never reached +0x84 at all
    # (the ack still said 2102 after it carried 500).
    evt = ev["npc"]
    if step[0] == "king_heard":
        staff.king_message_record(args)
        return event_step(conn, outbound, mode, be, args)
    if step[0] == "starter_books":
        staff.starter_books_give(conn, outbound, mode, be, args)
        return event_step(conn, outbound, mode, be, args)
    if step[0] == "give":
        # the free weapon: into the bag, pushed, then straight on to the line
        old = itemrecords.item_rows(args)
        nxt = inventory.new_item_uid(args, old)                   # bag AND bank uids
        new = old + [(nxt, int(step[1]), 1, 1)]
        character._store_char_field(args, "items", [list(x) for x in new])
        inventory.bag_layout_push(conn, outbound, mode, be, args, old, new, [],
                        "free weapon %d from %s" % (int(step[1]), ev.get("name")))
        return event_step(conn, outbound, mode, be, args)
    if step[0] == "pay":
        # the inn's fee: charged when the step RUNS, not when the script was
        # written at the click, so the wallet read is the current one. A
        # wallet that went short in between skips the rest (the heal).
        ok, _short = wallet.wallet_charge(conn, outbound, mode, be, args, int(step[1]),
                                   0, "the inn (%s)" % ev.get("name"))
        if not ok:
            ev["steps"] = ev["steps"][:ev["pc"]] + [
                ("text", "Your purse won't cover a room. Come back when it "
                         "will."), ("window", EV_WIN_END, 0)]
        return event_step(conn, outbound, mode, be, args)
    if step[0] == "heal":
        hp = death.player_hp_max(args)
        sess._SESSION["player_hp"] = hp
        death.player_hp_push(conn, outbound, mode, be, args, hp)
        print("[feworld]    the inn: HP %d/%d" % (hp, hp), flush=True)
        # Pw too: manual p.30, the inn fully restores HP and Power (audit
        # C18). Skipped under --pw-cost off, where nothing ever lowers it.
        if str(getattr(args, "pw_cost", progression.DEFAULT_PW_COST)) != "off":
            progression.pw_push(conn, outbound, mode, be, args,
                                progression.pw_max(args), "the inn: Pw to full")
        return event_step(conn, outbound, mode, be, args)
    if step[0] == "goto":
        # end the conversation first (0x1175 releases event mode), then move
        wire.send(conn, outbound, wire.inner_msg(0x1175, b"", ev["npc"]),
             mode, be, args.seq_mode == "echo", args.world_prefix)
        sess._SESSION.pop("event", None)
        print("[feworld] -> 0x30 inner 0x1175 event END, then the transition:",
              flush=True)
        goto_push(conn, outbound, mode, be, args, int(step[1]))
        return
    if step[0] == "text":
        body = ev_body(evt, EV_TEXT,
                       talk_wrap(step[1], int(getattr(args, "talk_wrap", 48) or 0),
                                 int(getattr(args, "talk_lines", 4) or 4)))
    elif step[0] == "window":
        # the op's own u32 is NOT what the shop polices (see `evt` above);
        # it goes out as the unit id too, and step[2] (the row's script id)
        # is kept for the log only
        body = ev_body(evt, EV_WINDOW, step[1], ev["npc"])
        if int(step[1]) == EV_WIN_BANK:
            # the bank window sizes itself from [unit+0x4fe] IN ITS CTOR and
            # lists the kind-1 array: both have to be there before op 8 lands
            bank.bank_open_push(conn, outbound, mode, be, args)
        # a SHOP/BANK window owns the conversation from here: its own
        # requests follow (0x2073 -> 0x1102, 0x204A..); its CLOSE is 0x2085
        # + a 0x20A8 (npc, 8, 0) ack, which cues the next command (END).
        # The END window (0x13) is the client closing itself -- 0x2099 follows.
        ev["in_window"] = True
        if 1 <= int(step[1]) <= 4:
            kind = shops.shop_kind_for(args, ev.get("name"), int(step[1]))
            cls = shops.shop_class_for(ev.get("name"))
            stock = shops.shop_stock(args, kind, cls)
            sess._SESSION["shop"] = {"npc": ev["npc"], "kind": kind, "class": cls,
                                "type": int(step[1]), "stock": stock}
            print("[feworld]    shop context: %s%s, %d item(s) in %d page(s) "
                  "(fet_items slot types %s; --shop-prices %s)"
                  % (kind, "" if cls is None else " for class %d" % cls,
                     len(stock), shops.shop_pages(args, stock),
                     shops.SHOP_SLOTS.get(kind), getattr(args, "shop_prices", "table")),
                  flush=True)
    elif step[0] == "menu":
        body = ev_body(evt, EV_MENU, step[1], step[2])
        if len(step) > 3:
            # the next ack is the pick; event_menu_pick splices its branch
            ev["await_menu"] = step[3]
    else:
        body = ev_body(evt, EV_U32, int(step[1]))
    wire.send(conn, outbound, wire.inner_msg(0x1174, body, ev["npc"]), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1174 event step %d/%d for %s (unit %d): %s "
          "-- wait for the window's 0x20A8 ack"
          % (pc + 1, len(ev["steps"]), ev.get("name"), ev["npc"],
             step if step[0] != "text" else ("text", step[1][:40])), flush=True)


def conversation_close(conn, outbound, mode, be, args, f, how):
    """0x2099 -> 0x1152 [u32 npc][cstr farewell]: the conversation CLOSE.

    Sent by opcode 8 type 0x13 (0x05174560) after clearing the talk mode;
    it registers 0x1152 OK / 0x1153 NG in the pending table. WARNING: LIVE
    2026-09-05 #7: the END op writes the request through a STACK-LOCAL
    stream object (`lea ecx,[esp+8]` at 0x517457c, not the session stream
    0x5338350), so it leaves the client as a BARE OUTER frame, id 0x2099,
    outside the 0x30 envelope. We logged it as OUTER and never answered;
    the pending entry drew "waiting..." after every Cancel. Both arrivals
    land here. 0x1152's arm (0x0505397d) reads [u32][cstr]."""
    ev = sess._SESSION.pop("event", None)
    sess._SESSION.pop("shop", None)
    sess._SESSION.pop("bank_open", None)
    npc = ev["npc"] if ev else (struct.unpack_from(">I", f, 0)[0]
                                if len(f) >= 4 else 0)
    wire.send(conn, outbound,
         wire.inner_msg(0x1152, struct.pack(">I", npc) + b"\x00", wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld]    0x2099 conversation close (%s) body=%s -> 0x1152 "
          "[npc %d][empty]; event cleared" % (how, f.hex(), npc), flush=True)
    # KEY: LIVE 2026-09-05 #8/#9: the EVENT END is **0x1175**. The dispatcher's
    # table (0x5057650 via 0x505768c, base 0x1165) maps 0x1172 -> 0x5055f70,
    # the arm that ARMS event mode (scene flag 0x4000, byte 0x53378f3 = 1,
    # the manager's modal bit, [mgr+0x94] slot 32) -- i.e. START OK locks
    # the player for the conversation -- and 0x1175 -> 0x5056039, the arm
    # that releases all of it (and the talk-mode bit). Every "stuck" was us
    # sending 0x1172 at the END: re-locking. 0x1175 reads nothing.
    wire.send(conn, outbound, wire.inner_msg(0x1175, b"", wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1175 event END (header-only, after the "
          "close OK): releases event mode", flush=True)
    # what the conversation deferred (a quest's take / gold / state) -- now
    # that event mode is released
    event_after(conn, outbound, mode, be, args, ev)
    room_exit_push(conn, outbound, mode, be, args, "the conversation ended")


def room_exit_push(conn, outbound, mode, be, args, why):
    """Walk the player OUT of a room, because the client will not.

    WARNING: REPORTED LIVE 2026-09-06: "I'm in the tavern, talked to the person, it
    gave me dialogue but then closed. Also, can't leave the tavern by any means
    (door doesn't work, gating out doesn't work)." The session then idled out.

    **A room has no exit of its own and that is measured, twice.** Inside, the
    client sends NOTHING at the room's door -- the 0x2017/0x2000 area -1 pair
    seen on 2026-09-04 was the war screen's "Army info" leave when its 60 s
    countdown ran out, not a door. A room is a scene we put the player in
    (0x1166 -> area ROOM_AREA_BASE+idx), so getting back out is the SERVER's
    move, and until now the only one that made it was a hand-typed `!goto` in
    the gm command file. A player without that is trapped until the read window
    times out.

    So the conversation END doubles as the exit: 0x1166 unsolicited, back to the
    field the door came from (`outside`, remembered when the door was taken).
    That is the same message and the same arm the door uses -- the arm
    (0x05056089) registers no request of its own and just acts on A.

    WARNING: Only when we are actually IN a room. Outdoors this must do nothing: A
    equal to the current field with B == -1 is a same-field WARP, so an
    unguarded call would teleport the player to the spawn point every time they
    finished talking to anybody.
    """
    if getattr(args, "room_exit", "conversation") == "off":
        print("[feworld]    --room-exit off: staying in room %s (%s). The "
              "client has no door out of a room -- only !goto will move it."
              % (sess._SESSION.get("room"), why), flush=True)
        return 0
    room = sess._SESSION.get("room", -1)
    if room == -1 or not sess._SESSION.get("in_field"):
        return 0
    # ONCE PER ROOM. A conversation can end down both paths in quick
    # succession (the script runs out, and a late 0x2099 arrives after the
    # event is already popped), and [room] does not change until the client
    # answers our 0x1166 with its own 0x2000 -- so without this the second
    # push goes out while the session still says "in room" and the player is
    # transitioned twice.
    if sess._SESSION.get("room_exit_sent") == room:
        print("[feworld]    room exit for room %s already pushed -- waiting "
              "for the client's 0x2000, not sending a second one" % room,
              flush=True)
        return 0
    outside = sess._SESSION.get("outside") or sess._SESSION.get("field")
    if not outside:
        print("[feworld]    WARNING: in room %s but nothing remembers the field it "
              "was entered from -- NOT pushing an exit, because 0x1166 to the "
              "wrong area is a transition to somewhere the player never was"
              % sess._SESSION.get("room"), flush=True)
        return 0
    print("[feworld]    ROOM EXIT: %s and this session is in room %s, which "
          "has no door of its own -- pushing the player back out to field %d"
          % (why, room, outside), flush=True)
    sess._SESSION["room_exit_sent"] = room
    goto_push(conn, outbound, mode, be, args, int(outside))
    return 1


def goto_push(conn, outbound, mode, be, args, area, pos=None, via=None,
              face=None):
    """0x1166 MSG_GOTO_SEARCH_OK -- send the client somewhere.

    The door's answer, and it works UNSOLICITED too: the arm (0x05056089)
    registers no request of its own, it just acts on A. A != the current
    field starts the field transition to area A (kind 3 -> 0x20A6 -> 0x2000);
    A == the current field with B == -1 is a same-field warp to `pos`. So
    this is both the door reply and a server-side "leave": `!goto 39` in
    --gmcmd-file pulls a player out of a room that has no exit of its own.
    """
    sx, sy, sz = pos if pos is not None else spawns.spawn_for(args, area)
    # WARNING: THE POSITION IN THIS MESSAGE IS ONLY USED FOR A SAME-FIELD WARP.
    # When A is a DIFFERENT area the arm starts a field TRANSITION instead
    # (kind 3 -> 0x20A6 -> 0x2000), and the place the player ends up is
    # whatever the ENTRY sequence sends -- 0x1027 MSG_SET_POSITION and the
    # self record, both of which ask spawn_for(). So a door's own arrival
    # point was computed, put on the wire, and then silently ignored: you
    # walked through a door and arrived at the area's spawn.
    # Reported live 2026-09-10, and true of the SHIPPED doors as well as
    # `!link` ones.
    #
    # So hand it forward: stash it against the destination, and spawn_for
    # prefers it for exactly that area. A goto with no position of its own
    # CLEARS the stash, so `!goto 91` still means "the spawn point".
    # KEY: WHICH DOOR YOU CAME OUT OF, recorded HERE and nowhere earlier: this
    # is where the move is committed. A door that is looked up and then
    # refused by the latch never reaches this line, so it is never reported
    # as "the door you just came through". The panel pre-selects it, which
    # turns fixing a bad landing into: walk through, walk to where you should
    # have appeared, press one button.
    if pos is not None:
        sess._SESSION["arrived_via"] = int(via) if via is not None else None
    else:
        sess._SESSION.pop("arrived_via", None)
    if pos is not None and area != sess._SESSION.get("field"):
        sess._SESSION["arrive_at"] = (int(area), (sx, sy, sz))
        # and the way to face on arrival: SE ships one per portal, pointing
        # away from the door -- we used to arrive every player facing north
        # whatever the door, which at a south-facing gate means staring back
        # into the doorway you just came out of
        if face is not None:
            sess._SESSION["arrive_face"] = (int(area), (face[0], face[1]))
        else:
            sess._SESSION.pop("arrive_face", None)
        # KEY: THE RE-ENTRY LATCH. SE's own arrival points sit a few units from
        # the doorway they belong to -- portal 12's is 5.7 units from its gate
        # -- and our match radius is the door's own plus --door-radius, so the
        # spot you arrive at is INSIDE the trigger. Land badly once and the
        # first step re-fires the door, which is a trap the player cannot walk
        # out of. Latch it: this door does not fire again until the player has
        # left its radius. Cleared in door_latch_ok below.
        sess._SESSION["door_latch"] = (int(area), sx, sz)
    elif pos is None:
        sess._SESSION.pop("arrive_at", None)
        sess._SESSION.pop("arrive_face", None)
    body = struct.pack(">IIfff", area & 0xFFFFFFFF, 0xFFFFFFFF, sx, sy, sz)
    wire.send(conn, outbound, wire.inner_msg(0x1166, body, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    what = ("room %d (%s)" % (area - zones.ROOM_AREA_BASE, zones.room_label(area - zones.ROOM_AREA_BASE))
            if zones.ROOM_AREA_BASE <= area < zones.ROOM_AREA_BASE + 0x100 else "field/group %d" % area)
    print("[feworld] -> 0x30 inner 0x1166 MSG_GOTO_SEARCH_OK A=0x%X (%s) B=-1 "
          "pos=(%g,%g,%g) -- A != the current field, so the arm 0x050561b7 "
          "stores it and sets 0x5345ea0=1: expect 0x20A6 MSG_MOVE_REQUEST "
          "(answered 0x1168), then 0x2000 area=0x%X"
          % (area, what, sx, sy, sz, area), flush=True)


def npc_walk_push(conn, outbound, mode, be, args, client_body):
    """Walk the NPC with `0x2023` action 0 -- the message the PLAYER cannot use.

    2026-08-25. Action 0's arm (0x04fea890) resolves the target and takes the
    speed-writing block only for kind 2 (0x04fea923), which is why three
    iterations of aiming it at the player -- kind 1 -- were discarded unread.
    A served avatar is stamped kind 2 by its own ctor (0x05067a32), so this is
    the first legitimate target that message has ever had here.

    THE TICK PAIR IS THE CLIENT'S OWN, NOT INVENTED. The two u32 after the
    action code are handed to 0x515ba10 -> 0x515b870 alongside the position,
    and their timebase is unmeasured. Rather than guess one, this reuses the
    pair off the client's most recent 28-byte 0x2023 heartbeat, so the record
    sits in whatever clock the client is already using. That is why this is
    driven from the inbound frame instead of a timer.

    \u26a0 THE POSITION IS DEAD-RECKONED SERVER-SIDE. We advance our own idea of
    where the NPC is by --npc-walk's step each push. The client is never asked
    where it thinks he is, so this WILL drift from what is drawn if the client
    interpolates differently. It is a probe for "does he move", not a
    simulation. i16 at x10 caps the world at +-3276.7 units.

    \u26a0 state is forced to 1: zero makes 0x04feadd8 skip the controller
    entirely, and then only the speed pair would be written.
    """
    if not args.npc_walk or not args.npc:
        return
    if len(client_body) != movement._MV_LEN:
        return
    speed, step = args.npc_walk
    for k, spec in enumerate(args.npc):
        obj = args.npc_base + k
        f = spec.split(":")
        try:
            home = tuple(float(v) for v in f[1:4]) if len(f) >= 4 else (0.0, 0.0, 0.0)
        except ValueError:
            return
        # flat per-object key: _SessionProxy forwards only
        # get/pop/__setitem__/__contains__, so a nested dict pulled back
        # through get() is not guaranteed to be the live object to mutate.
        key = "npc_z_%d" % obj
        x, y = home[0], home[1]
        z = sess._SESSION.get(key, home[2]) + step
        if abs(z) > 3000.0:            # stay inside the i16 the client reads
            z = home[2]
        sess._SESSION[key] = z
        out = bytearray(movement._MV_LEN)
        struct.pack_into(">I", out, movement._MV_ACTION, 0)
        # the client's OWN tick pair, lifted verbatim
        out[movement._MV_A:movement._MV_A + 8] = client_body[movement._MV_A:movement._MV_A + 8]
        # WARNING: STATE 0, NOT 1 -- RETRACTION, measured 2026-08-25.
        # I read the `je` at 0x04feadd8 as "zero SKIPS the controller" and
        # forced 1. Backwards. The two branches are:
        #   state != 0 -> 0x515ba10, which requires [ctrl+0x8c] to be 0 or
        #                 1 and RETURNS otherwise
        #   state == 0 -> 0x04feae15 calls 0x515b870 DIRECTLY -- the same
        #                 mover, NO mode check, and it passes the tail u32
        #                 where 0x515ba10 passes 0
        # `felive --npc Bob` measured [ctrl+0x8c] = 4, which is what the
        # controller's own initialiser stamps at birth (0x0515b535). A
        # fresh avatar is therefore ALWAYS in a mode 0x515ba10 rejects, and
        # state 1 could never have moved him. State 0 -- what the client's
        # own heartbeat sends -- is the path that works.
        # KEY: The speed pair landed either way (felive read 1.0): 0x04feadcc
        # writes it BEFORE the branch. Which is exactly why the probe prints
        # the controller mode too -- "the record arrived" and "the record
        # did something" are different claims.
        struct.pack_into(">H", out, movement._MV_STATE, 0)
        struct.pack_into(">hh", out, movement._MV_SPD, movement._i16(speed * 1000.0), 0)
        struct.pack_into(">hhh", out, movement._MV_POS,
                         movement._i16(x * 10.0), movement._i16(y * 10.0), movement._i16(z * 10.0))
        wire.send(conn, outbound, wire.inner_msg(0x2023, bytes(out), obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        n = sess._SESSION.get("npc_walk_n", 0) + 1
        sess._SESSION["npc_walk_n"] = n
        if n <= 3 or n % 40 == 0:
            print("[feworld]    -> 0x2023 action 0 NPC id=%d #%d speed=%g "
                  "target=(%.1f, %.1f, %.1f) -- kind 2, so this arm ACCEPTS it. "
                  "Watch the NPC, not felive (felive reads YOUR unit)."
                  % (obj, n, speed, x, y, z), flush=True)
