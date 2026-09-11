# SmartMigrate Langflow POC

이 디렉터리는 SmartMigrate Langflow POC의 최종 컴포넌트 묶음입니다. Langflow IDE에서는 각 `.py` 파일을 Custom Component로 올려 노드 입출력을 연결하고, 실제 업무 실행은 각 컴포넌트 내부의 Python/LangGraph 로직이 담당합니다.

상세 아키텍처 문서는 `00_guide` 아래에 모아두고, 이 README는 프로젝트 구조, 공통 설계 특징, 실행 순서, 운영 확인 포인트만 요약합니다.

## 가이드 HTML 실행

`00_guide/00_developer_guidebook.html`은 브라우저에서 바로 열 수 있는 정적 HTML입니다.

### 방법 1. 파일 직접 열기

1. 파일 탐색기에서 `smartMigrate_2.0_Langflow\00_guide` 폴더로 이동합니다.
2. `00_developer_guidebook.html`을 더블클릭합니다.
3. 기본 브라우저에서 개발자 가이드가 열리는지 확인합니다.

### 방법 2. 로컬 HTTP 서버로 열기

일부 브라우저 보안 정책이나 링크 동작을 더 실제 환경처럼 확인하려면 로컬 서버로 여는 방식을 권장합니다.

```powershell
cd C:\Users\11824\Desktop\SmartMigrate_Refactoring\smartMigrate_2.0_Langflow
python -m http.server 8000
```

브라우저에서 아래 주소를 엽니다.

```text
http://localhost:8000/00_guide/00_developer_guidebook.html
```

이미 8000 포트를 쓰고 있으면 다른 포트를 지정합니다.

```powershell
python -m http.server 8001
```

## 전체 패키지 구조

```text
smartMigrate_2.0_Langflow/
  README.md

  00_guide/
    00_developer_guidebook.html
    00_architecture*.md
    00_logging_rules.txt
    00_job_execution_payload_plan.md
    00_main_logic_components.md
    99.LogHelper.py
    todolist.md

  01_agent_start/
    00A_logRuntimeStart.py
    01_requestClassifierPrompt.md
    02_intentRouter.py
    03_llmResponsePrompt.md

  02_flow_management/
    04_managementRouter.py
    04_dashboard.py
    04_currentProgress.py
    04_jobQaAgentPrompt.md
    04_jobQaCommandTool.py
    04_statusChange.py
    04_correctSqlInput.py
    04_ragGuideManager.py
    04_saveVectorDB.py

  03_job_execution/
    06_getRemainingJobs.py
    08_jobExecutionRouter.py
    10A_migJobsToLoopTable.py
    10B_migLoop.py
    10C_migOneJobPocExecutor.py
    10D_migIterationDashboard.py
    11_finalDashboard.py
    11B_failureCauseAnalyzer.py
    12A_sqlConversionJobsToLoopTable.py
    12B_sqlConversionLoop.py
    12C_sqlConversionOneJobPocExecutor.py
    12C_sql_conversion_mapping_rule_contract.md
    12D_sqlConversionIterationDashboard.py
    15A_sqlTuningJobsToLoopTable.py
    15B_sqlTuningLoop.py
    15C_sqlTuningOneJobPocExecutor.py
    15D_sqlTuningIterationDashboard.py
    17A_sqlFormattingJobsToLoopTable.py
    17B_sqlFormattingLoop.py
    17C_sqlFormattingOneJobPocExecutor.py
    17D_sqlFormattingIterationDashboard.py
    18A_fullWorkflowJobsToLoopTable.py
    18B_fullWorkflowLoop.py
    18D_fullWorkflowDashboard.py
```

## 폴더 역할

| 폴더 | 역할 |
|---|---|
| `00_guide` | 개발자 가이드, 상세 아키텍처, logging 규칙, payload 설계 문서 |
| `01_agent_start` | workflow 시작, 사용자 요청 분류, 1차 intent route, 일반 답변 prompt |
| `02_flow_management` | 04 관리 기능: dashboard, progress, Job QA, 상태 변경, Correct SQL 입력, RAG Guide 관리, Vector DB 동기화 |
| `03_job_execution` | 실제 작업 실행: 잔여 작업 조회, 실행 라우팅, DB Migration, SQL Conversion, SQL Tuning, SQL Formatting, 최종 dashboard |

## 파일 번호 규칙

파일 앞 번호는 Langflow 실행 흐름의 위치를 나타냅니다.

