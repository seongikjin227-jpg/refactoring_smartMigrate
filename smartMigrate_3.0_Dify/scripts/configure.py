"""Bind generated apps using an Agent export from the user's Dify workspace.

No server access, tokens or credentials required. Does not edit the source export.
"""
import argparse
import ast
import json
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def configure(export_path, root=ROOT):
    root = Path(root)
    reference = yaml.safe_load(Path(export_path).read_text(encoding="utf-8-sig"))
    agents = [n["data"] for n in reference["workflow"]["graph"]["nodes"] if n["data"]["type"] == "agent"]
    matched = []
    for agent in agents:
        tools = agent.get("agent_parameters", {}).get("tools", {}).get("value", [])
        for tool in tools if isinstance(tools, list) else []:
            if isinstance(tool, dict) and tool.get("type") == "workflow" and tool.get("tool_name") in ("smartmigrate_execute", "smart_migrate_tool"):
                matched.append((agent, tool))
        if agent.get("agent_parameters", {}).get("tools") == {"type": "variable", "value": ["tool_binding", "tools"]}:
            generator = next(n["data"] for n in reference["workflow"]["graph"]["nodes"] if n["id"] == "tool_binding")
            tool = json.loads(ast.literal_eval(generator["code"].splitlines()[0].split(" = ", 1)[1]))
            matched.append((agent, tool))
    if len(matched) != 1:
        raise ValueError("참조 DSL에 smartmigrate_execute Workflow Tool을 연결한 Agent가 정확히 하나 필요합니다.")
    source_agent, source_tool = matched[0]
    source_model = source_agent["agent_parameters"]["model"]["value"]
    if not source_model.get("provider") or not source_model.get("model"):
        raise ValueError("참조 Agent에서 등록 모델을 먼저 선택하세요.")
    # Demo default: the reported Azure/LiteLLM gpt-5.5 route accepts only 1.
    # Do not reintroduce 0.1 when importing an older Agent reference export.
    source_model["completion_params"] = {**(source_model.get("completion_params") or {}), "temperature": 1}
    source_model["completion_params"].pop("stop", None)
    main_path = root / "dsl/02_smartmigrate_chatflow.yml"
    chat = yaml.safe_load(main_path.read_text(encoding="utf-8"))
    agent = next(n["data"] for n in chat["workflow"]["graph"]["nodes"] if n["data"]["type"] == "agent")
    for key in ("agent_strategy_provider_name", "agent_strategy_name", "agent_strategy_label", "plugin_unique_identifier", "tool_node_version"):
        if key in source_agent:
            agent[key] = source_agent[key]
    if agent["agent_strategy_name"] not in ("function_calling", "ReAct"):
        raise ValueError("참조 Agent 전략은 공식 Function Calling 또는 ReAct를 선택하세요.")
    # This demo's Azure/LiteLLM route rejects stop. ReAct injects Observation
    # even when the imported completion_params have no stop field.
    agent["agent_strategy_name"] = "function_calling"
    agent["agent_strategy_label"] = "Function Calling"
    target_tool = agent["agent_parameters"]["tools"]["value"][0]
    previous_name = target_tool.get("tool_name", "smartmigrate_execute")
    # Discard all old manual/automatic state slots from the server export.
    fixed_parameters = {"request": {"auto": 1, "value": None},
                        "session_json": {"auto": 0, "value": {"type": "mixed", "value": "{{#initialize.session_transport#}}"}}}
    target_tool.update(source_tool)
    target_tool.update(enabled=True, parameters=fixed_parameters)
    agent["agent_parameters"]["tools"] = {"type": "constant", "value": [target_tool]}
    instruction = agent["agent_parameters"]["instruction"]
    instruction["value"] = instruction["value"].replace(previous_name, source_tool["tool_name"])
    agent["agent_parameters"]["model"]["value"] = source_model
    agent["agent_parameters"]["maximum_iterations"]["value"] = 3 if agent["agent_strategy_name"] == "ReAct" else 1
    for n in chat["workflow"]["graph"]["nodes"]:
        if n["data"]["type"] == "llm":
            n["data"]["model"].update(provider=source_model["provider"], name=source_model["model"], mode=source_model.get("mode", "chat"))
            n["data"]["model"]["completion_params"] = {"temperature": 1, "max_tokens": 4096}
    chat["dependencies"] = reference.get("dependencies", [])
    tool_path = root / "dsl/01_smartmigrate_tool.yml"
    workflow = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
    workflow["dependencies"] = reference.get("dependencies", [])
    for n in workflow["workflow"]["graph"]["nodes"]:
        if n["data"]["type"] == "llm":
            n["data"]["model"].update(provider=source_model["provider"], name=source_model["model"], mode="chat",
                                       completion_params={"temperature": 1, "max_tokens": 4096})
    # All validation is completed before either generated file is modified.
    for path, value in ((main_path, chat), (tool_path, workflow)):
        path.write_text(yaml.safe_dump(value, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    manifest_path = root / "dsl/build_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest.update(model_provider=source_model["provider"], model=source_model["model"],
                    workflow_tool_provider_id=source_tool["provider_name"],
                    agent_plugin_identifier=source_agent.get("plugin_unique_identifier", ""),
                    requires_configuration=["Dify 서버 가져오기·연결 확인", "발행된 Tool LLM 모델 확인"],
                    server_import_tested=False)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    binding = {"source": main_path.name, "output": main_path.name,
               "tool_provider_id": source_tool["provider_name"], "tool_name": source_tool["tool_name"], "model_provider": source_model["provider"],
               "model": source_model["model"], "agent_plugin_identifier": source_agent.get("plugin_unique_identifier", ""),
               "server_import_tested": False}
    (root / "dsl/workspace_binding.json").write_text(json.dumps(binding, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Bound model, official Agent strategy and Workflow Tool from reference export.")
    print("Tool must expose request/session_json; keep static list and prewired encoded state.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--agent-export", required=True)
    configure(parser.parse_args().agent_export)
