# Batch Supervisor Agent

File: `langflow/components/Supervisor_Agent.py`

This is a single Langflow custom component for the always-on SmartMigrate batch
supervisor. It does not require chat input.

## Langflow Input

Only one text input is exposed:

```text
Run YN = Y
```

`Y` starts the supervisor loop. `N` requests stop. `STATUS` returns current
runtime/control status.

## Runtime Behavior

Each cycle runs this LangGraph shape:

```text
poll_jobs -> supervisor_decide -> run_data_migration | run_sql_conversion | no_job
```

The supervisor prompt chooses one route from the current job snapshot. The
component still applies a guard so an invalid route cannot execute a missing
job.

Priority:

```text
DB_MIGRATION -> SQL_CONVERSION -> NO_JOB
```

DB migration pending condition:

```sql
NEXT_MIG_INFO.USE_YN = 'Y'
AND NEXT_MIG_INFO.STATUS IS NULL
```

SQL conversion pending condition:

```sql
NEXT_SQL_INFO.STATUS_CONVERSION IS NULL
```

## Tool Routes

The component is intentionally self-contained. The route functions call the
existing internal command-tool logic in this same file:

```text
run_data_migration  -> _run_migration_job()
run_sql_conversion  -> _run_sql_conversion_job()
no_job              -> sleep 10 seconds
```

## Standalone Entrypoint

The same file can also be used as a server startup process:

```bash
python langflow/components/Supervisor_Agent.py
```

No separate background service file is used.
