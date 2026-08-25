# DB Migration Agent

`NEXT_MIG_INFO` 기반 DB migration job을 처리하는 agent입니다.

이 agent의 책임은 "mapping rule 기반으로 migration SQL과 verification SQL을 만들고, migration SQL을 실행한 뒤 verification SQL로 데이터 정합성을 확인"하는 데 있습니다. SQL 생성, 실행, 검증, 재시도 분기가 모두 LangGraph 안에 들어 있습니다.

## 진입점

```text
SupervisorGraph cycle
  -> poll_jobs()
     -> MigrationJobRepository.get_pending_jobs()
     -> mig_registry[job.map_id] = job
     -> poll_result에 map_id 노출
  -> supervisor LLM이 run_data_migration(map_id) tool 선택
  -> SupervisorMigrationTool.run_data_migration(map_id)
     -> mig_registry에서 job 객체 조회
     -> callbacks["mig_proc"](job)
     -> MigrationOrchestrator.process_job(job)
     -> initial_state 구성
     -> migration_graph.invoke(initial_state)
```

`poll_jobs()`가 agent를 직접 호출하지는 않습니다. DB에서 대기 job을 조회해 registry를 채우고, supervisor가 그 registry에 있는 `map_id`를 tool argument로 넘깁니다. 실제 migration agent에는 `map_id`만 넘어가는 것이 아니라, tool wrapper가 `mig_registry[map_id]`에서 꺼낸 job 객체를 전달합니다.

`MigrationOrchestrator.process_job()`은 graph 실행 결과의 최종 `status`를 반환합니다. `BatchAbortError`는 상위 supervisor로 전파하고, 예기치 못한 crash는 해당 job을 `FAIL`로 저장합니다.

## 전체 실행 순서

```text
MigrationOrchestrator.process_job(job)
  1. initial_state 생성
     - next_sql_info = job
     - db_attempts = 1
     - max_attempts = DB_MIGRATION_MAX_ATTEMPTS + 1
     - current_migration_sql/current_v_sql 초기화

  2. migration_graph 실행
     - check_dependency
     - fetch_ddl
     - generate
     - execute
     - verify
     - finalize

  3. 최종 status 반환
     - PASS / SKIP-PRIOR-FAIL / NOT_RUNNABLE / FAIL
```

## Graph 노드 순서

```text
check_dependency
  -> fetch_ddl
     -> generate
        -> execute
           -> verify
              -> finalize
```

의존성 확인을 먼저 수행해 선행 작업이 실패 계열이면 현재 job은 `SKIP-PRIOR-FAIL`로 저장하고, 선행 작업이 아직 미완료이면 DDL 조회와 상태 업데이트를 하지 않습니다. 재시도 상태에 따라 중간에 `finalize`로 빠지거나 `biz_retry_prepare`를 거쳐 다시 실행됩니다.

```text
check_dependency
  -> READY: fetch_ddl
  -> SKIP-PRIOR-FAIL/NOT_RUNNABLE: finalize

fetch_ddl
  -> generate

generate
  -> migration SQL 있음: execute
  -> verify retry이면 status=EXECUTED: verify
  -> BIZ_RETRY: biz_retry_prepare

execute
  -> EXECUTED: verify
  -> BIZ_RETRY: biz_retry_prepare

verify
  -> PASS: finalize
  -> BIZ_RETRY: biz_retry_prepare

biz_retry_prepare
  -> FAIL-TRUNCATE: execute
  -> 그 외: generate
```

## 노드별 역할

### `check_dependency`

담당 함수: `check_dependency_node()`

1. `PRIOR_MAP_ID` 의존성을 확인합니다.
2. 선행 job이 실패 계열이면 현재 job을 `SKIP-PRIOR-FAIL`로 저장합니다.
3. 선행 job이 아직 미완료이면 현재 job은 실행 대상이 아니므로 `NOT_RUNNABLE` 런타임 결과로 종료하고 DB status는 업데이트하지 않습니다.
4. 실행 가능하면 `BATCH_CNT`를 증가시키고 `fetch_ddl`로 진행합니다.

`PRIORITY`는 실행 정렬 기준이며 dependency가 아닙니다. 같은 `TO_TABLE`의 낮은 priority job이 실패했더라도 그 자체로 현재 job을 막지 않습니다.

### `fetch_ddl`

담당 함수: `fetch_ddl_node()`

1. source DDL 조회 대상을 결정합니다.
   - `MAP_TYPE!='COMPLEX'`: `FR_TABLE`을 단일 source table 이름으로 사용합니다.
   - `MAP_TYPE='COMPLEX'`: `FR_TABLE`에 SQL/표현식이 들어갈 수 있으므로 `FROM`/`JOIN` 뒤의 물리 table들을 파싱합니다.
2. source table DDL을 `OracleDdlReader.fetch_table_ddl()`로 조회합니다.
3. target DDL은 `TO_TABLE` 기준으로 조회합니다.
4. 조회 결과를 `source_ddl`, `target_ddl` state에 저장합니다.

DDL은 LLM prompt에 들어가며, migration SQL의 타입 변환과 target column 구성에 사용됩니다.

### `generate`

담당 함수: `generate_sql_node()`

