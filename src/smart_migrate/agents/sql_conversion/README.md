# SQL Conversion Agent

`NEXT_SQL_INFO`의 SQL conversion job을 처리해 `TO_SQL`, `BIND_SQL`, `BIND_SET`, `TEST_SQL`, `STATUS_CONVERSION`을 저장하는 agent입니다.

이 agent의 책임은 AS-IS SQL을 TO-BE SQL로 변환하고, SELECT SQL인 경우 원본과 변환 SQL의 row count가 같은지 검증하는 것입니다. `TUNED_TO_SQL` 생성은 별도 `sql_tuning/` agent가 담당합니다.

## 진입점

```text
SupervisorSqlConversionTool.run_sql_conversion(row_id)
  -> SqlConversionAgent.process_job(job)
     -> TobeMultiAgentCoordinator.process_job(job)
        -> SqlConversionGraph.invoke({"execution": JobExecutionState})
```

`poll_jobs()`는 `NEXT_SQL_INFO.ROWID`를 registry에 올리고, supervisor가 `run_sql_conversion(row_id)` tool을 호출하면 해당 row를 다시 조회해 `SqlInfoJob`을 구성합니다. 이 job 객체와 graph state가 각 node에 공유됩니다.

## 전체 실행 순서

```text
TobeMultiAgentCoordinator.process_job(job)
  1. mapping rule 로드
     - MappingRuleRepository.get_all_mapping_rules()

  2. runtime classification 계산
     - SQL 길이는 DB 컬럼에 저장하지 않음
     - job.source_sql, 즉 EDIT_FR_SQL 우선/없으면 FR_SQL 기준으로 SHORT/LONG 계산
     - MAP_TYPE도 NEXT_SQL_INFO에 저장하거나 conversion 분기에 사용하지 않음

  3. 기존 tuning 결과 자동 초기화 없음
     - 사용자가 재처리를 원하면 관련 컬럼을 직접 정리한 뒤 다시 실행하는 정책

  4. 최대 3회 retry loop
     - JobExecutionState 생성
     - 이전 오류를 RETRY_CONTEXT로 prompt에 전달
     - LangGraph 실행
     - SELECT는 TEST_SQL 실행 결과로 PASS/FAIL 판정
     - non-SELECT는 TO_SQL 생성 후 검증 없이 PASS 저장

  5. 최종 저장
     - 성공: update_cycle_result(..., STATUS_CONVERSION=PASS)
     - 실패: update_cycle_result(..., STATUS_CONVERSION=FAIL_TOBE/FAIL_BIND/FAIL_TEST)
```

## Graph 노드 순서

`SqlConversionGraph.build_migration_workflow()`의 graph는 단순합니다.

```text
START
  -> tobe_generation.generate
     -> non-SELECT: END
     -> SELECT: tobe_generation.validate
  -> END
```

### `tobe_generation.generate`

구현 위치: `TobeSqlGenerationAgent.generate()`

1. `USER_EDITED='Y'`이고 저장된 `TO_SQL`이 있으면 LLM을 호출하지 않고 해당 값을 재사용합니다.
2. 그렇지 않으면 LONG SQL 전처리 여부를 계산합니다.
3. 필요하면 AS-IS SQL에 SQL_TUNING RAG를 먼저 적용해 `TUNED_FR_SQL`에 저장합니다.
4. `TO_SQL`은 원본이 아니라 전처리된 `TUNED_FR_SQL` 기준으로 생성합니다.
5. 전처리가 없으면 `job.source_sql` 기준으로 `TO_SQL`을 생성합니다.

### `tobe_generation.validate`

구현 위치: `TobeSqlGenerationAgent.validate()`

1. bind parameter 이름을 추출합니다.
2. bind parameter가 없으면 `BIND_SQL` 생성을 생략하고 test용 bind set을 `[{}]`로 둡니다.
3. bind parameter가 있으면 `BIND_SQL`을 생성합니다.
4. `BIND_SQL`을 실행해 최대 3개 bind case를 만들고 `BIND_SET` JSON을 구성합니다.
5. `TEST_SQL`을 생성합니다.
6. `TEST_SQL`을 실행합니다.
7. `evaluate_status_from_test_rows()`로 FROM/TO row count 비교 결과를 PASS/FAIL로 판정합니다.

## LONG SQL과 `TUNED_FR_SQL`

`TUNED_FR_SQL`은 긴 AS-IS SQL을 바로 conversion prompt에 넣기 전에 SQL_TUNING RAG를 적용한 전처리 SQL입니다.

조건:

- `BIND_SQL_PRETUNING_ENABLED=true`
- 그리고 `job.source_sql` 기준 SQL 길이가 `LONG`이거나 `BIND_SQL_PRETUNING_MIN_LENGTH` 이상

`SQL_LENGTH` 컬럼 값은 사용하지 않습니다. SQL 길이는 실행 중 `EDIT_FR_SQL` 우선, 없으면 `FR_SQL`을 기준으로 계산합니다.

흐름:

```text
job.source_sql
  -> generate_bind_tuned_sql()
     - SQL_TUNING GENERAL rule 로드
     - SQL_TUNING SEARCH rule RAG 검색
     - bind_tuned_sql_prompt.json으로 AS-IS SQL 단순화/정리
  -> update_fr_bindtuned_sql(row_id, tuned_sql)
     - NEXT_SQL_INFO.TUNED_FR_SQL 저장
  -> generate_tobe_sql(source_sql=tuned_sql)
     - TO_SQL 생성 입력으로 사용
  -> validate 단계의 BIND_SQL 생성 입력으로도 재사용
```

이미 `TUNED_FR_SQL` 값이 있으면 다시 LLM 전처리를 하지 않고 저장된 값을 재사용합니다.