| 번호 | 역할 |
|---|---|
| `00` | 런타임 시작 또는 가이드 문서 |
| `01`-`03` | 사용자 요청 분류, 1차 라우팅, 일반 답변 |
| `04` | 관리성 요청 처리 |
| `06`-`08` | 실행 가능한 작업 조회와 실행 도메인 라우팅 |
| `10A`-`10D` | DB Migration 단일 도메인 실행 |
| `11` | 최종 대시보드와 실패 원인 분석 |
| `12A`-`12D` | SQL Conversion 실행 |
| `15A`-`15D` | SQL Tuning 실행 |
| `17A`-`17D` | SQL Formatting 실행 |
| `18A`-`18D` | DB Migration부터 Formatting까지 전체 Workflow 실행 |
| `99` | 공통 workflow log 작성 예시 |

`04_saveVectorDB.py`는 Vector DB 동기화 기능을 04 관리 영역으로 옮긴 파일입니다. 이제 Vector DB 동기화는 업무 실행이 아니라 04 관리 기능의 `VECTOR_DB_SYNC` route로 처리합니다.

## Langflow 공통 설계 특징

각 `.py` 파일은 Langflow Custom Component 하나를 기준으로 작성되어 있습니다. Langflow IDE에서 보이는 값은 주로 `display_name`, `description`, `name`, `inputs`, `outputs`입니다.

입력은 Langflow의 `Data`, `Message`, `SecretStrInput`, `StrInput`, `IntInput`, `BoolInput` 등을 사용합니다. 컴포넌트 내부에서는 `_parse_payload()`, `_secret_to_str()`, `_db_config()` 같은 helper로 Langflow 입력값을 일반 Python dict/string으로 정규화합니다.

출력은 보통 `Data`, `Message`, `DataFrame` 중 하나입니다. A 컴포넌트는 Loop 입력용 `DataFrame`을 만들고, B 컴포넌트는 Loop item/done 흐름을 만들고, C 컴포넌트는 단일 작업을 실행한 뒤 표준 payload를 반환하고, D 컴포넌트는 사용자에게 보여줄 진행 메시지를 만듭니다.

업무별 재시도와 stage 전이는 C 컴포넌트 내부에서 `langgraph.graph.StateGraph`로 처리합니다. Langflow는 노드 연결과 UI 실행 흐름을 담당하고, 복잡한 상태 전이와 DB 반영은 Python 코드가 책임지는 구조입니다.

## 주요 사용 패키지

| 패키지 | 사용 위치 |
|---|---|
| `langflow.custom`, `langflow.io`, `langflow.schema` | Custom Component, input/output 정의 |
| `langgraph.graph` | 10C/12C/15C 중심의 stage graph, retry route |
| `oracledb` | Oracle 접속, SQL 실행, CLOB/NUMBER bind 처리 |
| `requests` | OpenAI 호환 LLM/Embedding HTTP 호출 |
| `pymilvus` | RAG/Correct SQL Vector DB 동기화와 검색 |
| `logging` | `smartmigrate.workflow` logger 기반 DB workflow log |
| `json`, `re`, `time`, `datetime`, `uuid` | payload parsing, SQL 정리, 실행 metadata 생성 |

## Input/Output 연결 방식

기본 실행 라인은 아래 순서로 이해하면 됩니다.

```text
User Chat
  -> 01_agent_start/00A Runtime Logging Start
  -> 01_agent_start/01 Request Classifier Prompt
  -> 01_agent_start/02 Intent Router
  -> 01_agent_start/03 또는 02_flow_management/04 또는 03_job_execution/06/08
```

작업 실행 요청이면 `03_job_execution/06_getRemainingJobs.py`가 실행 가능 여부를 조회하고, `03_job_execution/08_jobExecutionRouter.py`가 실행 도메인을 선택합니다.

```text
08 Router
  -> 10A/12A/15A/17A/18A Jobs To Loop Table
  -> 10B/12B/15B/17B/18B Loop
  -> 10C/12C/15C/17C One Job Executor
  -> 10D/12D/15D/17D/18D Dashboard
```

단일 도메인 실행에서는 A-B-C-D 묶음이 반복됩니다. 전체 Workflow에서는 `18A -> 18B`가 route별 작업을 묶고, 각 item을 10C/12C/15C/17C로 전달한 뒤 `18D`가 통합 dashboard payload를 만듭니다.

## 공통 Logging

workflow log는 전용 logger 하나만 사용합니다.

```python
logging.getLogger("smartmigrate.workflow")
```

`01_agent_start/00A_logRuntimeStart.py`가 workflow 시작 시 `SmartMigrateDBHandler`를 등록합니다. 이후 각 컴포넌트는 `logger.info()` 또는 `logger.error()`에 `extra={"workflow_log": [...]}`만 넘기면 handler가 `NEXT_MIG_LOG`에 insert합니다.

`workflow_log` 기본 순서는 아래와 같습니다.

```text
map_id, mig_kind, log_type, log_level, step_name, status, retry_count, generate_sql
```

