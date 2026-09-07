# SmartMigrate Langflow Component POC

이 디렉터리는 SmartMigrate POC에서 Langflow IDE 기반 컴포넌트 개발과 Phoenix/OpenInference tracing 테스트를 정리하기 위한 공간입니다.

현재 개발 방식은 다음 두 계층으로 나뉩니다.

- Langflow IDE 계층: 각 업무 단계를 독립 Custom Component로 분리하고, 입력/출력 포트를 연결해 전체 workflow를 구성합니다.
- LangGraph 내부 실행 계층: 각 Custom Component 내부에서 실제 업무 절차, retry, stage routing, finalize 처리를 `StateGraph`로 구현합니다.

즉 Langflow는 컴포넌트 조립과 실행 UI 역할을 하고, 컴포넌트 내부의 복잡한 상태 전이와 재시도 제어는 LangGraph가 담당합니다.

## Directory

```text
langflow_260831_poc_complete/
  10C_migOneJobPocExecutor.py
  12C_sqlConversionOneJobPocExecutor.py
  15C_sqlTuningOneJobPocExecutor.py
  17C_sqlFormattingOneJobPocExecutor.py
  phoenix_test/
    18D_phoenixSessionDashboardTest.py
    README.md
```

## Main Components

### 10C DB Migration

파일: `../10C_migOneJobPocExecutor.py`

역할:

- DB migration SQL 생성
- migration SQL 실행
- verification SQL 생성 및 검증
- 실패 stage 기준 retry
- `NEXT_MIG_INFO` 상태/로그 업데이트

주요 수정 위치:

- prompt 수정: 파일 상단 `MIGRATION_PROMPT_TEMPLATE`
- Langflow 입력값 수정: `inputs`
- 내부 workflow 수정: `_run_langgraph_workflow()`
- SQL 생성 로직 수정: `_node_generate_sql()`
- 실행/검증 로직 수정: `_execute_migration_sql()`, `_execute_verification_sql()`
- DB row 업데이트 수정: `_update_row()`

LangGraph node:

```text
generate -> execute -> verify -> finalize
                  \-> retry_prepare -/
```

### 12C SQL Conversion

파일: `../12C_sqlConversionOneJobPocExecutor.py`

역할:

- FROM SQL을 Oracle 19c TO-BE SQL로 변환
- bind SQL 생성 및 bind set 추출
- test SQL 생성 및 row count 검증
- RAG GENERAL/SEARCH 조회
- correct SQL hint 조회
- 실패 stage 기준 retry
- `NEXT_SQL_INFO` 상태/로그 업데이트

주요 수정 위치:

- prompt 수정: 파일 상단 `SQL_PROMPT_TEMPLATES`
- Langflow 입력값 수정: `inputs`
- 내부 workflow 수정: `_run_langgraph_workflow()`
- TO-BE SQL 생성: `_generate_tobe_sql()`
- BIND SQL 생성: `_generate_bind_payload()`
- bind parameter 이름 추출: `_bind_names()`
- bind set 최대 3개 제한: `_build_bind_sets()`, `_bind_set_prompt_text()`
- TEST SQL 생성: `_generate_test_sql()`
- RAG 조회/로그: `_load_rag_general_rules()`, `_retrieve_rag_examples()`, `_log_rag_context()`
- correct SQL hint 조회/로그: `_correct_sql_hints_text()`
- DB row 업데이트: `_update_row()`

LangGraph node:

```text
prepare_source -> generate_tobe -> generate_bind -> generate_test -> finalize
                                      \-> retry_prepare -/
```

Retry 기준:

- `FAIL-TOBE`: TO-BE SQL 생성부터 재시도
- `FAIL-BIND`: BIND SQL 생성부터 재시도
- `FAIL-TEST`: TEST SQL 생성부터 재시도

### 15C SQL Tuning

파일: `../15C_sqlTuningOneJobPocExecutor.py`

역할:

- TO-BE SQL 튜닝
- tuned SQL 검증용 test SQL 생성
- baseline TO-BE SQL과 tuned SQL row count 비교
- RAG GENERAL/SEARCH 조회
- 실패 stage 기준 retry
- `NEXT_SQL_INFO` tuning 상태/로그 업데이트

주요 수정 위치:

- prompt 수정: 파일 상단 `SQL_PROMPT_TEMPLATES`
- Langflow 입력값 수정: `inputs`
- 내부 workflow 수정: `_run_langgraph_workflow()`
- 튜닝 적용: `_generate_tuned_sql()`
- tuned test SQL 생성: `_generate_tuned_test_sql()`
- bind set 최대 3개 제한: `_load_bind_sets_json()`
- RAG 조회/로그: `_retrieve_tuning_context()`, `_log_rag_context()`
- 실패 stage 계산: `_failure_stage()`
- DB row 업데이트: `_update_row()`

