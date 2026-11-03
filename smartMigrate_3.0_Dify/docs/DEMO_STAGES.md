# Dify 시연 단계

최신 기준은 [단일 SQL·RAG 튜닝·Agent 호출 점검](RAG_TUNING_DEMO.md)입니다.

인사·에이전트 소개 → 매핑룰 조회 → DB Migration 실행 → 변환 대상 SQL 조회 → SQL 변환 실행 → SQL 튜닝룰 조회 → SQL 튜닝 실행 순서로 요청합니다. 기본 SQL은 두 테이블 조인·실제 바인드 자리·서브쿼리를 포함한 한 개입니다.

단계는 migration/conversion/tuning/formatting뿐입니다. 복합 요청은 `{"actions":[작업1, 작업2]}`로 각 작업을 순서대로 수행합니다. 요청에 없는 단계는 추가하지 않습니다. 업무 변경은 한 번의 상태 확정으로 저장하며 중간 실패 시 이번 요청 전체를 취소합니다.

단계 검사는 Agent가 재작성한 문장 대신 sys.query가 담긴 상태 패킷의 원문을 사용합니다. 매핑 조회는 NEXT_MIG_INFO와 NEXT_MIG_INFO_DTL 전체가 기본값입니다.

이전 사용자 로그의 success는 ROUND/Thought의 상태였고 실제 Tool 결과는 비어 있었습니다. ReAct 전환은 현재 모델의 stop 미지원 400으로 되돌렸으며 기본 DSL은 Function Calling·반복 1입니다. [stop 오류 대응](GPT55_STOP_ERROR.md)과 위 가이드의 적용 절차를 따릅니다. EMPTY_AGENT_RESPONSE 진단은 유지합니다.
