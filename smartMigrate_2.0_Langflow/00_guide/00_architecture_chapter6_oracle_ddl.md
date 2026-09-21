# Chapter 6. Oracle & Milvus Schema Reference

이 문서는 SmartMigrate가 사용하는 Oracle 기준 테이블과 sequence의 배포 DDL을 정리한다. 테이블명 앞에는 운영 환경의 `system_schema`를 붙여 사용할 수 있다.

## 6.1 Object 목록

| Object | 유형 | 용도 |
|---|---|---|
| `NEXT_MIG_INFO` | Table | DB Migration 작업 master, 생성 SQL, 검증 SQL, 실행 상태 저장 |
| `NEXT_MIG_INFO_DTL` | Table | DB Migration 컬럼 매핑 detail 저장 |
| `NEXT_SQL_INFO` | Table | SQL Conversion, SQL Tuning, SQL Formatting 대상과 산출물 저장 |
| `NEXT_MIG_RAG_INFO` | Table | SQL Conversion/Tuning RAG Guide 원천 저장 |
| `NEXT_MIG_LOG` | Table | 전체 workflow, 관리, 실행 로그 저장 |
| `MIGRATION_LOG_SEQ` | Sequence | `NEXT_MIG_LOG.LOG_ID` 채번 |

## 6.2 NEXT_MIG_INFO

```sql
CREATE TABLE NEXT_MIG_INFO (
    MAP_ID          NUMBER          NOT NULL,
    MAP_TYPE        VARCHAR2(100),
    FR_TABLE        VARCHAR2(4000)  NOT NULL,
    TO_TABLE        VARCHAR2(4000)  NOT NULL,
    CONDITION       CLOB,
    USE_YN          CHAR(1)         DEFAULT 'Y',
    PRIORITY        NUMBER          DEFAULT 5,
    PRIOR_MAP_ID    NUMBER,
    STATUS          VARCHAR2(100),
    USER_EDITED     CHAR(1)         DEFAULT 'N',
    MIG_SQL         CLOB,
    VERIFY_SQL      CLOB,
    BATCH_CNT       NUMBER          DEFAULT 0,
    RETRY_COUNT     NUMBER          DEFAULT 0,
    ELAPSED_SECONDS NUMBER,
    LOG             VARCHAR2(4000),
    REG_TS          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    UPD_TS          TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT PK_NEXT_MIG_INFO PRIMARY KEY (MAP_ID),
    CONSTRAINT CK_NEXT_MIG_INFO_USE_YN CHECK (USE_YN IN ('Y', 'N')),
    CONSTRAINT CK_NEXT_MIG_INFO_USER_EDITED CHECK (USER_EDITED IN ('Y', 'N'))
);
```

