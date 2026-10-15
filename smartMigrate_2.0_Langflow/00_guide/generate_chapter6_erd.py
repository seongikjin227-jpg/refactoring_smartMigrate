"""Generate offline SVG/HTML and editable Mermaid from the Chapter 6 DDL.

Run: python smartMigrate_2.0_Langflow/00_guide/generate_chapter6_erd.py
Only reads the documented schema; does not connect to a database.
"""
from pathlib import Path
import html
import re
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
SOURCE = ROOT / '00_architecture_chapter6_oracle_ddl.md'
PREFIX = '00_architecture_chapter6_erd'


def esc(value):
    return html.escape(str(value), quote=True)


def text(x, y, value, size=17, color='#243447', weight='normal', mono=False):
    font = 'Consolas, monospace' if mono else 'Segoe UI, Malgun Gothic, sans-serif'
    return f'<text x="{x}" y="{y}" fill="{color}" font-size="{size}" font-weight="{weight}" font-family="{font}">{esc(value)}</text>'


def start(width, height, title, description, background='#ffffff'):
    return [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            f'<title id="title">{esc(title)}</title><desc id="desc">{esc(description)}</desc>',
            '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M 0 0 L 10 5 L 0 10 z" fill="context-stroke"/></marker></defs>',
            f'<rect width="{width}" height="{height}" fill="{background}"/>', text(55, 58, title, 30, weight='bold')]


def path(d, color='#5268bb', dashed=True):
    dash = ' stroke-dasharray="8 5"' if dashed else ''
    return f'<path d="{d}" fill="none" stroke="{color}" stroke-width="2.5"{dash} marker-end="url(#arrow)"/>'


def parse_schema():
    source = SOURCE.read_text(encoding='utf-8-sig')
    tables = {}
    for name, body in re.findall(r'CREATE TABLE (\w+)\s*\((.*?)\n\);', source, re.S):
        pk = re.search(r'PRIMARY KEY\s*\(([^)]+)\)', body)
        uq = re.search(r'UNIQUE\s*\(([^)]+)\)', body)
        primary = {x.strip() for x in pk[1].split(',')} if pk else set()
        unique = {x.strip() for x in uq[1].split(',')} if uq else set()
        columns = []
        for line in body.splitlines():
            match = re.match(r'\s*(\w+)\s+(NUMBER|VARCHAR2\(\d+\)|CHAR\(\d+\)|CLOB|TIMESTAMP)(.*)', line)
            if not match:
                continue
            col, dtype, rest = match.groups()
            default = re.search(r'\bDEFAULT\s+(\S+)', rest)
            columns.append(dict(name=col, type=dtype, pk=col in primary, uk=col in unique,
                                nn='NOT NULL' in rest or col in primary,
                                default=default[1].rstrip(',') if default and 'IDENTITY' not in rest else '',
                                identity='IDENTITY' in rest))
        tables[name] = columns
    if set(tables) != {'NEXT_MIG_INFO', 'NEXT_MIG_INFO_DTL', 'NEXT_SQL_INFO', 'NEXT_MIG_RAG_INFO', 'NEXT_MIG_LOG'}:
        raise ValueError('Unexpected table set; update the diagram layout and relationships.')
    return tables


def card(parts, name, cols, x, y, w, subtitle, color):
    h = 92 + len(cols) * 22
    parts += [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="white" stroke="#cbd5e1"/>',
              f'<rect x="{x}" y="{y}" width="{w}" height="58" rx="10" fill="{color}"/>',
              text(x+16, y+25, name, 22, '#fff', 'bold', True), text(x+16, y+46, subtitle, 15, '#fff'),
              text(x+16, y+75, 'KEY / NULL', 13), text(x+100, y+75, 'COLUMN', 13), text(x+352, y+75, 'ORACLE TYPE', 13)]
    for i, col in enumerate(cols):
        top = y + 82 + i * 22
        if i % 2 == 0:
            parts.append(f'<rect x="{x+1}" y="{top}" width="{w-2}" height="22" fill="#f1f5f9"/>')
        key = ('PK ' if col['pk'] else '') + ('UK ' if col['uk'] else '') + ('NN' if col['nn'] else '')
        parts += [text(x+16, top+16, key, 14, color, 'bold'), text(x+100, top+16, col['name'], 16, mono=True),
                  text(x+352, top+16, col['type'], 16, mono=True)]
    return h


def oracle_svg(tables):
    parts = start(1920, 1080, 'SmartMigrate · 데이터베이스 설계 (Oracle)', '16:9 PPT 장표. 5개 테이블의 전체 컬럼, PK/UK/NOT NULL과 논리 참조. 기본 DDL에 FK 없음.')
    parts.append(text(55, 94, '5개 테이블 · 전체 컬럼 · PK / UK / NN · 파란 점선: 논리 참조 · 주황 점선: 로그 대상 식별 · 기본 DDL에 FK 없음', 19, '#516176'))
    layout = {
        'NEXT_MIG_INFO': (70, 148, 540, 'DB Migration · 테이블 매핑 / 실행 / 검증', '#246a89'),
        'NEXT_MIG_INFO_DTL': (690, 148, 540, '컬럼 매핑 · (MAP_ID, MAP_DTL) 복합 PK', '#246a89'),
        'NEXT_SQL_INFO': (1310, 148, 540, 'SQL Conversion / Tuning / Formatting', '#6554a4'),
        'NEXT_MIG_LOG': (690, 421, 540, '공용 workflow / 실행 로그', '#9c6020'),
        'NEXT_MIG_RAG_INFO': (70, 700, 540, 'RAG Guide · 독립 참조 자료', '#357855'),
    }
    for name, spec in layout.items():
        card(parts, name, tables[name], *spec)

    def row_y(table, column):
        index = next(i for i, col in enumerate(tables[table]) if col['name'] == column)
        return layout[table][1] + 82 + index*22 + 16

    master = row_y('NEXT_MIG_INFO', 'MAP_ID')
    detail = row_y('NEXT_MIG_INFO_DTL', 'MAP_ID')
    prior = row_y('NEXT_MIG_INFO', 'PRIOR_MAP_ID')
    sql_seq = row_y('NEXT_SQL_INFO', 'SQL_SEQ')
    ref_seq = row_y('NEXT_SQL_INFO', 'REF_SEQ')
    log_id = row_y('NEXT_MIG_LOG', 'MAP_ID')
    parts += [path(f'M 690 {detail} H 640 V 138 H 625 V {master} H 610'),
              text(690, 129, 'MAP_ID → master · 1 : 0..N (논리 참조)', 16, '#246a89'),
              path(f'M 70 {prior} H 35 V {master} H 70'),
              text(70, 129, 'PRIOR_MAP_ID → MAP_ID · 선행 작업 0..1', 16, '#246a89'),
              path(f'M 1850 {ref_seq} H 1880 V {sql_seq} H 1850'),
              text(1310, 129, 'REF_SEQ → SQL_SEQ (UK) · 참고 SQL 0..1', 16, '#6554a4'),
              path(f'M 690 {log_id} H 660 V {master} H 610', '#b66b22'),
              path(f'M 1230 {log_id} H 1270 V {sql_seq} H 1310', '#b66b22')]

    parts.append('</svg>')
    return '\n'.join(parts)


def milvus_svg():
    parts = start(1850, 1060, 'SmartMigrate · Oracle → Milvus', 'Oracle 원천과 네 Milvus 검색 컬렉션의 동기화 흐름. ERD의 FK 관계가 아님.')
    parts += [text(55, 96, '검색용 복제 / 단계별 저장 흐름 · 화살표는 동기화 방향이며 FK 또는 보장된 카디널리티가 아님', 18),
              text(65, 144, 'Oracle 원천', 23, weight='bold'), text(960, 144, 'Milvus 검색 컬렉션', 23, weight='bold')]

    def box(x, y, w, title, lines, color):
        h = 65 + 27 * len(lines)
        parts.extend([f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="white" stroke="{color}" stroke-width="2"/>',
                      text(x+20, y+32, title, 22, color, 'bold', True)])
        for i, line in enumerate(lines):
            parts.append(text(x+20, y+64+27*i, line, 16))
    box(65, 180, 530, 'NEXT_MIG_RAG_INFO', ['PK: RAG_ID', 'category / rule_type / guidance / SQL 예시'], '#357855')
    box(65, 410, 530, 'NEXT_SQL_INFO', ['PK: SPACE_NM + SQL_ID · UK: SQL_SEQ', 'AS-IS SQL / 보정 SQL / 단계별 결과 SQL', 'REF_SEQ: Correct SQL 참고 대상'], '#6554a4')
    box(65, 770, 530, 'NEXT_MIG_INFO', ['PK: MAP_ID', 'MIG_SQL / VERIFY_SQL / 사용자 보정'], '#246a89')
    box(960, 180, 825, 'SM_RAG_RULES', ['rag_id / category / rule_type / use_yn / source_tables', 'guidance_text / source_sql / target_sql'], '#357855')
    box(960, 360, 825, 'SM_ASIS_SQL', ['sql_seq / space_nm / sql_id / tag_kind / target_table', 'fr_sql / edit_fr_sql (EDIT_FR_SQL 우선)', '실행 status와 TO-BE 결과 SQL은 저장하지 않음'], '#6554a4')
    box(960, 550, 825, 'SM_CORRECT_SQL_CONVERSION', ['sql_seq / space_nm / sql_id / status_conversion', 'correct_sql_kind / user_edited / tag_kind / target_table', 'source_sql / to_sql / bind_sql / test_sql'], '#6554a4')
    box(960, 770, 825, 'SM_CORRECT_SQL_MIGRATION', ['map_id / correct_sql_kind / fr_table / to_table / condition', 'mig_sql / verify_sql / user_edited / status'], '#246a89')
    parts += [path('M 595 240 H 960', '#357855', False), text(630, 222, '04_saveVectorDB.py', 18),
              path('M 595 460 H 770 V 420 H 960', '#6554a4', False), text(620, 402, 'AS-IS SQL 동기화', 17),
              path('M 595 505 H 760 V 610 H 960', '#6554a4', False), text(620, 654, 'save_correct_sql → sync_correct_sql', 16),
              text(620, 681, "USER_EDITED='Y' 단계 SQL", 16),
              path('M 595 831 H 960', '#246a89', False), text(620, 798, 'save_migration_* → sync_correct_sql', 16),
              text(620, 869, 'kind: MIG_SQL / VERIFY_SQL', 16),
              text(65, 958, '공통 필드: doc_id VARCHAR(256) PK · content · content_hash · is_active · updated_at · dense_vector · sparse_vector (선택)', 17),
              text(65, 993, 'REF_SEQ는 이미 SM_CORRECT_SQL_CONVERSION에 존재하는 SQL_SEQ만 지정 가능. 최신 실행 상태는 Oracle를 다시 조회.', 17),
              text(65, 1028, 'doc_id가 컬렉션 PK이며, sql_seq / map_id는 원천 식별 메타데이터. 단계별 Correct SQL 문서는 원천 한 건당 여러 건 가능.', 17), '</svg>']
    return '\n'.join(parts)


