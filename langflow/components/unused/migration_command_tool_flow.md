# Migration Command Tool Chat Flow

이 문서는 사용자가 채팅에서 “db migration 전체 작업 진행해줘”라고 입력했을 때, `migration_command_tool.py`가 연결된 flow라면 어떤 로직을 타고 최종 메시지가 만들어지는지 설명합니다.

중요한 전제: 이 파일은 현재 `langflow/components/unused/` 아래에 있습니다. 현재 권장 구조의 `Chat_Agent.py` prompt는 legacy/removed tool을 직접 호출하지 말고 mutating 요청은 specialized agent로 라우팅하라고 되어 있습니다. 따라서 실제 active flow가 `Supervisor_Agent.py`를 쓰는 경우에는 `Chat Agent -> Supervisor Agent -> _run_migration_job()` 경로가 맞고, 이 문서는 `MigrationCommandTool`을 Langflow tool로 직접 연결했을 때의 legacy 단독 tool 흐름입니다.

## 전체 경로

```text
User chat
  "db migration 전체 작업 진행해줘"
    -> Chat Agent / LLM
       -> MigrationCommandTool tool call 생성
          command_json={"action":"run_migration_job","map_id":..., "max_attempts":...}
    -> MigrationCommandTool.run_command()
       -> _parse_command()
       -> action == "run_migration_job"
       -> _run_migration_job(map_id, command)
    -> Data(data=result) 반환
    -> Chat Agent가 result를 보고 최종 한국어 메시지 작성
```

## 1. 사용자 채팅 해석

사용자가 “db migration 전체 작업 진행해줘”라고만 말하면 tool 실행에는 `map_id`가 필요합니다.

이 tool은 전체 queue를 자동 polling해서 여러 건을 반복 처리하는 supervisor가 아닙니다. `run_migration_job`은 단일 `MAP_ID`에 대해 migration SQL 생성, 실행, verify SQL 생성, 검증까지 한 번의 전체 job cycle을 수행합니다.

따라서 채팅 agent는 다음 중 하나를 해야 합니다.

- 사용자가 `map_id`를 같이 말한 경우: 바로 tool call 생성
- `map_id`가 없는 경우: `list_pending`으로 대기 job을 조회하거나 사용자에게 `map_id`를 물어봄

예시 tool call:

```json
{
  "command_json": {
    "action": "run_migration_job",
    "map_id": 101,
    "max_attempts": 3
  }
}
```

## 2. Tool 진입점

구현 위치: `migration_command_tool.py`

```text
run_command()
  -> _parse_command()
  -> action 분기
```

`run_command()`는 `command_json`을 dict로 파싱한 뒤 `action` 값을 소문자로 정리합니다. `action == "run_migration_job"`이면 `_run_migration_job(map_id, command)`를 호출합니다.

지원 action은 다음과 같습니다.

- `test_connection`
- `status`
- `list_pending`
- `get_table_ddl`
- `generate_mig_sql`
- `generate_verify_sql`
- `preview_mig_prompt`
- `preview_verify_prompt`
- `reset`
- `save_user_sql`
- `analyze_failure`
- `run_migration_job`

지원하지 않는 action이면 `{"ok": false, "error": "Unsupported action: ..."}` 형태로 반환합니다.

## 3. `_run_migration_job()` 전체 흐름

`_run_migration_job()`은 단일 `MAP_ID` job을 끝까지 처리하는 main workflow입니다.

```text
_run_migration_job(map_id, command)
  1. map_id / max_attempts 확인
  2. NEXT_MIG_INFO job 조회
  3. USE_YN / STATUS 사전 검사
  4. dependency 검사
  5. retry loop
     5-1. MIG_SQL 생성 또는 USER_EDITED SQL 재사용
     5-2. MIG_SQL 실행
     5-3. VERIFY_SQL 생성 또는 USER_EDITED SQL 재사용
     5-4. VERIFY_SQL 실행
     5-5. 검증 통과 시 PASS 저장 후 반환
  6. retry 소진 시 FAIL 상태 저장 후 반환
```

## 4. 사전 검사

처음에 `_load_job(map_id)`로 `NEXT_MIG_INFO`에서 job을 조회합니다.

조회 컬럼:

- `MAP_ID`, `MAP_TYPE`, `FR_TABLE`, `TO_TABLE`
- `USE_YN`, `TRUNC_YN`, `PRIORITY`, `STATUS`
- `USER_EDITED`, `PRIOR_MAP_ID`, `CONDITION`
- `MIG_SQL`, `VERIFY_SQL`
- `BATCH_CNT`, `ELAPSED_SECONDS`, `RETRY_COUNT`
- `CREATED_AT`, `UPD_TS`

사전 검사 규칙:

