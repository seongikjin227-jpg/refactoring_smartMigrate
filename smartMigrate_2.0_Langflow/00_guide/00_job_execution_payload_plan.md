# Job Execution Payload 전달 규칙

기준일: 2026-10-08. 실행 흐름은 `00A → 02 → 06 → 08 → A/B/C/D`입니다.

## 책임

- 02: LLM이 현재 Message의 route, request_action, requested_domain, execution_scope, target_filter를 함께 해석합니다. 상태 조회는 MANAGEMENT로 보냅니다. 실행 범위나 대상이 불명확하면 clarification_required=true, should_execute=false로 전달합니다.
- 06: 02가 전달한 식별자를 검증하고 DB 기준 잔여 카운트, 요청 대상 상태와 실행 가능한 requested_jobs를 조회합니다. 원문 regex 추출은 없습니다.
- 08: 02의 해석과 06의 DB 결과로 deterministic routing합니다. LLM 재호출이나 target merge는 없습니다. 이전 graph 호환용 llm 입력은 선택값이며 사용하지 않습니다.
- A: 전체/도메인 실행에서는 실제 목록을 DB에서 읽습니다. targeted 실행에서는 selected_jobs를 Loop 입력으로 만듭니다.
- SQL target은 SQL_SEQ 또는 SQL_ID + SPACE_NM을 사용합니다. SQL_ID만으로 대상을 확정하지 않습니다.
- NEXT_SQL_INFO에 USE_YN 필터를 적용하지 않습니다. Migration만 NEXT_MIG_INFO.USE_YN을 사용합니다.

## 02 특정 실행 출력 예시

```json
{"route":"JOB_EXECUTION","user_request":"마이그레이션 59번 다시 실행해줘","resolved_user_request":"마이그레이션 59번 다시 실행해줘","is_follow_up":false,"confirmation":"NOT_REQUIRED","request_action":"EXECUTE","clarification_required":false,"should_execute":true,"requested_domain":"MIG","execution_scope":"targeted","target_filter":{"map_ids":[59],"sql_seqs":[],"sql_ids":[],"space_nms":[]}}
```

06은 target_filter.map_ids=[59]로 runnable 상태를 조회합니다. 08은 실행 가능한 row가 있으면 MIG/targeted로 10A에 보냅니다. `전체 작업 실행해줘`는 02가 FULL_WORKFLOW/all과 빈 target_filter로 해석하며 06은 전체 count만 조회합니다. 08 이후 18A → 18B Loop2에서 실제 목록을 읽습니다. 단일 도메인 요청은 선행 조건을 확인하고, 전체 workflow는 MIG부터 단계별로 처리합니다. 대상 미조회, 조회 실패, 조회 후 실행 불가를 구분합니다.

## 보존해야 할 값

`user_request`, `resolved_user_request`, `should_execute`, `confirmation`, `target_filter`, `job_availability`, `requested_jobs`, `requested_target_status`를 중간 연결에서 제거하지 않습니다. `history`는 workflow 추적 기록이고 채팅 기록이 아닙니다. 02는 후속 발화의 대상을 복원하지 않으므로 식별자가 있는 완전한 요청을 사용합니다.

상세 연결은 `00_follow_up_payload_contract.md`, Loop 구성은 `00_full_workflow_loop_guide.md`, 실제 상태 조건은 chapter3 및 chapter4를 확인합니다.