def mermaid(tables):
    lines = ['erDiagram', '    direction LR',
             '    NEXT_MIG_INFO |o..o{ NEXT_MIG_INFO_DTL : "MAP_ID logical reference - no FK"',
             '    NEXT_MIG_INFO |o..o{ NEXT_MIG_INFO : "PRIOR_MAP_ID to MAP_ID - no FK"',
             '    NEXT_SQL_INFO |o..o{ NEXT_SQL_INFO : "REF_SEQ to SQL_SEQ UK - no FK"']
    for name, cols in tables.items():
        lines.append(f'    {name} {{')
        for col in cols:
            keys = ' PK' if col['pk'] else (' UK' if col['uk'] else '')
            note = ['NOT NULL' if col['nn'] else 'NULL allowed']
            if col['identity']:
                note.append('IDENTITY')
            elif col['default']:
                note.append('DEFAULT ' + col['default'])
            lines.append(f'        {col["type"]} {col["name"]}{keys} "{", ".join(note)}"')
        lines.append('    }')
    return '\n'.join(lines) + '\n'


def features_svg():
    """A 16:9 presentation diagram; validation labels follow active executors."""
    parts = start(1920, 1080, 'SmartMigrate · 주요 기능', '자연어 요청을 일반 대화, Management, 작업 실행으로 구분. 템플릿 제공, 매핑룰 등록과 실행별 생성 SQL 및 검증 기준을 정리한 16:9 장표.', background='#ffffff')
    parts += [text(55, 99, '자연어로 작업을 관리하고, 매핑 기반 SQL 생성부터 실행·검증까지 연결', 26, '#516176')]

    def rect(x, y, w, h, fill='white', stroke='#d8e0eb', radius=14):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    rect(60, 126, 1800, 64, '#18334e', '#18334e')
    parts += [text(90, 167, '사용자 요청', 25, '#fff', 'bold'),
              text(300, 167, '요청 분류  →  일반 대화 / Management / 작업 실행', 26, '#fff', 'bold'),
              text(1190, 167, '사용자의 목적에 따라 세 경로로 연결', 22, '#9cd8ee'),
              path('M 960 190 V 212 H 320 V 235', '#246a89', False),
              path('M 960 212 H 605 V 395 H 320 V 410', '#246a89', False),
              path('M 960 212 H 1245 V 235', '#6554a4', False)]

    rect(60, 235, 520, 150, '#f3f8fb')
    rect(60, 410, 520, 515)
    rect(630, 235, 1230, 690)
    parts += [text(85, 278, '01  일반 대화', 30, '#246a89', 'bold'),
              text(85, 312, '기능 소개 · 사용 방법 · 요청 예시 안내', 21, '#516176'),
              text(85, 348, '매핑정의서 템플릿 제공', 24, '#246a89', 'bold'),
              text(85, 374, 'Super Agent가 XLSX 생성 · 다운로드 제공', 18, '#516176'),
              text(85, 450, '02  Management', 30, '#246a89', 'bold'),
              text(85, 480, '현황 조회 · 매핑룰 등록 · 검토 자료 관리', 20, '#516176'),
              text(655, 278, '03  작업 실행 요청', 30, '#6554a4', 'bold'),
              text(655, 310, '단건 / 도메인 / 전체 workflow  ·  대상과 선행 조건 확인 후 실행', 21, '#516176')]

    management = [
        ('현황·결과 조회', ['대시보드 · 진행 상태 · 잔여 작업', '생성 SQL · 검증 결과 · 실패 원인 / 로그']),
        ('매핑룰 등록·수정 / 작업 관리', ['테이블 / 컬럼 매핑 등록 · SQL 생성·적용', '사용 여부 · 우선순위 · 재시도 준비']),
        ('Correct SQL 보정', ['사람이 검토한 단계 SQL 저장', '완료 단계 반영 · 해당 SQL 검색 자료 동기화']),
        ('참고 규칙·사례 관리', ['RAG Guide 등록 / 수정 · VectorDB 동기화', '유사 실패 SQL 검색 · 참고 SQL 지정']),
    ]
    for i, (title, lines) in enumerate(management):
        y = 496 + i * 98
        rect(80, y, 480, 90, '#eef6fa', '#d8e7ef', 10)
        parts.append(text(100, y+27, title, 22, '#246a89', 'bold'))
        for j, line in enumerate(lines):
            parts.append(text(100, y+51+j*24, line, 18))
    parts.append(text(85, 908, '조회·변경과 실제 작업 실행은 별도 요청', 21, '#246a89', 'bold'))

    parts += [text(655, 348, '실행 단계', 20, '#516176', 'bold'),
              text(855, 348, '생성 SQL / 산출물', 20, '#516176', 'bold'),
              text(1355, 348, '실제 검증 / 완료 기준', 20, '#516176', 'bold')]
    rows = [
        (365, 155, '1', 'DB Migration', '이관 · 실행 · 검증', '#246a89',
         ['MIG_SQL · INSERT INTO … SELECT …', 'VERIFY_SQL · 원천 / 대상 건수 비교', 'VERIFY2_SQL · PK 기준 레코드 비교', '매핑 + DDL 기반 생성 · INSERT 실행'],
         ['전체 행 수 + 비교 컬럼 COUNT 대조', "SUCCESS_YN='Y' · 모든 DIFF_*=0", 'PK로 연결한 비교 대상 컬럼 값 대조', 'MISMATCH_CNT=0 → PASS']),
        (520, 140, '2', 'SQL Conversion', '변환 · 바인드 · 검증', '#6554a4',
         ['TO_SQL · 매핑 기반 TO-BE SQL', 'BIND_SQL 실행 → BIND_SET (최대 3개)', 'TEST_SQL · AS-IS / TO-BE 건수 비교', 'RAG SEARCH · 테이블 범위 일치 규칙 전부'],
         ['동일 바인드 케이스별 결과 건수 대조', 'CASE_NO · FROM_COUNT / TO_COUNT', '각 케이스: FROM_COUNT = TO_COUNT > 0']),
        (660, 140, '3', 'SQL Tuning', '규칙 적용 · 검증', '#357855',
         ['TUNED_TO_SQL · 규칙 기반 튜닝 SQL', 'TUNED_RESULT · 변경 사유 (설명)', 'TUNED_TEST_SQL · 튜닝 전후 건수 비교'],
         ['TO_SQL ↔ TUNED_TO_SQL 건수 대조', '동일 BIND_SET 사용 (최대 3개)', '각 케이스: FROM_COUNT = TO_COUNT > 0']),
        (800, 105, '4', 'SQL Formatting', '서식 정리 · 저장', '#9c6020',
         ['배치: 생성 SQL의 해당 컬럼 갱신', '단건: TUNED_TO_SQL → FORMATTED_SQL'],
         ['응답 항목·출력 형식과 빈 결과 확인', '서식 정리 결과 저장 · DB 결과 비교 없음']),
    ]
    for y, h, number, title, subtitle, color, generated, verified in rows:
        rect(647, y, 1196, h-8, '#f5f7fb', '#e6ebf2', 8)
        rect(647, y, 5, h-8, color, color, 2)
        parts += [text(667, y+27, f'STEP {number}', 16, color, 'bold'),
                  text(667, y+58, title, 22, color, 'bold'),
                  text(667, y+86, subtitle, 17, '#516176')]
        for i, line in enumerate(generated):
            parts.append(text(855, y+31+i*29, line, 21))
        for i, line in enumerate(verified):
            parts.append(text(1355, y+31+i*29, line, 20))

    rect(60, 950, 1800, 74, '#e7edf5', '#e7edf5')
    parts += [text(85, 980, '공통 기반', 22, '#18334e', 'bold'),
              text(255, 980, 'Oracle: 매핑 · SQL · 상태 · 로그 저장', 22),
              text(825, 980, 'Milvus: 참고 규칙 · Correct SQL 검색', 22),
              text(1370, 980, 'DB 상태 기반 재개 · 실패 분석', 22),
              text(85, 1009, '검증 범위: Conversion / Tuning은 SELECT 건수 비교 · 비SELECT는 생략 · Tuning 변경 없음도 생략', 20, '#516176'),
              text(60, 1057, '전체 실행 순서  Migration → Conversion → Tuning → Formatting  |  유사 SQL 검색·일괄 반영: Conversion 전용', 20, '#516176'), '</svg>']
    return '\n'.join(parts)