- job이 없으면 `ok=false`, `error="job not found"`
- `USE_YN != 'Y'`이면 `status="SKIP"`
- `STATUS == 'PASS'`이면 이미 완료된 job으로 보고 `ok=true`
- `STATUS`가 다른 값으로 차 있으면 전체 migration 실행을 막음
- dependency가 통과하지 못하면 `NEXT_MIG_LOG`에 `DEPENDENCY` 로그를 쓰고 `WAITING` 계열 결과 반환

dependency는 두 가지를 봅니다.

- `PRIOR_MAP_ID`가 있으면 prior job의 `STATUS`가 `PASS`인지 확인
- 같은 `TO_TABLE`에서 더 높은 우선순위 job이 아직 `PASS`가 아니면 대기

## 5. MIG SQL 생성

MIG SQL 생성은 `_generate_mig_sql()`이 담당합니다.

```text
_generate_mig_sql()
  -> _load_job()
  -> USER_EDITED 확인
  -> _check_dependencies()
  -> _load_details()
  -> _render_sql_prompt()
  -> _call_llm()
  -> _extract_sql(..., key="migration_sql")
  -> _sanitize_migration_sql()
```

`USER_EDITED='Y'`이고 `MIG_SQL`이 이미 있으면 LLM을 호출하지 않고 기존 SQL을 보존합니다. 그렇지 않으면 `mig_sql_prompt` input을 렌더링해 LLM을 호출합니다.

LLM 응답은 JSON의 `migration_sql` 키에서 꺼냅니다. 이후 `_sanitize_migration_sql()`에서 다음을 검증합니다.

- 비어 있으면 실패
- 정확히 하나의 SQL statement여야 함
- `INSERT`로 시작해야 함
- `TRUNCATE`, `COMMIT`, `ROLLBACK`, `DELETE`, `UPDATE`, `MERGE`, `DROP`, `ALTER` 포함 금지

성공하면 `_run_migration_job()`의 `steps`에 `generate_mig_sql` 결과가 추가되고, `NEXT_MIG_LOG`에 `GENERATE_MIG_SQL` 로그가 기록됩니다.

## 6. MIG SQL 실행

MIG SQL 실행은 `_execute_sql_script()`가 담당합니다.

실행 전 처리:

- `_sanitize_migration_sql()`을 다시 호출해 SQL 안전성 확인
- `TRUNC_YN='Y'`이면 `_truncate_target()`으로 target table truncate
- truncate 성공 시 `NEXT_MIG_LOG`에 `TRUNCATE` 로그 기록

실행 후 처리:

- SQL을 실행하고 commit
- affected row count가 0 이하이면 실패 처리
- 성공하면 `steps`에 `execute_mig_sql` 결과 추가
- 실패하면 `FAIL-INSERT`로 기록하고 retry 가능하면 다음 attempt로 이동

## 7. VERIFY SQL 생성

VERIFY SQL 생성은 `_generate_verify_sql()`이 담당합니다.

```text
_generate_verify_sql()
  -> _load_job()
  -> USER_EDITED 확인
  -> _check_dependencies()
  -> _load_details()
  -> _render_sql_prompt()
  -> _call_llm()
  -> _extract_sql(..., key="verification_sql")
  -> _sanitize_verify_sql()
```

`USER_EDITED='Y'`이고 `VERIFY_SQL`이 있으면 LLM을 호출하지 않고 기존 SQL을 보존합니다. 그렇지 않으면 `verify_sql_prompt` input을 렌더링해 LLM을 호출합니다.

LLM 응답은 JSON의 `verification_sql` 키에서 꺼냅니다. 이후 `_sanitize_verify_sql()`에서 다음을 검증합니다.

- 비어 있으면 실패
- 정확히 하나의 SQL statement여야 함
- `SELECT` 또는 `WITH`로 시작해야 함
- `TRUNCATE`, `COMMIT`, `ROLLBACK`, `INSERT`, `DELETE`, `UPDATE`, `MERGE`, `DROP`, `ALTER` 포함 금지

성공하면 `steps`에 `generate_verify_sql` 결과가 추가되고, `NEXT_MIG_LOG`에 `GENERATE_VERIFY_SQL` 로그가 기록됩니다.

## 8. VERIFY SQL 실행과 PASS 판정

검증 실행은 `_execute_verify_sql_with_rows()`가 담당합니다.

```text
VERIFY_SQL 실행
  -> rows 없음: 실패
  -> 각 row의 각 값 확인
     -> 빈 값이면 실패
     -> 숫자로 변환했을 때 0이면 통과
     -> 0이 아니면 mismatch 실패
```

모든 반환 값이 0이면 `verify_ok=true`가 되고 최종 성공 처리로 넘어갑니다.

성공 시 저장:

