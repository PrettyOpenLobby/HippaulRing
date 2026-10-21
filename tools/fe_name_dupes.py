#!/usr/bin/env python3
"""fe_name_dupes.py -- list Fantasy Earth character names that the name check
(felobby --char-name-check, on by default) would refuse today.

READ ONLY: one SELECT on fe_character, no migration, no write. It reports

  * names held by more than one character, compared case blind (the check
    refuses a new name that matches any existing one this way, NG 0xD005
    err 12), with every holder's account, charid, nation and levels;
  * names that break the creation rule (3-10 half-width letters, digits or
    underscore, at least one letter or digit, not a reserved name; NG err 11).

Existing characters are never renamed or refused at login by the check; this
is the list for the operator to decide on.

    python tools/fe_name_dupes.py               # uses POL_DATABASE_URL
    python tools/fe_name_dupes.py --json        # machine-readable

Inside a running container the file can be fed on stdin:

    docker exec -i <felobby container> python - < tools/fe_name_dupes.py
"""
import json
import os
import sys


def _paths():
    here = os.path.dirname(os.path.abspath(__file__)) if "__file__" in globals() \
        else os.getcwd()
    for cand in (os.path.join(os.path.dirname(here), "services"), here, "/app"):
        if os.path.exists(os.path.join(cand, "fedb.py")) and cand not in sys.path:
            sys.path.insert(0, cand)


def rows():
    _paths()
    from fedb import db          # noqa: E402 -- path set above
    return db.query(
        'SELECT account, charid, name, "force", class_levels, total_score,'
        " created_at, updated_at FROM fe_character ORDER BY LOWER(name), charid")


def report(all_rows, refusal=None):
    """-> {"duplicates": [[row, ...], ...], "invalid": [(row, why), ...]}."""
    by = {}
    for r in all_rows:
        by.setdefault(str(r.get("name") or "").lower(), []).append(r)
    dupes = [v for k, v in sorted(by.items()) if k and len(v) > 1]
    bad = []
    if refusal is not None:
        for r in all_rows:
            why = refusal(str(r.get("name") or ""))
            if why:
                bad.append((r, why))
    return {"duplicates": dupes, "invalid": bad}


def _refusal():
    _paths()
    try:
        import felobby           # noqa: E402
    except Exception:            # noqa: BLE001 -- report the dupes anyway
        return None
    return getattr(felobby, "name_rule_problem", None)


def _line(r):
    return ("  %-12s account=%-14s charid=%-6s force=%-10s levels=%-14s "
            "score=%-5s created=%s updated=%s"
            % (r.get("name"), r.get("account"), r.get("charid"),
               r.get("force"), r.get("class_levels"), r.get("total_score"),
               r.get("created_at"), r.get("updated_at")))


def main(argv):
    rep = report(rows(), _refusal())
    if "--json" in argv:
        print(json.dumps({"duplicates": rep["duplicates"],
                          "invalid": [dict(r, why=w) for r, w in rep["invalid"]]},
                         default=str, indent=1))
        return 0
    print("Names held by more than one character (case blind): %d"
          % len(rep["duplicates"]))
    for grp in rep["duplicates"]:
        print("%r x%d" % (grp[0].get("name"), len(grp)))
        for r in grp:
            print(_line(r))
    print("\nNames that break the creation rule: %d" % len(rep["invalid"]))
    for r, why in rep["invalid"]:
        print(_line(r) + "  (%s)" % why)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
