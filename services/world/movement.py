"""Movement: who owns a position, unit speed, the move rows and jump physics."""
import struct
from . import sess, wire, zones

# 0x2023 action-0 record, BY OFFSET. Named because the first cut of
# --move-authority state1 wrote the state at 8 -- which is the tick's second
# u32 -- while the client reads it at 12. It never ran live (echo was the mode
# that shipped), but it would have corrupted the ticks AND left state at 0, and
# the result would have read as "state1 does nothing" rather than as a bug.
# Read order, arm 0x04fea890, each field at the width its reader advances:
_MV_ACTION = 0    # u32  -- the sub-dispatcher's 18-arm index; 0 = move
_MV_A = 4         # u32  -+ handed to 0x515ba10 -> 0x515b870 with the position
_MV_B = 8         # u32  -+
_MV_STATE = 12    # u16  -- ZERO makes 0x04feadd8 SKIP the movement controller
_MV_SPD = 14      # i16 x2 -> x0.001 (0x525f958) -> unit+0x4d4 / +0x4d8
_MV_POS = 18      # i16 x3 -> x0.1   (0x525f820) -> the target position
_MV_TAIL = 24     # u32  -- 0x5045ef0, four bytes
_MV_LEN = 28


def _i16(v):
    """Clamp to the signed 16-bit the client reads with `movsx`."""
    return max(-32768, min(32767, int(round(v))))


def _short_0x2023_dump(body):
    """Hexdump the SHORT 0x2023 -- a DIFFERENT message riding the same id.

    28 bytes is the movement heartbeat (action 0). The 13/14-byte forms come
    from other builders -- there are 16 `push 0x2023` sites -- and 14 fits
    [u32 action][u32][u32][u16]. Reading the action code off the wire beats
    deriving it from the other thirteen. First six only, then silence.

    Measured: `action=0xF  0000000f 0000b58d 0000c904 0001`. Its inbound arm
    (0x04fea815 -> 0x04fec400) gates on kind 2 exactly like action 0 and then
    measures the vector from the local player to that object -- so it is about
    OTHER units, not a movement request of ours.

    Called unconditionally, BEFORE --move-authority is consulted. It is
    read-only.
    """
    n = sess._SESSION.get("short_2023", 0) + 1
    sess._SESSION["short_2023"] = n
    if n > 6:
        return
    act = struct.unpack_from(">I", body, 0)[0] if len(body) >= 4 else None
    print("[feworld]    0x2023 SHORT FORM #%d: %d bytes, action=%s  %s"
          % (n, len(body), ("0x%X" % act) if act is not None else "?",
             body.hex()), flush=True)


