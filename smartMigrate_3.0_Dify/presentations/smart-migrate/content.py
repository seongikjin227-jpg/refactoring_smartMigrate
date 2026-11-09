"""Shared factual content for the confirmed report presentation."""

AGENTS = '''<div class="architecture" aria-label="Supervisor와 네 개 전문 Agent의 관계">
<div class="input"><strong>입력 자산</strong><span>매핑 마스터·상세 / 원천·대상 DDL / AS-IS SQL / 작업 요청</span></div>
<div class="connector" aria-hidden="true"></div>
<div class="supervisor"><h3>Supervisor</h3><p>대기 작업 조회 · 도구 선택 · Agent 호출 · 후속 단계 연결</p></div>
<div class="connector" aria-hidden="true"></div>
<div class="agent-tabs" role="tablist" aria-label="Agent별 역할">
<button id="tab-migration" role="tab" aria-selected="true" aria-controls="agent-panel" data-agent="migration"><strong>Migration</strong><span>이관 SQL 생성·실행·검증</span></button>
<button id="tab-conversion" role="tab" aria-selected="false" aria-controls="agent-panel" tabindex="-1" data-agent="conversion"><strong>Conversion</strong><span>AS-IS → TO-BE SQL</span></button>
<button id="tab-tuning" role="tab" aria-selected="false" aria-controls="agent-panel" tabindex="-1" data-agent="tuning"><strong>Tuning</strong><span>튜닝 규칙 적용·확인</span></button>
<button id="tab-formatting" role="tab" aria-selected="false" aria-controls="agent-panel" tabindex="-1" data-agent="formatting"><strong>Formatting</strong><span>최종 SQL 서식 정리</span></button>
</div>
<div class="agent-detail" id="agent-panel" role="tabpanel" tabindex="0" aria-labelledby="tab-migration"><h3>Migration Agent</h3><p class="detail-copy">매핑룰과 DDL로 이관 SQL과 검증 SQL을 생성하고, 실행·검증·재처리를 연결합니다.</p><p class="detail-steps">선행 작업 확인 → DDL 조회 → SQL 생성 → 실행 → 검증 → 상태·로그 저장</p></div>
<div class="connector" aria-hidden="true"></div>
<div class="store"><strong>공유 저장소</strong><span>매핑룰 · SQL 원본과 산출물 · 작업 상태 · 로그 · 참고 규칙</span></div>
</div>'''

ORIGIN = '''<div class="origin-layout"><div><p>신입사원으로 SI 개발 프로젝트에 참여하며 노후 플랫폼 리팩토링을 경험했습니다. 가장 크게 느낀 병목은 AS-IS SQL을 분석하고, TO-BE 구조에 맞게 수정하는 작업의 반복이었습니다.</p><p>기존 SQL에 담긴 업무의 맥락을 살리면서, 같은 분석과 수정에 드는 반복을 줄이고 싶었습니다.</p></div><div class="origin-problem"><strong>분석 → 수정 → 다시 분석</strong><p>기존 SQL을 재활용하고 매핑룰을 체계화하면, Migration과 개발 업무를 하나의 실행 흐름으로 연결할 수 있지 않을까?</p></div></div>'''

WORKFLOW = '''<p>매핑룰을 한 번의 기준으로 정리하고, 데이터 이관부터 기존 업무 SQL의 변환·검증까지 이어갑니다.</p>
<div class="flow"><div><strong>매핑룰 입력</strong><p>Excel 업로드<br>또는 DB에 정리</p><small>Excel은 확장 구상</small></div><div><strong>이관 SQL 생성</strong><p>INSERT SQL<br>+ COUNT·CONCAT 검증 SQL</p><small>이관 1개 + 검증 2개</small></div><div class="selected"><strong>이관·검증</strong><p>Oracle 실행<br>대상 전체 행의 건수·값 비교</p><small>DB 연결 가능한 경우</small></div><div><strong>업무 SQL 변환</strong><p>TO_SQL · BIND_SQL<br>실제 값 대입 TEST_SQL</p><small>변환·테스트 SQL 3종</small></div></div>
<p class="scope-note">현재 Dify 시연의 이관 예제는 MERGE/upsert입니다. 위의 INSERT SQL은 목표 산출물이며, Excel 입출력은 확장 구상입니다.</p>'''

