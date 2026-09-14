#!/usr/bin/env python3
"""fe_blob_verdict.py -- is FE's 0xC007 credential blob per-ACCOUNT or
per-INSTALL?

Run:  python tools/fe_blob_verdict.py

THE QUESTION, AND WHY IT MATTERS
--------------------------------
FE's character roster is keyed by the POL member that `feident.py` resolves from
the connecting ADDRESS, because the address is the only identity the lobby
socket carries. That works, and it has one documented hole: two POL accounts
behind one address collide, so two people on one machine share a roster.

The 52-byte blob in `0xC007 MSG_CERTIFICATION` might close that hole. fmo.py
records the same polcore-minted blob as "a session token plus a fixed
machine/account identity" -- and *machine* or *account* is the whole question:

  per-ACCOUNT  the fixed field IS the identity. Key on it and the address stops
               mattering: two POL accounts on one machine get two rosters.
  per-INSTALL  it identifies the box. Keying on it would be one roster per
               MACHINE -- a smaller version of the bug feident.py exists to fix,
               and one that looks perfectly correct on any single-account test.

WARNING: THIS CANNOT BE ANSWERED BY STARING AT ONE ACCOUNT'S BLOBS. Four captures from
one machine and one account showed bytes 4..7 identical every time, which is
equally consistent with both answers. The experiment needs TWO DIFFERENT POL
MEMBERS to have each presented a blob -- ideally from the same machine, which is
the case that actually hurts.

WHAT THIS DOES
--------------
Reads `data/fe_blobid.jsonl` (felobby appends one record per certification) and,
rather than trusting the byte window anybody guessed, recomputes from scratch:

  * which byte offsets are INVARIANT within each member's own sessions
    -- candidates for an identity field;
  * of those, which DIFFER between members -- an account-scoped identity;
  * and which are the same for everyone -- install- or build-scoped.

It refuses to answer until the data can actually support an answer, and says
what is missing instead. A verdict from one member is the failure mode here.
"""
import argparse
import collections
import json
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SERVICES = os.path.normpath(os.path.join(_HERE, os.pardir, "services"))
if _SERVICES not in sys.path:
    sys.path.insert(0, _SERVICES)

import feident  # noqa: E402


