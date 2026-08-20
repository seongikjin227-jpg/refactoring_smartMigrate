# 08 Job Execution Router 분기 설계

## 역할

`08_jobExecutionRouter.py`는 `06_getPendingJobs.py`가 조회한 작업 대상 요약과 사용자 요청을 함께 보고, 실행 파이프라인으로 보낼지 아니면 바로 Chat Output 메시지로 종료할지 결정한다.

이 컴포넌트는 LLM 판단을 사용한다. 단, Langflow output branch는 단순하게 유지한다.

| Output | 의미 |
|---|---|
| `mig_job` | DB Migration 실행 대상이 확정된 경우 |
| `sql_conversion_job` | SQL Conversion 실행 대상이 확정된 경우 |
| `sql_tuning_job` | SQL Tuning 실행 대상이 확정된 경우 |
| `sql_formatting_job` | SQL Formatting 실행 대상이 확정된 경우 |
| `prerequisite_required` | 작업 대상은 있지만 선행 작업이 남아 있어 지금 실행하면 안 되는 경우 |
| `no_runnable_job` | 요청한 작업 대상이 없거나 실행 업무 유형을 확정할 수 없는 경우 |

`prerequisite_required`와 `no_runnable_job`은 반드시 분리한다. 선행 작업이 남아 있는 것은 "대상이 없음"이 아니라 "지금 실행 순서가 아님"에 가깝기 때문이다.

## 입력 Payload

08번은 최소한 아래 정보를 받는다고 가정한다.

```json
{
  "user_request": "사용자 원문 요청",
  "pending_summary": {
    "migration_total": 0,
    "sql_conversion_total": 0,
    "sql_tuning_total": 0,
    "sql_formatting_total": 0
  },
  "pending_jobs": {
    "migration_jobs": [{"job_route": "MIG", "map_id": 101, "priority": 1, "prior_map_id": null}],
    "sql_conversion_jobs": [{"job_route": "SQL_CONVERSION", "space_nm": "SALES", "sql_id": "Q001", "priority": 1}],
    "sql_tuning_jobs": [{"job_route": "SQL_TUNING", "space_nm": "SALES", "sql_id": "Q002", "priority": 1}],
    "sql_formatting_jobs": [{"job_route": "SQL_FORMATTING", "space_nm": "SALES", "sql_id": "Q003", "priority": 1}],
    "job_lookup_jobs": []
  }
}
```

`06_getPendingJobs.py`는 CLOB SQL 본문을 넘기지 않는다. 08번은 실행 대상 판단에 필요한 count, 식별자, `priority`, `prior_map_id` 같은 경량 라우팅 메타데이터만 사용한다.

## LLM 반환 Payload

LLM은 아래 JSON만 반환한다.

```json
{
  "job_route": "MIG|SQL_CONVERSION|SQL_TUNING|SQL_FORMATTING|PREREQUISITE_REQUIRED|NO_RUNNABLE_JOB",
  "run_mode": "all_pending|targeted",
  "target_filter": {
    "map_ids": [],
    "sql_ids": [],
    "space_nms": []
  },
  "reason": "짧은 한국어 사유"
}
```

## 업무 우선순위

실행 업무는 아래 순서를 가진다.

1. DB Migration
2. SQL Conversion
3. SQL Tuning
4. SQL Formatting

뒤 단계 실행 요청이 들어왔는데 앞 단계 작업 대상이 남아 있으면 실행하지 않는다. 이 경우 output은 `prerequisite_required`이고, 메시지는 선행 업무를 먼저 진행하라고 안내한다.

예:

```text
SQL Conversion 작업을 실행할 수 없습니다.
DB Migration 작업 대상이 남아 있습니다. 해당 작업을 먼저 진행해주세요.
```

## 분기 유형

### 1. 전체 실행 요청