def scenarios_svg():
    """Two chat demonstration stories on one 1920x1080 PowerPoint slide."""
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="1920" height="1080" viewBox="0 0 1920 1080" role="img" aria-labelledby="title desc">',
             '<title id="title">SmartMigrate · 두 가지 채팅 시연 시나리오</title>',
             '<desc id="desc">기존 SQL 보정과 새 매핑 Migration의 두 흐름. 상세 채팅 문장은 사용자 가이드 참조.</desc>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#8292a5"/></marker></defs>',
             '<rect width="1920" height="1080" fill="white"/>',
             text(60, 76, 'SmartMigrate · 채팅으로 준비하고, 보정하고, 실행하기', 36, '#18334e', 'bold'),
             text(60, 121, '두 가지 시연 시나리오  |  30~40분 상세 시연  |  권장 진행 순서: 01 DB Migration → 02 SQL Conversion / Tuning', 23, '#516176')]

    def panel(y, number, heading, subtitle, color, stages, note):
        parts.append(f'<rect x="60" y="{y}" width="1800" height="350" rx="18" fill="#f8fafc" stroke="#dbe3ec"/>')
        parts.append(f'<rect x="88" y="{y+24}" width="54" height="44" rx="12" fill="{color}"/>')
        parts.append(text(101, y+54, number, 25, '#ffffff', 'bold'))
        parts.append(text(162, y+56, heading, 29, color, 'bold'))
        parts.append(text(162, y+91, subtitle, 20, '#516176'))
        for index, (title_lines, detail_lines) in enumerate(stages):
            x, top = 100 + index*285, y+122
            parts.append(f'<rect x="{x}" y="{top}" width="250" height="138" rx="13" fill="white" stroke="{color}" stroke-width="1.5"/>')
            for line_index, label in enumerate(title_lines):
                parts.append(text(x+18, top+34+line_index*29, label, 24, color, 'bold'))
            for line_index, label in enumerate(detail_lines):
                parts.append(text(x+18, top+94+line_index*26, label, 19, '#516176'))
            if index < len(stages)-1:
                parts.append(f'<path d="M{x+258} {top+65} H{x+277}" stroke="#8292a5" stroke-width="2.5" marker-end="url(#arrow)"/>')
        parts.append(text(100, y+309, note, 21, color))

    panel(171, '01', 'DB Migration · 매핑 등록과 이관 검증', '핵심: 템플릿 준비 → 실제 매핑 반영 → 이관 → 검증 실패 분석과 보정', '#6a4e9b', [
        (['일반 대화', '템플릿 다운로드'], ['업무 순서 안내', 'AI 생성 XLSX 검토']),
        (['파일 작성·등록', '매핑룰 조회'], ['테이블 + 컬럼 상세', '저장 값과 입력 비교']),
        (['작업 관리', 'Migration 실행'], ['대상 · 우선순위 관리', '범위를 정해 실행']),
        (['진행·결과', '생성 SQL 조회'], ['건수 · 레코드 검증', 'MIG / VERIFY SQL']),
        (['실패 분석', 'Correct SQL 입력'], ['별도 검증 실패 사례', '확인한 SQL 저장']),
        (['재실행 요청', '검증 결과 확인'], ['상태에 맞는 단계 재개', '최종 결과와 로그']),
    ], 'Migration Correct SQL: 확인한 MIG_SQL → 검증부터 재개   ·   검증 완료 VERIFY_SQL → PASS   |   유사 SQL REF_SEQ는 Conversion 전용')
    panel(547, '02', 'SQL Conversion / Tuning · 보정과 재실행', '핵심: 현황 확인 → SQL 조회·실패 분석 → 보정·참조 등록 → 재실행 → 변환 완료·튜닝 내역 확인', '#246a89', [
        (['대시보드', '현황 확인'], ['전체 작업 현황', '실패한 작업 목록']),
        (['SQL 조회', '실패 분석 요청'], ['AS-IS · TO-BE SQL', '실패 단계 · 원인']),
        (['Correct SQL', '입력'], ['TOBE / BIND / TEST', '저장 후 자동 동기화']),
        (['유사 SQL 검색', 'REF_SEQ 자동 등록'], ['AS-IS 유사도 80% 초과', '동일 실패 단계 후보']),
        (['재실행 요청', 'Conversion · Tuning'], ['참고 SQL로 재개', '변환 후 튜닝 진행']),
        (['Conversion PASS', '튜닝 SQL·내역 조회'], ['TUNED_TO_SQL', 'TUNED_RESULT']),
    ], '자동 참조 대상: TOBE → FAIL-TOBE   ·   BIND → FAIL-BIND   ·   TEST → FAIL-TEST   |   REF_SEQ 등록과 실제 실행은 별도')
    parts += [text(60, 967, '시연 준비  실제 대상 번호 · 실패 단계별 작업 · 검토한 SQL · 매핑 상세 행 · 검색 자료 동기화', 23, '#18334e', 'bold'),
              text(60, 1010, '진행 조회는 실행 중 별도 채팅에서 확인  |  마무리: 최근 전체 결과 → 남은 전체 워크플로우 → 선행 조건 안내', 21, '#516176'), '</svg>']
    return '\n'.join(parts)


def logging_svg():
    """Logging architecture and source-based code excerpts on a 16:9 slide."""
    parts = start(1920, 1080, 'SmartMigrate · 공통 DB 로깅 아키텍처',
                  '00A에서 공통 DB 핸들러 등록. 컴포넌트는 Python logging과 extra로 이벤트 전달. 핸들러가 NEXT_MIG_LOG에 저장.', background='#ffffff')
    parts.append(text(55, 105, '로깅용 DB 설정은 시작 단계에 모으고, 각 컴포넌트는 메시지와 작업 이벤트만 전달', 27, '#516176'))

    def box(x, y, w, h, fill='#f8fafc', color='#d8e0eb'):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{color}"/>')

    stages = [
        (60, 400, '01  시작 · 00A', '#246a89', ['DB 접속 정보 · system_schema 설정', 'SmartMigrateDBHandler 등록']),
        (530, 400, '02  각 컴포넌트', '#6554a4', ['Python 표준 logging 호출', '메시지 + extra.workflow_log 전달']),
        (1000, 400, '03  공통 DB 핸들러', '#357855', ['LogRecord → 이벤트·DB 컬럼 변환', 'emit() → _event() → _insert_row()']),
        (1470, 390, '04  Oracle 로그 저장', '#9c6020', ['NEXT_MIG_LOG · INSERT / COMMIT', 'SQL·프롬프트는 CLOB으로 저장']),
    ]
    for x, width, heading, color, lines in stages:
        box(x, 162, width, 162, '#f8fafc', color)
        parts.append(text(x+20, 202, heading, 26, color, 'bold'))
        for i, line in enumerate(lines):
            parts.append(text(x+20, 246+i*33, line, 20, '#243447'))
        if x != 1470:
            parts.append(path(f'M {x+width+9} 242 H {x+width+59}', '#8292a5', False))
    parts.append(text(60, 373, '코드로 보는 역할 분리', 26, '#18334e', 'bold'))

    def code_card(x, width, title, subtitle, lines, color):
        box(x, 401, width, 478, '#f5f7fb')
        parts.append(text(x+22, 438, title, 25, color, 'bold'))
        parts.append(text(x+22, 469, subtitle, 18, '#516176'))
        for i, line in enumerate(lines):
            parts.append(text(x+22, 510+i*23, line, 18, '#243447', mono=True))

    code_card(60, 500, '00A · 핸들러 준비', '워크플로우 시작 시 등록 · 기존 공통 핸들러 교체', [
        'logger = logging.getLogger(',
        '    "smartmigrate.workflow"',
        ')',
        'logger.setLevel(logging.DEBUG)',
        'logger.propagate = False',
        '',
        'handler = SmartMigrateDBHandler(',
        '    self._db_config()',
        ')',
        'logger.addHandler(handler)',
        '',
        '# 핸들러가 DB 연결을 보유',
        '# 각 컴포넌트의 로깅 설정 중복 제거',
    ], '#246a89')
    code_card(590, 680, '각 컴포넌트 · 이벤트 발행', '같은 이름의 Logger 호출 · 로깅용 DB 접속/INSERT 불필요', [
        'logger = logging.getLogger("smartmigrate.workflow")',
        'logger.info(',
        '    "TO-BE SQL 생성 완료",',
        '    extra={"workflow_log": [',
        '        "SQL_SEQ = 42",    # 작업 식별자',
        '        "SQL_CONVERSION", # 도메인',
        '        "TOBE_SQL",       # 로그 종류',
        '        "INFO",           # 로그 수준',
        '        "GENERATE_TOBE",  # 처리 단계',
        '        "SUCCESS",        # 상태',
        '        0,                # 재시도 횟수',
        '        generated_sql,    # 상세 본문 (선택)',
        '    ]}',
        ')',
    ], '#6554a4')
    code_card(1300, 560, 'SmartMigrateDBHandler · DB 저장', '실제 구현의 핵심 발췌 · row 필드 매핑은 축약', [
        'def emit(self, record):',
        '    event = self._event(record)',
        '    row = {',
        '        # 식별자·단계·상태 등 필드 매핑',
        '        # message ← record.getMessage()',
        '        # generate_sql ← event 상세 본문',
        '    }',
        '    self._insert_row(row)',
        '',
        '# _insert_row() 내부',
        'cursor.setinputsizes(',
        '    generate_sql=oracledb.DB_TYPE_CLOB',
        ')',
        'cursor.execute(INSERT_SQL, row)',
        'self.connection.commit()',
    ], '#357855')
    box(60, 909, 1800, 112, '#e7edf5', '#e7edf5')
    parts += [text(85, 944, 'NEXT_MIG_LOG', 24, '#18334e', 'bold'),
              text(340, 944, '작업 식별자 · 도메인 · 로그 종류 · 단계 · 상태 · 재시도 횟수', 23),
              text(85, 985, 'MESSAGE ← 로그 메시지', 22),
              text(540, 985, 'GENERATE_SQL (CLOB) ← SQL / 프롬프트 등 상세 본문', 22),
              text(1420, 985, 'LOG_ID · 시각 자동 구성', 22),
              text(60, 1057, '같은 Python 프로세스의 공통 Logger / Handler 사용  |  Python 표준 logging + extra  |  DB 연결·컬럼 매핑·저장을 한곳에서 처리', 21, '#516176'), '</svg>']
    return '\n'.join(parts)


def loop_svg():
    """Square dynamic queue graphic for a small PowerPoint inset."""
    parts = ['<svg xmlns="http://www.w3.org/2000/svg" width="900" height="900" viewBox="0 0 900 900" role="img" aria-labelledby="title desc">',
             '<title id="title">18B · 동적 큐잉</title>',
             '<desc id="desc">작업 사이 DB 재조회, 중복 제외, 단계와 우선순위에 따른 남은 실행 큐 편입.</desc>',
             '<defs><marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 L10 5 L0 10 Z" fill="#8292a5"/></marker></defs>',
             '<rect width="900" height="900" fill="white"/>',
             text(42, 70, '18B · 동적 큐잉', 42, '#18334e', 'bold'),
             text(42, 115, '새 실행 대상을 남은 큐에 자동 편입', 28, '#516176')]

    def box(x, y, w, h, fill, color, label, size=28):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="{fill}" stroke="{color}" stroke-width="2"/>')
        parts.append(text(x+20, y+(32 if h == 83 else h/2+size/3), label, size, color, 'bold'))

    parts += [text(42, 160, '18A 작업 목록 → list[Data] → ctx 실행 큐', 26, '#18334e', 'bold'),
              '<rect x="42" y="178" width="816" height="132" rx="14" fill="#eef2f7"/>',
              text(62, 214, 'data_list = self._validate_data(self.data)', 22, mono=True),
              text(62, 248, 'self.update_ctx({f"{self._id}_data": data_list,', 22, mono=True),
              text(62, 282, '                 f"{self._id}_index": 0})', 22, mono=True)]

    for y, title, detail, color in [
        (338, '① DB 재조회', '다음 작업 실행 전, 현재 실행 가능 대상 확인', '#246a89'),
        (439, '② 중복 제외', '남은 큐의 같은 단계·작업 번호와 비교', '#6554a4'),
        (540, '③ 큐에 추가', '단계 → 우선순위 순으로 미실행 구간에 삽입', '#357855'),
    ]:
        box(42, y, 816, 83, '#f5f7fb', color, title, 29)
        parts.append(text(64, y+66, detail, 23, '#516176'))
        if y != 540:
            parts.append(path(f'M 450 {y+87} V {y+96}', '#8292a5', False))
    parts.append(text(42, 678, '예시 · Conversion 직전 새 Migration 발견', 26, '#18334e', 'bold'))
    box(42, 710, 235, 62, '#e7edf5', '#516176', '완료한 작업', 26)
    box(317, 710, 235, 62, '#e3f2f8', '#246a89', '새 Migration', 26)
    box(592, 710, 266, 62, '#eeeaf7', '#6554a4', 'Conversion', 26)
    parts += [path('M 284 741 H 306', '#8292a5', False), path('M 560 741 H 580', '#8292a5', False),
              text(42, 811, '완료 구간 유지', 23, '#516176'),
              text(317, 811, '↑ 다음 실행 위치부터 추가', 23, '#246a89', 'bold'),
              text(42, 864, '작업 사이 재조회 · 큐가 소진되면 종료', 24, '#516176'), '</svg>']
    return '\n'.join(parts)


