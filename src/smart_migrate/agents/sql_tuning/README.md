# SQL Tuning Agent

`TO_SQL`을 입력으로 받아 `TUNED_TO_SQL`과 tuning 결과를 생성합니다.

실행 흐름:

```text
Supervisor tool
  -> SqlTuningAgent.process_job()
  -> SqlTuningWorkflow.run()
  -> RAG tuning rule 조회 / tuning SQL 생성 / tuned test 검증
  -> SqlJobRepository
```

실행 순서가 고정되어 있으므로 여러 LangGraph node 파일로 쪼개지 않고 `SqlTuningWorkflow.py` 하나로 읽히게 둡니다.