```sql
COMMENT ON TABLE NEXT_MIG_INFO IS 'DB Migration 작업 master, 매핑 범위, 생성 SQL, 검증 SQL, 실행 상태를 저장한다.';
COMMENT ON COLUMN NEXT_MIG_INFO.MAP_ID IS 'DB Migration 작업 식별자. 단건 실행, 조회, 상태 초기화의 기준 key.';
COMMENT ON COLUMN NEXT_MIG_INFO.MAP_TYPE IS '매핑 유형. SIMPLE, COMPLEX 등 migration rule 분류값.';
COMMENT ON COLUMN NEXT_MIG_INFO.FR_TABLE IS 'AS-IS source table 또는 source SQL scope.';
COMMENT ON COLUMN NEXT_MIG_INFO.TO_TABLE IS 'TO-BE target table.';
COMMENT ON COLUMN NEXT_MIG_INFO.CONDITION IS 'Migration SQL 생성 시 source row 범위를 제한하는 조건.';
COMMENT ON COLUMN NEXT_MIG_INFO.USE_YN IS '작업 사용 여부. Y인 row만 실행 대상으로 본다.';
COMMENT ON COLUMN NEXT_MIG_INFO.PRIORITY IS '실행 우선순위. 1이 높음, 기본값은 5.';
COMMENT ON COLUMN NEXT_MIG_INFO.PRIOR_MAP_ID IS '선행 완료가 필요한 DB Migration MAP_ID.';
COMMENT ON COLUMN NEXT_MIG_INFO.STATUS IS 'DB Migration 실행 상태. PASS, RUNNING, FAIL-* 등을 저장한다.';
COMMENT ON COLUMN NEXT_MIG_INFO.USER_EDITED IS '담당자가 SQL을 직접 보정했는지 여부.';
COMMENT ON COLUMN NEXT_MIG_INFO.MIG_SQL IS '생성 또는 담당자 보정된 migration INSERT SQL.';
COMMENT ON COLUMN NEXT_MIG_INFO.VERIFY_SQL IS 'Migration 결과 검증 SQL.';
COMMENT ON COLUMN NEXT_MIG_INFO.BATCH_CNT IS '작업 실행 시작 횟수.';
COMMENT ON COLUMN NEXT_MIG_INFO.RETRY_COUNT IS '현재 작업의 retry 횟수.';
COMMENT ON COLUMN NEXT_MIG_INFO.ELAPSED_SECONDS IS '마지막 실행 소요 시간 초 단위.';
COMMENT ON COLUMN NEXT_MIG_INFO.LOG IS '마지막 실행 결과 또는 오류 요약.';
COMMENT ON COLUMN NEXT_MIG_INFO.REG_TS IS 'row 최초 등록 시각.';
COMMENT ON COLUMN NEXT_MIG_INFO.UPD_TS IS 'row 마지막 갱신 시각.';
```

```sql
CREATE INDEX IX_NEXT_MIG_INFO_STATUS ON NEXT_MIG_INFO (USE_YN, STATUS, PRIORITY);
CREATE INDEX IX_NEXT_MIG_INFO_PRIOR ON NEXT_MIG_INFO (PRIOR_MAP_ID);
CREATE INDEX IX_NEXT_MIG_INFO_TABLES ON NEXT_MIG_INFO (SUBSTR(FR_TABLE, 1, 200), SUBSTR(TO_TABLE, 1, 200));
```

## 6.3 NEXT_MIG_INFO_DTL

```sql
CREATE TABLE NEXT_MIG_INFO_DTL (
    MAP_ID NUMBER          NOT NULL,
    FR_COL VARCHAR2(4000)  NOT NULL,
    TO_COL VARCHAR2(4000),
    REG_TS TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    UPD_TS TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT PK_NEXT_MIG_INFO_DTL PRIMARY KEY (MAP_ID, FR_COL)
);
```

```sql
COMMENT ON TABLE NEXT_MIG_INFO_DTL IS 'DB Migration 컬럼 매핑 detail을 저장한다.';
COMMENT ON COLUMN NEXT_MIG_INFO_DTL.MAP_ID IS 'NEXT_MIG_INFO.MAP_ID와 연결되는 mapping master 식별자.';
COMMENT ON COLUMN NEXT_MIG_INFO_DTL.FR_COL IS 'AS-IS source column.';
COMMENT ON COLUMN NEXT_MIG_INFO_DTL.TO_COL IS 'TO-BE target column. NULL, blank, NONE, N/A, NA, - 값은 미사용 컬럼으로 해석한다.';
COMMENT ON COLUMN NEXT_MIG_INFO_DTL.REG_TS IS 'row 최초 등록 시각.';
COMMENT ON COLUMN NEXT_MIG_INFO_DTL.UPD_TS IS 'row 마지막 갱신 시각.';
```

```sql
CREATE INDEX IX_NEXT_MIG_INFO_DTL_MAP ON NEXT_MIG_INFO_DTL (MAP_ID);
```

## 6.4 NEXT_SQL_INFO

