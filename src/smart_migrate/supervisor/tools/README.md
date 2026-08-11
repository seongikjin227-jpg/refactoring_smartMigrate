# Supervisor Tools Package

Supervisor graph에서 호출하는 LangChain tool wrapper 모음입니다. tool은 registry에서 job을 꺼내고 `SupervisorJobRegistry.init_callbacks()`로 등록된 callback을 호출합니다. SQL 생성, DB migration, tuning, formatting의 실제 업무 로직은 `agents/`에 있습니다.

## 호출 구조

```text
SupervisorGraph.run_action_node()
  -> run_data_migration.invoke({"map_id": ...})
     또는 run_sql_conversion.invoke({"row_id": ...})
     또는 run_sql_tuning.invoke({"row_ids": [...]})
     또는 run_sql_formatting.invoke({"row_ids": [...]})
  -> SupervisorJobRegistry callback 호출
  -> agent.process_job(job)
  -> refresh_jobs_after_tool()
```

## Tool 역할

- `SupervisorMigrationTool.run_data_migration(map_id)`: `mig_registry`에서 migration job을 찾아 `mig_proc(job)`을 호출합니다.
- `SupervisorSqlConversionTool.run_sql_conversion(row_id)`: `sql_registry`에서 SQL conversion job을 찾아 `sql_proc(job)`을 호출합니다.
- `SupervisorSqlTuningTool.run_sql_tuning(row_ids)`: `tuning_registry`의 job을 순차 처리합니다.
- `SupervisorSqlFormattingTool.run_sql_formatting(row_ids)`: `formatting_registry`의 job을 순차 처리합니다.
- `SupervisorCycleTool.request_wait(seconds)`: pause flag와 stop flag를 확인하며 cycle 사이 대기를 수행합니다.
- `SupervisorSqlChain.py`: SQL conversion 이후 tuning/formatting을 이어서 실행할 때 쓰는 continuation helper입니다.

## 주의점

한 cycle에서 실제 job은 `SupervisorJobRegistry.claim_job_execution()` 기준으로 1건만 실행되도록 제어합니다. tool을 추가할 때도 이 정책을 깨지 않도록 registry와 callback 경계를 유지해야 합니다.