사용자가 특정 식별자 없이 전체 실행을 요청한 경우다.

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 또는 다음 노드 |
|---|---|---|---|
| `DB Migration 전체 진행해줘` | MIG pending count > 0 | `mig_job` | `09_executionPlanSummary` |
| `DB Migration 전체 진행해줘` | MIG pending count = 0 | `no_runnable_job` | `DB Migration 작업 대상이 없습니다.` |
| `SQL Conversion 전체 실행해줘` | MIG pending count = 0 and SQL Conversion pending count > 0 | `sql_conversion_job` | `09_executionPlanSummary` |
| `SQL Conversion 전체 실행해줘` | MIG pending count > 0 | `prerequisite_required` | `DB Migration 작업 대상이 남아 있습니다. 해당 작업을 먼저 진행해주세요.` |
| `SQL Conversion 전체 실행해줘` | MIG pending count = 0 and SQL Conversion pending count = 0 | `no_runnable_job` | `SQL Conversion 작업 대상이 없습니다.` |
| `SQL Tuning 전체 진행해줘` | MIG/Conversion pending count = 0 and Tuning pending count > 0 | `sql_tuning_job` | `09_executionPlanSummary` |
| `SQL Tuning 전체 진행해줘` | MIG 또는 Conversion pending count > 0 | `prerequisite_required` | 남아 있는 선행 업무를 먼저 진행하라고 안내 |
| `SQL Tuning 전체 진행해줘` | 선행 업무 없음 and Tuning pending count = 0 | `no_runnable_job` | `SQL Tuning 작업 대상이 없습니다.` |
| `SQL Formatting 전체 진행해줘` | MIG/Conversion/Tuning pending count = 0 and Formatting pending count > 0 | `sql_formatting_job` | `09_executionPlanSummary` |
| `SQL Formatting 전체 진행해줘` | 앞 단계 pending count > 0 | `prerequisite_required` | 남아 있는 선행 업무를 먼저 진행하라고 안내 |
| `SQL Formatting 전체 진행해줘` | 선행 업무 없음 and Formatting pending count = 0 | `no_runnable_job` | `SQL Formatting 작업 대상이 없습니다.` |

### 2. 업무 유형 없는 전체 실행 요청

사용자가 `대기 작업 전체 실행해줘`, `전체 작업 진행해줘`처럼 업무 유형을 지정하지 않은 경우다.

| 판단 조건 | Output | 처리 |
|---|---|---|
| MIG pending count > 0 | `mig_job` | 가장 앞 단계인 DB Migration 전체 실행 |
| MIG = 0 and Conversion pending count > 0 | `sql_conversion_job` | SQL Conversion 전체 실행 |
| MIG/Conversion = 0 and Tuning pending count > 0 | `sql_tuning_job` | SQL Tuning 전체 실행 |
| MIG/Conversion/Tuning = 0 and Formatting pending count > 0 | `sql_formatting_job` | SQL Formatting 전체 실행 |
| 모든 pending count = 0 | `no_runnable_job` | `실행할 작업 대상이 없습니다.` |

### 3. DB Migration 단건/복수 실행 요청

사용자가 `map_id`를 지정한 경우다.

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 또는 다음 노드 |
|---|---|---|---|
| `map_id=101 실행해줘` | MIG 작업 대상에 101 존재 | `mig_job` | `09_executionPlanSummary` |
| `map_id=101,102 실행해줘` | MIG 작업 대상에 101,102 존재 | `mig_job` | 선택된 map_id를 순차 실행 |
| `map_id=101 실행해줘` | MIG 작업 대상에 101 없음 | `no_runnable_job` | `map_id=101이 작업 대상에서 조회되지 않았습니다.` |
| `map_id=101 실행해줘` | map_id=101이 `USE_YN=N` 또는 이미 처리 완료라 pending 대상이 아님 | `no_runnable_job` | `map_id=101이 작업 대상에서 조회되지 않았습니다. 상태 변경이 필요하면 관리 기능으로 요청해주세요.` |

### 4. SQL Conversion 단건/복수 실행 요청

사용자가 `sql_id`, `space_nm`을 지정하고 변환을 요청한 경우다.

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 또는 다음 노드 |
|---|---|---|---|
| `sql_id=Q001 변환해줘` | MIG pending count = 0 and Conversion 대상에 Q001 존재 | `sql_conversion_job` | `09_executionPlanSummary` |
| `space_nm=SALES sql_id=Q001 변환해줘` | MIG pending count = 0 and 해당 조합 존재 | `sql_conversion_job` | `09_executionPlanSummary` |
| `sql_id=Q001 변환해줘` | MIG pending count > 0 | `prerequisite_required` | `DB Migration 작업 대상이 남아 있습니다. 해당 작업을 먼저 진행해주세요.` |
| `sql_id=Q001 변환해줘` | MIG pending count = 0 and Conversion 대상에 Q001 없음 | `no_runnable_job` | `sql_id=Q001이 SQL Conversion 작업 대상에서 조회되지 않았습니다.` |

