"""fewar.py -- Fantasy Earth WAR ENTITIES: build, destroy, entrance records,
the build-cost table, the clock byte, and the war-clock / validate leftovers.

PARTIAL: EVERYTHING HERE IS BUILT AND UNTESTED (2026-09-10). Nothing in this file has
been seen on a screen. Each send logs the client-side gate it must pass so a
silent no-op can be read off the server log rather than guessed at.

WHAT THIS MODULE SERVES (the "Group 2" message ids, RE'd 2026-09-10
against the runtime dump of FE_Client.dll, base 0x04F90000; every read
sequence below was taken off the arm by static disassembly and is quoted
with its VA):

  0x2007 MSG_BUILD_BUILDING_REQUEST   (override of feworld.UI_HEADER_ONLY_OK)
      request body, builders 0x05030db6 / 0x05036298 / 0x050729e9, all three
      the same shape (write helpers: 0x5045bb0 u16, 0x5045c40 u16,
      0x5045b80 u8, 0x5045ca0 f32, flush 0x5045a60):
          [u16 type +0x380][u16 model +0x382][u16 gx +0x388][u16 gy +0x38a]
          [u16 gz +0x38c][u8 +0x38e][f32 x +0x1c4][f32 y +0x1c8][f32 z +0x1cc]
      = 23 bytes. Registered replies (0x05030d9c): 0x1009 OK / 0x100A NG.
      0x1009 arm 0x05054bda: HEADER-ONLY (zero stream reads).
      0x100A arm 0x05054c06: [u32 code] (codes in fe-ng-table.tsv; 8 = not
      on a field, 9 = cost condition violated, 40 = build limit reached).
      A REAL build = the header-only OK, then the building itself as a
      0x1006 type-1 record (feworld.building_send, verified live via `!build`
      2026-09-05), then the crystal wallet as 0x2035 bit 1 (crystal_push)
      when a cost applies. The OK is an acknowledgement; the pushes are the
      mutation (rule 2).
      Gates the type-1 record must pass (0x0503b4ce..0x0503b4ea):
        [scene+0x4fd] != 0  -- set by 0x1000 ENTER_AREA_OK (0x04ff83e7)
        [0x5336f9c] != 0    -- the building registry, built at field load
      so "in a field" is the whole precondition.

  0x100C MSG_DESTROY_BUILDING_OK / 0x100D _NG   (pushes)
      arms 0x05054c6c / 0x05054c94: HEADER-ONLY. Each logs its own name
      ("Rcv : MSG_DESTROY_BUILDING_OK/NG", 0x52d5e54 / 0x52d5e34) and
      writes [scene+0x29d] = 1 / 0. A byte-access scan of the image finds NO
      READER of +0x29d -- those two stores are its only references -- so in
      this build the pair is inert beyond the log line. There is NO
      DESTROY_BUILDING request: no `push 0x2008..0x200B` exists, no code
      registers the (0x100C, 0x100D) pair, and the NG table has no
      MSG_DESTROY_BUILDING_NG rows. The only "destroy" the client can ask for
      is the GM debug form below. What removes the building on screen is
      0x1004 MSG_DEL (arm 0x0503a079, header-only, envelope id = the object;
      the shape feworld.mob_kill_push uses).

  0x208C MSG_DEBUG_COMMAND   (inbound, 12 builder sites 0x051356f7..)
      [u8 sub][u32 arg] -- sub 7 is "break building objID=%d" (0x051356f7,
      the only site with a name string). Subs 0x0f..0x18 and 0x1c exist with
      the same [u8][u32] head (their tails were not read). sub 7 is answered
      with 0x1004 MSG_DEL on the object + 0x100C; everything else is logged.

  0x1043 / 0x1044   ENTRANCE ADD / ENTRANCE REMOVE-BY-TYPE   (label: mine;
      the client's own word is "Entrance Add", 0x52d2664)
      0x1043 arm 0x05055212: [u16 count] then count x
          {u8 type, u32 id, f32, f32, f32, f32}
      -- EXACTLY the entity record 0x1000 ENTER_AREA_OK carries (feworld's
      set_position docstring: "Entrance Add (%d) -> [%d]:(%f %f %f)", 0x3b8
      B per node, ctor 0x5052f20, list at [0x5336f74]+0x68; the same ctor is
      called from the 0x1000 loop at 0x04ff82c6). The 4th float is stored at
      node+0x30; the node's Y is REPLACED by the terrain height
      0x5001240(x, z) at 0x050552e7, so the wire Y is not what renders.
      0x1044 arm 0x05055350: [u16 count] then count x {u8 type}; walks the
      list and destroys (vtable[0](1)) every node whose +0x18 == type.
      Both are gated `cmp word [esp+..], 0 ; jbe 0x5057423` -- a ZERO count
      is not handled at all. WARNING: The 08-27 sweep guessed "capture-point /
      crystal table"; the record identity says otherwise.

  0x1049   BUILD COST TABLE   (label: mine)
      arm 0x050553c5: [u16 count] then count x
          {u16 type, u8 crystal, u16 c, u16 gold}
      into the global std::map at 0x53458b8 (getter 0x5126ff0) keyed by the
      u16: node+0x10 = gold (0x05055554), +0x14 = c (0x0505569d),
      +0x18 = crystal (0x050557dd). The build UI reads it at 0x05032983..:
      `esi = [self+0x4ec] GOLD - [node+0x10]` and
      `edi = [self+0x8ac] CRYSTAL - [node+0x18]` -- the affordability check.
      +0x14's meaning is NOT read (a limit is the guess). Same zero-count
      gate. NO SHIPPED TABLE CARRIES THESE COSTS (FE_BUILDING_DATA's rows
      are name + icon; fe-fet-castle_info is castle positions), so the
      numbers are a knob and the server-side debit uses the same knob.

  0x104A   CLOCK OFFSET BYTE   (label: mine)
      arm 0x05055aef: [u8] -> [scene+0x524] and, via 0x4fec800, byte +0x11
      of the clock object at scene+0x510. Its reader 0x4fec863 does
      `[clock+0x11] * 0x24C3` (9411) as a 64-bit product added to the clock
      base (0x4ffef10) -- an offset in 9411 ms steps. What that shifts on
      screen (time of day is the guess: the object logs
      'TIME %d : %d / %4d.%4d %4d.%4d', 0x52d2068) is unmeasured.

  0x101A   war-map screen arm 0x05118367 (case 5 of the 0x1015..0x1029 switch
      at 0x05117e00, index 0x5118748 / table 0x511871c, reached through c3
      0x050580ae -> poster 0x509fc50, so the WAR MAP screen must be active):
          [u32 A][u32 B][u32 HI][u32 LO]
      Same four reads as 0x1018/0x1019 (0x0511823d / 0x0511843b), same
      deadline install (0x5111630), same per-army stores (0x5001200(0,A),
      (1,B)), same window rebuild (7,1) and the shared tail 0x51184d6 that
      sets [screen+0x61]=[+0x62]=1 (arms the war-prepare window). Differences:
      [view+0x70] = 0 (phase 0, like OFFENSIVE) and [screen+0x60] = 0
      (`sete ebp==0x1018` -- NOT offensive). A third start-war form with the
      offensive flag clear; the client gives it no name.
      0x1028 -> 0x05118202, the PEACE arm shared with 0x1017 (feworld
      war_peace documents it). 0x1029 -> 0x05118039, the NG/failure arm that
      CLEARS [screen+0x62] (feworld war_notify documents it). Both are
      feworld's; noted here only because the brief asked.

  0x1156 MSG_START_VALIDATE_INTERVAL_NOTIFY  (validate screen 0x50acec0 ->
      0x50ad0b0, table 0x050AD2B8 entry 2 = 0x050AD16F): [u32 HI][u32 LO],
      logged as '>MSG_START_VALIDATE_INTERVAL_NOTIFY %lld' (0x52e046c) and
      NOTHING ELSE -- log-only. Dropped unless the validate screen exists.
  0x1158 MSG_START_VALIDATE_SEQUENCE_CANCEL_NOTIFY (entry 4 = 0x050AD1EC):
      [u32], logged '%ld' (0x52e0410), then [unit+0x138c] = 0 (the countdown
      0x1154 armed), 0x50ad470 (close), and the system line
      "Field move cancelled." (0x52e03e8 via 0x5121fd0(3,..)). This is the
      server CANCELLING a Field Out; sending it also drops feworld's
      pending 0x1157 FINISH so the two cannot both fire.

  0x112A "MSG_GET_CRYSTAL_OK" -- WARNING: NAME UNCONFIRMED (ng-1 of 0x112B, which
      IS in the NG table). c3 ladder 0x05058354: `id - 0x1129`, index bytes
      0x5058cd4, table 0x5058cb8 -> case 1 = 0x050583f8. That arm allocates a
      0x104-byte window (ctor 0x5111f80: "Popup" 0x52e5f3c with "MenuButton"
      children 0x52e5f44) and 0x5112e50 reads
          [u16 count] then count x {cstr label, u16, u16, u16, u16, u16, u32}
      appending each as a 0x40-byte row (0x51133f0). The buttons' callbacks
      (0x5112bb0 / 0x5113330) call NO message builder. No code registers
      (0x112A, 0x112B) as a reply pair and no request builder for a
      GET_CRYSTAL was found (every beginMessage caller was enumerated), so
      this is served as an unsolicited POPUP push (`!warpopup`), rows with
      the five u16 and the u32 zero. The crystal pickup itself (walking onto
      a type-7 'Crystal' building) could not be tied to a request; what the
      five u16 / u32 select is unread. NOT crystal credit -- that stays
      feworld.crystal_push.

  0x2010   ATTACK / HIT NOTIFY (both directions; c0 0x0503a62e -> 0x503be90)
      [u32 attacker][u32 target][u16 skill][u8 level][u32][f32 x][f32 y]
      [f32 z][u32] -- first measured 2026-08-27, re-read
      here. NOT a placeable: the earlier "crystal/item on the ground"
      candidate is wrong; 0x505f4b0 is the SKILL row lookup (map 0x535cd88
      via 0x51b1a50), the header unit is looked up (0x504d350) and must be
      KIND-B 3 (0x0503bfd1), and a 0xBBC target gets its hit-reaction timer
      from [row+0x102]. The client sends it for its own swings
      (0x0507ea1e / 0x0507ed12); inbound is decoded and logged here, and
      `!swing` pushes one. No `!drop` -- see type 2 for the item on the
      ground.

  0x1006 type 2  ITEM ON THE GROUND  (arm 0x0503b7ff, label 'Item %d ')
      after the common [u16 count][u32 id][u8 type]:
          [cstr name][u16 item id] -> ctor 0x50737f0(objid, item) kind 0xBB8
          [u8 -> +0x3ae][f32 x +0x1c4][f32 y +0x1c8][f32 z +0x1cc]
      (0x0503b977..0x0503b9a4); y is then replaced by the terrain height
      (0x0503b9ba). The item row (0x50742d0) with [+0x1c8] == 0x19 gets a
      spawn effect. `!spawn2`.
  0x1006 type 7  SKILL EFFECT AT A POSITION  (arm 0x0503b9fe, unlabelled)
          [u32 caster][u32 b][u32 c][u16 skill][u8 level][f32 x][f32 y]
          [f32 z][u32 d]
      caster == [[scene+0x88]+0x9e0] (self) sets a "mine" flag; gated on the
      skill row existing (0x505f4b0, bails to 0x0503baf1 on a miss). `!spawn7`.

KNOBS (read with getattr; defaults are what prod does today unless the
client is waiting for the reply):
  --build {real,ack,off}   default real  -- 0x2007: place + debit / header
                           only (the pre-2026-09-10 behaviour) / hang it
  --build-costs SPEC       default off   -- "off" | "all=C[:G[:X]]" |
                           "TYPE=C[:G[:X]],..." (C crystal, G gold, X the
                           unread +0x14). Serves 0x1049 on field entry AND
                           is the debit the real build applies.
  --destroy {on,off}       default on    -- answer 0x208C sub 7
  --war-obj-base N         default 5000  -- object ids for !spawn2/!spawn7

!VERBS (--gmcmd-file):
  !destroy OBJ | !destroy ng CODE
  !entrance add TYPE ID [X Y Z [H]] | !entrance del TYPE
  !warcost push | !warcost SPEC
  !clockbyte N
  !warclock A B MS            (0x101A, deadline MS ahead in the client clock)
  !fieldout interval MS | !fieldout cancel [CODE]
  !warpopup LABEL|LABEL|...
  !swing ATTACKER TARGET SKILL [LEVEL]
  !spawn2 ITEMID [NAME] | !spawn7 SKILL [LEVEL]

NOT DONE: the GET_CRYSTAL request (not found -- see 0x112A); 0x20AD
MSG_RECOVER_PARAMETER_FROM_CRYSTAL_REQUEST (not in this list); the meaning
of 0x1049's +0x14 and of 0x104A's byte on screen; 0x208C subs other than 7.
"""
import struct
import sys
import time

