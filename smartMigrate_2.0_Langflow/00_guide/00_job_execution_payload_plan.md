# Job Execution Payload 전달 규칙

기준일: 2026-10-07. 실행 흐름은 `00A → 02 → 06 → 08 → A/B/C/D`입니다.

## 책임

- 02: 현재 Message의 text로 JOB_EXECUTION 여부만 분류합니다. domain/scope/targets는 초기값입니다.
- 06: `resolved_user_request` 또는 `user_request`에서 식별자를 보완하고 DB 기준 잔여 카운트, 요청 대상 상태와 실행 가능한 requested_jobs를 조회합니다.
- 08: LLM과 구조화 payload로 도메인 및 run mode를 결정합니다. 02가 domain을 확정했다고 가정하지 않습니다.
- A: 전체/도메인 실행에서는 실제 목록을 DB에서 읽습니다. targeted 실행에서는 selected_jobs를 Loop 입력으로 만듭니다.
- SQL target은 SQL_SEQ 또는 SQL_ID + SPACE_NM을 사용합니다. SQL_ID만으로 대상을 확정하지 않습니다.
- NEXT_SQL_INFO에 USE_YN 필터를 적용하지 않습니다. Migration만 NEXT_MIG_INFO.USE_YN을 사용합니다.

## 02 출력 초기값

```json
{"route":"JOB_EXECUTION","user_request":"MAP_ID 101 Migration 실행해줘","resolved_user_request":"MAP_ID 101 Migration 실행해줘","is_follow_up":false,"confirmation":"NOT_REQUIRED","clarification_required":false,"should_execute":true,"requested_domain":"UNKNOWN","execution_scope":"unknown","target_filter":{"map_ids":[],"sql_seqs":[],"sql_ids":[],"space_nms":[]}}
```

06은 위 현재 문장에서 MAP_ID 101을 읽고 runnable 상태를 조회합니다. 08이 MIG/targeted로 확정하면 10A로 보냅니다. `전체 작업 실행해줘`는 08의 FULL_WORKFLOW/all_pending 결정 이후 18A → 18B Loop2로 갑니다. 단일 도메인 요청은 선행 조건을 확인하고, 전체 workflow는 MIG부터 단계별로 처리합니다.

## 보존해야 할 값

`user_request`, `resolved_user_request`, `should_execute`, `confirmation`, `target_filter`, `job_availability`, `requested_jobs`, `requested_target_status`를 중간 연결에서 제거하지 않습니다. `history`는 workflow 추적 기록이고 채팅 기록이 아닙니다. 02는 후속 발화의 대상을 복원하지 않으므로 식별자가 있는 완전한 요청을 사용합니다.

상세 연결은 `00_follow_up_payload_contract.md`, Loop 구성은 `00_full_workflow_loop_guide.md`, 실제 상태 조건은 chapter3 및 chapter4를 확인합니다.
