"""The Fantasy Earth title plugin for the OpenLobby core.

The game itself runs as its own services (fellb, felobby, feworld, femap).
This module is the part of Fantasy Earth that lives INSIDE the core's login
process: the content profile the Viewer shows for a Fantasy Earth Content ID
(prof_011.pfb), built from the player database (festore.py) that felobby and
feworld write.

Loaded with POL_TITLES=fetitle in the core's login and authsess services; see
docker-compose.title.yml. FE_DB names the database (the compose override
points it at the shared data volume).
"""
import os

import titles
import festore

#: the N of prof_011.pfb
CONTENT_CODE = 11

#: the profile's slots (prof_011.pfb): Player Name, Sex, World, Nation, Class, Level
SLOT_NAME, SLOT_SEX, SLOT_WORLD, SLOT_NATION, SLOT_CLASS, SLOT_LEVEL = 3, 4, 5, 6, 7, 8

#: The three player classes, from `fegamedata.item_classes` (the RoD classes,
#: the only rows PLAYER_CLASSES names). The index is the stored `look1`, which
#: is a CLASS, not appearance: see `feworld.self_class_id`.
CLASSES = {0: "Warrior", 1: "Scout", 2: "Sorcerer"}

#: The nations, in the client's own id order: the same defaults
#: `feworld.py --forces` serves to the nation-select screen, so the profile and
#: the game agree on what nation 1 is called.
NATIONS = {1: "Netzawar", 2: "Cathedira", 3: "Elsord", 4: "Holdein", 5: "Gebrand"}

#: The world the player is on. The game's own source of truth is
#: `felobby.py --worlds`; POL_FE_WORLD overrides this copy.
WORLD_NAME = "PlayOnline"


def profile_of(row):
    """`{slot: value}` for one roster row. Only what the row holds: an unset
    field reads as "not filled in", an invented value reads as fact."""
    out = {}
    if row.get("name"):
        out[SLOT_NAME] = row["name"]
    out[SLOT_SEX] = "Male" if not int(row.get("sex") or 0) else "Female"
    nation = NATIONS.get(int(row.get("force") or 0))
    if nation:
        out[SLOT_NATION] = nation
    world = os.environ.get("POL_FE_WORLD", WORLD_NAME).strip()
    if world:
        out[SLOT_WORLD] = world
    cls = row.get("look1")
    cls = int(cls) & 0xFF if cls is not None else None
    if cls in CLASSES:
        out[SLOT_CLASS] = CLASSES[cls]
    levels = row.get("class_levels") or {}
    if isinstance(levels, dict) and cls is not None:
        lvl = levels.get(str(cls), levels.get(cls))
        if lvl is not None:
            out[SLOT_LEVEL] = str(int(lvl))
    return out


class FantasyEarth(titles.Title):
    tag = b"FE0"
    content_code = CONTENT_CODE

    def describe(self):
        return f"Fantasy Earth player database {festore.DB_PATH}"

    def profile_fields(self, cid, member_id):
        if member_id is None:
            return {}
        # the store raises StoreUnavailable rather than answering "no
        # characters" for a read that failed; the core logs it and leaves the
        # fields unset
        rows = festore.load_roster(f"member:{member_id}") or []
        if not rows:
            return {}
        return profile_of(rows[0])


def register():
    return titles.register(FantasyEarth())
