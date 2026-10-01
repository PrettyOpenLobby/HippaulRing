"""Shared monsters: one copy per field, relayed between sessions, and their AI."""
import struct
import threading
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import character, combat, damage, death, ext, extrun, itemrecords, movement, sess, status, town, wire

# ---------------------------------------------------------------------------
# SHARED MONSTERS (2026-09-13). Until today every session built its OWN copy
# of a field's monsters (_SESSION["mobs"] = {} at field entry, then
# spawn_push), so two players in one field fought two different orcs in two
# different places, and one player's kill killed nothing on the other's
# screen. The design call: "we need to have monsters shared between players".
#
#   * the registry is the FIELD's (_FIELD_MOBS[gid]["mobs"]) and every
#     outdoor session in that field points _SESSION["mobs"] at the same dict,
#     so every reader (combat, chase, attack, wander, respawn) is unchanged;
#   * the first session in BUILDS it (register only, under _MOB_LOCK), and
#     every session -- the builder too -- is SHOWN it by mobs_replay_push:
#     each living monster where it is now (mob_pos), with the HP it has;
#   * ONE session drives a field's monsters (mob_driver: chase, wander,
#     respawn), toward the nearest LIVING player in the field
#     (field_players); _MOB_DRIVER_SECS of silence hands it over -- the
#     shape of fecampaign's tower driver;
#   * whatever one client is told about a monster the others in the field
#     are told too (the mob_* relays), applied only once that client has
#     been shown the field (mobs_shown): a type-8 stat record for an id the
#     client does not have would BUILD a new object with no position;
#   * a hit changes HP and death under _MOB_LOCK, so two players landing
#     the killing blow at once kill it once; reward, EXP and drop are the
#     killer's (combat_hit runs on the killer's thread).
# --monster-share off restores one copy per session.
# ---------------------------------------------------------------------------
_MOB_LOCK = threading.RLock()
_FIELD_MOBS = {}        # gid -> {"mobs": {obj: m}, "built": bool}
_MOB_DRIVER = {}        # gid -> (thread ident, monotonic of its last claim)
_MOB_DRIVER_SECS = 3.0


def mobs_shared(args):
    return str(getattr(args, "monster_share", "on") or "on") == "on"


def field_mobs(gid):
    """Field `gid`'s shared record: {"mobs": {obj: m}, "built": bool}."""
    with _MOB_LOCK:
        fm = _FIELD_MOBS.get(int(gid))
        if fm is None:
            fm = _FIELD_MOBS[int(gid)] = {"mobs": {}, "built": False}
        return fm


def _mob_mode(args):
    """'solo' (this session's own monsters: --monster-share off, a room, a
    capital, a test), 'wait' (attached to a field's monsters, not yet shown
    them) or 'shared'."""
    f = sess._SESSION.get("mobs_field")
    if f is None or not mobs_shared(args):
        return "solo"
    return "shared" if sess._SESSION.get("mobs_shown") == f else "wait"


def mob_driver(args):
    """True on the ONE session that moves this field's monsters. Whoever
    claims it keeps it while it keeps calling; _MOB_DRIVER_SECS of silence
    (a hang-up, a field change) hands it to the next caller."""
    gid = sess._SESSION.get("mobs_field")
    if gid is None:
        return True
    me, now = threading.get_ident(), time.monotonic()
    with _MOB_LOCK:
        d = _MOB_DRIVER.get(int(gid))
        if d and d[0] != me and now - d[1] < _MOB_DRIVER_SECS:
            return False
        _MOB_DRIVER[int(gid)] = (me, now)
    return True


def field_players(args):
    """[(session, (x, y, z))] for every LIVING player standing outdoors in
    this session's monster field -- this one and every other (ext_sessions,
    read-only)."""
    gid = sess._SESSION.get("mobs_field")

    def ok(s):
        return bool(s.get("in_field") and not s.get("player_dead")
                    and s.get("cpos") and s.get("room", -1) == -1
                    and (gid is None or s.get("field") == gid))
    out = [(sess._SESSION, sess._SESSION["cpos"])] if ok(sess._SESSION) else []
    if gid is not None and mobs_shared(args):
        for e in extrun.ext_sessions():
            s = e["session"]
            if s is not sess._SESSION and ok(s):
                out.append((s, s["cpos"]))
    return out


def mob_relay(args, kind, payload):
    """Tell every OTHER session showing this field's monsters what this one
    just told its own client. 0 in solo mode."""
    if _mob_mode(args) != "shared":
        return 0
    gid = int(sess._SESSION["mobs_field"])
    pl = dict(payload, field=gid)
    return extrun.ext_post(kind, pl, to=lambda s, _n: (s.get("mobs_shown") == gid
                                               and s.get("field") == gid))


def mob_show(conn, outbound, mode, be, args, obj, m, now=None):
    """Show monster `m` to this client as it is NOW: where it stands along
    its move, and its HP when it has taken damage."""
    x, y, z = mob_pos(m, now)
    town.npc_send(conn, outbound, mode, be, args, obj, m["type"], 0, m["level"],
             x, y, z, name=m.get("name", ""), type_id=m.get("type_id"),
             register=False, log=False)
    if m["hp"] < m["hpmax"]:
        combat.mob_stat_push(conn, outbound, mode, be, args, obj, m)


