#!/usr/bin/env python3
"""fellb.py -- Fantasy Earth LOBBY LOAD BALANCER (LLB) responder.

FE's login has two hops. First it dials f-earth00.pol.com:54848 and asks the
balancer where the lobby is; then it drops that connection and connects to the
address it was given. Its own logging names every step (recovered from the
unpacked FE_Client image via a debug-string hook on the running client):

    LLB Connect start IP=%s, port=%d
    <MSG_LLB_LOGIN_REQ
    > MSG_LLB_LOGIN_OK : Lobby= ip(%d.%d.%d.%d) port(%d)
    > MSG_LLB_LOGIN_NG (err=%d)
    FE_NETWORK :: Execute_LoginRoutine() / LLB終了        ("LLB finished")
    ロビーが稼動していません。                             ("the lobby is not running")

So the balancer is a redirector: one request, one reply carrying an IPv4 address
and a port. Nothing else.

WIRE FORMAT -- THERE IS A THREE-WORD HEADER, NOT ONE
----------------------------------------------------
    [u16 length][u16 message-id][u16 f2][u16 f3][body...]

`length` counts the bytes AFTER itself. Big-endian throughout.

The two words after the id are easy to miss and cost a full debugging round.
The receive path at 0x05046db3 calls read_u16 (0x522f970) THREE times before it
dispatches -- 0x05046dcf, 0x05046dda, 0x05046de5 -- so the header consumes SIX
bytes off the stream, and only the first word is the id the switch at 0x05046e09
compares. f3 is handed to a virtual call at 0x05046dfb; neither f2 nor f3 gates
the dispatch (measured: a reply carrying 0x4703/0xa8c0 in them still reached the
MSG_LLB_LOGIN_OK handler), so we send zeros.

This is the shape of EVERY message on this dispatch, the lobby protocol on 54849
included -- budget the 6 bytes there too.

THE MESSAGE IDS -- SETTLED STATICALLY 2026-08-17, NO LONGER GUESSWORK
---------------------------------------------------------------------
Everything below is read out of the reply dispatch in the unpacked image
(the runtime dump of FE_Client.dll, base 0x04F90000). The switch is
a compare chain at 0x05046e09:

    cmp eax, 0x9002 / je  0x50470d9   -> pushes 0x52d4e4c "> MSG_LLB_LOGIN_NG"
    cmp eax, 0x7822 / jg  0x5046fc6
      0x5046fc6: sub eax,0x7831 / je   -> MSG_LOBBY_GET_GAME_INFO_OK
                 dec eax        / je   -> MSG_LOBBY_GET_GAME_INFO_NG
                 sub eax,0x17cf / jne default
                   (0x7832 + 0x17CF = 0x9001)
                 fallthrough 0x5046fdf -> pushes 0x52d4ee8 "> MSG_LLB_LOGIN_OK"

    MSG_LLB_LOGIN_REQ = 0x9000    (what FE SENDS -- measured, see below)
    MSG_LLB_LOGIN_OK  = 0x9001    (what we must answer with)
    MSG_LLB_LOGIN_NG  = 0x9002

A clean REQ/OK/NG triple, and the NG half is confirmed live: on 2026-08-17 this
file answered 0x9000 with 0x9002 and FE logged "> MSG_LLB_LOGIN_NG (err=0)".

WARNING: CORRECTION -- what this file used to say was wrong in two ways, and both are
why FE never got past the balancer:

  1. It treated 0x9000 as a "hello" and answered it with 0x9002. 0x9002 is the
     REJECTION. FE's very first exchange was being told its login failed. The
     request FE logs as `<MSG_LLB_LOGIN_REQ` is the 0x9000 frame itself -- the
     debug-string line sits immediately before the send in the captured client log.
  2. It treated 0x0002 as MSG_LLB_LOGIN_REQ and answered it with 0x7003. 0x0002
     is an UNLOGGED keepalive FE only emits every 15s while it is stuck, and
     0x7003 is not a case in the switch at all -- it falls to the default arm.
     That is why 30+ such replies produced no FE log line whatsoever.

  The old docstring claimed 0x7003 had been measured as MSG_LLB_LOGIN_OK
  printing "ip(0.0.0.0) port(54849)". No log on this machine has ever contained
  the string MSG_LLB_LOGIN_OK, and the switch above has no 0x7003 case. Treat
  that claim as unfounded -- and with it the whole "the IP layout is wrong"
  premise and the LAYOUT sweep it motivated. The layout was never the problem.

PAYLOAD LAYOUT AND BYTE ORDER
-----------------------------
The MSG_LLB_LOGIN_OK handler at 0x05046fdf reads exactly two fields, in order:

    call 0x5045e60 -> [ebp+0x9b0]     read_u32, advances the cursor by 4
    call 0x5045e30 -> [ebp+0x9b4]     read_u16, advances the cursor by 2

so the full OK frame is [u16 len=12][u16 0x9001][u16 0][u16 0][u32 ip][u16 port].
Both readers bounds-check against the frame length and then byte-swap:
0x5045e60 -> 0x522f9b0 -> thunk 0x524d3b6 -> ws2_32 ntohl, 0x5045e30 ->
0x522f970 -> thunk 0x524d3b0 -> ntohs. (Confirmed by export match: the thunk
targets 0x76cf59c0 / 0x76d09c90 sit at ws2_32 RVA 0x0059c0 / 0x019c90 -- ntohl /
ntohs -- off a common base 0x76cf0000.)

WARNING: A BOUNDS-FAILED READ IS SILENT. 0x522f9b0/0x522f970 check
`consumed + N <= length` and, when it fails, return WITHOUT writing the
destination and WITHOUT advancing the cursor. A short frame therefore yields a
field that was never assigned rather than an error. That is exactly how the
first attempt at this reply failed: sending [len=8][id][ip][port] -- i.e.
forgetting f2/f3 -- fed our four address bytes to the header reads, left the
cursor at 6 of an 8-byte region, bailed the u32 (10 > 8) and then read the port
correctly at offset 6. FE printed `ip(0.0.0.0) port(54849)`: a perfect port
alongside a zero address is the signature of this mistake, NOT of a byte-order
bug. (Same mechanism made our payload-less NG frames print `err=32296` one run
and `err=0` another -- uninitialized stack, not a value we sent.)

That byte swap is the whole trap. FE prints the address dword LSB-FIRST:

    printf("ip(%d.%d.%d.%d)", eax & 0xff, (eax >> 8) & 0xff,
                              (eax >> 16) & 0xff, eax >> 24)

Push wire bytes w0 w1 w2 w3 through ntohl and that prints w3.w2.w1.w0. So the
address goes on the wire REVERSED -- i.e. as the in_addr in HOST order, which is
what the original server got by writing htonl(sin_addr.s_addr) on an x86. Send
127.0.0.1 as `47 03 a8 c0`; sending `c0 a8 03 47` makes FE dial 71.3.168.192.
gmd.py's POL_GMD_CHAT_IP carries the identical wart, and the note there is worth
repeating: a reversed address looks exactly like a rejection.

The port has no such twist -- ntohs means plain big-endian, 54849 -> `d6 41`.

WHAT SUCCESS LOOKS LIKE
-----------------------
FE should log "> MSG_LLB_LOGIN_OK : Lobby= ip(127.0.0.1) port(54849)" and
then "FE_NETWORK :: Execute_LoginRoutine() / LLB終了", drop this connection and
dial 54849 (felobby). When nothing serves 54849 it fails there next --
deliberately: the port must be UNBOUND rather than logger-bound, for the same
reason as 54848 -- a logger-only bind would let the connect succeed and strand
FE forever with no way back to the Viewer but killing it. Unbound, it fails
fast and says so, and its log names the next protocol
(MSG_LOBBY_LOGIN_REQ / _OK / _NG, MSG_LOBBY_GET_GAME_INFO_*, MSG_LOBBY_JOIN_GAME_*
-- ids 0x7001, 0x7002, 0x7821, 0x7822, 0x7831, 0x7832 in the same switch).

Env
---
  POL_FELLB_PORT        listen port                     (default 54848)
  POL_FELLB_IP          lobby IPv4 handed back          (default POL_STUB_IP)
  POL_FELLB_LOBBY_PORT  lobby port handed back          (default 54849)
  POL_LOG_DIR           where to tee the log            (default /logs)
  POL_FELLB_LOGIN_OK_ID  override the OK id             (default 0x9001)

  POL_FELLB_OK_ID is OBSOLETE and deliberately ignored. Early deployments
  pinned it to 0x7003; honouring that would re-break the login, so it is not
  read at all.
"""
import os
import socket
import struct
import sys
import threading
import time

