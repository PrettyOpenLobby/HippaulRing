"""feitems.py -- the FE item channel LEFTOVERS: enchant, repair, gather, the
sell-value ask, class level-up, the Player Item Slots window, and the unnamed
outbound requests around them.  PARTIAL: BUILT 2026-09-10, NOTHING HERE IS
VERIFIED LIVE YET.

Everything below was read off the runtime dump of FE_Client.dll (base
0x04F90000) by static analysis on 2026-09-10.  MEASURED means an address is
quoted; CHOSEN means we picked it and say so.  The equip / buy / sell / use /
stack / sort / bank / shop-page / skill ids are feworld's and are NOT touched
here; the rule this module inherits: an OK reply on the item channel moves
nothing, the server PUSHES the state.

== ENCHANT (the enchant window, message switch 0x050f53c0, base 0x1120) =====
  out 0x2077 MSG_ENCHANT_REQUEST   [u32 Target uid][u32 Enchant uid]
        builder 0x050f4160: log "<MSG_ENCHANT_REQUEST Target:%d,Enchant:%d",
        w32(edi=arg1=Target) then w32(esi=arg2=Enchant); it REGISTERS ONE reply
        (`mov dword [esp+0x10], 0x1122; push 1`) -- 0x1122, NOT 0x1120/0x1121.
  in  0x1122 MSG_ENCHANT_REPLY      [u32 RID][u32 COST]            (SERVED)
        arm 0x050f53f2: two 0x5045e60 reads, log ">MSG_ENCHANT_REPLY RID =
        %d,COST = %d" (0x52e3b28), RID -> [win+0xc0], COST -> [win+0xc8]; in
        window state 2 it auto-cancels through the 0x2079 builder (0x050f4240),
        in state 4 it shows the confirm "加工費に%sゴールドかかります。エンチャ
        ントをしますか？" (0x52e38cc).  The name is the client's own log line.
  out 0x2078 MSG_ENCHANT_REPLY_OK  [u32 RID]   builder 0x050f41d0 -> 0x1120/0x1121
  out 0x2079 MSG_ENCHANT_REPLY_NG  [u32 RID]   builder 0x050f4240 -> 0x1120/0x1121
  in  0x1120 MSG_ENCHANT_OK         HEADER-ONLY (SERVED)  arm 0x050f5524: log
        ">MSG_ENCHANT_OK", state=7, [win+0x74]=0x80000000, refresh; NO reads.
  in  0x1121 MSG_ENCHANT_NG         [u32 code]  (SERVED)  arm 0x050f54b9: one
        0x5045e60 read, log ">MSG_ENCHANT_NG ErrorID = %d"; state 2 -> quiet
        reset, else state=7 + the NG dialog (0x50613e0).  Codes from the
        client's NG table (dumped 2026-09-04): 7 = "Enchant cancelled." draws
        no dialog, 8 = not enough gold, 13 = equipped.
  MODEL (CHOSEN): 0x2077 validates (both uids in the bag, target not worn ->
  13, material present -> 4), issues a per-session RID, answers 0x1122 with
  --enchant-cost.  0x2078 with a matching RID: consume ONE of the material
  (the same remove path the sell/use arms take, then bag_layout_push), record
  the material's item number under the target uid in the store field
  `enchants` (a dict uid -> [item no...]), debit gold when a wallet is stored,
  answer 0x1120, re-push the target through 0x107A.  0x2079: answer 0x1121
  code 7.  WARNING: WHAT THE CLIENT SHOWS ON THE TARGET IS NOT MODELLED: the client
  has an enchant table keyed by "enchant type" (log 0x52e3084 "enchant type =
  %d : アイテムに対応するエンチャント情報が見つからなかった") and the enchant
  slots live in item field groups 2/3 that item_fields() sends EMPTY.  The
  material is consumed and the store remembers it; the target's displayed
  stats do not change.  Which items are enchant MATERIALS is not in
  fe-fet-items.tsv either (category is 0x0000 for 493 of 598 rows), so any bag
  item is accepted as one.

== REPAIR (the repair window, message switch 0x050f0f60, base 0x112C) ======
  out 0x209E MSG_GET_REPAIR_ITEM_COST_REQUEST  [u32 uid]   builder 0x050f1692..
        registers THREE replies (0x115B, 0x115C, 0x112D -- `push 3`), sets
        window state 4.  Name: its NG 0x115C is MSG_GET_REPAIR_ITEM_COST_NG in
        the NG table.
  out 0x2086 MSG_REPAIR_ITEM_REQUEST  [u32 uid][u32 -1]   builder 0x050f13d4
        (`push ebp=[win+0x78]; push -1`), registers 0x112C/0x112D, state 2.
        The -1 is a constant at this site; UNKNOWN meaning (CHOSEN: ignored).
  in  0x112C MSG_REPAIR_ITEM_OK   HEADER-ONLY (SERVED)  arm 0x050f116b:
        `mov dword [win+0x70], 0` and return -- no reads, nothing repaired.
  in  0x112D MSG_REPAIR_ITEM_NG   [u32 code]  (SERVED)  arm 0x050f109e reads
        the code out of the posted struct's +8 (code 5 = not enough gold, 6 =
        "『%s』の耐久度は最大のため、修復の必要はありません" with the item
        name looked up in category 0xbb8, 0x050f10dc).  WARNING: The stream read
        happens in the registered-reply path, NOT in this arm; every c3 NG arm
        reads exactly one u32 into that slot (0x050584bc/0x05058522 ->
        0x509fcc0), so [u32 code] is INFERRED FROM THE CONSUMER, not measured
        at the reader.
  in  0x115B MSG_GET_REPAIR_ITEM_COST_OK  arm 0x050f0f8c: uses struct+8 as a
        KEY against the window's uid-keyed repair list ([node+0xc]) before
        0x050f20b0(row, ...).  WARNING: BODY UNMEASURED beyond that first u32.  The
        default answer to 0x209E is therefore 0x112D code 6 ("durability full,
        no repair needed"), whose one-u32 body is the measured NG shape and
        which closes the dialog honestly -- this server does not track wear.
        `--repair-cost ok:N` answers 0x115B [u32 uid][u32 N] instead -- the
        0x1086 {uid, value} shape of the same window family, CHOSEN, PARTIAL:.
  MODEL: 0x2086 -> 0x112C, then the item is re-pushed through 0x107A, whose
  group-4 fields carry +0x4aa/+0x4ac (max/current durability -- the cast
  window's third rung is durability) from --item-durability -- that push IS
  the repair on the client.  The durability sub-record 0x2032 (worker
  0x0503a7a1 -> 0x5075b80, "%s broke and can't be used") belongs to the feunit
  module; the handshake is: feunit lowers +0x4ac through 0x2032, this module
  restores it through 0x107A on repair.  Nothing here sends 0x2032.

== GATHER (pick an object up off the ground) ==============================
  out 0x201F MSG_ITEM_GATHER_REQUEST  [u32 objectId]   builder 0x05152ba0:
        distance self <-> [win+0x6c] against [0x529700c], else the notice
        "...too far away to pick up." (0x5297010); sets flag 0x200000 on the
        player, stamps [win+0x78]=clock, writes the OBJECT's id.  Registers
        nothing.  Named by its replies: 0x1023/0x1024 sit right before
        0x1025/0x1026 THROW_AWAY whose request is 0x201E.  CHOSEN name.
  in  0x1023 MSG_ITEM_GATHER_OK  [u32 objectId]  (SERVED)  arm 0x0503a133 ->
        0x0503c2d0: one 0x5045e60 read, OBJ_FIND(id, class 4) -> 0x05019110:
        notice "E24_GetItem" / "E25_GetCrystal" ([tbl+0x1c8]==0x19) through
        0x05019150; a miss logs "GetItem : Object not found" (0x52d47a4).
  in  0x1024 MSG_ITEM_GATHER_NG  [u32 code]  arm 0x0503a14d (feworld names it;
        codes in the NG table: 2 = no right, 3 = too far, 4 = someone else
        took it).
  MODEL: feworld has NO ground-drop model (nothing spawns class-4 objects the
  player could pick up), so the honest default is `--gather ng:2`.  `ok:ITEM`
  adds item ITEM to the bag (uid allocated the way the buy arm does), pushes
  it through bag_layout_push, answers 0x1023 with the object id and MSG_DEL
  0x1004 addressed to the object -- for an operator who spawned a kind-0xbb8
  drop with --monster and wants to see the pick-up.  PARTIAL: untested.

== SELL VALUE (the sell window, 0x050ef3c0) ================================
  out 0x2050 MSG_GET_SELL_ITEM_VALUE_REQUEST  [u32 shopCtx][u32 uid]
        state-0 entry of the sell window's 7-state switch (0x050ef348):
        SELF_UNIT -> 0x0507ba90(unit, slot 0, kind 0) = the uid in BAG SLOT 0,
        then w32([win+0x7c]) w32(uid).  Registers nothing -- which is exactly
        why the equip research found "nothing registers 0x1086" and
        feworld volunteers 0x1086 for the whole bag at shop open.  CHOSEN
        name (its answer is 0x1086 whose name is the client's).  SERVED: the
        uid named gets one 0x1086 through feworld's sell_value_push at the
        --sell-price it already uses; the shop-context u32 is logged.

== CLASS LEVEL-UP -- MOVED to feprog.py (2026-09-11) =========================
  0x2059 / 0x1091 / 0x1092, --class-levelup, --class-level-max and !levelup
  now live in services/feprog.py with the rest of PROGRESSION (EXP-driven
  level-ups, skill points, GET!, Pw).  So do the 0x2028 regen ask and the
  0x1031/0x1032 skill-use announce this module used to log.

== PLAYER ITEM SLOTS ======================================================
  in  0x1171 (no client name; window title "Player Item Slots", 0x52ea30c)
        main arm 0x0505648f: `push 0x5338350 (the stream); call 0x051264d0` ->
        new 0x1f0-byte window, ctor 0x05126270 builds 96 (0x60) slot widgets
        (20 per row, 0x050b67d0) then 0x051263e0(stream) READS:
            [u32 count]  then count x { [u32 uid];  if uid != 0:
                                        [u16 itemNo][u8 wornMarker][u32 x] }
        per entry: malloc 0x4fc + item ctor 0x050737f0(uid, itemNo),
        [item+0x4c8] = wornMarker (0xff = not worn), widget slot i <- item
        (0x050b68a0(i, 0x1d, item, 0)); the fourth field is read (0x5045ef0,
        4 bytes) into [esp+0x18] and never used; entries stop at 96.
        SERVED behind `!slots` from the stored bag + worn map.  These are NEW
        item objects (display copies), not the bag's.

== THE c0 STUBS (server->client pushes; opened, named, NOT sent) ============
  every one is `push [esp+0x24]; push [esp+0x1c]; call W; ret 1` = W(unitId,
  stream) where unitId is the inner header's leading u32:
  0x1022 W=0x0503d6f0  [u16 v][u32 unused] -> [0x5339c14 +0x54] = v
         (the global at 0x5339c14 is the 0x5261fa4-vtable manager whose ctor
         sets +0x70 = 0xbb8, the item category).  Name unknown.
  0x102F W=0x0503c800  no body; unit lookup (class 3) -> vtable+0x58, +0x24,
         and if it is the local player: flags |= 0x11000800 (0x1000000 is
         the GHOST bit, fe-useskillrdy...) + effect 0x48 at the unit.  Sits
         right before 0x1030 ">MSG_CHANGE_GHOST_OK".  CHOSEN reading: the
         per-unit ghost notify.  Not sent by this module (feunit's state).
  0x1034 W=0x0503be70  no body; window-manager 0x5155f80 vtable+0x20(4, 0)
         = "refresh window kind 4".
  0x1035 W=0x0503c880  unit (class 3) then [u8 n] x { [u16 no][u8][u32]
         [u32 mask] + masked fields, cstr when bit 2 } -- the per-unit
         equipment/part record (feworld 6201 calls it the 0x1035 record's own
         player check).  doc-only.
  0x1036 W=0x0503cef0  unit (class 3) then [u8 n] x [u16 id] ->
         0x0506be20(unit, id): walks the list at [unit+0x8b4] for [node+0x1c]
         == id, ends that entry (vtable+4) and re-scans [unit+0xbc..0xc0].
         CHOSEN reading: "remove effects by id".
  0x1038 W=0x0503d000  [u16 mask] then per set bit i (16): [u16][u8][u8] ->
         0x5155fe0 vtable+0x28(i, u16, u8, u8) and vtable+0x18(i); then
         0x50ded70(0) and refresh kind 2.  The same 16-slot window 0x1041 /
         0x202A clear -- the SKILL PALETTE; this is the server-side SET
         (feworld pushes the palette as 0x2029, whose arm 0x0503a739 reads the
         same [u16][u16][u8][u8] shape).
  0x1039 W=0x0503d0d0  no body; refresh window kind 2.
  0x1041 W=0x0503d0f0  [u16 mask] -> per bit 0x5155fe0 vtable+0x30(bit), then
         0x50ded70(0), refresh kind 2.  Shared with 0x202A = palette CLEAR.
  0x1042 W=0x0503d150  no body; 0x50ded70(0) + refresh kind 2.
  0x106C W=0x0503d230  no body; unit (class 3) -> 0x0506c930: [unit+0x3b5]
         |= 2, flags |= 0x20000000, vtable+0x4c, and unless the unit is
         ghost/dead ([+0x2b4] & 0x1800000) the REDRESS loop 0x0507ba30.
         CHOSEN reading: "re-dress this unit".
  0x106D W=0x0503d250  bare `ret` -- a no-op on this build.
  0x106E W=0x0503d260  identical to 0x106C.
  0x1084/0x1085 c2 arms 0x05023c2f/0x05023c9f: struct {unit, id} (0x509fca0)
         -> [mgr+0xe8] window vtable+0x54, else the shop window [mgr+0xe0].
         The +0xe8 slot holds the kind-0x1b 0x7c-byte window (0x050a93ea,
         ctor 0x05097760, vtable 0x5284d2c) whose +0x54 is `ret 4`; the shop
         switch 0x050ee620 handles only 0x107B/0x107D/0x107E.  NEITHER reads
         the body or acts: these two ids are DISCARDED by the client.  Not
         serveable in any visible way.
  0x1073 MSG_GET_ACQUIRED_SKILL_LIST_OK (arm 0x050534ae) logs its name and
         returns 1; 0x1074 (arm 0x050534c8) logs "MSG_SHOP_PROCEDURE_START_NG"
         AND "MSG_GET_ACQUIRED_SKILL_LIST_NG" -- a copy-paste in the client;
         by position it is the acquired-skill-list NG.  Both log-only,
         confirmed.
  0x1150 MSG_ITEM_EXCHANGE_OK (main arm 0x05055dad) logs "> MSG_ITEM_EXCHANGE_OK"
         and returns; 0x1151 NG [u32 code] (0x05055dc8).  NO request found:
         no builder registers 0x1150 and the NG codes name an event TYPE and
         an "exchange condition number", so the request is a verb of the NPC
         event VM (0x1174, the open gap).  `!exchange` fires the OK/NG for a
         probe.

== UNNAMED OUTBOUND (decoded + logged; none registers a reply) ===============
  0x2097  no body  builders 0x050eca20 (after 0x4ff8510(0) = [scene+0x2ad]=0)
          and 0x050f0440 (after 0x4ff8510(1)), both in the shop windows
          right after 0x2073.  A shop-mode toggle notify.  Name unknown.
  0x2076  [32 bytes, cp932 name, NUL-padded]  builder 0x0505e860 (strncpy 32
          then 0x5045ad0 = write bytes), called from 0x05133c16 in the /tell
          window (0x2067 lives there).  Name unknown.
  0x2015  no body  builder 0x0505e8c0, called from 0x050aabe8/0x050aac02
          (a 1000.0f range check on the self unit, validate-screen family)
          and 0x05117bce (the proclamation window).  Name unknown.
  0x2014  no body  three builders: 0x0506b923 (after storing the target in
          [self+0x8dc] when the skill's table flags carry 0x80000),
          0x0507f400 (cast-context team check), 0x051181c7 (the truce arm,
          when the player is a ghost -- feworld's 0x1016 note).  Name unknown.
  0x2019  [u32][u32][u16 skillId][u8][u32 [ctx+0x3c]][u32][f32 x][f32 y][f32 z]
          builder 0x050725b6 off a cast context (getters 0x5021ef0 = the
          skill id, 0x505f4b0 = the skill table); logs "Invincible".  The
          CAST request shape; belongs with 0x1031/0x1032 below.  Logged.
  0x2045  no body  0x0507037a (ctor of a 0x52838f0-vtable object),
          0x0507fc70 ("Target is too close!"), 0x050e2cc9 ("Too far to
          summon.").  The body-less sibling of 0x2044 [u16 tbl+0xde][u8
          tbl+0xe0][u32 target] -> 0x106A/0x106B (0x5190da0 = the
          metamorphosis table).  feunit.py registers it as
          MSG_METAMORPHOSIS_RELEASE -- same reading, better name -- so this
          module does NOT register it.
  0x2081  [u32 crystalObjId]  builder 0x05152ca0: gated on [unit+0x8ac]
          (CRYSTAL) < 20, field status == 2, flags & 0x400, not ghost, 5 s
          cooldown, nearest kind-7 object (0x05066440) with [+0x778] > 0;
          sets flag 0x200 on the player.  "draw crystal from a formation".
  0x2082  no body  0x0507ec12 (unit class, then a 0x2010 placed-thing) and
          0x05153003 after a 0x2081.  "stop drawing".  Both crystal ids
          register nothing; crystal_push is feworld's.  0x2081 is only a
          log-only FALLBACK here: fecampaign shadows it and credits the draw
          (--crystal-war-max 20 / --crystal-carry-max 50, 2026-09-11).
  0x2028  the Pw regen ask -- ANSWERED by feprog.py since 2026-09-11.
  0x2020  [u32 clock][u32 clock][u32 selfId][u32 seq (0x504a460 counter)]
          [u32 [obj+0x3c]][u32 selfId]  builder 0x05070dda, on the 0x5339c14
          manager.  Name unknown.
  0x1033  [u32][u32][u16][u8][u32][u16 (float->int)][u16 (float->int)]
          [f32][f32][f32]  builder 0x0502d311, no direct caller (vtable).
          Name unknown.
  0xA00E  [u8 1][u32 selfId][u16]  builder 0x0506b857 after a squared-distance
          test; read 2026-08-27 as "per monster in aggro radius".
  0x1031 / 0x1032  BOTH directions.  Arm 0x0503bb70 (c0 0x0503a0e1/0x0503a0fd)
          reads [u32 a][u32 b][u8 hasPos][u32 targetObj][u32 e][u16 skillId]
          [u8 rung][u32 h][u32 i] (+ one u8 when the dispatcher's third arg is
          0, an uncertain u8) and, when hasPos == 1, [f32 x][f32 y][f32 z];
          then it gates skillId < 0x4a4 (SKILL_ARRAY_MAX), rung in 1..9, the
          header unit not flagged 0x800000, class 3, skill table [+0x1d8]
          flags -- THIS IS THE CAST RECORD, not the character record the 08-27
          sweep called it.  The client SENDS it once per cast -- feprog.py
          now reads it (skill id at body offset 17) to charge Pw.  0x1003 (the
          character record, arm 0x0503aae0) CRASHED live and is a DIFFERENT arm.

Knobs (all read with getattr; the harness builds args by hand):
  --enchant ok|ng|off (ok)      --enchant-cost N (0)
  --repair ok|ng|off (ok)       --repair-cost full|ok:N (full)
  --gather ng:CODE|ok:ITEM|off (ng:2)
  --items-log on|off (on)       -- the decode-and-log handlers
!verbs: !slots   !exchange [ok|ng CODE]   !enchants
"""
import struct