1. `USER_EDITED='Y'`이면 저장된 `MIG_SQL`과 `VERIFY_SQL`을 재사용합니다.
2. 아니면 `MigrationLlmClient.generate_sqls()`를 호출합니다.
3. LLM은 한 번의 호출에서 `migration_sql`, `verification_sql`을 반환합니다.
4. prompt JSON 형식에는 legacy 호환용 `ddl_sql` key가 남아 있지만, 현재 graph는 DDL SQL을 실행하거나 저장하지 않습니다.
5. `MAP_TYPE='COMPLEX'`이면 `FR_TABLE`을 table명으로 schema qualify하지 않고 source SQL 표현식으로 prompt에 전달합니다.
6. 최초 생성이면 `MIG_SQL`과 `VERIFY_SQL`을 함께 저장합니다.
7. `FAIL-TEST` 재시도이면 기존 `migration_sql`은 유지하고 `verification_sql`만 재생성합니다.

중요한 점은 migration SQL 생성과 verification SQL 생성이 별도 graph node로 나뉘어 있지 않다는 것입니다. 둘 다 `generate` node 안에서 생성됩니다.

### `execute`

담당 함수: `execute_sql_node()`

1. `TRUNC_YN='Y'`이면 `TO_TABLE`을 먼저 truncate합니다.
2. `current_migration_sql`을 실행합니다.
3. truncate 또는 SQL 실행 실패 시 `FAIL-TRUNCATE`나 `FAIL-INSERT`로 business retry를 유도합니다.

### `verify`

담당 함수: `verify_sql_node()`

1. `current_v_sql`이 비어 있으면 `FAIL-TEST`로 처리합니다.
2. verification SQL을 실행합니다.
3. 검증 결과가 false면 `FAIL-TEST`로 retry합니다.
4. 통과하면 `PASS`로 finalize합니다.

### `finalize`

담당 함수: `finalize_node()`

최종 status에 따라 `NEXT_MIG_INFO` 상태와 migration history를 저장합니다.

- `PASS`: migration 성공
- `SKIP-PRIOR-FAIL`: 선행 job이 실패 계열이라 현재 job도 실행하지 않고 skip 상태로 저장
- `NOT_RUNNABLE`: 선행 job이 아직 미완료라 실행하지 않음. DB status는 업데이트하지 않음
- `FAIL`: 최대 retry 초과 또는 복구 불가 실패

## Retry 정책

`DB_MIGRATION_MAX_ATTEMPTS`는 retry 횟수이고, 실제 최대 attempt는 `DB_MIGRATION_MAX_ATTEMPTS + 1`입니다.

```text
BIZ_RETRY 발생
  -> db_attempts < max_attempts 이면 biz_retry_prepare
  -> FAIL-TEST: 기존 migration SQL 유지, verification SQL 재생성
  -> FAIL-TRUNCATE: generate 없이 execute 재시도
  -> FAIL-INSERT: migration SQL/verification SQL 재생성
  -> max_attempts 도달: finalize에서 실패 저장
```

LLM 인증, token limit, invalid request 같은 치명 오류는 batch abort로 처리합니다. 일반 business 실패와 달리 해당 job만 재시도하지 않습니다.

## Mapping Rule 사용 방식

별도 `mapping_rule_collect` node는 없습니다.

`NEXT_MIG_INFO` job 객체의 `details`에 mapping detail이 포함되어 있고, `MigrationLlmClient.generate_sqls()`가 다음 형태로 prompt에 넣습니다.

```text
FR_COL -> TO_COL
```

즉, mapping rule 수집/직렬화는 `generate` node 내부의 LLM prompt 구성 로직입니다.

## State 공유 방식

`MigrationOrchestrator.process_job()`이 만든 `initial_state`가 LangGraph의 모든 node에 전달됩니다.

각 node는 필요한 값을 읽고, 변경할 값만 dict로 반환합니다. LangGraph는 반환된 dict를 기존 state에 merge해서 다음 node에 넘깁니다.

예:

```text
initial_state
  -> check_dependency: error_type/status/last_error 갱신
  -> fetch_ddl: source_ddl/target_ddl 갱신
  -> generate: current_migration_sql/current_v_sql 갱신
  -> execute: status/error_type/failure_status 갱신
  -> verify: status/error_type/failure_status 갱신
  -> finalize: elapsed_time/status 갱신
```

그래서 `source_ddl`, `target_ddl`, `current_migration_sql`, `current_v_sql`, `last_error`, `failure_status` 같은 값은 node 사이에서 공유되는 graph state입니다.

## 저장/로그

주요 저장 함수:

- `log_generated_sql(map_id, migration_sql, verification_sql)`: 최초 migration/verify SQL 생성 결과 저장
- `log_generated_verify_sql(map_id, verification_sql)`: 검증 실패 재시도에서 verify SQL만 저장
- `update_job_status(map_id, status, elapsed, retry_count)`: 최종 job 상태 저장
- `log_business_history(...)`: 단계별 이력 저장

## 주요 파일

- `MigrationAgent.py`: supervisor-facing 진입점입니다.
- `MigrationGraph.py`: LangGraph 노드, 조건 분기, retry routing을 정의합니다.
- `MigrationLlmClient.py`: migration SQL과 verification SQL 생성을 담당합니다.
- `MigrationPromptService.py`: migration prompt payload를 구성합니다.
- `MigrationExecuteNode.py`: truncate와 migration SQL 실행을 담당합니다.
- `MigrationVerifyNode.py`: verification SQL 실행과 결과 판정을 담당합니다.
- `MigrationState.py`: graph state schema입니다.
