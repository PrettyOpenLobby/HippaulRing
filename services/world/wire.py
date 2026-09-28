"""The world connection's transport: key exchange, inner message framing, send()."""
import struct
import fenet  # noqa: E402
from . import messages, sess

def do_handshake(conn, args):
    """Phases 1-3, byte-for-byte the lobby's. Returns (inbound, outbound) ciphers.

    Kept as its own function rather than imported from felobby because felobby's
    version narrates the LOBBY's story in its prints; the mechanics below are the
    shared ones from fenet.
    """
    P, S = fenet.fe_tables()
    master = fenet.Blowfish(args.master_key.encode(), P, S)
    mode, order = args.master_mode.split("/")
    be = order == "be"

    conn.settimeout(args.read_window)
    fr = fenet.recv_frame(conn)
    if not fr:
        print("[feworld] client closed before phase 1", flush=True)
        return None
    mid, body = fr
    print("[feworld] <- id=0x%04X %d bytes  (%s)"
          % (mid, len(body), messages.KNOWN.get(mid, "unknown")), flush=True)
    print(fenet.hexdump(body), flush=True)
    if mid != 0x34:
        print("[feworld] expected phase 1 (0x34); got 0x%04X. If this port turns "
              "out to be PLAINTEXT after all, that would mean the relay dialled "
              "with mode 0 -- check the client's `FE_NETWORK :: Connect ... /mode`"
              " line before assuming the cipher is wrong." % mid, flush=True)
        return None

    plain = fenet.bf_decrypt(master, body, mode, be)
    env = fenet.unwrap(plain)
    if not env:
        print("[feworld] phase-1 envelope did NOT validate -- so this port is not "
              "simply the lobby's exchange after all. Plaintext:", flush=True)
        print(fenet.hexdump(plain), flush=True)
        return None
    _keylen, session_key = env
    print("[feworld]   envelope VALID -- session key = %s" % session_key.hex(),
          flush=True)

    server_key = bytes.fromhex(args.server_key)
    if not server_key:
        raise SystemExit("--server-key must be non-empty: the client runs "
                         "key[i %% keylen] with an idiv at 0x05233c8d, so a zero "
                         "length crashes the process outright")
    session = fenet.Blowfish(session_key, P, S)
    outbound = fenet.Blowfish(server_key, P, S)

    body2 = fenet.wrap2(session_key, server_key, args.seq)
    fenet.send_frame(conn, 0x35, fenet.bf_encrypt(master, body2, mode, be))
    print("[feworld] -> id=0x35  client key echoed + our key %s"
          % server_key.hex(), flush=True)

    fr = fenet.recv_frame(conn)
    if not fr:
        print("[feworld] NO phase-3 reply. Read that as 'our 0x35 body failed the "
              "client's decipher validation', NOT as a wrong key -- the envelope "
              "above already proved the key. The client rewinds and retries the "
              "same message for ever, so FE will be spinning on "
              "'phase03...' with no error line.", flush=True)
        return None
    mid3, body3 = fr
    if mid3 != 0x36:
        print("[feworld] expected 0x36; got 0x%04X" % mid3, flush=True)
        return None
    echo = fenet.bf_decrypt(master, body3, mode, be)
    if server_key in echo:
        print("[feworld] *** phase 3 echoes our key -- HANDSHAKE COMPLETE, the "
              "world door speaks the lobby's transport. ***", flush=True)
    else:
        print("[feworld] phase 3 did not contain our key; continuing anyway so "
              "the next frame is still captured:", flush=True)
        print(fenet.hexdump(echo), flush=True)
    return session, outbound, mode, be


def unit_id_of(args):
    """The leading u32 of every world inner header -- resolved, not raw.

    KEY: MEASURED 2026-08-24, and it settles a guess open since 2026-08-19. The
    0x302B MSG_SERVER_UNIT_LOGIN_OK arm (0x050476d3) is

        mov ecx, [0x533998c]      ; the server unit -- SAME object as [scene+0x88]
        mov eax, [esp+0x10]
        mov [ecx+0x9e0], eax

    and nobody could hand-trace whether `[esp+0x10]` was this message's PAYLOAD
    or the inner header's leading u32, because we had always sent 0 in both. The
    A/B: send the charid (1) in the PAYLOAD with the header left at 0, then read
    [0x533998c]+0x9e0 live. It read **0**. So the slot is the HEADER, and
    --unit-login-value is not the knob -- this is.

    Why it matters: the entity we spawn for the player in 0x1000 carries
    id = charid, but the client was being told "you are unit 0". Two objects
    live in the field and they disagreed:

        server unit [0x533998c] == [scene+0x88]   id 0, pos (0,0,0)   INERT
        player unit [0x533aab4]                   pos (0, 15.948, 0)  grounded

    'auto' makes the header carry the charid so all of it names one character.
    WARNING: STILL A HYPOTHESIS FOR THE MOVEMENT WALL -- it is a coherent story for the
    split, not a proven cause. --unit-id 0 restores the old behaviour.
    """
    if args.unit_id == "auto":
        return sess._SESSION.get("charid", 0) & 0xFFFFFFFF
    return int(args.unit_id, 0) & 0xFFFFFFFF


