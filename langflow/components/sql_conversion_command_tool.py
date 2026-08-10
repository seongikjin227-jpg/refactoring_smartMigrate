from __future__ import annotations

import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any
from urllib.parse import quote_plus

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, IntInput, MessageTextInput, SecretStrInput, StrInput, Output
from lfx.schema.data import Data


class SqlConversionCommandTool(Component):
    display_name = "SQL Conversion Command Tool"
    description = "Generates TO_SQL for SmartMigration SQL conversion jobs."
    name = "SqlConversionCommandTool"
    icon = "FileCode"

    _db_cache: dict[str, Any] = {}


    # ==================== ?낅젰 ?뺤쓽: DB/LLM ?곌껐 ?뺣낫? command JSON, ?꾨＼?ы듃瑜??낅젰諛쏅뒗?? ====================
    inputs = [
        MessageTextInput(
            name="command_json",
            display_name="Command JSON",
            required=True,
            tool_mode=True,
            info='JSON command. Example: {"action":"status","space_nm":"SFA","sql_id":"selectUser"}',
        ),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=True),
        StrInput(name="db_service_name", display_name="Service Name", required=True),
        StrInput(name="db_username", display_name="Username", required=True),
        SecretStrInput(name="db_password", display_name="Password", required=True),
        StrInput(
            name="llm_base_url",
            display_name="LLM Base URL",
            required=False,
            info="OpenAI-compatible LLM gateway base URL. Only OpenAI-compatible chat/completions is supported.",
        ),
        SecretStrInput(name="llm_api_key", display_name="LLM API Key", required=False),
        StrInput(name="llm_model", display_name="LLM Model", value="claude-haiku-4-5-20251001", required=False),
        IntInput(name="llm_max_tokens", display_name="LLM Max Tokens", value=4096, required=False),
        IntInput(
            name="llm_timeout_seconds",
            display_name="LLM Timeout Seconds",
            value=900,
            required=False,
            info="HTTP timeout for LLM API calls. Default: 900 seconds.",
        ),
        MessageTextInput(
            name="to_sql_prompt",
            display_name="TO SQL Prompt",
            required=True,
            info="Prompt template for generate_to_sql. Use placeholders: {from_sql}, {mapping_schema_text}, {source_schema}, {target_schema}, {last_error}.",
        ),
        MessageTextInput(
            name="bind_sql_prompt",
            display_name="BIND SQL Prompt",
            required=False,
            info="Prompt template for BIND_SQL generation. Use placeholders: {from_sql}, {to_sql}, {mapping_schema_text}, {source_schema}, {target_schema}, {last_error}.",
        ),
        MessageTextInput(
            name="test_sql_prompt",
            display_name="TEST SQL Prompt",
            required=False,
            info="Prompt template for TEST_SQL generation. Use placeholders: {from_sql}, {to_sql}, {bind_sql}, {bind_set}, {mapping_schema_text}, {source_schema}, {target_schema}, {last_error}.",
        ),
        StrInput(
            name="system_schema",
            display_name="System Schema",
            required=False,
            info="Schema containing NEXT_SQL_INFO/NEXT_MIG_INFO/NEXT_MIG_INFO_DTL/NEXT_MIG_RAG_INFO. Leave blank for current user.",
        ),
        StrInput(
            name="source_schema",
            display_name="Source Schema",
            required=False,
            info="Optional AS-IS schema hint for matching source tables in FR_SQL/EDIT_FR_SQL.",
        ),
        StrInput(
            name="target_schema",
            display_name="Target Schema",
            required=False,
            info="Target schema to apply to physical TO-BE tables.",
        ),
        BoolInput(
            name="auto_install_packages",
            display_name="Auto Install Missing Packages",
            value=False,
            required=False,
            info="If true, installs missing runtime packages with pip before DB connection.",
        ),
    ]


    # ==================== 異쒕젰 ?뺤쓽: action 泥섎━ 寃곌낵瑜?result JSON?쇰줈 諛섑솚?쒕떎. ====================
    outputs = [
        Output(display_name="Result", name="result", method="run_command"),
    ]


    # ==================== ?≪뀡 肄붾뱶 ====================
    # Langflow?먯꽌 諛쏆? action 媛믪쓣 if/elif濡?遺꾧린?쒕떎.
    def run_command(self) -> Data:
        try:
            command = self._parse_command()
            action = str(command.get("action") or "").strip().lower()
            space_nm = command.get("space_nm")
            sql_id = command.get("sql_id")
            last_error = command.get("last_error")
            to_sql = command.get("to_sql")
            bind_sql = command.get("bind_sql")
            bind_set = command.get("bind_set")

            # command瑜?吏곸젒 action???곌껐?섎뒗寃?醫뗭?吏 ?꾨땲硫??뚮씪誘명꽣瑜??뺥솗?섍쾶 遺꾨━?댁꽌 諛쏅뒗寃?醫뗭?吏 怨좊?以? 
            # ?쇰떒 command JSON??洹몃?濡?諛쏅뒗嫄몃줈. db migration?먯꽌??command瑜??꾨떖 諛쏅뒗 action??留롮???以꾩뿬???섎굹,,
            if action == "test_connection":
                result = self._test_connection()
            elif action == "status":
                result = self._status(space_nm, sql_id)
            elif action == "list_pending":
                result = self._list_pending(command.get("limit", 20))
            elif action == "generate_to_sql":
                result = self._generate_to_sql(space_nm, sql_id, last_error)
            elif action == "generate_bind_sql":
                result = self._generate_bind_sql(space_nm, sql_id, to_sql, last_error)
            elif action == "generate_test_sql":
                result = self._generate_test_sql(space_nm, sql_id, to_sql, bind_sql, bind_set, last_error)
            elif action == "preview_to_sql_prompt":
                result = self._preview_to_sql_prompt(space_nm, sql_id, last_error)
            elif action == "preview_bind_sql_prompt":
                result = self._preview_bind_sql_prompt(space_nm, sql_id, to_sql, last_error)
            elif action == "preview_test_sql_prompt":
                result = self._preview_test_sql_prompt(space_nm, sql_id, to_sql, bind_sql, bind_set, last_error)
            elif action == "run_sql_conversion_job":
                result = self.run_sql_conversion_job(sql_id, space_nm, command)
            else:
                result = {"ok": False, "error": f"Unsupported action: {action}"}

            self.status = result
            return Data(data=result)

        except Exception as exc:
            result = {"ok": False, "error": str(exc)}
            self.status = result
            return Data(data=result)


    # action="test_connection": DB? LLM ?곌껐 ?곹깭瑜??뺤씤?쒕떎.
    def _test_connection(self) -> dict[str, Any]:
        try:
            result = self._get_db().run("SELECT 1 AS OK FROM DUAL", include_columns=True)
            db_result = {"ok": True, "message": "DB connection OK", "result": result}
        except Exception as exc:
            db_result = {"ok": False, "message": "DB connection failed", "error": str(exc)}

        api_key = str(self.llm_api_key or "").strip()
        model = str(self.llm_model or "").strip()
        if not api_key:
            llm_result = {"ok": False, "message": "LLM API key is empty"}
        elif not model:
            llm_result = {"ok": False, "message": "LLM model is empty"}
        else:
            try:
                base_url = str(self.llm_base_url or "https://api.openai.com/v1").strip().rstrip("/")
                url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
                payload = {
                    "model": model,
                    "messages": [{"role": "user", "content": "Return OK only."}],
                    "max_tokens": 8,
                    "temperature": 0,
                }
                data = self._post_json(url, payload, {"Authorization": f"Bearer {api_key}"})
                content = ""
                try:
                    content = data["choices"][0]["message"].get("content", "")
                except Exception:
                    content = ""
                llm_result = {"ok": True, "provider": "openai-compatible", "model": model, "url": url, "response_preview": str(content)[:200]}
            except Exception as exc:
                llm_result = {"ok": False, "provider": "openai-compatible", "model": model, "error": str(exc)}

        return {
            "ok": bool(db_result.get("ok")) and bool(llm_result.get("ok")),
            "db": db_result,
            "llm": llm_result,
        }


    # action="status": space_nm/sql_id 湲곗? SQL Conversion ?묒뾽 ?곹깭瑜?議고쉶?쒕떎.
    def _status(self, space_nm: Any, sql_id: Any) -> dict[str, Any]:
        job = self._load_job(space_nm, sql_id)
        return {"ok": bool(job), "job": job, "error": "" if job else "job not found"}


    # action="list_pending": STATUS_CONVERSION??NULL??SQL Conversion ?묒뾽 紐⑸줉??議고쉶?쒕떎.
    def _list_pending(self, limit: Any) -> dict[str, Any]:
        safe_limit = max(1, min(int(limit or 20), 100))
        table = self._qualify_table("NEXT_SQL_INFO", self.system_schema)
        # fr_sql 媛숈? CLOB 而щ읆? DBMS_LOB.SUBSTR濡?誘몃━ ?섎씪??媛?몄삤嫄곕굹 length留?媛?몄샂
        sql = f"""
                SELECT *
                FROM (
                    SELECT TAG_KIND, SPACE_NM, SQL_ID, STATUS_CONVERSION,
                           DBMS_LOB.SUBSTR(FR_SQL, 1000, 1) AS FR_SQL_PREVIEW,
                           DBMS_LOB.GETLENGTH(FR_SQL) AS FR_SQL_LEN,
                           DBMS_LOB.GETLENGTH(EDIT_FR_SQL) AS EDIT_FR_SQL_LEN,
                           PRIORITY, UPD_TS
                    FROM {table}
                    WHERE STATUS_CONVERSION IS NULL
                    ORDER BY PRIORITY ASC NULLS LAST, UPD_TS NULLS FIRST, SPACE_NM, SQL_ID
                )
                WHERE ROWNUM <= {safe_limit}
                """
        with self._connect() as conn:
                    cur = conn.cursor()
                    cur.execute(sql)
                    rows = cur.fetchall()
        jobs = [
            {
                "tag_kind": self._to_text(r[0]),
                "space_nm": self._to_text(r[1]),
                "sql_id": self._to_text(r[2]),
                "status_conversion": self._to_text(r[3]),
                "fr_sql_preview": self._to_text(r[4]),
                "fr_sql_len": r[5],
                "edit_fr_sql_len": r[6],
                "priority": r[7],
                "upd_ts": self._to_text(r[8]),
            }
            for r in rows
        ]
        return {"ok": True, "count": len(jobs), "jobs": jobs}

    # action="get_table_ddl" ???꾨＼?ы듃???ｌ쓣吏 怨좊?以?, ?꾩슂?섎㈃ migration_tool?먯꽌 媛?몄???render_*_prompt???ｌ뼱二쇰㈃ ?좊벏

    # action="generate_to_sql": TO_SQL???앹꽦?댁꽌 梨꾪똿 ?묐떟?쇰줈 諛섑솚?쒕떎. DB?먮뒗 ??ν븯吏 ?딅뒗??
    def _generate_to_sql(self, space_nm: Any, sql_id: Any, last_error: Any = None) -> dict[str, Any]:
        # map id??sql id 議댁옱 ?좊Т???ㅻⅨ ?⑥닔?먯꽌???쒖슜?좉굅??
        if not str(space_nm or "").strip() or not str(sql_id or "").strip():
            raise ValueError("space_nm and sql_id are required")
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}

        user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
        existing_to_sql = str(job.get("to_sql") or "").strip()
        if user_edited:
            if existing_to_sql:
                return {
                    "ok": True,
                    "space_nm": space_nm,
                    "sql_id": sql_id,
                    "status": "TO_SQL_SKIPPED_USER_EDITED",
                    "message": "USER_EDITED=Y. Existing TO_SQL was preserved.",
                    "db_updated": False,
                    "to_sql": existing_to_sql,
                }
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "USER_EDITED=Y but TO_SQL is empty"}

        # edit_fr_sql ???덉쑝硫?source_sql濡??, ?꾨＼?ы듃?먮뒗 source_sql ???ㅼ뼱媛?        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        prompt = self._render_to_sql_prompt(
            from_sql=source_sql,
            mapping_schema_text=mapping_schema_text,
            source_schema=str(self.source_schema or "").strip() or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )
        to_sql = self._sanitize_to_sql(self._call_llm(prompt))

        return {
            "ok": True,
            "space_nm": space_nm,
            "sql_id": sql_id,
            "status": "TO_SQL_GENERATED",
            "db_updated": False,
            "fr_tables": fr_tables,
            "map_ids": map_ids,
            "rag_rule_count": rag_rule_count,
            "to_sql": to_sql,
        }

    # action="generate_bind_sql": BIND_SQL???앹꽦?댁꽌 梨꾪똿 ?묐떟?쇰줈 諛섑솚?쒕떎. DB?먮뒗 ??ν븯吏 ?딅뒗??
    def _generate_bind_sql(self, space_nm: Any, sql_id: Any, to_sql: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}

        user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
        existing_bind_sql = str(job.get("bind_sql") or "").strip()
        if user_edited and existing_bind_sql:
            return {
                "ok": True,
                "space_nm": space_nm,
                "sql_id": sql_id,
                "status": "BIND_SQL_SKIPPED_USER_EDITED",
                "message": "USER_EDITED=Y. Existing BIND_SQL was preserved.",
                "db_updated": False,
                "bind_sql": existing_bind_sql,
            }

        final_to_sql = str(to_sql or job.get("to_sql") or "").strip()
        if not final_to_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "TO_SQL is empty. Pass to_sql or save TO_SQL before generating BIND_SQL."}

        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-BIND", "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        try:
            prompt = self._build_bind_sql_prompt(job, final_to_sql, mapping_schema_text, last_error)
            bind_sql = self._sanitize_to_sql(self._call_llm(prompt))
        except Exception as exc:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-BIND", "error": str(exc), "db_updated": False}

        return {
            "ok": True,
            "space_nm": space_nm,
            "sql_id": sql_id,
            "status": "SUCCESS-BIND",
            "db_updated": False,
            "fr_tables": fr_tables,
            "map_ids": map_ids,
            "rag_rule_count": rag_rule_count,
            "bind_sql": bind_sql,
        }

    # action="generate_test_sql": TEST_SQL???앹꽦?댁꽌 梨꾪똿 ?묐떟?쇰줈 諛섑솚?쒕떎. DB?먮뒗 ??ν븯吏 ?딅뒗??
    def _generate_test_sql(self, space_nm: Any, sql_id: Any, to_sql: Any = None, bind_sql: Any = None, bind_set: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}

        user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
        existing_test_sql = str(job.get("test_sql") or "").strip()
        if user_edited and existing_test_sql:
            return {
                "ok": True,
                "space_nm": space_nm,
                "sql_id": sql_id,
                "status": "TEST_SQL_SKIPPED_USER_EDITED",
                "message": "USER_EDITED=Y. Existing TEST_SQL was preserved.",
                "db_updated": False,
                "test_sql": existing_test_sql,
            }

        final_to_sql = str(to_sql or job.get("to_sql") or "").strip()
        final_bind_sql = str(bind_sql or job.get("bind_sql") or "").strip()
        final_bind_set = str(bind_set or job.get("bind_set") or "").strip()
        if not final_to_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "TO_SQL is empty. Pass to_sql or save TO_SQL before generating TEST_SQL."}
        if not final_bind_set:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "BIND_SET is empty. Pass bind_set or run BIND_SQL before generating TEST_SQL."}

        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-TEST", "error": "source SQL is empty"}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        try:
            prompt = self._build_test_sql_prompt(job, final_to_sql, final_bind_sql, final_bind_set, mapping_schema_text, last_error)
            test_sql = self._sanitize_to_sql(self._call_llm(prompt))
        except Exception as exc:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-TEST", "error": str(exc), "db_updated": False}

        return {
            "ok": True,
            "space_nm": space_nm,
            "sql_id": sql_id,
            "status": "TEST_SQL_GENERATED",
            "db_updated": False,
            "fr_tables": fr_tables,
            "map_ids": map_ids,
            "rag_rule_count": rag_rule_count,
            "test_sql": test_sql,
        }

    # action="preview_to_sql_prompt": LLM ?몄텧 ?놁씠 SQL Conversion prompt瑜?誘몃━ ?뺤씤?쒕떎.
    def _preview_to_sql_prompt(self, space_nm: Any, sql_id: Any, last_error: Any = None) -> dict[str, Any]:

        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}


        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "source SQL is empty"}


        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        prompt = self._render_to_sql_prompt(
            from_sql=source_sql,
            mapping_schema_text=mapping_schema_text,
            source_schema=str(self.source_schema or "").strip() or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )
        return {
            "ok": True,
            "action": "preview_to_sql_prompt",
            "space_nm": space_nm,
            "sql_id": sql_id,
            "prompt_kind": "to_sql",
            "prompt_length": len(prompt),
            "prompt": prompt,
            "db_updated": False,
            "llm_called": False,
            "fr_tables": fr_tables,
            "map_ids": map_ids,
            "rag_rule_count": rag_rule_count,
        }

    # action="preview_bind_sql_prompt": LLM ?몄텧 ?놁씠 BIND SQL prompt瑜?誘몃━ ?뺤씤?쒕떎.
    def _preview_bind_sql_prompt(self, space_nm: Any, sql_id: Any, to_sql: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}

        final_to_sql = str(to_sql or job.get("to_sql") or "").strip()
        if not final_to_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "TO_SQL is empty. Pass to_sql or save TO_SQL before previewing BIND prompt."}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        prompt = self._build_bind_sql_prompt(job, final_to_sql, mapping_schema_text, last_error)
        return {"ok": True, "action": "preview_bind_sql_prompt", "space_nm": space_nm, "sql_id": sql_id, "prompt_kind": "bind", "prompt_length": len(prompt), "prompt": prompt, "db_updated": False, "llm_called": False, "fr_tables": fr_tables, "map_ids": map_ids, "rag_rule_count": rag_rule_count}

    # action="preview_test_sql_prompt": LLM ?몄텧 ?놁씠 TEST SQL prompt瑜?誘몃━ ?뺤씤?쒕떎.
    def _preview_test_sql_prompt(self, space_nm: Any, sql_id: Any, to_sql: Any = None, bind_sql: Any = None, bind_set: Any = None, last_error: Any = None) -> dict[str, Any]:
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "error": "job not found"}

        final_to_sql = str(to_sql or job.get("to_sql") or "").strip()
        final_bind_sql = str(bind_sql or job.get("bind_sql") or "").strip()
        final_bind_set = str(bind_set or job.get("bind_set") or "").strip()
        if not final_to_sql:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "TO_SQL is empty. Pass to_sql or save TO_SQL before previewing TEST prompt."}
        if not final_bind_set:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "BIND_SET is empty. Pass bind_set or run BIND_SQL before previewing TEST prompt."}

        mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
        prompt = self._build_test_sql_prompt(job, final_to_sql, final_bind_sql, final_bind_set, mapping_schema_text, last_error)
        return {"ok": True, "action": "preview_test_sql_prompt", "space_nm": space_nm, "sql_id": sql_id, "prompt_kind": "test", "prompt_length": len(prompt), "prompt": prompt, "db_updated": False, "llm_called": False, "fr_tables": fr_tables, "map_ids": map_ids, "rag_rule_count": rag_rule_count}


    # action="run_sql_conversion_job": TO_SQL ?앹꽦, BIND_SQL ?ㅽ뻾, TEST_SQL 寃利앷퉴吏 SQL Conversion ?꾩껜 ?묒뾽???섑뻾?쒕떎.
    def run_sql_conversion_job(self, sql_id: str, space_nm: str, command: dict[str, Any]) -> dict[str, Any]:

        #=====_run_sql_conversion_job? ?ъ슜?먭? 梨꾪똿?쇰줈 ?몄텧???섎룄 ?덇린 ?뚮Ц???ъ슜?먭? ?붿껌??job???ㅽ뻾 媛?ν븳吏 寃利앺븳??=====
        if (sql_id is None or str(sql_id).strip() == "") or (space_nm is None or str(space_nm).strip() == ""):
            return {"ok": False, "error": "sql_id and space_nm are required for run_sql_conversion_job"}
        sql_id = str(sql_id or "").strip()
        space_nm = str(space_nm or "").strip()

        # job ?ㅽ뻾??嫄몃┛ ?쒓컙 痢≪젙 : ??肄붾뱶 湲곗? - 理쒖쥌 PASS/FAIL ?곹깭 ?????elapsed_seconds 怨꾩궛
        started = time.perf_counter()
        max_attempts = max(1, int(command.get("max_attempts") or 3))

        # job??DB?먯꽌 議고쉶?섍퀬, STATUS_CONVERSION??NULL?몄???_load_job?먯꽌 ?뺤씤
        job = self._load_job(space_nm, sql_id)
        if not job:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "error": "job not found"}

        # SQL Conversion ?묒뾽 ??곸? STATUS_CONVERSION??NULL??row留??덉슜?쒕떎.
        current_status = str(job.get("status_conversion") or "").strip().upper()
        if current_status:
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": current_status, "error": "run_sql_conversion_job is allowed only when STATUS_CONVERSION is NULL."}

        steps: list[dict[str, Any]] = []
        last_to_sql = str(job.get("to_sql") or "")
        last_bind_sql = str(job.get("bind_sql") or "")
        last_bind_set = str(job.get("bind_set") or "")
        last_test_sql = str(job.get("test_sql") or "")
        last_retry_count = 0

        try:
            # mapping_schema_text??TO_SQL/TEST_SQL prompt??怨듯넻?쇰줈 ?ㅼ뼱媛??留ㅽ븨猷??ㅻ챸?대떎.
            mapping_schema_text, map_ids, fr_tables, rag_rule_count = self._build_mapping_schema_text(job)
            last_failure: dict[str, Any] = {}
            to_sql_executed = False
            bind_sql_executed = False
            test_sql_executed = False
            # ===========================SQL Conversion ?④퀎蹂??ㅽ뻾 ===========================
            # attempt??1遺???쒖옉?섍퀬, retry_count/ATTEMPT_NO??濡쒓렇 湲곗??쇰줈 0遺???쒖옉?쒕떎.
            for attempt in range(1, max_attempts + 1):
                retry_count = attempt - 1
                last_retry_count = retry_count
                job = self._load_job(space_nm, sql_id) or job
                user_edited = str(job.get("user_edited") or "").strip().upper() == "Y"
                tag_kind = str(job.get("tag_kind") or "").strip().upper()

                # [TO_SQL ?④퀎: USER_EDITED=Y?대㈃ DB????λ맂 TO_SQL??洹몃?濡??곌퀬, ?꾨땲硫?LLM?쇰줈 ?앹꽦?쒕떎.]
                if not to_sql_executed:
                    # user_edited媛 Y?대㈃ ?ъ슜?먭? ??ν븳 TO_SQL??洹몃?濡??ъ슜?쒕떎.
                    if user_edited:
                        to_sql = str(job.get("to_sql") or "").strip()
                        if not to_sql:
                            raise ValueError("USER_EDITED=Y but TO_SQL is empty")
                        last_to_sql = to_sql
                        steps.append({"step": "generate_to_sql", "attempt": attempt, "status": "SUCCESS-TOBE", "message": "USER_EDITED=Y. Existing TO_SQL was used."})
                    # user_edited媛 N?대㈃ LLM?쇰줈 TO_SQL???덈줈 ?앹꽦?쒕떎.
                    else:
                        try:
                            to_sql_result = self._generate_to_sql(space_nm, sql_id, last_error=last_failure.get("error", ""))
                        except Exception as exc:
                            to_sql_result = {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": "FAIL-TOBE", "error": str(exc), "db_updated": False}
                        if to_sql_result.get("ok"):
                            to_sql_result["status"] = "SUCCESS-TOBE"
                        steps.append({"step": "generate_to_sql", "attempt": attempt, **self._summary_result(to_sql_result)})
                        if not to_sql_result.get("ok"):
                            last_failure = {"status": "FAIL-TOBE", "error": to_sql_result.get("error") or "TO_SQL generation failed"}
                            self._write_log(sql_id, space_nm, "TO_SQL", "FAIL", "GENERATE_TO_SQL", str(last_failure["error"])[:3900], retry_count, last_to_sql, int(time.perf_counter() - started), "TO_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_to_sql = str(to_sql_result.get("to_sql") or "").strip()
                    # TO_SQL ?앹꽦???깃났?섎㈃ ?ㅽ뻾 ?꾨즺 ?쒖떆瑜??④린怨? ?ㅼ쓬 ?ъ떆?꾩뿉?쒕뒗 TO_SQL ?ъ깮?깆쓣 嫄대꼫?대떎.
                    to_sql_executed = True
                    self._write_log(sql_id, space_nm, "TO_SQL", "PASS", "GENERATE_TO_SQL", "TO_SQL generated", retry_count, last_to_sql, int(time.perf_counter() - started), "TO_SQL_PROMPT")

                # SELECT媛 ?꾨땲硫?BIND/TEST ?놁씠 TO_SQL ?깃났留뚯쑝濡?Conversion???꾨즺?쒕떎.
                if tag_kind != "SELECT":
                    elapsed = int(time.perf_counter() - started)
                    self._save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
                    self._update_job_status(sql_id, space_nm, "PASS-CONVERSION", elapsed, retry_count, status_tuning="READY")
                    self._write_log(sql_id, space_nm, "TO_SQL", "PASS", "FINAL", "SQL Conversion completed without BIND/TEST because TAG_KIND is not SELECT", retry_count, last_to_sql, elapsed)
                    return {"ok": True, "space_nm": space_nm, "sql_id": sql_id, "status": "PASS-CONVERSION", "status_tuning": "READY", "elapsed_seconds": elapsed, "retry_count": retry_count, "steps": steps, "to_sql": last_to_sql, "map_ids": map_ids, "fr_tables": fr_tables, "rag_rule_count": rag_rule_count}

                # BIND_SQL ?④퀎: FR_SQL 湲곗??쇰줈 bind 媛믪쓣 戮묐뒗 SELECT瑜?留뚮뱾怨??ㅽ뻾 寃곌낵瑜?BIND_SET JSON?쇰줈 蹂닿??쒕떎.
                if not bind_sql_executed:
                    try:
                        bind_result = self._generate_bind_sql(space_nm, sql_id, last_to_sql, last_failure.get("error", ""))
                        steps.append({"step": "generate_bind_sql", "attempt": attempt, **self._summary_result(bind_result)})
                        if not bind_result.get("ok"):
                            last_failure = {"status": "FAIL-BIND", "error": bind_result.get("error") or "BIND_SQL generation failed"}
                            self._write_log(sql_id, space_nm, "BIND_SQL", "FAIL", "GENERATE_BIND_SQL", str(last_failure["error"])[:3900], retry_count, last_bind_sql, int(time.perf_counter() - started), "BIND_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_bind_sql = str(bind_result.get("bind_sql") or "").strip()
                        self._write_log(sql_id, space_nm, "BIND_SQL", "PASS", "GENERATE_BIND_SQL", "BIND_SQL generated", retry_count, last_bind_sql, int(time.perf_counter() - started), "BIND_SQL_PROMPT")
                        existing_bind_set = str(job.get("bind_set") or "").strip()
                        if bind_result.get("status") == "BIND_SQL_SKIPPED_USER_EDITED" and existing_bind_set:
                            last_bind_set = existing_bind_set
                            bind_sql_executed = True
                            steps.append({"step": "execute_bind_sql", "attempt": attempt, "ok": True, "status": "BIND_SET_SKIPPED_USER_EDITED", "message": "USER_EDITED=Y. Existing BIND_SET was used."})
                        else:
                            clean_bind_sql = self._prepare_runtime_sql(last_bind_sql, "EXECUTE_BIND_SQL")
                            if not clean_bind_sql:
                                raise ValueError("BIND_SQL is empty")
                            with self._connect() as conn:
                                cur = conn.cursor()
                                cur.execute(clean_bind_sql)
                                columns = [desc[0] for desc in cur.description] if cur.description else []
                                rows = cur.fetchmany(20)
                            result_rows = [{str(columns[i] if i < len(columns) else i): self._json_value(value) for i, value in enumerate(row)} for row in rows]
                            last_bind_set = json.dumps(result_rows, ensure_ascii=False)
                            bind_exec_result = {"ok": True, "status": "SUCCESS-BIND", "row_count": len(result_rows), "bind_set": last_bind_set}
                            steps.append({"step": "execute_bind_sql", "attempt": attempt, **self._summary_result(bind_exec_result)})
                            bind_sql_executed = True
                            self._write_log(sql_id, space_nm, "BIND_SET", "PASS", "EXECUTE_BIND_SQL", "BIND_SQL executed", retry_count, last_bind_set, int(time.perf_counter() - started))
                    except Exception as exc:
                        last_failure = {"status": "FAIL-BIND", "error": str(exc)}
                        steps.append({"step": "execute_bind_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._write_log(sql_id, space_nm, "BIND_SQL", "FAIL", "EXECUTE_BIND_SQL", str(exc)[:3900], retry_count, last_bind_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break

                # TEST_SQL ?④퀎: TO_SQL怨?BIND_SET??湲곗??쇰줈 FROM_COUNT/TO_COUNT 鍮꾧탳 SQL??留뚮뱾怨??ㅽ뻾?쒕떎.
                if not test_sql_executed:
                    try:
                        test_result = self._generate_test_sql(space_nm, sql_id, last_to_sql, last_bind_sql, last_bind_set, last_failure.get("error", ""))
                        steps.append({"step": "generate_test_sql", "attempt": attempt, **self._summary_result(test_result)})
                        if not test_result.get("ok"):
                            last_failure = {"status": "FAIL-TEST", "error": test_result.get("error") or "TEST_SQL generation failed"}
                            self._write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "GENERATE_TEST_SQL", str(last_failure["error"])[:3900], retry_count, last_test_sql, int(time.perf_counter() - started), "TEST_SQL_PROMPT")
                            if attempt < max_attempts:
                                continue
                            break
                        last_test_sql = str(test_result.get("test_sql") or "").strip()
                        self._write_log(sql_id, space_nm, "TEST_SQL", "PASS", "GENERATE_TEST_SQL", "TEST_SQL generated", retry_count, last_test_sql, int(time.perf_counter() - started), "TEST_SQL_PROMPT")

                        clean_test_sql = self._prepare_runtime_sql(last_test_sql, "EXECUTE_TEST_SQL")
                        if not clean_test_sql:
                            raise ValueError("TEST_SQL is empty")
                        with self._connect() as conn:
                            cur = conn.cursor()
                            cur.execute(clean_test_sql)
                            columns = [desc[0] for desc in cur.description] if cur.description else []
                            rows = cur.fetchall()
                        result_rows = [{str(columns[i] if i < len(columns) else i): self._json_value(value) for i, value in enumerate(row)} for row in rows]
                        if not result_rows:
                            test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": "TEST_SQL returned no rows", "result_rows": result_rows}
                        else:
                            sample_keys = {str(key).lower() for key in result_rows[0].keys()}
                            if not {"case_no", "from_count", "to_count"}.issubset(sample_keys):
                                test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": f"TEST_SQL must return CASE_NO, FROM_COUNT, TO_COUNT. Actual columns: {sorted(sample_keys)}", "result_rows": result_rows}
                            else:
                                test_exec_result = {"ok": True, "status": "PASS-CONVERSION", "message": "All test counts matched", "result_rows": result_rows}
                                for row in result_rows:
                                    from_count = self._get_row_value(row, "FROM_COUNT")
                                    to_count = self._get_row_value(row, "TO_COUNT")
                                    if str(from_count).strip() != str(to_count).strip():
                                        test_exec_result = {"ok": False, "status": "FAIL-TEST", "message": f"Count mismatch: {row}", "result_rows": result_rows}
                                        break
                        steps.append({"step": "execute_test_sql", "attempt": attempt, **self._summary_result(test_exec_result)})
                        if test_exec_result.get("ok"):
                            test_sql_executed = True
                            elapsed = int(time.perf_counter() - started)
                            self._save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
                            self._update_job_status(sql_id, space_nm, "PASS-CONVERSION", elapsed, retry_count, status_tuning="READY")
                            self._write_log(sql_id, space_nm, "TEST_SQL", "PASS", "EXECUTE_TEST_SQL", "SQL Conversion test passed", retry_count, last_test_sql, elapsed)
                            return {"ok": True, "space_nm": space_nm, "sql_id": sql_id, "status": "PASS-CONVERSION", "status_tuning": "READY", "elapsed_seconds": elapsed, "retry_count": retry_count, "steps": steps, "to_sql": last_to_sql, "bind_sql": last_bind_sql, "bind_set": last_bind_set, "test_sql": last_test_sql, "test_rows": test_exec_result.get("result_rows"), "map_ids": map_ids, "fr_tables": fr_tables, "rag_rule_count": rag_rule_count}
                        last_failure = {"status": "FAIL-TEST", "error": test_exec_result.get("message") or "TEST_SQL validation failed"}
                        self._write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "EXECUTE_TEST_SQL", str(last_failure["error"])[:3900], retry_count, last_test_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break
                    except Exception as exc:
                        last_failure = {"status": "FAIL-TEST", "error": str(exc)}
                        steps.append({"step": "execute_test_sql", "attempt": attempt, "ok": False, **last_failure})
                        self._write_log(sql_id, space_nm, "TEST_SQL", "FAIL", "EXECUTE_TEST_SQL", str(exc)[:3900], retry_count, last_test_sql, int(time.perf_counter() - started))
                        if attempt < max_attempts:
                            continue
                        break

            # 紐⑤뱺 ?ъ떆?꾧? PASS ?놁씠 ?앸굹硫?留덉?留??ㅽ뙣 ?곹깭? ?앹꽦??SQL????ν븳??
            final_status = str(last_failure.get("status") or self._fallback_conversion_failure_status(last_to_sql, last_bind_sql, last_bind_set, last_test_sql))
            elapsed = int(time.perf_counter() - started)
            self._save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._update_job_status(sql_id, space_nm, final_status, elapsed, last_retry_count)
            self._write_log(sql_id, space_nm, "ERROR", "FAIL", "FINAL", str(last_failure.get("error") or "Max attempts reached")[:3900], last_retry_count, last_test_sql or last_bind_sql or last_to_sql, elapsed)
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": final_status, "error": last_failure.get("error") or "Max attempts reached", "elapsed_seconds": elapsed, "retry_count": last_retry_count, "steps": steps}
        except Exception as exc:
            # ?덉긽?섏? 紐삵븳 ?덉쇅??理쒖쥌 ?ㅽ뙣濡???ν븯怨? ?꾩옱源뚯? ?앹꽦??SQL???④퍡 ?④릿??
            elapsed = int(time.perf_counter() - started)
            final_status = self._fallback_conversion_failure_status(last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._save_final_sql(sql_id, space_nm, last_to_sql, last_bind_sql, last_bind_set, last_test_sql)
            self._update_job_status(sql_id, space_nm, final_status, elapsed, last_retry_count)
            self._write_log(sql_id, space_nm, "ERROR", "FAIL", "RUN_FULL", str(exc)[:3900], last_retry_count, last_test_sql or last_bind_sql or last_to_sql, elapsed)
            return {"ok": False, "space_nm": space_nm, "sql_id": sql_id, "status": final_status, "error": str(exc), "elapsed_seconds": elapsed, "retry_count": last_retry_count, "steps": steps}

    # ======================================================================
    # 怨듯넻 肄붾뱶
    # ======================================================================
    # command_json??dict濡?蹂?섑븯怨?action/space_nm/sql_id 媛숈? ?ㅽ뻾 媛믪쓣 ?댁꽍?쒕떎.
    def _parse_command(self) -> dict[str, Any]:


        raw = self.command_json
        if isinstance(raw, dict):
            return raw
        text = str(raw or "").strip()
        if not text:
            raise ValueError("command_json is required")
        return json.loads(text)

    # DB ?낅젰媛믪쓣 Oracle SQLAlchemy connection string?쇰줈 議곕┰?쒕떎.
    def _connection_string(self) -> str:
        host = str(self.db_host or "").strip()
        port = int(self.db_port or 1521)
        service_name = str(self.db_service_name or "").strip()
        username = str(self.db_username or "").strip()
        password = str(self.db_password or "")
        if not host:
            raise ValueError("DB Host is required")
        if not service_name:
            raise ValueError("Service Name is required")
        if not username:
            raise ValueError("Username is required")
        return f"oracle+oracledb://{quote_plus(username)}:{quote_plus(password)}@{host}:{port}/{service_name}"

    # 媛숈? DB ?묒냽 ?뺣낫??_db_cache?먯꽌 ?ъ궗?⑺븳??
    # ??뵆濡쒖슦?먯꽌 SQLDatabase.from_uri 瑜??ъ슜?섍린 ?꾪빐 ?곕줈 怨듯넻?⑥닔濡?類?
    def _get_db(self):
        self._ensure_runtime_dependencies()
        from langchain_community.utilities import SQLDatabase

        cache_key = "|".join(
            [
                str(self.db_host or "").strip(),
                str(self.db_port or 1521),
                str(self.db_service_name or "").strip(),
                str(self.db_username or "").strip(),
            ]
        )
        if cache_key not in self._db_cache:
            self._db_cache[cache_key] = SQLDatabase.from_uri(self._connection_string())
        self.db = self._db_cache[cache_key]
        return self.db

    # DB ?곌껐???꾩슂???뚯씠???⑦궎吏媛 import 媛?ν븳吏 ?뺤씤?쒕떎.
    def _ensure_runtime_dependencies(self) -> None:
        missing_packages: list[str] = []
        try:
            import langchain_community
        except ModuleNotFoundError:
            missing_packages.append("langchain-community")
        try:
            import sqlalchemy
        except ModuleNotFoundError:
            missing_packages.append("SQLAlchemy")
        try:
            import oracledb
        except ModuleNotFoundError:
            missing_packages.append("oracledb")

        if not missing_packages:
            return
        if not self._as_bool(getattr(self, "auto_install_packages", False)):
            raise ModuleNotFoundError(
                "Missing packages: "
                + ", ".join(missing_packages)
                + ". Enable Auto Install Missing Packages or install them in the Langflow runtime."
            )
        for package in missing_packages:
            self._pip_install(package)

    def _pip_install(self, package: str) -> None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", package])

    # OpenAI ?명솚 LLM API??JSON ?붿껌??蹂대궡怨??묐떟 dict瑜?諛섑솚?쒕떎.
    def _post_json(self, url: str, payload: dict[str, Any], headers: dict[str, str]) -> dict[str, Any]:
        body = json.dumps(payload).encode("utf-8")
        req_headers = {"Content-Type": "application/json", **headers}
        request = urllib.request.Request(url, data=body, headers=req_headers, method="POST")
        timeout_seconds = max(1, int(self.llm_timeout_seconds or 900))
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
                    raw = response.read().decode("utf-8", errors="ignore")
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="ignore")[:1000]
                last_error = RuntimeError(f"HTTP {exc.code}: {detail}")
                if exc.code not in {429, 502, 503, 504} or attempt >= 3:
                    raise last_error from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = RuntimeError(f"LLM request failed: {exc}")
                if attempt >= 3:
                    raise last_error from exc
            time.sleep(min(8, 2 ** (attempt - 1)))
        raise last_error or RuntimeError("LLM request failed")

    # SQLDatabase ?대? engine?먯꽌 DB connection??爰쇰궡 cursor ?묒뾽???ъ슜?쒕떎.
    @contextmanager
    def _connect(self):
        db = self._get_db()
        with db._engine.connect() as conn:
            raw = conn.connection
            yield raw

    # NEXT_SQL_INFO?먯꽌 space_nm/sql_id???대떦?섎뒗 ?묒뾽 row瑜?議고쉶?쒕떎.
    def _load_job(self, space_nm: Any, sql_id: Any) -> dict[str, Any] | None:
        table = self._qualify_table("NEXT_SQL_INFO", self.system_schema)
        space_nm = str(space_nm or "").strip()
        sql_id = str(sql_id or "").strip()
        if not space_nm or not sql_id:
            raise ValueError("space_nm and sql_id are required")

        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT TAG_KIND, SPACE_NM, SQL_ID, FR_SQL, EDIT_FR_SQL,
                       TARGET_TABLE, TO_SQL, STATUS_CONVERSION, LOG,
                       TUNED_FR_SQL, TUNED_TO_SQL, SQL_LENGTH, MAP_TYPE,
                       PRIORITY, BATCH_CNT, UPD_TS, USER_EDITED,
                       BIND_SQL, BIND_SET, TEST_SQL, STATUS_TUNING, RETRY_COUNT
                FROM {table}
                WHERE SPACE_NM = :1
                  AND SQL_ID = :2
                """,
                [space_nm, sql_id],
            )
            row = cur.fetchone()
        if not row:
            return None
        return {
            "tag_kind": self._to_text(row[0]),
            "space_nm": self._to_text(row[1]),
            "sql_id": self._to_text(row[2]),
            "fr_sql": self._to_text(row[3]),
            "edit_fr_sql": self._to_text(row[4]),
            "target_table": self._to_text(row[5]),
            "to_sql": self._to_text(row[6]),
            "status_conversion": self._to_text(row[7]),
            "log": self._to_text(row[8]),
            "tuned_fr_sql": self._to_text(row[9]),
            "tuned_to_sql": self._to_text(row[10]),
            "sql_length": self._to_text(row[11]),
            "map_type": self._to_text(row[12]),
            "priority": row[13],
            "batch_cnt": row[14],
            "upd_ts": self._to_text(row[15]),
            "user_edited": self._to_text(row[16]),
            "bind_sql": self._to_text(row[17]),
            "bind_set": self._to_text(row[18]),
            "test_sql": self._to_text(row[19]),
            "status_tuning": self._to_text(row[20]),
            "retry_count": row[21],
        }


    # TARGET_TABLE??FR_TABLE 紐⑸줉??湲곗??쇰줈 migration map/rag ?뺣낫瑜?留뚮뱺??
    def _build_mapping_schema_text(self, job: dict[str, Any]) -> tuple[str, list[int], list[str], int]:

        fr_tables = self._extract_target_fr_tables(job.get("target_table"))
        if not fr_tables:
            sections = [
                "[TARGET_TABLE_FR_TABLE_HINTS]",
                "  - No FR_TABLE hints found.",
                "\n[MIGRATION_MAP_IDS]",
                "  - No MAP_ID found because TARGET_TABLE is empty.",
                "\n[MIGRATION_MAPPING_RULES]",
                "  - No mapping rules found because TARGET_TABLE is empty.",
                "\n[UNMAPPED_FR_TABLES]",
                "  - None.",
                "\n[SQL_CONVERSION_RAG_GUIDANCE]",
                "  - No FR_TABLE hints for SQL_CONVERSION RAG lookup.",
            ]
            return "\n".join(sections), [], [], 0

        normalized_fr_tables = {self._normalize_table_name(name) for name in fr_tables if self._normalize_table_name(name)}

        sections = ["[TARGET_TABLE_FR_TABLE_HINTS]"]
        for table_name in fr_tables:
            sections.append(f"  - {table_name}")

        sections.append("\n[MIGRATION_MAP_IDS]")
        map_ids: list[int] = []
        table = self._qualify_table("NEXT_MIG_INFO", self.system_schema)
        detail = self._qualify_table("NEXT_MIG_INFO_DTL", self.system_schema)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT M.MAP_ID, M.MAP_TYPE, M.FR_TABLE, D.FR_COL, M.TO_TABLE, D.TO_COL, M.CONDITION
                FROM {table} M
                LEFT JOIN {detail} D ON M.MAP_ID = D.MAP_ID
                ORDER BY M.PRIORITY ASC, M.MAP_ID ASC, D.MAP_DTL ASC
                """
            )
            rows = cur.fetchall()

        matched_rows = []
        matched_fr_tables: set[str] = set()
        for row in rows:
            map_id, map_type, fr_table, fr_col, to_table, to_col, condition = row
            fr_table_text = self._to_text(fr_table)
            normalized_fr_table = self._normalize_table_name(fr_table_text)
            if normalized_fr_table not in normalized_fr_tables:
                continue
            matched_rows.append((map_id, map_type, fr_table, fr_col, to_table, to_col, condition))
            matched_fr_tables.add(normalized_fr_table)
            if map_id is not None and int(map_id) not in map_ids:
                map_ids.append(int(map_id))

        if map_ids:
            for map_id in map_ids:
                sections.append(f"  - {map_id}")
        else:
            sections.append("  - No MAP_ID found for FR_TABLE hints.")

        unmatched_fr_tables = [
            table_name for table_name in fr_tables if self._normalize_table_name(table_name) not in matched_fr_tables
        ]
        sections.append("\n[UNMAPPED_FR_TABLES]")
        if unmatched_fr_tables:
            for table_name in unmatched_fr_tables:
                sections.append(f"  - {table_name}: no mapping rule found. Keep the original table/column names.")
        else:
            sections.append("  - None.")

        sections.append("\n[MIGRATION_MAPPING_RULES]")
        if not matched_rows:
            sections.append("  - No mapping rules found.")
        else:
            for row in matched_rows[:1000]:
                map_id, map_type, fr_table, fr_col, to_table, to_col, condition = row
                map_type, fr_table, fr_col, to_table, to_col, condition = [
                    self._to_text(v) for v in (map_type, fr_table, fr_col, to_table, to_col, condition)
                ]
                sections.append(
                    f"  - map_id={map_id}; map_type={map_type}; from={fr_table}.{fr_col or '*'}; to={to_table}.{to_col or '*'}; condition={condition}"
                )
        # NEXT_MIG_RAG_INFO?먯꽌 CATEGORY=SQL_CONVERSION?닿퀬 SOURCE_TABLES媛 FR_TABLE怨?媛숈? rule??理쒕? 3媛쒖뵫 媛?몄???guidance瑜?蹂댁뿬以??
        # ?먮옒???꾨쿋??湲곕컲 RAG濡??곕젮怨??덈뒗???곗꽑 FR_TABLE 湲곗??쇰줈 媛꾨떒?섍쾶 RAG瑜?蹂댁뿬二쇰뒗 寃껋쑝濡?援ы쁽?쒕떎.
        sections.append("\n[SQL_CONVERSION_RAG_GUIDANCE]")
        rag_lines = self._load_conversion_rag_rules(fr_tables)
        sections.extend(rag_lines)
        rag_rule_count = len([line for line in rag_lines if line.strip().startswith("- {")])
        return "\n".join(sections), map_ids, fr_tables, rag_rule_count

    # NEXT_MIG_RAG_INFO?먯꽌 CATEGORY=SQL_CONVERSION?닿퀬 SOURCE_TABLES媛 FR_TABLE怨?媛숈? rule??理쒕? 3媛쒖뵫 媛?몄삩??
    def _load_conversion_rag_rules(self, fr_tables: list[str]) -> list[str]:
        table = self._qualify_table("NEXT_MIG_RAG_INFO", self.system_schema)
        if not fr_tables:
            return ["  - No FR_TABLE hints for SQL_CONVERSION RAG lookup."]
        lines = []
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                for fr_table in fr_tables:
                    source_table = str(fr_table or "").strip().upper()
                    cur.execute(
                        f"""
                        SELECT RULE_TYPE, SOURCE_TABLES, GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL
                        FROM {table}
                        WHERE CATEGORY = 'SQL_CONVERSION'
                          AND UPPER(TRIM(NVL(USE_YN, 'Y'))) = 'Y'
                          AND UPPER(TRIM(SOURCE_TABLES)) = :1
                        ORDER BY CASE WHEN RULE_TYPE = 'GENERAL' THEN 1 ELSE 2 END, RAG_ID
                        FETCH FIRST 3 ROWS ONLY
                        """,
                        [source_table],
                    )
                    for rule_type, source_tables, guidance, source_sql, target_sql in cur.fetchall():
                        lines.append(
                            "  - "
                            + json.dumps(
                                {
                                    "rule_type": self._to_text(rule_type),
                                    "source_tables": self._to_text(source_tables),
                                    "guidance": self._to_text(guidance),
                                    "source_sql": self._to_text(source_sql)[:1000],
                                    "target_sql": self._to_text(target_sql)[:1000],
                                },
                                ensure_ascii=False,
                            )
                        )
        except Exception:
            return ["  - No SQL_CONVERSION RAG rules loaded."]
        return lines or ["  - No SQL_CONVERSION RAG rules found for FR_TABLE hints."]

    # to_sql_prompt???먮━?쒖떆?먮? ?ㅼ젣 媛믪쑝濡?移섑솚?쒕떎.
    def _render_to_sql_prompt(
        self,
        from_sql: str,
        mapping_schema_text: str,
        source_schema: str,
        target_schema: str,
        last_error: str,
    ) -> str:
        template = str(self.to_sql_prompt or "").strip()
        if not template:
            raise ValueError("TO SQL Prompt input is required for SQL generation")
        values = {
            "from_sql": from_sql,
            "mapping_schema_text": mapping_schema_text,
            "source_schema": source_schema,
            "target_schema": target_schema,
            "last_error": last_error,
        }
        for key, value in values.items():
            template = template.replace("{" + key + "}", str(value))
        return template

    # bind_sql_prompt???먮━?쒖떆?먮? ?ㅼ젣 媛믪쑝濡?移섑솚?쒕떎.
    def _render_bind_sql_prompt(
        self,
        from_sql: str,
        to_sql: str,
        mapping_schema_text: str,
        source_schema: str,
        target_schema: str,
        last_error: str,
    ) -> str:
        template = str(self.bind_sql_prompt or "").strip()
        if not template:
            raise ValueError("BIND SQL Prompt input is required for BIND_SQL generation")
        values = {"from_sql": from_sql, "to_sql": to_sql, "mapping_schema_text": mapping_schema_text, "source_schema": source_schema, "target_schema": target_schema, "last_error": last_error}
        for key, value in values.items():
            template = template.replace("{" + key + "}", str(value))
        return template

    # test_sql_prompt???먮━?쒖떆?먮? ?ㅼ젣 媛믪쑝濡?移섑솚?쒕떎.
    def _render_test_sql_prompt(
        self,
        from_sql: str,
        to_sql: str,
        bind_sql: str,
        bind_set: str,
        mapping_schema_text: str,
        source_schema: str,
        target_schema: str,
        last_error: str,
    ) -> str:
        template = str(self.test_sql_prompt or "").strip()
        if not template:
            raise ValueError("TEST SQL Prompt input is required for TEST_SQL generation")
        values = {"from_sql": from_sql, "to_sql": to_sql, "bind_sql": bind_sql, "bind_set": bind_set, "mapping_schema_text": mapping_schema_text, "source_schema": source_schema, "target_schema": target_schema, "last_error": last_error}
        for key, value in values.items():
            template = template.replace("{" + key + "}", str(value))
        return template

    # OpenAI ?명솚 chat/completions 寃쎈줈濡?LLM???몄텧?쒕떎.
    def _call_llm(self, prompt: str) -> str:
        api_key = str(self.llm_api_key or "").strip()
        model = str(self.llm_model or "").strip()
        max_tokens = int(self.llm_max_tokens or 4096)
        if not api_key:
            raise ValueError("LLM API key is empty")
        if not model:
            raise ValueError("LLM model is empty")
        base_url = str(self.llm_base_url or "https://api.openai.com/v1").strip().rstrip("/")
        url = base_url if base_url.endswith("/chat/completions") else f"{base_url}/chat/completions"
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        data = self._post_json(url, payload, {"Authorization": f"Bearer {api_key}"})
        return str(data["choices"][0]["message"].get("content", ""))

    # LLM ?묐떟?먯꽌 留덊겕?ㅼ슫 肄붾뱶 釉붾줉怨?留덉?留??몃?肄쒕줎???쒓굅?쒕떎.
    def _sanitize_to_sql(self, value: str) -> str:
        text = str(value or "").strip()
        if text.startswith("```"):
            fence = re.search(r"```(?:sql)?\s*(.*?)```", text, flags=re.I | re.S)
            if fence:
                text = fence.group(1).strip()
        text = text.rstrip(";").strip()
        if not text:
            raise ValueError("LLM returned empty SQL")
        return text

    # BIND_SQL ?앹꽦怨?preview媛 媛숈? prompt瑜??곕룄濡?prompt 援ъ꽦留???怨녹뿉 紐⑥???
    def _build_bind_sql_prompt(self, job: dict[str, Any], to_sql: str, mapping_schema_text: str, last_error: Any = None) -> str:
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            raise ValueError("source SQL is empty")

        fr_tables = self._extract_target_fr_tables(job.get("target_table"))
        source_schema = str(self.source_schema or "").strip().upper()
        if source_schema:
            for table_name in fr_tables:
                clean_table = str(table_name or "").strip().strip('"')
                if not clean_table or "." in clean_table:
                    continue
                source_sql = re.sub(rf"(?<![A-Z0-9_$#.]){re.escape(clean_table)}(?![A-Z0-9_$#])", f"{source_schema}.{clean_table}", source_sql, flags=re.I)

        return self._render_bind_sql_prompt(
            from_sql=source_sql,
            to_sql=to_sql,
            mapping_schema_text=mapping_schema_text,
            source_schema=source_schema or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )

    # TEST_SQL ?앹꽦怨?preview媛 媛숈? prompt瑜??곕룄濡?prompt 援ъ꽦留???怨녹뿉 紐⑥???
    def _build_test_sql_prompt(self, job: dict[str, Any], to_sql: str, bind_sql: str, bind_set: str, mapping_schema_text: str, last_error: Any = None) -> str:
        edit_fr_sql = str(job.get("edit_fr_sql") or "").strip()
        fr_sql = str(job.get("fr_sql") or "").strip()
        source_sql = edit_fr_sql if edit_fr_sql else fr_sql
        if not source_sql:
            raise ValueError("source SQL is empty")

        return self._render_test_sql_prompt(
            from_sql=source_sql,
            to_sql=to_sql,
            bind_sql=bind_sql,
            bind_set=bind_set,
            mapping_schema_text=mapping_schema_text,
            source_schema=str(self.source_schema or "").strip() or "UNKNOWN",
            target_schema=str(self.target_schema or "").strip() or "UNKNOWN",
            last_error=str(last_error or "None"),
        )

    # 理쒖쥌 ?깃났/?ㅽ뙣 ?쒖젏???앹꽦??SQL?ㅼ쓣 NEXT_SQL_INFO????ν븳??
    def _save_final_sql(self, sql_id: str, space_nm: str, to_sql: str, bind_sql: str, bind_set: str, test_sql: str) -> None:
        assignments = []
        params: list[Any] = []
        for column, value in (("TO_SQL", to_sql), ("BIND_SQL", bind_sql), ("BIND_SET", bind_set), ("TEST_SQL", test_sql)):
            clean_value = str(value or "").strip()
            if clean_value:
                params.append(clean_value)
                assignments.append(f"{column} = :{len(params)}")
        if not assignments:
            return
        params.extend([space_nm, sql_id])
        table = self._qualify_table("NEXT_SQL_INFO", self.system_schema)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                SET {", ".join(assignments)},
                    UPD_TS = CURRENT_TIMESTAMP
                WHERE SPACE_NM = :{len(params) - 1}
                  AND SQL_ID = :{len(params)}
                """,
                params,
            )
            conn.commit()

    # 理쒖쥌 STATUS_CONVERSION/STATUS_TUNING, retry, batch count瑜?NEXT_SQL_INFO????ν븳??
    def _update_job_status(self, sql_id: str, space_nm: str, status_conversion: str, elapsed_seconds: int, retry_count: int, status_tuning: str | None = None) -> None:
        assignments = ["STATUS_CONVERSION = :1", "RETRY_COUNT = :2", "BATCH_CNT = NVL(BATCH_CNT, 0) + 1", "LOG = :3", "UPD_TS = CURRENT_TIMESTAMP"]
        params: list[Any] = [status_conversion, retry_count, f"STATUS_CONVERSION={status_conversion}; elapsed={elapsed_seconds}s; retry={retry_count}"]
        if status_tuning:
            params.append(status_tuning)
            assignments.append(f"STATUS_TUNING = :{len(params)}")
        params.extend([space_nm, sql_id])
        table = self._qualify_table("NEXT_SQL_INFO", self.system_schema)
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                UPDATE {table}
                SET {", ".join(assignments)}
                WHERE SPACE_NM = :{len(params) - 1}
                  AND SQL_ID = :{len(params)}
                """,
                params,
            )
            conn.commit()

    # SQL Conversion ?④퀎蹂??대젰??NEXT_SQL_LOG??湲곕줉?쒕떎.
    def _write_log(self, sql_id: str, space_nm: str, sql_kind: str, status: str, stage_name: str, message: str, retry_count: int = 0, sql_content: str | None = None, elapsed_seconds: int | None = None, prompt_name: str | None = None) -> None:
        table = self._qualify_table("NEXT_SQL_LOG", self.system_schema)
        try:
            with self._connect() as conn:
                cur = conn.cursor()
                cur.execute(
                    f"""
                    INSERT INTO {table} (
                        CREATED_AT, SPACE_NM, SQL_ID, SQL_KIND, SQL_CONTENT,
                        STATUS, PROMPT_NAME, MODEL_NAME, ELAPSED_SECONDS,
                        ATTEMPT_NO, STAGE_NAME, ERROR_MESSAGE
                    ) VALUES (
                        CURRENT_TIMESTAMP, :1, :2, :3, :4,
                        :5, :6, :7, :8,
                        :9, :10, :11
                    )
                    """,
                    [
                        str(space_nm or "")[:200],
                        str(sql_id or "")[:200],
                        str(sql_kind or "")[:30],
                        sql_content,
                        str(status or "")[:20],
                        str(prompt_name or "")[:120] if prompt_name else None,
                        str(self.llm_model or "")[:120] if self.llm_model else None,
                        elapsed_seconds,
                        retry_count,
                        str(stage_name or "")[:100],
                        str(message or "")[:3900],
                    ],
                )
                conn.commit()
        except Exception:
            pass

    # ?ㅽ뻾??SQL?먯꽌 MyBatis ?쒓렇瑜?留됯퀬 LIMIT/FETCH 臾몃쾿??Oracle ?뺥깭濡??뺣━?쒕떎.
    def _prepare_runtime_sql(self, sql_text: str, stage: str) -> str:
        clean_sql = self._sanitize_to_sql(sql_text)
        lowered = clean_sql.lower()
        for token in ("<if", "<choose", "<when", "<otherwise", "<where", "<trim", "#{", "${"):
            if token in lowered:
                raise ValueError(f"{stage} generated non-executable SQL containing '{token}'")
        limit_match = re.search(r"\s+LIMIT\s+(\d+)\s*$", clean_sql, flags=re.I)
        if limit_match:
            limit = int(limit_match.group(1))
            inner = re.sub(r"\s+LIMIT\s+\d+\s*$", "", clean_sql, flags=re.I).strip()
            clean_sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {limit}"
        fetch_match = re.search(r"\s+FETCH\s+FIRST\s+(\d+)\s+ROWS\s+ONLY\s*$", clean_sql, flags=re.I)
        if fetch_match:
            limit = int(fetch_match.group(1))
            inner = re.sub(r"\s+FETCH\s+FIRST\s+\d+\s+ROWS\s+ONLY\s*$", "", clean_sql, flags=re.I).strip()
            clean_sql = f"SELECT * FROM ({inner}) WHERE ROWNUM <= {limit}"
        return clean_sql

    # dict row?먯꽌 而щ읆 ??뚮Ц??李⑥씠瑜?臾댁떆?섍퀬 媛믪쓣 爰쇰궦??
    def _get_row_value(self, row: dict[str, Any], key: str) -> Any:
        if key in row:
            return row[key]
        lowered = key.lower()
        for existing_key, value in row.items():
            if str(existing_key).lower() == lowered:
                return value
        return None

    # DB 媛믪쓣 JSON?쇰줈 蹂??媛?ν븳 媛믪쑝濡?諛붽씔??
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        if isinstance(value, (str, int, float, bool)):
            return value
        return str(value)

    # action step ?붿빟?먮뒗 ??SQL 蹂몃Ц???쒖쇅?섍퀬 ?묒? 媛믩쭔 ?④릿??
    def _summary_result(self, result: dict[str, Any]) -> dict[str, Any]:
        summary = {"ok": bool(result.get("ok")), "status": result.get("status")}
        for key in ["message", "error", "row_count", "elapsed_seconds", "retry_count"]:
            if key in result:
                summary[key] = result.get(key)
        return summary

    # TARGET_TABLE JSON 諛곗뿴?먯꽌 FR_TABLE 紐⑸줉??爰쇰궦??
    def _fallback_conversion_failure_status(self, to_sql: str, bind_sql: str, bind_set: str, test_sql: str) -> str:
        if not self._to_text(to_sql).strip():
            return "FAIL-TOBE"
        if not self._to_text(bind_sql).strip() or not self._to_text(bind_set).strip():
            return "FAIL-BIND"
        return "FAIL-TEST"

    def _extract_target_fr_tables(self, value: Any) -> list[str]:
        text = self._to_text(value).strip()
        if not text:
            return []
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            raise ValueError("TARGET_TABLE must be a JSON array like [\"table_a\", \"table_b\"]")
        names: list[str] = []
        for table_name in parsed:
            clean_table = str(table_name or "").strip()
            if clean_table and clean_table not in names:
                names.append(clean_table)
        return names[:50]

    # schema媛 遺숈? ?뚯씠釉붾챸? 留덉?留??뚯씠釉붾챸留??④꺼 鍮꾧탳 湲곗??쇰줈 ?ъ슜?쒕떎.
    def _normalize_table_name(self, value: Any) -> str:
        text = self._to_text(value).strip().strip('"').upper()
        if "." in text:
            text = text.split(".")[-1]
        return text

    # schema ?낅젰媛믪씠 ?덉쑝硫??뚯씠釉붾챸 ?욎뿉 schema瑜?遺숈씤??
    def _qualify_table(self, table_name: str, schema: str | None) -> str:
        clean = str(table_name or "").strip()
        clean_schema = str(schema or "").strip().upper()
        if not clean:
            raise ValueError("table_name is empty")
        if "." in clean or not clean_schema:
            return clean
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean_schema):
            raise ValueError(f"Invalid schema: {clean_schema}")
        return f"{clean_schema}.{clean}"

    # CLOB/bytes/None 媛믪쓣 ?덉쟾?섍쾶 臾몄옄?대줈 蹂?섑븳??
    def _to_text(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "read"):
            value = value.read()
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="ignore")
        return str(value)

    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if value is None:
            return False
        return str(value).strip().lower() in {"1", "true", "t", "y", "yes", "on"}

