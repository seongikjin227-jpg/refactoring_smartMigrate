from __future__ import annotations

import json
import logging
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data


class NewType04JobQaCommandTool(Component):
    display_name = "04 Job QA Command Tool"
    description = "Read-only DB lookup tool for Job QA Agent. Uses NEXT_MIG_LOG as the single log source."
    name = "NewType04JobQaCommandTool"
    icon = "Search"

    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info=(
                "Read-only JSON command. Examples: "
                '{"action":"get_migration_job","map_id":101}, '
                '{"action":"get_sql_job","sql_id":"Q001","space_nm":"SALES"}, '
                '{"action":"get_sql_text","sql_id":"Q001","space_nm":"SALES","columns":["TO_SQL","TUNED_TO_SQL"]}, '
                '{"action":"search_logs","mig_kind":"SQL_CONVERSION","fail_only":true,"limit":10}, '
                '{"action":"recent_domain_status","domain":"SQL_CONVERSION","limit":10}'
            ),
        ),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
        IntInput(name="default_limit", display_name="Default Limit", value=10, required=False),
        IntInput(name="max_limit", display_name="Max Limit", value=100, required=False),
        IntInput(name="max_text_chars", display_name="Max Text Chars", value=1000, required=False),
        IntInput(name="full_text_row_limit", display_name="Full Text Row Limit", value=3, required=False),
        BoolInput(name="include_sql_text", display_name="Include SQL Text", value=True, required=False),
    ]

    outputs = [Output(display_name="Result", name="result", method="run_command")]

    SQL_DOMAINS = {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}
    ALL_MIG_KINDS = {"DB_MIGRATION", "DB_MIG", "SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING", "WORKFLOW"}
    FAIL_STATUSES = {"FAIL", "FAILED", "ERROR"}
    FULL_SQL_INFO_COLUMNS = {
        "FR_SQL",
        "EDIT_FR_SQL",
        "TARGET_TABLE",
        "TO_SQL",
        "TUNED_TO_SQL",
        "TUNED_RESULT",
        "TUNED_FR_SQL",
        "BIND_SQL",
        "BIND_SET",
        "TEST_SQL",
        "FORMATTED_SQL",
        "BLOCK_RAG_CONTENT",
    }
    FULL_MIG_INFO_COLUMNS = {"FR_TABLE", "TO_TABLE", "CONDITION", "MIG_SQL", "VERIFY_SQL"}
    FULL_LOG_COLUMNS = {"GENERATE_SQL"}
    SQL_TEXT_COLUMNS = {
        "FR_TABLE",
        "TO_TABLE",
        "CONDITION",
        "MIG_SQL",
        "VERIFY_SQL",
        "FR_COL",
        "TO_COL",
        "FR_SQL",
        "EDIT_FR_SQL",
        "TARGET_TABLE",
        "TO_SQL",
        "TUNED_TO_SQL",
        "TUNED_RESULT",
        "TUNED_FR_SQL",
        "BIND_SQL",
        "BIND_SET",
        "TEST_SQL",
        "FORMATTED_SQL",
        "BLOCK_RAG_CONTENT",
        "GENERATE_SQL",
        "GUIDANCE_TEXT",
        "SOURCE_SQL",
        "TARGET_SQL",
    }

    def run_command(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info(
            "04 Job QA Command Tool started",
            extra={"workflow_log": [0, "WORKFLOW", "04_JOB_QA_TOOL", "INFO", "RUN", "START", 0]},
        )
        try:
            command = self._parse_command()
            result = self._dispatch(command)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = {"ok": False, "component": "04_jobQaCommandTool", "error": str(exc)}
            self.status = result
            return Data(data=result)

    def _dispatch(self, command: dict[str, Any]) -> dict[str, Any]:
        action = str(command.get("action") or "").strip().lower()
        if action == "get_migration_job":
            return self._get_migration_job(command)
        if action == "get_sql_job":
            return self._get_sql_job(command)
        if action == "get_sql_mapping_rules":
            return self._get_sql_mapping_rules(command)
        if action == "get_sql_text":
            return self._get_sql_text(command)
        if action == "get_migration_text":
            return self._get_migration_text(command)
        if action == "get_log_text":
            return self._get_log_text(command)
        if action == "search_logs":
            return self._search_logs(command)
        if action == "recent_domain_status":
            return self._recent_domain_status(command)
        if action == "search_jobs":
            return self._search_jobs(command)
        if action == "query_rag_info":
            return self._query_rag_info(command)
        if action in {"schema", "table_columns"}:
            return self._table_columns(command)
        if action in {"help", ""}:
            return {"ok": True, "action": "help", "supported_actions": self._supported_actions()}
        raise ValueError(f"Unsupported action: {action}")

    def _get_migration_job(self, command: dict[str, Any]) -> dict[str, Any]:
        map_id = self._required_text(command, "map_id")
        limit = self._limit(command.get("limit"))
        job = self._query_rows(
            f"SELECT {self._select_list('NEXT_MIG_INFO')} FROM {self._qualify('NEXT_MIG_INFO')} WHERE TO_CHAR(MAP_ID) = :map_id",
            {"map_id": map_id},
            table_name="NEXT_MIG_INFO",
        )
        details = self._query_rows(
            f"""
            SELECT {self._select_list('NEXT_MIG_INFO_DTL')}
              FROM {self._qualify('NEXT_MIG_INFO_DTL')}
             WHERE TO_CHAR(MAP_ID) = :map_id
             ORDER BY MAP_DTL
            """,
            {"map_id": map_id},
            table_name="NEXT_MIG_INFO_DTL",
        )
        logs = self._query_logs(
            {
                "map_id_like": str(command.get("map_id_like") or f"%{map_id}%"),
                "fail_only": bool(command.get("fail_only", False)),
                "limit": limit,
            }
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_migration_job",
            "target": {"map_id": map_id},
            "data": {"job": job, "details": details, "logs": logs},
        }

    def _get_sql_job(self, command: dict[str, Any]) -> dict[str, Any]:
        sql_id = self._required_text(command, "sql_id")
        space_nm = str(command.get("space_nm") or "").strip()
        limit = self._limit(command.get("limit"))
        conditions = ["UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id))"]
        params: dict[str, Any] = {"sql_id": sql_id}
        if space_nm:
            conditions.append("UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))")
            params["space_nm"] = space_nm
        sql_info = self._query_rows(
            f"SELECT {self._select_list('NEXT_SQL_INFO')} FROM {self._qualify('NEXT_SQL_INFO')} WHERE {' AND '.join(conditions)}",
            params,
            table_name="NEXT_SQL_INFO",
        )
        log_command = {
            "mig_kind": command.get("mig_kind") or list(self.SQL_DOMAINS),
            "sql_id": sql_id,
            "space_nm": space_nm,
            "limit": limit,
            "fail_only": bool(command.get("fail_only", False)),
        }
        logs = self._query_logs(log_command)
        mapping_rules = self._sql_mapping_rules(sql_info)
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_sql_job",
            "target": {"sql_id": sql_id, "space_nm": space_nm},
            "data": {"sql_info": sql_info, "mapping_rules": mapping_rules, "logs": logs},
        }

    def _get_sql_mapping_rules(self, command: dict[str, Any]) -> dict[str, Any]:
        sql_id = self._required_text(command, "sql_id")
        space_nm = str(command.get("space_nm") or "").strip()
        conditions = ["UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id))"]
        params: dict[str, Any] = {"sql_id": sql_id}
        if space_nm:
            conditions.append("UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))")
            params["space_nm"] = space_nm
        sql_info = self._query_rows(
            f"SELECT SPACE_NM, SQL_ID, TARGET_TABLE FROM {self._qualify('NEXT_SQL_INFO')} WHERE {' AND '.join(conditions)}",
            params,
            table_name="NEXT_SQL_INFO",
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_sql_mapping_rules",
            "target": {"sql_id": sql_id, "space_nm": space_nm},
            "data": {"sql_info": sql_info, "mapping_rules": self._sql_mapping_rules(sql_info)},
        }

    def _get_sql_text(self, command: dict[str, Any]) -> dict[str, Any]:
        sql_id = self._required_text(command, "sql_id")
        space_nm = str(command.get("space_nm") or "").strip()
        columns = self._requested_columns(
            command.get("columns"),
            default=self._ordered_allowed_sql_text_columns(),
            allowed=self.FULL_SQL_INFO_COLUMNS,
            table_name="NEXT_SQL_INFO",
        )
        conditions = ["UPPER(TRIM(SQL_ID)) = UPPER(TRIM(:sql_id))"]
        params: dict[str, Any] = {"sql_id": sql_id, "limit": self._full_text_limit(command.get("limit"))}
        if space_nm:
            conditions.append("UPPER(TRIM(SPACE_NM)) = UPPER(TRIM(:space_nm))")
            params["space_nm"] = space_nm
        select_columns = ["SPACE_NM", "SQL_ID", *columns]
        rows = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {', '.join(select_columns)}
                      FROM {self._qualify('NEXT_SQL_INFO')}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY UPD_TS DESC NULLS LAST, SPACE_NM, SQL_ID
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_sql_text",
            "target": {"sql_id": sql_id, "space_nm": space_nm, "columns": columns},
            "data": {"rows": rows},
            "full_text": True,
        }

    def _get_migration_text(self, command: dict[str, Any]) -> dict[str, Any]:
        map_id = self._required_text(command, "map_id")
        columns = self._requested_columns(
            command.get("columns"),
            default=["FR_TABLE", "TO_TABLE", "CONDITION", "MIG_SQL", "VERIFY_SQL"],
            allowed=self.FULL_MIG_INFO_COLUMNS,
            table_name="NEXT_MIG_INFO",
        )
        rows = self._query_rows(
            f"""
            SELECT MAP_ID, {', '.join(columns)}
              FROM {self._qualify('NEXT_MIG_INFO')}
             WHERE TO_CHAR(MAP_ID) = :map_id
            """,
            {"map_id": map_id},
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_migration_text",
            "target": {"map_id": map_id, "columns": columns},
            "data": {"rows": rows},
            "full_text": True,
        }

    def _get_log_text(self, command: dict[str, Any]) -> dict[str, Any]:
        log_id = str(command.get("log_id") or "").strip()
        columns = self._requested_columns(
            command.get("columns"),
            default=["GENERATE_SQL"],
            allowed=self.FULL_LOG_COLUMNS,
            table_name="NEXT_MIG_LOG",
        )
        conditions = []
        params: dict[str, Any] = {"limit": self._full_text_limit(command.get("limit"))}
        if log_id:
            conditions.append("TO_CHAR(LOG_ID) = :log_id")
            params["log_id"] = log_id
        if command.get("map_id_like"):
            conditions.append("TO_CHAR(MAP_ID) LIKE :map_id_like")
            params["map_id_like"] = str(command.get("map_id_like")).strip()
        if command.get("mig_kind"):
            conditions.append("UPPER(TRIM(MIG_KIND)) = :mig_kind")
            params["mig_kind"] = self._normalize_domain(command.get("mig_kind"))
        if command.get("status_like"):
            conditions.append("UPPER(TRIM(STATUS)) LIKE :status_like")
            params["status_like"] = str(command.get("status_like")).strip().upper()
        if bool(command.get("fail_only", False)):
            conditions.append(self._failure_status_condition("STATUS"))
        where_clause = " AND ".join(conditions) if conditions else "1=0"
        rows = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT LOG_ID, CREATED_AT, MAP_ID, MIG_KIND, LOG_TYPE, LOG_LEVEL, STEP_NAME, STATUS, MESSAGE, {', '.join(columns)}
                      FROM {self._qualify('NEXT_MIG_LOG')}
                     WHERE {where_clause}
                     ORDER BY CREATED_AT DESC NULLS LAST, LOG_ID DESC NULLS LAST
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "get_log_text",
            "target": {"log_id": log_id, "columns": columns},
            "data": {"rows": rows},
            "full_text": True,
        }

    def _search_logs(self, command: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "search_logs",
            "target": self._public_filter(command),
            "data": {"logs": self._query_logs(command)},
        }

    def _recent_domain_status(self, command: dict[str, Any]) -> dict[str, Any]:
        domain = self._normalize_domain(command.get("domain") or command.get("mig_kind") or "ALL")
        limit = self._limit(command.get("limit"))
        result: dict[str, Any] = {"domain": domain, "limit": limit}
        if domain in {"ALL", "DB_MIGRATION", "DB_MIG"}:
            result["migration_status_counts"] = self._status_counts("NEXT_MIG_INFO", "STATUS", "1=1")
            result["recent_migration_jobs"] = self._recent_jobs("NEXT_MIG_INFO", "MAP_ID", "STATUS", limit)
        if domain in {"ALL", *self.SQL_DOMAINS}:
            result["sql_conversion_status_counts"] = self._status_counts("NEXT_SQL_INFO", "STATUS_CONVERSION", "1=1")
            result["sql_tuning_status_counts"] = self._status_counts("NEXT_SQL_INFO", "STATUS_TUNING", "1=1")
            result["recent_sql_jobs"] = self._recent_jobs("NEXT_SQL_INFO", "SQL_ID", "STATUS_CONVERSION", limit)
        log_filter: dict[str, Any] = {"limit": limit}
        if domain != "ALL":
            log_filter["mig_kind"] = domain
        if bool(command.get("fail_only", True)):
            log_filter["fail_only"] = True
        result["recent_logs"] = self._query_logs(log_filter)
        return {"ok": True, "component": "04_jobQaCommandTool", "action": "recent_domain_status", "data": result}

    def _sql_mapping_rules(self, sql_info_rows: list[dict[str, Any]]) -> dict[str, Any]:
        source_scope_tables: set[str] = set()
        sql_targets = []
        for row in sql_info_rows:
            target_table = str(row.get("target_table") or row.get("TARGET_TABLE") or "").strip()
            if not target_table:
                continue
            sql_targets.append(
                {
                    "space_nm": row.get("space_nm") or row.get("SPACE_NM"),
                    "sql_id": row.get("sql_id") or row.get("SQL_ID"),
                    "target_table": target_table,
                }
            )
            source_scope_tables.update(self._source_tables(target_table))
        if not source_scope_tables:
            return {"source_scope_tables": [], "rules": [], "message": "NEXT_SQL_INFO.TARGET_TABLE is empty; mapping rules were not queried."}

        info_columns = self._available_column_types("NEXT_MIG_INFO")
        detail_columns = self._available_column_types("NEXT_MIG_INFO_DTL")
        if not {"MAP_ID", "STATUS", "MAP_TYPE", "FR_TABLE", "TO_TABLE"}.issubset(info_columns) or not {"MAP_ID", "FR_COL", "TO_COL"}.issubset(detail_columns):
            return {"source_scope_tables": sorted(source_scope_tables), "rules": [], "message": "Mapping rule tables do not have required columns."}

        description_expr = "M.DESCRIPTION" if "DESCRIPTION" in info_columns else "CAST(NULL AS VARCHAR2(4000))"
        condition_expr = "M.CONDITION" if "CONDITION" in info_columns else "CAST(NULL AS VARCHAR2(4000))"
        map_dtl_expr = "D.MAP_DTL" if "MAP_DTL" in detail_columns else "CAST(NULL AS NUMBER)"
        rules = self._query_rows(
            f"""
            SELECT M.MAP_ID,
                   M.MAP_TYPE,
                   M.FR_TABLE,
                   M.TO_TABLE,
                   {description_expr} AS DESCRIPTION,
                   {condition_expr} AS CONDITION,
                   {map_dtl_expr} AS MAP_DTL,
                   D.FR_COL,
                   D.TO_COL
              FROM {self._qualify('NEXT_MIG_INFO')} M
              JOIN {self._qualify('NEXT_MIG_INFO_DTL')} D ON M.MAP_ID = D.MAP_ID
             WHERE UPPER(TRIM(M.STATUS)) = 'PASS'
             ORDER BY M.MAP_ID, D.MAP_DTL
            """,
            {},
            table_name="NEXT_MIG_INFO",
        )
        matched = [
            rule
            for rule in rules
            if self._table_matches(str(rule.get("fr_table") or ""), source_scope_tables)
        ]

        # Group by (MAP_ID, MAP_TYPE, FR_TABLE, TO_TABLE, DESCRIPTION, CONDITION)
        # so table mappings are displayed once with all column mappings listed below.
        grouped: dict[tuple, list[dict[str, Any]]] = {}
        for rule in matched:
            key = (
                rule.get("map_id"),
                rule.get("map_type"),
                rule.get("fr_table"),
                rule.get("to_table"),
                rule.get("description"),
                rule.get("condition"),
            )
            grouped.setdefault(key, []).append(rule)

        # Format grouped rules: one table mapping with all its column mappings nested.
        formatted_rules = []
        for key, col_mappings in grouped.items():
            map_id, map_type, fr_table, to_table, description, condition = key
            base_rule = {
                "map_id": map_id,
                "map_type": map_type,
                "fr_table": fr_table,
                "to_table": to_table,
                "description": description,
                "condition": condition,
                "column_mappings": [
                    {"fr_col": m.get("fr_col"), "to_col": m.get("to_col")}
                    for m in col_mappings
                ]
            }
            formatted_rules.append(base_rule)

        return {
            "sql_targets": sql_targets,
            "source_scope_tables": sorted(source_scope_tables),
            "match_basis": "NEXT_SQL_INFO.TARGET_TABLE -> NEXT_MIG_INFO.FR_TABLE",
            "rules": formatted_rules,
            "message": "" if formatted_rules else "No PASS mapping rule matched the SQL FROM table scope.",
        }

    def _search_jobs(self, command: dict[str, Any]) -> dict[str, Any]:
        domain = self._normalize_domain(command.get("domain") or "ALL")
        keyword = str(command.get("keyword") or "").strip()
        fail_only = bool(command.get("fail_only", False))
        limit = self._limit(command.get("limit"))
        data: dict[str, Any] = {}
        if domain in {"ALL", "DB_MIGRATION", "DB_MIG"}:
            data["migration_jobs"] = self._search_migration_jobs(keyword, fail_only, limit)
        if domain in {"ALL", *self.SQL_DOMAINS}:
            data["sql_jobs"] = self._search_sql_jobs(keyword, domain if domain != "ALL" else "", fail_only, limit)
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "search_jobs",
            "target": {"domain": domain, "keyword": keyword, "fail_only": fail_only, "limit": limit},
            "data": data,
        }

    def _query_rag_info(self, command: dict[str, Any]) -> dict[str, Any]:
        limit = self._limit(command.get("limit"))
        category = str(command.get("category") or "").strip().upper()
        keyword = str(command.get("keyword") or "").strip()
        conditions = ["1=1"]
        params: dict[str, Any] = {}
        if category:
            conditions.append("UPPER(TRIM(CATEGORY)) = :category")
            params["category"] = category
        if "use_yn" in command:
            conditions.append("UPPER(TRIM(USE_YN)) = :use_yn")
            params["use_yn"] = str(command.get("use_yn") or "").strip().upper()
        if keyword:
            conditions.append(
                self._like_any_condition(
                    ["SOURCE_TABLES", "GUIDANCE_TEXT", "SOURCE_SQL", "TARGET_SQL"],
                    "keyword",
                    self._available_column_types("NEXT_MIG_RAG_INFO"),
                )
            )
            params["keyword"] = f"%{keyword.upper()}%"
        rows = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {self._select_list('NEXT_MIG_RAG_INFO')}
                      FROM {self._qualify('NEXT_MIG_RAG_INFO')}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY UPDATED_AT DESC NULLS LAST, RAG_ID DESC
                   )
             WHERE ROWNUM <= :limit
            """,
            {**params, "limit": limit},
            table_name="NEXT_MIG_RAG_INFO",
        )
        return {
            "ok": True,
            "component": "04_jobQaCommandTool",
            "action": "query_rag_info",
            "target": {"category": category, "keyword": keyword, "limit": limit},
            "data": {"rules": rows},
        }

    def _table_columns(self, command: dict[str, Any]) -> dict[str, Any]:
        tables = command.get("tables") or ["NEXT_MIG_INFO", "NEXT_MIG_INFO_DTL", "NEXT_SQL_INFO", "NEXT_MIG_LOG", "NEXT_MIG_RAG_INFO"]
        if isinstance(tables, str):
            tables = [tables]
        data = {}
        for table in tables:
            clean = self._clean_identifier(str(table))
            data[clean] = self._available_column_types(clean)
        return {"ok": True, "component": "04_jobQaCommandTool", "action": "table_columns", "data": data}

    def _query_logs(self, command: dict[str, Any]) -> list[dict[str, Any]]:
        limit = self._limit(command.get("limit"))
        column_types = self._available_column_types("NEXT_MIG_LOG")
        columns = set(column_types)
        conditions = ["1=1"]
        params: dict[str, Any] = {"limit": limit}

        mig_kinds = command.get("mig_kind") or command.get("mig_kinds")
        if mig_kinds:
            if isinstance(mig_kinds, str):
                mig_kinds = [mig_kinds]
            normalized = [self._normalize_domain(item) for item in mig_kinds if str(item or "").strip()]
            normalized = [item for item in normalized if item != "ALL"]
            if normalized:
                placeholders = []
                for index, kind in enumerate(normalized):
                    key = f"mig_kind_{index}"
                    placeholders.append(f":{key}")
                    params[key] = kind
                conditions.append(f"UPPER(TRIM(MIG_KIND)) IN ({', '.join(placeholders)})")

        if command.get("map_id_like"):
            conditions.append("TO_CHAR(MAP_ID) LIKE :map_id_like")
            params["map_id_like"] = str(command.get("map_id_like")).strip()
        if command.get("sql_id"):
            conditions.append(self._like_any_condition(["MAP_ID", "MESSAGE"], "sql_id_like", column_types))
            params["sql_id_like"] = f"%{str(command.get('sql_id')).strip()}%"
        if command.get("space_nm"):
            conditions.append(self._like_any_condition(["MAP_ID", "MESSAGE"], "space_nm_like", column_types))
            params["space_nm_like"] = f"%{str(command.get('space_nm')).strip()}%"
        if command.get("keyword"):
            searchable_columns = ["MAP_ID", "MIG_KIND", "LOG_TYPE", "LOG_LEVEL", "STEP_NAME", "STATUS", "MESSAGE"]
            if self._as_bool(command.get("include_generate_sql_search", False)):
                searchable_columns.append("GENERATE_SQL")
            conditions.append(self._like_any_condition(searchable_columns, "keyword", column_types))
            params["keyword"] = f"%{str(command.get('keyword')).strip()}%"
        if command.get("status_like"):
            conditions.append("UPPER(TRIM(STATUS)) LIKE :status_like")
            params["status_like"] = str(command.get("status_like")).strip().upper()
        if command.get("log_level"):
            conditions.append("UPPER(TRIM(LOG_LEVEL)) = :log_level")
            params["log_level"] = str(command.get("log_level")).strip().upper()
        if command.get("log_type"):
            conditions.append("UPPER(TRIM(LOG_TYPE)) = :log_type")
            params["log_type"] = str(command.get("log_type")).strip().upper()
        if command.get("step_name_like"):
            conditions.append("UPPER(TRIM(STEP_NAME)) LIKE :step_name_like")
            params["step_name_like"] = f"%{str(command.get('step_name_like')).strip().upper()}%"
        if bool(command.get("fail_only", False)):
            conditions.append(self._failure_status_condition("STATUS"))
        if command.get("created_after"):
            conditions.append("CREATED_AT >= TO_TIMESTAMP(:created_after, 'YYYY-MM-DD HH24:MI:SS')")
            params["created_after"] = str(command.get("created_after")).strip()

        excluded_columns = set()
        if not self._as_bool(command.get("include_generate_sql_preview", False)):
            excluded_columns.add("GENERATE_SQL")
        select_list = self._select_list("NEXT_MIG_LOG", excluded_columns=excluded_columns)
        rows = self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {select_list}
                      FROM {self._qualify('NEXT_MIG_LOG')}
                     WHERE {" AND ".join(conditions)}
                     ORDER BY CREATED_AT DESC NULLS LAST, LOG_ID DESC NULLS LAST
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
            table_name="NEXT_MIG_LOG",
        )
        return rows

    def _search_migration_jobs(self, keyword: str, fail_only: bool, limit: int) -> list[dict[str, Any]]:
        column_types = self._available_column_types("NEXT_MIG_INFO")
        columns = set(column_types)
        conditions = []
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            conditions.append(self._like_any_condition(["MAP_ID", "MAP_TYPE", "FR_TABLE", "TO_TABLE", "STATUS", "MIG_SQL", "VERIFY_SQL"], "keyword", column_types))
            params["keyword"] = f"%{keyword}%"
        if fail_only:
            conditions.append(self._failure_status_condition("STATUS"))
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {self._select_list('NEXT_MIG_INFO')}
                      FROM {self._qualify('NEXT_MIG_INFO')}
                     WHERE {where_clause}
                     ORDER BY UPD_TS DESC NULLS LAST, MAP_ID DESC
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
            table_name="NEXT_MIG_INFO",
        )

    def _search_sql_jobs(self, keyword: str, domain: str, fail_only: bool, limit: int) -> list[dict[str, Any]]:
        column_types = self._available_column_types("NEXT_SQL_INFO")
        columns = set(column_types)
        conditions = []
        params: dict[str, Any] = {"limit": limit}
        if keyword:
            conditions.append(self._like_any_condition(["SPACE_NM", "SQL_ID", "TAG_KIND", "FR_SQL", "EDIT_FR_SQL", "TARGET_TABLE", "TO_SQL", "TUNED_TO_SQL", "BIND_SQL", "TEST_SQL", "LOG"], "keyword", column_types))
            params["keyword"] = f"%{keyword}%"
        if fail_only:
            status_column = self._status_column_for_domain(domain or "SQL_CONVERSION")
            if status_column in columns:
                conditions.append(self._failure_status_condition(status_column))
        where_clause = " AND ".join(conditions) if conditions else "1=1"
        return self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {self._select_list('NEXT_SQL_INFO')}
                      FROM {self._qualify('NEXT_SQL_INFO')}
                     WHERE {where_clause}
                     ORDER BY UPD_TS DESC NULLS LAST, SPACE_NM, SQL_ID
                   )
             WHERE ROWNUM <= :limit
            """,
            params,
            table_name="NEXT_SQL_INFO",
        )

    def _recent_jobs(self, table_name: str, id_column: str, status_column: str, limit: int) -> list[dict[str, Any]]:
        columns = set(self._available_column_types(table_name))
        if status_column not in columns:
            return []
        order_column = "UPD_TS" if "UPD_TS" in columns else id_column
        return self._query_rows(
            f"""
            SELECT *
              FROM (
                    SELECT {self._select_list(table_name, include_text=False)}
                      FROM {self._qualify(table_name)}
                     ORDER BY {order_column} DESC NULLS LAST
                   )
             WHERE ROWNUM <= :limit
            """,
            {"limit": limit},
            table_name=table_name,
        )

    def _status_counts(self, table_name: str, status_column: str, where_clause: str) -> dict[str, int]:
        columns = set(self._available_column_types(table_name))
        if status_column not in columns:
            return {}
        rows = self._query_rows(
            f"""
            SELECT NVL(TO_CHAR({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) AS CNT
              FROM {self._qualify(table_name)}
             WHERE {where_clause}
             GROUP BY NVL(TO_CHAR({status_column}), 'NULL')
             ORDER BY CNT DESC, STATUS_VALUE ASC
            """,
            {},
            columns=["status", "count"],
        )
        return {str(row.get("status")): int(row.get("count") or 0) for row in rows}

    def _query_rows(
        self,
        sql: str,
        params: dict[str, Any],
        *,
        table_name: str | None = None,
        columns: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(sql, params or {})
            names = columns or [str(item[0]).lower() for item in cur.description]
            result = []
            for row in cur.fetchall():
                result.append({names[index]: self._json_value(row[index]) for index in range(len(names))})
        _ = table_name
        return result

    def _select_list(
        self,
        table_name: str,
        *,
        include_text: bool | None = None,
        excluded_columns: set[str] | None = None,
    ) -> str:
        column_types = self._available_column_types(table_name)
        if not column_types:
            raise ValueError(f"Table is not available or has no readable columns: {table_name}")
        expressions = []
        exclude = {self._clean_identifier(column) for column in (excluded_columns or set())}
        for column, data_type in column_types.items():
            if column in exclude:
                continue
            if not self._include_column(column, include_text=include_text):
                continue
            if data_type in {"CLOB", "NCLOB"}:
                length = min(self._positive_int(getattr(self, "max_text_chars", None), 1000), 1000)
                expressions.append(f"DBMS_LOB.SUBSTR({column}, {length}, 1) AS {column}")
            else:
                expressions.append(column)
        return ", ".join(expressions)

    def _include_column(self, column: str, *, include_text: bool | None = None) -> bool:
        if include_text is None:
            include_text = self._as_bool(getattr(self, "include_sql_text", True))
        if include_text:
            return True
        return column not in self.SQL_TEXT_COLUMNS

    def _like_any_condition(self, columns: list[str], param_name: str, available_columns: set[str] | dict[str, str]) -> str:
        column_types = available_columns if isinstance(available_columns, dict) else {}
        column_names = set(column_types) if column_types else set(available_columns)
        clauses = []
        for column in columns:
            clean = self._clean_identifier(column)
            if clean not in column_names:
                continue
            if column_types.get(clean) in {"CLOB", "NCLOB"}:
                clauses.append(f"UPPER(DBMS_LOB.SUBSTR({clean}, 1000, 1)) LIKE UPPER(:{param_name})")
            else:
                clauses.append(f"UPPER(TO_CHAR({clean})) LIKE UPPER(:{param_name})")
        if not clauses:
            return "1=0"
        return "(" + " OR ".join(clauses) + ")"

    def _source_tables(self, value: Any) -> set[str]:
        text = str(value or "").strip()
        if text.startswith("["):
            try:
                parsed = json.loads(text)
                if isinstance(parsed, list):
                    text = ",".join(str(item) for item in parsed)
            except json.JSONDecodeError:
                pass
        return {token.split(".")[-1].strip().strip('"').upper() for token in re.split(r"[,;|\s]+", text) if token.strip()}

    def _table_matches(self, table_name: str, candidates: set[str]) -> bool:
        normalized = str(table_name or "").upper()
        return any(re.search(rf"(?<![A-Z0-9_$#]){re.escape(table)}(?![A-Z0-9_$#])", normalized) for table in candidates)

    def _failure_status_condition(self, column: str) -> str:
        clean = self._clean_identifier(column)
        normalized = f"UPPER(TRIM(NVL({clean}, '')))"
        return f"({normalized} IN ('FAIL', 'FAILED', 'ERROR') OR {normalized} LIKE 'FAIL-%')"

    def _status_column_for_domain(self, domain: str) -> str:
        domain = self._normalize_domain(domain)
        if domain == "SQL_TUNING":
            return "STATUS_TUNING"
        return "STATUS_CONVERSION"

    def _available_column_types(self, table_name: str) -> dict[str, str]:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        with self._connect() as conn:
            cur = conn.cursor()
            if schema:
                cur.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM ALL_TAB_COLUMNS WHERE OWNER = :owner AND TABLE_NAME = :table_name ORDER BY COLUMN_ID",
                    {"owner": schema, "table_name": table},
                )
            else:
                cur.execute(
                    "SELECT COLUMN_NAME, DATA_TYPE FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :table_name ORDER BY COLUMN_ID",
                    {"table_name": table},
                )
            rows = cur.fetchall()
        return {str(row[0]).upper(): str(row[1]).upper() for row in rows}

    @contextmanager
    def _connect(self):
        import oracledb

        oracledb.defaults.fetch_lobs = False
        dsn = oracledb.makedsn(
            str(getattr(self, "db_host", "") or "").strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(getattr(self, "db_service_name", "") or "").strip(),
        )
        conn = oracledb.connect(
            user=str(getattr(self, "db_username", "") or "").strip(),
            password=self._secret_to_str(getattr(self, "db_password", None)),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    def _parse_command(self) -> dict[str, Any]:
        raw = getattr(self, "command_json", "")
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            return {"action": "help"}
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text)
        if not isinstance(parsed, dict):
            raise ValueError("command_json must be a JSON object")
        return parsed

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{self._clean_identifier(schema)}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _normalize_domain(self, value: Any) -> str:
        text = str(value or "").strip().upper().replace("-", "_").replace(" ", "_")
        aliases = {
            "MIG": "DB_MIGRATION",
            "DB": "DB_MIGRATION",
            "DB_MIG": "DB_MIGRATION",
            "MIGRATION": "DB_MIGRATION",
            "CONVERSION": "SQL_CONVERSION",
            "SQL": "SQL_CONVERSION",
            "TUNING": "SQL_TUNING",
            "FORMATTING": "SQL_FORMATTING",
            "FORMAT": "SQL_FORMATTING",
            "": "ALL",
        }
        return aliases.get(text, text)

    def _required_text(self, command: dict[str, Any], key: str) -> str:
        value = str(command.get(key) or "").strip()
        if not value:
            raise ValueError(f"{key} is required")
        return value

    def _limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "default_limit", None), 10)
        max_limit = self._positive_int(getattr(self, "max_limit", None), 100)
        return max(1, min(self._positive_int(value, default), max_limit))

    def _full_text_limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "full_text_row_limit", None), 3)
        return max(1, min(self._positive_int(value, default), 10))

    def _requested_columns(
        self,
        raw_columns: Any,
        *,
        default: list[str],
        allowed: set[str],
        table_name: str,
    ) -> list[str]:
        available = set(self._available_column_types(table_name))
        if not raw_columns:
            requested = [column for column in default if column in available]
        elif isinstance(raw_columns, str):
            requested = [raw_columns]
        else:
            requested = list(raw_columns)
        columns = []
        for column in requested:
            clean = self._clean_identifier(str(column))
            if clean not in allowed:
                raise ValueError(f"Column is not allowed for full text lookup: {clean}")
            if clean not in available:
                raise ValueError(f"Column is not available in {table_name}: {clean}")
            columns.append(clean)
        if not columns:
            raise ValueError("At least one column is required")
        return columns

    def _ordered_allowed_sql_text_columns(self) -> list[str]:
        return [
            "FR_SQL",
            "EDIT_FR_SQL",
            "TARGET_TABLE",
            "TO_SQL",
            "TUNED_TO_SQL",
            "TUNED_RESULT",
            "TUNED_FR_SQL",
            "BIND_SQL",
            "BIND_SET",
            "TEST_SQL",
            "FORMATTED_SQL",
            "BLOCK_RAG_CONTENT",
        ]

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
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    def _public_filter(self, command: dict[str, Any]) -> dict[str, Any]:
        allowed = [
            "mig_kind",
            "mig_kinds",
            "map_id_like",
            "sql_id",
            "space_nm",
            "keyword",
            "status_like",
            "log_level",
            "log_type",
            "step_name_like",
            "fail_only",
            "created_after",
            "include_generate_sql_preview",
            "include_generate_sql_search",
            "limit",
        ]
        return {key: command.get(key) for key in allowed if key in command}

    def _supported_actions(self) -> list[dict[str, Any]]:
        return [
            {"action": "get_migration_job", "required": ["map_id"], "optional": ["limit", "fail_only", "map_id_like"]},
            {"action": "get_sql_job", "required": ["sql_id"], "optional": ["space_nm", "mig_kind", "limit", "fail_only"]},
            {"action": "get_sql_mapping_rules", "required": ["sql_id"], "optional": ["space_nm"]},
            {"action": "get_sql_text", "required": ["sql_id"], "optional": ["space_nm", "columns", "limit"]},
            {"action": "get_migration_text", "required": ["map_id"], "optional": ["columns"]},
            {"action": "get_log_text", "optional": ["log_id", "map_id_like", "mig_kind", "status_like", "fail_only", "columns", "limit"]},
            {"action": "search_logs", "optional": ["mig_kind", "map_id_like", "sql_id", "space_nm", "keyword", "status_like", "log_level", "log_type", "step_name_like", "fail_only", "created_after", "include_generate_sql_preview", "include_generate_sql_search", "limit"]},
            {"action": "recent_domain_status", "optional": ["domain", "limit", "fail_only"]},
            {"action": "search_jobs", "optional": ["domain", "keyword", "fail_only", "limit"]},
            {"action": "query_rag_info", "optional": ["category", "keyword", "use_yn", "limit"]},
            {"action": "table_columns", "optional": ["tables"]},
        ]
