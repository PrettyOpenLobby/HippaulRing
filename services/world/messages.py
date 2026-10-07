"""Message id tables: names for the log, the client's NG table, header-only OK replies."""

# Ids the client is known to SEND on a MassPlayer connection. Only 0x20 is
# measured for this port; the rest are the lobby's, kept so a familiar id is
# named rather than printed as a bare number.
KNOWN = {
    0x0002: "keepalive (bare outer frame, no body)",
    # The full raw-outer-id family, from every call site of write_u16 (0x522f750)
    # that passes a literal. These are written OUTSIDE the message builder, so
    # they are transport frames, not application messages:
    #
    #   0x02 keepalive   0x08 tick    0x09 tick+counter   0x30 enciphered
    #   0x31 cipher-module sibling of 0x30   0x34/0x36 key exchange
    #
    # 0x08 carries a 64-bit value fetched by 0x52323e0, which is a plain getter
    # for the u64 at [obj+8] -- and the function directly after it compares that
    # same pair against a live reading from an indirect timer call, so the field
    # is a TICK, not a byte count. Observed 0x10 and 0x1f, both small and
    # constant within a session, which fits a connection-relative clock.
    0x0008: "transport TICK, u64 from [obj+8] (0x52323e0); client sends 2/session",
    0x0009: "transport tick + a u16 counter at [obj+0x1c8] (0x05230340)",
    0x0031: "cipher-module frame, sibling of 0x30 (0x05232bc7) -- NOT YET SEEN",
    0x0020: "MSG_AUTH_CODE_NOTIFY -- the CODE from 0x7821",
    0x0030: "outer ENCIPHERED marker (the real id is inside)",
    0x0034: "key exchange phase 1",
    0x0036: "key exchange phase 3",
    0x400F: "MSG_SERVER_UNIT_LOGIN_REQUEST -- account + charid + credential",
    # Measured 2026-08-19 off the registration idiom (see enter_area). Every row
    # is `request -> the ids its builder pre-registers as the only two answers`,
    # read from `mov word ptr [esp+N], imm16` pairs next to the beginMessage
    # call. The method is validated: it reproduces 0x400F->0x302B/0x302C and
    # 0x4012->0x3033/0x3034, both of which were established independently.
    0x2000: "MSG_ENTER_AREA [u32 areaId] -> 0x1000 OK / 0x1001 NG (0x0505e600)",
    0x2016: "MSG_MOVE_AREA_REQUEST -> 0x1010 / 0x1011 (0x0505e760)",
    0x2017: "MSG_MOVE_FIELD_REQUEST -> 0x1012 / 0x1013 (0x0505e6e0)",
    0x20A6: "MSG_MOVE_REQUEST -> 0x1168 / 0x1169 (0x0505e7c0)",
    0x4006: "MSG_MOVE_MY_CHARACTER_INFO_TO_AREA_REQUEST -> 0x300C / 0x300D",
    0x2018: "MSG_DECIDE_COUNTRY_REQUEST [u32 army] -> 0x1020 OK / 0x1021 NG "
            "(sender 0x05118940 = CWarPrepareWindow vtable +0x7c). The old "
            "'war-map screen request' label was inherited and WRONG -- the "
            "client names it itself: the sender logs '<< MSG_DECIDE_COUNTRY_REQUEST' "
            "(0x052e62dc) then 'army choice req sent : %d' (0x052e62c0)",
    0x2084: "MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_REQUEST [u32 islandId] -- "
            "the war-prep window's 1s heartbeat (screen vtable +0x78, "
            "0x05118900); answered by 0x1127 (arm 0x050581A8)",
    0x4010: "-> 0x302D / 0x302E (0x05127f09)",
    # ---- THE IN-GAME UI, mapped 2026-08-25 off the command table and the
    # inbound dispatchers (fedisp.py). Every row here is measured; see the
    # handler comments below for the reader-call chain each body came from.
    0x2080: "MSG_GET_CHARACTER_DISTRIBUTION_INFO_REQUEST (no body; ~10s poll) "
            "-> 0x1123 OK / 0x1124 NG",
    0x20A0: "MSG_SET_BLACKLIST_REQUEST [u32 myCharId][cstr NAME] -> 0x115F OK / "
            "0x1160 NG (0x05133220 = the /setblacklist command handler)",
    0x20A1: "MSG_REMOVE_BLACKLIST_REQUEST [u32 myCharId][u32 targetId] -> "
            "0x1161 OK / 0x1162 NG (0x051332c0 = /rmblacklist)",
    0x20A2: "MSG_GET_BLACKLIST_REQUEST [u32 myCharId] -- registers NO reply; "
            "the list arrives UNSOLICITED as 0x1163 (0x051333d0 = /getblacklist)",
    0xF900: "GM COMMAND CHANNEL [u16 len][ascii] -- a command LINE, both "
            "directions (0x0517e050 out, 0x0517ef60 in)",
    # KEY: THESE SIX RUN BOTH WAYS. The client's receive dispatcher routes
    # the first five to ONE arm (0x05053cbe, [cstr name][cstr text]) and
    # 0x2067 to its own (0x05053e28, THREE strings). So the long-open "find
    # the inbound chat id" is answered: it is the outbound id. See _CHAT_IDS.
    0x201A: "MSG_CHAT (/say) [cstr speaker][cstr cp932 text] -- registers no "
            "reply (out 0x051334f0, in 0x05053cbe)",
    0x201B: "/all chat (out 0x05133610, in 0x05053cbe)",
    0x2065: "/army chat (out 0x05133730, in 0x05053cbe)",
    0x2066: "/army chat, second form (out 0x05133730, in 0x05053cbe)",
    0x208A: "/party chat (out 0x05133970, in 0x05053cbe)",
    0x2067: "/tell chat (out 0x05133a90, in 0x05053e28 -- THREE strings)",
    0x209F: "display-state notify [u32 [obj+0x3d4]] -- sent by /disphp and "
            "/freecam and by the scene update; registers no reply (0x05079f60)",
    0x2002: "[u16][u16] -- gated on [0x5336d1c]+0x4fc and 0x4ff7f70; registers "
            "no reply (0x0507fe15)",
    0x2023: "outbound telemetry, 14 or 28 B, ~400ms -- fire-and-forget",
    # KEY: THE DOOR, live 2026-09-05. Walking into one of the capital's minimap
    # doors sends this -- [f32 x][f32 y][f32 z], the door's position -- from
    # the scene at 0x04ffbc7b, which registers ONE reply, 0x1166
    # (MSG_GOTO_SEARCH_OK by the NG table's 0x1167 MSG_GOTO_SEARCH_NG), and
    # sets [screen+0x62]=1 "in flight": an unanswered door never fires again.
    0x20AC: "MSG_GOTO_SEARCH (a DOOR was touched) [f32 x][f32 y][f32 z] -> "
            "0x1166 OK / 0x1167 NG (builder 0x04ffbc40)",
    0x2085: "event-window CLOSED notify [u32 mode] (the shop's close 0x50f00c0, "
            "the bank's 0x50ecf80, the box's slot-30 0x50c74c0; registers NO "
            "reply; a 0x20A8 (npc, 8, 0) follows it)",
    0x2046: "MSG_NPC_EVENT_ACTION (a keeper was clicked) [u32 npc] -> 0x1071 / 0x1072",
    0x20A7: "event start [u32 npc][u32 script] -> 0x1172 / 0x1173 (0x5173f70)",
    0x20A8: "event ACK [u32][u16 op][u32] (0x5173fd0) -- cues the next command",
    0x2099: "conversation CLOSE [u32] -> 0x1152 / 0x1153 (op 8 type 0x13) -- "
            "arrives as a BARE OUTER frame (stack-local stream, live #7); "
            "then 0x1175 EVENT END releases event mode",
    0x2073: "MSG_GET_SHOP_ITEM_LIST_TOTAL_PAGE [u32] -> 0x1102 / 0x1103",
    0x204A: "MSG_GET_SHOP_ITEM_LIST [u32 shop=npc][u32 page] -> 0x107B pages "
            "(0x50eec20; the widget asks page after page)",
    0x204B: "MSG_BUY_SHOP_ITEM [u32 shop][u32 list+0x94][u16 count] -> 0x107D "
            "/ 0x107E (0x50ee970) -- LOG the middle field: item number or row",
}

