"""Developer-facing narrative; implementation claims follow checked repository sources."""
from content import ORIGIN, WORKFLOW, ARCHITECTURE, TRANSFORM, VERIFICATION, DIFY, EXPANSION
from mapping_reference import render as render_mapping_reference
from demo_outputs import render as render_demo_outputs

ORIGIN = '''<div class="origin-layout"><div><p>신입사원으로 SI 개발 프로젝트에 참여하며 노후 플랫폼 리팩토링을 경험했습니다. AS-IS SQL을 분석하고 TO-BE 구조에 맞게 수정하는 작업이 반복됐고, 바꾼 SQL과 이관 데이터가 올바른지 검증하는 부담도 함께 느꼈습니다.</p><p>SQL을 사람이 작성해도 검증 SQL과 테스트 조건을 별도로 준비해야 합니다. 테이블과 SQL이 많아질수록 이 작업을 일일이 관리하기 어려워집니다.</p></div><div class="origin-problem"><strong>작성 → 검증 SQL → 테스트 → 재검증</strong><p>확정한 매핑룰과 기존 SQL을 재사용해, 생성부터 검증 준비·반복 실행·실패 추적까지 연결하고자 했습니다.</p></div></div>'''

VALIDATION_WORKLOAD = '''<p>Migration SQL이나 TO-BE SQL을 작성하는 것으로 전환 작업이 끝나지는 않습니다. 이관 데이터가 맞는지, 기존 조회가 같은 업무 결과를 반환하는지 확인할 검증 SQL과 테스트를 다시 만들어야 합니다.</p>
<div class="workload-loop"><span>SQL 작성·수정</span><span>검증 SQL 작성</span><span>조건·바인드별 실행</span><span>결과 대조</span><span>변경 후 재검증</span></div>
<div class="check"><strong>수백 개 SQL × 여러 조건 × 반복 재검증</strong><p>테이블과 SQL이 많아지면 매핑 변경의 영향, 검증 SQL, 테스트 조건, 실패 목록을 함께 관리해야 합니다. 이 규모 표현은 자동화가 필요한 업무 상황이며, 수백 건 처리 성능을 검증했다는 의미는 아닙니다.</p></div>
<h3 class="subsection-title">예: 부서별 인원 조회가 같은 결과를 반환해야 합니다.</h3>
<div class="sql-pair"><div><h3>AS-IS 조회</h3><pre><code>SELECT e.EMPLOYEE_ID, e.EMAIL
FROM ASIS_HR_EMP e
WHERE e.DEPARTMENT_ID = :dept_id</code></pre></div><div><h3>TO-BE 조회</h3><pre><code>SELECT e.EMP_ID, e.EMAIL_ADDR
FROM TOBE_HR_EMP e
WHERE e.DEPT_ID = :dept_id</code></pre></div></div>
<table class="expectation-table"><thead><tr><th>비교 기준</th><th>무엇이 같아야 할까요?</th></tr></thead><tbody><tr><th>같은 입력 조건</th><td>양쪽 모두 같은 dept_id와 동일한 업무 범위를 조회</td></tr><tr><th>같은 대상 인원</th><td>컬럼명을 매핑한 뒤 동일한 직원이 누락·추가 없이 포함</td></tr><tr><th>같은 값과 중복</th><td>이메일 값·NULL·중복 행의 발생 여부를 동일 기준으로 대조</td></tr><tr><th>변경 후 재검증</th><td>매핑·SQL이 바뀌면 관련 작업을 다시 실행하고 실패를 추적</td></tr></tbody></table>
<p class="caption">검증 기준을 설명하기 위한 별도 부서 필터 예시입니다. 현재 Dify 시연용 SQL은 뒤에서 소개하는 SQL_SEQ=1 한 개입니다. 위 두 SQL을 Oracle에서 실행한 결과는 아닙니다. 정렬은 업무 요구가 있을 때 별도로 비교합니다.</p>
<h3 class="subsection-title">Agent가 줄이려는 반복</h3><div class="roles"><div><strong>검증 준비</strong><p>매핑·DDL·AS-IS/TO-BE SQL을 기준으로 이관 검증 SQL, TEST_SQL, 바인드 사례 구성을 연결합니다.</p></div><div><strong>반복 처리</strong><p>원본 Agent는 DB 연결 환경에서 SQL 작업의 생성·실행·상태 기록·재시도를 연결합니다. 사람은 실패와 업무 의미 검토에 집중합니다.</p></div><div><strong>현재 확인 범위</strong><p>원본 Conversion의 SELECT 검증은 최대 3개 바인드 사례와 결과 건수 비교입니다. Dify는 JSON 이관 비교와 SQL 산출물 검사이며, 완전한 조회 결과 동등성은 별도 검증 대상입니다.</p></div></div>'''

