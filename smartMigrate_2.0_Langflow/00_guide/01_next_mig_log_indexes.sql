-- Run once in the schema that owns NEXT_MIG_LOG, after checking that an
-- equivalent index does not already exist.
--
-- Primary management lookup: one MAP_ID, newest log first.
-- This supports failure analysis such as MAP_ID=101 / 101.0 followed by the
-- most recent NEXT_MIG_LOG rows.  LOG_TYPE is intentionally not leading:
-- the management tool does not always restrict it.
CREATE INDEX NEXT_MIG_LOG_IDX1
    ON NEXT_MIG_LOG (MAP_ID, LOG_ID DESC);

-- Do not create (LOG_TYPE, LOG_ID DESC) unless an execution plan confirms
-- that queries commonly have an equality predicate on LOG_TYPE.  It does not
-- accelerate MAP_ID-specific failure analysis.
