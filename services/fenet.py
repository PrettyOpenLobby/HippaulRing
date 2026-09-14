#!/usr/bin/env python3
"""fenet.py -- Fantasy Earth's network transport: framing, envelopes, ciphering.

Extracted from `services/felobby.py` 2026-08-17, the day the lobby closed and the
client dialled its WORLD server. The relay connect (`0x50483b0`) dials with
**mode 2**, and the cipher exchange is gated on connect mode 1..2 (`0x05046804`)
-- so the world door speaks the SAME transport as the lobby, and the alternative
to sharing it was pasting 250 lines into a second service.

Everything here is the wire layer only; no message means anything at this level.

FRAMING is `[u16 len][u16 id][body]`, big-endian, len counting after itself.
Once the key exchange is done, every message rides an OUTER id `0x30` whose body
is `encipher(traffic_wrap([u16 inner_id][fields]))`, so the real id is INSIDE the
ciphertext and never appears on the wire.

THREE ENVELOPE LAYOUTS, and mixing them up is the main trap -- all three use the
same fold checksum over everything after it, padded to 8:

    phase 1   [u16 cks][u16 len][data][u32 counter]                  wrap()
    phase 2   [u16 cks][u16 keylen][CLIENT KEY][u16 len2][KEY][u32]  wrap2()
    traffic   [u16 cks][u32 seq][u16 len][data]                      traffic_wrap()

The sequence moves to the FRONT once the session is keyed.

WARNING: Blowfish here is FE's: non-standard tables (see feblowfish.py) and
LITTLE-endian block halves. Both are easy to miss and either one alone produces
garbage.

Regression vectors for all of this, taken from real session logs rather than from
this code, are in `tools/fe_transport_test.py`. Run it after touching anything
in this file.
"""
import struct

from feblowfish import Blowfish, load_tables, minus_one_per_byte, selftest

__all__ = ["Blowfish", "load_tables", "minus_one_per_byte", "selftest",
           "fe_tables", "stock_tables", "fold_checksum", "wrap", "wrap2",
           "inner_msg", "traffic_wrap", "traffic_unwrap", "unwrap",
           "bf_decrypt", "bf_encrypt", "recv_frame", "send_frame", "frame",
           "hexdump"]

_TABLES = None


def fe_tables():
    """FE's effective P/S, loaded once."""
    global _TABLES
    if _TABLES is None:
        _TABLES = load_tables()
    return _TABLES


def stock_tables():
    """The standard pi-derived constants, derived from FE's (stock = FE - 1)."""
    return minus_one_per_byte(*fe_tables())


def frame(mid, body):
    """The bytes send_frame puts on the wire -- exposed so a test can check the
    layout without a socket."""
    return struct.pack(">HH", 2 + len(body), mid) + body


def _xor(a, b):
    return bytes(x ^ y for x, y in zip(a, b))


#: THE CIPHER MODULE'S OWN FRAMING, not application data.
#:
#:      [u16 nonce][u16 datalen][data][u32 0x00000001]   then block-padded to 8
#:
#: Proof: in phase 1 the client generates exactly [obj+0x10] = 16 random key
#: bytes and hands them to encipher(), yet the plaintext we recover is 24 bytes
#: with the key bracketed by that structure. The client never built it, so
#: encipher() did -- and decipher() validates it on the way back in.
#:
#: This cost a live round. A 0x35 carrying RAW ciphertext (no envelope) is
#: received by the client, matches the id check, then fails decipher; the handler
#: rewinds via 0x522f180 and returns false, so the caller retries the SAME message
#: for ever. The signature is FE spamming `Exchanging Cipher key phase03...` with
#: no `Exchange Cipher Key failed` -- a silent livelock, not a rejection.

def fold_checksum(region):
    """The client's own checksum, read off the encipher at 0x052351ca.

    XOR every dword of `region` (native little-endian reads), XOR the leftover
    1-3 bytes, then fold high half onto low: (acc >> 16) ^ acc. Stored big-endian
    (the code htons()es it), so reading the wire bytes as '>H' gives this value
    back directly. Verified against five independent live phase-1 frames.
    """
    n = len(region)
    nd, nr = n // 4, n % 4
    acc = 0
    for i in range(nd):
        acc ^= struct.unpack_from("<I", region, i * 4)[0]
    for i in range(nr):
        acc ^= region[nd * 4 + i]
    return ((acc >> 16) ^ acc) & 0xFFFF

def wrap(data, counter=1):
    """[u16 checksum][u16 len BE][data][u32 counter BE], padded to 8.

    Built by the encipher at 0x052350e0: malloc(len+8), length at +2, payload at
    +4, htonl(counter) after it, checksum over everything from +2 onward. The
    counter lives at [obj+0x18] and is initialised to 1 if zero.
    """
    region = struct.pack(">H", len(data)) + data + struct.pack(">I", counter)
    body = struct.pack(">H", fold_checksum(region)) + region
    if len(body) % 8:
        body += b"\0" * (8 - len(body) % 8)
    return body

