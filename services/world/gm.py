"""GM commands (!verbs) and the pump that runs them."""
import os
import struct
import time
import fedevtool  # noqa: E402  -- the world-building panel (--devtool-port)
import fegamedata  # noqa: E402  -- dat.pak's spawn/NPC/item tables
from . import buildings, chat, clock, devtool, doors, entities, events, ext, extrun, mapcal, messages, sess, spawns, town, wallet, war, wire, zones

GM_COMMANDS = {
    "chat": ("say", "all", "army", "tell", "party", "s", "a", "t", "p", "sign"),
    "config": ("set", "get", "sos"),
    "blacklist": ("setblacklist", "rmblacklist", "getblacklist"),
    "debug": ("matchless", "chr", "put", "break", "stealth", "disphp", "freecam",
              "logout", "exit", "photomode", "sysmes", "fieldmes", "hostmes",
              "areames", "discard", "kill", "cinfo", "help", "itemlist",
              "ignore", "squelch", "freeze", "goto", "comment", "summon",
              "warp", "disphit", "dispcollision", "toggleshadow",
              "toggleinterface", "fixview", "healme", "allitemlist", "iteminfo",
              "itemslot", "monster", "popitem", "boost", "profile", "drawstat",
              "checkcastle", "envmap", "watertex", "wavetex", "expanditemslot",
              "far", "damagemap", "sendpm", "startwar", "mesure", "portal",
              "skillrange", "dispinfo"),
    "emote": ("greet", "think", "joy", "agree", "slap", "bow", "surprised",
              "clap", "blush", "angry", "point", "wave", "disgusted",
              "approach", "kneel", "cheer", "handshake", "salute", "depressed",
              "confuse", "pout"),
}


