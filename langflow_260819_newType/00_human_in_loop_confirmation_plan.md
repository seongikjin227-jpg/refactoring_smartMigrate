# Human In The Loop Confirmation Plan

이 문서는 SmartMigrate newType flow에서 사용자의 재확인 후 전체 작업을 실행하는 방법과, 이전 채팅 내용을 08 라우터가 기억하게 만드는 방법을 정리한다.

## 조사 요약

Langflow 공식 문서 기준으로 HITL과 채팅 기억은 서로 다른 기능이다.

- Human-in-the-Loop는 flow를 일시정지하고 checkpoint를 만든 뒤 사람의 결정을 기다린다. 승인/거절 후 선택된 branch에서 flow가 재개되고, 이미 끝난 step은 다시 실행하지 않는다.
- Human Input 컴포넌트는 Flow Controls에 있으며, 사용자가 선택할 User Action마다 output branch를 만든다. 예를 들어 `Approve`, `Reject`를 만들면 각각의 branch로 이어진다.
- Human Input은 timeout/fallback output도 구성할 수 있다.
- Agent tool approval은 agent가 특정 tool을 호출하려 할 때만 승인을 요구하는 방식이다. 이 방식은 branch node를 직접 추가하지 않는다.
- Message History 컴포넌트는 Langflow storage 또는 Redis/Mem0 같은 외부 memory에서 chat message를 저장/조회한다.
- Agent 컴포넌트는 built-in chat memory가 기본 활성화되어 있다.
- Chat Input/Chat Output이 있는 flow는 messages table에 chat log를 저장하지만, 이것만으로 LLM이 자동으로 기억하는 것은 아니다. LLM 또는 custom router의 입력에 Message History를 연결해야 memory로 작동한다.
- Chat memory는 `session_id` 기준으로 묶인다. 여러 사용자가 같은 flow를 쓰면 custom session id를 명시해야 한다.

참고:

- https://docs.langflow.org/next/human-in-the-loop
- https://docs.langflow.org/next/human-input
- https://docs.langflow.org/message-history
- https://docs.langflow.org/memory

## 현재 문제

현재 08 Job Execution Router는 사실상 stateless router다. 08의 LLM 호출에는 현재 user request, remaining summary, remaining job identifiers만 들어간다.

즉 Langflow Playground나 Chat Output이 메시지를 저장하더라도, 그 기록이 08 LLM payload에 연결되지 않으면 08은 이전 턴의 확인 요청을 알 수 없다.

일반 ChatGPT가 이전 내용을 기억하는 것처럼 보이는 이유는 모델 호출 시 이전 대화 메시지가 함께 전달되기 때문이다. Langflow custom component도 동일하다. 저장된 메시지가 있어도 component 입력에 넣지 않으면 기억하지 못한다.

## 목표 UX

사용자:

```text
전체 작업 실행해줘
```

시스템:

```text
요청하신 작업은 다음과 같습니다.

DB Migration: 2건
SQL Conversion: 10건
SQL Tuning: 4건
SQL Formatting: 5건

실행하시겠습니까?
[예] [아니오]
```

사용자:

```text
예
```

시스템:

```text
전체 작업을 실행합니다.
```

그 뒤 `18A -> 18B -> 10C -> 12C -> 15C -> 17C -> 18D`로 진행한다.

## 권장안: Human Input 기반 HITL

Langflow가 Human Input을 지원하는 버전이면 이 방식이 가장 정석이다.

```text
06 Get Remaining Jobs
  -> 08 Job Execution Router
     -> 09 Execution Plan Summary -> Chat Output
     -> 08 Confirmation Payload
        -> Human Input
           Approve -> 18A Full Workflow Jobs To Loop Table
           Reject  -> Confirmation Cancelled Message -> Chat Output
           Fallback -> Confirmation Timeout Message -> Chat Output
```

역할 분리:

- 08: 실행 route와 confirmation 필요 여부 판단
- 09: 실행 계획/통계만 출력
- Human Input: 예/아니오 선택 UI와 checkpoint/resume 담당
- 18A 이후: 실제 작업 실행

09에는 "진행하려면 ..." 같은 재확인 문구를 넣지 않는다. 09는 다른 작업 시작 전에도 공통으로 쓰는 계획표 컴포넌트이기 때문이다.

Human Input의 Form Content에는 08 또는 별도 08H 컴포넌트가 만든 confirmation message를 넣는다.

User Actions:

- `Approve`: 전체 작업 실행
- `Reject`: 실행 취소

Fallback:

- timeout이 지나면 실행하지 않고 취소/만료 메시지 출력

## 대안: Message History + Confirmation State

Human Input 컴포넌트를 현재 Langflow 버전이나 API 실행 방식에서 쓰기 어렵다면, 일반 채팅 기반 확인 flow를 구현한다.

