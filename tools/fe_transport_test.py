#!/usr/bin/env python3
"""fe_transport_test.py -- regression vectors for FE's transport layer.

Written 2026-08-17 as a safety net BEFORE moving the transport out of
`services/felobby.py` into `services/fenet.py`, because felobby was by then a
proven-working service and a mechanical 250-line move is exactly the kind of
change that breaks something silently. Every vector here is REAL TRAFFIC copied
out of a live session log, not something this code generated -- a self-consistent
round-trip would happily keep passing after both sides broke together.

    python tools/fe_transport_test.py
"""
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "services"))

import fenet  # noqa: E402

FAIL = []


def check(name, got, want):
    ok = got == want
    print("  %-46s %s" % (name, "PASS" if ok else "FAIL"))
    if not ok:
        print("      got  %r" % (got,))
        print("      want %r" % (want,))
        FAIL.append(name)


# --------------------------------------------------------------------------
# Vector 1: a real phase-1 (id 0x34) body, from a live felobby log (felobby-live3.log)
# (session of 2026-08-17 19:36). Deciphering it under the MASTER key must yield
# the cipher module's envelope and the client's random session key.
PHASE1_CT = bytes.fromhex("fd59316f58946f1e658b37328befb60313"
                          "4dfbf683edba8d")
PHASE1_PT = bytes.fromhex("766f0010d17c97e3527e4405b925f733988ee81a00000001")
SESSION_KEY = bytes.fromhex("d17c97e3527e4405b925f733988ee81a")


def test_phase1():
    print("phase 1 -- master-key decipher of a REAL 0x34 body")
    P, S = fenet.fe_tables()
    bf = fenet.Blowfish(b"fantasyearth", P, S)
    pt = fenet.bf_decrypt(bf, PHASE1_CT, "ecb", False)   # LITTLE-endian halves
    check("plaintext matches the logged decipher", pt, PHASE1_PT)
    nonce, keylen = struct.unpack(">HH", pt[:4])
    tail = struct.unpack(">I", pt[4 + keylen:8 + keylen])[0]
    check("keylen == 16", keylen, 16)
    check("envelope tail == 1", tail, 1)
    check("recovered session key", pt[4:4 + keylen], SESSION_KEY)
    check("nonce parsed", nonce, 0x766F)


# --------------------------------------------------------------------------
# Vector 2: the post-handshake envelope, from the same file's decrypted
# MSG_LOBBY_LOGIN_REQ:  [u16 cks][u32 seq][u16 len][data]
TRAFFIC_PLAIN = bytes.fromhex("4c7000000001000670000000004b0000")


def test_traffic_envelope():
    print("traffic envelope -- a REAL decrypted MSG_LOBBY_LOGIN_REQ")
    got = fenet.traffic_unwrap(TRAFFIC_PLAIN)
    check("unwraps", got is not None, True)
    if got:
        seq, data = got
        check("seq == 1", seq, 1)
        check("inner is 0x7000 + dword 0x4B", data,
              bytes.fromhex("70000000004b"))
    # and rebuilding it reproduces the client's own bytes, checksum included
    rebuilt = fenet.traffic_wrap(bytes.fromhex("70000000004b"), 1)
    check("traffic_wrap reproduces the client's frame",
          rebuilt[:len(TRAFFIC_PLAIN)], TRAFFIC_PLAIN)


# --------------------------------------------------------------------------
# Vector 3: the checksum, over a region whose expected value is in the frame.
def test_checksum():
    print("fold checksum -- verified against the frames above")
    region = TRAFFIC_PLAIN[2:]
    check("reproduces the logged checksum", fenet.fold_checksum(region), 0x4C70)


def test_blowfish_kat():
    print("cipher -- stock Blowfish known-answer test")
    ok, got, want = fenet.selftest()
    check("stock KAT (proves the +1 table relation too)", (ok, got), (True, want))


def test_framing():
    print("framing -- [u16 len][u16 id][body]")
    pkt = struct.pack(">HH", 2 + 3, 0x30) + b"abc"
    check("send_frame layout", fenet.frame(0x30, b"abc"), pkt)
    check("len counts everything after itself",
          struct.unpack(">H", pkt[:2])[0], len(pkt) - 2)


for t in (test_blowfish_kat, test_phase1, test_traffic_envelope,
          test_checksum, test_framing):
    t()

print()
if FAIL:
    print("%d FAILED: %s" % (len(FAIL), ", ".join(FAIL)))
    raise SystemExit(1)
print("all transport vectors pass")