def gm_command(conn, outbound, mode, be, args, text):
    """Send one 0xF900 GM-command frame:  [u16 len][ascii], no terminator.

    THIS IS THE LEVER. The client's receive path (0x0517ef60, reached from the
    world pump at 0x050476a6 -> 0x0517e5a0, which routes every id whose high
    byte is 0xF1..0xF9) decodes the payload and branches on the FIRST BYTE:

        '/'  -> 0x05133e00(text, 1)   -- runs it through the client's OWN slash
                                        command dispatcher, the same one the
                                        chat box feeds: /warp, /summon,
                                        /healme, /toggleinterface, every emote.
        '@'  -> 0x05121fd0(4, text+1) -- prints the rest as a system message in
                                        the client's message window.

    Anything else is decoded and dropped. The framing is the client's own: its
    send side builds {char text[0x200]; u16 len} and the schema serialiser emits
    the u16 first, which is exactly the wire we captured for a profile comment:

        f9 00 | 00 15 | "comment Hello comment"

    -- length = strlen, NO terminator. Matched byte for byte here.

    0x200 is the client's own buffer size for this field, so longer text is
    truncated here rather than handed to a fixed-size copy on the far side.
    """
    raw = text.encode("cp932", "replace")[:0x1FF]
    wire.send(conn, outbound,
         wire.inner_msg(0xF900, struct.pack(">H", len(raw)) + raw, wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0xF900 GM command %r (%d B) -- %s"
          % (text, len(raw),
             "the client will EXECUTE this" if text[:1] == "/" else
             "the client will PRINT this" if text[:1] == "@" else
             "NEITHER '/' nor '@': the client decodes it and drops it"),
          flush=True)


def validate_pump(conn, outbound, mode, be, args):
    """Push 0x1157 FINISH once the validate countdown has run out.

    Polled from the read loop rather than timed on a thread, for the same reason
    gm_pump is: the client's own 0x2023 telemetry wakes that loop every ~400ms
    in the field, so the resolution is far finer than the 10s this measures and
    it costs nothing.

    0x1157's arm (0x050AD1BE, entry 3 of the screen's table 0x050AD2B8) logs
    ">MSG_START_VALIDATE_SEQUENCE_FINISH_NOTIFY" and READS NOTHING -- so the
    push is the 6-byte header, like the OK that started it.
    """
    due = sess._SESSION.get("validate_due")
    if not due or time.time() < due:
        return
    sess._SESSION.pop("validate_due", None)
    mid, name, arm = messages.VALIDATE_FINISH
    wire.send(conn, outbound, wire.inner_msg(mid, b"", wire.unit_id_of(args)),
         mode, be, args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x%04X %s (header-only, arm %08x) -- the "
          "validate countdown elapsed" % (mid, name, arm), flush=True)


def gm_pump(conn, outbound, mode, be, args):
    """Drain --gmcmd-file, one command per line, and truncate it.

    A live console into the client. The read loop is woken constantly by the
    client's own 0x2023 telemetry (~400ms in the field), so polling a small file
    here costs nothing and needs no second thread.

    Lines are sent VERBATIM, including the leading '/' or '@' -- see
    gm_command() for what each prefix means. A line with neither is a no-op on
    the client and is sent anyway, because silently rewriting an admin's
    input is how a knob stops meaning what it says.
    """
    # KEY: THE PANEL'S QUEUE COMES THROUGH HERE TOO, and that is the whole
    # integration: a button on the web page puts the same string on a queue
    # that a line in the file would have put in the file, and both arrive at
    # the same dispatcher below. One implementation of every command.
    if getattr(args, "devtool_port", 0):
        devtool.devtool_snapshot()
    town.town_live_refresh(conn, outbound, mode, be, args)
    queued = fedevtool.drain()
    path = getattr(args, "gmcmd_file", None)
    if not path and not queued:
        return
    lines = list(queued)
    try:
        if path and (not os.path.exists(path) or os.path.getsize(path) == 0):
            path = None
            if not lines:
                return
        if path:
            with open(path, "r+", encoding="utf-8", errors="replace") as fh:
                lines += fh.read().splitlines()
                fh.seek(0)
                fh.truncate()
    except (OSError, IOError) as e:
        print("[feworld]    --gmcmd-file %r unreadable: %r" % (path, e),
              flush=True)
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # `!` is the SERVER-SIDE sigil: everything else in this file goes to
        # the client verbatim, and a bare word with neither '/' nor '@' is
        # already a documented no-op there, so this adds a directive space
        # without changing what any existing line means.
        if line.startswith("!chat "):
            got = chat.parse_chat_line(line[6:].strip())
            if got:
                chat.chat_send(conn, outbound, mode, be, args, got[0], got[1])
            continue
        # `!war` -- fire the phase advance NOW instead of waiting out the
        # countdown. Added 2026-08-26 during a live test spent sitting in a field
        # watching a five-minute clock: there was no way to trigger this
        # without a recreate, and a recreate drops the session, so testing the
        # transition cost a full re-entry every time.
        # WARNING: It skips the countdown rather than shortening it. To watch the
        # clock ITSELF reach zero, use --war-deadline-ms; this answers the
        # different question, "does the phase advance do anything".
        if line == "!war":
            if sess._SESSION.get("war_phase") in ("prewar", "war", "truce"):
                # A deadline is armed (--war-start deadline / --war-cycle on):
                # collapse it. The pump does the send, so the order of
                # messages is the same as on expiry.
                sess._SESSION["war_force"] = True
                print("[feworld]    !war -- forcing the %s deadline now"
                      % sess._SESSION["war_phase"], flush=True)
                war.war_deadline_pump(conn, outbound, mode, be, args)
                continue
            if sess._SESSION.get("war_started"):
                print("[feworld]    !war -- already advanced this entry. The "
                      "arm moves a phase, so re-sending it is the re-arming "
                      "loop 0x1018's own note warns about; re-enter the field "
                      "to do it again.", flush=True)
                continue
            sess._SESSION["war_started"] = True
            print("[feworld]    !war -- advancing the war phase on demand",
                  flush=True)
            war.war_start(conn, outbound, mode, be, args)
            continue
        # `!clock` -- send the 0x1148 world-clock base NOW (2026-09-12), with
        # or without --world-clock. It is the measurement lever: the base has
        # never landed in steady state, so stand still in a field with
        # `felive --scene --watch` running, send this, and read whether the
        # substates leave `0 4 0 0`. Still once per connection.
        if line == "!clock":
            sess._SESSION["clock_force"] = True
            clock.clock_sync_push(conn, outbound, mode, be, args)
            continue
        # `!crystal N` -- re-serve CRYSTAL without a re-entry, so a value can be
        # checked against the Status screen while it is open.
        if line.startswith("!crystal "):
            try:
                val = int(line[9:].strip(), 0)
            except ValueError:
                print("[feworld]    !crystal wants a number, got %r"
                      % line[9:].strip(), flush=True)
                continue
            # A PROBE value: served this once, NOT written to the store.
            wallet.crystal_push(conn, outbound, mode, be, args, value=val)
            continue
        # `!build TYPE[:MODEL]` -- a building at the player's reported
        # position. The capital's minimap doors: stand on one, `!build 10`
        # (BlackSmith / arms_store_a, the arms-shop door), read the client
        # log for 'Building N ... TYPE=10 (x,y,z)', then walk into it.
        if line.startswith("!build "):
            buildings.build_here(conn, outbound, mode, be, args, line[7:].strip())
            continue
        # `!npc MODELTYPE[:KIND[:LEVEL]]` -- a town NPC (or monster) at the
        # player's reported position, like !build. Town models are 228..274
        # (Npc00..Npc45): dat.pak's fet_npc_type names the shopkeepers --
        # Warrior_Weapon_Shop = 233, Scout_Weapon_Shop = 231, Sorcerer_Weapon_
        # Shop = 230, Item_Shop / Bank_Keeper = 238, Inn_Master = 230,
        # Trainer_Neos = 229 -- but ships NO positions for a capital.
        #
        # WARNING: "the minimap's icons are the only map" is CONTRADICTED and was
        # never measured. Observed live 2026-09-06: the capital minimap
        # shows no icons at all, and a sweep of every DATASTREAM tag in
        # dat.pak finds no town-position table of any kind -- the archive is
        # effects, skills, items, WAR-FIELD buildings, motion and class
        # params. The roster ships (20 roles per capital, identical across all
        # five, keyed by script id) but WHERE each one stood was SE server
        # data and is gone.
        #
        # So placement is: walk to the spot and `!npc 233`. DOORS are the one
        # thing that finds itself -- walk into one and the client sends the
        # door's own position (0x20AC), which is how both known doors in
        # capital 39 were found.
        # `!place X:Z NAME[:KIND[:LEVEL[:YAW]]]` and `!spawnat X:Z [TAG]` --
        # author from the MAP instead of from where you are standing.
        #
        # KEY: WHY THESE EXIST. Everything else here places at the player's
        # reported position, which means walking to twenty spots to dress a
        # capital. The panel draws the minimap with the recovered shop
        # positions on it, so the admin can see where a keeper belongs and
        # click it -- no client needed to CHOOSE a position.
        #
        # WARNING: WHAT THE MAP CANNOT GIVE YOU IS THE HEIGHT. It is a plan view:
        # x and z only. Getting that wrong is not cosmetic -- it is the
        # falling loop of 2026-09-10. So the height comes from the area's own
        # spawn row, somewhere a player has actually stood, and these refuse
        # when there is none, exactly as `!dress` does. One height for a whole
        # town is an ASSUMPTION that the town is flat; walking it afterwards
        # and using `!move` on whatever floats is still the last step.
        # `!mapcal` -- the minimap projection for ONE capital half.
        #
        # The panel sends this when you drag a door marker onto the door as it
        # is PAINTED on the art: the world position is the shipped table's, the
        # pixel is where you dropped it, and the pair is an anchor. See
        # mapcal_proj -- the four numbers are re-solved from the anchors on
        # every read, so a bad drag is undone by dropping that anchor, not by
        # re-deriving anything.
        if line.strip() == "!mapcal" or line.startswith("!mapcal "):
            f_c = line.split()
            sub_c = f_c[1] if len(f_c) > 1 else ""
            live_stem = fegamedata.minimap_name(sess._SESSION.get("field") or -1)
            if sub_c in ("drop", "reset"):
                if len(f_c) < 3:
                    print("[feworld]    !mapcal drop STEM [IDX] -- IDX from "
                          "`!mapcal STEM`, or leave it off to clear the half",
                          flush=True)
                    continue
                idx_c = None
                if len(f_c) > 3:
                    try:
                        idx_c = int(f_c[3])
                    except ValueError:
                        print("[feworld]    !mapcal drop: IDX must be a number",
                              flush=True)
                        continue
                gone_c = mapcal.mapcal_drop(args, f_c[2], idx_c)
                print("[feworld]    !mapcal: dropped %d anchor(s) from %s -- "
                      "%s" % (gone_c, f_c[2],
                              mapcal._mapcal_line(f_c[2])), flush=True)
                continue
            # KEY: THE ANCHOR THAT DOES NOT NEED YOU TO IDENTIFY ANYTHING.
            # Dragging a door onto its painted self assumes you can TELL which
            # painted blob is that door -- and on the four misaligned halves
            # the icon classifier itself could not, so neither can a person.
            # Your own position has none of that problem: the client reports
            # exactly where you are standing, you know where you are standing,
            # and one click says which pixel that is. One anchor fixes the
            # offset, which is the whole of the error on every half measured
            # so far.
            if sub_c == "here":
                pos_c = sess._SESSION.get("cpos")
                if not live_stem:
                    print("[feworld]    !mapcal here: you are not in a capital "
                          "-- there is no minimap to anchor", flush=True)
                    continue
                if not sess._SESSION.get("in_field") or not pos_c:
                    print("[feworld]    !mapcal here: no position reported "
                          "since entering this area -- take one step first",
                          flush=True)
                    continue
                if len(f_c) < 3:
                    print("[feworld]    !mapcal here PX:PY -- the pixel on the "
                          "art where you actually are", flush=True)
                    continue
                try:
                    pxs_h, _, pys_h = f_c[2].partition(":")
                    px_h, py_h = float(pxs_h), float(pys_h)
                except ValueError:
                    print("[feworld]    !mapcal here wants PX:PY", flush=True)
                    continue
                if not (0 <= px_h <= fegamedata.MINIMAP_SIZE
                        and 0 <= py_h <= fegamedata.MINIMAP_SIZE):
                    print("[feworld]    !mapcal here: pixel (%.1f, %.1f) is "
                          "off a %d px map"
                          % (px_h, py_h, fegamedata.MINIMAP_SIZE), flush=True)
                    continue
                was_h = mapcal.mapcal_proj(live_stem)
                old_px = pos_c[0] / was_h["ax"] + was_h["cx"]
                old_py = pos_c[2] / was_h["az"] + was_h["cz"]
                mapcal.mapcal_add(args, live_stem, pos_c[0], pos_c[2], px_h, py_h,
                           "stood here")
                print("[feworld]    !mapcal here: you are at world (%.1f, "
                      "%.1f); the map was drawing that at pixel (%.1f, %.1f) "
                      "and you say (%.1f, %.1f) -- a shift of (%.1f, %.1f) px"
                      % (pos_c[0], pos_c[2], old_px, old_py, px_h, py_h,
                         px_h - old_px, py_h - old_py), flush=True)
                print("[feworld]    !mapcal: %s" % mapcal._mapcal_line(live_stem),
                      flush=True)
                continue
            stem_c = sub_c or live_stem
            if not stem_c:
                print("[feworld]    !mapcal: no map half -- you are not in a "
                      "capital, so name one: `!mapcal map00_01`", flush=True)
                continue
            if len(f_c) >= 4:
                try:
                    wx_c, _, wz_c = f_c[2].partition(":")
                    pxs_c, _, pys_c = f_c[3].partition(":")
                    wx_c, wz_c = float(wx_c), float(wz_c)
                    px_c, py_c = float(pxs_c), float(pys_c)
                except ValueError:
                    print("[feworld]    !mapcal STEM X:Z PX:PY [NOTE] -- world "
                          "position first, then the pixel it is painted at",
                          flush=True)
                    continue
                if not (0 <= px_c <= fegamedata.MINIMAP_SIZE
                        and 0 <= py_c <= fegamedata.MINIMAP_SIZE):
                    print("[feworld]    !mapcal: pixel (%.1f, %.1f) is off a "
                          "%d px map -- an anchor outside the art can only be "
                          "a mis-drag" % (px_c, py_c, fegamedata.MINIMAP_SIZE),
                          flush=True)
                    continue
                mapcal.mapcal_add(args, stem_c, wx_c, wz_c, px_c, py_c,
                           " ".join(f_c[4:]))
                print("[feworld]    !mapcal: %s anchored world (%.1f, %.1f) to "
                      "pixel (%.1f, %.1f)"
                      % (stem_c, wx_c, wz_c, px_c, py_c), flush=True)
            print("[feworld]    !mapcal: %s" % mapcal._mapcal_line(stem_c), flush=True)
            for i_c, r_c in enumerate(mapcal.mapcal_residuals(stem_c)):
                print("[feworld]                 [%d] world(%.1f, %.1f) -> "
                      "pixel(%.1f, %.1f)  off by %.1f px%s"
                      % (i_c, r_c["x"], r_c["z"], r_c["px"], r_c["py"],
                         r_c["d"],
                         "  " + r_c["note"] if r_c["note"] else ""), flush=True)
            continue
        # `!moveto AREA:IDX X:Z [YAW]` -- move one placed NPC by INDEX.
        #
        # KEY: NOT the same command as `!move`. `!move` picks the NPC nearest to
        # where you are STANDING, which is exactly right when you are in game
        # walking the town -- and useless for a drag on a map, which has no
        # standing and may be looking at the half you are not in.
        if line.startswith("!moveto "):
            f_m = line[8:].strip().split()
            if len(f_m) < 2:
                print("[feworld]    !moveto AREA:IDX X:Z [YAW] -- IDX is the "
                      "row number `!town list npcs` prints", flush=True)
                continue
            try:
                a_s, _, i_s = f_m[0].partition(":")
                gid_m, idx_m = int(a_s, 0), int(i_s)
                mx_s, _, mz_s = f_m[1].partition(":")
                mx_m, mz_m = float(mx_s), float(mz_s)
                yaw_m = float(f_m[2]) if len(f_m) > 2 else None
            except ValueError:
                print("[feworld]    !moveto wants AREA:IDX X:Z [YAW]",
                      flush=True)
                continue
            rows_m = town.town_get(args, gid_m, "npcs")
            if not 0 <= idx_m < len(rows_m):
                print("[feworld]    !moveto: area %s has %d placed NPC(s), so "
                      "there is no row %d" % (gid_m, len(rows_m), idx_m),
                      flush=True)
                continue
            r_m = rows_m[idx_m]
            # The map is a plan view: it gives x and z and CANNOT give a
            # height. Keep the height the row already has -- somebody
            # established it -- and fall back to the area's spawn row only when
            # the row has none. Refuse rather than invent one.
            try:
                hy_m = float(r_m.get("y"))
            except (TypeError, ValueError):
                rows_s = spawns.spawn_rows_for(gid_m)
                if not rows_s:
                    print("[feworld]    !moveto: that row has no height and "
                          "area %s has no spawn row to borrow one from. Stand "
                          "on the ground there once and `!spawn`." % gid_m,
                          flush=True)
                    continue
                hy_m = rows_s[0]["y"]
            upd_m = {"x": round(mx_m, 1), "y": round(hy_m, 2),
                     "z": round(mz_m, 1)}
            if yaw_m is not None:
                upd_m["yaw"] = yaw_m
            town.town_set(args, gid_m, "npcs", idx_m, upd_m)
            who_m = r_m.get("name") or "model %s" % r_m.get("model")
            print("[feworld]    !moveto: %s [%d] in area %s -> (%.1f, %.2f, "
                  "%.1f)%s -- re-enter the area to see it"
                  % (who_m, idx_m, gid_m, mx_m, hy_m, mz_m,
                     "" if yaw_m is None else " facing %g°" % yaw_m),
                  flush=True)
            continue
        # `!arrive` -- where a door PUTS YOU DOWN, per destination portal.
        if line.strip() == "!arrive" or line.startswith("!arrive "):
            f_a = line.split()
            if len(f_a) > 1 and f_a[1] in ("drop", "reset"):
                p_a = None
                if len(f_a) > 2:
                    try:
                        p_a = int(f_a[2], 0)
                    except ValueError:
                        print("[feworld]    !arrive drop [PORTAL]", flush=True)
                        continue
                gone_a = doors.doorarr_drop(args, p_a)
                print("[feworld]    !arrive: dropped %d override(s); those "
                      "portals hand out SE's own arrival again" % gone_a,
                      flush=True)
                continue
            # `!arrive PORTAL ...` -- PORTAL is the door you walk THROUGH, and
            # the spot is where it puts you down, in its DESTINATION area.
            # (Before 2026-09-11 it was keyed by the door you came out next to,
            # because dat_door_for read the portal table backwards.)
            #
            # KEY: `!arrive PORTAL here` sets it from where you are STANDING:
            # walk through, walk to where you should have appeared, press it.
            # That needs no map and no drag, and a spot somebody is standing on
            # has ground under it by construction.
            if len(f_a) >= 3 and f_a[2] == "here":
                try:
                    p_a = int(f_a[1], 0)
                except ValueError:
                    print("[feworld]    !arrive PORTAL here", flush=True)
                    continue
                pr_a = fegamedata.portal(p_a)
                dd_a = fegamedata.portal(pr_a["dest"]) if pr_a else None
                pos_a = sess._SESSION.get("cpos")
                if pr_a is None or dd_a is None:
                    print("[feworld]    !arrive: portal %d is not a door in the "
                          "shipped table" % p_a, flush=True)
                    continue
                if not sess._SESSION.get("in_field") or not pos_a:
                    print("[feworld]    !arrive here: no position reported "
                          "since entering this area -- take one step first",
                          flush=True)
                    continue
                if sess._SESSION.get("field") != dd_a["area"]:
                    print("[feworld]    !arrive here: portal %d leads INTO area "
                          "%d and you are in area %s -- walk through it first, "
                          "then stand where it should put you down"
                          % (p_a, dd_a["area"], sess._SESSION.get("field")),
                          flush=True)
                    continue
                f_a = ["!arrive", str(p_a), "%.1f:%.1f" % (pos_a[0], pos_a[2])]
            if len(f_a) >= 3:
                try:
                    p_a = int(f_a[1], 0)
                    ax_s, _, az_s = f_a[2].partition(":")
                    ax_a, az_a = float(ax_s), float(az_s)
                except ValueError:
                    print("[feworld]    !arrive PORTAL X:Z -- PORTAL is the "
                          "door you walk THROUGH; X:Z is where it puts you "
                          "down on the other side", flush=True)
                    continue
                # one set of rules for the command and the editor
                ok_a, msg_a = doors.arrive_set(args, p_a, ax_a, az_a)
                print("[feworld]    !arrive: %s" % msg_a, flush=True)
                continue
            with doors._DOORARR_LOCK:
                snap_a = dict(doors._DOORARR)
            if not snap_a:
                print("[feworld]    !arrive: no overrides -- every door hands "
                      "out the shipped arrival. `!arrive PORTAL X:Z` moves "
                      "one.", flush=True)
            for p_a in sorted(snap_a):
                pr_a = fegamedata.portal(p_a)
                dd_a = fegamedata.portal(pr_a["dest"]) if pr_a else None
                print("[feworld]    !arrive: through portal %d%s -> (%.1f, %.1f)"
                      % (p_a, "" if dd_a is None else " (area %d -> %d)"
                         % (pr_a["area"], dd_a["area"]),
                         snap_a[p_a]["x"], snap_a[p_a]["z"]), flush=True)
            continue
        if line.startswith("!place ") or line.startswith("!spawnat "):
            verb = "place" if line.startswith("!place ") else "spawnat"
            rest_p = line[len(verb) + 2:].strip()
            gid_p = sess._SESSION.get("field")
            if gid_p is None:
                print("[feworld]    !%s: no area -- the panel sends this for "
                      "the field you are in, and there is not one" % verb,
                      flush=True)
                continue
            coord, _, tail_p = rest_p.partition(" ")
            try:
                cx_s, _, cz_s = coord.partition(":")
                px_, pz_ = float(cx_s), float(cz_s)
            except ValueError:
                print("[feworld]    !%s wants X:Z first, in world units "
                      "(the panel's hover readout is exactly this)" % verb,
                      flush=True)
                continue
            rows_h = spawns.spawn_rows_for(int(gid_p))
            if not rows_h:
                print("[feworld]    !%s: area %s has no spawn row, so there is "
                      "no height to place at -- the map is a plan view and "
                      "carries x and z only. Stand on the ground there once "
                      "and `!spawn`, and everything after that can be clicked."
                      % (verb, gid_p), flush=True)
                continue
            hy = rows_h[0]["y"]
            if verb == "spawnat":
                tag_p = tail_p.strip()
                was = spawns.spawn_add(args, gid_p, (px_, hy, pz_), tag_p)
                print("[feworld]    !spawnat: area %s%s arrives at "
                      "(%.1f, %.2f, %.1f)%s"
                      % (gid_p, "" if not tag_p else " [%s]" % tag_p,
                         px_, hy, pz_,
                         "" if was is None else " (was %.1f, %.2f, %.1f)"
                         % (was["x"], was["y"], was["z"])), flush=True)
                continue
            f_p = tail_p.strip().split(":")
            if not f_p or not f_p[0]:
                print("[feworld]    !place wants X:Z NAME[:KIND[:LEVEL[:YAW]]]",
                      flush=True)
                continue
            role_p = next((r for r in fegamedata.capital_roster(int(gid_p))
                           if r["name"] == f_p[0]), None)
            if role_p is None:
                print("[feworld]    !place: %r is not one of area %s's twenty "
                      "roles. `!expect` lists them." % (f_p[0], gid_p),
                      flush=True)
                continue
            try:
                kind_p = int(f_p[1], 0) if len(f_p) > 1 and f_p[1] else 1
                lvl_p = int(f_p[2], 0) if len(f_p) > 2 and f_p[2] else 1
                yaw_p = float(f_p[3]) if len(f_p) > 3 and f_p[3] else 0.0
            except ValueError:
                print("[feworld]    !place: KIND, LEVEL and YAW must be "
                      "numbers", flush=True)
                continue
            town.town_add(args, int(gid_p), "npcs",
                     {"model": role_p["modeltype"], "kind": kind_p,
                      "level": lvl_p, "x": round(px_, 1), "y": round(hy, 2),
                      "z": round(pz_, 1), "yaw": yaw_p,
                      "name": role_p["name"], "script": role_p["script"]})
            print("[feworld]    !place: %s at (%.1f, %.2f, %.1f) facing %g° "
                  "-- re-enter the area to see it"
                  % (role_p["name"], px_, hy, pz_, yaw_p), flush=True)
            continue
        if line.startswith("!npc "):
            # An optional trailing `event=...` so a talking NPC is ONE command
            # while the admin is standing on the spot, instead of !npc, then
            # !town list to find the index, then !town set. The event value may
            # contain spaces and colons (`event=say:Halt there;goto:15`), so it
            # is cut off the line BEFORE the colon fields are parsed.
            rest_np, _, ev_spec = line[5:].strip().partition(" event=")
            ev_spec = ev_spec.strip()
            f = rest_np.strip().split(":")
            named = None
            try:
                if f[0] and not f[0].lstrip("-").isdigit() and not f[0].lower().startswith("0x"):
                    # by dat.pak TYPE NAME (case-insensitive): Heavy_Armor_Shop,
                    # Item_Shop, Inn_Master... -- several keepers share one
                    # model, and only the name carries the right script id
                    hit = [(t, v) for t, v in fegamedata.npc_types().items()
                           if v["name"].lower() == f[0].lower()]
                    if not hit:
                        print("[feworld]    !npc: no dat.pak npc type named %r"
                              % f[0], flush=True)
                        continue
                    # WARNING: PREFER THIS CAPITAL'S OWN ROLE. Every capital has a
                    # Warrior_Weapon_Shop, an Item_Shop, ... under ITS OWN
                    # script id, and the lowest type id is always Beinwatt's
                    # (21xx). Taking sorted(hit)[0] put Beinwatt's scripts in
                    # other capitals: Azurwood's Item_Shop ran 2111 instead of
                    # 2311, and the 09-06 "castle guard" in 39 carried 2102.
                    own = {r["script"] for r in fegamedata.capital_roster(
                        sess._SESSION.get("field") or 0)}
                    mine = [h for h in hit if h[1].get("script") in own]
                    named = sorted(mine or hit)[0]
                    mtype = named[1]["modeltype"]
                else:
                    mtype = int(f[0], 0)
                kind = int(f[1], 0) if len(f) > 1 and f[1] else 1
                level = int(f[2], 0) if len(f) > 2 and f[2] else 1
                yaw = float(f[3]) if len(f) > 3 and f[3] else 0.0
            except ValueError:
                print("[feworld]    !npc wants MODELTYPE|TYPE_NAME[:KIND[:LEVEL"
                      "[:YAW]]] (yaw in degrees: 0=+Z 90=+X 180=-Z 270=-X)",
                      flush=True)
                continue
            pos = sess._SESSION.get("cpos")
            if mtype not in entities.MONSTER_MODELTYPE_IDS:
                print("[feworld]    !npc: modeltype %d is not in "
                      "data_NPC_ModelType.dat" % mtype, flush=True)
            elif not sess._SESSION.get("in_field") or not pos:
                print("[feworld]    !npc: %s. The reported position is dropped "
                      "whenever the area changes, because placing at a stale "
                      "one puts the NPC where the player USED to be -- in a "
                      "room, that is an NPC nobody can reach and a room has no "
                      "other way out. Take one step and run this again."
                      % ("not in a field" if not sess._SESSION.get("in_field")
                         else "no position reported since entering this area"),
                      flush=True)
            else:
                n = sess._SESSION.get("npc_here_n", 0)
                sess._SESSION["npc_here_n"] = n + 1
                obj = int(getattr(args, "monster_base", 400)) + 100 + n
                x, y, z = pos
                # the NAME and SCRIPT from dat.pak's type table, for the log
                # and the town file (the talk chain keys on the script id)
                tinfo = named or next(
                    ((t, v) for t, v in sorted(fegamedata.npc_types().items())
                     if v["modeltype"] == mtype and 228 <= mtype <= 274), None)
                name = tinfo[1]["name"] if tinfo else ""
                script = tinfo[1]["script"] if tinfo else 0
                town.npc_send(conn, outbound, mode, be, args, obj, mtype, kind, level,
                         x, y, z, why="(!npc at the player's position%s)"
                         % (", %s script %d" % (name, script) if name else ""),
                         yaw=yaw)
                row = {"model": mtype, "kind": kind, "level": level,
                       "x": round(x, 2), "y": round(y, 2), "z": round(z, 2),
                       "yaw": yaw, "name": name, "script": script}
                if ev_spec:
                    row["event"] = ev_spec
                    # Say where each goto actually LANDS rather than a fixed
                    # example: `goto:N` is a ROOM when N is in the client's
                    # switch window and a FIELD otherwise, and a line that
                    # always named room 15 was wrong for every other target.
                    where = ", ".join(
                        ("room %d (%s)" % (t, zones.room_label(t))
                         if t in zones.ROOM_INDEX_WINDOW else "field/group %d" % t)
                        for t in [int(v, 0) for k, _, v in
                                  (pp.strip().partition(":")
                                   for pp in str(ev_spec).split(";"))
                                  if k == "goto" and v.strip().lstrip("-").isdigit()])
                    print("[feworld]    !npc event=%r -- this row's talk script "
                          "is the OVERRIDE, not the dat.pak type's%s"
                          % (ev_spec,
                             ("; its goto lands in %s" % where) if where
                             else " (no goto: the conversation just ends)"),
                          flush=True)
                town.town_add(args, town.town_key(), "npcs", row)
            continue
        # `!door N` -- change where an unmapped door leads, live.
        # `!goto AREA` -- send the player somewhere NOW: a group id (39 = the
        # capital), or `room:N` for a room. The server-side exit for a room
        # that has no exit of its own (live 2026-09-05: the arms shop).
        # `!send ID[:HEXBODY[:UNIT]]` -- one inner message, verbatim: the
        # probe lever for a hung client (which release message frees it?).
        # ID and UNIT in hex or decimal; HEXBODY raw bytes, default empty.
        if line.startswith("!send "):
            parts = line[6:].strip().split(":")
            try:
                mid = int(parts[0], 16 if not parts[0].startswith("0x") else 0) \
                    if len(parts[0]) == 4 else int(parts[0], 0)
                body = bytes.fromhex(parts[1]) if len(parts) > 1 and parts[1] else b""
                unit = int(parts[2], 0) if len(parts) > 2 and parts[2] else wire.unit_id_of(args)
            except ValueError:
                print("[feworld]    !send wants ID[:HEXBODY[:UNIT]]", flush=True)
                continue
            wire.send(conn, outbound, wire.inner_msg(mid, body, unit), mode, be,
                 args.seq_mode == "echo", args.world_prefix)
            print("[feworld] -> 0x30 inner 0x%04X (!send probe, %d body bytes, "
                  "unit %d)" % (mid, len(body), unit), flush=True)
            continue
        # `!land KIND AREA [OTHER]` -- push a war-outcome notify and, for the
        # two that change the board, MOVE THE TERRITORY with it. This is the
        # admin handle on conquest until a war actually decides one:
        # `!land deprive 18 1` gives area 18 to the player's nation, takes it
        # from nation 1, tells the client, and persists it -- after which the
        # client's own frontier rule should let the player walk into 21.
        # `!spawn` -- make where you are standing this area's arrival point.
        # The same shape as `!npc` / `!door`: the client reports its own
        # position continuously, so this records a MEASURED spot on the ground
        # rather than a number somebody typed. `!spawn show` prints the table,
        # `!spawn clear` drops this area's row back to --spawn-pos.
        # `!face DEG` / `!move` / `!nudge DX:DZ` -- fix an NPC you already put
        # down, without deleting and re-placing it.
        #
        # KEY: THEY ALL ACT ON THE NEAREST ONE, because that is the gesture the
        # admin is already making: you walk up to the keeper who is facing
        # the wrong way. Naming an index would mean `!town list`, counting rows,
        # and hoping the order is what you think -- three steps to do what
        # standing next to somebody already says.
        if (line.startswith("!face") or line.startswith("!move")
                or line.startswith("!nudge") or line.startswith("!remove")):
            verb = line.split()[0][1:]
            rest = line[len(verb) + 1:].strip()
            fld, pos = sess._SESSION.get("field"), sess._SESSION.get("cpos")
            if fld is None or not sess._SESSION.get("in_field") or not pos:
                print("[feworld]    !%s: %s" % (verb,
                      "not in a field" if not sess._SESSION.get("in_field")
                      else "no position reported since entering this area -- "
                           "take one step"), flush=True)
                continue
            rows_ = town.town_get(args, int(fld), "npcs")
            if not rows_:
                print("[feworld]    !%s: no NPC placed in area %s yet"
                      % (verb, fld), flush=True)
                continue
            best, bd = None, None
            for i, r in enumerate(rows_):
                try:
                    d = ((float(r["x"]) - pos[0]) ** 2
                         + (float(r["z"]) - pos[2]) ** 2) ** 0.5
                except (KeyError, TypeError, ValueError):
                    continue
                if bd is None or d < bd:
                    best, bd = i, d
            if best is None:
                print("[feworld]    !%s: no NPC row has a position" % verb,
                      flush=True)
                continue
            r = rows_[best]
            who = r.get("name") or "model %s" % r.get("model")
            if verb == "remove":
                gone = town.town_del(args, int(fld), "npcs", best)
                if gone is None:
                    print("[feworld]    !remove: could not delete that row",
                          flush=True)
                    continue
                # If it was one of the shipped roles, say so -- the admin's
                # next question is always "is it back on the list?", and the
                # answer is yes, because the list is a diff and nothing caches.
                back = ""
                for role in fegamedata.capital_roster(int(fld)):
                    if (role["script"] == int(gone.get("script") or 0)
                            or role["name"] == gone.get("name")):
                        back = " -- %s is placeable again" % role["name"]
                        break
                print("[feworld]    !remove: deleted %s at (%.1f, %.1f), "
                      "%.1fu away%s"
                      % (who, float(gone.get("x", 0)), float(gone.get("z", 0)),
                         bd, back), flush=True)
            elif verb == "face":
                try:
                    yaw = float(rest) if rest else 0.0
                except ValueError:
                    print("[feworld]    !face wants degrees (0=+Z 90=+X "
                          "180=-Z 270=-X)", flush=True)
                    continue
                town.town_set(args, int(fld), "npcs", best, {"yaw": yaw})
                print("[feworld]    !face: %s (%.1fu away) now faces %g°"
                      % (who, bd, yaw), flush=True)
            elif verb == "move":
                town.town_set(args, int(fld), "npcs", best,
                         {"x": round(pos[0], 2), "y": round(pos[1], 2),
                          "z": round(pos[2], 2)})
                print("[feworld]    !move: %s (%.1fu away) is now where you are "
                      "standing (%.1f, %.2f, %.1f)"
                      % (who, bd, pos[0], pos[1], pos[2]), flush=True)
            else:
                try:
                    dx, _, dz = rest.partition(":")
                    dx, dz = float(dx), float(dz)
                except ValueError:
                    print("[feworld]    !nudge wants DX:DZ in world units, "
                          "e.g. `!nudge 1:0`", flush=True)
                    continue
                nx = round(float(r.get("x", 0)) + dx, 2)
                nz = round(float(r.get("z", 0)) + dz, 2)
                town.town_set(args, int(fld), "npcs", best, {"x": nx, "z": nz})
                print("[feworld]    !nudge: %s moved by (%g, %g) to (%.1f, %.1f)"
                      % (who, dx, dz, nx, nz), flush=True)
            # the change reaches the client live (town_live_refresh)
            continue
        # `!dress` -- place every shop this capital has a RECOVERED position
        # for, in one go. See fegamedata.shop_icons: the minimaps have the shop
        # icons painted in where SE put them, so the positions survived even
        # though the server data did not.
        #
        # Reversible by construction: every row it writes is an ordinary town
        # NPC, so `!remove` takes one back and `!town undo npcs` unwinds them.
        # It never overwrites a role that is already placed, so running it
        # twice is a no-op and running it after hand-placing a few fills in
        # the rest.
        if line.startswith("!dress"):
            rest = line[6:].strip()
            try:
                gid = int(rest, 0) if rest else sess._SESSION.get("field")
            except ValueError:
                print("[feworld]    !dress wants an area id, or nothing for "
                      "the one you are in", flush=True)
                continue
            if gid is None:
                print("[feworld]    !dress: not in a field", flush=True)
                continue
            icons = [r for r in fegamedata.shop_icons(int(gid)) if r["role"]]
            if not icons:
                print("[feworld]    !dress: no trusted shop positions for area "
                      "%s. Either the minimap classifier could not agree with "
                      "itself there (a duplicated role means scenery landed in "
                      "the same bucket as a shop), or it is not a capital."
                      % gid, flush=True)
                continue
            # KEY: THE HEIGHT IS THE ONE THING THE ART DOES NOT CARRY. The
            # minimap is a plan view: it gives x and z and says nothing about
            # y, and the capitals have no shipped map height either (their
            # hmap is 101..105, which ship no MapSpot). So take it from the
            # area's own spawn row -- somewhere a player has actually stood -- and
            # refuse rather than guess, because a keeper under the floor looks
            # exactly like a keeper that was never placed.
            rows_sp = spawns.spawn_rows_for(int(gid))
            if not rows_sp:
                print("[feworld]    !dress: area %s has no spawn row, so there "
                      "is no height to place at -- the minimap is a plan view "
                      "and carries x and z only. Stand on the ground there and "
                      "`!spawn` first." % gid, flush=True)
                continue
            y = rows_sp[0]["y"]
            have = town.town_get(args, int(gid), "npcs")
            have_s = {int(n.get("script") or 0) for n in have}
            have_n = {str(n.get("name") or "") for n in have}
            roster = {r["name"]: r for r in fegamedata.capital_roster(int(gid))}
            put, skip = [], []
            for ic in icons:
                role = roster.get(ic["role"])
                if role is None:
                    continue
                if role["script"] in have_s or role["name"] in have_n:
                    skip.append(role["name"])
                    continue
                town.town_add(args, int(gid), "npcs",
                         {"model": role["modeltype"], "kind": 1, "level": 1,
                          "x": round(ic["x"], 1), "y": round(y, 2),
                          "z": round(ic["z"], 1), "yaw": 0,
                          "name": role["name"], "script": role["script"]})
                put.append((role["name"], ic["x"], ic["z"]))
            print("[feworld]    !dress area %s: placed %d, already there %d, "
                  "at height %.2f (from its spawn row)"
                  % (gid, len(put), len(skip), y), flush=True)
            for nm, x_, z_ in put:
                print("[feworld]        %-22s (%7.1f, %7.1f)" % (nm, x_, z_),
                      flush=True)
            print("[feworld]                 WARNING: positions are the minimap's, good "
                  "to a couple of metres, and every one faces +Z. Walk them, "
                  "`!face` and `!nudge` what is wrong, `!remove` what is not a "
                  "shop. Re-enter the area to see them.", flush=True)
            continue
        # `!expect [AREA]` -- what SHIPS for this area against what is placed.
        #
        # The point of it: dat.pak says WHO belongs in a capital (twenty named
        # roles, keyed by script id, identical in all five) and WHERE the
        # monsters and doors go on a field -- but never where a person stands.
        # So "is this town finished?" is answerable, and was not.
        if line.startswith("!expect"):
            rest = line[7:].strip()
            try:
                gid = int(rest, 0) if rest else sess._SESSION.get("field")
            except ValueError:
                print("[feworld]    !expect wants an area id, or nothing for "
                      "the one you are in", flush=True)
                continue
            if gid is None:
                print("[feworld]    !expect: not in a field -- give it an area "
                      "id", flush=True)
                continue
            e = fegamedata.area_expected(int(gid))
            if e is None:
                print("[feworld]    !expect: area %s is not one of the 95"
                      % gid, flush=True)
                continue
            placed = town.town_get(args, int(gid), "npcs")
            have_s = {int(n.get("script") or 0) for n in placed}
            have_n = {str(n.get("name") or "") for n in placed}
            print("[feworld]    === area %d %s === map%02d, ground %s"
                  % (e["area"], e["name"], e["hmap"] or 0,
                     e["ground"] if e["ground"] is not None else "unknown"),
                  flush=True)
            if e["roster"]:
                miss = [r for r in e["roster"]
                        if r["script"] not in have_s and r["name"] not in have_n]
                print("[feworld]      NPCs   %d of %d roles placed"
                      % (len(e["roster"]) - len(miss), len(e["roster"])),
                      flush=True)
                for r in miss:
                    print("[feworld]        missing  !npc %-22s   (script %d, "
                          "model %d)" % (r["name"], r["script"], r["modeltype"]),
                          flush=True)
            if e["doors"]:
                mapped = town.town_get(args, int(gid), "doors")
                print("[feworld]      Doors  %d shipped, %d mapped by hand"
                      % (len(e["doors"]), len(mapped)), flush=True)
                for d in e["doors"]:
                    known = any(abs(float(m.get("x", 1e9)) - d["gate"][0])
                                <= args.door_radius
                                and abs(float(m.get("z", 1e9)) - d["gate"][1])
                                <= args.door_radius for m in mapped)
                    print("[feworld]        %-7s gate (%7.1f,%7.1f) -> portal "
                          "%d in area %d"
                          % ("mapped" if known else "shipped",
                             d["gate"][0], d["gate"][1], d["dest"],
                             fegamedata.portal(d["dest"])["area"]
                             if fegamedata.portal(d["dest"]) else -1), flush=True)
            if e["monsters"]:
                print("[feworld]      Mobs   %d shipped generator points "
                      "(positions ship -- --spawns serves them)"
                      % len(e["monsters"]), flush=True)
            if e["castle"]:
                print("[feworld]      Castle map%02d grid %s"
                      % (e["hmap"] or 0, e["castle"]), flush=True)
            rows_ = spawns.spawn_rows_for(int(gid))
            print("[feworld]      Spawn  %s"
                  % ("none -- falls back to the map height or --spawn-pos"
                     if not rows_ else
                     ", ".join("%s(%.0f,%.1f,%.0f)" % (r["tag"] or "plain",
                                                       r["x"], r["y"], r["z"])
                               for r in rows_)), flush=True)
            continue
        # `!spawn [TAG]` -- make where you are standing an arrival point for
        # this area. A battlefield has two sides that do not arrive together,
        # so a point can carry a tag ("atk", "def", or a nation id) and the
        # server picks the one that matches. No tag = the area's plain spawn.
        if line.startswith("!spawn"):
            rest = line[6:].strip().split()
            if rest and rest[0] == "show":
                with spawns._SPAWN_LOCK:
                    areas_ = sorted(spawns._SPAWN.items())
                if not areas_:
                    print("[feworld]    !spawn: nothing recorded. Every area "
                          "falls back to its map height (--spawn-height dat) "
                          "or --spawn-pos %s." % (args.spawn_xyz,), flush=True)
                for a_, rows_ in areas_:
                    nm = fegamedata.areas().get(a_, {}).get("name", "?")
                    for r_ in rows_:
                        print("[feworld]    spawn %3d %-20s %-6s (%.1f, %.2f, %.1f)"
                              % (a_, nm, r_["tag"] or "-",
                                 r_["x"], r_["y"], r_["z"]), flush=True)
                continue
            fld = sess._SESSION.get("field")
            if rest and rest[0] == "clear":
                if fld is None:
                    print("[feworld]    !spawn clear: not in a field", flush=True)
                    continue
                tag_ = rest[1] if len(rest) > 1 else None
                gone = spawns.spawn_drop(args, fld, tag_)
                print("[feworld]    !spawn clear: dropped %d row(s) from area %s%s"
                      % (gone, fld, "" if tag_ is None else " tagged %r" % tag_),
                      flush=True)
                continue
            tag = rest[0] if rest else ""
            pos = sess._SESSION.get("cpos")
            if fld is None or not sess._SESSION.get("in_field") or not pos:
                print("[feworld]    !spawn: %s. The position is dropped "
                      "whenever the area changes -- take one step and run it "
                      "again."
                      % ("not in a field" if not sess._SESSION.get("in_field")
                         else "no position reported since entering this area"),
                      flush=True)
                continue
            was = spawns.spawn_add(args, fld, pos, tag)
            print("[feworld]    !spawn: area %s (%s)%s now arrives at "
                  "(%.1f, %.2f, %.1f)%s -- persisted"
                  % (fld, fegamedata.areas().get(int(fld), {}).get("name", "?"),
                     "" if not tag else " [%s]" % tag,
                     pos[0], pos[1], pos[2],
                     "" if was is None else
                     " (was %.1f, %.2f, %.1f)" % (was["x"], was["y"], was["z"])),
                  flush=True)
            n_here = len(spawns.spawn_rows_for(fld))
            if n_here > 1:
                print("[feworld]                 area %s now has %d points: %s"
                      % (fld, n_here,
                         ", ".join(r["tag"] or "(plain)"
                                   for r in spawns.spawn_rows_for(fld))), flush=True)
            continue
        # `!link` -- pair BOTH sides of a doorway by walking it.
        #
        #   1. stand on the door, walk into it (so we learn its gate), step
        #      back to where you want to ARRIVE from the far side, `!link`
        #   2. go through to the other side, do the same there, `!link`
        #
        # The second call writes TWO rows: this door -> that area at that
        # arrival point, and that door -> this area at this one. Reciprocal,
        # the way all 64 of fet_area_portal's own records are, so walking back
        # returns you to the spot you left from.
        if line.startswith("!link"):
            rest = line[5:].strip().split()
            if rest and rest[0] in ("cancel", "clear"):
                sess._SESSION.pop("link_a", None)
                print("[feworld]    !link: pending side dropped", flush=True)
                continue
            if rest and rest[0] == "show":
                a_ = sess._SESSION.get("link_a")
                print("[feworld]    !link: %s"
                      % ("nothing pending -- `!link` on the first side to start"
                         if not a_ else
                         "side A held: door (%.1f, %.1f) in area %d, arrive "
                         "(%.1f, %.2f, %.1f). Walk to the other side and "
                         "`!link` again."
                         % (a_["dx"], a_["dz"], a_["gid"],
                            a_["ax"], a_["ay"], a_["az"])), flush=True)
                continue
            # KEY: THE CHICKEN AND EGG. You cannot walk to the far side of a
            # door that does not lead anywhere yet -- which is every door
            # before it is linked. `!link DEST` closes that: it holds this
            # side and then WARPS you to DEST, so the second half of the pair
            # is one command away instead of "now go find your own way there".
            dest_hint = None
            if rest and rest[0] not in ("show", "cancel", "clear"):
                try:
                    dest_hint = int(rest[0], 0)
                except ValueError:
                    print("[feworld]    !link wants an AREA to warp to, or "
                          "nothing at all if you can already reach the far "
                          "side. Got %r." % rest[0], flush=True)
                    continue
                if dest_hint not in fegamedata.areas():
                    print("[feworld]    !link %d: not one of the 95 areas -- "
                          "the client could not resolve it." % dest_hint,
                          flush=True)
                    continue
            last = sess._SESSION.get("last_door")
            pos = sess._SESSION.get("cpos")
            if not last:
                print("[feworld]    !link: no door touched yet in this area. "
                      "Walk into the doorway first -- that is how we learn "
                      "where it is; the client reports the gate itself in "
                      "0x20AC.", flush=True)
                continue
            if not pos or not sess._SESSION.get("in_field"):
                print("[feworld]    !link: no position reported since entering "
                      "this area -- take one step and run it again.", flush=True)
                continue
            gid, dx, dz = last
            side = {"gid": int(gid), "dx": float(dx), "dz": float(dz),
                    "ax": float(pos[0]), "ay": float(pos[1]), "az": float(pos[2])}
            a = sess._SESSION.get("link_a")
            if a is None:
                sess._SESSION["link_a"] = side
                print("[feworld]    !link: side A held -- door (%.1f, %.1f) in "
                      "area %d, arriving (%.1f, %.2f, %.1f)."
                      % (side["dx"], side["dz"], side["gid"],
                         side["ax"], side["ay"], side["az"]), flush=True)
                if dest_hint is not None:
                    print("[feworld]                 warping you to area %d "
                          "(%s). Walk into the door on that side, stand where "
                          "you want to come out, and `!link` again."
                          % (dest_hint,
                             fegamedata.areas()[dest_hint].get("name", "?")),
                          flush=True)
                    events.goto_push(conn, outbound, mode, be, args, dest_hint)
                else:
                    print("[feworld]                 Now get to the other side "
                          "-- `!link cancel` to drop this, or re-run as "
                          "`!link AREA` and it will warp you there.", flush=True)
                continue
            if a["gid"] == side["gid"] and abs(a["dx"] - side["dx"]) <= args.door_radius \
                    and abs(a["dz"] - side["dz"]) <= args.door_radius:
                print("[feworld]    !link: that is the SAME door as side A "
                      "(area %d, within --door-radius). A door that leads to "
                      "itself is not a link -- `!link cancel` and start again "
                      "on the far side." % a["gid"], flush=True)
                continue
            for src, dst in ((a, side), (side, a)):
                town.town_add(args, src["gid"], "doors",
                         {"x": round(src["dx"], 1), "z": round(src["dz"], 1),
                          "dest_area": dst["gid"],
                          "dest_x": round(dst["ax"], 2),
                          "dest_y": round(dst["ay"], 2),
                          "dest_z": round(dst["az"], 2)})
            sess._SESSION.pop("link_a", None)
            print("[feworld]    !link: LINKED both ways and persisted --\n"
                  "                 area %d door (%.1f, %.1f) -> area %d at "
                  "(%.1f, %.2f, %.1f)\n"
                  "                 area %d door (%.1f, %.1f) -> area %d at "
                  "(%.1f, %.2f, %.1f)"
                  % (a["gid"], a["dx"], a["dz"], side["gid"],
                     side["ax"], side["ay"], side["az"],
                     side["gid"], side["dx"], side["dz"], a["gid"],
                     a["ax"], a["ay"], a["az"]), flush=True)
            continue
        if line.startswith("!door "):
            rest = line[6:].strip()
            try:
                if rest.startswith("default "):
                    args.door_default = int(rest[8:].strip(), 0)
                    print("[feworld]    !door -> unmapped doors now open room %d "
                          "(%s)" % (args.door_default,
                                    zones.room_label(args.door_default)), flush=True)
                    continue
                room = int(rest, 0)
            except ValueError:
                print("[feworld]    !door wants a room index (or `default N`)",
                      flush=True)
                continue
            last = sess._SESSION.get("last_door")
            if not last:
                print("[feworld]    !door: no door touched yet this session",
                      flush=True)
                continue
            gid, x, z = last
            town.town_add(args, gid, "doors", {"x": round(x, 1), "z": round(z, 1),
                                          "room": room})
            print("[feworld]    !door -> the door at (%.1f, %.1f) in group %d "
                  "now opens %s" % (x, z, gid,
                  "room %d (%s)" % (room, zones.room_label(room))
                  if room in zones.ROOM_INDEX_WINDOW else
                  "GROUP %d -- a whole field, not a room (%d is outside the "
                  "client's room-switch window)" % (room, room)), flush=True)
            continue
        if line.startswith("!"):
            ctx = extrun.Ctx(conn, outbound, mode, be, args)
            if any(fn(ctx, line) for fn in ext.EXT_GM):
                continue
        gm_command(conn, outbound, mode, be, args, line)
    # typed commands in this batch may have changed the area the player is in
    town.town_live_refresh(conn, outbound, mode, be, args)