- `_save_final_sql(map_id, last_mig_sql, last_verify_sql)`
- `_update_job_status(map_id, "PASS", elapsed, retry_count)`
- `_write_log(..., LOG_TYPE="VERIFY_SQL", STEP_NAME="VERIFY", STATUS="PASS", MESSAGE="Migration Success")`

성공 반환:

```json
{
  "ok": true,
  "map_id": 101,
  "status": "PASS",
  "message": "Migration completed",
  "elapsed_seconds": 12,
  "retry_count": 0,
  "steps": [...]
}
```

## 9. Retry 동작

`max_attempts`는 command의 `max_attempts`가 우선이고, 없으면 component input `default_max_attempts`를 사용합니다.

retry loop에서 실패 context는 다음 attempt의 SQL 생성 prompt에 전달됩니다.

```text
retry_count = attempt - 1
last_error = last_failure.error
last_sql = last_mig_sql 또는 last_verify_sql
```

실패 지점별 동작:

- MIG SQL 생성 실패: `FAIL-INSERT`, 다음 attempt에서 MIG SQL 재생성
- MIG SQL 실행 실패: `FAIL-INSERT`, 다음 attempt에서 MIG SQL 재생성
- VERIFY SQL 생성 실패: `FAIL-TEST`, 다음 attempt에서 VERIFY SQL 재생성
- VERIFY SQL 실행 실패: `FAIL-TEST`, 다음 attempt에서 VERIFY SQL 재생성

주의할 점은 MIG SQL 실행이 한 번 성공하면 `mig_executed=True`가 되어 이후 verify 실패 retry에서는 MIG SQL을 다시 실행하지 않고 verify SQL 쪽만 재시도합니다.

retry를 모두 소진하면 다음을 저장합니다.

- `_save_final_sql()`로 마지막 생성 SQL 저장
- `_update_job_status()`로 `FAIL-INSERT`, `FAIL-TEST`, 또는 `FAIL` 저장
- `_write_log(..., LOG_TYPE="JOB_FAIL", STEP_NAME="FINAL")`

실패 반환:

```json
{
  "ok": false,
  "map_id": 101,
  "status": "FAIL-TEST",
  "error": "Mismatch found: ...",
  "elapsed_seconds": 30,
  "retry_count": 2,
  "steps": [...]
}
```

## 10. 최종 채팅 메시지 생성

`MigrationCommandTool.run_command()`은 최종적으로 `Data(data=result)`를 반환합니다. 실제 사용자에게 보이는 문장은 tool이 직접 만들지 않고, Langflow의 Chat Agent/LLM이 tool result를 바탕으로 작성합니다.

성공이면 보통 다음 정보를 요약합니다.

- `map_id`
- `status=PASS`
- `message="Migration completed"`
- `elapsed_seconds`
- `retry_count`
- 주요 `steps`

실패이면 다음 정보를 요약합니다.

- `map_id`
- `status`
- `error`
- 실패한 step
- retry 횟수
- `NEXT_MIG_LOG`에서 추가 확인 가능하다는 안내

예시 최종 메시지:

```text
DB migration 작업이 완료되었습니다.
- MAP_ID: 101
- 상태: PASS
- 소요 시간: 12초
- retry_count: 0
- 처리 단계: MIG_SQL 생성 -> MIG_SQL 실행 -> VERIFY_SQL 생성 -> VERIFY_SQL 검증
```

실패 예시:

```text
DB migration 작업이 실패했습니다.
- MAP_ID: 101
- 상태: FAIL-TEST
- 원인: Mismatch found: ...
- retry_count: 2
NEXT_MIG_LOG에서 해당 MAP_ID의 JOB_FAIL/ROW_ERROR 로그를 확인해야 합니다.
```

## Active Supervisor Agent와의 차이

현재 active 구조에서 “db migration 전체 작업 진행”은 보통 `MigrationCommandTool` 직접 호출보다 Supervisor Agent를 타는 것이 맞습니다.

```text
User chat
  -> Chat Agent
  -> Supervisor Agent
  -> _run_batch_supervisor_cycle()
     -> poll_jobs
     -> supervisor_decide
     -> run_data_migration
     -> _run_migration_job(config, map_id)
     -> _mig__run_migration_job(...)
  -> result
  -> Chat Agent final message
```

차이점:

- `MigrationCommandTool`: 단일 `map_id`를 받아 즉시 전체 migration job 실행
- `Supervisor Agent`: DB command/pending job을 poll하고, LLM supervisor decision으로 `run_data_migration` route를 선택한 뒤 단일 cycle 실행

따라서 운영 기준으로는 Supervisor Agent flow를 문서화하고, `unused/migration_command_tool.py`는 legacy 단독 tool 또는 로직 참고용으로 두는 편이 맞습니다.
