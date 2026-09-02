from __future__ import annotations

import logging
import json
import re
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


class NewType06GetRemainingJobs(Component):

    display_name = "06 Get Remaining Jobs"
    description = "Loads dashboard-like remaining counts plus exact target status when requested."
    name = "NewType06GetRemainingJobs"
    icon = "Database"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=False),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=False),
        StrInput(name="db_username", display_name="DB Username", required=False),
        SecretStrInput(name="db_password", display_name="DB Password", required=False),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]

    outputs = [Output(display_name="Payload", name="payload", method="get_remaining_jobs")]

    def get_remaining_jobs(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "START", 0]})
        try:
            try:
                payload = self._parse_payload(getattr(self, "payload_json", ""))
                if not payload.get("should_execute", True):
                    payload.update({"component": "06_getRemainingJobs", "next_node": "chat_output", "final": True})
                    __log_result = Data(data=payload)
                    logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
                    return __log_result

                if not self._has_db_config():
                    raise ValueError("DB connection settings are required for 06 Get Remaining Jobs")

                # 01 LLM owns natural-language target extraction; regex extraction here is only a legacy fallback.
                targets = self._extract_targets(payload)
                with self._connect() as conn:
                    counts = self._load_counts(conn)
                    requested_jobs = self._empty_requested_jobs()
                    target_statuses = self._load_target_statuses(conn, targets)
                    if self._has_exact_target(targets):
                        requested_jobs = self._load_target_jobs(conn, targets)

                summary = {
                    "total": sum(counts.values()),
                    "migration_total": counts["MIG"],
                    "sql_conversion_total": counts["SQL_CONVERSION"],
                    "sql_tuning_total": counts["SQL_TUNING"],
                    "sql_formatting_total": counts["SQL_FORMATTING"],
                }
                payload.update(
                    {
                        "component": "06_getRemainingJobs",
                        "job_availability": summary,
                        "requested_jobs": requested_jobs,
                        "requested_target_status": target_statuses,
                        "remaining_summary": summary,
                        "pending_summary": summary,
                        "target_filter": payload.get("target_filter") or targets,
                        "job_detail_mode": "requested_jobs" if self._has_exact_target(targets) else "counts_only",
                        "next_node": "08_jobExecutionRouter",
                    }
                )
                payload.setdefault("history", []).append(
                    {
                        "step": "get_remaining_jobs",
                        "message": (
                            f"total={summary['total']}, mig={summary['migration_total']}, "
                            f"sql_conversion={summary['sql_conversion_total']}, detail={payload['job_detail_mode']}"
                        ),
                    }
                )
                self.status = payload
                __log_result = Data(data=payload)
                logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
                return __log_result
            except Exception as exc:
                result = {"ok": False, "component": "06_getRemainingJobs", "error": str(exc)}
                self.status = result
                __log_result = Data(data=result)
                logging.getLogger("smartmigrate.workflow").error("error get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "ERROR", "GET_REMAINING_JOBS", "ERROR", 0]})
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after get_remaining_jobs", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "INFO", "GET_REMAINING_JOBS", "END", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error get_remaining_jobs: {exc}", extra={"workflow_log": [0, "WORKFLOW", "06_GET_JOBS", "ERROR", "GET_REMAINING_JOBS", "ERROR", 0]})
            raise

    def _load_counts(self, conn: Any) -> dict[str, int]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        queries = {
            "MIG": f"""
                SELECT COUNT(*)
                  FROM {mig_table}
                 WHERE UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'))
            """,
            "SQL_CONVERSION": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE STATUS_CONVERSION IS NULL
                    OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%')
            """,
            "SQL_TUNING": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                   AND (STATUS_TUNING IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%'))
            """,
            "SQL_FORMATTING": f"""
                SELECT COUNT(*)
                  FROM {sql_table}
                 WHERE UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
            """,
        }
        cur = conn.cursor()
        return {route: self._scalar_count(cur, sql) for route, sql in queries.items()}

    def _load_target_jobs(self, conn: Any, targets: dict[str, list[Any]]) -> dict[str, list[dict[str, Any]]]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        cur = conn.cursor()
        migration_jobs: list[dict[str, Any]] = []
        sql_conversion_jobs: list[dict[str, Any]] = []
        sql_tuning_jobs: list[dict[str, Any]] = []
        sql_formatting_jobs: list[dict[str, Any]] = []

        map_ids = [item for item in (self._to_int(v) for v in targets.get("map_ids", [])) if item is not None]
        if map_ids:
            placeholders = ", ".join(f":{index + 1}" for index in range(len(map_ids)))
            migration_jobs = self._query_jobs(
                cur,
                f"""
                SELECT MAP_ID, PRIORITY, PRIOR_MAP_ID
                  FROM {mig_table}
                 WHERE MAP_ID IN ({placeholders})
                   AND UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'
                   AND (STATUS IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS, 'NULL'))) LIKE 'FAIL-%'))
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                """,
                map_ids,
                "MIG",
                ["map_id", "priority", "prior_map_id"],
            )

        sql_where, sql_params = self._sql_target_where(targets)
        if sql_where:
            sql_conversion_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND (STATUS_CONVERSION IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_CONVERSION, 'NULL'))) LIKE 'FAIL-%'))
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_CONVERSION",
                ["space_nm", "sql_id", "priority"],
            )
            sql_tuning_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')
                   AND (STATUS_TUNING IS NULL OR (UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y' AND UPPER(TRIM(NVL(STATUS_TUNING, 'NULL'))) LIKE 'FAIL-%'))
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_TUNING",
                ["space_nm", "sql_id", "priority"],
            )
            sql_formatting_jobs = self._query_jobs(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM, TO_CHAR(SQL_ID) AS SQL_ID, PRIORITY
                  FROM {sql_table}
                 WHERE ({sql_where})
                   AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')
                   AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                "SQL_FORMATTING",
                ["space_nm", "sql_id", "priority"],
            )

        all_jobs = [*migration_jobs, *sql_conversion_jobs, *sql_tuning_jobs, *sql_formatting_jobs]
        return {
            "all_jobs": all_jobs,
            "job_lookup_jobs": all_jobs,
            "migration_jobs": migration_jobs,
            "sql_conversion_jobs": sql_conversion_jobs,
            "sql_jobs": sql_conversion_jobs,
            "sql_tuning_jobs": sql_tuning_jobs,
            "sql_formatting_jobs": sql_formatting_jobs,
        }

    def _load_target_statuses(self, conn: Any, targets: dict[str, list[Any]]) -> dict[str, list[dict[str, Any]]]:
        mig_table = self._qualify("NEXT_MIG_INFO")
        sql_table = self._qualify("NEXT_SQL_INFO")
        cur = conn.cursor()
        statuses = {"migration": [], "sql": []}

        map_ids = [item for item in (self._to_int(v) for v in targets.get("map_ids", [])) if item is not None]
        if map_ids:
            placeholders = ", ".join(f":{index + 1}" for index in range(len(map_ids)))
            statuses["migration"] = self._query_statuses(
                cur,
                f"""
                SELECT MAP_ID, STATUS, USER_EDITED, PRIOR_MAP_ID, USE_YN, PRIORITY
                  FROM {mig_table}
                 WHERE MAP_ID IN ({placeholders})
                 ORDER BY PRIORITY ASC NULLS LAST, MAP_ID ASC
                """,
                map_ids,
                ["map_id", "status", "user_edited", "prior_map_id", "use_yn", "priority"],
            )

        sql_where, sql_params = self._sql_target_where(targets)
        if sql_where:
            statuses["sql"] = self._query_statuses(
                cur,
                f"""
                SELECT TO_CHAR(SPACE_NM) AS SPACE_NM,
                       TO_CHAR(SQL_ID) AS SQL_ID,
                       STATUS_CONVERSION,
                       STATUS_TUNING,
                       USER_EDITED,
                       PRIORITY
                  FROM {sql_table}
                 WHERE {sql_where}
                 ORDER BY PRIORITY ASC NULLS LAST, SPACE_NM ASC NULLS LAST, SQL_ID ASC NULLS LAST
                """,
                sql_params,
                ["space_nm", "sql_id", "status_conversion", "status_tuning", "user_edited", "priority"],
            )
        return statuses

    def _query_jobs(self, cur: Any, sql: str, params: list[Any], route: str, columns: list[str]) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        jobs: list[dict[str, Any]] = []
        for row in cur.fetchall():
            job = {"job_route": route, "job_type": "MIG" if route == "MIG" else "SQL"}
            for index, column in enumerate(columns):
                job[column] = self._json_value(row[index])
            jobs.append(job)
        return jobs

    def _query_statuses(self, cur: Any, sql: str, params: list[Any], columns: list[str]) -> list[dict[str, Any]]:
        cur.execute(sql, params)
        rows: list[dict[str, Any]] = []
        for row in cur.fetchall():
            rows.append({column: self._json_value(row[index]) for index, column in enumerate(columns)})
        return rows

    def _scalar_count(self, cur: Any, sql: str) -> int:
        cur.execute(sql)
        row = cur.fetchone()
        return int(row[0] or 0) if row else 0

    def _empty_requested_jobs(self) -> dict[str, list[dict[str, Any]]]:
        return {
            "all_jobs": [],
            "job_lookup_jobs": [],
            "migration_jobs": [],
            "sql_conversion_jobs": [],
            "sql_jobs": [],
            "sql_tuning_jobs": [],
            "sql_formatting_jobs": [],
        }

    def _extract_targets(self, payload: dict[str, Any]) -> dict[str, list[Any]]:
        existing = payload.get("target_filter") if isinstance(payload.get("target_filter"), dict) else {}
        text = str(payload.get("user_request") or payload.get("original_request") or payload.get("input") or "")
        return {
            "map_ids": self._merge_lists(self._normalize_int_list(existing.get("map_ids")), self._extract_map_ids(text)),
            "sql_ids": self._merge_lists(self._normalize_str_list(existing.get("sql_ids")), self._extract_text_values(text, r"sql[_\s-]*id|sqlid")),
            "space_nms": self._merge_lists(self._normalize_str_list(existing.get("space_nms")), self._extract_text_values(text, r"space[_\s-]*nm|spacenm|space")),
        }

    def _has_exact_target(self, targets: dict[str, list[Any]]) -> bool:
        return bool(targets.get("map_ids") or targets.get("sql_ids") or targets.get("space_nms"))

    def _sql_target_where(self, targets: dict[str, list[Any]]) -> tuple[str, list[Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        sql_ids = [str(v).strip() for v in targets.get("sql_ids", []) if str(v).strip()]
        space_nms = [str(v).strip() for v in targets.get("space_nms", []) if str(v).strip()]
        if sql_ids:
            placeholders = []
            for value in sql_ids:
                params.append(value)
                placeholders.append(f":{len(params)}")
            clauses.append(f"TO_CHAR(SQL_ID) IN ({', '.join(placeholders)})")
        if space_nms:
            placeholders = []
            for value in space_nms:
                params.append(value)
                placeholders.append(f":{len(params)}")
            clauses.append(f"TO_CHAR(SPACE_NM) IN ({', '.join(placeholders)})")
        return " AND ".join(clauses), params

    def _extract_map_ids(self, text: str) -> list[int]:
        values: list[int] = []
        patterns = [
            r"(?:map[_\s-]*id|mapid|map|맵\s*아이디|맵아이디)\s*[=:]?\s*([0-9,\s]+)",
            r"([0-9]+)\s*번?\s*(?:map[_\s-]*id|mapid|map|맵\s*아이디|맵아이디|맵)",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, text, flags=re.I):
                for item in re.findall(r"\d+", match.group(1)):
                    values.append(int(item))
        return list(dict.fromkeys(values))

    def _extract_text_values(self, text: str, label_pattern: str) -> list[str]:
        values: list[str] = []
        for match in re.finditer(rf"(?:{label_pattern})\s*[=:]?\s*([A-Za-z0-9_.:-]+(?:\s*,\s*[A-Za-z0-9_.:-]+)*)", text, flags=re.I):
            values.extend([item.strip() for item in match.group(1).split(",") if item.strip()])
        return list(dict.fromkeys(values))

    def _normalize_int_list(self, value: Any) -> list[int]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        out: list[int] = []
        for item in values:
            converted = self._to_int(item)
            if converted is not None and converted not in out:
                out.append(converted)
        return out

    def _normalize_str_list(self, value: Any) -> list[str]:
        values = value if isinstance(value, list) else ([] if value is None else [value])
        out: list[str] = []
        for item in values:
            text = str(item or "").strip()
            if text and text not in out:
                out.append(text)
        return out

    def _merge_lists(self, first: list[Any], second: list[Any]) -> list[Any]:
        out: list[Any] = []
        for item in [*first, *second]:
            if item not in out:
                out.append(item)
        return out

    @contextmanager
    def _connect(self):
        import oracledb

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

    def _has_db_config(self) -> bool:
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    def _qualify(self, table_name: str) -> str:
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
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
            raise ValueError("payload_json must be a JSON object")
        return parsed

    def _to_int(self, value: Any) -> int | None:
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

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
