from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data


DEFAULT_DB_CONFIG = {
    "db_host": "10.0.0.1",
    "db_port": 1521,
    "db_service_name": "ORCL",
    "db_username": "SMARTMIGRATE",
    "db_password": "password",
    "system_schema": "SFAADM",
}


class SupervisorControlCommandTool(Component):
    display_name = "Supervisor Control Tool"
    description = "Control and inspect NEXT_BATCH_CONTROL (start/stop/pause/resume/status)"
    name = "SupervisorControlCommandTool"
    icon = "Server"

    inputs = [
        MessageTextInput(name="command_json", display_name="Command JSON", required=True, tool_mode=True),
        StrInput(name="db_host", display_name="DB Host", value=DEFAULT_DB_CONFIG["db_host"], required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", value=DEFAULT_DB_CONFIG["db_service_name"], required=False),
        StrInput(name="db_username", display_name="DB Username", value=DEFAULT_DB_CONFIG["db_username"], required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", value=DEFAULT_DB_CONFIG["system_schema"], required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    def run_command(self) -> Data:
        """Dispatch supervisor control actions (status/start/stop).

        Inputs:
        - {"action":"status"}
        - {"action":"stop","confirm":true}
        - {"action":"start","confirm":true}

        Mutating actions require explicit `confirm": true`.
        Output: action-specific result dict.
        """
        try:
            raw = getattr(self, "command_json", "")
            cmd = raw if isinstance(raw, dict) else ({} if not str(raw or "").strip() else json.loads(str(raw)))
            action = str(cmd.get("action") or "status").strip().lower()
            confirm = bool(cmd.get("confirm"))
            if action == "status":
                res = self._status()
            elif action == "stop":
                if not confirm:
                    return {"ok": True, "action": "stop", "confirmation_required": True}
                self._request_stop()
                res = {"ok": True, "action": "stop", "result": "Stop requested"}
            elif action == "start":
                if not confirm:
                    return {"ok": True, "action": "start", "confirmation_required": True}
                self._start()
                res = {"ok": True, "action": "start", "result": "Start requested"}
            else:
                raise ValueError(f"Unsupported action: {action}")
            self.status = res
            return Data(data=res)
        except Exception as exc:
            res = {"ok": False, "error": str(exc)}
            self.status = res
            return Data(data=res)

    def _status(self) -> dict[str, Any]:
        """Return the current NEXT_BATCH_CONTROL row for BATCH_AGENT.

        Output: dict with fields exists, status, run_id, stop_requested_yn, loop_no, heartbeat_at, last_event, last_agent, last_job_id, last_job_status, message.
        """
        table = self._qualify("NEXT_BATCH_CONTROL")
        rows = self._query(f"SELECT STATUS, RUN_ID, STOP_REQUESTED_YN, LOOP_NO, HEARTBEAT_AT, LAST_EVENT, LAST_AGENT, LAST_JOB_ID, LAST_JOB_STATUS, MESSAGE FROM {table} WHERE CONTROL_NAME = 'BATCH_AGENT'")
        if not rows:
            return {"exists": False}
        r = rows[0]
        return {"exists": True, "status": self._text(r[0]), "run_id": self._text(r[1]), "stop_requested_yn": self._text(r[2]), "loop_no": int(r[3] or 0), "heartbeat_at": self._text(r[4]), "last_event": self._text(r[5]), "last_agent": self._text(r[6]), "last_job_id": self._text(r[7]), "last_job_status": self._text(r[8]), "message": self._text(r[9])}

    def _request_stop(self) -> None:
        """Request the supervisor to stop by setting NEXT_BATCH_CONTROL stop flags.

        This updates the DB row; no return value.
        """
        table = self._qualify("NEXT_BATCH_CONTROL")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET STATUS = 'STOP_REQUESTED', STOP_REQUESTED_YN = 'Y', STOP_REQUESTED_AT = CURRENT_TIMESTAMP, UPDATED_AT = CURRENT_TIMESTAMP, LAST_EVENT = 'STOP_REQUESTED', MESSAGE = 'Stop requested by Chat Agent.' WHERE CONTROL_NAME = 'BATCH_AGENT'")
            conn.commit()

    def _start(self) -> None:
        """Request the supervisor to start by setting NEXT_BATCH_CONTROL to RUNNING.

        This updates the DB row; no return value.
        """
        table = self._qualify("NEXT_BATCH_CONTROL")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET STATUS = 'RUNNING', STOP_REQUESTED_YN = 'N', UPDATED_AT = CURRENT_TIMESTAMP, LAST_EVENT = 'START', MESSAGE = 'Start requested by Chat Agent.' WHERE CONTROL_NAME = 'BATCH_AGENT'")
            conn.commit()

    @contextmanager
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(str(getattr(self, "db_host", "") or DEFAULT_DB_CONFIG["db_host"]).strip(), int(getattr(self, "db_port", None) or DEFAULT_DB_CONFIG["db_port"]), service_name=str(getattr(self, "db_service_name", "") or DEFAULT_DB_CONFIG["db_service_name"]).strip())
        password = self._secret_to_str(getattr(self, "db_password", None)) or str(DEFAULT_DB_CONFIG["db_password"])
        conn = oracledb.connect(user=str(getattr(self, "db_username", "") or DEFAULT_DB_CONFIG["db_username"]).strip(), password=password, dsn=dsn)
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
        schema = str(getattr(self, "system_schema", "") or DEFAULT_DB_CONFIG["system_schema"]).strip().upper()
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