RESPONSIBILITY = '''<p>기존에는 개발자와 DBA가 업무 해석부터 SQL 작성·실행·결과 확인까지 반복해서 수행했습니다. 앞으로는 사람이 업무 기준을 확정하고, Agent가 그 기준에 따른 반복 실행을 맡는 역할 분담을 지향합니다.</p>
<table><thead><tr><th scope="col">업무</th><th scope="col">사람이 확정할 것</th><th scope="col">Agent에 맡길 반복 작업</th></tr></thead><tbody>
<tr><th scope="row">업무 이해</th><td>현업 엔지니어의 업무 흐름, 데이터의 의미, 예외 정책</td><td>기존 SQL·매핑 정보를 조회하고 검토 자료로 정리</td></tr>
<tr><th scope="row">매핑 설계</th><td>원천·대상 대응, 변환 기준, PK·NULL·중복 처리, 검증 기준</td><td>확정된 매핑룰을 SQL 생성·변환에 반복 적용</td></tr>
<tr><th scope="row">SQL 처리</th><td>생성 SQL 검토, 실행 범위와 운영 적용 판단</td><td>이관·검증 SQL 생성, SQL 변환·튜닝·포매팅</td></tr>
<tr><th scope="row">실행과 검증</th><td>업무 정합성 판정, 실패 원인 검토, 재실행 결정</td><td>실행기·검증 코드로 처리하고 상태·건수·오류·산출물을 기록</td></tr>
</tbody></table>
<div class="check"><strong>매핑룰을 확정한 뒤 Agent를 사용합니다.</strong><p>업무 의미와 정합성 기준을 사람이 먼저 정해야 합니다. 아래 흐름은 운영 활용을 위한 역할 분담이며, 별도의 매핑 승인·권한 시스템이 이미 구현됐다는 뜻은 아닙니다.</p></div>'''

USERS = '''<div class="user-paths"><article><h3>DBA</h3><p>실제 Migration과 검증의 반복을 자동화하고, 실패한 작업의 근거를 추적합니다.</p><ol><li>확정 매핑·대상 범위·선행 작업 확인</li><li>이관 SQL·검증 SQL 생성 및 검토</li><li>DB 연결 환경에서 이관·검증 실행</li><li>상태·로그·SQL로 실패 원인 분석</li><li>규칙·데이터·SQL 보정 후 재처리</li></ol><p class="caption">활용 산출물: MIG_SQL, VERIFY_SQL, 작업 상태, 실행 로그</p></article>
<article><h3>B/E 개발자</h3><p>매핑룰을 조회하고 생성된 TO-BE SQL을 검토해 새 시스템의 API·업무 로직 개발에 활용합니다.</p><ol><li>AS-IS SQL과 테이블·컬럼 매핑 조회</li><li>TO-BE SQL 변환 요청</li><li>바인드·조회 조건·결과 의미 확인</li><li>검토한 SQL을 Repository·DAO 등에 적용</li><li>API 응답·업무 시나리오 통합 테스트</li></ol><p class="caption">활용 산출물: 매핑룰, TO_SQL, BIND_SET, 검증 결과</p></article></div>
<p class="scope-note">DBA·B/E는 주요 활용 사용자입니다. 두 역할을 구분하는 접근 권한이나 백엔드 코드 자동 반영 기능의 구현을 뜻하지 않습니다.</p>'''

