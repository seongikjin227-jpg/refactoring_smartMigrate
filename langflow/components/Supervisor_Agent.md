# Supervisor Agent 컴포넌트

파일: `langflow/components/Supervisor_Agent.py`

`Supervisor_Agent.py`는 SmartMigrate 백그라운드 배치 처리를 담당하는 Langflow 커스텀 컴포넌트다.
사용자 채팅 입력을 받지 않고, `Run YN` 값이 `Y`일 때 background supervisor loop를 시작한다.
실행 이후 제어는 DB의 `NEXT_BATCH_CONTROL`, 1회성 작업 지정은 `NEXT_BATCH_COMMAND`에서 받는다.

## 핵심 구조

```text
Run YN=Y
  -> background supervisor process 시작
  -> NEXT_BATCH_CONTROL RUNNING 획득
  -> poll_jobs
  -> NEXT_BATCH_COMMAND PENDING 1건 claim
  -> supervisor_decide
  -> run_data_migration | run_sql_conversion | no_job
  -> NEXT_BATCH_LOG 저장
  -> 다음 cycle 반복
```

중요한 운영 기준:
- worker thread를 만들지 않는다.
- `NEXT_BATCH_CONTROL`로 running/stop/heartbeat 상태를 관리한다.
- `NEXT_BATCH_COMMAND`로 map_id/sql_id 같은 1회성 명령을 받는다.
- `Run YN=Y`이면 별도 background process를 시작한다.
- `Run YN=N`이면 loop를 시작하지 않는다.
- cycle 결과는 `NEXT_BATCH_LOG`에 저장한다.
- DB migration 상세 로그는 `NEXT_MIG_LOG`에 저장한다.
- SQL conversion 상세 로그는 `NEXT_SQL_LOG`에 저장한다.

## Langflow Input

컴포넌트 input은 다음 값만 받는다.

```text
Run YN
MIG SQL Prompt
VERIFY SQL Prompt
TO SQL Prompt
BIND SQL Prompt
TEST SQL Prompt
```

`Run YN` 값:

```text
Y       loop 실행
N       loop 미실행
STATUS  현재 메모리 상태 조회
```

프롬프트 input:
- `MIG SQL Prompt`: migration insert SQL 생성용
- `VERIFY SQL Prompt`: migration 검증 SQL 생성용
- `TO SQL Prompt`: SQL conversion TO_SQL 생성용
- `BIND SQL Prompt`: SQL conversion BIND_SQL 생성용
- `TEST SQL Prompt`: SQL conversion TEST_SQL 생성용

DB 접속 정보, LLM 설정, schema는 input으로 받지 않는다.

## Tool Mode

`Run YN` input은 `MessageTextInput(tool_mode=True)`로 설정되어 있다.

Agent에 Tool로 연결할 때 Agent가 전달할 수 있는 값은 `Run YN`뿐이다.
프롬프트 5개는 Tool Mode input이 아니므로 Agent가 임의로 변경하지 않는다.

Tool 호출 예:

```text
Run YN = Y       Supervisor loop 시작
Run YN = N       Supervisor loop 시작 안 함
Run YN = STATUS  현재 상태 조회
```

## 기본 설정

DB 기본값은 Python 파일의 `DEFAULT_DB_CONFIG`에서 관리한다.

```python
DEFAULT_DB_CONFIG = {
    "db_host": "10.0.0.1",
    "db_port": 1521,
    "db_service_name": "ORCL",
    "db_username": "SMARTMIGRATE",
    "db_password": "password",
    "system_schema": "SFAADM",
    "source_schema": "SFAMIG",
    "target_schema": "SFAADM",
}
```

LLM 기본값은 `DEFAULT_LLM_CONFIG`에서 관리한다.

```python
DEFAULT_LLM_CONFIG = {
    "llm_base_url": "",
    "llm_api_key": "",
    "llm_model": "claude-haiku-4-5-20251001",
    "llm_max_tokens": 4096,
    "llm_timeout_seconds": 900,
}
```

schema 고정값:

```text
system_schema = SFAADM
source_schema = SFAMIG
target_schema = SFAADM
```

## 자동 패키지 설치

missing package는 input 없이 항상 자동 설치한다.

```python
AUTO_INSTALL_MISSING_PACKAGES = True
```

Supervisor runtime에서 확인하는 패키지:
- `langchain-core`
- `langchain-openai`
- `langchain-community`
- `langgraph`
- `SQLAlchemy`
- `oracledb`

내부 migration/sql conversion 실행 로직에서도 필요한 패키지를 자동 설치한다.

## 작업 조회 조건

DB migration 작업 대상:

```sql
NEXT_MIG_INFO.USE_YN = 'Y'
AND NEXT_MIG_INFO.STATUS IS NULL
```

SQL conversion 작업 대상:

```sql
NEXT_SQL_INFO.STATUS_CONVERSION IS NULL
```

우선순위:

```text
DB_MIGRATION -> SQL_CONVERSION -> NO_JOB
```

DB migration job이 있으면 SQL conversion job보다 먼저 실행한다.
한 cycle에서는 최대 1건만 실행한다.

## LangGraph 흐름

각 cycle은 다음 그래프 형태로 실행된다.

```text
START
  -> poll_jobs
  -> supervisor_decide
  -> run_data_migration
       또는 run_sql_conversion
       또는 no_job
  -> END
```

