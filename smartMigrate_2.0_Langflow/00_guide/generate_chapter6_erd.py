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
    h = 90 + len(cols) * 28 + 18
    parts += [f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="white" stroke="#cbd5e1"/>',
              f'<rect x="{x}" y="{y}" width="{w}" height="62" rx="10" fill="{color}"/>',
              text(x+16, y+27, name, 22, '#fff', 'bold', True), text(x+16, y+49, subtitle, 15, '#fff'),
              text(x+16, y+81, 'KEY / NULL', 13), text(x+115, y+81, 'COLUMN', 13), text(x+345, y+81, 'ORACLE TYPE', 13)]
    for i, col in enumerate(cols):
        top = y + 90 + i * 28
        if i % 2 == 0:
            parts.append(f'<rect x="{x+1}" y="{top}" width="{w-2}" height="28" fill="#f1f5f9"/>')
        key = ('PK ' if col['pk'] else '') + ('UK ' if col['uk'] else '') + ('NN' if col['nn'] else '')
        parts += [text(x+16, top+20, key, 14, color, 'bold'), text(x+115, top+20, col['name'], 16, mono=True),
                  text(x+345, top+20, col['type'], 16, mono=True)]
    return h


def oracle_svg(tables):
    parts = start(2050, 1490, 'SmartMigrate · Oracle ERD', 'Chapter 6 DDL의 5개 테이블, 전체 컬럼, PK/UK/NOT NULL 및 논리 참조. 기본 DDL에는 FK 없음.')
    parts += [text(55, 94, '문서 기준 · 전체 컬럼 · PK / UK / NN · 기본 DDL에 FOREIGN KEY 없음', 19),
              text(55, 127, '파란 점선: 논리 참조 (참조하는 컬럼 → 대상 키)    주황 점선: 로그 대상 식별 (조건부, FK 아님)', 17)]
    layout = {
        'NEXT_MIG_INFO': (90, 200, 530, 'DB Migration 작업 master', '#246a89'),
        'NEXT_MIG_INFO_DTL': (720, 200, 530, '컬럼 매핑 detail · 복합 PK', '#246a89'),
        'NEXT_SQL_INFO': (1370, 200, 580, 'SQL Conversion / Tuning / Formatting', '#6554a4'),
        'NEXT_MIG_LOG': (720, 780, 530, '공용 workflow / 실행 로그', '#9c6020'),
        'NEXT_MIG_RAG_INFO': (90, 990, 530, 'RAG Guide 원천 · 직접 키 관계 없음', '#357855'),
    }
    for name, spec in layout.items():
        card(parts, name, tables[name], *spec)
    # References point from child/source columns toward the referenced key.
    parts += [path('M 720 304 H 670 V 170 H 645 V 304 H 620'),
              text(740, 179, 'DTL.MAP_ID → master.MAP_ID  |  의도: master 1 : detail 0..N', 15),
              path('M 90 528 H 35 V 304 H 90'),
              text(95, 179, 'PRIOR_MAP_ID → MAP_ID  |  선행 0..1 / 후속 0..N', 15),
              path('M 1950 388 H 2005 V 304 H 1950'),
              text(1380, 170, 'REF_SEQ → SQL_SEQ (UK)  |  참조 0..1 / 피참조 0..N', 15),
              path('M 720 912 H 655 V 304 H 620', '#b66b22'),
              text(680, 714, '로그 MAP_ID: DB_MIGRATION → MAP_ID', 16, '#965719'),
              path('M 1250 912 H 1310 V 304 H 1370', '#b66b22'),
              text(730, 747, 'SQL 계열 → SQL_SEQ = 값  |  Formatting: SQL_SEQ 또는 MAP_ID', 15, '#965719')]
    parts += [text(55, 1420, 'PK = Primary Key (복합 PK는 표시된 컬럼을 함께 사용) · UK = UNIQUE · NN = NOT NULL', 18, weight='bold'),
              text(55, 1455, 'Source: 00_architecture_chapter6_oracle_ddl.md', 16), '</svg>']
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
    parts = start(1920, 1080, 'SmartMigrate · 주요 기능', '자연어 요청을 Management와 작업 실행으로 구분. 실행별 생성 SQL과 실제 검증 기준을 정리한 16:9 장표.', background='#ffffff')
    parts += [text(55, 99, '자연어로 작업을 관리하고, 매핑 기반 SQL 생성부터 실행·검증까지 연결', 26, '#516176')]

    def rect(x, y, w, h, fill='white', stroke='#d8e0eb', radius=14):
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>')

    rect(60, 126, 1800, 64, '#18334e', '#18334e')
    parts += [text(90, 167, '사용자 요청', 25, '#fff', 'bold'),
              text(300, 167, '“상태·SQL·로그를 보여줘”', 23, '#fff'),
              text(800, 167, '요청 분류  →', 23, '#9cd8ee', 'bold'),
              text(1120, 167, '“Migration / Conversion 작업을 실행해줘”', 23, '#fff'),
              path('M 960 190 V 212 H 320 V 235', '#246a89', False),
              path('M 960 212 H 1245 V 235', '#6554a4', False)]

    rect(60, 235, 520, 690)
    rect(630, 235, 1230, 690)
    parts += [text(85, 278, '01  Management', 30, '#246a89', 'bold'),
              text(85, 310, '현황 조회 · 데이터 변경 · 검토 자료 관리', 21, '#516176'),
              text(655, 278, '02  작업 실행 요청', 30, '#6554a4', 'bold'),
              text(655, 310, '단건 / 도메인 / 전체 workflow  ·  대상과 선행 조건 확인 후 실행', 21, '#516176')]

    management = [
        ('현황·결과 조회', ['대시보드 · 진행 상태 · 잔여 작업', '생성 SQL · 검증 결과 · 실패 원인 / 로그']),
        ('매핑·작업 정보 관리', ['테이블 / 컬럼 매핑 SQL 생성·적용', '사용 여부 · 우선순위 · 재시도 준비']),
        ('Correct SQL 보정', ['사람이 검토한 단계 SQL 저장', '완료 단계 반영 · 해당 SQL 검색 자료 동기화']),
        ('참고 규칙·사례 관리', ['RAG Guide 등록 / 수정 · VectorDB 동기화', '유사 실패 SQL 검색 · 참고 SQL 지정']),
    ]
    for i, (title, lines) in enumerate(management):
        y = 337 + i * 138
        rect(80, y, 480, 120, '#eef6fa', '#d8e7ef', 10)
        parts.append(text(100, y+33, title, 25, '#246a89', 'bold'))
        for j, line in enumerate(lines):
            parts.append(text(100, y+65+j*29, line, 20))
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
    panel(547, '02', 'SQL Conversion / Tuning · 보정과 재실행', '핵심: RAG와 검색 자료 → Correct SQL → REF_SEQ → Conversion / Tuning / Formatting', '#246a89', [
        (['대시보드', '실패·SQL 조회'], ['일반 대화로 시작', '상태 · 오류 · 생성 SQL']),
        (['RAG 관리', 'VectorDB 동기화'], ['등록 · 조회 · 수정', '검색 자료에 반영']),
        (['Correct SQL', '저장·자동 동기화'], ['TOBE / BIND / TEST', '검토한 SQL 입력']),
        (['유사 SQL 검색', 'REF_SEQ 자동 등록'], ['AS-IS 유사도 80% 초과', '동일 실패 단계 후보']),
        (['재실행', 'Tuning · Formatting'], ['참고 SQL로 재개', '남은 후속 단계 처리']),
        (['결과·생성 SQL', '검증·로그 조회'], ['변환 · 튜닝 변경 사유', '정리된 SQL 확인']),
    ], '자동 참조 대상: TOBE → FAIL-TOBE   ·   BIND → FAIL-BIND   ·   TEST → FAIL-TEST   |   REF_SEQ 등록과 실제 실행은 별도')
    parts += [text(60, 967, '시연 준비  실제 대상 번호 · 실패 단계별 작업 · 검토한 SQL · 매핑 상세 행 · 검색 자료 동기화', 23, '#18334e', 'bold'),
              text(60, 1010, '진행 조회는 실행 중 별도 채팅에서 확인  |  마무리: 최근 전체 결과 → 남은 전체 워크플로우 → 선행 조건 안내', 21, '#516176'), '</svg>']
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
<h2 id="oracle-notes-title">관계·제약 설명</h2>
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
    scenario_notes = '<aside class="notes" aria-labelledby="scenarios-notes-title">\n<h2 id="scenarios-notes-title">30~40분 채팅 시연 · PPT 사용 안내</h2>\n<p>위 도식은 <strong>1920 × 1080 (16:9)</strong> 한 장입니다. <a href="00_demo_scenarios.svg" download>시연 시나리오 SVG 다운로드</a> 후 PowerPoint의 삽입 → 그림에서 사용하세요. 이 탭의 인쇄 / PDF는 설명 영역을 제외하고 장표만 출력합니다.</p>\n<p><strong>권장 순서: 01 → 02.</strong> 새 매핑 Migration을 먼저 진행하고, 사용 대상 Migration 완료 후 기존 SQL 보정과 후속 처리를 시연합니다. 각 흐름에 15~18분, 전체 결과와 예외 안내에 3~4분을 배정합니다. 실제 실행 대기 시간은 환경에 따라 달라집니다.</p>\n<p><strong>준비 데이터.</strong> 테이블/컬럼 매핑, 실패 단계별 독립 SQL 작업, 80% 초과 유사 후보, 사람이 검토한 SQL과 실제 바인드 값, 검색 자료를 준비합니다. 진행 중 조회는 별도 채팅/세션을 사용하며 지원되지 않는 환경에서는 단계별 로그 조회로 대체합니다.</p>\n<p><strong>자동 REF_SEQ.</strong> Conversion Correct SQL 저장·동기화 후 AS-IS SQL 기준으로 검색합니다. TO_SQL→FAIL-TOBE, BIND_SQL→FAIL-BIND, TEST_SQL→FAIL-TEST이며 유사도는 엄격히 80% 초과입니다. 후보의 실패 상태를 유지하고 REF_SEQ와 RETRY_COUNT=0만 반영합니다. Migration에는 이 자동 적용이 없습니다.</p>\n<p><a href="00_user_guide.md">사용자 가이드 · 실제 채팅 문장과 준비 조건</a></p>\n</aside>'
    page = f'''<!doctype html><html lang="ko"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>SmartMigrate ERD</title>
<style>body{{margin:0;background:#ffffff;font-family:'Segoe UI','Malgun Gothic',sans-serif;color:#243447}}header{{padding:18px 24px;background:white;position:sticky;top:0;z-index:1;border-bottom:1px solid #ddd}}h1{{font-size:22px;margin:0 0 12px}}button,a{{margin-right:8px}}button{{padding:7px 12px;cursor:pointer}}p{{margin:10px 0;font-size:14px}}#viewport{{overflow:auto;height:calc(100vh - 170px)}}svg{{display:block;width:100%;height:auto}}section[hidden]{{display:none}}.notes{{margin:24px;padding:20px 24px;max-width:1100px;background:white;border:1px solid #cbd5e1;border-radius:10px;line-height:1.7}}.notes h2{{margin:0 0 14px;font-size:20px}}.notes p{{font-size:16px;overflow-wrap:anywhere}}.notes code{{font-family:Consolas,monospace}}@media print{{header{{position:static}}button{{display:none}}#viewport{{height:auto;overflow:visible}}.notes{{break-before:page}}@page{{size:A3 landscape;margin:10mm}}}}</style>
<style>body[data-view="features"]{{background:#ffffff}}button[aria-pressed="true"]{{background:#18334e;color:white;border-color:#18334e}}@media print{{svg{{width:100% !important}}body[data-view="features"] header,body[data-view="features"] .notes{{display:none}}}}</style><style>@media print{{body[data-view="scenarios"] header,body[data-view="scenarios"] .notes{{display:none}}}}</style><style id="print-page"></style>
<header><h1>SmartMigrate · 구조와 주요 기능</h1><button data-view="oracle">Oracle 전체 컬럼</button><button data-view="milvus">Milvus 동기화</button><button data-view="features">주요 기능 · PPT 장표</button><button data-view="scenarios">시연 시나리오 · PPT</button><button id="out" aria-label="축소">−</button><button id="fit">화면 맞춤</button><button id="in" aria-label="확대">+</button><button id="print">인쇄 / PDF</button><span id="scale">100%</span><p id="view-description">문서 기준 · 기본 DDL에 FK 없음 · 파란 점선: 논리 참조 · 주황 점선: 조건부 로그 대상 식별</p><a href="{PREFIX}.svg">Oracle SVG</a><a href="{PREFIX}_milvus.svg">Milvus SVG</a><a href="00_agent_features.svg" download>주요 기능 SVG (PPT 삽입용)</a><a href="00_demo_scenarios.svg" download>시연 시나리오 SVG (PPT 삽입용)</a><a href="{PREFIX}.md">설명 / Mermaid</a></header>
<main id="viewport"><section id="oracle">{oracle}{oracle_notes}</section><section id="milvus" hidden>{inline_svg(milvus, 'milvus')}</section><section id="features" hidden>{inline_svg(features, 'features')}{feature_notes}</section><section id="scenarios" hidden>{inline_svg(scenarios, 'scenarios')}{scenario_notes}</section></main>
<script>
let zoom=1,active='oracle';
const descriptions={{oracle:'문서 기준 · 기본 DDL에 FK 없음 · 파란 점선: 논리 참조 · 주황 점선: 조건부 로그 대상 식별',milvus:'Oracle 원천 → Milvus 검색 컬렉션 · 화살표는 동기화 방향',features:'Management / 작업 실행 · 생성 SQL과 검증 기준 · 16:9 PPT 장표',scenarios:'두 가지 채팅 시연 · 30~40분 · 16:9 PPT 장표 · 상세 문장은 사용자 가이드 참조'}};
function render(){{
  document.querySelectorAll('section').forEach(s=>{{s.hidden=s.id!==active;s.querySelector('svg').style.width=(zoom*100)+'%'}});
  document.querySelectorAll('button[data-view]').forEach(b=>b.setAttribute('aria-pressed',String(b.dataset.view===active)));
  document.body.dataset.view=active;
  document.getElementById('scale').textContent=Math.round(zoom*100)+'%';
  document.getElementById('view-description').textContent=descriptions[active];
  document.getElementById('print-page').textContent=['features','scenarios'].includes(active)?'@page {{ size:338.6667mm 190.5mm; margin:0; }}':'';
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
    print('Generated 4 SVG diagrams, HTML viewer, Markdown guide, and Mermaid source.')
    print('Column counts:', {name: len(cols) for name, cols in tables.items()})


if __name__ == '__main__':
    main()
