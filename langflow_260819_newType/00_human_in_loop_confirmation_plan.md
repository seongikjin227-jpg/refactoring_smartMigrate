# Human In The Loop Confirmation Plan

SmartMigrate newType의 현재 승인 흐름은 Langflow Human Input checkpoint를 기준으로 한다.

## 목표 UX

사용자가 다음처럼 실행을 요청한다.

```text
전체 작업 실행해줘
```

시스템은 바로 실행하지 않고 계획을 보여준다.

```text
요청하신 작업 계획입니다.

작업 유형: Full Workflow
실행 모드: 전체 잔여 작업
실행 예정 건수: 21

| 기능 | 실행 예정 |
|---|---:|
| DB Migration | 2 |
| SQL Conversion | 10 |
| SQL Tuning | 4 |
| SQL Formatting | 5 |

진행 여부를 선택해주세요.
```

사용자가 `Approve`를 누르면 실행을 시작한다. `Reject`를 누르면 취소 메시지만 출력한다. Timeout으로 `Fallback`이 선택되면 승인으로 간주한다.

## 공식 Human Input 기준

Langflow Human Input은 다음 방식으로 동작한다.

- flow를 일시 정지한다.
- 사용자의 선택지를 action branch로 보여준다.
- 선택된 branch만 이어서 실행한다.
- timeout/fallback branch를 설정할 수 있다.

참고:

- https://docs.langflow.org/next/human-input
- https://github.com/langflow-ai/langflow/blob/main/src/lfx/src/lfx/components/flow_controls/human_input.py

## 현재 구현 방향

기본 Human Input 컴포넌트가 현재 설치 버전에서 보이지 않거나 payload 전달 제약이 있으므로, `20_humanInput.py` 커스텀 컴포넌트를 사용한다.

```text
08 Job Execution Router
  -> 08H Confirmation Prompt Builder
       -> 20 Human Input.prompt_message

08 Job Execution Router
  -> 20 Human Input.payload_json

20 Human Input
  Approve  -> 18A Full Workflow Jobs To Loop Table
  Fallback -> 18A Full Workflow Jobs To Loop Table
  Reject   -> 08R Confirmation Rejected -> Chat Output
```

## Payload 전달 방식

사용자 화면에는 payload를 출력하지 않는다.

금지:

- HTML comment 안에 payload 숨기기
- base64 marker 출력
- 파일 저장
- 외부 state 저장
- `08 -> 18A` 직접 연결

허용:

- `08 payload -> 20.payload_json` 직접 `Data` 연결
- `20 Approve/Fallback -> 18A` 연결
- `20 Reject -> 08R` 연결

즉, payload는 Langflow edge의 `Data`로만 이동하고, Human Input이 승인된 branch를 선택하기 전에는 실행 시작 노드로 전달되지 않는다.

## Component 역할

`08H`

- route/count/job list를 읽어서 한국어 승인 메시지를 만든다.
- payload를 메시지에 넣지 않는다.

`20 Human Input`

- `ActionPickerInput`: `Approve`, `Reject` branch 생성
- `BoolInput`: fallback 사용 여부
- `DurationInput`: timeout 설정
- `prompt_message`: 08H가 만든 동적 승인 메시지
- `payload_json`: 승인 후 전달할 실행 payload
- 선택되지 않은 branch는 `self.stop(...)`으로 중단

`08R`

- reject branch에서만 실행된다.
- 취소 메시지만 출력한다.

## Chat Memory

현재 구현은 채팅 기억 기반의 "다음 메시지에서 진행/취소 판단"이 아니라 Human Input 버튼 기반 승인이다.

채팅 기억 기반 승인까지 확장하려면 별도 설계가 필요하다.

- Message History를 `08` 앞에 연결한다.
- pending confirmation을 DB에 저장한다.
- 다음 사용자 메시지가 승인/거부인지 LLM 또는 deterministic parser로 판단한다.
- 저장된 pending payload를 복원해 실행한다.

하지만 현재 요구사항에서는 파일/state 저장을 하지 않기로 했으므로 이 방식은 사용하지 않는다.
