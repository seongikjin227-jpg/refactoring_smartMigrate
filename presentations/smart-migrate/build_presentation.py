"""Build the confirmed report-style, developer-facing scrolling presentation."""
from pathlib import Path
from developer_content import SECTIONS
from mapping_reference import SOURCE, NAMES

ROOT=Path(__file__).resolve().parent
OUT=ROOT/'concepts'
NAV=[('validation-workload','검증 부담'),('users','사용자'),('mapping-tables','매핑 테이블'),('workflow','Workflow'),('demo-outputs','시연 Output'),('dify-components','Dify'),('failures','실패 분석')]
header='<a class="skip" href="#content">본문으로 이동</a><header class="topbar"><a class="wordmark" href="#top">SQL깍두기<span>SmartMigrate Agent</span></a><nav aria-label="본문 탐색">'+''.join(f'<a href="#{id}">{label}</a>' for id,label in NAV)+'</nav></header>'
hero='''<div class="hero"><div><h1>SmartMigrate<br>Agent</h1><p class="intro">DBA와 B/E 개발자를 위한<br>매핑 기반 Migration·SQL 변환</p><p class="origin-line">사람이 업무 흐름과 정합성 기준을 검토하고,<br>Agent가 확정된 규칙으로 반복 처리를 연결합니다.</p><div class="hero-actions"><a class="primary" href="#responsibility">사람과 Agent의 역할</a><a href="#dify-components">Dify 구현 흐름 보기</a></div></div><div class="hero-sql"><p class="sql-label">사람이 확정할 것</p><p>업무 흐름 · 데이터 의미 · 정합성 기준<br>원천·대상 매핑룰</p><div class="sql-after"><p class="sql-label">Agent가 연결할 것</p><p>이관 SQL 생성·실행·검증<br>AS-IS SQL → TO-BE SQL 변환·검증</p></div></div></div>'''
parts=[]
for id,(title,desc,body) in SECTIONS.items():
 cls=' closing-section' if id=='expansion' else ''
 parts.append(f'<section class="section{cls}" id="{id}" aria-labelledby="heading-{id}"><div class="section-header"><h2 id="heading-{id}">{title}</h2><p>{desc}</p></div><div class="section-body">{body}</div></section>')
html='''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta name="description" content="SQL깍두기 SmartMigrate Agent: 사람과 AI의 역할, DBA·백엔드 활용, Workflow와 Dify 구현"><title>SQL깍두기 · SmartMigrate Agent</title><link rel="stylesheet" href="site.css"><script src="site.js" defer></script></head><body class="report" id="top">'''+header+'<main id="content">'+hero+''.join(parts)+'</main><footer class="page-footer"><span>SQL깍두기 · SmartMigrate Agent</span><span>개발자를 위한 설계와 검증</span><a href="#top">처음으로</a></footer></body></html>'
(OUT/'a-report.html').write_text(html,encoding='utf-8')
(OUT/'samples').mkdir(exist_ok=True)
for name in NAMES:
 (OUT/'samples'/f'{name}.json').write_bytes((SOURCE/f'{name}.json').read_bytes())
css=(OUT/'site.css').read_text(encoding='utf-8').replace("url('assets/","url('concepts/assets/")
default=html.replace('<link rel="stylesheet" href="site.css">','<style>'+css+'</style>').replace('<script src="site.js" defer></script>','')
default=default.replace('</body>','<script>'+(OUT/'site.js').read_text(encoding='utf-8')+'</script></body>')
default=default.replace('href="samples/','href="concepts/samples/')
for name in ['index.html','SmartMigrate-Agent.html']:(ROOT/name).write_text(default,encoding='utf-8')
print('Built confirmed report presentation:',len(SECTIONS),'sections.')
