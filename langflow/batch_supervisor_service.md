# Batch Supervisor Service

`BatchAgentCommandTool`은 이제 사용자 채팅 명령을 DB 제어 row에 반영하는 control agent다.
실제 배치 while loop는 `batch_supervisor_service.py`가 담당한다.

## 실행 구조

```text
Langflow Chat
  -> BatchAgentCommandTool
  -> NEXT_BATCH_CONTROL start/stop/status 갱신

Server Startup
  -> python langflow/batch_supervisor_service.py
  -> NEXT_BATCH_CONTROL 확인
  -> RUNNING이면 while loop 실행
```

서버가 재시작되면 startup command에서 service 파일을 다시 실행하면 된다.
`SMARTMIGRATE_BATCH_AUTO_START=true`이면 service boot 시 `NEXT_BATCH_CONTROL`이 `RUNNING`이 아니어도 자동으로 시작 요청을 만든다.

## 주요 환경변수

```text
SMARTMIGRATE_DB_HOST
SMARTMIGRATE_DB_PORT
SMARTMIGRATE_DB_SERVICE_NAME
SMARTMIGRATE_DB_USERNAME
SMARTMIGRATE_DB_PASSWORD
SMARTMIGRATE_LLM_BASE_URL
SMARTMIGRATE_LLM_API_KEY
SMARTMIGRATE_LLM_MODEL
SMARTMIGRATE_SYSTEM_SCHEMA
SMARTMIGRATE_SOURCE_SCHEMA
SMARTMIGRATE_TARGET_SCHEMA
SMARTMIGRATE_BATCH_AUTO_START=true
SMARTMIGRATE_BATCH_IDLE_SLEEP_SECONDS=10
```

프롬프트는 환경변수 값으로 직접 넣거나 파일 경로로 넣을 수 있다.

```text
SMARTMIGRATE_MIG_SQL_PROMPT_FILE
SMARTMIGRATE_VERIFY_SQL_PROMPT_FILE
SMARTMIGRATE_TO_SQL_PROMPT_FILE
SMARTMIGRATE_BIND_SQL_PROMPT_FILE
SMARTMIGRATE_TEST_SQL_PROMPT_FILE
```

JSON 설정 파일을 쓰려면 `SMARTMIGRATE_BATCH_CONFIG`에 파일 경로를 지정한다.
JSON 값은 환경변수로 만든 config를 덮어쓴다.