fw = None   # the feworld module, handed in by register()

#: request -> the client's own reply names, arms measured 2026-09-10
BUILD_OK, BUILD_NG = 0x1009, 0x100A
DESTROY_OK, DESTROY_NG = 0x100C, 0x100D
MSG_DEL = 0x1004
ENTRANCE_ADD, ENTRANCE_DEL = 0x1043, 0x1044
BUILD_COST_TABLE = 0x1049
CLOCK_BYTE = 0x104A
WAR_START_3 = 0x101A
VALIDATE_INTERVAL, VALIDATE_CANCEL = 0x1156, 0x1158
POPUP = 0x112A
HIT_NOTIFY = 0x2010
BUILDING_HIT = 0x2019       # the building class's on-hit, read 2026-09-12
DEBUG_COMMAND = 0x208C
BUILD_REQUEST = 0x2007

#: 0x100A codes, from fe-ng-table.tsv (the client's own text beside each)
NG_TYPE_MISMATCH = 7    # メッセージと建物タイプが対応していません
NG_NOT_ON_FIELD = 8     # フィールド以外の場所に建物建設しようとしています
NG_COST = 9             # 建物建設のコスト条件に違反しています
NG_LIMIT = 40           # Build limit reached; no more.

#: 0x208C sub-commands seen at the 12 builder sites (only 7 has a string)
DEBUG_SUBS = {7: "break building objID=%d",
              0x0f: "(unnamed)", 0x10: "(unnamed)", 0x11: "(unnamed)",
              0x12: "(unnamed)", 0x13: "(unnamed)", 0x14: "(unnamed)",
              0x15: "(unnamed)", 0x16: "(unnamed)", 0x17: "(unnamed)",
              0x18: "(unnamed)", 0x1c: "(unnamed)"}


