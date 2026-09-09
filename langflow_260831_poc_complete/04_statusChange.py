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
# 04 Status Change
# =============================================================================
# Management Router가 STATUS_CHANGE로 분기한 요청을 받아 특정 작업 row를
# 재실행 가능한 상태로 되돌리는 컴포넌트다.
#
# 핵심 원칙:
# - SQL 본문은 절대 삭제하거나 수정하지 않는다.
# - 상태 컬럼만 NULL로 되돌리고 RETRY_COUNT를 0으로 초기화한다.
# - 사용자가 긴급/우선 처리 의도를 표현하면 priority=1, 기본은 priority=5로 둔다.
#
# 대상 status 컬럼:
# - DB_MIGRATION: NEXT_MIG_INFO.STATUS
# - SQL_CONVERSION: NEXT_SQL_INFO.STATUS_CONVERSION
# - SQL_TUNING: NEXT_SQL_INFO.STATUS_TUNING
# - SQL_FORMATTING은 별도 status 컬럼이 없으므로 reset 대상에서 제외된다.
# =============================================================================
class NewType04StatusChange(Component):
    display_name = "04 Status Change"
    description = "Resets the selected job status and sets retry count to zero; SQL is preserved."
    name = "NewType04StatusChange"
    icon = "RotateCcw"

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
        # 전체 reset 흐름:
        # 1. Router payload에서 target을 읽는다.
        # 2. work_type별 table/status column/where 조건을 만든다.
        # 3. 필요한 컬럼이 DB에 실제 존재하는지 확인한다.
        # 4. status=NULL, RETRY_COUNT=0, PRIORITY=:priority로 업데이트한다.
        logging.getLogger("smartmigrate.workflow").info(
            "04 Status Change started",
            extra={"workflow_log": [0, "WORKFLOW", "04_STATUS_CHANGE", "INFO", "RESET", "START", 0]},
        )
        try:
            payload = self._parse_payload(getattr(self, "payload_json", ""))
            target = dict(payload.get("target") or {})
            priority = self._priority(target)
            table, status_column, where_sql, params, identity = self._target(target)

            with self._connect() as conn:
                columns = self._columns(conn, table)
                required = {status_column, "RETRY_COUNT", "PRIORITY"}
                if not required.issubset(columns):
                    raise ValueError(f"{table}에 Reset에 필요한 컬럼({', '.join(sorted(required))})이 없습니다.")

                cur = conn.cursor()
                cur.execute(
                    f"UPDATE {self._qualify(table)} SET {status_column} = NULL, RETRY_COUNT = 0, PRIORITY = :priority WHERE {where_sql}",
                    {**params, "priority": priority},
                )
                if cur.rowcount != 1:
                    conn.rollback()
                    raise ValueError(f"대상 작업을 정확히 1건 찾지 못했습니다. ({identity}, count={cur.rowcount})")
                conn.commit()

            answer = f"Status Change(Reset) 완료: {identity}. {status_column}=NULL, RETRY_COUNT=0, PRIORITY={priority}로 변경했고 SQL 본문은 유지했습니다."
            self.status = {**payload, "component": "04_statusChange", "updated_rows": 1, "priority": priority, "answer_text": answer, "final": True}
            log_id = target.get("map_id") or f"{target.get('sql_id') or ''} / {target.get('space_nm') or ''}".strip(" / ") or 0
            logging.getLogger("smartmigrate.workflow").info(
                answer,
                extra={"workflow_log": [log_id, "WORKFLOW", "04_STATUS_CHANGE", "INFO", "RESET", "PASS", 0]},
            )
            return Message(text=answer)
        except Exception as exc:
            answer = f"Status Change(Reset) 실패: {exc}"
            self.status = {"ok": False, "component": "04_statusChange", "error": str(exc), "answer_text": answer}
            logging.getLogger("smartmigrate.workflow").error(
                answer,
                extra={"workflow_log": [0, "WORKFLOW", "04_STATUS_CHANGE", "ERROR", "RESET", "ERROR", 0]},
            )
            return Message(text=answer)

    def _target(self, target: dict[str, Any]) -> tuple[str, str, str, dict[str, str], str]:
        # reset 대상은 allow-list로만 결정한다.
        # table/column 이름은 bind가 불가능하므로 외부 입력을 직접 붙이지 않는다.
        kind = str(target.get("work_type") or "").upper()

        if kind == "DB_MIGRATION":
            map_id = str(target.get("map_id") or "").strip()
            if not map_id:
                raise ValueError("Status Change(Reset)를 위해 MAP_ID를 알려주셔야 합니다.")
            return "NEXT_MIG_INFO", "STATUS", "MAP_ID = :map_id", {"map_id": map_id}, f"MAP_ID={map_id}"

        status = {"SQL_CONVERSION": "STATUS_CONVERSION", "SQL_TUNING": "STATUS_TUNING"}.get(kind)
        sql_id = str(target.get("sql_id") or "").strip()
        space_nm = str(target.get("space_nm") or "").strip()
        if not status or not sql_id or not space_nm:
            raise ValueError("Status Change(Reset)를 위해 SQL_ID와 SPACE_NM을 모두 알려주셔야 합니다.")
        return "NEXT_SQL_INFO", status, "SQL_ID = :sql_id AND SPACE_NM = :space_nm", {"sql_id": sql_id, "space_nm": space_nm}, f"SQL_ID={sql_id}, SPACE_NM={space_nm}"

    def _priority(self, target: dict[str, Any]) -> int:
        # Router가 priority를 숫자로 정리해 주지만, LLM 출력은 항상 신뢰하지 않는다.
        # 이 컴포넌트에서는 운영상 의미가 있는 1 또는 5만 허용한다.
        try:
            return 1 if int(target.get("priority") or 5) == 1 else 5
        except (TypeError, ValueError):
            return 5

    @contextmanager
    def _connect(self):
        # Oracle 연결 생성과 닫기를 한 곳에서 관리한다.
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
        # schema 입력이 있으면 운영 schema를 명시해서 조회하고,
        # 없으면 현재 접속 schema 기준으로 컬럼 metadata를 확인한다.
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
        # Oracle object name은 bind variable로 처리할 수 없으므로
        # allow-list를 통과한 table 이름과 schema만 조합한다.
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        return f"{schema}.{table}" if schema else table

    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Router output은 Data/dict/JSON 문자열 중 하나로 들어올 수 있으므로 dict로 통일한다.
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
        # Langflow SecretStrInput과 테스트용 plain string을 모두 처리한다.
        return str(value.get_secret_value()) if hasattr(value, "get_secret_value") else str(value or "")