def move_authority(conn, outbound, mode, be, args, body):
    """`0x2023` back at the client -- THE MESSAGE THAT GIVES THE PLAYER A SPEED.

    2026-08-25. The character renders and his animation plays, but he does not
    translate. Measured, seven consecutive `felive --avatar` samples with the
    player holding a movement key:

        pos [+0x1c4]     = (0.000, 15.948, 0.000)   identical in all 7
        [+0x4d4/+0x4d8]  = (0.0000, 0.0000)         identical in all 7
        slot0 [+0x2a4]    0 -> 8                    the walk animation started

    So input reaches the unit; nothing ever gives it a velocity.

    KEY: `0x2023` RUNS BOTH WAYS, and the receive side is the mover. Outbound it
    is the client's own report (builder 0x0515dd85, throttled 0.4s at
    [0x529755c]). Inbound, arm 0x0503a648 hands it to 0x04fea720 -- a
    SUB-DISPATCHER on a leading u32 ACTION CODE, 18 arms, table 0x4fea848 --
    and **action 0** is 0x04fea890, which writes both halves of the speed pair
    at 0x04feadcc and then drives the movement controller at [unit+0x920].

    THE TWO DIRECTIONS ARE THE SAME 28-BYTE RECORD, FIELD FOR FIELD:

        [u32 action]   client sends 0 -- and 0 IS the move arm
        [u32 a][u32 b] -> 0x515ba10 -> 0x515b870 with the position
        [u16 state]    if ZERO the controller is SKIPPED (0x04feadd8 `je`);
                       the speed pair is written either way
        [i16 spdA][i16 spdB]   -> x0.001 (0x525f958) -> +0x4d4 / +0x4d8
        [i16 x][i16 y][i16 z]  -> x0.1   (0x525f820) -> the target position
        [u32]          0x5045ef0, four bytes

    KEY: AND THE SCALES ARE EXACT INVERSES -- outbound multiplies the speed pair
    by 1000 (0x525f630) and the position by 10 (0x5261960); inbound multiplies
    by 0.001 and 0.1. WARNING: I claimed a 100x mismatch here earlier in the session
    and that was WRONG: I had paired the position's inbound constant with the
    speed pair. Both round-trip to 1.0.

    WHICH IS WHY THIS ECHOES THE CLIENT'S OWN BYTES, UNCHANGED. The record it
    sends is already a valid action-0 record -- same layout, same units, same
    leading zero. Relaying it invents no number, needs no unit conversion, and
    cannot be wrong about a scale. It is the smallest thing that can possibly
    move him, and if it does not, nothing about the units is to blame.

    WARNING: WHAT THIS IS NOT: authority. A real server would validate the move,
    clamp it to terrain and refuse a cheat; this agrees with whatever the
    client says. It is a probe for one question -- does the receive side move
    the player -- not a movement implementation.

    WARNING: ONLY THE 28-BYTE FORM. The client also sends 13/14-byte 0x2023 records
    from other sites; their layout is NOT decoded, and echoing an undecoded
    body would hand the sub-dispatcher a leading u32 that is some other action
    entirely. Those are counted and skipped.

    WARNING: NO LOOP: the client sends on its own 0.4s timer and does not re-send on
    receipt, so an echo per inbound frame stays at the client's own rate.
    """
    # WARNING: THE DUMP RUNS BEFORE THE OFF CHECK, ON PURPOSE.
    # It used to sit below it, so `--move-authority off` silently disabled
    # the diagnostic too -- and when the short form finally became the lead
    # worth chasing, the log had nothing in it and that silence read as
    # "the client stopped sending". Read-only logging must never sit behind
    # a knob it has nothing to do with. Self-inflicted 2026-08-25.
    if len(body) != _MV_LEN:
        _short_0x2023_dump(body)
    if args.move_authority == "off":
        return False
    if len(body) != _MV_LEN:
        sess._SESSION["move_skipped"] = sess._SESSION.get("move_skipped", 0) + 1
        if sess._SESSION["move_skipped"] % 50 == 1:
            print("[feworld]    --move-authority: SKIPPED a %d-byte 0x2023 "
                  "(x%d). Only the 28-byte form is decoded; the short ones "
                  "carry an undecoded leading u32 that the 18-arm "
                  "sub-dispatcher would read as some other action."
                  % (len(body), sess._SESSION["move_skipped"]), flush=True)
        return False
    out = bytearray(body)
    if args.move_authority in ("state1", "walk"):
        # WARNING: THE STATE IS AT 12, NOT 8. Offset 8 is the tick's second u32.
        # Writing it there corrupted the ticks and left the state at zero, so
        # the controller stayed skipped and the test would have read as
        # "state1 changes nothing" instead of as this bug. Caught before it
        # ran; it is why the offsets above are named.
        struct.pack_into(">H", out, _MV_STATE, 1)
    if args.move_authority == "walk":
        # WARNING: THIS MODE INVENTS NUMBERS, AND THAT IS THE POINT.
        # `echo` proved inert for a reason that is not about units: the client
        # reports speed (0,0) and state 0, because its outbound builder reads
        # the SAME +0x4d4/+0x4d8 the inbound arm writes. Nothing drives the
        # loop, so relaying it relays zeros. This mode stops relaying and
        # asserts a speed and a target AHEAD of the player, to answer one
        # question the intent channel is not needed for: does the receive side
        # move him at all?
        #
        # If he walks: 0x04fea890 IS the mover, and what remains is finding
        # what asks it to (the 13/14-byte 0x2023 is the suspect).
        # If he does not: action 0 is not the mover and nothing further should
        # be built on it.
        #
        # WARNING: WHICH speed component is which is UNMEASURED -- 0x0515bce0 only
        # ever sums them. --move-speed sets the FIRST (+0x4d4) and leaves the
        # second as the client sent it, so one knob moves one field.
        # WARNING: The axis is a guess too: +Z, because the facing we serve is
        # (0,0,1). If he moves along the wrong axis that is still a YES to the
        # question being asked.
        struct.pack_into(">h", out, _MV_SPD, _i16(args.move_speed * 1000.0))
        z = struct.unpack_from(">h", out, _MV_POS + 4)[0]
        struct.pack_into(">h", out, _MV_POS + 4,
                         _i16(z + args.move_dist * 10.0))
    seq = sess._SESSION.get("move_echo", 0) + 1
    sess._SESSION["move_echo"] = seq
    wire.send(conn, outbound, wire.inner_msg(0x2023, bytes(out), wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    if seq % 25 == 1:
        a, b, st, s1, s2, x, y, z = struct.unpack_from(">IIHhhhhh", out, 4)
        print("[feworld]    -> 0x2023 action 0 ECHO #%d  state=%d "
              "speed=(%.3f, %.3f) pos=(%.1f, %.1f, %.1f)  [a=%d b=%d]  "
              "-- read pos [+0x1c4] with felive --avatar, NOT the screen"
              % (seq, st, s1 * 0.001, s2 * 0.001, x * 0.1, y * 0.1, z * 0.1,
                 a, b), flush=True)
    return True


# 0x2024's applier, 0x04ff36c0: [u32 maskA][u32 maskB] then, per SET BIT in
# ascending order, that bit's own fields. 25 setters; the one this serves is
# maskA bit 0x20000 -> 0x04ff4670, which reads exactly ONE f32 (0x5045f20),
# resolves the unit, requires 0x04fcf8d0(unit) == 3 -- `movsx eax,word
# [ecx+0x20]`, MEASURED LIVE AS 3 ON THE PLAYER 2026-08-25 -- and stores the
# RAW float, unscaled, at [unit + (idx & 0xff)*4 + 0x4d4]. idx is 0 here, so it
# lands on the first half of the speed pair.
_U2024_SPEED_BIT = 0x00020000


def unit_speed_push(conn, outbound, mode, be, args):
    """`0x2024` maskA bit 0x20000 -- the write that gives the player a SPEED.

    2026-08-25. Three iterations went into `0x2023` before its gate was read:
    action 0 (arm 0x04fea890) resolves the target and branches on [obj+0x24],
    taking the speed-writing block ONLY for kind 2, and the player measures
    kind 1 -- so it bailed silently every time, whatever the record said.
    Action 0xF carries the same gate. The whole 0x2023 inbound family is for
    OTHER units.

    `0x2024` is a different message with a DIFFERENT gate, found from the call
    graph: only two addresses in the 0x04ff3000..0x04ff4700 setter cluster are
    called from outside it, and both are dispatcher arms -- 0x0503a69d
    (= 0x2024) -> 0x04ff36c0 and 0x0503a6d1 (= 0x2025) -> 0x04ff39a0.

    THE BODY, and it is 12 bytes:

        [u32 maskA = 0x00020000][u32 maskB = 0][f32 speed]

    \U0001f7e2 BOTH PRECONDITIONS ARE MEASURED, NOT ASSUMED:
      * the setter needs [unit+0x20] == 3 and felive read exactly 3 on the
        player (and 1 for [unit+0x24], which is why 0x2023 could never work);
      * the float is stored RAW -- `fld [esp+0xc] / fstp [esi+edx*4+0x4d4]`,
        no multiply -- so unlike 0x2023's i16 x0.001 there is no scale to get
        wrong.

    \u26a0 WHAT IS STILL A GUESS: the VALUE. What 1.0 means in this field is
    unmeasured; 0x0515bce0 sums +0x4d4 and +0x4d8 and scales the sum by a
    per-unit table for the animation rate, which says they are commensurate but
    not what the unit is. And whether a speed alone MOVES anything, or only
    feeds the animation while the controller at unit+0x920 still needs a
    target, is exactly what this probe asks.

    \u26a0 ONE BIT ONLY. maskA has 22 more setters and maskB two, each reading
    its own fields; a bit set with nothing behind it desynchronises the rest of
    the record. Widths for the other 24 are UNPINNED -- do not widen this mask
    without doing to each what was done to _AVATAR_SUB.
    """
    if args.unit_speed is None:
        return
    body = struct.pack(">IIf", _U2024_SPEED_BIT, 0, args.unit_speed)
    wire.send(conn, outbound, wire.inner_msg(0x2024, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    n = sess._SESSION.get("unit_speed_n", 0) + 1
    sess._SESSION["unit_speed_n"] = n
    if n <= 3 or n % 50 == 0:
        print("[feworld]    -> 0x2024 maskA=0x%X speed=%g  #%d  -- straight to "
              "[unit+0x4d4], unscaled. Read the SPEED PAIR and pos [+0x1c4] "
              "with felive --avatar."
              % (_U2024_SPEED_BIT, args.unit_speed, n), flush=True)


_U2024_BAGSIZE_BIT = 0x20000000


# ---------------------------------------------------------------------------
# MOVEMENT: what --unit-speed and --jump-phys ACTUALLY MEAN (static 2026-09-09)
#
# Both knobs are MULTIPLIERS on a shipped per-form table, not raw speeds, and
# the client picks the row from the METAMORPHOSIS form and the AREA:
#
#     speed = ([unit+0x4d8] + [unit+0x4d4]) * row[0]      (0x0515B8EE and
#                                                          0x0515B7E3/0x0515BCFD)
#     dash  = direction * row[2]                          (the horizontal)
#     up    = ([unit+0x4e0] + [unit+0x4dc]) * row[3]      (0x0504B640)
#     grav  = ([unit+0x4e8] + [unit+0x4e4]) * 24.5        (0x0504B680, whose
#                                                          non-player fallback
#                                                          IS 24.5 -- so 1.0 is
#                                                          the engine's own g)
#
# THE ROW is `0x5158E40`: for a class-3 object it is ([unit+0x3b4] & 0x3f) - 1
# through the byte map at 0x5159060; f28 = 0 (no metamorphosis, our case) falls
# out of range and takes the default arm, which asks 0x04FF7EF0 -- and that
# predicate is literally "am I in a capital": it indexes [world+0x4ec] - 21 and
# returns true for exactly 21, 39, 57, 62, 78, 91..95. CAPITAL_GROUP_IDS,
# arrived at from a third direction.
#
# WARNING: THE TABLE IS A GATHER, NOT A BLOCK COPY. 0x5158E40 lazily builds the
# runtime rows at 0x5349460 out of SCATTERED source dwords near 0x5297460 --
# dest +0x08 comes from source +0x10, and so on. Reading the source as four
# contiguous floats per row (the obvious thing) yields a table that is wrong in
# every row but the first, which is how "row[0xc] = 4.7" got written down.
# 4.7 is column [2], the horizontal dash. Column [3] is the launch.
_MOVE_ROWS = {           # row: (speed base, col1, dash, jump launch)
    0: (4.0, 4.0, 4.7, 16.5),     # war field, no metamorphosis
    1: (2.0, 6.0, 4.7, 9.9),
    2: (10.0, 10.0, 4.7, 13.2),
    3: (4.0, 9.5, 4.7, 16.5),
    4: (9.5, 8.5, 10.0, 21.45),
    5: (4.0, 4.0, 4.7, 11.55),
    6: (6.0, 6.0, 4.7, 16.5),     # CAPITAL, no metamorphosis
}


def move_row(args, area=None):
    """(row index, speed base, dash, jump launch) for where the player is.

    Only the f28 = 0 case, which is ours: we never serve a metamorphosis form
    (see _AVATAR_SUB's f28 note -- it swaps the player's model). Rows 1..5 are
    listed for completeness and are what a transformed character would get.
    """
    if area is None:
        area = sess._SESSION.get("field")
    row = 6 if area in zones.CAPITAL_GROUP_IDS else 0
    speed, _c1, dash, jump = _MOVE_ROWS[row]
    return row, speed, dash, jump


# VERIFIED KEY: THE 3x MEASURED LIVE IS 16.5 / 4.7 = 3.51.
#
# This block used to model the launch as `x4.7` and predict a 0.58 s flight for
# 1.5:1.0. LIVE 2026-09-08 run 4 measured SmartJumpMessage N ~2700 -- a ~2 s
# flight -- and the note written then said "the launch term in this model is
# low by ~3x". It was: the launch multiplier is row[3] = 16.5, and 4.7 is
# row[2], the HORIZONTAL dash, taken from a mis-assembled table.
#
#     1.5:1.0 -> launch 1.5*16.5 = 24.75 u/s, g 1.0*24.5 = 24.5 u/s^2
#             -> flight 2*24.75/24.5 = 2.02 s -> N = 700 + 2020 = 2720 ms
#
# against a measured ~2700. A static fix that lands within 1% of a number
# somebody already wrote down is worth more than the fix itself: it says the
# whole model (launch, gravity, the 300+400 ms immediates) is right and only
# the constant was wrong.
#
# The local jump handler 0x05152670 (single caller 0x051512ee) builds the dash:
#   horizontal = key direction (normalised, 0x04fd0d60) * row[2]
#   vertical   = 0x0504b640(unit, row[3]) = ([+0x4e0]+[+0x4dc]) * row[3]
#   gravity    = 0x0504b680(unit)         = ([+0x4e8]+[+0x4e4]) * 24.5
# then 0x05065240 flies that ballistic path in 0.1 s steps against the
# collision mesh and returns the flight time in ms (edi); the SmartJump is
# issued as THREE phases (0x05049730 args): 300 ms wind-up (push 0x12c at
# 0x05152b2c), edi ms of travel, 400 ms landing (push 0x190 at 0x05152b24;
# 0 while the key-state byte has bits 0/1), so the logged N = 300 + edi + 400
# and NO server field scales those two.
_U2024_JUMP_V_BIT = 0x00040000
_U2024_JUMP_G_BIT = 0x00080000
_JUMP_GRAVITY_BASE = 24.5        # 0x0504B680's own non-player fallback


def jump_flight_ms(args, v, g, area=None):
    """Predicted SmartJumpMessage N for a launch/gravity pair, in ms."""
    _row, _speed, _dash, jump = move_row(args, area)
    if g <= 0:
        return None
    return int(round((2.0 * v * jump / (g * _JUMP_GRAVITY_BASE)) * 1000)) + 700


def jump_phys_push(conn, outbound, mode, be, args):
    if args.jump_phys is None:
        return
    v, g = args.jump_phys
    body = struct.pack(">IIff", _U2024_JUMP_V_BIT | _U2024_JUMP_G_BIT, 0, v, g)
    wire.send(conn, outbound, wire.inner_msg(0x2024, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    row, _speed, _dash, jump = move_row(args)
    n = jump_flight_ms(args, v, g)
    print("[feworld]    -> 0x2024 JUMP PHYSICS +0x4dc=%g (x%g = %.2f u/s up) "
          "+0x4e4=%g (x%g = %.1f u/s^2), movement row %d (%s). Read the "
          "client's own 'SmartJumpMessage [..]->[..] N' lines: predicted "
          "N = %s ms including the 300+400 ms immediates."
          % (v, jump, v * jump, g, _JUMP_GRAVITY_BASE, g * _JUMP_GRAVITY_BASE,
             row, "capital" if row == 6 else "war field",
             n if n is not None else "-"), flush=True)
