"""Entering an area: enter_area, the field-ready batch, the first position."""
import struct
import time
from . import character, equipment, inventory, itemrecords, monsters, sess, skilllist, spawns, war, wire, zones

def enter_area(conn, outbound, mode, be, args, group_id, area_id=0, room=None):
    """0x1000 MSG_ENTER_AREA_OK -- the REPLY to the client's 0x2000, and what
    finally sets the CURRENT ISLAND.

    WARNING: 2026-08-19 -- THIS IS NOT A SERVER PUSH. IT IS AN ANSWER.

    `0x1000` is registered as one of the two acceptable replies to an OUTBOUND
    request the CLIENT sends:

        C->S 0x2000 MSG_ENTER_AREA  [u32 areaId]      built 0x0505e600
        S->C 0x1000 OK  /  0x1001 NG                  registered 0x0505e62e

    The builder is the canonical `push <count>; push &ids; push <id>` shape this
    port has trusted everywhere else -- 0x0505e62e/0x0505e635 write ids[0]=0x1000
    and ids[1]=0x1001 -- and the client narrates the send as
    `Send : MSG_ENTER_AREA %d` (0x52d6564).

    THAT RETRACTS THE "STATICALLY UNREACHABLE" READING OF [player+0x295]. An
    earlier note recorded that the only writer of 0xFF to that byte, at 0x04ff6ada,
    had zero references in the image, and concluded the arming had to come from
    somewhere else entirely. 0x04ff6ada is not a function -- it is an instruction
    in the middle of one. Its function starts at **0x04ff6a30**, and that has two
    callers (0x04ff8d57, 0x04ff9710). The xref was taken against a basic-block
    address instead of the function head, which is why it came back empty.

    0x04ff6a30 is the enter-area DRIVER, polled once a frame, returning 0/1/2:

        [0x5336c54] == 0   first call: stash the area id at [0x52d24ec], call
                           0x0505e600 (SEND 0x2000), [player+0x4fd]=0,
                           [player+0x295] = [player+0x296] = 0xFF, state=1,
                           return 2 (pending)
        [0x5336c54] == 1   poll [player+0x295]:
                              0xFF -> return 2, still waiting
                              1    -> "ENTER AREA 受信" (received), return 0
                              0    -> "ENTER AREA 失敗" (failure),  return 1

    So the 0xFF is the client ARMING ITSELF as it sends the request. Pushing
    0x1000 unsolicited can never work by construction: the byte is 0, the
    handler's `cmp [player+0x295], 0xFF` fails, and it takes the bail path that
    logs `!!!異常なエンターエリアを受信しました。!!!`. That is exactly what the live
    2026-08-18 run recorded, and the cause was the push, not a missing trigger.

    Handler 0x04ff80d0, named by the client's own `>MSG_ENTER_AREA_OK`
    (0x52d5f6c) and dispatched as `id - 0x1000` (byte table 0x5057474 into jump
    table 0x5057444, slot 0).

        u8   a
        u8   b            -> fild'd to a float at [player+0x88]+0x9e4
        f32 x4            -> +0x9e8 +0x9ec +0x9f0 +0x9f4   (a position, unnamed)
        u8   entityCount
          entityCount x { u8 type, u32 id, f32, f32, f32, f32 }  (0x3b8 each)
        i16               -> [0x5336d1c+0x4e6]
        u32               -> [0x5336d1c+0x4e8]
        u32  groupId      <- 1..0x5F, resolved by 0x5009820
        u32  ?

    WARNING: IT ONLY WORKS ONCE. The handler requires [player+0x295] == 0xFF and sets it
    to 1 on the way through, so a second one takes the bail path and logs instead.
    Hence the once-per-connection guard at the call site.

    Everything except the group id is sent as zero: the four floats are a position
    whose units and origin are NOT established, and inventing values would put
    made-up coordinates into the record. Zero is at least a coordinate we can
    recognise if the client renders it.
    """
    # KEY: THE 0x1000 BODY IS A FIELD-STATE SNAPSHOT WITH TWO EMBEDDED SUB-RECORDS,
    # and omitting them is what CRASHED the client (2026-08-23, root-caused from
    # the errlog: c0000005 at fixed RVA 0xAC647). The handler 0x04ff80d0 reads,
    # in order (readers 0x5045dc0=u8 / 0x5045e60=u32 / 0x5045f20=f32):
    #
    #   [u8 a][u8 b][f32 x4]                         0x04ff8136..0x04ff8175
    #   <0x0503d330 sub-record>                      called 0x04ff820c
    #       [u8 count][u8 f1][u8 f2] then, if count>0, count entries -- EMPTY = 3
    #       zero bytes (count 0 -> the loop is skipped at 0x0503d39f).
    #   <0x0503c470 sub-record>                      called 0x04ff8233
    #       [u32 mask] then, per set bit, an object record it looks up/creates in
    #       the 0xbb8 manager. A MISS default-creates with def-key 0 -> item table
    #       [0x535cad8] has no id 0 -> [obj+0x4e8]=NULL -> the crash. EMPTY =
    #       mask 0 (exits after 4 bytes at 0x0503c4d5).
    #   [u8 entityCount][entityCount x entity]       0x04ff8242 + loop
    #   [i16 -> scene+0x4e6][u32 -> scene+0x4e8]     tail 0x04ff83b1
    #   [u32 groupId][u32 spare]                     0x04ff8400..
    #
    # feworld used to send [BB][f32x4][entityCount][hI][II] -- skipping BOTH
    # sub-records -- so the client consumed our entityCount+i16 as 0x503d330's
    # 3 bytes and then read our AREA u32 as the 0x503c470 mask (area 4 -> mask
    # 0x4, bit 2 set -> a bogus object -> crash; every area crashed the same way).
    # Emitting the two sub-records EMPTY realigns the whole body and lands a field
    # with no extra objects instead of crashing.
    #
    # The u32 at [player+0x4e8] is the AREA id and it round-trips (the leave-field
    # driver 0x04ff8d49 reads it back into the next 0x2000), so echo area_id.
    # THE ENTITY LIST -- [u8 count] then count x { u8 type, u32 id, f32 x4 }
    # (21 bytes each; the handler's loop 0x04ff8263 reads u8/u32/f32x4 and mallocs
    # 0x3b8 per unit, logging `Entrance Add (%d) -> [%d]:(%f %f %f)` at 0x52d2664
    # -- type, id, xyz; the 4th float is heading/spare). This is how units enter
    # the field. The field loaded but stayed BLACK (3D frame loop running, 0 2D/
    # HUD) because it has no player unit for the camera -- the FMO self-unit
    # pattern. Serve the player's OWN unit so the camera has a subject.
    # WARNING: EXPERIMENTAL: the self-identification (does id==charid mark it "self"?),
    # the type value, and the spawn position are all unmeasured -- knobs, iterate
    # live. Size is exactly 21 bytes/entity so the tail stays aligned.
    entities = b""
    n_ent = 0
    if args.self_unit != "none":
        charid = sess._SESSION.get("charid", 0) or 0
        x, y, z = args.self_xyz
        entities = struct.pack(">BIffff", args.self_type & 0xFF, charid,
                               x, y, z, args.self_heading)
        n_ent = 1

    body = struct.pack(">BB", 0, 0)                 # a, b
    body += struct.pack(">ffff", 0.0, 0.0, 0.0, 0.0)  # position/orientation
    if getattr(args, "field_items", "bag") in ("on", "bag"):
        items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
        equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
        # WARNING: LIVE 2026-09-05 #11: bag + worn gear together CRASHED field entry
        # at RVA 0xAC647 (0x0503c647, the worn-gear reader's equip-sound loop
        # reading the item's table entry [obj+0x4e8] = NULL) -- the 08-23
        # site. 'bag' serves the bag alone (worn mask 0) until that path is
        # understood; 'on' serves both.
        # WARNING: LIVE 2026-09-05 #14: the equip marker WORKS -- the gear appeared
        # on the character -- but the bag reader's Equip(uid, -1, 1) attaches
        # the model ON TOP of the default outfit (clipping) and leaves the
        # player UNABLE TO MOVE, in a capital as well as a battlefield.
        # Dressing is the AVATAR record's job (0x1006 mask3 parts, 13 slots
        # -- the channel other players already see gear through); the bag is
        # only a bag. --field-equip on restores the marker for a later read.
        # WARNING: 2026-09-08 run 4: this used to map uid -> the stored SLOT TYPE
        # (10 for a wand) where the marker wants the WORN INDEX (0) -- so the
        # 09-05 #14 run put the wand in worn 10 and body armour in 4 instead
        # of 3, which is the "over the default outfit, cannot move" it was
        # blamed for. worn_layout() is the one converter. And the marker is
        # THE field dressing: the bag reader (0x0503d330) looks each uid up
        # in the FIELD's item manager, constructs and REGISTERS it on a miss
        # (0x0503d44f -> 0x504cd00) and Equips it when the marker is not
        # 0xff -- the lobby builder's registrations belong to the select
        # scene's manager, which is why gate 4 read 'NOT IN MANAGER' for a
        # wand the unit was holding, and why the client's own unit stands
        # undressed at field entry with the markers all 0xff.
        worn = (equipment.worn_layout(equip, items)
                if getattr(args, "field_equip", "off") == "on" else {})
        # WARNING: run 7 (2026-09-08): with the wand marked worn here, the self
        # 0x1006 that follows found a weapon in the hand at apply time and its
        # tail (0x0503aead 0x0507be60 -> 0x0503aecb) set STANCE -- frozen again,
        # mask3 empty or not. Run 1 moved because the unit was BARE when that
        # record landed. So the bag record dresses everything EXCEPT worn 0/1;
        # the weapon comes by the 0x107A replay after the first clock sample,
        # whose path sets USESKILLRDY without STANCE (runs 1 and 4).
        if getattr(args, "add_self", "off") == "on":
            held = {u: w for u, w in worn.items() if w in (0, 1)}
            if held:
                print("[feworld]    bag markers: worn 0/1 (%s) left UNMARKED so "
                      "the self 0x1006's tail finds an empty hand (STANCE); the "
                      "weapon arrives by 0x107A after the first clock sample"
                      % ", ".join("uid %d" % u for u in sorted(held)), flush=True)
                worn = {u: w for u, w in worn.items() if w not in (0, 1)}
        if getattr(args, "field_items", "bag") != "on":
            equip = []                              # the worn-gear record crashed live #11
        body += itemrecords.bag_record(items, worn,             # 0x0503d330 sub-record: THE BAG
                           getattr(args, "field_item_kind", "both"))
        body += itemrecords.equip_record(equip, items)          # 0x0503c470 sub-record: WORN GEAR
        print("[feworld]    0x1000 carries the bag (%d item%s, slots 0..%d, %d "
              "marked worn) and %d worn-gear slot%s from the stored character"
              % (len(items), "" if len(items) == 1 else "s",
                 max(0, len(items) - 1), len(worn), len(equip),
                 "" if len(equip) == 1 else "s"), flush=True)
    else:
        body += struct.pack(">BBB", 0, 0, 0)        # 0x0503d330 sub-record: count=0 (EMPTY)
        body += struct.pack(">I", 0)                # 0x0503c470 sub-record: mask=0 (no objects)
    body += struct.pack(">B", n_ent)               # entityCount
    body += entities                                # count x 21-byte entity record
    # KEY: THE i16 IS THE ROOM INDEX, AND -1 MEANS "OUTDOORS". The field-entry
    # step branches on it three ways (0x04ff9d5e onward):
    #   [scene+0x4ec] in the 10 capital ids {21,39,57,62,78,91..95}
    #                     -> 0x5001d70: DATA\capital\mapNN_hit.oct into
    #                        [fieldmgr+0x8c]
    #   else i16 == -1    (tested by 0x4ff7ed0)
    #                     -> 0x04ff9de2: the Hmap arm -- fieldinfo[+0x94] picks
    #                        Data\Hmap\mapNN.pak, i.e. THE BATTLEFIELD
    #   else              -> 0x04ff9f0b: 0x5001cf0, an INDOOR ROOM --
    #                        Rooms\<name>.mdl/_hit.mdl chosen by i16 through the
    #                        name map 0x5192030, into [fieldmgr+0x88]
    # We were sending 0, so every "field" entry was really a ROOM entry: that is
    # why the live run loaded room_test.pak, and why [fieldmgr+0x8c] read NULL
    # (it is capital-only by construction -- not the bug it looked like). The
    # client's own ctor initialises +0x4e6 to -1 (0x04ff74a1/0x04ff74e8); our 0
    # was overwriting the outdoor default.
    # KEY: THE u32 THAT LANDS AT [scene+0x4e8] MUST NOT EQUAL THE GROUP ID.
    # Mode 0xa's branch is `if [scene+0x4e8] == [scene+0x4ec] -> DUMMY AREA`
    # (0x4ff7eb0 via 0x04ff9d03; the [scene+0x2ac] escape beside it is dead --
    # +0x2ac's only writer sets it to 1 and nothing clears it). +0x4ec is the
    # group id from this same message (0x4ff8482), and the client derives the
    # area it asks for in 0x2000 FROM the group id, so echoing the request makes
    # the two equal BY CONSTRUCTION and every entry dummies.
    #
    # And the dummy arm is a dead end for a server: it resets to mode 0xd
    # (0x4ff73e0) but does NOT set [scene+0x59], and mode 0xd substate 1 substep
    # 1 is exactly `cmp byte [scene+0x59], 0 / je` (0x04ff8cc0) -- it parks there
    # forever. +0x59 has no server-reachable writer; only the war-map driver
    # (0x5115565, 0x4ff8bc0) and mode 0xb (0x4ffa869) touch it. MEASURED LIVE
    # with `felive --scene`: mode 0xD, substates 0/1/1/0, +0x59 park, +0x295 and
    # +0x296 both satisfied, area 1 == field 1.
    #
    # -1 is the client's OWN initial value for +0x4e8 (ctor 0x4ff74a1 sets it
    # from `or eax,-1`), so "no current area" is a state it is built to hold, and
    # it makes the dummy test fail -> the real LOAD path -> the Hmap terrain.
    # WARNING: HYPOTHESIS, not measured: --area-echo echo restores the old behaviour.
    area_slot = area_id if args.area_echo == "echo" else int(args.area_echo)
    # `room` overrides the per-group table: the room EXIT (0x2000 area -1)
    # re-enters the same group forced outdoors, whatever --field-rooms says.
    room = zones.room_for(group_id, args) if room is None else int(room)
    sess._SESSION["room"] = room
    sess._SESSION.pop("room_exit_sent", None)   # a new area re-arms the exit
    # WARNING: THE REPORTED POSITION IS STALE THE MOMENT THE AREA CHANGES, and
    # `!npc` / `!build` place at exactly that position. Live 2026-09-06: a
    # `!goto room:10` followed straight away by `!npc` put the keeper at the
    # player's OUTDOOR coordinates (-269.5, -53.4) instead of inside the room,
    # where nobody could reach it -- in a room whose only way out is talking to
    # somebody. Dropping it makes both commands refuse ("no position yet")
    # until the client has reported one IN THE NEW AREA, which costs a single
    # step and cannot silently place an unreachable NPC.
    sess._SESSION.pop("cpos", None)
    # Same reasoning for the last door TOUCHED: `!link` reads it, and a door
    # from the area you just left is not a door you can stand in. Live
    # 2026-09-10: after `!link 21` warped the admin to 21, `!link` there
    # answered "that is the SAME door as side A (area 91)" -- because the only
    # door on record was still 91's. Dropping it makes the second `!link` say
    # the true thing: no door touched in this area yet.
    sess._SESSION.pop("last_door", None)
    body += struct.pack(">hi", room, area_slot)     # -> +0x4e6, +0x4e8
    body += struct.pack(">II", group_id, 0)         # THE GROUP, then a spare
    # KEY: THE BAG SIZE GOES FIRST. The item window reads it ONCE, in its own
    # constructor (0x050b884c), and the resize hook the applier calls
    # (0x050a6450) only notifies the shop/bank/trade windows at [mgr+0xe0/
    # 0xdc/0xd8) -- never the inventory. LIVE 2026-09-05: sent after the
    # entry the client APPLIED it (its log: `uPossessItemNum = 30`) and the
    # window still drew 0/0, because the window already existed with zero
    # rows. The player unit exists before this reply -- 0x1000's own bag
    # sub-record looks it up -- so the setter lands here too.
    inventory.bag_size_push(conn, outbound, mode, be, args)
    wire.send(conn, outbound, wire.inner_msg(0x1000, body), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    if group_id in zones.CAPITAL_GROUP_IDS:
        where = "CAPITAL loader (the id wins over room=%d)" % room
    elif room == -1:
        where = "OUTDOORS, the Hmap battlefield"
    else:
        where = "indoor ROOM %d (%s)" % (room, zones.room_label(room))
    print("[feworld] -> 0x30 inner 0x1000 MSG_ENTER_AREA_OK area=%d(+0x4e8=%d) "
          "group=%d entities=%d room=%d -> %s (self type=%d id=%s pos=%s)"
          % (area_id, area_slot, group_id, n_ent, room, where, args.self_type,
             sess._SESSION.get("charid") if n_ent else "-",
             args.self_xyz if n_ent else "-"), flush=True)


def arrival_dir(args, area=None):
    """The facing to enter `area` with: the door's own, else --spawn-dir.

    A DIRECTION VECTOR, never zero -- the client builds its look-at target as
    position + facing (0x05078e40), so a zero facing collapses the orientation
    (measured live 2026-08-25). SE's portal faces are 2D (x, z) and not always
    unit length ((-1, 1) ships), so they are normalised here.
    """
    if area is None:
        area = sess._SESSION.get("field")
    pend = sess._SESSION.get("arrive_face")
    if pend and area is not None and pend[0] == int(area):
        fx, fz = pend[1]
        n = (fx * fx + fz * fz) ** 0.5
        if n > 1e-6:
            return (fx / n, 0.0, fz / n)
    return tuple(args.spawn_dir_xyz)


def set_position(conn, outbound, mode, be, args):
    """0x1027 MSG_SET_POSITION -- THE SECOND HALF OF THE ENTER-AREA HANDSHAKE.

    KEY: Found 2026-08-23 from the live black-screen log: the client had NEVER
    reached the field's terrain branch, in any run. Mode 0xa is a 6-way switch on
    [scene+0x38] (head 0x04ff9666, jump table 0x04ff9fcc):

        3 -> 0x4ff9779  the asset chain (building.pak, monster00..07) -- COMPLETED
        2 -> 0x4ff9ca2  `cmp byte [scene+0x296], 1 / jne 0x4ff9fb7`  <-- PARKED
        5 -> 0x4ff9cf4  the capital/Hmap/room branch -- NEVER REACHED

    and the live log matches exactly: `ENTER AREA recv`, the asset paks, then
    nothing but the frame loop. Neither of mode 0xa's two terminal logs -- 0x52d2768
    "Field Entry : field entry complete" and 0x52d27ac "Field Entry : entered the
    dummy area" -- has ever appeared.

    [scene+0x296] has exactly ONE writer that sets it to 1: 0x050551ff, in the
    handler for id 0x1027, and feworld had never sent it. The enter-area driver
    0x4ff6a30 arms BOTH [scene+0x295] and [scene+0x296] to 0xFF; our 0x1000
    satisfies only +0x295 (0x4ff8181/0x4ff83f1). +0x296 is this message's job, so
    the 0x1000 round trip was only ever half the handshake.

    THE BODY IS EXACTLY SIX BIG-ENDIAN f32 AND NOTHING ELSE. The arm
    (0x5054d6e..0x5055212) makes exactly six stream calls, all 0x5045f20 (the f32
    reader, +4), and the client narrates it as
    `MSG_SET_POSITION受信 : pos(%f %f %f) dir(%f %f %f)` (0x52d5d94):

        [f32 x][f32 y][f32 z][f32 dirX][f32 dirY][f32 dirZ]      24 bytes

    The pos->slot mapping is not read off the printf (one of its six values is a
    hardcoded 10000.0 from 0x461c4000, so the argument order is misleading). It is
    read off the CONSUMERS: when a 0xE00D move is pending ([scene+0x84]==1) the
    handler overwrites the same three slots from [scene+0x74/0x78/0x7c] --
    0xE00D's own x,y,z -- at 0x5054e20, which pins stream floats 1,2,3 to x,y,z.
    They then land in [unit+0x1c4/0x1c8/0x1cc].

    GATES (both fail SILENTLY -- a miss is a drop, not a crash):
      * the leading header u32 must equal [ [scene+0x88] + 0x9e0 ], the client's
        own unit id, which 0x302B set from the u32 WE sent there. Both are 0
        today, so 0 matches by construction -- see inner_msg().
      * 0x507d570 (the local unit) must be non-null; the 0x1000 handler already
        dereferences [scene+0x88] without crashing, so it is live by this point.

    WARNING: UNMEASURED: the coordinate space, and whether dir is a unit vector or euler.
    Zeros are served by default -- a value we can recognise -- and the knobs exist
    to iterate live. What is MEASURED is the size (24 bytes) and the field order.
    """
    x, y, z = spawns.spawn_for(args)
    dx, dy, dz = arrival_dir(args)
    body = struct.pack(">ffffff", x, y, z, dx, dy, dz)
    wire.send(conn, outbound, wire.inner_msg(0x1027, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1027 MSG_SET_POSITION pos=(%g,%g,%g) "
          "dir=(%g,%g,%g) unit=%d -- sets [scene+0x296]=1, the flag mode 0xa "
          "substate 2 waits on before it will load the field"
          % (x, y, z, dx, dy, dz, wire.unit_id_of(args)), flush=True)


# ---------------------------------------------------------------------------
# WARNING: THE FIELD-READY BATCH, AND WHY IT USED TO NEED THE PLAYER TO MOVE
#
# Four things are held back until the field is really loaded: the war notify,
# the 0x107A worn-item replay (+ the motion set), the skill palette, and the
# `field_ready` flag fecampaign gates its keeps and buildings on. The trigger
# was "the client's first 28-BYTE 0x2023" -- and the client does not send one
# until the player MOVES.
#
# Measured live 2026-09-12 (prod feworld, one field entry):
#
#     17:42:35.760  -> 0x100E MSG_ADD_COMPLETE    the loading screen comes off
#     17:42:35.783  <- 0x209F                     +23 ms, from the SCENE UPDATE
#     17:42:35.822  <- 0x2080                     +62 ms, the distribution poll
#     17:42:38.754  <- outer 0x0002 keepalive     +3.0 s
#     17:42:42.045  <- 0x2023 28 B                +6.3 s  <-- the player moved
#     17:42:42.109  <- 0x2023 28 B                +64 ms, and ~400 ms after
#
# So for 6.3 seconds the character stood in the field in its BASE OUTFIT with
# the equipment clipping through it (the 0x107A replay is what dresses the
# rebuilt unit -- see --add-self), with an empty skill palette and with no
# keeps on the board. The live-test report is exactly that: "until I move my
# character... the hp and pw bars are empty, and the character's base outfit
# is clipping through their equipment. Once I move it's fixed."
#
# KEY: The client was never silent -- it sent 0x209F 23 ms in, and THAT one comes
# from the SCENE UPDATE (0x04ffa23c/0x04ffa3c0), i.e. the scene is already
# running. We were waiting on the single message that needs player input.
#
# The gate stays conservative about what it protects, because that is real:
# building.pak is mounted inside the field-load phase machine, and the 0x2029
# palette workers deref the HUD singletons with no null check. But our own
# note (in the 0x2000 handler) already says the mount finishes DURING field
# load -- before any clock sample -- and 0x100E is the message that says field
# load is done. So the grace is counted from 0x100E rather than from an input
# the player may never give. --field-ready-ms 0 restores the old trigger.
# ---------------------------------------------------------------------------
def field_ready_release(conn, outbound, mode, be, args, why):
    """Declare the field loaded and run everything held for it. Idempotent:
    each piece pops its own due-flag and `field_ready` is a set."""
    pending = (sess._SESSION.get("equip_replay_due")
               or sess._SESSION.get("palette_replay_due")
               or sess._SESSION.get("war_notify_deferred"))
    if sess._SESSION.get("field_ready") and not pending:
        return False
    if (sess._SESSION.pop("war_notify_deferred", False)
            and sess._SESSION.get("in_field")):
        print("[feworld]    %s -- sending the held war notify" % why,
              flush=True)
        war.war_notify(conn, outbound, mode, be, args)
    # THE FIELD IS NOW LOADED: building.pak is mounted, the HUD exists, the
    # item manager is up. Anything that places a field object whose models
    # live in a field-load pak must wait for this -- keeps do (see
    # fecampaign._keeps_present).
    if not sess._SESSION.get("field_ready"):
        # when, so --world-clock session can wait well past the entry fade
        sess._SESSION["field_ready_at"] = time.monotonic()
    sess._SESSION["field_ready"] = True
    rows = sess._SESSION.pop("equip_replay_due", None)
    if rows and sess._SESSION.get("in_field"):
        equipment.equip_reflect_push(conn, outbound, mode, be, args, rows,
                           "worn items into the FIELD's item manager, %s"
                           % why, "client")
        # ...and then the motion set, which the 0x107A path never selects:
        # the cast animation was a T-pose.
        monsters.motion_refresh_push(conn, outbound, mode, be, args, rows,
                            "after the 0x107A replay")
    if (sess._SESSION.pop("palette_replay_due", False)
            and sess._SESSION.get("in_field")):
        skilllist.palette_push(conn, outbound, mode, be, args, skilllist.palette_rows(args),
                     "field entry replay, %s (the HUD exists)" % why)
    return True


def field_ready_pump(conn, outbound, mode, be, args):
    """Release the field-ready batch once --field-ready-ms has passed since
    0x100E, without waiting for the player to move. Runs on the loop's idle
    tick and on every inbound message, so it fires whether this client is
    talking or not."""
    grace = float(getattr(args, "field_ready_ms", 1500) or 0)
    if grace <= 0 or sess._SESSION.get("field_ready"):
        return False
    t0 = sess._SESSION.get("add_complete_at")
    if not t0 or not sess._SESSION.get("in_field"):
        return False
    waited = (time.monotonic() - t0) * 1000.0
    if waited < grace:
        return False
    return field_ready_release(
        conn, outbound, mode, be, args,
        "%d ms after 0x100E (--field-ready-ms %d; NOT waiting for the player "
        "to move)" % (waited, grace))


def add_complete(conn, outbound, mode, be, args):
    """0x100E MSG_ADD_COMPLETE -- releases the LOADING-SCREEN park (mode 0xb).

    KEY: Found 2026-08-23 from the first-ever loading screen. `Field Entry : field
    entry complete` (0x4ff9f9a) sets scene MODE 0xb, whose handler (0x4ffa030)
    is an 11-way switch on [scene+0x39]:

        0 -> 0x4ffa074  self-unit ground snap (ray 0x5000450), then substate 1
        1 -> 0x4ffa209  waits on the group-detail round trip (0x5009780; the
                        client SENDS 0x4011 here -- the two requests in the
                        hang log) -> substate 2
        2 -> 0x4ffa0a3+ `cmp byte [scene+0x509], 1 / jne exit`  <-- THE PARK
        3 -> 0x4ffa318  a client-local fade ([0x5338154]+0x54 sets itself when
                        elapsed >= duration at 0x5039163) -> substate 4
        4 -> 0x4ffa37d  THE STEADY GAMEPLAY LOOP (the 0xE00D consumer)

    [scene+0x509] has exactly ONE server-reachable writer that sets it to 1:
    the dispatcher arm for id 0x100E (byte table 0x503a864 idx 4 -> 0x503a050),
    which logs `> MSG_ADD_COMPLETE` (0x52d4668), sets the flag, and reads NO
    body -- header-only, safe to repeat. In retail this is the server saying
    "the initial ADD stream (0x1003.. objects/entities) is complete; go live."

    WARNING: TIMING: the field-entry step CLEARS +0x509 (0x4ff9d64) before loading
    terrain, so a 0x100E sent with the 0x1000 burst gets wiped. The client's
    own post-entry 0x4011 (mode 0xb substate 1) is the safe trigger -- it can
    only happen after the clear.
    """
    wire.send(conn, outbound, wire.inner_msg(0x100E, b"", wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    # when the loading screen came off -- field_ready_pump counts its grace
    # from here, so the held batch does not need the player to move
    sess._SESSION["add_complete_at"] = time.monotonic()
    print("[feworld] -> 0x30 inner 0x100E MSG_ADD_COMPLETE (empty body) -- "
          "sets [scene+0x509]=1, releasing mode 0xb substate 2 (the loading "
          "screen)", flush=True)


def move_character(conn, outbound, mode, be, args, field_id):
    """Push 0xE00D -- the server's MOVE-CHARACTER order, THE reason the client
    exits cleanly after registering its field entry.

    Decoded 2026-08-23 from the world dispatcher's 0xE000-family block: ids
    0xE001..0xE00F dispatch through the byte table at 0x5057720 into the jump
    table at 0x5057704; 0xE00D's arm is 0x050572c7:

        [u32 token][u32 fieldId][f32 x][f32 y][f32 z]      all BIG-ENDIAN
          token   -> [scene+0x6c]   echoed back in the client's 0xE00F NACK
          fieldId -> [scene+0x70]   SAME id space as 0x4006/0x1000 (group id)
          x,y,z   -> [scene+0x74/0x78/0x7c]  ([scene+0x80] keeps its 1.0f)

    The arm is guarded on [scene+0x84] == 0 -- a second 0xE00D while one is
    pending is refused with C->S 0xE00F [u32 token][u32 0]. On accept it arms
    [scene+0x84] = 1, and when fieldId != [scene+0x4ec] (current field) also
    calls 0x4ff7f90 -> [scene+0x69] = 1.

    WHY THIS ENDS THE EXIT-AFTER-REGISTRATION: mode 0xd substate 2 substep 2
    (0x04ff8d49), the code that polls the enter-area driver, does this the
    moment the driver reports success (our 0x1000 accepted):

        cmp byte [scene+0x84], 1
        ==1 -> [scene+0x5c]=[scene+0x70]; [scene+0x4f0]=[scene+0x70];
               substate 4 -> mode 0xa  == GO TO THE ASSIGNED FIELD
        !=1 -> 0x516c340(2) -- request game phase 2 -- substate 3
               == the measured clean GameStart return

    So without a pending 0xE00D the client is DESIGNED to leave the field
    chain right after the 0x2000/0x1000 round trip. Mode 0xb (the in-area
    mode) is what finally consumes the order: same-field with the player
    within the 0x525f5bc radius of (x,y,z) just clears [scene+0x84]; farther
    away it copies the target to [scene+0x2d4..] and walks/warps the unit
    (0x4ff8020(1)); a different field re-enters the move chain (0x4ff7ed0).

    Coordinate units/origin are NOT established; (0,0,0) is served until a
    live run shows what the client does with it (--move-pos to override).
    """
    x, y, z = args.move_xyz
    body = struct.pack(">II", args.move_seq, field_id)
    body += struct.pack(">fff", x, y, z)
    wire.send(conn, outbound, wire.inner_msg(0xE00D, body), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0xE00D MOVE_CHARACTER token=%d field=%d "
          "pos=(%g,%g,%g) -- arms [scene+0x84]; the enter-area success check "
          "now continues INTO the field instead of returning to the Viewer"
          % (args.move_seq, field_id, x, y, z), flush=True)