fw = None   # the feworld module, handed in by register()

# --- names -------------------------------------------------------------------
# Client's own names where the image has one; "(chosen)" where we picked it.
NAMES = {
    0x2077: "MSG_ENCHANT_REQUEST [u32 target uid][u32 enchant uid] -> 0x1122",
    0x2078: "MSG_ENCHANT_REPLY_OK [u32 RID] -> 0x1120/0x1121",
    0x2079: "MSG_ENCHANT_REPLY_NG [u32 RID] -> 0x1120/0x1121",
    0x209E: "MSG_GET_REPAIR_ITEM_COST_REQUEST [u32 uid] -> 0x115B/0x115C/0x112D",
    0x2086: "MSG_REPAIR_ITEM_REQUEST [u32 uid][u32 -1] -> 0x112C/0x112D",
    0x201F: "MSG_ITEM_GATHER_REQUEST (chosen) [u32 objectId] -> 0x1023/0x1024, registers nothing",
    0x2050: "MSG_GET_SELL_ITEM_VALUE_REQUEST (chosen) [u32 shopCtx][u32 uid]; answer = 0x1086",
    0x2097:"shop-mode toggle notify (unnamed) no body (0x050eca20 / 0x050f0440)",
    0x2076: "unnamed [char[32] cp932] from the /tell window (0x0505e860)",
    0x2015: "unnamed no body (0x0505e8c0; validate screen + proclamation window)",
    0x2014: "unnamed no body (target set / cast-context / ghost-at-truce)",
    0x2019: "cast-shaped request (unnamed) [u32][u32][u16 skill][u8][u32][u32][f32 x3]",
    0x2081: "draw crystal (unnamed) [u32 crystalObjId] (0x05152ca0)",
    0x2082: "stop drawing crystal (unnamed) no body",
    0x2020:"unnamed [u32 clock][u32 clock][u32 self][u32 seq][u32 obj+0x3c][u32 self]",
    0x1033: "unnamed client->server [u32][u32][u16][u8][u32][u16][u16][f32 x3] (0x0502d311)",
    0xA00E: "aggro-radius notice (sweep 08-27) [u8 1][u32 self][u16]",
}