ARCHITECTURE = AGENTS + '''<p class="caption">원본 구현의 책임 구조입니다. 각 Agent를 선택하면 처리 범위를 확인할 수 있습니다. Dify 시연에서는 하나의 통합 Workflow Tool로 축소했습니다.</p>
<div class="roles"><div><strong>생성의 기준</strong><p>매핑룰·DDL·기존 SQL을 공유해 각 단계의 판단 근거를 연결합니다.</p></div><div><strong>실행의 책임</strong><p>전문 Agent가 생성·실행·검증을 담당하고 결과를 상태와 로그로 남깁니다.</p></div><div><strong>재처리의 기준</strong><p>선행 작업과 실패 위치를 확인합니다. 검증 실패 시 기존 이관 SQL을 유지하고 검증 SQL을 재생성하는 경로가 있습니다.</p></div></div>'''

TRANSFORM = '''<p>데이터를 새 구조로 이관한 뒤에는, 기존 업무 SQL도 새 테이블과 컬럼을 바라보도록 연결해야 합니다.</p>
<div class="sql-pair"><div><h3>AS-IS SQL</h3><pre><code>SELECT e.EMPLOYEE_ID,
       e.FIRST_NAME, e.SALARY
FROM ASIS_HR_EMP e
WHERE e.DEPARTMENT_ID = :dept_id</code></pre></div><div><h3>TO-BE SQL</h3><pre><code>SELECT e.EMP_ID,
       e.GIVEN_NAME, e.BASE_SALARY
FROM TOBE_HR_EMP e
WHERE e.DEPT_ID = :dept_id</code></pre></div></div>
<div class="roles"><div><strong>Conversion</strong><p>TO_SQL·BIND_SQL·TEST_SQL을 생성합니다. BIND_SQL로 실제 원천 데이터의 파라미터 값을 추출하고, 그 값을 대입한 TEST_SQL로 AS-IS·TO-BE 조회 결과 건수를 비교합니다.</p></div><div><strong>Tuning</strong><p>변환 SQL에 튜닝 규칙을 적용하고 SELECT의 전후 건수를 비교합니다.</p></div><div><strong>Formatting</strong><p>최종 SQL의 서식과 가독성을 정리해 검토·전달을 돕습니다.</p></div></div>
<details><summary>변환 검증의 범위 자세히 보기</summary><p>원본 Oracle 구현에서 BIND_SQL은 바인드가 보존된 TO_SQL의 복사본이 아니라 실제 테스트용 값을 조회하는 SQL입니다. 실행 결과를 BIND_SET으로 저장하고, 최대 3개 바인드 사례의 실제 값을 AS-IS·TO-BE SQL에 대입해 실행 가능한 TEST_SQL을 생성합니다. 미해결 바인드는 남기지 않습니다.</p><p>SELECT 검증은 사례별 결과 건수를 비교하며 양쪽 모두 0건인 경우도 실패로 처리합니다. non-SELECT는 실행 검증 없이 SQL 생성 후 PASS를 저장하는 경로가 있습니다. 조회 값 전체의 동등성은 별도로 확인해야 합니다.</p></details>'''

