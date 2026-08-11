# Agents Package

Supervisor가 호출하는 실제 업무 agent를 담습니다.

## 공통 호출 규칙

```text
SupervisorGraph.run_action_node()
  -> supervisor/tools/* tool
  -> 각 Agent.process_job(job)
  -> Workflow 또는 Graph
  -> Repository에 결과 저장
```

각 agent 폴더는 다음 기준으로 구성합니다.

- `*Agent.py`: Supervisor가 호출하는 공개 진입점입니다.
- `*Workflow.py` 또는 `*Graph.py`: job 하나를 처리하는 실행 흐름입니다.
- `*State.py`: workflow 실행 중 공유되는 상태입니다.
- 역할이 분명한 helper 파일: 해당 agent 내부에서만 쓰는 세부 함수입니다.

LangGraph를 꼭 여러 node 파일로 쪼개지는 않습니다. 실행 순서가 고정된 agent는 `Workflow.py` 하나로 읽히게 유지하고, 분기/상태 전이가 큰 agent만 `Graph.py`를 둡니다.

## 하위 agent

- `db_migration/`: `NEXT_MIG_INFO` 기반 DB migration을 처리합니다. LangGraph 노드와 retry 분기가 큽니다.
- `sql_conversion/`: `NEXT_SQL_INFO` 기반 SQL conversion을 처리합니다. coordinator의 retry loop와 generation/validation graph가 중심입니다.
- `sql_tuning/`: conversion이 끝난 `TO_SQL`을 튜닝하고 검증합니다.
- `sql_formatting/`: `TUNED_TO_SQL` 또는 `TO_SQL`을 저장용 formatted SQL로 변환합니다.
