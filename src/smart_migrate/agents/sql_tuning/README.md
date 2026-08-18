# SQL Tuning Agent

SQL conversion이 만든 `TO_SQL`을 입력으로 받아 `TUNED_TO_SQL`, `TUNED_RESULT`, `STATUS_TUNING`을 저장하는 agent입니다.

이 agent의 책임은 "conversion 검증을 통과한 TO-BE SQL에 SQL_TUNING RAG rule을 적용하고, SELECT SQL이면 기존 `TO_SQL`과 결과 row count가 같은지 검증"하는 데 있습니다.

## 진입점

```text
SupervisorSqlTuningTool.run_sql_tuning(row_ids)
  -> SqlTuningAgent.process_job(job)
     -> SqlTuningWorkflow.run(job)
        -> SqlTuningGraph.invoke({"execution": SqlTuningState})
```

`SqlTuningWorkflow`는 state 생성과 최종 DB 저장을 담당하고, tuning 내부 분기는 `SqlTuningGraph`가 담당합니다.

## 전체 실행 순서

```text
SqlTuningWorkflow.run(job)
  1. SqlTuningState 생성
     - tobe_sql = job.to_sql_text
     - bind_set_for_db = job.bind_set
     - mapping_rules = get_all_mapping_rules()
     - max_tuning_attempts = SELECT이면 2, 그 외 1

  2. SqlTuningGraph 실행
     - apply_tuning_rules
     - validate_tuned_sql

  3. 결과 저장
     - update_cycle_result(..., TUNED_TO_SQL, TUNED_RESULT, STATUS_TUNING)
     - 예외 발생 시 update_tuning_error()
```

## Graph 노드 순서

```text
START
  -> apply_tuning_rules
     -> TUNING RULE NOT FOUND: END
     -> NO TUNING: END
     -> non-SELECT: END
     -> SELECT: validate_tuned_sql
          -> PASS: END
          -> FAIL and attempt 남음: apply_tuning_rules
          -> FAIL and attempt 소진: END
```

## `apply_tuning_rules`

담당 runner: `sql_conversion.SqlConversionCoordinator.SqlTuningAgent._apply_tuning_rules()`

1. 현재 SQL을 `state.tobe_sql`로 시작합니다.
2. `target_table`에서 source table set을 파싱합니다.
3. `tobe_sql_tuning_service.retrieve_tuning_examples()`로 SQL_TUNING SEARCH rule을 찾습니다.
4. 검색된 rule block은 `BLOCK_RAG_CONTENT`에 저장합니다.
5. tuning rule이 없으면 `TUNED_RESULT='TUNING RULE NOT FOUND'`, `STATUS_TUNING='FAIL-TUNED'`로 종료합니다.
6. rule이 있으면 `tune_tobe_sql()`이 `tobe_sql_tuning_prompt.json`으로 LLM을 호출합니다.
7. SQL이 바뀌지 않았으면 `TUNED_RESULT='NO TUNING'`으로 간주하고 `PASS-TUNING`으로 종료합니다.
8. SQL이 바뀌었고 `TAG_KIND!='SELECT'`이면 별도 검증 없이 `PASS-TUNING`으로 종료합니다.
9. SQL이 바뀌었고 `TAG_KIND='SELECT'`이면 `validate_tuned_sql`로 이동합니다.

## `validate_tuned_sql`

SELECT SQL에만 실행됩니다.

1. `generate_sql_comparison_test_sql()`로 비교 검증 SQL을 생성합니다.
2. baseline은 기존 `TO_SQL`, candidate는 `TUNED_TO_SQL`입니다.
3. `BIND_SET`을 사용해 동일 bind case 기준으로 비교합니다.
4. `execute_test_query()`로 비교 SQL을 실행합니다.
5. `evaluate_status_from_test_rows()`가 row count를 비교합니다.
6. 통과하면 `STATUS_TUNING='PASS-TUNING'`으로 종료합니다.
7. 실패하고 attempt가 남아 있으면 `last_error`를 설정한 뒤 `apply_tuning_rules`로 돌아갑니다.
8. 마지막 attempt까지 실패하면 실패 상태로 저장됩니다.

## SELECT 검증 실패 시 재시도 위치

검증 실패 후에는 검증 SQL만 다시 만드는 것이 아니라 `TUNED_TO_SQL` 생성부터 다시 시작합니다.

