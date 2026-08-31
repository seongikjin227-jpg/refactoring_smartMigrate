# A/C/D Replacement Guide

## 전제

현재 서비스 호출이 `response_output()`에서 완성된 `Data`를 한 번 받는 구조라면, Langflow 내부 Chat Output이 여러 번 실행되어도 플랫폼에는 마지막 객체만 보일 수 있다.

Langflow 공식 문서 기준 `/api/v1/run/{flow_id}?stream=true`는 token event stream을 지원한다. 다만 이 스트림이 custom component의 `Message` 출력 이벤트까지 서비스에 전달하는지는 실제 Langflow 버전/호출 방식으로 확인해야 한다.

참고:

- https://docs.langflow.org/api-flows-run
- https://docs.langflow.org/api

## A. sleep interval 테스트

목적: 18D가 job마다 메시지를 만든 뒤, 다음 loop로 넘어가기 전 sleep을 걸었을 때 Chat Output branch가 플랫폼에 보이는지 확인한다.

기존 연결:

```text
18D_fullWorkflowDashboard.Message -> Chat Output
18D_fullWorkflowDashboard.Loop Result -> 18B_fullWorkflowLoop
```

대체 연결:

```text
18D_fullWorkflowDashboard.Message -> 04A Sleep Interval Output Probe.message_payload
18D_fullWorkflowDashboard.Loop Result -> 04A Sleep Interval Output Probe.loop_result_input
04A Sleep Interval Output Probe.Message -> Chat Output
04A Sleep Interval Output Probe.Loop Result -> 18B_fullWorkflowLoop
```

권장 설정:

```text
sleep_seconds = 1.0
```

판정:

- 플랫폼에서 job마다 새 메시지 또는 partial update가 보이면 A 방식 가능성이 있다.
- 플랫폼에서 4~5시간 후 마지막 메시지만 보이면 A는 실패다.
- A가 실패하면 `sleep()` 문제가 아니라 API 호출/응답 전달 구조 문제다.

## C. side-channel progress sink 테스트

목적: Langflow 최종 응답과 별개로 job마다 progress event를 저장하고, 플랫폼이 별도 polling/SSE로 읽는다.

기존 연결:

```text
18D_fullWorkflowDashboard.Message -> Chat Output
18D_fullWorkflowDashboard.Loop Result -> 18B_fullWorkflowLoop
```

파일 sink 테스트 연결:

```text
18D_fullWorkflowDashboard.Message -> 04C Progress Side Channel Sink.message_payload
18D_fullWorkflowDashboard.Loop Result -> 04C Progress Side Channel Sink.loop_result_input
04C Progress Side Channel Sink.Message -> Chat Output
04C Progress Side Channel Sink.Loop Result -> 18B_fullWorkflowLoop
```

권장 설정:

```text
sink_mode = FILE
output_file = progress_events.jsonl
run_key = 테스트용 고정값 또는 session_id
include_full_payload = false
```

DB sink 테스트 전 DDL:

```sql
-- loop_output_test_260831/04C_next_workflow_progress_ddl.sql
```

DB sink 설정:

```text
sink_mode = ORACLE_DB 또는 BOTH
progress_table = NEXT_WORKFLOW_PROGRESS
db_host/db_port/db_service_name/db_username/db_password/system_schema = 기존 DB 설정과 동일
```

서비스 쪽 polling SQL 예:

```sql
SELECT EVENT_TS,
       JOB_ROUTE,
       JOB_INDEX,
       TOTAL_JOBS,
       STATUS,
       OK_YN,
       MAP_ID,
       SPACE_NM,
       SQL_ID,
       MESSAGE_TEXT
  FROM NEXT_WORKFLOW_PROGRESS
 WHERE RUN_KEY = :run_key
 ORDER BY EVENT_TS
```

판정:

- Chat Output이 마지막만 보이더라도 `NEXT_WORKFLOW_PROGRESS` 또는 JSONL에 job별 event가 쌓이면 C는 성공이다.
- 운영형으로는 파일보다 DB table 또는 플랫폼 API sink가 맞다.

## D. Langflow streaming/event API 테스트

목적: 서비스가 REST 최종 응답 대신 streaming response를 읽도록 바꿨을 때 실시간 표시가 가능한지 확인한다.

제공 파일:

```text
04D_langflow_streaming_client.py
04D_langflow_streaming_proxy.py
```

직접 client 테스트:

```powershell
$env:LANGFLOW_SERVER_URL="http://localhost:7860"
$env:LANGFLOW_FLOW_ID="FLOW_ID"
$env:LANGFLOW_API_KEY="..."
$env:LANGFLOW_INPUT_VALUE="전체 작업 실행"
python .\04D_langflow_streaming_client.py
```

서비스 proxy 테스트:

```powershell
pip install fastapi uvicorn httpx
$env:LANGFLOW_SERVER_URL="http://localhost:7860"
$env:LANGFLOW_FLOW_ID="FLOW_ID"
$env:LANGFLOW_API_KEY="..."
uvicorn 04D_langflow_streaming_proxy:app --host 0.0.0.0 --port 18080
```

플랫폼은 아래 endpoint를 SSE로 읽는다.

```text
POST http://localhost:18080/smartmigrate/run-stream
```

요청 예:

```json
{
  "input_value": "전체 작업 실행",
  "input_type": "chat",
  "output_type": "chat",
  "session_id": "stream-test"
}
```

판정:

- token/event가 실행 중 계속 들어오면 D가 가능하다.
- LLM token만 오고 18D job progress가 안 오면 Langflow streaming만으로는 부족하고 C와 결합해야 한다.
- 아무 것도 중간에 안 오고 마지막만 오면 현재 endpoint 또는 서비스 HTTP client가 buffering 중이다.

## 권장 테스트 순서

1. A: 연결만 바꿔 `sleep_seconds=1.0`으로 짧게 확인한다.
2. C: 파일 sink로 `progress_events.jsonl`이 job마다 증가하는지 확인한다.
3. C: 운영 후보면 DB sink로 바꿔 플랫폼 polling을 붙인다.
4. D: `/api/v1/run/{flow_id}?stream=true`를 직접 client로 테스트한다.
5. D: 직접 client가 성공하면 proxy 또는 플랫폼 HTTP client를 streaming 처리로 바꾼다.

## 내 판단

4~5시간짜리 작업을 사용자가 실시간으로 봐야 한다면 C가 가장 안정적이다. D는 성공하면 좋지만, Langflow streaming이 custom component progress까지 보장하지 않을 수 있다. A는 반드시 해볼 가치는 있지만, 실패하면 빠르게 버리고 C로 가는 것이 맞다.