```sql
CREATE TABLE NEXT_SQL_INFO (
    SQL_SEQ           NUMBER          NOT NULL,
    SPACE_NM          VARCHAR2(200)   NOT NULL,
    SQL_ID            VARCHAR2(200)   NOT NULL,
    REF_SEQ           NUMBER,
    STATUS_CONVERSION VARCHAR2(100),
    STATUS_TUNING     VARCHAR2(100),
    PRIORITY          NUMBER          DEFAULT 5,
    USER_EDITED       CHAR(1)         DEFAULT 'N',
    TARGET_TABLE      VARCHAR2(4000),
    TAG_KIND          VARCHAR2(100),
    FR_SQL            CLOB,
    EDIT_FR_SQL       CLOB,
    TO_SQL            CLOB,
    BIND_SQL          CLOB,
    BIND_SET          CLOB,
    TEST_SQL          CLOB,
    TUNED_TO_SQL      CLOB,
    TUNED_RESULT      CLOB,
    TUNED_FR_SQL      CLOB,
    FORMATTED_SQL     CLOB,
    BLOCK_RAG_CONTENT CLOB,
    BATCH_CNT         NUMBER          DEFAULT 0,
    RETRY_COUNT       NUMBER          DEFAULT 0,
    LOG               VARCHAR2(4000),
    UPD_TS            TIMESTAMP       DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT PK_NEXT_SQL_INFO PRIMARY KEY (SPACE_NM, SQL_ID),
    CONSTRAINT UQ_NEXT_SQL_INFO_SQL_SEQ UNIQUE (SQL_SEQ),
    CONSTRAINT CK_NEXT_SQL_INFO_REF_SEQ CHECK (REF_SEQ IS NULL OR REF_SEQ <> SQL_SEQ),
    CONSTRAINT CK_NEXT_SQL_INFO_USER_EDITED CHECK (USER_EDITED IN ('Y', 'N'))
);
```

```sql
COMMENT ON TABLE NEXT_SQL_INFO IS 'SQL Conversion, SQL Tuning, SQL Formatting 대상 SQL과 산출물을 저장한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.SQL_SEQ IS '사용자 지정 SQL 단건 번호. 최초 backfill은 SQL_ID, SPACE_NM 정렬 순번으로 부여하며 이후 변경하지 않는다.';
COMMENT ON COLUMN NEXT_SQL_INFO.SPACE_NM IS 'SQL job 업무 영역 또는 namespace. SQL_ID와 함께 단건 식별 key로 사용한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.SQL_ID IS 'SQL job 식별자. SPACE_NM과 함께 단건 식별 key로 사용한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.REF_SEQ IS '사용자 지정 Correct SQL 참고 대상으로 선택한 NEXT_SQL_INFO.SQL_SEQ. 지정 전 해당 row가 기존 Correct SQL 컬렉션에 존재하는지 검증한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.TAG_KIND IS 'SQL 유형 또는 처리 태그.';
COMMENT ON COLUMN NEXT_SQL_INFO.FR_SQL IS 'AS-IS 원본 SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.EDIT_FR_SQL IS '담당자가 보정한 AS-IS 원본 SQL. 존재하면 FR_SQL보다 우선한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.TARGET_TABLE IS 'SQL Conversion에서 mapping rule과 Conversion RAG를 찾기 위한 AS-IS table scope.';
COMMENT ON COLUMN NEXT_SQL_INFO.TO_SQL IS 'SQL Conversion으로 생성 또는 보정된 TO-BE SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.BIND_SQL IS 'Bind parameter 추출 또는 검증용 SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.BIND_SET IS 'Bind case/result set 정보.';
COMMENT ON COLUMN NEXT_SQL_INFO.TEST_SQL IS 'Conversion 또는 Tuning 결과 검증 SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.TUNED_TO_SQL IS 'SQL Tuning으로 생성 또는 보정된 TO-BE SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.TUNED_RESULT IS 'Tuning 결과 설명, 변경 사유, 검증 요약.';
COMMENT ON COLUMN NEXT_SQL_INFO.TUNED_FR_SQL IS 'Tuning 기준이 되는 원본 또는 보정 SQL.';
COMMENT ON COLUMN NEXT_SQL_INFO.FORMATTED_SQL IS 'SQL Formatting 결과.';
COMMENT ON COLUMN NEXT_SQL_INFO.BLOCK_RAG_CONTENT IS 'SQL Tuning block별로 사용된 RAG 검색 결과 요약.';
COMMENT ON COLUMN NEXT_SQL_INFO.STATUS_CONVERSION IS 'SQL Conversion 상태. PASS-CONVERSION, RUNNING, FAIL-* 등을 저장한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.STATUS_TUNING IS 'SQL Tuning 상태. PASS-TUNING, RUNNING, FAIL-* 등을 저장한다.';
COMMENT ON COLUMN NEXT_SQL_INFO.USER_EDITED IS '담당자가 SQL을 직접 보정했는지 여부.';
COMMENT ON COLUMN NEXT_SQL_INFO.PRIORITY IS '실행 우선순위. 1이 높음, 기본값은 5.';
COMMENT ON COLUMN NEXT_SQL_INFO.BATCH_CNT IS 'SQL job 실행 시작 횟수.';
COMMENT ON COLUMN NEXT_SQL_INFO.RETRY_COUNT IS '현재 작업의 retry 횟수.';
COMMENT ON COLUMN NEXT_SQL_INFO.LOG IS '마지막 실행 결과 또는 오류 요약.';
COMMENT ON COLUMN NEXT_SQL_INFO.UPD_TS IS 'row 마지막 갱신 시각.';
```

