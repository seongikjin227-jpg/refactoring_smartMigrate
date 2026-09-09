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


# =============================================================================
# 04 Correct SQL Input
# =============================================================================
# Management Router가 CORRECT_SQL_INPUT으로 분기한 payload를 받아 사용자가 직접
# 제공한 SQL 본문을 DB에 저장하는 컴포넌트다.
#
# 핵심 원칙:
# - 이 컴포넌트는 SQL을 생성하거나 보정하지 않는다.
# - correct_sql 값은 사용자가 제공한 원문 그대로 저장한다.
# - 저장 성공 시 USER_EDITED='Y'로 표시해서 이후 executor가 사용자 교정 SQL을
#   신뢰 가능한 입력 또는 재실행 후보로 인식할 수 있게 한다.
#
# 저장 대상:
# - DB_MIGRATION: NEXT_MIG_INFO.MIG_SQL 또는 NEXT_MIG_INFO.VERIFY_SQL
# - SQL_*: NEXT_SQL_INFO.TO_SQL/BIND_SQL/TEST_SQL/TUNED_TO_SQL/FORMATTED_SQL
#
# 안전장치:
# - Router가 추출한 table/column 값을 그대로 신뢰하지 않고 _target()에서 다시 검증한다.
# - UPDATE 결과가 정확히 1건이 아니면 rollback한다.
# - 테이블에 필요한 컬럼이 실제로 존재하는지 USER_TAB_COLUMNS/ALL_TAB_COLUMNS로 확인한다.
# =============================================================================
class NewType04CorrectSqlInput(Component):
    display_name = "04 Correct SQL Input"
    description = "Saves a router-validated corrected SQL and marks the row USER_EDITED='Y'."
    name = "NewType04CorrectSqlInput"
    icon = "FilePenLine"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=False),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    def run(self) -> Message:
        # 전체 저장 흐름:
        # 1. Langflow payload를 dict로 파싱한다.
        # 2. target/work_type/sql_column 조합을 검증하고 실제 UPDATE 대상을 만든다.
        # 3. DB metadata에서 컬럼 존재 여부를 확인한다.
        # 4. correct_sql을 CLOB 컬럼에 저장하고 USER_EDITED='Y'를 세팅한다.
        logging.getLogger("smartmigrate.workflow").info(
            "04 Correct SQL Input started",
            extra={"workflow_log": [0, "WORKFLOW", "04_CORRECT_SQL_INPUT", "INFO", "SAVE_SQL", "START", 0]},
        )
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            target = dict(payload.get("target") or {})
            sql_text = str(payload.get("correct_sql") or "").strip()
            table, column, where_sql, params, identity = self._target(target, sql_text)

            with self._connect() as conn:
                columns = self._columns(conn, table)
                if column not in columns or "USER_EDITED" not in columns:
                    raise ValueError(f"{table}에 Correct SQL 저장에 필요한 컬럼({column}, USER_EDITED)이 없습니다.")

                cur = conn.cursor()
                cur.execute(
                    f"UPDATE {self._qualify(table)} SET {column} = :correct_sql, USER_EDITED = 'Y' WHERE {where_sql}",
                    {**params, "correct_sql": sql_text},
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError(f"저장 대상 작업을 정확히 1건 찾지 못했습니다. ({identity}, count={cur.rowcount})")
                conn.commit()

            answer = f"Correct SQL 저장 완료: {table}.{column}, {identity}. USER_EDITED='Y'로 변경했습니다."
            self.status = {**payload, "component": "04_correctSqlInput", "updated_rows": 1, "answer_text": answer, "final": True}
            log_id = target.get("map_id") or f"{target.get('sql_id') or ''} / {target.get('space_nm') or ''}".strip(" / ") or 0
            logging.getLogger("smartmigrate.workflow").info(
                answer,
                extra={"workflow_log": [log_id, "WORKFLOW", "04_CORRECT_SQL_INPUT", "INFO", column, "PASS", 0, sql_text]},
            )
            return Message(text=answer)
        except Exception as exc:
            answer = f"Correct SQL 저장 실패: {exc}"
            self.status = {"ok": False, "component": "04_correctSqlInput", "error": str(exc), "answer_text": answer}
            logging.getLogger("smartmigrate.workflow").error(
                answer,
                extra={"workflow_log": [0, "WORKFLOW", "04_CORRECT_SQL_INPUT", "ERROR", "SAVE_SQL", "ERROR", 0]},
            )
            return Message(text=answer)

    def _target(self, target: dict[str, Any], sql_text: str) -> tuple[str, str, str, dict[str, str], str]:
        # work_type과 sql_column은 DB UPDATE 문을 구성하는 값이므로 반드시 allow-list로 제한한다.
        # 여기서 반환하는 table/column/where_sql만 run()에서 SQL 문자열에 삽입된다.
        if not sql_text:
            raise ValueError("Correct SQL 입력을 위해 저장할 SQL 본문을 알려주셔야 합니다.")

        kind = str(target.get("work_type") or "").upper()
        column = str(target.get("sql_column") or "").upper()

        if kind == "DB_MIGRATION":
            map_id = str(target.get("map_id") or "").strip()
            if not map_id:
                raise ValueError("DB Migration Correct SQL 입력을 위해 MAP_ID를 알려주셔야 합니다.")
            if column not in {"MIG_SQL", "VERIFY_SQL"}:
                raise ValueError("DB Migration에는 MIG_SQL 또는 VERIFY_SQL만 Correct SQL로 저장할 수 있습니다.")
            return "NEXT_MIG_INFO", column, "MAP_ID = :map_id", {"map_id": map_id}, f"MAP_ID={map_id}"

        if kind not in {"SQL_CONVERSION", "SQL_TUNING", "SQL_FORMATTING"}:
            raise ValueError("Correct SQL 입력을 위한 작업 종류가 올바르지 않습니다.")

        sql_id = str(target.get("sql_id") or "").strip()
        space_nm = str(target.get("space_nm") or "").strip()
        if not sql_id or not space_nm:
            raise ValueError("Correct SQL 입력을 위해 SQL_ID와 SPACE_NM을 모두 알려주셔야 합니다.")
        if column not in {"TO_SQL", "BIND_SQL", "TEST_SQL", "TUNED_TO_SQL", "FORMATTED_SQL"}:
            raise ValueError("NEXT_SQL_INFO에는 허용된 SQL 컬럼(TO_SQL, BIND_SQL, TEST_SQL, TUNED_TO_SQL, FORMATTED_SQL)만 저장할 수 있습니다.")
        return "NEXT_SQL_INFO", column, "SQL_ID = :sql_id AND SPACE_NM = :space_nm", {"sql_id": sql_id, "space_nm": space_nm}, f"SQL_ID={sql_id}, SPACE_NM={space_nm}"

    @contextmanager
    def _connect(self):
        # Oracle 연결 생성과 닫기를 한 곳에서 관리한다.
        # 호출부는 with 블록 안에서만 cursor와 LOB locator를 사용해야 한다.
        import oracledb

        conn = oracledb.connect(
            user=str(self.db_username).strip(),
            password=self._secret(getattr(self, "db_password", None)),
            dsn=oracledb.makedsn(
                str(self.db_host).strip(),
                int(getattr(self, "db_port", None) or 1521),
                service_name=str(self.db_service_name).strip(),
            ),
        )
        try:
            yield conn
        finally:
            conn.close()

    def _columns(self, conn: Any, table: str) -> set[str]:
        # system_schema가 있으면 ALL_TAB_COLUMNS에서 owner를 지정해 조회하고,
        # 없으면 현재 접속 schema 기준 USER_TAB_COLUMNS를 조회한다.
        cur = conn.cursor()
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if schema:
            cur.execute(
                "SELECT COLUMN_NAME FROM ALL_TAB_COLUMNS WHERE OWNER=:owner AND TABLE_NAME=:table_name",
                {"owner": schema, "table_name": table},
            )
        else:
            cur.execute("SELECT COLUMN_NAME FROM USER_TAB_COLUMNS WHERE TABLE_NAME=:table_name", {"table_name": table})
        return {str(row[0]).upper() for row in cur.fetchall()}

    def _qualify(self, table: str) -> str:
        # Oracle object name은 bind variable로 처리할 수 없으므로 schema/table은
        # _target()과 _columns()를 통과한 값만 조합한다.
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Router output은 Langflow 연결 방식에 따라 Data 또는 JSON 문자열로 들어온다.
        # downstream 로직은 dict만 다루도록 여기서 형태를 통일한다.
        if isinstance(raw, Data):
            return dict(raw.data or {})
        if isinstance(raw, dict):
            return dict(raw)
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(raw or "").strip(), flags=re.I)
        value = json.loads(text) if text else {}
        if not isinstance(value, dict):
            raise ValueError("payload_json must be a JSON object")
        return value

    def _secret(self, value: Any) -> str:
        # Langflow SecretStrInput은 get_secret_value()를 제공하고,
        # 테스트나 직접 호출에서는 일반 문자열이 들어올 수 있다.
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")
