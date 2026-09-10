from __future__ import annotations

import json
import logging
import re
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


class NewType04RagGuideManager(Component):
    display_name = "04 RAG Guide Manager"
    description = "Adds, updates, disables, deletes, or looks up NEXT_MIG_RAG_INFO rules/guides."
    name = "NewType04RagGuideManager"
    icon = "BookMarked"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
        IntInput(name="default_limit", display_name="Default Limit", value=10, required=False),
        IntInput(name="max_text_chars", display_name="Max Text Chars", value=4000, required=False),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    CATEGORIES = {"SQL_CONVERSION", "SQL_TUNING"}
    RULE_TYPES = {"GENERAL", "SEARCH"}

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def run(self) -> Message:
        logging.getLogger("smartmigrate.workflow").info(
            "04 RAG Guide Manager started",
            extra={"workflow_log": [0, "WORKFLOW", "04_RAG_GUIDE_MANAGER", "INFO", "RUN", "START", 0]},
        )
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            action = self._action(payload)
            with self._connect() as conn:
                if action in {"query", "list", "get"}:
                    result = self._query(conn, payload)
                elif action in {"add", "insert", "create"}:
                    result = self._insert(conn, self._rule_payload(payload))
                elif action in {"update", "modify"}:
                    result = self._update(conn, self._rule_payload(payload), partial=True)
                elif action == "upsert":
                    rule = self._rule_payload(payload)
                    result = self._update(conn, rule, partial=False) if rule.get("rag_id") else self._insert(conn, rule)
                elif action in {"disable", "soft_delete", "delete"}:
                    result = self._disable(conn, self._required_rag_id(payload))
                else:
                    raise ValueError(f"Unsupported RAG guide action: {action}")
                conn.commit()
            answer = self._answer(result)
            self.status = {**result, "component": "04_ragGuideManager", "answer_text": answer, "final": True}
            logging.getLogger("smartmigrate.workflow").info(
                answer,
                extra={"workflow_log": [result.get("rag_id") or 0, "WORKFLOW", "04_RAG_GUIDE_MANAGER", "INFO", action.upper(), "PASS", 0]},
            )
            return Message(text=answer)
        except Exception as exc:
            answer = f"RAG Guide 처리 실패: {exc}"
            self.status = {"ok": False, "component": "04_ragGuideManager", "error": str(exc), "answer_text": answer}
            logging.getLogger("smartmigrate.workflow").error(
                answer,
                extra={"workflow_log": [0, "WORKFLOW", "04_RAG_GUIDE_MANAGER", "ERROR", "RUN", "ERROR", 0]},
            )
            return Message(text=answer)

    # RAG guide 조회 조건을 조립해 NEXT_MIG_RAG_INFO에서 목록을 가져온다.
    def _query(self, conn: Any, payload: dict[str, Any]) -> dict[str, Any]:
        rag = dict(payload.get("rag") or {})
        rag_id = str(payload.get("rag_id") or rag.get("rag_id") or "").strip()
        category = self._optional_category(payload.get("category") or rag.get("category"))
        rule_type = self._optional_rule_type(payload.get("rule_type") or rag.get("rule_type"))
        use_yn = self._optional_use_yn(payload.get("use_yn") or rag.get("use_yn"))
        keyword = str(payload.get("keyword") or rag.get("keyword") or "").strip()
        limit = self._limit(payload.get("limit") or rag.get("limit"))
        full_text = self._as_bool(payload.get("full_text") or rag.get("full_text"))

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
        return {"ok": True, "action": "query", "updated_rows": 0, "row_count": len(rows), "data": {"rules": rows}}

    # 새 RAG guide row를 NEXT_MIG_RAG_INFO에 추가하고 생성된 RAG_ID를 반환한다.
    def _insert(self, conn: Any, rule: dict[str, Any]) -> dict[str, Any]:
        normalized = self._normalized_rule(rule, require_content=True, partial=False)
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
            {**normalized, "rag_id": rag_id_var},
        )
        return {"ok": True, "action": "add", "rag_id": int(rag_id_var.getvalue()[0]), "updated_rows": int(cur.rowcount)}

    # RAG_ID 기준으로 RAG guide 필드를 수정하고 정확히 한 건만 갱신됐는지 확인한다.
    def _update(self, conn: Any, rule: dict[str, Any], *, partial: bool) -> dict[str, Any]:
        rag_id = self._required_rag_id(rule)
        normalized = self._normalized_rule(rule, require_content=not partial, partial=partial)
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
            if key in normalized:
                assignments.append(f"{column} = :{key}")
                params[key] = normalized[key]
        if not assignments:
            raise ValueError("수정할 RAG guide 필드가 없습니다.")
        assignments.append("UPDATED_AT = SYSTIMESTAMP")
        cur = conn.cursor()
        cur.setinputsizes(**{key: self._clob_type() for key in ("guidance_text", "source_sql", "target_sql") if key in params})
        cur.execute(
            f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET {', '.join(assignments)} WHERE RAG_ID = :rag_id",
            params,
        )
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row를 찾지 못했습니다. count={cur.rowcount}")
        return {"ok": True, "action": "update", "rag_id": rag_id, "updated_rows": int(cur.rowcount)}

    # RAG guide를 물리 삭제하지 않고 USE_YN=N으로 비활성화한다.
    def _disable(self, conn: Any, rag_id: int) -> dict[str, Any]:
        cur = conn.cursor()
        cur.execute(
            f"UPDATE {self._qualify('NEXT_MIG_RAG_INFO')} SET USE_YN = 'N', UPDATED_AT = SYSTIMESTAMP WHERE RAG_ID = :rag_id",
            {"rag_id": rag_id},
        )
        if cur.rowcount != 1:
            raise ValueError(f"RAG_ID={rag_id} row를 찾지 못했습니다. count={cur.rowcount}")
        return {"ok": True, "action": "disable", "rag_id": rag_id, "updated_rows": int(cur.rowcount)}

    # RAG guide 입력을 category/rule_type/source/content 표준 필드로 검증·정규화한다.
    def _normalized_rule(self, rule: dict[str, Any], *, require_content: bool, partial: bool) -> dict[str, Any]:
        result: dict[str, Any] = {}
        if not partial or "category" in rule:
            result["category"] = self._required_category(rule.get("category"))
        if not partial or "rule_type" in rule:
            result["rule_type"] = self._required_rule_type(rule.get("rule_type"))
        if not partial or "source_tables" in rule:
            result["source_tables"] = self._source_tables(rule.get("source_tables"))
        if not partial or "use_yn" in rule:
            result["use_yn"] = self._required_use_yn(rule.get("use_yn") or "Y")
        for key in ("guidance_text", "source_sql", "target_sql"):
            if not partial or key in rule:
                result[key] = str(rule.get(key) or "").strip()
        if require_content and not any(str(result.get(key) or "").strip() for key in ("guidance_text", "source_sql", "target_sql")):
            raise ValueError("GUIDANCE_TEXT, SOURCE_SQL, TARGET_SQL 중 최소 1개는 필요합니다.")
        self._validate_rule_shape(result, require_complete=require_content and not partial)
        return result

    # category/rule_type 조합별 필수 입력을 검사한다.
    def _validate_rule_shape(self, rule: dict[str, Any], *, require_complete: bool) -> None:
        category = str(rule.get("category") or "").strip().upper()
        rule_type = str(rule.get("rule_type") or "").strip().upper()
        source_tables = str(rule.get("source_tables") or "").strip()
        guidance_text = str(rule.get("guidance_text") or "").strip()
        source_sql = str(rule.get("source_sql") or "").strip()
        target_sql = str(rule.get("target_sql") or "").strip()
        if category == "SQL_CONVERSION" and rule_type == "SEARCH" and not source_tables:
            raise ValueError("SQL_CONVERSION SEARCH RAG guide에는 SOURCE_TABLES가 필요합니다.")
        if rule_type == "SEARCH" and require_complete and (not source_sql or not target_sql):
            raise ValueError("SEARCH RAG guide 추가에는 SOURCE_SQL과 TARGET_SQL을 모두 입력해야 합니다.")
        if bool(source_sql) != bool(target_sql):
            raise ValueError("SOURCE_SQL과 TARGET_SQL은 둘 다 입력하거나 둘 다 비워야 합니다.")
        if rule_type == "GENERAL" and require_complete and not guidance_text:
            raise ValueError("GENERAL RAG guide 추가에는 GUIDANCE_TEXT가 필요합니다.")

    # 조회/실행 결과를 사용자가 읽을 Markdown 메시지로 만든다.
    def _answer(self, result: dict[str, Any]) -> str:
        action = result.get("action")
        if action == "query":
            return self._query_answer(result)
        detail = f" {result.get('message')}" if result.get("message") else ""
        sync_note = " Milvus RAG 검색에 반영하려면 00B Sync Milvus Vector DB를 실행하세요."
        return f"RAG Guide {action} 완료: RAG_ID={result.get('rag_id')}, updated_rows={result.get('updated_rows', 0)}.{detail}{sync_note}"

    # NEXT_MIG_RAG_INFO 조회 결과를 source/guidance/target 본문까지 포함한 답변으로 만든다.
    def _query_answer(self, result: dict[str, Any]) -> str:
        rows = list((result.get("data") or {}).get("rules") or [])
        if not rows:
            return "RAG Guide 조회 완료: 0건"
        parts = [f"RAG Guide 조회 완료: {len(rows)}건"]
        for index, row in enumerate(rows, start=1):
            parts.append(
                "\n".join(
                    [
                        "",
                        f"## {index}. RAG_ID={row.get('rag_id')}",
                        f"- CATEGORY: {row.get('category') or ''}",
                        f"- RULE_TYPE: {row.get('rule_type') or ''}",
                        f"- SOURCE_TABLES: {row.get('source_tables') or ''}",
                        f"- USE_YN: {row.get('use_yn') or ''}",
                        f"- HIT_CNT: {row.get('hit_cnt') or 0}",
                        f"- UPDATED_AT: {row.get('updated_at') or ''}",
                        "",
                        "### GUIDANCE_TEXT",
                        self._markdown_block(row.get("guidance_text")),
                        "",
                        "### SOURCE_SQL",
                        self._markdown_block(row.get("source_sql")),
                        "",
                        "### TARGET_SQL",
                        self._markdown_block(row.get("target_sql")),
                    ]
                )
            )
        return "\n".join(parts)

    # Markdown code fence가 깨지지 않도록 조회 본문을 문자열 block으로 감싼다.
    def _markdown_block(self, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            return "(empty)"
        return f"```text\n{text.replace('```', '` ` `')}\n```"

    # payload 여러 위치에 흩어진 RAG guide 입력값을 하나의 rule dict로 모은다.
    def _rule_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        rule = dict(payload.get("rag") or payload.get("guide") or payload.get("rule") or {})
        for key in ("rag_id", "category", "rule_type", "source_tables", "use_yn", "guidance_text", "source_sql", "target_sql"):
            if key in payload and key not in rule:
                rule[key] = payload[key]
        if "source_table" in payload and "source_tables" not in rule:
            rule["source_tables"] = payload["source_table"]
        return rule

    # RAG guide 요청 action을 query/add/update/delete 계열 문자열로 정규화한다.
    def _action(self, payload: dict[str, Any]) -> str:
        return str(payload.get("rag_action") or payload.get("action") or "").strip().lower() or "query"

    # 수정/삭제 대상 RAG_ID를 필수 정수 값으로 검증한다.
    def _required_rag_id(self, payload: dict[str, Any]) -> int:
        raw = payload.get("rag_id") or (payload.get("rag") or {}).get("rag_id")
        try:
            rag_id = int(str(raw or "").strip())
        except (TypeError, ValueError):
            rag_id = 0
        if rag_id <= 0:
            raise ValueError("RAG guide 수정/삭제에는 rag_id가 필요합니다.")
        return rag_id

    # RAG category가 허용된 필수 값인지 검증한다.
    def _required_category(self, value: Any) -> str:
        category = self._optional_category(value)
        if not category:
            raise ValueError("category는 SQL_CONVERSION 또는 SQL_TUNING이어야 합니다.")
        return category

    # 선택 category 입력이 허용값이면 반환하고 아니면 빈 값으로 둔다.
    def _optional_category(self, value: Any) -> str:
        category = str(value or "").strip().upper()
        return category if category in self.CATEGORIES else ""

    # RAG rule_type이 허용된 필수 값인지 검증한다.
    def _required_rule_type(self, value: Any) -> str:
        rule_type = self._optional_rule_type(value or "SEARCH")
        if not rule_type:
            raise ValueError("rule_type은 GENERAL 또는 SEARCH여야 합니다.")
        return rule_type

    # 선택 rule_type 입력이 허용값이면 반환하고 아니면 빈 값으로 둔다.
    def _optional_rule_type(self, value: Any) -> str:
        rule_type = str(value or "").strip().upper()
        return rule_type if rule_type in self.RULE_TYPES else ""

    # USE_YN 입력이 Y/N 중 하나인지 필수 값으로 검증한다.
    def _required_use_yn(self, value: Any) -> str:
        use_yn = self._optional_use_yn(value)
        if not use_yn:
            raise ValueError("use_yn은 Y 또는 N이어야 합니다.")
        return use_yn

    # 선택 USE_YN 입력이 Y/N이면 반환하고 아니면 빈 값으로 둔다.
    def _optional_use_yn(self, value: Any) -> str:
        use_yn = str(value or "").strip().upper()
        return use_yn if use_yn in {"Y", "N"} else ""

    # 테이블 범위 입력을 쉼표로 구분된 대문자 테이블 목록으로 정리한다.
    def _source_tables(self, value: Any) -> str:
        if isinstance(value, list):
            value = ",".join(str(item) for item in value)
        return str(value or "").strip().upper()

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
    def _connect(self):
        import oracledb

        dsn = oracledb.makedsn(
            str(self.db_host).strip(),
            int(getattr(self, "db_port", None) or 1521),
            service_name=str(self.db_service_name).strip(),
        )
        conn = oracledb.connect(
            user=str(self.db_username).strip(),
            password=self._secret(getattr(self, "db_password", None)),
            dsn=dsn,
        )
        try:
            yield conn
        finally:
            conn.close()

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table: str) -> str:
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        return f"{self._clean_identifier(schema)}.{self._clean_identifier(table)}"

    # 동적 SQL identifier에 안전한 Oracle 문자만 허용한다.
    def _clean_identifier(self, value: str) -> str:
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
        value = json.loads(text) if text else {}
        if not isinstance(value, dict):
            raise ValueError("payload_json must be a JSON object")
        return value

    # QA 조회 row 수와 전문 출력 제한 값을 허용 범위 안으로 보정한다.
    def _limit(self, value: Any) -> int:
        default = self._positive_int(getattr(self, "default_limit", None), 10)
        return max(1, min(self._positive_int(value, default), 100))

    # 숫자 입력을 양의 정수로 변환하고 실패하면 기본값을 사용한다.
    def _positive_int(self, value: Any, default: int) -> int:
        try:
            parsed = int(value or 0)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    # 문자열/숫자/boolean 입력을 Langflow 옵션용 boolean 값으로 정규화한다.
    def _as_bool(self, value: Any) -> bool:
        if isinstance(value, bool):
            return value
        return str(value or "").strip().lower() in {"1", "true", "t", "y", "yes", "on"}

    # payload나 로그에 넣을 값을 JSON 직렬화 가능한 형태로 정리한다.
    def _json_value(self, value: Any) -> Any:
        if value is None:
            return None
        if hasattr(value, "read"):
            value = value.read()
        return value if isinstance(value, (str, int, float, bool)) else str(value)

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret(self, value: Any) -> str:
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")

    # Oracle CLOB bind type을 지연 import로 가져와 긴 guide/sql 저장에 사용한다.
    def _clob_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_CLOB", getattr(oracledb, "CLOB", None))

    # Oracle NUMBER bind type을 지연 import로 가져와 RETURNING RAG_ID에 사용한다.
    def _number_type(self) -> Any:
        import oracledb

        return getattr(oracledb, "DB_TYPE_NUMBER", int)
