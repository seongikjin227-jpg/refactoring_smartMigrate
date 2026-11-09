"""Render the checked-in Dify sample tables without changing their data."""
from pathlib import Path
import html
import json

SOURCE = Path(__file__).resolve().parents[2] / 'smartMigrate_3.0_Dify' / 'samples' / 'tables'
NAMES = ('NEXT_MIG_INFO', 'NEXT_MIG_INFO_DTL', 'NEXT_SQL_INFO')

DESCRIPTIONS = {
 'MAP_ID':'매핑 마스터의 식별자. 상세의 MAP_ID와 연결합니다.',
 'MAP_TYPE':'매핑 유형. 현재 샘플은 SIMPLE입니다.',
 'FR_TABLE':'원천 AS-IS 테이블.', 'TO_TABLE':'대상 TO-BE 테이블.',
 'TRUNC_YN':'대상 초기화 설정. 현재 Dify 시연은 N만 지원합니다.',
 'CONDITION':'이관할 원천 범위. 현재 시연은 빈 값 또는 제한된 단일 비교만 지원합니다.',
 'USE_YN':'활성 여부. 실행·변환은 활성 매핑을 기준으로 합니다.',
 'PRIORITY':'처리 정렬 순서. 선행 의존성은 PRIOR_MAP_ID로 별도 지정합니다.',
 'PRIOR_MAP_ID':'먼저 처리할 매핑의 MAP_ID. 직원 매핑 2는 부서 매핑 1을 참조합니다.',
 'STATUS':'작업 상태. 초기 null이며 시연 이관 완료는 DEMO-PASS입니다.',
 'USER_EDITED':'사용자 수정 SQL 여부를 표현하는 계약 필드. 원본 Agent는 Y일 때 저장 SQL을 재사용합니다.',
 'MIG_SQL':'생성한 이관 SQL. 현재 Dify 예제는 MERGE이며 DB에서 실행하지 않습니다.',
 'VERIFY_SQL':'생성한 건수 검증 참고 SQL. Dify의 실제 판정은 JSON 비교입니다.',
 'VERIFY2_SQL':'행 값 비교를 설명하는 참고 SQL. Oracle 실행 검증은 별도입니다.',
 'BATCH_CNT':'처리 횟수 기록 필드.', 'RETRY_COUNT':'재시도 횟수 기록 필드.',
 'ELAPSED_SECONDS':'처리 시간 기록 필드. 샘플 초기값은 null입니다.',
 'LOG':'해당 매핑 또는 SQL 작업의 결과·오류 요약 필드.',
 'REG_TS':'등록 시각 기록 필드. 샘플 초기값은 null입니다.',
 'UPD_TS':'갱신 시각 기록 필드. 샘플 초기값은 null입니다.',
 'MAP_DTL':'매핑 내 컬럼 대응 순번. MAP_ID와 함께 상세의 복합 PK입니다.',
 'FR_COL':'원천 테이블의 실제 컬럼명.', 'TO_COL':'대상 테이블의 실제 컬럼명.',
 'SQL_SEQ':'SQL 작업의 PK.', 'SPACE_NM':'SQL 작업을 묶는 업무 영역. 샘플은 HR_DEMO입니다.',
 'SQL_ID':'영역 안의 업무 SQL 이름. SPACE_NM과 함께 중복 여부를 검사합니다.',
 'REF_SEQ':'다른 SQL_SEQ를 참조하는 필드. 매핑 MAP_ID를 연결하는 필드가 아닙니다.',
 'STATUS_CONVERSION':'변환 상태. 시연 생성 완료는 DEMO-GENERATED입니다.',
 'STATUS_TUNING':'튜닝 상태. 시연 제안 완료는 DEMO-PROPOSED입니다.',
 'TARGET_TABLE':'변환에 사용할 원천 AS-IS 테이블 범위. 여러 개면 쉼표로 구분합니다.',
 'TAG_KIND':'SQL 종류. 현재 샘플은 SELECT입니다.',
 'FR_SQL':'보존할 AS-IS SQL 원본.', 'EDIT_FR_SQL':'수정한 원본 SQL. 값이 있으면 계획 시 FR_SQL보다 우선합니다.',
 'TO_SQL':'생성한 TO-BE SQL.', 'BIND_SQL':'바인드 관련 SQL 산출물. 시연에서는 실제 바인드 실행을 하지 않습니다.',
 'BIND_SET':'바인드 샘플을 담은 JSON 문자열. 중첩 JSON 객체가 아닌 문자열로 보존합니다.',
 'TEST_SQL':'변환 결과 검증용 SQL 산출물. 실제 DB 실행 검증과 구분합니다.',
 'TUNED_TO_SQL':'튜닝한 TO-BE SQL.', 'TUNED_RESULT':'튜닝 근거·결과 설명. 실제 성능 측정과 구분합니다.',
 'TUNED_FR_SQL':'계약에 포함된 AS-IS 측 튜닝 SQL 산출물 필드.',
 'FORMATTED_SQL':'서식을 정리한 최종 SQL.',
 'BLOCK_RAG_CONTENT':'계약에 남아 있는 참고 규칙 관련 필드. 현재 샘플은 null입니다.'
}

