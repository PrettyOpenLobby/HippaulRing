#!/usr/bin/env python3
"""fe_armour_defence_build.py -- services/fedata/fe-armour-defence.tsv, the
DEFENCE of every armour piece and shield, from fewiki.com's 2006 tables.

    python tools/fe_armour_defence_build.py

WHY IT IS NOT FROM THE CLIENT (2026-09-12): FE_ITEM_DATA has no defence
number. Its armour-only +0x72 (0..6) follows the shop TIER, not defence --
at +0x72 = 6 the body armour of the three classes is 28 / 21 / 19. SE's
server held the values; fewiki's `Item/装備品/防具/{頭,鎧,脚,手,足,盾}` pages
published them at launch (columns 名称/防御/Lv/価格).

INPUT (--src): a fewiki-armour-matched-2006.tsv of decoded wiki rows
(Wayback captures 2006-05-26/06-14/08-19 and 2007-02-07), each already
matched to the client items of the same NAME:
    page  capture  section  name  defence  wiki_lv  price  idN/sS/x72=/L/p;...
It also needs your client's DECRYPTED dat.pak (--dat) to read FE_ITEM_DATA.

RULES:
  * 2006 captures only. The three 2006 captures agree; the 2007-02 one (FEZ
    era) changed some values (a casual shirt 6 -> 2), so it is excluded.
  * A NAME can be several wiki rows (ヴァイオレットシャツ: def 12 at 450 G and
    def 8 at 786 G) and several client items (two プレートアーマー). Per client
    item: the row with the same PRICE as the client's +0x90; else pair the
    same-name items by LEVEL ORDER (client +0x86 vs wiki Lv); else the LOWER
    value (never overstate a defence). `picked_by` says which.
"""
import argparse
import collections
import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = "fewiki-armour-matched-2006.tsv"
DST = os.path.join(os.path.dirname(HERE), "services", "fedata",
                   "fe-armour-defence.tsv")

sys.path.insert(0, os.path.join(HERE, "fedatagen"))
import fefet  # noqa: E402


def main():
    ap = argparse.ArgumentParser(
        description="build fe-armour-defence.tsv from matched fewiki rows")
    ap.add_argument("--src", default=SRC,
                    help="the matched wiki TSV (default: ./%s)" % SRC)
    ap.add_argument("--dat", default=fefet.DEFAULT_DAT,
                    help="your client's DECRYPTED dat.pak")
    ap.add_argument("--out", default=DST,
                    help="TSV to write (default: services/fedata/"
                         "fe-armour-defence.tsv)")
    a = ap.parse_args()
    blob = open(a.dat, "rb").read()
    items = {r["id"]: r for r in fefet._parsed(blob, b"FE_ITEM_DATA",
                                               fefet.item_record)}
    byname = collections.defaultdict(list)
    for line in io.open(a.src, encoding="utf-8"):
        f = line.rstrip("\r\n").split("\t")
        if len(f) < 8 or not f[1].startswith("2006") or not f[4].isdigit():
            continue
        page, cap, _sec, name, dfn, lv, price, match = f[:8]
        ids = [int(x) for x in re.findall(r"id(\d+)/", match)]
        if ids:
            byname[name].append(dict(
                page=page, cap=cap, d=int(dfn), ids=ids,
                lv=int(lv) if lv.isdigit() else 99,
                price=int(price) if price.isdigit() else 0))
    out, how = {}, collections.Counter()
    for name, rs in byname.items():
        cap = min(r["cap"] for r in rs)
        cand = sorted({(r["lv"], r["price"], r["d"], r["page"])
                       for r in rs if r["cap"] == cap})
        ids = sorted({i for r in rs for i in r["ids"] if i in items},
                     key=lambda i: (items[i]["x86"], i))
        if len({c[2] for c in cand}) == 1:
            for i in ids:
                out[i] = (cand[0], cap, "agree")
                how["agree"] += 1
            continue
        left = list(cand)
        for i in list(ids):
            p = items[i]["x90"]
            hit = [c for c in left if p and c[1] == p]
            if hit:
                out[i] = (hit[0], cap, "price")
                how["price"] += 1
                left.remove(hit[0])
                ids.remove(i)
        if ids and len(left) == len(ids):
            for i, c in zip(ids, sorted(left)):
                out[i] = (c, cap, "level-order")
                how["level-order"] += 1
            continue
        for i in ids:
            c = min(left or cand, key=lambda c: c[2])
            out[i] = (c, cap, "lowest")
            how["lowest"] += 1
    with io.open(a.out, "w", encoding="utf-8", newline="") as fh:
        fh.write("item_id\tname_jp\tdefence\twiki_page\tcapture\twiki_level"
                 "\tpicked_by\n")
        for i in sorted(out):
            (lv, _price, d, page), cap, rule = out[i]
            fh.write("%d\t%s\t%d\t%s\t%s\t%s\t%s\n"
                     % (i, items[i]["name"], d, page, cap,
                        lv if lv != 99 else "", rule))
    arm = sum(1 for r in items.values() if r["slot"] in (1, 3, 4, 5, 6, 7))
    print("%s: %d of %d armour/shield items, picked by %s"
          % (a.out, len(out), arm, dict(how)))


if __name__ == "__main__":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                                  errors="replace")
    main()