#: server->client ids this module OPENED but does not send (see docstring).
PUSH_NAMES = {
    0x1120: "MSG_ENCHANT_OK (header-only, screen 0x050f5524)",
    0x1121: "MSG_ENCHANT_NG [u32 code] (screen 0x050f54b9)",
    0x1122: "MSG_ENCHANT_REPLY [u32 RID][u32 COST] (screen 0x050f53f2)",
    0x112C: "MSG_REPAIR_ITEM_OK (header-only, screen 0x050f116b)",
    0x112D: "MSG_REPAIR_ITEM_NG [u32 code] (screen 0x050f109e)",
    0x115B: "MSG_GET_REPAIR_ITEM_COST_OK (body UNMEASURED past [u32 uid]; screen 0x050f0f8c)",
    0x1023: "MSG_ITEM_GATHER_OK [u32 objectId] (c0 0x0503c2d0)",
    0x1024: "MSG_ITEM_GATHER_NG [u32 code]",
    0x1150:"MSG_ITEM_EXCHANGE_OK (header-only, logs)",
    0x1151: "MSG_ITEM_EXCHANGE_NG [u32 code]",
    0x1171: "PLAYER ITEM SLOTS window [u32 n] + n x {[u32 uid] ([u16 no][u8 worn][u32 x] if uid)}",
    0x1022: "set [0x5339c14 +0x54] [u16 v][u32 unused] (c0 0x0503d6f0)",
    0x102F: "per-unit GHOST notify (chosen): flags |= 0x11000800 + effect 0x48 (c0 0x0503c800)",
    0x1034: "refresh window kind 4 (c0 0x0503be70)",
    0x1035: "per-unit equipment/part record (c0 0x0503c880) doc-only",
    0x1036: "remove effects by id (chosen) [u8 n] x [u16] (c0 0x0503cef0)",
    0x1038: "skill palette SET (server form) [u16 mask] + per bit [u16][u8][u8] (c0 0x0503d000)",
    0x1039: "refresh window kind 2 (c0 0x0503d0d0)",
    0x1041: "skill palette CLEAR (server form) [u16 mask] (c0 0x0503d0f0, shared with 0x202A)",
    0x1042: "0x50ded70(0) + refresh kind 2 (c0 0x0503d150)",
    0x106C: "re-dress unit (chosen), no body (c0 0x0503d230 -> 0x0506c930)",
    0x106D: "no-op (`ret`) (c0 0x0503d250)",
    0x106E: "re-dress unit (chosen), same as 0x106C (c0 0x0503d260)",
    0x1084: "shop family, DISCARDED by the client (kind-0x1b window +0x54 is `ret 4`)",
    0x1085: "shop family, DISCARDED by the client",
    0x1073: "MSG_GET_ACQUIRED_SKILL_LIST_OK, log-only (0x050534ae)",
    0x1074: "MSG_GET_ACQUIRED_SKILL_LIST_NG (mislabelled SHOP_PROCEDURE_START_NG), log-only (0x050534c8)",
}

