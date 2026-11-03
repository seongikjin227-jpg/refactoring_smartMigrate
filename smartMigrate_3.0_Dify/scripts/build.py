"""Rebuild JSON samples and Dify DSL. Local build dependency: PyYAML only.

Runtime Code nodes use Python standard library only. Paths resolve from this file.
"""
import argparse
import json
import runpy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
TARGET_DIFY_VERSION = "1.15.0"
APP_DSL_VERSION = "0.6.0"


def resolve_workspace(args, root=ROOT):
    """Keep saved model/Tool bindings when regenerating the same two apps."""
    path = Path(root) / "dsl/workspace_binding.json"
    saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for argument, key, fallback in (
        ("tool_provider_id", "tool_provider_id", "__WORKFLOW_TOOL_PROVIDER_ID__"),
        ("tool_name", "tool_name", "smartmigrate_execute"),
        ("model_provider", "model_provider", ""), ("model", "model", ""),
        ("agent_plugin_identifier", "agent_plugin_identifier", "")):
        if getattr(args, argument, None) is None:
            setattr(args, argument, saved.get(key, fallback))
    return args


def sample(root=ROOT):
    """Table JSON files are the source of truth; hr_demo.json is a build output."""
    root = Path(root)
    folder = root / "samples/tables"
    manifest = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    state = json.loads((root / "samples/session.json").read_text(encoding="utf-8"))
    state["tables"] = {}
    for item in manifest:
        filename = item["file"]
        if Path(filename).name != filename:
            raise ValueError("테이블 파일은 samples/tables 바로 아래에 있어야 합니다.")
        state["tables"][item["table"]] = json.loads((folder / filename).read_text(encoding="utf-8"))
    expected = {name: name.lower() + "_json" for name in (
        "ASIS_HR_DEPT", "ASIS_HR_EMP", "TOBE_HR_DEPT", "TOBE_HR_EMP", "NEXT_MIG_INFO",
        "NEXT_MIG_INFO_DTL", "NEXT_SQL_INFO", "NEXT_MIG_RAG_INFO", "NEXT_MIG_LOG")}
    if {item["table"]: item["input"] for item in manifest} != expected or len(manifest) != len(expected):
        raise ValueError("manifest의 테이블/Tool 입력 이름이 실행 계약과 다릅니다.")
    return state


def node(ident, kind, title, index, **data):
    return {"id": ident, "type": "custom", "position": {"x": index * 310, "y": 260},
            "sourcePosition": "right", "targetPosition": "left", "width": 244, "height": 100,
            "data": {"type": kind, "title": title, "desc": "", "selected": False, **data}}


def variable(name, selector, typ="string"):
    return {"variable": name, "value_selector": selector, "value_type": typ}


def code_node(ident, title, index, source, inputs, outputs):
    return node(ident, "code", title, index, code_language="python3", code=source, variables=inputs,
                outputs={key: {"type": typ, "children": None} for key, typ in outputs.items()})


def app(name, mode, nodes, conversation_variables=None):
    edges = [{"id": a["id"] + "-" + b["id"], "source": a["id"], "target": b["id"],
              "sourceHandle": "source", "targetHandle": "target", "type": "custom",
              "data": {"sourceType": a["data"]["type"], "targetType": b["data"]["type"], "isInIteration": False}}
             for a, b in zip(nodes, nodes[1:])]
    return {"app": {"name": name, "description": "DB 없는 SmartMigrate 대화형 시연", "mode": mode,
                    "icon": "🔄", "icon_background": "#E4FBCC", "use_icon_as_answer_icon": False},
            "kind": "app", "version": APP_DSL_VERSION, "dependencies": [],
            "workflow": {"conversation_variables": conversation_variables or [], "environment_variables": [],
                         "features": {"file_upload": {"enabled": False}, "opening_statement": "작업 실행, 매핑룰 변경, 결과 조회를 요청해주세요.",
                                      "suggested_questions": ["안녕, 어떤 에이전트야?", "매핑룰 조회해줘", "DB Migration 실행해줘", "SQL 변환 대상 조회해줘", "SQL 변환 실행해줘", "SQL 튜닝룰 조회해줘", "SQL 튜닝 실행해줘"],
                                      "retriever_resource": {"enabled": False}, "sensitive_word_avoidance": {"enabled": False},
                                      "speech_to_text": {"enabled": False}, "text_to_speech": {"enabled": False},
                                      "suggested_questions_after_answer": {"enabled": False}},
                         "graph": {"nodes": nodes, "edges": edges, "viewport": {"x": 0, "y": 0, "zoom": 0.65}}}}


