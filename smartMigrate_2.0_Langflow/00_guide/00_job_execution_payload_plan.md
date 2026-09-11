# Job Execution Payload Plan

이 문서는 사용자가 요청한 작업 실행 파라미터가 01 -> 02 -> 06 -> 08 -> A 컴포넌트로 전달되는 방식을 관리한다.

## 원칙

- 사용자의 원문 요청은 항상 `user_request`로 유지한다.
- 자연어에서 `map_id`, `sql_id`, `space_nm`을 이해하는 책임은 01 LLM이 1차로 가진다.
- 06은 전체 작업 목록을 미리 싣지 않는다. 대시보드 수준의 작업 가능 카운트와, 명시 요청이 있을 때의 요청 대상만 조회한다.
- 08은 작업 도메인과 실행 모드만 결정한다. 전체 실행의 실제 대상 목록은 10A/12A/15A/17A/18A가 DB에서 조회한다.
- `NEXT_SQL_INFO`에는 `USE_YN`을 참조하지 않는다. `USE_YN='Y'` 필터는 DB Migration의 `NEXT_MIG_INFO`에만 적용한다.

## 공통 Payload 필드

```json
{
  "route": "JOB_EXECUTION",
  "user_request": "사용자 원문",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  },
  "execution_scope": "all|domain|targeted|unknown",
  "requested_domain": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|FULL_WORKFLOW|UNKNOWN"
}
```

## 특정 Job 요청

예: `맵 아이디 101번 진행해줘`

01 LLM 출력:

```json
{
  "route": "JOB_EXECUTION",
  "user_request": "맵 아이디 101번 진행해줘",
  "execution_scope": "targeted",
  "requested_domain": "MIG",
  "target_filter": {
    "map_ids": [101],
    "sql_ids": [],
    "space_nms": []
  }
}
```

02:

- route가 `JOB_EXECUTION`이면 payload를 그대로 06으로 전달한다.

06:

- `job_availability`에 전체 잔여 카운트를 담는다.
- `target_filter`가 있으면 해당 대상의 현재 상태를 `requested_target_status`에 담는다.
- 해당 대상이 현재 실행 가능한 상태이면 `requested_jobs`에 담는다.

08:

- `requested_jobs`와 `requested_target_status`를 보고 targeted 실행 여부를 결정한다.
- 실행 가능하면 `selected_jobs = requested_jobs`로 10A/12A/15A/17A 중 하나로 보낸다.

## 완전 전체 작업 실행

예: `전체 작업 실행해줘`, `남은 작업 다 돌려줘`

01 LLM 출력:

```json
{
  "route": "JOB_EXECUTION",
  "user_request": "전체 작업 실행해줘",
  "execution_scope": "all",
  "requested_domain": "FULL_WORKFLOW",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  }
}
```

06:

- `job_availability` 카운트만 조회한다.
- `requested_jobs`는 빈 배열이다.

08:

- `job_availability` 합계가 1 이상이면 `FULL_WORKFLOW`, `all_pending`으로 보낸다.
- `selected_jobs`는 비워 둔다.

18A:

- `all_pending`이면 DB에서 실제 전체 작업 목록을 조회하고 정렬한다.

## 도메인 전체 작업 실행

예: `DB Mig 전체 진행해줘`, `SQL Conversion 남은 거 다 실행해줘`

01 LLM 출력:

```json
{
  "route": "JOB_EXECUTION",
  "user_request": "DB Mig 전체 진행해줘",
  "execution_scope": "domain",
  "requested_domain": "MIG",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  }
}
```

06:

- `job_availability` 카운트만 조회한다.
- `requested_jobs`는 빈 배열이다.

08:

- `requested_domain`에 맞는 route와 `all_pending`을 선택한다.
- 선행 조건이 남아 있으면 `PREREQUISITE_REQUIRED`를 선택한다.

10A/12A/15A/17A:

- `all_pending`이면 각자 DB에서 자기 도메인의 실제 작업 목록을 조회한다.

## 잔여 조건

- MIG 잔여: `NEXT_MIG_INFO.USE_YN='Y'` 이고 `STATUS IS NULL` 또는 `USER_EDITED='Y' AND STATUS LIKE 'FAIL-%'`
- SQL Conversion 잔여: `STATUS_CONVERSION IS NULL` 또는 `USER_EDITED='Y' AND STATUS_CONVERSION LIKE 'FAIL-%'`
- SQL Tuning 잔여: `STATUS_CONVERSION IN ('PASS', 'PASS-CONVERSION')` 이고 `STATUS_TUNING IS NULL` 또는 `USER_EDITED='Y' AND STATUS_TUNING LIKE 'FAIL-%'`
- SQL Formatting 잔여: `STATUS_TUNING IN ('PASS', 'PASS-TUNING')` 이고 `FORMATTED_SQL`이 비어 있음

## 10C DB Migration PoC Executor 결정 사항

- 운영 `src/smart_migrate/agents/db_migration` 코드는 이번 변경에서 수정하지 않는다.
- 10C Langflow executor만 다음 기준을 적용한다.
- `USER_EDITED='Y'`이고 `MIG_SQL`이 있으면 기존 `MIG_SQL`을 재사용한다.
- `USER_EDITED='Y'`이고 `VERIFY_SQL`이 비어 있으면 LLM으로 `VERIFY_SQL`만 생성한다.
- LLM이 생성한 `MIG_SQL`/`VERIFY_SQL`은 생성 성공 직후 `NEXT_MIG_INFO`에 저장한다.
- migration SQL 실행 결과 `affected_rows=0`이어도 실행은 `PASS`로 본다.
- 단, `affected_rows=0`인 사실은 step log message에 남긴다.
- 10C의 migration prompt는 외부 파일 입력이나 파일 로딩이 아니라 코드 내부 최하단 상수로 관리한다.
- 코드 내부 prompt 내용은 `src/smart_migrate/config/prompts/migration_prompt.json`을 최대한 그대로 유지한다.
