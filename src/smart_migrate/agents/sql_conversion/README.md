# SQL Conversion Agent

`NEXT_SQL_INFO`의 SQL conversion job을 처리합니다.

## 진입점

```text
SupervisorSqlConversionTool.run_sql_conversion()
  -> SqlConversionAgent.process_job(job)
     -> TobeMultiAgentCoordinator.process_job(job)
```

`SqlConversionAgent.__init__()`은 `TobeMultiAgentCoordinator`를 만들고, conversion 단계에서는 내부 tuning을 `SqlTuningAgent(max_iterations=0)`으로 비활성화합니다. 튜닝은 별도 `sql_tuning/` agent가 처리합니다.

## Coordinator 반복 구조

```text
TobeMultiAgentCoordinator.process_job()
  -> mapping rule 조회
  -> sql_length/map_type 분류 저장
  -> while retry_count < max_retries
     -> JobExecutionState 생성
     -> retry prompt context 구성
     -> self.graph.invoke({"execution": state})
     -> SELECT가 아니면 test 없이 conversion success 저장
     -> SELECT이고 test PASS이면 success 저장
     -> 실패/예외이면 backoff 후 retry
  -> max_retries 초과 시 failure 저장
```

`max_retries`는 현재 3입니다. 실패 원인은 `last_error`로 다음 attempt의 prompt context에 들어갑니다.

## Graph 1회 실행 흐름

```text
SqlConversionGraph.build_migration_workflow()
  -> START
  -> tobe_generation.generate
  -> route_after_generation()
     -> tobe_generation.validate 또는 END
  -> END
```

- `TobeSqlGenerationAgent.generate()`: `TO_SQL`을 생성합니다. 사용자가 편집한 `TO_SQL`이 있으면 LLM 호출 없이 재사용합니다.
- `TobeSqlGenerationAgent.validate()`: bind parameter 추출, `BIND_SQL` 생성/실행, bind set 생성, `TEST_SQL` 생성/실행, status 평가를 수행합니다.
- `SqlConversionValidateNode.execute_binding_query()`: bind SQL을 실행해 test case 값을 만듭니다.
- `SqlConversionValidateNode.execute_test_query()`: generated test SQL을 실행합니다.
- `evaluate_status_from_test_rows()`: row count 비교 결과로 PASS/FAIL을 판정합니다.

## 주요 파일

- `SqlConversionAgent.py`: supervisor-facing 진입점입니다.
- `SqlConversionCoordinator.py`: retry loop, state 구성, 결과 저장을 담당합니다.
- `SqlConversionGraph.py`: generation/validation LangGraph를 구성합니다.
- `SqlLlmService.py`: TO_SQL, BIND_SQL, TEST_SQL, tuning/formatting용 LLM 호출 helper를 포함합니다.
- `SqlBindCases.py`: bind parameter 이름 추출과 bind set JSON 생성을 담당합니다.
- `CorrectSqlRagService.py`: 과거 correct SQL 기반 hint 검색을 담당합니다.
- `SqlConversionValidateNode.py`: binding/test SQL 실행과 status 판정을 담당합니다.
- `SqlConversionState.py`: coordinator와 graph state schema입니다.

현재 `SqlLlmService.py`에는 conversion/tuning/formatting LLM helper가 일부 섞여 있습니다. private helper 의존이 커서 함수 단위 분리는 별도 단계에서 진행하는 것이 안전합니다.