# the client's own NG codes we answer with (fe-ng-table.tsv)
ENCHANT_NG_UNDEFINED, ENCHANT_NG_NO_MATERIAL, ENCHANT_NG_CANCELLED = 0, 4, 7
ENCHANT_NG_NO_GOLD, ENCHANT_NG_EQUIPPED = 8, 13
REPAIR_NG_NO_GOLD, REPAIR_NG_FULL = 5, 6
GATHER_NG_NO_RIGHT = 2


def register(feworld):
    global fw
    fw = feworld
    fw.register_handler(0x2077, on_enchant_request)
    fw.register_handler(0x2078, on_enchant_reply_ok)
    fw.register_handler(0x2079, on_enchant_reply_ng)
    fw.register_handler(0x209E, on_repair_cost)
    fw.register_handler(0x2086, on_repair)
    fw.register_handler(0x201F, on_gather)
    fw.register_handler(0x2050, on_sell_value)
    # 0x2059 (level-up), 0x2028 (Pw regen) and 0x1031/0x1032 (skill use) are
    # feprog.py's since 2026-09-11 -- PROGRESSION acts on all four.
    # 0x2045 is feunit's (MSG_METAMORPHOSIS_RELEASE, the body-less sibling of
    # 0x2044 -> 0x106A/0x106B) -- the same reading as the docstring above,
    # with the better name, so it is not registered here.
    # 0x2019 left this list on 2026-09-12: it is the BUILDING HIT (the
    # building class's on-hit 0x050724E0 sends it -- fewar.on_building_hit)
    # 0x2015 is RETURN TO BASE (2026-09-12) -- it revives a dead player now
    fw.register_handler(0x2015, on_return_to_base)
    for mid in (0x2097, 0x2076, 0x2014, 0x2081,
                0x2082, 0x2020, 0x1033, 0xA00E):
        fw.register_handler(mid, on_log_only)
    fw.register_gm(gm)
    fw.register_args(add_args)
    for mid, name in NAMES.items():
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    ap.add_argument("--enchant", default="ok", choices=["ok", "ng", "off"],
                    help="0x2077/0x2078/0x2079 enchant handshake: ok = answer "
                         "0x1122 [RID][COST] then 0x1120 and consume the material; "
                         "ng = 0x1121 code 0 to the request; off = log only")
    ap.add_argument("--enchant-cost", type=int, default=0,
                    help="COST in the 0x1122 reply, debited from the stored "
                         "wallet when one exists (0 = free)")
    ap.add_argument("--repair", default="ok", choices=["ok", "ng", "off"],
                    help="0x2086 -> 0x112C (ok) + a 0x107A re-push carrying "
                         "--item-durability, or 0x112D code 5 (ng), or nothing")
    ap.add_argument("--repair-cost", default="full",
                    help="answer to 0x209E: `full` = 0x112D code 6 (durability "
                         "full; the measured one-u32 NG shape), `ok:N` = 0x115B "
                         "[u32 uid][u32 N] (CHOSEN shape, untested)")
    ap.add_argument("--gather", default="ng:2",
                    help="0x201F: `ng:CODE` = 0x1024 code, `ok:ITEM` = add item "
                         "ITEM to the bag + 0x1023 + MSG_DEL of the object, `off`")
    ap.add_argument("--items-log", default="on", choices=["on", "off"],
                    help="decode-and-log the unnamed outbound ids this module "
                         "opened (0x2097, 0x2076, ... 0xA00E)")


