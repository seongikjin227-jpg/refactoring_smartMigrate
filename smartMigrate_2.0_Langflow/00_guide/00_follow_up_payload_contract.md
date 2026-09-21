# Follow-up Request Payload Contract

## 목적

`네`, `진행해`, `그것`, `방금 것`처럼 현재 문장만으로는 대상이 없는 후속 발화를 01 Agent가 한 번만 해석한다. 이후 02, 04, 06, 08과 업무 실행 컴포넌트는 채팅 기록을 다시 조회하거나 추측하지 않고, 이 문서의 구조화 payload만 사용한다.

```text
Chat Input + same session_id
  -> 01 Request Classifier Agent (chat history enabled)
  -> resolved payload
  -> 02 Intent Router
  -> 03 / 04 / 06 / 08
```

## 01 출력 계약

| 필드 | 01의 책임 | 후속 사용처 |
|---|---|---|
| `user_request` | 이번 turn의 원문. 예: `네` | 감사 로그, UI 표시 |
| `resolved_user_request` | 대화 문맥까지 합친 완전한 요청문 | 03, 04, 06, 08, Management Agent 입력 |
| `is_follow_up` | 후속 발화 여부 | 운영 추적, router LLM 보조 정보 |
| `confirmation` | `NOT_REQUIRED`, `PENDING`, `CONFIRMED`, `REJECTED`, `UNKNOWN` | 실행 guard, 운영 추적 |
| `clarification_required` | 이전 대상을 하나로 확정할 수 없는지 | 02의 실행 차단 |
| `clarification_message` | 재질문 문구 | 03 또는 Chat Output |
| `should_execute` | JOB_EXECUTION을 실제 06/08로 보낼 수 있는지 | 06/08의 실행 guard |
| `route` | `GENERAL_CHAT`, `MANAGEMENT`, `JOB_EXECUTION` | 02 branch 선택 |
| `execution_scope`, `requested_domain`, `target_filter` | 완전한 요청에서 추출한 업무 파라미터 | 06, 08, A 컴포넌트 |

직접 실행 요청은 `confirmation=NOT_REQUIRED`, `should_execute=true`으로 유지한다. 확인을 거친 `네`는 `confirmation=CONFIRMED`, `should_execute=true`이다. 불명확한 후속 발화 또는 거절은 `should_execute=false`이다.

## 후속 컴포넌트 규칙

| 컴포넌트 | 입력으로 사용해야 할 요청문 | chat history 필요 여부 |
|---|---|---|
| 03 General Chat Agent | `resolved_user_request`, 없으면 `user_request` | Agent 기본 memory 사용 가능 |
| 04 Management Router | 내부에서 `resolved_user_request` 우선 처리 | 불필요 |
| 04 Management Agent | `effective_user_request` | 불필요 |
| 06 Get Remaining Jobs | `target_filter` 우선, fallback은 `resolved_user_request` | 불필요 |
| 08 Job Execution Router | `resolved_user_request`, `target_filter`, domain/scope | 불필요 |

`history`는 workflow 처리 이력용 배열이며 Langflow chat history가 아니다. 채팅 원문을 이 배열에 저장하거나 LLM 입력으로 넘기지 않는다.

## Langflow 연결 점검

1. `Chat Input`, 01 Agent, 최종 `Chat Output`이 같은 `session_id`를 사용한다.
2. 01은 반드시 공식 Agent 컴포넌트로 구성하고 **Number of Chat History Messages**를 0보다 크게 설정한다.
3. 01의 JSON output 전체를 02의 `payload_json`에 연결한다.
4. 02의 General Chat branch에서 03의 사용자 입력은 `resolved_user_request`를 우선 연결한다. Data 템플릿을 쓴다면 `${resolved_user_request}`가 비어 있을 때만 `${user_request}`로 fallback한다.
5. 04 Management Agent의 사용자 입력도 04 Router가 출력한 `effective_user_request`에 연결한다. 원문의 `user_request`를 직접 연결하지 않는다.
6. JOB_EXECUTION branch는 02 -> 06 -> 08 순서로 `Data` payload 전체를 연결한다. 중간에서 `target_filter`, `confirmation`, `should_execute`를 제거하지 않는다.
7. `clarification_required=true` 또는 `confirmation=REJECTED`이면 02가 General Chat branch로 보내므로 06/08 또는 executor에 연결되면 안 된다.

## 최소 시나리오

| 직전 대화 | 현재 입력 | 01 기대 결과 |
|---|---|---|
| `map_id=101 SQL Conversion 실행할까요?` | `네` | `JOB_EXECUTION`, `CONFIRMED`, `target_filter.map_ids=[101]`, `SQL_CONVERSION` |
| `map_id=101 실패 원인을 조회할까요?` | `진행해` | `MANAGEMENT`, 완전한 조회 요청 |
| 실행 후보가 둘 이상인 질문 | `네` | `clarification_required=true`, `should_execute=false` |
| `map_id=101 실행할까요?` | `아니` | `REJECTED`, `should_execute=false` |
| `map_id=101 실행해줘` | 직접 요청 | `NOT_REQUIRED`, `should_execute=true` |
