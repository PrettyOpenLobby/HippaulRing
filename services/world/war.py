"""The war cycle on a field: start, notify, truce, peace, the deadline pump."""
import struct
import time
from . import campaignview, clock, death, sess, wire, zones

#: THE WAR-START NOTIFIES -- "the war has begun and you are on this side".
#:
#: KEY: BOTH HAVE EMPTY BODIES, and that was checked rather than assumed. Their
#: arms (0x05054a68 for OFFENSIVE, 0x05054cbb for DIFFENSIVE [SE's spelling])
#: make NO stream-reader call at all: each one logs its name, sets al=1 and
#: jumps to the common exit. Run against the land notifies as a positive
#: control, which DO call the reader at their fourth instruction, so "no reads"
#: is a measurement here and not a failure to find one.
#:
#: They also do nothing else -- no state, no window. Serving them costs nothing
#: and buys one line in the client's own log saying which side it thinks it is
#: on, which is the cheapest confirmation available that a war started.
WAR_START_NOTIFY_IDS = {"offensive": 0x101D, "defensive": 0x101E}


def war_start_notify(conn, outbound, mode, be, args, side):
    """Tell the client the war has started and which side it is on."""
    mid = WAR_START_NOTIFY_IDS.get(side)
    if mid is None:
        return None
    wire.send(conn, outbound, wire.inner_msg(mid, b""), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x%04X MSG_START_WAR_AS_%s_NOTIFY (empty body) -- the "
          "client should log it verbatim" % (mid, "OFFENSIVE" if side ==
                                             "offensive" else "DIFFENSIVE"),
          flush=True)
    return mid


