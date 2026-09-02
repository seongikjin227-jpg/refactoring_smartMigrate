# Main Logic Component Guide

이 문서는 `10C`, `12C`, `15C`, `17C`를 읽을 때의 기준을 정리한다. 네 컴포넌트는 각 flow에서 실제 DB row 1건을 처리하는 main executor다.

## Common Reading Pattern

각 C 컴포넌트는 같은 순서로 읽으면 된다.

1. `run_job()`
   - Langflow loop item을 파싱한다.
   - `db_config`를 확인한다.
   - DB에서 실제 row를 다시 조회한다.
   - 실제 작업을 수행할 경우 `BATCH_CNT + 1`을 수행한다.

2. `_run_*()`
   - 컴포넌트의 핵심 stage 분기가 들어 있다.
   - 현재는 DB 기준으로 가능한 분기와 POC 실패/성공 시뮬레이션이 구현되어 있다.
   - LLM/RAG/실제 SQL 실행이 필요한 부분은 `Future LLM/RAG section` 주석으로 분리되어 있다.

3. `_update_row()`
   - `NEXT_MIG_INFO` 또는 `NEXT_SQL_INFO` 결과 row를 업데이트한다.
   - SQL payload는 자르지 않는다.
   - 짧은 log/status metadata만 필요한 곳에서 제한한다.

4. SmartMigrate workflow logging
   - `10C`는 `NEXT_MIG_LOG`에 migration history를 남긴다.
   - `12C`, `15C`, `17C`도 `NEXT_MIG_LOG`에 SQL pipeline history를 남긴다.
   - DB insert는 `00A_logRuntimeStart.py`에서 등록한 `SmartMigrateDBHandler` 하나가 처리한다.
   - 로그 insert 실패는 작업 전체 실패로 보지 않는다.

5. `_result()`
   - Langflow loop feedback payload를 만든다.
   - `D` dashboard 컴포넌트가 이 payload를 받아 iteration message와 loop feedback을 만든다.

## 10C MIG One Job

`10C`는 `NEXT_MIG_INFO.MAP_ID` 1건을 처리한다.

- dependency를 먼저 확인한다.
- 실행 가능하면 `STATUS='RUNNING'`, `BATCH_CNT + 1`을 저장한다.
- `USER_EDITED='Y'`이고 `MIG_SQL`이 있으면 기존 SQL을 재사용한다.
- 생성된 `MIG_SQL`, `VERIFY_SQL`은 단계별로 즉시 저장한다.
- `TRUNC_YN='Y'`면 truncate stage를 먼저 탄다.
- 실패 상태는 `FAIL-TRUNCATE`, `FAIL-INSERT`, `FAIL-TEST` 중 하나다.
- 일반 `FAIL` 상태는 만들지 않는다.

## 12C SQL Conversion One Job

`12C`는 `NEXT_SQL_INFO` row 1건을 conversion 처리한다.

- `row_id` 우선, 없으면 `SPACE_NM + SQL_ID`로 row를 조회한다.
- 실제 conversion 작업이 시작되면 `BATCH_CNT + 1`을 수행한다.
- `EDIT_FR_SQL`이 있으면 `FR_SQL`보다 우선한다.
- `TARGET_TABLE`이 없으면 mapping rule 조회가 불가능하므로 `FAIL-TOBE`로 종료한다.
- source SQL 길이가 5000자를 초과하면 `TUNED_FR_SQL` branch를 탄다.
- `TAG_KIND='SELECT'`면 bind/test validation branch를 탄다.
- non-SELECT는 bind/test 없이 conversion pass 처리한다.
- 성공 시 `STATUS_CONVERSION='PASS-CONVERSION'`을 저장한다.
- 실패 시 `FAIL-TOBE`, `FAIL-BIND`, `FAIL-TEST` 중 하나를 저장한다.
- Conversion은 `STATUS_TUNING`을 변경하지 않는다.

## 15C SQL Tuning One Job

`15C`는 conversion pass row를 tuning 처리한다.

- `STATUS_CONVERSION`이 pass가 아니면 DB update 없이 pass-through 한다.
- 실제 tuning 작업이 시작되면 `BATCH_CNT + 1`을 수행한다.
- tuning rule retrieval, `BLOCK_RAG_CONTENT`, LLM tuning은 아직 TODO 영역이다.
- 현재 POC는 `TUNED_TO_SQL`, `TUNED_RESULT`를 만들어 tuning branch와 retry history를 보여준다.
- 성공 시 `STATUS_TUNING='PASS-TUNING'`을 저장한다.
- 실패 시 `FAIL-TUNED` 또는 `FAIL-TEST`를 저장한다.

## 17C SQL Formatting One Job

`17C`는 tuning pass row를 formatting 처리한다.

- `STATUS_TUNING`이 pass가 아니면 DB update 없이 pass-through 한다.
- 실제 formatting 작업이 시작되면 `BATCH_CNT + 1`을 수행한다.
- source SQL은 `TUNED_TO_SQL` 우선, 없으면 `TO_SQL`을 사용한다.
- `FORMATTED_SQL`만 저장한다.
- `STATUS_CONVERSION`, `STATUS_TUNING`은 변경하지 않는다.

## SQL Log Policy

SQL pipeline 로그는 `NEXT_MIG_LOG`에 저장한다.

- `MIG_KIND`: `SQL_CONVERSION`, `SQL_TUNING`, `SQL_FORMATTING`
- `MAP_ID`: `sql_id / space_nm`
- `GENERATE_SQL`: SQL 본문

| Component | SQL_KIND examples |
|---|---|
| `12C` | `TOBE_SQL`, `BIND_SQL`, `TEST_SQL`, `SQL_CONVERSION` |
| `15C` | `TUNED_TO_SQL`, `TUNED_TEST_SQL`, `SQL_TUNING` |
| `17C` | `FORMATTED_SQL` |

`GENERATE_SQL`은 실제 prompt/SQL 추적에 필요하므로 자르지 않는다. `STATUS`, `STEP_NAME`, `MESSAGE` 같은 metadata는 DB 컬럼 크기에 맞게 짧게 저장한다.

## RAG Table Policy

SQL conversion과 SQL tuning에서 필요한 rule/guidance/example은 `NEXT_MIG_RAG_INFO`를 사용한다.

- `CATEGORY='SQL_CONVERSION'`: conversion guidance/example
- `CATEGORY='SQL_TUNING'`: tuning guidance/example
- `RULE_TYPE='GENERAL'`: 전체 적용 가이드
- `RULE_TYPE='SEARCH'`: 검색 기반 예시/규칙

`NEXT_SQL_COMPLEX_MAP`, `NEXT_SQL_RULES`는 사용하지 않는다.
