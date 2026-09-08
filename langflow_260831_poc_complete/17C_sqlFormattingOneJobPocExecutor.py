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

SQL_FORMAT_BATCH_PROMPT = """
다음 Oracle/MyBatis SQL 목록을 의미 변경 없이 포맷하십시오.

[규칙]
- 반드시 유효한 JSON 배열만 반환하십시오. 첫 글자는 [ 이어야 하고 마지막 글자는 ] 이어야 합니다.
- JSON 배열 밖에는 어떤 문자도 출력하지 마십시오.
- markdown 코드블록, 설명, 주석, 제목, prefix, suffix를 절대 출력하지 마십시오.
- SQL 원문만 단독으로 반환하지 마십시오.
- 입력 JSON 배열의 각 item_id를 그대로 유지하십시오.
- SQL 의미, 테이블명, 컬럼명, alias, MyBatis 동적 태그, bind parameter를 변경하지 마십시오.
- SQL 끝에 세미콜론을 붙이지 마십시오.
- 출력 항목 수는 입력 항목 수와 정확히 같아야 합니다.
- 각 항목은 item_id와 formatted_sql key만 포함하십시오.
- formatted_sql 값은 JSON string이어야 하며 줄바꿈은 JSON 문자열 escape 규칙에 맞게 포함하십시오.
- item_id가 누락되거나 입력에 없는 item_id가 추가되면 실패입니다.

[Formatting 가이드]
{input_prompt}

[반환 형식]
[
  {{
    "item_id": "1",
    "formatted_sql": "SELECT ...\\nFROM ..."
  }}
]

[입력 JSON]
{input_sql_list_json}
""".strip()

