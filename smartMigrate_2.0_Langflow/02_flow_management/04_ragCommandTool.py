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

try:
    from lfx.io import FloatInput
except Exception:
    FloatInput = IntInput


MAX_SIMILAR_SQL_RESULTS = 20
MAX_SIMILAR_SQL_CANDIDATES = 500


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
        FloatInput(name="min_similarity", display_name="Minimum Similarity", value=0.7, required=False, info="AS-IS SQL similarity minimum (0.0 to 1.0). Command JSON min_similarity overrides this value."),
    ]
    outputs = [Output(display_name="Result", name="result", method="run_command")]

    CATEGORIES = {"SQL_CONVERSION", "SQL_TUNING"}
    RULE_TYPES = {"GENERAL", "SEARCH"}

    # 명령 JSON을 해석하고 요청된 RAG 작업을 실행한다.
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

    # 조건에 맞는 RAG 가이드 규칙을 Oracle에서 조회한다.
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

    # AS-IS SQL과 유사한 SQL을 벡터 검색으로 찾고 상태 조건으로 필터링한다.
    def _search_similar_asis_sql(self, conn: Any, command: dict[str, Any]) -> dict[str, Any]:
        """Find AS-IS SQL neighbours, then filter with the authoritative Oracle status.

        The AS-IS collection deliberately has no status metadata.  This avoids a
        sync delay accidentally offering a now-PASS job for retry.
        """
        # 기준 SQL을 직접 입력값에서 받거나 SQL_ID, SPACE_NM 기준으로 Oracle에서 조회한다.
        query_sql, query_source, query_identity, query_target_table = self._similarity_query_sql(conn, command)

        # 상태 필터는 기본적으로 FAIL_ONLY이며, AS-IS 재시도 검색은 변환 상태만 허용한다.
        status_filter = self._status_filter(command.get("status_filter") or command.get("filter"))
        status_scope = self._conversion_only_scope(command.get("status_scope") or command.get("domain"))

        # 사용자에게 돌려줄 결과는 최대 20건으로 제한하고, 필터링 여유분을 위해 후보는 더 많이 가져온다.
        limit = max(1, min(self._positive_int(command.get("limit"), MAX_SIMILAR_SQL_RESULTS), MAX_SIMILAR_SQL_RESULTS))
        candidate_limit = max(limit, min(self._positive_int(command.get("candidate_limit"), MAX_SIMILAR_SQL_CANDIDATES), MAX_SIMILAR_SQL_CANDIDATES))

        # 명령에 min_similarity가 없으면 컴포넌트 기본값을 쓰고, 퍼센트 입력도 0~1 범위로 정규화한다.
        requested_min_similarity = command.get("min_similarity")
        if requested_min_similarity in (None, ""):
            requested_min_similarity = getattr(self, "min_similarity", 0.7)
        min_similarity = self._similarity_threshold(requested_min_similarity)

        # 기준 SQL을 임베딩 API에 보내기 전에 정규화하고, 검색용 dense vector로 변환한다.
        config = self._milvus_config()
        client = self._milvus_client(config)
        vector, query_vector_source = self._stored_asis_query_vector(client, config, query_sql, query_identity)
        if vector is None:
            vector = self._embed_texts([self._sql_content(query_sql)], self._embed_config())[0]
            query_vector_source = "EMBEDDING_API"

        # Milvus AS-IS SQL 컬렉션에서 활성 문서만 대상으로 코사인 유사도 검색을 수행한다.
        hits = client.search(
            collection_name=config["asis_sql_collection"],
            data=[vector],
            anns_field="dense_vector",
            limit=candidate_limit,
            filter="is_active == true",
            output_fields=["sql_seq", "sql_id", "space_nm", "tag_kind", "target_table"],
            search_params={"metric_type": "COSINE", "params": {}},
        )

        # Milvus 응답을 후보 SQL 목록으로 변환하고, 후보들의 최신 변환 상태는 Oracle에서 다시 확인한다.
        candidates = self._milvus_hits(hits)
        statuses = self._load_sql_statuses(conn, candidates)
        matches: list[dict[str, Any]] = []
        for candidate in candidates:
            # 임계 유사도보다 낮은 후보는 제외한다.
            if min_similarity is not None and candidate["similarity"] < min_similarity:
                continue

            # 기준 SQL 자신은 include_self 요청이 있을 때만 결과에 포함한다.
            identity = self._identity_key(candidate.get("sql_id"), candidate.get("space_nm"))
            if query_identity and identity == query_identity and not self._as_bool(command.get("include_self")):
                continue

            # Milvus에는 상태 메타데이터가 없으므로 Oracle에서 조회한 상태가 없는 후보는 제외한다.
            status = statuses.get(identity)
            if not status:
                continue

            # FAIL_ONLY, PASS_ONLY, ALL 조건에 맞는 상태만 최종 후보로 남긴다.
            matched_statuses = self._matching_statuses(status, status_scope, status_filter)
            if not matched_statuses:
                continue

            # 대상 테이블이 겹치는지 계산해 최종 정렬 우선순위에 활용한다.
            overlapping_target_tables = self._overlapping_target_tables(query_target_table, candidate.get("target_table"))
            matches.append(
                {
                    **candidate,
                    "status_conversion": status["status_conversion"],
                    "matched_statuses": matched_statuses,
                    "target_table_overlap": bool(overlapping_target_tables),
                    "overlapping_target_tables": overlapping_target_tables,
                }
            )

        # 대상 테이블이 겹치는 후보를 먼저 보여주고, 같은 그룹 안에서는 벡터 유사도가 높은 순서로 정렬한다.
        matches.sort(key=lambda item: (not item["target_table_overlap"], -item["similarity"]))

        # 정렬된 후보 중 사용자 응답 제한 건수만 남긴다.
        matches = matches[:limit]

        # 상태 변경은 수행하지 않고, 후속 요청에 사용할 예시 문장과 검색 결과만 반환한다.
        return {
            "ok": True,
            "action": "search_similar_asis_sql",
            "query_source": query_source,
            "query_vector_source": query_vector_source,
            "query_target_table": query_target_table,
            "status_filter": status_filter,
            "status_scope": status_scope,
            "min_similarity": min_similarity,
            "result_limit": limit,
            "candidate_count": len(candidates),
            "row_count": len(matches),
            "data": {"similar_sqls": matches},
        }

    def _stored_asis_query_vector(
        self,
        client: Any,
        config: dict[str, str],
        query_sql: str,
        query_identity: tuple[str, str] | None,
    ) -> tuple[list[float] | None, str]:
        """Reuse the synced AS-IS vector only for the exact current SQL row."""
        if not query_identity:
            return None, "EMBEDDING_API"
        sql_id, space_nm = query_identity
        try:
            rows = client.query(
                collection_name=config["asis_sql_collection"],
                filter=(
                    f"sql_id == {json.dumps(sql_id, ensure_ascii=False)} "
                    f"and space_nm == {json.dumps(space_nm, ensure_ascii=False)} and is_active == true"
                ),
                output_fields=["dense_vector", "fr_sql", "edit_fr_sql"],
                limit=1,
            )
            row = rows[0] if isinstance(rows, list) and rows else {}
            stored_sql = str(row.get("edit_fr_sql") or row.get("fr_sql") or "").strip() if isinstance(row, dict) else ""
            vector = row.get("dense_vector") if isinstance(row, dict) else None
            if stored_sql == str(query_sql or "").strip() and isinstance(vector, list) and vector:
                return [float(value) for value in vector], "SM_ASIS_SQL"
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").warning("SM_ASIS_SQL vector reuse skipped: %s", exc)
        return None, "EMBEDDING_API"

    # 유사도 검색에 사용할 기준 SQL과 식별 정보를 명령 또는 DB에서 가져온다.
    def _similarity_query_sql(self, conn: Any, command: dict[str, Any]) -> tuple[str, str, tuple[str, str] | None, str]:
        for field in ("query_sql", "sql", "fr_sql", "edit_fr_sql"):
            value = str(command.get(field) or "").strip()
            if value:
                return value, field, None, str(command.get("target_table") or "").strip()
        sql_id = str(command.get("sql_id") or "").strip()
        space_nm = str(command.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError("query_sql (or sql/fr_sql) or both sql_id and space_nm are required")
        cur = conn.cursor()
        cur.execute(
            f"""
            SELECT EDIT_FR_SQL, FR_SQL, TARGET_TABLE
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
        return (
            str(sql_text).strip(),
            "sql_id+space_nm",
            self._identity_key(sql_id, space_nm),
            str(self._json_value(row[2]) or "").strip(),
        )

    # Milvus 후보 SQL들의 변환 상태를 Oracle에서 일괄 조회한다.
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
            SELECT SQL_ID, SPACE_NM, STATUS_CONVERSION
              FROM {self._qualify('NEXT_SQL_INFO')}
             WHERE {' OR '.join(conditions)}
            """,
            params,
        )
        return {
            self._identity_key(row[0], row[1]): {
                "status_conversion": str(self._json_value(row[2]) or "").strip().upper(),
            }
            for row in cur.fetchall()
        }

    # Milvus 검색 응답을 화면과 후속 처리에 쓰기 쉬운 후보 목록으로 변환한다.
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
                "similarity_percent": round(float(hit.get("distance", hit.get("score", 0.0)) or 0.0) * 100, 1),
            })
        return result

    # 상태 필터 입력값을 FAIL_ONLY, PASS_ONLY, ALL 중 하나로 정규화한다.
    def _status_filter(self, value: Any) -> str:
        normalized = str(value or "FAIL_ONLY").strip().upper().replace("-", "_")
        aliases = {"FAIL": "FAIL_ONLY", "FAILED": "FAIL_ONLY", "PASS": "PASS_ONLY", "ALL": "ALL", "ANY": "ALL"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"FAIL_ONLY", "PASS_ONLY", "ALL"}:
            raise ValueError("status_filter must be FAIL_ONLY, PASS_ONLY, or ALL")
        return normalized

    # 상태 확인 범위를 CONVERSION, TUNING, ANY 중 하나로 정규화한다.
    def _status_scope(self, value: Any) -> str:
        normalized = str(value or "CONVERSION").strip().upper().replace("-", "_")
        aliases = {"SQL_CONVERSION": "CONVERSION", "SQL_TUNING": "TUNING", "BOTH": "ANY", "ALL": "ANY"}
        normalized = aliases.get(normalized, normalized)
        if normalized not in {"CONVERSION", "TUNING", "ANY"}:
            raise ValueError("status_scope must be CONVERSION, TUNING, or ANY")
        return normalized

    # AS-IS SQL 재시도 검색이 변환 상태만 보도록 범위를 제한한다.
    def _conversion_only_scope(self, value: Any) -> str:
        """AS-IS retry candidates are intentionally based on conversion status only."""
        requested = self._status_scope(value)
        if requested != "CONVERSION":
            raise ValueError("AS-IS SQL similarity retry search supports STATUS_CONVERSION only; status_scope must be CONVERSION")
        return requested

    # 유사도 임계값을 0에서 1 사이의 실수로 변환한다.
    def _similarity_threshold(self, value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            threshold = float(str(value).strip().rstrip("%"))
        except (TypeError, ValueError) as exc:
            raise ValueError("min_similarity must be a number between 0 and 1, or a percentage such as 80") from exc
        if threshold > 1:
            threshold /= 100
        if not 0 <= threshold <= 1:
            raise ValueError("min_similarity must be between 0 and 1, or between 0 and 100 percent")
        return threshold

    # 조회된 상태 중 요청한 범위와 필터에 일치하는 상태명을 반환한다.
    def _matching_statuses(self, status: dict[str, str], scope: str, status_filter: str) -> list[str]:
        columns = {"CONVERSION": status.get("status_conversion", ""), "TUNING": status.get("status_tuning", "")}
        selected = ("CONVERSION", "TUNING") if scope == "ANY" else (scope,)
        if status_filter == "ALL":
            return [name for name in selected]
        prefix = "FAIL-" if status_filter == "FAIL_ONLY" else "PASS"
        return [name for name in selected if columns[name].startswith(prefix)]

    # 유사 SQL 검색 결과로부터 실행 요청 예시 문장을 만든다.
    def _execution_request_examples(self, matches: list[dict[str, Any]]) -> list[str]:
        examples = []
        for match in matches:
            domains = match.get("matched_statuses") or []
            for domain in domains:
                action = "SQL Conversion" if domain == "CONVERSION" else "SQL Tuning"
                examples.append(f"SQL_ID={match['sql_id']}, SPACE_NM={match['space_nm']} {action} 실행해줘.")
        return examples

    # 유사 SQL 검색 결과로부터 상태 초기화 요청 예시 문장을 만든다.
    def _status_reset_request_examples(self, matches: list[dict[str, Any]]) -> list[str]:
        return [
            f"SQL_ID={match['sql_id']}, SPACE_NM={match['space_nm']} 재시도 상태로 변경해줘."
            for match in matches
        ]

    # SQL_ID와 SPACE_NM을 비교용 대문자 식별 키로 만든다.
    def _identity_key(self, sql_id: Any, space_nm: Any) -> tuple[str, str]:
        return str(sql_id or "").strip().upper(), str(space_nm or "").strip().upper()

    # 기준 SQL과 후보 SQL의 대상 테이블 교집합을 계산한다.
    def _overlapping_target_tables(self, query_target_table: Any, candidate_target_table: Any) -> list[str]:
        query_tables = self._target_table_tokens(query_target_table)
        candidate_tables = self._target_table_tokens(candidate_target_table)
        return sorted(query_tables & candidate_tables)

    # 대상 테이블 문자열을 스키마명 포함 여부와 무관한 비교 토큰으로 분리한다.
    def _target_table_tokens(self, value: Any) -> set[str]:
        """Normalise comma/space-delimited table scopes, including schema aliases."""
        tokens: set[str] = set()
        for raw in re.findall(r"[A-Za-z][A-Za-z0-9_$#.]*", str(value or "").upper()):
            clean = raw.strip(".")
            if clean:
                tokens.add(clean)
                tokens.add(clean.rsplit(".", 1)[-1])
        return tokens

    # 새 RAG 가이드 규칙을 Oracle 테이블에 등록한다.
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

    # 지정한 RAG_ID의 RAG 가이드 규칙 필드를 수정한다.
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

    # 지정한 RAG_ID의 규칙을 비활성화 처리한다.
    def _disable(self, conn: Any, rag_id: int) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET USE_YN = 'N', UPDATED_AT = SYSTIMESTAMP WHERE RAG_ID = :rag_id", {"rag_id": rag_id})
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row not found. rowcount={cur.rowcount}")
        return {"ok": True, "action": "disable", "rag_id": rag_id, "updated_rows": int(cur.rowcount), "needs_vector_sync": True}

    # 작업 결과를 사용자가 읽기 쉬운 응답 문장으로 변환한다.
    def _answer(self, result: dict[str, Any]) -> str:
        action = result.get("action")
        if action == "query":
            return f"RAG Guide query completed: {result.get('row_count', 0)} row(s)."
        if action == "search_similar_asis_sql":
            return (
                f"AS-IS SQL similarity search completed: {result.get('row_count', 0)} row(s) "
                f"matched with status_filter={result.get('status_filter')}, limit={result.get('result_limit')}, "
                f"min_similarity={result.get('min_similarity')}. "
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

    # 입력 명령에서 RAG 규칙 필드를 정규화하고 기본 검증을 수행한다.
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

    # 카테고리와 규칙 유형에 맞게 필수 필드 조합을 검증한다.
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

    # Langflow 입력에서 명령 JSON을 읽어 딕셔너리로 파싱한다.
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

    # Oracle DB 연결을 생성하고 사용 후 닫는 컨텍스트를 제공한다.
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

    # 명령에서 필수 RAG_ID를 양의 정수로 추출한다.
    def _required_rag_id(self, command: dict[str, Any]) -> int:
        try:
            rag_id = int(str(command.get("rag_id") or "").strip())
        except (TypeError, ValueError):
            rag_id = 0
        if rag_id <= 0:
            raise ValueError("rag_id is required")
        return rag_id

    # 필수 카테고리 값을 검증하고 반환한다.
    def _required_category(self, value: Any) -> str:
        category = self._optional_category(value)
        if not category:
            raise ValueError("category must be SQL_CONVERSION or SQL_TUNING")
        return category

    # 선택 카테고리 값을 허용 목록 기준으로 정규화한다.
    def _optional_category(self, value: Any) -> str:
        category = str(value or "").strip().upper()
        return category if category in self.CATEGORIES else ""

    # 필수 규칙 유형 값을 검증하고 반환한다.
    def _required_rule_type(self, value: Any) -> str:
        rule_type = self._optional_rule_type(value)
        if not rule_type:
            raise ValueError("rule_type must be GENERAL or SEARCH")
        return rule_type

    # 선택 규칙 유형 값을 허용 목록 기준으로 정규화한다.
    def _optional_rule_type(self, value: Any) -> str:
        rule_type = str(value or "").strip().upper()
        return rule_type if rule_type in self.RULE_TYPES else ""

    # 필수 사용 여부 값을 Y 또는 N으로 검증한다.
    def _required_use_yn(self, value: Any) -> str:
        use_yn = self._optional_use_yn(value)
        if not use_yn:
            raise ValueError("use_yn must be Y or N")
        return use_yn

    # 선택 사용 여부 값을 Y 또는 N으로 정규화한다.
    def _optional_use_yn(self, value: Any) -> str:
        use_yn = str(value or "").strip().upper()
        return use_yn if use_yn in {"Y", "N"} else ""

    # 소스 테이블 입력을 쉼표 구분 대문자 문자열로 정리한다.
    def _source_tables(self, value: Any) -> str:
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        return str(value or "").strip().upper()

    # 조회 제한 건수를 기본값과 최대값 범위 안으로 보정한다.
    def _limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "default_limit", None), 10)
        return max(1, min(self._positive_int(value, default), 100))

    # 입력값을 양의 정수로 변환하고 실패하면 기본값을 반환한다.
    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value or 0)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    # 다양한 문자열 표현을 불리언 값으로 해석한다.
    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    # CLOB 등 직렬화하기 어려운 값을 JSON 친화적인 값으로 변환한다.
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    # 입력값과 환경변수에서 Milvus 접속 설정을 구성하고 검증한다.
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

    # Milvus 검색에 사용할 클라이언트 객체를 생성한다.
    def _milvus_client(self, config: dict[str, str]) -> Any:
        from pymilvus import MilvusClient

        return MilvusClient(
            uri=config["uri"],
            user=config["username"],
            password=config["password"],
            db_name=config["db_name"],
            timeout=10,
        )

    # 임베딩 API 호출에 필요한 설정을 입력값과 환경변수에서 구성한다.
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

    # 임베딩 API를 호출해 SQL 텍스트 목록을 벡터 목록으로 변환한다.
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

    # SQL에서 주석과 리터럴을 정리해 임베딩용 텍스트를 만든다.
    def _sql_content(self, sql_text: str) -> str:
        source = str(sql_text or "").strip()
        normalized = re.sub(r"/\*.*?\*/", " ", source, flags=re.DOTALL)
        normalized = re.sub(r"--[^\n]*", " ", normalized)
        normalized = re.sub(r"'(?:''|[^'])*'", " STR ", normalized)
        normalized = re.sub(r"\b\d+(?:\.\d+)?\b", " NUM ", normalized)
        normalized = re.sub(r"\s+", " ", normalized).strip().upper()
        return "\n".join(part for part in (normalized, source) if part).strip()

    # 시스템 스키마와 테이블명을 안전한 정규화된 전체 테이블명으로 조합한다.
    def _qualify(self, table: str) -> str:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema is required")
        return f"{self._clean_identifier(schema)}.{self._clean_identifier(table)}"

    # SQL 식별자가 허용된 문자 규칙을 만족하는지 검증한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # SecretStr 또는 일반 값을 안전하게 문자열 비밀번호로 변환한다.
    def _secret_to_str(self, value: Any) -> str:
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)

    # Oracle CLOB 바인딩에 사용할 타입 객체를 반환한다.
    def _clob_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_CLOB", getattr(oracledb, "CLOB", None))

    # Oracle NUMBER 바인딩에 사용할 타입 객체를 반환한다.
    def _number_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_NUMBER", int)