def register(feworld):
    global fw
    fw = feworld
    # Deliberate shadow of the UI_HEADER_ONLY_OK row: the header-only OK is
    # still sent, and the building follows it (see the docstring).
    fw.register_handler(BUILD_REQUEST, on_build, override=True)
    fw.register_handler(DEBUG_COMMAND, on_debug_command)
    fw.register_handler(HIT_NOTIFY, on_hit_notify)
    fw.register_handler(BUILDING_HIT, on_building_hit)
    fw.register_gm(gm)
    fw.register_args(add_args)
    fw.register_pump(pump)
    for mid, name in (
        (BUILD_REQUEST, "MSG_BUILD_BUILDING_REQUEST [u16 type][u16 model]"
                        "[u16 gx][u16 gy][u16 gz][u8][f32 x][f32 y][f32 z]"),
        (DEBUG_COMMAND, "MSG_DEBUG_COMMAND [u8 sub][u32 arg] (7 = break "
                        "building objID)"),
        (HIT_NOTIFY, "ATTACK/HIT notify [u32 attacker][u32 target][u16 skill]"
                     "[u8 lv][u32][f32 x3][u32] (c0 0x0503a62e)"),
        (DESTROY_OK, "MSG_DESTROY_BUILDING_OK (header-only; [scene+0x29d]=1)"),
        (DESTROY_NG, "MSG_DESTROY_BUILDING_NG (header-only; [scene+0x29d]=0)"),
        (ENTRANCE_ADD, "ENTRANCE ADD [u16 n]{u8 type,u32 id,f32 x4} (fewar)"),
        (ENTRANCE_DEL, "ENTRANCE REMOVE-BY-TYPE [u16 n]{u8 type} (fewar)"),
        (BUILD_COST_TABLE, "BUILD COST TABLE [u16 n]{u16 type,u8 crystal,"
                           "u16 c,u16 gold} (fewar)"),
        (CLOCK_BYTE, "CLOCK OFFSET BYTE [u8] -> [scene+0x524] (fewar)"),
        (WAR_START_3, "war-map arm 0x05118367 [u32 A][u32 B][u32 HI][u32 LO] "
                      "phase 0, offensive=0 (fewar)"),
        (VALIDATE_INTERVAL, "MSG_START_VALIDATE_INTERVAL_NOTIFY [u32 HI][u32 LO]"),
        (VALIDATE_CANCEL, "MSG_START_VALIDATE_SEQUENCE_CANCEL_NOTIFY [u32]"),
        (POPUP, "MSG_GET_CRYSTAL_OK? -- opens a Popup: [u16 n]{cstr,u16 x5,u32}"),
    ):
        fw.KNOWN.setdefault(mid, name)


def add_args(ap):
    ap.add_argument("--build", choices=("real", "ack", "off"), default="real",
                    help="0x2007 MSG_BUILD_BUILDING: 'real' answers OK and "
                         "then PUSHES the building (0x1006 type 1) at the "
                         "requested grid cell, debiting --build-costs; 'ack' "
                         "is the header-only OK prod served before 2026-09-10; "
                         "'off' leaves the dialog hanging (A/B only)")
    ap.add_argument("--build-types", default="costs", choices=("costs", "any"),
                    help="which building types 0x2007 will place: 'costs' = "
                         "exactly the ones in --build-costs (RoD's four, plus "
                         "the Gate of Hades), 'any' = every row the client "
                         "carries, which is what happened before 2026-09-12 "
                         "and let a DRAGON ALTAR go up for free -- a building "
                         "SE designed, shipped the data for and never enabled")
    ap.add_argument("--build-costs", default="off", metavar="SPEC",
                    help="0x1049 build-cost table: 'off', '2006' (Obelisk 15, "
                         "Arrow Tower 18, War Craft 20, Gate of Hades 20 -- "
                         "the 2006 sources' crystal costs), 'all=C[:G[:X]]' or "
                         "'TYPE=C[:G[:X]],...' (C crystal -> node+0x18, G gold "
                         "-> +0x10, X -> +0x14 unread). Pushed once per field "
                         "entry and used as the crystal debit of a real build. "
                         "No shipped table has these numbers.")
    ap.add_argument("--build-phase", choices=("war", "any"), default="war",
                    help="with --campaign on, 0x2007 builds only in a field "
                         "preparing for or at war, by a side of it (NG 15 "
                         "'Can't build here.'); 'any' = the old anywhere rule")
    ap.add_argument("--build-caps", default="2006", metavar="2006|TYPE=N,...",
                    help="per-side, per-war build caps (NG 40 'Build limit "
                         "reached'). 2006 (default) = the client's own "
                         "FE_BUILDING_DATA +0xe1: Obelisk 25, Arrow Tower 10, "
                         "War Craft 1, Gate of Hades 1 (once per war -- no "
                         "rebuild), which the 2006 wikis (Hordaine 建築物, "
                         "fewiki) state too. The old default was '4=1'")
    ap.add_argument("--build-hp", default="2006", metavar="2006|shipped|off|TYPE=HP,...",
                    help="hit points a BUILT war building is served with "
                         "(+0x778/+0x774). 2006 (default) = the Hordaine "
                         "wiki's 建築物 table (2006-05): Obelisk 9,500, Arrow "
                         "Tower 8,000, War Craft 18,000, Gate of Hades 4,000 "
                         "(after 4/25). shipped = FE_BUILDING_DATA +0xb0: the "
                         "same except Obelisk 12,000 and Gate 18,000 (the "
                         "4/14 value -- this client predates 4/25). off = "
                         "--building-hp for everything (the old 100:100)")
    ap.add_argument("--destroy", choices=("on", "off"), default="on",
                    help="answer 0x208C sub 7 (GM 'break building objID') "
                         "with 0x1004 MSG_DEL + 0x100C")
    ap.add_argument("--war-obj-base", type=int, default=5000, metavar="N",
                    help="first object id for !spawn2 / !spawn7 records")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------
def _log(msg):
    print("[fewar] " + msg, flush=True)