`poll_jobs`:
- `NEXT_BATCH_COMMAND`에서 `PENDING` 명령 1건을 `CLAIMED`로 가져옴
- command에 `map_id`가 있으면 해당 DB migration 대상 1건 조회
- command에 `sql_id`가 있으면 해당 SQL conversion 대상 1건 조회
- command가 없으면 `NEXT_MIG_INFO`에서 DB migration 대상 1건 조회
- DB migration 대상이 없으면 `NEXT_SQL_INFO`에서 SQL conversion 대상 1건 조회

`supervisor_decide`:
- 현재 job snapshot을 LLM Supervisor에게 전달
- route JSON을 받음
- 허용 route:
  - `run_data_migration`
  - `run_sql_conversion`
  - `no_job`

route 보정:
- LLM이 잘못된 route를 반환해도 실제 job 존재 여부 기준으로 보정한다.
- migration job이 있으면 `run_data_migration`으로 보정한다.
- migration job이 없고 sql job이 있으면 `run_sql_conversion`으로 보정한다.
- 둘 다 없으면 `no_job`으로 보정한다.

## Supervisor Prompt

Supervisor system prompt는 `SUPERVISOR_SYSTEM_PROMPT`에 정의되어 있다.

목적:
- 채팅 없이 DB command와 현재 job snapshot만 보고 route 결정
- DB_MIGRATION 우선
- cycle당 1건만 실행
- JSON만 반환

필수 반환 형식:

```json
{"route":"run_data_migration | run_sql_conversion | no_job","reason":"short reason"}
```

## 실행 route

```text
run_data_migration
  -> _run_migration_job()
  -> 내부 migration command 로직 실행
  -> NEXT_MIG_LOG 기록

run_sql_conversion
  -> _run_sql_conversion_job()
  -> 내부 SQL conversion command 로직 실행
  -> NEXT_SQL_LOG 기록

no_job
  -> 10초 sleep
  -> 다음 cycle
```

## NEXT_BATCH_LOG 저장

Supervisor cycle 로그는 `NEXT_BATCH_LOG`에 저장한다.

저장 함수:
- `_write_batch_log_safe()`
- `_write_batch_log()`

저장 컬럼:

```text
RUN_ID
LOOP_NO
EVENT_TYPE
AGENT_NAME
JOB_ID
JOB_STATUS
MESSAGE
ERROR_MESSAGE
SLEEP_SECONDS
STARTED_AT
FINISHED_AT
ELAPSED_SECONDS
```

저장 이벤트:

```text
START
AUTO_START
JOB_SUCCESS
JOB_FAIL
NO_JOB
JOB_STOPPED
FATAL_ERROR
LOOP_ERROR
STOP_REQUESTED
STOPPED
SERVICE_ERROR
```

`RUN_ID`는 제어용이 아니라 로그 묶음 식별자다.

## NEXT_BATCH_COMMAND

Supervisor는 각 cycle 시작 시 `NEXT_BATCH_COMMAND`에서 `CONTROL_NAME='BATCH_AGENT'`이고
`COMMAND_STATUS='PENDING'`인 row 1건을 `CLAIMED`로 가져온다.

지원하는 payload:

```json
{"map_id": 101}
{"sql_id": "SEL_001", "space_nm": "userMapper"}
```

`COMMAND_TEXT`에 `map_id=101`, `sql_id=SEL_001 space_nm=userMapper` 형식으로 넣어도 된다.
cycle이 정상 종료되면 `DONE`, 오류가 발생하면 `FAILED`로 갱신한다.

## Standalone 실행

서버 startup에서 같은 파일을 직접 실행할 수 있다.

```bash
python langflow/components/Supervisor_Agent.py
```

standalone 실행 시 환경변수를 사용할 수 있다.

대표 환경변수:

```text
SMARTMIGRATE_RUN_YN
SMARTMIGRATE_DB_HOST
SMARTMIGRATE_DB_PORT
SMARTMIGRATE_DB_SERVICE_NAME
SMARTMIGRATE_DB_USERNAME
SMARTMIGRATE_DB_PASSWORD
SMARTMIGRATE_LLM_BASE_URL
SMARTMIGRATE_LLM_API_KEY
SMARTMIGRATE_LLM_MODEL
SMARTMIGRATE_MIG_SQL_PROMPT
SMARTMIGRATE_VERIFY_SQL_PROMPT
SMARTMIGRATE_TO_SQL_PROMPT
SMARTMIGRATE_BIND_SQL_PROMPT
SMARTMIGRATE_TEST_SQL_PROMPT
```

프롬프트는 환경변수 본문 또는 파일 경로 방식으로 전달할 수 있다.

```text
SMARTMIGRATE_MIG_SQL_PROMPT_FILE
SMARTMIGRATE_VERIFY_SQL_PROMPT_FILE
SMARTMIGRATE_TO_SQL_PROMPT_FILE
SMARTMIGRATE_BIND_SQL_PROMPT_FILE
SMARTMIGRATE_TEST_SQL_PROMPT_FILE
```

## 주의사항

- `Run YN=Y`는 background process를 시작하고 빠르게 응답한다.
- worker thread 방식이 아니라 별도 process 방식이다.
- `NEXT_BATCH_CONTROL` heartbeat가 살아 있으면 중복 start를 막는다.
- stop 요청은 `NEXT_BATCH_CONTROL.STOP_REQUESTED_YN='Y'` 또는 `STATUS='STOP_REQUESTED'`로 전달한다.
