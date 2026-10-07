# 현재 요청과 후속 발화 payload 계약

기준일: 2026-10-07. 이전 01 history classifier는 현재 패키지에 없습니다. 현재 02는 Message를 받아 LLM으로 route만 분류하고 원본 metadata를 코드로 보존합니다.

```text
Chat Input → 00A Message → 02 input_message → 03 / 04 / 06 → 08
```

## 02가 만드는 값

| 필드 | 현재 값/의미 |
|---|---|
| `route` | GENERAL_CHAT / MANAGEMENT / JOB_EXECUTION |
| `user_request`, `resolved_user_request` | 현재 Message의 text. 이전 대화 복원 결과가 아님 |
| `is_follow_up` | false |
| `confirmation` | NOT_REQUIRED |
| `clarification_required` | false |
| `should_execute` | route가 JOB_EXECUTION이면 true |
| `execution_scope`, `requested_domain` | unknown / UNKNOWN. 06/08에서 보완 |
| `target_filter` | 빈 map_ids/sql_seqs/sql_ids/space_nms 배열. 06/08에서 현재 요청을 해석 |
| `source_message`, `message_data`, `files` | 원본 metadata |
| `uploaded_attachment` | 00A가 파싱한 XLSX 결과 또는 오류 |

`history`는 workflow 처리 이력이며 chat memory가 아닙니다. 같은 session_id를 사용해도 02가 이전 대상이나 확인 응답을 자동 복원하지 않습니다.

## 연결 및 사용자 안내

1. 00A의 Message 출력을 02의 `input_message`에 연결합니다. text 문자열로 축소하거나 이전 01 JSON을 `payload_json`에 연결하지 않습니다.
2. 02의 Data 출력 전체를 04/06에 전달합니다.
3. 04 Agent는 `effective_user_request`를 현재 요청으로 사용합니다. 04 매핑 생성기는 `router_payload` 전체를 받아 첨부 파싱 결과도 읽습니다.
4. 06 → 08 → A는 Data 전체를 전달하며 식별자와 실행 guard를 보존합니다.
5. “네”, “그 후보들”, “진행해”만으로 작업 대상을 추측하지 않습니다. `MAP_ID 101 Migration 실행해줘` 또는 `SQL_ID S001, SPACE_NM PAYMENT 변환해줘`처럼 완전한 요청을 받습니다.

과거 문서의 CONFIRMED/REJECTED/clarification 계약은 일부 downstream에서 방어적으로 지원하지만, 현재 02가 이를 생성하거나 후속 발화를 판별하는 기능은 없습니다. 해당 기능을 다시 도입하려면 history resolver와 확인 상태 전달을 별도로 구현해야 합니다.
