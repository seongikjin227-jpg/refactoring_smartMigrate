from __future__ import annotations

import json
import logging
import os
import re
import importlib.util
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04UpdateCommandTool(Component):
    display_name = "04 Update Command Tool"
    description = "Runs fixed SQL update actions for SmartMigrate management requests."
    name = "NewType04UpdateCommandTool"
    icon = "DatabaseZap"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info=(
                "Fixed UPDATE action JSON. Example: "
                '{"actions":[{"action":"set_migration_user_edited","map_id":101,"user_edited":"N"},'
                '{"action":"clear_migration_mig_sql","map_id":101}]}'
            ),
        ),
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        StrInput(name="milvus_uri", display_name="Milvus URI", required=False, advanced=True),
        StrInput(name="milvus_username", display_name="Milvus Username", required=False, advanced=True),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=False, advanced=True),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=False, advanced=True),
        StrInput(
            name="correct_sql_conversion_collection_name",
            display_name="Correct SQL Conversion Collection Name",
            value="SM_CORRECT_SQL_CONVERSION",
            required=False,
            advanced=True,
        ),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=False, advanced=True),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False, advanced=True),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False, advanced=True),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False, advanced=True),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    # 관리 Agent가 만든 action 목록을 고정된 UPDATE 명령으로만 실행한다.
    # 임의 SQL 문자열을 직접 실행하지 않는 것이 이 컴포넌트의 안전 경계다.
    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 Update Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            result = self._apply_actions(command)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_updateCommandTool", "error": str(exc)}
            self.status = result
            return Data(data=result)

    # 모든 action을 먼저 statement로 검증한 뒤 하나의 transaction으로 실행한다.
    # 중간 action 하나라도 실패하면 rollback하여 부분 갱신 상태를 남기지 않는다.
    def _apply_actions(self, command: dict[str, Any]) -> dict[str, Any]:
        actions = command.get("actions")
        if not isinstance(actions, list) or not actions:
            raise ValueError("actions must be a non-empty list")

        for raw in actions:
            action = str(raw.get("action") or "").strip().lower() if isinstance(raw, dict) else ""
            if action in {"set_sql_ref_seq", "set_sql_reference_seq"}:
                self._validate_ref_seq_in_correct_sql(raw)

        statements = [self._build_statement(item, index) for index, item in enumerate(actions)]
        results: list[dict[str, Any]] = []

        with self._connect() as conn:
            cur = conn.cursor()
            try:
                for index, statement in enumerate(statements):
                    cur.execute(statement["sql"], statement["params"])
                    skipped = bool(statement.get("skip_when_not_matched")) and cur.rowcount == 0
                    if cur.rowcount != 1 and not skipped:
                        raise ValueError(
                            f"Action {index + 1} did not update exactly one row: "
                            f"{statement['identity']}, rowcount={cur.rowcount}"
                        )
                    results.append(
                        {
                            "index": index,
                            "action": statement["action"],
                            "identity": statement["identity"],
                            "updated_rows": cur.rowcount,
                            "summary": "Skipped: status is no longer FAIL-*" if skipped else statement["summary"],
                            "skipped": skipped,
                        }
                    )
                conn.commit()
            except Exception:
                conn.rollback()
                raise

        vector_sync = None
        if self._requires_correct_sql_sync(actions):
            vector_sync = self._sync_correct_sql_vector_db()

        answer = self._answer(results)
        if vector_sync:
            answer += f"\n- Correct SQL VectorDB sync: {vector_sync['summary']}"
        logging.getLogger("smartmigrate.workflow").info(
            answer,
            extra={"workflow_log": [0, "WORKFLOW", "04_UPDATE_TOOL", "INFO", "UPDATE", "PASS", len(results)]},
        )
        return {
            "ok": True,
            "component": "04_updateCommandTool",
            "action": "apply_actions",
            "transaction": True,
            "updated_count": sum(int(item["updated_rows"]) for item in results),
            "actions": results,
            "answer_text": answer,
            "vector_sync": vector_sync,
            "final": True,
        }

    # action 이름을 허용 목록으로 해석해, 테이블/컬럼/SET 절이 모두 코드에서 결정되게 한다.
    # raw command는 식별자와 값 바인딩만 제공할 수 있다.
    def _build_statement(self, raw: Any, index: int) -> dict[str, Any]:
        if not isinstance(raw, dict):
            raise ValueError(f"actions[{index}] must be an object")
        action = str(raw.get("action") or "").strip().lower()
        if not action:
            raise ValueError(f"actions[{index}].action is required")

        if action == "reset_migration_status":
            map_id = self._map_id(raw)
            retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
            set_sql = "STATUS = NULL, RETRY_COUNT = :retry_count"
            params: dict[str, Any] = {"map_id": map_id, "retry_count": retry_count}
            if "priority" in raw and raw.get("priority") not in (None, ""):
                set_sql += ", PRIORITY = :priority"
                params["priority"] = self._int_value(raw.get("priority"), "priority")
            return self._migration_statement(action, map_id, set_sql, params, "STATUS=NULL, RETRY_COUNT reset")

        if action == "set_migration_user_edited":
            map_id = self._map_id(raw)
            value = self._yn_value(raw.get("user_edited"), "user_edited")
            return self._migration_statement(action, map_id, "USER_EDITED = :user_edited", {"map_id": map_id, "user_edited": value}, f"USER_EDITED={value}")

        if action == "set_migration_use_yn":
            map_id = self._map_id(raw)
            value = self._yn_value(raw.get("use_yn"), "use_yn")
            return self._migration_statement(action, map_id, "USE_YN = :use_yn", {"map_id": map_id, "use_yn": value}, f"USE_YN={value}")

        if action == "set_migration_priority":
            map_id = self._map_id(raw)
            value = self._int_value(raw.get("priority"), "priority")
            return self._migration_statement(action, map_id, "PRIORITY = :priority", {"map_id": map_id, "priority": value}, f"PRIORITY={value}")

        if action == "clear_migration_mig_sql":
            map_id = self._map_id(raw)
            return self._migration_statement(action, map_id, "MIG_SQL = NULL", {"map_id": map_id}, "MIG_SQL=NULL")

        if action == "clear_migration_verify_sql":
            map_id = self._map_id(raw)
            return self._migration_statement(action, map_id, "VERIFY_SQL = NULL", {"map_id": map_id}, "VERIFY_SQL=NULL")

        if action == "save_migration_mig_sql":
            map_id = self._map_id(raw)
            sql_text = self._text_value(raw, "sql_text", "mig_sql")
            return self._migration_statement(action, map_id, "MIG_SQL = :sql_text", {"map_id": map_id, "sql_text": sql_text}, "MIG_SQL saved")

        if action == "save_migration_verify_sql":
            map_id = self._map_id(raw)
            sql_text = self._text_value(raw, "sql_text", "verify_sql")
            return self._migration_statement(action, map_id, "VERIFY_SQL = :sql_text", {"map_id": map_id, "sql_text": sql_text}, "VERIFY_SQL saved")

        if action == "reset_sql_conversion_status":
            return self._sql_reset_statement(raw, action, "STATUS_CONVERSION")

        if action == "reset_sql_tuning_status":
            return self._sql_reset_statement(raw, action, "STATUS_TUNING")

        if action == "retry_failed_sql_conversion":
            return self._sql_retry_failed_statement(raw, action, "STATUS_CONVERSION")

        if action == "retry_failed_sql_tuning":
            return self._sql_retry_failed_statement(raw, action, "STATUS_TUNING")

        if action == "reset_sql_formatting_result":
            sql_id, space_nm = self._sql_identity(raw)
            retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
            return self._sql_statement(
                action,
                sql_id,
                space_nm,
                "FORMATTED_SQL = NULL, RETRY_COUNT = :retry_count",
                {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count},
                "FORMATTED_SQL=NULL, RETRY_COUNT reset",
            )

        if action == "set_sql_user_edited":
            sql_id, space_nm = self._sql_identity(raw)
            value = self._yn_value(raw.get("user_edited"), "user_edited")
            return self._sql_statement(action, sql_id, space_nm, "USER_EDITED = :user_edited", {"sql_id": sql_id, "space_nm": space_nm, "user_edited": value}, f"USER_EDITED={value}")

        if action == "set_sql_priority":
            sql_id, space_nm = self._sql_identity(raw)
            value = self._int_value(raw.get("priority"), "priority")
            return self._sql_statement(action, sql_id, space_nm, "PRIORITY = :priority", {"sql_id": sql_id, "space_nm": space_nm, "priority": value}, f"PRIORITY={value}")

        if action in {"set_sql_ref_seq", "set_sql_reference_seq"}:
            return self._sql_ref_seq_statement(raw, action, clear=False)

        if action in {"clear_sql_ref_seq", "clear_sql_reference_seq"}:
            return self._sql_ref_seq_statement(raw, action, clear=True)

        sql_text_actions = {
            "clear_sql_to_sql": ("TO_SQL", None),
            "clear_sql_bind_sql": ("BIND_SQL", None),
            "clear_sql_test_sql": ("TEST_SQL", None),
            "clear_sql_tuned_to_sql": ("TUNED_TO_SQL", None),
            "clear_sql_formatted_sql": ("FORMATTED_SQL", None),
            "save_sql_to_sql": ("TO_SQL", "to_sql"),
            "save_sql_bind_sql": ("BIND_SQL", "bind_sql"),
            "save_sql_test_sql": ("TEST_SQL", "test_sql"),
            "save_sql_tuned_to_sql": ("TUNED_TO_SQL", "tuned_to_sql"),
            "save_sql_formatted_sql": ("FORMATTED_SQL", "formatted_sql"),
        }
        if action in sql_text_actions:
            column, text_key = sql_text_actions[action]
            sql_id, space_nm = self._sql_identity(raw)
            if text_key is None:
                return self._sql_statement(action, sql_id, space_nm, f"{column} = NULL", {"sql_id": sql_id, "space_nm": space_nm}, f"{column}=NULL")
            sql_text = self._text_value(raw, "sql_text", text_key)
            return self._sql_statement(action, sql_id, space_nm, f"{column} = :sql_text", {"sql_id": sql_id, "space_nm": space_nm, "sql_text": sql_text}, f"{column} saved")

        raise ValueError(f"Unsupported update action: {action}")

    # 명시적 reset은 상태를 NULL로 돌려 해당 단계의 자동 실행 대상 조건에 다시 맞게 만든다.
    def _sql_reset_statement(self, raw: dict[str, Any], action: str, status_column: str) -> dict[str, Any]:
        sql_id, space_nm = self._sql_identity(raw)
        retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
        set_sql = f"{status_column} = NULL, RETRY_COUNT = :retry_count"
        params: dict[str, Any] = {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count}
        if "priority" in raw and raw.get("priority") not in (None, ""):
            set_sql += ", PRIORITY = :priority"
            params["priority"] = self._int_value(raw.get("priority"), "priority")
        return self._sql_statement(action, sql_id, space_nm, set_sql, params, f"{status_column}=NULL, RETRY_COUNT reset")

    def _sql_retry_failed_statement(self, raw: dict[str, Any], action: str, status_column: str) -> dict[str, Any]:
        """Reset a SQL status only when its current value is FAIL-*.

        The predicate is evaluated at write time, rather than trusting a preceding
        vector search result, so a concurrently completed PASS row is protected.
        """
        sql_id, space_nm = self._sql_identity(raw)
        retry_count = self._int_value(raw.get("retry_count", 0), "retry_count")
        return {
            "action": action,
            "identity": f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
            "summary": f"{status_column}=NULL, RETRY_COUNT reset (only if current status is FAIL-*)",
            "sql": (
                f"UPDATE {self._qualify('NEXT_SQL_INFO')} "
                f"SET {status_column} = NULL, RETRY_COUNT = :retry_count "
                "WHERE UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) "
                "AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm)) "
                f"AND UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"
            ),
            "params": {"sql_id": sql_id, "space_nm": space_nm, "retry_count": retry_count},
            "skip_when_not_matched": True,
        }

    def _sql_ref_seq_statement(self, raw: dict[str, Any], action: str, *, clear: bool) -> dict[str, Any]:
        """Set or clear a user-selected Correct SQL reference by SQL_SEQ.

        REF_SEQ points to an already indexed Correct SQL row.  It neither copies
        the source row nor changes the Correct SQL collection membership.
        """
        target_where, target_params, identity = self._sql_target_locator(raw)
        if clear:
            return {
                "action": action,
                "identity": identity,
                "summary": "REF_SEQ=NULL",
                "sql": f"UPDATE {self._qualify('NEXT_SQL_INFO')} SET REF_SEQ = NULL WHERE {target_where}",
                "params": target_params,
            }

        ref_seq = self._positive_int_value(raw.get("ref_seq"), "ref_seq")
        params = {**target_params, "ref_seq": ref_seq}
        table = self._qualify("NEXT_SQL_INFO")
        return {
            "action": action,
            "identity": identity,
            "summary": f"REF_SEQ={ref_seq}; existing Correct SQL reference selected",
            "sql": (
                f"UPDATE {table} T SET REF_SEQ = :ref_seq "
                f"WHERE {target_where} "
                "AND T.SQL_SEQ <> :ref_seq"
            ),
            "params": params,
        }

    def _validate_ref_seq_in_correct_sql(self, raw: dict[str, Any]) -> None:
        """Require REF_SEQ to identify an active, already-indexed Correct SQL doc.

        The DB row alone is intentionally insufficient: the actual retrieval path
        is the Milvus collection, so a reference is accepted only after that
        collection contains the exact source row.
        """
        ref_seq = self._positive_int_value(raw.get("ref_seq"), "ref_seq")
        table = self._qualify("NEXT_SQL_INFO")
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT 1 FROM {table} WHERE SQL_SEQ = :ref_seq", {"ref_seq": ref_seq})
            row = cur.fetchone()
        if not row:
            raise ValueError(f"REF_SEQ={ref_seq} 대상 SQL_SEQ를 찾을 수 없습니다.")
        if not self._correct_sql_document_exists(ref_seq):
            raise ValueError(
                "지정한 Correct SQL이 벡터 DB에 존재하지 않습니다. "
                "먼저 Correct SQL을 저장하고 VectorDB 동기화를 완료해 주세요."
            )

    def _correct_sql_document_exists(self, sql_seq: int) -> bool:
        config = self._milvus_config()
        self._require_milvus_config(config)
        from pymilvus import MilvusClient

        client = MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )
        collection = config["correct_sql_conversion_collection"]
        if not client.has_collection(collection_name=collection):
            return False
        filter_expression = f"sql_seq == {sql_seq} and is_active == true"
        rows = client.query(
            collection_name=collection,
            filter=filter_expression,
            output_fields=["doc_id"],
            limit=1,
        )
        return bool(rows)

    def _milvus_config(self) -> dict[str, str]:
        return {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "correct_sql_conversion_collection": self._clean_identifier(
                getattr(self, "correct_sql_conversion_collection_name", "")
                or os.getenv("MILVUS_CORRECT_SQL_CONVERSION_COLLECTION")
                or "SM_CORRECT_SQL_CONVERSION"
            ),
        }

    def _require_milvus_config(self, config: dict[str, str]) -> None:
        missing = [key for key in ("uri", "username", "password", "db_name") if not config.get(key)]
        if missing:
            raise ValueError(f"REF_SEQ 검증에 필요한 Milvus 설정이 없습니다: {', '.join(missing)}")

    def _requires_correct_sql_sync(self, actions: list[Any]) -> bool:
        """Return true only when an action can add, modify, or deactivate a hint."""
        relevant_actions = {
            "set_sql_user_edited",
            "reset_sql_conversion_status",
            "retry_failed_sql_conversion",
            "clear_sql_to_sql",
            "clear_sql_bind_sql",
            "clear_sql_test_sql",
            "save_sql_to_sql",
            "save_sql_bind_sql",
            "save_sql_test_sql",
        }
        return any(
            isinstance(raw, dict) and str(raw.get("action") or "").strip().lower() in relevant_actions
            for raw in actions
        )

    def _sync_correct_sql_vector_db(self) -> dict[str, Any]:
        """Run the existing one-shot sync after the Oracle transaction has committed.

        This is deliberately best-effort: Oracle and Milvus do not share one
        transaction, so a sync failure is reported without pretending the DB write
        was rolled back.
        """
        try:
            source = Path(__file__).with_name("04_saveVectorDB.py")
            spec = importlib.util.spec_from_file_location("smartmigrate_save_vector_db", source)
            if not spec or not spec.loader:
                raise RuntimeError("04_saveVectorDB.py module could not be loaded")
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            sync_component = module.NewType04SaveVectorDB()
            for name in (
                "db_host", "db_port", "db_service_name", "db_username", "db_password", "system_schema",
                "milvus_uri", "milvus_username", "milvus_password", "milvus_db_name",
                "correct_sql_conversion_collection_name", "rag_embed_base_url", "rag_embed_api_key",
                "rag_embed_model", "rag_embed_timeout_seconds",
            ):
                setattr(sync_component, name, getattr(self, name, None))
            message = sync_component.run()
            status = dict(getattr(sync_component, "status", {}) or {})
            if status.get("ok"):
                return {"ok": True, "summary": "completed", "details": status, "message": str(getattr(message, "text", ""))}
            return {"ok": False, "summary": "failed; Oracle update was committed", "details": status, "message": str(getattr(message, "text", ""))}
        except Exception as exc:
            return {"ok": False, "summary": f"failed; Oracle update was committed ({exc})"}

    def _sql_target_locator(self, raw: dict[str, Any]) -> tuple[str, dict[str, Any], str]:
        """Allow SQL_SEQ for new management actions while retaining the PK path."""
        if raw.get("sql_seq") not in (None, ""):
            sql_seq = self._positive_int_value(raw.get("sql_seq"), "sql_seq")
            return "SQL_SEQ = :target_sql_seq", {"target_sql_seq": sql_seq}, f"SQL_SEQ={sql_seq}"
        sql_id, space_nm = self._sql_identity(raw)
        return (
            "UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))",
            {"sql_id": sql_id, "space_nm": space_nm},
            f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
        )

    # migration row는 MAP_ID 하나가 갱신 단위다. 실행 전 rowcount=1 검증은 _apply_actions가 담당한다.
    def _migration_statement(self, action: str, map_id: str, set_sql: str, params: dict[str, Any], summary: str) -> dict[str, Any]:
        params.setdefault("map_id", map_id)
        return {
            "action": action,
            "identity": f"MAP_ID={map_id}",
            "summary": summary,
            "sql": f"UPDATE {self._qualify('NEXT_MIG_INFO')} SET {set_sql} WHERE TO_CHAR(MAP_ID) = :map_id",
            "params": params,
        }

    # SQL row의 안정적인 식별자는 SQL_ID 단독이 아니라 SPACE_NM + SQL_ID 조합이다.
    def _sql_statement(self, action: str, sql_id: str, space_nm: str, set_sql: str, params: dict[str, Any], summary: str) -> dict[str, Any]:
        params.setdefault("sql_id", sql_id)
        params.setdefault("space_nm", space_nm)
        return {
            "action": action,
            "identity": f"SQL_ID={sql_id}, SPACE_NM={space_nm}",
            "summary": summary,
            "sql": (
                f"UPDATE {self._qualify('NEXT_SQL_INFO')} SET {set_sql} "
                "WHERE UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id)) "
                "AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))"
            ),
            "params": params,
        }

    def _map_id(self, raw: dict[str, Any]) -> str:
        value = str(raw.get("map_id") or "").strip()
        if not value:
            raise ValueError("map_id is required")
        return value

    def _sql_identity(self, raw: dict[str, Any]) -> tuple[str, str]:
        if raw.get("sql_seq") not in (None, ""):
            sql_seq = self._positive_int_value(raw.get("sql_seq"), "sql_seq")
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"SELECT SQL_ID, SPACE_NM FROM {self._qualify('NEXT_SQL_INFO')} WHERE SQL_SEQ = :sql_seq",
                    {"sql_seq": sql_seq},
                )
                row = cur.fetchone()
            if not row:
                raise ValueError(f"SQL_SEQ={sql_seq} 대상 SQL을 찾을 수 없습니다.")
            return str(row[0] or "").strip(), str(row[1] or "").strip()
        sql_id = str(raw.get("sql_id") or "").strip()
        space_nm = str(raw.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError("sql_id and space_nm are required")
        return sql_id, space_nm

    def _yn_value(self, value: Any, name: str) -> str:
        text = str(value or "").strip().upper()
        if text not in {"Y", "N"}:
            raise ValueError(f"{name} must be Y or N")
        return text

    def _int_value(self, value: Any, name: str) -> int:
        try:
            return int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be a number") from exc

    def _positive_int_value(self, value: Any, name: str) -> int:
        parsed = self._int_value(value, name)
        if parsed <= 0:
            raise ValueError(f"{name} must be a positive number")
        return parsed

    def _text_value(self, raw: dict[str, Any], *names: str) -> str:
        for name in names:
            if name in raw and raw.get(name) is not None:
                return str(raw.get(name))
        raise ValueError(f"One of {', '.join(names)} is required")

    def _answer(self, results: list[dict[str, Any]]) -> str:
        lines = [f"Update Command completed: {len(results)} update(s) in one transaction."]
        for item in results:
            lines.append(f"- {item['identity']}: {item['summary']} ({item['action']})")
        return "\n".join(lines)

    @contextmanager
    # 쓰기 connection은 contextmanager 종료 시 닫고, commit/rollback은 호출자가 명시적으로 제어한다.
    def _connect(self):
        import oracledb

        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret_to_str(getattr(self, "db_password", None)),
            dsn=oracledb.makedsn(
                str(getattr(self, "db_host", "") or "").strip(),
                int(getattr(self, "db_port", None) or 1521),
                service_name=str(getattr(self, "db_service_name", "") or "").strip(),
            ),
        )
        try:
            yield conn
        finally:
            conn.close()

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if not raw:
            raw = getattr(self, "payload_json", "")
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required")
        return f"{self._clean_identifier(schema)}.{table}"

    def _clean_identifier(self, value: Any) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
