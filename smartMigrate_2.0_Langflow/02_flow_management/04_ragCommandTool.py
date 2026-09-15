from __future__ import annotations

import json
import logging
import os
import re
import urllib.request
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType04RagCommandTool(Component):
    display_name = "04 RAG Command Tool"
    description = "Tool-mode RAG Guide query/add/update/disable command component."
    name = "NewType04RagCommandTool"
    icon = "BookMarked"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=False,
            tool_mode=True,
            info='Example: {"action":"query","category":"SQL_CONVERSION","keyword":"CUSTOMER","limit":10}',
        ),
        DataInput(name="payload_json", display_name="Payload JSON", required=False),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        IntInput(name="default_limit", display_name="Default Limit", value=10, required=False),
        IntInput(name="max_text_chars", display_name="Max Text Chars", value=4000, required=False),
        StrInput(name="milvus_uri", display_name="Milvus URI", required=False),
        StrInput(name="milvus_username", display_name="Milvus Username", required=False),
        SecretStrInput(name="milvus_password", display_name="Milvus Password", required=False),
        StrInput(name="milvus_db_name", display_name="Milvus DB Name", value="default", required=False),
        StrInput(name="asis_sql_collection_name", display_name="AS-IS SQL Collection Name", value="SM_ASIS_SQL", required=False),
        StrInput(name="rag_embed_base_url", display_name="RAG Embedding Base URL", required=False),
        SecretStrInput(name="rag_embed_api_key", display_name="RAG Embedding API Key", required=False),
        StrInput(name="rag_embed_model", display_name="RAG Embedding Model", value="BAAI/bge-m3", required=False),
        IntInput(name="rag_embed_timeout_seconds", display_name="RAG Embedding Timeout Seconds", value=60, required=False),
    ]
    outputs = [Output(display_name="Result", name="result", method="run_command")]

    CATEGORIES = {"SQL_CONVERSION", "SQL_TUNING"}
    RULE_TYPES = {"GENERAL", "SEARCH"}

    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 RAG Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_RAG_COMMAND_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            action = str(command.get("action") or "").strip().lower() or "query"
            with self._connect() as conn:
                if action in {"query", "list", "get"}:
                    result = self._query(conn, command)
                elif action in {"search_similar_asis_sql", "find_similar_asis_sql", "similar_asis_sql"}:
                    result = self._search_similar_asis_sql(conn, command)
                elif action in {"add", "insert", "create"}:
                    result = self._insert(conn, command)
                elif action in {"update", "modify"}:
                    result = self._update(conn, command)
                elif action in {"disable", "delete", "soft_delete"}:
                    result = self._disable(conn, self._required_rag_id(command))
                else:
                    raise ValueError(f"Unsupported RAG action: {action}")
                conn.commit()
            result = {**result, "component": "04_ragCommandTool", "answer_text": self._answer(result), "final": True}
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_ragCommandTool", "error": str(exc), "answer_text": f"RAG Command failed: {exc}"}
            self.status = result
            return Data(data=result)

    def _query(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rag_id = str(command.get("rag_id") or "").strip()
        category = self._optional_category(command.get("category"))
        rule_type = self._optional_rule_type(command.get("rule_type"))
        use_yn = self._optional_use_yn(command.get("use_yn"))
        keyword = str(command.get("keyword") or "").strip()
        limit = self._limit(command.get("limit"))
        full_text = self._as_bool(command.get("full_text"))

        conditions = ["1=1"]
        params: dict[str, Any] = {"limit": limit}
        if rag_id:
            conditions.append("RAG_ID = :rag_id")
            params["rag_id"] = int(rag_id)
        if category:
            conditions.append("UPPER(TRIM(CATEGORY)) = :category")
            params["category"] = category
        if rule_type:
            conditions.append("UPPER(TRIM(RULE_TYPE)) = :rule_type")
            params["rule_type"] = rule_type
        if use_yn:
            conditions.append("UPPER(TRIM(USE_YN)) = :use_yn")
            params["use_yn"] = use_yn
        if keyword:
            conditions.append(
                "("
                "UPPER(TO_CHAR(SOURCE_TABLES)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(GUIDANCE_TEXT, 4000, 1)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(SOURCE_SQL, 4000, 1)) LIKE UPPER(:keyword) OR "
                "UPPER(DBMS_LOB.SUBSTR(TARGET_SQL, 4000, 1)) LIKE UPPER(:keyword)"
                ")"
            )
            params["keyword"] = f"%{keyword}%"

        text_length = min(self._positive_int(getattr(self, "max_text_chars", None), 4000), 4000)
        guidance_expr = "GUIDANCE_TEXT" if full_text else f"DBMS_LOB.SUBSTR(GUIDANCE_TEXT, {text_length}, 1)"
        source_expr = "SOURCE_SQL" if full_text else f"DBMS_LOB.SUBSTR(SOURCE_SQL, {text_length}, 1)"
        target_expr = "TARGET_SQL" if full_text else f"DBMS_LOB.SUBSTR(TARGET_SQL, {text_length}, 1)"
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT *
              FROM (
                    SELECT RAG_ID, CATEGORY, RULE_TYPE, SOURCE_TABLES, USE_YN,
                           {guidance_expr} AS GUIDANCE_TEXT,
                           {source_expr} AS SOURCE_SQL,
                           {target_expr} AS TARGET_SQL,
                           NVL(HIT_CNT, 0) AS HIT_CNT,
                           TO_CHAR(CREATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS CREATED_AT,
                           TO_CHAR(UPDATED_AT, 'YYYY-MM-DD HH24:MI:SS') AS UPDATED_AT
                      FROM {self._qualify("NEXT_MIG_RAG_INFO")}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY UPDATED_AT DESC NULLS LAST, RAG_ID DESC
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
        )
        names = [str(item[0]).lower() for item in cur.description]
        rows = [{names[index]: self._json_value(value) for index, value in enumerate(row)} for row in cur.fetchall()]
        return {"ok": True, "action": "query", "row_count": len(rows), "data": {"rules": rows}, "needs_vector_sync": False}

    def _search_similar_asis_sql(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        """Find AS-IS SQL neighbours, then filter with the authoritative Oracle status.

        The AS-IS collection deliberately has no status metadata.  This avoids a
        sync delay accidentally offering a now-PASS job for retry.
        """
        query_sql, query_source, query_identity = self._similarity_query_sql(conn, command)
        status_filter = self._status_filter(command.get("status_filter") or command.get("filter"))
        status_scope = self._status_scope(command.get("status_scope") or command.get("domain"))
        limit = self._limit(command.get("limit"))
        candidate_limit = max(limit, min(self._positive_int(command.get("candidate_limit"), max(limit * 5, 50)), 100))

        vector = self._embed_texts([self._sql_content(query_sql)], self._embed_config())[0]
        client = self._milvus_client(self._milvus_config())
        hits = client.search(
            collection_name=self._milvus_config()["asis_sql_collection"],
            data=[vector],
            anns_field="dense_vector",
            limit=candidate_limit,
            filter="is_active == true",
            output_fields=["sql_id", "space_nm", "tag_kind", "target_table"],
            search_params={"metric_type": "COSINE", "params": {}},
        )
        candidates = self._milvus_hits(hits)
        statuses = self._load_sql_statuses(conn, candidates)
        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            identity = self._identity_key(candidate.get("sql_id"), candidate.get("space_nm"))
            if query_identity and identity == query_identity and not self._as_bool(command.get("include_self")):
                continue
            status = statuses.get(identity)
            if not status:
                continue
            matched_statuses = self._matching_statuses(status, status_scope, status_filter)
            if not matched_statuses:
                continue
            matches.append(
                {
                    **candidate,
                    "status_conversion": status["status_conversion"],
                    "status_tuning": status["status_tuning"],
                    "matched_statuses": matched_statuses,
                    "retry_actions": self._retry_actions(candidate, matched_statuses) if status_filter == "FAIL_ONLY" else [],
                }
            )
            if len(matches) >= limit:
                break
        return {
            "ok": True,
            "action": "search_similar_asis_sql",
            "query_source": query_source,
            "status_filter": status_filter,
            "status_scope": status_scope,
            "candidate_count": len(candidates),
            "row_count": len(matches),
            "data": {"similar_sqls": matches},
            "confirmation_required": status_filter == "FAIL_ONLY" and bool(matches),
            "confirmation_message": (
                "The listed FAIL-* rows can be reset to NULL for retry. Confirm before running the supplied retry_actions; PASS rows are never included."
                if status_filter == "FAIL_ONLY" and matches
                else "No status change has been made."
            ),
        }

    def _similarity_query_sql(self, conn: Any, command: dict[str, Any]) -> tuple[str, str, tuple[str, str] | None]:
        for field in ("query_sql", "sql", "fr_sql", "edit_fr_sql"):
            value = str(command.get(field) or "").strip()
            if value:
                return value, field, None
        sql_id = str(command.get("sql_id") or "").strip()
        space_nm = str(command.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError("query_sql (or sql/fr_sql) or both sql_id and space_nm are required")
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT EDIT_FR_SQL, FR_SQL
              FROM {self._qualify('NEXT_SQL_INFO')}
             WHERE UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id))
               AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))
            """,
            {"sql_id": sql_id, "space_nm": space_nm},
        )
        row = cur.fetchone()
        if not row:
            raise ValueError(f"NEXT_SQL_INFO row not found: SQL_ID={sql_id}, SPACE_NM={space_nm}")
        sql_text = self._json_value(row[0]) or self._json_value(row[1]) or ""
        if not str(sql_text).strip():
            raise ValueError("The selected NEXT_SQL_INFO row has neither EDIT_FR_SQL nor FR_SQL")
        return str(sql_text).strip(), "sql_id+space_nm", self._identity_key(sql_id, space_nm)

    def _load_sql_statuses(self, conn: Any, candidates: list[dict[str, Any]]) -> dict[tuple[str, str], dict[str, str]]:
        if not candidates:
            return {}
        conditions = []
        params: dict[str, Any] = {}
        for index, candidate in enumerate(candidates):
            sql_id = str(candidate.get("sql_id") or "").strip()
            space_nm = str(candidate.get("space_nm") or "").strip()
            if not sql_id or not space_nm:
                continue
            conditions.append(f"(UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id_{index})) AND UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm_{index})))")
            params[f"sql_id_{index}"] = sql_id
            params[f"space_nm_{index}"] = space_nm
        if not conditions:
            return {}
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT SQL_ID, SPACE_NM, STATUS_CONVERSION, STATUS_TUNING
              FROM {self._qualify('NEXT_SQL_INFO')}
             WHERE {' OR '.join(conditions)}
            """,
            params,
        )
        return {
            self._identity_key(row[0], row[1]): {
                "status_conversion": str(self._json_value(row[2]) or "").strip().upper(),
                "status_tuning": str(self._json_value(row[3]) or "").strip().upper(),
            }
            for row in cur.fetchall()
        }

    def _milvus_hits(self, response: Any) -> list[dict[str, Any]]:
        raw_hits = response[0] if isinstance(response, list) and response and isinstance(response[0], list) else response
        if not isinstance(raw_hits, list):
            return []
        result = []
        for hit in raw_hits:
            if not isinstance(hit, dict):
                continue
            entity = hit.get("entity") if isinstance(hit.get("entity"), dict) else hit
            sql_id = str(entity.get("sql_id") or "").strip()
            space_nm = str(entity.get("space_nm") or "").strip()
            if not sql_id or not space_nm:
                continue
            result.append({
                "sql_id": sql_id,
                "space_nm": space_nm,
                "tag_kind": str(entity.get("tag_kind") or "").strip(),
                "target_table": str(entity.get("target_table") or "").strip(),
                "similarity": float(hit.get("distance", hit.get("score", 0.0)) or 0.0),
            })
        return result

    def _status_filter(self, value: Any) -> str:
        normalized = str(value or "FAIL_ONLY").strip().upper().replace("-", "_")
        aliases = {"FAIL": "FAIL_ONLY", "FAILED": "FAIL_ONLY", "PASS": "PASS_ONLY", "ALL": "ALL", "ANY": "ALL"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"FAIL_ONLY", "PASS_ONLY", "ALL"}:
            raise ValueError("status_filter must be FAIL_ONLY, PASS_ONLY, or ALL")
        return normalized

    def _status_scope(self, value: Any) -> str:
        normalized = str(value or "CONVERSION").strip().upper().replace("-", "_")
        aliases = {"SQL_CONVERSION": "CONVERSION", "SQL_TUNING": "TUNING", "BOTH": "ANY", "ALL": "ANY"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"CONVERSION", "TUNING", "ANY"}:
            raise ValueError("status_scope must be CONVERSION, TUNING, or ANY")
        return normalized

    def _matching_statuses(self, status: dict[str, str], scope: str, status_filter: str) -> list[str]:
        columns = {"CONVERSION": status.get("status_conversion", ""), "TUNING": status.get("status_tuning", "")}
        selected = ("CONVERSION", "TUNING") if scope == "ANY" else (scope,)
        if status_filter == "ALL":
            return [name for name in selected]
        prefix = "FAIL-" if status_filter == "FAIL_ONLY" else "PASS"
        return [name for name in selected if columns[name].startswith(prefix)]

    def _retry_actions(self, candidate: dict[str, Any], matched_statuses: list[str]) -> list[dict[str, str]]:
        return [
            {
                "action": "retry_failed_sql_conversion" if status_name == "CONVERSION" else "retry_failed_sql_tuning",
                "sql_id": str(candidate["sql_id"]),
                "space_nm": str(candidate["space_nm"]),
            }
            for status_name in matched_statuses
        ]

    def _identity_key(self, sql_id: Any, space_nm: Any) -> tuple[str, str]:
        return str(sql_id or "").strip().upper(), str(space_nm or "").strip().upper()

    def _insert(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rule = self._normalized_rule(command, require_content=True, partial=False)
        cur = conn.cursor()
        cur.setinputsizes(guidance_text=self._clob_type(), source_sql=self._clob_type(), target_sql=self._clob_type())
        rag_id_var = cur.var(self._number_type())
        cur.execute(
            f"""
            INSERT INTO {self._qualify("NEXT_MIG_RAG_INFO")}
                (CATEGORY, RULE_TYPE, SOURCE_TABLES, USE_YN, GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL)
            VALUES
                (:category, :rule_type, :source_tables, :use_yn, :guidance_text, :source_sql, :target_sql)
            RETURNING RAG_ID INTO :rag_id
            """,
            {**rule, "rag_id": rag_id_var},
        )
        return {"ok": True, "action": "add", "rag_id": int(rag_id_var.getvalue()[0]), "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _update(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        rag_id = self._required_rag_id(command)
        rule = self._normalized_rule(command, require_content=False, partial=True)
        assignments = []
        params: dict[str, Any] = {"rag_id": rag_id}
        for key, column in (
            ("category", "CATEGORY"),
            ("rule_type", "RULE_TYPE"),
            ("source_tables", "SOURCE_TABLES"),
            ("use_yn", "USE_YN"),
            ("guidance_text", "GUIDANCE_TEXT"),
            ("source_sql", "SOURCE_SQL"),
            ("target_sql", "TARGET_SQL"),
        ):
            if key in rule:
                assignments.append(f"{column} = :{key}")
                params[key] = rule[key]
        if not assignments:
            raise ValueError("No RAG fields to update")
        assignments.append("UPDATED_AT = SYSTIMESTAMP")
        cur = conn.cursor()
        cur.setinputsizes(**{key: self._clob_type() for key in ("guidance_text", "source_sql", "target_sql") if key in params})
        cur.execute(f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET {', '.join(assignments)} WHERE RAG_ID = :rag_id", params)
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row not found. rowcount={cur.rowcount}")
        return {"ok": True, "action": "update", "rag_id": rag_id, "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _disable(self, conn: Any, rag_id: int) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET USE_YN = 'N', UPDATED_AT = SYSTIMESTAMP WHERE RAG_ID = :rag_id", {"rag_id": rag_id})
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row not found. rowcount={cur.rowcount}")
        return {"ok": True, "action": "disable", "rag_id": rag_id, "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    def _answer(self, result: dict[str, Any]) -> str:
        action = result.get("action")
        if action == "query":
            return f"RAG Guide query completed: {result.get('row_count', 0)} row(s)."
        if action == "search_similar_asis_sql":
            return (
                f"AS-IS SQL similarity search completed: {result.get('row_count', 0)} row(s) "
                f"matched with status_filter={result.get('status_filter')}. "
                "No status was changed."
            )
        rag_id = result.get("rag_id")
        sync_sentence = "RAG Guide와 Correct SQL을 VectorDB에 동기화해줘"
        if rag_id:
            sync_sentence = f"RAG_ID {rag_id} 변경분을 포함해서 RAG Guide와 Correct SQL을 VectorDB에 동기화해줘"
        return (
            f"RAG Guide {action} completed: RAG_ID={rag_id}, updated_rows={result.get('updated_rows', 0)}.\n"
            f"VectorDB 반영은 자동으로 실행하지 않았습니다. 반영하려면 \"{sync_sentence}\"라고 요청하세요."
        )

    def _normalized_rule(self, command: dict[str, Any], *, require_content: bool, partial: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not partial or "category" in command:
            result["category"] = self._required_category(command.get("category"))
        if not partial or "rule_type" in command:
            result["rule_type"] = self._required_rule_type(command.get("rule_type") or "SEARCH")
        if not partial or "source_tables" in command:
            result["source_tables"] = self._source_tables(command.get("source_tables"))
        if not partial or "use_yn" in command:
            result["use_yn"] = self._required_use_yn(command.get("use_yn") or "Y")
        for key in ("guidance_text", "source_sql", "target_sql"):
            if not partial or key in command:
                result[key] = str(command.get(key) or "").strip()
        if require_content and not any(str(result.get(key) or "").strip() for key in ("guidance_text", "source_sql", "target_sql")):
            raise ValueError("At least one of guidance_text, source_sql, target_sql is required")
        self._validate_rule_shape(result, require_complete=require_content and not partial)
        if result.get("category") == "SQL_TUNING":
            result["source_tables"] = ""
        return result

    def _validate_rule_shape(self, rule: dict[str, Any], *, require_complete: bool) -> None:
        category = str(rule.get("category") or "").strip().upper()
        rule_type = str(rule.get("rule_type") or "").strip().upper()
        source_tables = str(rule.get("source_tables") or "").strip()
        guidance_text = str(rule.get("guidance_text") or "").strip()
        source_sql = str(rule.get("source_sql") or "").strip()
        target_sql = str(rule.get("target_sql") or "").strip()
        if category == "SQL_CONVERSION" and require_complete and not source_tables:
            raise ValueError("SQL_CONVERSION RAG requires source_tables")
        if category == "SQL_TUNING" and source_tables:
            raise ValueError("SQL_TUNING RAG does not use source_tables")
        if rule_type == "SEARCH" and require_complete and (not source_sql or not target_sql):
            raise ValueError("SEARCH RAG requires both source_sql and target_sql")
        if bool(source_sql) != bool(target_sql):
            raise ValueError("source_sql and target_sql must be both present or both empty")
        if category == "SQL_TUNING" and require_complete and not guidance_text:
            raise ValueError("SQL_TUNING RAG requires guidance_text")
        if rule_type == "GENERAL" and require_complete and not guidance_text:
            raise ValueError("GENERAL RAG requires guidance_text")

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if not raw:
            raw = getattr(self, "payload_json", "")
        if isinstance(raw, Data):
            parsed = dict(raw.data or {})
        elif isinstance(raw, dict):
            parsed = dict(raw)
        else:
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
            parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        if "rag" in parsed and isinstance(parsed["rag"], dict):
            merged = {**parsed["rag"], **{key: value for key, value in parsed.items() if key != "rag"}}
            return merged
        return parsed

    @contextmanager
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

    def _required_rag_id(self, command: dict[str, Any]) -> int:
        try:
            rag_id = int(str(command.get("rag_id") or "").strip())
        except (TypeError, ValueError):
            rag_id = 0
        if rag_id <= 0:
            raise ValueError("rag_id is required")
        return rag_id

    def _required_category(self, value: Any) -> str:
        category = self._optional_category(value)
        if not category:
            raise ValueError("category must be SQL_CONVERSION or SQL_TUNING")
        return category

    def _optional_category(self, value: Any) -> str:
        category = str(value or "").strip().upper()
        return category if category in self.CATEGORIES else ""

    def _required_rule_type(self, value: Any) -> str:
        rule_type = self._optional_rule_type(value)
        if not rule_type:
            raise ValueError("rule_type must be GENERAL or SEARCH")
        return rule_type

    def _optional_rule_type(self, value: Any) -> str:
        rule_type = str(value or "").strip().upper()
        return rule_type if rule_type in self.RULE_TYPES else ""

    def _required_use_yn(self, value: Any) -> str:
        use_yn = self._optional_use_yn(value)
        if not use_yn:
            raise ValueError("use_yn must be Y or N")
        return use_yn

    def _optional_use_yn(self, value: Any) -> str:
        use_yn = str(value or "").strip().upper()
        return use_yn if use_yn in {"Y", "N"} else ""

    def _source_tables(self, value: Any) -> str:
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        return str(value or "").strip().upper()

    def _limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "default_limit", None), 10)
        return max(1, min(self._positive_int(value, default), 100))

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value or 0)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    def _milvus_config(self) -> dict[str, str]:
        config = {
            "uri": str(getattr(self, "milvus_uri", "") or os.getenv("MILVUS_URI") or "").strip(),
            "username": str(getattr(self, "milvus_username", "") or os.getenv("MILVUS_USERNAME") or "").strip(),
            "password": self._secret_to_str(getattr(self, "milvus_password", None)) or str(os.getenv("MILVUS_PASSWORD") or ""),
            "db_name": str(getattr(self, "milvus_db_name", "") or os.getenv("MILVUS_DB_NAME") or "default").strip(),
            "asis_sql_collection": str(getattr(self, "asis_sql_collection_name", "") or os.getenv("MILVUS_ASIS_SQL_COLLECTION") or "SM_ASIS_SQL").strip(),
        }
        missing = [key for key in ("uri", "username", "password", "db_name", "asis_sql_collection") if not config[key]]
        if missing:
            raise ValueError(f"missing Milvus config for AS-IS SQL search: {', '.join(missing)}")
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", config["asis_sql_collection"]):
            raise ValueError("Invalid AS-IS SQL collection name")
        return config

    def _milvus_client(self, config: dict[str, str]) -> Any:
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    def _embed_config(self) -> dict[str, Any]:
        config = {
            "base_url": str(getattr(self, "rag_embed_base_url", "") or os.getenv("RAG_EMBED_BASE_URL") or "").strip(),
            "api_key": self._secret_to_str(getattr(self, "rag_embed_api_key", None)) or str(os.getenv("RAG_EMBED_API_KEY") or "").strip(),
            "model": str(getattr(self, "rag_embed_model", "") or os.getenv("RAG_EMBED_MODEL") or "BAAI/bge-m3").strip(),
            "timeout_seconds": self._positive_int(getattr(self, "rag_embed_timeout_seconds", None) or os.getenv("RAG_EMBED_TIMEOUT_SEC"), 60),
        }
        if not config["base_url"] or not config["model"]:
            raise ValueError("rag_embed_base_url and rag_embed_model are required for AS-IS SQL similarity search")
        return config

    def _embed_texts(self, texts: list[str], config: dict[str, Any]) -> list[list[float]]:
        base_url = str(config["base_url"]).rstrip("/")
        endpoint = base_url if base_url.endswith("/embeddings") else f"{base_url}/embeddings" if base_url.endswith("/v1") else f"{base_url}/v1/embeddings"
        headers = {"Content-Type": "application/json"}
        if config["api_key"]:
            headers["Authorization"] = f"Bearer {config['api_key']}"
        request = urllib.request.Request(
            endpoint,
            data=json.dumps({"model": config["model"], "input": texts}).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=int(config["timeout_seconds"])) as response:
            body = json.loads(response.read().decode("utf-8"))
        data = body.get("data") if isinstance(body, dict) else None
        vectors = [[float(value) for value in item["embedding"]] for item in data if isinstance(item, dict) and isinstance(item.get("embedding"), list)] if isinstance(data, list) else []
        if len(vectors) != len(texts):
            raise ValueError(f"embedding response count mismatch: expected={len(texts)}, actual={len(vectors)}")
        return vectors

    def _sql_content(self, sql_text: str) -> str:
        source = str(sql_text or "").strip()
        normalized = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
        normalized = re.sub(r"--[^\n]*", " ", normalized)
        normalized = re.sub(r"'(?:''|[^'])*'", " STR ", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip().upper()
        return "\n".join(part for part in (normalized, source) if part).strip()

    def _qualify(self, table: str) -> str:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required")
        return f"{self._clean_identifier(schema)}.{self._clean_identifier(table)}"

    def _clean_identifier(self, value: str) -> str:
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

    def _clob_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_CLOB", getattr(oracledb, "CLOB", None))

    def _number_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_NUMBER", int)