try:
    # Per-client address (LAN / tailnet / internet-via-edge). See srvcore's
    # "The address a client is told to dial next" and deploy/edge/README.md.
    from srvcore import advertise_for
except ImportError:                     # standalone use outside services/
    def advertise_for(default, peer_ip=None, dialed_ip=None):
        return default

PORT = int(os.environ.get("POL_FELLB_PORT", "54848"))
LOBBY_IP = os.environ.get("POL_FELLB_IP") or os.environ.get("POL_STUB_IP", "127.0.0.1")
LOBBY_PORT = int(os.environ.get("POL_FELLB_LOBBY_PORT", "54849"))
LOG_DIR = os.environ.get("POL_LOG_DIR", "/logs")

MSG_LLB_LOGIN_REQ = 0x9000
MSG_LLB_LOGIN_OK = int(os.environ.get("POL_FELLB_LOGIN_OK_ID", "0x9001"), 0)
MSG_LLB_LOGIN_NG = 0x9002

#: FE emits this every 15s on an LLB connection it is stuck on. It carries no
#: payload and, unlike the login request, FE prints NOTHING when it sends one --
#: so it is a keepalive, not a request, and it is not what the lobby address
#: belongs in. Logged and left unanswered on purpose; if a future capture shows
#: FE timing out waiting for an ack, that is the evidence needed to add one.
MSG_KEEPALIVE = 0x0002