def _push(ctx, mid, body, unit_id, why):
    """One inner message with an EXPLICIT header unit (rule 4)."""
    fw.send(ctx.conn, ctx.outbound, fw.inner_msg(mid, body, unit_id),
            ctx.mode, ctx.be, ctx.args.seq_mode == "echo",
            getattr(ctx.args, "world_prefix", 4))
    _log("-> 0x30 inner 0x%04X %s (%d body bytes, header unit %d)"
         % (mid, why, len(body), unit_id))


def _cpos(ctx):
    pos = ctx.session.get("cpos")
    return tuple(float(v) for v in pos) if pos else None


def _next_obj(ctx):
    n = ctx.session.get("war_obj_n", 0)
    ctx.session["war_obj_n"] = n + 1
    return int(getattr(ctx.args, "war_obj_base", 5000)) + n


#: `--build-costs 2006`: the crystal costs the 2006 sources give, by the
#: client's own type ids (BUILDING_TYPES, data_FE_BUILDING_DATA): Obelisk
#: (6, 29) 15 [RoD-press]; Arrow Tower (0, 19) 18 and War Craft (5) 20
#: [FFSKY = early FEZ, the only source]; Gate of Hades (4) 20 [SE 4/14].
#: Gold costs existed (SE warsystem02: "クリスタルとゴールド") but no number
#: survives, so gold is 0.
#: 2026-09-11 (second pass): these are ALSO the client's own numbers --
#: FE_BUILDING_DATA +0xa4 (dat.pak, parser 0x051906f8) ships ArrowTower 18,
#: Obelisk 15, WarCraft / GateOfHades 20 -- and the Hordaine wiki's 建築物
#: (2006-05) agrees, Keep 0 (a keep is the 0xF501 declaration, never 0x2007).
COSTS_2006 = "0=18,19=18,5=20,6=15,29=15,4=20"

#: HP a built war building is served with, `--build-hp 2006`: the Hordaine
#: wiki 建築物 (2006-05): Obelisk 9,500, Arrow Tower 8,000, War Craft 18,000
#: (cut to 8,000 only in 2008), Gate of Hades 4,000 (18,000 at 4/14, 4,000
#: after 4/25).
BUILD_HP_2006 = {0: 8000, 19: 8000, 6: 9500, 29: 9500, 5: 18000, 4: 4000}
#: `--build-hp shipped`: FE_BUILDING_DATA +0xb0, the client's max-HP field
#: (decoded 2026-09-11): it agrees on Arrow Tower 8,000 and War Craft 18,000
#: and differs on Obelisk (12,000) and Gate of Hades (18,000, the 4/14 value).
BUILD_HP_SHIPPED = {0: 8000, 19: 8000, 6: 12000, 29: 12000, 5: 18000,
                    4: 18000, 1: 18000, 3: 18000, 2: 500, 8: 30000}


def build_hp(args, btype):
    """(cur, max) for a freshly built `btype`, or None = --building-hp."""
    spec = str(getattr(args, "build_hp", "2006") or "off").strip().lower()
    if spec == "off":
        return None
    if spec == "2006":
        table = BUILD_HP_2006
    elif spec == "shipped":
        table = BUILD_HP_SHIPPED
    else:
        table = {}
        for part in spec.split(","):
            k, _, v = part.strip().partition("=")
            try:
                table[int(k, 0)] = int(v, 0)
            except ValueError:
                continue
    hp = table.get(int(btype))
    if not hp:
        return None
    hp = max(1, min(32767, int(hp)))       # the hit channel is a u16 (0x2024)
    return (hp, hp)


def parse_costs(spec):
    """'off' -> None; '2006' -> COSTS_2006; else {type: (crystal, gold, x)}
    over BUILDING_TYPES."""
    spec = (spec or "off").strip()
    if not spec or spec.lower() == "off":
        return None
    if spec.lower() == "2006":
        spec = COSTS_2006
    default = None
    per = {}
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        k, _, v = part.partition("=")
        f = [int(x, 0) if x else 0 for x in v.split(":")]
        while len(f) < 3:
            f.append(0)
        tup = (f[0] & 0xFF, f[1] & 0xFFFF, f[2] & 0xFFFF)
        if k.strip().lower() == "all":
            default = tup
        else:
            per[int(k, 0)] = tup
    out = {}
    for t in fw.BUILDING_TYPES:
        if t in per:
            out[t] = per[t]
        elif default is not None:
            out[t] = default
    return out


def costs_of(ctx):
    """The live table: a `!warcost SPEC` override beats the flag."""
    spec = ctx.session.get("war_costs_spec")
    if spec is None:
        spec = getattr(ctx.args, "build_costs", "off")
    return parse_costs(spec)


# ---------------------------------------------------------------------------
# 0x1049 -- the build-cost table
# ---------------------------------------------------------------------------
def cost_table_body(costs):
    """[u16 count] then count x {u16 type, u8 crystal, u16 c, u16 gold} --
    arm 0x050553c5 read order 0x5045e30 / 0x5045dc0 / 0x5045e30 / 0x5045e30;
    the u8 lands at node+0x18 (crystal), the LAST u16 at +0x10 (gold)."""
    rows = sorted(costs.items())
    body = struct.pack(">H", len(rows))
    for t, (crystal, gold, x) in rows:
        body += struct.pack(">HBHH", t & 0xFFFF, crystal & 0xFF,
                            x & 0xFFFF, gold & 0xFFFF)
    return body


def cost_table_push(ctx, why=""):
    costs = costs_of(ctx)
    if not costs:
        _log("0x1049 not sent: --build-costs is off%s" % (" " + why if why else ""))
        return False
    _push(ctx, BUILD_COST_TABLE, cost_table_body(costs), fw.unit_id_of(ctx.args),
          "BUILD COST TABLE %d rows%s" % (len(costs), (" " + why) if why else ""))
    _log("   gate: arm 0x050553c5 drops a ZERO count (jbe 0x5057423); the "
         "build UI reads node+0x18 against CRYSTAL and +0x10 against GOLD "
         "at 0x05032983 -- open the build dialog and compare its numbers")
    ctx.session["war_costs_field"] = ctx.session.get("field")
    return True


def pump(ctx):
    """Once per inbound message: push the cost table on each field entry."""
    s = ctx.session
    if not s.get("in_field"):
        return
    if s.get("war_costs_field") == s.get("field"):
        return
    if costs_of(ctx):
        cost_table_push(ctx, why="(field %s entered)" % s.get("field"))
    else:
        s["war_costs_field"] = s.get("field")


# ---------------------------------------------------------------------------
# 0x2007 -> 0x1009/0x100A + 0x1006 type 1 + 0x2035
# ---------------------------------------------------------------------------
def decode_build_request(f):
    """The 23-byte body all three builders write (0x05030db6 / 0x05036298 /
    0x050729e9): u16 type, u16 model, u16 gx, u16 gy, u16 gz, u8, f32 x3."""
    if len(f) < 23:
        return None
    btype, model, gx, gy, gz, f38e = struct.unpack_from(">HHHHHB", f, 0)
    x, y, z = struct.unpack_from(">fff", f, 11)
    return dict(type=btype, model=model, gx=gx, gy=gy, gz=gz, f38e=f38e,
                x=x, y=y, z=z)


