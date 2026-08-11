# Supervisor Tools Package

Supervisor graph에서 `ToolNode(agent_tools)`가 실행하는 LangChain tool wrapper 모음입니다. 여기의 함수들은 실제 agent가 아니라, registry에서 job을 찾고 `SupervisorJobRegistry.init_callbacks()`로 등록된 `Agent.process_job()` callback을 호출하는 wrapper입니다.

## 호출 구조

```text
SupervisorGraph
  -> LLM tool_call 선택
  -> ToolNode(agent_tools)
  -> supervisor/tools/* wrapper
  -> SupervisorJobRegistry callback
  -> Agent.process_job(job)
  -> Agent 내부 Graph/Workflow
```

## Agent Wrapper 역할

- `SupervisorMigrationTool.run_data_migration(map_id)`: `mig_registry`에서 migration job을 찾아 `MigrationOrchestrator.process_job()` callback을 호출합니다.
- `SupervisorSqlConversionTool.run_sql_conversion(row_id)`: `sql_registry`에서 SQL conversion job을 찾아 `SqlConversionAgent.process_job()` callback을 호출합니다.
- `SupervisorSqlTuningTool.run_sql_tuning(row_ids)`: `tuning_registry`에서 SQL tuning job을 찾아 `SqlTuningAgent.process_job()` callback을 호출합니다.
- `SupervisorSqlFormattingTool.run_sql_formatting(row_ids)`: `formatting_registry`에서 SQL formatting job을 찾아 `SqlFormattingAgent.process_job()` callback을 호출합니다.
- `SupervisorCycleTool.request_wait(seconds)`: pause/stop flag를 확인하며 cycle 사이 대기를 수행합니다.
- `SupervisorSqlContinuation.py`: LangChain chain이 아닙니다. SQL conversion PASS 후 tuning을 이어 실행하고, tuning PASS 후 formatting을 이어 실행하는 후속 agent continuation helper입니다.

## Continuation 흐름

```text
run_sql_conversion(row_id)
  -> SqlConversionAgent.process_job()
  -> conversion PASS이면 run_tuning_continuation(row_id)
     -> SqlTuningAgent.process_job()
     -> tuning PASS이면 run_formatting_continuation(row_id)
        -> SqlFormattingAgent.process_job()
```

이 continuation은 같은 `NEXT_SQL_INFO` row에 대한 후속 pipeline입니다. supervisor가 다시 LLM에게 tuning/formatting 선택을 묻지 않습니다.

## 실행 제한

한 cycle에서 실제 job 실행은 `SupervisorJobRegistry.claim_job_execution()` 기준으로 1건만 허용합니다. LLM이 여러 tool call을 만들더라도 첫 번째 job wrapper 이후의 job wrapper는 `SKIP`됩니다.
