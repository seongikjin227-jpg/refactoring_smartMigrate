# SmartMigrate 이용 가이드

## 1. 요청할 수 있는 작업

| 구분 | 식별자 | 요청 예시 |
|---|---|---|
| DB Migration 조회·실행·재시도 | `MAP_ID` | `MAP_ID=101 DB Migration 실행해줘.` |
| SQL Conversion 조회·실행·재시도 | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT SQL Conversion 실행해줘.` |
| SQL Tuning 조회·실행·재시도 | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT SQL Tuning 실행해줘.` |
| SQL Formatting | `SQL_ID`, `SPACE_NM` | `SQL_ID=S001, SPACE_NM=PAYMENT SQL Formatting 실행해줘.` |
| Correct SQL 저장 | 작업 식별자, SQL 컬럼, SQL 본문 | `MAP_ID=101의 MIG_SQL을 아래 SQL로 저장해줘. SQL=...` |
| RAG Guide 관리 | `CATEGORY`, `RULE_TYPE` 또는 `RAG_ID` | `SQL Tuning GENERAL RAG Guide 추가해줘. GUIDANCE_TEXT=...` |

SQL job은 `SQL_ID`와 `SPACE_NM`을 함께 입력해야 한다. 상태·로그·SQL 조회는 read-only이며, 실행·재시도 준비·SQL 저장은 DB 상태 또는 CLOB을 변경한다.

## 2. Correct SQL 입력 규칙

Correct SQL은 한 번에 한 단계 SQL만 저장한다. Migration은 `MIG_SQL` 또는 `VERIFY_SQL`만 Correct SQL로 저장·벡터화한다. `VERIFY2_SQL`은 MIG_SQL과 VERIFY_SQL에서 자동 조합하는 검증 SQL이므로 직접 Correct SQL로 저장하거나 17C formatting 대상으로 보내지 않는다.

### 2.1 Migration Correct SQL

| 컬럼 | 요구 구조 | 저장 후 동작 |
|---|---|---|
| `MIG_SQL` | `INSERT INTO target (target_columns...) SELECT source_expressions... FROM ...` | `FAIL-TEST`, Count Verify부터 재개 |
| `VERIFY_SQL` | `FROM (SELECT ...) S, (SELECT ...) T`의 Count Verify | migration 검증 완료로 처리 |

`MIG_SQL`은 INSERT 대상 컬럼 수와 SELECT 표현식 수·순서가 일치해야 한다. target 컬럼에는 alias를 붙이지 않으며, 변환식은 SELECT 쪽에 둔다.

```sql
INSERT INTO TARGET_SCHEMA.TO_EMP (EMP_NO, EMP_NAME)
SELECT LPAD(S.EMP_NO, 5, '0'),
       S.LAST_NAME || ' ' || S.FIRST_NAME
  FROM SOURCE_SCHEMA.ASIS_EMP S
 WHERE S.ACTIVE_YN = 'Y';
```

`VERIFY_SQL` T측의 `COUNT(target_column)` 목록은 Verify2가 비교할 target 컬럼 목록이 된다. `COUNT(*)`는 전체 건수 확인용이며 Verify2 컬럼 비교에서는 제외된다.

```sql
SELECT ABS(S.TOT - T.TOT) AS DIFF_TOT,
       ABS(S.C1 - T.C1) AS DIFF_C1
FROM (
    SELECT COUNT(*) AS TOT,
           COUNT(S.EMP_NO) AS C1
      FROM SOURCE_SCHEMA.ASIS_EMP S
) S,
(
    SELECT COUNT(*) AS TOT,
           COUNT(T.EMP_NO) AS C1
      FROM TARGET_SCHEMA.TO_EMP T
) T;
```

### 2.2 Verify2 자동 검증

1차 Count가 통과하면 Executor2가 `MIG_SQL`과 `VERIFY_SQL`을 읽어 `VERIFY2_SQL`을 자동 조합한다. MIG_SQL의 `INSERT` 컬럼과 동일 위치의 `SELECT` 표현식은 AS-IS 값으로, Verify SQL T측 `COUNT(target_column)`은 TO-BE 비교 컬럼으로 사용한다.

```text
MIG_SQL 변환식 + MIG_SQL source scope  → ASIS_CONCAT
VERIFY_SQL T측 컬럼 + T측 target scope → TOBE_CONCAT
ROW_CONCAT 정렬 후 같은 ROW_NO끼리    → MATCH / MISMATCH
```

- `COUNT(*)`는 1차에서 검증했으므로 제외한다.
- `VERIFY2_SQL`은 실행 전 CLOB에 저장되고, `FAIL-TEST2`에서는 이것만 다시 조합·실행한다.
- 최대 5건의 불일치 사례를 로그에 남긴다. 불일치가 없으면 일치 사례를 남긴다.
- ASIS/TOBE payload는 컬럼명 없이 값만 `|` 순서로 연결한다. 컬럼 해석은 로그의 `compared_columns` 순서를 기준으로 한다.
- Verify2 조합에는 `INSERT ... SELECT`와 단순 `COUNT(target_column)` 구조가 필요하다. `INSERT ... VALUES`, `INSERT ALL`, `COUNT(function(...))`은 지원하지 않는다.