LangGraph node:

```text
load_rules -> apply_tuning -> validate_tuned -> finalize
                             \-> retry_prepare -/
```

`validate_tuned` node는 이름상 validation이지만, 내부에서 먼저 `_generate_tuned_test_sql()`을 호출합니다. 따라서 `FAIL-TEST` retry는 tuning SQL을 다시 만들지 않고 `GENERATE_TUNED_TEST_SQL` 단계부터 재시작합니다.

### 17C SQL Formatting

파일: `../17C_sqlFormattingOneJobPocExecutor.py`

역할:

- 생성된 SQL 컬럼을 보기 좋게 formatting
- 여러 formatting 대상 SQL을 DB에서 batch load
- LLM에 JSON 배열로 한 번에 요청
- JSON 배열 응답을 받아 각 원본 row/column에 업데이트

주요 수정 위치:

- prompt 수정: 파일 상단 `SQL_FORMAT_PROMPT`, `SQL_FORMAT_BATCH_PROMPT`
- Langflow 입력값 수정: `inputs`
- batch formatting 흐름: `_run_batch_formatting()`
- SQL batch load: `_load_generated_sqls()`
- LLM batch 호출: `_call_formatter_llm_batch()`
- LLM batch 응답 파싱: `_parse_formatter_batch_response()`
- SQL update: `_update_generated_sql()`

주의:

- CLOB 값은 DB connection/cursor가 살아 있는 동안 `_lob_to_str()`로 읽어야 합니다.
- 17C는 현재 LLM 호출과 DB load는 batch 처리하지만, DB update는 각 대상 컬럼별로 수행합니다.

## Phoenix Test Component

파일: `18D_phoenixSessionDashboardTest.py`

역할:

- 18D dashboard message를 Phoenix/OpenInference session span으로 기록합니다.
- Langflow message 출력은 그대로 통과시키고, trace 결과를 별도 `Data` output으로 반환합니다.

Langflow input:

- `dashboard_message`: 18D dashboard에서 나온 최종 메시지
- `loop_result`: workflow 실행 결과 payload
- `session_id`: 명시하지 않으면 `loop_result.workflow_run_id`, `run_id`, 또는 UUID 사용
- `span_name`: 기본값 `smartmigrate.dashboard_message`
- `phoenix_project_name`: 지정하면 `PHOENIX_PROJECT_NAME` 환경변수 기본값으로 사용

Langflow output:

- `message`: dashboard message text
- `trace_result`: Phoenix span write 결과와 session metadata

주요 수정 위치:

- span attribute 수정: `_write_session_span()`
- session helper import 호환성 수정: `_openinference_session_helpers()`
- dashboard message 추출 방식 수정: `_extract_message_text()`
- loop result parsing 수정: `_parse_payload()`

## Development Model

### 1. Langflow IDE에서 보는 것

Langflow IDE에서는 각 `.py` 파일이 하나의 Custom Component입니다.

개발자가 먼저 확인할 항목:

- `display_name`: Langflow UI에 보이는 이름
- `description`: 컴포넌트 설명
- `name`: 내부 component identifier
- `inputs`: Langflow node 입력 포트
- `outputs`: Langflow node 출력 포트
- output method: 보통 `run_job()`, `build_message()`, `build_trace_result()`

Langflow에서 포트를 추가하거나 이름을 바꾸려면 먼저 `inputs`/`outputs`를 수정해야 합니다.

### 2. LangGraph에서 고치는 것

업무 stage, retry, 실패 후 재시작 지점은 대부분 `_run_langgraph_workflow()` 안에 있습니다.

수정 순서:

1. node 함수 확인
2. `workflow.add_node()` 확인
3. `workflow.add_conditional_edges()` 확인
4. 실패 시 `last_status`, `last_message`, `resume_stage`, `node_failed`가 어떻게 바뀌는지 확인
5. finalize에서 DB status가 어떻게 저장되는지 확인

### 3. Prompt를 고치는 위치

Prompt는 각 컴포넌트 파일 상단에 둡니다.

- 10C: `MIGRATION_PROMPT_TEMPLATE`
- 12C: `SQL_PROMPT_TEMPLATES`
- 15C: `SQL_PROMPT_TEMPLATES`
- 17C: `SQL_FORMAT_PROMPT`, `SQL_FORMAT_BATCH_PROMPT`

주의:

- Python `.format()` 또는 `.format_map()`을 사용하는 prompt 안에서 literal `{...}`가 필요하면 brace를 escape해야 합니다.
- MyBatis `#{id}` 예시는 prompt 문자열 안에서 `#{{id}}`처럼 작성합니다.
- JSON 예시의 `{`와 `}`는 `{{`와 `}}`로 작성합니다.