MAPPING = '''<ol class="handoff"><li><h3>현업 업무 흐름 파악</h3><p>어떤 시점에 데이터가 생성·변경되는지, 누가 어떤 업무에서 조회하는지 이해합니다.</p></li><li><h3>정합성 기준 정의</h3><p>업무 키, 필수값, NULL·중복, 코드 체계, 원천 조건과 검증 범위를 결정합니다.</p></li><li><h3>매핑룰 확정</h3><p>원천 테이블·컬럼과 대상 테이블·컬럼의 대응 및 필요한 변환 기준을 검토합니다.</p></li><li><h3>Agent에 작업 인계</h3><p>매핑룰·DDL·기존 SQL을 기준으로 이관과 변환을 요청하고 산출물을 확인합니다.</p></li></ol>
<table class="mapping-example"><caption>직원 정보 매핑 예시 · 직접 컬럼 대응</caption><thead><tr><th scope="col">AS-IS</th><th scope="col">TO-BE</th><th scope="col">사람이 확인할 기준</th></tr></thead><tbody><tr><td><code>EMPLOYEE_ID</code></td><td><code>EMP_ID</code></td><td>PK 대응, 빈 키·중복 허용 여부</td></tr><tr><td><code>FIRST_NAME</code></td><td><code>GIVEN_NAME</code></td><td>이름의 업무 의미와 NULL 정책</td></tr><tr><td><code>SALARY</code></td><td><code>BASE_SALARY</code></td><td>금액의 의미·단위·자료형</td></tr></tbody></table>
<p class="scope-note">매핑을 확정해도 구현이 지원하는 범위인지 확인해야 합니다. 현재 Dify 샘플은 직접 컬럼 복사와 제한된 단일 조건을 지원하며, 함수·수식·다중 조건 매핑은 지원하지 않습니다.</p>'''

EXECUTION = '''<p>원본 구현은 LangGraph 안에서 SQL 생성·실행·검증·재시도를 연결합니다. Supervisor는 대기 작업을 조회해 registry에 보관하고, 도구가 꺼낸 작업 객체를 Migration Agent에 전달합니다.</p>
<ol class="execution-path"><li><div><h3>check_dependency</h3><p><code>PRIOR_MAP_ID</code>로 선행 작업 확인. 실패한 선행 작업은 후속 작업을 건너뛰고, 미완료 작업은 실행 대상에서 보류합니다.</p></div></li><li><div><h3>fetch_ddl</h3><p>원천·대상 DDL을 조회해 컬럼·자료형 등 SQL 생성에 필요한 스키마 문맥을 준비합니다.</p></div></li><li><div><h3>generate</h3><p>매핑룰과 DDL로 이관 SQL·검증 SQL을 함께 생성합니다. <code>USER_EDITED='Y'</code>인 경우 저장된 SQL을 재사용합니다.</p></div></li><li><div><h3>execute</h3><p>설정에 따라 대상 테이블 초기화 후 이관 SQL을 실행합니다. 초기화·실행 오류는 서로 다른 실패 상태로 기록합니다.</p></div></li><li><div><h3>verify</h3><p>검증 SQL을 실행해 결과를 확인합니다. 검증 실패는 <code>FAIL-TEST</code> 경로로 분기합니다.</p></div></li><li><div><h3>finalize</h3><p>최종 상태, 처리 시간, 재시도와 이력을 저장합니다. 결과는 <code>PASS</code>·<code>SKIP-PRIOR-FAIL</code>·<code>NOT_RUNNABLE</code>·<code>FAIL</code>로 구분합니다.</p></div></li></ol>
<details><summary>원본의 재시도 분기와 한계</summary><table><thead><tr><th>실패 상태</th><th>재처리 경로</th><th>사람이 검토할 것</th></tr></thead><tbody><tr><td><code>FAIL-TRUNCATE</code></td><td>SQL 생성 없이 execute 재시도</td><td>권한·잠금·초기화 가능 범위</td></tr><tr><td><code>FAIL-INSERT</code></td><td>이관 SQL·검증 SQL 재생성</td><td>매핑·자료형·제약조건·실행 SQL</td></tr><tr><td><code>FAIL-TEST</code></td><td>기존 이관 SQL 유지, 검증 SQL 재생성</td><td>데이터와 검증 기준을 함께 검토</td></tr></tbody></table><p>최대 시도는 DB_MIGRATION_MAX_ATTEMPTS + 1입니다. 검증 SQL을 재생성해 PASS가 됐다는 사실만으로 업무 정합성이 증명되는 것은 아닙니다. LLM 인증·토큰 한도 등 치명 오류는 일반 업무 재시도와 구분해 배치를 중단합니다.</p></details>
<p class="caption">원본 코드의 흐름입니다. Dify 더미 시연은 Oracle execute 대신 JSON 처리와 비교를 수행합니다.</p>'''