### 5. SQL Tuning 단건/복수 실행 요청

SQL Tuning 작업 대상은 `STATUS_TUNING IS NULL`이고 `STATUS_CONVERSION = PASS-CONVERSION`인 건이다.

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 또는 다음 노드 |
|---|---|---|---|
| `sql_id=Q002 튜닝해줘` | 선행 pending 없음 and Tuning 대상에 Q002 존재 | `sql_tuning_job` | `09_executionPlanSummary` |
| `space_nm=SALES,HR 튜닝 진행해줘` | 선행 pending 없음 and 해당 space 대상 존재 | `sql_tuning_job` | 대상 조합을 순차 실행 |
| `sql_id=Q002 튜닝해줘` | MIG 또는 Conversion pending count > 0 | `prerequisite_required` | 남아 있는 선행 업무를 먼저 진행하라고 안내 |
| `sql_id=Q002 튜닝해줘` | 선행 pending 없음 and Tuning 대상에 Q002 없음 | `no_runnable_job` | `sql_id=Q002가 SQL Tuning 작업 대상에서 조회되지 않았습니다.` |

### 6. SQL Formatting 단건/복수 실행 요청

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 또는 다음 노드 |
|---|---|---|---|
| `sql_id=Q003 포맷팅해줘` | 선행 pending 없음 and Formatting 대상에 Q003 존재 | `sql_formatting_job` | `09_executionPlanSummary` |
| `space_nm=SALES,HR 포맷팅 진행해줘` | 선행 pending 없음 and 해당 space 대상 존재 | `sql_formatting_job` | 대상 조합을 순차 실행 |
| `sql_id=Q003 포맷팅해줘` | MIG/Conversion/Tuning pending count > 0 | `prerequisite_required` | 남아 있는 선행 업무를 먼저 진행하라고 안내 |
| `sql_id=Q003 포맷팅해줘` | 선행 pending 없음 and Formatting 대상에 Q003 없음 | `no_runnable_job` | `sql_id=Q003이 SQL Formatting 작업 대상에서 조회되지 않았습니다.` |

### 7. 실행 요청이지만 업무 유형이 불명확한 경우

| 사용자 요청 예시 | 판단 조건 | Output | 메시지 |
|---|---|---|---|
| `작업 실행해줘` | 업무 유형도 없고 pending 우선순위로도 결정하기 어려움 | `no_runnable_job` | `실행할 업무 유형을 확인할 수 없습니다. DB Migration, SQL Conversion, SQL Tuning, SQL Formatting 중 하나로 다시 요청해주세요.` |
| `이거 처리해줘` | 식별자도 업무 키워드도 없음 | `no_runnable_job` | 다시 요청 안내 |

## Message Output 사유

`prerequisite_required`와 `no_runnable_job`은 아래 기준으로 나눈다.

| Output | 사유 | 예시 메시지 |
|---|---|---|
| `prerequisite_required` | 선행 업무 잔여 | `DB Migration 작업 대상이 남아 있습니다. 해당 작업을 먼저 진행해주세요.` |
| `prerequisite_required` | targeted MIG 요청에서 06 payload로 확인 가능한 priority/prior_map_id 위반 | `map_id=101은 선행 map_id 또는 우선순위 작업이 남아 있어 지금 실행할 수 없습니다.` |
| `no_runnable_job` | 해당 업무 pending 없음 | `SQL Conversion 작업 대상이 없습니다.` |
| `no_runnable_job` | 요청한 식별자가 작업 대상에 없음 | `map_id=101이 작업 대상에서 조회되지 않았습니다.` |
| `no_runnable_job` | 업무 유형 불명확 | `실행할 업무 유형을 확인할 수 없습니다.` |

## 핵심 규칙

- `PREREQUISITE_REQUIRED`는 폐기 분기가 아니다. 작업 대상은 있지만 선행 조건 때문에 지금 실행하면 안 되는 경우를 담당한다.
- `NO_RUNNABLE_JOB`은 작업 대상 자체가 없거나 실행 요청을 확정할 수 없는 경우만 담당한다.
- 사용자가 요청한 식별자는 `target_filter`에 반드시 유지되어야 한다.
- 실행 파이프라인으로 넘어가는 경우에는 `selected_jobs`에 실제 실행 대상 식별자만 담는다.
- 사용자가 요청한 대상이 pending 조회 결과에 없으면 synthetic job을 만들지 않는다.