def mobs_replay_push(conn, outbound, mode, be, args, gid, built=False):
    """Show this client every LIVING monster of field `gid` as it is now,
    then open the relays to it (mobs_shown)."""
    now = time.monotonic()
    with _MOB_LOCK:
        items = sorted((sess._SESSION.get("mobs") or {}).items())
    n = dead = hurt = 0
    for obj, m in items:
        if m.get("dead"):
            dead += 1
            continue
        mob_show(conn, outbound, mode, be, args, obj, m, now)
        n += 1
        hurt += 1 if m["hp"] < m["hpmax"] else 0
    sess._SESSION["mobs_shown"] = int(gid)
    print("[feworld]    SHARED monsters, field %d (%s): %d shown where they "
          "stand now (%d hurt), %d dead awaiting respawn -- ONE set for every "
          "player in the field (--monster-share)"
          % (int(gid), "built by this session" if built else "joined",
             n, hurt, dead), flush=True)
    return n


def _mob_here(pl):
    return bool(sess._SESSION.get("in_field")
                and sess._SESSION.get("mobs_shown") == pl.get("field")
                and sess._SESSION.get("field") == pl.get("field"))


def _mob_relay_move(ctx, pl):
    """The driver moved a monster: the same 0x2023, verbatim (the client
    reads only END - START of its tick pair, so another clock is fine)."""
    if _mob_here(pl):
        wire.send(ctx.conn, ctx.outbound, wire.inner_msg(0x2023, pl["body"], int(pl["obj"])),
             ctx.mode, ctx.be, ctx.args.seq_mode == "echo",
             ctx.args.world_prefix)


def _mob_relay_hp(ctx, pl):
    m = (sess._SESSION.get("mobs") or {}).get(int(pl["obj"]))
    if _mob_here(pl) and m and not m["dead"]:
        combat.mob_stat_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                      int(pl["obj"]), m)


def _mob_relay_del(ctx, pl):
    if _mob_here(pl):
        combat.mob_kill_push(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                      int(pl["obj"]))


def _mob_relay_spawn(ctx, pl):
    m = (sess._SESSION.get("mobs") or {}).get(int(pl["obj"]))
    if _mob_here(pl) and m and not m["dead"]:
        mob_show(ctx.conn, ctx.outbound, ctx.mode, ctx.be, ctx.args,
                 int(pl["obj"]), m)


ext.register_relay("mob_move", _mob_relay_move)
ext.register_relay("mob_hp", _mob_relay_hp)
ext.register_relay("mob_del", _mob_relay_del)
ext.register_relay("mob_spawn", _mob_relay_spawn)


def status_speed(args, m):
    """What monster `m`'s statuses do to its speed (world/status.py): 1.0,
    a slow's fraction (Spider Web, Ice Javelin), or 0 while rooted or
    stunned. 1.0 under --status-effects off."""
    if not status.on(args) or not m.get("status"):
        return 1.0
    with _MOB_LOCK:
        return status.speed_factor(m)


