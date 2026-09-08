from __future__ import annotations

import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data



class FailAnalysisCommandTool(Component):
    display_name = "Fail Analysis Tool"
    description = "Failure log lookup and aggregated failure analysis"
    name = "FailAnalysisCommandTool"
    icon = "Bug"

    inputs = [
        MessageTextInput(name="command_json", display_name="Command JSON", required=True, tool_mode=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        try:
            cmd = self._parse_command()
            action = str(cmd.get("action") or "query_failure_log").strip().lower()
            if action == "query_failure_log":
                res = self._query_failure_log(cmd)
            elif action == "analyze_failures":
                res = self._analyze_failures(cmd)
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
            return {"action": "query_failure_log"}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _query_failure_log(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Retrieve failure logs for a specific migration or SQL.

        Inputs:
        - {"map_id": int} to fetch migration logs
        - {"sql_id": str, "space_nm": str (optional)} to fetch SQL logs

        Output: dict containing the matched logs and metadata.
        """
        # support map_id or sql_id+space_nm
        if cmd.get("map_id"):
            map_id = int(cmd["map_id"])
            rows = self._query(f"SELECT * FROM {self._qualify('NEXT_MIG_LOG')} WHERE MAP_ID = :1 ORDER BY CREATED_AT DESC, LOG_ID DESC", [map_id])
            logs = [{"row": list(r)} for r in rows]
            return {"ok": True, "action": "query_failure_log", "result": {"map_id": map_id, "logs": logs}}
        if cmd.get("sql_id"):
            sql_id = str(cmd["sql_id"])
            space = cmd.get("space_nm")
            if space:
                rows = self._query(f"SELECT * FROM {self._qualify('NEXT_SQL_LOG')} WHERE SQL_ID = :1 AND SPACE_NM = :2 ORDER BY CREATED_AT DESC", [sql_id, space])
            else:
                rows = self._query(f"SELECT * FROM {self._qualify('NEXT_SQL_LOG')} WHERE SQL_ID = :1 ORDER BY CREATED_AT DESC", [sql_id])
            logs = [{"row": list(r)} for r in rows]
            return {"ok": True, "action": "query_failure_log", "result": {"sql_id": sql_id, "space_nm": space, "logs": logs}}
        raise ValueError("map_id or sql_id is required for query_failure_log")

    def _analyze_failures(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Aggregate recent failure statistics for requested agents.

        Input: {"agent": "migration|sql_conversion|sql_tuning|all", "limit": int}
        Output: dict with recent failure totals and per-status counts.
        """
        agent = str(cmd.get("agent") or "all").lower()
        limit = int(cmd.get("limit") or 200)
        result = {}
        if agent in {"all", "migration", "mig"}:
            result["migration"] = self._recent_failures("NEXT_MIG_INFO", "STATUS", limit)
        if agent in {"all", "sql", "conversion", "sql_conversion"}:
            result["sql_conversion"] = self._recent_failures("NEXT_SQL_INFO", "STATUS_CONVERSION", limit)
        if agent in {"all", "tuning", "sql_tuning"}:
            result["sql_tuning"] = self._recent_failures("NEXT_SQL_INFO", "STATUS_TUNING", limit)
        return {"ok": True, "action": "analyze_failures", "result": result}

    # --- helpers ---
    @contextmanager
    def _connect(self):
        self._ensure_runtime_dependencies()
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        password = self._secret_to_str(getattr(self, "db_password", None)) or ""
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=password,
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

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

    def _query(self, sql: str, params: list[Any] | None = None) -> list[tuple]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params or [])
            return cur.fetchall()

    def _qualify(self, table_name: str) -> str:
        table = str(table_name or "").strip().upper()
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            return table
        return f"{schema}.{table}"

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        return str(value)

    def _recent_failures(self, table_name: str, status_column: str, limit: int) -> dict[str, Any]:
        table = self._qualify(table_name)
        rows = self._query(f"SELECT NVL(TRIM({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) AS CNT FROM {table} WHERE UPPER(TRIM(NVL({status_column}, ''))) LIKE 'FAIL%' GROUP BY NVL(TRIM({status_column}), 'NULL') ORDER BY CNT DESC")
        return {"total_fail": sum(int(r[1] or 0) for r in rows), "status_counts": [{"status": str(r[0]), "count": int(r[1] or 0)} for r in rows]}