### 2.3 Conversion Correct SQL

| 컬럼 | 필수 조건 | 저장 후 재개 단계 |
|---|---|---|
| `TO_SQL` | 실행 가능한 TO-BE SQL | Bind 생성부터 |
| `BIND_SQL` | 실행 가능한 bind SQL과 비어 있지 않은 JSON 배열 `BIND_SET` | Test 생성·실행부터 |
| `TEST_SQL` | 사람이 검증 완료한 test SQL | Conversion 완료 |

Correct SQL 저장 직후 해당 단계의 SQL만 VectorDB에 동기화한다. 기존 SQL을 수정할 때는 SQL 본문 전체와 대상 컬럼을 함께 명시한다.

## 3. Mapping Rule 입력 규칙

Mapping rule은 `NEXT_MIG_INFO`와 `NEXT_MIG_INFO_DTL`에 작성한다. `NEXT_MIG_INFO_DTL`의 한 row는 `FR_COL → TO_COL` 한 건이다.

| 입력 | 의미 |
|---|---|
| `FR_TABLE`, `TO_TABLE` | source table과 target table의 명시적 매핑 |
| `FR_COL → TO_COL` | Conversion과 Migration에서 사용할 컬럼 매핑 |
| `TO_COL=NULL`, blank, `NONE`, `N/A`, `NA`, `-` | 해당 source 컬럼은 TO-BE에서 미사용 |
| 이름이 같은 컬럼 | 그래도 `FR_COL → 동일한 TO_COL` row를 명시해야 사용 가능 |

매핑에 없는 source table/column은 “같은 이름 유지”가 아니라 **TO-BE 미사용**으로 해석한다. 그러므로 SELECT, JOIN, WHERE, GROUP BY 등에 계속 써야 하는 object는 이름이 같아도 반드시 매핑한다.

```text
FR_TABLE = ASIS_CUSTOMER       → TO_TABLE = TOBE_MEMBER
CUST_ID                        → MEMBER_ID
CUST_NM                        → MEMBER_NAME
LEGACY_YN                      → NULL       (TO-BE 미사용)
```

자세한 Conversion mapping 계약은 `03_job_execution/12C_sql_conversion_mapping_rule_contract.md`를 따른다.

## 4. Conversion·Tuning RAG Guide 입력 규칙

| 목적 | `CATEGORY` / `RULE_TYPE` | 필수 입력 | 입력하지 않는 값 |
|---|---|---|---|
| Conversion 공통 범위 | `SQL_CONVERSION` / `GENERAL` | `SOURCE_TABLES` | 보통 `GUIDANCE_TEXT` |
| Conversion 변환 예시 | `SQL_CONVERSION` / `SEARCH` | `SOURCE_TABLES`, `SOURCE_SQL`, `TARGET_SQL` | 보통 `GUIDANCE_TEXT` |
| Tuning 공통 규칙 | `SQL_TUNING` / `GENERAL` | `GUIDANCE_TEXT` | `SOURCE_TABLES` |
| Tuning 예시 | `SQL_TUNING` / `SEARCH` | `GUIDANCE_TEXT`, `SOURCE_SQL`, `TARGET_SQL` | `SOURCE_TABLES` |

- `SEARCH`의 `SOURCE_SQL`과 `TARGET_SQL`은 함께 입력한다.
- 신규 추가 시 `RAG_ID`는 입력하지 않는다. 수정·비활성화에만 사용한다.
- RAG Guide를 추가·수정한 뒤 `VectorDB 동기화 실행해줘.`를 요청해야 검색에 반영된다.

## 5. 실행과 formatting 규칙

- 재시도 준비는 상태를 임의로 PASS로 바꾸지 않고 `RETRY_COUNT=0`과 우선순위만 조정한다.
- 17C는 이번 LLM 실행에서 생성된 SQL만 `generated_sql_list`로 받아 formatting한다.
- `generated_sql_list=[]`이면 `NO_FORMATTING_TARGETS` 로그를 남기고 formatting 없이 넘어간다.
- 목록 키가 누락되거나 list가 아니면 계약 오류 로그를 남기고 DB SQL을 추측해 formatting하지 않는다.
- `VERIFY2_SQL`은 검증 전용 SQL이므로 formatting 대상이 아니다.

## 6. 자주 쓰는 조회 요청

| 목적 | 요청 예시 |
|---|---|
| Migration 상태·최근 로그 | `MAP_ID=101 상태와 최근 로그 5건 보여줘.` |
| Migration SQL 조회 | `MAP_ID=101의 MIG_SQL, VERIFY_SQL, VERIFY2_SQL 원문 보여줘.` |
| SQL job 상태 | `SQL_ID=S001, SPACE_NM=PAYMENT 상태와 최근 로그 보여줘.` |
| 실패 원인 | `최근 SQL Conversion 실패 10건 원인 요약해줘.` |
| RAG Guide 조회 | `SQL Conversion RAG Guide 중 CUSTOMER가 포함된 항목 20건 조회해줘.` |
