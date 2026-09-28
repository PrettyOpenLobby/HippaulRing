-- The Fantasy Earth world state (festate.py): the stores the game rewrites
-- while it runs, formerly JSON files in /data. One row per store, holding the
-- whole document the file held:
--
--   campaign      fe_campaign.json      the war cycle per area, nation records
--   territory     fe_territory.json     which nation holds each area
--   spawn         fe_spawn.json         arrival points per area (!spawn)
--   town          fe_town.json          placed NPCs, buildings, doors (!npc ...)
--   door_arrive   fe_door_arrive.json   where a door puts a player down
--   minimap_cal   fe_minimap_cal.json   the minimap calibration anchors
--   force         fe_force.json         force rows and the judge policy (!force)
--   map_discord   fe_map_discord.json   the Discord map message being edited
--
-- JSON, not JSONB: the document comes back with its keys in the order it was
-- written, and some of it is served to a client or a page as stored.

CREATE TABLE fe_world_state (
    name       TEXT        PRIMARY KEY,
    doc        JSON        NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
