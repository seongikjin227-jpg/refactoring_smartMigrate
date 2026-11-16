# 대화 문맥과 resolved_user_request

기존 01 공식 Agent는 동일 `session_id`의 내장 채팅 메모리와 현재 질문을 함께 읽어 `resolved_user_request`를 생성했다. `9833fe8`의 `00_follow_up_payload_contract.md`에는 공식 Agent의 **Number of Chat History Messages**를 0보다 크게 설정하도록 명시되어 있다. `bd933df`(2026-10-06)의 01·02 병합 이후에는 02가 현재 질문만 LLM에 전달하고 이 필드를 원문으로 고정했다.

현재는 별도 노드 없이 02 내부에서 Langflow 내장 메모리를 읽는다.

```text
Chat Input → 00A → 02 (동일 세션의 최근 사용자 질문 조회 + 현재 요청 해석)
                       → 03 / 04 / 06
```

- Chat Input과 Chat Output의 메시지 저장을 활성화하고, 매 대화에 같은 `session_id`를 유지한다. 세션이 바뀌거나 메시지가 저장되지 않으면 이전 대화를 복원할 수 없다.
- 02의 고급 설정 **Recent User Turns**는 기본 10개, 최대 20개다. 0이면 이력 조회를 끈다.
- 02는 `lfx.memory.get_messages`(이전 배포에서는 `langflow.memory.get_messages`)로 같은 세션의 사용자 메시지만 조회한다. graph의 flow_id가 있으면 함께 제한한다. 최신순으로 조회한 뒤 오래된 순서로 LLM에 전달하며 현재 입력이 포함되어 있으면 마지막에서 제외한다.
- 기존 호출자가 `recent_user_requests`, `chat_history`, `conversation_history`를 요청 JSON이나 Message의 data/properties에 명시했다면 이를 우선 사용한다. 배열은 오래된 순서다. 문자열 배열은 사용자 질문 전용이며, 혼합 메시지 배열은 `role=user/human` 또는 `sender=User`를 포함해야 한다. 명시적으로 다른 session/context의 메시지는 제외한다.
- `chat_history_status`: `LOADED`, `EMPTY`, `NO_SESSION`, `UNAVAILABLE`, `SUPPLIED`, `DISABLED`. 메모리 조회 실패는 경고 로그를 남기고 현재 질문만 판단한다.

| 필드 | 의미 |
|---|---|
| user_request | 이번 사용자 원문 |
| resolved_user_request | 현재 행위를 유지하면서 빠진 문맥을 보완한 완전한 요청 |
| recent_user_requests | LLM이 실제로 받은 최근 사용자 질문 |
| is_follow_up | 이전 질문으로 빠진 문맥을 보충했는지 |
| history | workflow 처리 추적. 채팅 이력이 아님 |

예를 들어 이전 질문이 “102번 마이그레이션 하고 싶은데 어떻게 해?”이고 현재 요청이 “102번 진행해줘”라면 `MIG / targeted / map_ids=[102]`와 “마이그레이션 MAP_ID 102번을 실행해줘”로 해석한다. 현재 질문이 “102번 상태 보여줘”라면 실행하지 않고 MANAGEMENT 상태 조회로 해석한다. 현재 요청이 명시한 다른 도메인·대상은 이전 문맥보다 우선한다. 과거 대상들을 합쳐 실행 범위를 넓히지 않는다.

이력이 없거나 같은 번호에 Migration과 SQL 후보가 충돌해 해소할 수 없으면 확인 질문을 한다. 단순 “네”만으로 실행을 승인했다고 해석하지 않는다. 전체 현황·진행 상황·최근 결과 조회에는 단건 식별자를 요구하지 않는다.

04는 전달받은 최근 질문, 현재 원문, 02의 해석과 구조화 도메인을 함께 읽는다. 필요한 문맥을 보완해 `effective_user_request`로 Management Agent에 전달한다. 모호한 특정 대상은 EXCEPTION으로 보내며, 02의 `clarification_required=true`를 우회하지 않는다.

배포할 때 02와 04의 컴포넌트 코드를 갱신하고 03·04 Agent 프롬프트도 함께 갱신한다. 기존 노드 연결은 유지한다. 실제 LLM의 문맥 판단은 배포 환경에서 Migration·SQL Conversion 후속 요청, 현재 도메인 변경, 문맥 충돌, 이력 없는 모호한 요청을 각각 확인한다.

메모리 API 참고: [Langflow 구현](https://github.com/langflow-ai/langflow/blob/main/src/backend/base/langflow/memory.py).
