-- EXAMPLES ONLY. Run as target/management owner (TGT_NEW); replace project names.
-- Run selected statements as the target/management owner (TGT_NEW), after reviewing existing keys.
-- Business tables must already exist. This script does not run migration.
SET DEFINE OFF;
WHENEVER SQLERROR EXIT SQL.SQLCODE ROLLBACK;

INSERT INTO NEXT_MIG_INFO
    (MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, USE_YN, PRIORITY, TRUNC_YN)
VALUES
    (1001, 'SIMPLE', 'SRC_NEW.SRC_CUSTOMER', 'TGT_NEW.TGT_MEMBER', 'Y', 5, 'N');

INSERT INTO NEXT_MIG_INFO_DTL (MAP_ID, MAP_DTL, FR_COL, TO_COL)
VALUES (1001, 1, 'CUSTOMER_ID', 'MEMBER_ID');
INSERT INTO NEXT_MIG_INFO_DTL (MAP_ID, MAP_DTL, FR_COL, TO_COL)
VALUES (1001, 2, 'CUSTOMER_NAME', 'MEMBER_NAME');

-- SQL jobs are separate from table/column mappings.
-- TARGET_TABLE is the AS-IS table scope used for conversion mapping lookup.
INSERT INTO NEXT_SQL_INFO
    (SQL_SEQ, SPACE_NM, SQL_ID, TAG_KIND, TARGET_TABLE, FR_SQL, PRIORITY, USER_EDITED)
VALUES
    (NEXT_SQL_INFO_SQL_SEQ.NEXTVAL, 'NEW_PROJECT', 'CUSTOMER_SELECT_001', 'SELECT',
     'SRC_NEW.SRC_CUSTOMER',
     'SELECT CUSTOMER_ID, CUSTOMER_NAME FROM SRC_NEW.SRC_CUSTOMER WHERE CUSTOMER_ID = :customer_id',
     5, 'N');

-- Validate before committing. For SQL Developer execute the selected statements only.
SELECT MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, TRUNC_YN FROM NEXT_MIG_INFO WHERE MAP_ID = 1001;
SELECT MAP_ID, MAP_DTL, FR_COL, TO_COL FROM NEXT_MIG_INFO_DTL WHERE MAP_ID = 1001 ORDER BY MAP_DTL;
SELECT SQL_SEQ, SPACE_NM, SQL_ID, TARGET_TABLE FROM NEXT_SQL_INFO WHERE SPACE_NM = 'NEW_PROJECT';
-- COMMIT; -- Execute explicitly after review; otherwise ROLLBACK.
