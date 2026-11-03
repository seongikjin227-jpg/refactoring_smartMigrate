"""Replay exported DSL Code with fixed plans; no LLM, Dify server, or Oracle."""
from pathlib import Path
import importlib.util
import json

ROOT = Path(__file__).resolve().parent
PROJECT = ROOT.parents[1]
TESTS = PROJECT/'smartMigrate_3.0_Dify/tests/test_demo.py'

def capture():
    spec=importlib.util.spec_from_file_location('smartmigrate_fixture_support',TESTS)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    execute=module.exported_node('01_smartmigrate_tool.yml','execute')
    commit=module.exported_node('02_smartmigrate_chatflow.yml','commit')
    initial=module.seed()
    cases={}
    def run(key,state,plan,request_example):
        ident='presentation-'+key
        output=execute(json.dumps(state,ensure_ascii=False),json.dumps(plan,ensure_ascii=False),ident,request_example)
        result=json.loads(output['result_json'])
        saved=commit(json.dumps(state,ensure_ascii=False),ident,module.generated_logs(output),'',user_request=request_example)
        after=json.loads(saved['state_json'])
        cases[key]={'request_example':request_example,'commit_status':saved['commit_status'],
                    'answer':saved['answer'],'narrative':json.loads(saved['narrative_json']),'state':after}
        return after
    introduction='안녕, 어떤 에이전트야?'
    greeting=commit(json.dumps(initial,ensure_ascii=False),'presentation-introduction',[],'',user_request=introduction)
    cases['introduction']={'request_example':introduction,'commit_status':greeting['commit_status'],
        'answer':greeting['answer'],'narrative':json.loads(greeting['narrative_json']),'state':json.loads(greeting['state_json']), 'tool_called':False}
    queried=run('query',initial,{'action':'query','tables':['NEXT_MIG_INFO','NEXT_MIG_INFO_DTL']},'매핑룰 조회해줘')
    migrated=run('migration',queried,{'action':'run','stage':'migration'},'DB Migration 실행해줘')
    sources=run('sql_source',migrated,{'action':'query','tables':['NEXT_SQL_INFO'],'query_view':'sql_source'},'SQL 변환 대상 조회해줘')
    converted=run('conversion',sources,{'action':'run','stage':'conversion','sql_results':module.fixture_artifacts(sources)},'SQL 변환 실행해줘')
    run('tuning_guide',converted,{'action':'query','rule_category':'SQL_TUNING'},'SQL 튜닝룰 조회해줘')
    tuned=run('tuning',converted,{'action':'run','stage':'tuning','sql_results':module.fixture_artifacts(converted)},'SQL 튜닝 실행해줘')
    formatted=run('formatting',tuned,{'action':'run','stage':'formatting','sql_results':module.fixture_artifacts(tuned)},'SQL 서식 정리해줘')
    run('export',formatted,{'action':'export'},'현재 상태를 JSON으로 내보내줘')
    run('mapping_update',formatted,{'action':'update','table':'NEXT_MIG_INFO','key':{'MAP_ID':2},'values':{'CONDITION':'SALARY >= 7000'}},'MAP_ID 2의 조건을 SALARY >= 7000으로 바꿔줘')
    run('clarify',formatted,{'action':'clarify','question':'변경할 매핑의 MAP_ID를 알려주세요.'},'그 매핑을 바꿔줘')
    run('error',migrated,{'action':'run','stage':'conversion','sql_results':[]},'변환 계획에서 SQL 산출물이 누락된 경우')
    run('blocked_scope',initial,{'action':'run','stage':'migration'},'SQL 변환 및 튜닝 진행해줘')
    run('compound_sql',initial,module.pipeline_plan(initial, ('conversion','tuning')),'SQL 변환 및 튜닝 진행해줘')
    assert all(cases[k]['commit_status']=='OK' for k in ['query','migration','conversion','tuning_guide','tuning','formatting','export','mapping_update','compound_sql'])
    assert cases['blocked_scope']['commit_status']=='ERROR'
    assert cases['introduction']['commit_status']=='CHAT'
    assert cases['sql_source']['commit_status']=='OK'
    assert cases['clarify']['commit_status']=='NEEDS_INPUT'
    assert cases['error']['commit_status']=='ERROR'
    assert len(migrated['tables']['TOBE_HR_EMP']['rows'])==10
    assert all(r['STATUS_CONVERSION']=='DEMO-GENERATED' for r in converted['tables']['NEXT_SQL_INFO']['rows'])
    assert all(r['STATUS']=='STALE' for r in cases['mapping_update']['state']['tables']['NEXT_MIG_INFO']['rows'])
    payload={'provenance':{'execution':'exported DSL execute and commit Code, local replay',
             'planner':'fixed plans and SQL fixtures replace LLM','dify_server':False,'oracle':False},'cases':cases}
    path=ROOT/'concepts/samples/demo-evidence.json'
    path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Exported DSL Code replay: 14 cases passed. No Dify server, LLM or Oracle.')
    return payload

if __name__=='__main__':capture()