## TO-BE SQL 생성에서 RAG 활용

구현 위치: `SqlLlmService.generate_tobe_sql()`

`TO_SQL` 생성 prompt에는 mapping rule과 SQL_CONVERSION RAG가 함께 들어갑니다.

```text
generate_tobe_sql()
  -> _select_mapping_rules_for_job()
  -> tobe_sql_tuning_service.load_universal_conversion_rules()
  -> tobe_sql_tuning_service.retrieve_conversion_examples()
  -> _serialize_sql_conversion_mapping_rules()
  -> tobe_sql_prompt.json
  -> LLM 호출
```

RAG 데이터 출처는 `RAG_INFO_TABLE` 환경변수이며 기본값은 `NEXT_MIG_RAG_INFO`입니다.

- `CATEGORY='SQL_CONVERSION'`, `RULE_TYPE='GENERAL'`: 항상 적용 가능한 일반 변환 가이드입니다.
- `CATEGORY='SQL_CONVERSION'`, `RULE_TYPE='SEARCH'`: SQL block별 유사 예시입니다. embedding/FAISS로 top-k rule을 찾고, 실패하면 lexical fallback을 사용합니다.
- prompt에 사용한 SEARCH rule은 `HIT_CNT`가 증가합니다.

우선순위:

```text
현재 job 입력 / retry error
  > MIGRATION_MAPPING_RULES
  > COMPLEX_TABLE_MAPPING_RULES
  > SQL_CONVERSION RAG guidance/example
```

RAG는 예시와 가이드이며 mapping rule과 현재 SQL보다 우선하지 않습니다.

## Mapping Rule 처리

SQL conversion은 `NEXT_SQL_INFO.MAP_TYPE`을 저장하거나 사용하지 않습니다.

- `NEXT_SQL_INFO.TARGET_TABLE`에 적힌 target table 토큰을 기준으로 관련 mapping rule을 선별합니다.
- `NEXT_MIG_INFO.FR_TABLE`이 target table 토큰을 포함하는 rule이 있으면 prompt의 `MIGRATION_MAPPING_RULES` 또는 `COMPLEX_TABLE_MAPPING_RULES`에 포함됩니다.
- 매칭 rule이 없으면 prompt의 mapping section은 비어 있고, prompt 정책에 따라 매핑되지 않은 이름은 원본 이름을 유지합니다.

따라서 target table과 맞는 mapping rule이 반드시 있어야 한다는 정책을 원하면, conversion 시작 전에 별도 precheck로 SKIP/FAIL 처리하는 것이 맞습니다.

## SQL_TUNING RAG 사용 위치

conversion agent 안에서는 SQL_TUNING RAG가 두 군데에서 쓰일 수 있습니다.

1. `TUNED_FR_SQL` 전처리
   - `generate_bind_tuned_sql()`
   - 긴 AS-IS SQL을 TO-BE 변환 전에 단순화/정리합니다.

2. legacy 내부 `SqlTuningAgent`
   - `SqlConversionCoordinator.SqlTuningAgent`
   - 현재 `SqlConversionGraph`에는 연결되어 있지 않습니다.
   - 실제 `TUNED_TO_SQL` 후처리는 `src/smart_migrate/agents/sql_tuning/` agent가 담당합니다.

## 저장 컬럼

성공 시 `update_cycle_result()`가 주로 갱신하는 값:

- `TO_SQL`: 생성된 TO-BE SQL
- `BIND_SQL`: bind case 추출 SQL
- `BIND_SET`: bind case JSON
- `TEST_SQL`: 원본/TO-BE row count 비교 SQL
- `STATUS_CONVERSION`: `PASS`
- `STATUS_TUNING`: conversion does not update this column. SQL tuning writes `PASS-TUNING`, `FAIL-TUNED`, or `FAIL-TEST`.
- `LOG`, `RETRY_COUNT`, `UPD_TS`

전처리 결과는 별도 저장합니다.

- `TUNED_FR_SQL`: LONG SQL 전처리 결과

`SQL_LENGTH`, `MAP_TYPE`은 conversion 결과로 저장하지 않습니다. 필요한 분기는 실행 중 계산값과 mapping rule 조회 결과로 처리합니다.

실패 시 `STATUS_CONVERSION`은 실패 지점에 따라 다음 중 하나가 됩니다.

- `FAIL_TOBE`: TO_SQL 생성 실패
- `FAIL_BIND`: BIND_SQL 생성/실행 실패
- `FAIL_TEST`: TEST_SQL 생성/실행 또는 row count 검증 실패

## 주요 파일

- `SqlConversionAgent.py`: supervisor-facing 진입점입니다.
- `SqlConversionCoordinator.py`: retry loop, graph 실행, LONG SQL 전처리와 저장을 담당합니다.
- `SqlConversionGraph.py`: `tobe_generation.generate`와 `tobe_generation.validate` 노드 순서를 정의합니다.
- `SqlConversionState.py`: graph에서 공유되는 mutable state입니다.
- `SqlLlmService.py`: TO_SQL, TUNED_FR_SQL, BIND_SQL, TEST_SQL LLM 호출 helper입니다.
- `SqlBindCases.py`: bind parameter 추출과 bind set JSON 생성을 담당합니다.
- `SqlConversionValidateNode.py`: BIND_SQL/TEST_SQL 실행과 PASS/FAIL 판정을 담당합니다.
- `CorrectSqlRagService.py`: 과거 correct SQL hint 검색 서비스입니다. 현재 conversion prompt에는 `correct_sql_hint_json="[]"`로 들어가므로 active path에는 연결되어 있지 않습니다.
