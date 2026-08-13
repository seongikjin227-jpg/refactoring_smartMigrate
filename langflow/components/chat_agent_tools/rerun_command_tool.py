from __future__ import annotations

import json
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data



class RerunCommandTool(Component):
    display_name = "Rerun Command Tool"
    description = "Enqueue re-run requests for migration/sql conversion/tuning"
    name = "RerunCommandTool"
    icon = "PlayCircle"

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
            action = str(cmd.get("action") or "rerun_migration").strip().lower()
            # require explicit confirm flag to perform mutation
            confirm = bool(cmd.get("confirm"))
            if action == "rerun_migration":
                if not confirm:
                    return {"ok": True, "action": action, "confirmation_required": True, "details": {"map_id": int(cmd.get("map_id"))}}
                res = self._rerun_migration(int(cmd.get("map_id")))
            elif action == "rerun_sql_conversion":
                if not confirm:
                    return {"ok": True, "action": action, "confirmation_required": True, "details": {"sql_id": str(cmd.get("sql_id")), "space_nm": cmd.get("space_nm")}}
                res = self._rerun_sql_conversion(str(cmd.get("sql_id")), cmd.get("space_nm"))
            elif action == "rerun_sql_tuning":
                if not confirm:
                    return {"ok": True, "action": action, "confirmation_required": True, "details": {"sql_id": str(cmd.get("sql_id")), "space_nm": cmd.get("space_nm")}}
                res = self._rerun_sql_tuning(str(cmd.get("sql_id")), cmd.get("space_nm"))
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
            return {}
        return json.loads(text)

    def _enqueue_command(self, command_text: str, command_json: dict[str, Any]) -> int:
        table = self._qualify("NEXT_BATCH_COMMAND")
        with self._connect() as conn:
            cur = conn.cursor()
            out_id = cur.var(int)
            cur.execute(
                f"""
                INSERT INTO {table} (
                    CONTROL_NAME, COMMAND_STATUS, COMMAND_TYPE, COMMAND_TEXT,
                    COMMAND_JSON, REQUESTED_BY, REQUESTED_AT
                ) VALUES (
                    'BATCH_AGENT', 'PENDING', 'USER_COMMAND', :1, :2,
                    'LANGFLOW_CHAT_AGENT', CURRENT_TIMESTAMP
                )
                RETURNING COMMAND_ID INTO :3
                """,
                [str(command_text or ""), json.dumps(command_json or {}, ensure_ascii=False), out_id],
            )
            conn.commit()
            value = out_id.getvalue()
            if isinstance(value, list):
                value = value[0]
            return int(value)

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

    def _rerun_migration(self, map_id: int) -> dict[str, Any]:
        """Enqueue a migration re-run request for the provided MAP_ID.

        This creates a NEXT_BATCH_COMMAND row with action `rerun_migration`.
        Input: map_id (int)
        Output: dict with queued command id and status.
        """
        cmd = {"action": "rerun_migration", "map_id": map_id}
        text = f"rerun_migration map_id={map_id}"
        cid = self._enqueue_command(text, cmd)
        return {"ok": True, "action": "rerun_migration", "queued": True, "command_id": cid, "map_id": map_id, "message": f"Requeued map_id={map_id} with top priority."}

    def _rerun_sql_conversion(self, sql_id: str, space_nm: str | None) -> dict[str, Any]:
        """Enqueue a SQL conversion re-run request for the given SQL_ID (and optional SPACE_NM).

        Creates a NEXT_BATCH_COMMAND row. Input: sql_id, optional space_nm. Output: queued command info.
        """
        payload = {"action": "rerun_sql_conversion", "sql_id": sql_id}
        if space_nm:
            payload["space_nm"] = space_nm
        text = f"rerun_sql_conversion sql_id={sql_id}" + (f" space_nm={space_nm}" if space_nm else "")
        cid = self._enqueue_command(text, payload)
        return {"ok": True, "action": "rerun_sql_conversion", "queued": True, "command_id": cid, "sql_id": sql_id, "space_nm": space_nm}

    def _rerun_sql_tuning(self, sql_id: str, space_nm: str | None) -> dict[str, Any]:
        """Enqueue a SQL tuning re-run request for the given SQL_ID (and optional SPACE_NM).

        Creates a NEXT_BATCH_COMMAND row. Input: sql_id, optional space_nm. Output: queued command info.
        """
        payload = {"action": "rerun_sql_tuning", "sql_id": sql_id}
        if space_nm:
            payload["space_nm"] = space_nm
        text = f"rerun_sql_tuning sql_id={sql_id}" + (f" space_nm={space_nm}" if space_nm else "")
        cid = self._enqueue_command(text, payload)
        return {"ok": True, "action": "rerun_sql_tuning", "queued": True, "command_id": cid, "sql_id": sql_id, "space_nm": space_nm}

    @contextmanager
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        password = self._secret_to_str(getattr(self, "db_password", None)) or ""
        conn = oracledb.connect(user=str(getattr(self, "db_username", "") or "").strip(), password=password, dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

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
