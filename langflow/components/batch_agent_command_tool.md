# Batch Agent Command Tool

파일: `langflow/components/batch_agent_command_tool.py`

이 component는 사용자 채팅 명령을 배치 실행 제어 테이블에 반영하는 control agent다.
실제 배치 while loop는 `langflow/batch_supervisor_service.py`가 담당한다.

## 역할

- `start`: `NEXT_BATCH_CONTROL`의 `BATCH_AGENT` row를 `RUNNING`으로 변경한다.
- `stop`: `NEXT_BATCH_CONTROL`에 `STOP_REQUESTED`를 기록한다.
- `status`: `NEXT_BATCH_CONTROL` 상태와 heartbeat를 조회한다.

채팅 요청 안에서 worker thread를 만들지 않는다.
Langflow chat request는 command 결과를 반환하면 끝난다.

## 실행 프로세스

```text
사용자 채팅
  -> Batch Agent
  -> Batch Agent Command Tool
  -> NEXT_BATCH_CONTROL 변경

서버 시작
  -> python langflow/batch_supervisor_service.py
  -> NEXT_BATCH_CONTROL 확인
  -> RUNNING이면 while loop 실행
```

## 지원 command_json

```json
{"action":"start"}
```

```json
{"action":"stop"}
```

```json
{"action":"status"}
```

## 상태 기준

`status.running=true`는 아래 조건을 만족할 때만 반환한다.

```text
NEXT_BATCH_CONTROL.STATUS = RUNNING
STOP_REQUESTED_YN = N
HEARTBEAT_AT이 grace 시간 안에 있음
LAST_EVENT가 START만 남아있는 상태가 아님
```

`status.requested_running=true`이고 `running=false`이면 start 요청은 DB에 들어갔지만 service heartbeat가 아직 확인되지 않은 상태다.

## 배치 실행 규칙

배치 supervisor는 LangGraph로 아래 흐름을 구성한다.

```text
poll_jobs
  -> supervisor_decide
  -> run_data_migration | run_sql_conversion | no_job
```

`supervisor_decide`는 supervisor prompt로 현재 job 후보와 정책을 보고 route JSON을 생성한다.
별도 `validate_decision` node는 두지 않는다. 대신 conditional route에서 존재하지 않는 job을 실행하려는 경우만 최소 보정한다.
DB_MIGRATION은 `NEXT_MIG_INFO`에서 `USE_YN = Y`, `STATUS IS NULL`인 1건을 찾는다.
SQL_CONVERSION은 `NEXT_SQL_INFO`에서 `STATUS_CONVERSION IS NULL`인 1건을 찾는다.
터미널과 `runtime/agent.log`에는 cycle 시작, poll 결과, supervisor decision, 실행 agent, job_id, status, error가 출력된다.

## 운영 주의

서버 재시작 후 자동 실행이 필요하면 Langflow 서버 startup command 또는 process manager에서 아래 프로세스를 함께 실행한다.

```bash
python langflow/batch_supervisor_service.py
```

환경변수와 실행 예시는 `langflow/batch_supervisor_service.md`를 따른다.