## Logging

주요 로그는 `smartmigrate.workflow` logger로 남깁니다.

일반 형태:

```python
extra={
    "workflow_log": [
        map_id,
        category,
        component,
        level,
        step_name,
        status,
        retry_count,
        generated_or_context_text,
    ]
}
```

최근 정리 방향:

- `RAG_GENERAL`, `RAG_SEARCH` 성공 로그는 따로 남기지 않고 `RAG_CONTEXT` 한 번으로 묶습니다.
- TO/BIND/TEST correct SQL hint도 개별 로그 대신 `LOAD_SQL_HINTS` 한 번으로 묶습니다.
- 과거 POC/실행 구분용 로그 prefix는 더 이상 사용하지 않습니다.

## DB Tables

컴포넌트가 주로 읽고 쓰는 테이블:

- `NEXT_MIG_INFO`: DB migration job, migration SQL, verification SQL, migration status
- `NEXT_SQL_INFO`: SQL conversion/tuning/formatting job, TO/BIND/TEST/TUNED/FORMATTED SQL, status
- `NEXT_MIG_MAPPING`: migration mapping rule
- `NEXT_MIG_RAG_INFO`: RAG general rule
- Milvus `SM_RAG_RULES`: SQL conversion/tuning search RAG
- Milvus `SM_CORRECT_SQL_CONVERSION`: conversion correct SQL hint
- Milvus `SM_CORRECT_SQL_MIGRATION`: migration correct SQL hint

테이블/컬럼 존재 여부는 helper에서 가능한 한 동적으로 확인합니다.

- `_table_columns()`
- `_select_expr()`
- `_qualify()`

## Common Fix Points

### Prompt 결과가 깨질 때

확인할 곳:

- 각 파일 상단 prompt constant
- `_build_prompt()`
- `_log_prompt()`
- `_clean_generated_sql()`

자주 나는 문제:

- prompt 내부 `{}` escape 누락
- markdown fence 제거 실패
- SQL 끝 세미콜론 처리
- MyBatis bind marker가 실행 SQL에 남음

### CLOB 오류가 날 때

확인할 곳:

- `_lob_to_str()`
- `cur.fetchone()` 또는 `cur.fetchall()` 후 LOB를 connection 밖에서 읽고 있지 않은지
- CLOB update 시 driver가 타입을 못 잡는지

원칙:

- Oracle LOB locator는 connection/cursor가 살아 있을 때 문자열로 변환합니다.
- 필요하면 update cursor에 `setinputsizes(..., oracledb.DB_TYPE_CLOB)`를 추가합니다.

### Retry 시작 지점이 이상할 때

확인할 곳:

- `_failure_stage()`
- `retry_prepare_node`
- `resume_stage`
- `workflow.add_conditional_edges("retry_prepare", ...)`

기준:

- 실패한 stage의 SQL 생성부터 다시 시작합니다.
- 이미 성공한 이전 stage output은 가능한 한 재사용합니다.

### Bind set 개수가 너무 많을 때

확인할 곳:

- 12C `_build_bind_sets()`
- 12C `_bind_set_prompt_text()`
- 15C `_load_bind_sets_json()`

기준:

- bind set은 최대 3개입니다.
- source data가 3개 미만이면 있는 만큼만 사용합니다.
- 기존 DB에 저장된 bind set이 3개를 초과해도 prompt에는 앞 3개만 전달합니다.

## Minimal Validation

수정 후 최소 검증:

```powershell
python -m py_compile `
  langflow_260831_poc_complete\10C_migOneJobPocExecutor.py `
  langflow_260831_poc_complete\12C_sqlConversionOneJobPocExecutor.py `
  langflow_260831_poc_complete\15C_sqlTuningOneJobPocExecutor.py `
  langflow_260831_poc_complete\17C_sqlFormattingOneJobPocExecutor.py `
  langflow_260831_poc_complete\phoenix_test\18D_phoenixSessionDashboardTest.py
```

Prompt를 수정했다면 별도로 `.format()`/`.format_map()` brace 오류가 없는지도 확인해야 합니다.

## Practical Rule

처음 보는 개발자는 다음 순서로 보면 됩니다.

1. Langflow IDE에서 component input/output 연결을 확인합니다.
2. 해당 `.py` 파일의 `inputs`, `outputs`, `run_job()`을 확인합니다.
3. 내부 업무 흐름은 `_run_langgraph_workflow()`의 node와 edge를 확인합니다.
4. LLM 결과가 문제면 파일 상단 prompt와 `_clean_generated_sql()`을 확인합니다.
5. DB 저장 값이 문제면 `_update_row()` 또는 `_update_generated_sql()`을 확인합니다.
6. retry가 문제면 `last_status`, `resume_stage`, `retry_prepare_node`를 확인합니다.
