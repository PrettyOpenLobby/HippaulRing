-- feident's record of the 0xC007 credential blobs (feident.record_blob),
-- formerly data/fe_blobid.jsonl: one row per certification felobby saw, read
-- back by tools/fe_blob_verdict.py. feident keeps the newest FE_BLOB_LOG_MAX
-- rows (default 500) and deletes older ones as it adds.
--
-- `rec` is the record the file had one line for, as JSON with its keys in
-- the order written. `at` and `member_key` are copies of two of its fields,
-- for ordering and for reading by hand.

CREATE TABLE fe_blob_observation (
    id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    at         TEXT,
    member_key TEXT,
    rec        JSON   NOT NULL
);