```text
Chat Input
  -> Message History Retrieve
  -> 01/02/06
  -> 08 Job Execution Router
     -> confirmation_request -> Chat Output
     -> confirmed full_workflow_job -> 18A
     -> cancel/no_runnable/prerequisite -> Chat Output
  -> Message History Store
```

이 방식에서는 "예/아니오"가 별도 UI button이 아니라 다음 사용자 채팅으로 들어온다.

08 LLM 입력에 반드시 포함해야 하는 값:

```json
{
  "user_request": "예",
  "chat_memory": "최근 대화 내용...",
  "pending_confirmation": {
    "pending": true,
    "action_type": "FULL_WORKFLOW",
    "created_at": "...",
    "expires_at": "...",
    "plan_counts": {
      "MIG": 2,
      "SQL_CONVERSION": 10,
      "SQL_TUNING": 4,
      "SQL_FORMATTING": 5
    }
  },
  "remaining_summary": {},
  "remaining_job_identifiers": {}
}
```

Message History만으로는 부족하다. "예"라는 답변이 어떤 작업에 대한 예인지 정확히 알기 위해 pending confirmation state가 필요하다.

## Pending Confirmation 저장 위치

추천은 DB 저장이다.

후보 테이블:

```sql
NEXT_CONFIRMATION_STATE
```

컬럼 예시:

```text
CONFIRM_ID
SESSION_ID
ACTION_TYPE
ACTION_PAYLOAD_JSON
PLAN_COUNTS_JSON
STATUS
CREATED_AT
EXPIRES_AT
CONFIRMED_AT
CANCELLED_AT
USER_REPLY
```

상태값:

- `PENDING`
- `CONFIRMED`
- `CANCELLED`
- `EXPIRED`

DB state가 필요한 이유:

- Chat Output이 payload를 끊어도 다음 턴에서 복구 가능
- 08이 stateless여도 pending 작업을 조회 가능
- session별로 동작을 분리 가능
- "예" 같은 짧은 답변도 직전 pending action과 안전하게 연결 가능

## 08 Router 판단 규칙

08은 LLM을 호출하기 전에 pending confirmation을 먼저 조회한다.

1. pending confirmation이 없고 사용자가 실행 요청을 함
   - `FULL_WORKFLOW`
   - `confirmation_required=true`
   - 실제 실행 branch는 stop
   - confirmation request로 출력
   - pending state 저장

2. pending confirmation이 있고 사용자가 승인 답변을 함
   - `confirmed=true`
   - 저장된 `ACTION_PAYLOAD_JSON`을 복원
   - `FULL_WORKFLOW -> 18A`로 실행

3. pending confirmation이 있고 사용자가 거절 답변을 함
   - pending state를 `CANCELLED`로 변경
   - 취소 메시지 출력

4. pending confirmation이 있지만 사용자가 새 작업을 요청함
   - 기존 pending state를 취소하거나 유지할지 정책 필요
   - 추천: 새 명확한 작업 요청이면 기존 pending을 취소하고 새 요청으로 라우팅

승인 답변 예:

- `예`
- `응`
- `진행`
- `진행해`
- `확인`
- `그대로 실행`
- `전체 작업 진행`

거절 답변 예:

- `아니오`
- `취소`
- `하지마`
- `중단`
- `다시 선택`

## 09의 역할

09는 다음만 담당한다.

- 전체 몇 건인지 표시
- 기능별 몇 건인지 표시
- 선택된 route/run mode 표시
- 실행 예정 대상 요약 표시

09가 하지 말아야 할 것:

- 재확인 문구 생성
- 예/아니오 안내
- confirmation 상태 저장
- confirmation 승인 판단

## 권장 구현 순서

1. 현재 Langflow 버전에서 Human Input 컴포넌트 사용 가능 여부 확인
2. 가능하면 `08 -> Human Input -> 18A` 구조로 구현
3. 불가능하거나 API/Chat Playground에서 제약이 있으면 `NEXT_CONFIRMATION_STATE` 기반 chat confirmation 구현
4. Message History Retrieve를 08 앞에 연결하고, 최근 N개 메시지를 08 LLM 입력에 포함
5. 08에 `confirmation_request`, `confirmation_cancelled`, 기존 실행 outputs를 분리
6. 09는 계획표 전용으로 유지

## 최종 추천

운영 안정성을 기준으로는 두 계층을 같이 쓰는 것이 가장 좋다.

1. UI/Playground에서는 Human Input을 사용해서 예/아니오 button 기반 HITL을 구현한다.
2. 동시에 `NEXT_CONFIRMATION_STATE`를 저장해서 flow 재시작, Chat Output payload 단절, 짧은 승인 답변 문제를 방어한다.
3. 08에는 Message History Retrieve 결과를 연결해서 사용자의 짧은 답변과 이전 assistant 확인 요청을 LLM이 함께 볼 수 있게 한다.

이렇게 하면 Langflow의 HITL checkpoint UX와 SmartMigrate의 DB 기반 실행 안정성을 동시에 가져갈 수 있다.