def rag_milvus_svg():
    """Connection and retrieval code beside four collection-to-executor routes."""
    parts = start(1920, 1080, 'SmartMigrate · RAG / Milvus 코드와 업무별 조회',
                  '왼쪽은 Milvus 접속·동기화·검색 코드, 오른쪽은 DB Migration, SQL Conversion, SQL Tuning의 조회 경로와 공통 General 규칙.', background='#ffffff')
    parts.append(text(55, 102, 'Milvus 핵심 코드 + 조회 흐름도  |  무엇을 입력하고, 어떤 컬렉션과 비교하며, 무엇을 프롬프트에 반영하는가', 25, '#516176'))
    parts += [text(60, 155, '01  접속 · 동기화 · 검색 코드', 27, '#18334e', 'bold'),
              text(750, 155, '02  입력 → 조회·비교 대상 → 선택 결과', 27, '#18334e', 'bold')]

    def box(x, y, w, h, fill='#f5f7fb', color='#d8e0eb'):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="13" fill="{fill}" stroke="{color}"/>')

    def code_card(y, h, heading, subtitle, lines, color):
        box(60, y, 640, h)
        parts.append(text(82, y+35, heading, 24, color, 'bold'))
        parts.append(text(82, y+66, subtitle, 18, '#516176'))
        for i, line in enumerate(lines):
            parts.append(text(82, y+104+i*23, line, 18, '#243447', mono=True))

    code_card(180, 243, 'MilvusClient · DB 접속', '_milvus_config() → _milvus_client()', [
        'from pymilvus import MilvusClient',
        'client = MilvusClient(',
        '    uri=cfg["uri"], db_name=cfg["db_name"],',
        '    user=cfg["username"], password=cfg["password"],',
        '    timeout=10,',
        ')',
    ], '#246a89')
    code_card(438, 204, '검색 자료 동기화', '_sync_collection() · 변경분만 임베딩·upsert', [
        'vectors = self._embed_texts(texts, embed_config)',
        'entities = [{**row, "dense_vector": vector}',
        '            for row, vector in zip(rows, vectors)]',
        'client.upsert(collection_name=name, data=entities)',
    ], '#357855')
    code_card(657, 288, '벡터 검색 · COSINE 유사도', '유사 AS-IS 검색 예시 · 저장 벡터 재사용 가능', [
        'vector = self._embed_texts([text], embed_config)[0]',
        'hits = client.search(',
        '    collection_name=cfg["asis_sql_collection"],',
        '    data=[vector], anns_field="dense_vector",',
        '    filter="is_active == true", limit=20,',
        '    search_params={"metric_type": "COSINE"},',
        ')',
        '# query() / query_iterator(): 벡터 순위 없는 조건 조회',
    ], '#6554a4')

    # ERD 형태의 작은 테이블을 연결해 입력 / 컬렉션 / 결과를 구분한다.
    parts += [text(750, 188, '업무 · 입력', 18, '#516176'),
              text(1080, 188, 'Milvus 컬렉션 · 조회 필드', 18, '#516176'),
              text(1620, 188, '방식 · 반영 결과', 18, '#516176')]

    def entity(x, y, w, heading, fields, color, size=21):
        box(x, y, w, 100, '#ffffff', color)
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="32" rx="8" fill="{color}"/>')
        parts.append(text(x+12, y+23, heading, size, '#ffffff', 'bold'))
        for i, field in enumerate(fields):
            if i % 2 == 0:
                parts.append(f'<rect x="{x+1}" y="{y+37+i*27}" width="{w-2}" height="27" fill="#f1f5f9"/>')
            parts.append(text(x+12, y+57+i*27, field, 18, '#243447'))

    # 같은 업무는 한 배경으로 묶고 번호 대신 외부 사용자용 업무명을 사용한다.
    box(738, 319, 1134, 376, '#f2eff8', '#d9d1eb')
    rows = [
        (205, '#9c6020', 'DB Migration',
         ['FR_TABLE / TO_TABLE', '조건 · 컬럼 매핑 · 유형'],
         'SM_CORRECT_SQL_MIGRATION', ['매핑 정보 → dense_vector', 'mig_sql / verify_sql'],
         'COSINE · Top-K', ['같은 SQL 종류만 검색', '이관·검증 생성 힌트']),
        (331, '#6554a4', 'SQL Conversion',
         ['SEARCH · TARGET_TABLE', 'AS-IS 테이블 범위'],
         'SM_RAG_RULES', ['RULE_TYPE = SEARCH', 'SOURCE_TABLES / SOURCE_SQL / TARGET_SQL'],
         '테이블 범위 필터', ['겹치는 사례 전부 반영', '벡터 Top-K 미사용']),
        (457, '#6554a4', 'SQL Conversion',
         ['Correct · AS-IS SQL', 'REF_SEQ (선택)'],
         'SM_CORRECT_SQL_CONVERSION', ['source_sql / dense_vector', 'to_sql / bind_sql / test_sql'],
         'COSINE / REF_SEQ', ['REF_SEQ 있으면 지정 조회', '단계별 Correct SQL 힌트']),
        (583, '#6554a4', 'SQL Conversion',
         ['유사 SQL · AS-IS SQL', 'EDIT_FR_SQL 우선'],
         'SM_ASIS_SQL', ['fr_sql / edit_fr_sql / dense_vector', 'sql_seq / target_table'],
         'COSINE', ['유사 작업 후보 조회', 'Oracle 최신 상태 확인']),
        (709, '#357855', 'SQL Tuning',
         ['SEARCH · TO_SQL 블록', 'MAIN / SUBQUERY'],
         'SM_RAG_RULES', ['SEARCH · SOURCE_SQL → dense_vector', 'GUIDANCE_TEXT / TARGET_SQL → 프롬프트'],
         'COSINE · Top-K', ['범위 확인 · 블록별 선택', '튜닝 사례·가이드 반영']),
        (835, '#516c86', '공통 · GENERAL',
         ['SQL Conversion / Tuning', '현재 테이블 범위'],
         'SM_RAG_RULES', ['RULE_TYPE = GENERAL', 'SOURCE_TABLES / GUIDANCE_TEXT'],
         '조건 조회', ['적용 가능한 규칙 반영', '유사도 순위 없음']),
    ]
    for y, color, source, source_fields, collection, fields, method, result in rows:
        entity(750, y, 265, source, source_fields, color, 20)
        entity(1080, y, 480, collection, fields, color, 21)
        entity(1620, y, 240, method, result, color, 20)
        parts += [path(f'M 1023 {y+51} H 1071', color, False),
                  path(f'M 1568 {y+51} H 1611', color, False)]
    parts += [text(60, 994, 'GENERAL의 SOURCE_TABLES: 공란 → 공통 적용  /  값 있음 → 현재 테이블 범위와 겹침 확인', 23, '#18334e', 'bold'),
              text(60, 1038, '화살표는 조회·검색 흐름 (FK 관계 아님)  |  COSINE은 입력과 저장 데이터의 dense_vector 비교  |  SQL Formatting은 Milvus 직접 조회 없음', 20, '#516176'), '</svg>']
    return '\n'.join(parts)


def correct_sql_svg():
    """Correct SQL state transitions, resumed generation, and similar-job references."""
    parts = start(1920, 1080, 'SmartMigrate · Correct SQL 입력과 후속 실행',
                  'DB Migration과 SQL Conversion의 보정 SQL 저장 상태, 다음 실행 생성 단계, 유사 실패 작업 REF_SEQ 자동 등록 흐름.', background='#ffffff')
    parts.append(text(55, 103, '확인한 SQL을 저장해 해당 단계를 완료 처리하고, 다음 실행은 저장된 상태에 맞는 단계부터 재개', 25, '#516176'))

    def box(x, y, w, h, fill='#f8fafc', color='#d8e0eb'):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="12" fill="{fill}" stroke="{color}"/>')

    parts += [text(60, 156, '01  SQL을 입력한 기준 작업', 27, '#18334e', 'bold'),
              text(1205, 156, '02  유사 후보 작업 · SQL Conversion 전용', 26, '#6554a4', 'bold'),
              text(80, 194, '입력 SQL · 확인 의미', 19, '#516176'),
              text(370, 194, '저장 후 상태', 19, '#516176'),
              text(650, 194, '다음 실행 · Generate / Reuse', 22, '#516176')]
    box(60, 212, 1100, 248, '#f6f9fc', '#cbdce6')
    box(60, 480, 1100, 419, '#f6f4fb', '#ded6ee')
    parts += [text(80, 247, 'DB Migration', 27, '#246a89', 'bold'),
              text(80, 515, 'SQL Conversion', 27, '#6554a4', 'bold')]

    def transition(y, label, meaning, status, steps, color):
        box(80, y, 250, 90, '#ffffff', color)
        box(370, y, 235, 90, '#ffffff', color)
        box(650, y, 490, 90, '#ffffff', color)
        parts.extend([text(95, y+34, label, 20 if label == 'BIND_SQL + BIND_SET' else 25, color, 'bold'),
                  text(95, y+66, meaning, 18, '#516176'),
                  text(384, y+52, status, 22, color, 'bold'),
                  path(f'M 337 {y+44} H 361', color, False),
                  path(f'M 612 {y+44} H 641', color, False)])
        for i, line in enumerate(steps):
            primary = line.startswith(('LLM:', 'System:', 'Migration Complete'))
            baseline = y+31+i*31 if len(steps) <= 2 else y+25+i*27
            parts.append(text(665, baseline, line, 25 if primary else 21,
                              color if primary else '#516176', 'bold' if primary else 'normal'))

    transition(263, 'MIG_SQL', 'INSERT 완료를 사람이 확인', 'FAIL-TEST', [
        'LLM: Generate VERIFY_SQL',
        'System: Build VERIFY2_SQL',
    ], '#246a89')
    transition(358, 'VERIFY_SQL', '검증 완료를 사람이 확인', 'PASS', [
        'Migration Complete',
        '추가 SQL 생성 없음',
    ], '#246a89')
    transition(535, 'TO_SQL (TOBE)', '변환 SQL 확인', 'FAIL-BIND', [
        'LLM: Generate BIND_SQL',
        'System: Get BIND_SET',
        'LLM: Generate TEST_SQL',
    ], '#6554a4')
    transition(650, 'BIND_SQL + BIND_SET', '두 값을 함께 업데이트 · 필수', 'FAIL-TEST', [
        'LLM: Generate TEST_SQL',
        'Reuse: BIND_SQL + BIND_SET',
    ], '#6554a4')
    transition(765, 'TEST_SQL', 'Conversion 검증 완료 확인', 'PASS-CONVERSION', [
        'LLM: Generate TUNED_TO_SQL',
        'Save: TUNED_RESULT (튜닝 내역)',
    ], '#6554a4')

    def flow(y, h, title, lines, color='#6554a4'):
        box(1205, y, 655, h, '#f6f4fb', '#ded6ee')
        parts.append(text(1225, y+34, title, 25, color, 'bold'))
        for i, line in enumerate(lines):
            parts.append(text(1225, y+67+i*28, line, 21, '#243447'))

    flow(212, 95, 'Correct SQL 저장 → 해당 종류 VectorDB 동기화', ['예: SQL_SEQ = 42 · TO_SQL / BIND_SQL / TEST_SQL'])
    flow(335, 109, '현재 AS-IS SQL ↔ 후보 AS-IS SQL', ['COSINE 검색 · 유사도가 엄격히 80% 초과인 후보'])
    box(1205, 472, 655, 171, '#f6f4fb', '#ded6ee')
    parts.append(text(1225, 505, '저장한 SQL 종류와 후보 실패 단계 일치', 25, '#6554a4', 'bold'))
    for i, (kind, status) in enumerate([('TO_SQL', 'FAIL-TOBE'), ('BIND_SQL', 'FAIL-BIND'), ('TEST_SQL', 'FAIL-TEST')]):
        y = 520+i*35
        parts.append(f'<rect x="1225" y="{y}" width="615" height="31" fill="{ "#e9e4f4" if i%2==0 else "#ffffff" }"/>')
        parts += [text(1240, y+23, kind, 21, '#243447', mono=True),
                  text(1450, y+23, '→', 21, '#6554a4'),
                  text(1500, y+23, status, 21, '#243447', mono=True)]
    flow(671, 116, '일치 후보에 REF_SEQ = 42 자동 등록', ['RETRY_COUNT = 0 · 후보의 FAIL 상태는 유지', '기준 작업 자신 제외 · 후보별 추가 승인 없이 반영'])
    flow(815, 102, '별도 재실행 요청 → 실패 단계에서 생성 재개', ['REF_SEQ의 Correct SQL을 생성 힌트로 사용'])
    for y1, y2 in [(307,335), (444,472), (643,671), (787,815)]:
        parts.append(path(f'M 1532 {y1+5} V {y2-6}', '#6554a4', False))
    parts += [text(60, 947, '저장 공통: USER_EDITED = Y · RETRY_COUNT = 0 · 저장한 SQL 한 종류만 VectorDB 동기화', 23, '#18334e', 'bold'),
              text(60, 994, '저장·참조 등록과 실행은 별도  |  DB 상태로 재개 단계 결정  |  Migration은 유사 작업 자동 REF_SEQ 적용 없음', 21, '#516176'),
              text(60, 1038, '좌측 상태 = 보정한 기준 작업의 새 상태  /  우측 FAIL 단계 = 참조 SQL을 연결할 유사 후보의 현재 상태', 21, '#516176'), '</svg>']
    return '\n'.join(parts)