def cell(value):
    return '<span class="null-value">null</span>' if value is None else html.escape(str(value))

def sample_table(name, columns):
    data=json.loads((SOURCE/f'{name}.json').read_text(encoding='utf-8'))
    header=''.join(f'<th scope="col">{html.escape(c)}</th>' for c in columns)
    rows=''.join('<tr>'+''.join(f'<td>{cell(row.get(c))}</td>' for c in columns)+'</tr>' for row in data['rows'])
    return f'<table class="metadata-table"><thead><tr>{header}</tr></thead><tbody>{rows}</tbody></table>'

def full_contract(name):
    raw=(SOURCE/f'{name}.json').read_text(encoding='utf-8')
    data=json.loads(raw)
    descriptions = dict(DESCRIPTIONS)
    if name == 'NEXT_SQL_INFO':
        descriptions['PRIORITY'] = '기존 계약의 우선순위 필드. 현재 Dify SQL 실행기는 이 값으로 정렬하지 않습니다.'
        descriptions['USER_EDITED'] = '사용자 수정 여부를 담는 기존 계약 필드. 현재 시연의 입력 SQL 우선순위는 EDIT_FR_SQL 값 유무로 정합니다.'
    fields=''.join(f'<tr><th scope="row"><code>{col}</code></th><td>{html.escape(descriptions.get(col,"기존 샘플 계약에 포함된 필드."))}</td></tr>' for col in data['columns'])
    return f'''<details class="metadata-details"><summary>{name} · 전체 컬럼과 의미</summary><table><thead><tr><th>컬럼</th><th>역할</th></tr></thead><tbody>{fields}</tbody></table></details>
<details class="metadata-details"><summary>{name} · 샘플 JSON 원문 보기</summary><p class="caption">원본의 columns·primary_key·rows와 초기 null 값을 그대로 표시합니다.</p><pre class="sample-json"><code>{html.escape(raw)}</code></pre><a class="sample-download" href="samples/{name}.json" download="{name}.json">원본 JSON 다운로드</a></details>'''

