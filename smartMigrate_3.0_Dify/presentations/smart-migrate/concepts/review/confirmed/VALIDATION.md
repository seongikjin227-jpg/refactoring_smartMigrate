# 확정 A · 단계별 시연 추가 검증

2026-10-25. 개발자 대상 20~25분, 16개 섹션.

검증 범위 설명 보완: 원본 Migration의 INSERT·COUNT·PK 기준 전체 대상 행 CONCAT 검증과 Conversion의 TO_SQL·실제 원천값 조회 BIND_SQL·BIND_SET 값 대입 TEST_SQL을 확인했다. TEST_SQL 예시의 출력은 원본 실행기 계약인 CASE_NO/FROM_COUNT/TO_COUNT에 맞췄다. Dify 축소 범위와 비교 제외 컬럼·최대 3개 바인드 사례는 별도로 표시했다. 이 수정에서는 실행 코드·DSL을 변경하지 않았다.

Chrome에서 데스크톱 1280×800, 1440×900, 1920×1080을 확인했다. 가로 넘침·제목/코드/버튼 잘림·목차 대상 누락·글꼴 오류·콘솔 오류 없음. Agent 클릭·키보드·전체 details 펼침과 세 HTML 진입을 확인했다. 휴대폰·태블릿 검사는 하지 않았다.

표시한 NEXT_MIG_INFO, NEXT_MIG_INFO_DTL, NEXT_SQL_INFO의 전체 JSON을 DOM에서 읽어 기존 샘플과 비교했다. 다운로드 JSON도 표시 JSON과 같음을 확인했다. columns·primary_key·rows·초기 null·BIND_SET 문자열을 유지한다.

최신 독립 검토 판정 ship. 단일 SQL·두 바인드·두 테이블·(+)/PK IN, 활성 RAG 기반 튜닝, actions 순차 실행이 코드·재생 증거와 일치한다. 이전 부서 필터 SQL은 별도 설명 예제로 구분했고 RAG의 현재 역할도 갱신했다. 기존 A의 디자인 토큰·글꼴·탭·접근성 동작을 유지했다.

capture_demo.py로 내보낸 DSL의 execute·commit Code를 고정 계획·SQL fixture로 로컬 재생해 14개 경로를 확인했다. 인사·소개 CHAT, 매핑룰 조회, 부서 3행·직원 10행의 JSON 이관 비교, 변환 대상 SQL 조회, SQL 한 개의 변환, 튜닝룰 조회와 튜닝, STALE·NEEDS_INPUT·ERROR, 요청 밖 이관 차단을 관찰했다. 출력은 demo-evidence.json을 읽어 표시한다. WHERE 1 = 1은 활성 규칙에 따라 Code가 적용하며 ANSI JOIN·서브쿼리 축소는 조건부 제안이다. Dify 서버·실제 모델·Oracle은 이 로컬 재생에 포함되지 않았다.

이 HTML은 DB 실행·모델 호출·Excel 업로드를 수행하는 앱이 아니다. 실제 Oracle·Dify 서버·LLM 실행, 수백 SQL 규모 부하 검증, 조회 값 동등성은 수행하지 않았다.
