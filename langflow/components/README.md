# Langflow Components

현재 사용하는 Langflow 컴포넌트는 세 개다.

```text
Chat_Agent.py
chat_agent_tools/Chat_Command_Tool.py
Supervisor_Agent.py
```

## Chat Agent

`Chat_Agent.py`는 Langflow Agent에 넣을 system prompt를 제공한다.
DB 변경은 직접 하지 않고, Agent가 아래 action tool을 호출하도록 지시한다.

```text
chat_agent_tools/Chat_Command_Tool.py
```

`Chat_Command_Tool.py`는 `command_json` 하나만 Tool Mode input으로 받는 단일 action 기반 tool이다.

지원 action:
- `enqueue_migration`: `NEXT_BATCH_COMMAND`에 migration 실행 명령 등록
- `enqueue_sql_conversion`: `NEXT_BATCH_COMMAND`에 SQL conversion 실행 명령 등록
- `request_stop`: `NEXT_BATCH_CONTROL`에 stop 요청 반영
- `status`: 현재 batch/job summary 조회
- `failure_summary`: FAIL summary 조회

Chat Agent는 migration/sql conversion을 직접 실행하지 않는다. 실행 요청은 DB command queue에 등록하고,
실제 처리는 `Supervisor_Agent.py`의 background loop가 수행한다.

## Supervisor Agent

`Supervisor_Agent.py`는 `Run YN=Y`로 background supervisor process를 시작한다.

실행 중에는 다음 DB 테이블을 사용한다.

```text
NEXT_BATCH_CONTROL   running/stop/heartbeat 제어
NEXT_BATCH_COMMAND   1회성 실행 명령 queue
NEXT_BATCH_LOG       cycle/event 로그
```

## Unused

기존에 Langflow Tool로 직접 실행하던 컴포넌트는 더 이상 사용하지 않는다.
참고용으로만 아래 폴더에 보관한다.

```text
unused/
  dashboard_command_tool.py
  dashboard_command_tool.md
  migration_command_tool.py
  sql_conversion_command_tool.py
  sql_conversion_command_tool.md
```