def inline_svg(svg, prefix):
    """Namespace accessibility and arrow IDs when several diagrams share a page."""
    return (svg.replace('id="title"', f'id="{prefix}-title"')
            .replace('id="desc"', f'id="{prefix}-desc"')
            .replace('aria-labelledby="title desc"', f'aria-labelledby="{prefix}-title {prefix}-desc"')
            .replace('id="arrow"', f'id="{prefix}-arrow"')
            .replace('url(#arrow)', f'url(#{prefix}-arrow)'))


def main():
    tables = parse_schema()
    oracle, milvus, mmd = oracle_svg(tables), milvus_svg(), mermaid(tables)
    features = features_svg()
    scenarios = scenarios_svg()
    logging_diagram = logging_svg()
    loop_diagram = loop_svg()
    rag_milvus_diagram = rag_milvus_svg()
    correct_sql_diagram = correct_sql_svg()
    ET.fromstring(correct_sql_diagram)
    (ROOT / '00_correct_sql_flow.svg').write_text(correct_sql_diagram, encoding='utf-8')
    ET.fromstring(rag_milvus_diagram)
    (ROOT / '00_rag_milvus_architecture.svg').write_text(rag_milvus_diagram, encoding='utf-8')
    ET.fromstring(loop_diagram)
    (ROOT / '00_loop_architecture.svg').write_text(loop_diagram, encoding='utf-8')
    ET.fromstring(logging_diagram)
    (ROOT / '00_logging_architecture.svg').write_text(logging_diagram, encoding='utf-8')
    ET.fromstring(scenarios)
    (ROOT / '00_demo_scenarios.svg').write_text(scenarios, encoding='utf-8')
    ET.fromstring(features)
    (ROOT / '00_agent_features.svg').write_text(features, encoding='utf-8')
    for suffix, contents in [('.svg', oracle), ('_milvus.svg', milvus), ('.mmd', mmd)]:
        if suffix.endswith('.svg'):
            ET.fromstring(contents)
        (ROOT / (PREFIX+suffix)).write_text(contents, encoding='utf-8')
    overview = '''flowchart LR
    MIG["NEXT_MIG_INFO<br/>MAP_ID PK"] -. "MAP_ID 논리 참조 · FK 없음" .-> DTL["NEXT_MIG_INFO_DTL<br/>MAP_ID + MAP_DTL PK"]
    SQL["NEXT_SQL_INFO<br/>SPACE_NM + SQL_ID PK<br/>SQL_SEQ UK"]
    LOG["NEXT_MIG_LOG<br/>LOG_ID PK<br/>MAP_ID VARCHAR2 + MIG_KIND"]
    MIG -. "DB_MIGRATION 대상 식별" .-> LOG
    SQL -. "SQL_SEQ = 값 대상 식별" .-> LOG
    RAG["NEXT_MIG_RAG_INFO<br/>RAG_ID PK / IDENTITY"] --> RULES["Milvus · SM_RAG_RULES"]
    SQL --> ASIS["Milvus · SM_ASIS_SQL"]
    SQL --> CORRECT["Milvus · SM_CORRECT_SQL_CONVERSION"]
    MIG --> MCORRECT["Milvus · SM_CORRECT_SQL_MIGRATION"]
    SQL -. "REF_SEQ 지정 시 기존 문서 존재 검증" .-> CORRECT
'''
    md = f'''# Chapter 6 ERD — 개발·설계 검토용

기준: [Oracle & Milvus DDL](00_architecture_chapter6_oracle_ddl.md). 운영 DB 실측이 아닌 문서 기준이다.

- [전체 컬럼 Oracle ERD 그림]({PREFIX}.svg)
- [Oracle → Milvus 동기화 그림]({PREFIX}_milvus.svg)
- [확대·축소 가능한 오프라인 뷰어]({PREFIX}.html)
- [수정 가능한 Mermaid 원본]({PREFIX}.mmd)
- [주요 기능 한 장표 — Management / 작업 실행](00_agent_features.svg)

## Oracle 테이블 전체 컬럼

![Oracle ERD]({PREFIX}.svg)

PK는 기본키, UK는 UNIQUE, NN은 NOT NULL이다. 복합 PK로 표시된 컬럼은 함께 하나의 키를 이룬다.
기본 DDL에는 FOREIGN KEY가 **없다**. 파란 점선은 문서에 설명된 논리 참조이고,
주황 점선은 `MIG_KIND` 및 문자열 로그 대상을 해석하는 조건부 연결이다. 참조 컬럼을 FK로 표시하지 않았다.

| 참조 컬럼 | 대상 | 의도한 관계 | DB 강제 여부 |
|---|---|---|---|
| `NEXT_MIG_INFO_DTL.MAP_ID` | `NEXT_MIG_INFO.MAP_ID` | master 1 : detail 0..N | FK 없음. 운영 정책에 따라 별도 추가 가능 |
| `NEXT_MIG_INFO.PRIOR_MAP_ID` | 같은 테이블의 `MAP_ID` | 작업당 선행 0..1, 선행 작업당 후속 0..N | FK 없음, 순환 방지 제약도 문서에 없음 |
| `NEXT_SQL_INFO.REF_SEQ` | 같은 테이블의 `SQL_SEQ` (UK) | SQL당 참조 0..1, 대상 SQL당 피참조 0..N | FK 없음. 자기 자신 참조만 CHECK로 금지 |
| `NEXT_MIG_LOG.MAP_ID` + `MIG_KIND` | Migration `MAP_ID` / SQL `SQL_SEQ` | 해당 작업에 로그 0..N, WORKFLOW 등은 작업 대상이 없을 수 있음 | 문자열 기반 대상 식별. 일반 FK 관계 아님 |

`NOT NULL`은 참조 값의 존재만 보장한다. 참조 대상 row의 존재를 보장하지 않으므로 고아 detail이나 잘못된 참조 값이 저장될 수 있다.
Mermaid는 이 사실을 반영해 논리 참조 대상 쪽을 `0..1`로 표시한다. 의도한 master–detail 관계의 master는 1이다.
Mermaid의 점선은 비식별 관계 기호이므로 **점선 자체가 FK 부재를 뜻하지 않는다**. 각 라벨의 `no FK`와 위 설명이 FK 부재를 명시한다.
master–detail은 `MAP_ID`가 detail PK에 포함되는 논리적 식별 관계지만, 이 문서의 점선은 전체 논리 참조를 구분하기 위한 표현이다.

`NEXT_SQL_INFO`의 PK는 `(SPACE_NM, SQL_ID)`이며 `SQL_SEQ`는 NOT NULL UNIQUE 키다.
`NEXT_MIG_LOG.MAP_ID`는 VARCHAR2(200)이므로 NUMBER 키와 단순 동등 조인하면 안 된다.
DB Migration은 MAP_ID, SQL Conversion/Tuning은 `SQL_SEQ = 값`, Formatting은 `SQL_SEQ = 값` 또는 `MAP_ID = 값`을 사용한다.
`FR_TABLE`, `TARGET_TABLE`, `SOURCE_TABLES`는 table scope 메타데이터다. 이를 일반 FK나 보장된 N:M 관계로 표현하지 않는다.

```mermaid
{mmd}```

## Oracle / Milvus 연결 개요

실선은 검색용 동기화 방향이다. Milvus 컬렉션은 Oracle의 FK 대상 테이블이 아니다.
전체 전용 metadata와 공통 필드는 SVG 및 원문 6.8을 참조한다.

![Milvus synchronization]({PREFIX}_milvus.svg)

```mermaid
{overview}```

Milvus의 PK는 `doc_id`다. `sql_seq`, `map_id`는 Oracle 원천 식별 메타데이터이며 컬렉션 PK가 아니다.
`REF_SEQ` 지정 시 대상 `SQL_SEQ`가 이미 `SM_CORRECT_SQL_CONVERSION`에 있는지 검증한다.
단계별 Correct SQL 문서는 원천 한 건에서 여러 건 생성될 수 있으므로 이를 1:1로 단정하지 않는다.

## 설계 검토 포인트

- 복합 PK와 UK를 분리해서 본다. `SQL_SEQ`로 참조하더라도 기존 SQL 업무키는 복합 PK다.
- FK 부재에 따른 고아 detail, 존재하지 않는 PRIOR_MAP_ID/REF_SEQ의 검증 책임을 확인한다.
- `REF_SEQ <> SQL_SEQ` CHECK는 직접 자기 참조만 금지한다. 여러 row를 거치는 참조 순환은 금지하지 않는다.
- 공용 로그의 문자열 키 규약과 MIG_KIND를 함께 확인한다. 로그 관계에 일반 FK를 추가하는 설계는 현재 구조와 맞지 않는다.
- Milvus는 검색용 복제다. 최신 실행 상태와 재실행 가능 여부는 Oracle에서 판단한다.
- 원문의 RAG SOURCE_TABLES 규칙은 6.5의 “범위가 분명할 때만 입력”과 6.7의 “Conversion은 반드시 입력” 사이에 차이가 있다. ERD에서는 DDL대로 nullable로 표시했으며, 업무 필수 조건은 별도 확인이 필요하다.

## 수정·내보내기 팁

SVG는 브라우저에서 열어 확대하거나 문서/슬라이드에 삽입할 수 있다. HTML 뷰어는 외부 라이브러리 없이 동작하며 인쇄/PDF 저장을 지원한다.
Mermaid를 지원하는 Markdown 미리보기에서 이 문서 또는 `.mmd`를 편집하면 관계도를 다시 그릴 수 있다.
Oracle 타입의 길이, 기본값, CHECK와 인덱스는 원문 DDL을 기준으로 검토한다.
그림은 먼저 키와 참조 관계를 배치하고 상태/SQL 본문/타임스탬프 컬럼을 그 아래에 놓으면 읽기 쉽다.

DDL이 바뀌면 저장소 루트에서 다음 명령으로 산출물을 갱신한다. 테이블 추가나 관계 변경은 생성 스크립트의 레이아웃·관계도 함께 수정해야 한다.

```powershell
python smartMigrate_2.0_Langflow/00_guide/generate_chapter6_erd.py
```
'''
    (ROOT / (PREFIX+'.md')).write_text(md, encoding='utf-8')
    oracle_notes = '''<aside class="notes" aria-labelledby="oracle-notes-title">
<h2 id="oracle-notes-title">관계·제약 설명</h2><p>데이터베이스 설계도는 <strong>1920 × 1080 (16:9)</strong>으로 배치했습니다. <a href="00_architecture_chapter6_erd.svg" download>Oracle ERD SVG</a>를 PowerPoint에 삽입하거나, 이 탭에서 인쇄 / PDF를 선택해 장표만 출력할 수 있습니다.</p>
<p><strong>로그 대상 구분.</strong> 로그는 <code>MAP_ID</code> 문자열과 <code>MIG_KIND</code>로 대상을 구분한다. WORKFLOW / 관리 로그는 특정 작업과 연결되지 않을 수 있다.</p>
<p><strong>Correct SQL 참조.</strong> <code>NEXT_SQL_INFO</code>에는 <code>CHECK (REF_SEQ IS NULL OR REF_SEQ &lt;&gt; SQL_SEQ)</code>가 있다. <code>REF_SEQ</code> 지정 시 Correct SQL 컬렉션에 대상이 존재하는지 앱에서 검증한다.</p>
<p><strong>테이블 범위 메타데이터.</strong> <code>TARGET_TABLE</code>, <code>FR_TABLE</code>, <code>SOURCE_TABLES</code>는 scope 메타데이터다. 동일한 테이블명 문자열만으로 FK 또는 N:M 관계를 만들지 않는다.</p>
<p><strong>관계의 보장 범위.</strong> 1 / 0..1 / 0..N은 문서상 의도이며 DB가 보장하지 않는다. 기본 DDL에 FK가 없으므로 고아 참조가 저장될 수 있다.</p>
<p><strong>채번.</strong> <code>NEXT_SQL_INFO_SQL_SEQ</code>는 <code>SQL_SEQ</code>, <code>MIGRATION_LOG_SEQ</code>는 <code>LOG_ID</code>를 채번한다. <code>RAG_ID</code>는 IDENTITY다.</p>
<p>기본값·CHECK·인덱스 상세는 동반 Markdown과 원문 DDL을 참조한다.</p>
</aside>'''
    feature_notes = '''<aside class="notes" aria-labelledby="features-notes-title">
<h2 id="features-notes-title">장표 사용과 검증 범위</h2>
<p>위 그림은 1920 × 1080 (16:9)입니다. <strong>주요 기능 SVG</strong>를 저장한 뒤 PowerPoint의 삽입 → 그림에서 선택하면 한 장표로 사용할 수 있습니다. 이 탭에서 인쇄 / PDF를 선택하면 설명 영역을 제외하고 장표만 출력합니다.</p>
<p><strong>Migration.</strong> MIG_SQL은 INSERT INTO … SELECT … 형태의 이관 SQL입니다. VERIFY_SQL은 전체 행 수와 비교 대상 컬럼의 COUNT를 대조합니다. VERIFY2_SQL은 MIG_SQL과 VERIFY_SQL, 대상 PK 정보를 바탕으로 시스템이 조합하며, PK로 연결한 레코드의 비교 대상 값을 대조합니다. LOB/LONG 및 감사 컬럼 등 제외 조건이 있으므로 모든 컬럼의 전수 값 비교라고 해석하지 않습니다. TRUNC_YN 설정에 따라 대상 초기화가 선행될 수 있습니다.</p>
<p><strong>Conversion RAG SEARCH.</strong> 활성 SQL_CONVERSION SEARCH 규칙 중 원천 테이블 범위가 겹치는 규칙을 개수 제한 없이 전부 반영합니다. 각 규칙의 GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL이 TO_SQL 생성 프롬프트에 포함됩니다. SQL Tuning용 RAG Top K 설정은 이 경로에 적용되지 않습니다.</p>
<p><strong>Conversion 검증.</strong> TO_SQL을 생성하고, BIND_SQL을 실행해 검증용 바인드 값을 확보합니다. TEST_SQL은 최대 3개 바인드 케이스를 대상으로 AS-IS와 TO-BE 결과 건수를 비교합니다. 이 3개 제한은 RAG 가이던스 개수가 아닌 검증 케이스 수입니다. 케이스별 건수 불일치나 양쪽 모두 0건이면 실패합니다. 비SELECT 작업은 바인드·테스트를 생략합니다. 마지막 재시도에서 긴 원본 SQL을 사전 튜닝해 TUNED_FR_SQL을 생성하는 보조 경로도 있습니다.</p>
<p><strong>Tuning.</strong> 관련 규칙을 적용해 TUNED_TO_SQL과 변경 사유인 TUNED_RESULT를 만듭니다. SELECT가 변경된 경우 TUNED_TEST_SQL을 생성·실행해 기존 TO_SQL과 건수를 비교합니다. 검증 SQL은 런타임 결과와 로그에 기록되며 독립 DB 컬럼명이 아닙니다. 비SELECT 또는 튜닝 변경 없음은 검증을 생략합니다. 이 검증은 실행계획이나 응답시간 개선을 판정하는 성능 벤치마크가 아닙니다.</p>
<p><strong>Formatting.</strong> 배치는 이번 실행에서 전달된 생성 SQL 목록의 해당 컬럼을 갱신합니다. 단건은 TUNED_TO_SQL을 정리해 FORMATTED_SQL에 저장합니다. 출력 항목·형식·빈 결과를 확인하며 결과 데이터 비교 검증을 수행하지 않습니다.</p>
<p><strong>Management.</strong> 매핑 생성·실제 적용은 설정에 따라 구분됩니다. Correct SQL 저장은 해당 단계의 확인 완료 상태를 반영합니다. 규칙 변경·재시도 준비와 실제 작업 실행은 별도 요청입니다.</p>
</aside>'''
    scenario_notes = '<aside class="notes" aria-labelledby="scenarios-notes-title">\n<h2 id="scenarios-notes-title">30~40분 채팅 시연 · PPT 사용 안내</h2>\n<p>위 도식은 <strong>1920 × 1080 (16:9)</strong> 한 장입니다. <a href="00_demo_scenarios.svg" download>시연 시나리오 SVG 다운로드</a> 후 PowerPoint의 삽입 → 그림에서 사용하세요. 이 탭의 인쇄 / PDF는 설명 영역을 제외하고 장표만 출력합니다.</p>\n<p><strong>권장 순서: 01 → 02.</strong> 새 매핑 Migration을 먼저 진행하고, 사용 대상 Migration 완료 후 기존 SQL 보정과 후속 처리를 시연합니다. 각 흐름에 15~18분, 전체 결과와 예외 안내에 3~4분을 배정합니다. 실제 실행 대기 시간은 환경에 따라 달라집니다.</p>\n<p><strong>준비 데이터.</strong> 테이블/컬럼 매핑, 실패 단계별 독립 SQL 작업, 80% 초과 유사 후보, 사람이 검토한 SQL과 실제 바인드 값, 검색 자료를 준비합니다. 진행 중 조회는 별도 채팅/세션을 사용하며 지원되지 않는 환경에서는 단계별 로그 조회로 대체합니다.</p>\n<p><strong>자동 REF_SEQ.</strong> Conversion Correct SQL 저장·동기화 후 AS-IS SQL 기준으로 검색합니다. TO_SQL→FAIL-TOBE, BIND_SQL→FAIL-BIND, TEST_SQL→FAIL-TEST이며 유사도는 엄격히 80% 초과입니다. 후보의 실패 상태를 유지하고 REF_SEQ와 RETRY_COUNT=0만 반영합니다. Migration에는 이 자동 적용이 없습니다.</p>\n<p><strong>튜닝 결과 확인.</strong> Conversion의 PASS-CONVERSION 상태를 확인하고, TUNED_TO_SQL에서 튜닝된 TO-BE SQL을, TUNED_RESULT에서 적용한 튜닝 요약과 변경 사유를 조회합니다. TUNED_RESULT가 NO TUNING이면 적용한 튜닝이 없다는 뜻입니다. 성능 개선 수치를 검증한 결과와는 구분합니다.</p><p><a href="00_user_guide.md">사용자 가이드 · 실제 채팅 문장과 준비 조건</a></p>\n</aside>'
    logging_notes = '<aside class="notes" aria-labelledby="logging-notes-title">\n<h2 id="logging-notes-title">공통 DB 로깅 · 코드 설명</h2>\n<p>장표는 <strong>1920 × 1080 (16:9)</strong>입니다. <a href="00_logging_architecture.svg" download>로깅 아키텍처 SVG</a>를 PowerPoint의 삽입 → 그림으로 넣을 수 있습니다. 이 탭에서 인쇄 / PDF를 선택하면 장표만 출력합니다.</p>\n<p><strong>00A의 역할.</strong> DB 접속 정보와 system_schema를 받아 SmartMigrateDBHandler를 생성하고 smartmigrate.workflow Logger에 등록합니다. 워크플로우 시작 시 기존 공통 핸들러를 제거·닫고 새로 등록하여 중복 저장을 방지합니다. Logger는 같은 Python 프로세스에서 공유하며, 서로 다른 프로세스 사이에 자동으로 연결되는 메시지 브로커 구조는 아닙니다.</p>\n<p><strong>컴포넌트의 역할.</strong> Python 표준 logging 모듈로 같은 이름의 Logger를 얻어 info/error 등을 호출합니다. 로그 메시지와 extra의 workflow_log에 작업 식별자, 도메인, 로그 종류, 수준, 단계, 상태, 재시도 횟수, 선택 상세 본문을 전달합니다. SQL 작업은 SQL_SEQ, Migration은 MAP_ID로 식별합니다. 컴포넌트의 업무 처리용 DB 접속 설정과 로깅 설정은 별개입니다.</p>\n<p><strong>핸들러의 역할.</strong> emit()이 LogRecord를 받아 _event()로 이벤트를 해석하고, record.getMessage()로 메시지를 얻어 DB 행을 구성합니다. _insert_row()가 NEXT_MIG_LOG에 INSERT한 뒤 commit합니다. 상세 본문은 GENERATE_SQL에 Oracle CLOB으로 바인딩합니다. LOG_ID는 MIGRATION_LOG_SEQ, 발생 시각은 logging record.created를 기준으로 구성합니다. 장표의 INSERT_SQL은 실제 고정 INSERT 문을 간략히 표시한 이름입니다.</p>\n<p><strong>실행 방식.</strong> 현재 emit()에서 직접 DB 저장을 호출하는 동기 방식이며 별도 큐·백그라운드 전송은 사용하지 않습니다. 저장 오류는 핸들러의 insert_error에 보관하므로 Logger 호출만으로 DB 저장 성공을 단정하지 않습니다.</p>\n<p>코드: <a href="../01_agent_start/00A_logRuntimeStart.py">00A_logRuntimeStart.py · 핸들러와 등록</a> / <a href="../03_job_execution/12C_sqlConversionOneJobPocExecutor.py">12C · 컴포넌트의 이벤트 호출</a></p>\n</aside>'
    loop_notes = '<aside class="notes" aria-labelledby="loop-notes-title">\n<h2 id="loop-notes-title">18B · 동적 큐잉 도식</h2>\n<p><strong>900 × 900 (1:1)</strong> 도식입니다. <a href="00_loop_architecture.svg" download>동적 큐잉 SVG</a>를 PPT에 삽입해 가로·세로 비율을 유지한 채 장표의 약 1/3 영역에 배치하세요.</p>\n<p><strong>큐 초기화 코드.</strong> self.data는 18A에서 받은 작업 목록입니다. _validate_data()로 list[Data]로 정규화하고 ctx의 컴포넌트별 data 키에 저장하며 index=0부터 순회합니다. 도식의 3줄은 핵심 발췌로, 실제 initialize_data()의 작업 식별자 검증과 initialized 플래그 등은 생략했습니다.</p><p>전체 실행의 database_snapshot 목록은 첫 작업 이후 다음 작업 실행 전에 DB를 재조회합니다. 남은 큐의 동일 단계·MAP_ID/SQL_SEQ와 중복을 비교하고, 새 실행 후보를 Migration → Conversion → Tuning → Formatting 단계와 우선순위에 맞춰 미실행 구간에 넣습니다. 완료 구간은 유지하고 삽입 후 다음 실행 위치를 다시 확인합니다.</p>\n<p>작업 사이에 조회하는 방식이며 상시 감시는 아닙니다. 큐가 소진되면 종료합니다. 실제 코드는 <a href="../03_job_execution/18B_fullWorkflowLoop2.py">18B_fullWorkflowLoop2.py</a>를 참고하세요.</p>\n</aside>'
    rag_milvus_notes = '<aside class="notes" aria-labelledby="ragmilvus-notes-title">\n<h2 id="ragmilvus-notes-title">RAG / Milvus · 코드와 업무별 조회</h2>\n<p><strong>1920 × 1080 (16:9)</strong>입니다. <a href="00_rag_milvus_architecture.svg" download>RAG / Milvus SVG</a>를 PowerPoint에 삽입할 수 있습니다. 왼쪽은 폭을 줄인 핵심 코드, 오른쪽은 입력·컬렉션/필드·방식/결과를 ERD 형태의 테이블과 연결선으로 표시했습니다. DB Migration → SQL Conversion(SEARCH·Correct SQL·유사 SQL 검색) → SQL Tuning 순서로 배치하고 General은 공통 영역으로 구분했습니다. 연결선은 조회·검색 흐름이며 DB의 FK 관계가 아닙니다. 이 탭의 인쇄 / PDF는 장표만 출력합니다.</p>\n<p><strong>접속.</strong> 네 개는 각각 별도 DB가 아니라 설정된 Milvus DB 안의 컬렉션입니다. pymilvus.MilvusClient에 uri, user, password, db_name, timeout을 전달합니다. _milvus_config()가 컴포넌트 입력과 환경변수에서 설정을 모으고 _milvus_client()가 클라이언트를 생성합니다. 실제 비밀번호·키·주소는 장표에 넣지 않았습니다.</p>\n<p><strong>동기화.</strong> 04_saveVectorDB의 _ensure_collection()이 컬렉션과 인덱스를 준비하고 _sync_collection()이 doc_id/content_hash를 비교해 변경된 활성 문서를 임베딩하여 upsert합니다. sync_all은 RAG와 AS-IS SQL을 동기화하며, Correct SQL 전체를 다시 색인하지 않습니다. Correct SQL 저장 뒤에는 sync_correct_sql로 해당 SQL_SEQ 또는 MAP_ID의 저장 종류만 동기화합니다.</p>\n<p><strong>벡터 검색과 조건 조회.</strong> _embed_texts() 또는 저장된 벡터를 이용해 dense_vector에 COSINE 검색을 수행하고 필터·Top-K·검색 후 업무 조건을 적용합니다. query()/query_iterator()는 벡터 유사도 순위 없이 메타데이터 조건으로 조회합니다. 장표 코드는 핵심 발췌이며 실제 구현의 output_fields, 추가 필터, 후보 확대·후처리 등은 생략했습니다. 현재 설명한 실행 경로는 dense vector 검색이며 sparse_vector를 이용한 하이브리드 검색이라고 해석하지 않습니다.</p>\n<p><strong>General 반영 범위.</strong> SQL Conversion/SQL Tuning는 활성 GENERAL 규칙을 category/rule_type 조건으로 조회하고 SOURCE_TABLES 범위를 확인합니다. SOURCE_TABLES가 비어 있으면 공통 적용, 값이 있으면 현재 source table 범위와 겹치는지 확인합니다. 현재 query limit=1000이므로 무조건 모든 General을 제한 없이 넣는다는 의미는 아닙니다.</p><p><strong>SM_RAG_RULES.</strong> SQL Conversion은 SQL_CONVERSION SEARCH를 query_iterator()로 읽고 SOURCE_TABLES가 겹치는 사례를 모두 사용합니다. Top-K 벡터 검색이 아닙니다. SQL Tuning은 현재 TO_SQL(튜닝 반복 시 현재 SQL)의 MAIN/SUBQUERY 블록을 정규화해 임베딩하고, 가이드 SOURCE_SQL의 정규화 구조+원문으로 생성된 벡터와 COSINE 비교합니다. 후보의 SOURCE_TABLES 적용 범위를 확인한 뒤 블록별 Top-K를 선택합니다. GUIDANCE_TEXT와 TARGET_SQL은 비교 벡터의 대상이 아니라 조회된 결과를 프롬프트에 반영하는 자료입니다. GENERAL 규칙은 두 Executor 모두 category/rule_type 등의 조건 조회와 범위 확인으로 가져옵니다.</p>\n<p><strong>SM_ASIS_SQL.</strong> 04_ragCommandTool의 _search_similar_asis_sql()이 AS-IS SQL 유사 후보를 검색하고 Oracle의 최신 Conversion 상태와 비교합니다. REF_SEQ 적용은 별도 Update Tool이 담당합니다. SQL Conversion의 _stored_asis_query_vector()는 저장 SQL이 현재 원문과 일치할 때만 벡터를 재사용하고 없거나 불일치하면 새로 임베딩합니다.</p>\n<p><strong>SM_CORRECT_SQL_CONVERSION.</strong> SQL Conversion의 _correct_sql_hints_text()가 TO_SQL/BIND_SQL/TEST_SQL 생성 힌트를 구성합니다. REF_SEQ가 지정되어 있으면 해당 SQL_SEQ를 조건 조회하여 우선 사용하고, 지정되지 않았으면 원본 SQL 벡터로 유사 Correct SQL을 검색합니다. SQL Tuning이 이 컬렉션을 직접 검색하는 구조는 아닙니다.</p>\n<p><strong>SM_CORRECT_SQL_MIGRATION.</strong> DB Migration의 _migration_correct_sql_hints()가 테이블·조건·컬럼 매핑 정보를 임베딩하고 현재 생성하는 MIG_SQL 또는 VERIFY_SQL과 같은 종류의 예시를 검색합니다. SQL Formatting은 Milvus를 직접 조회하지 않습니다.</p>\n<p>코드: <a href="../02_flow_management/04_saveVectorDB.py">04 동기화</a> / <a href="../02_flow_management/04_ragCommandTool.py">04 유사 SQL 검색</a> / <a href="../03_job_execution/10C_migOneJobPocExecutor3.py">DB Migration</a> / <a href="../03_job_execution/12C_sqlConversionOneJobPocExecutor.py">SQL Conversion</a> / <a href="../03_job_execution/15C_sqlTuningOneJobPocExecutor.py">SQL Tuning</a></p>\n</aside>'
    correct_sql_notes = '<aside class="notes" aria-labelledby="correctsql-notes-title">\n<h2 id="correctsql-notes-title">Correct SQL · 상태 변경과 다음 실행</h2>\n<p><strong>1920 × 1080 (16:9)</strong> 장표입니다. <a href="00_correct_sql_flow.svg" download>Correct SQL 흐름 SVG</a>를 PPT에 삽입할 수 있습니다. 왼쪽은 사람이 보정한 기준 작업, 오른쪽은 그 Correct SQL을 참고하도록 연결할 유사 후보 작업입니다. 이 탭의 인쇄 / PDF는 장표만 출력합니다.</p>\n<p><strong>Migration.</strong> Correct MIG_SQL 저장은 INSERT를 사람이 이미 확인한 의미이며 STATUS=FAIL-TEST, RETRY_COUNT=0이 됩니다. 다음 실행은 INSERT를 반복하지 않고 AI의 VERIFY_SQL 생성과 건수 검증부터 재개하며 이후 시스템이 VERIFY2_SQL을 구성해 레코드 검증을 수행합니다. Correct VERIFY_SQL 저장은 검증까지 사람이 확인한 의미이며 STATUS=PASS, RETRY_COUNT=0으로 Migration을 완료 처리합니다. 초안 저장이나 SQL 저장만으로 이관/검증이 자동 수행된다는 의미가 아닙니다.</p>\n<p><strong>Conversion.</strong> Correct TO_SQL 저장은 STATUS_CONVERSION=FAIL-BIND이며 다음 실행은 저장 TO_SQL을 재사용해 BIND_SQL 생성·실행으로 BIND_SET을 확보하고 TEST_SQL 생성·검증으로 이어집니다. Correct BIND_SQL은 비어 있지 않은 JSON 객체 배열 BIND_SET과 함께 저장하고 FAIL-TEST로 전이하여 다음 실행에서 TEST_SQL 생성·검증부터 재개합니다. Correct TEST_SQL은 사용자 검증 완료로 PASS-CONVERSION이 되며 후속 Tuning 대상입니다. Tuning에서는 TUNED_TO_SQL과 TUNED_RESULT를 생성합니다.</p>\n<p><strong>동기화와 유사 검색.</strong> Management Agent는 저장 성공 후 저장한 SQL 한 건·종류를 sync_correct_sql로 동기화하고 Conversion의 AS-IS SQL을 기준으로 유사 실패 작업을 검색합니다. Correct TO_SQL 자체와 후보 TO_SQL을 비교하는 검색이 아닙니다. 동기화나 검색이 실패하면 완료 단계와 실패 단계를 구분하며 참조 등록 성공을 주장하지 않습니다.</p>\n<p><strong>후보별 REF_SEQ.</strong> 유사도가 엄격히 80%를 초과하고 저장 종류와 후보의 현재 실패 단계가 맞을 때만 적용합니다. TO_SQL→FAIL-TOBE, BIND_SQL→FAIL-BIND, TEST_SQL→FAIL-TEST입니다. 예를 들어 기준 SQL_SEQ=42의 Correct TO_SQL 저장으로 기준 상태는 FAIL-BIND가 되지만 후보는 FAIL-TOBE인 행에 REF_SEQ=42, RETRY_COUNT=0을 적용합니다. 후보의 실패 상태는 유지하고 기준 자신은 제외합니다. 실제 필드명은 REF_SQL이 아니라 REF_SEQ입니다.</p>\n<p><strong>별도 실행.</strong> REF_SEQ는 저장한 Correct SQL의 SQL_SEQ를 가리킵니다. 후보 SQL 전문을 복사하거나 즉시 실행하지 않고, 이후 명시적인 실행 요청에서 해당 실패 단계의 생성 힌트로 사용합니다. Migration에는 유사 후보 자동 REF_SEQ 적용이 없습니다. 재개 위치는 USER_EDITED 표시가 아니라 DB 상태로 결정합니다.</p>\n<p>코드: <a href="../02_flow_management/04_updateCommandTool.py">Correct SQL 저장·REF_SEQ 갱신</a> / <a href="../02_flow_management/04_managementAgentPrompt.md">동기화·검색·자동 적용 지침</a> / <a href="../03_job_execution/10C_migOneJobPocExecutor3.py">Migration 실행</a> / <a href="../03_job_execution/12C_sqlConversionOneJobPocExecutor.py">Conversion 실행</a></p>\n</aside>'
    page = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SmartMigrate ERD</title>
