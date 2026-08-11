# SQL Formatting Agent

`TUNED_TO_SQL`을 우선 입력으로 받아 `FORMATTED_SQL`을 생성합니다. 없으면 `TO_SQL`을 사용합니다.

## 진입점

```text
SupervisorSqlFormattingTool.run_sql_formatting()
  -> SqlFormattingAgent.process_job(job)
     -> SqlFormattingWorkflow.run(job)
```

## Workflow 호출 구조

```text
SqlFormattingWorkflow.run()
  -> SqlFormattingState 생성
     -> source_sql = job.tuned_sql or job.to_sql_text
  -> source_sql이 없으면 SKIP
  -> generate_formatted_sql(job, input_sql)
  -> SqlJobRepository.update_formatted_sql(row_id, formatted_sql)
  -> PASS 또는 FAIL 반환
```

## 주요 파일

- `SqlFormattingAgent.py`: supervisor-facing 진입점입니다.
- `SqlFormattingWorkflow.py`: job 1건의 fixed workflow와 저장을 담당합니다.
- `SqlFormattingNode.py`: 저장용 SQL 포맷팅 helper입니다.
- `SqlFormattingState.py`: formatting workflow state입니다.

실행 순서가 단순하므로 LangGraph를 쓰지 않고 workflow class 하나로 읽히게 둡니다.