def on_build(ctx, inner):
    args = ctx.args
    req = decode_build_request(inner[2:])
    mode = getattr(args, "build", "real")
    if req is None:
        _log("0x2007 MSG_BUILD_BUILDING_REQUEST: %d-byte body, expected 23 -- "
             "%s" % (len(inner) - 2, inner[2:].hex()))
        if mode != "off":
            ctx.reply(BUILD_OK, b"", why="MSG_BUILD_BUILDING_OK (undecoded "
                      "body, acknowledged only)")
        return
    _log("0x2007 MSG_BUILD_BUILDING_REQUEST type=%d (%s) model=%d (%s) grid=(%d,"
         "%d,%d) f38e=%d at (%g, %g, %g)"
         % (req["type"], fw.BUILDING_TYPES.get(req["type"], "?"), req["model"],
            fw.BUILDING_MODELS.get(req["model"], "?"), req["gx"], req["gy"],
            req["gz"], req["f38e"], req["x"], req["y"], req["z"]))
    if mode == "off":
        _log("   --build off: NOT ANSWERED; the build dialog WILL HANG")
        return
    if mode == "ack":
        ctx.reply(BUILD_OK, b"", why="MSG_BUILD_BUILDING_OK (--build ack: "
                  "header-only, nothing placed -- the pre-09-10 behaviour)")
        return
    if not ctx.session.get("in_field"):
        ctx.reply(BUILD_NG, struct.pack(">I", NG_NOT_ON_FIELD),
                  why="MSG_BUILD_BUILDING_NG code 8 (not on a field: the "
                      "type-1 arm's gates [scene+0x4fd] / [0x5336f9c] "
                      "would drop the record)")
        return
    btype, model = req["type"], req["model"]
    if btype not in fw.BUILDING_TYPES or model not in fw.BUILDING_MODELS:
        ctx.reply(BUILD_NG, struct.pack(">I", 10),
                  why="MSG_BUILD_BUILDING_NG code 10 (type/model outside "
                      "the shipped tables)")
        return
    if fw.BUILDING_TYPES.get(btype) == "Keep":
        # the client sends a KEEP as 0xF501 (the war declaration, fecampaign)
        # -- its build commit 0x05030d40 branches on the type row's flag bit
        # 3 -- so a keep on 0x2007 is not a thing the client does
        ctx.reply(BUILD_NG, struct.pack(">I", NG_TYPE_MISMATCH),
                  why="MSG_BUILD_BUILDING_NG code 7 (a Keep is declared with "
                      "0xF501 CommandBuildKeepForWar, not built with 0x2007)")
        return
    # Grid: the request carries the building object's own grid cell; a
    # dialog-built request (0x05036298) may carry gy=0 and a world position
    # instead, so fall back to the floats when the cell is (0,0).
    gx, gz = req["gx"], req["gz"]
    if gx == 0 and gz == 0 and (req["x"] or req["z"]):
        gx, gz = fw.world_to_grid(req["x"]), fw.world_to_grid(req["z"])
    # THE WAR RULES (2026-09-11): only while this field is preparing for or
    # at war, only by a side of it, within the per-side caps, inside that
    # side's territory (fecampaign --build-territory)
    camp = sys.modules.get("fecampaign")
    side = None
    if camp is not None and hasattr(camp, "build_check"):
        code, side = camp.build_check(args, ctx.session.get("field"), btype,
                                      grid=(gx, gz))
        if code is not None:
            ctx.reply(BUILD_NG, struct.pack(">I", code),
                      why="MSG_BUILD_BUILDING_NG code %d (%s)" % (code, side))
            return
    # THE COST. Same knob the client saw in 0x1049, so both sides agree.
    costs = costs_of(ctx) or {}
    # KEY: AND THE COST TABLE IS THE BUILDABLE SET (2026-09-12, found live: a
    # tester put up a DRAGON ALTAR). A type absent from the table used to
    # fall through to cost 0 and get built FREE -- so every row the client
    # carries was buildable, including three SE never enabled (fewiki
    # 2006-05-26 lists アルターオブドリゴン and アルケミーラボ as 未実装;
    # the Observatory, the DefenseTower, the houses and the shops are town
    # and scenery rows) . RoD's buildable set is exactly Keep + Arrow Tower +
    # Obelisk + War Craft, plus the Gate of Hades from the 2006-04-25 patch
    # [SE warsystem02 names four player buildings + the castle; the client's
    # own tutorial book 61/114 names three]. --build-costs 2006 is that set,
    # so the table defines what may go up. --build-types any restores the old
    # behaviour (and an empty table never refuses anything, so --build-costs
    # off still builds).
    if costs and int(btype) not in costs             and getattr(args, "build_types", "costs") == "costs":
        ctx.reply(BUILD_NG, struct.pack(">I", NG_TYPE_MISMATCH),
                  why="MSG_BUILD_BUILDING_NG code %d (type %d %s is not in the "
                      "build-cost table, so it is not a building this war "
                      "offers -- RoD shipped four: Keep, Arrow Tower, Obelisk, "
                      "War Craft, + Gate of Hades from 4/25)"
                      % (NG_TYPE_MISMATCH, btype,
                         fw.BUILDING_TYPES.get(btype, "?")))
        return
    crystal_cost = costs.get(btype, (0, 0, 0))[0]
    have = None
    if crystal_cost > 0:
        have = fw._seeded_value(args, "crystal", getattr(args, "crystal", None))
        if have is None:
            _log("   cost %d crystal but the CRYSTAL channel is off (--crystal "
                 "unset): nothing to debit, building anyway" % crystal_cost)
        elif int(have) < crystal_cost:
            ctx.reply(BUILD_NG, struct.pack(">I", NG_COST),
                      why="MSG_BUILD_BUILDING_NG code 9 (costs %d crystal, "
                          "have %d)" % (crystal_cost, have))
            return
    # KEY: THE ID COMES FROM THE WAR, NOT THE SESSION (2026-09-12). It used
    # to be --building-base + a per-session counter, so the second builder in a
    # field allocated 3000 all over again and a hit on their tower addressed
    # the first builder's. fecampaign.building_add hands out the id from the
    # war's own sequence and registers the building so every session serves it
    # (fecampaign._buildings_present) and anybody can damage it.
    hp = build_hp(args, btype) or (0, 0)   # (0, 0) = --build-hp off, no channel
    obj = None
    if camp is not None and hasattr(camp, "building_add"):
        obj = camp.building_add(args, ctx.session.get("field"), side, btype,
                                model, (gx, gz), hp,
                                by=ctx.session.get("charid"))
    if obj is None:
        # no war row to own it (a --build real placement outside a war, or
        # fecampaign off): fall back to the old per-session id, and say so
        n = ctx.session.get("build_n", 0)
        obj = int(getattr(args, "building_base", 3000)) + n
        ctx.session["build_n"] = n + 1
        _log("   WARNING: no war row owns this building (campaign off, or not at "
             "war here): id %d is this SESSION's and nobody else will see it"
             % obj)
    ctx.reply(BUILD_OK, b"", why="MSG_BUILD_BUILDING_OK (header-only, arm "
              "0x05054bda) -- the building follows as 0x1006 type 1")
    # age 0 -- THE ONE PLACE THAT SHOULD SEND IT. This building is being
    # raised right now, so the client runs its own +0xac construction timer
    # (40 s for an Arrow Tower, 30 s for an Obelisk, 60 s for a War Craft)
    # and draws the site until it expires. Every other sender is re-placing
    # something that already stands and passes None = finished.
    fw.building_send(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args, obj,
                     btype, model, gx, gz,
                     why="(0x2007 real build; cost %d crystal%s)"
                         % (crystal_cost, ", %d HP (--build-hp)" % hp[0]
                            if hp[1] else ""), hp=hp or None, age_ms=0)
    # this session has it on screen already -- do not let the reconciler send
    # a second 0x1006 for the same object
    if obj is not None:
        ctx.session.setdefault("built_seen", {})[obj] = ctx.session.get("field")
    _log("   gates for the type-1 record: [scene+0x4fd] (set by 0x1000, "
         "0x04ff83e7) and [0x5336f9c] (registry, built at field load) -- both "
         "hold in a field; the client's oracle line is 'building TYPE=%d "
         "added at (x,y,z)' (0x52d46ec)" % btype)
    # side/area: feunit's summons read this registry -- a War Craft summons a
    # Giant for the side that BUILT it, in this field only
    ctx.session.setdefault("war_buildings", {})[obj] = dict(
        type=btype, model=model, gx=gx, gz=gz, cost=crystal_cost,
        side=side if side in ("atk", "def") else None,
        area=ctx.session.get("field"))
    if side in ("atk", "def"):
        n = camp.build_count(args, ctx.session.get("field"), side, btype,
                             grid=(gx, gz))
        _log("   %s side has now built %d of type %d this war" % (side, n, btype))
    # the war result's "Built" column (SE warsystem04) -- this session's own
    # count for this field visit, reset on field entry like every other tally
    fw.battle_tally("built", 1)
    if crystal_cost > 0 and have is not None:
        new = max(0, int(have) - crystal_cost)
        fw._store_char_field(args, "crystal", new)
        fw.crystal_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, args,
                        value=new)
        _log("   crystal %d -> %d (stored; 0x2035 bit 1 is a TOTAL, the "
             "client compares and only animates an INCREASE, 0x04fe4e9a)"
             % (have, new))