def inner_msg(msg_id, payload=b"", unit_id=0):
    """Server->client inner message ON THE WORLD DOOR: [u32 unitId][u16 id][payload].

    WARNING: THIS IS NOT THE LOBBY'S HEADER, AND USING THE LOBBY'S HERE COSTS THE WHOLE
    SESSION. The two doors have DIFFERENT receive paths and they read different
    headers:

        lobby  0x05046db3   read_u16 x3        -> [u16 id][u16 f2][u16 f3][body]
        world  0x05047590   read_u32, read_u16 -> [u32 pad][u16 id][body]

    The world path is a separate dispatcher: 0x050475b2 reads a DWORD (0x522f9b0)
    into a local that is never read again, then 0x050475bd reads the u16 it
    actually dispatches on (0x522f970), and 0x050475e1 decodes it as
    `sub 0x2005 / sub 0x1026` -> 0x302B, `dec` -> 0x302C.

    Sending the lobby's `[u16 id][u16 0][u16 0]` here makes the client read the
    leading u32 as 0x302B0000 and the id as **0x0000**, which matches nothing. It
    dispatches nothing, logs neither MSG_SERVER_UNIT_LOGIN_OK nor NG, and drops
    the connection with `>Disconnected. err=0`. Three separate hypotheses were
    spent on that silence -- the outbound sequence twice, and a shim build --
    before anyone checked how many words the WORLD path reads.

    WARNING: RETRACTED 2026-08-23: "the leading u32 is read into `local-16` and never
    referenced again, so its value is free". IT IS THE MESSAGE'S TARGET UNIT ID.
    The world receive path passes it as the FIRST argument to the dispatcher --
    `0x050475fe mov eax,[esp+0x10]; push <stream>; push <id>; push eax; call
    0x5054980` -- and arms read it back as `[esp+0x1d08]` (arg1; note the head's
    identically-written `[esp+0x1d08]` at 0x050549a2 is arg2, the id, because
    `push edi` has not happened yet -- four bytes of stack drift between two
    textually identical operands).

    Two arms prove the meaning:
      0x302B MSG_SERVER_UNIT_LOGIN_OK (0x050476d3) does
             `mov [ [0x533998c] + 0x9e0 ], <that u32>` -- it TELLS the client its
             own unit id.
      0x1027 MSG_SET_POSITION (0x05054e50) drops the message unless that u32
             equals `[ [scene+0x88] + 0x9e0 ]` -- i.e. it is addressed to us.

    So it is only "free" in the sense that we have been sending 0 on BOTH sides
    and 0 == 0 happens to match. Keep the default at 0 so the two stay
    self-consistent; --unit-id changes both together.
    """
    return struct.pack(">IH", unit_id, msg_id) + payload


def _peer_ip(conn):
    """The client's address, or "" if the socket is already gone."""
    try:
        return conn.getpeername()[0]
    except OSError:
        return ""


def world_frame(mid, body, prefix):
    """`[u16 len][prefix bytes][u16 id][body]` -- the WORLD connection's framing.

    WARNING: THE ID IS NOT AT A FIXED OFFSET. The transport reads it at
    `cursor + [conn+0x1ca]`, and that field is the "mode" the connect call chose:
    `0x5046b40` maps connect mode 1 -> 0 and connect mode 2 -> **4**
    (`0x05046b8b`), and `0x522fd70` stores it at `+0x1ca`. The lobby dials with
    mode 1, the relay with mode 2, so the same protocol has the id at +0 on one
    door and +4 on the other.

    Read it in the frame-id reader at `0x05230582`:

        mov ecx, [ebx+4]           ; read cursor
        mov ax, word [edi+0x1ca]   ; the mode
        add eax, ecx               ; cursor + mode
        mov ax, word [eax]         ; the id lives HERE

    and the same shape governs the enciphered path at `0x05232e7c`, which also
    sizes the ciphertext as `total - header - 2`.

    This is why EVERY frame we sent on 54850 was refused, including a trivial
    one -- the client read a garbage id out of our payload and tore the
    connection down. Four message-level hypotheses died on this before a bisect
    showed the transport itself was at fault.

    The skipped bytes are never read, so their value is free.
    """
    return (struct.pack(">H", len(body) + 2 + prefix)
            + bytes(prefix)
            + struct.pack(">H", mid) + body)


def send_world_frame(conn, mid, body, prefix):
    conn.sendall(world_frame(mid, body, prefix))


def send(conn, outbound, body, mode, be, echo=True, prefix=4):
    """Send one enciphered message, numbered from OUR OWN counter.

    WARNING: OUR OUTBOUND SEQUENCE IS OUR OWN COUNTER, NOT AN ECHO OF THE CLIENT'S.
    This is what broke the world door on its first live reply, and the failure
    is silent from here: the client validates the sequence in the enveloped
    body (exchange_key_phase3's error strings include `illegal(sequence)`, and
    it keeps a decremented copy at [obj+0x20]). Our 0x302B answer carried
    seq=2, because 0x20 MSG_AUTH_CODE_NOTIFY arrived first and we did not
    answer it -- so our first outbound frame claimed to be our second. The
    client received the frame, never dispatched it (no `> MSG_SERVER_UNIT_
    LOGIN_OK` and no NG either), and dropped the connection with
    `>Disconnected. err=0`.
   
    The lobby door had been echoing too and got away with it ONLY because it
    answers essentially every enveloped message, so the echoed number happened
    to equal its own count. That is a coincidence, not a design, and it would
    have broken the first time an unanswered message arrived mid-session.
    """
    sess._OUT_SEQ[0] += 1
    seq = sess._LAST_IN[0] if echo else sess._OUT_SEQ[0]
    print("[feworld]    [seq] client sent %d; replying with %d (%s)"
          % (sess._LAST_IN[0], seq, "echo" if echo else "count"), flush=True)
    send_world_frame(conn, 0x30,
                     fenet.bf_encrypt(outbound, fenet.traffic_wrap(body, seq),
                                      mode, be), prefix)
