#!/usr/bin/env python3
"""fe_partyloop_test.py -- a PARTY end to end, three sessions, real defaults.

Run from services/:  python ../tools/fe_partyloop_test.py

2026-10-08 (round 2). Prod has never seen a party answer. This drives the
whole life of one through feworld's real objects (fe_harness: the args main()
runs with, the real _serve_loop per inbound frame, the relays pumped on each
receiver's own session) and keeps a MODEL OF EACH CLIENT'S MEMBER LIST built
only from what that client was sent, read the way its arms read it:

  0x1134 0x050515c0  [u8 N]{u32 ChrID, cstr name, u8 class}, appended unless
                     the ChrID is already listed (0x05051160)
  0x1135 0x050517d0  [u32 ChrID] removed
  0x1136/0x1137/0x113C  the list freed
  0x1139 0x05051c60  [u8 N]{u32 ChrID, u8, u16 HP max (+0x40), u16 HP
                     (+0x42), u32 condition (+0x44), u32}; the row prints
                     "HP %4d/%4d" from (+0x42, +0x40) (0x050ca1ec)

and the two facts the List menu rests on: the client finds ITS OWN ChrID in
that list (0x050cb5e3 -> 0x05051160; not found = no menu) and the row at
index 0 has the leader's entries (0x05051180 numbers the rows into +0x50,
0x050cb629 tests the own row's +0x50 == 0). So after every step each
client's list must hold itself, and its head must be the server's leader.
"""
import struct
import sys

import fe_harness as H
from fe_harness import check, ids, bodies, say


class ClientList(object):
    """One client's party member list, from its own inbound only."""

    def __init__(self):
        self.rows, self.hp, self.boxes = [], {}, []

    def feed(self, box):
        for mid, _unit, b in box:
            if mid == 0x1134:
                n, o = b[0], 1
                for _ in range(n):
                    cid = struct.unpack_from(">I", b, o)[0]
                    e = b.index(b"\0", o + 4)
                    o = e + 2
                    if cid not in self.rows:
                        self.rows.append(cid)
                assert o == len(b), "0x1134 left %d bytes" % (len(b) - o)
                if not self.boxes and n == 1:
                    self.boxes.append("Party formed.")
                elif not self.boxes:
                    self.boxes.append("Joined the party.")
            elif mid == 0x1135:
                cid = struct.unpack(">I", b)[0]
                if cid in self.rows:
                    self.rows.remove(cid)
            elif mid in (0x1136, 0x1137, 0x113C):
                self.rows, self.boxes = [], []
            elif mid == 0x1139:
                n, o = b[0], 1
                for _ in range(n):
                    cid, _b, hmax, hp, cond, _d = struct.unpack_from(
                        ">IBHHII", b, o)
                    o += 17
                    self.hp[cid] = (hp, hmax, cond)
                assert o == len(b)