MAIN_NODES = [
('Start', 'start', '초기 테이블 JSON과 대화 요청', '선택 입력과 현재 사용자 요청', '최초 샘플 입력과 후속 대화의 진입점입니다.'),
('Code', 'initialize', '저장된 상태 또는 샘플을 복원', 'state_json · session_transport · request_id', '최신 테이블을 준비합니다. Agent가 상태 전체를 새로 만들게 하지 않습니다.'),
('Agent', 'agent', '사용자 요청을 통합 Workflow Tool로 전달', 'request + 사전 연결된 session_json', 'request는 모델이 작성하고 상태는 initialize 출력에서 연결합니다. 실제 작업 계획과 실행은 Tool 내부가 담당합니다.'),
('Code', 'commit', 'Tool 응답을 검증하고 반영할 상태를 확정', 'state_json · answer · commit_status · narrative_json', '요청 ID·입력 해시·revision을 비교합니다. Agent의 자연어 답변을 상태의 근거로 사용하지 않습니다.'),
('Variable Assigner', 'save', '확정한 상태를 대화 변수에 저장', '다음 턴에서 복원할 상태', '같은 대화에서 변경을 이어갑니다. 대화 간 공유 DB나 영구 업무 저장소를 뜻하지 않습니다.'),
('LLM', 'narrator', '검증된 요약으로 결과 해설과 다음 작업 안내', '한국어 설명', '상태를 변경하거나 DB 실행 결과를 만들어내는 노드가 아닙니다.'),
('Answer', 'answer', '해설과 확정 상태의 결과 표 출력', '사용자에게 보이는 설명 + 표', '표와 처리 상태는 commit이 확인한 사실을 기준으로 전달합니다.')
]
TOOL_NODES = [
('Start', 'start', 'Tool 인자 수신', 'request · session_json', 'Agent에서 호출한 요청과 사전 연결한 최신 상태를 받습니다.'),
('Code', 'prepare', '상태 검증과 계획용 문맥 구성', 'state_json · context_json · request · request_id', '현재 매핑·SQL·참고 규칙과 대화 문맥을 정리합니다.'),
('LLM', 'planner', '요청을 구조화된 작업 계획·SQL로 변환', 'action · stage · 대상 키 · SQL 산출물', 'query·insert·update·delete·run·clarify·export 중 지원 작업을 계획합니다. 모호한 요청은 clarify로 남깁니다.'),
('Code', 'execute', '계획 검사 후 JSON 작업·검증 수행', 'result_json', '허용된 작업을 실행합니다. 이관은 PK 기준 추가·갱신과 COUNT·행 값 비교를 수행하며 임의 SQL을 DB에서 실행하지 않습니다.'),
('End', 'end', '작업 결과를 Agent로 반환', 'result_json', '반환된 결과는 메인 commit의 검증을 통과한 뒤 대화 상태에 반영됩니다.')
]

def component_list(nodes):
    return '<ol class="component-list">'+''.join(f'<li><div class="node-name"><strong>{kind}</strong><code>{id}</code></div><div><h3>{title}</h3><p>{desc}</p><p class="node-output"><strong>전달 값</strong> {out}</p></div></li>' for kind,id,title,out,desc in nodes)+'</ol>'

