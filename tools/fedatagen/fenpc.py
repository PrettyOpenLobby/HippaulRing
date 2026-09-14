"""Dump FE's NPC_ModelType table -- the id `--monster TYPE` takes.

Reads dat.pak from YOUR OWN Fantasy Earth client install; pass its path
(usually <CLIENT>/DATA/dat.pak) with --pak.

    python fenpc.py --pak /path/to/DATA/dat.pak            # the whole table
    python fenpc.py --pak /path/to/DATA/dat.pak --monsters # bestiary rows only
    python fenpc.py --pak /path/to/DATA/dat.pak --tsv fe-npc-modeltypes.tsv

WHY THIS EXISTS. `0x1006` entity type 8 (CFeClientNPCObject) carries ONE u16
that decides what the thing looks like: the client hands it to 0x51ab3f0, which
searches the table parsed out of `dat\\data_NPC_ModelType.dat`, and on a miss
logs

    ERROR : CFeClientNPCObject::Initialize() get Modeltype type = %d

and builds nothing. So the id is not free-form and guessing one is how you get
a monster that never appears.

THE RECORD, read off the parser at 0x051aaf60 (it compares a length-prefixed
class tag against "NPC_ModelType" before reading the rest):

    [u32 len]"NPC_ModelType" [u32 id]
    [u32 len]"Model\\<x>.mdl" [u32 len]"Model\\<x>.tex"
    [u32 len]"Motion\\<y>.ans" [u32 len]"Motion\\<x>.msd"
    [i32 -1] [i32 a] [i32 b] [i32 c] [i32 d] [i32 e] [f32 f] [f32 g]

WARNING: THE TRAILING SCALARS ARE NOT DECODED. `a`/`b` are -1 on every town NPC and a
small number shared across each monster family, which is consistent with a
name/description pair in some string table -- and consistent is not measured.
Do not put a name on them from this file. The two floats track model bulk
(dem_00_l, the biggest model in the set, carries the largest pair), which is
also only consistent.

WARNING: MEMBER BOUNDARIES SLICE MID-RECORD. The pak's TOC entry for
data_NPC_ModelType.dat starts partway through a record and the neighbouring
member (data_METAMORPHOSIS_DATA.dat) contains more of the same rows, so this
scans the WHOLE archive for the class tag instead of trusting one member.
"""
import argparse
import os
import struct
import sys

#: Default input: dat.pak in the current directory. Point --pak at the copy
#: in your client install (<CLIENT>/DATA/dat.pak).
DAT = "dat.pak"
TAG = b"NPC_ModelType"

# The model-name prefixes, from the archives they live in. Named from the
# models themselves, not from any string table -- see the warning above.
FAMILY = [
    ("gob", "goblin"), ("goa", "goblin archer"), ("orc", "orc"),
    ("und", "undead"), ("dra", "dragon"), ("gri", "griffin"),
    ("har", "harpy"), ("sal", "salamander"), ("ven", "venom"),
    ("vol", "vol-"), ("dem", "demon"), ("chi", "chimera"), ("gia", "giant"),
    ("kni", "knight"), ("wra", "wraith"), ("fai", "fai-"), ("ulf", "ulf-"),
    ("exe", "exe-"), ("scb", "scb-"), ("ggal", "ggal-"), ("ifn", "ifn-"),
    ("Npc", "town NPC"),
]


def family_of(model):
    stem = model.rsplit(".", 1)[0]
    for pre, name in FAMILY:
        if stem.startswith(pre):
            return name
    return "?"


def rows(blob):
    def lstr(o):
        n = struct.unpack_from("<I", blob, o)[0]
        return blob[o + 4:o + 4 + n].decode("latin1"), o + 4 + n

    marks, o = [], 0
    while True:
        i = blob.find(TAG, o)
        if i < 0:
            break
        if i >= 4 and struct.unpack_from("<I", blob, i - 4)[0] == len(TAG):
            marks.append(i - 4)
        o = i + 1
    out = []
    for k, m in enumerate(marks):
        try:
            _, o = lstr(m)
            tid = struct.unpack_from("<I", blob, o)[0]
            o += 4
            paths = []
            for _ in range(4):
                s, o = lstr(o)
                paths.append(s)
            end = marks[k + 1] if k + 1 < len(marks) else min(o + 32, len(blob))
            tail = blob[o:end]
            ints = struct.unpack_from("<6i", tail) if len(tail) >= 24 else ()
            fl = struct.unpack_from("<2f", tail, 24) if len(tail) >= 32 else ()
            out.append((tid, [p.replace("\\", "/").split("/")[-1] for p in paths],
                        ints, fl))
        except Exception as e:              # a truncated tail is not fatal
            print("  ! record at %d: %s" % (m, e), file=sys.stderr)
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pak", default=DAT,
                    help="path to your client install's dat.pak "
                         "(default: ./dat.pak)")
    ap.add_argument("--monsters", action="store_true",
                    help="only rows whose first two scalars are not -1 -- the "
                         "ones with a bestiary entry, i.e. not town NPCs")
    ap.add_argument("--tsv", metavar="PATH", help="also write a TSV")
    a = ap.parse_args()
    if not os.path.exists(a.pak):
        sys.exit("no dat.pak at %s" % a.pak)
    rs = rows(open(a.pak, "rb").read())
    if a.monsters:
        rs = [r for r in rs if r[2] and r[2][1] != -1]
    print("%-5s %-16s %-14s %-16s %s" % ("id", "model", "family", "motion", "scalars"))
    for tid, paths, ints, fl in sorted(rs):
        print("%-5d %-16s %-14s %-16s %s %s"
              % (tid, paths[0], family_of(paths[0]), paths[2],
                 ints, tuple(round(x, 2) for x in fl)))
    print("\n%d rows (%d monsters, %d town NPCs)"
          % (len(rs),
             sum(1 for r in rs if r[2] and r[2][1] != -1),
             sum(1 for r in rs if r[2] and r[2][1] == -1)))
    if a.tsv:
        with open(a.tsv, "w", encoding="utf-8", newline="") as fh:
            # walk_ups/run_ups (2026-09-12): the two floats rows() always read
            # (NPC_ModelType +0x41c/+0x420, the monster move's own walk and run
            # speeds) and this export silently DROPPED -- so for weeks the
            # server's monster speed was a "chosen" knob while the shipped
            # value sat in memory one column to the right.
            fh.write("id\tmodel\tfamily\ttexture\tmotion_ans\tmotion_msd\tscalars\twalk_ups\trun_ups\n")
            for tid, paths, ints, fl in sorted(rs):
                fh.write("%d\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n"
                         % (tid, paths[0], family_of(paths[0]), paths[1],
                            paths[2], paths[3],
                            " ".join(str(x) for x in ints),
                            ("%.4f" % fl[0]) if fl else "",
                            ("%.4f" % fl[1]) if fl else ""))
        print("wrote %s" % a.tsv)


if __name__ == "__main__":
    main()
