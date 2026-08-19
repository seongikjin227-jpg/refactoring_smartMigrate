from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


def _load_base_migration_tool():
    root = Path(__file__).resolve().parents[1]
    source = root / "langflow" / "components" / "unused" / "migration_command_tool.py"
    spec = importlib.util.spec_from_file_location("_newtype_base_migration_command_tool", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"Unable to load base migration tool from {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MigrationCommandTool


_BaseMigrationCommandTool = _load_base_migration_tool()


class NewType10MigPipeline(_BaseMigrationCommandTool):
    display_name = "10 MIG Pipeline"
    description = "Runs only run_migration_job from the legacy MigrationCommandTool in the new routed flow."
    name = "NewType10MigPipeline"
    icon = "Database"

    inputs = [
        DataInput(
            name="payload_json",
            display_name="Payload JSON",
            required=False,
            info="Payload from 09 DB Migration Agent. selected_job.map_id is used when command_json has no map_id.",
        ),
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info='Only run_migration_job is supported. Example: {"action":"run_migration_job","map_id":101}',
        ),
        StrInput(
            name="run_all_if_no_map_id",
            display_name="Run All If No MAP_ID",
            value="Y",
            required=False,
            info="Y: if no map_id is provided, repeatedly run all pending DB Migration jobs.",
        ),
        IntInput(name="run_all_limit", display_name="Run All Limit", value=1000, required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=True),
        StrInput(name="db_service_name", display_name="Service Name", required=True),
        StrInput(name="db_username", display_name="Username", required=True),
        SecretStrInput(name="db_password", display_name="Password", required=True),
        StrInput(
            name="llm_base_url",
            display_name="LLM Base URL",
            required=False,
            info="OpenAI-compatible LLM gateway base URL.",
        ),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="claude-haiku-4-5-20251001", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=4096, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=900, required=False),
        MessageTextInput(
            name="mig_sql_prompt",
            display_name="MIG SQL Prompt",
            required=False,
            info="Prompt template used by legacy _run_migration_job.",
        ),
        MessageTextInput(
            name="verify_sql_prompt",
            display_name="VERIFY SQL Prompt",
            required=False,
            info="Prompt template used by legacy _run_migration_job.",
        ),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        StrInput(name="source_schema", display_name="Source Schema", required=False),
        StrInput(name="target_schema", display_name="Target Schema", required=False),
        IntInput(name="default_max_attempts", display_name="Default Max Attempts", value=3, required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="run_pipeline")]

    def run_pipeline(self) -> Data:
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            command = self._parse_newtype_command(payload)
            action = str(command.get("action") or "run_migration_job").strip().lower()
            if action != "run_migration_job":
                raise ValueError("10 MIG Pipeline supports only action=run_migration_job")

            run_all_pending = self._as_bool(command.get("run_all_pending"))
            map_id = None if run_all_pending else command.get("map_id")
            if map_id is None or str(map_id).strip() == "":
                if not self._as_bool(getattr(self, "run_all_if_no_map_id", "Y")):
                    raise ValueError("map_id is required when run_all_if_no_map_id is not Y")
                result = self._run_all_pending_migration_jobs(command)
            else:
                result = self._run_one_migration_job(map_id, command)

            out = {
                **payload,
                "component": "10_migPipeline",
                "pipeline_status": result.get("status"),
                "job_result": result,
                "next_node": "13_finalSummary",
            }
            out.setdefault("history", []).append(
                {
                    "step": "mig_pipeline",
                    "message": f"status={result.get('status')}, ok={result.get('ok')}",
                }
            )
            self.status = result
            return Data(data=out)
        except Exception as exc:
            result = {"ok": False, "component": "10_migPipeline", "error": str(exc), "status": "ERROR"}
            self.status = result
            return Data(data=result)

    def _run_one_migration_job(self, map_id: Any, command: dict[str, Any]) -> dict[str, Any]:
        command = {**command, "action": "run_migration_job", "map_id": int(map_id)}
        return self._run_migration_job(int(map_id), command)

    def _run_all_pending_migration_jobs(self, command: dict[str, Any]) -> dict[str, Any]:
        limit = max(1, int(getattr(self, "run_all_limit", None) or 1000))
        results: list[dict[str, Any]] = []
        seen: set[int] = set()
        for _ in range(limit):
            pending = self._list_pending(1)
            jobs = list(pending.get("jobs") or [])
            if not jobs:
                return {
                    "ok": True,
                    "status": "DONE",
                    "message": "No pending DB Migration jobs remain.",
                    "run_mode": "all_pending",
                    "count": len(results),
                    "results": results,
                }
            map_id = int(jobs[0]["map_id"])
            if map_id in seen:
                return {
                    "ok": False,
                    "status": "STOPPED_DUPLICATE_PENDING",
                    "message": f"Same pending map_id={map_id} was returned again. Stop to avoid an infinite loop.",
                    "run_mode": "all_pending",
                    "count": len(results),
                    "results": results,
                }
            seen.add(map_id)
            result = self._run_one_migration_job(map_id, command)
            results.append(result)
            status = str(result.get("status") or "").upper()
            if status == "WAITING":
                break
        return {
            "ok": all(bool(item.get("ok")) for item in results) if results else True,
            "status": "DONE" if len(results) < limit else "LIMIT_REACHED",
            "message": f"Processed {len(results)} DB Migration job(s).",
            "run_mode": "all_pending",
            "count": len(results),
            "results": results,
        }

    def _parse_newtype_command(self, payload: dict[str, Any]) -> dict[str, Any]:
        command = self._parse_optional_json(getattr(self, "command_json", ""))
        if not command:
            command = {"action": "run_migration_job"}
        selected = payload.get("selected_job") or {}
        if not self._as_bool(command.get("run_all_pending")) and "map_id" not in command and selected.get("map_id") is not None:
            command["map_id"] = selected.get("map_id")
        command["action"] = "run_migration_job"
        return command

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        return self._parse_optional_json(raw)

    def _parse_optional_json(self, raw: Any) -> dict[str, Any]:
        if raw is None:
            return {}
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if not text:
            return {}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("JSON input must be an object")
        return parsed
