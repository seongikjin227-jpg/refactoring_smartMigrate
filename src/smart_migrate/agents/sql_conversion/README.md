# SQL Conversion Agent

`NEXT_SQL_INFO`의 SQL conversion job을 처리합니다.

실행 흐름:

```text
Supervisor tool
  -> SqlConversionAgent.process_job()
  -> SqlConversionCoordinator
  -> SqlConversionGraph
  -> TO_SQL / BIND_SQL / TEST_SQL 생성과 검증
  -> SqlJobRepository, SqlLogRepository
```

이 agent는 conversion 작업 안에서 TO_SQL, BIND_SQL, TEST_SQL 단계가 이어집니다. 단, 너무 많은 node 파일로 쪼개지지 않도록 핵심 흐름은 `SqlConversionCoordinator.py`가 읽히게 유지합니다.

현재 `SqlLlmService.py`에는 conversion/tuning/formatting LLM helper가 일부 섞여 있습니다. private helper 의존이 커서 함수 단위 분리는 별도 단계에서 진행하는 것이 안전합니다.