# --- small helpers -------------------------------------------------------------
def _p(msg):
    print("[feitems] " + msg, flush=True)


def _u32(f, o):
    return struct.unpack_from(">I", f, o)[0] if len(f) >= o + 4 else None


def _bag(args):
    return fw.item_rows(args)


def _equip(args):
    return [tuple(x) for x in (fw._load_char_field(args, "equip", []) or [])]


def _worn_uids(args):
    return {int(u) for _slot, u in _equip(args)}


def _row_of(args, uid):
    for i, r in enumerate(_bag(args)):
        if int(r[0]) == int(uid):
            return i, r
    return None, None


def _store_bag(args, rows):
    return fw._store_char_field(args, "items", [list(r) for r in rows])


def _remove_one(ctx, uid, why):
    """Take ONE of `uid` out of the stored bag and push the new layout -- the
    sell/use arms' path (feworld 10225.., 10130..) reused, not redone.
    Returns True when the store took it."""
    old = _bag(ctx.args)
    i, row = _row_of(ctx.args, uid)
    if row is None:
        return False
    new, gone = list(old), []
    if row[3] > 1:
        new[i] = (row[0], row[1], row[2], row[3] - 1)
    else:
        new = old[:i] + old[i + 1:]
        gone = [(int(uid), i)]
    if not _store_bag(ctx.args, new):
        _p("    %s: no stored character matched account=%r charid=%s -- the "
           "bag was NOT changed" % (why, ctx.session.get("account"),
                                    ctx.session.get("charid")))
        return False
    fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                       old, new, gone, why)
    return True


