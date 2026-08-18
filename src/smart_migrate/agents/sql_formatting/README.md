# SQL Formatting Agent

`TUNED_TO_SQL` 또는 `TO_SQL`을 LLM formatting prompt 기준으로 정리해 `FORMATTED_SQL`에 저장하는 agent입니다.

이 agent는 SQL 의미를 바꾸거나 검증하지 않습니다. conversion/tuning이 끝난 SQL을 사용자가 읽기 쉬운 형태로 저장하는 후처리만 담당합니다.

## 진입점

```text
SupervisorSqlFormattingTool.run_sql_formatting(row_ids)
  -> SqlFormattingAgent.process_job(job)
     -> SqlFormattingWorkflow.run(job)
```

`SqlFormattingWorkflow`는 LangGraph를 사용하지 않습니다. formatting 대상 row는 repository polling 단계에서 이미 선별됩니다.

## 대상 Job 조건

formatting 대상은 `SqlJobRepository.get_formatting_jobs()`에서 조회합니다.

```text
STATUS_TUNING in TUNING_SUCCESS_STATUSES
AND FORMATTED_SQL is null or empty
```

따라서 정상 경로에서는 `TUNED_TO_SQL` 또는 `TO_SQL`이 존재해야 합니다. 둘 다 없으면 formatting 대상 데이터가 잘못 들어온 것이므로 예외로 봅니다.

## 전체 실행 순서

```text
SqlFormattingWorkflow.run(job)
  1. 입력 SQL 선택
     - source_sql = job.tuned_sql or job.to_sql_text

  2. LLM formatting 호출
     - generate_formatted_sql(job, input_sql)
     - sql_indent_format_prompt.json 사용

  3. DB 저장
     - update_formatted_sql(row_id, formatted_sql)
     - FORMATTED_SQL 저장 후 종료
```

## 입력 선택 기준

```text
source_sql = job.tuned_sql or job.to_sql_text
```

우선순위는 다음과 같습니다.

1. `TUNED_TO_SQL`: tuning agent가 생성한 최종 SQL
2. `TO_SQL`: tuning 결과 SQL이 없을 때 conversion 결과 SQL

## Formatting 방식

formatting은 무조건 LLM prompt를 사용합니다.

```text
generate_formatted_sql(job, input_sql)
  -> sql_indent_format_prompt.json
  -> LLM 호출
  -> formatted_sql 반환
```

사용자가 설정한 `sql_indent_format_prompt.json`의 formatting 규칙이 기준입니다. 별도 로컬 formatter나 fallback formatter는 사용하지 않습니다.

## 저장 컬럼

`SqlJobRepository.update_formatted_sql()`가 다음 값만 갱신합니다.

- `FORMATTED_SQL`: formatting된 SQL
- `UPD_TS`: 갱신 시각

formatting agent는 conversion/tuning status를 변경하지 않습니다. 별도의 formatting status도 저장하지 않습니다.

## 반환값

`SqlFormattingWorkflow.run()`은 저장이 끝나면 supervisor log용 문자열 `"FORMATTED"`를 반환합니다.

이 반환값은 DB status가 아닙니다. DB에는 `FORMATTED_SQL` 저장만 수행됩니다.

## 주요 파일

- `SqlFormattingAgent.py`: supervisor-facing 진입점입니다.
- `SqlFormattingWorkflow.py`: job 1건의 formatting workflow와 `FORMATTED_SQL` 저장을 담당합니다.
- `SqlFormattingState.py`: formatting workflow state입니다.
- `../sql_conversion/SqlLlmService.py`: `generate_formatted_sql()` LLM 호출 helper를 제공합니다.