def render():
    master=sample_table('NEXT_MIG_INFO',['MAP_ID','FR_TABLE','TO_TABLE','PRIOR_MAP_ID','USE_YN'])
    dtl=sample_table('NEXT_MIG_INFO_DTL',['MAP_ID','MAP_DTL','FR_COL','TO_COL'])
    sql=sample_table('NEXT_SQL_INFO',['SQL_SEQ','SQL_ID','TARGET_TABLE'])
    example=json.loads((SOURCE/'NEXT_SQL_INFO.json').read_text(encoding='utf-8'))['rows'][0]
    return '''<p>시연에서는 아래 3개 테이블을 핵심 입력으로 설명합니다. 원천·대상 업무 데이터는 별도로 준비하고, 매핑 마스터·컬럼 상세·SQL 작업을 이 구조에 맞춰 정리합니다.</p>
<div class="metadata-links"><div><strong>NEXT_MIG_INFO</strong><span>테이블 단위 이관 작업</span></div><div><strong>NEXT_MIG_INFO_DTL</strong><span>MAP_ID로 연결한 컬럼 대응</span></div><div><strong>NEXT_SQL_INFO</strong><span>AS-IS SQL과 변환 산출물</span></div></div>
<h3 class="subsection-title">NEXT_MIG_INFO · 무엇을 어디로 이관할까요?</h3><p class="metadata-key">PK: <code>MAP_ID</code> · 기존 샘플 2행</p>'''+master+'''
<p class="caption">MAP_ID=1은 부서, MAP_ID=2는 직원입니다. 직원의 PRIOR_MAP_ID=1로 부서 이관을 먼저 요구합니다. PRIORITY는 정렬 기준이며 의존성을 대신하지 않습니다.</p>
<p class="scope-note">초기에 정리할 값은 테이블·조건·활성 여부·순서·선행 관계입니다. MIG_SQL·VERIFY_SQL·STATUS 등은 처리 후 확인할 산출물입니다. 전체 JSON에는 기존 계약 필드를 유지합니다.</p>'''+full_contract('NEXT_MIG_INFO')+'''
<h3 class="subsection-title">NEXT_MIG_INFO_DTL · 어떤 컬럼을 연결할까요?</h3><p class="metadata-key">복합 PK: <code>MAP_ID + MAP_DTL</code> · 기존 샘플 10행 전부</p>'''+dtl+'''
<p class="caption">마스터의 MAP_ID에 컬럼 규칙을 여러 행으로 연결합니다. 현재 시연은 FR_COL → TO_COL 직접 복사이며 함수·수식 매핑은 지원하지 않습니다.</p>'''+full_contract('NEXT_MIG_INFO_DTL')+'''
<h3 class="subsection-title">NEXT_SQL_INFO · 어떤 SQL을 변환할까요?</h3><p class="metadata-key">PK: <code>SQL_SEQ</code> · 두 테이블 조인의 단일 시연 SQL</p>'''+sql+f'''
<div class="sql-input-example"><strong>SQL_SEQ=1 · EMP_DEPT_TUNING_DEMO 원본</strong><pre><code>{html.escape(example['FR_SQL'])}</code></pre><p><code>BIND_SET</code>: <code>{html.escape(example['BIND_SET'])}</code></p></div>
<p class="scope-note">이 테이블에는 MAP_ID 연결 컬럼이 없습니다. TARGET_TABLE과 FR_SQL의 원천 테이블을 기준으로 활성 매핑을 적용합니다. REF_SEQ는 다른 SQL_SEQ의 참조이며 매핑 ID가 아닙니다. FR_SQL은 보존하고 TO_SQL·TEST_SQL·FORMATTED_SQL 등을 산출물로 확인합니다.</p>'''+full_contract('NEXT_SQL_INFO')+'''
<h3 class="subsection-title">LOG JSON과 RAG JSON의 위치</h3>
<table><thead><tr><th>테이블</th><th>현재 DSL에서의 역할</th><th>시연에서의 사용</th></tr></thead><tbody><tr><th scope="row"><code>NEXT_MIG_LOG</code></th><td>실행 Code가 작업·상태·메시지를 기록하며 최근 30행을 유지합니다.</td><td>초기 rows는 비워 두고 실행 후 확인합니다. Dify Trace와 업무 이력은 역할이 다릅니다.</td></tr><tr><th scope="row"><code>NEXT_MIG_RAG_INFO</code></th><td>현재 변환·튜닝 규칙의 원본입니다. 활성 규칙을 planner에 전달하며 WHERE·ANSI 검사도 활성 규칙에 의해 켜집니다. 임베딩·벡터 검색은 없습니다.</td><td>먼저 가이드를 조회하고 SOURCE_SQL/TARGET_SQL 예시를 확인합니다. USE_YN=N이면 해당 규칙을 적용하지 않습니다. 활성 튜닝 규칙이 없으면 TO_SQL을 유지합니다.</td></tr></tbody></table>
<div class="check"><strong>매핑·SQL은 처리 대상, RAG_INFO는 적용 규칙, LOG는 실행 이력입니다.</strong><p>RAG_ID 2·3·4로 WHERE 작성 규칙·ANSI JOIN·PK IN 축소를 관리합니다. 규칙을 수정하면 기존 튜닝은 STALE로 표시하고 재튜닝합니다. LOG·RAG의 테이블 구조는 현재 상태 스키마에 포함됩니다.</p></div>'''