# ---------------------------------------------------------------------------
# destroy: 0x208C sub 7 / !destroy -> 0x1004 + 0x100C
# ---------------------------------------------------------------------------
def destroy_building(ctx, obj, why=""):
    fw.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, obj)
    _log("-> 0x30 inner 0x1004 MSG_DEL header unit %d (header-only arm "
         "0x0503a079; the envelope id IS the object)%s"
         % (obj, (" " + why) if why else ""))
    _push(ctx, DESTROY_OK, b"", fw.unit_id_of(ctx.args),
          "MSG_DESTROY_BUILDING_OK (header-only, arm 0x05054c6c: logs "
          "'Rcv : MSG_DESTROY_BUILDING_OK', [scene+0x29d]=1 -- NO reader)")
    b = ctx.session.setdefault("war_buildings", {}).pop(obj, None)
    ctx.session.setdefault("built_seen", {}).pop(obj, None)
    fw.battle_tally("destroyed", 1)       # the result window's "Destroyed"
    # 2026-09-12: the WAR owns the building. building_fell takes it off the
    # war's board (so it leaves everybody ELSE's screen on their next pump),
    # gives an obelisk's territory back, and charges its own side's base the
    # dots -- the three things this function used to do inline for the
    # builder's session only. The session copy above is still popped for
    # feunit's summon lookups.
    camp = sys.modules.get("fecampaign")
    if camp is not None and hasattr(camp, "building_fell"):
        fell = camp.building_fell(ctx.args, ctx.session.get("field"), obj,
                                  conn=ctx.conn, outbound=ctx.outbound,
                                  mode=ctx.mode, be=ctx.be)
        if fell is None and b is not None:
            _log("   building %d was this SESSION's only (no war row owned "
                 "it): nothing else to release" % obj)


def on_debug_command(ctx, inner):
    f = inner[2:]
    if len(f) < 1:
        _log("0x208C MSG_DEBUG_COMMAND with no body")
        return
    sub = f[0]
    arg = struct.unpack_from(">I", f, 1)[0] if len(f) >= 5 else None
    _log("0x208C MSG_DEBUG_COMMAND sub=%d (%s) arg=%s body=%s"
         % (sub, DEBUG_SUBS.get(sub, "UNSEEN sub"), arg, f.hex()))
    if sub != 7 or arg is None:
        return
    if getattr(ctx.args, "destroy", "on") != "on":
        _log("   --destroy off: logged only")
        return
    destroy_building(ctx, arg, why="(GM 'break building objID=%d')" % arg)


# ---------------------------------------------------------------------------
# 0x2010 -- attack/hit notify, inbound decode + !swing
# ---------------------------------------------------------------------------
def hit_body(attacker, target, skill, level, u32a, pos, u32b):
    """0x503be90 read order: u32 u32 u16 u8 u32 f32 f32 f32 u32."""
    return (struct.pack(">IIHBI", attacker & 0xFFFFFFFF, target & 0xFFFFFFFF,
                        skill & 0xFFFF, level & 0xFF, u32a & 0xFFFFFFFF)
            + struct.pack(">fff", *pos) + struct.pack(">I", u32b & 0xFFFFFFFF))


def on_hit_notify(ctx, inner):
    f = inner[2:]
    if len(f) < 27:
        _log("0x2010 HIT notify: %d-byte body, expected 27: %s" % (len(f), f.hex()))
        return
    attacker, target, skill, level, a = struct.unpack_from(">IIHBI", f, 0)
    x, y, z = struct.unpack_from(">fff", f, 15)
    b = struct.unpack_from(">I", f, 27)[0] if len(f) >= 31 else None
    _log("0x2010 HIT notify from the client: attacker=%d target=%d skill=%d "
         "lv=%d u32=%d at (%g, %g, %g) tail=%s -- its own swing (builders "
         "0x0507ea1e / 0x0507ed12); no reply is registered, nothing sent"
         % (attacker, target, skill, level, a, x, y, z, b))
    # a swing on a KEEP (2026-09-11): the campaign's buildings take it
    if getattr(ctx.args, "combat", "off") == "on" and hasattr(fw, "keep_hit"):
        fw.keep_hit(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args, target,
                    why="(0x2010)")