```sql
CREATE INDEX IX_NEXT_SQL_INFO_CONV ON NEXT_SQL_INFO (STATUS_CONVERSION, USER_EDITED, PRIORITY);
CREATE INDEX IX_NEXT_SQL_INFO_TUNING ON NEXT_SQL_INFO (STATUS_TUNING, USER_EDITED, PRIORITY);
CREATE INDEX IX_NEXT_SQL_INFO_TARGET ON NEXT_SQL_INFO (SUBSTR(TARGET_TABLE, 1, 200));
CREATE INDEX IX_NEXT_SQL_INFO_REF_SEQ ON NEXT_SQL_INFO (REF_SEQ);
```

```sql
-- 신규 설치: importer가 신규 row INSERT 시 NEXTVAL을 사용한다.
CREATE SEQUENCE NEXT_SQL_INFO_SQL_SEQ START WITH 1 INCREMENT BY 1 NOCACHE;
```

### `NEXT_SQL_INFO_BACKUP_260921` 복원 INSERT

대상 `NEXT_SQL_INFO`가 비어 있을 때만 실행한다. `SQL_SEQ`는 `SQL_ID`, `SPACE_NM` 오름차순으로 부여하고, 백업에 없는 `REF_SEQ`는 `NULL`로 시작한다.

```sql
SELECT COUNT(*) AS NEXT_SQL_INFO_COUNT FROM NEXT_SQL_INFO;

INSERT INTO NEXT_SQL_INFO (
    SQL_SEQ, SPACE_NM, SQL_ID, REF_SEQ,
    STATUS_CONVERSION, STATUS_TUNING, PRIORITY, USER_EDITED, TARGET_TABLE,
    TAG_KIND, FR_SQL, EDIT_FR_SQL,
    TO_SQL, BIND_SQL, BIND_SET, TEST_SQL,
    TUNED_TO_SQL, TUNED_RESULT, TUNED_FR_SQL,
    FORMATTED_SQL, BLOCK_RAG_CONTENT,
    BATCH_CNT, RETRY_COUNT, LOG, UPD_TS
)
SELECT
    ROW_NUMBER() OVER (ORDER BY SQL_ID ASC, SPACE_NM ASC) AS SQL_SEQ,
    SPACE_NM, SQL_ID, NULL AS REF_SEQ,
    STATUS_CONVERSION, STATUS_TUNING, PRIORITY, USER_EDITED, TARGET_TABLE,
    TAG_KIND, FR_SQL, EDIT_FR_SQL,
    TO_SQL, BIND_SQL, BIND_SET, TEST_SQL,
    TUNED_TO_SQL, TUNED_RESULT, TUNED_FR_SQL,
    FORMATTED_SQL, BLOCK_RAG_CONTENT,
    BATCH_CNT, RETRY_COUNT, LOG, UPD_TS
  FROM NEXT_SQL_INFO_BACK_260921;

COMMIT;

SELECT MAX(SQL_SEQ) + 1 AS NEXT_SQL_SEQ FROM NEXT_SQL_INFO;
-- 복원 절차에서는 위 결과를 <NEXT_SQL_SEQ>에 넣어 START WITH 1 대신 생성한다.
CREATE SEQUENCE NEXT_SQL_INFO_SQL_SEQ START WITH <NEXT_SQL_SEQ> INCREMENT BY 1 NOCACHE;
```

