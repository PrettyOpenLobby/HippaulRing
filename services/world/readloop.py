"""The per-session read loop: every inbound message id and its answer."""
import select
import struct
import time
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
import fenet  # noqa: E402
from . import arrival, bank, buildings, campaignview, character, charsheet, chat, clock, combat, damage, death, doors, drops, entities, equipment, events, ext, extrun, gm, inventory, itemrecords, messages, monsters, movement, populate, probes, progression, sess, shops, skilllist, territory, town, unitstate, wallet, war, warresult, wire, zones

def serve(conn, args, session, outbound, mode, be):
    """Read, decode, print. Answers NOTHING -- see the module docstring."""
    print("\n[feworld] --- post-handshake. CAPTURE ONLY: nothing here answers, "
          "because nothing past 0x20 is measured yet ---", flush=True)
    conn.settimeout(args.read_window)
    sess._OUT_SEQ[0] = 0
    sess._SESSION.pop("entered", None)
    chat._chat_room_join()
    try:
        _serve_loop(conn, args, session, outbound, mode, be)
    finally:
        chat._chat_room_leave()


def _serve_loop(conn, args, session, outbound, mode, be):
    seen = 0
    # WARNING:KEY: THE IDLE TICK (2026-09-11). Until now this loop BLOCKED in
    # recv_frame until the client said something, so every pump -- chat, the
    # revive, fecampaign's phases, fepresence's relays -- ran at the mercy of
    # THIS client's outbound rate. war_deadline_pump's own comment claimed it
    # ran "on the read loop's idle path"; there was no such path, and the
    # comment had been wrong since it was written.
    #
    # What it cost, measured live: a player standing still watching another
    # player walk got a position update every 3-6 SECONDS, because their own
    # client was quiet and nothing else could wake their thread. The peer
    # walked in 3-6 unit jumps with a run animation in between.
    #
    # select() rather than a socket timeout, so recv_frame is only ever
    # entered when a frame is actually there -- a short recv timeout would
    # tear a frame in half. --idle-tick-ms 0 restores the blocking loop.
    tick = max(0.0, float(getattr(args, "idle_tick_ms", 250) or 0) / 1000.0)
    idle_since = time.monotonic()
    wake_r = chat._my_wake_socket()
    while True:
        if tick:
            try:
                ready = select.select([conn] + ([wake_r] if wake_r else []),
                                      [], [], tick)[0]
            except (OSError, ValueError):
                print("[feworld] socket gone while waiting -- hanging up",
                      flush=True)
                return
            except (TypeError, AttributeError):
                # not a real socket: the test harnesses hand this loop a stub
                # with recv() and nothing else. Fall back to the blocking
                # path, which is exactly what they ran against before.
                tick, ready = 0.0, [conn]
            if wake_r is not None and wake_r in ready:
                # another session posted us a relay: drain the poke and run
                # the pumps right now instead of at the next tick
                try:
                    wake_r.recv(256)
                except (OSError, BlockingIOError):
                    pass
                ready = [r for r in ready if r is not wake_r]
            if not ready:
                if time.monotonic() - idle_since >= args.read_window:
                    print("[feworld] no further traffic (idle %gs) -- hanging "
                          "up so FE fails fast" % args.read_window, flush=True)
                    return
                # the same pumps the inbound path runs, on our own clock
                arrival.field_ready_pump(conn, outbound, mode, be, args)
                gm.gm_pump(conn, outbound, mode, be, args)
                chat.chat_pump(conn, outbound, mode, be, args)
                gm.validate_pump(conn, outbound, mode, be, args)
                war.war_deadline_pump(conn, outbound, mode, be, args)
                monsters.monster_wander_pump(conn, outbound, mode, be, args)
                death.revive_tick(conn, outbound, mode, be, args)   # a respawn wait
                drops.drop_pump(conn, outbound, mode, be, args)
                damage.item_effect_pump(conn, outbound, mode, be, args)
                extrun.ext_pump(extrun.Ctx(conn, outbound, mode, be, args))
                continue
            idle_since = time.monotonic()
        # the client IS talking (0x209F from the scene update lands 23 ms
        # after 0x100E), so the batch held for field-load can go on any
        # message once the grace has passed -- not only on a movement sample
        arrival.field_ready_pump(conn, outbound, mode, be, args)
        progression.exp_max_seed_pump(conn, outbound, mode, be, args)
        try:
            fr = fenet.recv_frame(conn)
        except (OSError, IOError):
            print("[feworld] no further traffic (idle %gs) -- hanging up so FE "
                  "fails fast" % args.read_window, flush=True)
            return
        if not fr:
            print("[feworld] client closed after %d message(s)" % seen, flush=True)
            return
        outer, body = fr
        if outer != 0x30:
            print("\n[feworld] <- OUTER id=0x%04X %d bytes  (%s)"
                  % (outer, len(body), messages.KNOWN.get(outer, "unknown")), flush=True)
            if body:
                print(fenet.hexdump(body), flush=True)
            if outer == 0x2099:
                # the END op's stack-local stream: the close arrives BARE
                events.conversation_close(conn, outbound, mode, be, args, body, "outer")
            continue
        plain = fenet.bf_decrypt(session, body, mode, be)
        env = fenet.traffic_unwrap(plain)
        if not env:
            print("\n[feworld] <- 0x30 whose envelope did not validate:", flush=True)
            print(fenet.hexdump(plain), flush=True)
            continue
        seq, inner = env
        sess._LAST_IN[0] = seq
        seen += 1
        # The admin's live console into the client: anything dropped into
        # --gmcmd-file goes out as a 0xF900 command line. Polled here rather
        # than on a timer because the read loop is already woken constantly by
        # the client's 0x2023 telemetry.
        gm.gm_pump(conn, outbound, mode, be, args)
        chat.chat_pump(conn, outbound, mode, be, args)
        gm.validate_pump(conn, outbound, mode, be, args)
        war.war_deadline_pump(conn, outbound, mode, be, args)
        monsters.monster_wander_pump(conn, outbound, mode, be, args)
        death.revive_tick(conn, outbound, mode, be, args)   # a respawn wait
        drops.drop_pump(conn, outbound, mode, be, args)
        damage.item_effect_pump(conn, outbound, mode, be, args)
        ctx = extrun.Ctx(conn, outbound, mode, be, args)
        extrun.ext_pump(ctx)
        real_id = struct.unpack(">H", inner[:2])[0] if len(inner) >= 2 else -1
        death.spawn_protect_note(real_id)     # an action ends the 15 s protection
        print("\n[feworld] <- 0x30 seq=%d inner id=0x%04X, %d bytes of fields  (%s)"
              % (seq, real_id, max(0, len(inner) - 2),
                 messages.KNOWN.get(real_id, "NOT SEEN BEFORE -- this is the finding")),
              flush=True)
        print(fenet.hexdump(inner), flush=True)
        if real_id == 0x0020 and args.probe_on_auth:
            # BISECT: answer the AUTH_CODE_NOTIFY -- which needs no answer -- with
            # a minimal enciphered frame, and see whether the connection survives
            # it. This splits the failure cleanly and RE was not converging:
            #
            #   dies here too   -> ANY enciphered frame from us is rejected, so
            #                      the fault is our ciphering/envelope on this
            #                      connection, not 0x302B's contents
            #   survives here   -> the transport takes our frames fine and the
            #                      problem really is the 0x302B message
            #
            # Worth stating why this is a live test rather than more disassembly:
            # with NO reply at all the client sits happily sending keepalives
            # (measured, first capture), and the disconnect follows our first
            # outbound frame every single time. So the trigger is ours; the open
            # question is whether it is the envelope or the payload.
            body = wire.inner_msg(0x0001)
            wire.send(conn, outbound, body, mode, be, args.seq_mode == "echo")
            print("[feworld]    --probe-on-auth: sent a minimal inner 0x0001. If "
                  "the client now disconnects WITHOUT sending 0x400F, the fault "
                  "is our transport, not the login message.", flush=True)
        if (real_id in ext.EXT_HANDLERS
                and extrun.ext_dispatch(ctx, real_id, inner) is not False):
            # THE EXTENSION SEAM -- first in the chain on purpose, see the
            # registries at the top of the file. A handler that returns False
            # DECLINED the message and the chain below answers it as if no
            # extension existed (fecampaign with --campaign off).
            pass
        elif real_id == 0x0020 and len(inner) >= 6:
            code = struct.unpack(">I", inner[2:6])[0]
            print("[feworld]    MSG_AUTH_CODE_NOTIFY code=%d (0x%08X) -- this is "
                  "the CODE the lobby issued in 0x7821. Matching it against what "
                  "felobby handed out is what ties the two doors together."
                  % (code, code), flush=True)
        elif real_id == 0x206A:
            # MSG_GET_FORCE_INFO_REQUEST -- the first thing the client asks once
            # it is IN the world, and the reason the screen sits black. Named by
            # its own log format at 0x52d2cb0, built at 0x05005030 / 0x05005610:
            #
            #     [u32 ForceID][u32 BitMask]
            #
            # A "Force" is the faction/army; the mask says which fields to send
            # back, and the client asks for 0xFFFFFFFF -- everything.
            #
            # The answer is 0x3027 OK / 0x3028 NG, from the dispatcher at
            # 0x050057c0: `id - 0x3027`, a byte index table at 0x5005958 and a
            # jump table at 0x5005944. (0x3033/0x3034 are JOIN_FORCE OK/NG in the
            # same family.)
            #
            #     0x3027 = [u32 ForceID][u32 Bit] + the fields Bit selects
            #
            # WARNING: WE SEND Bit = 0 ON PURPOSE. The handler at 0x05005868 reads the
            # two dwords, then only parses fields when the force lookup
            # (0x5005560) finds an object, and it sets the screen's state word
            # `[esi+0x50] = 3` EITHER WAY. So an empty record is a complete valid
            # answer and unblocks the client -- the same shape as answering the
            # character list with count 0. The eleven m_* fields (KingMessage,
            # WinTotalNum, TargetFieldID, ...) are named in the strings at
            # 0x52d2cdc..0x52d2e6c and can be filled in later.
            f = inner[2:]
            force_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            bits = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
            print("[feworld]    GET_FORCE_INFO ForceID=%d mask=0x%08X"
                  % (force_id, bits), flush=True)
            vals = args.force_table.get(force_id)
            if vals is None:
                body = wire.inner_msg(0x3027, struct.pack(">II", force_id, 0))
                wire.send(conn, outbound, body, mode, be,
                     args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x3027 ForceID=%d NOT in --forces, empty "
                      "record" % force_id, flush=True)
            else:
                vals = campaignview.campaign_force_vals(args, force_id, vals)
                # KEY: THE NATION'S OWN COLOUR (force+0x1bc, FORCE_FIELDS `f1bc`)
                # -- never set, so every nation sent palette index 0, which is
                # BLACK. Observed live, 2026-09-12: the "<nation> (of Player)" circle
                # on the continent map is black for Netzawar AND Gebrand while
                # their fields are red and cyan. 0x05105280 resolves the
                # character's nation ([char+0x30]) through force_by_id
                # (0x5005560) and draws `movsx byte [force+0x1bc]` through the
                # same palette as the field colour byte (0x5008680 ->
                # 0x5336fe8); the war-prep window colours both sides with it
                # too (0x051157ff). The palette IS indexed by force id -- 1 red,
                # 2 green, 3 blue, 4 yellow, 5 cyan, matching the live
                # screenshots byte for byte -- so the index is the id.
                _nc = str(getattr(args, "force_color", "id") or "off")
                if _nc != "off":
                    vals = dict(vals)
                    if _nc == "id":
                        vals["f1bc"] = int(force_id)
                    else:
                        try:
                            vals["f1bc"] = max(-128, min(127, int(_nc, 0)))
                        except ValueError:
                            pass
                rec = territory.build_force_record(vals)
                body = wire.inner_msg(0x3027,
                                 struct.pack(">II", force_id, bits) + rec)
                wire.send(conn, outbound, body, mode, be,
                     args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x3027 MSG_GET_FORCE_INFO_OK "
                      "ForceID=%d name=%r TargetFieldID=%d (%d record bytes)"
                      % (force_id, vals.get("name"),
                         vals.get("TargetFieldID", 0), len(rec)), flush=True)
        elif real_id == 0x400A:
            # MSG_REGISTER_DISTRIBUTER_OF_FIELDINFO_REQUEST -- no fields, sent
            # straight after the nation is registered (built 0x0500950d, logged
            # `<MSG_REGISTER_DISTRIBUTER_OF_FIELDINFO_REQUEST` at 0x52d338c).
            #
            # It has no registration call, so its answer came from the state word
            # instead: the builder sets [obj+0x14] = 2, and two sibling functions
            # set it to 0 and 1. Their only callers are dispatcher arms at
            # 0x050569af, which decodes `sub 0x3016 / dec / dec`:
            #
            #     0x3016 OK   -> logs MSG_REGISTER_DISTRIBUTER_OF_FIELDINFO_OK,
            #                    calls 0x5009540 ([obj+0x14] = 0)
            #     0x3017 NG   -> 0x5009550 ([obj+0x14] = 1)
            #     0x3018      -> 0x5009590, a third arm
            #
            # The OK arm reads NOTHING from the message, so 0x3016 carries no
            # fields. "Find the state word the sending code set, then the handler
            # that clears it" keeps being the fastest route when a request has no
            # registered reply list.
            print("[feworld]    REGISTER FIELDINFO DISTRIBUTER (no fields)",
                  flush=True)
            wire.send(conn, outbound, wire.inner_msg(0x3016), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x3016 "
                  "MSG_REGISTER_DISTRIBUTER_OF_FIELDINFO_OK (no fields)",
                  flush=True)
            # ...and then PUSH the island list, because registering a distributer
            # is a subscription: the client asked to be sent field info, so the
            # ack alone leaves it waiting for the thing it subscribed to.
            #
            #     0x3030 MSG_DISTRIBUTE_GROUPINFO_LIST_NOTIFY
            #            [u16 count][count x u16 islandId]
            #
            # Handler 0x050095b0, named by the log line at 0x52d5b54 and reached
            # from the dispatcher at 0x05056a3c (`id - 0x301F`, byte table
            # 0x50576cc into jump table 0x50576ac -> slot 5). It prints
            # `num island = %d` (0x52d33bc) with the count, allocates a vector of
            # count 16-BYTE records, then reads one u16 per island and marks
            # `array[id-1]` through 0x5008d50 -- bounds-checked as (id-1) < count,
            # so the ids must be 1..count.
            #
            # It ALSO clears [obj+0x14] itself (0x050095cf), the same word 0x3016
            # clears -- so this notify is what 0x400A was really waiting for, and
            # the ack is the lesser half of the answer.
            #
            # `Island Info was Updated. Open War Prepare Window.` (0x52e620c) is
            # what the client does next, so this should move it to the war-prepare
            # screen. The 16-byte record is NOT mapped -- only the id list is
            # served -- so expect the map to be sparse until it is.
            # WARNING: EACH ISLAND IS MORE THAN AN ID, and sending only ids is what put
            # the client in a RETRY LOOP -- thousands of iterations of
            # 0x400A/0x3030/0x400B/0x4011, and 0x4011 asking about island
            # 65535, which was garbage read past the end of our short record.
            #
            # The caller's loop (0x0500968f) reads the island id, then hands the
            # record to 0x5008d50 -- which reads ANOTHER u16 and treats it as a
            # count, frees and reallocates that many 0x100-byte buffers
            # (0x05008df8) and reads that many NUL-terminated strings into them
            # (0x05008e2a). So:
            #
            #     0x3030 = [u16 islandCount]
            #              + islandCount x { u16 id, u16 nameCount,
            #                                nameCount x cstr }
            #
            # With only ids on the wire, island N's id was being consumed as
            # island N-1's name count, and everything after it desynchronised --
            # which is exactly the shape of a stream whose element is bigger than
            # the sender thinks.
            #
            # nameCount 0 is a complete, aligned record. --island-names attaches
            # names when we know what they should be; the strings land in
            # 0x100-byte buffers, so they are not short.
            n = args.islands
            names = [x for x in (args.island_names or "").split(",") if x]
            payload = struct.pack(">H", n)
            for i in range(n):
                these = [names[i]] if i < len(names) else []
                payload += struct.pack(">HH", i + 1, len(these))
                for nm in these:
                    payload += nm.encode("cp932", "replace") + bytes(1)
            wire.send(conn, outbound, wire.inner_msg(0x3030, payload), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x3030 "
                  "MSG_DISTRIBUTE_GROUPINFO_LIST_NOTIFY, %d island(s) 1..%d"
                  % (n, n), flush=True)
            print("[feworld]    expect the client to log 'num island = %d' and "
                  "then 'Island Info was Updated. Open War Prepare Window.'" % n,
                  flush=True)
        elif real_id == 0x4015:
            # One u8 -- the character's UNIT id, read from [char+0x1258], the same
            # field the character record's first byte carries (see
            # fe-character-screen). Built at 0x05105cfc; it parks the screen's
            # state ladder at [obj+0x6c] = 6.
            #
            # No registration call, so the answer came from the result byte again.
            # [obj+0xec] is 0 while pending, and the only writer is the handler at
            # 0x0510bb00, which switches on the message id:
            #
            #     0x303C -> [i32 id][cstr][cstr], sets [obj+0xec] = 1
            #     0x303D -> sets [obj+0xec] = 0xFF
            #
            # and the waiter at 0x05105d36 advances to state 7 for ANY non-zero
            # value that is not 1 -- i.e. for 0x303D. A 1 falls through into a
            # different path instead.
            #
            # WARNING: THIS IS A NOTICE, and 0x303D is "nothing to show". The tell is the
            # i32 being compared against a cached value at 0x52e4a48 and only
            # acted on when it DIFFERS, plus two strings -- exactly the lobby's
            # 0xD014/0xD015 pair (fe-lobby-blowfish), where 0xD014 opens a window
            # the player must close and 0xD015 moves on. So the id here is a
            # seen-already key, not a payload.
            #
            # Default to 0x303D for the same reason the lobby defaults to 0xD015:
            # it advances. --world-notice sends the 0x303C form instead.
            #
            # WARNING: 2026-08-19 -- 0x303D IS WHAT RESTARTS THE BLACK-SCREEN LOOP.
            #
            # The reply handler is 0x0510bb00, a method of the map-select /
            # war-prepare screen, and it switches on the id at msg[+4]:
            #
            #     0x303C  [i32 id][cstr][cstr]   -> [screen+0xec] = 1, and then
            #                                       ONLY if `id != [0x52e4a48]`
            #                                       (the seen-already cache) does
            #                                       it stay 1; a repeat id falls
            #                                       through to 0xFF
            #     0x303D  no fields              -> [screen+0xec] = 0xFF
            #
            # and the screen's state 6 (0x05105d36) reads that byte:
            #
            #     0    keep waiting
            #     1    BUILD THE SCREEN (allocates 0x4C0 and goes on)
            #     else 0x05105e91 -> [+0xec]=0, state = 7
            #
            # State 7 (0x05105eb2) needs `[screen+0x5c] == 4` and otherwise runs
            # 0x051060a6 -> 0x5101280(2,0) -> **0x051060b1: [screen+0x6c] = 0**,
            # which is state 0 again -- and state 0 sends 0x400A. That is the
            # whole observed cycle, mechanically:
            #
            #     0x400A subscribe -> wait for the island list -> 0x400B stop
            #     -> 0x4011 per island (states 2/3, once each) -> 0x4015 notice
            #     -> 0x303D -> 0xFF -> state 7 -> RESET TO STATE 0 -> 0x400A ...
            #
            # So the default is now 0x303C with a per-session id. --world-notice
            # supplies real text; without it a minimal notice is still sent,
            # because "nothing to show" is the one answer that provably cannot
            # advance this screen. --world-notice-none restores 0x303D.
            f = inner[2:]
            unit = f[0] if f else 0
            if not args.world_notice_none:
                # WARNING: AN EMPTY NOTICE RENDERS AS AN EMPTY BOX. Seen live 2026-08-19:
                # the player got a dialog with no text and reasonably wondered
                # whether the translation had broken. It had not -- we were
                # sending two empty cstrs and the client drew exactly that.
                # A default that says something also PROVES the two string fields
                # land, which an empty notice never could.
                title, _, text = (args.world_notice or messages.DEFAULT_NOTICE).partition("|")
                # WARNING: DO NOT BUMP THIS PER SEND. Bumping it was a real bug, seen
                # live on prod 2026-08-19: the player got a notice dialog built
                # and torn down on EVERY refresh lap, rendering as a flickering
                # fragment.
                #
                # 0x0510bb5b compares the id against the cached [0x52e4a48]:
                # a NEW id shows the window, a REPEAT falls to the same 0xFF path
                # as 0x303D. That cache is the feature, not an obstacle -- the
                # 0x400A/0x400B/0x4011-sweep/0x4015 cycle is the war map's own
                # island-info REFRESH loop (0x400A is literally
                # "島情報更新要求", 0x400B its stop), so 0x4015 is asked once per
                # lap and a constant id is what makes the notice appear once and
                # then stay quiet.
                nid = args.world_notice_id
                payload = (struct.pack(">i", nid)
                           + title.encode("cp932", "replace") + bytes(1)
                           + text.encode("cp932", "replace") + bytes(1))
                wire.send(conn, outbound, wire.inner_msg(0x303C, payload), mode, be,
                     args.seq_mode == "echo", args.world_prefix)
                # MEASURED LIVE 2026-08-19 on prod: this DOES reach
                # [screen+0xec]=1 and the notice window is really built -- the
                # player saw it on screen. It is NOT sufficient on its own. The
                # build path (0x05105d4c..0x05105e8e) falls straight through into
                # 0x05105e91 `[screen+0xec]=0` / 0x05105e98 `[screen+0x6c]=7`,
                # and state 7 (0x05105eb2) demands `[screen+0x5c] == 4` or it
                # runs 0x051060a6 -> 0x051060b1 `[screen+0x6c]=0` and starts the
                # whole 0x400A cycle again. So the dialog is built and torn down
                # once per lap, which is why it renders as a flickering fragment.
                # WARNING: THE OPEN GATE IS [screen+0x5c]==4, not this message.
                sess._SESSION["notices"] = sess._SESSION.get("notices", 0) + 1
                print("[feworld]    unit=%d -> 0x303C notice id=%d %r "
                      "(#%d this session; the client caches the id at 0x52e4a48, "
                      "so only the FIRST builds a window)"
                      % (unit, nid, title, sess._SESSION["notices"]), flush=True)
            else:
                wire.send(conn, outbound, wire.inner_msg(0x303D), mode, be,
                     args.seq_mode == "echo", args.world_prefix)
                print("[feworld]    unit=%d -> 0x303D --world-notice-none: sets "
                      "[screen+0xec]=0xFF -> state 7 -> state 0 = THE LOOP"
                      % unit, flush=True)
        elif real_id == 0x4011:
            # MSG_GET_GROUP_DETAIL_INFO_REQUEST (built 0x050092aa, logged at
            # 0x52d32bc) -- one u16, the island the client wants the groups for.
            # It sets [obj+0x2c] = 2; the NG arm at 0x050094c5 sets it to 1 and
            # uses id 0x3032, and the OK is 0x3031 -- slot 6 of the same
            # `id - 0x301F` table that produced the island notify, logged
            # `> MSG_GET_GROUP_DETAIL_INFO_OK` (0x52d5b34) and parsed by
            # 0x050092f0:
            #
            #     0x3031 = [u16 islandId][u16 groupCount]
            #              + groupCount x { u32 groupId, u32 MASK, ...gated }
            #
            # WARNING: THIS ONE REALLY IS MASK-DRIVEN, unlike force-info. Verified rather
            # than assumed this time: 0x5008850 reads the group id into [rec+0],
            # then a u32 mask into a local, and EVERY subsequent field sits behind
            # a `test byte [mask], <bit> / je` -- bit 0 gates a u8 at +4, bit 1 a
            # u32 at +8, and so on through +0x8c. So mask = 0 means "no optional
            # fields", and a group record is then just its id and a zero mask.
            # (Per-group records are 0xCC bytes in memory, which is where the
            # `count * 0xcc` allocation comes from.)
            #
            # --groups sets how many to announce; 0 is a complete valid answer.
            f = inner[2:]
            island = struct.unpack_from(">H", f, 0)[0] if len(f) >= 2 else 0
            # WARNING: GROUP IDS MUST BE GLOBALLY UNIQUE AND 1..0x5F. MSG_ENTER_AREA_OK
            # names a GROUP, and the client resolves it with 0x5009820 to find
            # which island it belongs to ([group+0xc8], written here by the outer
            # parser at 0x0500943a). With groupCount=0 nothing can ever resolve,
            # the current island stays -1, and the war-prepare window is
            # unreachable -- which is what left the client spinning.
            n = args.groups
            # The war fields, then any CAPITAL announced for this island. A
            # capital rides in the same list because the client picks a field
            # off it; what makes it a town rather than a battlefield is purely
            # the ID (CAPITAL_GROUP_IDS), which it tests on ENTRY, not here.
            caps = [g for g in zones._CAPITALS.get(island, [])]
            gids = zones.group_ids_for(island, args)
            # one census for the whole answer -- the marker each field draws is
            # a population tier (field_census)
            census = campaignview.field_census(args)
            by_nation = campaignview.field_census_nations(args)
            _recs = []
            payload = struct.pack(">HH", island, len(gids))
            for gid in gids:
                # In dat mode a capital is whatever fet_area's 500 marker says,
                # not whatever --capital was pointed at: the table knows all
                # ten, and island 2 carries 21 and 91 whether or not anyone
                # named them on the command line.
                if territory.world_from_dat(args):
                    is_capital = gid in zones.CAPITAL_GROUP_IDS
                else:
                    is_capital = gid in caps
                vals = {}
                if args.field_status is not None:
                    vals["status"] = args.field_status
                # KEY: A LIVE PHASE BEATS THE CONSTANT. The client draws this
                # byte through its own five-name table (0 Peace, 1 War Prep,
                # 2 At war, 3 Truce, 4 ServerDown, 0x0510b540 -> 0x52e4b1c),
                # so once a campaign is running the war map says what is
                # actually happening on each field instead of one value for
                # the whole world.
                _ph = campaignview.campaign_phase(args, gid)
                if _ph is not None:
                    vals["status"] = _ph
                # KEY: THE FIELD'S MARKER IS ITS POPULATION (see field_census).
                # Sent for every field, including 0: the client needs the field
                # present in the record to size a marker, and "nobody here" is
                # a true answer that the ~9 tier can draw.
                if str(getattr(args, "field_census", "on") or "off") == "on":
                    vals["chars_all"] = census.get(int(gid), 0)
                    # (F%d): players of THIS VIEWER's own nation in the field.
                    # WARNING: "F" is a READING -- Force is the obvious expansion and
                    # the viewer's own side is the only per-viewer quantity in
                    # a line that otherwise has ALL and the two war sides --
                    # but nothing names it. --field-census off drops it.
                    _mine = territory.nation_of(args)
                    if _mine:
                        vals["chars_f"] = (by_nation.get(int(gid), {})
                                           .get(int(_mine), 0))
                # KEY: THE COLOUR BYTE (+0x96, mask bit 19, the client's own log
                # calls it COLOR[%d]) -- NEVER SENT until 2026-09-12, and it is
                # the original design question: "should map circles/lines be
                # coloured depending upon area ownership?" There is a literal
                # per-field colour on the wire and we left its bit clear.
                #
                # It is a PALETTE INDEX. Both map-screen readers do
                # `movsx <r>, byte [group+0x96]` (SIGNED -- -1 reads as "none")
                # then call 0x5008680, which is one instruction of arithmetic:
                #     return 0x5336fe8 + (variant + colour*2) * 4
                # i.e. table[colour*2 + variant], two dwords per colour, and
                # the result's first dword is stored into the widget at +0x89.
                # The map screen reads it SIX times per field
                # (0x0510b10b/b1b9/b20b/b55e/b57e/b605), around the same panel
                # updater that draws Total Players and Status.
                #
                # WARNING: THE VALUE IS A READING: the owning nation is the only
                # per-field quantity that a five-nation palette would be keyed
                # on, and `--field-color owner` sends def_id. The table is
                # runtime-built (0x5336fe8 is writable) so its length is not
                # readable statically -- if the wrong end of the palette comes
                # out, `--field-color N` pins one index for a probe run and
                # `off` restores the absent field.
                _fc = str(getattr(args, "field_color", "owner") or "off")
                if _fc != "off":
                    if _fc == "owner":
                        vals["color"] = int(territory.territory_owner(gid, args) or 0)
                    else:
                        try:
                            vals["color"] = max(-128, min(127, int(_fc, 0)))
                        except ValueError:
                            pass
                if args.field_flags is not None:
                    # WARNING: REQUIRED for the map screen to ever build. The home-field
                    # resolve 0x05009240 -- called from map-select state 7
                    # (0x05105eed) -- only accepts a group when BOTH
                    # [group+0x78] == the character's nation AND
                    # [group+0x8c] has BIT 3 set. +0x8c is mask bit 15 of this
                    # record (u16, read at 0x05008a3f), and it was never served,
                    # so the resolve returned NULL for every nation and state 7
                    # restarted the island sweep each lap -- the 267-lap loop /
                    # black window. Bit 3's meaning beyond "counts as a home
                    # field" is unmeasured; 8 is the minimum that passes.
                    #
                    # VERIFIED 2026-09-09: BIT 3 IS "THIS GROUP IS A CAPITAL",
                    # found by looking at the live world map: every
                    # marker read "Cap <name>". The map-label builder at
                    # 0x0510A2A4 does
                    #     mov al,[group+0x8c] ; test al,8
                    #     jne  -> sprintf("首都%s", name)   (0x052E5DCC)
                    #     je   -> sprintf("%s", name)       (0x052D2214)
                    # and 首都 is "capital city", which our UI table renders
                    # "Cap" (it has 4 bytes to fit in). So the same bit the
                    # home-field resolve needs is the one that prefixes the
                    # label -- and setting it on EVERY field, which is what
                    # makes the resolve succeed, tells the map that every
                    # field is a capital.
                    #
                    # --field-home capital sets it only on the capitals, which
                    # is what the client means by it. It is NOT the default:
                    # the resolve wants a group with BOTH def_id == the
                    # player's nation AND this bit, so narrowing it can put a
                    # nation back in the 2026-08-27 black screen if that
                    # nation's capital is not in the island's list with a
                    # matching def_id. PARTIAL: UNTESTED either way.
                    flags = args.field_flags
                    if getattr(args, "field_home", "all") == "capital" and not is_capital:
                        flags &= ~0x8
                    vals["g8c"] = flags
                # 2026-09-06: dat.pak SHIPS THE FIELD NAMES. fet_area has a
                # row for every area 1..95 carrying its cp932 name, the pixel
                # it draws at on the continent map, its owning nation and its
                # adjacency list -- and prod has been running with five
                # invented English names ("Ruined Plain", "Old Bridge", ...)
                # hardcoded in docker-compose. `--field-names dat` serves the
                # shipped ones. It is not the default only because the names
                # are Japanese and the rest of this client is being run in
                # English; --field-names dat-en falls back to the id for a
                # field with no translation, which is every field today.
                if getattr(args, "field_names", "") in ("dat", "dat-en"):
                    if args.field_names == "dat":
                        nm = fegamedata.areas().get(gid, {}).get("name")
                    else:
                        # 2026-09-09: dat-en is no longer a placeholder. All 95
                        # areas have an English name in
                        # fedata/fe-area-names-en.tsv -- OURS, not SE's, since
                        # dat.pak ships none. A missing row still falls back to
                        # "Field N", so the file can be edited or removed
                        # without breaking field entry.
                        nm = fegamedata.area_names_en().get(gid)
                    vals["name"] = nm or "Field %d" % gid
                elif args.field_names:
                    names = [x for x in args.field_names.split(",") if x]
                    if is_capital:
                        vals["name"] = args.capital_name
                    elif gid - 1 < len(names):
                        vals["name"] = names[gid - 1]
                    else:
                        vals["name"] = "Field %d" % gid
                contest = territory.field_contest(island, args)
                if contest:
                    # KEY: PER-ISLAND DEFENDER OVERRIDE -- the Elena black screen fix
                    # (2026-08-27). The map-select home-field resolve 0x05009240
                    # (called from state 7, 0x05105eed) accepts a group ONLY when
                    #     [group+0x78] == [char+0x30]  AND  ([group+0x8c] & 8)
                    # where [char+0x30] is the 0xD002 `force` field = the player's
                    # NATION (read at 0x0502513d; felobby CHAR_FIELDS `force`->0x30).
                    # [group+0x78] is `def_id`. With one DEF:ATK pair for every
                    # field (`--field-nations 1:5`) EVERY group has def_id=1, so
                    # ONLY nation 1 has a home field: Lex (force 1) resolves and
                    # enters; Elena (force 5) matches no group, the resolve returns
                    # NULL every lap, state 7 restarts the island sweep forever ->
                    # the 267-lap 0x4011 flood that renders as a black screen,
                    # and 0x2000 MSG_ENTER_AREA is never sent. Giving each island
                    # its own defender means every nation 1..5 owns exactly one
                    # home field, so the resolve succeeds for whatever `force` the
                    # character carries. Unset -> old single-pair behaviour (so a
                    # capture replay is unchanged). The rule lives in
                    # field_contest() so the force records agree with it.
                    dfn, atk = contest
                    # KEY: AND THE REAL OWNER BEATS THE KNOB (2026-09-12). SE's own
                    # continent-map page says a normal field is marked with the
                    # ICON OF THE NATION THAT HOLDS IT -- 「通常フィールドには国
                    # アイコンが表示されます」 (guide/interface/interface02) -- and
                    # [group+0x78] `def_id` is the only owning-nation field on the
                    # wire, so that icon is this value. field_contest answers per
                    # ISLAND from --field-nations/--field-defenders: a static
                    # constant for all fifteen fields of a continent. So the
                    # frontier island, which the five nations split three ways
                    # each, showed every field as nation 1's -- and a field that
                    # CHANGED HANDS in a war never changed on the map, because
                    # nothing in this record came from the territory board.
                    #
                    # territory_owner is that board: the same function force_vals
                    # counts held fields with, and the one fecampaign moves when a
                    # war resolves. It wins here; the knob stays the fallback for
                    # a field it has no answer for, which keeps the 2026-08-27
                    # home-field resolve working (every nation still owns its own
                    # continent, so [group+0x78] == [char+0x30] still matches for
                    # every `force`).
                    own = None
                    if getattr(args, "field_owner", "board") == "board":
                        own = territory.territory_owner(gid, args)
                    if own:
                        dfn = int(own)
                    # ...and the ATTACKER is whoever actually declared on this
                    # field, not a per-island constant. A field at peace has
                    # none: fecampaign answers 0 and the knob's value stands.
                    live_atk = campaignview.campaign_attacker(args, gid)
                    if live_atk:
                        atk = int(live_atk)
                    vals["def_id"] = dfn
                    vals["def_name"] = args.force_table.get(dfn, {}).get("name", "")
                    vals["atk_id"] = atk
                    vals["atk_name"] = args.force_table.get(atk, {}).get("name", "")
                    # THE WHOLE `Char[D%d/%dvsA%d/%d...]` BLOCK, from the war
                    # itself (2026-09-12). Only def_chars was served before, and
                    # only it of the four: the D and A ceilings and the attacker
                    # count were absent, so a field at war could not show its
                    # strengths. The ceilings are the per-side cap -- SE
                    # warsystem01's 最大50人対50人, fecampaign --war-cap.
                    if str(getattr(args, "field_census", "on") or "off") == "on":
                        d = campaignview.campaign_side_count(args, gid, "def")
                        a_ = campaignview.campaign_side_count(args, gid, "atk")
                        if d is not None:
                            vals["def_chars"] = d
                            vals["def_now"] = d
                            cap = campaignview.campaign_war_cap(args)
                            if cap:
                                vals["def_max"] = cap
                                vals["atk_max"] = cap
                        if a_ is not None:
                            vals["atk_now"] = a_
                # Marker position on the continent map (mask bit 17, the
                # [%03d-%03d] pair in the client's own FIELD log line). Never
                # served -> every field's marker rendered at (0,0), stacked in
                # the top-left corner (reported the first time the map
                # was seen live, 2026-08-22). The coordinate SPACE is unmeasured;
                # these defaults exist to spread the markers so it can be
                # calibrated from one look at the screen.
                if is_capital and args.capital_coords:
                    # WARNING: A CAPITAL GOT NO MARKER AT ALL. --field-coords is
                    # indexed `gid - 1`, and a capital id (21..95) is far past
                    # a five-entry list, so bit 17 was never set and the marker
                    # rendered at the origin -- which the client then CLAMPS to
                    # 40 (0x0500fae6 `mov word [ebp+0x90], 0x28`), planting it
                    # hard in the corner. Reported from the screen 2026-09-04.
                    cx, _, cy = args.capital_coords.partition(":")
                    try:
                        vals["coords"] = (int(cx, 0), int(cy, 0))
                    except ValueError:
                        pass
                elif args.field_coords:
                    pairs = [p for p in args.field_coords.split(",") if p]
                    if gid - 1 < len(pairs):
                        cx, _, cy = pairs[gid - 1].partition(":")
                        try:
                            vals["coords"] = (int(cx, 0), int(cy, 0))
                        except ValueError:
                            pass
                # The Hmap map number (mask bit 18). See GROUP_FIELDS: on the
                # OUTDOOR entry path this decides which Data\Hmap\mapNN.pak is
                # loaded, and a value the client's table 0x52d30e0 does not know
                # is a null deref, not a blank field. Only 1..14 are shipped.
                if args.field_hmaps:
                    maps = [m for m in args.field_hmaps.split(",") if m]
                    if maps:
                        try:
                            vals["hmap"] = int(maps[(gid - 1) % len(maps)], 0)
                        except ValueError:
                            pass
                if is_capital and args.capital_attacker != "on":
                    # \U0001f534 THIS DROPPED THE ATTACKER PAIR ON THE FIRST TRY AND THE
                    # MAP SCREEN CRASHED (2026-09-04). Reasoning: "a town is not
                    # contested". The record was verified afterwards to be a
                    # clean SUBSET of a war field's -- same bits minus these
                    # two, body well-formed -- so it PARSES; that is not the
                    # same as the client tolerating it. atk_name is an INLINE
                    # buffer at struct offset 0x97 and atk_id a u32 at 0xB8, so
                    # omitting them leaves those bytes of the 0xCC record as
                    # whatever the allocation held, and the map screen reads a
                    # name out of there.
                    # Default is now ON: a capital carries the SAME fields as a
                    # war field, differing only in its id and name, because a
                    # war-field record demonstrably does not crash that screen.
                    # `off` reproduces the crash on purpose -- it is the other
                    # half of the A/B, not a setting anyone should run.
                    vals.pop("atk_id", None)
                    vals.pop("atk_name", None)
                if territory.world_from_dat(args):
                    # KEY: THE SHIPPED WORLD WINS. Everything above authored a
                    # field by formula -- a name off a list index, a def_id off
                    # the island number, an hmap cycled `(gid-1) % 5`, a marker
                    # off a five-entry --field-coords. fet_area has the real
                    # value for all 95 areas, so overwrite rather than restate:
                    # the blocks above stay exactly as they are and keep
                    # working for --world synthetic.
                    row = fegamedata.areas().get(gid)
                    if row:
                        # def_id is the LIVE holder, which starts as SE's board
                        # and moves when a war is won. atk_id stays whatever
                        # --field-nations said until the war layer owns it;
                        # dropping it here would leave the 0xCC record's
                        # +0x97/+0xB8 as allocation junk, which is the crash
                        # recorded under --capital-attacker.
                        vals["def_id"] = territory.territory_owner(gid, args)
                        vals["def_name"] = args.force_table.get(
                            vals["def_id"], {}).get("name", "")
                        # WARNING: NEVER LEAVE THE ATTACKER PAIR UNSET. atk_name is
                        # an INLINE buffer at struct offset 0x97 and atk_id a
                        # u32 at 0xB8, so an unserved pair is whatever the 0xCC
                        # allocation happened to hold -- and the map screen
                        # reads a name out of there. That crashed it on
                        # 2026-09-04 (see --capital-attacker). With 95 fields
                        # and only one DEF:ATK pair configured, most fields
                        # would have hit exactly that.
                        #
                        # A field nobody is attacking gets id -1 and an EMPTY
                        # name: -1 is what the client's own FIELD line prints
                        # for "unset" ((ID:-1)), and an empty cstr is one NUL
                        # into a buffer rather than no write at all. PARTIAL: that -1
                        # is SE's "no attacker" is a reading; that junk is
                        # wrong is measured.
                        vals.setdefault("atk_id", 0xFFFFFFFF)
                        vals.setdefault("atk_name", "")
                        # Same hazard, and this one the map screen acts on.
                        # 0x051067d2 does `cmp byte [group+4], 4` and refuses
                        # the field with "the server is down" (0x052e4d14) on a
                        # match. +4 is mask bit 0, unset by default, so the
                        # byte is whatever the count*0xCC allocation held --
                        # and a field that happens to hold 4 is unreachable for
                        # no reason anyone could see. Serve 0.
                        vals.setdefault("status", 0)
                        # WARNING: hmap is a NULL DEREF, not a blank field, if the
                        # client's table 0x52d30e0 does not know it. A capital
                        # is 101..105 there -- in the table but NOT shipped as
                        # a pak -- which is fine because the capital loader
                        # never consults it, and dangerous if a capital ever
                        # takes the outdoor arm. Serve what the table says.
                        if row.get("hmap"):
                            vals["hmap"] = row["hmap"]
                        vals["coords"] = (row["map_x"], row["map_y"])
                        # The flags word, including bit 3 = CAPITAL. --field-home
                        # still narrows it, so a run can go back to the old
                        # "every field is a home field" behaviour.
                        flags = row.get("flags", 0)
                        if getattr(args, "field_home", "all") == "all":
                            flags |= 0x8
                        vals["g8c"] = flags
                        # THE ADJACENCY LIST. Without it the client's frontier
                        # test collapses to "my nation already holds this" and
                        # no enemy field is enterable by anyone -- see
                        # GROUP_FIELDS bit 12.
                        if args.world_neighbours == "on":
                            vals["neighbours"] = row["neighbours"]
                # KEY: THE ALWAYS-READ TAIL (see build_group_record), from the
                # FINAL owner and attacker above. +0xC0 = the attacker's palette
                # index -- only a real nation; "no attacker" is 0xFFFFFFFF and
                # 0x5008680 has no bounds check, so anything else sends 0.
                # +0xC4 = how many fields the holder has, counted exactly as
                # fecampaign.force_vals counts the nation record's pairs, so
                # the field panel and the nation legend cannot disagree.
                if getattr(args, "field_tail", "on") == "on":
                    _atk = int(vals.get("atk_id", 0) or 0)
                    vals["tail_color"] = _atk if _atk in args.force_table else 0
                    _hold = int(vals.get("def_id", 0) or 0)
                    if _hold:
                        vals["tail_held"] = sum(
                            1 for _a in fegamedata.areas()
                            if territory.territory_owner(_a, args) == _hold)
                _r = zones.build_group_record(gid, vals)
                _recs.append(_r)
                payload += _r
            wire.send(conn, outbound, wire.inner_msg(0x3031, payload), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            # WARNING: THIS PRINTED `n` -- the war-field count -- while the record
            # list could be longer. On 2026-09-04 it reported "1 group(s)" for
            # an island that was announcing TWO, so the first question asked
            # about the capital crash ("did it even go out?") could not be
            # answered from our own log. Report what was actually SENT.
            # Print the MASK per record, not the literal "mask 0" this used
            # to claim. Reconstructing masks by hand is how the 5-byte tail was
            # finally cornered on 2026-09-04; the log should just say them.
            print("[feworld]    GROUP DETAIL for island %d -> 0x3031 with %d "
                  "group(s) %s, masks %s, %d bytes"
                  % (island, len(gids), gids,
                     ["0x%08X" % struct.unpack_from(">I", r, 4)[0]
                      for r in _recs],
                     len(payload)), flush=True)
            # A 0x4011 arriving AFTER the enter-area round trip is mode 0xb
            # substate 1 asking for the entered field's detail -- the client is
            # past the +0x509 clear (0x4ff9d64, in the field-entry step), so
            # 0x100E lands and sticks. ONCE per entry: measured live 2026-08-23,
            # the in-field war screen re-sends 0x4011 EVERY FRAME, and answering
            # each with 0x100E flooded the client log with `> MSG_ADD_COMPLETE`
            # (harmless -- the arm is an idempotent flag-set -- but noise). The
            # 0x2000 handler clears this flag so a re-entry gets its 0x100E.
            if (sess._SESSION.get("in_field") and args.add_complete == "on"
                    and not sess._SESSION.get("add_complete_sent")):
                sess._SESSION["add_complete_sent"] = True
                # THE ADD STREAM COMES FIRST, then 0x100E says it is complete --
                # that is what 0x100E MEANS ("the initial ADD stream is done, go
                # live"). Sending the entities after it would be telling the
                # client the stream finished and then continuing it.
                sess._SESSION.pop("self_redressed", None)
                if args.add_self in ("on", "client"):
                    if args.add_self == "on":
                        entities.add_entities(conn, outbound, mode, be, args)
                        # the self record just rebuilt the body and stripped
                        # the bag reader's armour -- put it back before 0x100E
                        # (see self_redress_push; --self-redress, default off)
                        equipment.self_redress_push(conn, outbound, mode, be, args)
                    else:
                        print("[feworld]    --add-self client: NO self 0x1006 -- "
                              "the client's own 'Chara 1' stands (dressed by the "
                              "lobby list, USESKILLRDY from 0x04fe78aa, items in "
                              "the manager); the stat block goes by 0x2024 after "
                              "0x100E", flush=True)
                    # WARNING: THE HUD EXP GAUGE'S MAX GOES OUT FIRST -- ahead of
                    # skill_list_push, which carries the NUMERATOR (the
                    # per-class EXP dword), and ahead of the 0x100E that
                    # releases the field. The gauge CACHES [unit+0x135c] when
                    # it is built and can never refresh it while the exp is
                    # above that cache, so a max written later in this burst
                    # is a max the widget never reads -- the deadlock behind
                    # five reports of "the bar shows 100%". Disassembly in
                    # exp_max_push. It still rides AFTER the ADD, for the same
                    # reason the skill list does (below).
                    progression.exp_max_push(conn, outbound, mode, be, args,
                                 "field entry, ahead of the gauge")
                    # ...and the skills that unit already owns. It has to
                    # ride AFTER the ADD, because 0x1075 resolves the unit by
                    # id (0x0504CB30) and writes onto it -- on a miss it
                    # consumes the stream and writes nothing, which is a silent
                    # no-op, not an error.
                    if args.skill_list != "off":
                        skilllist.skill_list_push(conn, outbound, mode, be, args)
                    # ...and the SKILL BAR, which is a separate thing from the
                    # acquired list: 0x1075 writes [unit+0xA14+id*2] (do I own
                    # it), the palette is eight UI slots the player filled by
                    # double-clicking. Nothing else restores it, so without
                    # this the bar is empty every session -- the same shape
                    # --skill-list fixed for the skill window.
                    # THE PALETTE REPLAY, 2026-09-08: it was gated on
                    # --skill-palette on, and prod runs the default 'request'
                    # -- so the stored bar was never restored and the player
                    # re-set it every session ("my equipped skills did not
                    # persist"). It is now sent in every mode, but NOT here:
                    # both 0x2029 workers deref the HUD singletons with no null
                    # check and the HUD is built after PLAY_START, so it is
                    # HELD until the client's first 28-byte 0x2023 -- the same
                    # beat the war notify waits for -- and sent from there.
                    if getattr(args, "skill_palette", "request") != "off":
                        sess._SESSION["palette_replay_due"] = True
                arrival.add_complete(conn, outbound, mode, be, args)
                if args.add_self == "client":
                    unitstate.stat_2024_push(conn, outbound, mode, be, args)
                entities.draw_self_push(conn, outbound, mode, be, args)
                # WARNING: NOT INDOORS. A room is a UI scene the player stands in;
                # live 2026-09-05 the arms shop received the field's nine
                # orcs, Bob and the goblin along with the player, because
                # the room rides the war field's gid. Population is for the
                # field, not the shop.
                if sess._SESSION.get("room", -1) == -1:
                    entities.npc_push(conn, outbound, mode, be, args)
                    entities.monster_push(conn, outbound, mode, be, args)
                    buildings.building_push(conn, outbound, mode, be, args)
                    town.town_push(conn, outbound, mode, be, args)
                    populate.spawn_push(conn, outbound, mode, be, args)
                    doors.door_table_log(args, sess._SESSION.get("field"))
                else:
                    # indoors only the ROOM's own keepers (town file key
                    # "room:N") -- no field NPCs, monsters, buildings, spawns
                    print("[feworld]    in room %s -- serving its keepers only"
                          % sess._SESSION.get("room"), flush=True)
                    town.town_push(conn, outbound, mode, be, args)
                if getattr(args, "unit_physics", "table") == "push":
                    movement.unit_speed_push(conn, outbound, mode, be, args)
                else:
                    # the client's own unit takes walk speed, jump launch and
                    # gravity from the class table (0x0506a5e0 copies rec+0x44/
                    # +0x48/+0x4c into +0x4d4/+0x4dc/+0x4e4); our 1.0 over the
                    # top of that read as "moves so much slower" (run 5).
                    print("[feworld]    --add-self client: no --unit-speed / "
                          "--jump-phys push -- the class table's own values "
                          "stand on the client's unit", flush=True)
                inventory.bag_size_push(conn, outbound, mode, be, args)
                # THE STORED EQUIPMENT, RE-APPLIED AFTER THE FIELD IS BUILT.
                # Not in the 0x1000 bag record: setting the marker there made
                # the bag reader's tail equip DURING field entry, which dressed
                # the character over the default outfit and froze movement
                # (live 2026-09-05 #14). Here the avatar already exists and the
                # same Equip call is the client's own mid-session path.
                if getattr(args, "field_items", "bag") in ("on", "bag"):
                    rows = equipment.stored_equip_rows(args)
                    rows = [r for r in rows if r[3] is not None]
                    if rows and args.add_self in ("on", "client"):
                        # WARNING: run 5 (2026-09-08): the bag record's markers
                        # dressed the client's unit, and gate 4 STILL read
                        # `NOT IN MANAGER` for the wand in hand. The bag reader
                        # registers each item (0x504cd00) into whatever
                        # [0x5339cc8] holds while 0x1000 is applied -- during
                        # the load, before the field scene stands -- and by the
                        # time a cast looks, that registration is gone. The
                        # 0x107A worker (0x0503d540) does the same lookup ->
                        # construct -> register (0x0503d614) -> Equip, so one
                        # 0x107A per worn item AFTER the field is live puts the
                        # worn objects in the manager the gate walks. HELD for
                        # the first 28-byte 0x2023, with the palette.
                        # (--self-redress: minus the armour already back on)
                        sess._SESSION["equip_replay_due"] = equipment.replay_rows_after_redress(rows)
                    elif rows:
                        equipment.equip_reflect_push(conn, outbound, mode, be, args,
                                           rows, "stored equipment, on entry",
                                           "entry")
                probes.stat_probe_push(conn, outbound, mode, be, args)
                probes.stat_probe2_push(conn, outbound, mode, be, args)
                # THE STATE WORD, and with it every skill gate. It rides here
                # rather than earlier because 0x2024's applier resolves the
                # unit by id and does nothing on a miss -- the avatar has to
                # exist first. See unit_state_push.
                unitstate.unit_state_push(conn, outbound, mode, be, args)
                equipment.cast_report_log(args, "on field entry")
                if getattr(args, "unit_physics", "table") == "push":
                    movement.jump_phys_push(conn, outbound, mode, be, args)
                wallet.wallet_push(conn, outbound, mode, be, args)
                wallet.crystal_push(conn, outbound, mode, be, args)
                # This visit's battle starts at zero; the STORED EXP total
                # rides in behind the wallet so the Status screen shows what
                # the character has already earned (see exp_resume_push).
                combat.battle_reset()
                # WARNING: A DEATH MUST NOT SURVIVE A FIELD ENTRY. Live 2026-09-09:
                # a player died, pressed Return to Base, and came back with
                # the DEAD bit still set and 0 HP -- the revive is driven off
                # the 0x2023 tick and the tick is gated on `in_field`, so it
                # did not run until the next field entry two minutes later.
                # Whatever else is true, arriving in a field means alive.
                if sess._SESSION.pop("player_dead", False) or "player_hp" in sess._SESSION:
                    sess._SESSION["player_hp"] = death.player_hp_max(args)
                    sess._SESSION.pop("revive_at", None)
                    if getattr(args, "monster_attack", "off") == "on":
                        death.player_dead_push(conn, outbound, mode, be, args, False)
                        death.player_hp_push(conn, outbound, mode, be, args,
                                       death.player_hp_max(args))
                        print("[feworld]    entering a field ALIVE: HP %d/%d, "
                              "DEAD cleared" % (death.player_hp_max(args),
                                                death.player_hp_max(args)), flush=True)
                progression.exp_resume_push(conn, outbound, mode, be, args)
                # skill points (earned by level) and a full Pw gauge -- see
                # the PROGRESSION block
                progression.progress_entry_push(conn, outbound, mode, be, args)
                chat.chat_push(conn, outbound, mode, be, args)
            # WARNING: WE USED TO PUSH 0x1000 MSG_ENTER_AREA_OK HERE. That was wrong,
            # and it is why the live 2026-08-18 run answered with
            # `!!!異常なエンターエリアを受信しました。!!!` and never set the current
            # island. 0x1000 is the ANSWER to the client's own 0x2000
            # MSG_ENTER_AREA, and the client only accepts it after it has armed
            # [player+0x295] = 0xFF by sending that request. See enter_area().
            #
            # --push-enter-area restores the old behaviour for a comparison run;
            # it is off by default because the disassembly says it cannot work.
            if n and args.push_enter_area and not sess._SESSION.get("entered"):
                sess._SESSION["entered"] = True
                arrival.enter_area(conn, outbound, mode, be, args,
                           zones._group_id(island, 0, n), 0)
                if args.war != "none":
                    war.war_notify(conn, outbound, mode, be, args)
            elif n:
                # Remember what we would answer 0x2000 with, and wait for it.
                sess._SESSION["group"] = zones._group_id(island, 0, n)
                print("[feworld]    waiting for the client's 0x2000 "
                      "MSG_ENTER_AREA (it must arm itself before 0x1000 is "
                      "accepted); will answer with group=%d"
                      % sess._SESSION["group"], flush=True)
                # WARNING: SENDING THIS ONCE IS NOT ENOUGH -- IT CAN MISS THE SCREEN.
                # Measured on prod 2026-08-19: feworld sent exactly one 0x1018
                # and the client's log contains NO `MsgType->0x1018` at all,
                # while `MsgType->0x303C` (logged by the SAME handler) appears
                # 795 times. So the frame arrived when no screen was active to
                # receive it and was simply dropped. On 08-18 a single 0x1018 did
                # land -- the difference is timing, not delivery.
                #
                # 0x1018 is the arm that is safe to repeat: per its handler
                # (0x0511843b) it sets war-view [+0x70]=0 and just stores u32_3/
                # u32_4, with no scene transition (that is 0x1019, which must
                # never be sent blind). So push it once per LAP instead of once
                # per session and let it land whenever the screen is up.
                #
                # WARNING: BUT NOT IN-FIELD. Measured live 2026-08-23: the war-prep
                # window polls the group-detail machine every frame WHILE its
                # arm byte [screen+0x62] is set, and clears it when the round
                # trip completes (0x5117c87..0x5117cbe, success log "Island
                # Info was Updated. Open War Prepare Window."). Re-pushing
                # 0x1018 with every 0x4011 answer RE-ARMS +0x62 -- we were
                # feeding the very loop that flooded 0x4011 at frame rate. Push
                # per-lap only BEFORE the field entry; once per entry after.
                # WARNING: AND OUT OF FIELD IT IS STILL UNBOUNDED -- measured on prod
                # 2026-08-24, a brand-new character on its first
                # login sent 3,185 and then 1,401 0x4011 in two sessions, each
                # answered with a fresh 0x1018, never once reaching field entry.
                # The comment above says "push per-lap only BEFORE the field
                # entry" and that is exactly the case with no ceiling: a client
                # that never enters the field re-arms [screen+0x62] forever, at
                # frame rate, and the player sees a black screen.
                #
                # --war-max-laps caps the PRE-FIELD pushes. Default 0 = the
                # unbounded behaviour, deliberately, because per-lap was chosen
                # for a measured reason -- a single 0x1018 can arrive when no
                # screen is up and be dropped (prod 2026-08-19: one sent, zero
                # `MsgType->0x1018` in the client log) -- so a cap trades a
                # known delivery problem for an unknown one and must not become
                # the default until it is measured. It is also NOT established
                # that this loop is what blacks the screen: no working session
                # was captured in the same window to compare against.
                lap_cap = getattr(args, "war_max_laps", 0)
                capped = (lap_cap and not sess._SESSION.get("in_field")
                          and sess._SESSION.get("warned", 0) >= lap_cap)
                if capped and not sess._SESSION.get("war_capped"):
                    sess._SESSION["war_capped"] = True
                    print("[feworld]    --war-max-laps %d reached out of field: "
                          "no further 0x1018. If the war-prep window now settles "
                          "instead of re-polling 0x4011 every frame, the re-arm "
                          "WAS the loop; if 0x4011 keeps coming, it is not and "
                          "this cap should go back to 0." % lap_cap, flush=True)
                if (args.war != "none" and not capped
                        and (not sess._SESSION.get("in_field")
                             or not sess._SESSION.get("war_notified"))):
                    if sess._SESSION.get("in_field"):
                        sess._SESSION["war_notified"] = True
                    sess._SESSION["warned"] = sess._SESSION.get("warned", 0) + 1
                    war.war_notify(conn, outbound, mode, be, args)
                    # 0x1015 rides immediately behind the notify that arms the
                    # wait, in the same burst, so the two cannot be separated by
                    # a lap. Once per entry only -- the arm advances a phase, and
                    # re-sending a phase advance every lap is exactly the
                    # re-arming loop the 0x1018 comment above warns about.
                    if (args.war_start == "on" and sess._SESSION.get("in_field")
                            and not sess._SESSION.get("war_started")):
                        sess._SESSION["war_started"] = True
                        war.war_start(conn, outbound, mode, be, args)
        elif real_id == 0x2018:
            # KEY: MSG_DECIDE_COUNTRY_REQUEST -- THE ARMY CHOICE, and the client
            # asks it the moment the battle actually starts.
            #
            # Measured 2026-08-24 in the client's own debug log (the local
            # per-pid shim log, polshim.<pid>.log -- NOT the shipped
            # shim-<host>.log, which is a different file and cost me a round):
            #
            #     MsgType->0x1015          <- our war-phase advance lands
            #     PLAY_START done.         <- THE GAME STARTS
            #     << MSG_DECIDE_COUNTRY_REQUEST
            #     army choice req sent : 0
            #     [Data\Window\battlestart.tex] Stand-By ok.
            #     nation state: now at war.
            #
            # So the sequence is: answer 0x2084, advance the phase with 0x1015,
            # the client starts the battle -- and then asks us which army it is
            # fighting for and waits. We had never answered.
            #
            # The sender is CWarPrepareWindow's vtable slot +0x7c (0x05118940):
            # it logs both strings above, sets [screen+0x70] = 1 ("request in
            # flight"), and registers exactly two acceptable replies as u16s at
            # 0x05118989/0x05118990 -- 0x1020 OK and 0x1021 NG. Body is one u32,
            # the chosen army; the live wire bytes `20 18 00 00 00 00` are that
            # u32 = 0, which is also what the client's own log line prints.
            #
            # 0x1020's arm (0x05117f15) READS NOTHING -- zero stream-reader
            # calls -- so the OK is header-only, like 0x100E and 0x3016. It
            # clears [screen+0x70], clears scene pending bit 0x80 (0x05117f24),
            # rebuilds the window through vtable +0x74 with (3,0), and sets
            # [screen+0x61] = 0.
            #
            # WARNING: NG (0x1021, arm 0x05117f7c) is NOT the safe default here: it
            # sets [screen+0x68] = -1 and re-sends. Answer OK unless
            # explicitly A/B-ing the refusal path.
            f = inner[2:]
            army = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            if args.decide_country == "off":
                print("[feworld]    0x2018 MSG_DECIDE_COUNTRY_REQUEST army=%d "
                      "-- NOT answering (--decide-country off)" % army, flush=True)
            else:
                mid = 0x1020 if args.decide_country == "ok" else 0x1021
                wire.send(conn, outbound, wire.inner_msg(mid, b"", wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x%04X MSG_DECIDE_COUNTRY_%s "
                      "(empty body) for army=%d -- the client asked this right "
                      "after 'PLAY_START done.'; watch its log for the reply and "
                      "for [scene+0x528] bit 0x80 clearing again"
                      % (mid, "OK" if mid == 0x1020 else "NG", army), flush=True)
        elif real_id == 0x2084:
            # KEY: 0x2084 MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_REQUEST [u32 AREA id]
            # -- THE WAR-PREP WINDOW'S OWN 1-SECOND HEARTBEAT, and until now we
            # answered it with nothing at all.
            #
            # It is the war-map screen's vtable slot +0x78 (0x05118900 on vtable
            # 0x528c0b0), fired by the 1s tick at 0x05117cd4 for as long as
            # [screen+0x61] is set -- and the ONLY thing that sets +0x61 is the
            # 0x1018/0x1019 arm at 0x051184de, i.e. OUR war notify. So we armed
            # the poll and then never fed it. Measured live 2026-08-24 in the
            # feworld log: `id=0x2084, 4 bytes of fields  20 84 00 00 00 01`,
            # once a second, no reply, forever.
            #
            # The body is one u32 = [scene+0x4ec] (0x05118910).
            #
            # WARNING: THAT IS THE AREA, NOT THE ISLAND, and this handler called it
            # islandId for two weeks. [scene+0x4ec] is the GROUP/AREA id -- the
            # same word the capital predicate 0x04ff7ef0 tests on entry and the
            # move classifier 0x04ffbad5 bounds against 95. It read as an
            # island because the only frame ever captured said 1, and under
            # --islands 5 --groups 1 the island and its single group BOTH have
            # id 1: the observation could not tell them apart.
            # an earlier note
            #
            # Under --world dat they diverge -- area 39 is on island 3 -- so
            # the poll now names a real field, and a war layer that keys off
            # this must key off the FIELD.
            #
            # THE ANSWER IS 0x1127 MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_OK, and
            # it is reachable: the transport tries three handlers in turn
            # (0x050532f0 -> 0x05023ad0 -> 0x05057d50, each returning al=1 when
            # it consumed the id, chain at 0x0504734b..0x05047394). The main
            # dispatcher's tree drops 0x1127 (0x050549c7 `cmp 0x1123 / jg` ->
            # table 0x5057620 has no entry for it), but the THIRD handler
            # 0x05057d50 takes it: `cmp 0x1089 / jg 0x05058189` -> byte table
            # 0x5058c18 (ids 0x108A..0x1127) slot 0x9d -> arm 0x050581A8.
            #
            # THE BODY IS FOUR u16 (ntohs -- reader 0x5045e30 -> 0x522f970 ->
            # the ws2_32 thunk 0x524d3b0), read in this order at 0x050581af..:
            #
            #     u16 attack_count, u16 defense_count,
            #     u16 attack_max,   u16 defense_max
            #
            # The order is not a guess: the arm logs them through the client's
            # own format string 0x52d5fbc,
            #     ">MSG_GET_INFO_OF_PROCLAMATION_OF_WAR_OK
            #       Attack  [Count %3d / Max %3d]  %08d
            #       Defense [Count %3d / Max %3d]"
            # with args (read1, read3, tick, read2, read4) -- so read1/read3 are
            # the ATTACK pair and read2/read4 the DEFENSE pair.
            #
            # Then, if the scene [0x5336d1c] and the field/war manager
            # [0x5336f74] are both live, it stores all four into the manager via
            # 0x5001c60 -> 0x5001c40 (`[mgr + idx*2 + 0x5c] = u16`):
            #
            #     mgr+0x5c = defense_count   mgr+0x5e = defense_max
            #     mgr+0x60 = attack_count    mgr+0x62 = attack_max
            #
            # -- i.e. the DEFEND pair first, which is the same order as the
            # "Defend n/m vs Attack n/m" the war UI draws. (The manager's other
            # half, [mgr + idx*4 + 0x50] via 0x5001200/0x5001210, is where
            # 0x1018/0x1019's first two u32s land; the war-prep window reads
            # slots 0 and 1 back at 0x051157ff.. and looks each up in the 0xa00-
            # byte record table [0x5336fa8] by `[rec+8] == key` (0x5005560),
            # then draws that record's nation byte [rec+0x1bc] through the
            # per-nation resource table 0x5336fe8 (0x5008680). So those two u32s
            # are RECORD KEYS for the two sides, not counts -- the "Count/Max"
            # reading in war_notify's docstring was inferred from a neighbouring
            # string and is superseded here.)
            #
            # WARNING: UNMEASURED: whether non-zero counts change anything visible. The
            # values are a knob (--war-counts), NOT baked in, and the reply can
            # be turned off wholesale with --proclamation off for a clean A/B.
            f = inner[2:]
            area = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            if args.proclamation == "off":
                print("[feworld]    0x2084 PROCLAMATION-OF-WAR poll area=%d (%s) "
                      "-- NOT ANSWERED (--proclamation off)"
                      % (area, fegamedata.areas().get(area, {}).get("name", "?")),
                      flush=True)
            else:
                atk_c, def_c, atk_m, def_m = args.war_counts
                wire.send(conn, outbound,
                     wire.inner_msg(0x1127,
                               struct.pack(">HHHH", atk_c & 0xFFFF,
                                           def_c & 0xFFFF, atk_m & 0xFFFF,
                                           def_m & 0xFFFF),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                sess._SESSION["proc_polls"] = sess._SESSION.get("proc_polls", 0) + 1
                if sess._SESSION["proc_polls"] <= 3 or sess._SESSION["proc_polls"] % 30 == 0:
                    print("[feworld]    0x2084 PROCLAMATION-OF-WAR poll #%d "
                          "area=%d -> 0x1127 OK Attack[%d/%d] Defense[%d/%d] "
                          "-- expect the client log '>MSG_GET_INFO_OF_"
                          "PROCLAMATION_OF_WAR_OK Attack [Count %d / Max %d]'"
                          % (sess._SESSION["proc_polls"], area, atk_c, atk_m,
                             def_c, def_m, atk_c, atk_m), flush=True)
        elif real_id == 0x400B:
            # The SIBLING of 0x400A, and it has no name string of its own -- so it
            # is left unnamed here rather than guessed at. Same shape, one state
            # word over: the builder (0x0500956d) sets [obj+0x18] = 2, and the
            # only functions that clear it are 0x5009590 -> 0 and 0x50095a0 -> 1.
            #
            # 0x5009590's sole caller is the THIRD arm of the same dispatcher that
            # answered 0x400A (0x050569ce, reached by `sub 0x3016 / dec / dec`),
            # so its id is 0x3018 -- and that arm reads nothing from the message,
            # so the reply carries no fields. The NG setter is called from
            # 0x5056a30, a different arm, if a failure ever needs serving.
            #
            #     0x3018 = OK for 0x400B, no fields
            #
            # Worth noting the two requests were mapped in one pass: walking that
            # dispatcher once for 0x400A had already produced 0x400B's answer
            # before the client asked for it.
            print("[feworld]    0x400B (no fields) -- the 0x400A sibling, "
                  "unnamed in the image", flush=True)
            wire.send(conn, outbound, wire.inner_msg(0x3018), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x3018 (OK for 0x400B, no fields)",
                  flush=True)
        elif real_id == 0x4006:
            # MSG_MOVE_MY_CHARACTER_INFO_TO_AREA_REQUEST [u32 fieldId] -- what
            # the map screen actually sends when the player picks a field
            # (0x04ffb9a0 classifies the transition kind at 0x04ffbad5; kind 0 =
            # this message; kinds 1/2/3 are 0x2017/0x2016/0x20A6). First seen
            # live 2026-08-22, the test player picking "Move to Ruined Plain" on
            # the first map screen ever to render.
            #
            #     0x300C = MSG_MOVE_OK -- NO FIELDS. The arm (0x0505685d in the
            #     compare ladder at 0x0505680d: sub 0x20b2 then sub 0xf5a) reads
            #     nothing, sets [player+0x29a] = 1 (the byte 0x04ffb9a0 armed to
            #     0xFF and polls), runs a socket op on [0x533998c]+0x980 and
            #     queues `/getblacklist <charid>`.
            #     0x300D = MSG_MOVE_NG, [u32 err], sets [player+0x29a] = 0.
            #
            # After the OK the client is expected to walk its own enter-area
            # path (0x2000), which is already answered below.
            f = inner[2:]
            field_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            print("[feworld]    MOVE_MY_CHARACTER_INFO_TO_AREA fieldId=%d"
                  % field_id, flush=True)
            wire.send(conn, outbound, wire.inner_msg(0x300C), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x300C MSG_MOVE_OK (no fields)",
                  flush=True)
        elif real_id == 0x4014:
            # TUTORIAL END, [u32 charid] -- fire-and-forget (the builder at
            # 0x051333d0 registers no replies, so nothing is owed on the wire).
            # The map screen sends it from 0x05105a73 alongside its own log line
            # "チュートリアル終了" (0x52e5d1c) and sets the tutorial-done global
            # 0x52d3a88 = 1 locally. The FIRST world entry is the tutorial: the
            # 0xD008 DECIDE_CHARACTER_OK trailing u8 seeds that global, and 0
            # routes mode 8 into the tutorial map variant, which ends -- by
            # design -- with the client exiting back to the Viewer after its
            # field pick ("please wait" -> clean GameStart return). Persist the
            # flag so felobby serves u8=1 next login and the client boots the
            # NORMAL map instead of replaying the tutorial forever.
            f = inner[2:]
            t_charid = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            if character._store_char_field(args, "tutorial", 1):
                print("[feworld]    TUTORIAL END charid=%d -- persisted "
                      "tutorial=1; next login serves 0xD008 u8=1 (normal map)"
                      % t_charid, flush=True)
            else:
                print("[feworld]    TUTORIAL END charid=%d -- NO stored "
                      "character matched; flag NOT persisted" % t_charid,
                      flush=True)
        elif real_id == 0x4012:
            # MSG_JOIN_FORCE_REQUEST -- "registering nation". Built at 0x05005697
            # as a single dword, logged by the client as
            # `< MSG_JOIN_FORCE_REQUEST ForceID = %d` (0x52d2f7c), and its
            # registration two instructions earlier (0x0500567d) names the only
            # two answers: 0x3033 OK / 0x3034 NG.
            #
            #     0x3033 = MSG_JOIN_FORCE_OK, NO FIELDS AT ALL
            #
            # The OK arm (0x050057ec) reads nothing: it sets the screen's result
            # word [esi+0x54] = 0 and logs `> MSG_JOIN_FORCE_OK`. The NG arm
            # (0x0500580e) is the one that takes a payload -- a u32 error code it
            # resolves to a message via 0x5061290(0x3034, code) -- and sets
            # [esi+0x54] = 1. Both live in the same dispatcher as GET_FORCE_INFO
            # (id - 0x3027 through the tables at 0x5005958 / 0x5005944).
            f = inner[2:]
            force_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            name = args.force_table.get(force_id, {}).get("name", "?")
            print("[feworld]    JOIN FORCE ForceID=%d (%s)" % (force_id, name),
                  flush=True)
            wire.send(conn, outbound, wire.inner_msg(0x3033), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x3033 MSG_JOIN_FORCE_OK (no fields)",
                  flush=True)
            # PERSIST THE CHOICE. Asked outright -- "not sure if it saves it for
            # real". It did not; now it does, into the same character store
            # felobby keeps, keyed by the account and charid that arrived in
            # 0x400F.
            #
            # The 0xD002 field that carries this back IS established now: it is
            # `force` (wire u32 -> [rec+0x30], read at 0x0502513d; CHAR_FIELDS in
            # felobby). The nation-prompt decision is scene mode 6 (0x04ffc7d0):
            # [rec+0x30] == 0 or 0x7FFFFFFF -> mode 7 shows the NationSelect
            # screen; ANY other value skips the prompt and goes straight to the
            # map (mode 8). So a persisted choice really does suppress the
            # prompt on the next login -- and a STALE persisted choice is why
            # "the join-nation flow never runs": the client was told it had
            # already joined.
            if character._store_char(args, force_id):
                print("[feworld]    saved nation ForceID=%d for %r/charid=%s "
                      "-- served back as 0xD002 `force`, so the nation prompt "
                      "will be SKIPPED on the next login"
                      % (force_id, sess._SESSION.get("account"),
                         sess._SESSION.get("charid")), flush=True)
        elif real_id == 0x2000:
            # 🔵 THE MESSAGE THE WORLD DOOR HAS BEEN WAITING FOR.
            #
            #     C->S 0x2000 MSG_ENTER_AREA [u32 areaId]
            #
            # Built at 0x0505e600: it logs `Send : MSG_ENTER_AREA %d`
            # (0x52d6564), pre-registers ids[0]=0x1000 / ids[1]=0x1001 at
            # 0x0505e62e-0x0505e635, writes the area id as one dword (0x5045be0)
            # and flushes. Its caller, the driver at 0x04ff6a30, has ALREADY set
            # [player+0x295] = 0xFF by the time this lands, which is the only
            # state in which the 0x1000 handler (0x04ff80d0) will do anything.
            #
            # Answering it is therefore the ONLY route to a non-negative current
            # island, and so to the war-prepare window.
            f = inner[2:]
            area = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            print("[feworld]    *** 0x2000 MSG_ENTER_AREA area=%d (%d body "
                  "bytes) -- the client armed itself; 0x1000 is now accepted"
                  % (area, len(f)), flush=True)
            if args.capture_only:
                print("[feworld]    --capture-only: NOT answering", flush=True)
                continue
            # Answer with the group the CLIENT asked for, not the last island the
            # sweep happened to poll. Measured live 2026-08-22: the first-ever
            # 0x2000 carried area=1 (the picked field, "Ruined Plain") and the
            # stale _SESSION leftover answered group=5 -- entering the player
            # into the wrong island. The area id is the group/field id the map
            # screen resolved; trust it when it is a group we serve.
            n = max(1, args.groups)
            # (this hardcoded island 5 until 2026-09-04; --islands is the count)
            served_max = zones._group_id(max(1, args.islands), n - 1, n)
            # KEY: AND THAT BOUND IS THE PRE-`dat` NUMBERING (2026-09-12, live).
            # --islands defaults to 5 and --groups to 1, so served_max is FIVE
            # -- while `--world dat` announces all 95 real area ids on the map
            # screen. The test player clicked Rhinelay Valley (area 84, island 6),
            # 84 failed `1 <= area <= served_max`, and the fallback below
            # answered with _SESSION["group"] = _group_id(6, 0, 1) = 6, so the
            # client was told it had entered group 6 -- Schelln Greenwood --
            # while its own loader had already put Rhinelay Valley's terrain on
            # screen. "It says I'm in Schelln Greenwood, but I clicked Rhinelay
            # Valley and the environment is Rhinelay Valley."
            #
            # The right test is the set we actually ANNOUNCED, from the same
            # function that builds the map answer. The synthetic bound stays as
            # the fallback for a world that is not dat-driven.
            served = set()
            try:
                for _i in range(1, max(1, int(args.islands)) + 1):
                    served |= set(zones.group_ids_for(_i, args))
                if territory.world_from_dat(args):
                    served |= set(fegamedata.areas())
            except Exception:                          # noqa: BLE001
                served = set()
            # A capital id is far above served_max (21..95 vs 1..5), so without
            # this the client's own pick was silently rerouted to a war field
            # and the capital could never be entered no matter what we announced.
            announced_caps = {g for gs in zones._CAPITALS.values() for g in gs}
            room = None
            if area in announced_caps or area in zones.CAPITAL_GROUP_IDS:
                group = area
                print("[feworld]    area %d is a CAPITAL (%s) -- entering it. "
                      "The client's predicate 0x04ff7ef0 will take the capital "
                      "branch (0x5001d70) and load %s instead of Data/Hmap."
                      % (area,
                         "announced" if area in announced_caps else
                         "NOT announced on the map screen, but the client knows "
                         "it and we only get here by having sent the player "
                         "ourselves -- a door or a !goto",
                         zones.CAPITAL_MAP.get(area, "DATA/capital/*.oct")),
                      flush=True)
            elif area in served or 1 <= area <= served_max:
                group = area
                if area not in served and area <= served_max:
                    print("[feworld]    (area %d passed the synthetic bound "
                          "%d, not the announced set)" % (area, served_max),
                          flush=True)
            elif (zones.ROOM_AREA_BASE <= area < zones.ROOM_AREA_BASE + 0x100
                  and sess._SESSION.get("field") is not None):
                # KEY: A DOOR DESTINATION we handed out in 0x1166 (see
                # ROOM_AREA_BASE). Enter the room on the island's war-field
                # gid -- the capital's own id would load the town again --
                # and remember the field to come back OUT to.
                room = area - zones.ROOM_AREA_BASE
                outside = sess._SESSION["field"]
                island = next((i for i in range(1, int(args.islands) + 1)
                               if outside in zones.group_ids_for(i, args)), 1)
                group = zones._group_id(island, 0, n)
                sess._SESSION["outside"] = outside
                print("[feworld]    area 0x%X = DOOR into room %d (%s) -- "
                      "entering it on gid %d (island %d); the exit returns "
                      "to field %d" % (area, room, zones.room_label(room), group,
                                       island, outside), flush=True)
            elif area == 0xFFFFFFFF and sess._SESSION.get("field") is not None:
                # KEY: THE ROOM EXIT, measured live 2026-09-04: walking out of
                # the arms shop's door the client sent 0x2017 MOVE_FIELD -1
                # and then 0x2000 area=-1 (the transition classifier at
                # 0x04ffbad5 makes -1 kind 1 because it equals the -1 we serve
                # at [scene+0x4e8]). We answered with --field-rooms' shop
                # again and put the player straight back inside. -1 is "out":
                # the same group, forced OUTDOORS (room -1) -- or the capital
                # loader, which wins on the id anyway.
                group = sess._SESSION.pop("outside", None) or sess._SESSION["field"]
                room = -1
                print("[feworld]    area -1 = LEAVING the room -> back to "
                      "group %d outdoors (was room %s)"
                      % (group, sess._SESSION.get("room")), flush=True)
            else:
                group = sess._SESSION.get("group") or zones._group_id(1, 0, n)
                print("[feworld]    WARNING: area %d (0x%08X) is not a served group, "
                      "an announced capital or -1 -- falling back to group %d. "
                      "If this followed walking into a DOOR, this number is "
                      "the door protocol: record it."
                      % (area, area, group), flush=True)
            # WARNING: 0xE00D IS NOT THE EXIT FIX -- DISPROVEN LIVE 2026-08-23.
            # Two runs (0xE00D before AND after 0x1000): the client exits
            # identically. The "cannot leave field in this state" log the
            # before-order produced PROVES 0xE00D decodes and dispatches, but
            # the exit is independent of it. The 0x1000 handler (0x04ff80d0,
            # re-read end to end) resolves group->island, sets the current
            # field, and returns cleanly -- no exit path -- and the field
            # asset loader (mode 0xa substate 3: building.pak/common2.pak/
            # monster00.pak) never even starts. GameStart returns right after
            # `erase >>> 0x2000`, before the next enter-area poll. So the exit
            # is GameStart completing the map phase, which is the memory's
            # original relaunch/field-boot frontier -- NOT a missing message.
            #
            # 0xE00D is left in as an off-by-default knob (it may belong in the
            # field/battle phase we haven't reached). When enabled, send it
            # AFTER 0x1000 so [scene+0x4ec] is already the entered field and
            # the arm takes the clean same-field path instead of tripping the
            # leave-field guard.
            arrival.enter_area(conn, outbound, mode, be, args, group, area, room=room)
            # THE GROUP ACTUALLY ENTERED. `group` above (the 0x4011 leftover)
            # is what we WOULD answer with and is rewritten every lap; this is
            # what war_notify's capital guard reads.
            sess._SESSION["field"] = group
            # A new field is a new set of monsters: the registry npc_send fills
            # is keyed by object id, and ids are re-used across fields.
            # KEY: SHARED (2026-09-13): outdoors the registry IS the field's --
            # one set for every player in it (field_mobs); spawn_push shows it
            # to this client and mobs_shown opens the relays to it.
            sess._SESSION.pop("mobs_shown", None)
            if (monsters.mobs_shared(args) and room == -1
                    and group not in zones.CAPITAL_GROUP_IDS):
                sess._SESSION["mobs"] = monsters.field_mobs(group)["mobs"]
                sess._SESSION["mobs_field"] = int(group)
            else:
                sess._SESSION["mobs"] = {}
                sess._SESSION.pop("mobs_field", None)
            # The NEXT 0x4011 the client sends is mode 0xb substate 1 (post
            # field entry) -- the trigger for 0x100E MSG_ADD_COMPLETE.
            sess._SESSION["in_field"] = True
            sess._SESSION["add_complete_sent"] = False
            # arm the EXP-threshold seed: the client builds its own unit AND
            # its HUD while the field loads, and the HUD's exp gauge caches
            # [unit+0x135c] at construction. See exp_max_seed_pump.
            sess._SESSION["exp_max_seed_left"] = int(
                getattr(args, "exp_max_seed", 8) or 0)
            progression.exp_max_seed_pump(conn, outbound, mode, be, args)
            sess._SESSION["war_notified"] = False
            # KEY: FIELD NOT READY YET (2026-09-11). The client mounts
            # Data\building.pak inside its field-load phase machine (singleton
            # 0x5349800, phase [+0x39]; the mount is 0x04ff97a1). That finishes
            # DURING field load, before the first 28-byte 0x2023 clock sample.
            # A type-1 building pushed BEFORE the mount completes loads its
            # models from an unmounted pak -- the `_stand.mdl` comes back "not
            # found" and the building's update derefs the NULL model at
            # 0x05071531 (`[[obj+0x84]+0x2bd]`). Live 09-11 that was the crash
            # on the SECOND war field of a session, where the placement raced
            # ahead of the remount; the first field happened to win the race.
            # So keeps (like worn items) wait for field_ready -- see the first
            # clock-sample handler.
            sess._SESSION["field_ready"] = False
            # WARNING: AND THE GRACE CLOCK WITH IT (2026-09-12, live regression).
            # field_ready_pump counts --field-ready-ms from add_complete_at,
            # and on a SECOND field entry that stamp still held the PREVIOUS
            # field's 0x100E -- already many seconds old. So the batch fired
            # the moment this field's 0x2000 armed, 2.5 s BEFORE its own
            # 0x100E, and the 0x107A Equip landed DURING field load: the
            # player's character could not move in the second area. That is
            # the 2026-09-05 #14 freeze exactly ("equip DURING field entry ...
            # dressed the character over the default outfit and froze
            # movement"), reintroduced by a stamp that was set but never
            # cleared. The pump is a no-op while this is absent.
            sess._SESSION.pop("add_complete_at", None)
            # 15 s of protection after an area move [SE interface05]; the new
            # unit carries no INVINCIBLE bit of ours yet.
            sess._SESSION.pop("protect_flag_sent", None)
            death.spawn_protect_start(args, "an area move (field entry)")
            # A new field is a new war. (CHOSEN: war_started used to persist
            # across re-entries, which is why `!war` said "re-enter to do it
            # again" and then could not.)
            sess._SESSION["war_started"] = False
            # WARNING: `clock_synced` IS NOT HERE ANY MORE (2026-09-12): the 0x1148
            # base lives as long as the CONNECTION (only the RESUME sequence
            # zeroes it), so re-arming it per entry re-sent it per entry --
            # and the re-send was computed from a clock that already carried
            # the first base, so it came out ~0 and put the clock BACK ~56
            # years. See clock_sync_push.
            for k in clock.FIELD_ENTRY_CLOCK_CLEAR:
                sess._SESSION.pop(k, None)
            # 0x1027 MUST follow 0x1000: it releases mode 0xa substate 2, which
            # only starts waiting once the enter-area round trip has armed it.
            # Without this the client parks there forever -- assets loaded, field
            # never built, black screen. See set_position().
            if args.set_position:
                arrival.set_position(conn, outbound, mode, be, args)
            if args.move != "none":
                arrival.move_character(conn, outbound, mode, be, args, group)
            # The blacklist the player left with, restored before they can open
            # the window. 0x1163 is a whole-list PUT and the client is in the
            # field by now, so this is the natural place for it.
            if args.blacklist == "on" and character.blacklist_rows(args):
                character.blacklist_send(conn, outbound, mode, be, args)
            if args.char_record != "off":
                charsheet.char_record_push(conn, outbound, mode, be, args)
            if args.status != "off":
                charsheet.status_push(conn, outbound, mode, be, args)
            for _cmd in args.gmcmd or []:
                gm.gm_command(conn, outbound, mode, be, args, _cmd)
            # The war notify already went out with the group detail -- see the
            # note there on why it is not gated on this message.
        elif real_id == 0x2017:
            # KEY: 0x2017 MSG_MOVE_FIELD_REQUEST [u32 fieldId] -- THE CLIENT ASKING
            # TO LEAVE THE DUMMY AREA FOR THE REAL FIELD, and the first question
            # it has ever put to us that we could answer.
            #
            # Reaching this at all is what 0x1027 unlocked. Mode 0xa's branch
            # takes the DUMMY arm when [scene+0x4e8] == [scene+0x4ec] (0x4ff7eb0
            # via 0x04ff9d03; the [scene+0x2ac] escape at 0x04ff9d0e is dead --
            # +0x2ac's only writer sets it to 1 and nothing clears it). We send
            # area and group as the SAME number, so it always dummies.
            #
            # WARNING: THAT IS NOT A FAILURE. The dummy arm is a staging area: it logs
            # "entered the dummy area", resets the substate and sets mode 0xd
            # (0x4ff73e0 zeroes +0x38..+0x3b and writes +0x3c = 0xd), then sends
            # THIS request (0x4ff9d3f -> 0x5128900) and waits. The client is
            # driving its own way to the real field and we were leaving it
            # hanging.
            #
            # THE REPLY IS EMPTY. Arm 0x5054af3 (id 0x1012, byte table slot 8)
            # makes ZERO stream reads: it logs "> MSG_MOVE_FIELD_REQUEST_OK",
            # sets [scene+0x29a]=1, calls 0x4ff84e0(scene, 0x4000) and
            # 0x4ffef90(0), then needs [0x533998c] and [+0x980] non-null (a null
            # there is a silent drop, not a crash). 0x1013 is the NG and DOES
            # read a u32 error code (0x5054b62), which it feeds to the error
            # string table via 0x5061290 -- so OK and NG are not symmetric, and
            # an empty 0x1013 would desync.
            f = inner[2:]
            want = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            if args.move_field == "ok":
                wire.send(conn, outbound, wire.inner_msg(0x1012, b"", wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld]    0x2017 MOVE_FIELD_REQUEST field=%d -> 0x1012 "
                      "OK (empty body) -- the client asked to leave the dummy "
                      "area for the real field" % want, flush=True)
            elif args.move_field == "ng":
                wire.send(conn, outbound,
                     wire.inner_msg(0x1013, struct.pack(">I", args.move_field_err),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld]    0x2017 MOVE_FIELD_REQUEST field=%d -> 0x1013 "
                      "NG err=%d" % (want, args.move_field_err), flush=True)
            else:
                print("[feworld]    0x2017 MOVE_FIELD_REQUEST field=%d -- NOT "
                      "ANSWERED (--move-field none); the client will sit in the "
                      "dummy area" % want, flush=True)
        elif real_id == 0x20AC:
            # KEY: THE DOOR. Live 2026-09-05: a player walked into one of
            # the capital's minimap doors and the client sent [f32 x][f32 y]
            # [f32 z] = (186.5, 0.003, -132.5), the door's own position, with
            # nothing else on the wire -- no building object was needed. Its
            # builder (0x04ffbc40) registers exactly one reply, 0x1166, and
            # marks the request in flight at [screen+0x62]; until an answer
            # lands no door in the field fires again, which is why the
            # second door was as dead as the first.
            #
            # THE ANSWER is where the door leads -- see ROOM_AREA_BASE for
            # what 0x1166's arm does with it. Doors are matched by position
            # against --door entries (the client tells us where the door is,
            # so mapping them is a matter of touching each one and reading
            # this log line); an unmapped door opens --door-default.
            f = inner[2:]
            x, y, z = (struct.unpack_from(">fff", f, 0) if len(f) >= 12
                       else (0.0, 0.0, 0.0))
            field = sess._SESSION.get("field")
            # WARNING: THE LATCH FIRST. If the player is still standing on the
            # arrival a door just gave them, this touch is that same door
            # firing again, not a new one -- answering it is the loop.
            if field is not None and not town.door_latch_ok(args, field, x, z):
                print("[feworld]    0x20AC door at (%.1f, %.1f) in field %s "
                      "IGNORED -- the player has not left the arrival point "
                      "they were just put on. Walk away from the doorway and "
                      "it arms again." % (x, z, field), flush=True)
                wire.send(conn, outbound, wire.inner_msg(0x1167, struct.pack(">I", 4),
                                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                continue
            room = town.town_door_for(args, field, x, z) if field is not None else None
            how = "mapped (--door / town file)"
            sess._SESSION["last_door"] = (field, x, z)
            # A `!link` row wins over everything: it is the admin having
            # WALKED both sides, so it carries an arrival position and the
            # shipped table does not know about it at all.
            # WARNING: IS THE DESTINATION A ROOM OR AN AREA? Until 2026-09-10 that
            # was inferred from the NUMBER -- inside the client's room window
            # (9..30) meant room, outside meant group -- and room indices and
            # area ids OVERLAP. So a shipped door pointing at AREA 21 (a
            # capital!) was read as "room 21", hit the empty-room guard, and
            # the player got MSG_GOTO_SEARCH_NG and stood there. The same door
            # in the other direction pointed at area 91, which is outside the
            # window, and worked -- which is why it failed one way only.
            #
            # Provenance settles it: fet_area_portal and a `!link` row are
            # AREAS by construction. Only a `--door`/`!door` row is ambiguous,
            # and there the 9..30 convention is what the command documents.
            dat_pos, dest_is_area = None, False
            via = None
            face = None
            if field is not None:
                lk = town.town_link_for(args, field, x, z)
                if lk is not None:
                    room, dat_pos = lk
                    dest_is_area = True
                    how = "!link (walked, both ways)"
            # The shipped table answers with an ARRIVAL POSITION too -- SE's
            # own spot on the other side of the door, not the group's default
            # spawn. A hand-mapped --door row still wins: it is the admin
            # saying "this door goes here", and a capital's town file may map
            # a door the shipped table does not have.
            if room is None and field is not None:
                hit = doors.dat_door_for(args, field, x, z, y)
                if hit is not None:
                    room, dat_pos, via, face = hit
                    dest_is_area = True
                    how = "dat.pak fet_area_portal (--doors dat)"
            if room is None:
                room = getattr(args, "door_default", None)
                how = "UNMAPPED -> --door-default"
            print("[feworld]    *** 0x20AC DOOR touched at (%.1f, %.3f, %.1f) "
                  "in field %s -> %s: room %s (%s). To map it: `!door ROOM` "
                  "now, or --door %s:%.1f:%.1f:ROOM"
                  % (x, y, z, field, how, room,
                     zones.room_label(room) if room is not None else "-",
                     field, x, z), flush=True)
            if room is None or field is None:
                # 0x1167 [u32 err] -- code 4 is the client's own "system
                # error / database" text; it also clears the in-flight flag
                # so the door can be tried again.
                wire.send(conn, outbound, wire.inner_msg(0x1167, struct.pack(">I", 4),
                                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1167 MSG_GOTO_SEARCH_NG err=4 "
                      "(no room for this door; --door-default is unset)",
                      flush=True)
                continue
            # WARNING: NEVER SEND A PLAYER INTO A ROOM THEY CANNOT LEAVE. A room has
            # no door of its own -- inside one the client sends nothing at all
            # at its door -- so the ONLY way out is talking to somebody
            # (--room-exit) or a hand-typed !goto. --door-default is 10 in
            # prod, and room 10 has no keeper, so touching any unmapped door
            # would have dropped the player into an empty room with no exit and
            # held them there until the 120 s read window closed the session.
            # That is the tavern trap of 2026-09-06 with a stranger's door
            # instead of a stranger's dialogue.
            #
            # The refusal is the door's own NG, which clears the in-flight flag
            # so the next door still works, and the log says exactly what to do
            # about it. `!goto room:N` still goes anywhere: this guards the
            # path a player can walk into by accident, not the admin's lever.
            # KEY: A DOOR MAY LEAD TO ANOTHER GROUP, NOT JUST A ROOM. The
            # client's own capital table (0x052D1880, stride 0x24, its last
            # dword the GROUP ID) says every capital ships as TWO map files
            # under TWO group ids -- 91 = map00_00 and 39 = map00_01, 92/57 for
            # map01, 62/93 for map02, 94/78 for map03, 95 for map04_00 -- with
            # the SAME street layout and DIFFERENT shop icons baked into each
            # one's minimap. So a door in a capital plausibly leads to the
            # other half of that capital rather than into a shop interior, and
            # until now `!door N` could only ever say "a room".
            #
            # Same convention the event `goto:N` parser uses: inside the
            # client's room-switch window (9..30) it is a ROOM, anything else
            # is a group id. Hypothesis of 2026-09-06, and the table is
            # what makes it testable.
            if dest_is_area or int(room) not in zones.ROOM_INDEX_WINDOW:
                print("[feworld]    door -> GROUP %d (not a room: %d is outside "
                      "the client's room-switch window %d..%d). No keeper check "
                      "applies -- a group is a whole field, not a room with one "
                      "way out." % (room, room, zones.ROOM_INDEX_WINDOW[0],
                                    zones.ROOM_INDEX_WINDOW[-1]), flush=True)
                events.goto_push(conn, outbound, mode, be, args, int(room),
                          pos=dat_pos, via=via, face=face)
                continue
            # WARNING: Only when a town file is actually configured. Without one no
            # room can have a keeper, so an unconditional check would refuse
            # every door on a server that simply does not author rooms.
            keepers = town.town_get(args, "room:%d" % int(room), "npcs")
            if (town.town_path(args) and not keepers
                    and getattr(args, "room_exit", "conversation") != "off"):
                wire.send(conn, outbound, wire.inner_msg(0x1167, struct.pack(">I", 4),
                                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1167 MSG_GOTO_SEARCH_NG err=4 "
                      "-- REFUSED: room %d (%s) has nobody in it, and a room "
                      "has no door of its own, so this would trap the player "
                      "until the read window closed the session. Put a keeper "
                      "in it first (`!goto room:%d`, then `!npc MODEL "
                      "event=say:...;goto:%s`), or map this door somewhere "
                      "else with `!door N`."
                      % (room, zones.room_label(room), room,
                         sess._SESSION.get("field")), flush=True)
                continue
            events.goto_push(conn, outbound, mode, be, args, zones.ROOM_AREA_BASE + int(room))
        elif real_id == 0x2046:
            # MSG_NPC_EVENT_ACTION_REQUEST -- the player TALKED to an NPC.
            # Builder 0x05153580: [u32 npc unit id][u32], registers 0x1071 OK /
            # 0x1072 NG. See THE EVENT VM (event_step) for the conversation.
            f = inner[2:]
            npc = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            rows = town.town_get(args, town.town_key(), "npcs")
            idx = npc - (int(getattr(args, "monster_base", 400)) + 100)
            who = rows[idx] if 0 <= idx < len(rows) else {}
            steps = (events.event_script_for(args, who)
                     if getattr(args, "events", "on") == "on" else [])
            script = int(who.get("script") or 0) if steps else 0
            print("[feworld]    0x2046 NPC_EVENT_ACTION npc=%d -- %s -> %s"
                  % (npc, "%s (dat.pak script %s)" % (who.get("name") or "unnamed",
                                                     who.get("script", "?"))
                     if who else "not a town-file NPC",
                     "%d-step event, D=%d" % (len(steps), script) if steps
                     else "no event (D=0)"), flush=True)
            wire.send(conn, outbound,
                 wire.inner_msg(0x1071, struct.pack(">IHHI", npc, 0, 0, script),
                           wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            if not steps:
                # LIVE 2026-09-05: D=0 alone LEFT THE CLIENT IN TALK MODE (bit
                # 1 of the UI-mode word, set by the 0x2046 builder; the door
                # builder 0x04ffbb50 bails on it). The event END arm 0x1175
                # (0x05056039) clears it -- NOT 0x1172, whose arm LOCKS.
                wire.send(conn, outbound, wire.inner_msg(0x1175, b"", wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1175 event END (no script) -- "
                      "releases the talk-mode bit", flush=True)
            else:
                sess._SESSION["event"] = {"npc": npc, "script": script, "steps": steps,
                                     "pc": 0, "name": who.get("name", "")}
                print("[feworld] -> 0x30 inner 0x1071 D=%d -- the client's event "
                      "starter 0x5173f70 now sends 0x20A7/0x20A8; the first "
                      "command follows 0x20A7" % script, flush=True)
        elif real_id == 0x20A7:
            # The event starter's request (0x5173f70): [u32 npc][u32 D],
            # registering 0x1172/0x1173. The cue for the first command;
            # 0x1172 goes out when the script is exhausted.
            f = inner[2:]
            print("[feworld]    0x20A7 event start body=%s" % f.hex(), flush=True)
            # WARNING: LIVE 2026-09-05 #6: the shop opened, listed all four pages,
            # and the client sat on "waiting..." with every reply consumed
            # (its own log: four `erase >>> 0x204a`, nothing after). The
            # starter 0x5173f70 REGISTERS 0x1172/0x1173 as 0x20A7's reply in
            # the pending table (0x535cec8); an entry never erased is the
            # "waiting..." overlay -- the unanswered door showed the same.
            # 0x1172 is the START acknowledgement: its arm is 0x5055f70 (the
            # dispatcher table maps it there, NOT to 0x5056039 as first read)
            # and it ENTERS EVENT MODE: scene flag 0x4000, byte 0x53378f3 = 1,
            # the manager's modal bit, [mgr+0x94] slot 32 -- the player is
            # locked for the conversation, as SE intended. Zero stream reads.
            # The release is 0x1175 (arm 0x5056039), sent after the close OK.
            wire.send(conn, outbound, wire.inner_msg(0x1172, b"", wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            ev = sess._SESSION.get("event")
            print("[feworld] -> 0x30 inner 0x1172 event START OK (header-only; "
                  "erases 0x20A7's pending entry)%s"
                  % ("" if ev else " (no event in flight)"), flush=True)
            if ev and ev["pc"] == 0:
                events.event_step(conn, outbound, mode, be, args)
        elif real_id == 0x20A8:
            # The per-command ACK (0x5173fd0): [u32 a][u16 b][u32 c]. The
            # dialogue window sends (id, 6, 0) on click-through; the choice
            # menu is expected to put the pick in one of them -- LOGGED so the
            # first live menu names the slot. Each ack advances one command.
            f = inner[2:]
            a = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            b = struct.unpack_from(">H", f, 4)[0] if len(f) >= 6 else 0
            c = struct.unpack_from(">I", f, 6)[0] if len(f) >= 10 else 0
            ev = sess._SESSION.get("event")
            print("[feworld]    0x20A8 event ACK a=%d b=%d c=%d%s"
                  % (a, b, c, " -- step %d of %d acknowledged"
                     % (ev["pc"], len(ev["steps"])) if ev else " (no event)"),
                  flush=True)
            if ev:
                if ev["pc"] == 0 and ev.get("started") is None:
                    ev["started"] = True          # the starter's own ack
                elif ev.get("in_window"):
                    # The window's (npc, 8, 0) ack is sent by its CLOSE routine
                    # (the shop's 0x50f00c0: release the list, 0x2085, this
                    # ack, hide, [mgr+0xe0]=0) -- it means CLOSED, and the
                    # interpreter now wants the next command: the END WINDOW
                    # (scripts carry it; see event_script_for). WARNING: LIVE #1
                    # and #4: a 0x1172 here stuck the client both times.
                    ev["in_window"] = False
                    sess._SESSION.pop("shop", None)
                    sess._SESSION.pop("bank_open", None)
                    print("[feworld]    (the window closed; next command)",
                          flush=True)
                    events.event_step(conn, outbound, mode, be, args)
                else:
                    ev.setdefault("acks", []).append((a, b, c))
                    if ev.get("await_menu") is not None:
                        # a choice menu is up: this ack is its pick
                        events.event_menu_pick(ev, a, b, c)
                    events.event_step(conn, outbound, mode, be, args)
        elif real_id == 0x2085:
            # An event window's CLOSED notify: [u32 mode], registers nothing.
            # Sent by the shop's close 0x50f00c0 (after ReleaseItemListData,
            # before its (npc, 8, 0) ack), the bank's 0x50ecf80 and the
            # box's slot-30 routine 0x50c74c0. Two earlier readings were
            # wrong: it is NOT the dialogue box closing under a new window,
            # and the box does NOT sit at [mgr+0xe8] swallowing the shop's
            # replies (the 0x1102 arm 0x0502409e tries +0xe8, else +0xe0;
            # +0xe8 is the bank-style window that clears it at 0x50ecfd3).
            # So nothing is held on its account any more; the ack that
            # follows it advances the event.
            f = inner[2:]
            ev = sess._SESSION.get("event")
            sess._SESSION.pop("bank_open", None)     # its replies deref the window
            print("[feworld]    0x2085 event window closed body=%s%s"
                  % (f.hex(), " (its 0x20A8 ack cues the next command)"
                     if ev else " (no event)"), flush=True)
        elif real_id == 0x2099:
            # never seen INSIDE the envelope live -- the client sends it as a
            # bare outer frame (see conversation_close); kept for symmetry
            events.conversation_close(conn, outbound, mode, be, args, inner[2:], "inner")
        elif real_id == 0xE00F:
            # The client's NACK for a 0xE00D MOVE order (arm 0x50573a8): sent
            # when a second order arrives while [scene+0x84] is still armed.
            #     [u32 token][u32 0]
            f = inner[2:]
            tok = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            print("[feworld]    0xE00F MOVE NACK token=%d -- the client "
                  "already holds an unconsumed 0xE00D; do not resend until "
                  "mode 0xb clears it" % tok, flush=True)
        elif real_id == 0x204B and sess._SESSION.get("shop") and args.ui_auto != "off":
            # MSG_BUY_SHOP_ITEM [u32 shop][u32 item number][u16 count]
            # (0x50ee970; live #8: 000001f4 00000213 0001 = wand x1). The
            # reply 0x107D is header-only (arm 0x05023e19 reads nothing) and
            # the client then asks 0x204D -> 0x1082.
            #
            # RETRACTED 2026-09-05: "NOTHING delivers the item live / no
            # incremental item-add message exists". 0x107A DOES -- one item,
            # add or remove, into a named bag slot (worker 0x0503d540, the same
            # per-item reader 0x0503d280 the 0x1000 bag record uses). That
            # claim came from noticing 0x1000's bag reader has a single caller
            # and concluding no other path existed, without decoding the second
            # field dispatcher's jump table. The purchase is still STORED here
            # -- delivering it live also needs a free bag slot chosen the way
            # the client would, which the store does not model yet -- but the
            # channel is no longer missing. See item_add_body.
            #
            # VERIFIED: DELIVERED LIVE SINCE 2026-09-06. The store half was right and
            # the missing half was the same one Sort and Stack were missing: the
            # OK is an acknowledgement (arm 0x05023E19 reads nothing), so the
            # purchase has to be PUSHED. It goes on the end of the bag, which
            # shifts nobody, so the burst is one add per unit bought.
            f = inner[2:]
            shop_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            item = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
            count = struct.unpack_from(">H", f, 8)[0] if len(f) >= 10 else 0
            #
            # KEY: AND SINCE 2026-09-11 IT IS PAID FOR. The row's own price (the
            # item table's, see shop_stock) times the count comes out of the
            # stored wallet -- gold at a gold shop, Rings at the Ring Shop --
            # and a player who cannot afford it gets the client's own refusal
            # (BUY_NG_NO_GOLD / BUY_NG_NO_RINGS) and keeps both the money and
            # the bag they had. Checked BEFORE the bag is written, charged
            # after, so a refusal changes nothing.
            shop = sess._SESSION["shop"]
            price = {r[0]: (r[1], r[2]) for r in shop["stock"]}
            ok = item in price and 1 <= count <= 16
            code, why = shops.BUY_NG_DEFAULT, "not in this shop's stock"
            old = itemrecords.item_rows(args)
            items = old
            cost_g, cost_r = 0, 0
            if ok:
                cost_g, cost_r = price[item][0] * count, price[item][1] * count
                # item_rows, not a bare unpack: a stacked row is FOUR long
                # (see the Stack button) and `for u, _, _ in items` raised on it.
                short = wallet.wallet_short(args, cost_g, cost_r)
                if len(items) + count > inventory.bag_capacity(args):
                    # the character's own grid (uPossessItemNum), <= 96 deep
                    ok, code, why = False, shops.BUY_NG_BAG_FULL, "bag full"
                elif short:
                    ok = False
                    code = shops.BUY_NG_NO_GOLD if short == "gold" else shops.BUY_NG_NO_RINGS
                    why = ("costs %d gold, has %s" % (cost_g, wallet.gold_get(args))
                           if short == "gold" else
                           "costs %d rings, has %s" % (cost_r, wallet.rings_get(args)))
                else:
                    nxt = inventory.new_item_uid(args, items)     # bag AND bank uids
                    items = old + [(nxt + i, item, 1, 1) for i in range(count)]
                    ok = character._store_char_field(args, "items", [list(x) for x in items])
                    why = "no stored character to write to"
            if ok:
                wire.send(conn, outbound, wire.inner_msg(0x107D, b"", wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                wallet.wallet_charge(conn, outbound, mode, be, args, cost_g, cost_r,
                              "bought item %d x%d" % (item, count))
                print("[feworld] -> 0x30 inner 0x107D MSG_BUY_SHOP_ITEM_OK -- item %d x%d "
                      "stored on the character (bag now %d) for %d gold + %d rings"
                      % (item, count, len(items), cost_g, cost_r), flush=True)
                if getattr(args, "bag_organize", "on") != "off":
                    inventory.bag_layout_push(conn, outbound, mode, be, args, old, items,
                                    [], "bought item %d x%d" % (item, count))
            else:
                wire.send(conn, outbound, wire.inner_msg(0x107E, struct.pack(">I", code), wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x107E MSG_BUY_SHOP_ITEM_NG err=%d -- item %d "
                      "x%d refused: %s" % (code, item, count, why), flush=True)
        elif real_id in (0x2021, 0x2022, 0x2098) and args.ui_auto != "off":
            # EQUIP / UNEQUIP. All three carry [u32 uid][u8 worn slot] and all
            # three are answered by their header-only OK -- but the OK is not
            # the reflection. 0x102A's arm (0x0503c390) looks the player up and
            # clears the in-flight flag 0x200000; it reads no body and touches
            # no item, so answering it and stopping is exactly what "OK and
            # NOTHING CHANGES" looked like. See equip_reflect_push.
            #
            #   0x2021 MSG_EQUIP_ITEM        (0x0507c0f0)  put it on
            #   0x2022 MSG_DISRAM_ITEM       (0x0507c1f2)  take it off
            #   0x2098 MSG_DISRAM_EQUIP_ITEM (0x0507c2dd)  swap out of a full slot
            #
            # The client has ALREADY validated (0x05076090 at 0x050b6638) and
            # already picked the worn slot.
            #
            # WARNING: THE THREE ARE NOT THE SAME SHAPE, and the comment above used
            # to say they were. Measured live 2026-09-06 off the wire:
            #
            #     0x2021  20 21 | 00 00 03 ea | 03      [u32 uid][u8 worn]
            #     0x2022  20 22 | 00 00 03 ef          [u32 uid]  -- NO SLOT
            #
            # An UNEQUIP names only the item. So the worn index has to come
            # from the ITEM TABLE, exactly the way the client's own Equip
            # derives it (0x0507b920 on [tbl+0x50]) -- and until this was
            # noticed, `cslot` fell to the 0xFF default and unequip_push
            # refused every single unequip as "outside the 13-slot array".
            # On screen that read as a hat that would not come off,
            # even clicked four times in a row.
            ok, ng, name, arm_va = messages.UI_HEADER_ONLY_OK[real_id]
            f = inner[2:]
            uid = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            cslot = f[4] if len(f) >= 5 else 0xFF
            # WARNING: 0x2098 IS AN EQUIP, and calling it an unequip is why the
            # quick-access pockets stayed empty. Live 2026-09-06: a
            # player set two items "to pocket 1 and pocket 2" and the
            # client sent 0x2098 carrying worn slots 11 and 12 -- exactly
            # where item slot type 11 lands in Equip's own jump table
            # (0x0507b920, "first free of 11..12"). It uses this verb
            # rather than 0x2021 because the target slot is already
            # occupied: MSG_DISRAM_EQUIP_ITEM is unequip-THEN-equip, a
            # SWAP, whose net effect is that `uid` ends up worn. Treating
            # it as a removal took the item back out of the stored equip
            # list and then reflected it with the 0xff marker -- the
            # client was told, correctly and uselessly, that the thing it
            # had just put in a pocket was not worn.
            wear = real_id in (0x2021, 0x2098)
            items = [tuple(x) for x in (character._load_char_field(args, "items", []) or [])]
            equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
            by_uid = {int(u): int(no) for u, no, _f, _c in itemrecords.item_rows(items)}
            stored = True
            if uid in by_uid:
                # The store keeps (item table slot TYPE, uid) -- felobby seeds
                # it that way and the 0xD002 record carries it -- so the pair
                # is rebuilt from the item table, not from the client's worn
                # index. worn_layout() is the one place that converts.
                slot_type = fegamedata.items().get(by_uid[uid], {}).get("slot", -1)
                equip = [(int(sl), int(u)) for sl, u in equip if int(u) != uid]
                if wear and slot_type is not None and slot_type >= 0:
                    # one item per worn slot: whatever else Equip would put in
                    # this slot comes off, the same way the client's own
                    # 0x0507b950 clears the previous occupant
                    taken = equipment.worn_layout(equip, items)
                    want = equipment.worn_index(slot_type, set(taken.values()))
                    equip = [(sl, u) for sl, u in equip
                             if taken.get(int(u)) != want]
                    equip.append((int(slot_type), uid))
                stored = character._store_char_field(args, "equip",
                                           [list(x) for x in equip])
            else:
                stored = False
            mid = ok if (uid in by_uid and stored) else ng
            body = b"" if mid == ok else struct.pack(">I", args.ui_auto_err)
            wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            # The CLIENT's slot and the one worn_layout derives are printed
            # together on purpose: they are computed from different things
            # (its jump table vs our copy of the item table) and the only
            # cheap way to notice them drifting is to see both.
            derived = equipment.worn_layout(equip, items).get(uid)
            print("[feworld] -> 0x30 inner 0x%04X %s_%s -- uid=%d (%s); "
                  "client worn slot=%s, we derive %s; stored equipment is "
                  "now %s"
                  % (mid, name, "OK" if mid == ok else "NG", uid,
                     "EQUIP" if wear else "UNEQUIP",
                     "none" if cslot == 0xFF else cslot,
                     "none" if derived is None else derived,
                     [(sl, u) for sl, u in equip] if uid in by_uid
                     else "unchanged -- that uid is not in this character's bag"),
                  flush=True)
            if (mid == ok and wear and cslot != 0xFF
                    and derived is not None and derived != cslot):
                print("[feworld]    \u26a0 WORN INDEX DISAGREEMENT: the client "
                      "put uid %d in worn slot %d and we will reflect %d. "
                      "Both come from item slot type %s; ours is "
                      "order-dependent for the multi-slot types (8, 11)."
                      % (uid, cslot, derived,
                         fegamedata.items().get(by_uid.get(uid, -1), {})
                         .get("slot")), flush=True)
            if mid == ok:
                # THE UNEQUIP GOES FIRST, while [unit+0x870] still names the
                # item: 0x2004's uid==0 branch reads it back out of that array.
                # The client's own worn slot (the u8 it just sent) is the one
                # to clear -- it picked the index, not us.
                # WARNING: ONLY 0x2022 IS AN UNEQUIP. Measured live 2026-09-06:
                # a player put cheese, a potion and a feather "into slot 1
                # and 2 for quick use" and the client sent 0x2098 with worn
                # slots 11 and 12 -- which is exactly where worn_index_for
                # sends item slot type 11. So 0x2098 ASSIGNS an item to a worn
                # slot that is already occupied; the name
                # MSG_DISRAM_EQUIP_ITEM reads like a removal and is not one
                # (a message NAME is not evidence -- again). Sending the
                # 0x2004 clear for it would have emptied the quick slot the
                # player had just filled.
                # WARNING: The STORE still treats 0x2098 as a removal, which is why
                # none of those three items appears in `equip`. Left alone on
                # purpose: quick slots are unmodelled either way, and changing
                # store semantics on an untested reading is how this file
                # collected its regressions.
                if real_id == 0x2022:
                    # 0x2022 carries no slot, so derive it. worn_index_for
                    # is the client's own jump table; for the two-handed
                    # types it answers 0, and 0x0507b950's own branch
                    # clears the paired [unit+0x874] with it.
                    wslot = cslot if 0 <= cslot <= 12 else None
                    if wslot is None:
                        wslot = fegamedata.worn_index_for(
                            fegamedata.items().get(by_uid.get(uid, -1), {})
                            .get("slot", -1))
                    equipment.unequip_push(conn, outbound, mode, be, args, wslot,
                                 "0x%04X for uid %d (item %s, slot type %s)"
                                 % (real_id, uid, by_uid.get(uid),
                                    fegamedata.items()
                                    .get(by_uid.get(uid, -1), {}).get("slot")))
                equipment.equip_reflect_push(conn, outbound, mode, be, args,
                                   equipment.stored_equip_rows(args, only_uid=uid),
                                   "0x%04X for uid %d" % (real_id, uid))
                # WARNING: LIVE 2026-09-12: ONLY the field-entry replay called
                # motion_refresh_push, so a weapon equipped mid-session never
                # got its motion set and every skill on it was a T-pose --
                # Quinn's bow, 0x2021 at seq 86, long after the entry restate,
                # while Lex (wand worn at entry) animated. The 0x107A path
                # never selects the motion set (its docstring says so).
                # Only for the HELD weapon (worn 0/1): the motion set depends on
                # nothing else, and a 0x2098 into a pocket (worn 11/12) must
                # not be followed by any 0x2004 (fe_ui_test part14, live 09-06).
                if (wear and real_id == 0x2021
                        and getattr(args, "equip_reflect", "request") != "off"
                        and equipment.worn_layout(equip, items).get(uid) in (0, 1)):
                    monsters.motion_refresh_push(conn, outbound, mode, be, args,
                                        equipment.stored_equip_rows(args),
                                        "after 0x%04X for uid %d (a weapon "
                                        "equipped in the field)" % (real_id, uid))
                equipment.cast_report_log(args, "after 0x%04X" % real_id)
        elif (real_id == 0x2051 and args.ui_auto == "ok"
              and getattr(args, "use_item", "on") != "off"):
            # KEY: USING AN ITEM -- unanswered until now, so the cheese did
            # nothing and the request hung. Reported live 2026-09-06 ("I just
            # tried eating cheese").
            #
            # The request, captured live (uid 1011 = item 7, the thing that had
            # just been bought), 25 body bytes:
            #
            #     [u32 uid][u32 ?1] [8 bytes] [u8 hasPos] [u32 ?][u32 ?]
            #     00 00 03 f3|00 00 00 01|00 01 0f 35 00 01 14 e0|00|...
            #
            # WARNING: ONLY THE FIRST FIELD IS DECODED, and it is decoded by
            # elimination plus the builder: 0x05042DF4 writes [win+0x84] first
            # and 1011 was a uid in that bag and not an item number in it. The
            # eight bytes in the middle come from a sub-writer (0x050389E0) and
            # look like two client timestamps; the u8 is the builder's own
            # "position follows" flag (0x05042E4D `cmp bl,1` gates three floats,
            # and it was 0 here). The rest is LOGGED, not guessed at -- naming a
            # field off its shape is what cost this channel six probes.
            #
            # KEY: AND THE OK IS AN ACKNOWLEDGEMENT AGAIN. 0x1089's arm
            # (0x050580EC) reads [u32 A][u32 B], finds object B, and calls
            # 0x05074E20 -- which is `and byte [obj+0x4ec], 0xfe; ret 4`. It
            # clears one in-flight bit and DISCARDS A. Nothing consumes the
            # item, nothing applies an effect. A miss on B is a log line
            # (0x0505814C), not a fault.
            f = inner[2:]
            uid = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            arg1 = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
            old = itemrecords.item_rows(args)
            equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
            hit = [(i, r) for i, r in enumerate(old) if r[0] == uid]
            why = ""
            if not hit:
                why = "that uid is not in this character's bag"
            elif uid in {int(u) for sl, u in equip
                         if int(sl) != equipment.POCKET_SLOT_TYPE}:
                # WARNING: LIVE 2026-09-12: a player used both POCKET items, the
                # client emptied the pockets, and a relog brought them back --
                # because a pocket IS a worn slot (type 11 -> worn 11/12) and
                # this refused every worn uid: "REFUSED: that item is EQUIPPED"
                # for uids 1009/1011, 6 times in the log. A pocket item is
                # exactly the one meant to be used.
                why = "that item is EQUIPPED"
            fx = None
            if not why and getattr(args, "item_effects", "table") == "table":
                fx = fegamedata.item_effect(hit[0][1][1])
                cur = ((sess._SESSION.get("item_fx") or {}).get(fx["stat"])
                       if fx and fx["total_ms"] else None)
                if cur and cur["tier"] > fx["tier"]:
                    # refused BEFORE it is eaten, so the weaker potion is kept
                    why = ("a stronger %s potion (tier %d) is still working -- "
                           "fewiki 2006 (fv_consum.txt): a potion only replaces "
                           "one of equal or lower rank"
                           % (fx["stat"].upper(), cur["tier"]))
            new_rows, gone = list(old), []
            if not why:
                i, row = hit[0]
                # ONE. `arg1` is 1 in the only capture we have and is NOT
                # confirmed to be a count, so it is logged rather than obeyed:
                # obeying an unmeasured field is how a stack of five gets eaten
                # in one bite.
                if row[3] > 1:
                    new_rows[i] = (row[0], row[1], row[2], row[3] - 1)
                else:
                    new_rows = old[:i] + old[i + 1:]
                    gone = [(uid, i)]
                if not character._store_char_field(args, "items",
                                         [list(r) for r in new_rows]):
                    why = ("no stored character matched account=%r charid=%s"
                           % (sess._SESSION.get("account"), sess._SESSION.get("charid")))
                elif gone and any(int(u) == uid for _sl, u in equip):
                    # the pocket item is GONE from the bag: forget its worn
                    # entry too, or the next login re-fills the pocket (a
                    # stack that still has some keeps its entry)
                    character._store_char_field(args, "equip",
                                      [list(e) for e in equip if int(e[1]) != uid])
                    print("[feworld]    pocket uid %d emptied from the stored "
                          "worn map (pockets are worn slots)" % uid, flush=True)
            if why:
                wire.send(conn, outbound,
                     wire.inner_msg(0x108A, struct.pack(">II", 0, uid),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x108A MSG_USE_ITEM_NG uid=%d "
                      "REFUSED: %s (body %s)" % (uid, why, f.hex()), flush=True)
            else:
                # THE OK GOES FIRST, while the object still exists: its arm
                # looks uid up and clears the in-flight bit on it. The removal
                # below destroys that object, so the other order would clear the
                # flag on nothing.
                wire.send(conn, outbound,
                     wire.inner_msg(0x1089, struct.pack(">II", 0, uid),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                item_no = hit[0][1][1]
                damage.item_use_effect(conn, outbound, mode, be, args, item_no, fx)
                print("[feworld] -> 0x30 inner 0x1089 MSG_USE_ITEM_OK [u32 0]"
                      "[u32 %d] -- item %d consumed, bag %d -> %d row(s). The "
                      "arm only clears [obj+0x4ec] bit 0; the 0x107A below is "
                      "what takes it out of the bag; the effect is "
                      "item_use_effect's (above). Body tail "
                      "(undecoded): %s"
                      % (uid, item_no, len(old), len(new_rows), f[8:].hex()),
                      flush=True)
                inventory.bag_layout_push(conn, outbound, mode, be, args, old, new_rows,
                                gone, "used uid %d (item %d)" % (uid, item_no))
        elif (real_id == 0x204C and args.ui_auto == "ok"
              and getattr(args, "bag_organize", "on") != "off"):
            # KEY: SELLING, and it is the last of the acknowledge-and-do-nothing
            # family. 0x1080's arm (0x05023FEE) reads nothing, so answering it
            # left the item in the bag -- reported live 2026-09-06, "I bought
            # and sold an item but my inventory didn't change".
            #
            #     0x204C  [u32 shop][u32 uid][u16 count]    builder 0x050EF530
            #
            # WARNING: THE MIDDLE FIELD IS A uid, NOT AN ITEM NUMBER, and that is the
            # client's own word: 0x050EF7F5 fills it with
            # `0x0507BA90(unit, [row+0x88], 0)` -- the same "uid at bag slot N
            # of array 0" call the 0x107A remove branch uses. (The live capture
            # agrees: `000001f6 000003ee 0001` = shop 502, uid 1006, count 1,
            # and 1006 was a uid in that bag and not an item number in it.)
            # Reading it as an item number would sell the wrong row whenever a
            # bag held two of anything.
            f = inner[2:]
            shop_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            uid = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
            count = struct.unpack_from(">H", f, 8)[0] if len(f) >= 10 else 0
            old = itemrecords.item_rows(args)
            equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
            worn = {int(u) for _sl, u in equip}
            hit = [(i, r) for i, r in enumerate(old) if r[0] == uid]
            why, code = "", args.ui_auto_err
            if not hit:
                why, code = "that uid is not in this character's bag", shops.SELL_NG_NOT_HELD
            elif uid in worn:
                # The client builds its sell list off the bag array, which still
                # holds worn items, so this is reachable. Refusing is the honest
                # answer: selling what you are wearing needs an unequip we would
                # have to invent, and the equip pair would be left dangling.
                why = "that item is EQUIPPED -- take it off first"
                code = shops.SELL_NG_EQUIPPED
            elif count < 1:
                why, code = "count %d" % count, shops.SELL_NG_COUNT
            new_rows, gone = list(old), []
            earned = 0
            if not why:
                i, row = hit[0]
                sold = min(count, row[3])
                earned = shops.item_sell_value(args, row[1]) * sold
                if sold >= row[3]:
                    new_rows = old[:i] + old[i + 1:]
                    gone = [(uid, i)]
                else:
                    # a partial stack: the row stays where it is with fewer in
                    # it, so there is nothing to remove and nothing shifts
                    new_rows = list(old)
                    new_rows[i] = (row[0], row[1], row[2], row[3] - sold)
                if not character._store_char_field(args, "items",
                                         [list(r) for r in new_rows]):
                    why = ("no stored character matched account=%r charid=%s"
                           % (sess._SESSION.get("account"), sess._SESSION.get("charid")))
            if why:
                wire.send(conn, outbound,
                     wire.inner_msg(0x1081, struct.pack(">I", code),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1081 MSG_SELL_ITEM_NG err=%d -- "
                      "uid=%d x%d at shop %d REFUSED: %s"
                      % (code, uid, count, shop_id, why), flush=True)
            else:
                wire.send(conn, outbound, wire.inner_msg(0x1080, b"", wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                # 09-11: the sale PAYS -- the same value the window was shown
                # through 0x1086 (item_sell_value), into the stored wallet.
                got = wallet.wallet_credit(conn, outbound, mode, be, args, earned, 0,
                                    "sold uid %d x%d" % (uid, count))
                print("[feworld] -> 0x30 inner 0x1080 MSG_SELL_ITEM_OK "
                      "(header-only, arm 05023fee reads nothing) -- uid=%d x%d "
                      "at shop %d; bag %d -> %d row(s), %d gold credited. The "
                      "OK moves nothing; the 0x107A below is what empties the "
                      "slot." % (uid, count, shop_id, len(old), len(new_rows),
                                 got), flush=True)
                inventory.bag_layout_push(conn, outbound, mode, be, args, old, new_rows,
                                gone, "sold uid %d" % uid)
        elif (real_id in (0x114D, 0x20AA) and args.ui_auto == "ok"
              and getattr(args, "bag_organize", "on") != "off"):
            # `--ui-auto ng` and `off` deliberately fall through to the generic
            # UI_HEADER_ONLY_OK arm below, which is where the refusal and the
            # silence A/Bs live. This branch is the SUCCESS path only.
            # KEY: THE STACK AND SORT BUTTONS. Answered since 2026-08-25, inert
            # ever since, and inert for the same reason the equip was: the OK
            # is an ACKNOWLEDGEMENT. 0x2095's arm (0x05058747) and 0x1179's
            # (0x0505846D) make zero stream reads and move no item. In live
            # testing Sort was pressed four times in a row, which is what a
            # button that acknowledges and does nothing looks like.
            #
            #   0x114D  STACK  [u32 1]  builder 0x050b9330 / 0x050ea220; the
            #                           u32 is a literal 1 in both builders,
            #                           not a page or an array selector
            #   0x20AA  SORT   no body  builder 0x050b9390 / 0x050ea280
            #
            # Both also set the inventory window's in-flight state, which only
            # the OK clears -- so the OK still has to go, and it goes FIRST:
            # the 0x107A burst is the result of an operation the client has
            # already been told succeeded.
            ok, ng, name, arm_va = messages.UI_HEADER_ONLY_OK[real_id]
            how = "stack" if real_id == 0x114D else "sort"
            f = inner[2:]
            wire.send(conn, outbound, wire.inner_msg(ok, b"", wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x%04X %s_OK (header-only, arm %08x "
                  "reads nothing) for 0x%04X %s%s -- the OK only clears the "
                  "window's in-flight state; the bag itself moves on the "
                  "0x107A burst below"
                  % (ok, name, arm_va, real_id, how.upper(),
                     " body=%s" % f.hex() if f else ""), flush=True)
            inventory.bag_organize(conn, outbound, mode, be, args, how)
        elif (real_id in (0x2029, 0x202A) and args.ui_auto != "off"
              and getattr(args, "skill_palette", "request") != "off"):
            # KEY: THE SKILL PALETTE -- and the message that answers it is the
            # SAME ID coming back. See the PALETTE_SLOTS block for the whole
            # derivation; the short version is that the double-click sender
            # (vtable +0x24) and the local setter (+0x28) are different methods
            # and only the inbound worker calls the setter, so a click the
            # server never echoes changes nothing on screen.
            f = inner[2:]
            mask = struct.unpack_from(">H", f, 0)[0] if len(f) >= 2 else 0
            rows, off, bad = [], 2, []
            for slot in range(skilllist.PALETTE_MASK_BITS):
                if not mask & (1 << slot):
                    continue
                if real_id == 0x202A:
                    rows.append((slot, None))
                    continue
                if len(f) < off + 4:
                    bad.append(slot)
                    break
                skill = struct.unpack_from(">H", f, off)[0]
                off += 4                        # [u16 skill][u8][u8]
                rows.append((slot, skill))
            keep = {s: k for s, k in skilllist.palette_rows(args)}
            for slot, skill in rows:
                if slot >= skilllist.PALETTE_SLOTS:
                    continue                    # 0x0516236F bails above 7
                if skill is None:
                    keep.pop(slot, None)
                else:
                    keep[slot] = skill
            stored = character._store_char_field(args, "palette",
                                       [[s, k] for s, k in sorted(keep.items())])
            # ECHO ONLY WHAT THE CLIENT ASKED FOR. Re-sending the whole palette
            # here would also re-apply slots it already has, which is harmless
            # but makes the log unreadable; the full push is the entry replay.
            if real_id == 0x202A:
                body = skilllist.palette_clear_body([s for s, _k in rows])
            else:
                body = skilllist.palette_set_body([(s, k) for s, k in rows
                                         if k is not None])
            wire.send(conn, outbound, wire.inner_msg(real_id, body, wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x%04X SKILL PALETTE %s: %s -- "
                  "echoed back, which is what applies it (%s); palette is now "
                  "%s%s"
                  % (real_id, "CLEAR" if real_id == 0x202A else "SET",
                     ", ".join("slot %d%s" % (s, "" if k is None
                                              else " = skill %d" % k)
                               for s, k in rows) or "nothing (mask 0)",
                     "worker 0x0503D0F0" if real_id == 0x202A
                     else "worker 0x0503CF60",
                     ", ".join("%d:%d" % r for r in sorted(keep.items()))
                     or "empty",
                     "" if stored else " WARNING: NOT PERSISTED -- no stored character "
                     "matched, so it is gone at the next login"), flush=True)
            if bad:
                print("[feworld]    WARNING: 0x%04X mask claimed slot(s) %s the body "
                      "had no record for -- %d body bytes for %d set bit(s)"
                      % (real_id, bad, len(f), bin(mask).count("1")), flush=True)
        elif real_id == 0x204A and sess._SESSION.get("shop") and args.ui_auto != "off":
            # MSG_GET_SHOP_ITEM_LIST [u32 shop][u32 page] from the list data
            # object's 0x50eec20 ([list+0x90], [list+0x88] = the page it is
            # on). Its 0x107B handler (0x50ee7a0) reads the page straight
            # from the stream, then asks the NEXT page until [list+0x84]
            # (the 0x1102 count) is reached, then renders (0x50f6350) and
            # clears the waiting flag. See shop_page_body for the layout.
            f = inner[2:]
            shop_id = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            page = struct.unpack_from(">I", f, 4)[0] if len(f) >= 8 else 0
            shop = sess._SESSION["shop"]
            body = shops.shop_page_body(args, shop["stock"], page)
            # THE SELL PRICE, while the window that consumes it is open. See
            # sell_value_push: [item+0x4f0] is -1 from the ctor and 0x1086 is
            # the only thing that ever writes it, the client never asks, and
            # the arm drops the message when [mgr+0xe0] is null.
            if page == 0:
                shops.sell_value_push(conn, outbound, mode, be, args,
                                shops.sell_value_rows(args))
            n = struct.unpack_from(">H", body, 4)[0]
            wire.send(conn, outbound, wire.inner_msg(0x107B, body, wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x107B MSG_GET_SHOP_ITEM_LIST_OK "
                  "[page %d][u16 %d] x {[item][gold][rings][mask1=2][u16 item]"
                  "[3 x mask 0]} for the "
                  "%s shop (shop id %d in the request, %d/%d pages); items %s"
                  % (page, n, shop["kind"], shop_id, page + 1,
                     shops.shop_pages(args, shop["stock"]),
                     shop["stock"][page * 30:page * 30 + 6]),
                  flush=True)
        elif (real_id in bank.BANK_REQUESTS and args.ui_auto == "ok"
              and getattr(args, "bank", "on") == "on"):
            # THE BANK, simulated since 2026-09-11 -- see bank_request and the
            # block above BANK_REQUESTS. `--ui-auto ng`/`off` and `--bank off`
            # fall through to the generic header-only arm below, which is the
            # old acknowledge-and-store-nothing behaviour.
            bank.bank_request(conn, outbound, mode, be, args, real_id, inner[2:])
        elif real_id in messages.UI_REPLY_BODY and args.ui_auto != "off":
            # The body-carrying siblings of UI_HEADER_ONLY_OK. Same rule: the
            # OK's shape is measured, not assumed -- see the table.
            ok, ng, name, arm_va, spec = messages.UI_REPLY_BODY[real_id]
            f = inner[2:]
            if args.ui_auto == "ng":
                wire.send(conn, outbound,
                     wire.inner_msg(ng, struct.pack(">I", args.ui_auto_err),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x%04X %s_NG err=%d"
                      % (ng, name, args.ui_auto_err), flush=True)
                continue
            if spec == "echo32":
                val = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
                why = "echoed from the request"
            else:
                val = spec[1]
                why = "constant"
            if real_id == 0x2073 and sess._SESSION.get("shop"):
                val = shops.shop_pages(args, sess._SESSION["shop"]["stock"])
                why = "the open shop's stock / --shop-page"
            wire.send(conn, outbound,
                 wire.inner_msg(ok, struct.pack(">I", val & 0xFFFFFFFF),
                           wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x%04X %s_OK [u32 %d] (%s) -- for "
                  "0x%04X; arm %08x reads exactly one u32"
                  % (ok, name, val, why, real_id, arm_va), flush=True)
        elif (real_id in messages.UI_HEADER_ONLY_OK
              or (real_id == messages.UI_MOVE_REQUEST[0]
                  and args.move_request != "off")):
            # KEY: THE GENERIC HEADER-ONLY ANSWER -- see UI_HEADER_ONLY_OK.
            #
            # This exists because the same failure arrived twice in one hour
            # from two different buttons (STACK -> 0x114D, SORT -> 0x20AA), and
            # both times the fix was "send the OK the request already told us it
            # wanted". Answering them one at a time is a queue with no end; the
            # client publishes the whole request -> reply map in its own image,
            # so the table does.
            if real_id in messages.UI_HEADER_ONLY_OK:
                ok, ng, name, arm_va = messages.UI_HEADER_ONLY_OK[real_id]
                want = args.ui_auto
            else:
                _, ok, ng, name, arm_va = messages.UI_MOVE_REQUEST
                want = args.move_request
            f = inner[2:]
            if want == "off":
                print("[feworld]    0x%04X %s_REQUEST (%d body bytes) -- NOT "
                      "ANSWERED; the dialog that sent it WILL HANG"
                      % (real_id, name, len(f)), flush=True)
                continue
            mid = ok if want == "ok" else ng
            body = b"" if mid == ok else struct.pack(">I", args.ui_auto_err)
            wire.send(conn, outbound, wire.inner_msg(mid, body, wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x%04X %s_%s%s -- for 0x%04X (%d "
                  "body bytes in); OK arm %08x makes ZERO stream reads, so the "
                  "reply is the header and nothing else"
                  % (mid, name, "OK" if mid == ok else "NG",
                     "" if mid == ok else " err=%d" % args.ui_auto_err,
                     real_id, len(f), arm_va), flush=True)
            if f:
                print("[feworld]    (request body: %s)" % f.hex(), flush=True)
            # THE VALIDATE SEQUENCE IS A TIMER, NOT A ROUND TRIP. 0x1154's arm
            # sets [unit+0x138c] = 0x2710 and stamps [unit+0x1390], so the OK
            # only STARTS a countdown -- the client then waits for the server to
            # say it finished. Answering OK and stopping would move the hang ten
            # seconds later rather than fix it.
            if real_id == 0x209A and mid == ok and args.validate_finish > 0:
                sess._SESSION["validate_due"] = time.time() + args.validate_finish
                print("[feworld]    validate sequence armed: 0x1157 FINISH in "
                      "%.1fs (the client's own countdown constant is 0x2710 = "
                      "10000ms, so the default matches what it already expects)"
                      % args.validate_finish, flush=True)
            if real_id == 0x209C:
                sess._SESSION.pop("validate_due", None)
            # THE DUMMY AREA. 0x1045 releases the field, and mode 0x0B then
            # walks state 6 -> 7 -> 8 and PARKS in 8 forever, because state 8
            # is the war-result screen's frame and there is no war-result
            # screen. 0x1100 is the only message that builds one. See
            # WAR_RESULT for the whole chain, including the part that is the
            # PLAYER's move (closing the window) and not ours.
            if real_id == 0x2033 and mid == ok and args.war_result != "off":
                warresult.war_result_push(conn, outbound, mode, be, args)
            if real_id == 0x2033 and mid == ok:
                # KEY: FIELD OUT IS WHERE `in_field` GOES FALSE -- and until
                # 2026-09-12 NOTHING EVER CLEARED IT. It was set True once on
                # field entry (one assignment in the whole file) and stayed
                # true for the life of the session, with `field` holding
                # whatever the player last entered. Found live: the map's
                # population marker stayed on the field the player had LEFT
                # ("the 1 icon is still over the map I first went to"), because
                # field_census counts sessions by (in_field, field) -- and my
                # own comment in field_census asserted "to field OUT,
                # `in_field` goes false", which was never true.
                #
                # It is not only the census. Everything that asks "is this
                # player in a field" was answered yes forever:
                # fecampaign._present_here could never take its cleanup branch
                # (the keeps, the giant crystals and the served buildings are
                # popped there), fepvp would still route a hit at a player
                # standing on the map, and the war pump's own in_field gate
                # (the one whose failure it logs at 5021) could not close.
                #
                # Pushed AFTER war_result_push above, which reads the session.
                if sess._SESSION.get("in_field"):
                    print("[feworld]    FIELD OUT: in_field cleared (was field "
                          "%s). Until today this flag was never cleared, so the "
                          "map kept counting this player in the field they had "
                          "left." % sess._SESSION.get("field"), flush=True)
                sess._SESSION["in_field"] = False
                sess._SESSION.pop("field", None)
                combat.battle_reset()
        elif real_id == 0x2049:
            # KEY: THE `GET!` BUTTON -- MSG_ACQUIRE_SKILL_REQUEST.
            #
            #     [u16 skillId][u8 1]
            #
            # built at 0x050d4a8c: 0x5045bb0 writes the u16 from [window+0xc8]
            # (+2 on the cursor) and 0x5045b80 writes the u8 (+1). Its
            # registration two instructions earlier (0x050d4a7c/0x050d4a83)
            # names the only two answers: 0x1078 OK / 0x1079 NG.
            #
            # 0x1078's arm (0x050534f7) READS NOTHING -- it just calls
            # 0x050d4cf0 on the skill window if [0x534406c] is live. So the OK
            # is header-only. 0x1079 (0x05053511) logs MSG_ACQUIRE_SKILL_NG and
            # reads a u32 error code, so the pair is NOT symmetric.
            #
            # WARNING: ANSWERING OK IS NOT ENOUGH ON ITS OWN. The skill has to go into
            # the store, because the list the client displays comes back from
            # US in 0x303E -- acknowledge without persisting and the skill is
            # gone on the next login, having cost the player SP for nothing.
            # That is the whole reason this is its own arm and not another row
            # in UI_HEADER_ONLY_OK.
            f = inner[2:]
            skill = struct.unpack_from(">H", f, 0)[0] if len(f) >= 2 else 0
            if args.acquire_skill == "off":
                print("[feworld]    0x2049 ACQUIRE_SKILL id=%d -- NOT ANSWERED "
                      "(--acquire-skill off); the skill window will hang"
                      % skill, flush=True)
                continue
            if args.acquire_skill == "ng":
                wire.send(conn, outbound,
                     wire.inner_msg(0x1079, struct.pack(">I", args.ui_auto_err),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1079 MSG_ACQUIRE_SKILL_NG "
                      "err=%d for skill=%d" % (args.ui_auto_err, skill),
                      flush=True)
                continue
            stored, rows = skilllist.acquire_skill(args, skill)
            wire.send(conn, outbound, wire.inner_msg(0x1078, b"", wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x1078 MSG_ACQUIRE_SKILL_OK "
                  "(header-only) for skill=%d -- %s; %d skill(s) now stored, "
                  "and they are what 0x303E serves back"
                  % (skill, "PERSISTED" if stored is True
                     else "already known, list unchanged" if stored is None
                     else "WARNING: NOT PERSISTED -- no stored character matched "
                          "account=%r charid=%s; the point is SPENT and the "
                          "skill will be gone at the next login"
                          % (sess._SESSION.get("account"), sess._SESSION.get("charid")),
                     len(rows)),
                  flush=True)
            # Re-push the status record so the window agrees with the store
            # immediately rather than at the next login.
            if args.status_resync == "on":
                if args.char_record != "off":
                    charsheet.char_record_push(conn, outbound, mode, be, args)
                if args.status != "off":
                    charsheet.status_push(conn, outbound, mode, be, args)
        elif real_id in (0x20A0, 0x20A1, 0x20A2):
            # KEY: THE BLACKLIST, END TO END -- three requests, and until now all
            # three hung the dialog that sent them.
            #
            # All three are SLASH COMMANDS with local handlers that build the
            # message; the UI just formats a command line and runs it
            # (0x05171489 does sprintf("/setblacklist %d %s", myCharId, name)).
            # So the command table at 0x052eacc0 and the wire protocol are the
            # same surface -- see GM_COMMANDS.
            #
            #   0x20A0  /setblacklist  0x05133220  [u32 myCharId][cstr NAME]
            #           registers 0x115F/0x1160 at 0x05133270/0x05133277
            #   0x20A1  /rmblacklist   0x051332c0  [u32 myCharId][u32 targetId]
            #           registers 0x1161/0x1162 at 0x05133383/0x0513338a
            #   0x20A2  /getblacklist  0x051333d0  [u32 myCharId]
            #           registers NOTHING -- the list arrives unsolicited
            #
            # THE REPLY BODIES, read reader-call by reader-call off each arm
            # (0x5045dc0 = u8, 0x5045e30 = u16, 0x5045e60 = u32,
            # 0x5045f50 = cstr; every one of those confirmed by the byte count
            # its stream primitive advances):
            #
            #   0x115F  arm 0x050241e2   [u32 id][cstr name]   -> append
            #   0x1161  arm 0x05024413   [u32 id]              -> erase by id
            #   0x1163  arm 0x05024490   [u32 count] then
            #                            count x { [u32 id][cstr name] }
            #                            -> CLEARS the vector first, then fills
            #
            # WARNING: THE u32 IN 0x115F IS THE TARGET'S ID, NOT THE REQUESTER'S. The
            # client appends {id, name} to its vector (0x05170be0) and
            # MSG_REMOVE erases by that id (0x05170cc0, `cmp [eax], edx` over a
            # 0x14 stride). Echoing the requester's charid -- which is what the
            # request carries -- would give every row the SAME id and make
            # removing one remove the wrong one. blacklist_add() allocates a
            # stable id instead.
            f = inner[2:]
            if args.blacklist == "off":
                print("[feworld]    0x%04X blacklist request -- NOT ANSWERED "
                      "(--blacklist off); the dialog will hang, which is the "
                      "pre-2026-08-25 behaviour and only useful as an A/B"
                      % real_id, flush=True)
                continue
            who = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else 0
            if real_id == 0x20A0:
                # WARNING: THE CLIENT UPPER-CASES THE NAME: adding "Jimmy" put
                # 4a 49 4d 4d 59 00 = "JIMMY\0" on the wire. Store what
                # arrived; do not try to restore the case, we do not have it.
                raw = f[4:]
                name = raw.split(b"\0", 1)[0].decode("cp932", "replace")
                nid, rows, fresh = character.blacklist_add(args, name)
                if not fresh:
                    print("[feworld]    0x20A0 SET_BLACKLIST %r from charid=%d "
                          "-- ALREADY on the list as id=%d; re-answering 0x115F "
                          "with the SAME id so the client's append is a no-op "
                          "duplicate of a row it has, not a second row"
                          % (name, who, nid), flush=True)
                body = (struct.pack(">I", nid)
                        + name.encode("cp932", "replace") + bytes(1))
                wire.send(conn, outbound, wire.inner_msg(0x115F, body, wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x115F MSG_SET_BLACKLIST_OK "
                      "id=%d name=%r (%d row(s) stored)"
                      % (nid, name, len(rows)), flush=True)
            elif real_id == 0x20A1:
                target = (struct.unpack_from(">I", f, 4)[0]
                          if len(f) >= 8 else 0)
                gone = character.blacklist_remove(args, target)
                wire.send(conn, outbound,
                     wire.inner_msg(0x1161, struct.pack(">I", target),
                               wire.unit_id_of(args)),
                     mode, be, args.seq_mode == "echo", args.world_prefix)
                print("[feworld] -> 0x30 inner 0x1161 MSG_REMOVE_BLACKLIST_OK "
                      "id=%d (requested by charid=%d; %s)"
                      % (target, who,
                         "row dropped from the store" if gone else
                         "WARNING: NO stored row had that id -- the client will still "
                         "erase it locally"), flush=True)
            else:
                character.blacklist_send(conn, outbound, mode, be, args, who)
        elif real_id == 0x2080:
            # KEY: 0x2080 MSG_GET_CHARACTER_DISTRIBUTION_INFO_REQUEST -- no body,
            # polled every ~10s (accumulator [obj+0x9e] vs 0x2710) and
            # unanswered 80 times in a single measured session.
            #
            # OK = 0x1123 (main dispatcher, special-cased at 0x050549c7
            # `cmp 0x1123 / je` -> arm 0x05055b74 -> 0x05010ff0).
            # NG = 0x1124 (arm 0x05055bc2).
            #
            # THE BODY, read call by call in 0x05010ff0:
            #
            #     0x05011006  0x5045e30  u16 count   -> clamped to 0xFF by
            #                                          0x05010d70 and stored at
            #                                          [obj+0xda2]
            #     then, count times:
            #     0x0501104c  0x5045dc0  u8
            #     0x0501105b  0x5045dc0  u8
            #     0x0501106a  0x5045dc0  u8   <- low 5 bits = a class code
            #                                    (1,4,5,6,7,8,9 are tested at
            #                                    0x050110cc..), top bit = the
            #                                    side, compared against the
            #                                    player's own nation
            #     0x05011079  0x5045e60  u32  <- a CHARACTER ID: it is fed to
            #                                    0x05051160 to find the unit and
            #                                    read [unit+0x50]
            #
            # WARNING: COUNT 0 IS THE DEFAULT AND IT IS THE HONEST ANSWER. What the
            # rows DRAW is unmeasured -- probably the minimap distribution dots
            # (cf. the A-H x 1-5 minimap grid) -- so serving invented characters
            # would be inventing a screen. The point of answering at all is that
            # the poll stops being an unanswered request.
            if args.distribution == "off":
                print("[feworld]    0x2080 CHARACTER_DISTRIBUTION poll -- NOT "
                      "ANSWERED (--distribution off)", flush=True)
                continue
            rows = args.distribution_rows
            payload = struct.pack(">H", len(rows) & 0xFF)
            for a, b, cls, cid in rows:
                payload += struct.pack(">BBBI", a & 0xFF, b & 0xFF,
                                       cls & 0xFF, cid & 0xFFFFFFFF)
            wire.send(conn, outbound, wire.inner_msg(0x1123, payload, wire.unit_id_of(args)),
                 mode, be, args.seq_mode == "echo", args.world_prefix)
            sess._SESSION["dist_polls"] = sess._SESSION.get("dist_polls", 0) + 1
            if sess._SESSION["dist_polls"] <= 3 or sess._SESSION["dist_polls"] % 30 == 0:
                print("[feworld]    0x2080 poll #%d -> 0x1123 "
                      "MSG_GET_CHARACTER_DISTRIBUTION_INFO_OK with %d row(s)"
                      % (sess._SESSION["dist_polls"], len(rows)), flush=True)
        elif real_id == 0xF900:
            # KEY: THE GM COMMAND CHANNEL, INBOUND.  [u16 len][ascii command line]
            #
            # FE routes a whole class of UI actions through its own slash
            # command dispatcher and then forwards the line here with the
            # LEADING SLASH STRIPPED -- setting a profile comment to
            # "Hello comment" put
            #
            #     f9 00 | 00 15 | "comment Hello comment"
            #
            # on the wire, len = strlen, no terminator. The vocabulary is the
            # client's own table at 0x052eacc0; see GM_COMMANDS.
            #
            # WARNING: THE FRAME REGISTERS NO REPLY (its builder 0x0517e050 just does
            # beginMessage/serialise/flush, with none of the
            # `mov word ptr [esp+N], imm16` pairs every other request has). So
            # "what answers 0xF900" has no single answer: the COMMAND's own OK
            # id does. For `comment` that is 0x111E MSG_SET_COMMENT_OK
            # (arm 0x05055b24, which logs "> MSG_SET_COMMENT_OK" and READS
            # NOTHING -- header-only, like 0x1020 and 0x100E; a body here would
            # desynchronise the stream). 0x111F is the NG (arm 0x05055b3f).
            f = inner[2:]
            ln = struct.unpack_from(">H", f, 0)[0] if len(f) >= 2 else 0
            text = f[2:2 + ln].decode("cp932", "replace")
            verb, _, rest = text.partition(" ")
            print("[feworld]    *** 0xF900 GM COMMAND %r (len=%d, verb=%r)"
                  % (text, ln, verb), flush=True)
            if verb == "comment":
                # The client truncates to 23 bytes itself (0x05138240 checks
                # strlen > 0x17 and clips on a DBCS lead byte), so whatever
                # arrives is already the field's real width.
                # WARNING: THE KEY IS "profile_comment", NOT "comment".
                # felobby's CHAR_FIELDS maps the key "comment" to record
                # offset 0xE4, and 0xE4 is the USE-PROHIBITED (suspension)
                # comment -- named from the client's own
                # 0x05025493 "use-prohibited comment %s" and printed by its
                # dump at 0x05025481 as `banned comment %s`. Storing the
                # profile text under "comment" served it back as a BAN
                # NOTICE: the live log read `banned comment JOJ`, which is
                # us telling the client the player is suspended with their
                # own profile text as the reason. It only stayed harmless
                # because the suspension PERIOD fields beside it
                # (period_from 0xDC / period_to 0xE0) are still zero.
                # WARNING: This leaves the comment STORED BUT UNSERVED, which is
                # honest: the channel that carries a comment TO the profile
                # window is still unmapped. Ruled out so far -- /profile's
                # handler 0x05137960 is a stub (`mov al,1; ret`),
                # /comment's 0x05138240 only truncates to 23 bytes so the
                # client keeps nothing locally, and the NG name table has
                # no MSG_*PROFILE*/MSG_GET_COMMENT at all. Best remaining
                # lead: identity sub-record bit 0x8000 -> +0x3d8, a cstr we
                # have never served.
                if character._store_char_field(args, "profile_comment", rest):
                    print("[feworld]    profile comment persisted: %r -- served "
                          "back on the next login by whatever carries it; the "
                          "STORE side is now real, which is the half that was "
                          "losing it across a relaunch" % rest, flush=True)
                else:
                    print("[feworld]    WARNING: profile comment %r NOT persisted -- "
                          "no stored character matched account=%r charid=%s"
                          % (rest, sess._SESSION.get("account"),
                             sess._SESSION.get("charid")), flush=True)
                if args.set_comment != "off":
                    mid = 0x111E if args.set_comment == "ok" else 0x111F
                    wire.send(conn, outbound,
                         wire.inner_msg(mid, b"", wire.unit_id_of(args)),
                         mode, be, args.seq_mode == "echo", args.world_prefix)
                    print("[feworld] -> 0x30 inner 0x%04X MSG_SET_COMMENT_%s "
                          "(header-only) -- watch the client log for "
                          "'> MSG_SET_COMMENT_OK'"
                          % (mid, "OK" if mid == 0x111E else "NG"), flush=True)
            elif verb in ("setblacklist", "rmblacklist", "getblacklist"):
                # These have local handlers that ALSO build 0x20A0/0x20A1/0x20A2
                # (0x05133220 / 0x051332c0 / 0x051333d0), so the same action can
                # reach us twice by two routes. Answer the message form and log
                # the command form rather than acting on both -- acting twice is
                # how a name lands in the list twice.
                print("[feworld]    (handled via 0x20A0/0x20A1/0x20A2, not "
                      "here -- the command form is logged only)", flush=True)
            else:
                known = any(verb in v for v in gm.GM_COMMANDS.values())
                print("[feworld]    no server-side handler for %r (%s)"
                      % (verb, "a known verb in the client's own table"
                         if known else "NOT in the client's command table -- "
                         "new vocabulary, worth recording"), flush=True)
        elif real_id in messages._CHAT_IDS:
            # CHAT, all six ids. Built by the /say /all /army /party /tell
            # handlers at 0x051334f0 / 0x05133610 / 0x05133730 / 0x05133970 /
            # 0x05133a90: [cstr [unit+0x389] name][cstr text], and 0x2067 is
            # [cstr TARGET][cstr name][cstr text]. None registers a reply.
            #
            # KEY: THE CLIENT DOES NOT LOG ITS OWN /say. 0x051334f0 joins the
            # words (0x5133c50), sends, and calls 0x05129890 -- which is the
            # per-object BALLOON slot ([obj+0x47c]), not the log. The chat
            # LOG line comes from the chat-window listener at 0x0512bf60,
            # which only ever sees lines the arm 0x05053cbe BROADCASTS
            # (0x509fc50) -- i.e. lines that arrived from the server. So the
            # server's echo of your own line is what put it in the log in
            # retail, and 'self' echoes it with your own unit id, which is
            # the shape the arm has a test for (0x05053d8a: speaker == me ->
            # skip the balloon it already drew). /tell is the exception: its
            # handler logs ">> target : text" itself (0x512c8b0 at
            # 0x05133b22), so echoing a tell would double it.
            #
            # WARNING: THE TEXT IS cp932 ON THE WIRE. Relayed bytes go back out as
            # the same cp932 (encode after decode is byte-identical for valid
            # sequences), and the far client draws them as cp932 too.
            name, want = messages._CHAT_IDS[real_id]
            want = want or 2
            f = inner[2:]
            parts = [p.decode("cp932", "replace")
                     for p in f.split(bytes(1))[:want]]
            while len(parts) < want:
                parts.append("")
            speaker = parts[1] if real_id == 0x2067 else parts[0]
            text = parts[-1]
            chat._chat_room_name(speaker)
            print("[feworld]    0x%04X CHAT %s %r: %r%s"
                  % (real_id, name, speaker, text,
                     " (to %r)" % parts[0] if real_id == 0x2067 else ""),
                  flush=True)
            if args.chat_echo == "gm":
                gm.gm_command(conn, outbound, mode, be, args,
                           "@%s: %s" % (speaker, text))
            elif args.chat_echo == "say":
                # The 08-25 shape: back on the same id with --chat-unit-id.
                chat.chat_send(conn, outbound, mode, be, args, real_id, parts)
            elif args.chat_echo == "self" and real_id != 0x2067:
                # The retail shape: the speaker IS this player.
                chat.chat_send(conn, outbound, mode, be, args, real_id, parts,
                          speaker=wire.unit_id_of(args))
            if getattr(args, "chat_relay", "on") == "on":
                n = chat.chat_relay(real_id, parts, field=sess._SESSION.get("field"),
                               nation=territory.nation_of(args))
                print("[feworld]    chat relay: queued for %d other "
                      "session(s)%s" % (n, "" if n or real_id != 0x2067 else
                                        " -- no in-field session signs as %r"
                                        % parts[0]), flush=True)
        elif (real_id == 0xF102
              and getattr(args, "discard", "on") != "off"):
            # KEY: DISCARDING AN ITEM -- unhandled until now, so a dropped item
            # came back on the next field entry. Reported live 2026-09-06
            # ("dropping my extra cane"), captured the same minute:
            #
            #     f1 02 | 00 00 | 00 00 00 01 | 00 00 03 ed | 02 13 | 00 01 | 00 00
            #
            # Builder 0x05074ea0, and the client names it itself at 0x052de878:
            # `> ComamndDiscardItem name = %s objid = %d` (SE's own typo). Field
            # for field, from the stores into the 16-byte struct it sends:
            #
            #     [u16 0]
            #     [u32 owner ]  0x050739e0(item) -- the object id 0x05073f90
            #                   compares against the local player, and it was 1
            #                   here, which is --unit-id
            #     [u32 uid   ]  [item+0x3c]
            #     [u16 item  ]  word 0 of [item+0x4e8], the TABLE record --
            #                   i.e. the item number, not the uid
            #     [u16 count ]  the caller's argument
            #     [u16 0]
            #
            # WARNING: The uid is the field to act on; the item number is only a
            # cross-check. Acting on the item number would drop the WRONG
            # stack whenever a bag held two of something -- and this bag held
            # two Beginner Wands, which is what made the difference visible.
            f = inner[2:]
            if len(f) < 14:
                print("[feworld]    0xF102 DISCARD: %d-byte body, expected 16 "
                      "-- not decoded: %s" % (len(f), f.hex()), flush=True)
                continue
            owner, duid = struct.unpack_from(">II", f, 2)
            dno, dcount = struct.unpack_from(">HH", f, 10)
            old = itemrecords.item_rows(args)
            equip = [tuple(x) for x in (character._load_char_field(args, "equip", []) or [])]
            hit = [(i, r) for i, r in enumerate(old) if int(r[0]) == duid]
            if not hit:
                print("[feworld]    0xF102 DISCARD uid=%d item=%d x%d owner=%d "
                      "-- NOT in this character's bag, nothing removed "
                      "(body %s)" % (duid, dno, dcount, owner, f.hex()),
                      flush=True)
                continue
            i, row = hit[0]
            if int(row[1]) != dno:
                # A cross-check, not a gate: the client built the number from
                # the item's own table record, so a mismatch means our bag and
                # the client's disagree about what uid %d IS -- worth saying
                # out loud rather than silently trusting either.
                print("[feworld]    WARNING: 0xF102 uid=%d is item %d here and %d on "
                      "the client" % (duid, row[1], dno), flush=True)
            if row[3] > dcount:
                new_rows = list(old)
                new_rows[i] = (row[0], row[1], row[2], row[3] - dcount)
                gone = []
            else:
                new_rows = old[:i] + old[i + 1:]
                gone = [(duid, i)]
            worn = [(sl, u) for sl, u in equip if int(u) == duid]
            if worn:
                # Dropping something you are wearing takes it off first, or the
                # stored equipment names a uid the bag no longer has and every
                # later worn-index lookup misses.
                equip = [(sl, u) for sl, u in equip if int(u) != duid]
                character._store_char_field(args, "equip", [list(x) for x in equip])
            stored = character._store_char_field(args, "items",
                                       [list(r) for r in new_rows])
            print("[feworld]    0xF102 DISCARD uid=%d item=%d x%d -- bag %d -> "
                  "%d row(s)%s, stored=%s. No reply is registered for this id "
                  "and none is sent; the client drops it locally and this is "
                  "only what makes the drop SURVIVE the next field entry."
                  % (duid, dno, dcount, len(old), len(new_rows),
                     ", unequipped from worn slot %s" % worn[0][0] if worn
                     else "", stored), flush=True)
            if worn:
                equipment.unequip_push(conn, outbound, mode, be, args,
                             fegamedata.worn_index_for(
                                 fegamedata.items().get(int(row[1]), {})
                                 .get("slot", -1)),
                             "0xF102 discard of worn uid %d" % duid)
            if getattr(args, "bag_organize", "on") != "off":
                inventory.bag_layout_push(conn, outbound, mode, be, args, old, new_rows,
                                gone, "discarded uid %d (item %d)"
                                % (duid, dno))
        elif real_id == 0xA011:
            # VERIFIED:KEY: MSG_NPC_HIT_NOTIFY -- THE COMBAT CHANNEL, decoded
            # 2026-09-06 (static). The client is authoritative for its OWN hits
            # on an NPC: it swings, it does the collision, and it TELLS us.
            # Nothing about damage is computed client side -- see combat_hit.
            #
            # Builder 0x05069700 (a method on the unit being hit; the log line
            # is 0x052de4f8 `< MSG_NPC_HIT_NOTIFY : [%08x] SkillId=%d
            # UniqueId=%d`). Writer for writer, and each writer's width is its
            # own advance of [0x533835c] -- 0x5045b80 +1, 0x5045bb0 +2,
            # 0x5045be0 +4, 0x5045ca0 +4 (f32):
            #
            #     [u32 target ]  0x5077cc0(this)     the unit that was HIT
            #     [u32 attacker] 0x5021f00(ev)       gated == the local player
            #     [u32 b      ]  0x5021fb0(ev)
            #     [u32 c      ]  0x5021ee0(ev)
            #     [u16 skill  ]  0x5021ef0(ev)       the skill that landed
            #     [u8  kind   ]  0x5021f20(ev) + 1
            #     [f32 x][f32 y][f32 z]              the HIT POINT
            #
            # It is only sent when ALL of these hold (0x05069700):
            #   * the event object's class tag is 0x3E8 (0x4fcf8f0)
            #   * 0x5021bb0(ev) is true
            #   * the attacker id == [0x533998c]+0x9e0, THE LOCAL PLAYER --
            #     so this is never another player's hit, and PvP is not here
            #   * the target is out of its invincibility window
            #     ([unit+0x8d4] vs the clock); otherwise it logs `Invincible`
            #     (0x052de530) and sends NOTHING
            #   * the skill resolves (0x505f4b0) and 0x504a7c0 accepts it
            f = inner[2:]
            combat.combat_hit(conn, outbound, mode, be, args, f)
        elif real_id in (0x209F, 0x2002, 0x2023):
            # The three fire-and-forget notifies. WARNING: CHECKED: none of them
            # registers an expected reply, so none of them hangs anything, and
            # answering them is not work that is waiting to be done.
            #
            #   0x209F  [u32 [obj+0x3d4]]  built at 0x05079f60, sent by
            #           /disphp (0x05135531) and /freecam (0x05135605/0x051356a5)
            #           and from the scene update (0x04ffa23c/0x04ffa3c0). A
            #           display-state toggle; the observed "toggles 0 -> 1"
            #           is that flag.
            #   0x2002  [u16][u16]  built at 0x0507fe15 (0x5045c40 is the u16
            #           writer, not the u32 one -- the old "[u32]" reading came
            #           from the 4-byte length, not from the writers), gated on
            #           [0x5336d1c]+0x4fc and 0x04ff7f70.
            #   0x2023  14 or 28 B of telemetry at ~400ms.
            f = inner[2:]
            if real_id == 0x2023:
                sess._SESSION["telemetry"] = sess._SESSION.get("telemetry", 0) + 1
                if len(f) == 28:
                    clock.note_client_clock(f)
                    arrival.field_ready_release(conn, outbound, mode, be, args,
                                        "the first 28-byte clock sample")
                    # The client's own position, i16 x0.1 (see _MV_POS) --
                    # what `!build` places a building at.
                    px, py, pz = struct.unpack_from(">hhh", f, movement._MV_POS)
                    sess._SESSION["cpos"] = (px * 0.1, py * 0.1, pz * 0.1)
                echoed = movement.move_authority(conn, outbound, mode, be,
                                        args, f)
                if args.unit_speed_repeat == "on":
                    movement.unit_speed_push(conn, outbound, mode, be, args)
                events.npc_walk_push(conn, outbound, mode, be, args, f)
                entities.monster_place_push(conn, outbound, mode, be, args, f)
                monsters.mob_respawn_tick(conn, outbound, mode, be, args)
                monsters.monster_chase_tick(conn, outbound, mode, be, args, f)
                monsters.monster_attack_tick(conn, outbound, mode, be, args)
                if sess._SESSION["telemetry"] % 100 == 1:
                    print("[feworld]    0x2023 telemetry x%d (%d B) -- %s"
                          % (sess._SESSION["telemetry"], len(f),
                             "ECHOED back as action 0 (--move-authority)"
                             if echoed else
                             "fire-and-forget, not answered"), flush=True)
            elif real_id == 0x209F:
                v = struct.unpack_from(">I", f, 0)[0] if len(f) >= 4 else None
                print("[feworld]    0x209F display-state notify = %s "
                      "(no reply registered)" % v, flush=True)
            else:
                v = struct.unpack_from(">HH", f, 0) if len(f) >= 4 else None
                print("[feworld]    0x2002 %s (no reply registered)" % (v,),
                      flush=True)
        elif real_id == 0x400F:
            # MSG_SERVER_UNIT_LOGIN_REQUEST, built at 0x0504859e and named by the
            # client's own log line at 0x52d5260:
            #     <MSG_SERVER_UNIT_LOGIN_REQUEST : [%s] / ChrID=%d
            #
            #     [cstr account][u32 charid][52-byte credential blob]
            #
            # -- writer for writer: 0x5045cd0 (string, from [0x5336d1c]+0x8e),
            # 0x5045be0 (dword, [edi+0x34] = the chosen character's id), then
            # 0x5045ad0 with an explicit length of 0x34.
            f = inner[2:]
            try:
                e = f.index(b"\0")
                acct = f[:e].decode("cp932", "replace")
                charid = struct.unpack_from(">I", f, e + 1)[0]
                blob = f[e + 5:]
            except (ValueError, struct.error):
                acct, charid, blob = "?", 0, b""
            print("[feworld]    WORLD LOGIN account=%r charid=%d blob=%dB"
                  % (acct, charid, len(blob)), flush=True)
            # WARNING: `acct` IS THE CLIENT'S ECHO OF WHATEVER felobby PUT IN 0xC010 --
            # not a name we can key a store by on its own. Before 2026-08-24
            # that was the constant "TestPlayer" for everybody, and
            # _store_char_field wrote the chosen nation onto whatever character
            # in the single shared roster happened to carry this charid.
            #
            # resolve_account() turns it into the store key felobby used: the
            # echo first (free and exact when the client is verbatim, which is
            # LIKELY but has never been proven byte-for-byte on a live 0x400F),
            # then felobby's handoff file by address, then a direct POL member
            # lookup. It logs which one fired, because a carry that quietly
            # stopped working looks exactly like one that worked.
            key = character.resolve_account(acct, wire._peer_ip(conn), args)
            sess._SESSION["account"], sess._SESSION["charid"] = key, charid
            sess._SESSION["wire_account"] = acct
            print("[feworld]    blob: %s" % blob.hex(), flush=True)
            if args.capture_only:
                print("[feworld]    --capture-only: NOT answering", flush=True)
                continue
            # Its builder pre-registers exactly two acceptable replies
            # (0x05048584/0x0504858b): 0x302B OK and 0x302C NG.
            #
            # What the OK arm (0x050476d3) actually DOES is the useful part: it
            # stores one dword at [obj+0x9e0], logs `> MSG_SERVER_UNIT_LOGIN_OK`,
            # and sets [obj+0x948] = 0. That last store is the one that matters --
            # the relay-connect state at 0x0504860b reads [esi+0x948] and only
            # advances the login routine when it is 0 (1 takes the failure path).
            # The NG arm (0x050476b0) sets it to 1.
            #
            # WARNING: THE DWORD IS A GUESS. Its slot could not be pinned by hand-tracing
            # esp through that switch, so --unit-login-value is exposed rather
            # than pretended: 0 is the default, and the client's own log
            # (`> MSG_SERVER_UNIT_LOGIN_OK`) says whether the message was accepted
            # regardless of what the dword turns out to mean.
            # KEY: 2026-08-24: RESOLVE 'auto' TO THE CHARID, and note WHY this is
            # the single most informative value to send.
            #
            # Measured live in the field with felive:
            #   server unit ([0x533998c], the 0x302B target) +0x9e0 = 0
            #   the entity we spawn for the player in 0x1000  id    = charid = 1
            #   player unit [0x533aab4]                       pos   = (0,15.948,0)
            # Three ids that should describe ONE character, and they disagree:
            # the client is told "you are unit 0" and then handed a body in the
            # world called unit 1. The server unit sits inert at the origin while
            # the player unit stands correctly on the ground -- and movement input
            # does nothing. WARNING: THAT IS A HYPOTHESIS FOR THE MOVEMENT WALL, NOT A
            # FINDING; it is a coherent story for the two-object split, no more.
            #
            # KEY: SETTLED 2026-08-24 (STATIC, not yet corroborated live):
            # [esp+0x10] IS THE INNER HEADER'S LEADING u32. --unit-login-value
            # CANNOT reach +0x9e0; --unit-id is the only knob that does.
            #
            # The arm is not a function on its own -- it is an arm of the pump
            # at 0x05047590, and reading its esp needs that prologue:
            # `sub esp,0x14 / push ebp / push esi`, so the 0x14 bytes of locals
            # start at the first local dword. The pump then reads TWO values off
            # the stream before dispatching:
            #     0x050475b0  0x522f9b0(stream, &A)   advances 4  -> u32
            #     0x050475bd  0x522f970(stream, &B)   advances 2  -> u16
            # (both readers confirmed by their own `add ecx,4` / `add ecx,2`
            # bounds check at 0x0522f9c4 / 0x0522f984), and B is the one
            # compared against 0x2005 / 0x302B / 0x302C at 0x050475e1..0x050475f8
            # -- so B is the MESSAGE ID and A is the u32 read BEFORE it, i.e. the
            # frame's leading u32.
            #
            # Four independent sites in that frame agree on the two slots:
            #     0x050475fe  main dispatcher   f(A, B, stream)
            #     0x0504765b  handler chain 1   f(A, B, stream)
            #     0x0504767a  handler chain 2   f(A, B, stream)
            #     0x05047699  handler chain 3   f(A, B, stream)
            # and the OK arm's `mov eax,[esp+0x10]` at 0x050476d9 resolves to the
            # same A. Every one of those only type-checks if A is the header.
            #
            # WARNING: SO ff265024 (--unit-login-value auto) WAS A NO-OP FOR +0x9e0.
            # The payload candidate was already ruled out live (sent charid, read
            # 0 back); this says why, and closes the row.
            #
            # WARNING: AND THERE IS A SECOND, UNMEASURED MISMATCH IN THE SAME PAIR.
            # 0x302B WRITES [0x533998c]+0x9e0 (0x050476d3), but the 0x1027
            # MSG_SET_POSITION handler TESTS [[scene+0x88]]+0x9e0
            # (0x05054e4a: `mov edx,[ecx+0x88]` / 0x05054e57: `mov eax,[edx+0x9e0]`).
            # If those two pointers are not the same object, ANY non-zero
            # --unit-id writes one and tests the other and 0x1027 is dropped in
            # silence -- which is a second, sufficient explanation for the
            # --unit-id auto black screen, independent of the header-on-0x302B
            # bug that was fixed in 36358c17. felive prints both pointers and
            # flags them when they differ; check that BEFORE trusting --unit-id
            # auto as a clean test of anything.
            ulv = (sess._SESSION.get("charid", 0) if args.unit_login_value == "auto"
                   else int(args.unit_login_value, 0))
            # WARNING: THE HEADER GOES HERE TOO. 2026-08-24: --unit-id auto changed
            # the leading u32 on 0x1027/0x100E/0x1012/0x1127 but NOT on this
            # message, which is the only one that can set the server unit's id.
            # So 0x1027 arrived carrying 1 against a server unit still holding
            # 0, was dropped, [scene+0x296] never got set, and mode 0xa substate
            # 2 parked -> BLACK SCREEN (measured live: the client goes silent
            # right after "0x1027 ... unit=1" and idles out). A mismatch BY
            # CONSTRUCTION, entirely self-inflicted.
            body = wire.inner_msg(0x302B, struct.pack(">I", ulv & 0xFFFFFFFF),
                             wire.unit_id_of(args))
            wire.send(conn, outbound, body, mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x302B MSG_SERVER_UNIT_LOGIN_OK "
                  "(value=%d%s) -- lands at [0x533998c]+0x9e0, the SERVER "
                  "UNIT's id; re-read it with `felive --scene` in the field"
                  % (ulv, " = charid, --unit-login-value auto"
                     if args.unit_login_value == "auto" else ""), flush=True)
            print("[feworld]    watch the client for '> MSG_SERVER_UNIT_LOGIN_OK'. "
                  "'> MSG_SERVER_UNIT_LOGIN_NG' means it parsed but rejected; "
                  "SILENCE means it never accepted the frame at all.", flush=True)
            # the world clock, now: logged in, no field loading, the map
            # screen about to draw its clock (--world-clock connect)
            clock.clock_connect_push(conn, outbound, mode, be, args)


def _serve_one(conn, peer, args):
    """One world session, on its own thread."""
    try:
        hs = wire.do_handshake(conn, args)
        if hs:
            serve(conn, args, *hs)
    except (OSError, IOError) as e:
        print("[feworld] socket error (%s:%d): %r" % (peer[0], peer[1], e),
              flush=True)
    except Exception as e:                      # noqa: BLE001
        # A thread dying silently would look like the client hanging up, which
        # is the single most misleading failure this file can produce.
        print("[feworld] session error (%s:%d): %r" % (peer[0], peer[1], e),
              flush=True)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        print("[feworld] CLOSED (%s:%d)" % peer, flush=True)