def on_building_hit(ctx, inner):
    """0x2019 -- THE BUILDING HIT (static analysis, 2026-09-12).

    The building class (kind 0x7D0, vtable 0x052838F0) answers a swing that
    reaches it in its on-hit slot 0x050724E0: attack object of kind 0x3E8,
    owned by the local player, the skill's relation gate 0x504A7C0 passed,
    250 ms cooldown per building -- and then it sends THIS, from builder
    0x050725B6:

        [u32 attacker][u32 attack seq][u16 skill][u8 hits][u32 building id]
        [u32 weapon uid][f32 x][f32 y][f32 z]          31 bytes

    Live 2026-09-12: 17 of them in one session, all at keep 2901, skills 270/
    285/330 -- while 0x2010 and 0xA011 stayed at ZERO. So this is the only
    thing a swing on a keep produces, and until today it was logged by
    feitems as "cast-shaped request (unnamed), not answered". Monsters send
    0xA011 from THEIR on-hit (0x05069700); avatars (kind 2, on-hit 0x050688A0)
    send nothing at all -- PvP damage cannot come from the victim's client.
    """
    f = inner[2:]
    if len(f) < 31:
        _log("0x2019 BUILDING HIT: %d-byte body, expected 31: %s" % (len(f), f.hex()))
        return
    attacker, seq, skill, hits, target, weapon = struct.unpack_from(">IIHBII", f, 0)
    x, y, z = struct.unpack_from(">fff", f, 19)
    _log("0x2019 BUILDING HIT: attacker=%d seq=%d skill=%d hits=%d building=%d "
         "weapon uid=%d at (%g, %g, %g)" % (attacker, seq, skill, hits, target,
                                            weapon, x, y, z))
    if getattr(ctx.args, "combat", "off") != "on":
        _log("   --combat off: logged only")
        return
    if hasattr(fw, "keep_hit") and fw.keep_hit(ctx.conn, ctx.outbound, ctx.mode,
                                               ctx.be, ctx.args, target,
                                               why="(0x2019)"):
        return
    _log("   building %d is not one of this session's keeps -- nothing takes "
         "damage (a giant crystal is DRAWN by crouching within 10 u during a "
         "war, 0x2081, never by hitting it)" % target)


# ---------------------------------------------------------------------------
# 0x1006 types 2 and 7
# ---------------------------------------------------------------------------
def item_ground_record(obj, name, item, pos, f3ae=0):
    """type 2, arm 0x0503b7ff: [u16 1][u32 obj][u8 2][cstr name][u16 item]
    [u8 +0x3ae][f32 x][f32 y][f32 z]."""
    return (struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 2)
            + name.encode("cp932", "replace") + b"\0"
            + struct.pack(">HB", item & 0xFFFF, f3ae & 0xFF)
            + struct.pack(">fff", *pos))


def skill_effect_record(obj, caster, skill, level, pos, b=0, c=0, d=0):
    """type 7, arm 0x0503b9fe: [u16 1][u32 obj][u8 7][u32 caster][u32 b]
    [u32 c][u16 skill][u8 level][f32 x][f32 y][f32 z][u32 d]."""
    return (struct.pack(">HIB", 1, obj & 0xFFFFFFFF, 7)
            + struct.pack(">IIIHB", caster & 0xFFFFFFFF, b & 0xFFFFFFFF,
                          c & 0xFFFFFFFF, skill & 0xFFFF, level & 0xFF)
            + struct.pack(">fff", *pos) + struct.pack(">I", d & 0xFFFFFFFF))


# ---------------------------------------------------------------------------
# 0x1043 / 0x1044 entrance records
# ---------------------------------------------------------------------------
def entrance_add_body(rows):
    """[u16 n] then n x {u8 type, u32 id, f32 x, f32 y, f32 z, f32 h} --
    arm 0x05055212 (0x5045e30, then 0x5045dc0 / 0x5045e60 / 0x5045f20 x4)."""
    body = struct.pack(">H", len(rows))
    for t, i, x, y, z, h in rows:
        body += struct.pack(">BIffff", t & 0xFF, i & 0xFFFFFFFF, x, y, z, h)
    return body


def entrance_del_body(types):
    """[u16 n] then n x {u8 type} -- arm 0x05055350."""
    return struct.pack(">H", len(types)) + bytes(t & 0xFF for t in types)


