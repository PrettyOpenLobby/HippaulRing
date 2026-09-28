-- The Fantasy Earth player database (festore.py): one row per character.
--
-- This is festore's SQLite schema version 2 (fe.db) carried over whole. The
-- integer columns are BIGINT because SQLite's INTEGER is 64-bit and several of
-- these hold unsigned 32-bit wire values (exp, the w/f fields). The lists stay
-- JSON text, read and written whole, as they were.

CREATE TABLE fe_character (
    -- feident's resolved store key ("member:3", or "addr:<ip>" when no POL
    -- session names the box)
    account         TEXT    NOT NULL,
    -- the slot key the client echoes back and the unit login id; unique across
    -- the whole table (festore.next_charid), not just one account
    charid          BIGINT  NOT NULL,
    slot_ord        INTEGER NOT NULL DEFAULT 0,    -- roster order, oldest first

    -- identity, as the 0xD002 character record carries it
    name            TEXT,
    unit            BIGINT,
    sex             BIGINT,
    look1           BIGINT,
    look2           BIGINT,
    look3           BIGINT,
    look4           BIGINT,
    f24             BIGINT,
    f28             BIGINT,
    f2d             BIGINT,
    s38             TEXT,

    -- the nation: 1..5, 0x7FFFFFFF is the client's "none"
    "force"         BIGINT,

    -- the suspension block the client prints verbatim (YYMMDDHH)
    period_from     BIGINT,
    period_to       BIGINT,
    comment         TEXT,

    -- the economy; NULL means "no stored value, seed from the flag", which is
    -- not the same as a stored 0
    gold            BIGINT,
    ring            BIGINT,
    crystal         BIGINT,
    total_score     BIGINT,

    tutorial        BIGINT,
    profile_comment TEXT,

    -- progression
    exp             BIGINT,
    class_levels    TEXT,                          -- {class index: level}, JSON
    bag_size        BIGINT,

    -- the lists, JSON text
    equip           TEXT,
    items           TEXT,
    skills          TEXT,
    palette         TEXT,
    blacklist       TEXT,

    -- every key without a column of its own, kept verbatim as JSON
    extra           TEXT,

    created_at      TEXT,
    updated_at      TEXT,
    PRIMARY KEY (account, charid)
);

CREATE INDEX fe_character_account ON fe_character (account, slot_ord);