# ---------------------------------------------------------------------------
# KEY: THE ID -> NAME MAP, LIFTED OUT OF THE CLIENT'S OWN NG TABLE (2026-08-25).
#
# FE ships a table of error records, 0x70 bytes each, laid out
# {u32 code, u32, u32=3, char* text, char* MSG_NAME, ..., u32 msgid at +0x50},
# and `0x50613e0(msgid, code)` resolves an NG body's error code through it. A
# whole-image sweep for "a dword pointing at a MSG_ string with a plausible id
# 0x40 later" named 81 message ids in one pass -- and because FE numbers replies
# OK = NG - 1 everywhere it has been checked, it names the OK side too.
#
# This is here as DOCUMENTATION, not code: it is the fastest way to find out
# what an unknown id is before writing a handler for it, and re-deriving it cost
# an afternoon. Regenerate by re-running that string-table sweep over the image.
NG_NAMES = {
    0x100A: "MSG_BUILD_BUILDING_NG",      0x1013: "MSG_MOVE_FIELD_REQUEST_NG",
    0x1021: "MSG_DECIDE_COUNTRY_NG",      0x1024: "MSG_ITEM_GATHER_NG",
    0x1026: "MSG_ITEM_THROW_AWAY_NG",     0x102B: "MSG_EQUIP_ITEM_NG",
    0x102D: "MSG_DISRAM_ITEM_NG",         0x1046: "MSG_CHARA_LEVEL_LIQUIDATION_NG",
    0x106B: "MSG_METAMORPHOSIS_NG",       0x1072: "MSG_NPC_EVENT_ACTION_NG",
    0x1079: "MSG_ACQUIRE_SKILL_NG",       0x107C: "MSG_GET_SHOP_ITEM_LIST_NG",
    0x107E: "MSG_BUY_SHOP_ITEM_NG",       0x1081: "MSG_SELL_ITEM_NG",
    0x1083: "MSG_SHOP_PROCEDURE_START_NG", 0x1087: "MSG_GET_SELL_ITEM_VALUE_NG",
    0x1088: "MSG_EVENT_CANCEL_NOTIFY",    0x108A: "MSG_USE_ITEM_NG",
    0x108C: "MSG_TRADE_REQUEST_NG",       0x108F: "MSG_TRADE_ENTRY_NG",
    0x1092: "MSG_CLASS_LEVEL_UP_NG",      0x111D: "MSG_ATTACH_ITEM_GET_NG",
    0x1103: "MSG_GET_SHOP_ITEM_LIST_TOTAL_PAGE_NG", 0x1121: "MSG_ENCHANT_NG",
    0x1124: "MSG_GET_CHARACTER_DISTRIBUTION_INFO_NG",
    0x1126: "MSG_SET_FORCE_BOSS_WORD_NG",
    0x1128: "MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_NG",
    0x112B: "MSG_GET_CRYSTAL_NG",         0x112D: "MSG_REPAIR_ITEM_NG",
    0x1130: "MSG_CANVASS_PARTY_MEMBER_NG", 0x1138: "MSG_DISBAND_PARTY_NG",
    0x113B: "MSG_PARTY_MEMBER_DISBAND_NG", 0x113D: "MSG_PARTY_CHAT_NG",
    0x1141: "MSG_DEBUG_COMMAND_NG",       0x1145: "MSG_TRADE_ENTRY_CLEAR_NG",
    0x1147: "MSG_TRADE_COMPLETE_NG",      0x114F: "MSG_DISRAM_EQUIP_ITEM_NG",
    0x1151: "MSG_ITEM_EXCHANGE_NG",       0x115C: "MSG_GET_REPAIR_ITEM_COST_NG",
    0x1160: "MSG_SET_BLACKLIST_NG",       0x1162: "MSG_REMOVE_BLACKLIST_NG",
    0x1167: "MSG_GOTO_SEARCH_NG",         0x1169: "MSG_MOVE_NG",
    0x117A: "MSG_ITEM_SORT_NG",           0x117C: "MSG_ITEM_MANUAL_SORT_NG",
    0x208E: "MSG_DEPOSIT_GOLD_NG",        0x2090: "MSG_WITHDRAW_GOLD_NG",
    0x2092: "MSG_DEPOSIT_ITEM_NG",        0x2094: "MSG_WITHDRAW_ITEM_NG",
    0x2096: "MSG_ORGANIZE_ITEM_NG",       0x20B2: "MSG_SOS_NG",
    0x300D: "MSG_MOVE_MY_CHARACTER_INFO_TO_AREA_NG",
    0x302E: "MSG_GET_SYSTEM_LOG_NG",      0x3032: "MSG_GET_GROUP_DETAIL_INFO_NG",
    0xA052: "MSG_MAIL_SEND_NG",           0xA082: "MSG_MAIL_DEL_NG",
    0xC00E: "MSG_LC_LOGIN_PROCEDURE_NG",  0xC011: "MSG_CERTIFICATION_NG",
    0xD003: "MSG_LC_GET_CHARACTER_LIST_NG", 0xD005: "MSG_LC_ADD_CHARACTER_NG",
    0xD007: "MSG_LC_DELETE_CHARACTER_NG", 0xD009: "MSG_LC_DECIDE_CHARACTER_NG",
    0xE002: "MSG_R_GM_GET_CHARACTER_INFO_FROM_NAME_NG",
    0xE00C: "MSG_R_GM_SYSTEM_MESSAGE_NG", 0xE00F: "MSG_R_GM_FORCE_MOVE_NG",
    0xF0FF: "MSG_CLIENT_ERROR_CODE",
}

