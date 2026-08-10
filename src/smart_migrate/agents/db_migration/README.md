# DB Migration Agent

`NEXT_MIG_INFO` 기반 DB migration job을 처리합니다.

실행 흐름:

```text
Supervisor tool
  -> MigrationAgent.process_job()
  -> MigrationGraph
  -> DDL 조회 / 의존성 확인 / SQL 생성 / 실행 / 검증
  -> MigrationJobRepository, MigrationHistoryRepository
```

이 agent는 LangGraph 기반 분기와 재시도 흐름이 비교적 크기 때문에 `MigrationGraph.py`를 유지합니다.
