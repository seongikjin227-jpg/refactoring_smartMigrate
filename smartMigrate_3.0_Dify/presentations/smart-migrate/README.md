# SQL깍두기 · SmartMigrate Agent

A 리포트형으로 확정한 개발자 대상 20~25분 스크롤 발표. B·C와 비교 화면은 제거했다.

## 열기
- index.html 또는 SmartMigrate-Agent.html: 확정본. 폴더 구조를 유지한다.
- concepts/a-report.html: 같은 내용의 분리된 HTML.
- SmartMigrate-Agent.zip: 확정본과 로컬 글꼴·샘플·자산 묶음.

일반 스크롤과 목차, Agent 역할 탭, 기술 상세 펼쳐보기를 제공한다. 매핑 테이블 섹션에는 NEXT_MIG_INFO 2행·NEXT_MIG_INFO_DTL 10행·NEXT_SQL_INFO 8행, 전체 컬럼 설명과 JSON 원문·다운로드를 넣었다.

## 수정·검증

검증 섹션은 원본 Oracle 구현의 두 산출물 흐름을 구분한다. Migration은 INSERT SQL과 COUNT·PK별 전체 대상 행 CONCAT 검증 SQL을, Conversion은 TO_SQL·실제 값 조회 BIND_SQL·BIND_SET 값 대입 TEST_SQL을 설명한다. 실제 값 대입 예시와 비교 제외 컬럼·최대 3개 바인드 사례·Dify 축소 범위는 펼쳐보기로 제공한다.
- content.py: 기존 공통 SQL·매핑·샘플 설명.
- developer_content.py: 역할·사용자·Workflow·Dify 설명.
- mapping_reference.py: 저장소 샘플을 그대로 읽어 실제 매핑 자료를 표시.
- capture_demo.py: 내보낸 DSL의 execute·commit Code를 고정 계획과 SQL fixture로 로컬 재생해 출력 증거를 저장. LLM·Dify 서버·Oracle 호출 없음.
- demo_outputs.py: 로컬 재생 증거에서 현재 기능·요청 예시·출력 표·SQL·오류를 표시.
- build_presentation.py: HTML과 샘플 다운로드 파일 생성.
- concepts/site.css 및 site.js: 스타일·역할 탭·목차 상태.
- verify_presentation.mjs: 제작 환경의 Taste Skill CDP를 이용한 데스크톱 검증.

데스크톱 1280×800, 1440×900, 1920×1080에서 16개 섹션·앵커·탭·전체 펼쳐보기를 확인했다. 표시·다운로드 JSON이 기존 원본 3개와 같은지 검사한다. 휴대폰·태블릿 검사는 하지 않는다.

시연 출력의 재생 근거는 concepts/samples/demo-evidence.json이다. 인사·소개와 변환 대상 조회를 포함한 14개 경로를 로컬에서 확인했다. 본문 시연은 인사·에이전트 소개 → 매핑룰 조회 → DB Migration 실행 → 변환 대상 SQL 조회 → SQL 변환 실행 → SQL 튜닝룰 조회 → SQL 튜닝 실행이다. 기본 SQL은 두 테이블·두 바인드·(+)·PK IN 서브쿼리를 포함한 한 개다. WHERE 1 = 1은 활성 RAG 규칙에 따라 Code가 적용하며 ANSI JOIN·서브쿼리 축소는 조건부 LLM 제안이다. 복합 요청은 actions로 순차 처리한다. Dify 서버·실제 모델·Oracle 실행을 검증한 결과는 아니다. 증거를 갱신하려면 capture_demo.py 다음 build_presentation.py를 실행한다.

사람의 매핑 확정은 운영 역할 분담이다. DBA 실패 분석은 상태·로그·SQL 기반이며 완전 자동 진단·복구 보장이 아니다. Excel은 확장 구상, 실DB 실행은 Oracle 연결 시의 경로다. 현재 LOG/RAG 역할은 DIFY_METADATA_SCOPE.md를 참고한다. Dify 런타임·DSL은 사용자 요청에 맞춰 별도 수정했으며 Langflow 구현은 수정하지 않았다.

이전 슬라이드·PDF는 previous-slides/에 보존했다. 루트의 기존 PDF도 이전 슬라이드 버전이다. 최신 ZIP에는 이전 PDF를 포함하지 않는다.

Pretendard v1.3.9를 로컬에 포함한다. SIL OFL 라이선스: concepts/assets/FONT-LICENSE.txt.