## 6.5 NEXT_MIG_RAG_INFO

```sql
CREATE TABLE NEXT_MIG_RAG_INFO (
    RAG_ID        NUMBER GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    CATEGORY      VARCHAR2(50)    NOT NULL,
    RULE_TYPE     VARCHAR2(50)    NOT NULL,
    SOURCE_TABLES VARCHAR2(4000),
    USE_YN        CHAR(1)         DEFAULT 'Y',
    GUIDANCE_TEXT CLOB,
    SOURCE_SQL    CLOB,
    TARGET_SQL    CLOB,
    HIT_CNT       NUMBER          DEFAULT 0,
    CREATED_AT    TIMESTAMP       DEFAULT SYSTIMESTAMP,
    UPDATED_AT    TIMESTAMP       DEFAULT SYSTIMESTAMP,
    CONSTRAINT PK_NEXT_MIG_RAG_INFO PRIMARY KEY (RAG_ID),
    CONSTRAINT CK_NEXT_MIG_RAG_CATEGORY CHECK (CATEGORY IN ('SQL_CONVERSION', 'SQL_TUNING')),
    CONSTRAINT CK_NEXT_MIG_RAG_RULE_TYPE CHECK (RULE_TYPE IN ('GENERAL', 'SEARCH')),
    CONSTRAINT CK_NEXT_MIG_RAG_USE_YN CHECK (USE_YN IN ('Y', 'N'))
);
```

```sql
COMMENT ON TABLE NEXT_MIG_RAG_INFO IS 'SQL Conversion/Tuning RAG Guide 원천 rule, guidance, SQL 예시를 저장한다.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.RAG_ID IS 'RAG Guide 식별자. 신규 추가 시 DB가 자동 생성한다.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.CATEGORY IS 'RAG 적용 도메인. SQL_CONVERSION 또는 SQL_TUNING.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.RULE_TYPE IS 'RAG 적용 방식. GENERAL은 공통 가이드, SEARCH는 유사 SQL 검색용 예시.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.SOURCE_TABLES IS 'SQL_CONVERSION Guide의 table scope 메타데이터. 범위가 분명할 때만 입력하고, 기본값은 비워 둔다. SQL_TUNING에서는 비워 둔다.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.USE_YN IS 'Guide 사용 여부. 삭제 요청은 물리 삭제가 아니라 N으로 비활성화한다.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.GUIDANCE_TEXT IS '가이드 본문. SQL_TUNING에서는 필수 입력값이다.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.SOURCE_SQL IS 'SEARCH Guide의 원본 SQL 예시.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.TARGET_SQL IS 'SEARCH Guide의 변환 또는 튜닝 후 SQL 예시.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.HIT_CNT IS 'SEARCH Guide가 실행 중 참조된 횟수.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.CREATED_AT IS 'Guide 최초 등록 시각.';
COMMENT ON COLUMN NEXT_MIG_RAG_INFO.UPDATED_AT IS 'Guide 마지막 갱신 시각.';
```

```sql
CREATE INDEX IX_NEXT_MIG_RAG_LOOKUP ON NEXT_MIG_RAG_INFO (CATEGORY, RULE_TYPE, USE_YN);
CREATE INDEX IX_NEXT_MIG_RAG_UPDATED ON NEXT_MIG_RAG_INFO (UPDATED_AT);
```

## 6.6 NEXT_MIG_LOG

