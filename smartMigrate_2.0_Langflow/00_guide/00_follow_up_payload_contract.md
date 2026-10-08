# 현재 요청과 후속 발화 payload 계약

기준일: 2026-10-08. 이전 01 history classifier는 현재 패키지에 없습니다. 02는 Message를 받아 LLM으로 route와 의도·도메인·범위·대상을 함께 해석하고 원본 metadata를 코드로 보존합니다.

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
| `request_action` | STATUS_QUERY / EXECUTE / OTHER. 상태 조회와 실행을 구분 |
| `clarification_required`, `clarification_message` | LLM 해석 또는 코드 검증에서 확인이 필요하면 true와 질문 |
| `should_execute` | JOB_EXECUTION + EXECUTE + clarification 불필요일 때만 true |
| `execution_scope`, `requested_domain` | 02 LLM이 해석. 실행 요청의 unknown은 확인 필요 |
| `target_filter` | 02 LLM이 map_ids/sql_seqs/sql_ids/space_nms를 채움. 전체/도메인 실행은 빈 배열 |
| `source_message`, `message_data`, `files` | 원본 metadata |
| `uploaded_attachment` | 00A가 파싱한 XLSX 결과 또는 오류 |

`history`는 workflow 처리 이력이며 chat memory가 아닙니다. 같은 session_id를 사용해도 02가 이전 대상이나 확인 응답을 자동 복원하지 않습니다.

## 연결 및 사용자 안내

1. 00A의 Message 출력을 02의 `input_message`에 연결합니다. text 문자열로 축소하거나 이전 01 JSON을 `payload_json`에 연결하지 않습니다.
2. 02의 Data 출력 전체를 04/06에 전달합니다.
3. 04 Agent는 `effective_user_request`를 현재 요청으로 사용합니다. 04 매핑 생성기는 Router의 Mapping Rule Update Message를 `user_request`로 받고 원본 user_request만 사용합니다. 매핑 내용 전체가 요청문에 있어야 합니다.
4. 06 → 08 → A는 Data 전체를 전달하며 식별자와 실행 guard를 보존합니다.
5. “네”, “그 후보들”만으로 이전 작업 대상을 추측하지 않습니다. `마이그레이션 59번 다시 실행해줘` 또는 `SQL_ID S001, SPACE_NM PAYMENT 변환해줘`처럼 대상을 명시합니다. 도메인/대상 없는 일반 “작업 실행”은 전체 워크플로우 요청으로 해석합니다.

02는 현재 요청의 clarification 필요 여부를 생성하지만 이전 발화 대상을 복원하거나 CONFIRMED/REJECTED를 생성하지 않습니다. 대화 기반 후속 확인을 도입하려면 history resolver와 확인 상태 전달을 별도로 구현해야 합니다.
