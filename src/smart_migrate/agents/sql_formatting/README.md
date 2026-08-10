# SQL Formatting Agent

`TUNED_TO_SQL`을 우선 입력으로 받아 `FORMATTED_SQL`을 생성합니다. 없으면 `TO_SQL`을 사용합니다.

실행 흐름:

```text
Supervisor tool
  -> SqlFormattingAgent.process_job()
  -> SqlFormattingWorkflow.run()
  -> formatted SQL 생성
  -> SqlJobRepository.update_formatted_sql()
```

실행 순서가 단순하므로 `SqlFormattingWorkflow.py`와 `SqlFormattingState.py`만 둡니다.