class NewType17CSqlFormattingOneJobPocExecutor(Component):
    display_name = "17C SQL Formatting One Job Executor"
    description = "Formats TUNED_TO_SQL for a passed tuning job and stores FORMATTED_SQL."
    name = "NewType17CSqlFormattingOneJobPocExecutor"
    icon = "TextCursorInput"

    inputs = [
        DataInput(name="job_item", display_name="Job Item", required=True),
        MessageTextInput(name="input_prompt", display_name="Formatting Guide", value="", required=False),
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
        started = time.perf_counter()
        payload: dict[str, Any] = {}
        job: dict[str, Any] = {}
        try:
            payload = self._parse_payload(getattr(self, "job_item", ""))
            prior_failure = self._prior_failure_status(payload)
            if prior_failure:
                result = self._component_pass_through(payload, started, f"SQL formatting skipped because prior stage failed: {prior_failure}")
                result["status"] = prior_failure
                result["formatting_skipped"] = True
                self.status = result
                return Data(data=result)
            generated_sql_list = self._formatting_candidates(payload)
            payload["generated_sql_list"] = generated_sql_list
            if generated_sql_list:
                db_config = self._db_config(payload)
                self._require_db_config(db_config)
                result = self._run_batch_formatting(payload, db_config, started)
                self.status = result
                return Data(data=result)
            if self._job_name(payload) == "formatting":
                db_config = self._db_config(payload)
                self._require_db_config(db_config)
                job = self._load_sql_job(db_config, payload)
                result = self._run_single_sql_formatting({**payload, **job}, job, db_config, started)
                self.status = result
                return Data(data=result)
            result = self._run_batch_formatting(payload, {}, started)
            self.status = result
            return Data(data=result)
        except Exception as exc:
            result = self._finish_failure(payload, job, started, str(exc))
            self.status = result
            return Data(data=result)

    def _run_batch_formatting(self, payload: dict[str, Any], db_config: dict[str, Any], started: float) -> dict[str, Any]:
        """Format generated SQL references with one DB load pass and one LLM batch call."""
        allowed = {
            "NEXT_MIG_INFO": {"MIG_SQL", "VERIFY_SQL"},
            "NEXT_SQL_INFO": {"TUNED_FR_SQL", "TO_SQL", "BIND_SQL", "TEST_SQL", "TUNED_TO_SQL"},
        }
        results: list[dict[str, Any]] = []
        candidates: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for item in payload.get("generated_sql_list") or []:
            if not isinstance(item, dict):
                results.append({"status": "SKIPPED_INVALID_ITEM", "item": str(item)[:500]})
                continue
            table_name = str(item.get("table") or "").strip().upper()
            column = str(item.get("column") or "").strip().upper()
            if table_name not in allowed or column not in allowed[table_name]:
                results.append({"table": table_name, "column": column, "status": "SKIPPED_UNSUPPORTED"})
                continue
            key = str(item.get("row_id") or item.get("key_value") or "").strip()
            identity = (table_name, key, column)
            if not key or identity in seen:
                results.append({"table": table_name, "column": column, "key": key, "status": "SKIPPED_DUPLICATE_OR_EMPTY_KEY"})
                continue
            seen.add(identity)
            candidates.append({**item, "table": table_name, "column": column, "key": key, "item_id": f"{len(candidates) + 1}"})

        loaded_sqls: dict[str, str] = {}
        if candidates:
            try:
                loaded_sqls = self._load_generated_sqls(db_config, candidates)
            except Exception as exc:
                for item in candidates:
                    results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": FAIL_FORMATTING, "error": str(exc)})
                candidates = []

        format_inputs = []
        for item in candidates:
            sql_text = loaded_sqls.get(item["item_id"], "").strip()
            if not sql_text:
                results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": "SKIPPED_EMPTY"})
                continue
            format_inputs.append({"item_id": item["item_id"], "sql": sql_text})

        formatted_by_id: dict[str, tuple[str, str]] = {}
        if format_inputs:
            prompt = self._build_formatter_batch_prompt(format_inputs)
            self._log_formatting_event(payload, step_name="FORMAT_PROMPT", status="PASS", message="Formatting prompt assembled", generate_sql=prompt)
            try:
                raw_response = self._call_formatter_prompt(prompt, self._llm_config(payload))
                self._log_formatting_event(payload, step_name="LLM_RESPONSE", status="PASS", message="LLM formatting response returned", generate_sql=raw_response)
                formatted_by_id = self._format_sql_batch_response(format_inputs, raw_response)
            except Exception as exc:
                message = f"LLM batch formatting failed: {type(exc).__name__}: {exc}"
                format_input_ids = {fmt["item_id"] for fmt in format_inputs}
                for item in candidates:
                    if item["item_id"] in format_input_ids:
                        results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": FAIL_FORMATTING, "error": message})
                formatted_by_id = {}

        for item in candidates:
            if item["item_id"] not in formatted_by_id:
                continue
            formatted_sql, method = formatted_by_id[item["item_id"]]
            if not formatted_sql:
                results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": "SKIPPED_EMPTY"})
                continue
            try:
                self._update_generated_sql(db_config, item["table"], item, item["column"], formatted_sql)
                results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": FORMATTED, "method": method})
            except Exception as exc:
                results.append({"table": item["table"], "column": item["column"], "key": item["key"], "status": FAIL_FORMATTING, "error": str(exc)})
        failures = [item for item in results if item["status"] == FAIL_FORMATTING]
        status = FORMATTED if not failures else FAIL_FORMATTING
        self._log_formatting_event(
            payload,
            step_name="FORMAT_COMPLETE",
            status=status,
            message="SQL formatting completed" if status == FORMATTED else "SQL formatting failed",
            generate_sql=self._json_dump(results),
        )
        return {
            **payload,
            "component": "17C_sqlFormattingOneJobPocExecutor",
            "ok": not failures,
            "status": status,
            "formatting_status": status,
            "formatting_results": results,
            "formatted_count": len([item for item in results if item["status"] == FORMATTED]),
            "failed_count": len(failures),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
            "db_status_updated": bool(results and not failures),
            "next_node": self._dashboard_node(payload),
        }

    def _formatting_candidates(self, payload: dict[str, Any]) -> list[dict[str, Any]]:
        raw = payload.get("generated_sql_list")
        if isinstance(raw, list) and raw:
            return list(raw)
        route = str(payload.get("planned_job_route") or payload.get("job_route") or "").strip().upper()
        job_name = self._job_name(payload)
        if route == "MIG" or job_name == "migration":
            map_id = payload.get("map_id") or payload.get("key_value")
            if map_id is None or str(map_id).strip() == "":
                return []
            return [
                {"table": "NEXT_MIG_INFO", "key_column": "MAP_ID", "key_value": map_id, "column": "MIG_SQL"},
                {"table": "NEXT_MIG_INFO", "key_column": "MAP_ID", "key_value": map_id, "column": "VERIFY_SQL"},
            ]
        return []

    def _run_single_sql_formatting(self, payload: dict[str, Any], job: dict[str, Any], db_config: dict[str, Any], started: float) -> dict[str, Any]:
        """Format the final tuned SQL into FORMATTED_SQL for standalone SQL Formatting runs."""
        if not self._is_tuning_pass(job.get("status_tuning") or payload.get("status_tuning") or payload.get("tuning_status")):
            return self._pass_through(
                payload=payload,
                job=job,
                started=started,
                status=self._status(job.get("status_tuning") or payload.get("status_tuning") or payload.get("tuning_status")) or "NOT-RUN",
                message="SQL formatting passed through because tuning status is not PASS.",
            )
        source_sql = str(job.get("tuned_to_sql") or payload.get("tuned_to_sql") or "").strip()
        if not source_sql:
            return self._finish_failure(payload, job, started, "TUNED_TO_SQL is empty")

        format_inputs = [{"item_id": "1", "sql": source_sql}]
        prompt = self._build_formatter_batch_prompt(format_inputs)
        self._log_formatting_event(payload, step_name="FORMAT_PROMPT", status="PASS", message="Formatting prompt assembled", generate_sql=prompt)
        raw_response = self._call_formatter_prompt(prompt, self._llm_config(payload))
        self._log_formatting_event(payload, step_name="LLM_RESPONSE", status="PASS", message="LLM formatting response returned", generate_sql=raw_response)
        formatted_by_id = self._format_sql_batch_response(format_inputs, raw_response)
        formatted_sql = (formatted_by_id.get("1") or ("", ""))[0]
        if not formatted_sql:
            return self._finish_failure(payload, job, started, "FORMATTED_SQL generation returned empty SQL")

        self._update_row(db_config, str(job["row_id"]), {"FORMATTED_SQL": formatted_sql})
        results = [{"table": "NEXT_SQL_INFO", "column": "FORMATTED_SQL", "key": str(job.get("row_id") or ""), "status": FORMATTED, "method": "llm_batch"}]
        self._log_formatting_event(payload, step_name="FORMAT_COMPLETE", status=FORMATTED, message="SQL formatting completed", generate_sql=self._json_dump(results))
        return self._result(
            payload=payload,
            job=job,
            ok=True,
            status=FORMATTED,
            elapsed=time.perf_counter() - started,
            attempts=[],
            message="SQL formatting completed.",
            extra={
                "formatting_status": FORMATTED,
                "formatted_sql": formatted_sql,
                "formatting_results": results,
                "formatted_count": 1,
                "failed_count": 0,
                "next_node": self._dashboard_node(payload),
            },
        )

    def _log_formatting_event(self, payload: dict[str, Any], *, step_name: str, status: str, message: str, generate_sql: Any = None) -> None:
        map_id = str(payload.get("map_id") or payload.get("sql_id") or 0)[:100]
        retry_count = self._to_int(payload.get("retry_count"), 0)
        logging.getLogger("smartmigrate.workflow").info(
            str(message or ""),
            extra={"workflow_log": [map_id, "SQL_FORMATTING", "17C_SQL_FORMAT", "INFO", str(step_name or "")[:50], str(status or "")[:20], retry_count, generate_sql]},
        )

    def _json_dump(self, value: Any) -> str:
        try:
            return json.dumps(value, ensure_ascii=False, default=str)
        except Exception:
            return str(value)

    def _load_generated_sql(self, db_config: dict[str, Any], table_name: str, item: dict[str, Any], column: str) -> str:
        table = self._qualify(table_name, db_config.get("system_schema"))
        if table_name == "NEXT_MIG_INFO":
            map_id = item.get("key_value") or item.get("map_id")
            if map_id is None:
                raise ValueError("NEXT_MIG_INFO formatting item requires key_value/map_id")
            where_sql, params = "MAP_ID = :key", {"key": int(map_id)}
        else:
            row_id = str(item.get("row_id") or "").strip()
            if not row_id:
                raise ValueError("NEXT_SQL_INFO formatting item requires row_id")
            where_sql, params = "ROWID = CHARTOROWID(:key)", {"key": row_id}
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT {column} FROM {table} WHERE {where_sql}", params)
            row = cur.fetchone()
            if not row:
                raise ValueError(f"{table_name} row not found")
            return self._lob_to_str(row[0]).strip()

    def _load_generated_sqls(self, db_config: dict[str, Any], items: list[dict[str, Any]]) -> dict[str, str]:
        """Load all requested SQL columns in table-level batches."""
        loaded: dict[str, str] = {}
        mig_items = [item for item in items if item["table"] == "NEXT_MIG_INFO"]
        sql_items = [item for item in items if item["table"] == "NEXT_SQL_INFO"]

        if mig_items:
            table = self._qualify("NEXT_MIG_INFO", db_config.get("system_schema"))
            columns = sorted({item["column"] for item in mig_items})
            keys = sorted({int(item.get("key_value") or item.get("map_id") or item["key"]) for item in mig_items})
            params = {f"k{index}": key for index, key in enumerate(keys)}
            placeholders = ", ".join(f":{name}" for name in params)
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT MAP_ID, {', '.join(columns)} FROM {table} WHERE MAP_ID IN ({placeholders})", params)
                # Oracle CLOB values can be LOB locators; read them before the connection closes.
                by_key = {
                    str(row[0]): {
                        column: self._lob_to_str(row[index + 1]).strip()
                        for index, column in enumerate(columns)
                    }
                    for row in cur.fetchall()
                }
            for item in mig_items:
                loaded[item["item_id"]] = by_key.get(str(item["key"]), {}).get(item["column"], "")

        if sql_items:
            table = self._qualify("NEXT_SQL_INFO", db_config.get("system_schema"))
            columns = sorted({item["column"] for item in sql_items})
            row_ids = sorted({str(item["key"]).strip() for item in sql_items})
            params = {f"r{index}": row_id for index, row_id in enumerate(row_ids)}
            predicates = " OR ".join(f"ROWID = CHARTOROWID(:{name})" for name in params)
            with self._connect(db_config) as conn:
                cur = conn.cursor()
                cur.execute(f"SELECT ROWIDTOCHAR(ROWID) AS ROW_ID, {', '.join(columns)} FROM {table} WHERE {predicates}", params)
                # Oracle CLOB values can be LOB locators; read them before the connection closes.
                by_key = {
                    str(row[0]): {
                        column: self._lob_to_str(row[index + 1]).strip()
                        for index, column in enumerate(columns)
                    }
                    for row in cur.fetchall()
                }
            for item in sql_items:
                loaded[item["item_id"]] = by_key.get(str(item["key"]), {}).get(item["column"], "")

        return loaded

    def _update_generated_sql(self, db_config: dict[str, Any], table_name: str, item: dict[str, Any], column: str, value: str) -> None:
        table = self._qualify(table_name, db_config.get("system_schema"))
        columns = self._table_columns(db_config, table)
        if column not in columns:
            raise ValueError(f"{table_name}.{column} does not exist")
        if table_name == "NEXT_MIG_INFO":
            key_sql, params = "MAP_ID = :key", {"key": int(item.get("key_value") or item.get("map_id"))}
        else:
            key_sql, params = "ROWID = CHARTOROWID(:key)", {"key": str(item.get("row_id") or "").strip()}
        params["value"] = value
        update_ts = ", UPD_TS = CURRENT_TIMESTAMP" if "UPD_TS" in columns else ""
        with self._connect(db_config) as conn:
            cur = conn.cursor()
            cur.execute(f"UPDATE {table} SET {column} = :value{update_ts} WHERE {key_sql}", params)
            conn.commit()

    def _format_sql_batch_response(self, items: list[dict[str, str]], raw_response: str) -> dict[str, tuple[str, str]]:
        if not items:
            return {}
        parsed = self._parse_formatter_batch_response(raw_response)
        result: dict[str, tuple[str, str]] = {}
        for item_id, sql in parsed.items():
            cleaned = self._clean_formatted_sql(sql)
            if cleaned:
                result[item_id] = (cleaned, "llm_batch")
        expected_ids = {item["item_id"] for item in items}
        missing = sorted(expected_ids - set(result))
        extra = sorted(set(result) - expected_ids)
        if missing or extra:
            raise ValueError(f"formatter batch response item_id mismatch: missing={missing}, extra={extra}")
        return result

    def _build_formatter_batch_prompt(self, items: list[dict[str, str]]) -> str:
        sql_list_json = json.dumps(items, ensure_ascii=False, default=str)
        input_prompt = self._text_input(getattr(self, "input_prompt", "")).strip() or "- (empty)"
        return SQL_FORMAT_BATCH_PROMPT.format(input_prompt=input_prompt, input_sql_list_json=sql_list_json)

    def _parse_formatter_batch_response(self, raw: str) -> dict[str, str]:
        text = str(raw or "").strip()
        if not text:
            raise ValueError("formatter batch response is empty")
        if not text.startswith("[") or not text.endswith("]"):
            raise ValueError(f"formatter batch response must be a JSON array only; preview={text[:300]}")
        parsed: Any = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("formatter batch response must be a JSON array")
        result: dict[str, str] = {}
        for item in parsed:
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("item_id") or item.get("id") or "").strip()
            sql = str(item.get("formatted_sql") or item.get("sql") or "").strip()
            if item_id and sql:
                result[item_id] = sql
        return result

    def _call_formatter_prompt(self, prompt: str, config: dict[str, Any]) -> str:
        api_key = str(config.get("llm_api_key") or "").strip()
        model = str(config.get("llm_model") or "").strip()
        base_url = str(config.get("llm_base_url") or "").strip().rstrip("/")
        provider = str(config.get("llm_provider") or "").strip().lower()
        if not api_key or not model or not base_url:
            raise ValueError("llm_base_url, llm_api_key, and llm_model are required")
        if not provider:
            provider = "anthropic" if "anthropic" in base_url.lower() or model.lower().startswith("claude") else "openai"
        candidates = [model, *[item.strip() for item in str(config.get("llm_fallback_models") or "").split(",") if item.strip()]]
        candidate_models = list(dict.fromkeys(candidates))
        for index, candidate in enumerate(candidate_models):
            try:
                if provider == "anthropic":
                    from anthropic import Anthropic

                    response = Anthropic(api_key=api_key, base_url=base_url, timeout=self._positive_int(config.get("llm_timeout_seconds"), 900)).messages.create(
                        model=candidate,
                        max_tokens=self._positive_int(config.get("llm_max_tokens"), 4096),
                        temperature=0,
                        system="Oracle/MyBatis SQL을 의미 변경 없이 포맷하십시오.",
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
                                {"role": "system", "content": "Oracle/MyBatis SQL을 의미 변경 없이 포맷하십시오."},
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

    def _prior_failure_status(self, payload: dict[str, Any]) -> str:
        stages = payload.get("stages") or {}
        for stage_name in ("migration", "conversion", "tuning"):
            status = str((stages.get(stage_name) or {}).get("status") or "").strip()
            if status.upper().startswith("FAIL"):
                return status
        for key in ("status_mig", "migration_status", "status_conversion", "conversion_status", "status_tuning", "tuning_status", "status"):
            status = str(payload.get(key) or "").strip()
            if status.upper().startswith("FAIL"):
                return status
        if payload.get("ok") is False:
            status = str(payload.get("status") or "").strip()
            return status or "FAIL"
        return ""

    def _format_source_column(self, payload: dict[str, Any], job: dict[str, Any]) -> str:
        for column, key in (("TUNED_TO_SQL", "tuned_to_sql"), ("TO_SQL", "to_sql")):
            if str(payload.get(key) or job.get(key) or "").strip():
                return column
        return ""

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

    def _text_input(self, value: Any) -> str:
        if value is None:
            return ""
        if isinstance(value, Message):
            return str(value.text or "")
        if isinstance(value, Data):
            data = dict(value.data or {})
            for key in ("text", "message", "prompt", "value"):
                if str(data.get(key) or "").strip():
                    return str(data.get(key) or "")
            return json.dumps(data, ensure_ascii=False, default=str)
        return str(value or "")

    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _to_int(self, value: Any, default: int = 0) -> int:
        try:
            return int(value)
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
