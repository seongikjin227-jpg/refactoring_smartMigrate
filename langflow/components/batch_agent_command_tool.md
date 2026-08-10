# Batch Agent Command Tool

File: `langflow/components/batch_agent_command_tool.py`

This is a single Langflow component for background batch monitoring tests.
It does not execute migration or SQL conversion jobs.

The same file is also the background service entrypoint:

```bash
python langflow/components/batch_agent_command_tool.py
```

Deploying the custom component only registers it in Langflow. To keep it always
running, the server/process startup command must execute this same file.

## Commands

```json
{"action":"start"}
```

```json
{"action":"stop"}
```

```json
{"action":"status"}
```

## Runtime Behavior

When started, the component stays alive while `NEXT_BATCH_CONTROL` is `RUNNING`.
Every 10 seconds it counts pending DB migration jobs from `NEXT_MIG_INFO`.

Pending condition:

```sql
USE_YN = 'Y'
AND STATUS IS NULL
```

It writes one row to `NEXT_BATCH_LOG` with:

```text
EVENT_TYPE = HEARTBEAT
AGENT_NAME = BATCH_MONITOR
MESSAGE = 작업대상이 N건 있습니다.
```

No separate service file is used.
