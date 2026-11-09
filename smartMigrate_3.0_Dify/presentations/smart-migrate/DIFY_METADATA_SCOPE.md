# LOG/RAG JSON과 시연 입력

핵심 입력은 NEXT_MIG_INFO(테이블 매핑), NEXT_MIG_INFO_DTL(컬럼 대응), NEXT_SQL_INFO(SQL 작업)다. HTML에는 샘플 2·10·1행과 전체 컬럼·JSON·다운로드를 제공한다. SQL은 직원·부서 조인과 두 바인드·(+)·PK IN 구조를 한 개로 시연한다. REF_SEQ는 다른 SQL_SEQ 참조이며 MAP_ID가 아니다.

## NEXT_MIG_LOG
engine.log()가 작업·상태·메시지를 기록하고 최근 30행을 유지한다. 초기 rows는 빈 배열이다. 수동 작성할 매핑 자료보다 실행 후 확인할 보조 업무 이력에 가깝다. Dify Trace는 노드 실행·오류 경로이고 이 테이블은 대화 상태의 업무 요약이다. 매핑/SQL 작업의 LOG·산출물·검증 결과도 함께 확인한다.

## NEXT_MIG_RAG_INFO
현재 변환·튜닝 정책의 원본이다. 변환 1개·튜닝 3개를 저장하며 활성 행을 reference_rules로 planner에 전달한다. WHERE_ONE·ANSI_JOIN·REDUCE_SUBQUERY의 설명과 전후 SQL을 조회한 뒤 변환·튜닝을 시연한다. USE_YN·SOURCE_TABLES에 따라 적용하며 활성 튜닝 규칙이 없으면 TO_SQL을 유지한다. Code의 WHERE 보정과 잔존 (+) 검사도 활성 규칙을 따른다. 임베딩·벡터 검색은 없다.

## DSL에서 완전히 제거하려면
현재는 두 테이블 모두 사용한다. 규칙을 지우는 대신 USE_YN으로 관리하며 규칙 변경 시 기존 튜닝을 STALE로 표시한다. validate가 요구하는 테이블 구조는 유지한다. 최신 Dify 동작·적용은 smartMigrate_3.0_Dify/docs/RAG_TUNING_DEMO.md가 기준이다.

근거: smartMigrate_3.0_Dify/nodes/engine.py의 validate()/log()/sql_stage(), nodes/prepare.py, prompts/planner.md, samples/tables 원본.