```sql
CREATE SEQUENCE MIGRATION_LOG_SEQ START WITH 1 INCREMENT BY 1 NOCACHE NOCYCLE;

CREATE TABLE NEXT_MIG_LOG (
    LOG_ID      NUMBER         NOT NULL,
    MAP_ID      VARCHAR2(200),
    MIG_KIND    VARCHAR2(100),
    LOG_TYPE    VARCHAR2(100),
    LOG_LEVEL   VARCHAR2(50),
    STEP_NAME   VARCHAR2(200),
    STATUS      VARCHAR2(100),
    MESSAGE     CLOB,
    RETRY_COUNT NUMBER         DEFAULT 0,
    GENERATE_SQL CLOB,
    CREATED_AT  TIMESTAMP      DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT PK_NEXT_MIG_LOG PRIMARY KEY (LOG_ID)
);
```

```sql
COMMENT ON TABLE NEXT_MIG_LOG IS 'SmartMigrate 전체 workflow, 관리 요청, 도메인 실행 로그를 저장하는 단일 로그 테이블.';
COMMENT ON COLUMN NEXT_MIG_LOG.LOG_ID IS '로그 식별자. MIGRATION_LOG_SEQ.NEXTVAL로 채번한다.';
COMMENT ON COLUMN NEXT_MIG_LOG.MAP_ID IS '로그 대상 key. DB Migration은 MAP_ID, SQL 계열은 SQL_ID/SPACE_NM 조합 문자열을 저장한다.';
COMMENT ON COLUMN NEXT_MIG_LOG.MIG_KIND IS '로그 도메인. WORKFLOW, DB_MIGRATION, SQL_CONVERSION, SQL_TUNING, SQL_FORMATTING 등.';
COMMENT ON COLUMN NEXT_MIG_LOG.LOG_TYPE IS '로그 유형. INFO, ERROR 등 업무 구분값.';
COMMENT ON COLUMN NEXT_MIG_LOG.LOG_LEVEL IS '로그 레벨.';
COMMENT ON COLUMN NEXT_MIG_LOG.STEP_NAME IS '실행 단계명 또는 component명.';
COMMENT ON COLUMN NEXT_MIG_LOG.STATUS IS '단계 실행 상태. START, RUNNING, PASS, FAIL, ERROR 등.';
COMMENT ON COLUMN NEXT_MIG_LOG.MESSAGE IS '로그 상세 메시지.';
COMMENT ON COLUMN NEXT_MIG_LOG.RETRY_COUNT IS '로그 발생 시점의 retry count.';
COMMENT ON COLUMN NEXT_MIG_LOG.GENERATE_SQL IS '생성 SQL 또는 오류 분석에 필요한 SQL 본문.';
COMMENT ON COLUMN NEXT_MIG_LOG.CREATED_AT IS '로그 생성 시각.';
```

```sql
CREATE INDEX IX_NEXT_MIG_LOG_TARGET ON NEXT_MIG_LOG (MAP_ID, MIG_KIND, CREATED_AT);
CREATE INDEX IX_NEXT_MIG_LOG_STATUS ON NEXT_MIG_LOG (MIG_KIND, STATUS, CREATED_AT);
```

## 6.7 제약 및 운영 메모

| 항목 | 기준 |
|---|---|
| SQL job 단건 식별 | `NEXT_SQL_INFO`의 PK는 계속 `SPACE_NM + SQL_ID`다. `SQL_SEQ`는 사용자 입력, 조회, 실행 대상 지정에 쓰는 immutable unique 번호다. |
| Correct SQL 지정 | 어떤 row의 `REF_SEQ`는 기존 `SM_CORRECT_SQL_CONVERSION`에 이미 존재하는 `SQL_SEQ`만 가리킬 수 있다. 지정 시 원본 row나 기존 Correct SQL 문서를 복제·수정하지 않는다. |
| DB Migration 단건 식별 | `NEXT_MIG_INFO`는 `MAP_ID`를 단건 key로 사용한다. |
| SQL Conversion RAG | `CATEGORY='SQL_CONVERSION'` row는 `SOURCE_TABLES`를 반드시 입력하고, `GUIDANCE_TEXT`는 비운다. |
| SQL Tuning RAG | `CATEGORY='SQL_TUNING'` row는 `GUIDANCE_TEXT`를 입력하고 `SOURCE_TABLES`를 비운다. |
| 로그 단일화 | `NEXT_SQL_LOG`는 사용하지 않고 SQL 계열 로그도 `NEXT_MIG_LOG`에 저장한다. |
| FK 적용 | 초기 적재, 보정, 재실행 편의성을 위해 DDL에는 FK를 기본 포함하지 않는다. 필요 시 운영 정책에 따라 `NEXT_MIG_INFO_DTL.MAP_ID -> NEXT_MIG_INFO.MAP_ID`만 별도 추가한다. |

