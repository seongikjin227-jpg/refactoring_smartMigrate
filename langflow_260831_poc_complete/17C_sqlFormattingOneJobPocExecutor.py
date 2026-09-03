from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data
from lfx.schema.message import Message

try:
    from lfx.io import DataInput
except Exception:
    DataInput = MessageTextInput


TUNING_SUCCESS_STATUSES = {"PASS", "PASS-TUNING"}
FORMATTED = "FORMATTED"
FAIL_FORMATTING = "FAIL-FORMATTING"

SQL_FORMAT_PROMPT = """
You are an Oracle/MyBatis SQL formatter.

Goal:
Format the input SQL using line breaks and 4-space indentation only.

Hard rules:
- Do not change table names, column names, aliases, JOIN/WHERE logic, MyBatis tags, #{param}, or ${param}.
- Do not add explanations, markdown fences, comments, wrappers, or extra SQL.
- Preserve Oracle/MyBatis semantics exactly.
- Return only the formatted SQL text.

Input SQL:
{input_sql}
""".strip()


class NewType17CSqlFormattingOneJobPocExecutor(Component):
    display_name = "17C SQL Formatting One Job Executor"
    description = "Formats TUNED_TO_SQL for a passed tuning job and stores FORMATTED_SQL."
    name = "NewType17CSqlFormattingOneJobPocExecutor"
    icon = "TextCursorInput"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        IntInput(name="max_retry", display_name="Max Retry", value=2, required=False),
        StrInput(name="llm_base_url", display_name="LLM Base URL", required=False),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_provider", display_name="LLM Provider", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="GLM-5.1", required=False),
        StrInput(name="llm_fallback_models", display_name="LLM Fallback Models", value="GLM-5.1,Qwen3.6-35B-A3B,Kimi-K2.5", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=4096, required=False),
        IntInput(name="llm_timeout_seconds", display_name="LLM Timeout Seconds", value=900, required=False),
    ]

    outputs = [Output(display_name="Job Result", name="job_result", method="run_job", types=["Data"])]

    def run_job(self) -> Data:
        logging.getLogger("smartmigrate.workflow").info("before run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "START", 0]})
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        job: dict[str, Any] = {}
        try:
            payload = self._parse_payload(getattr(self, "job_item", ""))
            if not self._should_run_formatting(payload):
                result = self._component_pass_through(payload, started, "17C skipped because job_name is migration.")
                self.status = result
                return Data(data=result)

            db_config = self._db_config(payload)
            self._require_db_config(db_config)
            job = self._load_sql_job(db_config, payload)
            merged = {**job, **payload}

            tuning_status = self._status(merged.get("tuning_status") or merged.get("status_tuning") or job.get("status_tuning"))
            if not self._is_tuning_pass(tuning_status):
                result = self._pass_through(
                    payload=merged,
                    job=job,
                    started=started,
                    status=self._status(merged.get("status")) or tuning_status or "NOT-RUN",
                    message=f"SQL formatting passed through without DB update because tuning status is {tuning_status or 'NULL'}.",
                )
                self.status = result
                return Data(data=result)

            self._increment_batch_count(db_config, str(job["row_id"]))
            result = self._run_formatting(merged, job, db_config, started)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = self._finish_failure(payload, job, started, str(exc))
            self.status = result
            return Data(data=result)
        finally:
            logging.getLogger("smartmigrate.workflow").info("after run_job", extra={"workflow_log": [0, "WORKFLOW", "17C_SQL_FORMAT", "INFO", "RUN_JOB", "END", 0]})

    def _run_formatting(self, payload: dict[str, Any], job: dict[str, Any], db_config: dict[str, Any], started: float) -> dict[str, Any]:
        source_sql = str(payload.get("tuned_to_sql") or job.get("tuned_to_sql") or payload.get("to_sql") or job.get("to_sql") or "").strip()
        if not source_sql:
            return self._finish_failure(payload, job, started, "TUNED_TO_SQL is empty")

        formatted_sql, method = self._format_sql(source_sql, self._llm_config(payload))
        if not formatted_sql.strip():
            return self._finish_failure(payload, job, started, "SQL formatting returned empty SQL")

        self._update_row(db_config, str(job["row_id"]), {"FORMATTED_SQL": formatted_sql})
        logging.getLogger("smartmigrate.workflow").info(
            "FORMATTED_SQL generated",
            extra={"workflow_log": [self._map_id(job), "SQL_FORMATTING", "FORMATTED_SQL", "INFO", "GENERATE_FORMATTED_SQL", "SUCCESS", 0, f"method={method}, source_column=TUNED_TO_SQL"]},
        )
        return self._result(
            payload=payload,
            job=job,
            ok=True,
            status=FORMATTED,
            elapsed=time.perf_counter() - started,
            attempts=[{"attempt": 1, "stage": "GENERATE_FORMATTED_SQL", "status": FORMATTED, "method": method}],
            message="SQL formatting completed.",
            extra={"formatting_status": FORMATTED, "formatted_sql": formatted_sql, "format_method": method, "next_node": self._dashboard_node(payload)},
        )

    def _format_sql(self, sql_text: str, llm_config: dict[str, Any]) -> tuple[str, str]:
        source = str(sql_text or "").strip()
        if not source:
            return "", "empty"
        try:
            formatted = self._call_formatter_llm(source, llm_config)
            formatted = self._clean_formatted_sql(formatted)
            if formatted:
                return formatted, "llm"
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning(
                f"LLM SQL formatting fallback: {type(exc).__name__}: {exc}",
                extra={"workflow_log": [0, "SQL_FORMATTING", "FORMATTED_SQL", "WARN", "LLM_FORMAT_SQL", "FALLBACK", 0]},
            )
        return self._format_sql_deterministic(source), "deterministic_fallback"

    def _call_formatter_llm(self, sql_text: str, config: dict[str, Any]) -> str:
        api_key = str(config.get("llm_api_key") or "").strip()
        model = str(config.get("llm_model") or "").strip()
        base_url = str(config.get("llm_base_url") or "").strip().rstrip("/")
        provider = str(config.get("llm_provider") or "").strip().lower()
        if not api_key or not model or not base_url:
            raise ValueError("llm_base_url, llm_api_key, and llm_model are required")
        if not provider:
            provider = "anthropic" if "anthropic" in base_url.lower() or model.lower().startswith("claude") else "openai"
        candidates = [model, *[item.strip() for item in str(config.get("llm_fallback_models") or "").split(",") if item.strip()]]
        prompt = SQL_FORMAT_PROMPT.format(input_sql=sql_text)
        candidate_models = list(dict.fromkeys(candidates))
        for index, candidate in enumerate(candidate_models):
            try:
                if provider == "anthropic":
                    from anthropic import Anthropic

                    response = Anthropic(api_key=api_key, base_url=base_url, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).messages.create(
                        model=candidate,
                        max_tokens=self._positive_int(config.get("llm_max_tokens"), 4096),
                        temperature=0,
                        system="You format Oracle/MyBatis SQL without changing semantics.",
                        messages=[{"role": "user", "content": prompt}],
                    )
                    return "".join(str(getattr(item, "text", "")) for item in response.content).strip()
                url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
                request = urllib.request.Request(
                    url,
                    data=json.dumps(
                        {
                            "model": candidate,
                            "messages": [
                                {"role": "system", "content": "You format Oracle/MyBatis SQL without changing semantics."},
                                {"role": "user", "content": prompt},
                            ],
                            "temperature": 0,
                            "max_tokens": self._positive_int(config.get("llm_max_tokens"), 4096),
                        },
                        ensure_ascii=False,
                    ).encode("utf-8"),
                    headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
                    method="POST",
                )
                with urllib.request.urlopen(request, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)) as response:
                    body = json.loads(response.read().decode("utf-8", errors="ignore"))
                return str((((body.get("choices") or [{}])[0].get("message") or {}).get("content") or "")).strip()
            except urllib.error.HTTPError as exc:
                if index == len(candidate_models) - 1:
                    detail = exc.read().decode("utf-8", errors="ignore")
                    raise ValueError(f"LLM HTTP {exc.code}: {detail[:500]}") from exc
            except Exception:
                if index == len(candidate_models) - 1:
                    raise
        raise ValueError("LLM formatter returned no content")

    def _clean_formatted_sql(self, value: str) -> str:
        sql = str(value or "").strip()
        if sql.startswith("```"):
            sql = re.sub(r"^```(?:sql|xml)?\s*", "", sql, flags=re.I)
            sql = re.sub(r"\s*```$", "", sql)
        return sql.strip().rstrip(";").strip()

    def _format_sql_deterministic(self, sql_text: str) -> str:
        text = re.sub(r"\s+", " ", str(sql_text or "").strip())
        keywords = ["SELECT", "FROM", "WHERE", "GROUP BY", "ORDER BY", "HAVING", "UNION ALL", "UNION", "INSERT INTO", "VALUES", "UPDATE", "SET", "DELETE FROM"]
        for keyword in sorted(keywords, key=len, reverse=True):
            text = re.sub(rf"\b{re.escape(keyword)}\b", f"\n{keyword}", text, flags=re.I)
        return "\n".join(line.strip() for line in text.splitlines() if line.strip())

    def _finish_failure(self, payload: dict[str, Any], job: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=FAIL_FORMATTING,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message=message,
            extra={"formatting_status": FAIL_FORMATTING, "next_node": self._dashboard_node(payload)},
        )

    def _pass_through(self, *, payload: dict[str, Any], job: dict[str, Any], started: float, status: str, message: str) -> dict[str, Any]:
        return self._result(
            payload=payload,
            job=job,
            ok=False,
            status=status,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message=message,
            extra={"formatting_skipped": True, "next_node": self._dashboard_node(payload)},
        )

    def _component_pass_through(self, payload: dict[str, Any], started: float, message: str) -> dict[str, Any]:
        elapsed = time.perf_counter() - started
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        result = {
            **payload,
            "component": "17C_sqlFormattingOneJobPocExecutor",
            "ok": bool(payload.get("ok", True)),
            "status": payload.get("status") or "PASS-THROUGH",
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": int(payload.get("attempt_count") or 0),
            "attempts": list(payload.get("attempts") or []),
            "job_index": index,
            "total_jobs": total,
            "completed_count": index,
            "remaining_count": max(total - index, 0),
            "stages": dict(payload.get("stages") or {}),
            "component_pass_through": True,
            "pass_through_component": "17C",
            "message": payload.get("message") or message,
            "next_node": self._dashboard_node(payload),
        }
        history = list(result.get("history") or [])
        history.append({"step": "17C_pass_through", "message": message})
        result["history"] = history
        return result

    def _result(self, *, payload: dict[str, Any], job: dict[str, Any], ok: bool, status: str, elapsed: float, attempts: list[dict[str, Any]], message: str, extra: dict[str, Any]) -> dict[str, Any]:
        total = int(payload.get("total_jobs") or 1)
        index = int(payload.get("job_index") or 1)
        completed = min(index, total)
        stages = dict(payload.get("stages") or {})
        if not extra.get("formatting_skipped"):
            stages["formatting"] = {"ok": ok, "status": status, "message": message, "attempts": attempts}
        return {
            **payload,
            **extra,
            "component": "17C_sqlFormattingOneJobPocExecutor",
            "job_route": payload.get("job_route") or "SQL_FORMATTING",
            "job_type": "SQL",
            "row_id": job.get("row_id") or payload.get("row_id"),
            "space_nm": job.get("space_nm") or payload.get("space_nm"),
            "sql_id": job.get("sql_id") or payload.get("sql_id"),
            "ok": ok,
            "status": status,
            "elapsed_seconds": round(elapsed, 3),
            "attempt_count": len(attempts),
            "attempts": attempts,
            "message": message,
            "job_index": index,
            "total_jobs": total,
            "completed_count": completed,
            "remaining_count": max(total - completed, 0),
            "stages": stages,
            "generated_sql_list": list(payload.get("generated_sql_list") or []),
            "db_status_updated": bool(job.get("row_id")) and ok,
        }

    def _should_run_formatting(self, payload: dict[str, Any]) -> bool:
        return self._job_name(payload) in {"conversion", "tuning", "formatting"}

    def _job_name(self, payload: dict[str, Any]) -> str:
        value = str(payload.get("job_name") or "").strip().lower()
        if value:
            return value
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()
        return {"MIG": "migration", "SQL_CONVERSION": "conversion", "SQL_TUNING": "tuning", "SQL_FORMATTING": "formatting"}.get(route, "")

    def _load_sql_job(self, db_config: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        aliases = [
            ("SPACE_NM", "space_nm", "VARCHAR2(4000)"),
            ("SQL_ID", "sql_id", "VARCHAR2(4000)"),
            ("TO_SQL", "to_sql", "CLOB"),
            ("TUNED_TO_SQL", "tuned_to_sql", "CLOB"),
            ("STATUS_TUNING", "status_tuning", "VARCHAR2(100)"),
            ("FORMATTED_SQL", "formatted_sql", "CLOB"),
            ("TUNED_RESULT", "tuned_result", "VARCHAR2(4000)"),
            ("RETRY_COUNT", "retry_count", "NUMBER"),
        ]
        select_sql = ",\n               ".join(["ROWIDTOCHAR(ROWID) AS row_id", *[self._select_expr(columns, col, alias, data_type) for col, alias, data_type in aliases]])
        row_id = str(payload.get("row_id") or "").strip()
        if row_id:
            where_sql = "ROWID = CHARTOROWID(:rid)"
            params = {"rid": row_id}
        else:
            space_nm = str(payload.get("space_nm") or "").strip()
            sql_id = str(payload.get("sql_id") or "").strip()
            if not space_nm or not sql_id:
                raise ValueError("SQL formatting item requires row_id or space_nm+sql_id")
            where_sql = "TO_CHAR(SPACE_NM) = :space_nm AND TO_CHAR(SQL_ID) = :sql_id"
            params = {"space_nm": space_nm, "sql_id": sql_id}
        order_expr = "UPD_TS NULLS FIRST" if "UPD_TS" in columns else "ROWID"
        query = f"SELECT {select_sql} FROM {table} WHERE {where_sql} ORDER BY {order_expr}"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(query, params)
            row = cur.fetchone()
            if not row:
                raise ValueError(f"NEXT_SQL_INFO row not found: space_nm={payload.get('space_nm')}, sql_id={payload.get('sql_id')}")
            keys = ["row_id", *[alias for _, alias, _ in aliases]]
            loaded = {key: self._lob_to_str(row[index]) for index, key in enumerate(keys)}
        return {**payload, **loaded}

    def _update_row(self, db_config: dict[str, Any], row_id: str, values: dict[str, Any]) -> None:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        set_clauses: list[str] = []
        params: dict[str, Any] = {"rid": row_id}
        for index, (column, value) in enumerate(values.items(), start=1):
            if column not in columns:
                continue
            name = f"p{index}"
            set_clauses.append(f"{column} = :{name}")
            params[name] = value
        if "UPD_TS" in columns:
            set_clauses.append("UPD_TS = CURRENT_TIMESTAMP")
        if not set_clauses:
            return
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {', '.join(set_clauses)} WHERE ROWID = CHARTOROWID(:rid)", params)
            conn.commit()

    def _increment_batch_count(self, db_config: dict[str, Any], row_id: str) -> None:
        table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if "BATCH_CNT" not in columns:
            return
        set_clause = "BATCH_CNT = NVL(BATCH_CNT, 0) + 1"
        if "UPD_TS" in columns:
            set_clause += ", UPD_TS = CURRENT_TIMESTAMP"
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {set_clause} WHERE ROWID = CHARTOROWID(:1)", [row_id])
            conn.commit()

    def _status(self, value: Any) -> str:
        return str(value or "").strip().upper()

    def _is_tuning_pass(self, value: Any) -> bool:
        return self._status(value) in TUNING_SUCCESS_STATUSES

    def _dashboard_node(self, payload: dict[str, Any]) -> str:
        if payload.get("full_workflow"):
            return "18D_fullWorkflowDashboard"
        route = str(payload.get("job_route") or "").upper()
        if route == "SQL_CONVERSION":
            return "12D_sqlConversionIterationDashboard"
        if route == "SQL_TUNING":
            return "15D_sqlTuningIterationDashboard"
        return "17D_sqlFormattingIterationDashboard"

    def _map_id(self, job: dict[str, Any]) -> str:
        return f"{job.get('sql_id') or ''} / {job.get('space_nm') or ''}"[:100]

    def _select_expr(self, columns: set[str], column: str, alias: str, data_type: str) -> str:
        if column in columns:
            return f"{column} AS {alias}"
        if data_type.upper() == "CLOB":
            return f"TO_CLOB(NULL) AS {alias}"
        return f"CAST(NULL AS {data_type}) AS {alias}"

    def _table_columns(self, db_config: dict[str, Any], table: str) -> set[str]:
        owner, table_name = self._split_table_owner_and_name(table)
        if owner:
            sql = "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER = :1 AND TABLE_NAME = :2"
            params = [owner, table_name]
        else:
            sql = "SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME = :1"
            params = [table_name]
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(sql, params)
            return {str(row[0]).upper() for row in cur.fetchall()}

    @contextmanager
    def _connect(self, db_config: dict[str, Any]):
        import oracledb

        dsn = oracledb.makedsn(str(db_config.get("db_host") or "").strip(), int(db_config.get("db_port") or 1521), service_name=str(db_config.get("db_service_name") or "").strip())
        conn = oracledb.connect(user=str(db_config.get("db_username") or "").strip(), password=str(db_config.get("db_password") or ""), dsn=dsn)
        try:
            yield conn
        finally:
            conn.close()

    def _llm_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("llm_config") or {})
        return {
            "llm_base_url": str(getattr(self, "llm_base_url", "") or item_config.get("llm_base_url") or "").strip(),
            "llm_api_key": self._secret_to_str(getattr(self, "llm_api_key", None)) or str(item_config.get("llm_api_key") or "").strip(),
            "llm_provider": str(getattr(self, "llm_provider", "") or item_config.get("llm_provider") or "").strip(),
            "llm_model": str(getattr(self, "llm_model", "") or item_config.get("llm_model") or "").strip(),
            "llm_fallback_models": str(getattr(self, "llm_fallback_models", "") or item_config.get("llm_fallback_models") or "").strip(),
            "llm_max_tokens": self._positive_int(getattr(self, "llm_max_tokens", None) or item_config.get("llm_max_tokens"), 4096),
            "llm_timeout_seconds": self._positive_int(getattr(self, "llm_timeout_seconds", None) or item_config.get("llm_timeout_seconds"), 900),
        }

    def _db_config(self, payload: dict[str, Any]) -> dict[str, Any]:
        item_config = dict(payload.get("db_config") or {})
        return {
            "db_host": str(item_config.get("db_host") or "").strip(),
            "db_port": int(item_config.get("db_port") or 1521),
            "db_service_name": str(item_config.get("db_service_name") or "").strip(),
            "db_username": str(item_config.get("db_username") or "").strip(),
            "db_password": str(item_config.get("db_password") or ""),
            "system_schema": str(item_config.get("system_schema") or "").strip(),
        }

    def _require_db_config(self, db_config: dict[str, Any]) -> None:
        missing = [key for key in ("db_host", "db_service_name", "db_username") if not str(db_config.get(key) or "").strip()]
        if missing:
            raise ValueError(f"17C SQL Formatting is not connected to database settings: missing {', '.join(missing)}")

    def _qualify(self, table_name: str, schema: Any) -> str:
        clean_table = self._clean_identifier(table_name)
        clean_schema = str(schema or "").strip().upper()
        return f"{self._clean_identifier(clean_schema)}.{clean_table}" if clean_schema else clean_table

    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    def _split_table_owner_and_name(self, table: str) -> tuple[str | None, str]:
        value = str(table or "").strip().upper()
        if "." in value:
            owner, name = value.split(".", 1)
            return owner, name
        return None, value

    def _lob_to_str(self, value: Any) -> str:
        if value is not None and hasattr(value, "read"):
            return str(value.read())
        return "" if value is None else str(value)

    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value() or "")
        return str(value or "")

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, Message):
            raw = raw.text
        if isinstance(raw, dict):
            return dict(raw)
        text = str(raw or "").strip()
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
            text = re.sub(r"\s*```$", "", text)
        parsed = json.loads(text) if text else {}
        if not isinstance(parsed, dict):
            raise ValueError("job_item must be a JSON object")
        return parsed
