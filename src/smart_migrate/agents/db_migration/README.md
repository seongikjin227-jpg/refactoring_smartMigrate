# DB Migration Agent

`NEXT_MIG_INFO` 기반 DB migration job을 처리합니다.

## 진입점

```text
SupervisorMigrationTool.run_data_migration()
  -> MigrationOrchestrator.process_job(NEXT_SQL_INFO)
     -> initial_state 구성
     -> migration_graph.invoke(initial_state)
```

`MigrationOrchestrator.process_job()`은 `MigrationGraph.migration_graph`를 호출하고 최종 `status`를 반환합니다. graph 실행 중 치명적 오류가 발생하면 `BatchAbortError`는 상위로 전파하고, 일반 crash는 해당 job을 `FAIL`로 저장합니다.

## Graph 호출 구조

```text
fetch_ddl
  -> check_dependency
     -> generate
        -> execute
           -> verify
              -> finalize
```

조건 분기:

- `check_dependency_node()`: 선행 job 또는 같은 target 우선순위 job이 준비되지 않으면 `WAITING`, 실패했으면 `SKIP`으로 `finalize`합니다.
- `generate_sql_node()`: `USER_EDITED=Y`이면 저장된 `MIG_SQL/VERIFY_SQL`을 사용하고, 아니면 `MigrationLlmClient.generate_sqls()`를 호출합니다.
- `execute_sql_node()`: `TRUNC_YN=Y`이면 target truncate 후 migration SQL을 실행합니다.
- `verify_sql_node()`: verification SQL로 정합성을 검증합니다.
- `should_continue()`: 상태와 `error_type`에 따라 다음 노드를 결정합니다.
- `biz_retry_prepare_node()`: `BIZ_RETRY`일 때 재시도 횟수를 올리고 다시 `generate` 또는 `execute`로 보냅니다.

## 재시도 구조

```text
BIZ_RETRY 발생
  -> db_attempts < max_attempts 이면 biz_retry_prepare
  -> FAIL-TEST는 기존 migration SQL 유지 후 verify SQL 재생성 흐름
  -> FAIL-TRUNCATE는 generate 없이 execute 재시도
  -> max_attempts 도달 시 finalize에서 실패 저장
```

`DB_MIGRATION_MAX_ATTEMPTS`는 business retry 횟수이고, 실제 최대 시도 횟수는 `BIZ_MAX_ATTEMPTS = retry + 1`입니다.

## 주요 파일

- `MigrationAgent.py`: supervisor-facing 진입점입니다.
- `MigrationGraph.py`: LangGraph 노드, 조건 분기, retry routing을 정의합니다.
- `MigrationLlmClient.py`: migration SQL과 verification SQL 생성을 담당합니다.
- `MigrationExecuteNode.py`: truncate와 migration SQL 실행을 담당합니다.
- `MigrationVerifyNode.py`: verification SQL 실행과 결과 판정을 담당합니다.
- `MigrationPromptService.py`: migration prompt payload를 구성합니다.
- `MigrationState.py`: graph state schema입니다.
