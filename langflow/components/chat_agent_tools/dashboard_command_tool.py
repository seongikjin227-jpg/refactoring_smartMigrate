from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data



class DashboardCommandTool(Component):
    display_name = "Dashboard Command Tool"
    description = "Dashboard queries: overview, current_jobs, stats"
    name = "DashboardCommandTool"
    icon = "LayoutDashboard"

    inputs = [
        MessageTextInput(name="command_json", display_name="Command JSON", required=True, tool_mode=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="list_limit", display_name="Default List Limit", value=5, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        try:
            cmd = self._parse_command()
            action = str(cmd.get("action") or "overview").strip().lower()
            if action == "overview":
                res = self._overview(cmd)
            elif action == "current_jobs":
                res = self._current_jobs(cmd)
            elif action == "stats":
                res = self._stats(cmd)
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
            return {"action": "overview"}
        if text.startswith("```"):
            text = text.strip("`\n ")
        return json.loads(text)

    def _overview(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Return a high-level overview of the system.

        Input: command dict (ignored)
        Output: dict containing supervisor/control summary, pending commands and top-level counts.
        """
        summary = self._status_summary()
        return {"ok": True, "action": "overview", "result": summary}

    def _current_jobs(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """List recent/current job heartbeat entries from NEXT_BATCH_LOG.

        Input: {"limit": int}
        Output: list of running job summaries (run_id, loop_no, event, agent, job_id, message)
        """
        limit = int(cmd.get("limit") or self.list_limit or 5)
        # Minimal: list running jobs from NEXT_BATCH_LOG heartbeat
        rows = self._query(
            f"SELECT RUN_ID, LOOP_NO, EVENT_TYPE, AGENT_NAME, JOB_ID, MESSAGE FROM {self._qualify('NEXT_BATCH_LOG')} WHERE ROWNUM <= :1",
            [max(1, limit)],
        )
        jobs = [
            {"run_id": r[0], "loop_no": r[1], "event": r[2], "agent": r[3], "job_id": r[4], "message": self._text(r[5])}
            for r in rows
        ]
        return {"ok": True, "action": "current_jobs", "result": jobs}

    def _stats(self, cmd: dict[str, Any]) -> dict[str, Any]:
        """Return aggregated counts by status for migration, sql conversion, and tuning.

        Input: command dict (ignored)
        Output: dict containing status count maps for each agent.
        """
        migration = self._count_by_status("NEXT_MIG_INFO", "STATUS")
        sql_conv = self._count_by_status("NEXT_SQL_INFO", "STATUS_CONVERSION")
        sql_tune = self._count_by_status("NEXT_SQL_INFO", "STATUS_TUNING")
        return {"ok": True, "action": "stats", "result": {"migration": migration, "sql_conversion": sql_conv, "sql_tuning": sql_tune}}

    # --- helpers (lightweight copy from ChatCommandTool) ---
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

    def _text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        return str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _count_by_status(self, table_name: str, status_column: str) -> dict[str, int]:
        table = self._qualify(table_name)
        rows = self._query(f"SELECT NVL(TRIM({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) FROM {table} GROUP BY NVL(TRIM({status_column}), 'NULL')")
        return {str(r[0] or 'NULL'): int(r[1] or 0) for r in rows}