VERIFICATION = '''<p>DB Migration은 이관 대상 전체 행의 <strong>건수와 실제 데이터 값</strong>을 검증합니다. SQL Conversion은 <strong>실제 데이터에서 찾은 바인드 값을 대입한 테스트 SQL</strong>까지 생성합니다. 두 작업의 산출물과 검증 방식은 다릅니다.</p>
<h3 class="subsection-title">DB Migration · 이관 SQL 1개 + 검증 SQL 2개</h3>
<table class="verification-table"><thead><tr><th scope="col">산출물</th><th scope="col">하는 일</th><th scope="col">확인할 결과</th></tr></thead><tbody><tr><th scope="row">MIG_SQL</th><td>확정한 매핑으로 INSERT INTO … SELECT … 이관 SQL 생성</td><td>원천 데이터를 TO-BE 구조로 적재</td></tr><tr><th scope="row">VERIFY_SQL</th><td>이관 범위 전체의 행 수와 비교 대상 컬럼의 COUNT 비교</td><td>AS-IS·TO-BE 건수 일치 여부</td></tr><tr><th scope="row">VERIFY2_SQL</th><td>PK로 원천·대상 행을 대응시키고, 각 행의 비교 대상 값을 CONCAT해 전수 비교</td><td>MATCH_CNT·MISMATCH_CNT로 실제 값의 일치·불일치 확인</td></tr></tbody></table>
<div class="check"><strong>건수 비교에서 끝나지 않습니다.</strong><p>이관 조건에 해당하는 모든 원천 행을 PK로 대상 행과 연결하고, 매핑된 비교 대상 컬럼의 CONCAT 값을 대조합니다. 같은 건수로 적재돼도 특정 직원의 부서·급여 값이 다르면 행 값 비교에서 불일치를 확인합니다. 일부 행을 샘플링하는 검증이 아닙니다.</p></div>
<h3 class="subsection-title">SQL Conversion · TO_SQL + BIND_SQL + TEST_SQL</h3>
<div class="flow"><div><strong>TO_SQL 생성</strong><p>AS-IS SQL의 테이블·컬럼을<br>TO-BE 매핑으로 변환</p><small>실행 시 사용할 바인드 유지</small></div><div><strong>BIND_SQL 생성·실행</strong><p>원천의 실제 데이터에서<br>테스트용 파라미터 값 조회</p><small>검증값을 조회하는 SQL</small></div><div><strong>BIND_SET 확보</strong><p>예: dept_id = 20<br>조회된 실제 값을 저장</p><small>최대 3개 바인드 사례</small></div><div class="selected"><strong>TEST_SQL 생성·실행</strong><p>파라미터 자리에 실제 값 대입<br>AS-IS·TO-BE 결과 비교</p><small>미해결 바인드 없는 검증 SQL</small></div></div>
<table class="verification-table"><thead><tr><th scope="col">산출물</th><th scope="col">목적</th><th scope="col">바인드 처리</th></tr></thead><tbody><tr><th scope="row">TO_SQL</th><td>개발에 사용할 변환된 TO-BE SQL</td><td><code>:dept_id</code>와 같은 파라미터 유지</td></tr><tr><th scope="row">BIND_SQL</th><td>원본 SQL의 조건·원천 데이터를 기준으로 실제 검증값 조회</td><td>조회 결과를 파라미터 이름별 BIND_SET으로 저장</td></tr><tr><th scope="row">TEST_SQL</th><td>같은 사례로 AS-IS·TO-BE SQL의 조회 결과 건수 비교</td><td>실제 조회값을 Oracle 리터럴로 대입해 바로 실행할 SQL 생성</td></tr></tbody></table>
<details><summary>예시 · :dept_id에 실제 조회값 20을 대입한 TEST_SQL</summary><p>처리 방식을 설명하는 예시입니다. 아래 20은 BIND_SQL 실행으로 확보한 값이라고 가정합니다. 웹페이지에서 DB를 조회한 결과가 아닙니다.</p><pre class="contract"><code>-- BIND_SQL: 실제 원천 데이터에서 테스트값 조회
SELECT DISTINCT DEPARTMENT_ID AS "dept_id"
FROM ASIS_HR_EMP
WHERE DEPARTMENT_ID IS NOT NULL

-- BIND_SET: [{"dept_id": 20}]
-- TO_SQL의 바인드: WHERE e.DEPT_ID = :dept_id
-- TEST_SQL: 원천·대상 모두 실제 값을 대입해 비교
SELECT
  1 AS CASE_NO,
  (SELECT COUNT(*) FROM ASIS_HR_EMP e
   WHERE e.DEPARTMENT_ID = '20') AS FROM_COUNT,
  (SELECT COUNT(*) FROM TOBE_HR_EMP e
   WHERE e.DEPT_ID = '20') AS TO_COUNT
FROM DUAL</code></pre><p>원본 실행기는 사례별 두 건수의 일치 여부를 확인하고, 양쪽 모두 0건인 경우도 실패로 판단합니다. 실제 TEST_SQL은 원본 조회 조건과 변환된 조회 구조를 반영합니다.</p></details>
<details><summary>검증 범위의 경계 · 전체 행과 전체 컬럼, 원본과 Dify</summary><p>Migration의 전수 비교는 매핑·이관 조건으로 정한 대상 행과 비교 대상 컬럼에 적용됩니다. 원본 Oracle 구현은 LOB/LONG·감사 컬럼 등 제외 규칙이 있으며, 모든 DB 테이블·모든 컬럼을 무조건 비교한다는 뜻은 아닙니다.</p><p>Conversion의 최대 3개 제한은 바인드 사례 수입니다. 각 사례의 조회 결과 건수를 비교하는 검증이며 Migration의 PK별 전체 행 CONCAT 비교와 구분합니다. 조회 값·NULL·중복의 완전한 의미 동등성과 튜닝 성능은 별도로 확인합니다.</p><p>위의 INSERT 및 TO_SQL·BIND_SQL·TEST_SQL 생성·실행은 원본 Oracle Agent의 처리 흐름입니다. 현재 Dify는 MERGE/upsert 방식의 JSON 이관과 전체 대상 행 값 비교를 시연합니다. Dify의 BIND_SQL은 TO_SQL 복사본, TEST_SQL은 Oracle 미실행 안내 문자열로 남아 있어 실제 값 추출·대입 테스트 생성까지 구현된 것으로 표시하지 않습니다.</p></details>'''