def monster_speed_of(args, modeltype):
    """How fast THIS monster chases, in world units per second.

    --monster-speed run (the default) is the model's own RUN speed,
    NPC_ModelType +0x420 -- a chasing monster runs, and at exactly its run
    speed the client plays the run motion at rate 1.0 (fegamedata.model_speeds
    has the disassembly). `walk` is +0x41c. A number pins every monster to it,
    which is the pre-2026-09-12 behaviour. A model with no row falls back to
    the table's median run speed rather than to a chosen constant."""
    spec = str(getattr(args, "monster_speed", "run") or "run").strip().lower()
    if spec in ("run", "walk"):
        sp = fegamedata.model_speed(modeltype)
        if sp:
            return float(sp[1] if spec == "run" else sp[0])
        runs = sorted(v[1] for v in fegamedata.model_speeds().values())
        return float(runs[len(runs) // 2]) if runs else 3.0
    try:
        return max(0.1, float(spec))
    except ValueError:
        return 3.0


def _chase_clock_ms():
    """A monotonic millisecond counter for a monster move's START tick.

    Only END minus START is read by the client, as the move's DURATION
    (0x04FEAB38, `sub eax, ecx`, a 32-bit subtraction -- so a wrap between the
    two still yields the right difference). START is monotonic anyway, so the
    END the client keeps at [task+0x4c] never runs backwards between steps."""
    return int(time.monotonic() * 1000.0)


# WARNING: LIVE 2026-09-12: "an orc started slingshotting toward my player and back,
# doing damage and then being super far away". The server kept ONE position
# per monster, m["pos"], and wrote the move's DESTINATION into it the instant
# the 0x2023 went out -- while the client walks there over the move's duration
# (a wander is 1.4..14 s). And the client DISCARDS a move under 1.0 u
# (0x04FEAAD4) while the server still advanced its copy (a 1.5 u/s model at
# the 0.5 s tick steps 0.75 u -- logged at 22:46:17 that night). Either way
# the server's copy got AHEAD of the screen, and every next move starts from
# the client's CURRENT spot with the server's short duration -- so the client
# covers the whole gap in ~0.8 s (the slingshot), and swings were judged
# against the server's copy (every hit logged at exactly 6.0 u, the chase's
# stop band) while the model stood somewhere else.
#
# So: a monster's position is read ALONG its last move (mob_pos), and no move
# shorter than _MOB_MIN_STEP goes out -- nothing moves the server's copy that
# does not also move the client's.
_MOB_MIN_STEP = 1.2      # > the client's 1.0 u discard, with margin


def mob_pos(m, now=None):
    """Where the CLIENT has monster `m` now: part-way along its last 0x2023
    move, not at the move's end (m["pos"] stays the destination)."""
    mv = m.get("mv")
    if not mv:
        return m["pos"]
    (fx, fy, fz), (tx, ty, tz), t0, t1 = mv
    now = time.monotonic() if now is None else now
    if now >= t1 or t1 <= t0:
        return (tx, ty, tz)
    f = max(0.0, (now - t0) / (t1 - t0))
    return (fx + (tx - fx) * f, fy + (ty - fy) * f, fz + (tz - fz) * f)


def mob_move_note(m, target, dur_ms, now=None):
    """Record a 0x2023 move just sent: from where the monster is NOW to
    `target`, over `dur_ms` -- the same START/END the client got."""
    now = time.monotonic() if now is None else now
    m["mv"] = (mob_pos(m, now), tuple(target), now, now + dur_ms / 1000.0)
    m["pos"] = tuple(target)


def _chase_lead(args, every):
    """How far ahead a chase step reaches, in seconds: past the latest the
    next send can come (the chase interval plus one idle tick), with a
    quarter second of margin for a relay on another session's thread."""
    tick = max(0.0, float(getattr(args, "idle_tick_ms", 250) or 0) / 1000.0)
    return max(2.0 * every, every + tick + 0.25)


def monster_chase_tick(conn, outbound, mode, be, args, client_body=None):
    """Walk every aggro'd monster toward the player with 0x2023 action 0.

    WARNING: CREDIT WHERE IT IS DUE, AND A CORRECTION TO THIS FILE'S OWN COMMIT
    MESSAGE: monster_place_push already documented the second branch in 2026-08
    ("this is the FIRST TIME that arm has been aimed at a 0xBBB/0xBBC object").
    It is npc_walk_push's note alone that says "only kind 2". What is new here
    is aiming the arm at the DAT.PAK SPAWNS rather than at --monster, not the
    discovery of the branch.

    THE ARM ACCEPTS MONSTERS. Its kind gate (0x04FEA91E..0x04FEA93C) is

        call 0x4FCF8F0            ; the object's kind
        cmp eax, 2   ; je  -> the AVATAR branch
        cmp eax, 0xBBA ; jbe -> reject
        cmp eax, 0xBBC ; ja  -> reject
                                  ; 0xBBB / 0xBBC fall through to a SECOND
                                  ; branch the old reading missed entirely

    and 0xBBC is exactly what CFeClientNPCObject's ctor stamps on a monster.
    "Only kind 2" was an under-read of a two-branch gate, which is why nothing
    has ever tried to move a monster.

    WARNING: THE GATES, RE-READ 2026-09-11 (arm 0x04FEA890, dump base 0x04F90000) --
    and one of them was a RED HERRING that would have cost a live session:
      * `cmp edi, ebx(0); je bail` (0x04FEA900): the target unit must resolve
        from the 0x2023 header id. We send inner_msg(0x2023, .., obj) and the
        06:09Z first kill proved a monster exists under `obj` (0xA011 target
        1406), so this passes.
      * `0x504A4A0(obj) & 0x800000` (0x04FEA911): WARNING: THIS CANNOT BAIL A MONSTER.
        0x0504A4A0 returns [obj+0x2b4] ONLY when [obj+0x20]==3 (the player's
        class); for a monster ([+0x20] is 2/4/5/7) it returns 0, so the test is
        always false. The earlier note here -- "an airborne monster refuses
        every move", 0x800000 = no ground -- was WRONG: that flag is the
        PLAYER's, read through a getter that hands a monster a hard 0. Do not
        spend a run on it.
      * `0x5077EC0(obj, 0)` slot 0 of the +0x84 model array must be non-null
        and [model+0x27c] non-zero -- the model LOADED (0x04FEA945). A monster
        renders, so a model is loaded somewhere; whether slot 0 specifically
        carries [+0x27c] is the one gate still unmeasured on a served monster.
        THIS is the real remaining suspect.
      * `dist(target, [obj+0x1c4]) >= 1.0` (0x04FEA99..0x04FEAAD4, threshold
        0x525f5bc = 1.0f): a move whose target is under one unit from the
        monster's CURRENT client position is DISCARDED silently. A far chase
        step (~1.5u) clears it; the final approach steps as `d` nears `stop` do
        not, so a monster halts ~a unit short rather than never starting.
    So: gates 1/2 pass by construction; the live question is the model gate and
    whether the per-tick step actually clears 1.0 -- which the log below now
    measures, so "it didn't follow" becomes a number instead of a theory.

    WARNING:KEY: THE "TICK PAIR" IS A START AND AN END, AND THE CLIENT USES ONLY THEIR
    DIFFERENCE -- AS THE MOVE'S DURATION. Read 2026-09-12 off the monster
    branch, which the 08-26 note called UNREAD and which is a different path
    from the avatar branch (kind 2) that npc_walk_push drives:

        04feaadf  call 0x4fa8000 (0xa8)       ; allocate a MOVE TASK
        04feab0e  call 0x503e070              ; vtable 0x5261d98, [+0x3c]=0xa
        04feab2c  eax = u32 B ; ecx = u32 A
        04feab38  sub eax, ecx                ; DURATION = B - A (ms)
        04feab4d  call 0x4faf890              ; now
        04feab5e  [task+0x40] = now           ; start
        04feab74  [task+0x44] = now + (B-A)   ; end
        04feab6e  dist * 1000.0 / (B-A)       ; speed -> motion 5 (walk) or 8 (run)

    This used to copy the client's own heartbeat pair in, on the theory that
    "a guessed timebase is a desync". But that pair is the HIGH and LOW half of
    the client's 64-bit millisecond clock (note_client_clock: the 28-byte
    0x2023 begins [u32 0][u32 HI][u32 LO]), so every step's duration was
    LO - HI -- 491,813 ms at 18:18Z on 2026-09-12, i.e. a five-unit step
    spread over EIGHT MINUTES. The task existed, so the walk motion played;
    it never got anywhere, and the next tick replaced it. Live-test report: "they're
    animating like they're trying to move, but they are stuck in place."

    So the pair is now (t, t + step/speed in ms). --monster-chase-timing
    client restores the borrowed pair for an A/B.

    WARNING: CHOSEN, not measured: the aggro radius, the speed, and that a monster
    should walk at all rather than be placed. The live-test report is the
    spec here -- "the enemy does sort of animate now when attacking, but
    doesn't follow the character at all".

    2026-09-28, "enemies feel jittery and hard to target": this ran ONLY on
    the chasing player's own 0x2023 telemetry, and each step lasted exactly
    the gap since the last one (1.2 u / 800 ms every ~800 ms). An FE client
    standing still goes quiet for 3-6 s (see readloop's idle tick), so the
    moment a player stopped to target a monster, the monster moved in
    bursts, one step then a stand. While walking, any step that landed late
    left the monster with no move task, so it stood a frame and restarted its
    walk. The client's attach (0x050781E0, 0x05078226..0x0507826D) DELETES a
    running move of equal or lower priority for a new one, and only refuses a
    move whose END has already passed. So the chase now runs on the session's
    own pump clock (client_body None) and each step covers _chase_lead() --
    longer than the gap to the next send -- so a move is always replaced
    mid-walk and the monster never stands between steps. mob_pos still reads
    the replaced move part-way, so the server's copy stays where the client
    has it.
    """
    if getattr(args, "combat", "off") != "on":
        return
    if getattr(args, "monster_chase", "on") != "on":
        return
    if not sess._SESSION.get("in_field"):
        return
    if client_body is not None and len(client_body) != movement._MV_LEN:
        return
    mm = _mob_mode(args)
    if mm == "wait":
        return
    if mm == "shared":
        # KEY: SHARED MONSTERS (2026-09-13): ONE session moves a field's
        # monsters (mob_driver), each toward the NEAREST living player in the
        # field -- this session's own player may be dead, the field is not
        if not mob_driver(args):
            return
        targets = [p for _s, p in field_players(args)]
    else:
        if sess._SESSION.get("player_dead") or not sess._SESSION.get("cpos"):
            return
        targets = [sess._SESSION["cpos"]]
    if not targets:
        return
    pos = targets[0]
    now = time.monotonic()
    aggro = float(getattr(args, "monster_aggro", 15.0) or 15.0)
    stop = float(getattr(args, "monster_range", 8.0) or 8.0) * 0.75
    speeds = []         # per-monster speeds this tick (monster_speed_of)
    every = max(0.2, float(getattr(args, "monster_chase_interval", 0.5) or 0.5))
    px, py, pz = pos
    moved = 0
    steps = []          # per-tick step distances, to measure the >=1.0 gate
    durs = []           # per-step durations in ms -- END minus START, see above
    timing = str(getattr(args, "monster_chase_timing", "step") or "step")
    for obj, m in sorted(sess._SESSION.get("mobs", {}).items()):
        if m["dead"]:
            continue
        # from where the CLIENT has it -- mid-walk, not the walk's end
        mx, my, mz = mob_pos(m, now)
        # the nearest living player (one target solo)
        px, py, pz = min(targets, key=lambda p: (p[0] - mx) ** 2
                         + (p[2] - mz) ** 2)
        # this monster's OWN speed -- NPC_ModelType +0x420 by default, not one
        # number for every monster in the game
        speed = monster_speed_of(args, m.get("type", 0))
        # rooted / stunned stand still; a slow walks slower (--status-effects)
        sf = status_speed(args, m)
        if sf <= 0:
            continue
        speed *= sf
        dx, dz = px - mx, pz - mz
        d = (dx * dx + dz * dz) ** 0.5
        if d > aggro or d <= stop:
            continue
        last = m.get("chase_at", 0)
        if now - last < every:
            continue
        room = max(0.0, d - stop)
        if room < _MOB_MIN_STEP:
            # the last approach: a step this short is one the client drops
            # (0x04FEAAD4), so send nothing and move nothing -- it halts a
            # little outside the stop band, still inside --monster-range
            continue
        # at least _MOB_MIN_STEP; the duration below follows the step, so a
        # longer step is a longer move, never a faster one. It LEADS the next
        # send (_chase_lead), so the next step replaces it mid-walk.
        step = min(max(speed * _chase_lead(args, every), _MOB_MIN_STEP), room)
        steps.append(step)
        speeds.append(speed)
        m["chase_at"] = now
        nx, nz = mx + dx / d * step, mz + dz / d * step
        # WARNING: 2026-09-11: KEEP THE MONSTER ON THE GROUND AS IT MOVES. This used
        # to send the spawn Y unchanged while walking in x/z, so after one step
        # the monster was over different terrain and the client's per-frame
        # ground check (bit 0x800000, 0x050690d2) could read "no ground" and
        # refuse every further move -- a monster that lurches once and freezes.
        # We have no per-cell heightfield, but the player's own Y (cpos) is a
        # real point on this terrain, so lerp the monster's Y toward it by the
        # same fraction it closes the horizontal gap. It stays near the surface
        # along the path instead of drifting into the air.
        ny = my + (py - my) * (step / d) if d > 0 else my
        out = bytearray(movement._MV_LEN)
        struct.pack_into(">I", out, movement._MV_ACTION, 0)
        # START and END: the client reads END - START as this step's duration
        # (0x04FEAB38). The step is `step` units at `speed` u/s, so it should
        # take step/speed seconds -- which is also roughly the gap to the
        # next tick, so consecutive steps join up instead of queueing.
        dur_ms = max(1, int(round(step / max(speed, 0.001) * 1000.0)))
        durs.append(dur_ms)
        mob_move_note(m, (nx, ny, nz), dur_ms, now)
        if timing == "client" and client_body is not None:
            out[movement._MV_A:movement._MV_A + 8] = client_body[movement._MV_A:movement._MV_A + 8]
        else:
            t0 = _chase_clock_ms()
            struct.pack_into(">II", out, movement._MV_A, t0 & 0xFFFFFFFF,
                             (t0 + dur_ms) & 0xFFFFFFFF)
        struct.pack_into(">H", out, movement._MV_STATE, 0)
        struct.pack_into(">hh", out, movement._MV_SPD, movement._i16(speed * 1000.0), 0)
        struct.pack_into(">hhh", out, movement._MV_POS,
                         movement._i16(nx * 10.0), movement._i16(ny * 10.0), movement._i16(nz * 10.0))
        wire.send(conn, outbound, wire.inner_msg(0x2023, bytes(out), obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        mob_relay(args, "mob_move", {"obj": obj, "body": bytes(out)})
        moved += 1
    if moved:
        n = sess._SESSION.get("chase_n", 0) + 1
        sess._SESSION["chase_n"] = n
        if n <= 3 or n % 40 == 0:
            if (timing == "client" and client_body is not None
                    and len(client_body) >= movement._MV_B + 4):
                _a, _b = struct.unpack_from(">II", client_body, movement._MV_A)
                _why = ("the CLIENT's own pair, whose difference is %d ms -- "
                        "a step that long never visibly moves"
                        % ((_b - _a) & 0xFFFFFFFF))
            else:
                _why = ("each step timed %d..%d ms (END - START, 0x04FEAB38)"
                        % (min(durs), max(durs)))
            print("[feworld]    chase timing (--monster-chase-timing %s): %s; "
                  "speed %.2f..%.2f u/s (--monster-speed %s: each model's own, "
                  "NPC_ModelType +0x41c walk / +0x420 run)"
                  % (timing, _why, min(speeds), max(speeds),
                     getattr(args, "monster_speed", "run")), flush=True)
            speed = max(speeds)
            lo, hi = min(steps), max(steps)
            under = sum(1 for s in steps if s < 1.0)
            print("[feworld]    -> 0x2023 action 0 CHASE x%d (tick #%d): "
                  "monsters inside %gu walk to %gu of the player at %g u/s. "
                  "step %.2f..%.2fu; %d of %d are < 1.0u and the CLIENT "
                  "DISCARDS those (0x04FEAAD4, threshold 0x525f5bc=1.0). If "
                  "the monster does not move, read [mob+0x84] slot 0 / "
                  "[model+0x27c] -- the model-loaded gate is the only one left."
                  % (moved, n, aggro, stop, speed, lo, hi, under, len(steps)),
                  flush=True)
        return
    # KEY: NOTHING MOVED -- say why, ONCE, because "the monster didn't chase me"
    # has two very different causes and they are invisible from the outside:
    #   (a) we never sent a thing -- no monster was inside --monster-aggro, or
    #       the session has no player position;
    #   (b) we sent it and the CLIENT refused. Its NPC branch needs slot 0 of
    #       the unit's 4-slot array at +0x84 (0x5077EC0 with index 0 -- the
    #       MODEL object) to be non-null AND [model+0x27c] to be non-zero, and
    #       whether a served monster has that is unmeasured.
    # If this line appears, it is (a) and the numbers say how far off. If the
    # CHASE line above appears and the monster still stands still, it is (b),
    # and the next thing to read is +0x27c on a live monster.
    if sess._SESSION.get("chase_why"):
        return
    live = [(((px - mob_pos(m, now)[0]) ** 2
              + (pz - mob_pos(m, now)[2]) ** 2) ** 0.5, o)
            for o, m in sess._SESSION.get("mobs", {}).items() if not m["dead"]]
    sess._SESSION["chase_why"] = True
    if not live:
        print("[feworld]    chase: no live monster is registered at all "
              "(--spawns/--monster served none, or they are all dead).",
              flush=True)
    else:
        d, o = min(live)
        print("[feworld]    chase: nothing moved -- %d live monster(s), the "
              "nearest (id %d) is %.1fu away and --monster-aggro is %g, so it "
              "is out of range (or already inside the %gu stop band). Walk "
              "closer; if the CHASE line still never appears, the range is "
              "the problem, not the client."
              % (len(live), o, d, aggro, stop), flush=True)


def monster_attack_tick(conn, outbound, mode, be, args):
    """Let every live monster in range hit the player. Driven off the 0x2023
    telemetry, the same ~400 ms clock the respawn uses -- and the only clock
    that also carries the player's POSITION (_SESSION["cpos"], i16 x0.1)."""
    if getattr(args, "combat", "off") != "on":
        return
    if getattr(args, "monster_attack", "off") != "on":
        return
    if not sess._SESSION.get("in_field"):
        return
    now = time.monotonic()

    # the revive first, so a dead player cannot be hit while down
    if sess._SESSION.get("player_dead"):
        death.revive_tick(conn, outbound, mode, be, args)
        return

    pos = sess._SESSION.get("cpos")
    if not pos:
        return
    if "player_hp" not in sess._SESSION:
        sess._SESSION["player_hp"] = death.player_hp_max(args)
    protected = death.spawn_protected(args, now)
    death.protect_flag_sync(conn, outbound, mode, be, args)
    rng = float(getattr(args, "monster_range", 8.0) or 8.0)
    every = max(0.2, float(getattr(args, "monster_interval", 2.0) or 2.0))
    px, py, pz = pos
    for obj, m in sorted(sess._SESSION.get("mobs", {}).items()):
        if m["dead"]:
            continue
        # where the client DRAWS it -- a swing judged at the end of a walk
        # still under way is a hit from across the field (live 2026-09-12)
        mx, my, mz = mob_pos(m, now)
        d = ((px - mx) ** 2 + (pz - mz) ** 2) ** 0.5
        # its OWN melee skill's reach and period (--monster-timing table),
        # looked up once per monster
        m_rng, m_every = rng, every
        if (getattr(args, "monster_timing", "table") == "table"
                and m.get("type_id") is not None):
            if "atk_skill" not in m:
                m["atk_skill"] = fegamedata.npc_attack_skill(m["type_id"], 0)
            sk = m["atk_skill"]
            if sk.get("range"):
                m_rng = sk["range"]
            if sk.get("period"):
                m_every = max(0.2, sk["period"])
        if d > m_rng:
            continue
        # KEY: AND IT HAS TO BE AT ROUGHLY YOUR HEIGHT (2026-09-12, live: a
        # player met a monster STANDING ON A CLIFF -- "I couldn't reach it,
        # but somehow it could attack me"). The distance above is HORIZONTAL,
        # y dropped, so a monster forty units up is "eight away" and swings
        # freely -- while the player's own cast is gated by the CLIENT's real
        # three-dimensional reach and cannot answer. The server measured 2D
        # and the client measured 3D, and the mismatch is a monster that hits
        # from somewhere you cannot hit back.
        #
        # The fix is a height gate, not a 3D distance: a 3D distance is what
        # broke the arrow tower this same day (its y is a map-wide median
        # estimate, so the y term alone put every target out of range). A
        # monster's y is its real placement and the player's is real, so
        # comparing them is sound -- and on flat ground the gate never fires,
        # so ordinary combat is untouched.
        ry = float(getattr(args, "monster_reach_y", 8.0) or 0.0)
        # WARNING: NOT FOR A GUESSED HEIGHT (2026-09-12, live: "enemies aren't
        # attacking me rn"). 714 of 721 shipped spawns have no Y and are served
        # at the map's MEDIAN ground -- 62 on map 12, where the player stood at
        # ~47 -- so this test read "Duke_Orc is -15.3 u above/below the player
        # ... it cannot reach them" for a monster standing on the same ground
        # as the player (the client ground-snaps it), and refused every swing
        # until the chase happened to drag our copy of its Y close enough.
        # A measured Y (a --monster row, a stored spawn) is still tested.
        if ry > 0 and not m.get("y_guess") and abs(py - my) > ry:
            if not m.get("high_logged"):
                m["high_logged"] = True
                print("[feworld]    %s is %.1f u above/below the player (limit "
                      "%.1f, --monster-reach-y) -- it cannot reach them. Its "
                      "horizontal distance is %.1f u, which is what used to be "
                      "the only test."
                      % (m["name"] or "monster %d" % obj, py - my, ry, d),
                      flush=True)
            continue
        with _MOB_LOCK:
            # shared monsters: one swing per interval, whichever player
            if now - m.get("swing_at", 0) < m_every:
                continue
            # a stunned or disarmed monster does not swing (manual p.39)
            if status.on(args) and m.get("status") and status.mob_refusal(m, now):
                continue
            m["swing_at"] = now
        if protected:
            # the 15 s protection [SE interface05]: the swing happens, it
            # just does nothing -- no HP push, so no damage number either
            if not sess._SESSION.get("protect_logged"):
                sess._SESSION["protect_logged"] = True
                print("[feworld]    %s swings at a PROTECTED player -- no "
                      "damage (--spawn-protect-secs)"
                      % (m["name"] or "monster %d" % obj), flush=True)
            continue
        dmg, why = damage.monster_hit_damage(args, m)
        hp = max(0, int(sess._SESSION.get("player_hp", death.player_hp_max(args))) - dmg)
        sess._SESSION["player_hp"] = hp
        death.player_hp_push(conn, outbound, mode, be, args, hp)
        if hp > 0 and status.on(args):
            # the hit ends root/hide, then its OWN skill's bad statuses land
            # (Venomous's bite poisons, Ice Lizard roots: SKILL_DATA +0x108)
            status.player_hit_taken(conn, outbound, mode, be, args,
                                    m["name"] or "monster %d" % obj)
            sk = (m.get("atk_skill") or {}).get("skill")
            if sk is not None:
                status.player_apply(conn, outbound, mode, be, args,
                                    status.bad_specs(sk), src=obj, skill=sk,
                                    why="%s's skill %d" % (m["name"] or obj, sk))
        print("[feworld]    %s (L%s) hits the player for %d at %.1fu -- HP "
              "%d/%d [%s] (0x2024 maskA 0x4 carries the NEW hp; the client "
              "draws the difference as the damage number)"
              % (m["name"] or "monster %d" % obj, m.get("level", "?"), dmg, d,
                 hp, death.player_hp_max(args), why), flush=True)
        if hp <= 0:
            sess._SESSION["player_dead"] = True
            # the SAFETY NET, not the way back -- Return to Base (0x2015,
            # return_to_base) is. A client that can never send it still stands.
            sess._SESSION["revive_at"] = now + float(getattr(args, "revive_secs", 60) or 60)
            death.player_dead_push(conn, outbound, mode, be, args, True)
            death.respawn_wait_arm(conn, outbound, mode, be, args)
            side = death.war_death_side(args)
            print("[feworld]    THE PLAYER IS DEAD -- waiting for RETURN TO "
                  "BASE (0x2015); the safety net revives in %.0fs %s. No "
                  "ghost, no item loss."
                  % (float(getattr(args, "revive_secs", 60) or 60),
                     "AT THE %s BASE (a war field)" % side.upper() if side
                     else "where they fell"), flush=True)
            death.death_penalty(conn, outbound, mode, be, args)
            return


def motion_refresh_push(conn, outbound, mode, be, args, rows, why=""):
    """0x2004 worn-gear carrying what is ALREADY worn -- for the MOTION SET.

    KEY: 2026-09-09, the T-pose. A weapon's animation set is chosen by
    0x0506aa30, and only two things call it with the held weapon: the entity
    decoder's tail (0x0503aec2) -- which sets STANCE two instructions later and
    freezes the character -- and **the worn-gear reader's tail** (0x0503c70e),
    which selects the motion set, refreshes the HUD (vtable+0x30 with 1, 0xa)
    and touches OBJSTATUS NOT AT ALL. That reader is bit 0 of message 0x2004,
    so re-stating the worn gear after the weapon is in hand gives the motions
    without the freeze bit.

    WARNING: Only ever to the LOCAL player: for any other unit the same reader's
    uid == 0 branch destroys the object (0x0503c578). We send real uids, but
    the address is the rule, not the payload."""
    if getattr(args, "motion_refresh", "on") != "on" or not rows:
        return False
    if not sess._SESSION.get("in_field"):
        return False
    equip = [(r[3], r[0]) for r in rows if r[3] is not None and 0 <= r[3] <= 12]
    if not equip:
        return False
    items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
    body = struct.pack(">I", 0x1) + itemrecords.equip_record(equip, items)
    wire.send(conn, outbound, wire.inner_msg(0x2004, body, wire.unit_id_of(args)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x2004 WORN-GEAR RESTATED (%s): %s -- for "
          "0x0503c70e, the weapon MOTION SET (0x0506aa30). This reader does "
          "NOT set STANCE, which is why the cast can stop being a T-pose "
          "without the character freezing."
          % (why, ", ".join("worn %d = uid %d" % (w, u) for w, u in sorted(equip))),
          flush=True)
    return True


def mob_respawn_tick(conn, outbound, mode, be, args):
    """Put a dead monster back after --respawn-secs. Driven off the 0x2023
    telemetry, which is the only clock this loop has (~400 ms in the field)."""
    if getattr(args, "combat", "off") != "on":
        return
    secs = float(getattr(args, "respawn_secs", 0) or 0)
    if secs <= 0:
        return
    mm = _mob_mode(args)
    if mm == "wait" or (mm == "shared" and not mob_driver(args)):
        return                      # the field's driver respawns for everyone
    now = time.monotonic()
    for obj, m in sorted(sess._SESSION.get("mobs", {}).items()):
        if not m["dead"] or now - m.get("died_at", now) < secs:
            continue
        m["hp"], m["dead"], m["hits"] = m["hpmax"], False, 0
        # WARNING: THE RESPAWN FORGOT WHICH MONSTER IT WAS (live 2026-09-12): a
        # Duke_Orc L40 came back as L15 45 s after dying. This re-send passed
        # only the MODELTYPE, so mob_register resolved level and HP through the
        # modeltype fallback -- the LOWEST npc type sharing the model (a known
        # quirk: "returns level 15 for most things") -- and dropped the name.
        # It also respawned at m["pos"], wherever the chase had dragged the
        # body, instead of at its spawner. Now: the npc TYPE id and name ride
        # along, and it comes back at the point it was first placed.
        x, y, z = m.get("home", m["pos"])
        guess = m.get("y_guess")
        town.npc_send(conn, outbound, mode, be, args, obj, m["type"], 0,
                 m["level"], x, y, z, name=m.get("name", ""),
                 type_id=m.get("type_id"),
                 why="(respawn after %gs, at its spawn point)" % secs)
        # npc_send re-registered it -- push the stats of the NEW record
        m = sess._SESSION.get("mobs", {}).get(obj, m)
        if guess:
            m["y_guess"] = True
        combat.mob_stat_push(conn, outbound, mode, be, args, obj, m)
        mob_relay(args, "mob_spawn", {"obj": obj})


def monster_wander_pump(conn, outbound, mode, be, args):
    """Idle monsters STROLL (2026-09-12, live-test report: the spawns "feel a bit
    inorganic"). Until now a monster moved only when chasing, so every pack
    stood in its spawn formation forever.

    Each live monster, every --monster-wander-secs (a random pause, per
    monster), walks to a random spot within --monster-wander-radius of its
    HOME -- so a monster the chase dragged off drifts back -- at its model's
    own WALK speed (NPC_ModelType +0x41c: at walk speed the client plays the
    walk motion at rate 1.0). The move is the chase's own record: 0x2023
    action 0, START/END = the walk's duration (0x04FEAB38), the target at
    >= 1.5 u (the client drops a move under 1.0, 0x04FEAAD4). Never while the
    chase owns it: inside --monster-aggro of the player, or chased < 5 s ago.
    Y stays the monster's current one (no per-spot heightfield; the chase does
    the same between its lerps). Runs on the idle tick too, so they wander
    while the player stands still. Monsters are per-session, like the chase.
    CHOSEN, not measured: the pause, the radius and that they wander at all.
    """
    if getattr(args, "combat", "off") != "on":
        return 0
    if str(getattr(args, "monster_wander", "on") or "on") != "on":
        return 0
    if not sess._SESSION.get("in_field") or not sess._SESSION.get("field_ready"):
        return 0
    mobs = sess._SESSION.get("mobs")
    if not mobs:
        return 0
    mm = _mob_mode(args)
    if mm == "wait" or (mm == "shared" and not mob_driver(args)):
        return 0
    import math as _m
    import random as _r
    try:
        lo, hi = (float(v) for v in
                  str(getattr(args, "monster_wander_secs", "6:15")).split(":"))
    except ValueError:
        lo, hi = 6.0, 15.0
    lo, hi = max(0.5, min(lo, hi)), max(0.5, max(lo, hi))
    leash = max(2.0, float(getattr(args, "monster_wander_radius", 12.0) or 12.0))
    aggro = float(getattr(args, "monster_aggro", 15.0) or 15.0)
    pos = sess._SESSION.get("cpos")
    # every player in the field holds a monster still, not only this one
    near = ([p for _s, p in field_players(args)] if mm == "shared"
            else ([pos] if pos else []))
    rng = sess._SESSION.get("wander_rng")
    if rng is None:
        rng = sess._SESSION["wander_rng"] = _r.Random()
    now = time.monotonic()
    moved, durs = 0, []
    for obj, m in sorted(mobs.items()):
        if m.get("dead"):
            continue
        due = m.get("wander_due")
        if due is None:
            # stagger the first strolls so a field does not move in unison
            m["wander_due"] = now + rng.uniform(0.0, hi)
            continue
        if now < due:
            continue
        m["wander_due"] = now + rng.uniform(lo, hi)
        mx, my, mz = mob_pos(m, now)
        if now - m.get("chase_at", 0) < 5.0:
            continue
        if any(((p[0] - mx) ** 2 + (p[2] - mz) ** 2) ** 0.5 <= aggro
               for p in near):
            continue
        hx, _hy, hz = m.get("home", m["pos"])
        target = None
        for _try in range(6):
            a = rng.uniform(0.0, 2.0 * _m.pi)
            r = leash * _m.sqrt(rng.random())
            tx, tz = hx + r * _m.cos(a), hz + r * _m.sin(a)
            d = ((tx - mx) ** 2 + (tz - mz) ** 2) ** 0.5
            if d >= 1.5:
                target = (tx, tz, d)
                break
        if not target:
            continue
        tx, tz, d = target
        sp = fegamedata.model_speed(m.get("type", 0))
        walk = float(sp[0]) if sp and sp[0] else 1.5
        sf = status_speed(args, m)
        if sf <= 0:
            continue                    # rooted or stunned (--status-effects)
        walk *= sf
        dur_ms = max(1, int(round(d / max(walk, 0.1) * 1000.0)))
        durs.append(dur_ms)
        # the pause starts when it ARRIVES
        m["wander_due"] += dur_ms / 1000.0
        mob_move_note(m, (tx, my, tz), dur_ms, now)
        out = bytearray(movement._MV_LEN)
        struct.pack_into(">I", out, movement._MV_ACTION, 0)
        t0 = _chase_clock_ms()
        struct.pack_into(">II", out, movement._MV_A, t0 & 0xFFFFFFFF,
                         (t0 + dur_ms) & 0xFFFFFFFF)
        struct.pack_into(">H", out, movement._MV_STATE, 0)
        struct.pack_into(">hh", out, movement._MV_SPD, movement._i16(walk * 1000.0), 0)
        struct.pack_into(">hhh", out, movement._MV_POS,
                         movement._i16(tx * 10.0), movement._i16(my * 10.0), movement._i16(tz * 10.0))
        wire.send(conn, outbound, wire.inner_msg(0x2023, bytes(out), obj), mode, be,
             args.seq_mode == "echo", args.world_prefix)
        mob_relay(args, "mob_move", {"obj": obj, "body": bytes(out)})
        moved += 1
    if moved:
        n = sess._SESSION.get("wander_n", 0) + 1
        sess._SESSION["wander_n"] = n
        if n <= 3 or n % 50 == 0:
            print("[feworld]    -> 0x2023 action 0 WANDER x%d (pump #%d): idle "
                  "monsters stroll within %gu of home at walk speed, %d..%d ms "
                  "per walk, then pause %g..%g s (--monster-wander)"
                  % (moved, n, leash, min(durs), max(durs), lo, hi), flush=True)
    return moved