<style>body{{margin:0;background:#ffffff;font-family:'Segoe UI','Malgun Gothic',sans-serif;color:#243447}}header{{padding:18px 24px;background:white;position:sticky;top:0;z-index:1;border-bottom:1px solid #ddd}}h1{{font-size:22px;margin:0 0 12px}}button,a{{margin-right:8px}}button{{padding:7px 12px;cursor:pointer}}p{{margin:10px 0;font-size:14px}}#viewport{{overflow:auto;height:calc(100vh - 170px)}}svg{{display:block;width:100%;height:auto}}section[hidden]{{display:none}}.notes{{margin:24px;padding:20px 24px;max-width:1100px;background:white;border:1px solid #cbd5e1;border-radius:10px;line-height:1.7}}.notes h2{{margin:0 0 14px;font-size:20px}}.notes p{{font-size:16px;overflow-wrap:anywhere}}.notes code{{font-family:Consolas,monospace}}@media print{{header{{position:static}}button{{display:none}}#viewport{{height:auto;overflow:visible}}.notes{{break-before:page}}@page{{size:A3 landscape;margin:10mm}}}}</style>
<style>body[data-view="features"]{{background:#ffffff}}button[aria-pressed="true"]{{background:#18334e;color:white;border-color:#18334e}}@media print{{svg{{width:100% !important}}body[data-view="features"] header,body[data-view="features"] .notes{{display:none}}}}</style><style>@media print{{body[data-view="scenarios"] header,body[data-view="scenarios"] .notes,body[data-view="logging"] header,body[data-view="logging"] .notes,body[data-view="oracle"] header,body[data-view="oracle"] .notes,body[data-view="loop"] header,body[data-view="loop"] .notes,body[data-view="ragmilvus"] header,body[data-view="ragmilvus"] .notes,body[data-view="correctsql"] header,body[data-view="correctsql"] .notes{{display:none}}}}</style><style>body[data-view="loop"] #loop > svg{{width:min(100%,720px) !important;margin:0 auto}}@media print{{body[data-view="loop"] #loop > svg{{width:100% !important}}}}</style><style id="print-page"></style>
<header><h1>SmartMigrate · 구조와 주요 기능</h1><button data-view="oracle">데이터베이스 설계 · PPT</button><button data-view="milvus">Milvus 동기화</button><button data-view="ragmilvus">RAG · Milvus 코드 · PPT</button><button data-view="correctsql">Correct SQL 입력 · PPT</button><button data-view="features">주요 기능 · PPT 장표</button><button data-view="scenarios">시연 시나리오 · PPT</button><button data-view="logging">로깅 아키텍처 · PPT</button><button data-view="loop">루프 컴포넌트 · PPT</button><button id="out" aria-label="축소">−</button><button id="fit">화면 맞춤</button><button id="in" aria-label="확대">+</button><button id="print">인쇄 / PDF</button><span id="scale">100%</span><p id="view-description">데이터베이스 설계 · 전체 컬럼 · 16:9 PPT 장표 · 기본 DDL에 FK 없음</p><a href="{PREFIX}.svg">Oracle SVG</a><a href="{PREFIX}_milvus.svg">Milvus SVG</a><a href="00_agent_features.svg" download>주요 기능 SVG (PPT 삽입용)</a><a href="00_demo_scenarios.svg" download>시연 시나리오 SVG (PPT 삽입용)</a><a href="00_logging_architecture.svg" download>로깅 아키텍처 SVG (PPT 삽입용)</a><a href="00_rag_milvus_architecture.svg" download>RAG / Milvus SVG (PPT 삽입용)</a><a href="00_correct_sql_flow.svg" download>Correct SQL SVG (PPT 삽입용)</a><a href="00_loop_architecture.svg" download>루프 아키텍처 SVG (PPT 삽입용)</a><a href="{PREFIX}.md">설명 / Mermaid</a></header>
<main id="viewport"><section id="oracle">{oracle}{oracle_notes}</section><section id="milvus" hidden>{inline_svg(milvus, 'milvus')}</section><section id="features" hidden>{inline_svg(features, 'features')}{feature_notes}</section><section id="scenarios" hidden>{inline_svg(scenarios, 'scenarios')}{scenario_notes}</section><section id="logging" hidden>{inline_svg(logging_diagram, 'logging')}{logging_notes}</section><section id="loop" hidden>{inline_svg(loop_diagram, 'loop')}{loop_notes}</section><section id="ragmilvus" hidden>{inline_svg(rag_milvus_diagram, 'ragmilvus')}{rag_milvus_notes}</section><section id="correctsql" hidden>{inline_svg(correct_sql_diagram, 'correctsql')}{correct_sql_notes}</section></main>
<script>
let zoom=1,active='oracle';
const descriptions={{oracle:'데이터베이스 설계 · 전체 컬럼 · 16:9 PPT 장표 · 기본 DDL에 FK 없음',milvus:'Oracle 원천 → Milvus 검색 컬렉션 · 화살표는 동기화 방향',ragmilvus:'DB Migration / SQL Conversion / SQL Tuning · 업무별 검색 경로 · 공통 General · 16:9 PPT 장표',correctsql:'Correct SQL 입력 → 상태 전이 → 후속 생성 · 유사 실패 후보 REF_SEQ 등록 · 16:9 PPT 장표',features:'일반 대화 / Management / 작업 실행 · 템플릿 제공 · 매핑룰 등록 · 16:9 PPT 장표',scenarios:'두 가지 채팅 시연 · 30~40분 · 16:9 PPT 장표 · 상세 문장은 사용자 가이드 참조',logging:'Python 표준 logging + extra → 공통 DB Handler → NEXT_MIG_LOG · 16:9 PPT 장표',loop:'18B 동적 큐잉 · 1:1 도식 · PPT 약 1/3 영역 삽입용'}};
function render(){{
  document.querySelectorAll('section').forEach(s=>{{s.hidden=s.id!==active;s.querySelector('svg').style.width=(zoom*100)+'%'}});
  document.querySelectorAll('button[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===active)));
  document.body.dataset.view=active;
  document.getElementById('scale').textContent=Math.round(zoom*100)+'%';
  document.getElementById('view-description').textContent=descriptions[active];
  document.getElementById('print-page').textContent=active==='loop'?'@page {{ size:190.5mm 190.5mm; margin:0; }}':['oracle','features','scenarios','logging','ragmilvus','correctsql'].includes(active)?'@page {{ size:338.6667mm 190.5mm; margin:0; }}':'';
}}
function selectView(view){{active=Object.hasOwn(descriptions,view)?view:'oracle';zoom=1;render();document.getElementById('viewport').scrollTo(0,0)}}
document.querySelectorAll('button[data-view]').forEach(b=>b.onclick=()=>{{selectView(b.dataset.view);location.hash=active}});
window.addEventListener('hashchange',()=>selectView(location.hash.slice(1)));
document.getElementById('in').onclick=()=>{{zoom=Math.min(4,zoom+.25);render()}};
document.getElementById('out').onclick=()=>{{zoom=Math.max(.5,zoom-.25);render()}};
document.getElementById('fit').onclick=()=>{{zoom=1;render()}};
document.getElementById('print').onclick=()=>window.print();
selectView(location.hash.slice(1));
</script></html>'''
    (ROOT / (PREFIX+'.html')).write_text(page, encoding='utf-8')
    print('Generated 8 SVG diagrams, HTML viewer, Markdown guide, and Mermaid source.')
    print('Column counts:', {name: len(cols) for name, cols in tables.items()})


if __name__ == '__main__':
    main()