def _repush(ctx, uid, why):
    """Re-send one bag row through 0x107A (feworld.equip_reflect_push): the
    per-item reader 0x0503d280 re-reads every group-4 field including
    +0x4aa/+0x4ac durability, which is what makes this the repair."""
    i, row = _row_of(ctx.args, uid)
    if row is None:
        return 0
    worn = fw.worn_layout(_equip(ctx.args), _bag(ctx.args)).get(int(uid))
    return fw.equip_reflect_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be,
                                 ctx.args, [(row[0], row[1], i, worn, row[3])],
                                 why, kind="request")


def _gold(args):
    """The stored wallet, or None when no wallet is served (--gold unset)."""
    return fw._seeded_value(args, "gold", getattr(args, "gold", None))


def _debit(ctx, cost, why):
    """Spend `cost` gold. None = fine (free, or no wallet is served); False =
    not enough. Writes the store and pushes the wallet -- the first verb that
    DEBITS a _seeded_value, which its docstring asked for."""
    cost = int(cost or 0)
    if cost <= 0:
        return None
    gold = _gold(ctx.args)
    if gold is None:
        _p("    %s: cost %d NOT debited -- no wallet is served (--gold unset)"
           % (why, cost))
        return None
    if int(gold) < cost:
        return False
    if fw._store_char_field(ctx.args, "gold", int(gold) - cost):
        fw.wallet_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args)
        _p("    %s: gold %d -> %d (cost %d), wallet re-pushed"
           % (why, int(gold), int(gold) - cost, cost))
    return None


# --- ENCHANT -------------------------------------------------------------------
def on_enchant_request(ctx, inner):
    f = inner[2:]
    target, material = _u32(f, 0), _u32(f, 4)
    mode = getattr(ctx.args, "enchant", "ok")
    _p("<- 0x2077 MSG_ENCHANT_REQUEST target=%s enchant=%s (builder 0x050f4160; "
       "registers ONLY 0x1122)" % (target, material))
    if mode == "off":
        _p("    --enchant off: not answered -- the enchant window stays in "
           "its waiting state")
        return
    code = None
    if target is None or material is None:
        code = ENCHANT_NG_UNDEFINED
    elif _row_of(ctx.args, target)[1] is None:
        code = 3                                   # "enchant item not found"
    elif _row_of(ctx.args, material)[1] is None:
        code = ENCHANT_NG_NO_MATERIAL
    elif int(target) in _worn_uids(ctx.args):
        code = ENCHANT_NG_EQUIPPED
    elif int(target) == int(material):
        code = ENCHANT_NG_UNDEFINED
    if mode == "ng" and code is None:
        code = ENCHANT_NG_UNDEFINED
    if code is not None:
        ctx.reply(0x1121, struct.pack(">I", code),
                  why="MSG_ENCHANT_NG code=%d (window state 2 resets quietly)"
                      % code)
        return
    cost = max(0, int(getattr(ctx.args, "enchant_cost", 0) or 0))
    gold = _gold(ctx.args)
    if gold is not None and cost > int(gold):
        ctx.reply(0x1121, struct.pack(">I", ENCHANT_NG_NO_GOLD),
                  why="MSG_ENCHANT_NG code=8 not enough gold (%d < %d)"
                      % (int(gold), cost))
        return
    rid = (ctx.session.get("enchant_rid", 0) + 1) & 0xFFFFFFFF
    ctx.session["enchant_rid"] = rid
    ctx.session["enchant"] = {"rid": rid, "target": int(target),
                              "material": int(material), "cost": cost}
    ctx.reply(0x1122, struct.pack(">II", rid, cost),
              why="MSG_ENCHANT_REPLY RID=%d COST=%d -- the client logs "
                  "'>MSG_ENCHANT_REPLY RID = %%d,COST = %%d' and shows the "
                  "cost confirm; it answers with 0x2078 or 0x2079" % (rid, cost))


def _enchant_pending(ctx, rid, verb):
    pend = ctx.session.get("enchant")
    if not pend or rid is None or int(pend["rid"]) != int(rid):
        _p("    %s RID=%s does not match the pending enchant %s"
           % (verb, rid, pend and pend.get("rid")))
        return None
    return pend


def on_enchant_reply_ok(ctx, inner):
    rid = _u32(inner[2:], 0)
    _p("<- 0x2078 MSG_ENCHANT_REPLY_OK RID=%s (builder 0x050f41d0)" % rid)
    if getattr(ctx.args, "enchant", "ok") == "off":
        return
    pend = _enchant_pending(ctx, rid, "0x2078")
    if pend is None:
        ctx.reply(0x1121, struct.pack(">I", ENCHANT_NG_UNDEFINED),
                  why="MSG_ENCHANT_NG code=0 (no pending enchant for that RID)")
        return
    ctx.session.pop("enchant", None)
    if _debit(ctx, pend["cost"], "enchant") is False:
        ctx.reply(0x1121, struct.pack(">I", ENCHANT_NG_NO_GOLD),
                  why="MSG_ENCHANT_NG code=8 not enough gold")
        return
    _i, mrow = _row_of(ctx.args, pend["material"])
    if mrow is None or not _remove_one(ctx, pend["material"], "enchant material"):
        ctx.reply(0x1121, struct.pack(">I", ENCHANT_NG_NO_MATERIAL),
                  why="MSG_ENCHANT_NG code=4 material gone")
        return
    ench = fw._load_char_field(ctx.args, "enchants", {}) or {}
    key = str(pend["target"])
    ench[key] = list(ench.get(key, [])) + [int(mrow[1])]
    fw._store_char_field(ctx.args, "enchants", ench)
    ctx.reply(0x1120, b"", why="MSG_ENCHANT_OK (header-only, arm 0x050f5524 "
                               "reads nothing)")
    _p("    enchant recorded: target uid %d now carries %s (item numbers of "
       "the materials, store field `enchants`) -- WARNING: the client's enchant "
       "slots ride item field groups 2/3, which item_fields() sends EMPTY, so "
       "the target's displayed stats do NOT change" % (pend["target"], ench[key]))
    _repush(ctx, pend["target"], "enchant target re-push")