def war_truce(conn, outbound, mode, be, args, dl, kind):
    """0x1016 -- TRUCE (phase 1, the label 休戦終了まで hh:mm:ss).

    Arm 0x051180fb, reached through c3 0x050580ae -> the active screen's
    +0x12c handler (the same route 0x1015 takes, LIVE 08-24). It clears scene
    pending bit 0x80, rebuilds the window (vtable+0x74 with 3,0), reads
    [u32 KEY][u32 HI][u32 LO], sets [view+0x70] = 1 and installs the deadline
    (0x05118167/0x0511817a), logs the client's own 「絶対平和時間 %d ms.」
    ("absolute peace time", 0x52e61c4 -- printing the LOW dword), and puts the
    field manager in state 3 (0x5118760(3, id==0x1029)).

    WARNING: THE KEY IS A GATE WITH A CRASH BEHIND IT. The arm pre-loads its slot with
    0x7FFFFFFF (0x05118125) and at 0x051181e4 skips the lookup when the read
    value still equals it; anything else goes to 0x5005560 (record search by
    key) and the result -- NULL on a miss -- straight into 0x5118880, which
    does `lea edi,[rec+0x10]; repne scasb` unguarded. So 0x7FFFFFFF, the
    client's own sentinel, is the only value that is safe without a matching
    0x3031 record. CHOSEN.
    WARNING: Also: if [player+0x2b4] & 0x1800000 the arm sends 0x2014, a
    screen-registered request nobody has opened. Watch for it in the log."""
    body = struct.pack(">I", 0x7FFFFFFF) + clock.pack_deadline(dl)
    wire.send(conn, outbound, wire.inner_msg(0x1016, body), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    clock.war_arm("truce", dl, kind)
    print("[feworld] -> 0x30 inner 0x1016 TRUCE key=0x7FFFFFFF deadline=%d ms "
          "(%s clock) -- phase [view+0x70] -> 1, label 休戦終了まで; client log "
          "should print 絶対平和時間 %d ms." % (dl, kind, dl & 0xFFFFFFFF),
          flush=True)


def war_peace(conn, outbound, mode, be, args):
    """0x1017 -- back to PEACE. Arm 0x05118202 (shared with 0x1028): clears
    scene pending bit 0x80, rebuilds the window (3,0), field manager state
    (0, 1), [screen+0x61]=[+0x62]=0 -- the 0x2084 heartbeat stops. Reads NO
    body. It does not touch [view+0x70], so the label keeps drawing the last
    phase's text off an expired deadline until the next 0x1018."""
    wire.send(conn, outbound, wire.inner_msg(0x1017, b""), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    sess._SESSION["war_phase"] = "peace"
    sess._SESSION.pop("war_deadline", None)
    print("[feworld] -> 0x30 inner 0x1017 PEACE (header-only, arm 0x05118202) "
          "-- field manager state 0, [screen+0x61]/[+0x62] cleared", flush=True)


def war_notify(conn, outbound, mode, be, args):
    """Push the START_WAR notify that ARMS the war-prepare window.

    This is the message the black war map has been waiting for. The window is
    gated on `[screen+0x62]`, which the war-map screen's per-frame update reads
    at 0x05117c87 and skips the whole "Open War Prepare Window" check unless it
    is set. Exactly one thing sets it: the war-map SCREEN handler (0x05117e00,
    the active screen's vtable slot +0x12c) processing a message whose id lands
    in its switch (`msg[+4]`, range 0x1015..0x1029, table at 0x5118748/0x511871c).

    WARNING: THIS IS NOT A WIRE ID THE WORLD DISPATCHER HANDLES. The world message
    dispatcher (the same one that owns 0x3027/0x3031/0x1000) maps id 0x1018 to
    its DEFAULT arm (byte table 0x5057474 slot 0x18 -> 0x0b -> 0x5057427), which
    just returns `al=0` = "not handled". The caller then forwards the unhandled
    message to the active screen, and THAT is where 0x1018 is consumed. So the
    frame is an ordinary world frame -- `[u32 pad][u16 0x1018][body]` -- and the
    only reason it reaches the war map is that the model layer ignores it.

    WARNING: THE WIRE "MSG_START_WAR_AS_OFFENSIVE/DIFFENSIVE_NOTIFY" ARE A DIFFERENT
    PAIR. The strings at 0x52d5f34/0x52d5e10 are logged by the world dispatcher
    for ids 0x101d/0x101e (0x05054a68 / 0x05054cbb), and in THIS build both those
    arms ONLY log -- they arm nothing. The ids that actually open the window are
    the screen's own 0x1018/0x1019; 0x1029 is the NG/failure arm (0x05118039)
    that CLEARS [screen+0x62], not the defensive side. (An earlier note had
    0x1018/0x1029 as the two sides; the jump table says otherwise.)

    OFFENSIVE (0x1018) vs DEFENSIVE (0x1019): the handler stores `sete on
    ebp==0x1018` at [screen+0x60] (offensive = 1). Both arms read four u32s and
    both arm the window, but they differ in one dangerous way:

        0x1018  sets the war-view object's [+0x70] = 0.
        0x1019  sets [+0x70] = 2.

    Both then call 0x5111630, which is a PURE SETTER -- `[view+0x68]=arg1;
    [view+0x6c]=arg2; ret 8`, four instructions, no branch (corrected
    2026-08-24; the earlier note wrongly had 0x5111630 doing the +0x70 test and
    calling a "scene transition"). The
    +0x70 test lives 0x20 bytes later in a DIFFERENT function, 0x5111650:

        if [view+0x70] == 2 and [0x5336d1c]+0x3c == 0xb:
            0x5111010(this=[view+0x74], [view+0x68], [view+0x6c])

    AND 0x5111010 IS NOT A SCENE TRANSITION EITHER. Opened: it calls the 64-bit
    clock 0x4ffef10 (which returns -1:-1 when [0x533998c] or its +0x980 is
    null), compares edx:eax against the two args as a DEADLINE, subtracts to get
    the remaining time, `fild qword` it and divides by a constant to a 0..1
    fraction stored at [+0x30]/[+0x34], then walks objects via 0x504cb30/
    0x504d1f0 and drives more fractions off their +0x774/+0x778 ratios. It is a
    COUNTDOWN AND GAUGE ANIMATOR -- the war timer and the territory bars.

    So u32_3/u32_4 are the LOW and HIGH halves of a 64-bit war deadline (arg1 is
    compared against the clock's eax, arg2 against its edx). Sending 0,0 is an
    already-expired deadline, which is harmless -- the expiry arm 0x51110b6 just
    skips the fraction and falls through to the gauge walk.

    WARNING: THAT DOWNGRADES THE STATED RISK OF --war defensive: 0x1019 does not drive
    a transition, it arms a timer. It is still the untested arm, but "never send
    it with placeholder zeros" was based on the transition reading and no longer
    stands on that reasoning.

    The four u32s (0x0511843b reads them via the same 0x5045e60/0x5338350 reader
    the force and group records use, so BIG-ENDIAN like everything else here):

        u32_1 -> edi[0]  ([edi+0x50], edi = [0x5336f74], the field/war manager)
        u32_2 -> edi[1]  ([edi+0x54])
        u32_3 -> war-view +0x6c   = HIGH half of the 64-bit war deadline
        u32_4 -> war-view +0x68   = LOW  half of the 64-bit war deadline

    WARNING: THE LAST TWO WERE WRITTEN DOWN BACKWARDS HERE UNTIL 2026-08-26, and
    the correction is off the arms' own stack, not a preference. 0x5045e60
    takes its destination as arg1 (`mov eax,[esp+8]` AFTER its `push esi`), so
    the read that runs FIRST fills the slot pushed LAST:

        05118263  push edx            ; &slot_1c
        05118264  push eax            ; &slot_20   <- top, so the FIRST read
        0511826a  call 0x5045e60      ; u32_3 -> slot_20
        05118271  call 0x5045e60      ; u32_4 -> slot_1c   (ret 4 popped the first)
        0511828f  mov ecx,[esp+0x20]  ; u32_3
        05118293  mov edx,[esp+0x1c]  ; u32_4
        05118297  push ecx / push edx ; edx is on top
        0511829b  call 0x5111630      ; [esp+4]=u32_4 -> +0x68 ; [esp+8]=u32_3 -> +0x6c

    and 0x5111010 compares +0x68 against the clock's eax (low) and +0x6c
    against edx (high). All three arms that install a deadline -- 0x1015
    (0x0511850f), 0x1018 (0x0511823d) and 0x1019 (0x0511843b) -- have the
    identical shape, so THE WIRE CARRIES HIGH FIRST, THEN LOW. For any deadline
    short of ~49 days the high word is 0, i.e. the pair is `0,<milliseconds>`.
    an earlier note recorded this correctly ("first u32 -> the +0x6c slot");
    this docstring did not.

    WARNING: THE "Attack [Count %3d / Max %3d]" READING OF u32_1/u32_2 IS WITHDRAWN
    (2026-08-24). That string (0x52d5fbc) belongs to a DIFFERENT message --
    0x1127, the answer to the window's own 0x2084 poll -- and its counts are
    four u16s that land at mgr+0x5c..0x62, not these u32s. See the 0x2084
    handler below.

    What u32_1/u32_2 actually are, read from the consumer rather than a
    neighbouring string: the war-prep window reads slots 0 and 1 straight back
    (0x051157ff: get-manager 0x50014d0, then 0x5001210(0) and 0x5001210(1)) and
    feeds each to 0x5005560 -- a linear search of the 0xa00-byte record array at
    [0x5336fa8]+0x40 for `[rec+8] == key`. The matched record's nation byte
    [rec+0x1bc] then indexes the per-nation resource table 0x5336fe8 via
    0x5008680(nation, 0/1), and [rec+0x10] is its name. So u32_1/u32_2 are
    RECORD KEYS for the two sides of the war, and a key with no record simply
    draws nothing (0x5005560 returns 0 and the caller skips) -- which is why
    0,0,0,0 arms the window without breaking it.

    u32_3/u32_4 remain undecoded. Arming does not depend on any of the four; it
    depends only on edi being non-null, which it is once the world subsystem is
    up (it is, or the client would not have reached the map).
    """
    # A CAPITAL IS AT PEACE. Guarded here rather than at the two call sites so a
    # third one cannot miss it: 0x1018 arms the war-prepare countdown, and a
    # countdown ticking down in town is the shape where somebody spends an
    # evening deciding the capital "half loaded".
    # WARNING: THIS GUARD NEVER FIRED IN A TOWN until 2026-09-04. It read
    # _SESSION["group"], which the 0x4011 handler overwrites on EVERY lap with
    # the island's first WAR field (what a 0x2000 would be answered with) --
    # and the 0x2000 handler never recorded the group it actually entered. So
    # standing in capital 39 the session said "group 1" and 0x1018 armed the
    # countdown in town anyway. The entered group is now `field`, written by
    # the 0x2000 handler and nowhere else.
    grp = sess._SESSION.get("field") if sess._SESSION.get("in_field") else None
    if sess._SESSION.get("in_field") and sess._SESSION.get("room", -1) != -1:
        # Inside a ROOM (a shop, the inn). The room rides a war-field gid
        # because a capital gid would take the capital loader, but nobody
        # fights in a shop: live 2026-09-04 the arms shop "thought it was a
        # war zone" because this fired for the gid it rides on.
        if not sess._SESSION.get("war_hushed"):
            sess._SESSION["war_hushed"] = True
            print("[feworld]    in room %s -- no war notify indoors"
                  % sess._SESSION.get("room"), flush=True)
        return
    if grp in zones.CAPITAL_GROUP_IDS:
        if not sess._SESSION.get("war_hushed"):
            sess._SESSION["war_hushed"] = True
            print("[feworld]    group %d is a CAPITAL -- no war notify. A "
                  "capital is a peace field; 0x1018 would arm a countdown in "
                  "town." % grp, flush=True)
        return
    # WARNING:KEY: A COUNTDOWN NOBODY WILL EVER ADVANCE IS A TRAP, AND IT COST THE
    # FIRST TWO-PLAYER SESSION (live 2026-09-11, both players stuck).
    #
    # With --campaign on, fecampaign owns the field's phase and war_deadline_pump
    # stands down for it ("a campaign owns this field's phase"). But this arm
    # still fired on field entry, so the client opened the War Prepare Window on
    # a 60 s prewar countdown -- in a field the campaign says is at PEACE, where
    # nothing will ever send the 0x1015 that closes it. The countdown hit
    # 00:00:00 and both players sat in a modal window they could not leave: they
    # could rotate the camera and nothing else. Two hours of 0x2084 polls
    # (1096 of them) and "area=5 Peace" in every answer.
    #
    # So: no war to announce, no window. A campaign field at peace is exactly
    # as peaceful as a capital, and fecampaign's own PEACE path (0x1017) is what
    # speaks for it.
    ph = death.field_war_phase(args, grp)
    if ph is not None and ph not in death._war_phases("PREP", "WAR", "TRUCE"):
        if not sess._SESSION.get("war_hushed_peace"):
            sess._SESSION["war_hushed_peace"] = True
            print("[feworld]    group %s is at PEACE under the campaign -- no "
                  "war notify. 0x1018 would open the War Prepare Window on a "
                  "countdown only a war can end, and there is no war here."
                  % grp, flush=True)
        return
    side = args.war
    mid = 0x1018 if side == "offensive" else 0x1019
    fields = [int(x, 0) for x in args.war_fields.split(",")] if args.war_fields else []
    fields = (fields + [0, 0, 0, 0])[:4]
    # ONE number drives both messages. The tail of this record is the same
    # 64-bit deadline 0x1015 carries -- HIGH first, then LOW -- and this is the
    # one the client renders as READABLE TEXT (phase 0 draws
    # 「戦争開始まで %02d:%02d:%02d」 at 0x052e5ee8), so it is the better of the
    # two to give a real value.
    ms = getattr(args, "war_deadline_ms", 0)
    # KEY: ONE CLOCK (2026-09-10): when a campaign owns this field the countdown
    # is its remaining phase time, not the fixed --war-deadline-ms.
    cms = campaignview.campaign_deadline_ms(args, grp)
    if cms is not None:
        ms = max(1, cms)
    dl, kind = (0, "raw")
    # --war-clock telemetry HAD NEVER ENGAGED for this arm (measured
    # 2026-08-28, three runs, every 0x1018 logged fields=[0,0,0,60000]):
    # this runs at field entry, BEFORE the first 28-byte 0x2023 has landed, so
    # war_abs_deadline had no sample and fell back to raw -- and the raw
    # deadline is judged on OUR monotonic clock from the send, while the
    # client's label counts from ITS clock (base = the CONNECT), so 0x1015
    # landed ~7-9 s after the label read 00:00:00. Fix: in the field, hold the
    # notify until the first sample and let the 0x2023 handler send it (a
    # sample arrives within ~400 ms of entry). On the war-map screen there is
    # no telemetry to wait for, so that path keeps the raw shape.
    if (ms and getattr(args, "war_clock", "telemetry") != "off"
            and sess._SESSION.get("in_field") and clock.client_now_ms() is None
            and not sess._SESSION.get("war_notify_deferred")):
        sess._SESSION["war_notify_deferred"] = True
        print("[feworld]    0x%04X war notify HELD until the client's first "
              "0x2023 clock sample (--war-clock telemetry): an absolute "
              "deadline needs the client's clock, and a raw one fires ~8 s "
              "after the on-screen label" % mid, flush=True)
        return
    sess._SESSION.pop("war_notify_deferred", None)
    if ms:
        # ABSOLUTE in the client's clock when a telemetry sample exists
        # (--war-clock telemetry/sync), else the old raw N.
        dl, kind = clock.war_abs_deadline(args, ms)
        fields[2] = (dl >> 32) & 0xFFFFFFFF
        fields[3] = dl & 0xFFFFFFFF
    body = struct.pack(">IIII", *[f & 0xFFFFFFFF for f in fields])
    wire.send(conn, outbound, wire.inner_msg(mid, body), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    # When this landed is the zero point --war-start deadline counts from: the
    # client's own clock is ms since ITS field-load timer reset (0x1012 /
    # 0x300C), and this message goes out a beat after that, so server-elapsed
    # and client-elapsed differ by the field load, not by an epoch.
    if sess._SESSION.get("in_field"):
        sess._SESSION["war_notify_t"] = time.monotonic()
        if ms:
            clock.war_arm("prewar", dl, kind)
    print("[feworld] -> 0x30 inner 0x%04X MSG_START_WAR_AS_%s_NOTIFY "
          "fields=%s%s -- arms [screen+0x62]; watch the client log for "
          "'Island Info was Updated. Open War Prepare Window.'"
          % (mid, side.upper(), fields,
             ("  [deadline %d ms -> the phase-0 label 戦争開始まで %02d:%02d:%02d]"
              % (ms, ms // 3600000, ms // 60000 % 60, ms // 1000 % 60))
             if ms else ""), flush=True)


def war_deadline_pump(conn, outbound, mode, be, args):
    """Advance the war when the armed deadline passes -- THE CLIENT NEVER DOES.

    2026-08-26: when the war view's countdown reaches zero (0x05111495) it
    raises UI event 0x80000002 and the war screen's handler DROPS it
    (0x05117d33-37; its siblings 0x80000000/0x80000001 are handled). Every
    phase change is therefore a server message:

        0x1018/0x1019  phase 0  戦争開始まで   (0x0511828c / 0x1019 -> 2)
        0x1015         phase 2  the arc        (0x05118554)      war ON
        0x1016/0x1029  phase 1  休戦終了まで   (0x05118167 / 0x051180a2) TRUCE
        0x1017/0x1028  (no phase write) field manager state 0   PEACE

    --war-start deadline: prewar -> 0x1015 only (the staged 08-26 run).
    --war-cycle on: prewar -> war -> truce -> peace -> a fresh 0x1018, with
    --war-length-ms / --war-truce-ms as the durations. All CHOSEN.

    2026-08-27: expiry is judged against the CLIENT's clock as reported in its
    own 0x2023 telemetry (see note_client_clock), not against monotonic()
    since the notify -- so the phase flips when the label reads 00:00:00, not
    a field-load earlier. `!war` in --gmcmd-file forces the next transition.
    """
    # The revive rides here too: war_deadline_pump runs at the top of inbound
    # frame processing AND on the read loop's idle path, so a dead player stands
    # up on time even if the client goes quiet (2026-09-09: it took 2m16s).
    death.revive_tick(conn, outbound, mode, be, args)
    clock.clock_sync_push(conn, outbound, mode, be, args)
    cycle = getattr(args, "war_cycle", "off") == "on"
    if getattr(args, "war_start", "off") != "deadline" and not cycle:
        return
    # KEY: 2026-09-09 -- WHY THE SECOND COUNTDOWN DID NOTHING, made printable.
    #
    # Live testing watched a full cycle (war -> "truce ends" -> the declaration
    # screen again) and then: "at the end of the second battle start timer,
    # nothing happens". Every gate below can produce exactly that and they are
    # indistinguishable from outside, so --war-trace prints which one it was,
    # once per gate per phase rather than every tick.
    #
    # What static RE ALREADY RULED OUT, so nobody re-checks it:
    #   * the 0x1015 arm skips its whole body when the war-view object is null
    #     (0x0511853B: call 0x50A4D60; test eax,eax; je) -- but that global
    #     [0x534354C] has exactly ONE writer in the image (0x050A4EA9) and the
    #     peace arm is not it, so the view is never torn down. Not this.
    #   * client_now_ms() EXTRAPOLATES from the last sample, so a client that
    #     stops sending 0x2023 on the war screen does not freeze the deadline.
    #     Not this either.
    # Which leaves: in_field false, the phase not armed, or the deadline not
    # due -- and this says which.
    def _gate(why):
        if getattr(args, "war_trace", "off") != "on":
            return
        key = "wartrace_%s" % why
        seen = sess._SESSION.setdefault("war_trace_seen", {})
        ph = sess._SESSION.get("war_phase")
        if seen.get(key) == ph:
            return
        seen[key] = ph
        print("[feworld]    war pump STOPPED at '%s' -- phase=%r in_field=%r "
              "deadline=%r kind=%r client_clock=%r"
              % (why, ph, sess._SESSION.get("in_field"),
                 sess._SESSION.get("war_deadline"),
                 sess._SESSION.get("war_deadline_kind"), clock.client_now_ms()), flush=True)
    if not sess._SESSION.get("in_field"):
        _gate("not in a field")
        return
    # KEY: ONE CLOCK. When a campaign is running, the FIELD's phase is the truth
    # and this pump is only its presentation: fecampaign advances the area on
    # its own persisted timer and calls back in here to arm the client's
    # countdown and push the phase advance. Left free-running alongside it,
    # this loop re-armed its own deadlines and the two disagreed -- the war map
    # could read "At war" while the client's clock was still counting down
    # prep, which is exactly the kind of thing that costs a session to
    # diagnose. Added 2026-09-10 with fecampaign.
    if campaignview.campaign_phase(args, sess._SESSION.get("field")) is not None:
        _gate("a campaign owns this field's phase")
        return
    phase = sess._SESSION.get("war_phase")
    if phase not in ("prewar", "war", "truce"):
        _gate("phase is not one that advances")
        return
    if not clock.war_deadline_due():
        now, dl = clock.client_now_ms(), sess._SESSION.get("war_deadline")
        if (getattr(args, "war_trace", "off") == "on" and now is not None
                and dl is not None):
            left = dl - now
            last = sess._SESSION.get("war_trace_left")
            # only once a second, and only inside the last 10 s, so the log
            # shows the approach to zero without drowning the run
            if left < 10000 and (last is None or abs(last - left) > 900):
                sess._SESSION["war_trace_left"] = left
                print("[feworld]    war %s: %d ms to the deadline "
                      "(client clock %d, deadline %d)"
                      % (phase, left, now, dl), flush=True)
        _gate("deadline not due")
        return
    sess._SESSION.pop("war_trace_left", None)
    sess._SESSION.pop("war_trace_seen", None)
    forced = sess._SESSION.pop("war_force", False)
    why = ("forced by !war" if forced else
           "the %s-clock deadline %d passed"
           % (sess._SESSION.get("war_deadline_kind"), sess._SESSION.get("war_deadline")))
    if phase == "prewar":
        sess._SESSION["war_started"] = True
        print("[feworld]    war countdown over (%s) -- advancing the phase, "
              "because the CLIENT does not: 0x05117d37 drops its own "
              "0x80000002 zero-crossing event." % why, flush=True)
        war_start(conn, outbound, mode, be, args)
    elif phase == "war" and cycle:
        dl, kind = clock.war_abs_deadline(args, getattr(args, "war_truce_ms", 0))
        print("[feworld]    war over (%s) -- truce" % why, flush=True)
        war_truce(conn, outbound, mode, be, args, dl, kind)
    elif phase == "truce" and cycle:
        print("[feworld]    truce over (%s) -- peace, then a fresh "
              "declaration" % why, flush=True)
        war_peace(conn, outbound, mode, be, args)
        sess._SESSION["war_started"] = False
        war_notify(conn, outbound, mode, be, args)


def war_start(conn, outbound, mode, be, args, deadline=None):
    """0x1015 -- THE MESSAGE THE WAR-PREP WINDOW HAS BEEN WAITING FOR.

    Found 2026-08-24 from a LIVE READ, not from a disassembly hunch. felive in
    the field reported

        [scene+0x528] pending-request flags = 0x00000080

    -- exactly one bit set, hours into a session. That word is the scene's
    "replies the server still owes me" bitfield (0x04ff84c0 sets, 0x04ff84e0
    clears, 0x04ff8500 tests), and bit 0x80 is armed by the arm that handles OUR
    OWN 0x1018/0x1019 war notify:

        0x051182c3  mov ecx, [0x5336d1c]      ; the scene
        0x051182cd  push 0x80
        0x051182d2  call 0x04ff84c0           ; SET

    So the client's answer to "a war has been declared" is to mark itself as
    awaiting the follow-up, and we have never sent one. Same shape as the 0x2084
    poll we armed and ignored for a whole session -- a wait WE caused.

    WHICH FOLLOW-UP. The clearers of bit 0x80 are all inside CWarPrepareWindow's
    own message family (ids 0x1015..0x1029, dispatched at 0x05117e00, which is
    slot +0x54 of its vtable 0x0528c0b0 -- the ctor at 0x051179ad is what pins
    that vtable). The dispatcher's index is read off the code and not assumed:

        0x05117e29  lea ecx, [ebp - 0x1015]
        0x05117e2f  cmp ecx, 0x14
        0x05117e3a  mov dl, [ecx + 0x5118748]
        0x05117e40  jmp [edx*4 + 0x511871c]

    so the byte table really is based at 0x1015. (An inherited note put
    0x0511843b on 0x1018; the table says 0x1019. The arm at 0x0511823d serves
    BOTH -- it does `cmp ebp, 0x1018 / sete al` at 0x051182d9 -- which is why
    that was easy to get one out.)

    0x1015's arm is 0x0511850f, and it is the only clearer that also advances the
    war's PHASE:

        0x0511851b  push 0x80 / call 0x04ff84e0     ; clear the pending bit
        0x05118525  two u32 reads (0x5045e60 x2)    ; <- the whole body
        0x05118554  mov [edi+0x70], 2               ; war-view phase 0 -> 2
        0x05118567  call 0x5111630                  ; install the new deadline

    Compare the 0x1018 arm, which sets the SAME phase field to 0 (0x0511828c)
    and installs a deadline through the SAME setter (0x0511829b). Read together:
    0x1018/0x1019 puts the war in phase 0 with a deadline, and 0x1015 moves it to
    phase 2 with a fresh one. Prep, then the thing prep was counting down to.

    THE BODY IS EXACTLY TWO BIG-ENDIAN u32 AND NOTHING ELSE -- the arm makes two
    stream calls and hands both straight to the deadline setter
    (`0x5111630(this, a, b)` = `[this+0x68] = a; [this+0x6c] = b`, four
    instructions, no ambiguity). The on-wire order matches 0x1018's LAST TWO
    fields exactly: first u32 -> the +0x6c slot, second -> +0x68. So
    --war-start-fields is the same pair as the tail of --war-fields.

    WHY THE DEFAULT MATTERS. --war-fields defaults to 0,0,0,0, so the deadline we
    install with 0x1018 is ALREADY EXPIRED the moment it lands. A countdown fed
    from an expired deadline sits at its end and stays there -- which matches the
    live-test report of "a loading bar almost completely full that
    stops there". That is CONSISTENT, not proven; the bar has not been tied to
    this gauge by measurement, and it must not be recorded as if it had.

    WARNING: WHAT IS MEASURED vs WHAT IS NOT.
      MEASURED: bit 0x80 is set live and stays set; only this family clears it;
                the 0x1018 arm is what sets it; the body is two u32s.
      NOT MEASURED: that 0x1015 means "the battle has begun", that sending it
                hands the player control, or that it is what the bar tracks.
    That is why this is OFF BY DEFAULT and a knob. Turn it on, watch
    [scene+0x528] go to 0 in felive, and observe what the screen
    does. A cleared flag is a real result even if nothing else changes -- it
    proves the message was accepted -- and it is the first thing to check before
    reading anything into what did or did not happen on screen.
    """
    if deadline is not None:
        # a campaign's own (dl, kind) -- fecampaign.drive_client (2026-09-10)
        dl, kind = deadline
        a, b = (dl >> 32) & 0xFFFFFFFF, dl & 0xFFFFFFFF
        clock.war_arm("war", dl, kind)
    elif getattr(args, "war_cycle", "off") == "on":
        # THE WAR'S OWN LENGTH, absolute in the client's clock. The arc sweeps
        # 3600000 ms (0x528b738), so a length above an hour draws negative.
        dl, kind = clock.war_abs_deadline(args, getattr(args, "war_length_ms", 0))
        a, b = (dl >> 32) & 0xFFFFFFFF, dl & 0xFFFFFFFF
        clock.war_arm("war", dl, kind)
    elif args.war_deadline_ms:
        # ONE number, split the way the wire wants it -- high dword first.
        a = (args.war_deadline_ms >> 32) & 0xFFFFFFFF
        b = args.war_deadline_ms & 0xFFFFFFFF
        sess._SESSION["war_phase"] = "war"
        sess._SESSION.pop("war_deadline", None)
    else:
        a, b = [int(x, 0) & 0xFFFFFFFF
                for x in (args.war_start_fields.split(",") + ["0", "0"])[:2]]
    wire.send(conn, outbound, wire.inner_msg(0x1015, struct.pack(">II", a, b)), mode, be,
         args.seq_mode == "echo", args.world_prefix)
    print("[feworld] -> 0x30 inner 0x1015 war-phase advance fields=(hi=%d,lo=%d)"
          " = deadline %d ms (%.1f min into the client's OWN clock, which is ms "
          "since the field-load timer reset -- 0x1148 is never sent, so its base "
          "is 0) -- clears [scene+0x528] bit 0x80 (armed by our own 0x1018), "
          "sets the war-view phase [+0x70] to 2 and installs the deadline. "
          "CHECK IT with `felive --war --watch --interval 1`: the gauge's frame "
          "counter moving with a frozen needle = EXPIRED, a frozen counter = a "
          "SHUT GATE (phase, or scene kind != 0xB)."
          % (a, b, (a << 32) | b, (((a << 32) | b) / 60000.0)), flush=True)