# KEY: THE 0xF900 COMMAND VOCABULARY -- the client's own table at 0x052eacc0
# (0x10-byte rows: {const char* name, handler_a, handler_b, u32 category}),
# plus the emote-only rows at 0x052eab70. Category 1 = chat, 3 = set/get/sos,
# 4 = blacklist, 5 = the debug/GM set. Emotes carry no handler at all.
#
# WARNING: THE CHANNEL RUNS BOTH WAYS AND THE RECEIVE SIDE IS A COMMAND EXECUTOR.
# 0x0517ef60 decodes the payload, then:
#     text[0] == '/'  -> 0x05133e00(text, 1)  = RUN IT as a slash command
#     text[0] == '@'  -> 0x05121fd0(4, text+1) = PRINT it as a system message
# so one 0xF900 from us either drives the client's own UI or writes a line into
# its message window. That is the cheapest lever we have on FE.
# ---------------------------------------------------------------------------
# KEY: THE HEADER-ONLY OK TABLE -- how to stop answering FE one button at a time.
#
# Measured 2026-08-25 after two live hangs an hour apart (the inventory STACK
# button, then SORT). Both were the same shape and both were one line of table:
# a request whose registered OK the server had never sent.
#
# Every FE request registers its two acceptable replies as u16 immediates next
# to the send call, so a sweep for those immediates lifts the WHOLE map out of
# the image -- 47 requests -- and then resolving each OK to its dispatcher arm
# reports which stream primitives that arm calls. The
# rows below are the ones whose OK arm calls NONE: the reply is the 6-byte
# header and nothing else, exactly like 0x1020 MSG_DECIDE_COUNTRY_OK, which has
# been verified live since 2026-08-24.
#
# WARNING: HEADER-ONLY IS NOT A DEFAULT, IT IS A MEASUREMENT. A body on a header-only
# reply does not add data, it DESYNCHRONISES the stream and every message after
# it decodes as garbage. Nothing goes in this table without its arm address.
#
# WARNING: AND "OK" HERE MEANS ACKNOWLEDGED, NOT SIMULATED. We have no inventory, bank
# or shop model, so answering MSG_BUY_SHOP_ITEM_OK tells the client a purchase
# succeeded that nothing actually performed. That is deliberate -- an
# unanswered request hangs the dialog forever and takes the session with it,
# which is strictly worse -- but it is why --ui-auto exists and why every one of
# these logs loudly.
#
# WARNING: TWO CLASSES OF FALSE POSITIVE were filtered out of this table by hand, and
# fedisp.NOT_ARMS now filters them automatically: a jump-table slot pointing at
# a dispatcher's shared EPILOGUE (0x05057423, 0x05058b17, ...) means the handler
# DECLINES the id, and reading that as "an arm that reads nothing" would invite
# an empty reply to a message the client never decodes at all. 0x1154, 0x1177,
# 0x1120 and 0x112C all looked header-only until that filter went in.
#
#     request: (OK, NG, name, the OK arm it was measured at)
UI_HEADER_ONLY_OK = {
    # The bank window. Requests are 0x114x and replies 0x20xx -- this family
    # runs the id ranges BACKWARDS from the rest of the protocol, which is why
    # its NG names turned up under 0x20xx in FE's error table. SIMULATED since
    # 2026-09-11 (bank_request, BANK_REQUESTS); these rows are now only the
    # `--bank off` / `--ui-auto ng` fallback.
    0x1149: (0x208D, 0x208E, "MSG_DEPOSIT_GOLD", 0x05058653),
    0x114A: (0x208F, 0x2090, "MSG_WITHDRAW_GOLD", 0x050586CD),
    0x114B: (0x2091, 0x2092, "MSG_DEPOSIT_ITEM", 0x0505855F),
    0x114C: (0x2093, 0x2094, "MSG_WITHDRAW_ITEM", 0x050585D9),
    # WARNING: THE STACK BUTTON, live hang 2026-08-25: `11 4d 00 00 00 01`. The
    # builder (0x050ea220 / 0x050b9330) writes one u32 = 1 and sets the
    # inventory window's in-flight state [window+0xc4] = 5; the OK arm
    # (0x050e9ca3, the screen side) clears it back to 0 and logs
    # "< MSG_ORGANIZE_ITEM_OK". Nothing else clears +0xc4 -- hence the hang.
    0x114D: (0x2095, 0x2096, "MSG_ORGANIZE_ITEM", 0x05058747),
    0x2007: (0x1009, 0x100A, "MSG_BUILD_BUILDING", 0x05054BDA),
    0x2016: (0x1010, 0x1011, "MSG_MOVE_AREA_REQUEST", 0x05054A82),
    # The shop. See the "acknowledged, not simulated" warning above.
    # 0x204A is answered WITH A BODY while a shop is open (the 0x107B branch
    # below, measured in the page reader 0x50f5df0); this row is the fallback.
    0x204A: (0x107B, 0x107C, "MSG_GET_SHOP_ITEM_LIST", 0x05023D0F),
    0x204B: (0x107D, 0x107E, "MSG_BUY_SHOP_ITEM", 0x05023E19),
    0x204C: (0x1080, 0x1081, "MSG_SELL_ITEM", 0x05023FEE),
    0x204D: (0x1082, 0x1083, "MSG_SHOP_PROCEDURE_START", 0x05023B27),
    0x2074: (0x111C, 0x111D, "MSG_ATTACH_ITEM_GET", 0x0505889E),
    0x2088: (0x113A, 0x113B, "MSG_PARTY_MEMBER_DISBAND", 0x05055C58),
    # WARNING: THE SORT BUTTON, live hang 2026-08-25: `20 aa`, no body at all.
    0x20AA: (0x1179, 0x117A, "MSG_ITEM_SORT", 0x0505846D),
    0x20AB: (0x117B, 0x117C, "MSG_ITEM_MANUAL_SORT", 0x050584D3),
    0xC003: (0xD006, 0xD007, "MSG_LC_DELETE_CHARACTER", 0x050258FE),
    # WARNING: FIELD OUT (the "Field Out(BS)" button), live hang 2026-08-25: `20 9a`,
    # no body at all. It starts a VALIDATE SEQUENCE -- the client's own name --
    # and the OK arm (screen handler 0x050AD0B0 entry 0) logs
    # ">MSG_START_VALIDATE_SEQUENCE_OK", reads nothing, and arms a countdown:
    # [unit+0x138c] = 0x2710 (10000) with a timestamp at [unit+0x1390].
    #
    # WARNING: These two are SCREEN-registered, so febody reports them as "not in the
    # five dispatchers" and they can never appear in an automated header-only
    # sweep. The arm addresses below were read by hand, which is why they are
    # recorded here -- the sweep cannot find them again for you.
    0x209A: (0x1154, 0x1155, "MSG_START_VALIDATE_SEQUENCE", 0x050AD0D4),
    0x209C: (0x1159, 0x115A, "MSG_START_VALIDATE_SEQUENCE_CANCEL", 0x050AD248),
    # KEY: THE LAST HOP OF FIELD OUT -- and the one that actually exits.
    #
    # Measured live 2026-08-25: answering 0x209A and pushing 0x1157 ran the
    # countdown correctly and STILL did not field the player out, because the
    # FINISH is not the exit -- it only triggers it. 0x1157's arm calls
    # 0x050AD420, which logs the client's own "フィールドから抜けますよ"
    # ("leaving the field"), and the client THEN sends 0x2033 and waits:
    #
    #     04ffb433  call 0x0505e840          send 0x2033 (no body, no
    #                                        registered replies)
    #     04ffb438  mov byte [esi+0x29b], 0xFF   <- ARM, the same idiom as
    #                                        [player+0x295] for enter-area
    #     04ffb444  log "清算開始要求を送信"  ("settlement start request sent")
    #
    # The only writers of +0x29b are 0x05054D38 (=1) and 0x05054D62 (=0), which
    # sit inside the 0x1045 and 0x1046 arms -- and 0x1046 is
    # MSG_CHARA_LEVEL_LIQUIDATION_NG in the client's error table. So Field Out
    # ends in an end-of-battle SETTLEMENT, and 0x1045 is header-only: its arm
    # logs, pokes the result screen, and sets [scene+0x29b] = 1.
    0x2033: (0x1045, 0x1046, "MSG_CHARA_LEVEL_LIQUIDATION", 0x05054CF5),
    # EQUIPMENT. Added 2026-08-25 BEFORE anyone hit them, off the sweep that
    # crosses the client's 47-request map against what this file answers --
    # which is the point: the hangs are enumerable in advance, so waiting for a
    # player to find each one is a choice, not a necessity.
    0x2021: (0x102A, 0x102B, "MSG_EQUIP_ITEM", 0x0503A19B),
    0x2022: (0x102C, 0x102D, "MSG_DISRAM_ITEM", 0x0503A1E0),
    0x2098: (0x114E, 0x114F, "MSG_DISRAM_EQUIP_ITEM", 0x0503A480),
}