DIFY_COMPONENTS = '''<p>원본의 전문 Agent 구조를 Dify에서는 기본 Agent와 통합 Workflow Tool 하나로 재현했습니다. LLM은 요청 해석·계획·해설을, Code는 실행·비교·상태 검사를 맡습니다.</p>
<figure class="dify-diagram"><figcaption>메인 Chatflow와 Agent가 호출하는 Tool</figcaption><ol class="graph-lane"><li><strong>Start</strong><span>입력</span></li><li><strong>Code</strong><code>initialize</code></li><li class="active-node"><strong>Agent</strong><span>Tool 호출</span></li><li><strong>Code</strong><code>commit</code></li><li><strong>Assigner</strong><code>save</code></li><li><strong>LLM</strong><code>narrator</code></li><li><strong>Answer</strong><span>결과 출력</span></li></ol><div class="tool-lane"><p><strong>Agent가 호출하고 결과를 돌려받는 내부 Workflow Tool</strong></p><ol class="graph-lane"><li><strong>Start</strong><span>인자 수신</span></li><li><strong>Code</strong><code>prepare</code></li><li><strong>LLM</strong><code>planner</code></li><li><strong>Code</strong><code>execute</code></li><li><strong>End</strong><span>결과 반환</span></li></ol></div></figure>
<h3 class="subsection-title">메인 Chatflow · 대화와 상태 관리</h3>'''+component_list(MAIN_NODES)+'''
<div class="tool-call"><strong>Agent → Workflow Tool 호출 → Agent Tool 응답 → commit</strong><p>Tool은 Agent가 호출하는 내부 실행 경로입니다. 메인 그래프에서 Agent와 commit 사이에 독립적인 직렬 노드가 추가되는 구조는 아닙니다.</p></div>
<h3 class="subsection-title">통합 Workflow Tool · 계획과 실행</h3>'''+component_list(TOOL_NODES)+'''
<div class="check"><strong>왜 계획 LLM과 실행 Code를 나누었을까요?</strong><p>계획은 생성형 모델이 만들지만, 실제 변경은 허용 작업·키·매핑·SQL 산출물 검사를 거칩니다. Code가 계산한 결과와 상태를 확정한 뒤 LLM이 설명하게 해, 자연어 응답과 실제 작업 결과를 구분합니다.</p></div>
<details><summary>Tool 결과 계약과 commit 검증</summary><pre class="contract"><code>{
  "kind": "smartmigrate.result.v1",
  "request_id": "이번 요청 ID",
  "base_hash": "입력 상태 SHA-256",
  "base_revision": 0,
  "status": "OK | ERROR | NEEDS_INPUT",
  "state_json": "변경된 상태 JSON 문자열",
  "answer": "처리 결과"
}</code></pre><p>commit은 Tool 응답에서 결과를 읽어 request_id, base_hash, base_revision과 반환 상태 revision을 확인합니다. 중복된 동일 결과는 하나로 보고, 서로 다른 결과는 CONFLICT로 거부합니다. 결과가 없으면 NO_RESULT로 원래 상태를 유지합니다. 해시는 오래된 상태 확인용이며 인증 수단이 아닙니다.</p><p>현재 commit Code의 최상위 출력은 DSL 선언과 맞춘 문자열 4개(state_json, answer, commit_status, narrative_json)입니다.</p></details>
<p class="caption">저장소의 Dify 1.15 대상 DSL 01·02와 노드 소스를 기준으로 정리했습니다. 설치 서버의 실제 import·모델 호출 성공을 새로 검증한 결과는 아닙니다.</p>'''