마지막 `generate_sql`은 선택값입니다. 일반 workflow 이동 로그는 7개 값만 넘기고, prompt/생성 SQL/검증 SQL처럼 본문 추적이 필요한 경우에만 8번째 값을 사용합니다.

## 작동 방법

1. Langflow IDE에 필요한 Custom Component `.py` 파일을 등록합니다.
2. 각 컴포넌트의 `inputs`에 Oracle 접속 정보, system schema, LLM endpoint/model/api key, Milvus 설정을 연결합니다.
3. 사용자 입력은 00A와 01을 지나 02에서 `GENERAL_CHAT`, `MANAGEMENT`, `JOB_EXECUTION`으로 나뉩니다.
4. 관리 요청은 `02_flow_management`의 04 계열 컴포넌트가 처리합니다.
5. Vector DB 동기화 요청은 `VECTOR_DB_SYNC` route로 `04_saveVectorDB.py`에 연결합니다.
6. 실행 요청은 `03_job_execution`의 06/08을 거쳐 10/12/15/17/18 계열 실행 flow로 들어갑니다.
7. C 컴포넌트가 DB row 한 건을 다시 조회하고, LLM/RAG/SQL 실행/검증/retry/status update를 수행합니다.
8. D 컴포넌트와 11 계열 컴포넌트가 반복 결과와 최종 결과를 사용자 메시지로 만듭니다.
9. 모든 주요 이동과 실행 결과는 `NEXT_MIG_LOG`에서 확인합니다.

## DB와 Vector DB 역할

| 저장소 | 역할 |
|---|---|
| `NEXT_MIG_INFO` | DB Migration 대상 row, MIG SQL, VERIFY SQL, migration status |
| `NEXT_SQL_INFO` | SQL Conversion/Tuning/Formatting 대상 row와 생성 SQL/status |
| `NEXT_MIG_INFO_DTL` | migration column mapping detail |
| `NEXT_MIG_RAG_INFO` | RAG guide와 correct SQL 원천 데이터 |
| Milvus `SM_RAG_RULES` | SQL Conversion/Tuning RAG 검색 |
| Milvus `SM_CORRECT_SQL_CONVERSION` | 보정된 SQL Conversion 예시 검색 |
| Milvus `SM_CORRECT_SQL_MIGRATION` | 보정된 Migration SQL 예시 검색 |

`02_flow_management/04_saveVectorDB.py`는 Oracle의 RAG/Correct SQL 원천 데이터를 읽어 Milvus collection에 동기화하는 관리 컴포넌트입니다. 런타임 C 컴포넌트들은 Oracle 전체 row를 매번 임베딩하지 않고, 이미 동기화된 Milvus collection을 검색해 prompt hint로 사용합니다.

## 최소 검증

코드 수정 후에는 최소한 compile check를 권장합니다.

```powershell
cd C:\Users\11824\Desktop\SmartMigrate_Refactoring
python -m compileall smartMigrate_2.0_Langflow\01_agent_start smartMigrate_2.0_Langflow\02_flow_management smartMigrate_2.0_Langflow\03_job_execution
```

특정 핵심 실행기만 확인하려면 아래처럼 돌릴 수 있습니다.

```powershell
python -m py_compile `
  smartMigrate_2.0_Langflow\03_job_execution\10C_migOneJobPocExecutor.py `
  smartMigrate_2.0_Langflow\03_job_execution\12C_sqlConversionOneJobPocExecutor.py `
  smartMigrate_2.0_Langflow\03_job_execution\15C_sqlTuningOneJobPocExecutor.py `
  smartMigrate_2.0_Langflow\03_job_execution\17C_sqlFormattingOneJobPocExecutor.py `
  smartMigrate_2.0_Langflow\02_flow_management\04_saveVectorDB.py
```

## 관련 문서

상세 설계는 README에 반복하지 않고 아래 문서에서 관리합니다.

| 문서 | 내용 |
|---|---|
| `00_guide/00_architecture.md` | 전체 아키텍처 진입점과 상세 문서 목차 |
| `00_guide/00_architecture_chapter1_overview.md` | 시스템 목적, 저장소, 기본 흐름 |
| `00_guide/00_architecture_chapter2_chat_management.md` | 채팅/관리 요청 처리 |
| `00_guide/00_architecture_chapter3_job_execution.md` | 작업 실행 라우팅과 Loop 구조 |
| `00_guide/00_architecture_chapter4_domain_executors.md` | 10C/12C/15C/17C 실행기 상세 |
| `00_guide/00_architecture_chapter5_logging_operations.md` | logging, 운영, 장애 확인 |
| `00_guide/00_job_execution_payload_plan.md` | 실행 요청 payload 전달 규칙 |
| `00_guide/00_main_logic_components.md` | C 컴포넌트 중심 읽기 가이드 |
| `00_guide/00_logging_rules.txt` | workflow logging 세부 규칙 |