# ---------------------------------------------------------------------------
# Replies whose OK arm DOES read a body, where the value is known.
#
#   "echo32"       -- send back the request's own leading u32
#   ("const", n)   -- send a fixed u32
#
# WARNING: Everything here needed its arm opened to learn what the u32 MEANS. That is
# why this table is short and UI_HEADER_ONLY_OK is long: "the arm reads a u32"
# is not enough to answer with, and a plausible number is worse than a hang
# because it renders.
UI_REPLY_BODY = {
    # 0x201E throw-away carries [u32 objectId][u16 ...] and 0x1025's arm feeds
    # its u32 straight to 0x0504CB30(id, 1) -- an OBJECT LOOKUP -- then sets
    # flag 0x200000 on what it finds (0x0503C3C0). So the reply names the same
    # object the request named: echo it, do not invent one.
    0x201E: (0x1025, 0x1026, "MSG_ITEM_THROW_AWAY", 0x0503A167, "echo32"),
    # 0x2073 asks how many pages the shop list has; 0x1102's arm (0x0502409E)
    # reads one u32 and that IS the page count. 1 is the honest answer while
    # there is no shop inventory behind it.
    0x2073: (0x1102, 0x1103, "MSG_GET_SHOP_ITEM_LIST_TOTAL_PAGE",
             0x0502409E, ("const", 1)),
}

