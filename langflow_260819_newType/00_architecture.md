# Langflow newType POC Architecture

## 핵심 원칙

- `01 Request Classifier LLM`은 `01_requestClassifierPrompt.md`를 사용해 사용자 요청을 `GENERAL_CHAT`, `MANAGEMENT`, `JOB_EXECUTION`으로 1차 분류한다.
- `SQL Conversion 작업 대상 조회`, `SQL Tuning 대상 보여줘`, `Formatting 대기 작업 몇 건이야`, `DB Migration 대상 목록`처럼 읽기성 작업 대상 조회 요청은 `MANAGEMENT`로 보낸다.
- `04 Management LLM Router`는 관리 요청을 `DASHBOARD`, `STATUS_CHANGE`, `CORRECT_SQL_INPUT`, `EXCEPTION`으로 분기한다.
- 작업 대상 조회/현황/건수/실패/대기 작업 조회는 `DASHBOARD`로 분기하고, 대시보드 조회 결과를 활용해 답변한다.
- `JOB_EXECUTION`은 실제 작업 실행 요청이다. 전체 pending 실행과 `map_id`/`sql_id`/`space_nm` 기반 단건 또는 복수건 실행을 모두 포함한다.
- `01 Request Classifier LLM`, `04 Management LLM Router`, `08 Job Target Router`는 LLM 기반 분기다. rule fallback, classifier mode, router mode는 사용하지 않는다.
- LLM 프롬프트는 컴포넌트 코드 내부 상수로 관리하고 Langflow 입력값으로 받지 않는다.
- `06 Get Pending Jobs`는 실행 라우팅에 필요한 최소 상태 컬럼만 조회한다. CLOB/SQL 본문 컬럼은 조회하지 않는다.
- `08 Job Target Router`는 `06 Get Pending Jobs` 결과와 사용자 요청을 함께 보고 실행 도메인, 실행 모드, 대상 필터를 LLM으로 결정한다.
- 실행 전 `09 Execution Plan Summary`가 어떤 파이프라인에서 몇 개의 job을 실행할지 `Message`로 먼저 안내한다.
- 각 pipeline은 현재 POC이므로 실제 DB Migration/SQL 변환/튜닝/포맷팅 로직을 실행하지 않고 테스트용 랜덤 결과와 로그를 반환한다.
- Chat Output에 직접 연결되는 출력은 모두 `Message` 타입이다.

## 전체 흐름

```mermaid
flowchart TD
    A["Chat Input"] --> B["01 Request Classifier LLM"]
    B --> C{"02 Intent Conditional Router"}

    C -->|general_chat| D["03 LLM Response"]
    C -->|management| E{"04 Management LLM Router"}
    C -->|job_execution| G["06 Get Pending Jobs"]

    E -->|dashboard| E1["04 Dashboard"]
    E -->|status_change| E2["04 Status Change"]
    E -->|correct_sql_input| E3["04 Correct SQL Input"]
    E -->|exception message| OUT["Chat Output"]

    G --> H{"08 Job Target Router"}

    H -->|MIG targets| P["09 Execution Plan Summary"]
    H -->|SQL Conversion targets| P
    H -->|SQL Tuning targets| P
    H -->|SQL Formatting targets| P
    H -->|prerequisite_blocked message| OUT
    H -->|no_runnable_target message| OUT

    P -->|notice message| OUT
    P -->|payload / MIG| M["10 MIG Pipeline"]
    P -->|payload / SQL_CONVERSION| C2["12 SQL Conversion Pipeline"]
    P -->|payload / SQL_TUNING| T2["15 SQL Tuning Pipeline"]
    P -->|payload / SQL_FORMATTING| F2["17 SQL Formatting Pipeline"]

    M --> S3["13 Final Summary"]
    C2 --> S3
    T2 --> S3
    F2 --> S3

    D --> OUT
    E1 --> OUT
    E2 --> OUT
    E3 --> OUT
    S3 --> OUT
```

## 포트 연결