def wrap2(key, data=b"", seq=1):
    """PHASE-2 structure -- NOT the same as phase 1's, which is the trap.

        [u16 checksum][u16 keylen][client key echoed][u16 len2][SERVER KEY][u32 seq]

    WARNING: `data` IS THE SERVER'S KEY AND MUST NOT BE EMPTY. Once the body passes
    validation the client installs it via objC's setKey, and the Blowfish key
    schedule indexes key[i % keylen] with a literal `idiv` at 0x05233c8d. A zero
    length divides by zero and takes the process down with
    STATUS_INTEGER_DIVIDE_BY_ZERO (c0000094) -- which is exactly what
    errlog<date>.csv recorded when we sent len2=0. So the exchange is
    bidirectional: objB holds the CLIENT's key (their traffic), objC holds OURS.

    exchange_key_phase3 (0x052355c0) parses it in that order: key length, the key
    (byte-compared against its own), then a second length-prefixed block, then an
    ntohl sequence at 0x052358ee (whose error label is `illegal(sequence)`), which
    it stores decremented at [obj+0x20].

    The checksum region is `keylen + len2 + 8` bytes from buf+2 (0x0523581d) --
    i.e. everything after the checksum field -- which only balances if BOTH length
    fields are present. Phase 1's frame has no len2, so reusing wrap() here makes
    the client read the counter's high word as len2 and walk off the end. That is
    why a body byte-identical to the client's own phase-1 frame is still rejected.
    """
    region = (struct.pack(">H", len(key)) + key
              + struct.pack(">H", len(data)) + data
              + struct.pack(">I", seq))
    body = struct.pack(">H", fold_checksum(region)) + region
    if len(body) % 8:
        body += b"\0" * (8 - len(body) % 8)
    return body

def inner_msg(msg_id, payload=b""):
    """Server->client inner message: [u16 id][u16 f2][u16 f3][payload].

    WARNING: THE TWO WORDS AFTER THE ID ARE MANDATORY. The lobby shares the LLB's
    receive path (0x05046db3), which calls read_u16 THREE times before it
    dispatches -- id, f2, f3 -- so a reply of [id][payload] has its first FOUR
    payload bytes swallowed as the header.

    The client's own messages to us do NOT carry them (beginMessage writes just
    the id, so MSG_LOBBY_LOGIN_REQ is exactly `7000 0000004b`); the asymmetry is
    real and easy to mirror wrongly.

    Diagnosed from FE's own log, where every field was shifted by exactly 4:
        account="TestPlayer" -> "Player"        (lost "Test")
        Num of Games 1       -> 0
        IP 127.0.0.1:54850 -> IP(0.0.66.214):Port(1)
    A uniform 4-byte shift across three unrelated replies is this bug's
    signature.
    """
    return struct.pack(">HHH", msg_id, 0, 0) + payload

def traffic_wrap(data, seq=1):
    """POST-HANDSHAKE envelope -- a THIRD field order, measured live:

        [u16 checksum][u32 seq][u16 len][data]   then block-padded to 8

    Note this is neither wrap()'s (`[cks][len][data][u32]`) nor wrap2()'s. The
    sequence moves to the FRONT once the session is keyed. First seen decrypting
    a real MSG_LOBBY_LOGIN_REQ:

        4c70 00000001 0006 7000 0000004b 0000
        cks  seq=1    len=6  id=0x7000 + dword 0x4B

    Checksum is the same fold over everything after itself.
    """
    region = struct.pack(">I", seq) + struct.pack(">H", len(data)) + data
    body = struct.pack(">H", fold_checksum(region)) + region
    if len(body) % 8:
        body += b"\0" * (8 - len(body) % 8)
    return body

def traffic_unwrap(plain):
    """-> (seq, data) or None if the structure/checksum does not validate."""
    if len(plain) < 8:
        return None
    cks, seq, ln = struct.unpack(">HIH", plain[:8])
    if 8 + ln > len(plain):
        return None
    if fold_checksum(plain[2:8 + ln]) != cks:
        return None
    return seq, plain[8:8 + ln]

def unwrap(plain):
    """-> (counter, data) or None if the structure/checksum does not validate."""
    if len(plain) < 8:
        return None
    want, ln = struct.unpack(">HH", plain[:4])
    if 4 + ln + 4 > len(plain):
        return None
    region = plain[2:4 + ln + 4]
    if fold_checksum(region) != want:
        return None
    counter = struct.unpack(">I", plain[4 + ln:4 + ln + 4])[0]
    return counter, plain[4:4 + ln]

def bf_decrypt(bf, data, mode, be):
    out, prev = b"", b"\0" * 8
    for i in range(0, len(data) - 7, 8):
        blk = data[i:i + 8]
        d = bf.decrypt_block(blk, be)
        out += _xor(d, prev) if mode == "cbc" else d
        prev = blk
    return out

def bf_encrypt(bf, data, mode, be):
    if len(data) % 8:
        data += b"\0" * (8 - len(data) % 8)
    out, prev = b"", b"\0" * 8
    for i in range(0, len(data), 8):
        blk = data[i:i + 8]
        if mode == "cbc":
            blk = _xor(blk, prev)
        c = bf.encrypt_block(blk, be)
        out += c
        prev = c
    return out

def recv_frame(sock):
    """[u16 len][u16 id][body] -- len counts everything after itself."""
    hdr = b""
    while len(hdr) < 2:
        c = sock.recv(2 - len(hdr))
        if not c:
            return None
        hdr += c
    (ln,) = struct.unpack(">H", hdr)
    if ln > 8192:
        return None
    body = b""
    while len(body) < ln:
        c = sock.recv(ln - len(body))
        if not c:
            return None
        body += c
    (mid,) = struct.unpack(">H", body[:2])
    return mid, body[2:]

def send_frame(sock, mid, body):
    pkt = struct.pack(">HH", 2 + len(body), mid) + body
    sock.sendall(pkt)
    return pkt

def hexdump(b, indent="    "):
    out = []
    for off in range(0, len(b), 16):
        chunk = b[off:off + 16]
        hx = " ".join("%02x" % c for c in chunk)
        asc = "".join(chr(c) if 32 <= c < 127 else "." for c in chunk)
        out.append("%s%04x  %-47s  %s" % (indent, off, hx, asc))
    return "\n".join(out)