# The rest of that screen's table, for whoever needs the other half. All read by
# hand off 0x050AD0B0's jump table 0x050AD2B8 (ids 0x1154..0x115A):
#
#   0x1155 SEQUENCE_NG              u32          0x1156 INTERVAL_NOTIFY  u32,u32
#   0x1157 SEQUENCE_FINISH_NOTIFY   HEADER-ONLY  0x1158 CANCEL_NOTIFY    u32
#   0x115A CANCEL_NG                u32
#
# 0x1157 is a PUSH, not a reply -- see validate_finish().
VALIDATE_FINISH = (0x1157, "MSG_START_VALIDATE_SEQUENCE_FINISH_NOTIFY",
                   0x050AD1BE)

# WARNING: MEASURED HEADER-ONLY, DELIBERATELY NOT AUTO-ANSWERED.
#
# 0x20A6 MSG_MOVE_REQUEST -> 0x1168/0x1169 (arm 0x05056248, no stream reads).
# It is in the MOVEMENT path, and movement is a separate open investigation
# (the player unit is never dressed, see fe-world-door). Quietly starting to
# answer MOVE would change that investigation's behaviour underneath it without
# anyone deciding to -- so it is a knob, --move-request, default off, not a row
# in the table above.
UI_MOVE_REQUEST = (0x20A6, 0x1168, 0x1169, "MSG_MOVE", 0x05056248)