def load(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
    return [r for r in rows if r.get("full")]


def invariant_offsets(blobs):
    """Byte offsets whose value is the same in every blob given."""
    if not blobs:
        return set()
    n = min(len(b) for b in blobs) // 2
    return {i for i in range(n)
            if len({b[i * 2:i * 2 + 2] for b in blobs}) == 1}


def fmt_ranges(offsets):
    """[4,5,6,7,12] -> '4..7, 12' -- byte ranges read better than a byte list."""
    out, run = [], []
    for i in sorted(offsets):
        if run and i == run[-1] + 1:
            run.append(i)
        else:
            if run:
                out.append(run)
            run = [i]
    if run:
        out.append(run)
    return ", ".join("%d..%d" % (r[0], r[-1]) if len(r) > 1 else str(r[0])
                     for r in out) or "(none)"


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--log", default=feident.BLOB_LOG,
                    help="observations file (default data/fe_blobid.jsonl)")
    args = ap.parse_args()

    rows = load(args.log)
    print("%s -- %d observation(s)\n" % (args.log, len(rows)))
    if not rows:
        print("Nothing recorded yet. felobby appends one line per 0xC007, so\n"
              "launch FE once and this fills in.")
        return 0

    by_member = collections.OrderedDict()
    for r in rows:
        by_member.setdefault(r.get("key") or "?", []).append(r)

    for key, rs in by_member.items():
        nick = next((r.get("nick") for r in rs if r.get("nick")), None)
        ips = sorted({r.get("ip") for r in rs if r.get("ip")})
        print("  %-14s %d session(s)  nick=%s  ip=%s"
              % (key, len(rs), nick, ", ".join(ips) or "?"))
        for r in rs:
            print("      %s  fixed=%s lead=%s" % (r.get("at"), r.get("fixed"),
                                                  r.get("lead")))
    print()

    real = [k for k in by_member if k.startswith("member:")]
    if len(by_member) < 2:
        print("VERDICT: NOT YET ANSWERABLE -- only one identity has presented a\n"
              "blob. One account's captures look identical whether the fixed\n"
              "field names the account or the machine; that is the whole reason\n"
              "this tool exists. Sign in as a SECOND POL account (ideally on the\n"
              "same machine) and launch FE.")
        return 0
    if len(real) < 2:
        print("VERDICT: NOT YET ANSWERABLE -- %d identities seen but only %d\n"
              "resolved to a POL member (%s). An `addr:` key does not name an\n"
              "account, so it cannot answer an account-vs-install question.\n"
              "Check felobby's `identity:` line for why the lookup missed."
              % (len(by_member), len(real), ", ".join(real) or "none"))
        return 0

    # Per-member invariance first: a field that is not stable WITHIN one member
    # cannot be an identity at all, whatever it does between members.
    stable = None
    for key in real:
        got = invariant_offsets([r["full"] for r in by_member[key]])
        stable = got if stable is None else (stable & got)
    print("stable within every member's own sessions: bytes %s"
          % fmt_ranges(stable))

    # Of those, which actually separate the members?
    per_member_vals = {k: {o: by_member[k][0]["full"][o * 2:o * 2 + 2]
                           for o in stable} for k in real}
    differing = {o for o in stable
                 if len({per_member_vals[k][o] for k in real}) > 1}
    shared = stable - differing
    print("  ...and DIFFERENT between members: bytes %s" % fmt_ranges(differing))
    print("  ...identical for everyone:        bytes %s\n" % fmt_ranges(shared))

    same_box = len({r.get("ip") for k in real for r in by_member[k]}) == 1
    print("the two members were on %s\n"
          % ("THE SAME address -- the case that actually hurts" if same_box
             else "DIFFERENT addresses, so a difference here could still be "
                  "the machine rather than the account"))

    # WARNING: HOW MANY SESSIONS EACH MEMBER HAS IS PART OF THE ANSWER. "Stable within
    # a member" over two sessions is one coin flip per byte: with 52 bytes you
    # should EXPECT a fraction of a byte to hold still by luck, and a lone
    # scattered byte doing so is noise, not an identity. A real identity field
    # is contiguous and survives more than two samples. Ignoring this turned a
    # deliberately PER-INSTALL test fixture into a confident "PER-ACCOUNT"
    # verdict off a single stray byte -- caught in testing, and exactly the
    # premature-certainty failure this project keeps paying for.
    depth = min(len(by_member[k]) for k in real)
    runs = []
    for o in sorted(differing):
        if runs and o == runs[-1][-1] + 1:
            runs[-1].append(o)
        else:
            runs.append([o])
    best = max((len(r) for r in runs), default=0)
    expected_noise = len(stable) * (256.0 ** -(depth - 1)) if depth > 1 else 999

    print("sessions per member: %d (each 'stable' byte is %s)"
          % (depth,
             "certain to be coincidence -- one sample proves nothing"
             if depth < 2 else
             "a 1-in-%d coincidence at this depth" % int(256 ** (depth - 1))))
    print("longest CONTIGUOUS differing run: %d byte(s)\n" % best)

    conclusive = differing and depth >= 3 and best >= 2
    if differing and not conclusive:
        why = []
        if depth < 3:
            why.append("only %d session(s) per member -- a byte can hold still "
                       "by luck at this depth (expect ~%.1f of the %d stable "
                       "bytes to be noise)"
                       % (depth, expected_noise, len(stable)))
        if best < 2:
            why.append("the differing bytes are SCATTERED, not a contiguous "
                       "field -- an identity is a run of bytes, and isolated "
                       "ones are what coincidence looks like")
        print("VERDICT: SUGGESTIVE, NOT CONCLUSIVE. Bytes %s differ between\n"
              "members, but this data cannot carry the weight:\n"
              % fmt_ranges(differing))
        for w in why:
            print("  - %s" % w)
        print("\nGet each member to a third FE launch and re-run. Do NOT key on\n"
              "these bytes yet: a wrong call here is one roster per machine,\n"
              "which passes every single-account test.")
    elif conclusive:
        print("VERDICT: PER-ACCOUNT. Bytes %s are a contiguous field, stable\n"
              "across each member's own %d sessions and different between\n"
              "members -- an account-scoped identity carried on the FE socket.\n"
              % (fmt_ranges(differing), depth))
        if not same_box:
            print("WARNING: HOLD THE CHAMPAGNE: the members were on DIFFERENT\n"
                  "addresses, so those bytes could identify the MACHINE and\n"
                  "merely correlate with the account. Repeat with two accounts\n"
                  "on ONE machine before building on this.")
        else:
            print("Same machine, so this is the result that closes the\n"
                  "per-address hole: key on these bytes and two POL accounts on\n"
                  "one box stop sharing a roster. Confirm on a third account\n"
                  "before rewiring feident -- two points also fit a coin flip.")
    else:
        print("VERDICT: PER-INSTALL (or per-build). Every byte that is stable\n"
              "within a member is also identical BETWEEN members, so nothing in\n"
              "this blob separates two accounts on one machine. Do not key on\n"
              "it -- that would be one roster per machine, which looks correct\n"
              "on every single-account test and is the same bug in miniature.\n\n"
              "The remaining lead is the varying tail: fmo.py calls it a session\n"
              "token minted by polcore, 'whose auth our own server already mints\n"
              "elsewhere'. Correlating it against session.token / session.iv is\n"
              "the next thing to try, and we control that side.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