| 순서 | From | Output | To | Input |
|---:|---|---|---|---|
| 1 | Chat Input | Message/Text | `01 Request Classifier LLM` | `user message` + `01_requestClassifierPrompt.md` |
| 2 | `01 Request Classifier LLM` | `Message(JSON text)` | `02 Intent Conditional Router` | `payload_json` |
| 3 | `02 Intent Conditional Router` | `General Chat` | `03 LLM Response` | `user_request` + `03_llmResponsePrompt.md` |
| 4 | `02 Intent Conditional Router` | `Management` | `04 Management LLM Router` | `payload_json` |
| 5 | `04 Management LLM Router` | `Dashboard` | `04 Dashboard` | `payload_json` |
| 6 | `04 Management LLM Router` | `Status Change` | `04 Status Change` | `payload_json` |
| 7 | `04 Management LLM Router` | `Correct SQL Input` | `04 Correct SQL Input` | `payload_json` |
| 8 | `04 Management LLM Router` | `Exception Message` | Chat Output | Message |
| 9 | `02 Intent Conditional Router` | `Job Execution` | `06 Get Pending Jobs` | `payload_json` |
| 10 | `06 Get Pending Jobs` | `payload` | `08 Job Target Router` | `payload_json` |
| 11 | `08 Job Target Router` | executable target output | `09 Execution Plan Summary` | `payload_json` |
| 12 | `09 Execution Plan Summary` | `Notice Message` | Chat Output | Message |
| 13 | `09 Execution Plan Summary` | `Payload` | selected Pipeline | `payload_json` |
| 14 | `10 MIG Pipeline` | `payload` | `13 Final Summary` | `payload_json` |
| 15 | `12 SQL Conversion Pipeline` | `payload` | `13 Final Summary` | `payload_json` |
| 16 | `15 SQL Tuning Pipeline` | `payload` | `13 Final Summary` | `payload_json` |
| 17 | `17 SQL Formatting Pipeline` | `payload` | `13 Final Summary` | `payload_json` |
| 18 | `08 Job Target Router` | `Prerequisite Blocked Message` | Chat Output | Message |
| 19 | `08 Job Target Router` | `No Runnable Target Message` | Chat Output | Message |
| 20 | `13 Final Summary` | `Result Message` | Chat Output | Message |

## 요청별 분기 기준

| 사용자 요청 예시 | 1차 route | 2차 route / 실행 모드 | 연결 |
|---|---|---|---|
| `안녕`, `이 구조 설명해줘` | `GENERAL_CHAT` | - | `03 LLM Response` |
| `SQL Conversion 작업 대상 조회해줘` | `MANAGEMENT` | `DASHBOARD` | `04 Dashboard` |
| `DB Migration 대상 목록 보여줘` | `MANAGEMENT` | `DASHBOARD` | `04 Dashboard` |
| `map_id=101 priority 올려줘` | `MANAGEMENT` | `STATUS_CHANGE` | `04 Status Change` |
| `sql_id=Q001 correct sql 저장해줘` | `MANAGEMENT` | `CORRECT_SQL_INPUT` | `04 Correct SQL Input` |
| `DB Migration 전체 진행해줘` | `JOB_EXECUTION` | `all_pending / MIG` | `06 -> 08 -> 09 -> 10 -> 13` |
| `map_id=101 실행해줘` | `JOB_EXECUTION` | `targeted / MIG` | `06 -> 08 -> 09 -> 10 -> 13` |
| `sql_id=Q001 변환해줘` | `JOB_EXECUTION` | `targeted / SQL_CONVERSION` | `06 -> 08 -> 09 -> 12 -> 13` |
| `space_nm=SALES 튜닝 진행해줘` | `JOB_EXECUTION` | `targeted / SQL_TUNING` | `06 -> 08 -> 09 -> 15 -> 13` |
| `sql_id=Q001 포맷팅해줘` | `JOB_EXECUTION` | `targeted / SQL_FORMATTING` | `06 -> 08 -> 09 -> 17 -> 13` |

## Dashboard 응답 계약

`04 Dashboard`는 관리성 조회 branch다. 실제 Dashboard 조회 컴포넌트나 DB 조회 결과가 payload에 포함되면 그 내용을 `Message`로 정리한다.

지원 payload key:

```text
dashboard_data
dashboard_result
query_result
rows
summary
```

POC에서 위 데이터가 없으면 실제 조회 결과가 아직 연결되지 않았다고 명시한다. 실제 플로우에서는 Dashboard 조회 결과를 위 key 중 하나로 전달한 뒤 Chat Output 또는 응답용 LLM에 연결한다.

## 실행 전 안내

`09 Execution Plan Summary`는 실제 pipeline 실행 전에 사용자에게 아래 정보를 `Message`로 안내한다.

```text
실행 파이프라인
실행 모드: all_pending 또는 targeted
실행 예정 job 수
실행 예정 job list
```

`Notice Message`는 Chat Output으로 바로 연결하고, `Payload`는 선택된 pipeline으로 연결한다.

## Chat Output 연결 규칙

Chat Output으로 직접 연결되는 출력은 모두 `Message` 타입이다.

| Component | Output |
|---|---|
| `03 LLM Response` | `LLM Message` |
| `04 Dashboard` | `Result Message` |
| `04 Status Change` | `Result Message` |
| `04 Correct SQL Input` | `Result Message` |
| `04 Management LLM Router` | `Exception Message` |
| `08 Job Target Router` | `Prerequisite Blocked Message` |
| `08 Job Target Router` | `No Runnable Target Message` |
| `09 Execution Plan Summary` | `Notice Message` |
| `13 Final Summary` | `Result Message` |

## 제거된 컴포넌트

- `05_jobExecutionNotice.py`
- `03_generalChatResponder.py`
- `07_prioritySelector.py`
- `09_dbMigrationAgent.py`
- `11_sqlConversionAgent.py`
- `12_nextIncompleteLoop.py`
- `14_sqlTuningAgent.py`
- `16_sqlFormattingAgent.py`
