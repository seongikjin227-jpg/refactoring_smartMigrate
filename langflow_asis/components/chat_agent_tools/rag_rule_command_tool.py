from __future__ import annotations

import json
import re
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data

try:
    from app.utils.rag_db import get_all_rules, get_top_rules
except Exception:
    # Langflow may run this component with a different cwd / sys.path.
    # Add workspace root to sys.path at runtime and retry import.
    import sys
    from pathlib import Path

    _ROOT = Path(__file__).resolve().parents[3]
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from app.utils.rag_db import get_all_rules, get_top_rules


class RagRuleCommandTool(Component):
    display_name = "RAG Rule Command Tool"
    description = "Search and inspect RAG rules for SQL conversion/tuning"
    name = "RagRuleCommandTool"
    icon = "Book"

    inputs = [
        MessageTextInput(name="command_json", display_name="Command JSON", required=True, tool_mode=True),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        """Dispatch RAG rule related actions.

        Supported actions:
        - top_rules: return top N rules by hit count
        - search_rules: search rules by keyword
        - get_rule: return rule details for given rag_id

        Input: command_json describing the action and parameters.
        Output: JSON-serializable result dict.
        """
        try:
            # Ensure dependencies used by app.utils.rag_db (e.g., oracledb) are available
            self._ensure_runtime_dependencies()
            cmd = self._parse_command()
            action = str(cmd.get("action") or "top_rules").strip().lower()
            if action == "top_rules":
                limit = int(cmd.get("limit") or 5)
                rules = get_top_rules(limit=limit)
                res = {"ok": True, "action": "top_rules", "result": rules}
            elif action == "search_rules":
                keyword = str(cmd.get("keyword") or "").strip()
                rules = get_all_rules()
                if keyword:
                    k = keyword.lower()
                    rules = [r for r in rules if k in str(r.get("RAG_ID") or "").lower() or k in str(r.get("SOURCE_TABLES") or "").lower() or k in str(r.get("GUIDANCE_TEXT") or "").lower() or k in str(r.get("SOURCE_SQL") or "").lower()]
                res = {"ok": True, "action": "search_rules", "result": rules}
            elif action == "get_rule":
                rag_id = int(cmd.get("rag_id"))
                rules = get_all_rules()
                rule = next((r for r in rules if int(r.get("RAG_ID") or 0) == rag_id), None)
                res = {"ok": True, "action": "get_rule", "result": rule}
            else:
                raise ValueError(f"Unsupported action: {action}")
            self.status = res
            return Data(data=res)
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
            self.status = res
            return Data(data=res)

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {"action": "top_rules"}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _ensure_runtime_dependencies(self) -> None:
        AUTO_INSTALL_MISSING_PACKAGES = True
        missing = []
        try:
            import oracledb  # type: ignore
        except ModuleNotFoundError:
            missing.append("oracledb")
        if not missing:
            return
        if not AUTO_INSTALL_MISSING_PACKAGES:
            raise ModuleNotFoundError("Missing packages: " + ", ".join(missing))
        for pkg in missing:
            self._pip_install(pkg)

    def _pip_install(self, package: str) -> None:
        import subprocess, sys

        subprocess.check_call([sys.executable, "-m", "pip", "install", package])