```text
attempt 1
  -> apply_tuning_rules
     -> TUNED_TO_SQL 생성
  -> validate_tuned_sql
     -> tuned comparison TEST SQL 생성/실행
     -> FAIL

attempt 2
  -> apply_tuning_rules
     -> TUNED_TO_SQL 다시 생성
  -> validate_tuned_sql
     -> tuned comparison TEST SQL 다시 생성/실행
```

재시도 prompt에는 이전 실패 원인이 `last_error`로 들어갑니다.

- 검증 SQL 실행 자체가 예외로 실패한 경우: `TUNED_TEST_SQL_ERROR: ...`
- 검증 SQL은 실행됐지만 row count 비교가 실패한 경우: `TUNED_TEST_VALIDATION_FAIL: ...`

## `NO TUNING`과 `TUNING RULE NOT FOUND`

두 값은 의미가 다릅니다.

- `NO TUNING`: matching rule은 있었지만 LLM이 안전하게 바꿀 내용이 없다고 판단한 상태입니다. 기존 `TO_SQL`이 conversion 단계에서 이미 검증됐으므로 tuned 검증 없이 `PASS-TUNING`으로 저장합니다.
- `TUNING RULE NOT FOUND`: 현재 SQL에 적용할 SQL_TUNING SEARCH rule을 찾지 못한 상태입니다. rule 기반 tuning을 수행할 수 없으므로 `FAIL-TUNED`로 저장합니다.

## RAG 사용 방식

RAG 데이터 출처는 `RAG_INFO_TABLE` 환경변수이며 기본값은 `NEXT_MIG_RAG_INFO`입니다.

사용 rule:

- `CATEGORY='SQL_TUNING'`, `RULE_TYPE='GENERAL'`: tuning prompt에 들어가는 일반 가이드
- `CATEGORY='SQL_TUNING'`, `RULE_TYPE='SEARCH'`: 현재 SQL block과 유사한 tuning 예시

검색 방식:

```text
retrieve_tuning_examples(sql_text)
  -> SQL을 MAIN_SQL/SUBQUERY block으로 분리
  -> SEARCH rule 로드
  -> embedding + FAISS vector search
  -> 실패 시 token lexical fallback
  -> top-k rule block 반환
```

prompt에 사용된 SEARCH rule은 `HIT_CNT`가 증가합니다.

## 저장 컬럼

`update_cycle_result()`는 기존 conversion 결과를 유지하면서 tuning 관련 값을 갱신합니다.

- `TO_SQL`: 기존 `job.to_sql_text` 유지
- `TUNED_TO_SQL`: tuning 결과 SQL
- `TUNED_RESULT`: tuning 설명, `NO TUNING`, 또는 `TUNING RULE NOT FOUND`
- `STATUS_TUNING`: `PASS-TUNING`, `FAIL-TUNED`, `FAIL-TEST`
- `BIND_SQL`, `BIND_SET`, `TEST_SQL`, `STATUS_CONVERSION`: 기존 conversion 값 유지
- `LOG`: tuning final log

예외 발생 시 `update_tuning_error()`가 error log와 실패 상태를 저장합니다.

## 주요 설정

- `TOBE_SQL_TUNING_MAX_ITERATIONS`: tuning rule 반복 적용 횟수
- `TOBE_SQL_TUNING_TOP_K`: SQL block별 RAG SEARCH rule 개수
- `RAG_EMBED_BASE_URL`: embedding endpoint
- `RAG_EMBED_MODEL`: embedding model
- `RAG_EMBED_TIMEOUT_SEC`: embedding timeout

## 주요 파일

- `SqlTuningAgent.py`: supervisor-facing 진입점입니다.
- `SqlTuningWorkflow.py`: state 생성, graph 실행, 최종 저장을 담당합니다.
- `SqlTuningGraph.py`: tuning LangGraph 노드와 분기 routing을 정의합니다.
- `SqlTuningRuleRetrieveNode.py`: SQL_TUNING/SQL_CONVERSION RAG rule 조회, vector search, fallback, hit count 갱신을 담당합니다.
- `SqlTuningState.py`: tuning workflow/graph state입니다.
- `../sql_conversion/SqlConversionCoordinator.py`: RAG 적용과 tuned validation 실행 helper를 제공합니다.
- `../sql_conversion/SqlLlmService.py`: `tune_tobe_sql()`과 tuned validation SQL 생성을 제공합니다.