# The notice served when --world-notice is not given. Admin-authored content
# by design -- 0x303C is a world announcement, so this is ours to write, not
# something recovered from SE. Override per deployment with --world-notice.
DEFAULT_NOTICE = "Notice|Welcome to Fantasy Earth."


# ---- CHAT, THE INBOUND DIRECTION.  2026-08-25.
#
# KEY: THE INBOUND CHAT ID IS THE OUTBOUND ONE. There is no separate reply
# family to find: the ids the client SENDS on /say, /all, /army and /party are
# the same ids its receive dispatcher accepts, and one shared arm draws them.
# The old note in this file ("the numbers are unknown, none of MSG_CHAT_NG's
# family carries an id") was looking for a message that does not exist.
#
# HOW IT WAS FOUND, and it is the DRAW-SITE-BACKWARDS trick again: /say echoes
# locally (0x051334f0) through 0x05077cc0 then 0x05129890, so 0x05129890 is the
# chat LOG sink. It has fifteen callers; eleven are the local /say /all /army
# /tell family, and the rest are dispatcher arms. One of them, 0x05053cbe,
# reads two strings off the wire and prints them.
#
# WARNING: fedisp.py --sweep MIS-RESOLVED THIS ONE. It reported 0x201A -> 0x05053d50,
# an address in the MIDDLE of the arm, because the ladder hangs two ids off one
# compare (`cmp eax,0x201b / jg / cmp eax,0x201a / jge 0x5053cbe`) and the
# emulator took the second branch's target as the arm. The jump TABLES were
# decoded by hand to settle it -- 0x0505418c/0x05054178 for 0x1152..0x117D and
# 0x050541d8/0x050541b8 for 0x2056..0x208A.
#
# THE ARM, 0x05053cbe, in order:
#   1. read cstr -> [esp+0xd5]   the SPEAKER NAME
#   2. read cstr -> [esp+0x54]   the TEXT
#   3. 0x05170e10(name)          THE BLACKLIST -- it walks the list at
#                                [0x535c29c]..[0x535c2a0] comparing strings and
#                                returns 1 on a hit, and a hit DROPS THE LINE
#                                with no log. A blacklisted test name reads
#                                exactly like a broken message.
#   4. 0x05129890(id, name, text, 0, -1)   the chat log window
# The sink's FIRST argument is the message id itself, which is why the five ids
# share one arm and can still render differently -- that argument is what
# 0x05077cc0 computes for a locally-echoed line.
#
# WARNING: THE READER IS AN UNBOUNDED strcpy (0x05045f50 -> 0x0522f5e0) INTO A STACK
# FRAME, and the text slot is 0x81 bytes ([esp+0x54] up to the name at
# [esp+0xd5]). A relay that passes a long line through smashes the receiving
# client's stack. Both strings are clamped here, and any future
# player-to-player relay MUST clamp too -- this is a remote write on every
# client in the field, not a cosmetic limit.
#
# WARNING: THE TEXT IS cp932 ON THE WIRE and FE splits DBCS with IsDBCSLeadByte,
# which answers for the SYSTEM codepage (1252 on this host), so Japanese echoed
# back draws as dot pairs. ASCII is safe.
_CHAT_IDS = {
    0x201A: ("/say (area)", 2),
    0x201B: ("/all", 2),
    0x2065: ("/army", 2),
    0x2066: ("/army, second form", 2),
    0x208A: ("/party", 2),
    # /tell is a DIFFERENT arm (0x05053e28) and it reads THREE strings, into
    # [esp+0xf6], [esp+0xd5] and [esp+0x54]. It sets the arm's channel slot to
    # 3 where the five above set 2. Which of its three is from/to/text is NOT
    # measured -- serve it with distinct sentinels and read the screen.
    0x2067: ("/tell (3 strings, UNMEASURED order)", 3),
}