DIFY = '''<div class="dify-compare"><div><h3>이번 환경의 제약</h3><p>Dify 서버가 로컬 PC의 Oracle에 접근할 수 없었습니다. 실제 DB 연결을 제외하고 JSON 더미 데이터로 처리 흐름을 시연했습니다.</p><p class="caption">Dify 제품 전체의 DB 연결 한계가 아니라 이번 배포 환경의 제약입니다.</p></div><div><h3>더미 데이터로 확인한 흐름</h3><p>테이블 입력 → 매핑 기반 이관 → JSON 결과 비교 → SQL 산출물 확인 → 대화 내 상태 유지</p></div></div>
<div class="dify-run"><span>대화 입력</span><span>기본 Agent</span><span>통합 Workflow Tool</span><span>Code 상태 확인·저장</span><span>해설 + 결과 표</span></div>
<p class="demo-info">부서 3개 · 직원 10명 · 매핑 2개 · 컬럼 규칙 10개 · SQL 1개</p>
<table><thead><tr><th scope="col">샘플 대상</th><th scope="col">원천 건수</th><th scope="col">대상 대응</th><th scope="col">값 일치</th><th scope="col">불일치</th></tr></thead><tbody><tr><th scope="row">부서</th><td>3</td><td>3</td><td>3</td><td>0</td></tr><tr><th scope="row">직원</th><td>10</td><td>10</td><td>10</td><td>0</td></tr></tbody></table>
<div class="sample-status"><p><strong>DEMO-PASS</strong>더미 이관·비교 완료</p><p><strong>DEMO-GENERATED</strong>변환 산출물 생성</p><p><strong>DEMO-PROPOSED</strong>튜닝 산출물 제안</p></div>
<p class="scope-note">저장소에 기록된 JSON 비교 결과입니다. 이 웹페이지에서 Oracle이나 Dify 서버를 실행한 결과가 아닙니다. 오프라인 고정 SQL 테스트는 LLM의 자연어 해석을 검증하지 않습니다.</p>'''

EXPANSION = '''<div class="flow"><div><strong>매핑 입력</strong><p>Excel 업로드<br>또는 DB 매핑 관리</p><small>파일 구조·PK·필수값 확인</small></div><div><strong>실DB 실행·검증</strong><p>이관 SQL·검증 SQL<br>업무 SQL 변환·검증</p><small>Oracle 연결 시 검증</small></div><div><strong>산출물 제공</strong><p>SQL 파일과 검증 결과<br>매핑·결과 Excel</p><small>Excel 입출력은 확장 구상</small></div></div>
<p class="end-statement">기존 자산을 재사용하고,<br>규칙과 검증으로 반복 업무를 줄입니다.</p>'''