def log(msg):
    line = "%s [fellb] %s" % (time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), msg)
    print(line, flush=True)
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(os.path.join(LOG_DIR, "fellb.log"), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def build_login_ok(ip_str, port):
    """[u16 len][u16 id][u16 f2][u16 f3][u32 ip][u16 port].

    f2/f3 are the two header words after the id -- they are NOT optional; omit
    them and the address bytes get consumed as header, silently. See the wire
    format section in the module docstring.

    The address is written REVERSED -- host order, not network order. FE ntohl's
    it and then prints and stores it LSB-first, so this is what makes it come
    out as ip_str and makes it dial ip_str.
    """
    octets = socket.inet_aton(ip_str)              # network order: 192,168,3,71
    body = struct.pack(">HHH", MSG_LLB_LOGIN_OK, 0, 0) \
        + octets[::-1] \
        + struct.pack(">H", port)
    return struct.pack(">H", len(body)) + body


def recv_frame(sock):
    """Read one [u16 length][body] frame. Returns (msg_id, payload) or None."""
    hdr = b""
    while len(hdr) < 2:
        c = sock.recv(2 - len(hdr))
        if not c:
            return None
        hdr += c
    (ln,) = struct.unpack(">H", hdr)
    if ln > 4096:                      # nothing sane on this channel is large
        log("absurd length %d -- dropping connection" % ln)
        return None
    body = b""
    while len(body) < ln:
        c = sock.recv(ln - len(body))
        if not c:
            return None
        body += c
    if len(body) < 2:
        return (None, body)
    (mid,) = struct.unpack(">H", body[:2])
    return (mid, body[2:])


def handle(conn, addr):
    log("CONNECT from %s:%d" % addr)
    try:
        dialed = conn.getsockname()[0]
    except OSError:
        dialed = None
    try:
        conn.settimeout(300)
        while True:
            fr = recv_frame(conn)
            if fr is None:
                break
            mid, payload = fr
            log("  <- id=0x%04X payload=%s" % (mid if mid is not None else -1,
                                               payload.hex() or "(none)"))

            if mid == MSG_LLB_LOGIN_REQ:
                # LOBBY_IP is the box-wide default; THIS client may only be able
                # to reach another of our addresses (LAN console, edge player).
                lobby_ip = advertise_for(LOBBY_IP, addr[0], dialed)
                pkt = build_login_ok(lobby_ip, LOBBY_PORT)
                conn.sendall(pkt)
                log("  -> MSG_LLB_LOGIN_OK id=0x%04X lobby=%s:%d  %s"
                    % (MSG_LLB_LOGIN_OK, lobby_ip, LOBBY_PORT, pkt.hex()))
                log("     expect FE to log: > MSG_LLB_LOGIN_OK : Lobby= "
                    "ip(%s) port(%d)   -- any other address means the octet "
                    "order regressed" % (lobby_ip, LOBBY_PORT))
                continue

            if mid == MSG_KEEPALIVE:
                log("  (keepalive 0x0002 -- no reply by design; FE only sends "
                    "these while stuck, so seeing them AFTER a LOGIN_OK is "
                    "itself a finding)")
                continue

            log("  (unknown id 0x%04X -- no reply; add a case if FE waits on it)"
                % (mid if mid is not None else 0))
    except (OSError, socket.timeout) as e:
        log("  connection ended: %s" % e)
    finally:
        try:
            conn.close()
        except OSError:
            pass
        log("DISCONNECT %s:%d" % addr)


def main():
    log("listening on 0.0.0.0:%d -- answering MSG_LLB_LOGIN_REQ (0x%04X) with "
        "MSG_LLB_LOGIN_OK (0x%04X), lobby %s:%d"
        % (PORT, MSG_LLB_LOGIN_REQ, MSG_LLB_LOGIN_OK, LOBBY_IP, LOBBY_PORT))
    log("reply bytes: %s  (address reversed on the wire -- see the docstring)"
        % build_login_ok(LOBBY_IP, LOBBY_PORT).hex())
    if os.environ.get("POL_FELLB_OK_ID"):
        log("NOTE: POL_FELLB_OK_ID=%s is set and IGNORED -- it is a leftover "
            "from the id sweep and pinning it would re-break the login."
            % os.environ["POL_FELLB_OK_ID"])

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", PORT))
    srv.listen(8)
    while True:
        conn, addr = srv.accept()
        threading.Thread(target=handle, args=(conn, addr), daemon=True).start()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
