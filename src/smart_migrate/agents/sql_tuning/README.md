# SQL Tuning Agent

`TO_SQL`을 입력으로 받아 `TUNED_TO_SQL`과 tuning 결과를 생성합니다.

## 진입점

```text
SupervisorSqlTuningTool.run_sql_tuning()
  -> SqlTuningAgent.process_job(job)
     -> SqlTuningWorkflow.run(job)
```

## Workflow 호출 구조

```text
SqlTuningWorkflow.run()
  -> SqlTuningState 생성
  -> mapping rules 조회
  -> state.tobe_sql = job.to_sql_text
  -> _SqlTuningRunner.run(state)
     -> tuning rule/RAG example 조회
     -> tune_tobe_sql()
     -> SELECT이면 tuned SQL 비교 검증
  -> update_cycle_result(...)
```

`SqlTuningWorkflow`는 `sql_conversion.SqlConversionCoordinator.SqlTuningAgent`를 runner로 재사용합니다. 따라서 실제 튜닝 반복은 coordinator 내부 runner의 `for iteration in range(1, max_iterations + 1)`와 SELECT 검증 retry 흐름에서 수행됩니다.

## 주요 파일

- `SqlTuningAgent.py`: supervisor-facing 진입점입니다.
- `SqlTuningWorkflow.py`: job 1건의 fixed workflow와 최종 저장을 담당합니다.
- `SqlTuningRuleRetrieveNode.py`: tuning/conversion rule 조회, RAG 검색, lexical fallback, hit count 갱신을 담당합니다.
- `SqlTuningState.py`: tuning workflow state입니다.

실행 순서가 고정되어 있으므로 여러 LangGraph node 파일로 쪼개지 않고 `SqlTuningWorkflow.py` 하나로 읽히게 둡니다.