def main():
    H.setup()
    import feparty
    from world import combat
    args = H.real_args()
    check("real defaults: --unit-id auto, party HP on, leader disbands",
          args.unit_id == "auto" and args.party_hp == "on"
          and args.party_disband == "leader" and args.party_max == 5,
          (args.unit_id, args.party_hp, args.party_disband))
    alice = H.Peer("Alice", 31, args, nation=2, field=17)
    bob = H.Peer("Bob", 32, args, nation=2, field=17)
    carol = H.Peer("Carol", 33, args, nation=3, field=17)
    peers = (alice, bob, carol)
    lists = {p.charid: ClientList() for p in peers}

    def flow(*boxes):
        for p, box in boxes:
            lists[p.charid].feed(box)

    def settle():
        got = H.deliver(*peers)
        for p in peers:
            lists[p.charid].feed(got[p.charid])
        return got

    def party():
        with feparty._LOCK:
            p = feparty._party_of(alice.charid) or feparty._party_of(bob.charid)
            return (p["leader"], list(p["order"])) if p else (None, [])

    def lists_agree(label):
        leader, order = party()
        for p in peers:
            rows = lists[p.charid].rows
            if p.charid in order:
                check("%s: %s's list holds itself, head = leader %s"
                      % (label, p.name, leader),
                      p.charid in rows and rows[0] == leader
                      and sorted(rows) == sorted(order), rows)
            else:
                check("%s: %s (not a member) lists nobody" % (label, p.name),
                      rows == [], rows)
    try:
        # ---- invite -> prompt on the target, nothing to the inviter yet
        out = alice.send(0x112E, struct.pack(">I", bob.charid))
        check("0x112E naming Bob's unit (= his charid) answers Alice nothing yet",
              not [m for m in ids(out) if m in (0x112F, 0x1130)], ids(out))
        got = settle()
        inv = bodies(got[bob.charid], 0x112E)
        a, b = struct.unpack(">II", inv[0]) if inv else (None, None)
        check("Bob's client gets 0x112E [u32 A = Alice's unit][u32 B token]",
              len(inv) == 1 and a == alice.charid and len(inv[0]) == 8, inv)
        # ---- Bob accepts (P key -> 0x2087 [1][B][0])
        out = bob.send(0x2087, struct.pack(">BII", 1, b, 0))
        flow((bob, out))
        check("Bob: 0x1134 (Alice, Bob) then 0x1131",
              [m for m in ids(out) if m in (0x1134, 0x1131)] == [0x1134, 0x1131],
              ids(out))
        got = settle()
        check("Alice: 0x1134 {Alice}, 0x1134 {Bob}, 0x112F",
              [m for m in ids(got[alice.charid]) if m in (0x1134, 0x112F)]
              == [0x1134, 0x1134, 0x112F], ids(got[alice.charid]))
        check("the arm's boxes: Alice 'Party formed.', Bob 'Joined the party.'",
              lists[alice.charid].boxes == ["Party formed."]
              and lists[bob.charid].boxes == ["Joined the party."],
              (lists[alice.charid].boxes, lists[bob.charid].boxes))
        lists_agree("formed")

        # ---- the HP rows: Bob is hit, his row moves on both clients
        bob.session["player_hp"] = 640
        flow((bob, bob.pump()))
        settle()
        check("Alice's party window: Bob HP 640/1000",
              lists[alice.charid].hp.get(bob.charid, (0, 0, 0))[:2] == (640, 1000),
              lists[alice.charid].hp)
        flow((alice, alice.pump()))
        settle()
        check("Bob's own window: Alice HP 1000/1000",
              lists[bob.charid].hp.get(alice.charid, (0, 0, 0))[:2] == (1000, 1000),
              lists[bob.charid].hp)
        n_before = len(bob.out)
        bob.pump()
        check("no change -> no new 0x1139", not [
            m for m, _u, _b in bob.out[n_before:] if m == 0x1139])
        # a poison icon on the row: condition bit 0x1 (the panel's green)
        import time as _t
        bob.session.setdefault("status", {})["poison"] = {
            "until": _t.monotonic() + 30, "effect": 133, "amount": 15,
            "tick_ms": 3000, "next_at": _t.monotonic() + 30}
        flow((bob, bob.pump()))
        settle()
        check("a poisoned Bob reads condition 0x1 on Alice's row",
              lists[alice.charid].hp.get(bob.charid, (0, 0, 0))[2] & 0x1,
              lists[alice.charid].hp)
        bob.session["status"].clear()

        # ---- /party chat reaches members only
        out = alice.send(0x208A, b"Alice\0hello party\0")
        got = settle()
        check("Bob gets the /party line, Carol does not",
              bodies(got[bob.charid], 0x208A)
              and not bodies(got[carol.charid], 0x208A))

        # ---- a member (not the leader) may invite; Carol of ANOTHER nation
        # may join at peace (manual p.43)
        bob.send(0x112E, struct.pack(">I", carol.charid))
        got = settle()
        tok = struct.unpack(">II", bodies(got[carol.charid], 0x112E)[0])[1]
        out = carol.send(0x2087, struct.pack(">BII", 1, tok, 0))
        flow((carol, out))
        settle()
        check("Carol joined: three members, Alice leads",
              party() == (alice.charid, [31, 32, 33]), party())
        lists_agree("three")

        # ---- kicks: a member cannot, the leader can
        out = bob.send(0x2088, struct.pack(">I", carol.charid))
        check("Bob (not the leader) kicking -> 0x113B [u32 2]",
              bodies(out, 0x113B) == [struct.pack(">I", 2)], out)
        out = alice.send(0x2088, struct.pack(">I", carol.charid))
        flow((alice, out))
        settle()
        check("the leader's kick: 0x113A + 0x1135 to her, 0x113C to Carol",
              [m for m in ids(out) if m in (0x113A, 0x1135)] == [0x113A, 0x1135]
              and lists[carol.charid].rows == [])
        lists_agree("after the kick")

        # ---- Carol back, then the LEADER LOGS OUT: Bob leads (manual p.43)
        alice.send(0x112E, struct.pack(">I", carol.charid))
        got = settle()
        tok = struct.unpack(">II", bodies(got[carol.charid], 0x112E)[0])[1]
        flow((carol, carol.send(0x2087, struct.pack(">BII", 1, tok, 0))))
        settle()
        lists_agree("Carol again")
        alice.call(feparty.session_end)
        lists[alice.charid] = ClientList()
        settle()
        check("leader logout -> Bob leads Bob+Carol",
              party() == (bob.charid, [32, 33]), party())
        lists_agree("after the leader's logout")

        # ---- the new leader DISBANDS (List > Disband Party = 0x2089)
        out = bob.send(0x2089)
        flow((bob, out))
        settle()
        check("leader's 0x2089 -> 0x1136 to him and to Carol; party gone",
              0x1136 in ids(out) and party() == (None, [])
              and lists[carol.charid].rows == [], ids(out))

        # ---- a member who changes field leaves (manual p.43)
        bob.send(0x112E, struct.pack(">I", carol.charid))
        got = settle()
        tok = struct.unpack(">II", bodies(got[carol.charid], 0x112E)[0])[1]
        flow((carol, carol.send(0x2087, struct.pack(">BII", 1, tok, 0))))
        settle()
        check("Bob+Carol again", party() == (bob.charid, [32, 33]), party())
        carol.session["field"] = 18
        out = carol.pump()
        flow((carol, out))
        settle()
        check("Carol walks into field 18 -> 0x1137 to her, the party breaks up",
              0x1137 in ids(out) and party() == (None, []), ids(out))
        lists_agree("after the field change")

        # ---- loot right: the party's pooled damage beats a heavier solo
        alice.session["field"] = carol.session["field"] = 17
        bob.send(0x112E, struct.pack(">I", alice.charid))
        got = settle()
        tok = struct.unpack(">II", bodies(got[alice.charid], 0x112E)[0])[1]
        alice.send(0x2087, struct.pack(">BII", 1, tok, 0))
        settle()
        m = {"ledger": {31: 300, 32: 300, 33: 500},
             "first": {33: 1, 31: 2, 32: 3}}
        owner, group, _led, why = combat.loot_winner(m, 33)
        check("manual p.38: Alice+Bob (600) beat Carol (500); owner is a member",
              group == {31, 32} and owner in (31, 32), (owner, group, why))
        share = combat.split_by_damage(1000, {31: 300, 32: 300, 33: 500}, owner)
        check("gold split by damage over every contributor",
              share.get(33) == 454 and sum(share.values()) == 1000, share)
    finally:
        for p in peers:
            try:
                p.call(feparty.session_end)
            except Exception:                          # noqa: BLE001
                pass
            p.leave()
    return H.finish("fe_partyloop_test")


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as e:
        say("FAIL: %s" % e)
        sys.exit(1)