FAILURES = '''<p>DBA는 실패 상태와 로그를 시작점으로, 문제가 매핑·SQL·데이터·실행 환경 중 어디에 있는지 좁힐 수 있습니다. 원인 후보와 조치 판단을 연결하는 활용 방식입니다.</p>
<table><thead><tr><th>실패 지점</th><th>확인할 근거</th><th>사람이 판단할 다음 조치</th></tr></thead><tbody><tr><th>선행 작업</th><td>PRIOR_MAP_ID, 선행 작업 상태</td><td>선행 작업 복구 후 후속 실행 여부 결정</td></tr><tr><th>SQL 실행</th><td>MIG_SQL, 오류 메시지, DDL·제약조건</td><td>SQL·매핑·권한·데이터 중 원인 후보 확인</td></tr><tr><th>검증</th><td>VERIFY_SQL, 불일치 건수·행, 매핑 기준</td><td>잘못된 데이터와 잘못된 검증 기준을 구분</td></tr><tr><th>Dify 상태 반영</th><td>commit_status, request_id·hash·revision</td><td>결과 누락·충돌·오래된 상태 여부 확인</td></tr></tbody></table>
<div class="check"><strong>분석 근거를 모으는 자동화와 원인 확정은 구분합니다.</strong><p>현재 구현은 상태·SQL·검증 결과·로그를 제공합니다. 모든 실패를 AI가 자율적으로 진단·복구한다고 보장하지 않습니다. 재시도만 반복하기보다 매핑과 검증 기준을 함께 확인해야 합니다.</p></div>
<details><summary>Dify 시연에서 실패가 상태를 바꾸지 않게 하는 방식</summary><p>잘못된 계획 JSON, 누락된 SQL 산출물 등 실행 실패는 업무 변경을 취소하고 오류 결과를 반환합니다. commit은 결과 없음·충돌·손상·오래된 상태를 거부하거나 원래 상태를 보존합니다. LLM 호출 자체의 오류는 Dify 실패 노드와 Traceback을 확인해야 합니다. 원본 Oracle 트랜잭션 동작과 이 JSON 시연의 롤백을 동일한 보장으로 설명하지 않습니다.</p></details>'''

SECTIONS = {
 'origin': ('현장에서 시작한 문제', 'SQL 작성·수정 이후에도 검증을 위한 반복 작업이 이어집니다.', ORIGIN),
 'validation-workload': ('수백 개 SQL의 검증 부담', '검증 SQL 작성과 조건별 실행·비교·재검증까지 자동화의 대상으로 봅니다.', VALIDATION_WORKLOAD),
 'responsibility': ('사람과 Agent의 역할', '업무 해석과 기준 확정은 사람에게, 규칙에 따른 반복 처리는 Agent에게.', RESPONSIBILITY),
 'users': ('DBA와 B/E 개발자', '하나의 매핑 기준을 서로 다른 업무에 활용합니다.', USERS),
 'mapping': ('Agent 사용의 출발점', '현업 업무 흐름과 데이터 정합성을 검토해 매핑룰을 확정합니다.', MAPPING),
 'mapping-tables': ('시연 매핑룰의 실제 구조', '테이블 매핑 → 컬럼 상세 → SQL 작업을 기존 JSON 그대로 살펴봅니다.', render_mapping_reference()),
 'workflow': ('매핑룰에서 검증까지', '매핑 확정 → 이관·검증 → 업무 SQL 변환·검증.', WORKFLOW),
 'architecture': ('Supervisor와 전문 Agent', '공통 규칙을 공유하고 실행 책임을 분리합니다.', ARCHITECTURE),
 'execution': ('Migration 실행 Workflow', '원본 LangGraph의 노드와 실패·재시도 분기를 살펴봅니다.', EXECUTION),
 'conversion': ('B/E 개발로 연결되는 SQL', '검토된 TO-BE SQL을 백엔드 구현과 통합 테스트에 활용합니다.', TRANSFORM),
 'validation': ('검증은 어디까지인가', '이관은 전체 대상 행의 COUNT·CONCAT 비교, 변환은 실제 바인드 값을 대입한 TEST_SQL까지.', VERIFICATION),
 'dify': ('Dify에서의 개념 시연', '실제 DB 접근 제약에 맞춰 JSON 더미 데이터로 범위를 줄였습니다.', DIFY),
 'demo-outputs': ('현재 기능과 시연 Output', '구현한 시연 기능과 실제 로컬 Code 재생에서 얻은 출력을 확인합니다.', render_demo_outputs()),
 'dify-components': ('Dify 컴포넌트의 책임', '메인 Chatflow와 내부 Workflow Tool의 입력·출력·검증 경계를 분리합니다.', DIFY_COMPONENTS),
 'failures': ('실패에서 다음 조치까지', '상태·로그·SQL·검증 결과를 DBA의 원인 분석 근거로 연결합니다.', FAILURES),
 'expansion': ('실제 업무로 이어갈 확장', '확정한 규칙을 기준으로 실행하고, 검토 가능한 결과로 돌려받습니다.', EXPANSION)
}