# ---------------------------------------------------------------------------
# the GM verbs
# ---------------------------------------------------------------------------
def gm(ctx, line):                 # return True if we took the line
    args = ctx.args
    words = line.split()
    if not words:
        return False
    verb = words[0].lower()
    rest = words[1:]
    try:
        if verb == "!destroy":
            if not rest:
                _log("!destroy wants OBJ, or `ng CODE`")
                return True
            if rest[0].lower() == "ng":
                code = int(rest[1], 0) if len(rest) > 1 else 0
                _push(ctx, DESTROY_NG, b"", fw.unit_id_of(args),
                      "MSG_DESTROY_BUILDING_NG (header-only, arm 0x05054c94; "
                      "[scene+0x29d]=0; code %d NOT on the wire -- the arm "
                      "reads nothing)" % code)
                return True
            destroy_building(ctx, int(rest[0], 0), why="(!destroy)")
            return True
        if verb == "!entrance":
            return _gm_entrance(ctx, rest)
        if verb == "!warcost":
            if rest and rest[0].lower() == "push":
                cost_table_push(ctx, why="(!warcost push)")
            elif rest:
                spec = " ".join(rest)
                parse_costs(spec)          # validate first
                ctx.session["war_costs_spec"] = spec
                _log("!warcost table -> %r (session override of "
                     "--build-costs)" % spec)
                cost_table_push(ctx, why="(!warcost)")
            else:
                _log("!warcost wants `push` or a SPEC")
            return True
        if verb == "!clockbyte":
            v = int(rest[0], 0) & 0xFF if rest else 0
            _push(ctx, CLOCK_BYTE, struct.pack(">B", v), fw.unit_id_of(args),
                  "CLOCK OFFSET BYTE %d -> [scene+0x524] and clock+0x11 "
                  "(x 0x24C3 = 9411 ms at 0x4fec863)" % v)
            return True
        if verb == "!warclock":
            if len(rest) < 3:
                _log("!warclock wants A B MS")
                return True
            a, b, ms = int(rest[0], 0), int(rest[1], 0), int(rest[2], 0)
            dl, kind = fw.war_abs_deadline(args, ms)
            body = struct.pack(">II", a & 0xFFFFFFFF, b & 0xFFFFFFFF)
            body += fw.pack_deadline(dl)
            # header unit 0 like feworld's 0x1015..0x1019 siblings
            _push(ctx, WAR_START_3, body, 0,
                  "war-map arm 0x05118367: A=%d B=%d deadline=%d (%s clock) "
                  "-- phase [view+0x70]=0, [screen+0x60]=0 (NOT offensive), "
                  "window (7,1), [screen+0x61]=[+0x62]=1. GATE: the WAR MAP "
                  "screen must be active (poster 0x509fc50 -> 0x05117e00)"
                  % (a, b, dl, kind))
            return True
        if verb == "!fieldout":
            return _gm_fieldout(ctx, rest)
        if verb == "!warpopup":
            labels = [s for s in " ".join(rest).split("|") if s != ""]
            if not labels:
                _log("!warpopup wants LABEL|LABEL|...")
                return True
            body = struct.pack(">H", len(labels))
            for s in labels:
                body += s.encode("cp932", "replace") + b"\0"
                body += struct.pack(">HHHHHI", 0, 0, 0, 0, 0, 0)
            _push(ctx, POPUP, body, fw.unit_id_of(args),
                  "'MSG_GET_CRYSTAL_OK'? -> Popup with %d MenuButton(s) (c3 "
                  "0x050583f8 -> ctor 0x5111f80 / reader 0x5112e50). GATE: "
                  "the window manager 0x50a4d60; an open popup at [mgr+0xf0] "
                  "is closed first. The five u16 + u32 per row are sent 0 "
                  "(unread)" % len(labels))
            return True
        if verb == "!swing":
            if len(rest) < 3:
                _log("!swing wants ATTACKER TARGET SKILL [LEVEL]")
                return True
            attacker, target, skill = (int(v, 0) for v in rest[:3])
            level = int(rest[3], 0) if len(rest) > 3 else 1
            pos = _cpos(ctx) or (0.0, 0.0, 0.0)
            _push(ctx, HIT_NOTIFY, hit_body(attacker, target, skill, level, 0,
                                            pos, 0), attacker,
                  "ATTACK notify attacker=%d target=%d skill=%d lv=%d at "
                  "(%g, %g, %g). GATES (0x503be90): skill row 0x505f4b0 must "
                  "exist; the HEADER unit (%d) must resolve (0x504d350) and "
                  "be KIND-B 3 (0x0503bfd1)"
                  % (attacker, target, skill, level, pos[0], pos[1], pos[2],
                     attacker))
            return True
        if verb == "!spawn2":
            if not rest:
                _log("!spawn2 wants ITEMID [NAME]")
                return True
            item = int(rest[0], 0)
            name = " ".join(rest[1:]) if len(rest) > 1 else "item%d" % item
            pos = _cpos(ctx)
            if not ctx.session.get("in_field") or pos is None:
                _log("!spawn2: not in a field / no 0x2023 position yet")
                return True
            obj = _next_obj(ctx)
            _push(ctx, 0x1006, item_ground_record(obj, name, item, pos), obj,
                  "MSG_ADD type 2 ITEM %r item=%d id=%d at (%g, %g, %g) -- "
                  "arm 0x0503b7ff, ctor 0x50737f0 kind 0xBB8; the client "
                  "logs 'Item %d already exists' (0x52d467c) on an id clash; "
                  "y is replaced by the terrain height (0x0503b9ba)"
                  % (name, item, obj, pos[0], pos[1], pos[2], obj))
            return True
        if verb == "!spawn7":
            if not rest:
                _log("!spawn7 wants SKILL [LEVEL]")
                return True
            skill = int(rest[0], 0)
            level = int(rest[1], 0) if len(rest) > 1 else 1
            pos = _cpos(ctx)
            if not ctx.session.get("in_field") or pos is None:
                _log("!spawn7: not in a field / no 0x2023 position yet")
                return True
            obj = _next_obj(ctx)
            caster = ctx.session.get("charid", 0) or 0
            _push(ctx, 0x1006, skill_effect_record(obj, caster, skill, level,
                                                   pos), obj,
                  "MSG_ADD type 7 SKILL EFFECT skill=%d lv=%d caster=%d id=%d "
                  "at (%g, %g, %g) -- arm 0x0503b9fe; GATE: skill row "
                  "0x505f4b0(%d) must exist or the record is dropped at "
                  "0x0503baf1; caster==self sets the 'mine' flag"
                  % (skill, level, caster, obj, pos[0], pos[1], pos[2], skill))
            return True
    except (ValueError, IndexError) as e:
        _log("%s: bad argument (%s): %r" % (verb, e, line))
        return True
    return False


def _gm_entrance(ctx, rest):
    if not rest:
        _log("!entrance wants `add TYPE ID [X Y Z [H]]` or `del TYPE`")
        return True
    op = rest[0].lower()
    if op == "del":
        types = [int(v, 0) for v in rest[1:]]
        if not types:
            _log("!entrance del wants TYPE...")
            return True
        _push(ctx, ENTRANCE_DEL, entrance_del_body(types), fw.unit_id_of(ctx.args),
              "ENTRANCE REMOVE types %s (arm 0x05055350 destroys every node "
              "with +0x18 == type; a zero count is dropped)" % types)
        return True
    if op == "add":
        if len(rest) < 3:
            _log("!entrance add wants TYPE ID [X Y Z [H]]")
            return True
        t, i = int(rest[1], 0), int(rest[2], 0)
        if len(rest) >= 6:
            x, y, z = (float(v) for v in rest[3:6])
        else:
            pos = _cpos(ctx)
            if pos is None:
                _log("!entrance add: no position given and none reported yet")
                return True
            x, y, z = pos
        h = float(rest[6]) if len(rest) >= 7 else 0.0
        _push(ctx, ENTRANCE_ADD, entrance_add_body([(t, i, x, y, z, h)]),
              fw.unit_id_of(ctx.args),
              "ENTRANCE ADD type=%d id=%d at (%g, %g, %g) h=%g (arm 0x05055212: "
              "the 0x1000 entity record again, 0x3b8-byte node, y replaced by "
              "the terrain height; a zero count is dropped)"
              % (t, i, x, y, z, h))
        return True
    _log("!entrance: unknown op %r" % op)
    return True


def _gm_fieldout(ctx, rest):
    if not rest:
        _log("!fieldout wants `interval MS` or `cancel [CODE]`")
        return True
    op = rest[0].lower()
    if op == "interval":
        ms = int(rest[1], 0) if len(rest) > 1 else 10000
        body = struct.pack(">II", (ms >> 32) & 0xFFFFFFFF, ms & 0xFFFFFFFF)
        _push(ctx, VALIDATE_INTERVAL, body, fw.unit_id_of(ctx.args),
              "MSG_START_VALIDATE_INTERVAL_NOTIFY %d ms (arm 0x050AD16F: "
              "logs '%%lld' and does NOTHING else). GATE: the validate "
              "screen 0x50acec0 must exist (a Field Out in progress)" % ms)
        return True
    if op == "cancel":
        code = int(rest[1], 0) if len(rest) > 1 else 0
        _push(ctx, VALIDATE_CANCEL, struct.pack(">I", code), fw.unit_id_of(ctx.args),
              "MSG_START_VALIDATE_SEQUENCE_CANCEL_NOTIFY %d (arm 0x050AD1EC: "
              "[unit+0x138c]=0, close 0x50ad470, system line 'Field move "
              "cancelled.'). GATE: the validate screen must exist" % code)
        if ctx.session.pop("validate_due", None) is not None:
            _log("   feworld's pending 0x1157 FINISH dropped -- a cancel and a "
                 "finish must not both fire")
        return True
    _log("!fieldout: unknown op %r" % op)
    return True
