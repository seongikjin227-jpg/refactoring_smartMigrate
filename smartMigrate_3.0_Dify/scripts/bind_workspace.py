"""Update the sole main Chatflow (02) with known workspace IDs."""
import argparse
import json
import uuid
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def bind_workspace(tool_provider_id, model_provider, model, root=ROOT, tool_name="smartmigrate_execute"):
    root = Path(root)
    uuid.UUID(tool_provider_id)
    if not model_provider.strip() or not model.strip():
        raise ValueError("모델 제공자와 모델 이름이 필요합니다.")
    source = root / "dsl/02_smartmigrate_chatflow.yml"
    chat = yaml.safe_load(source.read_text(encoding="utf-8"))
    agent = next(n["data"] for n in chat["workflow"]["graph"]["nodes"] if n["id"] == "agent")
    tools = agent["agent_parameters"]["tools"]["value"]
    if not isinstance(tools, list) or len(tools) != 1 or not isinstance(tools[0], dict):
        raise ValueError("현재 build.py로 정적 Tool 목록 DSL을 먼저 생성하세요.")
    tool = tools[0]
    if tool_name not in ("smartmigrate_execute", "smart_migrate_tool"):
        raise ValueError("Unsupported SmartMigrate tool name")
    previous_name = tool.get("tool_name", "smartmigrate_execute")
    tool.update(provider_name=tool_provider_id, enabled=True, tool_name=tool_name)
    instruction = agent["agent_parameters"]["instruction"]
    instruction["value"] = instruction["value"].replace(previous_name, tool_name)
    tool["parameters"] = {"request": {"auto": 1, "value": None},
                          "session_json": {"auto": 0, "value": {"type": "mixed", "value": "{{#initialize.session_transport#}}"}}}
    agent["agent_parameters"]["tools"] = {"type": "constant", "value": [tool]}
    selected = agent["agent_parameters"]["model"]["value"]
    selected.update(provider=model_provider, model=model, model_type="llm", mode="chat")
    selected.setdefault("completion_params", {})["temperature"] = 1
    selected["completion_params"].pop("stop", None)
    agent["agent_strategy_name"] = "function_calling"
    agent["agent_strategy_label"] = "Function Calling"
    agent["agent_parameters"]["maximum_iterations"]["value"] = 3 if agent["agent_strategy_name"] == "ReAct" else 1
    for n in chat["workflow"]["graph"]["nodes"]:
        if n["data"]["type"] == "llm":
            n["data"]["model"].update(provider=model_provider, name=model, mode="chat",
                                      completion_params={"temperature": 1, "max_tokens": 4096})
    chat["app"]["name"] = "SmartMigrate 3.0 대화 시연"
    destination = source
    destination.write_text(yaml.safe_dump(chat, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    tool_path = root / "dsl/01_smartmigrate_tool.yml"
    if tool_path.exists():
        workflow = yaml.safe_load(tool_path.read_text(encoding="utf-8"))
        for n in workflow["workflow"]["graph"]["nodes"]:
            if n["data"]["type"] == "llm":
                n["data"]["model"].update(provider=model_provider, name=model, mode="chat",
                                          completion_params={"temperature": 1, "max_tokens": 4096})
        tool_path.write_text(yaml.safe_dump(workflow, allow_unicode=True, sort_keys=False, width=120), encoding="utf-8")
    manifest = {"source": source.name, "output": destination.name, "tool_provider_id": tool_provider_id, "tool_name": tool_name,
                "model_provider": model_provider, "model": model, "manual_inputs": ["session_json"], "prewired_inputs": ["session_json"],
                "auto_inputs": ["request"], "server_import_tested": False,
                "agent_plugin_identifier": agent.get("plugin_unique_identifier", ""),
                "note": "Agent strategy identity remains from template; select the installed strategy if needed."}
    (root / "dsl/workspace_binding.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    build_manifest_path = root / "dsl/build_manifest.json"
    if build_manifest_path.exists():
        build_manifest = json.loads(build_manifest_path.read_text(encoding="utf-8"))
        build_manifest.update(workflow_tool_provider_id=tool_provider_id, tool_name=tool_name, server_import_tested=False)
        build_manifest_path.write_text(json.dumps(build_manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return destination


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--tool-provider-id", required=True)
    parser.add_argument("--model-provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--tool-name", default="smartmigrate_execute")
    args = parser.parse_args()
    output = bind_workspace(args.tool_provider_id, args.model_provider, args.model, tool_name=args.tool_name)
    print("Updated " + output.name + "; static Tool list, state input prewired in DSL.")
