"""Render observed local replay results, clearly separate from server execution."""
from pathlib import Path
import html
import json

ROOT=Path(__file__).resolve().parent

def escaped(value):return html.escape(str(value))

def render():
    evidence=json.loads((ROOT/'concepts/samples/demo-evidence.json').read_text(encoding='utf-8'))
    cases=evidence['cases']
    results=cases['migration']['narrative']['migration_results']
    counts=''.join(f'<tr><td>{r["MAP_ID"]}</td><td>{r["inserted"]}</td><td>{r["updated"]}</td><td>{r["unchanged"]}</td><td>{r["verification"]["count_result"]}</td><td>{r["verification"]["matched_count"]}</td><td>{r["verification"]["mismatch_count"]}</td></tr>' for r in results)
    jobs=cases['tuning']['state']['tables']['NEXT_SQL_INFO']['rows']
    sql_rows=''.join(f'<tr><td>{r["SQL_SEQ"]}</td><td>{escaped(r["SQL_ID"])}</td><td>{escaped(r["STATUS_CONVERSION"])}</td><td>{escaped(r["STATUS_TUNING"])}</td><td>{"적용" if "WHERE 1 = 1" in (r["TUNED_TO_SQL"] or "") else "없음"}</td></tr>' for r in jobs)
    selected=next(r for r in jobs if r['SQL_SEQ']==1)
    mapping=next(r for r in cases['migration']['state']['tables']['NEXT_MIG_INFO']['rows'] if r['MAP_ID']==2)
    artifacts=''.join(f'<details><summary>{field} · 직원 이관의 생성 SQL 보기</summary><pre class="contract"><code>{escaped(mapping.get(field) or "미생성")}</code></pre></details>' for field in ['MIG_SQL','VERIFY_SQL','VERIFY2_SQL'])
    features=[
      ('조회','매핑룰·원천/대상 테이블·SQL 작업 조회','현재 테이블과 상태의 표'),
      ('메타데이터 관리','매핑·SQL 작업 추가/수정/삭제','변경된 행, 매핑 변경 시 STALE 표시'),
      ('이관 시연','PK 기준 JSON 추가·갱신, COUNT·행 값 비교','대상 JSON, 처리 건수, 비교 표, 이관·검증 참고 SQL'),
      ('SQL 변환','매핑을 반영한 TO-BE SQL 생성·형태 검사','TO_SQL, 바인드 보존, DEMO-GENERATED'),
      ('튜닝 가이드 조회','NEXT_MIG_RAG_INFO의 활성 SQL_TUNING 규칙 조회','WHERE 작성 규칙, ANSI JOIN, 서브쿼리 축소의 설명과 전후 예시'),
      ('튜닝','저장된 TO_SQL에 작성 규칙 적용·구조 개선 제안','TUNED_FR_SQL, TUNED_TO_SQL, TUNED_RESULT; TO_SQL 보존'),
      ('실행 범위 검사','원문 요청과 계획의 단계가 다르면 업무 변경 전에 차단','SQL 변환·튜닝 요청에 migration 계획이 오면 ERROR'),
      ('포매팅','별도 요청 시 튜닝 SQL의 토큰을 유지하는 서식 정리','FORMATTED_SQL; 기본 7단계 시연과 별도'),
      ('대화 상태 관리','같은 대화에서 변경 값 유지, 모호한 요청 명확화','revision, pending_action, NEEDS_INPUT'),
      ('오류·결과 보호','잘못된 계획의 업무 변경 취소, 결과 상태 확인','ERROR와 업무 상태 보존, 검증된 결과 표'),
      ('JSON 내보내기','현재 상태를 JSON 텍스트로 출력','다음 시연 초기값으로 사용할 JSON; Excel 파일 아님')
    ]
    inventory=''.join(f'<tr><th scope="row">{escaped(a)}</th><td>{escaped(b)}</td><td>{escaped(c)}</td></tr>' for a,b,c in features)
    requests=''.join(f'<li><strong>{label}</strong><code>{escaped(cases[key]["request_example"])}</code><span>{desc}</span></li>' for key,label,desc in [
      ('introduction','인사 & 에이전트 소개','SmartMigrate Agent의 역할·기능 안내. Tool 호출 없이 일반 대화'),
      ('query','매핑룰 조회','NEXT_MIG_INFO·NEXT_MIG_INFO_DTL의 테이블·컬럼 매핑 확인'),
      ('migration','DB Migration 실행','AS-IS·TO-BE 데이터, COUNT·CONCAT 비교, 최종 결과'),
      ('sql_source','변환 대상 SQL 조회','NEXT_SQL_INFO의 AS-IS FR_SQL·바인드 확인. 변환은 실행하지 않음'),
      ('conversion','SQL 변환 실행','테이블·컬럼 매핑만 적용한 TO_SQL 생성. 조인·서브쿼리 구조 보존'),
      ('tuning_guide','SQL 튜닝룰 조회','NEXT_MIG_RAG_INFO의 활성 규칙과 전후 예시 확인'),
      ('tuning','SQL 튜닝 실행','직전 TO_SQL에 규칙을 적용해 TUNED_TO_SQL 생성')])
    return '''<div class="check"><strong>현재 개발한 것은 DSL과 시연 실행 로직입니다.</strong><p>아래 출력은 현재 내보낸 DSL의 execute·commit Code를 고정 계획·SQL fixture로 로컬 재생한 결과입니다. Dify 서버·LLM·Oracle은 호출하지 않았습니다. 자연어 요청은 시연용 예시이며 실제 모델이 해석한 입력이라는 뜻은 아닙니다.</p></div>
<h3 class="subsection-title">현재 개발된 기능과 출력</h3><table class="feature-table"><thead><tr><th>기능</th><th>현재 구현</th><th>사용자가 확인할 출력</th></tr></thead><tbody>'''+inventory+'''</tbody></table>
<h3 class="subsection-title">시연은 이 순서로 보여줍니다.</h3><ol class="demo-requests">'''+requests+'''</ol>
<h3 class="subsection-title">각 요청의 변경 범위</h3><table class="feature-table"><thead><tr><th>요청</th><th>변경하는 데이터</th><th>완료 후 다음 요청</th></tr></thead><tbody><tr><th>인사 & 소개</th><td>일반 답변만 제공</td><td>매핑룰 조회</td></tr><tr><th>매핑룰 조회</th><td>조회만 수행</td><td>검토 후 DB Migration 실행</td></tr><tr><th>DB Migration</th><td>TO-BE JSON·이관 상태·이관/검증 SQL</td><td>변환 대상 SQL 조회</td></tr><tr><th>변환 대상 SQL 조회</th><td>FR_SQL·바인드 조회만 수행</td><td>SQL 변환 실행</td></tr><tr><th>SQL Conversion</th><td>TO_SQL·변환 상태</td><td>SQL 튜닝룰 조회</td></tr><tr><th>SQL 튜닝룰 조회</th><td>조회만 수행</td><td>SQL 튜닝 실행</td></tr><tr><th>SQL 튜닝</th><td>TUNED_TO_SQL·튜닝 근거/상태</td><td>산출물·Oracle 검증 계획 확인</td></tr></tbody></table>
<h3 class="subsection-title">Output · 이관 처리와 검증 표</h3><table class="metadata-table"><thead><tr><th>MAP_ID</th><th>추가</th><th>갱신</th><th>동일</th><th>COUNT</th><th>값 일치</th><th>불일치</th></tr></thead><tbody>'''+counts+'''</tbody></table><p class="caption">부서 3행·직원 10행. 조건 적용 원천 PK와 매핑 컬럼을 JSON으로 비교한 결과입니다. 최종 결과는 DEMO-PASS입니다. Oracle 검증 SQL의 실행 결과가 아닙니다. Dify 출력에는 AS-IS·TO-BE 데이터 조회 표도 함께 표시됩니다.</p>'''+artifacts+'''
<h3 class="subsection-title">Output · 단일 SQL 작업의 산출물</h3><p>직원·부서 두 테이블 조인, :dept_id·:min_salary 바인드, Oracle (+)와 PK IN 서브쿼리를 한 SQL로 시연합니다. Conversion에서는 구조를 보존하고, 이후 튜닝에서 활성 RAG 규칙에 따라 변경합니다.</p><table class="metadata-table"><thead><tr><th>SQL_SEQ</th><th>SQL_ID</th><th>변환 상태</th><th>튜닝 상태</th><th>WHERE 1 = 1</th></tr></thead><tbody>'''+sql_rows+f'''</tbody></table>
<div class="sql-pair"><div><h3>변환 입력 · FR_SQL</h3><pre><code>{escaped(selected['FR_SQL'])}</code></pre><p class="caption">BIND_SET: <code>{escaped(selected['BIND_SET'])}</code></p></div><div><h3>변환 출력 · TO_SQL</h3><pre><code>{escaped(selected['TO_SQL'])}</code></pre></div></div>
<h3 class="subsection-title">튜닝은 변환된 TO_SQL에서 시작합니다.</h3><pre class="contract"><code>{escaped(selected['TUNED_TO_SQL'])}</code></pre><p class="caption">TUNED_FR_SQL에 직전 TO_SQL을 보존하고 TUNED_TO_SQL에 결과를 저장합니다. NEXT_MIG_RAG_INFO의 [WHERE_ONE]이 활성일 때 Code가 WHERE 작성 규칙을 적용하며, 비활성이면 강제하지 않습니다. 성능 향상을 뜻하지 않습니다.</p>
<details><summary>튜닝 가이드 출력과 조건부 적용 기준</summary><pre class="contract"><code>{escaped(cases['tuning_guide']['answer'])}</code></pre><p>(+) → ANSI JOIN과 서브쿼리 축소는 LLM의 조건부 제안입니다. 현재 샘플에 해당 구조가 없으면 미적용 사유를 설명합니다. 복잡한 조인·중첩의 의미 보존과 성능은 Oracle에서 별도로 검증해야 합니다.</p></details>
<div class="check"><strong>복합 요청은 요청한 작업만 순서대로 실행합니다.</strong><p><code>SQL 변환 및 튜닝 진행해줘</code>는 <code>actions</code> 목록의 conversion → tuning으로 처리합니다. 로컬 재생에서 TO-BE JSON은 초기 상태로 유지됐고 포매팅도 실행되지 않았습니다. 요청에 없는 이관 계획은 <code>{escaped(cases['blocked_scope']['commit_status'])}</code>로 차단합니다.</p></div>
<p class="scope-note">SQL 변환의 DEMO-GENERATED는 산출물 생성·정적 검사 상태입니다. 현재 Dify의 TEST_SQL은 Oracle 미실행·실제 결과 동등성 미검증을 알리는 문자열이며, 실행 가능한 검증 SQL을 생성·실행한 결과가 아닙니다.</p>
<h3 class="subsection-title">Output · 변경과 오류도 확인합니다.</h3><div class="output-cases"><div><strong>매핑 조건 변경</strong><p>MAP_ID=2의 조건을 변경하면 기존 매핑·SQL 결과를 STALE로 표시해 재처리가 필요함을 알립니다.</p></div><div><strong>대상이 모호한 요청</strong><p><code>{escaped(cases['clarify']['commit_status'])}</code> · {escaped(cases['clarify']['answer'])}</p></div><div><strong>SQL 산출물 누락</strong><p><code>{escaped(cases['error']['commit_status'])}</code> · {escaped(cases['error']['answer'])}</p><p>실패한 요청의 업무 변경을 취소하고 오류를 기록합니다.</p></div></div>
<details><summary>현재 개발 범위와 후속 실환경 검증</summary><p>로컬 Code 재생은 인사·소개와 변환 대상 조회를 포함해 14개 경로를 확인했습니다. 인사·소개는 Tool 호출 없이 commit의 CHAT 경로를 재생했습니다. 모델의 자연어 해석, Dify 서버 연결과 실행은 별도 검증 대상입니다.</p><p>Oracle 실제 실행·AS-IS/TO-BE 조회 결과의 값 동등성·성능 측정·수백 SQL 규모 부하 검증·Excel 입출력은 이번 Dify 시연에서 완료한 기능으로 표시하지 않습니다.</p></details>'''
