# Langflow Integration

Langflow 또는 외부 HTTP client가 SmartMigrate 기능을 호출할 수 있게 하는 FastAPI wrapper입니다.

## 호출 구조

```text
HTTP request
  -> LangflowApi endpoint
  -> repository에서 job 조회
  -> 선택된 job을 agent.process_job(job)으로 실행
  -> 실행 결과 payload 반환
```

## 주요 endpoint 흐름

- `health()`: API 상태를 반환합니다.
- `get_agent_status()`: supervisor runtime 상태를 조회합니다.
- `start_agent()`, `stop_agent()`, `pause_agent()`, `resume_agent()`: runtime flag 파일 또는 supervisor stop 요청을 제어합니다.
- `queue_agent_command()`: `runtime/chat_command.json`에 사용자 명령을 저장해 다음 supervisor cycle에 반영합니다.
- `list_pending_*_jobs()`: 각 job repository의 pending job을 조회합니다.
- `run_*_job()`: 요청된 job 또는 첫 pending job을 선택하고 `_run_with_timing()`으로 agent를 실행합니다.
