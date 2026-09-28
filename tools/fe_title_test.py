#!/usr/bin/env python3
"""The Fantasy Earth title plugin: the Viewer's profile out of the player database.

    python tools/fe_title_test.py

Needs the OpenLobby core checked out beside this repository (or OPENLOBBY_DIR
pointing at it) for `titles.py`. Offline; uses a database in a temp directory.

Each check is a regression that has already happened once: a store read as
JSON after the data moved into sqlite (the read kept working and kept
returning migration-day values), a swapped key read by name (`nation` is the
gender byte), an index passed between two ladders with different strides.
"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OPENLOBBY = os.environ.get("OPENLOBBY_DIR", os.path.join(ROOT, os.pardir, "openlobby"))
sys.path.insert(0, os.path.join(OPENLOBBY, "services"))
sys.path.insert(0, os.path.join(ROOT, "services"))

TMP = tempfile.mkdtemp(prefix="fe-title-")
os.environ["FE_DB"] = os.path.join(TMP, "fe.db")
os.environ.pop("POL_FE_WORLD", None)

import titles          # noqa: E402
import festore         # noqa: E402
import fetitle         # noqa: E402

MEMBER = 7
FAILS = []


def check(ok, label, detail=""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f"  --  {detail}" if detail else ""))
    if not ok:
        FAILS.append(label)


def main():
    t = fetitle.register()
    check(titles.for_code(11) is t, "registers as content code 11")
    check(titles.profile_fields(11, 30000011, MEMBER) == {},
          "a member with no characters: nothing, not a guess")
    check(titles.profile_fields(11, 30000011, None) == {},
          "no member id: nothing")

    # `charid` is NOT decoration: the store's PRIMARY KEY is (account, charid)
    festore.save_roster(f"member:{MEMBER}", [
        {"charid": 1, "name": "Elle", "sex": 1, "force": 3, "look1": 2,
         "class_levels": {"2": 27}},
        {"charid": 2, "name": "Second", "sex": 0, "force": 1, "look1": 0},
    ])
    f = titles.profile_fields(11, 30000011, MEMBER)
    check(f.get(fetitle.SLOT_NAME) == "Elle" and f.get(fetitle.SLOT_SEX) == "Female"
          and f.get(fetitle.SLOT_NATION) == "Elsord",
          "name, sex and nation of the FIRST character, off the database", repr(f))
    check(f.get(fetitle.SLOT_CLASS) == "Sorcerer", "class from look1 (a class, not a look)")
    check(f.get(fetitle.SLOT_LEVEL) == "27", "level from the class_levels table, as text")
    check(f.get(fetitle.SLOT_WORLD) == "PlayOnline", "the world name")

    os.environ["POL_FE_WORLD"] = "Elsewhere"
    check(titles.profile_fields(11, 30000011, MEMBER).get(fetitle.SLOT_WORLD) == "Elsewhere",
          "POL_FE_WORLD overrides the world name")
    os.environ.pop("POL_FE_WORLD")

    festore.save_roster(f"member:{MEMBER}", [
        {"charid": 1, "name": "Elle", "sex": 1, "force": 3, "look1": 1},
    ])
    f = titles.profile_fields(11, 30000011, MEMBER)
    check(fetitle.SLOT_CLASS in f and fetitle.SLOT_LEVEL not in f,
          "a class with no level row: class set, level UNSET (not 0)")

    print()
    if FAILS:
        print(f"FAILED: {len(FAILS)}: " + ", ".join(FAILS))
        return 1
    print("all Fantasy Earth title checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
