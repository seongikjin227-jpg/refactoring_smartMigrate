from __future__ import annotations

import logging
import json
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


AGENT_ORDER = [
    ("db_migration", "DB Migration"),
    ("sql_conversion", "SQL Conversion"),
    ("sql_tuning", "SQL Tuning"),
    ("sql_formatting", "SQL Formatting"),
]


class NewType04Dashboard(Component):

    display_name = "04 Dashboard"
    description = "Queries dashboard counts/progress and formats a concise Gaia output message."
    name = "NewType04Dashboard"
    icon = "Gauge"

    inputs = [
        DataInput(name="payload_json", display_name="Payload JSON", required=True),
        StrInput(name="db_host", display_name="DB Host", required=True),
        IntInput(name="db_port", display_name="DB Port", value=1521, required=False),
        StrInput(name="db_service_name", display_name="DB Service Name", required=True),
        StrInput(name="db_username", display_name="DB Username", required=True),
        SecretStrInput(name="db_password", display_name="DB Password", required=True),
        StrInput(name="system_schema", display_name="System Schema", required=True),
    ]
    outputs = [Output(display_name="Result Message", name="result", method="run", types=["Message"])]

    # Langflow output 진입점에서 입력을 검증하고 이 컴포넌트의 주요 실행 흐름을 시작한다.
    def run(self) -> Message:
        # Dashboard 데이터를 조회해 사용자에게 보여줄 Markdown 메시지로 반환한다.
        logging.getLogger("smartmigrate.workflow").info("before run", extra={"workflow_log": [0, "WORKFLOW", "04_DASHBOARD", "INFO", "RUN", "START", 0]})
        try:
            try:
                payload = self._parse_payload(getattr(self, "payload_json", ""))
                dashboard = self._query_dashboard()
                answer = self._build_answer(payload, dashboard)
                self.status = {
                    **payload,
                    "component": "04_dashboard",
                    "dashboard_data": dashboard,
                    "answer_text": answer,
                    "final": True,
                }
                __log_result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").info("after run", extra={"workflow_log": [0, "WORKFLOW", "04_DASHBOARD", "INFO", "RUN", "END", 0]})
                return __log_result
            except Exception as exc:
                answer = f"[Dashboard 조회 결과]\nDashboard 조회 중 오류가 발생했습니다.\n오류: {exc}"
                self.status = {"ok": False, "component": "04_dashboard", "error": str(exc), "answer_text": answer}
                __log_result = Message(text=answer)
                logging.getLogger("smartmigrate.workflow").error("error run", extra={"workflow_log": [0, "WORKFLOW", "04_DASHBOARD", "ERROR", "RUN", "ERROR", 0]})
                return __log_result
            logging.getLogger("smartmigrate.workflow").info("after run", extra={"workflow_log": [0, "WORKFLOW", "04_DASHBOARD", "INFO", "RUN", "END", 0]})
        except Exception as exc:
            logging.getLogger("smartmigrate.workflow").error(f"error run: {exc}", extra={"workflow_log": [0, "WORKFLOW", "04_DASHBOARD", "ERROR", "RUN", "ERROR", 0]})
            raise

    # 조회 조건을 조립해 DB에서 요청된 정보를 가져온다.
    def _query_dashboard(self) -> dict[str, Any]:
        # 각 업무 단계별 잔여/성공/실패 건수를 DB에서 직접 집계한다.
        if not self._has_db_config():
            raise ValueError("DB connection settings are required for 04 Dashboard")
        agents = {
            "db_migration": self._migration_summary(),
            "sql_conversion": self._sql_conversion_summary(),
            "sql_tuning": self._sql_tuning_summary(),
            "sql_formatting": self._sql_formatting_summary(),
        }
        return {"ok": True, "agents": agents, "recommendation": self._recommendation(agents)}

    # NEXT_MIG_INFO 기준 DB Migration 진행률과 성공/실패 현황을 집계한다.
    def _migration_summary(self) -> dict[str, Any]:
        # DB Migration 단계의 전체/대상/성공/실패 건수를 계산한다.
        table = self._qualify("NEXT_MIG_INFO")
        target_scope = "UPPER(TRIM(NVL(USE_YN, 'N'))) = 'Y'"
        pending_where = f"{target_scope} AND STATUS IS NULL"
        fail_where = f"{target_scope} AND ({self._fail_status_condition('STATUS')})"
        edited_fail_where = f"{target_scope} AND {self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, target_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        progress_base = total
        pass_count = self._count(table, f"{target_scope} AND UPPER(TRIM(NVL(STATUS, 'NULL'))) IN ('PASS', 'SUCCESS')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="DB_MIGRATION",
            table=table,
            target_condition="USE_YN='Y' AND (STATUS IS NULL OR (USER_EDITED='Y' AND STATUS LIKE 'FAIL-%'))",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=progress_base,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS", target_scope),
        )

    # NEXT_SQL_INFO 기준 SQL Conversion 진행률과 성공/실패 현황을 집계한다.
    def _sql_conversion_summary(self) -> dict[str, Any]:
        # SQL Conversion 단계의 전체/대상/성공/실패 건수를 계산한다.
        table = self._qualify("NEXT_SQL_INFO")
        target_scope = self._sql_status_target_condition("STATUS_CONVERSION", ("PASS", "PASS-CONVERSION"))
        pending_where = "STATUS_CONVERSION IS NULL"
        fail_where = f"{target_scope} AND ({self._fail_status_condition('STATUS_CONVERSION')})"
        edited_fail_where = f"{self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS_CONVERSION')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, target_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        pass_count = self._count(table, f"{target_scope} AND UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="SQL_CONVERSION",
            table=table,
            target_condition="total excludes STATUS_CONVERSION='NA'; remaining is NULL or USER_EDITED='Y' FAIL-*",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_CONVERSION", target_scope),
        )

    # NEXT_SQL_INFO 기준 SQL Tuning 진행률과 성공/실패 현황을 집계한다.
    def _sql_tuning_summary(self) -> dict[str, Any]:
        # SQL Tuning 단계의 전체/대상/성공/실패 건수를 계산한다.
        table = self._qualify("NEXT_SQL_INFO")
        total_scope = self._sql_status_target_condition("STATUS_TUNING", ("PASS", "PASS-TUNING"))
        base_where = "UPPER(TRIM(STATUS_CONVERSION)) IN ('PASS', 'PASS-CONVERSION')"
        pending_where = f"{base_where} AND STATUS_TUNING IS NULL"
        fail_where = f"{base_where} AND ({self._fail_status_condition('STATUS_TUNING')})"
        edited_fail_where = f"{base_where} AND {self._user_edited_condition()} AND ({self._detailed_fail_status_condition('STATUS_TUNING')})"
        target_where = f"{pending_where} OR ({edited_fail_where})"
        total = self._count(table, total_scope)
        target = self._count(table, target_where)
        pending = self._count(table, pending_where)
        pass_count = self._count(table, f"{base_where} AND UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')")
        fail_count = self._count(table, fail_where)
        return self._stage_summary(
            agent="SQL_TUNING",
            table=table,
            target_condition="total excludes STATUS_TUNING='NA'; remaining requires conversion PASS and NULL or USER_EDITED='Y' FAIL-*",
            total=total,
            target_count=target,
            pending_count=pending,
            progress_count=pass_count + fail_count,
            progress_base=total,
            success_count=pass_count,
            success_base=pass_count + fail_count,
            pass_count=pass_count,
            fail_count=fail_count,
            status_counts=self._status_counts(table, "STATUS_TUNING", total_scope),
        )

    # NEXT_SQL_INFO 기준 SQL Formatting 적용/대기 현황을 집계한다.
    def _sql_formatting_summary(self) -> dict[str, Any]:
        # SQL Formatting 단계의 적용/대기 건수를 계산한다.
        table = self._qualify("NEXT_SQL_INFO")
        total_scope = self._sql_status_target_condition("STATUS_TUNING", ("PASS", "PASS-TUNING"))
        base_where = "UPPER(TRIM(STATUS_TUNING)) IN ('PASS', 'PASS-TUNING')"
        target_where = f"{base_where} AND (FORMATTED_SQL IS NULL OR NVL(DBMS_LOB.GETLENGTH(FORMATTED_SQL), 0) = 0)"
        applied_where = f"{base_where} AND FORMATTED_SQL IS NOT NULL AND DBMS_LOB.GETLENGTH(FORMATTED_SQL) > 0"
        total = self._count(table, total_scope)
        target = self._count(table, target_where)
        applied = self._count(table, applied_where)
        return self._stage_summary(
            agent="SQL_FORMATTING",
            table=table,
            target_condition="total excludes STATUS_TUNING='NA'; remaining requires tuning PASS and FORMATTED_SQL empty",
            total=total,
            target_count=target,
            progress_count=applied,
            progress_base=total,
            success_count=applied,
            success_base=0,
            pass_count=applied,
            fail_count=0,
            status_counts={"APPLIED": applied, "PENDING": target},
            has_success_rate=False,
        )

    # 단계별 실행 결과를 dashboard용 집계 구조로 요약한다.
    def _stage_summary(
        self,
        *,
        agent: str,
        table: str,
        target_condition: str,
        total: int,
        target_count: int,
        pending_count: int | None = None,
        progress_count: int,
        progress_base: int,
        success_count: int,
        success_base: int,
        pass_count: int,
        fail_count: int,
        status_counts: dict[str, int],
        has_success_rate: bool = True,
    ) -> dict[str, Any]:
        # 화면 출력 로직이 단계별 차이를 몰라도 되도록 공통 summary 구조로 맞춘다.
        effective_pending_count = int(target_count if pending_count is None else pending_count or 0)
        return {
            "agent": agent,
            "available": True,
            "table": table,
            "target_condition": target_condition,
            "total": int(total or 0),
            "target_count": int(target_count or 0),
            "remaining_count": int(target_count or 0),
            "pass_count": int(pass_count or 0),
            "fail_count": int(fail_count or 0),
            "other_count": max(
                int(total or 0) - effective_pending_count - int(pass_count or 0) - int(fail_count or 0),
                0,
            ),
            "progress": {
                "count": int(progress_count or 0),
                "base": int(progress_base or 0),
                "rate": self._pct(progress_count, progress_base),
            },
            "success": (
                {
                    "count": int(success_count or 0),
                    "base": int(success_base or 0),
                    "rate": self._pct(success_count, success_base),
                }
                if has_success_rate
                else {"count": 0, "base": 0, "rate": "-", "not_applicable": True}
            ),
            "status_counts": status_counts,
        }

    # 후속 단계나 사용자 응답에 필요한 구조화된 결과를 조립한다.
    def _build_answer(self, payload: dict[str, Any], dashboard: dict[str, Any]) -> str:
        # 집계 결과를 Langflow Chat Output에 보여줄 Markdown으로 변환한다.
        agents = dashboard.get("agents") or {}
        lines = ["# SmartMigrate Dashboard"]
        lines.append("## 작업 현황")
        lines.append("| 순서 | 단계 | 작업 대상 | 잔여 | 성공 | 실패 |")
        lines.append("|---:|---|---:|---:|---:|---:|")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - | - | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | "
                f"{label} | "
                f"{self._num(summary.get('total'))} | "
                f"{self._num(summary.get('remaining_count', summary.get('target_count')))} | "
                f"{self._num(summary.get('pass_count'))} | "
                f"{self._num(summary.get('fail_count'))} |"
            )

        lines.append("")
        lines.append("## 진척률 / 성공률")
        lines.append("| 순서 | 단계 | 진척률 | 성공률 |")
        lines.append("|---:|---|---|---|")
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            priority = AGENT_ORDER.index((key, label)) + 1
            if not summary.get("available", True):
                lines.append(f"| {priority} | {label} | - | - |")
                continue
            lines.append(
                "| "
                f"{priority} | {label} | "
                f"{self._rate(summary.get('progress') or {})} | "
                f"{self._rate(summary.get('success') or {})} |"
            )

        unavailable = [
            (label, (agents.get(key) or {}).get("reason"))
            for key, label in AGENT_ORDER
            if not (agents.get(key) or {}).get("available", True)
        ]
        if unavailable:
            lines.append("")
            lines.append("## 조회 불가")
            lines.append("")
            for label, reason in unavailable:
                lines.append(f"- **{label}**: {reason}")

        return "\n".join(lines)

    # 남은 작업이 있는 단계 중 다음에 실행할 우선순위 작업을 고른다.
    def _recommendation(self, agents: dict[str, dict[str, Any]]) -> dict[str, Any]:
        # 남은 작업이 있는 단계 중 실행 우선순위가 가장 높은 단계를 고른다.
        for key, label in AGENT_ORDER:
            summary = agents.get(key) or {}
            count = int(summary.get("target_count") or 0)
            if summary.get("available", True) and count > 0:
                return {"agent": summary.get("agent"), "label": label, "target_count": count}
        return {}

    # 전달된 SQL/조건으로 단일 COUNT 값을 조회한다.
    def _count(self, table: str, where_clause: str = "1=1") -> int:
        # 전달받은 조건으로 COUNT 쿼리를 실행한다.
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(f"SELECT COUNT(*) FROM {table} WHERE {where_clause}")
            row = cur.fetchone()
        return int(row[0] if row else 0)

    # FAIL 계열 status를 찾는 SQL WHERE 조건을 만든다.
    def _fail_status_condition(self, status_column: str) -> str:
        # 실패로 종료된 상태값을 판별하는 SQL 조건을 만든다.
        return f"UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"

    # 상세 실패 status 집계용 FAIL 계열 SQL 조건을 만든다.
    def _detailed_fail_status_condition(self, status_column: str) -> str:
        # 사람이 SQL을 보정한 row는 FAIL-BIND 같은 세부 실패 단계부터 재실행 대상에 포함한다.
        return f"UPPER(TRIM(NVL({status_column}, 'NULL'))) LIKE 'FAIL-%'"

    # SQL 단계에서 실행 대상이 되는 NULL/FAIL/USER_EDITED 상태 조건을 만든다.
    def _sql_status_target_condition(self, status_column: str, pass_statuses: tuple[str, ...]) -> str:
        pass_list = ", ".join(f"'{status}'" for status in pass_statuses)
        normalized = f"UPPER(TRIM(NVL({status_column}, 'NULL')))"
        return f"({status_column} IS NULL OR {normalized} IN ({pass_list}) OR {normalized} LIKE 'FAIL-%')"

    # USER_EDITED=Y인 실패 row를 재실행 대상으로 판단하는 SQL 조건을 만든다.
    def _user_edited_condition(self) -> str:
        # USER_EDITED=Y인 실패 row를 재실행 대상으로 판단하는 공통 조건이다.
        return "UPPER(TRIM(NVL(USER_EDITED, 'N'))) = 'Y'"

    # status 값별 row 수를 집계해 dashboard와 QA 응답에 사용한다.
    def _status_counts(self, table: str, status_column: str, where_clause: str = "1=1") -> dict[str, int]:
        # 단계별 상태 분포를 status 값 기준으로 묶어 집계한다.
        with self._connect() as conn:
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT NVL(TO_CHAR({status_column}), 'NULL') AS STATUS_VALUE, COUNT(*) AS CNT
                  FROM {table}
                 WHERE {where_clause}
                 GROUP BY NVL(TO_CHAR({status_column}), 'NULL')
                 ORDER BY CNT DESC, STATUS_VALUE ASC
                """
            )
            rows = cur.fetchall()
        return {str(row[0]): int(row[1]) for row in rows}

    @contextmanager
    # Oracle 연결을 열고 호출 구간이 끝나면 닫는 context manager다.
    def _connect(self):
        # Oracle 연결을 열고 호출 블록이 끝나면 닫는다.
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

    # 필수 DB 접속 값이 없으면 DB 작업 전에 명확히 실패시킨다.
    def _has_db_config(self) -> bool:
        # DB 연결에 필요한 최소 입력값이 채워졌는지 확인한다.
        return all(str(getattr(self, name, "") or "").strip() for name in ("db_host", "db_service_name", "db_username"))

    # system_schema가 명시된 테이블명을 schema-qualified 이름으로 만든다.
    def _qualify(self, table_name: str) -> str:
        # 표준 system_schema와 내부 테이블명을 조합해 schema.table 이름을 만든다.
        table = self._clean_identifier(table_name)
        schema = str(getattr(self, "system_schema", "") or "").strip().upper()
        if not schema:
            raise ValueError("System Schema를 입력해야 합니다.")
        return f"{schema}.{table}"

    # 동적 SQL identifier에 안전한 Oracle 문자만 허용한다.
    def _clean_identifier(self, value: str) -> str:
        # Oracle identifier 형식을 검증하고 대문자로 정규화한다.
        clean = str(value or "").strip().upper()
        if not re.fullmatch(r"[A-Z][A-Z0-9_$#]*", clean):
            raise ValueError(f"Invalid identifier: {clean}")
        return clean

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _pct(self, numerator: int, denominator: int) -> str:
        # 분자/분모를 퍼센트 문자열로 변환한다.
        denominator = int(denominator or 0)
        if denominator <= 0:
            return "-"
        return f"{(int(numerator or 0) / denominator) * 100:.1f}%"

    # 문자/숫자/NULL 값을 정수로 변환하고 실패하면 안전한 기본값을 반환한다.
    def _num(self, value: Any) -> int:
        # 화면 표시용 count 값을 정수로 변환한다.
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _rate(self, value: dict[str, Any]) -> str:
        # Markdown 표에 들어갈 진행률 문자열을 만든다.
        if value.get("not_applicable"):
            return "-"
        count = self._num(value.get("count"))
        base = self._num(value.get("base"))
        if base <= 0:
            return f"{self._progress_bar(0, 0)} - ({count}/{base})"
        return f"{self._progress_bar(count, base)} {value.get('rate', '-')} ({count}/{base})"

    # dashboard 표시용 숫자/텍스트를 짧고 안전한 문자열로 변환한다.
    def _progress_bar(self, count: int, base: int, width: int = 10) -> str:
        # 채팅 화면에서도 깨지지 않는 고정 폭 진행 막대를 만든다.
        if base <= 0:
            filled = 0
        else:
            filled = max(0, min(width, round((int(count or 0) / int(base)) * width)))
        return "■" * filled + "□" * (width - filled)

    # Langflow 입력이 Data/Message/dict/JSON 문자열 중 무엇이든 dict로 통일한다.
    def _parse_payload(self, raw: Any) -> dict[str, Any]:
        # Langflow 입력 payload를 dict로 통일한다.
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

    # Langflow Secret 입력을 일반 문자열로 꺼내 client library 설정에 사용한다.
    def _secret_to_str(self, value: Any) -> str:
        # Langflow Secret 입력과 일반 문자열 입력을 동일한 문자열로 변환한다.
        if value is None:
            return ""
        if hasattr(value, "get_secret_value"):
            return str(value.get_secret_value())
        return str(value)