## 6.8 Milvus 컬렉션 구조

Milvus는 Oracle 원천 데이터를 검색용으로 복제한 벡터 저장소다. 동기화는 `04_saveVectorDB.py`가 수행하며, `doc_id`를 primary key로 사용한다. 모든 컬렉션에는 아래 공통 필드가 있다.

| 공통 필드 | 타입 | 설명 |
|---|---|---|
| `doc_id` | VARCHAR(256), PK | Oracle row key에서 생성한 안정적인 문서 식별자 |
| `content` | VARCHAR | embedding 및 BM25 입력 텍스트 |
| `content_hash` | VARCHAR(64) | 원천/metadata 변경 감지용 SHA-256 |
| `is_active` | BOOL | 동기화 기준 활성 여부 |
| `updated_at` | VARCHAR(64) | Oracle 원천 갱신 시각 |
| `dense_vector` | FLOAT_VECTOR | `content`의 embedding, COSINE index |
| `sparse_vector` | SPARSE_FLOAT_VECTOR, 선택 | Milvus BM25를 지원하는 환경에서만 생성 |

| 컬렉션 | Oracle 원천 | 전용 metadata | 사용처 |
|---|---|---|---|
| `SM_RAG_RULES` | `NEXT_MIG_RAG_INFO` | `rag_id`, `category`, `rule_type`, `use_yn`, `source_tables`, `guidance_text`, `source_sql`, `target_sql` | 12C Conversion, 15C Tuning RAG 검색 |
| `SM_CORRECT_SQL_CONVERSION` | `save_correct_sql`로 저장되어 `USER_EDITED='Y'`인 SQL row | `sql_seq`, `space_nm`, `sql_id`, `status_conversion`, `user_edited`, `tag_kind`, `target_table`, `source_sql`, `to_sql`, `bind_sql`, `test_sql` | 12C correct SQL hint 및 REF_SEQ 지정 대상 |
| `SM_CORRECT_SQL_MIGRATION` | `NEXT_MIG_INFO`의 user-edited/PASS migration row | `map_id`, `fr_table`, `to_table`, `condition`, `mig_sql`, `verify_sql`, `user_edited`, `status` | 10C migration SQL hint |
| `SM_ASIS_SQL` | `NEXT_SQL_INFO`의 `EDIT_FR_SQL` 또는 `FR_SQL` | `sql_seq`, `space_nm`, `sql_id`, `tag_kind`, `target_table`, `fr_sql`, `edit_fr_sql` | 04 유사 AS-IS SQL 검색 및 12C Correct SQL hint 검색의 query vector 재사용 |

`SM_ASIS_SQL`에는 실행 status와 TO-BE 결과 SQL을 저장하지 않는다. 검색 결과의 재실행 가능 여부와 최신 status는 항상 Oracle `NEXT_SQL_INFO`를 다시 조회해 판단한다.

`SM_CORRECT_SQL_CONVERSION` 기존 컬렉션에는 최초 VectorDB sync 때 nullable `sql_seq`(INT64) 필드를 추가하고, 활성 Correct SQL 문서를 upsert하여 값을 채운다. 이 스키마 확장은 Milvus/pymilvus 2.6 이상이 필요하다.

`04_ragCommandTool`과 12C는 `SPACE_NM + SQL_ID`로 요청된 SQL만 `SM_ASIS_SQL.dense_vector`를 query vector로 재사용한다. 저장된 `EDIT_FR_SQL`/`FR_SQL`이 현재 source SQL과 정확히 같을 때만 사용하며, 직접 입력 SQL·동기화 누락·원문 불일치 시에는 embedding API로 새 벡터를 생성한다.