def input_field(name, required=True, default=None):
    field = {"variable": name, "label": name, "type": "paragraph", "required": required, "max_length": 200000, "options": []}
    if default is not None:
        field["default"] = default
    return field


def build(args):
    args = resolve_workspace(args)
    for name in ("samples", "dsl", "docs"):
        (ROOT / name).mkdir(exist_ok=True)
    state = sample()
    runpy.run_path(str(ROOT / "nodes/engine.py"))["validate"](state)
    table_inputs = [name.lower() + "_json" for name in state["tables"]]
    mapping = ["# 테이블 파일과 Tool 입력 연결", "",
               "`tables/*.json`은 수정·관리하는 원본이며 `hr_demo.json`은 build.py가 합치는 생성 파일입니다.", "",
               "| 원본 파일 | 메인 Chatflow 시작 입력 | 상태 준비 출력(확인용) |", "|---|---|---|"]
    for name in state["tables"]:
        var = name.lower() + "_json"
        mapping.append("| [" + name + "](tables/" + name + ".json) | `" + var + "` | `initialize." + var + "` |")
    mapping += ["", "Tool 입력은 request와 session_json입니다. 테이블별 입력을 Tool에 수동 연결하지 않습니다.",
                "02가 현재 session_transport를 session_json에 DSL로 사전 연결합니다. Agent는 request만 작성합니다.",
                "01 Tool이 실제 조회·관리·이관·SQL 처리·검증을 수행하고 변경된 상태를 반환합니다.",
                "대화 중에는 저장한 최신 상태를 사용합니다. 원본 파일을 매번 상수로 붙여넣으면 이전 변경이 사라집니다.",
                "메인 Chatflow 최초 입력의 테이블별 JSON은 선택 사항입니다. 비운 항목은 내장 기본값을 사용합니다."]
    (ROOT / "samples/TABLE_INPUTS.md").write_text("\n".join(mapping) + "\n", encoding="utf-8")
    compact = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    visible_sample = json.dumps(state, ensure_ascii=False, indent=2)
    table_defaults = {name.lower() + "_json": json.dumps(spec, ensure_ascii=False, indent=2)
                      for name, spec in state["tables"].items()}
    (ROOT / "samples/hr_demo.json").write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    # A standalone, readable definition bundle; no database install is required.
    definitions = {name: {"columns": spec["columns"], "primary_key": spec["primary_key"]} for name, spec in state["tables"].items()}
    (ROOT / "samples/table_definitions.json").write_text(json.dumps(definitions, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    core = (ROOT / "nodes/engine.py").read_text(encoding="utf-8")
    embedded = lambda filename: core + "\n\n" + (ROOT / "nodes" / filename).read_text(encoding="utf-8")
    (ROOT / "deployment").mkdir(exist_ok=True)
    (ROOT / "deployment/01_prepare_code.py").write_text(embedded("prepare.py"), encoding="utf-8")
    (ROOT / "deployment/01_execute_code.py").write_text(core, encoding="utf-8")
    (ROOT / "deployment/02_commit_code.py").write_text(embedded("commit.py"), encoding="utf-8")
    prepare_tool = embedded("prepare.py") + "\n\nprepare_legacy = main\n\n" + (ROOT / "nodes/prepare_tool.py").read_text(encoding="utf-8")
    (ROOT / "deployment/01_prepare_code.py").write_text(prepare_tool, encoding="utf-8")
    planner = (ROOT / "prompts/planner.md").read_text(encoding="utf-8")
    narrator = (ROOT / "prompts/narrator.md").read_text(encoding="utf-8")
    agent_prompt = (ROOT / "prompts/agent.md").read_text(encoding="utf-8").replace("smartmigrate_execute", args.tool_name)
    model = {"provider": args.model_provider, "name": args.model, "mode": "chat", "completion_params": {"temperature": 1, "max_tokens": 4096}}
    tool_nodes = [node("start", "start", "요청·현재 테이블", 0,
                       variables=[input_field("request"), input_field("session_json")]),
        code_node("prepare", "상태 확인·참고 규칙 선택", 1, prepare_tool,
                  [variable("request", ["start", "request"]), variable("session_json", ["start", "session_json"])],
                  {key: "string" for key in ("state_json", "context_json", "request", "request_id")}),
        node("planner", "llm", "통합 작업 계획·SQL 생성", 2, model=model.copy(),
             context={"enabled": False, "variable_selector": []},
             prompt_template=[{"role": "system", "text": planner},
                              {"role": "user", "text": "사용자 요청: {{#prepare.request#}}\n현재 데이터:\n{{#prepare.context_json#}}"}],
             variables=[], vision={"enabled": False}, error_strategy="default-value",
             default_value=[{"key": "text", "type": "string", "value": '{"action":"error","message":"작업 계획 LLM 호출에 실패했습니다."}'}]),
        code_node("execute", "작업 실행·COUNT·CONCAT 검증", 3, core,
                  [variable("state_json", ["prepare", "state_json"]), variable("plan_json", ["planner", "text"]),
                   variable("request_id", ["prepare", "request_id"]), variable("request", ["prepare", "request"])], {"result_json": "string"}),
        node("end", "end", "실행 결과·변경된 테이블", 4, outputs=[variable("result_json", ["execute", "result_json"])])]
    tool_app = app("SmartMigrate 통합 실행", "workflow", tool_nodes)
    initial = core + "\n\nDEFAULT_SAMPLE_JSON = " + repr(compact) + "\n\n" + (ROOT / "nodes/initialize.py").read_text(encoding="utf-8")
    parameters = {"request": {"auto": 1, "value": None},
                  "session_json": {"auto": 0, "value": {"type": "mixed", "value": "{{#initialize.session_transport#}}"}}}
    tool = {"enabled": True, "type": "workflow", "provider_name": args.tool_provider_id,
            "provider_show_name": "SmartMigrate 통합 실행", "tool_name": args.tool_name, "tool_label": "SmartMigrate 통합 실행",
            "parameters": parameters, "settings": {}, "extra": {"description": "매핑룰 조회, NEXT_SQL_INFO의 SQL 변환 대상 FR_SQL 조회, 튜닝룰 조회와 이관·SQL 변환·튜닝을 처리합니다. 조회 요청은 해당 테이블만 읽고 실행 단계는 수행하지 않습니다. request만 작성하세요. 현재 테이블은 시스템이 전달합니다."}}
    model_selector = {"provider": args.model_provider, "model": args.model, "model_type": "llm", "mode": "chat", "completion_params": {"temperature": 1}}
    agent_params = {"model": {"type": "constant", "value": model_selector},
                    "tools": {"type": "constant", "value": [tool]},
                    "instruction": {"type": "constant", "value": agent_prompt},
                    "query": {"type": "mixed", "value": "{{#sys.query#}}"},
                    "maximum_iterations": {"type": "constant", "value": 1}}
    main_nodes = [node("start", "start", "테이블 JSON(선택)", 0, variables=[input_field("sample_json", False, visible_sample)] + [input_field(n, False, table_defaults[n]) for n in table_inputs]),
        code_node("initialize", "대화 상태 준비", 1, initial,
                  [variable("saved_state", ["conversation", "demo_state"]), variable("sample_json", ["start", "sample_json"]), variable("turn_id", ["sys", "workflow_run_id"]), variable("user_request", ["sys", "query"])] + [variable(n, ["start", n]) for n in table_inputs],
                  {n: "string" for n in ["state_json", "session_json", "session_transport", "request_id"] + table_inputs}),
        node("agent", "agent", "SmartMigrate Agent", 2, agent_strategy_provider_name="langgenius/agent/agent", agent_strategy_name="function_calling",
             agent_strategy_label="Function Calling", tool_node_version="2", agent_parameters=agent_params,
             memory={"window": {"enabled": False, "size": 3}, "query_prompt_template": "{{#sys.query#}}"},
             error_strategy="default-value", default_value=[
                 {"key": "text", "type": "string", "value": "Agent Tool 호출에 실패해 실행하지 않았습니다. 추적의 Agent 오류 상세를 확인해주세요."},
                 {"key": "json", "type": "array[object]", "value": "[]"}]),
        code_node("commit", "Tool 결과 확인·상태 확정", 3, embedded("commit.py"),
                  [variable("state_json", ["initialize", "state_json"]), variable("request_id", ["initialize", "request_id"]), variable("agent_json", ["agent", "json"], "array[object]"),
                   variable("agent_text", ["agent", "text"]), variable("user_request", ["sys", "query"])],
                  {"state_json": "string", "answer": "string", "commit_status": "string", "narrative_json": "string"}),
        node("save", "assigner", "대화 테이블 저장", 4, version="2", items=[{"variable_selector": ["conversation", "demo_state"], "input_type": "variable", "operation": "over-write", "value": ["commit", "state_json"]}]),
        node("narrator", "llm", "결과 해설·다음 작업 추천", 5, model=model.copy(),
             context={"enabled": False, "variable_selector": []},
             prompt_template=[{"role": "system", "text": narrator},
                              {"role": "user", "text": "사용자 요청: {{#sys.query#}}\n검증된 결과 요약:\n{{#commit.narrative_json#}}"}],
             variables=[], vision={"enabled": False}, error_strategy="default-value",
             default_value=[{"key": "text", "type": "string", "value": "처리 결과를 아래에 표시합니다."}]),
        code_node("render", "일반 대화·업무 결과 출력 구분", 6, (ROOT / "nodes/render_answer.py").read_text(encoding="utf-8"),
                  [variable("narrative_json", ["commit", "narrative_json"]), variable("explanation", ["narrator", "text"]), variable("answer", ["commit", "answer"])], {"text": "string"}),
        node("answer", "answer", "설명과 결과 표", 7, answer="{{#render.text#}}", variables=[])]
    chat = app("SmartMigrate 3.0 대화 시연", "advanced-chat", main_nodes, [{"id": "5d9acfa3-a41c-4abf-8ccf-c3be3fd592a8", "name": "demo_state", "value_type": "string", "value": "", "description": "현재 샘플 테이블 전체 JSON"}])
    if args.agent_plugin_identifier:
        chat["dependencies"].append({"type": "marketplace", "value": {"marketplace_plugin_unique_identifier": args.agent_plugin_identifier}})
        next(n["data"] for n in main_nodes if n["id"] == "agent")["plugin_unique_identifier"] = args.agent_plugin_identifier
    for name, value in [("01_smartmigrate_tool.yml", tool_app), ("02_smartmigrate_chatflow.yml", chat)]:
        (ROOT / "dsl" / name).write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    manifest = {"dsl_version": APP_DSL_VERSION, "target_dify_version": TARGET_DIFY_VERSION,
                "architecture": "executing_workflow_tool_with_static_list_encoded_state_binding",
                "compatibility_review": "1.15.0 official source; local tests only",
                "model_provider": args.model_provider, "model": args.model,
                "agent_strategy": "function_calling", "agent_maximum_iterations": 1, "sample_sql_count": 1,
                "workflow_tool_provider_id": args.tool_provider_id, "agent_plugin_identifier": args.agent_plugin_identifier,
                "server_import_tested": False,
                "requires_configuration": ["Dify 등록 모델 선택", "Workflow를 " + args.tool_name + " Tool로 발행(request/session_json)", "Agent Tool ID 확인", "request 자동 ON/session_json 자동 OFF 및 V3 사전 연결 유지"]}
    (ROOT / "dsl/build_manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Built samples and two Dify apps. Server import/run is not tested.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-provider-id", default=None)
    parser.add_argument("--tool-name", default=None)
    parser.add_argument("--model-provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--agent-plugin-identifier", default=None)
    build(parser.parse_args())