def on_enchant_reply_ng(ctx, inner):
    rid = _u32(inner[2:], 0)
    _p("<- 0x2079 MSG_ENCHANT_REPLY_NG RID=%s (builder 0x050f4240 -- the "
       "player cancelled, or the window auto-cancelled in state 2)" % rid)
    if getattr(ctx.args, "enchant", "ok") == "off":
        return
    _enchant_pending(ctx, rid, "0x2079")
    ctx.session.pop("enchant", None)
    ctx.reply(0x1121, struct.pack(">I", ENCHANT_NG_CANCELLED),
              why="MSG_ENCHANT_NG code=7 'Enchant cancelled.' (0/0: no dialog)")


# --- REPAIR --------------------------------------------------------------------
def on_repair_cost(ctx, inner):
    uid = _u32(inner[2:], 0)
    spec = str(getattr(ctx.args, "repair_cost", "full") or "full")
    _p("<- 0x209E MSG_GET_REPAIR_ITEM_COST_REQUEST uid=%s (registers 0x115B/"
       "0x115C/0x112D; window state 4)" % uid)
    if getattr(ctx.args, "repair", "ok") == "off":
        return
    if spec.startswith("ok:"):
        try:
            cost = int(spec[3:], 0)
        except ValueError:
            cost = 0
        ctx.reply(0x115B, struct.pack(">II", uid or 0, cost),
                  why="MSG_GET_REPAIR_ITEM_COST_OK [u32 uid][u32 cost] -- "
                      "PARTIAL: CHOSEN shape (0x1086's), the reader is unmeasured")
        return
    ctx.reply(0x112D, struct.pack(">I", REPAIR_NG_FULL),
              why="MSG_REPAIR_ITEM_NG code=6 'durability full, no repair "
                  "needed' -- this server tracks no wear; the arm names the "
                  "item from category 0xbb8 and closes the dialog")


def on_repair(ctx, inner):
    f = inner[2:]
    uid, minus = _u32(f, 0), _u32(f, 4)
    mode = getattr(ctx.args, "repair", "ok")
    _p("<- 0x2086 MSG_REPAIR_ITEM_REQUEST uid=%s second=%s (builder 0x050f13d4 "
       "writes a constant -1 there; registers 0x112C/0x112D)"
       % (uid, None if minus is None else struct.unpack(">i", struct.pack(">I", minus))[0]))
    if mode == "off":
        return
    if mode == "ng" or uid is None or _row_of(ctx.args, uid)[1] is None:
        ctx.reply(0x112D, struct.pack(">I", REPAIR_NG_NO_GOLD if mode == "ng" else 2),
                  why="MSG_REPAIR_ITEM_NG code=%d" % (REPAIR_NG_NO_GOLD if mode == "ng" else 2))
        return
    spec = str(getattr(ctx.args, "repair_cost", "full") or "full")
    cost = 0
    if spec.startswith("ok:"):
        try:
            cost = int(spec[3:], 0)
        except ValueError:
            cost = 0
    if _debit(ctx, cost, "repair") is False:
        ctx.reply(0x112D, struct.pack(">I", REPAIR_NG_NO_GOLD),
                  why="MSG_REPAIR_ITEM_NG code=5 not enough gold")
        return
    ctx.reply(0x112C, b"", why="MSG_REPAIR_ITEM_OK (header-only, arm 0x050f116b "
                               "only resets window state)")
    n = _repush(ctx, uid, "repair: 0x107A re-push carries +0x4aa/+0x4ac from "
                          "--item-durability (%s)" % getattr(ctx.args, "item_durability", "-1:100"))
    if not n:
        _p("    WARNING: the repair OK went out but NO 0x107A followed (not in a field, "
           "or --equip-reflect off) -- on screen nothing changed")


# --- GATHER --------------------------------------------------------------------
def on_gather(ctx, inner):
    obj = _u32(inner[2:], 0)
    # a MONSTER-DROP chest is feworld's (drop_pickup: rights, range, bag, the
    # announce=1 grant); anything else falls through to the probe below
    if obj is not None and fw.drop_pickup(ctx, obj):
        return
    spec = str(getattr(ctx.args, "gather", "ng:2") or "ng:2")
    _p("<- 0x201F MSG_ITEM_GATHER_REQUEST object=%s (builder 0x05152ba0, "
       "range-gated locally at [0x529700c]; registers nothing)" % obj)
    if spec == "off" or obj is None:
        return
    if spec.startswith("ok:"):
        try:
            item = int(spec[3:], 0)
        except ValueError:
            item = 0
        old = _bag(ctx.args)
        if item <= 0 or len(old) >= 96:
            # WARNING: 0x1024 is a [u16 code] (reader 0x5045ec0, `add edx,2`): as a
            # u32 every code arrived as 0 and showed no dialog (2026-09-12)
            ctx.reply(0x1024, struct.pack(">H", 1),
                      why="MSG_ITEM_GATHER_NG code=1 (bag full / no item)")
            return
        nxt = fw.new_item_uid(ctx.args, old)    # bag AND bank uids
        new = old + [(nxt, item, 1, 1)]
        if not _store_bag(ctx.args, new):
            ctx.reply(0x1024, struct.pack(">H", 0),
                      why="MSG_ITEM_GATHER_NG code=0 (store refused)")
            return
        fw.bag_layout_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                           old, new, [], "gather object %d -> item %d uid %d"
                           % (obj, item, nxt))
        ctx.reply(0x1023, struct.pack(">I", obj),
                  why="MSG_ITEM_GATHER_OK [u32 objectId] -> notice E24_GetItem "
                      "(or E25_GetCrystal when [tbl+0x1c8]==0x19)")
        ctx.reply(0x1004, b"", unit_id=obj,
                  why="MSG_DEL of the picked-up object (header id = the object)")
        return
    try:
        code = int(spec.split(":", 1)[1], 0)
    except (IndexError, ValueError):
        code = GATHER_NG_NO_RIGHT
    ctx.reply(0x1024, struct.pack(">H", code),
              why="MSG_ITEM_GATHER_NG code=%d -- feworld has no ground-drop "
                  "model, so nothing can be picked up yet" % code)


# --- SELL VALUE -----------------------------------------------------------------
def on_sell_value(ctx, inner):
    f = inner[2:]
    shop, uid = _u32(f, 0), _u32(f, 4)
    _p("<- 0x2050 MSG_GET_SELL_ITEM_VALUE_REQUEST shopCtx=%s uid=%s (sell window "
       "state 0, 0x050ef3c0: the uid in BAG SLOT 0; registers nothing)"
       % (shop, uid))
    if uid is None:
        return
    rows = [(u, v) for u, v in fw.sell_value_rows(ctx.args) if int(u) == int(uid)]
    if not rows:
        _p("    no sell value for uid %s (--sell-price 0, or not in the bag) -- "
           "not answered; the price stays whatever the shop-open burst set" % uid)
        return
    fw.sell_value_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, rows)


# --- PLAYER ITEM SLOTS ------------------------------------------------------------
def slots_body(rows, worn):
    """0x1171 the way 0x051263e0 reads it: [u32 n] then n x { [u32 uid];
    if uid: [u16 itemNo][u8 wornMarker][u32 x(unused)] }. Capped at 96 -- the
    ctor built exactly 0x60 widgets and the loop stops there."""
    rows = list(rows)[:96]
    body = struct.pack(">I", len(rows))
    for i, (uid, no, _flag, _count) in enumerate(rows):
        body += struct.pack(">I", int(uid) & 0xFFFFFFFF)
        if int(uid):
            w = worn.get(int(uid))
            body += struct.pack(">HBI", int(no) & 0xFFFF,
                                0xFF if w is None else (int(w) & 0xFF), i)
    return body


def slots_push(ctx):
    rows = _bag(ctx.args)
    worn = fw.worn_layout(_equip(ctx.args), rows)
    body = slots_body(rows, worn)
    ctx.reply(0x1171, body,
              why="PLAYER ITEM SLOTS window (%d row(s)) -- main arm 0x0505648f "
                  "opens 'Player Item Slots' (0x051264d0) and its ctor reads "
                  "the stream; each entry becomes a NEW display item object"
                  % len(rows))
    return len(rows)


# --- !verbs ---------------------------------------------------------------------
def gm(ctx, line):
    words = line.strip().split()
    if not words:
        return False
    verb = words[0].lower()
    if verb == "!slots":
        slots_push(ctx)
        return True
    if verb == "!exchange":
        if len(words) > 1 and words[1].lower() == "ng":
            try:
                code = int(words[2], 0) if len(words) > 2 else 0
            except ValueError:
                code = 0
            ctx.reply(0x1151, struct.pack(">I", code),
                      why="MSG_ITEM_EXCHANGE_NG code=%d (probe; the request is an "
                          "event-VM verb nobody has found)" % code)
        else:
            ctx.reply(0x1150, b"", why="MSG_ITEM_EXCHANGE_OK (header-only; the "
                                       "arm 0x05055dad only logs) -- probe")
        return True
    if verb == "!enchants":
        ench = fw._load_char_field(ctx.args, "enchants", {}) or {}
        _p("    stored enchants: %s" % (ench or "none"))
        return True
    return False


# --- decode-and-log ---------------------------------------------------------------
def _hex(b):
    return " ".join("%02x" % x for x in b)


def decode(mid, f):
    """A one-line reading of an unnamed outbound body, by the builder's
    write sequence (docstring). Returns text; never raises."""
    try:
        if mid == 0x2076:
            return "name=%r" % f[:32].split(b"\0")[0].decode("cp932", "replace")
        if mid == 0x2019:
            a, b, skill, r, c, d = struct.unpack_from(">IIHBII", f, 0)
            x, y, z = struct.unpack_from(">fff", f, 19)
            return ("a=%d b=%d skill=%d u8=%d ctx+0x3c=%d d=%d pos=(%.2f,%.2f,%.2f)"
                    % (a, b, skill, r, c, d, x, y, z))
        if mid == 0x2081:
            return "crystalObj=%d" % struct.unpack_from(">I", f, 0)[0]
        if mid == 0x2020:
            return "clock=%d clock=%d self=%d seq=%d obj+0x3c=%d self=%d" % struct.unpack_from(">IIIIII", f, 0)
        if mid == 0x1033:
            a, b, c, d, e, g, h = struct.unpack_from(">IIHBIHH", f, 0)
            x, y, z = struct.unpack_from(">fff", f, 19)
            return "a=%d b=%d u16=%d u8=%d u32=%d i16a=%d i16b=%d pos=(%.2f,%.2f,%.2f)" % (a, b, c, d, e, g, h, x, y, z)
        if mid == 0xA00E:
            k, self_id, v = struct.unpack_from(">BIH", f, 0)
            return "u8=%d self=%d u16=%d" % (k, self_id, v)
    except struct.error:
        pass
    return "raw=[%s]" % _hex(f) if f else "no body"


def on_return_to_base(ctx, inner):
    """0x2015, header-only, from the client's own Return to Base button
    (0x050AABB0). See feworld.return_to_base."""
    fw.return_to_base(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args)


def on_log_only(ctx, inner):
    if getattr(ctx.args, "items_log", "on") == "off":
        return
    mid = struct.unpack(">H", inner[:2])[0]
    _p("<- 0x%04X %s :: %s -- registers no reply; not answered"
       % (mid, NAMES.get(mid, "?"), decode(mid, inner[2:])))
